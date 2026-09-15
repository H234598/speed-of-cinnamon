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
RPM_SPEC_PATHS = (
    ROOT / "packaging/speed-of-cinnamon.spec",
    ROOT / "packaging/speed-of-cinnamon-generic.spec",
)
MANPAGE_PATHS = (
    ROOT / "docs/man/speed-of-cinnamon.1",
    ROOT / "docs/man/speed-of-cinnamon-alarms.1",
)


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


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> object:
    raise ValueError(f"non-finite JSON value: {value}")


def _load_json(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_non_finite_json_number,
    )


def _project_version() -> str:
    data = tomllib.loads(_read_text(ROOT / "pyproject.toml"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or VERSION_RE.fullmatch(version) is None:
        raise RuntimeError("pyproject.toml contains invalid project version")
    return version


def validate() -> list[str]:
    version = _project_version()
    metadata = _load_json(_read_text(ROOT / "files/speed-of-cinnamon@H234598/metadata.json"))
    schema = _load_json(_read_text(ROOT / "files/speed-of-cinnamon@H234598/settings-schema.json"))
    errors: list[str] = []
    for spec_path in RPM_SPEC_PATHS:
        spec_version_match = re.search(r"^Version:\s*([^\s]+)\s*$", _read_text(spec_path), flags=re.MULTILINE)
        if spec_version_match is None or spec_version_match.group(1) != version:
            errors.append(f"{spec_path.name} version does not match pyproject.toml")
    if metadata.get("version") != version:
        errors.append("Cinnamon metadata version does not match pyproject.toml")
    if metadata.get("comments") != f"Version: {version}":
        errors.append("Cinnamon metadata comments version does not match pyproject.toml")
    about_description = schema.get("about-version", {}).get("description")
    if not isinstance(about_description, str) or not about_description.startswith(f"Version: {version}\n"):
        errors.append("settings schema about-version does not match pyproject.toml")
    for manpage_path in MANPAGE_PATHS:
        manpage = _read_text(manpage_path)
        manpage_header = manpage.splitlines()[0] if manpage.splitlines() else ""
        if f"speed-of-cinnamon {version}" not in manpage_header:
            errors.append(f"{manpage_path.name} version does not match pyproject.toml")
    return errors


def main() -> int:
    try:
        errors = validate()
    except (
        MemoryError,
        RecursionError,
        OSError,
        UnicodeError,
        RuntimeError,
        ValueError,
        TypeError,
        tomllib.TOMLDecodeError,
    ) as exc:
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
