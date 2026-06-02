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
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

for tool in python3 snapcraft mktemp rm mkdir find; do
  require_cmd "${tool}"
done

if ! snapcraft --version >/dev/null 2>&1; then
  printf 'snapcraft is installed but did not execute successfully.\n' >&2
  exit 1
fi

if [[ ! -f "${repo_dir}/snap/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest missing: %s\n' "${repo_dir}/snap/snapcraft.yaml" >&2
  exit 1
fi

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
if [[ ! -d "${repo_tmp_root}" || ! -w "${repo_tmp_root}" ]]; then
  repo_tmp_root="${repo_dir}/.tmp"
fi
mkdir -p "${repo_tmp_root}"

snapcraft_file="${repo_dir}/snap/snapcraft.yaml"
snapcraft_backup="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snapcraft-XXXXXX")"
cp "${snapcraft_file}" "${snapcraft_backup}"
tmp_output=""
cleanup_tmpdir() {
  if [[ -n "${tmp_output}" && -f "${tmp_output}" ]]; then
    rm -f -- "${tmp_output}"
  fi
  if [[ -f "${snapcraft_backup}" ]]; then
    cp "${snapcraft_backup}" "${snapcraft_file}"
    rm -f -- "${snapcraft_backup}"
  fi
}
trap cleanup_tmpdir EXIT

python3 - "${snapcraft_file}" "${version}" "${snapcraft_base}" <<'PYCODE'
import pathlib
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
path.write_text("\n".join(out) + "\n", encoding="utf-8")
PYCODE

dist_dir="${repo_dir}/dist/snap"
mkdir -p "${dist_dir}"
rm -f -- "${dist_dir}/speed-of-cinnamon_${version}_*.snap" "${repo_dir}/speed-of-cinnamon_${version}_*.snap"

tmp_output="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snap-output-XXXXXX")"

( umask 022 && snapcraft --destructive-mode )
{
  find "${dist_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print
  find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print
} | sort -u > "${tmp_output}"

mapfile -t snap_files < "${tmp_output}"
if [[ ${#snap_files[@]} -ne 1 ]]; then
  printf 'expected exactly one new snap package, found %d\n' "${#snap_files[@]}" >&2
  exit 1
fi

for path in "${snap_files[@]}"; do
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
