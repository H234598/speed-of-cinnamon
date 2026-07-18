#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

readonly REQUIRED_TOOLS=(dirname find grep getent id mktemp realpath cut python3)

check_required_tools() {
  local missing_tool
  local tool

  for tool in "${REQUIRED_TOOLS[@]}"; do
    if ! command -v -- "${tool}" >/dev/null 2>&1; then
      missing_tool=1
      printf 'required tool missing: %s\n' "${tool}" >&2
    fi
  done

  if [[ "${missing_tool:-0}" != "0" ]]; then
    exit 1
  fi
}

check_required_tools

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
uuid="speed-of-cinnamon@H234598"

if [[ -z "${HOME:-}" ]]; then
  printf 'HOME must be set.\n' >&2
  exit 1
fi
app_data="${HOME}/.local/share/speed-of-cinnamon"
bin_dir="${HOME}/.local/bin"
applet_target="${HOME}/.local/share/cinnamon/applets/${uuid}"
man_dir="${HOME}/.local/share/man/man1"
account_home="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6 || true)"
if [[ "${SPEED_OF_CINNAMON_TEST_HOME:-0}" != "1" && ( -z "${account_home}" || "${HOME}" != "${account_home}" ) ]]; then
  printf 'Refusing to run with mismatched HOME: %s (expected %s).\n' "${HOME}" "${account_home}" >&2
  exit 1
fi

dbus_send_command=""
if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" && -n "${account_home}" && "${HOME}" == "${account_home}" ]]; then
  dbus_send_command="$(command -v -- dbus-send || true)"
  if [[ -z "${dbus_send_command}" ]]; then
    printf 'dbus-send not available; Cinnamon applet reload will be skipped.\n' >&2
  fi
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
  "${python3_path}" "${repo_dir}/scripts/safe-local-fs.py" "$@"
}
safe_fs_cmd=("${python3_path}" "${repo_dir}/scripts/safe-local-fs.py")

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

resolve_tmp_root() {
  local base="${TMPDIR:-/tmp}"

  if [[ ! "${base}" == /* ]]; then
    printf 'temporary root must be an absolute path: %s\n' "${base}" >&2
    exit 1
  fi
  if [[ -L "${base}" ]]; then
    printf 'temporary root must not be a symlink: %s\n' "${base}" >&2
    exit 1
  fi
  if [[ ! -d "${base}" || ! -w "${base}" ]]; then
    printf 'temporary root is not a writable directory: %s\n' "${base}" >&2
    exit 1
  fi
  if ! base="$(realpath "${base}")"; then
    printf 'failed to resolve temporary root: %s\n' "${base}" >&2
    exit 1
  fi
  mkdir -p "${base}"
  printf '%s\n' "${base}"
}

write_staging_dir() {
  local source_root="$1"
  local tmp_root="$2"

  local stage_root
  stage_root="${tmp_root}/speed-of-cinnamon-install-staging"
  safe_fs mkdirs install "${tmp_root}"
  safe_fs mkdirs install "${stage_root}"
  safe_fs mkdirs install "${stage_root}/speed-of-cinnamon/share"
  safe_fs mkdirs install "${stage_root}/speed-of-cinnamon/python"
  safe_fs mkdirs install "${stage_root}/speed-of-cinnamon/bin"
  safe_fs mkdirs install "${stage_root}/man/man1"

  if ! safe_fs install-tree install "${source_root}/files/${uuid}" "${stage_root}/speed-of-cinnamon/share/${uuid}" "applet"; then
    printf 'failed to stage applet installation files\n' >&2
    exit 1
  fi
  if ! safe_fs install-tree install "${source_root}/src/speed_of_cinnamon" "${stage_root}/speed-of-cinnamon/python/speed_of_cinnamon" "python package"; then
    printf 'failed to stage Python package\n' >&2
    exit 1
  fi

  if ! safe_fs write-wrapper install "${stage_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${app_data}/python" "${python3_path}"; then
    printf 'failed to stage backend wrapper\n' >&2
    exit 1
  fi

  if ! safe_fs copy-file install "${source_root}/docs/man/speed-of-cinnamon.1" "${stage_root}/man/man1/speed-of-cinnamon.1" 0644; then
    printf 'failed to stage man page\n' >&2
    exit 1
  fi
  if ! safe_fs copy-file install "${source_root}/docs/man/speed-of-cinnamon-alarms.1" "${stage_root}/man/man1/speed-of-cinnamon-alarms.1" 0644; then
    printf 'failed to stage man page\n' >&2
    exit 1
  fi

  printf '%s\n' "${stage_root}"
}

activate_staged() {
  local source="$1"
  local target="$2"
  local kind="$3"
  local label="$4"
  local backup_path="${rollback_root}/.${label}"
  local source_identity
  local existing_identity="missing"

  if [[ -L "${target}" ]]; then
    rollback_staged_items
    printf 'refusing to follow symlink during install: %s\n' "${target}" >&2
    exit 1
  fi

  if ! source_identity="$(safe_fs identity install "${source}" --kind "${kind}")"; then
    rollback_staged_items
    printf 'failed to inspect staged %s\n' "${label}" >&2
    exit 1
  fi

  if [[ -e "${target}" ]]; then
    if ! existing_identity="$(safe_fs identity install "${target}" --kind "${kind}")"; then
      rollback_staged_items
      printf 'failed to inspect existing %s\n' "${label}" >&2
      exit 1
    fi
  fi

  activated_targets+=("${target}")
  activated_backups+=("${backup_path}")
  activated_kinds+=("${kind}")
  activated_identities+=("${source_identity}")
  activated_original_identities+=("${existing_identity}")
  if [[ "${existing_identity}" != "missing" ]]; then
    activated_had_existing+=("1")
    if ! "${safe_fs_cmd[@]}" replace install "${target}" "${backup_path}" --src-kind "${kind}" \
      --expected-src-identity "${existing_identity}"; then
      rollback_staged_items
      printf 'failed to back up existing %s\n' "${label}" >&2
      exit 1
    fi
  else
    activated_had_existing+=("0")
  fi

  if ! "${safe_fs_cmd[@]}" replace install "${source}" "${target}" --src-kind "${kind}" \
    --expected-dst-identity missing; then
    rollback_staged_items
    printf 'failed to activate staged %s\n' "${label}" >&2
    exit 1
  fi
}

rollback_staged_items() {
  local target
  local backup
  local kind
  local expected_identity
  local original_identity
  local index

  if [[ "${rollback_attempted}" == "1" ]]; then
    return 0
  fi
  rollback_attempted=1

  for ((index = ${#activated_targets[@]} - 1; index >= 0; index--)); do
    target="${activated_targets[index]}"
    backup="${activated_backups[index]}"
    kind="${activated_kinds[index]}"
    expected_identity="${activated_identities[index]}"
    original_identity="${activated_original_identities[index]}"

    if [[ "${activated_had_existing[index]}" == "1" ]]; then
      if [[ -e "${backup}" || -L "${backup}" ]]; then
        if [[ -e "${target}" || -L "${target}" ]]; then
          if ! "${safe_fs_cmd[@]}" replace install "${backup}" "${target}" --src-kind "${kind}" \
            --expected-src-identity "${original_identity}" \
            --expected-dst-identity "${expected_identity}"; then
            rollback_failed=1
            printf 'rollback failed for %s\n' "${target}" >&2
          fi
        elif ! "${safe_fs_cmd[@]}" replace install "${backup}" "${target}" --src-kind "${kind}" \
          --expected-src-identity "${original_identity}" --dst-must-not-exist; then
          rollback_failed=1
          printf 'rollback failed for %s\n' "${target}" >&2
        fi
      elif [[ ! -e "${target}" && ! -L "${target}" ]]; then
        rollback_failed=1
        printf 'rollback failed for %s: backup is missing\n' "${target}" >&2
      fi
    else
      if [[ -e "${target}" || -L "${target}" ]]; then
        if ! "${safe_fs_cmd[@]}" remove-leaf install "${target}" \
          --expected-identity "${expected_identity}"; then
          rollback_failed=1
          printf 'rollback failed for %s\n' "${target}" >&2
        fi
      fi
    fi
  done
}

install_workspace_cleanup() {
  if [[ "${rollback_failed}" == "1" ]]; then
    printf 'preserving install recovery workspace: %s\n' "${staged_workspace}" >&2
    return 0
  fi
  if [[ -n "${staged_workspace}" && -e "${staged_workspace}" ]]; then
    if ! safe_fs remove install "${staged_workspace}" --kind dir; then
      printf 'failed to clean install staging workspace: %s\n' "${staged_workspace}" >&2
    fi
  fi
}

install_exit_cleanup() {
  local exit_code="$?"

  if [[ "${install_complete}" != "1" ]]; then
    rollback_staged_items
  fi
  install_workspace_cleanup
  return "${exit_code}"
}

validate_staged_workspace() {
  local app_data_real
  local staged_real

  if [[ -z "${staged_workspace}" || "${staged_workspace}" != "${app_data}/install-stage-"* ]]; then
    printf 'install staging workspace is outside app data: %s\n' "${staged_workspace}" >&2
    exit 1
  fi
  if [[ -L "${staged_workspace}" || ! -d "${staged_workspace}" ]]; then
    printf 'install staging workspace is invalid: %s\n' "${staged_workspace}" >&2
    exit 1
  fi
  app_data_real="$(realpath "${app_data}")"
  staged_real="$(realpath "${staged_workspace}")"
  if [[ "${staged_real}" != "${app_data_real}/install-stage-"* ]]; then
    printf 'install staging workspace resolved outside app data: %s\n' "${staged_workspace}" >&2
    exit 1
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

reject_unsafe_tree "${repo_dir}/files/${uuid}" "applet source tree"
reject_unsafe_tree "${repo_dir}/src/speed_of_cinnamon" "python package source tree"
reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon.1" "man page source"
reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1" "man page source"

resolve_tmp_root >/dev/null
activated_targets=()
activated_backups=()
activated_kinds=()
activated_identities=()
activated_original_identities=()
activated_had_existing=()
rollback_attempted=0
rollback_failed=0
install_complete=0
staged_workspace="$(mktemp -d "${app_data}/install-stage-XXXXXX")"
trap install_exit_cleanup EXIT
validate_staged_workspace
rollback_root="${staged_workspace}/rollback"
safe_fs mkdirs install "${rollback_root}"

staging_root="$(write_staging_dir "${repo_dir}" "${staged_workspace}")"

wrapper_target="${bin_dir}/speed-of-cinnamon"

activate_staged "${staging_root}/speed-of-cinnamon/share/${uuid}" "${applet_target}" "dir" "applet"
activate_staged "${staging_root}/speed-of-cinnamon/python/speed_of_cinnamon" "${app_data}/python/speed_of_cinnamon" "dir" "python-package"
activate_staged "${staging_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${wrapper_target}" "file" "wrapper"
activate_staged "${staging_root}/man/man1/speed-of-cinnamon.1" "${man_dir}/speed-of-cinnamon.1" "file" "man-page"
activate_staged "${staging_root}/man/man1/speed-of-cinnamon-alarms.1" "${man_dir}/speed-of-cinnamon-alarms.1" "file" "man-page-alarms"
install_complete=1

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
if [[ -n "${dbus_send_command}" ]]; then
    if "${dbus_send_command}" --session --dest=org.Cinnamon.LookingGlass --type=method_call \
        /org/Cinnamon/LookingGlass org.Cinnamon.LookingGlass.ReloadExtension \
        string:"${uuid}" string:'APPLET' >/dev/null 2>&1; then
        printf 'Reloaded Cinnamon applet %s\n' "${uuid}"
    else
        printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'
    fi
else
    printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'
fi
