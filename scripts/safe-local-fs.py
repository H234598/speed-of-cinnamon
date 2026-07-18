#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import os
import secrets
import shlex
import shutil
import sys
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
                    os.close(fd)
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
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        with context_suppress():
            os.close(fd)
        raise


class context_suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> bool:
        return True


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
    action: str,
) -> None:
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


def _cleanup_temporary_file(
    parent_fd: int,
    temporary_name: str,
    expected_stat: os.stat_result | None,
    *,
    action: str,
) -> None:
    if expected_stat is None:
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
            )
        except FileExistsError:
            continue
        except FileNotFoundError:
            return
        try:
            claimed_stat = _lstat_at(parent_fd, cleanup_name)
            if claimed_stat is None:
                return
            if not _same_identity(claimed_stat, expected_stat):
                with context_suppress():
                    _rename_without_replacing(
                        cleanup_name,
                        temporary_name,
                        directory_fd=parent_fd,
                        action=f"{action} temporary restore",
                    )
                    os.fsync(parent_fd)
                return
            os.unlink(cleanup_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except BaseException:
            with context_suppress():
                _rename_without_replacing(
                    cleanup_name,
                    temporary_name,
                    directory_fd=parent_fd,
                    action=f"{action} temporary restore",
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
) -> None:
    current_stat = _lstat_at(parent_fd, name)
    if expected_identity == "missing":
        if current_stat is not None:
            raise OSError(f"destination changed during {action}: {path}")
        return
    if current_stat is None or _stat_identity(current_stat) != _parse_identity(expected_identity, action=action):
        raise OSError(f"destination changed during {action}: {path}")


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


def _rmtree_safe(path: str, *, dir_fd: int, action: str) -> None:
    if not getattr(shutil.rmtree, "avoids_symlink_attacks", False):
        fail(f"refusing unsafe recursive removal during {action}: shutil.rmtree is not fd-safe")
    shutil.rmtree(path, dir_fd=dir_fd)


def cmd_mkdirs(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "directory path")
    fd = _open_dir_chain(path, action=args.action, create=True)
    if fd is not None:
        os.close(fd)


def cmd_replace(args: argparse.Namespace) -> None:
    src = _validate_absolute(args.src, "source path")
    dst = _validate_absolute(args.dst, "destination path")
    src_fd, src_name = _open_parent(src, action=args.action)
    dst_fd, dst_name = _open_parent(dst, action=args.action)
    if src_fd is None or dst_fd is None:
        if src_fd is not None:
            os.close(src_fd)
        if dst_fd is not None:
            os.close(dst_fd)
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
        if args.dst_must_not_exist:
            _rename_without_replacing(
                src_name,
                dst_name,
                directory_fd=src_fd,
                target_directory_fd=dst_fd,
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
        os.close(src_fd)
        os.close(dst_fd)


def _write_bytes_atomic(dst: Path, data: bytes, mode: int, *, action: str) -> None:
    parent_fd, leaf = _open_parent(dst, action=action)
    if parent_fd is None:
        fail(f"failed to open parent directory during {action}: {dst}")
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
                os.close(fd)
            _cleanup_temporary_file(parent_fd, tmp_name, tmp_stat, action=action)
        raise
    finally:
        os.close(parent_fd)


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


def _hash_open_file(handle: object) -> str:
    hasher = hashlib.sha256()
    while True:
        chunk = handle.read(COPY_CHUNK_SIZE)
        if not chunk:
            break
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
) -> None:
    parent_fd, leaf = _open_parent(dst, action=action)
    if parent_fd is None:
        fail(f"failed to open parent directory during {action}: {dst}")
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
        with os.fdopen(fd, "wb", closefd=True) as output:
            fd = None
            while True:
                chunk = source_handle.read(COPY_CHUNK_SIZE)
                if not chunk:
                    break
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
                os.close(fd)
            _cleanup_temporary_file(parent_fd, tmp_name, tmp_stat, action=action)
        raise
    finally:
        os.close(parent_fd)


def cmd_copy_file(args: argparse.Namespace) -> None:
    src = _validate_absolute(args.src, "source file")
    dst = _validate_absolute(args.dst, "destination file")
    src_fd, src_name = _open_parent(src, action=args.action)
    if src_fd is None:
        fail(f"failed to open parent directory during {args.action}: {src}")
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
            source_digest = _hash_open_file(handle)
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
            )
    finally:
        os.close(src_fd)


def _hash_file(path: Path) -> str:
    hasher = hashlib.sha256()
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | nonblock_flag)
    with os.fdopen(fd, "rb", closefd=True) as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK_SIZE), b""):
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
        os.close(parent_fd)


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
        if args.kind == "file" and stat_result.st_nlink != 1:
            fail(f"refusing to use hardlinked file during {args.action}: {path}")
        print(_identity_text(stat_result))
    finally:
        os.close(parent_fd)


def _reject_unsafe_tree(tree: Path, label: str, *, reject_symlink_ancestors: bool = False) -> None:
    if reject_symlink_ancestors:
        _reject_symlink_ancestors(tree, label)
    if not tree.exists() or tree.is_symlink() or not tree.is_dir():
        fail(f"refusing to install unsafe {label}: {tree}")
    for root, dirs, files in os.walk(tree):
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


def _tree_signature(tree: Path, *, include_identity: bool = True, reject_symlink_ancestors: bool = False) -> dict[str, tuple[object, ...]]:
    if reject_symlink_ancestors:
        _reject_symlink_ancestors(tree, "source tree")
    signature: dict[str, tuple[object, ...]] = {}
    root_stat = tree.lstat()
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
        signature["."] = (0, 0, root_stat.st_mode, 0, "")
    for root, dirs, files in os.walk(tree):
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
                    digest = _hash_file(path)
                except OSError as exc:
                    fail(f"failed to hash source tree during signature: {path}: {exc}")
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
                signature[rel_path] = (0, 0, stat_result.st_mode, size, digest)
    return signature


def cmd_install_tree(args: argparse.Namespace) -> None:
    source = _validate_absolute(args.source, "source tree")
    target = _validate_absolute(args.target, "target tree")
    label = str(args.label or "tree")
    _reject_unsafe_tree(source, f"{label} source tree", reject_symlink_ancestors=True)
    source_signature = _tree_signature(source, include_identity=False, reject_symlink_ancestors=True)
    parent_fd, leaf = _open_parent(target, action=args.action, create=True)
    if parent_fd is None:
        fail(f"failed to open parent directory during {args.action}: {target}")
    token = secrets.token_hex(8)
    stage_name = f".{leaf}.{token}.install"
    backup_name = f".{leaf}.{token}.backup"
    backup_created = False
    activated = False
    activated_stat: os.stat_result | None = None
    parent_path = Path(f"/proc/self/fd/{parent_fd}")
    try:
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        _fsync_directory_fd(parent_fd, action=args.action)
        staged_tree = parent_path / stage_name / leaf
        if _tree_signature(source, include_identity=False, reject_symlink_ancestors=True) != source_signature:
            fail(f"source tree changed during {args.action}: {source}")
        shutil.copytree(source, staged_tree, symlinks=True)
        if _tree_signature(source, include_identity=False, reject_symlink_ancestors=True) != source_signature:
            fail(f"source tree changed during {args.action}: {source}")
        if _tree_signature(staged_tree, include_identity=False) != source_signature:
            fail(f"staged copy changed during {args.action}: {target}")
        _reject_unsafe_tree(staged_tree, label)
        _check_leaf(parent_fd, stage_name, parent_path / stage_name, action=args.action, kind="dir", must_exist=True)
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            _check_leaf(stage_fd, leaf, staged_tree, action=args.action, kind="dir", must_exist=True)
        finally:
            os.close(stage_fd)
        existing = _lstat_at(parent_fd, leaf)
        _assert_target_unchanged(parent_fd, leaf, existing, action=args.action)
        if existing is not None:
            if stat_is_symlink_no_follow(existing.st_mode):
                fail(f"refusing to follow symlink during {args.action}: {target}")
            if not stat_is_dir_no_follow(existing.st_mode):
                fail(f"path must be a directory during {args.action}: {target}")
            os.replace(leaf, backup_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            backup_created = True
            _fsync_directory_fd(parent_fd, action=args.action)
        os.replace(f"{stage_name}/{leaf}", leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        activated_stat = _lstat_at(parent_fd, leaf)
        activated = True
        _check_leaf(parent_fd, leaf, target, action=args.action, kind="dir", must_exist=True)
        _fsync_directory_fd(parent_fd, action=args.action)
        if backup_created:
            _rmtree_safe(backup_name, dir_fd=parent_fd, action=args.action)
            _fsync_directory_fd(parent_fd, action=args.action)
            backup_created = False
    except BaseException:
        if backup_created and activated and _lstat_at(parent_fd, backup_name) is not None:
            with context_suppress():
                current = _lstat_at(parent_fd, leaf)
                if (
                    current is not None
                    and activated_stat is not None
                    and _same_identity(current, activated_stat)
                    and stat_is_dir_no_follow(current.st_mode)
                ):
                    _rmtree_safe(leaf, dir_fd=parent_fd, action=args.action)
                    os.fsync(parent_fd)
                    os.replace(backup_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                    os.fsync(parent_fd)
                    backup_created = False
        elif activated and not backup_created and _lstat_at(parent_fd, leaf) is not None:
            with context_suppress():
                current = _lstat_at(parent_fd, leaf)
                if (
                    current is not None
                    and activated_stat is not None
                    and _same_identity(current, activated_stat)
                    and stat_is_dir_no_follow(current.st_mode)
                ):
                    _rmtree_safe(leaf, dir_fd=parent_fd, action=args.action)
                    os.fsync(parent_fd)
        elif backup_created and _lstat_at(parent_fd, backup_name) is not None and _lstat_at(parent_fd, leaf) is None:
            with context_suppress():
                os.replace(backup_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
                backup_created = False
        raise
    finally:
        with context_suppress():
            _rmtree_safe(stage_name, dir_fd=parent_fd, action=args.action)
            os.fsync(parent_fd)
        # A backup that is still marked as created is the only recovery copy
        # left after a failed rollback.  Keep it instead of deleting the
        # previous installation while handling the original error.
        os.close(parent_fd)


def cmd_remove(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "remove path")
    parent_fd, leaf = _open_parent(path, action=args.action, missing_ok=True)
    if parent_fd is None:
        return
    try:
        stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            return
        if stat_is_symlink_no_follow(stat_result.st_mode):
            fail(f"refusing to follow symlink during {args.action}: {path}")
        if args.kind == "dir":
            if not stat_is_dir_no_follow(stat_result.st_mode):
                fail(f"path must be a directory during {args.action}: {path}")
            _rmtree_safe(leaf, dir_fd=parent_fd, action=args.action)
            _fsync_directory_fd(parent_fd, action=args.action)
        elif args.kind == "file":
            if not stat_is_file_no_follow(stat_result.st_mode):
                fail(f"path must be a regular file during {args.action}: {path}")
            os.unlink(leaf, dir_fd=parent_fd)
            _fsync_directory_fd(parent_fd, action=args.action)
        else:
            fail(f"unsupported remove kind: {args.kind}")
    finally:
        os.close(parent_fd)


def cmd_remove_leaf(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "path")
    parent_fd, leaf = _open_parent(path, action=args.action, missing_ok=True)
    if parent_fd is None:
        return
    try:
        stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            return
        expected_identity = getattr(args, "expected_identity", None)
        if expected_identity is not None:
            _assert_expected_identity(
                parent_fd,
                leaf,
                expected_identity,
                action=args.action,
                path=path,
            )
        mode = stat_result.st_mode
        if stat_is_dir_no_follow(mode):
            _rmtree_safe(leaf, dir_fd=parent_fd, action=args.action)
        else:
            os.unlink(leaf, dir_fd=parent_fd)
        _fsync_directory_fd(parent_fd, action=args.action)
    finally:
        os.close(parent_fd)


def cmd_rmdir(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "directory path")
    parent_fd, leaf = _open_parent(path, action=args.action, missing_ok=True)
    if parent_fd is None:
        return
    try:
        stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            return
        if stat_is_symlink_no_follow(stat_result.st_mode):
            fail(f"refusing to follow symlink during {args.action}: {path}")
        if not stat_is_dir_no_follow(stat_result.st_mode):
            fail(f"path must be a directory during {args.action}: {path}")
        try:
            os.rmdir(leaf, dir_fd=parent_fd)
            _fsync_directory_fd(parent_fd, action=args.action)
        except OSError as exc:
            if args.ignore_non_empty and exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                return
            raise
    finally:
        os.close(parent_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    mkdirs = subparsers.add_parser("mkdirs")
    mkdirs.add_argument("action")
    mkdirs.add_argument("path")
    mkdirs.set_defaults(func=cmd_mkdirs)

    replace = subparsers.add_parser("replace")
    replace.add_argument("action")
    replace.add_argument("src")
    replace.add_argument("dst")
    replace.add_argument("--src-kind", choices=("file", "dir"), required=True)
    replace.add_argument("--dst-must-not-exist", action="store_true")
    replace.add_argument("--expected-dst-identity")
    replace.set_defaults(func=cmd_replace)

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
    copy_file.set_defaults(func=cmd_copy_file)

    assert_file = subparsers.add_parser("assert-file")
    assert_file.add_argument("action")
    assert_file.add_argument("path")
    assert_file.add_argument("label")
    assert_file.set_defaults(func=cmd_assert_file)

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
    install_tree.set_defaults(func=cmd_install_tree)

    remove = subparsers.add_parser("remove")
    remove.add_argument("action")
    remove.add_argument("path")
    remove.add_argument("--kind", choices=("file", "dir"), required=True)
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
    rmdir.set_defaults(func=cmd_rmdir)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
