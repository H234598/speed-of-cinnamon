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
for tool in cp mkdir mktemp python3 rmdir command; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
for path in \
  "${repo_dir}/files/${uuid}" \
  "${repo_dir}/src/speed_of_cinnamon" \
  "${repo_dir}/scripts/safe-local-fs.py" \
  "${repo_dir}/docs/man/speed-of-cinnamon.1" \
  "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1"
do
  if [[ ! -e "${path}" || -L "${path}" ]]; then
    printf 'missing required source path: %s\n' "${path}" >&2
    exit 1
  fi
done

safe_fs() {
  python3 "${repo_dir}/scripts/safe-local-fs.py" "$@"
}

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

  if ! safe_fs assert-file install "${path}" "${label}"; then
    printf 'refusing to install unsafe %s: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
}

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

write_backend_wrapper() {
  local wrapper_path="${bin_dir}/speed-of-cinnamon"

  reject_symlink_ancestors "${wrapper_path}" "install"
  if [[ -L "${bin_dir}" || -L "${wrapper_path}" ]]; then
    printf 'refusing to follow symlink during install: %s\n' "${wrapper_path}" >&2
    exit 1
  fi
  if ! safe_fs write-wrapper install "${wrapper_path}" "${app_data}/python"; then
    printf 'failed to install backend wrapper: %s\n' "${wrapper_path}" >&2
    exit 1
  fi
}

for target in "${applet_target}" "${app_data}" "${bin_dir}" "${man_dir}"; do
  reject_symlink_ancestors "${target}" "install"
  if [[ -L "${target}" ]]; then
    printf 'refusing to follow symlink during install: %s\n' "${target}" >&2
    exit 1
  fi
done

install_tree_staged() {
  local source_tree="$1"
  local target_tree="$2"
  local label="$3"
  local parent="${target_tree%/*}"
  local name="${target_tree##*/}"
  local stage_root
  local staged_tree
  local backup_tree=""

  if [[ -z "${parent}" || "${parent}" == "${target_tree}" ]]; then
    printf 'invalid install target for %s: %s\n' "${label}" "${target_tree}" >&2
    exit 1
  fi
  if [[ -L "${parent}" || -L "${target_tree}" ]]; then
    printf 'refusing to follow symlink during install: %s\n' "${target_tree}" >&2
    exit 1
  fi
  reject_symlink_ancestors "${target_tree}" "install"
  safe_fs mkdirs install "${parent}"
  reject_symlink_ancestors "${target_tree}" "install"
  if [[ -L "${parent}" || -L "${target_tree}" ]]; then
    printf 'refusing to follow symlink during install: %s\n' "${target_tree}" >&2
    exit 1
  fi
  reject_unsafe_tree "${source_tree}" "${label} source tree"

  stage_root="$(mktemp -d "${parent}/.${name}.install.XXXXXX")"
  staged_tree="${stage_root}/${name}"
  if ! cp -a "${source_tree}" "${staged_tree}"; then
    safe_fs remove install "${stage_root}" --kind dir || true
    printf 'failed to stage %s install: %s\n' "${label}" "${target_tree}" >&2
    exit 1
  fi
  reject_unsafe_tree "${staged_tree}" "${label}"

  if [[ -e "${target_tree}" ]]; then
    backup_tree="$(mktemp -d "${parent}/.${name}.backup.XXXXXX")"
    rmdir -- "${backup_tree}"
    reject_symlink_ancestors "${target_tree}" "install"
    if [[ -L "${parent}" || -L "${target_tree}" || -L "${backup_tree}" ]]; then
      safe_fs remove install "${stage_root}" --kind dir || true
      printf 'refusing to follow symlink during install: %s\n' "${target_tree}" >&2
      exit 1
    fi
    if ! safe_fs replace install "${target_tree}" "${backup_tree}" --src-kind dir --dst-must-not-exist; then
      safe_fs remove install "${stage_root}" --kind dir || true
      safe_fs remove install "${backup_tree}" --kind dir || true
      printf 'failed to preserve existing %s install: %s\n' "${label}" "${target_tree}" >&2
      exit 1
    fi
  fi

  reject_symlink_ancestors "${target_tree}" "install"
  if [[ -L "${parent}" || -L "${target_tree}" ]]; then
    if [[ -n "${backup_tree}" && -e "${backup_tree}" && ! -e "${target_tree}" ]]; then
      safe_fs replace install "${backup_tree}" "${target_tree}" --src-kind dir --dst-must-not-exist || true
    fi
    safe_fs remove install "${stage_root}" --kind dir || true
    printf 'refusing to follow symlink during install: %s\n' "${target_tree}" >&2
    exit 1
  fi
  if ! safe_fs replace install "${staged_tree}" "${target_tree}" --src-kind dir --dst-must-not-exist; then
    if [[ -n "${backup_tree}" && -e "${backup_tree}" && ! -e "${target_tree}" ]]; then
      safe_fs replace install "${backup_tree}" "${target_tree}" --src-kind dir --dst-must-not-exist || true
    fi
    safe_fs remove install "${stage_root}" --kind dir || true
    printf 'failed to activate staged %s install: %s\n' "${label}" "${target_tree}" >&2
    exit 1
  fi

  safe_fs remove install "${stage_root}" --kind dir || true
  if [[ -n "${backup_tree}" ]]; then
    safe_fs remove install "${backup_tree}" --kind dir || true
  fi
}

safe_fs mkdirs install "$(dirname "${applet_target}")"
safe_fs mkdirs install "${app_data}"
safe_fs mkdirs install "${app_data}/python"
safe_fs mkdirs install "${bin_dir}"
safe_fs mkdirs install "${man_dir}"
for target in "${applet_target}" "${app_data}" "${bin_dir}" "${man_dir}"; do
  reject_symlink_ancestors "${target}" "install"
done
install_tree_staged "${repo_dir}/files/${uuid}" "${applet_target}" "applet"
install_tree_staged "${repo_dir}/src/speed_of_cinnamon" "${app_data}/python/speed_of_cinnamon" "python package"

reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon.1" "man page source"
reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1" "man page source"

write_backend_wrapper

safe_fs copy-file install "${repo_dir}/docs/man/speed-of-cinnamon.1" "${man_dir}/speed-of-cinnamon.1" 0644
safe_fs copy-file install "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1" "${man_dir}/speed-of-cinnamon-alarms.1" 0644

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
