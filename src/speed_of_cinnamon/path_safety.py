from __future__ import annotations

import os
from pathlib import Path


def _safe_path_parts(path: Path, *, field_name: str) -> tuple[str, ...]:
    parts = path.parts
    if path.is_absolute():
        parts = parts[1:]
    if not parts:
        raise OSError(f"{field_name} is invalid")
    if any(part in {"", ".", ".."} for part in parts):
        raise OSError(f"{field_name} contains an unsafe path component")
    return parts


def assert_no_symlink_ancestors(path: Path, *, field_name: str = "path") -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    current = path
    while True:
        if current.is_symlink():
            raise RuntimeError(f"{field_name} must not pass through a symlink: {current}")
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
    try:
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | directory_flag | nofollow_flag,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(parts[-1], flags | nofollow_flag, mode, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


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


def read_text_without_following_symlinks(path: Path, *, field_name: str = "path", encoding: str = "utf-8") -> str:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY, field_name=field_name)
    except OSError as exc:
        raise OSError(str(exc)) from exc
    try:
        with os.fdopen(fd, "r", encoding=encoding) as handle:
            return handle.read()
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
