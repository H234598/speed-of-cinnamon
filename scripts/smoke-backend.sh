#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

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

start_or_skip_audio_smoke() {
  local output
  if output="$("${backend}" start "$@" 2>&1)"; then
    printf '%s\n' "${output}"
    return 0
  fi
  printf '%s\n' "${output}"
  if grep -Fq 'no recorder backend started successfully' <<<"${output}"; then
    printf 'Skipping live recorder smoke because no recorder backend can start in this session.\n' >&2
    "${backend}" cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
    exit 0
  fi
  return 1
}

"${backend}" doctor --json
"${backend}" models --json
"${backend}" alarms list --json
"${backend}" alarms check --json
start_or_skip_audio_smoke \
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

start_or_skip_audio_smoke \
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

start_or_skip_audio_smoke \
  --max-seconds 10 \
  --insert-method none \
  --json
sleep 1
"${backend}" cancel --json
"${backend}" cleanup --keep-transcripts 100 --keep-recordings 25 --dry-run --json
