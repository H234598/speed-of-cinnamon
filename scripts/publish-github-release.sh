#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

usage() {
  printf 'usage: %s [--dry-run] [--skip-snap] [--skip-generic-rpm] [v]VERSION\n' "$0" >&2
}

dry_run=false
skip_snap=false
skip_generic=false
while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --dry-run)
      dry_run=true
      shift
      ;;
    --skip-snap)
      skip_snap=true
      shift
      ;;
    --skip-generic-rpm)
      skip_generic=true
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
if [[ "${input_tag}" == v* ]]; then
  tag="${input_tag}"
else
  tag="v${input_tag}"
fi

if [[ ! "${tag}" =~ ^v[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'release tag %s is invalid\n' "${tag}" >&2
  exit 1
fi

if [[ "${dry_run}" == "true" ]]; then
  required_tools=(git python3 realpath awk)
else
  required_tools=(gh git python3 realpath awk)
fi

for tool in "${required_tools[@]}"; do
  if ! command -v "${tool}" >/dev/null 2>&1; then
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
if [[ "${dry_run}" == "false" && "${tag}" != "${expected_tag}" ]]; then
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
snaps=(dist/snap/speed-of-cinnamon_${version}_*.snap)

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

notes_file="$(mktemp "${TMPDIR:-/tmp}/speed-of-cinnamon-release-notes-XXXXXX")"
cleanup_notes() {
  rm -f -- "${notes_file}"
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
- Generic noarch RPM: $([ "${skip_generic}" = "true" ] && printf "not built in this run (build_generic_rpm=false)" || basename "${generic_rpms[0]}")
- Source RPM (generic): $([ "${skip_generic}" = "true" ] && printf "not built in this run (build_generic_rpm=false)" || basename "${generic_srpms[0]}")
- Snap package: $([ "${skip_snap}" = "true" ] && printf "not built in this run (SNAP_BUILD=0)" || basename "${snaps[0]}")
EOF

if [[ "${dry_run}" == "true" ]]; then
  printf 'Would publish %s to %s with assets:\n' "${tag}" "${repo}"
  printf '  %s\n' "${assets[@]}"
  exit 0
fi

if [[ -z "${GH_TOKEN:-${GITHUB_TOKEN:-}}" ]]; then
  printf 'GH_TOKEN or GITHUB_TOKEN must be set to publish a release.\n' >&2
  exit 1
fi

if gh release view "${tag}" --repo "${repo}" >/dev/null 2>&1; then
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

gh release upload "${tag}" "${assets[@]}" --repo "${repo}" --clobber
