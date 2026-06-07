from __future__ import annotations

import os
import stat as stat_module
import tempfile
from pathlib import Path

from .path_safety import (
    assert_fd_is_private_directory,
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
)

APP_ID = "speed-of-cinnamon"
APP_NAME = "Speed of Cinnamon"
APPLET_UUID = "speed-of-cinnamon@H234598"
MAX_XDG_PATH_CHARS = 4_096


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise RuntimeError("value must be text")
    lowered = (value or "").lower()
    control_codepoints = tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))
    if any(sequence in lowered for sequence in ("\\a", "\\b", "\\f", "\\n", "\\r", "\\t", "\\v")):
        return True
    if any(f"\\x{codepoint:02x}" in lowered or f"\\u00{codepoint:02x}" in lowered for codepoint in control_codepoints):
        return True
    return any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)


def _is_oversized_utf8_text(value: str, *, max_chars: int) -> bool:
    try:
        return len(value.encode("utf-8")) > max_chars
    except UnicodeEncodeError:
        return True


def _xdg_path(environment_variable: str, default: Path) -> Path:
    if isinstance(environment_variable, bool) or not isinstance(environment_variable, str):
        raise RuntimeError("environment variable name must be text")
    try:
        value = os.environ[environment_variable]
    except KeyError:
        return default
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return default
    if _contains_escaped_null(value) or _contains_control_chars(value):
        return default
    normalized = (value or "").strip()
    if not normalized:
        return default
    if len(normalized) > MAX_XDG_PATH_CHARS or _is_oversized_utf8_text(
        normalized, max_chars=MAX_XDG_PATH_CHARS
    ):
        return default
    candidate = Path(normalized).expanduser()
    if not candidate.is_absolute():
        return default
    try:
        assert_safe_path_components(candidate, field_name=environment_variable)
        assert_no_symlink_ancestors(candidate, field_name=environment_variable)
    except RuntimeError:
        return default
    return candidate


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
        raise RuntimeError("temporary directory must not be a symlink")
    private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RuntimeError("secure temporary directory open is not supported on this platform")
    open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow_flag
    try:
        fd = os.open(private_root, open_flags)
    except OSError as exc:
        raise RuntimeError("temporary directory is not safe") from exc
    try:
        file_stat = os.fstat(fd)
        if not stat_module.S_ISDIR(file_stat.st_mode):
            raise RuntimeError("temporary directory is not a directory")
        if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
            raise RuntimeError("temporary directory is not owned by the current user")
        os.fchmod(fd, 0o700)
        file_stat = os.fstat(fd)
    finally:
        os.close(fd)
    assert_no_symlink_ancestors(private_root, field_name="temporary directory")
    if file_stat.st_mode & 0o077:
        raise RuntimeError("temporary directory is not private")
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


def xdg_config_home() -> Path:
    return _xdg_path("XDG_CONFIG_HOME", _safe_home_path(".config"))


def state_dir() -> Path:
    return xdg_state_home() / APP_ID


def data_dir() -> Path:
    return xdg_data_home() / APP_ID


def cache_dir() -> Path:
    return xdg_cache_home() / APP_ID


def config_dir() -> Path:
    return xdg_config_home() / APP_ID


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


def profanity_filter_file() -> Path:
    return data_dir() / "profanity-filter.txt"


def alarms_file() -> Path:
    return data_dir() / "alarms.json"


def ensure_runtime_dirs() -> None:
    for directory in (
        config_dir(),
        data_dir(),
        state_dir(),
        cache_dir(),
        recordings_dir(),
        transcript_dir(),
        diagnostics_dir(),
        logs_dir(),
        models_dir(),
        ctranslate2_models_dir(),
    ):
        fd = ensure_directory_without_following_symlinks(directory, field_name="runtime directory")
        try:
            try:
                os.fchmod(fd, 0o700)
            except OSError as exc:
                raise RuntimeError("runtime directory could not be made private") from exc
            assert_fd_is_private_directory(fd, field_name="runtime directory")
        finally:
            os.close(fd)
