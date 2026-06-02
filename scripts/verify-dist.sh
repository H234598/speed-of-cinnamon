#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

if [[ $# -ne 1 ]]; then
  printf 'usage: %s dist/speed-of-cinnamon-VERSION.tar.gz\n' "$0" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

dist_dir="${repo_dir}/dist"
if [[ -L "${dist_dir}" || ! -d "${dist_dir}" ]]; then
  printf 'dist directory is invalid: %s\n' "${dist_dir}" >&2
  exit 1
fi

if ! command -v -- realpath >/dev/null 2>&1; then
  printf 'realpath not found.\n' >&2
  exit 1
fi

tarball="$(realpath "$1")"
if [[ -L "${tarball}" || ! -f "${tarball}" || ! "${tarball}" == *.tar.gz || ! "${tarball}" == "${repo_dir}/dist/"* ]]; then
  printf 'archive missing or invalid: %s\n' "${tarball}" >&2
  exit 1
fi

if ! tar -tzf "${tarball}" | awk -F'/' '
  /(^|\/)\.\.(\/|$)/ || /^\// { print; bad = 1 }
  END { exit bad ? 1 : 0 }
' > /dev/null; then
  printf 'archive contains unsafe path entries (path traversal or absolute path): %s\n' "${tarball}" >&2
  exit 1
fi

tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${tmp_root}" == /* ]]; then
  tmp_root="/tmp"
fi
if [[ -L "${tmp_root}" ]]; then
  tmp_root="${repo_dir}/.tmp"
fi
if [[ ! -d "${tmp_root}" || ! -w "${tmp_root}" ]]; then
  tmp_root="${repo_dir}/.tmp"
fi
if [[ -L "${tmp_root}" ]]; then
  tmp_root="${repo_dir}/.tmp"
fi
mkdir -p "${tmp_root}"

tmp_dir="$(mktemp -d "${tmp_root}/speed-of-cinnamon-dist-verify-XXXXXX")"
cleanup_tmpdir() {
  rm -rf -- "${tmp_dir}"
}
trap cleanup_tmpdir EXIT

python3 - "$tarball" "$tmp_dir" <<'PY'
import pathlib
import tarfile
import sys

tarball = sys.argv[1]
target = pathlib.Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)

with tarfile.open(tarball, "r:gz") as archive:
    for member in archive.getmembers():
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"dist archive contains unsupported entry type: {member.name}")
        if member.name.startswith("/"):
            raise SystemExit(f"dist archive path is absolute: {member.name}")
        if ".." in member.name.split("/"):
            raise SystemExit(f"dist archive path escapes target: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"dist archive contains unsupported link entry: {member.name}")
        archive.extract(member, target)
PY

package_dirs=()
while IFS= read -r -d '' path; do
  package_dirs+=("${path}")
done < <(find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

if [[ ${#package_dirs[@]} -ne 1 ]]; then
  printf 'archive should contain exactly one top-level directory, found %d\n' "${#package_dirs[@]}" >&2
  exit 1
fi

package_dir="${package_dirs[0]}"
if [[ -z "${package_dir}" || ! -d "${package_dir}" ]]; then
  printf 'archive did not contain a package directory: %s\n' "${tarball}" >&2
  exit 1
fi

if find "${package_dir}" -type l -print -quit | grep -q .; then
  printf 'archive expansion contains unsupported symlink entries.\n' >&2
  exit 1
fi
# shellcheck disable=SC2016
if ! grep -Fq 'exec "$(command -v -- python3)" -m speed_of_cinnamon.cli "$@"' "${package_dir}/scripts/install-local.sh"; then
  printf 'archive install-local wrapper does not invoke the expected CLI module.\n' >&2
  exit 1
fi

for path in \
  README.md \
  LICENSE \
  RELEASE-MANIFEST.txt \
  Makefile \
  pyproject.toml \
  packaging/speed-of-cinnamon.spec \
  docs/architecture.md \
  docs/cli-reference.md \
  docs/development.md \
  docs/fedora-cinnamon-runbook.md \
  docs/man/speed-of-cinnamon.1 \
  docs/man/speed-of-cinnamon-alarms.1 \
  docs/user-guide.md \
  docs/wiki/Home.md \
  files/speed-of-cinnamon@H234598/applet.js \
  files/speed-of-cinnamon@H234598/metadata.json \
  files/speed-of-cinnamon@H234598/settings-schema.json \
  scripts/install-local.sh \
  scripts/publish-github-release.sh \
  scripts/verify-authorship.sh \
  scripts/verify-rpm.sh \
  src/speed_of_cinnamon/alarms.py \
  src/speed_of_cinnamon/cli.py \
  src/speed_of_cinnamon/setup_plan.py \
  tests/test_alarms.py \
  tests/test_ci_static.py \
  tests/test_cli.py
do
  if [[ ! -e "${package_dir}/${path}" ]]; then
    printf 'archive is missing %s\n' "${path}" >&2
    exit 1
  fi
done

python3 -m compileall -q "${package_dir}"

printf 'Verified %s\n' "${tarball}"
