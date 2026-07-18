from __future__ import annotations

import os
import stat as stat_module
import tempfile
from pathlib import Path
from typing import Callable

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


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"runtime directory cleanup failed: {cleanup_error}")


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


def _xdg_path(environment_variable: str, default: Path | Callable[[], Path]) -> Path:
    if isinstance(environment_variable, bool) or not isinstance(environment_variable, str):
        raise RuntimeError("environment variable name must be text")

    def fallback() -> Path:
        return default() if callable(default) else default

    try:
        value = os.environ[environment_variable]
    except KeyError:
        return fallback()
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return fallback()
    if _contains_escaped_null(value) or _contains_control_chars(value):
        return fallback()
    if not value or not value.strip():
        return fallback()
    normalized = value
    if len(normalized) > MAX_XDG_PATH_CHARS or _is_oversized_utf8_text(
        normalized, max_chars=MAX_XDG_PATH_CHARS
    ):
        return fallback()
    try:
        candidate = Path(normalized).expanduser()
    except (OSError, RuntimeError):
        return fallback()
    if not candidate.is_absolute():
        return fallback()
    try:
        assert_safe_path_components(candidate, field_name=environment_variable)
        assert_no_symlink_ancestors(candidate, field_name=environment_variable)
    except RuntimeError:
        return fallback()
    return candidate


def _private_runtime_temp_root() -> Path:
    try:
        temp_root = Path(tempfile.gettempdir())
        temp_root_text = str(temp_root)
        if (
            not temp_root.is_absolute()
            or _contains_escaped_null(temp_root_text)
            or _contains_control_chars(temp_root_text)
            or len(temp_root_text) > MAX_XDG_PATH_CHARS
            or _is_oversized_utf8_text(temp_root_text, max_chars=MAX_XDG_PATH_CHARS)
        ):
            raise RuntimeError("temporary directory is invalid")
        assert_safe_path_components(temp_root, field_name="temporary directory")
        assert_no_symlink_ancestors(temp_root, field_name="temporary directory")
    except (OSError, RuntimeError, TypeError, ValueError):
        temp_root = Path("/tmp")  # nosec B108
        assert_safe_path_components(temp_root, field_name="temporary directory")
        assert_no_symlink_ancestors(temp_root, field_name="temporary directory")
    uid = os.getuid() if hasattr(os, "getuid") else os.getpid()
    private_root = temp_root / f"{APP_ID}-{uid}"
    if private_root.is_symlink():
        raise RuntimeError("temporary directory must not be a symlink")
    try:
        private_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("temporary directory is not safe") from exc
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RuntimeError("secure temporary directory open is not supported on this platform")
    open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow_flag
    try:
        fd = os.open(private_root, open_flags)
    except OSError as exc:
        raise RuntimeError("temporary directory is not safe") from exc
    try:
        primary_error: BaseException | None = None
        try:
            file_stat = os.fstat(fd)
            if not stat_module.S_ISDIR(file_stat.st_mode):
                raise RuntimeError("temporary directory is not a directory")
            if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
                raise RuntimeError("temporary directory is not owned by the current user")
            os.fchmod(fd, 0o700)
            file_stat = os.fstat(fd)
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
                    pass
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise
    except (OSError, ValueError) as exc:
        raise RuntimeError("temporary directory is not safe") from exc
    assert_no_symlink_ancestors(private_root, field_name="temporary directory")
    if file_stat.st_mode & 0o077:
        raise RuntimeError("temporary directory is not private")
    return private_root


def _safe_home_path(*parts: str) -> Path:
    try:
        candidate = Path.home().joinpath(*parts)
    except (OSError, RuntimeError):
        return _private_runtime_temp_root().joinpath(*parts)
    try:
        candidate_text = str(candidate)
        if (
            not candidate.is_absolute()
            or not candidate_text
            or len(candidate_text) > MAX_XDG_PATH_CHARS
            or _is_oversized_utf8_text(candidate_text, max_chars=MAX_XDG_PATH_CHARS)
            or _contains_escaped_null(candidate_text)
            or _contains_control_chars(candidate_text)
        ):
            raise RuntimeError("home path is invalid")
        assert_safe_path_components(candidate, field_name="home path")
        assert_no_symlink_ancestors(candidate, field_name="home path")
    except RuntimeError:
        return _private_runtime_temp_root().joinpath(*parts)
    return candidate


def xdg_data_home() -> Path:
    return _xdg_path("XDG_DATA_HOME", lambda: _safe_home_path(".local", "share"))


def xdg_state_home() -> Path:
    return _xdg_path("XDG_STATE_HOME", lambda: _safe_home_path(".local", "state"))


def xdg_cache_home() -> Path:
    return _xdg_path("XDG_CACHE_HOME", lambda: _safe_home_path(".cache"))


def xdg_config_home() -> Path:
    return _xdg_path("XDG_CONFIG_HOME", lambda: _safe_home_path(".config"))


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
        primary_error: BaseException | None = None
        try:
            try:
                os.fchmod(fd, 0o700)
            except OSError as exc:
                raise RuntimeError("runtime directory could not be made private") from exc
            assert_fd_is_private_directory(fd, field_name="runtime directory")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise
