#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

for tool in python3 tar sha256sum mktemp find git stat realpath; do
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
if [[ ! "${name}" == "speed-of-cinnamon" ]]; then
  printf 'unexpected package name: %s\n' "${name}" >&2
  exit 1
fi
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

package="${name}-${version}"
dist_dir="${repo_dir}/dist"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
work_root="${TMPDIR:-/tmp}"
if [[ ! "${work_root}" == /* ]]; then
  printf 'temporary root must be an absolute path: %s\n' "${work_root}" >&2
  exit 1
fi
if [[ -L "${work_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${work_root}" >&2
  exit 1
fi
if [[ ! -d "${work_root}" || ! -w "${work_root}" ]]; then
  printf 'temporary root is not a writable directory: %s\n' "${work_root}" >&2
  exit 1
fi
if ! work_root="$(realpath "${work_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${work_root}" >&2
  exit 1
fi
mkdir -p "${work_root}"
work_dir="$(mktemp -d "${work_root}/speed-of-cinnamon-build-dist-XXXXXX")"
if [[ -L "${work_dir}" ]]; then
  printf 'temporary build-dist workspace must not be a symlink: %s\n' "${work_dir}" >&2
  exit 1
fi
if ! work_dir_abs="$(realpath "${work_dir}")"; then
  printf 'failed to resolve temporary build-dist workspace: %s\n' "${work_dir}" >&2
  exit 1
fi
if [[ "${work_dir_abs}" != "${work_root}/speed-of-cinnamon-build-dist-"* ]]; then
  printf 'temporary build-dist workspace escaped temporary root: %s\n' "${work_dir}" >&2
  exit 1
fi
work_dir="${work_dir_abs}"
staging_tarball=""
staging_checksum=""
dist_staging_dir=""
dist_finalize_lock="${dist_dir}/.build-dist.finalize.lock"
cleanup() {
  if [[ -n "${staging_tarball}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-dist "${staging_tarball}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${staging_checksum}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-dist "${staging_checksum}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${dist_staging_dir}" ]]; then
    "${safe_fs_cmd[@]}" remove build-dist "${dist_staging_dir}" --kind dir >/dev/null 2>&1 || true
  fi
  if [[ -n "${work_dir}" ]]; then
    "${safe_fs_cmd[@]}" remove build-dist "${work_dir}" --kind dir >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

fsync_regular_file() {
  local path=$1
  local label=$2
  python3 - "$path" "$label" <<'PY'
import os
import stat
import sys

path, label = sys.argv[1:]
flags = os.O_RDONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
except OSError as exc:
    print(f"failed to open {label} for fsync: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        print(f"{label} must be a regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

write_regular_file_from_stdin() {
  local path=$1
  local label=$2

  python3 -c '
import os
import stat
import sys

path, label = sys.argv[1:3]
flags = os.O_WRONLY | os.O_CREAT
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags, 0o600)
except OSError as exc:
    print(f"failed to open {label} for writing: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        print(f"{label} must be a regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    if getattr(file_stat, "st_nlink", 1) != 1:
        print(f"{label} must not be hardlinked: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.ftruncate(fd, 0)
    while True:
        chunk = sys.stdin.buffer.read(1024 * 1024)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                print(f"failed to write {label}: {path}", file=sys.stderr)
                raise SystemExit(1)
            view = view[written:]
    os.fsync(fd)
finally:
    os.close(fd)
' "${path}" "${label}"
}

replace_with_finalize_lock() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3
  local staging_checksum_path=$4
  local final_checksum_path=$5

  python3 - "$lock_path" "$safe_fs" "$staging_path" "$final_path" "$staging_checksum_path" "$final_checksum_path" <<'PY'
import os
import secrets
import subprocess
import stat
import sys

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path, staging_checksum_path, final_checksum_path = sys.argv[1:]
lock_parent = os.path.dirname(lock_path)
lock_name = os.path.basename(lock_path)


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _regular_file(path, label, *, required):
    path_stat = _lstat(path)
    if path_stat is None:
        if required:
            raise RuntimeError(f"{label} is missing: {path}")
        return None
    if stat.S_ISLNK(path_stat.st_mode):
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a regular file: {path}")
    if getattr(path_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{label} must not be hardlinked: {path}")
    return path_stat


def _run_safe_fs(*arguments):
    subprocess.run([sys.executable, safe_fs, *arguments], check=True)


def _file_identity(path_stat):
    return (path_stat.st_dev, path_stat.st_ino)


def _new_backup_path(final_path):
    for _ in range(16):
        backup_path = f"{final_path}.{os.getpid()}.{secrets.token_hex(8)}.backup"
        if _lstat(backup_path) is None:
            return backup_path
    raise RuntimeError(f"could not allocate a free backup path for {final_path}")


def _rollback(entries):
    rollback_errors = []
    for entry in reversed(entries):
        try:
            if entry["backup_attempted"]:
                backup_stat = _lstat(entry["backup"])
                if backup_stat is not None:
                    if _file_identity(backup_stat) != entry["final_identity"]:
                        raise RuntimeError(
                            f"refusing to restore changed backup during rollback: {entry['backup']}"
                        )
                    _run_safe_fs(
                        "replace",
                        "build-dist",
                        entry["backup"],
                        entry["final"],
                        "--src-kind",
                        "file",
                    )
                    entry["backup_created"] = False
                else:
                    final_stat = _lstat(entry["final"])
                    if entry["backup_created"] or (
                        final_stat is None or _file_identity(final_stat) != entry["final_identity"]
                    ):
                        raise RuntimeError(f"backup disappeared during rollback: {entry['backup']}")
            elif entry["activation_attempted"] and not entry["had_existing"]:
                current = _lstat(entry["final"])
                if current is not None:
                    if _file_identity(current) != entry["staging_identity"]:
                        raise RuntimeError(
                            f"refusing to remove changed final file during rollback: {entry['final']}"
                        )
                    _run_safe_fs("remove-leaf", "build-dist", entry["final"])
        except BaseException as rollback_exc:
            rollback_errors.append(f"{entry['final']}: {rollback_exc}")
    return rollback_errors

if not lock_name:
    print(f"finalization lock path is invalid: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

parent_flags = os.O_RDONLY
if hasattr(os, "O_DIRECTORY"):
    parent_flags |= os.O_DIRECTORY
if hasattr(os, "O_NOFOLLOW"):
    parent_flags |= os.O_NOFOLLOW
try:
    parent_fd = os.open(lock_parent, parent_flags)
except OSError as exc:
    print(f"failed to open finalization lock parent safely: {lock_parent}: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    parent_stat = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_stat.st_mode):
        print(f"finalization lock parent must be a directory: {lock_parent}", file=sys.stderr)
        raise SystemExit(1)
    try:
        lock_stat = os.stat(lock_name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        lock_stat = None
    if lock_stat is not None:
        if stat.S_ISLNK(lock_stat.st_mode):
            print(f"finalization lock must not be a symlink: {lock_path}", file=sys.stderr)
            raise SystemExit(1)
        if not stat.S_ISREG(lock_stat.st_mode):
            print(f"finalization lock must be a regular file: {lock_path}", file=sys.stderr)
            raise SystemExit(1)
        if getattr(lock_stat, "st_nlink", 1) != 1:
            print(f"finalization lock must not be hardlinked: {lock_path}", file=sys.stderr)
            raise SystemExit(1)

    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    lock_fd = os.open(lock_name, flags, 0o600, dir_fd=parent_fd)
    with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        pair_specs = (
            (staging_path, final_path, "dist tarball"),
            (staging_checksum_path, final_checksum_path, "dist checksum"),
        )
        entries = []
        for staging, final, label in pair_specs:
            if bool(staging) != bool(final):
                raise RuntimeError(f"incomplete {label} finalization pair")
            if not staging:
                continue
            staging_stat = _regular_file(staging, f"staged {label}", required=True)
            final_stat = _regular_file(final, f"existing {label}", required=False)
            entries.append(
                {
                    "backup": _new_backup_path(final),
                    "backup_attempted": False,
                    "backup_created": False,
                    "activation_attempted": False,
                    "final": final,
                    "final_identity": _file_identity(final_stat) if final_stat is not None else None,
                    "had_existing": final_stat is not None,
                    "staging": staging,
                    "staging_identity": _file_identity(staging_stat),
                }
            )

        try:
            for entry in entries:
                if entry["had_existing"]:
                    entry["backup_attempted"] = True
                    _run_safe_fs(
                        "replace",
                        "build-dist",
                        entry["final"],
                        entry["backup"],
                        "--src-kind",
                        "file",
                        "--dst-must-not-exist",
                    )
                    entry["backup_created"] = True
            for entry in entries:
                entry["activation_attempted"] = True
                _run_safe_fs(
                    "replace",
                    "build-dist",
                    entry["staging"],
                    entry["final"],
                    "--src-kind",
                    "file",
                )
        except BaseException as exc:
            rollback_errors = _rollback(entries)
            if rollback_errors:
                exc.add_note("finalization rollback failed: " + "; ".join(rollback_errors))
            raise

        # Cleanup failures leave a complete new archive/checksum pair active;
        # keep any remaining backup as a recovery copy rather than rolling back
        # only one member of the pair.
        for entry in entries:
            if entry["backup_created"]:
                _run_safe_fs("remove-leaf", "build-dist", entry["backup"])
                entry["backup_created"] = False
finally:
    os.close(parent_fd)
PY
}

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
  source_path="${repo_dir}/${path}"
  target_path="${work_dir}/${package}/${path}"
  if [[ -d "${source_path}" ]]; then
    if ! python3 "${safe_fs}" install-tree build-dist "${source_path}" "${target_path}" "distribution source tree"; then
      printf 'failed to copy distribution source tree: %s\n' "${source_path}" >&2
      exit 1
    fi
  else
    if ! python3 "${safe_fs}" copy-file build-dist "${source_path}" "${target_path}" 0644; then
      printf 'failed to copy distribution source file: %s\n' "${source_path}" >&2
      exit 1
    fi
  fi
done

while IFS= read -r -d '' cache_dir; do
  "${safe_fs_cmd[@]}" remove build-dist "${cache_dir}" --kind dir
done < <(
  find "${work_dir}/${package}" \
    -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \) \
    -prune -print0
)
while IFS= read -r -d '' bytecode_file; do
  "${safe_fs_cmd[@]}" remove build-dist "${bytecode_file}" --kind file
done < <(find "${work_dir}/${package}" -type f \( -name '*.pyc' -o -name '*.pyo' \) -print0)

if find "${work_dir}/${package}" -type l -print -quit | grep -q .; then
  printf 'build-dist detected unsupported symlink in package contents.\n' >&2
  exit 1
fi

write_regular_file_from_stdin "${work_dir}/${package}/RELEASE-MANIFEST.txt" "release manifest" <<EOF
${package}

Contains:
- Cinnamon applet files under files/speed-of-cinnamon@H234598/
- Python backend under src/speed_of_cinnamon/
- local build, verify, install, uninstall, and dependency scripts under scripts/
- tests, CI workflow, README, license, and docs
EOF

final_tarball="${dist_dir}/${package}.tar.gz"
final_checksum="${final_tarball}.sha256"
dist_staging_dir="$(mktemp -d "${repo_dir}/.build-dist-staging-XXXXXX")"
if [[ -L "${dist_staging_dir}" ]]; then
  printf 'dist staging directory must not be a symlink: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
if ! dist_staging_dir_abs="$(realpath "${dist_staging_dir}")"; then
  printf 'failed to resolve dist staging directory: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
if [[ "${dist_staging_dir_abs}" != "${repo_dir}/.build-dist-staging-"* ]]; then
  printf 'dist staging directory escaped repository root: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
dist_staging_dir="${dist_staging_dir_abs}"
staging_tarball="$(mktemp "${dist_staging_dir}/.${package}.tar.gz.XXXXXX")"

tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@0" -C "${work_dir}" -czf - "${package}" \
  | write_regular_file_from_stdin "${staging_tarball}" "staged dist tarball"
fsync_regular_file "${staging_tarball}" "staged dist tarball"
checksum_value="$(sha256sum "${staging_tarball}")"
checksum_value="${checksum_value%% *}"
staging_checksum="$(mktemp "${dist_staging_dir}/.${package}.tar.gz.sha256.XXXXXX")"
printf '%s  %s\n' "${checksum_value}" "${package}.tar.gz" \
  | write_regular_file_from_stdin "${staging_checksum}" "staged dist checksum"
replace_with_finalize_lock \
  "${dist_finalize_lock}" \
  "${staging_tarball}" \
  "${final_tarball}" \
  "${staging_checksum}" \
  "${final_checksum}"
staging_tarball=""
staging_checksum=""

printf 'Built %s\n' "${final_tarball}" >&2
printf '%s\n' "${final_tarball}"
