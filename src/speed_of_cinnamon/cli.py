from __future__ import annotations

import argparse
import errno
import hashlib
import heapq
import io
import json
import math
import os
import platform
import re
import secrets
import signal
import shutil
import stat as stat_module
import subprocess  # nosec B404
import sys
import threading
import time
import tempfile
import urllib.parse
from collections.abc import Mapping
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

from . import __version__, remote_http
from . import doctor
from .backup import BackupError, BackupInput, create_backup, restore_backup, restore_dry_run, verify_backup
from .alarms import (
    _locked_alarm_store,
    _save_alarm_store_unlocked,
    add_alarm,
    check_due_alarms,
    list_alarm_payload,
    load_alarm_store,
    remove_alarm,
    save_alarm_store,
    set_alarm_enabled,
)
from .app_logging import (
    DEFAULT_LOG_LEVEL,
    LOG_LEVELS,
    MAX_LOG_MESSAGE_CHARS,
    configure_logging,
    log_event,
    sanitize_error_message,
)
from .artifact_crypto import (
    ARTIFACT_ENCRYPTION_CHOICES,
    ARTIFACT_ENCRYPTION_OFF,
    ArtifactCryptoError,
    encrypted_path_for,
    is_encrypted_path,
    normalize_artifact_encryption,
    read_decrypted_bytes_from_file,
    read_private_bytes,
    write_encrypted_bytes_atomically,
)
from .command_chain import CommandChainError, run_process_bounded_output
from .doctor import parse_settings_json, report as doctor_report
from .http_safety import has_unsafe_url_characters, is_loopback_hostname
from .models import (
    CATALOG,
    ModelError,
    ModelSpec,
    download_model,
    list_models,
    model_path,
    model_status,
    model_supports_language,
    resolve_model,
    remove_model,
)
from .output import OutputNotInsertedError, insert_text
from .proc_safety import _read_proc_boot_id, _read_proc_stat
from .process_priority import (
    PriorityScopeError,
    SOC_CPU_WEIGHT,
    SOC_IO_WEIGHT,
    SOC_PRIORITY_SCOPE_MARKER,
    apply_process_priority,
    ensure_soc_priority_scope,
    parse_priority_scope_identity,
    serialize_priority_scope_identity,
    verify_priority_scope_identity,
)
from .paths import (
    APP_ID,
    APP_NAME,
    default_settings_export_file,
    default_state_file,
    diagnostics_dir,
    blacklist_file,
    profanity_filter_file,
    ensure_runtime_dirs,
    recordings_dir,
    state_dir,
    transcript_dir,
)
from .path_safety import (
    _rename_without_replacing,
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    open_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)
from .secure_delete import (
    secure_wipe_bound_regular_fd,
    secure_wipe_regular_file_at,
)
from .postprocessor import (
    DEFAULT_OLLAMA_URL,
    DEFAULT_OPENAI_COMPATIBLE_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    PostProcessError,
    postprocess_public_failure_message,
    post_process_text,
)
from .security_parser import (
    apply_security_mode,
    apply_blacklist_mode,
    load_blacklist_file,
    parse_security_directives,
    update_blacklist_file,
)
from .recorder import (
    MAX_INPUT_SOURCE_FIELD_CHARS,
    MAX_RECORDING_INPUT_DEVICE_CHARS,
    _PROCESS_IDENTITY_REUSED,
    _PROCESS_IDENTITY_SAME,
    _PROCESS_IDENTITY_UNKNOWN,
    RecorderCommand,
    recorder_startup_ownership,
    RecorderStartupError,
    RecordingLevel,
    SilenceDetectionResult,  # noqa: F401 - public CLI compatibility export
    choose_recorder,
    detect_silent_recording,
    list_input_sources,
    read_recording_level,
    reencode_recording_to_flac,
    normalize_input_device,
    process_group_has_live_processes,
    _recorder_scope_for_pid,
    _recorder_scope_has_live_processes,
    _recorder_scope_is_current,
    _recorder_scope_is_soc_recorder_unit,
    _recorder_scope_matches_unit,
    _recorder_scope_unit_is_soc_recorder_unit,
    _recorder_scope_unit_is_stably_gone,
    _recorder_scope_unit_for_process,
    _recording_process_identity_relation,
    start_recorder,
    stop_process,
    trim_recording_silence,
)
from .recorder import MAX_RECORDING_SECONDS, RecorderError, validate_recording_path
from .settings_export import (
    MAX_SETTINGS_EXPORT_PATH_CHARS,
    POST_COMMIT_DIRECTORY_CLOSE_WARNING,
    POST_COMMIT_RECOVERY_BACKUP_CLEANUP_WARNING,
    SettingsExportError,
    _reject_duplicate_json_keys,
    _reject_non_finite_json_number,
    normalize_alarm_store,
    read_export,
    write_export,
)
from .setup_plan import build_setup_plan
from .state import (
    MAX_CLEANUP_BACKUP_IDENTITY_VALUE,
    MAX_PENDING_CLEANUP_BACKUP_ENTRIES,
    MAX_PENDING_CLEANUP_OWNER_PATH_CHARS,
    MAX_PENDING_CLEANUP_OWNER_PATHS,
    RecordingState,
    StateStore,
    VALID_STATE_STATUSES,
    is_state_read_error,
    now_iso,
    process_is_alive,
)
from .text_utils import sanitize_special_chars
from .transcriber import (
    MAX_AUDIO_FILE_BYTES,
    MAX_AUDIO_PATH_CHARS,
    MAX_LANGUAGE_CODE_CHARS,
    TranscriptionError,
    TranscriptionCleanupError,
    normalize_backend,
    validate_audio_file,
    transcribe,
)
from .profanity_filter import (
    MAX_PROFANITY_FILTER_BYTES,
    _compile_profanity_replacements_with_hints,
    _normalize_profanity_candidate,
    _profanity_pattern_hint,  # noqa: F401 - public CLI compatibility export
    PROFANITY_REPLACEMENTS,  # noqa: F401 - public CLI compatibility export
    PROFANITY_REPLACEMENT_PAIRS,  # noqa: F401 - public CLI compatibility export
    compile_profanity_replacements,  # noqa: F401 - public CLI compatibility export
    parse_profanity_replacement_list,
    render_profanity_replacement_list,
)


def _note_cleanup_failure(primary: BaseException, _cleanup_error: BaseException) -> None:
    try:
        primary.add_note("diagnostics artifact cleanup failed")
    except Exception:
        return


RECORDER_START_GRACE_SECONDS = 0.2
RECORDER_PROCESS_RECONCILIATION_DELAY_SECONDS = 0.01
DEFAULT_KEEP_TRANSCRIPTS = 500
DEFAULT_KEEP_RECORDINGS = 20
DEFAULT_RECORDING_MAX_AGE_DAYS = 7
MAX_TEMP_RECORDING_FILES = 20
TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS = 3600
TRANSIENT_TRANSCRIPT_OWNER_SUFFIX = ".owner"
TRANSIENT_TRANSCRIPT_WRITE_ERROR = "failed to write transcript file"
TRANSIENT_TRANSCRIPT_CLEANUP_ERROR = "failed to clean up transcript file"
TRANSIENT_TRANSCRIPT_PROCESSING_ERROR = "transcribe failed"
_PUBLIC_PAYLOAD_STATUSES = VALID_STATE_STATUSES | frozenset(
    {"skipped", "warning"}
)
_DOCTOR_REPORT_FIELDS = (
    "ok",
    "checks",
    "desktop",
    "configured",
    "audio",
    "acceleration",
    "applet",
    "notes",
)
_DOCTOR_CPU_PROBE_STATUSES = frozenset(
    {"ok", "partial", "unavailable", "unsupported-platform"}
)
_DOCTOR_CTRANSLATE2_PROBE_STATUSES = frozenset(
    {"ok", "partial", "unavailable", "timeout", "failed"}
)
_DOCTOR_AUDIO_PROBE_STATUSES = frozenset({"ok", "failed", "unavailable"})
_DOCTOR_PIPEWIRE_SOURCE_CLASSES = frozenset(
    {"unknown", "alsa", "bluetooth", "other"}
)
_DOCTOR_RESOLVED_BACKENDS = frozenset(
    {"command", "faster-whisper", "whisper-cpp", "whisper", "openai-compatible"}
)
_DOCTOR_GNA_REASONS = frozenset({"upstream software stack discontinued"})
_DOCTOR_AUDIO_PACKAGE_NAMES = (
    "alsa-sof-firmware",
    "alsa-ucm",
    "alsa-ucm-utils",
    "pipewire",
    "pipewire-utils",
    "wireplumber",
)
_DOCTOR_AUDIO_PROBE_NAMES = ("pci", "modules", "packages", "pipewire")
TRANSIENT_POSTPROCESS_ERROR = (
    "post-process failed SOC-P001: transcript processing could not be completed. "
    "Retry finalization."
)
LEGACY_TRANSIENT_TRANSCRIPT_INSERT_ERROR = "insert failed"
TRANSIENT_TRANSCRIPT_INSERT_ERROR = (
    "insert failed SOC-O001: no text was inserted. Retry finalization."
)
TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR = (
    "insert failed SOC-O002: output completion is uncertain. "
    "Check the target before retrying."
)
TRANSIENT_TRANSCRIPT_INTERRUPT_ERROR = "transcript operation interrupted"
TRANSIENT_RECORDING_PROCESS_ERROR = "recording process reconciliation failed"
TRANSIENT_AUDIO_PATH_ERROR = "recording audio path validation failed"
TRANSIENT_AUDIO_FILE_ERROR = "audio file validation failed"
TRANSIENT_SILENCE_DETECTION_ERROR = "silence detection failed"
RECORDING_ARTIFACT_EXTENSIONS = (".wav", ".flac", ".log", ".socenc")
ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES = (".wav.socenc", ".flac.socenc")
TRANSCRIPT_ARTIFACT_SUFFIXES = (".txt", ".socenc")
ENCRYPTED_TRANSCRIPT_SUFFIX = ".txt.socenc"
MAX_LOG_EXCERPT_CHARS = 2000
MAX_STORED_TRANSCRIPT_BYTES = 1_000_000
MAX_TRANSCRIPT_HISTORY_TEXT_CHARS = 4_000
MAX_TRANSCRIPTS_DOCUMENT_CHARS = 180_000
MAX_TRANSCRIPTS_DOCUMENT_JSON_BYTES = 240_000
MAX_TRANSCRIPTS_EXPORT_CHARS = 64_000_000
MAX_PROFANITY_OUTPUT_CHARS = 2_000_000
# AES-GCM adds a 16-byte tag; URL-safe base64 needs 4 * ceil(n / 3)
# bytes. Supported fixed envelope metadata is below 1 KiB, which remains a
# hard reserve rather than admitting the generic 340 MiB artifact ceiling.
MAX_ENCRYPTED_STORED_TRANSCRIPT_BYTES = (
    4 * ((MAX_STORED_TRANSCRIPT_BYTES + 16 + 2) // 3) + 1024
)
HISTORY_PREVIEW_REDACTED_TEXT = "[transcript preview redacted]"
HISTORY_METADATA_REDACTED_TEXT = "[transcript metadata redacted]"
TRANSCRIPT_DISPLAY_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f\u2028\u2029]")
TRANSCRIPT_METADATA_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_PUBLIC_FAILURE_CODES = {
    "transcription": frozenset(
        {*(f"SOC-T{index:03d}" for index in range(1, 13)), "SOC-T099"}
    ),
    "postprocess": frozenset({"SOC-P001"}),
    "output": frozenset({"SOC-O001", "SOC-O002"}),
}
_ERROR_RECORD_GROUP_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,127}\Z", re.ASCII)
_PERSISTED_STATUS_ERROR_PROVENANCE = object()


class _ClassifiedPublicError(RuntimeError):
    __slots__ = (
        "failure_stage",
        "failure_code",
        "failure_reason",
        "provider_status",
        "remote_error_code",
    )

    def __init__(
        self,
        message: str,
        failure_stage: str,
        failure_code: str,
        *,
        failure_reason: str | None = None,
        provider_status: int | None = None,
        remote_error_code: str | None = None,
    ) -> None:
        if (
            type(message) is not str
            or type(failure_stage) is not str
            or type(failure_code) is not str
            or failure_code not in _PUBLIC_FAILURE_CODES.get(failure_stage, ())
            or failure_code not in message
        ):
            raise RuntimeError("classified public error is invalid")
        try:
            if remote_error_code is None and type(failure_reason) is str:
                remote_error_code = remote_http.default_error_code_for_failure_reason(
                    failure_reason
                )
            failure_reason, provider_status = remote_http.validate_failure_metadata(
                failure_reason,
                provider_status,
                error_code=remote_error_code,
            )
        except ValueError:
            raise RuntimeError("classified public error diagnostics are invalid") from None
        super().__init__(message)
        self.failure_stage = failure_stage
        self.failure_code = failure_code
        self.failure_reason = failure_reason
        self.provider_status = provider_status
        self.remote_error_code = remote_error_code


def _validated_public_failure_fields(
    failure_stage: object,
    failure_code: object,
    message: object,
) -> dict[str, str]:
    if (
        type(failure_stage) is not str
        or type(failure_code) is not str
        or type(message) is not str
        or failure_code not in _PUBLIC_FAILURE_CODES.get(failure_stage, ())
        or failure_code not in message
    ):
        return {}
    return {
        "failure_stage": failure_stage,
        "failure_code": failure_code,
    }


def _classified_public_error_fields(
    error: BaseException,
    message: object,
) -> dict[str, object]:
    if type(error) is not _ClassifiedPublicError:
        return {}
    error_args = getattr(error, "args", ())
    if (
        type(error_args) is not tuple
        or len(error_args) != 1
        or type(error_args[0]) is not str
        or error_args[0] != message
    ):
        return {}
    fields = _validated_public_failure_fields(
        getattr(error, "failure_stage", None),
        getattr(error, "failure_code", None),
        message,
    )
    if not fields:
        return {}
    try:
        reason, status = remote_http.validate_failure_metadata(
            getattr(error, "failure_reason", None),
            getattr(error, "provider_status", None),
            error_code=getattr(error, "remote_error_code", None),
        )
    except ValueError:
        return fields
    if reason is not None:
        fields["failure_reason"] = reason
    if status is not None:
        fields["provider_status"] = status
    return fields


def _fresh_classified_public_error(
    error: _ClassifiedPublicError,
    message: str,
) -> _ClassifiedPublicError | None:
    fields = _classified_public_error_fields(error, message)
    if not fields:
        return None
    fresh_error = _ClassifiedPublicError(
        message,
        fields["failure_stage"],
        fields["failure_code"],
        failure_reason=fields.get("failure_reason"),
        provider_status=fields.get("provider_status"),
        remote_error_code=getattr(error, "remote_error_code", None),
    )
    _clear_transient_exception_metadata(fresh_error)
    return fresh_error


def _postprocess_public_failure(error: BaseException | None = None) -> _ClassifiedPublicError:
    reason: str | None = None
    status: int | None = None
    remote_error_code: str | None = None
    if type(error) is PostProcessError:
        try:
            remote_error_code = getattr(error, "error_code", None)
            reason, status = remote_http.validate_failure_metadata(
                getattr(error, "reason", None),
                getattr(error, "status", None),
                error_code=remote_error_code,
            )
        except ValueError:
            reason = None
            status = None
            remote_error_code = None
    message = postprocess_public_failure_message(reason) or TRANSIENT_POSTPROCESS_ERROR
    return _ClassifiedPublicError(
        message,
        "postprocess",
        "SOC-P001",
        failure_reason=reason,
        provider_status=status,
        remote_error_code=remote_error_code,
    )


def _output_public_failure(*, no_side_effect: bool) -> _ClassifiedPublicError:
    if no_side_effect:
        return _ClassifiedPublicError(
            TRANSIENT_TRANSCRIPT_INSERT_ERROR,
            "output",
            "SOC-O001",
        )
    return _ClassifiedPublicError(
        TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR,
        "output",
        "SOC-O002",
    )


def _is_retryable_transcript_insert_error(value: object) -> bool:
    return type(value) is str and value == TRANSIENT_TRANSCRIPT_INSERT_ERROR


def _is_transcript_insert_recovery_error(value: object) -> bool:
    return type(value) is str and value in {
        LEGACY_TRANSIENT_TRANSCRIPT_INSERT_ERROR,
        TRANSIENT_TRANSCRIPT_INSERT_ERROR,
    }
EMPTY_TRANSCRIPT_MARKERS = frozenset(
    {
        "leere aufnahme",
        "leerer text",
        "keine transkription",
        "keine sprache erkannt",
        "empty recording",
        "empty transcript",
        "no transcript",
        "no speech detected",
    }
)
MAX_HISTORY_LIMIT = 1_000
MAX_TRANSCRIPT_HISTORY_SCAN = 10_000
MAX_TRANSCRIPT_HISTORY_SCAN_CHARS = 64 * 1024 * 1024
MAX_DIRECTORY_SCAN_ENTRIES = 100_000
DEFAULT_MAX_SECONDS = 30
MAX_KEEP_TRANSCRIPTS = 1_000
MAX_KEEP_RECORDINGS = 1_000
MAX_RECORDING_MAX_AGE_DAYS = 3_650
MAX_TYPING_DELAY_MS = 10_000
DEFAULT_TYPING_DELAY_MS = 8
MAX_PATH_CHARS = 240
MAX_TRANSCRIBER_TEXT_CHARS = 65_535
MAX_SETTINGS_JSON_CHARS = 250_000
MAX_SETTINGS_FILE_BYTES = 1_000_000
MAX_DIAGNOSTICS_JSON_BYTES = 1_000_000
MAX_FINALIZATION_LOCK_BYTES = 1_024
MAX_PROC_STAT_BYTES = 64 * 1024
MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS = 300
MAX_URL_CHARS = 2_048
MAX_ALARM_CATCH_UP_MINUTES = 14_400
MAX_RECORDING_ARTIFACT_CANDIDATES = 100
_MAX_CLEANUP_CLAIM_FILE_BYTES = 1 << 30
_CLEANUP_CLAIM_HASH_CHUNK_BYTES = 1 << 20
_MAX_CLEANUP_CLAIM_HASH_INTERRUPTS = 128
_MAX_CLEANUP_TREE_SCAN_ENTRIES = 4_096
_MAX_CLEANUP_TREE_SCAN_DEPTH = 32
_CLEANUP_CLAIM_NONCE_HEX_CHARS = 32
_CLEANUP_CLAIM_COMMIT_MAGIC = b"SOC-CLEANUP-V3-COMMIT\x00"
_CLEANUP_CLAIM_COMMIT_SIZE_HEX_BYTES = 16
_CLEANUP_CLAIM_AUTHORIZATION_SUFFIX = b"2"
DEFAULT_BENCHMARK_LANGUAGE = "de"
OLLAMA_PULL_TIMEOUT_SECONDS = 1800
TRANSCRIBER_CHOICES = [
    "auto",
    "openai",
    "openai-whisper",
    "whisper",
    "whisper-cpp",
    "faster-whisper",
    "openai-compatible",
    "openai-compatible-api",
    "external-api",
    "command",
    "custom",
    "template",
]
_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BASE_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
}
_DANGEROUS_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")
_DANGEROUS_ENV_KEYS = {
    "ENV",
    "PWD",
    "OLDPWD",
    "CDPATH",
    "PS4",
    "BASH_XTRACEFD",
    "SHELLOPTS",
    "PROMPT_COMMAND",
    "IFS",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "BASH_ENV",
}


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


def _required_nonblocking_flag() -> int:
    flag = getattr(os, "O_NONBLOCK", None)
    if type(flag) is not int or flag <= 0:
        raise OSError("secure nonblocking file open is not supported on this platform")
    return flag


_FINALIZATION_LOCK_PID_UNKNOWN = object()
_FINALIZATION_LOCK_PID_EMPTY = object()
_FINALIZATION_LOCK_PID_CORRUPT = object()
_FINALIZATION_LOCK_SCOPE_MISSING = object()
_FINALIZATION_LOCK_SCOPE_CORRUPT = object()
_FINALIZATION_LOCK_SCOPE_UNKNOWN = object()
_FINALIZATION_LOCK_SCOPE_UNIT_MISSING = object()
_FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT = object()
_FINALIZATION_LOCK_SCOPE_UNIT_UNKNOWN = object()
_FINALIZATION_LOCK_OWNER_SNAPSHOTS: dict[
    Path,
    tuple[int, str | None, os.stat_result],
] = {}


def _finalization_lock_path(state_path: Path) -> Path:
    return Path(state_path).with_name(f".{state_path.name}.finalizing")


def _read_finalization_lock_pid_state(lock_path: Path) -> object:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        return _FINALIZATION_LOCK_PID_UNKNOWN
    try:
        nonblock_flag = _required_nonblocking_flag()
    except OSError:
        return _FINALIZATION_LOCK_PID_UNKNOWN
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    try:
        assert_no_symlink_ancestors(lock_path, field_name="finalization lock")
        fd = os.open(str(lock_path), os.O_RDONLY | nofollow_flag | nonblock_flag | cloexec_flag)
        assert_fd_is_regular_private_file(fd, field_name="finalization lock", require_private_mode=True)
        handle = os.fdopen(fd, "rb")
        fd = None
        read_error: BaseException | None = None
        try:
            raw = handle.read(512)
        except BaseException as exc:
            read_error = exc
            raise
        finally:
            try:
                handle.close()
            except BaseException:
                if read_error is not None:
                    read_error.add_note("finalization lock cleanup failed")
                else:
                    raise
    except (OSError, RuntimeError, ValueError):
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        return _FINALIZATION_LOCK_PID_UNKNOWN
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        raise
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return _FINALIZATION_LOCK_PID_CORRUPT
    text = text.splitlines()
    if not text:
        return _FINALIZATION_LOCK_PID_EMPTY
    first = text[0].strip()
    if not first.isdigit():
        return _FINALIZATION_LOCK_PID_CORRUPT
    pid = int(first)
    return pid if pid > 0 else _FINALIZATION_LOCK_PID_CORRUPT


def _read_finalization_lock_pid(lock_path: Path) -> int | None:
    state = _read_finalization_lock_pid_state(lock_path)
    return None if state is _FINALIZATION_LOCK_PID_UNKNOWN else state


def _finalization_lock_identity_for_pid(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        raw = _read_proc_stat(pid)
    except OSError:
        return None
    try:
        close = raw.rindex(")")
        rest = raw[close + 2 :].split()
    except ValueError:
        return None
    if len(rest) < 20:
        return None
    try:
        boot_id = _read_proc_boot_id()
    except OSError:
        return None
    start_time = rest[19]
    if not boot_id or not start_time:
        return None
    return f"{boot_id}:{start_time}"


def _finalization_lock_process_start_time_for_pid(pid: int) -> float | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        raw = _read_proc_stat(pid)
        close = raw.rindex(")")
        rest = raw[close + 2 :].split()
        if len(rest) < 20:
            return None
        start_time_ticks = int(rest[19])
        with Path("/proc/stat").open("r", encoding="ascii") as handle:
            proc_stat = handle.read(MAX_PROC_STAT_BYTES)
        boot_time = next(
            int(line.split()[1])
            for line in proc_stat.split("\n")
            if line.startswith("btime ")
        )
        clock_ticks = os.sysconf("SC_CLK_TCK")
    except (OSError, UnicodeError, ValueError, IndexError, StopIteration, TypeError):
        return None
    if start_time_ticks <= 0 or not isinstance(clock_ticks, int) or isinstance(clock_ticks, bool):
        return None
    if clock_ticks <= 0:
        return None
    return float(boot_time) + (float(start_time_ticks) / float(clock_ticks))


def _finalization_lock_pid_started_after_lock(
    pid: int,
    lock_mtime: float,
    *,
    grace_seconds: float = 1.0,
) -> bool | None:
    process_start_time = _finalization_lock_process_start_time_for_pid(pid)
    if process_start_time is None:
        return None
    try:
        return process_start_time >= float(lock_mtime) + float(grace_seconds)
    except (TypeError, ValueError, OverflowError):
        return None


def _finalization_lock_owner_reused(
    owner_pid: int | None,
    identity_relation: str,
    *,
    has_liveness_anchor: bool,
) -> bool:
    return (
        owner_pid is not None
        and has_liveness_anchor
        and identity_relation == _PROCESS_IDENTITY_REUSED
    )


def _process_is_zombie(pid: int) -> bool | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        raw = _read_proc_stat(pid)
        close = raw.rindex(")")
        rest = raw[close + 2 :].split()
    except (OSError, UnicodeError, ValueError, IndexError):
        return None
    return bool(rest and rest[0] in {"Z", "X", "x"})


def _process_is_running(pid: int) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return True
    return _process_is_zombie(pid) is not True


def _read_finalization_lock_identity(lock_path: Path) -> str | None:
    try:
        raw = read_text_without_following_symlinks(
            lock_path,
            field_name="finalization lock",
            max_bytes=MAX_FINALIZATION_LOCK_BYTES,
            require_private_mode=True,
        )
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None
    lines = raw.splitlines()
    if len(lines) < 2:
        return None
    identity = lines[1].strip()
    if len(lines) == 2 and _validated_finalization_lock_scope(identity) is not None:
        return None
    return identity or None


def _read_finalization_lock_scope_state(lock_path: Path) -> object:
    try:
        raw = read_text_without_following_symlinks(
            lock_path,
            field_name="finalization lock",
            max_bytes=MAX_FINALIZATION_LOCK_BYTES,
            require_private_mode=True,
            encoding="ascii",
        )
    except UnicodeDecodeError:
        return _FINALIZATION_LOCK_SCOPE_CORRUPT
    except (OSError, RuntimeError, TypeError):
        return _FINALIZATION_LOCK_SCOPE_UNKNOWN
    lines = raw.splitlines()
    if len(lines) == 4:
        if not lines[2]:
            return _FINALIZATION_LOCK_SCOPE_MISSING
    if len(lines) == 2:
        legacy_scope = _validated_finalization_lock_scope(lines[1])
        if legacy_scope is not None:
            return legacy_scope
    if len(lines) < 3:
        return _FINALIZATION_LOCK_SCOPE_MISSING
    if len(lines) not in {3, 4}:
        return _FINALIZATION_LOCK_SCOPE_CORRUPT
    scope = lines[2]
    if _validated_finalization_lock_scope(scope) != scope:
        return _FINALIZATION_LOCK_SCOPE_CORRUPT
    return scope


def _read_finalization_lock_scope_unit_state(lock_path: Path) -> object:
    try:
        raw = read_text_without_following_symlinks(
            lock_path,
            field_name="finalization lock",
            max_bytes=MAX_FINALIZATION_LOCK_BYTES,
            require_private_mode=True,
            encoding="ascii",
        )
    except UnicodeDecodeError:
        return _FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT
    except (OSError, RuntimeError, TypeError):
        return _FINALIZATION_LOCK_SCOPE_UNIT_UNKNOWN
    lines = raw.splitlines()
    if len(lines) < 4:
        return _FINALIZATION_LOCK_SCOPE_UNIT_MISSING
    if len(lines) != 4:
        return _FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT
    scope_unit = lines[3]
    if not _recorder_scope_unit_is_soc_recorder_unit(scope_unit):
        return _FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT
    scope = _validated_finalization_lock_scope(lines[2])
    if scope is not None and not _recorder_scope_matches_unit(scope, scope_unit):
        return _FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT
    return scope_unit


def _validated_finalization_lock_scope(value: object) -> str | None:
    if not isinstance(value, str) or isinstance(value, bool) or not value:
        return None
    identity = parse_priority_scope_identity(value)
    if identity is None:
        return None
    scope_path = Path(identity.path)
    if (
        not scope_path.is_absolute()
        or scope_path != Path(os.path.normpath(identity.path))
        or not _recorder_scope_is_soc_recorder_unit(value)
    ):
        return None
    serialized = serialize_priority_scope_identity(identity)
    return serialized if serialized == value else None


def _write_all(fd: int, payload: bytes, *, field_name: str) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(fd, view[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(f"short write to {field_name}")
        offset += written


def _fsync_fd(fd: int) -> None:
    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            continue


def _read_bounded_fd(fd: int, max_bytes: int, *, field_name: str) -> bytes:
    payload = bytearray()
    while len(payload) <= max_bytes:
        try:
            chunk = os.pread(fd, min(64 * 1024, max_bytes + 1 - len(payload)), len(payload))
        except InterruptedError:
            continue
        if not chunk:
            break
        payload.extend(chunk)
    if len(payload) > max_bytes:
        raise OSError(f"{field_name} exceeds safe size limit")
    return bytes(payload)


def _finalization_lock_owner_from_fd(fd: int) -> tuple[int, str | None] | None:
    try:
        text = _read_bounded_fd(
            fd,
            MAX_FINALIZATION_LOCK_BYTES,
            field_name="finalization lock",
        ).decode("ascii")
    except (OSError, UnicodeDecodeError):
        return None
    lines = text.splitlines()
    if not lines or not lines[0].strip().isdigit():
        return None
    owner_pid = int(lines[0].strip())
    if owner_pid <= 0:
        return None
    owner_identity = lines[1].strip() if len(lines) > 1 else ""
    return owner_pid, owner_identity or None


def _same_finalization_lock_snapshot(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    try:
        first_snapshot = (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            first.st_nlink,
            first.st_uid,
            first.st_gid,
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
        )
        second_snapshot = (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            second.st_nlink,
            second.st_uid,
            second.st_gid,
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
        )
    except AttributeError:
        return False
    return first_snapshot == second_snapshot


def _same_finalization_lock_identity(current: os.stat_result, expected: os.stat_result) -> bool:
    return _same_finalization_lock_snapshot(current, expected)


def _finalization_lock_snapshot_key(lock_path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(lock_path)))


def _remember_finalization_lock_owner(
    lock_path: Path,
    owner_identity: str | None,
    lock_stat: os.stat_result,
) -> None:
    _FINALIZATION_LOCK_OWNER_SNAPSHOTS[_finalization_lock_snapshot_key(lock_path)] = (
        os.getpid(),
        owner_identity,
        lock_stat,
    )


def _forget_finalization_lock_owner(lock_path: Path) -> None:
    _FINALIZATION_LOCK_OWNER_SNAPSHOTS.pop(_finalization_lock_snapshot_key(lock_path), None)


def _finalization_lock_is_owned_by_current_process(
    lock_path: Path,
    lock_stat: os.stat_result,
    owner_pid: int | None,
    owner_identity: str | None,
) -> bool:
    if owner_pid != os.getpid():
        return False
    current_identity = _finalization_lock_identity_for_pid(os.getpid())
    if current_identity is not None and owner_identity is not None:
        return (
            _recording_process_identity_relation(
                owner_pid,
                owner_identity,
                current_identity,
            )
            == _PROCESS_IDENTITY_SAME
        )
    snapshot = _FINALIZATION_LOCK_OWNER_SNAPSHOTS.get(
        _finalization_lock_snapshot_key(lock_path)
    )
    return bool(
        snapshot
        and snapshot[0] == owner_pid
        and snapshot[1] == owner_identity
        and _same_finalization_lock_snapshot(snapshot[2], lock_stat)
    )


def _finalization_lock_owner_and_group_stably_absent(pid: int) -> bool:
    for attempt in range(2):
        if _process_is_running(pid) or process_group_has_live_processes(pid) is not False:
            return False
        if attempt == 0:
            time.sleep(RECORDER_PROCESS_RECONCILIATION_DELAY_SECONDS)
    return True


def _unlink_finalization_lock_at(
    parent_fd: int,
    lock_path: Path,
    *,
    expected_stat: os.stat_result | None = None,
) -> bool:
    source_fd: int | None = None
    operation_error: BaseException | None = None
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
            return False
        try:
            nonblock_flag = _required_nonblocking_flag()
        except OSError:
            return False
        try:
            source_fd = os.open(
                lock_path.name,
                os.O_RDONLY | nofollow_flag | nonblock_flag | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            source_stat = os.fstat(source_fd)
            current = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except (FileNotFoundError, OSError, ValueError):
            return False
        if (
            not stat_module.S_ISREG(source_stat.st_mode)
            or source_stat.st_nlink != 1
            or not _same_finalization_lock_snapshot(source_stat, current)
            or (expected_stat is not None and not _same_finalization_lock_snapshot(source_stat, expected_stat))
        ):
            return False
        for _ in range(100):
            cleanup_name = f"{lock_path.name}.{secrets.token_hex(8)}.cleanup"
            try:
                preclaim_stat = os.fstat(source_fd)
                path_stat = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat_module.S_ISREG(preclaim_stat.st_mode)
                    or preclaim_stat.st_nlink != 1
                    or not _same_finalization_lock_snapshot(preclaim_stat, path_stat)
                    or (expected_stat is not None and not _same_finalization_lock_snapshot(preclaim_stat, expected_stat))
                ):
                    return False
                _rename_without_replacing(
                    lock_path.name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name="finalization lock cleanup",
                )
            except FileExistsError:
                continue
            except (OSError, ValueError):
                return False

            claim_verified = False
            unlinked = False
            claimed: os.stat_result | None = None
            try:
                claimed = os.fstat(source_fd)
                claimed_path = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat_module.S_ISREG(claimed.st_mode)
                    or claimed.st_nlink != 1
                    or not _same_finalization_lock_snapshot(claimed_path, claimed)
                ):
                    raise RuntimeError("finalization lock changed before cleanup")
                claim_verified = True

                final_claim = os.fstat(source_fd)
                final_path = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not _same_finalization_lock_snapshot(final_claim, claimed) or not _same_finalization_lock_snapshot(
                    final_path, final_claim
                ):
                    raise RuntimeError("finalization lock changed before cleanup")
                # ponytail: Python has no unlink-by-fd primitive. The private same-UID
                # namespace remains the trust boundary; remove it with a separate-UID broker.
                os.unlink(cleanup_name, dir_fd=parent_fd)
                unlinked = True
                _fsync_fd(parent_fd)
            except BaseException as exc:
                if not unlinked and claim_verified and claimed is not None:
                    try:
                        restore_stat = os.fstat(source_fd)
                        restore_path = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                        if _same_finalization_lock_snapshot(restore_stat, claimed) and _same_finalization_lock_snapshot(
                            restore_path, restore_stat
                        ):
                            _rename_without_replacing(
                                cleanup_name,
                                lock_path.name,
                                directory_fd=parent_fd,
                                field_name="finalization lock cleanup restore",
                            )
                            _fsync_fd(parent_fd)
                    except BaseException:
                        exc.add_note("finalization lock cleanup restore failed")
                if isinstance(exc, RuntimeError) and str(exc) == "finalization lock changed before cleanup":
                    return False
                raise
            return True
        return False
    except BaseException as exc:
        operation_error = exc
        raise
    finally:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except BaseException:
                if operation_error is None:
                    raise
                try:
                    operation_error.add_note("finalization lock cleanup failed")
                except BaseException:
                    pass


def _acquire_finalization_lock(state_path: Path) -> Path | None:
    lock_path = _finalization_lock_path(state_path)
    acquired_path: Path | None = None
    cleanup_failed = False
    try:
        assert_no_symlink_ancestors(lock_path, field_name="finalization lock path")
    except RuntimeError:
        return None
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        return None
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            lock_path.parent,
            field_name="finalization lock directory",
        )
    except OSError:
        return None
    try:
        for _attempt in range(2):
            now = time.time()
            created_stat: os.stat_result | None = None
            fd: int | None = None
            try:
                fd = os.open(
                    lock_path.name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow_flag | cloexec_flag,
                    0o600,
                    dir_fd=parent_fd,
                )
                created_stat = os.fstat(fd)
            except FileExistsError:
                try:
                    existing = lock_path.lstat()
                except OSError:
                    return None
                if not stat_module.S_ISREG(existing.st_mode):
                    return None
                if getattr(existing, "st_nlink", 1) != 1:
                    return None
                try:
                    _required_nonblocking_flag()
                except OSError:
                    return None
                owner_state = _read_finalization_lock_pid(lock_path)
                if owner_state is None:
                    return None
                corrupt_owner_pid = owner_state is _FINALIZATION_LOCK_PID_CORRUPT
                if owner_state in {_FINALIZATION_LOCK_PID_EMPTY, _FINALIZATION_LOCK_PID_CORRUPT}:
                    owner_pid = None
                else:
                    owner_pid = owner_state
                scope_state = _read_finalization_lock_scope_state(lock_path)
                if scope_state is _FINALIZATION_LOCK_SCOPE_UNKNOWN:
                    return None
                scope_unit_state = _read_finalization_lock_scope_unit_state(lock_path)
                if scope_unit_state is _FINALIZATION_LOCK_SCOPE_UNIT_UNKNOWN:
                    return None
                corrupt_scope = scope_state is _FINALIZATION_LOCK_SCOPE_CORRUPT
                corrupt_scope_unit = (
                    scope_unit_state is _FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT
                )
                corrupt_metadata = corrupt_scope or corrupt_scope_unit
                has_liveness_anchor = isinstance(scope_state, str) or isinstance(
                    scope_unit_state,
                    str,
                )
                if isinstance(scope_state, str):
                    scope_gone = _recorder_scope_is_stably_gone(scope_state)
                    if scope_gone is not True:
                        return None
                owner_identity = _read_finalization_lock_identity(lock_path)
                owner_current_identity = (
                    _finalization_lock_identity_for_pid(owner_pid)
                    if owner_identity is not None and owner_pid is not None
                    else None
                )
                owner_identity_relation = (
                    _recording_process_identity_relation(
                        owner_pid,
                        owner_identity,
                        owner_current_identity,
                    )
                    if owner_pid is not None
                    else _PROCESS_IDENTITY_UNKNOWN
                )
                owner_reused = _finalization_lock_owner_reused(
                    owner_pid,
                    owner_identity_relation,
                    has_liveness_anchor=has_liveness_anchor,
                )
                if not owner_reused:
                    return None
                if corrupt_owner_pid or corrupt_metadata:
                    if (
                        not has_liveness_anchor
                        or now - existing.st_mtime <= MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS
                    ):
                        return None
                if isinstance(scope_unit_state, str) and _recorder_scope_unit_is_stably_gone(
                    scope_unit_state,
                    expected_scope=scope_state if isinstance(scope_state, str) else None,
                ) is not True:
                    return None
                try:
                    current = lock_path.lstat()
                except OSError:
                    return None
                if getattr(current, "st_nlink", 1) != 1:
                    return None
                if not _same_finalization_lock_snapshot(existing, current):
                    return None
                try:
                    if not _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=current):
                        return None
                except OSError:
                    return None
                continue
            except OSError:
                if fd is not None:
                    try:
                        os.close(fd)
                    except BaseException:
                        pass
                    if created_stat is not None:
                        try:
                            _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                        except BaseException:
                            pass
                return None
            except BaseException:
                if fd is not None:
                    try:
                        os.close(fd)
                    except BaseException:
                        pass
                    if created_stat is not None:
                        try:
                            _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                        except BaseException:
                            pass
                raise

            identity: str | None = None
            written_stat: os.stat_result | None = None
            try:
                identity = _finalization_lock_identity_for_pid(os.getpid())
                if identity is None:
                    _write_all(fd, f"{os.getpid()}\n".encode("ascii"), field_name="finalization lock")
                else:
                    _write_all(fd, f"{os.getpid()}\n{identity}\n".encode("ascii"), field_name="finalization lock")
                _fsync_fd(fd)
                written_stat = os.fstat(fd)
            except OSError:
                try:
                    os.close(fd)
                except BaseException:
                    pass
                try:
                    _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                except BaseException:
                    pass
                return None
            except BaseException:
                try:
                    os.close(fd)
                except BaseException:
                    pass
                try:
                    _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                except BaseException:
                    pass
                raise
            try:
                os.close(fd)
            except OSError:
                try:
                    _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                except BaseException:
                    pass
                return None
            except BaseException:
                try:
                    _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                except BaseException:
                    pass
                raise
            try:
                owned_stat = lock_path.lstat()
                if (
                    written_stat is None
                    or not stat_module.S_ISREG(owned_stat.st_mode)
                    or getattr(owned_stat, "st_nlink", 1) != 1
                    or not _same_finalization_lock_snapshot(written_stat, owned_stat)
                ):
                    raise OSError("finalization lock changed during acquisition")
                _remember_finalization_lock_owner(lock_path, identity, owned_stat)
            except OSError:
                try:
                    _unlink_finalization_lock_at(
                        parent_fd,
                        lock_path,
                        expected_stat=written_stat,
                    )
                except BaseException:
                    pass
                return None
            acquired_path = lock_path
            break
        if acquired_path is None:
            return None
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            if acquired_path is not None:
                try:
                    _release_finalization_lock(acquired_path)
                except BaseException:
                    pass
            cleanup_failed = True
        except BaseException:
            if created_stat is not None:
                try:
                    _release_finalization_lock(lock_path)
                except BaseException:
                    pass
            raise
    if cleanup_failed:
        return None
    return acquired_path


def _release_finalization_lock(lock_path: Path | None) -> None:
    if not lock_path:
        return
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            lock_path.parent,
            field_name="finalization lock directory",
        )
    except OSError:
        return
    try:
        try:
            current = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
            owner_pid = _read_finalization_lock_pid(lock_path)
            if owner_pid in {_FINALIZATION_LOCK_PID_EMPTY, _FINALIZATION_LOCK_PID_CORRUPT}:
                owner_pid = None
            owner_identity = _read_finalization_lock_identity(lock_path)
            if not _finalization_lock_is_owned_by_current_process(
                lock_path,
                current,
                owner_pid,
                owner_identity,
            ):
                return
            if _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=current):
                _forget_finalization_lock_owner(lock_path)
        except BaseException:
            pass
    finally:
        try:
            os.close(parent_fd)
        except BaseException:
            pass


def _retain_finalization_lock_for_process(
    lock_path: Path | None,
    pid: int,
    process_identity: str | None = None,
    *,
    recorder_scope: str | None = None,
    recorder_scope_unit: str | None = None,
) -> bool:
    if lock_path is None or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if (
        not isinstance(process_identity, str)
        or isinstance(process_identity, bool)
        or _recording_process_identity_relation(pid, process_identity, process_identity)
        != _PROCESS_IDENTITY_SAME
    ):
        return False
    if recorder_scope is not None:
        recorder_scope = _validated_finalization_lock_scope(recorder_scope)
        if recorder_scope is None:
            return False
    if recorder_scope_unit is not None:
        if not _recorder_scope_unit_is_soc_recorder_unit(recorder_scope_unit):
            return False
        if recorder_scope is not None and not _recorder_scope_matches_unit(
            recorder_scope,
            recorder_scope_unit,
        ):
            return False
    if recorder_scope is not None:
        scope_identity = parse_priority_scope_identity(recorder_scope)
        if scope_identity is None or not verify_priority_scope_identity(
            scope_identity,
            cpu_weight=SOC_CPU_WEIGHT,
            io_weight=SOC_IO_WEIGHT,
        ):
            return False
    parent_fd: int | None = None
    lock_fd: int | None = None
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            lock_path.parent,
            field_name="finalization lock directory",
        )
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
        ):
            return False
        lock_fd = os.open(
            lock_path.name,
            os.O_RDWR
            | nofollow_flag
            | _required_nonblocking_flag()
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(lock_fd)
        path_stat = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat_module.S_ISREG(opened_stat.st_mode)
            or getattr(opened_stat, "st_nlink", 1) != 1
            or not _same_finalization_lock_snapshot(opened_stat, path_stat)
        ):
            return False
        owner_snapshot = _FINALIZATION_LOCK_OWNER_SNAPSHOTS.get(
            _finalization_lock_snapshot_key(lock_path)
        )
        current_pid = os.getpid()
        current_identity = _finalization_lock_identity_for_pid(current_pid)
        owner = _finalization_lock_owner_from_fd(lock_fd)
        if (
            owner_snapshot is None
            or owner_snapshot[0] != current_pid
            or _recording_process_identity_relation(
                current_pid,
                owner_snapshot[1],
                current_identity,
            )
            != _PROCESS_IDENTITY_SAME
            or not _same_finalization_lock_snapshot(owner_snapshot[2], opened_stat)
            or owner is None
            or owner[0] != current_pid
            or _recording_process_identity_relation(
                current_pid,
                owner[1],
                current_identity,
            )
            != _PROCESS_IDENTITY_SAME
        ):
            return False
        target_identity, target_identity_status = _recording_process_identity_probe(pid)
        if (
            target_identity_status != _RECORDING_PROCESS_IDENTITY_PRESENT
            or _recording_process_identity_relation(
                pid,
                process_identity,
                target_identity,
            )
            != _PROCESS_IDENTITY_SAME
        ):
            return False
        if recorder_scope is not None and not _recorder_scope_is_current(pid, recorder_scope):
            return False
        payload = f"{pid}\n{process_identity or ''}\n"
        if recorder_scope_unit is not None:
            payload += f"{recorder_scope or ''}\n{recorder_scope_unit}\n"
        elif recorder_scope is not None:
            payload += f"{recorder_scope}\n"
        payload_bytes = payload.encode("ascii")
        prewrite_stat = os.fstat(lock_fd)
        prewrite_path_stat = os.stat(
            lock_path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not _same_finalization_lock_snapshot(opened_stat, prewrite_stat)
            or not _same_finalization_lock_snapshot(prewrite_stat, prewrite_path_stat)
        ):
            return False
        os.ftruncate(lock_fd, 0)
        os.lseek(lock_fd, 0, os.SEEK_SET)
        _write_all(lock_fd, payload_bytes, field_name="finalization lock")
        _fsync_fd(lock_fd)
        retained_stat = os.fstat(lock_fd)
        retained_payload = _read_bounded_fd(
            lock_fd,
            MAX_FINALIZATION_LOCK_BYTES,
            field_name="finalization lock",
        )
        final_fd_stat = os.fstat(lock_fd)
        retained_path_stat = os.stat(
            lock_path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat_module.S_ISREG(retained_stat.st_mode)
            or getattr(retained_stat, "st_nlink", 1) != 1
            or not _same_finalization_lock_snapshot(retained_stat, final_fd_stat)
            or not _same_finalization_lock_snapshot(final_fd_stat, retained_path_stat)
            or retained_payload != payload_bytes
        ):
            return False
        _forget_finalization_lock_owner(lock_path)
        return True
    except (OSError, RuntimeError, UnicodeError, ValueError):
        return False
    finally:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except BaseException:
                pass
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except BaseException:
                pass


def _is_unsafe_env_var(name: str) -> bool:
    return name in _DANGEROUS_ENV_KEYS or name.startswith(_DANGEROUS_ENV_PREFIXES)


def _coerce_environment_value(name: str) -> str | None:
    if isinstance(name, bool) or not isinstance(name, str):
        return None
    try:
        value = os.environ.__getitem__(name)
    except KeyError:
        return None
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return None
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _filtered_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = _coerce_environment_value(key)
        if value is not None:
            env[key] = value
    if base is not None:
        if not isinstance(base, dict):
            raise RuntimeError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise RuntimeError("environment keys must be text")
            if isinstance(value, bool):
                raise RuntimeError("environment values must be text")
            if not isinstance(value, str):
                raise RuntimeError("environment base must be a mapping")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise RuntimeError("environment key contains invalid control character")
            if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
                raise RuntimeError("environment value contains invalid control character")
            try:
                key.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise RuntimeError("environment key contains invalid UTF-8") from exc
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise RuntimeError("environment value contains invalid UTF-8") from exc
            if _is_unsafe_env_var(key):
                raise RuntimeError(f"environment key is not allowed: {key}")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


_ESCAPED_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    if _ESCAPED_CONTROL_RE.search(lowered):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _coerce_log_level_from_environment() -> str:
    level = _coerce_environment_value("SPEED_OF_CINNAMON_LOG_LEVEL") or DEFAULT_LOG_LEVEL
    if not isinstance(level, str) or isinstance(level, bool):
        return DEFAULT_LOG_LEVEL
    if _contains_http_header_control_chars(level):
        return DEFAULT_LOG_LEVEL
    cleaned = level.strip().lower()
    if not cleaned:
        return DEFAULT_LOG_LEVEL
    if cleaned in LOG_LEVELS:
        return cleaned
    return DEFAULT_LOG_LEVEL


def _coerce_desktop_payload() -> dict[str, str]:
    desktop = doctor._env_desktop()
    if not isinstance(desktop, dict):
        return {"current_desktop": "", "session_type": "", "desktop_session": ""}

    def _coerce_text(value: object) -> str:
        if not isinstance(value, str) or isinstance(value, bool):
            return ""
        return value

    return {
        "current_desktop": _coerce_text(desktop.get("current_desktop")),
        "session_type": _coerce_text(desktop.get("session_type")),
        "desktop_session": _coerce_text(desktop.get("desktop_session")),
    }


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise RuntimeError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise RuntimeError("command is empty")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise RuntimeError("command must be a bare command name without path separators")
    path = _which(command_name)
    if not path:
        raise RuntimeError(f"{command_name} command is not available")
    return path


def _assert_text_limit(value: str, *, field_name: str, max_chars: int) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError(f"{field_name} must be text")
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeError(f"{field_name} contains invalid UTF-8") from exc
    if len(value) > max_chars:
        if field_name == "audio file path":
            raise RuntimeError(f"{field_name} is too long (max {max_chars} characters)")
        raise RuntimeError(f"{field_name} is too large (max {max_chars} characters)")
    if len(encoded) > max_chars:
        if field_name == "audio file path":
            raise RuntimeError(f"{field_name} is too long (max {max_chars} bytes)")
        raise RuntimeError(f"{field_name} is too large (max {max_chars} bytes)")
    return value


def _assert_clean_text(value: str, *, field_name: str, max_chars: int) -> str:
    if _contains_escaped_null(value):
        raise RuntimeError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(value):
        raise RuntimeError(f"{field_name} contains invalid control character")
    return _assert_text_limit(value, field_name=field_name, max_chars=max_chars)


def _validate_text_model_url(url: str, *, field_name: str) -> str:
    if isinstance(url, bool) or not isinstance(url, str):
        return _assert_clean_text(url, field_name=field_name, max_chars=MAX_URL_CHARS)
    probe = url[: MAX_URL_CHARS + 1].strip(" ")
    if _contains_escaped_null(probe):
        raise RuntimeError(f"{field_name} contains invalid null byte")
    if has_unsafe_url_characters(probe) or _contains_http_header_control_chars(probe):
        raise RuntimeError(f"{field_name} contains invalid control character")
    try:
        probe.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeError(f"{field_name} contains invalid UTF-8") from exc
    if len(url) > MAX_URL_CHARS:
        raise RuntimeError(f"{field_name} is too large (max {MAX_URL_CHARS} characters)")
    try:
        raw_encoded = url.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeError(f"{field_name} contains invalid UTF-8") from exc
    if len(raw_encoded) > MAX_URL_CHARS:
        raise RuntimeError(f"{field_name} is too large (max {MAX_URL_CHARS} bytes)")
    normalized = url.strip(" ")
    return normalized.rstrip("/")


def _validate_ollama_http_url(url: str, *, field_name: str) -> str:
    base = _validate_text_model_url(url, field_name=field_name)
    try:
        parsed = urllib.parse.urlparse(base)
    except ValueError as exc:
        raise RuntimeError(f"{field_name} is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{field_name} must use http:// or https://")
    if not parsed.hostname:
        raise RuntimeError(f"{field_name} is missing hostname")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise RuntimeError(f"{field_name} must use https:// unless host is local loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{field_name} has invalid port") from exc
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f"{field_name} must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{field_name} must not contain query or fragment")
    return base


def _validate_openai_compatible_http_url(url: str, field_name: str) -> str:
    base = _validate_text_model_url(url, field_name=field_name)
    try:
        parsed = urllib.parse.urlparse(base)
    except ValueError as exc:
        raise RuntimeError(f"{field_name} is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{field_name} must use http:// or https://")
    if not parsed.hostname:
        raise RuntimeError(f"{field_name} is missing hostname")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise RuntimeError(f"{field_name} must use https:// unless host is local loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{field_name} has invalid port") from exc
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise RuntimeError(f"{field_name} must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{field_name} must not contain query or fragment")
    return base


def _is_local_ollama_url(url: str) -> bool:
    raw = url or DEFAULT_OLLAMA_URL
    if isinstance(raw, bool) or not isinstance(raw, str):
        return False
    if _contains_escaped_null(raw) or _contains_http_header_control_chars(raw):
        return False
    normalized = raw.strip().lower()
    try:
        parsed = urllib.parse.urlparse(normalized)
    except ValueError:
        return False
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        return False
    return is_loopback_hostname(parsed.hostname)


def _effective_post_process_backend(backend: str, command_template: str) -> str:
    raw = backend or "none"
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise RuntimeError("post-process backend must be text")
    if _contains_escaped_null(raw):
        raise RuntimeError("post-process backend contains invalid null byte")
    if _contains_http_header_control_chars(raw):
        raise RuntimeError("post-process backend contains invalid control character")
    normalized = raw.strip().lower().replace("_", "-")
    if normalized in {"none", "off", "disabled"} and (command_template or "").strip():
        return "command"
    return normalized


def _is_remote_post_process_backend(backend: str) -> bool:
    raw = backend or "none"
    if isinstance(raw, bool) or not isinstance(raw, str):
        return False
    if _contains_escaped_null(raw) or _contains_http_header_control_chars(raw):
        return False
    normalized = raw.strip().lower().replace("_", "-")
    return normalized in {"ollama", "openai-compatible", "openai"}


def _openai_compatible_transcribe_kwargs(args: argparse.Namespace, backend: str) -> dict[str, object]:
    if backend != "openai-compatible":
        return {}
    return {
        "openai_compatible_model": getattr(args, "openai_compatible_model", DEFAULT_OPENAI_COMPATIBLE_MODEL),
        "openai_compatible_url": getattr(args, "openai_compatible_url", DEFAULT_OPENAI_COMPATIBLE_URL),
        "openai_compatible_api_key": _openai_compatible_api_key_from_args(args),
        "openai_compatible_flex_processing": getattr(args, "openai_compatible_flex_processing", True),
        "openai_compatible_service_tier_fallback": True,
    }


def _openai_compatible_api_key_from_args(args: argparse.Namespace) -> str:
    cached = getattr(args, "_resolved_openai_compatible_api_key", None)
    if isinstance(cached, str):
        return cached
    raw = getattr(args, "openai_compatible_api_key", "")
    use_stdin = _coerce_bool(
        getattr(args, "openai_compatible_api_key_stdin", False),
        field_name="openai_compatible_api_key_stdin",
    )
    if use_stdin:
        if raw:
            raise RuntimeError("openai-compatible API key must be provided by either stdin or environment, not both")
        raw = sys.stdin.read(MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1).strip()
        if len(raw) > MAX_OPENAI_COMPATIBLE_API_KEY_CHARS:
            raise RuntimeError(
                f"openai-compatible API key is too large (max {MAX_OPENAI_COMPATIBLE_API_KEY_CHARS} characters)"
            )
    elif not raw:
        raw = (
            os.environ.get("OPENAI_COMPATIBLE_API_KEY", "")
            or os.environ.get("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY", "")
        )
    key = _assert_clean_text(
        raw,
        field_name="openai-compatible API key",
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    ).strip()
    try:
        setattr(args, "_resolved_openai_compatible_api_key", key)
    except (AttributeError, TypeError):
        pass
    return key


def _openai_compatible_post_process_model(args: argparse.Namespace) -> str:
    text_model = getattr(args, "openai_compatible_text_model", "")
    return text_model or DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL


_REMOTE_ERROR_MESSAGES = {
    "remote-request-invalid": "remote request was rejected",
    "remote-url-unsafe": "remote URL was rejected",
    "remote-dns-failed": "remote DNS lookup failed",
    "remote-connect-failed": "remote connection failed",
    "remote-http-failed": "remote HTTP request failed",
    "remote-response-too-large": "remote response was too large",
    "remote-response-invalid": "remote response was invalid",
    "remote-operation-failed": "remote operation failed",
    "remote-operation-timeout": "remote operation timed out",
    "remote-worker-unavailable": "remote worker was unavailable",
    "remote-worker-protocol-invalid": "remote worker protocol was invalid",
    "remote-worker-cancelled": "remote operation was cancelled",
    "remote-worker-cleanup-unconfirmed": "remote worker cleanup was not confirmed",
}
_REMOTE_ERROR_CODES = frozenset(_REMOTE_ERROR_MESSAGES)
_REMOTE_PUBLIC_SUCCESS_KEYS = frozenset({"nonce", "result", "schema_version", "status"})
_REMOTE_PUBLIC_ERROR_REQUIRED_KEYS = frozenset({"error_code", "nonce", "schema_version", "status"})
_REMOTE_PUBLIC_ERROR_KEYS = _REMOTE_PUBLIC_ERROR_REQUIRED_KEYS | frozenset(
    {"failure_reason", "provider_status"}
)


def _remote_request(
    operation: str,
    payload: dict[str, object],
    *,
    timeout_ns: int | None = None,
) -> dict[str, object]:
    if type(operation) is not str or operation not in remote_http.SUPPORTED_OPERATIONS:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    if type(payload) is not dict:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    nonce = secrets.token_hex(16)
    if type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    now = time.monotonic_ns()
    if type(now) is not int or isinstance(now, bool):
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    duration = timeout_ns
    if duration is None:
        duration = (
            remote_http.POSTPROCESS_DEADLINE_NS
            if operation
            in {
                remote_http.POSTPROCESS_OLLAMA_OPERATION,
                remote_http.POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
            }
            else remote_http.LISTING_DEADLINE_NS
        )
    if type(duration) is not int or duration <= 0:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    return {
        "deadline_monotonic_ns": now + duration,
        "nonce": nonce,
        "operation": operation,
        "payload": payload,
        "schema_version": remote_http.PROTOCOL_SCHEMA_VERSION,
    }


def _validate_remote_response(
    response: object,
    request: dict[str, object],
) -> dict[str, object]:
    try:
        if type(response) is not dict:
            raise ValueError
        operation = request["operation"]
        nonce = request["nonce"]
        status = response.get("status")
        keys = frozenset(response)
        if status == "ok" and keys != _REMOTE_PUBLIC_SUCCESS_KEYS:
            raise ValueError
        if status == "error" and (
            not _REMOTE_PUBLIC_ERROR_REQUIRED_KEYS.issubset(keys)
            or not keys.issubset(_REMOTE_PUBLIC_ERROR_KEYS)
        ):
            raise ValueError
        if status not in {"ok", "error"}:
            raise ValueError
        if (
            type(response["schema_version"]) is not int
            or response["schema_version"] != remote_http.PROTOCOL_SCHEMA_VERSION
            or type(response["nonce"]) is not str
            or response["nonce"] != nonce
        ):
            raise ValueError
        if status == "error":
            if type(response["error_code"]) is not str or response["error_code"] not in _REMOTE_ERROR_CODES:
                raise ValueError
            if "failure_reason" in response and response["failure_reason"] is None:
                raise ValueError
            remote_http.validate_failure_metadata(
                response.get("failure_reason"),
                response.get("provider_status"),
                error_code=response["error_code"],
            )
            return response
        result = response["result"]
        if type(result) is not dict:
            raise ValueError
        if operation in {
            remote_http.LIST_OLLAMA_MODELS_OPERATION,
            remote_http.LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        }:
            if frozenset(result) != frozenset({"listing_state", "models"}):
                raise ValueError
            if result["listing_state"] not in {"listed", "missing-model-list"}:
                raise ValueError
            models = result["models"]
            if type(models) is not list or len(models) > remote_http.MAX_MODEL_LIST_ENTRIES:
                raise ValueError
            if result["listing_state"] == "missing-model-list" and models:
                raise ValueError
            if any(type(model) is not dict for model in models):
                raise ValueError
        else:
            if frozenset(result) != frozenset({"text"}):
                raise ValueError
            text = result["text"]
            if (
                type(text) is not str
                or not text
                or len(text) > remote_http.MAX_POSTPROCESS_TEXT_CHARS
            ):
                raise ValueError
        return response
    except Exception:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-worker-protocol-invalid"]) from None


def _run_remote_operation(
    operation: str,
    payload: dict[str, object],
    *,
    timeout_ns: int | None = None,
) -> dict[str, object]:
    request = _remote_request(operation, payload, timeout_ns=timeout_ns)
    runners = {
        remote_http.LIST_OLLAMA_MODELS_OPERATION: remote_http.run_list_ollama_models,
        remote_http.LIST_OPENAI_COMPATIBLE_MODELS_OPERATION: remote_http.run_list_openai_compatible_models,
        remote_http.POSTPROCESS_OLLAMA_OPERATION: remote_http.run_postprocess_ollama,
        remote_http.POSTPROCESS_OPENAI_COMPATIBLE_OPERATION: remote_http.run_postprocess_openai_compatible,
    }
    runner = runners.get(operation)
    if runner is None:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    try:
        response = runner(request)
    except Exception:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-worker-protocol-invalid"]) from None
    return _validate_remote_response(response, request)


def _remote_listing_payload(
    operation: str,
    provider: str,
    url: str,
    api_key: str = "",
    *,
    timeout_ns: int,
) -> dict[str, object]:
    payload = {"url": url}
    if operation == remote_http.LIST_OPENAI_COMPATIBLE_MODELS_OPERATION:
        payload["api_key"] = api_key
    try:
        response = _run_remote_operation(operation, payload, timeout_ns=timeout_ns)
    except RuntimeError as error:
        return {"available": False, "models": [], "message": f"{provider}: {error}"}
    if response["status"] == "error":
        code = response["error_code"]
        return {
            "available": False,
            "models": [],
            "message": f"{provider}: {_REMOTE_ERROR_MESSAGES[code]}",
        }
    result = response["result"]
    state = result["listing_state"]
    models = list(result["models"])
    if state == "missing-model-list":
        message = (
            "Ollama is running but returned no model list"
            if provider == "Ollama"
            else "OpenAI-compatible API returned no model list"
        )
    elif models:
        message = f"{provider} models loaded"
    else:
        message = (
            "No local Ollama models found"
            if provider == "Ollama"
            else "No OpenAI-compatible text models found"
        )
    return {"available": True, "models": models, "message": message}


def _validate_listing_timeout(timeout: object) -> int:
    if type(timeout) not in {int, float}:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    if type(timeout) is float and not math.isfinite(timeout):
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    if timeout <= 0 or timeout > 5:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    timeout_ns = math.ceil(timeout * 1_000_000_000)
    if timeout_ns <= 0 or timeout_ns > remote_http.LISTING_DEADLINE_NS:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    return timeout_ns


def list_ollama_models(
    url: str = DEFAULT_OLLAMA_URL,
    timeout: int | float = 5,
) -> dict[str, object]:
    timeout_ns = _validate_listing_timeout(timeout)
    normalized_url = _validate_ollama_http_url(url, field_name="ollama url")
    return _remote_listing_payload(
        remote_http.LIST_OLLAMA_MODELS_OPERATION,
        "Ollama",
        normalized_url,
        timeout_ns=timeout_ns,
    )


def list_openai_compatible_models(
    url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    timeout: int | float = 5,
    api_key: str = "",
) -> dict[str, object]:
    timeout_ns = _validate_listing_timeout(timeout)
    normalized_url = _validate_openai_compatible_http_url(
        url,
        field_name="openai-compatible url",
    )
    if type(api_key) is not str:
        raise RuntimeError(_REMOTE_ERROR_MESSAGES["remote-request-invalid"])
    return _remote_listing_payload(
        remote_http.LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
        "OpenAI-compatible",
        normalized_url,
        api_key=api_key,
        timeout_ns=timeout_ns,
    )


def _remote_postprocess_text(
    operation: str,
    payload: dict[str, object],
) -> str:
    try:
        response = _run_remote_operation(operation, payload)
    except RuntimeError as error:
        raise PostProcessError(
            str(error),
            error_code="remote-worker-protocol-invalid",
            reason="worker_protocol",
        ) from None
    if response["status"] == "error":
        code = response["error_code"]
        raise PostProcessError(
            _REMOTE_ERROR_MESSAGES[code],
            error_code=code,
            reason=response.get("failure_reason"),
            status=response.get("provider_status"),
        )
    return response["result"]["text"]


def _validate_pipeline_text_args(
    args: argparse.Namespace,
    *,
    language: str,
) -> str:
    language = _assert_clean_text(language, field_name="language", max_chars=MAX_LANGUAGE_CODE_CHARS)
    _assert_clean_text(args.personal_context, field_name="personal context", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.vocabulary, field_name="vocabulary", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.transcriber_command, field_name="transcriber command", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.post_process_command, field_name="post-process command", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.whisper_model, field_name="whisper model", max_chars=MAX_PATH_CHARS)
    _assert_clean_text(args.post_process_prompt, field_name="post-process prompt", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    _assert_clean_text(args.ollama_model, field_name="ollama model", max_chars=MAX_PATH_CHARS)
    _assert_clean_text(args.openai_compatible_model, field_name="openai-compatible model", max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS)
    _assert_clean_text(getattr(args, "openai_compatible_text_model", ""), field_name="openai-compatible text model", max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS)
    _assert_clean_text(
        getattr(args, "openai_compatible_api_key", ""),
        field_name="openai-compatible API key",
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    )
    _coerce_bool(
        getattr(args, "openai_compatible_flex_processing", True),
        field_name="openai-compatible flex processing",
    )
    _coerce_bool(
        getattr(args, "openai_compatible_api_key_stdin", False),
        field_name="openai-compatible_api_key_stdin",
    )
    if getattr(args, "openai_compatible_api_key_stdin", False) or getattr(args, "openai_compatible_api_key", ""):
        _openai_compatible_api_key_from_args(args)
    _coerce_bool(getattr(args, "soften_profanity", False), field_name="soften_profanity")
    _validate_ollama_http_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
    _validate_openai_compatible_http_url(args.openai_compatible_url or DEFAULT_OPENAI_COMPATIBLE_URL, field_name="openai-compatible url")
    return language


def read_file_tail(path: Path, max_chars: int) -> str:
    if isinstance(path, str):
        path = Path(path)
    elif not isinstance(path, Path):
        raise TypeError("path must be a Path")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TypeError("max_chars must be an integer")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if max_chars > MAX_TRANSCRIPT_HISTORY_TEXT_CHARS:
        raise ValueError(f"max_chars must be at most {MAX_TRANSCRIPT_HISTORY_TEXT_CHARS}")
    path_text = str(path)
    if _contains_escaped_null(path_text):
        raise ValueError(f"file path contains invalid null byte: {path}")
    lowered_path = path_text.lower()
    control_codepoints = tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))
    if (
        any(sequence in lowered_path for sequence in ("\\a", "\\b", "\\f", "\\n", "\\r", "\\t", "\\v"))
        or any(f"\\x{codepoint:02x}" in lowered_path or f"\\u00{codepoint:02x}" in lowered_path for codepoint in control_codepoints)
        or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in path_text)
    ):
        raise ValueError(f"file path contains invalid control character: {path}")
    max_bytes = max_chars * 4
    try:
        assert_no_symlink_ancestors(path, field_name="file path")
    except RuntimeError as exc:
        raise OSError(str(exc)) from exc
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise OSError("secure file open is not supported on this platform")
    nonblock_flag = _required_nonblocking_flag()
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow_flag | nonblock_flag)
        assert_fd_is_regular_private_file(fd, field_name="file path")
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        raise OSError(str(exc)) from exc
    except RuntimeError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        raise OSError(str(exc)) from exc
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        raise
    try:
        handle = os.fdopen(fd, "rb")
        fd = None
    except (OSError, ValueError):
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        raise
    except BaseException:
        if fd is not None:
            try:
                os.close(fd)
            except BaseException:
                pass
        raise
    primary_error: BaseException | None = None
    try:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size <= max_bytes:
            handle.seek(0)
            raw = handle.read(size)
        else:
            # Read up to three extra bytes so byte slicing cannot start inside
            # a valid four-byte UTF-8 code point.
            tail_start = size - max_bytes
            read_start = max(tail_start - 3, 0)
            handle.seek(read_start)
            raw = handle.read(size - read_start)
            leading_continuations = 0
            while (
                leading_continuations < tail_start - read_start
                and leading_continuations < len(raw)
                and (raw[leading_continuations] & 0xC0) == 0x80
            ):
                leading_continuations += 1
            if leading_continuations:
                prefix_start = max(read_start - 3, 0)
                handle.seek(prefix_start)
                prefix = handle.read(read_start - prefix_start)
                combined = prefix + raw[:leading_continuations]
                crossing_sequence = False
                for candidate_start in range(max(0, len(prefix) - 3), len(prefix)):
                    candidate = combined[candidate_start:]
                    if len(candidate) <= len(prefix) - candidate_start:
                        continue
                    try:
                        candidate.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    crossing_sequence = True
                    break
                if crossing_sequence:
                    raw = raw[leading_continuations:]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"failed to decode file as UTF-8: {path}") from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            handle.close()
        except BaseException:
            if primary_error is not None:
                primary_error.add_note("file tail cleanup failed")
            else:
                raise
    if _contains_escaped_null(text):
        raise ValueError(f"file tail contains invalid null byte: {path}")
    if len(text) > max_chars:
        text = text[-max_chars:]
    return text


def _read_binary_output(file: io.BufferedRandom, max_bytes: int, *, field_name: str) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "tell") or not hasattr(file, "read"):
        raise RuntimeError(f"{field_name} must be a binary file handle")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise RuntimeError("max_bytes must be an integer")
    if max_bytes <= 0:
        raise RuntimeError("max_bytes must be positive")
    file.seek(0, os.SEEK_END)
    size = file.tell()
    if size > max_bytes:
        raise RuntimeError(f"{field_name} exceeded {max_bytes} bytes")
    file.seek(0)
    data = file.read(max_bytes + 1)
    if not isinstance(data, bytes):
        raise RuntimeError(f"{field_name} must return bytes")
    if len(data) > max_bytes:
        raise RuntimeError(f"{field_name} exceeded {max_bytes} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{field_name} is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise RuntimeError(f"{field_name} contains invalid null byte")
    return text


def _decode_binary_output(data: bytes, *, field_name: str) -> str:
    if not isinstance(data, bytes):
        raise RuntimeError(f"{field_name} must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{field_name} is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise RuntimeError(f"{field_name} contains invalid null byte")
    return text


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def print_result(payload: dict[str, object], json_output: bool) -> None:
    safe_payload = payload
    message = payload.get("message")
    if isinstance(message, str):
        safe_payload = dict(payload)
        safe_payload["message"] = _redact_error_for_user(message)
    if json_output:
        print(json.dumps(safe_payload, indent=2, sort_keys=True, allow_nan=False))
    else:
        status = safe_payload.get("status", "ok")
        message = safe_payload.get("message") or safe_payload.get("error") or status
        print(f"{APP_NAME}: {message}")


def _known_cli_secret_values(args: argparse.Namespace | None = None) -> tuple[str, ...]:
    values = [
        os.environ.get("OPENAI_COMPATIBLE_API_KEY", ""),
        os.environ.get("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY", ""),
    ]
    if args is not None:
        values.extend(
            getattr(args, attribute, "")
            for attribute in ("openai_compatible_api_key", "_resolved_openai_compatible_api_key")
        )
    return tuple(
        sorted(
            {value for value in values if type(value) is str and value},
            key=len,
            reverse=True,
        )
    )


def _redact_known_cli_secrets(text: str, secret_values: tuple[str, ...] = ()) -> str:
    for secret in (*_known_cli_secret_values(), *secret_values):
        if secret:
            text = text.replace(secret, "[redacted]")
    return text


def _redact_error_for_user(error: object, *, secret_values: tuple[str, ...] = ()) -> str:
    if isinstance(error, bool) or not isinstance(error, str):
        return "[invalid]"
    return _redact_known_cli_secrets(
        sanitize_error_message(error, max_chars=MAX_LOG_EXCERPT_CHARS),
        secret_values,
    )


def _clear_transient_exception_metadata(error: BaseException) -> BaseException:
    for attribute, value in (
        ("__cause__", None),
        ("__context__", None),
        ("__traceback__", None),
        ("__suppress_context__", True),
        ("__notes__", []),
    ):
        try:
            setattr(error, attribute, value)
        except BaseException:
            pass
    return error


def _sanitize_transient_exception(
    error: BaseException,
    *,
    message: str = TRANSIENT_TRANSCRIPT_WRITE_ERROR,
    system_exit_as_runtime: bool = False,
) -> BaseException:
    """Build safe native exception outside active exception handling."""
    if isinstance(error, KeyboardInterrupt):
        sanitized: BaseException = KeyboardInterrupt(TRANSIENT_TRANSCRIPT_INTERRUPT_ERROR)
    elif isinstance(error, SystemExit) and not system_exit_as_runtime:
        sanitized = SystemExit(1)
    else:
        sanitized = RuntimeError(message)
    return _clear_transient_exception_metadata(sanitized)


def _raise_sanitized_transient_exception(
    error: BaseException,
    *,
    message: str = TRANSIENT_TRANSCRIPT_WRITE_ERROR,
    system_exit_as_runtime: bool = False,
) -> NoReturn:
    """Raise a sanitized exception without inheriting an active exception chain."""
    sanitized = _sanitize_transient_exception(
        error,
        message=message,
        system_exit_as_runtime=system_exit_as_runtime,
    )
    try:
        raise sanitized from None
    except BaseException as raised:
        _clear_transient_exception_metadata(raised)
        raise


def _raise_backend_sanitized_exception(error: BaseException, *, message: str) -> NoReturn:
    _raise_sanitized_transient_exception(
        error,
        message=message,
        system_exit_as_runtime=True,
    )


def _raise_foreign_backend_sanitized_exception(
    error: BaseException,
    *,
    message: str,
) -> NoReturn:
    sanitized: BaseException
    if isinstance(error, KeyboardInterrupt):
        sanitized = KeyboardInterrupt()
    else:
        sanitized = RuntimeError(message)
    _clear_transient_exception_metadata(sanitized)
    try:
        raise sanitized from None
    except BaseException as raised:
        _clear_transient_exception_metadata(raised)
        raise


def _recording_level_lacks_clear_speech_signal(
    recording_level: RecordingLevel | None,
) -> bool:
    if not isinstance(recording_level, RecordingLevel) or not recording_level.ok:
        return False
    peak = recording_level.peak
    rms = recording_level.rms
    if (
        not isinstance(peak, (int, float))
        or isinstance(peak, bool)
        or not isinstance(rms, (int, float))
        or isinstance(rms, bool)
        or not math.isfinite(peak)
        or not math.isfinite(rms)
        or peak < 0
        or rms < 0
    ):
        return False
    return peak < 0.05 and rms < 0.01


def _recording_level_diagnostic(
    recording_level: RecordingLevel | None,
    *,
    recorder: object = "",
    input_device: object = "",
) -> str:
    if not isinstance(recording_level, RecordingLevel) or not recording_level.ok:
        return ""
    peak = recording_level.peak
    rms = recording_level.rms
    if (
        not isinstance(peak, (int, float))
        or isinstance(peak, bool)
        or not isinstance(rms, (int, float))
        or isinstance(rms, bool)
        or not math.isfinite(peak)
        or not math.isfinite(rms)
        or peak < 0
        or rms < 0
    ):
        return ""
    diagnostic = ""
    if _recording_level_lacks_clear_speech_signal(recording_level):
        diagnostic = " No clear speech signal detected; check selected input source and speak closer."
    context = ""
    if isinstance(recorder, str) and not isinstance(recorder, bool):
        recorder_name = recorder.strip().lower()
        if recorder_name in {"auto", "pw-record", "parecord", "arecord"}:
            source_kind = "configured input source" if isinstance(input_device, str) and input_device.strip() else "system default input source"
            context = f" Recorder: {recorder_name}; using {source_kind}."
    return f" Measured input signal: peak {peak:.1%}, RMS {rms:.1%}.{diagnostic}{context}"


def _classified_transcription_failure(
    error: BaseException,
    *,
    recording_level: RecordingLevel | None = None,
    recorder: object = "",
    input_device: object = "",
) -> _ClassifiedPublicError:
    """Classify known transcriber failures without retaining their exceptions."""
    if not isinstance(error, Exception):
        raise RuntimeError("transcription failure is not classifiable")
    if isinstance(error, RecorderError):
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T006): recorded audio could not be prepared. "
                "Check microphone input and recorder settings, then retry."
            ),
            "transcription",
            "SOC-T006",
        )
    if not isinstance(error, TranscriptionError):
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T099): transcription backend could not process the recording. "
                "Check Voice settings and Diagnostics."
            ),
            "transcription",
            "SOC-T099",
        )
    detail = str(error)
    if detail.startswith("no transcriber available"):
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T001): no transcription backend is available. "
                "Install or select a backend in Voice settings."
            ),
            "transcription",
            "SOC-T001",
        )
    if detail in {
        "transcriber executable is not available",
        "custom transcriber executable is not available",
        "OpenAI whisper command is not installed",
        "whisper.cpp command is not installed",
    }:
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T002): transcription executable is unavailable. "
                "Check Voice settings or install the selected backend."
            ),
            "transcription",
            "SOC-T002",
        )
    if detail in {
        "faster-whisper is not available",
        "faster-whisper is not installed",
        "faster-whisper could not be loaded",
        "configured CTranslate2 model requires faster-whisper",
        "configured model requires whisper.cpp",
        "configured whisper model requires whisper.cpp",
        "configured whisper model path is missing",
        "whisper.cpp model failed integrity verification",
        "CTranslate2 model failed integrity verification",
    }:
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T003): selected transcription model is unavailable. "
                "Install the model/backend or choose another model in Voice settings."
            ),
            "transcription",
            "SOC-T003",
        )
    if detail.endswith("timed out"):
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T004): transcription timed out. "
                "Try a shorter recording or check the selected backend."
            ),
            "transcription",
            "SOC-T004",
        )
    if detail in {
        "transcriber completed without transcript",
        "transcriber completed but did not update the transcript file",
        "whisper completed but did not produce a transcript",
        "whisper.cpp completed but did not produce a transcript",
        "OpenAI-compatible speech API returned no transcript",
    }:
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T005): backend returned no usable transcript. "
                "Check microphone input and selected model."
                f"{_recording_level_diagnostic(recording_level, recorder=recorder, input_device=input_device)}"
            ),
            "transcription",
            "SOC-T005",
        )
    remote_http_error = re.fullmatch(
        r"OpenAI-compatible speech API failed \(([1-5][0-9]{2})(?:; ([a-z-]+))?\): .*",
        detail,
    )
    if remote_http_error:
        status_code = int(remote_http_error.group(1))
        category = remote_http_error.group(2) or ""
        if status_code in {401, 403}:
            return _ClassifiedPublicError(
                (
                    "transcribe failed (SOC-T006): external speech API rejected authentication "
                    f"(HTTP {status_code}). Check API key and project access."
                ),
                "transcription",
                "SOC-T006",
            )
        if status_code == 404:
            return _ClassifiedPublicError(
                (
                    "transcribe failed (SOC-T007): external speech API endpoint or model was not found "
                    "(HTTP 404). Check API URL and selected model."
                ),
                "transcription",
                "SOC-T007",
            )
        if status_code == 429 or status_code >= 500:
            if category == "quota-exhausted":
                return _ClassifiedPublicError(
                    (
                        "transcribe failed (SOC-T012): external speech API quota is exhausted. "
                        "Increase project budget or select another backend."
                    ),
                    "transcription",
                    "SOC-T012",
                )
            return _ClassifiedPublicError(
                (
                    "transcribe failed (SOC-T008): external speech API is temporarily unavailable "
                    f"(HTTP {status_code}). Retry shortly."
                ),
                "transcription",
                "SOC-T008",
            )
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T009): external speech API rejected the transcription request "
                f"(HTTP {status_code}). Check selected model and audio format."
            ),
            "transcription",
            "SOC-T009",
        )
    if detail.startswith("OpenAI-compatible speech API is not reachable"):
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T010): external speech API is unreachable. "
                "Check network access and API URL."
            ),
            "transcription",
            "SOC-T010",
        )
    if detail in {
        "OpenAI-compatible speech API returned invalid JSON",
        "OpenAI-compatible speech API response must be a JSON object",
    }:
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T011): external speech API returned an invalid response. "
                "Retry shortly or check API compatibility."
            ),
            "transcription",
            "SOC-T011",
        )
    if detail.startswith("OpenAI-compatible speech API failed:"):
        return _ClassifiedPublicError(
            (
                "transcribe failed (SOC-T009): external speech API rejected the transcription request. "
                "Check selected model and audio format."
            ),
            "transcription",
            "SOC-T009",
        )
    return _ClassifiedPublicError(
        (
            "transcribe failed (SOC-T099): transcription backend could not process the recording. "
            "Check Voice settings and Diagnostics."
        ),
        "transcription",
        "SOC-T099",
    )


def _public_transcription_failure_message(
    error: BaseException,
    *,
    recording_level: RecordingLevel | None = None,
    recorder: object = "",
    input_device: object = "",
) -> str:
    """Return actionable detail only for known, non-sensitive transcriber failures."""
    return str(
        _classified_transcription_failure(
            error,
            recording_level=recording_level,
            recorder=recorder,
            input_device=input_device,
        )
    )


def _redact_error_payload(value: object, *, secret_values: tuple[str, ...] = ()) -> object:
    if isinstance(value, str):
        return _redact_known_cli_secrets(value, secret_values)
    if isinstance(value, dict):
        clean: dict[object, object] = {}
        for key, child in value.items():
            if isinstance(key, str) and key in {"detail", "error", "error_message", "message"} and child is not None:
                clean[key] = _redact_error_for_user(child, secret_values=secret_values)
            else:
                clean[key] = _redact_error_payload(child, secret_values=secret_values)
        return clean
    if isinstance(value, list):
        return [_redact_error_payload(item, secret_values=secret_values) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_error_payload(item, secret_values=secret_values) for item in value)
    return value


def _safe_log_event(level: str, event: str, **fields: object) -> None:
    try:
        log_event(level, event, **fields)
    except Exception:
        return


def append_space_if_needed(text: str, append_space: bool) -> str:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RuntimeError("text must be text")
    if not isinstance(append_space, bool):
        raise RuntimeError("append_space must be a boolean")
    if append_space and text and text[-1] not in " \n\t":
        return text + " "
    return text


def _match_replacement_case(original: str, replacement: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


class _ProfanityOutputLimitExceeded(Exception):
    pass


_PROFANITY_REPLACEMENT_CACHE: tuple[
    Path,
    str,
    tuple[tuple[str, str], ...],
] | None = None


def soften_profanity_text(text: str) -> str:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RuntimeError("text must be text")
    output = text
    if len(output) > MAX_PROFANITY_OUTPUT_CHARS:
        return output
    compiled_rules = _compile_profanity_replacements_with_hints(
        _profanity_replacement_pairs_from_file(),
    )
    normalized_output: str | None = None
    for pattern, replacement, pattern_hint in compiled_rules:
        if normalized_output is None:
            normalized_output = _normalize_profanity_candidate(output)
        if pattern_hint not in normalized_output:
            continue
        projected_chars = len(output)

        def replace(match: re.Match[str], value: str = replacement) -> str:
            nonlocal projected_chars
            replacement_value = _match_replacement_case(match.group(0), value)
            projected_chars += len(replacement_value) - len(match.group(0))
            if projected_chars > MAX_PROFANITY_OUTPUT_CHARS:
                raise _ProfanityOutputLimitExceeded
            return replacement_value

        try:
            output, replacement_count = pattern.subn(replace, output)
        except _ProfanityOutputLimitExceeded:
            break
        if replacement_count:
            normalized_output = None
    return output


def prepare_output_text(text: str, append_space: bool, sanitize: bool, soften_profanity: bool = False) -> str:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RuntimeError("text must be text")
    if not isinstance(append_space, bool):
        raise RuntimeError("append_space must be a boolean")
    if not isinstance(sanitize, bool):
        raise RuntimeError("sanitize must be a boolean")
    if not isinstance(soften_profanity, bool):
        raise RuntimeError("soften_profanity must be a boolean")
    output = soften_profanity_text(text) if soften_profanity else text
    output = sanitize_special_chars(output) if sanitize else output
    return append_space_if_needed(output, append_space)


def _ensure_private_text_file(path: Path, *, field_name: str = "blacklist file") -> None:
    assert_no_symlink_ancestors(path, field_name=field_name)
    _prepare_private_file(path, field_name=field_name, exclusive=False)


def _ensure_editable_profanity_filter_file() -> Path:
    ensure_runtime_dirs()
    path = profanity_filter_file()
    assert_no_symlink_ancestors(path, field_name="profanity filter file")
    if not path.exists():
        _write_text_atomic(path, render_profanity_replacement_list())
    _ensure_private_text_file(path, field_name="profanity filter file")
    return path


def _profanity_replacement_pairs_from_file() -> tuple[tuple[str, str], ...]:
    global _PROFANITY_REPLACEMENT_CACHE
    path = _ensure_editable_profanity_filter_file()
    raw_text = read_text_without_following_symlinks(
        path,
        field_name="profanity filter file",
        max_bytes=MAX_PROFANITY_FILTER_BYTES,
    )
    digest = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    cached = _PROFANITY_REPLACEMENT_CACHE
    if cached is not None and cached[0] == path and cached[1] == digest:
        return cached[2]
    pairs = parse_profanity_replacement_list(raw_text)
    _PROFANITY_REPLACEMENT_CACHE = (path, digest, pairs)
    return pairs


def _profanity_replacements(text: str = "") -> tuple[tuple[re.Pattern[str], str, str], ...]:
    if isinstance(text, bool) or not isinstance(text, str):
        raise ValueError("text must be text")
    return _compile_profanity_replacements_with_hints(
        _profanity_replacement_pairs_from_file(),
        text=text,
    )


BACKGROUND_PROCESS_REAP_TIMEOUT_SECONDS = 5.0
BACKGROUND_PROCESS_REAP_GRACE_SECONDS = 1.0


def _kill_background_process_group(process: subprocess.Popen[bytes]) -> None:
    pid = getattr(process, "pid", None)
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except (OSError, ValueError):
            pass
    try:
        process.kill()
    except (OSError, ValueError):
        pass


def _force_reap_background_process(process: subprocess.Popen[bytes]) -> None:
    _kill_background_process_group(process)
    try:
        process.wait(timeout=BACKGROUND_PROCESS_REAP_GRACE_SECONDS)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


def _reap_background_process(process: subprocess.Popen[bytes]) -> None:
    def reap() -> None:
        try:
            process.wait(timeout=BACKGROUND_PROCESS_REAP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            _force_reap_background_process(process)
        except BaseException:
            _force_reap_background_process(process)

    threading.Thread(target=reap, daemon=True).start()


def _open_path_with_desktop(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    try:
        assert_no_symlink_ancestors(path, field_name="open path")
    except RuntimeError:
        return False
    for command in ((_which("xdg-open"),), (_which("gio"), "open")):
        executable = command[0]
        if not executable:
            continue
        try:
            process = subprocess.Popen(  # nosec B603
                [executable, *command[1:], str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_filtered_environment(),
                start_new_session=True,
            )
            _reap_background_process(process)
            return True
        except (OSError, ValueError):
            continue
    return False


def _open_blacklist_document() -> bool:
    path = blacklist_file()
    try:
        assert_no_symlink_ancestors(path, field_name="blacklist file")
    except RuntimeError:
        return False
    ensure_runtime_dirs()
    _ensure_private_text_file(path)
    return _open_path_with_desktop(path)


def _apply_security_post_processing(text: str) -> tuple[str, dict[str, object]]:
    directives = parse_security_directives(text)
    entries = load_blacklist_file(blacklist_file(), strict=True)
    if directives.added_blacklist:
        entries = update_blacklist_file(blacklist_file(), directives.added_blacklist)
    blacklist_opened = False
    if directives.show_blacklist:
        blacklist_opened = _open_blacklist_document()
    sanitized, redactions = apply_security_mode(directives.text, entries)
    blacklist_hits = 0
    if entries:
        _, blacklist_hits = apply_blacklist_mode(directives.text, entries)
    if directives.added_blacklist or blacklist_hits > 0:
        second_pass, second_pass_redactions = apply_security_mode(sanitized, entries)
        sanitized = second_pass
        redactions += second_pass_redactions
    return sanitized, {
        "blacklist_added": directives.added_blacklist,
        "blacklist_opened": blacklist_opened,
        "redacted_words": redactions,
        "blacklist_hits": blacklist_hits,
    }


def _empty_security_post_processing() -> dict[str, object]:
    return {"blacklist_added": [], "blacklist_opened": False, "redacted_words": 0, "blacklist_hits": 0}


def _empty_transcript_marker(text: str) -> str:
    return re.sub(r"[\W_]+", " ", str(text or "").casefold()).strip()


def _is_empty_transcript_text(text: str) -> bool:
    marker = _empty_transcript_marker(text)
    return marker == "" or marker in EMPTY_TRANSCRIPT_MARKERS


def _merge_security_post_processing(left: dict[str, object], right: dict[str, object]) -> dict[str, object]:
    left_added = left.get("blacklist_added", [])
    right_added = right.get("blacklist_added", [])
    left_redacted = left.get("redacted_words", 0)
    right_redacted = right.get("redacted_words", 0)
    return {
        "blacklist_added": [
            item
            for item in [*(left_added if isinstance(left_added, list) else []), *(right_added if isinstance(right_added, list) else [])]
            if isinstance(item, str)
        ],
        "blacklist_opened": bool(left.get("blacklist_opened")) or bool(right.get("blacklist_opened")),
        "redacted_words": int(left_redacted if isinstance(left_redacted, int) and not isinstance(left_redacted, bool) else 0)
        + int(right_redacted if isinstance(right_redacted, int) and not isinstance(right_redacted, bool) else 0),
        "blacklist_hits": int(left.get("blacklist_hits") or 0) + int(right.get("blacklist_hits") or 0),
    }


def _public_security_post_processing(security: dict[str, object]) -> dict[str, object]:
    added = security.get("blacklist_added", [])
    added_count = len([item for item in added if isinstance(item, str)])
    public = dict(security)
    public["blacklist_added"] = ["[redacted]"] * added_count
    public["blacklist_added_count"] = added_count
    return public


def _apply_security_mask_only(text: str) -> tuple[str, dict[str, object]]:
    directives = parse_security_directives(text)
    entries = load_blacklist_file(blacklist_file(), strict=True)
    sanitized, redactions = apply_security_mode(directives.text, entries)
    blacklist_hits = 0
    if entries:
        _, blacklist_hits = apply_blacklist_mode(directives.text, entries)
    if blacklist_hits > 0:
        second_pass, second_pass_redactions = apply_security_mode(sanitized, entries)
        sanitized = second_pass
        redactions += second_pass_redactions
    return sanitized, {
        "blacklist_added": [],
        "blacklist_opened": False,
        "redacted_words": redactions,
        "blacklist_hits": blacklist_hits,
    }


def _process_transcript(
    text: str,
    args: argparse.Namespace,
    language: str,
) -> tuple[str, dict[str, object]]:
    post_process_backend = _effective_post_process_backend(args.post_process_backend, args.post_process_command)
    openai_compatible_api_key = (
        _openai_compatible_api_key_from_args(args)
        if post_process_backend in {"openai-compatible", "openai", "local-openai"}
        else getattr(args, "openai_compatible_api_key", "")
    )
    text, security_post_processing = _apply_security_post_processing(text)
    if post_process_backend == "ollama":
        text = _remote_postprocess_text(
            remote_http.POSTPROCESS_OLLAMA_OPERATION,
            {
                "language": language,
                "model": args.ollama_model,
                "personal_context": args.personal_context,
                "prompt": args.post_process_prompt,
                "text": text,
                "url": args.ollama_url,
                "vocabulary": args.vocabulary,
            },
        )
    elif post_process_backend in {"openai-compatible", "openai", "local-openai"}:
        text = _remote_postprocess_text(
            remote_http.POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
            {
                "api_key": openai_compatible_api_key,
                "flex_processing": getattr(args, "openai_compatible_flex_processing", True),
                "language": language,
                "model": _openai_compatible_post_process_model(args),
                "personal_context": args.personal_context,
                "prompt": args.post_process_prompt,
                "service_tier_fallback": True,
                "text": text,
                "url": args.openai_compatible_url,
                "vocabulary": args.vocabulary,
            },
        )
    else:
        text = post_process_text(
            text,
            language,
            args.post_process_command,
            args.personal_context,
            args.vocabulary,
            post_process_backend,
            args.ollama_model,
            args.ollama_url,
            args.post_process_prompt,
            _openai_compatible_post_process_model(args),
            args.openai_compatible_url,
            openai_compatible_api_key,
            getattr(args, "openai_compatible_flex_processing", True),
            openai_compatible_service_tier_fallback=True,
        )
    if text.strip() and _coerce_bool(getattr(args, "soften_profanity", False), field_name="soften_profanity"):
        text = soften_profanity_text(text)
    text, final_security_post_processing = _apply_security_mask_only(text)
    return text, _merge_security_post_processing(security_post_processing, final_security_post_processing)


def build_store(args: argparse.Namespace) -> StateStore:
    state_path = normalized_path(args.state_file)
    if not state_path:
        raise RuntimeError("state file path is required")
    return StateStore(state_path)


def read_log_excerpt(path: Path | None, max_chars: int = 2000) -> str:
    if path is not None and not isinstance(path, Path):
        if isinstance(path, str):
            path = Path(path)
        else:
            raise TypeError("path must be a Path")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TypeError("max_chars must be an integer")
    if max_chars <= 0:
        return ""
    if max_chars > MAX_LOG_EXCERPT_CHARS:
        raise ValueError(f"max_chars must be at most {MAX_LOG_EXCERPT_CHARS}")
    if not path:
        return ""
    try:
        text = read_file_tail(path, max_chars)
    except (OSError, ValueError):
        return ""
    return _redact_error_for_user(text.strip())


def transcript_preview(text: str, max_chars: int = 80) -> str:
    text = _sanitize_transcript_display_text(text)
    if text and len(text) <= max_chars and all(text.find(ch) < 0 for ch in " \t\n\r\f\v"):
        return text
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3] + "..."


def _sanitize_transcript_display_text(text: str) -> str:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RuntimeError("transcript display text must be text")
    return TRANSCRIPT_DISPLAY_CONTROL_RE.sub(lambda match: f"\\u{ord(match.group(0)):04x}", text)


def _sanitize_transcript_metadata_text(text: str) -> str:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RuntimeError("transcript metadata text must be text")
    return TRANSCRIPT_METADATA_CONTROL_RE.sub(lambda match: f"\\u{ord(match.group(0)):04x}", text)


def _transcript_display_name(path: Path) -> str:
    if not isinstance(path, Path):
        raise RuntimeError("transcript path must be a path")
    return _sanitize_transcript_metadata_text(path.name)


def _transcript_modified_at(mtime: float) -> str:
    try:
        return datetime.fromtimestamp(mtime, timezone.utc).isoformat()
    except (OSError, OverflowError, TypeError, ValueError):
        return "unknown"


def _redact_history_previews(transcripts: list[dict[str, object]]) -> list[dict[str, object]]:
    redacted: list[dict[str, object]] = []
    for entry in transcripts:
        redacted_entry: dict[str, object] = {
            "preview": HISTORY_PREVIEW_REDACTED_TEXT,
            "name": HISTORY_METADATA_REDACTED_TEXT,
            "path": HISTORY_METADATA_REDACTED_TEXT,
        }
        redacted.append(redacted_entry)
    return redacted


def _transcript_history_candidates(directory: Path):
    for path, file_stat in _safe_directory_entries(directory, field_name="transcript directory"):
        if not _is_transcript_artifact(path):
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if getattr(file_stat, "st_nlink", 1) != 1:
            continue
        yield file_stat.st_mtime, path, file_stat


def _is_transcript_artifact(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    name = path.name.lower()
    if name.startswith("."):
        return False
    return name.endswith(".txt") or name.endswith(ENCRYPTED_TRANSCRIPT_SUFFIX)


def _transcript_group_key(path: Path) -> str:
    if path.name.lower().endswith(ENCRYPTED_TRANSCRIPT_SUFFIX):
        return str(path.with_name(path.name[: -len(".socenc")]))
    return str(path)


def _group_transcript_artifacts(paths: list[Path]) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {}
    for path in paths:
        if not isinstance(path, Path):
            continue
        groups.setdefault(_transcript_group_key(path), []).append(path)
    return groups


def _safe_transcript_artifact_files(
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> list[Path]:
    return [
        path
        for path in _safe_regular_child_files(
            transcript_dir(),
            TRANSCRIPT_ARTIFACT_SUFFIXES,
            field_name="transcript directory",
            expected_stats=expected_stats,
        )
        if _is_transcript_artifact(path)
    ]


def _is_transient_transcript_artifact(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    name = path.name.lower()
    return name.startswith(".") and name.endswith(".tmp.txt")


def _transient_transcript_owner_path(path: Path) -> Path:
    return path.with_name(f"{path.name}{TRANSIENT_TRANSCRIPT_OWNER_SUFFIX}")


def _write_transient_transcript_owner(path: Path) -> os.stat_result:
    owner_path = _transient_transcript_owner_path(path)
    identity = _finalization_lock_identity_for_pid(os.getpid()) or ""
    content = f"{os.getpid()}\n{identity}\n"
    try:
        write_text_atomically_without_following_symlinks(
            owner_path,
            content,
            field_name="transient transcript owner",
        )
    except KeyboardInterrupt:
        raise
    except (OSError, RuntimeError):
        raise RuntimeError("failed to write transient transcript owner") from None
    owner_stat = _recording_artifact_stat(owner_path)
    if owner_stat is None:
        raise RuntimeError("failed to write transient transcript owner") from None
    return owner_stat


def _remove_transient_transcript_owner(
    path: Path,
    *,
    expected_stat: os.stat_result | None = None,
) -> bool:
    owner_path = _transient_transcript_owner_path(path)
    try:
        return _unlink_regular_leaf_with_parent_fsync(
            owner_path,
            field_name="transient transcript owner",
            expected_stat=expected_stat,
        )
    except FileNotFoundError:
        return False


def _transient_transcript_owner_cleanup_is_safe(path: Path) -> bool:
    owner_path = _transient_transcript_owner_path(path)
    try:
        file_stat = owner_path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat_module.S_ISLNK(file_stat.st_mode):
        return False
    if not stat_module.S_ISREG(file_stat.st_mode):
        return False
    return getattr(file_stat, "st_nlink", 1) == 1


def _read_transient_transcript_owner(path: Path) -> tuple[int | None, str | None]:
    try:
        raw = read_text_without_following_symlinks(
            _transient_transcript_owner_path(path),
            field_name="transient transcript owner",
            max_bytes=512,
            require_private_mode=True,
        )
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None, None
    lines = raw.splitlines()
    if not lines:
        return None, None
    pid_text = lines[0].strip()
    if not pid_text.isdigit():
        return None, None
    pid = int(pid_text)
    identity = lines[1].strip() if len(lines) > 1 else ""
    return (pid if pid > 0 else None), (identity or None)


def _transient_transcript_owner_is_active(path: Path) -> bool:
    owner_pid, owner_identity = _read_transient_transcript_owner(path)
    if owner_pid is None:
        return False
    if not _process_is_running(owner_pid):
        return False
    if owner_identity is None:
        return True
    current_identity = _finalization_lock_identity_for_pid(owner_pid)
    if current_identity is None:
        return True
    return (
        _recording_process_identity_relation(
            owner_pid,
            owner_identity,
            current_identity,
        )
        != _PROCESS_IDENTITY_REUSED
    )


def _safe_stale_transient_transcript_files(
    max_age_seconds: int = TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS,
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> list[Path]:
    if not isinstance(max_age_seconds, int) or isinstance(max_age_seconds, bool):
        raise RuntimeError("transient transcript max age must be an integer")
    cutoff = time.time() - max(max_age_seconds, 0)
    files: list[Path] = []
    for path, file_stat in _safe_directory_entries(transcript_dir(), field_name="transcript directory"):
        if not _is_transient_transcript_artifact(path):
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if getattr(file_stat, "st_nlink", 1) != 1:
            continue
        if file_stat.st_mtime > cutoff:
            continue
        if _transient_transcript_owner_is_active(path):
            continue
        if expected_stats is not None:
            expected_stats[path] = file_stat
        files.append(path)
    return files


def prune_stale_transient_transcripts(dry_run: bool = False) -> dict[str, object]:
    file_stats: dict[Path, os.stat_result] = {}
    try:
        files = _safe_stale_transient_transcript_files(expected_stats=file_stats)
    except DirectoryScanError as exc:
        return {
            "planned_paths": [],
            "deleted_paths": [],
            "failed_paths": [str(exc.directory)],
            "skipped_active_paths": [],
        }
    owner_stats = {
        path: _recording_artifact_stat(_transient_transcript_owner_path(path))
        for path in files
    }
    eligible_files: list[Path] = []
    failed_paths: list[str] = []
    for path in files:
        owner_path = _transient_transcript_owner_path(path)
        expected_owner_stat = owner_stats.get(path)
        if expected_owner_stat is None:
            owner_presence = _safe_leaf_presence(owner_path)
            if owner_presence is None or owner_presence:
                failed_paths.append(str(owner_path))
                continue
        elif dry_run:
            owner_presence = _safe_leaf_presence(owner_path)
            current_owner_stat = _recording_artifact_stat(owner_path) if owner_presence else None
            if (
                owner_presence is None
                or not owner_presence
                or current_owner_stat is None
                or not _same_leaf_identity(current_owner_stat, expected_owner_stat)
                or not _transient_transcript_owner_cleanup_is_safe(path)
            ):
                failed_paths.append(str(owner_path))
                continue
        else:
            try:
                owner_removed = _remove_transient_transcript_owner(path, expected_stat=expected_owner_stat)
            except RuntimeError:
                owner_removed = False
            if not owner_removed:
                failed_paths.append(str(owner_path))
                continue
            owner_presence = _safe_leaf_presence(owner_path)
            if owner_presence is None or owner_presence:
                failed_paths.append(str(owner_path))
                continue
        eligible_files.append(path)
    result = prune_files_by_mtime(
        eligible_files,
        0,
        active_paths=set(),
        dry_run=dry_run,
        expected_stats=file_stats,
    )
    result["failed_paths"] = failed_paths + result["failed_paths"]
    return result


def _cleanup_failed_paths(*cleanup_results: dict[str, object]) -> list[str]:
    failed_paths: list[str] = []
    for cleanup_result in cleanup_results:
        if "failed_paths" not in cleanup_result:
            raise RuntimeError("cleanup result missing failed_paths")
        paths = cleanup_result["failed_paths"]
        if not isinstance(paths, list):
            raise RuntimeError("cleanup result failed_paths must be a list")
        for path in paths:
            if not isinstance(path, str) or not path:
                raise RuntimeError("cleanup result failed_paths entries must be non-empty strings")
            failed_paths.append(path)
    return failed_paths


def _cleanup_failure_error(failed_paths: list[str]) -> str:
    return f"failed to scan or delete {len(failed_paths)} cleanup artifact(s)"


def _public_cleanup_result(cleanup_result: dict[str, object]) -> dict[str, object]:
    public = dict(cleanup_result)
    count_fields = {
        "planned_paths": "planned_path_count",
        "deleted_paths": "deleted_path_count",
        "failed_paths": "failed_path_count",
        "skipped_active_paths": "skipped_active_path_count",
    }
    for path_field, count_field in count_fields.items():
        paths = public.get(path_field, [])
        if not isinstance(paths, list):
            raise RuntimeError(f"cleanup result {path_field} must be a list")
        public[count_field] = len(paths)
        public[path_field] = []
    return public


def _persist_cleanup_failure_state(
    store: StateStore,
    failed_paths: list[str],
    *,
    artifact_state: RecordingState | None = None,
    clear_transcript: bool = False,
) -> None:
    if not failed_paths:
        return
    error_text = _cleanup_failure_error(failed_paths)
    try:
        updates: dict[str, object] = {
            "status": "error",
            "pid": None,
            "process_identity": "",
            "stopped_at": now_iso(),
            "error": error_text,
        }
        if artifact_state is not None:
            updates.update(
                {
                    "audio_path": artifact_state.audio_path,
                    "log_path": artifact_state.log_path,
                    "transcript": artifact_state.transcript,
                    "transcript_path": artifact_state.transcript_path,
                    "inserted": artifact_state.inserted,
                }
            )
            if clear_transcript:
                updates["transcript"] = ""
        store.update(**updates)
    except BaseException as exc:
        _raise_backend_sanitized_exception(
            exc,
            message="failed to persist error state",
        )


def _read_stored_transcript_text(
    path: Path,
    *,
    max_bytes: int | None = None,
    expected_stat: os.stat_result | None = None,
) -> str:
    if max_bytes is not None and (
        isinstance(max_bytes, bool)
        or not isinstance(max_bytes, int)
        or max_bytes < 1
    ):
        raise RuntimeError("transcript read size limit must be positive")
    read_limit = MAX_STORED_TRANSCRIPT_BYTES if max_bytes is None else min(
        max_bytes,
        MAX_STORED_TRANSCRIPT_BYTES,
    )
    if is_encrypted_path(path):
        payload = read_decrypted_bytes_from_file(
            path,
            kind="transcript",
            field_name="transcript file",
            max_bytes=MAX_ENCRYPTED_STORED_TRANSCRIPT_BYTES,
            require_encrypted=True,
            expected_stat=expected_stat,
        )
        if len(payload) > read_limit:
            raise RuntimeError("transcript file is too large")
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"transcript file is not valid UTF-8: {path}") from exc
    try:
        return read_text_without_following_symlinks(
            path,
            field_name="transcript file",
            max_bytes=read_limit,
            expected_stat=expected_stat,
        )
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"transcript file is not valid UTF-8: {path}") from exc


def _stored_transcript_encryption_mode(
    path: Path,
    *,
    expected_stat: os.stat_result,
) -> str:
    if not is_encrypted_path(path):
        return ARTIFACT_ENCRYPTION_OFF
    payload = read_private_bytes(
        path,
        field_name="transcript file",
        max_bytes=MAX_ENCRYPTED_STORED_TRANSCRIPT_BYTES,
        expected_stat=expected_stat,
    )
    try:
        envelope = json.loads(
            payload.decode("utf-8"),
            parse_constant=_reject_non_finite_json_number,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
        mode = normalize_artifact_encryption(
            envelope.get("mode", "") if isinstance(envelope, dict) else ""
        )
    except (UnicodeDecodeError, ValueError, RecursionError, MemoryError, ArtifactCryptoError):
        raise RuntimeError("encrypted transcript metadata is invalid") from None
    if mode == ARTIFACT_ENCRYPTION_OFF:
        raise RuntimeError("encrypted transcript metadata is invalid")
    return mode


def _artifact_encryption_mode(args: argparse.Namespace) -> str:
    return normalize_artifact_encryption(getattr(args, "artifact_encryption", ARTIFACT_ENCRYPTION_OFF))


def _confirm_plaintext_transcript_output(args: argparse.Namespace) -> bool:
    return _coerce_bool(
        getattr(args, "confirm_plaintext_output", False),
        field_name="confirm-plaintext-output",
    )


def _transcript_payload_text(text: str, transcript_encryption: str, args: argparse.Namespace) -> str:
    if _confirm_plaintext_transcript_output(args):
        return text
    return ""


def _transcript_work_path(storage_path: Path, encryption_mode: str) -> Path:
    return storage_path.with_name(f".{storage_path.stem}.{secrets.token_hex(8)}.tmp.txt")


def _prepare_transient_transcript_path(
    path: Path,
    storage_path: Path,
) -> tuple[int | None, os.stat_result | None]:
    captured_error: BaseException | None = None
    result: tuple[int | None, os.stat_result | None] | None = None
    try:
        result = _prepare_transient_transcript_path_impl(path, storage_path)
    except BaseException as exc:
        captured_error = exc
    if captured_error is not None:
        _raise_sanitized_transient_exception(captured_error)
    if result is None:
        raise RuntimeError(TRANSIENT_TRANSCRIPT_WRITE_ERROR)
    return result


def _prepare_transient_transcript_path_impl(
    path: Path,
    storage_path: Path,
) -> tuple[int | None, os.stat_result | None]:
    if path == storage_path:
        return None, None
    try:
        path.relative_to(transcript_dir())
    except ValueError as exc:
        raise RuntimeError(f"refusing to prepare transient transcript outside transcript directory: {path}") from exc
    if not path.name.startswith(".") or not path.name.endswith(".tmp.txt"):
        raise RuntimeError(f"refusing to prepare unexpected transient transcript path: {path}")
    assert_no_symlink_ancestors(path, field_name="transient transcript file")

    def cleanup_created_path(
        primary_error: BaseException,
        expected_stat: os.stat_result | None = None,
        expected_owner_stat: os.stat_result | None = None,
    ) -> None:
        if expected_stat is None:
            return
        try:
            if expected_owner_stat is None:
                _remove_transient_transcript_path(path, storage_path, expected_stat=expected_stat)
            else:
                _remove_transient_transcript_path(
                    path,
                    storage_path,
                    expected_stat=expected_stat,
                    expected_owner_stat=expected_owner_stat,
                )
        except BaseException as cleanup_error:
            interrupt_error = (
                primary_error
                if isinstance(primary_error, KeyboardInterrupt)
                else cleanup_error
                if isinstance(cleanup_error, KeyboardInterrupt)
                else None
            )
            if interrupt_error is not None:
                _raise_sanitized_transient_exception(interrupt_error)
            if isinstance(primary_error, Exception):
                _raise_sanitized_transient_exception(
                    primary_error,
                    message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
                )
            _raise_sanitized_transient_exception(primary_error)

    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise OSError("secure transient transcript file open is not supported on this platform")
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    file_stat: os.stat_result | None = None
    owner_stat: os.stat_result | None = None
    try:
        prepared = _prepare_private_file(
            path,
            field_name="transient transcript file",
            _keep_fd=True,
        )
    except _PrivateFilePrepareError as exc:
        if exc.created:
            cleanup_created_path(exc, getattr(exc, "created_stat", None))
        raise
    except BaseException as exc:
        cleanup_created_path(exc, getattr(exc, "_speed_of_cinnamon_created_stat", None))
        _raise_sanitized_transient_exception(exc)

    def finish_transient_prepare_failure(
        primary_error: BaseException,
        *,
        expected_stat: os.stat_result | None,
        expected_owner_stat: os.stat_result | None,
        message: str = TRANSIENT_TRANSCRIPT_WRITE_ERROR,
    ) -> NoReturn:
        close_error: BaseException | None = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            except BaseException as exc:
                close_error = exc
        cleanup_error: BaseException | None = None
        try:
            cleanup_created_path(primary_error, expected_stat, expected_owner_stat)
        except BaseException as exc:
            cleanup_error = exc
        interrupt_error = next(
            (
                error
                for error in (primary_error, close_error, cleanup_error)
                if isinstance(error, KeyboardInterrupt)
            ),
            None,
        )
        if interrupt_error is not None:
            _raise_sanitized_transient_exception(
                interrupt_error,
                message=TRANSIENT_TRANSCRIPT_INTERRUPT_ERROR,
            )
        if close_error is not None and isinstance(primary_error, Exception):
            _raise_sanitized_transient_exception(close_error)
        if cleanup_error is not None:
            _raise_sanitized_transient_exception(cleanup_error, message=message)
        _raise_sanitized_transient_exception(primary_error, message=message)

    try:
        if isinstance(prepared, tuple) and len(prepared) == 2:
            fd, file_stat = prepared
        else:
            fd = os.open(path, os.O_RDONLY | nofollow_flag | cloexec_flag)
            file_stat = os.fstat(fd)
        if not stat_module.S_ISREG(file_stat.st_mode):
            raise RuntimeError(f"transient transcript file must be a regular file: {path}")
        if getattr(file_stat, "st_nlink", 1) != 1:
            raise RuntimeError(f"transient transcript file must not be hardlinked: {path}")
        owner_stat = _write_transient_transcript_owner(path)
        return fd, owner_stat
    except OSError as exc:
        finish_transient_prepare_failure(
            exc,
            expected_stat=file_stat,
            expected_owner_stat=owner_stat,
        )
    except RuntimeError as exc:
        finish_transient_prepare_failure(
            exc,
            expected_stat=file_stat,
            expected_owner_stat=owner_stat,
        )
    except BaseException as exc:
        finish_transient_prepare_failure(
            exc,
            expected_stat=file_stat,
            expected_owner_stat=owner_stat,
            message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
        )


def _same_leaf_identity(current: os.stat_result, expected: os.stat_result) -> bool:
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_mode == expected.st_mode
        and current.st_size == expected.st_size
        and getattr(current, "st_nlink", 1) == getattr(expected, "st_nlink", 1)
        and current.st_mtime_ns == expected.st_mtime_ns
        and current.st_ctime_ns == expected.st_ctime_ns
    )


def _same_leaf_claim_identity(current: os.stat_result, expected: os.stat_result) -> bool:
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_mode == expected.st_mode
        and current.st_size == expected.st_size
        and getattr(current, "st_nlink", 1) == getattr(expected, "st_nlink", 1)
        and current.st_mtime_ns == expected.st_mtime_ns
    )


def _unlink_regular_leaf_with_parent_fsync(
    path: Path,
    *,
    field_name: str,
    expected_stat: os.stat_result | None = None,
    already_claimed: bool = False,
    wipe_bytes: int | None = None,
) -> bool:
    parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
    try:
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not stat_module.S_ISREG(current.st_mode):
            raise RuntimeError(f"{field_name} must be a regular file: {path}")
        if getattr(current, "st_nlink", 1) != 1:
            raise RuntimeError(f"{field_name} must not be hardlinked: {path}")
        if expected_stat is not None and not _same_leaf_identity(current, expected_stat):
            raise RuntimeError(f"{field_name} changed before deletion: {path}")
        if already_claimed:
            wiped_stat = secure_wipe_regular_file_at(
                parent_fd,
                path.name,
                current,
                field_name=field_name,
                wipe_bytes=wipe_bytes,
            )
            final_named_stat = os.stat(
                path.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if not _same_leaf_identity(final_named_stat, wiped_stat):
                raise RuntimeError(f"{field_name} changed after secure deletion: {path}")
            os.unlink(path.name, dir_fd=parent_fd)
            _fsync_fd(parent_fd)
            return True
        for _ in range(100):
            cleanup_name = f"{path.name}.{secrets.token_hex(8)}.cleanup"
            try:
                _rename_without_replacing(
                    path.name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name=f"{field_name} cleanup",
                )
            except FileExistsError:
                continue
            try:
                claimed = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat_module.S_ISREG(claimed.st_mode) or getattr(claimed, "st_nlink", 1) != 1:
                    raise RuntimeError(f"{field_name} changed before deletion: {path}")
                if not _same_leaf_claim_identity(claimed, current):
                    raise RuntimeError(f"{field_name} changed before deletion: {path}")
                wiped_stat = secure_wipe_regular_file_at(
                    parent_fd,
                    cleanup_name,
                    claimed,
                    field_name=field_name,
                )
                final_named_stat = os.stat(
                    cleanup_name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                if not _same_leaf_identity(final_named_stat, wiped_stat):
                    raise RuntimeError(
                        f"{field_name} changed after secure deletion: {path}"
                    )
                os.unlink(cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                try:
                    _rename_without_replacing(
                        cleanup_name,
                        path.name,
                        directory_fd=parent_fd,
                        field_name=f"{field_name} cleanup restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException:
                    exc.add_note(f"{field_name} cleanup restore failed")
                raise
            return True
        raise RuntimeError(f"failed to claim {field_name} cleanup path: {path}")
    except OSError as exc:
        raise RuntimeError(f"failed to delete {field_name}: {path}") from exc
    finally:
        try:
            os.close(parent_fd)
        except BaseException:
            pass


def _copy_recording_artifact_to_backup(
    source: Path,
    backup: Path,
    *,
    expected_stat: os.stat_result,
) -> None:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise RuntimeError("secure recording cleanup backup copy is not supported on this platform")
    try:
        nonblock_flag = _required_nonblocking_flag()
    except OSError:
        raise RuntimeError("secure nonblocking file open is not supported on this platform") from None
    parent_fd = ensure_directory_without_following_symlinks(
        source.parent,
        field_name="recording cleanup backup directory",
    )
    source_fd: int | None = None
    backup_fd: int | None = None
    primary_error: BaseException | None = None
    try:
        source_fd = os.open(
            source.name,
            os.O_RDONLY | nonblock_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        assert_fd_is_regular_private_file(source_fd, field_name="recording cleanup source")
        source_open_stat = os.fstat(source_fd)
        if not _same_leaf_identity(source_open_stat, expected_stat):
            raise RuntimeError("recording cleanup source changed before backup copy")
        backup_fd = os.open(
            backup.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | nofollow_flag
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
        os.fchmod(backup_fd, 0o600)
        while True:
            try:
                chunk = os.read(source_fd, 1024 * 1024)
            except InterruptedError:
                continue
            if not chunk:
                break
            _write_all(backup_fd, chunk, field_name="recording cleanup backup")
        _fsync_fd(backup_fd)
        assert_fd_is_regular_private_file(
            backup_fd,
            field_name="recording cleanup backup",
            require_private_mode=True,
        )
        current_source_stat = os.stat(source.name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_leaf_identity(current_source_stat, expected_stat):
            raise RuntimeError("recording cleanup source changed during backup copy")
        _fsync_fd(parent_fd)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        for fd in (backup_fd, source_fd, parent_fd):
            if fd is None:
                continue
            try:
                os.close(fd)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            if primary_error is not None:
                for cleanup_error in cleanup_errors:
                    primary_error.add_note("recording cleanup backup copy cleanup failed")
            else:
                raise cleanup_errors[0]


def _remove_transient_transcript_path(
    path: Path,
    storage_path: Path,
    *,
    expected_fd: int | None = None,
    expected_stat: os.stat_result | None = None,
    expected_owner_stat: os.stat_result | None = None,
) -> bool:
    if path == storage_path:
        return False
    try:
        path.relative_to(transcript_dir())
    except ValueError:
        return False
    if not path.name.startswith(".") or not path.name.endswith(".tmp.txt"):
        return False
    try:
        assert_no_symlink_ancestors(path, field_name="transient transcript file")
        if expected_fd is not None and expected_stat is None:
            expected_stat = os.fstat(expected_fd)
        try:
            _unlink_regular_leaf_with_parent_fsync(
                path,
                field_name="transient transcript file",
                expected_stat=expected_stat,
            )
        except FileNotFoundError:
            path_presence = _safe_leaf_presence(path)
            if path_presence is None or path_presence:
                raise RuntimeError(f"failed to inspect deleted transient transcript file: {path}") from None
        owner_path = _transient_transcript_owner_path(path)
        if expected_owner_stat is None:
            owner_presence = _safe_leaf_presence(owner_path)
            if owner_presence is None or owner_presence:
                raise RuntimeError(f"failed to delete transient transcript owner: {owner_path}")
        else:
            _remove_transient_transcript_owner(path, expected_stat=expected_owner_stat)
        owner_presence = _safe_leaf_presence(owner_path)
        if owner_presence is None or owner_presence:
            raise RuntimeError(f"failed to delete transient transcript owner: {owner_path}")
        return True
    except FileNotFoundError:
        return False
    except RuntimeError as exc:
        raise RuntimeError(f"failed to delete transient transcript file: {path}") from exc
    finally:
        if expected_fd is not None:
            try:
                os.close(expected_fd)
            except BaseException:
                pass


def _transcription_cleanup_exception(
    transcription_error: BaseException | None,
    cleanup_error: BaseException,
    *,
    stable_public_error: bool = False,
) -> BaseException:
    del stable_public_error
    interrupt_error = (
        transcription_error
        if isinstance(transcription_error, KeyboardInterrupt)
        else cleanup_error
        if isinstance(cleanup_error, KeyboardInterrupt)
        else None
    )
    if interrupt_error is not None:
        return _sanitize_transient_exception(interrupt_error)
    return _sanitize_transient_exception(
        RuntimeError(),
        message=TRANSIENT_TRANSCRIPT_CLEANUP_ERROR,
    )


def _raise_recording_cleanup_failure(
    store: StateStore,
    failures: list[tuple[str, str, str]],
    *,
    inserted: bool = False,
) -> None:
    if not failures:
        return
    failed_labels = ", ".join(label for _, _, label in failures)
    error_text = f"failed to delete recording artifact(s): {failed_labels}"
    error_update: dict[str, object] = {
        "status": "error",
        "pid": None,
        "process_identity": "",
        "stopped_at": now_iso(),
        "error": error_text,
        "inserted": inserted,
    }
    for field_name, path_text, _label in failures:
        error_update[field_name] = path_text
    try:
        store.update(**error_update)
    except BaseException as exc:
        _raise_backend_sanitized_exception(
            exc,
            message="failed to persist error state",
        )
    raise RuntimeError(error_text)


def _write_stored_transcript(path: Path, text: str, args: argparse.Namespace) -> tuple[Path, str]:
    mode = _artifact_encryption_mode(args)
    try:
        payload = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RuntimeError("failed to write transcript file: transcript text is not valid UTF-8") from exc
    if len(payload) > MAX_STORED_TRANSCRIPT_BYTES:
        raise RuntimeError("transcript file is too large")
    if mode == ARTIFACT_ENCRYPTION_OFF:
        encrypted_sibling = _transcript_sibling_path(path)
        encrypted_sibling_present = bool(
            encrypted_sibling is not None and _path_exists_or_is_symlink(encrypted_sibling)
        )
        if encrypted_sibling_present and _path_exists_or_is_symlink(path):
            raise RuntimeError(
                f"refusing to overwrite existing plaintext transcript while encrypted sibling is present: {path}"
            )
        encrypted_sibling_stat = (
            _recording_artifact_stat(encrypted_sibling)
            if encrypted_sibling_present and encrypted_sibling is not None
            else None
        )
        _write_text_atomic(path, text)
        plaintext_stat = _recording_artifact_stat(path)
        if encrypted_sibling is not None and _path_exists_or_is_symlink(encrypted_sibling):
            try:
                if not encrypted_sibling_present:
                    raise RuntimeError(f"encrypted transcript sibling appeared during plaintext storage: {encrypted_sibling}")
                if encrypted_sibling_stat is None or not _remove_transcript_file(
                    encrypted_sibling,
                    expected_stat=encrypted_sibling_stat,
                ):
                    raise RuntimeError(f"encrypted transcript sibling is missing: {encrypted_sibling}")
            except RuntimeError as exc:
                try:
                    _rollback_plaintext_transcript_after_sibling_cleanup_failure(
                        path,
                        expected_stat=plaintext_stat,
                    )
                except BaseException as rollback_exc:
                    if isinstance(rollback_exc, Exception):
                        raise RuntimeError(f"{exc}; plaintext transcript rollback failed") from exc
                    exc.add_note("plaintext transcript rollback failed")
                    raise exc.with_traceback(exc.__traceback__) from None
                raise RuntimeError(
                    f"failed to remove encrypted transcript sibling after plaintext storage: {encrypted_sibling}"
                ) from exc
        return path, ARTIFACT_ENCRYPTION_OFF
    encrypted_target_existed = False
    plaintext_present = _path_exists_or_is_symlink(path)
    plaintext_stat = _recording_artifact_stat(path) if plaintext_present else None
    try:
        encrypted_target_existed = _path_exists_or_is_symlink(encrypted_path_for(path))
        encrypted_path, effective_mode = write_encrypted_bytes_atomically(
            path,
            payload,
            mode,
            kind="transcript",
            field_name="transcript file",
        )
        encrypted_artifact_stat = _recording_artifact_stat(encrypted_path)
    except ArtifactCryptoError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        if plaintext_present:
            if plaintext_stat is None:
                raise RuntimeError(f"plaintext transcript file is not a safe regular file: {path}")
        elif _path_exists_or_is_symlink(path):
            raise RuntimeError(f"plaintext transcript file appeared during encryption: {path}")
        _remove_plaintext_transcript_sibling_after_encryption(
            path,
            encrypted_path,
            expected_stat=plaintext_stat,
        )
    except RuntimeError as exc:
        if not encrypted_target_existed:
            try:
                _rollback_encrypted_artifact_after_plaintext_cleanup_failure(
                    encrypted_path,
                    field_name="encrypted transcript file",
                    expected_stat=encrypted_artifact_stat,
                )
            except BaseException as rollback_exc:
                _raise_encrypted_artifact_rollback_failure(exc, rollback_exc)
        raise
    return encrypted_path, effective_mode


def _remove_plaintext_transcript_sibling_after_encryption(
    storage_path: Path,
    encrypted_path: Path,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    if encrypted_path == storage_path or not is_encrypted_path(encrypted_path):
        return
    plaintext_path = encrypted_path.with_name(encrypted_path.name[:-len(".socenc")])
    if plaintext_path != storage_path:
        raise RuntimeError(f"unexpected encrypted transcript sibling path: {encrypted_path}")
    plaintext_presence = _safe_leaf_presence(plaintext_path)
    if plaintext_presence is None:
        raise RuntimeError(f"failed to inspect plaintext transcript artifact: {plaintext_path}")
    if not plaintext_presence:
        return
    if not _remove_transcript_file(plaintext_path, expected_stat=expected_stat):
        raise RuntimeError(f"failed to remove plaintext transcript artifact after encryption: {plaintext_path}")


def _rollback_plaintext_transcript_after_sibling_cleanup_failure(
    path: Path,
    *,
    expected_stat: os.stat_result | None,
) -> None:
    path_presence = _safe_leaf_presence(path)
    if path_presence is None:
        raise RuntimeError(f"failed to inspect plaintext transcript artifact: {path}")
    if not path_presence:
        return
    if expected_stat is None:
        raise RuntimeError(f"failed to roll back plaintext transcript artifact: {path}")
    if not _remove_transcript_file(path, expected_stat=expected_stat):
        path_presence = _safe_leaf_presence(path)
        if path_presence is None or path_presence:
            raise RuntimeError(f"failed to roll back plaintext transcript artifact: {path}")


def _remove_plaintext_export_sibling_after_encryption(
    storage_path: Path,
    encrypted_path: Path,
    *,
    expected_stat: os.stat_result | None = None,
    expected_present: bool = True,
) -> None:
    if encrypted_path == storage_path or not is_encrypted_path(encrypted_path):
        return
    plaintext_path = encrypted_path.with_name(encrypted_path.name[:-len(".socenc")])
    if plaintext_path != storage_path:
        raise RuntimeError(f"unexpected encrypted transcript export sibling path: {encrypted_path}")
    plaintext_presence = _safe_leaf_presence(plaintext_path)
    if not expected_present:
        if plaintext_presence is None or plaintext_presence:
            raise RuntimeError(f"transcript export appeared during encryption: {plaintext_path}")
        return
    if plaintext_presence is None:
        raise RuntimeError(f"failed to inspect plaintext transcript export: {plaintext_path}")
    if not plaintext_presence:
        return
    if expected_stat is None:
        raise RuntimeError(f"transcript export is not a safe regular file: {plaintext_path}")
    try:
        assert_no_symlink_ancestors(plaintext_path, field_name="transcript export")
        if not _unlink_regular_leaf_with_parent_fsync(
            plaintext_path,
            field_name="transcript export",
            expected_stat=expected_stat,
        ):
            return
    except RuntimeError as exc:
        raise RuntimeError(f"failed to remove plaintext transcript export after encryption: {plaintext_path}") from exc


def _plaintext_recording_sibling_for_encrypted_path(path: Path) -> Path | None:
    if not is_encrypted_path(path) or not path.name.lower().endswith(".socenc"):
        return None
    plaintext_path = path.with_name(path.name[:-len(".socenc")])
    if plaintext_path.suffix.lower() not in {".flac", ".wav"}:
        return None
    return plaintext_path


def _plaintext_recording_cleanup_candidates(original_path: Path, encrypted_path: Path) -> list[Path]:
    candidates: list[Path] = []
    if encrypted_path != original_path:
        candidates.append(original_path)
    plaintext_sibling = _plaintext_recording_sibling_for_encrypted_path(encrypted_path)
    if plaintext_sibling is not None:
        candidates.append(plaintext_sibling)

    seen: set[Path] = set()
    unique_candidates: list[Path] = []
    for candidate in candidates:
        if candidate in seen or candidate == encrypted_path:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)
    return unique_candidates


def _remove_plaintext_recording_sibling_after_encryption(
    original_path: Path,
    encrypted_path: Path,
    *,
    expected_stats: dict[Path, os.stat_result | None] | None = None,
) -> None:
    for candidate in _plaintext_recording_cleanup_candidates(original_path, encrypted_path):
        expected_stat = expected_stats.get(candidate) if expected_stats is not None else None
        candidate_presence = _safe_leaf_presence(candidate)
        if expected_stats is not None and candidate in expected_stats and expected_stat is None:
            if candidate_presence is None or candidate_presence:
                raise RuntimeError(f"plaintext recording artifact changed before cleanup: {candidate}")
            continue
        if candidate_presence is None:
            raise RuntimeError(f"failed to inspect plaintext recording artifact: {candidate}")
        if not candidate_presence:
            continue
        suffix = candidate.suffix.lower()
        if suffix not in {".flac", ".wav"}:
            raise RuntimeError(f"refusing to remove unexpected plaintext recording artifact: {candidate}")
        if not remove_file(str(candidate), suffix=suffix, expected_stat=expected_stat):
            raise RuntimeError(f"failed to remove plaintext recording artifact after encryption: {candidate}")


def _rollback_encrypted_artifact_after_plaintext_cleanup_failure(
    encrypted_path: Path,
    *,
    field_name: str,
    expected_stat: os.stat_result | None,
) -> None:
    if not is_encrypted_path(encrypted_path):
        return
    encrypted_presence = _safe_leaf_presence(encrypted_path)
    if encrypted_presence is None:
        raise RuntimeError(f"failed to inspect {field_name}: {encrypted_path}")
    if not encrypted_presence:
        return
    try:
        if expected_stat is None:
            raise RuntimeError(f"{field_name} identity is unavailable: {encrypted_path}")
        _unlink_regular_leaf_with_parent_fsync(
            encrypted_path,
            field_name=field_name,
            expected_stat=expected_stat,
        )
    except RuntimeError as exc:
        raise RuntimeError(f"failed to roll back encrypted artifact after plaintext cleanup failure: {encrypted_path}") from exc


def _raise_encrypted_artifact_rollback_failure(primary_error: RuntimeError, rollback_error: BaseException) -> None:
    if isinstance(rollback_error, Exception):
        raise RuntimeError(f"{primary_error}; encrypted artifact rollback failed") from primary_error
    primary_error.add_note(f"encrypted artifact rollback failed: {rollback_error}")
    raise primary_error.with_traceback(primary_error.__traceback__)


def _encrypt_kept_recording_artifact(path: Path, args: argparse.Namespace) -> tuple[Path, str]:
    mode = _artifact_encryption_mode(args)
    if mode == ARTIFACT_ENCRYPTION_OFF:
        if is_encrypted_path(path):
            plaintext_path = _plaintext_recording_sibling_for_encrypted_path(path)
            if plaintext_path is None:
                raise RuntimeError(f"encrypted recording artifact has no safe plaintext sibling: {path}")
            if _path_exists_or_is_symlink(plaintext_path):
                raise RuntimeError(
                    f"refusing to overwrite existing plaintext recording artifact during downgrade: {plaintext_path}"
                )
            source_stat = _recording_artifact_stat(path)
            if source_stat is None:
                raise RuntimeError(f"encrypted recording artifact is not a safe regular file: {path}")
            try:
                payload = read_decrypted_bytes_from_file(
                    path,
                    kind="recording",
                    field_name="recording audio file",
                    require_encrypted=True,
                )
                plaintext_path, _effective_mode = write_encrypted_bytes_atomically(
                    plaintext_path,
                    payload,
                    mode,
                    kind="recording",
                    field_name="recording audio file",
                )
                plaintext_stat = _recording_artifact_stat(plaintext_path)
                if plaintext_stat is None:
                    raise RuntimeError(f"plaintext recording artifact is not a safe regular file: {plaintext_path}")
            except ArtifactCryptoError as exc:
                raise RuntimeError(str(exc)) from exc
            if not remove_file(str(path), suffix=".socenc", expected_stat=source_stat):
                try:
                    plaintext_presence = _safe_leaf_presence(plaintext_path)
                    if plaintext_presence is None:
                        raise RuntimeError(f"failed to inspect plaintext recording artifact: {plaintext_path}")
                    if plaintext_presence and not remove_file(
                        str(plaintext_path),
                        suffix=plaintext_path.suffix.lower(),
                        expected_stat=plaintext_stat,
                    ):
                        raise RuntimeError(f"failed to roll back plaintext recording artifact: {plaintext_path}")
                except RuntimeError as cleanup_exc:
                    raise RuntimeError(
                        f"failed to remove encrypted recording artifact after plaintext storage: {path}"
                    ) from cleanup_exc
                raise RuntimeError(f"failed to remove encrypted recording artifact after plaintext storage: {path}")
            return plaintext_path, ARTIFACT_ENCRYPTION_OFF
        encrypted_sibling = encrypted_path_for(path)
        encrypted_sibling_present = _path_exists_or_is_symlink(encrypted_sibling)
        encrypted_sibling_stat = (
            _recording_artifact_stat(encrypted_sibling) if encrypted_sibling_present else None
        )
        if encrypted_sibling_present:
            if encrypted_sibling_stat is None or not remove_file(
                str(encrypted_sibling),
                suffix=".socenc",
                expected_stat=encrypted_sibling_stat,
            ):
                raise RuntimeError(
                    f"failed to remove encrypted recording sibling after plaintext storage: {encrypted_sibling}"
                )
        return path, ARTIFACT_ENCRYPTION_OFF
    encrypted_target_existed = False
    plaintext_cleanup_expected_stats: dict[Path, os.stat_result | None] = {}
    try:
        encrypted_target_path = encrypted_path_for(path)
        encrypted_target_existed = _path_exists_or_is_symlink(encrypted_target_path)
        plaintext_cleanup_expected_stats = {
            candidate: _recording_artifact_stat(candidate)
            for candidate in _plaintext_recording_cleanup_candidates(path, encrypted_target_path)
        }
        payload = read_decrypted_bytes_from_file(
            path,
            kind="recording",
            field_name="recording audio file",
            require_encrypted=True,
        ) if is_encrypted_path(path) else read_decrypted_bytes_from_file(
            path,
            kind="recording",
            field_name="recording audio file",
            max_bytes=None,
            require_encrypted=False,
        )
        encrypted_path, effective_mode = write_encrypted_bytes_atomically(
            path,
            payload,
            mode,
            kind="recording",
            field_name="recording audio file",
        )
        encrypted_artifact_stat = _recording_artifact_stat(encrypted_path)
    except ArtifactCryptoError as exc:
        raise RuntimeError(str(exc)) from exc
    try:
        _remove_plaintext_recording_sibling_after_encryption(
            path,
            encrypted_path,
            expected_stats=plaintext_cleanup_expected_stats,
        )
    except RuntimeError as exc:
        if not encrypted_target_existed:
            try:
                _rollback_encrypted_artifact_after_plaintext_cleanup_failure(
                    encrypted_path,
                    field_name="encrypted recording artifact",
                    expected_stat=encrypted_artifact_stat,
                )
            except BaseException as rollback_exc:
                _raise_encrypted_artifact_rollback_failure(exc, rollback_exc)
        raise
    return encrypted_path, effective_mode


_TRANSCRIPT_READ_EXCEPTIONS = (OSError, RuntimeError, ValueError, UnicodeDecodeError, ArtifactCryptoError)


def _transcript_read_failure(path: Path, exc: BaseException, *, reveal_metadata: bool = True) -> RuntimeError:
    name = _transcript_display_name(path) if reveal_metadata else HISTORY_METADATA_REDACTED_TEXT
    return RuntimeError(f"failed to read transcript {name}: {_redact_error_for_user(str(exc))}")


def _collect_transcript_history(
    limit: int = 10,
    *,
    include_text: bool = False,
) -> tuple[list[dict[str, object]], int]:
    if limit <= 0:
        return [], 0
    directory = transcript_dir()

    try:
        candidates = heapq.nlargest(
            max(MAX_TRANSCRIPT_HISTORY_SCAN, limit),
            _transcript_history_candidates(directory),
            key=lambda candidate: (candidate[0], str(candidate[1])),
        )
    except DirectoryScanError:
        return [], 1

    entries: list[dict[str, object]] = []
    unreadable_count = 0
    scanned_chars = 0
    for candidate in candidates:
        mtime, path = candidate[:2]
        expected_stat = candidate[2] if len(candidate) > 2 else None
        remaining_scan_chars = MAX_TRANSCRIPT_HISTORY_SCAN_CHARS - scanned_chars
        if remaining_scan_chars < 1:
            break
        try:
            read_kwargs: dict[str, object] = {"max_bytes": remaining_scan_chars}
            if expected_stat is not None:
                read_kwargs["expected_stat"] = expected_stat
            text = _read_stored_transcript_text(path, **read_kwargs).strip()
        except _TRANSCRIPT_READ_EXCEPTIONS:
            unreadable_count += 1
            continue
        scanned_chars += len(text)
        if scanned_chars > MAX_TRANSCRIPT_HISTORY_SCAN_CHARS:
            break
        if not text:
            continue
        modified_at = _transcript_modified_at(mtime)
        entry: dict[str, object] = {
            "path": str(path),
            "name": _transcript_display_name(path),
            "modified_at": modified_at,
            "preview": transcript_preview(text),
        }
        if include_text:
            entry["text"] = text
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries, unreadable_count


def _partition_transcript_cleanup_files(paths: list[Path]) -> tuple[list[Path], list[Path]]:
    readable_transcripts: list[Path] = []
    empty_transcripts: list[Path] = []
    for group in _group_transcript_artifacts(paths).values():
        group_has_content = False
        group_has_unreadable = False
        for path in group:
            try:
                if _read_stored_transcript_text(path).strip():
                    group_has_content = True
            except _TRANSCRIPT_READ_EXCEPTIONS:
                group_has_unreadable = True
        target = readable_transcripts if group_has_content or group_has_unreadable else empty_transcripts
        target.extend(group)
    return readable_transcripts, empty_transcripts


def read_transcript_history(limit: int = 10) -> list[dict[str, object]]:
    entries, _unreadable_count = _collect_transcript_history(limit)
    return entries


def build_transcripts_document(
    limit: int = MAX_HISTORY_LIMIT,
    *,
    max_chars: int | None = None,
    allow_truncate: bool = False,
    reveal_metadata: bool = True,
) -> tuple[str, int, bool]:
    if limit <= 0:
        limit = 0
    else:
        limit = min(limit, MAX_HISTORY_LIMIT)
    if max_chars is not None and (isinstance(max_chars, bool) or max_chars < 1):
        raise RuntimeError("transcript document size limit must be positive")
    directory = transcript_dir()
    candidates = (
        []
        if limit <= 0
        else heapq.nlargest(
            max(MAX_TRANSCRIPT_HISTORY_SCAN, limit),
            _transcript_history_candidates(directory),
            key=lambda candidate: (candidate[0], str(candidate[1])),
        )
    )
    lines = [
        "Speed of Cinnamon transcripts",
        f"Generated: {now_iso()}",
        "",
    ]
    count = 0
    truncated = False
    scanned_chars = 0

    def _current_text() -> str:
        return "\n".join(lines).rstrip() + "\n"

    for candidate in candidates:
        mtime, path = candidate[:2]
        expected_stat = candidate[2] if len(candidate) > 2 else None
        remaining_scan_chars = MAX_TRANSCRIPT_HISTORY_SCAN_CHARS - scanned_chars
        if remaining_scan_chars < 1:
            break
        try:
            read_kwargs: dict[str, object] = {"max_bytes": remaining_scan_chars}
            if expected_stat is not None:
                read_kwargs["expected_stat"] = expected_stat
            text = _read_stored_transcript_text(path, **read_kwargs).strip()
        except _TRANSCRIPT_READ_EXCEPTIONS as exc:
            raise _transcript_read_failure(path, exc, reveal_metadata=reveal_metadata) from exc
        if not text:
            continue
        scanned_chars += len(text)
        if scanned_chars > MAX_TRANSCRIPT_HISTORY_SCAN_CHARS:
            break
        display_text = _sanitize_transcript_display_text(text)
        display_name = _transcript_display_name(path)
        modified_at = _transcript_modified_at(mtime)
        entry = [
            f"===== {display_name} =====",
            f"Modified: {modified_at}",
            "",
            display_text,
            "",
        ]
        if max_chars is not None:
            candidate_text = "\n".join([*lines, *entry]).rstrip() + "\n"
            if len(candidate_text) > max_chars:
                truncated = True
                if allow_truncate:
                    lines.extend(
                        [
                            "===== transcript list truncated =====",
                            f"Stopped before {display_name} because the display limit was reached.",
                            "",
                        ]
                    )
                    break
                raise RuntimeError("transcript export is too large; reduce transcript retention or export fewer files")
        lines.extend(entry)
        count += 1
        if count >= limit:
            break
    return _current_text(), count, truncated


def _transcript_export_path(plaintext: bool) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(4)
    return state_dir() / "exports" / f"all-transcripts-{timestamp}-{suffix}.txt"


def _ensure_transcript_export_dir(path: Path) -> None:
    fd = ensure_directory_without_following_symlinks(path.parent, field_name="transcript export directory")
    try:
        os.fchmod(fd, 0o700)
    finally:
        try:
            os.close(fd)
        except BaseException:
            pass


def write_transcripts_export(
    limit: int = MAX_HISTORY_LIMIT,
    *,
    encryption_mode: object = "keyring",
    plaintext: bool = False,
    confirm_plaintext: bool = False,
) -> tuple[Path, int, str]:
    plaintext = _coerce_bool(plaintext, field_name="plaintext transcript export")
    confirm_plaintext = _coerce_bool(confirm_plaintext, field_name="confirm_plaintext")
    if plaintext:
        if not confirm_plaintext:
            raise RuntimeError("plaintext transcript export requires --confirm-plaintext")
    else:
        mode = normalize_artifact_encryption(encryption_mode)
        if mode == ARTIFACT_ENCRYPTION_OFF:
            raise RuntimeError("encrypted transcript export requires keyring or passphrase; use --plaintext --confirm-plaintext for plaintext export")
    content, count, _truncated = build_transcripts_document(
        limit,
        max_chars=MAX_TRANSCRIPTS_EXPORT_CHARS,
        allow_truncate=False,
        reveal_metadata=plaintext and confirm_plaintext,
    )
    output_path = _transcript_export_path(plaintext)
    _ensure_transcript_export_dir(output_path)
    if plaintext:
        _write_text_atomic(output_path, content)
        return output_path, count, ARTIFACT_ENCRYPTION_OFF
    plaintext_present = _path_exists_or_is_symlink(output_path)
    plaintext_stat = _recording_artifact_stat(output_path) if plaintext_present else None
    encrypted_target_existed = _path_exists_or_is_symlink(encrypted_path_for(output_path))
    encrypted_path, used_mode = write_encrypted_bytes_atomically(
        output_path,
        content.encode("utf-8"),
        mode,
        kind="transcript",
        field_name="transcript export",
    )
    encrypted_artifact_stat = _recording_artifact_stat(encrypted_path)
    try:
        _remove_plaintext_export_sibling_after_encryption(
            output_path,
            encrypted_path,
            expected_stat=plaintext_stat,
            expected_present=plaintext_present,
        )
    except RuntimeError as exc:
        if not encrypted_target_existed:
            try:
                _rollback_encrypted_artifact_after_plaintext_cleanup_failure(
                    encrypted_path,
                    field_name="encrypted transcript export",
                    expected_stat=encrypted_artifact_stat,
                )
            except BaseException as rollback_exc:
                _raise_encrypted_artifact_rollback_failure(exc, rollback_exc)
        raise
    return encrypted_path, count, used_mode


def normalized_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    return _coerce_path(path_value, field_name="path", resolve=True)


def _normalized_state_artifact_path(path_value: str | None, *, state_path: Path | None = None) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    base_dir = state_path.parent if state_path is not None else Path.cwd()
    was_relative = not path.is_absolute()
    normalized = path if not was_relative else base_dir / path
    try:
        assert_no_symlink_ancestors(normalized, field_name="state artifact path")
    except (OSError, RuntimeError):
        return None
    normalized = normalized.resolve(strict=False)
    if was_relative and state_path is not None and not normalized.is_relative_to(base_dir.resolve(strict=False)):
        return None
    return normalized


def _normalized_state_recording_artifact_path(
    path_value: str | None,
    *,
    suffix: str | tuple[str, ...],
    state_path: Path | None = None,
    require_recordings_dir: bool = True,
) -> Path | None:
    if not path_value:
        return None
    path = Path(path_value).expanduser()
    base_dir = state_path.parent if state_path is not None else Path.cwd()
    was_relative = not path.is_absolute()
    path = path if not was_relative else base_dir / path
    try:
        assert_no_symlink_ancestors(path, field_name="state recording artifact path")
    except (OSError, RuntimeError):
        return None
    path = path.resolve(strict=False)
    if was_relative and state_path is not None and not path.is_relative_to(base_dir.resolve(strict=False)):
        return None
    try:
        return validate_recording_path(
            path,
            suffix=suffix,
            require_recordings_dir=require_recordings_dir,
        )
    except (RecorderError, ValueError, OSError, TypeError):
        return None


def _protected_unverified_state_recording_artifact_path(
    path_value: str | None,
    *,
    suffix: str | tuple[str, ...],
    state_path: Path | None = None,
    require_recordings_dir: bool = True,
) -> Path | None:
    if (
        not path_value
        or isinstance(path_value, bool)
        or not isinstance(path_value, str)
        or _contains_escaped_null(path_value)
        or _contains_http_header_control_chars(path_value)
    ):
        return None
    try:
        path = Path(path_value).expanduser()
        base_dir = state_path.parent if state_path is not None else Path.cwd()
        was_relative = not path.is_absolute()
        normalized = Path(os.path.abspath(os.fspath(path if not was_relative else base_dir / path)))
        allowed_suffixes = (suffix,) if isinstance(suffix, str) else suffix
        if not normalized.name.lower().endswith(tuple(item.lower() for item in allowed_suffixes)):
            return None
        if was_relative and state_path is not None:
            normalized_base = Path(os.path.abspath(os.fspath(base_dir)))
            if not normalized.is_relative_to(normalized_base):
                return None
        if require_recordings_dir:
            normalized_recordings_dir = Path(os.path.abspath(os.fspath(recordings_dir())))
            if not normalized.is_relative_to(normalized_recordings_dir):
                return None
        return normalized
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _assert_json_payload_size(payload: dict[str, object], *, max_bytes: int) -> None:
    try:
        rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (MemoryError, RecursionError) as exc:
        raise RuntimeError("output JSON could not be rendered") from exc
    if len(rendered.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"output JSON is too large (max {max_bytes} bytes)")


def _write_json_atomic(path: Path, payload: dict[str, object], *, max_bytes: int) -> None:
    try:
        content = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    except (MemoryError, RecursionError) as exc:
        raise RuntimeError("output JSON could not be rendered") from exc
    if len(content.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"output JSON is too large (max {max_bytes} bytes)")
    try:
        write_text_atomically_without_following_symlinks(path, content, field_name="JSON output path")
    except (OSError, RuntimeError, MemoryError) as exc:
        raise RuntimeError(f"failed to write JSON output: {path}") from exc


def _harden_diagnostics_artifacts() -> None:
    """Make legacy diagnostics reports private without following symlinks."""
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise RuntimeError("secure diagnostics artifact open is not supported on this platform")
    directory_fd = open_directory_without_following_symlinks(
        diagnostics_dir(),
        field_name="diagnostics directory",
    )
    primary_error: BaseException | None = None
    try:
        try:
            with os.scandir(directory_fd) as directory_entries:
                scanned_entries = 0
                for directory_entry in directory_entries:
                    if scanned_entries >= MAX_DIRECTORY_SCAN_ENTRIES:
                        raise RuntimeError("diagnostics directory contains too many entries")
                    scanned_entries += 1
                    name = directory_entry.name
                    if not isinstance(name, str) or not name.startswith("diagnostics-") or not name.endswith(".json"):
                        continue
                    try:
                        artifact_fd = os.open(
                            name,
                            os.O_RDONLY | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                            dir_fd=directory_fd,
                        )
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise RuntimeError(f"diagnostics artifact could not be opened safely: {name}") from exc
                    try:
                        assert_fd_is_regular_private_file(artifact_fd, field_name="diagnostics artifact")
                        os.fchmod(artifact_fd, 0o600)
                        assert_fd_is_regular_private_file(
                            artifact_fd,
                            field_name="diagnostics artifact",
                            require_private_mode=True,
                        )
                    except OSError as exc:
                        raise RuntimeError(f"diagnostics artifact could not be made private: {name}") from exc
                    finally:
                        try:
                            os.close(artifact_fd)
                        except OSError:
                            pass
        except OSError as exc:
            raise RuntimeError("diagnostics directory could not be inspected") from exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(directory_fd)
        except OSError:
            pass
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def _write_text_atomic(path: Path, text: str) -> None:
    try:
        write_text_atomically_without_following_symlinks(path, text, field_name="text output path")
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"failed to write transcript file: {path}") from exc


class _PrivateFilePrepareError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        created: bool,
        errno_value: int | None = None,
        created_stat: os.stat_result | None = None,
    ) -> None:
        super().__init__(message)
        self.created = created
        self.errno = errno_value
        self.created_stat = created_stat


def _prepare_private_file(
    path: Path,
    *,
    field_name: str,
    exclusive: bool = True,
    _keep_fd: bool = False,
) -> tuple[int, os.stat_result] | None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    assert_safe_path_components(path, field_name=field_name)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise RuntimeError(f"secure {field_name} open is not supported on this platform")
    try:
        parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
    except OSError as exc:
        raise _PrivateFilePrepareError(f"failed to prepare {field_name}: {path}", created=False, errno_value=exc.errno) from exc
    try:
        flags = os.O_WRONLY | os.O_CREAT | nofollow_flag
        if exclusive:
            flags |= os.O_EXCL
        else:
            flags |= os.O_APPEND
        fd = os.open(path.name, flags, 0o600, dir_fd=parent_fd)
    except OSError as exc:
        raise _PrivateFilePrepareError(f"failed to prepare {field_name}: {path}", created=False, errno_value=exc.errno) from exc
    finally:
        try:
            os.close(parent_fd)
        except BaseException:
            pass
    created_stat: os.stat_result | None = None
    if exclusive or _keep_fd:
        try:
            created_stat = os.fstat(fd)
        except OSError as exc:
            try:
                os.close(fd)
            except BaseException:
                pass
            raise _PrivateFilePrepareError(
                f"failed to prepare {field_name}: {path}",
                created=True,
                errno_value=exc.errno,
            ) from exc
        except BaseException as exc:
            try:
                setattr(exc, "_speed_of_cinnamon_created_stat", created_stat)
            except BaseException:
                pass
            try:
                os.close(fd)
            except BaseException:
                pass
            raise
    if _keep_fd:
        try:
            os.fchmod(fd, 0o600)
            created_stat = os.fstat(fd)
        except (OSError, ValueError) as exc:
            try:
                os.close(fd)
            except BaseException:
                pass
            raise _PrivateFilePrepareError(
                f"failed to prepare {field_name}: {path}",
                created=exclusive,
                errno_value=getattr(exc, "errno", None),
                created_stat=created_stat,
            ) from exc
        except BaseException as exc:
            if exclusive:
                try:
                    setattr(exc, "_speed_of_cinnamon_created_stat", created_stat)
                except BaseException:
                    pass
            try:
                os.close(fd)
            except BaseException:
                pass
            raise
        result = (fd, created_stat)
        fd = -1
        return result
    try:
        with os.fdopen(fd, "ab") as handle:
            os.fchmod(handle.fileno(), 0o600)
    except (OSError, ValueError) as exc:
        try:
            os.close(fd)
        except BaseException:
            pass
        raise _PrivateFilePrepareError(
            f"failed to prepare {field_name}: {path}",
            created=exclusive,
            errno_value=getattr(exc, "errno", None),
            created_stat=created_stat,
        ) from exc
    except BaseException as exc:
        if exclusive:
            try:
                setattr(exc, "_speed_of_cinnamon_created_stat", created_stat)
            except BaseException:
                pass
        try:
            os.close(fd)
        except BaseException:
            pass
        raise


def _allocate_recording_artifacts() -> tuple[Path, Path]:
    root = recordings_dir()
    candidates_checked = 0
    while candidates_checked < MAX_RECORDING_ARTIFACT_CANDIDATES:
        base_stem = timestamp()
        for collision_index in range(MAX_RECORDING_ARTIFACT_CANDIDATES - candidates_checked):
            stem = base_stem if collision_index == 0 else f"{base_stem}-{collision_index:02d}"
            audio_path = validate_recording_path(
                root / f"{stem}.wav",
                suffix=".wav",
                require_recordings_dir=True,
                recordings_root=root,
            )
            log_path = validate_recording_path(
                root / f"{stem}.log",
                suffix=".log",
                require_recordings_dir=True,
                recordings_root=root,
            )
            candidates_checked += 1
            if _recording_artifact_stat(audio_path) is not None or _recording_artifact_stat(log_path) is not None:
                continue
            try:
                _prepare_private_file(audio_path, field_name="recording audio file")
            except _PrivateFilePrepareError as exc:
                if exc.created:
                    if exc.created_stat is None or not remove_file(
                        str(audio_path),
                        suffix=".wav",
                        recordings_root=root,
                        expected_stat=exc.created_stat,
                    ):
                        raise RuntimeError(f"failed to clean partial recording audio file: {audio_path}") from None
                    if _recording_artifact_stat(audio_path) is not None:
                        continue
                    break
                if exc.errno == errno.EEXIST and _recording_artifact_stat(audio_path) is not None:
                    continue
                raise
            else:
                return audio_path, log_path
    raise RuntimeError("failed to allocate collision-free recording artifacts")


def _remove_transcript_file(path: Path, *, expected_stat: os.stat_result | None = None) -> bool:
    if not isinstance(path, Path) or not _is_transcript_artifact(path):
        raise RuntimeError("transcript path must be a .txt or .txt.socenc path")
    try:
        assert_safe_path_components(path, field_name="transcript file")
        assert_no_symlink_ancestors(path, field_name="transcript file")
        path.resolve(strict=False).relative_to(transcript_dir().resolve(strict=False))
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError(f"refusing to delete transcript outside transcript directory: {path}") from None
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(f"failed to delete transcript file: {path}") from exc
    if not stat_module.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"transcript file must be a regular file: {path}")
    if getattr(file_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"transcript file must not be hardlinked: {path}")
    try:
        return _unlink_regular_leaf_with_parent_fsync(
            path,
            field_name="transcript file",
            expected_stat=expected_stat or file_stat,
        )
    except FileNotFoundError:
        return False
    except RuntimeError as exc:
        raise RuntimeError(f"failed to delete transcript file: {path}") from exc


def _require_json_path(
    path_value: str,
    *,
    field_name: str,
    default: Path | None = None,
    max_chars: int = MAX_PATH_CHARS,
) -> Path:
    if path_value:
        path = _coerce_path(path_value, field_name=field_name, resolve=False, max_chars=max_chars)
        if not path.is_absolute():
            path = Path.cwd() / path
    elif default is not None:
        path = default
    else:
        raise RuntimeError(f"{field_name} is required")
    path = path.expanduser()
    if len(str(path)) > max_chars:
        raise RuntimeError(f"{field_name} is too large (max {max_chars} characters)")
    if path.suffix.lower() != ".json":
        raise RuntimeError(f"{field_name} must end with .json")
    return path


def _settings_json_path_limit(path_value: str) -> int:
    if not path_value:
        return MAX_SETTINGS_EXPORT_PATH_CHARS
    return MAX_SETTINGS_EXPORT_PATH_CHARS if Path(path_value).expanduser().is_absolute() else MAX_PATH_CHARS


def _parse_cli_settings_json(raw: str) -> dict[str, object]:
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise RuntimeError("settings JSON must be text")
    if len(raw) > MAX_SETTINGS_JSON_CHARS:
        raise RuntimeError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} characters)")
    try:
        return parse_settings_json(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings JSON could not be parsed: {exc}") from exc


def _settings_json_from_args(args: argparse.Namespace) -> dict[str, object]:
    if _coerce_bool(getattr(args, "settings_json_stdin", False), field_name="settings_json_stdin"):
        if str(getattr(args, "settings_json", "{}") or "{}") != "{}":
            raise RuntimeError("settings JSON must be provided by either --settings-json or stdin, not both")
        raw = sys.stdin.read(MAX_SETTINGS_JSON_CHARS + 1)
        return _parse_cli_settings_json(raw or "{}")
    settings = _parse_cli_settings_json(getattr(args, "settings_json", "{}"))
    from .settings_export import NON_EXPORTABLE_PRIVATE_SETTINGS

    if any(key in settings for key in NON_EXPORTABLE_PRIVATE_SETTINGS):
        raise RuntimeError("private settings must be provided via --settings-json-stdin, not --settings-json")
    return settings


def _coerce_path(
    path_value: str,
    *,
    field_name: str,
    resolve: bool = False,
    max_chars: int = MAX_PATH_CHARS,
) -> Path:
    if isinstance(path_value, bool) or not isinstance(path_value, str):
        raise RuntimeError(f"{field_name} must be text")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise RuntimeError("max_chars must be an integer")
    if max_chars <= 0:
        raise RuntimeError("max_chars must be positive")
    _assert_clean_text(path_value, field_name=field_name, max_chars=max_chars)
    path = Path(path_value).expanduser()
    assert_no_symlink_ancestors(path, field_name=field_name)
    return path.resolve(strict=False) if resolve else path


def _coerce_int(
    value: int,
    *,
    field_name: str,
    min_value: int = 0,
    max_value: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"{field_name} must be an integer")
    if not isinstance(min_value, int) or isinstance(min_value, bool):
        raise RuntimeError("min_value must be an integer")
    if max_value is not None:
        if not isinstance(max_value, int) or isinstance(max_value, bool):
            raise RuntimeError("max_value must be an integer")
        if max_value < min_value:
            raise RuntimeError(f"{field_name} has invalid max_value")
    if value < min_value:
        raise RuntimeError(f"{field_name} must be at least {min_value}")
    if max_value is not None and value > max_value:
        raise RuntimeError(f"{field_name} must be at most {max_value}")
    return value


def _coerce_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{field_name} must be a boolean")
    return value


def _normalize_input_sources(sources: object) -> list[dict[str, object]]:
    if not isinstance(sources, list):
        raise RuntimeError("input sources must be a list")

    def normalize_source_text(value: object, *, field_name: str, max_chars: int = MAX_INPUT_SOURCE_FIELD_CHARS) -> str:
        if not isinstance(value, str) or isinstance(value, bool):
            raise RuntimeError(f"{field_name} must be text")
        if _contains_escaped_null(value):
            raise RuntimeError(f"{field_name} contains invalid null byte")
        if _contains_http_header_control_chars(value):
            raise RuntimeError(f"{field_name} contains invalid control character")
        try:
            encoded_value = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RuntimeError(f"{field_name} contains invalid UTF-8") from exc
        if len(value) > max_chars or len(encoded_value) > max_chars:
            raise RuntimeError(f"{field_name} is too long (max {max_chars} bytes)")
        return value

    normalized: list[dict[str, object]] = []
    for source in sources:
        source_id = source.id if hasattr(source, "id") else None
        source_id = normalize_source_text(
            source_id,
            field_name="input source id",
            max_chars=MAX_RECORDING_INPUT_DEVICE_CHARS,
        )

        name = source.name if hasattr(source, "name") else None
        name = normalize_source_text(
            name,
            field_name="input source name",
            max_chars=MAX_RECORDING_INPUT_DEVICE_CHARS,
        )

        description = source.description if hasattr(source, "description") else None
        description = normalize_source_text(description, field_name="input source description")

        driver = source.driver if hasattr(source, "driver") else None
        driver = normalize_source_text(driver, field_name="input source driver")

        state = source.state if hasattr(source, "state") else None
        state = normalize_source_text(state, field_name="input source state")

        default = source.default if hasattr(source, "default") else None
        if not isinstance(default, bool):
            raise RuntimeError("input source default must be a boolean")

        monitor = source.monitor if hasattr(source, "monitor") else None
        if not isinstance(monitor, bool):
            raise RuntimeError("input source monitor must be a boolean")

        normalized.append(
            {
                "id": source_id,
                "name": name,
                "description": description,
                "driver": driver,
                "state": state,
                "default": default,
                "monitor": monitor,
            }
        )
    return normalized


def _normalize_model_payloads(models: object) -> list[dict[str, object]]:
    if not isinstance(models, list):
        raise RuntimeError("model payload must be a list")

    normalized: list[dict[str, object]] = []
    for model in models:
        if not isinstance(model, dict):
            raise RuntimeError("model payload entry must be an object")
        name = model.get("name")
        if not isinstance(name, str) or isinstance(name, bool):
            raise RuntimeError("model name must be text")
        if _contains_escaped_null(name):
            raise RuntimeError("model name contains invalid null byte")
        if _contains_http_header_control_chars(name):
            raise RuntimeError("model name contains invalid control character")
        normalized.append(model)
    return normalized


def _redact_model_payload_paths(models: object) -> list[dict[str, object]]:
    redacted: list[dict[str, object]] = []
    for model in _normalize_model_payloads(models):
        redacted.append(_redact_model_payload_path(model))
    return redacted


def _redact_model_payload_path(model: dict[str, object]) -> dict[str, object]:
    model_payload = dict(model)
    path_value = model_payload.pop("path", "")
    model_payload["path_present"] = bool(path_value)
    return model_payload


def _normalize_text_models_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise RuntimeError("text models payload must be an object")
    available = payload.get("available")
    if not isinstance(available, bool):
        raise RuntimeError("text models payload available must be a boolean")
    message = payload.get("message")
    if not isinstance(message, str) or isinstance(message, bool):
        raise RuntimeError("text models payload message must be text")
    if _contains_escaped_null(message):
        raise RuntimeError("text models payload message contains invalid null byte")
    if _contains_http_header_control_chars(message):
        raise RuntimeError("text models payload message contains invalid control character")
    models = _normalize_model_payloads(payload.get("models"))
    return {
        "available": available,
        "models": models,
        "message": message,
    }


def active_artifact_paths(
    state: RecordingState,
    *,
    state_path: Path | None = None,
    include_finalizing_inflight: bool = True,
) -> set[Path]:
    paths: set[Path] = set()
    audio_path = _normalized_state_recording_artifact_path(
        state.audio_path,
        suffix=(".wav", ".flac", ".socenc"),
        state_path=state_path,
        require_recordings_dir=True,
    )
    if audio_path is None:
        audio_path = _protected_unverified_state_recording_artifact_path(
            state.audio_path,
            suffix=(".wav", ".flac", ".socenc"),
            state_path=state_path,
            require_recordings_dir=True,
        )
    audio_candidates: list[Path] = []
    if audio_path is not None:
        audio_candidates.append(audio_path)
        sibling_path = _recording_sibling_path(audio_path)
        if sibling_path is not None:
            audio_candidates.append(sibling_path)
    log_path = _normalized_state_recording_artifact_path(
        state.log_path,
        suffix=".log",
        state_path=state_path,
        require_recordings_dir=True,
    )
    if log_path is None:
        log_path = _protected_unverified_state_recording_artifact_path(
            state.log_path,
            suffix=".log",
            state_path=state_path,
            require_recordings_dir=True,
        )
    for candidate in audio_candidates:
        paths.add(candidate)
    if log_path:
        paths.add(log_path)
    path = _normalized_state_artifact_path(state.transcript_path, state_path=state_path)
    if path:
        paths.add(path)
        sibling_path = _transcript_sibling_path(path)
        if sibling_path is not None:
            paths.add(sibling_path)
    if state_path is not None and state.status == "finalizing" and include_finalizing_inflight:
        paths.update(_finalizing_inflight_artifact_paths(state_path, state))
    return paths


def _enforce_recording_artifact_cap(
    state: RecordingState | None,
    active_paths: set[Path] | None = None,
    *,
    state_path: Path | None = None,
) -> dict[str, object]:
    if state is None:
        return {"planned_paths": [], "deleted_paths": [], "failed_paths": [], "skipped_active_paths": []}
    active_paths = set(active_artifact_paths(state, state_path=state_path)) | (active_paths or set())
    artifact_stats: dict[Path, os.stat_result] = {}
    try:
        artifact_files = recording_artifact_files(expected_stats=artifact_stats)
    except DirectoryScanError as exc:
        return {
            "planned_paths": [],
            "deleted_paths": [],
            "failed_paths": [str(exc.directory)],
            "skipped_active_paths": [],
        }
    return prune_files_by_mtime(
        artifact_files,
        MAX_TEMP_RECORDING_FILES,
        active_paths,
        dry_run=False,
        expected_stats=artifact_stats,
    )


def _safe_recording_artifact_path(
    value: str | None,
    *,
    suffix: str | tuple[str, ...],
    require_recordings_dir: bool = True,
) -> Path | None:
    if not value:
        return None
    try:
        path = Path(value)
        if path.name.lower().endswith(ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES):
            suffixes = (suffix,) if isinstance(suffix, str) else suffix
            if ".socenc" not in suffixes:
                return None
            return validate_recording_path(path, suffix=".socenc", require_recordings_dir=require_recordings_dir)
        return validate_recording_path(path, suffix=suffix, require_recordings_dir=require_recordings_dir)
    except (RecorderError, ValueError, OSError, TypeError):
        return None


def _is_recording_process_alive(pid: object) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    return process_is_alive(pid)


def _recording_process_identity_for_pid(pid: int) -> str | None:
    return _finalization_lock_identity_for_pid(pid)


def _recorder_scope_for_process(process: object) -> str | None:
    value = vars(process).get("_soc_recorder_scope") if hasattr(process, "__dict__") else None
    if isinstance(value, str) and value:
        return value
    return None


def _recorder_start_cleanup_is_incomplete(error: BaseException) -> bool:
    pending: list[BaseException | None] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        try:
            ownership = recorder_startup_ownership(current)
        except BaseException:
            ownership = None
        if ownership is not None and ownership.cleanup_incomplete:
            return True
        if any("recorder process cleanup was incomplete" in note for note in getattr(current, "__notes__", ())):
            return True
        pending.append(getattr(current, "__cause__", None))
        pending.append(getattr(current, "__context__", None))
    return False


def _retain_incomplete_recorder_startup_lock(
    error: BaseException,
    finalization_lock_path: Path | None,
    *,
    lock_release_state: dict[str, bool] | None = None,
) -> bool:
    retained = False
    try:
        ownership = recorder_startup_ownership(error)
        if ownership is not None and ownership.cleanup_incomplete and ownership.pid is not None:
            retention_kwargs: dict[str, str | None] = {
                "recorder_scope": ownership.recorder_scope,
            }
            if ownership.recorder_scope_unit is not None:
                retention_kwargs["recorder_scope_unit"] = ownership.recorder_scope_unit
            retained = _retain_finalization_lock_for_process(
                finalization_lock_path,
                ownership.pid,
                ownership.process_identity,
                **retention_kwargs,
            )
            if (
                not retained
                and ownership.recorder_scope is not None
                and ownership.recorder_scope_unit is not None
            ):
                retained = _retain_finalization_lock_for_process(
                    finalization_lock_path,
                    ownership.pid,
                    ownership.process_identity,
                    recorder_scope_unit=ownership.recorder_scope_unit,
                )
    except BaseException:
        retained = False
    if not retained:
        error.add_note("recorder lifecycle lock could not be retained")
        if lock_release_state is not None:
            lock_release_state["release"] = False
    return retained


_RECORDING_PROCESS_IDENTITY_INVALID_ERROR = (
    "recording process identity is missing or invalid; recording state preserved"
)
_RECORDING_PROCESS_GROUP_ACTIVE_ERROR = (
    "recording process group is still active; recording state preserved"
)


def _validated_recording_process_identity(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, str):
        return None
    if not value or len(value) > 256:
        return None
    if value != value.strip(" \t"):
        return None
    if any(not 0x20 <= ord(character) <= 0x7E for character in value):
        return None
    return value


def _recording_process_identity_for_lifecycle(value: object) -> str | None:
    """Validate identity while retaining legacy blank-as-missing semantics."""
    identity = _validated_recording_process_identity(value)
    if identity is not None:
        return identity
    if value is None or value == "":
        return ""
    return None


_RECORDING_PROCESS_IDENTITY_ABSENT = "absent"
_RECORDING_PROCESS_IDENTITY_PRESENT = "present"
_RECORDING_PROCESS_IDENTITY_UNKNOWN = "unknown"


def _recording_process_identity_probe(pid: int) -> tuple[str | None, str]:
    try:
        identity = _recording_process_identity_for_pid(pid)
    except (FileNotFoundError, ProcessLookupError):
        return None, _RECORDING_PROCESS_IDENTITY_ABSENT
    except Exception:
        return None, _RECORDING_PROCESS_IDENTITY_UNKNOWN
    if isinstance(identity, str) and identity:
        return identity, _RECORDING_PROCESS_IDENTITY_PRESENT
    try:
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError):
        return None, _RECORDING_PROCESS_IDENTITY_ABSENT
    except (OSError, OverflowError, ValueError):
        return None, _RECORDING_PROCESS_IDENTITY_UNKNOWN
    return None, _RECORDING_PROCESS_IDENTITY_UNKNOWN


def _recording_process_identity_is_stably_absent(pid: int) -> bool:
    for attempt in range(2):
        _identity, identity_status = _recording_process_identity_probe(pid)
        if identity_status != _RECORDING_PROCESS_IDENTITY_ABSENT:
            return False
        if attempt == 0:
            time.sleep(RECORDER_PROCESS_RECONCILIATION_DELAY_SECONDS)
    return True


def _recording_process_verified_alive(state: RecordingState) -> bool:
    pid = state.pid
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    expected_identity = _recording_process_identity_for_lifecycle(state.process_identity)
    if expected_identity is None:
        raise RuntimeError(_RECORDING_PROCESS_IDENTITY_INVALID_ERROR)
    try:
        leader_alive = process_is_alive(pid)
    except Exception:
        raise RuntimeError(
            "recording process liveness could not be verified; refusing to signal pid"
        ) from None
    if not leader_alive:
        return False
    if not expected_identity:
        raise RuntimeError(_RECORDING_PROCESS_IDENTITY_INVALID_ERROR)
    try:
        current_identity = _recording_process_identity_for_pid(pid)
    except Exception:
        raise RuntimeError(
            "recording process identity could not be verified; refusing to signal pid"
        ) from None
    if current_identity is None:
        raise RuntimeError("recording process identity could not be verified; refusing to signal pid")
    return (
        _recording_process_identity_relation(
            pid,
            expected_identity,
            current_identity,
        )
        == _PROCESS_IDENTITY_SAME
    )


def _recording_process_verified_active(state: RecordingState) -> bool:
    if not _recording_process_verified_alive(state):
        if not state.recorder_scope:
            expected_identity = _recording_process_identity_for_lifecycle(state.process_identity)
            if expected_identity is None:
                raise RuntimeError(_RECORDING_PROCESS_IDENTITY_INVALID_ERROR)
            stable_absence, absence_error = _recording_process_stable_absence(
                state.pid,
                expected_identity,
            )
            if absence_error is not None:
                raise RuntimeError(absence_error)
            if stable_absence:
                return False
            raise RuntimeError(
                _RECORDING_PROCESS_GROUP_ACTIVE_ERROR
            )
        scope_gone = _recorder_scope_is_stably_gone(state.recorder_scope)
        if scope_gone is None:
            raise RuntimeError(
                "recording ownership scope could not be verified; recording state preserved"
            )
        return not scope_gone
    if not state.recorder_scope:
        try:
            is_zombie = _process_is_zombie(state.pid)
        except Exception:
            raise RuntimeError(
                "recording process liveness could not be verified; recording state preserved"
            ) from None
        if is_zombie is None:
            raise RuntimeError(
                "recording process liveness could not be verified; recording state preserved"
            )
        if is_zombie is False:
            return True
        try:
            group_live = process_group_has_live_processes(state.pid)
        except Exception:
            raise RuntimeError(
                "recording process liveness could not be verified; recording state preserved"
            ) from None
        return group_live is not False
    if not _recorder_scope_is_current(state.pid, state.recorder_scope):
        raise RuntimeError(
            "recording ownership scope could not be verified; recording state preserved"
        )
    scope_gone = _recorder_scope_is_stably_gone(state.recorder_scope)
    if scope_gone is None:
        raise RuntimeError(
            "recording ownership scope could not be verified; recording state preserved"
        )
    return not scope_gone


def _recorder_scope_is_stably_gone(scope: str) -> bool | None:
    if _validated_finalization_lock_scope(scope) != scope:
        return None
    first = _recorder_scope_has_live_processes(scope)
    if first is None:
        return None
    if first:
        return False
    time.sleep(RECORDER_PROCESS_RECONCILIATION_DELAY_SECONDS)
    second = _recorder_scope_has_live_processes(scope)
    if second is None:
        return None
    return second is False


def _recorder_process_liveness_snapshot(
    process: subprocess.Popen[bytes],
    *,
    expected_recorder_scope: str | None = None,
) -> tuple[bool, str, str | None]:
    if expected_recorder_scope is None:
        expected_recorder_scope = vars(process).get("_soc_recorder_scope")
    if expected_recorder_scope is not None and (
        not isinstance(expected_recorder_scope, str)
        or isinstance(expected_recorder_scope, bool)
        or not expected_recorder_scope
    ):
        return (
            False,
            "recording ownership scope could not be verified; recording state preserved",
            None,
        )
    try:
        if process.poll() is None:
            return False, "recording process could not be stopped safely", expected_recorder_scope
        if expected_recorder_scope is not None:
            scope_gone = _recorder_scope_is_stably_gone(expected_recorder_scope)
            if scope_gone is True:
                return (
                    True,
                    "recording process has exited; stop confirmation was unavailable",
                    expected_recorder_scope,
                )
            if scope_gone is None:
                return (
                    False,
                    "recording ownership scope could not be verified; recording state preserved",
                    expected_recorder_scope,
                )
            return False, _RECORDING_PROCESS_GROUP_ACTIVE_ERROR, expected_recorder_scope
        group_live = process_group_has_live_processes(process.pid)
    except Exception:
        return (
            False,
            "recording process liveness could not be verified; recording state preserved",
            expected_recorder_scope,
        )
    if group_live is True:
        return False, _RECORDING_PROCESS_GROUP_ACTIVE_ERROR, expected_recorder_scope
    if group_live is None:
        return (
            False,
            "recording process liveness could not be verified; recording state preserved",
            expected_recorder_scope,
        )
    if group_live is False:
        return True, "recording process has exited; stop confirmation was unavailable", expected_recorder_scope
    return False, "recording process could not be stopped safely", expected_recorder_scope


def _recorder_process_is_gone(process: subprocess.Popen[bytes]) -> bool:
    return _recorder_process_liveness_snapshot(process)[0]


def _recorder_process_stop_failure_message(
    process: subprocess.Popen[bytes],
    *,
    liveness_snapshot: tuple[bool, str, str | None] | None = None,
) -> str:
    """Return a precise fail-closed message after recorder cleanup failed."""
    if liveness_snapshot is None:
        liveness_snapshot = _recorder_process_liveness_snapshot(process)
    return liveness_snapshot[1]


def _recorder_process_liveness_snapshot_for_failure(
    process: subprocess.Popen[bytes],
    *,
    finalization_lock_path: Path | None,
    process_identity: str | None,
    expected_recorder_scope: str | None = None,
    lock_release_state: dict[str, bool] | None = None,
) -> tuple[bool, str, str | None]:
    try:
        return _recorder_process_liveness_snapshot(
            process,
            expected_recorder_scope=expected_recorder_scope,
        )
    except BaseException as control_flow_error:
        try:
            retained = _retain_finalization_lock_for_process(
                finalization_lock_path,
                process.pid,
                process_identity,
                recorder_scope=expected_recorder_scope,
            )
        except BaseException:
            retained = False
        if not retained:
            control_flow_error.add_note("recorder lifecycle lock could not be retained")
            if lock_release_state is not None:
                lock_release_state["release"] = False
        raise


def _recording_process_group_is_active(pid: int | None) -> bool | None:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        group_live = process_group_has_live_processes(pid)
    except Exception:
        return None
    if group_live is None:
        return None
    return bool(group_live)


def _recording_process_absence_probe(pid: int) -> tuple[str | None, bool | None, str]:
    current_identity, identity_status = _recording_process_identity_probe(pid)
    if identity_status == _RECORDING_PROCESS_IDENTITY_UNKNOWN:
        return None, None, identity_status
    try:
        group_live = process_group_has_live_processes(pid)
    except Exception:
        return current_identity, None, identity_status
    return current_identity, group_live, identity_status


def _recording_process_stable_absence(
    pid: int,
    expected_identity: str,
    *,
    allow_matching_identity: bool = False,
) -> tuple[bool, str | None]:
    """Return stable leader/group absence, or an explicit fail-closed error."""
    if (
        _recording_process_identity_relation(
            pid,
            expected_identity,
            expected_identity,
        )
        != _PROCESS_IDENTITY_SAME
    ):
        return False, _RECORDING_PROCESS_IDENTITY_INVALID_ERROR
    current_identity, group_live, identity_status = (
        _recording_process_absence_probe(pid)
    )
    if identity_status == _RECORDING_PROCESS_IDENTITY_UNKNOWN:
        return False, "recording process identity could not be verified; recording state preserved"
    if identity_status == _RECORDING_PROCESS_IDENTITY_PRESENT:
        identity_relation = _recording_process_identity_relation(
            pid,
            expected_identity,
            current_identity,
        )
        if identity_relation == _PROCESS_IDENTITY_REUSED:
            return False, "recording process identity does not match; recording state preserved"
        if (
            not allow_matching_identity
            or not expected_identity
            or identity_relation != _PROCESS_IDENTITY_SAME
        ):
            return False, "recording process liveness could not be verified; recording state preserved"
        if group_live is None:
            return False, "recording process liveness could not be verified; recording state preserved"
        if group_live is True:
            return False, None
    if group_live is None:
        return False, "recording process liveness could not be verified; recording state preserved"
    if group_live is True:
        return False, None

    time.sleep(RECORDER_PROCESS_RECONCILIATION_DELAY_SECONDS)
    second_identity, second_group_live, second_identity_status = (
        _recording_process_absence_probe(pid)
    )
    if second_identity_status == _RECORDING_PROCESS_IDENTITY_UNKNOWN:
        return False, "recording process identity could not be verified; recording state preserved"
    if second_identity_status == _RECORDING_PROCESS_IDENTITY_PRESENT:
        identity_relation = _recording_process_identity_relation(
            pid,
            expected_identity,
            second_identity,
        )
        if identity_relation == _PROCESS_IDENTITY_REUSED:
            return False, "recording process identity does not match; recording state preserved"
        if (
            not allow_matching_identity
            or not expected_identity
            or identity_relation != _PROCESS_IDENTITY_SAME
        ):
            return False, "recording process liveness could not be verified; recording state preserved"
    if second_group_live is not False:
        return False, "recording process liveness could not be verified; recording state preserved"
    return True, None


def _reconcile_recording_process(state: RecordingState) -> str | None:
    """Reconcile recorder lifecycle without stopping an unverified process."""
    pid = state.pid
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "recording process liveness could not be verified; recording state preserved"
    expected_identity = _recording_process_identity_for_lifecycle(state.process_identity)
    if expected_identity is None:
        return _RECORDING_PROCESS_IDENTITY_INVALID_ERROR
    if state.recorder_scope:
        scope_gone = _recorder_scope_is_stably_gone(state.recorder_scope)
        if scope_gone is None:
            return "recording ownership scope could not be verified; recording state preserved"
    try:
        leader_alive = _is_recording_process_alive(pid)
    except Exception:
        return "recording process liveness could not be verified; recording state preserved"
    if leader_alive:
        if not expected_identity:
            return _RECORDING_PROCESS_IDENTITY_INVALID_ERROR
        try:
            current_identity = _recording_process_identity_for_pid(pid)
        except Exception:
            return "recording process identity could not be verified; recording state preserved"
        if current_identity is None:
            return "recording process identity could not be verified; recording state preserved"
        identity_relation = _recording_process_identity_relation(
            pid,
            expected_identity,
            current_identity,
        )
        if identity_relation == _PROCESS_IDENTITY_REUSED:
            return "recording process identity does not match; recording state preserved"
        if identity_relation != _PROCESS_IDENTITY_SAME:
            return "recording process identity could not be verified; recording state preserved"
        stop_kwargs: dict[str, object] = {
            "expected_process_identity": expected_identity,
        }
        if state.recorder_scope:
            stop_kwargs["expected_recorder_scope"] = state.recorder_scope
        if not stop_process(pid, **stop_kwargs):
            stable_absence, absence_error = _recording_process_stable_absence(
                pid,
                expected_identity,
                allow_matching_identity=True,
            )
            if absence_error is not None:
                return absence_error
            if stable_absence:
                return None
            return _RECORDING_PROCESS_GROUP_ACTIVE_ERROR
        return None

    if not state.recorder_scope:
        stable_absence, absence_error = _recording_process_stable_absence(
            pid,
            expected_identity,
        )
        if absence_error is not None:
            return absence_error
        if stable_absence:
            return None
        return _RECORDING_PROCESS_GROUP_ACTIVE_ERROR

    if state.recorder_scope and not scope_gone:
        if stop_process(
            pid,
            expected_process_identity=expected_identity,
            expected_recorder_scope=state.recorder_scope,
        ):
            return None
        return _RECORDING_PROCESS_GROUP_ACTIVE_ERROR
    stable_absence, absence_error = _recording_process_stable_absence(
        pid,
        expected_identity,
    )
    if absence_error is not None:
        return absence_error
    if stable_absence:
        return None
    if not expected_identity:
        return _RECORDING_PROCESS_IDENTITY_INVALID_ERROR
    return _RECORDING_PROCESS_GROUP_ACTIVE_ERROR


def _raise_if_state_unreadable(state: RecordingState) -> None:
    if is_state_read_error(state.error):
        raise RuntimeError(state.error)


def _recording_level_payload(state: RecordingState, *, state_path: Path | None = None) -> dict[str, object] | None:
    audio_path = _normalized_state_recording_artifact_path(
        state.audio_path,
        suffix=(".wav", ".flac", ".socenc"),
        state_path=state_path,
    )
    if not audio_path:
        if state.audio_path:
            return {
                "ok": False,
                "percent": 0,
                "peak": 0.0,
                "rms": 0.0,
                "samples": 0,
                "detail": "microphone level requires a readable recording artifact",
            }
        return None
    if audio_path.suffix.lower() == ".flac":
        return {
            "ok": False,
            "percent": 0,
            "peak": 0.0,
            "rms": 0.0,
            "samples": 0,
            "detail": "microphone level is unavailable for FLAC artifacts",
        }
    if _is_encrypted_recording_artifact(audio_path):
        return {
            "ok": False,
            "percent": 0,
            "peak": 0.0,
            "rms": 0.0,
            "samples": 0,
            "detail": "microphone level is unavailable for encrypted recording artifacts",
        }
    try:
        return asdict(read_recording_level(audio_path))
    except RecorderError as exc:
        return {"ok": False, "percent": 0, "peak": 0.0, "rms": 0.0, "samples": 0, "detail": _redact_error_for_user(str(exc))}


def _recording_sibling_path(path: Path | None) -> Path | None:
    if not isinstance(path, Path):
        return None
    if _is_encrypted_recording_artifact(path):
        return _plaintext_recording_sibling_for_encrypted_path(path)
    if path.suffix.lower() not in {".wav", ".flac"}:
        return None
    try:
        return encrypted_path_for(path)
    except ArtifactCryptoError:
        return None


def _remove_recording_artifact(
    path_value: str | None,
    *,
    expected_stats: dict[Path, os.stat_result | None] | None = None,
) -> bool:
    if not path_value:
        return False
    path = Path(str(path_value))
    if path.name.lower().endswith(ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES):
        primary_suffix = ".socenc"
    elif path.suffix.lower() in {".wav", ".flac"}:
        primary_suffix = path.suffix.lower()
    else:
        return False

    def remove_candidate(candidate: Path, candidate_value: str, suffix: str) -> bool:
        if expected_stats is None:
            return remove_file(candidate_value, suffix=suffix)
        if candidate not in expected_stats or expected_stats[candidate] is None:
            return False
        return remove_file(candidate_value, suffix=suffix, expected_stat=expected_stats[candidate])

    primary_exists = _safe_leaf_presence(path)
    if primary_exists is None:
        return False
    sibling_path = _recording_sibling_path(path)
    sibling_exists = False
    if sibling_path is not None:
        sibling_presence = _safe_leaf_presence(sibling_path)
        if sibling_presence is None:
            return False
        sibling_exists = sibling_presence
    candidates = ((path, primary_exists), (sibling_path, sibling_exists))
    for candidate, candidate_exists in candidates:
        if candidate is None or not candidate_exists:
            continue
        if expected_stats is not None:
            if candidate not in expected_stats or expected_stats[candidate] is None:
                return False
        elif _recording_artifact_stat(candidate) is None:
            return False
    if primary_exists and not remove_candidate(path, path_value, primary_suffix):
        return False
    if sibling_exists:
        sibling_suffix = ".socenc" if is_encrypted_path(sibling_path) else sibling_path.suffix.lower()
        if not remove_candidate(sibling_path, str(sibling_path), sibling_suffix):
            return False
    return primary_exists or sibling_exists


def _recording_artifact_missing_but_safe(
    path_value: str | None,
    *,
    suffix: str | tuple[str, ...],
    state_path: Path | None = None,
) -> bool:
    if not path_value:
        return False
    try:
        path_value = _assert_clean_text(path_value, field_name="path", max_chars=MAX_PATH_CHARS)
        path = _normalized_state_recording_artifact_path(
            path_value,
            suffix=suffix,
            state_path=state_path,
            require_recordings_dir=True,
        )
        if path is None:
            return False
        path.lstat()
    except FileNotFoundError:
        sibling_path = _recording_sibling_path(path)
        if sibling_path is None:
            return True
        try:
            sibling_path.lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return False
    except (RecorderError, RuntimeError, ValueError, OSError, TypeError):
        return False
    return False


def _transcript_artifact_missing_but_safe(path: Path | None) -> bool:
    if not isinstance(path, Path) or not _is_transcript_artifact(path):
        return False
    try:
        assert_safe_path_components(path, field_name="transcript file")
        assert_no_symlink_ancestors(path, field_name="transcript file")
        path.resolve(strict=False).relative_to(transcript_dir().resolve(strict=False))
        path.lstat()
    except FileNotFoundError:
        return True
    except (OSError, RuntimeError, ValueError):
        return False
    return False


def _transcript_sibling_path(path: Path | None) -> Path | None:
    if not isinstance(path, Path) or not _is_transcript_artifact(path):
        return None
    if is_encrypted_path(path):
        return path.with_name(path.name[:-len(".socenc")])
    return encrypted_path_for(path)


def _path_exists_or_is_symlink(path: Path) -> bool:
    return _safe_leaf_presence(path) is not False


def _safe_leaf_presence(path: Path) -> bool | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return True


def _transcript_sibling_missing_but_safe(path: Path | None) -> bool:
    sibling_path = _transcript_sibling_path(path)
    if sibling_path is None:
        return True
    return _transcript_artifact_missing_but_safe(sibling_path)


def _stabilize_recording_artifact_path(
    artifact_path: Path,
    *,
    replace_existing_path: Path | None = None,
) -> Path:
    if not isinstance(artifact_path, Path):
        raise RuntimeError("recording artifact path is invalid")
    if replace_existing_path is not None and not isinstance(replace_existing_path, Path):
        raise RuntimeError("replacement recording artifact path is invalid")
    if artifact_path.suffix.lower() not in {".wav", ".flac"}:
        raise RuntimeError("recording artifact path has invalid suffix")
    assert_no_symlink_ancestors(artifact_path, field_name="recording artifact path")
    if _recording_artifact_stat(artifact_path) is None:
        raise RuntimeError("recording artifact path is not a safe regular file")
    stem = artifact_path.stem
    lower_stem = stem.lower()
    marker_stem = stem
    for marker in (".trimmed-", ".encoded-"):
        index = lower_stem.find(marker)
        if index >= 0:
            marker_stem = stem[:index]
            break
    if marker_stem == stem:
        return artifact_path
    stable_path = artifact_path.with_name(f"{marker_stem}{artifact_path.suffix}")
    if stable_path == artifact_path:
        return artifact_path
    parent_fd: int | None = None
    source_stat: os.stat_result | None = None
    target_stat: os.stat_result | None = None
    backup_name = ""
    target_removed = False
    transaction_active = False

    def same_artifact_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            first.st_size,
            first.st_mtime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            second.st_size,
            second.st_mtime_ns,
        )

    def unlink_artifact_leaf_safely(
        leaf_name: str,
        expected_stat: os.stat_result,
        *,
        field_name: str,
    ) -> bool:
        try:
            current_stat = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not same_artifact_identity(current_stat, expected_stat):
            raise RuntimeError(f"{field_name} changed before cleanup: {stable_path}")
        for _ in range(100):
            cleanup_name = f"{leaf_name}.{secrets.token_hex(8)}.cleanup"
            try:
                _rename_without_replacing(
                    leaf_name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name=f"{field_name} cleanup",
                )
            except FileExistsError:
                continue
            try:
                claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not same_artifact_identity(claimed_stat, expected_stat):
                    raise RuntimeError(f"{field_name} changed before cleanup: {stable_path}")
                if getattr(claimed_stat, "st_nlink", 1) == 1:
                    secure_wipe_regular_file_at(parent_fd, cleanup_name, claimed_stat, field_name=field_name)
                os.unlink(cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                try:
                    _rename_without_replacing(
                        cleanup_name,
                        leaf_name,
                        directory_fd=parent_fd,
                        field_name=f"{field_name} restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException:
                    exc.add_note("stable recording artifact cleanup restore failed")
                raise
            return True
        raise RuntimeError(f"{field_name} cleanup path could not be claimed: {stable_path}")

    def assert_backup_identity() -> None:
        if target_stat is None:
            raise RuntimeError(f"stable recording artifact backup identity is unavailable: {stable_path}")
        current_backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        if not same_artifact_identity(current_backup_stat, target_stat):
            raise RuntimeError(f"stable recording artifact backup changed during rollback: {stable_path}")

    def remove_backup_safely() -> None:
        nonlocal backup_name
        if not backup_name:
            return
        assert_backup_identity()
        for _ in range(100):
            cleanup_name = f"{backup_name}.{secrets.token_hex(8)}.cleanup"
            try:
                _rename_without_replacing(
                    backup_name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name="stable recording artifact backup cleanup",
                )
            except FileExistsError:
                continue
            try:
                claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if target_stat is None or not same_artifact_identity(claimed_stat, target_stat):
                    raise RuntimeError(f"stable recording artifact backup changed before cleanup: {stable_path}")
                if not unlink_artifact_leaf_safely(
                    cleanup_name,
                    claimed_stat,
                    field_name="stable recording artifact backup",
                ):
                    raise RuntimeError(f"stable recording artifact backup disappeared during cleanup: {stable_path}")
                _fsync_fd(parent_fd)
            except BaseException as exc:
                try:
                    _rename_without_replacing(
                        cleanup_name,
                        backup_name,
                        directory_fd=parent_fd,
                        field_name="stable recording artifact backup restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException:
                    exc.add_note("stable recording artifact backup restore failed")
                raise
            backup_name = ""
            return
        raise RuntimeError(f"stable recording artifact backup cleanup path could not be claimed: {stable_path}")

    def rollback() -> None:
        nonlocal backup_name, target_removed
        if not transaction_active or parent_fd is None or source_stat is None:
            return
        try:
            current_target_stat = os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            current_target_stat = None
        activated = current_target_stat is not None and same_artifact_identity(current_target_stat, source_stat)
        if activated:
            if target_stat is None:
                _rename_without_replacing(
                    stable_path.name,
                    artifact_path.name,
                    directory_fd=parent_fd,
                    field_name="recording artifact path",
                )
                _fsync_fd(parent_fd)
                return
            if backup_name:
                try:
                    assert_backup_identity()
                except FileNotFoundError:
                    _rename_without_replacing(
                        stable_path.name,
                        artifact_path.name,
                        directory_fd=parent_fd,
                        field_name="recording artifact path",
                    )
                    _fsync_fd(parent_fd)
                    return
            if not unlink_artifact_leaf_safely(
                stable_path.name,
                current_target_stat,
                field_name="stable recording artifact rollback",
            ):
                raise RuntimeError(f"stable recording artifact disappeared during rollback: {stable_path}")
            _fsync_fd(parent_fd)
        elif target_removed and current_target_stat is not None:
            raise RuntimeError(f"stable recording artifact changed during rollback: {stable_path}")
        elif not target_removed and target_stat is not None and (
            current_target_stat is None or not same_artifact_identity(current_target_stat, target_stat)
        ):
            raise RuntimeError(f"stable recording artifact changed during rollback: {stable_path}")
        if not backup_name:
            return
        if target_removed or activated:
            if current_target_stat is None or activated:
                try:
                    os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    assert_backup_identity()
                    _rename_without_replacing(
                        backup_name,
                        stable_path.name,
                        directory_fd=parent_fd,
                        field_name="stable recording artifact",
                    )
                    backup_name = ""
                    target_removed = False
                    _fsync_fd(parent_fd)
                    return
            raise RuntimeError(f"stable recording artifact exists during rollback: {stable_path}")
        backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        if target_stat is None or not same_artifact_identity(backup_stat, target_stat):
            raise RuntimeError(f"stable recording artifact backup changed during rollback: {stable_path}")
        if not unlink_artifact_leaf_safely(
            backup_name,
            backup_stat,
            field_name="stable recording artifact backup",
        ):
            raise RuntimeError(f"stable recording artifact backup disappeared during rollback: {stable_path}")
        backup_name = ""
        _fsync_fd(parent_fd)

    try:
        assert_no_symlink_ancestors(stable_path, field_name="recording artifact path")
        parent_fd = ensure_directory_without_following_symlinks(
            stable_path.parent,
            field_name="recording artifact directory",
        )
        source_stat = os.stat(artifact_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat_module.S_ISREG(source_stat.st_mode) or getattr(source_stat, "st_nlink", 1) != 1:
            raise RuntimeError(f"recording artifact path is not a safe regular file: {artifact_path}")
        try:
            target_stat = os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_stat = None
        if target_stat is not None:
            if not stat_module.S_ISREG(target_stat.st_mode):
                raise RuntimeError(f"stable recording artifact is not a safe regular file: {stable_path}")
            if replace_existing_path != stable_path:
                if getattr(target_stat, "st_nlink", 1) != 1:
                    raise RuntimeError(f"stable recording artifact is not a safe regular file: {stable_path}")
                raise RuntimeError(f"stable recording artifact already exists: {stable_path}")
            stale_backup_removed = False
            try:
                candidate_names: list[str] = []
                with os.scandir(parent_fd) as candidate_entries:
                    for candidate_entry in candidate_entries:
                        if len(candidate_names) >= MAX_DIRECTORY_SCAN_ENTRIES:
                            raise RuntimeError(
                                f"failed to scan stable recording artifact backups: {stable_path}"
                            )
                        candidate_names.append(candidate_entry.name)
            except OSError as exc:
                raise RuntimeError(f"failed to scan stable recording artifact backups: {stable_path}") from exc
            backup_prefix = f".{stable_path.name}."
            for candidate_name in candidate_names:
                if not isinstance(candidate_name, str) or not (
                    candidate_name.startswith(backup_prefix) and candidate_name.endswith(".bak")
                ):
                    continue
                try:
                    candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat_module.S_ISREG(candidate_stat.st_mode):
                    continue
                if not same_artifact_identity(candidate_stat, target_stat):
                    continue
                if not unlink_artifact_leaf_safely(
                    candidate_name,
                    candidate_stat,
                    field_name="stale stable recording artifact backup",
                ):
                    raise RuntimeError(f"stable recording artifact backup disappeared during cleanup: {stable_path}")
                stale_backup_removed = True
            if stale_backup_removed:
                _fsync_fd(parent_fd)
                target_stat = os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
            if getattr(target_stat, "st_nlink", 1) != 1:
                raise RuntimeError(f"stable recording artifact is not a safe regular file: {stable_path}")
            for _ in range(100):
                candidate_name = f".{stable_path.name}.{secrets.token_hex(8)}.bak"
                try:
                    os.link(
                        stable_path.name,
                        candidate_name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                try:
                    backup_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                    current_target_stat = os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat_module.S_ISREG(backup_stat.st_mode)
                        or getattr(backup_stat, "st_nlink", 1) != 2
                        or not same_artifact_identity(backup_stat, target_stat)
                        or not same_artifact_identity(current_target_stat, target_stat)
                    ):
                        raise RuntimeError("stable recording artifact changed during backup activation")
                    backup_name = candidate_name
                    break
                except BaseException as backup_error:
                    try:
                        candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                        if same_artifact_identity(candidate_stat, target_stat):
                            if unlink_artifact_leaf_safely(
                                candidate_name,
                                candidate_stat,
                                field_name="stable recording artifact backup candidate",
                            ):
                                _fsync_fd(parent_fd)
                    except FileNotFoundError:
                        pass
                    except BaseException:
                        backup_error.add_note("recording artifact backup cleanup failed")
                    raise
            if not backup_name:
                raise RuntimeError("failed to create stable recording artifact backup")
            transaction_active = True
            current_target_stat = os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
            if not same_artifact_identity(current_target_stat, target_stat):
                raise RuntimeError(f"stable recording artifact changed before replacement: {stable_path}")
            if not unlink_artifact_leaf_safely(
                stable_path.name,
                current_target_stat,
                field_name="stable recording artifact",
            ):
                raise RuntimeError(f"stable recording artifact disappeared before replacement: {stable_path}")
            target_removed = True
            _fsync_fd(parent_fd)
        transaction_active = True
        _rename_without_replacing(
            artifact_path.name,
            stable_path.name,
            directory_fd=parent_fd,
            field_name="stable recording artifact",
        )
        activated_stat = os.stat(stable_path.name, dir_fd=parent_fd, follow_symlinks=False)
        if not same_artifact_identity(activated_stat, source_stat):
            raise RuntimeError(f"stable recording artifact changed during activation: {stable_path}")
        _fsync_fd(parent_fd)
        if backup_name:
            remove_backup_safely()
        transaction_active = False
        return stable_path
    except (OSError, RuntimeError) as exc:
        try:
            rollback()
        except BaseException:
            exc.add_note("recording artifact rollback failed")
        raise RuntimeError(f"failed to stabilize recording artifact path: {exc}") from exc
    except BaseException as exc:
        try:
            rollback()
        except BaseException:
            exc.add_note("recording artifact rollback failed")
        raise
    finally:
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except BaseException:
                pass


def _safe_regular_leaf_probe(
    path: Path,
) -> tuple[bool | None, os.stat_result | None]:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False, None
    except OSError:
        return None, None
    if (
        not stat_module.S_ISREG(file_stat.st_mode)
        or getattr(file_stat, "st_nlink", 1) != 1
    ):
        return True, None
    return True, file_stat


def _recording_artifact_stat(path: Path) -> os.stat_result | None:
    try:
        file_stat = path.lstat()
    except OSError:
        return None
    if not stat_module.S_ISREG(file_stat.st_mode):
        return None
    if getattr(file_stat, "st_nlink", 1) != 1:
        return None
    return file_stat


class DirectoryScanError(RuntimeError):
    def __init__(self, directory: Path, *, field_name: str) -> None:
        super().__init__(f"failed to scan {field_name}")
        self.directory = directory
        self.field_name = field_name


def _safe_directory_entries(
    directory: Path,
    *,
    field_name: str,
    missing_ok: bool = False,
) -> list[tuple[Path, os.stat_result]]:
    try:
        directory_fd = open_directory_without_following_symlinks(directory, field_name=field_name)
    except FileNotFoundError as exc:
        if missing_ok:
            return []
        raise DirectoryScanError(directory, field_name=field_name) from exc
    except (OSError, RuntimeError) as exc:
        raise DirectoryScanError(directory, field_name=field_name) from exc
    try:
        entries: list[tuple[Path, os.stat_result]] = []
        try:
            with os.scandir(directory_fd) as directory_entries:
                for directory_entry in directory_entries:
                    if len(entries) >= MAX_DIRECTORY_SCAN_ENTRIES:
                        raise DirectoryScanError(directory, field_name=field_name)
                    name = directory_entry.name
                    if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
                        continue
                    try:
                        file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    except OSError as exc:
                        raise DirectoryScanError(directory, field_name=field_name) from exc
                    entries.append((directory / name, file_stat))
        except FileNotFoundError as exc:
            if missing_ok:
                return []
            raise DirectoryScanError(directory, field_name=field_name) from exc
        except OSError as exc:
            raise DirectoryScanError(directory, field_name=field_name) from exc
        return entries
    finally:
        try:
            os.close(directory_fd)
        except BaseException:
            pass


def _same_state_v3_cleanup_claims_present(
    state_path: Path,
    *,
    allowed_paths: frozenset[Path] = frozenset(),
    include_all_cleanup_artifacts: bool = False,
) -> bool | None:
    try:
        if type(include_all_cleanup_artifacts) is not bool:
            return None
        root = Path(os.path.abspath(os.fspath(recordings_dir())))
        normalized_allowed = frozenset(
            Path(os.path.abspath(os.fspath(path)))
            for path in allowed_paths
        )
        if any(not path.is_relative_to(root) for path in normalized_allowed):
            return None
        state_prefix = f".cleanup.v3.{_cleanup_backup_state_namespace(state_path)}."
        effective_uid = os.geteuid()
        if type(effective_uid) is not int or effective_uid < 0:
            return None
        nofollow = getattr(os, "O_NOFOLLOW", None)
        cloexec = getattr(os, "O_CLOEXEC", None)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        nonblock = getattr(os, "O_NONBLOCK", None)
        if any(
            isinstance(flag, bool)
            or not isinstance(flag, int)
            or flag <= 0
            for flag in (nofollow, cloexec, directory_flag, nonblock)
        ):
            return None
        try:
            root_fd = open_directory_without_following_symlinks(
                root,
                field_name="recording cleanup root",
            )
        except FileNotFoundError:
            return False
        primary_error: BaseException | None = None
        result: bool | None = None
        try:
            root_stat = os.fstat(root_fd)
            if (
                not stat_module.S_ISDIR(root_stat.st_mode)
                or root_stat.st_uid != effective_uid
                or root_stat.st_mode & 0o022
            ):
                raise RuntimeError("recording cleanup root is invalid")
            entry_count = 0

            def scan_directory(
                directory_fd: int,
                directory_path: Path,
                depth: int,
            ) -> bool | None:
                nonlocal entry_count
                before = os.fstat(directory_fd)
                with os.scandir(directory_fd) as entries:
                    for entry in entries:
                        entry_count += 1
                        if entry_count > _MAX_CLEANUP_TREE_SCAN_ENTRIES:
                            return None
                        name = entry.name
                        if (
                            type(name) is not str
                            or name in {"", ".", ".."}
                            or "/" in name
                            or "\x00" in name
                        ):
                            return None
                        child_path = directory_path / name
                        child_stat = os.stat(
                            name,
                            dir_fd=directory_fd,
                            follow_symlinks=False,
                        )
                        if name.startswith(state_prefix) or (
                            include_all_cleanup_artifacts
                            and name.startswith(".cleanup.")
                        ):
                            if child_path in normalized_allowed:
                                continue
                            if (
                                name.startswith(state_prefix)
                                and
                                _bound_cleanup_v3_tombstone_parts(name)
                                is not None
                                and stat_module.S_ISREG(child_stat.st_mode)
                                and child_stat.st_uid == effective_uid
                                and child_stat.st_dev == root_stat.st_dev
                                and getattr(child_stat, "st_nlink", 1) == 1
                                and child_stat.st_size == 0
                                and not child_stat.st_mode & 0o7022
                            ):
                                continue
                            return True
                        if not stat_module.S_ISDIR(child_stat.st_mode):
                            continue
                        if depth >= _MAX_CLEANUP_TREE_SCAN_DEPTH:
                            return None
                        if (
                            child_stat.st_uid != effective_uid
                            or child_stat.st_dev != root_stat.st_dev
                            or child_stat.st_mode & 0o022
                        ):
                            return None
                        child_fd = os.open(
                            name,
                            os.O_RDONLY
                            | directory_flag
                            | nofollow
                            | cloexec
                            | nonblock,
                            dir_fd=directory_fd,
                        )
                        child_primary_error: BaseException | None = None
                        nested_result: bool | None = None
                        try:
                            opened = os.fstat(child_fd)
                            if (
                                opened.st_dev,
                                opened.st_ino,
                                opened.st_mode,
                                opened.st_uid,
                            ) != (
                                child_stat.st_dev,
                                child_stat.st_ino,
                                child_stat.st_mode,
                                child_stat.st_uid,
                            ):
                                nested_result = None
                            else:
                                nested_result = scan_directory(
                                    child_fd,
                                    child_path,
                                    depth + 1,
                                )
                        except BaseException as exc:
                            child_primary_error = exc
                        finally:
                            try:
                                os.close(child_fd)
                            except BaseException as exc:
                                if child_primary_error is None or (
                                    isinstance(
                                        exc,
                                        (KeyboardInterrupt, SystemExit),
                                    )
                                    and not isinstance(
                                        child_primary_error,
                                        (KeyboardInterrupt, SystemExit),
                                    )
                                ):
                                    child_primary_error = exc
                        if child_primary_error is not None:
                            raise child_primary_error
                        if nested_result is not False:
                            return nested_result
                after = os.fstat(directory_fd)
                if (
                    after.st_dev,
                    after.st_ino,
                    after.st_mode,
                    after.st_uid,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ) != (
                    before.st_dev,
                    before.st_ino,
                    before.st_mode,
                    before.st_uid,
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                ):
                    return None
                return False

            result = scan_directory(root_fd, root, 0)
        except (KeyboardInterrupt, SystemExit) as exc:
            primary_error = exc
        except Exception as exc:
            primary_error = exc
        finally:
            try:
                os.close(root_fd)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
        if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
            raise primary_error
        if primary_error is not None:
            return None
        return result
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        return None


def _safe_regular_child_files(
    directory: Path,
    suffixes: tuple[str, ...],
    *,
    field_name: str,
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> list[Path]:
    files: list[Path] = []
    for path, file_stat in _safe_directory_entries(directory, field_name=field_name):
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if getattr(file_stat, "st_nlink", 1) != 1:
            continue
        if expected_stats is not None:
            expected_stats[path] = file_stat
        files.append(path)
    return files


def _is_finalization_lock_active(state_path: Path) -> bool:
    lock_path = _finalization_lock_path(state_path)
    try:
        lock_stat = lock_path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if not stat_module.S_ISREG(lock_stat.st_mode) or getattr(lock_stat, "st_nlink", 1) != 1:
        return True
    owner_pid = _read_finalization_lock_pid(lock_path)
    if owner_pid is None:
        return True
    corrupt_owner_pid = owner_pid is _FINALIZATION_LOCK_PID_CORRUPT
    if owner_pid in {_FINALIZATION_LOCK_PID_EMPTY, _FINALIZATION_LOCK_PID_CORRUPT}:
        owner_pid = None
    scope_state = _read_finalization_lock_scope_state(lock_path)
    if scope_state is _FINALIZATION_LOCK_SCOPE_UNKNOWN:
        return True
    scope_unit_state = _read_finalization_lock_scope_unit_state(lock_path)
    if scope_unit_state is _FINALIZATION_LOCK_SCOPE_UNIT_UNKNOWN:
        return True
    has_liveness_anchor = isinstance(scope_state, str) or isinstance(
        scope_unit_state,
        str,
    )
    if isinstance(scope_state, str):
        scope_gone = _recorder_scope_is_stably_gone(scope_state)
        if scope_gone is not True:
            return True
    corrupt_lock_metadata = (
        corrupt_owner_pid
        or scope_state is _FINALIZATION_LOCK_SCOPE_CORRUPT
        or scope_unit_state is _FINALIZATION_LOCK_SCOPE_UNIT_CORRUPT
    )
    owner_identity = (
        _read_finalization_lock_identity(lock_path) if owner_pid is not None else None
    )
    current_identity = (
        _finalization_lock_identity_for_pid(owner_pid)
        if owner_identity is not None and owner_pid is not None
        else None
    )
    owner_identity_relation = (
        _recording_process_identity_relation(
            owner_pid,
            owner_identity,
            current_identity,
        )
        if owner_pid is not None
        else _PROCESS_IDENTITY_UNKNOWN
    )
    owner_reused = _finalization_lock_owner_reused(
        owner_pid,
        owner_identity_relation,
        has_liveness_anchor=has_liveness_anchor,
    )
    if not owner_reused:
        return True
    lock_age = time.time() - lock_stat.st_mtime
    if corrupt_lock_metadata:
        if (
            not has_liveness_anchor
            or lock_age <= MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS
        ):
            return True
    if isinstance(scope_unit_state, str) and _recorder_scope_unit_is_stably_gone(
        scope_unit_state,
        expected_scope=scope_state if isinstance(scope_state, str) else None,
    ) is not True:
        return True
    try:
        current = lock_path.lstat()
    except OSError:
        return True
    return not _same_finalization_lock_snapshot(lock_stat, current)


def _inflight_recording_artifact_paths(audio_path: Path) -> set[Path]:
    if not isinstance(audio_path, Path):
        return set()
    if _is_encrypted_recording_artifact(audio_path):
        audio_path = _plaintext_recording_sibling_for_encrypted_path(audio_path)
        if audio_path is None:
            return set()
    if audio_path.suffix.lower() not in {".wav", ".flac"}:
        return set()
    prefixes = tuple(
        f"{audio_path.stem}{marker}" for marker in (".trimmed-", ".encoded-")
    )
    suffixes = (".wav", ".flac", ".wav.socenc", ".flac.socenc")
    return {
        path
        for path, _file_stat in _safe_directory_entries(
            audio_path.parent,
            field_name="recording artifact directory",
            missing_ok=True,
        )
        if any(
            path.name.startswith(prefix)
            and len(path.name) >= len(prefix) + len(suffix)
            and path.name.endswith(suffix)
            for prefix in prefixes
            for suffix in suffixes
        )
    }


def _transcript_path_for_audio(audio_path: Path) -> Path:
    transcript_root = transcript_dir()
    try:
        recordings_root = recordings_dir().resolve(strict=False)
        canonical_audio_path = audio_path.resolve(strict=False)
        canonical_audio_path.relative_to(recordings_root)
    except ValueError:
        canonical_audio_path = audio_path.resolve(strict=False)
    except OSError:
        canonical_audio_path = audio_path.absolute()
    else:
        legacy_transcript = transcript_root / f"{audio_path.stem}.txt"
        if audio_path.suffix.lower() == ".flac":
            wav_path = audio_path.with_suffix(".wav")
            collision_candidates = (
                wav_path,
                encrypted_path_for(wav_path),
                legacy_transcript,
                encrypted_path_for(legacy_transcript),
            )
            if any(_path_exists_or_is_symlink(candidate) for candidate in collision_candidates):
                return transcript_root / f"{audio_path.stem}.flac.txt"
        return legacy_transcript

    digest = hashlib.sha256(str(canonical_audio_path).encode("utf-8")).hexdigest()[:16]
    suffix = f"-{digest}.txt"
    max_stem_bytes = max(1, 255 - len(suffix.encode("utf-8")))
    stem = audio_path.stem.encode("utf-8")[:max_stem_bytes].decode("utf-8", errors="ignore") or "transcript"
    return transcript_root / f"{stem}{suffix}"


def _finalizing_inflight_artifact_paths(
    state_path: Path,
    state: RecordingState,
    *,
    known_audio_path: Path | None = None,
    include_recording_inflight: bool = True,
) -> set[Path]:
    recover_cleanup_paths = (
        state.status == "error"
        and state.error == "failed to discard recording artifacts"
    )
    if state.status != "finalizing" and not recover_cleanup_paths:
        return set()

    in_flight_paths: set[Path] = set()
    audio_path = known_audio_path
    if audio_path is None:
        audio_path = _normalized_state_recording_artifact_path(
            state.audio_path,
            suffix=(".wav", ".flac", ".socenc"),
            state_path=state_path,
            require_recordings_dir=True,
        )
    if audio_path is None:
        try:
            raw_audio_value = _assert_clean_text(
                state.audio_path,
                field_name="state recording artifact path",
                max_chars=MAX_PATH_CHARS,
            )
            raw_audio_path = Path(raw_audio_value).expanduser()
            if not raw_audio_path.is_absolute():
                raw_audio_path = state_path.parent / raw_audio_path
            assert_safe_path_components(raw_audio_path, field_name="state recording artifact path")
            assert_no_symlink_ancestors(raw_audio_path.parent, field_name="state recording artifact directory")
            raw_audio_path.parent.resolve(strict=False).relative_to(recordings_dir().resolve(strict=False))
            if not (
                raw_audio_path.suffix.lower() in {".wav", ".flac"}
                or _is_encrypted_recording_artifact(raw_audio_path)
            ):
                return set()
            audio_path = raw_audio_path
        except (OSError, RuntimeError, TypeError, ValueError):
            return set()
    if not _is_finalization_lock_active(state_path) and _recording_artifact_stat(audio_path) is not None:
        return set()
    if _is_encrypted_recording_artifact(audio_path):
        audio_path = _plaintext_recording_sibling_for_encrypted_path(audio_path)
        if audio_path is None:
            return set()
    if audio_path.suffix.lower() in {".wav", ".flac"}:
        transcript_path = _transcript_path_for_audio(audio_path)
        in_flight_paths.add(transcript_path)
        in_flight_paths.add(encrypted_path_for(transcript_path))
        if audio_path.suffix.lower() == ".wav":
            # Re-encoding activates sibling .flac before final state commit.
            # Keep it recoverable if process dies in that window.
            recovery_audio_path = audio_path.with_suffix(".flac")
            in_flight_paths.add(recovery_audio_path)
            in_flight_paths.add(encrypted_path_for(recovery_audio_path))
    if include_recording_inflight:
        in_flight_paths.update(
            _inflight_recording_artifact_paths(audio_path)
        )
    return in_flight_paths


def _is_encrypted_recording_artifact(path: Path) -> bool:
    return isinstance(path, Path) and path.name.lower().endswith(ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES)


def _is_recording_audio_artifact(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    suffix = path.suffix.lower()
    return suffix in {".wav", ".flac"} or _is_encrypted_recording_artifact(path)


def _is_recording_artifact(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    return path.suffix.lower() == ".log" or _is_recording_audio_artifact(path)


def _recording_group_stem(path: Path) -> str:
    name = path.name
    lowered = name.lower()
    for suffix in ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _is_inflight_recording_artifact(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    if not _is_recording_artifact(path):
        return False
    stem = path.stem.lower()
    return ".trimmed-" in stem or ".encoded-" in stem


def sorted_files(
    paths: list[Path],
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> list[Path]:
    entries: list[tuple[float, str, Path]] = []
    for path in paths:
        try:
            file_stat = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise DirectoryScanError(path.parent, field_name="recording artifacts") from exc
        if not stat_module.S_ISREG(file_stat.st_mode) or getattr(file_stat, "st_nlink", 1) != 1:
            continue
        if expected_stats is not None:
            expected_stats[path] = file_stat
        entries.append((file_stat.st_mtime, path.name, path))
    return [path for _, _, path in sorted(entries, reverse=True)]


def delete_artifact(path: Path, *, expected_stat: os.stat_result | None = None) -> bool:
    file_stat = _recording_artifact_stat(path)
    if file_stat is None:
        return False
    if expected_stat is not None and not _same_leaf_identity(file_stat, expected_stat):
        return False
    try:
        return _unlink_regular_leaf_with_parent_fsync(
            path,
            field_name="recording artifact",
            expected_stat=expected_stat or file_stat,
        )
    except RuntimeError:
        return False


def prune_transcript_files_by_mtime(
    paths: list[Path],
    keep: int,
    active_paths: set[Path],
    dry_run: bool,
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> dict[str, object]:
    planned_paths: list[str] = []
    deleted_paths: list[str] = []
    failed_paths: list[str] = []
    skipped_active: list[str] = []
    normalized_active_paths = {path.resolve(strict=False) for path in active_paths}
    scan_stats: dict[Path, os.stat_result] = {}
    try:
        sorted_paths = sorted_files(paths, scan_stats)
    except DirectoryScanError as exc:
        return {
            "planned_paths": [],
            "deleted_paths": [],
            "failed_paths": [str(exc.directory)],
            "skipped_active_paths": [],
        }
    grouped_paths: dict[str, list[Path]] = {}
    for path in sorted_paths:
        grouped_paths.setdefault(_transcript_group_key(path), []).append(path)
    groups = sorted(
        grouped_paths.values(),
        key=lambda group: (
            max(scan_stats[path].st_mtime for path in group),
            max(str(path) for path in group),
        ),
        reverse=True,
    )
    inactive_groups: list[list[Path]] = []
    skipped_group_count = 0
    for group in groups:
        if any(path.resolve(strict=False) in normalized_active_paths for path in group):
            skipped_active.extend(str(path) for path in group)
            skipped_group_count += 1
        else:
            inactive_groups.append(group)
    inactive_keep = max(max(keep, 0) - skipped_group_count, 0)
    for group in inactive_groups[inactive_keep:]:
        for path in group:
            if dry_run:
                planned_paths.append(str(path))
                continue
            expected_stat = scan_stats.get(path)
            if expected_stats is not None:
                expected_stat = expected_stats.get(path, expected_stat)
            if delete_artifact(path, expected_stat=expected_stat):
                deleted_paths.append(str(path))
            else:
                failed_paths.append(str(path))
    return {
        "planned_paths": planned_paths,
        "deleted_paths": deleted_paths,
        "failed_paths": failed_paths,
        "skipped_active_paths": skipped_active,
    }


def prune_files_by_mtime(
    paths: list[Path],
    keep: int,
    active_paths: set[Path],
    dry_run: bool,
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> dict[str, object]:
    planned_paths: list[str] = []
    deleted_paths: list[str] = []
    failed_paths: list[str] = []
    skipped_active: list[str] = []
    inactive_paths: list[Path] = []
    normalized_active_paths = {path.resolve(strict=False) for path in active_paths}
    scan_stats: dict[Path, os.stat_result] = {}
    try:
        sorted_paths = sorted_files(paths, scan_stats)
    except DirectoryScanError as exc:
        return {
            "planned_paths": [],
            "deleted_paths": [],
            "failed_paths": [str(exc.directory)],
            "skipped_active_paths": [],
        }
    for path in sorted_paths:
        normalized = path.resolve(strict=False)
        if normalized in normalized_active_paths:
            skipped_active.append(str(path))
            continue
        inactive_paths.append(path)
    inactive_keep = max(max(keep, 0) - len(skipped_active), 0)
    for path in inactive_paths[inactive_keep:]:
        if dry_run:
            planned_paths.append(str(path))
            continue
        expected_stat = scan_stats.get(path)
        if expected_stats is not None:
            expected_stat = expected_stats.get(path, expected_stat)
        if delete_artifact(path, expected_stat=expected_stat):
            deleted_paths.append(str(path))
        else:
            failed_paths.append(str(path))
    return {
        "planned_paths": planned_paths,
        "deleted_paths": deleted_paths,
        "failed_paths": failed_paths,
        "skipped_active_paths": skipped_active,
    }


def recording_groups() -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    directory = recordings_dir()
    for path, file_stat in _safe_directory_entries(directory, field_name="recordings directory"):
        if not _is_recording_artifact(path):
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if getattr(file_stat, "st_nlink", 1) != 1:
            continue
        group_stem = _recording_group_stem(path)
        group = groups.setdefault(
            group_stem,
            {"stem": group_stem, "mtime": 0.0, "files": [], "file_stats": {}},
        )
        group["mtime"] = max(float(group["mtime"]), file_stat.st_mtime)
        group_files = group["files"]
        if isinstance(group_files, list):
            group_files.append(path)
        group_stats = group["file_stats"]
        if isinstance(group_stats, dict):
            group_stats[path] = file_stat
    return sorted(groups.values(), key=lambda group: (float(group["mtime"]), str(group["stem"])), reverse=True)


def recording_artifact_files(
    expected_stats: dict[Path, os.stat_result] | None = None,
) -> list[Path]:
    return [
        path
        for path in _safe_regular_child_files(
            recordings_dir(),
            RECORDING_ARTIFACT_EXTENSIONS,
            field_name="recordings directory",
            expected_stats=expected_stats,
        )
        if _is_recording_artifact(path)
    ]


def _add_recording_artifact_counts(paths: list[str], recording_result: dict[str, object], prefix: str) -> None:
    recording_key = f"{prefix}_recordings"
    log_key = f"{prefix}_logs"
    recording_count = _coerce_int(recording_result[recording_key], field_name=recording_key)
    log_count = _coerce_int(recording_result[log_key], field_name=log_key)
    for path_text in paths:
        path = Path(path_text)
        suffix = path.suffix.lower()
        if _is_recording_audio_artifact(path):
            recording_count += 1
        elif suffix == ".log":
            log_count += 1
    recording_result[recording_key] = recording_count
    recording_result[log_key] = log_count


def prune_recording_groups(
    keep: int,
    active_paths: set[Path],
    dry_run: bool,
    max_age_days: int = DEFAULT_RECORDING_MAX_AGE_DAYS,
) -> dict[str, object]:
    planned_recordings = 0
    planned_logs = 0
    planned_paths: list[str] = []
    deleted_recordings = 0
    deleted_logs = 0
    deleted_paths: list[str] = []
    failed_paths: list[str] = []
    skipped_active_paths: list[str] = []
    skipped_group_paths: list[Path] = []
    normalized_active_paths = {path.resolve(strict=False) for path in active_paths}
    grouped_artifact_stats: dict[Path, os.stat_result] = {}
    cutoff = time.time() - max(0, max_age_days) * 24 * 60 * 60
    try:
        groups = recording_groups()
    except DirectoryScanError as exc:
        return {
            "planned_recordings": 0,
            "planned_logs": 0,
            "planned_paths": [],
            "deleted_recordings": 0,
            "deleted_logs": 0,
            "deleted_paths": [],
            "failed_paths": [str(exc.directory)],
            "skipped_active_paths": [],
        }
    grouped_artifacts: list[Path] = []
    retained_file_count = 0
    file_cap = max(keep, 0)
    for group in groups:
        files = group.get("files", [])
        file_stats = group.get("file_stats", {})
        if isinstance(files, list):
            grouped_artifacts.extend(path for path in files if isinstance(path, Path))
            if isinstance(file_stats, dict):
                for path in files:
                    if isinstance(path, Path) and isinstance(file_stats.get(path), os.stat_result):
                        grouped_artifact_stats[path] = file_stats[path]
        if not isinstance(files, list):
            continue
        group_paths = [path for path in files if isinstance(path, Path)]
        if any(path.resolve(strict=False) in normalized_active_paths for path in group_paths):
            skipped_group_paths.extend(group_paths)
            skipped_active_paths.extend(str(path) for path in group_paths)
            continue
        if (
            float(group.get("mtime", 0.0)) >= cutoff
            and retained_file_count + len(group_paths) <= file_cap
        ):
            retained_file_count += len(group_paths)
            continue
        for path in group_paths:
            if dry_run:
                planned_paths.append(str(path))
                suffix = path.suffix.lower()
                if _is_recording_audio_artifact(path):
                    planned_recordings += 1
                elif suffix == ".log":
                    planned_logs += 1
                continue
            if delete_artifact(path, expected_stat=grouped_artifact_stats.get(path)):
                deleted_paths.append(str(path))
                suffix = path.suffix.lower()
                if _is_recording_audio_artifact(path):
                    deleted_recordings += 1
                elif suffix == ".log":
                    deleted_logs += 1
            else:
                failed_paths.append(str(path))
    result: dict[str, object] = {
        "planned_recordings": planned_recordings,
        "planned_logs": planned_logs,
        "planned_paths": planned_paths,
        "deleted_recordings": deleted_recordings,
        "deleted_logs": deleted_logs,
        "deleted_paths": deleted_paths,
        "failed_paths": failed_paths,
        "skipped_active_paths": skipped_active_paths,
    }
    handled_paths = {
        Path(path).resolve(strict=False)
        for path in planned_paths + deleted_paths + failed_paths
    } | {path.resolve(strict=False) for path in skipped_group_paths}
    remaining_artifacts = [
        path
        for path in grouped_artifacts
        if path.resolve(strict=False) not in handled_paths
        and _is_inflight_recording_artifact(path)
    ]
    file_cap_result = prune_files_by_mtime(
        remaining_artifacts,
        MAX_TEMP_RECORDING_FILES,
        normalized_active_paths,
        dry_run,
        expected_stats=grouped_artifact_stats,
    )
    cap_planned = list(file_cap_result["planned_paths"])
    cap_deleted = list(file_cap_result["deleted_paths"])
    cap_failed = list(file_cap_result["failed_paths"])
    cap_skipped = list(file_cap_result["skipped_active_paths"])
    planned_paths.extend(cap_planned)
    deleted_paths.extend(cap_deleted)
    failed_paths.extend(cap_failed)
    skipped_active_paths.extend(path for path in cap_skipped if path not in skipped_active_paths)
    _add_recording_artifact_counts(cap_planned, result, "planned")
    _add_recording_artifact_counts(cap_deleted, result, "deleted")
    return result


def _command_start_locked(
    args: argparse.Namespace,
    store: StateStore,
    *,
    finalization_lock_path: Path | None = None,
    lock_release_state: dict[str, bool] | None = None,
) -> dict[str, object]:
    current = store.read()
    _raise_if_state_unreadable(current)
    if not (
        current.pending_cleanup_owner_paths
        or current.pending_cleanup_restore_owner_paths
        or current.pending_cleanup_backup_entries
        or current.cleanup_backup_journal_overflow
    ):
        cleanup_claims_present = _same_state_v3_cleanup_claims_present(
            store.path,
            include_all_cleanup_artifacts=True,
        )
        if cleanup_claims_present is not False:
            return {
                "status": "error",
                "message": (
                    "previous recording cleanup is unresolved; "
                    "run cancel before starting a new recording"
                ),
            }
    if (
        current.pending_cleanup_owner_paths
        or current.pending_cleanup_restore_owner_paths
        or current.pending_cleanup_backup_entries
        or current.cleanup_backup_journal_overflow
    ):
        return {
            "status": "error",
            "message": (
                "previous recording cleanup is unresolved; "
                "run cancel before starting a new recording"
            ),
        }
    if current.status == "finalizing":
        if finalization_lock_path is not None and (
            current.audio_path or current.transcript_path
        ):
            return finalize_recording(
                args,
                store,
                current,
                finalization_lock_path=finalization_lock_path,
            )
        return {
            "status": "finalizing",
            "message": "finalization in progress; wait for completion",
        }
    identity_marker_present = current.process_identity is not None and current.process_identity != ""
    if current.status == "error" and (current.pid is not None or identity_marker_present):
        marker_error = (
            "previous recorder process state is unresolved; run cancel before starting a new recording"
        )
        if (
            isinstance(current.pid, bool)
            or not isinstance(current.pid, int)
            or current.pid <= 0
            or _validated_recording_process_identity(current.process_identity) is None
        ):
            return {
                "status": "error",
                "message": marker_error,
                "error": marker_error,
                "pid_present": current.pid is not None,
            }
        process_error = _reconcile_recording_process(current)
        if process_error is not None:
            return {
                "status": "error",
                "message": process_error,
                "error": process_error,
                "pid_present": True,
            }
        current = store.update(
            status="error",
            pid=None,
            process_identity="",
            stopped_at=current.stopped_at or now_iso(),
            error="",
            inserted=False,
        )
    if current.error == TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR:
        return {
            "status": "error",
            "message": (
                "previous transcript recovery is unresolved; "
                "preserve it before starting a new recording"
            ),
            "transcript_present": bool(current.transcript),
        }
    if (
        _is_transcript_insert_recovery_error(current.error)
        and bool(current.transcript)
    ):
        return {
            "status": "error",
            "message": (
                "previous transcript recovery is unresolved; "
                "preserve it before starting a new recording"
            ),
            "transcript_present": True,
        }
    if (
        current.status == "error"
        and _is_retryable_transcript_insert_error(current.error)
        and not current.audio_path
        and not current.log_path
        and not current.transcript
    ):
        current = store.update(
            status="idle",
            transcript="",
            transcript_path="",
            error="",
            inserted=False,
            pid=None,
            process_identity="",
            stopped_at=current.stopped_at or now_iso(),
        )
    if current.status == "error" and (current.audio_path or current.log_path or current.transcript_path):
        return {
            "status": "error",
            "message": "previous recording cleanup is unresolved; run cancel before starting a new recording",
            "audio_path_present": bool(current.audio_path),
            "log_path_present": bool(current.log_path),
            "transcript_path_present": bool(current.transcript_path),
        }
    if current.status in {"recorded", "processing"}:
        return {
            "status": current.status,
            "message": "previous recording is pending; run stop or toggle to finalize before starting a new recording",
            "audio_path_present": bool(current.audio_path),
            "log_path_present": bool(current.log_path),
            "transcript_path_present": bool(current.transcript_path),
        }
    if current.status == "recording":
        try:
            recording_active = _recording_process_verified_active(current)
        except RuntimeError as exc:
            error_text = f"{exc}; recording state preserved"
            store.update(status="recording", error=error_text, inserted=False)
            return {"status": "recording", "message": error_text, "error": error_text}
        if recording_active:
            return {
                "status": "recording",
                "message": "already recording",
                "pid_present": bool(current.pid),
                "language": current.language,
            }
        if current.pid is None and not identity_marker_present:
            current_audio_path = _normalized_state_recording_artifact_path(
                current.audio_path,
                suffix=(".wav", ".flac", ".socenc"),
                state_path=store.path,
                require_recordings_dir=False,
            )
            if current.audio_path and not current_audio_path:
                store.update(
                    status="error",
                    pid=None,
                    process_identity="",
                    stopped_at=current.stopped_at or now_iso(),
                    error="recording state references an invalid artifact path",
                    inserted=False,
                )
                return {
                    "status": "error",
                    "message": "recording state references an invalid artifact path",
                }
        if current.pid is None:
            error_text = "recording process pid is missing; recording state preserved"
            store.update(status="recording", error=error_text, inserted=False)
            return {"status": "recording", "message": error_text, "error": error_text}
        process_error = _reconcile_recording_process(current)
        if process_error is not None:
            if process_error == _RECORDING_PROCESS_GROUP_ACTIVE_ERROR:
                return {
                    "status": "recording",
                    "message": process_error,
                    "error": process_error,
                }
            store.update(status="recording", error=process_error, inserted=False)
            return {"status": "recording", "message": process_error, "error": process_error}
        current_audio_path = _normalized_state_recording_artifact_path(
            current.audio_path,
            suffix=(".wav", ".flac", ".socenc"),
            state_path=store.path,
            require_recordings_dir=False,
        )
        if current.audio_path and not current_audio_path:
            store.update(
                status="error",
                pid=None,
                process_identity="",
                stopped_at=current.stopped_at or now_iso(),
                error="recording state references an invalid artifact path",
                inserted=False,
            )
            return {
                "status": "error",
                "message": "recording state references an invalid artifact path",
            }
        current_audio_stat = _recording_artifact_stat(current_audio_path) if current_audio_path else None
        if current_audio_stat is not None and current_audio_stat.st_size > 0:
            recorded = store.update(
                status="recorded",
                pid=None,
                process_identity="",
                stopped_at=current.stopped_at or now_iso(),
                error="",
                inserted=False,
            )
            return {
                "status": "recorded",
                "message": "previous recording has exited; run stop or toggle to transcribe",
                "audio_path_present": bool(recorded.audio_path),
                "language": recorded.language,
            }
        store.update(
            status="error",
            pid=None,
            process_identity="",
            stopped_at=current.stopped_at or now_iso(),
            error="recording exited before audio was saved",
            inserted=False,
        )
        return {
            "status": "error",
            "message": "recording exited before audio was saved",
        }

    max_seconds = _coerce_int(args.max_seconds, field_name="max-seconds", max_value=MAX_RECORDING_SECONDS)
    normalized_input_device = normalize_input_device(args.input_device)
    audio_path, log_path = _allocate_recording_artifacts()

    def remove_started_artifact(path: Path, suffix: str) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        file_stat = _recording_artifact_stat(path)
        if file_stat is None:
            return False
        return remove_file(str(path), suffix=suffix, expected_stat=file_stat)

    def cleanup_started_artifacts() -> bool:
        audio_deleted = remove_started_artifact(audio_path, ".wav")
        log_deleted = remove_started_artifact(log_path, ".log")
        return audio_deleted and log_deleted

    def cleanup_unpersisted_startup(
        primary_error: BaseException,
        process: subprocess.Popen[bytes] | None,
        *,
        expected_process_identity: str | None = None,
        expected_recorder_scope: str | None = None,
    ) -> None:
        artifacts_safe_to_remove = process is None
        if process is not None:
            if expected_process_identity is None:
                primary_error.add_note("recorder process identity could not be verified; process cleanup skipped")
            else:
                try:
                    stop_kwargs: dict[str, object] = {
                        "expected_process_identity": expected_process_identity,
                    }
                    if expected_recorder_scope:
                        stop_kwargs["expected_recorder_scope"] = expected_recorder_scope
                    stopped = stop_process(process.pid, **stop_kwargs)
                except BaseException:
                    primary_error.add_note("recorder process cleanup failed")
                    artifacts_safe_to_remove = False
                else:
                    if stopped:
                        artifacts_safe_to_remove = True
                    else:
                        try:
                            liveness_snapshot = _recorder_process_liveness_snapshot(
                                process,
                                expected_recorder_scope=expected_recorder_scope,
                            )
                            process_gone = liveness_snapshot[0]
                        except BaseException:
                            process_gone = False
                            artifacts_safe_to_remove = False
                            primary_error.add_note("recorder process could not be stopped safely")
                        else:
                            artifacts_safe_to_remove = process_gone
                            if not process_gone:
                                primary_error.add_note("recorder process could not be stopped safely")
                            else:
                                primary_error.add_note("recorder process stop was not confirmed")
            if not artifacts_safe_to_remove:
                try:
                    retained = _retain_finalization_lock_for_process(
                        finalization_lock_path,
                        process.pid,
                        expected_process_identity,
                        recorder_scope=expected_recorder_scope,
                    )
                except BaseException:
                    retained = False
                    primary_error.add_note("recorder lifecycle lock retention failed")
                if not retained:
                    primary_error.add_note("recorder lifecycle lock could not be retained")
                    if lock_release_state is not None:
                        lock_release_state["release"] = False
        if artifacts_safe_to_remove:
            try:
                if not cleanup_started_artifacts():
                    primary_error.add_note("recorder artifacts could not be cleaned")
            except BaseException:
                primary_error.add_note("recorder artifact cleanup failed")
        else:
            primary_error.add_note("recorder artifacts preserved because process stop was not confirmed")

    def reset_recording_artifacts() -> None:
        nonlocal audio_path, log_path
        if not cleanup_started_artifacts():
            raise RuntimeError("failed to clean recording artifacts after recorder startup failure")
        audio_path, log_path = _allocate_recording_artifacts()

    recorder_preferences = ["pw-record", "parecord", "arecord"] if args.recorder == "auto" else [args.recorder]
    startup_errors: list[str] = []
    command: RecorderCommand | None = None
    proc: subprocess.Popen[bytes] | None = None
    candidate_recorder_scope = ""
    for recorder_preference in recorder_preferences:
        candidate_proc: subprocess.Popen[bytes] | None = None
        candidate_process_identity: str | None = None
        candidate_scope_for_attempt: str | None = None
        start_backend_control_error = False
        try:
            candidate = choose_recorder(recorder_preference, audio_path, max_seconds, normalized_input_device)
            try:
                candidate_proc = start_recorder(candidate, log_path)
            except (KeyboardInterrupt, SystemExit):
                start_backend_control_error = True
                raise
        except Exception as exc:
            if isinstance(exc, RecorderStartupError) and exc.cleanup_incomplete:
                _retain_incomplete_recorder_startup_lock(
                    exc,
                    finalization_lock_path,
                    lock_release_state=lock_release_state,
                )
                # start_recorder still owns a live-or-uncertain child.  Keep
                # its lifecycle lock and never rotate artifacts or try a
                # second backend.
                raise
            if _recorder_start_cleanup_is_incomplete(exc):
                # start_recorder still owns a live-or-uncertain child.  Do not
                # rotate artifacts and launch another backend into that race.
                raise
            startup_errors.append(f"{recorder_preference}: {exc}")
            if args.recorder != "auto":
                if not cleanup_started_artifacts():
                    raise RuntimeError("failed to clean recording artifacts after recorder startup failure") from exc
                raise
            reset_recording_artifacts()
            continue
        except BaseException as exc:
            ownership = recorder_startup_ownership(exc)
            if ownership is not None:
                if ownership.cleanup_incomplete:
                    _retain_incomplete_recorder_startup_lock(
                        exc,
                        finalization_lock_path,
                        lock_release_state=lock_release_state,
                    )
                if start_backend_control_error:
                    _raise_foreign_backend_sanitized_exception(
                        exc,
                        message=TRANSIENT_RECORDING_PROCESS_ERROR,
                    )
                raise
            cleanup_unpersisted_startup(
                exc,
                candidate_proc,
                expected_recorder_scope=_recorder_scope_for_process(candidate_proc),
            )
            if start_backend_control_error:
                _raise_foreign_backend_sanitized_exception(
                    exc,
                    message=TRANSIENT_RECORDING_PROCESS_ERROR,
                )
            raise
        try:
            candidate_process_identity = _recording_process_identity_for_pid(candidate_proc.pid)
        except BaseException as exc:
            cleanup_unpersisted_startup(
                exc,
                candidate_proc,
                expected_recorder_scope=_recorder_scope_for_process(candidate_proc),
            )
            raise
        candidate_scope_for_attempt = _recorder_scope_for_process(candidate_proc)
        caller_scope_for_attempt = _recorder_scope_for_pid(os.getpid())
        scope_is_caller_scope = (
            candidate_scope_for_attempt is not None
            and caller_scope_for_attempt is not None
            and candidate_scope_for_attempt == caller_scope_for_attempt
        )
        if (
            candidate_scope_for_attempt is not None
            and scope_is_caller_scope
        ) or (
            os.environ.get(SOC_PRIORITY_SCOPE_MARKER) == "1"
            and (candidate_scope_for_attempt is None or caller_scope_for_attempt is None)
        ):
            scope_error = RuntimeError(
                "recording ownership scope is not dedicated; recorder state was not persisted"
            )
            cleanup_unpersisted_startup(
                scope_error,
                candidate_proc,
                expected_process_identity=candidate_process_identity,
                expected_recorder_scope=(
                    None if scope_is_caller_scope else candidate_scope_for_attempt
                ),
            )
            raise scope_error
        if candidate_scope_for_attempt is not None and not _recorder_scope_is_current(
            candidate_proc.pid,
            candidate_scope_for_attempt,
        ):
            scope_error = RuntimeError(
                "recording ownership scope could not be verified; recorder state was not persisted"
            )
            cleanup_unpersisted_startup(
                scope_error,
                candidate_proc,
                expected_process_identity=candidate_process_identity,
                expected_recorder_scope=candidate_scope_for_attempt,
            )
            raise scope_error
        try:
            time.sleep(RECORDER_START_GRACE_SECONDS)
            candidate_running = candidate_proc.poll() is None
        except BaseException as exc:
            cleanup_unpersisted_startup(
                exc,
                candidate_proc,
                expected_process_identity=candidate_process_identity,
                expected_recorder_scope=candidate_scope_for_attempt,
            )
            raise
        if candidate_running:
            if candidate_scope_for_attempt:
                try:
                    persisted_identity = _recording_process_identity_for_pid(candidate_proc.pid)
                    persisted_scope = _recorder_scope_for_pid(candidate_proc.pid)
                    persisted_caller_scope = _recorder_scope_for_pid(os.getpid())
                    persisted_scope_unit = _recorder_scope_unit_for_process(candidate_proc)
                    scope_revalidated = (
                        _recording_process_identity_relation(
                            candidate_proc.pid,
                            candidate_process_identity,
                            persisted_identity,
                        )
                        == _PROCESS_IDENTITY_SAME
                        and persisted_scope == candidate_scope_for_attempt
                        and persisted_scope is not None
                        and _recorder_scope_matches_unit(persisted_scope, persisted_scope_unit)
                        and _recorder_scope_is_current(candidate_proc.pid, persisted_scope)
                        and not (
                            persisted_caller_scope is not None
                            and persisted_caller_scope == persisted_scope
                        )
                    )
                except BaseException as exc:
                    cleanup_unpersisted_startup(
                        exc,
                        candidate_proc,
                        expected_process_identity=candidate_process_identity,
                        expected_recorder_scope=candidate_scope_for_attempt,
                    )
                    raise
                if not scope_revalidated:
                    scope_error = RuntimeError(
                        "recording ownership scope changed before persistence; recorder state was not persisted"
                    )
                    cleanup_unpersisted_startup(
                        scope_error,
                        candidate_proc,
                        expected_process_identity=candidate_process_identity,
                        expected_recorder_scope=candidate_scope_for_attempt,
                    )
                    raise scope_error
                candidate_process_identity = persisted_identity
            command = candidate
            proc = candidate_proc
            candidate_recorder_scope = candidate_scope_for_attempt or ""
            break
        detail = read_log_excerpt(log_path) or f"exit code {candidate_proc.returncode}"
        startup_errors.append(f"{candidate.name} exited immediately: {detail}")
        if candidate_process_identity is None:
            error = RuntimeError(
                f"{startup_errors[-1]}; recorder process identity could not be verified; "
                "recorder artifacts were preserved"
            )
            if not _retain_finalization_lock_for_process(
                finalization_lock_path,
                candidate_proc.pid,
                recorder_scope=candidate_scope_for_attempt,
            ):
                error.add_note("recorder lifecycle lock could not be retained")
                if lock_release_state is not None:
                    lock_release_state["release"] = False
            raise error
        try:
            stop_kwargs = {"expected_process_identity": candidate_process_identity}
            if candidate_scope_for_attempt:
                stop_kwargs["expected_recorder_scope"] = candidate_scope_for_attempt
            stopped = stop_process(candidate_proc.pid, **stop_kwargs)
        except BaseException as cleanup_error:
            if not _retain_finalization_lock_for_process(
                finalization_lock_path,
                candidate_proc.pid,
                candidate_process_identity,
                recorder_scope=candidate_scope_for_attempt,
            ):
                cleanup_error.add_note("recorder lifecycle lock could not be retained")
                if lock_release_state is not None:
                    lock_release_state["release"] = False
            if isinstance(cleanup_error, Exception):
                raise RuntimeError(f"{startup_errors[-1]}; recorder process cleanup failed") from cleanup_error
            cleanup_error.add_note("recorder process cleanup failed")
            raise
        if not stopped:
            liveness_snapshot = _recorder_process_liveness_snapshot_for_failure(
                candidate_proc,
                finalization_lock_path=finalization_lock_path,
                process_identity=candidate_process_identity,
                expected_recorder_scope=candidate_scope_for_attempt or None,
                lock_release_state=lock_release_state,
            )
            if not liveness_snapshot[0]:
                error = RuntimeError(
                    f"{startup_errors[-1]}; {liveness_snapshot[1]}"
                )
                if not _retain_finalization_lock_for_process(
                    finalization_lock_path,
                    candidate_proc.pid,
                    candidate_process_identity,
                    recorder_scope=candidate_scope_for_attempt,
                ):
                    error.add_note("recorder lifecycle lock could not be retained")
                    if lock_release_state is not None:
                        lock_release_state["release"] = False
                raise error
        if args.recorder != "auto":
            if not cleanup_started_artifacts():
                raise RuntimeError("failed to clean recording artifacts after recorder exited") from None
            raise RuntimeError(startup_errors[-1])
        reset_recording_artifacts()
    if command is None or proc is None:
        if not cleanup_started_artifacts():
            raise RuntimeError("failed to clean recording artifacts after recorder startup failures")
        detail = "; ".join(startup_errors) if startup_errors else "no supported recorder found"
        raise RuntimeError(f"no recorder backend started successfully: {detail}")

    process_identity = candidate_process_identity or _recording_process_identity_for_pid(proc.pid)
    if process_identity is None:
        identity_error = RuntimeError(
            "recording process identity could not be verified; recorder artifacts were preserved"
        )
        cleanup_unpersisted_startup(
            identity_error,
            proc,
            expected_recorder_scope=candidate_recorder_scope or None,
        )
        raise identity_error
    language = args.language or "en"
    state = RecordingState(
        status="recording",
        pid=proc.pid,
        process_identity=process_identity,
        recorder_scope=candidate_recorder_scope,
        audio_path=str(audio_path),
        log_path=str(log_path),
        started_at=now_iso(),
        language=language,
        recorder=command.name,
        max_seconds=max_seconds,
        input_device=normalized_input_device,
    )
    try:
        store.write(state)
    except Exception as state_error:
        try:
            stop_kwargs = {"expected_process_identity": process_identity}
            if candidate_recorder_scope:
                stop_kwargs["expected_recorder_scope"] = candidate_recorder_scope
            stopped = stop_process(proc.pid, **stop_kwargs)
        except Exception as cleanup_error:
            if not _retain_finalization_lock_for_process(
                finalization_lock_path,
                proc.pid,
                process_identity,
                recorder_scope=candidate_recorder_scope or None,
            ):
                cleanup_error.add_note("recorder lifecycle lock could not be retained")
                if lock_release_state is not None:
                    lock_release_state["release"] = False
            raise RuntimeError(f"{state_error}; recorder process cleanup failed") from cleanup_error
        if not stopped:
            liveness_snapshot = _recorder_process_liveness_snapshot_for_failure(
                proc,
                finalization_lock_path=finalization_lock_path,
                process_identity=process_identity,
                expected_recorder_scope=candidate_recorder_scope or None,
                lock_release_state=lock_release_state,
            )
            if not liveness_snapshot[0]:
                error = RuntimeError(
                    f"{state_error}; {liveness_snapshot[1]}"
                )
                if not _retain_finalization_lock_for_process(
                    finalization_lock_path,
                    proc.pid,
                    process_identity,
                    recorder_scope=candidate_recorder_scope or None,
                ):
                    error.add_note("recorder lifecycle lock could not be retained")
                    if lock_release_state is not None:
                        lock_release_state["release"] = False
                raise error from state_error
        if not cleanup_started_artifacts():
            raise RuntimeError(f"{state_error}; recorder artifacts could not be cleaned") from state_error
        raise
    except BaseException as state_error:
        cleanup_unpersisted_startup(
            state_error,
            proc,
            expected_process_identity=process_identity,
            expected_recorder_scope=candidate_recorder_scope or None,
        )
        raise
    artifact_cleanup = _enforce_recording_artifact_cap(state, state_path=store.path)
    cleanup_failed_paths = _cleanup_failed_paths(artifact_cleanup)
    message = "recording started"
    if cleanup_failed_paths:
        message = f"{message}; {_cleanup_failure_error(cleanup_failed_paths)}"
    return {
        "status": "recording",
        "message": message,
        "pid_present": bool(proc.pid),
        "process_identity_present": bool(process_identity),
        "audio_path_present": True,
        "recorder": command.name,
        "input_device": normalized_input_device,
        "language": language,
        "recording_artifact_cap": _public_cleanup_result(artifact_cleanup),
        **({"cleanup_failed_path_count": len(cleanup_failed_paths)} if cleanup_failed_paths else {}),
    }


def command_start(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        current = store.read()
        _raise_if_state_unreadable(current)
        if (
            current.pending_cleanup_owner_paths
            or current.pending_cleanup_restore_owner_paths
            or current.pending_cleanup_backup_entries
            or current.cleanup_backup_journal_overflow
        ):
            return {
                "status": "error",
                "message": (
                    "previous recording cleanup is unresolved; "
                    "run cancel before starting a new recording"
                ),
            }
        return {
            "status": "finalizing",
            "message": "recording lifecycle in progress; wait for completion",
        }
    lock_release_state = {"release": True}
    try:
        return _command_start_locked(
            args,
            store,
            finalization_lock_path=lock_path,
            lock_release_state=lock_release_state,
        )
    finally:
        if lock_release_state["release"]:
            _release_finalization_lock(lock_path)


def _finalize_non_recording_state_with_lock(args: argparse.Namespace, store: StateStore) -> dict[str, object]:
    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        return {"status": "finalizing", "message": "finalization already in progress"}
    try:
        state = store.read()
        _raise_if_state_unreadable(state)
        if state.status in {"recorded", "processing"}:
            return finalize_recording(args, store, state, finalization_lock_path=lock_path)
        if state.status == "finalizing":
            if state.audio_path or state.transcript_path:
                return finalize_recording(args, store, state, finalization_lock_path=lock_path)
            return {"status": "finalizing", "message": "finalization in progress"}
        return {"status": state.status, "message": "not recording"}
    finally:
        _release_finalization_lock(lock_path)


def _cleanup_backup_prefix(path: Path) -> str:
    digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
    return f".cleanup.{digest}."


def _cleanup_backup_state_namespace(state_path: Path) -> str:
    canonical_state_path = state_path.resolve(strict=False)
    return hashlib.sha256(
        str(canonical_state_path).encode("utf-8")
    ).hexdigest()[:32]


def _cleanup_backup_v2_prefix(
    path: Path,
    state_path: Path,
    *,
    state_namespace: str | None = None,
) -> str:
    effective_namespace = state_namespace
    if effective_namespace is None:
        effective_namespace = _cleanup_backup_state_namespace(state_path)
    owner_digest = hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()[:32]
    return f".cleanup.v2.{effective_namespace}.{owner_digest}."


def _cleanup_backup_v3_prefix(
    path: Path,
    state_path: Path,
    *,
    state_namespace: str | None = None,
) -> str:
    effective_namespace = state_namespace
    if effective_namespace is None:
        effective_namespace = _cleanup_backup_state_namespace(state_path)
    owner_digest = hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()[:32]
    return f".cleanup.v3.{effective_namespace}.{owner_digest}."


def _cleanup_backup_journal_entry_belongs_to_state(
    entry: str,
    state_path: Path,
    legacy_owner_paths: (
        tuple[Path, ...] | set[Path] | frozenset[Path]
    ) = frozenset(),
    *,
    legacy_owner_prefixes: frozenset[str] | None = None,
    state_namespace: str | None = None,
) -> bool:
    backup_name = entry.split("|", 1)[0]
    parts = backup_name.split(".")
    if len(parts) == 5:
        owner_prefix = _cleanup_backup_owner_prefix_from_name(backup_name)
        return (
            _is_cleanup_backup_journal_basename(backup_name)
            and owner_prefix is not None
            and (
                owner_prefix in legacy_owner_prefixes
                if legacy_owner_prefixes is not None
                else any(
                    _cleanup_backup_prefix(owner_path) == owner_prefix
                    for owner_path in legacy_owner_paths
                )
            )
        )
    effective_namespace = state_namespace
    if effective_namespace is None:
        effective_namespace = _cleanup_backup_state_namespace(state_path)
    if (
        not _is_cleanup_backup_journal_basename(backup_name)
        or len(parts) not in {7, 8, 9}
        or parts[2] not in {"v2", "v3"}
        or parts[3] != effective_namespace
    ):
        return False
    return any(
        backup_name.startswith(
            _cleanup_backup_v2_prefix(
                owner_path,
                state_path,
                state_namespace=effective_namespace,
            )
            if parts[2] == "v2"
            else _cleanup_backup_v3_prefix(
                owner_path,
                state_path,
                state_namespace=effective_namespace,
            )
        )
        for owner_path in legacy_owner_paths
    )


def _cleanup_backup_owner_prefix_from_name(name: str) -> str | None:
    parts = name.split(".")
    lowercase_hex = frozenset("0123456789abcdef")
    if (
        len(parts) >= 5
        and parts[0] == ""
        and parts[1] == "cleanup"
        and len(parts[2]) == 16
        and all(character in lowercase_hex for character in parts[2])
    ):
        return f".cleanup.{parts[2]}."
    if (
        len(parts) >= 7
        and parts[0] == ""
        and parts[1] == "cleanup"
        and parts[2] == "v2"
        and len(parts[3]) == 32
        and len(parts[4]) == 32
        and all(character in lowercase_hex for character in parts[3])
        and all(character in lowercase_hex for character in parts[4])
    ):
        return f".cleanup.v2.{parts[3]}.{parts[4]}."
    if (
        len(parts) >= 8
        and parts[0] == ""
        and parts[1] == "cleanup"
        and parts[2] == "v3"
        and len(parts[3]) == 32
        and len(parts[4]) == 32
        and all(character in lowercase_hex for character in parts[3])
        and all(character in lowercase_hex for character in parts[4])
    ):
        return f".cleanup.v3.{parts[3]}.{parts[4]}."
    return None


def _is_cleanup_backup_journal_basename(name: str) -> bool:
    parts = name.split(".")
    lowercase_hex = frozenset("0123456789abcdef")
    v1_name = (
        len(parts) == 5
        and parts[0] == ""
        and parts[1] == "cleanup"
        and len(parts[2]) == 16
        and len(parts[3]) == 16
        and parts[4] == "bak"
        and all(character in lowercase_hex for character in parts[2])
        and all(character in lowercase_hex for character in parts[3])
    )
    v2_name = (
        len(parts) == 7
        and parts[0] == ""
        and parts[1] == "cleanup"
        and parts[2] == "v2"
        and len(parts[3]) == 32
        and len(parts[4]) == 32
        and len(parts[5]) == 32
        and parts[6] == "bak"
        and all(character in lowercase_hex for character in parts[3])
        and all(character in lowercase_hex for character in parts[4])
        and all(character in lowercase_hex for character in parts[5])
    )
    v3_name = (
        len(parts) == 8
        and parts[0] == ""
        and parts[1] == "cleanup"
        and parts[2] == "v3"
        and len(parts[3]) == 32
        and len(parts[4]) == 32
        and parts[5] in {"intent", "staged", "wiping"}
        and len(parts[6]) == 64
        and parts[7] == "bak"
        and all(character in lowercase_hex for character in parts[3])
        and all(character in lowercase_hex for character in parts[4])
        and all(character in lowercase_hex for character in parts[6])
    )
    v3_bound_name = (
        len(parts) == 9
        and parts[0] == ""
        and parts[1] == "cleanup"
        and parts[2] == "v3"
        and len(parts[3]) == 32
        and len(parts[4]) == 32
        and parts[5] in {"intent", "claimed", "prepared", "armed", "wiping"}
        and len(parts[6]) == 64
        and len(parts[7]) == _CLEANUP_CLAIM_NONCE_HEX_CHARS
        and parts[8] == "bak"
        and all(character in lowercase_hex for character in parts[3])
        and all(character in lowercase_hex for character in parts[4])
        and all(character in lowercase_hex for character in parts[6])
        and all(character in lowercase_hex for character in parts[7])
    )
    return v1_name or v2_name or v3_name or v3_bound_name


def _cleanup_backup_journal_entry(
    path: Path,
    file_stat: os.stat_result,
) -> str:
    if not _is_cleanup_backup_journal_basename(path.name):
        raise RuntimeError("cleanup backup name cannot be journaled")
    identity = (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        getattr(file_stat, "st_nlink", 1),
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )
    return _cleanup_backup_journal_entry_from_identity(path.name, identity)


def _cleanup_backup_journal_entry_from_identity(
    name: str,
    identity: tuple[int, int, int, int, int, int, int],
) -> str:
    if not _is_cleanup_backup_journal_basename(name):
        raise RuntimeError("cleanup backup name cannot be journaled")
    if len(identity) != 7 or any(
        isinstance(value, bool)
        or not isinstance(value, int)
        or value < 0
        for value in identity
    ):
        raise RuntimeError("cleanup backup identity cannot be journaled")
    return "|".join((name, *(str(value) for value in identity)))


def _parse_cleanup_backup_journal_entry(
    entry: str,
) -> tuple[str, tuple[int, int, int, int, int, int, int]]:
    parts = entry.split("|")
    if len(parts) != 8 or not _is_cleanup_backup_journal_basename(parts[0]):
        raise RuntimeError("cleanup backup journal entry is invalid")
    try:
        identity_values = tuple(int(value) for value in parts[1:])
    except ValueError as exc:
        raise RuntimeError("cleanup backup journal entry is invalid") from exc
    if len(identity_values) != 7:
        raise RuntimeError("cleanup backup journal entry is invalid")
    return parts[0], identity_values  # type: ignore[return-value]


def _cleanup_backup_journal_identity_matches(
    file_stat: os.stat_result,
    identity: tuple[int, int, int, int, int, int, int],
) -> bool:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        getattr(file_stat, "st_nlink", 1),
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    ) == identity


def _cleanup_backup_journal_claim_matches(
    file_stat: os.stat_result,
    identity: tuple[int, int, int, int, int, int, int],
    *,
    fields: int,
) -> bool:
    if fields not in {5, 6, 7}:
        return False
    current_identity = (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        getattr(file_stat, "st_nlink", 1),
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )
    return current_identity[:fields] == identity[:fields]


def _bound_cleanup_v3_basename_parts(
    name: str,
    *,
    phases: frozenset[str],
) -> tuple[str, str, str, str, str] | None:
    if type(name) is not str or not name.isascii():
        return None
    parts = name.split(".")
    lowercase_hex = frozenset("0123456789abcdef")
    if (
        len(parts) != 9
        or parts[:3] != ["", "cleanup", "v3"]
        or len(parts[3]) != 32
        or len(parts[4]) != 32
        or parts[5] not in phases
        or len(parts[6]) != 64
        or len(parts[7]) != _CLEANUP_CLAIM_NONCE_HEX_CHARS
        or parts[8] != "bak"
        or any(
            any(character not in lowercase_hex for character in parts[index])
            for index in (3, 4, 6, 7)
        )
    ):
        return None
    return parts[3], parts[4], parts[5], parts[6], parts[7]


def _tokenless_cleanup_v3_basename_parts(
    name: str,
) -> tuple[str, str, str, str] | None:
    if type(name) is not str or not name.isascii():
        return None
    parts = name.split(".")
    lowercase_hex = frozenset("0123456789abcdef")
    if (
        len(parts) != 8
        or parts[:3] != ["", "cleanup", "v3"]
        or len(parts[3]) != 32
        or len(parts[4]) != 32
        or parts[5] not in {"intent", "staged", "wiping"}
        or len(parts[6]) != 64
        or parts[7] != "bak"
        or any(
            any(character not in lowercase_hex for character in parts[index])
            for index in (3, 4, 6)
        )
    ):
        return None
    return parts[3], parts[4], parts[5], parts[6]


def _bound_cleanup_v3_tombstone_parts(
    name: str,
) -> tuple[str, str, str, str] | None:
    if type(name) is not str or not name.isascii():
        return None
    parts = name.split(".")
    lowercase_hex = frozenset("0123456789abcdef")
    if (
        len(parts) != 9
        or parts[:3] != ["", "cleanup", "v3"]
        or len(parts[3]) != 32
        or len(parts[4]) != 32
        or parts[5] != "tombstone"
        or len(parts[6]) != 64
        or len(parts[7]) != _CLEANUP_CLAIM_NONCE_HEX_CHARS
        or parts[8] != "done"
        or any(
            any(character not in lowercase_hex for character in parts[index])
            for index in (3, 4, 6, 7)
        )
    ):
        return None
    return parts[3], parts[4], parts[6], parts[7]


def _cleanup_claim_physical_basename(journal_name: str) -> str | None:
    parts = _bound_cleanup_v3_basename_parts(
        journal_name,
        phases=frozenset({"intent", "claimed", "prepared", "armed", "wiping"}),
    )
    if parts is None:
        return None
    state_namespace, owner_namespace, _phase, digest, nonce = parts
    return (
        f".cleanup.v3.{state_namespace}.{owner_namespace}.tombstone."
        f"{digest}.{nonce}.done"
    )


def _cleanup_claim_journal_basename(
    claim_name: str,
    phase: str,
) -> str:
    parts = _bound_cleanup_v3_tombstone_parts(claim_name)
    if parts is None or phase not in {
        "intent",
        "claimed",
        "prepared",
        "armed",
        "wiping",
    }:
        raise RuntimeError("cleanup claim binding is invalid")
    state_namespace, owner_namespace, digest, nonce = parts
    return (
        f".cleanup.v3.{state_namespace}.{owner_namespace}.{phase}."
        f"{digest}.{nonce}.bak"
    )


def _cleanup_claim_commit_trailer(
    claim_name: str,
    content_digest: str,
    original_size: int,
    *,
    authorized: bool = False,
) -> bytes:
    if (
        _bound_cleanup_v3_tombstone_parts(claim_name) is None
        or type(content_digest) is not str
        or len(content_digest) != 64
        or any(character not in "0123456789abcdef" for character in content_digest)
        or type(original_size) is not int
        or original_size < 0
        or original_size > _MAX_CLEANUP_CLAIM_FILE_BYTES
    ):
        raise RuntimeError("cleanup claim commit is invalid")
    binding = hashlib.sha256(
        b"SOC-CLEANUP-V3-WIPING-BINDING\x00"
        + claim_name.encode("ascii")
    ).hexdigest()
    return (
        _CLEANUP_CLAIM_COMMIT_MAGIC
        + binding.encode("ascii")
        + f"{original_size:0{_CLEANUP_CLAIM_COMMIT_SIZE_HEX_BYTES}x}".encode(
            "ascii"
        )
        + content_digest.encode("ascii")
        + (
            b"C" + _CLEANUP_CLAIM_AUTHORIZATION_SUFFIX
            if authorized
            else b"P"
        )
    )


def _cleanup_claim_fd_digest(file_fd: int, size: int) -> str:
    pread = getattr(os, "pread", None)
    if (
        type(file_fd) is not int
        or file_fd < 0
        or type(size) is not int
        or size < 0
        or size > _MAX_CLEANUP_CLAIM_FILE_BYTES
        or not callable(pread)
    ):
        raise RuntimeError("cleanup content binding failed")
    digest = hashlib.sha256()
    offset = 0
    interrupts = 0
    read_calls = 0
    max_read_calls = (
        size // _CLEANUP_CLAIM_HASH_CHUNK_BYTES
        + _MAX_CLEANUP_CLAIM_HASH_INTERRUPTS
        + 2
    )
    while offset < size:
        if read_calls >= max_read_calls:
            raise RuntimeError("cleanup content binding failed")
        requested = min(
            _CLEANUP_CLAIM_HASH_CHUNK_BYTES,
            size - offset,
        )
        try:
            chunk = pread(file_fd, requested, offset)
        except InterruptedError:
            interrupts += 1
            if interrupts > _MAX_CLEANUP_CLAIM_HASH_INTERRUPTS:
                raise RuntimeError("cleanup content binding failed") from None
            continue
        read_calls += 1
        if not isinstance(chunk, bytes) or not chunk or len(chunk) > requested:
            raise RuntimeError("cleanup content binding failed")
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _cleanup_claim_read_exact(
    file_fd: int,
    size: int,
    offset: int,
) -> bytes:
    pread = getattr(os, "pread", None)
    if (
        type(file_fd) is not int
        or file_fd < 0
        or type(size) is not int
        or size < 0
        or type(offset) is not int
        or offset < 0
        or not callable(pread)
    ):
        raise RuntimeError("cleanup content binding failed")
    result = bytearray()
    interrupts = 0
    calls = 0
    max_calls = size + _MAX_CLEANUP_CLAIM_HASH_INTERRUPTS + 1
    while len(result) < size:
        if calls >= max_calls:
            raise RuntimeError("cleanup content binding failed")
        try:
            chunk = pread(file_fd, size - len(result), offset + len(result))
        except InterruptedError:
            interrupts += 1
            if interrupts > _MAX_CLEANUP_CLAIM_HASH_INTERRUPTS:
                raise RuntimeError("cleanup content binding failed") from None
            continue
        calls += 1
        if not isinstance(chunk, bytes) or not chunk:
            raise RuntimeError("cleanup content binding failed")
        result.extend(chunk)
    if len(result) != size:
        raise RuntimeError("cleanup content binding failed")
    return bytes(result)


def _cleanup_claim_content_snapshot(
    path: Path,
    identity: tuple[int, int, int, int, int, int, int],
    *,
    identity_fields: int,
) -> tuple[str, os.stat_result] | None:
    parent_fd: int | None = None
    file_fd: int | None = None
    primary_error: BaseException | None = None
    result: tuple[str, os.stat_result] | None = None
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
            or isinstance(cloexec_flag, bool)
            or not isinstance(cloexec_flag, int)
            or cloexec_flag <= 0
            or not callable(getattr(os, "pread", None))
        ):
            raise RuntimeError("cleanup content binding is unavailable")
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup directory",
        )
        file_fd = os.open(
            path.name,
            os.O_RDONLY
            | os.O_NONBLOCK
            | nofollow_flag
            | cloexec_flag,
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(file_fd)
        if (
            not stat_module.S_ISREG(opened_stat.st_mode)
            or getattr(opened_stat, "st_nlink", 1) != 1
            or opened_stat.st_size > _MAX_CLEANUP_CLAIM_FILE_BYTES
            or not _cleanup_backup_journal_claim_matches(
                opened_stat,
                identity,
                fields=identity_fields,
            )
        ):
            raise RuntimeError("cleanup content binding failed")
        digest = _cleanup_claim_fd_digest(file_fd, opened_stat.st_size)
        if os.pread(file_fd, 1, opened_stat.st_size) != b"":
            raise RuntimeError("cleanup content binding failed")
        if not _same_leaf_identity(os.fstat(file_fd), opened_stat):
            raise RuntimeError("cleanup content binding failed")
        result = digest, opened_stat
    except (KeyboardInterrupt, SystemExit) as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = exc
    finally:
        for descriptor in (file_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        raise primary_error
    if primary_error is not None:
        return None
    return result


def _commit_cleanup_claim(
    path: Path,
    identity: tuple[int, int, int, int, int, int, int],
    content_digest: str,
) -> os.stat_result | None:
    parent_fd: int | None = None
    file_fd: int | None = None
    primary_error: BaseException | None = None
    result: os.stat_result | None = None
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        pwrite = getattr(os, "pwrite", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
            or isinstance(cloexec_flag, bool)
            or not isinstance(cloexec_flag, int)
            or cloexec_flag <= 0
            or not callable(pwrite)
        ):
            raise RuntimeError("cleanup claim commit is unavailable")
        trailer = _cleanup_claim_commit_trailer(
            path.name,
            content_digest,
            identity[4],
        )
        if identity[4] > _MAX_CLEANUP_CLAIM_FILE_BYTES - len(trailer):
            raise RuntimeError("cleanup claim commit is invalid")
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup directory",
        )
        file_fd = os.open(
            path.name,
            os.O_RDWR | os.O_NONBLOCK | nofollow_flag | cloexec_flag,
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(file_fd)
        original_size = identity[4]
        if (
            not stat_module.S_ISREG(opened_stat.st_mode)
            or getattr(opened_stat, "st_nlink", 1) != 1
            or (
                opened_stat.st_dev,
                opened_stat.st_ino,
                opened_stat.st_mode,
                getattr(opened_stat, "st_nlink", 1),
            )
            != identity[:4]
        ):
            raise RuntimeError("cleanup claim commit failed")
        if opened_stat.st_size == original_size:
            pass
        elif original_size < opened_stat.st_size < original_size + len(trailer):
            existing_trailer = _cleanup_claim_read_exact(
                file_fd,
                opened_stat.st_size - original_size,
                original_size,
            )
            if (
                not secrets.compare_digest(
                    existing_trailer,
                    trailer[: len(existing_trailer)],
                )
                or not secrets.compare_digest(
                    _cleanup_claim_fd_digest(file_fd, original_size),
                    content_digest,
                )
                or os.pread(file_fd, 1, opened_stat.st_size) != b""
            ):
                raise RuntimeError("cleanup claim commit failed")
            os.ftruncate(file_fd, original_size)
            _fsync_fd(file_fd)
            opened_stat = os.fstat(file_fd)
            if (
                (
                    opened_stat.st_dev,
                    opened_stat.st_ino,
                    opened_stat.st_mode,
                    getattr(opened_stat, "st_nlink", 1),
                )
                != identity[:4]
                or opened_stat.st_size != original_size
            ):
                raise RuntimeError("cleanup claim commit failed")
        elif opened_stat.st_size in {
            original_size + len(trailer),
            original_size
            + len(
                _cleanup_claim_commit_trailer(
                    path.name,
                    content_digest,
                    original_size,
                    authorized=True,
                )
            ),
        }:
            prepared_trailer = trailer
            authorized_trailer = _cleanup_claim_commit_trailer(
                path.name,
                content_digest,
                original_size,
                authorized=True,
            )
            intermediate_trailer = prepared_trailer[:-1] + b"C"
            existing_size = opened_stat.st_size - original_size
            existing_trailer = _cleanup_claim_read_exact(
                file_fd,
                existing_size,
                original_size,
            )
            if not (
                secrets.compare_digest(existing_trailer, prepared_trailer)
                or secrets.compare_digest(
                    existing_trailer,
                    authorized_trailer,
                )
                or secrets.compare_digest(
                    existing_trailer,
                    intermediate_trailer,
                )
            ) or not secrets.compare_digest(
                _cleanup_claim_fd_digest(file_fd, original_size),
                content_digest,
            ) or os.pread(file_fd, 1, opened_stat.st_size) != b"":
                raise RuntimeError("cleanup claim commit failed")
            os.ftruncate(file_fd, original_size)
            _fsync_fd(file_fd)
            opened_stat = os.fstat(file_fd)
            if (
                (
                    opened_stat.st_dev,
                    opened_stat.st_ino,
                    opened_stat.st_mode,
                    getattr(opened_stat, "st_nlink", 1),
                )
                != identity[:4]
                or opened_stat.st_size != original_size
            ):
                raise RuntimeError("cleanup claim commit failed")
        else:
            raise RuntimeError("cleanup claim commit failed")
        if (
            not secrets.compare_digest(
                _cleanup_claim_fd_digest(file_fd, original_size),
                content_digest,
            )
            or os.pread(file_fd, 1, original_size) != b""
        ):
            raise RuntimeError("cleanup claim commit failed")
        offset = 0
        interrupts = 0
        calls = 0
        max_calls = len(trailer) + _MAX_CLEANUP_CLAIM_HASH_INTERRUPTS + 1
        while offset < len(trailer):
            if calls >= max_calls:
                raise RuntimeError("cleanup claim commit failed")
            try:
                written = pwrite(
                    file_fd,
                    trailer[offset:],
                    original_size + offset,
                )
            except InterruptedError:
                interrupts += 1
                if interrupts > _MAX_CLEANUP_CLAIM_HASH_INTERRUPTS:
                    raise RuntimeError("cleanup claim commit failed") from None
                continue
            calls += 1
            if (
                type(written) is not int
                or written <= 0
                or written > len(trailer) - offset
            ):
                raise RuntimeError("cleanup claim commit failed")
            offset += written
        _fsync_fd(file_fd)
        committed_stat = os.fstat(file_fd)
        if (
            not stat_module.S_ISREG(committed_stat.st_mode)
            or getattr(committed_stat, "st_nlink", 1) != 1
            or (
                committed_stat.st_dev,
                committed_stat.st_ino,
                committed_stat.st_mode,
                getattr(committed_stat, "st_nlink", 1),
            )
            != identity[:4]
            or committed_stat.st_size != original_size + len(trailer)
            or not secrets.compare_digest(
                _cleanup_claim_read_exact(
                    file_fd,
                    len(trailer),
                    original_size,
                ),
                trailer,
            )
            or os.pread(file_fd, 1, committed_stat.st_size) != b""
        ):
            raise RuntimeError("cleanup claim commit failed")
        result = committed_stat
    except (KeyboardInterrupt, SystemExit) as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = exc
    finally:
        for descriptor in (file_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        raise primary_error
    if primary_error is not None:
        return None
    return result


def _prepare_cleanup_claim_wipe_authorization(
    path: Path,
    identity: tuple[int, int, int, int, int, int, int],
    content_digest: str,
    original_size: int,
) -> os.stat_result | None:
    parent_fd: int | None = None
    file_fd: int | None = None
    primary_error: BaseException | None = None
    result: os.stat_result | None = None
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        pwrite = getattr(os, "pwrite", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
            or isinstance(cloexec_flag, bool)
            or not isinstance(cloexec_flag, int)
            or cloexec_flag <= 0
            or not callable(pwrite)
        ):
            raise RuntimeError("cleanup claim authorization is unavailable")
        prepared_trailer = _cleanup_claim_commit_trailer(
            path.name,
            content_digest,
            original_size,
        )
        intermediate_trailer = prepared_trailer[:-1] + b"C"
        authorized_trailer = _cleanup_claim_commit_trailer(
            path.name,
            content_digest,
            original_size,
            authorized=True,
        )
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup directory",
        )
        file_fd = os.open(
            path.name,
            os.O_RDWR | os.O_NONBLOCK | nofollow_flag | cloexec_flag,
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(file_fd)
        if (
            (
                opened_stat.st_dev,
                opened_stat.st_ino,
                opened_stat.st_mode,
                getattr(opened_stat, "st_nlink", 1),
            )
            != identity[:4]
            or opened_stat.st_size
            not in {
                original_size + len(prepared_trailer),
                original_size + len(authorized_trailer),
            }
        ):
            raise RuntimeError("cleanup claim authorization failed")
        actual_trailer = _cleanup_claim_read_exact(
            file_fd,
            opened_stat.st_size - original_size,
            original_size,
        )
        prepared = secrets.compare_digest(actual_trailer, prepared_trailer)
        intermediate = secrets.compare_digest(
            actual_trailer,
            intermediate_trailer,
        )
        authorized = secrets.compare_digest(actual_trailer, authorized_trailer)
        if not (prepared or intermediate or authorized) or os.pread(
            file_fd,
            1,
            opened_stat.st_size,
        ) != b"" or not secrets.compare_digest(
            _cleanup_claim_fd_digest(file_fd, original_size),
            content_digest,
        ):
            raise RuntimeError("cleanup claim authorization failed")
        if prepared:
            if not _cleanup_backup_journal_claim_matches(
                opened_stat,
                identity,
                fields=7,
            ):
                raise RuntimeError("cleanup claim authorization failed")
            written = pwrite(
                file_fd,
                b"C",
                original_size + len(prepared_trailer) - 1,
            )
            if type(written) is not int or written != 1:
                raise RuntimeError("cleanup claim authorization failed")
            _fsync_fd(file_fd)
            authorized_stat = os.fstat(file_fd)
            if (
                (
                    authorized_stat.st_dev,
                    authorized_stat.st_ino,
                    authorized_stat.st_mode,
                    getattr(authorized_stat, "st_nlink", 1),
                    authorized_stat.st_size,
                )
                != (
                    opened_stat.st_dev,
                    opened_stat.st_ino,
                    opened_stat.st_mode,
                    getattr(opened_stat, "st_nlink", 1),
                    opened_stat.st_size,
                )
                or not secrets.compare_digest(
                    _cleanup_claim_read_exact(
                        file_fd,
                        len(intermediate_trailer),
                        original_size,
                    ),
                    intermediate_trailer,
                )
                or not secrets.compare_digest(
                    _cleanup_claim_fd_digest(file_fd, original_size),
                    content_digest,
                )
            ):
                raise RuntimeError("cleanup claim authorization failed")
            intermediate = True
        if intermediate:
            written = pwrite(
                file_fd,
                _CLEANUP_CLAIM_AUTHORIZATION_SUFFIX,
                original_size + len(intermediate_trailer),
            )
            if type(written) is not int or written != len(
                _CLEANUP_CLAIM_AUTHORIZATION_SUFFIX
            ):
                raise RuntimeError("cleanup claim authorization failed")
            _fsync_fd(file_fd)
        _fsync_fd(file_fd)
        authorized_stat = os.fstat(file_fd)
        if (
            (
                authorized_stat.st_dev,
                authorized_stat.st_ino,
                authorized_stat.st_mode,
                getattr(authorized_stat, "st_nlink", 1),
                authorized_stat.st_size,
            )
            != (
                opened_stat.st_dev,
                opened_stat.st_ino,
                opened_stat.st_mode,
                getattr(opened_stat, "st_nlink", 1),
                original_size + len(authorized_trailer),
            )
            or not secrets.compare_digest(
                _cleanup_claim_read_exact(
                    file_fd,
                    len(authorized_trailer),
                    original_size,
                ),
                authorized_trailer,
            )
            or not secrets.compare_digest(
                _cleanup_claim_fd_digest(file_fd, original_size),
                content_digest,
            )
            or os.pread(file_fd, 1, authorized_stat.st_size) != b""
        ):
            raise RuntimeError("cleanup claim authorization failed")
        named_stat = os.stat(
            path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        verified_stat = os.fstat(file_fd)
        if (
            not _same_leaf_identity(named_stat, authorized_stat)
            or not _same_leaf_identity(verified_stat, authorized_stat)
        ):
            raise RuntimeError("cleanup claim authorization failed")
        result = verified_stat
    except (KeyboardInterrupt, SystemExit) as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = exc
    finally:
        for descriptor in (file_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        raise primary_error
    if primary_error is not None:
        return None
    return result


def _legacy_cleanup_claim_authorization_snapshot(
    path: Path,
    identity: tuple[int, int, int, int, int, int, int],
    content_digest: str,
) -> tuple[int, os.stat_result] | None:
    parent_fd: int | None = None
    file_fd: int | None = None
    primary_error: BaseException | None = None
    result: tuple[int, os.stat_result] | None = None
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
            or isinstance(cloexec_flag, bool)
            or not isinstance(cloexec_flag, int)
            or cloexec_flag <= 0
        ):
            raise RuntimeError("legacy cleanup authorization is unavailable")
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup directory",
        )
        file_fd = os.open(
            path.name,
            os.O_RDONLY | os.O_NONBLOCK | nofollow_flag | cloexec_flag,
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(file_fd)
        if (
            not stat_module.S_ISREG(opened_stat.st_mode)
            or getattr(opened_stat, "st_nlink", 1) != 1
            or (
                opened_stat.st_dev,
                opened_stat.st_ino,
                opened_stat.st_mode,
                getattr(opened_stat, "st_nlink", 1),
            )
            != identity[:4]
        ):
            raise RuntimeError("legacy cleanup authorization failed")
        prepared_size = len(
            _cleanup_claim_commit_trailer(
                path.name,
                content_digest,
                0,
            )
        )
        authorized_size = len(
            _cleanup_claim_commit_trailer(
                path.name,
                content_digest,
                0,
                authorized=True,
            )
        )
        candidates: list[tuple[int, bytes]] = []
        if _cleanup_backup_journal_claim_matches(
            opened_stat,
            identity,
            fields=7,
        ):
            for trailer_size in (prepared_size, authorized_size):
                candidate_original_size = opened_stat.st_size - trailer_size
                if candidate_original_size >= 0:
                    candidates.append(
                        (candidate_original_size, b"legacy")
                    )
        elif (
            opened_stat.st_size == identity[4]
            and _cleanup_backup_journal_claim_matches(
                opened_stat,
                identity,
                fields=5,
            )
        ):
            candidate_original_size = opened_stat.st_size - prepared_size
            if candidate_original_size >= 0:
                candidates.append((candidate_original_size, b"legacy"))
        elif opened_stat.st_size == identity[4] + 1:
            candidate_original_size = identity[4] - prepared_size
            if candidate_original_size >= 0:
                candidates.append((candidate_original_size, b"upgraded"))
        matches: list[int] = []
        for candidate_original_size, candidate_kind in candidates:
            prepared = _cleanup_claim_commit_trailer(
                path.name,
                content_digest,
                candidate_original_size,
            )
            authorized = _cleanup_claim_commit_trailer(
                path.name,
                content_digest,
                candidate_original_size,
                authorized=True,
            )
            trailer_size = opened_stat.st_size - candidate_original_size
            actual = _cleanup_claim_read_exact(
                file_fd,
                trailer_size,
                candidate_original_size,
            )
            allowed = (
                (prepared, prepared[:-1] + b"C", authorized)
                if candidate_kind == b"legacy"
                else (authorized,)
            )
            if any(secrets.compare_digest(actual, value) for value in allowed):
                matches.append(candidate_original_size)
        if (
            len(matches) != 1
            or not secrets.compare_digest(
                _cleanup_claim_fd_digest(file_fd, matches[0]),
                content_digest,
            )
            or os.pread(file_fd, 1, opened_stat.st_size) != b""
        ):
            raise RuntimeError("legacy cleanup authorization failed")
        named_stat = os.stat(
            path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        verified_stat = os.fstat(file_fd)
        if (
            not _same_leaf_identity(named_stat, opened_stat)
            or not _same_leaf_identity(verified_stat, opened_stat)
        ):
            raise RuntimeError("legacy cleanup authorization failed")
        result = matches[0], verified_stat
    except (KeyboardInterrupt, SystemExit) as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = exc
    finally:
        for descriptor in (file_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        raise primary_error
    if primary_error is not None:
        return None
    return result


def _authorize_cleanup_claim_wipe(
    path: Path,
    identity: tuple[int, int, int, int, int, int, int],
    content_digest: str,
    original_size: int,
    *,
    require_content_digest: bool = False,
) -> os.stat_result | None:
    parent_fd: int | None = None
    file_fd: int | None = None
    primary_error: BaseException | None = None
    result: os.stat_result | None = None
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
            or isinstance(cloexec_flag, bool)
            or not isinstance(cloexec_flag, int)
            or cloexec_flag <= 0
            or type(require_content_digest) is not bool
        ):
            raise RuntimeError("cleanup claim authorization is unavailable")
        authorized_trailer = _cleanup_claim_commit_trailer(
            path.name,
            content_digest,
            original_size,
            authorized=True,
        )
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup directory",
        )
        file_fd = os.open(
            path.name,
            os.O_RDWR | os.O_NONBLOCK | nofollow_flag | cloexec_flag,
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(file_fd)
        if (
            not _cleanup_backup_journal_claim_matches(
                opened_stat,
                identity,
                fields=5,
            )
            or opened_stat.st_size != original_size + len(authorized_trailer)
            or not secrets.compare_digest(
                _cleanup_claim_read_exact(
                    file_fd,
                    len(authorized_trailer),
                    original_size,
                ),
                authorized_trailer,
            )
            or os.pread(file_fd, 1, opened_stat.st_size) != b""
            or (
                require_content_digest
                and not secrets.compare_digest(
                    _cleanup_claim_fd_digest(file_fd, original_size),
                    content_digest,
                )
            )
        ):
            raise RuntimeError("cleanup claim authorization failed")
        # Persisted ARMED plus this claim-bound marker authorizes finishing
        # this inode's original range. Same-UID mutation after that durable
        # boundary is not distinguishable in-process.
        wiped_stat = secure_wipe_bound_regular_fd(
            file_fd,
            opened_stat,
            field_name="recording cleanup backup",
            wipe_bytes=original_size,
            truncate_after_wipe=True,
        )
        named_stat = os.stat(
            path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            wiped_stat.st_size != 0
            or not _same_leaf_identity(named_stat, wiped_stat)
            or not _same_leaf_identity(os.fstat(file_fd), wiped_stat)
        ):
            raise RuntimeError("cleanup claim authorization failed")
        _fsync_fd(parent_fd)
        result = wiped_stat
    except (KeyboardInterrupt, SystemExit) as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = exc
    finally:
        for descriptor in (file_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        raise primary_error
    if primary_error is not None:
        return None
    return result


def _retire_zero_cleanup_tombstone(
    path: Path,
    identity: tuple[int, int, int, int, int, int, int],
) -> bool:
    parent_fd: int | None = None
    file_fd: int | None = None
    primary_error: BaseException | None = None
    retired = False
    try:
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        cloexec_flag = getattr(os, "O_CLOEXEC", None)
        if (
            isinstance(nofollow_flag, bool)
            or not isinstance(nofollow_flag, int)
            or nofollow_flag <= 0
            or isinstance(cloexec_flag, bool)
            or not isinstance(cloexec_flag, int)
            or cloexec_flag <= 0
        ):
            raise RuntimeError("cleanup tombstone retirement is unavailable")
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup directory",
        )
        file_fd = os.open(
            path.name,
            os.O_RDWR | os.O_NONBLOCK | nofollow_flag | cloexec_flag,
            dir_fd=parent_fd,
        )
        opened_stat = os.fstat(file_fd)
        if (
            not stat_module.S_ISREG(opened_stat.st_mode)
            or getattr(opened_stat, "st_nlink", 1) != 1
            or opened_stat.st_size != 0
            or (
                opened_stat.st_dev,
                opened_stat.st_ino,
                opened_stat.st_mode,
                getattr(opened_stat, "st_nlink", 1),
            )
            != identity[:4]
        ):
            raise RuntimeError("cleanup tombstone retirement failed")
        _fsync_fd(file_fd)
        synced_stat = os.fstat(file_fd)
        if not _same_leaf_identity(synced_stat, opened_stat):
            raise RuntimeError("cleanup tombstone retirement failed")
        named_stat = os.stat(
            path.name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if not _same_leaf_identity(named_stat, synced_stat):
            raise RuntimeError("cleanup tombstone retirement failed")
        os.unlink(path.name, dir_fd=parent_fd)
        unlinked_stat = os.fstat(file_fd)
        if (
            (
                unlinked_stat.st_dev,
                unlinked_stat.st_ino,
                unlinked_stat.st_mode,
                unlinked_stat.st_size,
            )
            != (
                synced_stat.st_dev,
                synced_stat.st_ino,
                synced_stat.st_mode,
                synced_stat.st_size,
            )
            or getattr(unlinked_stat, "st_nlink", 0) != 0
        ):
            raise RuntimeError("cleanup tombstone retirement failed")
        _fsync_fd(parent_fd)
        retired = True
    except (KeyboardInterrupt, SystemExit) as exc:
        primary_error = exc
    except Exception as exc:
        primary_error = exc
    finally:
        for descriptor in (file_fd, parent_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        raise primary_error
    return retired and primary_error is None


def _normalized_cleanup_backup_owner_path(
    path_value: str,
    *,
    state_path: Path,
) -> Path:
    try:
        clean_path_value = _assert_clean_text(
            path_value,
            field_name="pending cleanup owner path",
            max_chars=MAX_PENDING_CLEANUP_OWNER_PATH_CHARS,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeError("pending cleanup owner path is invalid") from exc
    owner_path = _normalized_state_recording_artifact_path(
        clean_path_value,
        suffix=(".wav", ".flac", ".log", ".socenc"),
        state_path=state_path,
        require_recordings_dir=True,
    )
    if owner_path is None or (
        owner_path.suffix.lower() not in {".wav", ".flac", ".log"}
        and not _is_encrypted_recording_artifact(owner_path)
    ):
        raise RuntimeError("pending cleanup owner path is invalid")
    return owner_path


def _cleanup_backup_restore_journal_pairs(
    state: RecordingState,
    *,
    state_path: Path,
    state_namespace: str | None = None,
    v2_prefix_cache: dict[Path, str] | None = None,
) -> tuple[tuple[Path, str], ...]:
    owners = state.pending_cleanup_restore_owner_paths
    entries = state.pending_cleanup_backup_entries
    if not owners:
        if state.cleanup_backup_journal_restore:
            raise RuntimeError("cleanup backup restore journal has no owners")
        return ()
    if len(owners) != len(entries):
        raise RuntimeError("cleanup backup restore journal is incomplete")
    pairs: list[tuple[Path, str]] = []
    validated_prefixes: dict[Path, str] = {}
    seen_prefixes: set[str] = set()
    for owner_text, entry in zip(owners, entries, strict=True):
        owner_path = _normalized_cleanup_backup_owner_path(
            owner_text,
            state_path=state_path,
        )
        backup_name, _ = _parse_cleanup_backup_journal_entry(entry)
        expected_prefix = (
            v2_prefix_cache.get(owner_path)
            if v2_prefix_cache is not None
            else None
        )
        if expected_prefix is None:
            expected_prefix = _cleanup_backup_v2_prefix(
                owner_path,
                state_path,
                state_namespace=state_namespace,
            )
        if (
            not backup_name.startswith(expected_prefix)
            or expected_prefix in seen_prefixes
        ):
            raise RuntimeError(
                "cleanup backup restore journal owner does not match entry"
            )
        seen_prefixes.add(expected_prefix)
        validated_prefixes[owner_path] = backup_name[: len(expected_prefix)]
        pairs.append((owner_path, entry))
    if v2_prefix_cache is not None:
        v2_prefix_cache.update(validated_prefixes)
    return tuple(pairs)


def finalize_recording(
    args: argparse.Namespace,
    store: StateStore,
    state: RecordingState,
    *,
    finalization_lock_path: Path | None = None,
) -> dict[str, object]:
    lock_path = finalization_lock_path if finalization_lock_path is not None else _acquire_finalization_lock(store.path)
    if lock_path is None:
        return {"status": "finalizing", "message": "finalization already in progress"}

    state_marked_finalizing = False
    written_text_path: Path | None = None
    stored_transcript_text: str | None = None
    artifact_encryption = ARTIFACT_ENCRYPTION_OFF
    preserve_written_text_on_error = False
    safe_no_output_failure = False
    insert_backend_interrupted = False
    retryable_pre_output_processing_failure = False
    silent_transcript_state_cleared = False
    inserted = False
    written_text_stat: os.stat_result | None = None
    cleanup_rollback_backups: list[tuple[Path, Path, os.stat_result, os.stat_result]] = []
    cleanup_source_stats: dict[Path, os.stat_result] = {}
    cleanup_backup_restore_failed = False
    cleanup_backup_journal_persisted = False
    cleanup_backup_restore_pairs: list[tuple[Path, str]] = []
    preserve_recording_artifacts_after_cleanup_failure = False
    preserved_encrypted_audio_path: Path | None = None
    original_audio_stat: os.stat_result | None = None
    trimmed_audio_stat: os.stat_result | None = None
    stabilized_audio_stat: os.stat_result | None = None
    persisted_audio_path: str | None = None
    persisted_log_path: str | None = None
    audio_deleted = False
    log_deleted = False
    audio_suffix = ""
    keep_recording_artifacts = False
    automatic_backup_result: dict[str, object] | None = None
    automatic_backup_settings: dict[str, object] = {}
    automatic_audio_backup_requested = False
    trimmed_audio_path: Path | None = None
    stabilized_audio_path: Path | None = None
    transcript_encryption = ARTIFACT_ENCRYPTION_OFF
    audio_path: Path | None = None
    log_path: Path | None = None
    cleanup_state_namespace: str | None = None
    cleanup_v2_prefix_cache: dict[Path, str] = {}
    finalize_error_message = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
    finalize_public_error: _ClassifiedPublicError | None = None
    raw_store_update = store.update

    def _set_finalization_public_error(error: _ClassifiedPublicError) -> None:
        nonlocal finalize_error_message, finalize_public_error
        finalize_public_error = error
        finalize_error_message = str(error)

    def _finalize_store_update(*update_args: object, **update_values: object) -> RecordingState:
        nonlocal finalize_error_message, finalize_public_error
        try:
            return raw_store_update(*update_args, **update_values)
        except BaseException:
            finalize_error_message = "failed to persist error state"
            finalize_public_error = None
            raise

    def current_cleanup_state_namespace() -> str:
        nonlocal cleanup_state_namespace
        if cleanup_state_namespace is None:
            cleanup_state_namespace = (
                _cleanup_backup_state_namespace(store.path)
            )
        return cleanup_state_namespace

    def _persist_cleanup_backup_journal() -> None:
        nonlocal cleanup_backup_journal_persisted
        entries = tuple(
            sorted(
                _cleanup_backup_journal_entry(backup_path, expected_backup_stat)
                for _, backup_path, _, expected_backup_stat in cleanup_rollback_backups
            )
        )
        if not entries:
            return
        _finalize_store_update(
            pending_cleanup_restore_owner_paths=(),
            pending_cleanup_backup_entries=entries,
            cleanup_backup_journal_overflow=False,
            cleanup_backup_journal_restore=False,
        )
        cleanup_backup_journal_persisted = True

    def _clear_cleanup_backup_journal() -> None:
        nonlocal cleanup_backup_journal_persisted
        if not cleanup_backup_journal_persisted:
            return
        _finalize_store_update(
            pending_cleanup_restore_owner_paths=(),
            pending_cleanup_backup_entries=(),
            cleanup_backup_journal_overflow=False,
            cleanup_backup_journal_restore=False,
        )
        cleanup_backup_journal_persisted = False
        cleanup_backup_restore_pairs.clear()

    def _persist_cleanup_backup_restore_pairs(
        pairs: list[tuple[Path, str]],
    ) -> None:
        nonlocal cleanup_backup_journal_persisted
        _finalize_store_update(
            pid=None,
            process_identity="",
            pending_cleanup_restore_owner_paths=tuple(
                str(owner_path) for owner_path, _ in pairs
            ),
            pending_cleanup_backup_entries=tuple(
                entry for _, entry in pairs
            ),
            cleanup_backup_journal_overflow=False,
            cleanup_backup_journal_restore=bool(pairs),
        )
        cleanup_backup_restore_pairs[:] = pairs
        cleanup_backup_journal_persisted = bool(pairs)

    def _complete_cleanup_backup_restore_pair(
        owner_path: Path,
        entry: str,
    ) -> None:
        remaining_pairs = [
            pair
            for pair in cleanup_backup_restore_pairs
            if pair != (owner_path, entry)
        ]
        if len(remaining_pairs) == len(cleanup_backup_restore_pairs):
            raise RuntimeError(
                "cleanup backup restore journal pair is missing"
            )
        _persist_cleanup_backup_restore_pairs(remaining_pairs)

    def _backup_cleanup_file(path_text: str | None, *, suffix: str) -> Path | None:
        nonlocal audio_deleted, log_deleted, preserve_recording_artifacts_after_cleanup_failure
        nonlocal preserve_written_text_on_error
        if not path_text:
            return None
        source = Path(path_text)
        v2_prefix = cleanup_v2_prefix_cache.get(source)
        if v2_prefix is None:
            v2_prefix = _cleanup_backup_v2_prefix(
                source,
                store.path,
                state_namespace=current_cleanup_state_namespace(),
            )
            cleanup_v2_prefix_cache[source] = v2_prefix
        backup = source.with_name(
            f"{v2_prefix}"
            f"{secrets.token_hex(16)}.bak"
        )
        try:
            source_stat = _recording_artifact_stat(source)
            if source_stat is None:
                if _recording_artifact_missing_but_safe(
                    path_text,
                    suffix=suffix,
                    state_path=store.path,
                ):
                    return None
                raise RuntimeError(f"recording cleanup source is not a safe regular file: {source}")
            _copy_recording_artifact_to_backup(source, backup, expected_stat=source_stat)
            backup_stat = _recording_artifact_stat(backup)
            if backup_stat is None:
                raise RuntimeError(f"cleanup backup is not a safe regular file: {backup}")
        except BaseException as exc:
            if isinstance(exc, FileNotFoundError) and _recording_artifact_missing_but_safe(
                path_text,
                suffix=suffix,
                state_path=store.path,
            ):
                return None
            try:
                partial_stat = _recording_artifact_stat(backup)
                if partial_stat is not None:
                    _unlink_regular_leaf_with_parent_fsync(
                        backup,
                        field_name="recording cleanup backup",
                        expected_stat=partial_stat,
                    )
            except BaseException:
                exc.add_note("cleanup backup removal failed")
            preserve_recording_artifacts_after_cleanup_failure = True
            preserve_written_text_on_error = True
            audio_deleted = False
            log_deleted = False
            try:
                _restore_cleanup_backups()
            except BaseException:
                exc.add_note("cleanup backup restore failed")
            raise
        cleanup_rollback_backups.append((source, backup, source_stat, backup_stat))
        cleanup_source_stats[source] = source_stat
        return backup

    def _restore_cleanup_backups() -> None:
        nonlocal cleanup_backup_restore_failed
        try:
            restore_pairs: list[tuple[Path, str]] = []
            for original_path, backup_path, expected_original_stat, expected_backup_stat in cleanup_rollback_backups:
                parent_fd = ensure_directory_without_following_symlinks(
                    original_path.parent,
                    field_name="recording cleanup rollback directory",
                )
                try:
                    try:
                        current_backup_stat = os.stat(
                            backup_path.name,
                            dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        current_backup_stat = None
                    try:
                        current_original_stat = os.stat(
                            original_path.name,
                            dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        current_original_stat = None
                    if current_backup_stat is None:
                        if current_original_stat is None:
                            raise RuntimeError(f"recording cleanup backup is missing: {backup_path}")
                        if not _same_leaf_identity(current_original_stat, expected_original_stat):
                            raise RuntimeError(f"recording cleanup original changed before rollback: {original_path}")
                        continue
                    if not _same_leaf_identity(current_backup_stat, expected_backup_stat):
                        raise RuntimeError(f"recording cleanup backup changed before rollback: {backup_path}")
                    if current_original_stat is None:
                        restore_pairs.append(
                            (
                                original_path,
                                _cleanup_backup_journal_entry(
                                    backup_path,
                                    expected_backup_stat,
                                ),
                            )
                        )
                    else:
                        if not _same_leaf_identity(current_original_stat, expected_original_stat):
                            raise RuntimeError(f"recording cleanup original changed before rollback: {original_path}")
                        if not _unlink_regular_leaf_with_parent_fsync(
                            backup_path,
                            field_name="recording cleanup backup",
                            expected_stat=expected_backup_stat,
                        ):
                            raise RuntimeError(f"recording cleanup backup disappeared during rollback: {backup_path}")
                finally:
                    try:
                        os.close(parent_fd)
                    except BaseException:
                        pass
            if restore_pairs:
                _persist_cleanup_backup_restore_pairs(restore_pairs)
                for owner_path, entry in tuple(restore_pairs):
                    _restore_cleanup_backup_if_needed(
                        owner_path,
                        restore_entry=entry,
                    )
        except BaseException:
            cleanup_backup_restore_failed = True
            raise
        else:
            _clear_cleanup_backup_journal()
            cleanup_rollback_backups.clear()
            cleanup_source_stats.clear()

    def _raise_error_state_update_failure(
        update_error: BaseException,
        final_error_text: str,
        failure_label: str = "error state",
    ) -> None:
        del final_error_text
        stable_message = (
            "failed to persist error cleanup state"
            if failure_label == "error cleanup state"
            else "failed to persist error state"
        )
        try:
            _restore_cleanup_backups()
        except BaseException:
            pass
        _raise_backend_sanitized_exception(update_error, message=stable_message)

    def _discard_cleanup_backups() -> None:
        nonlocal cleanup_backup_restore_failed
        if cleanup_backup_restore_failed:
            raise RuntimeError("recording cleanup backup state is unsafe")
        for _, backup_path, _expected_original_stat, expected_backup_stat in cleanup_rollback_backups:
            try:
                current_backup_stat = backup_path.lstat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                cleanup_backup_restore_failed = True
                raise RuntimeError(f"failed to inspect recording cleanup backup: {backup_path}") from exc
            if not _same_leaf_identity(current_backup_stat, expected_backup_stat):
                cleanup_backup_restore_failed = True
                raise RuntimeError(f"recording cleanup backup changed before discard: {backup_path}")
            try:
                removed = _unlink_regular_leaf_with_parent_fsync(
                    backup_path,
                    field_name="recording cleanup backup",
                    expected_stat=expected_backup_stat,
                )
            except BaseException:
                cleanup_backup_restore_failed = True
                raise
            if not removed:
                cleanup_backup_restore_failed = True
                raise RuntimeError(f"recording cleanup backup disappeared during discard: {backup_path}")
        _clear_cleanup_backup_journal()
        cleanup_rollback_backups.clear()
        cleanup_source_stats.clear()

    def _remove_backed_up_cleanup_file(path_text: str | None, *, suffix: str) -> bool:
        if not path_text:
            return False
        expected_stat = cleanup_source_stats.get(Path(path_text))
        if expected_stat is None:
            return False
        return remove_file(path_text, suffix=suffix, expected_stat=expected_stat)

    def _remove_recording_artifact_if_present(
        path: Path,
        *,
        suffix: str,
        expected_stat: os.stat_result | None,
    ) -> bool:
        if expected_stat is not None and remove_file(str(path), suffix=suffix, expected_stat=expected_stat):
            return True
        return _recording_artifact_missing_but_safe(
            str(path),
            suffix=suffix,
            state_path=store.path,
        )

    def _restore_cleanup_backup_if_needed(
        path: Path | None,
        *,
        restore_entry: str | None = None,
    ) -> None:
        nonlocal cleanup_backup_journal_persisted
        if path is None:
            return
        presence, source_stat = _safe_regular_leaf_probe(path)
        if presence is None:
            raise RuntimeError(f"failed to inspect recording cleanup source: {path}")
        if presence:
            if source_stat is None:
                raise RuntimeError(f"recording cleanup source is not a safe regular file: {path}")
        legacy_prefix = _cleanup_backup_prefix(path)
        v2_prefix = cleanup_v2_prefix_cache.get(path)
        if v2_prefix is None:
            v2_prefix = _cleanup_backup_v2_prefix(
                path,
                store.path,
                state_namespace=current_cleanup_state_namespace(),
            )
            cleanup_v2_prefix_cache[path] = v2_prefix
        restore_entry_name = ""
        restore_identity: tuple[int, int, int, int, int, int, int] | None = None
        if restore_entry is not None:
            restore_entry_name, restore_identity = (
                _parse_cleanup_backup_journal_entry(
                    restore_entry
                )
            )
            if not restore_entry_name.startswith(v2_prefix):
                raise RuntimeError(
                    "cleanup backup restore journal owner does not match entry"
                )
            cleanup_backup_journal_persisted = True
        try:
            entries = _safe_directory_entries(path.parent, field_name="recording cleanup directory")
        except DirectoryScanError as exc:
            raise RuntimeError(f"failed to scan recording cleanup directory: {path.parent}") from exc
        candidates = [
            (candidate, file_stat)
            for candidate, file_stat in entries
            if candidate.name.startswith((legacy_prefix, v2_prefix))
        ]
        backups: list[tuple[Path, os.stat_result]] = []
        for candidate, file_stat in candidates:
            if (
                not candidate.name.startswith(v2_prefix)
                or not _is_cleanup_backup_journal_basename(candidate.name)
                or not stat_module.S_ISREG(file_stat.st_mode)
                or getattr(file_stat, "st_nlink", 1) != 1
            ):
                raise RuntimeError(
                    "recording cleanup backup is not authorized for automatic "
                    f"recovery: {candidate}"
                )
            backups.append((candidate, file_stat))
        if restore_identity is not None:
            matching_backup = next(
                (
                    (candidate, file_stat)
                    for candidate, file_stat in backups
                    if candidate.name == restore_entry_name
                ),
                None,
            )
            if presence:
                if matching_backup is not None or backups:
                    raise RuntimeError(
                        f"recording cleanup restore state is ambiguous: {path}"
                    )
                source_stat = _recording_artifact_stat(path)
                if source_stat is None or (
                    source_stat.st_dev,
                    source_stat.st_ino,
                    source_stat.st_mode,
                    getattr(source_stat, "st_nlink", 1),
                    source_stat.st_size,
                    source_stat.st_mtime_ns,
                ) != restore_identity[:6]:
                    raise RuntimeError(
                        f"recording cleanup restored source changed: {path}"
                    )
                parent_fd = ensure_directory_without_following_symlinks(
                    path.parent,
                    field_name="recording cleanup restore directory",
                )
                try:
                    _fsync_fd(parent_fd)
                finally:
                    try:
                        os.close(parent_fd)
                    except BaseException:
                        pass
                _complete_cleanup_backup_restore_pair(
                    path,
                    restore_entry,
                )
                return
            if matching_backup is None:
                raise RuntimeError(
                    f"recording cleanup restore artifacts are missing: {path}"
                )
            if len(backups) != 1 or not _cleanup_backup_journal_identity_matches(
                matching_backup[1],
                restore_identity,
            ):
                raise RuntimeError(
                    f"recording cleanup backup changed before restore: {matching_backup[0]}"
                )
            backup, expected_backup_stat = matching_backup
        else:
            if not backups:
                return
            if not presence and len(backups) != 1:
                raise RuntimeError(f"recording cleanup backup is ambiguous: {path}")
            if presence:
                for backup, expected_backup_stat in backups:
                    if not _unlink_regular_leaf_with_parent_fsync(
                        backup,
                        field_name="recording cleanup backup",
                        expected_stat=expected_backup_stat,
                    ):
                        raise RuntimeError(f"recording cleanup backup disappeared: {backup}")
                return
            backup, expected_backup_stat = backups[0]
            restore_entry = _cleanup_backup_journal_entry(
                backup,
                expected_backup_stat,
            )
            _persist_cleanup_backup_restore_pairs(
                [(path, restore_entry)]
            )
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording cleanup restore directory",
        )
        try:
            try:
                os.stat(
                    path.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise RuntimeError(
                    f"recording cleanup source appeared during restore: {path}"
                )
            try:
                current_backup_stat = os.stat(
                    backup.name,
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f"recording cleanup backup disappeared: {backup}"
                ) from exc
            if not _same_leaf_identity(
                current_backup_stat,
                expected_backup_stat,
            ):
                raise RuntimeError(
                    f"recording cleanup backup changed before restore: {backup}"
                )
            _rename_without_replacing(
                backup.name,
                path.name,
                directory_fd=parent_fd,
                field_name="recording cleanup restore artifact",
            )
            activated_stat = os.stat(
                path.name,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            if not _same_leaf_claim_identity(
                activated_stat,
                expected_backup_stat,
            ):
                activation_error = RuntimeError(
                    f"recording cleanup backup changed during restore: {backup}"
                )
                try:
                    _rename_without_replacing(
                        path.name,
                        backup.name,
                        directory_fd=parent_fd,
                        field_name="recording cleanup restore rollback",
                    )
                    _fsync_fd(parent_fd)
                except BaseException:
                    activation_error.add_note("recording cleanup restore rollback failed")
                raise activation_error
            _fsync_fd(parent_fd)
        finally:
            try:
                os.close(parent_fd)
            except BaseException:
                pass
        if restore_entry is None:
            raise RuntimeError(
                "cleanup backup restore journal entry is unavailable"
            )
        _complete_cleanup_backup_restore_pair(path, restore_entry)

    deferred_final_error: BaseException | None = None
    try:
        state = store.read()
        _raise_if_state_unreadable(state)
        cleanup_backup_restore_pending = bool(
            state.cleanup_backup_journal_restore
        )
        restore_pairs = _cleanup_backup_restore_journal_pairs(
            state,
            state_path=store.path,
            state_namespace=(
                current_cleanup_state_namespace()
                if state.pending_cleanup_restore_owner_paths
                else None
            ),
            v2_prefix_cache=cleanup_v2_prefix_cache,
        )
        cleanup_backup_restore_pairs[:] = restore_pairs
        if (
            state.pending_cleanup_owner_paths
            or (
                state.pending_cleanup_restore_owner_paths
                and not cleanup_backup_restore_pending
            )
            or state.cleanup_backup_journal_overflow
            or (
                state.pending_cleanup_backup_entries
                and not cleanup_backup_restore_pending
            )
        ):
            return {
                "status": state.status,
                "message": (
                    "previous recording cleanup is unresolved; "
                    "run cancel before finalizing"
                ),
            }
        if (
            state.status == "finalizing"
            and type(state.error) is str
            and state.error == LEGACY_TRANSIENT_TRANSCRIPT_INSERT_ERROR
        ):
            return {
                "status": "error",
                "message": TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR,
                "error": TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR,
                "transcript_path_present": bool(state.transcript_path),
                "inserted": bool(state.inserted),
            }
        retry_stored_transcript = (
            state.status == "finalizing"
            and _is_retryable_transcript_insert_error(state.error)
            and not state.inserted
            and bool(state.transcript_path)
        )
        if retry_stored_transcript:
            state_marked_finalizing = True
            artifact_encryption = _artifact_encryption_mode(args)
            keep_recording_artifacts = _coerce_bool(
                getattr(args, "keep_recording_artifacts", False),
                field_name="keep_recording_artifacts",
            )
            retry_path = _normalized_state_artifact_path(
                state.transcript_path,
                state_path=store.path,
            )
            try:
                transcript_root = transcript_dir().resolve(strict=False)
                retry_path_is_safe = (
                    retry_path is not None
                    and _is_transcript_artifact(retry_path)
                    and retry_path.is_relative_to(transcript_root)
                )
            except (OSError, RuntimeError, ValueError):
                retry_path_is_safe = False
            if not retry_path_is_safe or retry_path is None:
                raise RuntimeError("stored transcript retry artifact is invalid")
            retry_presence, retry_stat = _safe_regular_leaf_probe(retry_path)
            if retry_presence is not True or retry_stat is None:
                raise RuntimeError("stored transcript retry artifact is unavailable")
            written_text_path = retry_path
            written_text_stat = retry_stat
            stored_transcript_text = _read_stored_transcript_text(
                retry_path,
                expected_stat=retry_stat,
            ).strip()
            if not stored_transcript_text:
                raise RuntimeError("stored transcript retry artifact is empty")
            transcript_encryption = _stored_transcript_encryption_mode(
                retry_path,
                expected_stat=retry_stat,
            )
            chosen_language = state.language or args.language or "en"
            language = _validate_pipeline_text_args(
                args,
                language=chosen_language,
            )
            append_space = _coerce_bool(
                args.append_space,
                field_name="append_space",
            )
            sanitize_special_chars = _coerce_bool(
                args.sanitize_special_chars,
                field_name="sanitize_special_chars",
            )
            text_to_insert = prepare_output_text(
                stored_transcript_text,
                append_space,
                sanitize_special_chars,
            )
            typing_delay_ms = _coerce_int(
                args.typing_delay_ms,
                field_name="typing-delay-ms",
                max_value=MAX_TYPING_DELAY_MS,
            )
            if text_to_insert:
                _finalize_store_update(inserted=True, error="")
                inserted = True
                try:
                    insert_result = insert_text(
                        text_to_insert,
                        args.insert_method,
                        typing_delay_ms,
                    )
                    if not isinstance(insert_result, bool):
                        raise RuntimeError(
                            "insert_text returned a non-boolean result"
                        )
                    inserted = insert_result
                except OutputNotInsertedError as exc:
                    preserve_written_text_on_error = True
                    _set_finalization_public_error(
                        _output_public_failure(no_side_effect=True)
                    )
                    _finalize_store_update(
                        inserted=False,
                        error=finalize_error_message,
                    )
                    inserted = False
                    safe_no_output_failure = True
                    _raise_backend_sanitized_exception(
                        exc,
                        message=finalize_error_message,
                    )
                except BaseException as exc:
                    if isinstance(exc, KeyboardInterrupt):
                        insert_backend_interrupted = True
                    if isinstance(exc, (Exception, SystemExit)):
                        _set_finalization_public_error(
                            _output_public_failure(no_side_effect=False)
                        )
                    _raise_foreign_backend_sanitized_exception(
                        exc,
                        message=finalize_error_message,
                    )
                if not inserted:
                    _finalize_store_update(inserted=False)
            preserve_written_text_on_error = True
            done = _finalize_store_update(
                status="done",
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
                transcript=(
                    stored_transcript_text
                    if transcript_encryption == ARTIFACT_ENCRYPTION_OFF
                    else ""
                ),
                transcript_path=str(retry_path),
                inserted=inserted,
                error="",
            )
            return {
                "status": done.status,
                "message": "transcription completed",
                "transcript": _transcript_payload_text(
                    stored_transcript_text,
                    transcript_encryption,
                    args,
                ),
                "transcript_output_redacted": bool(stored_transcript_text)
                and not _confirm_plaintext_transcript_output(args),
                "transcript_path_present": True,
                "artifact_encryption": artifact_encryption,
                "transcript_encryption": transcript_encryption,
                "transcript_encrypted": (
                    transcript_encryption != ARTIFACT_ENCRYPTION_OFF
                ),
                "inserted": inserted,
                "language": language,
                "recording_artifacts_kept": keep_recording_artifacts,
                "audio_deleted": not bool(done.audio_path),
                "log_deleted": not bool(done.log_path),
            }
        persisted_audio_path = state.audio_path
        persisted_log_path = state.log_path
        if state.status in {"done", "idle"} or (
            state.status == "error"
            and not cleanup_backup_restore_pending
        ):
            return {"status": state.status, "message": state.error or f"recording already {state.status}"}
        if state.status in {"recorded", "processing", "finalizing"} and (state.pid is not None or state.process_identity):
            finalize_error_message = TRANSIENT_RECORDING_PROCESS_ERROR
            if state.pid is None:
                error_text = (
                    f"{TRANSIENT_RECORDING_PROCESS_ERROR}: "
                    "recording process identity is incomplete; recording state preserved"
                )
                _finalize_store_update(status=state.status, error=error_text)
                raise RuntimeError(error_text)
            process_error = _reconcile_recording_process(state)
            if process_error is not None:
                error_text = f"{TRANSIENT_RECORDING_PROCESS_ERROR}: {process_error}"
                if process_error != _RECORDING_PROCESS_GROUP_ACTIVE_ERROR:
                    _finalize_store_update(status=state.status, error=error_text)
                else:
                    finalize_error_message = process_error
                raise RuntimeError(process_error)
            state = _finalize_store_update(
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
            )
            finalize_error_message = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
        if state.status == "finalizing":
            inserted = bool(state.inserted)

        if not state.audio_path:
            finalize_error_message = TRANSIENT_AUDIO_PATH_ERROR
            _finalize_store_update(
                status="error",
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
                error=finalize_error_message,
            )
            raise RuntimeError("no recording is available")
        audio_path = _normalized_state_recording_artifact_path(
            state.audio_path,
            suffix=(".wav", ".flac", ".socenc"),
            state_path=store.path,
            require_recordings_dir=True,
        )
        log_path = _normalized_state_recording_artifact_path(
            state.log_path,
            suffix=".log",
            state_path=store.path,
            require_recordings_dir=True,
        )
        if cleanup_backup_restore_pending:
            for owner_path, entry in tuple(restore_pairs):
                _restore_cleanup_backup_if_needed(
                    owner_path,
                    restore_entry=entry,
                )
        elif state.status == "finalizing":
            _restore_cleanup_backup_if_needed(audio_path)
            _restore_cleanup_backup_if_needed(log_path)
        if not audio_path:
            finalize_error_message = TRANSIENT_AUDIO_PATH_ERROR
            _finalize_store_update(
                status="error",
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
                error=finalize_error_message,
            )
            raise RuntimeError("recording audio path is invalid")
        audio_presence, original_audio_stat = _safe_regular_leaf_probe(audio_path)
        if audio_presence is None:
            finalize_error_message = TRANSIENT_AUDIO_PATH_ERROR
            raise RuntimeError(f"failed to inspect recording audio path: {audio_path}")
        if audio_presence is True and original_audio_stat is None:
            finalize_error_message = TRANSIENT_AUDIO_PATH_ERROR
            _finalize_store_update(
                status="error",
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
                error=finalize_error_message,
            )
            raise RuntimeError("recording audio path is not a safe regular file")

        if state.status == "finalizing" and original_audio_stat is None and audio_path.suffix.lower() == ".wav":
            recovered_audio_path = audio_path.with_suffix(".flac")
            recovered_audio_stat = _recording_artifact_stat(recovered_audio_path)
            if recovered_audio_stat is not None:
                audio_path = recovered_audio_path
                original_audio_stat = recovered_audio_stat
                persisted_audio_path = str(recovered_audio_path)
                state = _finalize_store_update(audio_path=str(recovered_audio_path))

        chosen_language = state.language or args.language or "en"
        language = _validate_pipeline_text_args(args, language=chosen_language)
        normalized_transcriber = normalize_backend(args.transcriber)
        keep_recording_artifacts = _coerce_bool(
            getattr(args, "keep_recording_artifacts", False),
            field_name="keep_recording_artifacts",
        )
        if _coerce_bool(getattr(args, "settings_json_stdin", False), field_name="settings_json_stdin"):
            preserve_recording_artifacts_after_cleanup_failure = True
            automatic_backup_settings = _settings_json_from_args(args)
            automatic_audio_backup_requested = _auto_backup_configuration(automatic_backup_settings) is not None
        _coerce_bool(getattr(args, "skip_silent_auto_relisten", False), field_name="skip_silent_auto_relisten")
        artifact_encryption = _artifact_encryption_mode(args)
        if state.status != "finalizing":
            state = _finalize_store_update(
                status="finalizing",
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
                error="",
                inserted=(
                    state.inserted
                    if cleanup_backup_restore_pending
                    else False
                ),
            )
            state_marked_finalizing = True
            inserted = (
                state.inserted
                if cleanup_backup_restore_pending
                else False
            )
        else:
            if state.pid is not None or state.process_identity:
                state = _finalize_store_update(pid=None, process_identity="")
            inserted = bool(state.inserted)
            state_marked_finalizing = True
        if state.status == "finalizing":
            encrypted_recovery_path: Path | None = None
            if is_encrypted_path(audio_path):
                encrypted_recovery_path = audio_path
            elif original_audio_stat is None and audio_path.suffix.lower() == ".wav":
                recovery_audio_path = audio_path.with_suffix(".flac")
                if _recording_artifact_stat(recovery_audio_path) is None:
                    candidate_path = encrypted_path_for(recovery_audio_path)
                    if _recording_artifact_stat(candidate_path) is not None:
                        encrypted_recovery_path = candidate_path
            if encrypted_recovery_path is not None:
                persisted_audio_path = str(encrypted_recovery_path)
                preserved_encrypted_audio_path = encrypted_recovery_path
                plaintext_recovery_path = _plaintext_recording_sibling_for_encrypted_path(
                    encrypted_recovery_path
                )
                if plaintext_recovery_path is None:
                    raise RuntimeError(f"encrypted recording artifact has no safe plaintext sibling: {encrypted_recovery_path}")
                plaintext_presence = _safe_leaf_presence(plaintext_recovery_path)
                if plaintext_presence is None:
                    raise RuntimeError(f"failed to inspect plaintext recording recovery path: {plaintext_recovery_path}")
                if plaintext_presence:
                    recovered_plaintext_stat = _recording_artifact_stat(plaintext_recovery_path)
                    if recovered_plaintext_stat is None:
                        raise RuntimeError(f"plaintext recording recovery path is not a safe regular file: {plaintext_recovery_path}")
                else:
                    encrypted_source_stat = _recording_artifact_stat(encrypted_recovery_path)
                    if encrypted_source_stat is None:
                        raise RuntimeError(f"encrypted recording artifact is not a safe regular file: {encrypted_recovery_path}")
                    try:
                        recovered_payload = read_decrypted_bytes_from_file(
                            encrypted_recovery_path,
                            kind="recording",
                            field_name="recording audio recovery artifact",
                        )
                        if len(recovered_payload) > MAX_AUDIO_FILE_BYTES:
                            raise RuntimeError(
                                f"recovered recording artifact is too large: {len(recovered_payload)} bytes"
                            )
                        write_encrypted_bytes_atomically(
                            plaintext_recovery_path,
                            recovered_payload,
                            ARTIFACT_ENCRYPTION_OFF,
                            kind="recording",
                            field_name="recording audio recovery artifact",
                        )
                    except ArtifactCryptoError:
                        raise RuntimeError("recording recovery failed") from None
                    recovered_plaintext_stat = _recording_artifact_stat(plaintext_recovery_path)
                    if recovered_plaintext_stat is None:
                        raise RuntimeError(f"recovered plaintext recording artifact is not a safe regular file: {plaintext_recovery_path}")
                    current_encrypted_stat = _recording_artifact_stat(encrypted_recovery_path)
                    if current_encrypted_stat is None or not _same_leaf_identity(
                        current_encrypted_stat,
                        encrypted_source_stat,
                    ):
                        _remove_recording_artifact_if_present(
                            plaintext_recovery_path,
                            suffix=plaintext_recovery_path.suffix.lower(),
                            expected_stat=recovered_plaintext_stat,
                        )
                        raise RuntimeError(f"encrypted recording artifact changed during recovery: {encrypted_recovery_path}")
                audio_path = plaintext_recovery_path
                original_audio_stat = recovered_plaintext_stat
                persisted_audio_path = str(encrypted_recovery_path)
                if str(state.audio_path or "") != str(encrypted_recovery_path):
                    state = _finalize_store_update(audio_path=str(encrypted_recovery_path))
        audio_deleted = False
        log_deleted = False
        done_audio_path = str(audio_path)
        done_log_path = str(log_path) if log_path else None
        trimmed_audio_path: Path | None = None
        stabilized_audio_path: Path | None = None
        remove_original_after_state_update = False
        audio_suffix = ""
        finalize_error_message = TRANSIENT_AUDIO_FILE_ERROR
        audio_path = validate_audio_file(audio_path)
        audio_suffix = audio_path.suffix.lower()
        recording_level: RecordingLevel | None = None
        try:
            recording_level = read_recording_level(audio_path)
        except RecorderError:
            # Level is diagnostic only; silence detection remains authoritative.
            pass
        finalize_error_message = TRANSIENT_SILENCE_DETECTION_ERROR
        silence = detect_silent_recording(audio_path)
        finalize_error_message = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
        skip_speechless_recording = silence.silent and silence.speech_seconds <= 0.0
        if skip_speechless_recording:
            cleanup_log_path = str(log_path) if log_path else None
            recording_encryption = ARTIFACT_ENCRYPTION_OFF
            if not keep_recording_artifacts:
                done_audio_path = ""
                done_log_path = ""
            elif done_audio_path:
                plaintext_done_audio_path = Path(done_audio_path)
                encrypted_audio_path, recording_encryption = _encrypt_kept_recording_artifact(plaintext_done_audio_path, args)
                if recording_encryption != ARTIFACT_ENCRYPTION_OFF and is_encrypted_path(encrypted_audio_path):
                    preserved_encrypted_audio_path = encrypted_audio_path
                if encrypted_audio_path != plaintext_done_audio_path:
                    done_audio_path = str(encrypted_audio_path)
                if artifact_encryption != ARTIFACT_ENCRYPTION_OFF:
                    done_log_path = ""
            state.audio_path = done_audio_path
            state.log_path = done_log_path
            silent_transcript_state_cleared = True
            state.transcript_path = ""
            state.transcript = ""
            artifact_cleanup = _enforce_recording_artifact_cap(state, state_path=store.path)
            cleanup_failures: list[tuple[str, str, str]] = []
            if automatic_audio_backup_requested:
                preserve_written_text_on_error = True
                finalize_error_message = "automatic audio backup failed"
                automatic_backup_result = _run_inline_auto_backup(args, automatic_backup_settings)
            if not keep_recording_artifacts:
                audio_backup = _backup_cleanup_file(str(audio_path), suffix=audio_suffix)
                log_backup = _backup_cleanup_file(cleanup_log_path, suffix=".log")
                encrypted_audio_backup = (
                    _backup_cleanup_file(
                        str(preserved_encrypted_audio_path),
                        suffix=".socenc",
                    )
                    if preserved_encrypted_audio_path is not None
                    else None
                )
                _persist_cleanup_backup_journal()
                audio_deleted = audio_backup is None or _remove_backed_up_cleanup_file(
                    str(audio_path), suffix=audio_suffix
                )
                log_deleted = (
                    log_backup is None or _remove_backed_up_cleanup_file(cleanup_log_path, suffix=".log")
                    if cleanup_log_path
                    else False
                )
                if preserved_encrypted_audio_path is not None:
                    encrypted_audio_deleted = encrypted_audio_backup is None or _remove_backed_up_cleanup_file(
                        str(preserved_encrypted_audio_path),
                        suffix=".socenc",
                    )
                    if not encrypted_audio_deleted:
                        cleanup_failures.append(
                            (
                                "audio_path",
                                str(preserved_encrypted_audio_path),
                                "encrypted recording artifact",
                            )
                        )
                if not audio_deleted:
                    cleanup_failures.append(("audio_path", str(audio_path), "recording audio artifact"))
                if cleanup_log_path and not log_deleted:
                    cleanup_failures.append(("log_path", cleanup_log_path, "recorder log artifact"))
            elif artifact_encryption != ARTIFACT_ENCRYPTION_OFF and cleanup_log_path:
                log_backup = _backup_cleanup_file(cleanup_log_path, suffix=".log")
                _persist_cleanup_backup_journal()
                log_deleted = log_backup is None or _remove_backed_up_cleanup_file(cleanup_log_path, suffix=".log")
                if not log_deleted:
                    cleanup_failures.append(("log_path", cleanup_log_path, "recorder log artifact"))
            if cleanup_failures:
                audio_deleted = False
                log_deleted = False
                preserve_recording_artifacts_after_cleanup_failure = True
                _restore_cleanup_backups()
            _raise_recording_cleanup_failure(store, cleanup_failures, inserted=inserted)
            cleanup_failed_paths = _cleanup_failed_paths(artifact_cleanup)
            message = "silent recording skipped"
            if cleanup_failed_paths:
                _persist_cleanup_failure_state(
                    store,
                    cleanup_failed_paths,
                    artifact_state=state,
                    clear_transcript=silent_transcript_state_cleared,
                )
                message = f"{message}; {_cleanup_failure_error(cleanup_failed_paths)}"
                return {
                    "status": "error",
                    "message": message,
                    "error": message,
                    "cleanup_failed_path_count": len(cleanup_failed_paths),
                    "transcript": "",
                    "transcript_path": "",
                    "artifact_encryption": artifact_encryption,
                    "transcript_encryption": ARTIFACT_ENCRYPTION_OFF,
                    "transcript_encrypted": False,
                    "recording_encryption": recording_encryption,
                    "recording_encrypted": recording_encryption != ARTIFACT_ENCRYPTION_OFF,
                    "inserted": False,
                    "recording_artifact_cap": _public_cleanup_result(artifact_cleanup),
                    "language": language,
                    "recording_artifacts_kept": keep_recording_artifacts,
                    "audio_deleted": audio_deleted,
                    "log_deleted": log_deleted,
                    "silence_detected": True,
                    "silence_duration_seconds": silence.silence_seconds,
                    "speech_duration_seconds": silence.speech_seconds,
                    **({"automatic_backup": automatic_backup_result} if automatic_backup_result is not None else {}),
                }
            done = _finalize_store_update(
                status="done",
                stopped_at=state.stopped_at or now_iso(),
                audio_path=done_audio_path,
                log_path=done_log_path,
                transcript="",
                transcript_path="",
                inserted=False,
                error="",
            )
            _discard_cleanup_backups()
            return {
                "status": done.status,
                "message": "silent recording skipped",
                "transcript": "",
                "transcript_path": "",
                "artifact_encryption": artifact_encryption,
                "transcript_encryption": ARTIFACT_ENCRYPTION_OFF,
                "transcript_encrypted": False,
                "recording_encryption": recording_encryption,
                "recording_encrypted": recording_encryption != ARTIFACT_ENCRYPTION_OFF,
                "inserted": False,
                "recording_artifact_cap": _public_cleanup_result(artifact_cleanup),
                "language": language,
                "recording_artifacts_kept": keep_recording_artifacts,
                "audio_deleted": audio_deleted,
                "log_deleted": log_deleted,
                "silence_detected": True,
                "silence_duration_seconds": silence.silence_seconds,
                "speech_duration_seconds": silence.speech_seconds,
                **({"automatic_backup": automatic_backup_result} if automatic_backup_result is not None else {}),
            }

        text_path = _transcript_path_for_audio(audio_path)
        transcriber_text_path = _transcript_work_path(text_path, artifact_encryption)
        finalize_error_message = TRANSIENT_TRANSCRIPT_WRITE_ERROR
        try:
            transient_text_fd, transient_owner_stat = _prepare_transient_transcript_path(
                transcriber_text_path,
                text_path,
            )
        except BaseException as exc:
            _raise_backend_sanitized_exception(
                exc,
                message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
            )
        transcript_audio_path = audio_path
        try:
            trimmed_audio_path = trim_recording_silence(audio_path)
            transcript_audio_path = trimmed_audio_path
            if trimmed_audio_path is not None and trimmed_audio_path != audio_path:
                trimmed_audio_stat = _recording_artifact_stat(trimmed_audio_path)
        except RecorderError:
            transcript_audio_path = audio_path
        transcription_error: BaseException | None = None
        cleanup_error: BaseException | None = None
        finalize_error_message = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
        try:
            text = transcribe(
                audio_path=transcript_audio_path,
                language=language,
                text_path=transcriber_text_path,
                command_template=args.transcriber_command,
                backend=normalized_transcriber,
                whisper_model=args.whisper_model,
                personal_context=args.personal_context,
                vocabulary=args.vocabulary,
                **_openai_compatible_transcribe_kwargs(args, normalized_transcriber),
            )
        except BaseException as exc:
            transcription_error = exc
            text = ""
        try:
            _remove_transient_transcript_path(
                transcriber_text_path,
                text_path,
                expected_fd=transient_text_fd,
                expected_stat=_trusted_transcript_stat_for_cleanup(
                    text,
                    transcriber_text_path,
                ),
                expected_owner_stat=transient_owner_stat,
            )
            if trimmed_audio_path is not None and trimmed_audio_path != audio_path and not keep_recording_artifacts:
                if not _remove_recording_artifact_if_present(
                    trimmed_audio_path,
                    suffix=trimmed_audio_path.suffix.lower(),
                    expected_stat=trimmed_audio_stat,
                ):
                    raise RuntimeError(f"failed to delete transient trimmed recording artifact: {trimmed_audio_path}")
        except BaseException as cleanup_exc:
            cleanup_error = cleanup_exc
        if cleanup_error is not None:
            finalize_error_message = TRANSIENT_TRANSCRIPT_CLEANUP_ERROR
            raise _transcription_cleanup_exception(transcription_error, cleanup_error, stable_public_error=True) from None
        if transcription_error is not None:
            if isinstance(transcription_error, TranscriptionCleanupError):
                finalize_error_message = TRANSIENT_TRANSCRIPT_CLEANUP_ERROR
            elif isinstance(transcription_error, Exception):
                classified_failure = _classified_transcription_failure(
                    transcription_error,
                    recording_level=recording_level,
                    recorder=getattr(state, "recorder", ""),
                    input_device=getattr(state, "input_device", ""),
                )
                if (
                    classified_failure.failure_code == "SOC-T005"
                    and _recording_level_lacks_clear_speech_signal(recording_level)
                ):
                    _clear_transient_exception_metadata(transcription_error)
                    transcription_error = None
                else:
                    _set_finalization_public_error(classified_failure)
            if transcription_error is not None:
                _raise_backend_sanitized_exception(
                    transcription_error,
                    message=finalize_error_message,
                )

        try:
            if _is_empty_transcript_text(text):
                text = ""
                security_post_processing = _empty_security_post_processing()
            else:
                try:
                    text, security_post_processing = _process_transcript(
                        text,
                        args,
                        language,
                    )
                except Exception as error:
                    retryable_pre_output_processing_failure = not inserted
                    _set_finalization_public_error(_postprocess_public_failure(error))
                    raise
        except BaseException as exc:
            _raise_backend_sanitized_exception(
                exc,
                message=finalize_error_message,
            )
        stripped_text = text.strip()
        if not stripped_text:
            text = ""
        stored_text_path: Path | None = None
        transcript_encryption = ARTIFACT_ENCRYPTION_OFF
        if stripped_text:
            finalize_error_message = TRANSIENT_TRANSCRIPT_WRITE_ERROR
            try:
                stored_text_path, transcript_encryption = _write_stored_transcript(
                    text_path,
                    stripped_text + "\n",
                    args,
                )
            except BaseException as exc:
                _raise_backend_sanitized_exception(
                    exc,
                    message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
                )
            written_text_path = stored_text_path
            written_text_stat = _recording_artifact_stat(stored_text_path)
            finalize_error_message = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
        stored_transcript_text = text
        append_space = _coerce_bool(args.append_space, field_name="append_space")
        sanitize_special_chars = _coerce_bool(
            args.sanitize_special_chars,
            field_name="sanitize_special_chars",
        )
        text_to_insert = ""
        if stripped_text:
            text_to_insert = prepare_output_text(text, append_space, sanitize_special_chars)
        typing_delay_ms = _coerce_int(args.typing_delay_ms, field_name="typing-delay-ms", max_value=MAX_TYPING_DELAY_MS)
        if text_to_insert and not inserted:
            # Paste has no transaction boundary. Persist intent first so a crash after
            # the external side effect cannot cause a duplicate paste on resume.
            _finalize_store_update(inserted=True)
            inserted = True
            try:
                insert_result = insert_text(text_to_insert, args.insert_method, typing_delay_ms)
                if not isinstance(insert_result, bool):
                    raise RuntimeError("insert_text returned a non-boolean result")
                inserted = insert_result
            except OutputNotInsertedError as exc:
                preserve_written_text_on_error = True
                _set_finalization_public_error(
                    _output_public_failure(no_side_effect=True)
                )
                _finalize_store_update(
                    inserted=False,
                    error=finalize_error_message,
                )
                inserted = False
                safe_no_output_failure = True
                _raise_backend_sanitized_exception(
                    exc,
                    message=finalize_error_message,
                )
            except BaseException as exc:
                if isinstance(exc, KeyboardInterrupt):
                    insert_backend_interrupted = True
                if isinstance(exc, (Exception, SystemExit)):
                    _set_finalization_public_error(
                        _output_public_failure(no_side_effect=False)
                    )
                _raise_foreign_backend_sanitized_exception(
                    exc,
                    message=finalize_error_message,
                )
            if not inserted:
                _finalize_store_update(inserted=False)
        if inserted:
            preserve_written_text_on_error = True

        cleanup_audio_path: Path | None = None
        cleanup_log_path: str | None = None
        cleanup_encrypted_audio_path: Path | None = None
        if not keep_recording_artifacts:
            cleanup_audio_path = audio_path
            cleanup_log_path = str(log_path) if log_path else None
            if preserved_encrypted_audio_path is not None:
                cleanup_encrypted_audio_path = preserved_encrypted_audio_path
            done_audio_path = ""
            done_log_path = ""
        elif trimmed_audio_path is not None:
            stabilized_audio_path = _stabilize_recording_artifact_path(
                trimmed_audio_path,
                replace_existing_path=audio_path,
            )
            stabilized_audio_stat = _recording_artifact_stat(stabilized_audio_path)
            done_audio_path = str(stabilized_audio_path)
            if done_audio_path != str(audio_path):
                remove_original_after_state_update = True
        else:
            if audio_path.suffix.lower() == ".wav":
                try:
                    converted_audio_path = reencode_recording_to_flac(audio_path)
                except RecorderError:
                    done_audio_path = str(audio_path)
                else:
                    stabilized_audio_path = _stabilize_recording_artifact_path(
                        converted_audio_path,
                        replace_existing_path=audio_path,
                    )
                    stabilized_audio_stat = _recording_artifact_stat(stabilized_audio_path)
                    done_audio_path = str(stabilized_audio_path)
                    if done_audio_path != str(audio_path):
                        remove_original_after_state_update = True
            else:
                done_audio_path = str(audio_path)
        if cleanup_log_path is None and not keep_recording_artifacts and log_path is not None:
            cleanup_log_path = str(log_path)

        recording_encryption = ARTIFACT_ENCRYPTION_OFF
        plaintext_done_audio_path: Path | None = None
        if keep_recording_artifacts and done_audio_path:
            plaintext_done_audio_path = Path(done_audio_path)
            encrypted_audio_path, recording_encryption = _encrypt_kept_recording_artifact(plaintext_done_audio_path, args)
            if recording_encryption != ARTIFACT_ENCRYPTION_OFF and is_encrypted_path(encrypted_audio_path):
                preserved_encrypted_audio_path = encrypted_audio_path
            if encrypted_audio_path != plaintext_done_audio_path:
                done_audio_path = str(encrypted_audio_path)
                stabilized_audio_path = encrypted_audio_path
                stabilized_audio_stat = _recording_artifact_stat(stabilized_audio_path)
                if plaintext_done_audio_path == audio_path:
                    remove_original_after_state_update = False
            if artifact_encryption != ARTIFACT_ENCRYPTION_OFF and done_log_path:
                cleanup_log_path = done_log_path
                done_log_path = ""

        if automatic_audio_backup_requested:
            preserve_written_text_on_error = True
            finalize_error_message = "automatic audio backup failed"
            automatic_backup_result = _run_inline_auto_backup(args, automatic_backup_settings)

        cleanup_failures: list[tuple[str, str, str]] = []
        audio_backup = (
            _backup_cleanup_file(str(cleanup_audio_path), suffix=audio_suffix)
            if cleanup_audio_path is not None
            else None
        )
        encrypted_audio_backup = (
            _backup_cleanup_file(
                str(cleanup_encrypted_audio_path),
                suffix=".socenc",
            )
            if cleanup_encrypted_audio_path is not None
            else None
        )
        log_backup = (
            _backup_cleanup_file(cleanup_log_path, suffix=".log")
            if cleanup_log_path
            else None
        )
        _persist_cleanup_backup_journal()
        if cleanup_audio_path is not None:
            audio_deleted = audio_backup is None or _remove_backed_up_cleanup_file(
                str(cleanup_audio_path), suffix=audio_suffix
            )
            if not audio_deleted:
                cleanup_failures.append(("audio_path", str(cleanup_audio_path), "recording audio artifact"))
        if cleanup_encrypted_audio_path is not None:
            encrypted_audio_deleted = encrypted_audio_backup is None or _remove_backed_up_cleanup_file(
                str(cleanup_encrypted_audio_path),
                suffix=".socenc",
            )
            if not encrypted_audio_deleted:
                cleanup_failures.append(
                    ("audio_path", str(cleanup_encrypted_audio_path), "encrypted recording artifact")
                )
        if cleanup_log_path:
            log_deleted = log_backup is None or _remove_backed_up_cleanup_file(cleanup_log_path, suffix=".log")
            if not log_deleted:
                cleanup_failures.append(("log_path", cleanup_log_path, "recorder log artifact"))
        if cleanup_failures:
            audio_deleted = False
            log_deleted = False
            preserve_recording_artifacts_after_cleanup_failure = True
            preserve_written_text_on_error = True
            _restore_cleanup_backups()
            _raise_recording_cleanup_failure(store, cleanup_failures, inserted=inserted)

        done_candidate = RecordingState(
            status="done",
            stopped_at=state.stopped_at or now_iso(),
            audio_path=done_audio_path,
            log_path=done_log_path,
            transcript=text if transcript_encryption == ARTIFACT_ENCRYPTION_OFF else "",
            transcript_path=str(stored_text_path) if stored_text_path is not None else "",
            inserted=inserted,
            error="",
        )
        artifact_cleanup_active_paths: set[Path] = set()
        if stabilized_audio_path is not None:
            artifact_cleanup_active_paths.add(stabilized_audio_path)
        if trimmed_audio_path is not None:
            artifact_cleanup_active_paths.add(trimmed_audio_path)
        if audio_path is not None:
            artifact_cleanup_active_paths.add(audio_path)
        artifact_cleanup = _enforce_recording_artifact_cap(state, artifact_cleanup_active_paths, state_path=store.path)
        keep_transcripts = _coerce_int(
            getattr(args, "keep_transcripts", DEFAULT_KEEP_TRANSCRIPTS),
            field_name="keep-transcripts",
            max_value=MAX_KEEP_TRANSCRIPTS,
        )
        transcript_stats: dict[Path, os.stat_result] = {}
        transcript_files = _safe_transcript_artifact_files(expected_stats=transcript_stats)
        transcript_cleanup = prune_transcript_files_by_mtime(
            transcript_files,
            keep_transcripts,
            active_artifact_paths(done_candidate, state_path=store.path),
            False,
            expected_stats=transcript_stats,
        )
        transient_transcript_cleanup = prune_stale_transient_transcripts(False)
        cleanup_failed_paths = _cleanup_failed_paths(
            artifact_cleanup,
            transcript_cleanup,
            transient_transcript_cleanup,
        )
        message = "recording finished without transcript" if not stripped_text else "transcription completed"
        if cleanup_failed_paths:
            _persist_cleanup_failure_state(store, cleanup_failed_paths, artifact_state=done_candidate)
            status = "error"
            message = f"{message}; {_cleanup_failure_error(cleanup_failed_paths)}"
            done = done_candidate
        else:
            done = _finalize_store_update(
                status="done",
                pid=None,
                process_identity="",
                stopped_at=done_candidate.stopped_at,
                audio_path=done_candidate.audio_path,
                log_path=done_candidate.log_path,
                transcript=done_candidate.transcript,
                transcript_path=done_candidate.transcript_path,
                inserted=done_candidate.inserted,
                error=done_candidate.error,
            )
            preserve_written_text_on_error = True
            post_done_cleanup_failures: list[tuple[str, str, str]] = []
            if remove_original_after_state_update and original_audio_stat is not None:
                if not remove_file(str(audio_path), suffix=audio_suffix, expected_stat=original_audio_stat):
                    preserve_recording_artifacts_after_cleanup_failure = True
                    preserved_audio_path = stabilized_audio_path or audio_path
                    post_done_cleanup_failures.append(
                        ("audio_path", str(preserved_audio_path), "original recording artifact")
                    )
            _raise_recording_cleanup_failure(store, post_done_cleanup_failures, inserted=inserted)
            state = done
            status = done.status
            _discard_cleanup_backups()
        return {
            "status": status,
            "message": message,
            **({"error": message, "cleanup_failed_path_count": len(cleanup_failed_paths)} if cleanup_failed_paths else {}),
            "transcript": _transcript_payload_text(text, transcript_encryption, args),
            "transcript_output_redacted": bool(text) and not _confirm_plaintext_transcript_output(args),
            "transcript_path_present": stored_text_path is not None,
            "artifact_encryption": artifact_encryption,
            "transcript_encryption": transcript_encryption,
            "transcript_encrypted": transcript_encryption != ARTIFACT_ENCRYPTION_OFF,
            "recording_encryption": recording_encryption,
            "recording_encrypted": recording_encryption != ARTIFACT_ENCRYPTION_OFF,
            "inserted": inserted,
            "security": _public_security_post_processing(security_post_processing),
            "recording_artifact_cap": _public_cleanup_result(artifact_cleanup),
            "transcript_file_cap": _public_cleanup_result(transcript_cleanup),
            "transient_transcript_cleanup": _public_cleanup_result(transient_transcript_cleanup),
            "language": language,
            "recording_artifacts_kept": keep_recording_artifacts,
            "audio_deleted": audio_deleted,
            "log_deleted": log_deleted,
            **({"automatic_backup": automatic_backup_result} if automatic_backup_result is not None else {}),
        }
    except BaseException as exc:
        error_text = finalize_error_message
        # Refresh state once more on error so the most recent status is persisted.
        if state_marked_finalizing:
            try:
                refreshed_state = store.read()
            except BaseException:
                exc.add_note("finalization state refresh failed")
            else:
                if isinstance(refreshed_state, RecordingState):
                    state = refreshed_state
                else:
                    exc.add_note("finalization state refresh returned invalid state")
            state_audio_anchor = _normalized_state_recording_artifact_path(
                state.audio_path,
                suffix=(".wav", ".flac", ".socenc"),
                state_path=store.path,
                require_recordings_dir=True,
            )
            stabilized_is_state_anchor = (
                state_audio_anchor is not None
                and stabilized_audio_path is not None
                and state_audio_anchor == stabilized_audio_path
            )
            error_cleanup_failures: list[str] = []
            if trimmed_audio_path is not None and trimmed_audio_path != audio_path:
                try:
                    trimmed_audio_deleted = _remove_recording_artifact_if_present(
                        trimmed_audio_path,
                        suffix=trimmed_audio_path.suffix.lower(),
                        expected_stat=trimmed_audio_stat,
                    )
                except BaseException:
                    error_cleanup_failures.append("transient trimmed recording artifact")
                    exc.add_note("transient trimmed recording cleanup failed")
                else:
                    if not trimmed_audio_deleted:
                        error_cleanup_failures.append("transient trimmed recording artifact")
            stabilized_audio_deleted = False
            if (
                stabilized_audio_path is not None
                and stabilized_audio_path != preserved_encrypted_audio_path
                and not stabilized_is_state_anchor
            ):
                try:
                    stabilized_audio_deleted = _remove_recording_artifact_if_present(
                        stabilized_audio_path,
                        suffix=stabilized_audio_path.suffix.lower(),
                        expected_stat=stabilized_audio_stat,
                    )
                except BaseException:
                    error_cleanup_failures.append("stabilized recording artifact")
                    exc.add_note("stabilized recording cleanup failed")
                else:
                    if not stabilized_audio_deleted:
                        error_cleanup_failures.append("stabilized recording artifact")
            if (
                retryable_pre_output_processing_failure
                and preserved_encrypted_audio_path is not None
                and audio_path != preserved_encrypted_audio_path
            ):
                try:
                    decrypted_audio_deleted = _remove_recording_artifact_if_present(
                        audio_path,
                        suffix=audio_path.suffix.lower(),
                        expected_stat=original_audio_stat,
                    )
                except BaseException:
                    error_cleanup_failures.append("decrypted recording recovery artifact")
                    exc.add_note("decrypted recording recovery cleanup failed")
                else:
                    if not decrypted_audio_deleted:
                        error_cleanup_failures.append(
                            "decrypted recording recovery artifact"
                        )
            if error_cleanup_failures:
                error_text = (
                    f"{error_text}; failed to delete recording artifact(s): "
                    f"{', '.join(error_cleanup_failures)}"
                )
            error_update: dict[str, object] = {
                "status": (
                    "finalizing"
                    if (
                        safe_no_output_failure
                        or (
                            retryable_pre_output_processing_failure
                            and not error_cleanup_failures
                        )
                    )
                    else "error"
                ),
                "pid": None,
                "process_identity": "",
                "stopped_at": now_iso(),
                "error": error_text,
                "inserted": inserted,
            }
            if retryable_pre_output_processing_failure:
                error_update["transcript"] = ""
                error_update["transcript_path"] = ""
            preserved_recovery_anchor = False
            if preserved_encrypted_audio_path is not None:
                preserved_presence, _ = _safe_regular_leaf_probe(preserved_encrypted_audio_path)
                if preserved_presence is not False:
                    error_update["audio_path"] = str(preserved_encrypted_audio_path)
                    preserved_recovery_anchor = True
                elif str(state.audio_path or "") == str(preserved_encrypted_audio_path):
                    error_update["audio_path"] = ""
            if stabilized_audio_path is not None:
                stabilized_presence, _ = _safe_regular_leaf_probe(stabilized_audio_path)
                if stabilized_presence is False:
                    if stabilized_is_state_anchor and not preserved_recovery_anchor:
                        error_update["audio_path"] = ""
                else:
                    error_update["audio_path"] = str(stabilized_audio_path)
            if audio_deleted and persisted_audio_path:
                error_update["audio_path"] = ""
            if log_deleted and persisted_log_path:
                error_update["log_path"] = ""
            if silent_transcript_state_cleared:
                error_update["transcript"] = ""
                error_update["transcript_path"] = ""
            elif written_text_path is not None and preserve_written_text_on_error:
                error_update["transcript"] = (
                    stored_transcript_text if transcript_encryption == ARTIFACT_ENCRYPTION_OFF else ""
                )
                error_update["transcript_path"] = str(written_text_path)
            elif written_text_path is not None and not preserve_written_text_on_error:
                try:
                    if written_text_stat is None:
                        raise RuntimeError(f"transcript file identity is unavailable: {written_text_path}")
                    _remove_transcript_file(written_text_path, expected_stat=written_text_stat)
                    transcript_presence, _ = _safe_regular_leaf_probe(written_text_path)
                    if transcript_presence is not False:
                        raise RuntimeError("transcript cleanup could not be verified")
                except BaseException:
                    error_update["error"] = TRANSIENT_TRANSCRIPT_CLEANUP_ERROR
                    transcript_presence, _ = _safe_regular_leaf_probe(written_text_path)
                    error_update["transcript_path"] = (
                        str(written_text_path) if transcript_presence is not False else ""
                    )
                    exc.add_note("transcript cleanup failed")
                else:
                    error_update["transcript"] = ""
                    error_update["transcript_path"] = ""
            final_error_text = error_update.get("error", error_text)
            if not isinstance(final_error_text, str):
                final_error_text = error_text
            cleanup_targets_by_field: dict[
                str,
                list[tuple[str, str, bool | None, os.stat_result | None]],
            ] = {}
            cleanup_plaintext_recording_artifacts = (
                keep_recording_artifacts
                and artifact_encryption != ARTIFACT_ENCRYPTION_OFF
            )
            if (
                not retryable_pre_output_processing_failure
                and not preserve_recording_artifacts_after_cleanup_failure
                and (not keep_recording_artifacts or cleanup_plaintext_recording_artifacts)
            ):
                if (
                    audio_suffix
                    and not audio_deleted
                    and (not cleanup_plaintext_recording_artifacts or audio_suffix in {".wav", ".flac"})
                ):
                    audio_cleanup_presence, audio_cleanup_stat = (
                        _safe_regular_leaf_probe(audio_path)
                    )
                    cleanup_targets_by_field.setdefault(
                        "audio_path",
                        [],
                    ).append(
                        (
                            str(audio_path),
                            audio_suffix,
                            audio_cleanup_presence,
                            audio_cleanup_stat,
                        )
                    )
                if not keep_recording_artifacts and preserved_encrypted_audio_path is not None:
                    encrypted_cleanup_presence, encrypted_cleanup_stat = (
                        _safe_regular_leaf_probe(preserved_encrypted_audio_path)
                    )
                    cleanup_targets_by_field.setdefault(
                        "audio_path",
                        [],
                    ).append(
                        (
                            str(preserved_encrypted_audio_path),
                            ".socenc",
                            encrypted_cleanup_presence,
                            encrypted_cleanup_stat,
                        )
                    )
                if state.log_path and not log_deleted:
                    log_cleanup_presence, log_cleanup_stat = (
                        _safe_regular_leaf_probe(log_path)
                        if log_path is not None
                        else (None, None)
                    )
                    cleanup_targets_by_field.setdefault(
                        "log_path",
                        [],
                    ).append(
                        (
                            str(log_path) if log_path else state.log_path,
                            ".log",
                            log_cleanup_presence,
                            log_cleanup_stat,
                        )
                    )
            try:
                _finalize_store_update(**error_update)
            except BaseException as update_exc:
                _raise_error_state_update_failure(update_exc, final_error_text)
            if cleanup_targets_by_field:
                try:
                    cleanup_clear_update: dict[str, object] = {}
                    for cleanup_field, field_targets in cleanup_targets_by_field.items():
                        field_deleted = True
                        for (
                            cleanup_path,
                            cleanup_suffix,
                            cleanup_presence,
                            cleanup_stat,
                        ) in field_targets:
                            if cleanup_presence is False:
                                continue
                            if cleanup_presence is not True or cleanup_stat is None:
                                field_deleted = False
                                break
                            remove_file(
                                cleanup_path,
                                suffix=cleanup_suffix,
                                expected_stat=cleanup_stat,
                            )
                            try:
                                post_cleanup_presence, _ = _safe_regular_leaf_probe(Path(cleanup_path))
                            except BaseException:
                                post_cleanup_presence = None
                            if post_cleanup_presence is not False:
                                field_deleted = False
                                break
                        if field_deleted:
                            cleanup_clear_update[cleanup_field] = ""
                    if cleanup_clear_update:
                        _finalize_store_update(**cleanup_clear_update)
                except BaseException as update_exc:
                    _raise_error_state_update_failure(update_exc, final_error_text, "error cleanup state")
            try:
                _discard_cleanup_backups()
            except BaseException:
                exc.add_note("cleanup backup discard failed")
        final_error = (
            error_update.get("error", error_text)
            if state_marked_finalizing and isinstance(error_update.get("error", error_text), str)
            else error_text
        )
        classified_final_error = (
            _fresh_classified_public_error(finalize_public_error, final_error)
            if finalize_public_error is not None
            else None
        )
        if insert_backend_interrupted and isinstance(exc, KeyboardInterrupt):
            deferred_final_error = _clear_transient_exception_metadata(
                KeyboardInterrupt()
            )
        else:
            deferred_final_error = classified_final_error or _sanitize_transient_exception(
                exc,
                message=final_error,
                system_exit_as_runtime=True,
            )
    finally:
        _release_finalization_lock(lock_path)
    if deferred_final_error is not None:
        raise deferred_final_error


def remove_file(
    path_value: str | None,
    *,
    suffix: str | None = None,
    recordings_root: Path | None = None,
    expected_stat: os.stat_result | None = None,
) -> bool:
    if not path_value:
        return False
    try:
        path_value = _assert_clean_text(path_value, field_name="path", max_chars=MAX_PATH_CHARS)
    except RuntimeError:
        return False
    if suffix:
        try:
            path = validate_recording_path(
                Path(path_value),
                suffix=suffix,
                require_recordings_dir=True,
                recordings_root=recordings_root,
            )
        except (RecorderError, RuntimeError, ValueError, OSError):
            return False
    else:
        path = Path(path_value)
    file_stat = _recording_artifact_stat(path)
    if file_stat is None:
        return False
    if expected_stat is not None and not _same_leaf_identity(file_stat, expected_stat):
        return False
    try:
        return _unlink_regular_leaf_with_parent_fsync(
            path,
            field_name="recording artifact",
            expected_stat=expected_stat or file_stat,
        )
    except RuntimeError:
        return False


def command_stop(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    state = store.read()
    _raise_if_state_unreadable(state)
    if state.status != "recording":
        if state.status in {"recorded", "processing"}:
            return _finalize_non_recording_state_with_lock(args, store)
        if state.status == "finalizing":
            return _finalize_non_recording_state_with_lock(args, store)
        if _is_finalization_lock_active(store.path):
            return {"status": "finalizing", "message": "recording lifecycle in progress; wait for completion"}
        return {"status": state.status, "message": "not recording"}

    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        return {"status": "finalizing", "message": "finalization already in progress"}
    try:
        state = store.read()
        _raise_if_state_unreadable(state)
        if state.status != "recording":
            if state.status == "finalizing":
                if state.audio_path or state.transcript_path:
                    return finalize_recording(
                        args,
                        store,
                        state,
                        finalization_lock_path=lock_path,
                    )
                return {"status": "finalizing", "message": "finalization in progress"}
            if state.status in {"recorded", "processing"}:
                return finalize_recording(args, store, state, finalization_lock_path=lock_path)
            return {"status": state.status, "message": "not recording"}
        if state.pid is None:
            error_text = "recording process pid is missing; recording state preserved"
            store.update(status="recording", error=error_text, inserted=False)
            return {"status": "recording", "message": error_text, "error": error_text}
        process_error = _reconcile_recording_process(state)
        if process_error is not None:
            if process_error == _RECORDING_PROCESS_GROUP_ACTIVE_ERROR:
                return {
                    "status": "recording",
                    "message": process_error,
                    "error": process_error,
                }
            store.update(status="recording", error=process_error, inserted=False)
            return {"status": "recording", "message": process_error, "error": process_error}
        state = store.update(
            status="recorded",
            pid=None,
            process_identity="",
            stopped_at=now_iso(),
            error="",
            inserted=False,
        )
        return finalize_recording(args, store, state, finalization_lock_path=lock_path)
    finally:
        _release_finalization_lock(lock_path)


def command_cancel(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    cleanup_state_namespace: str | None = None
    legacy_cleanup_prefixes: dict[Path, str] = {}
    v2_cleanup_prefixes: dict[Path, str] = {}
    v3_cleanup_prefixes: dict[Path, str] = {}
    persisted_legacy_owner_prefixes: frozenset[str] | None = None

    def current_cleanup_state_namespace() -> str:
        nonlocal cleanup_state_namespace
        if cleanup_state_namespace is None:
            cleanup_state_namespace = (
                _cleanup_backup_state_namespace(store.path)
            )
        return cleanup_state_namespace

    def current_legacy_cleanup_prefix(path: Path) -> str:
        prefix = legacy_cleanup_prefixes.get(path)
        if prefix is None:
            prefix = _cleanup_backup_prefix(path)
            legacy_cleanup_prefixes[path] = prefix
        return prefix

    def current_v2_cleanup_prefix(path: Path) -> str:
        prefix = v2_cleanup_prefixes.get(path)
        if prefix is None:
            prefix = _cleanup_backup_v2_prefix(
                path,
                store.path,
                state_namespace=current_cleanup_state_namespace(),
            )
            v2_cleanup_prefixes[path] = prefix
        return prefix

    def current_v3_cleanup_prefix(path: Path) -> str:
        prefix = v3_cleanup_prefixes.get(path)
        if prefix is None:
            prefix = _cleanup_backup_v3_prefix(
                path,
                store.path,
                state_namespace=current_cleanup_state_namespace(),
            )
            v3_cleanup_prefixes[path] = prefix
        return prefix

    def current_persisted_legacy_owner_prefixes(
        owner_paths: set[Path],
    ) -> frozenset[str]:
        nonlocal persisted_legacy_owner_prefixes
        if persisted_legacy_owner_prefixes is None:
            persisted_legacy_owner_prefixes = frozenset(
                current_legacy_cleanup_prefix(path)
                for path in owner_paths
            )
        return persisted_legacy_owner_prefixes

    def new_transaction_claim_path(
        path: Path,
        content_digest: str,
    ) -> tuple[Path, str] | None:
        prefix = current_v3_cleanup_prefix(path)
        for _ in range(100):
            nonce = secrets.token_hex(
                _CLEANUP_CLAIM_NONCE_HEX_CHARS // 2
            )
            if (
                type(nonce) is not str
                or len(nonce) != _CLEANUP_CLAIM_NONCE_HEX_CHARS
                or any(
                    character not in "0123456789abcdef"
                    for character in nonce
                )
            ):
                return None
            candidate = path.with_name(
                f"{prefix}tombstone.{content_digest}.{nonce}.done"
            )
            presence = _safe_leaf_presence(candidate)
            if presence is None:
                return None
            if not presence:
                return candidate, nonce
        return None

    def cleanup_identity_claims(
        expected_stats: dict[Path, os.stat_result | None],
    ) -> tuple[tuple[Path, str], ...] | None:
        claims: list[tuple[Path, str]] = []
        for path in expected_stats:
            expected_stat = expected_stats[path]
            presence, current_stat = _safe_regular_leaf_probe(path)
            if expected_stat is None:
                if presence is False:
                    continue
                return None
            if (
                presence is not True
                or current_stat is None
                or not _same_leaf_identity(current_stat, expected_stat)
            ):
                return None
            identity = (
                current_stat.st_dev,
                current_stat.st_ino,
                current_stat.st_mode,
                getattr(current_stat, "st_nlink", 1),
                current_stat.st_size,
                current_stat.st_mtime_ns,
                current_stat.st_ctime_ns,
            )
            snapshot = _cleanup_claim_content_snapshot(
                path,
                identity,
                identity_fields=7,
            )
            if snapshot is None:
                return None
            content_digest, bound_stat = snapshot
            claim_binding = new_transaction_claim_path(
                path,
                content_digest,
            )
            if claim_binding is None:
                return None
            claim_path, _nonce = claim_binding
            intent_path = path.with_name(
                _cleanup_claim_journal_basename(
                    claim_path.name,
                    "intent",
                )
            )
            claims.append(
                (
                    path,
                    _cleanup_backup_journal_entry(
                        intent_path,
                        bound_stat,
                    ),
                )
            )
        return tuple(claims)

    def recovery_cleanup_transaction_groups(
        transaction_state: RecordingState,
    ) -> tuple[
        tuple[
            Path,
            str,
            str,
            str,
            str,
            tuple[int, int, int, int, int, int, int],
        ],
        ...,
    ] | None:
        owner_values = transaction_state.pending_cleanup_owner_paths
        entry_values = transaction_state.pending_cleanup_backup_entries
        if not owner_values and not entry_values:
            return ()
        if (
            not owner_values
            or len(owner_values) != len(entry_values)
            or transaction_state.pending_cleanup_restore_owner_paths
            or transaction_state.cleanup_backup_journal_restore
            or transaction_state.cleanup_backup_journal_overflow
        ):
            return None
        try:
            owners = tuple(
                _normalized_cleanup_backup_owner_path(
                    value,
                    state_path=store.path,
                )
                for value in owner_values
            )
            if len(set(owners)) != len(owners):
                return None
            groups: list[
                tuple[
                    Path,
                    str,
                    str,
                    str,
                    str,
                    tuple[int, int, int, int, int, int, int],
                ]
            ] = []
            seen_names: set[str] = set()
            for owner_path, entry in zip(owners, entry_values, strict=True):
                backup_name, identity = (
                    _parse_cleanup_backup_journal_entry(entry)
                )
                parts = _bound_cleanup_v3_basename_parts(
                    backup_name,
                    phases=frozenset(
                        {"intent", "claimed", "prepared", "armed", "wiping"}
                    ),
                )
                prefix = current_v3_cleanup_prefix(owner_path)
                if (
                    backup_name in seen_names
                    or parts is None
                    or backup_name
                    != (
                        f"{prefix}{parts[2]}.{parts[3]}."
                        f"{parts[4]}.bak"
                    )
                ):
                    return None
                seen_names.add(backup_name)
                groups.append(
                    (
                        owner_path,
                        entry,
                        parts[2],
                        parts[3],
                        parts[4],
                        identity,
                    )
                )
            return tuple(groups)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def legacy_cleanup_journal_is_valid(
        transaction_state: RecordingState,
    ) -> bool:
        if (
            not transaction_state.pending_cleanup_owner_paths
            or not transaction_state.pending_cleanup_backup_entries
            or transaction_state.pending_cleanup_restore_owner_paths
            or transaction_state.cleanup_backup_journal_restore
            or transaction_state.cleanup_backup_journal_overflow
        ):
            return False
        try:
            owners = tuple(
                _normalized_cleanup_backup_owner_path(
                    value,
                    state_path=store.path,
                )
                for value in transaction_state.pending_cleanup_owner_paths
            )
            if len(set(owners)) != len(owners):
                return False
            matched_owners: set[Path] = set()
            seen_names: set[str] = set()
            for entry in transaction_state.pending_cleanup_backup_entries:
                backup_name, _identity = (
                    _parse_cleanup_backup_journal_entry(entry)
                )
                if (
                    backup_name in seen_names
                    or backup_name.startswith(".cleanup.v3.")
                ):
                    return False
                matching_owners = {
                    owner_path
                    for owner_path in owners
                    if backup_name.startswith(
                        current_v2_cleanup_prefix(owner_path)
                        if backup_name.startswith(".cleanup.v2.")
                        else current_legacy_cleanup_prefix(owner_path)
                    )
                }
                if len(matching_owners) != 1:
                    return False
                matched_owners.update(matching_owners)
                seen_names.add(backup_name)
            return matched_owners == set(owners)
        except (OSError, RuntimeError, TypeError, ValueError):
            return False

    def transaction_claim_path(
        owner_path: Path,
        content_digest: str,
        nonce: str,
    ) -> Path:
        return owner_path.with_name(
            f"{current_v3_cleanup_prefix(owner_path)}"
            f"tombstone.{content_digest}.{nonce}.done"
        )

    def migration_claim_nonce(source_name: str) -> str:
        if not _is_cleanup_backup_journal_basename(source_name):
            raise RuntimeError("cleanup migration source is invalid")
        return hashlib.sha256(
            b"SOC-CLEANUP-V3-MIGRATION-NONCE\x00"
            + source_name.encode("ascii")
        ).hexdigest()[:_CLEANUP_CLAIM_NONCE_HEX_CHARS]

    def transaction_journal_path(
        owner_path: Path,
        claim_path: Path,
        phase: str,
    ) -> Path:
        return owner_path.with_name(
            _cleanup_claim_journal_basename(claim_path.name, phase)
        )

    def largest_transaction_phase_entries(
        entries: tuple[str, ...],
    ) -> tuple[str, ...] | None:
        projected_entries: list[str] = []
        try:
            for entry in entries:
                journal_name, identity = (
                    _parse_cleanup_backup_journal_entry(entry)
                )
                physical_name = _cleanup_claim_physical_basename(
                    journal_name
                )
                if physical_name is None:
                    return None
                bound_parts = _bound_cleanup_v3_basename_parts(
                    journal_name,
                    phases=frozenset(
                        {"intent", "claimed", "prepared", "armed", "wiping"}
                    ),
                )
                if bound_parts is None:
                    return None
                trailer_size = len(
                    _cleanup_claim_commit_trailer(
                        physical_name,
                        bound_parts[3],
                        0,
                    )
                )
                original_size = identity[4]
                if bound_parts[2] == "prepared":
                    original_size -= trailer_size
                elif bound_parts[2] == "armed":
                    original_size -= len(
                        _cleanup_claim_commit_trailer(
                            physical_name,
                            bound_parts[3],
                            0,
                            authorized=True,
                        )
                    )
                if original_size < 0:
                    return None
                candidates: list[str] = []
                for phase, size in (
                    ("claimed", original_size),
                    ("prepared", original_size + trailer_size),
                    (
                        "armed",
                        original_size
                        + len(
                            _cleanup_claim_commit_trailer(
                                physical_name,
                                bound_parts[3],
                                0,
                                authorized=True,
                            )
                        ),
                    ),
                ):
                    projected_identity = (
                        identity[0],
                        identity[1],
                        identity[2],
                        identity[3],
                        size,
                        MAX_CLEANUP_BACKUP_IDENTITY_VALUE,
                        MAX_CLEANUP_BACKUP_IDENTITY_VALUE,
                    )
                    candidates.append(
                        _cleanup_backup_journal_entry_from_identity(
                            _cleanup_claim_journal_basename(
                                physical_name,
                                phase,
                            ),
                            projected_identity,
                        )
                    )
                projected_entries.append(max(candidates, key=len))
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return tuple(projected_entries)

    def transaction_state_would_fit(
        candidate_state: RecordingState,
    ) -> bool:
        projected_entries = largest_transaction_phase_entries(
            candidate_state.pending_cleanup_backup_entries
        )
        if projected_entries is None:
            return False
        return StateStore.write_would_fit(
            replace(
                candidate_state,
                pending_cleanup_backup_entries=projected_entries,
            )
        )

    def transaction_snapshot(
        path: Path,
        identity: tuple[int, int, int, int, int, int, int],
        *,
        identity_fields: int,
        content_digest: str | None,
    ) -> tuple[bool | None, os.stat_result | None]:
        presence = _safe_leaf_presence(path)
        if presence is not True:
            return presence, None
        if content_digest is None:
            regular, file_stat = _safe_regular_leaf_probe(path)
            if (
                regular is not True
                or file_stat is None
                or not _cleanup_backup_journal_claim_matches(
                    file_stat,
                    identity,
                    fields=identity_fields,
                )
            ):
                return None, None
            return True, file_stat
        snapshot = _cleanup_claim_content_snapshot(
            path,
            identity,
            identity_fields=identity_fields,
        )
        if snapshot is None or not secrets.compare_digest(
            snapshot[0],
            content_digest,
        ):
            return None, None
        return True, snapshot[1]

    def replace_transaction_entry(
        current_state: RecordingState,
        owner_path: Path,
        old_entry: str,
        replacement_path: Path,
        replacement_stat: os.stat_result,
        *,
        phase: str,
    ) -> RecordingState:
        owners = list(current_state.pending_cleanup_owner_paths)
        entries = list(current_state.pending_cleanup_backup_entries)
        owner_index = owners.index(str(owner_path))
        if entries[owner_index] != old_entry:
            raise RuntimeError("recording cleanup transaction changed")
        entries[owner_index] = _cleanup_backup_journal_entry(
            transaction_journal_path(
                owner_path,
                replacement_path,
                phase,
            ),
            replacement_stat,
        )
        changes: dict[str, object] = {
            "status": "finalizing",
            "pending_cleanup_backup_entries": tuple(entries),
            "cleanup_backup_journal_overflow": False,
            "error": current_state.error,
        }
        if current_state.audio_path == str(owner_path):
            changes["audio_path"] = ""
        if current_state.log_path == str(owner_path):
            changes["log_path"] = ""
        return store.update(
            **changes,
        )

    def rename_transaction_phase(
        source_path: Path,
        target_path: Path,
        identity: tuple[int, int, int, int, int, int, int],
        content_digest: str,
        *,
        source_identity_fields: int,
    ) -> os.stat_result | None:
        source_presence, _source_stat = transaction_snapshot(
            source_path,
            identity,
            identity_fields=source_identity_fields,
            content_digest=content_digest,
        )
        if source_presence is not True:
            return None
        if _safe_leaf_presence(target_path) is not False:
            return None
        try:
            parent_fd = ensure_directory_without_following_symlinks(
                source_path.parent,
                field_name="recording cleanup directory",
            )
        except (OSError, RuntimeError):
            return None
        primary_error: BaseException | None = None
        result: os.stat_result | None = None
        try:
            _rename_without_replacing(
                source_path.name,
                target_path.name,
                directory_fd=parent_fd,
                field_name="recording cleanup retry artifact",
            )
            target_snapshot = _cleanup_claim_content_snapshot(
                target_path,
                identity,
                identity_fields=6,
            )
            if target_snapshot is None or not secrets.compare_digest(
                target_snapshot[0],
                content_digest,
            ):
                raise RuntimeError("cleanup transaction binding failed")
            _fsync_fd(parent_fd)
            result = target_snapshot[1]
        except (KeyboardInterrupt, SystemExit) as exc:
            primary_error = exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            primary_error = exc
        finally:
            try:
                os.close(parent_fd)
            except BaseException as exc:
                if primary_error is None or (
                    isinstance(exc, (KeyboardInterrupt, SystemExit))
                    and not isinstance(
                        primary_error,
                        (KeyboardInterrupt, SystemExit),
                    )
                ):
                    primary_error = exc
        if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
            raise primary_error
        if primary_error is not None:
            return None
        return result

    def fsync_transaction_parent(path: Path) -> bool:
        parent_fd: int | None = None
        primary_error: BaseException | None = None
        succeeded = False
        try:
            parent_fd = ensure_directory_without_following_symlinks(
                path.parent,
                field_name="recording cleanup directory",
            )
            _fsync_fd(parent_fd)
            succeeded = True
        except (KeyboardInterrupt, SystemExit) as exc:
            primary_error = exc
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            primary_error = exc
        finally:
            if parent_fd is not None:
                try:
                    os.close(parent_fd)
                except BaseException as exc:
                    if primary_error is None or (
                        isinstance(exc, (KeyboardInterrupt, SystemExit))
                        and not isinstance(
                            primary_error,
                            (KeyboardInterrupt, SystemExit),
                        )
                    ):
                        primary_error = exc
        if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
            raise primary_error
        return succeeded and primary_error is None

    def migrate_tokenless_v3_transaction(
        transaction_state: RecordingState,
    ) -> RecordingState | None:
        entries = transaction_state.pending_cleanup_backup_entries
        tokenless_parts = tuple(
            _tokenless_cleanup_v3_basename_parts(
                entry.split("|", 1)[0]
            )
            for entry in entries
        )
        if not any(parts is not None for parts in tokenless_parts):
            return transaction_state
        if (
            not entries
            or any(parts is None for parts in tokenless_parts)
            or len(entries) != len(transaction_state.pending_cleanup_owner_paths)
            or transaction_state.pending_cleanup_restore_owner_paths
            or transaction_state.cleanup_backup_journal_restore
            or transaction_state.cleanup_backup_journal_overflow
        ):
            return None
        try:
            owners = tuple(
                _normalized_cleanup_backup_owner_path(
                    value,
                    state_path=store.path,
                )
                for value in transaction_state.pending_cleanup_owner_paths
            )
            if len(set(owners)) != len(owners):
                return None
            prepared: list[
                tuple[
                    Path,
                    Path | None,
                    Path,
                    tuple[int, int, int, int, int, int, int],
                    str,
                    int,
                    os.stat_result,
                ]
            ] = []
            retired: list[
                tuple[
                    Path,
                    Path | None,
                    tuple[int, int, int, int, int, int, int],
                ]
            ] = []
            prospective_entries: list[str] = []
            for owner_path, entry, parts in zip(
                owners,
                entries,
                tokenless_parts,
                strict=True,
            ):
                if parts is None:
                    return None
                backup_name, identity = (
                    _parse_cleanup_backup_journal_entry(entry)
                )
                state_namespace, owner_namespace, phase, digest = parts
                if backup_name != (
                    f".cleanup.v3.{state_namespace}.{owner_namespace}."
                    f"{parts[2]}.{digest}.bak"
                ) or not backup_name.startswith(
                    current_v3_cleanup_prefix(owner_path)
                ):
                    return None
                source_path = owner_path.with_name(backup_name)
                claim_path = transaction_claim_path(
                    owner_path,
                    digest,
                    migration_claim_nonce(backup_name),
                )
                owner_presence = _safe_leaf_presence(owner_path)
                source_presence = _safe_leaf_presence(source_path)
                claim_presence = _safe_leaf_presence(claim_path)
                if None in {owner_presence, source_presence, claim_presence}:
                    return None
                if (
                    phase == "wiping"
                    and not owner_presence
                    and not claim_presence
                ):
                    if not source_presence:
                        retired.append((owner_path, None, identity))
                        continue
                    regular, zero_stat = _safe_regular_leaf_probe(source_path)
                    if (
                        regular is not True
                        or zero_stat is None
                    ):
                        return None
                    if zero_stat.st_size == 0:
                        if (
                            zero_stat.st_dev,
                            zero_stat.st_ino,
                            zero_stat.st_mode,
                            getattr(zero_stat, "st_nlink", 1),
                        ) != identity[:4]:
                            return None
                        retired.append((owner_path, source_path, identity))
                        continue
                selected_source: Path | None
                source_identity_fields: int
                if claim_presence:
                    if owner_presence or source_presence:
                        return None
                    selected_source = None
                    source_identity_fields = 6
                    snapshot = _cleanup_claim_content_snapshot(
                        claim_path,
                        identity,
                        identity_fields=6,
                    )
                elif source_presence:
                    if owner_presence:
                        return None
                    selected_source = source_path
                    source_identity_fields = 6
                    snapshot = _cleanup_claim_content_snapshot(
                        source_path,
                        identity,
                        identity_fields=6,
                    )
                elif phase == "intent" and owner_presence:
                    selected_source = owner_path
                    source_identity_fields = 7
                    snapshot = _cleanup_claim_content_snapshot(
                        owner_path,
                        identity,
                        identity_fields=7,
                    )
                else:
                    return None
                if snapshot is None or not secrets.compare_digest(
                    snapshot[0],
                    digest,
                ):
                    return None
                journal_path = transaction_journal_path(
                    owner_path,
                    claim_path,
                    "claimed",
                )
                prospective_entries.append(
                    _cleanup_backup_journal_entry(
                        journal_path,
                        snapshot[1],
                    )
                )
                prepared.append(
                    (
                        owner_path,
                        selected_source,
                        claim_path,
                        identity,
                        digest,
                        source_identity_fields,
                        snapshot[1],
                    )
                )
            prospective_state = replace(
                transaction_state,
                status="finalizing",
                pending_cleanup_owner_paths=tuple(
                    str(item[0]) for item in prepared
                ),
                pending_cleanup_restore_owner_paths=(),
                pending_cleanup_backup_entries=tuple(prospective_entries),
                cleanup_backup_journal_overflow=False,
                cleanup_backup_journal_restore=False,
            )
            if not transaction_state_would_fit(prospective_state):
                return None
            for owner_path, retired_path, identity in retired:
                if retired_path is None:
                    if not fsync_transaction_parent(owner_path):
                        return None
                elif not _retire_zero_cleanup_tombstone(
                    retired_path,
                    identity,
                ):
                    return None
            migrated_entries: list[str] = []
            for (
                owner_path,
                source_path,
                claim_path,
                identity,
                digest,
                source_identity_fields,
                _source_stat,
            ) in prepared:
                if source_path is None:
                    existing_snapshot = _cleanup_claim_content_snapshot(
                        claim_path,
                        identity,
                        identity_fields=6,
                    )
                    if (
                        existing_snapshot is None
                        or not secrets.compare_digest(
                            existing_snapshot[0],
                            digest,
                        )
                        or not fsync_transaction_parent(claim_path)
                    ):
                        return None
                    migrated_stat = existing_snapshot[1]
                else:
                    migrated_stat = rename_transaction_phase(
                        source_path,
                        claim_path,
                        identity,
                        digest,
                        source_identity_fields=source_identity_fields,
                    )
                if migrated_stat is None:
                    return None
                migrated_entries.append(
                    _cleanup_backup_journal_entry(
                        transaction_journal_path(
                            owner_path,
                            claim_path,
                            "claimed",
                        ),
                        migrated_stat,
                    )
                )
            return store.update(
                status="finalizing",
                audio_path=(
                    ""
                    if transaction_state.audio_path
                    in {str(path) for path in owners}
                    else transaction_state.audio_path
                ),
                log_path=(
                    ""
                    if transaction_state.log_path
                    in {str(path) for path in owners}
                    else transaction_state.log_path
                ),
                pending_cleanup_owner_paths=tuple(
                    str(item[0]) for item in prepared
                ),
                pending_cleanup_restore_owner_paths=(),
                pending_cleanup_backup_entries=tuple(migrated_entries),
                cleanup_backup_journal_overflow=False,
                cleanup_backup_journal_restore=False,
                error=transaction_state.error,
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def process_recovery_cleanup_transaction(
        transaction_state: RecordingState,
    ) -> tuple[RecordingState, bool, bool] | None:
        groups = recovery_cleanup_transaction_groups(transaction_state)
        if groups is None:
            return None
        if not groups:
            return transaction_state, True, True
        current_state = transaction_state
        owned_artifacts_absent = True
        for owner_path, entry, phase, content_digest, nonce, identity in groups:
            claim_path = transaction_claim_path(
                owner_path,
                content_digest,
                nonce,
            )
            owner_presence = _safe_leaf_presence(owner_path)
            if owner_presence is None:
                return current_state, False, False
            claim_presence = _safe_leaf_presence(claim_path)
            if claim_presence is None:
                return current_state, False, False
            if claim_presence and phase == "armed":
                regular, zero_stat = _safe_regular_leaf_probe(claim_path)
                if (
                    regular is True
                    and zero_stat is not None
                    and zero_stat.st_size == 0
                    and (
                        zero_stat.st_dev,
                        zero_stat.st_ino,
                        zero_stat.st_mode,
                        getattr(zero_stat, "st_nlink", 1),
                    )
                    == identity[:4]
                ):
                    if not _retire_zero_cleanup_tombstone(
                        claim_path,
                        identity,
                    ):
                        return current_state, False, False
                    if owner_presence:
                        owned_artifacts_absent = False
                    continue
            if not claim_presence and phase == "armed":
                if owner_presence:
                    owned_artifacts_absent = False
                if not fsync_transaction_parent(owner_path):
                    return current_state, False, False
                continue
            if not claim_presence and (
                phase != "intent" or not owner_presence
            ):
                return current_state, False, False
            if not claim_presence:
                if not transaction_state_would_fit(current_state):
                    return current_state, False, False
                owner_snapshot = _cleanup_claim_content_snapshot(
                    owner_path,
                    identity,
                    identity_fields=7,
                )
                if owner_snapshot is None or not secrets.compare_digest(
                    owner_snapshot[0],
                    content_digest,
                ):
                    return current_state, False, False
                claim_stat = rename_transaction_phase(
                    owner_path,
                    claim_path,
                    identity,
                    content_digest,
                    source_identity_fields=7,
                )
                if claim_stat is None:
                    return current_state, False, False
                current_state = replace_transaction_entry(
                    current_state,
                    owner_path,
                    entry,
                    claim_path,
                    claim_stat,
                    phase="claimed",
                )
                phase = "claimed"
                entry = current_state.pending_cleanup_backup_entries[
                    list(current_state.pending_cleanup_owner_paths).index(
                        str(owner_path)
                    )
                ]
                _name, identity = _parse_cleanup_backup_journal_entry(entry)
            elif phase == "intent":
                observed = _cleanup_claim_content_snapshot(
                    claim_path,
                    identity,
                    identity_fields=6,
                )
                if observed is None or not secrets.compare_digest(
                    observed[0],
                    content_digest,
                ):
                    return current_state, False, False
                if not fsync_transaction_parent(claim_path):
                    return current_state, False, False
                current_state = replace_transaction_entry(
                    current_state,
                    owner_path,
                    entry,
                    claim_path,
                    observed[1],
                    phase="claimed",
                )
                phase = "claimed"
                entry = current_state.pending_cleanup_backup_entries[
                    list(current_state.pending_cleanup_owner_paths).index(
                        str(owner_path)
                    )
                ]
                _name, identity = _parse_cleanup_backup_journal_entry(entry)

            if not fsync_transaction_parent(claim_path):
                return current_state, False, False
            if phase == "claimed":
                if not transaction_state_would_fit(current_state):
                    return current_state, False, False
                committed_stat = _commit_cleanup_claim(
                    claim_path,
                    identity,
                    content_digest,
                )
                if committed_stat is None:
                    return current_state, False, False
                current_state = replace_transaction_entry(
                    current_state,
                    owner_path,
                    entry,
                    claim_path,
                    committed_stat,
                    phase="prepared",
                )
                phase = "prepared"
                entry = current_state.pending_cleanup_backup_entries[
                    list(current_state.pending_cleanup_owner_paths).index(
                        str(owner_path)
                    )
                ]
                _name, identity = _parse_cleanup_backup_journal_entry(entry)
            authorization_retry_entry: str | None = None
            fresh_armed = False
            if phase == "wiping":
                owner_index = list(
                    current_state.pending_cleanup_owner_paths
                ).index(str(owner_path))
                projected_entries = list(
                    current_state.pending_cleanup_backup_entries
                )
                projected_identity = (
                    identity[0],
                    identity[1],
                    identity[2],
                    identity[3],
                    identity[4] + 1,
                    MAX_CLEANUP_BACKUP_IDENTITY_VALUE,
                    MAX_CLEANUP_BACKUP_IDENTITY_VALUE,
                )
                try:
                    projected_entries[owner_index] = (
                        _cleanup_backup_journal_entry_from_identity(
                            _cleanup_claim_journal_basename(
                                claim_path.name,
                                "armed",
                            ),
                            projected_identity,
                        )
                    )
                except (OSError, RuntimeError, TypeError, ValueError):
                    return current_state, False, False
                if not StateStore.write_would_fit(
                    replace(
                        current_state,
                        pending_cleanup_backup_entries=tuple(
                            projected_entries
                        ),
                    )
                ):
                    return current_state, False, False
                legacy_snapshot = (
                    _legacy_cleanup_claim_authorization_snapshot(
                        claim_path,
                        identity,
                        content_digest,
                    )
                )
                if legacy_snapshot is None:
                    return current_state, False, False
                legacy_original_size, legacy_stat = legacy_snapshot
                authorized_stat = _prepare_cleanup_claim_wipe_authorization(
                    claim_path,
                    (
                        legacy_stat.st_dev,
                        legacy_stat.st_ino,
                        legacy_stat.st_mode,
                        getattr(legacy_stat, "st_nlink", 1),
                        legacy_stat.st_size,
                        legacy_stat.st_mtime_ns,
                        legacy_stat.st_ctime_ns,
                    ),
                    content_digest,
                    legacy_original_size,
                )
                if authorized_stat is None:
                    return current_state, False, False
                authorization_retry_entry = entry
                current_state = replace_transaction_entry(
                    current_state,
                    owner_path,
                    entry,
                    claim_path,
                    authorized_stat,
                    phase="armed",
                )
                phase = "armed"
                fresh_armed = True
                entry = current_state.pending_cleanup_backup_entries[
                    owner_index
                ]
                _name, identity = _parse_cleanup_backup_journal_entry(entry)
            if phase == "prepared":
                prepared_trailer_size = len(
                    _cleanup_claim_commit_trailer(
                        claim_path.name,
                        content_digest,
                        0,
                    )
                )
                original_size = identity[4] - prepared_trailer_size
                if original_size < 0:
                    return current_state, False, False
                authorization_retry_entry = entry
                authorized_stat = _prepare_cleanup_claim_wipe_authorization(
                    claim_path,
                    identity,
                    content_digest,
                    original_size,
                )
                if authorized_stat is None:
                    return current_state, False, False
                current_state = replace_transaction_entry(
                    current_state,
                    owner_path,
                    entry,
                    claim_path,
                    authorized_stat,
                    phase="armed",
                )
                phase = "armed"
                fresh_armed = True
                entry = current_state.pending_cleanup_backup_entries[
                    list(current_state.pending_cleanup_owner_paths).index(
                        str(owner_path)
                    )
                ]
                _name, identity = _parse_cleanup_backup_journal_entry(entry)
            if phase != "armed":
                return current_state, False, False
            trailer_size = len(
                _cleanup_claim_commit_trailer(
                    claim_path.name,
                    content_digest,
                    0,
                    authorized=True,
                )
            )
            original_size = identity[4] - trailer_size
            if original_size < 0:
                return current_state, False, False
            if fresh_armed and _prepare_cleanup_claim_wipe_authorization(
                claim_path,
                identity,
                content_digest,
                original_size,
            ) is None:
                if authorization_retry_entry is not None:
                    entries = list(
                        current_state.pending_cleanup_backup_entries
                    )
                    owner_index = list(
                        current_state.pending_cleanup_owner_paths
                    ).index(str(owner_path))
                    if entries[owner_index] == entry:
                        entries[owner_index] = authorization_retry_entry
                        try:
                            current_state = store.update(
                                status="finalizing",
                                pending_cleanup_backup_entries=tuple(entries),
                                cleanup_backup_journal_overflow=False,
                                error=current_state.error,
                            )
                        except (KeyboardInterrupt, SystemExit):
                            raise
                        except Exception:
                            pass
                return current_state, False, False
            wiped_stat = _authorize_cleanup_claim_wipe(
                claim_path,
                identity,
                content_digest,
                original_size,
            )
            if wiped_stat is None:
                return current_state, False, False
            repeated, repeated_stat = _safe_regular_leaf_probe(claim_path)
            if (
                repeated is not True
                or repeated_stat is None
                or not _same_leaf_identity(repeated_stat, wiped_stat)
                or repeated_stat.st_size != 0
            ):
                return current_state, False, False
            if not _retire_zero_cleanup_tombstone(
                claim_path,
                identity,
            ):
                return current_state, False, False
            if _safe_leaf_presence(owner_path) is not False:
                owned_artifacts_absent = False

        current_state = store.update(
            status="finalizing",
            audio_path="",
            log_path="",
            pending_cleanup_owner_paths=(),
            pending_cleanup_backup_entries=(),
            cleanup_backup_journal_overflow=False,
            error=transaction_state.error,
        )
        if any(
            _safe_leaf_presence(owner_path) is not False
            for owner_path, _entry, _phase, _digest, _nonce, _identity in groups
        ):
            owned_artifacts_absent = False
        return (
            current_state,
            True,
            owned_artifacts_absent,
        )

    def cleanup_overflow_payload(
        blocked_state: RecordingState,
    ) -> dict[str, object]:
        error_text = "recording cleanup journal capacity exceeded"
        return {
            "status": "error",
            "message": error_text,
            "error": error_text,
            "discarded_audio_path_present": bool(blocked_state.audio_path),
            "audio_deleted": False,
            "log_deleted": False,
            "inflight_artifacts_deleted": False,
            "cleanup_backups_deleted": False,
            "transcript_deleted": False,
        }

    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        blocked_state = store.read()
        _raise_if_state_unreadable(blocked_state)
        if blocked_state.cleanup_backup_journal_overflow:
            return cleanup_overflow_payload(blocked_state)
        if (
            blocked_state.pending_cleanup_owner_paths
            or blocked_state.pending_cleanup_restore_owner_paths
            or blocked_state.pending_cleanup_backup_entries
        ):
            error_text = (
                "previous recording cleanup is unresolved; "
                "use cancel after completion"
            )
            return {
                "status": "error",
                "message": error_text,
                "error": error_text,
                "discarded_audio_path_present": bool(
                    blocked_state.audio_path
                ),
                "audio_deleted": False,
                "log_deleted": False,
                "inflight_artifacts_deleted": False,
                "cleanup_backups_deleted": False,
                "transcript_deleted": False,
            }
        return {
            "status": "finalizing",
            "message": "finalization in progress; use cancel after completion",
        }
    try:
        state = store.read()
        _raise_if_state_unreadable(state)
        if state.cleanup_backup_journal_overflow:
            return cleanup_overflow_payload(state)
        preserve_transcript_after_insert_failure = (
            state.status in {"error", "finalizing"}
            and _is_transcript_insert_recovery_error(state.error)
        )
        recovery_audio_was_bound = bool(state.audio_path)
        preserved_transcript_recovery_error = (
            state.error
            if preserve_transcript_after_insert_failure
            else None
        )
        preserved_output_recovery_error = (
            state.error
            if type(state.error) is str
            and state.error
            in {
                LEGACY_TRANSIENT_TRANSCRIPT_INSERT_ERROR,
                TRANSIENT_TRANSCRIPT_INSERT_ERROR,
                TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR,
            }
            else None
        )
        preserved_transcript_path = state.transcript_path
        if (
            preserve_transcript_after_insert_failure
            and not preserved_transcript_path
        ):
            recovery_audio_path = (
                _normalized_state_recording_artifact_path(
                    state.audio_path,
                    suffix=(".wav", ".flac", ".socenc"),
                    state_path=store.path,
                )
            )
            if recovery_audio_path is not None:
                transcript_audio_path = recovery_audio_path
                if _is_encrypted_recording_artifact(
                    transcript_audio_path
                ):
                    transcript_audio_path = (
                        _plaintext_recording_sibling_for_encrypted_path(
                            transcript_audio_path
                        )
                    )
                if transcript_audio_path is not None:
                    candidate = _transcript_path_for_audio(
                        transcript_audio_path
                    )
                    candidate_sibling = _transcript_sibling_path(candidate)
                    transcript_candidates = [candidate]
                    if candidate_sibling is not None:
                        transcript_candidates.append(candidate_sibling)
                    candidate_probes = [
                        _safe_regular_leaf_probe(path)
                        for path in transcript_candidates
                    ]
                    if all(
                        presence is False or file_stat is not None
                        for presence, file_stat in candidate_probes
                    ) and any(
                        presence is True and file_stat is not None
                        for presence, file_stat in candidate_probes
                    ):
                        preserved_transcript_path = str(candidate)

        def recovery_cleanup_failure(
            failed_state: RecordingState,
            *,
            recovery_reason: str | None = None,
        ) -> dict[str, object]:
            error_text = "failed to discard recording artifacts"
            try:
                store.update(
                    status="error",
                    pid=None,
                    process_identity="",
                    transcript_path=preserved_transcript_path,
                    cleanup_backup_journal_overflow=(
                        failed_state.cleanup_backup_journal_overflow
                    ),
                    error=(preserved_output_recovery_error or error_text),
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                pass
            return {
                "status": "error",
                "message": error_text,
                "error": error_text,
                "audio_deleted": False,
                "log_deleted": False,
                "inflight_artifacts_deleted": False,
                "cleanup_backups_deleted": False,
                "transcript_deleted": False,
                **(
                    {"recovery_reason": recovery_reason}
                    if recovery_reason is not None
                    else {}
                ),
            }
        if state.status == "recording":
            if state.pid is None:
                error_text = "recording process pid is missing; recording state preserved"
                store.update(status="recording", error=error_text, inserted=False)
                return {"status": "recording", "message": error_text, "error": error_text}
            process_error = _reconcile_recording_process(state)
            if process_error is not None:
                if process_error == _RECORDING_PROCESS_GROUP_ACTIVE_ERROR:
                    return {
                        "status": "recording",
                        "message": process_error,
                        "error": process_error,
                    }
                store.update(status="recording", error=process_error, inserted=False)
                return {"status": "recording", "message": process_error, "error": process_error}
            state = store.update(
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
            )
        elif state.pid is not None:
            process_error = _reconcile_recording_process(state)
            if process_error is not None:
                if process_error == _RECORDING_PROCESS_GROUP_ACTIVE_ERROR:
                    return {
                        "status": state.status,
                        "message": process_error,
                        "error": process_error,
                    }
                store.update(
                    status=state.status,
                    error=(preserved_transcript_recovery_error or process_error),
                    inserted=state.inserted,
                )
                return {"status": state.status, "message": process_error, "error": process_error}
            state = store.update(
                pid=None,
                process_identity="",
                stopped_at=state.stopped_at or now_iso(),
            )
        elif state.pid is None and state.process_identity is not None and state.process_identity != "":
            error_text = "recording process identity is incomplete or invalid; recording state preserved"
            store.update(
                status=state.status,
                error=(preserved_transcript_recovery_error or error_text),
                inserted=state.inserted,
            )
            return {"status": state.status, "message": error_text, "error": error_text}
        try:
            persisted_cleanup_owner_paths = {
                _normalized_cleanup_backup_owner_path(
                    path_value,
                    state_path=store.path,
                )
                for path_value in state.pending_cleanup_owner_paths
            }
            recovery_audio_was_bound = recovery_audio_was_bound or any(
                _is_recording_artifact(path)
                for path in persisted_cleanup_owner_paths
            )
            pending_restore_pairs = list(
                _cleanup_backup_restore_journal_pairs(
                    state,
                    state_path=store.path,
                    state_namespace=(
                        current_cleanup_state_namespace()
                        if state.pending_cleanup_restore_owner_paths
                        else None
                    ),
                )
            )
        except RuntimeError as exc:
            error_text = f"{exc}; recording state preserved"
            return {
                "status": state.status,
                "message": error_text,
                "error": error_text,
            }
        resumed_recovery_inflight_paths = {
            path
            for path in persisted_cleanup_owner_paths
            if _is_inflight_recording_artifact(path)
        }
        recovery_cleanup_inflight_artifacts_absent = True
        if pending_restore_pairs:
            restore_directory_entries_by_parent: dict[
                Path,
                list[tuple[Path, os.stat_result]],
            ] = {}
            prepared_migrations: list[
                tuple[
                    Path,
                    Path | None,
                    Path,
                    tuple[int, int, int, int, int, int, int],
                    str,
                    os.stat_result,
                ]
            ] = []
            paired_owner_paths = {
                owner_path for owner_path, _entry in pending_restore_pairs
            }
            try:
                for owner_path, entry in pending_restore_pairs:
                    backup_name, expected_identity = (
                        _parse_cleanup_backup_journal_entry(entry)
                    )
                    backup_path = owner_path.with_name(backup_name)
                    owner_prefix = v2_cleanup_prefixes.get(owner_path)
                    if owner_prefix is None:
                        owner_prefix = (
                            _cleanup_backup_owner_prefix_from_name(backup_name)
                        )
                        if owner_prefix is None:
                            raise RuntimeError(
                                "cleanup restore owner prefix is invalid"
                            )
                        v2_cleanup_prefixes[owner_path] = owner_prefix
                    entries = restore_directory_entries_by_parent.get(
                        owner_path.parent
                    )
                    if entries is None:
                        entries = _safe_directory_entries(
                            owner_path.parent,
                            field_name="recording cleanup restore directory",
                        )
                        restore_directory_entries_by_parent[
                            owner_path.parent
                        ] = entries
                    if any(
                        candidate.name.startswith(owner_prefix)
                        and candidate.name != backup_name
                        for candidate, _file_stat in entries
                    ):
                        raise RuntimeError(
                            "cleanup restore delete state is ambiguous"
                        )
                    backup_presence = _safe_leaf_presence(backup_path)
                    owner_presence = _safe_leaf_presence(owner_path)
                    if backup_presence is None or owner_presence is None:
                        raise RuntimeError(
                            "cleanup restore artifact presence is unsafe"
                        )
                    if backup_presence and owner_presence:
                        raise RuntimeError(
                            "cleanup restore delete state is ambiguous"
                        )
                    if owner_presence:
                        return recovery_cleanup_failure(
                            state,
                            recovery_reason=(
                                "legacy-v2-post-rename-unverified"
                            ),
                        )
                    migration_nonce = migration_claim_nonce(backup_name)
                    matching_claims = tuple(
                        candidate
                        for candidate, _file_stat in entries
                        if (
                            (
                                parts := _bound_cleanup_v3_tombstone_parts(
                                    candidate.name
                                )
                            )
                            is not None
                            and candidate.name.startswith(
                                current_v3_cleanup_prefix(owner_path)
                            )
                            and parts[3] == migration_nonce
                        )
                    )
                    if len(matching_claims) > 1:
                        raise RuntimeError(
                            "cleanup restore migration is ambiguous"
                        )
                    if backup_presence:
                        if matching_claims:
                            raise RuntimeError(
                                "cleanup restore migration is ambiguous"
                            )
                        backup_snapshot = _cleanup_claim_content_snapshot(
                            backup_path,
                            expected_identity,
                            identity_fields=7,
                        )
                        if backup_snapshot is None:
                            raise RuntimeError(
                                "cleanup restore backup identity changed"
                            )
                        content_digest, migration_stat = backup_snapshot
                        claim_path = transaction_claim_path(
                            owner_path,
                            content_digest,
                            migration_nonce,
                        )
                        migration_source: Path | None = backup_path
                    elif matching_claims:
                        claim_path = matching_claims[0]
                        claim_parts = _bound_cleanup_v3_tombstone_parts(
                            claim_path.name
                        )
                        if claim_parts is None:
                            raise RuntimeError(
                                "cleanup restore migration is invalid"
                            )
                        content_digest = claim_parts[2]
                        claim_snapshot = _cleanup_claim_content_snapshot(
                            claim_path,
                            expected_identity,
                            identity_fields=6,
                        )
                        if (
                            claim_snapshot is None
                            or not secrets.compare_digest(
                                claim_snapshot[0],
                                content_digest,
                            )
                        ):
                            raise RuntimeError(
                                "cleanup restore migration changed"
                            )
                        migration_stat = claim_snapshot[1]
                        migration_source = None
                    else:
                        if not fsync_transaction_parent(owner_path):
                            raise RuntimeError(
                                "cleanup restore parent sync failed"
                            )
                        continue
                    prepared_migrations.append(
                        (
                            owner_path,
                            migration_source,
                            claim_path,
                            expected_identity,
                            content_digest,
                            migration_stat,
                        )
                    )
                prospective_state = replace(
                    state,
                    status="finalizing",
                    pid=None,
                    process_identity="",
                    audio_path=(
                        ""
                        if state.audio_path
                        in {str(path) for path in paired_owner_paths}
                        else state.audio_path
                    ),
                    log_path=(
                        ""
                        if state.log_path
                        in {str(path) for path in paired_owner_paths}
                        else state.log_path
                    ),
                    transcript_path=preserved_transcript_path,
                    pending_cleanup_owner_paths=tuple(
                        str(owner_path)
                        for (
                            owner_path,
                            _source_path,
                            _claim_path,
                            _identity,
                            _digest,
                            _migration_stat,
                        ) in prepared_migrations
                    ),
                    pending_cleanup_restore_owner_paths=(),
                    pending_cleanup_backup_entries=tuple(
                        _cleanup_backup_journal_entry(
                            transaction_journal_path(
                                owner_path,
                                claim_path,
                                "claimed",
                            ),
                            migration_stat,
                        )
                        for (
                            owner_path,
                            _source_path,
                            claim_path,
                            _identity,
                            _digest,
                            migration_stat,
                        ) in prepared_migrations
                    ),
                    cleanup_backup_journal_overflow=False,
                    cleanup_backup_journal_restore=False,
                    error=(preserved_transcript_recovery_error or ""),
                )
                if (
                    prepared_migrations
                    and not transaction_state_would_fit(prospective_state)
                ):
                    return recovery_cleanup_failure(state)
                migrated_claims: list[tuple[Path, str]] = []
                for (
                    owner_path,
                    migration_source,
                    claim_path,
                    expected_identity,
                    content_digest,
                    _migration_stat,
                ) in prepared_migrations:
                    if migration_source is None:
                        claim_snapshot = _cleanup_claim_content_snapshot(
                            claim_path,
                            expected_identity,
                            identity_fields=6,
                        )
                        if (
                            claim_snapshot is None
                            or not secrets.compare_digest(
                                claim_snapshot[0],
                                content_digest,
                            )
                            or not fsync_transaction_parent(claim_path)
                        ):
                            raise RuntimeError(
                                "cleanup restore migration changed"
                            )
                        migrated_stat = claim_snapshot[1]
                    else:
                        migrated_stat = rename_transaction_phase(
                            migration_source,
                            claim_path,
                            expected_identity,
                            content_digest,
                            source_identity_fields=7,
                        )
                        if migrated_stat is None:
                            raise RuntimeError(
                                "cleanup restore migration failed"
                            )
                    migrated_claims.append(
                        (
                            owner_path,
                            _cleanup_backup_journal_entry(
                                transaction_journal_path(
                                    owner_path,
                                    claim_path,
                                    "claimed",
                                ),
                                migrated_stat,
                            ),
                        )
                    )
                state = store.update(
                    status="finalizing",
                    pid=None,
                    process_identity="",
                    audio_path=prospective_state.audio_path,
                    log_path=prospective_state.log_path,
                    transcript_path=preserved_transcript_path,
                    pending_cleanup_owner_paths=tuple(
                        str(owner_path)
                        for owner_path, _entry in migrated_claims
                    ),
                    pending_cleanup_restore_owner_paths=(),
                    pending_cleanup_backup_entries=tuple(
                        entry for _owner_path, entry in migrated_claims
                    ),
                    cleanup_backup_journal_overflow=False,
                    cleanup_backup_journal_restore=False,
                    error=(preserved_transcript_recovery_error or ""),
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except (
                DirectoryScanError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                return recovery_cleanup_failure(state)
        resumed_recovery_cleanup_completed = False
        recovery_cleanup_artifacts_absent = True
        legacy_cleanup_journal_active = False
        transaction_result = None
        if (
            state.pending_cleanup_owner_paths
            or state.pending_cleanup_backup_entries
        ):
            try:
                migrated_state = migrate_tokenless_v3_transaction(state)
                if migrated_state is None:
                    return recovery_cleanup_failure(state)
                state = migrated_state
                transaction_result = process_recovery_cleanup_transaction(
                    state
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                return recovery_cleanup_failure(state)
        if transaction_result is not None:
            try:
                (
                    state,
                    transaction_complete,
                    recovery_cleanup_artifacts_absent,
                ) = transaction_result
            except (KeyboardInterrupt, SystemExit):
                raise
            if not transaction_complete:
                return recovery_cleanup_failure(state)
            resumed_recovery_cleanup_completed = bool(
                state.pending_cleanup_owner_paths
                == state.pending_cleanup_backup_entries
                == ()
            )
            recovery_cleanup_inflight_artifacts_absent = all(
                _safe_leaf_presence(path) is False
                for path in resumed_recovery_inflight_paths
            )
            persisted_cleanup_owner_paths = {
                _normalized_cleanup_backup_owner_path(
                    path_value,
                    state_path=store.path,
                )
                for path_value in state.pending_cleanup_owner_paths
            }
        elif state.pending_cleanup_backup_entries:
            legacy_cleanup_journal_active = legacy_cleanup_journal_is_valid(
                state
            )
            if not legacy_cleanup_journal_active:
                return recovery_cleanup_failure(state)
        discarded_audio_path = _normalized_state_recording_artifact_path(
            state.audio_path,
            suffix=(".wav", ".flac", ".socenc"),
            state_path=store.path,
        )
        discarded_log_path = _normalized_state_recording_artifact_path(
            state.log_path,
            suffix=".log",
            state_path=store.path,
        )
        discarded_finalizing_paths = (
            _finalizing_inflight_artifact_paths(
                store.path,
                state,
                known_audio_path=discarded_audio_path,
                include_recording_inflight=(
                    discarded_audio_path is None
                ),
            )
        )
        discarded_finalizing_transcript_paths = {
            path for path in discarded_finalizing_paths if _is_transcript_artifact(path)
        }
        discarded_finalizing_transcript_cleanup_paths = (
            set()
            if preserve_transcript_after_insert_failure
            else discarded_finalizing_transcript_paths
        )
        discarded_inflight_paths = (
            (
                _inflight_recording_artifact_paths(discarded_audio_path)
                if discarded_audio_path is not None
                else set()
            )
            | {
                path
                for path in discarded_finalizing_paths
                if _is_recording_artifact(path)
            }
        )
        reported_inflight_paths = (
            discarded_inflight_paths | resumed_recovery_inflight_paths
        )
        discarded_inflight_groups: dict[Path, set[Path]] = {}
        for inflight_path in discarded_inflight_paths:
            group_paths = {inflight_path}
            sibling_path = _recording_sibling_path(inflight_path)
            if sibling_path is not None:
                group_paths.add(sibling_path)
            group_path = min(group_paths, key=lambda path: str(path))
            discarded_inflight_groups.setdefault(group_path, set()).update(group_paths)
        selected_inflight_groups: dict[Path, set[Path]] = {}
        direct_cleanup_owner_paths = {
            path
            for path in (discarded_audio_path, discarded_log_path)
            if path is not None
        }
        reserved_cleanup_owner_paths = (
            persisted_cleanup_owner_paths | direct_cleanup_owner_paths
        )
        deferred_inflight_groups: dict[Path, set[Path]] = {}
        deferred_inflight_group_paths: set[Path] = set()
        for group_path, group_paths in sorted(
            discarded_inflight_groups.items(),
            key=lambda item: str(item[0]),
        ):
            new_owner_paths = group_paths - reserved_cleanup_owner_paths
            if (
                len(reserved_cleanup_owner_paths) + len(new_owner_paths)
                > MAX_PENDING_CLEANUP_OWNER_PATHS
            ):
                deferred_inflight_groups[group_path] = group_paths
                deferred_inflight_group_paths.add(group_path)
                continue
            selected_inflight_groups[group_path] = group_paths
            reserved_cleanup_owner_paths.update(new_owner_paths)
        discarded_inflight_groups = selected_inflight_groups

        inflight_group_owner_paths = {
            owner_path
            for group_paths in discarded_inflight_groups.values()
            for owner_path in group_paths
        }
        cleanup_journal_owner_paths = (
            inflight_group_owner_paths | persisted_cleanup_owner_paths
        )
        if discarded_audio_path is not None:
            cleanup_journal_owner_paths.add(discarded_audio_path)
        if discarded_log_path is not None:
            cleanup_journal_owner_paths.add(discarded_log_path)

        safe_cleanup_backups_by_owner: dict[
            Path,
            dict[Path, os.stat_result],
        ] = {
            owner_path: {} for owner_path in cleanup_journal_owner_paths
        }
        unsafe_cleanup_backup_owner_paths: set[Path] = set()
        cleanup_owner_paths_by_parent_and_prefix: dict[
            Path,
            dict[str, set[Path]],
        ] = {}
        for owner_path in cleanup_journal_owner_paths:
            owner_prefixes = cleanup_owner_paths_by_parent_and_prefix.setdefault(
                owner_path.parent,
                {},
            )
            for owner_prefix in (
                current_legacy_cleanup_prefix(owner_path),
                current_v2_cleanup_prefix(owner_path),
            ):
                owner_prefixes.setdefault(
                    owner_prefix,
                    set(),
                ).add(owner_path)

        cleanup_directory_entries_by_parent: dict[
            Path,
            list[tuple[Path, os.stat_result]],
        ] = {}
        matched_cleanup_backup_candidate_paths: set[Path] = set()
        for parent_path, owner_paths_by_prefix in (
            cleanup_owner_paths_by_parent_and_prefix.items()
        ):
            try:
                entries = _safe_directory_entries(
                    parent_path,
                    field_name="recording cleanup directory",
                )
            except DirectoryScanError as exc:
                raise RuntimeError(
                    "failed to scan recording cleanup directory: "
                    f"{parent_path}"
                ) from exc
            cleanup_directory_entries_by_parent[parent_path] = entries
            for candidate, file_stat in entries:
                if not candidate.name.endswith(".bak"):
                    continue
                candidate_owner_prefix = (
                    _cleanup_backup_owner_prefix_from_name(
                        candidate.name
                    )
                )
                matching_owner_paths = owner_paths_by_prefix.get(
                    candidate_owner_prefix,
                )
                if not matching_owner_paths:
                    continue
                matched_cleanup_backup_candidate_paths.add(candidate)
                if not _is_cleanup_backup_journal_basename(
                    candidate.name
                ):
                    unsafe_cleanup_backup_owner_paths.update(
                        matching_owner_paths
                    )
                    continue
                if (
                    stat_module.S_ISREG(file_stat.st_mode)
                    and getattr(file_stat, "st_nlink", 1) == 1
                ):
                    for owner_path in matching_owner_paths:
                        safe_cleanup_backups_by_owner[owner_path][
                            candidate
                        ] = file_stat
                else:
                    unsafe_cleanup_backup_owner_paths.update(
                        matching_owner_paths
                    )

        deferred_cleanup_backup_candidate_paths: set[Path] = set()
        deferred_owner_prefixes_by_parent: dict[Path, set[str]] = {}
        for group_paths in deferred_inflight_groups.values():
            for owner_path in group_paths:
                owner_prefixes = deferred_owner_prefixes_by_parent.setdefault(
                    owner_path.parent,
                    set(),
                )
                owner_prefixes.add(current_legacy_cleanup_prefix(owner_path))
                owner_prefixes.add(current_v2_cleanup_prefix(owner_path))
        for parent_path, owner_prefixes in deferred_owner_prefixes_by_parent.items():
            entries = cleanup_directory_entries_by_parent.get(parent_path)
            if entries is None:
                try:
                    entries = _safe_directory_entries(
                        parent_path,
                        field_name="recording cleanup directory",
                    )
                except DirectoryScanError as exc:
                    raise RuntimeError(
                        "failed to scan recording cleanup directory: "
                        f"{parent_path}"
                    ) from exc
                cleanup_directory_entries_by_parent[parent_path] = entries
            for candidate, _file_stat in entries:
                if (
                    candidate.name.endswith(".bak")
                    and _cleanup_backup_owner_prefix_from_name(candidate.name)
                    in owner_prefixes
                ):
                    deferred_cleanup_backup_candidate_paths.add(candidate)

        discarded_audio_cleanup_backups = (
            safe_cleanup_backups_by_owner.get(discarded_audio_path, {})
            if discarded_audio_path is not None
            else {}
        )
        discarded_audio_cleanup_backup_unsafe = (
            discarded_audio_path in unsafe_cleanup_backup_owner_paths
            if discarded_audio_path is not None
            else False
        )
        discarded_log_cleanup_backups = (
            safe_cleanup_backups_by_owner.get(discarded_log_path, {})
            if discarded_log_path is not None
            else {}
        )
        discarded_log_cleanup_backup_unsafe = (
            discarded_log_path in unsafe_cleanup_backup_owner_paths
            if discarded_log_path is not None
            else False
        )
        direct_cleanup_retry_owner_paths = {
            owner_path
            for owner_path, cleanup_backups, cleanup_backup_unsafe in (
                (
                    discarded_audio_path,
                    discarded_audio_cleanup_backups,
                    discarded_audio_cleanup_backup_unsafe,
                ),
                (
                    discarded_log_path,
                    discarded_log_cleanup_backups,
                    discarded_log_cleanup_backup_unsafe,
                ),
            )
            if owner_path is not None
            and (
                cleanup_backup_unsafe
                or any(
                    not cleanup_backup.name.startswith(
                        current_v2_cleanup_prefix(owner_path)
                    )
                    for cleanup_backup in cleanup_backups
                )
            )
        }
        discarded_cleanup_backups = (
            discarded_audio_cleanup_backups | discarded_log_cleanup_backups
        )
        discarded_cleanup_backup_candidates_present = bool(
            discarded_cleanup_backups
        ) or (
            discarded_audio_cleanup_backup_unsafe
            or discarded_log_cleanup_backup_unsafe
        )
        cleanup_backup_owner_paths = (
            inflight_group_owner_paths | persisted_cleanup_owner_paths
        )
        discarded_inflight_cleanup_backup_results_by_owner = {
            owner_path: (
                safe_cleanup_backups_by_owner.get(owner_path, {}),
                owner_path in unsafe_cleanup_backup_owner_paths,
            )
            for owner_path in cleanup_backup_owner_paths
        }
        discarded_inflight_cleanup_backups_by_owner = {
            owner_path: result[0]
            for owner_path, result in (
                discarded_inflight_cleanup_backup_results_by_owner.items()
            )
        }
        selected_unsafe_cleanup_backup_owner_paths = {
            owner_path
            for owner_path, result in (
                discarded_inflight_cleanup_backup_results_by_owner.items()
            )
            if result[1]
        }
        recordings_root = recordings_dir().resolve(strict=False)
        if recordings_root not in cleanup_directory_entries_by_parent:
            try:
                cleanup_directory_entries_by_parent[recordings_root] = (
                    _safe_directory_entries(
                        recordings_root,
                        field_name="recording cleanup directory",
                    )
                )
            except DirectoryScanError as exc:
                raise RuntimeError(
                    "failed to scan recording cleanup directory: "
                    f"{recordings_root}"
                ) from exc
        recordings_entries_by_name = {
            candidate.name: (candidate, file_stat)
            for candidate, file_stat in (
                cleanup_directory_entries_by_parent.get(
                    recordings_root,
                    [],
                )
            )
        }
        allowed_v3_claim_paths: set[Path] = set()
        validated_v3_groups = recovery_cleanup_transaction_groups(state)
        if validated_v3_groups:
            allowed_v3_claim_paths.update(
                transaction_claim_path(owner_path, digest, nonce)
                for (
                    owner_path,
                    _entry,
                    _phase,
                    digest,
                    nonce,
                    _identity,
                ) in validated_v3_groups
            )
        unjournaled_v3_status = False
        if not state.cleanup_backup_journal_overflow:
            unjournaled_v3_status = _same_state_v3_cleanup_claims_present(
                store.path,
                allowed_paths=frozenset(allowed_v3_claim_paths),
            )
        state_owned_unjournaled_v3 = unjournaled_v3_status is not False
        if state_owned_unjournaled_v3:
            error_message = "recording cleanup contains an unresolved state claim"
            try:
                state = store.update(
                    status="error",
                    error=(preserved_output_recovery_error or error_message),
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except (OSError, RuntimeError, ValueError):
                pass
            payload: dict[str, object] = {
                "status": "error",
                "message": error_message,
                "error": error_message,
                "discarded_audio_path_present": bool(state.audio_path),
                "audio_deleted": False,
                "log_deleted": False,
                "inflight_artifact_count": len(reported_inflight_paths),
                "inflight_artifacts_deleted": False,
                "cleanup_backups_deleted": False,
                "transcript_deleted": False,
            }
            return payload
        cleanup_journal_entries_by_name: dict[str, str] = {}
        cleanup_journal_paths_by_name: dict[str, Path] = {}
        cleanup_journal_identity_failed_names: set[str] = set()
        cleanup_journal_entry_owner_paths_by_name: dict[str, set[Path]] = {}
        for persisted_entry in state.pending_cleanup_backup_entries:
            if not _cleanup_backup_journal_entry_belongs_to_state(
                persisted_entry,
                store.path,
                persisted_cleanup_owner_paths,
                legacy_owner_prefixes=(
                    current_persisted_legacy_owner_prefixes(
                        persisted_cleanup_owner_paths
                    )
                    if not persisted_entry.startswith(".cleanup.v2.")
                    else None
                ),
                state_namespace=(
                    current_cleanup_state_namespace()
                    if persisted_entry.startswith(".cleanup.v2.")
                    else None
                ),
            ):
                error_text = (
                    "cleanup backup journal entry belongs to another state"
                )
                store.update(
                    status="error",
                    error=(
                        preserved_output_recovery_error
                        or error_text
                    ),
                    inserted=state.inserted,
                )
                return {
                    "status": "error",
                    "message": error_text,
                    "error": error_text,
                }
            backup_name, expected_identity = (
                _parse_cleanup_backup_journal_entry(persisted_entry)
            )
            current_entry = recordings_entries_by_name.get(backup_name)
            if current_entry is None:
                continue
            cleanup_backup, cleanup_backup_stat = current_entry
            cleanup_journal_entries_by_name[backup_name] = persisted_entry
            cleanup_journal_paths_by_name[backup_name] = cleanup_backup
            if not _cleanup_backup_journal_identity_matches(
                cleanup_backup_stat,
                expected_identity,
            ):
                cleanup_journal_identity_failed_names.add(backup_name)
        for owner_path in cleanup_journal_owner_paths:
            for cleanup_backup, cleanup_backup_stat in (
                safe_cleanup_backups_by_owner.get(owner_path, {}).items()
            ):
                if not _is_cleanup_backup_journal_basename(
                    cleanup_backup.name
                ):
                    continue
                if cleanup_backup.name in cleanup_journal_entries_by_name:
                    continue
                cleanup_journal_entries_by_name[cleanup_backup.name] = (
                    _cleanup_backup_journal_entry(
                        cleanup_backup,
                        cleanup_backup_stat,
                    )
                )
                cleanup_journal_paths_by_name[
                    cleanup_backup.name
                ] = cleanup_backup
                if not cleanup_backup.name.startswith(".cleanup.v2."):
                    candidate_owner_prefix = (
                        _cleanup_backup_owner_prefix_from_name(
                            cleanup_backup.name
                        )
                    )
                    cleanup_journal_entry_owner_paths_by_name[
                        cleanup_backup.name
                    ] = set(
                        cleanup_owner_paths_by_parent_and_prefix.get(
                            owner_path.parent,
                            {},
                        ).get(candidate_owner_prefix, {owner_path})
                    )
        for owner_path in unsafe_cleanup_backup_owner_paths:
            prefix = current_legacy_cleanup_prefix(owner_path)
            for candidate, file_stat in (
                cleanup_directory_entries_by_parent.get(
                    owner_path.parent,
                    [],
                )
            ):
                if (
                    not candidate.name.startswith(prefix)
                    or not _is_cleanup_backup_journal_basename(
                        candidate.name
                    )
                    or candidate.name in cleanup_journal_entries_by_name
                ):
                    continue
                cleanup_journal_entries_by_name[candidate.name] = (
                    _cleanup_backup_journal_entry(candidate, file_stat)
                )
                cleanup_journal_paths_by_name[
                    candidate.name
                ] = candidate
                cleanup_journal_entry_owner_paths_by_name.setdefault(
                    candidate.name,
                    set(),
                ).add(owner_path)
                cleanup_journal_identity_failed_names.add(candidate.name)
        sorted_cleanup_journal_names = tuple(
            sorted(cleanup_journal_entries_by_name)
        )
        pending_cleanup_backup_entries_before_delete = tuple(
            cleanup_journal_entries_by_name[name]
            for name in sorted_cleanup_journal_names
        )
        pending_cleanup_owner_paths_before_delete = (
            set(persisted_cleanup_owner_paths)
            | {
                owner_path
                for entry_name, owner_paths in (
                    cleanup_journal_entry_owner_paths_by_name.items()
                )
                if entry_name in cleanup_journal_entries_by_name
                for owner_path in owner_paths
            }
        )
        cleanup_journal_owner_capacity_paths = (
            pending_cleanup_owner_paths_before_delete
            | direct_cleanup_retry_owner_paths
        )
        cleanup_backup_journal_overflow = (
            state.cleanup_backup_journal_overflow
            or state_owned_unjournaled_v3
            or (
                len(cleanup_journal_owner_capacity_paths)
                > MAX_PENDING_CLEANUP_OWNER_PATHS
            )
            or bool(deferred_cleanup_backup_candidate_paths)
            or len(matched_cleanup_backup_candidate_paths)
            > MAX_PENDING_CLEANUP_BACKUP_ENTRIES
            or len(pending_cleanup_backup_entries_before_delete)
            > MAX_PENDING_CLEANUP_BACKUP_ENTRIES
        )
        def snapshot_recording_artifact_paths(path: Path | None) -> dict[Path, os.stat_result | None]:
            if path is None:
                return {}
            candidates = [path]
            sibling_path = _recording_sibling_path(path)
            if sibling_path is not None:
                candidates.append(sibling_path)
            return {candidate: _recording_artifact_stat(candidate) for candidate in candidates}

        discarded_audio_expected_stats = snapshot_recording_artifact_paths(discarded_audio_path)
        discarded_audio_present_before = False
        discarded_audio_present_before = any(
            file_stat is not None for file_stat in discarded_audio_expected_stats.values()
        )
        discarded_log_stat = _recording_artifact_stat(discarded_log_path) if discarded_log_path else None
        discarded_log_present_before = discarded_log_stat is not None
        discarded_inflight_expected_stats = {
            group_path: snapshot_recording_artifact_paths(group_path)
            for group_path in discarded_inflight_groups
        }
        recovery_cleanup_claims: tuple[tuple[Path, str], ...] = ()
        recovery_cleanup_authorized = True
        if preserve_transcript_after_insert_failure:
            recovery_expected_stats = dict(
                discarded_audio_expected_stats
            )
            if discarded_log_path is not None:
                recovery_expected_stats[discarded_log_path] = (
                    discarded_log_stat
                )
            for group_stats in discarded_inflight_expected_stats.values():
                recovery_expected_stats.update(group_stats)
            if (
                (state.audio_path and discarded_audio_path is None)
                or (state.log_path and discarded_log_path is None)
                or (
                    (
                        pending_cleanup_owner_paths_before_delete
                        or pending_cleanup_backup_entries_before_delete
                    )
                    and not legacy_cleanup_journal_active
                )
                or deferred_inflight_group_paths
            ):
                recovery_cleanup_authorized = False
            else:
                candidate_claims = cleanup_identity_claims(
                    recovery_expected_stats
                )
                if candidate_claims is None:
                    recovery_cleanup_authorized = False
                else:
                    recovery_cleanup_claims = candidate_claims
        if (
            preserve_transcript_after_insert_failure
            and not recovery_cleanup_authorized
        ):
            return recovery_cleanup_failure(state)

        cleanup_transaction_owner_paths = tuple(
            str(path) for path, _entry in recovery_cleanup_claims
        )
        cleanup_transaction_entries = tuple(
            entry for _path, entry in recovery_cleanup_claims
        )
        has_artifacts = bool(
            state.audio_path
            or state.log_path
            or state.transcript_path
            or state.transcript
            or persisted_cleanup_owner_paths
            or state.pending_cleanup_backup_entries
            or state.cleanup_backup_journal_overflow
            or state_owned_unjournaled_v3
        )
        has_recording_state = state.status in {
            "recording",
            "recorded",
            "processing",
            "finalizing",
            "done",
            "error",
        }

        def cleanup_capacity_failure(
            blocked_state: RecordingState,
        ) -> dict[str, object]:
            if not blocked_state.cleanup_backup_journal_overflow:
                blocker_state = replace(
                    blocked_state,
                    cleanup_backup_journal_overflow=True,
                )
                if StateStore.write_would_fit(blocker_state):
                    try:
                        store.write(blocker_state)
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except (OSError, RuntimeError, TypeError, ValueError):
                        pass
            error_text = "recording cleanup journal capacity exceeded"
            return {
                "status": "error",
                "message": error_text,
                "error": error_text,
                "discarded_audio_path_present": bool(
                    blocked_state.audio_path
                ),
                "audio_deleted": False,
                "log_deleted": False,
                "inflight_artifact_count": len(reported_inflight_paths),
                "inflight_artifacts_deleted": False,
                "cleanup_backups_deleted": False,
                "transcript_deleted": False,
            }

        if not has_artifacts and not has_recording_state:
            store.write(
                RecordingState(
                    status="idle",
                    audio_path="",
                    log_path="",
                    transcript_path="",
                    pending_cleanup_owner_paths=(),
                    pending_cleanup_backup_entries=(),
                    cleanup_backup_journal_overflow=False,
                    stopped_at=now_iso(),
                    language=state.language,
                    recorder=state.recorder,
                    input_device=state.input_device,
                    max_seconds=state.max_seconds,
                    error=(
                        preserved_transcript_recovery_error
                        if (
                            preserve_transcript_after_insert_failure
                            and bool(state.transcript)
                        )
                        else ""
                    ),
                )
            )
            return {
                "status": "idle",
                "message": "nothing to cancel",
                "discarded_audio_path_present": False,
                "audio_deleted": True,
                "log_deleted": True,
                "inflight_artifact_count": 0,
                "inflight_artifacts_deleted": True,
                "cleanup_backups_deleted": True,
                "transcript_deleted": True,
            }

        error_message = "discarding recording artifacts"
        if cleanup_backup_journal_overflow:
            return cleanup_capacity_failure(state)
        recovery_cleanup_completed = resumed_recovery_cleanup_completed
        cleanup_transaction_state = RecordingState(
            status="finalizing",
            audio_path=state.audio_path,
            log_path=state.log_path,
            transcript=state.transcript,
            transcript_path=preserved_transcript_path,
            pending_cleanup_owner_paths=tuple(
                cleanup_transaction_owner_paths
                if recovery_cleanup_claims
                else tuple(
                    str(path)
                    for path in sorted(
                        pending_cleanup_owner_paths_before_delete,
                        key=lambda candidate: str(candidate),
                    )
                )
            ),
            pending_cleanup_restore_owner_paths=(),
            pending_cleanup_backup_entries=(
                cleanup_transaction_entries
                if recovery_cleanup_claims
                else pending_cleanup_backup_entries_before_delete
            ),
            cleanup_backup_journal_overflow=False,
            cleanup_backup_journal_restore=False,
            inserted=state.inserted,
            stopped_at=now_iso(),
            language=state.language,
            recorder=state.recorder,
            input_device=state.input_device,
            max_seconds=state.max_seconds,
            error=(preserved_transcript_recovery_error or ""),
        )
        if (
            not StateStore.write_would_fit(cleanup_transaction_state)
            or (
                recovery_cleanup_claims
                and not transaction_state_would_fit(
                    cleanup_transaction_state
                )
            )
        ):
            return cleanup_capacity_failure(state)
        try:
            store.write(cleanup_transaction_state)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            return recovery_cleanup_failure(state)
        if recovery_cleanup_claims:
            try:
                transaction_result = process_recovery_cleanup_transaction(
                    store.read()
                )
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception:
                return recovery_cleanup_failure(store.read())
            if transaction_result is None:
                return recovery_cleanup_failure(store.read())
            (
                state,
                recovery_cleanup_completed,
                recovery_cleanup_artifacts_absent,
            ) = transaction_result
            if not recovery_cleanup_completed:
                return recovery_cleanup_failure(state)
            discarded_inflight_groups = {}
            deferred_inflight_group_paths = set()
        elif (
            preserve_transcript_after_insert_failure
            and recovery_cleanup_authorized
            and not legacy_cleanup_journal_active
            and not recovery_cleanup_completed
        ):
            state = store.update(
                status="finalizing",
                audio_path="",
                log_path="",
                transcript_path=preserved_transcript_path,
                cleanup_backup_journal_overflow=False,
                error=(preserved_transcript_recovery_error or ""),
            )
            recovery_cleanup_completed = True
            recovery_cleanup_artifacts_absent = True
            discarded_inflight_groups = {}
            deferred_inflight_group_paths = set()

        audio_deleted = (
            (
                recovery_cleanup_artifacts_absent
                and (
                    not preserve_transcript_after_insert_failure
                    or recovery_audio_was_bound
                )
            )
            if recovery_cleanup_completed
            else (
                _remove_recording_artifact(
                    str(discarded_audio_path),
                    expected_stats=discarded_audio_expected_stats,
                )
                if discarded_audio_path
                and (
                    not preserve_transcript_after_insert_failure
                    or recovery_cleanup_authorized
                )
                else not bool(state.audio_path)
            )
        )
        log_deleted = (
            recovery_cleanup_artifacts_absent
            if recovery_cleanup_completed
            else (
                remove_file(str(discarded_log_path), suffix=".log", expected_stat=discarded_log_stat)
                if discarded_log_path and discarded_log_present_before
                else not bool(state.log_path)
            )
        )
        if not audio_deleted and discarded_audio_path and not discarded_audio_present_before:
            if Path(str(discarded_audio_path)).name.lower().endswith(ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES):
                audio_deleted = _recording_artifact_missing_but_safe(
                    str(discarded_audio_path),
                    suffix=".socenc",
                    state_path=store.path,
                )
            else:
                audio_deleted = _recording_artifact_missing_but_safe(
                    str(discarded_audio_path),
                    suffix=(".wav", ".flac"),
                    state_path=store.path,
                )
        if not log_deleted and discarded_log_path and not discarded_log_present_before:
            log_deleted = _recording_artifact_missing_but_safe(str(discarded_log_path), suffix=".log", state_path=store.path)
        known_cleanup_backups = {
            cleanup_backup: cleanup_backup_stat
            for owner_backups in (
                discarded_inflight_cleanup_backups_by_owner.values()
            )
            for cleanup_backup, cleanup_backup_stat in owner_backups.items()
        } | discarded_cleanup_backups
        known_cleanup_backups = {
            cleanup_backup: cleanup_backup_stat
            for cleanup_backup, cleanup_backup_stat in (
                known_cleanup_backups.items()
            )
            if cleanup_backup.name
            not in cleanup_journal_identity_failed_names
        }
        for backup_name, cleanup_backup in (
            cleanup_journal_paths_by_name.items()
        ):
            if backup_name in cleanup_journal_identity_failed_names:
                continue
            current_entry = recordings_entries_by_name.get(backup_name)
            if current_entry is not None:
                known_cleanup_backups[cleanup_backup] = current_entry[1]
        cleanup_backup_delete_results: dict[Path, bool] = {}
        for cleanup_backup, cleanup_backup_stat in sorted(
            known_cleanup_backups.items(),
            key=lambda item: str(item[0]),
        ):
            try:
                deleted = _unlink_regular_leaf_with_parent_fsync(
                    cleanup_backup,
                    field_name="recording cleanup backup",
                    expected_stat=cleanup_backup_stat,
                )
            except RuntimeError:
                deleted = False
            cleanup_backup_delete_results[cleanup_backup] = deleted
        failed_direct_cleanup_owner_paths = {
            owner_path
            for owner_path, owner_backups, owner_is_unsafe in (
                (
                    discarded_audio_path,
                    discarded_audio_cleanup_backups,
                    discarded_audio_cleanup_backup_unsafe,
                ),
                (
                    discarded_log_path,
                    discarded_log_cleanup_backups,
                    discarded_log_cleanup_backup_unsafe,
                ),
            )
            if owner_path is not None
            and (
                owner_is_unsafe
                or any(
                    not backup.name.startswith(".cleanup.v2.")
                    and not cleanup_backup_delete_results.get(
                        backup,
                        False,
                    )
                    for backup in owner_backups
                )
            )
        }
        failed_owner_paths = (
            {
                owner_path
                for owner_path, owner_backups in (
                    discarded_inflight_cleanup_backups_by_owner.items()
                )
                if any(
                    not cleanup_backup_delete_results.get(
                        cleanup_backup,
                        False,
                    )
                    for cleanup_backup in owner_backups
                )
            }
            | selected_unsafe_cleanup_backup_owner_paths
            | failed_direct_cleanup_owner_paths
        )
        persistable_cleanup_owner_paths = (
            inflight_group_owner_paths
            | persisted_cleanup_owner_paths
            | direct_cleanup_owner_paths
        )
        pending_cleanup_owner_paths = (
            failed_owner_paths & persistable_cleanup_owner_paths
        )
        pending_cleanup_backup_entries = tuple(
            cleanup_journal_entries_by_name[name]
            for name in sorted_cleanup_journal_names
            if (
                name in cleanup_journal_identity_failed_names
                or not cleanup_backup_delete_results.get(
                    cleanup_journal_paths_by_name[name],
                    False,
                )
            )
        )
        cleanup_backup_journal_overflow = bool(
            (
                failed_owner_paths - persistable_cleanup_owner_paths
            )
            and not pending_cleanup_backup_entries
        )
        cleanup_backups_deleted = (
            all(cleanup_backup_delete_results.values())
            and not selected_unsafe_cleanup_backup_owner_paths
            and not discarded_audio_cleanup_backup_unsafe
            and not discarded_log_cleanup_backup_unsafe
            and not pending_cleanup_backup_entries
            and not cleanup_backup_journal_overflow
        )
        inflight_deleted = (
            not deferred_inflight_group_paths
            and recovery_cleanup_inflight_artifacts_absent
        )
        failed_inflight_group_paths: set[Path] = set()
        for inflight_path, inflight_group_paths in discarded_inflight_groups.items():
            if (
                preserve_transcript_after_insert_failure
                and not recovery_cleanup_authorized
            ):
                inflight_deleted = False
                continue
            if failed_owner_paths.intersection(inflight_group_paths):
                inflight_deleted = False
                continue
            deleted = _remove_recording_artifact(
                str(inflight_path),
                expected_stats=discarded_inflight_expected_stats[inflight_path],
            )
            if not deleted:
                deleted = all(
                    _recording_artifact_missing_but_safe(
                        str(owner_path),
                        suffix=(
                            ".socenc"
                            if _is_encrypted_recording_artifact(owner_path)
                            else owner_path.suffix.lower()
                        ),
                        state_path=store.path,
                    )
                    for owner_path in sorted(
                        inflight_group_paths,
                        key=lambda path: str(path),
                    )
                )
            if not deleted:
                inflight_deleted = False
                failed_inflight_group_paths.add(inflight_path)

        transcript_path: Path | None = None
        transcript_sibling_path: Path | None = None
        transcript_deleted = not state.transcript_path and not preserve_transcript_after_insert_failure
        if state.transcript_path and not preserve_transcript_after_insert_failure:
            transcript_present_before = False
            transcript_sibling_present_before = False
            try:
                transcript_path = _normalized_state_artifact_path(
                    _assert_clean_text(state.transcript_path, field_name="transcript path", max_chars=MAX_PATH_CHARS),
                    state_path=store.path,
                )
                transcript_presence, transcript_expected_stat = (
                    _safe_regular_leaf_probe(transcript_path)
                    if transcript_path is not None
                    else (False, None)
                )
                if transcript_presence is None:
                    raise RuntimeError(f"transcript file presence could not be verified: {transcript_path}")
                transcript_present_before = transcript_presence
                transcript_sibling_path = _transcript_sibling_path(transcript_path)
                transcript_sibling_presence, transcript_sibling_expected_stat = (
                    _safe_regular_leaf_probe(transcript_sibling_path)
                    if transcript_sibling_path is not None
                    else (False, None)
                )
                if transcript_sibling_presence is None:
                    raise RuntimeError(
                        f"transcript sibling presence could not be verified: {transcript_sibling_path}"
                    )
                transcript_sibling_present_before = transcript_sibling_presence
                if transcript_present_before:
                    if transcript_expected_stat is None:
                        raise RuntimeError(f"transcript file is not a safe regular file: {transcript_path}")
                    transcript_deleted = _remove_transcript_file(
                        transcript_path,
                        expected_stat=transcript_expected_stat,
                    )
                else:
                    transcript_deleted = False
                if not transcript_deleted and not transcript_present_before:
                    transcript_deleted = _transcript_artifact_missing_but_safe(transcript_path)
                if transcript_sibling_path is not None and transcript_sibling_present_before:
                    if transcript_present_before and not transcript_deleted:
                        raise RuntimeError(f"transcript file could not be deleted: {transcript_path}")
                    if not transcript_present_before and not transcript_deleted:
                        raise RuntimeError(f"transcript file presence could not be verified: {transcript_path}")
                    if transcript_sibling_expected_stat is None:
                        raise RuntimeError(f"transcript sibling is not a safe regular file: {transcript_sibling_path}")
                    if not _remove_transcript_file(
                        transcript_sibling_path,
                        expected_stat=transcript_sibling_expected_stat,
                    ):
                        raise RuntimeError(f"transcript sibling is missing: {transcript_sibling_path}")
                    if not transcript_present_before:
                        transcript_deleted = _transcript_artifact_missing_but_safe(transcript_path)
            except RuntimeError:
                transcript_deleted = False
            if (
                not transcript_deleted
                and transcript_path is not None
                and not transcript_present_before
                and not transcript_sibling_present_before
            ):
                transcript_deleted = _transcript_artifact_missing_but_safe(transcript_path)
                if transcript_deleted:
                    transcript_deleted = _transcript_sibling_missing_but_safe(transcript_path)
        finalizing_transcript_cleanup_failed_path: Path | None = None
        for inflight_transcript_path in sorted(
            discarded_finalizing_transcript_cleanup_paths,
            key=lambda path: str(path),
            reverse=True,
        ):
            if inflight_transcript_path in {transcript_path, transcript_sibling_path}:
                continue
            expected_stat = _recording_artifact_stat(inflight_transcript_path)
            if expected_stat is None:
                deleted = _transcript_artifact_missing_but_safe(inflight_transcript_path)
            else:
                try:
                    deleted = _remove_transcript_file(
                        inflight_transcript_path,
                        expected_stat=expected_stat,
                    )
                except RuntimeError:
                    deleted = False
            if not deleted:
                transcript_deleted = False
                if finalizing_transcript_cleanup_failed_path is None:
                    finalizing_transcript_cleanup_failed_path = inflight_transcript_path
        if (
            (state.audio_path and not audio_deleted)
            or (state.log_path and not log_deleted)
            or (discarded_inflight_paths and not inflight_deleted)
            or not cleanup_backups_deleted
            or pending_cleanup_owner_paths
            or pending_cleanup_backup_entries
            or cleanup_backup_journal_overflow
            or (
                state.transcript_path
                and not transcript_deleted
                and not preserve_transcript_after_insert_failure
            )
            or (
                discarded_finalizing_transcript_cleanup_paths
                and not transcript_deleted
            )
        ):
            error_message = "failed to discard recording artifacts"
            cleanup_failure_reasons: list[str] = []
            if state.audio_path and not audio_deleted:
                cleanup_failure_reasons.append("audio")
            if state.log_path and not log_deleted:
                cleanup_failure_reasons.append("log")
            if discarded_inflight_paths and not inflight_deleted:
                cleanup_failure_reasons.append("inflight")
            if not cleanup_backups_deleted:
                cleanup_failure_reasons.append("backup")
            if (
                pending_cleanup_owner_paths
                or pending_cleanup_backup_entries
                or cleanup_backup_journal_overflow
            ):
                cleanup_failure_reasons.append("journal")
            if (
                (
                    state.transcript_path
                    and not transcript_deleted
                    and not preserve_transcript_after_insert_failure
                )
                or (
                    discarded_finalizing_transcript_cleanup_paths
                    and not transcript_deleted
                )
            ):
                cleanup_failure_reasons.append("transcript")
            if not cleanup_failure_reasons:
                cleanup_failure_reasons.append("unknown")
            diagnostic_message = (
                f"{error_message} ({', '.join(cleanup_failure_reasons)})"
            )
            store.write(
                RecordingState(
                    status="error",
                    audio_path=(
                        ""
                        if (
                            preserve_transcript_after_insert_failure
                            and audio_deleted
                        )
                        else (
                            state.audio_path
                            if (
                                not audio_deleted
                                or not inflight_deleted
                                or not cleanup_backups_deleted
                                or finalizing_transcript_cleanup_failed_path
                                is not None
                            )
                            else ""
                        )
                    ),
                    log_path=(
                        state.log_path
                        if (
                            not log_deleted
                            or (
                                discarded_cleanup_backup_candidates_present
                                and not cleanup_backups_deleted
                            )
                        )
                        else ""
                    ),
                    transcript=state.transcript,
                    transcript_path=(
                        preserved_transcript_path
                        if preserve_transcript_after_insert_failure
                        else (
                            state.transcript_path
                            if state.transcript_path and not transcript_deleted
                            else str(
                                finalizing_transcript_cleanup_failed_path or ""
                            )
                        )
                    ),
                    pending_cleanup_owner_paths=tuple(
                        str(path)
                        for path in sorted(
                            pending_cleanup_owner_paths,
                            key=lambda candidate: str(candidate),
                        )
                    ),
                    pending_cleanup_restore_owner_paths=(
                        ()
                    ),
                    pending_cleanup_backup_entries=(
                        pending_cleanup_backup_entries
                    ),
                    cleanup_backup_journal_overflow=(
                        cleanup_backup_journal_overflow
                    ),
                    cleanup_backup_journal_restore=bool(
                        False
                    ),
                    inserted=state.inserted,
                    stopped_at=now_iso(),
                    language=state.language,
                    recorder=state.recorder,
                    input_device=state.input_device,
                    max_seconds=state.max_seconds,
                    error=(
                        preserved_transcript_recovery_error
                        or error_message
                    ),
                )
            )
            payload = {
                "status": "error",
                "message": diagnostic_message,
                "error": diagnostic_message,
                "cleanup_failure_reasons": cleanup_failure_reasons,
                "discarded_audio_path_present": bool(state.audio_path),
                "audio_deleted": audio_deleted,
                "log_deleted": log_deleted,
                "inflight_artifact_count": len(reported_inflight_paths),
                "inflight_artifacts_deleted": inflight_deleted,
                "cleanup_backups_deleted": cleanup_backups_deleted,
            "transcript_deleted": bool(
                transcript_deleted
                and not preserve_transcript_after_insert_failure
                and not state.transcript
            ),
            }
            return payload
        try:
            store.write(
                RecordingState(
                    status="idle",
                    audio_path="",
                    log_path="",
                    transcript=(
                        state.transcript
                        if preserve_transcript_after_insert_failure
                        else ""
                    ),
                    transcript_path="",
                    pending_cleanup_owner_paths=(),
                    pending_cleanup_backup_entries=(),
                    cleanup_backup_journal_overflow=False,
                    stopped_at=now_iso(),
                    language=state.language,
                    recorder=state.recorder,
                    input_device=state.input_device,
                    max_seconds=state.max_seconds,
                    error=(
                        preserved_transcript_recovery_error
                        if (
                            preserve_transcript_after_insert_failure
                            and bool(state.transcript)
                        )
                        else ""
                    ),
                )
            )
        except Exception:
            try:
                store.write(
                    RecordingState(
                        status="error",
                        audio_path="",
                        log_path="",
                        transcript=(
                            state.transcript
                            if preserve_transcript_after_insert_failure
                            else ""
                        ),
                        transcript_path=(
                            preserved_transcript_path
                            if preserve_transcript_after_insert_failure
                            else ""
                        ),
                        pending_cleanup_restore_owner_paths=(
                            ()
                        ),
                        pending_cleanup_backup_entries=(
                            ()
                        ),
                        cleanup_backup_journal_restore=False,
                        inserted=state.inserted,
                        stopped_at=now_iso(),
                        language=state.language,
                        recorder=state.recorder,
                        input_device=state.input_device,
                        max_seconds=state.max_seconds,
                        error=(
                            preserved_transcript_recovery_error
                            or "failed to persist canceled recording state"
                        ),
                    )
                )
            except Exception as persist_exc:
                log_event(
                    "error",
                    "cancel_error_state_persist_failed",
                    error=sanitize_error_message(str(persist_exc)),
                )
            raise
        return {
            "status": "idle",
            "message": "recording discarded",
            "discarded_audio_path_present": bool(state.audio_path),
            "audio_deleted": audio_deleted,
            "log_deleted": log_deleted,
            "inflight_artifact_count": len(reported_inflight_paths),
            "inflight_artifacts_deleted": inflight_deleted,
            "cleanup_backups_deleted": cleanup_backups_deleted,
            "transcript_deleted": (
                transcript_deleted and not preserve_transcript_after_insert_failure
            ),
        }
    finally:
        _release_finalization_lock(lock_path)


def command_toggle(args: argparse.Namespace) -> dict[str, object]:
    store = build_store(args)
    state = store.read()
    _raise_if_state_unreadable(state)
    if state.status == "finalizing":
        args.confirm_plaintext_output = True
        return command_stop(args)
    if state.status == "recording":
        try:
            recording_active = _recording_process_verified_active(state)
        except RuntimeError:
            args.confirm_plaintext_output = True
            return command_stop(args)
        if recording_active:
            args.confirm_plaintext_output = True
            return command_stop(args)
        if state.audio_path:
            args.confirm_plaintext_output = True
            return command_stop(args)
    if state.status in {"recorded", "processing"}:
        args.confirm_plaintext_output = True
        return command_stop(args)
    return command_start(args)


def command_status(args: argparse.Namespace) -> dict[str, object]:
    store = build_store(args)
    state = store.read()
    payload = _diagnostics_state_payload(state)
    if is_state_read_error(state.error):
        payload["status"] = "error"
        return payload
    persisted_state_error = bool(state.error)
    if state.status not in {"recording", "recorded", "processing", "finalizing"} and _is_finalization_lock_active(store.path):
        payload["status"] = "finalizing"
        payload["message"] = "recording lifecycle in progress; wait for completion"
        payload.pop("error", None)
        return payload
    if state.status == "recording":
        if state.pid is None:
            error_text = "recording process pid is missing; recording state preserved"
            payload["status"] = "error"
            payload["message"] = error_text
            payload["error"] = error_text
            payload["inserted"] = False
            return payload
        if isinstance(state.pid, bool) or not isinstance(state.pid, int) or state.pid <= 0:
            error_text = "recording process liveness could not be verified; recording state preserved"
            payload["status"] = "error"
            payload["message"] = error_text
            payload["error"] = error_text
            payload["inserted"] = False
            return payload
        try:
            verified_alive = _recording_process_verified_active(state)
        except RuntimeError as exc:
            payload["status"] = "error"
            payload["message"] = str(exc)
            payload["error"] = str(exc)
            payload["inserted"] = False
            return payload
        if not verified_alive:
            expected_identity = _recording_process_identity_for_lifecycle(state.process_identity)
            if expected_identity is None:
                payload["status"] = "error"
                payload["message"] = _RECORDING_PROCESS_IDENTITY_INVALID_ERROR
                payload["error"] = payload["message"]
                payload["inserted"] = False
                return payload
            try:
                zombie_state = _process_is_zombie(state.pid)
            except Exception:
                error_text = "recording process liveness could not be verified; recording state preserved"
                payload["status"] = "error"
                payload["message"] = error_text
                payload["error"] = error_text
                payload["inserted"] = False
                return payload
            # A vanished leader has no zombie state. Stable absence still proves
            # safety; an unknown group or identity probe remains fail-closed.
            allow_matching_identity = zombie_state is True
            stable_absence, absence_error = _recording_process_stable_absence(
                state.pid,
                expected_identity,
                allow_matching_identity=allow_matching_identity,
            )
            if absence_error is not None:
                persisted_state_error = False
                payload["status"] = "error"
                payload["message"] = absence_error
                payload["error"] = payload["message"]
                payload["inserted"] = False
            elif not stable_absence:
                if not expected_identity:
                    persisted_state_error = False
                    payload["status"] = "error"
                    payload["message"] = _RECORDING_PROCESS_IDENTITY_INVALID_ERROR
                    payload["error"] = payload["message"]
                    payload["inserted"] = False
                else:
                    payload["status"] = "recording"
                    payload["message"] = "recording process group is still active; run stop to transcribe"
                    payload.pop("error", None)
            else:
                current_audio_path = _normalized_state_recording_artifact_path(
                    state.audio_path,
                    suffix=(".wav", ".flac", ".socenc"),
                    state_path=store.path,
                    require_recordings_dir=False,
                )
                current_audio_stat = _recording_artifact_stat(current_audio_path) if current_audio_path else None
                if current_audio_stat is None or current_audio_stat.st_size == 0:
                    persisted_state_error = False
                    payload["status"] = "error"
                    payload["message"] = "recording exited before audio was saved"
                    payload["error"] = payload["message"]
                    payload["inserted"] = False
                else:
                    payload["status"] = "recorded"
                    payload["message"] = "recording process has exited; run stop to transcribe"
                    payload.pop("error", None)
                    payload["inserted"] = False
    if payload.get("status") in {"recording", "recorded"}:
        microphone_level = _recording_level_payload(state, state_path=store.path)
        if microphone_level is not None:
            payload["microphone_level"] = microphone_level
    if payload.get("status") == "done" and _confirm_plaintext_transcript_output(args):
        transcript = state.transcript
        if not transcript:
            transcript_path = _normalized_state_artifact_path(
                state.transcript_path,
                state_path=store.path,
            )
            if transcript_path is not None:
                try:
                    transcript = _read_stored_transcript_text(transcript_path)
                except (OSError, RuntimeError):
                    payload["transcript_recovery_failed"] = True
        if transcript:
            payload["transcript"] = transcript
            payload["transcript_output_redacted"] = False
            payload["transcript_recovered"] = True
    if persisted_state_error and payload.get("error"):
        # Status is read-only. Unchanged error state remains historical even
        # while recovery artifacts are intentionally retained for retry.
        payload["persisted_error"] = True
        payload[_PERSISTED_STATUS_ERROR_PROVENANCE] = (
            _PERSISTED_STATUS_ERROR_PROVENANCE
        )
    return payload


def _doctor_exact_dict(value: object, fields: frozenset[str]) -> bool:
    return (
        type(value) is dict
        and all(type(key) is str for key in value)
        and frozenset(value) == fields
    )


def _doctor_text_list(value: object) -> bool:
    return type(value) is list and all(
        type(item) is str and bool(item.strip()) for item in value
    )


def _doctor_audio_is_valid(audio: object) -> bool:
    return bool(
        _doctor_exact_dict(
            audio,
            frozenset(
                {
                    "schema_version",
                    "pci_drivers",
                    "sof_modules",
                    "soundwire_modules",
                    "sof_active",
                    "soundwire_active",
                    "packages",
                    "pipewire_source_class",
                    "pipewire_source_detected",
                    "dmic_visible",
                    "legacy_hda_warning",
                    "warnings",
                    "probes",
                }
            ),
        )
        and type(audio["schema_version"]) is int
        and audio["schema_version"] == 2
        and all(
            _doctor_text_list(audio[field])
            for field in ("pci_drivers", "sof_modules", "soundwire_modules", "warnings")
        )
        and type(audio["sof_active"]) is bool
        and audio["soundwire_active"] is None
        and _doctor_exact_dict(
            audio["packages"],
            frozenset(
                {
                    "alsa-sof-firmware",
                    "alsa-ucm",
                    "alsa-ucm-utils",
                    "pipewire",
                    "pipewire-utils",
                    "wireplumber",
                }
            ),
        )
        and all(
            value is None or type(value) is str
            for value in audio["packages"].values()
        )
        and type(audio["pipewire_source_class"]) is str
        and type(audio["pipewire_source_detected"]) is bool
        and (
            audio["dmic_visible"] is None
            or type(audio["dmic_visible"]) is bool
        )
        and type(audio["legacy_hda_warning"]) is bool
        and _doctor_exact_dict(
            audio["probes"],
            frozenset({"pci", "modules", "packages", "pipewire"}),
        )
        and all(type(value) is str for value in audio["probes"].values())
    )


def _validated_doctor_report(report: object) -> dict[str, object] | None:
    if not isinstance(report, Mapping):
        return None
    try:
        items = tuple(report.items())
        keys = tuple(key for key, _value in items)
        if (
            len(items) != len(_DOCTOR_REPORT_FIELDS)
            or not all(type(key) is str for key in keys)
            or len(frozenset(keys)) != len(keys)
            or frozenset(keys) != frozenset(_DOCTOR_REPORT_FIELDS)
        ):
            return None
        values = dict(items)
        checks = values["checks"]
        desktop = values["desktop"]
        configured = values["configured"]
        audio = values["audio"]
        acceleration = values["acceleration"]
        notes = values["notes"]
        if type(values["ok"]) is not bool or type(values["applet"]) is not bool:
            return None
        if (
            type(checks) is not list
            or not checks
            or len(checks) > len(doctor.DOCTOR_CHECK_NAMES)
        ):
            return None
        check_names: list[str] = []
        for check in checks:
            if not _doctor_exact_dict(check, frozenset({"name", "ok", "detail"})):
                return None
            if (
                type(check["name"]) is not str
                or not check["name"].strip()
                or type(check["ok"]) is not bool
                or type(check["detail"]) is not str
                or check["name"] not in doctor.DOCTOR_CHECK_NAMES
            ):
                return None
            check_names.append(check["name"])
        if len(check_names) != len(frozenset(check_names)) or "python3" not in check_names:
            return None
        if not _doctor_exact_dict(
            desktop,
            frozenset(
                {
                    "current_desktop",
                    "session_type",
                    "desktop_session",
                    "cinnamon",
                    "x11",
                }
            ),
        ):
            return None
        if (
            not all(
                type(desktop[field]) is str
                for field in ("current_desktop", "session_type", "desktop_session")
            )
            or type(desktop["cinnamon"]) is not bool
            or type(desktop["x11"]) is not bool
        ):
            return None
        if not _doctor_exact_dict(
            configured,
            frozenset(
                {
                    "recorder",
                    "transcriber",
                    "output",
                    "postprocessor",
                    "warnings",
                }
            ),
        ):
            return None
        section_fields = {
            "recorder": frozenset({"ok", "value", "detail"}),
            "transcriber": frozenset({"ok", "value", "detail"}),
            "output": frozenset({"ok", "value", "paste_ok", "detail"}),
            "postprocessor": frozenset({"ok", "value", "detail"}),
        }
        for name, required_fields in section_fields.items():
            section = configured[name]
            allowed_fields = (
                required_fields | frozenset({"resolved"})
                if name == "transcriber"
                else required_fields
            )
            if type(section) is not dict or frozenset(section) not in {
                required_fields,
                allowed_fields,
            }:
                return None
            if (
                not all(type(key) is str for key in section)
                or type(section["ok"]) is not bool
                or type(section["value"]) is not str
                or type(section["detail"]) is not str
                or (name == "output" and type(section["paste_ok"]) is not bool)
                or ("resolved" in section and type(section["resolved"]) is not str)
            ):
                return None
        if not _doctor_text_list(configured["warnings"]):
            return None
        if not _doctor_audio_is_valid(audio):
            return None
        if not _doctor_exact_dict(
            acceleration,
            frozenset({"schema_version", "cpu", "audio", "ctranslate2", "gna"}),
        ):
            return None
        if (
            type(acceleration["schema_version"]) is not int
            or acceleration["schema_version"] != 3
            or not _doctor_audio_is_valid(acceleration["audio"])
            or acceleration["audio"] != audio
            or not _doctor_text_list(notes)
        ):
            return None
        cpu = acceleration["cpu"]
        if not _doctor_exact_dict(
            cpu,
            frozenset(
                {
                    "probe_status",
                    "model",
                    "physical_cores",
                    "logical_cpus",
                    "avx2",
                    "avx_vnni",
                    "hybrid",
                    "hfi_cpu_flag",
                    "hfi_kernel_built_in",
                    "hfi_runtime_active",
                    "hfi_available",
                }
            ),
        ):
            return None
        if (
            type(cpu["probe_status"]) is not str
            or cpu["model"] is not None
            and type(cpu["model"]) is not str
            or any(
                cpu[field] is not None and type(cpu[field]) is not int
                for field in ("physical_cores", "logical_cpus")
            )
            or any(
                cpu[field] is not None and type(cpu[field]) is not bool
                for field in (
                    "avx2",
                    "avx_vnni",
                    "hybrid",
                    "hfi_cpu_flag",
                    "hfi_kernel_built_in",
                    "hfi_runtime_active",
                    "hfi_available",
                )
            )
        ):
            return None
        ctranslate2 = acceleration["ctranslate2"]
        if not _doctor_exact_dict(
            ctranslate2,
            frozenset(
                {
                    "probe_status",
                    "available",
                    "version",
                    "supported_compute_types",
                    "requested_compute_type",
                    "cpu_threads",
                    "num_workers",
                    "faster_whisper",
                }
            ),
        ):
            return None
        compute_types = ctranslate2["supported_compute_types"]
        faster_whisper = ctranslate2["faster_whisper"]
        if (
            type(ctranslate2["probe_status"]) is not str
            or ctranslate2["available"] is not None
            and type(ctranslate2["available"]) is not bool
            or ctranslate2["version"] is not None
            and type(ctranslate2["version"]) is not str
            or compute_types is not None
            and not _doctor_text_list(compute_types)
            or type(ctranslate2["requested_compute_type"]) is not str
            or (
                ctranslate2["cpu_threads"] is not None
                and type(ctranslate2["cpu_threads"]) is not int
            )
            or (
                ctranslate2["num_workers"] is not None
                and type(ctranslate2["num_workers"]) is not int
            )
            or not _doctor_exact_dict(
                faster_whisper,
                frozenset({"available", "version", "worker_available", "ready"}),
            )
        ):
            return None
        if (
            any(
                faster_whisper[field] is not None
                and type(faster_whisper[field]) is not bool
                for field in ("available", "worker_available", "ready")
            )
            or faster_whisper["version"] is not None
            and type(faster_whisper["version"]) is not str
        ):
            return None
        gna = acceleration["gna"]
        if not _doctor_exact_dict(
            gna,
            frozenset(
                {
                    "hardware_advertised_by_cpu_model",
                    "driver_detected",
                    "device_node_detected",
                    "supported_by_soc",
                    "reason",
                }
            ),
        ):
            return None
        if (
            any(
                gna[field] is not None and type(gna[field]) is not bool
                for field in (
                    "hardware_advertised_by_cpu_model",
                    "driver_detected",
                    "device_node_detected",
                )
            )
            or type(gna["supported_by_soc"]) is not bool
            or type(gna["reason"]) is not str
        ):
            return None
        validated = {field: values[field] for field in _DOCTOR_REPORT_FIELDS}
        canonical_acceleration = dict(acceleration)
        canonical_acceleration["audio"] = audio
        validated["acceleration"] = canonical_acceleration
        return validated
    except (Exception, SystemExit):
        return None


def _doctor_applet_payload(report: dict[str, object]) -> dict[str, object]:
    python_check = next(
        check for check in report["checks"] if check["name"] == "python3"
    )
    configured = report["configured"]
    return {
        "schema_version": 1,
        "status": "done",
        "ok": report["ok"],
        "checks": [
            {
                "name": python_check["name"],
                "ok": python_check["ok"],
                "detail": python_check["detail"],
            }
        ],
        "desktop": {"cinnamon": report["desktop"]["cinnamon"]},
        "configured": {
            name: {
                "ok": configured[name]["ok"],
                **(
                    {"paste_ok": configured[name]["paste_ok"]}
                    if name == "output"
                    else {}
                ),
                "detail": configured[name]["detail"],
            }
            for name in ("recorder", "transcriber", "output", "postprocessor")
        },
        "warnings": list(configured["warnings"]),
        "applet": True,
    }


def _redact_doctor_payload(
    payload: dict[str, object],
    *,
    secret_values: tuple[str, ...],
) -> dict[str, object]:
    def safe_text(value: object, *, fixed_values: frozenset[str] = frozenset()) -> str:
        if type(value) is not str:
            raise RuntimeError("doctor returned invalid report")
        redacted = _redact_error_for_user(value, secret_values=secret_values)
        if redacted != value:
            return redacted
        if value in fixed_values:
            return value
        return redacted

    def safe_text_list(values: object) -> list[str]:
        if type(values) is not list:
            raise RuntimeError("doctor returned invalid report")
        return [safe_text(value) for value in values]

    def safe_check(check: object) -> dict[str, object]:
        if type(check) is not dict:
            raise RuntimeError("doctor returned invalid report")
        name = check["name"]
        if name not in doctor.DOCTOR_CHECK_NAMES:
            raise RuntimeError("doctor returned invalid report")
        return {
            "name": name,
            "ok": check["ok"],
            "detail": safe_text(check["detail"]),
        }

    def safe_audio(audio: object) -> dict[str, object]:
        if type(audio) is not dict:
            raise RuntimeError("doctor returned invalid report")
        packages = audio["packages"]
        probes = audio["probes"]
        if type(packages) is not dict or type(probes) is not dict:
            raise RuntimeError("doctor returned invalid report")
        return {
            "schema_version": audio["schema_version"],
            "pci_drivers": safe_text_list(audio["pci_drivers"]),
            "sof_modules": safe_text_list(audio["sof_modules"]),
            "soundwire_modules": safe_text_list(audio["soundwire_modules"]),
            "sof_active": audio["sof_active"],
            "soundwire_active": audio["soundwire_active"],
            "packages": {
                name: (
                    None
                    if packages[name] is None
                    else safe_text(packages[name])
                )
                for name in _DOCTOR_AUDIO_PACKAGE_NAMES
            },
            "pipewire_source_class": safe_text(
                audio["pipewire_source_class"],
                fixed_values=_DOCTOR_PIPEWIRE_SOURCE_CLASSES,
            ),
            "pipewire_source_detected": audio["pipewire_source_detected"],
            "dmic_visible": audio["dmic_visible"],
            "legacy_hda_warning": audio["legacy_hda_warning"],
            "warnings": safe_text_list(audio["warnings"]),
            "probes": {
                name: safe_text(
                    probes[name],
                    fixed_values=_DOCTOR_AUDIO_PROBE_STATUSES,
                )
                for name in _DOCTOR_AUDIO_PROBE_NAMES
            },
        }

    def safe_cpu(cpu: object) -> dict[str, object]:
        if type(cpu) is not dict:
            raise RuntimeError("doctor returned invalid report")
        return {
            "probe_status": safe_text(
                cpu["probe_status"],
                fixed_values=_DOCTOR_CPU_PROBE_STATUSES,
            ),
            "model": None if cpu["model"] is None else safe_text(cpu["model"]),
            "physical_cores": cpu["physical_cores"],
            "logical_cpus": cpu["logical_cpus"],
            "avx2": cpu["avx2"],
            "avx_vnni": cpu["avx_vnni"],
            "hybrid": cpu["hybrid"],
            "hfi_cpu_flag": cpu["hfi_cpu_flag"],
            "hfi_kernel_built_in": cpu["hfi_kernel_built_in"],
            "hfi_runtime_active": cpu["hfi_runtime_active"],
            "hfi_available": cpu["hfi_available"],
        }

    def safe_ctranslate2(ctranslate2: object) -> dict[str, object]:
        if type(ctranslate2) is not dict or type(ctranslate2["faster_whisper"]) is not dict:
            raise RuntimeError("doctor returned invalid report")
        faster_whisper = ctranslate2["faster_whisper"]
        supported_compute_types = ctranslate2["supported_compute_types"]
        if supported_compute_types is not None:
            supported_compute_types = safe_text_list(supported_compute_types)
        return {
            "probe_status": safe_text(
                ctranslate2["probe_status"],
                fixed_values=_DOCTOR_CTRANSLATE2_PROBE_STATUSES,
            ),
            "available": ctranslate2["available"],
            "version": (
                None
                if ctranslate2["version"] is None
                else safe_text(ctranslate2["version"])
            ),
            "supported_compute_types": supported_compute_types,
            "requested_compute_type": safe_text(
                ctranslate2["requested_compute_type"],
                fixed_values=frozenset(
                    {doctor.FASTER_WHISPER_REQUESTED_COMPUTE_TYPE}
                ),
            ),
            "cpu_threads": ctranslate2["cpu_threads"],
            "num_workers": ctranslate2["num_workers"],
            "faster_whisper": {
                "available": faster_whisper["available"],
                "version": (
                    None
                    if faster_whisper["version"] is None
                    else safe_text(faster_whisper["version"])
                ),
                "worker_available": faster_whisper["worker_available"],
                "ready": faster_whisper["ready"],
            },
        }

    def safe_gna(gna: object) -> dict[str, object]:
        if type(gna) is not dict:
            raise RuntimeError("doctor returned invalid report")
        return {
            "hardware_advertised_by_cpu_model": gna["hardware_advertised_by_cpu_model"],
            "driver_detected": gna["driver_detected"],
            "device_node_detected": gna["device_node_detected"],
            "supported_by_soc": gna["supported_by_soc"],
            "reason": safe_text(gna["reason"], fixed_values=_DOCTOR_GNA_REASONS),
        }

    checks = payload["checks"]
    configured = payload["configured"]
    if type(checks) is not list or type(configured) is not dict:
        raise RuntimeError("doctor returned invalid report")
    if payload.get("schema_version") == 1:
        safe_configured: dict[str, object] = {}
        for name in ("recorder", "transcriber", "output", "postprocessor"):
            section = configured[name]
            if type(section) is not dict:
                raise RuntimeError("doctor returned invalid report")
            safe_section: dict[str, object] = {
                "ok": section["ok"],
                "detail": safe_text(section["detail"]),
            }
            if name == "output":
                safe_section["paste_ok"] = section["paste_ok"]
            safe_configured[name] = safe_section
        return {
            "schema_version": 1,
            "status": "done",
            "ok": payload["ok"],
            "checks": [safe_check(check) for check in checks],
            "desktop": {"cinnamon": payload["desktop"]["cinnamon"]},
            "configured": safe_configured,
            "warnings": safe_text_list(payload["warnings"]),
            "applet": True,
        }

    safe_audio_value = safe_audio(payload["audio"])
    acceleration = payload["acceleration"]
    if type(acceleration) is not dict or type(acceleration["cpu"]) is not dict:
        raise RuntimeError("doctor returned invalid report")
    safe_acceleration = {
        "schema_version": acceleration["schema_version"],
        "cpu": safe_cpu(acceleration["cpu"]),
        "audio": safe_audio_value,
        "ctranslate2": safe_ctranslate2(acceleration["ctranslate2"]),
        "gna": safe_gna(acceleration["gna"]),
    }
    safe_configured = {}
    for name in ("recorder", "transcriber", "output", "postprocessor"):
        section = configured[name]
        if type(section) is not dict:
            raise RuntimeError("doctor returned invalid report")
        safe_section = {
            "ok": section["ok"],
            "value": safe_text(section["value"]),
            "detail": safe_text(section["detail"]),
        }
        if name == "output":
            safe_section["paste_ok"] = section["paste_ok"]
        if name == "transcriber" and "resolved" in section:
            safe_section["resolved"] = safe_text(
                section["resolved"],
                fixed_values=_DOCTOR_RESOLVED_BACKENDS,
            )
        safe_configured[name] = safe_section
    return {
        "status": "done",
        "ok": payload["ok"],
        "checks": [safe_check(check) for check in checks],
        "desktop": {
            "current_desktop": safe_text(payload["desktop"]["current_desktop"]),
            "session_type": safe_text(payload["desktop"]["session_type"]),
            "desktop_session": safe_text(payload["desktop"]["desktop_session"]),
            "cinnamon": payload["desktop"]["cinnamon"],
            "x11": payload["desktop"]["x11"],
        },
        "configured": {
            **safe_configured,
            "warnings": safe_text_list(configured["warnings"]),
        },
        "audio": safe_audio_value,
        "acceleration": safe_acceleration,
        "applet": payload["applet"],
        "notes": safe_text_list(payload["notes"]),
        "warnings": safe_text_list(payload["warnings"]),
    }


def command_doctor(args: argparse.Namespace) -> dict[str, object]:
    settings = _settings_json_from_args(args)
    applet = _coerce_bool(getattr(args, "applet", False), field_name="applet")
    report = doctor_report(settings, applet=applet)
    validated_report = _validated_doctor_report(report)
    if validated_report is None or validated_report["applet"] is not applet:
        raise RuntimeError("doctor returned invalid report") from None
    if applet:
        return _doctor_applet_payload(validated_report)
    payload: dict[str, object] = {"status": "done"}
    for field in _DOCTOR_REPORT_FIELDS:
        payload[field] = validated_report[field]
    payload["warnings"] = list(validated_report["configured"]["warnings"])
    return payload


def command_setup(args: argparse.Namespace) -> dict[str, object]:
    settings = _settings_json_from_args(args)
    applet = _coerce_bool(getattr(args, "applet", False), field_name="applet")
    doctor_payload = doctor_report(settings, applet=applet)
    return {
        "status": "done",
        "doctor": doctor_payload,
        **build_setup_plan(doctor_payload),
    }


def command_list_inputs(args: argparse.Namespace) -> dict[str, object]:
    include_monitors = _coerce_bool(args.include_monitors, field_name="include_monitors")
    sources = _normalize_input_sources(list_input_sources(include_monitors))
    return {
        "status": "done",
        "sources": [
            {
                "id": source["id"],
                "name": source["name"],
                "description": source["description"],
                "driver": source["driver"],
                "state": source["state"],
                "default": source["default"],
                "monitor": source["monitor"],
            }
            for source in sources
        ],
    }


def command_models(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return {"status": "done", "models": _redact_model_payload_paths(list_models(verify=True))}


def command_text_models(args: argparse.Namespace) -> dict[str, object]:
    raw_backend = args.backend or "ollama"
    if isinstance(raw_backend, bool) or not isinstance(raw_backend, str):
        raise RuntimeError("text models backend must be text")
    if _contains_escaped_null(raw_backend):
        raise RuntimeError("text models backend contains invalid null byte")
    if _contains_http_header_control_chars(raw_backend):
        raise RuntimeError("text models backend contains invalid control character")
    backend = raw_backend.strip().lower().replace("_", "-")
    if backend not in {"ollama", "openai-compatible"}:
        raise RuntimeError("text models backend must be ollama or openai-compatible")
    if backend == "openai-compatible":
        url = _validate_openai_compatible_http_url(args.openai_compatible_url or DEFAULT_OPENAI_COMPATIBLE_URL, field_name="openai-compatible url")
        api_key = _openai_compatible_api_key_from_args(args)
        payload = _normalize_text_models_payload(list_openai_compatible_models(url, api_key=api_key))
        return {
            "status": "done",
            "backend": "openai-compatible",
            "url": url,
            **payload,
        }
    url = _validate_ollama_http_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
    payload = _normalize_text_models_payload(list_ollama_models(url))
    if _is_local_ollama_url(url):
        try:
            _command_path("ollama")
            ollama_available = True
        except RuntimeError:
            ollama_available = False
    else:
        ollama_available = False
    if _is_local_ollama_url(url) and not ollama_available and payload["available"] is False:
        return {
            "status": "done",
            "backend": "ollama",
            "url": url,
            "available": False,
            "models": [],
            "message": "Ollama command is not available; install Ollama and start the local server",
        }
    return {
        "status": "done",
        "backend": "ollama",
        "url": url,
        **payload,
    }


def command_install_text_model(args: argparse.Namespace) -> dict[str, object]:
    raw_backend = args.backend or "ollama"
    if isinstance(raw_backend, bool) or not isinstance(raw_backend, str):
        raise RuntimeError("text model backend must be text")
    if _contains_escaped_null(raw_backend):
        raise RuntimeError("text model backend contains invalid null byte")
    if _contains_http_header_control_chars(raw_backend):
        raise RuntimeError("text model backend contains invalid control character")
    backend = raw_backend.strip().lower().replace("_", "-")
    if backend != "ollama":
        raise RuntimeError("text model installation currently supports only ollama")
    model = _assert_clean_text(args.model, field_name="ollama model", max_chars=MAX_PATH_CHARS).strip()
    if not model:
        raise RuntimeError("ollama model must not be empty")
    if model.startswith("-"):
        raise RuntimeError("ollama model must not start with '-'")
    url = _validate_ollama_http_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
    try:
        ollama = _command_path("ollama")
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith("command path is not trusted") or "command is not available" in message:
            raise RuntimeError("ollama command is not available") from exc
        raise
    env = _filtered_environment()
    if url:
        env["OLLAMA_HOST"] = url
    try:
        returncode, stdout_data, stderr_data = run_process_bounded_output(
            [ollama, "pull", model],
            timeout_seconds=OLLAMA_PULL_TIMEOUT_SECONDS,
            max_output_bytes=MAX_LOG_EXCERPT_CHARS,
            env=env,
            label="ollama pull",
        )
        stdout = _decode_binary_output(stdout_data, field_name="ollama pull stdout")
        stderr = _decode_binary_output(stderr_data, field_name="ollama pull stderr")
    except CommandChainError as exc:
        raise RuntimeError(str(exc)) from exc
    except OSError as exc:
        raise RuntimeError(f"failed to run ollama pull: {exc}") from exc
    if returncode != 0:
        detail = (stderr or stdout or f"exit code {returncode}").strip()
        detail = _redact_error_for_user(detail[:MAX_LOG_EXCERPT_CHARS])
        raise RuntimeError(f"ollama pull failed: {detail}")
    return {
        "status": "done",
        "backend": "ollama",
        "model": model,
        "url": url,
        "message": f"Ollama model installed: {model}",
    }


def command_download_model(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    force = _coerce_bool(args.force, field_name="force")
    return _redact_model_payload_path(download_model(args.model, force))


def command_remove_model(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return _redact_model_payload_path(remove_model(args.model))


def _benchmark_targets(model_names: list[str] | None, language: str) -> list[ModelSpec]:
    if model_names:
        targets: list[ModelSpec] = []
        for name in model_names:
            clean_name = _assert_clean_text(name, field_name="model name", max_chars=MAX_PATH_CHARS).strip()
            if not clean_name:
                raise RuntimeError("model name must not be empty")
            try:
                targets.append(resolve_model(clean_name))
            except ModelError as exc:
                raise RuntimeError(str(exc)) from exc
        return targets
    return [model for model in CATALOG if bool(model_status(model, verify=False).get("downloaded"))]


def _temporary_benchmark_transcript_path() -> tuple[Path, os.stat_result, os.stat_result]:
    fd: int | None = None
    path: Path | None = None
    file_stat: os.stat_result | None = None
    owner_stat: os.stat_result | None = None
    setup_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    close_error: BaseException | None = None
    cleanup_attempted = False
    cleanup_succeeded = False
    try:
        transcript_directory = transcript_dir()
        directory_fd = open_directory_without_following_symlinks(
            transcript_directory,
            field_name="transcript directory",
        )
        try:
            fd, path_text = tempfile.mkstemp(
                prefix=".benchmark-",
                suffix=".tmp.txt",
                dir=f"/proc/self/fd/{directory_fd}",
            )
        finally:
            os.close(directory_fd)
        path_text = str(transcript_directory / Path(path_text).name)
        path = Path(path_text)
        file_stat = os.fstat(fd)
        if (
            not stat_module.S_ISREG(file_stat.st_mode)
            or getattr(file_stat, "st_nlink", 1) != 1
        ):
            raise RuntimeError("benchmark transcript file identity is unavailable")
        owner_stat = _write_transient_transcript_owner(path)
    except BaseException as exc:
        setup_error = exc
        if path is not None and file_stat is not None:
            cleanup_attempted = True
            try:
                _remove_transient_transcript_path(
                    path,
                    transcript_dir() / ".benchmark-storage.txt",
                    expected_stat=file_stat,
                    expected_owner_stat=owner_stat,
                )
            except BaseException as cleanup_exc:
                cleanup_error = cleanup_exc
            else:
                cleanup_succeeded = True
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            except BaseException as exc:
                close_error = exc
    close_cleanup_error: BaseException | None = None
    retry_cleanup_allowed = (
        not cleanup_attempted
        or cleanup_error is None
        or isinstance(cleanup_error, Exception)
    )
    if (
        close_error is not None
        and path is not None
        and file_stat is not None
        and not cleanup_succeeded
        and retry_cleanup_allowed
    ):
        cleanup_attempted = True
        try:
            _remove_transient_transcript_path(
                path,
                transcript_dir() / ".benchmark-storage.txt",
                expected_stat=file_stat,
                expected_owner_stat=owner_stat,
            )
        except BaseException as cleanup_exc:
            close_cleanup_error = cleanup_exc
        else:
            cleanup_succeeded = True
            cleanup_error = None
    interrupt_error = next(
        (
            error
            for error in (setup_error, close_error, cleanup_error, close_cleanup_error)
            if isinstance(error, KeyboardInterrupt)
        ),
        None,
    )
    if interrupt_error is not None:
        _raise_sanitized_transient_exception(
            interrupt_error,
            message=TRANSIENT_TRANSCRIPT_INTERRUPT_ERROR,
        )
    cleanup_failure = (
        close_cleanup_error
        if close_cleanup_error is not None
        else cleanup_error
    )
    if cleanup_failure is not None:
        _raise_sanitized_transient_exception(
            RuntimeError(),
            message=TRANSIENT_TRANSCRIPT_CLEANUP_ERROR,
        )
    close_signal = (
        close_error
        if close_error is not None
        and (setup_error is None or isinstance(setup_error, Exception))
        else None
    )
    if close_signal is not None:
        close_message = (
            TRANSIENT_TRANSCRIPT_INTERRUPT_ERROR
            if isinstance(close_signal, KeyboardInterrupt)
            else TRANSIENT_TRANSCRIPT_WRITE_ERROR
        )
        _raise_sanitized_transient_exception(close_signal, message=close_message)
    if setup_error is not None:
        _raise_backend_sanitized_exception(
            setup_error,
            message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
        )
    if path is None:
        raise RuntimeError(TRANSIENT_TRANSCRIPT_WRITE_ERROR)
    if file_stat is None or owner_stat is None:
        raise RuntimeError("benchmark transcript file identity is unavailable")
    return path, file_stat, owner_stat


def _benchmark_transcript_writer_identity(
    path: Path,
    expected_stat: os.stat_result,
    expected_owner_stat: os.stat_result,
    text: str,
) -> tuple[os.stat_result | None, bool]:
    presence, current_stat = _safe_regular_leaf_probe(path)
    if presence is False:
        return expected_stat, True
    if presence is not True or current_stat is None:
        return None, False
    owner_presence, current_owner_stat = _safe_regular_leaf_probe(
        _transient_transcript_owner_path(path),
    )
    if (
        owner_presence is not True
        or current_owner_stat is None
        or not _same_leaf_identity(current_owner_stat, expected_owner_stat)
        or not _transient_transcript_owner_is_active(path)
    ):
        return None, False
    unchanged = (
        _same_leaf_identity(current_stat, expected_stat)
        and current_stat.st_size == expected_stat.st_size
        and getattr(current_stat, "st_mtime_ns", 0) == getattr(expected_stat, "st_mtime_ns", 0)
        and getattr(current_stat, "st_ctime_ns", 0) == getattr(expected_stat, "st_ctime_ns", 0)
    )
    if unchanged:
        return current_stat, True
    try:
        observed_text = read_text_without_following_symlinks(
            path,
            field_name="benchmark transcript",
            max_bytes=MAX_STORED_TRANSCRIPT_BYTES,
        )
    except Exception:
        return None, False
    if observed_text.strip() != text.strip():
        return None, False
    return current_stat, True


def _trusted_transcript_stat_for_cleanup(
    value: object,
    expected_path: Path,
) -> os.stat_result | None:
    if getattr(value, "output_path", None) != expected_path:
        return None
    output_stat = getattr(value, "output_stat", None)
    if output_stat is None:
        return None
    if (
        not stat_module.S_ISREG(output_stat.st_mode)
        or getattr(output_stat, "st_nlink", 1) != 1
    ):
        return None
    return output_stat


def _benchmark_model(audio_path: Path, language: str, model: ModelSpec) -> dict[str, object]:
    path: Path | None = None
    downloaded = False
    compatible = False
    metadata_error: BaseException | None = None
    try:
        path = model_path(model)
        status = model_status(model, verify=True)
        downloaded = bool(status.get("downloaded"))
        compatible = model_supports_language(path, language)
    except BaseException as exc:
        metadata_error = exc
    result: dict[str, object] = {
        "model": model.name,
        "path_present": bool(path),
        "downloaded": downloaded,
        "compatible": compatible,
        "ok": False,
        "seconds": None,
        "transcript": "",
        "transcript_output_redacted": False,
        "error": "",
    }
    if metadata_error is not None:
        if isinstance(metadata_error, KeyboardInterrupt):
            _raise_sanitized_transient_exception(
                metadata_error,
                message=TRANSIENT_TRANSCRIPT_PROCESSING_ERROR,
            )
        result["error"] = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
        return result
    if not downloaded:
        result["error"] = f"model is not downloaded: {model.name}"
        return result
    if not compatible:
        result["error"] = f"model does not support language: {language}"
        return result

    started = time.perf_counter()
    def finish_failure(error: str) -> dict[str, object]:
        result["seconds"] = round(time.perf_counter() - started, 3)
        result["error"] = error
        return result

    try:
        text_path, text_path_stat, text_owner_stat = _temporary_benchmark_transcript_path()
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            _raise_sanitized_transient_exception(
                exc,
                message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
            )
        return finish_failure(TRANSIENT_TRANSCRIPT_WRITE_ERROR)
    cleanup_error: BaseException | None = None
    transcribe_failed = False
    deferred_error: BaseException | None = None
    cleanup_stat: os.stat_result | None = text_path_stat
    cleanup_identity_safe = True
    try:
        text = transcribe(
            audio_path=audio_path,
            language=language,
            text_path=text_path,
            command_template="",
            backend=model.backend,
            whisper_model=str(path),
            personal_context="",
            vocabulary="",
            openai_compatible_model=DEFAULT_OPENAI_COMPATIBLE_MODEL,
            openai_compatible_url=DEFAULT_OPENAI_COMPATIBLE_URL,
            openai_compatible_api_key="",
        )
        trusted_output_path = getattr(text, "output_path", None)
        if trusted_output_path is not None:
            trusted_output_stat = getattr(text, "output_stat", None)
            if trusted_output_path != text_path or (
                trusted_output_stat is not None
                and (
                    not stat_module.S_ISREG(trusted_output_stat.st_mode)
                    or getattr(trusted_output_stat, "st_nlink", 1) != 1
                )
            ):
                cleanup_identity_safe = False
                cleanup_error = RuntimeError()
            else:
                cleanup_stat = trusted_output_stat or text_path_stat
        else:
            cleanup_stat, cleanup_identity_safe = _benchmark_transcript_writer_identity(
                text_path,
                text_path_stat,
                text_owner_stat,
                text,
            )
        if not cleanup_identity_safe:
            cleanup_error = RuntimeError()
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            deferred_error = _sanitize_transient_exception(
                exc,
                message=TRANSIENT_TRANSCRIPT_PROCESSING_ERROR,
            )
        else:
            transcribe_failed = True
        text = ""
    finally:
        if cleanup_identity_safe and (transcribe_failed or deferred_error is not None):
            try:
                cleanup_stat, cleanup_identity_safe = _benchmark_transcript_writer_identity(
                    text_path,
                    text_path_stat,
                    text_owner_stat,
                    text,
                )
            except BaseException as exc:
                cleanup_identity_safe = False
                cleanup_error = exc
            else:
                if not cleanup_identity_safe:
                    cleanup_error = RuntimeError()
        if cleanup_identity_safe and cleanup_stat is not None:
            try:
                _remove_transient_transcript_path(
                    text_path,
                    transcript_dir() / ".benchmark-storage.txt",
                    expected_stat=cleanup_stat,
                    expected_owner_stat=text_owner_stat,
                )
            except BaseException as exc:
                cleanup_error = exc

    if cleanup_error is not None:
        if isinstance(cleanup_error, KeyboardInterrupt):
            deferred_error = _sanitize_transient_exception(
                cleanup_error,
                message=TRANSIENT_TRANSCRIPT_CLEANUP_ERROR,
            )
        elif isinstance(deferred_error, KeyboardInterrupt):
            pass
        else:
            return finish_failure(TRANSIENT_TRANSCRIPT_CLEANUP_ERROR)
    if deferred_error is not None:
        raise deferred_error
    if transcribe_failed:
        return finish_failure(TRANSIENT_TRANSCRIPT_PROCESSING_ERROR)
    clean_text = text.strip()
    result["ok"] = True
    result["seconds"] = round(time.perf_counter() - started, 3)
    result["transcript_output_redacted"] = bool(clean_text)
    result["characters"] = len(clean_text)
    result["words"] = len(clean_text.split())
    return result


def command_benchmark_models(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    audio_path = _coerce_path(args.audio_path, field_name="audio file path", max_chars=MAX_AUDIO_PATH_CHARS)
    audio_path = validate_audio_file(audio_path)
    language = _assert_clean_text(args.language or DEFAULT_BENCHMARK_LANGUAGE, field_name="language", max_chars=64).strip()
    if not language:
        language = DEFAULT_BENCHMARK_LANGUAGE
    targets = _benchmark_targets(args.models, language)
    if not targets:
        targets = list(CATALOG)
    results = [_benchmark_model(audio_path, language, model) for model in targets]
    successes = [result for result in results if result.get("ok") and isinstance(result.get("seconds"), (int, float))]
    fastest = min(successes, key=lambda result: float(result["seconds"])) if successes else None
    message = (
        f"benchmarked {len(successes)} of {len(results)} model(s)"
        if successes
        else "no model benchmark completed successfully"
    )
    return {
        "status": "done" if successes else "error",
        "message": message,
        **({} if successes else {"error": message}),
        "audio_path_present": bool(audio_path),
        "language": language,
        "fastest_model": fastest["model"] if fastest else "",
        "results": results,
    }


def command_history(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    limit = _coerce_int(args.limit, field_name="history limit", max_value=MAX_HISTORY_LIMIT)
    confirm_plaintext = _coerce_bool(getattr(args, "confirm_plaintext", False), field_name="confirm_plaintext")
    transcripts, unreadable_count = _collect_transcript_history(limit, include_text=confirm_plaintext)
    if not confirm_plaintext:
        transcripts = _redact_history_previews(transcripts)
    return {"status": "done", "transcripts": transcripts, "unreadable_count": unreadable_count}


def command_transcripts_document(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    limit = _coerce_int(args.limit, field_name="history limit", max_value=MAX_HISTORY_LIMIT)
    confirm_plaintext = _coerce_bool(getattr(args, "confirm_plaintext", False), field_name="confirm_plaintext")
    if not confirm_plaintext:
        raise RuntimeError("plaintext transcript document requires --confirm-plaintext")
    max_chars = MAX_TRANSCRIPTS_DOCUMENT_CHARS
    for _attempt in range(8):
        content, count, truncated = build_transcripts_document(
            limit,
            max_chars=max_chars,
            allow_truncate=True,
        )
        payload = {
            "status": "done",
            "content": content,
            "transcripts": count,
            "truncated": truncated or max_chars < MAX_TRANSCRIPTS_DOCUMENT_CHARS,
        }
        try:
            _assert_json_payload_size(payload, max_bytes=MAX_TRANSCRIPTS_DOCUMENT_JSON_BYTES)
            return payload
        except RuntimeError:
            max_chars = max(256, max_chars // 2)
    raise RuntimeError("transcript document JSON is too large for applet display") from None


def command_transcripts_export(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    limit = _coerce_int(args.limit, field_name="history limit", max_value=MAX_HISTORY_LIMIT)
    plaintext = _coerce_bool(getattr(args, "plaintext", False), field_name="plaintext")
    confirm_plaintext = _coerce_bool(getattr(args, "confirm_plaintext", False), field_name="confirm_plaintext")
    output_path, count, encryption = write_transcripts_export(
        limit,
        encryption_mode=args.artifact_encryption,
        plaintext=plaintext,
        confirm_plaintext=confirm_plaintext,
    )
    opened = False
    if _coerce_bool(getattr(args, "open", False), field_name="open"):
        opened = _open_path_with_desktop(output_path.parent)
    return {
        "status": "done",
        "path_present": bool(output_path),
        "opened": opened,
        "transcripts": count,
        "encryption": encryption,
        "plaintext": plaintext,
        "encrypted": encryption != ARTIFACT_ENCRYPTION_OFF and not plaintext,
    }


def _backup_source_identity(kind: str, path: Path) -> str:
    digest = hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:24]
    return f"{kind}-{digest}"


def _backup_inputs(
    *,
    config: bool,
    transcripts: bool,
    audio: bool,
    settings: dict[str, object],
    alarm_store: dict[str, object],
    settings_path: Path | None = None,
) -> tuple[list[BackupInput], tuple[Path, ...]]:
    settings_path = settings_path or default_settings_export_file()
    sources: list[BackupInput] = []
    if config:
        write_export(settings_path, settings, alarm_store, include_created_at=False)
        sources.append(
            BackupInput(
                "config",
                "config/settings-export.json",
                "settings-export",
                settings_path,
            )
        )
    if transcripts:
        for path in _safe_transcript_artifact_files():
            sources.append(
                BackupInput(
                    "transcript",
                    f"transcripts/{path.name}",
                    _backup_source_identity("transcript", path),
                    path,
                )
            )
    if audio:
        for path in recording_artifact_files():
            if not _is_recording_audio_artifact(path):
                continue
            sources.append(
                BackupInput(
                    "audio",
                    f"audio/{path.name}",
                    _backup_source_identity("audio", path),
                    path,
                )
            )
    return sources, (settings_path.parent, transcript_dir(), recordings_dir())


def command_backup_create(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    target = _coerce_path(args.directory, field_name="backup directory", resolve=True)
    config = _coerce_bool(args.config, field_name="config")
    transcripts = _coerce_bool(args.transcripts, field_name="transcripts")
    audio = _coerce_bool(args.audio, field_name="audio")
    settings_override = getattr(args, "_settings_override", None)
    settings = settings_override if isinstance(settings_override, dict) else _settings_json_from_args(args)
    settings_stage = tempfile.TemporaryDirectory(prefix="soc-backup-settings-") if config else None
    try:
        staged_settings_path = (
            Path(settings_stage.name) / "settings-export.json"
            if settings_stage is not None
            else None
        )
        with _locked_alarm_store() as store_path:
            alarm_store = load_alarm_store(store_path)
            sources, source_roots = _backup_inputs(
                config=config,
                transcripts=transcripts,
                audio=audio,
                settings=settings,
                alarm_store=alarm_store,
                settings_path=staged_settings_path,
            )
        try:
            result = create_backup(
                target,
                sources=sources,
                source_roots=source_roots,
                selection={"config": config, "transcripts": transcripts, "audio": audio},
                app_version=__version__,
                encryption_mode=args.artifact_encryption,
            )
        except BackupError:
            raise
    finally:
        if settings_stage is not None:
            settings_stage.cleanup()
    opened = False
    if _coerce_bool(getattr(args, "open", False), field_name="open") and result.archive_path is not None:
        opened = _open_path_with_desktop(result.archive_path.parent)
    warnings = list(result.warnings)
    return {
        "status": "skipped" if result.skipped else "done",
        "message": (
            "backup skipped; selected artifacts are unchanged"
            if result.skipped
            else "backup created with cleanup warnings"
            if warnings
            else "backup created"
        ),
        "opened": opened,
        "archive_name": result.archive_path.name if result.archive_path is not None else "",
        "archive_path": str(result.archive_path) if result.archive_path is not None else "",
        "archive_present": result.archive_path is not None,
        "encrypted": result.manifest is not None and result.manifest.encryption_enabled,
        "artifacts": len(result.manifest.artifacts) if result.manifest is not None else 0,
        "warnings": warnings,
    }


def _auto_backup_configuration(settings: dict[str, object]) -> dict[str, object] | None:
    if not isinstance(settings, dict):
        raise RuntimeError("automatic backup settings must be an object")
    enabled = _coerce_bool(settings.get("auto-backup-enabled", False), field_name="auto-backup-enabled")
    on_success = _coerce_bool(settings.get("auto-backup-on-success", True), field_name="auto-backup-on-success")
    audio = _coerce_bool(settings.get("auto-backup-audio", False), field_name="auto-backup-audio")
    if not enabled or not on_success or not audio:
        return None
    directory = settings.get("auto-backup-directory", "")
    if not isinstance(directory, str) or not directory.strip():
        raise RuntimeError("automatic audio backup requires a backup directory")
    config = _coerce_bool(settings.get("auto-backup-config", True), field_name="auto-backup-config")
    transcripts = _coerce_bool(settings.get("auto-backup-transcripts", True), field_name="auto-backup-transcripts")
    encryption = normalize_artifact_encryption(settings.get("auto-backup-encryption", "keyring"))
    if not (config or transcripts or audio):
        raise RuntimeError("automatic backup requires at least one selected category")
    return {
        "directory": directory.strip(),
        "config": config,
        "transcripts": transcripts,
        "audio": audio,
        "encryption": encryption,
    }


def _run_inline_auto_backup(
    args: argparse.Namespace,
    settings: dict[str, object],
) -> dict[str, object] | None:
    configuration = _auto_backup_configuration(settings)
    if configuration is None:
        return None
    backup_args = argparse.Namespace(
        directory=configuration["directory"],
        config=configuration["config"],
        transcripts=configuration["transcripts"],
        audio=True,
        artifact_encryption=configuration["encryption"],
        open=False,
        _settings_override=settings,
    )
    result = command_backup_create(backup_args)
    if result.get("status") not in {"done", "skipped"}:
        raise RuntimeError("automatic audio backup did not complete")
    if result.get("status") == "done" and result.get("archive_present") is not True:
        raise RuntimeError("automatic audio backup did not publish an archive")
    result.pop("archive_path", None)
    return result


def command_backup_verify(args: argparse.Namespace) -> dict[str, object]:
    archive_path = _coerce_path(args.archive_path, field_name="backup archive path", resolve=True)
    manifest = verify_backup(archive_path)
    return {
        "status": "done",
        "message": "backup verified",
        "verified": True,
        "encrypted": manifest.encryption_enabled,
        "encryption": manifest.encryption_mode,
        "job_id": manifest.job_id,
        "artifacts": len(manifest.artifacts),
        "selection": dict(manifest.selection),
    }


def command_backup_restore_dry_run(args: argparse.Namespace) -> dict[str, object]:
    archive_path = _coerce_path(args.archive_path, field_name="backup archive path", resolve=True)
    destination = _coerce_path(args.destination_directory, field_name="restore destination", resolve=True)
    plan = restore_dry_run(
        archive_path,
        destination,
        source_roots=(default_settings_export_file().parent, transcript_dir(), recordings_dir()),
    )
    return {
        "status": "done",
        "message": "restore dry-run completed",
        "verified": True,
        "encrypted": plan.manifest.encryption_enabled,
        "artifacts": len(plan.manifest.artifacts),
        "members": len(plan.archive_members),
        "destination_present": destination.exists(),
        "conflicts": list(plan.conflicts),
        "conflict_count": len(plan.conflicts),
    }


def command_backup_restore(args: argparse.Namespace) -> dict[str, object]:
    archive_path = _coerce_path(args.archive_path, field_name="backup archive path", resolve=True)
    destination = _coerce_path(args.destination_directory, field_name="restore destination", resolve=True)
    plan = restore_backup(
        archive_path,
        destination,
        source_roots=(default_settings_export_file().parent, transcript_dir(), recordings_dir()),
    )
    return {
        "status": "done",
        "message": "backup restored",
        "verified": True,
        "encrypted": plan.manifest.encryption_enabled,
        "artifacts": len(plan.manifest.artifacts),
        "members": len(plan.archive_members),
        "destination": str(destination),
        "conflicts": [],
        "conflict_count": 0,
    }


def command_cleanup(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    lifecycle_lock_active = _is_finalization_lock_active(store.path)
    if lifecycle_lock_active:
        return _command_cleanup_locked(args, store, lifecycle_lock_active=True)
    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        return {
            "status": "finalizing",
            "message": "recording lifecycle in progress; wait for completion",
        }
    try:
        return _command_cleanup_locked(args, store, lifecycle_lock_active=False)
    finally:
        _release_finalization_lock(lock_path)


def _command_cleanup_locked(
    args: argparse.Namespace,
    store: StateStore,
    *,
    lifecycle_lock_active: bool,
) -> dict[str, object]:
    keep_transcripts = _coerce_int(args.keep_transcripts, field_name="keep-transcripts", max_value=MAX_KEEP_TRANSCRIPTS)
    keep_recordings = _coerce_int(args.keep_recordings, field_name="keep-recordings", max_value=MAX_KEEP_RECORDINGS)
    recording_max_age_days = _coerce_int(
        args.recording_max_age_days,
        field_name="recording-max-age-days",
        max_value=MAX_RECORDING_MAX_AGE_DAYS,
    )
    dry_run = _coerce_bool(args.dry_run, field_name="dry-run")
    state = store.read()
    _raise_if_state_unreadable(state)
    include_finalizing_inflight = lifecycle_lock_active
    if not include_finalizing_inflight and state.status == "finalizing":
        state_audio_path = _normalized_state_recording_artifact_path(
            state.audio_path,
            suffix=(".wav", ".flac", ".socenc"),
            state_path=store.path,
            require_recordings_dir=True,
        )
        include_finalizing_inflight = (
            state_audio_path is None or _recording_artifact_stat(state_audio_path) is None
        )
    active_paths = active_artifact_paths(
        state,
        state_path=store.path,
        include_finalizing_inflight=include_finalizing_inflight,
    )
    if lifecycle_lock_active and not any(
        path.suffix.lower() == ".log" or _is_recording_audio_artifact(path)
        for path in active_paths
    ):
        try:
            active_paths.update(recording_artifact_files())
        except DirectoryScanError:
            pass
        try:
            active_paths.update(_safe_transcript_artifact_files())
        except DirectoryScanError:
            pass
    transcript_stats: dict[Path, os.stat_result] = {}
    try:
        transcript_files = _safe_transcript_artifact_files(expected_stats=transcript_stats)
    except DirectoryScanError as exc:
        transcript_result = {
            "planned_paths": [],
            "deleted_paths": [],
            "failed_paths": [str(exc.directory)],
            "skipped_active_paths": [],
        }
    else:
        transcript_files, empty_transcript_files = _partition_transcript_cleanup_files(transcript_files)
        retention_result = prune_transcript_files_by_mtime(
            transcript_files,
            keep_transcripts,
            active_paths,
            dry_run,
            expected_stats=transcript_stats,
        )
        empty_result = prune_transcript_files_by_mtime(
            empty_transcript_files,
            0,
            active_paths,
            dry_run,
            expected_stats=transcript_stats,
        )
        transcript_result = {
            key: [*empty_result[key], *retention_result[key]]
            for key in ("planned_paths", "deleted_paths", "failed_paths", "skipped_active_paths")
        }
    transient_transcript_result = prune_stale_transient_transcripts(dry_run)
    recording_result = prune_recording_groups(keep_recordings, active_paths, dry_run, recording_max_age_days)
    deleted_transcripts = len(transcript_result["deleted_paths"])
    deleted_transient_transcripts = len(transient_transcript_result["deleted_paths"])
    deleted_recordings = _coerce_int(recording_result["deleted_recordings"], field_name="deleted-recordings")  # type: ignore[arg-type]
    deleted_logs = _coerce_int(recording_result["deleted_logs"], field_name="deleted-logs")  # type: ignore[arg-type]
    would_delete_transcripts = len(transcript_result["planned_paths"])
    would_delete_transient_transcripts = len(transient_transcript_result["planned_paths"])
    would_delete_recordings = _coerce_int(recording_result["planned_recordings"], field_name="planned-recordings")  # type: ignore[arg-type]
    would_delete_logs = _coerce_int(recording_result["planned_logs"], field_name="planned-logs")  # type: ignore[arg-type]
    total = (
        would_delete_transcripts + would_delete_transient_transcripts + would_delete_recordings + would_delete_logs
        if dry_run
        else deleted_transcripts + deleted_transient_transcripts + deleted_recordings + deleted_logs
    )
    verb = "would clean" if dry_run else "cleaned"
    failed_paths = transcript_result["failed_paths"] + transient_transcript_result["failed_paths"] + recording_result["failed_paths"]
    deleted_paths = transcript_result["deleted_paths"] + transient_transcript_result["deleted_paths"] + recording_result["deleted_paths"]
    would_delete_paths = transcript_result["planned_paths"] + transient_transcript_result["planned_paths"] + recording_result["planned_paths"]
    skipped_active_paths = (
        transcript_result["skipped_active_paths"]
        + transient_transcript_result["skipped_active_paths"]
        + recording_result["skipped_active_paths"]
    )
    status = "error" if failed_paths else "done"
    message = f"{verb} {total} old file(s)"
    if status == "error":
        message = f"{message}; failed to scan or delete {len(failed_paths)} file(s)"
    return {
        "status": status,
        "message": message,
        **({"error": message} if status == "error" else {}),
        "dry_run": dry_run,
        "keep_transcripts": keep_transcripts,
        "keep_recordings": keep_recordings,
        "recording_max_age_days": recording_max_age_days,
        "deleted_transcripts": deleted_transcripts,
        "deleted_transient_transcripts": deleted_transient_transcripts,
        "deleted_recordings": deleted_recordings,
        "deleted_logs": deleted_logs,
        "would_delete_transcripts": would_delete_transcripts,
        "would_delete_transient_transcripts": would_delete_transient_transcripts,
        "would_delete_recordings": would_delete_recordings,
        "would_delete_logs": would_delete_logs,
        "deleted_path_count": len(deleted_paths),
        "would_delete_path_count": len(would_delete_paths),
        "failed_path_count": len(failed_paths),
        "skipped_active_path_count": len(skipped_active_paths),
        "deleted_paths": [],
        "would_delete_paths": [],
        "failed_paths": [],
        "skipped_active_paths": [],
    }


def command_diagnostics(args: argparse.Namespace) -> dict[str, object]:
    payload = build_diagnostics_payload(args)
    output = str(getattr(args, "output", "") or "").strip()
    save = _coerce_bool(getattr(args, "save", False), field_name="save")
    if output or save:
        _harden_diagnostics_artifacts()
        path = (
            _require_json_path(output, field_name="diagnostics output")
            if output
            else diagnostics_dir() / f"diagnostics-{timestamp()}.json"
        )
        _assert_json_payload_size(payload, max_bytes=MAX_DIAGNOSTICS_JSON_BYTES)
        _write_json_atomic(path, payload, max_bytes=MAX_DIAGNOSTICS_JSON_BYTES)
        payload["saved_path_present"] = True
        payload["message"] = "diagnostics saved"
    return payload


def command_record_error(args: argparse.Namespace) -> dict[str, object]:
    raw = sys.stdin.read(MAX_LOG_MESSAGE_CHARS * 8)
    if len(raw) >= MAX_LOG_MESSAGE_CHARS * 8:
        raise RuntimeError("error record is too large")
    try:
        payload = json.loads(
            raw or "{}",
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
        )
    except (json.JSONDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise RuntimeError("error record is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("error record must be an object")
    group = payload.get("group", "unknown")
    message = payload.get("message", "")
    if type(group) is not str:
        raise RuntimeError("error group must be text")
    if type(message) is not str:
        raise RuntimeError("error message must be text")
    secret_values = _known_cli_secret_values(args)
    raw_group = group.strip()
    if len(raw_group) > 128:
        group = "unknown"
    else:
        redacted_group = _redact_known_cli_secrets(raw_group, secret_values)
        if redacted_group != raw_group:
            group = "redacted"
        elif _ERROR_RECORD_GROUP_RE.fullmatch(raw_group) is not None:
            group = raw_group
        else:
            group = "unknown"
    message = _redact_known_cli_secrets(message.strip(), secret_values)
    message = sanitize_error_message(message, max_chars=MAX_LOG_MESSAGE_CHARS)
    if not message:
        raise RuntimeError("error message must not be empty")
    log_event("error", "applet_error", group=group, error_message=message)
    return {"status": "done", "logged": True}


def command_alarms_list(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return list_alarm_payload()


def command_alarms_add(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    disabled = _coerce_bool(args.disabled, field_name="disabled")
    alarm = add_alarm(
        args.time,
        name=args.name,
        days=args.days,
        urgency=args.urgency,
        enabled=not disabled,
    )
    return {"status": "done", "message": f"alarm added: {alarm['label']} at {alarm['time']}", "alarm": alarm}


def command_alarms_remove(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return remove_alarm(args.id)


def command_alarms_enable(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return set_alarm_enabled(args.id, True)


def command_alarms_disable(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return set_alarm_enabled(args.id, False)


def command_alarms_check(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    mark = _coerce_bool(args.mark, field_name="mark")
    catch_up_minutes = _coerce_int(args.catch_up_minutes, field_name="catch-up-minutes", max_value=MAX_ALARM_CATCH_UP_MINUTES)
    return check_due_alarms(mark=mark, catch_up_minutes=catch_up_minutes)


def command_alarms_import(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    raw = sys.stdin.read(MAX_SETTINGS_JSON_CHARS + 1)
    if len(raw) > MAX_SETTINGS_JSON_CHARS:
        raise RuntimeError(f"alarm JSON is too large (max {MAX_SETTINGS_JSON_CHARS} characters)")
    try:
        value = json.loads(
            raw,
            parse_constant=_reject_non_finite_json_number,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise RuntimeError("alarm JSON could not be parsed") from exc
    try:
        alarm_store = normalize_alarm_store(value)
    except SettingsExportError as exc:
        raise RuntimeError("alarm JSON is invalid") from exc
    with _locked_alarm_store() as store_path:
        save_alarm_store(alarm_store, store_path)
    return {
        "status": "done",
        "message": "alarms imported",
        "alarms_count": len(alarm_store["alarms"]),
    }


def _diagnostics_state_payload(state: RecordingState) -> dict[str, object]:
    transcript_length = len(state.transcript) if type(state.transcript) is str else 0
    error = state.error if type(state.error) is str else ""
    payload: dict[str, object] = {
        "status": state.status,
        "pid": state.pid,
        "started_at": state.started_at,
        "stopped_at": state.stopped_at,
        "language": state.language,
        "recorder": state.recorder,
        "input_device": state.input_device,
        "max_seconds": state.max_seconds,
        "inserted": state.inserted,
        "updated_at": state.updated_at,
        "transcript_length": transcript_length,
        "audio_path_present": bool(state.audio_path),
        "log_path_present": bool(state.log_path),
        "transcript_path_present": bool(state.transcript_path),
        "process_identity_present": bool(state.process_identity),
        "pending_cleanup_owner_path_count": min(
            len(state.pending_cleanup_owner_paths),
            100_000,
        ),
        "pending_cleanup_restore_owner_path_count": min(
            len(state.pending_cleanup_restore_owner_paths),
            100_000,
        ),
        "pending_cleanup_backup_entry_count": min(
            len(state.pending_cleanup_backup_entries),
            100_000,
        ),
        "cleanup_backup_journal_overflow": state.cleanup_backup_journal_overflow
        is True,
        "cleanup_backup_journal_restore": state.cleanup_backup_journal_restore
        is True,
        "recorder_scope_present": bool(state.recorder_scope),
    }
    if error.strip():
        payload["error"] = _redact_error_for_user(error)
    return payload


def _diagnostics_applet_lifecycle_payload(settings: dict[str, object]) -> dict[str, object]:
    raw = settings.get("applet-lifecycle")
    if not isinstance(raw, dict):
        return {
            "present": False,
            "state": "unknown",
            "error_counts": {},
            "disabled_groups": [],
            "resources": {},
            "process_groups": {},
        }
    allowed_states = {"INITIALIZING", "RUNNING", "DEGRADED", "REMOVING", "REMOVED"}
    state = str(raw.get("state") or "unknown")
    if state not in allowed_states:
        state = "unknown"

    def safe_group(value: object) -> str:
        text = str(value or "")
        if not text or len(text) > 64 or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
            return ""
        return text

    def safe_count(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return max(0, min(100_000, value))

    error_counts: dict[str, int] = {}
    raw_errors = raw.get("error_counts")
    if isinstance(raw_errors, dict):
        for key, value in list(raw_errors.items())[:64]:
            group = safe_group(key)
            if group:
                error_counts[group] = safe_count(value)

    disabled_groups: list[str] = []
    raw_disabled = raw.get("disabled_groups")
    if isinstance(raw_disabled, list):
        for value in raw_disabled[:64]:
            group = safe_group(value)
            if group and group not in disabled_groups:
                disabled_groups.append(group)
    disabled_groups.sort()

    resources: dict[str, int] = {}
    raw_resources = raw.get("resources")
    if isinstance(raw_resources, dict):
        for key, value in list(raw_resources.items())[:32]:
            group = safe_group(key)
            if group:
                resources[group] = safe_count(value)

    process_groups: dict[str, int] = {}
    raw_process_groups = raw.get("process_groups")
    if isinstance(raw_process_groups, dict):
        for key, value in list(raw_process_groups.items())[:32]:
            group = safe_group(key)
            if group:
                process_groups[group] = safe_count(value)

    return {
        "present": True,
        "state": state,
        "error_counts": error_counts,
        "disabled_groups": disabled_groups,
        "resources": resources,
        "process_groups": process_groups,
    }


def build_diagnostics_payload(args: argparse.Namespace) -> dict[str, object]:
    settings = _settings_json_from_args(args)
    ensure_runtime_dirs()
    applet = _coerce_bool(getattr(args, "applet", False), field_name="applet")
    alarm_payload = list_alarm_payload()
    if not isinstance(alarm_payload, dict):
        raise RuntimeError("alarms payload must be an object")
    alarm_entries = alarm_payload.get("alarms", [])
    if not isinstance(alarm_entries, list):
        raise RuntimeError("alarms entries must be a list")
    source_payload: dict[str, object]
    try:
        source_items_raw = _normalize_input_sources(list_input_sources(False))
        source_items: list[dict[str, object]] = [
            {
                "name": source["name"],
                "description": source["description"],
                "default": source["default"],
                "state": source["state"],
            }
            for source in source_items_raw
        ]
        source_payload = {
            "ok": True,
            "sources": source_items,
        }
    except Exception as exc:
        source_payload = {"ok": False, "error": _redact_error_for_user(str(exc))}

    transcript_entries = [
        {key: entry[key] for key in ("name", "modified_at") if key in entry}
        for entry in _redact_history_previews(read_transcript_history(5))
    ]
    state_payload = _diagnostics_state_payload(build_store(args).read())
    state_file_path = normalized_path(args.state_file)
    if state_file_path is None:
        state_file_path = _coerce_path(str(args.state_file), field_name="state file")
    desktop = _coerce_desktop_payload()
    return {
        "status": "done",
        "message": "diagnostics collected",
        "app": {
            "id": APP_ID,
            "name": APP_NAME,
            "version": __version__,
        },
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
        },
        "desktop": {
            "current_desktop": str(desktop["current_desktop"]),
            "session_type": str(desktop["session_type"]),
            "desktop_session": str(desktop["desktop_session"]),
        },
        "paths": {
            "state_dir_present": bool(state_dir()),
            "state_file_present": state_file_path.is_file(),
            "transcript_dir_present": bool(transcript_dir()),
            "recordings_dir_present": bool(recordings_dir()),
            "diagnostics_dir_present": bool(diagnostics_dir()),
            "redacted": True,
        },
        "state": state_payload,
        "applet_lifecycle": _diagnostics_applet_lifecycle_payload(settings),
        "doctor": doctor_report(settings, applet=applet),
        "inputs": source_payload,
        "models": _redact_model_payload_paths(list_models()),
        "alarms": {
            "configured": len(alarm_entries),
            "active": sum(1 for alarm in alarm_entries if isinstance(alarm, dict) and alarm.get("enabled", True)),
            "last_checked_at": str(alarm_payload.get("last_checked_at") or ""),
        },
        "recent_transcripts": transcript_entries,
    }


def command_settings_export(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    settings = _settings_json_from_args(args)
    path = _require_json_path(
        args.output,
        field_name="settings export output",
        default=default_settings_export_file(),
        max_chars=_settings_json_path_limit(args.output),
    )
    with _locked_alarm_store() as store_path:
        alarm_store = load_alarm_store(store_path)
        payload = write_export(path, settings, alarm_store)
    result: dict[str, object] = {
        "status": "done",
        "message": "settings exported",
        "path_present": bool(path),
        "settings_count": len(payload["settings"]),
        "alarms_count": len(payload["alarms"]["alarms"]),
    }
    raw_warnings = payload.get("post_commit_warnings", [])
    allowed_warnings = {
        POST_COMMIT_RECOVERY_BACKUP_CLEANUP_WARNING,
        POST_COMMIT_DIRECTORY_CLOSE_WARNING,
    }
    post_commit_warnings: list[str] = []
    if isinstance(raw_warnings, list):
        for warning in raw_warnings:
            if isinstance(warning, str) and warning in allowed_warnings and warning not in post_commit_warnings:
                post_commit_warnings.append(warning)
    if post_commit_warnings:
        result.update(
            {
                "status": "warning",
                "cleanup_warning": True,
                "message": post_commit_warnings[0],
                "warnings": list(post_commit_warnings),
            }
        )
    return result


def command_settings_import(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    preview = _coerce_bool(getattr(args, "preview", False), field_name="preview")
    path = _require_json_path(
        args.input,
        field_name="settings import input",
        default=default_settings_export_file(),
        max_chars=_settings_json_path_limit(args.input),
    )
    payload = read_export(path)
    include_settings = _coerce_bool(
        getattr(args, "confirm_plaintext_settings_output", False),
        field_name="confirm_plaintext_settings_output",
    )
    if not preview:
        with _locked_alarm_store() as store_path:
            _save_alarm_store_unlocked(payload["alarms"], store_path)
    result: dict[str, object] = {
        "status": "done",
        "message": "settings imported",
        "path_present": bool(path),
        "settings_count": len(payload["settings"]),
        "alarms_count": len(payload["alarms"]["alarms"]),
        "export_version": payload["version"],
    }
    if preview:
        result["preview"] = True
        result["alarms"] = payload["alarms"]
    if include_settings:
        result["settings"] = payload["settings"]
    else:
        result["settings_redacted"] = True
    return result


def write_profanity_filter_document() -> tuple[Path, int]:
    path = _ensure_editable_profanity_filter_file()
    pairs = _profanity_replacement_pairs_from_file()
    return path, len(pairs)


def command_profanity_filter_document(args: argparse.Namespace) -> dict[str, object]:
    path, entries = write_profanity_filter_document()
    opened = False
    if _coerce_bool(getattr(args, "open", False), field_name="open"):
        opened = _open_path_with_desktop(path)
    return {
        "status": "done",
        "path_present": bool(path),
        "opened": opened,
        "entries": entries,
        "editable": True,
    }


def command_insert_text(args: argparse.Namespace) -> dict[str, object]:
    text = _assert_clean_text(args.text, field_name="text", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    sanitize_special_chars_flag = _coerce_bool(args.sanitize_special_chars, field_name="sanitize_special_chars")
    append_space = _coerce_bool(getattr(args, "append_space", False), field_name="append_space")
    soften_profanity = _coerce_bool(getattr(args, "soften_profanity", False), field_name="soften_profanity")
    text = prepare_output_text(text, append_space, sanitize_special_chars_flag, soften_profanity)
    typing_delay_ms = _coerce_int(args.typing_delay_ms, field_name="typing-delay-ms", max_value=MAX_TYPING_DELAY_MS)
    try:
        inserted = insert_text(text, args.insert_method, typing_delay_ms)
    except (KeyboardInterrupt, SystemExit) as exc:
        _raise_foreign_backend_sanitized_exception(
            exc,
            message=TRANSIENT_TRANSCRIPT_INSERT_UNCERTAIN_ERROR,
        )
    return {"status": "done", "inserted": inserted}


def command_transcribe_file(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    audio_path = _coerce_path(args.audio_path, field_name="audio file path", max_chars=MAX_AUDIO_PATH_CHARS)
    language = _validate_pipeline_text_args(args, language=args.language)
    normalized_transcriber = normalize_backend(args.transcriber)
    audio_path = validate_audio_file(audio_path)
    text_path = _transcript_path_for_audio(audio_path)
    artifact_encryption = _artifact_encryption_mode(args)
    transcriber_text_path = _transcript_work_path(text_path, artifact_encryption)
    preparation_error: BaseException | None = None
    transient_text_fd: int | None = None
    transient_owner_stat: os.stat_result | None = None
    try:
        transient_text_fd, transient_owner_stat = _prepare_transient_transcript_path(
            transcriber_text_path,
            text_path,
        )
    except BaseException as exc:
        preparation_error = exc
    if preparation_error is not None:
        _raise_backend_sanitized_exception(
            preparation_error,
            message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
        )
    transcription_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    try:
        text = transcribe(
            audio_path=audio_path,
            language=language,
            text_path=transcriber_text_path,
            command_template=args.transcriber_command,
            backend=normalized_transcriber,
            whisper_model=args.whisper_model,
            personal_context=args.personal_context,
            vocabulary=args.vocabulary,
            **_openai_compatible_transcribe_kwargs(args, normalized_transcriber),
        )
    except BaseException as exc:
        transcription_error = exc
        text = ""
    try:
        _remove_transient_transcript_path(
            transcriber_text_path,
            text_path,
            expected_fd=transient_text_fd,
            expected_stat=_trusted_transcript_stat_for_cleanup(
                text,
                transcriber_text_path,
            ),
            expected_owner_stat=transient_owner_stat,
        )
    except BaseException as cleanup_exc:
        cleanup_error = cleanup_exc
    if cleanup_error is not None:
        raise _transcription_cleanup_exception(
            transcription_error,
            cleanup_error,
            stable_public_error=True,
        ) from None
    if transcription_error is not None:
        if isinstance(transcription_error, TranscriptionCleanupError):
            failure_message = TRANSIENT_TRANSCRIPT_CLEANUP_ERROR
            classified_failure = None
        elif isinstance(transcription_error, Exception):
            classified_failure = _classified_transcription_failure(
                transcription_error
            )
            failure_message = str(classified_failure)
        else:
            classified_failure = None
            failure_message = TRANSIENT_TRANSCRIPT_PROCESSING_ERROR
        if classified_failure is not None:
            _clear_transient_exception_metadata(transcription_error)
            raise _fresh_classified_public_error(
                classified_failure,
                failure_message,
            ) from None
        _raise_backend_sanitized_exception(
            transcription_error,
            message=failure_message,
        )
    postprocess_error: BaseException | None = None
    try:
        if _is_empty_transcript_text(text):
            text = ""
            security_post_processing = _empty_security_post_processing()
        else:
            text, security_post_processing = _process_transcript(text, args, language)
    except BaseException as exc:
        postprocess_error = exc
    if isinstance(postprocess_error, (Exception, SystemExit)):
        classified_failure = _postprocess_public_failure(postprocess_error)
        _clear_transient_exception_metadata(postprocess_error)
        raise _fresh_classified_public_error(
            classified_failure,
            str(classified_failure),
        ) from None
    if postprocess_error is not None:
        _raise_backend_sanitized_exception(
            postprocess_error,
            message=TRANSIENT_TRANSCRIPT_PROCESSING_ERROR,
        )
    stripped_text = text.strip()
    if not stripped_text:
        text = ""
    stored_text_path: Path | None = None
    transcript_encryption = ARTIFACT_ENCRYPTION_OFF
    if stripped_text:
        try:
            stored_text_path, transcript_encryption = _write_stored_transcript(
                text_path,
                stripped_text + "\n",
                args,
            )
        except BaseException as exc:
            _raise_backend_sanitized_exception(
                exc,
                message=TRANSIENT_TRANSCRIPT_WRITE_ERROR,
            )
    keep_transcripts = _coerce_int(
        getattr(args, "keep_transcripts", DEFAULT_KEEP_TRANSCRIPTS),
        field_name="keep-transcripts",
        max_value=MAX_KEEP_TRANSCRIPTS,
    )
    transcript_stats: dict[Path, os.stat_result] = {}
    transcript_files = _safe_transcript_artifact_files(expected_stats=transcript_stats)
    transcript_cleanup = prune_transcript_files_by_mtime(
        transcript_files,
        keep_transcripts,
        {stored_text_path} if stored_text_path is not None else set(),
        False,
        expected_stats=transcript_stats,
    )
    transient_transcript_cleanup = prune_stale_transient_transcripts(False)
    cleanup_failed_paths = _cleanup_failed_paths(transcript_cleanup, transient_transcript_cleanup)
    status = "done"
    message = "recording finished without transcript" if not stripped_text else "transcription completed"
    if cleanup_failed_paths:
        status = "error"
        message = f"{message}; {_cleanup_failure_error(cleanup_failed_paths)}"
    reveal_transcript = _confirm_plaintext_transcript_output(args)
    return {
        "status": status,
        "message": message,
        **({"error": message, "cleanup_failed_path_count": len(cleanup_failed_paths)} if cleanup_failed_paths else {}),
        "transcript": text if reveal_transcript else "",
        "transcript_output_redacted": bool(text) and not reveal_transcript,
        **(
            {"transcript_path": str(stored_text_path)}
            if stored_text_path is not None and reveal_transcript
            else {"transcript_path_present": stored_text_path is not None}
        ),
        "security": _public_security_post_processing(security_post_processing),
        "transcript_file_cap": _public_cleanup_result(transcript_cleanup),
        "transient_transcript_cleanup": _public_cleanup_result(transient_transcript_cleanup),
        "artifact_encryption": artifact_encryption,
        "transcript_encryption": transcript_encryption,
        "transcript_encrypted": transcript_encryption != ARTIFACT_ENCRYPTION_OFF,
    }


def add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-file", default=str(default_state_file()))
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--log-level",
        default=_coerce_log_level_from_environment(),
        choices=LOG_LEVELS,
        help="write logs at this level; default: error",
    )


def add_pipeline_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--language", default="")
    parser.add_argument("--max-seconds", type=int, default=DEFAULT_MAX_SECONDS)
    parser.add_argument("--recorder", default="auto", choices=["auto", "pw-record", "parecord", "arecord"])
    parser.add_argument("--input-device", default="")
    parser.add_argument("--transcriber", default="auto", choices=TRANSCRIBER_CHOICES)
    parser.add_argument("--transcriber-command", default="")
    parser.add_argument("--whisper-model", default="")
    parser.add_argument(
        "--post-process-backend",
        default="none",
        choices=["none", "command", "ollama", "openai-compatible", "openai", "local-openai"],
    )
    parser.add_argument("--post-process-command", default="")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    parser.add_argument("--openai-compatible-model", default=DEFAULT_OPENAI_COMPATIBLE_MODEL)
    parser.add_argument("--openai-compatible-text-model", default=DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL)
    parser.add_argument(
        "--openai-compatible-api-key-stdin",
        action="store_true",
        help="read OpenAI-compatible API key from stdin; otherwise use OPENAI_COMPATIBLE_API_KEY",
    )
    parser.add_argument(
        "--openai-compatible-flex-processing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use OpenAI-compatible flex processing for speech-to-text and text polishing requests; default: enabled",
    )
    parser.add_argument("--post-process-prompt", default="")
    parser.add_argument("--personal-context", default="")
    parser.add_argument("--vocabulary", default="")
    parser.add_argument("--settings-json", default="{}")
    parser.add_argument(
        "--settings-json-stdin",
        action="store_true",
        help="read private settings JSON from stdin for lifecycle-gated operations",
    )
    parser.add_argument(
        "--insert-method",
        default="clipboard-paste",
        choices=["clipboard-paste", "clipboard-paste-submit", "clipboard", "type", "none"],
    )
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--keep-transcripts", type=int, default=DEFAULT_KEEP_TRANSCRIPTS)
    parser.add_argument(
        "--artifact-encryption",
        default=ARTIFACT_ENCRYPTION_OFF,
        choices=ARTIFACT_ENCRYPTION_CHOICES,
        help=(
            "encrypt stored transcripts and retained recordings: off, passphrase, or keyring; "
            "keyring fails closed if Secret Service is unavailable; choose passphrase explicitly when needed; "
            "passphrase uses SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE_FILE, an existing "
            "~/.config/speed-of-cinnamon/artifact.key, SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE, "
            "or generates ~/.config/speed-of-cinnamon/artifact.key at runtime; weak default key files are regenerated"
        ),
    )
    parser.add_argument("--sanitize-special-chars", action="store_true")
    parser.add_argument("--soften-profanity", action="store_true")
    parser.add_argument("--append-space", action="store_true")
    parser.add_argument(
        "--confirm-plaintext-output",
        action="store_true",
        help="allow full transcript text in command output even when the stored transcript is encrypted",
    )
    parser.add_argument(
        "--keep-recording-artifacts",
        action="store_true",
        help="keep temporary FLAC/log files after successful transcription",
    )
    parser.add_argument(
        "--skip-silent-auto-relisten",
        action="store_true",
        help=(
            "compatibility flag; silent recordings are always skipped before transcription "
            "so empty recordings never reach clipboard or paste"
        ),
    )


class _ArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args: object, **kwargs: object) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="speed-of-cinnamon")
    parser.add_argument(
        "--version",
        action="version",
        version=f"speed-of-cinnamon {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, handler in [("start", command_start), ("stop", command_stop), ("toggle", command_toggle)]:
        child = subparsers.add_parser(name)
        add_common_options(child)
        add_pipeline_options(child)
        child.set_defaults(handler=handler)

    cancel = subparsers.add_parser("cancel")
    add_common_options(cancel)
    cancel.set_defaults(handler=command_cancel)

    status = subparsers.add_parser("status")
    add_common_options(status)
    status.add_argument(
        "--confirm-plaintext-output",
        action="store_true",
        help="allow confirmed transcript recovery for the local applet",
    )
    status.set_defaults(handler=command_status)

    doctor = subparsers.add_parser("doctor")
    add_common_options(doctor)
    doctor.add_argument("--settings-json", default="")
    doctor.add_argument(
        "--settings-json-stdin",
        action="store_true",
        help="read settings JSON from stdin instead of exposing it in process arguments",
    )
    doctor.add_argument(
        "--applet",
        action="store_true",
        help="evaluate output readiness for the Cinnamon applet path",
    )
    doctor.set_defaults(handler=command_doctor)

    setup = subparsers.add_parser("setup")
    add_common_options(setup)
    setup.add_argument("--settings-json", default="")
    setup.add_argument(
        "--settings-json-stdin",
        action="store_true",
        help="read settings JSON from stdin instead of exposing it in process arguments",
    )
    setup.add_argument(
        "--applet",
        action="store_true",
        help="build setup steps for the Cinnamon applet path",
    )
    setup.set_defaults(handler=command_setup)

    list_inputs = subparsers.add_parser("list-inputs")
    add_common_options(list_inputs)
    list_inputs.add_argument("--include-monitors", action="store_true")
    list_inputs.set_defaults(handler=command_list_inputs)

    models = subparsers.add_parser("models")
    add_common_options(models)
    models.set_defaults(handler=command_models)

    text_models = subparsers.add_parser("text-models")
    add_common_options(text_models)
    text_models.add_argument("--backend", default="ollama", choices=["ollama", "openai-compatible"])
    text_models.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    text_models.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    text_models.add_argument(
        "--openai-compatible-api-key-stdin",
        action="store_true",
        help="read OpenAI-compatible API key from stdin; otherwise use OPENAI_COMPATIBLE_API_KEY",
    )
    text_models.set_defaults(handler=command_text_models)

    install_text_model = subparsers.add_parser("install-text-model")
    add_common_options(install_text_model)
    install_text_model.add_argument("--backend", default="ollama", choices=["ollama"])
    install_text_model.add_argument("--model", required=True)
    install_text_model.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    install_text_model.set_defaults(handler=command_install_text_model)

    download_model_parser = subparsers.add_parser("download-model")
    add_common_options(download_model_parser)
    download_model_parser.add_argument("model")
    download_model_parser.add_argument("--force", action="store_true")
    download_model_parser.set_defaults(handler=command_download_model)

    remove_model_parser = subparsers.add_parser("remove-model")
    add_common_options(remove_model_parser)
    remove_model_parser.add_argument("model")
    remove_model_parser.set_defaults(handler=command_remove_model)

    benchmark_models = subparsers.add_parser("benchmark-models")
    add_common_options(benchmark_models)
    benchmark_models.add_argument("audio_path")
    benchmark_models.add_argument("--language", default=DEFAULT_BENCHMARK_LANGUAGE)
    benchmark_models.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="catalog model names to compare; defaults to downloaded compatible models",
    )
    benchmark_models.set_defaults(handler=command_benchmark_models)

    history = subparsers.add_parser("history")
    add_common_options(history)
    history.add_argument("--limit", type=int, default=10)
    history.add_argument(
        "--confirm-plaintext",
        action="store_true",
        help="confirm that recent transcript previews are intentional",
    )
    history.set_defaults(handler=command_history)

    transcripts_document = subparsers.add_parser("transcripts-document")
    add_common_options(transcripts_document)
    transcripts_document.add_argument("--limit", type=int, default=MAX_HISTORY_LIMIT)
    transcripts_document.add_argument(
        "--confirm-plaintext",
        action="store_true",
        help="confirm that plaintext transcript display is intentional",
    )
    transcripts_document.set_defaults(handler=command_transcripts_document)

    transcripts_export = subparsers.add_parser("transcripts-export")
    add_common_options(transcripts_export)
    transcripts_export.add_argument("--limit", type=int, default=MAX_HISTORY_LIMIT)
    transcripts_export.add_argument(
        "--artifact-encryption",
        default="keyring",
        choices=ARTIFACT_ENCRYPTION_CHOICES,
        help="encrypt exported transcript bundle; default is keyring",
    )
    transcripts_export.add_argument(
        "--plaintext",
        action="store_true",
        help="write plaintext transcript export; also requires --confirm-plaintext",
    )
    transcripts_export.add_argument(
        "--confirm-plaintext",
        action="store_true",
        help="confirm that plaintext transcript export is intentional",
    )
    transcripts_export.add_argument(
        "--open",
        action="store_true",
        help="open the encrypted export folder without returning its path",
    )
    transcripts_export.set_defaults(handler=command_transcripts_export)

    backup = subparsers.add_parser("backup")
    backup_subparsers = backup.add_subparsers(dest="backup_command", required=True)

    backup_create = backup_subparsers.add_parser("create")
    add_common_options(backup_create)
    backup_create.add_argument("--directory", required=True)
    backup_create.add_argument("--settings-json", default="{}")
    backup_create.add_argument("--settings-json-stdin", action="store_true")
    backup_create.add_argument("--config", action=argparse.BooleanOptionalAction, default=True)
    backup_create.add_argument("--transcripts", action=argparse.BooleanOptionalAction, default=True)
    backup_create.add_argument("--audio", action=argparse.BooleanOptionalAction, default=False)
    backup_create.add_argument("--artifact-encryption", choices=ARTIFACT_ENCRYPTION_CHOICES, default="keyring")
    backup_create.add_argument("--open", action="store_true")
    backup_create.set_defaults(handler=command_backup_create)

    backup_verify = backup_subparsers.add_parser("verify")
    add_common_options(backup_verify)
    backup_verify.add_argument("archive_path")
    backup_verify.set_defaults(handler=command_backup_verify)

    backup_restore = backup_subparsers.add_parser("restore-dry-run")
    add_common_options(backup_restore)
    backup_restore.add_argument("archive_path")
    backup_restore.add_argument("destination_directory")
    backup_restore.set_defaults(handler=command_backup_restore_dry_run)

    backup_restore_apply = backup_subparsers.add_parser("restore")
    add_common_options(backup_restore_apply)
    backup_restore_apply.add_argument("archive_path")
    backup_restore_apply.add_argument("destination_directory")
    backup_restore_apply.set_defaults(handler=command_backup_restore)

    cleanup = subparsers.add_parser("cleanup")
    add_common_options(cleanup)
    cleanup.add_argument("--keep-transcripts", type=int, default=DEFAULT_KEEP_TRANSCRIPTS)
    cleanup.add_argument("--keep-recordings", type=int, default=DEFAULT_KEEP_RECORDINGS)
    cleanup.add_argument("--recording-max-age-days", type=int, default=DEFAULT_RECORDING_MAX_AGE_DAYS)
    cleanup.add_argument("--dry-run", action="store_true")
    cleanup.set_defaults(handler=command_cleanup)

    diagnostics = subparsers.add_parser("diagnostics")
    add_common_options(diagnostics)
    diagnostics.add_argument("--save", action="store_true")
    diagnostics.add_argument("--output", default="")
    diagnostics.add_argument("--settings-json", default="")
    diagnostics.add_argument(
        "--settings-json-stdin",
        action="store_true",
        help="read settings JSON from stdin instead of exposing it in process arguments",
    )
    diagnostics.add_argument(
        "--applet",
        action="store_true",
        help="evaluate doctor readiness for the Cinnamon applet path",
    )
    diagnostics.set_defaults(handler=command_diagnostics)

    record_error = subparsers.add_parser("record-error")
    add_common_options(record_error)
    record_error.set_defaults(handler=command_record_error)

    alarms = subparsers.add_parser("alarms")
    alarm_subparsers = alarms.add_subparsers(dest="alarm_command", required=True)

    alarms_list = alarm_subparsers.add_parser("list")
    add_common_options(alarms_list)
    alarms_list.set_defaults(handler=command_alarms_list)

    alarms_add = alarm_subparsers.add_parser("add")
    add_common_options(alarms_add)
    alarms_add.add_argument("--time", required=True, help="local alarm time in HH:MM")
    alarms_add.add_argument("--name", default="")
    alarms_add.add_argument("--days", default="daily", help="daily, weekdays, weekends, or comma-separated day codes")
    alarms_add.add_argument("--urgency", default="normal", choices=["silent", "normal", "critical"])
    alarms_add.add_argument("--disabled", action="store_true")
    alarms_add.set_defaults(handler=command_alarms_add)

    alarms_remove = alarm_subparsers.add_parser("remove")
    add_common_options(alarms_remove)
    alarms_remove.add_argument("id")
    alarms_remove.set_defaults(handler=command_alarms_remove)

    alarms_enable = alarm_subparsers.add_parser("enable")
    add_common_options(alarms_enable)
    alarms_enable.add_argument("id")
    alarms_enable.set_defaults(handler=command_alarms_enable)

    alarms_disable = alarm_subparsers.add_parser("disable")
    add_common_options(alarms_disable)
    alarms_disable.add_argument("id")
    alarms_disable.set_defaults(handler=command_alarms_disable)

    alarms_check = alarm_subparsers.add_parser("check")
    add_common_options(alarms_check)
    alarms_check.add_argument("--mark", action="store_true", help="persist trigger state for due alarms")
    alarms_check.add_argument("--catch-up-minutes", type=int, default=15)
    alarms_check.set_defaults(handler=command_alarms_check)

    alarms_import = subparsers.add_parser("alarms-import")
    add_common_options(alarms_import)
    alarms_import.set_defaults(handler=command_alarms_import)

    settings_export = subparsers.add_parser("settings-export")
    add_common_options(settings_export)
    settings_export.add_argument("--settings-json", default="{}")
    settings_export.add_argument(
        "--settings-json-stdin",
        action="store_true",
        help="read settings JSON from stdin instead of exposing it in process arguments",
    )
    settings_export.add_argument("--output", default="")
    settings_export.set_defaults(handler=command_settings_export)

    settings_import = subparsers.add_parser("settings-import")
    add_common_options(settings_import)
    settings_import.add_argument("--input", default="")
    settings_import.add_argument(
        "--confirm-plaintext-settings-output",
        action="store_true",
        help="include imported settings in JSON output; may expose personal context or vocabulary",
    )
    settings_import.add_argument(
        "--preview",
        action="store_true",
        help="validate import without persisting alarms; returns normalized alarms for a later commit",
    )
    settings_import.set_defaults(handler=command_settings_import)

    profanity_filter_document = subparsers.add_parser("profanity-filter-document")
    add_common_options(profanity_filter_document)
    profanity_filter_document.add_argument(
        "--open",
        action="store_true",
        help="open the editable list without returning its path",
    )
    profanity_filter_document.set_defaults(handler=command_profanity_filter_document)

    insert = subparsers.add_parser("insert-text")
    add_common_options(insert)
    insert.add_argument("text")
    insert.add_argument(
        "--insert-method",
        default="clipboard-paste",
        choices=["clipboard-paste", "clipboard-paste-submit", "clipboard", "type", "none"],
    )
    insert.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    insert.add_argument("--append-space", action="store_true")
    insert.add_argument("--sanitize-special-chars", action="store_true")
    insert.add_argument("--soften-profanity", action="store_true")
    insert.set_defaults(handler=command_insert_text)

    transcribe_file = subparsers.add_parser("transcribe-file")
    add_common_options(transcribe_file)
    transcribe_file.add_argument("audio_path")
    transcribe_file.add_argument("--language", default="en")
    transcribe_file.add_argument("--transcriber", default="auto", choices=TRANSCRIBER_CHOICES)
    transcribe_file.add_argument("--transcriber-command", default="")
    transcribe_file.add_argument("--whisper-model", default="")
    transcribe_file.add_argument(
        "--post-process-backend",
        default="none",
        choices=["none", "command", "ollama", "openai-compatible", "openai", "local-openai"],
    )
    transcribe_file.add_argument("--post-process-command", default="")
    transcribe_file.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    transcribe_file.add_argument("--ollama-model", default="")
    transcribe_file.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    transcribe_file.add_argument("--openai-compatible-model", default=DEFAULT_OPENAI_COMPATIBLE_MODEL)
    transcribe_file.add_argument("--openai-compatible-text-model", default=DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL)
    transcribe_file.add_argument(
        "--openai-compatible-api-key-stdin",
        action="store_true",
        help="read OpenAI-compatible API key from stdin; otherwise use OPENAI_COMPATIBLE_API_KEY",
    )
    transcribe_file.add_argument(
        "--openai-compatible-flex-processing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use OpenAI-compatible flex processing for speech-to-text and text polishing requests; default: enabled",
    )
    transcribe_file.add_argument("--post-process-prompt", default="")
    transcribe_file.add_argument("--personal-context", default="")
    transcribe_file.add_argument("--vocabulary", default="")
    transcribe_file.add_argument(
        "--artifact-encryption",
        default=ARTIFACT_ENCRYPTION_OFF,
        choices=ARTIFACT_ENCRYPTION_CHOICES,
        help=(
            "encrypt the stored transcript: off, passphrase, or keyring; "
            "keyring fails closed if Secret Service is unavailable; choose passphrase explicitly when needed; "
            "passphrase uses "
            "SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE_FILE, an existing "
            "~/.config/speed-of-cinnamon/artifact.key, SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE, "
            "or generates ~/.config/speed-of-cinnamon/artifact.key at runtime; weak default key files are regenerated"
        ),
    )
    transcribe_file.add_argument("--soften-profanity", action="store_true")
    transcribe_file.add_argument(
        "--confirm-plaintext-output",
        action="store_true",
        help="allow full transcript text in command output; transcript remains redacted by default",
    )
    transcribe_file.set_defaults(handler=command_transcribe_file)
    return parser


def run(argv: list[str] | None = None, *, bootstrap_error: str | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_output = False
    command_name = str(getattr(args, "command", "unknown"))
    requested_log_level = getattr(args, "log_level", DEFAULT_LOG_LEVEL)
    lazy_status_error_logging = command_name == "status" and requested_log_level == "error"
    status_error_logging_promoted = False
    deferred_output_interrupt = False

    def promote_status_error_logging() -> None:
        nonlocal status_error_logging_promoted
        if not lazy_status_error_logging or status_error_logging_promoted:
            return
        status_error_logging_promoted = True
        try:
            configure_logging("error")
        except Exception:
            return

    secret_values: tuple[str, ...] = _known_cli_secret_values(args)
    try:
        json_output = _coerce_bool(getattr(args, "json", False), field_name="json")
        configure_logging("off" if lazy_status_error_logging else requested_log_level)
        _safe_log_event("info", "command_start", command=command_name)
        if bootstrap_error is not None:
            raise RuntimeError(bootstrap_error)
        payload = args.handler(args)
        if type(payload) is not dict:
            raise RuntimeError("command returned invalid status")
        raw_status = payload.get("status")
        if (
            type(raw_status) is not str
            or raw_status not in _PUBLIC_PAYLOAD_STATUSES
        ):
            raise RuntimeError("command returned invalid status")
        error_present = "error" in payload
        if error_present:
            raw_error = payload["error"]
            if type(raw_error) is not str or raw_error.strip() == "":
                raise RuntimeError("command returned invalid response")
        elif raw_status == "error":
            raw_message = payload.get("message")
            if type(raw_message) is not str or raw_message.strip() == "":
                raise RuntimeError("command returned invalid response")
            payload["error"] = raw_message
        has_semantic_error = raw_status == "error" or error_present
        persisted_status_error = False
        persisted_provenance = payload.pop(
            _PERSISTED_STATUS_ERROR_PROVENANCE,
            None,
        )
        payload.pop("persisted_error", None)
        persisted_status_error = (
            command_name == "status"
            and persisted_provenance is _PERSISTED_STATUS_ERROR_PROVENANCE
        )
        if persisted_status_error:
            payload["persisted_error"] = True
        secret_values = _known_cli_secret_values(args)
        if command_name == "doctor":
            payload = _redact_doctor_payload(payload, secret_values=secret_values)
        else:
            payload = _redact_error_payload(payload, secret_values=secret_values)
        payload["status"] = raw_status
        status = raw_status
        if status == "error":
            if payload.get("message"):
                payload["message"] = _redact_error_for_user(payload["message"], secret_values=secret_values)
        if has_semantic_error:
            payload.pop("exit_code", None)
        if "error" in payload and payload["error"] is not None:
            payload["error"] = _redact_error_for_user(payload["error"], secret_values=secret_values)
        payload.pop("failure_stage", None)
        payload.pop("failure_code", None)
        payload.pop("failure_reason", None)
        payload.pop("provider_status", None)
        failure_fields: dict[str, object] = {}
        if has_semantic_error and not persisted_status_error:
            promote_status_error_logging()
            _safe_log_event(
                "error",
                "command_error",
                group="cli",
                command=command_name,
                status=status,
                error_type="payload",
                error_message=_redact_error_for_user(payload.get("error", ""), secret_values=secret_values),
                **failure_fields,
            )
        else:
            _safe_log_event("info", "command_done", command=command_name, status=status)
        try:
            print_result(payload, json_output)
        except OSError:
            return 1
        exit_code = payload.get("exit_code")
        if (
            command_name == "cancel"
            and status != "error"
            and not has_semantic_error
            and isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and 0 <= exit_code <= 255
        ):
            return exit_code
        return 0 if status != "error" and not has_semantic_error else 1
    except Exception as exc:
        secret_values = _known_cli_secret_values(args)
        error_message = _redact_error_for_user(str(exc), secret_values=secret_values)
        failure_fields = _classified_public_error_fields(exc, error_message)
        promote_status_error_logging()
        _safe_log_event(
            "error",
            "command_exception",
            group="cli",
            command=command_name,
            error_type=exc.__class__.__name__,
            error_message=error_message,
            **failure_fields,
        )
        payload = {
            "status": "error",
            "error": error_message,
            **failure_fields,
        }
        try:
            print_result(payload, json_output)
        except OSError:
            pass
        except KeyboardInterrupt:
            deferred_output_interrupt = True
        except (MemoryError, RecursionError):
            fallback = (
                '{"status":"error","error":"result could not be rendered"}\n'
                if json_output
                else f"{APP_NAME}: result could not be rendered\n"
            )
            try:
                sys.stdout.write(fallback)
            except (BrokenPipeError, OSError, MemoryError):
                pass
            except KeyboardInterrupt:
                deferred_output_interrupt = True
        if not deferred_output_interrupt:
            return 1
    if deferred_output_interrupt:
        interrupt = KeyboardInterrupt()
        _clear_transient_exception_metadata(interrupt)
        raise interrupt from None
    return 1


def _is_metadata_only_invocation(arguments: list[str]) -> bool:
    for argument in arguments:
        if argument == "--":
            break
        if argument in {"-h", "--help", "--version"}:
            return True
    return False


def main() -> None:
    arguments = sys.argv[1:]
    if _is_metadata_only_invocation(arguments):
        sys.exit(run())
    try:
        scoped = ensure_soc_priority_scope(arguments)
    except PriorityScopeError as exc:
        sys.exit(run(bootstrap_error=str(exc)))
    if scoped:
        cpu_priority_ok, io_priority_ok = apply_process_priority()
        if not cpu_priority_ok or not io_priority_ok:
            sys.exit(run(bootstrap_error="SOC priority scope verification failed"))
    sys.exit(run())


if __name__ == "__main__":
    main()
