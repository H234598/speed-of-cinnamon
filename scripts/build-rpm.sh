#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"
rpm_tmpdir="${TMPDIR:-/tmp}"

if [ ! -d "${rpm_tmpdir}" ] || [ ! -w "${rpm_tmpdir}" ]; then
  rpm_tmpdir="${repo_dir}/.tmp"
fi
mkdir -p "${rpm_tmpdir}"

if ! command -v rpmbuild >/dev/null 2>&1; then
  printf 'rpmbuild not found. Install rpm-build on Fedora.\n' >&2
  exit 1
fi

python_bin="$(command -v python3)"
tarball="$("${repo_dir}/scripts/build-dist.sh")"
topdir="${repo_dir}/dist/rpmbuild"
spec_source="${repo_dir}/packaging/speed-of-cinnamon.spec"
spec_file="${topdir}/SPECS/speed-of-cinnamon.spec"

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

cp "${spec_source}" "${spec_file}"
${python_bin} - <<'PY' "${spec_file}" "${version}"
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
  --define "_tmppath ${rpm_tmpdir}" \
  --define "__python3 ${python_bin}" \
  -ba "${spec_file}"

find "${topdir}/RPMS" "${topdir}/SRPMS" -type f \( -name '*.rpm' -o -name '*.src.rpm' \) -print | sort
