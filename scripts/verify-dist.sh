#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"
readonly MAX_DIST_ARCHIVE_BYTES=$((128 * 1024 * 1024))
readonly MAX_DIST_MEMBERS=2000
readonly MAX_DIST_PATH_CHARS=240
readonly MAX_DIST_PATH_DEPTH=20
readonly MAX_DIST_FILE_BYTES=$((32 * 1024 * 1024))
readonly MAX_DIST_TOTAL_EXTRACTED_BYTES=$((256 * 1024 * 1024))

if [[ $# -ne 1 ]]; then
  printf 'usage: %s dist/speed-of-cinnamon-VERSION.tar.gz\n' "$0" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

dist_dir="${repo_dir}/dist"
if [[ -L "${dist_dir}" || ! -d "${dist_dir}" ]]; then
  printf 'dist directory is invalid: %s\n' "${dist_dir}" >&2
  exit 1
fi

for tool in realpath stat tar awk mktemp find grep python3 sha256sum; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done

if [[ -L "${safe_fs}" || ! -f "${safe_fs}" || "$(stat -c '%F' "${safe_fs}")" != "regular file" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${safe_fs}")" -ne 1 ]]; then
  printf 'safe local filesystem helper must not be hardlinked: %s\n' "${safe_fs}" >&2
  exit 1
fi

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

tarball_input="$1"
if contains_control_chars "${tarball_input}"; then
  printf 'archive path contains control characters\n' >&2
  exit 1
fi
if [[ -L "${tarball_input}" ]]; then
  printf 'archive must not be a symlink: %s\n' "${tarball_input}" >&2
  exit 1
fi
if [[ ! -f "${tarball_input}" ]]; then
  printf 'archive missing or invalid\n' >&2
  exit 1
fi
if ! tarball="$(realpath "${tarball_input}" 2>/dev/null)"; then
  printf 'failed to resolve archive path\n' >&2
  exit 1
fi
if [[ -L "${tarball}" || ! -f "${tarball}" || ! "${tarball}" == *.tar.gz || ! "${tarball}" == "${repo_dir}/dist/"* ]]; then
  printf 'archive missing or invalid: %s\n' "${tarball}" >&2
  exit 1
fi
if [[ -L "${tarball}" || ! -f "${tarball}" || "$(stat -c '%F' "${tarball}")" != "regular file" ]]; then
  printf 'archive must be a regular file: %s\n' "${tarball}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${tarball}")" -ne 1 ]]; then
  printf 'archive must not be hardlinked: %s\n' "${tarball}" >&2
  exit 1
fi
tarball_bytes="$(stat -c '%s' "${tarball}")"
if [[ "${tarball_bytes}" -le 0 || "${tarball_bytes}" -gt "${MAX_DIST_ARCHIVE_BYTES}" ]]; then
  printf 'archive size is outside allowed bounds: %s bytes\n' "${tarball_bytes}" >&2
  exit 1
fi

checksum_path="${tarball}.sha256"
if [[ -L "${checksum_path}" || ! -f "${checksum_path}" ]]; then
  printf 'archive checksum file missing or invalid: %s\n' "${checksum_path}" >&2
  exit 1
fi
if ! checksum_path="$(realpath "${checksum_path}" 2>/dev/null)"; then
  printf 'failed to resolve archive checksum file\n' >&2
  exit 1
fi
if [[ -L "${checksum_path}" || ! -f "${checksum_path}" || ! "${checksum_path}" == "${repo_dir}/dist/"* ]]; then
  printf 'archive checksum file missing or invalid: %s\n' "${checksum_path}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${checksum_path}")" -ne 1 ]]; then
  printf 'archive checksum file must not be hardlinked: %s\n' "${checksum_path}" >&2
  exit 1
fi
checksum_value="$(python3 - "${checksum_path}" "$(basename "${tarball}")" <<'PY'
from pathlib import Path
import re
import sys

checksum_path, expected_name = sys.argv[1:]
raw = Path(checksum_path).read_bytes()
if len(raw) > 4096:
    raise SystemExit("archive checksum file is too large")
try:
    text = raw.decode("ascii")
except UnicodeDecodeError:
    raise SystemExit("archive checksum file is not ASCII") from None
lines = text.splitlines()
if len(lines) != 1:
    raise SystemExit("archive checksum file must contain exactly one entry")
parts = lines[0].split()
if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
    raise SystemExit("archive checksum file has invalid format")
if parts[1].lstrip("*") != expected_name:
    raise SystemExit("archive checksum file target does not match archive")
print(parts[0].lower())
PY
)" || {
  printf 'archive checksum file has invalid format: %s\n' "${checksum_path}" >&2
  exit 1
}
actual_checksum="$(sha256sum "${tarball}" | awk '{print tolower($1)}')"
if [[ "${actual_checksum}" != "${checksum_value}" ]]; then
  printf 'archive checksum mismatch: %s\n' "${tarball}" >&2
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
tmp_dir="$(mktemp -d "${tmp_root}/speed-of-cinnamon-dist-verify-XXXXXX")"
if [[ -L "${tmp_dir}" ]]; then
  printf 'temporary dist verification directory must not be a symlink: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if ! tmp_dir_abs="$(realpath "${tmp_dir}")"; then
  printf 'failed to resolve temporary dist verification directory: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if [[ "${tmp_dir_abs}" != "${tmp_root}/speed-of-cinnamon-dist-verify-"* ]]; then
  printf 'temporary dist verification directory escaped temporary root: %s\n' "${tmp_dir}" >&2
  exit 1
fi
tmp_dir="${tmp_dir_abs}"
tmp_dir_identity=""
cleanup_tmpdir() {
  if [[ -n "${tmp_dir_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove verify-dist "${tmp_dir}" --kind dir \
      --expected-identity "${tmp_dir_identity}" >/dev/null 2>&1 || true
  else
    printf 'refusing dist verification cleanup without verified identity: %s\n' "${tmp_dir}" >&2
  fi
}
trap cleanup_tmpdir EXIT

if ! tmp_dir_identity="$("${safe_fs_cmd[@]}" identity verify-dist "${tmp_dir}" --kind dir)"; then
  printf 'failed to capture dist verification directory identity: %s\n' "${tmp_dir}" >&2
  exit 1
fi

tarball_snapshot="${tmp_dir}/speed-of-cinnamon-verify.tar.gz"
if ! "${safe_fs_cmd[@]}" copy-file verify-dist "${tarball}" "${tarball_snapshot}" 0644 \
  --max-bytes "${MAX_DIST_ARCHIVE_BYTES}"; then
  printf 'failed to snapshot archive for verification: %s\n' "${tarball}" >&2
  exit 1
fi
if [[ -L "${tarball_snapshot}" || ! -f "${tarball_snapshot}" || "$(stat -c '%F' "${tarball_snapshot}")" != "regular file" ]]; then
  printf 'archive snapshot must be a regular file: %s\n' "${tarball_snapshot}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${tarball_snapshot}")" -ne 1 ]]; then
  printf 'archive snapshot must not be hardlinked: %s\n' "${tarball_snapshot}" >&2
  exit 1
fi
snapshot_bytes="$(stat -c '%s' "${tarball_snapshot}")"
if [[ "${snapshot_bytes}" -le 0 || "${snapshot_bytes}" -gt "${MAX_DIST_ARCHIVE_BYTES}" ]]; then
  printf 'archive snapshot size is outside allowed bounds: %s bytes\n' "${snapshot_bytes}" >&2
  exit 1
fi
tarball_bytes="${snapshot_bytes}"

if ! tar -tzf "${tarball_snapshot}" | awk -F'/' '
  /(^|\/)\.\.(\/|$)/ || /^\// { print; bad = 1 }
  END { exit bad ? 1 : 0 }
' > /dev/null; then
  printf 'archive contains unsafe path entries (path traversal or absolute path): %s\n' "${tarball}" >&2
  exit 1
fi

python3 - "$tarball_snapshot" "$tmp_dir" "${MAX_DIST_MEMBERS}" "${MAX_DIST_PATH_CHARS}" "${MAX_DIST_PATH_DEPTH}" "${MAX_DIST_FILE_BYTES}" "${MAX_DIST_TOTAL_EXTRACTED_BYTES}" <<'PY'
import os
import pathlib
import stat
import tarfile
import sys

tarball_snapshot = sys.argv[1]
target = pathlib.Path(sys.argv[2])
MAX_DIST_MEMBERS = int(sys.argv[3])
MAX_DIST_PATH_CHARS = int(sys.argv[4])
MAX_DIST_PATH_DEPTH = int(sys.argv[5])
MAX_DIST_FILE_BYTES = int(sys.argv[6])
MAX_DIST_TOTAL_EXTRACTED_BYTES = int(sys.argv[7])
target.mkdir(parents=True, exist_ok=True)
target_root = target.resolve(strict=True)


def member_target(member_name):
    path = target / member_name
    if not path.resolve(strict=False).is_relative_to(target_root):
        raise SystemExit(f"dist archive path escapes target: {member_name}")
    return path


def validate_member_mode(member):
    if member.mode is None:
        raise SystemExit(f"dist archive member has no mode: {member.name}")
    permissions = stat.S_IMODE(member.mode)
    if permissions & 0o7000:
        raise SystemExit(f"dist archive member has disallowed setuid/setgid/sticky bits: {member.name}")
    if permissions & 0o022:
        raise SystemExit(f"dist archive member is group/world writable: {member.name}")
    if member.isdir():
        if permissions & 0o777 > 0o755:
            raise SystemExit(f"dist archive directory has disallowed permissions: {member.name}")
        return
    file_permissions = permissions & 0o777
    if file_permissions & 0o111:
        if file_permissions != 0o755:
            raise SystemExit(f"dist archive executable file has disallowed permissions: {member.name}")
    elif file_permissions > 0o644:
        raise SystemExit(f"dist archive non-executable file has disallowed permissions: {member.name}")


with tarfile.open(tarball_snapshot, "r:gz") as archive:
    package_root = None
    member_count = 0
    total_file_size = 0
    for member in archive.getmembers():
        member_count += 1
        if member_count > MAX_DIST_MEMBERS:
            raise SystemExit("dist archive contains too many entries")
        if (
            "\x00" in member.name
            or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in member.name)
            or any(0xDC80 <= ord(char) <= 0xDCFF for char in member.name)
        ):
            raise SystemExit(f"dist archive contains unsafe path entry: {member.name!r}")
        if len(member.name) > MAX_DIST_PATH_CHARS:
            raise SystemExit(f"dist archive path is too long: {member.name}")
        if len([part for part in member.name.split("/") if part]) > MAX_DIST_PATH_DEPTH:
            raise SystemExit(f"dist archive path is too deep: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"dist archive contains unsupported entry type: {member.name}")
        validate_member_mode(member)
        if member.name.startswith("/"):
            raise SystemExit(f"dist archive path is absolute: {member.name}")
        if ".." in member.name.split("/"):
            raise SystemExit(f"dist archive path escapes target: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"dist archive contains unsupported link entry: {member.name}")
        root = member.name.split("/", 1)[0]
        if not root:
            raise SystemExit(f"dist archive contains an empty path entry: {member.name}")
        if package_root is None:
            package_root = root
        elif root != package_root:
            raise SystemExit(f"dist archive contains multiple top-level entries: {member.name}")
        output_path = member_target(member.name)
        if member.isdir():
            output_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise SystemExit(f"dist archive contains unsupported entry type: {member.name}")
        if member.size < 0:
            raise SystemExit(f"dist archive file has invalid size: {member.name}")
        if member.size > MAX_DIST_FILE_BYTES:
            raise SystemExit(f"dist archive file is too large: {member.name}")
        total_file_size += member.size
        if total_file_size > MAX_DIST_TOTAL_EXTRACTED_BYTES:
            raise SystemExit("dist archive extracted size budget exceeded")
        output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"dist archive file could not be read: {member.name}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(output_path, flags, 0o600)
        except FileExistsError:
            raise SystemExit(f"dist archive contains duplicate file entry: {member.name}") from None
        with source, os.fdopen(fd, "wb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
PY

package_dirs=()
while IFS= read -r -d '' path; do
  package_dirs+=("${path}")
done < <(find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d -print0 | sort -z)

if [[ ${#package_dirs[@]} -ne 1 ]]; then
  printf 'archive should contain exactly one top-level directory, found %d\n' "${#package_dirs[@]}" >&2
  exit 1
fi

package_dir="${package_dirs[0]}"
if [[ -z "${package_dir}" || ! -d "${package_dir}" ]]; then
  printf 'archive did not contain a package directory: %s\n' "${tarball}" >&2
  exit 1
fi

if find "${package_dir}" -type l -print -quit | grep -q .; then
  printf 'archive expansion contains unsupported symlink entries.\n' >&2
  exit 1
fi

python3 - "${package_dir}" <<'PY'
from pathlib import Path
import stat
import sys


def validate_mode(path: Path) -> None:
    mode = path.lstat().st_mode
    permissions = stat.S_IMODE(mode)
    if stat.S_ISLNK(mode):
        raise SystemExit(f"archive expansion contains unsupported symlink entry: {path}")
    if stat.S_ISDIR(mode):
        if permissions & 0o7000:
            raise SystemExit(f"archive directory has disallowed setuid/setgid/sticky bits: {path}")
        if permissions & 0o022:
            raise SystemExit(f"archive directory is group/world writable: {path}")
        if permissions & 0o777 > 0o755:
            raise SystemExit(f"archive directory has disallowed permissions: {path} ({oct(permissions & 0o777)})")
        return
    if not stat.S_ISREG(mode):
        raise SystemExit(f"archive contains unsupported entry type: {path}")

    if permissions & 0o7000:
        raise SystemExit(f"archive file has disallowed setuid/setgid/sticky bits: {path}")
    if permissions & 0o022:
        raise SystemExit(f"archive file is group/world writable: {path}")

    file_permissions = permissions & 0o777
    if file_permissions & 0o111:
        if file_permissions != 0o755:
            raise SystemExit(
                "archive executable file has disallowed permissions "
                f"({oct(file_permissions)}): {path}"
            )
    elif file_permissions > 0o644:
        raise SystemExit(
            f"archive non-executable file has disallowed permissions ({oct(file_permissions)}): {path}"
        )


package_root = Path(sys.argv[1])
validate_mode(package_root)
for child in package_root.rglob("*"):
  validate_mode(child)
PY

if grep -Fq 'command -v -- python3' "${package_dir}/scripts/safe-local-fs.py"; then
  printf 'archive backend wrapper helper must not resolve python3 through PATH at runtime.\n' >&2
  exit 1
fi
if ! grep -Fq 'python_executable = _validate_absolute(args.python_executable, "python executable path")' "${package_dir}/scripts/safe-local-fs.py" \
  || ! grep -Fq 'write_wrapper.add_argument("python_executable")' "${package_dir}/scripts/safe-local-fs.py" \
  || ! grep -Fq ' -m speed_of_cinnamon.cli \"$@\"' "${package_dir}/scripts/safe-local-fs.py"; then
  printf 'archive backend wrapper helper does not invoke the expected CLI module.\n' >&2
  exit 1
fi

for path in \
  README.md \
  LICENSE \
  RELEASE-MANIFEST.txt \
  Makefile \
  pyproject.toml \
  packaging/speed-of-cinnamon.spec \
  docs/architecture.md \
  docs/cli-reference.md \
  docs/development.md \
  docs/fedora-cinnamon-runbook.md \
  docs/man/speed-of-cinnamon.1 \
  docs/man/speed-of-cinnamon-alarms.1 \
  docs/user-guide.md \
  docs/wiki/Home.md \
  files/speed-of-cinnamon@H234598/applet.js \
  files/speed-of-cinnamon@H234598/metadata.json \
  files/speed-of-cinnamon@H234598/settings-schema.json \
  scripts/install-local.sh \
  scripts/local-model-e2e-acceptance.sh \
  scripts/real-e2e-acceptance.sh \
  scripts/safe-local-fs.py \
  scripts/verify-local-model-e2e-attestation.sh \
  scripts/verify-real-e2e-attestation.sh \
  scripts/publish-github-release.sh \
  scripts/verify-authorship.sh \
  scripts/verify-rpm.sh \
  src/speed_of_cinnamon/alarms.py \
  src/speed_of_cinnamon/cli.py \
  src/speed_of_cinnamon/setup_plan.py \
  tests/test_alarms.py \
  tests/test_ci_static.py \
  tests/test_cli.py
do
  if [[ ! -e "${package_dir}/${path}" ]]; then
    printf 'archive is missing %s\n' "${path}" >&2
    exit 1
  fi
done

python3 -m compileall -q "${package_dir}"

printf 'Verified %s\n' "${tarball}"
