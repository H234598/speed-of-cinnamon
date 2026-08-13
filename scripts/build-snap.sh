#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:/var/lib/snapd/snap/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
snapcraft_base="${SNAPCRAFT_BASE:-core22}"
if [[ ! "${snapcraft_base}" =~ ^[a-z][a-z0-9-]*$ ]]; then
  printf 'invalid SNAPCRAFT_BASE value: %s\n' "${snapcraft_base}" >&2
  exit 1
fi
snapcraft_mode="${SNAPCRAFT_MODE:-auto}"
case "${snapcraft_mode}" in
  auto|destructive|lxd) ;;
  *)
    printf 'invalid SNAPCRAFT_MODE value: %s (expected auto, destructive, or lxd)\n' "${snapcraft_mode}" >&2
    exit 1
    ;;
esac

host_id=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  host_id="${ID:-}"
fi
if [[ "${snapcraft_mode}" == "auto" ]]; then
  if [[ "${host_id}" == "ubuntu" ]]; then
    snapcraft_mode="destructive"
  elif command -v -- lxc >/dev/null 2>&1 && lxc info >/dev/null 2>&1; then
    snapcraft_mode="lxd"
  else
    printf 'Snap build needs Ubuntu destructive mode or LXD on non-Ubuntu hosts; install LXD or set up an Ubuntu builder.\n' >&2
    exit 1
  fi
fi
if [[ "${snapcraft_mode}" == "destructive" && "${host_id}" != "ubuntu" ]]; then
  printf 'SNAPCRAFT_MODE=destructive is supported only on Ubuntu; use SNAPCRAFT_MODE=lxd on this host.\n' >&2
  exit 1
fi
if [[ "${snapcraft_mode}" == "lxd" ]]; then
  require_lxc="true"
  snapcraft_args=(--use-lxd)
else
  require_lxc="false"
  snapcraft_args=(--destructive-mode)
fi

require_cmd() {
  local tool=$1
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

require_regular_source_file() {
  local path=$1
  local label=$2
  local link_count

  if [[ ! -f "${path}" || -L "${path}" ]]; then
    printf '%s must be a regular file: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${path}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf '%s must not be hardlinked: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
}

activate_snap_output() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3

  python3 - "$lock_path" "$safe_fs" "$staging_path" "$final_path" <<'PY'
import os
import secrets
import stat
import subprocess
import sys

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe snap finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path = sys.argv[1:]
lock_parent = os.path.dirname(lock_path)
lock_name = os.path.basename(lock_path)


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _regular_file(path, label, *, required):
    path_stat = _lstat(path)
    if path_stat is None:
        if required:
            raise RuntimeError(f"{label} is missing: {path}")
        return None
    if stat.S_ISLNK(path_stat.st_mode):
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a regular file: {path}")
    if getattr(path_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{label} must not be hardlinked: {path}")
    return path_stat


def _run_safe_fs(*arguments):
    subprocess.run([sys.executable, safe_fs, *arguments], check=True)


def _identity(path_stat):
    return (path_stat.st_dev, path_stat.st_ino)


def _safe_fs_identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


def _new_backup_path():
    for _ in range(16):
        backup_path = f"{final_path}.{os.getpid()}.{secrets.token_hex(8)}.backup"
        if _lstat(backup_path) is None:
            return backup_path
    raise RuntimeError(f"could not allocate a free snap backup path for {final_path}")


def _rollback(*, backup_path, backup_attempted, backup_created, final_stat, final_identity, activation_attempted, staging_identity):
    if backup_attempted:
        backup_stat = _lstat(backup_path)
        if backup_stat is not None:
            if _identity(backup_stat) != final_identity:
                raise RuntimeError(f"refusing to restore changed snap backup: {backup_path}")
            current = _lstat(final_path)
            restore_guard = []
            if current is None:
                restore_guard.append("--dst-must-not-exist")
            elif activation_attempted and _identity(current) == staging_identity:
                restore_guard.extend(("--expected-dst-identity", _safe_fs_identity(current)))
            else:
                raise RuntimeError(f"refusing to restore changed snap output: {final_path}")
            _run_safe_fs(
                "replace",
                "build-snap",
                backup_path,
                final_path,
                "--src-kind",
                "file",
                "--expected-src-identity",
                _safe_fs_identity(backup_stat),
                *restore_guard,
            )
            return
        current = _lstat(final_path)
        if backup_created or current is None or _identity(current) != final_identity:
            raise RuntimeError(f"snap backup disappeared during rollback: {backup_path}")
        return
    if activation_attempted and final_stat is None:
        current = _lstat(final_path)
        if current is None:
            return
        if _identity(current) != staging_identity:
            raise RuntimeError(f"refusing to remove changed snap output during rollback: {final_path}")
        _run_safe_fs(
            "remove-leaf",
            "build-snap",
            final_path,
            "--expected-identity",
            _safe_fs_identity(current),
        )


if not lock_name:
    print(f"snap finalization lock path is invalid: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

parent_flags = os.O_RDONLY
if hasattr(os, "O_DIRECTORY"):
    parent_flags |= os.O_DIRECTORY
if hasattr(os, "O_NOFOLLOW"):
    parent_flags |= os.O_NOFOLLOW
try:
    parent_fd = os.open(lock_parent, parent_flags)
except OSError as exc:
    print(f"failed to open snap finalization lock parent safely: {lock_parent}: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    parent_stat = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_stat.st_mode):
        print(f"snap finalization lock parent must be a directory: {lock_parent}", file=sys.stderr)
        raise SystemExit(1)
    try:
        lock_stat = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        lock_stat = None
    if lock_stat is not None:
        if stat.S_ISLNK(lock_stat.st_mode):
            print(f"snap finalization lock must not be a symlink: {lock_path}", file=sys.stderr)
            raise SystemExit(1)
        if not stat.S_ISREG(lock_stat.st_mode):
            print(f"snap finalization lock must be a regular file: {lock_path}", file=sys.stderr)
            raise SystemExit(1)
        if getattr(lock_stat, "st_nlink", 1) != 1:
            print(f"snap finalization lock must not be hardlinked: {lock_path}", file=sys.stderr)
            raise SystemExit(1)

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    lock_fd = os.open(lock_name, flags, 0o600, dir_fd=parent_fd)
    with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        staging_stat = _regular_file(staging_path, "staged snap", required=True)
        final_stat = _regular_file(final_path, "existing snap output", required=False)
        staging_identity = _identity(staging_stat)
        final_identity = _identity(final_stat) if final_stat is not None else None
        final_fs_identity = _safe_fs_identity(final_stat) if final_stat is not None else None
        backup_path = _new_backup_path()
        backup_attempted = False
        backup_created = False
        activation_attempted = False
        try:
            if final_stat is not None:
                backup_attempted = True
                _run_safe_fs(
                    "replace",
                    "build-snap",
                    final_path,
                    backup_path,
                    "--src-kind",
                    "file",
                    "--dst-must-not-exist",
                    "--expected-src-identity",
                    final_fs_identity,
                )
                backup_created = True
            activation_attempted = True
            _run_safe_fs(
                "replace",
                "build-snap",
                staging_path,
                final_path,
                "--src-kind",
                "file",
                "--expected-src-identity",
                _safe_fs_identity(staging_stat),
                "--expected-dst-identity",
                "missing",
            )
        except BaseException as exc:
            try:
                _rollback(
                    backup_path=backup_path,
                    backup_attempted=backup_attempted,
                    backup_created=backup_created,
                    final_stat=final_stat,
                    final_identity=final_identity,
                    activation_attempted=activation_attempted,
                    staging_identity=staging_identity,
                )
            except BaseException as rollback_exc:
                exc.add_note(f"snap finalization rollback failed: {rollback_exc}")
            raise
        if backup_created:
            # A cleanup failure leaves the new snap active; the backup remains
            # available as a recovery copy instead of risking a partial rollback.
            _run_safe_fs("remove-leaf", "build-snap", backup_path)
finally:
    os.close(parent_fd)
PY
}

for tool in python3 snapcraft cp mktemp mkdir find realpath stat chmod grep sort basename; do
  require_cmd "${tool}"
done
if [[ "${require_lxc}" == "true" ]]; then
  require_cmd lxc
fi
snap_dir="${repo_dir}/snap"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

if ! snapcraft --version >/dev/null 2>&1; then
  printf 'snapcraft is installed but did not execute successfully.\n' >&2
  exit 1
fi

if [[ -L "${snap_dir}" ]]; then
  printf 'snap directory must not be a symlink: %s\n' "${snap_dir}" >&2
  exit 1
fi

if [[ ! -f "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest missing: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
if [[ -L "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest must not be a symlink: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
snapcraft_file="${snap_dir}/snapcraft.yaml"
require_regular_source_file "${snapcraft_file}" "snapcraft manifest"
require_regular_source_file "${safe_fs}" "safe local filesystem helper"

version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

repo_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${repo_tmp_root}" == /* ]]; then
  printf 'temporary root must be an absolute path: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if [[ -L "${repo_tmp_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if [[ ! -d "${repo_tmp_root}" || ! -w "${repo_tmp_root}" ]]; then
  printf 'temporary root is not a writable directory: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if ! repo_tmp_abs="$(realpath "${repo_tmp_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if [[ "${repo_tmp_abs}" == "${repo_dir}" || "${repo_tmp_abs}" == "${repo_dir}/"* ]]; then
  printf 'snap temporary root must be outside repository: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
repo_tmp_root="${repo_tmp_abs}"

snap_workspace="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-snap-tree-XXXXXX")"
if [[ -L "${snap_workspace}" ]]; then
  printf 'temporary snap workspace must not be a symlink: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! snap_workspace_abs="$(realpath "${snap_workspace}")"; then
  printf 'failed to resolve temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if [[ "${snap_workspace_abs}" != "${repo_tmp_root}/speed-of-cinnamon-snap-tree-"* ]]; then
  printf 'temporary snap workspace escaped temporary root: %s\n' "${snap_workspace}" >&2
  exit 1
fi
snap_workspace="${snap_workspace_abs}"
snapcraft_file_rendered="${snap_workspace}/snap/snapcraft.yaml"
snap_workspace_dist="${snap_workspace}/dist/snap"
tmp_output=""
tmp_output_identity=""
snap_workspace_identity=""
snap_stage_path=""
snap_stage_identity=""
cleanup_tmpdir() {
  if [[ -n "${tmp_output}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-snap "${tmp_output}" \
      --expected-identity "${tmp_output_identity}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${snap_stage_path}" && -n "${snap_stage_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-snap "${snap_stage_path}" \
      --expected-identity "${snap_stage_identity}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${snap_workspace}" && -n "${snap_workspace_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove build-snap "${snap_workspace}" --kind dir \
      --expected-identity "${snap_workspace_identity}" >/dev/null 2>&1 || true
  elif [[ -n "${snap_workspace}" ]]; then
    printf 'refusing snap workspace cleanup without verified identity: %s\n' "${snap_workspace}" >&2
  fi
}
trap cleanup_tmpdir EXIT

if ! snap_workspace_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_workspace}" --kind dir)"; then
  printf 'failed to capture temporary snap workspace identity: %s\n' "${snap_workspace}" >&2
  exit 1
fi

if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/src" "${snap_workspace}/src" "Python source tree"; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
remove_python_bytecode_from_snap_source() {
  local candidate candidate_identity

  while IFS= read -r -d '' candidate; do
    if ! candidate_identity="$("${safe_fs_cmd[@]}" identity build-snap "${candidate}" --kind dir)"; then
      printf 'failed to capture bytecode directory identity: %s\n' "${candidate}" >&2
      return 1
    fi
    if ! "${safe_fs_cmd[@]}" remove build-snap "${candidate}" --kind dir \
      --expected-identity "${candidate_identity}"; then
      printf 'failed to remove bytecode directory: %s\n' "${candidate}" >&2
      return 1
    fi
  done < <(find "${snap_workspace}/src" -type d -name "__pycache__" -print0)

  while IFS= read -r -d '' candidate; do
    if ! candidate_identity="$("${safe_fs_cmd[@]}" identity build-snap "${candidate}" --kind file)"; then
      printf 'failed to capture bytecode file identity: %s\n' "${candidate}" >&2
      return 1
    fi
    if ! "${safe_fs_cmd[@]}" remove-leaf build-snap "${candidate}" \
      --expected-identity "${candidate_identity}"; then
      printf 'failed to remove bytecode file: %s\n' "${candidate}" >&2
      return 1
    fi
  done < <(find "${snap_workspace}/src" -type f \( -name "*.pyc" -o -name "*.pyo" \) -print0)
}
if ! remove_python_bytecode_from_snap_source; then
  printf 'failed to remove stale Python bytecode from Snap source tree.\n' >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_workspace}/pyproject.toml" 0644; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${repo_dir}/README.md" "${snap_workspace}/README.md" 0644; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}" <<'PYCODE'
import os
import pathlib
import secrets
import sys

path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
version = sys.argv[3]
base = sys.argv[4]
text = path.read_text(encoding="utf-8")
out = []
replaced = False
base_replaced = False
for line in text.splitlines():
    if line.startswith("version:"):
        out.append(f"version: \"{version}\"")
        replaced = True
    elif line.startswith("base:"):
        out.append(f"base: {base}")
        base_replaced = True
    else:
        out.append(line)
if not replaced:
    raise SystemExit("snapcraft version field not found")
if not base_replaced:
    raise SystemExit("snapcraft base field not found")
payload = ("\n".join(out) + "\n").encode("utf-8")
parent_fd = os.open(output_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
tmp_name = f".{output_path.name}.{secrets.token_hex(8)}.tmp"
fd = -1
try:
    fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        fd = -1
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
    os.replace(tmp_name, output_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
    tmp_name = ""
finally:
    if fd >= 0:
        os.close(fd)
    if tmp_name:
        try:
            os.unlink(tmp_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            pass
    os.close(parent_fd)
PYCODE
snapcraft_mode="$(stat -c '%a' "${snapcraft_file}")"
if ! snapcraft_rendered_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snapcraft_file_rendered}" --kind file)"; then
  printf 'failed to capture rendered snapcraft manifest identity: %s\n' "${snapcraft_file_rendered}" >&2
  exit 1
fi
python3 - "${snapcraft_file_rendered}" "${snapcraft_mode}" "${snapcraft_rendered_identity}" <<'PY'
import os
import stat
import sys

path, mode_text, expected_identity = sys.argv[1:]
flags = os.O_RDWR
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
except OSError as exc:
    print(f"failed to open rendered snapcraft manifest safely: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode) or getattr(file_stat, "st_nlink", 1) != 1:
        print(f"rendered snapcraft manifest must be a private regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    actual_identity = f"{file_stat.st_dev}:{file_stat.st_ino}:{file_stat.st_mode}"
    if actual_identity != expected_identity:
        print(f"rendered snapcraft manifest changed before chmod: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.fchmod(fd, int(mode_text, 8))
    os.fsync(fd)
finally:
    os.close(fd)
PY

if ! (
  cd "${snap_workspace}"
  umask 022
  snapcraft pack "${snapcraft_args[@]}"
); then
  printf 'snapcraft build failed.\n' >&2
  exit 1
fi

if ! "${safe_fs_cmd[@]}" remove build-snap "${snap_workspace_dist}" --kind dir --expected-identity missing; then
  printf 'failed to prepare temporary snap output directory: %s\n' "${snap_workspace_dist}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-snap "${snap_workspace_dist}"

dist_parent="${repo_dir}/dist"
if [[ -L "${dist_parent}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_parent}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-snap "${dist_parent}"
if [[ -L "${dist_parent}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_parent}" >&2
  exit 1
fi
dist_dir="${dist_parent}/snap"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-snap "${dist_dir}"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi

tmp_output="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snap-output-XXXXXX")"
tmp_output_identity="$("${safe_fs_cmd[@]}" identity build-snap "${tmp_output}" --kind file)"

{
  find "${snap_workspace}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
  find "${snap_workspace_dist}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
} | sort -z > "${tmp_output}"

mapfile -d '' -t snap_files < "${tmp_output}"
if [[ ${#snap_files[@]} -ne 1 ]]; then
  printf 'expected exactly one new snap package, found %d\n' "${#snap_files[@]}" >&2
  exit 1
fi

for path in "${snap_files[@]}"; do
  if [[ -L "${path}" ]]; then
    printf 'snap package must not be a symlink: %s\n' "${path}" >&2
    exit 1
  fi
  absolute="$(realpath "${path}")"
  if [[ "${absolute}" != "${snap_workspace}/speed-of-cinnamon_${version}_"* &&
        "${absolute}" != "${snap_workspace_dist}/speed-of-cinnamon_${version}_"* ]]; then
    printf 'snap package path is unexpected: %s\n' "${path}" >&2
    exit 1
  fi
  filename="$(basename "${path}")"
  if [[ ! "${filename}" == "speed-of-cinnamon_${version}_"* ]]; then
    printf 'unexpected snap file name: %s\n' "${filename}" >&2
    exit 1
  fi
  if [[ ! -s "${path}" ]]; then
    printf 'snap package is empty: %s\n' "${path}" >&2
    exit 1
  fi
done

snap_filename="$(basename "${snap_files[0]}")"
snap_stage_path="${dist_dir}/.${snap_filename}.$(python3 -c 'import secrets; print(secrets.token_hex(16))').staging"
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${snap_files[0]}" "${snap_stage_path}" 0644 --dst-must-not-exist; then
  printf 'failed to copy built snap into output filesystem: %s\n' "${snap_files[0]}" >&2
  exit 1
fi
snap_stage_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_stage_path}" --kind file)"
output_path="${dist_dir}/${snap_filename}"
activate_snap_output "${dist_parent}/.build-snap.finalize.lock" "${snap_stage_path}" "${output_path}"
printf 'Built %s\n' "${output_path}" >&2
printf '%s\n' "${output_path}"
