from __future__ import annotations

import json
import os
import secrets
import stat as stat_module
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .alarms import MAX_ALARM_COUNT, MAX_ALARM_TRIGGER_CHARS, STORE_VERSION as ALARM_STORE_VERSION
from .alarms import _dedupe_alarm_ids
from .alarms import normalize_alarm
from .http_safety import is_loopback_hostname
from .paths import APP_ID
from .postprocessor import MAX_OLLAMA_MODEL_CHARS, MAX_OPENAI_COMPATIBLE_MODEL_CHARS
from .recorder import MAX_RECORDING_INPUT_DEVICE_CHARS, MAX_RECORDING_SECONDS
from .transcriber import normalize_backend
from .path_safety import (
    assert_fd_is_private_directory,
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    _fsync_fd,
    _rename_exchange,
    _rename_without_replacing,
)

EXPORT_VERSION = 2
MAX_SETTINGS_EXPORT_BYTES = 1_000_000
MAX_SETTINGS_TEXT_CHARS = 4_096
MAX_SETTINGS_URL_CHARS = 2_048
MAX_SETTINGS_EXPORT_PATH_CHARS = 4_096
MAX_SETTINGS_EXPORT_JSON_DEPTH = 24
MAX_SETTINGS_EXPORT_JSON_TOKENS = 20_000
MAX_SETTINGS_EXPORT_JSON_NODES = 10_000
MAX_TYPING_DELAY_MS = 10_000
DEFAULT_MAX_SECONDS = 30
DEFAULT_TYPING_DELAY_MS = 8
DEFAULT_MAX_TRANSCRIPT_FILES = 500
MAX_TRANSCRIPT_FILES = 1_000
MIN_RECORDING_SECONDS = 0
MIN_TYPING_DELAY_MS = 0
POST_COMMIT_RECOVERY_BACKUP_CLEANUP_WARNING = (
    "settings export committed but settings export recovery backup cleanup failed; "
    "private backup data may remain"
)
POST_COMMIT_DIRECTORY_CLOSE_WARNING = "settings export committed but settings export directory close failed"


def _note_cleanup_failure(primary: BaseException, _cleanup_error: BaseException) -> None:
    primary.add_note("settings export cleanup failed")


NON_EXPORTABLE_PRIVATE_SETTINGS: tuple[str, ...] = (
    "cli-path",
    "openai-compatible-api-key",
    "personal-context",
    "vocabulary",
    "post-process-command",
    "post-process-prompt",
    "transcriber-command",
)

_STATUS_ICON_VALUES = frozenset(
    {"soc-original"}
    | {
        f"{family}-{index:02d}"
        for family in ("ready", "recording", "processing", "recorded", "error", "setup")
        for index in range(1, 52)
    }
)

EXPORTABLE_SETTINGS: dict[str, tuple[type, Any]] = {
    "toggle-keybinding": (str, "<Super>z::"),
    "primary-language-keybinding": (str, ""),
    "secondary-language-keybinding": (str, ""),
    "cancel-keybinding": (str, ""),
    "show-panel-label": (bool, True),
    "show-transcript-text": (bool, True),
    "language": (str, "en"),
    "secondary-language": (str, "de"),
    "max-seconds": (int, DEFAULT_MAX_SECONDS),
    "auto-transcribe-timeout": (bool, True),
    "auto-relisten": (bool, False),
    "keep-recording-artifacts": (bool, False),
    "recorder": (str, "auto"),
    "input-device": (str, ""),
    "personal-context": (str, ""),
    "vocabulary": (str, ""),
    "notify-recording": (bool, False),
    "notify-complete": (bool, False),
    "notify-error": (bool, True),
    "status-icon-ready": (str, "soc-original"),
    "status-icon-recording": (str, "soc-original"),
    "status-icon-processing": (str, "soc-original"),
    "status-icon-recorded": (str, "soc-original"),
    "status-icon-error": (str, "soc-original"),
    "status-icon-setup": (str, "soc-original"),
    "insert-method": (str, "clipboard-paste"),
    "append-space": (bool, True),
    "sanitize-special-chars": (bool, False),
    "soften-profanity": (bool, False),
    "typing-delay-ms": (int, DEFAULT_TYPING_DELAY_MS),
    "max-transcript-files": (int, DEFAULT_MAX_TRANSCRIPT_FILES),
    "artifact-encryption": (str, "keyring"),
    "auto-paste-window-title": (str, "codex"),
    "transcriber": (str, "auto"),
    "whisper-model": (str, ""),
    "post-process-backend": (str, "none"),
    "ollama-url": (str, "http://127.0.0.1:11434"),
    "ollama-model": (str, ""),
    "openai-compatible-url": (str, "https://api.openai.com/v1"),
    "openai-compatible-model": (str, "gpt-4o-transcribe"),
    "openai-compatible-text-model": (str, "gpt-4o-mini"),
    "openai-compatible-flex-processing": (bool, True),
    "post-process-preset": (str, "minimal"),
    "post-process-preserve-code": (bool, True),
    "post-process-never-add-content": (bool, True),
    "post-process-mask-sensitive-data": (bool, False),
    "post-process-prompt": (str, ""),
}

_ALLOWED_SETTING_TEXT_VALUES: dict[str, frozenset[str]] = {
    "status-icon-ready": _STATUS_ICON_VALUES,
    "status-icon-recording": _STATUS_ICON_VALUES,
    "status-icon-processing": _STATUS_ICON_VALUES,
    "status-icon-recorded": _STATUS_ICON_VALUES,
    "status-icon-error": _STATUS_ICON_VALUES,
    "status-icon-setup": _STATUS_ICON_VALUES,
    "artifact-encryption": frozenset({"keyring", "passphrase", "off"}),
    "insert-method": frozenset({"clipboard-paste", "clipboard", "type", "none"}),
    "language": frozenset({
        "ar",
        "zh",
        "cs",
        "da",
        "en",
        "fi",
        "de",
        "el",
        "fr",
        "hi",
        "it",
        "ja",
        "ko",
        "nl",
        "no",
        "pl",
        "pt",
        "ru",
        "es",
        "sv",
        "tr",
        "uk",
    }),
    "post-process-backend": frozenset({"none", "command", "ollama", "openai-compatible"}),
    "post-process-preset": frozenset({"minimal", "clean", "code", "chat", "email", "safety", "custom"}),
    "recorder": frozenset({"auto", "pw-record", "parecord", "arecord"}),
    "secondary-language": frozenset({
        "ar",
        "zh",
        "cs",
        "da",
        "en",
        "fi",
        "de",
        "el",
        "fr",
        "hi",
        "it",
        "ja",
        "ko",
        "nl",
        "no",
        "pl",
        "pt",
        "ru",
        "es",
        "sv",
        "tr",
        "uk",
    }),
    "transcriber": frozenset({"auto", "whisper", "faster-whisper", "whisper-cpp", "openai-compatible", "command"}),
}


def _utf8_byte_count(value: str, *, field_name: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise SettingsExportError(f"{field_name} contains invalid Unicode characters") from exc


class SettingsExportError(RuntimeError):
    pass


def _required_nonblock_flag(field_name: str) -> int:
    flag = getattr(os, "O_NONBLOCK", None)
    if type(flag) is not int or flag <= 0:
        raise SettingsExportError(f"{field_name} requires nonblocking file access")
    return flag


def _assert_clean_path(path: Path, *, field_name: str) -> None:
    if not isinstance(path, Path):
        raise SettingsExportError(f"{field_name} must be a path")
    text = str(path)
    if not text or len(text) > MAX_SETTINGS_EXPORT_PATH_CHARS:
        raise SettingsExportError(f"{field_name} path is invalid")
    try:
        if _utf8_byte_count(text, field_name=f"{field_name} path") > MAX_SETTINGS_EXPORT_PATH_CHARS:
            raise SettingsExportError(f"{field_name} path is invalid")
    except SettingsExportError as exc:
        raise SettingsExportError(f"{field_name} path is invalid") from exc
    if _contains_escaped_null(text):
        raise SettingsExportError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(text):
        raise SettingsExportError(f"{field_name} contains invalid control character")
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
    except RuntimeError as exc:
        raise SettingsExportError(str(exc)) from exc


def _contains_escaped_null(text: str) -> bool:
    if isinstance(text, bool) or not isinstance(text, str):
        raise SettingsExportError("value must be text")
    lowered = (text or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(text: str, *, allow_newline: bool = False) -> bool:
    if isinstance(text, bool) or not isinstance(text, str):
        raise SettingsExportError("value must be text")
    lowered = (text or "").lower()
    control_codepoints = tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))
    if any(sequence in lowered for sequence in ("\\a", "\\b", "\\f", "\\n", "\\r", "\\t", "\\v")):
        return True
    if any(f"\\x{codepoint:02x}" in lowered or f"\\u00{codepoint:02x}" in lowered for codepoint in control_codepoints):
        return True
    for char in lowered:
        codepoint = ord(char)
        if allow_newline and codepoint == 0x0A:
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _assert_json_text_budget(text: str) -> None:
    depth = 0
    tokens = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char in "{[":
            depth += 1
            tokens += 1
            if depth > MAX_SETTINGS_EXPORT_JSON_DEPTH:
                raise SettingsExportError("settings export JSON is too deeply nested")
        elif char in "}]":
            depth -= 1
        elif char in ":,":
            tokens += 1
        if tokens > MAX_SETTINGS_EXPORT_JSON_TOKENS:
            raise SettingsExportError("settings export JSON is too complex")


def _assert_json_value_budget(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SETTINGS_EXPORT_JSON_NODES:
            raise SettingsExportError("settings export JSON is too complex")
        if depth > MAX_SETTINGS_EXPORT_JSON_DEPTH:
            raise SettingsExportError("settings export JSON is too deeply nested")
        if isinstance(item, dict):
            nodes += len(item)
            if nodes > MAX_SETTINGS_EXPORT_JSON_NODES:
                raise SettingsExportError("settings export JSON is too complex")
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)


def _reject_non_finite_json_number(_value: str) -> object:
    raise ValueError("settings export contains non-finite numbers")


def _create_private_temp_file(parent_fd: int, final_name: str) -> tuple[int, str]:
    safe_name = final_name.replace("/", "_") or "settings-export.json"
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise SettingsExportError("secure settings export temp file creation is not supported on this platform")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag | getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        temp_name = f".{safe_name}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(temp_name, flags, 0o600, dir_fd=parent_fd), temp_name
        except FileExistsError:
            continue
    raise SettingsExportError("failed to create settings export temp file")


def _scrub_settings_export_fd(
    fd: int,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    file_stat = os.fstat(fd)
    if expected_stat is not None and (
        file_stat.st_dev != expected_stat.st_dev
        or file_stat.st_ino != expected_stat.st_ino
        or file_stat.st_mode != expected_stat.st_mode
        or getattr(file_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
        or file_stat.st_size != expected_stat.st_size
        or file_stat.st_mtime_ns != expected_stat.st_mtime_ns
        or file_stat.st_ctime_ns != expected_stat.st_ctime_ns
    ):
        raise SettingsExportError("settings export temp file changed before scrubbing")
    if not stat_module.S_ISREG(file_stat.st_mode):
        raise SettingsExportError("settings export temp file must be a regular file")
    remaining = int(file_stat.st_size)
    if remaining > 0:
        os.lseek(fd, 0, os.SEEK_SET)
        chunk = b"\x00" * min(remaining, 65536)
        while remaining > 0:
            try:
                written = os.write(fd, chunk[: min(remaining, len(chunk))])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError("settings export temp file scrub made no progress")
            remaining -= written
        _fsync_fd(fd)
    while True:
        try:
            os.ftruncate(fd, 0)
            break
        except InterruptedError:
            continue
    _fsync_fd(fd)


def _scrub_temp_settings_export_file(
    parent_fd: int,
    temp_name: str,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    if not temp_name:
        return
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise SettingsExportError("secure settings export temp file scrubbing is not supported on this platform")
    nonblock_flag = _required_nonblock_flag("settings export temp file scrubbing")
    fd = os.open(
        temp_name,
        os.O_WRONLY | nofollow_flag | nonblock_flag | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    primary_error: BaseException | None = None
    try:
        _scrub_settings_export_fd(fd, expected_stat=expected_stat)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def _read_text_capped_without_following_symlinks(path: Path) -> str:
    nonblock_flag = _required_nonblock_flag("settings export path")
    fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name="settings export path")
    primary_error: BaseException | None = None
    try:
        try:
            assert_fd_is_regular_private_file(fd, field_name="settings export")
        except RuntimeError as exc:
            raise SettingsExportError(str(exc)) from exc
        file_stat = os.fstat(fd)
        if file_stat.st_size > MAX_SETTINGS_EXPORT_BYTES:
            raise SettingsExportError(f"settings export is too large: {path}")
        handle = os.fdopen(fd, "r", encoding="utf-8")
        fd = -1
        handle_primary_error: BaseException | None = None
        try:
            return handle.read(MAX_SETTINGS_EXPORT_BYTES + 1)
        except BaseException as exc:
            handle_primary_error = exc
            raise
        finally:
            try:
                handle.close()
            except BaseException as cleanup_error:
                if handle_primary_error is not None:
                    _note_cleanup_failure(handle_primary_error, cleanup_error)
                else:
                    raise
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise


def _sanitize_text_field(
    value: object,
    *,
    field_name: str,
    max_chars: int | None = None,
    allow_newline: bool = False,
) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise SettingsExportError(f"{field_name} must be text")
    if max_chars is None:
        max_chars = MAX_SETTINGS_TEXT_CHARS
    text = str(value or "")
    if _contains_escaped_null(text):
        raise SettingsExportError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(text, allow_newline=allow_newline):
        raise SettingsExportError(f"{field_name} contains invalid control character")
    text = text.strip()
    if len(text) > max_chars:
        raise SettingsExportError(f"{field_name} is too long")
    if _utf8_byte_count(text, field_name=field_name) > max_chars:
        raise SettingsExportError(f"{field_name} is too long (max {max_chars} bytes)")
    return text


def _reject_secret_bearing_url_setting(key: str, text: str) -> None:
    if key not in {"ollama-url", "openai-compatible-url"}:
        return
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise SettingsExportError(f"setting {key} is not a valid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SettingsExportError(f"setting {key} must use http:// or https://")
    if not parsed.hostname:
        raise SettingsExportError(f"setting {key} is missing hostname")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise SettingsExportError(f"setting {key} must use https:// unless host is local loopback")
    try:
        _ = parsed.port
    except ValueError:
        raise SettingsExportError(f"setting {key} has invalid port") from None
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise SettingsExportError(f"setting {key} must not contain URL credentials")
    if parsed.query or parsed.fragment:
        raise SettingsExportError(f"setting {key} must not contain URL query or fragment")


def normalize_setting(key: str, value: Any) -> Any:
    expected, default = EXPORTABLE_SETTINGS[key]
    if expected is bool:
        if not isinstance(value, bool):
            raise SettingsExportError(f"setting {key} must be a boolean")
        return value
    if expected is int:
        if isinstance(value, bool):
            raise SettingsExportError(f"setting {key} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            value = _sanitize_text_field(value, field_name=f"setting {key}")
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                raise SettingsExportError(f"setting {key} must be an integer") from None
        else:
            raise SettingsExportError(f"setting {key} must be an integer")
        if key == "max-seconds":
            if parsed < MIN_RECORDING_SECONDS:
                raise SettingsExportError(f"setting max-seconds must be at least {MIN_RECORDING_SECONDS}")
            if parsed > MAX_RECORDING_SECONDS:
                raise SettingsExportError(
                    f"setting max-seconds must be at most {MAX_RECORDING_SECONDS}"
                )
            return parsed
        if key == "typing-delay-ms":
            if parsed < MIN_TYPING_DELAY_MS:
                raise SettingsExportError(f"setting typing-delay-ms must be at least {MIN_TYPING_DELAY_MS}")
            if parsed > MAX_TYPING_DELAY_MS:
                raise SettingsExportError(
                    f"setting typing-delay-ms must be at most {MAX_TYPING_DELAY_MS}"
                )
            return parsed
        if key == "max-transcript-files":
            if parsed < 1:
                raise SettingsExportError("setting max-transcript-files must be at least 1")
            if parsed > MAX_TRANSCRIPT_FILES:
                raise SettingsExportError(
                    f"setting max-transcript-files must be at most {MAX_TRANSCRIPT_FILES}"
                )
            return parsed
        return parsed
    text_max_chars = {
        "input-device": MAX_RECORDING_INPUT_DEVICE_CHARS,
        "ollama-model": MAX_OLLAMA_MODEL_CHARS,
        "openai-compatible-model": MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        "openai-compatible-text-model": MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        "ollama-url": MAX_SETTINGS_URL_CHARS,
        "openai-compatible-url": MAX_SETTINGS_URL_CHARS,
    }.get(key, MAX_SETTINGS_TEXT_CHARS)
    text = _sanitize_text_field(
        value,
        field_name=f"setting {key}",
        max_chars=text_max_chars,
        allow_newline=key in {"personal-context", "vocabulary"},
    )
    if key == "transcriber":
        text = normalize_backend(text)
    _reject_secret_bearing_url_setting(key, text)
    allowed_values = _ALLOWED_SETTING_TEXT_VALUES.get(key)
    if allowed_values is not None and text not in allowed_values:
        raise SettingsExportError(f"setting {key} has unsupported value")
    return text


def normalize_settings(values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise SettingsExportError("settings export settings must be an object")
    normalized: dict[str, Any] = {}
    for key, (_, default) in EXPORTABLE_SETTINGS.items():
        if key in values:
            normalized[key] = normalize_setting(key, values[key])
        else:
            normalized[key] = default
    return normalized


def normalize_alarm_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SettingsExportError("settings export alarms must be an object")
    alarms = value.get("alarms", [])
    if not isinstance(alarms, list):
        raise SettingsExportError("settings export alarms must be a list")
    last_checked_at = _sanitize_text_field(
        value.get("last_checked_at", ""),
        field_name="settings export alarm last_checked_at",
        max_chars=MAX_ALARM_TRIGGER_CHARS,
    )
    normalized_alarms: list[dict[str, Any]] = []
    for raw_alarm in alarms:
        if len(normalized_alarms) >= MAX_ALARM_COUNT:
            break
        if not isinstance(raw_alarm, dict):
            continue
        try:
            normalized_alarms.append(normalize_alarm(raw_alarm))
        except (TypeError, ValueError):
            continue
    return {
        "version": ALARM_STORE_VERSION,
        "alarms": _dedupe_alarm_ids(normalized_alarms),
        "last_checked_at": last_checked_at,
    }


def normalize_excluded_private_settings(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SettingsExportError("settings export excluded private settings must be a list")
    allowed = set(NON_EXPORTABLE_PRIVATE_SETTINGS)
    normalized: list[str] = []
    for raw_item in value:
        if not isinstance(raw_item, str):
            continue
        item = _sanitize_text_field(raw_item, field_name="settings export excluded private setting")
        if item in allowed and item not in normalized:
            normalized.append(item)
    return normalized


def normalize_included_private_settings(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SettingsExportError("settings export included private settings must be a list")
    allowed = set(NON_EXPORTABLE_PRIVATE_SETTINGS)
    normalized: list[str] = []
    for raw_item in value:
        if not isinstance(raw_item, str):
            continue
        item = _sanitize_text_field(raw_item, field_name="settings export included private setting")
        if item in allowed and item not in normalized:
            normalized.append(item)
    return normalized


def build_export(
    settings: dict[str, Any],
    alarm_store: dict[str, Any] | None = None,
    *,
    include_private_settings: bool = False,
) -> dict[str, Any]:
    if type(include_private_settings) is not bool:
        raise SettingsExportError("settings export private setting opt-in must be boolean")
    normalized_alarm_store = normalize_alarm_store(alarm_store if alarm_store is not None else {})
    normalized_settings = normalize_settings(settings)
    if not include_private_settings:
        for key in NON_EXPORTABLE_PRIVATE_SETTINGS:
            normalized_settings.pop(key, None)
    included_private_settings = [
        key for key in NON_EXPORTABLE_PRIVATE_SETTINGS if key in normalized_settings
    ]
    excluded_private_settings = [
        key for key in NON_EXPORTABLE_PRIVATE_SETTINGS if key not in normalized_settings
    ]
    return {
        "app": APP_ID,
        "version": EXPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "excluded_private_settings": excluded_private_settings,
        "included_private_settings": included_private_settings,
        "speed_of_cinnamon_version": __version__,
        "settings": normalized_settings,
        "alarms": normalized_alarm_store,
    }


def write_export(
    path: Path,
    settings: dict[str, Any],
    alarm_store: dict[str, Any] | None = None,
    *,
    include_private_settings: bool = False,
) -> dict[str, Any]:
    _assert_clean_path(path, field_name="settings export path")
    if not path.is_absolute():
        raise SettingsExportError("settings export path must be absolute")
    payload = build_export(settings, alarm_store, include_private_settings=include_private_settings)
    try:
        rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    except (MemoryError, RecursionError) as exc:
        raise SettingsExportError("settings export could not be rendered") from exc
    if _utf8_byte_count(rendered, field_name="settings export payload") > MAX_SETTINGS_EXPORT_BYTES:
        raise SettingsExportError(f"settings export is too large: {path}")
    _required_nonblock_flag("settings export")
    parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name="settings export directory")
    temp_name = ""
    temp_fd: int | None = None
    backup_name = ""
    backup_moved = False
    activation_attempted = False
    activation_exchange = False
    activation_stat: os.stat_result | None = None
    temporary_stat: os.stat_result | None = None
    transaction_active = False
    committed = False
    primary_error: BaseException | None = None
    public_error: SettingsExportError | None = None
    public_error_cause: BaseException | None = None
    post_commit_warnings: list[str] = []

    def _report_post_commit_warning(message: str) -> None:
        if message in post_commit_warnings:
            return
        post_commit_warnings.append(message)
        try:
            warnings.warn(message, RuntimeWarning, stacklevel=3)
        except Exception:
            return

    def _note_cleanup_failure(
        primary: BaseException,
        _cleanup_error: BaseException | None = None,
    ) -> None:
        try:
            primary.add_note("settings export cleanup failed")
        except BaseException:
            pass

    def _sanitized_public_error_cause(error: BaseException) -> BaseException:
        if isinstance(error, OSError):
            cause = OSError("settings export operation failed")
            if "settings export cleanup failed" in getattr(error, "__notes__", ()):
                cause.add_note("settings export cleanup failed")
            return cause
        if isinstance(error, MemoryError):
            return MemoryError()
        return RecursionError()

    def _close_claim_fd(fd: int, primary: BaseException | None) -> bool:
        if fd < 0:
            return True
        try:
            os.close(fd)
        except BaseException:
            if primary is None:
                raise
            _note_cleanup_failure(primary)
            return False
        return True

    class _RecoveryBackupChanged(OSError):
        pass

    def _same_leaf_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            getattr(first, "st_nlink", 1),
            first.st_size,
            first.st_mtime_ns,
            first.st_ctime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            getattr(second, "st_nlink", 1),
            second.st_size,
            second.st_mtime_ns,
            second.st_ctime_ns,
        )

    def _same_leaf_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            getattr(first, "st_nlink", 1),
            first.st_size,
            first.st_mtime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            getattr(second, "st_nlink", 1),
            second.st_size,
            second.st_mtime_ns,
        )

    def _same_leaf_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (first.st_dev, first.st_ino, first.st_mode) == (second.st_dev, second.st_ino, second.st_mode)

    def _unlink_temp_if_same() -> None:
        nonlocal temporary_stat
        if not temp_name:
            return
        nonblock_flag = _required_nonblock_flag("settings export temporary file cleanup")
        if temporary_stat is None:
            if temp_fd is None:
                raise OSError("settings export temporary file identity is unavailable")
            try:
                temporary_stat = os.fstat(temp_fd)
            except (OSError, ValueError) as exc:
                raise OSError("settings export temporary file identity is unavailable") from exc
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if nofollow_flag is None:
            raise OSError("settings export temporary file cleanup is not supported on this platform")
        claim_fd = os.open(
            temp_name,
            os.O_RDWR
            | nofollow_flag
            | nonblock_flag
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        primary_error: BaseException | None = None
        try:
            claimed_stat = os.fstat(claim_fd)
            if not _same_leaf_snapshot(claimed_stat, temporary_stat):
                raise OSError("settings export temporary file changed before cleanup")
            current_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat_module.S_ISREG(current_stat.st_mode)
                or getattr(current_stat, "st_nlink", 1) != 1
                or not _same_leaf_inode(current_stat, claimed_stat)
            ):
                raise OSError("settings export temporary file changed before cleanup")
            _scrub_settings_export_fd(claim_fd, expected_stat=claimed_stat)
            final_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat_module.S_ISREG(final_stat.st_mode)
                or getattr(final_stat, "st_nlink", 1) != 1
                or not _same_leaf_inode(final_stat, claimed_stat)
            ):
                raise OSError("settings export temporary file changed during cleanup")
            cleanup_name = f"{temp_name}.{secrets.token_hex(8)}.cleanup"
            _rename_without_replacing(
                temp_name,
                cleanup_name,
                directory_fd=parent_fd,
                expected_source_stat=final_stat,
                expected_source_fd=claim_fd,
                field_name="settings export temporary file cleanup",
            )
            cleanup_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat_module.S_ISREG(cleanup_stat.st_mode)
                or getattr(cleanup_stat, "st_nlink", 1) != 1
                or not _same_leaf_inode(cleanup_stat, final_stat)
            ):
                raise OSError("settings export temporary file changed after cleanup claim")
            if not _unlink_leaf_safely(
                cleanup_name,
                cleanup_stat,
                field_name="settings export temporary file cleanup",
            ):
                raise OSError("settings export temporary file disappeared during cleanup")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                os.close(claim_fd)
            except OSError as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise

    def _scrub_temp_if_same() -> None:
        if temporary_stat is None:
            raise OSError("settings export temporary file identity is unavailable")
        _scrub_temp_settings_export_file(parent_fd, temp_name, expected_stat=temporary_stat)

    def _assert_recovery_backup_identity() -> None:
        if not backup_moved:
            return
        try:
            current_backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise OSError("settings export recovery backup disappeared during rollback") from exc
        if (
            existing_stat is None
            or not stat_module.S_ISREG(current_backup_stat.st_mode)
            or getattr(current_backup_stat, "st_nlink", 1) != 1
            or not _same_leaf_identity(current_backup_stat, existing_stat)
        ):
            raise OSError("settings export recovery backup changed during rollback")

    def _remove_recovery_backup_safely() -> None:
        if not backup_moved or not backup_name or existing_stat is None:
            raise _RecoveryBackupChanged("settings export recovery backup identity is unavailable")
        nonblock_flag = _required_nonblock_flag("settings export recovery backup cleanup")
        current_backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat_module.S_ISREG(current_backup_stat.st_mode)
            or getattr(current_backup_stat, "st_nlink", 1) != 1
            or not _same_leaf_identity(current_backup_stat, existing_stat)
        ):
            raise _RecoveryBackupChanged("settings export recovery backup changed before cleanup")
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if nofollow_flag is None:
            raise _RecoveryBackupChanged("settings export recovery backup cleanup is not supported on this platform")
        claim_flags = (
            os.O_RDONLY
            | nofollow_flag
            | nonblock_flag
            | getattr(os, "O_CLOEXEC", 0)
        )
        scrub_flags = os.O_WRONLY | nofollow_flag | nonblock_flag | getattr(os, "O_CLOEXEC", 0)
        for _ in range(100):
            cleanup_name = f"{backup_name}.{secrets.token_hex(8)}.cleanup"
            claim_fd = -1
            try:
                claim_fd = os.open(backup_name, claim_flags, dir_fd=parent_fd)
                source_stat = os.fstat(claim_fd)
                if not _same_leaf_identity(source_stat, current_backup_stat):
                    raise _RecoveryBackupChanged("settings export recovery backup changed before cleanup")
                _rename_without_replacing(
                    backup_name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    expected_source_stat=source_stat,
                    expected_source_fd=claim_fd,
                    field_name="settings export recovery backup cleanup",
                )
            except FileExistsError as exc:
                if claim_fd >= 0:
                    if not _close_claim_fd(claim_fd, exc):
                        raise
                continue
            except BaseException as exc:
                if claim_fd >= 0:
                    _close_claim_fd(claim_fd, exc)
                raise

            primary_error: BaseException | None = None
            scrub_started = False
            try:
                claimed_stat = os.fstat(claim_fd)
                cleanup_path_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat_module.S_ISREG(claimed_stat.st_mode)
                    or getattr(claimed_stat, "st_nlink", 1) != 1
                    or not _same_leaf_identity(claimed_stat, existing_stat)
                    or not _same_leaf_inode(cleanup_path_stat, claimed_stat)
                ):
                    raise _RecoveryBackupChanged("settings export recovery backup changed before cleanup")

                os.fchmod(claim_fd, 0o600)
                claimed_stat = os.fstat(claim_fd)
                cleanup_path_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat_module.S_ISREG(claimed_stat.st_mode)
                    or getattr(claimed_stat, "st_nlink", 1) != 1
                    or claimed_stat.st_dev != source_stat.st_dev
                    or claimed_stat.st_ino != source_stat.st_ino
                    or not _same_leaf_inode(cleanup_path_stat, claimed_stat)
                ):
                    raise _RecoveryBackupChanged("settings export recovery backup changed after permission update")

                scrub_fd = os.open(cleanup_name, scrub_flags, dir_fd=parent_fd)
                scrub_primary_error: BaseException | None = None
                try:
                    scrub_stat = os.fstat(scrub_fd)
                    if not _same_leaf_inode(scrub_stat, claimed_stat):
                        raise _RecoveryBackupChanged("settings export recovery backup changed before scrubbing")
                    scrub_started = True
                    _scrub_settings_export_fd(scrub_fd, expected_stat=scrub_stat)
                except BaseException as exc:
                    scrub_primary_error = exc
                    raise
                finally:
                    try:
                        _close_claim_fd(scrub_fd, scrub_primary_error)
                    except BaseException:
                        raise

                final_path_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    not stat_module.S_ISREG(final_path_stat.st_mode)
                    or getattr(final_path_stat, "st_nlink", 1) != 1
                    or not _same_leaf_inode(final_path_stat, claimed_stat)
                ):
                    raise _RecoveryBackupChanged("settings export recovery backup changed during cleanup")
                os.unlink(cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                primary_error = exc
                if not scrub_started:
                    try:
                        restore_stat = os.fstat(claim_fd)
                        _rename_without_replacing(
                            Path(cleanup_name),
                            backup_name,
                            directory_fd=parent_fd,
                            expected_source_stat=restore_stat,
                            expected_source_fd=claim_fd,
                            field_name="settings export recovery backup restore",
                        )
                        _fsync_fd(parent_fd)
                    except BaseException as restore_error:
                        _note_cleanup_failure(exc, restore_error)
                raise
            finally:
                _close_claim_fd(claim_fd, primary_error)
            return
        raise _RecoveryBackupChanged("settings export recovery backup cleanup path could not be claimed")

    def _unlink_leaf_safely(
        leaf_name: str,
        expected_stat: os.stat_result,
        *,
        field_name: str,
    ) -> bool:
        nonblock_flag = _required_nonblock_flag(f"{field_name} cleanup")
        try:
            current_stat = os.stat(leaf_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if not _same_leaf_snapshot(current_stat, expected_stat):
            raise OSError(f"{field_name} changed before cleanup")
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if nofollow_flag is None:
            raise OSError(f"{field_name} cleanup is not supported on this platform")
        claim_flags = (
            getattr(os, "O_PATH", os.O_RDONLY)
            | nofollow_flag
            | nonblock_flag
            | getattr(os, "O_CLOEXEC", 0)
        )
        allow_symlink_candidate = (
            field_name == "settings export recovery backup candidate"
            and stat_module.S_ISLNK(expected_stat.st_mode)
        )
        for _ in range(100):
            cleanup_name = f"{leaf_name}.{secrets.token_hex(8)}.cleanup"
            claim_fd = -1
            try:
                claim_fd = os.open(leaf_name, claim_flags, dir_fd=parent_fd)
                source_stat = os.fstat(claim_fd)
                if not _same_leaf_snapshot(source_stat, current_stat):
                    raise OSError(f"{field_name} changed before cleanup")
                _rename_without_replacing(
                    leaf_name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    expected_source_stat=source_stat,
                    expected_source_fd=claim_fd,
                    field_name=f"{field_name} cleanup",
                )
            except FileExistsError as exc:
                if claim_fd >= 0:
                    if not _close_claim_fd(claim_fd, exc):
                        raise
                continue
            except BaseException as exc:
                if claim_fd >= 0:
                    _close_claim_fd(claim_fd, exc)
                raise

            primary_error: BaseException | None = None
            claimed_stat: os.stat_result | None = None
            unlinked = False
            try:
                claimed_stat = os.fstat(claim_fd)
                cleanup_path_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    (not allow_symlink_candidate and not stat_module.S_ISREG(claimed_stat.st_mode))
                    or (allow_symlink_candidate and not stat_module.S_ISLNK(claimed_stat.st_mode))
                    or not _same_leaf_identity(claimed_stat, expected_stat)
                    or not _same_leaf_inode(cleanup_path_stat, claimed_stat)
                ):
                    raise OSError(f"{field_name} changed before cleanup")
                os.unlink(cleanup_name, dir_fd=parent_fd)
                unlinked = True
                _fsync_fd(parent_fd)
            except BaseException as exc:
                primary_error = exc
                if not unlinked and claimed_stat is not None and _same_leaf_inode(claimed_stat, expected_stat):
                    try:
                        restore_stat = os.fstat(claim_fd)
                        _rename_without_replacing(
                            cleanup_name,
                            leaf_name,
                            directory_fd=parent_fd,
                            expected_source_stat=restore_stat,
                            expected_source_fd=claim_fd,
                            field_name=f"{field_name} restore",
                        )
                        _fsync_fd(parent_fd)
                    except BaseException as restore_error:
                        _note_cleanup_failure(exc, restore_error)
                raise
            finally:
                _close_claim_fd(claim_fd, primary_error)
            return True
        raise OSError(f"{field_name} cleanup path could not be claimed")

    try:
        try:
            assert_fd_is_private_directory(parent_fd, field_name="settings export directory")
        except RuntimeError as exc:
            raise SettingsExportError(str(exc)) from exc
        try:
            existing_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing_stat = None
        if existing_stat is not None and stat_module.S_ISLNK(existing_stat.st_mode):
            raise SettingsExportError(f"settings export path must not be a symlink: {path}")
        if existing_stat is not None and not stat_module.S_ISREG(existing_stat.st_mode):
            raise SettingsExportError(f"settings export path must be a regular file: {path}")
        if existing_stat is not None and getattr(existing_stat, "st_nlink", 1) != 1:
            raise SettingsExportError(f"settings export path must not be hardlinked: {path}")
        temp_fd, temp_name = _create_private_temp_file(parent_fd, path.name)
        try:
            temporary_stat = os.fstat(temp_fd)
        except (OSError, ValueError) as exc:
            raise OSError("failed to inspect settings export temporary file") from exc
        try:
            handle = os.fdopen(temp_fd, "w", encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise OSError("failed to open settings export temporary file") from exc
        temp_fd = None
        handle_primary_error: BaseException | None = None
        try:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
            try:
                temporary_stat = os.fstat(handle.fileno())
            except (OSError, ValueError) as exc:
                raise OSError("failed to inspect settings export temporary file") from exc
            handle.write(rendered)
            handle.flush()
            temporary_stat = os.fstat(handle.fileno())
            _fsync_fd(handle.fileno())
        except BaseException as exc:
            handle_primary_error = exc
            raise
        finally:
            try:
                handle.close()
            except BaseException as cleanup_error:
                if handle_primary_error is not None:
                    _note_cleanup_failure(handle_primary_error, cleanup_error)
                else:
                    raise
        if temporary_stat is None:
            raise OSError("settings export temporary file identity is unavailable")
        try:
            current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            current_target_stat = None
        if existing_stat is None:
            if current_target_stat is not None:
                raise OSError("settings export path changed before activation")
        elif current_target_stat is None or not _same_leaf_snapshot(current_target_stat, existing_stat):
            raise OSError("settings export path changed before activation")
        staged_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat_module.S_ISREG(staged_stat.st_mode) or getattr(staged_stat, "st_nlink", 1) != 1:
            raise OSError("settings export temporary file is not safe")
        if not _same_leaf_snapshot(staged_stat, temporary_stat):
            raise OSError("settings export temporary file changed before activation")

        transaction_active = True
        if existing_stat is not None:
            for _ in range(100):
                candidate_name = f".{path.name}.{secrets.token_hex(8)}.bak"
                try:
                    os.link(
                        path.name,
                        candidate_name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    raise OSError("settings export path disappeared before backup activation") from None
                except FileExistsError:
                    continue
                candidate_stat_at_creation: os.stat_result | None = None
                try:
                    backup_name = candidate_name
                    candidate_stat_at_creation = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                    backup_stat = candidate_stat_at_creation
                    current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat_module.S_ISREG(backup_stat.st_mode)
                        or getattr(backup_stat, "st_nlink", 1) < 2
                        or not _same_leaf_inode(backup_stat, existing_stat)
                        or not stat_module.S_ISREG(current_target_stat.st_mode)
                        or not _same_leaf_inode(current_target_stat, existing_stat)
                    ):
                        raise OSError("settings export path changed during backup activation")
                    backup_moved = True
                    _fsync_fd(parent_fd)
                    break
                except BaseException as exc:
                    if not backup_moved:
                        try:
                            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            backup_moved = True
                        except BaseException as cleanup_error:
                            _note_cleanup_failure(exc, cleanup_error)
                        else:
                            try:
                                candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                                if (
                                    candidate_stat_at_creation is not None
                                    and _same_leaf_inode(candidate_stat, candidate_stat_at_creation)
                                ):
                                    if _unlink_leaf_safely(
                                        candidate_name,
                                        candidate_stat,
                                        field_name="settings export recovery backup candidate",
                                    ):
                                        _fsync_fd(parent_fd)
                            except FileNotFoundError:
                                pass
                            except BaseException as cleanup_error:
                                _note_cleanup_failure(exc, cleanup_error)
                    raise
            if not backup_name:
                raise OSError("failed to create settings export recovery backup")

        activation_attempted = True
        if existing_stat is not None:
            _rename_exchange(
                temp_name,
                path.name,
                directory_fd=parent_fd,
                field_name="settings export path",
            )
            activation_exchange = True
            try:
                activated_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                replaced_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as stat_error:
                raise OSError("settings export could not be inspected after activation") from stat_error
            if (
                temporary_stat is None
                or not _same_leaf_identity(activated_stat, temporary_stat)
                or not stat_module.S_ISREG(replaced_stat.st_mode)
                or getattr(replaced_stat, "st_nlink", 1) < 2
                or not _same_leaf_inode(replaced_stat, existing_stat)
            ):
                if not _same_leaf_inode(replaced_stat, existing_stat):
                    _rename_exchange(
                        temp_name,
                        path.name,
                        directory_fd=parent_fd,
                        field_name="settings export path rollback",
                    )
                    if backup_moved:
                        _remove_recovery_backup_safely()
                        backup_moved = False
                    activation_exchange = False
                    activation_attempted = False
                raise OSError("settings export path changed during activation")
            if not _unlink_leaf_safely(
                temp_name,
                replaced_stat,
                field_name="settings export replaced target",
            ):
                raise OSError("settings export replaced target disappeared during activation")
            temp_name = ""
        else:
            _rename_without_replacing(
                temp_name,
                path.name,
                directory_fd=parent_fd,
                field_name="settings export path",
            )
            temp_name = ""
        try:
            activated_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as stat_error:
            raise OSError("settings export could not be inspected after activation") from stat_error
        if temporary_stat is None or not _same_leaf_identity(activated_stat, temporary_stat):
            raise OSError("settings export changed after activation")
        activation_stat = activated_stat
        _fsync_fd(parent_fd)
        committed = True
        transaction_active = False
        if backup_moved:
            try:
                _remove_recovery_backup_safely()
            except _RecoveryBackupChanged:
                _report_post_commit_warning(
                    POST_COMMIT_RECOVERY_BACKUP_CLEANUP_WARNING,
                )
            except Exception:
                _report_post_commit_warning(
                    POST_COMMIT_RECOVERY_BACKUP_CLEANUP_WARNING,
                )
    except BaseException as exc:
        primary_error = exc
        if transaction_active:
            try:
                _assert_recovery_backup_identity()
                if activation_attempted:
                    try:
                        current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current_target_stat = None
                    expected_activation_stat = activation_stat or temporary_stat
                    if current_target_stat is not None:
                        if expected_activation_stat is None or not _same_leaf_identity(current_target_stat, expected_activation_stat):
                            raise OSError("settings export target changed during rollback")
                        if not _unlink_leaf_safely(
                            path.name,
                            current_target_stat,
                            field_name="settings export target",
                        ):
                            raise OSError("settings export target disappeared during rollback")
                        _fsync_fd(parent_fd)
                if backup_moved:
                    try:
                        os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        _rename_without_replacing(
                            backup_name,
                            path.name,
                            directory_fd=parent_fd,
                            field_name="settings export path",
                        )
                        _fsync_fd(parent_fd)
                    else:
                        raise OSError("settings export target exists during rollback")
                if activation_exchange and temp_name and existing_stat is not None:
                    try:
                        replaced_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        temp_name = ""
                    else:
                        if _same_leaf_identity(replaced_stat, existing_stat) and _unlink_leaf_safely(
                            temp_name,
                            replaced_stat,
                            field_name="settings export replaced target rollback",
                        ):
                            temp_name = ""
            except BaseException as rollback_error:
                _note_cleanup_failure(primary_error, rollback_error)
        if isinstance(exc, (MemoryError, OSError, RecursionError)):
            cleanup_failure: BaseException | None = None
            if temp_name:
                try:
                    _unlink_temp_if_same()
                except OSError as cleanup_exc:
                    try:
                        _scrub_temp_if_same()
                    except BaseException as scrub_error:
                        _note_cleanup_failure(exc, scrub_error)
                    cleanup_failure = cleanup_exc
                except BaseException as cleanup_exc:
                    try:
                        _scrub_temp_if_same()
                    except BaseException as scrub_error:
                        _note_cleanup_failure(exc, scrub_error)
                    cleanup_failure = cleanup_exc
            if cleanup_failure is not None:
                error = SettingsExportError(f"failed to write settings export: {path}")
                _note_cleanup_failure(error, cleanup_failure)
                primary_error = error
                public_error = error
                public_error_cause = _sanitized_public_error_cause(exc)
            else:
                public_error = SettingsExportError(f"failed to write settings export: {path}")
                primary_error = public_error
                public_error_cause = _sanitized_public_error_cause(exc)
        if public_error is None:
            if temp_name:
                try:
                    _unlink_temp_if_same()
                except OSError as cleanup_error:
                    try:
                        _scrub_temp_if_same()
                    except BaseException as scrub_error:
                        _note_cleanup_failure(primary_error, scrub_error)
                    _note_cleanup_failure(primary_error, cleanup_error)
                except BaseException as cleanup_error:
                    try:
                        _scrub_temp_if_same()
                    except BaseException as scrub_error:
                        _note_cleanup_failure(primary_error, scrub_error)
                    _note_cleanup_failure(primary_error, cleanup_error)
            raise
    finally:
        if temp_fd is not None:
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            elif committed:
                _report_post_commit_warning(
                    POST_COMMIT_DIRECTORY_CLOSE_WARNING,
                )
            else:
                error = SettingsExportError("failed to close settings export directory")
                raise error from cleanup_error
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
    if public_error is not None:
        if public_error_cause is not None:
            raise public_error from public_error_cause
        raise public_error
    if post_commit_warnings:
        return {**payload, "post_commit_warnings": list(post_commit_warnings)}
    return payload


def read_export(path: Path) -> dict[str, Any]:
    _assert_clean_path(path, field_name="settings export path")
    if not path.is_absolute():
        raise SettingsExportError("settings export path must be absolute")
    try:
        text = _read_text_capped_without_following_symlinks(path)
        if _utf8_byte_count(text, field_name="settings export content") > MAX_SETTINGS_EXPORT_BYTES:
            raise SettingsExportError(f"settings export is too large: {path}")
        if _contains_escaped_null(text):
            raise SettingsExportError("settings export contains invalid null byte")
        _assert_json_text_budget(text)
        payload = json.loads(text, parse_constant=_reject_non_finite_json_number)
        _assert_json_value_budget(payload)
    except FileNotFoundError as exc:
        raise SettingsExportError(f"settings export not found: {path}") from exc
    except (OSError, ValueError, RecursionError, UnicodeDecodeError, MemoryError) as exc:
        raise SettingsExportError(f"settings export could not be read: {path}") from exc

    if not isinstance(payload, dict):
        raise SettingsExportError("settings export must be a JSON object")
    if payload.get("app") != APP_ID:
        raise SettingsExportError("settings export is for a different app")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or version > EXPORT_VERSION:
        raise SettingsExportError("unsupported settings export version")
    if version == EXPORT_VERSION and "alarms" not in payload:
        raise SettingsExportError("settings export alarms must be an object")
    raw_settings = payload.get("settings")
    if not isinstance(raw_settings, dict):
        raise SettingsExportError("settings export does not contain a settings object")
    raw_alarms = payload.get("alarms") if version == EXPORT_VERSION else payload.get("alarms", {})
    included_private_settings = normalize_included_private_settings(
        payload.get("included_private_settings", [])
    )
    if "excluded_private_settings" in payload:
        excluded_private_settings = normalize_excluded_private_settings(
            payload.get("excluded_private_settings")
        )
    else:
        excluded_private_settings = [
            key
            for key in NON_EXPORTABLE_PRIVATE_SETTINGS
            if key not in included_private_settings
        ]
    if set(excluded_private_settings).intersection(included_private_settings):
        raise SettingsExportError("settings export private setting metadata conflicts")
    importable_settings = normalize_settings(raw_settings)
    allowed_private_settings = set(included_private_settings)
    for key in NON_EXPORTABLE_PRIVATE_SETTINGS:
        if key in importable_settings and key not in allowed_private_settings:
            importable_settings.pop(key)
    return {
        "app": APP_ID,
        "version": version,
        "created_at": _sanitize_text_field(payload.get("created_at", ""), field_name="settings export created_at"),
        "speed_of_cinnamon_version": _sanitize_text_field(
            payload.get("speed_of_cinnamon_version", ""),
            field_name="settings export speed_of_cinnamon_version",
        ),
        "excluded_private_settings": excluded_private_settings,
        "included_private_settings": included_private_settings,
        "settings": importable_settings,
        "alarms": normalize_alarm_store(raw_alarms),
    }
