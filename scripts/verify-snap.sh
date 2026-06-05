#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
snap_dir="${repo_dir}/dist/snap"
if [[ -L "${snap_dir}" ]]; then
  printf 'snap directory must not be a symlink: %s\n' "${snap_dir}" >&2
  exit 1
fi
if [[ ! -d "${snap_dir}" ]]; then
  printf 'snap directory not found: %s\n' "${snap_dir}" >&2
  exit 1
fi

require_cmd() {
  local tool=$1
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

require_cmd realpath
require_cmd stat
require_cmd wc
require_cmd grep
require_cmd basename
require_cmd mkdir
require_cmd mktemp
require_cmd python3
require_cmd unsquashfs

safe_fs="${repo_dir}/scripts/safe-local-fs.py"
if [[ -L "${safe_fs}" || ! -f "${safe_fs}" || "$(stat -c '%F' "${safe_fs}")" != "regular file" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
  exit 1
fi
safe_fs_cmd=(python3 "${safe_fs}")

if [[ $# -lt 1 ]]; then
  printf 'usage: %s <snap-path>\n' "$0" >&2
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

snap_path="$1"

if contains_control_chars "${snap_path}"; then
  printf 'snap file path contains control characters\n' >&2
  exit 1
fi
if [[ ! -f "${snap_path}" ]]; then
  printf 'snap file not found: %s\n' "${snap_path}" >&2
  exit 1
fi

if [[ -L "${snap_path}" ]]; then
  printf 'snap file must not be a symlink: %s\n' "${snap_path}" >&2
  exit 1
fi
if [[ "$(stat -c '%F' "${snap_path}")" != "regular file" ]]; then
  printf 'snap file must be a regular file: %s\n' "${snap_path}" >&2
  exit 1
fi
link_count="$(stat -c '%h' "${snap_path}")"
if [[ "${link_count}" -ne 1 ]]; then
  printf 'snap file must not be hardlinked: %s\n' "${snap_path}" >&2
  exit 1
fi

if ! absolute="$(realpath "${snap_path}" 2>/dev/null)"; then
  printf 'failed to resolve snap path\n' >&2
  exit 1
fi
if [[ "${absolute}" != "${snap_dir}/"* ]]; then
  printf 'snap file is outside repository root: %s\n' "${snap_path}" >&2
  exit 1
fi

if [[ ! "$(basename "${absolute}")" == speed-of-cinnamon_*_*.snap ]]; then
  printf 'unexpected snap file name: %s\n' "${snap_path}" >&2
  exit 1
fi

size="$(wc -c < "${absolute}")"
if [[ "${size}" -le 0 ]]; then
  printf 'snap file is empty: %s\n' "${snap_path}" >&2
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
if ! tmp_root="$(realpath "${tmp_root}" 2>/dev/null)"; then
  printf 'failed to resolve temporary root\n' >&2
  exit 1
fi
mkdir -p "${tmp_root}"

tmp_dir="$(mktemp -d "${tmp_root}/speed-of-cinnamon-snap-verify-XXXXXX")"
if [[ -L "${tmp_dir}" ]]; then
  printf 'temporary snap verification directory must not be a symlink: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if ! tmp_dir_abs="$(realpath "${tmp_dir}")"; then
  printf 'failed to resolve temporary snap verification directory: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if [[ "${tmp_dir_abs}" != "${tmp_root}/speed-of-cinnamon-snap-verify-"* ]]; then
  printf 'temporary snap verification directory escaped temporary root: %s\n' "${tmp_dir}" >&2
  exit 1
fi
tmp_dir="${tmp_dir_abs}"
cleanup_tmpdir() {
  "${safe_fs_cmd[@]}" remove verify-snap "${tmp_dir}" --kind dir >/dev/null 2>&1 || true
}
trap cleanup_tmpdir EXIT

snap_snapshot="${tmp_dir}/speed-of-cinnamon-verify.snap"
if ! "${safe_fs_cmd[@]}" copy-file verify-snap "${absolute}" "${snap_snapshot}" 0644; then
  printf 'failed to snapshot snap package for verification: %s\n' "${absolute}" >&2
  exit 1
fi
if [[ -L "${snap_snapshot}" || ! -f "${snap_snapshot}" || "$(stat -c '%F' "${snap_snapshot}")" != "regular file" ]]; then
  printf 'snap snapshot must be a regular file: %s\n' "${snap_snapshot}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${snap_snapshot}")" -ne 1 ]]; then
  printf 'snap snapshot must not be hardlinked: %s\n' "${snap_snapshot}" >&2
  exit 1
fi

snap_listing="${tmp_dir}/snap-listing.txt"
unsquashfs -lln -no-progress "${snap_snapshot}" > "${snap_listing}"
python3 - <<'PY' "${snap_listing}"
from pathlib import PurePosixPath
from pathlib import Path
import posixpath
import sys

REQUIRED_ENTRIES = {
    "squashfs-root/meta/snap.yaml",
    "squashfs-root/bin/speed-of-cinnamon",
    "squashfs-root/src/speed_of_cinnamon/cli.py",
}


def contains_unsafe_text(value: str) -> bool:
    return (
        "\x00" in value
        or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)
        or any(0xDC80 <= ord(char) <= 0xDCFF for char in value)
    )


def validate_snap_path(path_text: str) -> PurePosixPath:
    if contains_unsafe_text(path_text):
        raise SystemExit(f"snap package contains unsafe path entry: {path_text!r}")
    if path_text != "squashfs-root" and not path_text.startswith("squashfs-root/"):
        raise SystemExit(f"snap package contains unexpected root entry: {path_text}")
    path = PurePosixPath(path_text)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise SystemExit(f"snap package contains unsafe path entry: {path_text}")
    return path


def validate_symlink_target(path: PurePosixPath, target_text: str) -> None:
    if not target_text or contains_unsafe_text(target_text):
        raise SystemExit(f"snap package contains unsafe link target: {path} -> {target_text!r}")
    target = PurePosixPath(target_text)
    if target.is_absolute():
        raise SystemExit(f"snap package contains unsafe link target: {path} -> {target_text}")
    resolved = posixpath.normpath(posixpath.join(str(path.parent), target_text))
    if resolved != "squashfs-root" and not resolved.startswith("squashfs-root/"):
        raise SystemExit(f"snap package contains unsafe link target: {path} -> {target_text}")


seen: set[str] = set()
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").split("\n"):
    if not raw:
        continue
    parts = raw.split(maxsplit=5)
    if len(parts) != 6:
        raise SystemExit(f"snap package contains malformed listing entry: {raw!r}")
    mode, _owner_group, size_text, _date, _time, path_text = parts
    link_target = None
    if " -> " in path_text:
        path_text, link_target = path_text.split(" -> ", 1)
        if not mode or mode[0] != "l":
            raise SystemExit(f"snap package contains unsupported link entry: {path_text} -> {link_target}")
    elif mode and mode[0] == "l":
        raise SystemExit(f"snap package contains malformed link entry: {path_text}")
    if link_target is None and (not mode or mode[0] not in {"-", "d"}):
        raise SystemExit(f"snap package contains unsupported entry type: {path_text}")
    path = validate_snap_path(path_text)
    if link_target is not None:
        validate_symlink_target(path, link_target)
    if path_text in seen:
        raise SystemExit(f"snap package contains duplicate entry: {path_text}")
    seen.add(path_text)
    try:
        size = int(size_text)
    except ValueError:
        raise SystemExit(f"snap package contains malformed entry size for {path_text}: {size_text}") from None
    if size < 0:
        raise SystemExit(f"snap package contains negative entry size for {path_text}: {size_text}")

missing = REQUIRED_ENTRIES - seen
if missing:
    raise SystemExit(f"snap package is missing required entries: {sorted(missing)}")
PY

snap_yaml="${tmp_dir}/snap.yaml"
snap_backend="${tmp_dir}/speed-of-cinnamon"
unsquashfs -cat "${snap_snapshot}" meta/snap.yaml > "${snap_yaml}"
unsquashfs -cat "${snap_snapshot}" bin/speed-of-cinnamon > "${snap_backend}"
grep -Fxq 'name: speed-of-cinnamon' "${snap_yaml}"
grep -Fq 'src/speed_of_cinnamon/cli.py' "${snap_backend}"

printf 'Verified snap package: %s (%s bytes)\n' "${absolute}" "${size}"
