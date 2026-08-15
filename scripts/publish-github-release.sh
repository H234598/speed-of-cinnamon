#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
readonly RELEASE_TARGET_REPOSITORY="H234598/speed-of-cinnamon"
readonly RELEASE_EXPECTED_BRANCH="main"
export PATH="${TRUSTED_COMMAND_PATH}"

usage() {
  printf 'usage: %s [--skip-snap] [--skip-generic-rpm] [--dry-run] [v]VERSION\n' "$0" >&2
  printf ' --dry-run: validate artifacts and report planned upload targets without publishing\n' >&2
  printf ' existing release assets are never overwritten; delete stale assets explicitly before publishing\n' >&2
}

skip_snap=false
skip_generic=false
dry_run=false
while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --skip-snap)
      skip_snap=true
      shift
      ;;
    --skip-generic-rpm)
      skip_generic=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    --*)
      printf 'unknown option: %s\n' "${1}" >&2
      usage
      exit 2
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

input_tag="$1"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"
repo_dir="$(realpath "${repo_dir}")"
if [[ -L "${repo_dir}/dist" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${repo_dir}/dist" >&2
  exit 1
fi
if [[ "${input_tag}" == v* ]]; then
  tag="${input_tag}"
else
  tag="v${input_tag}"
fi

if [[ ! "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'release tag %s is invalid\n' "${tag}" >&2
  exit 1
fi

required_tools=(git python3 realpath awk sha256sum grep stat mktemp chmod basename dirname)
if [[ "${dry_run}" == "false" ]]; then
  required_tools+=(gh)
fi
release_is_mutated="false"
release_publish_complete="false"
existing_release="false"
existing_was_draft="false"
existing_was_prerelease="false"
existing_release_title=""
existing_release_title_captured="false"
existing_notes_file=""
existing_notes_file_identity=""
notes_file=""
notes_file_identity=""
staging_dir=""
staging_dir_identity=""
created_release="false"
uploaded_asset_names=()
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

for tool in "${required_tools[@]}"; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done

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

version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

MAX_PROJECT_METADATA_BYTES = 1 << 20
with Path("pyproject.toml").open("rb") as handle:
    payload = handle.read(MAX_PROJECT_METADATA_BYTES + 1)
if len(payload) > MAX_PROJECT_METADATA_BYTES:
    raise SystemExit("pyproject.toml is too large")
data = tomllib.loads(payload.decode("utf-8"))
print(data["project"]["version"])
PY
)"
expected_tag="v${version}"
if [[ "${tag}" != "${expected_tag}" ]]; then
  printf 'release tag %s does not match pyproject version %s\n' "${tag}" "${expected_tag}" >&2
  exit 1
fi

shopt -s nullglob
source_archives=(dist/speed-of-cinnamon-"${version}".tar.gz)
checksums=(dist/speed-of-cinnamon-"${version}".tar.gz.sha256)
rpms=(dist/rpmbuild/RPMS/noarch/speed-of-cinnamon-"${version}"-*.noarch.rpm)
srpms=(dist/rpmbuild/SRPMS/speed-of-cinnamon-"${version}"-*.src.rpm)
generic_rpms=(dist/rpmbuild-generic/RPMS/noarch/speed-of-cinnamon-"${version}"-*.noarch.rpm)
generic_srpms=(dist/rpmbuild-generic/SRPMS/speed-of-cinnamon-"${version}"-*.src.rpm)
snaps=(dist/snap/speed-of-cinnamon_"${version}"_*.snap)

asset_display_name() {
  local ref=$1
  printf '%s' "${ref}"
}

generic_asset_label() {
  local file=$1
  local base

  base="$(basename "${file}")"
  if [[ "${base}" == speed-of-cinnamon-* ]]; then
    printf 'speed-of-cinnamon-generic-%s' "${base#speed-of-cinnamon-}"
  else
    printf 'generic-%s' "${base}"
  fi
}

require_one() {
  local label=$1
  shift
  local count=$#
  if [[ ${count} -ne 1 ]]; then
    printf 'expected exactly one %s, found %d\n' "${label}" "${count}" >&2
    exit 1
  fi
  if [[ ! -s "$1" ]]; then
    printf '%s is missing or empty: %s\n' "${label}" "$1" >&2
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

verify_asset_path() {
  local asset=$1
  local absolute
  local link_count

  if [[ "${asset}" == -* ]]; then
    printf 'asset name may not start with option-like prefix: %s\n' "${asset}" >&2
    exit 1
  fi
  if contains_control_chars "${asset}"; then
    printf 'asset name must not contain control characters\n' >&2
    exit 1
  fi
  if [[ -L "${asset}" ]]; then
    printf 'asset must not be a symlink\n' >&2
    exit 1
  fi
  if [[ ! -f "${asset}" ]]; then
    printf 'asset is not a regular file\n' >&2
    exit 1
  fi
  if ! absolute="$(realpath "${asset}" 2>/dev/null)"; then
    printf 'failed to resolve asset path\n' >&2
    exit 1
  fi
  if contains_control_chars "${absolute}"; then
    printf 'asset path must not contain control characters\n' >&2
    exit 1
  fi
  if [[ "${absolute}" != "${repo_dir}"/* ]]; then
    printf 'asset is outside repository\n' >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${asset}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf 'asset must not be hardlinked: %s\n' "${asset}" >&2
    exit 1
  fi
}

verify_staged_asset_path() {
  local asset=$1
  local absolute
  local link_count

  if [[ "${asset}" == -* ]]; then
    printf 'staged asset name may not start with option-like prefix: %s\n' "${asset}" >&2
    exit 1
  fi
  if contains_control_chars "${asset}"; then
    printf 'staged asset name must not contain control characters\n' >&2
    exit 1
  fi
  if [[ -L "${asset}" ]]; then
    printf 'staged asset must not be a symlink\n' >&2
    exit 1
  fi
  if [[ ! -f "${asset}" ]]; then
    printf 'staged asset is not a regular file\n' >&2
    exit 1
  fi
  if ! absolute="$(realpath "${asset}" 2>/dev/null)"; then
    printf 'failed to resolve staged asset path\n' >&2
    exit 1
  fi
  if contains_control_chars "${absolute}"; then
    printf 'staged asset path must not contain control characters\n' >&2
    exit 1
  fi
  if [[ "${absolute}" != "${staging_dir}"/* ]]; then
    printf 'staged asset is outside release staging directory\n' >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${asset}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf 'staged asset must not be hardlinked: %s\n' "${asset}" >&2
    exit 1
  fi
}

resolve_github_remote_repo() {
  local remote_url
  remote_url="$(git remote get-url origin 2>/dev/null || true)"
  remote_url="${remote_url%.git}"
  if [[ "${remote_url}" =~ ^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(\.git)?$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${remote_url}" =~ ^git@github\.com:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(\.git)?$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${remote_url}" =~ ^ssh://git@github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(\.git)?$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

rollback_release_state() {
  local tag=$1
  local repo=$2
  local existing_release=$3
  local existing_was_draft=$4
  local created_release=$5
  local edit_args

  if [[ "${created_release}" == "true" ]]; then
    gh release delete "${tag}" --repo "${repo}" --yes >/dev/null 2>&1 || true
    return
  fi
  if [[ "${existing_release}" == "true" ]]; then
    for asset_name in "${uploaded_asset_names[@]}"; do
      gh release delete-asset "${tag}" "${asset_name}" --repo "${repo}" --yes >/dev/null 2>&1 || true
    done
    edit_args=(gh release edit "${tag}" --repo "${repo}")
    if [[ "${existing_release_title_captured}" == "true" ]]; then
      edit_args+=(--title "${existing_release_title}")
    fi
    if [[ -n "${existing_notes_file}" && -f "${existing_notes_file}" ]]; then
      edit_args+=(--notes-file "${existing_notes_file}")
    fi
    if [[ "${existing_was_draft}" == "true" ]]; then
      edit_args+=(--draft)
    else
      edit_args+=(--draft=false)
    fi
    if [[ "${existing_was_prerelease}" == "true" ]]; then
      edit_args+=(--prerelease)
    else
      edit_args+=(--prerelease=false)
    fi
    "${edit_args[@]}" >/dev/null 2>&1 || true
    return
  fi
}

mark_release_mutation() {
  release_is_mutated="true"
}

publish_release_succeeded() {
  release_publish_complete="true"
}

cleanup_release_state() {
  local rollback_release_tag=${tag-}
  local rollback_repo=${repo-}
  local existing_release_state=${existing_release-false}
  local existing_draft_state=${existing_was_draft-false}
  local created_release_state=${created_release-false}
  local was_mutated=${release_is_mutated-false}
  local publish_complete=${release_publish_complete-false}

  if [[ "${dry_run}" == "true" || "${publish_complete}" == "true" || "${was_mutated}" != "true" || -z "${rollback_release_tag}" || -z "${rollback_repo}" ]]; then
    return
  fi
  rollback_release_state "${rollback_release_tag}" "${rollback_repo}" "${existing_release_state}" "${existing_draft_state}" "${created_release_state}"
}

cleanup_notes() {
  cleanup_release_state
  if [[ -n "${notes_file}" ]]; then
    if [[ -n "${notes_file_identity}" ]]; then
      "${safe_fs_cmd[@]}" remove-leaf publish "${notes_file}" \
        --expected-identity "${notes_file_identity}" >/dev/null 2>&1 || true
    else
      printf 'refusing release notes cleanup without verified identity: %s\n' "${notes_file}" >&2
    fi
  fi
  if [[ -n "${existing_notes_file}" ]]; then
    if [[ -n "${existing_notes_file_identity}" ]]; then
      "${safe_fs_cmd[@]}" remove-leaf publish "${existing_notes_file}" \
        --expected-identity "${existing_notes_file_identity}" >/dev/null 2>&1 || true
    else
      printf 'refusing existing release notes cleanup without verified identity: %s\n' "${existing_notes_file}" >&2
    fi
  fi
  if [[ -n "${staging_dir}" ]]; then
    if [[ -n "${staging_dir_identity}" ]]; then
      "${safe_fs_cmd[@]}" remove publish "${staging_dir}" --kind dir \
        --expected-identity "${staging_dir_identity}" >/dev/null 2>&1 || true
    else
      printf 'refusing release staging cleanup without verified identity: %s\n' "${staging_dir}" >&2
    fi
  fi
}
trap cleanup_notes EXIT

require_one "source archive" "${source_archives[@]}"
require_one "checksum file" "${checksums[@]}"
require_one "RPM" "${rpms[@]}"
require_one "source RPM" "${srpms[@]}"
if [[ "${skip_generic}" != "true" ]]; then
  require_one "generic RPM" "${generic_rpms[@]}"
  require_one "generic source RPM" "${generic_srpms[@]}"
fi
if [[ "${skip_snap}" != "true" ]]; then
  require_one "Snap package" "${snaps[@]}"
fi
assets=("${source_archives[@]}" "${checksums[@]}" "${rpms[@]}" "${srpms[@]}")
if [[ "${skip_generic}" != "true" ]]; then
  assets+=("${generic_rpms[@]}" "${generic_srpms[@]}")
fi
if [[ "${skip_snap}" != "true" ]]; then
  assets+=("${snaps[@]}")
fi
for asset in "${assets[@]}"; do
  verify_asset_path "${asset}"
done
require_regular_source_file "${safe_fs}" "safe local filesystem helper"

staging_dir=""
staging_dir_identity=""
upload_refs=()
staging_root="${TMPDIR:-/tmp}"
declare -A staged_names_seen=()
source_archive_ref=""
checksum_ref=""
rpm_ref=""
srpm_ref=""
snap_ref=""
if [[ ! "${staging_root}" == /* ]]; then
  printf 'temporary root must be an absolute path: %s\n' "${staging_root}" >&2
  exit 1
fi
if [[ -L "${staging_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${staging_root}" >&2
  exit 1
fi
if [[ ! -d "${staging_root}" || ! -w "${staging_root}" ]]; then
  printf 'temporary root is not a writable directory: %s\n' "${staging_root}" >&2
  exit 1
fi
if ! staging_root="$(realpath "${staging_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${staging_root}" >&2
  exit 1
fi
mkdir -p "${staging_root}"

staging_dir="$(mktemp -d "${staging_root}/speed-of-cinnamon-release-upload-XXXXXX")"
if [[ -z "${staging_dir}" ]]; then
  printf 'failed to create staging directory for upload assets.\n' >&2
  exit 1
fi
if [[ -L "${staging_dir}" ]]; then
  printf 'release staging directory must not be a symlink: %s\n' "${staging_dir}" >&2
  exit 1
fi
if ! staging_dir_abs="$(realpath "${staging_dir}")"; then
  printf 'failed to resolve release staging directory: %s\n' "${staging_dir}" >&2
  exit 1
fi
if [[ "${staging_dir_abs}" != "${staging_root}/speed-of-cinnamon-release-upload-"* ]]; then
  printf 'release staging directory escaped temporary root: %s\n' "${staging_dir}" >&2
  exit 1
fi
staging_dir="${staging_dir_abs}"
if ! staging_dir_identity="$("${safe_fs_cmd[@]}" identity publish "${staging_dir}" --kind dir)"; then
  printf 'failed to capture release staging directory identity: %s\n' "${staging_dir}" >&2
  exit 1
fi

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
    primary_error = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("release fsync descriptor cleanup failed")
        else:
            raise SystemExit("release fsync descriptor cleanup failed") from cleanup_error
PY
}

chmod_and_fsync_regular_file() {
  local path=$1
  local mode=$2
  local label=$3
  local expected_identity=$4
  python3 - "$path" "$mode" "$label" "$expected_identity" <<'PY'
import os
import stat
import sys

path, raw_mode, label, expected_identity = sys.argv[1:]
try:
    mode = int(raw_mode, 8)
except ValueError:
    print(f"invalid mode for {label}: {raw_mode}", file=sys.stderr)
    raise SystemExit(1)
flags = os.O_RDONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
except OSError as exc:
    print(f"failed to open {label} for chmod: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        print(f"{label} must be a regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    if getattr(file_stat, "st_nlink", 1) != 1:
        print(f"{label} must not be hardlinked: {path}", file=sys.stderr)
        raise SystemExit(1)
    actual_identity = f"{file_stat.st_dev}:{file_stat.st_ino}:{file_stat.st_mode}"
    if actual_identity != expected_identity:
        print(f"{label} changed before chmod: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.fchmod(fd, mode)
    os.fsync(fd)
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("release chmod descriptor cleanup failed")
        else:
            raise SystemExit("release chmod descriptor cleanup failed") from cleanup_error
PY
}

for asset in "${assets[@]}"; do
  if ! asset_abs="$(realpath "${asset}")"; then
    printf 'failed to resolve release asset for staging: %s\n' "${asset}" >&2
    exit 1
  fi
  staged_name="$(basename "${asset}")"
  for generic_asset in "${generic_rpms[@]}" "${generic_srpms[@]}"; do
    if [[ "${asset}" == "${generic_asset}" ]]; then
      staged_name="$(generic_asset_label "${asset}")"
      break
    fi
  done
  if [[ -n "${staged_names_seen[${staged_name}]:-}" ]]; then
    printf 'duplicate release asset staging name: %s\n' "${staged_name}" >&2
    exit 1
  fi
  staged_names_seen["${staged_name}"]=1
  staged_path="${staging_dir}/${staged_name}"
  if ! "${safe_fs_cmd[@]}" copy-file publish "${asset_abs}" "${staged_path}" 0644; then
    printf 'failed to stage release asset for upload: %s\n' "${asset}" >&2
    exit 1
  fi
  staged_asset_identity="$("${safe_fs_cmd[@]}" identity publish "${staged_path}" --kind file)"
  chmod_and_fsync_regular_file "${staged_path}" 0444 "staged release asset" "${staged_asset_identity}"
  upload_refs+=("${staged_path}")
  verify_staged_asset_path "${staged_path}"
  if [[ "${asset}" == "${source_archives[0]}" ]]; then
    source_archive_ref="${staged_path}"
  elif [[ "${asset}" == "${checksums[0]}" ]]; then
    checksum_ref="${staged_path}"
  elif [[ "${asset}" == "${rpms[0]}" ]]; then
    rpm_ref="${staged_path}"
  elif [[ "${asset}" == "${srpms[0]}" ]]; then
    srpm_ref="${staged_path}"
  elif [[ "${skip_snap}" != "true" && "${asset}" == "${snaps[0]}" ]]; then
    snap_ref="${staged_path}"
  fi
done
if [[ -z "${source_archive_ref}" || -z "${checksum_ref}" || -z "${rpm_ref}" || -z "${srpm_ref}" ]]; then
  printf 'failed to stage required release assets.\n' >&2
  exit 1
fi

generic_rpm_label="[not built in this run (build_generic_rpm=false)]"
generic_src_label="[not built in this run (build_generic_rpm=false)]"
snap_label="[not built in this run (SNAP_BUILD=0)]"
for asset_ref in "${upload_refs[@]}"; do
  staged_name="$(basename "${asset_ref}")"
  if [[ "${staged_name}" == speed-of-cinnamon-generic-*.noarch.rpm ]]; then
    generic_rpm_label="${staged_name}"
  elif [[ "${staged_name}" == speed-of-cinnamon-generic-*.src.rpm ]]; then
    generic_src_label="${staged_name}"
  elif [[ "${skip_snap}" != "true" && "${asset_ref}" == "${snap_ref}" ]]; then
    snap_label="${staged_name}"
  fi
done
repo="${GITHUB_REPOSITORY:-}"
remote_repo=""
if remote_repo="$(resolve_github_remote_repo)"; then
  :
else
  remote_repo=""
fi
if [[ -z "${repo}" && -z "${remote_repo}" ]]; then
  printf 'GITHUB_REPOSITORY is not set and origin is not a GitHub repository; cannot verify target repository safely.\n' >&2
  exit 1
fi
if [[ -n "${repo}" && "${repo}" != "${RELEASE_TARGET_REPOSITORY}" ]]; then
  printf 'release repository is fixed to %s: %s is not allowed\n' "${RELEASE_TARGET_REPOSITORY}" "${repo}" >&2
  exit 1
fi
if [[ -n "${remote_repo}" && "${remote_repo}" != "${RELEASE_TARGET_REPOSITORY}" ]]; then
  printf 'checked out origin is not the allowed release repository: %s\n' "${remote_repo}" >&2
  exit 1
fi
if [[ -z "${repo}" ]]; then
  repo="${RELEASE_TARGET_REPOSITORY}"
else
  if [[ -n "${remote_repo}" && "${repo}" != "${remote_repo}" ]]; then
    printf 'checked out origin (%s) does not match GITHUB_REPOSITORY (%s).\n' "${remote_repo}" "${repo}" >&2
    exit 1
  fi
fi
if [[ "${repo}" != "${RELEASE_TARGET_REPOSITORY}" ]]; then
  printf 'release repository must be pinned to %s, got %s\n' "${RELEASE_TARGET_REPOSITORY}" "${repo}" >&2
  exit 1
fi

if ! tag_commit="$(git rev-parse --verify "${tag}^{commit}")"; then
  printf 'failed to resolve release tag commit: %s\n' "${tag}" >&2
  exit 1
fi
commit="${RELEASE_EXPECTED_COMMIT:-${GITHUB_SHA:-${tag_commit}}}"
if [[ ! "${commit}" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  printf 'invalid commit value: %s\n' "${commit}" >&2
  exit 1
fi
if [[ "${commit}" != "${tag_commit}" ]]; then
  printf 'release expected commit does not match release tag commit: %s\n' "${tag}" >&2
  exit 1
fi

current_branch="$(git symbolic-ref --quiet --short HEAD || true)"
if [[ -n "${current_branch}" ]]; then
  if [[ "${current_branch}" != "${RELEASE_EXPECTED_BRANCH}" ]]; then
    printf 'release must run from %s, got %s\n' "${RELEASE_EXPECTED_BRANCH}" "${current_branch}" >&2
    exit 1
  fi
elif [[ "${GITHUB_EVENT_NAME:-}" == "workflow_dispatch" ]]; then
  if [[ "${GITHUB_REF_NAME:-}" != "${RELEASE_EXPECTED_BRANCH}" ]]; then
    printf 'workflow-dispatch release must originate from %s\n' "${RELEASE_EXPECTED_BRANCH}" >&2
    exit 1
  fi
elif [[ "${GITHUB_REF_TYPE:-}" == "tag" ]]; then
  if [[ "${GITHUB_REF_NAME:-}" != "${tag}" ]]; then
    printf 'detached release checkout does not match release tag: %s\n' "${tag}" >&2
    exit 1
  fi
else
  printf 'release requires %s branch checkout or verified GitHub release ref\n' "${RELEASE_EXPECTED_BRANCH}" >&2
  exit 1
fi

checksum="$(awk 'NF {print $1; exit}' "${checksum_ref}")"
if [[ ! "${checksum}" =~ ^[0-9A-Fa-f]{64}$ ]]; then
  printf 'invalid sha256 checksum: %s\n' "${checksum}" >&2
  exit 1
fi
checksum_target="$(awk 'NF {print $2; exit}' "${checksum_ref}")"
if [[ -z "${checksum_target}" ]]; then
  printf 'checksum file missing target path: %s\n' "${checksum_ref}" >&2
  exit 1
fi
if [[ "${checksum_target##*/}" != "$(basename "${source_archive_ref}")" ]]; then
  printf 'checksum file target mismatch (%s) for %s\n' "${checksum_target}" "${source_archive_ref}" >&2
  exit 1
fi
checksum_dir="$(dirname "${checksum_ref}")"
checksum_file="$(basename "${checksum_ref}")"
if ! (cd "${checksum_dir}" && sha256sum --check --strict --status "${checksum_file}"); then
  printf 'checksum verification failed for %s\n' "${source_archive_ref}" >&2
  exit 1
fi
if ! sha256sum "${source_archive_ref}" | awk '{print $1}' | grep -Fxq "${checksum}"; then
  printf 'checksum mismatch for %s\n' "${source_archive_ref}" >&2
  exit 1
fi

notes_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${notes_tmp_root}" == /* ]]; then
  printf 'temporary root must be an absolute path: %s\n' "${notes_tmp_root}" >&2
  exit 1
fi
if [[ -L "${notes_tmp_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${notes_tmp_root}" >&2
  exit 1
fi
if [[ ! -d "${notes_tmp_root}" || ! -w "${notes_tmp_root}" ]]; then
  printf 'temporary root is not a writable directory: %s\n' "${notes_tmp_root}" >&2
  exit 1
fi
if [[ -L "${notes_tmp_root}" ]]; then
  printf 'temporary root must not be a symlink: %s\n' "${notes_tmp_root}" >&2
  exit 1
fi
if ! notes_tmp_root="$(realpath "${notes_tmp_root}")"; then
  printf 'failed to resolve temporary root: %s\n' "${notes_tmp_root}" >&2
  exit 1
fi
mkdir -p "${notes_tmp_root}"
notes_file="$(mktemp "${notes_tmp_root}/speed-of-cinnamon-release-notes-XXXXXX")"
if [[ -L "${notes_file}" ]]; then
  printf 'release notes file must not be a symlink: %s\n' "${notes_file}" >&2
  exit 1
fi
if ! notes_file_abs="$(realpath "${notes_file}")"; then
  printf 'failed to resolve release notes file: %s\n' "${notes_file}" >&2
  exit 1
fi
if [[ "${notes_file_abs}" != "${notes_tmp_root}/speed-of-cinnamon-release-notes-"* ]]; then
  printf 'release notes file escaped temporary root: %s\n' "${notes_file}" >&2
  exit 1
fi
notes_file="${notes_file_abs}"
if ! notes_file_identity="$("${safe_fs_cmd[@]}" identity publish "${notes_file}" --kind file)"; then
  printf 'failed to capture release notes file identity: %s\n' "${notes_file}" >&2
  exit 1
fi
write_regular_file_from_stdin() {
  local path=$1
  local label=$2
  local expected_identity=$3

  python3 -c '
import os
import stat
import sys

path, label, expected_identity = sys.argv[1:4]
MAX_RELEASE_NOTES_BYTES = 1_000_000
payload = sys.stdin.buffer.read(MAX_RELEASE_NOTES_BYTES + 1)
if len(payload) > MAX_RELEASE_NOTES_BYTES:
    print(f"{label} exceeds {MAX_RELEASE_NOTES_BYTES} bytes: {path}", file=sys.stderr)
    raise SystemExit(1)
flags = os.O_WRONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
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
    actual_identity = f"{file_stat.st_dev}:{file_stat.st_ino}:{file_stat.st_mode}"
    if actual_identity != expected_identity:
        print(f"{label} changed before writing: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.ftruncate(fd, 0)
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            print(f"failed to write {label}: {path}", file=sys.stderr)
            raise SystemExit(1)
        view = view[written:]
    os.fsync(fd)
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("release notes descriptor cleanup failed")
        else:
            raise SystemExit("release notes descriptor cleanup failed") from cleanup_error
' "${path}" "${label}" "${expected_identity}"
}
write_regular_file_from_stdin "${notes_file}" "release notes file" "${notes_file_identity}" <<EOF
Speed of Cinnamon ${tag}

Cinnamon-native voice typing for Fedora Cinnamon.

Built from commit ${commit}.

Assets:
- Source archive: $(basename "${source_archive_ref}")
- Source archive SHA-256: ${checksum}
- Fedora noarch RPM: $(basename "${rpm_ref}")
- Source RPM: $(basename "${srpm_ref}")
- Generic noarch RPM: ${generic_rpm_label}
- Source RPM (generic): ${generic_src_label}
- Snap package: ${snap_label}
EOF

if [[ "${dry_run}" == "true" ]]; then
  printf 'Dry-run mode enabled. Build and assets validated for tag %s.\n' "${tag}"
  printf 'Planned assets:\n'
  for asset in "${upload_refs[@]}"; do
    printf '  - %s\n' "${asset}"
  done
  exit 0
fi

if [[ -z "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  printf 'GH_TOKEN or GITHUB_TOKEN must be set to publish a release.\n' >&2
  exit 1
fi
release_probe_headers=""
if release_probe_headers="$(gh api --include --silent "repos/${repo}/releases/tags/${tag}" 2>/dev/null)"; then
  :
fi
release_probe_status="$(awk '$1 ~ /^HTTP\/[0-9]+(\.[0-9]+)?$/ { status = $2 } END { print status }' <<<"${release_probe_headers}")"
if [[ "${release_probe_status}" == "200" ]]; then
  existing_release="true"
  existing_was_draft="$(gh release view "${tag}" --repo "${repo}" --json isDraft --jq '.isDraft')"
  existing_was_prerelease="$(gh release view "${tag}" --repo "${repo}" --json isPrerelease --jq '.isPrerelease')"
  existing_assets="$(gh release view "${tag}" --repo "${repo}" --json assets --jq '.assets[].name')"
  existing_release_title="$(gh release view "${tag}" --repo "${repo}" --json name --jq '.name // empty')"
  existing_release_title_captured="true"
  existing_notes_file="$(mktemp "${notes_tmp_root}/speed-of-cinnamon-existing-release-notes-XXXXXX")"
  if [[ -L "${existing_notes_file}" ]]; then
    printf 'existing release notes file must not be a symlink: %s\n' "${existing_notes_file}" >&2
    exit 1
  fi
  if ! existing_notes_file_abs="$(realpath "${existing_notes_file}")"; then
    printf 'failed to resolve existing release notes file: %s\n' "${existing_notes_file}" >&2
    exit 1
  fi
  if [[ "${existing_notes_file_abs}" != "${notes_tmp_root}/speed-of-cinnamon-existing-release-notes-"* ]]; then
    printf 'existing release notes file escaped temporary root: %s\n' "${existing_notes_file}" >&2
    exit 1
  fi
  existing_notes_file="${existing_notes_file_abs}"
  if ! existing_notes_file_identity="$("${safe_fs_cmd[@]}" identity publish "${existing_notes_file}" --kind file)"; then
    printf 'failed to capture existing release notes file identity: %s\n' "${existing_notes_file}" >&2
    exit 1
  fi
  if ! gh release view "${tag}" --repo "${repo}" --json body --jq '.body // ""' \
      | write_regular_file_from_stdin "${existing_notes_file}" "existing release notes file" "${existing_notes_file_identity}"; then
    printf 'failed to snapshot existing release notes for rollback: %s\n' "${tag}" >&2
    exit 1
  fi
  for asset_ref in "${upload_refs[@]}"; do
    asset_name="$(basename "${asset_ref}")"
    if grep -Fxq -- "${asset_name}" <<<"${existing_assets}"; then
      printf 'release asset already exists; delete it explicitly before publishing: %s\n' "${asset_name}" >&2
      exit 1
    fi
  done
  mark_release_mutation
  if ! gh release edit "${tag}" \
      --repo "${repo}" \
      --title "Speed of Cinnamon ${tag}" \
      --notes-file "${notes_file}" \
      --draft; then
    printf 'failed to prepare existing release as draft: %s\n' "${tag}" >&2
    exit 1
  fi
elif [[ "${release_probe_status}" == "404" ]]; then
  if ! gh release create "${tag}" \
      --repo "${repo}" \
      --title "Speed of Cinnamon ${tag}" \
      --notes-file "${notes_file}" \
      --verify-tag \
      --draft; then
    printf 'failed to create draft release: %s\n' "${tag}" >&2
    exit 1
  fi
  created_release="true"
  mark_release_mutation
else
  printf 'could not determine release state for %s (HTTP status: %s)\n' "${tag}" "${release_probe_status:-unknown}" >&2
  exit 1
fi

for upload_ref in "${upload_refs[@]}"; do
  if ! gh release upload "${tag}" "${upload_ref}" --repo "${repo}"; then
    printf 'failed to upload release asset: %s\n' "$(basename "${upload_ref}")" >&2
    exit 1
  fi
  uploaded_asset_names+=("$(basename "${upload_ref}")")
done

if ! gh release edit "${tag}" \
    --repo "${repo}" \
    --title "Speed of Cinnamon ${tag}" \
    --notes-file "${notes_file}" \
    --draft=false; then
  printf 'failed to publish release after uploading assets.\n' >&2
  exit 1
fi
publish_release_succeeded
