#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

require_cmd() {
  local tool=$1
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

repo_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${repo_tmp_root}" == /* ]]; then
  repo_tmp_root="/tmp"
fi

if [[ ! -d "${repo_tmp_root}" || ! -w "${repo_tmp_root}" ]]; then
  repo_tmp_root="${repo_dir}/.tmp"
fi
mkdir -p "${repo_tmp_root}"

rpmbuild_tmpdir="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-rpm-tmp-XXXXXX")"
cleanup_tmpdir() {
  rm -rf -- "${rpmbuild_tmpdir}"
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

python_bin="$(command -v python3)"
if [[ ! -x "${repo_dir}/scripts/build-dist.sh" ]]; then
  printf 'build-dist script is missing: %s\n' "${repo_dir}/scripts/build-dist.sh" >&2
  exit 1
fi
read -r tarball < <("${repo_dir}/scripts/build-dist.sh")
tarball="$(realpath "${tarball}")"
if [[ ! -f "${tarball}" || ! "${tarball}" == "${repo_dir}/dist/"*".tar.gz" ]]; then
  printf 'build-dist output is invalid: %s\n' "${tarball}" >&2
  exit 1
fi

if [[ "${profile}" == "generic" ]]; then
  topdir="${repo_dir}/dist/rpmbuild-generic"
  spec_source="${repo_dir}/packaging/speed-of-cinnamon-generic.spec"
else
  topdir="${repo_dir}/dist/rpmbuild"
  spec_source="${repo_dir}/packaging/speed-of-cinnamon.spec"
fi
spec_file="${topdir}/SPECS/speed-of-cinnamon.spec"

if [[ ! -f "${spec_source}" ]]; then
  printf 'spec source missing: %s\n' "${spec_source}" >&2
  exit 1
fi

rm -rf "${topdir}"
mkdir -p "${topdir}"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp "${tarball}" "${topdir}/SOURCES/"

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
  --define "_topdir ${topdir}" \
  --define "_sourcedir ${topdir}/SOURCES" \
  --define "_specdir ${repo_dir}/packaging" \
  --define "_smp_build_ncpus 1" \
  --define "_tmppath ${rpmbuild_tmpdir}" \
  --define "__python3 ${python_bin}" \
  -ba "${spec_file}"

find "${topdir}/RPMS" "${topdir}/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) -print | sort
