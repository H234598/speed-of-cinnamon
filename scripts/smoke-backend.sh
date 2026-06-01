#!/usr/bin/env bash
set -euo pipefail

backend="${1:-${HOME}/.local/bin/speed-of-cinnamon}"

"${backend}" doctor --json
"${backend}" models --json
"${backend}" start \
  --max-seconds 1 \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-smoke" \
  --json
sleep 1
"${backend}" stop \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-smoke" \
  --json

"${backend}" start \
  --max-seconds 1 \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-expired-smoke" \
  --json
sleep 2
"${backend}" status --json
"${backend}" toggle \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-expired-smoke" \
  --json

"${backend}" start \
  --max-seconds 10 \
  --insert-method none \
  --json
sleep 1
"${backend}" cancel --json
"${backend}" cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
