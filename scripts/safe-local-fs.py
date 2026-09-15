#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import secrets
import shlex
import stat
import sys
import time
from pathlib import Path


COPY_CHUNK_SIZE = 1 << 20


def _source_file_signature(stat_result: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        stat_result.st_dev,
        stat_result.st_ino,
        stat_result.st_mode,
        stat_result.st_size,
        stat_result.st_mtime_ns,
        stat_result.st_nlink,
    )


MAX_TREE_ENTRIES = 100_000
MAX_TREE_FILE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024 * 1024
MANIFEST_SCHEMA_VERSION = 1
INSTALL_PHASE_FILE = ".install-phase"
INSTALL_PHASES = frozenset({"pre-activation", "recovery-required"})
INSTALL_PHASE_MAX_BYTES = 64
MAX_STALE_INSTALL_WORKSPACES = 32
STALE_CLEANUP_MAX_ENTRIES = 4096
STALE_CLEANUP_MAX_DEPTH = 64
STALE_CLEANUP_BUDGET_NS = 5_000_000_000
TREE_IO_PASSES = 7
TREE_IO_ENTRY_PASSES = 7
_TRUSTED_STICKY_SYSTEM_DIRS = frozenset({Path("/tmp"), Path("/var/tmp"), Path("/dev/shm")})


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _validate_absolute(path: str, label: str) -> Path:
    if "\x00" in path:
        fail(f"{label} contains invalid null byte")
    target = Path(path)
    if not target.is_absolute():
        fail(f"{label} must be absolute: {path}")
    if target == Path("/"):
        fail(f"{label} must not be filesystem root")
    if ".." in target.parts:
        fail(f"{label} must not contain parent traversal: {path}")
    return target


def _open_dir_chain(path: Path, *, action: str, create: bool = False, missing_ok: bool = False) -> int | None:
    if path == Path("/"):
        return os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if not part or part in {".", ".."}:
                fail(f"invalid path component during {action}: {path}")
            try:
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                if missing_ok:
                    _close_fds(fd, action=action)
                    return None
                if not create:
                    fail(f"path is missing during {action}: {path}")
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    # Another process created component; verify it below.
                    pass
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    fail(f"refusing to follow symlink during {action}: {path}")
                fail(f"failed to open path during {action}: {path}: {exc}")
            _close_fds(fd, action=action)
            fd = next_fd
        return fd
    except BaseException:
        with context_suppress():
            _close_fds(fd, action=action, primary_error=sys.exc_info()[1])
        raise


def _validate_private_dir_chain(path: Path, *, action: str) -> None:
    """Validate existing persistent-directory ancestors through pinned descriptors."""
    if path == Path("/"):
        raise OSError(f"persistent directory must not be filesystem root during {action}: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptors: list[int] = []
    current_path = Path("/")
    try:
        current_fd = os.open("/", flags)
        descriptors.append(current_fd)
        root_stat = os.fstat(current_fd)
        if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_uid != 0 or root_stat.st_mode & 0o022:
            raise OSError(f"untrusted persistent directory ancestor during {action}: {current_path}")
        for part in path.parts[1:]:
            if not part or part in {".", ".."}:
                raise OSError(f"invalid persistent directory component during {action}: {path}")
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise OSError(f"refusing to follow symlink during {action}: {current_path / part}") from exc
                raise
            descriptors.append(next_fd)
            current_fd = next_fd
            current_path /= part
            current_stat = os.fstat(next_fd)
            if not stat.S_ISDIR(current_stat.st_mode):
                raise OSError(f"persistent directory ancestor is not a directory during {action}: {current_path}")
            if current_stat.st_uid not in {0, os.geteuid()}:
                raise OSError(f"persistent directory ancestor has untrusted owner during {action}: {current_path}")
            writable = current_stat.st_mode & 0o022
            sticky_system_root = (
                current_stat.st_uid == 0
                and current_path in _TRUSTED_STICKY_SYSTEM_DIRS
                and current_stat.st_mode & stat.S_ISVTX
            )
            if writable and not sticky_system_root:
                raise OSError(f"persistent directory ancestor is writable during {action}: {current_path}")
            if current_path == path and current_stat.st_uid != os.geteuid():
                raise OSError(f"persistent directory root is not owned by current user during {action}: {path}")
    finally:
        _close_fds(*descriptors, action=action, primary_error=sys.exc_info()[1])


def _validate_nearest_private_dir_chain(path: Path, *, action: str) -> None:
    candidate = path
    while candidate != Path("/"):
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError:
            candidate = candidate.parent
            continue
        if stat.S_ISLNK(candidate_stat.st_mode):
            raise OSError(f"refusing to follow symlink during {action}: {candidate}")
        if not stat.S_ISDIR(candidate_stat.st_mode):
            raise OSError(f"persistent directory ancestor is not a directory during {action}: {candidate}")
        _validate_private_dir_chain(candidate, action=action)
        return
    raise OSError(f"no private persistent directory ancestor during {action}: {path}")


class context_suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> bool:
        return True


def _close_fds(*fds: int, action: str, primary_error: BaseException | None = None) -> None:
    cleanup_errors: list[BaseException] = []
    for fd in fds:
        try:
            os.close(fd)
        except BaseException as exc:
            cleanup_errors.append(exc)
    if not cleanup_errors:
        return
    if primary_error is not None:
        primary_error.add_note(f"{action} descriptor cleanup failed")
        return
    raise OSError(f"{action} descriptor cleanup failed") from cleanup_errors[0]


def _open_parent(path: Path, *, action: str, create: bool = False, missing_ok: bool = False) -> tuple[int | None, str]:
    parent = path.parent
    if not path.name:
        fail(f"invalid path during {action}: {path}")
    return _open_dir_chain(parent, action=action, create=create, missing_ok=missing_ok), path.name


def _lstat_at(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _stat_identity(stat_result: os.stat_result) -> tuple[int, int, int]:
    return (stat_result.st_dev, stat_result.st_ino, stat_result.st_mode)


def _same_identity(left: os.stat_result | None, right: os.stat_result | None) -> bool:
    return left is not None and right is not None and _stat_identity(left) == _stat_identity(right)


def _find_identity_residue(
    parent_fd: int,
    candidates: tuple[tuple[str, Path], ...],
    expected_stat: os.stat_result,
) -> Path | None:
    for name, path in candidates:
        try:
            current_stat = _lstat_at(parent_fd, name)
        except OSError:
            continue
        if _same_identity(expected_stat, current_stat):
            return path
    return None


def _identity_text(stat_result: os.stat_result) -> str:
    return ":".join(str(value) for value in _stat_identity(stat_result))


def _parse_identity(value: str, *, action: str) -> tuple[int, int, int]:
    parts = value.split(":")
    if len(parts) != 3:
        fail(f"invalid filesystem identity during {action}: {value}")
    try:
        identity = (int(parts[0], 10), int(parts[1], 10), int(parts[2], 10))
    except ValueError:
        fail(f"invalid filesystem identity during {action}: {value}")
    if any(part < 0 for part in identity):
        fail(f"invalid filesystem identity during {action}: {value}")
    return identity


def _rename_without_replacing(
    source_name: str,
    target_name: str,
    *,
    directory_fd: int,
    target_directory_fd: int | None = None,
    expected_source_stat: os.stat_result | None = None,
    action: str,
) -> None:
    """Atomically claim a name; same-UID mutations after checks need cooperation."""
    if expected_source_stat is not None:
        source_stat = _lstat_at(directory_fd, source_name)
        if source_stat is None or not _same_identity(source_stat, expected_source_stat):
            raise OSError(f"source changed during {action}: {source_name}")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOTSUP, f"no-clobber rename is not supported during {action}") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    target_fd = directory_fd if target_directory_fd is None else target_directory_fd
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        target_fd,
        os.fsencode(target_name),
        1,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target_name)


def _rename_exchange(
    source_name: str,
    target_name: str,
    *,
    directory_fd: int,
    target_directory_fd: int | None = None,
    action: str,
) -> None:
    """Exchange pinned-directory names; Linux has no conditional inode exchange."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = libc.renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOTSUP, f"no atomic exchange is supported during {action}") from exc
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    target_fd = directory_fd if target_directory_fd is None else target_directory_fd
    result = renameat2(
        directory_fd,
        os.fsencode(source_name),
        target_fd,
        os.fsencode(target_name),
        2,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target_name)


def _cleanup_temporary_file(
    parent_fd: int,
    temporary_name: str,
    expected_stat: os.stat_result | None,
    *,
    action: str,
) -> None:
    if expected_stat is None:
        return
    try:
        parent_stat = os.fstat(parent_fd)
    except OSError:
        return
    if (
        parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o022
        or (parent_stat.st_mode & 0o170000) != 0o040000
    ):
        return
    current_stat = _lstat_at(parent_fd, temporary_name)
    if current_stat is None or not _same_identity(current_stat, expected_stat):
        return
    for _ in range(100):
        cleanup_name = f"{temporary_name}.{secrets.token_hex(8)}.cleanup"
        try:
            _rename_without_replacing(
                temporary_name,
                cleanup_name,
                directory_fd=parent_fd,
                action=f"{action} temporary cleanup",
                expected_source_stat=expected_stat,
            )
        except FileExistsError:
            continue
        except FileNotFoundError:
            return
        try:
            os.unlink(cleanup_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except BaseException:
            with context_suppress():
                _rename_without_replacing(
                    cleanup_name,
                    temporary_name,
                    directory_fd=parent_fd,
                    action=f"{action} temporary restore",
                    expected_source_stat=expected_stat,
                )
                os.fsync(parent_fd)
            raise
        return
    raise OSError(f"failed to claim temporary file cleanup path during {action}")


def _assert_target_unchanged(
    parent_fd: int,
    name: str,
    expected_stat: os.stat_result | None,
    *,
    action: str,
) -> None:
    current_stat = _lstat_at(parent_fd, name)
    if expected_stat is None:
        if current_stat is not None:
            raise OSError(f"destination changed during {action}")
        return
    if current_stat is None or not _same_identity(current_stat, expected_stat):
        raise OSError(f"destination changed during {action}")


def _assert_expected_identity(
    parent_fd: int,
    name: str,
    expected_identity: str,
    *,
    action: str,
    path: Path,
    role: str = "destination",
) -> None:
    current_stat = _lstat_at(parent_fd, name)
    if expected_identity == "missing":
        if current_stat is not None:
            raise OSError(f"{role} changed during {action}: {path}")
        return
    if current_stat is None or _stat_identity(current_stat) != _parse_identity(expected_identity, action=action):
        raise OSError(f"{role} changed during {action}: {path}")


def _require_private_identity_removal_parent(
    parent_fd: int,
    *,
    action: str,
    path: Path,
) -> None:
    try:
        parent_stat = os.fstat(parent_fd)
    except OSError as exc:
        raise OSError(f"could not inspect removal parent during {action}: {path}") from exc
    if (
        parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o022
        or not stat_is_dir_no_follow(parent_stat.st_mode)
    ):
        raise OSError(f"identity-checked removal requires private parent during {action}: {path}")


def _check_leaf(parent_fd: int, name: str, path: Path, *, action: str, kind: str, must_exist: bool) -> None:
    stat_result = _lstat_at(parent_fd, name)
    if stat_result is None:
        if must_exist:
            fail(f"path is missing during {action}: {path}")
        return
    mode = stat_result.st_mode
    if stat_is_symlink_no_follow(mode):
        fail(f"refusing to follow symlink during {action}: {path}")
    if kind == "dir" and not stat_is_dir_no_follow(mode):
        fail(f"path must be a directory during {action}: {path}")
    if kind == "file" and not stat_is_file_no_follow(mode):
        fail(f"path must be a regular file during {action}: {path}")


def stat_is_dir_no_follow(mode: int) -> bool:
    return (mode & 0o170000) == 0o040000


def stat_is_file_no_follow(mode: int) -> bool:
    return (mode & 0o170000) == 0o100000


def stat_is_symlink_no_follow(mode: int) -> bool:
    return (mode & 0o170000) == 0o120000


def _fsync_directory_fd(directory_fd: int, *, action: str) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        fail(f"failed to synchronize directory during {action}: {exc}")


def cmd_mkdirs(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "directory path")
    fd = _open_dir_chain(path, action=args.action, create=True)
    if fd is not None:
        _close_fds(fd, action=args.action)


def cmd_replace(args: argparse.Namespace) -> None:
    src = _validate_absolute(args.src, "source path")
    dst = _validate_absolute(args.dst, "destination path")
    src_fd, src_name = _open_parent(src, action=args.action)
    dst_fd, dst_name = _open_parent(dst, action=args.action)
    if src_fd is None or dst_fd is None:
        if src_fd is not None:
            _close_fds(src_fd, action=args.action)
        if dst_fd is not None:
            _close_fds(dst_fd, action=args.action)
        fail(f"failed to open parent directory during {args.action}")
    try:
        _check_leaf(src_fd, src_name, src, action=args.action, kind=args.src_kind, must_exist=True)
        src_stat = _lstat_at(src_fd, src_name)
        if src_stat is None:
            fail(f"source file missing during {args.action}: {src}")
        src_signature = _source_file_signature(src_stat) if args.src_kind == "file" else None
        if args.src_kind == "file" and src_stat.st_nlink != 1:
            fail(f"source file must not be hardlinked during {args.action}: {src}")
        existing = _lstat_at(dst_fd, dst_name)
        if existing is not None:
            if args.dst_must_not_exist:
                fail(f"destination already exists during {args.action}: {dst}")
            if stat_is_symlink_no_follow(existing.st_mode):
                fail(f"refusing to follow symlink during {args.action}: {dst}")
        _check_leaf(src_fd, src_name, src, action=args.action, kind=args.src_kind, must_exist=True)
        source_before_replace = _lstat_at(src_fd, src_name)
        if src_signature is not None and (
            source_before_replace is None or _source_file_signature(source_before_replace) != src_signature
        ):
            fail(f"source changed during {args.action}: {src}")
        if src_signature is None and not _same_identity(src_stat, source_before_replace):
            fail(f"source changed during {args.action}: {src}")
        expected_src_identity = getattr(args, "expected_src_identity", None)
        if expected_src_identity is not None:
            _assert_expected_identity(
                src_fd,
                src_name,
                expected_src_identity,
                action=args.action,
                path=src,
                role="source",
            )
        expected_dst_identity = getattr(args, "expected_dst_identity", None)
        if expected_dst_identity is None:
            _assert_target_unchanged(dst_fd, dst_name, existing, action=args.action)
        else:
            _assert_expected_identity(
                dst_fd,
                dst_name,
                expected_dst_identity,
                action=args.action,
                path=dst,
            )
        no_clobber = args.dst_must_not_exist or expected_dst_identity == "missing"
        if no_clobber:
            _rename_without_replacing(
                src_name,
                dst_name,
                directory_fd=src_fd,
                target_directory_fd=dst_fd,
                expected_source_stat=src_stat,
                action=args.action,
            )
        else:
            os.replace(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        final_stat = _lstat_at(dst_fd, dst_name)
        if src_signature is not None and (final_stat is None or _source_file_signature(final_stat) != src_signature):
            fail(f"destination changed during {args.action}: {dst}")
        if src_signature is None and not _same_identity(src_stat, final_stat):
            fail(f"destination changed during {args.action}: {dst}")
        _check_leaf(dst_fd, dst_name, dst, action=args.action, kind=args.src_kind, must_exist=True)
        _fsync_directory_fd(dst_fd, action=args.action)
        _fsync_directory_fd(src_fd, action=args.action)
    finally:
        _close_fds(src_fd, dst_fd, action=args.action, primary_error=sys.exc_info()[1])


def cmd_exchange(args: argparse.Namespace) -> None:
    source = _validate_absolute(args.source, "source path")
    target = _validate_absolute(args.target, "target path")
    source_fd, source_name = _open_parent(source, action=args.action)
    target_fd, target_name = _open_parent(target, action=args.action)
    if source_fd is None or target_fd is None:
        if source_fd is not None:
            _close_fds(source_fd, action=args.action)
        if target_fd is not None:
            _close_fds(target_fd, action=args.action)
        fail(f"failed to open exchange parent during {args.action}")
    try:
        _check_leaf(source_fd, source_name, source, action=args.action, kind=args.kind, must_exist=True)
        _check_leaf(target_fd, target_name, target, action=args.action, kind=args.kind, must_exist=True)
        source_stat = _lstat_at(source_fd, source_name)
        target_stat = _lstat_at(target_fd, target_name)
        if source_stat is None or target_stat is None:
            fail(f"exchange path disappeared during {args.action}")
        if args.kind == "file" and (source_stat.st_nlink != 1 or target_stat.st_nlink != 1):
            fail(f"exchange path must not be hardlinked during {args.action}")
        _assert_expected_identity(
            source_fd,
            source_name,
            args.expected_source_identity,
            action=args.action,
            path=source,
            role="source",
        )
        _assert_expected_identity(
            target_fd,
            target_name,
            args.expected_target_identity,
            action=args.action,
            path=target,
        )
        _rename_exchange(
            source_name,
            target_name,
            directory_fd=source_fd,
            target_directory_fd=target_fd,
            action=args.action,
        )
        exchanged_source = _lstat_at(source_fd, source_name)
        exchanged_target = _lstat_at(target_fd, target_name)
        if exchanged_source is None or exchanged_target is None:
            fail(f"exchange result disappeared during {args.action}")
        if _stat_identity(exchanged_source) != _parse_identity(args.expected_target_identity, action=args.action):
            fail(f"source exchange result changed during {args.action}: {source}")
        if _stat_identity(exchanged_target) != _parse_identity(args.expected_source_identity, action=args.action):
            fail(f"target exchange result changed during {args.action}: {target}")
        _check_leaf(source_fd, source_name, source, action=args.action, kind=args.kind, must_exist=True)
        _check_leaf(target_fd, target_name, target, action=args.action, kind=args.kind, must_exist=True)
        _fsync_directory_fd(source_fd, action=args.action)
        _fsync_directory_fd(target_fd, action=args.action)
    finally:
        _close_fds(source_fd, target_fd, action=args.action, primary_error=sys.exc_info()[1])


def _write_bytes_atomic_at(
    parent_fd: int,
    parent_path: Path,
    leaf: str,
    data: bytes,
    mode: int,
    *,
    action: str,
) -> None:
    if not leaf or "/" in leaf or leaf in {".", ".."}:
        fail(f"invalid atomic file name during {action}: {parent_path / leaf}")
    dst = parent_path / leaf
    tmp_name = f".{leaf}.{secrets.token_hex(8)}.tmp"
    fd: int | None = None
    tmp_stat: os.stat_result | None = None
    target_stat: os.stat_result | None = None
    try:
        existing = _lstat_at(parent_fd, leaf)
        if existing is not None and stat_is_symlink_no_follow(existing.st_mode):
            fail(f"refusing to follow symlink during {action}: {dst}")
        target_stat = existing
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        tmp_stat = os.fstat(fd)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
            tmp_stat = os.fstat(handle.fileno())
        staged_stat = _lstat_at(parent_fd, tmp_name)
        if staged_stat is None or not _same_identity(staged_stat, tmp_stat):
            raise OSError(f"temporary file changed during {action}: {dst}")
        _assert_target_unchanged(parent_fd, leaf, target_stat, action=action)
        os.replace(tmp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        if not _same_identity(tmp_stat, _lstat_at(parent_fd, leaf)):
            fail(f"destination changed during {action}: {dst}")
        _check_leaf(parent_fd, leaf, dst, action=action, kind="file", must_exist=True)
        _fsync_directory_fd(parent_fd, action=action)
    except BaseException:
        with context_suppress():
            if fd is not None:
                _close_fds(fd, action=action, primary_error=sys.exc_info()[1])
            _cleanup_temporary_file(parent_fd, tmp_name, tmp_stat, action=action)
        raise


def _write_bytes_atomic(dst: Path, data: bytes, mode: int, *, action: str) -> None:
    parent_fd, leaf = _open_parent(dst, action=action)
    if parent_fd is None:
        fail(f"failed to open parent directory during {action}: {dst}")
    try:
        _write_bytes_atomic_at(parent_fd, dst.parent, leaf, data, mode, action=action)
    finally:
        _close_fds(parent_fd, action=action, primary_error=sys.exc_info()[1])


def cmd_write_wrapper(args: argparse.Namespace) -> None:
    dst = _validate_absolute(args.dst, "wrapper path")
    python_path = _validate_absolute(args.python_path, "python package path")
    python_executable = _validate_absolute(args.python_executable, "python executable path")
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export PYTHONPATH={shlex.quote(str(python_path))}\n"
        f"exec {shlex.quote(str(python_executable))} -m speed_of_cinnamon.cli \"$@\"\n"
    )
    _write_bytes_atomic(dst, content.encode("utf-8"), 0o755, action=args.action)


def _require_private_directory(stat_result: os.stat_result, path: Path, *, action: str) -> None:
    if (
        stat_is_symlink_no_follow(stat_result.st_mode)
        or not stat_is_dir_no_follow(stat_result.st_mode)
        or stat_result.st_uid != os.geteuid()
        or stat_result.st_mode & 0o022
        or stat_result.st_nlink < 1
    ):
        raise OSError(f"unsafe private directory during {action}: {path}")


def _open_private_directory(path: Path, *, action: str) -> int:
    _validate_private_dir_chain(path, action=action)
    directory_fd = _open_dir_chain(path, action=action)
    if directory_fd is None:
        fail(f"directory is unavailable during {action}: {path}")
    try:
        _require_private_directory(os.fstat(directory_fd), path, action=action)
    except BaseException:
        with context_suppress():
            _close_fds(directory_fd, action=action, primary_error=sys.exc_info()[1])
        raise
    return directory_fd


def _validate_install_phase(value: object, *, action: str) -> str:
    if type(value) is not str or value not in INSTALL_PHASES:
        fail(f"invalid install phase during {action}")
    return value


def _read_install_phase(directory_fd: int, workspace: Path, *, action: str) -> str:
    fd: int | None = None
    try:
        fd = os.open(
            INSTALL_PHASE_FILE,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        before = os.fstat(fd)
        if (
            not stat_is_file_no_follow(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o077
            or before.st_nlink != 1
        ):
            raise OSError(f"unsafe install phase file during {action}: {workspace}")
        data = os.read(fd, INSTALL_PHASE_MAX_BYTES + 1)
        if len(data) > INSTALL_PHASE_MAX_BYTES:
            raise OSError(f"install phase exceeds size limit during {action}: {workspace}")
        try:
            phase = data.decode("ascii")
        except UnicodeDecodeError as exc:
            raise OSError(f"install phase is not ASCII during {action}: {workspace}") from exc
        if phase not in {f"{item}\n" for item in INSTALL_PHASES}:
            raise OSError(f"invalid install phase during {action}: {workspace}")
        after = os.fstat(fd)
        if _source_file_signature(before) != _source_file_signature(after):
            raise OSError(f"install phase changed during {action}: {workspace}")
        return phase[:-1]
    finally:
        if fd is not None:
            _close_fds(fd, action=action, primary_error=sys.exc_info()[1])


class _StaleCleanupBudget:
    def __init__(self) -> None:
        self.deadline_ns = time.monotonic_ns() + STALE_CLEANUP_BUDGET_NS
        self.entries = 0

    def check(self) -> None:
        if time.monotonic_ns() >= self.deadline_ns:
            raise OSError("stale install cleanup deadline exceeded")

    def enter_directory(self, depth: int) -> None:
        self.check()
        if depth > STALE_CLEANUP_MAX_DEPTH:
            raise OSError(f"stale install cleanup depth exceeds max {STALE_CLEANUP_MAX_DEPTH}")

    def consume_entry(self) -> None:
        self.check()
        if self.entries >= STALE_CLEANUP_MAX_ENTRIES:
            raise OSError(f"stale install cleanup entries exceed max {STALE_CLEANUP_MAX_ENTRIES}")
        self.entries += 1


def _remove_tree_bounded_fd_body(
    root_fd: int,
    parent_fd: int,
    root_name: str,
    root_path: Path,
    expected_root_stat: os.stat_result,
    *,
    action: str,
    budget: _StaleCleanupBudget,
    required_phase: str | None = None,
    residue_name: list[str | None] | None = None,
) -> None:
    budget.check()
    root_stat = os.fstat(root_fd)
    budget.check()
    _require_private_directory(root_stat, root_path, action=action)
    if not _same_identity(expected_root_stat, root_stat):
        raise OSError(f"claimed workspace identity changed during {action}")
    if required_phase is not None:
        budget.check()
        phase = _read_install_phase(root_fd, root_path, action=action)
        budget.check()
        if phase != required_phase:
            raise OSError(f"phase changed after cleanup claim during {action}")

    def remove_directory(directory_fd: int, directory_path: Path, depth: int) -> None:
        budget.enter_directory(depth)
        budget.check()
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                budget.consume_entry()
                name = entry.name
                if not name or name in {".", ".."} or "/" in name:
                    raise OSError(f"invalid stale cleanup entry during {action}: {directory_path / name}")
                child_path = directory_path / name
                budget.check()
                child_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                budget.check()
                if stat_is_dir_no_follow(child_stat.st_mode):
                    _require_private_directory(child_stat, child_path, action=action)
                    child_fd: int | None = None
                    try:
                        budget.check()
                        child_fd = os.open(
                            name,
                            os.O_RDONLY
                            | os.O_DIRECTORY
                            | os.O_NOFOLLOW
                            | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=directory_fd,
                        )
                        budget.check()
                        opened_stat = os.fstat(child_fd)
                        budget.check()
                        if not _same_identity(child_stat, opened_stat):
                            raise OSError(f"stale cleanup directory changed during {action}: {child_path}")
                        remove_directory(child_fd, child_path, depth + 1)
                        budget.check()
                        current_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                        budget.check()
                        if not _same_identity(child_stat, current_stat):
                            raise OSError(f"stale cleanup directory changed during {action}: {child_path}")
                        delete_name = f".stale-delete-{secrets.token_hex(16)}"
                        budget.check()
                        _rename_without_replacing(
                            name,
                            delete_name,
                            directory_fd=directory_fd,
                            expected_source_stat=child_stat,
                            action=action,
                        )
                        budget.check()
                        delete_stat = os.stat(delete_name, dir_fd=directory_fd, follow_symlinks=False)
                        budget.check()
                        if not _same_identity(child_stat, delete_stat):
                            raise OSError(f"stale cleanup claim changed during {action}: {child_path}")
                        budget.check()
                        os.rmdir(delete_name, dir_fd=directory_fd)
                        budget.check()
                    finally:
                        if child_fd is not None:
                            _close_fds(child_fd, action=action, primary_error=sys.exc_info()[1])
                elif stat_is_file_no_follow(child_stat.st_mode) or stat_is_symlink_no_follow(child_stat.st_mode):
                    if (
                        child_stat.st_uid != os.geteuid()
                        or (
                            stat_is_file_no_follow(child_stat.st_mode)
                            and child_stat.st_mode & 0o022
                        )
                    ):
                        raise OSError(f"unsafe stale cleanup file during {action}: {child_path}")
                    delete_name = f".stale-delete-{secrets.token_hex(16)}"
                    budget.check()
                    _rename_without_replacing(
                        name,
                        delete_name,
                        directory_fd=directory_fd,
                        expected_source_stat=child_stat,
                        action=action,
                    )
                    budget.check()
                    delete_stat = os.stat(delete_name, dir_fd=directory_fd, follow_symlinks=False)
                    budget.check()
                    if not _same_identity(child_stat, delete_stat):
                        raise OSError(f"stale cleanup claim changed during {action}: {child_path}")
                    budget.check()
                    os.unlink(delete_name, dir_fd=directory_fd)
                    budget.check()
                else:
                    raise OSError(f"unsafe stale cleanup node during {action}: {child_path}")
        budget.check()

    remove_directory(root_fd, root_path, 0)
    budget.check()
    root_stat = os.fstat(root_fd)
    budget.check()
    if not _same_identity(expected_root_stat, root_stat):
        raise OSError(f"claimed workspace identity changed during {action}")
    current_stat = os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False)
    budget.check()
    if not _same_identity(expected_root_stat, current_stat):
        raise OSError(f"claimed workspace identity changed during {action}")
    final_name = f"{root_name}.final-{secrets.token_hex(16)}"
    _rename_without_replacing(
        root_name,
        final_name,
        directory_fd=parent_fd,
        expected_source_stat=expected_root_stat,
        action=action,
    )
    if residue_name is not None:
        residue_name[0] = final_name
    final_path = root_path.with_name(final_name)
    try:
        budget.check()
        final_stat = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
        budget.check()
    except OSError as exc:
        raise OSError(f"stale cleanup root remains at {final_path}: {exc}") from exc
    if not _same_identity(expected_root_stat, final_stat):
        raise OSError(f"stale cleanup root changed at {final_path} during {action}")
    try:
        budget.check()
    except OSError as exc:
        raise OSError(f"stale cleanup root remains at {final_path}: {exc}") from exc
    try:
        os.rmdir(final_name, dir_fd=parent_fd)
    except OSError as exc:
        raise OSError(f"stale cleanup root remains at {final_path}: {exc}") from exc
    budget.check()
    _fsync_directory_fd(parent_fd, action=action)


def _remove_tree_bounded_fd(
    root_fd: int,
    parent_fd: int,
    root_name: str,
    root_path: Path,
    expected_root_stat: os.stat_result,
    *,
    action: str,
    budget: _StaleCleanupBudget,
    required_phase: str | None = None,
    residue_name: list[str | None] | None = None,
) -> None:
    actual_residue_name = residue_name if residue_name is not None else [None]
    try:
        _remove_tree_bounded_fd_body(
            root_fd,
            parent_fd,
            root_name,
            root_path,
            expected_root_stat,
            action=action,
            budget=budget,
            required_phase=required_phase,
            residue_name=actual_residue_name,
        )
    except BaseException as exc:
        residue_name_value = actual_residue_name[0] or root_name
        residue_path = _find_identity_residue(
            parent_fd,
            ((residue_name_value, root_path.with_name(residue_name_value)),),
            expected_root_stat,
        )
        if residue_path is not None:
            if isinstance(exc, OSError):
                raise OSError(f"stale cleanup residue remains at {residue_path}: {exc}") from exc
            exc.add_note(f"stale cleanup residue remains at {residue_path}")
            raise
        if isinstance(exc, OSError):
            raise OSError(f"stale cleanup residue path not confirmed for {root_path}: {exc}") from exc
        exc.add_note(f"stale cleanup residue path not confirmed for {root_path}")
        raise


def _remove_named_directory(
    parent_fd: int,
    name: str,
    path: Path,
    expected_stat: os.stat_result,
    *,
    action: str,
) -> None:
    budget = _StaleCleanupBudget()
    budget.check()
    current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    budget.check()
    if not _same_identity(expected_stat, current_stat):
        raise OSError(f"directory identity changed during {action}: {path}")
    root_fd: int | None = None
    try:
        budget.check()
        root_fd = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        budget.check()
        opened_stat = os.fstat(root_fd)
        budget.check()
        _require_private_directory(opened_stat, path, action=action)
        if not _same_identity(expected_stat, opened_stat):
            raise OSError(f"directory identity changed during {action}: {path}")
        _remove_tree_bounded_fd(
            root_fd,
            parent_fd,
            name,
            path,
            expected_stat,
            action=action,
            budget=budget,
        )
    finally:
        if root_fd is not None:
            _close_fds(root_fd, action=action, primary_error=sys.exc_info()[1])


def _remove_named_file(
    parent_fd: int,
    name: str,
    path: Path,
    expected_stat: os.stat_result,
    *,
    action: str,
    allow_symlink: bool = False,
) -> None:
    budget = _StaleCleanupBudget()
    _require_private_identity_removal_parent(parent_fd, action=action, path=path)
    budget.check()
    current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    budget.check()
    if not _same_identity(expected_stat, current_stat):
        raise OSError(f"file identity changed during {action}: {path}")
    is_symlink = stat_is_symlink_no_follow(current_stat.st_mode)
    if (
        (is_symlink and not allow_symlink)
        or (not is_symlink and not stat_is_file_no_follow(current_stat.st_mode))
        or current_stat.st_uid != os.geteuid()
        or (not is_symlink and current_stat.st_mode & 0o022)
    ):
        raise OSError(f"unsafe file during {action}: {path}")
    delete_name = f"{name}.safe-delete-{secrets.token_hex(16)}"
    delete_path = path.with_name(delete_name)
    budget.check()
    _rename_without_replacing(
        name,
        delete_name,
        directory_fd=parent_fd,
        expected_source_stat=expected_stat,
        action=action,
    )
    budget.check()
    delete_stat = os.stat(delete_name, dir_fd=parent_fd, follow_symlinks=False)
    budget.check()
    if not _same_identity(expected_stat, delete_stat):
        raise OSError(f"file claim changed during {action}: {delete_path}")
    try:
        budget.check()
        os.unlink(delete_name, dir_fd=parent_fd)
    except OSError as exc:
        residue_path = _find_identity_residue(
            parent_fd,
            ((delete_name, delete_path),),
            expected_stat,
        )
        if residue_path is not None:
            raise OSError(f"file claim remains at {residue_path}: {exc}") from exc
        raise OSError(f"file cleanup residue path not confirmed for {path}: {exc}") from exc
    budget.check()
    _fsync_directory_fd(parent_fd, action=action)


def _remove_named_empty_directory(
    parent_fd: int,
    name: str,
    path: Path,
    expected_stat: os.stat_result,
    *,
    action: str,
    ignore_non_empty: bool,
) -> None:
    budget = _StaleCleanupBudget()
    _require_private_identity_removal_parent(parent_fd, action=action, path=path)
    budget.check()
    current_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    budget.check()
    if not _same_identity(expected_stat, current_stat):
        raise OSError(f"directory identity changed during {action}: {path}")
    _require_private_directory(current_stat, path, action=action)
    claim_name = f"{name}.safe-rmdir-{secrets.token_hex(16)}"
    claim_path = path.with_name(claim_name)
    removed = False
    budget.check()
    _rename_without_replacing(
        name,
        claim_name,
        directory_fd=parent_fd,
        expected_source_stat=expected_stat,
        action=action,
    )
    try:
        budget.check()
        claimed_stat = os.stat(claim_name, dir_fd=parent_fd, follow_symlinks=False)
        budget.check()
        if not _same_identity(expected_stat, claimed_stat):
            raise OSError(f"directory claim changed during {action}: {claim_path}")
        try:
            budget.check()
            os.rmdir(claim_name, dir_fd=parent_fd)
            removed = True
        except OSError as exc:
            if not ignore_non_empty or exc.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                raise OSError(f"directory claim remains at {claim_path}: {exc}") from exc
            budget.check()
            _rename_without_replacing(
                claim_name,
                name,
                directory_fd=parent_fd,
                expected_source_stat=expected_stat,
                action=f"{action} restore",
            )
            budget.check()
            _fsync_directory_fd(parent_fd, action=action)
            return
        budget.check()
        _fsync_directory_fd(parent_fd, action=action)
    except BaseException as exc:
        if removed:
            if isinstance(exc, OSError):
                raise OSError(
                    f"directory claim removed; cleanup residue path not confirmed for {path}: {exc}"
                ) from exc
            exc.add_note(f"directory claim removed; cleanup residue path not confirmed for {path}")
            raise
        residue_path = _find_identity_residue(
            parent_fd,
            ((claim_name, claim_path), (name, path)),
            expected_stat,
        )
        if residue_path is not None:
            if isinstance(exc, OSError):
                raise OSError(f"directory claim remains at {residue_path}: {exc}") from exc
            exc.add_note(f"directory claim remains at {residue_path}")
            raise
        if isinstance(exc, OSError):
            raise OSError(f"directory cleanup residue path not confirmed for {path}: {exc}") from exc
        exc.add_note(f"directory cleanup residue path not confirmed for {path}")
        raise


def cmd_phase_set(args: argparse.Namespace) -> None:
    workspace = _validate_absolute(args.workspace, "install workspace")
    phase = _validate_install_phase(args.phase, action=args.action)
    workspace_fd = _open_private_directory(workspace, action=args.action)
    try:
        _write_bytes_atomic_at(
            workspace_fd,
            workspace,
            INSTALL_PHASE_FILE,
            f"{phase}\n".encode("ascii"),
            0o600,
            action=args.action,
        )
    finally:
        _close_fds(workspace_fd, action=args.action, primary_error=sys.exc_info()[1])


def cmd_phase_read(args: argparse.Namespace) -> None:
    workspace = _validate_absolute(args.workspace, "install workspace")
    workspace_fd = _open_private_directory(workspace, action=args.action)
    try:
        print(_read_install_phase(workspace_fd, workspace, action=args.action))
    except OSError as exc:
        fail(str(exc))
    finally:
        _close_fds(workspace_fd, action=args.action, primary_error=sys.exc_info()[1])


def cmd_cleanup_install_stages(args: argparse.Namespace) -> None:
    app_data = _validate_absolute(args.app_data, "install app data")
    app_data_fd = _open_private_directory(app_data, action=args.action)
    budget = _StaleCleanupBudget()
    unresolved_count = 0
    first_unresolved: Path | None = None

    def unresolved(path: Path, reason: str) -> None:
        nonlocal unresolved_count, first_unresolved
        unresolved_count += 1
        if first_unresolved is None:
            first_unresolved = path
        print(f"unresolved install recovery workspace: {path}: {reason}", file=sys.stderr)

    try:
        scanned_entries = 0
        names: list[str] = []
        with os.scandir(app_data_fd) as entries:
            for entry in entries:
                budget.check()
                scanned_entries += 1
                if scanned_entries > MAX_STALE_INSTALL_WORKSPACES:
                    fail(
                        f"install recovery workspace enumeration exceeds max "
                        f"{MAX_STALE_INSTALL_WORKSPACES}: {app_data}"
                )
                names.append(entry.name)
        budget.check()

        for name in names:
            if not name.startswith("install-stage-"):
                continue
            workspace = app_data / name
            candidate_fd: int | None = None
            try:
                candidate_stat = os.stat(name, dir_fd=app_data_fd, follow_symlinks=False)
                _require_private_directory(candidate_stat, workspace, action=args.action)
                candidate_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=app_data_fd,
                )
                opened_stat = os.fstat(candidate_fd)
                if not _same_identity(candidate_stat, opened_stat):
                    raise OSError(f"workspace identity changed during {args.action}")
                phase = _read_install_phase(candidate_fd, workspace, action=args.action)
            except OSError as exc:
                unresolved(workspace, str(exc))
                continue
            finally:
                if candidate_fd is not None:
                    _close_fds(candidate_fd, action=args.action, primary_error=sys.exc_info()[1])

            if phase != "pre-activation":
                unresolved(workspace, f"phase {phase} requires manual recovery")
                continue

            claim_name = f"{name}.cleanup-{secrets.token_hex(8)}"
            claim_path = app_data / claim_name
            claimed = False
            claimed_fd: int | None = None
            residue_name: list[str | None] = [None]
            try:
                _rename_without_replacing(
                    name,
                    claim_name,
                    directory_fd=app_data_fd,
                    expected_source_stat=candidate_stat,
                    action=args.action,
                )
                claimed = True
                _fsync_directory_fd(app_data_fd, action=args.action)
                claimed_fd = os.open(
                    claim_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=app_data_fd,
                )
                claimed_stat = os.fstat(claimed_fd)
                _require_private_directory(claimed_stat, claim_path, action=args.action)
                if not _same_identity(candidate_stat, claimed_stat):
                    raise OSError(f"claimed workspace identity changed during {args.action}")
                claimed_phase = _read_install_phase(claimed_fd, claim_path, action=args.action)
                if claimed_phase != "pre-activation":
                    raise OSError(f"phase changed after cleanup claim during {args.action}")
                current_stat = os.stat(claim_name, dir_fd=app_data_fd, follow_symlinks=False)
                if not _same_identity(candidate_stat, current_stat):
                    raise OSError(f"claimed workspace identity changed during {args.action}")
                _remove_tree_bounded_fd(
                    claimed_fd,
                    app_data_fd,
                    claim_name,
                    claim_path,
                    candidate_stat,
                    action=args.action,
                    budget=budget,
                    required_phase="pre-activation",
                    residue_name=residue_name,
                )
            except OSError as exc:
                unresolved_path = claim_path
                if residue_name[0] is not None:
                    unresolved_path = claim_path.with_name(residue_name[0])
                unresolved(unresolved_path if claimed else workspace, str(exc))
            finally:
                if claimed_fd is not None:
                    _close_fds(claimed_fd, action=args.action, primary_error=sys.exc_info()[1])
        if unresolved_count:
            fail(
                f"unresolved install recovery workspace count {unresolved_count}; "
                f"first path: {first_unresolved}"
            )
    finally:
        _close_fds(app_data_fd, action=args.action, primary_error=sys.exc_info()[1])


def _hash_open_file(
    handle: object,
    *,
    max_bytes: int | None = None,
    io_budget: _CopyBudget | None = None,
) -> str:
    hasher = hashlib.sha256()
    total_bytes = 0
    while True:
        if max_bytes is None:
            per_file_remaining = None
        else:
            if max_bytes < 0:
                raise OSError("copy size limit must not be negative")
            per_file_remaining = max_bytes - total_bytes
        read_size = (
            io_budget.read_size(per_file_remaining, copy=False)
            if io_budget is not None
            else min(COPY_CHUNK_SIZE, max(1, per_file_remaining + 1))
            if per_file_remaining is not None
            else COPY_CHUNK_SIZE
        )
        chunk = handle.read(read_size)
        if not chunk:
            break
        total_bytes += len(chunk)
        if max_bytes is not None and total_bytes > max_bytes:
            raise OSError("source exceeds copy size limit")
        if io_budget is not None:
            io_budget.consume_read(len(chunk), copy=False)
        hasher.update(chunk)
    return hasher.hexdigest()


def _copy_file_atomically_from_checked_source(
    src: Path,
    dst: Path,
    *,
    source_parent_fd: int,
    source_name: str,
    source_handle: object,
    source_before: os.stat_result,
    source_digest: str,
    mode: int,
    action: str,
    dst_must_not_exist: bool,
    max_bytes: int | None,
    destination_parent_fd: int | None = None,
    destination_name: str | None = None,
    copy_budget: _CopyBudget | None = None,
) -> None:
    if (destination_parent_fd is None) != (destination_name is None):
        fail(f"destination descriptor arguments are incomplete during {action}: {dst}")
    if destination_parent_fd is None:
        parent_fd, leaf = _open_parent(dst, action=action)
        if parent_fd is None:
            fail(f"failed to open parent directory during {action}: {dst}")
    else:
        parent_fd = os.dup(destination_parent_fd)
        leaf = destination_name
    tmp_name = f".{leaf}.{secrets.token_hex(8)}.tmp"
    fd: int | None = None
    tmp_stat: os.stat_result | None = None
    target_stat: os.stat_result | None = None
    try:
        existing = _lstat_at(parent_fd, leaf)
        if existing is not None and dst_must_not_exist:
            fail(f"destination already exists during {action}: {dst}")
        if existing is not None and stat_is_symlink_no_follow(existing.st_mode):
            fail(f"refusing to follow symlink during {action}: {dst}")
        target_stat = existing
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
        tmp_stat = os.fstat(fd)
        copied_hasher = hashlib.sha256()
        copied_bytes = 0
        with os.fdopen(fd, "wb", closefd=True) as output:
            fd = None
            while True:
                if copy_budget is None:
                    if max_bytes is None:
                        read_size = COPY_CHUNK_SIZE
                    else:
                        read_size = min(COPY_CHUNK_SIZE, max_bytes - copied_bytes + 1)
                else:
                    per_file_remaining = None if max_bytes is None else max_bytes - copied_bytes
                    read_size = copy_budget.read_size(per_file_remaining, copy=True)
                chunk = source_handle.read(read_size)
                if not chunk:
                    break
                copied_bytes += len(chunk)
                if max_bytes is not None and copied_bytes > max_bytes:
                    raise OSError(f"source exceeds copy size limit during {action}: {src}")
                if copy_budget is not None:
                    copy_budget.consume_read(len(chunk), copy=True)
                    copy_budget.consume_write(len(chunk))
                copied_hasher.update(chunk)
                output.write(chunk)
            output.flush()
            os.fchmod(output.fileno(), mode)
            os.fsync(output.fileno())
            tmp_stat = os.fstat(output.fileno())
        copied_digest = copied_hasher.hexdigest()
        source_after_fd = os.fstat(source_handle.fileno())
        source_after = _lstat_at(source_parent_fd, source_name)
        if source_after is None:
            fail(f"source file missing during {action}: {src}")
        if _source_file_signature(source_before) != _source_file_signature(source_after_fd):
            fail(f"source changed during {action}: {src}")
        if _source_file_signature(source_before) != _source_file_signature(source_after):
            fail(f"source changed during {action}: {src}")
        if copied_digest != source_digest:
            fail(f"source changed during {action}: {src}")
        staged_stat = _lstat_at(parent_fd, tmp_name)
        if staged_stat is None or not _same_identity(staged_stat, tmp_stat):
            raise OSError(f"temporary file changed during {action}: {dst}")
        _assert_target_unchanged(parent_fd, leaf, target_stat, action=action)
        if dst_must_not_exist:
            _rename_without_replacing(tmp_name, leaf, directory_fd=parent_fd, action=action)
        else:
            os.replace(tmp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        if not _same_identity(tmp_stat, _lstat_at(parent_fd, leaf)):
            fail(f"destination changed during {action}: {dst}")
        _check_leaf(parent_fd, leaf, dst, action=action, kind="file", must_exist=True)
        _fsync_directory_fd(parent_fd, action=action)
    except BaseException:
        with context_suppress():
            if fd is not None:
                _close_fds(fd, action=action, primary_error=sys.exc_info()[1])
            _cleanup_temporary_file(parent_fd, tmp_name, tmp_stat, action=action)
        raise
    finally:
        _close_fds(parent_fd, action=action, primary_error=sys.exc_info()[1])


def cmd_copy_file(args: argparse.Namespace) -> None:
    src = _validate_absolute(args.src, "source file")
    dst = _validate_absolute(args.dst, "destination file")
    src_fd, src_name = _open_parent(src, action=args.action)
    if src_fd is None:
        fail(f"failed to open parent directory during {args.action}: {src}")
    max_bytes = getattr(args, "max_bytes", None)
    if max_bytes is None:
        max_bytes = MAX_TREE_FILE_BYTES
    if max_bytes < 0:
        fail(f"copy size limit must not be negative during {args.action}: {src}")
    try:
        _check_leaf(src_fd, src_name, src, action=args.action, kind="file", must_exist=True)
        source_checked = _lstat_at(src_fd, src_name)
        if source_checked is None:
            fail(f"source file missing during {args.action}: {src}")
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(
            os.open(src_name, os.O_RDONLY | os.O_NOFOLLOW | nonblock_flag, dir_fd=src_fd),
            "rb",
        ) as handle:
            source_before = os.fstat(handle.fileno())
            if _source_file_signature(source_checked) != _source_file_signature(source_before):
                fail(f"source changed during {args.action}: {src}")
            if source_before.st_nlink != 1:
                fail(f"source file must not be hardlinked during {args.action}: {src}")
            source_digest = _hash_open_file(handle, max_bytes=max_bytes)
            handle.seek(0)
            _copy_file_atomically_from_checked_source(
                src,
                dst,
                source_parent_fd=src_fd,
                source_name=src_name,
                source_handle=handle,
                source_before=source_before,
                source_digest=source_digest,
                mode=int(args.mode, 8),
                action=args.action,
                dst_must_not_exist=args.dst_must_not_exist,
                max_bytes=max_bytes,
            )
    finally:
        _close_fds(src_fd, action=args.action, primary_error=sys.exc_info()[1])


def _hash_file(
    path: Path,
    *,
    max_bytes: int | None = None,
    io_budget: _CopyBudget | None = None,
) -> str:
    hasher = hashlib.sha256()
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | nonblock_flag)
    with os.fdopen(fd, "rb", closefd=True) as handle:
        total_bytes = 0
        while True:
            if max_bytes is None:
                per_file_remaining = None
            else:
                if max_bytes < 0:
                    raise OSError("hash size limit must not be negative")
                per_file_remaining = max_bytes - total_bytes
            read_size = (
                io_budget.read_size(per_file_remaining, copy=False)
                if io_budget is not None
                else min(COPY_CHUNK_SIZE, max(1, per_file_remaining + 1))
                if per_file_remaining is not None
                else COPY_CHUNK_SIZE
            )
            chunk = handle.read(read_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if max_bytes is not None and total_bytes > max_bytes:
                raise OSError("source exceeds hash size limit")
            if io_budget is not None:
                io_budget.consume_read(len(chunk), copy=False)
            hasher.update(chunk)
    return hasher.hexdigest()


def cmd_assert_file(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "file path")
    parent_fd, leaf = _open_parent(path, action=args.action)
    if parent_fd is None:
        fail(f"failed to open parent directory during {args.action}: {path}")
    try:
        _check_leaf(parent_fd, leaf, path, action=args.action, kind="file", must_exist=True)
        stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            fail(f"path is missing during {args.action}: {path}")
        if stat_result.st_nlink != 1:
            fail(f"refusing to use hardlinked {args.label} during {args.action}: {path}")
    finally:
        _close_fds(parent_fd, action=args.action, primary_error=sys.exc_info()[1])


def cmd_assert_private_chain(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "directory path")
    if args.allow_missing:
        _validate_nearest_private_dir_chain(path, action=args.action)
    else:
        _validate_private_dir_chain(path, action=args.action)


def cmd_identity(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "path")
    parent_fd, leaf = _open_parent(path, action=args.action)
    if parent_fd is None:
        fail(f"failed to open parent directory during {args.action}: {path}")
    try:
        _check_leaf(parent_fd, leaf, path, action=args.action, kind=args.kind, must_exist=True)
        stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            fail(f"path is missing during {args.action}: {path}")
        _validate_private_dir_chain(
            path if stat_is_dir_no_follow(stat_result.st_mode) else path.parent,
            action=args.action,
        )
        if args.kind == "file" and stat_result.st_nlink != 1:
            fail(f"refusing to use hardlinked file during {args.action}: {path}")
        print(_identity_text(stat_result))
    finally:
        _close_fds(parent_fd, action=args.action, primary_error=sys.exc_info()[1])


def _reject_unsafe_tree(
    tree: Path,
    label: str,
    *,
    reject_symlink_ancestors: bool = False,
    exclude_names: frozenset[str] = frozenset(),
    io_budget: _CopyBudget | None = None,
) -> tuple[int, int, int]:
    if reject_symlink_ancestors:
        _reject_symlink_ancestors(tree, label)
    try:
        root_stat = tree.lstat()
    except OSError as exc:
        fail(f"failed to inspect {label}: {tree}: {exc}")
    if stat_is_symlink_no_follow(root_stat.st_mode) or not stat_is_dir_no_follow(root_stat.st_mode):
        fail(f"refusing to install unsafe {label}: {tree}")
    root_identity = _stat_identity(root_stat)
    for root, dirs, files in _bounded_tree_walk(tree, label, exclude_names=exclude_names, io_budget=io_budget):
        root_path = Path(root)
        for name in [*dirs, *files]:
            path = root_path / name
            try:
                stat_result = path.lstat()
            except OSError as exc:
                fail(f"failed to inspect {label}: {path}: {exc}")
            if stat_is_symlink_no_follow(stat_result.st_mode):
                fail(f"refusing to install unsafe {label}: {path}")
            if not stat_is_dir_no_follow(stat_result.st_mode) and not stat_is_file_no_follow(stat_result.st_mode):
                fail(f"refusing to install unsupported file type in {label}: {path}")
            if stat_is_file_no_follow(stat_result.st_mode) and stat_result.st_nlink != 1:
                fail(f"refusing to install hardlinked {label}: {path}")
    try:
        final_root_stat = tree.lstat()
    except OSError as exc:
        fail(f"failed to inspect {label} after traversal: {tree}: {exc}")
    if _stat_identity(final_root_stat) != root_identity:
        fail(f"{label} root changed during traversal: {tree}")
    return root_identity


def _bounded_tree_walk(
    tree: Path,
    label: str,
    *,
    exclude_names: frozenset[str],
    io_budget: _CopyBudget | None = None,
):
    pending = [tree]
    inspected_entries = 0
    inspected_file_bytes = 0
    while pending:
        root = pending.pop()
        dirs: list[str] = []
        files: list[str] = []
        try:
            with os.scandir(root) as entries:
                for directory_entry in entries:
                    name = directory_entry.name
                    if name in exclude_names:
                        continue
                    inspected_entries += 1
                    if inspected_entries > MAX_TREE_ENTRIES:
                        fail(f"{label} contains too many entries (max {MAX_TREE_ENTRIES})")
                    if io_budget is not None:
                        io_budget.consume_entry(copy=False)
                    path = Path(directory_entry.path)
                    try:
                        stat_result = path.lstat()
                    except OSError as exc:
                        fail(f"failed to inspect {label}: {path}: {exc}")
                    if stat_is_symlink_no_follow(stat_result.st_mode):
                        fail(f"refusing to install unsafe {label}: {path}")
                    if stat_is_dir_no_follow(stat_result.st_mode):
                        dirs.append(name)
                        pending.append(path)
                        continue
                    if stat_is_file_no_follow(stat_result.st_mode):
                        inspected_file_bytes += stat_result.st_size
                        if inspected_file_bytes > MAX_TREE_FILE_BYTES:
                            fail(
                                f"{label} contains too many file bytes "
                                f"(max {MAX_TREE_FILE_BYTES})"
                            )
                        files.append(name)
                        continue
                    fail(f"refusing unsupported file type in {label}: {path}")
        except OSError as exc:
            fail(f"failed to inspect {label}: {root}: {exc}")
        yield root, dirs, files


def _reject_symlink_ancestors(path: Path, label: str) -> None:
    for ancestor in reversed(path.parents):
        if ancestor == Path("/"):
            continue
        try:
            stat_result = ancestor.lstat()
        except OSError as exc:
            fail(f"failed to inspect {label} ancestor: {ancestor}: {exc}")
        if stat_is_symlink_no_follow(stat_result.st_mode):
            fail(f"refusing to install {label} through symlinked ancestor: {ancestor}")


def _tree_signature(
    tree: Path,
    *,
    include_identity: bool = True,
    include_nlink: bool = False,
    reject_symlink_ancestors: bool = False,
    expected_root_identity: tuple[int, int, int] | None = None,
    exclude_names: frozenset[str] = frozenset(),
    io_budget: _CopyBudget | None = None,
) -> dict[str, tuple[object, ...]]:
    if reject_symlink_ancestors:
        _reject_symlink_ancestors(tree, "source tree")
    signature: dict[str, tuple[object, ...]] = {}
    root_stat = tree.lstat()
    if expected_root_identity is not None and _stat_identity(root_stat) != expected_root_identity:
        fail(f"source tree root changed during signature: {tree}")
    if include_identity:
        signature["."] = (
            root_stat.st_dev,
            root_stat.st_ino,
            root_stat.st_mode,
            root_stat.st_size,
            root_stat.st_nlink,
            "",
        )
    else:
        if include_nlink:
            signature["."] = (0, 0, root_stat.st_mode, 0, 0, "")
        else:
            signature["."] = (0, 0, root_stat.st_mode, 0, "")
    hashed_file_bytes = 0
    for root, dirs, files in _bounded_tree_walk(
        tree,
        "source tree",
        exclude_names=exclude_names,
        io_budget=io_budget,
    ):
        root_path = Path(root)
        for name in [*dirs, *files]:
            path = root_path / name
            try:
                stat_result = path.lstat()
            except OSError as exc:
                fail(f"failed to inspect source tree during signature: {path}: {exc}")
            rel_path = str(path.relative_to(tree))
            digest = ""
            if stat_is_file_no_follow(stat_result.st_mode):
                try:
                    remaining_bytes = MAX_TREE_FILE_BYTES - hashed_file_bytes
                    if remaining_bytes < 0:
                        fail(f"source tree exceeds size limit during signature: {tree}")
                    digest = _hash_file(path, max_bytes=remaining_bytes, io_budget=io_budget)
                    final_stat = path.lstat()
                except OSError as exc:
                    fail(f"failed to hash source tree during signature: {path}: {exc}")
                if _source_file_signature(stat_result) != _source_file_signature(final_stat):
                    fail(f"source file changed during signature: {path}")
                hashed_file_bytes += final_stat.st_size
                if hashed_file_bytes > MAX_TREE_FILE_BYTES:
                    fail(f"source tree exceeds size limit during signature: {tree}")
            elif not stat_is_dir_no_follow(stat_result.st_mode):
                fail(f"refusing unsupported file type in source tree during signature: {path}")
            if include_identity:
                signature[rel_path] = (
                    stat_result.st_dev,
                    stat_result.st_ino,
                    stat_result.st_mode,
                    stat_result.st_size,
                    stat_result.st_nlink,
                    digest,
                )
            else:
                size = stat_result.st_size if stat_is_file_no_follow(stat_result.st_mode) else 0
                if include_nlink:
                    link_count = stat_result.st_nlink if stat_is_file_no_follow(stat_result.st_mode) else 0
                    signature[rel_path] = (0, 0, stat_result.st_mode, size, link_count, digest)
                else:
                    signature[rel_path] = (0, 0, stat_result.st_mode, size, digest)
    if expected_root_identity is not None:
        try:
            final_root_stat = tree.lstat()
        except OSError as exc:
            fail(f"failed to inspect source tree after signature: {tree}: {exc}")
        if _stat_identity(final_root_stat) != expected_root_identity:
            fail(f"source tree root changed during signature: {tree}")
    return signature


def _path_signature(
    path: Path,
    *,
    exclude_names: frozenset[str] = frozenset(),
    io_budget: _CopyBudget | None = None,
) -> tuple[str, dict[str, tuple[object, ...]]]:
    _reject_symlink_ancestors(path, "source tree")
    try:
        root_stat = path.lstat()
    except OSError as exc:
        fail(f"failed to inspect source tree: {path}: {exc}")
    if stat_is_symlink_no_follow(root_stat.st_mode):
        fail(f"refusing to follow symlink during source tree verification: {path}")
    if stat_is_file_no_follow(root_stat.st_mode):
        if root_stat.st_size > MAX_TREE_FILE_BYTES:
            fail(f"source file exceeds size limit during source tree verification: {path}")
        if root_stat.st_nlink != 1:
            fail(f"refusing hardlinked source file during source tree verification: {path}")
        try:
            digest = _hash_file(path, max_bytes=MAX_TREE_FILE_BYTES, io_budget=io_budget)
            final_stat = path.lstat()
        except OSError as exc:
            fail(f"failed to hash source tree: {path}: {exc}")
        if _source_file_signature(root_stat) != _source_file_signature(final_stat):
            fail(f"source file changed during source tree verification: {path}")
        return "file", {
            ".": (0, 0, root_stat.st_mode, root_stat.st_size, root_stat.st_nlink, digest),
        }
    if not stat_is_dir_no_follow(root_stat.st_mode):
        fail(f"refusing unsupported source tree type: {path}")
    root_identity = _reject_unsafe_tree(
        path,
        "source tree",
        reject_symlink_ancestors=True,
        exclude_names=exclude_names,
        io_budget=io_budget,
    )
    return "dir", _tree_signature(
        path,
        include_identity=False,
        reject_symlink_ancestors=True,
        expected_root_identity=root_identity,
        exclude_names=exclude_names,
        include_nlink=True,
        io_budget=io_budget,
    )


def _manifest_bytes(kind: str, signature: dict[str, tuple[object, ...]], exclude_names: frozenset[str]) -> bytes:
    payload = {
        "exclude_names": sorted(exclude_names),
        "kind": kind,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "signature": {key: list(value) for key, value in sorted(signature.items())},
    }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _read_regular_file(path: Path, *, action: str) -> bytes:
    parent_fd, leaf = _open_parent(path, action=action)
    if parent_fd is None:
        fail(f"failed to open manifest parent during {action}: {path}")
    fd: int | None = None
    try:
        _check_leaf(parent_fd, leaf, path, action=action, kind="file", must_exist=True)
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
        before = os.fstat(fd)
        if not stat_is_file_no_follow(before.st_mode) or before.st_nlink != 1:
            fail(f"manifest is not a private regular file during {action}: {path}")
        with os.fdopen(fd, "rb", closefd=True) as handle:
            fd = None
            data = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            fail(f"manifest exceeds size limit during {action}: {path}")
        after = _lstat_at(parent_fd, leaf)
        if after is None or _source_file_signature(before) != _source_file_signature(after):
            fail(f"manifest changed during {action}: {path}")
        return data
    finally:
        if fd is not None:
            _close_fds(fd, parent_fd, action=action, primary_error=sys.exc_info()[1])
        else:
            _close_fds(parent_fd, action=action, primary_error=sys.exc_info()[1])


def _reject_manifest_constant(_value: str) -> object:
    raise ValueError("manifest contains nonfinite number")


def _reject_manifest_float(_value: str) -> object:
    raise ValueError("manifest contains floating-point number")


def _manifest_pairs(pairs: list[tuple[object, object]]) -> dict[object, object]:
    result: dict[object, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifest contains duplicate key")
        result[key] = value
    return result


def _validate_manifest_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise ValueError("manifest contains invalid relative path")
    if value == ".":
        return value
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise ValueError("manifest contains invalid relative path")
    return value


def _validate_manifest(raw: bytes, *, action: str) -> tuple[str, frozenset[str], dict[str, tuple[object, ...]]]:
    try:
        decoded = raw.decode("utf-8")
        payload = json.loads(
            decoded,
            object_pairs_hook=_manifest_pairs,
            parse_constant=_reject_manifest_constant,
            parse_float=_reject_manifest_float,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        fail(f"invalid manifest during {action}: {exc}")
    if type(payload) is not dict or set(payload) != {"exclude_names", "kind", "schema_version", "signature"}:
        fail(f"invalid manifest schema during {action}")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != MANIFEST_SCHEMA_VERSION:
        fail(f"invalid manifest version during {action}")
    kind = payload["kind"]
    if type(kind) is not str or kind not in {"file", "dir"}:
        fail(f"invalid manifest kind during {action}")
    exclude_names_value = payload["exclude_names"]
    if type(exclude_names_value) is not list:
        fail(f"invalid manifest exclusions during {action}")
    exclude_names = _normalize_exclude_names(exclude_names_value)
    if list(exclude_names_value) != sorted(exclude_names):
        fail(f"manifest exclusions are not canonical during {action}")
    signature_value = payload["signature"]
    if type(signature_value) is not dict or "." not in signature_value:
        fail(f"invalid manifest signature during {action}")
    signature: dict[str, tuple[object, ...]] = {}
    for key, value in signature_value.items():
        rel_path = _validate_manifest_path(key)
        if type(value) is not list or len(value) != 6:
            fail(f"invalid manifest entry during {action}")
        if any(type(item) is not int for item in value[:5]) or type(value[5]) is not str:
            fail(f"invalid manifest entry types during {action}")
        if value[0] != 0 or value[1] != 0 or value[2] < 0 or value[3] < 0:
            fail(f"invalid manifest entry values during {action}")
        if stat_is_file_no_follow(value[2]) and value[4] < 1:
            fail(f"invalid manifest file link count during {action}")
        if stat_is_dir_no_follow(value[2]) and value[4] != 0:
            fail(f"invalid manifest directory link count during {action}")
        digest = value[5]
        if digest and (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
            fail(f"invalid manifest digest during {action}")
        if rel_path in signature:
            fail(f"manifest contains duplicate path during {action}")
        signature[rel_path] = tuple(value)
    canonical = _manifest_bytes(kind, signature, exclude_names)
    if raw != canonical:
        fail(f"manifest is not canonical during {action}")
    return kind, exclude_names, signature


def cmd_snapshot_tree(args: argparse.Namespace) -> None:
    source = _validate_absolute(args.source, "source tree")
    manifest = _validate_absolute(args.manifest, "manifest path")
    exclude_names = _normalize_exclude_names(getattr(args, "exclude_name", ()))
    kind, signature = _path_signature(source, exclude_names=exclude_names)
    data = _manifest_bytes(kind, signature, exclude_names)
    if len(data) > MAX_MANIFEST_BYTES:
        fail(f"manifest exceeds size limit during {args.action}: {manifest}")
    _write_bytes_atomic(manifest, data, 0o600, action=args.action)
    print(hashlib.sha256(data).hexdigest())


def cmd_verify_tree(args: argparse.Namespace) -> None:
    manifest = _validate_absolute(args.manifest, "manifest path")
    target = _validate_absolute(args.target, "target tree")
    expected_digest = args.expected_digest
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or any(char not in "0123456789abcdef" for char in expected_digest)
    ):
        fail(f"invalid manifest digest during {args.action}")
    data = _read_regular_file(manifest, action=args.action)
    if hashlib.sha256(data).hexdigest() != expected_digest:
        fail(f"manifest digest changed during {args.action}: {manifest}")
    kind, exclude_names, expected_signature = _validate_manifest(data, action=args.action)
    actual_kind, actual_signature = _path_signature(target, exclude_names=exclude_names)
    final_kind, final_signature = _path_signature(target, exclude_names=exclude_names)
    if final_kind != actual_kind or final_signature != actual_signature:
        fail(f"target changed during final verification: {target}")
    if final_kind != kind or final_signature != expected_signature:
        fail(f"{args.label or 'target'} does not match staging expectation during {args.action}: {target}")


def _normalize_exclude_names(values: object) -> frozenset[str]:
    if values is None:
        return frozenset()
    if not isinstance(values, (list, tuple)):
        fail("tree exclusion names must be a list")
    result: set[str] = set()
    for value in values:
        if (
            not isinstance(value, str)
            or not value
            or value in {".", ".."}
            or any(char in value for char in "/\\\x00")
            or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
        ):
            fail("tree exclusion name must be a plain path component")
        result.add(value)
    return frozenset(result)


class _CopyBudget:
    def __init__(self, *, entry_limit: int, byte_limit: int, io_entry_limit: int, io_byte_limit: int) -> None:
        self.entry_limit = entry_limit
        self.byte_limit = byte_limit
        self.io_entry_limit = io_entry_limit
        self.io_byte_limit = io_byte_limit
        self.entries = 0
        self.bytes_read = 0
        self.bytes_written = 0
        self.io_entries = 0
        self.io_bytes = 0

    def consume_entry(self, *, copy: bool) -> None:
        if copy and self.entries >= self.entry_limit:
            raise OSError(f"tree copy entry limit exceeded (max {self.entry_limit})")
        if self.io_entries >= self.io_entry_limit:
            raise OSError(f"tree I/O entry budget exceeded (max {self.io_entry_limit})")
        self.io_entries += 1
        if copy:
            self.entries += 1

    def read_size(self, per_file_remaining: int | None, *, copy: bool) -> int:
        per_file_probe = COPY_CHUNK_SIZE if per_file_remaining is None else max(1, per_file_remaining + 1)
        io_remaining = self.io_byte_limit - self.io_bytes
        shared_probe = max(1, io_remaining // 2 + 1) if copy else max(1, io_remaining + 1)
        copy_remaining = self.byte_limit - self.bytes_read
        copy_probe = max(1, copy_remaining + 1) if copy else COPY_CHUNK_SIZE
        return min(COPY_CHUNK_SIZE, per_file_probe, shared_probe, copy_probe)

    def consume_read(self, amount: int, *, copy: bool) -> None:
        if self.io_bytes + amount > self.io_byte_limit:
            raise OSError(f"tree I/O byte budget exceeded (max {self.io_byte_limit})")
        if copy and self.bytes_read + amount > self.byte_limit:
            raise OSError(f"tree copy byte limit exceeded (max {self.byte_limit})")
        self.io_bytes += amount
        if copy:
            self.bytes_read += amount

    def consume_write(self, amount: int) -> None:
        if self.io_bytes + amount > self.io_byte_limit:
            raise OSError(f"tree I/O byte budget exceeded (max {self.io_byte_limit})")
        if self.bytes_written + amount > self.byte_limit:
            raise OSError(f"tree copy byte limit exceeded (max {self.byte_limit})")
        self.io_bytes += amount
        self.bytes_written += amount


def _copy_tree_fd(
    source: Path,
    staged_tree: Path,
    *,
    parent_fd: int,
    stage_name: str,
    leaf: str,
    exclude_names: frozenset[str],
    copy_budget: _CopyBudget,
) -> None:
    source_fd = _open_dir_chain(source, action="install-tree")
    stage_fd: int | None = None
    destination_fd: int | None = None

    def copy_directory(src_fd: int, dst_fd: int) -> None:
        try:
            with os.scandir(src_fd) as entries:
                for entry in entries:
                    name = entry.name
                    if name in exclude_names:
                        continue
                    copy_budget.consume_entry(copy=True)
                    stat_result = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
                    if stat_is_symlink_no_follow(stat_result.st_mode):
                        fail(f"refusing to install unsafe source tree entry during install-tree: {name}")
                    if stat_is_dir_no_follow(stat_result.st_mode):
                        os.mkdir(name, 0o700, dir_fd=dst_fd)
                        child_source_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=src_fd,
                        )
                        child_destination_fd = os.open(
                            name,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=dst_fd,
                        )
                        try:
                            copy_directory(child_source_fd, child_destination_fd)
                            os.fchmod(child_destination_fd, stat_result.st_mode & 0o7777)
                        finally:
                            _close_fds(
                                child_source_fd,
                                child_destination_fd,
                                action="install-tree",
                                primary_error=sys.exc_info()[1],
                            )
                        continue
                    if not stat_is_file_no_follow(stat_result.st_mode):
                        fail(f"refusing unsupported source tree entry during install-tree: {name}")
                    if stat_result.st_nlink != 1:
                        fail(f"source file must not be hardlinked during install-tree: {name}")
                    source_path = Path(f"/proc/self/fd/{src_fd}") / name
                    destination_path = Path(f"/proc/self/fd/{dst_fd}") / name
                    with os.fdopen(
                        os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_fd),
                        "rb",
                    ) as handle:
                        source_before = os.fstat(handle.fileno())
                        if _source_file_signature(stat_result) != _source_file_signature(source_before):
                            fail(f"source changed during install-tree: {source_path}")
                        source_digest = _hash_open_file(handle, max_bytes=MAX_TREE_FILE_BYTES, io_budget=copy_budget)
                        handle.seek(0)
                        _copy_file_atomically_from_checked_source(
                            source_path,
                            destination_path,
                            source_parent_fd=src_fd,
                            source_name=name,
                            source_handle=handle,
                            source_before=source_before,
                            source_digest=source_digest,
                            mode=stat_result.st_mode & 0o7777,
                            action="install-tree",
                            dst_must_not_exist=True,
                            max_bytes=MAX_TREE_FILE_BYTES,
                            destination_parent_fd=dst_fd,
                            destination_name=name,
                            copy_budget=copy_budget,
                        )
        except OSError:
            raise

    try:
        if source_fd is None:
            fail(f"failed to open source tree during install-tree: {source}")
        source_root_stat = os.fstat(source_fd)
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        os.mkdir(leaf, 0o700, dir_fd=stage_fd)
        destination_fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=stage_fd)
        copy_directory(source_fd, destination_fd)
        os.fchmod(destination_fd, source_root_stat.st_mode & 0o7777)
    finally:
        primary_error = sys.exc_info()[1]
        _close_fds(
            *(fd for fd in (source_fd, stage_fd, destination_fd) if fd is not None),
            action="install-tree",
            primary_error=primary_error,
        )


def cmd_install_tree(args: argparse.Namespace) -> None:
    source = _validate_absolute(args.source, "source tree")
    target = _validate_absolute(args.target, "target tree")
    label = str(args.label or "tree")
    exclude_names = _normalize_exclude_names(getattr(args, "exclude_name", ()))
    copy_budget = _CopyBudget(
        entry_limit=MAX_TREE_ENTRIES,
        byte_limit=MAX_TREE_FILE_BYTES,
        io_entry_limit=MAX_TREE_ENTRIES * TREE_IO_ENTRY_PASSES,
        io_byte_limit=MAX_TREE_FILE_BYTES * TREE_IO_PASSES,
    )
    source_root_identity = _reject_unsafe_tree(
        source,
        f"{label} source tree",
        reject_symlink_ancestors=True,
        exclude_names=exclude_names,
        io_budget=copy_budget,
    )
    source_signature = _tree_signature(
        source,
        include_identity=False,
        reject_symlink_ancestors=True,
        expected_root_identity=source_root_identity,
        exclude_names=exclude_names,
        io_budget=copy_budget,
    )
    parent_fd, leaf = _open_parent(target, action=args.action, create=True)
    if parent_fd is None:
        fail(f"failed to open parent directory during {args.action}: {target}")
    stage_name = f".{leaf}.{secrets.token_hex(8)}.install"
    stage_stat: os.stat_result | None = None
    parent_path = Path(f"/proc/self/fd/{parent_fd}")
    try:
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        stage_stat = _lstat_at(parent_fd, stage_name)
        if stage_stat is None:
            raise OSError(f"staging directory disappeared during {args.action}: {stage_name}")
        _require_private_directory(stage_stat, parent_path / stage_name, action=args.action)
        _fsync_directory_fd(parent_fd, action=args.action)
        staged_tree = parent_path / stage_name / leaf
        if _tree_signature(
            source,
            include_identity=False,
            reject_symlink_ancestors=True,
            expected_root_identity=source_root_identity,
            exclude_names=exclude_names,
            io_budget=copy_budget,
        ) != source_signature:
            fail(f"source tree changed during {args.action}: {source}")
        _copy_tree_fd(
            source,
            staged_tree,
            parent_fd=parent_fd,
            stage_name=stage_name,
            leaf=leaf,
            exclude_names=exclude_names,
            copy_budget=copy_budget,
        )
        if _tree_signature(
            source,
            include_identity=False,
            reject_symlink_ancestors=True,
            expected_root_identity=source_root_identity,
            exclude_names=exclude_names,
            io_budget=copy_budget,
        ) != source_signature:
            fail(f"source tree changed during {args.action}: {source}")
        if (
            _tree_signature(staged_tree, include_identity=False, exclude_names=exclude_names, io_budget=copy_budget)
            != source_signature
        ):
            fail(f"staged copy changed during {args.action}: {target}")
        _reject_unsafe_tree(staged_tree, label, exclude_names=exclude_names, io_budget=copy_budget)
        _check_leaf(parent_fd, stage_name, parent_path / stage_name, action=args.action, kind="dir", must_exist=True)
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            _check_leaf(stage_fd, leaf, staged_tree, action=args.action, kind="dir", must_exist=True)
            staged_leaf_stat = _lstat_at(stage_fd, leaf)
            if staged_leaf_stat is None:
                fail(f"staged destination disappeared during {args.action}: {staged_tree}")
            existing = _lstat_at(parent_fd, leaf)
            if existing is not None:
                fail(f"install-tree destination must not exist during {args.action}: {target}")
            _rename_without_replacing(
                leaf,
                leaf,
                directory_fd=stage_fd,
                target_directory_fd=parent_fd,
                expected_source_stat=staged_leaf_stat,
                action=args.action,
            )
            # Successful no-clobber rename is the only activation commit point.
            activated_stat = _lstat_at(parent_fd, leaf)
            if activated_stat is None or not _same_identity(staged_leaf_stat, activated_stat):
                raise OSError(f"install-tree commit identity unavailable: {target}")
            try:
                _check_leaf(parent_fd, leaf, target, action=args.action, kind="dir", must_exist=True)
            except BaseException as exc:
                raise OSError(f"install-tree commit completed but verification failed: {target}") from exc
            try:
                _fsync_directory_fd(stage_fd, action=args.action)
                _fsync_directory_fd(parent_fd, action=args.action)
            except BaseException as exc:
                raise OSError(f"install-tree commit completed but synchronization failed: {target}") from exc
        finally:
            _close_fds(stage_fd, action=args.action, primary_error=sys.exc_info()[1])
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_error: BaseException | None = None
        try:
            if stage_stat is not None:
                _remove_named_directory(
                    parent_fd,
                    stage_name,
                    parent_path / stage_name,
                    stage_stat,
                    action=args.action,
                )
        except BaseException as exc:
            cleanup_error = exc
            if primary_error is not None:
                primary_error.add_note(f"staging cleanup failed: {exc}")
        try:
            _close_fds(
                parent_fd,
                action=args.action,
                primary_error=primary_error or cleanup_error,
            )
        except BaseException as exc:
            if primary_error is not None:
                primary_error.add_note(f"parent descriptor cleanup failed: {exc}")
            elif cleanup_error is None:
                cleanup_error = exc
            else:
                cleanup_error.add_note(f"parent descriptor cleanup failed: {exc}")
        if primary_error is None and cleanup_error is not None:
            raise cleanup_error


def cmd_remove(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "remove path")
    expected_identity = getattr(args, "expected_identity", None)
    parent_fd, leaf = _open_parent(path, action=args.action, missing_ok=True)
    if parent_fd is None:
        _validate_nearest_private_dir_chain(path.parent, action=args.action)
        if expected_identity not in {None, "missing"}:
            raise OSError(f"destination changed during {args.action}: {path}")
        return
    try:
        stat_result = _lstat_at(parent_fd, leaf)
        if expected_identity is not None:
            _require_private_identity_removal_parent(parent_fd, action=args.action, path=path)
            _assert_expected_identity(
                parent_fd,
                leaf,
                expected_identity,
                action=args.action,
                path=path,
            )
            stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            _validate_private_dir_chain(path.parent, action=args.action)
            return
        _validate_private_dir_chain(
            path if stat_is_dir_no_follow(stat_result.st_mode) else path.parent,
            action=args.action,
        )
        if stat_is_symlink_no_follow(stat_result.st_mode):
            if args.kind != "file":
                fail(f"refusing to remove symlink as directory during {args.action}: {path}")
            _remove_named_file(parent_fd, leaf, path, stat_result, action=args.action, allow_symlink=True)
        elif args.kind == "dir":
            if not stat_is_dir_no_follow(stat_result.st_mode):
                fail(f"path must be a directory during {args.action}: {path}")
            _remove_named_directory(parent_fd, leaf, path, stat_result, action=args.action)
        elif args.kind == "file":
            if not stat_is_file_no_follow(stat_result.st_mode):
                fail(f"path must be a regular file during {args.action}: {path}")
            _remove_named_file(parent_fd, leaf, path, stat_result, action=args.action)
        else:
            fail(f"unsupported remove kind: {args.kind}")
    finally:
        _close_fds(parent_fd, action=args.action, primary_error=sys.exc_info()[1])


def cmd_remove_leaf(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "path")
    expected_identity = getattr(args, "expected_identity", None)
    parent_fd, leaf = _open_parent(path, action=args.action, missing_ok=True)
    if parent_fd is None:
        _validate_nearest_private_dir_chain(path.parent, action=args.action)
        if expected_identity not in {None, "missing"}:
            raise OSError(f"destination changed during {args.action}: {path}")
        return
    try:
        stat_result = _lstat_at(parent_fd, leaf)
        if expected_identity is not None:
            _require_private_identity_removal_parent(parent_fd, action=args.action, path=path)
            _assert_expected_identity(
                parent_fd,
                leaf,
                expected_identity,
                action=args.action,
                path=path,
            )
            stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            _validate_private_dir_chain(path.parent, action=args.action)
            return
        mode = stat_result.st_mode
        _validate_private_dir_chain(
            path if stat_is_dir_no_follow(mode) else path.parent,
            action=args.action,
        )
        if expected_identity is not None:
            _require_private_identity_removal_parent(parent_fd, action=args.action, path=path)
        if stat_is_dir_no_follow(mode):
            _remove_named_directory(parent_fd, leaf, path, stat_result, action=args.action)
        else:
            _remove_named_file(parent_fd, leaf, path, stat_result, action=args.action, allow_symlink=True)
    finally:
        _close_fds(parent_fd, action=args.action, primary_error=sys.exc_info()[1])


def cmd_rmdir(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "directory path")
    expected_identity = getattr(args, "expected_identity", None)
    parent_fd, leaf = _open_parent(path, action=args.action, missing_ok=True)
    if parent_fd is None:
        _validate_nearest_private_dir_chain(path.parent, action=args.action)
        if expected_identity not in {None, "missing"}:
            raise OSError(f"destination changed during {args.action}: {path}")
        return
    try:
        stat_result = _lstat_at(parent_fd, leaf)
        if expected_identity is not None:
            _require_private_identity_removal_parent(parent_fd, action=args.action, path=path)
            _assert_expected_identity(
                parent_fd,
                leaf,
                expected_identity,
                action=args.action,
                path=path,
            )
            stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            _validate_private_dir_chain(path.parent, action=args.action)
            return
        _validate_private_dir_chain(
            path if stat_is_dir_no_follow(stat_result.st_mode) else path.parent,
            action=args.action,
        )
        if stat_is_symlink_no_follow(stat_result.st_mode):
            fail(f"refusing to follow symlink during {args.action}: {path}")
        if not stat_is_dir_no_follow(stat_result.st_mode):
            fail(f"path must be a directory during {args.action}: {path}")
        _remove_named_empty_directory(
            parent_fd,
            leaf,
            path,
            stat_result,
            action=args.action,
            ignore_non_empty=args.ignore_non_empty,
        )
    finally:
        _close_fds(parent_fd, action=args.action, primary_error=sys.exc_info()[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    mkdirs = subparsers.add_parser("mkdirs")
    mkdirs.add_argument("action")
    mkdirs.add_argument("path")
    mkdirs.set_defaults(func=cmd_mkdirs)

    phase_set = subparsers.add_parser("phase-set")
    phase_set.add_argument("action")
    phase_set.add_argument("workspace")
    phase_set.add_argument("phase", choices=sorted(INSTALL_PHASES))
    phase_set.set_defaults(func=cmd_phase_set)

    phase_read = subparsers.add_parser("phase-read")
    phase_read.add_argument("action")
    phase_read.add_argument("workspace")
    phase_read.set_defaults(func=cmd_phase_read)

    cleanup_install_stages = subparsers.add_parser("cleanup-install-stages")
    cleanup_install_stages.add_argument("action")
    cleanup_install_stages.add_argument("app_data")
    cleanup_install_stages.set_defaults(func=cmd_cleanup_install_stages)

    replace = subparsers.add_parser("replace")
    replace.add_argument("action")
    replace.add_argument("src")
    replace.add_argument("dst")
    replace.add_argument("--src-kind", choices=("file", "dir"), required=True)
    replace.add_argument("--dst-must-not-exist", action="store_true")
    replace.add_argument("--expected-src-identity")
    replace.add_argument("--expected-dst-identity")
    replace.set_defaults(func=cmd_replace)

    exchange = subparsers.add_parser("exchange")
    exchange.add_argument("action")
    exchange.add_argument("source")
    exchange.add_argument("target")
    exchange.add_argument("--kind", choices=("file", "dir"), required=True)
    exchange.add_argument("--expected-source-identity", required=True)
    exchange.add_argument("--expected-target-identity", required=True)
    exchange.set_defaults(func=cmd_exchange)

    write_wrapper = subparsers.add_parser("write-wrapper")
    write_wrapper.add_argument("action")
    write_wrapper.add_argument("dst")
    write_wrapper.add_argument("python_path")
    write_wrapper.add_argument("python_executable")
    write_wrapper.set_defaults(func=cmd_write_wrapper)

    copy_file = subparsers.add_parser("copy-file")
    copy_file.add_argument("action")
    copy_file.add_argument("src")
    copy_file.add_argument("dst")
    copy_file.add_argument("mode")
    copy_file.add_argument("--dst-must-not-exist", action="store_true")
    copy_file.add_argument("--max-bytes", type=int)
    copy_file.set_defaults(func=cmd_copy_file)

    assert_file = subparsers.add_parser("assert-file")
    assert_file.add_argument("action")
    assert_file.add_argument("path")
    assert_file.add_argument("label")
    assert_file.set_defaults(func=cmd_assert_file)

    assert_private_chain = subparsers.add_parser("assert-private-chain")
    assert_private_chain.add_argument("action")
    assert_private_chain.add_argument("path")
    assert_private_chain.add_argument("--allow-missing", action="store_true")
    assert_private_chain.set_defaults(func=cmd_assert_private_chain)

    identity = subparsers.add_parser("identity")
    identity.add_argument("action")
    identity.add_argument("path")
    identity.add_argument("--kind", choices=("file", "dir"), required=True)
    identity.set_defaults(func=cmd_identity)

    install_tree = subparsers.add_parser("install-tree")
    install_tree.add_argument("action")
    install_tree.add_argument("source")
    install_tree.add_argument("target")
    install_tree.add_argument("label")
    install_tree.add_argument("--exclude-name", action="append", default=[])
    install_tree.set_defaults(func=cmd_install_tree)

    snapshot_tree = subparsers.add_parser("snapshot-tree")
    snapshot_tree.add_argument("action")
    snapshot_tree.add_argument("source")
    snapshot_tree.add_argument("manifest")
    snapshot_tree.add_argument("label")
    snapshot_tree.add_argument("--exclude-name", action="append", default=[])
    snapshot_tree.set_defaults(func=cmd_snapshot_tree)

    verify_tree = subparsers.add_parser("verify-tree")
    verify_tree.add_argument("action")
    verify_tree.add_argument("manifest")
    verify_tree.add_argument("expected_digest")
    verify_tree.add_argument("target")
    verify_tree.add_argument("label")
    verify_tree.set_defaults(func=cmd_verify_tree)

    remove = subparsers.add_parser("remove")
    remove.add_argument("action")
    remove.add_argument("path")
    remove.add_argument("--kind", choices=("file", "dir"), required=True)
    remove.add_argument("--expected-identity")
    remove.set_defaults(func=cmd_remove)

    remove_leaf = subparsers.add_parser("remove-leaf")
    remove_leaf.add_argument("action")
    remove_leaf.add_argument("path")
    remove_leaf.add_argument("--expected-identity")
    remove_leaf.set_defaults(func=cmd_remove_leaf)

    rmdir = subparsers.add_parser("rmdir")
    rmdir.add_argument("action")
    rmdir.add_argument("path")
    rmdir.add_argument("--ignore-non-empty", action="store_true")
    rmdir.add_argument("--expected-identity")
    rmdir.set_defaults(func=cmd_rmdir)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"{getattr(args, 'action', 'safe-local-fs')}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
