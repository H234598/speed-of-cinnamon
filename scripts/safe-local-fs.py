#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import os
import secrets
import shlex
import shutil
import sys
from pathlib import Path


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
                os.mkdir(part, 0o700, dir_fd=fd)
                next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    fail(f"refusing to follow symlink during {action}: {path}")
                fail(f"failed to open path during {action}: {path}: {exc}")
            os.close(fd)
            fd = next_fd
        return fd
    except Exception:
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
    assert src_fd is not None and dst_fd is not None
    try:
        _check_leaf(src_fd, src_name, src, action=args.action, kind=args.src_kind, must_exist=True)
        src_stat = _lstat_at(src_fd, src_name)
        existing = _lstat_at(dst_fd, dst_name)
        if existing is not None:
            if args.dst_must_not_exist:
                fail(f"destination already exists during {args.action}: {dst}")
            if stat_is_symlink_no_follow(existing.st_mode):
                fail(f"refusing to follow symlink during {args.action}: {dst}")
        _check_leaf(src_fd, src_name, src, action=args.action, kind=args.src_kind, must_exist=True)
        if not _same_identity(src_stat, _lstat_at(src_fd, src_name)):
            fail(f"source changed during {args.action}: {src}")
        os.replace(src_name, dst_name, src_dir_fd=src_fd, dst_dir_fd=dst_fd)
        final_stat = _lstat_at(dst_fd, dst_name)
        if not _same_identity(src_stat, final_stat):
            fail(f"destination changed during {args.action}: {dst}")
        _check_leaf(dst_fd, dst_name, dst, action=args.action, kind=args.src_kind, must_exist=True)
    finally:
        os.close(src_fd)
        os.close(dst_fd)


def _write_bytes_atomic(dst: Path, data: bytes, mode: int, *, action: str) -> None:
    parent_fd, leaf = _open_parent(dst, action=action)
    assert parent_fd is not None
    tmp_name = f".{leaf}.{secrets.token_hex(8)}.tmp"
    fd: int | None = None
    replaced = False
    tmp_stat: os.stat_result | None = None
    try:
        existing = _lstat_at(parent_fd, leaf)
        if existing is not None and stat_is_symlink_no_follow(existing.st_mode):
            fail(f"refusing to follow symlink during {action}: {dst}")
        fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent_fd)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            fd = None
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
        tmp_stat = _lstat_at(parent_fd, tmp_name)
        os.replace(tmp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        replaced = True
        if not _same_identity(tmp_stat, _lstat_at(parent_fd, leaf)):
            fail(f"destination changed during {action}: {dst}")
        _check_leaf(parent_fd, leaf, dst, action=action, kind="file", must_exist=True)
    except Exception:
        with context_suppress():
            if fd is not None:
                os.close(fd)
            os.unlink(tmp_name, dir_fd=parent_fd)
        if replaced and _same_identity(tmp_stat, _lstat_at(parent_fd, leaf)):
            with context_suppress():
                os.unlink(leaf, dir_fd=parent_fd)
        raise
    finally:
        os.close(parent_fd)


def cmd_write_wrapper(args: argparse.Namespace) -> None:
    dst = _validate_absolute(args.dst, "wrapper path")
    python_path = _validate_absolute(args.python_path, "python package path")
    content = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"export PYTHONPATH={shlex.quote(str(python_path))}\n"
        'exec "$(command -v -- python3)" -m speed_of_cinnamon.cli "$@"\n'
    )
    _write_bytes_atomic(dst, content.encode("utf-8"), 0o755, action=args.action)


def cmd_copy_file(args: argparse.Namespace) -> None:
    src = _validate_absolute(args.src, "source file")
    dst = _validate_absolute(args.dst, "destination file")
    src_fd, src_name = _open_parent(src, action=args.action)
    assert src_fd is not None
    try:
        _check_leaf(src_fd, src_name, src, action=args.action, kind="file", must_exist=True)
        with os.fdopen(os.open(src_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_fd), "rb") as handle:
            data = handle.read()
    finally:
        os.close(src_fd)
    _write_bytes_atomic(dst, data, int(args.mode, 8), action=args.action)


def cmd_assert_file(args: argparse.Namespace) -> None:
    path = _validate_absolute(args.path, "file path")
    parent_fd, leaf = _open_parent(path, action=args.action)
    assert parent_fd is not None
    try:
        _check_leaf(parent_fd, leaf, path, action=args.action, kind="file", must_exist=True)
        stat_result = _lstat_at(parent_fd, leaf)
        if stat_result is None:
            fail(f"path is missing during {args.action}: {path}")
        if stat_result.st_nlink != 1:
            fail(f"refusing to use hardlinked {args.label} during {args.action}: {path}")
    finally:
        os.close(parent_fd)


def _reject_unsafe_tree(tree: Path, label: str) -> None:
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
            if stat_is_file_no_follow(stat_result.st_mode) and stat_result.st_nlink != 1:
                fail(f"refusing to install hardlinked {label}: {path}")


def _tree_signature(tree: Path) -> dict[str, tuple[int, int, int, int]]:
    signature: dict[str, tuple[int, int, int, int]] = {}
    root_stat = tree.lstat()
    signature["."] = (root_stat.st_dev, root_stat.st_ino, root_stat.st_mode, root_stat.st_mtime_ns)
    for root, dirs, files in os.walk(tree):
        root_path = Path(root)
        for name in [*dirs, *files]:
            path = root_path / name
            stat_result = path.lstat()
            rel_path = str(path.relative_to(tree))
            signature[rel_path] = (
                stat_result.st_dev,
                stat_result.st_ino,
                stat_result.st_mode,
                stat_result.st_mtime_ns,
            )
    return signature


def cmd_install_tree(args: argparse.Namespace) -> None:
    source = _validate_absolute(args.source, "source tree")
    target = _validate_absolute(args.target, "target tree")
    label = str(args.label or "tree")
    _reject_unsafe_tree(source, f"{label} source tree")
    source_signature = _tree_signature(source)
    parent_fd, leaf = _open_parent(target, action=args.action, create=True)
    assert parent_fd is not None
    token = secrets.token_hex(8)
    stage_name = f".{leaf}.{token}.install"
    backup_name = f".{leaf}.{token}.backup"
    backup_created = False
    activated = False
    parent_path = Path(f"/proc/self/fd/{parent_fd}")
    try:
        os.mkdir(stage_name, 0o700, dir_fd=parent_fd)
        staged_tree = parent_path / stage_name / leaf
        if _tree_signature(source) != source_signature:
            fail(f"source tree changed during {args.action}: {source}")
        shutil.copytree(source, staged_tree)
        if _tree_signature(source) != source_signature:
            fail(f"source tree changed during {args.action}: {source}")
        _reject_unsafe_tree(staged_tree, label)
        _check_leaf(parent_fd, stage_name, parent_path / stage_name, action=args.action, kind="dir", must_exist=True)
        stage_fd = os.open(stage_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            _check_leaf(stage_fd, leaf, staged_tree, action=args.action, kind="dir", must_exist=True)
        finally:
            os.close(stage_fd)
        existing = _lstat_at(parent_fd, leaf)
        if existing is not None:
            if stat_is_symlink_no_follow(existing.st_mode):
                fail(f"refusing to follow symlink during {args.action}: {target}")
            if not stat_is_dir_no_follow(existing.st_mode):
                fail(f"path must be a directory during {args.action}: {target}")
            os.replace(leaf, backup_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            backup_created = True
        os.replace(f"{stage_name}/{leaf}", leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        activated = True
        _check_leaf(parent_fd, leaf, target, action=args.action, kind="dir", must_exist=True)
        if backup_created:
            shutil.rmtree(backup_name, dir_fd=parent_fd)
            backup_created = False
    except Exception:
        if backup_created and activated and _lstat_at(parent_fd, backup_name) is not None:
            with context_suppress():
                current = _lstat_at(parent_fd, leaf)
                if current is not None and stat_is_dir_no_follow(current.st_mode):
                    shutil.rmtree(leaf, dir_fd=parent_fd)
                os.replace(backup_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                backup_created = False
        elif backup_created and _lstat_at(parent_fd, backup_name) is not None and _lstat_at(parent_fd, leaf) is None:
            with context_suppress():
                os.replace(backup_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                backup_created = False
        raise
    finally:
        with context_suppress():
            shutil.rmtree(stage_name, dir_fd=parent_fd)
        if backup_created:
            with context_suppress():
                shutil.rmtree(backup_name, dir_fd=parent_fd)
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
            shutil.rmtree(leaf, dir_fd=parent_fd)
        elif args.kind == "file":
            if not stat_is_file_no_follow(stat_result.st_mode):
                fail(f"path must be a regular file during {args.action}: {path}")
            os.unlink(leaf, dir_fd=parent_fd)
        else:
            fail(f"unsupported remove kind: {args.kind}")
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
    replace.set_defaults(func=cmd_replace)

    write_wrapper = subparsers.add_parser("write-wrapper")
    write_wrapper.add_argument("action")
    write_wrapper.add_argument("dst")
    write_wrapper.add_argument("python_path")
    write_wrapper.set_defaults(func=cmd_write_wrapper)

    copy_file = subparsers.add_parser("copy-file")
    copy_file.add_argument("action")
    copy_file.add_argument("src")
    copy_file.add_argument("dst")
    copy_file.add_argument("mode")
    copy_file.set_defaults(func=cmd_copy_file)

    assert_file = subparsers.add_parser("assert-file")
    assert_file.add_argument("action")
    assert_file.add_argument("path")
    assert_file.add_argument("label")
    assert_file.set_defaults(func=cmd_assert_file)

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
