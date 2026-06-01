#!/usr/bin/env bash
set -euo pipefail

uuid="speed-of-cinnamon@H234598"
rm -rf "${HOME}/.local/share/cinnamon/applets/${uuid}"
rm -f "${HOME}/.local/bin/speed-of-cinnamon"
printf 'Removed %s applet and backend wrapper.\n' "${uuid}"

