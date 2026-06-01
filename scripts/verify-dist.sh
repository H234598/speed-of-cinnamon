#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'usage: %s dist/speed-of-cinnamon-VERSION.tar.gz\n' "$0" >&2
  exit 2
fi

tarball="$(realpath "$1")"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

tar -xzf "${tarball}" -C "${tmp_dir}"
package_dir="$(find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d | sort | head -n 1)"
if [[ -z "${package_dir}" ]]; then
  printf 'archive did not contain a package directory: %s\n' "${tarball}" >&2
  exit 1
fi

for path in \
  README.md \
  LICENSE \
  RELEASE-MANIFEST.txt \
  Makefile \
  pyproject.toml \
  packaging/speed-of-cinnamon.spec \
  docs/fedora-cinnamon-runbook.md \
  files/speed-of-cinnamon@H234598/applet.js \
  files/speed-of-cinnamon@H234598/metadata.json \
  files/speed-of-cinnamon@H234598/settings-schema.json \
  scripts/install-local.sh \
  scripts/publish-github-release.sh \
  scripts/verify-authorship.sh \
  scripts/verify-rpm.sh \
  src/speed_of_cinnamon/cli.py \
  src/speed_of_cinnamon/setup_plan.py \
  tests/test_ci_static.py \
  tests/test_cli.py
do
  if [[ ! -e "${package_dir}/${path}" ]]; then
    printf 'archive is missing %s\n' "${path}" >&2
    exit 1
  fi
done

make -C "${package_dir}" check

home_dir="${tmp_dir}/home"
mkdir -p "${home_dir}"
HOME="${home_dir}" make -C "${package_dir}" install-local

backend="${home_dir}/.local/bin/speed-of-cinnamon"
applet="${home_dir}/.local/share/cinnamon/applets/speed-of-cinnamon@H234598/applet.js"
if [[ ! -x "${backend}" || ! -f "${applet}" ]]; then
  printf 'installed package is incomplete\n' >&2
  exit 1
fi

HOME="${home_dir}" "${backend}" setup \
  --applet \
  --settings-json '{"transcriber":"command","transcriber-command":"printf ok","insert-method":"clipboard-paste"}' \
  --json > "${tmp_dir}/setup.json"
python3 -m json.tool "${tmp_dir}/setup.json" >/dev/null

printf 'Verified %s\n' "${tarball}"
