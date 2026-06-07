from __future__ import annotations

import gzip
import json
import logging
import os
import re
import secrets
import shutil
import stat as stat_module
import string
import time
from itertools import islice
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from .path_safety import (
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
MAX_LOG_MESSAGE_CHARS = 320
MAX_LOG_FIELD_CHARS = 160
LOG_MAINTENANCE_INTERVAL_SECONDS = 60.0
LOGGER_NAME = "speed_of_cinnamon"
HOME_DIR = str(Path.home())

_DAILY_LOG_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log$")
_DAILY_GZ_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log\.gz$")
_MONTHLY_GZ_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2})\.log\.gz$")
_TOKEN_RE = re.compile(r"(?i)\b(bearer|token|api[_ -]?key|apikey|secret|password|passwd|passphrase)\b\s*[:=]\s*[^,\s;]+")
_BARE_CREDENTIAL_RE = re.compile(
    r"(?i)\b(token|api[_ -]?key|apikey|password|passwd|passphrase)\b\s+(?!(?:is|are|was|were|contains?|must|too|missing|invalid|required)\b)[^,\s;]+"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^,\s;]+")
_OPENAI_KEY_RE = re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_\-]{12,}\b")
_SHORT_API_KEY_RE = re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b")
_URL_CREDENTIAL_RE = re.compile(r"([a-z][a-z0-9+.-]*://)([^/@\s:]+):([^@\s]+)@")
_ERROR_DETAIL_RE = re.compile(
    r"(?i)(?:\b(?:stdout|stderr)\s*:|\b(?:raw\s+)?transcript\s*(?::|\b(?:text|words|payload|for)\b)|\bprompt\s*:|command\s+output\s*:|backend\s+output\s*:)"
)
_ERROR_OUTPUT_LIKELY_RE = re.compile(r"(?i)\b(traceback|exception|at|exit\s+code|stderr|stdout|command\s+output|process\s+exited|python|failed\s+with|npm|node)\b")
_ERROR_SECRET_WORD_RE = re.compile(r"(?i)\bsecret\b")
_SANITIZE_HINT_RE = re.compile(
    r"(?i)(?:\b(?:bearer|token|api[_ -]?key|apikey|secret|password|passwd|passphrase)\b\s*[:=]\s*[^,\s;]+|\b(?:token|api[_ -]?key|apikey|password|passwd|passphrase)\b\s+(?!(?:is|are|was|were|contains?|must|too|missing|invalid|required)\b)[^,\s;]+|\bbearer\s+[^,\s;]+|\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b|[a-z][a-z0-9+.-]*://[^/@\s:]+:[^@\s]+@)"
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
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class SizeCappedJsonFileHandler(logging.Handler):
    def __init__(self, path: Path, base_dir: Path) -> None:
        super().__init__()
        self.path = path
        self.base_dir = base_dir
        self.stream: TextIO | None = None
        self._disabled = False
        self._next_maintenance_at = time.monotonic() + LOG_MAINTENANCE_INTERVAL_SECONDS

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        if self._disabled:
            return
        try:
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
        except Exception:
            self._disabled = True
            self.close()

    def _open(self) -> None:
        if self.stream is None:
            parent_fd = ensure_directory_without_following_symlinks(self.path.parent, field_name="log directory")
            try:
                os.close(parent_fd)
            except OSError:
                pass
            fd = open_file_without_following_symlinks(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
                field_name="log file",
            )
            try:
                file_stat = os.fstat(fd)
                if not stat_module.S_ISREG(file_stat.st_mode):
                    raise RuntimeError(f"log file must be a regular file: {self.path}")
                if getattr(file_stat, "st_nlink", 1) != 1:
                    raise RuntimeError(f"log file must not be hardlinked: {self.path}")
                try:
                    os.fchmod(fd, 0o600)
                except OSError:
                    pass
                self.stream = os.fdopen(fd, "a", encoding="utf-8")
            except Exception:
                os.close(fd)
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


def maintain_logs(base_dir: Path | None = None, *, today: date | None = None) -> None:
    directory = base_dir or logs_dir()
    _ensure_log_directory(directory)
    current_day = today or date.today()
    _merge_old_months(directory, current_day)
    _compress_old_daily_logs(directory, current_day)
    _enforce_file_size_limit(directory)
    _enforce_total_size_limit(directory)


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


def sanitize_text(value: str, *, max_chars: int = MAX_LOG_FIELD_CHARS) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        return "[invalid]"
    redacted_value = _OPENAI_KEY_RE.sub("[redacted]", value)
    redacted_value = _SHORT_API_KEY_RE.sub("[redacted]", redacted_value)
    if redacted_value != value:
        value = redacted_value
    if (
        not _contains_control_chars(value)
        and ":" not in value
        and "@" not in value
        and _SANITIZE_HINT_RE.search(value) is None
        and (not HOME_DIR or HOME_DIR == "/" or HOME_DIR not in value)
    ):
        if len(value) > max_chars:
            return value[:max_chars] + "...[truncated]"
        return value
    text = value.translate(_SANITIZE_ESCAPE_TABLE)
    text = _TOKEN_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BARE_CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}=[redacted]", text)
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _OPENAI_KEY_RE.sub("[redacted]", text)
    text = _SHORT_API_KEY_RE.sub("[redacted]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[redacted]@", text)
    if HOME_DIR and HOME_DIR != "/":
        text = text.replace(HOME_DIR, "~")
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _contains_control_chars(value: str) -> bool:
    return any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)


def sanitize_error_message(error: object, *, max_chars: int = MAX_LOG_MESSAGE_CHARS) -> str:
    if isinstance(error, bool) or not isinstance(error, str):
        return "[invalid]"
    failed_match = re.match(r"(?is)^(?P<command>.+?)\s+(?P<marker>failed|error)\s*:\s*(?P<details>.+)$", error)
    if failed_match:
        details = failed_match.group("details").strip()
        if (
            _ERROR_DETAIL_RE.search(details) is not None
            or _BARE_CREDENTIAL_RE.search(details) is not None
            or _ERROR_SECRET_WORD_RE.search(details) is not None
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
        details_max_chars = max(8, max_chars - len(prefix))
        details = sanitize_text(details, max_chars=details_max_chars)
        if _ERROR_SECRET_WORD_RE.search(details):
            return "[redacted error details]"
        if len(details) <= 0:
            return "[redacted error details]"
        candidate = f"{prefix}{details}"
        if len(candidate) > max_chars:
            return candidate[:max_chars] + "...[truncated]"
        return candidate
    if _ERROR_DETAIL_RE.search(error):
        return "[redacted error details]"
    if _BARE_CREDENTIAL_RE.search(error):
        return "[redacted error details]"
    sanitized = sanitize_text(error, max_chars=max(len(error), max_chars))
    sanitized = _SHORT_API_KEY_RE.sub("[redacted]", sanitized)
    if _ERROR_SECRET_WORD_RE.search(sanitized):
        return "[redacted error details]"
    if len(sanitized) > max_chars:
        return sanitized[:max_chars] + "...[truncated]"
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
    try:
        directory_fd = ensure_directory_without_following_symlinks(directory, field_name="log directory")
    except OSError as exc:
        raise RuntimeError(f"failed to prepare log directory: {directory}") from exc
    try:
        os.close(directory_fd)
    except OSError:
        pass


def _oversized_record_line(record: logging.LogRecord) -> str:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname.lower(),
        "event": "oversized_log_record_redacted",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _assert_regular_unlinked_file(path: Path, *, field_name: str) -> os.stat_result:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise RuntimeError(f"{field_name} must be an existing file: {path}")
    if stat_module.S_ISLNK(file_stat.st_mode):
        raise RuntimeError(f"{field_name} must not be a symlink: {path}")
    if not stat_module.S_ISREG(file_stat.st_mode):
        raise RuntimeError(f"{field_name} must be a regular file: {path}")
    if getattr(file_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{field_name} must not be hardlinked: {path}")
    return file_stat


def _open_log_source_file(path: Path, *, field_name: str) -> int:
    _assert_regular_unlinked_file(path, field_name=field_name)
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name=field_name)
    except OSError as exc:
        raise RuntimeError(f"{field_name} is not readable: {path}") from exc
    try:
        assert_fd_is_regular_private_file(fd, field_name=field_name)
    except Exception:
        os.close(fd)
        raise
    return fd


def _create_log_temp_file(directory: Path, *, prefix: str, suffix: str) -> tuple[int, int, str]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RuntimeError("secure log temporary file creation is not supported on this platform")
    try:
        parent_fd = ensure_directory_without_following_symlinks(directory, field_name="log directory")
    except OSError as exc:
        raise RuntimeError(f"failed to prepare log directory: {directory}") from exc
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
    try:
        for _ in range(100):
            temp_name = f".{prefix}.{secrets.token_hex(8)}{suffix}"
            try:
                fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
                return fd, parent_fd, temp_name
            except FileExistsError:
                continue
        raise RuntimeError("failed to create log temporary file")
    except Exception:
        os.close(parent_fd)
        raise


def _log_temp_name_matches_fd(parent_fd: int, temp_name: str, fd: int) -> bool:
    try:
        path_stat = os.stat(temp_name, dir_fd=parent_fd, follow_symlinks=False)
        fd_stat = os.fstat(fd)
    except OSError:
        return False
    return (
        stat_module.S_ISREG(path_stat.st_mode)
        and path_stat.st_dev == fd_stat.st_dev
        and path_stat.st_ino == fd_stat.st_ino
    )


def _unlink_log_temp(parent_fd: int, temp_name: str) -> None:
    if not temp_name:
        return
    try:
        os.unlink(temp_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as exc:
        raise RuntimeError("failed to remove log temporary file") from exc


def _rotate_active_if_needed(path: Path, *, force: bool = False) -> None:
    if path.is_symlink():
        raise RuntimeError(f"active log file must not be a symlink: {path}")
    if not path.exists():
        return
    file_stat = _assert_regular_unlinked_file(path, field_name="active log file")
    size = file_stat.st_size
    if not force and size < MAX_DAILY_LOG_BYTES:
        return
    suffix = 1
    while True:
        candidate = path.with_name(f"{path.stem}.{suffix}{path.suffix}")
        if not candidate.exists() and not candidate.is_symlink():
            parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name="log directory")
            try:
                _assert_regular_unlinked_file(path, field_name="active log file")
                os.replace(path.name, candidate.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            return
        suffix += 1


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
        try:
            source_stats: dict[Path, os.stat_result] = {}
            try:
                raw_output = os.fdopen(temp_fd, "wb")
            except Exception:
                os.close(temp_fd)
                raise
            with raw_output:
                with gzip.GzipFile(fileobj=raw_output, mode="wb") as output:
                    for path in sorted(existing + paths, key=lambda item: item.name):
                        if not path.exists():
                            continue
                        source_stats[path] = _assert_regular_unlinked_file(path, field_name="monthly log source")
                        _copy_log_content(path, output)
                raw_output.flush()
                os.fsync(raw_output.fileno())
                if not _log_temp_name_matches_fd(parent_fd, temp_name, raw_output.fileno()):
                    raise RuntimeError("monthly log temporary archive was replaced")
            os.replace(temp_name, archive.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            temp_name = ""
            os.fsync(parent_fd)
            for path in paths:
                original_stat = source_stats.get(path)
                if original_stat is None:
                    continue
                _assert_same_log_file_identity(path, original_stat, field_name="monthly log source")
                os.unlink(path.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
        except Exception:
            _unlink_log_temp(parent_fd, temp_name)
            raise
        finally:
            os.close(parent_fd)


def _copy_log_content(path: Path, output: gzip.GzipFile) -> None:
    fd = _open_log_source_file(path, field_name="log source file")
    with os.fdopen(fd, "rb") as source_file:
        if path.suffix == ".gz":
            with gzip.GzipFile(fileobj=source_file, mode="rb") as source:
                shutil.copyfileobj(source, output)
        else:
            shutil.copyfileobj(source_file, output)
        output.write(b"\n")


def _assert_same_log_file_identity(path: Path, expected_stat: os.stat_result, *, field_name: str) -> None:
    current_stat = _assert_regular_unlinked_file(path, field_name=field_name)
    if (
        current_stat.st_dev != expected_stat.st_dev
        or current_stat.st_ino != expected_stat.st_ino
        or getattr(current_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
    ):
        raise RuntimeError(f"{field_name} changed before deletion: {path}")


def _unlink_log_file_with_parent_fsync(path: Path, expected_stat: os.stat_result, *, field_name: str) -> bool:
    parent_fd = ensure_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
    try:
        try:
            current_stat = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (
            current_stat.st_dev != expected_stat.st_dev
            or current_stat.st_ino != expected_stat.st_ino
            or current_stat.st_mode != expected_stat.st_mode
            or getattr(current_stat, "st_nlink", 1) != getattr(expected_stat, "st_nlink", 1)
        ):
            raise RuntimeError(f"{field_name} changed before deletion: {path}")
        if not stat_module.S_ISREG(current_stat.st_mode):
            raise RuntimeError(f"{field_name} must be a regular file: {path}")
        os.unlink(path.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return True
    finally:
        os.close(parent_fd)


def _gzip_file(source: Path, target: Path) -> None:
    if target.exists():
        _assert_regular_unlinked_file(target, field_name="log target file")
    temp_fd, parent_fd, temp_name = _create_log_temp_file(target.parent, prefix=target.stem, suffix=".tmp")
    try:
        try:
            source_fd = _open_log_source_file(source, field_name="log source file")
            source_stat = os.fstat(source_fd)
        except Exception:
            os.close(temp_fd)
            raise
        try:
            input_file = os.fdopen(source_fd, "rb")
        except Exception:
            os.close(source_fd)
            os.close(temp_fd)
            raise
        try:
            raw_output = os.fdopen(temp_fd, "wb")
        except Exception:
            input_file.close()
            os.close(temp_fd)
            raise
        with input_file, raw_output:
            with gzip.GzipFile(fileobj=raw_output, mode="wb") as output_file:
                shutil.copyfileobj(input_file, output_file)
            raw_output.flush()
            os.fsync(raw_output.fileno())
            if not _log_temp_name_matches_fd(parent_fd, temp_name, raw_output.fileno()):
                raise RuntimeError("log temporary archive was replaced")
        os.replace(temp_name, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        temp_name = ""
        os.fsync(parent_fd)
        _assert_same_log_file_identity(source, source_stat, field_name="log source file")
        _unlink_log_file_with_parent_fsync(source, source_stat, field_name="log source file")
    except Exception:
        _unlink_log_temp(parent_fd, temp_name)
        raise
    finally:
        os.close(parent_fd)


def _enforce_file_size_limit(directory: Path) -> None:
    for path in directory.glob("speed-of-cinnamon-*.log"):
        file_stat = _assert_regular_unlinked_file(path, field_name="log file")
        if file_stat.st_size <= MAX_DAILY_LOG_BYTES:
            continue
        _rotate_active_if_needed(path)


def _enforce_total_size_limit(directory: Path) -> None:
    file_info = []
    for path in directory.glob("speed-of-cinnamon-*.log*"):
        if path.name.endswith(".tmp"):
            continue
        try:
            st = path.lstat()
        except OSError:
            continue
        if stat_module.S_ISLNK(st.st_mode):
            raise RuntimeError(f"log file must not be a symlink: {path}")
        if not stat_module.S_ISREG(st.st_mode):
            continue
        if getattr(st, "st_nlink", 1) != 1:
            raise RuntimeError(f"log file must not be hardlinked: {path}")
        file_info.append((st.st_mtime, path.name, st.st_size, path, st))
    total = sum(size for _, _, size, _, _ in file_info)
    if total <= MAX_TOTAL_LOG_BYTES:
        return
    active = _active_log_path(directory)
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
