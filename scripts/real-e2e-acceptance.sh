#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

# Real applet acceptance test. It never writes clipboard or sends keystrokes.
if [[ "${SOC_REAL_E2E:-0}" != "1" ]]; then
  printf 'Refusing live API test. Set SOC_REAL_E2E=1. This consumes API quota.\n' >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
uuid="speed-of-cinnamon@H234598"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/speed-of-cinnamon"
attestation="${state_dir}/real-e2e-attestation.json"
tmp_root=""
tmp_identity=""
sink_name="soc-real-e2e-$$"
sink_module=""
old_source=""
snapshot_set=0

for tool in arecord espeak-ng ffmpeg gdbus pactl pw-play python3 mktemp git; do
  command -v -- "${tool}" >/dev/null 2>&1 || {
    printf 'real-e2e: required tool missing: %s\n' "${tool}" >&2
    exit 2
  }
done
[[ -f "${safe_fs}" && ! -L "${safe_fs}" ]] || {
  printf 'real-e2e: invalid safe filesystem helper.\n' >&2
  exit 2
}
[[ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]] || {
  printf 'real-e2e: Cinnamon session D-Bus is unavailable.\n' >&2
  exit 2
}

eval_cinnamon() {
  gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon \
    --method org.Cinnamon.Eval "$1"
}

cleanup() {
  set +e
  if (( snapshot_set )); then
    eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; if(i&&i._socRealE2eSnapshot){Object.assign(i,i._socRealE2eSnapshot);delete i._socRealE2eSnapshot;} \"restored\";" >/dev/null
  fi
  if [[ -n "${old_source}" ]]; then
    pactl set-default-source "${old_source}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${sink_module}" ]]; then
    pactl unload-module "${sink_module}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${tmp_root}" && -n "${tmp_identity}" ]]; then
    python3 "${safe_fs}" remove real-e2e "${tmp_root}" --kind dir \
      --expected-identity "${tmp_identity}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/speed-of-cinnamon-real-e2e.XXXXXX")"
tmp_identity="$(python3 "${safe_fs}" identity real-e2e "${tmp_root}" --kind dir)"

active="$(eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; i ? (i.transcriber===\"openai-compatible\" && i.openaiCompatibleUrl && i.openaiCompatibleModel && i.openaiCompatibleTextModel ? \"ready\" : \"misconfigured\") : \"missing\";")"
if [[ "${active}" != *ready* ]]; then
  printf 'real-e2e: running applet is missing or OpenAI-compatible API/model settings are incomplete.\n' >&2
  exit 2
fi

old_source="$(pactl get-default-source)"
sink_module="$(pactl load-module module-null-sink sink_name="${sink_name}")"
pactl set-default-source "${sink_name}.monitor"

espeak-ng -v de -s 145 -w "${tmp_root}/source.wav" \
  'Dies ist ein echter Speed of Cinnamon Aufnahme Test.'
ffmpeg -nostdin -loglevel error -y -i "${tmp_root}/source.wav" -ac 1 -ar 16000 "${tmp_root}/speech.wav"

eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; if(!i) throw new Error(\"applet missing\"); i._socRealE2eSnapshot={insertMethod:i.insertMethod,autoRelisten:i.autoRelisten,showTranscriptionNotifications:i.showTranscriptionNotifications,openaiCompatibleFlexProcessing:i.openaiCompatibleFlexProcessing,inputDevice:i.inputDevice}; if(i.recorder===\"arecord\") i.inputDevice=\"pipewire\"; i.insertMethod=\"none\"; i.autoRelisten=false; i.showTranscriptionNotifications=false; \"prepared\";" >/dev/null
snapshot_set=1

run_case() {
  local flex="$1"
  local state=""
  eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; i.openaiCompatibleFlexProcessing=${flex}; i._toggleRecording(\"start\"); \"started\";" >/dev/null
  for _ in $(seq 1 30); do
    state="$(eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; i&&i.status===\"recording\" ? \"recording\" : (i&&i.status||\"unknown\");")"
    [[ "${state}" == *recording* ]] && break
    [[ "${state}" == *error* ]] && break
    sleep 1
  done
  if [[ "${state}" != *recording* ]]; then
    printf 'real-e2e: recorder did not become ready for flex=%s (state: %s)\n' "${flex}" "${state}" >&2
    return 1
  fi
  pw-play --target "${sink_name}" "${tmp_root}/speech.wav"
  sleep 1
  eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; i._toggleRecording(\"stop\"); \"stopped\";" >/dev/null
  for _ in $(seq 1 90); do
    state="$(eval_cinnamon "const i=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\")[0]; i&&i.status===\"done\"&&i.lastTranscript ? \"done\" : (i&&i.status||\"unknown\");")"
    [[ "${state}" == *done* ]] && return 0
    [[ "${state}" == *failed* ]] && break
    sleep 1
  done
  printf 'real-e2e: applet case flex=%s failed with state: %s\n' "${flex}" "${state}" >&2
  return 1
}

run_case true
run_case false

mkdir -p -- "${state_dir}"
chmod 700 "${state_dir}"
git_head="$(git -C "${repo_dir}" rev-parse HEAD)"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
attestation_tmp="${attestation}.tmp.$$"
printf '{"git_head":"%s","created_at":"%s","matrix":["live-applet","arecord","pipewire","openai-compatible","gpt-transcribe-configured","text-model-configured","flex-on","flex-off","clipboard-disabled"]}\n' \
  "${git_head}" "${created_at}" >"${attestation_tmp}"
chmod 600 "${attestation_tmp}"
mv -f -- "${attestation_tmp}" "${attestation}"
printf 'real-e2e: passed; attestation: %s\n' "${attestation}"
