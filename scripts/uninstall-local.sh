#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

uuid="speed-of-cinnamon@H234598"
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

if [[ "${HOME}" == "/" ]]; then
  printf 'Refusing to run uninstall from root home directory.\n' >&2
  exit 1
fi
for target in "${applet_dir}" "${bin_path}" "${man_dir}" "${python_dir}"; do
  if [[ -L "${target}" ]]; then
    printf 'refusing to follow symlink during uninstall: %s\n' "${target}" >&2
    exit 1
  fi
done
if [[ "${applet_dir}" == "${HOME}" || "${bin_path}" == "${HOME}" || "${man_dir}" == "${HOME}" || "${python_dir}" == "${HOME}" ]]; then
  printf 'Unsafe uninstall target resolved inside HOME root.\n' >&2
  exit 1
fi

rm -rf -- "${applet_dir}"
rm -f -- "${bin_path}"
rm -f -- "${man_dir}/speed-of-cinnamon.1"
rm -f -- "${man_dir}/speed-of-cinnamon-alarms.1"
rm -rf -- "${python_dir}"
rmdir --ignore-fail-on-non-empty -- "${app_data}" 2>/dev/null || true
printf 'Removed %s applet, backend wrapper, local Python package, and local man pages.\n' "${uuid}"
