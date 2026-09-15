#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")
readonly MAX_SMOKE_OUTPUT_BYTES=$((1 * 1024 * 1024))
readonly MAX_SMOKE_RUNTIME_SECONDS=30

if [[ -z "${HOME:-}" ]]; then
  printf 'HOME must be set.\n' >&2
  exit 1
fi
if ! command -v -- python3 >/dev/null 2>&1; then
  printf 'python3 not found.\n' >&2
  exit 1
fi
if [[ -L "${safe_fs}" || ! -f "${safe_fs}" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
  exit 1
fi
if [[ "${SPEED_OF_CINNAMON_TEST_HOME:-}" != "1" ]]; then
  account_home="$(getent passwd "$(id -un)" 2>/dev/null | cut -d: -f6 || true)"
  if [[ -z "${account_home}" || "${HOME}" != "${account_home}" ]]; then
    printf 'Refusing to run with mismatched HOME: %s (expected %s).\n' "${HOME}" "${account_home}" >&2
    exit 1
  fi
fi
if [[ -L "${HOME}" ]]; then
  printf 'HOME must not be a symlink: %s\n' "${HOME}" >&2
  exit 1
fi
if [[ "${HOME}" == "/" ]]; then
  printf 'Refusing to run with root home directory.\n' >&2
  exit 1
fi
if [[ ! -d "${HOME}" ]]; then
  printf 'HOME must be an existing directory: %s\n' "${HOME}" >&2
  exit 1
fi

resolve_smoke_tmp_root() {
  local tmp_root="${TMPDIR:-/tmp}"
  if [[ ! "${tmp_root}" == /* ]]; then
    printf 'temporary root must be an absolute path: %s\n' "${tmp_root}" >&2
    exit 1
  fi
  if [[ -L "${tmp_root}" ]]; then
    printf 'temporary root must not be a symlink: %s\n' "${tmp_root}" >&2
    exit 1
  fi
  if [[ ! -d "${tmp_root}" || ! -w "${tmp_root}" ]]; then
    printf 'temporary root is not a writable directory: %s\n' "${tmp_root}" >&2
    exit 1
  fi
  if ! tmp_root="$(realpath "${tmp_root}")"; then
    printf 'failed to resolve temporary root: %s\n' "${tmp_root}" >&2
    exit 1
  fi
  if [[ -L "${tmp_root}" ]]; then
    printf 'temporary root must not be a symlink: %s\n' "${tmp_root}" >&2
    exit 1
  fi
  printf '%s\n' "${tmp_root}"
}

smoke_root=""
smoke_root_identity=""
if [[ "${SPEED_OF_CINNAMON_SMOKE_REAL_STATE:-0}" != "1" ]]; then
  smoke_tmp_root="$(resolve_smoke_tmp_root)"
  smoke_root="$(mktemp -d "${smoke_tmp_root}/speed-of-cinnamon-smoke-XXXXXX")"
  if [[ -L "${smoke_root}" ]]; then
    printf 'temporary smoke directory must not be a symlink: %s\n' "${smoke_root}" >&2
    exit 1
  fi
  if ! smoke_root_abs="$(realpath "${smoke_root}")"; then
    printf 'failed to resolve temporary smoke directory: %s\n' "${smoke_root}" >&2
    exit 1
  fi
  if [[ "${smoke_root_abs}" != "${smoke_tmp_root}/speed-of-cinnamon-smoke-"* ]]; then
    printf 'temporary smoke directory escaped temporary root: %s\n' "${smoke_root}" >&2
    exit 1
  fi
  smoke_root="${smoke_root_abs}"
  cleanup_smoke() {
    if [[ -n "${smoke_root_identity}" ]]; then
      "${safe_fs_cmd[@]}" remove smoke-backend "${smoke_root}" --kind dir \
        --expected-identity "${smoke_root_identity}" >/dev/null 2>&1 || true
    else
      printf 'refusing smoke cleanup without verified identity: %s\n' "${smoke_root}" >&2
    fi
  }
  trap cleanup_smoke EXIT
  if ! smoke_root_identity="$("${safe_fs_cmd[@]}" identity smoke-backend "${smoke_root}" --kind dir)"; then
    printf 'failed to capture smoke directory identity: %s\n' "${smoke_root}" >&2
    exit 1
  fi
  export XDG_STATE_HOME="${smoke_root}/state"
  export XDG_DATA_HOME="${smoke_root}/data"
  export XDG_CACHE_HOME="${smoke_root}/cache"
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
if command -v -- "${backend}" >/dev/null 2>&1; then
  backend="$(command -v -- "${backend}")"
fi

run_backend_bounded() {
  python3 - "${backend}" "${MAX_SMOKE_OUTPUT_BYTES}" "${MAX_SMOKE_RUNTIME_SECONDS}" "$@" <<'PY'
import os
import selectors
import signal
import subprocess
import sys
import time

backend, limit_text, timeout_text, *arguments = sys.argv[1:]
limit = int(limit_text)
timeout_seconds = float(timeout_text)
reap_timeout_seconds = 1.0
command = [backend, *arguments]
process = subprocess.Popen(
    command,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    start_new_session=True,
)
captured = bytearray()
selector = None

def terminate_process_group() -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (OSError, ValueError):
            pass
    try:
        process.wait(timeout=reap_timeout_seconds)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass

try:
    assert process.stdout is not None
    deadline = time.monotonic() + timeout_seconds
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            terminate_process_group()
            sys.stdout.buffer.write(captured)
            print("smoke backend timed out", file=sys.stderr)
            raise SystemExit(124)
        if not selector.select(remaining):
            terminate_process_group()
            sys.stdout.buffer.write(captured)
            print("smoke backend timed out", file=sys.stderr)
            raise SystemExit(124)
        chunk = os.read(process.stdout.fileno(), min(65_536, limit + 1 - len(captured)))
        if not chunk:
            selector.unregister(process.stdout)
            break
        captured.extend(chunk)
        if len(captured) > limit:
            terminate_process_group()
            sys.stdout.buffer.write(captured[:limit])
            print("smoke backend output is too large", file=sys.stderr)
            raise SystemExit(125)
    try:
        return_code = process.wait(timeout=max(0.0, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        terminate_process_group()
        print("smoke backend did not exit after output closed", file=sys.stderr)
        raise SystemExit(124)
finally:
    terminate_process_group()
    if selector is not None:
        selector.close()
    if process.stdout is not None:
        process.stdout.close()
sys.stdout.buffer.write(captured)
raise SystemExit(return_code)
PY
}

start_or_skip_audio_smoke() {
  local output
  if output="$(run_backend_bounded start "$@" 2>&1)"; then
    printf '%s\n' "${output}"
    return 0
  fi
  printf '%s\n' "${output}"
  if grep -Fq 'no recorder backend started successfully' <<<"${output}"; then
    printf 'Skipping live recorder smoke because no recorder backend can start in this session.\n' >&2
    run_backend_bounded cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
    exit 0
  fi
  return 1
}

assert_smoke_transcript() {
  local output="$1"
  local expected="$2"
  python3 -c '
import json
import sys

MAX_SMOKE_JSON_BYTES = 1 * 1024 * 1024
raw = sys.stdin.buffer.read(MAX_SMOKE_JSON_BYTES + 1)
if len(raw) > MAX_SMOKE_JSON_BYTES:
    raise SystemExit("smoke backend JSON payload is too large")


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key: {}".format(key))
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("non-finite JSON value: {}".format(value))


payload = json.loads(
    raw.decode("utf-8"),
    object_pairs_hook=reject_duplicate_keys,
    parse_constant=reject_constant,
)
expected = sys.argv[1]
if payload.get("status") != "done":
    raise SystemExit("smoke recording did not finish: {!r}".format(payload.get("status")))
if payload.get("transcript") != expected:
    raise SystemExit("smoke transcript does not match expected command output")
if payload.get("transcript_output_redacted"):
    raise SystemExit("smoke transcript unexpectedly remained redacted")
' "${expected}" <<<"${output}"
}

run_backend_bounded doctor --json
run_backend_bounded models --json
run_backend_bounded alarms list --json
run_backend_bounded alarms check --json
start_or_skip_audio_smoke \
  --max-seconds 1 \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-smoke" \
  --json
sleep 1
stop_output="$(run_backend_bounded stop \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-smoke" \
  --confirm-plaintext-output \
  --json)"
printf '%s\n' "${stop_output}"
assert_smoke_transcript "${stop_output}" "speed-of-cinnamon-smoke"

start_or_skip_audio_smoke \
  --max-seconds 1 \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-expired-smoke" \
  --json
sleep 2
run_backend_bounded status --json
toggle_output="$(run_backend_bounded toggle \
  --insert-method none \
  --transcriber command \
  --transcriber-command "printf speed-of-cinnamon-expired-smoke" \
  --confirm-plaintext-output \
  --json)"
printf '%s\n' "${toggle_output}"
assert_smoke_transcript "${toggle_output}" "speed-of-cinnamon-expired-smoke"

start_or_skip_audio_smoke \
  --max-seconds 10 \
  --insert-method none \
  --json
sleep 1
run_backend_bounded cancel --json
run_backend_bounded cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
