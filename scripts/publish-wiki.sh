#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
expected_wiki_url="https://github.com/H234598/speed-of-cinnamon.wiki.git"
wiki_url="${WIKI_URL:-${expected_wiki_url}}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"

for tool in git python3 stat command realpath; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
if [[ ! -f "${safe_fs}" || -L "${safe_fs}" ]]; then
  printf 'missing required helper: %s\n' "${safe_fs}" >&2
  exit 1
fi
safe_fs_cmd=(python3 "${safe_fs}")
if [[ "${wiki_url}" != "${expected_wiki_url}" ]]; then
  printf 'Invalid wiki URL: expected %s, got %s\n' "${expected_wiki_url}" "${wiki_url}" >&2
  exit 1
fi

require_source_file() {
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
if ! "${safe_fs_cmd[@]}" mkdirs publish-wiki "${work_root}"; then
  printf 'failed to prepare wiki publish temporary root: %s\n' "${work_root}" >&2
  exit 1
fi
work_dir="$(mktemp -d "${work_root}/speed-of-cinnamon-publish-wiki-XXXXXX")"
if [[ -L "${work_dir}" ]]; then
  printf 'temporary wiki publish workspace must not be a symlink: %s\n' "${work_dir}" >&2
  exit 1
fi
if ! work_dir_abs="$(realpath "${work_dir}")"; then
  printf 'failed to resolve temporary wiki publish workspace: %s\n' "${work_dir}" >&2
  exit 1
fi
if [[ "${work_dir_abs}" != "${work_root}/speed-of-cinnamon-publish-wiki-"* ]]; then
  printf 'temporary wiki publish workspace escaped temporary root: %s\n' "${work_dir}" >&2
  exit 1
fi
work_dir="${work_dir_abs}"
cleanup() {
  if [[ -n "${work_dir}" && -e "${work_dir}" ]]; then
    if ! "${safe_fs_cmd[@]}" remove publish-wiki "${work_dir}" --kind dir; then
      printf 'failed to clean wiki publish workspace: %s\n' "${work_dir}" >&2
    fi
  fi
}
trap cleanup EXIT

if ! git clone "${wiki_url}" "${work_dir}/wiki"; then
  printf 'failed to clone wiki repository; refusing to initialize a replacement wiki checkout: %s\n' "${wiki_url}" >&2
  exit 1
fi

require_source_file "${repo_dir}/docs/wiki/Home.md" "wiki source"
require_source_file "${repo_dir}/docs/user-guide.md" "wiki source"
require_source_file "${repo_dir}/docs/cli-reference.md" "wiki source"
require_source_file "${repo_dir}/docs/architecture.md" "wiki source"
require_source_file "${repo_dir}/docs/development.md" "wiki source"
require_source_file "${repo_dir}/docs/fedora-cinnamon-runbook.md" "wiki source"

"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/wiki/Home.md" "${work_dir}/wiki/Home.md" 0644
"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/user-guide.md" "${work_dir}/wiki/User-Guide.md" 0644
"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/cli-reference.md" "${work_dir}/wiki/CLI-Reference.md" 0644
"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/architecture.md" "${work_dir}/wiki/Architecture.md" 0644
"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/development.md" "${work_dir}/wiki/Development.md" 0644
"${safe_fs_cmd[@]}" copy-file publish-wiki "${repo_dir}/docs/fedora-cinnamon-runbook.md" "${work_dir}/wiki/Fedora-Cinnamon-Runbook.md" 0644

cd "${work_dir}/wiki"
if [[ -z "$(git status --porcelain -- .)" ]]; then
  printf 'Wiki already up to date.\n'
  exit 0
fi

git add Home.md User-Guide.md CLI-Reference.md Architecture.md Development.md Fedora-Cinnamon-Runbook.md
git commit -m "Update Speed of Cinnamon documentation"
git push origin HEAD:master
printf 'Updated wiki at %s\n' "${wiki_url}"
