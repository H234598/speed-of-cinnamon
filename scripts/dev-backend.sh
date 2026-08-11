#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${repo_dir}" ]]; then
  printf 'Could not determine repository directory.\n' >&2
  exit 1
fi
export PYTHONPATH="${repo_dir}/src"
for tool in python3 realpath; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
python3_path="$(realpath -- "$(command -v -- python3)")"
if [[ "${python3_path}" != /* || ! -x "${python3_path}" || -d "${python3_path}" ]]; then
  printf 'python3 path is invalid: %s\n' "${python3_path}" >&2
  exit 1
fi
exec "${python3_path}" -m speed_of_cinnamon.cli "$@"
