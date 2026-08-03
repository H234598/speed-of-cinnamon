from __future__ import annotations

import errno
import gzip
import json
import logging
import math
import os
import re
import secrets
import stat as stat_module
import string
import unicodedata
import time
import zlib
from collections.abc import Callable
from functools import wraps
from itertools import islice
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from .path_safety import (
    _rename_without_replacing,
    assert_fd_is_private_directory,
    assert_fd_is_regular_private_file,
    ensure_directory_without_following_symlinks,
    open_file_without_following_symlinks,
)
from .paths import logs_dir

LOG_LEVELS = ("off", "error", "warning", "info", "debug")
LOG_LEVEL_VALUES = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}
DEFAULT_LOG_LEVEL = "error"
MAX_DAILY_LOG_BYTES = 1_000_000
MAX_TOTAL_LOG_BYTES = 5_000_000
COMPRESS_AFTER_DAYS = 3
MAX_LOG_ROTATION_CANDIDATES = 100
MAX_LOG_MESSAGE_CHARS = 320
MAX_LOG_FIELD_CHARS = 160
LOG_MAINTENANCE_INTERVAL_SECONDS = 60.0
LOGGER_NAME = "speed_of_cinnamon"
HOME_DIR = str(Path.home())


def _note_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    try:
        primary.add_note("log cleanup failed")
    except Exception:
        return


def _fsync_fd(fd: int) -> None:
    while True:
        try:
            os.fsync(fd)
            return
        except InterruptedError:
            continue


_DAILY_LOG_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log$")
_DAILY_GZ_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log\.gz$")
_MONTHLY_GZ_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2})\.log\.gz$")
_CREDENTIAL_KEY_PATTERN = (
    r"bearer|token|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|api[_ -]?key|apikey|"
    r"client[_ -]?secret|private[_ -]?key|secret[_ -]?key|secret|password|passwd|passphrase"
)
_TOKEN_RE = re.compile(rf"(?i)\b({_CREDENTIAL_KEY_PATTERN})\b\s*[:=]\s*\S+(?:[ \t]+\S+)*")
_BARE_CREDENTIAL_RE = re.compile(
    r"(?i)(?<![/\\])\b(token|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|api[_ -]?key|apikey|"
    r"client[_ -]?secret|private[_ -]?key|secret[_ -]?key|password|passwd|passphrase)\b\s+"
    r"(?:(?:is|are|was|were)\s+)?"
    r"(?!(?:is|are|was|were|contains?|must|too|missing|invalid|required|not|empty)\b)\S+"
    r"(?:[ \t]+\S+)*"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+\S+")
_OPENAI_KEY_RE = re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_\-]{12,}\b")
_SHORT_API_KEY_RE = re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b")
_LOG_MATCH_IGNORE_CATEGORIES = frozenset({"Mn", "Mc", "Me", "Cf"})
_URL_CREDENTIAL_RE = re.compile(r"([a-z][a-z0-9+.-]{0,255}+://)([^/@\s]+)@")
_PATH_STOP_CHARS = frozenset({" ", "\t", "\n", "\r", ")", "}", "]"})
_PATH_PREFIX_BOUNDARY_BLOCKED_CHARS = frozenset(
    set(string.ascii_letters + string.digits + "._-/")
)
_PATH_SCHEME_CHARS = frozenset(string.ascii_letters + string.digits + "+-.")
_CSI_MAX_PREFIX_BYTES = 64
_ERROR_SCAN_MAX_CHARS = 8192
_MAX_ERROR_INPUT_CHARS = 65_536


def _is_ignored_char(char: str) -> bool:
    codepoint = ord(char)
    if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
        return True
    return unicodedata.category(char) in _LOG_MATCH_IGNORE_CATEGORIES


def _path_token_end(value: str, start: int) -> int:
    end = start
    while end < len(value) and value[end] not in _PATH_STOP_CHARS:
        end += 1
    return end


def _has_path_prefix_boundary(value: str, start: int) -> bool:
    if start <= 0:
        return True
    if value[start - 1] not in _PATH_PREFIX_BOUNDARY_BLOCKED_CHARS:
        return True
    return _has_ansi_csi_prefix(value, start)


def _has_ansi_csi_prefix(value: str, start: int) -> bool:
    if start <= 0:
        return False
    end = start - 1
    if end < 0:
        return False
    index = end
    scanned = 0
    if 0x40 <= ord(value[index]) <= 0x7E:
        scanned = 1
        index -= 1
    while scanned < _CSI_MAX_PREFIX_BYTES and index >= 0 and 0x30 <= ord(value[index]) <= 0x3F:
        index -= 1
        scanned += 1
    if scanned >= _CSI_MAX_PREFIX_BYTES:
        return True
    while scanned < _CSI_MAX_PREFIX_BYTES and index >= 0 and 0x20 <= ord(value[index]) <= 0x2F:
        index -= 1
        scanned += 1
    if scanned >= _CSI_MAX_PREFIX_BYTES:
        return True
    if index < 0:
        return False
    if value[index] == "\x9b":
        return True
    if value[index] == "\x1b":
        return True
    if index > 0 and value[index] == "[" and value[index - 1] == "\x1b":
        return True
    if index > 0 and value[index] == "[" and value[index - 1] == "\x9b":
        return True
    return False


def _url_scheme_before(value: str, first_slash: int) -> str | None:
    if first_slash <= 0 or value[first_slash - 1] != ":":
        return None
    pos = first_slash - 2
    while pos >= 0 and value[pos] in _PATH_SCHEME_CHARS:
        pos -= 1
    scheme_start = pos + 1
    if scheme_start == first_slash - 1:
        return None
    scheme = value[scheme_start : first_slash - 1]
    if not scheme:
        return None
    if pos >= 0 and value[pos] in _PATH_SCHEME_CHARS:
        return None
    if not scheme[0].isalpha():
        return None
    return scheme.lower()


def _match_local_absolute_path(value: str, start: int) -> int:
    if start >= len(value):
        return 0
    if not _has_path_prefix_boundary(value, start):
        return 0
    if value[start].isalpha() and start + 2 < len(value) and value[start + 1] == ":" and value[start + 2] in "\\/":
        end = _path_token_end(value, start)
        if end > start + 3:
            return end - start
        return 0
    if value[start] == "\\":
        if start + 1 >= len(value):
            return 0
        if value[start + 1] not in "\\/":
            token_end = _path_token_end(value, start)
            if token_end <= start + 1:
                return 0
            return token_end - start
        if start > 0 and value[start - 1] == "\\":
            return 0
        if start + 3 >= len(value) or value[start + 2] in _PATH_STOP_CHARS:
            return 0
        host_end = start + 2
        while host_end < len(value) and value[host_end] not in (_PATH_STOP_CHARS | {'\\', '/'}):
            host_end += 1
        if host_end >= len(value) or value[host_end] not in "\\/":
            return 0
        share_end = _path_token_end(value, host_end + 1)
        if share_end <= host_end + 1:
            return 0
        return share_end - start
    if value[start] != "/":
        return 0
    if start + 1 >= len(value):
        return 0
    if value[start + 1] == "/":
        if start > 0 and value[start - 1] == "/":
            return 0
        scheme = _url_scheme_before(value, start)
        if scheme is not None and scheme != "file" and len(scheme) > 1:
            return 0
        end = _path_token_end(value, start)
        if end > start + 2:
            return end - start
        return 0
    end = _path_token_end(value, start)
    if end > start + 1:
        return end - start
    return 0


def _contains_local_absolute_path(value: str) -> bool:
    n = len(value)
    if "/" not in value and "\\" not in value:
        return False
    index = 0
    while index < n:
        if value[index] in "/\\" or (value[index].isalpha() and index + 2 < n and value[index + 1] == ":" and value[index + 2] in "\\/"):
            if _match_local_absolute_path(value, index) > 0:
                return True
            if value[index].isalpha() and index + 2 < n and value[index + 1] == ":" and value[index + 2] in "\\/":
                index += 3
                continue
        index += 1
    return False


def _redact_local_absolute_paths(value: str) -> str:
    n = len(value)
    index = 0
    out: list[str] = []
    while index < n:
        token_len = _match_local_absolute_path(value, index)
        if token_len:
            out.append("[redacted path]")
            index += token_len
            continue
        out.append(value[index])
        index += 1
    return "".join(out)
_ERROR_DETAIL_RE = re.compile(
    r"(?i)(?:\b(?:stdout|stderr)\s*:|\b(?:raw\s+)?transcript\s*(?::|\b(?:text|words|payload|for)\b)|\bprompt\s*:|command\s+output\s*:|backend\s+output\s*:)"
)
_ERROR_OUTPUT_LIKELY_RE = re.compile(r"(?i)\b(traceback|exception|at|exit\s+code|stderr|stdout|command\s+output|process\s+exited|python|failed\s+with|npm|node)\b")
_ERROR_SECRET_WORD_RE = re.compile(r"(?i)\bsecret\b")
_ERROR_OPAQUE_DETAIL_RE = re.compile(r"(?i)^(?=[A-Za-z0-9_.:/+=@-]{6,}$)(?=.*[a-z])(?=.*\d)[A-Za-z0-9_.:/+=@-]+$")
_SANITIZE_HINT_RE = re.compile(
    rf"(?i)(?:\b(?:{_CREDENTIAL_KEY_PATTERN})\b\s*[:=]\s*\S+(?:[ \t]+\S+)*|"
    r"(?<![/\\])\b(?:token|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|api[_ -]?key|apikey|"
    r"client[_ -]?secret|private[_ -]?key|secret[_ -]?key|password|passwd|passphrase)\b\s+(?:(?:is|are|was|were)\s+)?"
    r"(?!(?:is|are|was|were|contains?|must|too|missing|invalid|required|not|empty)\b)\S+|"
    r"\bbearer\s+\S+|\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b|[a-z][a-z0-9+.-]{0,255}+://[^/@\s]+@)"
)
_SANITIZE_ESCAPE_TABLE = {
    **{codepoint: f"\\x{codepoint:02x}" for codepoint in tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))},
    ord("\r"): "\\r",
    ord("\n"): "\\n",
    ord("\x00"): "\\x00",
}
_SANITIZE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_SENSITIVE_KEY_SPLIT_RE = re.compile(r"[._-]+")
_SENSITIVE_KEY_TOKENS = frozenset({
    "apikey",
    "authorization",
    "bearer",
    "password",
    "passwd",
    "passphrase",
    "prompt",
    "secret",
    "token",
    "transcript",
    "vocabulary",
})
_SENSITIVE_KEY_EXACT = frozenset({"command", "context", "key", "text"})
_SENSITIVE_KEY_PHRASES = (
    ("api", "key"),
    ("backend", "output"),
    ("command", "args"),
    ("command", "line"),
    ("command", "output"),
    ("command", "template"),
    ("personal", "context"),
    ("raw", "transcript"),
    ("transcript", "text"),
)
_SANITIZE_KEY_SAFE_CHARS = frozenset(string.ascii_lowercase + string.digits + "_.-")
_FORBIDDEN_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ESCAPED_FORBIDDEN_CONTROL_RE = re.compile(
    r"(?i)(?:\\[abfnrtv]|\\x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|\\u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "event": sanitize_text(str(record.getMessage()), max_chars=MAX_LOG_MESSAGE_CHARS),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            for key, value in fields.items():
                clean_key = sanitize_key(key)
                if clean_key:
                    payload[clean_key] = sanitize_value(clean_key, value)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


class SizeCappedJsonFileHandler(logging.Handler):
    def __init__(self, path: Path, base_dir: Path) -> None:
        super().__init__()
        self.path = path
        self.base_dir = base_dir
        self.stream: TextIO | None = None
        self._disabled = False
        self._retry_until = 0.0
        self._retry_count = 0
        self._retry_base_delay = 1.0
        self._next_maintenance_at = time.monotonic() + LOG_MAINTENANCE_INTERVAL_SECONDS

    def close(self) -> None:
        stream = self.stream
        self.stream = None
        try:
            if stream is not None:
                stream.close()
        except Exception:
            self.stream = None
        finally:
            super().close()

    def emit(self, record: logging.LogRecord) -> None:
        if self._disabled:
            return
        now = time.monotonic()
        if now < self._retry_until:
            return
        try:
            daily_path = _active_log_path(self.base_dir)
            if self.path != daily_path:
                self.close()
                self.path = daily_path
                self._next_maintenance_at = 0.0
            line = self.format(record) + "\n"
            encoded = line.encode("utf-8")
            if len(encoded) > MAX_DAILY_LOG_BYTES:
                line = _oversized_record_line(record)
                encoded = line.encode("utf-8")
            try:
                current_size = self.path.stat().st_size
            except OSError:
                current_size = None
            if current_size is not None and current_size + len(encoded) > MAX_DAILY_LOG_BYTES:
                self.close()
                _rotate_active_if_needed(self.path, force=True)
                rotated = True
            else:
                rotated = False
            self._open()
            if self.stream is None:
                raise RuntimeError("failed to open log file")
            self.stream.write(line)
            self.stream.flush()
            self._maintain_after_emit(force=rotated)
            self._retry_count = 0
            self._retry_until = 0.0
        except Exception:
            self.close()
            try:
                path_is_insecure = self._is_log_path_insecure()
            except Exception:
                path_is_insecure = True
            if path_is_insecure:
                self._disabled = True
                return
            self._retry_count += 1
            delay = min(self._retry_base_delay * (2 ** (self._retry_count - 1)), 60.0)
            self._retry_until = now + delay

    def _is_log_path_insecure(self) -> bool:
        parent_fd = None
        try:
            try:
                file_stat = self.path.lstat()
            except FileNotFoundError:
                file_stat = None
            except PermissionError:
                return True
            if file_stat is not None:
                if stat_module.S_ISLNK(file_stat.st_mode) or not stat_module.S_ISREG(file_stat.st_mode):
                    return True
                if getattr(file_stat, "st_nlink", 1) != 1:
                    return True
            parent_fd = ensure_directory_without_following_symlinks(self.path.parent, field_name="log directory")
            assert_fd_is_private_directory(parent_fd, field_name="log directory")
            return False
        except RuntimeError:
            return True
        except PermissionError:
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                return True
            return False
        finally:
            if parent_fd is not None:
                try:
                    os.close(parent_fd)
                except OSError:
                    pass

    def _open(self) -> None:
        if self.stream is not None:
            try:
                stream_stat = os.fstat(self.stream.fileno())
                path_stat = self.path.lstat()
                stream_matches_path = (
                    stat_module.S_ISREG(stream_stat.st_mode)
                    and stat_module.S_ISREG(path_stat.st_mode)
                    and getattr(stream_stat, "st_nlink", 1) == 1
                    and getattr(path_stat, "st_nlink", 1) == 1
                    and stream_stat.st_dev == path_stat.st_dev
                    and stream_stat.st_ino == path_stat.st_ino
                    and stream_stat.st_mode == path_stat.st_mode
                )
            except (OSError, ValueError):
                stream_matches_path = False
            if stream_matches_path:
                return
            self.close()
        if self.stream is None:
            try:
                expected_stat = self.path.lstat()
            except FileNotFoundError:
                expected_stat = None
            parent_fd = ensure_directory_without_following_symlinks(self.path.parent, field_name="log directory")
            parent_error: BaseException | None = None
            try:
                assert_fd_is_private_directory(parent_fd, field_name="log directory")
            except BaseException as exc:
                parent_error = exc
                raise
            finally:
                try:
                    os.close(parent_fd)
                except OSError:
                    pass
                except BaseException as cleanup_error:
                    if parent_error is not None:
                        _note_cleanup_failure(parent_error, cleanup_error)
                    else:
                        raise
            open_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if expected_stat is None:
                open_flags |= os.O_EXCL
            fd = open_file_without_following_symlinks(
                self.path,
                open_flags,
                0o600,
                field_name="log file",
            )
            try:
                file_stat = os.fstat(fd)
                if not stat_module.S_ISREG(file_stat.st_mode):
                    raise RuntimeError("log file must be a regular file")
                if getattr(file_stat, "st_nlink", 1) != 1:
                    raise RuntimeError("log file must not be hardlinked")
                if expected_stat is not None and (
                    file_stat.st_dev != expected_stat.st_dev
                    or file_stat.st_ino != expected_stat.st_ino
                    or file_stat.st_mode != expected_stat.st_mode
                ):
                    raise RuntimeError("log file changed while opening")
                path_error: RuntimeError | None = None
                try:
                    current_path_stat = self.path.lstat()
                except OSError:
                    path_error = RuntimeError("log file changed while opening")
                if path_error is not None:
                    raise path_error
                if (
                    current_path_stat.st_dev != file_stat.st_dev
                    or current_path_stat.st_ino != file_stat.st_ino
                    or current_path_stat.st_mode != file_stat.st_mode
                ):
                    raise RuntimeError("log file changed while opening")
                permission_error: RuntimeError | None = None
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    permission_error = RuntimeError("log file permissions could not be restricted")
                if permission_error is not None:
                    raise permission_error
                assert_fd_is_regular_private_file(fd, field_name="log file", require_private_mode=True)
                self.stream = os.fdopen(fd, "a", encoding="utf-8")
            except Exception as exc:
                try:
                    os.close(fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                raise
            except BaseException as exc:
                try:
                    os.close(fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                raise

    def _maintain_after_emit(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now < self._next_maintenance_at:
            return
        maintain_logs(self.base_dir)
        self._next_maintenance_at = now + LOG_MAINTENANCE_INTERVAL_SECONDS


def _contains_forbidden_control(value: str) -> bool:
    return _FORBIDDEN_CONTROL_RE.search(value) is not None or _ESCAPED_FORBIDDEN_CONTROL_RE.search(value) is not None


def validate_log_level(level: str) -> str:
    if isinstance(level, bool) or not isinstance(level, str):
        raise RuntimeError("log level must be text")
    if _contains_forbidden_control(level):
        raise RuntimeError("log level contains invalid control character")
    normalized = level.strip().lower()
    if normalized not in LOG_LEVELS:
        raise RuntimeError(f"log level must be one of: {', '.join(LOG_LEVELS)}")
    return normalized


def _log_level_value(level: str) -> int:
    if level == "off":
        return 0
    return LOG_LEVEL_VALUES[level]


def _safe_public_log_exception(error: Exception) -> Exception:
    try:
        safe_message = sanitize_error_message(str(error), max_chars=MAX_LOG_MESSAGE_CHARS)
    except Exception:
        safe_message = ""
    if not safe_message or safe_message.startswith("[redacted") or "[redacted path]" in safe_message:
        safe_message = "log operation failed"
    try:
        sanitized = type(error)(safe_message)
    except Exception:
        sanitized = RuntimeError("log operation failed")
    return sanitized


def _public_log_boundary(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        sanitized: Exception | None = None
        try:
            return function(*args, **kwargs)
        except Exception as error:
            sanitized = _safe_public_log_exception(error)
        if sanitized is not None:
            raise sanitized
        return None

    return wrapped


@_public_log_boundary
def configure_logging(level: str = DEFAULT_LOG_LEVEL, *, base_dir: Path | None = None) -> None:
    normalized = validate_log_level(level)
    logger = logging.getLogger(LOGGER_NAME)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()
    logger.propagate = False
    if normalized == "off":
        logger.disabled = True
        return

    logger.disabled = False
    level_value = _log_level_value(normalized)
    logger.setLevel(level_value)
    directory = base_dir or logs_dir()
    maintain_logs(directory)
    log_path = _active_log_path(directory)
    _rotate_active_if_needed(log_path)

    handler = SizeCappedJsonFileHandler(log_path, directory)
    handler.setFormatter(JsonLogFormatter())
    handler.setLevel(level_value)
    logger.addHandler(handler)


def log_event(level: str, event: str, **fields: object) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.disabled:
        return
    normalized = validate_log_level(level)
    if normalized == "off":
        return
    logger.log(_log_level_value(normalized), event, extra={"fields": fields})


@_public_log_boundary
def maintain_logs(base_dir: Path | None = None, *, today: date | None = None) -> None:
    directory = base_dir or logs_dir()
    _ensure_log_directory(directory)
    current_day = today or date.today()
    _merge_old_months(directory, current_day)
    _compress_old_daily_logs(directory, current_day)
    _enforce_file_size_limit(directory, today=current_day)
    _enforce_total_size_limit(directory, today=current_day)


def sanitize_key(key: object) -> str:
    if isinstance(key, bool) or not isinstance(key, str):
        return ""
    if _contains_forbidden_control(key):
        return ""
    safe = key.strip().lower()
    if safe.isascii() and all(ch in _SANITIZE_KEY_SAFE_CHARS for ch in safe):
        return safe[:64]
    safe = _SANITIZE_KEY_RE.sub("_", safe)
    return safe[:64]


def sanitize_value(key: str, value: object) -> object:
    if _is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 3)
    if isinstance(value, Path):
        return _safe_path(value)
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(key, item) for item in islice(value, 8)]
    if isinstance(value, dict):
        clean: dict[str, object] = {}
        for raw_key, raw_value in islice(value.items(), 16):
            child_key = sanitize_key(raw_key)
            if child_key:
                clean[child_key] = sanitize_value(child_key, raw_value)
        return clean
    return sanitize_text(str(value), max_chars=MAX_LOG_FIELD_CHARS)


def _sub_with_ignored_projection(
    text: str,
    pattern: re.Pattern[str],
    replacement: str | Callable[[re.Match[str]], str] = "[redacted]",
) -> str:
    if not any(_is_ignored_char(char) for char in text):
        return pattern.sub(replacement, text)
    normalized: list[str] = []
    index_map: list[int] = []
    for source_index, char in enumerate(text):
        for normalized_char in unicodedata.normalize("NFKD", char).casefold():
            if _is_ignored_char(normalized_char):
                continue
            normalized.append(normalized_char)
            index_map.append(source_index)
    normalized_text = "".join(normalized)
    if not normalized_text:
        return text
    pieces: list[str] = []
    cursor = 0
    for match in pattern.finditer(normalized_text):
        if match.start() >= len(index_map) or match.end() - 1 >= len(index_map):
            continue
        original_start = index_map[match.start()]
        original_end = index_map[match.end() - 1] + 1
        while original_start > cursor and _is_ignored_char(text[original_start - 1]):
            original_start -= 1
        while original_end < len(text) and _is_ignored_char(text[original_end]):
            original_end += 1
        if original_end <= cursor:
            continue
        if original_start < cursor:
            original_start = cursor
        pieces.append(text[cursor:original_start])
        pieces.append(replacement(match) if callable(replacement) else replacement)
        cursor = original_end
    if not pieces:
        return text
    pieces.append(text[cursor:])
    return "".join(pieces)


def sanitize_text(value: str, *, max_chars: int = MAX_LOG_FIELD_CHARS) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        return "[invalid]"
    input_was_truncated = len(value) > max_chars
    if input_was_truncated:
        value = value[:max_chars]
    if any(_is_ignored_char(char) for char in value):
        redacted_value = _sub_with_ignored_projection(
            value,
            _TOKEN_RE,
            lambda match: f"{match.group(1)}=[redacted]",
        )
        redacted_value = _sub_with_ignored_projection(
            redacted_value,
            _BARE_CREDENTIAL_RE,
            lambda match: f"{match.group(1)}=[redacted]",
        )
        redacted_value = _sub_with_ignored_projection(redacted_value, _BEARER_RE, "Bearer [redacted]")
        redacted_value = _sub_with_ignored_projection(
            redacted_value,
            _URL_CREDENTIAL_RE,
            lambda match: f"{match.group(1)}[redacted]@",
        )
        redacted_value = _sub_with_ignored_projection(redacted_value, _OPENAI_KEY_RE)
        redacted_value = _sub_with_ignored_projection(redacted_value, _SHORT_API_KEY_RE)
    else:
        redacted_value = _OPENAI_KEY_RE.sub("[redacted]", value)
        redacted_value = _SHORT_API_KEY_RE.sub("[redacted]", redacted_value)
    has_control = _contains_control_chars(redacted_value)
    if has_control:
        redacted_value = _redact_local_absolute_paths(redacted_value)
    if _contains_local_absolute_path(redacted_value):
        redacted_value = _redact_local_absolute_paths(redacted_value)
    if redacted_value != value:
        value = redacted_value
    if (
        not has_control
        and ":" not in value
        and "@" not in value
        and _SANITIZE_HINT_RE.search(value) is None
        and not _contains_local_absolute_path(value)
        and (not HOME_DIR or HOME_DIR == "/" or HOME_DIR not in value)
    ):
        if input_was_truncated or len(value) > max_chars:
            return value[:max_chars] + "...[truncated]"
        return value
    text = value.translate(_SANITIZE_ESCAPE_TABLE)
    text = _TOKEN_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BARE_CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _OPENAI_KEY_RE.sub("[redacted]", text)
    text = _SHORT_API_KEY_RE.sub("[redacted]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[redacted]@", text)
    if not has_control:
        text = _redact_local_absolute_paths(text)
    if HOME_DIR and HOME_DIR != "/":
        text = text.replace(HOME_DIR, "~")
    if input_was_truncated or len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _contains_control_chars(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)


def sanitize_error_message(error: object, *, max_chars: int = MAX_LOG_MESSAGE_CHARS) -> str:
    if isinstance(error, bool) or not isinstance(error, str):
        return "[invalid]"
    effective_max_chars = min(max(0, max_chars), _MAX_ERROR_INPUT_CHARS)
    input_was_truncated = len(error) > effective_max_chars
    scan_limit = min(max(_ERROR_SCAN_MAX_CHARS, effective_max_chars), _MAX_ERROR_INPUT_CHARS)
    scan_error = error[:scan_limit]
    failed_match = re.match(
        r"(?is)^(?P<command>.+?)\s+(?P<marker>failed|error)\s*:\s*(?P<details>.+)$",
        scan_error,
    )
    if failed_match:
        details = failed_match.group("details").strip()
        details_for_scan = failed_match.group("details").strip()[:_ERROR_SCAN_MAX_CHARS]
        if (
            _ERROR_DETAIL_RE.search(details_for_scan) is not None
            or _BARE_CREDENTIAL_RE.search(details_for_scan) is not None
            or _ERROR_SECRET_WORD_RE.search(details_for_scan) is not None
            or _ERROR_OPAQUE_DETAIL_RE.fullmatch(details_for_scan) is not None
        ):
            return "[redacted error details]"
        if (
            "\n" in details
            or "\r" in details
            or _ERROR_OUTPUT_LIKELY_RE.search(details) is not None
            or len(details) > 120
        ):
            return "[redacted error details]"
        command = sanitize_text(failed_match.group("command").strip(), max_chars=80)
        prefix = f"{command} {failed_match.group('marker')}: "
        details_max_chars = max(8, effective_max_chars - len(prefix))
        details = sanitize_text(details, max_chars=details_max_chars)
        if _ERROR_SECRET_WORD_RE.search(details):
            return "[redacted error details]"
        if len(details) <= 0:
            return "[redacted error details]"
        candidate = f"{prefix}{details}"
        if len(candidate) > effective_max_chars:
            return candidate[:effective_max_chars] + "...[truncated]"
        return candidate
    if _ERROR_DETAIL_RE.search(scan_error):
        return "[redacted error details]"
    if _BARE_CREDENTIAL_RE.search(scan_error):
        return "[redacted error details]"
    if (
        _ERROR_SECRET_WORD_RE.search(scan_error)
        and _TOKEN_RE.search(scan_error) is None
        and _BARE_CREDENTIAL_RE.search(scan_error) is None
        and _BEARER_RE.search(scan_error) is None
        and _OPENAI_KEY_RE.search(scan_error) is None
        and _SHORT_API_KEY_RE.search(scan_error) is None
        and _URL_CREDENTIAL_RE.search(scan_error) is None
    ):
        return "[redacted error details]"
    sanitized = sanitize_text(error[:effective_max_chars], max_chars=effective_max_chars)
    sanitized = _SHORT_API_KEY_RE.sub("[redacted]", sanitized)
    if input_was_truncated or len(sanitized) > effective_max_chars:
        return sanitized[:effective_max_chars] + "...[truncated]"
    return sanitized


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    tokens = tuple(token for token in _SENSITIVE_KEY_SPLIT_RE.split(lowered) if token)
    if not tokens:
        return False
    if len(tokens) == 1 and tokens[0] in _SENSITIVE_KEY_EXACT:
        return True
    if "".join(tokens) in _SENSITIVE_KEY_TOKENS:
        return True
    if any(token in _SENSITIVE_KEY_TOKENS for token in tokens):
        return True
    return any(
        tokens[index:index + len(phrase)] == phrase
        for phrase in _SENSITIVE_KEY_PHRASES
        for index in range(0, len(tokens) - len(phrase) + 1)
    )


def _safe_path(path: Path) -> str:
    try:
        expanded = path.expanduser()
        return sanitize_text(str(expanded), max_chars=MAX_LOG_FIELD_CHARS)
    except RuntimeError:
        return "[invalid-path]"


def _active_log_path(directory: Path, today: date | None = None) -> Path:
    current_day = today or date.today()
    return directory / f"speed-of-cinnamon-{current_day.isoformat()}.log"


def _ensure_log_directory(directory: Path) -> None:
    directory_fd: int | None = None
    try:
        directory_fd = ensure_directory_without_following_symlinks(directory, field_name="log directory")
    except OSError:
        directory_error = RuntimeError("failed to prepare log directory")
    else:
        directory_error = None
    if directory_error is not None:
        raise directory_error
    try:
        if directory_fd is not None:
            os.close(directory_fd)
    except OSError:
        pass


def _oversized_record_line(record: logging.LogRecord) -> str:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname.lower(),
        "event": "oversized_log_record_redacted",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def _assert_regular_unlinked_file(path: Path, *, field_name: str) -> os.stat_result:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    try:
        file_stat = path.lstat()
    except OSError:
        file_error = RuntimeError(f"{field_name} must be an existing file")
    else:
        file_error = None
    if file_error is not None:
        raise file_error
    if stat_module.S_ISLNK(file_stat.st_mode):
        raise RuntimeError(f"{field_name} must not be a symlink")
    if not stat_module.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"{field_name} must be a regular file")
    if getattr(file_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{field_name} must not be hardlinked")
    if hasattr(os, "getuid") and file_stat.st_uid != os.getuid():
        raise RuntimeError(f"{field_name} must be owned by the current user")
    return file_stat


def _same_log_inode(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev,
        first.st_ino,
        first.st_mode,
        first.st_size,
        first.st_mtime_ns,
    ) == (
        second.st_dev,
        second.st_ino,
        second.st_mode,
        second.st_size,
        second.st_mtime_ns,
    )


def _open_log_source_file(
    path: Path,
    *,
    field_name: str,
    expected_stat: os.stat_result | None = None,
) -> int:
    current_stat = _assert_regular_unlinked_file(path, field_name=field_name)
    if expected_stat is not None and (
        current_stat.st_dev != expected_stat.st_dev
        or current_stat.st_ino != expected_stat.st_ino
        or current_stat.st_mode != expected_stat.st_mode
        or current_stat.st_size != expected_stat.st_size
        or getattr(current_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
        or current_stat.st_mtime_ns != expected_stat.st_mtime_ns
        or current_stat.st_ctime_ns != expected_stat.st_ctime_ns
    ):
        raise RuntimeError(f"{field_name} changed before opening")
    expected_for_fd = expected_stat or current_stat
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    open_error: RuntimeError | None = None
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name=field_name)
    except OSError:
        open_error = RuntimeError(f"{field_name} is not readable")
        fd = -1
    if open_error is not None:
        raise open_error
    try:
        assert_fd_is_regular_private_file(fd, field_name=field_name, require_private_mode=True)
        opened_stat = os.fstat(fd)
        if (
            opened_stat.st_dev != expected_for_fd.st_dev
            or opened_stat.st_ino != expected_for_fd.st_ino
            or opened_stat.st_mode != expected_for_fd.st_mode
            or opened_stat.st_size != expected_for_fd.st_size
            or getattr(opened_stat, "st_nlink", 1) != getattr(expected_for_fd, "st_nlink", 1)
            or opened_stat.st_mtime_ns != expected_for_fd.st_mtime_ns
            or opened_stat.st_ctime_ns != expected_for_fd.st_ctime_ns
        ):
            raise RuntimeError(f"{field_name} changed while opening")
    except Exception as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    except BaseException as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    return fd


def _create_log_temp_file(directory: Path, *, prefix: str, suffix: str) -> tuple[int, int, str]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RuntimeError("secure log temporary file creation is not supported on this platform")
    try:
        parent_fd = ensure_directory_without_following_symlinks(directory, field_name="log directory")
    except OSError:
        directory_error = RuntimeError("failed to prepare log directory")
        parent_fd = -1
    else:
        directory_error = None
    if directory_error is not None:
        raise directory_error
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag | getattr(os, "O_CLOEXEC", 0)
    try:
        for _ in range(100):
            temp_name = f".{prefix}.{secrets.token_hex(8)}{suffix}"
            try:
                fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
                return fd, parent_fd, temp_name
            except FileExistsError:
                continue
        raise RuntimeError("failed to create log temporary file")
    except Exception as exc:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    except BaseException as exc:
        try:
            os.close(parent_fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise


def _same_log_temp_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        stat_module.S_ISREG(first.st_mode)
        and stat_module.S_ISREG(second.st_mode)
        and first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_mode == second.st_mode
        and getattr(first, "st_nlink", 1) == getattr(second, "st_nlink", 1)
    )


def _log_temp_stat_for_fd(fd: int) -> os.stat_result | None:
    try:
        return os.fstat(fd)
    except (OSError, ValueError):
        proc_fd_path = Path("/proc/self/fd") / str(fd)
        try:
            return os.stat(proc_fd_path)
        except (OSError, ValueError):
            return None


def _log_temp_name_matches_fd(parent_fd: int, temp_name: str, fd: int) -> bool:
    try:
        path_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
    except (OSError, ValueError):
        return False
    fd_stat = _log_temp_stat_for_fd(fd)
    if fd_stat is None:
        return False
    return _same_log_temp_identity(path_stat, fd_stat)


def _unlink_log_temp(
    parent_fd: int,
    temp_name: str,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    if not temp_name:
        return
    if expected_stat is None:
        raise RuntimeError("cannot verify log temporary file before cleanup")
    try:
        current_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_log_temp_identity(current_stat, expected_stat):
            raise RuntimeError("log temporary file changed before cleanup")
        for _ in range(100):
            cleanup_name = f"{temp_name}.{secrets.token_hex(8)}.cleanup"
            try:
                _rename_without_replacing(
                    temp_name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name="log temporary file cleanup",
                )
            except FileExistsError:
                continue
            try:
                claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not _same_log_temp_identity(claimed_stat, expected_stat):
                    raise RuntimeError("log temporary file changed before cleanup")
                os.unlink(cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                try:
                    _rename_without_replacing(
                        cleanup_name,
                        temp_name,
                        directory_fd=parent_fd,
                        field_name="log temporary file cleanup restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException as restore_error:
                    _note_cleanup_failure(exc, restore_error)
                raise
            return
        raise RuntimeError("failed to claim log temporary file cleanup path")
    except OSError:
        cleanup_error = RuntimeError("failed to remove log temporary file")
    else:
        cleanup_error = None
    if cleanup_error is not None:
        raise cleanup_error


def _rotate_active_if_needed(path: Path, *, force: bool = False) -> None:
    if path.is_symlink():
        raise RuntimeError("active log file must not be a symlink")
    if not path.exists():
        return
    file_stat = _assert_regular_unlinked_file(path, field_name="active log file")
    size = file_stat.st_size
    if not force and size < MAX_DAILY_LOG_BYTES:
        return
    for suffix in range(1, MAX_LOG_ROTATION_CANDIDATES + 1):
        candidate = path.with_name(f"{path.stem}.{suffix}{path.suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name="log directory")
            candidate_linked = False
            source_unlink_attempted = False
            source_claimed = False
            source_cleanup_name: str | None = None
            primary_error: BaseException | None = None
            try:
                rotation_stat = _assert_regular_unlinked_file(path, field_name="active log file")
                try:
                    os.link(
                        path.name,
                        candidate.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                candidate_linked = True
                _fsync_fd(parent_fd)
                current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    current_stat.st_dev != rotation_stat.st_dev
                    or current_stat.st_ino != rotation_stat.st_ino
                    or current_stat.st_mode != rotation_stat.st_mode
                ):
                    raise RuntimeError("active log changed during rotation")
                current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
                if (
                    current_stat.st_dev != rotation_stat.st_dev
                    or current_stat.st_ino != rotation_stat.st_ino
                    or current_stat.st_mode != rotation_stat.st_mode
                ):
                    raise RuntimeError("active log changed during rotation")
                # Keep candidate if unlink outcome is ambiguous; process can
                # die after the syscall but before Python records success.
                source_unlink_attempted = True
                for _ in range(100):
                    source_cleanup_name = f"{path.name}.{secrets.token_hex(8)}.cleanup"
                    try:
                        _rename_without_replacing(
                            path.name,
                            source_cleanup_name,
                            directory_fd=parent_fd,
                            field_name="active log cleanup",
                        )
                    except FileExistsError:
                        continue
                    source_claimed = True
                    break
                else:
                    raise RuntimeError("failed to claim active log cleanup path")
                claimed_source_stat = os.stat(source_cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not _same_log_inode(claimed_source_stat, rotation_stat):
                    raise RuntimeError("active log changed during rotation")
                os.unlink(source_cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                primary_error = exc
                if source_claimed and source_cleanup_name is not None:
                    try:
                        os.stat(source_cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    except BaseException as cleanup_error:
                        _note_cleanup_failure(primary_error, cleanup_error)
                    else:
                        try:
                            _rename_without_replacing(
                                source_cleanup_name,
                                path.name,
                                directory_fd=parent_fd,
                                field_name="active log cleanup restore",
                            )
                            _fsync_fd(parent_fd)
                        except BaseException as cleanup_error:
                            _note_cleanup_failure(primary_error, cleanup_error)
                if candidate_linked and not source_unlink_attempted:
                    try:
                        candidate_stat = os.stat(candidate.name, dir_fd=parent_fd, follow_symlinks=False)
                        if (
                            candidate_stat.st_dev == rotation_stat.st_dev
                            and candidate_stat.st_ino == rotation_stat.st_ino
                            and candidate_stat.st_mode == rotation_stat.st_mode
                        ):
                            os.unlink(candidate.name, dir_fd=parent_fd)
                            _fsync_fd(parent_fd)
                    except FileNotFoundError:
                        pass
                    except BaseException as cleanup_error:
                        _note_cleanup_failure(primary_error, cleanup_error)
                raise
            finally:
                try:
                    os.close(parent_fd)
                except OSError:
                    pass
                except BaseException as cleanup_error:
                    if primary_error is not None:
                        _note_cleanup_failure(primary_error, cleanup_error)
                    else:
                        raise
            return
    raise RuntimeError("failed to allocate log rotation slot")


def _compress_old_daily_logs(directory: Path, today: date) -> None:
    cutoff = today - timedelta(days=COMPRESS_AFTER_DAYS)
    for path in sorted(directory.glob("speed-of-cinnamon-*.log")):
        log_date = _daily_log_date(path)
        if log_date is None or log_date > cutoff or log_date.month != today.month or log_date.year != today.year:
            continue
        _assert_regular_unlinked_file(path, field_name="daily log file")
        _gzip_file(path, path.with_suffix(path.suffix + ".gz"))


def _merge_old_months(directory: Path, today: date) -> None:
    grouped: dict[str, list[Path]] = {}
    for path in directory.glob("speed-of-cinnamon-*.log*"):
        log_date = _daily_log_date(path)
        if log_date is None or (log_date.year == today.year and log_date.month == today.month):
            continue
        month_key = f"{log_date.year:04d}-{log_date.month:02d}"
        grouped.setdefault(month_key, []).append(path)

    for month_key, paths in grouped.items():
        archive = directory / f"speed-of-cinnamon-{month_key}.log.gz"
        existing = [archive] if archive.exists() else []
        if archive.exists():
            _assert_regular_unlinked_file(archive, field_name="monthly log archive")
        temp_fd, parent_fd, temp_name = _create_log_temp_file(directory, prefix=archive.stem, suffix=".tmp")
        temp_stat = _log_temp_stat_for_fd(temp_fd)
        archive_backup_name: str | None = None
        archive_backup_moved = False
        archive_activation_attempted = False
        archive_activation_stat: os.stat_result | None = None
        archive_transaction_active = False
        archive_rollback_safe = True
        primary_error: BaseException | None = None
        try:
            source_stats: dict[Path, os.stat_result] = {}
            try:
                raw_output = os.fdopen(temp_fd, "wb")
            except Exception as exc:
                try:
                    os.close(temp_fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                raise
            except BaseException as exc:
                try:
                    os.close(temp_fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                raise
            archive_block_error: BaseException | None = None
            try:
                output = gzip.GzipFile(fileobj=raw_output, mode="wb")
                try:
                    for path in sorted(existing + paths, key=lambda item: item.name):
                        if not path.exists():
                            continue
                        source_stat = _assert_regular_unlinked_file(path, field_name="monthly log source")
                        source_stats[path] = source_stat
                        _copy_log_content(path, output, expected_stat=source_stat)
                except BaseException as exc:
                    archive_block_error = exc
                    raise
                finally:
                    try:
                        output.close()
                    except BaseException as cleanup_error:
                        if archive_block_error is not None:
                            _note_cleanup_failure(archive_block_error, cleanup_error)
                        else:
                            archive_block_error = cleanup_error
                if archive_block_error is not None:
                    raise archive_block_error
                raw_output.flush()
                _fsync_fd(raw_output.fileno())
                if not _log_temp_name_matches_fd(parent_fd, temp_name, raw_output.fileno()):
                    raise RuntimeError("monthly log temporary archive was replaced")
            except BaseException as exc:
                if archive_block_error is None:
                    archive_block_error = exc
                raise
            finally:
                try:
                    raw_output.close()
                except BaseException as cleanup_error:
                    if archive_block_error is not None:
                        _note_cleanup_failure(archive_block_error, cleanup_error)
                    else:
                        archive_block_error = cleanup_error
            if archive_block_error is not None:
                raise archive_block_error
            archive_activation_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat_module.S_ISREG(archive_activation_stat.st_mode):
                raise RuntimeError("monthly log temporary archive must be a regular file")
            if getattr(archive_activation_stat, "st_nlink", 1) != 1:
                raise RuntimeError("monthly log temporary archive must not be hardlinked")
            archive_transaction_active = True
            if archive.exists():
                archive_stat = _assert_regular_unlinked_file(archive, field_name="monthly log archive")
                for _ in range(100):
                    candidate_name = f".{archive.name}.{secrets.token_hex(8)}.backup"
                    try:
                        os.link(
                            archive.name,
                            candidate_name,
                            src_dir_fd=parent_fd,
                            dst_dir_fd=parent_fd,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        raise RuntimeError("monthly log archive disappeared before backup activation") from None
                    except FileExistsError:
                        continue
                    try:
                        backup_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                        current_archive_stat = os.stat(archive.name, dir_fd=parent_fd, follow_symlinks=False)
                        if (
                            not stat_module.S_ISREG(backup_stat.st_mode)
                            or getattr(backup_stat, "st_nlink", 1) < 2
                            or not _same_log_inode(backup_stat, archive_stat)
                            or not stat_module.S_ISREG(current_archive_stat.st_mode)
                            or not _same_log_inode(current_archive_stat, archive_stat)
                        ):
                            raise RuntimeError("monthly log archive changed during backup activation")
                        archive_backup_name = candidate_name
                        if not _unlink_log_file_with_parent_fsync(
                            archive,
                            current_archive_stat,
                            field_name="monthly log archive",
                        ):
                            raise RuntimeError("monthly log archive disappeared before activation")
                        archive_backup_moved = True
                        _fsync_fd(parent_fd)
                        break
                    except BaseException as exc:
                        if not archive_backup_moved:
                            try:
                                os.stat(archive.name, dir_fd=parent_fd, follow_symlinks=False)
                            except FileNotFoundError:
                                archive_backup_moved = True
                            except BaseException as cleanup_error:
                                _note_cleanup_failure(exc, cleanup_error)
                            else:
                                try:
                                    candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                                    if _same_log_inode(candidate_stat, archive_stat):
                                        if _unlink_log_file_with_parent_fsync(
                                            directory / candidate_name,
                                            candidate_stat,
                                            field_name="monthly log archive backup cleanup",
                                        ):
                                            _fsync_fd(parent_fd)
                                except FileNotFoundError:
                                    pass
                                except BaseException as cleanup_error:
                                    _note_cleanup_failure(exc, cleanup_error)
                        raise
                if archive_backup_name is None:
                    raise RuntimeError("failed to allocate monthly log archive backup")
            archive_activation_attempted = True
            _rename_without_replacing(
                temp_name,
                archive.name,
                directory_fd=parent_fd,
                field_name="monthly log archive",
            )
            temp_name = ""
            archive_activation_stat = os.stat(archive.name, dir_fd=parent_fd, follow_symlinks=False)
            _fsync_fd(parent_fd)
            source_cleanup_errors: list[BaseException] = []
            for path in paths:
                try:
                    original_stat = source_stats.get(path)
                    if original_stat is None:
                        continue
                    _assert_same_log_file_identity(path, original_stat, field_name="monthly log source")
                except BaseException as source_error:
                    archive_rollback_safe = False
                    source_cleanup_errors.append(source_error)
                    continue
                try:
                    _assert_same_log_file_identity(path, original_stat, field_name="monthly log source")
                    if not _unlink_log_file_with_parent_fsync(
                        path,
                        original_stat,
                        field_name="monthly log source",
                    ):
                        raise RuntimeError("monthly log source disappeared before cleanup")
                    archive_rollback_safe = False
                except BaseException as delete_error:
                    cleanup_name = f"{path.name}.{secrets.token_hex(8)}.merged"
                    try:
                        _rename_without_replacing(
                            path.name,
                            cleanup_name,
                            directory_fd=parent_fd,
                            field_name="monthly log source",
                        )
                        archive_rollback_safe = False
                        _fsync_fd(parent_fd)
                        moved_stat = _assert_regular_unlinked_file(
                            path.with_name(cleanup_name),
                            field_name="monthly log source",
                        )
                        if (
                            moved_stat.st_dev != original_stat.st_dev
                            or moved_stat.st_ino != original_stat.st_ino
                            or moved_stat.st_mode != original_stat.st_mode
                            or moved_stat.st_size != original_stat.st_size
                            or getattr(moved_stat, "st_nlink", 1) != getattr(original_stat, "st_nlink", 1)
                            or moved_stat.st_mtime_ns != original_stat.st_mtime_ns
                        ):
                            raise RuntimeError("monthly log source changed before cleanup")
                    except OSError:
                        try:
                            current_source_stat = _assert_regular_unlinked_file(
                                path,
                                field_name="monthly log source",
                            )
                            if not _same_log_claim_identity(current_source_stat, original_stat):
                                raise RuntimeError("monthly log source changed before cleanup")
                        except BaseException:
                            archive_rollback_safe = False
                        if isinstance(delete_error, OSError):
                            raise delete_error
                        source_cleanup_errors.append(delete_error)
                        if archive_rollback_safe:
                            break
                        continue
                    except Exception:
                        archive_rollback_safe = False
                        raise
                    except BaseException as cleanup_error:
                        archive_rollback_safe = False
                        _note_cleanup_failure(delete_error, cleanup_error)
                        source_cleanup_errors.append(delete_error)
                        continue
                    try:
                        if not _unlink_log_file_with_parent_fsync(
                            path.with_name(cleanup_name),
                            moved_stat,
                            field_name="monthly log source quarantine",
                        ):
                            raise RuntimeError("monthly log source quarantine disappeared before cleanup")
                        _fsync_fd(parent_fd)
                    except OSError as cleanup_error:
                        archive_rollback_safe = False
                        if not isinstance(delete_error, OSError):
                            _note_cleanup_failure(delete_error, cleanup_error)
                            source_cleanup_errors.append(delete_error)
                            continue
                        # The archive already contains this source. Retain the
                        # moved cleanup copy rather than allowing a later run
                        # to merge the source a second time.
                        pass
                    except Exception:
                        archive_rollback_safe = False
                        raise
                    except BaseException as cleanup_error:
                        archive_rollback_safe = False
                        _note_cleanup_failure(delete_error, cleanup_error)
                        source_cleanup_errors.append(delete_error)
                        continue
                    if not isinstance(delete_error, OSError):
                        source_cleanup_errors.append(delete_error)
                    continue
                try:
                    _fsync_fd(parent_fd)
                except BaseException as source_error:
                    archive_rollback_safe = False
                    source_cleanup_errors.append(source_error)
            if source_cleanup_errors:
                primary_source_error = source_cleanup_errors[0]
                for additional_source_error in source_cleanup_errors[1:]:
                    _note_cleanup_failure(primary_source_error, additional_source_error)
                raise primary_source_error
            if archive_backup_moved and archive_backup_name is not None:
                backup_path = directory / archive_backup_name
                backup_stat = _assert_regular_unlinked_file(backup_path, field_name="monthly log archive backup")
                if not _same_log_inode(backup_stat, archive_stat):
                    raise RuntimeError("monthly log archive backup changed before deletion")
                _unlink_log_file_with_parent_fsync(
                    backup_path,
                    backup_stat,
                    field_name="monthly log archive backup",
                )
            archive_transaction_active = False
        except (gzip.BadGzipFile, EOFError, zlib.error):
            # Keep malformed archives intact so size-based cleanup can handle them.
            _unlink_log_temp(parent_fd, temp_name, expected_stat=temp_stat)
            temp_name = ""
            continue
        except BaseException as exc:
            primary_error = exc
            if archive_transaction_active and archive_rollback_safe:
                try:
                    if archive_activation_attempted and archive_activation_stat is not None:
                        try:
                            current_stat = os.stat(archive.name, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            current_stat = None
                        if current_stat is not None:
                            if (
                                current_stat.st_dev != archive_activation_stat.st_dev
                                or current_stat.st_ino != archive_activation_stat.st_ino
                                or current_stat.st_mode != archive_activation_stat.st_mode
                                or current_stat.st_size != archive_activation_stat.st_size
                                or getattr(current_stat, "st_nlink", 1)
                                != getattr(archive_activation_stat, "st_nlink", 1)
                            ):
                                raise RuntimeError("monthly log archive changed during activation rollback")
                            current_stat = os.stat(archive.name, dir_fd=parent_fd, follow_symlinks=False)
                            if (
                                current_stat.st_dev != archive_activation_stat.st_dev
                                or current_stat.st_ino != archive_activation_stat.st_ino
                                or current_stat.st_mode != archive_activation_stat.st_mode
                                or current_stat.st_size != archive_activation_stat.st_size
                                or getattr(current_stat, "st_nlink", 1)
                                != getattr(archive_activation_stat, "st_nlink", 1)
                            ):
                                raise RuntimeError("monthly log archive changed during activation rollback")
                            if not _unlink_log_file_with_parent_fsync(
                                archive,
                                archive_activation_stat,
                                field_name="monthly log archive rollback",
                            ):
                                raise RuntimeError("monthly log archive disappeared during rollback")
                            _fsync_fd(parent_fd)
                    if archive_backup_moved and archive_backup_name is not None:
                        try:
                            os.stat(archive.name, dir_fd=parent_fd, follow_symlinks=False)
                        except FileNotFoundError:
                            backup_path = directory / archive_backup_name
                            backup_stat = _assert_regular_unlinked_file(
                                backup_path,
                                field_name="monthly log archive backup",
                            )
                            if not _same_log_inode(backup_stat, archive_stat):
                                raise RuntimeError("monthly log archive backup changed during rollback")
                            _rename_without_replacing(
                                archive_backup_name,
                                archive.name,
                                directory_fd=parent_fd,
                                field_name="monthly log archive",
                            )
                            _fsync_fd(parent_fd)
                        else:
                            raise RuntimeError("monthly log archive target exists during rollback")
                except BaseException as rollback_error:
                    _note_cleanup_failure(primary_error, rollback_error)
            try:
                _unlink_log_temp(parent_fd, temp_name, expected_stat=temp_stat)
            except BaseException as cleanup_error:
                _note_cleanup_failure(primary_error, cleanup_error)
            raise
        finally:
            try:
                os.close(parent_fd)
            except OSError:
                pass
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _note_cleanup_failure(primary_error, cleanup_error)
                else:
                    raise


def _copy_stream_capped(source: Any, output: Any, *, source_path: Path) -> None:
    copied = 0
    while True:
        chunk = source.read(min(65536, MAX_TOTAL_LOG_BYTES - copied + 1))
        if not chunk:
            return
        copied += len(chunk)
        if copied > MAX_TOTAL_LOG_BYTES:
            raise RuntimeError("log source content is too large")
        output.write(chunk)


def _copy_log_content(
    path: Path,
    output: gzip.GzipFile,
    *,
    expected_stat: os.stat_result | None = None,
) -> None:
    fd = _open_log_source_file(path, field_name="log source file", expected_stat=expected_stat)
    try:
        source_file = os.fdopen(fd, "rb")
    except Exception as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    except BaseException as exc:
        try:
            os.close(fd)
        except OSError as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        except BaseException as cleanup_error:
            _note_cleanup_failure(exc, cleanup_error)
        raise
    source_primary_error: BaseException | None = None
    try:
        if path.suffix == ".gz":
            source = gzip.GzipFile(fileobj=source_file, mode="rb")
            gzip_primary_error: BaseException | None = None
            try:
                _copy_stream_capped(source, output, source_path=path)
            except BaseException as exc:
                gzip_primary_error = exc
                raise
            finally:
                try:
                    source.close()
                except BaseException as cleanup_error:
                    if gzip_primary_error is not None:
                        _note_cleanup_failure(gzip_primary_error, cleanup_error)
                    else:
                        raise
        else:
            _copy_stream_capped(source_file, output, source_path=path)
        output.write(b"\n")
    except BaseException as exc:
        source_primary_error = exc
        raise
    finally:
        try:
            source_file.close()
        except BaseException as cleanup_error:
            if source_primary_error is not None:
                _note_cleanup_failure(source_primary_error, cleanup_error)
            else:
                raise


def _assert_same_log_file_identity(path: Path, expected_stat: os.stat_result, *, field_name: str) -> None:
    current_stat = _assert_regular_unlinked_file(path, field_name=field_name)
    if (
        current_stat.st_dev != expected_stat.st_dev
        or current_stat.st_ino != expected_stat.st_ino
        or current_stat.st_size != expected_stat.st_size
        or getattr(current_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
        or current_stat.st_mtime_ns != expected_stat.st_mtime_ns
        or current_stat.st_ctime_ns != expected_stat.st_ctime_ns
    ):
        raise RuntimeError(f"{field_name} changed before deletion")


def _same_log_claim_identity(current: os.stat_result, expected: os.stat_result) -> bool:
    return (
        current.st_dev == expected.st_dev
        and current.st_ino == expected.st_ino
        and current.st_mode == expected.st_mode
        and current.st_size == expected.st_size
        and getattr(current, "st_nlink", 1) == getattr(expected, "st_nlink", 1)
        and current.st_mtime_ns == expected.st_mtime_ns
    )


def _unlink_log_file_with_parent_fsync(path: Path, expected_stat: os.stat_result, *, field_name: str) -> bool:
    parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
    primary_error: BaseException | None = None
    try:
        try:
            current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (
            current_stat.st_dev != expected_stat.st_dev
            or current_stat.st_ino != expected_stat.st_ino
            or current_stat.st_mode != expected_stat.st_mode
            or current_stat.st_size != expected_stat.st_size
            or getattr(current_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
            or current_stat.st_mtime_ns != expected_stat.st_mtime_ns
            or current_stat.st_ctime_ns != expected_stat.st_ctime_ns
        ):
            raise RuntimeError(f"{field_name} changed before deletion")
        if not stat_module.S_ISREG(current_stat.st_mode):
            raise RuntimeError(f"{field_name} must be a regular file")
        for _ in range(100):
            cleanup_name = f"{path.name}.{secrets.token_hex(8)}.cleanup"
            try:
                _rename_without_replacing(
                    path.name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name=f"{field_name} cleanup",
                )
            except FileExistsError:
                continue
            try:
                claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not stat_module.S_ISREG(claimed_stat.st_mode) or not _same_log_claim_identity(claimed_stat, current_stat):
                    raise RuntimeError(f"{field_name} changed before deletion")
                os.unlink(cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                try:
                    _rename_without_replacing(
                        cleanup_name,
                        path.name,
                        directory_fd=parent_fd,
                        field_name=f"{field_name} cleanup restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException as restore_error:
                    _note_cleanup_failure(exc, restore_error)
                raise
            return True
        raise RuntimeError(f"failed to claim {field_name} cleanup path")
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        try:
            os.close(parent_fd)
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


def _gzip_file(source: Path, target: Path) -> None:
    if target.exists():
        _assert_regular_unlinked_file(target, field_name="log target file")
    temp_fd, parent_fd, temp_name = _create_log_temp_file(target.parent, prefix=target.stem, suffix=".tmp")
    temp_stat = _log_temp_stat_for_fd(temp_fd)
    source_fd: int | None = None
    target_backup_name = ""
    target_backup_created = False
    target_backup_stat: os.stat_result | None = None
    target_existing_stat: os.stat_result | None = None
    target_temp_stat: os.stat_result | None = None
    target_activation_stat: os.stat_result | None = None
    target_activation_attempted = False
    target_removed = False
    target_transaction_active = False
    primary_error: BaseException | None = None

    def _same_target_inode(first: os.stat_result, second: os.stat_result) -> bool:
        return (
            first.st_dev,
            first.st_ino,
            first.st_mode,
            first.st_size,
            first.st_mtime_ns,
        ) == (
            second.st_dev,
            second.st_ino,
            second.st_mode,
            second.st_size,
            second.st_mtime_ns,
        )

    def _same_activated_target(first: os.stat_result, second: os.stat_result) -> bool:
        return _same_target_inode(first, second) and getattr(first, "st_nlink", 1) == getattr(second, "st_nlink", 1)

    def _unlink_target_backup() -> None:
        if not target_backup_name or target_backup_stat is None:
            raise RuntimeError("log target backup identity is unavailable")
        current_backup_stat = os.stat(target_backup_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_target_inode(current_backup_stat, target_backup_stat):
            raise RuntimeError("log target backup changed before cleanup")
        for _ in range(100):
            cleanup_name = f"{target_backup_name}.{secrets.token_hex(8)}.cleanup"
            try:
                _rename_without_replacing(
                    target_backup_name,
                    cleanup_name,
                    directory_fd=parent_fd,
                    field_name="log target backup cleanup",
                )
            except FileExistsError:
                continue
            try:
                claimed_stat = os.stat(cleanup_name, dir_fd=parent_fd, follow_symlinks=False)
                if not _same_target_inode(claimed_stat, target_backup_stat):
                    raise RuntimeError("log target backup changed before cleanup")
                os.unlink(cleanup_name, dir_fd=parent_fd)
                _fsync_fd(parent_fd)
            except BaseException as exc:
                try:
                    _rename_without_replacing(
                        cleanup_name,
                        target_backup_name,
                        directory_fd=parent_fd,
                        field_name="log target backup restore",
                    )
                    _fsync_fd(parent_fd)
                except BaseException as restore_error:
                    _note_cleanup_failure(exc, restore_error)
                raise
            return
        raise RuntimeError("log target backup cleanup path could not be claimed")

    def _assert_target_backup_identity() -> None:
        if not target_backup_name or target_backup_stat is None:
            raise RuntimeError("log target backup identity is unavailable")
        current_backup_stat = os.stat(target_backup_name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_target_inode(current_backup_stat, target_backup_stat):
            raise RuntimeError("log target backup changed during activation rollback")

    def _rollback_target_activation() -> None:
        nonlocal target_backup_created, target_backup_name, target_removed
        if not target_transaction_active:
            return
        activation_visible = False
        if target_activation_attempted:
            try:
                current_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                current_stat = None
            expected_stat = target_activation_stat or target_temp_stat
            if current_stat is not None and expected_stat is not None and _same_activated_target(current_stat, expected_stat):
                activation_visible = True
            elif target_existing_stat is None and current_stat is None:
                pass
            elif target_existing_stat is not None and current_stat is None and target_backup_created:
                target_removed = True
            elif target_removed and target_existing_stat is not None and current_stat is None:
                pass
            elif target_existing_stat is not None and current_stat is not None and _same_target_inode(current_stat, target_existing_stat):
                pass
            else:
                raise RuntimeError("log target changed during activation rollback")
            if activation_visible:
                if target_backup_created:
                    _assert_target_backup_identity()
                current_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                if expected_stat is None or not _same_activated_target(current_stat, expected_stat):
                    raise RuntimeError("log target changed during activation rollback")
                if not _unlink_log_file_with_parent_fsync(
                    target,
                    expected_stat,
                    field_name="log target rollback",
                ):
                    raise RuntimeError("log target disappeared during rollback")
                _fsync_fd(parent_fd)
        if target_backup_created:
            if not activation_visible:
                if target_removed:
                    try:
                        os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        _assert_target_backup_identity()
                        _rename_without_replacing(
                            target_backup_name,
                            target.name,
                            directory_fd=parent_fd,
                            field_name="log target",
                        )
                        target_backup_created = False
                        target_backup_name = ""
                        target_removed = False
                        _fsync_fd(parent_fd)
                    else:
                        raise RuntimeError("log target exists during activation rollback")
                else:
                    _unlink_target_backup()
                    target_backup_created = False
                    target_backup_name = ""
                    _fsync_fd(parent_fd)
            else:
                try:
                    os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                except FileNotFoundError:
                    _assert_target_backup_identity()
                    _rename_without_replacing(
                        target_backup_name,
                        target.name,
                        directory_fd=parent_fd,
                        field_name="log target",
                    )
                    target_backup_created = False
                    target_backup_name = ""
                    target_removed = False
                    _fsync_fd(parent_fd)
                else:
                    raise RuntimeError("log target exists during activation rollback")

    try:
        try:
            source_fd = _open_log_source_file(source, field_name="log source file")
            source_stat = os.fstat(source_fd)
        except Exception as exc:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                source_fd = None
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            temp_fd = -1
            raise
        except BaseException as exc:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                source_fd = None
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            temp_fd = -1
            raise
        try:
            input_file = os.fdopen(source_fd, "rb")
            source_fd = None
        except Exception as exc:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                source_fd = None
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            temp_fd = -1
            raise
        except BaseException as exc:
            if source_fd is not None:
                try:
                    os.close(source_fd)
                except OSError as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                except BaseException as cleanup_error:
                    _note_cleanup_failure(exc, cleanup_error)
                source_fd = None
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            temp_fd = -1
            raise
        try:
            raw_output = os.fdopen(temp_fd, "wb")
            temp_fd = -1
        except Exception as exc:
            try:
                input_file.close()
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            temp_fd = -1
            raise
        except BaseException as exc:
            try:
                input_file.close()
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            try:
                os.close(temp_fd)
            except OSError as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_cleanup_failure(exc, cleanup_error)
            temp_fd = -1
            raise
        block_error: BaseException | None = None
        try:
            output_file = gzip.GzipFile(fileobj=raw_output, mode="wb")
            try:
                _copy_stream_capped(input_file, output_file, source_path=source)
            except BaseException as exc:
                block_error = exc
                raise
            finally:
                try:
                    output_file.close()
                except BaseException as cleanup_error:
                    if block_error is not None:
                        _note_cleanup_failure(block_error, cleanup_error)
                    else:
                        block_error = cleanup_error
            if block_error is not None:
                raise block_error
            raw_output.flush()
            _fsync_fd(raw_output.fileno())
            if not _log_temp_name_matches_fd(parent_fd, temp_name, raw_output.fileno()):
                raise RuntimeError("log temporary archive was replaced")
        except BaseException as exc:
            if block_error is None:
                block_error = exc
            raise
        finally:
            try:
                raw_output.close()
            except BaseException as cleanup_error:
                if block_error is not None:
                    _note_cleanup_failure(block_error, cleanup_error)
                else:
                    block_error = cleanup_error
            try:
                input_file.close()
            except BaseException as cleanup_error:
                if block_error is not None:
                    _note_cleanup_failure(block_error, cleanup_error)
                else:
                    block_error = cleanup_error
        if block_error is not None:
            raise block_error
        target_temp_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat_module.S_ISREG(target_temp_stat.st_mode) or getattr(target_temp_stat, "st_nlink", 1) != 1:
            raise RuntimeError("log temporary archive must be a private regular file")
        try:
            target_existing_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            target_existing_stat = None
        target_transaction_active = True
        if target_existing_stat is not None:
            if not stat_module.S_ISREG(target_existing_stat.st_mode) or getattr(target_existing_stat, "st_nlink", 1) != 1:
                raise RuntimeError("log target file must be a private regular file")
            for _ in range(100):
                candidate_name = f".{target.name}.{secrets.token_hex(8)}.backup"
                try:
                    os.link(
                        target.name,
                        candidate_name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    continue
                try:
                    backup_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                    current_target_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
                    if (
                        not stat_module.S_ISREG(backup_stat.st_mode)
                        or getattr(backup_stat, "st_nlink", 1) < 2
                        or not _same_target_inode(backup_stat, target_existing_stat)
                        or not stat_module.S_ISREG(current_target_stat.st_mode)
                        or not _same_target_inode(current_target_stat, target_existing_stat)
                    ):
                        raise RuntimeError("log target changed during backup activation")
                    target_backup_name = candidate_name
                    target_backup_created = True
                    target_backup_stat = backup_stat
                    break
                except BaseException as exc:
                    try:
                        candidate_stat = os.stat(candidate_name, dir_fd=parent_fd, follow_symlinks=False)
                        if _same_target_inode(candidate_stat, target_existing_stat):
                            if _unlink_log_file_with_parent_fsync(
                                target.parent / candidate_name,
                                candidate_stat,
                                field_name="log target backup cleanup",
                            ):
                                _fsync_fd(parent_fd)
                    except FileNotFoundError:
                        pass
                    except BaseException as cleanup_error:
                        _note_cleanup_failure(exc, cleanup_error)
                    raise
            if not target_backup_created:
                raise RuntimeError("failed to allocate log target backup")
            _fsync_fd(parent_fd)
        target_activation_attempted = True
        if target_backup_created:
            if target_existing_stat is None:
                raise RuntimeError("log target backup lost its original inode")
            current_target_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat_module.S_ISREG(current_target_stat.st_mode)
                or not _same_target_inode(current_target_stat, target_existing_stat)
            ):
                raise RuntimeError("log target changed before activation")
            if not _unlink_log_file_with_parent_fsync(target, current_target_stat, field_name="log target"):
                raise RuntimeError("log target disappeared before activation")
            target_removed = True
        _rename_without_replacing(
            temp_name,
            target.name,
            directory_fd=parent_fd,
            field_name="log target",
        )
        temp_name = ""
        target_activation_stat = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        _fsync_fd(parent_fd)
        if target_backup_created:
            _unlink_target_backup()
            target_backup_created = False
            target_backup_name = ""
            target_transaction_active = False
            _fsync_fd(parent_fd)
        target_transaction_active = False
        _assert_same_log_file_identity(source, source_stat, field_name="log source file")
        _unlink_log_file_with_parent_fsync(source, source_stat, field_name="log source file")
    except BaseException as exc:
        primary_error = exc
        try:
            _rollback_target_activation()
        except BaseException as rollback_error:
            _note_cleanup_failure(primary_error, rollback_error)
        try:
            _unlink_log_temp(parent_fd, temp_name, expected_stat=temp_stat)
        except BaseException as cleanup_error:
            _note_cleanup_failure(primary_error, cleanup_error)
        raise
    finally:
        try:
            os.close(parent_fd)
        except OSError:
            pass
        except BaseException as cleanup_error:
            if primary_error is not None:
                _note_cleanup_failure(primary_error, cleanup_error)
            else:
                raise


def _enforce_file_size_limit(directory: Path, *, today: date | None = None) -> None:
    active = _active_log_path(directory, today=today)
    for path in directory.glob("speed-of-cinnamon-*.log"):
        file_stat = _assert_regular_unlinked_file(path, field_name="log file")
        if file_stat.st_size <= MAX_DAILY_LOG_BYTES:
            continue
        if path == active:
            _rotate_active_if_needed(path)
            continue
        if _daily_log_date(path) is not None:
            _gzip_file(path, path.with_suffix(path.suffix + ".gz"))


def _enforce_total_size_limit(directory: Path, *, today: date | None = None) -> None:
    file_info = []
    for path in directory.glob("speed-of-cinnamon-*.log*"):
        if path.name.endswith(".tmp"):
            continue
        try:
            st = path.lstat()
        except OSError:
            continue
        if stat_module.S_ISLNK(st.st_mode):
            raise RuntimeError("log file must not be a symlink")
        if not stat_module.S_ISREG(st.st_mode):
            continue
        if getattr(st, "st_nlink", 1) != 1:
            raise RuntimeError("log file must not be hardlinked")
        file_info.append((st.st_mtime, path.name, st.st_size, path, st))
    total = sum(size for _, _, size, _, _ in file_info)
    if total <= MAX_TOTAL_LOG_BYTES:
        return
    active = _active_log_path(directory, today=today)
    candidates = sorted(
        (item for item in file_info if item[3] != active),
        key=lambda item: (item[0], item[1]),
    )
    for _, _, size, path, original_stat in candidates:
        if total <= MAX_TOTAL_LOG_BYTES:
            break
        _assert_same_log_file_identity(path, original_stat, field_name="log file")
        _unlink_log_file_with_parent_fsync(path, original_stat, field_name="log file")
        total -= size


def _daily_log_date(path: Path) -> date | None:
    match = _DAILY_LOG_RE.match(path.name) or _DAILY_GZ_RE.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def _monthly_log_date(path: Path) -> date | None:
    match = _MONTHLY_GZ_RE.match(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m").date()
    except ValueError:
        return None
