"""Bounded runtime observation for future CTranslate2 benchmarks."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from . import ct2_benchmark_worker as worker_protocol
from .command_chain import run_process_bounded_output
from .process_priority import local_model_command


RUNTIME_OBSERVATION_TIMEOUT_SECONDS = 15
ARTIFACT_ATTESTATION_TIMEOUT_SECONDS = 60
MAX_RESULT_BYTES = worker_protocol.MAX_RESULT_BYTES
MAX_PATH_CHARS = worker_protocol.MAX_PATH_CHARS
MAX_EXPERIMENT_MANIFEST_BYTES = MAX_RESULT_BYTES
MAX_RUN_PLAN_BYTES = MAX_RESULT_BYTES
EXPERIMENT_MANIFEST_SCHEMA_VERSION = 1
RUN_PLAN_SCHEMA_VERSION = 1
FULL_PAIR_COUNT = 5
QUICK_PAIR_COUNT = 1
MAX_MODEL_BLOCKS = 8
ARTIFACT_MANIFEST_SCHEMA_VERSION = 1
MAX_ARTIFACT_MANIFEST_BYTES = 1024 * 1024
MAX_CORPUS_BYTES = 1 << 20
MAX_ARTIFACT_FILES = 4096
MAX_ARTIFACT_PATH_CHARS = 512
MAX_ARTIFACT_PATH_BYTES = 2048
MAX_ARTIFACT_PATH_DEPTH = 32
MAX_DECLARED_ARTIFACT_BYTES = 1 << 30
MAX_EXPERIMENT_ARTIFACT_BYTES = 4 << 30
MAX_ARM_REQUEST_BYTES = 3_403_776
_MAX_UINT64 = (1 << 64) - 1
_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "LANG": "C",
    "LC_ALL": "C",
    "TRANSFORMERS_OFFLINE": "1",
}
_ERROR_CODES = frozenset(
    {
        "auxiliary-output",
        "artifact-attestation-failed",
        "artifact-manifest-invalid",
        "artifact-reference-invalid",
        "artifact-reference-mismatch",
        "artifact-root-invalid",
        "artifact-set-invalid",
        "artifact-tree-changed",
        "artifact-tree-mismatch",
        "benchmark-request-invalid",
        "internal",
        "invalid-spec",
        "low-qos-failed",
        "manifest-invalid",
        "plan-invalid",
        "result-invalid",
        "result-mismatch",
        "result-missing",
        "result-oversized",
        "runner-failed",
        "seed-invalid",
        "version-mismatch",
        "worker-error",
        "worker-exit",
    }
)
_ARTIFACT_ATTESTATION_ERROR_CODES = frozenset(
    {
        "artifact-attestation-failed",
        "artifact-root-invalid",
        "artifact-tree-changed",
        "artifact-tree-mismatch",
    }
)
_BENCHMARK_BINDING_BUILDER_ERROR_CODES = frozenset(
    {
        "artifact-manifest-invalid",
        "artifact-reference-mismatch",
        "artifact-set-invalid",
        "benchmark-request-invalid",
        "plan-invalid",
    }
)
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}", re.ASCII)
_IMPLEMENTATION_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}", re.ASCII)
_ABI_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
_COMPUTE_TYPE_RE = re.compile(r"[a-z0-9_]{1,32}", re.ASCII)
_NONCE_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_ARTIFACT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}", re.ASCII)
_SHA256_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]", re.ASCII)


class RuntimeProbeError(RuntimeError):
    """Protocol failure containing no runtime-controlled detail."""

    def __init__(self, code: str) -> None:
        self.code = code if code in _ERROR_CODES else "internal"
        super().__init__(self.code)


@dataclass(frozen=True)
class _RuntimeSpec:
    interpreter: str
    site_packages: str
    expected_ctranslate2_version: str
    expected_faster_whisper_version: str


@dataclass(frozen=True)
class RuntimeObservation:
    python_version: str
    implementation: str
    abi: str
    ctranslate2_version: str
    faster_whisper_version: str
    supported_cpu_compute_types: tuple[str, ...]


@dataclass(frozen=True)
class ArtifactManifestReference:
    artifact_id: str
    manifest_sha256: str


@dataclass(frozen=True)
class ArtifactFileDeclaration:
    path: str
    sha256: str
    size: int
    executable: bool


@dataclass(frozen=True)
class ArtifactManifest:
    """Validated declaration; does not attest any filesystem object."""

    reference: ArtifactManifestReference
    kind: str
    files: tuple[ArtifactFileDeclaration, ...]
    schema_version: int


@dataclass(frozen=True, slots=True)
class ArtifactAttestation:
    """Bounded tree observation, not a future child-execution binding."""

    artifact_id: str
    manifest_sha256: str
    file_count: int
    total_bytes: int


@dataclass(frozen=True)
class ExperimentManifest:
    mode: str
    pair_count: int
    runtimes: tuple[ArtifactManifestReference, ArtifactManifestReference]
    clips: ArtifactManifestReference
    models: tuple[ArtifactManifestReference, ...]


@dataclass(frozen=True)
class RunPair:
    runtime_order: tuple[str, str]
    clip_order_seed: int


@dataclass(frozen=True)
class RunPhase:
    kind: str
    fresh_process_per_arm: bool
    warmup_before_measurement: bool
    warmup_discarded: bool
    pair: RunPair


@dataclass(frozen=True)
class ModelRunBlock:
    model: ArtifactManifestReference
    phases: tuple[RunPhase, ...]


@dataclass(frozen=True)
class RunPlan:
    schema_version: int
    mode: str
    seed: int
    pair_count: int
    decision_eligibility_capable: bool
    runtimes: tuple[ArtifactManifestReference, ArtifactManifestReference]
    clips: ArtifactManifestReference
    model_blocks: tuple[ModelRunBlock, ...]


@dataclass(frozen=True)
class DecodeProfile:
    profile_schema_version: int
    device: str
    requested_compute_type: str
    cpu_threads: int
    num_workers: int
    language: str
    task: str
    beam_size: int
    temperature_milli: int
    vad_filter: bool
    condition_on_previous_text: bool
    word_timestamps: bool
    without_timestamps: bool


def _validated_path(value: object) -> str | None:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or len(value) > MAX_PATH_CHARS
        or _CONTROL_RE.search(value) is not None
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        return None
    return value


def _validated_version(value: object) -> str | None:
    if type(value) is not str or _VERSION_RE.fullmatch(value) is None:
        return None
    return value


def _validated_spec(spec: object) -> _RuntimeSpec:
    if (
        type(spec) is not _RuntimeSpec
        or _validated_path(spec.interpreter) is None
        or _validated_path(spec.site_packages) is None
        or _validated_version(spec.expected_ctranslate2_version) is None
        or _validated_version(spec.expected_faster_whisper_version) is None
    ):
        raise RuntimeProbeError("invalid-spec")
    return spec


def _worker_path() -> str:
    package_file = worker_protocol.__file__
    if type(package_file) is not str:
        raise RuntimeProbeError("invalid-spec")
    path = os.path.join(os.path.dirname(package_file), "ct2_benchmark_worker.py")
    validated = _validated_path(path)
    if validated is None:
        raise RuntimeProbeError("invalid-spec")
    return validated


def _new_nonce() -> str:
    try:
        value = secrets.token_hex(32)
    except Exception:
        raise RuntimeProbeError("internal") from None
    if type(value) is not str or _NONCE_RE.fullmatch(value) is None:
        raise RuntimeProbeError("internal")
    return value


def _canonical_json(
    payload: object,
    *,
    max_bytes: int = MAX_RESULT_BYTES,
    error_code: str = "result-invalid",
) -> bytes:
    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise RuntimeProbeError(error_code) from None
    if not encoded or len(encoded) > max_bytes:
        raise RuntimeProbeError(error_code)
    return encoded


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result


def _reject_constant(_value: str) -> object:
    raise ValueError("nonfinite")


def _decode_canonical_object(
    data: bytes,
    *,
    max_bytes: int,
    error_code: str,
) -> dict[str, object]:
    if type(data) is not bytes or not data or len(data) > max_bytes:
        raise RuntimeProbeError(error_code)
    try:
        payload = json.loads(
            data.decode("ascii"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise RuntimeProbeError(error_code) from None
    if (
        type(payload) is not dict
        or _canonical_json(
            payload,
            max_bytes=max_bytes,
            error_code=error_code,
        )
        != data
    ):
        raise RuntimeProbeError(error_code)
    return payload


def _decode_result(data: bytes) -> dict[str, object]:
    return _decode_canonical_object(
        data,
        max_bytes=MAX_RESULT_BYTES,
        error_code="result-invalid",
    )


def _validated_artifact_path(value: object) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_ARTIFACT_PATH_CHARS
        or value.startswith("/")
        or "\\" in value
        or unicodedata.normalize("NFC", value) != value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value
        )
    ):
        return None
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        return None
    components = value.split("/")
    if (
        len(encoded) > MAX_ARTIFACT_PATH_BYTES
        or len(components) > MAX_ARTIFACT_PATH_DEPTH
        or any(component in {"", ".", ".."} for component in components)
    ):
        return None
    return value


def _artifact_file_declaration(value: object) -> ArtifactFileDeclaration:
    if type(value) is not dict or set(value) != {
        "executable",
        "path",
        "sha256",
        "size",
    }:
        raise RuntimeProbeError("artifact-manifest-invalid")
    path = _validated_artifact_path(value.get("path"))
    sha256 = value.get("sha256")
    size = value.get("size")
    executable = value.get("executable")
    if (
        path is None
        or type(sha256) is not str
        or _SHA256_RE.fullmatch(sha256) is None
        or type(size) is not int
        or not 0 <= size <= _MAX_UINT64
        or type(executable) is not bool
    ):
        raise RuntimeProbeError("artifact-manifest-invalid")
    return ArtifactFileDeclaration(
        path=path,
        sha256=sha256,
        size=size,
        executable=executable,
    )


def _artifact_files_are_valid(files: object) -> bool:
    if (
        type(files) is not tuple
        or not 1 <= len(files) <= MAX_ARTIFACT_FILES
        or not all(type(value) is ArtifactFileDeclaration for value in files)
    ):
        return False
    total_size = 0
    paths: list[str] = []
    for declaration in files:
        if (
            _validated_artifact_path(declaration.path) is None
            or type(declaration.sha256) is not str
            or _SHA256_RE.fullmatch(declaration.sha256) is None
            or type(declaration.size) is not int
            or not 0 <= declaration.size <= _MAX_UINT64
            or type(declaration.executable) is not bool
            or declaration.size > MAX_DECLARED_ARTIFACT_BYTES - total_size
        ):
            return False
        total_size += declaration.size
        paths.append(declaration.path)
    path_set = set(paths)
    if paths != sorted(paths) or len(paths) != len(path_set):
        return False
    for path in paths:
        components = path.split("/")
        for depth in range(1, len(components)):
            if "/".join(components[:depth]) in path_set:
                return False
    return True


def _artifact_manifest_is_valid(manifest: object) -> bool:
    return (
        type(manifest) is ArtifactManifest
        and _reference_is_valid(manifest.reference)
        and type(manifest.kind) is str
        and manifest.kind in {"runtime", "model", "clips"}
        and type(manifest.schema_version) is int
        and manifest.schema_version == ARTIFACT_MANIFEST_SCHEMA_VERSION
        and _artifact_files_are_valid(manifest.files)
    )


def _artifact_file_payload(
    declaration: ArtifactFileDeclaration,
) -> dict[str, object]:
    return {
        "executable": declaration.executable,
        "path": declaration.path,
        "sha256": declaration.sha256,
        "size": declaration.size,
    }


def _artifact_manifest_payload(manifest: ArtifactManifest) -> dict[str, object]:
    return {
        "artifact_id": manifest.reference.artifact_id,
        "files": [_artifact_file_payload(value) for value in manifest.files],
        "kind": manifest.kind,
        "schema_version": manifest.schema_version,
    }


def _artifact_manifest_size_is_bounded(manifest: ArtifactManifest) -> bool:
    empty_manifest_size = len(
        _canonical_json(
            {
                "artifact_id": manifest.reference.artifact_id,
                "files": [],
                "kind": manifest.kind,
                "schema_version": manifest.schema_version,
            },
            max_bytes=MAX_ARTIFACT_MANIFEST_BYTES,
            error_code="artifact-manifest-invalid",
        )
    )
    encoded_size = empty_manifest_size
    for index, declaration in enumerate(manifest.files):
        entry_size = len(
            _canonical_json(
                _artifact_file_payload(declaration),
                max_bytes=MAX_ARTIFACT_MANIFEST_BYTES,
                error_code="artifact-manifest-invalid",
            )
        )
        increment = entry_size + (1 if index else 0)
        if encoded_size > MAX_ARTIFACT_MANIFEST_BYTES - increment:
            return False
        encoded_size += increment
    return True


def canonical_artifact_manifest_bytes(manifest: ArtifactManifest) -> bytes:
    try:
        if not _artifact_manifest_is_valid(
            manifest
        ) or not _artifact_manifest_size_is_bounded(manifest):
            raise RuntimeProbeError("artifact-manifest-invalid")
        encoded = _canonical_json(
            _artifact_manifest_payload(manifest),
            max_bytes=MAX_ARTIFACT_MANIFEST_BYTES,
            error_code="artifact-manifest-invalid",
        )
        digest = hashlib.sha256(encoded).hexdigest()
        if not secrets.compare_digest(digest, manifest.reference.manifest_sha256):
            raise RuntimeProbeError("artifact-reference-mismatch")
        return encoded
    except KeyboardInterrupt:
        raise
    except RuntimeProbeError:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("artifact-manifest-invalid") from None


def _parse_artifact_manifest_checked(
    data: bytes,
    *,
    reference: ArtifactManifestReference,
) -> ArtifactManifest:
    if not _reference_is_valid(reference):
        raise RuntimeProbeError("artifact-reference-invalid")
    if (
        type(data) is not bytes
        or not data
        or len(data) > MAX_ARTIFACT_MANIFEST_BYTES
    ):
        raise RuntimeProbeError("artifact-manifest-invalid")
    digest = hashlib.sha256(data).hexdigest()
    if not secrets.compare_digest(digest, reference.manifest_sha256):
        raise RuntimeProbeError("artifact-reference-mismatch")
    payload = _decode_canonical_object(
        data,
        max_bytes=MAX_ARTIFACT_MANIFEST_BYTES,
        error_code="artifact-manifest-invalid",
    )
    if set(payload) != {"artifact_id", "files", "kind", "schema_version"}:
        raise RuntimeProbeError("artifact-manifest-invalid")
    artifact_id = payload.get("artifact_id")
    kind = payload.get("kind")
    schema_version = payload.get("schema_version")
    if (
        type(artifact_id) is not str
        or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
    ):
        raise RuntimeProbeError("artifact-manifest-invalid")
    if not secrets.compare_digest(artifact_id, reference.artifact_id):
        raise RuntimeProbeError("artifact-reference-mismatch")
    if (
        type(kind) is not str
        or kind not in {"runtime", "model", "clips"}
        or type(schema_version) is not int
        or schema_version != ARTIFACT_MANIFEST_SCHEMA_VERSION
    ):
        raise RuntimeProbeError("artifact-manifest-invalid")
    files_value = payload.get("files")
    if (
        type(files_value) is not list
        or not 1 <= len(files_value) <= MAX_ARTIFACT_FILES
    ):
        raise RuntimeProbeError("artifact-manifest-invalid")
    files = tuple(_artifact_file_declaration(value) for value in files_value)
    if not _artifact_files_are_valid(files):
        raise RuntimeProbeError("artifact-manifest-invalid")
    manifest = ArtifactManifest(
        reference=reference,
        kind=kind,
        files=files,
        schema_version=schema_version,
    )
    if canonical_artifact_manifest_bytes(manifest) != data:
        raise RuntimeProbeError("artifact-manifest-invalid")
    return manifest


def parse_artifact_manifest(
    data: bytes,
    *,
    reference: ArtifactManifestReference,
) -> ArtifactManifest:
    try:
        return _parse_artifact_manifest_checked(data, reference=reference)
    except KeyboardInterrupt:
        raise
    except RuntimeProbeError:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("artifact-manifest-invalid") from None


def _artifact_root_text_is_valid(raw: object) -> bool:
    try:
        return (
            type(raw) is str
            and bool(raw)
            and len(raw) <= MAX_PATH_CHARS
            and not raw.startswith("//")
            and os.path.isabs(raw)
            and os.path.normpath(raw) == raw
            and not any(
                unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
                for character in raw
            )
        )
    except (Exception, SystemExit):
        return False


def _artifact_root_path_is_valid(root: object) -> bool:
    if not isinstance(root, Path):
        return False
    try:
        return _artifact_root_text_is_valid(os.fspath(root))
    except (Exception, SystemExit):
        return False


def attest_artifact_root(
    root: Path,
    manifest: ArtifactManifest,
) -> ArtifactAttestation:
    try:
        manifest_bytes = canonical_artifact_manifest_bytes(manifest)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("artifact-attestation-failed") from None
    if not _artifact_root_path_is_valid(root):
        raise RuntimeProbeError("artifact-root-invalid")
    try:
        interpreter = _validated_path(sys.executable)
        if interpreter is None:
            raise RuntimeProbeError("artifact-attestation-failed")
        nonce = _new_nonce()
        request = _canonical_json(
            {
                "manifest": _artifact_manifest_payload(manifest),
                "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                "mode": "attest-artifact",
                "nonce": nonce,
                "root": os.fspath(root),
                "schema_version": worker_protocol.SCHEMA_VERSION,
            },
            max_bytes=worker_protocol.MAX_ATTESTATION_REQUEST_BYTES,
            error_code="artifact-attestation-failed",
        )
        runtime_argv = [
            interpreter,
            "-I",
            "-S",
            "-B",
            _worker_path(),
            "--attest-artifact",
            "--nonce",
            nonce,
        ]
        try:
            command = local_model_command(runtime_argv)
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit):
            raise RuntimeProbeError("artifact-attestation-failed") from None
        try:
            response = run_process_bounded_output(
                command,
                input_bytes=request,
                timeout_seconds=ARTIFACT_ATTESTATION_TIMEOUT_SECONDS,
                max_output_bytes=MAX_RESULT_BYTES,
                env=dict(_ENVIRONMENT),
                label="CTranslate2 benchmark artifact attestation",
                deadline=time.monotonic() + ARTIFACT_ATTESTATION_TIMEOUT_SECONDS,
                preserve_user_systemd_environment=True,
            )
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit):
            raise RuntimeProbeError("artifact-attestation-failed") from None
        if type(response) is not tuple or len(response) != 3:
            raise RuntimeProbeError("artifact-attestation-failed")
        returncode, stdout, stderr = response
        if (
            type(returncode) is not int
            or type(stdout) is not bytes
            or type(stderr) is not bytes
            or stderr
            or not stdout
            or len(stdout) > MAX_RESULT_BYTES
        ):
            raise RuntimeProbeError("artifact-attestation-failed")
        return _parse_artifact_attestation_response(
            stdout,
            nonce=nonce,
            returncode=returncode,
            manifest=manifest,
        )
    except KeyboardInterrupt:
        raise
    except RuntimeProbeError as exc:
        if exc.code in _ARTIFACT_ATTESTATION_ERROR_CODES:
            raise
        raise RuntimeProbeError("artifact-attestation-failed") from None
    except (Exception, SystemExit):
        raise RuntimeProbeError("artifact-attestation-failed") from None


def _parse_artifact_attestation_response(
    data: bytes,
    *,
    nonce: str,
    returncode: int,
    manifest: ArtifactManifest,
) -> ArtifactAttestation:
    try:
        payload = _decode_canonical_object(
            data,
            max_bytes=MAX_RESULT_BYTES,
            error_code="artifact-attestation-failed",
        )
        if (
            type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != worker_protocol.SCHEMA_VERSION
            or type(payload.get("nonce")) is not str
            or not secrets.compare_digest(payload["nonce"], nonce)
            or type(returncode) is not int
        ):
            raise RuntimeProbeError("artifact-attestation-failed")
        status = payload.get("status")
        if status == "error":
            if set(payload) != {"error_code", "nonce", "schema_version", "status"}:
                raise RuntimeProbeError("artifact-attestation-failed")
            code = payload.get("error_code")
            if (
                type(code) is not str
                or code not in _ARTIFACT_ATTESTATION_ERROR_CODES
                or returncode != 65
            ):
                raise RuntimeProbeError("artifact-attestation-failed")
            raise RuntimeProbeError(code)
        if (
            status != "ok"
            or returncode != 0
            or set(payload)
            != {"attestation", "mode", "nonce", "schema_version", "status"}
            or payload.get("mode") != "attest-artifact"
        ):
            raise RuntimeProbeError("artifact-attestation-failed")
        value = payload.get("attestation")
        if type(value) is not dict or set(value) != {
            "artifact_id",
            "file_count",
            "manifest_sha256",
            "total_bytes",
        }:
            raise RuntimeProbeError("artifact-attestation-failed")
        artifact_id = value.get("artifact_id")
        manifest_sha256 = value.get("manifest_sha256")
        file_count = value.get("file_count")
        total_bytes = value.get("total_bytes")
        expected_total = sum(item.size for item in manifest.files)
        if (
            type(artifact_id) is not str
            or not secrets.compare_digest(
                artifact_id,
                manifest.reference.artifact_id,
            )
            or type(manifest_sha256) is not str
            or not secrets.compare_digest(
                manifest_sha256,
                manifest.reference.manifest_sha256,
            )
            or type(file_count) is not int
            or file_count != len(manifest.files)
            or type(total_bytes) is not int
            or total_bytes != expected_total
        ):
            raise RuntimeProbeError("artifact-attestation-failed")
        return ArtifactAttestation(
            artifact_id=artifact_id,
            manifest_sha256=manifest_sha256,
            file_count=file_count,
            total_bytes=total_bytes,
        )
    except KeyboardInterrupt:
        raise
    except RuntimeProbeError:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("artifact-attestation-failed") from None


def _manifest_reference(value: object) -> ArtifactManifestReference:
    if type(value) is not dict or set(value) != {"id", "manifest_sha256"}:
        raise RuntimeProbeError("manifest-invalid")
    artifact_id = value.get("id")
    manifest_sha256 = value.get("manifest_sha256")
    if (
        type(artifact_id) is not str
        or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
        or type(manifest_sha256) is not str
        or _SHA256_RE.fullmatch(manifest_sha256) is None
    ):
        raise RuntimeProbeError("manifest-invalid")
    return ArtifactManifestReference(
        artifact_id=artifact_id,
        manifest_sha256=manifest_sha256,
    )


def _parse_experiment_manifest_checked(data: bytes) -> ExperimentManifest:
    if (
        type(data) is not bytes
        or not data
        or len(data) > MAX_EXPERIMENT_MANIFEST_BYTES
    ):
        raise RuntimeProbeError("manifest-invalid")
    payload = _decode_result(data)
    if set(payload) != {
        "clips",
        "mode",
        "models",
        "pair_count",
        "runtimes",
        "schema_version",
    }:
        raise RuntimeProbeError("manifest-invalid")
    schema_version = payload.get("schema_version")
    mode = payload.get("mode")
    pair_count = payload.get("pair_count")
    if (
        type(schema_version) is not int
        or schema_version != EXPERIMENT_MANIFEST_SCHEMA_VERSION
        or type(mode) is not str
        or mode not in {"full", "quick"}
        or type(pair_count) is not int
        or pair_count
        != (FULL_PAIR_COUNT if mode == "full" else QUICK_PAIR_COUNT)
    ):
        raise RuntimeProbeError("manifest-invalid")

    runtimes_value = payload.get("runtimes")
    if type(runtimes_value) is not dict or set(runtimes_value) != {"a", "b"}:
        raise RuntimeProbeError("manifest-invalid")
    runtimes = (
        _manifest_reference(runtimes_value.get("a")),
        _manifest_reference(runtimes_value.get("b")),
    )
    if (
        runtimes[0].artifact_id == runtimes[1].artifact_id
        or runtimes[0].manifest_sha256 == runtimes[1].manifest_sha256
    ):
        raise RuntimeProbeError("manifest-invalid")

    models_value = payload.get("models")
    if (
        type(models_value) is not list
        or not models_value
        or len(models_value) > MAX_MODEL_BLOCKS
    ):
        raise RuntimeProbeError("manifest-invalid")
    models = tuple(_manifest_reference(value) for value in models_value)
    model_ids = tuple(model.artifact_id for model in models)
    model_hashes = tuple(model.manifest_sha256 for model in models)
    if (
        len(set(model_ids)) != len(model_ids)
        or len(set(model_hashes)) != len(model_hashes)
    ):
        raise RuntimeProbeError("manifest-invalid")
    return ExperimentManifest(
        mode=mode,
        pair_count=pair_count,
        runtimes=runtimes,
        clips=_manifest_reference(payload.get("clips")),
        models=models,
    )


def _parse_experiment_manifest(data: bytes) -> ExperimentManifest:
    try:
        return _parse_experiment_manifest_checked(data)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("manifest-invalid") from None


def _pair_seed(seed: int, model_id: str, phase: str, index: int) -> int:
    material = (
        b"SOC-CT2-RUN-PLAN\x00"
        + seed.to_bytes(8, "big")
        + b"\x00"
        + model_id.encode("ascii")
        + b"\x00"
        + phase.encode("ascii")
        + b"\x00"
        + index.to_bytes(1, "big")
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _run_pair(
    runtime_ids: tuple[str, str],
    *,
    first_runtime: str,
    clip_order_seed: int,
) -> RunPair:
    other_runtime = runtime_ids[1] if first_runtime == runtime_ids[0] else runtime_ids[0]
    return RunPair(
        runtime_order=(first_runtime, other_runtime),
        clip_order_seed=clip_order_seed,
    )


def _measurement_first_runtimes(
    runtime_ids: tuple[str, str],
    *,
    mode: str,
    seed: int,
) -> tuple[str, ...]:
    majority = runtime_ids[seed & 1]
    if mode == "quick":
        return (majority,)
    minority = runtime_ids[1 - (seed & 1)]
    order = (majority, minority, majority, minority, majority)
    offset = (seed >> 1) % FULL_PAIR_COUNT
    return order[offset:] + order[:offset]


def _model_phases(
    runtime_ids: tuple[str, str],
    *,
    mode: str,
    model_id: str,
    seed: int,
) -> tuple[RunPhase, ...]:
    cold_seed = _pair_seed(seed, model_id, "cold", 0)
    cold = RunPhase(
        kind="cold",
        fresh_process_per_arm=True,
        warmup_before_measurement=False,
        warmup_discarded=False,
        pair=_run_pair(
            runtime_ids,
            first_runtime=runtime_ids[cold_seed & 1],
            clip_order_seed=cold_seed,
        ),
    )
    measurements = tuple(
        RunPhase(
            kind="measurement",
            fresh_process_per_arm=True,
            warmup_before_measurement=True,
            warmup_discarded=True,
            pair=_run_pair(
                runtime_ids,
                first_runtime=first_runtime,
                clip_order_seed=_pair_seed(seed, model_id, "measurement", index),
            ),
        )
        for index, first_runtime in enumerate(
            _measurement_first_runtimes(runtime_ids, mode=mode, seed=seed)
        )
    )
    return (cold, *measurements)


def build_run_plan(manifest_data: bytes, *, seed: int) -> RunPlan:
    if type(seed) is not int or not 0 <= seed <= _MAX_UINT64:
        raise RuntimeProbeError("seed-invalid")
    manifest = _parse_experiment_manifest(manifest_data)
    runtime_ids = tuple(reference.artifact_id for reference in manifest.runtimes)
    blocks = tuple(
        ModelRunBlock(
            model=model,
            phases=_model_phases(
                runtime_ids,
                mode=manifest.mode,
                model_id=model.artifact_id,
                seed=seed,
            ),
        )
        for model in manifest.models
    )
    plan = RunPlan(
        schema_version=RUN_PLAN_SCHEMA_VERSION,
        mode=manifest.mode,
        seed=seed,
        pair_count=manifest.pair_count,
        decision_eligibility_capable=(
            manifest.mode == "full" and manifest.pair_count == FULL_PAIR_COUNT
        ),
        runtimes=manifest.runtimes,
        clips=manifest.clips,
        model_blocks=blocks,
    )
    canonical_run_plan_bytes(plan)
    return plan


def _reference_is_valid(value: object) -> bool:
    return (
        type(value) is ArtifactManifestReference
        and type(value.artifact_id) is str
        and _ARTIFACT_ID_RE.fullmatch(value.artifact_id) is not None
        and type(value.manifest_sha256) is str
        and _SHA256_RE.fullmatch(value.manifest_sha256) is not None
    )


def _phase_values_are_typed(phase: object) -> bool:
    return (
        type(phase) is RunPhase
        and type(phase.kind) is str
        and type(phase.fresh_process_per_arm) is bool
        and type(phase.warmup_before_measurement) is bool
        and type(phase.warmup_discarded) is bool
        and type(phase.pair) is RunPair
        and type(phase.pair.runtime_order) is tuple
        and len(phase.pair.runtime_order) == 2
        and all(type(value) is str for value in phase.pair.runtime_order)
        and type(phase.pair.clip_order_seed) is int
        and 0 <= phase.pair.clip_order_seed <= _MAX_UINT64
    )


def _run_plan_is_valid(plan: object) -> bool:
    if (
        type(plan) is not RunPlan
        or type(plan.schema_version) is not int
        or plan.schema_version != RUN_PLAN_SCHEMA_VERSION
        or type(plan.mode) is not str
        or plan.mode not in {"full", "quick"}
        or type(plan.seed) is not int
        or not 0 <= plan.seed <= _MAX_UINT64
        or type(plan.pair_count) is not int
        or plan.pair_count
        != (FULL_PAIR_COUNT if plan.mode == "full" else QUICK_PAIR_COUNT)
        or type(plan.decision_eligibility_capable) is not bool
        or plan.decision_eligibility_capable != (plan.mode == "full")
        or type(plan.runtimes) is not tuple
        or len(plan.runtimes) != 2
        or not all(_reference_is_valid(value) for value in plan.runtimes)
        or plan.runtimes[0].artifact_id == plan.runtimes[1].artifact_id
        or plan.runtimes[0].manifest_sha256 == plan.runtimes[1].manifest_sha256
        or not _reference_is_valid(plan.clips)
        or type(plan.model_blocks) is not tuple
        or not 1 <= len(plan.model_blocks) <= MAX_MODEL_BLOCKS
        or not all(type(block) is ModelRunBlock for block in plan.model_blocks)
        or not all(_reference_is_valid(block.model) for block in plan.model_blocks)
    ):
        return False
    model_ids = tuple(block.model.artifact_id for block in plan.model_blocks)
    model_hashes = tuple(block.model.manifest_sha256 for block in plan.model_blocks)
    if (
        len(set(model_ids)) != len(model_ids)
        or len(set(model_hashes)) != len(model_hashes)
    ):
        return False
    runtime_ids = tuple(reference.artifact_id for reference in plan.runtimes)
    return all(
        type(block.phases) is tuple
        and all(_phase_values_are_typed(phase) for phase in block.phases)
        and block.phases
        == _model_phases(
            runtime_ids,
            mode=plan.mode,
            model_id=block.model.artifact_id,
            seed=plan.seed,
        )
        for block in plan.model_blocks
    )


def _reference_payload(reference: ArtifactManifestReference) -> dict[str, object]:
    return {
        "id": reference.artifact_id,
        "manifest_sha256": reference.manifest_sha256,
    }


def _pair_payload(pair: RunPair) -> dict[str, object]:
    return {
        "clip_order_seed": pair.clip_order_seed,
        "runtime_order": list(pair.runtime_order),
    }


def _phase_payload(phase: RunPhase) -> dict[str, object]:
    return {
        "fresh_process_per_arm": phase.fresh_process_per_arm,
        "kind": phase.kind,
        "pair": _pair_payload(phase.pair),
        "warmup_before_measurement": phase.warmup_before_measurement,
        "warmup_discarded": phase.warmup_discarded,
    }


def canonical_run_plan_bytes(plan: RunPlan) -> bytes:
    try:
        if not _run_plan_is_valid(plan):
            raise ValueError("plan")
        payload = {
            "clips": _reference_payload(plan.clips),
            "decision_eligibility_capable": plan.decision_eligibility_capable,
            "mode": plan.mode,
            "model_blocks": [
                {
                    "model": _reference_payload(block.model),
                    "phases": [_phase_payload(phase) for phase in block.phases],
                }
                for block in plan.model_blocks
            ],
            "pair_count": plan.pair_count,
            "runtimes": {
                "a": _reference_payload(plan.runtimes[0]),
                "b": _reference_payload(plan.runtimes[1]),
            },
            "schema_version": plan.schema_version,
            "seed": plan.seed,
        }
        encoded = _canonical_json(payload)
        if len(encoded) > MAX_RUN_PLAN_BYTES:
            raise ValueError("plan")
        return encoded
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("plan-invalid") from None


def _validated_run_plan_artifacts(
    plan: RunPlan,
    artifacts: tuple[tuple[object, ArtifactManifest], ...],
) -> tuple[bytes, tuple[tuple[Path, ArtifactManifest], ...]]:
    try:
        plan_bytes = canonical_run_plan_bytes(plan)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("plan-invalid") from None

    requirements = (
        (plan.runtimes[0], "runtime"),
        (plan.runtimes[1], "runtime"),
        (plan.clips, "clips"),
        *((block.model, "model") for block in plan.model_blocks),
    )
    reference_ids = tuple(reference.artifact_id for reference, _kind in requirements)
    reference_hashes = tuple(
        reference.manifest_sha256 for reference, _kind in requirements
    )
    if (
        len(set(reference_ids)) != len(reference_ids)
        or len(set(reference_hashes)) != len(reference_hashes)
        or type(artifacts) is not tuple
        or len(artifacts) != len(requirements)
        or any(type(item) is not tuple or len(item) != 2 for item in artifacts)
    ):
        raise RuntimeProbeError("artifact-set-invalid")

    frozen: list[tuple[Path, ArtifactManifest]] = []
    root_names: set[str] = set()
    total_bytes = 0
    for item, (expected_reference, expected_kind) in zip(
        artifacts,
        requirements,
        strict=True,
    ):
        root_value, manifest = item
        try:
            canonical_artifact_manifest_bytes(manifest)
        except KeyboardInterrupt:
            raise
        except RuntimeProbeError:
            raise
        except (Exception, SystemExit):
            raise RuntimeProbeError("artifact-manifest-invalid") from None
        if (
            manifest.kind != expected_kind
            or manifest.reference != expected_reference
        ):
            raise RuntimeProbeError("artifact-set-invalid")
        try:
            root_name = os.fspath(root_value)
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit):
            raise RuntimeProbeError("artifact-set-invalid") from None
        if (
            type(root_name) is not str
            or not _artifact_root_text_is_valid(root_name)
            or root_name in root_names
        ):
            raise RuntimeProbeError("artifact-set-invalid")
        manifest_bytes = sum(declaration.size for declaration in manifest.files)
        if manifest_bytes > MAX_EXPERIMENT_ARTIFACT_BYTES - total_bytes:
            raise RuntimeProbeError("artifact-set-invalid")
        total_bytes += manifest_bytes
        root_names.add(root_name)
        frozen.append((Path(root_name), manifest))
    return plan_bytes, tuple(frozen)


def attest_run_plan_artifacts(
    plan: RunPlan,
    artifacts: tuple[tuple[object, ArtifactManifest], ...],
) -> tuple[ArtifactAttestation, ...]:
    """Validate and attest the complete ordered artifact set for a run plan."""
    _plan_bytes, frozen = _validated_run_plan_artifacts(plan, artifacts)

    attestations: list[ArtifactAttestation] = []
    for root, manifest in frozen:
        try:
            attestation = attest_artifact_root(root, manifest)
        except KeyboardInterrupt:
            raise
        except RuntimeProbeError as exc:
            if exc.code in _ARTIFACT_ATTESTATION_ERROR_CODES:
                raise
            raise RuntimeProbeError("artifact-attestation-failed") from None
        except (Exception, SystemExit):
            raise RuntimeProbeError("artifact-attestation-failed") from None
        try:
            if (
                type(attestation) is not ArtifactAttestation
                or type(attestation.artifact_id) is not str
                or attestation.artifact_id != manifest.reference.artifact_id
                or type(attestation.manifest_sha256) is not str
                or attestation.manifest_sha256
                != manifest.reference.manifest_sha256
                or type(attestation.file_count) is not int
                or attestation.file_count != len(manifest.files)
                or type(attestation.total_bytes) is not int
                or attestation.total_bytes
                != sum(declaration.size for declaration in manifest.files)
            ):
                raise RuntimeProbeError("artifact-attestation-failed")
        except KeyboardInterrupt:
            raise
        except RuntimeProbeError:
            raise
        except (Exception, SystemExit):
            raise RuntimeProbeError("artifact-attestation-failed") from None
        attestations.append(attestation)
    return tuple(attestations)


def _decode_profile_payload(profile: object) -> dict[str, object]:
    if (
        type(profile) is not DecodeProfile
        or type(profile.profile_schema_version) is not int
        or profile.profile_schema_version != 1
        or type(profile.device) is not str
        or profile.device != "cpu"
        or type(profile.requested_compute_type) is not str
        or profile.requested_compute_type not in {"int8", "float32"}
        or type(profile.cpu_threads) is not int
        or not 1 <= profile.cpu_threads <= 256
        or type(profile.num_workers) is not int
        or profile.num_workers != 1
        or type(profile.language) is not str
        or profile.language != "de"
        or type(profile.task) is not str
        or profile.task != "transcribe"
        or type(profile.beam_size) is not int
        or not 1 <= profile.beam_size <= 32
        or type(profile.temperature_milli) is not int
        or profile.temperature_milli != 0
        or type(profile.vad_filter) is not bool
        or profile.vad_filter
        or type(profile.condition_on_previous_text) is not bool
        or profile.condition_on_previous_text
        or type(profile.word_timestamps) is not bool
        or profile.word_timestamps
        or type(profile.without_timestamps) is not bool
        or profile.without_timestamps
    ):
        raise RuntimeProbeError("benchmark-request-invalid")
    return {
        "beam_size": profile.beam_size,
        "condition_on_previous_text": profile.condition_on_previous_text,
        "cpu_threads": profile.cpu_threads,
        "device": profile.device,
        "language": profile.language,
        "num_workers": profile.num_workers,
        "profile_schema_version": profile.profile_schema_version,
        "requested_compute_type": profile.requested_compute_type,
        "task": profile.task,
        "temperature_milli": profile.temperature_milli,
        "vad_filter": profile.vad_filter,
        "without_timestamps": profile.without_timestamps,
        "word_timestamps": profile.word_timestamps,
    }


def _arm_artifact_payload(
    root: Path,
    manifest: ArtifactManifest,
) -> dict[str, object]:
    return {
        "manifest": _artifact_manifest_payload(manifest),
        "manifest_sha256": manifest.reference.manifest_sha256,
        "root": str(root),
    }


def _canonical_arm_request(payload: object) -> bytes:
    try:
        encoded = _canonical_json(
            payload,
            max_bytes=MAX_ARM_REQUEST_BYTES,
            error_code="benchmark-request-invalid",
        )
        if (
            type(encoded) is not bytes
            or not encoded
            or len(encoded) > MAX_ARM_REQUEST_BYTES
        ):
            raise RuntimeProbeError("benchmark-request-invalid")
        return encoded
    except KeyboardInterrupt:
        raise
    except RuntimeProbeError as exc:
        if exc.code == "benchmark-request-invalid":
            raise
        raise RuntimeProbeError("benchmark-request-invalid") from None
    except (Exception, SystemExit):
        raise RuntimeProbeError("benchmark-request-invalid") from None


def build_benchmark_pair_requests(
    plan: RunPlan,
    artifacts: tuple[tuple[object, ArtifactManifest], ...],
    runtime_specs: tuple[_RuntimeSpec, _RuntimeSpec],
    profile: DecodeProfile,
    *,
    model_block_index: int,
    phase_index: int,
    nonces: tuple[str, str],
) -> tuple[bytes, bytes]:
    """Build canonical pair requests without reading or attesting artifacts."""
    plan_bytes, frozen = _validated_run_plan_artifacts(plan, artifacts)
    try:
        if type(runtime_specs) is not tuple or len(runtime_specs) != 2:
            raise RuntimeProbeError("benchmark-request-invalid")
        specs = (
            _validated_spec(runtime_specs[0]),
            _validated_spec(runtime_specs[1]),
        )
        runtime_roots = (str(frozen[0][0]), str(frozen[1][0]))
        interpreter = specs[0].interpreter
        interpreter_path = Path(interpreter)
        ctranslate2_versions = tuple(
            spec.expected_ctranslate2_version for spec in specs
        )
        faster_whisper_versions = tuple(
            spec.expected_faster_whisper_version for spec in specs
        )
        if (
            tuple(spec.site_packages for spec in specs) != runtime_roots
            or specs[1].interpreter != interpreter
            or interpreter.startswith("//")
            or any(
                interpreter_path == root or root in interpreter_path.parents
                for root, _manifest in frozen
            )
            or len(set(ctranslate2_versions)) != 2
            or set(ctranslate2_versions) != {"4.7.2", "4.8.1"}
            or faster_whisper_versions != ("1.2.1", "1.2.1")
            or type(model_block_index) is not int
            or not 0 <= model_block_index < len(plan.model_blocks)
            or type(phase_index) is not int
            or not 0 <= phase_index < len(plan.model_blocks[model_block_index].phases)
            or type(nonces) is not tuple
            or len(nonces) != 2
            or any(type(nonce) is not str for nonce in nonces)
            or any(_NONCE_RE.fullmatch(nonce) is None for nonce in nonces)
            or nonces[0] == nonces[1]
        ):
            raise RuntimeProbeError("benchmark-request-invalid")

        profile_payload = _decode_profile_payload(profile)
        clips_root, clips_manifest = frozen[2]
        corpus_members = tuple(
            declaration
            for declaration in clips_manifest.files
            if declaration.path == "corpus-v1.json"
        )
        if (
            len(corpus_members) != 1
            or corpus_members[0].executable
            or not 1
            <= corpus_members[0].size
            <= MAX_CORPUS_BYTES
        ):
            raise RuntimeProbeError("benchmark-request-invalid")

        plan_payload = _decode_canonical_object(
            plan_bytes,
            max_bytes=MAX_RUN_PLAN_BYTES,
            error_code="benchmark-request-invalid",
        )
        profile_bytes = _canonical_json(
            profile_payload,
            max_bytes=MAX_RESULT_BYTES,
            error_code="benchmark-request-invalid",
        )
        plan_sha256 = hashlib.sha256(plan_bytes).hexdigest()
        profile_sha256 = hashlib.sha256(profile_bytes).hexdigest()
        model_root, model_manifest = frozen[3 + model_block_index]
        phase = plan.model_blocks[model_block_index].phases[phase_index]
        runtime_index_by_id = {
            plan.runtimes[0].artifact_id: 0,
            plan.runtimes[1].artifact_id: 1,
        }
        shared = {
            "clips": _arm_artifact_payload(clips_root, clips_manifest),
            "corpus": {"manifest_member": "corpus-v1.json"},
            "decode_profile": profile_payload,
            "decode_profile_sha256": profile_sha256,
            "mode": "benchmark-arm",
            "model": _arm_artifact_payload(model_root, model_manifest),
            "run_plan": plan_payload,
            "run_plan_sha256": plan_sha256,
            "schema_version": 1,
        }
        requests: list[bytes] = []
        for arm_index, nonce in enumerate(nonces):
            runtime_index = runtime_index_by_id[phase.pair.runtime_order[arm_index]]
            runtime_root, runtime_manifest = frozen[runtime_index]
            runtime_spec = specs[runtime_index]
            requests.append(
                _canonical_arm_request(
                    {
                        **shared,
                        "nonce": nonce,
                        "runtime": _arm_artifact_payload(
                            runtime_root,
                            runtime_manifest,
                        ),
                        "runtime_layout": {
                            "expected_ctranslate2_version": (
                                runtime_spec.expected_ctranslate2_version
                            ),
                            "expected_faster_whisper_version": (
                                runtime_spec.expected_faster_whisper_version
                            ),
                            "interpreter": interpreter,
                            "interpreter_contract": "host-tcb-unattested",
                            "site_packages_member": ".",
                        },
                        "selection": {
                            "arm_index": arm_index,
                            "model_block_index": model_block_index,
                            "phase_index": phase_index,
                        },
                    }
                )
            )
        return (requests[0], requests[1])
    except KeyboardInterrupt:
        raise
    except RuntimeProbeError as exc:
        if exc.code == "benchmark-request-invalid":
            raise
        raise RuntimeProbeError("benchmark-request-invalid") from None
    except (Exception, SystemExit):
        raise RuntimeProbeError("benchmark-request-invalid") from None


def _benchmark_pair_bytes_are_valid(value: object) -> bool:
    return (
        type(value) is tuple
        and len(value) == 2
        and all(
            type(item) is bytes and 1 <= len(item) <= MAX_ARM_REQUEST_BYTES
            for item in value
        )
        and value[0] is not value[1]
        and value[0] != value[1]
    )


def bind_benchmark_pair_requests(
    requests: tuple[bytes, bytes],
    plan: RunPlan,
    artifacts: tuple[tuple[object, ArtifactManifest], ...],
    runtime_specs: tuple[_RuntimeSpec, _RuntimeSpec],
    profile: DecodeProfile,
    *,
    model_block_index: int,
    phase_index: int,
    nonces: tuple[str, str],
) -> tuple[bytes, bytes]:
    """Bind declaration bytes only; no runtime, filesystem, or execution claim."""
    if not _benchmark_pair_bytes_are_valid(requests):
        raise RuntimeProbeError("benchmark-request-invalid") from None

    builder_error_code: str | None = None
    try:
        regenerated = build_benchmark_pair_requests(
            plan,
            artifacts,
            runtime_specs,
            profile,
            model_block_index=model_block_index,
            phase_index=phase_index,
            nonces=nonces,
        )
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit) as exc:
        if type(exc) is RuntimeProbeError:
            exc_args = exc.args
            exc_code = getattr(exc, "code", None)
        else:
            exc_args = None
            exc_code = None
        if (
            type(exc) is RuntimeProbeError
            and type(exc_args) is tuple
            and len(exc_args) == 1
            and type(exc_args[0]) is str
            and exc_args[0] in _BENCHMARK_BINDING_BUILDER_ERROR_CODES
            and type(exc_code) is str
            and exc_code == exc_args[0]
        ):
            builder_error_code = exc_args[0]
        else:
            builder_error_code = "benchmark-request-invalid"
    if builder_error_code is not None:
        raise RuntimeProbeError(builder_error_code) from None

    if not _benchmark_pair_bytes_are_valid(regenerated):
        raise RuntimeProbeError("benchmark-request-invalid") from None
    length_matches = (
        len(requests[0]) == len(regenerated[0]),
        len(requests[1]) == len(regenerated[1]),
    )
    comparison_failed = False
    try:
        digest_matches = (
            secrets.compare_digest(requests[0], regenerated[0]),
            secrets.compare_digest(requests[1], regenerated[1]),
        )
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        comparison_failed = True
    if comparison_failed:
        raise RuntimeProbeError("benchmark-request-invalid") from None
    if (
        type(digest_matches[0]) is not bool
        or type(digest_matches[1]) is not bool
        or not (
            length_matches[0]
            & length_matches[1]
            & digest_matches[0]
            & digest_matches[1]
        )
    ):
        raise RuntimeProbeError("benchmark-request-invalid") from None
    return regenerated


def _runtime_text(
    runtime: dict[str, object],
    key: str,
    pattern: re.Pattern[str],
) -> str:
    value = runtime.get(key)
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise RuntimeProbeError("result-invalid")
    return value


def _parse_observation_checked(
    data: bytes,
    *,
    nonce: str,
    returncode: int,
    spec: _RuntimeSpec,
) -> RuntimeObservation:
    payload = _decode_result(data)
    if "schema_version" not in payload or "nonce" not in payload:
        raise RuntimeProbeError("result-invalid")
    schema_version = payload.get("schema_version")
    if (
        type(schema_version) is not int
        or schema_version != worker_protocol.SCHEMA_VERSION
        or payload.get("nonce") != nonce
    ):
        raise RuntimeProbeError("result-mismatch")
    status = payload.get("status")
    if status == "error":
        error_code = payload.get("error_code")
        if (
            set(payload) != {"error_code", "nonce", "schema_version", "status"}
            or type(error_code) is not str
            or error_code not in worker_protocol.WORKER_ERROR_CODES
        ):
            raise RuntimeProbeError("result-invalid")
        if type(returncode) is not int or returncode != 65:
            raise RuntimeProbeError("worker-exit")
        raise RuntimeProbeError("worker-error")
    if (
        status != "ok"
        or set(payload) != {"mode", "nonce", "runtime", "schema_version", "status"}
        or payload.get("mode") != "probe-runtime"
    ):
        raise RuntimeProbeError("result-invalid")
    if type(returncode) is not int or returncode != 0:
        raise RuntimeProbeError("worker-exit")
    runtime = payload.get("runtime")
    if type(runtime) is not dict or set(runtime) != {
        "abi",
        "ctranslate2_version",
        "faster_whisper_version",
        "implementation",
        "python_version",
        "supported_cpu_compute_types",
    }:
        raise RuntimeProbeError("result-invalid")
    python_version = _runtime_text(runtime, "python_version", _VERSION_RE)
    implementation = _runtime_text(runtime, "implementation", _IMPLEMENTATION_RE)
    abi = _runtime_text(runtime, "abi", _ABI_RE)
    ctranslate2_version = _runtime_text(runtime, "ctranslate2_version", _VERSION_RE)
    faster_whisper_version = _runtime_text(
        runtime, "faster_whisper_version", _VERSION_RE
    )
    compute_types = runtime.get("supported_cpu_compute_types")
    if (
        type(compute_types) is not list
        or not compute_types
        or len(compute_types) > worker_protocol.MAX_COMPUTE_TYPES
        or any(
            type(value) is not str or _COMPUTE_TYPE_RE.fullmatch(value) is None
            for value in compute_types
        )
    ):
        raise RuntimeProbeError("result-invalid")
    if compute_types != sorted(set(compute_types)):
        raise RuntimeProbeError("result-invalid")
    if (
        ctranslate2_version != spec.expected_ctranslate2_version
        or faster_whisper_version != spec.expected_faster_whisper_version
    ):
        raise RuntimeProbeError("version-mismatch")
    return RuntimeObservation(
        python_version=python_version,
        implementation=implementation,
        abi=abi,
        ctranslate2_version=ctranslate2_version,
        faster_whisper_version=faster_whisper_version,
        supported_cpu_compute_types=tuple(compute_types),
    )


def _parse_observation(
    data: bytes,
    *,
    nonce: str,
    returncode: int,
    spec: _RuntimeSpec,
) -> RuntimeObservation:
    try:
        return _parse_observation_checked(
            data,
            nonce=nonce,
            returncode=returncode,
            spec=spec,
        )
    except RuntimeProbeError:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("result-invalid") from None


def observe_runtime(spec: _RuntimeSpec) -> RuntimeObservation:
    spec = _validated_spec(spec)
    worker_path = _worker_path()
    nonce = _new_nonce()
    runtime_argv = [
        spec.interpreter,
        "-I",
        "-S",
        "-B",
        worker_path,
        "--probe-runtime",
        "--site-packages",
        spec.site_packages,
        "--nonce",
        nonce,
    ]
    try:
        command = local_model_command(runtime_argv)
    except (Exception, SystemExit):
        raise RuntimeProbeError("low-qos-failed") from None
    try:
        response = run_process_bounded_output(
            command,
            timeout_seconds=RUNTIME_OBSERVATION_TIMEOUT_SECONDS,
            max_output_bytes=MAX_RESULT_BYTES,
            env=dict(_ENVIRONMENT),
            label="CTranslate2 benchmark runtime observation",
            deadline=time.monotonic() + RUNTIME_OBSERVATION_TIMEOUT_SECONDS,
            preserve_user_systemd_environment=True,
        )
        if type(response) is not tuple or len(response) != 3:
            raise RuntimeProbeError("runner-failed")
        returncode, stdout, stderr = response
    except RuntimeProbeError:
        raise
    except (Exception, SystemExit):
        raise RuntimeProbeError("runner-failed") from None
    if type(stdout) is not bytes or type(stderr) is not bytes:
        raise RuntimeProbeError("runner-failed")
    if stderr:
        raise RuntimeProbeError("auxiliary-output")
    if not stdout:
        raise RuntimeProbeError("result-missing")
    if len(stdout) > MAX_RESULT_BYTES:
        raise RuntimeProbeError("result-oversized")
    return _parse_observation(
        stdout,
        nonce=nonce,
        returncode=returncode,
        spec=spec,
    )
