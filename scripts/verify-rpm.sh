#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
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

safe_fs="${repo_dir}/scripts/safe-local-fs.py"
if [[ -L "${safe_fs}" || ! -f "${safe_fs}" || "$(stat -c '%F' "${safe_fs}")" != "regular file" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
  exit 1
fi
safe_fs_cmd=(python3 "${safe_fs}")

rpm_candidates=()
if [[ $# -eq 1 ]]; then
  rpm_path="${1}"
  if [[ -L "${rpm_path}" ]]; then
    printf 'RPM package must not be a symlink: %s\n' "${rpm_path}" >&2
    exit 1
  fi
  if ! rpm_path="$(realpath "${rpm_path}")"; then
    printf 'failed to resolve RPM path: %s\n' "${1}" >&2
    exit 1
  fi
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
if [[ -L "${rpm_path}" || ! -f "${rpm_path}" || "$(stat -c '%F' "${rpm_path}")" != "regular file" ]]; then
  printf 'RPM package must be a regular file: %s\n' "${rpm_path}" >&2
  exit 1
fi
rpm_path="$(realpath "${rpm_path}")"
if [[ -L "${rpm_path}" || ! -f "${rpm_path}" || ! ( "${rpm_path}" == "${repo_dir}/dist/rpmbuild/"*".rpm" || "${rpm_path}" == "${repo_dir}/dist/rpmbuild-generic/"*".rpm" ) || "$(stat -c '%F' "${rpm_path}")" != "regular file" ]]; then
  printf 'RPM package not valid: %s\n' "${rpm_path}" >&2
  exit 1
fi
rpm_link_count="$(stat -c '%h' "${rpm_path}")"
if [[ "${rpm_link_count}" -ne 1 ]]; then
  printf 'RPM package must not be hardlinked: %s\n' "${rpm_path}" >&2
  exit 1
fi

tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${tmp_root}" == /* ]]; then
  printf 'temporary root must be an absolute path: %s\n' "${tmp_root}" >&2
  exit 1
fi
if [[ -L "${tmp_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${tmp_root}" >&2
  exit 1
fi
if [[ ! -d "${tmp_root}" || ! -w "${tmp_root}" ]]; then
  printf 'temporary root is not a writable directory: %s\n' "${tmp_root}" >&2
  exit 1
fi
if [[ -L "${tmp_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${tmp_root}" >&2
  exit 1
fi
if ! tmp_root="$(realpath "${tmp_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${tmp_root}" >&2
  exit 1
fi
mkdir -p "${tmp_root}"

tmp_dir="$(mktemp -d "${tmp_root}/speed-of-cinnamon-rpm-verify-XXXXXX")"
cleanup_tmpdir() {
  rm -rf -- "${tmp_dir}"
}
trap cleanup_tmpdir EXIT

rpm_snapshot="${tmp_dir}/speed-of-cinnamon-verify.rpm"
if ! "${safe_fs_cmd[@]}" copy-file verify-rpm "${rpm_path}" "${rpm_snapshot}" 0644; then
  printf 'failed to snapshot RPM package for verification: %s\n' "${rpm_path}" >&2
  exit 1
fi
if [[ -L "${rpm_snapshot}" || ! -f "${rpm_snapshot}" || "$(stat -c '%F' "${rpm_snapshot}")" != "regular file" ]]; then
  printf 'RPM snapshot must be a regular file: %s\n' "${rpm_snapshot}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${rpm_snapshot}")" -ne 1 ]]; then
  printf 'RPM snapshot must not be hardlinked: %s\n' "${rpm_snapshot}" >&2
  exit 1
fi

metadata_file="${tmp_dir}/rpm-metadata.txt"
rpm -qp --qf 'name=%{NAME}\nversion=%{VERSION}\narch=%{ARCH}\npackager=%{PACKAGER}\nvendor=%{VENDOR}\nurl=%{URL}\n' "${rpm_snapshot}" > "${metadata_file}"
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

rpm -qpl "${rpm_snapshot}" > "${file_list}"
python3 - <<'PY' "${file_list}"
from pathlib import Path
import sys

ALLOWED_PREFIXES = (
    "/usr/bin/",
    "/usr/lib/",
    "/usr/lib64/",
    "/usr/share/",
)

file_list = Path(sys.argv[1])
for raw in file_list.read_text(encoding="utf-8").splitlines():
    entry = raw.strip()
    if not entry:
        continue
    if "\x00" in entry or any(ord(char) < 0x20 or ord(char) == 0x7F for char in entry):
        raise SystemExit(f"RPM package contains unsafe path entry: {entry!r}")
    if not entry.startswith("/"):
        raise SystemExit(f"RPM package contains unsafe relative path entry: {entry}")
    path = Path(entry)
    if any(part == ".." for part in path.parts):
        raise SystemExit(f"RPM package contains unsafe path entry: {entry}")
    if not entry.startswith(ALLOWED_PREFIXES):
        raise SystemExit(f"RPM package contains unexpected path entry: {entry}")
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
  rpm2cpio "${rpm_snapshot}" | cpio -idmu --no-absolute-filenames --quiet
)

if find "${tmp_dir}" -type l -print -quit | grep -q .; then
  printf 'RPM expansion contains unsupported symlink entries.\n' >&2
  exit 1
fi
if find "${tmp_dir}" -type f -links +1 -print -quit | grep -q .; then
  printf 'RPM expansion contains unsupported hardlink entries.\n' >&2
  exit 1
fi

backend="${tmp_dir}/usr/bin/speed-of-cinnamon"
if [[ ! -x "${backend}" ]]; then
  printf 'extracted backend is not executable: %s\n' "${backend}" >&2
  exit 1
fi
if ! grep -Fq 'from speed_of_cinnamon.cli import main' "${backend}"; then
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
