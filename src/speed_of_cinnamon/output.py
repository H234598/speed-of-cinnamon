from __future__ import annotations

import hashlib
import fcntl
import json
import math
import signal
import shutil
import secrets
import stat
import subprocess  # nosec B404
import sys
import tempfile
import os
import threading
import time
from pathlib import Path
from typing import BinaryIO

from .app_logging import log_event
from .path_safety import (
    _rename_without_replacing,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)
from .paths import state_dir


class OutputError(RuntimeError):
    pass


class _OutputProcessError(OutputError):
    def __init__(self, message: str, *, phase: str) -> None:
        super().__init__(message)
        self.phase = phase


class OutputCleanupError(OutputError):
    pass


class PasteNotAttemptedError(OutputError):
    pass


_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BASE_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
}
_DANGEROUS_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")
_DANGEROUS_ENV_KEYS = {
    "ENV",
    "PWD",
    "OLDPWD",
    "CDPATH",
    "PS4",
    "BASH_XTRACEFD",
    "SHELLOPTS",
    "PROMPT_COMMAND",
    "IFS",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "BASH_ENV",
    "DBUS_SESSION_BUS_ADDRESS",
}
MAX_OUTPUT_CHARS = 1_000_000
MAX_INPUT_CHARS = 1_000_000
MAX_ERROR_CHARS = 1_024
MAX_PASTE_TIMEOUT_SECONDS = 10
MAX_TYPE_TIMEOUT_SECONDS = 30
MAX_EXEC_TIMEOUT_SECONDS = 10
MAX_CLIPBOARD_PENDING_QUARANTINES = 8
MAX_CLIPBOARD_PENDING_QUARANTINE_CONTEXT_LENGTH = 128
MAX_CLIPBOARD_PENDING_QUARANTINE_BYTES = 16 * 1024
MAX_TYPE_DELAY_MS = 10_000
MAX_DUPLICATE_TEXT_SECONDS = 2.5
MAX_DUPLICATE_LOCK_SECONDS = 30.0
MAX_CLIPBOARD_DEDUP_STATE_BYTES = 1_000_000
MAX_CLIPBOARD_DEDUP_LOCK_BYTES = 1_024
CLIPBOARD_DEDUP_LOCK_RETRY_ATTEMPTS = 5
CLIPBOARD_DEDUP_LOCK_RETRY_DELAY_SECONDS = 0.01
CLIPBOARD_DEDUP_STATE_FILE = "clipboard-last.json"
CLIPBOARD_DEDUP_LOCK_FILE = ".clipboard-last.lock"
CLIPBOARD_PENDING_QUARANTINE_FILE = "clipboard-pending.json"
CLIPBOARD_PENDING_QUARANTINE_LOCK_FILE = ".clipboard-pending.lock"
_CLIPBOARD_DEDUP_PENDING_FIELD = "pending"
_CLIPBOARD_FINGERPRINT_HEX_CHARS = frozenset("0123456789abcdef")

_LAST_CLIPBOARD_TEXT: str = ""
_LAST_CLIPBOARD_METHOD: str | None = None
_LAST_CLIPBOARD_INSERTION: float = 0.0
_LAST_CLIPBOARD_CONTEXT: str | None = None
_CLIPBOARD_PENDING_QUARANTINE: dict[tuple[str, str | None], float] = {}
_CLIPBOARD_PENDING_LEDGER_MISSING = "missing"
_CLIPBOARD_PENDING_LEDGER_VALID = "valid"
_CLIPBOARD_PENDING_LEDGER_INVALID = "invalid"


def _is_unsafe_env_var(name: str) -> bool:
    return name in _DANGEROUS_ENV_KEYS or name.startswith(_DANGEROUS_ENV_PREFIXES)


def _coerce_environment_value(name: str) -> str | None:
    if isinstance(name, bool) or not isinstance(name, str):
        return None
    try:
        value = os.environ.__getitem__(name)
    except KeyError:
        return None
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return None
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
        return None
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return value


def _filtered_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = _coerce_environment_value(key)
        if value is not None:
            env[key] = value
    if base is not None:
        if not isinstance(base, dict):
            raise OutputError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise OutputError("environment keys must be text")
            if isinstance(value, bool):
                raise OutputError("environment values must be text")
            if not isinstance(value, str):
                raise OutputError("environment base must be a mapping")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise OutputError("environment key contains invalid control character")
            if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
                raise OutputError("environment value contains invalid control character")
            try:
                key.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise OutputError("environment key contains invalid UTF-8") from exc
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise OutputError("environment value contains invalid UTF-8") from exc
            if _is_unsafe_env_var(key):
                raise OutputError(f"environment key is not allowed: {key}")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env
TERMINAL_WINDOW_MARKERS = (
    "alacritty",
    "blackbox",
    "codex",
    "com.mitchellh.ghostty",
    "com.system76.cosmic-term",
    "console",
    "cool-retro-term",
    "cosmic terminal",
    "cosmic-term",
    "foot",
    "gnome-terminal",
    "guake",
    "hyper",
    "kgx",
    "kitty",
    "konsole",
    "lxterminal",
    "mate-terminal",
    "org.gnome.console",
    "org.gnome.terminal",
    "ptyxis",
    "qterminal",
    "rio",
    "rxvt",
    "sakura",
    "tabby",
    "terminator",
    "termius",
    "tilix",
    "tty",
    "urxvt",
    "wezterm",
    "xfce4-terminal",
    "xterm",
    "yakuake",
)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise OutputError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise OutputError("value must be text")
    lowered = (value or "").lower()
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


def _filesize(file: BinaryIO) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise OutputError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: BinaryIO, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise OutputError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise OutputError("max_chars must be an integer")
    if max_chars <= 0:
        raise OutputError("max_chars must be positive")

    file.seek(0)
    try:
        text = file.read(max_chars).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutputError("command output is not valid UTF-8") from exc
    if _contains_escaped_null(text):
        raise OutputError("command output contains invalid null byte")
    return text


def _clipboard_text_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def _clipboard_insertion_fingerprint(text: str, dedupe_context: str | None = None) -> str:
    if not dedupe_context:
        return _clipboard_text_fingerprint(text)
    payload = "\0".join(("clipboard-insertion-v2", dedupe_context, text))
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


def _clipboard_method_dedupe_context(method: str, dedupe_context: str | None = None) -> str:
    return "\0".join((method, dedupe_context or ""))


def _reject_non_finite_json_number(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _clipboard_dedup_context_for_window_snapshot(snapshot: tuple[str, str, str] | None) -> str | None:
    if snapshot is None:
        return None
    window_id, _window_title, window_class = snapshot
    payload = "\0".join(("x-window-v3", window_id, window_class))
    return hashlib.sha256(payload.encode("utf-8", "surrogatepass")).hexdigest()


def _is_clipboard_text_fingerprint(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        return False
    return len(value) == 64 and all(char in _CLIPBOARD_FINGERPRINT_HEX_CHARS for char in value.lower())


def _clipboard_dedup_state_path() -> Path:
    path = state_dir() / CLIPBOARD_DEDUP_STATE_FILE
    assert_no_symlink_ancestors(path, field_name="clipboard dedupe state")
    return path


def _read_clipboard_dedup_state() -> tuple[str, float]:
    trusted, snapshot = _read_trusted_clipboard_dedup_state()
    if not trusted:
        return "", 0.0
    return snapshot


def _read_trusted_clipboard_dedup_state() -> tuple[bool, tuple[str, float]]:
    trusted, snapshot, _pending = _read_clipboard_dedup_state_entry()
    return trusted, snapshot


def _read_clipboard_dedup_state_entry() -> tuple[bool, tuple[str, float], bool]:
    try:
        path = _clipboard_dedup_state_path()
    except RuntimeError:
        return False, ("", 0.0), False
    try:
        path_exists = path.exists()
        path_is_symlink = path.is_symlink()
    except OSError:
        return False, ("", 0.0), False
    if not path_exists and not path_is_symlink:
        return True, ("", 0.0), False
    try:
        raw = read_text_without_following_symlinks(
            path,
            field_name="clipboard dedupe state",
            max_bytes=MAX_CLIPBOARD_DEDUP_STATE_BYTES,
        )
    except FileNotFoundError:
        return True, ("", 0.0), False
    except (OSError, UnicodeDecodeError):
        return False, ("", 0.0), False
    try:
        payload = json.loads(raw, parse_constant=_reject_non_finite_json_number)
    except (TypeError, ValueError, RecursionError, MemoryError):
        return False, ("", 0.0), False
    if not isinstance(payload, dict):
        return False, ("", 0.0), False
    at_value = payload.get("at")
    if not isinstance(at_value, (int, float)) or isinstance(at_value, bool):
        return False, ("", 0.0), False
    try:
        at = float(at_value)
    except (OverflowError, TypeError, ValueError):
        return False, ("", 0.0), False
    if not math.isfinite(at):
        return False, ("", 0.0), False
    pending_raw = payload.get(_CLIPBOARD_DEDUP_PENDING_FIELD, False)
    if pending_raw is not False and not isinstance(pending_raw, bool):
        return False, ("", 0.0), False
    pending = bool(pending_raw)
    fingerprint_value = payload.get("sha256")
    if "sha256" in payload:
        if not _is_clipboard_text_fingerprint(fingerprint_value):
            return False, ("", 0.0), False
        return True, (str(fingerprint_value).lower(), at), pending

    text_value = payload.get("text")
    if not isinstance(text_value, str) or text_value is None or isinstance(text_value, bool):
        return False, ("", 0.0), False
    if not text_value:
        return False, ("", 0.0), False
    _clear_clipboard_dedup_state()
    return False, ("", 0.0), False


def _write_clipboard_dedup_state(text: str, at: float) -> bool:
    if not text:
        return False
    return _write_clipboard_dedup_fingerprint_state(_clipboard_text_fingerprint(text), at)


def _write_clipboard_dedup_fingerprint_state(fingerprint: str, at: float, *, pending: bool = False) -> bool:
    if not _is_clipboard_text_fingerprint(fingerprint):
        return False
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return False
    try:
        at_value = float(at)
    except (OverflowError, TypeError, ValueError):
        return False
    if not math.isfinite(at_value):
        return False
    try:
        path = _clipboard_dedup_state_path()
    except RuntimeError:
        return False
    payload = {"sha256": fingerprint, "at": at_value}
    if pending:
        payload[_CLIPBOARD_DEDUP_PENDING_FIELD] = True
    try:
        write_text_atomically_without_following_symlinks(
            path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            field_name="clipboard dedupe state",
        )
    except (OSError, RuntimeError, MemoryError):
        return False
    return True


def _clipboard_dedup_lock_path() -> Path:
    path = state_dir() / CLIPBOARD_DEDUP_LOCK_FILE
    assert_no_symlink_ancestors(path, field_name="clipboard dedupe lock")
    return path


def _clipboard_lock_pid_is_zombie(pid: int) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").strip()
        close = raw.rindex(")")
        rest = raw[close + 2 :].split()
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    return bool(rest and rest[0] in {"Z", "X", "x"})


def _clipboard_lock_pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, ValueError):
        return False
    except OSError:
        return True
    return not _clipboard_lock_pid_is_zombie(pid)


def _clipboard_lock_identity_for_pid(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    try:
        close = raw.rindex(")")
        rest = raw[close + 2 :].split()
    except ValueError:
        return None
    if len(rest) < 20:
        return None
    boot_id = None
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    if not boot_id:
        return None
    start_time = rest[19]
    if not start_time:
        return None
    return f"{boot_id}:{start_time}"


def _read_clipboard_dedup_lock_pid(path: Path) -> int | None:
    try:
        raw = read_text_without_following_symlinks(
            path,
            field_name="clipboard dedupe lock",
            max_bytes=MAX_CLIPBOARD_DEDUP_LOCK_BYTES,
        )
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None
    first_line = raw.splitlines()[0].strip() if raw.splitlines() else ""
    try:
        pid = int(first_line)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _read_clipboard_dedup_lock_identity(path: Path) -> str | None:
    try:
        raw = read_text_without_following_symlinks(
            path,
            field_name="clipboard dedupe lock",
            max_bytes=MAX_CLIPBOARD_DEDUP_LOCK_BYTES,
        )
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None
    lines = raw.splitlines()
    if len(lines) < 2:
        return None
    identity = lines[1].strip()
    return identity or None


def _read_clipboard_dedup_lock_lines_at(parent_fd: int, name: str) -> list[str] | None:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        return None
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    fd: int | None = None
    primary_error: BaseException | None = None
    cleanup_failed = False
    try:
        fd = os.open(name, os.O_RDONLY | nofollow_flag | nonblock_flag | cloexec_flag, dir_fd=parent_fd)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or getattr(file_stat, "st_nlink", 1) != 1:
            return None
        while True:
            try:
                raw = os.read(fd, MAX_CLIPBOARD_DEDUP_LOCK_BYTES + 1)
            except InterruptedError:
                continue
            break
    except OSError as exc:
        primary_error = exc
        return None
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                if primary_error is not None:
                    primary_error.add_note("clipboard lock cleanup failed during FD close")
                else:
                    cleanup_failed = True
            except BaseException:
                if primary_error is not None:
                    primary_error.add_note("clipboard lock cleanup failed")
                else:
                    raise
    if cleanup_failed:
        return None
    if len(raw) > MAX_CLIPBOARD_DEDUP_LOCK_BYTES:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text.splitlines()


def _read_clipboard_dedup_lock_pid_at(parent_fd: int, name: str) -> int | None:
    lines = _read_clipboard_dedup_lock_lines_at(parent_fd, name)
    first_line = lines[0].strip() if lines else ""
    try:
        pid = int(first_line)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _read_clipboard_dedup_lock_identity_at(parent_fd: int, name: str) -> str | None:
    lines = _read_clipboard_dedup_lock_lines_at(parent_fd, name)
    if not lines or len(lines) < 2:
        return None
    identity = lines[1].strip()
    return identity or None


def _write_all(fd: int, payload: bytes, *, field_name: str) -> None:
    view = memoryview(payload)
    offset = 0
    while offset < len(view):
        try:
            written = os.write(fd, view[offset:])
        except InterruptedError:
            continue
        if written <= 0:
            raise OSError(f"short write to {field_name}")
        offset += written


def _fsync_fd(fd: int) -> None:
    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            continue


def _same_clipboard_lock_snapshot(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_nlink,
        first.st_size,
        first.st_mtime_ns,
        first.st_ctime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_nlink,
        second.st_size,
        second.st_mtime_ns,
        second.st_ctime_ns,
    )


def _same_clipboard_lock_claim(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_nlink,
        first.st_mode,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_nlink,
        second.st_mode,
        second.st_size,
        second.st_mtime_ns,
    )


def _same_clipboard_lock_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        getattr(first, "st_nlink", 1),
    ) == (
        second.st_dev,
        second.st_ino,
        getattr(second, "st_nlink", 1),
    )


def _unlink_clipboard_lock_at(
    parent_fd: int,
    path: Path,
    *,
    expected_stat: os.stat_result | None = None,
) -> bool:
    try:
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(current.st_mode):
        return False
    if getattr(current, "st_nlink", 1) != 1:
        return False
    if expected_stat is not None and not _same_clipboard_lock_snapshot(current, expected_stat):
        return False
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        return False
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    for _ in range(100):
        cleanup_name = f"{path.name}.{secrets.token_hex(8)}.cleanup"
        try:
            _rename_without_replacing(
                path.name,
                cleanup_name,
                directory_fd=parent_fd,
                field_name="clipboard dedupe lock cleanup",
            )
        except FileExistsError:
            continue
        changed = False
        unlinked = False
        cleanup_fd: int | None = None
        try:
            claimed = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(claimed.st_mode)
                or getattr(claimed, "st_nlink", 1) != 1
                or (expected_stat is not None and not _same_clipboard_lock_claim(claimed, expected_stat))
            ):
                changed = True
                raise OSError("clipboard dedupe lock changed before cleanup")
            cleanup_fd = os.open(
                cleanup_name,
                os.O_RDONLY | nofollow_flag | cloexec_flag,
                dir_fd=parent_fd,
            )
            opened = os.fstat(cleanup_fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_nlink", 1) != 1
                or not _same_clipboard_lock_identity(opened, claimed)
                or (expected_stat is not None and not _same_clipboard_lock_identity(opened, expected_stat))
            ):
                changed = True
                raise OSError("clipboard dedupe lock changed before cleanup")
            latest = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(latest.st_mode)
                or getattr(latest, "st_nlink", 1) != 1
                or not _same_clipboard_lock_identity(latest, opened)
            ):
                changed = True
                raise OSError("clipboard dedupe lock changed before cleanup")
            os.unlink(cleanup_name, dir_fd=parent_fd)
            unlinked = True
            _fsync_fd(parent_fd)
        except BaseException as exc:
            if not changed and not unlinked:
                try:
                    if cleanup_fd is not None:
                        restore_fd_stat = os.fstat(cleanup_fd)
                        restore_path_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                        if not _same_clipboard_lock_identity(restore_fd_stat, restore_path_stat):
                            changed = True
                            raise OSError("clipboard dedupe lock changed before cleanup restore")
                    _rename_without_replacing(
                        cleanup_name,
                        path.name,
                        directory_fd=parent_fd,
                        field_name="clipboard dedupe lock cleanup restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException:
                    exc.add_note("clipboard dedupe lock cleanup restore failed")
            if changed:
                if isinstance(exc, Exception):
                    return False
                raise
            raise
        finally:
            if cleanup_fd is not None:
                active_error = sys.exc_info()[1]
                try:
                    os.close(cleanup_fd)
                except BaseException:
                    if active_error is not None:
                        active_error.add_note("clipboard dedupe lock cleanup FD close failed")
                    else:
                        raise
        return True
    return False


def _acquire_clipboard_dedup_lock() -> Path | None:
    acquired_path: Path | None = None
    try:
        path = _clipboard_dedup_lock_path()
    except RuntimeError:
        return None
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        return None
    cloexec_flag = getattr(os, "O_CLOEXEC", 0)
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="clipboard dedupe lock directory",
        )
    except OSError:
        return None
    primary_error: BaseException | None = None
    cleanup_failed = False
    try:
        for _attempt in range(2):
            now = time.time()
            created_stat: os.stat_result | None = None
            created_fd: int | None = None
            try:
                created_fd = os.open(
                    path.name,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY | nofollow_flag | cloexec_flag,
                    0o600,
                    dir_fd=parent_fd,
                )
                created_stat = os.fstat(created_fd)
            except FileExistsError:
                try:
                    existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except OSError:
                    return None
                if not stat.S_ISREG(existing.st_mode):
                    return None
                if getattr(existing, "st_nlink", 1) != 1:
                    return None
                owner_pid = _read_clipboard_dedup_lock_pid_at(parent_fd, path.name)
                owner_identity = _read_clipboard_dedup_lock_identity_at(parent_fd, path.name)
                if owner_pid is not None and _clipboard_lock_pid_is_running(owner_pid):
                    if owner_identity is None:
                        return None
                    owner_current_identity = _clipboard_lock_identity_for_pid(owner_pid)
                    if owner_current_identity is None:
                        return None
                    if owner_identity == owner_current_identity:
                        return None
                    if now - existing.st_mtime <= MAX_DUPLICATE_LOCK_SECONDS:
                        return None
                if owner_pid is None and now - existing.st_mtime <= MAX_DUPLICATE_LOCK_SECONDS:
                    return None
                try:
                    current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except OSError:
                    return None
                if getattr(current, "st_nlink", 1) != 1:
                    return None
                if not _same_clipboard_lock_snapshot(existing, current):
                    return None
                try:
                    if not _unlink_clipboard_lock_at(parent_fd, path, expected_stat=current):
                        return None
                except OSError:
                    return None
                continue
            except OSError:
                if created_fd is not None:
                    try:
                        os.close(created_fd)
                    except BaseException:
                        pass
                    if created_stat is not None:
                        try:
                            _unlink_clipboard_lock_at(parent_fd, path, expected_stat=created_stat)
                        except BaseException:
                            pass
                return None
            except BaseException:
                if created_fd is not None:
                    try:
                        os.close(created_fd)
                    except BaseException:
                        pass
                    if created_stat is not None:
                        try:
                            _unlink_clipboard_lock_at(parent_fd, path, expected_stat=created_stat)
                        except BaseException:
                            pass
                raise

            fd = created_fd
            try:
                identity = _clipboard_lock_identity_for_pid(os.getpid())
                if identity is None:
                    _write_all(fd, f"{os.getpid()}\n".encode("ascii"), field_name="clipboard dedupe lock")
                else:
                    _write_all(fd, f"{os.getpid()}\n{identity}\n".encode("ascii"), field_name="clipboard dedupe lock")
                _fsync_fd(fd)
                _fsync_fd(parent_fd)
            except OSError:
                cleanup_stat = created_stat
                try:
                    cleanup_stat = os.fstat(fd)
                except BaseException:
                    pass
                try:
                    os.close(fd)
                except BaseException:
                    pass
                try:
                    _unlink_clipboard_lock_at(parent_fd, path, expected_stat=cleanup_stat)
                except BaseException:
                    pass
                return None
            except BaseException:
                cleanup_stat = created_stat
                try:
                    cleanup_stat = os.fstat(fd)
                except BaseException:
                    pass
                try:
                    os.close(fd)
                except BaseException:
                    pass
                try:
                    _unlink_clipboard_lock_at(parent_fd, path, expected_stat=cleanup_stat)
                except BaseException:
                    pass
                raise
            try:
                acquired_path = path
                os.close(fd)
            except OSError:
                try:
                    _release_clipboard_dedup_lock(path)
                except BaseException:
                    pass
                return None
            except BaseException:
                try:
                    _release_clipboard_dedup_lock(path)
                except BaseException:
                    pass
                raise
            break
        if acquired_path is None:
            return None
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            if primary_error is not None:
                primary_error.add_note("clipboard dedupe lock cleanup failed during parent FD close")
            else:
                if acquired_path is not None:
                    try:
                        _release_clipboard_dedup_lock(acquired_path)
                    except BaseException:
                        pass
                cleanup_failed = True
        except BaseException:
            if primary_error is not None:
                primary_error.add_note("clipboard dedupe lock cleanup failed during parent FD close")
            else:
                if acquired_path is not None:
                    try:
                        _release_clipboard_dedup_lock(acquired_path)
                    except BaseException:
                        pass
                raise
    if cleanup_failed:
        return None
    return acquired_path


def _release_clipboard_dedup_lock(path: Path | None) -> None:
    if path is None:
        return
    primary_error = sys.exc_info()[1]

    def note_primary() -> None:
        if primary_error is not None:
            primary_error.add_note("clipboard dedupe lock release failed")

    try:
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="clipboard dedupe lock directory",
        )
    except Exception:
        note_primary()
        return
    except BaseException:
        if primary_error is not None:
            note_primary()
            return
        raise
    try:
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            owner_pid = _read_clipboard_dedup_lock_pid_at(parent_fd, path.name)
            if owner_pid != os.getpid():
                return
            owner_identity = _read_clipboard_dedup_lock_identity_at(parent_fd, path.name)
            current_identity = _clipboard_lock_identity_for_pid(os.getpid())
            if owner_identity is not None and owner_identity != current_identity:
                return
            _unlink_clipboard_lock_at(parent_fd, path, expected_stat=current)
        except Exception:
            note_primary()
            return
        except BaseException:
            if primary_error is not None:
                note_primary()
                return
            raise
    finally:
        try:
            os.close(parent_fd)
        except Exception:
            note_primary()
        except BaseException:
            if primary_error is not None:
                note_primary()
            else:
                raise


def _normalize_clipboard_text(text: str) -> str:
    return text


def _clipboard_pending_quarantine_path() -> Path:
    path = state_dir() / CLIPBOARD_PENDING_QUARANTINE_FILE
    assert_no_symlink_ancestors(path, field_name="clipboard pending quarantine")
    return path


def _valid_clipboard_pending_quarantine_context(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or len(value) > MAX_CLIPBOARD_PENDING_QUARANTINE_CONTEXT_LENGTH:
        return False
    return all(0x20 <= ord(char) <= 0x7E for char in value)


def _clipboard_pending_quarantine_key(
    text: str,
    method: str,
    dedupe_context: str | None,
) -> tuple[str, str | None] | None:
    if not isinstance(text, str) or isinstance(text, bool):
        return None
    if not isinstance(method, str) or not _valid_clipboard_pending_quarantine_context(dedupe_context):
        return None
    fingerprint = _clipboard_insertion_fingerprint(
        _normalize_clipboard_text(text),
        _clipboard_method_dedupe_context(method, dedupe_context),
    )
    return fingerprint, dedupe_context


def _log_clipboard_pending_quarantine_issue(event: str, message: str) -> None:
    try:
        log_event("warning", event, error=message)
    except Exception:
        return


def _prune_clipboard_pending_quarantine(now: float | None = None) -> None:
    if now is None:
        now = time.monotonic()
    for key, deadline in list(_CLIPBOARD_PENDING_QUARANTINE.items()):
        if not isinstance(deadline, (int, float)) or isinstance(deadline, bool) or deadline <= now:
            _CLIPBOARD_PENDING_QUARANTINE.pop(key, None)
    if len(_CLIPBOARD_PENDING_QUARANTINE) > MAX_CLIPBOARD_PENDING_QUARANTINES:
        excess = len(_CLIPBOARD_PENDING_QUARANTINE) - MAX_CLIPBOARD_PENDING_QUARANTINES
        for key, _deadline in sorted(
            _CLIPBOARD_PENDING_QUARANTINE.items(),
            key=lambda item: item[1],
        )[:excess]:
            _CLIPBOARD_PENDING_QUARANTINE.pop(key, None)


def _clipboard_pending_quarantine_file_identity(
    path: Path,
) -> tuple[str, tuple[int, int, int, int, int] | None]:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return _CLIPBOARD_PENDING_LEDGER_MISSING, None
    except OSError:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, None
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, None
    get_uid = getattr(os, "getuid", None)
    if get_uid is not None:
        try:
            if file_stat.st_uid != get_uid():
                return _CLIPBOARD_PENDING_LEDGER_INVALID, None
        except (OSError, RuntimeError):
            return _CLIPBOARD_PENDING_LEDGER_INVALID, None
    if file_stat.st_mode & 0o077:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, None
    identity = (
        int(file_stat.st_dev),
        int(file_stat.st_ino),
        int(file_stat.st_mode),
        int(file_stat.st_nlink),
        int(file_stat.st_uid),
    )
    return _CLIPBOARD_PENDING_LEDGER_VALID, identity


def _read_clipboard_pending_quarantine_ledger() -> tuple[
    str,
    dict[tuple[str, str | None], float],
    tuple[int, int, int, int, int] | None,
]:
    try:
        path = _clipboard_pending_quarantine_path()
    except (OSError, RuntimeError):
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    status, identity = _clipboard_pending_quarantine_file_identity(path)
    if status == _CLIPBOARD_PENDING_LEDGER_MISSING:
        return status, {}, None
    if status != _CLIPBOARD_PENDING_LEDGER_VALID or identity is None:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    try:
        raw = read_text_without_following_symlinks(
            path,
            field_name="clipboard pending quarantine",
            max_bytes=MAX_CLIPBOARD_PENDING_QUARANTINE_BYTES,
        )
    except (FileNotFoundError, OSError, RuntimeError, UnicodeDecodeError, MemoryError, ValueError):
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    final_status, final_identity = _clipboard_pending_quarantine_file_identity(path)
    if final_status != _CLIPBOARD_PENDING_LEDGER_VALID or final_identity != identity:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    try:
        payload = json.loads(raw, parse_constant=_reject_non_finite_json_number)
    except (TypeError, ValueError, RecursionError, MemoryError):
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    if not isinstance(payload, dict) or set(payload) != {"entries"}:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    entries = payload["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_CLIPBOARD_PENDING_QUARANTINES:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    now = time.time()
    try:
        now_value = float(now)
    except (OverflowError, TypeError, ValueError):
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now_value):
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    result: dict[tuple[str, str | None], float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"context", "expires_at", "sha256"}:
            return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
        fingerprint = entry["sha256"]
        context = entry["context"]
        expires_at = entry["expires_at"]
        if (
            not _is_clipboard_text_fingerprint(fingerprint)
            or not _valid_clipboard_pending_quarantine_context(context)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
        ):
            return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
        try:
            expires_value = float(expires_at)
        except (OverflowError, TypeError, ValueError):
            return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
        if not math.isfinite(expires_value):
            return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
        key = (fingerprint.lower(), context)
        if key in result:
            return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
        if expires_value > now_value:
            result[key] = expires_value
    final_status, final_identity = _clipboard_pending_quarantine_file_identity(path)
    if final_status != _CLIPBOARD_PENDING_LEDGER_VALID or final_identity != identity:
        return _CLIPBOARD_PENDING_LEDGER_INVALID, {}, None
    return _CLIPBOARD_PENDING_LEDGER_VALID, result, identity


def _clipboard_pending_quarantine_lock_path() -> Path:
    path = state_dir() / CLIPBOARD_PENDING_QUARANTINE_LOCK_FILE
    assert_no_symlink_ancestors(path, field_name="clipboard pending quarantine lock")
    return path


def _acquire_clipboard_pending_quarantine_lock() -> int | None:
    try:
        path = _clipboard_pending_quarantine_lock_path()
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if nofollow_flag is None:
            return None
        cloexec_flag = getattr(os, "O_CLOEXEC", 0)
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="clipboard pending quarantine lock directory",
        )
    except (OSError, RuntimeError):
        return None
    try:
        fd = os.open(
            path.name,
            os.O_CREAT | os.O_RDWR | nofollow_flag | cloexec_flag,
            0o600,
            dir_fd=parent_fd,
        )
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode) or getattr(file_stat, "st_nlink", 1) != 1:
            os.close(fd)
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            return None
        return fd
    except OSError:
        return None
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass


def _release_clipboard_pending_quarantine_lock(fd: int | None) -> None:
    if fd is None:
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        os.close(fd)
    except OSError:
        pass


def _write_clipboard_pending_quarantine_ledger(
    entries: dict[tuple[str, str | None], float],
    *,
    expected_identity: tuple[int, int, int, int, int] | None,
    allow_missing: bool,
    lock_fd: int | None = None,
) -> bool:
    if lock_fd is not None:
        return _write_clipboard_pending_quarantine_ledger_unlocked(
            entries,
            expected_identity=expected_identity,
            allow_missing=allow_missing,
        )
    lock_fd = _acquire_clipboard_pending_quarantine_lock()
    if lock_fd is None:
        return False
    try:
        return _write_clipboard_pending_quarantine_ledger_unlocked(
            entries,
            expected_identity=expected_identity,
            allow_missing=allow_missing,
        )
    finally:
        _release_clipboard_pending_quarantine_lock(lock_fd)


def _write_clipboard_pending_quarantine_ledger_unlocked(
    entries: dict[tuple[str, str | None], float],
    *,
    expected_identity: tuple[int, int, int, int, int] | None,
    allow_missing: bool,
) -> bool:
    now = time.time()
    try:
        now_value = float(now)
    except (OverflowError, TypeError, ValueError):
        return False
    if isinstance(now, bool) or not isinstance(now, (int, float)) or not math.isfinite(now_value):
        return False
    valid_entries: list[dict[str, object]] = []
    for (fingerprint, context), expires_at in entries.items():
        if (
            not _is_clipboard_text_fingerprint(fingerprint)
            or not _valid_clipboard_pending_quarantine_context(context)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, (int, float))
        ):
            continue
        try:
            expires_value = float(expires_at)
        except (OverflowError, TypeError, ValueError):
            continue
        if not math.isfinite(expires_value) or expires_value <= now_value:
            continue
        valid_entries.append(
            {"context": context, "expires_at": expires_value, "sha256": fingerprint.lower()}
        )
    valid_entries.sort(key=lambda entry: float(entry["expires_at"]))
    valid_entries = valid_entries[-MAX_CLIPBOARD_PENDING_QUARANTINES:]
    payload = {"entries": valid_entries}
    try:
        path = _clipboard_pending_quarantine_path()
        current_status, current_identity = _clipboard_pending_quarantine_file_identity(path)
        if current_status == _CLIPBOARD_PENDING_LEDGER_MISSING:
            if not allow_missing or expected_identity is not None:
                return False
        elif (
            current_status != _CLIPBOARD_PENDING_LEDGER_VALID
            or expected_identity is None
            or current_identity != expected_identity
        ):
            return False
        write_text_atomically_without_following_symlinks(
            path,
            json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
            field_name="clipboard pending quarantine",
        )
    except (OSError, RuntimeError, MemoryError, TypeError, ValueError):
        return False
    return True


def _set_clipboard_pending_quarantine(
    text: str,
    method: str,
    *,
    dedupe_context: str | None = None,
) -> bool:
    key = _clipboard_pending_quarantine_key(text, method, dedupe_context)
    if key is None:
        return False
    lock_fd = _acquire_clipboard_pending_quarantine_lock()
    if lock_fd is None:
        return False
    try:
        ledger_status, ledger, ledger_identity = _read_clipboard_pending_quarantine_ledger()
        if ledger_status == _CLIPBOARD_PENDING_LEDGER_INVALID:
            _log_clipboard_pending_quarantine_issue(
                "clipboard_pending_quarantine_untrusted",
                "clipboard pending quarantine is untrusted",
            )
            return False
        now_mono = time.monotonic()
        _prune_clipboard_pending_quarantine(now_mono)
        _CLIPBOARD_PENDING_QUARANTINE[key] = now_mono + MAX_PASTE_TIMEOUT_SECONDS + MAX_EXEC_TIMEOUT_SECONDS
        _prune_clipboard_pending_quarantine(now_mono)
        ledger[key] = time.time() + MAX_PASTE_TIMEOUT_SECONDS + MAX_EXEC_TIMEOUT_SECONDS
        return _write_clipboard_pending_quarantine_ledger(
            ledger,
            expected_identity=ledger_identity,
            allow_missing=ledger_status == _CLIPBOARD_PENDING_LEDGER_MISSING,
            lock_fd=lock_fd,
        )
    finally:
        _release_clipboard_pending_quarantine_lock(lock_fd)


def _clear_clipboard_pending_quarantine(
    text: str,
    method: str,
    *,
    dedupe_context: str | None = None,
) -> bool:
    key = _clipboard_pending_quarantine_key(text, method, dedupe_context)
    if key is None:
        return False
    lock_fd = _acquire_clipboard_pending_quarantine_lock()
    if lock_fd is None:
        return False
    try:
        ledger_status, ledger, ledger_identity = _read_clipboard_pending_quarantine_ledger()
        if ledger_status == _CLIPBOARD_PENDING_LEDGER_INVALID:
            _log_clipboard_pending_quarantine_issue(
                "clipboard_pending_quarantine_untrusted",
                "clipboard pending quarantine is untrusted",
            )
            return False
        _prune_clipboard_pending_quarantine()
        removed = key in _CLIPBOARD_PENDING_QUARANTINE
        if key in ledger:
            ledger.pop(key, None)
            if not _write_clipboard_pending_quarantine_ledger(
                ledger,
                expected_identity=ledger_identity,
                allow_missing=False,
                lock_fd=lock_fd,
            ):
                return False
            _CLIPBOARD_PENDING_QUARANTINE.pop(key, None)
            return True
        if removed:
            _CLIPBOARD_PENDING_QUARANTINE.pop(key, None)
            return True
        return True
    finally:
        _release_clipboard_pending_quarantine_lock(lock_fd)


def _record_clipboard_insertion(text: str, method: str, *, dedupe_context: str | None = None) -> bool:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT
    cleaned = _normalize_clipboard_text(text)
    if not cleaned:
        _LAST_CLIPBOARD_TEXT = cleaned
        _LAST_CLIPBOARD_INSERTION = time.monotonic()
        _LAST_CLIPBOARD_METHOD = method
        _LAST_CLIPBOARD_CONTEXT = dedupe_context
        return True
    now = time.time()
    written = _write_clipboard_dedup_fingerprint_state(
        _clipboard_insertion_fingerprint(cleaned, _clipboard_method_dedupe_context(method, dedupe_context)),
        now,
    )
    if not written:
        return False
    _LAST_CLIPBOARD_TEXT = cleaned
    _LAST_CLIPBOARD_INSERTION = time.monotonic()
    _LAST_CLIPBOARD_METHOD = method
    _LAST_CLIPBOARD_CONTEXT = dedupe_context
    if not _clear_clipboard_pending_quarantine(cleaned, method, dedupe_context=dedupe_context):
        _log_clipboard_pending_quarantine_issue(
            "clipboard_pending_quarantine_clear_failed",
            "clipboard pending quarantine clear failed",
        )
    return True


def _commit_clipboard_insertion(text: str, method: str, *, dedupe_context: str | None = None) -> bool:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT
    cleaned = _normalize_clipboard_text(text)
    if not cleaned:
        if text != "":
            return False
        _LAST_CLIPBOARD_TEXT = cleaned
        _LAST_CLIPBOARD_METHOD = method
        _LAST_CLIPBOARD_INSERTION = time.monotonic()
        _LAST_CLIPBOARD_CONTEXT = dedupe_context
        return True
    now = time.time()
    written = _write_clipboard_dedup_fingerprint_state(
        _clipboard_insertion_fingerprint(cleaned, _clipboard_method_dedupe_context(method, dedupe_context)),
        now,
    )
    if not written:
        return False
    _LAST_CLIPBOARD_TEXT = cleaned
    _LAST_CLIPBOARD_METHOD = method
    _LAST_CLIPBOARD_INSERTION = time.monotonic()
    _LAST_CLIPBOARD_CONTEXT = dedupe_context
    if not _clear_clipboard_pending_quarantine(cleaned, method, dedupe_context=dedupe_context):
        _log_clipboard_pending_quarantine_issue(
            "clipboard_pending_quarantine_clear_failed",
            "clipboard pending quarantine clear failed",
        )
    return True


def _clear_clipboard_insertion_memory() -> None:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT
    _LAST_CLIPBOARD_TEXT = ""
    _LAST_CLIPBOARD_METHOD = None
    _LAST_CLIPBOARD_INSERTION = 0.0
    _LAST_CLIPBOARD_CONTEXT = None


def _restore_clipboard_insertion_snapshot(snapshot: tuple[str, str | None, float, str | None]) -> None:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT
    _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT = snapshot


def _reserve_clipboard_insertion_memory(
    text: str,
    method: str,
    *,
    dedupe_context: str | None = None,
) -> tuple[str, str | None, float, str | None] | None:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT
    cleaned = _normalize_clipboard_text(text)
    if not cleaned and text != "":
        return None
    snapshot = _clipboard_insertion_snapshot()
    insertion_at = time.monotonic()
    _LAST_CLIPBOARD_TEXT = cleaned
    _LAST_CLIPBOARD_METHOD = method
    _LAST_CLIPBOARD_INSERTION = insertion_at
    _LAST_CLIPBOARD_CONTEXT = dedupe_context
    return snapshot


def _add_clipboard_state_cleanup_note(error: BaseException) -> None:
    try:
        error.add_note("clipboard state cleanup failed")
    except BaseException:
        return


def _unlink_clipboard_state_file(path: Path) -> bool:
    caller_primary = sys.exc_info()[1]
    parent_fd: int | None = None
    operation_error: BaseException | None = None
    close_error: BaseException | None = None
    result = False
    try:
        try:
            parent_fd = ensure_directory_without_following_symlinks(
                path.parent,
                field_name="clipboard dedupe state directory",
            )
        except BaseException as exc:
            operation_error = exc
        if operation_error is None:
            try:
                try:
                    current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    result = True
                else:
                    if not stat.S_ISREG(current.st_mode) or getattr(current, "st_nlink", 1) != 1:
                        result = False
                    else:
                        try:
                            latest = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            result = True
                        else:
                            if _same_clipboard_lock_snapshot(latest, current):
                                result = _unlink_clipboard_lock_at(
                                    parent_fd,
                                    path,
                                    expected_stat=latest,
                                )
            except BaseException as exc:
                operation_error = exc
    finally:
        if parent_fd is not None:
            owned_fd = parent_fd
            parent_fd = None
            try:
                os.close(owned_fd)
            except BaseException as exc:
                close_error = exc
                if operation_error is not None:
                    _add_clipboard_state_cleanup_note(operation_error)
                elif caller_primary is not None:
                    _add_clipboard_state_cleanup_note(caller_primary)
                    result = False
                elif isinstance(exc, Exception):
                    result = False

    if caller_primary is not None:
        if operation_error is not None or close_error is not None:
            _add_clipboard_state_cleanup_note(caller_primary)
            return False
        return result
    if operation_error is not None:
        if isinstance(operation_error, Exception):
            return False
        raise operation_error
    if close_error is not None:
        if isinstance(close_error, Exception):
            return False
        raise close_error
    return result


def _clear_clipboard_dedup_state() -> bool:
    try:
        path = _clipboard_dedup_state_path()
    except RuntimeError:
        return False
    return _unlink_clipboard_state_file(path)


def _restore_clipboard_dedup_state(snapshot: tuple[str, float], *, pending: bool = False) -> None:
    primary_error = sys.exc_info()[1]
    restore_failed = False
    try:
        fingerprint, at = snapshot
        if fingerprint:
            if not _write_clipboard_dedup_fingerprint_state(fingerprint, at, pending=pending):
                restore_failed = not _clear_clipboard_dedup_state()
            if not restore_failed:
                return
        else:
            restore_failed = not _clear_clipboard_dedup_state()
    except Exception:
        if primary_error is not None:
            primary_error.add_note("clipboard dedupe state restore failed")
            return
        restore_failed = True
    except BaseException:
        if primary_error is not None:
            primary_error.add_note("clipboard dedupe state restore failed")
            return
        raise
    if restore_failed:
        if primary_error is not None:
            primary_error.add_note("clipboard dedupe state restore failed")
            return
        try:
            log_event(
                "warning",
                "clipboard_dedup_state_restore_failed",
                error="clipboard dedupe state restore failed",
            )
        except Exception:
            restore_failed = True
        raise OutputCleanupError("clipboard dedupe state restore failed") from None


def _clipboard_insertion_snapshot() -> tuple[str, str | None, float, str | None]:
    return _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT


def _validate_text_input(text: str) -> bytes:
    if not isinstance(text, str) or isinstance(text, bool):
        raise OutputError("text must be text")
    if _contains_escaped_null(text):
        raise OutputError("command input contains invalid null byte")
    if len(text) > MAX_INPUT_CHARS:
        raise OutputError(f"command input is too large (max {MAX_INPUT_CHARS} characters)")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise OutputError("command input contains invalid Unicode") from exc
    if len(encoded) > MAX_INPUT_CHARS:
        raise OutputError(f"command input is too large (max {MAX_INPUT_CHARS} bytes)")
    return encoded


class _BoundedOutputCapture:
    def __init__(self, output_file: BinaryIO, max_bytes: int) -> None:
        self._output_file = output_file
        self._max_bytes = max_bytes
        self._captured_bytes = 0
        self.overflowed = False
        self._error: BaseException | None = None
        self._read_fd: int | None = None
        self._write_fd: int | None = None
        try:
            self._read_fd, self._write_fd = os.pipe()
            self._thread = threading.Thread(target=self._drain, name="soc-output-capture", daemon=True)
            self._thread.start()
        except BaseException:
            for fd in (self._read_fd, self._write_fd):
                if fd is None:
                    continue
                try:
                    os.close(fd)
                except BaseException:
                    pass
            self._read_fd = None
            self._write_fd = None
            raise

    def fileno(self) -> int:
        if self._write_fd is None:
            raise ValueError("output capture writer is closed")
        return self._write_fd

    def write(self, payload: bytes) -> int:
        if not isinstance(payload, bytes):
            raise TypeError("output capture payload must be bytes")
        offset = 0
        while offset < len(payload):
            try:
                written = os.write(self.fileno(), payload[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError("bounded output capture write made no progress")
            offset += written
        return len(payload)

    def flush(self) -> None:
        return None

    def close_writer(self) -> None:
        write_fd = self._write_fd
        self._write_fd = None
        if write_fd is not None:
            os.close(write_fd)

    def finish(self) -> None:
        errors: list[BaseException] = []
        try:
            self.close_writer()
        except BaseException as exc:
            errors.append(exc)
        self._thread.join(timeout=1.0)
        if self._thread.is_alive():
            errors.append(OSError("bounded output capture did not finish"))
        if self._error is not None:
            errors.append(self._error)
        if errors:
            raise OSError("bounded output capture failed") from errors[0]

    def _drain(self) -> None:
        read_fd = self._read_fd
        if read_fd is None:
            return
        try:
            while True:
                try:
                    chunk = os.read(read_fd, 64 * 1024)
                except InterruptedError:
                    continue
                if not chunk:
                    break
                remaining = self._max_bytes - self._captured_bytes
                if remaining > 0 and self._error is None:
                    captured = chunk[:remaining]
                    try:
                        offset = 0
                        while offset < len(captured):
                            written = self._output_file.write(captured[offset:])
                            if (
                                not isinstance(written, int)
                                or isinstance(written, bool)
                                or written <= 0
                                or written > len(captured) - offset
                            ):
                                raise OSError("bounded output capture sink made no progress")
                            offset += written
                    except BaseException as exc:
                        self._error = exc
                        self._captured_bytes = self._max_bytes
                    else:
                        self._captured_bytes += len(captured)
                if len(chunk) > max(remaining, 0):
                    self.overflowed = True
            if self._error is None:
                self._output_file.flush()
        except BaseException as exc:
            self._error = exc
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass


def _pipe_target_for_fd(fd: object) -> str | None:
    if isinstance(fd, bool) or not isinstance(fd, int) or fd < 0:
        return None
    try:
        target = os.readlink(f"/proc/self/fd/{fd}")
    except (OSError, ValueError):
        return None
    if target.startswith("pipe:[") and target.endswith("]"):
        return target
    return None


def _pipe_targets_for_process(process: object, *captures: object) -> tuple[str, ...]:
    fds: set[int] = set()
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(process, stream_name, None)
        fileno = getattr(stream, "fileno", None)
        if not callable(fileno):
            continue
        try:
            fd = fileno()
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(fd, int) and not isinstance(fd, bool) and fd >= 0:
            fds.add(fd)
    for capture in captures:
        for fd_name in ("_read_fd", "_write_fd"):
            fd = getattr(capture, fd_name, None)
            if isinstance(fd, int) and not isinstance(fd, bool) and fd >= 0:
                fds.add(fd)
    targets = [target for fd in fds if (target := _pipe_target_for_fd(fd)) is not None]
    return tuple(sorted(targets))


def _finish_bounded_output_captures(
    *captures: _BoundedOutputCapture | None,
) -> None:
    errors: list[BaseException] = []
    for capture in captures:
        if capture is None:
            continue
        try:
            capture.finish()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise OSError("bounded output capture failed") from errors[0]


def _run_with_input(
    argv: list[str] | tuple[str, ...],
    text: str,
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    max_output_chars: int | None = None,
    resolved_command: str | None = None,
) -> None:
    if not isinstance(argv, (list, tuple)):
        raise OutputError("argv must be a sequence")
    if not all(isinstance(arg, str) for arg in argv):
        raise OutputError("command arguments must be text")
    if not argv:
        raise OutputError("empty command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise OutputError("timeout must be an integer")
    if timeout <= 0:
        raise OutputError("timeout must be positive")
    if max_output_chars is None:
        max_output_chars = MAX_OUTPUT_CHARS
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool):
        raise OutputError("max_output_chars must be an integer")
    if max_output_chars < 0:
        raise OutputError("max_output_chars must be non-negative")
    if max_output_chars > MAX_OUTPUT_CHARS:
        raise OutputError(f"max_output_chars must not exceed {MAX_OUTPUT_CHARS}")

    command = argv[0].strip()
    if not command:
        raise OutputError("command is empty")
    if _contains_escaped_null(command) or any(_contains_escaped_null(arg) for arg in argv[1:]):
        raise OutputError("command argument contains invalid null byte")
    if _contains_http_header_control_chars(command) or any(
        _contains_http_header_control_chars(arg) for arg in argv[1:]
    ):
        raise OutputError("command argument contains invalid control character")
    runtime_command = resolved_command or _command_path(command)

    input_bytes = _validate_text_input(text)

    stdout_file = None
    stderr_file = None
    stdout_capture: _BoundedOutputCapture | None = None
    stderr_capture: _BoundedOutputCapture | None = None
    primary_error: BaseException | None = None
    post_spawn_error: Exception | None = None
    post_spawn_error_kind: str | None = None
    post_spawn_phase = "pre_spawn"
    cleanup_unconfirmed = False
    process_spawned = False
    try:
        try:
            stdout_file = tempfile.TemporaryFile()
            stderr_file = tempfile.TemporaryFile()
            stdout_capture = _BoundedOutputCapture(stdout_file, max_output_chars)
            stderr_capture = _BoundedOutputCapture(stderr_file, max_output_chars)
        except (OSError, ValueError) as exc:
            try:
                _finish_bounded_output_captures(stdout_capture, stderr_capture)
            except BaseException:
                exc.add_note(f"{command} output capture cleanup failed")
            raise OutputError(f"{command} failed to prepare output capture") from None
        try:
            proc = subprocess.Popen(  # type: ignore[call-overload]  # nosec B603
                [runtime_command, *argv[1:]],
                stdin=subprocess.PIPE,
                stdout=stdout_capture,
                stderr=stderr_capture,
                shell=False,
                env=_filtered_environment(),
                start_new_session=True,
            )
            process_spawned = True
            setattr(proc, "_soc_process_identity", _clipboard_lock_identity_for_pid(proc.pid) or "")
            setattr(
                proc,
                "_soc_output_pipe_targets",
                _pipe_targets_for_process(proc, stdout_capture, stderr_capture),
            )
            proc.communicate(input=input_bytes, timeout=timeout)
        except FileNotFoundError as exc:
            if "proc" in locals():
                cleanup_unconfirmed = not _reap_output_process_after_failure(proc)
            post_spawn_error = exc
            post_spawn_error_kind = "unavailable"
            post_spawn_phase = "post_spawn_confirmed" if process_spawned else "pre_spawn"
        except subprocess.TimeoutExpired as exc:
            cleanup_unconfirmed = False
            if "proc" in locals():
                try:
                    terminated = _reap_timed_out_output_process(proc)
                except BaseException:
                    exc.add_note(f"{command} process cleanup failed")
                    cleanup_unconfirmed = True
                else:
                    if not terminated:
                        exc.add_note(f"{command} process cleanup was not confirmed")
                        cleanup_unconfirmed = True
            if cleanup_unconfirmed:
                raise OutputCleanupError(
                    f"{command} timed out; process cleanup was not confirmed"
                ) from exc
            raise OutputError(f"{command} timed out after {timeout}s") from exc
        except (OSError, ValueError) as exc:
            if "proc" in locals():
                cleanup_unconfirmed = not _reap_output_process_after_failure(proc)
            post_spawn_error = exc
            post_spawn_error_kind = "execute"
            post_spawn_phase = "post_spawn_confirmed" if process_spawned else "pre_spawn"
        except Exception as exc:
            if "proc" in locals():
                cleanup_unconfirmed = not _reap_output_process_after_failure(proc)
            post_spawn_error = exc
            post_spawn_error_kind = "propagate"
            post_spawn_phase = "post_spawn_confirmed" if process_spawned else "pre_spawn"
        except BaseException as exc:
            if "proc" in locals():
                try:
                    terminated = _reap_timed_out_output_process(proc)
                    if not terminated:
                        exc.add_note(f"{command} process cleanup was not confirmed")
                except BaseException:
                    exc.add_note(f"{command} process cleanup failed")
            raise

        if cleanup_unconfirmed:
            raise OutputCleanupError(f"{command} process cleanup was not confirmed") from None
        if post_spawn_error is not None:
            if post_spawn_error_kind == "unavailable":
                raise _OutputProcessError(
                    f"{command} is not available",
                    phase=post_spawn_phase,
                ) from None
            if post_spawn_error_kind == "execute":
                raise _OutputProcessError(
                    f"{command} failed to execute",
                    phase=post_spawn_phase,
                ) from None
            raise post_spawn_error

        try:
            _finish_bounded_output_captures_after_process(
                proc,
                stdout_capture,
                stderr_capture,
                process_name=command,
            )
        except (OSError, ValueError) as exc:
            raise OutputError(f"{command} output capture failed") from exc
        if stdout_capture is not None and stdout_capture.overflowed:
            raise OutputError(f"{command} produced too much output")
        if stderr_capture is not None and stderr_capture.overflowed:
            raise OutputError(f"{command} produced too much error output")
        try:
            stdout_size = _filesize(stdout_file)
            stderr_size = _filesize(stderr_file)
        except (OSError, ValueError) as exc:
            raise OutputError(f"{command} output could not be read") from exc
        if stdout_size > max_output_chars:
            raise OutputError(f"{command} produced too much output")
        if stderr_size > max_output_chars:
            raise OutputError(f"{command} produced too much error output")

        if proc.returncode != 0:
            raise OutputError(f"{command} failed with exit code {proc.returncode}")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        try:
            _finish_bounded_output_captures(stdout_capture, stderr_capture)
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(f"{command} output capture cleanup failed")
            else:
                cleanup_errors.append(cleanup_error)
        for capture_file in (stderr_file, stdout_file):
            if capture_file is None:
                continue
            try:
                capture_file.close()
            except BaseException as cleanup_error:
                if primary_error is not None:
                    primary_error.add_note(f"{command} output cleanup failed")
                else:
                    cleanup_errors.append(cleanup_error)
        if cleanup_errors:
            first_cleanup_error = cleanup_errors[0]
            for additional_error in cleanup_errors[1:]:
                first_cleanup_error.add_note(f"{command} output cleanup failed")
            raise OutputError(f"{command} output cleanup failed") from None


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise OutputError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise OutputError("command is empty")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise OutputError("command must be a bare command name without path separators")
    resolved = _which(command_name)
    if not resolved:
        raise OutputError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


def _bounded_command_output_bytes(
    handle: BinaryIO,
    completed_output: bytes | None,
    *,
    max_output_chars: int = MAX_OUTPUT_CHARS,
) -> bytes | None:
    size = _filesize(handle)
    if size > max_output_chars:
        return None
    handle.seek(0)
    output = handle.read(max_output_chars + 1)
    if len(output) > max_output_chars:
        return None
    if output or completed_output is None:
        return output
    if len(completed_output) > max_output_chars:
        return None
    return completed_output


def _same_session_process_group_ids(session_id: int) -> set[int] | None:
    if not isinstance(session_id, int) or isinstance(session_id, bool) or session_id <= 0:
        return None
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    process_group_ids: set[int] = set()
    scan_incomplete = False
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        try:
            raw = proc_entry.joinpath("stat").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_group = int(fields[2])
            member_session_id = int(fields[3])
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        if member_session_id != session_id:
            continue
        if process_group <= 0:
            scan_incomplete = True
            continue
        process_group_ids.add(process_group)
    if scan_incomplete:
        return None
    return process_group_ids


def _process_tree_descendant_identities(process_id: int) -> dict[int, str] | None:
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        return None
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    children_by_parent: dict[int, set[int]] = {}
    process_identities: dict[int, str] = {}
    scan_incomplete = False
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        member_id = int(proc_entry.name)
        try:
            raw = proc_entry.joinpath("stat").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            parent_id = int(fields[1])
            start_time = fields[19]
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        children_by_parent.setdefault(parent_id, set()).add(member_id)
        process_identities[member_id] = start_time
    if scan_incomplete:
        return None
    descendants: dict[int, str] = {}
    pending = [process_id]
    while pending:
        parent_id = pending.pop()
        for child_id in children_by_parent.get(parent_id, ()):
            if child_id in descendants:
                continue
            descendants[child_id] = process_identities[child_id]
            pending.append(child_id)
    return descendants


def _process_pipe_holder_identities(process: subprocess.Popen[bytes]) -> dict[int, str] | None:
    pipe_inodes = set(_pipe_targets_for_process(process))
    stored_targets = getattr(process, "_soc_output_pipe_targets", ())
    if isinstance(stored_targets, (list, tuple, set)):
        pipe_inodes.update(
            target
            for target in stored_targets
            if isinstance(target, str) and target.startswith("pipe:[") and target.endswith("]")
        )
    if not pipe_inodes:
        return {}
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    holders: dict[int, str] = {}
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        process_id = int(proc_entry.name)
        if process_id == os.getpid():
            continue
        if current_uid is not None:
            try:
                if proc_entry.stat().st_uid != current_uid:
                    continue
            except FileNotFoundError:
                continue
            except OSError:
                continue
        try:
            raw = proc_entry.joinpath("stat").read_text(encoding="ascii").strip()
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            start_time = fields[19]
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError, IndexError, ValueError):
            continue
        try:
            fd_entries = tuple(proc_entry.joinpath("fd").iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            continue
        for fd_entry in fd_entries:
            try:
                target = os.readlink(fd_entry)
            except FileNotFoundError:
                continue
            except OSError:
                continue
            if target in pipe_inodes:
                holders[process_id] = start_time
                break
    return holders


def _output_process_identity_is_current(process: subprocess.Popen[bytes]) -> bool:
    expected_identity = vars(process).get("_soc_process_identity")
    if expected_identity is None:
        return True
    if not isinstance(expected_identity, str) or not expected_identity:
        return False
    current_identity = _clipboard_lock_identity_for_pid(process.pid)
    if current_identity is None:
        try:
            os.stat(f"/proc/{process.pid}")
        except FileNotFoundError:
            return process.returncode is not None
        except OSError:
            return False
        return False
    return current_identity == expected_identity


def _process_tree_has_live_processes(process_identities: dict[int, str]) -> bool | None:
    for process_id, expected_start_time in process_identities.items():
        try:
            raw = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            return None
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_state = fields[0]
            start_time = fields[19]
        except (IndexError, ValueError):
            return None
        if start_time != expected_start_time:
            continue
        if process_state not in {"Z", "X", "x"}:
            return True
    return False


def _kill_output_process_tree(process_identities: dict[int, str]) -> bool:
    cleanup_incomplete = False
    for process_id, expected_start_time in sorted(process_identities.items()):
        try:
            raw = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            cleanup_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_state = fields[0]
            start_time = fields[19]
        except (IndexError, ValueError):
            cleanup_incomplete = True
            continue
        if start_time != expected_start_time or process_state in {"Z", "X", "x"}:
            continue
        try:
            os.kill(process_id, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except (OSError, ValueError):
            cleanup_incomplete = True
    return not cleanup_incomplete


def _wait_for_output_process_tree_stop(process_identities: dict[int, str], timeout_seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        tree_live = _process_tree_has_live_processes(process_identities)
        if tree_live is False:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _process_group_has_live_descendants(process_group_id: int) -> bool | None:
    if not isinstance(process_group_id, int) or isinstance(process_group_id, bool) or process_group_id <= 0:
        return None
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    scan_incomplete = False
    group_live = False
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        process_id = int(proc_entry.name)
        try:
            raw = proc_entry.joinpath("stat").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_state = fields[0]
            process_group = int(fields[2])
            session_id = int(fields[3])
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        if session_id != process_group_id:
            continue
        if process_group != process_group_id:
            if process_state not in {"Z", "X", "x"}:
                group_live = True
            continue
        if process_id != process_group_id and process_state not in {"Z", "X", "x"}:
            group_live = True
    if scan_incomplete:
        return None
    return group_live


def _wait_for_output_process_group_stop(process_group_id: int, timeout_seconds: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while True:
        descendants = _process_group_has_live_descendants(process_group_id)
        if descendants is False:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _terminate_output_process_group(process: subprocess.Popen[bytes]) -> bool:
    if not process or not isinstance(process.pid, int) or process.pid <= 0:
        return False
    if not _output_process_identity_is_current(process):
        return False
    pipe_holders: dict[int, str] = {}
    try:
        descendants = _process_group_has_live_descendants(process.pid)
        if descendants is None:
            return False
        if process.returncode is not None:
            pipe_holders = _process_pipe_holder_identities(process)
            if pipe_holders is None:
                return False
            if descendants is not True:
                if not pipe_holders:
                    return descendants is False
            try:
                raw = Path(f"/proc/{process.pid}/stat").read_text(encoding="ascii").strip()
                close = raw.rindex(")")
                process_state = raw[close + 2 :].split()[0]
            except FileNotFoundError:
                process_state = "gone"
            except (OSError, IndexError, ValueError):
                return False
            if process_state not in {"Z", "X", "x", "gone"}:
                return False
    except (OSError, ValueError):
        return False
    session_group_ids = _same_session_process_group_ids(process.pid)
    if session_group_ids is None:
        return False
    process_tree = _process_tree_descendant_identities(process.pid)
    process_tree_scan_incomplete = process_tree is None
    if process_tree is None:
        process_tree = {}
    process_tree.update(pipe_holders)
    if not _output_process_identity_is_current(process):
        return False
    process_tree_cleanup = _kill_output_process_tree(process_tree)
    if not _output_process_identity_is_current(process):
        return False
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except (OSError, ValueError):
        try:
            process.kill()
        except (OSError, ValueError):
            return False
        return False
    for process_group_id in sorted(session_group_ids):
        if process_group_id == process.pid:
            continue
        if not _output_process_identity_is_current(process):
            return False
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except ProcessLookupError:
            continue
        except (OSError, ValueError):
            return False
    process_tree_stopped = _wait_for_output_process_tree_stop(process_tree)
    process_group_stopped = _wait_for_output_process_group_stop(process.pid)
    return (
        process_tree_cleanup
        and not process_tree_scan_incomplete
        and process_tree_stopped
        and process_group_stopped
    )


def _reap_timed_out_output_process(process: subprocess.Popen[bytes]) -> bool:
    terminated = _terminate_output_process_group(process)
    try:
        process.communicate(timeout=1)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False
    return terminated


def _reap_output_process_after_failure(process: subprocess.Popen[bytes]) -> bool:
    try:
        return bool(_reap_timed_out_output_process(process))
    except Exception:
        return False


def _finish_bounded_output_captures_after_process(
    process: subprocess.Popen[bytes],
    stdout_capture: _BoundedOutputCapture | None,
    stderr_capture: _BoundedOutputCapture | None,
    *,
    process_name: str,
) -> None:
    capture_error: BaseException | None = None
    cleanup_unconfirmed = False
    try:
        _finish_bounded_output_captures(stdout_capture, stderr_capture)
    except BaseException as exc:
        capture_error = exc
        try:
            terminated = _reap_output_process_after_failure(process)
        except BaseException:
            raise
        if not terminated:
            cleanup_unconfirmed = True
            capture_error.add_note(f"{process_name} process cleanup was incomplete")
        try:
            _finish_bounded_output_captures(stdout_capture, stderr_capture)
        except BaseException:
            capture_error.add_note(f"{process_name} output capture cleanup failed")
    if cleanup_unconfirmed and isinstance(capture_error, Exception):
        raise OutputCleanupError(f"{process_name} process cleanup was not confirmed") from None
    if capture_error is not None:
        raise capture_error


def _run_bounded_stdout_command(
    argv: list[str] | tuple[str, ...],
    *,
    timeout: int,
    runtime_command: str,
) -> tuple[int, bytes, bytes] | None:
    stdout_file = None
    stderr_file = None
    stdout_capture: _BoundedOutputCapture | None = None
    stderr_capture: _BoundedOutputCapture | None = None
    result: tuple[int, bytes, bytes] | None = None
    primary_error: BaseException | None = None
    cleanup_failed = False
    cleanup_unconfirmed = False
    unhandled_error: Exception | None = None
    try:
        stdout_file = tempfile.TemporaryFile()
        stderr_file = tempfile.TemporaryFile()
        stdout_capture = _BoundedOutputCapture(stdout_file, MAX_OUTPUT_CHARS)
        stderr_capture = _BoundedOutputCapture(stderr_file, MAX_OUTPUT_CHARS)
        try:
            proc = subprocess.Popen(  # type: ignore[call-overload]  # nosec B603
                [runtime_command, *argv[1:]],
                stdin=subprocess.PIPE,
                stdout=stdout_capture,
                stderr=stderr_capture,
                shell=False,
                env=_filtered_environment(),
                start_new_session=True,
            )
            setattr(proc, "_soc_process_identity", _clipboard_lock_identity_for_pid(proc.pid) or "")
            setattr(
                proc,
                "_soc_output_pipe_targets",
                _pipe_targets_for_process(proc, stdout_capture, stderr_capture),
            )
            completed_stdout, completed_stderr = proc.communicate(input=b"", timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if "proc" in locals():
                try:
                    terminated = _reap_timed_out_output_process(proc)
                except BaseException:
                    exc.add_note("bounded command process cleanup failed")
                    cleanup_unconfirmed = True
                else:
                    if not terminated:
                        exc.add_note("bounded command process cleanup was not confirmed")
                        cleanup_unconfirmed = True
            primary_error = exc
        except (FileNotFoundError, OSError, ValueError) as exc:
            if "proc" in locals():
                cleanup_unconfirmed = not _reap_output_process_after_failure(proc)
            primary_error = exc
        except Exception as exc:
            if "proc" in locals():
                cleanup_unconfirmed = not _reap_output_process_after_failure(proc)
            primary_error = exc
            unhandled_error = exc
        except BaseException as exc:
            if "proc" in locals():
                try:
                    terminated = _reap_timed_out_output_process(proc)
                    if not terminated:
                        exc.add_note("bounded command process cleanup was not confirmed")
                except BaseException:
                    exc.add_note("bounded command process cleanup failed")
            primary_error = exc
            raise
        else:
            try:
                _finish_bounded_output_captures_after_process(
                    proc,
                    stdout_capture,
                    stderr_capture,
                    process_name="bounded command",
                )
            except (OSError, ValueError) as exc:
                primary_error = exc
            else:
                if stdout_capture is not None and stdout_capture.overflowed:
                    primary_error = OutputError("bounded command produced too much output")
                elif stderr_capture is not None and stderr_capture.overflowed:
                    primary_error = OutputError("bounded command produced too much error output")
            if primary_error is None:
                try:
                    output = _bounded_command_output_bytes(stdout_file, completed_stdout)
                    error_output = _bounded_command_output_bytes(stderr_file, completed_stderr)
                except (OSError, ValueError) as exc:
                    primary_error = exc
                else:
                    if output is not None and error_output is not None:
                        result = proc.returncode, output, error_output
    except (OSError, ValueError) as exc:
        primary_error = exc
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            _finish_bounded_output_captures(stdout_capture, stderr_capture)
        except BaseException:
            cleanup_failed = True
            if primary_error is not None:
                primary_error.add_note("bounded command output capture cleanup failed")
        for capture_file in (stderr_file, stdout_file):
            if capture_file is None:
                continue
            try:
                capture_file.close()
            except BaseException:
                cleanup_failed = True
                if primary_error is not None:
                    primary_error.add_note("bounded command output cleanup failed")
    if cleanup_unconfirmed:
        raise OutputCleanupError("bounded command process cleanup was not confirmed") from None
    if unhandled_error is not None:
        raise unhandled_error
    if cleanup_failed:
        return None
    return result


def _run_stdout(
    argv: list[str] | tuple[str, ...],
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    resolved_command: str | None = None,
) -> str:
    if not isinstance(argv, (list, tuple)):
        raise OutputError("argv must be a sequence")
    if not all(isinstance(item, str) for item in argv):
        raise OutputError("command arguments must be text")
    if not argv:
        raise OutputError("empty command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise OutputError("timeout must be an integer")
    if timeout <= 0:
        raise OutputError("timeout must be positive")

    command = argv[0].strip()
    if not command:
        raise OutputError("command is empty")
    if _contains_escaped_null(command) or any(_contains_escaped_null(arg) for arg in argv[1:]):
        raise OutputError("command argument contains invalid null byte")
    if _contains_http_header_control_chars(command) or any(
        _contains_http_header_control_chars(arg) for arg in argv[1:]
    ):
        raise OutputError("command argument contains invalid control character")

    runtime_command = resolved_command or _command_path(command)
    result = _run_bounded_stdout_command(argv, timeout=timeout, runtime_command=runtime_command)
    if result is None:
        return ""
    returncode, output, error_output = result
    if returncode != 0:
        return ""
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if _contains_escaped_null(text):
        return ""
    return text.strip()


def _run_stdout_raw(
    argv: list[str] | tuple[str, ...],
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    resolved_command: str | None = None,
) -> str | None:
    if not isinstance(argv, (list, tuple)):
        raise OutputError("argv must be a sequence")
    if not all(isinstance(item, str) for item in argv):
        raise OutputError("command arguments must be text")
    if not argv:
        raise OutputError("empty command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise OutputError("timeout must be an integer")
    if timeout <= 0:
        raise OutputError("timeout must be positive")
    command = argv[0].strip()
    if not command:
        raise OutputError("command is empty")
    if _contains_escaped_null(command) or any(_contains_escaped_null(arg) for arg in argv[1:]):
        raise OutputError("command argument contains invalid null byte")
    if _contains_http_header_control_chars(command) or any(
        _contains_http_header_control_chars(arg) for arg in argv[1:]
    ):
        raise OutputError("command argument contains invalid control character")
    runtime_command = resolved_command or _command_path(command)
    result = _run_bounded_stdout_command(argv, timeout=timeout, runtime_command=runtime_command)
    if result is None:
        return None
    returncode, output, error_output = result
    if returncode != 0:
        return None
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if _contains_escaped_null(text):
        return None
    return text


def _active_x_window_snapshot(*, xdotool_command: str | None = None) -> tuple[str, str, str] | None:
    runtime_command = xdotool_command or _which("xdotool")
    if not runtime_command:
        return None
    window_id = _run_stdout(
        ["xdotool", "getactivewindow"],
        timeout=MAX_PASTE_TIMEOUT_SECONDS,
        resolved_command=runtime_command,
    )
    if not window_id or not window_id.isdigit() or len(window_id) > 64:
        return None
    window_title = _run_stdout(
        ["xdotool", "getwindowname", window_id],
        timeout=MAX_PASTE_TIMEOUT_SECONDS,
        resolved_command=runtime_command,
    )
    window_class = _run_stdout(
        ["xdotool", "getwindowclassname", window_id],
        timeout=MAX_PASTE_TIMEOUT_SECONDS,
        resolved_command=runtime_command,
    )
    if not isinstance(window_class, str) or not window_class:
        return None
    return window_id, window_title, window_class


def _active_x_window_matches_snapshot(
    snapshot: tuple[str, str, str] | None,
    *,
    xdotool_command: str | None = None,
) -> bool:
    if snapshot is None:
        return True
    current = _active_x_window_snapshot(xdotool_command=xdotool_command)
    if current is None:
        return False
    expected_id, expected_title, expected_class = snapshot
    current_id, current_title, current_class = current
    if current_id != expected_id:
        return False
    if not isinstance(expected_class, str) or not expected_class:
        return False
    if not isinstance(current_class, str) or current_class != expected_class:
        return False
    if isinstance(expected_title, str) and expected_title and current_title != expected_title:
        return False
    return True


def _paste_key_for_window_snapshot(snapshot: tuple[str, str, str] | None) -> str:
    if snapshot is None:
        return "ctrl+v"
    _window_id, window_title, window_class = snapshot
    return "ctrl+shift+v" if _looks_like_terminal(window_class) or _looks_like_terminal(window_title) else "ctrl+v"


def _looks_like_terminal(value: str) -> bool:
    normalized = str(value or "").lower()
    return any(marker in normalized for marker in TERMINAL_WINDOW_MARKERS)


def _active_window_paste_key(*, xdotool_available: bool | None = None, xdotool_command: str | None = None) -> str:
    if xdotool_available is None:
        if xdotool_command is None:
            xdotool_command = _which("xdotool")
        xdotool_available = bool(xdotool_command)
    if not xdotool_available:
        return "ctrl+v"
    runtime_command = xdotool_command or _command_path("xdotool")
    return _paste_key_for_window_snapshot(_active_x_window_snapshot(xdotool_command=runtime_command))


def set_clipboard(text: str, *, allowed_helpers: tuple[str, ...] | None = None) -> str:
    if not isinstance(text, str) or isinstance(text, bool):
        raise OutputError("text must be text")
    _validate_text_input(text)
    last_error: OutputError | None = None
    candidates = (
        ("xclip", ["xclip", "-selection", "clipboard"]),
        ("xsel", ["xsel", "--clipboard", "--input"]),
        ("wl-copy", ["wl-copy"]),
    )
    for helper, command in candidates:
        if allowed_helpers is not None and helper not in allowed_helpers:
            continue
        resolved = _which(helper)
        if not resolved:
            continue
        try:
            _run_with_input(command, text, resolved_command=resolved)
            return helper
        except OutputCleanupError:
            raise
        except OutputError as exc:
            last_error = exc
    if last_error is not None:
        if allowed_helpers is not None:
            raise OutputError("no compatible clipboard helper succeeded for automatic paste; install xclip or xsel") from last_error
        raise OutputError("no clipboard helper succeeded; install xclip, xsel, or wl-clipboard") from last_error
    if allowed_helpers is not None:
        raise OutputError("no X11 clipboard helper found for automatic paste; install xclip or xsel")
    raise OutputError("no clipboard helper found; install xclip, xsel, or wl-clipboard")


def _clipboard_read_candidates(*, targets: bool = False) -> tuple[tuple[list[str], str], ...]:
    if targets:
        candidates = (
            ("xclip", ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-out"]),
            ("xsel", ["xsel", "--clipboard", "--output", "--target", "TARGETS"]),
            ("wl-paste", ["wl-paste", "--list-types"]),
        )
    else:
        candidates = (
            ("xclip", ["xclip", "-selection", "clipboard", "-out"]),
            ("xsel", ["xsel", "--clipboard", "--output"]),
            ("wl-paste", ["wl-paste"]),
        )
    available: list[tuple[list[str], str]] = []
    for helper, command in candidates:
        resolved = _which(helper)
        if resolved:
            available.append((command, resolved))
    return tuple(available)


def _read_text_clipboard() -> str | None:
    for command, resolved in _clipboard_read_candidates():
        text = _run_stdout_raw(command, resolved_command=resolved)
        if text is not None:
            return text.strip() or None
    return None


def _read_text_clipboard_snapshot() -> tuple[bool, str]:
    for command, resolved in _clipboard_read_candidates():
        text = _run_stdout_raw(command, resolved_command=resolved)
        if text is not None:
            return True, text
    return False, ""


def _clipboard_targets_contain_non_text_payload(targets: str, *, empty_is_non_text: bool = True) -> bool:
    if not isinstance(empty_is_non_text, bool):
        raise OutputError("empty_is_non_text must be a boolean")
    ignored = {"targets", "multiple", "timestamp", "save_targets"}
    known_text_targets = {
        "compound_text",
        "text",
        "string",
        "utf8_string",
    }
    non_text_text_targets = {"text/html", "text/rtf", "text/uri-list", "text/x-moz-url"}
    saw_text_target = False
    saw_target = False
    for line in str(targets or "").splitlines():
        raw_target = line.strip().lower()
        target = raw_target.split(";", 1)[0]
        if not target or target in ignored:
            continue
        saw_target = True
        if target in non_text_text_targets:
            return True
        if target in known_text_targets or target.startswith("text/"):
            saw_text_target = True
            continue
        return True
    return empty_is_non_text if not saw_target else not saw_text_target


def _clipboard_still_contains_inserted_text(text: str) -> bool:
    available, current_text = _read_text_clipboard_snapshot()
    return available and current_text == text


def _clipboard_has_non_text_payload(
    *, unknown_is_non_text: bool = True, empty_is_non_text: bool = True
) -> bool:
    if not isinstance(unknown_is_non_text, bool) or not isinstance(empty_is_non_text, bool):
        raise OutputError("clipboard payload policy must be boolean")
    for command, resolved in _clipboard_read_candidates(targets=True):
        targets = _run_stdout_raw(command, resolved_command=resolved)
        if targets is not None:
            return _clipboard_targets_contain_non_text_payload(targets, empty_is_non_text=empty_is_non_text)
    return unknown_is_non_text


def _clipboard_snapshot_is_verified_empty(snapshot_available: bool, snapshot_text: str) -> bool:
    return (
        snapshot_available
        and snapshot_text == ""
        and not _clipboard_has_non_text_payload(empty_is_non_text=False)
    )


def _assert_clipboard_text_snapshot_unchanged(snapshot_available: bool, snapshot_text: str) -> None:
    if _clipboard_has_non_text_payload() and not _clipboard_snapshot_is_verified_empty(
        snapshot_available, snapshot_text
    ):
        raise OutputError("refusing to overwrite non-text clipboard for automatic paste")
    current_available, current_text = _read_text_clipboard_snapshot()
    if not current_available:
        raise OutputError("refusing automatic paste without readable text clipboard snapshot")
    if current_available != snapshot_available or current_text != snapshot_text:
        raise OutputError("clipboard changed before automatic paste")


def _restore_clipboard_snapshot_after_failed_paste(
    inserted_text: str,
    snapshot_available: bool,
    snapshot_text: str,
    *,
    allowed_helpers: tuple[str, ...] | None = None,
) -> bool:
    try:
        if not snapshot_available:
            return True
        if not _clipboard_still_contains_inserted_text(inserted_text):
            return True
        if _clipboard_has_non_text_payload():
            return True
        set_clipboard(snapshot_text, allowed_helpers=allowed_helpers)
        return True
    except Exception:
        try:
            log_event(
                "warning",
                "clipboard_restore_after_failed_automatic_paste_failed",
                error="clipboard restore after failed automatic paste failed",
            )
        except Exception:
            return False
        return False
    except BaseException:
        raise


def paste_from_clipboard(expected_window_snapshot: tuple[str, str, str] | None = None) -> None:
    if expected_window_snapshot is None:
        raise PasteNotAttemptedError("refusing automatic paste without verifiable active window")
    xdotool_error: OutputError | None = None
    xdotool = _which("xdotool")
    if xdotool:
        if expected_window_snapshot is not None and not _active_x_window_matches_snapshot(
            expected_window_snapshot,
            xdotool_command=xdotool,
        ):
            raise PasteNotAttemptedError("active window changed before automatic paste")
        try:
            paste_key = _paste_key_for_window_snapshot(
                expected_window_snapshot or _active_x_window_snapshot(xdotool_command=xdotool)
            )
        except OutputError as exc:
            xdotool_error = exc
        else:
            if expected_window_snapshot is not None and not _active_x_window_matches_snapshot(
                expected_window_snapshot,
                xdotool_command=xdotool,
            ):
                raise PasteNotAttemptedError("active window changed before automatic paste")
            try:
                _run_with_input(
                    ["xdotool", "key", "--clearmodifiers", paste_key],
                    "",
                    timeout=MAX_PASTE_TIMEOUT_SECONDS,
                    resolved_command=xdotool,
                )
            except _OutputProcessError as exc:
                if exc.phase == "pre_spawn":
                    raise PasteNotAttemptedError(str(exc)) from None
                raise
            except OutputError:
                raise
            return
    wtype = _which("wtype")
    if wtype and xdotool_error is None:
        raise PasteNotAttemptedError("refusing automatic paste without verifiable active window")
    if xdotool_error is not None:
        raise PasteNotAttemptedError(str(xdotool_error)) from xdotool_error
    raise PasteNotAttemptedError("no automatic paste helper found; install xdotool")


def _clipboard_paste_helper_available() -> bool:
    return bool(_which("xdotool"))


def _clipboard_paste_writer_available() -> bool:
    return bool(_which("xclip") or _which("xsel"))


def type_text(
    text: str,
    delay_ms: int,
    expected_window_snapshot: tuple[str, str, str] | None = None,
    *,
    xdotool_command: str | None = None,
) -> None:
    xdotool = xdotool_command or _which("xdotool")
    if not xdotool:
        raise OutputError("xdotool is required for direct typing on Cinnamon X11")
    if not isinstance(delay_ms, int) or isinstance(delay_ms, bool):
        raise OutputError("typing delay must be an integer")
    _validate_text_input(text)
    if delay_ms < 0:
        delay_ms = 0
    if delay_ms > MAX_TYPE_DELAY_MS:
        raise OutputError(f"typing delay must be at most {MAX_TYPE_DELAY_MS}")
    if expected_window_snapshot is None:
        raise OutputError("refusing direct typing without verifiable active window")
    if not _active_x_window_matches_snapshot(expected_window_snapshot, xdotool_command=xdotool):
        raise OutputError("active window changed before direct typing")
    try:
        _run_with_input(
            ["xdotool", "type", "--clearmodifiers", "--delay", str(max(delay_ms, 0)), "--file", "/dev/stdin"],
            text,
            timeout=MAX_TYPE_TIMEOUT_SECONDS,
            resolved_command=xdotool,
        )
    except OSError as exc:
        raise OutputError("failed to prepare direct typing input") from exc


def _clipboard_dedup_state_is_untrusted() -> bool:
    trusted, _snapshot = _read_trusted_clipboard_dedup_state()
    return not trusted


def _should_skip_clipboard_duplicate(
    text: str,
    method: str,
    *,
    persistent_snapshot: tuple[str, float] | None = None,
    persistent_state_trusted: bool | None = None,
    pending_state: bool | None = None,
    dedupe_context: str | None = None,
) -> bool:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION, _LAST_CLIPBOARD_CONTEXT
    if not isinstance(text, str) or isinstance(text, bool):
        raise OutputError("text must be text")
    if method not in {"clipboard", "clipboard-paste"}:
        return False
    cleaned = _normalize_clipboard_text(text)
    if not cleaned:
        return False
    fingerprint = _clipboard_insertion_fingerprint(cleaned, _clipboard_method_dedupe_context(method, dedupe_context))
    now = time.monotonic()
    _prune_clipboard_pending_quarantine(now)
    quarantine_key = (fingerprint, dedupe_context)
    quarantine_until = _CLIPBOARD_PENDING_QUARANTINE.get(quarantine_key)
    if quarantine_until is not None and now <= quarantine_until:
        return True
    ledger_status, ledger, _ledger_identity = _read_clipboard_pending_quarantine_ledger()
    if ledger_status == _CLIPBOARD_PENDING_LEDGER_INVALID:
        _log_clipboard_pending_quarantine_issue(
            "clipboard_pending_quarantine_untrusted",
            "clipboard pending quarantine is untrusted",
        )
        return True
    now_wall = time.time()
    ledger_until = ledger.get(quarantine_key)
    if ledger_until is not None and now_wall <= ledger_until:
        return True
    if persistent_snapshot is None or persistent_state_trusted is None:
        persistent_state_trusted, persistent_snapshot = _read_trusted_clipboard_dedup_state()
    if not persistent_state_trusted:
        return False
    cached_fingerprint, cached_at = persistent_snapshot
    fingerprint_matches = fingerprint == cached_fingerprint
    if pending_state:
        return fingerprint_matches and 0 <= (now_wall - cached_at) <= MAX_PASTE_TIMEOUT_SECONDS
    if fingerprint_matches and 0 <= (now_wall - cached_at) <= MAX_DUPLICATE_TEXT_SECONDS:
        return True
    if (
        cleaned == _LAST_CLIPBOARD_TEXT
        and method == _LAST_CLIPBOARD_METHOD
        and (dedupe_context == _LAST_CLIPBOARD_CONTEXT or (dedupe_context is None and _LAST_CLIPBOARD_CONTEXT is None))
        and (now - _LAST_CLIPBOARD_INSERTION) <= MAX_DUPLICATE_TEXT_SECONDS
    ):
        return True
    return False


def _should_skip_clipboard_memory_duplicate(text: str, method: str, *, dedupe_context: str | None = None) -> bool:
    return _should_skip_clipboard_duplicate(
        text,
        method,
        persistent_snapshot=("", 0.0),
        persistent_state_trusted=True,
        dedupe_context=dedupe_context,
    )


def _begin_clipboard_insertion(
    text: str,
    method: str,
    *,
    dedupe_context: str | None = None,
) -> tuple[Path, tuple[str, float], bool] | None:
    if _should_skip_clipboard_memory_duplicate(text, method, dedupe_context=dedupe_context):
        return None
    lock_path: Path | None = None
    for attempt in range(CLIPBOARD_DEDUP_LOCK_RETRY_ATTEMPTS):
        lock_path = _acquire_clipboard_dedup_lock()
        if lock_path is not None:
            break
        if _should_skip_clipboard_memory_duplicate(text, method, dedupe_context=dedupe_context):
            return None
        if attempt + 1 < CLIPBOARD_DEDUP_LOCK_RETRY_ATTEMPTS:
            time.sleep(CLIPBOARD_DEDUP_LOCK_RETRY_DELAY_SECONDS)
    if lock_path is None:
        raise OutputError("clipboard dedupe lock unavailable")
    try:
        (
            persistent_state_trusted,
            persistent_snapshot,
            persistent_state_pending,
        ) = _read_clipboard_dedup_state_entry()
        if not persistent_state_trusted:
            raise OutputError("untrusted clipboard dedupe state")
        if _should_skip_clipboard_duplicate(
            text,
            method,
            persistent_snapshot=persistent_snapshot,
            persistent_state_trusted=persistent_state_trusted,
            pending_state=persistent_state_pending,
            dedupe_context=dedupe_context,
        ):
            _release_clipboard_dedup_lock(lock_path)
            return None
        return lock_path, persistent_snapshot, persistent_state_pending
    except BaseException:
        _release_clipboard_dedup_lock(lock_path)
        raise


def _refresh_pending_clipboard_dedup_state(
    text: str,
    method: str,
    *,
    dedupe_context: str | None = None,
) -> bool:
    quarantine_written = _set_clipboard_pending_quarantine(
        text,
        method,
        dedupe_context=dedupe_context,
    )
    if not quarantine_written:
        return False
    fingerprint = _clipboard_insertion_fingerprint(
        _normalize_clipboard_text(text),
        _clipboard_method_dedupe_context(method, dedupe_context),
    )
    state_written = _write_clipboard_dedup_fingerprint_state(fingerprint, time.time(), pending=True)
    return quarantine_written and state_written


def _handle_uncertain_clipboard_paste(
    text: str,
    dedupe_context: str | None,
    snapshot_available: bool,
    snapshot_text: str,
    *,
    restore_allowed: bool = True,
    restore_confirmed: bool | None = None,
) -> None:
    primary_error = sys.exc_info()[1]
    if restore_confirmed is None:
        restore_confirmed = True
        if restore_allowed:
            try:
                restore_confirmed = _restore_clipboard_snapshot_after_failed_paste(
                    text,
                    snapshot_available,
                    snapshot_text,
                    allowed_helpers=("xclip", "xsel"),
                )
            except BaseException:
                if primary_error is None:
                    raise
                primary_error.add_note("clipboard restore after failed automatic paste failed")
                restore_confirmed = False
        else:
            restore_confirmed = False
    if not restore_confirmed and primary_error is not None:
        primary_error.add_note("clipboard restore after failed automatic paste was not confirmed")
    try:
        refreshed = _refresh_pending_clipboard_dedup_state(
            text,
            "clipboard-paste",
            dedupe_context=dedupe_context,
        )
    except BaseException:
        if primary_error is None:
            raise
        primary_error.add_note("clipboard dedupe pending state refresh failed")
        return
    if refreshed:
        return
    if primary_error is not None:
        primary_error.add_note("clipboard dedupe pending state refresh failed")
        return
    raise OutputCleanupError("clipboard dedupe pending state refresh failed") from None


def _fallback_to_clipboard_only(text: str, *, dedupe_context: str | None = None) -> bool:
    if _clipboard_has_non_text_payload(unknown_is_non_text=False):
        raise OutputError("refusing to overwrite non-text clipboard for automatic paste")
    return insert_text(
        text,
        "clipboard",
        _dedupe_method="clipboard-paste",
        _dedupe_context=dedupe_context,
    )


def _commit_clipboard_only_after_paste_not_attempted(text: str, dedupe_context: str | None) -> bool:
    if not _set_clipboard_pending_quarantine(text, "clipboard-paste", dedupe_context=dedupe_context):
        return False
    if not _commit_clipboard_insertion(text, "clipboard"):
        return False
    return _clear_clipboard_pending_quarantine(
        text,
        "clipboard-paste",
        dedupe_context=dedupe_context,
    )


def insert_text(
    text: str,
    method: str,
    delay_ms: int = 8,
    *,
    _dedupe_method: str | None = None,
    _dedupe_context: str | None = None,
) -> bool:
    if not isinstance(method, str) or isinstance(method, bool):
        raise OutputError("method must be text")
    if _contains_escaped_null(method):
        raise OutputError("method contains invalid null byte")
    if _contains_http_header_control_chars(method):
        raise OutputError("method contains invalid control character")
    method = (method or "clipboard-paste").strip().lower()
    if method == "none":
        return False
    if text == "":
        return False
    if method == "clipboard":
        dedupe_method = _dedupe_method or method
        if dedupe_method not in {"clipboard", "clipboard-paste"}:
            raise OutputError("invalid clipboard dedupe method")
        insertion = _begin_clipboard_insertion(
            text,
            dedupe_method,
            dedupe_context=_dedupe_context,
        )
        if insertion is None:
            return False
        lock_path, persistent_snapshot, persistent_snapshot_pending = insertion
        try:
            snapshot = _reserve_clipboard_insertion_memory(
                text,
                dedupe_method,
                dedupe_context=_dedupe_context,
            )
        except BaseException:
            _release_clipboard_dedup_lock(lock_path)
            raise
        if snapshot is None:
            _release_clipboard_dedup_lock(lock_path)
            return False
        operation_performed = False
        committed = False
        ambiguous_cleanup = False
        try:
            if not _write_clipboard_dedup_fingerprint_state(
                _clipboard_insertion_fingerprint(
                    _normalize_clipboard_text(text),
                    _clipboard_method_dedupe_context(dedupe_method, _dedupe_context),
                ),
                time.time(),
                pending=True,
            ):
                raise OutputError("failed to reserve clipboard insertion state")
            try:
                set_clipboard(text)
            except OutputCleanupError:
                ambiguous_cleanup = True
                raise
            operation_performed = True
            if not _commit_clipboard_insertion(
                text,
                dedupe_method,
                dedupe_context=_dedupe_context,
            ):
                raise OutputError("failed to commit clipboard insertion state")
            committed = True
            return True
        finally:
            try:
                if ambiguous_cleanup:
                    if not _refresh_pending_clipboard_dedup_state(
                        text,
                        dedupe_method,
                        dedupe_context=_dedupe_context,
                    ):
                        primary_error = sys.exc_info()[1]
                        if primary_error is not None:
                            primary_error.add_note("clipboard dedupe pending state refresh failed")
                elif not committed:
                    if not operation_performed:
                        _restore_clipboard_insertion_snapshot(snapshot)
                        _restore_clipboard_dedup_state(persistent_snapshot, pending=persistent_snapshot_pending)
            finally:
                _release_clipboard_dedup_lock(lock_path)
    if method == "clipboard-paste":
        xdotool = _which("xdotool")
        target_window_snapshot = _active_x_window_snapshot(xdotool_command=xdotool) if xdotool else None
        dedupe_context: str | None = None
        if not _clipboard_paste_helper_available():
            return _fallback_to_clipboard_only(text, dedupe_context=dedupe_context)
        if not xdotool:
            return _fallback_to_clipboard_only(text, dedupe_context=dedupe_context)
        if target_window_snapshot is None:
            return _fallback_to_clipboard_only(text, dedupe_context=dedupe_context)
        if not _clipboard_paste_writer_available():
            return _fallback_to_clipboard_only(text, dedupe_context=dedupe_context)
        dedupe_context = _clipboard_dedup_context_for_window_snapshot(target_window_snapshot)
        insertion = _begin_clipboard_insertion(text, method, dedupe_context=dedupe_context)
        if insertion is None:
            return False
        lock_path, persistent_snapshot, persistent_snapshot_pending = insertion
        try:
            snapshot = _reserve_clipboard_insertion_memory(text, method, dedupe_context=dedupe_context)
        except BaseException:
            _release_clipboard_dedup_lock(lock_path)
            raise
        if snapshot is None:
            _release_clipboard_dedup_lock(lock_path)
            return False
        operation_performed = False
        committed = False
        paste_not_attempted = False
        ambiguous_cleanup = False
        paste_attempt_uncertain = False
        paste_cleanup_unconfirmed = False
        clipboard_snapshot_available = False
        clipboard_snapshot = ""
        try:
            clipboard_snapshot_available, clipboard_snapshot = _read_text_clipboard_snapshot()
            if not clipboard_snapshot_available:
                raise OutputError("refusing automatic paste without readable text clipboard snapshot")
            if _clipboard_has_non_text_payload() and not _clipboard_snapshot_is_verified_empty(
                clipboard_snapshot_available, clipboard_snapshot
            ):
                raise OutputError("refusing to overwrite non-text clipboard for automatic paste")
            if not _write_clipboard_dedup_fingerprint_state(
                _clipboard_insertion_fingerprint(
                    _normalize_clipboard_text(text),
                    _clipboard_method_dedupe_context(method, dedupe_context),
                ),
                time.time(),
                pending=True,
            ):
                raise OutputError("failed to reserve clipboard-paste insertion state")
            _assert_clipboard_text_snapshot_unchanged(clipboard_snapshot_available, clipboard_snapshot)
            try:
                set_clipboard(text, allowed_helpers=("xclip", "xsel"))
            except OutputCleanupError:
                ambiguous_cleanup = True
                raise
            operation_performed = True
            try:
                clipboard_matches = _clipboard_still_contains_inserted_text(text)
            except BaseException:
                paste_not_attempted = True
                raise
            if not clipboard_matches:
                paste_not_attempted = True
                raise PasteNotAttemptedError("clipboard changed before automatic paste")
            paste_attempt_uncertain = True
            try:
                paste_from_clipboard(expected_window_snapshot=target_window_snapshot)
            except PasteNotAttemptedError:
                paste_not_attempted = True
                paste_attempt_uncertain = False
                if operation_performed and _commit_clipboard_only_after_paste_not_attempted(
                    text,
                    dedupe_context,
                ):
                    committed = True
                    return True
                raise
            except OutputCleanupError:
                paste_cleanup_unconfirmed = True
                raise
            except BaseException as exc:
                if not isinstance(exc, Exception):
                    paste_cleanup_unconfirmed = True
                raise
            if not _commit_clipboard_insertion(text, method, dedupe_context=dedupe_context):
                raise OutputError("failed to commit clipboard-paste insertion state")
            committed = True
            return True
        finally:
            try:
                if ambiguous_cleanup:
                    if not _refresh_pending_clipboard_dedup_state(
                        text,
                        method,
                        dedupe_context=dedupe_context,
                    ):
                        primary_error = sys.exc_info()[1]
                        if primary_error is not None:
                            primary_error.add_note("clipboard dedupe pending state refresh failed")
                elif paste_attempt_uncertain and not committed:
                    _handle_uncertain_clipboard_paste(
                        text,
                        dedupe_context,
                        clipboard_snapshot_available,
                        clipboard_snapshot,
                        restore_allowed=not paste_cleanup_unconfirmed,
                    )
                elif not committed:
                    if paste_not_attempted:
                        restore_confirmed = True
                        try:
                            restore_confirmed = _restore_clipboard_snapshot_after_failed_paste(
                                text,
                                clipboard_snapshot_available,
                                clipboard_snapshot,
                                allowed_helpers=("xclip", "xsel"),
                            )
                        except BaseException:
                            primary_error = sys.exc_info()[1]
                            if primary_error is None:
                                raise
                            primary_error.add_note(
                                "clipboard restore after failed automatic paste failed"
                            )
                            restore_confirmed = False
                        if restore_confirmed:
                            _restore_clipboard_insertion_snapshot(snapshot)
                            _restore_clipboard_dedup_state(
                                persistent_snapshot,
                                pending=persistent_snapshot_pending,
                            )
                        else:
                            _handle_uncertain_clipboard_paste(
                                text,
                                dedupe_context,
                                clipboard_snapshot_available,
                                clipboard_snapshot,
                                restore_allowed=False,
                                restore_confirmed=False,
                            )
                    elif not operation_performed:
                        _restore_clipboard_insertion_snapshot(snapshot)
                        _restore_clipboard_dedup_state(persistent_snapshot, pending=persistent_snapshot_pending)
                    else:
                        _restore_clipboard_snapshot_after_failed_paste(
                            text,
                            clipboard_snapshot_available,
                            clipboard_snapshot,
                            allowed_helpers=("xclip", "xsel"),
                        )
            finally:
                _release_clipboard_dedup_lock(lock_path)
    if method == "type":
        xdotool = _which("xdotool")
        if not xdotool:
            raise OutputError("xdotool is required for direct typing on Cinnamon X11")
        target_window_snapshot = _active_x_window_snapshot(xdotool_command=xdotool)
        type_text(text, delay_ms, expected_window_snapshot=target_window_snapshot, xdotool_command=xdotool)
        return True
    raise OutputError(f"unknown insert method: {method}")
