from __future__ import annotations

import os
from pathlib import Path

APP_ID = "speed-of-cinnamon"
APP_NAME = "Speed of Cinnamon"
APPLET_UUID = "speed-of-cinnamon@H234598"
MAX_XDG_PATH_CHARS = 4_096


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _xdg_path(environment_variable: str, default: Path) -> Path:
    if isinstance(environment_variable, bool) or not isinstance(environment_variable, str):
        raise RuntimeError("environment variable name must be text")
    value = os.environ.get(environment_variable)
    if value is None:
        return default
    if not isinstance(value, str):
        return default
    normalized = (value or "").strip()
    if not normalized:
        return default
    if len(normalized) > MAX_XDG_PATH_CHARS or len(normalized.encode("utf-8")) > MAX_XDG_PATH_CHARS:
        return default
    if _contains_escaped_null(normalized):
        return default
    candidate = Path(normalized)
    return candidate if candidate.is_absolute() else default


def xdg_data_home() -> Path:
    return _xdg_path("XDG_DATA_HOME", Path.home() / ".local" / "share")


def xdg_state_home() -> Path:
    return _xdg_path("XDG_STATE_HOME", Path.home() / ".local" / "state")


def xdg_cache_home() -> Path:
    return _xdg_path("XDG_CACHE_HOME", Path.home() / ".cache")


def state_dir() -> Path:
    return xdg_state_home() / APP_ID


def data_dir() -> Path:
    return xdg_data_home() / APP_ID


def cache_dir() -> Path:
    return xdg_cache_home() / APP_ID


def recordings_dir() -> Path:
    return cache_dir() / "recordings"


def transcript_dir() -> Path:
    return state_dir() / "transcripts"


def diagnostics_dir() -> Path:
    return state_dir() / "diagnostics"


def models_dir() -> Path:
    return data_dir() / "models" / "whisper.cpp"


def ctranslate2_models_dir() -> Path:
    return data_dir() / "models" / "ctranslate2"


def default_state_file() -> Path:
    return state_dir() / "state.json"


def default_settings_export_file() -> Path:
    return data_dir() / "settings-export.json"


def alarms_file() -> Path:
    return data_dir() / "alarms.json"


def ensure_runtime_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
    recordings_dir().mkdir(parents=True, exist_ok=True)
    transcript_dir().mkdir(parents=True, exist_ok=True)
    diagnostics_dir().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)
    ctranslate2_models_dir().mkdir(parents=True, exist_ok=True)
