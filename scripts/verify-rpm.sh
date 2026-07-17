#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"
readonly MAX_RPM_ARCHIVE_BYTES=$((256 * 1024 * 1024))
readonly MAX_RPM_FILES=5000
readonly MAX_RPM_PATH_CHARS=260
readonly MAX_RPM_PATH_DEPTH=32
readonly MAX_RPM_FILE_BYTES=$((64 * 1024 * 1024))
readonly MAX_RPM_TOTAL_FILE_BYTES=$((512 * 1024 * 1024))

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

for tool in rpm rpm2cpio cpio python3 realpath stat; do
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
if [[ "$(stat -c '%h' "${safe_fs}")" -ne 1 ]]; then
  printf 'safe local filesystem helper must not be hardlinked: %s\n' "${safe_fs}" >&2
  exit 1
fi
safe_fs_cmd=(python3 "${safe_fs}")

contains_control_chars() {
  local value=$1
  python3 - "${value}" <<'PY'
import sys

value = sys.argv[1]
raise SystemExit(
    not (
        any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)
        or any(0xDC80 <= ord(char) <= 0xDCFF for char in value)
    )
)
PY
}

rpm_candidates=()
if [[ $# -eq 1 ]]; then
  rpm_path="${1}"
  if contains_control_chars "${rpm_path}"; then
    printf 'RPM package path contains control characters\n' >&2
    exit 1
  fi
  if [[ -L "${rpm_path}" ]]; then
    printf 'RPM package must not be a symlink: %s\n' "${rpm_path}" >&2
    exit 1
  fi
  if [[ ! -f "${rpm_path}" ]]; then
    printf 'RPM package not found\n' >&2
    exit 1
  fi
  if ! rpm_path="$(realpath "${rpm_path}" 2>/dev/null)"; then
    printf 'failed to resolve RPM path\n' >&2
    exit 1
  fi
else
  rpm_candidates=(
    "${repo_dir}"/dist/rpmbuild/RPMS/noarch/speed-of-cinnamon-*.noarch.rpm
    "${repo_dir}"/dist/rpmbuild-generic/RPMS/noarch/speed-of-cinnamon-*.noarch.rpm
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
rpm_path="$(realpath "${rpm_path}" 2>/dev/null)"
if [[ -L "${rpm_path}" || ! -f "${rpm_path}" || ! ( "${rpm_path}" == "${repo_dir}/dist/rpmbuild/"*".rpm" || "${rpm_path}" == "${repo_dir}/dist/rpmbuild-generic/"*".rpm" ) || "$(stat -c '%F' "${rpm_path}")" != "regular file" ]]; then
  printf 'RPM package not valid: %s\n' "${rpm_path}" >&2
  exit 1
fi
rpm_link_count="$(stat -c '%h' "${rpm_path}")"
if [[ "${rpm_link_count}" -ne 1 ]]; then
  printf 'RPM package must not be hardlinked: %s\n' "${rpm_path}" >&2
  exit 1
fi
rpm_bytes="$(stat -c '%s' "${rpm_path}")"
if [[ "${rpm_bytes}" -le 0 || "${rpm_bytes}" -gt "${MAX_RPM_ARCHIVE_BYTES}" ]]; then
  printf 'RPM package size is outside allowed bounds: %s bytes\n' "${rpm_bytes}" >&2
  exit 1
fi

tmp_root="${TMPDIR:-/tmp}"
if contains_control_chars "${tmp_root}"; then
  printf 'temporary root contains control characters\n' >&2
  exit 1
fi
if [[ ! "${tmp_root}" == /* ]]; then
  printf 'temporary root must be an absolute path\n' >&2
  exit 1
fi
if [[ -L "${tmp_root}" ]]; then
  printf 'temporary root must not be a symlink\n' >&2
  exit 1
fi
if [[ ! -d "${tmp_root}" || ! -w "${tmp_root}" ]]; then
  printf 'temporary root is not a writable directory\n' >&2
  exit 1
fi
if [[ -L "${tmp_root}" ]]; then
  printf 'temporary root must not be a symlink\n' >&2
  exit 1
fi
if ! tmp_root="$(realpath "${tmp_root}" 2>/dev/null)"; then
  printf 'failed to resolve temporary root\n' >&2
  exit 1
fi
mkdir -p "${tmp_root}"

tmp_dir="$(mktemp -d "${tmp_root}/speed-of-cinnamon-rpm-verify-XXXXXX")"
if [[ -L "${tmp_dir}" ]]; then
  printf 'temporary RPM verification directory must not be a symlink: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if ! tmp_dir_abs="$(realpath "${tmp_dir}")"; then
  printf 'failed to resolve temporary RPM verification directory: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if [[ "${tmp_dir_abs}" != "${tmp_root}/speed-of-cinnamon-rpm-verify-"* ]]; then
  printf 'temporary RPM verification directory escaped temporary root: %s\n' "${tmp_dir}" >&2
  exit 1
fi
tmp_dir="${tmp_dir_abs}"
cleanup_tmpdir() {
  "${safe_fs_cmd[@]}" remove verify-rpm "${tmp_dir}" --kind dir >/dev/null 2>&1 || true
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
scriptlets_file="${tmp_dir}/rpm-scriptlets.txt"
triggers_file="${tmp_dir}/rpm-triggers.txt"
rpm -qp --qf 'name=%{NAME}\nversion=%{VERSION}\narch=%{ARCH}\npackager=%{PACKAGER}\nvendor=%{VENDOR}\nurl=%{URL}\n' "${rpm_snapshot}" > "${metadata_file}"
grep -Fxq 'name=speed-of-cinnamon' "${metadata_file}"
grep -Fxq 'arch=noarch' "${metadata_file}"
grep -Fxq 'packager=H234598 <54270221+H234598@users.noreply.github.com>' "${metadata_file}"
grep -Fxq 'vendor=H234598' "${metadata_file}"
grep -Fxq 'url=https://github.com/H234598/speed-of-cinnamon' "${metadata_file}"

if ! rpm -qp --scripts "${rpm_snapshot}" > "${scriptlets_file}"; then
  printf 'failed to query RPM scriptlets: %s\n' "${rpm_snapshot}" >&2
  exit 1
fi
if [[ -s "${scriptlets_file}" ]]; then
  printf 'RPM scriptlets are not allowed for release packages: %s\n' "${rpm_snapshot}" >&2
  exit 1
fi
if ! rpm -qp --triggers "${rpm_snapshot}" > "${triggers_file}"; then
  printf 'failed to query RPM triggers: %s\n' "${rpm_snapshot}" >&2
  exit 1
fi
if [[ -s "${triggers_file}" ]]; then
  printf 'RPM triggers are not allowed for release packages: %s\n' "${rpm_snapshot}" >&2
  exit 1
fi

required_files=(
  /usr/bin/speed-of-cinnamon
  /usr/share/cinnamon/applets/speed-of-cinnamon@H234598/applet.js
  /usr/share/cinnamon/applets/speed-of-cinnamon@H234598/metadata.json
  /usr/share/cinnamon/applets/speed-of-cinnamon@H234598/settings-schema.json
)
file_list="${tmp_dir}/rpm-files.txt"
file_metadata="${tmp_dir}/rpm-file-metadata.txt"

rpm -qpl "${rpm_snapshot}" > "${file_list}"
python3 - <<'PY' "${file_list}" "${MAX_RPM_FILES}" "${MAX_RPM_PATH_CHARS}" "${MAX_RPM_PATH_DEPTH}"
from pathlib import Path
import sys

MAX_RPM_FILES = int(sys.argv[2])
MAX_RPM_PATH_CHARS = int(sys.argv[3])
MAX_RPM_PATH_DEPTH = int(sys.argv[4])
ALLOWED_PREFIXES = (
    "/usr/bin/",
    "/usr/lib/",
    "/usr/lib64/",
    "/usr/share/",
)

file_list = Path(sys.argv[1])
entry_count = 0
for entry in file_list.read_text(encoding="utf-8").split("\n"):
    if not entry:
        continue
    entry_count += 1
    if entry_count > MAX_RPM_FILES:
        raise SystemExit("RPM package contains too many file entries")
    if (
        "\x00" in entry
        or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in entry)
        or any(0xDC80 <= ord(char) <= 0xDCFF for char in entry)
    ):
        raise SystemExit(f"RPM package contains unsafe path entry: {entry!r}")
    if not entry.startswith("/"):
        raise SystemExit(f"RPM package contains unsafe relative path entry: {entry}")
    if len(entry) > MAX_RPM_PATH_CHARS:
        raise SystemExit(f"RPM package contains path that is too long: {entry}")
    path = Path(entry)
    if len([part for part in path.parts if part not in {path.anchor, ''}]) > MAX_RPM_PATH_DEPTH:
        raise SystemExit(f"RPM package contains path that is too deep: {entry}")
    if any(part == ".." for part in path.parts):
        raise SystemExit(f"RPM package contains unsafe path entry: {entry}")
    if not entry.startswith(ALLOWED_PREFIXES):
        raise SystemExit(f"RPM package contains unexpected path entry: {entry}")
PY

rpm -qp --qf '[%{FILENAMES}\t%{FILEMODES:octal}\t%{FILECAPS}\t%{FILELINKTOS}\t%{FILESIZES}\n]' "${rpm_snapshot}" > "${file_metadata}"
python3 - <<'PY' "${file_list}" "${file_metadata}" "${MAX_RPM_FILE_BYTES}" "${MAX_RPM_TOTAL_FILE_BYTES}"
from pathlib import Path
import stat
import sys

file_entries = [entry for entry in Path(sys.argv[1]).read_text(encoding="utf-8").split("\n") if entry]
metadata_entries: list[str] = []
seen: set[str] = set()
MAX_RPM_FILE_BYTES = int(sys.argv[3])
MAX_RPM_TOTAL_FILE_BYTES = int(sys.argv[4])
total_file_bytes = 0
for raw in Path(sys.argv[2]).read_text(encoding="utf-8").split("\n"):
    if not raw:
        continue
    parts = raw.split("\t", 4)
    if len(parts) != 5:
        raise SystemExit(f"RPM package contains malformed file metadata: {raw!r}")
    entry, mode_text, file_caps, link_target, size_text = parts
    if entry in seen:
        raise SystemExit(f"RPM package contains duplicate file entry: {entry}")
    seen.add(entry)
    try:
        mode = int(mode_text, 8)
    except ValueError:
        raise SystemExit(f"RPM package contains malformed file mode for {entry}: {mode_text!r}") from None
    permissions = stat.S_IMODE(mode)
    if permissions & 0o7000:
        raise SystemExit(f"RPM package contains privileged file mode for {entry}: {mode_text!r}")
    if permissions & 0o022:
        raise SystemExit(f"RPM package contains group/world-writable file mode for {entry}: {mode_text!r}")
    if file_caps.strip() not in {"", "(none)"}:
        raise SystemExit(f"RPM package contains file capabilities on {entry}: {file_caps!r}")
    file_type = stat.S_IFMT(mode)
    if file_type not in (stat.S_IFREG, stat.S_IFDIR):
        raise SystemExit(f"RPM package contains unsupported file type: {entry}")
    if file_type == stat.S_IFDIR:
        if permissions & 0o777 > 0o755:
            raise SystemExit(f"RPM package contains disallowed directory mode for {entry}: {mode_text!r}")
    else:
        file_permissions = permissions & 0o777
        if file_permissions & 0o111:
            if file_permissions != 0o755:
                raise SystemExit(f"RPM package contains disallowed executable mode for {entry}: {mode_text!r}")
        elif file_permissions > 0o644:
            raise SystemExit(f"RPM package contains disallowed file mode for {entry}: {mode_text!r}")
    if link_target:
        raise SystemExit(f"RPM package contains unsupported link target: {entry} -> {link_target}")
    try:
        file_size = int(size_text)
    except ValueError:
        raise SystemExit(f"RPM package contains malformed file size for {entry}: {size_text!r}") from None
    if file_size < 0:
        raise SystemExit(f"RPM package contains negative file size for {entry}: {size_text!r}")
    if file_type == stat.S_IFREG:
        if file_size > MAX_RPM_FILE_BYTES:
            raise SystemExit(f"RPM package contains oversized file: {entry}")
        total_file_bytes += file_size
        if total_file_bytes > MAX_RPM_TOTAL_FILE_BYTES:
            raise SystemExit("RPM package file size budget exceeded")
    metadata_entries.append(entry)
if metadata_entries != file_entries:
    raise SystemExit("RPM package file metadata does not match file listing")
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
