#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"

for tool in python3 tar sha256sum mktemp cp find rm git stat; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done

name="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["name"])
PY
)"
version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"
if [[ ! "${name}" == "speed-of-cinnamon" ]]; then
  printf 'unexpected package name: %s\n' "${name}" >&2
  exit 1
fi
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

package="${name}-${version}"
dist_dir="${repo_dir}/dist"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
work_root="${TMPDIR:-/tmp}"
if [[ ! "${work_root}" == /* ]]; then
  work_root="/tmp"
fi
if [[ -L "${work_root}" ]]; then
  work_root="${repo_dir}/.tmp"
fi
if [[ ! -d "${work_root}" || ! -w "${work_root}" ]]; then
  work_root="${repo_dir}/.tmp"
fi
if [[ -L "${work_root}" ]]; then
  work_root="/tmp"
fi
mkdir -p "${work_root}"
work_dir="$(mktemp -d "${work_root}/speed-of-cinnamon-build-dist-XXXXXX")"
staging_tarball=""
staging_checksum=""
cleanup() {
  if [[ -n "${staging_tarball}" ]]; then
    rm -f -- "${staging_tarball}"
  fi
  if [[ -n "${staging_checksum}" ]]; then
    rm -f -- "${staging_checksum}"
  fi
  rm -rf -- "${work_dir}"
}
trap cleanup EXIT

mkdir -p "${dist_dir}" "${work_dir}/${package}"

require_unsafe_source() {
  local path=$1
  local label=$2
  local link_count

  if [[ -d "${path}" ]]; then
    if find "${path}" \( -type l -o -type f -links +1 \) -print -quit | grep -q .; then
      printf '%s must not contain symlinks or hardlinks: %s\n' "${label}" "${path}" >&2
      exit 1
    fi
    return
  fi
  if [[ ! -f "${path}" || -L "${path}" ]]; then
    printf '%s must be a regular file: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${path}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf '%s must not be hardlinked: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
}

for path in \
  .github \
  docs \
  files \
  packaging \
  scripts \
  src \
  tests \
  LICENSE \
  Makefile \
  pyproject.toml \
  README.md
do
  require_unsafe_source "${repo_dir}/${path}" "distribution source"
  cp -a "${repo_dir}/${path}" "${work_dir}/${package}/"
done
require_unsafe_source "${safe_fs}" "safe local filesystem helper"

find "${work_dir}/${package}" \
  -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \) \
  -prune -exec rm -rf {} +
find "${work_dir}/${package}" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

if find "${work_dir}/${package}" -type l -print -quit | grep -q .; then
  printf 'build-dist detected unsupported symlink in package contents.\n' >&2
  exit 1
fi

cat > "${work_dir}/${package}/RELEASE-MANIFEST.txt" <<EOF
${package}

Contains:
- Cinnamon applet files under files/speed-of-cinnamon@H234598/
- Python backend under src/speed_of_cinnamon/
- local build, verify, install, uninstall, and dependency scripts under scripts/
- tests, CI workflow, README, license, and docs
EOF

final_tarball="${dist_dir}/${package}.tar.gz"
final_checksum="${final_tarball}.sha256"
staging_tarball="$(mktemp "${dist_dir}/.${package}.tar.gz.XXXXXX")"

tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@0" -C "${work_dir}" -czf "${staging_tarball}" "${package}"
python3 "${safe_fs}" replace build-dist "${staging_tarball}" "${final_tarball}" --src-kind file
staging_tarball=""
checksum_value="$(sha256sum "${final_tarball}")"
checksum_value="${checksum_value%% *}"
staging_checksum="$(mktemp "${dist_dir}/.${package}.tar.gz.sha256.XXXXXX")"
printf '%s  %s\n' "${checksum_value}" "${package}.tar.gz" > "${staging_checksum}"
python3 "${safe_fs}" replace build-dist "${staging_checksum}" "${final_checksum}" --src-kind file
staging_checksum=""

printf 'Built %s\n' "${final_tarball}" >&2
printf '%s\n' "${final_tarball}"
