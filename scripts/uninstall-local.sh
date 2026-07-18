#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

uuid="speed-of-cinnamon@H234598"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -z "${HOME:-}" ]]; then
  printf 'HOME must be set.\n' >&2
  exit 1
fi
account_home="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6 || true)"
if [[ "${SPEED_OF_CINNAMON_TEST_HOME:-0}" != "1" && ( -z "${account_home}" || "${HOME}" != "${account_home}" ) ]]; then
  printf 'Refusing to run with mismatched HOME: %s (expected %s).\n' "${HOME}" "${account_home}" >&2
  exit 1
fi
if [[ -L "${HOME}" ]]; then
  printf 'HOME must not be a symlink: %s\n' "${HOME}" >&2
  exit 1
fi
if [[ "${HOME}" == "/" ]]; then
  printf 'Refusing to run with root home directory.\n' >&2
  exit 1
fi
if [[ ! -d "${HOME}" ]]; then
  printf 'HOME must be an existing directory: %s\n' "${HOME}" >&2
  exit 1
fi
applet_dir="${HOME}/.local/share/cinnamon/applets/${uuid}"
bin_path="${HOME}/.local/bin/speed-of-cinnamon"
man_dir="${HOME}/.local/share/man/man1"
app_data="${HOME}/.local/share/speed-of-cinnamon"
python_dir="${app_data}/python"

if [[ ! -f "${repo_dir}/scripts/safe-local-fs.py" || -L "${repo_dir}/scripts/safe-local-fs.py" ]]; then
  printf 'missing required helper: %s\n' "${repo_dir}/scripts/safe-local-fs.py" >&2
  exit 1
fi
if ! command -v -- realpath >/dev/null 2>&1; then
  printf 'realpath not found.\n' >&2
  exit 1
fi
resolve_python3() {
  local candidate
  local resolved
  for candidate in /usr/bin/python3 /bin/python3; do
    if [[ -x "${candidate}" && ! -d "${candidate}" ]]; then
      resolved="$(realpath "${candidate}")"
      printf '%s\n' "${resolved}"
      return 0
    fi
  done
  candidate="$(command -v -- python3 || true)"
  if [[ -z "${candidate}" ]]; then
    printf 'python3 not found.\n' >&2
    return 1
  fi
  resolved="$(realpath "${candidate}")"
  if [[ "${resolved}" != /* || ! -x "${resolved}" || -d "${resolved}" ]]; then
    printf 'python3 path is invalid: %s\n' "${candidate}" >&2
    return 1
  fi
  printf '%s\n' "${resolved}"
}
python3_path="$(resolve_python3)"
safe_fs() {
  "${python3_path}" "${repo_dir}/scripts/safe-local-fs.py" "$@"
}

snapshot_identity() {
  local target="$1"
  local kind="$2"

  if [[ -e "${target}" || -L "${target}" ]]; then
    safe_fs identity uninstall "${target}" --kind "${kind}"
  else
    printf 'missing\n'
  fi
}

remove_installed_target() {
  local target="$1"
  local kind="$2"
  local expected_identity

  expected_identity="$(snapshot_identity "${target}" "${kind}")"
  safe_fs remove uninstall "${target}" --kind "${kind}" --expected-identity "${expected_identity}"
}

if [[ "${HOME}" == "/" ]]; then
  printf 'Refusing to run uninstall from root home directory.\n' >&2
  exit 1
fi
reject_symlink_ancestors() {
  local path="$1"
  local action="$2"
  local parent="${path%/*}"
  local next

  while [[ -n "${parent}" && "${parent}" != "${path}" && "${parent}" != "/" ]]; do
    if [[ -L "${parent}" ]]; then
      printf 'refusing to follow symlink during %s: %s\n' "${action}" "${parent}" >&2
      exit 1
    fi
    next="${parent%/*}"
    if [[ "${next}" == "${parent}" ]]; then
      break
    fi
    parent="${next}"
  done
}

for target in "${applet_dir}" "${bin_path}" "${man_dir}" "${python_dir}" "${app_data}"; do
  reject_symlink_ancestors "${target}" "uninstall"
  if [[ -L "${target}" ]]; then
    printf 'refusing to follow symlink during uninstall: %s\n' "${target}" >&2
    exit 1
  fi
done
if [[ "${applet_dir}" == "${HOME}" || "${bin_path}" == "${HOME}" || "${man_dir}" == "${HOME}" || "${python_dir}" == "${HOME}" ]]; then
  printf 'Unsafe uninstall target resolved inside HOME root.\n' >&2
  exit 1
fi

app_data_identity="$(snapshot_identity "${app_data}" dir)"
remove_installed_target "${applet_dir}" dir
remove_installed_target "${bin_path}" file
remove_installed_target "${man_dir}/speed-of-cinnamon.1" file
remove_installed_target "${man_dir}/speed-of-cinnamon-alarms.1" file
remove_installed_target "${python_dir}" dir
safe_fs rmdir uninstall "${app_data}" --ignore-non-empty --expected-identity "${app_data_identity}"
printf 'Removed %s applet, backend wrapper, local Python package, and local man pages.\n' "${uuid}"
