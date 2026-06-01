#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

name="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["name"])
PY
)"
version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"
package="${name}-${version}"
dist_dir="${repo_dir}/dist"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

mkdir -p "${dist_dir}" "${work_dir}/${package}"

for path in \
  .github \
  docs \
  files \
  packaging \
  scripts \
  src \
  tests \
  LICENSE \
  Makefile \
  pyproject.toml \
  README.md
do
  cp -a "${repo_dir}/${path}" "${work_dir}/${package}/"
done

find "${work_dir}/${package}" \
  -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \) \
  -prune -exec rm -rf {} +
find "${work_dir}/${package}" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

cat > "${work_dir}/${package}/RELEASE-MANIFEST.txt" <<EOF
${package}

Contains:
- Cinnamon applet files under files/speed-of-cinnamon@H234598/
- Python backend under src/speed_of_cinnamon/
- local install/uninstall/dependency scripts under scripts/
- tests, CI workflow, README, license, and Fedora Cinnamon runbook
EOF

tarball="${dist_dir}/${package}.tar.gz"
tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@0" -C "${work_dir}" -czf "${tarball}" "${package}"
sha256sum "${tarball}" > "${tarball}.sha256"

printf 'Built %s\n' "${tarball}" >&2
printf '%s\n' "${tarball}"
