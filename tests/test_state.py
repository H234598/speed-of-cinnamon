from __future__ import annotations

import fcntl
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

    def test_state_store_rejects_parent_traversal_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "unsafe path component"):
            StateStore(Path("../outside/state.json"))
        with self.assertRaisesRegex(RuntimeError, "unsafe path component"):
            StateStore(Path("state/../state.json"))

    def test_state_store_rejects_relative_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "state file path must be absolute"):
            StateStore(Path("./state.json"))

    def test_state_store_rejects_control_character_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid control character"):
            StateStore(Path("state\x85spoof.json"))

    def test_state_store_rejects_escaped_control_character_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid control character"):
            StateStore(Path("state\\x85spoof.json"))

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
            self.assertNotEqual(state.updated_at, "")
            loaded = store.read()
        self.assertEqual(loaded.pid, 123)
        self.assertEqual(loaded.language, "de")

    def test_update_reads_after_lock_to_avoid_lost_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            lock_acquired = False
            original_read = StateStore.read

            def fake_flock(fd: int, operation: int) -> None:
                nonlocal lock_acquired
                if operation == fcntl.LOCK_EX:
                    lock_acquired = True

            def guarded_read(target: StateStore) -> RecordingState:
                self.assertTrue(lock_acquired)
                return original_read(target)

            with (
                mock.patch("speed_of_cinnamon.state.fcntl.flock", side_effect=fake_flock),
                mock.patch.object(StateStore, "read", guarded_read),
            ):
                state = store.update(status="recording", language="de")

        self.assertEqual(state.status, "recording")
        self.assertEqual(state.language, "de")

    def test_state_lock_rejects_hardlinked_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            lock_path = path.with_name(f".{path.name}.lock")
            backing = Path(tmp) / "foreign-lock"
            backing.write_text("lock\n", encoding="utf-8")
            try:
                os.link(backing, lock_path)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(RuntimeError, "must not be hardlinked"):
                StateStore(path).write(RecordingState(status="recording"))

            self.assertTrue(lock_path.exists())
            self.assertTrue(backing.exists())

    def test_update_returns_persisted_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            state = store.update(status="recording")
            self.assertNotEqual(state.updated_at, "")
            self.assertEqual(state.updated_at, store.read().updated_at)

    def test_update_avoids_second_read_after_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.json")
            with mock.patch.object(store, "read", wraps=store.read) as mocked_read:
                state = store.update(status="recording")

        self.assertEqual(state.status, "recording")
        mocked_read.assert_called_once()

    def test_invalid_boolean_error_does_not_echo_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "^state inserted contains invalid boolean value$") as raised:
            StateStore._coerce_boolean("secret-token")

        self.assertNotIn("secret-token", str(raised.exception))

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
            with self.assertRaisesRegex(RuntimeError, "^failed to persist state$"):
                store.write(store.read())
        mocked_replace.assert_called_once()

    @mock.patch("speed_of_cinnamon.path_safety.os.open", wraps=os.open)
    def test_write_uses_secure_directory_relative_replace(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            StateStore(path).write(RecordingState(status="done"))

        self.assertTrue(
            any(
                isinstance(args[0], str)
                and args[0].startswith(f".{path.name}.")
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
            )
        )

    def test_write_creates_parent_without_pathlib_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            with mock.patch.object(Path, "mkdir", side_effect=AssertionError("unsafe mkdir")):
                StateStore(path).write(RecordingState(status="done"))

            self.assertEqual(StateStore(path).read().status, "done")

    @mock.patch("speed_of_cinnamon.path_safety.os.chmod")
    def test_write_does_not_chmod_target_path_after_replace(self, mocked_chmod: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            StateStore(path).write(RecordingState(status="done"))

        mocked_chmod.assert_not_called()

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

    def test_read_rejects_state_file_replaced_by_broken_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            path.symlink_to(Path(tmp) / "missing.json")

            state = store.read()

        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_state_file_replaced_by_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            outside = Path(tmp) / "outside.json"
            outside.write_text('{"status":"recording"}', encoding="utf-8")
            path.symlink_to(outside)

            state = store.read()

        self.assertEqual(state.error, "state file could not be read")

    def test_read_rejects_file_that_grows_after_size_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")

            with mock.patch(
                "speed_of_cinnamon.state.read_text_without_following_symlinks",
                side_effect=OSError("state file path is too large"),
            ) as mocked_read:
                state = StateStore(path).read()

        self.assertEqual(state.error, "state file is too large")
        mocked_read.assert_called_once_with(path, field_name="state file path", max_bytes=MAX_STATE_FILE_BYTES)

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
                args[0] == path.name
                and isinstance(args[1], int)
                and args[1] & os.O_NOFOLLOW
                and "dir_fd" in kwargs
                for args, kwargs in mocked_open.call_args_list
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
