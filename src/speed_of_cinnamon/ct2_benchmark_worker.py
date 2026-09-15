"""Isolated runtime observer for CTranslate2 benchmark preparation."""

from __future__ import annotations

import importlib
import errno
import fcntl
import hashlib
import json
import os
import platform
import re
import secrets
import stat
import sys
import sysconfig
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePath


SCHEMA_VERSION = 1
MAX_RESULT_BYTES = 16 * 1024
MAX_PATH_CHARS = 4096
MAX_ARTIFACT_MANIFEST_BYTES = 1024 * 1024
MAX_ARM_REQUEST_BYTES = 3_403_776
MAX_RUN_PLAN_BYTES = MAX_RESULT_BYTES
MAX_DECODE_PROFILE_BYTES = MAX_RESULT_BYTES
MAX_CORPUS_BYTES = 1 << 20
MAX_CORPUS_AUDIO_FILE_BYTES = 32 * 1024 * 1024
MAX_SELECTED_CORPUS_AUDIO_BYTES = 256 * 1024 * 1024
MAX_ATTESTATION_REQUEST_BYTES = MAX_ARTIFACT_MANIFEST_BYTES + (12 * MAX_PATH_CHARS) + 1024
MAX_ARTIFACT_FILES = 4096
MAX_ARTIFACT_PATH_CHARS = 512
MAX_ARTIFACT_PATH_BYTES = 2048
MAX_ARTIFACT_PATH_DEPTH = 32
MAX_DECLARED_ARTIFACT_BYTES = 1 << 30
MAX_COMPUTE_TYPES = 32
MAX_MODEL_BLOCKS = 8
FULL_PAIR_COUNT = 5
QUICK_PAIR_COUNT = 1
_MAX_UINT64 = (1 << 64) - 1
WORKER_ERROR_CODES = frozenset(
    {
        "artifact-attestation-failed",
        "artifact-root-invalid",
        "artifact-tree-changed",
        "artifact-tree-mismatch",
    "benchmark-corpus-invalid",
    "benchmark-audio-invalid",
        "benchmark-request-invalid",
        "internal",
        "python-isolation",
        "runtime-import",
        "runtime-values",
    }
)
ARTIFACT_ERROR_CODES = frozenset(
    {
        "artifact-attestation-failed",
        "artifact-root-invalid",
        "artifact-tree-changed",
        "artifact-tree-mismatch",
    }
)

_HEX_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+~-]{0,63}", re.ASCII)
_IMPLEMENTATION_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}", re.ASCII)
_ABI_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", re.ASCII)
_COMPUTE_TYPE_RE = re.compile(r"[a-z0-9_]{1,32}", re.ASCII)
_ARTIFACT_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}", re.ASCII)
_SCENARIO_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}", re.ASCII)
_CORPUS_ORDER_DOMAIN = b"SOC-CT2-CORPUS-ORDER-V1\x00"
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]", re.ASCII)
_HASH_CHUNK_BYTES = 65_536
_MAX_PREAD_INTERRUPTS = 16
_MAX_SHORT_READS = 64


class WorkerFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code if code in WORKER_ERROR_CODES else "internal"
        super().__init__(self.code)


def _absolute_path(value: object) -> str | None:
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


def _parse_arguments(arguments: Sequence[object]) -> tuple[str, str | None, str]:
    if isinstance(arguments, (str, bytes, bytearray)):
        raise WorkerFailure("internal")
    if len(arguments) == 5 and [arguments[index] for index in (0, 1, 3)] == [
        "--probe-runtime",
        "--site-packages",
        "--nonce",
    ]:
        mode = "probe-runtime"
        site_packages = _absolute_path(arguments[2])
        nonce = arguments[4]
    elif len(arguments) == 3 and [arguments[index] for index in (0, 1)] == [
        "--attest-artifact",
        "--nonce",
    ]:
        mode = "attest-artifact"
        site_packages = None
        nonce = arguments[2]
    else:
        raise WorkerFailure("internal")
    if (
        (mode == "probe-runtime" and site_packages is None)
        or type(nonce) is not str
        or _HEX_RE.fullmatch(nonce) is None
    ):
        raise WorkerFailure("internal")
    return mode, site_packages, nonce


def _isolated_python_start_is_valid() -> bool:
    try:
        values = (
            sys.flags.isolated,
            sys.flags.ignore_environment,
            sys.flags.no_user_site,
            sys.flags.no_site,
        )
        safe_path = sys.flags.safe_path
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        all(type(value) is int and value == 1 for value in values)
        and type(safe_path) is bool
        and safe_path
    )


def _validated_text(value: object, pattern: re.Pattern[str]) -> str:
    if (
        type(value) is not str
        or not value.isascii()
        or pattern.fullmatch(value) is None
    ):
        raise WorkerFailure("runtime-values")
    return value


def _compute_types(module: object) -> list[str]:
    try:
        values = module.get_supported_compute_types("cpu")
    except (Exception, SystemExit):
        raise WorkerFailure("runtime-values") from None
    if (
        not isinstance(values, (list, tuple, set, frozenset))
        or not values
        or len(values) > MAX_COMPUTE_TYPES
    ):
        raise WorkerFailure("runtime-values")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if (
            type(value) is not str
            or not value.isascii()
            or _COMPUTE_TYPE_RE.fullmatch(value) is None
            or value in seen
        ):
            raise WorkerFailure("runtime-values")
        seen.add(value)
        result.append(value)
    return sorted(result)


def _runtime_observation(site_packages: str) -> dict[str, object]:
    original_path = list(sys.path)
    expected_path = [*original_path, site_packages]
    if site_packages in original_path:
        raise WorkerFailure("runtime-values")
    sys.path.append(site_packages)
    try:
        try:
            ctranslate2 = importlib.import_module("ctranslate2")
            faster_whisper = importlib.import_module("faster_whisper")
        except (Exception, SystemExit):
            raise WorkerFailure("runtime-import") from None
        try:
            observation = {
                "python_version": _validated_text(
                    platform.python_version(), _VERSION_RE
                ),
                "implementation": _validated_text(
                    platform.python_implementation(), _IMPLEMENTATION_RE
                ),
                "abi": _validated_text(sysconfig.get_config_var("SOABI"), _ABI_RE),
                "ctranslate2_version": _validated_text(
                    getattr(ctranslate2, "__version__"), _VERSION_RE
                ),
                "faster_whisper_version": _validated_text(
                    getattr(faster_whisper, "__version__"), _VERSION_RE
                ),
                "supported_cpu_compute_types": _compute_types(ctranslate2),
            }
        except WorkerFailure:
            raise
        except (Exception, SystemExit):
            raise WorkerFailure("runtime-values") from None
        if sys.path != expected_path:
            raise WorkerFailure("runtime-values")
        return observation
    finally:
        sys.path[:] = original_path


def _canonical_json(
    payload: object,
    *,
    max_bytes: int = MAX_RESULT_BYTES,
    error_code: str = "internal",
) -> bytes:
    try:
        result = json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, UnicodeError, ValueError):
        raise WorkerFailure(error_code) from None
    if not result or len(result) > max_bytes:
        raise WorkerFailure(error_code)
    return result


def _write_all(payload: bytes) -> None:
    if type(payload) is not bytes or not payload or len(payload) > MAX_RESULT_BYTES:
        raise WorkerFailure("internal")
    offset = 0
    while offset < len(payload):
        remaining = payload[offset:]
        try:
            written = os.write(1, remaining)
        except InterruptedError:
            continue
        except OSError:
            raise WorkerFailure("internal") from None
        if type(written) is not int or written <= 0 or written > len(remaining):
            raise WorkerFailure("internal")
        offset += written


def _write_result(payload: dict[str, object]) -> None:
    _write_all(_canonical_json(payload))


def _error_payload(nonce: str, code: str) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "error_code": code,
        "nonce": nonce,
    }


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
        raise WorkerFailure(error_code)
    try:
        payload = json.loads(
            data.decode("ascii"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError):
        raise WorkerFailure(error_code) from None
    if (
        type(payload) is not dict
        or _canonical_json(
            payload,
            max_bytes=max_bytes,
            error_code=error_code,
        )
        != data
    ):
        raise WorkerFailure(error_code)
    return payload


class _RedactedRequestValue:
    __slots__ = ()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkReference(_RedactedRequestValue):
    artifact_id: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkFile(_RedactedRequestValue):
    path: str
    sha256: str
    size: int
    executable: bool


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkManifest(_RedactedRequestValue):
    reference: _BenchmarkReference
    kind: str
    files: tuple[_BenchmarkFile, ...]
    schema_version: int


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkArtifact(_RedactedRequestValue):
    root: str
    manifest: _BenchmarkManifest


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkRunPair(_RedactedRequestValue):
    runtime_order: tuple[str, str]
    clip_order_seed: int


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkRunPhase(_RedactedRequestValue):
    kind: str
    fresh_process_per_arm: bool
    warmup_before_measurement: bool
    warmup_discarded: bool
    pair: _BenchmarkRunPair


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkModelBlock(_RedactedRequestValue):
    model: _BenchmarkReference
    phases: tuple[_BenchmarkRunPhase, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkRunPlan(_RedactedRequestValue):
    schema_version: int
    mode: str
    seed: int
    pair_count: int
    decision_eligibility_capable: bool
    runtimes: tuple[_BenchmarkReference, _BenchmarkReference]
    clips: _BenchmarkReference
    model_blocks: tuple[_BenchmarkModelBlock, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkSelection(_RedactedRequestValue):
    arm_index: int
    model_block_index: int
    phase_index: int


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkRuntimeLayout(_RedactedRequestValue):
    interpreter: str
    interpreter_contract: str
    site_packages_member: str
    expected_ctranslate2_version: str
    expected_faster_whisper_version: str


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkCorpus(_RedactedRequestValue):
    manifest_member: str


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkDecodeProfile(_RedactedRequestValue):
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


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkArmRequest(_RedactedRequestValue):
    schema_version: int
    mode: str
    nonce: str
    run_plan: _BenchmarkRunPlan
    run_plan_sha256: str
    selection: _BenchmarkSelection
    runtime: _BenchmarkArtifact
    model: _BenchmarkArtifact
    clips: _BenchmarkArtifact
    runtime_layout: _BenchmarkRuntimeLayout
    corpus: _BenchmarkCorpus
    decode_profile: _BenchmarkDecodeProfile
    decode_profile_sha256: str


@dataclass(frozen=True, slots=True, repr=False)
class _CorpusClip(_RedactedRequestValue):
    id: str
    audio_path: str
    audio_sha256: str
    audio_size: int
    reference_text: str
    scenario: str


@dataclass(frozen=True, slots=True, repr=False)
class _ParsedCorpus(_RedactedRequestValue):
    schema_version: int
    language: str
    clips: tuple[_CorpusClip, ...]
    execution_clips: tuple[_CorpusClip, ...]


@dataclass(frozen=True, slots=True, repr=False)
class _BenchmarkArmBundle(_RedactedRequestValue):
    request: _BenchmarkArmRequest
    corpus: _ParsedCorpus


def _benchmark_invalid() -> None:
    raise WorkerFailure("benchmark-request-invalid")


def _exact_object(value: object, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        _benchmark_invalid()
    return value


def _benchmark_reference(value: object) -> _BenchmarkReference:
    payload = _exact_object(value, frozenset({"id", "manifest_sha256"}))
    artifact_id = payload.get("id")
    manifest_sha256 = payload.get("manifest_sha256")
    if (
        type(artifact_id) is not str
        or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
        or type(manifest_sha256) is not str
        or _HEX_RE.fullmatch(manifest_sha256) is None
    ):
        _benchmark_invalid()
    return _BenchmarkReference(artifact_id, manifest_sha256)


def _same_benchmark_reference(
    left: _BenchmarkReference,
    right: _BenchmarkReference,
) -> bool:
    return secrets.compare_digest(
        left.artifact_id,
        right.artifact_id,
    ) and secrets.compare_digest(left.manifest_sha256, right.manifest_sha256)


def _benchmark_pair_seed(seed: int, model_id: str, phase: str, index: int) -> int:
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


def _benchmark_run_pair(
    runtime_ids: tuple[str, str],
    *,
    first_runtime: str,
    clip_order_seed: int,
) -> _BenchmarkRunPair:
    other_runtime = runtime_ids[1] if first_runtime == runtime_ids[0] else runtime_ids[0]
    return _BenchmarkRunPair(
        runtime_order=(first_runtime, other_runtime),
        clip_order_seed=clip_order_seed,
    )


def _benchmark_measurement_first_runtimes(
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


def _benchmark_model_phases(
    runtime_ids: tuple[str, str],
    *,
    mode: str,
    model_id: str,
    seed: int,
) -> tuple[_BenchmarkRunPhase, ...]:
    cold_seed = _benchmark_pair_seed(seed, model_id, "cold", 0)
    cold = _BenchmarkRunPhase(
        kind="cold",
        fresh_process_per_arm=True,
        warmup_before_measurement=False,
        warmup_discarded=False,
        pair=_benchmark_run_pair(
            runtime_ids,
            first_runtime=runtime_ids[cold_seed & 1],
            clip_order_seed=cold_seed,
        ),
    )
    measurements = tuple(
        _BenchmarkRunPhase(
            kind="measurement",
            fresh_process_per_arm=True,
            warmup_before_measurement=True,
            warmup_discarded=True,
            pair=_benchmark_run_pair(
                runtime_ids,
                first_runtime=first_runtime,
                clip_order_seed=_benchmark_pair_seed(
                    seed,
                    model_id,
                    "measurement",
                    index,
                ),
            ),
        )
        for index, first_runtime in enumerate(
            _benchmark_measurement_first_runtimes(
                runtime_ids,
                mode=mode,
                seed=seed,
            )
        )
    )
    return (cold, *measurements)


def _benchmark_run_pair_from_payload(value: object) -> _BenchmarkRunPair:
    payload = _exact_object(
        value,
        frozenset({"clip_order_seed", "runtime_order"}),
    )
    runtime_order = payload.get("runtime_order")
    clip_order_seed = payload.get("clip_order_seed")
    if (
        type(runtime_order) is not list
        or len(runtime_order) != 2
        or any(type(item) is not str for item in runtime_order)
        or type(clip_order_seed) is not int
        or not 0 <= clip_order_seed <= _MAX_UINT64
    ):
        _benchmark_invalid()
    return _BenchmarkRunPair(
        runtime_order=(runtime_order[0], runtime_order[1]),
        clip_order_seed=clip_order_seed,
    )


def _benchmark_run_phase(value: object) -> _BenchmarkRunPhase:
    payload = _exact_object(
        value,
        frozenset(
            {
                "fresh_process_per_arm",
                "kind",
                "pair",
                "warmup_before_measurement",
                "warmup_discarded",
            }
        ),
    )
    kind = payload.get("kind")
    fresh = payload.get("fresh_process_per_arm")
    warmup = payload.get("warmup_before_measurement")
    discarded = payload.get("warmup_discarded")
    if (
        type(kind) is not str
        or kind not in {"cold", "measurement"}
        or type(fresh) is not bool
        or type(warmup) is not bool
        or type(discarded) is not bool
    ):
        _benchmark_invalid()
    return _BenchmarkRunPhase(
        kind=kind,
        fresh_process_per_arm=fresh,
        warmup_before_measurement=warmup,
        warmup_discarded=discarded,
        pair=_benchmark_run_pair_from_payload(payload.get("pair")),
    )


def _benchmark_run_plan(value: object) -> _BenchmarkRunPlan:
    payload = _exact_object(
        value,
        frozenset(
            {
                "clips",
                "decision_eligibility_capable",
                "mode",
                "model_blocks",
                "pair_count",
                "runtimes",
                "schema_version",
                "seed",
            }
        ),
    )
    schema_version = payload.get("schema_version")
    mode = payload.get("mode")
    seed = payload.get("seed")
    pair_count = payload.get("pair_count")
    eligible = payload.get("decision_eligibility_capable")
    if (
        type(schema_version) is not int
        or schema_version != 1
        or type(mode) is not str
        or mode not in {"full", "quick"}
        or type(seed) is not int
        or not 0 <= seed <= _MAX_UINT64
        or type(pair_count) is not int
        or pair_count != (FULL_PAIR_COUNT if mode == "full" else QUICK_PAIR_COUNT)
        or type(eligible) is not bool
        or eligible != (mode == "full")
    ):
        _benchmark_invalid()
    runtimes_value = _exact_object(
        payload.get("runtimes"),
        frozenset({"a", "b"}),
    )
    runtimes = (
        _benchmark_reference(runtimes_value.get("a")),
        _benchmark_reference(runtimes_value.get("b")),
    )
    if (
        runtimes[0].artifact_id == runtimes[1].artifact_id
        or runtimes[0].manifest_sha256 == runtimes[1].manifest_sha256
    ):
        _benchmark_invalid()
    clips = _benchmark_reference(payload.get("clips"))
    blocks_value = payload.get("model_blocks")
    if (
        type(blocks_value) is not list
        or not 1 <= len(blocks_value) <= MAX_MODEL_BLOCKS
    ):
        _benchmark_invalid()
    blocks: list[_BenchmarkModelBlock] = []
    runtime_ids = (runtimes[0].artifact_id, runtimes[1].artifact_id)
    for value in blocks_value:
        block = _exact_object(value, frozenset({"model", "phases"}))
        model = _benchmark_reference(block.get("model"))
        phases_value = block.get("phases")
        if type(phases_value) is not list:
            _benchmark_invalid()
        phases = tuple(_benchmark_run_phase(item) for item in phases_value)
        if phases != _benchmark_model_phases(
            runtime_ids,
            mode=mode,
            model_id=model.artifact_id,
            seed=seed,
        ):
            _benchmark_invalid()
        blocks.append(_BenchmarkModelBlock(model=model, phases=phases))
    references = (*runtimes, clips, *(block.model for block in blocks))
    reference_ids = tuple(value.artifact_id for value in references)
    reference_hashes = tuple(value.manifest_sha256 for value in references)
    if (
        len(set(reference_ids)) != len(reference_ids)
        or len(set(reference_hashes)) != len(reference_hashes)
    ):
        _benchmark_invalid()
    return _BenchmarkRunPlan(
        schema_version=schema_version,
        mode=mode,
        seed=seed,
        pair_count=pair_count,
        decision_eligibility_capable=eligible,
        runtimes=runtimes,
        clips=clips,
        model_blocks=tuple(blocks),
    )


def _benchmark_selection(
    value: object,
    plan: _BenchmarkRunPlan,
) -> _BenchmarkSelection:
    payload = _exact_object(
        value,
        frozenset({"arm_index", "model_block_index", "phase_index"}),
    )
    arm_index = payload.get("arm_index")
    model_block_index = payload.get("model_block_index")
    phase_index = payload.get("phase_index")
    if (
        type(arm_index) is not int
        or not 0 <= arm_index < 2
        or type(model_block_index) is not int
        or not 0 <= model_block_index < len(plan.model_blocks)
        or type(phase_index) is not int
        or not 0 <= phase_index < len(plan.model_blocks[model_block_index].phases)
    ):
        _benchmark_invalid()
    return _BenchmarkSelection(arm_index, model_block_index, phase_index)


def _benchmark_manifest(
    value: object,
    *,
    manifest_sha256: object,
) -> _BenchmarkManifest:
    if type(manifest_sha256) is not str or _HEX_RE.fullmatch(manifest_sha256) is None:
        _benchmark_invalid()
    manifest_bytes = _canonical_json(
        value,
        max_bytes=MAX_ARTIFACT_MANIFEST_BYTES,
        error_code="benchmark-request-invalid",
    )
    if not secrets.compare_digest(
        hashlib.sha256(manifest_bytes).hexdigest(),
        manifest_sha256,
    ):
        _benchmark_invalid()
    payload = _exact_object(
        value,
        frozenset({"artifact_id", "files", "kind", "schema_version"}),
    )
    artifact_id = payload.get("artifact_id")
    kind = payload.get("kind")
    schema_version = payload.get("schema_version")
    files_value = payload.get("files")
    if (
        type(artifact_id) is not str
        or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
        or type(kind) is not str
        or kind not in {"runtime", "model", "clips"}
        or type(schema_version) is not int
        or schema_version != 1
        or type(files_value) is not list
        or not 1 <= len(files_value) <= MAX_ARTIFACT_FILES
    ):
        _benchmark_invalid()
    files: list[_BenchmarkFile] = []
    total_size = 0
    paths: list[str] = []
    for value in files_value:
        file_payload = _exact_object(
            value,
            frozenset({"executable", "path", "sha256", "size"}),
        )
        path = _safe_artifact_path(file_payload.get("path"))
        sha256 = file_payload.get("sha256")
        size = file_payload.get("size")
        executable = file_payload.get("executable")
        if (
            path is None
            or type(sha256) is not str
            or _HEX_RE.fullmatch(sha256) is None
            or type(size) is not int
            or not 0 <= size <= _MAX_UINT64
            or type(executable) is not bool
            or size > MAX_DECLARED_ARTIFACT_BYTES - total_size
        ):
            _benchmark_invalid()
        total_size += size
        paths.append(path)
        files.append(_BenchmarkFile(path, sha256, size, executable))
    path_set = set(paths)
    if paths != sorted(paths) or len(paths) != len(path_set):
        _benchmark_invalid()
    for path in paths:
        components = path.split("/")
        if any(
            "/".join(components[:depth]) in path_set
            for depth in range(1, len(components))
        ):
            _benchmark_invalid()
    return _BenchmarkManifest(
        reference=_BenchmarkReference(artifact_id, manifest_sha256),
        kind=kind,
        files=tuple(files),
        schema_version=schema_version,
    )


def _benchmark_artifact(
    value: object,
    *,
    expected_reference: _BenchmarkReference,
    expected_kind: str,
    domain: str,
) -> _BenchmarkArtifact:
    payload = _exact_object(
        value,
        frozenset({"manifest", "manifest_sha256", "root"}),
    )
    root = _safe_root_path(payload.get("root"))
    if root is None or (domain == "runtime" and _absolute_path(root) is None):
        _benchmark_invalid()
    manifest = _benchmark_manifest(
        payload.get("manifest"),
        manifest_sha256=payload.get("manifest_sha256"),
    )
    if (
        manifest.kind != expected_kind
        or not _same_benchmark_reference(manifest.reference, expected_reference)
    ):
        _benchmark_invalid()
    return _BenchmarkArtifact(root=root, manifest=manifest)


def _benchmark_decode_profile(value: object) -> _BenchmarkDecodeProfile:
    payload = _exact_object(
        value,
        frozenset(
            {
                "beam_size",
                "condition_on_previous_text",
                "cpu_threads",
                "device",
                "language",
                "num_workers",
                "profile_schema_version",
                "requested_compute_type",
                "task",
                "temperature_milli",
                "vad_filter",
                "without_timestamps",
                "word_timestamps",
            }
        ),
    )
    profile = _BenchmarkDecodeProfile(
        profile_schema_version=payload.get("profile_schema_version"),
        device=payload.get("device"),
        requested_compute_type=payload.get("requested_compute_type"),
        cpu_threads=payload.get("cpu_threads"),
        num_workers=payload.get("num_workers"),
        language=payload.get("language"),
        task=payload.get("task"),
        beam_size=payload.get("beam_size"),
        temperature_milli=payload.get("temperature_milli"),
        vad_filter=payload.get("vad_filter"),
        condition_on_previous_text=payload.get("condition_on_previous_text"),
        word_timestamps=payload.get("word_timestamps"),
        without_timestamps=payload.get("without_timestamps"),
    )
    if (
        type(profile.profile_schema_version) is not int
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
        _benchmark_invalid()
    return profile


def _benchmark_runtime_layout(value: object) -> _BenchmarkRuntimeLayout:
    payload = _exact_object(
        value,
        frozenset(
            {
                "expected_ctranslate2_version",
                "expected_faster_whisper_version",
                "interpreter",
                "interpreter_contract",
                "site_packages_member",
            }
        ),
    )
    layout = _BenchmarkRuntimeLayout(
        interpreter=payload.get("interpreter"),
        interpreter_contract=payload.get("interpreter_contract"),
        site_packages_member=payload.get("site_packages_member"),
        expected_ctranslate2_version=payload.get("expected_ctranslate2_version"),
        expected_faster_whisper_version=payload.get("expected_faster_whisper_version"),
    )
    if (
        _absolute_path(layout.interpreter) is None
        or layout.interpreter.startswith("//")
        or type(layout.interpreter_contract) is not str
        or layout.interpreter_contract != "host-tcb-unattested"
        or type(layout.site_packages_member) is not str
        or layout.site_packages_member != "."
        or type(layout.expected_ctranslate2_version) is not str
        or layout.expected_ctranslate2_version not in {"4.7.2", "4.8.1"}
        or type(layout.expected_faster_whisper_version) is not str
        or layout.expected_faster_whisper_version != "1.2.1"
    ):
        _benchmark_invalid()
    return layout


def _canonical_benchmark_domain(
    value: object,
    *,
    max_bytes: int,
    expected_sha256: object,
) -> bytes:
    if type(expected_sha256) is not str or _HEX_RE.fullmatch(expected_sha256) is None:
        _benchmark_invalid()
    encoded = _canonical_json(
        value,
        max_bytes=max_bytes,
        error_code="benchmark-request-invalid",
    )
    if not secrets.compare_digest(
        hashlib.sha256(encoded).hexdigest(),
        expected_sha256,
    ):
        _benchmark_invalid()
    return encoded


def _parse_benchmark_arm_request_checked(
    data: bytes,
    *,
    expected_nonce: str,
) -> _BenchmarkArmRequest:
    if type(expected_nonce) is not str or _HEX_RE.fullmatch(expected_nonce) is None:
        _benchmark_invalid()
    payload = _decode_canonical_object(
        data,
        max_bytes=MAX_ARM_REQUEST_BYTES,
        error_code="benchmark-request-invalid",
    )
    if set(payload) != {
        "clips",
        "corpus",
        "decode_profile",
        "decode_profile_sha256",
        "mode",
        "model",
        "nonce",
        "run_plan",
        "run_plan_sha256",
        "runtime",
        "runtime_layout",
        "schema_version",
        "selection",
    }:
        _benchmark_invalid()
    schema_version = payload.get("schema_version")
    mode = payload.get("mode")
    nonce = payload.get("nonce")
    if (
        type(schema_version) is not int
        or schema_version != 1
        or type(mode) is not str
        or mode != "benchmark-arm"
        or type(nonce) is not str
        or _HEX_RE.fullmatch(nonce) is None
        or not secrets.compare_digest(nonce, expected_nonce)
    ):
        _benchmark_invalid()

    _canonical_benchmark_domain(
        payload.get("run_plan"),
        max_bytes=MAX_RUN_PLAN_BYTES,
        expected_sha256=payload.get("run_plan_sha256"),
    )
    run_plan = _benchmark_run_plan(payload.get("run_plan"))
    selection = _benchmark_selection(payload.get("selection"), run_plan)
    block = run_plan.model_blocks[selection.model_block_index]
    phase = block.phases[selection.phase_index]
    runtime_id = phase.pair.runtime_order[selection.arm_index]
    runtime_reference = next(
        value for value in run_plan.runtimes if value.artifact_id == runtime_id
    )
    runtime = _benchmark_artifact(
        payload.get("runtime"),
        expected_reference=runtime_reference,
        expected_kind="runtime",
        domain="runtime",
    )
    model = _benchmark_artifact(
        payload.get("model"),
        expected_reference=block.model,
        expected_kind="model",
        domain="model",
    )
    clips = _benchmark_artifact(
        payload.get("clips"),
        expected_reference=run_plan.clips,
        expected_kind="clips",
        domain="clips",
    )
    roots = (runtime.root, model.root, clips.root)
    if len(set(roots)) != len(roots):
        _benchmark_invalid()

    layout = _benchmark_runtime_layout(payload.get("runtime_layout"))
    interpreter_path = PurePath(layout.interpreter)
    if any(
        interpreter_path == PurePath(root) or PurePath(root) in interpreter_path.parents
        for root in roots
    ):
        _benchmark_invalid()

    corpus_payload = _exact_object(
        payload.get("corpus"),
        frozenset({"manifest_member"}),
    )
    corpus_member = corpus_payload.get("manifest_member")
    if type(corpus_member) is not str or corpus_member != "corpus-v1.json":
        _benchmark_invalid()
    corpus_files = tuple(
        value for value in clips.manifest.files if value.path == corpus_member
    )
    if (
        len(corpus_files) != 1
        or corpus_files[0].executable
        or not 1 <= corpus_files[0].size <= MAX_CORPUS_BYTES
    ):
        _benchmark_invalid()
    corpus = _BenchmarkCorpus(manifest_member=corpus_member)

    _canonical_benchmark_domain(
        payload.get("decode_profile"),
        max_bytes=MAX_DECODE_PROFILE_BYTES,
        expected_sha256=payload.get("decode_profile_sha256"),
    )
    profile = _benchmark_decode_profile(payload.get("decode_profile"))
    return _BenchmarkArmRequest(
        schema_version=schema_version,
        mode=mode,
        nonce=nonce,
        run_plan=run_plan,
        run_plan_sha256=payload["run_plan_sha256"],
        selection=selection,
        runtime=runtime,
        model=model,
        clips=clips,
        runtime_layout=layout,
        corpus=corpus,
        decode_profile=profile,
        decode_profile_sha256=payload["decode_profile_sha256"],
    )


def _parse_benchmark_arm_request(
    data: bytes,
    *,
    expected_nonce: str,
) -> _BenchmarkArmRequest:
    """Validate one arm declaration, not its pair, roots, attestation, or execution."""
    failed = False
    try:
        result = _parse_benchmark_arm_request_checked(
            data,
            expected_nonce=expected_nonce,
        )
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        failed = True
    if failed:
        raise WorkerFailure("benchmark-request-invalid") from None
    return result


def _corpus_invalid() -> None:
    raise WorkerFailure("benchmark-corpus-invalid")


def _reference_text_is_valid(value: object) -> bool:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 4096
        or unicodedata.normalize("NFC", value) != value
        or value.startswith(" ")
        or value.endswith(" ")
        or "  " in value
        or any(character.isspace() and character != " " for character in value)
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value
        )
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _order_corpus_clips(
    clips: tuple[_CorpusClip, ...],
    *,
    seed: int,
) -> tuple[_CorpusClip, ...]:
    if type(seed) is not int or not 0 <= seed <= _MAX_UINT64:
        _corpus_invalid()

    def order_key(clip: _CorpusClip) -> tuple[bytes, str]:
        digest = hashlib.sha256(
            _CORPUS_ORDER_DOMAIN
            + seed.to_bytes(8, "big")
            + b"\x00"
            + clip.id.encode("ascii")
        ).digest()
        return digest, clip.id

    return tuple(sorted(clips, key=order_key))


def _parse_benchmark_corpus_checked(
    corpus_data: bytes,
    *,
    request: _BenchmarkArmRequest,
) -> _ParsedCorpus:
    if (
        type(request) is not _BenchmarkArmRequest
        or type(corpus_data) is not bytes
        or not 1 <= len(corpus_data) <= MAX_CORPUS_BYTES
    ):
        _corpus_invalid()

    manifest_files = request.clips.manifest.files
    corpus_declarations = tuple(
        value
        for value in manifest_files
        if value.path == request.corpus.manifest_member
    )
    if len(corpus_declarations) != 1:
        _corpus_invalid()
    corpus_declaration = corpus_declarations[0]
    if (
        corpus_declaration.executable
        or corpus_declaration.size != len(corpus_data)
        or not secrets.compare_digest(
            hashlib.sha256(corpus_data).hexdigest(),
            corpus_declaration.sha256,
        )
    ):
        _corpus_invalid()

    payload = _decode_canonical_object(
        corpus_data,
        max_bytes=MAX_CORPUS_BYTES,
        error_code="benchmark-corpus-invalid",
    )
    if set(payload) != {"clips", "language", "schema_version"}:
        _corpus_invalid()
    schema_version = payload.get("schema_version")
    language = payload.get("language")
    values = payload.get("clips")
    if (
        type(schema_version) is not int
        or schema_version != 1
        or type(language) is not str
        or language != "de"
        or language != request.decode_profile.language
        or type(values) is not list
        or not 12 <= len(values) <= 20
    ):
        _corpus_invalid()

    if any(value.executable for value in manifest_files):
        _corpus_invalid()
    manifest_by_path = {value.path: value for value in manifest_files}
    if len(manifest_by_path) != len(manifest_files):
        _corpus_invalid()

    clips: list[_CorpusClip] = []
    ids: list[str] = []
    paths: list[str] = []
    audio_hashes: list[str] = []
    reference_codepoints = 0
    reference_bytes = 0
    clip_keys = {"audio_path", "id", "reference_text", "scenario"}
    for value in values:
        if type(value) is not dict or set(value) != clip_keys:
            _corpus_invalid()
        clip_id = value.get("id")
        audio_path_value = value.get("audio_path")
        reference_text = value.get("reference_text")
        scenario = value.get("scenario")
        audio_path = _safe_artifact_path(audio_path_value)
        if (
            type(clip_id) is not str
            or _ARTIFACT_ID_RE.fullmatch(clip_id) is None
            or type(scenario) is not str
            or _SCENARIO_RE.fullmatch(scenario) is None
            or audio_path is None
            or not audio_path.endswith((".wav", ".flac"))
            or audio_path == request.corpus.manifest_member
            or not _reference_text_is_valid(reference_text)
        ):
            _corpus_invalid()
        declaration = manifest_by_path.get(audio_path)
        if declaration is None or declaration.executable or declaration.size <= 0:
            _corpus_invalid()
        reference_encoded = reference_text.encode("utf-8")
        reference_codepoints += len(reference_text)
        reference_bytes += len(reference_encoded)
        if reference_codepoints > 65_536 or reference_bytes > 262_144:
            _corpus_invalid()
        ids.append(clip_id)
        paths.append(audio_path)
        audio_hashes.append(declaration.sha256)
        clips.append(
            _CorpusClip(
                id=clip_id,
                audio_path=audio_path,
                audio_sha256=declaration.sha256,
                audio_size=declaration.size,
                reference_text=reference_text,
                scenario=scenario,
            )
        )

    if (
        ids != sorted(ids)
        or len(ids) != len(set(ids))
        or len(paths) != len(set(paths))
        or len(audio_hashes) != len(set(audio_hashes))
        or set(manifest_by_path)
        != {request.corpus.manifest_member, *paths}
    ):
        _corpus_invalid()

    plan = request.run_plan
    phase = plan.model_blocks[request.selection.model_block_index].phases[
        request.selection.phase_index
    ]
    ordered = _order_corpus_clips(tuple(clips), seed=phase.pair.clip_order_seed)
    if plan.mode == "full":
        execution = ordered
    elif (
        plan.mode == "quick"
        and plan.pair_count == QUICK_PAIR_COUNT
        and plan.decision_eligibility_capable is False
    ):
        execution = ordered[:3]
    else:
        _corpus_invalid()
    return _ParsedCorpus(
        schema_version=schema_version,
        language=language,
        clips=ordered,
        execution_clips=execution,
    )


def _parse_benchmark_arm_bundle(
    request_data: bytes,
    corpus_data: bytes,
    *,
    expected_nonce: str,
) -> _BenchmarkArmBundle:
    """Parse declarations only; no filesystem, attestation, or execution claim."""
    request = _parse_benchmark_arm_request(
        request_data,
        expected_nonce=expected_nonce,
    )
    if type(request) is not _BenchmarkArmRequest:
        raise WorkerFailure("benchmark-request-invalid") from None
    corpus_failed = False
    try:
        corpus = _parse_benchmark_corpus_checked(corpus_data, request=request)
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        corpus_failed = True
    if corpus_failed or type(corpus) is not _ParsedCorpus:
        raise WorkerFailure("benchmark-corpus-invalid") from None
    return _BenchmarkArmBundle(request=request, corpus=corpus)


def _read_attestation_request() -> bytes:
    data = bytearray()
    interruptions = 0
    while len(data) <= MAX_ATTESTATION_REQUEST_BYTES:
        try:
            chunk = os.read(
                0,
                min(65_536, MAX_ATTESTATION_REQUEST_BYTES + 1 - len(data)),
            )
        except InterruptedError:
            interruptions += 1
            if interruptions > _MAX_PREAD_INTERRUPTS:
                raise WorkerFailure("artifact-attestation-failed") from None
            continue
        except (Exception, SystemExit):
            raise WorkerFailure("artifact-attestation-failed") from None
        if type(chunk) is not bytes:
            raise WorkerFailure("artifact-attestation-failed")
        if not chunk:
            break
        data.extend(chunk)
    if not data or len(data) > MAX_ATTESTATION_REQUEST_BYTES:
        raise WorkerFailure("artifact-attestation-failed")
    return bytes(data)


@dataclass(frozen=True, slots=True)
class _ArtifactFile:
    path: str
    sha256: str
    size: int
    executable: bool


@dataclass(frozen=True, slots=True)
class _ArtifactRequest:
    root: str
    artifact_id: str
    manifest_sha256: str
    files: tuple[_ArtifactFile, ...]


@dataclass(frozen=True, slots=True)
class _StatSnapshot:
    device: int
    inode: int
    mode: int
    link_count: int
    user_id: int
    group_id: int
    size: int
    modified_ns: int
    changed_ns: int


def _safe_artifact_path(value: object) -> str | None:
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


def _safe_root_path(value: object) -> str | None:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_PATH_CHARS
        or value.startswith("//")
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
        or any(
            unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
            for character in value
        )
    ):
        return None
    return value


def _parse_artifact_request(data: bytes, *, nonce: str) -> _ArtifactRequest:
    payload = _decode_canonical_object(
        data,
        max_bytes=MAX_ATTESTATION_REQUEST_BYTES,
        error_code="artifact-attestation-failed",
    )
    if set(payload) != {
        "manifest",
        "manifest_sha256",
        "mode",
        "nonce",
        "root",
        "schema_version",
    }:
        raise WorkerFailure("artifact-attestation-failed")
    if (
        payload.get("mode") != "attest-artifact"
        or type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != SCHEMA_VERSION
        or type(payload.get("nonce")) is not str
        or not secrets.compare_digest(payload["nonce"], nonce)
    ):
        raise WorkerFailure("artifact-attestation-failed")
    root = _safe_root_path(payload.get("root"))
    if root is None:
        raise WorkerFailure("artifact-root-invalid")
    manifest_sha256 = payload.get("manifest_sha256")
    manifest = payload.get("manifest")
    if (
        type(manifest_sha256) is not str
        or _HEX_RE.fullmatch(manifest_sha256) is None
        or type(manifest) is not dict
        or set(manifest) != {"artifact_id", "files", "kind", "schema_version"}
    ):
        raise WorkerFailure("artifact-attestation-failed")
    manifest_bytes = _canonical_json(
        manifest,
        max_bytes=MAX_ARTIFACT_MANIFEST_BYTES,
        error_code="artifact-attestation-failed",
    )
    if not secrets.compare_digest(
        hashlib.sha256(manifest_bytes).hexdigest(),
        manifest_sha256,
    ):
        raise WorkerFailure("artifact-attestation-failed")
    artifact_id = manifest.get("artifact_id")
    kind = manifest.get("kind")
    values = manifest.get("files")
    if (
        type(artifact_id) is not str
        or _ARTIFACT_ID_RE.fullmatch(artifact_id) is None
        or type(kind) is not str
        or kind not in {"runtime", "model", "clips"}
        or type(manifest.get("schema_version")) is not int
        or manifest.get("schema_version") != 1
        or type(values) is not list
        or not 1 <= len(values) <= MAX_ARTIFACT_FILES
    ):
        raise WorkerFailure("artifact-attestation-failed")
    files: list[_ArtifactFile] = []
    total = 0
    for value in values:
        if type(value) is not dict or set(value) != {
            "executable",
            "path",
            "sha256",
            "size",
        }:
            raise WorkerFailure("artifact-attestation-failed")
        path = _safe_artifact_path(value.get("path"))
        sha256 = value.get("sha256")
        size = value.get("size")
        executable = value.get("executable")
        if (
            path is None
            or type(sha256) is not str
            or _HEX_RE.fullmatch(sha256) is None
            or type(size) is not int
            or size < 0
            or size > MAX_DECLARED_ARTIFACT_BYTES - total
            or type(executable) is not bool
        ):
            raise WorkerFailure("artifact-attestation-failed")
        total += size
        files.append(_ArtifactFile(path, sha256, size, executable))
    paths = [value.path for value in files]
    path_set = set(paths)
    if paths != sorted(paths) or len(paths) != len(path_set):
        raise WorkerFailure("artifact-attestation-failed")
    for path in paths:
        components = path.split("/")
        if any("/".join(components[:depth]) in path_set for depth in range(1, len(components))):
            raise WorkerFailure("artifact-attestation-failed")
    return _ArtifactRequest(root, artifact_id, manifest_sha256, tuple(files))


def _open_flags(*, directory: bool) -> int:
    names = ["O_CLOEXEC", "O_NOFOLLOW"]
    if directory:
        names.append("O_DIRECTORY")
    else:
        names.append("O_NONBLOCK")
    values = [getattr(os, name, None) for name in names]
    if any(type(value) is not int or value == 0 for value in values):
        raise WorkerFailure("artifact-attestation-failed")
    flags = os.O_RDONLY
    for value in values:
        flags |= value
    return flags


def _snapshot(descriptor: int) -> _StatSnapshot:
    try:
        value = os.fstat(descriptor)
    except (Exception, SystemExit):
        raise WorkerFailure("artifact-attestation-failed") from None
    return _StatSnapshot(
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_uid,
        value.st_gid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _has_cloexec(descriptor: int) -> bool:
    try:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    except (Exception, SystemExit):
        raise WorkerFailure("artifact-attestation-failed") from None
    return type(flags) is int and flags & fcntl.FD_CLOEXEC == fcntl.FD_CLOEXEC


def _directory_snapshot(
    descriptor: int,
    *,
    user_id: int,
    device: int | None,
    exact_mode: bool,
    error_code: str,
) -> _StatSnapshot:
    value = _snapshot(descriptor)
    if (
        not stat.S_ISDIR(value.mode)
        or (exact_mode and stat.S_IMODE(value.mode) != 0o700)
        or (exact_mode and value.user_id != user_id)
        or (device is not None and value.device != device)
        or not _has_cloexec(descriptor)
    ):
        raise WorkerFailure(error_code)
    return value


def _file_snapshot(
    descriptor: int,
    declaration: _ArtifactFile,
    *,
    user_id: int,
    device: int,
    error_code: str,
) -> _StatSnapshot:
    value = _snapshot(descriptor)
    required_mode = 0o700 if declaration.executable else 0o600
    if (
        not stat.S_ISREG(value.mode)
        or stat.S_IMODE(value.mode) != required_mode
        or value.user_id != user_id
        or value.device != device
        or value.link_count != 1
        or value.size != declaration.size
        or not _has_cloexec(descriptor)
    ):
        raise WorkerFailure(error_code)
    return value


def _close_descriptor(descriptor: int, primary: BaseException | None = None) -> None:
    cleanup: BaseException | None = None
    try:
        os.close(descriptor)
    except BaseException as exc:
        cleanup = exc
    if isinstance(primary, KeyboardInterrupt):
        raise primary
    if isinstance(cleanup, KeyboardInterrupt):
        raise cleanup
    if cleanup is not None:
        raise WorkerFailure("artifact-attestation-failed") from None
    if primary is not None:
        raise primary


def _open_bound_root(root: str, *, error_code: str) -> int:
    current: int | None = None
    result: int | None = None
    primary: BaseException | None = None
    try:
        current = os.open("/", _open_flags(directory=True))
        _directory_snapshot(
            current,
            user_id=-1,
            device=None,
            exact_mode=False,
            error_code=error_code,
        )
        for component in root.split("/")[1:]:
            if not component:
                continue
            next_descriptor = os.open(
                component,
                _open_flags(directory=True),
                dir_fd=current,
            )
            try:
                _directory_snapshot(
                    next_descriptor,
                    user_id=-1,
                    device=None,
                    exact_mode=False,
                    error_code=error_code,
                )
            except BaseException as exc:
                _close_descriptor(next_descriptor, exc)
            old = current
            current = next_descriptor
            _close_descriptor(old)
        if current is None:
            raise WorkerFailure(error_code)
        result = current
        current = None
    except (KeyboardInterrupt, WorkerFailure) as exc:
        primary = exc
    except (Exception, SystemExit):
        primary = WorkerFailure(error_code)
    if current is not None:
        _close_descriptor(current, primary)
    if primary is not None:
        raise primary from None
    if result is None:
        raise WorkerFailure(error_code)
    return result


def _open_child(
    parent: int,
    name: str,
    *,
    directory: bool,
    error_code: str,
) -> int:
    try:
        return os.open(name, _open_flags(directory=directory), dir_fd=parent)
    except KeyboardInterrupt:
        raise
    except OSError as exc:
        if exc.errno in {errno.ENOENT, errno.ELOOP, errno.ENOTDIR}:
            raise WorkerFailure(error_code) from None
        raise WorkerFailure("artifact-attestation-failed") from None
    except (Exception, SystemExit):
        raise WorkerFailure("artifact-attestation-failed") from None


def _directory_children(
    descriptor: int,
    expected: set[str],
    *,
    error_code: str,
) -> None:
    seen: set[str] = set()
    count = 0
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                count += 1
                if count > len(expected):
                    raise WorkerFailure(error_code)
                name = entry.name
                if type(name) is not str or name not in expected or name in seen:
                    raise WorkerFailure(error_code)
                seen.add(name)
    except KeyboardInterrupt:
        raise
    except WorkerFailure:
        raise
    except (Exception, SystemExit):
        raise WorkerFailure("artifact-attestation-failed") from None
    if seen != expected:
        raise WorkerFailure(error_code)


def _artifact_file_digest(descriptor: int, declared_size: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    interruptions = 0
    read_calls = 0
    maximum_calls = (
        (declared_size + _HASH_CHUNK_BYTES - 1) // _HASH_CHUNK_BYTES
        + _MAX_SHORT_READS
        + 1
    )
    while offset < declared_size:
        read_calls += 1
        if read_calls > maximum_calls:
            raise WorkerFailure("artifact-attestation-failed")
        requested = min(_HASH_CHUNK_BYTES, declared_size - offset)
        try:
            chunk = os.pread(descriptor, requested, offset)
        except InterruptedError:
            interruptions += 1
            if interruptions > _MAX_PREAD_INTERRUPTS:
                raise WorkerFailure("artifact-attestation-failed") from None
            continue
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit):
            raise WorkerFailure("artifact-attestation-failed") from None
        if type(chunk) is not bytes or not chunk or len(chunk) > requested:
            raise WorkerFailure("artifact-attestation-failed")
        digest.update(chunk)
        offset += len(chunk)
    while True:
        try:
            trailing = os.pread(descriptor, 1, declared_size)
            break
        except InterruptedError:
            interruptions += 1
            if interruptions > _MAX_PREAD_INTERRUPTS:
                raise WorkerFailure("artifact-attestation-failed") from None
        except KeyboardInterrupt:
            raise
        except (Exception, SystemExit):
            raise WorkerFailure("artifact-attestation-failed") from None
    if type(trailing) is not bytes or trailing:
        raise WorkerFailure("artifact-attestation-failed")
    return digest.hexdigest()


def _manifest_trie(files: tuple[_ArtifactFile, ...]) -> dict[str, object]:
    root: dict[str, object] = {}
    for declaration in files:
        current = root
        components = declaration.path.split("/")
        for component in components[:-1]:
            child = current.setdefault(component, {})
            if type(child) is not dict:
                raise WorkerFailure("artifact-attestation-failed")
            current = child
        current[components[-1]] = declaration
    return root


def _walk_artifact_tree(
    directory: int,
    trie: dict[str, object],
    *,
    parts: tuple[str, ...],
    user_id: int,
    root_device: int,
    snapshots: dict[tuple[str, ...], _StatSnapshot],
    expected: dict[tuple[str, ...], _StatSnapshot] | None,
) -> None:
    second_walk = expected is not None
    error_code = "artifact-tree-changed" if second_walk else "artifact-tree-mismatch"
    before = _directory_snapshot(
        directory,
        user_id=user_id,
        device=root_device,
        exact_mode=True,
        error_code=error_code,
    )
    if second_walk and expected.get(parts) != before:
        raise WorkerFailure("artifact-tree-changed")
    snapshots[parts] = before
    _directory_children(directory, set(trie), error_code=error_code)
    for name in sorted(trie):
        child = trie[name]
        child_parts = (*parts, name)
        if type(child) is dict:
            descriptor = _open_child(
                directory,
                name,
                directory=True,
                error_code=error_code,
            )
            primary: BaseException | None = None
            try:
                _walk_artifact_tree(
                    descriptor,
                    child,
                    parts=child_parts,
                    user_id=user_id,
                    root_device=root_device,
                    snapshots=snapshots,
                    expected=expected,
                )
            except BaseException as exc:
                primary = exc
            _close_descriptor(descriptor, primary)
            continue
        if type(child) is not _ArtifactFile:
            raise WorkerFailure("artifact-attestation-failed")
        descriptor = _open_child(
            directory,
            name,
            directory=False,
            error_code=error_code,
        )
        primary = None
        try:
            file_before = _file_snapshot(
                descriptor,
                child,
                user_id=user_id,
                device=root_device,
                error_code=error_code,
            )
            if second_walk and expected.get(child_parts) != file_before:
                raise WorkerFailure("artifact-tree-changed")
            snapshots[child_parts] = file_before
            digest = _artifact_file_digest(descriptor, child.size)
            file_after = _file_snapshot(
                descriptor,
                child,
                user_id=user_id,
                device=root_device,
                error_code="artifact-tree-changed",
            )
            if file_after != file_before:
                raise WorkerFailure("artifact-tree-changed")
            if not secrets.compare_digest(digest, child.sha256):
                raise WorkerFailure(error_code)
        except BaseException as exc:
            primary = exc
        _close_descriptor(descriptor, primary)
    after = _directory_snapshot(
        directory,
        user_id=user_id,
        device=root_device,
        exact_mode=True,
        error_code="artifact-tree-changed",
    )
    if after != before:
        raise WorkerFailure("artifact-tree-changed")


def _attest_artifact(request: _ArtifactRequest) -> dict[str, object]:
    try:
        user_id = os.getuid()
    except (Exception, SystemExit):
        raise WorkerFailure("artifact-attestation-failed") from None
    if type(user_id) is not int or user_id < 0:
        raise WorkerFailure("artifact-attestation-failed")
    root = _open_bound_root(request.root, error_code="artifact-root-invalid")
    primary: BaseException | None = None
    result: dict[str, object] | None = None
    try:
        root_snapshot = _directory_snapshot(
            root,
            user_id=user_id,
            device=None,
            exact_mode=True,
            error_code="artifact-root-invalid",
        )
        trie = _manifest_trie(request.files)
        first: dict[tuple[str, ...], _StatSnapshot] = {}
        _walk_artifact_tree(
            root,
            trie,
            parts=(),
            user_id=user_id,
            root_device=root_snapshot.device,
            snapshots=first,
            expected=None,
        )
        second: dict[tuple[str, ...], _StatSnapshot] = {}
        _walk_artifact_tree(
            root,
            trie,
            parts=(),
            user_id=user_id,
            root_device=root_snapshot.device,
            snapshots=second,
            expected=first,
        )
        if second != first or _directory_snapshot(
            root,
            user_id=user_id,
            device=root_snapshot.device,
            exact_mode=True,
            error_code="artifact-tree-changed",
        ) != root_snapshot:
            raise WorkerFailure("artifact-tree-changed")
        rebound = _open_bound_root(request.root, error_code="artifact-tree-changed")
        rebound_error: BaseException | None = None
        try:
            if _directory_snapshot(
                rebound,
                user_id=user_id,
                device=root_snapshot.device,
                exact_mode=True,
                error_code="artifact-tree-changed",
            ) != root_snapshot:
                raise WorkerFailure("artifact-tree-changed")
        except BaseException as exc:
            rebound_error = exc
        _close_descriptor(rebound, rebound_error)
        result = {
            "artifact_id": request.artifact_id,
            "file_count": len(request.files),
            "manifest_sha256": request.manifest_sha256,
            "total_bytes": sum(value.size for value in request.files),
        }
    except BaseException as exc:
        primary = exc
    _close_descriptor(root, primary)
    if result is None:
        raise WorkerFailure("artifact-attestation-failed")
    return result


def main(argv: list[str] | None = None) -> int:
    try:
        mode, site_packages, nonce = _parse_arguments(
            sys.argv[1:] if argv is None else argv
        )
    except (Exception, SystemExit):
        return 65
    try:
        if not _isolated_python_start_is_valid():
            raise WorkerFailure("python-isolation")
        if mode == "probe-runtime":
            if site_packages is None:
                raise WorkerFailure("internal")
            payload = {
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
                "mode": mode,
                "nonce": nonce,
                "runtime": _runtime_observation(site_packages),
            }
        else:
            request = _parse_artifact_request(
                _read_attestation_request(),
                nonce=nonce,
            )
            payload = {
                "attestation": _attest_artifact(request),
                "mode": mode,
                "nonce": nonce,
                "schema_version": SCHEMA_VERSION,
                "status": "ok",
            }
        returncode = 0
    except WorkerFailure as exc:
        code = exc.code
        if mode == "attest-artifact" and code not in ARTIFACT_ERROR_CODES:
            code = "artifact-attestation-failed"
        payload = _error_payload(nonce, code)
        returncode = 65
    except (Exception, SystemExit):
        code = (
            "artifact-attestation-failed"
            if mode == "attest-artifact"
            else "internal"
        )
        payload = _error_payload(nonce, code)
        returncode = 65
    try:
        _write_result(payload)
    except (Exception, SystemExit):
        return 65
    return returncode


def _benchmark_audio_preflight(
    bundle: object,
    execution_index: object,
) -> tuple[str, tuple[str, ...], _CorpusClip, _BenchmarkFile]:
    if (
        type(bundle) is not _BenchmarkArmBundle
        or type(bundle.request) is not _BenchmarkArmRequest
        or type(bundle.corpus) is not _ParsedCorpus
        or type(execution_index) is not int
    ):
        raise ValueError

    clips = bundle.corpus.clips
    execution_clips = bundle.corpus.execution_clips
    plan = bundle.request.run_plan
    selection = bundle.request.selection
    if (
        type(plan) is not _BenchmarkRunPlan
        or type(selection) is not _BenchmarkSelection
        or type(selection.model_block_index) is not int
        or type(selection.phase_index) is not int
        or type(selection.arm_index) is not int
        or selection.arm_index not in {0, 1}
        or type(plan.model_blocks) is not tuple
        or not 0 <= selection.model_block_index < len(plan.model_blocks)
    ):
        raise ValueError
    block = plan.model_blocks[selection.model_block_index]
    if (
        type(block) is not _BenchmarkModelBlock
        or type(block.phases) is not tuple
        or not 0 <= selection.phase_index < len(block.phases)
    ):
        raise ValueError
    phase = block.phases[selection.phase_index]
    if type(phase) is not _BenchmarkRunPhase or type(phase.pair) is not _BenchmarkRunPair:
        raise ValueError
    order_seed = phase.pair.clip_order_seed
    if (
        type(order_seed) is not int
        or not 0 <= order_seed <= _MAX_UINT64
        or type(plan.mode) is not str
        or plan.mode not in {"full", "quick"}
    ):
        raise ValueError

    if (
        type(clips) is not tuple
        or not 12 <= len(clips) <= 20
        or type(execution_clips) is not tuple
        or not 0 <= execution_index < len(execution_clips)
    ):
        raise ValueError

    seen_clip_ids: set[str] = set()
    seen_clip_paths: set[str] = set()
    seen_clip_hashes: set[str] = set()
    for clip in clips:
        if (
            type(clip) is not _CorpusClip
            or type(clip.id) is not str
            or _ARTIFACT_ID_RE.fullmatch(clip.id) is None
            or type(clip.audio_path) is not str
            or _safe_artifact_path(clip.audio_path) != clip.audio_path
            or type(clip.audio_sha256) is not str
            or len(clip.audio_sha256) != 64
            or any(value not in "0123456789abcdef" for value in clip.audio_sha256)
            or type(clip.audio_size) is not int
            or not 1 <= clip.audio_size <= MAX_CORPUS_AUDIO_FILE_BYTES
            or clip.id in seen_clip_ids
            or clip.audio_path in seen_clip_paths
            or clip.audio_sha256 in seen_clip_hashes
        ):
            raise ValueError
        seen_clip_ids.add(clip.id)
        seen_clip_paths.add(clip.audio_path)
        seen_clip_hashes.add(clip.audio_sha256)

    def order_key(clip: _CorpusClip) -> tuple[bytes, str]:
        material = (
            b"SOC-CT2-CORPUS-ORDER-V1\x00"
            + order_seed.to_bytes(8, "big")
            + b"\x00"
            + clip.id.encode("ascii")
        )
        return hashlib.new("sha256", material).digest(), clip.id

    canonical_clips = tuple(sorted(clips, key=order_key))
    if any(actual is not expected for actual, expected in zip(clips, canonical_clips)):
        raise ValueError
    canonical_execution = clips if plan.mode == "full" else clips[:3]
    if len(execution_clips) != len(canonical_execution) or any(
        actual is not expected
        for actual, expected in zip(execution_clips, canonical_execution)
    ):
        raise ValueError

    artifact = bundle.request.clips
    if (
        type(artifact) is not _BenchmarkArtifact
        or type(artifact.root) is not str
        or type(artifact.manifest) is not _BenchmarkManifest
        or type(artifact.manifest.files) is not tuple
        or not 1 <= len(artifact.manifest.files) <= MAX_ARTIFACT_FILES
    ):
        raise ValueError
    root = _safe_root_path(artifact.root)
    if root is None:
        raise ValueError
    declarations: list[_BenchmarkFile] = []
    for declaration in artifact.manifest.files:
        if (
            type(declaration) is not _BenchmarkFile
            or type(declaration.path) is not str
            or _safe_artifact_path(declaration.path) != declaration.path
            or type(declaration.sha256) is not str
            or len(declaration.sha256) != 64
            or any(value not in "0123456789abcdef" for value in declaration.sha256)
            or type(declaration.size) is not int
            or declaration.size < 0
            or type(declaration.executable) is not bool
        ):
            raise ValueError
    for clip in clips:
        matching = tuple(
            declaration
            for declaration in artifact.manifest.files
            if declaration.path == clip.audio_path
        )
        if len(matching) != 1:
            raise ValueError
        declaration = matching[0]
        if (
            declaration.executable
            or declaration.size != clip.audio_size
            or not secrets.compare_digest(declaration.sha256, clip.audio_sha256)
        ):
            raise ValueError
        declarations.append(declaration)

    selected_total = 0
    for clip in execution_clips:
        if clip.audio_size > MAX_SELECTED_CORPUS_AUDIO_BYTES - selected_total:
            raise ValueError
        selected_total += clip.audio_size

    selected = execution_clips[execution_index]
    declaration = declarations[execution_index]
    components = tuple(selected.audio_path.split("/"))
    return root, components, selected, declaration


def _pread_corpus_audio(descriptor: int, size: int) -> bytes:
    if (
        type(descriptor) is not int
        or type(size) is not int
        or not 1 <= size <= MAX_CORPUS_AUDIO_FILE_BYTES
        or type(_HASH_CHUNK_BYTES) is not int
        or _HASH_CHUNK_BYTES <= 0
        or type(_MAX_SHORT_READS) is not int
        or _MAX_SHORT_READS < 0
    ):
        raise ValueError
    payload = bytearray(size)
    offset = 0
    calls = 0
    interruptions = 0
    short_reads = 0
    maximum_calls = (
        (size + _HASH_CHUNK_BYTES - 1) // _HASH_CHUNK_BYTES
        + _MAX_SHORT_READS
        + _MAX_PREAD_INTERRUPTS
        + 1
    )
    while offset < size:
        requested = min(_HASH_CHUNK_BYTES, size - offset)
        try:
            calls += 1
            if calls > maximum_calls:
                raise ValueError
            chunk = os.pread(descriptor, requested, offset)
        except InterruptedError:
            interruptions += 1
            if interruptions > _MAX_PREAD_INTERRUPTS:
                raise
            continue
        if type(chunk) is not bytes or not chunk or len(chunk) > requested:
            raise ValueError
        if len(chunk) < requested:
            short_reads += 1
            if short_reads > _MAX_SHORT_READS:
                raise ValueError
        payload[offset : offset + len(chunk)] = chunk
        offset += len(chunk)

    while True:
        try:
            calls += 1
            if calls > maximum_calls:
                raise ValueError
            trailing = os.pread(descriptor, 1, size)
            break
        except InterruptedError:
            interruptions += 1
            if interruptions > _MAX_PREAD_INTERRUPTS:
                raise
    if type(trailing) is not bytes or trailing:
        raise ValueError
    return bytes(payload)


def _new_audio_descriptor_slots(count: int) -> list[int | None]:
    return [None] * count


def _read_attested_corpus_audio_checked(
    bundle: object,
    *,
    execution_index: object,
) -> bytes:
    root, components, _selected, declaration = _benchmark_audio_preflight(
        bundle,
        execution_index,
    )
    user_id = os.geteuid()
    if type(user_id) is not int or user_id < 0:
        raise ValueError
    descriptors = _new_audio_descriptor_slots(len(components) + 1)
    if len(descriptors) != len(components) + 1 or any(
        descriptor is not None for descriptor in descriptors
    ):
        raise ValueError
    directory_snapshots: list[_StatSnapshot | None] = [None] * len(components)
    primary_error: BaseException | None = None
    result: bytes | None = None
    try:
        root_descriptor = _open_bound_root(
            root,
            error_code="benchmark-audio-invalid",
        )
        descriptors[0] = root_descriptor
        root_snapshot = _directory_snapshot(
            root_descriptor,
            user_id=user_id,
            device=None,
            exact_mode=True,
            error_code="benchmark-audio-invalid",
        )
        directory_snapshots[0] = root_snapshot

        parent_descriptor = root_descriptor
        for slot, component in enumerate(components[:-1], 1):
            child_descriptor = _open_child(
                parent_descriptor,
                component,
                directory=True,
                error_code="benchmark-audio-invalid",
            )
            descriptors[slot] = child_descriptor
            directory_snapshots[slot] = _directory_snapshot(
                child_descriptor,
                user_id=user_id,
                device=root_snapshot.device,
                exact_mode=True,
                error_code="benchmark-audio-invalid",
            )
            parent_descriptor = child_descriptor

        file_descriptor = _open_child(
            parent_descriptor,
            components[-1],
            directory=False,
            error_code="benchmark-audio-invalid",
        )
        descriptors[-1] = file_descriptor
        file_snapshot = _file_snapshot(
            file_descriptor,
            declaration,
            user_id=user_id,
            device=root_snapshot.device,
            error_code="benchmark-audio-invalid",
        )
        payload = _pread_corpus_audio(file_descriptor, declaration.size)
        if _file_snapshot(
            file_descriptor,
            declaration,
            user_id=user_id,
            device=root_snapshot.device,
            error_code="benchmark-audio-invalid",
        ) != file_snapshot:
            raise ValueError
        for slot in range(len(directory_snapshots)):
            descriptor = descriptors[slot]
            initial = directory_snapshots[slot]
            if type(descriptor) is not int or type(initial) is not _StatSnapshot:
                raise ValueError
            if _directory_snapshot(
                descriptor,
                user_id=user_id,
                device=root_snapshot.device,
                exact_mode=True,
                error_code="benchmark-audio-invalid",
            ) != initial:
                raise ValueError
        digest = hashlib.sha256(payload).hexdigest()
        if not secrets.compare_digest(digest, declaration.sha256):
            raise ValueError
        result = payload
    except BaseException as error:
        primary_error = error

    for descriptor in reversed(descriptors):
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except BaseException as error:
            if isinstance(error, KeyboardInterrupt):
                if not isinstance(primary_error, KeyboardInterrupt):
                    primary_error = error
            elif primary_error is None:
                primary_error = error
    if primary_error is not None:
        raise primary_error
    if type(result) is not bytes:
        raise ValueError
    return result


def _read_attested_corpus_audio(
    bundle: _BenchmarkArmBundle,
    *,
    execution_index: int,
) -> bytes:
    failed = False
    result: object = None
    try:
        result = _read_attested_corpus_audio_checked(
            bundle,
            execution_index=execution_index,
        )
    except KeyboardInterrupt:
        raise
    except (Exception, SystemExit):
        failed = True
    if failed or type(result) is not bytes:
        raise WorkerFailure("benchmark-audio-invalid") from None
    return result


if __name__ == "__main__":
    raise SystemExit(main())
