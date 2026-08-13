#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/speed-of-cinnamon"
attestation="${state_dir}/real-e2e-attestation.json"

[[ -f "${attestation}" && ! -L "${attestation}" ]] || {
  printf 'real-e2e attestation missing. Run make real-e2e-acceptance after committing.\n' >&2
  exit 1
}
python3 - "${attestation}" "$(git -C "${repo_dir}" rev-parse HEAD)" <<'PY'
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta

path, expected_head = sys.argv[1:]
entry = os.lstat(path)
if not stat.S_ISREG(entry.st_mode) or entry.st_uid != os.geteuid() or entry.st_mode & 0o077:
    raise SystemExit("real-e2e attestation must be an owned private regular file")
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
if data.get("git_head") != expected_head:
    raise SystemExit("real-e2e attestation is for another commit; rerun make real-e2e-acceptance")
try:
    created = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
except (KeyError, TypeError, ValueError) as exc:
    raise SystemExit("real-e2e attestation timestamp is invalid") from exc
if datetime.now(UTC) - created > timedelta(hours=24):
    raise SystemExit("real-e2e attestation is older than 24 hours; rerun make real-e2e-acceptance")
if not {"live-applet", "arecord", "pipewire", "openai-compatible", "flex-on", "flex-off"}.issubset(data.get("matrix", [])):
    raise SystemExit("real-e2e attestation matrix is incomplete")
PY
