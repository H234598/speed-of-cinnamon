#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import subprocess
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[import-not-found]

def parse_version(value: str) -> tuple[int, int, int]:
    parts = value.strip().lstrip("vV").split(".")
    if len(parts) != 3:
        raise ValueError(f"invalid version format: {value}")
    try:
        major, minor, patch = (int(x) for x in parts)
    except ValueError as exc:
        raise ValueError(f"invalid version format: {value}") from exc
    if major < 0 or minor < 0 or patch < 0:
        raise ValueError(f"invalid version format: {value}")
    return major, minor, patch


def normalize_tag(tag: str) -> str:
    return tag if tag.startswith("v") else f"v{tag}"

def to_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"

def commits_since_ref(ref: str) -> int:
    try:
        result = subprocess.run(["git", "rev-list", "--count", f"{ref}..HEAD"], check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise ValueError("git command not available") from exc
    except subprocess.CalledProcessError as exc:
        raise ValueError(f"failed to compute commits since {ref}: {exc.stderr.strip()}") from exc
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise ValueError(f"invalid git commit-count output: {result.stdout!r}") from exc

def commits_since_tag(tag: str) -> int:
    return commits_since_ref(normalize_tag(tag))

def add_patches(base: tuple[int,int,int], patch_steps: int) -> tuple[int,int,int]:
    major, minor, patch = base
    if patch_steps < 0:
        raise ValueError("negative patch steps are not supported")
    patch_steps = patch_steps // 100
    total_patch = patch + patch_steps
    minor += total_patch // 100
    patch = total_patch % 100
    major += minor // 100
    minor %= 100
    return major, minor, patch

def apply_feature_increase(major:int, minor:int, patch:int) -> tuple[int,int,int]:
    minor += 1
    if minor >= 100:
        minor = 0
        major += 1
    return major, minor, patch

def apply_breaking_change(major:int, minor:int, patch:int) -> tuple[int,int,int]:
    return major+1, 0, 0

def read_current_version(path: Path = Path("pyproject.toml")) -> tuple[int,int,int]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    val = data["project"]["version"]
    if not isinstance(val, str):
        raise ValueError("project.version is not a string")
    return parse_version(val)

def tag_for_version(version: tuple[int,int,int]) -> str:
    return f"v{to_version(*version)}"

def tag_exists(tag: str) -> bool:
    tag = normalize_tag(tag)
    rc = subprocess.run(["git", "tag", "-l", tag], text=True, capture_output=True)
    return rc.returncode == 0 and tag in (rc.stdout or "").splitlines()

def ensure_tag_exists(tag: str) -> None:
    if not tag_exists(tag):
        raise ValueError(f"release tag {tag} does not exist")

def parse_args() -> argparse.Namespace:
    def non_negative_int(value: str) -> int:
        number = int(value)
        if number < 0:
            raise argparse.ArgumentTypeError("must be >= 0")
        return number

    p = argparse.ArgumentParser()
    p.add_argument("--base", default=None)
    increments = p.add_mutually_exclusive_group()
    increments.add_argument("--from-tag", default=None, help="Version tag to derive commit count from")
    increments.add_argument("--add-commits", type=non_negative_int, default=None, help="Explicit commit count")
    kind = p.add_mutually_exclusive_group()
    kind.add_argument("--feature", action="store_true")
    kind.add_argument("--breaking", action="store_true")
    return p.parse_args()

def main() -> int:
    a = parse_args()
    base = parse_version(a.base) if a.base else read_current_version()
    if a.from_tag is not None:
        ensure_tag_exists(a.from_tag)
        commits = commits_since_tag(a.from_tag)
    elif a.add_commits is not None:
        commits = a.add_commits
    else:
        auto_tag = tag_for_version(base)
        commits = commits_since_tag(auto_tag) if tag_exists(auto_tag) else 0
    major, minor, patch = add_patches(base, commits)
    if a.feature:
        major, minor, patch = apply_feature_increase(major, minor, patch)
    if a.breaking:
        major, minor, patch = apply_breaking_change(major, minor, patch)
    print(to_version(major, minor, patch))
    return 0


def run() -> int:
    try:
        return main()
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(run())
