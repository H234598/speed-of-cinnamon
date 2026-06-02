#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

if [[ -z "${HOME:-}" ]]; then
  printf 'HOME must be set.\n' >&2
  exit 1
fi

if [[ ! -x "${HOME}/.local/bin/speed-of-cinnamon" && $# -eq 0 ]]; then
  printf 'backend not executable or missing; provide explicit backend path as first argument\n' >&2
  exit 1
fi

backend="${1:-${HOME}/.local/bin/speed-of-cinnamon}"
if [[ ! -x "${backend}" ]]; then
  printf 'backend path is not executable: %s\n' "${backend}" >&2
  exit 1
fi
if command -v "${backend}" >/dev/null 2>&1; then
  backend="$(command -v "${backend}")"
fi

"${backend}" doctor --json
"${backend}" models --json
"${backend}" alarms list --json
"${backend}" alarms check --json
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
