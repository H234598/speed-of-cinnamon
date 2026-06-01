#!/usr/bin/env bash
set -euo pipefail

backend="${1:-${HOME}/.local/bin/speed-of-cinnamon}"

"${backend}" doctor --json
"${backend}" start \
  --max-seconds 1 \
  --insert-method none \
  --transcriber-command "printf speed-of-cinnamon-smoke" \
  --json
sleep 1
"${backend}" stop \
  --insert-method none \
  --transcriber-command "printf speed-of-cinnamon-smoke" \
  --json

