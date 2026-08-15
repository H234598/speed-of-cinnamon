#!/usr/bin/env python3
"""Verify that shipped version surfaces match pyproject.toml."""
from __future__ import annotations

import json
import os
import re
import stat
import sys
import tomllib
from pathlib import Path


MAX_VERSION_FILE_BYTES = 1 << 20
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
ROOT = Path(__file__).resolve().parents[1]


def _read_text(path: Path) -> str:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if not isinstance(no_follow, int) or isinstance(no_follow, bool) or no_follow <= 0:
        raise RuntimeError("secure no-follow support is unavailable")
    fd = os.open(path, os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0))
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > MAX_VERSION_FILE_BYTES
        ):
            raise RuntimeError(f"unsafe or oversized version file: {path}")
        raw = os.read(fd, MAX_VERSION_FILE_BYTES + 1)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    if len(raw) > MAX_VERSION_FILE_BYTES or (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise RuntimeError(f"version file changed while reading: {path}")
    return raw.decode("utf-8")


def _project_version() -> str:
    data = tomllib.loads(_read_text(ROOT / "pyproject.toml"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise RuntimeError("pyproject.toml contains invalid project version")
    return version


def validate() -> list[str]:
    version = _project_version()
    metadata = json.loads(_read_text(ROOT / "files/speed-of-cinnamon@H234598/metadata.json"))
    schema = json.loads(_read_text(ROOT / "files/speed-of-cinnamon@H234598/settings-schema.json"))
    manpage = _read_text(ROOT / "docs/man/speed-of-cinnamon.1")
    errors: list[str] = []
    if metadata.get("version") != version:
        errors.append("Cinnamon metadata version does not match pyproject.toml")
    if metadata.get("comments") != f"Version: {version}":
        errors.append("Cinnamon metadata comments version does not match pyproject.toml")
    about_description = schema.get("about-version", {}).get("description")
    if not isinstance(about_description, str) or not about_description.startswith(f"Version: {version}\n"):
        errors.append("settings schema about-version does not match pyproject.toml")
    manpage_header = manpage.splitlines()[0] if manpage.splitlines() else ""
    if f"speed-of-cinnamon {version}" not in manpage_header:
        errors.append("manpage version does not match pyproject.toml")
    return errors


def main() -> int:
    try:
        errors = validate()
    except (OSError, UnicodeError, RuntimeError, ValueError, TypeError, tomllib.TOMLDecodeError) as exc:
        print(f"version consistency check failed: {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"version consistency check failed: {error}", file=sys.stderr)
        return 1
    print("Version consistency OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
