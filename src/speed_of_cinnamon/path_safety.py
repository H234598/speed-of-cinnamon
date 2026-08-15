from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import secrets
import stat
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .secure_delete import secure_wipe_regular_file_at

DEFAULT_MAX_TEXT_READ_BYTES = 1_000_000


class ExpectedTargetKind(Enum):
    MISSING = "missing"
    CAPTURED = "captured"
    UNKNOWN = "unknown"


class _TargetChangedError(OSError):
    pass


@dataclass(frozen=True, slots=True)
class _TargetSnapshot:
    device: int
    inode: int
    file_type: int
    link_count: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class ExpectedTarget:
    kind: ExpectedTargetKind
    snapshot: _TargetSnapshot | None
    require_same_version: bool
    content_digest: bytes | None
    max_digest_bytes: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ExpectedTargetKind):
            raise TypeError("expected target kind is invalid")
        if type(self.require_same_version) is not bool:
            raise TypeError("require_same_version must be a boolean")
        if self.kind is ExpectedTargetKind.CAPTURED:
            if not isinstance(self.snapshot, _TargetSnapshot):
                raise TypeError("captured expected target requires a snapshot")
            if self.require_same_version:
                max_digest_bytes = self.max_digest_bytes
                if max_digest_bytes is None:
                    max_digest_bytes = DEFAULT_MAX_TEXT_READ_BYTES
                    object.__setattr__(self, "max_digest_bytes", max_digest_bytes)
                if (
                    isinstance(max_digest_bytes, bool)
                    or not isinstance(max_digest_bytes, int)
                    or max_digest_bytes < 0
                ):
                    raise TypeError("max_digest_bytes must be a non-negative integer")
                if type(self.content_digest) is not bytes or len(self.content_digest) != 32:
                    raise TypeError("version-bound expected target requires a SHA-256 digest")
            elif self.content_digest is not None or self.max_digest_bytes is not None:
                raise ValueError("identity-only expected target must not contain version evidence")
        elif (
            self.snapshot is not None
            or self.require_same_version
            or self.content_digest is not None
            or self.max_digest_bytes is not None
        ):
            raise ValueError("non-captured expected target must not contain a snapshot or version requirement")

    @classmethod
    def missing(cls) -> ExpectedTarget:
        return cls(ExpectedTargetKind.MISSING, None, False, None)

    @classmethod
    def unknown(cls) -> ExpectedTarget:
        return cls(ExpectedTargetKind.UNKNOWN, None, False, None)

    @classmethod
    def captured(
        cls,
        fd: int,
        *,
        require_same_version: bool = True,
        max_digest_bytes: int | None = None,
    ) -> ExpectedTarget:
        if type(require_same_version) is not bool:
            raise TypeError("require_same_version must be a boolean")
        if max_digest_bytes is None:
            max_digest_bytes = DEFAULT_MAX_TEXT_READ_BYTES
        elif (
            isinstance(max_digest_bytes, bool)
            or not isinstance(max_digest_bytes, int)
            or max_digest_bytes < 0
        ):
            raise TypeError("max_digest_bytes must be a non-negative integer")
        snapshot, content_digest = _capture_fd_evidence(
            fd,
            include_digest=require_same_version,
            field_name="captured expected target",
            max_digest_bytes=max_digest_bytes,
        )
        return cls(
            ExpectedTargetKind.CAPTURED,
            snapshot,
            require_same_version,
            content_digest,
            max_digest_bytes if require_same_version else None,
        )


_MAX_EXCEPTION_NOTE_LENGTH = 256


def _add_exception_note(error: BaseException, note: str) -> None:
    if type(note) is not str or len(note) > _MAX_EXCEPTION_NOTE_LENGTH:
        return
    if any(unicodedata.category(character).startswith("C") for character in note):
        return
    try:
        add_note = getattr(error, "add_note")
        if callable(add_note):
            add_note(note)
            return
    except BaseException:
        pass
    try:
        notes = list(getattr(error, "__notes__", ()))
        notes.append(note)
        setattr(error, "__notes__", notes)
    except BaseException:
        pass


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    _add_exception_note(primary, "secure path cleanup failed")


def _fsync_fd(fd: int) -> None:
    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            continue


def _resolve_renameat2(*, field_name: str):
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
    return renameat2


def _rename_without_replacing(
    source_name: str,
    target_name: str,
    *,
    directory_fd: int,
    target_directory_fd: int | None = None,
    expected_source_stat: os.stat_result | None = None,
    expected_source_fd: int | None = None,
    field_name: str,
) -> None:
    renameat2 = _resolve_renameat2(field_name=field_name)
    if expected_source_fd is not None:
        source_name_fd: int | None = None
        source_name_error: BaseException | None = None
        try:
            source_fd_stat = os.fstat(expected_source_fd)
            source_name_fd = os.open(
                source_name,
                os.O_RDONLY
                | _resolve_no_follow_flag(field_name=field_name)
                | getattr(os, "O_NONBLOCK", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            source_name_stat = os.fstat(source_name_fd)
        except FileNotFoundError:
            source_name_error = _TargetChangedError(f"{field_name} changed before cleanup")
            raise source_name_error from None
        except BaseException as exc:
            source_name_error = exc
            raise
        finally:
            if source_name_fd is not None:
                owned_source_name_fd, source_name_fd = source_name_fd, None
                _close_fd_preserving_primary(
                    owned_source_name_fd,
                    primary_error=source_name_error,
                    committed=False,
                )
        if expected_source_stat is not None and not _same_claim_snapshot(source_fd_stat, expected_source_stat):
            raise _TargetChangedError(f"{field_name} changed before cleanup")
        if not _same_claim_snapshot(source_name_stat, source_fd_stat):
            raise _TargetChangedError(f"{field_name} changed before cleanup")
    elif expected_source_stat is not None and not stat.S_ISLNK(expected_source_stat.st_mode):
        source_fd: int | None = None
        source_error: BaseException | None = None
        try:
            try:
                source_fd = os.open(
                    source_name,
                    os.O_RDONLY
                    | _resolve_no_follow_flag(field_name=field_name)
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory_fd,
                )
                source_stat = os.fstat(source_fd)
            except FileNotFoundError:
                source_error = _TargetChangedError(f"{field_name} changed before cleanup")
                raise source_error from None
        except BaseException as exc:
            source_error = exc
            raise
        finally:
            if source_fd is not None:
                owned_source_fd, source_fd = source_fd, None
                _close_fd_preserving_primary(
                    owned_source_fd,
                    primary_error=source_error,
                    committed=False,
                )
        if (
            source_stat.st_dev,
            source_stat.st_ino,
            source_stat.st_mode,
            getattr(source_stat, "st_nlink", 1),
            source_stat.st_size,
            source_stat.st_mtime_ns,
            source_stat.st_ctime_ns,
        ) != (
            expected_source_stat.st_dev,
            expected_source_stat.st_ino,
            expected_source_stat.st_mode,
            getattr(expected_source_stat, "st_nlink", 1),
            expected_source_stat.st_size,
            expected_source_stat.st_mtime_ns,
            expected_source_stat.st_ctime_ns,
        ):
            raise _TargetChangedError(f"{field_name} changed before cleanup")
    destination_fd = directory_fd if target_directory_fd is None else target_directory_fd
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(target_name),
        1,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.EEXIST:
            raise FileExistsError(errno.EEXIST, "secure rename target exists")
        raise OSError("secure rename failed")


def _rename_exchange(
    source_name: str,
    target_name: str,
    *,
    directory_fd: int,
    target_directory_fd: int | None = None,
    field_name: str,
) -> None:
    renameat2 = _resolve_renameat2(field_name=field_name)
    destination_fd = directory_fd if target_directory_fd is None else target_directory_fd
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        destination_fd,
        os.fsencode(target_name),
        2,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        if error_number == errno.ENOENT:
            raise FileNotFoundError(errno.ENOENT, "secure exchange source or target missing")
        raise OSError("secure exchange failed")


def _same_claim_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
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


_CONTROL_FLOW_CLOSE_ERRORS = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _sanitized_close_error(close_error: BaseException, *, committed: bool) -> BaseException:
    if isinstance(close_error, _CONTROL_FLOW_CLOSE_ERRORS):
        message = (
            "secure postcommit cleanup interrupted"
            if committed
            else "secure cleanup interrupted"
        )
        sanitized_error = type(close_error)(message)
        if committed:
            _add_exception_note(
                sanitized_error,
                "mutation already committed; no rollback attempted",
            )
    elif isinstance(close_error, ValueError):
        sanitized_error = ValueError(
            "secure postcommit cleanup failed" if committed else "secure cleanup failed"
        )
    else:
        sanitized_error = OSError(
            "secure postcommit cleanup failed" if committed else "secure cleanup failed"
        )
    sanitized_error.__cause__ = None
    sanitized_error.__context__ = None
    sanitized_error.__suppress_context__ = True
    return sanitized_error


def _close_fd_preserving_primary(
    fd: int,
    *,
    primary_error: BaseException | None,
    committed: bool,
) -> None:
    pending_error: BaseException | None = None
    try:
        os.close(fd)
    except BaseException as close_error:
        if primary_error is not None:
            try:
                _note_cleanup_failure(primary_error, close_error)
            except BaseException:
                pass
        elif committed and isinstance(close_error, _CONTROL_FLOW_CLOSE_ERRORS):
            pending_error = _sanitized_close_error(close_error, committed=True)
        elif not committed:
            pending_error = _sanitized_close_error(close_error, committed=False)
    if pending_error is not None:
        raise pending_error from None


def _close_handle_preserving_primary(
    handle: Any,
    *,
    primary_error: BaseException | None,
    committed: bool,
) -> None:
    pending_error: BaseException | None = None
    try:
        handle.close()
    except BaseException as close_error:
        if primary_error is not None:
            try:
                _note_cleanup_failure(primary_error, close_error)
            except BaseException:
                pass
        elif committed and isinstance(close_error, _CONTROL_FLOW_CLOSE_ERRORS):
            pending_error = _sanitized_close_error(close_error, committed=True)
        elif not committed:
            pending_error = _sanitized_close_error(close_error, committed=False)
    if pending_error is not None:
        raise pending_error from None


def _unlink_verified_leaf(
    leaf_name: str,
    *,
    directory_fd: int,
    leaf_fd: int,
    expected_stat: os.stat_result,
    field_name: str,
    secure_wipe: bool = False,
) -> None:
    try:
        current_stat = os.stat(leaf_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise OSError(f"{field_name} changed before cleanup") from None
    fd_stat = os.fstat(leaf_fd)
    if not _same_claim_snapshot(current_stat, expected_stat) or not _same_claim_snapshot(fd_stat, expected_stat):
        raise OSError(f"{field_name} changed before cleanup")
    if type(secure_wipe) is not bool:
        raise TypeError(f"{field_name} secure wipe flag is invalid")
    if secure_wipe:
        secure_wipe_regular_file_at(
            directory_fd,
            leaf_name,
            fd_stat,
            field_name=field_name,
        )
    expected_link_count = getattr(fd_stat, "st_nlink", 1) - 1
    os.unlink(leaf_name, dir_fd=directory_fd)
    _fsync_fd(directory_fd)
    try:
        os.stat(leaf_name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise OSError(f"{field_name} cleanup entry survived unlink")
    after_stat = os.fstat(leaf_fd)
    if getattr(after_stat, "st_nlink", 1) != expected_link_count:
        raise OSError(f"{field_name} link count changed unexpectedly during cleanup")


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


def _resolve_no_follow_flag(*, field_name: str) -> int:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise OSError(f"secure no-follow flag is invalid for {field_name}")
    return nofollow_flag


def _target_snapshot(file_stat: os.stat_result, *, field_name: str) -> _TargetSnapshot:
    required_values = {
        "st_dev": file_stat.st_dev,
        "st_ino": file_stat.st_ino,
        "st_mode": file_stat.st_mode,
        "st_nlink": file_stat.st_nlink,
        "st_size": file_stat.st_size,
        "st_mtime_ns": file_stat.st_mtime_ns,
        "st_ctime_ns": file_stat.st_ctime_ns,
    }
    if any(type(value) is not int for value in required_values.values()) or file_stat.st_size < 0:
        raise ValueError(f"{field_name} has invalid identity fields")
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"{field_name} must identify a regular file")
    if file_stat.st_nlink != 1:
        raise ValueError(f"{field_name} must not be hardlinked")
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise ValueError(f"{field_name} must be owned by the current user")
    return _TargetSnapshot(
        file_stat.st_dev,
        file_stat.st_ino,
        stat.S_IFMT(file_stat.st_mode),
        file_stat.st_nlink,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _capture_fd_evidence(
    fd: int,
    *,
    include_digest: bool,
    field_name: str,
    max_digest_bytes: int | None = None,
) -> tuple[_TargetSnapshot, bytes | None]:
    if isinstance(fd, bool) or not isinstance(fd, int) or fd < 0:
        raise TypeError("captured expected target requires an open file descriptor")
    if type(include_digest) is not bool:
        raise TypeError("include_digest must be a boolean")
    if max_digest_bytes is not None and (
        isinstance(max_digest_bytes, bool)
        or not isinstance(max_digest_bytes, int)
        or max_digest_bytes < 0
    ):
        raise TypeError("max_digest_bytes must be a non-negative integer")
    before = _target_snapshot(os.fstat(fd), field_name=field_name)
    digest: bytes | None = None
    if include_digest:
        effective_max_digest_bytes = (
            DEFAULT_MAX_TEXT_READ_BYTES if max_digest_bytes is None else max_digest_bytes
        )
        if before.size > effective_max_digest_bytes:
            raise OSError(errno.EFBIG, f"{field_name} is too large for secure version capture")
        if not hasattr(os, "pread"):
            raise OSError(errno.ENOTSUP, f"{field_name} cannot be securely versioned on this platform")
        hasher = hashlib.sha256()
        offset = 0
        while offset < before.size:
            try:
                chunk = os.pread(fd, min(65_536, before.size - offset), offset)
            except InterruptedError:
                continue
            if not chunk:
                raise OSError(f"{field_name} changed during secure version capture")
            hasher.update(chunk)
            offset += len(chunk)
        digest = hasher.digest()
    after = _target_snapshot(os.fstat(fd), field_name=field_name)
    if after != before:
        raise OSError(f"{field_name} changed during secure version capture")
    return after, digest


def _verify_expected_target_strict(fd: int, expected_target: ExpectedTarget) -> bool:
    if expected_target.kind is not ExpectedTargetKind.CAPTURED or expected_target.snapshot is None:
        return False
    current, content_digest = _capture_fd_evidence(
        fd,
        include_digest=expected_target.require_same_version,
        field_name="current target",
        max_digest_bytes=expected_target.max_digest_bytes,
    )
    expected = expected_target.snapshot
    identity_matches = (
        current.device,
        current.inode,
        current.file_type,
        current.link_count,
    ) == (
        expected.device,
        expected.inode,
        expected.file_type,
        expected.link_count,
    )
    if not identity_matches or not expected_target.require_same_version:
        return identity_matches
    return current.size == expected.size and content_digest == expected_target.content_digest


def _matches_expected_target(fd: int, expected_target: ExpectedTarget) -> bool:
    try:
        return _verify_expected_target_strict(fd, expected_target)
    except (OSError, ValueError):
        return False


def _verify_expected_target_with_retry(
    fd: int,
    expected_target: ExpectedTarget,
    *,
    field_name: str,
) -> bool:
    try:
        return _verify_expected_target_strict(fd, expected_target)
    except (OSError, ValueError):
        pass
    try:
        return _verify_expected_target_strict(fd, expected_target)
    except (OSError, ValueError):
        pass
    raise OSError(f"{field_name} verification failed")


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
    nofollow_flag = _resolve_no_follow_flag(field_name=field_name)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        raise OSError(f"secure file open is not supported for {field_name}")

    parts = _safe_path_parts(path, field_name=field_name)
    start_path = path.anchor if path.is_absolute() else "."
    directory_fd = os.open(start_path, os.O_RDONLY | directory_flag | getattr(os, "O_CLOEXEC", 0))
    result_fd: int | None = None
    primary_error: BaseException | None = None
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
            try:
                old_fd, directory_fd = directory_fd, None
                _close_fd_preserving_primary(
                    old_fd,
                    primary_error=None,
                    committed=False,
                )
            except BaseException as close_error:
                owned_next_fd, next_fd = next_fd, None
                _close_fd_preserving_primary(
                    owned_next_fd,
                    primary_error=close_error,
                    committed=False,
                )
                raise
            directory_fd = next_fd
            next_fd = None
        result_fd = os.open(
            parts[-1],
            flags | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
            mode,
            dir_fd=directory_fd,
        )
        return result_fd
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        owned_directory_fd, directory_fd = directory_fd, None
        if owned_directory_fd is not None:
            try:
                _close_fd_preserving_primary(
                    owned_directory_fd,
                    primary_error=primary_error,
                    committed=False,
                )
            except BaseException as cleanup_error:
                if result_fd is not None:
                    owned_result_fd, result_fd = result_fd, None
                    _close_fd_preserving_primary(
                        owned_result_fd,
                        primary_error=cleanup_error,
                        committed=False,
                    )
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise


def open_directory_without_following_symlinks(path: Path, *, field_name: str = "path") -> int:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    nofollow_flag = _resolve_no_follow_flag(field_name=field_name)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        raise OSError(f"secure directory open is not supported for {field_name}")
    if str(path) in {"", "."}:
        return os.open(
            ".",
            os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
        )
    if path.parent == path:
        return os.open(
            str(path),
            os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
        )
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
    nofollow_flag = _resolve_no_follow_flag(field_name=field_name)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory_flag is None:
        raise OSError(f"secure directory creation is not supported for {field_name}")
    if str(path) in {"", "."}:
        return os.open(
            ".",
            os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
        )
    if path.parent == path:
        return os.open(
            str(path),
            os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
        )

    parts = _safe_path_parts(path, field_name=field_name)
    start_path = path.anchor if path.is_absolute() else "."
    directory_fd = os.open(start_path, os.O_RDONLY | directory_flag | getattr(os, "O_CLOEXEC", 0))
    try:
        for component in parts:
            try:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory_fd,
                )
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=directory_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(
                    component,
                    os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=directory_fd,
                )
            try:
                old_fd, directory_fd = directory_fd, None
                _close_fd_preserving_primary(
                    old_fd,
                    primary_error=None,
                    committed=False,
                )
            except BaseException as close_error:
                owned_next_fd, next_fd = next_fd, None
                _close_fd_preserving_primary(
                    owned_next_fd,
                    primary_error=close_error,
                    committed=False,
                )
                raise
            directory_fd = next_fd
            next_fd = None
        return directory_fd
    except BaseException as exc:
        owned_directory_fd, directory_fd = directory_fd, None
        if owned_directory_fd is not None:
            _close_fd_preserving_primary(
                owned_directory_fd,
                primary_error=exc,
                committed=False,
            )
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
    except (MemoryError, RecursionError) as exc:
        raise OSError(f"{field_name} could not be opened") from exc
    except OSError:
        raise OSError(f"{field_name} could not be opened") from None
    try:
        try:
            assert_fd_is_regular_private_file(
                fd,
                field_name=field_name,
                require_private_mode=require_private_mode,
            )
        except RuntimeError as exc:
            raise OSError(str(exc)) from exc
        opened_stat: os.stat_result | None = None
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
    except (MemoryError, RecursionError) as exc:
        error = OSError(f"{field_name} could not be opened")
        owned_fd, fd = fd, None
        _close_fd_preserving_primary(
            owned_fd,
            primary_error=error,
            committed=False,
        )
        raise error from exc
    except Exception as exc:
        owned_fd, fd = fd, None
        _close_fd_preserving_primary(
            owned_fd,
            primary_error=exc,
            committed=False,
        )
        raise
    except BaseException as exc:
        owned_fd, fd = fd, None
        _close_fd_preserving_primary(
            owned_fd,
            primary_error=exc,
            committed=False,
        )
        raise
    primary_error: BaseException | None = None
    try:
        payload = handle.read(effective_max_bytes + 1)
        if expected_stat is not None and opened_stat is not None:
            final_stat = os.fstat(handle.fileno())
            if (
                final_stat.st_dev != opened_stat.st_dev
                or final_stat.st_ino != opened_stat.st_ino
                or final_stat.st_mode != opened_stat.st_mode
                or final_stat.st_size != opened_stat.st_size
                or getattr(final_stat, "st_nlink", 1) != getattr(opened_stat, "st_nlink", 1)
                or final_stat.st_mtime_ns != opened_stat.st_mtime_ns
                or final_stat.st_ctime_ns != opened_stat.st_ctime_ns
            ):
                raise OSError(f"{field_name} changed while reading")
            try:
                final_path_stat = path.lstat()
            except FileNotFoundError as exc:
                raise OSError(f"{field_name} changed while reading") from exc
            if (
                final_path_stat.st_dev != final_stat.st_dev
                or final_path_stat.st_ino != final_stat.st_ino
                or final_path_stat.st_mode != final_stat.st_mode
            ):
                raise OSError(f"{field_name} changed while reading")
    except (MemoryError, RecursionError) as exc:
        primary_error = OSError(f"{field_name} could not be read")
        raise primary_error from exc
    except Exception as exc:
        primary_error = exc
        raise
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        owned_handle, handle = handle, None
        _close_handle_preserving_primary(
            owned_handle,
            primary_error=primary_error,
            committed=False,
        )
    if len(payload) > effective_max_bytes:
        raise OSError(f"{field_name} is too large")
    try:
        return payload.decode(encoding)
    except (MemoryError, RecursionError) as exc:
        raise OSError(f"{field_name} could not be decoded") from exc


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
    nofollow_flag = _resolve_no_follow_flag(field_name=field_name)
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

    def _unlink_leaf_safely(
        leaf_name: str,
        expected_stat: os.stat_result,
        *,
        field_name: str,
    ) -> bool:
        try:
            current_stat = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not _same_leaf_snapshot(current_stat, expected_stat):
            raise OSError(f"{field_name} changed before cleanup")
        source_fd: int | None = None
        source_error: BaseException | None = None
        try:
            if not stat.S_ISLNK(expected_stat.st_mode):
                source_fd = os.open(
                    leaf_name,
                    os.O_RDONLY
                    | nofollow_flag
                    | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                source_stat = os.fstat(source_fd)
                if not _same_leaf_snapshot(source_stat, expected_stat):
                    raise OSError(f"{field_name} changed before cleanup")
            for _ in range(100):
                cleanup_name = f"{leaf_name}.{secrets.token_hex(8)}.cleanup"
                try:
                    _rename_without_replacing(
                        leaf_name,
                        cleanup_name,
                        directory_fd=parent_fd,
                        expected_source_stat=(
                            None if stat.S_ISLNK(expected_stat.st_mode) else source_stat
                        ),
                        expected_source_fd=source_fd,
                        field_name=f"{field_name} cleanup",
                    )
                except FileExistsError:
                    continue
                claimed_fd = source_fd
                source_fd = None
                quarantine_error: BaseException | None = None
                cleanup_committed = False
                claimed_matches = False
                try:
                    if stat.S_ISLNK(expected_stat.st_mode):
                        claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                    else:
                        if claimed_fd is None:
                            raise OSError(f"{field_name} cleanup claim FD is unavailable")
                        claimed_stat = os.fstat(claimed_fd)
                    if not _same_leaf_identity(claimed_stat, expected_stat):
                        raise OSError(f"{field_name} changed before cleanup")
                    claimed_matches = True
                    if claimed_fd is None:
                        os.unlink(cleanup_name, dir_fd=parent_fd)
                        _fsync_fd(parent_fd)
                    else:
                        _unlink_verified_leaf(
                            cleanup_name,
                            directory_fd=parent_fd,
                            leaf_fd=claimed_fd,
                            expected_stat=claimed_stat,
                            field_name=f"{field_name} cleanup",
                        )
                    cleanup_committed = True
                except BaseException as exc:
                    quarantine_error = exc
                    if claimed_matches and claimed_fd is not None:
                        try:
                            current_cleanup_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                            claimed_fd_stat = os.fstat(claimed_fd)
                            if _same_leaf_identity(current_cleanup_stat, claimed_fd_stat):
                                try:
                                    os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
                                except FileNotFoundError:
                                    pass
                                else:
                                    raise OSError(f"{field_name} restore target already exists")
                                _rename_without_replacing(
                                    cleanup_name,
                                    leaf_name,
                                    directory_fd=parent_fd,
                                    expected_source_stat=claimed_fd_stat,
                                    expected_source_fd=claimed_fd,
                                    field_name=f"{field_name} restore",
                                )
                                _fsync_fd(parent_fd)
                                restored_stat = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
                                if not _same_claim_snapshot(restored_stat, os.fstat(claimed_fd)):
                                    raise OSError(f"{field_name} changed after restore")
                            else:
                                _add_exception_note(exc, "quarantine replacement was retained")
                        except BaseException as restore_error:
                            _note_cleanup_failure(exc, restore_error)
                    else:
                        _add_exception_note(exc, "quarantine replacement was retained")
                    raise
                finally:
                    if claimed_fd is not None:
                        owned_claimed_fd, claimed_fd = claimed_fd, None
                        _close_fd_preserving_primary(
                            owned_claimed_fd,
                            primary_error=quarantine_error,
                            committed=cleanup_committed,
                        )
                return True
            raise OSError(f"{field_name} cleanup path could not be claimed")
        except BaseException as exc:
            source_error = exc
            raise
        finally:
            if source_fd is not None:
                owned_source_fd, source_fd = source_fd, None
                _close_fd_preserving_primary(
                    owned_source_fd,
                    primary_error=source_error,
                    committed=False,
                )

    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag | getattr(os, "O_CLOEXEC", 0)
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
                try:
                    temporary_stat = os.fstat(fd)
                except (OSError, ValueError) as exc:
                    owned_fd, fd = fd, None
                    _close_fd_preserving_primary(
                        owned_fd,
                        primary_error=exc,
                        committed=False,
                    )
                    raise OSError(f"{field_name} temporary file identity is unavailable") from exc
                except BaseException as exc:
                    owned_fd, fd = fd, None
                    _close_fd_preserving_primary(
                        owned_fd,
                        primary_error=exc,
                        committed=False,
                    )
                    raise
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
        except (MemoryError, RecursionError) as exc:
            error = OSError(f"{field_name} temporary file could not be opened")
            owned_fd, fd = fd, None
            _close_fd_preserving_primary(
                owned_fd,
                primary_error=error,
                committed=False,
            )
            raise error from exc
        except Exception as exc:
            owned_fd, fd = fd, None
            _close_fd_preserving_primary(
                owned_fd,
                primary_error=exc,
                committed=False,
            )
            raise
        except BaseException as exc:
            owned_fd, fd = fd, None
            _close_fd_preserving_primary(
                owned_fd,
                primary_error=exc,
                committed=False,
            )
            raise
        handle_primary_error: BaseException | None = None
        try:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError as exc:
                raise OSError(f"{field_name} temporary file could not be made private") from exc
            handle.write(payload)
            handle.flush()
            _fsync_fd(handle.fileno())
            temporary_stat = os.fstat(handle.fileno())
        except (MemoryError, RecursionError) as exc:
            try:
                temporary_stat = os.fstat(handle.fileno())
            except BaseException as stat_error:
                _note_cleanup_failure(exc, stat_error)
            handle_primary_error = OSError(f"{field_name} temporary file could not be written")
            raise handle_primary_error from exc
        except BaseException as exc:
            try:
                temporary_stat = os.fstat(handle.fileno())
            except (OSError, ValueError) as stat_error:
                _note_cleanup_failure(exc, stat_error)
            handle_primary_error = exc
            raise
        finally:
            owned_handle, handle = handle, None
            _close_handle_preserving_primary(
                owned_handle,
                primary_error=handle_primary_error,
                committed=False,
            )

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
                candidate_stat_at_creation: os.stat_result | None = None
                try:
                    candidate_stat_at_creation = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                    backup_stat = candidate_stat_at_creation
                    current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat.S_ISREG(backup_stat.st_mode)
                        or getattr(backup_stat, "st_nlink", 1) < 2
                        or not _same_leaf_inode(backup_stat, target_stat)
                        or not stat.S_ISREG(current_target_stat.st_mode)
                        or not _same_leaf_inode(current_target_stat, target_stat)
                    ):
                        raise OSError(f"{field_name} path changed during backup activation")
                    if not _unlink_leaf_safely(
                        path.name,
                        current_target_stat,
                        field_name=field_name,
                    ):
                        raise OSError(f"{field_name} disappeared before activation")
                    backup_name = candidate_name
                    backup_moved = True
                    _fsync_fd(parent_fd)
                    break
                except BaseException as exc:
                    if not backup_moved:
                        try:
                            candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                            if (
                                candidate_stat_at_creation is not None
                                and _same_leaf_inode(candidate_stat, candidate_stat_at_creation)
                            ):
                                try:
                                    os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                                except FileNotFoundError:
                                    backup_name = candidate_name
                                    backup_moved = True
                                else:
                                    if _unlink_leaf_safely(
                                        candidate_name,
                                        candidate_stat,
                                        field_name=f"{field_name} recovery backup candidate",
                                    ):
                                        _fsync_fd(parent_fd)
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
            activated_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as stat_error:
            raise OSError(f"{field_name} could not be inspected after activation") from stat_error
        if temporary_stat is None or not _same_leaf_identity(activated_stat, temporary_stat):
            raise OSError(f"{field_name} changed after activation")
        activation_stat = activated_stat
        _fsync_fd(parent_fd)
        transaction_active = False
        if backup_moved:
            try:
                backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    stat.S_ISREG(backup_stat.st_mode)
                    and getattr(backup_stat, "st_nlink", 1) == 1
                    and target_stat is not None
                    and _same_leaf_identity(backup_stat, target_stat)
                ):
                    for _ in range(100):
                        cleanup_name = f"{backup_name}.{secrets.token_hex(8)}.cleanup"
                        try:
                            _rename_without_replacing(
                                backup_name,
                                cleanup_name,
                                directory_fd=parent_fd,
                                field_name=f"{field_name} recovery backup cleanup",
                            )
                        except FileExistsError:
                            continue
                        except OSError:
                            break
                        try:
                            claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                            if (
                                stat.S_ISREG(claimed_stat.st_mode)
                                and getattr(claimed_stat, "st_nlink", 1) == 1
                                and target_stat is not None
                                and _same_leaf_identity(claimed_stat, target_stat)
                            ):
                                if not _unlink_leaf_safely(
                                    cleanup_name,
                                    claimed_stat,
                                    field_name=f"{field_name} recovery backup cleanup",
                                ):
                                    raise OSError(f"{field_name} recovery backup disappeared during cleanup")
                                _fsync_fd(parent_fd)
                            else:
                                _rename_without_replacing(
                                    cleanup_name,
                                    backup_name,
                                    directory_fd=parent_fd,
                                    field_name=f"{field_name} recovery backup restore",
                                )
                                _fsync_fd(parent_fd)
                        except BaseException:
                            try:
                                _rename_without_replacing(
                                    cleanup_name,
                                    backup_name,
                                    directory_fd=parent_fd,
                                    field_name=f"{field_name} recovery backup restore",
                                )
                                _fsync_fd(parent_fd)
                            except BaseException:
                                pass
                        break
            except OSError:
                pass
            except BaseException:
                pass
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
                            same_activation = _same_leaf_identity
                        else:
                            same_activation = _same_leaf_identity
                        if expected_activation_stat is None or not same_activation(current_target_stat, expected_activation_stat):
                            raise OSError(f"{field_name} target changed during rollback")
                        if not _unlink_leaf_safely(
                            path.name,
                            current_target_stat,
                            field_name=f"{field_name} rollback",
                        ):
                            raise OSError(f"{field_name} disappeared during rollback")
                        _fsync_fd(parent_fd)
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
                        _fsync_fd(parent_fd)
                    else:
                        raise OSError(f"{field_name} target exists during rollback")
            except BaseException as rollback_error:
                _note_cleanup_failure(primary_error, rollback_error)
        temporary_cleanup_error: OSError | None = None
        if temp_name:
            try:
                if temporary_stat is None:
                    raise OSError(f"{field_name} temporary file identity is unavailable")
                if _unlink_leaf_safely(
                    temp_name,
                    temporary_stat,
                    field_name=f"{field_name} temporary file",
                ):
                    _fsync_fd(parent_fd)
            except OSError as exc:
                temporary_cleanup_error = exc
            except BaseException as cleanup_exception:
                _note_cleanup_failure(primary_error, cleanup_exception)
        if temporary_cleanup_error is not None:
            _note_cleanup_failure(primary_error, temporary_cleanup_error)
        if isinstance(primary_error, (MemoryError, RecursionError)):
            raise OSError(f"{field_name} could not be written") from primary_error
        raise
    finally:
        owned_parent_fd, parent_fd = parent_fd, None
        _close_fd_preserving_primary(
            owned_parent_fd,
            primary_error=primary_error,
            committed=primary_error is None,
        )


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


def create_bytes_atomically_without_following_symlinks(
    path: Path,
    data: bytes,
    *,
    field_name: str = "path",
) -> None:
    _conditional_namespace_operation(
        path,
        ExpectedTarget.missing(),
        replacement_data=data,
        field_name=field_name,
    )


def _create_private_transaction_directory(
    parent_fd: int,
    leaf_name: str,
    *,
    field_name: str,
) -> tuple[str, int]:
    nofollow_flag = _resolve_no_follow_flag(field_name=field_name)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if not isinstance(directory_flag, int) or isinstance(directory_flag, bool) or directory_flag <= 0:
        raise OSError(f"secure transaction directory is not supported for {field_name}")
    for _ in range(100):
        transaction_name = f".{leaf_name}.{secrets.token_hex(16)}.txn"
        try:
            os.mkdir(transaction_name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        transaction_fd: int | None = None
        try:
            transaction_fd = os.open(
                transaction_name,
                os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            assert_fd_is_private_directory(transaction_fd, field_name=f"{field_name} transaction directory")
            named_stat = os.stat(transaction_name, dir_fd=parent_fd, follow_symlinks=False)
            opened_stat = os.fstat(transaction_fd)
            if (named_stat.st_dev, named_stat.st_ino, stat.S_IFMT(named_stat.st_mode)) != (
                opened_stat.st_dev,
                opened_stat.st_ino,
                stat.S_IFMT(opened_stat.st_mode),
            ):
                raise OSError(f"{field_name} transaction directory changed during creation")
            return transaction_name, transaction_fd
        except BaseException as exc:
            if transaction_fd is not None:
                owned_transaction_fd, transaction_fd = transaction_fd, None
                _close_fd_preserving_primary(
                    owned_transaction_fd,
                    primary_error=exc,
                    committed=False,
                )
            try:
                os.rmdir(transaction_name, dir_fd=parent_fd)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            raise
    raise OSError(f"failed to create private transaction directory for {field_name}")


def _stage_private_bytes(
    transaction_fd: int,
    data: bytes,
    *,
    field_name: str,
) -> ExpectedTarget:
    nofollow_flag = _resolve_no_follow_flag(field_name=field_name)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow_flag | getattr(os, "O_CLOEXEC", 0)
    fd = os.open("staged", flags, 0o600, dir_fd=transaction_fd)

    def remove_staged_after_failure(primary_error: BaseException) -> None:
        nonlocal fd
        owned_fd, fd = fd, None
        if owned_fd is not None:
            _close_fd_preserving_primary(
                owned_fd,
                primary_error=primary_error,
                committed=False,
            )
        try:
            os.unlink("staged", dir_fd=transaction_fd)
            _fsync_fd(transaction_fd)
        except FileNotFoundError:
            pass
        except BaseException as cleanup_error:
            _note_cleanup_failure(primary_error, cleanup_error)

    try:
        os.fchmod(fd, 0o600)
        payload = memoryview(data)
        offset = 0
        while offset < len(payload):
            try:
                written = os.write(fd, payload[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError(f"{field_name} staged file could not be written")
            offset += written
        _fsync_fd(fd)
        staged_expected = ExpectedTarget.captured(
            fd,
            max_digest_bytes=len(data),
        )
    except BaseException as exc:
        remove_staged_after_failure(exc)
        raise
    try:
        owned_fd, fd = fd, None
        if owned_fd is None:
            raise OSError(f"{field_name} staged file descriptor is unavailable")
        _close_fd_preserving_primary(
            owned_fd,
            primary_error=None,
            committed=False,
        )
    except BaseException as close_error:
        remove_staged_after_failure(close_error)
        raise
    return staged_expected


def _restore_conditional_claim(
    transaction_fd: int,
    parent_fd: int,
    leaf_name: str,
    *,
    claimed_fd: int,
    expected_claim_stat: os.stat_result,
    field_name: str,
) -> None:
    try:
        claim_stat = os.stat("claimed", dir_fd=transaction_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise OSError(f"{field_name} claim disappeared before restore") from None
    if not _same_claim_snapshot(claim_stat, expected_claim_stat):
        raise OSError(f"{field_name} claim changed before restore")
    fd_stat = os.fstat(claimed_fd)
    if not _same_claim_snapshot(fd_stat, expected_claim_stat):
        raise OSError(f"{field_name} claim FD changed before restore")
    try:
        os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        raise OSError(f"{field_name} restore target already exists")
    _rename_without_replacing(
        "claimed",
        leaf_name,
        directory_fd=transaction_fd,
        target_directory_fd=parent_fd,
        expected_source_stat=fd_stat,
        expected_source_fd=claimed_fd,
        field_name=f"{field_name} restore",
    )
    _fsync_fd(transaction_fd)
    _fsync_fd(parent_fd)
    restored_stat = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
    if not _same_claim_snapshot(restored_stat, os.fstat(claimed_fd)):
        raise OSError(f"{field_name} changed after restore")


def _conditional_namespace_operation(
    path: Path,
    expected_target: ExpectedTarget,
    *,
    replacement_data: bytes | None,
    field_name: str,
    secure_wipe: bool = False,
) -> bool | None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    if not isinstance(expected_target, ExpectedTarget):
        raise TypeError("expected_target must be ExpectedTarget")
    if replacement_data is not None and type(replacement_data) is not bytes:
        raise TypeError("replacement data must be bytes")
    if type(secure_wipe) is not bool:
        raise TypeError("secure_wipe must be a boolean")
    if expected_target.kind is ExpectedTargetKind.UNKNOWN:
        raise OSError(f"{field_name} expected target is unknown")
    assert_safe_path_components(path, field_name=field_name)
    _resolve_renameat2(field_name=field_name)

    parent_fd = ensure_directory_without_following_symlinks(
        path.parent,
        field_name=f"{field_name} directory",
    )
    primary_error: BaseException | None = None
    transaction_name = ""
    transaction_fd: int | None = None
    staged_present = False
    claim_present = False
    claim_verified = False
    claim_verification_failed = False
    claimed_fd: int | None = None
    claimed_stat: os.stat_result | None = None
    committed = False
    postcommit_cleanup_error: BaseException | None = None
    try:
        assert_fd_is_private_directory(parent_fd, field_name=f"{field_name} directory")
        if expected_target.kind is ExpectedTargetKind.MISSING and replacement_data is None:
            try:
                os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            raise OSError(f"{field_name} expected target to be missing")

        transaction_name, transaction_fd = _create_private_transaction_directory(
            parent_fd,
            path.name,
            field_name=field_name,
        )
        staged_expected: ExpectedTarget | None = None
        if replacement_data is not None:
            staged_expected = _stage_private_bytes(transaction_fd, replacement_data, field_name=field_name)
            staged_present = True

        def remove_activated_target_for_rollback() -> None:
            if staged_expected is None:
                raise OSError(f"{field_name} staged target evidence is unavailable")
            activated_target_fd: int | None = None
            try:
                activated_target_fd = os.open(
                    path.name,
                    os.O_RDONLY
                    | getattr(os, "O_NONBLOCK", 0)
                    | _resolve_no_follow_flag(field_name=field_name)
                    | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                activated_target_stat = os.fstat(activated_target_fd)
                if not _verify_expected_target_with_retry(
                    activated_target_fd,
                    staged_expected,
                    field_name=f"{field_name} rollback activation",
                ):
                    raise OSError(f"{field_name} rollback target does not match activation")
                _unlink_verified_leaf(
                    path.name,
                    directory_fd=parent_fd,
                    leaf_fd=activated_target_fd,
                    expected_stat=activated_target_stat,
                    field_name=f"{field_name} rollback activation",
                )
            except FileNotFoundError:
                return
            finally:
                if activated_target_fd is not None:
                    _close_fd_preserving_primary(
                        activated_target_fd,
                        primary_error=primary_error,
                        committed=False,
                    )

        if expected_target.kind is ExpectedTargetKind.MISSING:
            try:
                _rename_without_replacing(
                    "staged",
                    path.name,
                    directory_fd=transaction_fd,
                    target_directory_fd=parent_fd,
                    field_name=field_name,
                )
            except FileExistsError:
                raise OSError(f"{field_name} expected target to be missing") from None
            staged_present = False
            _fsync_fd(transaction_fd)
            _fsync_fd(parent_fd)
            activated_fd: int | None = None
            activation_error: BaseException | None = None
            activation_verification_failed = False
            try:
                try:
                    activated_fd = os.open(
                        path.name,
                        os.O_RDONLY
                        | getattr(os, "O_NONBLOCK", 0)
                        | _resolve_no_follow_flag(field_name=field_name)
                        | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=parent_fd,
                    )
                    activation_matches = _verify_expected_target_with_retry(
                        activated_fd,
                        staged_expected,
                        field_name=f"{field_name} activation",
                    )
                except (OSError, ValueError):
                    activation_verification_failed = True
                if activation_verification_failed:
                    raise OSError(f"{field_name} changed after activation")
                if not activation_matches:
                    raise OSError(f"{field_name} changed after activation")
                committed = True
            except BaseException as exc:
                activation_error = exc
                raise
            finally:
                if activated_fd is not None:
                    owned_activated_fd, activated_fd = activated_fd, None
                    _close_fd_preserving_primary(
                        owned_activated_fd,
                        primary_error=activation_error,
                        committed=committed,
                    )
            return None

        source_fd: int | None = None
        claim_succeeded = False
        source_error: BaseException | None = None
        source_boundary_error: OSError | None = None
        try:
            source_fd = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | _resolve_no_follow_flag(field_name=field_name)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            source_stat = os.fstat(source_fd)
            source_matches = _verify_expected_target_with_retry(
                source_fd,
                expected_target,
                field_name=f"{field_name} source",
            )
            if not source_matches:
                raise _TargetChangedError(f"{field_name} changed before cleanup")
            _rename_without_replacing(
                path.name,
                "claimed",
                directory_fd=parent_fd,
                target_directory_fd=transaction_fd,
                expected_source_stat=source_stat,
                expected_source_fd=source_fd,
                field_name=f"{field_name} claim",
            )
            claim_succeeded = True
        except FileNotFoundError:
            source_error = OSError(f"{field_name} expected target is missing")
            source_boundary_error = source_error
        except _TargetChangedError:
            source_error = OSError(f"{field_name} does not match expected target")
            source_boundary_error = source_error
        except OSError:
            source_error = OSError(f"{field_name} source verification failed")
            source_boundary_error = source_error
        except BaseException as exc:
            source_error = exc
            raise
        finally:
            if source_fd is not None and not claim_succeeded:
                owned_source_fd, source_fd = source_fd, None
                _close_fd_preserving_primary(
                    owned_source_fd,
                    primary_error=source_error,
                    committed=False,
                )
        if source_boundary_error is not None:
            raise source_boundary_error
        claimed_fd = source_fd
        source_fd = None
        claim_present = True
        _fsync_fd(parent_fd)
        _fsync_fd(transaction_fd)

        claimed_stat = os.fstat(claimed_fd)
        try:
            claim_matches = _verify_expected_target_with_retry(
                claimed_fd,
                expected_target,
                field_name=f"{field_name} claim",
            )
        except (OSError, ValueError):
            claim_verification_failed = True
            raise
        if not claim_matches:
            raise OSError(f"{field_name} does not match expected target")
        claim_verified = True

        if replacement_data is None:
            _unlink_verified_leaf(
                "claimed",
                directory_fd=transaction_fd,
                leaf_fd=claimed_fd,
                expected_stat=claimed_stat,
                field_name=f"{field_name} cleanup",
                secure_wipe=secure_wipe,
            )
            claim_present = False
            _fsync_fd(transaction_fd)
            committed = True
            return True

        try:
            _rename_without_replacing(
                "staged",
                path.name,
                directory_fd=transaction_fd,
                target_directory_fd=parent_fd,
                field_name=field_name,
            )
        except FileExistsError:
            raise OSError(f"{field_name} target appeared before activation") from None
        staged_present = False
        _fsync_fd(transaction_fd)
        _fsync_fd(parent_fd)
        activated_fd: int | None = None
        activation_error: BaseException | None = None
        try:
            activated_fd = os.open(
                path.name,
                os.O_RDONLY
                | getattr(os, "O_NONBLOCK", 0)
                | _resolve_no_follow_flag(field_name=field_name)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            activated_stat = os.fstat(activated_fd)
            activation_matches = staged_expected is not None and _verify_expected_target_with_retry(
                activated_fd,
                staged_expected,
                field_name=f"{field_name} activation",
            )
            if not activation_matches:
                try:
                    target_matches_fd = _same_claim_snapshot(
                        os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False),
                        activated_stat,
                    )
                except FileNotFoundError:
                    target_matches_fd = False
                if not target_matches_fd:
                    raise OSError(f"{field_name} does not match expected target")
                raise OSError(f"{field_name} changed after activation")
            committed = True
        except BaseException as exc:
            activation_error = exc
            raise
        finally:
            if activated_fd is not None:
                owned_activated_fd, activated_fd = activated_fd, None
                _close_fd_preserving_primary(
                    owned_activated_fd,
                    primary_error=activation_error,
                    committed=committed,
                )
        try:
            _unlink_verified_leaf(
                "claimed",
                directory_fd=transaction_fd,
                leaf_fd=claimed_fd,
                expected_stat=claimed_stat,
                field_name=f"{field_name} cleanup",
            )
        except BaseException as cleanup_error:
            _add_exception_note(cleanup_error, "committed; quarantine cleanup pending")
            raise
        claim_present = False
        _fsync_fd(transaction_fd)
        return None
    except BaseException as exc:
        primary_error = exc
        if claim_present and transaction_fd is not None and not committed and claim_verified:
            if not staged_present and staged_expected is not None:
                try:
                    # claim_present is set only after helper definition above.
                    remove_activated_target_for_rollback()  # pylint: disable=used-before-assignment
                except BaseException as remove_error:
                    _note_cleanup_failure(primary_error, remove_error)
            try:
                _restore_conditional_claim(
                    transaction_fd,
                    parent_fd,
                    path.name,
                    claimed_fd=claimed_fd,
                    expected_claim_stat=claimed_stat,
                    field_name=field_name,
                )
                claim_present = False
            except BaseException as restore_error:
                _note_cleanup_failure(primary_error, restore_error)
                try:
                    os.stat("claimed", dir_fd=transaction_fd, follow_symlinks=False)
                except FileNotFoundError:
                    claim_present = False
        elif claim_present and not claim_verified:
            if claim_verification_failed:
                _add_exception_note(primary_error, "claim verification failed; unverified claim retained")
            else:
                _add_exception_note(primary_error, "unverified claim retained")
        if staged_present and transaction_fd is not None:
            try:
                os.unlink("staged", dir_fd=transaction_fd)
                staged_present = False
                _fsync_fd(transaction_fd)
            except BaseException as cleanup_error:
                _note_cleanup_failure(primary_error, cleanup_error)
        raise
    finally:
        if claimed_fd is not None:
            owned_claimed_fd, claimed_fd = claimed_fd, None
            try:
                _close_fd_preserving_primary(
                    owned_claimed_fd,
                    primary_error=primary_error,
                    committed=committed,
                )
            except _CONTROL_FLOW_CLOSE_ERRORS as cleanup_error:
                if not committed:
                    raise
                postcommit_cleanup_error = cleanup_error
        if transaction_name and not claim_present and not staged_present:
            try:
                if transaction_fd is None:
                    raise OSError(f"{field_name} transaction directory FD is unavailable")
                transaction_fd_stat = os.fstat(transaction_fd)
                transaction_name_stat = os.stat(
                    transaction_name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if (
                    transaction_name_stat.st_dev,
                    transaction_name_stat.st_ino,
                    stat.S_IFMT(transaction_name_stat.st_mode),
                ) != (
                    transaction_fd_stat.st_dev,
                    transaction_fd_stat.st_ino,
                    stat.S_IFMT(transaction_fd_stat.st_mode),
                ):
                    raise OSError(f"{field_name} transaction directory changed before cleanup")
                os.rmdir(transaction_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                elif committed:
                    if postcommit_cleanup_error is None:
                        postcommit_cleanup_error = _sanitized_close_error(
                            cleanup_error,
                            committed=True,
                        )
                    else:
                        _note_cleanup_failure(postcommit_cleanup_error, cleanup_error)
                elif not committed:
                    raise
        if transaction_fd is not None:
            owned_transaction_fd, transaction_fd = transaction_fd, None
            try:
                _close_fd_preserving_primary(
                    owned_transaction_fd,
                    primary_error=primary_error,
                    committed=committed,
                )
            except _CONTROL_FLOW_CLOSE_ERRORS as cleanup_error:
                if not committed:
                    raise
                if postcommit_cleanup_error is None:
                    postcommit_cleanup_error = cleanup_error
                else:
                    _note_cleanup_failure(postcommit_cleanup_error, cleanup_error)
        owned_parent_fd, parent_fd = parent_fd, None
        _close_fd_preserving_primary(
            owned_parent_fd,
            primary_error=primary_error,
            committed=committed,
        )
        if postcommit_cleanup_error is not None:
            raise postcommit_cleanup_error from None


def replace_bytes_atomically_if_identity(
    path: Path,
    data: bytes,
    expected_target: ExpectedTarget,
    *,
    field_name: str = "path",
) -> None:
    _conditional_namespace_operation(
        path,
        expected_target,
        replacement_data=data,
        field_name=field_name,
    )


def unlink_file_if_identity(
    path: Path,
    expected_target: ExpectedTarget,
    *,
    field_name: str = "path",
    secure_wipe: bool = False,
) -> bool:
    result = _conditional_namespace_operation(
        path,
        expected_target,
        replacement_data=None,
        field_name=field_name,
        secure_wipe=secure_wipe,
    )
    return bool(result)


def normalize_backup_archive_path(value: object, *, field_name: str = "backup archive path") -> str:
    """Return a canonical, relative POSIX archive path or reject it."""
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError(f"{field_name} must be text")
    if not value or len(value) > 4096:
        raise RuntimeError(f"{field_name} is invalid")
    if "\x00" in value or "\\" in value or value.startswith("/") or value.startswith("//"):
        raise RuntimeError(f"{field_name} is not a safe relative path")
    if len(value) >= 2 and value[1] == ":":
        raise RuntimeError(f"{field_name} is not a safe relative path")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise RuntimeError(f"{field_name} contains an invalid control character")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise RuntimeError(f"{field_name} contains an unsafe path component")
    normalized_parts = tuple(unicodedata.normalize("NFC", part) for part in parts)
    if any(not part for part in normalized_parts):
        raise RuntimeError(f"{field_name} contains an empty path component")
    return "/".join(normalized_parts)


def assert_backup_source_regular_file(path: Path, *, field_name: str = "backup source") -> os.stat_result:
    """Validate a user-owned, unlinked regular source file and return its lstat."""
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    assert_safe_path_components(path, field_name=field_name)
    assert_no_symlink_ancestors(path, field_name=field_name)
    try:
        source_stat = os.lstat(path)
    except FileNotFoundError:
        raise OSError(f"{field_name} does not exist") from None
    if not stat.S_ISREG(source_stat.st_mode):
        raise OSError(f"{field_name} must be a regular file")
    if getattr(source_stat, "st_nlink", 1) != 1:
        raise OSError(f"{field_name} must not be hardlinked")
    if hasattr(os, "getuid") and source_stat.st_uid != os.getuid():
        raise OSError(f"{field_name} must be owned by the current user")
    return source_stat


def assert_backup_target_not_within_sources(
    target: Path,
    source_roots: object,
    *,
    field_name: str = "backup target",
) -> None:
    """Reject a backup target that aliases or nests any source root."""
    if not isinstance(target, Path):
        raise RuntimeError(f"{field_name} must be a path")
    if isinstance(source_roots, (str, bytes, Path)):
        raise RuntimeError("backup source roots must be a sequence of paths")
    try:
        roots = tuple(source_roots)  # type: ignore[arg-type]
    except TypeError as exc:
        raise RuntimeError("backup source roots must be a sequence of paths") from exc
    if not roots:
        raise RuntimeError("backup source roots must not be empty")
    assert_safe_path_components(target, field_name=field_name)
    assert_no_symlink_ancestors(target, field_name=field_name)
    try:
        target_resolved = target.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"{field_name} could not be resolved") from exc
    for index, root in enumerate(roots):
        if not isinstance(root, Path):
            raise RuntimeError(f"backup source root {index} must be a path")
        root_label = f"backup source root {index}"
        assert_safe_path_components(root, field_name=root_label)
        assert_no_symlink_ancestors(root, field_name=root_label)
        try:
            root_resolved = root.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(f"{root_label} could not be resolved") from exc
        if target_resolved == root_resolved or root_resolved in target_resolved.parents:
            raise RuntimeError(f"{field_name} must not be inside a backup source")
