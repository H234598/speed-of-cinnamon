#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

uuid="speed-of-cinnamon@H234598"
if [[ -z "${HOME:-}" ]]; then
  printf 'HOME must be set.\n' >&2
  exit 1
fi
applet_dir="${HOME}/.local/share/cinnamon/applets/${uuid}"
bin_path="${HOME}/.local/bin/speed-of-cinnamon"
man_dir="${HOME}/.local/share/man/man1"

if [[ "${HOME}" == "/" ]]; then
  printf 'Refusing to run uninstall from root home directory.\n' >&2
  exit 1
fi
if [[ "${applet_dir}" == "${HOME}" || "${bin_path}" == "${HOME}" || "${man_dir}" == "${HOME}" ]]; then
  printf 'Unsafe uninstall target resolved inside HOME root.\n' >&2
  exit 1
fi

rm -rf -- "${applet_dir}"
rm -f -- "${bin_path}"
rm -f -- "${man_dir}/speed-of-cinnamon.1"
rm -f -- "${man_dir}/speed-of-cinnamon-alarms.1"
printf 'Removed %s applet, backend wrapper, and local man pages.\n' "${uuid}"
