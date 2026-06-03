#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
import sys

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe RPM finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path = sys.argv[1:]
lock_parent = os.path.dirname(lock_path)

if os.path.islink(lock_parent):
    print(f"finalization lock parent must not be a symlink: {lock_parent}", file=sys.stderr)
    raise SystemExit(1)
if os.path.islink(lock_path):
    print(f"finalization lock must not be a symlink: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

flags = os.O_CREAT | os.O_RDWR
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW

lock_fd = os.open(lock_path, flags, 0o600)
with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    subprocess.run(
        [sys.executable, safe_fs, "install-tree", "build-rpm", staging_path, final_path, "RPM build directory"],
        check=True,
    )
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
mkdir -p "${repo_tmp_root}"

rpmbuild_tmpdir="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-rpm-tmp-XXXXXX")"
stage_topdir=""
cleanup_tmpdir() {
  rm -rf -- "${rpmbuild_tmpdir}"
  if [[ -n "${stage_topdir}" ]]; then
    rm -rf -- "${stage_topdir}"
  fi
}
trap cleanup_tmpdir EXIT

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
mkdir -p "${dist_dir}"
dist_finalize_lock="${dist_dir}/.build-rpm.finalize.lock"
stage_topdir="$(mktemp -d "${rpmbuild_tmpdir}/.$(basename "${final_topdir}").stage.XXXXXX")"
spec_file="${stage_topdir}/SPECS/speed-of-cinnamon.spec"

if [[ ! -f "${spec_source}" ]]; then
  printf 'spec source missing: %s\n' "${spec_source}" >&2
  exit 1
fi
require_regular_source_file "${tarball}" "tarball source"
require_regular_source_file "${spec_source}" "spec source"

mkdir -p "${stage_topdir}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
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
data = tomllib.loads((repo_dir / "pyproject.toml").read_text(encoding="utf-8"))
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
from pathlib import Path
import re
import sys

spec_path = Path(sys.argv[1])
version = sys.argv[2]
text = spec_path.read_text(encoding="utf-8")
text = re.sub(r"^Version:\s*.*$", f"Version:        {version}", text, flags=re.M)
spec_path.write_text(text, encoding="utf-8")
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
rm -rf -- "${stage_topdir}"
stage_topdir=""

find "${final_topdir}/RPMS" "${final_topdir}/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) -print | sort
