#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${repo_dir}" ]]; then
  printf 'Could not determine repository directory.\n' >&2
  exit 1
fi
export PYTHONPATH="${repo_dir}/src${PYTHONPATH:+:${PYTHONPATH}}"
for tool in python3 command; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
exec "$(command -v python3)" -m speed_of_cinnamon.cli "$@"
