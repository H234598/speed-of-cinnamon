from __future__ import annotations

import json
import os
import secrets
import stat as stat_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .alarms import MAX_ALARM_COUNT, STORE_VERSION as ALARM_STORE_VERSION
from .alarms import normalize_alarm
from .http_safety import is_loopback_hostname
from .paths import APP_ID
from .recorder import MAX_RECORDING_SECONDS
from .path_safety import (
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
)

EXPORT_VERSION = 2
MAX_SETTINGS_EXPORT_BYTES = 1_000_000
MAX_SETTINGS_TEXT_CHARS = 4_096
MAX_SETTINGS_EXPORT_PATH_CHARS = 4_096
MAX_SETTINGS_EXPORT_JSON_DEPTH = 24
MAX_SETTINGS_EXPORT_JSON_TOKENS = 20_000
MAX_SETTINGS_EXPORT_JSON_NODES = 10_000
MAX_TYPING_DELAY_MS = 10_000
DEFAULT_MAX_SECONDS = 30
DEFAULT_TYPING_DELAY_MS = 8
DEFAULT_MAX_TRANSCRIPT_FILES = 500
MIN_RECORDING_SECONDS = 0
MIN_TYPING_DELAY_MS = 0

NON_EXPORTABLE_PRIVATE_SETTINGS: tuple[str, ...] = (
    "cli-path",
    "openai-compatible-api-key",
    "post-process-command",
    "transcriber-command",
)

EXPORTABLE_SETTINGS: dict[str, tuple[type, Any]] = {
    "toggle-keybinding": (str, "<Super>z::"),
    "primary-language-keybinding": (str, ""),
    "secondary-language-keybinding": (str, ""),
    "cancel-keybinding": (str, ""),
    "show-panel-label": (bool, False),
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


def _contains_http_header_control_chars(text: str) -> bool:
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


def _create_private_temp_file(parent_fd: int, final_name: str) -> tuple[int, str]:
    safe_name = final_name.replace("/", "_") or "settings-export.json"
    nofollow_flag = getattr(os, "O_NOFOLLOW", 0)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
    for _ in range(100):
        temp_name = f".{safe_name}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(temp_name, flags, 0o600, dir_fd=parent_fd), temp_name
        except FileExistsError:
            continue
    raise SettingsExportError("failed to create settings export temp file")


def _read_text_capped_without_following_symlinks(path: Path) -> str:
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name="settings export path")
    try:
        try:
            assert_fd_is_regular_private_file(fd, field_name="settings export")
        except RuntimeError as exc:
            raise SettingsExportError(str(exc)) from exc
        file_stat = os.fstat(fd)
        if file_stat.st_size > MAX_SETTINGS_EXPORT_BYTES:
            raise SettingsExportError(f"settings export is too large: {path}")
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            fd = -1
            return handle.read(MAX_SETTINGS_EXPORT_BYTES + 1)
    finally:
        if fd >= 0:
            os.close(fd)


def _sanitize_text_field(value: object, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise SettingsExportError(f"{field_name} must be text")
    text = str(value or "")
    if _contains_escaped_null(text):
        raise SettingsExportError(f"{field_name} contains invalid null byte")
    text = text.strip()
    if _contains_http_header_control_chars(text):
        raise SettingsExportError(f"{field_name} contains invalid control character")
    if len(text) > MAX_SETTINGS_TEXT_CHARS:
        raise SettingsExportError(f"{field_name} is too long")
    if _utf8_byte_count(text, field_name=field_name) > MAX_SETTINGS_TEXT_CHARS:
        raise SettingsExportError(f"{field_name} is too long (max {MAX_SETTINGS_TEXT_CHARS} bytes)")
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
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise SettingsExportError(f"setting {key} must use https:// unless host is local loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise SettingsExportError(f"setting {key} has invalid port") from exc
    if parsed.username or parsed.password:
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
                raise SettingsExportError(f"setting {key} must be an integer")
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
        return parsed
    text = _sanitize_text_field(value, field_name=f"setting {key}")
    _reject_secret_bearing_url_setting(key, text)
    allowed_values = _ALLOWED_SETTING_TEXT_VALUES.get(key)
    if allowed_values is not None and text not in allowed_values:
        raise SettingsExportError(f"setting {key} has unsupported value")
    return text


def normalize_settings(values: dict[str, Any]) -> dict[str, Any]:
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
    last_checked_at = _sanitize_text_field(value.get("last_checked_at", ""), field_name="settings export alarm last_checked_at")
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
        "alarms": normalized_alarms,
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


def build_export(settings: dict[str, Any], alarm_store: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_alarm_store = normalize_alarm_store(alarm_store if alarm_store is not None else {})
    return {
        "app": APP_ID,
        "version": EXPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "excluded_private_settings": list(NON_EXPORTABLE_PRIVATE_SETTINGS),
        "speed_of_cinnamon_version": __version__,
        "settings": normalize_settings(settings),
        "alarms": normalized_alarm_store,
    }


def write_export(path: Path, settings: dict[str, Any], alarm_store: dict[str, Any] | None = None) -> dict[str, Any]:
    _assert_clean_path(path, field_name="settings export path")
    if not path.is_absolute():
        raise SettingsExportError("settings export path must be absolute")
    payload = build_export(settings, alarm_store)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if _utf8_byte_count(rendered, field_name="settings export payload") > MAX_SETTINGS_EXPORT_BYTES:
        raise SettingsExportError(f"settings export is too large: {path}")
    parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name="settings export directory")
    temp_name = ""
    try:
        try:
            existing_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing_stat = None
        if existing_stat is not None and stat_module.S_ISLNK(existing_stat.st_mode):
            raise SettingsExportError(f"settings export path must not be a symlink: {path}")
        temp_fd, temp_name = _create_private_temp_file(parent_fd, path.name)
        with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temp_name = ""
        os.fsync(parent_fd)
    except OSError as exc:
        cleanup_error: OSError | None = None
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError as cleanup_exc:
                cleanup_error = cleanup_exc
        if cleanup_error is not None:
            raise SettingsExportError(f"failed to remove settings export temporary file: {path}") from cleanup_error
        raise SettingsExportError(f"failed to write settings export: {path}") from exc
    finally:
        os.close(parent_fd)
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
        payload = json.loads(text)
        _assert_json_value_budget(payload)
    except FileNotFoundError as exc:
        raise SettingsExportError(f"settings export not found: {path}") from exc
    except (OSError, json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
        raise SettingsExportError(f"settings export could not be read: {path}") from exc

    if not isinstance(payload, dict):
        raise SettingsExportError("settings export must be a JSON object")
    if payload.get("app") != APP_ID:
        raise SettingsExportError(f"settings export is for a different app: {payload.get('app')}")
    version = payload.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1 or version > EXPORT_VERSION:
        raise SettingsExportError(f"unsupported settings export version: {version}")
    if version == EXPORT_VERSION and "alarms" not in payload:
        raise SettingsExportError("settings export alarms must be an object")
    raw_settings = payload.get("settings")
    if not isinstance(raw_settings, dict):
        raise SettingsExportError("settings export does not contain a settings object")
    raw_alarms = payload.get("alarms") if version == EXPORT_VERSION else payload.get("alarms", {})
    return {
        "app": APP_ID,
        "version": version,
        "created_at": _sanitize_text_field(payload.get("created_at", ""), field_name="settings export created_at"),
        "speed_of_cinnamon_version": _sanitize_text_field(
            payload.get("speed_of_cinnamon_version", ""),
            field_name="settings export speed_of_cinnamon_version",
        ),
        "excluded_private_settings": normalize_excluded_private_settings(
            payload.get("excluded_private_settings", list(NON_EXPORTABLE_PRIVATE_SETTINGS))
        ),
        "settings": normalize_settings(raw_settings),
        "alarms": normalize_alarm_store(raw_alarms),
    }
