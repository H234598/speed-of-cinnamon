#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wiki_url="${WIKI_URL:-https://github.com/H234598/speed-of-cinnamon.wiki.git}"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

if ! command -v git >/dev/null 2>&1; then
  printf 'git not found.\n' >&2
  exit 1
fi

if ! git clone "${wiki_url}" "${work_dir}/wiki"; then
  mkdir -p "${work_dir}/wiki"
  git -C "${work_dir}/wiki" init -b master
  git -C "${work_dir}/wiki" remote add origin "${wiki_url}"
fi

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
