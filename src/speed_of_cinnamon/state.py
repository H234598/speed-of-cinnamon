from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


MAX_STATE_FILE_BYTES = 1_000_000


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
        if "\x00" in str(path):
            raise RuntimeError("state file path contains invalid null byte")
        self.path = path

    def read(self) -> RecordingState:
        if not self.path.exists():
            return RecordingState()
        try:
            if self.path.stat().st_size > MAX_STATE_FILE_BYTES:
                return RecordingState(error="state file is too large")
        except OSError:
            return RecordingState(error="state file could not be read")
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return RecordingState(error="state file could not be read")
        return RecordingState(**{k: v for k, v in data.items() if k in RecordingState.__dataclass_fields__})

    def write(self, state: RecordingState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        state.updated_at = now_iso()
        with tempfile.NamedTemporaryFile("w", delete=False, dir=self.path.parent, encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")
            tmp_path = Path(handle.name)
        try:
            os.replace(tmp_path, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise RuntimeError(f"failed to persist state: {self.path}") from exc

    def update(self, **values: Any) -> RecordingState:
        state = self.read()
        for key, value in values.items():
            if key in RecordingState.__dataclass_fields__:
                setattr(state, key, value)
        self.write(state)
        return state


def process_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
