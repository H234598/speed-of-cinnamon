from __future__ import annotations

import argparse
import errno
import glob
import heapq
import io
import json
import os
import platform
import re
import secrets
import shutil
import stat as stat_module
import subprocess  # nosec B404
import sys
import time
import tempfile
import urllib.parse
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import doctor
from .alarms import (
    add_alarm,
    check_due_alarms,
    list_alarm_payload,
    load_alarm_store,
    remove_alarm,
    save_alarm_store,
    set_alarm_enabled,
)
from .app_logging import DEFAULT_LOG_LEVEL, LOG_LEVELS, configure_logging, log_event, sanitize_error_message
from .artifact_crypto import (
    ARTIFACT_ENCRYPTION_CHOICES,
    ARTIFACT_ENCRYPTION_OFF,
    ArtifactCryptoError,
    encrypted_path_for,
    is_encrypted_path,
    normalize_artifact_encryption,
    read_decrypted_bytes_from_file,
    write_encrypted_bytes_atomically,
)
from .doctor import parse_settings_json, report as doctor_report
from .http_safety import is_loopback_hostname
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
from .output import insert_text
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
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    open_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)
from .postprocessor import (
    DEFAULT_OLLAMA_URL,
    DEFAULT_OPENAI_COMPATIBLE_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    list_ollama_models,
    list_openai_compatible_models,
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
    RecorderCommand,
    SilenceDetectionResult,
    choose_recorder,
    detect_silent_recording,
    list_input_sources,
    read_recording_level,
    reencode_recording_to_flac,
    normalize_input_device,
    start_recorder,
    stop_process,
    trim_recording_silence,
)
from .recorder import MAX_RECORDING_SECONDS, RecorderError, validate_recording_path
from .settings_export import read_export, write_export
from .setup_plan import build_setup_plan
from .state import RecordingState, StateStore, now_iso, process_is_alive
from .text_utils import sanitize_special_chars
from .transcriber import MAX_AUDIO_PATH_CHARS, normalize_backend, validate_audio_file, transcribe
from .profanity_filter import (
    MAX_PROFANITY_FILTER_BYTES,
    PROFANITY_REPLACEMENTS,
    PROFANITY_REPLACEMENT_PAIRS,
    compile_profanity_replacements,
    parse_profanity_replacement_list,
    render_profanity_replacement_list,
)

RECORDER_START_GRACE_SECONDS = 0.2
DEFAULT_KEEP_TRANSCRIPTS = 500
DEFAULT_KEEP_RECORDINGS = 20
DEFAULT_RECORDING_MAX_AGE_DAYS = 7
MAX_TEMP_RECORDING_FILES = 20
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
HISTORY_PREVIEW_REDACTED_TEXT = "[transcript preview redacted]"
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
MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS = 5
MAX_URL_CHARS = 2_048
MAX_ALARM_CATCH_UP_MINUTES = 14_400
MAX_RECORDING_ARTIFACT_CANDIDATES = 100
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


def _finalization_lock_path(state_path: Path) -> Path:
    return Path(state_path).with_name(f".{state_path.name}.finalizing")


def _read_finalization_lock_pid(lock_path: Path) -> int | None:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        return None
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    try:
        assert_no_symlink_ancestors(lock_path, field_name="finalization lock")
        fd = os.open(str(lock_path), os.O_RDONLY | nofollow_flag | nonblock_flag)
        assert_fd_is_regular_private_file(fd, field_name="finalization lock", require_private_mode=True)
        with os.fdopen(fd, "rb") as handle:
            fd = None
            raw = handle.read(512)
    except (OSError, RuntimeError):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    text = text.splitlines()
    if not text:
        return None
    first = text[0].strip()
    if not first.isdigit():
        return None
    pid = int(first)
    return pid if pid > 0 else None


def _finalization_lock_identity_for_pid(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").strip()
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
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    start_time = rest[19]
    if not boot_id or not start_time:
        return None
    return f"{boot_id}:{start_time}"


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
    return identity or None


def _same_finalization_lock_snapshot(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_nlink,
        first.st_size,
        first.st_mtime_ns,
        first.st_ctime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_nlink,
        second.st_size,
        second.st_mtime_ns,
        second.st_ctime_ns,
    )


def _same_finalization_lock_identity(current: os.stat_result, expected: os.stat_result) -> bool:
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_mode == expected.st_mode
        and getattr(current, "st_nlink", 1) == getattr(expected, "st_nlink", 1)
    )


def _unlink_finalization_lock_at(
    parent_fd: int,
    lock_path: Path,
    *,
    expected_stat: os.stat_result | None = None,
) -> bool:
    try:
        current = os.stat(lock_path.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat_module.S_ISREG(current.st_mode):
        return False
    if getattr(current, "st_nlink", 1) != 1:
        return False
    if expected_stat is not None and not _same_finalization_lock_identity(current, expected_stat):
        return False
    os.unlink(lock_path.name, dir_fd=parent_fd)
    os.fsync(parent_fd)
    return True


def _acquire_finalization_lock(state_path: Path) -> Path | None:
    lock_path = _finalization_lock_path(state_path)
    try:
        assert_no_symlink_ancestors(lock_path, field_name="finalization lock path")
    except RuntimeError:
        return None
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        return None
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
            try:
                fd = os.open(
                    lock_path.name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow_flag,
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
                owner_pid = _read_finalization_lock_pid(lock_path)
                owner_identity = _read_finalization_lock_identity(lock_path)
                if owner_pid is not None and process_is_alive(owner_pid):
                    if owner_identity is not None:
                        owner_current_identity = _finalization_lock_identity_for_pid(owner_pid)
                        if owner_current_identity is not None and owner_identity == owner_current_identity:
                            return None
                        if owner_current_identity is None and now - existing.st_mtime <= MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS:
                            return None
                    elif now - existing.st_mtime <= MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS:
                        return None
                if owner_pid is None and now - existing.st_mtime <= MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS:
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
                return None

            try:
                identity = _finalization_lock_identity_for_pid(os.getpid())
                if identity is None:
                    os.write(fd, f"{os.getpid()}\n".encode("ascii"))
                else:
                    os.write(fd, f"{os.getpid()}\n{identity}\n".encode("ascii"))
            except OSError:
                try:
                    os.close(fd)
                except OSError:
                    pass
                try:
                    _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                except OSError:
                    pass
                return None
            try:
                os.close(fd)
            except OSError:
                try:
                    _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=created_stat)
                except OSError:
                    pass
                return None
            return lock_path
        return None
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass


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
            if owner_pid != os.getpid():
                return
            owner_identity = _read_finalization_lock_identity(lock_path)
            current_identity = _finalization_lock_identity_for_pid(os.getpid())
            if owner_identity is not None and owner_identity != current_identity:
                return
            _unlink_finalization_lock_at(parent_fd, lock_path, expected_stat=current)
        except OSError:
            pass
    finally:
        try:
            os.close(parent_fd)
        except OSError:
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
    if len(value) > max_chars:
        if field_name == "audio file path":
            raise RuntimeError(f"{field_name} is too long (max {max_chars} characters)")
        raise RuntimeError(f"{field_name} is too large (max {max_chars} characters)")
    if len(value.encode("utf-8")) > max_chars:
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
    return _assert_clean_text(url, field_name=field_name, max_chars=MAX_URL_CHARS).rstrip("/")


def _validate_openai_compatible_http_url(url: str, field_name: str) -> str:
    base = _validate_text_model_url(url, field_name=field_name)
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError(f"{field_name} must use http:// or https://")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise RuntimeError(f"{field_name} must use https:// unless host is local loopback")
    return base


def _is_local_ollama_url(url: str) -> bool:
    raw = url or DEFAULT_OLLAMA_URL
    if isinstance(raw, bool) or not isinstance(raw, str):
        return False
    if _contains_escaped_null(raw) or _contains_http_header_control_chars(raw):
        return False
    normalized = raw.strip().lower()
    return (
        normalized.startswith("http://127.0.0.1:")
        or normalized.startswith("http://localhost:")
        or normalized.startswith("http://[::1]:")
    )


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
        "openai_compatible_api_key": getattr(args, "openai_compatible_api_key", ""),
        "openai_compatible_flex_processing": getattr(args, "openai_compatible_flex_processing", True),
    }


def _openai_compatible_post_process_model(args: argparse.Namespace) -> str:
    text_model = getattr(args, "openai_compatible_text_model", "")
    return text_model or DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL


def _validate_pipeline_text_args(
    args: argparse.Namespace,
    *,
    language: str,
) -> str:
    language = _assert_clean_text(language, field_name="language", max_chars=MAX_PATH_CHARS)
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
    _coerce_bool(getattr(args, "soften_profanity", False), field_name="soften_profanity")
    _validate_text_model_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
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
    if nofollow_flag is None:
        raise OSError("secure file open is not supported on this platform")
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    try:
        fd = os.open(path, os.O_RDONLY | nofollow_flag | nonblock_flag)
        assert_fd_is_regular_private_file(fd, field_name="file path")
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise OSError(str(exc)) from exc
    except RuntimeError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise OSError(str(exc)) from exc
    try:
        handle = os.fdopen(fd, "rb")
        fd = None
    except OSError:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise
    try:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size <= max_bytes:
            handle.seek(0)
        else:
            handle.seek(size - max_bytes)
        try:
            text = handle.read().decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"failed to decode file as UTF-8: {path}") from exc
    finally:
        try:
            handle.close()
        except OSError:
            pass
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
    try:
        text = file.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"{field_name} is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise RuntimeError(f"{field_name} contains invalid null byte")
    return text


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


def print_result(payload: dict[str, object], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        status = payload.get("status", "ok")
        message = payload.get("message") or payload.get("error") or status
        print(f"{APP_NAME}: {message}")


def _redact_error_for_user(error: object) -> str:
    if isinstance(error, bool) or not isinstance(error, str):
        return "[invalid]"
    return sanitize_error_message(error, max_chars=MAX_LOG_EXCERPT_CHARS)


def _redact_error_payload(value: object) -> object:
    if isinstance(value, dict):
        clean: dict[object, object] = {}
        for key, child in value.items():
            if isinstance(key, str) and key in {"detail", "error", "error_message"} and child is not None:
                clean[key] = _redact_error_for_user(child)
            else:
                clean[key] = _redact_error_payload(child)
        return clean
    if isinstance(value, list):
        return [_redact_error_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_error_payload(item) for item in value)
    return value


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


def soften_profanity_text(text: str) -> str:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RuntimeError("text must be text")
    output = text
    for pattern, replacement in _profanity_replacements():
        output = pattern.sub(lambda match, value=replacement: _match_replacement_case(match.group(0), value), output)
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
    path = _ensure_editable_profanity_filter_file()
    text = read_text_without_following_symlinks(
        path,
        field_name="profanity filter file",
        max_bytes=MAX_PROFANITY_FILTER_BYTES,
    )
    return parse_profanity_replacement_list(text)


def _profanity_replacements() -> tuple[tuple[re.Pattern[str], str], ...]:
    return compile_profanity_replacements(_profanity_replacement_pairs_from_file())


def _open_blacklist_document() -> bool:
    path = blacklist_file()
    try:
        assert_no_symlink_ancestors(path, field_name="blacklist file")
    except RuntimeError:
        return False
    ensure_runtime_dirs()
    _ensure_private_text_file(path)
    xdg_open = _which("xdg-open")
    if xdg_open:
        try:
            subprocess.Popen(  # nosec B603
                [xdg_open, str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    gio_open = _which("gio")
    if gio_open:
        try:
            subprocess.Popen(  # nosec B603
                [gio_open, "open", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except OSError:
            pass
    return False


def _apply_security_post_processing(text: str) -> tuple[str, dict[str, object]]:
    directives = parse_security_directives(text)
    entries = load_blacklist_file(blacklist_file(), strict=True)
    if directives.added_blacklist:
        entries = update_blacklist_file(blacklist_file(), directives.added_blacklist)
    blacklist_opened = False
    if directives.show_blacklist:
        blacklist_opened = _open_blacklist_document()
    sanitized, redactions = apply_security_mode(directives.text, entries)
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
    return {"blacklist_added": [], "blacklist_opened": False, "redacted_words": [], "blacklist_hits": 0}


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


def _apply_security_mask_only(text: str) -> tuple[str, dict[str, object]]:
    directives = parse_security_directives(text)
    entries = load_blacklist_file(blacklist_file(), strict=True)
    sanitized, redactions = apply_security_mode(directives.text, entries)
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
    text, security_post_processing = _apply_security_post_processing(text)
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
        getattr(args, "openai_compatible_api_key", ""),
        getattr(args, "openai_compatible_flex_processing", True),
    )
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
    if not path or not path.exists():
        return ""
    try:
        text = read_file_tail(path, max_chars)
    except (OSError, ValueError):
        return ""
    return _redact_error_for_user(text.strip())


def transcript_preview(text: str, max_chars: int = 80) -> str:
    if text and len(text) <= max_chars and all(text.find(ch) < 0 for ch in " \t\n\r\f\v"):
        return text
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[: max_chars - 3] + "..."


def _redact_history_previews(transcripts: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {**entry, "preview": HISTORY_PREVIEW_REDACTED_TEXT}
        for entry in transcripts
    ]


def _transcript_history_candidates(directory: Path):
    for path, file_stat in _safe_directory_entries(directory, field_name="transcript directory"):
        if not _is_transcript_artifact(path):
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if getattr(file_stat, "st_nlink", 1) != 1:
            continue
        yield file_stat.st_mtime, path


def _is_transcript_artifact(path: Path) -> bool:
    if not isinstance(path, Path):
        return False
    name = path.name.lower()
    if name.startswith("."):
        return False
    return name.endswith(".txt") or name.endswith(ENCRYPTED_TRANSCRIPT_SUFFIX)


def _safe_transcript_artifact_files() -> list[Path]:
    return [
        path
        for path in _safe_regular_child_files(transcript_dir(), TRANSCRIPT_ARTIFACT_SUFFIXES, field_name="transcript directory")
        if _is_transcript_artifact(path)
    ]


def _read_stored_transcript_text(path: Path) -> str:
    if is_encrypted_path(path):
        payload = read_decrypted_bytes_from_file(
            path,
            kind="transcript",
            field_name="transcript file",
            max_bytes=MAX_STORED_TRANSCRIPT_BYTES * 2,
            require_encrypted=True,
        )
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"transcript file is not valid UTF-8: {path}") from exc
    return read_text_without_following_symlinks(
        path,
        field_name="transcript file",
        max_bytes=MAX_STORED_TRANSCRIPT_BYTES,
    )


def _artifact_encryption_mode(args: argparse.Namespace) -> str:
    return normalize_artifact_encryption(getattr(args, "artifact_encryption", ARTIFACT_ENCRYPTION_OFF))


def _confirm_plaintext_transcript_output(args: argparse.Namespace) -> bool:
    return _coerce_bool(
        getattr(args, "confirm_plaintext_output", False),
        field_name="confirm-plaintext-output",
    )


def _transcript_payload_text(text: str, transcript_encryption: str, args: argparse.Namespace) -> str:
    if transcript_encryption == ARTIFACT_ENCRYPTION_OFF or _confirm_plaintext_transcript_output(args):
        return text
    return ""


def _transcript_work_path(storage_path: Path, encryption_mode: str) -> Path:
    if encryption_mode == ARTIFACT_ENCRYPTION_OFF:
        return storage_path
    return storage_path.with_name(f".{storage_path.stem}.{secrets.token_hex(8)}.tmp.txt")


def _same_leaf_identity(current: os.stat_result, expected: os.stat_result) -> bool:
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_mode == expected.st_mode
        and getattr(current, "st_nlink", 1) == getattr(expected, "st_nlink", 1)
    )


def _unlink_regular_leaf_with_parent_fsync(
    path: Path,
    *,
    field_name: str,
    expected_stat: os.stat_result | None = None,
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
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    except OSError as exc:
        raise RuntimeError(f"failed to delete {field_name}: {path}") from exc
    finally:
        os.close(parent_fd)


def _remove_transient_transcript_path(path: Path, storage_path: Path) -> None:
    if path == storage_path:
        return
    try:
        path.relative_to(transcript_dir())
    except ValueError:
        return
    if not path.name.startswith(".") or not path.name.endswith(".tmp.txt"):
        return
    try:
        assert_no_symlink_ancestors(path, field_name="transient transcript file")
        _unlink_regular_leaf_with_parent_fsync(path, field_name="transient transcript file")
    except (FileNotFoundError, RuntimeError):
        return


def _write_stored_transcript(path: Path, text: str, args: argparse.Namespace) -> tuple[Path, str]:
    mode = _artifact_encryption_mode(args)
    payload = text.encode("utf-8")
    if mode == ARTIFACT_ENCRYPTION_OFF:
        _write_text_atomic(path, text)
        return path, ARTIFACT_ENCRYPTION_OFF
    try:
        encrypted_path, effective_mode = write_encrypted_bytes_atomically(
            path,
            payload,
            mode,
            kind="transcript",
            field_name="transcript file",
        )
    except ArtifactCryptoError as exc:
        raise RuntimeError(str(exc)) from exc
    _remove_plaintext_transcript_sibling_after_encryption(path, encrypted_path)
    return encrypted_path, effective_mode


def _remove_plaintext_transcript_sibling_after_encryption(storage_path: Path, encrypted_path: Path) -> None:
    if encrypted_path == storage_path or not is_encrypted_path(encrypted_path):
        return
    plaintext_path = encrypted_path.with_name(encrypted_path.name.removesuffix(".socenc"))
    if plaintext_path != storage_path:
        raise RuntimeError(f"unexpected encrypted transcript sibling path: {encrypted_path}")
    if not plaintext_path.exists() and not plaintext_path.is_symlink():
        return
    if not _remove_transcript_file(plaintext_path):
        raise RuntimeError(f"failed to remove plaintext transcript artifact after encryption: {plaintext_path}")


def _remove_plaintext_export_sibling_after_encryption(storage_path: Path, encrypted_path: Path) -> None:
    if encrypted_path == storage_path or not is_encrypted_path(encrypted_path):
        return
    plaintext_path = encrypted_path.with_name(encrypted_path.name.removesuffix(".socenc"))
    if plaintext_path != storage_path:
        raise RuntimeError(f"unexpected encrypted transcript export sibling path: {encrypted_path}")
    if not plaintext_path.exists() and not plaintext_path.is_symlink():
        return
    try:
        assert_no_symlink_ancestors(plaintext_path, field_name="transcript export")
        if not _unlink_regular_leaf_with_parent_fsync(plaintext_path, field_name="transcript export"):
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


def _remove_plaintext_recording_sibling_after_encryption(original_path: Path, encrypted_path: Path) -> None:
    candidates: list[Path] = []
    if encrypted_path != original_path:
        candidates.append(original_path)
    plaintext_sibling = _plaintext_recording_sibling_for_encrypted_path(encrypted_path)
    if plaintext_sibling is not None:
        candidates.append(plaintext_sibling)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or candidate == encrypted_path:
            continue
        seen.add(candidate)
        if not candidate.exists() and not candidate.is_symlink():
            continue
        suffix = candidate.suffix.lower()
        if suffix not in {".flac", ".wav"}:
            raise RuntimeError(f"refusing to remove unexpected plaintext recording artifact: {candidate}")
        if not remove_file(str(candidate), suffix=suffix):
            raise RuntimeError(f"failed to remove plaintext recording artifact after encryption: {candidate}")


def _encrypt_kept_recording_artifact(path: Path, args: argparse.Namespace) -> tuple[Path, str]:
    mode = _artifact_encryption_mode(args)
    if mode == ARTIFACT_ENCRYPTION_OFF:
        return path, ARTIFACT_ENCRYPTION_OFF
    try:
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
        )
        encrypted_path, effective_mode = write_encrypted_bytes_atomically(
            path,
            payload,
            mode,
            kind="recording",
            field_name="recording audio file",
        )
    except ArtifactCryptoError as exc:
        raise RuntimeError(str(exc)) from exc
    _remove_plaintext_recording_sibling_after_encryption(path, encrypted_path)
    return encrypted_path, effective_mode


def read_transcript_history(limit: int = 10) -> list[dict[str, object]]:
    if limit <= 0:
        return []
    directory = transcript_dir()

    candidates = heapq.nlargest(max(limit * 4, limit + 16), _transcript_history_candidates(directory))

    entries: list[dict[str, object]] = []
    for mtime, path in candidates:
        try:
            text = _read_stored_transcript_text(path).strip()
        except (OSError, RuntimeError, ValueError, UnicodeDecodeError, ArtifactCryptoError):
            continue
        if not text:
            continue
        modified_at = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        entries.append(
            {
                "path": str(path),
                "name": path.name,
                "modified_at": modified_at,
                "preview": transcript_preview(text),
            }
        )
        if len(entries) >= limit:
            break
    return entries


def build_transcripts_document(
    limit: int = MAX_HISTORY_LIMIT,
    *,
    max_chars: int | None = None,
    allow_truncate: bool = False,
) -> tuple[str, int, bool]:
    if limit <= 0:
        limit = MAX_HISTORY_LIMIT
    limit = min(limit, MAX_HISTORY_LIMIT)
    if max_chars is not None and (isinstance(max_chars, bool) or max_chars < 1):
        raise RuntimeError("transcript document size limit must be positive")
    directory = transcript_dir()
    candidates = heapq.nlargest(max(limit * 4, limit + 16), _transcript_history_candidates(directory))
    lines = [
        "Speed of Cinnamon transcripts",
        f"Generated: {now_iso()}",
        "",
    ]
    count = 0
    truncated = False

    def _current_text() -> str:
        return "\n".join(lines).rstrip() + "\n"

    for mtime, path in candidates:
        try:
            text = _read_stored_transcript_text(path).strip()
        except (OSError, RuntimeError, ValueError, UnicodeDecodeError, ArtifactCryptoError):
            continue
        if not text:
            continue
        modified_at = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
        entry = [
            f"===== {path.name} =====",
            f"Modified: {modified_at}",
            f"Path: {path}",
            "",
            text,
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
                            f"Stopped before {path.name} because the display limit was reached.",
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
        os.close(fd)


def write_transcripts_export(
    limit: int = MAX_HISTORY_LIMIT,
    *,
    encryption_mode: object = "keyring",
    plaintext: bool = False,
    confirm_plaintext: bool = False,
) -> tuple[Path, int, str]:
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
    )
    output_path = _transcript_export_path(plaintext)
    _ensure_transcript_export_dir(output_path)
    if plaintext:
        _write_text_atomic(output_path, content)
        return output_path, count, ARTIFACT_ENCRYPTION_OFF
    encrypted_path, used_mode = write_encrypted_bytes_atomically(
        output_path,
        content.encode("utf-8"),
        mode,
        kind="transcript",
        field_name="transcript export",
    )
    _remove_plaintext_export_sibling_after_encryption(output_path, encrypted_path)
    return encrypted_path, count, used_mode


def normalized_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    return _coerce_path(path_value, field_name="path", resolve=True)


def _assert_json_payload_size(payload: dict[str, object], *, max_bytes: int) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(rendered.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"output JSON is too large (max {max_bytes} bytes)")


def _write_json_atomic(path: Path, payload: dict[str, object], *, max_bytes: int) -> None:
    content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(content.encode("utf-8")) > max_bytes:
        raise RuntimeError(f"output JSON is too large (max {max_bytes} bytes)")
    try:
        write_text_atomically_without_following_symlinks(path, content, field_name="JSON output path")
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"failed to write JSON output: {path}") from exc


def _write_text_atomic(path: Path, text: str) -> None:
    try:
        write_text_atomically_without_following_symlinks(path, text, field_name="text output path")
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"failed to write transcript file: {path}") from exc


class _PrivateFilePrepareError(RuntimeError):
    def __init__(self, message: str, *, created: bool, errno_value: int | None = None) -> None:
        super().__init__(message)
        self.created = created
        self.errno = errno_value


def _prepare_private_file(path: Path, *, field_name: str, exclusive: bool = True) -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    assert_safe_path_components(path, field_name=field_name)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
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
        os.close(parent_fd)
    try:
        with os.fdopen(fd, "ab") as handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise _PrivateFilePrepareError(f"failed to prepare {field_name}: {path}", created=True, errno_value=exc.errno) from exc


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
                    if not remove_file(str(audio_path), suffix=".wav", recordings_root=root):
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


def _remove_transcript_file(path: Path) -> bool:
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
            expected_stat=file_stat,
        )
    except FileNotFoundError:
        return False
    except RuntimeError as exc:
        raise RuntimeError(f"failed to delete transcript file: {path}") from exc


def _require_json_path(path_value: str, *, field_name: str, default: Path | None = None) -> Path:
    if path_value:
        path = _coerce_path(path_value, field_name=field_name, resolve=True)
    elif default is not None:
        path = default
    else:
        raise RuntimeError(f"{field_name} is required")
    if path.suffix.lower() != ".json":
        raise RuntimeError(f"{field_name} must end with .json")
    return path


def _parse_cli_settings_json(raw: str) -> dict[str, object]:
    _assert_clean_text(raw, field_name="settings JSON", max_chars=MAX_SETTINGS_JSON_CHARS)
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
    return _parse_cli_settings_json(getattr(args, "settings_json", "{}"))


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

    normalized: list[dict[str, object]] = []
    for source in sources:
        source_id = source.id if hasattr(source, "id") else None
        if not isinstance(source_id, str) or isinstance(source_id, bool):
            raise RuntimeError("input source id must be text")
        if _contains_escaped_null(source_id):
            raise RuntimeError("input source id contains invalid null byte")
        if _contains_http_header_control_chars(source_id):
            raise RuntimeError("input source id contains invalid control character")

        name = source.name if hasattr(source, "name") else None
        if not isinstance(name, str) or isinstance(name, bool):
            raise RuntimeError("input source name must be text")
        if _contains_escaped_null(name):
            raise RuntimeError("input source name contains invalid null byte")
        if _contains_http_header_control_chars(name):
            raise RuntimeError("input source name contains invalid control character")

        description = source.description if hasattr(source, "description") else None
        if not isinstance(description, str) or isinstance(description, bool):
            raise RuntimeError("input source description must be text")
        if _contains_escaped_null(description):
            raise RuntimeError("input source description contains invalid null byte")
        if _contains_http_header_control_chars(description):
            raise RuntimeError("input source description contains invalid control character")

        driver = source.driver if hasattr(source, "driver") else None
        if not isinstance(driver, str) or isinstance(driver, bool):
            raise RuntimeError("input source driver must be text")
        if _contains_escaped_null(driver):
            raise RuntimeError("input source driver contains invalid null byte")
        if _contains_http_header_control_chars(driver):
            raise RuntimeError("input source driver contains invalid control character")

        state = source.state if hasattr(source, "state") else None
        if not isinstance(state, str) or isinstance(state, bool):
            raise RuntimeError("input source state must be text")
        if _contains_escaped_null(state):
            raise RuntimeError("input source state contains invalid null byte")
        if _contains_http_header_control_chars(state):
            raise RuntimeError("input source state contains invalid control character")

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
) -> set[Path]:
    paths: set[Path] = set()
    audio_path = _safe_recording_artifact_path(
        state.audio_path,
        suffix=(".wav", ".flac", ".socenc"),
        require_recordings_dir=True,
    )
    if audio_path is not None and _recording_artifact_stat(audio_path) is None:
        audio_path = None
    log_path = _safe_recording_artifact_path(state.log_path, suffix=".log", require_recordings_dir=True)
    if log_path is not None and _recording_artifact_stat(log_path) is None:
        log_path = None
    if audio_path:
        paths.add(audio_path)
    if log_path:
        paths.add(log_path)
    path = normalized_path(state.transcript_path)
    if path:
        paths.add(path)
    if state_path is not None and state.status == "finalizing":
        paths.update(_finalizing_inflight_artifact_paths(state_path, state))
    return paths


def _enforce_recording_artifact_cap(
    state: RecordingState | None,
    active_paths: set[Path] | None = None,
) -> dict[str, object]:
    if state is None:
        return {"deleted_paths": [], "failed_paths": []}
    active_paths = set(active_artifact_paths(state)) | (active_paths or set())
    return prune_files_by_mtime(
        recording_artifact_files(),
        MAX_TEMP_RECORDING_FILES,
        active_paths,
        dry_run=False,
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


def _recording_process_verified_alive(state: RecordingState) -> bool:
    pid = state.pid
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if not process_is_alive(pid):
        return False
    expected_identity = str(state.process_identity or "").strip()
    if not expected_identity:
        raise RuntimeError("recording process identity is missing; refusing to signal pid")
    current_identity = _recording_process_identity_for_pid(pid)
    if current_identity is None:
        raise RuntimeError("recording process identity could not be verified; refusing to signal pid")
    return current_identity == expected_identity


def _raise_if_state_unreadable(state: RecordingState) -> None:
    if state.error.startswith("state file "):
        raise RuntimeError(state.error)


def _recording_level_payload(state: RecordingState) -> dict[str, object] | None:
    audio_path = _safe_recording_artifact_path(state.audio_path, suffix=(".wav", ".flac", ".socenc"))
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
        return {"ok": False, "percent": 0, "peak": 0.0, "rms": 0.0, "samples": 0, "detail": str(exc)}


def _remove_recording_artifact(path_value: str | None) -> bool:
    if not path_value:
        return False
    if Path(str(path_value)).name.lower().endswith(ENCRYPTED_RECORDING_ARTIFACT_SUFFIXES):
        return remove_file(path_value, suffix=".socenc")
    return remove_file(path_value, suffix=".wav") or remove_file(path_value, suffix=".flac")


def _stabilize_recording_artifact_path(artifact_path: Path) -> Path:
    if not isinstance(artifact_path, Path):
        raise RuntimeError("recording artifact path is invalid")
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
    try:
        assert_no_symlink_ancestors(stable_path, field_name="recording artifact path")
        parent_fd = ensure_directory_without_following_symlinks(
            stable_path.parent,
            field_name="recording artifact directory",
        )
        if stable_path.exists() and _recording_artifact_stat(stable_path) is None:
            raise RuntimeError(f"stable recording artifact is not a safe regular file: {stable_path}")
        try:
            os.replace(
                artifact_path.name,
                stable_path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        except OSError as exc:
            if stable_path.exists() and stable_path.is_symlink():
                raise RuntimeError(f"stable recording artifact is not a safe regular file: {stable_path}") from exc
            if stable_path.exists() and _recording_artifact_stat(stable_path) is None:
                raise RuntimeError(f"stable recording artifact is not a safe regular file: {stable_path}") from exc
            raise
        return stable_path
    except (OSError, RuntimeError) as exc:
        raise RuntimeError(f"failed to stabilize recording artifact path: {exc}") from exc
    finally:
        if parent_fd is not None:
            try:
                os.close(parent_fd)
            except OSError:
                pass


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


def _safe_directory_entries(directory: Path, *, field_name: str) -> list[tuple[Path, os.stat_result]]:
    try:
        directory_fd = open_directory_without_following_symlinks(directory, field_name=field_name)
    except (OSError, RuntimeError):
        return []
    try:
        try:
            names = os.listdir(directory_fd)
        except OSError:
            return []
        entries: list[tuple[Path, os.stat_result]] = []
        for name in names:
            if not isinstance(name, str) or name in {"", ".", ".."} or "/" in name:
                continue
            try:
                file_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError:
                continue
            entries.append((directory / name, file_stat))
        return entries
    finally:
        os.close(directory_fd)


def _safe_regular_child_files(directory: Path, suffixes: tuple[str, ...], *, field_name: str) -> list[Path]:
    files: list[Path] = []
    for path, file_stat in _safe_directory_entries(directory, field_name=field_name):
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        if not stat_module.S_ISREG(file_stat.st_mode):
            continue
        if getattr(file_stat, "st_nlink", 1) != 1:
            continue
        files.append(path)
    return files


def _is_finalization_lock_active(state_path: Path) -> bool:
    lock_path = _finalization_lock_path(state_path)
    owner_pid = _read_finalization_lock_pid(lock_path)
    if not owner_pid:
        return False
    if not process_is_alive(owner_pid):
        return False
    owner_identity = _read_finalization_lock_identity(lock_path)
    if owner_identity is None:
        return True
    current_identity = _finalization_lock_identity_for_pid(owner_pid)
    return current_identity is None or current_identity == owner_identity


def _inflight_recording_artifact_paths(audio_path: Path) -> set[Path]:
    if not isinstance(audio_path, Path):
        return set()
    if audio_path.suffix.lower() not in {".wav", ".flac"}:
        return set()
    artifact_paths: set[Path] = set()
    escaped_stem = glob.escape(audio_path.stem)
    for marker in (".trimmed-", ".encoded-"):
        for suffix in (".wav", ".flac"):
            for path in audio_path.parent.glob(f"{escaped_stem}{marker}*{suffix}"):
                artifact_paths.add(path)
    return artifact_paths


def _finalizing_inflight_artifact_paths(state_path: Path, state: RecordingState) -> set[Path]:
    if not _is_finalization_lock_active(state_path):
        return set()
    if state.status != "finalizing":
        return set()

    in_flight_paths: set[Path] = set()
    audio_path = _safe_recording_artifact_path(state.audio_path, suffix=(".wav", ".flac", ".socenc"), require_recordings_dir=True)
    if audio_path is None:
        return set()
    if audio_path.suffix.lower() in {".wav", ".flac"}:
        transcript_path = transcript_dir() / f"{audio_path.stem}.txt"
        in_flight_paths.add(transcript_path)
        in_flight_paths.add(encrypted_path_for(transcript_path))
    in_flight_paths.update(_inflight_recording_artifact_paths(audio_path))
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


def sorted_files(paths: list[Path]) -> list[Path]:
    entries: list[tuple[float, str, Path]] = []
    for path in paths:
        file_stat = _recording_artifact_stat(path)
        if file_stat is None:
            continue
        entries.append((file_stat.st_mtime, path.name, path))
    return [path for _, _, path in sorted(entries, reverse=True)]


def delete_artifact(path: Path) -> bool:
    file_stat = _recording_artifact_stat(path)
    if file_stat is None:
        return False
    try:
        return _unlink_regular_leaf_with_parent_fsync(
            path,
            field_name="recording artifact",
            expected_stat=file_stat,
        )
    except RuntimeError:
        return False


def prune_files_by_mtime(paths: list[Path], keep: int, active_paths: set[Path], dry_run: bool) -> dict[str, object]:
    planned_paths: list[str] = []
    deleted_paths: list[str] = []
    failed_paths: list[str] = []
    skipped_active: list[str] = []
    inactive_paths: list[Path] = []
    normalized_active_paths = {path.resolve(strict=False) for path in active_paths}
    for path in sorted_files(paths):
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
        if delete_artifact(path):
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
        group = groups.setdefault(group_stem, {"stem": group_stem, "mtime": 0.0, "files": []})
        group["mtime"] = max(float(group["mtime"]), file_stat.st_mtime)
        group_files = group["files"]
        if isinstance(group_files, list):
            group_files.append(path)
    return sorted(groups.values(), key=lambda group: (float(group["mtime"]), str(group["stem"])), reverse=True)


def recording_artifact_files() -> list[Path]:
    return [
        path
        for path in _safe_regular_child_files(
            recordings_dir(),
            RECORDING_ARTIFACT_EXTENSIONS,
            field_name="recordings directory",
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
    cutoff = time.time() - max(0, max_age_days) * 24 * 60 * 60
    groups = recording_groups()
    grouped_artifacts: list[Path] = []
    for index, group in enumerate(groups):
        files = group.get("files", [])
        if isinstance(files, list):
            grouped_artifacts.extend(path for path in files if isinstance(path, Path))
        if index < max(keep, 0) and float(group.get("mtime", 0.0)) >= cutoff:
            continue
        if not isinstance(files, list):
            continue
        group_paths = [path for path in files if isinstance(path, Path)]
        if any(path.resolve(strict=False) in active_paths for path in group_paths):
            skipped_group_paths.extend(group_paths)
            skipped_active_paths.extend(str(path) for path in group_paths)
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
            if delete_artifact(path):
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
    ]
    file_cap_result = prune_files_by_mtime(
        remaining_artifacts,
        MAX_TEMP_RECORDING_FILES,
        active_paths,
        dry_run,
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


def command_start(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    current = store.read()
    _raise_if_state_unreadable(current)
    if current.status == "finalizing":
        return {
            "status": "finalizing",
            "message": "finalization in progress; wait for completion",
        }
    if current.status == "recording":
        current_audio_path = _safe_recording_artifact_path(
            current.audio_path, suffix=(".wav", ".flac", ".socenc"), require_recordings_dir=False
        )
        if _recording_process_verified_alive(current):
            return {
                "status": "recording",
                "message": "already recording",
                "pid": current.pid,
                "language": current.language,
            }
        if current_audio_path and current_audio_path.exists() and current_audio_path.stat().st_size > 0:
            recorded = store.update(status="recorded", stopped_at=current.stopped_at or now_iso())
            return {
                "status": "recorded",
                "message": "previous recording has exited; run stop or toggle to transcribe",
                "audio_path": recorded.audio_path,
                "language": recorded.language,
            }
        if current.audio_path and not current_audio_path:
            store.update(
                status="error",
                stopped_at=current.stopped_at or now_iso(),
                error="recording state references an invalid artifact path",
            )
            return {
                "status": "error",
                "message": "recording state references an invalid artifact path",
            }
        store.update(status="error", stopped_at=current.stopped_at or now_iso(), error="recording exited before audio was saved")
        return {
            "status": "error",
            "message": "recording exited before audio was saved",
        }

    max_seconds = _coerce_int(args.max_seconds, field_name="max-seconds", max_value=MAX_RECORDING_SECONDS)
    normalized_input_device = normalize_input_device(args.input_device)
    audio_path, log_path = _allocate_recording_artifacts()

    def reset_recording_artifacts() -> None:
        nonlocal audio_path, log_path
        remove_file(str(audio_path), suffix=".wav")
        remove_file(str(log_path), suffix=".log")
        audio_path, log_path = _allocate_recording_artifacts()

    recorder_preferences = ["pw-record", "parecord", "arecord"] if args.recorder == "auto" else [args.recorder]
    startup_errors: list[str] = []
    command: RecorderCommand | None = None
    proc: subprocess.Popen[bytes] | None = None
    for recorder_preference in recorder_preferences:
        try:
            candidate = choose_recorder(recorder_preference, audio_path, max_seconds, normalized_input_device)
            candidate_proc = start_recorder(candidate, log_path)
        except Exception as exc:
            startup_errors.append(f"{recorder_preference}: {exc}")
            if args.recorder != "auto":
                remove_file(str(audio_path), suffix=".wav")
                remove_file(str(log_path), suffix=".log")
                raise
            reset_recording_artifacts()
            continue
        time.sleep(RECORDER_START_GRACE_SECONDS)
        if candidate_proc.poll() is None:
            command = candidate
            proc = candidate_proc
            break
        detail = read_log_excerpt(log_path) or f"exit code {candidate_proc.returncode}"
        startup_errors.append(f"{candidate.name} exited immediately: {detail}")
        if args.recorder != "auto":
            remove_file(str(audio_path), suffix=".wav")
            remove_file(str(log_path), suffix=".log")
            raise RuntimeError(startup_errors[-1])
        reset_recording_artifacts()
    if command is None or proc is None:
        remove_file(str(audio_path), suffix=".wav")
        remove_file(str(log_path), suffix=".log")
        detail = "; ".join(startup_errors) if startup_errors else "no supported recorder found"
        raise RuntimeError(f"no recorder backend started successfully: {detail}")
    process_identity = _recording_process_identity_for_pid(proc.pid)
    if process_identity is None:
        stop_process(proc.pid)
        remove_file(str(audio_path), suffix=".wav")
        remove_file(str(log_path), suffix=".log")
        raise RuntimeError("recording process identity could not be verified")
    language = args.language or "en"
    state = RecordingState(
        status="recording",
        pid=proc.pid,
        process_identity=process_identity,
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
    except Exception:
        stop_process(proc.pid)
        remove_file(str(audio_path), suffix=".wav")
        remove_file(str(log_path), suffix=".log")
        raise
    _enforce_recording_artifact_cap(state)
    return {
        "status": "recording",
        "message": "recording started",
        "pid": proc.pid,
        "process_identity": process_identity,
        "audio_path": str(audio_path),
        "recorder": command.name,
        "input_device": normalized_input_device,
        "language": language,
    }


def finalize_recording(args: argparse.Namespace, store: StateStore, state: RecordingState) -> dict[str, object]:
    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        return {"status": "finalizing", "message": "finalization already in progress"}

    state_marked_finalizing = False
    written_text_path: Path | None = None
    try:
        state = store.read()
        _raise_if_state_unreadable(state)
        if state.status in {"done", "error", "idle"}:
            return {"status": state.status, "message": state.error or f"recording already {state.status}"}

        if not state.audio_path:
            store.update(status="error", stopped_at=state.stopped_at or now_iso(), error="no recording is available")
            raise RuntimeError("no recording is available")
        audio_path = _safe_recording_artifact_path(state.audio_path, suffix=(".wav", ".flac", ".socenc"), require_recordings_dir=True)
        if not audio_path:
            store.update(status="error", stopped_at=state.stopped_at or now_iso(), error="recording audio path is invalid")
            raise RuntimeError("recording audio path is invalid")
        try:
            audio_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if _recording_artifact_stat(audio_path) is None:
                store.update(
                    status="error",
                    stopped_at=state.stopped_at or now_iso(),
                    error="recording audio path is not a safe regular file",
                )
                raise RuntimeError("recording audio path is not a safe regular file")

        chosen_language = state.language or args.language or "en"
        language = _validate_pipeline_text_args(args, language=chosen_language)
        normalized_transcriber = normalize_backend(args.transcriber)
        keep_recording_artifacts = _coerce_bool(
            getattr(args, "keep_recording_artifacts", False),
            field_name="keep_recording_artifacts",
        )
        _coerce_bool(getattr(args, "skip_silent_auto_relisten", False), field_name="skip_silent_auto_relisten")
        if state.status != "finalizing":
            state = store.update(
                status="finalizing",
                stopped_at=state.stopped_at or now_iso(),
                error="",
                inserted=False,
            )
            state_marked_finalizing = True
        else:
            state_marked_finalizing = True
        audio_deleted = False
        log_deleted = False
        done_audio_path = str(audio_path)
        done_log_path = state.log_path
        trimmed_audio_path: Path | None = None
        stabilized_audio_path: Path | None = None
        remove_original_after_state_update = False
        audio_suffix = ""
        audio_path = validate_audio_file(audio_path)
        audio_suffix = audio_path.suffix.lower()
        silence = detect_silent_recording(audio_path)
        if silence.silent:
            cleanup_log_path = state.log_path
            if not keep_recording_artifacts:
                done_audio_path = None
                done_log_path = None
            state.audio_path = done_audio_path
            state.log_path = done_log_path
            state.transcript_path = ""
            artifact_cleanup = _enforce_recording_artifact_cap(state)
            done = store.update(
                status="done",
                stopped_at=state.stopped_at or now_iso(),
                audio_path=done_audio_path,
                log_path=done_log_path,
                transcript="",
                transcript_path="",
                inserted=False,
                error="",
            )
            if not keep_recording_artifacts:
                audio_deleted = remove_file(str(audio_path), suffix=audio_suffix)
                log_deleted = remove_file(cleanup_log_path, suffix=".log")
            return {
                "status": done.status,
                "message": "silent recording skipped",
                "transcript": "",
                "transcript_path": "",
                "inserted": False,
                "recording_artifact_cap": artifact_cleanup,
                "language": language,
                "recording_artifacts_kept": keep_recording_artifacts,
                "audio_deleted": audio_deleted,
                "log_deleted": log_deleted,
                "silence_detected": True,
                "silence_duration_seconds": silence.silence_seconds,
                "speech_duration_seconds": silence.speech_seconds,
            }

        text_path = transcript_dir() / f"{audio_path.stem}.txt"
        artifact_encryption = _artifact_encryption_mode(args)
        transcriber_text_path = _transcript_work_path(text_path, artifact_encryption)
        transcript_audio_path = audio_path
        try:
            trimmed_audio_path = trim_recording_silence(audio_path)
            transcript_audio_path = trimmed_audio_path
        except RecorderError:
            transcript_audio_path = audio_path
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
        finally:
            _remove_transient_transcript_path(transcriber_text_path, text_path)
            if trimmed_audio_path is not None and not keep_recording_artifacts:
                remove_file(str(trimmed_audio_path), suffix=".flac")

        if _is_empty_transcript_text(text):
            text = ""
            security_post_processing = _empty_security_post_processing()
        else:
            text, security_post_processing = _process_transcript(text, args, language)
        soften_profanity = _coerce_bool(getattr(args, "soften_profanity", False), field_name="soften_profanity")
        if text.strip() and soften_profanity:
            text = soften_profanity_text(text)
        stored_text_path, transcript_encryption = _write_stored_transcript(text_path, text.strip() + "\n", args)
        written_text_path = stored_text_path
        append_space = _coerce_bool(args.append_space, field_name="append_space")
        sanitize_special_chars = _coerce_bool(
            args.sanitize_special_chars,
            field_name="sanitize_special_chars",
        )
        text_to_insert = ""
        if text.strip():
            text_to_insert = prepare_output_text(text, append_space, sanitize_special_chars)
        typing_delay_ms = _coerce_int(args.typing_delay_ms, field_name="typing-delay-ms", max_value=MAX_TYPING_DELAY_MS)
        inserted = bool(text_to_insert) and bool(insert_text(text_to_insert, args.insert_method, typing_delay_ms))

        cleanup_audio_path: Path | None = None
        cleanup_log_path: str | None = None
        if not keep_recording_artifacts:
            cleanup_audio_path = audio_path
            cleanup_log_path = state.log_path
            done_audio_path = None
            done_log_path = None
        elif trimmed_audio_path is not None:
            stabilized_audio_path = _stabilize_recording_artifact_path(trimmed_audio_path)
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
                    stabilized_audio_path = _stabilize_recording_artifact_path(converted_audio_path)
                    done_audio_path = str(stabilized_audio_path)
                    if done_audio_path != str(audio_path):
                        remove_original_after_state_update = True
            else:
                done_audio_path = str(audio_path)

        recording_encryption = ARTIFACT_ENCRYPTION_OFF
        if keep_recording_artifacts and done_audio_path:
            plaintext_done_audio_path = Path(done_audio_path)
            encrypted_audio_path, recording_encryption = _encrypt_kept_recording_artifact(plaintext_done_audio_path, args)
            if encrypted_audio_path != plaintext_done_audio_path:
                done_audio_path = str(encrypted_audio_path)
                stabilized_audio_path = encrypted_audio_path
                if plaintext_done_audio_path == audio_path:
                    remove_original_after_state_update = False

        done = store.update(
            status="done",
            stopped_at=state.stopped_at or now_iso(),
            audio_path=done_audio_path,
            log_path=done_log_path,
            transcript=text if transcript_encryption == ARTIFACT_ENCRYPTION_OFF else "",
            transcript_path=str(stored_text_path),
            inserted=inserted,
            error="",
        )
        if remove_original_after_state_update:
            remove_file(str(audio_path), suffix=audio_suffix)
        if cleanup_audio_path is not None:
            audio_deleted = remove_file(str(cleanup_audio_path), suffix=audio_suffix)
        if cleanup_log_path is not None:
            log_deleted = remove_file(cleanup_log_path, suffix=".log")
        state = done
        artifact_cleanup_active_paths: set[Path] = set()
        if stabilized_audio_path is not None:
            artifact_cleanup_active_paths.add(stabilized_audio_path)
        if trimmed_audio_path is not None:
            artifact_cleanup_active_paths.add(trimmed_audio_path)
        if audio_path is not None:
            artifact_cleanup_active_paths.add(audio_path)
        artifact_cleanup = _enforce_recording_artifact_cap(state, artifact_cleanup_active_paths)
        keep_transcripts = _coerce_int(
            getattr(args, "keep_transcripts", DEFAULT_KEEP_TRANSCRIPTS),
            field_name="keep-transcripts",
            max_value=MAX_KEEP_TRANSCRIPTS,
        )
        transcript_cleanup = prune_files_by_mtime(
            _safe_transcript_artifact_files(),
            keep_transcripts,
            active_artifact_paths(done, state_path=store.path),
            False,
        )
        return {
            "status": done.status,
            "message": "recording finished without transcript" if not text.strip() else "transcription completed",
            "transcript": _transcript_payload_text(text, transcript_encryption, args),
            "transcript_output_redacted": bool(text) and transcript_encryption != ARTIFACT_ENCRYPTION_OFF and not _confirm_plaintext_transcript_output(args),
            "transcript_path": str(stored_text_path),
            "artifact_encryption": artifact_encryption,
            "transcript_encryption": transcript_encryption,
            "transcript_encrypted": transcript_encryption != ARTIFACT_ENCRYPTION_OFF,
            "recording_encryption": recording_encryption,
            "recording_encrypted": recording_encryption != ARTIFACT_ENCRYPTION_OFF,
            "inserted": inserted,
            "security": security_post_processing,
            "recording_artifact_cap": artifact_cleanup,
            "transcript_file_cap": transcript_cleanup,
            "language": language,
            "recording_artifacts_kept": keep_recording_artifacts,
            "audio_deleted": audio_deleted,
            "log_deleted": log_deleted,
        }
    except Exception as exc:
        error_text = _redact_error_for_user(str(exc))
        # Refresh state once more on error so the most recent status is persisted.
        if state_marked_finalizing:
            state = store.read()
            if not isinstance(state, RecordingState):
                state = store.read()
            if trimmed_audio_path is not None:
                remove_file(str(trimmed_audio_path), suffix=".flac")
            stabilized_audio_deleted = False
            if stabilized_audio_path is not None and str(state.audio_path or "") != str(stabilized_audio_path):
                stabilized_audio_deleted = remove_file(str(stabilized_audio_path), suffix=stabilized_audio_path.suffix)
            error_update: dict[str, object] = {
                "status": "error",
                "stopped_at": now_iso(),
                "error": error_text,
            }
            if (
                stabilized_audio_path is not None
                and str(state.audio_path or "") == str(stabilized_audio_path)
                and (stabilized_audio_deleted or _recording_artifact_stat(stabilized_audio_path) is None)
            ):
                error_update["audio_path"] = ""
            if written_text_path is not None:
                try:
                    _remove_transcript_file(written_text_path)
                except RuntimeError as cleanup_exc:
                    error_update["error"] = f"{error_text}; {cleanup_exc}"
                else:
                    error_update["transcript"] = ""
                    error_update["transcript_path"] = ""
            final_error_text = str(error_update.get("error", error_text))
            cleanup_targets: list[tuple[str, str, str]] = []
            cleanup_clear_update: dict[str, object] = {}
            if not keep_recording_artifacts:
                if audio_suffix and _recording_artifact_stat(audio_path) is not None:
                    cleanup_clear_update["audio_path"] = ""
                    cleanup_targets.append(("audio_path", str(audio_path), audio_suffix))
                if state.log_path:
                    cleanup_clear_update["log_path"] = ""
                    cleanup_targets.append(("log_path", state.log_path, ".log"))
            try:
                store.update(**error_update)
            except Exception as update_exc:
                update_error = _redact_error_for_user(str(update_exc))
                raise RuntimeError(f"{final_error_text}; failed to persist error state: {update_error}") from update_exc
            if cleanup_clear_update:
                try:
                    store.update(**cleanup_clear_update)
                except Exception as update_exc:
                    update_error = _redact_error_for_user(str(update_exc))
                    raise RuntimeError(f"{final_error_text}; failed to persist error cleanup state: {update_error}") from update_exc
                cleanup_restore_update: dict[str, object] = {}
                for cleanup_field, cleanup_path, cleanup_suffix in cleanup_targets:
                    if not remove_file(cleanup_path, suffix=cleanup_suffix):
                        cleanup_restore_update[cleanup_field] = cleanup_path
                if cleanup_restore_update:
                    try:
                        store.update(**cleanup_restore_update)
                    except Exception as update_exc:
                        update_error = _redact_error_for_user(str(update_exc))
                        raise RuntimeError(
                            f"{final_error_text}; failed to persist error cleanup state: {update_error}"
                        ) from update_exc
        raise RuntimeError(str(error_update.get("error", error_text)) if state_marked_finalizing else error_text)
    finally:
        _release_finalization_lock(lock_path)


def remove_file(path_value: str | None, *, suffix: str | None = None, recordings_root: Path | None = None) -> bool:
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
    try:
        return _unlink_regular_leaf_with_parent_fsync(
            path,
            field_name="recording artifact",
            expected_stat=file_stat,
        )
    except RuntimeError:
        return False


def command_stop(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    state = store.read()
    _raise_if_state_unreadable(state)
    if state.status != "recording":
        if state.status == "finalizing":
            if state.audio_path:
                return finalize_recording(args, store, state)
            return {"status": "finalizing", "message": "finalization in progress"}
        if state.status in {"recorded", "processing"} and state.audio_path:
            return finalize_recording(args, store, state)
        return {"status": state.status, "message": "not recording"}

    if _recording_process_verified_alive(state):
        stop_process(_coerce_int(state.pid, field_name="state pid"))
    state = store.update(
        status="recorded",
        stopped_at=now_iso(),
        error="",
        inserted=False,
    )
    return finalize_recording(args, store, state)


def command_cancel(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    store = build_store(args)
    lock_path = _acquire_finalization_lock(store.path)
    if lock_path is None:
        return {
            "status": "finalizing",
            "message": "finalization in progress; use cancel after completion",
        }
    try:
        state = store.read()
        _raise_if_state_unreadable(state)
        if state.status == "finalizing":
            return {
                "status": "finalizing",
                "message": "finalization in progress; use cancel after completion",
            }
        if state.status == "recording" and _recording_process_verified_alive(state):
            stop_process(_coerce_int(state.pid, field_name="state pid"))

        discarded_audio_path = state.audio_path
        has_artifacts = bool(discarded_audio_path or state.log_path or state.transcript_path)
        has_recording_state = state.status in {"recording", "recorded", "processing", "finalizing"}
        if not has_artifacts and not has_recording_state:
            return {"status": "idle", "message": "nothing to cancel"}

        error_message = "discarding recording artifacts"
        store.write(
            RecordingState(
                status="error",
                audio_path=state.audio_path,
                log_path=state.log_path,
                transcript_path=state.transcript_path,
                stopped_at=now_iso(),
                language=state.language,
                recorder=state.recorder,
                input_device=state.input_device,
                max_seconds=state.max_seconds,
                error=error_message,
            )
        )

        audio_deleted = _remove_recording_artifact(discarded_audio_path)
        log_deleted = remove_file(state.log_path, suffix=".log")
        transcript_path = normalized_path(state.transcript_path)
        transcript_deleted = True
        if transcript_path is not None:
            try:
                transcript_deleted = _remove_transcript_file(transcript_path)
            except RuntimeError:
                transcript_deleted = False
        if (
            (discarded_audio_path and not audio_deleted)
            or (state.log_path and not log_deleted)
            or (state.transcript_path and not transcript_deleted)
        ):
            error_message = "failed to discard recording artifacts"
            store.write(
                RecordingState(
                    status="error",
                    audio_path=state.audio_path if not audio_deleted else None,
                    log_path=state.log_path if not log_deleted else None,
                    transcript_path=state.transcript_path if not transcript_deleted else "",
                    stopped_at=now_iso(),
                    language=state.language,
                    recorder=state.recorder,
                    input_device=state.input_device,
                    max_seconds=state.max_seconds,
                    error=error_message,
                )
            )
            return {
                "status": "error",
                "message": error_message,
                "discarded_audio_path": discarded_audio_path,
                "audio_deleted": audio_deleted,
                "log_deleted": log_deleted,
                "transcript_deleted": transcript_deleted,
            }
        store.write(
            RecordingState(
                status="idle",
                stopped_at=now_iso(),
                language=state.language,
                recorder=state.recorder,
                input_device=state.input_device,
                max_seconds=state.max_seconds,
            )
        )
        return {
            "status": "idle",
            "message": "recording discarded",
            "discarded_audio_path": discarded_audio_path,
            "audio_deleted": audio_deleted,
            "log_deleted": log_deleted,
            "transcript_deleted": transcript_deleted,
        }
    finally:
        _release_finalization_lock(lock_path)


def command_toggle(args: argparse.Namespace) -> dict[str, object]:
    store = build_store(args)
    state = store.read()
    _raise_if_state_unreadable(state)
    if state.status == "finalizing":
        if state.audio_path:
            return finalize_recording(args, store, state)
        return {"status": "finalizing", "message": "finalization in progress"}
    if state.status == "recording":
        if _recording_process_verified_alive(state):
            return command_stop(args)
        if state.audio_path:
            store.update(status="processing", stopped_at=state.stopped_at or now_iso())
            return command_stop(args)
    return command_start(args)


def command_status(args: argparse.Namespace) -> dict[str, object]:
    state = build_store(args).read()
    payload = asdict(state)
    if state.error.startswith("state file "):
        payload["status"] = "error"
        return payload
    if state.status == "recording":
        try:
            verified_alive = _recording_process_verified_alive(state)
        except RuntimeError as exc:
            payload["status"] = "error"
            payload["message"] = str(exc)
            return payload
        if not verified_alive:
            payload["status"] = "recorded"
            payload["message"] = "recording process has exited; run stop to transcribe"
    if payload.get("status") in {"recording", "recorded"}:
        microphone_level = _recording_level_payload(state)
        if microphone_level is not None:
            payload["microphone_level"] = microphone_level
    return payload


def command_doctor(args: argparse.Namespace) -> dict[str, object]:
    settings = _parse_cli_settings_json(getattr(args, "settings_json", ""))
    applet = _coerce_bool(getattr(args, "applet", False), field_name="applet")
    return doctor_report(settings, applet=applet)


def command_setup(args: argparse.Namespace) -> dict[str, object]:
    settings = _parse_cli_settings_json(getattr(args, "settings_json", ""))
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
    return {"status": "done", "models": _normalize_model_payloads(list_models())}


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
        api_key = _assert_clean_text(
            getattr(args, "openai_compatible_api_key", ""),
            field_name="openai-compatible API key",
            max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
        )
        payload = _normalize_text_models_payload(list_openai_compatible_models(url, api_key=api_key))
        return {
            "status": "done",
            "backend": "openai-compatible",
            "url": url,
            **payload,
        }
    url = _validate_text_model_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
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
    url = _validate_text_model_url(args.ollama_url or DEFAULT_OLLAMA_URL, field_name="ollama url")
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
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            proc = subprocess.run(  # nosec B603
                [ollama, "pull", model],
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=OLLAMA_PULL_TIMEOUT_SECONDS,
                env=env,
                shell=False,
            )
            stdout = _read_binary_output(stdout_file, MAX_LOG_EXCERPT_CHARS, field_name="ollama pull stdout")
            stderr = _read_binary_output(stderr_file, MAX_LOG_EXCERPT_CHARS, field_name="ollama pull stderr")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ollama pull timed out after {OLLAMA_PULL_TIMEOUT_SECONDS}s") from exc
    except OSError as exc:
        raise RuntimeError(f"failed to run ollama pull: {exc}") from exc
    if proc.returncode != 0:
        detail = (stderr or stdout or f"exit code {proc.returncode}").strip()
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
    return download_model(args.model, force)


def command_remove_model(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    return remove_model(args.model)


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


def _benchmark_model(audio_path: Path, language: str, model: ModelSpec) -> dict[str, object]:
    path = model_path(model)
    status = model_status(model, verify=True)
    downloaded = bool(status.get("downloaded"))
    result: dict[str, object] = {
        "model": model.name,
        "path": str(path),
        "downloaded": downloaded,
        "compatible": model_supports_language(path, language),
        "ok": False,
        "seconds": None,
        "transcript": "",
        "error": "",
    }
    if not downloaded:
        result["error"] = f"model is not downloaded: {model.name}"
        return result
    if not model_supports_language(path, language):
        result["error"] = f"model does not support language: {language}"
        return result

    started = time.perf_counter()
    try:
        text = transcribe(
            audio_path=audio_path,
            language=language,
            text_path=transcript_dir() / f"{audio_path.stem}-{model.name}.txt",
            command_template="",
            backend=model.backend,
            whisper_model=str(path),
            personal_context="",
            vocabulary="",
            openai_compatible_model=DEFAULT_OPENAI_COMPATIBLE_MODEL,
            openai_compatible_url=DEFAULT_OPENAI_COMPATIBLE_URL,
            openai_compatible_api_key="",
        )
    except Exception as exc:
        result["seconds"] = round(time.perf_counter() - started, 3)
        result["error"] = _redact_error_for_user(str(exc))
        return result

    result["ok"] = True
    result["seconds"] = round(time.perf_counter() - started, 3)
    result["transcript"] = text.strip()
    result["characters"] = len(text.strip())
    result["words"] = len(text.strip().split())
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
        "audio_path": str(audio_path),
        "language": language,
        "fastest_model": fastest["model"] if fastest else "",
        "results": results,
    }


def command_history(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    limit = _coerce_int(args.limit, field_name="history limit", max_value=MAX_HISTORY_LIMIT)
    transcripts = read_transcript_history(limit)
    if not bool(getattr(args, "confirm_plaintext", False)):
        transcripts = _redact_history_previews(transcripts)
    return {"status": "done", "transcripts": transcripts}


def command_transcripts_document(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    limit = _coerce_int(args.limit, field_name="history limit", max_value=MAX_HISTORY_LIMIT)
    if not bool(getattr(args, "confirm_plaintext", False)):
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
    output_path, count, encryption = write_transcripts_export(
        limit,
        encryption_mode=args.artifact_encryption,
        plaintext=args.plaintext,
        confirm_plaintext=args.confirm_plaintext,
    )
    return {
        "status": "done",
        "path": str(output_path),
        "transcripts": count,
        "encryption": encryption,
        "plaintext": args.plaintext,
        "encrypted": encryption != ARTIFACT_ENCRYPTION_OFF and not args.plaintext,
    }


def command_cleanup(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    keep_transcripts = _coerce_int(args.keep_transcripts, field_name="keep-transcripts", max_value=MAX_KEEP_TRANSCRIPTS)
    keep_recordings = _coerce_int(args.keep_recordings, field_name="keep-recordings", max_value=MAX_KEEP_RECORDINGS)
    recording_max_age_days = _coerce_int(
        args.recording_max_age_days,
        field_name="recording-max-age-days",
        max_value=MAX_RECORDING_MAX_AGE_DAYS,
    )
    dry_run = _coerce_bool(args.dry_run, field_name="dry-run")
    store = build_store(args)
    state = store.read()
    active_paths = active_artifact_paths(state, state_path=store.path)
    transcript_result = prune_files_by_mtime(
        _safe_transcript_artifact_files(),
        keep_transcripts,
        active_paths,
        dry_run,
    )
    recording_result = prune_recording_groups(keep_recordings, active_paths, dry_run, recording_max_age_days)
    deleted_transcripts = len(transcript_result["deleted_paths"])
    deleted_recordings = _coerce_int(recording_result["deleted_recordings"], field_name="deleted-recordings")  # type: ignore[arg-type]
    deleted_logs = _coerce_int(recording_result["deleted_logs"], field_name="deleted-logs")  # type: ignore[arg-type]
    would_delete_transcripts = len(transcript_result["planned_paths"])
    would_delete_recordings = _coerce_int(recording_result["planned_recordings"], field_name="planned-recordings")  # type: ignore[arg-type]
    would_delete_logs = _coerce_int(recording_result["planned_logs"], field_name="planned-logs")  # type: ignore[arg-type]
    total = (
        would_delete_transcripts + would_delete_recordings + would_delete_logs
        if dry_run
        else deleted_transcripts + deleted_recordings + deleted_logs
    )
    verb = "would clean" if dry_run else "cleaned"
    return {
        "status": "done",
        "message": f"{verb} {total} old file(s)",
        "dry_run": dry_run,
        "keep_transcripts": keep_transcripts,
        "keep_recordings": keep_recordings,
        "recording_max_age_days": recording_max_age_days,
        "deleted_transcripts": deleted_transcripts,
        "deleted_recordings": deleted_recordings,
        "deleted_logs": deleted_logs,
        "would_delete_transcripts": would_delete_transcripts,
        "would_delete_recordings": would_delete_recordings,
        "would_delete_logs": would_delete_logs,
        "deleted_paths": transcript_result["deleted_paths"] + recording_result["deleted_paths"],
        "would_delete_paths": transcript_result["planned_paths"] + recording_result["planned_paths"],
        "failed_paths": transcript_result["failed_paths"] + recording_result["failed_paths"],
        "skipped_active_paths": transcript_result["skipped_active_paths"] + recording_result["skipped_active_paths"],
    }


def command_diagnostics(args: argparse.Namespace) -> dict[str, object]:
    payload = build_diagnostics_payload(args)
    output = str(getattr(args, "output", "") or "").strip()
    save = _coerce_bool(getattr(args, "save", False), field_name="save")
    if output or save:
        path = (
            _require_json_path(output, field_name="diagnostics output")
            if output
            else diagnostics_dir() / f"diagnostics-{timestamp()}.json"
        )
        _assert_json_payload_size(payload, max_bytes=MAX_DIAGNOSTICS_JSON_BYTES)
        payload["saved_path"] = str(path)
        _write_json_atomic(path, payload, max_bytes=MAX_DIAGNOSTICS_JSON_BYTES)
        payload["message"] = f"diagnostics saved to {path}"
    return payload


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


def build_diagnostics_payload(args: argparse.Namespace) -> dict[str, object]:
    settings_json = getattr(args, "settings_json", "")
    settings = _parse_cli_settings_json(settings_json)
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
        {key: entry[key] for key in ("name", "path", "modified_at") if key in entry}
        for entry in read_transcript_history(5)
    ]
    state_payload = asdict(build_store(args).read())
    state_payload["transcript_length"] = len(str(state_payload.get("transcript") or ""))
    state_payload.pop("transcript", None)
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
            "state_dir": str(state_dir()),
            "state_file": str(state_file_path),
            "transcript_dir": str(transcript_dir()),
            "recordings_dir": str(recordings_dir()),
            "diagnostics_dir": str(diagnostics_dir()),
        },
        "state": state_payload,
        "doctor": doctor_report(settings, applet=applet),
        "inputs": source_payload,
        "models": _normalize_model_payloads(list_models()),
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
    path = _require_json_path(args.output, field_name="settings export output", default=default_settings_export_file())
    payload = write_export(path, settings, load_alarm_store())
    return {
        "status": "done",
        "message": f"settings exported to {path}",
        "path": str(path),
        "settings_count": len(payload["settings"]),
        "alarms_count": len(payload["alarms"]["alarms"]),
    }


def command_settings_import(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    path = _require_json_path(args.input, field_name="settings import input", default=default_settings_export_file())
    payload = read_export(path)
    save_alarm_store(payload["alarms"])
    include_settings = _coerce_bool(
        getattr(args, "confirm_plaintext_settings_output", False),
        field_name="confirm_plaintext_settings_output",
    )
    result: dict[str, object] = {
        "status": "done",
        "message": f"settings imported from {path}",
        "path": str(path),
        "settings_count": len(payload["settings"]),
        "alarms_count": len(payload["alarms"]["alarms"]),
        "export_version": payload["version"],
    }
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
    return {
        "status": "done",
        "path": str(path),
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
    inserted = insert_text(text, args.insert_method, typing_delay_ms)
    return {"status": "done", "inserted": inserted}


def command_transcribe_file(args: argparse.Namespace) -> dict[str, object]:
    ensure_runtime_dirs()
    audio_path = _coerce_path(args.audio_path, field_name="audio file path", max_chars=MAX_AUDIO_PATH_CHARS)
    language = _validate_pipeline_text_args(args, language=args.language)
    normalized_transcriber = normalize_backend(args.transcriber)
    audio_path = validate_audio_file(audio_path)
    text_path = transcript_dir() / f"{audio_path.stem}.txt"
    artifact_encryption = _artifact_encryption_mode(args)
    transcriber_text_path = _transcript_work_path(text_path, artifact_encryption)
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
    finally:
        _remove_transient_transcript_path(transcriber_text_path, text_path)
    if _is_empty_transcript_text(text):
        text = ""
        security_post_processing = _empty_security_post_processing()
    else:
        text, security_post_processing = _process_transcript(text, args, args.language)
    if text.strip() and _coerce_bool(getattr(args, "soften_profanity", False), field_name="soften_profanity"):
        text = soften_profanity_text(text)
    stored_text_path, transcript_encryption = _write_stored_transcript(text_path, text.strip() + "\n", args)
    keep_transcripts = _coerce_int(
        getattr(args, "keep_transcripts", DEFAULT_KEEP_TRANSCRIPTS),
        field_name="keep-transcripts",
        max_value=MAX_KEEP_TRANSCRIPTS,
    )
    transcript_cleanup = prune_files_by_mtime(
        _safe_transcript_artifact_files(),
        keep_transcripts,
        {stored_text_path},
        False,
    )
    return {
        "status": "done",
        "transcript": _transcript_payload_text(text, transcript_encryption, args),
        "transcript_output_redacted": bool(text) and transcript_encryption != ARTIFACT_ENCRYPTION_OFF and not _confirm_plaintext_transcript_output(args),
        "transcript_path": str(stored_text_path),
        "security": security_post_processing,
        "transcript_file_cap": transcript_cleanup,
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
    parser.add_argument("--post-process-backend", default="none", choices=["none", "command", "ollama", "openai-compatible"])
    parser.add_argument("--post-process-command", default="")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--ollama-model", default="")
    parser.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    parser.add_argument("--openai-compatible-model", default=DEFAULT_OPENAI_COMPATIBLE_MODEL)
    parser.add_argument("--openai-compatible-text-model", default=DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL)
    parser.add_argument("--openai-compatible-api-key", default="")
    parser.add_argument(
        "--openai-compatible-flex-processing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="use OpenAI-compatible flex processing for speech-to-text and text polishing requests; default: enabled",
    )
    parser.add_argument("--post-process-prompt", default="")
    parser.add_argument("--personal-context", default="")
    parser.add_argument("--vocabulary", default="")
    parser.add_argument(
        "--insert-method",
        default="clipboard-paste",
        choices=["clipboard-paste", "clipboard", "type", "none"],
    )
    parser.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
    parser.add_argument("--keep-transcripts", type=int, default=DEFAULT_KEEP_TRANSCRIPTS)
    parser.add_argument(
        "--artifact-encryption",
        default=ARTIFACT_ENCRYPTION_OFF,
        choices=ARTIFACT_ENCRYPTION_CHOICES,
        help=(
            "encrypt stored transcripts and retained recordings: off, passphrase, or keyring; "
            "keyring falls back to passphrase when the Secret Service CLI path is unavailable; "
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speed-of-cinnamon")
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
    status.set_defaults(handler=command_status)

    doctor = subparsers.add_parser("doctor")
    add_common_options(doctor)
    doctor.add_argument("--settings-json", default="")
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
    text_models.add_argument("--openai-compatible-api-key", default="")
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
    transcripts_export.set_defaults(handler=command_transcripts_export)

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
        "--applet",
        action="store_true",
        help="evaluate doctor readiness for the Cinnamon applet path",
    )
    diagnostics.set_defaults(handler=command_diagnostics)

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
    settings_import.set_defaults(handler=command_settings_import)

    profanity_filter_document = subparsers.add_parser("profanity-filter-document")
    add_common_options(profanity_filter_document)
    profanity_filter_document.set_defaults(handler=command_profanity_filter_document)

    insert = subparsers.add_parser("insert-text")
    add_common_options(insert)
    insert.add_argument("text")
    insert.add_argument("--insert-method", default="clipboard-paste", choices=["clipboard-paste", "clipboard", "type", "none"])
    insert.add_argument("--typing-delay-ms", type=int, default=DEFAULT_TYPING_DELAY_MS)
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
    transcribe_file.add_argument("--post-process-backend", default="none", choices=["none", "command", "ollama", "openai-compatible"])
    transcribe_file.add_argument("--post-process-command", default="")
    transcribe_file.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    transcribe_file.add_argument("--ollama-model", default="")
    transcribe_file.add_argument("--openai-compatible-url", default=DEFAULT_OPENAI_COMPATIBLE_URL)
    transcribe_file.add_argument("--openai-compatible-model", default=DEFAULT_OPENAI_COMPATIBLE_MODEL)
    transcribe_file.add_argument("--openai-compatible-text-model", default=DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL)
    transcribe_file.add_argument("--openai-compatible-api-key", default="")
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
            "encrypt the stored transcript: off, passphrase, or keyring; passphrase uses "
            "SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE_FILE, an existing "
            "~/.config/speed-of-cinnamon/artifact.key, SPEED_OF_CINNAMON_ENCRYPTION_PASSPHRASE, "
            "or generates ~/.config/speed-of-cinnamon/artifact.key at runtime; weak default key files are regenerated"
        ),
    )
    transcribe_file.add_argument("--soften-profanity", action="store_true")
    transcribe_file.add_argument(
        "--confirm-plaintext-output",
        action="store_true",
        help="allow full transcript text in command output even when the stored transcript is encrypted",
    )
    transcribe_file.set_defaults(handler=command_transcribe_file)
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    json_output = False
    command_name = str(getattr(args, "command", "unknown"))
    try:
        configure_logging(getattr(args, "log_level", DEFAULT_LOG_LEVEL))
        json_output = _coerce_bool(getattr(args, "json", False), field_name="json")
        log_event("info", "command_start", command=command_name)
        payload = _redact_error_payload(args.handler(args))
        if "error" in payload and payload["error"] is not None:
            payload["error"] = _redact_error_for_user(payload["error"])
        status = str(payload.get("status", "ok"))
        if payload.get("error"):
            log_event(
                "error",
                "command_error",
                command=command_name,
                status=status,
                error_type="payload",
                error_message=_redact_error_for_user(payload.get("error", "")),
            )
        else:
            log_event("info", "command_done", command=command_name, status=status)
        print_result(payload, json_output)
        return 0 if not payload.get("error") else 1
    except Exception as exc:
        error_message = _redact_error_for_user(str(exc))
        log_event(
            "error",
            "command_exception",
            command=command_name,
            error_type=exc.__class__.__name__,
            error_message=error_message,
        )
        payload = {"status": "error", "error": error_message}
        print_result(payload, json_output)
        return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
