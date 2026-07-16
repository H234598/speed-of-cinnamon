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
    assert_fd_is_private_directory,
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    _rename_without_replacing,
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
MAX_TRANSCRIPT_FILES = 1_000
MIN_RECORDING_SECONDS = 0
MIN_TYPING_DELAY_MS = 0


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"settings export cleanup failed: {cleanup_error}")


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
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise SettingsExportError("secure settings export temp file creation is not supported on this platform")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
    for _ in range(100):
        temp_name = f".{safe_name}.{secrets.token_hex(8)}.tmp"
        try:
            return os.open(temp_name, flags, 0o600, dir_fd=parent_fd), temp_name
        except FileExistsError:
            continue
    raise SettingsExportError("failed to create settings export temp file")


def _scrub_temp_settings_export_file(parent_fd: int, temp_name: str) -> None:
    if not temp_name:
        return
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise SettingsExportError("secure settings export temp file scrubbing is not supported on this platform")
    fd = os.open(temp_name, os.O_WRONLY | nofollow_flag, dir_fd=parent_fd)
    primary_error: BaseException | None = None
    try:
        file_stat = os.fstat(fd)
        if not stat_module.S_ISREG(file_stat.st_mode):
            return
        remaining = int(file_stat.st_size)
        if remaining > 0:
            os.lseek(fd, 0, os.SEEK_SET)
            chunk = b"\x00" * min(remaining, 65536)
            while remaining > 0:
                written = os.write(fd, chunk[: min(remaining, len(chunk))])
                if written <= 0:
                    break
                remaining -= written
            try:
                os.fsync(fd)
            except OSError:
                pass
        os.ftruncate(fd, 0)
        try:
            os.fsync(fd)
        except OSError:
            pass
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
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
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


def _sanitize_text_field(value: object, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise SettingsExportError(f"{field_name} must be text")
    text = str(value or "")
    if _contains_escaped_null(text):
        raise SettingsExportError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(text):
        raise SettingsExportError(f"{field_name} contains invalid control character")
    text = text.strip()
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
        if key == "max-transcript-files":
            if parsed < 1:
                raise SettingsExportError("setting max-transcript-files must be at least 1")
            if parsed > MAX_TRANSCRIPT_FILES:
                raise SettingsExportError(
                    f"setting max-transcript-files must be at most {MAX_TRANSCRIPT_FILES}"
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
    temp_fd: int | None = None
    backup_name = ""
    backup_moved = False
    activation_attempted = False
    activation_stat: os.stat_result | None = None
    temporary_stat: os.stat_result | None = None
    transaction_active = False
    primary_error: BaseException | None = None

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
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            getattr(second, "st_nlink", 1),
            second.st_size,
        )

    def _same_leaf_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (first.st_dev, first.st_ino, first.st_mode) == (second.st_dev, second.st_ino, second.st_mode)

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
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
            temporary_stat = os.fstat(handle.fileno())
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
                try:
                    backup_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                    current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat_module.S_ISREG(backup_stat.st_mode)
                        or getattr(backup_stat, "st_nlink", 1) < 2
                        or not _same_leaf_inode(backup_stat, existing_stat)
                        or not stat_module.S_ISREG(current_target_stat.st_mode)
                        or not _same_leaf_inode(current_target_stat, existing_stat)
                    ):
                        raise OSError("settings export path changed during backup activation")
                    backup_name = candidate_name
                    os.unlink(path.name, dir_fd=parent_fd)
                    backup_moved = True
                    os.fsync(parent_fd)
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
                                if _same_leaf_inode(candidate_stat, existing_stat):
                                    os.unlink(candidate_name, dir_fd=parent_fd)
                                    os.fsync(parent_fd)
                            except FileNotFoundError:
                                pass
                            except BaseException as cleanup_error:
                                _note_cleanup_failure(exc, cleanup_error)
                    raise
            if not backup_name:
                raise OSError("failed to create settings export recovery backup")

        activation_attempted = True
        _rename_without_replacing(
            temp_name,
            path.name,
            directory_fd=parent_fd,
            field_name="settings export path",
        )
        temp_name = ""
        try:
            activation_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as stat_error:
            raise OSError("settings export could not be inspected after activation") from stat_error
        os.fsync(parent_fd)
        transaction_active = False
        if backup_moved:
            backup_stat = os.stat(backup_name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat_module.S_ISREG(backup_stat.st_mode) or getattr(backup_stat, "st_nlink", 1) != 1:
                raise OSError("settings export recovery backup is not safe")
            if existing_stat is None or not _same_leaf_identity(backup_stat, existing_stat):
                raise OSError("settings export recovery backup changed before cleanup")
            os.unlink(backup_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except BaseException as exc:
        primary_error = exc
        if transaction_active:
            try:
                if activation_attempted:
                    try:
                        current_target_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        current_target_stat = None
                    expected_activation_stat = activation_stat or temporary_stat
                    if current_target_stat is not None:
                        if expected_activation_stat is None or not _same_leaf_identity(current_target_stat, expected_activation_stat):
                            raise OSError("settings export target changed during rollback")
                        os.unlink(path.name, dir_fd=parent_fd)
                        os.fsync(parent_fd)
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
                        os.fsync(parent_fd)
                    else:
                        raise OSError("settings export target exists during rollback")
            except BaseException as rollback_error:
                _note_cleanup_failure(primary_error, rollback_error)
        if isinstance(exc, OSError):
            cleanup_error: BaseException | None = None
            if temp_name:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                except OSError as cleanup_exc:
                    try:
                        _scrub_temp_settings_export_file(parent_fd, temp_name)
                    except BaseException as scrub_error:
                        _note_cleanup_failure(exc, scrub_error)
                    cleanup_error = cleanup_exc
                except BaseException as cleanup_exc:
                    try:
                        _scrub_temp_settings_export_file(parent_fd, temp_name)
                    except BaseException as scrub_error:
                        _note_cleanup_failure(exc, scrub_error)
                    cleanup_error = cleanup_exc
            if cleanup_error is not None:
                error = SettingsExportError(f"failed to write settings export: {path}")
                for note in getattr(exc, "__notes__", ()):
                    error.add_note(note)
                _note_cleanup_failure(error, cleanup_error)
                primary_error = error
                raise error from exc
            error = SettingsExportError(f"failed to write settings export: {path}")
            for note in getattr(exc, "__notes__", ()):
                error.add_note(note)
            primary_error = error
            raise error from exc
        if temp_name:
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError as cleanup_error:
                try:
                    _scrub_temp_settings_export_file(parent_fd, temp_name)
                except BaseException as scrub_error:
                    _note_cleanup_failure(primary_error, scrub_error)
                _note_cleanup_failure(primary_error, cleanup_error)
            except BaseException as cleanup_error:
                try:
                    _scrub_temp_settings_export_file(parent_fd, temp_name)
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
            else:
                error = SettingsExportError("failed to close settings export directory")
                raise error from cleanup_error
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise
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
    except (OSError, ValueError, RecursionError, UnicodeDecodeError) as exc:
        raise SettingsExportError(f"settings export could not be read: {path}") from exc

    if not isinstance(payload, dict):
        raise SettingsExportError("settings export must be a JSON object")
    if payload.get("app") != APP_ID:
        raise SettingsExportError("settings export is for a different app")
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
