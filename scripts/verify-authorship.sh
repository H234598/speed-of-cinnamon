#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

readonly TRUSTED_PATH='/usr/bin:/usr/sbin:/bin:/sbin'
PATH="${TRUSTED_PATH}"
export PATH

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

python_bin="$(command -v python3 || true)"
git_bin="$(command -v git || true)"
if [[ -z "${python_bin}" || -z "${git_bin}" ]]; then
  printf 'python3 and git are required in trusted PATH\n' >&2
  exit 1
fi

"${python_bin}" - <<'PY' "${repo_dir}" "${git_bin}"
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tomllib
from pathlib import Path

repo_dir = Path(sys.argv[1])
git_bin = sys.argv[2]
expected_name = "H234598"
expected_email = "54270221+H234598@users.noreply.github.com"
expected_repo = "github.com/H234598/speed-of-cinnamon"
allowed_remote_urls = {
    f"https://{expected_repo}",
    f"git@github.com:H234598/speed-of-cinnamon",
    f"ssh://git@{expected_repo}",
}
allowed_history_commit_identities = {
    "5756bb6532666998e015c003a069ee5f5ac42aa0": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "9567d0a1daa6d7f982e769ed268155769dd39b91": (
        ("dependabot[bot]", "49699333+dependabot[bot]@users.noreply.github.com"),
        ("GitHub", "noreply@github.com"),
    ),
    "e3893bae4656b26fc83aa21477cac6b5733424c0": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "e4ec7d426844770cd655794c180dbf9ba35b7821": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "b13b5b8af393ca925958d100ae990b877624fcfe": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "0d3f81c54d9f6e87408cc9eb63497dedda2a4b95": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "f8210a7ebf692ae3246e2ba58256891f8fcde07e": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "137a38d0ac84d46cb667231bc00b2ab3f17ab5b9": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "63059a082f35713189f4efeb5d9f95a0349a66a0": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
    "c306b77cad968f5e25ec40b67f38c574f12c244d": (
        (expected_name, expected_email),
        ("GitHub", "noreply@github.com"),
    ),
}
forbidden = re.compile("staff" + r"[-_ ]?" + "control", re.IGNORECASE)
FORBIDDEN_SCAN_CHUNK_BYTES = 1 << 20
FORBIDDEN_SCAN_OVERLAP_CHARS = 64


def fail(message: str) -> None:
    raise SystemExit(message)


if not Path(git_bin).is_absolute():
    fail("git executable must resolve to an absolute path")


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [git_bin, "-C", str(repo_dir), *args],
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


def normalize_remote_url(remote: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 or 0x80 <= ord(char) <= 0x9F for char in remote):
        fail("origin must not contain control characters")
    normalized = remote.strip()
    if not normalized:
        fail("origin must not be empty")
    return normalized.removesuffix(".git")


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
        return [path for item in output.split("\0") if item and (path := repo_dir / item).exists()]

    ignored_dirs = {".git", "dist", "__pycache__", ".pytest_cache", ".mypy_cache"}
    files: list[Path] = []
    for root, dirs, names in os.walk(repo_dir):
        dirs[:] = [name for name in dirs if name not in ignored_dirs]
        files.extend(Path(root) / name for name in names)
    return files


def check_forbidden_names() -> None:
    for path in tracked_files():
        if path.is_symlink():
            continue
        if contains_forbidden_marker(path):
            fail(f"forbidden upstream author marker found in {path.relative_to(repo_dir)}")


def contains_forbidden_marker(path: Path) -> bool:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    try:
        fd = os.open(path, flags)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        with os.fdopen(fd, "r", encoding="utf-8", closefd=True) as handle:
            fd = None
            overlap = ""
            while True:
                chunk = handle.read(FORBIDDEN_SCAN_CHUNK_BYTES)
                if not chunk:
                    return forbidden.search(overlap) is not None
                candidate = overlap + chunk
                if forbidden.search(candidate):
                    return True
                overlap = candidate[-FORBIDDEN_SCAN_OVERLAP_CHARS:]
    except UnicodeDecodeError:
        return False
    except OSError as exc:
        fail(f"could not inspect tracked file {path.relative_to(repo_dir)}: {exc}")
    finally:
        if fd is not None:
            os.close(fd)


def check_git_identity() -> None:
    if not in_git_worktree():
        return

    remote_stdout = run_git("config", "--get", "remote.origin.url", check=False).stdout
    remote = remote_stdout.removesuffix("\n")
    if remote != remote.strip():
        fail("origin must not contain leading or trailing whitespace")
    normalized_remote = normalize_remote_url(remote)
    if normalized_remote not in allowed_remote_urls:
        fail(f"origin must point at {expected_repo}, got {remote!r}")

    shallow_result = run_git("rev-parse", "--is-shallow-repository", check=False)
    shallow_state = shallow_result.stdout.strip()
    if shallow_result.returncode != 0 or shallow_state not in {"true", "false"}:
        fail("could not determine whether repository is shallow")
    if shallow_state == "true":
        fail("cannot verify full commit history in shallow clone; use fetch-depth: 0")

    log = run_git(
        "--no-pager",
        "log",
        "--no-color",
        "--no-show-signature",
        "HEAD",
        "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce%x1e",
    ).stdout
    bad_commits: list[str] = []
    for record in log.strip("\x1e").split("\x1e"):
        record = record.strip()
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) != 5:
            fail(f"could not parse commit identity record: {record!r}")
        sha, author_name, author_email, committer_name, committer_email = fields
        normal_identity = (
            (author_name, author_email) == (expected_name, expected_email)
            and (committer_name, committer_email) == (expected_name, expected_email)
        )
        history_identity = allowed_history_commit_identities.get(sha)
        allowed_history_identity = (
            history_identity is not None
            and (author_name, author_email) == history_identity[0]
            and (committer_name, committer_email) == history_identity[1]
        )
        if not (normal_identity or allowed_history_identity):
            bad_commits.append(
                f"{sha[:12]} author={author_name} <{author_email}> "
                f"committer={committer_name} <{committer_email}>"
            )
    if bad_commits:
        fail("unexpected commit identities:\n" + "\n".join(bad_commits))


check_project_metadata()
check_forbidden_names()
check_git_identity()
print(f"Verified project authorship for {expected_name} <{expected_email}>")
PY
