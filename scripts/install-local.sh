#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
readonly MAX_TREE_FILE_BYTES=536870912
readonly MAX_PYTHON_PACKAGE_ENTRIES=4096
export PATH="${TRUSTED_COMMAND_PATH}"

readonly REQUIRED_TOOLS=(dirname find flock grep getent id mktemp realpath cut python3)

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
timeout_command=""
if [[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" && -n "${account_home}" && "${HOME}" == "${account_home}" ]]; then
  dbus_send_command="$(command -v -- dbus-send || true)"
  timeout_command="$(command -v -- timeout || true)"
  if [[ -z "${dbus_send_command}" ]]; then
    printf 'dbus-send not available; Cinnamon applet reload will be skipped.\n' >&2
  fi
  if [[ -z "${timeout_command}" ]]; then
    printf 'timeout not available; Cinnamon applet reload will be skipped.\n' >&2
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
flock_command="$(command -v -- flock)"

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

assert_private_chain() {
  local path="$1"
  local action="$2"
  if ! safe_fs assert-private-chain "${action}" "${path}" --allow-missing; then
    printf 'refusing unsafe persistent directory chain during %s: %s\n' "${action}" "${path}" >&2
    exit 1
  fi
}

assert_private_chain "${HOME}" "install"

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
  printf '%s\n' "${base}"
}

compile_staged_python() {
  local stage_python_root="$1"
  local package_root="${stage_python_root}/speed_of_cinnamon"

  if ! "${python3_path}" -I -m compileall -q -f \
    --invalidation-mode checked-hash \
    -s "${stage_python_root}" -p "${app_data}/python" \
    "${package_root}"; then
    printf 'failed to compile staged Python package\n' >&2
    exit 1
  fi

  # Generated bytecode must be validated before manifests or activation.
  if ! "${python3_path}" -I -B - "${package_root}" \
    "${MAX_PYTHON_PACKAGE_ENTRIES}" "${MAX_TREE_FILE_BYTES}" <<'PY'
import importlib.util
import os
import stat
import sys


def fail(message):
    print(f"staged Python bytecode validation failed: {message}", file=sys.stderr)
    raise SystemExit(1)


def display(path):
    return os.path.relpath(path, package_root)


def require_directory(path, *, cache=False):
    try:
        info = os.lstat(path)
    except OSError as exc:
        fail(f"cannot inspect directory {display(path)}: {exc}")
    if not stat.S_ISDIR(info.st_mode):
        fail(f"non-directory or symlink found at {display(path)}")
    if info.st_uid != owner_uid:
        fail(f"directory owner mismatch at {display(path)}")
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o022:
        fail(f"writable directory mode at {display(path)}")
    if cache and mode != 0o700:
        fail(f"bytecode directory mode is not 0700 at {display(path)}")
    return info


def require_regular(info, path, *, bytecode=False):
    if not stat.S_ISREG(info.st_mode):
        fail(f"non-regular file or symlink found at {display(path)}")
    if info.st_nlink != 1:
        fail(f"hardlinked file found at {display(path)}")
    if info.st_uid != owner_uid:
        fail(f"file owner mismatch at {display(path)}")
    mode = stat.S_IMODE(info.st_mode)
    if bytecode:
        if mode != 0o600:
            fail(f"bytecode mode is not 0600 at {display(path)}")
    elif mode & 0o022:
        fail(f"writable file mode at {display(path)}")


def stable_key(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_source(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open source {display(path)}: {exc}")
    try:
        before = os.fstat(descriptor)
        require_regular(before, path)
        if before.st_size > max_bytes:
            fail(f"source exceeds byte budget at {display(path)}")
        chunks = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(65536, max_bytes - size + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                fail(f"source exceeds byte budget at {display(path)}")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if stable_key(before) != stable_key(after) or size != before.st_size:
        fail(f"source changed while hashing at {display(path)}")
    return b"".join(chunks)


def read_pyc_header(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        fail(f"cannot open bytecode {display(path)}: {exc}")
    try:
        before = os.fstat(descriptor)
        require_regular(before, path, bytecode=True)
        if before.st_size < 16 or before.st_size > max_bytes:
            fail(f"invalid bytecode size at {display(path)}")
        header = os.read(descriptor, 16)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if stable_key(before) != stable_key(after) or len(header) != 16:
        fail(f"bytecode changed while reading at {display(path)}")
    return header


if len(sys.argv) != 4:
    fail("invalid validator arguments")
package_root = os.path.abspath(sys.argv[1])
try:
    max_entries = int(sys.argv[2])
    max_bytes = int(sys.argv[3])
except ValueError:
    fail("invalid validator budgets")
if max_entries < 1 or max_bytes < 16:
    fail("invalid validator budgets")

owner_uid = os.getuid()
require_directory(package_root)
sources = set()
bytecode_files = set()
cache_directories = set()
entry_count = 0
total_bytes = 0

for current, directories, files in os.walk(package_root, topdown=True, followlinks=False):
    in_cache = os.path.basename(current) == "__pycache__"
    require_directory(current, cache=in_cache)
    if in_cache and directories:
        fail(f"nested directory in bytecode cache at {display(current)}")
    if in_cache:
        cache_directories.add(os.path.normpath(current))
    for name in directories:
        entry_count += 1
        if entry_count > max_entries:
            fail("package entry budget exceeded")
        require_directory(os.path.join(current, name), cache=name == "__pycache__")
    for name in files:
        entry_count += 1
        if entry_count > max_entries:
            fail("package entry budget exceeded")
        path = os.path.normpath(os.path.join(current, name))
        try:
            info = os.lstat(path)
        except OSError as exc:
            fail(f"cannot inspect file {display(path)}: {exc}")
        require_regular(info, path, bytecode=name.endswith(".pyc"))
        total_bytes += info.st_size
        if total_bytes > max_bytes:
            fail("package byte budget exceeded")
        if name.endswith(".py") and not in_cache:
            sources.add(path)
        elif name.endswith(".pyc"):
            bytecode_files.add(path)
        elif name.endswith(".pyo") or in_cache:
            fail(f"unexpected bytecode cache entry at {display(path)}")

if not sources:
    fail("package contains no Python sources")
expected = {}
for source in sources:
    pyc_path = os.path.normpath(importlib.util.cache_from_source(source))
    if os.path.commonpath((package_root, pyc_path)) != package_root:
        fail(f"bytecode path escapes package for {display(source)}")
    if pyc_path in expected:
        fail("multiple sources map to one bytecode path")
    expected[pyc_path] = source

missing = set(expected) - bytecode_files
extra = bytecode_files - set(expected)
if missing:
    fail("missing bytecode for " + ", ".join(display(path) for path in sorted(missing)[:3]))
if extra:
    fail("unexpected bytecode " + ", ".join(display(path) for path in sorted(extra)[:3]))
expected_cache_directories = {os.path.dirname(path) for path in expected}
if cache_directories != expected_cache_directories:
    fail("bytecode cache directory coverage mismatch")

hashed_source_bytes = 0
for pyc_path, source_path in sorted(expected.items()):
    source_bytes = read_source(source_path)
    hashed_source_bytes += len(source_bytes)
    if hashed_source_bytes > max_bytes:
        fail("source hash byte budget exceeded")
    header = read_pyc_header(pyc_path)
    if header[:4] != importlib.util.MAGIC_NUMBER:
        fail(f"Python magic mismatch at {display(pyc_path)}")
    if int.from_bytes(header[4:8], "little") != 3:
        fail(f"bytecode flags are not checked-hash at {display(pyc_path)}")
    if header[8:16] != importlib.util.source_hash(source_bytes):
        fail(f"source hash mismatch at {display(pyc_path)}")
PY
  then
    printf 'staged Python bytecode verification failed\n' >&2
    exit 1
  fi
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

  if ! safe_fs install-tree install "${source_root}/files/${uuid}" "${stage_root}/speed-of-cinnamon/share/${uuid}" "applet" \
    --exclude-name __pycache__; then
    printf 'failed to stage applet installation files\n' >&2
    exit 1
  fi
  if ! safe_fs install-tree install "${source_root}/src/speed_of_cinnamon" "${stage_root}/speed-of-cinnamon/python/speed_of_cinnamon" "python package" \
    --exclude-name __pycache__; then
    printf 'failed to stage Python package\n' >&2
    exit 1
  fi
  compile_staged_python "${stage_root}/speed-of-cinnamon/python"

  if ! safe_fs write-wrapper install "${stage_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${app_data}/python" "${python3_path}"; then
    printf 'failed to stage backend wrapper\n' >&2
    exit 1
  fi

  if ! safe_fs copy-file install "${source_root}/docs/man/speed-of-cinnamon.1" \
    "${stage_root}/man/man1/speed-of-cinnamon.1" 0644 --max-bytes "${MAX_TREE_FILE_BYTES}"; then
    printf 'failed to stage man page\n' >&2
    exit 1
  fi
  if ! safe_fs copy-file install "${source_root}/docs/man/speed-of-cinnamon-alarms.1" \
    "${stage_root}/man/man1/speed-of-cinnamon-alarms.1" 0644 --max-bytes "${MAX_TREE_FILE_BYTES}"; then
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
          if ! "${safe_fs_cmd[@]}" exchange install "${backup}" "${target}" --kind "${kind}" \
            --expected-source-identity "${original_identity}" \
            --expected-target-identity "${expected_identity}"; then
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
      else
        rollback_failed=1
        printf 'rollback failed for %s: backup is missing and target remains\n' "${target}" >&2
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
  if [[ -z "${staged_workspace}" ]]; then
    return 0
  fi
  if [[ -n "${staged_workspace_identity}" ]]; then
    if ! safe_fs remove install "${staged_workspace}" --kind dir \
      --expected-identity "${staged_workspace_identity}"; then
      printf 'failed to clean install staging workspace: %s\n' "${staged_workspace}" >&2
      return 1
    fi
  else
    printf 'refusing install cleanup without verified identity: %s\n' "${staged_workspace}" >&2
    return 1
  fi
}

release_install_lock() {
  if (( install_lock_fd < 0 )); then
    return 0
  fi
  if ! exec {install_lock_fd}<&-; then
    printf 'failed to release install lock\n' >&2
    return 1
  fi
  install_lock_fd=-1
}

install_exit_cleanup() {
  local exit_code="$?"

  if [[ "${install_complete}" != "1" ]]; then
    rollback_staged_items
  fi
  if ! install_workspace_cleanup; then
    exit_code=1
  fi
  if ! release_install_lock; then
    exit_code=1
  fi
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

set_staged_phase() {
  local phase="$1"
  if ! safe_fs phase-set install "${staged_workspace}" "${phase}"; then
    rollback_failed=1
    printf 'failed to journal install phase %s; preserving recovery workspace: %s\n' \
      "${phase}" "${staged_workspace}" >&2
    exit 1
  fi
}

snapshot_staging_target() {
  local source="$1"
  local manifest="$2"
  local label="$3"
  local digest
  shift 3

  if ! digest="$(safe_fs snapshot-tree install "${source}" "${manifest}" "${label}" "$@")"; then
    printf 'failed to snapshot staged %s\n' "${label}" >&2
    exit 1
  fi
  if [[ ! "${digest}" =~ ^[0-9a-f]{64}$ ]]; then
    printf 'invalid staged %s snapshot digest\n' "${label}" >&2
    exit 1
  fi
  printf '%s\n' "${digest}"
}

verify_installed_target() {
  local manifest="$1"
  local digest="$2"
  local target="$3"
  local label="$4"

  if ! safe_fs verify-tree install "${manifest}" "${digest}" "${target}" "${label}"; then
    printf 'installed %s verification failed\n' "${label}" >&2
    exit 1
  fi
}

verify_installed_targets() {
  verify_installed_target "${staging_applet_manifest}" "${staging_applet_digest}" \
    "${applet_target}" "applet"
  verify_installed_target "${staging_python_manifest}" "${staging_python_digest}" \
    "${app_data}/python/speed_of_cinnamon" "python package"
  verify_installed_target "${staging_wrapper_manifest}" "${staging_wrapper_digest}" \
    "${wrapper_target}" "wrapper"
  verify_installed_target "${staging_man_manifest}" "${staging_man_digest}" \
    "${man_dir}/speed-of-cinnamon.1" "man page"
  verify_installed_target "${staging_alarms_manifest}" "${staging_alarms_digest}" \
    "${man_dir}/speed-of-cinnamon-alarms.1" "alarms man page"
}

for target in "${applet_target}" "${app_data}" "${bin_dir}" "${man_dir}"; do
  assert_private_chain "${target}" "install"
done
safe_fs mkdirs install "$(dirname "${applet_target}")"
safe_fs mkdirs install "${app_data}"
safe_fs mkdirs install "${app_data}/python"
safe_fs mkdirs install "${bin_dir}"
safe_fs mkdirs install "${man_dir}"
for target in "${applet_target}" "${app_data}" "${bin_dir}" "${man_dir}"; do
  assert_private_chain "${target}" "install"
done

reject_unsafe_tree "${repo_dir}/files/${uuid}" "applet source tree"
reject_unsafe_tree "${repo_dir}/src/speed_of_cinnamon" "python package source tree"
reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon.1" "man page source"
reject_unsafe_file "${repo_dir}/docs/man/speed-of-cinnamon-alarms.1" "man page source"

# Validate TMPDIR for helper subprocesses; keep install staging under app_data so rollback survives /tmp cleanup.
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
install_lock_fd=-1
staged_workspace=""
staged_workspace_identity=""
trap install_exit_cleanup EXIT
# This kernel-held FD serializes installers through final verification and install_complete.
# Same-user mutations after FD release are runtime drift outside this postcheck boundary.
if ! exec {install_lock_fd}<"${app_data}"; then
  printf 'failed to open private install lock directory\n' >&2
  exit 1
fi
if ! "${flock_command}" -n "${install_lock_fd}"; then
  exec {install_lock_fd}<&- || true
  install_lock_fd=-1
  printf 'another local install is active or the install lock is unsafe\n' >&2
  exit 1
fi
if ! safe_fs cleanup-install-stages install "${app_data}"; then
  exit 1
fi
staged_workspace="$(mktemp -d "${app_data}/install-stage-XXXXXX")"
if ! staged_workspace_identity="$(safe_fs identity install "${staged_workspace}" --kind dir)"; then
  printf 'failed to capture install staging workspace identity: %s\n' "${staged_workspace}" >&2
  exit 1
fi
validate_staged_workspace
set_staged_phase pre-activation
rollback_root="${staged_workspace}/rollback"
safe_fs mkdirs install "${rollback_root}"

staging_root="$(write_staging_dir "${repo_dir}" "${staged_workspace}")"

wrapper_target="${bin_dir}/speed-of-cinnamon"
staging_expectation_root="${staged_workspace}/expectations"
safe_fs mkdirs install "${staging_expectation_root}"
staging_applet_manifest="${staging_expectation_root}/applet.manifest"
staging_python_manifest="${staging_expectation_root}/python.manifest"
staging_wrapper_manifest="${staging_expectation_root}/wrapper.manifest"
staging_man_manifest="${staging_expectation_root}/man.manifest"
staging_alarms_manifest="${staging_expectation_root}/alarms.manifest"
staging_applet_digest="$(snapshot_staging_target \
  "${staging_root}/speed-of-cinnamon/share/${uuid}" "${staging_applet_manifest}" "applet")"
staging_python_digest="$(snapshot_staging_target \
  "${staging_root}/speed-of-cinnamon/python/speed_of_cinnamon" "${staging_python_manifest}" "python package")"
staging_wrapper_digest="$(snapshot_staging_target \
  "${staging_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${staging_wrapper_manifest}" "wrapper")"
staging_man_digest="$(snapshot_staging_target \
  "${staging_root}/man/man1/speed-of-cinnamon.1" "${staging_man_manifest}" "man page")"
staging_alarms_digest="$(snapshot_staging_target \
  "${staging_root}/man/man1/speed-of-cinnamon-alarms.1" "${staging_alarms_manifest}" "alarms man page")"

set_staged_phase recovery-required
activate_staged "${staging_root}/speed-of-cinnamon/share/${uuid}" "${applet_target}" "dir" "applet"
activate_staged "${staging_root}/speed-of-cinnamon/python/speed_of_cinnamon" "${app_data}/python/speed_of_cinnamon" "dir" "python-package"
activate_staged "${staging_root}/speed-of-cinnamon/bin/speed-of-cinnamon" "${wrapper_target}" "file" "wrapper"
activate_staged "${staging_root}/man/man1/speed-of-cinnamon.1" "${man_dir}/speed-of-cinnamon.1" "file" "man-page"
activate_staged "${staging_root}/man/man1/speed-of-cinnamon-alarms.1" "${man_dir}/speed-of-cinnamon-alarms.1" "file" "man-page-alarms"

verify_installed_targets
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
if [[ -n "${dbus_send_command}" && -n "${timeout_command}" ]]; then
    if "${timeout_command}" --signal=TERM --kill-after=2s 10s \
        "${dbus_send_command}" --session --reply-timeout=10000 \
        --dest=org.Cinnamon.LookingGlass --type=method_call \
        /org/Cinnamon/LookingGlass org.Cinnamon.LookingGlass.ReloadExtension \
        string:"${uuid}" string:'APPLET' >/dev/null 2>&1; then
        printf 'Reloaded Cinnamon applet %s\n' "${uuid}"
    else
        printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'
    fi
else
    printf 'Reload Cinnamon with Alt+F2, r, Enter if the applet list does not refresh.\n'
fi
