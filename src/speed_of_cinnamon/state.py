from __future__ import annotations

import json
import math
import os
import fcntl
import time
import re
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
MAX_PENDING_CLEANUP_OWNER_PATHS = 128
MAX_PENDING_CLEANUP_OWNER_PATH_CHARS = 240
MAX_PENDING_CLEANUP_BACKUP_ENTRIES = 4_096
MAX_PENDING_CLEANUP_BACKUP_ENTRY_CHARS = 384
MAX_CLEANUP_BACKUP_IDENTITY_VALUE = 18_446_744_073_709_551_615
MAX_STATE_INT = 2_147_483_647
STATE_LOCK_TIMEOUT_SECONDS = 5.0
STATE_LOCK_RETRY_SECONDS = 0.01
VALID_STATE_STATUSES = frozenset({"idle", "recording", "recorded", "processing", "finalizing", "done", "error"})
_CLEANUP_BACKUP_BASENAME_PATTERN = re.compile(
    r"(?:"
    r"\.cleanup\.[0-9a-f]{16}\.[0-9a-f]{16}"
    r"|"
    r"\.cleanup\.v2\.[0-9a-f]{32}\.[0-9a-f]{32}\.[0-9a-f]{32}"
    r"|"
    r"\.cleanup\.v3\.[0-9a-f]{32}\.[0-9a-f]{32}\."
    r"(?:intent|staged|wiping)\.[0-9a-f]{64}"
    r"|"
    r"\.cleanup\.v3\.[0-9a-f]{32}\.[0-9a-f]{32}\."
    r"(?:intent|claimed|prepared|armed|wiping)\.[0-9a-f]{64}\.[0-9a-f]{32}"
    r")\.bak"
)


def _reject_non_finite_json_number(value: str) -> object:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key is not allowed")
        result[key] = value
    return result

_STATE_READ_ERRORS = frozenset(
    {
        "state file could not be read",
        "state file is malformed",
        "state file is too large",
    }
)


def is_state_read_error(error: object) -> bool:
    return isinstance(error, str) and error in _STATE_READ_ERRORS


def _note_lock_cleanup_failure(primary: BaseException, cleanup_error: BaseException) -> None:
    primary.add_note("state lock cleanup failed")


def _preferred_lock_error(
    primary_error: BaseException | None,
    cleanup_errors: list[BaseException],
) -> BaseException | None:
    if isinstance(primary_error, (KeyboardInterrupt, SystemExit)):
        return primary_error
    for cleanup_error in cleanup_errors:
        if isinstance(cleanup_error, (KeyboardInterrupt, SystemExit)):
            return cleanup_error
    if primary_error is not None:
        return primary_error
    return cleanup_errors[0] if cleanup_errors else None


def _flock_retry(fd: int, operation: int, *, timeout_seconds: float | None = None) -> None:
    if timeout_seconds is None:
        raise RuntimeError("state lock timeout is required")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
        raise RuntimeError("state lock timeout is invalid")
    try:
        finite_timeout = math.isfinite(timeout_seconds)
    except OverflowError:
        finite_timeout = False
    if not finite_timeout or timeout_seconds <= 0:
        raise RuntimeError("state lock timeout is invalid")
    if timeout_seconds > STATE_LOCK_TIMEOUT_SECONDS:
        raise RuntimeError("state lock timeout exceeds safe limit")
    if operation & fcntl.LOCK_UN:
        fcntl.flock(fd, operation)
        return
    deadline = time.monotonic() + timeout_seconds
    nonblocking_operation = operation | fcntl.LOCK_NB
    first_attempt = True
    while True:
        if not first_attempt and deadline - time.monotonic() <= 0:
            raise RuntimeError("state lock acquisition timed out") from None
        first_attempt = False
        try:
            fcntl.flock(fd, nonblocking_operation)
            return
        except (BlockingIOError, InterruptedError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("state lock acquisition timed out") from None
            time.sleep(min(STATE_LOCK_RETRY_SECONDS, remaining))


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
    pending_cleanup_owner_paths: tuple[str, ...] = ()
    pending_cleanup_restore_owner_paths: tuple[str, ...] = ()
    pending_cleanup_backup_entries: tuple[str, ...] = ()
    cleanup_backup_journal_overflow: bool = False
    cleanup_backup_journal_restore: bool = False
    recorder_scope: str = ""


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
    def _locked(self, *, shared: bool = False) -> Iterator[None]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        assert_safe_path_components(lock_path, field_name="state lock path")
        assert_no_symlink_ancestors(lock_path, field_name="state lock path")
        nofollow_flag = getattr(os, "O_NOFOLLOW", None)
        if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
            raise RuntimeError("secure state lock open is not supported on this platform")
        nonblock_flag = getattr(os, "O_NONBLOCK", 0)
        parent_fd = ensure_directory_without_following_symlinks(
            lock_path.parent,
            field_name="state lock directory",
        )
        fd: int | None = None
        primary_error: BaseException | None = None
        primary_cause: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        try:
            try:
                assert_fd_is_private_directory(
                    parent_fd,
                    field_name="state lock directory",
                )
                fd = os.open(
                    lock_path.name,
                    os.O_RDWR
                    | os.O_CREAT
                    | nofollow_flag
                    | nonblock_flag
                    | getattr(os, "O_CLOEXEC", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except BaseException as exc:
                if isinstance(exc, RuntimeError) or not isinstance(
                    exc,
                    Exception,
                ):
                    primary_error = exc
                else:
                    primary_error = RuntimeError(
                        "failed to open state lock file"
                    )
                    primary_cause = exc
            if primary_error is None:
                try:
                    assert fd is not None
                    assert_fd_is_regular_private_file(
                        fd,
                        field_name="state lock file",
                        require_private_mode=True,
                    )
                    _flock_retry(
                        fd,
                        fcntl.LOCK_SH if shared else fcntl.LOCK_EX,
                        timeout_seconds=STATE_LOCK_TIMEOUT_SECONDS,
                    )
                    assert_fd_is_regular_private_file(
                        fd,
                        field_name="state lock file",
                        require_private_mode=True,
                    )
                    yield
                except BaseException as exc:
                    primary_error = exc
        finally:
            if fd is not None:
                try:
                    _flock_retry(
                        fd,
                        fcntl.LOCK_UN,
                        timeout_seconds=STATE_LOCK_TIMEOUT_SECONDS,
                    )
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
            selected_error = _preferred_lock_error(
                primary_error,
                cleanup_errors,
            )
            if selected_error is not None:
                if (
                    primary_error is not None
                    and selected_error is not primary_error
                ):
                    selected_error.add_note(
                        "state lock primary operation failed"
                    )
                for additional_error in cleanup_errors:
                    if additional_error is not selected_error:
                        _note_lock_cleanup_failure(
                            selected_error,
                            additional_error,
                        )
                if (
                    selected_error is primary_error
                    and primary_cause is not None
                ):
                    raise selected_error from primary_cause
                raise selected_error from None

    @staticmethod
    def _sanitize_text_field(
        value: Any,
        *,
        field_name: str,
        allow_null: bool = False,
    ) -> str | None:
        if value is None:
            if allow_null:
                return None
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
    def _sanitize_pending_cleanup_owner_paths(
        value: Any,
        *,
        field_name: str = "pending_cleanup_owner_paths",
    ) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"state {field_name} must be a list")
        if len(value) > MAX_PENDING_CLEANUP_OWNER_PATHS:
            raise ValueError(
                f"state {field_name} contains too many paths "
                f"(max {MAX_PENDING_CLEANUP_OWNER_PATHS})"
            )

        normalized: list[str] = []
        seen: set[Path] = set()
        for item in value:
            text = StateStore._sanitize_text_field(
                item,
                field_name=f"{field_name} entry",
            )
            if text is None or not text:
                raise ValueError(
                    f"state {field_name} contains an empty path"
                )
            if len(text) > MAX_PENDING_CLEANUP_OWNER_PATH_CHARS:
                raise ValueError(
                    f"state {field_name} contains an oversized path"
                )
            if (
                _utf8_byte_count(
                    text,
                    field_name=f"state {field_name} entry",
                )
                > MAX_PENDING_CLEANUP_OWNER_PATH_CHARS
            ):
                raise ValueError(
                    f"state {field_name} contains an oversized path"
                )

            owner_path = Path(text)
            if (
                not owner_path.is_absolute()
                or not owner_path.name
                or ".." in owner_path.parts
                or str(owner_path) != text
            ):
                raise ValueError(
                    f"state {field_name} contains an invalid path"
                )
            if owner_path in seen:
                raise ValueError(
                    f"state {field_name} contains a duplicate path"
                )
            seen.add(owner_path)
            normalized.append(text)

        return tuple(normalized)

    @staticmethod
    def _sanitize_pending_cleanup_backup_entries(
        value: Any,
    ) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError(
                "state pending_cleanup_backup_entries must be a list"
            )
        if len(value) > MAX_PENDING_CLEANUP_BACKUP_ENTRIES:
            raise ValueError(
                "state pending_cleanup_backup_entries contains too many "
                f"entries (max {MAX_PENDING_CLEANUP_BACKUP_ENTRIES})"
            )

        normalized: list[str] = []
        seen: set[str] = set()
        seen_basenames: set[str] = set()
        for item in value:
            text = StateStore._sanitize_text_field(
                item,
                field_name="pending_cleanup_backup_entries entry",
            )
            if (
                text is None
                or not text
                or len(text) > MAX_PENDING_CLEANUP_BACKUP_ENTRY_CHARS
                or _utf8_byte_count(
                    text,
                    field_name="state pending_cleanup_backup_entries entry",
                )
                > MAX_PENDING_CLEANUP_BACKUP_ENTRY_CHARS
            ):
                raise ValueError(
                    "state pending_cleanup_backup_entries contains an "
                    "invalid entry"
                )
            parts = text.split("|")
            if (
                len(parts) != 8
                or _CLEANUP_BACKUP_BASENAME_PATTERN.fullmatch(parts[0])
                is None
            ):
                raise ValueError(
                    "state pending_cleanup_backup_entries contains an "
                    "invalid entry"
                )
            for identity_value in parts[1:]:
                if (
                    not identity_value
                    or not identity_value.isascii()
                    or not identity_value.isdecimal()
                ):
                    raise ValueError(
                        "state pending_cleanup_backup_entries contains an "
                        "invalid identity"
                    )
                parsed_identity = int(identity_value)
                if (
                    str(parsed_identity) != identity_value
                    or parsed_identity > MAX_CLEANUP_BACKUP_IDENTITY_VALUE
                ):
                    raise ValueError(
                        "state pending_cleanup_backup_entries contains an "
                        "invalid identity"
                    )
            if text in seen or parts[0] in seen_basenames:
                raise ValueError(
                    "state pending_cleanup_backup_entries contains a "
                    "duplicate entry"
                )
            seen.add(text)
            seen_basenames.add(parts[0])
            normalized.append(text)

        return tuple(normalized)

    @staticmethod
    def _coerce_boolean(
        value: Any,
        *,
        field_name: str = "inserted",
    ) -> bool:
        if not isinstance(value, bool):
            raise ValueError(
                f"state {field_name} contains invalid boolean value"
            )
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
        known_fields = {state_field.name for state_field in fields(RecordingState)}
        unknown_fields = set(raw) - known_fields
        if unknown_fields:
            raise ValueError("state contains unknown fields")
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
                "recorder_scope",
            }:
                normalized[field_name] = StateStore._sanitize_text_field(value, field_name=field_name)
            elif field_name == "transcript":
                normalized[field_name] = StateStore._sanitize_transcript_field(value)
            elif field_name in {
                "pending_cleanup_owner_paths",
                "pending_cleanup_restore_owner_paths",
            }:
                normalized[field_name] = (
                    StateStore._sanitize_pending_cleanup_owner_paths(
                        value,
                        field_name=field_name,
                    )
                )
            elif field_name == "pending_cleanup_backup_entries":
                normalized[field_name] = (
                    StateStore._sanitize_pending_cleanup_backup_entries(value)
                )
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
            elif field_name in {
                "inserted",
                "cleanup_backup_journal_overflow",
                "cleanup_backup_journal_restore",
            }:
                normalized[field_name] = StateStore._coerce_boolean(
                    value,
                    field_name=field_name,
                )
        if normalized.get("pid") is None:
            # A cgroup scope is meaningful only while its leader identity is
            # persisted; never carry an ownership token into terminal state.
            normalized["recorder_scope"] = ""
        pending_entries = normalized.get(
            "pending_cleanup_backup_entries",
            (),
        )
        restore_owners = normalized.get(
            "pending_cleanup_restore_owner_paths",
            (),
        )
        if restore_owners and (
            len(restore_owners) != len(pending_entries)
            or any(
                not entry.split("|", 1)[0].startswith(".cleanup.v2.")
                for entry in pending_entries
            )
            or normalized.get("pending_cleanup_owner_paths", ())
            or normalized.get(
                "cleanup_backup_journal_overflow",
                False,
            )
            or not normalized.get("cleanup_backup_journal_restore", False)
            or normalized["status"] not in {"finalizing", "error"}
            or normalized.get("pid") is not None
            or bool(normalized.get("process_identity", ""))
            or not (
                normalized.get("audio_path")
                or normalized.get("log_path")
            )
        ):
            raise ValueError(
                "state cleanup backup restore owner journal is invalid"
            )
        if normalized.get("cleanup_backup_journal_restore", False):
            pending_owners = normalized.get(
                "pending_cleanup_owner_paths",
                (),
            )
            if (
                not restore_owners
                or len(pending_entries) > MAX_PENDING_CLEANUP_OWNER_PATHS
                or pending_owners
                or normalized.get(
                    "cleanup_backup_journal_overflow",
                    False,
                )
                or normalized["status"] not in {"finalizing", "error"}
                or normalized.get("pid") is not None
                or bool(normalized.get("process_identity", ""))
                or not (
                    normalized.get("audio_path")
                    or normalized.get("log_path")
                )
            ):
                raise ValueError(
                    "state cleanup backup restore journal is invalid"
                )
        return normalized

    def read(self) -> RecordingState:
        with self._locked(shared=True):
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
            data = json.loads(
                data_text,
                parse_constant=_reject_non_finite_json_number,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
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

    @staticmethod
    def _render_state_for_write(
        state: RecordingState,
    ) -> tuple[dict[str, Any], str, int]:
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
        return normalized_payload, rendered, rendered_size

    @staticmethod
    def write_would_fit(state: RecordingState) -> bool:
        try:
            _payload, _rendered, rendered_size = (
                StateStore._render_state_for_write(state)
            )
        except (RuntimeError, TypeError, ValueError, MemoryError, RecursionError):
            return False
        return rendered_size <= MAX_STATE_FILE_BYTES

    def _write_unlocked(self, state: RecordingState) -> RecordingState:
        normalized_payload, rendered, rendered_size = (
            StateStore._render_state_for_write(state)
        )
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
            if is_state_read_error(state.error):
                raise RuntimeError(state.error)
            state_fields = {state_field.name for state_field in fields(RecordingState)}
            if set(values) - state_fields:
                raise ValueError("state update contains unknown fields")
            for key, value in values.items():
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
