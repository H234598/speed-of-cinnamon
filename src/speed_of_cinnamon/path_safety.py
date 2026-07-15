from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
from pathlib import Path
from typing import Any

DEFAULT_MAX_TEXT_READ_BYTES = 1_000_000


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"secure path cleanup failed: {cleanup_error}")


def _rename_without_replacing(
    source_name: str,
    target_name: str,
    *,
    directory_fd: int,
    field_name: str,
) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(
            errno.ENOTSUP,
            f"{field_name} no-clobber activation is not supported on this platform",
        ) from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        directory_fd,
        os.fsencode(target_name),
        1,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target_name)


def _safe_path_parts(path: Path, *, field_name: str) -> tuple[str, ...]:
    if not path.is_absolute():
        raise OSError(f"{field_name} must be absolute")
    parts = path.parts
    parts = parts[1:]
    if not parts:
        raise OSError(f"{field_name} is invalid")
    if any(part in {"", ".."} for part in parts):
        raise OSError(f"{field_name} contains an unsafe path component")
    if any("\x00" in part for part in parts):
        raise OSError(f"{field_name} contains an invalid null byte")
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
                except BaseException as next_close_error:
                    _note_cleanup_failure(close_error, next_close_error)
                raise
            except BaseException as close_error:
                try:
                    os.close(next_fd)
                except BaseException as next_close_error:
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
                except BaseException as result_close_error:
                    _note_cleanup_failure(cleanup_error, result_close_error)
                result_fd = None
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
        except BaseException as cleanup_error:
            if result_fd is not None:
                try:
                    os.close(result_fd)
                except BaseException as result_close_error:
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
                except BaseException as next_close_error:
                    _note_cleanup_failure(close_error, next_close_error)
                raise
            except BaseException as close_error:
                try:
                    os.close(next_fd)
                except BaseException as next_close_error:
                    _note_cleanup_failure(close_error, next_close_error)
                raise
            directory_fd = next_fd
        return directory_fd
    except BaseException as exc:
        try:
            os.close(directory_fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise


def read_text_without_following_symlinks(
    path: Path,
    *,
    field_name: str = "path",
    encoding: str = "utf-8",
    max_bytes: int | None = None,
    require_private_mode: bool = False,
    expected_stat: os.stat_result | None = None,
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
        if expected_stat is not None:
            opened_stat = os.fstat(fd)
            if (
                opened_stat.st_dev != expected_stat.st_dev
                or opened_stat.st_ino != expected_stat.st_ino
                or opened_stat.st_mode != expected_stat.st_mode
                or opened_stat.st_size != expected_stat.st_size
                or getattr(opened_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
                or opened_stat.st_mtime_ns != expected_stat.st_mtime_ns
                or opened_stat.st_ctime_ns != expected_stat.st_ctime_ns
            ):
                raise OSError(f"{field_name} changed before reading")
            try:
                current_path_stat = path.lstat()
            except FileNotFoundError as exc:
                raise OSError(f"{field_name} changed before reading") from exc
            if (
                current_path_stat.st_dev != opened_stat.st_dev
                or current_path_stat.st_ino != opened_stat.st_ino
                or current_path_stat.st_mode != opened_stat.st_mode
            ):
                raise OSError(f"{field_name} changed before reading")
        handle = os.fdopen(fd, "rb")
    except Exception as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    except BaseException as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
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
    backup_name = ""
    backup_moved = False
    activation_attempted = False
    activation_completed = False
    activation_stat: os.stat_result | None = None
    temporary_stat: os.stat_result | None = None
    transaction_active = False
    primary_error: BaseException | None = None

    def _same_leaf_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            getattr(first, "st_nlink", 1),
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            getattr(second, "st_nlink", 1),
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
        )

    def _same_leaf_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            getattr(first, "st_nlink", 1),
            first.st_size,
            first.st_mtime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            getattr(second, "st_nlink", 1),
            second.st_size,
            second.st_mtime_ns,
        )

    def _same_leaf_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (first.st_dev, first.st_ino, first.st_mode) == (second.st_dev, second.st_ino, second.st_mode)

    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
        try:
            target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None and stat.S_ISLNK(target_stat.st_mode):
            raise OSError(f"{field_name} must not be a symlink")
        if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
            raise OSError(f"{field_name} must be a regular file")
        if target_stat is not None and getattr(target_stat, "st_nlink", 1) != 1:
            raise OSError(f"{field_name} must not be hardlinked")
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
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            raise
        except BaseException as exc:
            try:
                os.close(fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
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
            temporary_stat = os.fstat(handle.fileno())

        try:
            current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            current_target_stat = None
        if target_stat is None:
            if current_target_stat is not None:
                raise OSError(f"{field_name} changed before activation")
        elif current_target_stat is None or not _same_leaf_snapshot(current_target_stat, target_stat):
            raise OSError(f"{field_name} changed before activation")
        if temporary_stat is None:
            raise OSError(f"{field_name} temporary file identity is unavailable")
        staged_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISREG(staged_stat.st_mode) or getattr(staged_stat, "st_nlink", 1) != 1:
            raise OSError(f"{field_name} temporary file is not safe")
        if not _same_leaf_snapshot(staged_stat, temporary_stat):
            raise OSError(f"{field_name} temporary file changed before activation")

        transaction_active = True
        if target_stat is not None:
            for _ in range(100):
                candidate_name = f".{path.name}.{secrets.token_hex(8)}.bak"
                try:
                    os.link(
                        path.name,
                        candidate_name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    raise OSError(f"{field_name} path disappeared before backup activation") from None
                except FileExistsError:
                    continue
                try:
                    backup_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                    current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(backup_stat.st_mode)
                        or getattr(backup_stat, "st_nlink", 1) < 2
                        or not _same_leaf_inode(backup_stat, target_stat)
                        or not stat.S_ISREG(current_target_stat.st_mode)
                        or not _same_leaf_inode(current_target_stat, target_stat)
                    ):
                        raise OSError(f"{field_name} path changed during backup activation")
                    os.unlink(path.name, dir_fd=parent_fd)
                    backup_name = candidate_name
                    backup_moved = True
                    os.fsync(parent_fd)
                    break
                except BaseException as exc:
                    if not backup_moved:
                        try:
                            candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                            if _same_leaf_inode(candidate_stat, target_stat):
                                os.unlink(candidate_name, dir_fd=parent_fd)
                                os.fsync(parent_fd)
                        except FileNotFoundError:
                            pass
                        except BaseException as cleanup_error:
                            _note_cleanup_failure(exc, cleanup_error)
                    raise
            if not backup_name:
                raise OSError(f"failed to create recovery backup for {field_name}")
        activation_attempted = True
        _rename_without_replacing(
            temp_name,
            path.name,
            directory_fd=parent_fd,
            field_name=field_name,
        )
        activation_completed = True
        temp_name = ""
        try:
            activation_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as stat_error:
            raise OSError(f"{field_name} could not be inspected after activation") from stat_error
        os.fsync(parent_fd)
        transaction_active = False
        if backup_moved:
            backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(backup_stat.st_mode) or getattr(backup_stat, "st_nlink", 1) != 1:
                raise OSError(f"{field_name} recovery backup is not safe")
            if target_stat is None or not _same_leaf_identity(backup_stat, target_stat):
                raise OSError(f"{field_name} recovery backup changed before cleanup")
            os.unlink(backup_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except BaseException as exc:
        primary_error = exc
        if transaction_active:
            try:
                if activation_attempted:
                    expected_activation_stat = activation_stat or temporary_stat
                    try:
                        current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current_target_stat = None
                    if current_target_stat is not None:
                        if activation_stat is not None:
                            same_activation = _same_leaf_snapshot
                        elif activation_completed:
                            same_activation = _same_leaf_inode
                        else:
                            same_activation = _same_leaf_identity
                        if expected_activation_stat is None or not same_activation(current_target_stat, expected_activation_stat):
                            raise OSError(f"{field_name} target changed during rollback")
                        os.unlink(path.name, dir_fd=parent_fd)
                        os.fsync(parent_fd)
                if backup_moved:
                    try:
                        os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        _rename_without_replacing(
                            backup_name,
                            path.name,
                            directory_fd=parent_fd,
                            field_name=field_name,
                        )
                        os.fsync(parent_fd)
                    else:
                        raise OSError(f"{field_name} target exists during rollback")
            except BaseException as rollback_error:
                _note_cleanup_failure(primary_error, rollback_error)
        cleanup_error: OSError | None = None
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError as exc:
                cleanup_error = exc
            except BaseException as cleanup_exception:
                _note_cleanup_failure(primary_error, cleanup_exception)
        if cleanup_error is not None:
            _note_cleanup_failure(primary_error, cleanup_error)
            raise
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
        except BaseException as cleanup_error:
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
