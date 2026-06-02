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


class NextVersionError(Exception):
    exit_code = 2


class UserInputError(NextVersionError):
    exit_code = 2


class GitEnvironmentError(NextVersionError):
    exit_code = 3

def parse_version(value: str) -> tuple[int, int, int]:
    parts = value.strip().lstrip("vV").split(".")
    if len(parts) != 3:
        raise UserInputError(f"invalid version format: {value}")
    try:
        major, minor, patch = (int(x) for x in parts)
    except ValueError as exc:
        raise UserInputError(f"invalid version format: {value}") from exc
    if major < 0 or minor < 0 or patch < 0:
        raise UserInputError(f"invalid version format: {value}")
    return major, minor, patch


def normalize_tag(tag: str) -> str:
    return tag if tag.startswith("v") else f"v{tag}"

def to_version(major: int, minor: int, patch: int) -> str:
    return f"{major}.{minor}.{patch}"

def commits_since_ref(ref: str) -> int:
    try:
        result = subprocess.run(["git", "rev-list", "--count", f"{ref}..HEAD"], check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise GitEnvironmentError("git command not available") from exc
    except subprocess.CalledProcessError as exc:
        raise GitEnvironmentError(f"failed to compute commits since {ref}: {exc.stderr.strip()}") from exc
    try:
        return int(result.stdout.strip())
    except ValueError as exc:
        raise GitEnvironmentError(f"invalid git commit-count output: {result.stdout!r}") from exc

def commits_since_tag(tag: str) -> int:
    return commits_since_ref(normalize_tag(tag))

def add_patches(base: tuple[int,int,int], patch_steps: int) -> tuple[int,int,int]:
    major, minor, patch = base
    if patch_steps < 0:
        raise UserInputError("negative patch steps are not supported")
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
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise UserInputError(f"project metadata file not found: {path}") from exc
    except OSError as exc:
        raise UserInputError(f"unable to read project metadata: {exc}") from exc
    except Exception as exc:  # pragma: no cover - tomllib decode error path
        raise UserInputError(f"invalid project metadata: {exc}") from exc

    try:
        val = data["project"]["version"]
    except (KeyError, TypeError) as exc:
        raise UserInputError("project.version is missing") from exc
    if not isinstance(val, str):
        raise UserInputError("project.version is not a string")
    return parse_version(val)

def tag_for_version(version: tuple[int,int,int]) -> str:
    return f"v{to_version(*version)}"

def tag_exists(tag: str) -> bool:
    tag = normalize_tag(tag)
    try:
        rc = subprocess.run(["git", "tag", "-l", tag], text=True, capture_output=True, check=True)
    except FileNotFoundError as exc:
        raise GitEnvironmentError("git command not available") from exc
    except subprocess.CalledProcessError as exc:
        raise GitEnvironmentError(f"failed to inspect git tags: {exc.stderr.strip()}") from exc
    return tag in (rc.stdout or "").splitlines()

def ensure_tag_exists(tag: str) -> None:
    if not tag_exists(tag):
        raise UserInputError(f"release tag {tag} does not exist")

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
    except SystemExit as exc:
        if isinstance(exc.code, int) and exc.code == 0:
            return 0
        return 2
    except NextVersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

if __name__ == '__main__':
    raise SystemExit(run())
