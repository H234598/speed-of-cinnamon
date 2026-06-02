from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon.state import (
    MAX_STATE_INT,
    MAX_STATE_FILE_BYTES,
    MAX_STATE_STRING_CHARS,
    MAX_STATE_PATH_CHARS,
    RecordingState,
    StateStore,
    process_is_alive,
    _contains_escaped_null,
)


class StateStoreTest(unittest.TestCase):
    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            _contains_escaped_null(1)  # type: ignore[arg-type]

    def test_state_store_rejects_non_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be a Path"):
            StateStore("state.json")  # type: ignore[arg-type]

    def test_state_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            StateStore(Path("state\x00.json"))

    def test_state_store_rejects_escaped_null_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            StateStore(Path("state\\\\x00.json"))

    def test_state_store_rejects_oversized_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "state file path is invalid"):
            StateStore(Path("a" * (MAX_STATE_PATH_CHARS + 1)))

    def test_state_store_rejects_oversized_path_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.state.MAX_STATE_PATH_CHARS", 4):
            with self.assertRaisesRegex(RuntimeError, "state file path is invalid"):
                StateStore(Path("é" * 3))

    def test_missing_state_defaults_to_idle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = StateStore(Path(tmp) / "state.json").read()
        self.assertEqual(state.status, "idle")
        self.assertEqual(state.transcript, "")

    def test_write_and_update_are_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.update(status="recording", pid=123, language="de")
            self.assertEqual(state.status, "recording")
            loaded = store.read()
        self.assertEqual(loaded.pid, 123)
        self.assertEqual(loaded.language, "de")

    def test_state_roundtrip_preserves_text_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            store.write(RecordingState(transcript="  hello  ", audio_path=" /tmp/audio.wav "))
            loaded = store.read()
        self.assertEqual(loaded.transcript, "  hello  ")
        self.assertEqual(loaded.audio_path, " /tmp/audio.wav ")

    @mock.patch("speed_of_cinnamon.state.os.replace", side_effect=OSError("disk full"))
    def test_write_raises_runtime_error_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(RuntimeError, "failed to persist state:"):
                store.write(store.read())
        mocked_replace.assert_called_once()

    def test_write_does_not_mutate_input_state_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state = RecordingState(updated_at="original")
            StateStore(Path(tmp) / "state.json").write(state)
        self.assertEqual(state.updated_at, "original")

    def test_read_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("x" * (MAX_STATE_FILE_BYTES + 1), encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is too large")

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_read_uses_secure_open_flags(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.status, "idle")
        self.assertTrue(mocked_open.called)
        self.assertTrue(
            any(
                Path(args[0]) == path and isinstance(args[1], int) and args[1] & os.O_NOFOLLOW
                for args, _ in mocked_open.call_args_list
            )
        )

    def test_read_rejects_invalid_utf8_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_bytes(b"\xff")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_escaped_x00_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle\\\\x00"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_null_byte_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"idle\\u0000"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_control_char_text_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"status":"id\\u000ale"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_non_object_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('["idle"]', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is malformed")

    def test_read_rejects_oversized_long_text_state(self) -> None:
        long_value = "X" * (MAX_STATE_STRING_CHARS + 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(f'{{"status":"{long_value}"}}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is too large")

    def test_write_rejects_invalid_text_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(ValueError, "state status contains invalid null byte"):
                store.write(RecordingState(status="oops\x00"))

    def test_write_rejects_control_char_text_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(ValueError, "state status contains invalid control character"):
                store.write(RecordingState(status="oops\rextra"))

    def test_write_rejects_oversized_state(self) -> None:
        long_value = "Y" * (MAX_STATE_STRING_CHARS + 5)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            state = store.read()
            state.transcript = long_value
            with self.assertRaisesRegex(ValueError, "is too large"):
                store.update(transcript=long_value)

    def test_sanitize_text_field_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.state.MAX_STATE_STRING_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "is too large"):
                StateStore._sanitize_text_field("😀" * 2, field_name="status")

    def test_write_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            state = StateStore(path).read()
            state.status = "done"
            StateStore(path).write(state)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_sanitize_text_field_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            StateStore._sanitize_text_field(12, field_name="status")

    def test_process_is_alive_rejects_non_int(self) -> None:
        self.assertFalse(process_is_alive("123"))  # type: ignore[arg-type]
        self.assertFalse(process_is_alive(True))

    def test_read_rejects_invalid_boolean_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"inserted":"maybe"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_string_boolean_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"inserted":"true"}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_integer_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"inserted":1}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_boolean_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"pid": true}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_boolean_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"max_seconds": true}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_float_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"pid": 12.5}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_float_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"max_seconds": 12.5}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_negative_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"pid": -1}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_negative_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"max_seconds": -5}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_oversized_pid_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(f'{{"pid": {MAX_STATE_INT + 1}}}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_oversized_max_seconds_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(f'{{"max_seconds": {MAX_STATE_INT + 1}}}', encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file could not be read")


if __name__ == "__main__":
    unittest.main()
