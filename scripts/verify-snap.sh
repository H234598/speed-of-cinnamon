#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

require_cmd() {
  local tool=$1
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

require_cmd realpath
require_cmd wc

if [[ $# -lt 1 ]]; then
  printf 'usage: %s <snap-path>\n' "$0" >&2
  exit 1
fi

snap_path="$1"

if [[ ! -f "${snap_path}" ]]; then
  printf 'snap file not found: %s\n' "${snap_path}" >&2
  exit 1
fi

if [[ -L "${snap_path}" ]]; then
  printf 'snap file must not be a symlink: %s\n' "${snap_path}" >&2
  exit 1
fi

absolute="$(realpath "${snap_path}")"
if [[ "${absolute}" != "${repo_dir}/dist/snap/"* ]]; then
  printf 'snap file is outside repository root: %s\n' "${snap_path}" >&2
  exit 1
fi

if [[ ! "$(basename "${absolute}")" == speed-of-cinnamon_*_*.snap ]]; then
  printf 'unexpected snap file name: %s\n' "${snap_path}" >&2
  exit 1
fi

size="$(wc -c < "${absolute}")"
if [[ "${size}" -le 0 ]]; then
  printf 'snap file is empty: %s\n' "${snap_path}" >&2
  exit 1
fi

printf 'Verified snap package: %s (%s bytes)\n' "${absolute}" "${size}"
