from __future__ import annotations

import os
from pathlib import Path


def assert_no_symlink_ancestors(path: Path, *, field_name: str = "path") -> None:
    current = path
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    while True:
        if current.is_symlink():
            raise RuntimeError(f"{field_name} must not pass through a symlink: {current}")
        if current.parent == current:
            break
        current = current.parent


def read_text_without_following_symlinks(path: Path, *, field_name: str = "path", encoding: str = "utf-8") -> str:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    assert_no_symlink_ancestors(path, field_name=field_name)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise OSError(f"secure file open is not supported for {field_name}")
    try:
        fd = os.open(path, os.O_RDONLY | nofollow_flag)
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
