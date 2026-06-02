#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uuid="speed-of-cinnamon@H234598"
app_data="${HOME}/.local/share/speed-of-cinnamon"
bin_dir="${HOME}/.local/bin"
applet_target="${HOME}/.local/share/cinnamon/applets/${uuid}"
man_dir="${HOME}/.local/share/man/man1"
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
for tool in cp install mkdir rm command; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
for path in \
  "${repo_dir}/files/${uuid}" \
  "${repo_dir}/src/speed_of_cinnamon" \
  "${repo_dir}/docs/man/speed-of-cinnamon.1" \
  "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1"
do
  if [[ ! -e "${path}" || -L "${path}" ]]; then
    printf 'missing required source path: %s\n' "${path}" >&2
    exit 1
  fi
done

reject_unsafe_tree() {
  local tree="$1"
  local label="$2"
  if find "${tree}" \( -type l -o -type f -links +1 \) -print -quit | grep -q .; then
    printf 'refusing to install unsafe %s: %s\n' "${label}" "${tree}" >&2
    exit 1
  fi
}

reject_unsafe_file() {
  local path="$1"
  local label="$2"
  local link_count

  if [[ ! -f "${path}" || -L "${path}" ]]; then
    printf 'refusing to install unsafe %s: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${path}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf 'refusing to install hardlinked %s: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
}

for target in "${applet_target}" "${app_data}" "${bin_dir}" "${man_dir}"; do
  if [[ -L "${target}" ]]; then
    printf 'refusing to follow symlink during install: %s\n' "${target}" >&2
    exit 1
  fi
done

mkdir -p "$(dirname "${applet_target}")" "${app_data}" "${bin_dir}" "${man_dir}"
rm -rf "${applet_target}"
reject_unsafe_tree "${repo_dir}/files/${uuid}" "applet source tree"
cp -a "${repo_dir}/files/${uuid}" "${applet_target}"
reject_unsafe_tree "${applet_target}" "applet source tree"

rm -rf "${app_data}/python"
mkdir -p "${app_data}/python"
reject_unsafe_tree "${repo_dir}/src/speed_of_cinnamon" "python package source tree"
cp -a "${repo_dir}/src/speed_of_cinnamon" "${app_data}/python/"
reject_unsafe_tree "${app_data}/python/speed_of_cinnamon" "python package"

reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon.1" "man page source"
reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1" "man page source"

rm -f "${bin_dir}/speed-of-cinnamon"
{
  printf '#!/usr/bin/env bash\n'
  printf 'set -euo pipefail\n'
  printf 'export PYTHONPATH=%q\n' "${app_data}/python"
  # shellcheck disable=SC2016
  printf 'exec "$(command -v -- python3)" -m speed_of_cinnamon.cli "$@"\n'
} > "${bin_dir}/speed-of-cinnamon"
chmod +x "${bin_dir}/speed-of-cinnamon"

install -m 0644 "${repo_dir}/docs/man/speed-of-cinnamon.1" "${man_dir}/speed-of-cinnamon.1"
install -m 0644 "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1" "${man_dir}/speed-of-cinnamon-alarms.1"

printf 'Installed %s to %s\n' "${uuid}" "${applet_target}"
printf 'Installed backend command to %s/speed-of-cinnamon\n' "${bin_dir}"
printf 'Installed man pages to %s\n' "${man_dir}"
if ! command -v -- whisper >/dev/null 2>&1 \
    && ! command -v -- whisper-cli >/dev/null 2>&1 \
    && ! command -v -- whisper.cpp >/dev/null 2>&1 \
    && ! command -v -- pwcpp >/dev/null 2>&1; then
    printf 'ASR backend missing. On Fedora install python3-pywhispercpp, then run: speed-of-cinnamon download-model tiny --json\n'
fi
account_home="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6 || true)"
if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" && -n "${account_home}" && "${HOME}" == "${account_home}" ]] \
    && command -v -- dbus-send >/dev/null 2>&1; then
    if dbus-send --session --dest=org.Cinnamon.LookingGlass --type=method_call \
        /org/Cinnamon/LookingGlass org.Cinnamon.LookingGlass.ReloadExtension \
        string:"${uuid}" string:'APPLET' >/dev/null 2>&1; then
        printf 'Reloaded Cinnamon applet %s\n' "${uuid}"
    else
        printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'
    fi
else
    printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'
fi
