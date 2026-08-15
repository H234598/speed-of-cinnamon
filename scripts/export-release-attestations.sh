#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

if [[ $# -ne 1 || ! "${1}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'usage: %s vMAJOR.MINOR.PATCH\n' "$0" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
tag="$1"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/speed-of-cinnamon"
bundle_dir="${repo_dir}/release-attestations/${tag}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")
for tool in python3 git; do
  command -v -- "${tool}" >/dev/null 2>&1 || { printf '%s not found.\n' "${tool}" >&2; exit 1; }
done
if [[ -e "${bundle_dir}" || -L "${bundle_dir}" ]]; then
  printf 'release attestation bundle already exists: %s\n' "${bundle_dir}" >&2
  exit 1
fi
if [[ ! -f "${state_dir}/real-e2e-attestation.json" || ! -f "${state_dir}/local-model-e2e-attestation.json" ]]; then
  printf 'run both real/local E2E acceptance gates before exporting attestations.\n' >&2
  exit 1
fi

./scripts/verify-real-e2e-attestation.sh
./scripts/verify-local-model-e2e-attestation.sh
expected_head="$(git rev-parse HEAD)"
bundle_identity=""
cleanup_bundle() {
  if [[ -n "${bundle_identity}" ]]; then
    "${safe_fs_cmd[@]}" remove export-release-attestations "${bundle_dir}" --kind dir \
      --expected-identity "${bundle_identity}" >/dev/null 2>&1 || true
  fi
}
trap cleanup_bundle EXIT

"${safe_fs_cmd[@]}" mkdirs export-release-attestations "${bundle_dir}"
bundle_identity="$("${safe_fs_cmd[@]}" identity export-release-attestations "${bundle_dir}" --kind dir)"
"${safe_fs_cmd[@]}" copy-file export-release-attestations \
  "${state_dir}/real-e2e-attestation.json" "${bundle_dir}/real-e2e-attestation.json" 0644 --dst-must-not-exist
"${safe_fs_cmd[@]}" copy-file export-release-attestations \
  "${state_dir}/local-model-e2e-attestation.json" "${bundle_dir}/local-model-e2e-attestation.json" 0644 --dst-must-not-exist
python3 "${repo_dir}/scripts/verify-release-attestation.py" \
  "${bundle_dir}" "${repo_dir}" "${expected_head}"
trap - EXIT
bundle_identity=""
printf 'Exported release attestation bundle: %s\n' "${bundle_dir}"
printf 'Commit this bundle as the only change after the tested source commit, then tag that bundle commit.\n'
