#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"

dist_dir="${repo_dir}/dist"
if [[ -L "${dist_dir}" || ! -d "${dist_dir}" ]]; then
  printf 'dist directory is invalid: %s\n' "${dist_dir}" >&2
  exit 1
fi

if [[ $# -gt 1 ]]; then
  printf 'usage: %s [dist/rpmbuild*/RPMS/noarch/speed-of-cinnamon-*.rpm]\n' "$0" >&2
  exit 2
fi

for tool in rpm rpm2cpio cpio python3 realpath; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install rpm and cpio tooling.\n' "${tool}" >&2
    exit 1
  fi
done

rpm_candidates=()
if [[ $# -eq 1 ]]; then
  rpm_path="${1}"
  if [[ -L "${rpm_path}" ]]; then
    printf 'RPM package must not be a symlink: %s\n' "${rpm_path}" >&2
    exit 1
  fi
  rpm_path="$(realpath "${rpm_path}")"
else
  rpm_candidates=(
    "${repo_dir}"/dist/rpmbuild/RPMS/noarch/speed-of-cinnamon-*.noarch.rpm
  )
  shopt -s nullglob
  filtered_rpms=()
  for candidate in "${rpm_candidates[@]}"; do
    if [[ -f "${candidate}" && ! -L "${candidate}" ]]; then
      filtered_rpms+=("${candidate}")
    fi
  done
  shopt -u nullglob
  if [[ ${#filtered_rpms[@]} -ne 1 ]]; then
    printf 'expected exactly one RPM package, found %d\n' "${#filtered_rpms[@]}" >&2
    exit 1
  fi
  rpm_path="${filtered_rpms[0]}"
fi

if [[ ! -f "${rpm_path}" || ! ( "${rpm_path}" == "${repo_dir}/dist/rpmbuild/"*".rpm" || "${rpm_path}" == "${repo_dir}/dist/rpmbuild-generic/"*".rpm" ) ]]; then
  printf 'RPM package not found: %s\n' "${rpm_path}" >&2
  exit 1
fi
rpm_path="$(realpath "${rpm_path}")"
if [[ -L "${rpm_path}" || ! -f "${rpm_path}" || ! ( "${rpm_path}" == "${repo_dir}/dist/rpmbuild/"*".rpm" || "${rpm_path}" == "${repo_dir}/dist/rpmbuild-generic/"*".rpm" ) ]]; then
  printf 'RPM package not valid: %s\n' "${rpm_path}" >&2
  exit 1
fi

tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${tmp_root}" == /* ]]; then
  tmp_root="/tmp"
fi
if [[ -L "${tmp_root}" ]]; then
  tmp_root="${repo_dir}/.tmp"
fi
if [[ ! -d "${tmp_root}" || ! -w "${tmp_root}" ]]; then
  tmp_root="${repo_dir}/.tmp"
fi
if [[ -L "${tmp_root}" ]]; then
  tmp_root="${repo_dir}/.tmp"
fi
mkdir -p "${tmp_root}"

tmp_dir="$(mktemp -d "${tmp_root}/speed-of-cinnamon-rpm-verify-XXXXXX")"
cleanup_tmpdir() {
  rm -rf -- "${tmp_dir}"
}
trap cleanup_tmpdir EXIT

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
)
file_list="${tmp_dir}/rpm-files.txt"

rpm -qpl "${rpm_path}" > "${file_list}"
python3 - <<'PY' "${file_list}"
from pathlib import Path
import sys

file_list = Path(sys.argv[1])
for raw in file_list.read_text(encoding="utf-8").splitlines():
    entry = raw.strip()
    if not entry:
        continue
    path = Path(entry)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise SystemExit(f"RPM package contains unsafe path entry: {entry}")
PY

for required in "${required_files[@]}"; do
  if ! grep -Fxq "${required}" "${file_list}"; then
    printf 'RPM is missing %s\n' "${required}" >&2
    exit 1
  fi
done
for required in \
  '^/usr/share/man/man1/speed-of-cinnamon\.1(\.gz)?$' \
  '^/usr/share/man/man1/speed-of-cinnamon-alarms\.1(\.gz)?$' \
; do
  if ! grep -Eq "${required}" "${file_list}"; then
    printf 'RPM is missing file matching %s\n' "${required}" >&2
    exit 1
  fi
done
if ! grep -Eq '^/usr/lib/python[^/]+/site-packages/speed_of_cinnamon/cli\.py$' "${file_list}"; then
  printf 'RPM is missing speed_of_cinnamon/cli.py under site-packages\n' >&2
  exit 1
fi
for pattern in \
  '^/usr/share/doc/speed-of-cinnamon(-[^/]*)?/README\.md$' \
  '^/usr/share/doc/speed-of-cinnamon(-[^/]*)?/architecture\.md$' \
  '^/usr/share/doc/speed-of-cinnamon(-[^/]*)?/cli-reference\.md$' \
  '^/usr/share/doc/speed-of-cinnamon(-[^/]*)?/development\.md$' \
  '^/usr/share/doc/speed-of-cinnamon(-[^/]*)?/fedora-cinnamon-runbook\.md$' \
  '^/usr/share/doc/speed-of-cinnamon(-[^/]*)?/user-guide\.md$' \
  '^/usr/share/(doc|licenses)/speed-of-cinnamon(-[^/]*)?/LICENSE$'
do
  if ! grep -Eq "${pattern}" "${file_list}"; then
    printf 'RPM is missing a file matching %s\n' "${pattern}" >&2
    exit 1
  fi
done

(
  cd "${tmp_dir}"
  rpm2cpio "${rpm_path}" | cpio -idmu --no-absolute-filenames --quiet
)

if find "${tmp_dir}" -type l -print -quit | grep -q .; then
  printf 'RPM expansion contains unsupported symlink entries.\n' >&2
  exit 1
fi

backend="${tmp_dir}/usr/bin/speed-of-cinnamon"
if [[ ! -x "${backend}" ]]; then
  printf 'extracted backend is not executable: %s\n' "${backend}" >&2
  exit 1
fi
if ! grep -Fq 'python3)" -m speed_of_cinnamon.cli "$@"' "${backend}"; then
  printf 'extracted backend does not invoke the expected CLI module: %s\n' "${backend}" >&2
  exit 1
fi

package_dir="$(find "${tmp_dir}/usr/lib" -type d -path '*/site-packages/speed_of_cinnamon' | sort | head -n 1)"
if [[ -z "${package_dir}" ]]; then
  printf 'extracted Python package not found under site-packages\n' >&2
  exit 1
fi

python3 -m compileall -q "${package_dir}"

printf 'Verified %s\n' "${rpm_path}"
