#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

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

required_tools=(git python3 realpath awk sha256sum grep stat mktemp chmod)
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

version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])
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
  if [[ "${asset}" == *$'\n'* || "${asset}" == *$'\r'* || "${asset}" == *$'\t'* ]]; then
    printf 'asset name must not contain control characters: %s\n' "${asset}" >&2
    exit 1
  fi

  absolute="$(realpath "${asset}")"
  if [[ "${absolute}" != "${repo_dir}"/* ]]; then
    printf 'asset is outside repository: %s\n' "${asset}" >&2
    exit 1
  fi
  if [[ ! -f "${asset}" ]]; then
    printf 'asset is not a regular file: %s\n' "${asset}" >&2
    exit 1
  fi
  if [[ -L "${asset}" ]]; then
    printf 'asset must not be a symlink: %s\n' "${asset}" >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${asset}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf 'asset must not be hardlinked: %s\n' "${asset}" >&2
    exit 1
  fi
}

resolve_github_remote_repo() {
  local remote_url
  remote_url="$(git remote get-url origin 2>/dev/null || true)"
  if [[ "${remote_url}" =~ ^https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(\.git)?$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${remote_url}" =~ ^git@github\.com:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)(\.git)?$ ]]; then
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
upload_refs=()
source_archive_ref=""
checksum_ref=""
rpm_ref=""
srpm_ref=""
snap_ref=""
staging_dir="$(mktemp -d "${repo_dir}/dist/release-upload-XXXXXX")"
if [[ -z "${staging_dir}" ]]; then
  printf 'failed to create staging directory for upload assets.\n' >&2
  exit 1
fi

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
  staged_path="${staging_dir}/${staged_name}"
  if ! "${safe_fs_cmd[@]}" copy-file publish "${asset_abs}" "${staged_path}" 0644; then
    printf 'failed to stage release asset for upload: %s\n' "${asset}" >&2
    exit 1
  fi
  chmod 0444 -- "${staged_path}"
  upload_refs+=("${staged_path}")
  uploaded_asset_names+=("${staged_name}")
  verify_asset_path "${staged_path}"
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
if [[ -z "${repo}" ]]; then
  if [[ -z "${remote_repo}" ]]; then
    printf 'GITHUB_REPOSITORY is not set and origin is not a GitHub repository.\n' >&2
    exit 1
  fi
  repo="${remote_repo}"
fi
if [[ ! "${repo}" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  printf 'invalid repository value: %s\n' "${repo}" >&2
  exit 1
fi
if [[ -n "${remote_repo}" && "${repo}" != "${remote_repo}" ]]; then
  printf 'repository value does not match checked out origin: %s != %s\n' "${repo}" "${remote_repo}" >&2
  exit 1
fi

commit="${GITHUB_SHA:-$(git rev-parse HEAD)}"
if [[ ! "${commit}" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  printf 'invalid commit value: %s\n' "${commit}" >&2
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
cleanup_notes() {
  cleanup_release_state
  rm -f -- "${notes_file}"
  if [[ -n "${existing_notes_file}" ]]; then
    rm -f -- "${existing_notes_file}"
  fi
  if [[ -n "${staging_dir}" ]]; then
    rm -rf -- "${staging_dir}"
  fi
}
trap cleanup_notes EXIT
cat > "${notes_file}" <<EOF
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
if gh release view "${tag}" --repo "${repo}" >/dev/null 2>&1; then
  existing_release="true"
  existing_was_draft="$(gh release view "${tag}" --repo "${repo}" --json isDraft --jq '.isDraft')"
  existing_was_prerelease="$(gh release view "${tag}" --repo "${repo}" --json isPrerelease --jq '.isPrerelease')"
  existing_assets="$(gh release view "${tag}" --repo "${repo}" --json assets --jq '.assets[].name')"
  existing_release_title="$(gh release view "${tag}" --repo "${repo}" --json name --jq '.name // empty')"
  existing_release_title_captured="true"
  existing_notes_file="$(mktemp "${notes_tmp_root}/speed-of-cinnamon-existing-release-notes-XXXXXX")"
  if ! gh release view "${tag}" --repo "${repo}" --json body --jq '.body // ""' > "${existing_notes_file}"; then
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
else
  created_release="true"
  mark_release_mutation
  if ! gh release create "${tag}" \
      --repo "${repo}" \
      --title "Speed of Cinnamon ${tag}" \
      --notes-file "${notes_file}" \
      --verify-tag \
      --draft; then
    printf 'failed to create draft release: %s\n' "${tag}" >&2
    exit 1
  fi
fi

if ! gh release upload "${tag}" "${upload_refs[@]}" --repo "${repo}"; then
  printf 'failed to upload one or more release assets.\n' >&2
  exit 1
fi

if ! gh release edit "${tag}" \
    --repo "${repo}" \
    --title "Speed of Cinnamon ${tag}" \
    --notes-file "${notes_file}" \
    --draft=false; then
  printf 'failed to publish release after uploading assets.\n' >&2
  exit 1
fi
publish_release_succeeded
