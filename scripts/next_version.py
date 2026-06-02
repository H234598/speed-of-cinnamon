#!/usr/bin/env python3
"""Calculate the next version based on commit-counting rules.

Rules:
- PATCH increments by one per commit.
- MINOR increments by one every 100 PATCH steps.
- MAJOR increments by one every 100 MINOR steps.
- Additional MAJOR increment on breaking changes (independent).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11 compatibility fallback
    import tomli as tomllib  # type: ignore[import-not-found]


def parse_version(value: str) -> tuple[int, int, int]:
    parts = value.strip().lstrip("vV").split(".")
    if len(parts) != 3:
        raise ValueError(f"invalid version format: {value}")
    major_s, minor_s, patch_s = parts
    return int(major_s), int(minor_s), int(patch_s)


def to_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"


def commits_since_tag(tag: str) -> int:
    cmd = ["git", "rev-list", "--count", f"{tag}..HEAD"]
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return int(result.stdout.strip())


def add_patches(base: tuple[int, int, int], patch_steps: int) -> tuple[int, int, int]:
    major, minor, patch = base
    total_patch = patch + patch_steps
    if total_patch < 0:
        raise ValueError("negative patch steps are not supported")
    minor += total_patch // 100
    patch = total_patch % 100
    major += minor // 100
    minor = minor % 100
    return major, minor, patch


def apply_feature_increase(major: int, minor: int, patch: int) -> tuple[int, int, int]:
    minor += 1
    if minor >= 100:
        minor = 0
        major += 1
    return major, minor, patch


def apply_breaking_change(major: int, minor: int, patch: int) -> tuple[int, int, int]:
    return major + 1, 0, 0


def read_current_version(path: Path = Path("pyproject.toml")) -> tuple[int, int, int]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    value = data["project"]["version"]
    if not isinstance(value, str):
        raise ValueError("project.version is not a string")
    return parse_version(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print next project version")
    parser.add_argument("--base", default=None, help="Base version (default: pyproject.toml)")
    parser.add_argument(
        "--from-tag",
        default=None,
        help="Git tag in form v<major>.<minor>.<patch> to calculate patch steps from",
    )
    parser.add_argument(
        "--add-commits",
        type=int,
        default=None,
        help="Explicit number of patch steps; overrides --from-tag if set",
    )
    parser.add_argument("--feature", action="store_true", help="Advance MINOR for a compatible new feature")
    parser.add_argument("--breaking", action="store_true", help="Apply breaking-change bump")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = parse_version(args.base) if args.base else read_current_version()

    if args.add_commits is not None:
        commits = args.add_commits
    elif args.from_tag:
        commits = commits_since_tag(args.from_tag)
    else:
        commits = 1

    major, minor, patch = add_patches(base, commits)

    if args.feature:
        major, minor, patch = apply_feature_increase(major, minor, patch)
    if args.breaking:
        major, minor, patch = apply_breaking_change(major, minor, patch)

    print(to_version(major, minor, patch))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
