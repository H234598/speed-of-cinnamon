#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

if ! command -v rpmbuild >/dev/null 2>&1; then
  printf 'rpmbuild not found. Install rpm-build on Fedora.\n' >&2
  exit 1
fi

tarball="$("${repo_dir}/scripts/build-dist.sh")"
topdir="${repo_dir}/dist/rpmbuild"
rm -rf "${topdir}"
mkdir -p "${topdir}/"{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}
cp "${tarball}" "${topdir}/SOURCES/"

rpmbuild \
  --nodeps \
  --define "_topdir ${topdir}" \
  --define "_sourcedir ${topdir}/SOURCES" \
  --define "_specdir ${repo_dir}/packaging" \
  -ba "${repo_dir}/packaging/speed-of-cinnamon.spec"

find "${topdir}/RPMS" "${topdir}/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) -print | sort
