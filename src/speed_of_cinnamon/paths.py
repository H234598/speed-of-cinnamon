from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .path_safety import assert_no_symlink_ancestors

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
    try:
        value = os.environ[environment_variable]
    except KeyError:
        return default
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return default
    normalized = (value or "").strip()
    if not normalized:
        return default
    if len(normalized) > MAX_XDG_PATH_CHARS or len(normalized.encode("utf-8")) > MAX_XDG_PATH_CHARS:
        return default
    if _contains_escaped_null(normalized):
        return default
    candidate = Path(normalized).expanduser()
    if not candidate.is_absolute():
        return default
    try:
        assert_no_symlink_ancestors(candidate, field_name=environment_variable)
    except RuntimeError:
        return default
    return candidate.resolve(strict=False)


def _private_runtime_temp_root() -> Path:
    temp_root = Path(tempfile.gettempdir())
    if not temp_root.is_absolute():
        temp_root = Path("/tmp")  # nosec B108
    try:
        assert_no_symlink_ancestors(temp_root, field_name="temporary directory")
    except RuntimeError:
        temp_root = Path("/tmp")  # nosec B108
        assert_no_symlink_ancestors(temp_root, field_name="temporary directory")
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    private_root = temp_root / f"{APP_ID}-{uid}"
    if private_root.is_symlink():
        raise RuntimeError(f"temporary directory must not be a symlink: {private_root}")
    private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_root.chmod(0o700)
    assert_no_symlink_ancestors(private_root, field_name="temporary directory")
    if hasattr(os, "getuid") and private_root.stat().st_uid != os.getuid():
        raise RuntimeError(f"temporary directory is not owned by the current user: {private_root}")
    if private_root.stat().st_mode & 0o077:
        raise RuntimeError(f"temporary directory is not private: {private_root}")
    return private_root


def _safe_home_path(*parts: str) -> Path:
    candidate = Path.home().joinpath(*parts)
    try:
        assert_no_symlink_ancestors(candidate, field_name="home path")
    except RuntimeError:
        return _private_runtime_temp_root().joinpath(*parts)
    return candidate


def xdg_data_home() -> Path:
    return _xdg_path("XDG_DATA_HOME", _safe_home_path(".local", "share"))


def xdg_state_home() -> Path:
    return _xdg_path("XDG_STATE_HOME", _safe_home_path(".local", "state"))


def xdg_cache_home() -> Path:
    return _xdg_path("XDG_CACHE_HOME", _safe_home_path(".cache"))


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


def logs_dir() -> Path:
    return state_dir() / "logs"


def models_dir() -> Path:
    return data_dir() / "models" / "whisper.cpp"


def ctranslate2_models_dir() -> Path:
    return data_dir() / "models" / "ctranslate2"


def default_state_file() -> Path:
    return state_dir() / "state.json"


def default_settings_export_file() -> Path:
    return data_dir() / "settings-export.json"


def blacklist_file() -> Path:
    return data_dir() / "blacklist.txt"


def alarms_file() -> Path:
    return data_dir() / "alarms.json"


def ensure_runtime_dirs() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)
    recordings_dir().mkdir(parents=True, exist_ok=True)
    transcript_dir().mkdir(parents=True, exist_ok=True)
    diagnostics_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)
    ctranslate2_models_dir().mkdir(parents=True, exist_ok=True)
