#!/usr/bin/env python3
"""Verify source-bound E2E evidence committed for a release tag."""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from speed_of_cinnamon.models import CATALOG, ModelError, source_attestation_snapshot


MAX_ATTESTATION_BYTES = 4 * 1024 * 1024
EXPECTED_FILES = ("local-model-e2e-attestation.json", "real-e2e-attestation.json")
COMMON_ATTESTATION_FIELDS = frozenset(
    {"schema_version", "git_head", "created_at", "expires_at", "matrix", "source"}
)
REAL_ATTESTATION_FIELDS = COMMON_ATTESTATION_FIELDS
LOCAL_ATTESTATION_FIELDS = COMMON_ATTESTATION_FIELDS | {
    "case_count",
    "ggml_case_count",
    "ct2_case_count",
    "models",
}
REAL_MATRIX = {"live-applet", "arecord", "pipewire", "openai-compatible", "flex-on", "flex-off"}
LOCAL_MATRIX = {
    "local-models",
    "generated-audio",
    "ggml",
    "ctranslate2",
    "explicit-backend",
    "auto-backend",
    "no-microphone",
    "no-clipboard",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
ATTESTATION_SCHEMA_VERSION = 1
ATTESTATION_TTL = timedelta(hours=24)


class AttestationError(RuntimeError):
    pass


def _reject_constant(value: str) -> None:
    raise AttestationError(f"non-finite JSON value is not allowed: {value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AttestationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, object]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow, bool) or not isinstance(nofollow, int) or nofollow <= 0:
        raise AttestationError("release attestation requires secure no-follow support")
    fd: int | None = None
    primary_error: BaseException | None = None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0))
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or before.st_mode & 0o022
            or before.st_size > MAX_ATTESTATION_BYTES
        ):
            raise AttestationError(f"unsafe release attestation file: {path.name}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(MAX_ATTESTATION_BYTES + 1)
        after = os.fstat(fd)
        if (
            len(raw) > MAX_ATTESTATION_BYTES
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        ):
            raise AttestationError(f"release attestation changed while reading: {path.name}")
    except OSError as exc:
        primary_error = AttestationError(f"cannot open release attestation: {path.name}")
        raise primary_error from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException as cleanup_error:
                if primary_error is not None:
                    primary_error.add_note("release attestation descriptor cleanup failed")
                else:
                    raise AttestationError("release attestation descriptor cleanup failed") from cleanup_error
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (MemoryError, UnicodeDecodeError, json.JSONDecodeError, RecursionError, TypeError, ValueError) as exc:
        raise AttestationError(f"invalid release attestation JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise AttestationError(f"release attestation must be an object: {path.name}")
    return value


def _require_string(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str) or not value:
        raise AttestationError(f"release attestation field is invalid: {field}")
    return value


def _reject_unexpected_fields(data: dict[str, object], *, name: str, allowed: frozenset[str]) -> None:
    if set(data) - allowed:
        raise AttestationError(f"{name} attestation contains unexpected fields")


def _validate_common(
    data: dict[str, object],
    *,
    name: str,
    expected_head: str,
    current_source: list[dict[str, str]],
) -> None:
    if _require_string(data.get("git_head"), f"{name}.git_head") != expected_head:
        raise AttestationError(f"{name} attestation is for the wrong tested commit")
    schema_version = data.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != ATTESTATION_SCHEMA_VERSION
    ):
        raise AttestationError(f"{name} attestation schema version is unsupported")
    created_text = _require_string(data.get("created_at"), f"{name}.created_at")
    expires_text = _require_string(data.get("expires_at"), f"{name}.expires_at")
    try:
        created = datetime.fromisoformat(created_text.replace("Z", "+00:00"))
        expires = datetime.fromisoformat(expires_text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AttestationError(f"{name} attestation timestamp or expiry is invalid") from exc
    if created.tzinfo is None or expires.tzinfo is None:
        raise AttestationError(f"{name} attestation timestamp or expiry has no timezone")
    if expires - created != ATTESTATION_TTL:
        raise AttestationError(f"{name} attestation expiry contract is invalid")
    now = datetime.now(UTC)
    age = now - created
    if age > ATTESTATION_TTL or now > expires or age < timedelta(minutes=-5):
        raise AttestationError(f"{name} attestation is outside the allowed age window")
    source = data.get("source")
    if not isinstance(source, list) or not source or source != current_source:
        raise AttestationError(f"{name} attestation source snapshot does not match release source")


def _validate_real(data: dict[str, object], *, current_source: list[dict[str, str]], expected_head: str) -> None:
    _reject_unexpected_fields(data, name="real-e2e", allowed=REAL_ATTESTATION_FIELDS)
    _validate_common(data, name="real-e2e", expected_head=expected_head, current_source=current_source)
    matrix = data.get("matrix")
    if (
        not isinstance(matrix, list)
        or not all(isinstance(item, str) for item in matrix)
        or len(matrix) != len(set(matrix))
        or not REAL_MATRIX.issubset(matrix)
    ):
        raise AttestationError("real-e2e attestation matrix is incomplete")


def _validate_local(data: dict[str, object], *, current_source: list[dict[str, str]], expected_head: str) -> None:
    _reject_unexpected_fields(data, name="local-model-e2e", allowed=LOCAL_ATTESTATION_FIELDS)
    _validate_common(data, name="local-model-e2e", expected_head=expected_head, current_source=current_source)
    matrix = data.get("matrix")
    if (
        not isinstance(matrix, list)
        or not all(isinstance(item, str) for item in matrix)
        or len(matrix) != len(set(matrix))
        or not LOCAL_MATRIX.issubset(matrix)
    ):
        raise AttestationError("local-model-e2e attestation matrix is incomplete")
    counts = [data.get(key) for key in ("case_count", "ggml_case_count", "ct2_case_count")]
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in counts):
        raise AttestationError("local-model-e2e attestation counts are invalid")
    total, ggml, ct2 = counts
    if total != ggml + ct2:
        raise AttestationError("local-model-e2e attestation counts are inconsistent")
    models = data.get("models")
    if not isinstance(models, list) or not models:
        raise AttestationError("local-model-e2e attestation model snapshot is missing")
    catalog = {model.name: model for model in CATALOG}
    seen_names: set[str] = set()
    formats: set[str] = set()
    for entry in models:
        if not isinstance(entry, dict):
            raise AttestationError("local-model-e2e attestation model snapshot is invalid")
        name = _require_string(entry.get("name"), "models[].name")
        if name in seen_names or name not in catalog:
            raise AttestationError(f"local-model-e2e attestation contains unknown model: {name}")
        seen_names.add(name)
        spec = catalog[name]
        if entry.get("backend") != spec.backend or entry.get("model_format") != spec.model_format:
            raise AttestationError(f"local-model-e2e attestation model metadata mismatch: {name}")
        languages = entry.get("languages")
        tested_languages = entry.get("tested_languages")
        if (
            not isinstance(languages, list)
            or languages != list(spec.languages)
            or not isinstance(tested_languages, list)
            or not tested_languages
            or not all(isinstance(language, str) for language in tested_languages)
            or len(tested_languages) != len(set(tested_languages))
        ):
            raise AttestationError(f"local-model-e2e attestation model languages are invalid: {name}")
        if spec.languages and not set(tested_languages).issubset(spec.languages):
            raise AttestationError(f"local-model-e2e attestation tested unsupported language: {name}")
        expected_files = set(spec.files or (spec.filename,))
        file_hashes = dict(spec.file_sha1s) if spec.file_sha1s else {spec.filename: spec.sha1}
        files = entry.get("files")
        if not isinstance(files, list):
            raise AttestationError(f"local-model-e2e attestation model files are invalid: {name}")
        file_names: list[str] = []
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise AttestationError(f"local-model-e2e attestation model files are invalid: {name}")
            file_names.append(item["name"])
        if len(file_names) != len(set(file_names)) or set(file_names) != expected_files:
            raise AttestationError(f"local-model-e2e attestation model files are invalid: {name}")
        for item in files:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not SHA1.fullmatch(str(item.get("sha1", ""))):
                raise AttestationError(f"local-model-e2e attestation model hashes are invalid: {name}")
            if file_hashes.get(item["name"]) != item["sha1"]:
                raise AttestationError(f"local-model-e2e attestation model hash mismatch: {name}")
        formats.add(spec.model_format)
    if formats != {"ggml", "ctranslate2"}:
        raise AttestationError("local-model-e2e attestation lacks a complete GGML/CT2 matrix")


def verify_bundle(bundle_dir: Path, repo_root: Path, expected_head: str) -> None:
    if not HEX40.fullmatch(expected_head):
        raise AttestationError("expected tested commit is invalid")
    try:
        repo = repo_root.resolve(strict=True)
        release_root_path = repo / "release-attestations"
        release_root_stat = os.lstat(release_root_path)
        if stat.S_ISLNK(release_root_stat.st_mode) or not stat.S_ISDIR(release_root_stat.st_mode):
            raise AttestationError("release attestation root directory is unsafe")
        bundle_path_stat = os.lstat(bundle_dir)
        if stat.S_ISLNK(bundle_path_stat.st_mode) or not stat.S_ISDIR(bundle_path_stat.st_mode):
            raise AttestationError("release attestation bundle directory is unsafe")
        bundle = bundle_dir.resolve(strict=True)
        release_root = release_root_path.resolve(strict=True)
    except OSError as exc:
        raise AttestationError("release attestation bundle or repository path is missing") from exc
    if not repo.is_dir() or not bundle.is_dir() or bundle.parent != release_root.resolve(strict=True):
        raise AttestationError("release attestation bundle is outside the allowed directory")
    bundle_stat = os.lstat(bundle)
    if stat.S_ISLNK(bundle_stat.st_mode) or not stat.S_ISDIR(bundle_stat.st_mode) or bundle_stat.st_mode & 0o022:
        raise AttestationError("release attestation bundle directory is unsafe")
    entries = sorted(path.name for path in bundle.iterdir())
    if entries != sorted(EXPECTED_FILES):
        raise AttestationError("release attestation bundle contains unexpected files")
    try:
        current_source = source_attestation_snapshot(repo)
    except (ModelError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise AttestationError("current release source is not attestable") from exc
    _validate_real(_read_json(bundle / "real-e2e-attestation.json"), current_source=current_source, expected_head=expected_head)
    _validate_local(_read_json(bundle / "local-model-e2e-attestation.json"), current_source=current_source, expected_head=expected_head)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(f"usage: {argv[0]} BUNDLE_DIR REPO_ROOT TESTED_COMMIT", file=sys.stderr)
        return 2
    try:
        verify_bundle(Path(argv[1]), Path(argv[2]), argv[3])
    except AttestationError as exc:
        print(f"release attestation rejected: {exc}", file=sys.stderr)
        return 1
    print(f"Verified release attestation bundle: {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
