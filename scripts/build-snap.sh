#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"
snapcraft_base="${SNAPCRAFT_BASE:-core22}"
if [[ ! "${snapcraft_base}" =~ ^[a-z][a-z0-9-]*$ ]]; then
  printf 'invalid SNAPCRAFT_BASE value: %s\n' "${snapcraft_base}" >&2
  exit 1
fi

require_cmd() {
  local tool=$1
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

require_regular_source_file() {
  local path=$1
  local label=$2
  local link_count

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

for tool in python3 snapcraft mktemp rm mkdir find realpath; do
  require_cmd "${tool}"
done
snap_dir="${repo_dir}/snap"

if ! snapcraft --version >/dev/null 2>&1; then
  printf 'snapcraft is installed but did not execute successfully.\n' >&2
  exit 1
fi

if [[ -L "${snap_dir}" ]]; then
  printf 'snap directory must not be a symlink: %s\n' "${snap_dir}" >&2
  exit 1
fi

if [[ ! -f "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest missing: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
if [[ -L "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest must not be a symlink: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
require_regular_source_file "${snap_dir}/snapcraft.yaml" "snapcraft manifest"

version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

repo_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${repo_tmp_root}" == /* ]]; then
  repo_tmp_root="/tmp"
fi
if [[ -L "${repo_tmp_root}" ]]; then
  repo_tmp_root="${repo_dir}/.tmp"
fi
if [[ ! -d "${repo_tmp_root}" || ! -w "${repo_tmp_root}" ]]; then
  repo_tmp_root="${repo_dir}/.tmp"
fi
if [[ -L "${repo_tmp_root}" ]]; then
  repo_tmp_root="/tmp"
fi
mkdir -p "${repo_tmp_root}"

snapcraft_file="${snap_dir}/snapcraft.yaml"
snapcraft_backup="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snapcraft-XXXXXX")"
cp "${snapcraft_file}" "${snapcraft_backup}"
tmp_output=""
cleanup_tmpdir() {
  if [[ -n "${tmp_output}" && -f "${tmp_output}" ]]; then
    rm -f -- "${tmp_output}"
  fi
  if [[ -f "${snapcraft_backup}" ]]; then
    mv -f -- "${snapcraft_backup}" "${snapcraft_file}"
    rm -f -- "${snapcraft_backup}"
  fi
}
trap cleanup_tmpdir EXIT

python3 - "${snapcraft_file}" "${version}" "${snapcraft_base}" <<'PYCODE'
import pathlib
import tempfile
import sys

path = pathlib.Path(sys.argv[1])
version = sys.argv[2]
base = sys.argv[3]
text = path.read_text(encoding="utf-8")
out = []
replaced = False
base_replaced = False
for line in text.splitlines():
    if line.startswith("version:"):
        out.append(f"version: \"{version}\"")
        replaced = True
    elif line.startswith("base:"):
        out.append(f"base: {base}")
        base_replaced = True
    else:
        out.append(line)
if not replaced:
    raise SystemExit("snapcraft version field not found")
if not base_replaced:
    raise SystemExit("snapcraft base field not found")
with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
    handle.write("\n".join(out) + "\n")
    tmp_path = pathlib.Path(handle.name)
tmp_path.replace(path)
PYCODE

dist_dir="${repo_dir}/dist/snap"
mkdir -p "${dist_dir}"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
rm -f -- "${dist_dir}/speed-of-cinnamon_${version}"_*.snap "${repo_dir}/speed-of-cinnamon_${version}"_*.snap

tmp_output="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snap-output-XXXXXX")"

( umask 022 && snapcraft pack --destructive-mode )
{
  find "${dist_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
  find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
} | sort -z > "${tmp_output}"

mapfile -d '' -t snap_files < "${tmp_output}"
if [[ ${#snap_files[@]} -ne 1 ]]; then
  printf 'expected exactly one new snap package, found %d\n' "${#snap_files[@]}" >&2
  exit 1
fi

for path in "${snap_files[@]}"; do
  if [[ -L "${path}" ]]; then
    printf 'snap package must not be a symlink: %s\n' "${path}" >&2
    exit 1
  fi
  absolute="$(realpath "${path}")"
  if [[ "${absolute}" != "${dist_dir}/speed-of-cinnamon_${version}_"* && "${absolute}" != "${repo_dir}/speed-of-cinnamon_${version}_"* ]]; then
    printf 'snap package path is unexpected: %s\n' "${path}" >&2
    exit 1
  fi
  filename="$(basename "${path}")"
  if [[ ! "${filename}" == "speed-of-cinnamon_${version}_"* ]]; then
    printf 'unexpected snap file name: %s\n' "${filename}" >&2
    exit 1
  fi
  if [[ ! -s "${path}" ]]; then
    printf 'snap package is empty: %s\n' "${path}" >&2
    exit 1
  fi
done

output_path="${dist_dir}/$(basename "${snap_files[0]}")"
mv "${snap_files[0]}" "${output_path}"
printf 'Built %s\n' "${output_path}" >&2
printf '%s\n' "${output_path}"
