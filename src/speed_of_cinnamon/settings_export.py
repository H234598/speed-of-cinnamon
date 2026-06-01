from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .alarms import STORE_VERSION as ALARM_STORE_VERSION
from .alarms import normalize_alarm
from .paths import APP_ID

EXPORT_VERSION = 2
MAX_SETTINGS_EXPORT_BYTES = 1_000_000

EXPORTABLE_SETTINGS: dict[str, tuple[type, Any]] = {
    "toggle-keybinding": (str, "<Super>z::"),
    "primary-language-keybinding": (str, ""),
    "secondary-language-keybinding": (str, ""),
    "show-panel-label": (bool, True),
    "language": (str, "en"),
    "secondary-language": (str, "de"),
    "max-seconds": (int, 30),
    "auto-transcribe-timeout": (bool, True),
    "keep-recording-artifacts": (bool, False),
    "recorder": (str, "auto"),
    "input-device": (str, ""),
    "personal-context": (str, ""),
    "vocabulary": (str, ""),
    "notify-recording": (bool, False),
    "notify-complete": (bool, True),
    "notify-error": (bool, True),
    "insert-method": (str, "clipboard-paste"),
    "append-space": (bool, True),
    "sanitize-special-chars": (bool, False),
    "typing-delay-ms": (int, 8),
    "transcriber": (str, "auto"),
    "whisper-model": (str, ""),
    "post-process-backend": (str, "command"),
    "transcriber-command": (str, ""),
    "post-process-command": (str, ""),
    "ollama-url": (str, "http://127.0.0.1:11434"),
    "ollama-model": (str, ""),
    "openai-compatible-url": (str, "http://127.0.0.1:8000/v1"),
    "openai-compatible-model": (str, ""),
    "post-process-prompt": (str, ""),
}


class SettingsExportError(RuntimeError):
    pass


def _assert_clean_path(path: Path, *, field_name: str) -> None:
    if "\x00" in str(path):
        raise SettingsExportError(f"{field_name} contains invalid null byte")


def normalize_setting(key: str, value: Any) -> Any:
    expected, default = EXPORTABLE_SETTINGS[key]
    if expected is bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)
    if expected is int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    return str(value if value is not None else default)


def normalize_settings(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: normalize_setting(key, values.get(key, default))
        for key, (_, default) in EXPORTABLE_SETTINGS.items()
    }


def normalize_alarm_store(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"version": ALARM_STORE_VERSION, "alarms": [], "last_checked_at": ""}
    alarms = value.get("alarms", [])
    if not isinstance(alarms, list):
        alarms = []
    return {
        "version": ALARM_STORE_VERSION,
        "alarms": [normalize_alarm(alarm) for alarm in alarms if isinstance(alarm, dict)],
        "last_checked_at": str(value.get("last_checked_at") or ""),
    }


def build_export(settings: dict[str, Any], alarm_store: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "app": APP_ID,
        "version": EXPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "speed_of_cinnamon_version": __version__,
        "settings": normalize_settings(settings),
        "alarms": normalize_alarm_store(alarm_store or {}),
    }


def write_export(path: Path, settings: dict[str, Any], alarm_store: dict[str, Any] | None = None) -> dict[str, Any]:
    _assert_clean_path(path, field_name="settings export path")
    payload = build_export(settings, alarm_store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        tmp_path = Path(handle.name)
    try:
        os.replace(tmp_path, path)
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
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SettingsExportError(f"settings export not found: {path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SettingsExportError(f"settings export could not be read: {path}") from exc

    if not isinstance(payload, dict):
        raise SettingsExportError("settings export must be a JSON object")
    if payload.get("app") != APP_ID:
        raise SettingsExportError(f"settings export is for a different app: {payload.get('app')}")
    version = payload.get("version")
    if not isinstance(version, int) or version < 1 or version > EXPORT_VERSION:
        raise SettingsExportError(f"unsupported settings export version: {version}")
    raw_settings = payload.get("settings")
    if not isinstance(raw_settings, dict):
        raise SettingsExportError("settings export does not contain a settings object")
    return {
        "app": APP_ID,
        "version": version,
        "created_at": payload.get("created_at", ""),
        "speed_of_cinnamon_version": payload.get("speed_of_cinnamon_version", ""),
        "settings": normalize_settings(raw_settings),
        "alarms": normalize_alarm_store(payload.get("alarms", {})),
    }
