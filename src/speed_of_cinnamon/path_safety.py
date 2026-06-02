from __future__ import annotations

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
