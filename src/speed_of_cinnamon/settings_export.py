from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .alarms import MAX_ALARM_COUNT, STORE_VERSION as ALARM_STORE_VERSION
from .alarms import normalize_alarm
from .paths import APP_ID
from .recorder import MAX_RECORDING_SECONDS

EXPORT_VERSION = 2
MAX_SETTINGS_EXPORT_BYTES = 1_000_000
MAX_SETTINGS_TEXT_CHARS = 4_096
MAX_SETTINGS_EXPORT_PATH_CHARS = 4_096
MAX_TYPING_DELAY_MS = 10_000
DEFAULT_MAX_SECONDS = 30
DEFAULT_TYPING_DELAY_MS = 8
MIN_RECORDING_SECONDS = 0
MIN_TYPING_DELAY_MS = 0

EXPORTABLE_SETTINGS: dict[str, tuple[type, Any]] = {
    "toggle-keybinding": (str, "<Super>z::"),
    "primary-language-keybinding": (str, ""),
    "secondary-language-keybinding": (str, ""),
    "show-panel-label": (bool, True),
    "language": (str, "en"),
    "secondary-language": (str, "de"),
    "max-seconds": (int, DEFAULT_MAX_SECONDS),
    "auto-transcribe-timeout": (bool, True),
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
    "typing-delay-ms": (int, DEFAULT_TYPING_DELAY_MS),
    "transcriber": (str, "auto"),
    "whisper-model": (str, ""),
    "post-process-backend": (str, "none"),
    "transcriber-command": (str, ""),
    "post-process-command": (str, ""),
    "ollama-url": (str, "http://127.0.0.1:11434"),
    "ollama-model": (str, ""),
    "openai-compatible-url": (str, "https://api.openai.com/v1"),
    "openai-compatible-model": (str, "gpt-4o-transcribe"),
    "openai-compatible-text-model": (str, ""),
    "post-process-prompt": (str, ""),
}


class SettingsExportError(RuntimeError):
    pass


def _assert_clean_path(path: Path, *, field_name: str) -> None:
    if not isinstance(path, Path):
        raise SettingsExportError(f"{field_name} must be a path")
    text = str(path)
    if not text or len(text) > MAX_SETTINGS_EXPORT_PATH_CHARS:
        raise SettingsExportError(f"{field_name} path is invalid")
    if len(text.encode("utf-8")) > MAX_SETTINGS_EXPORT_PATH_CHARS:
        raise SettingsExportError(f"{field_name} path is invalid")
    if _contains_escaped_null(text):
        raise SettingsExportError(f"{field_name} contains invalid null byte")


def _contains_escaped_null(text: str) -> bool:
    if isinstance(text, bool) or not isinstance(text, str):
        raise SettingsExportError("value must be text")
    lowered = (text or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _sanitize_text_field(value: object, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise SettingsExportError(f"{field_name} must be text")
    text = str(value or "")
    if _contains_escaped_null(text):
        raise SettingsExportError(f"{field_name} contains invalid null byte")
    text = text.strip()
    if len(text) > MAX_SETTINGS_TEXT_CHARS:
        raise SettingsExportError(f"{field_name} is too long")
    if len(text.encode("utf-8")) > MAX_SETTINGS_TEXT_CHARS:
        raise SettingsExportError(f"{field_name} is too long (max {MAX_SETTINGS_TEXT_CHARS} bytes)")
    return text


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
    return _sanitize_text_field(value if value is not None else default, field_name=f"setting {key}")


def normalize_settings(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: normalize_setting(key, values.get(key, default))
        for key, (_, default) in EXPORTABLE_SETTINGS.items()
    }


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


def build_export(settings: dict[str, Any], alarm_store: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_alarm_store = normalize_alarm_store(alarm_store if alarm_store is not None else {})
    return {
        "app": APP_ID,
        "version": EXPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "speed_of_cinnamon_version": __version__,
        "settings": normalize_settings(settings),
        "alarms": normalized_alarm_store,
    }


def write_export(path: Path, settings: dict[str, Any], alarm_store: dict[str, Any] | None = None) -> dict[str, Any]:
    _assert_clean_path(path, field_name="settings export path")
    payload = build_export(settings, alarm_store)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if len(rendered.encode("utf-8")) > MAX_SETTINGS_EXPORT_BYTES:
        raise SettingsExportError(f"settings export is too large: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        try:
            os.fchmod(handle.fileno(), 0o600)
        except OSError:
            pass
        handle.write(rendered)
        tmp_path = Path(handle.name)
    try:
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise SettingsExportError(f"failed to write settings export: {path}") from exc
    return payload


def read_export(path: Path) -> dict[str, Any]:
    _assert_clean_path(path, field_name="settings export path")
    try:
        if path.stat().st_size > MAX_SETTINGS_EXPORT_BYTES:
            raise SettingsExportError(f"settings export is too large: {path}")
        text = path.read_text(encoding="utf-8")
        if _contains_escaped_null(text):
            raise SettingsExportError("settings export contains invalid null byte")
        payload = json.loads(text)
    except FileNotFoundError as exc:
        raise SettingsExportError(f"settings export not found: {path}") from exc
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
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
        "created_at": payload.get("created_at", ""),
        "speed_of_cinnamon_version": payload.get("speed_of_cinnamon_version", ""),
        "settings": normalize_settings(raw_settings),
        "alarms": normalize_alarm_store(raw_alarms),
    }
