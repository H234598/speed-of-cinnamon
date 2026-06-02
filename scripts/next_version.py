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


COMMITS_PER_PATCH = 100
PATCHES_PER_MINOR = 100
MINORS_PER_MAJOR = 100


def _assert_non_negative_int(name: str, value: int) -> int:
    if isinstance(value, bool):
        raise UserInputError(f"{name} must be an int")
    if not isinstance(value, int):
        raise UserInputError(f"{name} must be an int")
    if value < 0:
        raise UserInputError(f"{name} must be >= 0")
    return value


def _assert_version_tuple(value: tuple[int, int, int]) -> tuple[int, int, int]:
    if not isinstance(value, tuple) or len(value) != 3:
        raise UserInputError("base version must be a tuple of three elements")
    major, minor, patch = value
    return (
        _assert_non_negative_int("major", major),
        _assert_non_negative_int("minor", minor),
        _assert_non_negative_int("patch", patch),
    )


def parse_version(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise UserInputError("invalid version format: value must be a string")
    parts = value.strip().lstrip("vV").split(".")
    if len(parts) != 3:
        raise UserInputError(f"invalid version format: {value}")
    try:
        major, minor, patch = (int(x) for x in parts)
    except ValueError as exc:
        raise UserInputError(f"invalid version format: {value}") from exc
    return _assert_version_tuple((major, minor, patch))


def normalize_tag(tag: str) -> str:
    if not isinstance(tag, str):
        raise UserInputError("tag must be a non-empty string")
    tag = tag.strip()
    if tag == "":
        raise UserInputError("tag must be a non-empty string")
    return tag if tag.startswith("v") else f"v{tag}"

def to_version(major: int, minor: int, patch: int) -> str:
    major = _assert_non_negative_int("major", major)
    minor = _assert_non_negative_int("minor", minor)
    patch = _assert_non_negative_int("patch", patch)
    return f"{major}.{minor}.{patch}"

def commits_since_ref(ref: str) -> int:
    if not isinstance(ref, str):
        raise UserInputError("ref must be a non-empty string")
    ref = ref.strip()
    if ref == "":
        raise UserInputError("ref must be a non-empty string")
    try:
        result = subprocess.run(["git", "rev-list", "--count", f"{ref}..HEAD"], check=True, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise GitEnvironmentError("git command not available") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise GitEnvironmentError(f"failed to compute commits since {ref}: {stderr}") from exc
    try:
        value_text = result.stdout.strip()
        if value_text == "":
            raise GitEnvironmentError(f"invalid git commit-count output: {result.stdout!r}")
        value = int(value_text)
    except ValueError as exc:
        raise GitEnvironmentError(f"invalid git commit-count output: {result.stdout!r}") from exc
    if value < 0:
        raise GitEnvironmentError(f"invalid git commit-count output: {value}")
    return value

def commits_since_tag(tag: str) -> int:
    return commits_since_ref(normalize_tag(tag))

def add_patches(base: tuple[int,int,int], patch_steps: int) -> tuple[int,int,int]:
    major, minor, patch = _assert_version_tuple(base)
    patch_steps = _assert_non_negative_int("patch_steps", patch_steps)
    patch_steps = patch_steps // COMMITS_PER_PATCH
    total_patch = patch + patch_steps
    minor += total_patch // PATCHES_PER_MINOR
    patch = total_patch % PATCHES_PER_MINOR
    major += minor // MINORS_PER_MAJOR
    minor %= MINORS_PER_MAJOR
    return major, minor, patch

def apply_feature_increase(major:int, minor:int, patch:int) -> tuple[int,int,int]:
    major = _assert_non_negative_int("major", major)
    minor = _assert_non_negative_int("minor", minor)
    patch = _assert_non_negative_int("patch", patch)
    minor += 1
    if minor >= MINORS_PER_MAJOR:
        minor = 0
        major += 1
    return major, minor, patch

def apply_breaking_change(major:int, minor:int, patch:int) -> tuple[int,int,int]:
    major = _assert_non_negative_int("major", major)
    minor = _assert_non_negative_int("minor", minor)
    patch = _assert_non_negative_int("patch", patch)
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
    return f"v{to_version(*_assert_version_tuple(version))}"

def tag_exists(tag: str) -> bool:
    if not isinstance(tag, str):
        raise UserInputError("tag must be a non-empty string")
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
        value = value.strip()
        if value == "":
            raise argparse.ArgumentTypeError("must be >= 0")
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
    if a.base is not None and not isinstance(a.base, str):
        raise UserInputError("base must be a valid version string")
    if a.from_tag is not None and not isinstance(a.from_tag, str):
        raise UserInputError("from-tag must be a valid version string")
    base_raw = a.base.strip() if isinstance(a.base, str) else a.base
    from_tag_raw = a.from_tag.strip() if isinstance(a.from_tag, str) else a.from_tag
    if a.base is not None:
        if not base_raw:
            raise UserInputError("base must be a non-empty version")
        base = parse_version(base_raw)
    else:
        base = read_current_version()
    if from_tag_raw is not None and a.from_tag is not None:
        if not from_tag_raw:
            raise UserInputError("from-tag must be a non-empty version")
        ensure_tag_exists(from_tag_raw)
        commits = commits_since_tag(from_tag_raw)
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
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except NextVersionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"error: unexpected error: {exc}", file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(run())
