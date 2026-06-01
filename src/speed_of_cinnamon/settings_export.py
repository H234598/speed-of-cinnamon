from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import __version__
from .paths import APP_ID

EXPORT_VERSION = 1

EXPORTABLE_SETTINGS: dict[str, tuple[type, Any]] = {
    "toggle-keybinding": (str, "<Super>z::"),
    "show-panel-label": (bool, True),
    "language": (str, "en"),
    "secondary-language": (str, "de"),
    "max-seconds": (int, 30),
    "recorder": (str, "auto"),
    "input-device": (str, ""),
    "personal-context": (str, ""),
    "vocabulary": (str, ""),
    "notify-recording": (bool, False),
    "notify-complete": (bool, True),
    "notify-error": (bool, True),
    "insert-method": (str, "clipboard-paste"),
    "append-space": (bool, True),
    "typing-delay-ms": (int, 8),
    "transcriber": (str, "auto"),
    "whisper-model": (str, ""),
    "transcriber-command": (str, ""),
    "post-process-command": (str, ""),
}


class SettingsExportError(RuntimeError):
    pass


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


def build_export(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "app": APP_ID,
        "version": EXPORT_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "speed_of_cinnamon_version": __version__,
        "settings": normalize_settings(settings),
    }


def write_export(path: Path, settings: dict[str, Any]) -> dict[str, Any]:
    payload = build_export(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)
    return payload


def read_export(path: Path) -> dict[str, Any]:
    try:
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
    }
