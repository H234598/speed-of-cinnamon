from __future__ import annotations

import json
import os
import fcntl
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from .path_safety import (
    assert_fd_is_private_directory,
    assert_no_symlink_ancestors,
    assert_fd_is_regular_private_file,
    assert_safe_path_components,
    ensure_directory_without_following_symlinks,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_STATE_FILE_BYTES = 1_000_000
MAX_STATE_STRING_CHARS = 1_000_000
MAX_STATE_PATH_CHARS = 4_096
MAX_STATE_INT = 2_147_483_647
VALID_STATE_STATUSES = frozenset({"idle", "recording", "recorded", "processing", "finalizing", "done", "error"})
_STATE_READ_ERRORS = frozenset(
    {
        "state file could not be read",
        "state file is malformed",
        "state file is too large",
    }
)


def _note_lock_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note(f"state lock cleanup failed: {cleanup_error}")


def _flock_retry(fd: int, operation: int) -> None:
    while True:
        try:
            fcntl.flock(fd, operation)
            return
        except InterruptedError:
            continue


def _utf8_byte_count(value: str, *, field_name: str) -> int:
    try:
        return len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} contains invalid Unicode characters") from exc


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
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


def _contains_transcript_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    for char in value:
        if char in ("\n", "\r", "\t"):
            continue
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


@dataclass
class RecordingState:
    status: str = "idle"
    pid: int | None = None
    process_identity: str = ""
    audio_path: str | None = None
    log_path: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    language: str = "en"
    recorder: str = "auto"
    input_device: str = ""
    max_seconds: int = 30
    transcript: str = ""
    transcript_path: str | None = None
    inserted: bool = False
    error: str = ""
    updated_at: str = field(default_factory=now_iso)


class StateStore:
    def __init__(self, path: Path):
        if isinstance(path, bool) or not isinstance(path, Path):
            raise RuntimeError("state file path must be a Path")
        text = str(path)
        if not text or len(text) > MAX_STATE_PATH_CHARS:
            raise RuntimeError("state file path is invalid")
        try:
            if _utf8_byte_count(text, field_name="state file path") > MAX_STATE_PATH_CHARS:
                raise RuntimeError("state file path is invalid")
        except ValueError as exc:
            raise RuntimeError("state file path is invalid") from exc
        if _contains_escaped_null(text):
            raise RuntimeError("state file path contains invalid null byte")
        if _contains_http_header_control_chars(text):
            raise RuntimeError("state file path contains invalid control character")
        assert_safe_path_components(path, field_name="state file path")
        if not path.is_absolute():
            raise RuntimeError("state file path must be absolute")
        assert_no_symlink_ancestors(path, field_name="state file path")
        self.path = path

    @contextmanager
    def _locked(self) -> Iterator[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        assert_safe_path_components(lock_path, field_name="state lock path")
        assert_no_symlink_ancestors(lock_path, field_name="state lock path")
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if nofollow_flag is None:
            raise RuntimeError("secure state lock open is not supported on this platform")
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        parent_fd = ensure_directory_without_following_symlinks(lock_path.parent, field_name="state lock directory")
        try:
            assert_fd_is_private_directory(parent_fd, field_name="state lock directory")
            fd = os.open(
                lock_path.name,
                os.O_RDWR | os.O_CREAT | nofollow_flag | nonblock_flag,
                0o600,
                dir_fd=parent_fd,
            )
        except RuntimeError as exc:
            try:
                os.close(parent_fd)
            except OSError as cleanup_error:
                _note_lock_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_lock_cleanup_failure(exc, cleanup_error)
            raise
        except Exception as exc:
            error = RuntimeError("failed to open state lock file")
            try:
                os.close(parent_fd)
            except OSError as cleanup_error:
                _note_lock_cleanup_failure(error, cleanup_error)
            except BaseException as cleanup_error:
                _note_lock_cleanup_failure(error, cleanup_error)
            raise error from exc
        except BaseException as exc:
            try:
                os.close(parent_fd)
            except OSError as cleanup_error:
                _note_lock_cleanup_failure(exc, cleanup_error)
            except BaseException as cleanup_error:
                _note_lock_cleanup_failure(exc, cleanup_error)
            raise
        primary_error: BaseException | None = None
        try:
            assert_fd_is_regular_private_file(fd, field_name="state lock file", require_private_mode=True)
            _flock_retry(fd, fcntl.LOCK_EX)
            assert_fd_is_regular_private_file(fd, field_name="state lock file", require_private_mode=True)
            yield
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            try:
                _flock_retry(fd, fcntl.LOCK_UN)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            try:
                os.close(fd)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            try:
                os.close(parent_fd)
            except BaseException as cleanup_error:
                cleanup_errors.append(cleanup_error)
            if cleanup_errors:
                if primary_error is not None:
                    for additional_error in cleanup_errors:
                        _note_lock_cleanup_failure(primary_error, additional_error)
                else:
                    raise cleanup_errors[0]

    @staticmethod
    def _sanitize_text_field(
        value: Any,
        *,
        field_name: str,
        allow_null: bool = False,
    ) -> str:
        if value is None:
            if allow_null:
                return ""
            raise ValueError(f"state {field_name} must be text")
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError(f"state {field_name} must be text")
        text = str(value)
        if _contains_escaped_null(text):
            raise ValueError(f"state {field_name} contains invalid null byte")
        if _contains_http_header_control_chars(text):
            raise ValueError(f"state {field_name} contains invalid control character")
        if len(text) > MAX_STATE_STRING_CHARS:
            raise ValueError(f"state {field_name} is too large (max {MAX_STATE_STRING_CHARS} characters)")
        if _utf8_byte_count(text, field_name=f"state {field_name}") > MAX_STATE_STRING_CHARS:
            raise ValueError(f"state {field_name} is too large (max {MAX_STATE_STRING_CHARS} bytes)")
        return text

    @staticmethod
    def _sanitize_transcript_field(value: Any) -> str:
        if value is None:
            raise ValueError("state transcript must be text")
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError("state transcript must be text")
        text = str(value)
        if _contains_escaped_null(text):
            raise ValueError("state transcript contains invalid null byte")
        if _contains_transcript_control_chars(text):
            raise ValueError("state transcript contains invalid control character")
        if len(text) > MAX_STATE_STRING_CHARS:
            raise ValueError(f"state transcript is too large (max {MAX_STATE_STRING_CHARS} characters)")
        if _utf8_byte_count(text, field_name="state transcript") > MAX_STATE_STRING_CHARS:
            raise ValueError(f"state transcript is too large (max {MAX_STATE_STRING_CHARS} bytes)")
        return text

    @staticmethod
    def _coerce_boolean(value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError("state inserted contains invalid boolean value")
        return value

    @staticmethod
    def _coerce_state_int(
        value: Any,
        *,
        field_name: str,
        min_value: int | None = None,
        max_value: int | None = None,
    ) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{field_name} must be an integer")
        if isinstance(value, float):
            raise ValueError(f"{field_name} must be an integer")
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, str):
            raw_value = value.strip()
            if not raw_value:
                raise ValueError(f"{field_name} must be an integer")
            try:
                parsed = int(raw_value)
            except ValueError as exc:
                raise ValueError(f"{field_name} must be an integer") from exc
        else:
            raise ValueError(f"{field_name} must be an integer")
        if min_value is not None and parsed < min_value:
            raise ValueError(f"{field_name} must be at least {min_value}")
        if max_value is not None and parsed > max_value:
            raise ValueError(f"{field_name} must be at most {max_value}")
        return parsed

    @staticmethod
    def _normalize_state_data(raw: dict[str, Any]) -> dict[str, Any]:
        if "status" not in raw:
            raise ValueError("state status is missing")
        normalized: dict[str, Any] = {}
        for state_field in fields(RecordingState):
            field_name = state_field.name
            if field_name not in raw:
                continue
            value = raw[field_name]
            optional_text_fields = {"audio_path", "log_path", "started_at", "stopped_at", "transcript_path"}
            if field_name == "status":
                status = StateStore._sanitize_text_field(value, field_name=field_name)
                if status not in VALID_STATE_STATUSES:
                    raise ValueError("state status is invalid")
                normalized[field_name] = status
            elif field_name in {
                "process_identity",
                "language",
                "recorder",
                "input_device",
                "error",
                "updated_at",
            }:
                normalized[field_name] = StateStore._sanitize_text_field(value, field_name=field_name)
            elif field_name == "transcript":
                normalized[field_name] = StateStore._sanitize_transcript_field(value)
            elif field_name in optional_text_fields:
                normalized[field_name] = StateStore._sanitize_text_field(
                    value, field_name=field_name, allow_null=True
                )
            elif field_name == "pid":
                normalized[field_name] = StateStore._coerce_state_int(
                    value,
                    field_name="state pid",
                    min_value=1,
                    max_value=MAX_STATE_INT,
                ) if value is not None else None
            elif field_name == "max_seconds":
                normalized[field_name] = StateStore._coerce_state_int(
                    value,
                    field_name="state max_seconds",
                    min_value=0,
                    max_value=MAX_STATE_INT,
                )
            elif field_name == "inserted":
                normalized[field_name] = StateStore._coerce_boolean(value)
        return normalized

    def read(self) -> RecordingState:
        with self._locked():
            return self._read_unlocked()

    def _read_unlocked(self) -> RecordingState:
        try:
            assert_no_symlink_ancestors(self.path, field_name="state file path")
            file_stat = self.path.lstat()
        except FileNotFoundError:
            return RecordingState()
        except (OSError, RuntimeError):
            return RecordingState(error="state file could not be read")
        if not stat.S_ISREG(file_stat.st_mode):
            return RecordingState(error="state file could not be read")
        if file_stat.st_size > MAX_STATE_FILE_BYTES:
            return RecordingState(error="state file is too large")
        try:
            data_text = read_text_without_following_symlinks(
                self.path,
                field_name="state file path",
                max_bytes=MAX_STATE_FILE_BYTES,
                require_private_mode=True,
                expected_stat=file_stat,
            )
            if _contains_escaped_null(data_text):
                return RecordingState(error="state file could not be read")
            data = json.loads(data_text)
            if not isinstance(data, dict):
                return RecordingState(error="state file is malformed")
            normalized = StateStore._normalize_state_data(data)
        except OSError as exc:
            if "too large" in str(exc):
                return RecordingState(error="state file is too large")
            return RecordingState(error="state file could not be read")
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError, MemoryError):
            return RecordingState(error="state file could not be read")
        return RecordingState(**normalized)

    def write(self, state: RecordingState) -> None:
        with self._locked():
            self._write_unlocked(state)

    def _write_unlocked(self, state: RecordingState) -> RecordingState:
        payload = asdict(state)
        payload["updated_at"] = now_iso()
        normalized_payload = StateStore._normalize_state_data(payload)
        try:
            rendered = json.dumps(normalized_payload, indent=2, sort_keys=True) + "\n"
        except (MemoryError, RecursionError) as exc:
            raise RuntimeError("state payload could not be rendered") from exc
        try:
            rendered_size = _utf8_byte_count(rendered, field_name="state payload")
        except ValueError as exc:
            raise RuntimeError("state payload is not valid UTF-8") from exc
        if rendered_size > MAX_STATE_FILE_BYTES:
            raise RuntimeError("state file is too large")
        try:
            write_text_atomically_without_following_symlinks(
                self.path,
                rendered,
                field_name="state file path",
            )
        except OSError as exc:
            raise RuntimeError("failed to persist state") from exc
        return RecordingState(**normalized_payload)

    def update(self, **values: Any) -> RecordingState:
        with self._locked():
            state = self._read_unlocked()
            if state.error in _STATE_READ_ERRORS:
                raise RuntimeError(state.error)
            state_fields = {state_field.name for state_field in fields(RecordingState)}
            for key, value in values.items():
                if key in state_fields:
                    setattr(state, key, value)
            return self._write_unlocked(state)


def process_is_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OSError, OverflowError, ValueError):
        return False
    return True
