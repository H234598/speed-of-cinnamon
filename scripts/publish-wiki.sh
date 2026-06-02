#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wiki_url="${WIKI_URL:-https://github.com/H234598/speed-of-cinnamon.wiki.git}"

if ! command -v -- git >/dev/null 2>&1; then
  printf 'git not found.\n' >&2
  exit 1
fi
if [[ ! "${wiki_url}" =~ ^https://github\\.com/[A-Za-z0-9._-]+/[A-Za-z0-9._-]+\\.wiki\\.git$ ]]; then
  printf 'Invalid wiki URL: %s\n' "${wiki_url}" >&2
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
  work_root="/tmp"
fi
if [[ -L "${work_root}" ]]; then
  work_root="${repo_dir}/.tmp"
fi
if [[ ! -d "${work_root}" || ! -w "${work_root}" ]]; then
  work_root="${repo_dir}/.tmp"
fi
if [[ -L "${work_root}" ]]; then
  work_root="/tmp"
fi
mkdir -p "${work_root}"
work_dir="$(mktemp -d "${work_root}/speed-of-cinnamon-publish-wiki-XXXXXX")"
cleanup() {
  rm -rf -- "${work_dir}"
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

cp "${repo_dir}/docs/wiki/Home.md" "${work_dir}/wiki/Home.md"
cp "${repo_dir}/docs/user-guide.md" "${work_dir}/wiki/User-Guide.md"
cp "${repo_dir}/docs/cli-reference.md" "${work_dir}/wiki/CLI-Reference.md"
cp "${repo_dir}/docs/architecture.md" "${work_dir}/wiki/Architecture.md"
cp "${repo_dir}/docs/development.md" "${work_dir}/wiki/Development.md"
cp "${repo_dir}/docs/fedora-cinnamon-runbook.md" "${work_dir}/wiki/Fedora-Cinnamon-Runbook.md"

cd "${work_dir}/wiki"
if [[ -z "$(git status --porcelain -- .)" ]]; then
  printf 'Wiki already up to date.\n'
  exit 0
fi

git add Home.md User-Guide.md CLI-Reference.md Architecture.md Development.md Fedora-Cinnamon-Runbook.md
git commit -m "Update Speed of Cinnamon documentation"
git push origin HEAD:master
printf 'Updated wiki at %s\n' "${wiki_url}"
