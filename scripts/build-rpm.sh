#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

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

activate_with_finalize_lock() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3

  "${python_bin}" - "$lock_path" "$safe_fs" "$staging_path" "$final_path" <<'PY'
import os
import subprocess
import stat
import sys

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe RPM finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path = sys.argv[1:]
lock_parent = os.path.dirname(lock_path)
lock_name = os.path.basename(lock_path)

if not lock_name:
    print(f"finalization lock path is invalid: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

parent_flags = os.O_RDONLY
if hasattr(os, "O_DIRECTORY"):
    parent_flags |= os.O_DIRECTORY
if hasattr(os, "O_NOFOLLOW"):
    parent_flags |= os.O_NOFOLLOW
try:
    parent_fd = os.open(lock_parent, parent_flags)
except OSError as exc:
    print(f"failed to open finalization lock parent safely: {lock_parent}: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    parent_stat = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_stat.st_mode):
        print(f"finalization lock parent must be a directory: {lock_parent}", file=sys.stderr)
        raise SystemExit(1)
    try:
        lock_stat = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        lock_stat = None
    if lock_stat is not None:
        if stat.S_ISLNK(lock_stat.st_mode):
            print(f"finalization lock must not be a symlink: {lock_path}", file=sys.stderr)
            raise SystemExit(1)
        if not stat.S_ISREG(lock_stat.st_mode):
            print(f"finalization lock must be a regular file: {lock_path}", file=sys.stderr)
            raise SystemExit(1)
        if getattr(lock_stat, "st_nlink", 1) != 1:
            print(f"finalization lock must not be hardlinked: {lock_path}", file=sys.stderr)
            raise SystemExit(1)

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    lock_fd = os.open(lock_name, flags, 0o600, dir_fd=parent_fd)
    with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        subprocess.run(
            [sys.executable, safe_fs, "install-tree", "build-rpm", staging_path, final_path, "RPM build directory"],
            check=True,
        )
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(parent_fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-rpm finalization descriptor cleanup failed")
        else:
            raise SystemExit("build-rpm finalization descriptor cleanup failed") from cleanup_error
PY
}

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
if ! repo_tmp_root="$(realpath "${repo_tmp_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
rpmbuild_tmpdir="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-rpm-tmp-XXXXXX")"
if [[ -L "${rpmbuild_tmpdir}" ]]; then
  printf 'temporary RPM workspace must not be a symlink: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi
if ! rpmbuild_tmpdir_abs="$(realpath "${rpmbuild_tmpdir}")"; then
  printf 'failed to resolve temporary RPM workspace: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi
if [[ "${rpmbuild_tmpdir_abs}" != "${repo_tmp_root}/speed-of-cinnamon-rpm-tmp-"* ]]; then
  printf 'temporary RPM workspace escaped temporary root: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi
rpmbuild_tmpdir="${rpmbuild_tmpdir_abs}"
stage_topdir=""
rpmbuild_tmpdir_identity=""
stage_topdir_identity=""
cleanup_tmpdir() {
  if [[ -n "${stage_topdir}" && -n "${stage_topdir_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove build-rpm "${stage_topdir}" --kind dir \
      --expected-identity "${stage_topdir_identity}" >/dev/null 2>&1 || true
  elif [[ -n "${stage_topdir}" ]]; then
    printf 'refusing RPM stage cleanup without verified identity: %s\n' "${stage_topdir}" >&2
  fi
  if [[ -n "${rpmbuild_tmpdir}" && -n "${rpmbuild_tmpdir_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove build-rpm "${rpmbuild_tmpdir}" --kind dir \
      --expected-identity "${rpmbuild_tmpdir_identity}" >/dev/null 2>&1 || true
  elif [[ -n "${rpmbuild_tmpdir}" ]]; then
    printf 'refusing RPM workspace cleanup without verified identity: %s\n' "${rpmbuild_tmpdir}" >&2
  fi
}
trap cleanup_tmpdir EXIT

if ! rpmbuild_tmpdir_identity="$("${safe_fs_cmd[@]}" identity build-rpm "${rpmbuild_tmpdir}" --kind dir)"; then
  printf 'failed to capture temporary RPM workspace identity: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi

profile="${1:-fedora}"
case "${profile}" in
  fedora|generic)
    ;;
  *)
    printf 'unknown rpm profile: %s\n' "${profile}" >&2
    exit 1
    ;;
esac

require_cmd rpmbuild
require_cmd python3
require_cmd realpath

python_bin="$(command -v -- python3)"
if [[ ! -x "${repo_dir}/scripts/build-dist.sh" ]]; then
  printf 'build-dist script is missing: %s\n' "${repo_dir}/scripts/build-dist.sh" >&2
  exit 1
fi
require_regular_source_file "${safe_fs}" "safe local filesystem helper"
read -r tarball < <("${repo_dir}/scripts/build-dist.sh")
if [[ -L "${tarball}" ]]; then
  printf 'build-dist output must not be a symlink: %s\n' "${tarball}" >&2
  exit 1
fi
tarball="$(realpath "${tarball}")"
if [[ ! -f "${tarball}" || ! "${tarball}" == "${repo_dir}/dist/"*".tar.gz" ]]; then
  printf 'build-dist output is invalid: %s\n' "${tarball}" >&2
  exit 1
fi

if [[ "${profile}" == "generic" ]]; then
  final_topdir="${repo_dir}/dist/rpmbuild-generic"
  spec_source="${repo_dir}/packaging/speed-of-cinnamon-generic.spec"
else
  final_topdir="${repo_dir}/dist/rpmbuild"
  spec_source="${repo_dir}/packaging/speed-of-cinnamon.spec"
fi
if [[ -L "${final_topdir}" ]]; then
  printf 'RPM build directory must not be a symlink: %s\n' "${final_topdir}" >&2
  exit 1
fi
if [[ -L "${spec_source}" ]]; then
  printf 'spec source file must not be a symlink: %s\n' "${spec_source}" >&2
  exit 1
fi
dist_dir="$(dirname "${final_topdir}")"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist parent directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-rpm "${dist_dir}"
dist_finalize_lock="${dist_dir}/.build-rpm.finalize.lock"
stage_topdir="$(mktemp -d "${rpmbuild_tmpdir}/.$(basename "${final_topdir}").stage.XXXXXX")"
if [[ -L "${stage_topdir}" ]]; then
  printf 'temporary RPM stage directory must not be a symlink: %s\n' "${stage_topdir}" >&2
  exit 1
fi
if ! stage_topdir_abs="$(realpath "${stage_topdir}")"; then
  printf 'failed to resolve temporary RPM stage directory: %s\n' "${stage_topdir}" >&2
  exit 1
fi
if [[ "${stage_topdir_abs}" != "${rpmbuild_tmpdir}/."*".stage."* ]]; then
  printf 'temporary RPM stage directory escaped temporary workspace: %s\n' "${stage_topdir}" >&2
  exit 1
fi
stage_topdir="${stage_topdir_abs}"
if ! stage_topdir_identity="$("${safe_fs_cmd[@]}" identity build-rpm "${stage_topdir}" --kind dir)"; then
  printf 'failed to capture temporary RPM stage identity: %s\n' "${stage_topdir}" >&2
  exit 1
fi
spec_file="${stage_topdir}/SPECS/speed-of-cinnamon.spec"

if [[ ! -f "${spec_source}" ]]; then
  printf 'spec source missing: %s\n' "${spec_source}" >&2
  exit 1
fi
require_regular_source_file "${tarball}" "tarball source"
require_regular_source_file "${spec_source}" "spec source"

for rpm_stage_dir in BUILD BUILDROOT RPMS SOURCES SPECS SRPMS; do
  "${safe_fs_cmd[@]}" mkdirs build-rpm "${stage_topdir}/${rpm_stage_dir}"
done
if ! "${safe_fs_cmd[@]}" copy-file build-rpm "${tarball}" "${stage_topdir}/SOURCES/$(basename "${tarball}")" 0644; then
  printf 'failed to copy tarball source into RPM staging: %s\n' "${tarball}" >&2
  exit 1
fi

version="$(
  "${python_bin}" - "${repo_dir}" <<'PY'
import sys
from pathlib import Path
import tomllib

repo_dir = Path(sys.argv[1])
MAX_PROJECT_METADATA_BYTES = 1 << 20
with (repo_dir / "pyproject.toml").open("rb") as handle:
    payload = handle.read(MAX_PROJECT_METADATA_BYTES + 1)
if len(payload) > MAX_PROJECT_METADATA_BYTES:
    raise SystemExit("pyproject.toml is too large")
data = tomllib.loads(payload.decode("utf-8"))
print(data["project"]["version"])
PY
)"
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid version from pyproject.toml: %s\n' "${version}" >&2
  exit 1
fi

if ! "${safe_fs_cmd[@]}" copy-file build-rpm "${spec_source}" "${spec_file}" 0644; then
  printf 'failed to copy spec source into RPM staging: %s\n' "${spec_source}" >&2
  exit 1
fi
"${python_bin}" - <<'PY' "${spec_file}" "${version}"
import os
from pathlib import Path
import re
import secrets
import sys

spec_path = Path(sys.argv[1])
version = sys.argv[2]
MAX_RPM_SPEC_BYTES = 1 << 20
with spec_path.open("rb") as handle:
    payload = handle.read(MAX_RPM_SPEC_BYTES + 1)
if len(payload) > MAX_RPM_SPEC_BYTES:
    raise SystemExit("RPM spec is too large")
text = payload.decode("utf-8")
text = re.sub(r"^Version:\s*.*$", f"Version:        {version}", text, flags=re.M)
payload = text.encode("utf-8")
parent_fd = os.open(spec_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
tmp_name = f".{spec_path.name}.{secrets.token_hex(8)}.tmp"
fd = -1
try:
    fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        fd = -1
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
    os.replace(tmp_name, spec_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
    tmp_name = ""
finally:
    primary_error = sys.exc_info()[1]
    cleanup_errors = []
    if fd >= 0:
        try:
            os.close(fd)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if tmp_name:
        try:
            os.unlink(tmp_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    try:
        os.close(parent_fd)
    except BaseException as cleanup_error:
        cleanup_errors.append(cleanup_error)
    if cleanup_errors:
        if primary_error is not None:
            primary_error.add_note("build-rpm spec descriptor cleanup failed")
        else:
            raise SystemExit("build-rpm spec descriptor cleanup failed") from cleanup_errors[0]
PY

rpmbuild \
  --nodeps \
  --define "_topdir ${stage_topdir}" \
  --define "_sourcedir ${stage_topdir}/SOURCES" \
  --define "_specdir ${repo_dir}/packaging" \
  --define "_smp_build_ncpus 1" \
  --define "_tmppath ${rpmbuild_tmpdir}" \
  --define "__python3 ${python_bin}" \
  --define "py_auto_byte_compile 0" \
  --define "__brp_python_bytecompile %{nil}" \
  --define "__brp_python_hardlink %{nil}" \
  -ba "${spec_file}"

if ! activate_with_finalize_lock "${dist_finalize_lock}" "${stage_topdir}" "${final_topdir}"; then
  printf 'failed to activate RPM build directory: %s\n' "${final_topdir}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" remove build-rpm "${stage_topdir}" --kind dir \
  --expected-identity "${stage_topdir_identity}"
stage_topdir=""
stage_topdir_identity=""

find "${final_topdir}/RPMS" "${final_topdir}/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) -print | sort
