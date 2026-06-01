#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: %s [--dry-run] [v]VERSION\n' "$0" >&2
}

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
  shift
fi

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

input_tag="$1"
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_dir}"
if [[ "${input_tag}" == v* ]]; then
  tag="${input_tag}"
else
  tag="v${input_tag}"
fi

if [[ "${dry_run}" == "true" ]]; then
  required_tools=(git python3)
else
  required_tools=(gh git python3)
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

require_one() {
  local label="$1"
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

require_one "source archive" "${source_archives[@]}"
require_one "checksum file" "${checksums[@]}"
require_one "RPM" "${rpms[@]}"
require_one "source RPM" "${srpms[@]}"

assets=("${source_archives[@]}" "${checksums[@]}" "${rpms[@]}" "${srpms[@]}")
repo="${GITHUB_REPOSITORY:-H234598/speed-of-cinnamon}"
commit="${GITHUB_SHA:-$(git rev-parse HEAD)}"
checksum="$(awk '{print $1}' "${checksums[0]}")"

notes_file="$(mktemp)"
trap 'rm -f "${notes_file}"' EXIT
cat > "${notes_file}" <<EOF
Speed of Cinnamon ${tag}

Cinnamon-native voice typing for Fedora Cinnamon.

Built from commit ${commit}.

Assets:
- Source archive: $(basename "${source_archives[0]}")
- Source archive SHA-256: ${checksum}
- Fedora noarch RPM: $(basename "${rpms[0]}")
- Source RPM: $(basename "${srpms[0]}")
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
