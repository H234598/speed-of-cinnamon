#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

python3 - <<'PY' "${repo_dir}"
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

repo_dir = Path(sys.argv[1])
expected_name = "H234598"
expected_email = "54270221+H234598@users.noreply.github.com"
expected_repo = "github.com/H234598/speed-of-cinnamon"
legacy_name = "Staff" + "-Control"
forbidden = re.compile("staff" + r"[-_ ]?" + "control", re.IGNORECASE)


def fail(message: str) -> None:
    raise SystemExit(message)


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def in_git_worktree() -> bool:
    result = run_git("rev-parse", "--is-inside-work-tree", check=False)
    if result.returncode != 0 or result.stdout.strip() != "true":
        return False
    top_level = run_git("rev-parse", "--show-toplevel").stdout.strip()
    return Path(top_level).resolve() == repo_dir.resolve()


def check_project_metadata() -> None:
    pyproject = tomllib.loads((repo_dir / "pyproject.toml").read_text(encoding="utf-8"))
    authors = pyproject["project"].get("authors", [])
    if authors != [{"name": expected_name}]:
        fail(f"pyproject authors must be {expected_name!r}, got {authors!r}")

    applet_metadata_path = repo_dir / "files" / "speed-of-cinnamon@H234598" / "metadata.json"
    applet_metadata = json.loads(applet_metadata_path.read_text(encoding="utf-8"))
    if applet_metadata.get("author") != expected_name:
        fail(f"{applet_metadata_path.relative_to(repo_dir)} author must be {expected_name!r}")

    spec = (repo_dir / "packaging" / "speed-of-cinnamon.spec").read_text(encoding="utf-8")
    if f"Packager:       {expected_name} <{expected_email}>" not in spec:
        fail("RPM spec Packager does not match the expected GitHub identity")
    if f"Vendor:         {expected_name}" not in spec:
        fail("RPM spec Vendor does not match the expected GitHub identity")
    if f"URL:            https://{expected_repo}" not in spec:
        fail("RPM spec URL does not point at the expected repository")


def tracked_files() -> list[Path]:
    if in_git_worktree():
        output = run_git("ls-files", "-z").stdout
        return [repo_dir / item for item in output.split("\0") if item]

    ignored_dirs = {".git", "dist", "__pycache__", ".pytest_cache", ".mypy_cache"}
    files: list[Path] = []
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = [name for name in dirs if name not in ignored_dirs]
        files.extend(Path(root) / name for name in names)
    return files


def check_forbidden_names() -> None:
    for path in tracked_files():
        if path.relative_to(repo_dir).as_posix() == ".mailmap":
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if forbidden.search(content):
            fail(f"forbidden upstream author marker found in {path.relative_to(repo_dir)}")


def check_mailmap() -> None:
    mailmap_path = repo_dir / ".mailmap"
    content = mailmap_path.read_text(encoding="utf-8").splitlines()
    expected_lines = {
        "H234598 <54270221+H234598@users.noreply.github.com> <180772946+H234598@users.noreply.github.com>",
        f"H234598 <54270221+H234598@users.noreply.github.com> {legacy_name} <180772946+{legacy_name}@users.noreply.github.com>",
        f"H234598 <54270221+H234598@users.noreply.github.com> {legacy_name} <54270221+{legacy_name}@users.noreply.github.com>",
        f"H234598 <54270221+H234598@users.noreply.github.com> {legacy_name} <54270221+H234598@users.noreply.github.com>",
    }
    missing = expected_lines.difference(content)
    if missing:
        fail(".mailmap misses legacy GitHub author mappings:\n" + "\n".join(sorted(missing)))

    if not in_git_worktree():
        return

    checks = [
        f"{legacy_name} <180772946+{legacy_name}@users.noreply.github.com>",
        f"{legacy_name} <54270221+{legacy_name}@users.noreply.github.com>",
        f"{legacy_name} <54270221+H234598@users.noreply.github.com>",
        "H234598 <180772946+H234598@users.noreply.github.com>",
    ]
    output = run_git("check-mailmap", *checks).stdout.splitlines()
    expected = f"{expected_name} <{expected_email}>"
    for line in output:
        if line != expected:
            fail(f"legacy GitHub author mapping resolved to {line!r}, expected {expected!r}")


def check_git_identity() -> None:
    if not in_git_worktree():
        return

    remote = run_git("config", "--get", "remote.origin.url", check=False).stdout.strip()
    normalized_remote = remote.removesuffix(".git")
    if expected_repo not in normalized_remote:
        fail(f"origin must point at {expected_repo}, got {remote!r}")

    log = run_git("--no-pager", "log", "--all", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1e").stdout
    bad_commits: list[str] = []
    for record in log.strip("\x1e").split("\x1e"):
        record = record.strip()
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) != 5:
            fail(f"could not parse commit identity record: {record!r}")
        sha, author_name, author_email, committer_name, committer_email = fields
        if (
            author_name != expected_name
            or author_email != expected_email
            or committer_name != expected_name
            or committer_email != expected_email
        ):
            bad_commits.append(
                f"{sha[:12]} author={author_name} <{author_email}> "
                f"committer={committer_name} <{committer_email}>"
            )
    if bad_commits:
        fail("unexpected commit identities:\n" + "\n".join(bad_commits))


check_project_metadata()
check_forbidden_names()
check_mailmap()
check_git_identity()
print(f"Verified project authorship for {expected_name} <{expected_email}>")
PY
