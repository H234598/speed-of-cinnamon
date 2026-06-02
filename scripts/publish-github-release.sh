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

if [[ ! "${tag}" =~ ^v[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'release tag %s is invalid\n' "${tag}" >&2
  exit 1
fi

required_tools=(git python3 realpath awk sha256sum grep)
if [[ "${dry_run}" == "false" ]]; then
  required_tools+=(gh)
fi

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

staging_dir=""
upload_refs=("${assets[@]}")
if [[ "${skip_generic}" != "true" ]]; then
  staging_dir="$(mktemp -d "${repo_dir}/dist/release-upload-XXXXXX")"
  if [[ -z "${staging_dir}" ]]; then
    printf 'failed to create staging directory for upload assets.\n' >&2
    exit 1
  fi
  for generic_asset in "${generic_rpms[@]}" "${generic_srpms[@]}"; do
    staged_path="${staging_dir}/$(generic_asset_label "${generic_asset}")"
    cp -f -- "${generic_asset}" "${staged_path}"

    for idx in "${!upload_refs[@]}"; do
      if [[ "${upload_refs[idx]}" == "${generic_asset}" ]]; then
        upload_refs[idx]="${staged_path}"
      fi
    done
  done
fi

for asset in "${upload_refs[@]}"; do
  verify_asset_path "${asset}"
done

generic_rpm_label="[not built in this run (build_generic_rpm=false)]"
generic_src_label="[not built in this run (build_generic_rpm=false)]"
snap_label="[not built in this run (SNAP_BUILD=0)]"
for asset_ref in "${upload_refs[@]}"; do
  case "${asset_ref}" in
    "${staging_dir}/"*)
      staged_name="$(basename "${asset_ref}")"
      if [[ "${staged_name}" == speed-of-cinnamon-generic-*.noarch.rpm ]]; then
        generic_rpm_label="${staged_name}"
      elif [[ "${staged_name}" == speed-of-cinnamon-generic-*.src.rpm ]]; then
        generic_src_label="${staged_name}"
      fi
      ;;
    dist/snap/*)
      if [[ "${skip_snap}" != "true" ]]; then
        snap_label="$(basename "${asset_ref}")"
      fi
      ;;
  esac
done
repo="${GITHUB_REPOSITORY:-H234598/speed-of-cinnamon}"
if [[ ! "${repo}" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  printf 'invalid repository value: %s\n' "${repo}" >&2
  exit 1
fi

commit="${GITHUB_SHA:-$(git rev-parse HEAD)}"
if [[ ! "${commit}" =~ ^[0-9a-fA-F]{7,40}$ ]]; then
  printf 'invalid commit value: %s\n' "${commit}" >&2
  exit 1
fi

checksum="$(awk 'NF {print $1; exit}' "${checksums[0]}")"
if [[ ! "${checksum}" =~ ^[0-9A-Fa-f]{64}$ ]]; then
  printf 'invalid sha256 checksum: %s\n' "${checksum}" >&2
  exit 1
fi
checksum_target="$(awk 'NF {print $2; exit}' "${checksums[0]}")"
if [[ -z "${checksum_target}" ]]; then
  printf 'checksum file missing target path: %s\n' "${checksums[0]}" >&2
  exit 1
fi
if [[ "${checksum_target##*/}" != "$(basename "${source_archives[0]}")" ]]; then
  printf 'checksum file target mismatch (%s) for %s\n' "${checksum_target}" "${source_archives[0]}" >&2
  exit 1
fi
checksum_dir="$(dirname "${checksums[0]}")"
checksum_file="$(basename "${checksums[0]}")"
if ! (cd "${repo_dir}/${checksum_dir}" && sha256sum --check --strict --status "${checksum_file}"); then
  printf 'checksum verification failed for %s\n' "${source_archives[0]}" >&2
  exit 1
fi
if ! sha256sum "${source_archives[0]}" | awk '{print $1}' | grep -Fxq "${checksum}"; then
  printf 'checksum mismatch for %s\n' "${source_archives[0]}" >&2
  exit 1
fi

notes_tmp_root="${TMPDIR:-/tmp}"
if [[ ! "${notes_tmp_root}" == /* ]]; then
  notes_tmp_root="/tmp"
fi
if [[ -L "${notes_tmp_root}" ]]; then
  notes_tmp_root="${repo_dir}/.tmp"
fi
if [[ ! -d "${notes_tmp_root}" || ! -w "${notes_tmp_root}" ]]; then
  notes_tmp_root="${repo_dir}/.tmp"
fi
if [[ -L "${notes_tmp_root}" ]]; then
  notes_tmp_root="/tmp"
fi
mkdir -p "${notes_tmp_root}"
notes_file="$(mktemp "${notes_tmp_root}/speed-of-cinnamon-release-notes-XXXXXX")"
cleanup_notes() {
  rm -f -- "${notes_file}"
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
- Source archive: $(basename "${source_archives[0]}")
- Source archive SHA-256: ${checksum}
- Fedora noarch RPM: $(basename "${rpms[0]}")
- Source RPM: $(basename "${srpms[0]}")
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
  existing_assets="$(gh release view "${tag}" --repo "${repo}" --json assets --jq '.assets[].name')"
  for asset_ref in "${upload_refs[@]}"; do
    asset_name="$(basename "${asset_ref}")"
    if grep -Fxq -- "${asset_name}" <<<"${existing_assets}"; then
      printf 'release asset already exists; delete it explicitly before publishing: %s\n' "${asset_name}" >&2
      exit 1
    fi
  done
  gh release edit "${tag}" \
    --repo "${repo}" \
    --title "Speed of Cinnamon ${tag}" \
    --notes-file "${notes_file}" \
    --draft=false
else
  gh release create "${tag}" \
    --repo "${repo}" \
    --title "Speed of Cinnamon ${tag}" \
    --notes-file "${notes_file}" \
    --verify-tag
fi

gh release upload "${tag}" "${upload_refs[@]}" --repo "${repo}"
