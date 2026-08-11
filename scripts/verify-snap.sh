#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"
readonly MAX_SNAP_ARCHIVE_BYTES=$((512 * 1024 * 1024))
readonly MAX_SNAP_ENTRIES=10000
readonly MAX_SNAP_PATH_CHARS=320
readonly MAX_SNAP_PATH_DEPTH=40
readonly MAX_SNAP_FILE_BYTES=$((128 * 1024 * 1024))
readonly MAX_SNAP_TOTAL_FILE_BYTES=$((1024 * 1024 * 1024))

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
if [[ "$(stat -c '%h' "${safe_fs}")" -ne 1 ]]; then
  printf 'safe local filesystem helper must not be hardlinked: %s\n' "${safe_fs}" >&2
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

snap_filename="$(basename "${absolute}")"
if [[ ! "${snap_filename}" =~ ^speed-of-cinnamon_([^_]+)_[^/]+\.snap$ ]]; then
  printf 'unexpected snap file name: %s\n' "${snap_path}" >&2
  exit 1
fi
snap_filename_version="${BASH_REMATCH[1]}"

size="$(wc -c < "${absolute}")"
if [[ "${size}" -le 0 ]]; then
  printf 'snap file is empty: %s\n' "${snap_path}" >&2
  exit 1
fi
if [[ "${size}" -gt "${MAX_SNAP_ARCHIVE_BYTES}" ]]; then
  printf 'snap file is too large: %s bytes\n' "${size}" >&2
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
tmp_dir_identity=""
cleanup_tmpdir() {
  if [[ -n "${tmp_dir_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove verify-snap "${tmp_dir}" --kind dir \
      --expected-identity "${tmp_dir_identity}" >/dev/null 2>&1 || true
  else
    printf 'refusing snap verification cleanup without verified identity: %s\n' "${tmp_dir}" >&2
  fi
}
trap cleanup_tmpdir EXIT

if ! tmp_dir_identity="$("${safe_fs_cmd[@]}" identity verify-snap "${tmp_dir}" --kind dir)"; then
  printf 'failed to capture snap verification directory identity: %s\n' "${tmp_dir}" >&2
  exit 1
fi

snap_snapshot="${tmp_dir}/speed-of-cinnamon-verify.snap"
if ! "${safe_fs_cmd[@]}" copy-file verify-snap "${absolute}" "${snap_snapshot}" 0644 \
  --max-bytes "${MAX_SNAP_ARCHIVE_BYTES}"; then
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
snapshot_size="$(wc -c < "${snap_snapshot}")"
if [[ "${snapshot_size}" -le 0 || "${snapshot_size}" -gt "${MAX_SNAP_ARCHIVE_BYTES}" ]]; then
  printf 'snap snapshot has invalid size: %s bytes\n' "${snapshot_size}" >&2
  exit 1
fi
size="${snapshot_size}"

snap_listing="${tmp_dir}/snap-listing.txt"
unsquashfs -lln -no-progress "${snap_snapshot}" > "${snap_listing}"
python3 - <<'PY' "${snap_listing}" "${MAX_SNAP_ENTRIES}" "${MAX_SNAP_PATH_CHARS}" "${MAX_SNAP_PATH_DEPTH}" "${MAX_SNAP_FILE_BYTES}" "${MAX_SNAP_TOTAL_FILE_BYTES}"
from pathlib import PurePosixPath
from pathlib import Path
import posixpath
import sys

MAX_SNAP_ENTRIES = int(sys.argv[2])
MAX_SNAP_PATH_CHARS = int(sys.argv[3])
MAX_SNAP_PATH_DEPTH = int(sys.argv[4])
MAX_SNAP_FILE_BYTES = int(sys.argv[5])
MAX_SNAP_TOTAL_FILE_BYTES = int(sys.argv[6])
REQUIRED_ENTRIES = {
    "squashfs-root/meta/snap.yaml",
    "squashfs-root/bin/speed-of-cinnamon",
    "squashfs-root/src/speed_of_cinnamon/cli.py",
}
REQUIRED_RUNTIME_ENTRIES = {
    "squashfs-root/usr/bin/python3",
    "squashfs-root/usr/bin/secret-tool",
    "squashfs-root/usr/lib/python3/dist-packages/cryptography/__init__.py",
}
REQUIRED_REGULAR_ENTRIES = {
    "squashfs-root/meta/snap.yaml",
    "squashfs-root/bin/speed-of-cinnamon",
    "squashfs-root/src/speed_of_cinnamon/cli.py",
    "squashfs-root/usr/bin/secret-tool",
    "squashfs-root/usr/lib/python3/dist-packages/cryptography/__init__.py",
}
REQUIRED_EXECUTABLE_ENTRIES = {
    "squashfs-root/bin/speed-of-cinnamon",
    "squashfs-root/usr/bin/secret-tool",
}


def symbolic_mode_to_octal(mode_text: str) -> int:
    if len(mode_text) != 10:
        raise SystemExit(f"snap package contains malformed mode: {mode_text!r}")
    if mode_text[0] not in {"d", "-", "l"}:
        raise SystemExit(f"snap package contains unsupported entry type marker: {mode_text!r}")

    mode = 0

    perms = mode_text[1:]
    read_write_execute = (
        (perms[0:3], (0o400, 0o200, 0o100), (0o4000, 0o0, 0o4000)),
        (perms[3:6], (0o40, 0o20, 0o10), (0o2000, 0o0, 0o2000)),
        (perms[6:9], (0o4, 0o2, 0o1), (0o0, 0o0, 0o1000)),
    )
    for chunk, permissions, specials in read_write_execute:
        read_char, write_char, exec_char = chunk
        if read_char not in {"r", "-"}:
            raise SystemExit(f"snap package contains malformed mode: {mode_text!r}")
        if write_char not in {"w", "-"}:
            raise SystemExit(f"snap package contains malformed mode: {mode_text!r}")
        mode |= permissions[0] if read_char == "r" else 0
        mode |= permissions[1] if write_char == "w" else 0

        if exec_char == "x":
            mode |= permissions[2]
        elif exec_char == "s":
            mode |= permissions[2]
            mode |= specials[0]
        elif exec_char == "S":
            mode |= specials[0]
        elif exec_char == "-":
            pass
        elif exec_char == "t":
            mode |= permissions[2]
            mode |= specials[2]
        elif exec_char == "T":
            mode |= specials[2]
        else:
            raise SystemExit(f"snap package contains malformed mode: {mode_text!r}")

    return mode


def enforce_mode_policy(path: PurePosixPath, mode_text: str, is_link: bool) -> None:
    # Fail-closed policy: reject setuid/setgid/sticky and all group/world writable bits.
    # Non-executable files may be at most 0644, executable regular files must be 0755.
    # Directories are limited to at most 0755.
    if is_link:
        return

    mode = symbolic_mode_to_octal(mode_text)
    permissions = mode & 0o777

    if mode & 0o7000:
        raise SystemExit(f"snap package contains entry with setuid/setgid/sticky bits: {path}")
    if mode & 0o022:
        raise SystemExit(f"snap package contains group/world-writable entry: {path}")

    if mode_text[0] == "d":
        if permissions > 0o755:
            raise SystemExit(
                f"snap package directory has disallowed permissions ({oct(permissions)}): {path}"
            )
        return

    if permissions & 0o111:
        if permissions != 0o755:
            raise SystemExit(
                f"snap package executable file has disallowed permissions ({oct(permissions)}): {path}"
            )
    else:
        if permissions > 0o644:
            raise SystemExit(
                f"snap package non-executable file has disallowed permissions ({oct(permissions)}): {path}"
            )


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


seen: dict[str, str] = {}
entry_count = 0
total_file_bytes = 0
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").split("\n"):
    if not raw:
        continue
    entry_count += 1
    if entry_count > MAX_SNAP_ENTRIES:
        raise SystemExit("snap package contains too many entries")
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
    validated_path = validate_snap_path(path_text)
    enforce_mode_policy(validated_path, mode, link_target is not None)
    if link_target is not None:
        validate_symlink_target(validated_path, link_target)
    if path_text in seen:
        raise SystemExit(f"snap package contains duplicate entry: {path_text}")
    if len(path_text) > MAX_SNAP_PATH_CHARS:
        raise SystemExit(f"snap package contains path that is too long: {path_text}")
    if len([part for part in validated_path.parts if part]) > MAX_SNAP_PATH_DEPTH:
        raise SystemExit(f"snap package contains path that is too deep: {path_text}")
    if (
        path_text.endswith("/__pycache__")
        or "/__pycache__/" in path_text
        or path_text.endswith(".pyc")
        or path_text.endswith(".pyo")
    ):
        raise SystemExit(f"snap package contains stale Python bytecode: {path_text}")
    seen[path_text] = mode
    try:
        size = int(size_text)
    except ValueError:
        raise SystemExit(f"snap package contains malformed entry size for {path_text}: {size_text}") from None
    if size < 0:
        raise SystemExit(f"snap package contains negative entry size for {path_text}: {size_text}")
    if mode[0] == "-":
        if size > MAX_SNAP_FILE_BYTES:
            raise SystemExit(f"snap package contains oversized file: {path_text}")
        total_file_bytes += size
        if total_file_bytes > MAX_SNAP_TOTAL_FILE_BYTES:
            raise SystemExit("snap package file size budget exceeded")

missing = REQUIRED_ENTRIES - seen.keys()
if missing:
    raise SystemExit(f"snap package is missing required entries: {sorted(missing)}")
missing_runtime = REQUIRED_RUNTIME_ENTRIES - seen.keys()
if missing_runtime:
    raise SystemExit(f"snap package is missing required runtime entries: {sorted(missing_runtime)}")

for required_entry in REQUIRED_REGULAR_ENTRIES:
    if seen[required_entry][0] != "-":
        raise SystemExit(f"snap package required entry is not regular file: {required_entry} ({seen[required_entry]})")
for required_entry in REQUIRED_EXECUTABLE_ENTRIES:
    if symbolic_mode_to_octal(seen[required_entry]) != 0o755:
        raise SystemExit(f"snap package required executable has disallowed permissions: {required_entry}")
PY

snap_yaml="${tmp_dir}/snap.yaml"
snap_backend="${tmp_dir}/speed-of-cinnamon"
unsquashfs -cat "${snap_snapshot}" meta/snap.yaml > "${snap_yaml}"
unsquashfs -cat "${snap_snapshot}" bin/speed-of-cinnamon > "${snap_backend}"
python3 - "${snap_yaml}" "${snap_filename_version}" <<'PY'
from pathlib import Path
import sys

snap_yaml_path, expected_version = sys.argv[1:]
fields: dict[str, str] = {}
for raw_line in Path(snap_yaml_path).read_text(encoding="utf-8").split("\n"):
    if raw_line.startswith("name:") or raw_line.startswith("version:"):
        key, raw_value = raw_line.split(":", 1)
        if key in fields:
            raise SystemExit(f"snap metadata contains duplicate {key} field")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        fields[key] = value

if fields.get("name") != "speed-of-cinnamon":
    raise SystemExit("snap metadata name does not match speed-of-cinnamon")
if fields.get("version") != expected_version:
    raise SystemExit("snap metadata version does not match snap filename")
PY
if ! grep -Fq 'src/speed_of_cinnamon/cli.py' "${snap_backend}" \
  && ! grep -Eq 'exec[[:space:]].*-m[[:space:]]+speed_of_cinnamon[.]cli([[:space:]]|$)' "${snap_backend}"; then
  printf '%s\n' 'snap launcher does not target speed_of_cinnamon.cli' >&2
  cat "${snap_backend}" >&2
  exit 1
fi

printf 'Verified snap package: %s (%s bytes)\n' "${absolute}" "${size}"
