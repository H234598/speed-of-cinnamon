#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:/var/lib/snapd/snap/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
snapcraft_base="${SNAPCRAFT_BASE:-core22}"
if [[ ! "${snapcraft_base}" =~ ^[a-z][a-z0-9-]*$ ]]; then
  printf 'invalid SNAPCRAFT_BASE value: %s\n' "${snapcraft_base}" >&2
  exit 1
fi

require_cmd() {
  local tool=$1
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

require_regular_source_file() {
  local path=$1
  local label=$2
  local link_count

  if [[ ! -f "${path}" || -L "${path}" ]]; then
    printf '%s must be a regular file: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${path}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf '%s must not be hardlinked: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
}

for tool in python3 snapcraft mktemp mkdir find realpath stat chmod grep sort basename; do
  require_cmd "${tool}"
done
snap_dir="${repo_dir}/snap"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

if ! snapcraft --version >/dev/null 2>&1; then
  printf 'snapcraft is installed but did not execute successfully.\n' >&2
  exit 1
fi

if [[ -L "${snap_dir}" ]]; then
  printf 'snap directory must not be a symlink: %s\n' "${snap_dir}" >&2
  exit 1
fi

if [[ ! -f "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest missing: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
if [[ -L "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest must not be a symlink: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
snapcraft_file="${snap_dir}/snapcraft.yaml"
require_regular_source_file "${snapcraft_file}" "snapcraft manifest"
require_regular_source_file "${safe_fs}" "safe local filesystem helper"

version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
PY
)"
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

repo_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${repo_tmp_root}" == /* ]]; then
  printf 'temporary root must be an absolute path: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if [[ -L "${repo_tmp_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if [[ ! -d "${repo_tmp_root}" || ! -w "${repo_tmp_root}" ]]; then
  printf 'temporary root is not a writable directory: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
if ! repo_tmp_abs="$(realpath "${repo_tmp_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
mkdir -p "${repo_tmp_root}"
if [[ "${repo_tmp_abs}" == "${repo_dir}" || "${repo_tmp_abs}" == "${repo_dir}/"* ]]; then
  printf 'snap temporary root must be outside repository: %s\n' "${repo_tmp_root}" >&2
  exit 1
fi
repo_tmp_root="${repo_tmp_abs}"

snap_workspace="$(mktemp -d "${repo_tmp_root}/speed-of-cinnamon-snap-tree-XXXXXX")"
if [[ -L "${snap_workspace}" ]]; then
  printf 'temporary snap workspace must not be a symlink: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! snap_workspace_abs="$(realpath "${snap_workspace}")"; then
  printf 'failed to resolve temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if [[ "${snap_workspace_abs}" != "${repo_tmp_root}/speed-of-cinnamon-snap-tree-"* ]]; then
  printf 'temporary snap workspace escaped temporary root: %s\n' "${snap_workspace}" >&2
  exit 1
fi
snap_workspace="${snap_workspace_abs}"
snapcraft_file_rendered="${snap_workspace}/snap/snapcraft.yaml"
snap_workspace_dist="${snap_workspace}/dist/snap"
tmp_output=""
cleanup_tmpdir() {
  if [[ -n "${tmp_output}" ]]; then
    "${safe_fs_cmd[@]}" remove-leaf build-snap "${tmp_output}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${snap_workspace}" ]]; then
    "${safe_fs_cmd[@]}" remove build-snap "${snap_workspace}" --kind dir >/dev/null 2>&1 || true
  fi
}
trap cleanup_tmpdir EXIT

if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/src" "${snap_workspace}/src" "Python source tree"; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_workspace}/pyproject.toml" 0644; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${repo_dir}/README.md" "${snap_workspace}/README.md" 0644; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" remove build-snap "${snap_workspace_dist}" --kind dir; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
mkdir -p "${snap_workspace_dist}"

python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}" <<'PYCODE'
import os
import pathlib
import secrets
import sys

path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
version = sys.argv[3]
base = sys.argv[4]
text = path.read_text(encoding="utf-8")
out = []
replaced = False
base_replaced = False
for line in text.splitlines():
    if line.startswith("version:"):
        out.append(f"version: \"{version}\"")
        replaced = True
    elif line.startswith("base:"):
        out.append(f"base: {base}")
        base_replaced = True
    else:
        out.append(line)
if not replaced:
    raise SystemExit("snapcraft version field not found")
if not base_replaced:
    raise SystemExit("snapcraft base field not found")
payload = ("\n".join(out) + "\n").encode("utf-8")
parent_fd = os.open(output_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
tmp_name = f".{output_path.name}.{secrets.token_hex(8)}.tmp"
fd = -1
try:
    fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        fd = -1
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
    os.replace(tmp_name, output_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
    tmp_name = ""
finally:
    if fd >= 0:
        os.close(fd)
    if tmp_name:
        try:
            os.unlink(tmp_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            pass
    os.close(parent_fd)
PYCODE
snapcraft_mode="$(stat -c '%a' "${snapcraft_file}")"
chmod "${snapcraft_mode}" "${snapcraft_file_rendered}"

if ! ( cd "${snap_workspace}" && umask 022 && snapcraft pack --destructive-mode ); then
  printf 'snapcraft build failed.\n' >&2
  exit 1
fi

dist_parent="${repo_dir}/dist"
if [[ -L "${dist_parent}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_parent}" >&2
  exit 1
fi
mkdir -p "${dist_parent}"
if [[ -L "${dist_parent}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_parent}" >&2
  exit 1
fi
dist_dir="${dist_parent}/snap"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
mkdir -p "${dist_dir}"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi

cleanup_existing_dist_snaps() {
  local cleanup_list
  local existing_snap
  local existing_real
  local -a existing_dist_snaps=()

  if find "${dist_dir}" -maxdepth 1 -name "speed-of-cinnamon_*.snap" ! -type f -print -quit | grep -q .; then
    printf 'refusing to clean non-regular snap artifact from output directory: %s\n' "${dist_dir}" >&2
    exit 1
  fi

  cleanup_list="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snap-cleanup-XXXXXX")"
  find "${dist_dir}" -maxdepth 1 -type f -name "speed-of-cinnamon_*.snap" -print0 | sort -z > "${cleanup_list}"
  mapfile -d '' -t existing_dist_snaps < "${cleanup_list}"
  "${safe_fs_cmd[@]}" remove build-snap "${cleanup_list}" --kind file

  for existing_snap in "${existing_dist_snaps[@]}"; do
    existing_real="$(realpath "${existing_snap}")"
    if [[ "${existing_real}" != "${dist_dir}/speed-of-cinnamon_"*".snap" ]]; then
      printf 'refusing to clean snap artifact outside output directory: %s\n' "${existing_snap}" >&2
      exit 1
    fi
    "${safe_fs_cmd[@]}" remove build-snap "${existing_snap}" --kind file
  done
}

cleanup_existing_dist_snaps

if find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -print -quit | grep -q .; then
  printf 'refusing to overwrite existing snap artifact for version %s\n' "${version}" >&2
  exit 1
fi

tmp_output="$(mktemp "${repo_tmp_root}/speed-of-cinnamon-snap-output-XXXXXX")"

{
  find "${snap_workspace}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
  find "${snap_workspace_dist}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
  find "${dist_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
  find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap" -type f -print0
} | sort -z > "${tmp_output}"

mapfile -d '' -t snap_files < "${tmp_output}"
if [[ ${#snap_files[@]} -ne 1 ]]; then
  printf 'expected exactly one new snap package, found %d\n' "${#snap_files[@]}" >&2
  exit 1
fi

for path in "${snap_files[@]}"; do
  if [[ -L "${path}" ]]; then
    printf 'snap package must not be a symlink: %s\n' "${path}" >&2
    exit 1
  fi
  absolute="$(realpath "${path}")"
  if [[ "${absolute}" != "${snap_workspace}/speed-of-cinnamon_${version}_"* &&
        "${absolute}" != "${snap_workspace_dist}/speed-of-cinnamon_${version}_"* &&
        "${absolute}" != "${dist_dir}/speed-of-cinnamon_${version}_"* &&
        "${absolute}" != "${repo_dir}/speed-of-cinnamon_${version}_"* ]]; then
    printf 'snap package path is unexpected: %s\n' "${path}" >&2
    exit 1
  fi
  filename="$(basename "${path}")"
  if [[ ! "${filename}" == "speed-of-cinnamon_${version}_"* ]]; then
    printf 'unexpected snap file name: %s\n' "${filename}" >&2
    exit 1
  fi
  if [[ ! -s "${path}" ]]; then
    printf 'snap package is empty: %s\n' "${path}" >&2
    exit 1
  fi
done

output_path="${dist_dir}/$(basename "${snap_files[0]}")"
if [[ "$(realpath "${snap_files[0]}")" != "${output_path}" ]]; then
  "${safe_fs_cmd[@]}" copy-file build-snap "${snap_files[0]}" "${output_path}" 0644 --dst-must-not-exist
fi
printf 'Built %s\n' "${output_path}" >&2
printf '%s\n' "${output_path}"
