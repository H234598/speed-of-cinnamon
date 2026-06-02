#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

if [[ "${EUID:-0}" -eq 0 ]]; then
  printf 'Do not run this script as root. It uses sudo for package install.\n' >&2
  exit 1
fi

for tool in dnf sudo command; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
if ! command -v -- dnf >/dev/null 2>&1; then
  printf 'dnf not found. This helper is intended for Fedora.\n' >&2
  exit 1
fi

sudo dnf install -y \
  python3 \
  pipewire-utils \
  pulseaudio-utils \
  xdotool \
  libnotify

cat <<'MSG'

Optional standalone CLI clipboard helpers:
  sudo dnf install -y xclip xsel

Optional ALSA fallback recorder:
  sudo dnf install -y alsa-utils

Reload Cinnamon after installing the applet if it is already open:
  Alt+F2, r, Enter
MSG
