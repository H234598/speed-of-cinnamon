#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
state_dir="${XDG_STATE_HOME:-${HOME}/.local/state}/speed-of-cinnamon"
attestation="${state_dir}/local-model-e2e-attestation.json"

[[ -f "${attestation}" && ! -L "${attestation}" ]] || {
  printf 'local-model-e2e attestation missing. Run make local-model-e2e-acceptance after committing.\n' >&2
  exit 1
}
PYTHONPATH="${repo_dir}/src" python3 - "${attestation}" "${repo_dir}" "$(git -C "${repo_dir}" rev-parse HEAD)" <<'PY'
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from speed_of_cinnamon.models import ModelError, model_attestation_snapshot, source_attestation_snapshot

path, repo_dir, expected_head = sys.argv[1:]
ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_TTL = timedelta(hours=24)
MAX_ATTESTATION_BYTES = 4 * 1024 * 1024


def reject_constant(value: str) -> None:
    raise SystemExit(f"local-model-e2e attestation contains non-finite JSON value: {value}")


def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SystemExit(f"local-model-e2e attestation contains duplicate JSON key: {key}")
        result[key] = value
    return result


no_follow = getattr(os, "O_NOFOLLOW", None)
if not isinstance(no_follow, int) or isinstance(no_follow, bool) or no_follow <= 0:
    raise SystemExit("local-model-e2e attestation cannot be opened safely on this platform")
fd = None
primary_error = None
try:
    fd = os.open(path, os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0))
    entry = os.fstat(fd)
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_nlink != 1
        or entry.st_uid != os.geteuid()
        or entry.st_mode & 0o077
        or entry.st_size > MAX_ATTESTATION_BYTES
    ):
        raise SystemExit("local-model-e2e attestation must be an owned private regular file")
    with os.fdopen(fd, "rb", closefd=False) as handle:
        raw = handle.read(MAX_ATTESTATION_BYTES + 1)
    after = os.fstat(fd)
    if (
        len(raw) > MAX_ATTESTATION_BYTES
        or (entry.st_dev, entry.st_ino, entry.st_size, entry.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise SystemExit("local-model-e2e attestation changed while reading")
    data = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=unique_object,
        parse_constant=reject_constant,
    )
except BaseException as exc:
    primary_error = exc
    raise
finally:
    if fd is not None:
        try:
            os.close(fd)
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note("local-model-e2e attestation descriptor cleanup failed")
            else:
                raise SystemExit("local-model-e2e attestation descriptor cleanup failed") from cleanup_error
if not isinstance(data, dict):
    raise SystemExit("local-model-e2e attestation must be a JSON object")
if data.get("git_head") != expected_head:
    raise SystemExit("local-model-e2e attestation is for another commit; rerun make local-model-e2e-acceptance")
schema_version = data.get("schema_version")
if (
    isinstance(schema_version, bool)
    or not isinstance(schema_version, int)
    or schema_version != ATTESTATION_SCHEMA_VERSION
):
    raise SystemExit("local-model-e2e attestation schema version is unsupported; rerun make local-model-e2e-acceptance")
if set(data) - {
    "schema_version", "git_head", "created_at", "expires_at", "matrix", "source",
    "case_count", "ggml_case_count", "ct2_case_count", "models",
}:
    raise SystemExit("local-model-e2e attestation contains unexpected fields")
try:
    created = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    expires = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
except (KeyError, TypeError, ValueError) as exc:
    raise SystemExit("local-model-e2e attestation timestamp or expiry is invalid") from exc
if created.tzinfo is None or expires.tzinfo is None or expires - created != ATTESTATION_TTL:
    raise SystemExit("local-model-e2e attestation expiry contract is invalid")
now = datetime.now(UTC)
if now - created > ATTESTATION_TTL or now > expires or created - now > timedelta(minutes=5):
    raise SystemExit("local-model-e2e attestation is older than 24 hours; rerun make local-model-e2e-acceptance")
required = {"local-models", "generated-audio", "ggml", "ctranslate2", "explicit-backend", "auto-backend", "no-microphone", "no-clipboard"}
if not required.issubset(data.get("matrix", [])):
    raise SystemExit("local-model-e2e attestation matrix is incomplete")
for key in ("case_count", "ggml_case_count", "ct2_case_count"):
    if not isinstance(data.get(key), int) or isinstance(data[key], bool) or data[key] <= 0:
        raise SystemExit("local-model-e2e attestation counts are invalid")
if data["case_count"] != data["ggml_case_count"] + data["ct2_case_count"]:
    raise SystemExit("local-model-e2e attestation counts are inconsistent")
attested_models = data.get("models")
if not isinstance(attested_models, list) or not attested_models:
    raise SystemExit("local-model-e2e attestation model snapshot is missing")
for model in attested_models:
    if not isinstance(model, dict) or not isinstance(model.get("tested_languages"), list):
        raise SystemExit("local-model-e2e attestation model snapshot is invalid")
    if not all(isinstance(language, str) for language in model["tested_languages"]):
        raise SystemExit("local-model-e2e attestation model languages are invalid")
try:
    current_models = model_attestation_snapshot()
except (ModelError, OSError, RuntimeError, TypeError, ValueError) as exc:
    raise SystemExit("local-model-e2e current model inventory is not attestable") from exc
attested_without_cases = [
    {key: value for key, value in model.items() if key != "tested_languages"}
    for model in attested_models
]
canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
if canonical(sorted(attested_without_cases, key=lambda model: str(model.get("name")))) != canonical(current_models):
    raise SystemExit("local-model-e2e attestation model artifacts changed; rerun acceptance")
attested_source = data.get("source")
if not isinstance(attested_source, list) or not attested_source:
    raise SystemExit("local-model-e2e attestation source snapshot is missing")
try:
    current_source = source_attestation_snapshot(Path(repo_dir))
except (ModelError, OSError, RuntimeError, TypeError, ValueError) as exc:
    raise SystemExit("local-model-e2e current source tree is not attestable") from exc
if canonical(attested_source) != canonical(current_source):
    raise SystemExit("local-model-e2e attestation source changed; rerun acceptance")
PY
