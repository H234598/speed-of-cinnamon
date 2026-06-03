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

repo_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${repo_tmp_root}" == /* ]]; then
  repo_tmp_root="/tmp"
fi
if [[ -L "${repo_tmp_root}" ]]; then
  repo_tmp_root="${repo_dir}/.tmp"
fi
if [[ -L "${repo_tmp_root}" ]]; then
  repo_tmp_root="/tmp"
fi
if [[ ! -d "${repo_tmp_root}" || ! -w "${repo_tmp_root}" ]]; then
  repo_tmp_root="${repo_dir}/.tmp"
fi
if [[ -L "${repo_tmp_root}" ]]; then
  repo_tmp_root="/tmp"
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
stage_topdir="$(mktemp -d "${dist_dir}/.$(basename "${final_topdir}").stage.XXXXXX")"
spec_file="${stage_topdir}/SPECS/speed-of-cinnamon.spec"

if [[ ! -f "${spec_source}" ]]; then
  printf 'spec source missing: %s\n' "${spec_source}" >&2
  exit 1
fi
require_regular_source_file "${tarball}" "tarball source"
require_regular_source_file "${spec_source}" "spec source"

mkdir -p "${stage_topdir}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp "${tarball}" "${stage_topdir}/SOURCES/"

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

cp "${spec_source}" "${spec_file}"
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

if ! "${safe_fs_cmd[@]}" install-tree build-rpm "${stage_topdir}" "${final_topdir}" "RPM build directory"; then
  printf 'failed to activate RPM build directory: %s\n' "${final_topdir}" >&2
  exit 1
fi
rm -rf -- "${stage_topdir}"
stage_topdir=""

find "${final_topdir}/RPMS" "${final_topdir}/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) -print | sort
