#!/usr/bin/env bash
set -euo pipefail

uuid="speed-of-cinnamon@H234598"
rm -rf "${HOME}/.local/share/cinnamon/applets/${uuid}"
rm -f "${HOME}/.local/bin/speed-of-cinnamon"
rm -f "${HOME}/.local/share/man/man1/speed-of-cinnamon.1"
rm -f "${HOME}/.local/share/man/man1/speed-of-cinnamon-alarms.1"
printf 'Removed %s applet, backend wrapper, and local man pages.\n' "${uuid}"
