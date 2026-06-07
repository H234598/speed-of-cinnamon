#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

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

for tool in realpath stat tar awk mktemp find grep python3; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done

if [[ -L "${safe_fs}" || ! -f "${safe_fs}" || "$(stat -c '%F' "${safe_fs}")" != "regular file" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
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
cleanup_tmpdir() {
  "${safe_fs_cmd[@]}" remove verify-dist "${tmp_dir}" --kind dir >/dev/null 2>&1 || true
}
trap cleanup_tmpdir EXIT

tarball_snapshot="${tmp_dir}/speed-of-cinnamon-verify.tar.gz"
if ! "${safe_fs_cmd[@]}" copy-file verify-dist "${tarball}" "${tarball_snapshot}" 0644; then
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

if ! tar -tzf "${tarball_snapshot}" | awk -F'/' '
  /(^|\/)\.\.(\/|$)/ || /^\// { print; bad = 1 }
  END { exit bad ? 1 : 0 }
' > /dev/null; then
  printf 'archive contains unsafe path entries (path traversal or absolute path): %s\n' "${tarball}" >&2
  exit 1
fi

python3 - "$tarball_snapshot" "$tmp_dir" <<'PY'
import os
import pathlib
import tarfile
import sys

tarball_snapshot = sys.argv[1]
target = pathlib.Path(sys.argv[2])
target.mkdir(parents=True, exist_ok=True)
target_root = target.resolve(strict=True)


def member_target(member_name):
    path = target / member_name
    if not path.resolve(strict=False).is_relative_to(target_root):
        raise SystemExit(f"dist archive path escapes target: {member_name}")
    return path

with tarfile.open(tarball_snapshot, "r:gz") as archive:
    package_root = None
    for member in archive.getmembers():
        if (
            "\x00" in member.name
            or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in member.name)
            or any(0xDC80 <= ord(char) <= 0xDCFF for char in member.name)
        ):
            raise SystemExit(f"dist archive contains unsafe path entry: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"dist archive contains unsupported entry type: {member.name}")
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
  scripts/safe-local-fs.py \
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
