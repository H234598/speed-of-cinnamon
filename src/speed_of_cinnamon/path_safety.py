from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path
from typing import Any

DEFAULT_MAX_TEXT_READ_BYTES = 1_000_000


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"secure path cleanup failed: {cleanup_error}")


def _safe_path_parts(path: Path, *, field_name: str) -> tuple[str, ...]:
    if not path.is_absolute():
        raise OSError(f"{field_name} must be absolute")
    parts = path.parts
    parts = parts[1:]
    if not parts:
        raise OSError(f"{field_name} is invalid")
    if any(part in {"", ".."} for part in parts):
        raise OSError(f"{field_name} contains an unsafe path component")
    return parts


def assert_safe_path_components(path: Path, *, field_name: str = "path") -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    try:
        _safe_path_parts(path, field_name=field_name)
    except OSError as exc:
        raise RuntimeError(str(exc)) from exc


def assert_no_symlink_ancestors(path: Path, *, field_name: str = "path") -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    current = path
    while True:
        if current.is_symlink():
            raise RuntimeError(f"{field_name} must not pass through a symlink")
        if current.parent == current:
            break
        current = current.parent


def open_file_without_following_symlinks(
    path: Path,
    flags: int,
    mode: int = 0o600,
    *,
    field_name: str = "path",
) -> int:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow_flag is None or directory_flag is None:
        raise OSError(f"secure file open is not supported for {field_name}")

    parts = _safe_path_parts(path, field_name=field_name)
    start_path = path.anchor if path.is_absolute() else "."
    directory_fd = os.open(start_path, os.O_RDONLY | directory_flag)
    result_fd: int | None = None
    primary_error: BaseException | None = None
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | directory_flag | nofollow_flag,
                dir_fd=directory_fd,
            )
            try:
                os.close(directory_fd)
            except OSError as close_error:
                try:
                    os.close(next_fd)
                except OSError as next_close_error:
                    _note_cleanup_failure(close_error, next_close_error)
                raise
            directory_fd = next_fd
        result_fd = os.open(parts[-1], flags | nofollow_flag, mode, dir_fd=directory_fd)
        return result_fd
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(directory_fd)
        except OSError as cleanup_error:
            if result_fd is not None:
                try:
                    os.close(result_fd)
                except OSError as result_close_error:
                    _note_cleanup_failure(cleanup_error, result_close_error)
                result_fd = None
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def open_directory_without_following_symlinks(path: Path, *, field_name: str = "path") -> int:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        raise OSError(f"secure directory open is not supported for {field_name}")
    if str(path) in {"", "."}:
        return os.open(".", os.O_RDONLY | directory_flag)
    if path.parent == path:
        return os.open(str(path), os.O_RDONLY | directory_flag)
    return open_file_without_following_symlinks(path, os.O_RDONLY | directory_flag, field_name=field_name)


def assert_fd_is_regular_private_file(
    fd: int,
    *,
    field_name: str = "path",
    require_private_mode: bool = False,
) -> None:
    try:
        file_stat = os.fstat(fd)
    except OSError as exc:
        raise RuntimeError(f"{field_name} could not be inspected") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"{field_name} must be a regular file")
    if getattr(file_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{field_name} must not be hardlinked")
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise RuntimeError(f"{field_name} must be owned by the current user")
    if require_private_mode and file_stat.st_mode & 0o077:
        raise RuntimeError(f"{field_name} must be private")


def assert_fd_is_private_directory(fd: int, *, field_name: str = "path") -> None:
    try:
        file_stat = os.fstat(fd)
    except OSError as exc:
        raise RuntimeError(f"{field_name} could not be inspected") from exc
    if not stat.S_ISDIR(file_stat.st_mode):
        raise RuntimeError(f"{field_name} must be a directory")
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise RuntimeError(f"{field_name} must be owned by the current user")
    if file_stat.st_mode & 0o077:
        raise RuntimeError(f"{field_name} must be private")


def ensure_directory_without_following_symlinks(path: Path, *, field_name: str = "path") -> int:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if nofollow_flag is None or directory_flag is None:
        raise OSError(f"secure directory creation is not supported for {field_name}")
    if str(path) in {"", "."}:
        return os.open(".", os.O_RDONLY | directory_flag)
    if path.parent == path:
        return os.open(str(path), os.O_RDONLY | directory_flag)

    parts = _safe_path_parts(path, field_name=field_name)
    start_path = path.anchor if path.is_absolute() else "."
    directory_fd = os.open(start_path, os.O_RDONLY | directory_flag)
    try:
        for component in parts:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | directory_flag | nofollow_flag,
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(
                    component,
                    os.O_RDONLY | directory_flag | nofollow_flag,
                    dir_fd=directory_fd,
                )
            try:
                os.close(directory_fd)
            except OSError as close_error:
                try:
                    os.close(next_fd)
                except OSError as next_close_error:
                    _note_cleanup_failure(close_error, next_close_error)
                raise
            directory_fd = next_fd
        return directory_fd
    except OSError as exc:
        try:
            os.close(directory_fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise


def read_text_without_following_symlinks(
    path: Path,
    *,
    field_name: str = "path",
    encoding: str = "utf-8",
    max_bytes: int | None = None,
    require_private_mode: bool = False,
) -> str:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0):
        raise RuntimeError("max_bytes must be a non-negative integer")
    effective_max_bytes = DEFAULT_MAX_TEXT_READ_BYTES if max_bytes is None else max_bytes
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name=field_name)
    except OSError as exc:
        raise OSError(str(exc)) from exc
    try:
        try:
            assert_fd_is_regular_private_file(
                fd,
                field_name=field_name,
                require_private_mode=require_private_mode,
            )
        except RuntimeError as exc:
            raise OSError(str(exc)) from exc
        handle = os.fdopen(fd, "rb")
    except Exception as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    with handle:
        payload = handle.read(effective_max_bytes + 1)
    if len(payload) > effective_max_bytes:
        raise OSError(f"{field_name} is too large")
    return payload.decode(encoding)


def _write_atomically_without_following_symlinks(
    path: Path,
    payload: str | bytes,
    *,
    field_name: str,
    mode: str,
    encoding: str | None,
) -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    assert_safe_path_components(path, field_name=field_name)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise OSError(f"secure atomic write is not supported for {field_name}")
    parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
    temp_name = ""
    primary_error: BaseException | None = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
        try:
            target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None and stat.S_ISLNK(target_stat.st_mode):
            raise OSError(f"{field_name} must not be a symlink")
        for _ in range(100):
            candidate_name = f".{path.name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(candidate_name, flags, 0o600, dir_fd=parent_fd)
                temp_name = candidate_name
                break
            except FileExistsError:
                continue
        else:
            raise OSError(f"failed to create temporary file for {field_name}")
        handle_kwargs: dict[str, Any] = {}
        if encoding is not None:
            handle_kwargs["encoding"] = encoding
        try:
            handle = os.fdopen(fd, mode, **handle_kwargs)
        except Exception as exc:
            try:
                os.close(fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            raise
        with handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError as exc:
                raise OSError(f"{field_name} temporary file could not be made private") from exc
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temp_name = ""
        os.fsync(parent_fd)
    except BaseException as exc:
        primary_error = exc
        cleanup_error: OSError | None = None
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            primary_error = OSError(f"failed to remove temporary file for {field_name}")
            raise primary_error from cleanup_error
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def write_text_atomically_without_following_symlinks(
    path: Path,
    text: str,
    *,
    field_name: str = "path",
    encoding: str = "utf-8",
) -> None:
    _write_atomically_without_following_symlinks(
        path,
        text,
        field_name=field_name,
        mode="w",
        encoding=encoding,
    )


def write_bytes_atomically_without_following_symlinks(
    path: Path,
    data: bytes,
    *,
    field_name: str = "path",
) -> None:
    _write_atomically_without_following_symlinks(
        path,
        data,
        field_name=field_name,
        mode="wb",
        encoding=None,
    )
