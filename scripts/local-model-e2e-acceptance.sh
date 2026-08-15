#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

# Local release-gate runs must not contact Hugging Face or send implicit
# credentials. Model inventory and model loading use only verified local paths.
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_DISABLE_IMPLICIT_TOKEN=1
unset HF_TOKEN HUGGINGFACE_HUB_TOKEN

# Real local-model acceptance. It generates audio only and never accesses a
# microphone, clipboard, paste target, downloaded model, or persistent text.
if [[ "${SOC_LOCAL_MODEL_E2E:-0}" != "1" ]]; then
  printf 'Refusing local model acceptance test. Set SOC_LOCAL_MODEL_E2E=1.\n' >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
backend="${repo_dir}/scripts/dev-backend.sh"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/speed-of-cinnamon"
attestation="${state_dir}/local-model-e2e-attestation.json"
tmp_root=""
tmp_identity=""

for tool in espeak-ng ffmpeg python3 mktemp git; do
  command -v -- "${tool}" >/dev/null 2>&1 || {
    printf 'local-model-e2e: required tool missing: %s\n' "${tool}" >&2
    exit 2
  }
done
[[ -x "${backend}" ]] || {
  printf 'local-model-e2e: development backend is unavailable.\n' >&2
  exit 2
}
[[ -f "${safe_fs}" && ! -L "${safe_fs}" ]] || {
  printf 'local-model-e2e: invalid safe filesystem helper.\n' >&2
  exit 2
}

cleanup() {
  set +e
  if [[ -n "${tmp_root}" && -n "${tmp_identity}" ]]; then
    python3 "${safe_fs}" remove local-model-e2e "${tmp_root}" --kind dir \
      --expected-identity "${tmp_identity}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/speed-of-cinnamon-local-model-e2e.XXXXXX")"
tmp_identity="$(python3 "${safe_fs}" identity local-model-e2e "${tmp_root}" --kind dir)"
models_json="${tmp_root}/models.json"
cases_file="${tmp_root}/cases.tsv"
models_manifest="${tmp_root}/models-manifest.json"

espeak-ng -v de -s 145 -w "${tmp_root}/source-de.wav" \
  'Eins zwei drei vier fünf. Dies ist ein lokaler deutscher Transkriptionstest.'
espeak-ng -v en-us -s 145 -w "${tmp_root}/source-en.wav" \
  'One two three four five. This is a local English transcription test.'
ffmpeg -nostdin -loglevel error -y -i "${tmp_root}/source-de.wav" -ac 1 -ar 16000 "${tmp_root}/speech-de.flac"
ffmpeg -nostdin -loglevel error -y -i "${tmp_root}/source-en.wav" -ac 1 -ar 16000 "${tmp_root}/speech-en.flac"

"${backend}" models --json >"${models_json}"
PYTHONPATH="${repo_dir}/src" python3 - "${models_json}" "${models_manifest}" >"${cases_file}" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

from speed_of_cinnamon.models import CATALOG, model_attestation_snapshot, model_path

models_json_path, models_manifest_path = sys.argv[1:]
MAX_LOCAL_MODEL_JSON_BYTES = 4 * 1024 * 1024


def load_json(path: Path, label: str) -> object:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_LOCAL_MODEL_JSON_BYTES + 1)
    except OSError as exc:
        raise SystemExit(f"local-model-e2e: cannot read {label}") from exc
    if len(raw) > MAX_LOCAL_MODEL_JSON_BYTES:
        raise SystemExit(f"local-model-e2e: {label} exceeds {MAX_LOCAL_MODEL_JSON_BYTES} bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError, MemoryError) as exc:
        raise SystemExit(f"local-model-e2e: {label} is invalid JSON") from exc


payload = load_json(Path(models_json_path), "model inventory")
if not isinstance(payload, dict):
    raise SystemExit("local-model-e2e: model inventory is not a JSON object")
models = payload.get("models")
if payload.get("status") != "done" or not isinstance(models, list):
    raise SystemExit("local-model-e2e: model inventory is invalid")

catalog = {model.name: model for model in CATALOG}
seen_backends = set()
unverified_installed = []
selected_names = set()
selected_languages = {}
limit_per_backend = int(os.environ.get("SOC_LOCAL_MODEL_E2E_LIMIT_PER_BACKEND", "0"))
if limit_per_backend < 0:
    raise SystemExit("local-model-e2e: model limit must not be negative")
selected_per_backend = {}
for entry in models:
    if not isinstance(entry, dict) or entry.get("downloaded") is not True:
        continue
    backend = entry.get("backend")
    model_format = entry.get("model_format")
    name = entry.get("name")
    languages = entry.get("languages")
    if backend not in {"whisper-cpp", "faster-whisper"}:
        continue
    if limit_per_backend and selected_per_backend.get(backend, 0) >= limit_per_backend:
        continue
    if (backend == "whisper-cpp") != (model_format == "ggml"):
        raise SystemExit("local-model-e2e: model backend and format disagree")
    if (backend == "faster-whisper") != (model_format == "ctranslate2"):
        raise SystemExit("local-model-e2e: model backend and format disagree")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", name):
        raise SystemExit("local-model-e2e: model name is invalid")
    model = catalog.get(name)
    if model is None:
        raise SystemExit("local-model-e2e: model is absent from trusted catalog")
    path = model_path(model)
    if not path.is_absolute() or not path.exists() or any(char in str(path) for char in "\t\r\n\x00"):
        raise SystemExit("local-model-e2e: model path is invalid")
    if entry.get("verified") is not True:
        unverified_installed.append(name)
        continue
    if not isinstance(languages, list) or not all(isinstance(language, str) for language in languages):
        raise SystemExit("local-model-e2e: model language metadata is invalid")
    normalized_languages = set()
    for language in languages:
        normalized = language.strip().lower()
        if normalized in {"en", "eng", "english"}:
            normalized_languages.add("en")
        elif normalized in {"de", "deu", "german"}:
            normalized_languages.add("de")
        else:
            raise SystemExit("local-model-e2e: unsupported installed model language")
    for language in sorted(normalized_languages or {"de", "en"}):
        print(f"{backend}\t{name}\t{path}\t{language}")
        seen_backends.add(backend)
        selected_names.add(name)
        selected_languages.setdefault(name, set()).add(language)
    selected_per_backend[backend] = selected_per_backend.get(backend, 0) + 1

if unverified_installed:
    raise SystemExit(
        "local-model-e2e: installed model integrity not verified: "
        + ", ".join(sorted(unverified_installed))
    )

missing = {"whisper-cpp", "faster-whisper"} - seen_backends
if missing:
    raise SystemExit("local-model-e2e: installed model matrix lacks " + ", ".join(sorted(missing)))

attested_models = []
for model in model_attestation_snapshot():
    name = model["name"]
    if name not in selected_names:
        raise SystemExit("local-model-e2e: model selection is incomplete")
    model_with_cases = dict(model)
    model_with_cases["tested_languages"] = sorted(selected_languages[name])
    attested_models.append(model_with_cases)
if {model["name"] for model in attested_models} != selected_names:
    raise SystemExit("local-model-e2e: model attestation selection is incomplete")
Path(models_manifest_path).write_text(
    json.dumps(attested_models, sort_keys=True, separators=(",", ":")) + "\n",
    encoding="utf-8",
)
PY

case_count=0
ggml_case_count=0
ct2_case_count=0
while IFS=$'\t' read -r model_backend model_name model_path language; do
  [[ -n "${model_backend}" && -n "${model_name}" && -n "${model_path}" && -n "${language}" ]] || {
    printf 'local-model-e2e: malformed test case.\n' >&2
    exit 1
  }
  audio_path="${tmp_root}/speech-${language}.flac"
  [[ -f "${audio_path}" ]] || {
    printf 'local-model-e2e: generated fixture is missing.\n' >&2
    exit 1
  }
  for requested_backend in "${model_backend}" auto; do
    result_path="${tmp_root}/result-${case_count}.json"
    if ! "${backend}" transcribe-file "${audio_path}" --json \
      --language "${language}" \
      --transcriber "${requested_backend}" \
      --whisper-model "${model_path}" \
      --post-process-backend none \
      --artifact-encryption off \
      --confirm-plaintext-output >"${result_path}"; then
      printf 'local-model-e2e: failed backend=%s model=%s language=%s mode=%s.\n' \
        "${model_backend}" "${model_name}" "${language}" "${requested_backend}" >&2
      exit 1
    fi
    python3 - "${result_path}" <<'PY'
import json
import sys
from pathlib import Path

MAX_LOCAL_MODEL_JSON_BYTES = 4 * 1024 * 1024
result_path = Path(sys.argv[1])
try:
    with result_path.open("rb") as handle:
        raw = handle.read(MAX_LOCAL_MODEL_JSON_BYTES + 1)
except OSError as exc:
    raise SystemExit("local-model-e2e: transcription result cannot be read") from exc
if len(raw) > MAX_LOCAL_MODEL_JSON_BYTES:
    raise SystemExit(f"local-model-e2e: transcription result exceeds {MAX_LOCAL_MODEL_JSON_BYTES} bytes")
try:
    payload = json.loads(raw.decode("utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError, MemoryError) as exc:
    raise SystemExit("local-model-e2e: transcription result is invalid JSON") from exc
if not isinstance(payload, dict):
    raise SystemExit("local-model-e2e: transcription result is not a JSON object")
text = payload.get("transcript")
if payload.get("status") != "done" or not isinstance(text, str) or len(text.strip()) < 3:
    raise SystemExit("local-model-e2e: transcription did not produce usable text")
PY
    case_count=$((case_count + 1))
    if [[ "${model_backend}" == "whisper-cpp" ]]; then
      ggml_case_count=$((ggml_case_count + 1))
    else
      ct2_case_count=$((ct2_case_count + 1))
    fi
  done
done <"${cases_file}"

(( case_count > 0 && ggml_case_count > 0 && ct2_case_count > 0 )) || {
  printf 'local-model-e2e: no complete GGML and CT2 matrix was executed.\n' >&2
  exit 1
}

git_head="$(git -C "${repo_dir}" rev-parse HEAD)"
created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
PYTHONPATH="${repo_dir}/src" python3 - "${attestation}" "${git_head}" "${created_at}" "${case_count}" "${ggml_case_count}" "${ct2_case_count}" "${models_manifest}" "${repo_dir}" <<'PY'
import json
import os
import secrets
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

from speed_of_cinnamon.models import ModelError, source_attestation_snapshot

path = Path(sys.argv[1])
head, created_at, total, ggml, ct2, models_manifest, repo_dir = sys.argv[2:]
created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
expires_at = (created + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
MAX_LOCAL_MODEL_JSON_BYTES = 4 * 1024 * 1024
try:
    with Path(models_manifest).open("rb") as handle:
        raw = handle.read(MAX_LOCAL_MODEL_JSON_BYTES + 1)
except OSError as exc:
    raise SystemExit("local-model-e2e: model manifest cannot be read") from exc
if len(raw) > MAX_LOCAL_MODEL_JSON_BYTES:
    raise SystemExit(f"local-model-e2e: model manifest exceeds {MAX_LOCAL_MODEL_JSON_BYTES} bytes")
try:
    models = json.loads(raw.decode("utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError, MemoryError) as exc:
    raise SystemExit("local-model-e2e: model manifest is invalid JSON") from exc
try:
    source = source_attestation_snapshot(Path(repo_dir))
except (ModelError, OSError, RuntimeError, TypeError, ValueError) as exc:
    raise SystemExit("local-model-e2e: source tree is not attestable") from exc
path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
parent = os.lstat(path.parent)
if not stat.S_ISDIR(parent.st_mode) or stat.S_ISLNK(parent.st_mode) or parent.st_uid != os.geteuid():
    raise SystemExit("local-model-e2e: unsafe attestation directory")
os.chmod(path.parent, 0o700)
data = {
    "schema_version": 1,
    "git_head": head,
    "created_at": created_at,
    "expires_at": expires_at,
    "matrix": ["local-models", "generated-audio", "ggml", "ctranslate2", "explicit-backend", "auto-backend", "no-microphone", "no-clipboard"],
    "case_count": int(total),
    "ggml_case_count": int(ggml),
    "ct2_case_count": int(ct2),
    "models": models,
    "source": source,
}
tmp = path.parent / f".{path.name}.{secrets.token_hex(16)}.tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
finally:
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
PY
printf 'local-model-e2e: passed %s runs; attestation: %s\n' "${case_count}" "${attestation}"
