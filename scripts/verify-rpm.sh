#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

if [[ $# -gt 1 ]]; then
  printf 'usage: %s [dist/rpmbuild/RPMS/noarch/speed-of-cinnamon-*.rpm]\n' "$0" >&2
  exit 2
fi

for tool in rpm rpm2cpio cpio python3; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install rpm and cpio tooling.\n' "${tool}" >&2
    exit 1
  fi
done

if [[ $# -eq 1 ]]; then
  rpm_path="$1"
else
  rpm_path="$(find "${repo_dir}/dist/rpmbuild/RPMS" -type f -name 'speed-of-cinnamon-*.noarch.rpm' 2>/dev/null | sort | tail -n 1 || true)"
fi

if [[ -z "${rpm_path}" || ! -f "${rpm_path}" ]]; then
  printf 'RPM package not found. Run make rpm first.\n' >&2
  exit 1
fi
rpm_path="$(realpath "${rpm_path}")"

tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

metadata_file="${tmp_dir}/rpm-metadata.txt"
rpm -qp --qf 'name=%{NAME}\nversion=%{VERSION}\narch=%{ARCH}\npackager=%{PACKAGER}\nvendor=%{VENDOR}\nurl=%{URL}\n' "${rpm_path}" > "${metadata_file}"
grep -Fxq 'name=speed-of-cinnamon' "${metadata_file}"
grep -Fxq 'arch=noarch' "${metadata_file}"
grep -Fxq 'packager=H234598 <54270221+H234598@users.noreply.github.com>' "${metadata_file}"
grep -Fxq 'vendor=H234598' "${metadata_file}"
grep -Fxq 'url=https://github.com/H234598/speed-of-cinnamon' "${metadata_file}"

required_files=(
  /usr/bin/speed-of-cinnamon
  /usr/share/cinnamon/applets/speed-of-cinnamon@H234598/applet.js
  /usr/share/cinnamon/applets/speed-of-cinnamon@H234598/metadata.json
  /usr/share/cinnamon/applets/speed-of-cinnamon@H234598/settings-schema.json
  /usr/share/doc/speed-of-cinnamon/README.md
  /usr/share/doc/speed-of-cinnamon/fedora-cinnamon-runbook.md
  /usr/share/licenses/speed-of-cinnamon/LICENSE
)
file_list="${tmp_dir}/rpm-files.txt"

rpm -qpl "${rpm_path}" > "${file_list}"
for required in "${required_files[@]}"; do
  if ! grep -Fxq "${required}" "${file_list}"; then
    printf 'RPM is missing %s\n' "${required}" >&2
    exit 1
  fi
done
if ! grep -Eq '^/usr/lib/python[^/]+/site-packages/speed_of_cinnamon/cli\.py$' "${file_list}"; then
  printf 'RPM is missing speed_of_cinnamon/cli.py under site-packages\n' >&2
  exit 1
fi

(
  cd "${tmp_dir}"
  rpm2cpio "${rpm_path}" | cpio -idmu --quiet
)

backend="${tmp_dir}/usr/bin/speed-of-cinnamon"
if [[ ! -x "${backend}" ]]; then
  printf 'extracted backend is not executable: %s\n' "${backend}" >&2
  exit 1
fi

package_dir="$(find "${tmp_dir}/usr/lib" -type d -path '*/site-packages/speed_of_cinnamon' | sort | head -n 1)"
if [[ -z "${package_dir}" ]]; then
  printf 'extracted Python package not found under site-packages\n' >&2
  exit 1
fi
python_path="$(dirname "${package_dir}")"

run_home="${tmp_dir}/home"
mkdir -p "${run_home}" "${tmp_dir}/cache" "${tmp_dir}/state" "${tmp_dir}/data"
PYTHONPATH="${python_path}" \
HOME="${run_home}" \
XDG_CACHE_HOME="${tmp_dir}/cache" \
XDG_STATE_HOME="${tmp_dir}/state" \
XDG_DATA_HOME="${tmp_dir}/data" \
  "${backend}" setup \
    --applet \
    --settings-json '{"transcriber":"command","transcriber-command":"printf ok","insert-method":"clipboard-paste"}' \
    --json > "${tmp_dir}/setup.json"
python3 -m json.tool "${tmp_dir}/setup.json" >/dev/null
python3 - <<'PY' "${tmp_dir}/setup.json"
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "done" or not payload.get("ready"):
    raise SystemExit(f"unexpected setup payload: {payload!r}")
PY

printf 'Verified %s\n' "${rpm_path}"
