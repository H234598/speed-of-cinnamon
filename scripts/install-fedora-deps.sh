#!/usr/bin/env bash
set -euo pipefail

if ! command -v dnf >/dev/null 2>&1; then
  printf 'dnf not found. This helper is intended for Fedora.\n' >&2
  exit 1
fi

sudo dnf install -y \
  python3 \
  pipewire-utils \
  xdotool \
  libnotify

cat <<'MSG'

Optional standalone CLI clipboard helpers:
  sudo dnf install -y xclip

Reload Cinnamon after installing the applet if it is already open:
  Alt+F2, r, Enter
MSG

