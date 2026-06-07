#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

for tool in python3 tar sha256sum mktemp find git stat realpath; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done

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
dist_finalize_lock="${dist_dir}/.build-dist.finalize.lock"
cleanup() {
  if [[ -n "${staging_tarball}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-dist "${staging_tarball}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${staging_checksum}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-dist "${staging_checksum}" >/dev/null 2>&1 || true
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

replace_with_finalize_lock() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3
  local staging_checksum_path=$4
  local final_checksum_path=$5

  python3 - "$lock_path" "$safe_fs" "$staging_path" "$final_path" "$staging_checksum_path" "$final_checksum_path" <<'PY'
import os
import subprocess
import sys

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path, staging_checksum_path, final_checksum_path = sys.argv[1:]

if os.path.islink(lock_path):
    print(f"finalization lock must not be a symlink: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

flags = os.O_CREAT | os.O_RDWR
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW

lock_fd = os.open(lock_path, flags, 0o600)
with os.fdopen(lock_fd, "r+", encoding="utf-8") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    if staging_path and final_path:
        subprocess.run(
            [sys.executable, safe_fs, "replace", "build-dist", staging_path, final_path, "--src-kind", "file"],
            check=True,
        )
    if staging_checksum_path and final_checksum_path:
        subprocess.run(
            [sys.executable, safe_fs, "replace", "build-dist", staging_checksum_path, final_checksum_path, "--src-kind", "file"],
            check=True,
        )
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

cat > "${work_dir}/${package}/RELEASE-MANIFEST.txt" <<EOF
${package}

Contains:
- Cinnamon applet files under files/speed-of-cinnamon@H234598/
- Python backend under src/speed_of_cinnamon/
- local build, verify, install, uninstall, and dependency scripts under scripts/
- tests, CI workflow, README, license, and docs
EOF

final_tarball="${dist_dir}/${package}.tar.gz"
final_checksum="${final_tarball}.sha256"
staging_tarball="$(mktemp "${dist_dir}/.${package}.tar.gz.XXXXXX")"

tar --sort=name --owner=0 --group=0 --numeric-owner --mtime="@0" -C "${work_dir}" -czf "${staging_tarball}" "${package}"
fsync_regular_file "${staging_tarball}" "staged dist tarball"
checksum_value="$(sha256sum "${staging_tarball}")"
checksum_value="${checksum_value%% *}"
staging_checksum="$(mktemp "${dist_dir}/.${package}.tar.gz.sha256.XXXXXX")"
printf '%s  %s\n' "${checksum_value}" "${package}.tar.gz" > "${staging_checksum}"
fsync_regular_file "${staging_checksum}" "staged dist checksum"
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
