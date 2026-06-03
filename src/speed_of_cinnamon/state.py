from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .path_safety import (
    assert_no_symlink_ancestors,
    assert_safe_path_components,
    read_text_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_STATE_FILE_BYTES = 1_000_000
MAX_STATE_STRING_CHARS = 1_000_000
MAX_STATE_PATH_CHARS = 4_096
MAX_STATE_INT = 2_147_483_647


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    lowered = (value or "").lower()
    if "\r" in lowered or "\n" in lowered or "\\r" in lowered or "\\n" in lowered or "\\u000d" in lowered or "\\u000a" in lowered:
        return True
    for char in lowered:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return True
    return False


@dataclass
class RecordingState:
    status: str = "idle"
    pid: int | None = None
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
        if len(text.encode("utf-8")) > MAX_STATE_PATH_CHARS:
            raise RuntimeError("state file path is invalid")
        if _contains_escaped_null(text):
            raise RuntimeError("state file path contains invalid null byte")
        assert_safe_path_components(path, field_name="state file path")
        assert_no_symlink_ancestors(path, field_name="state file path")
        self.path = path

    @staticmethod
    def _sanitize_text_field(value: Any, *, field_name: str) -> str:
        if value is None:
            return ""
        if isinstance(value, bool) or not isinstance(value, str):
            raise ValueError(f"state {field_name} must be text")
        text = str(value)
        if _contains_escaped_null(text):
            raise ValueError(f"state {field_name} contains invalid null byte")
        if _contains_http_header_control_chars(text):
            raise ValueError(f"state {field_name} contains invalid control character")
        if len(text) > MAX_STATE_STRING_CHARS:
            raise ValueError(f"state {field_name} is too large (max {MAX_STATE_STRING_CHARS} characters)")
        if len(text.encode("utf-8")) > MAX_STATE_STRING_CHARS:
            raise ValueError(f"state {field_name} is too large (max {MAX_STATE_STRING_CHARS} bytes)")
        return text

    @staticmethod
    def _coerce_boolean(value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValueError(f"state inserted contains invalid boolean value: {value!r}")
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
        normalized: dict[str, Any] = {}
        for state_field in fields(RecordingState):
            field_name = state_field.name
            if field_name not in raw:
                continue
            value = raw[field_name]
            if field_name in {"status", "audio_path", "log_path", "started_at", "stopped_at", "language", "recorder", "input_device", "transcript", "transcript_path", "error", "updated_at"}:
                normalized[field_name] = StateStore._sanitize_text_field(value, field_name=field_name)
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
        if not self.path.exists():
            return RecordingState()
        try:
            if self.path.stat().st_size > MAX_STATE_FILE_BYTES:
                return RecordingState(error="state file is too large")
        except OSError:
            return RecordingState(error="state file could not be read")
        try:
            data_text = read_text_without_following_symlinks(self.path, field_name="state file path")
            if _contains_escaped_null(data_text):
                return RecordingState(error="state file could not be read")
            data = json.loads(data_text)
            if not isinstance(data, dict):
                return RecordingState(error="state file is malformed")
            normalized = StateStore._normalize_state_data(data)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return RecordingState(error="state file could not be read")
        return RecordingState(**normalized)

    def write(self, state: RecordingState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(state)
        payload["updated_at"] = now_iso()
        normalized_payload = StateStore._normalize_state_data(payload)
        rendered = json.dumps(normalized_payload, indent=2, sort_keys=True) + "\n"
        if len(rendered.encode("utf-8")) > MAX_STATE_FILE_BYTES:
            raise RuntimeError("state file is too large")
        try:
            write_text_atomically_without_following_symlinks(
                self.path,
                rendered,
                field_name="state file path",
            )
        except OSError as exc:
            raise RuntimeError(f"failed to persist state: {self.path}") from exc

    def update(self, **values: Any) -> RecordingState:
        state = self.read()
        state_fields = {state_field.name for state_field in fields(RecordingState)}
        for key, value in values.items():
            if key in state_fields:
                setattr(state, key, value)
        self.write(state)
        return self.read()


def process_is_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
