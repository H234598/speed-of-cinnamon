from __future__ import annotations

import gzip
import json
import logging
import os
import re
import shutil
import tempfile
from itertools import islice
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TextIO

from .paths import logs_dir

LOG_LEVELS = ("off", "error", "warning", "info", "debug")
DEFAULT_LOG_LEVEL = "error"
MAX_DAILY_LOG_BYTES = 1_000_000
MAX_TOTAL_LOG_BYTES = 5_000_000
COMPRESS_AFTER_DAYS = 3
MAX_LOG_MESSAGE_CHARS = 320
MAX_LOG_FIELD_CHARS = 160
LOGGER_NAME = "speed_of_cinnamon"
HOME_DIR = str(Path.home())

_DAILY_LOG_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log$")
_DAILY_GZ_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2}-\d{2})(?:\.(\d+))?\.log\.gz$")
_MONTHLY_GZ_RE = re.compile(r"^speed-of-cinnamon-(\d{4}-\d{2})\.log\.gz$")
_TOKEN_RE = re.compile(r"(?i)\b(bearer|token|api[_-]?key|secret|password)\b\s*[:=]\s*[^,\s;]+")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[^,\s;]+")
_OPENAI_KEY_RE = re.compile(r"\b(?:sk|sess)-[A-Za-z0-9_\-]{12,}\b")
_URL_CREDENTIAL_RE = re.compile(r"([a-z][a-z0-9+.-]*://)([^/@\s:]+):([^/@\s]+)@")
_SANITIZE_HINT_RE = re.compile(
    r"(?i)(?:\b(?:bearer|token|api[_-]?key|secret|password)\b\s*[:=]\s*[^,\s;]+|\bbearer\s+[^,\s;]+|\b(?:sk|sess)-[A-Za-z0-9_\-]{12,}\b|[a-z][a-z0-9+.-]*://[^/@\s:]+:[^/@\s]+@)"
)
_SANITIZE_ESCAPE_TABLE = {
    ord("\r"): "\\r",
    ord("\n"): "\\n",
    ord("\x00"): "\\x00",
}
_SANITIZE_KEY_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_SENSITIVE_KEYWORDS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "command",
    "context",
    "key",
    "password",
    "prompt",
    "secret",
    "text",
    "token",
    "transcript",
    "vocabulary",
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
            for key, value in sorted(fields.items()):
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

    def close(self) -> None:
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record) + "\n"
            encoded = line.encode("utf-8")
            if len(encoded) > MAX_DAILY_LOG_BYTES:
                line = _oversized_record_line(record)
                encoded = line.encode("utf-8")
            if self.path.exists() and self.path.stat().st_size + len(encoded) > MAX_DAILY_LOG_BYTES:
                self.close()
                _rotate_active_if_needed(self.path, force=True)
            self._open()
            if self.stream is None:
                raise RuntimeError("failed to open log file")
            self.stream.write(line)
            self.stream.flush()
            maintain_logs(self.base_dir)
        except Exception:
            self.handleError(record)

    def _open(self) -> None:
        if self.stream is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = open(self.path, "a", encoding="utf-8")


def validate_log_level(level: str) -> str:
    if isinstance(level, bool) or not isinstance(level, str):
        raise RuntimeError("log level must be text")
    normalized = level.strip().lower()
    if normalized not in LOG_LEVELS:
        raise RuntimeError(f"log level must be one of: {', '.join(LOG_LEVELS)}")
    return normalized


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
    logger.setLevel(getattr(logging, normalized.upper()))
    directory = base_dir or logs_dir()
    maintain_logs(directory)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = _active_log_path(directory)
    _rotate_active_if_needed(log_path)

    handler = SizeCappedJsonFileHandler(log_path, directory)
    handler.setFormatter(JsonLogFormatter())
    handler.setLevel(getattr(logging, normalized.upper()))
    logger.addHandler(handler)


def log_event(level: str, event: str, **fields: object) -> None:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.disabled:
        return
    normalized = validate_log_level(level)
    if normalized == "off":
        return
    logger.log(getattr(logging, normalized.upper()), event, extra={"fields": fields})


def maintain_logs(base_dir: Path | None = None, *, today: date | None = None) -> None:
    directory = base_dir or logs_dir()
    directory.mkdir(parents=True, exist_ok=True)
    current_day = today or date.today()
    _merge_old_months(directory, current_day)
    _compress_old_daily_logs(directory, current_day)
    _enforce_file_size_limit(directory)
    _enforce_total_size_limit(directory)


def sanitize_key(key: object) -> str:
    if isinstance(key, bool) or not isinstance(key, str):
        return ""
    safe = _SANITIZE_KEY_RE.sub("_", key.strip().lower())
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
        return [sanitize_value(key, item) for item in list(value)[:8]]
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
    if (
        "\r" not in value
        and "\n" not in value
        and "\x00" not in value
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
    text = _BEARER_RE.sub("Bearer [redacted]", text)
    text = _OPENAI_KEY_RE.sub("[redacted]", text)
    text = _URL_CREDENTIAL_RE.sub(r"\1[redacted]@", text)
    if HOME_DIR and HOME_DIR != "/":
        text = text.replace(HOME_DIR, "~")
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered == "command":
        return False
    return any(keyword in lowered for keyword in _SENSITIVE_KEYWORDS)


def _safe_path(path: Path) -> str:
    try:
        return sanitize_text(str(path.expanduser()), max_chars=MAX_LOG_FIELD_CHARS)
    except RuntimeError:
        return "[invalid-path]"


def _active_log_path(directory: Path, today: date | None = None) -> Path:
    current_day = today or date.today()
    return directory / f"speed-of-cinnamon-{current_day.isoformat()}.log"


def _oversized_record_line(record: logging.LogRecord) -> str:
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": record.levelname.lower(),
        "event": "oversized_log_record_redacted",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"


def _assert_regular_unlinked_file(path: Path, *, field_name: str) -> None:
    if not isinstance(path, Path):
        raise RuntimeError(f"{field_name} must be a path")
    if path.is_symlink():
        raise RuntimeError(f"{field_name} must not be a symlink: {path}")
    if not path.exists():
        raise RuntimeError(f"{field_name} must be an existing file: {path}")
    if not path.is_file():
        raise RuntimeError(f"{field_name} must be a regular file: {path}")
    if path.stat().st_nlink != 1:
        raise RuntimeError(f"{field_name} must not be hardlinked: {path}")


def _rotate_active_if_needed(path: Path, *, force: bool = False) -> None:
    if path.is_symlink():
        raise RuntimeError(f"active log file must not be a symlink: {path}")
    if path.exists():
        _assert_regular_unlinked_file(path, field_name="active log file")
    if not path.exists() or (not force and path.stat().st_size < MAX_DAILY_LOG_BYTES):
        return
    suffix = 1
    while True:
        candidate = path.with_name(f"{path.stem}.{suffix}{path.suffix}")
        if not candidate.exists():
            path.replace(candidate)
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
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=directory) as tmp_handle:
            tmp_archive = Path(tmp_handle.name)
        try:
            with gzip.open(tmp_archive, "wb") as output:
                for path in sorted(existing + paths, key=lambda item: item.name):
                    if not path.exists():
                        continue
                    _assert_regular_unlinked_file(path, field_name="monthly log source")
                    _copy_log_content(path, output)
            if archive.exists():
                archive.unlink()
            tmp_archive.replace(archive)
        except Exception:
            try:
                tmp_archive.unlink()
            except OSError:
                pass
            raise
        for path in paths:
            if path.exists():
                path.unlink()


def _copy_log_content(path: Path, output: gzip.GzipFile) -> None:
    _assert_regular_unlinked_file(path, field_name="log source file")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rb") as source:
        shutil.copyfileobj(source, output)
        output.write(b"\n")


def _gzip_file(source: Path, target: Path) -> None:
    _assert_regular_unlinked_file(source, field_name="log source file")
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=target.parent) as tmp_handle:
        tmp_target = Path(tmp_handle.name)
    try:
        with open(source, "rb") as input_file, gzip.open(tmp_target, "wb") as output_file:
            shutil.copyfileobj(input_file, output_file)
        source.unlink()
        tmp_target.replace(target)
    except Exception:
        try:
            tmp_target.unlink()
        except OSError:
            pass
        raise


def _enforce_file_size_limit(directory: Path) -> None:
    for path in directory.glob("speed-of-cinnamon-*.log"):
        if path.stat().st_size <= MAX_DAILY_LOG_BYTES:
            continue
        _rotate_active_if_needed(path)


def _enforce_total_size_limit(directory: Path) -> None:
    files = [path for path in directory.glob("speed-of-cinnamon-*.log*") if path.is_file()]
    for path in files:
        _assert_regular_unlinked_file(path, field_name="log file")
    total = sum(path.stat().st_size for path in files)
    if total <= MAX_TOTAL_LOG_BYTES:
        return
    active = _active_log_path(directory)
    candidates = sorted(
        (path for path in files if path != active),
        key=lambda item: (item.stat().st_mtime, item.name),
    )
    for path in candidates:
        if total <= MAX_TOTAL_LOG_BYTES:
            break
        size = path.stat().st_size
        path.unlink()
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
