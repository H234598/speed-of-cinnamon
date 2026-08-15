#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

if [[ "${EUID:-0}" -eq 0 ]]; then
  printf 'Do not run this script as root. It uses sudo for package install.\n' >&2
  exit 1
fi

for tool in dnf sudo; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
dnf_cmd="$(command -v -- dnf)"
sudo_cmd="$(command -v -- sudo)"
if [[ -z "${dnf_cmd}" ]]; then
  printf 'dnf not found. This helper is intended for Fedora.\n' >&2
  exit 1
fi

"${sudo_cmd}" "${dnf_cmd}" install -y \
  python3 \
  pipewire-utils \
  pulseaudio-utils \
  xdotool \
  wtype \
  libnotify

cat <<'MSG'

Optional standalone CLI clipboard helpers:
  run this helper's resolved sudo/dnf command with: xclip xsel

Optional ALSA fallback recorder:
  run this helper's resolved sudo/dnf command with: alsa-utils

Reload Cinnamon after installing the applet if it is already open:
  Alt+F2, r, Enter
MSG
