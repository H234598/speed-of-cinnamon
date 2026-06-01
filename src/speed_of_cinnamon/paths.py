from __future__ import annotations

import os
from pathlib import Path

APP_ID = "speed-of-cinnamon"
APP_NAME = "Speed of Cinnamon"
APPLET_UUID = "speed-of-cinnamon@H234598"


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))


def xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


def state_dir() -> Path:
    return xdg_state_home() / APP_ID


def cache_dir() -> Path:
    return xdg_cache_home() / APP_ID


def recordings_dir() -> Path:
    return cache_dir() / "recordings"


def transcript_dir() -> Path:
    return state_dir() / "transcripts"


def default_state_file() -> Path:
    return state_dir() / "state.json"


def ensure_runtime_dirs() -> None:
    state_dir().mkdir(parents=True, exist_ok=True)
    recordings_dir().mkdir(parents=True, exist_ok=True)
    transcript_dir().mkdir(parents=True, exist_ok=True)

