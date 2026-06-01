from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon.state import MAX_STATE_FILE_BYTES, StateStore


class StateStoreTest(unittest.TestCase):
    def test_state_store_rejects_null_byte_path(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            StateStore(Path("state\x00.json"))

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

    @mock.patch("speed_of_cinnamon.state.os.replace", side_effect=OSError("disk full"))
    def test_write_raises_runtime_error_when_atomic_replace_fails(self, mocked_replace: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            store = StateStore(path)
            with self.assertRaisesRegex(RuntimeError, "failed to persist state:"):
                store.write(store.read())
        mocked_replace.assert_called_once()

    def test_read_rejects_oversized_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("x" * (MAX_STATE_FILE_BYTES + 1), encoding="utf-8")
            state = StateStore(path).read()
        self.assertEqual(state.error, "state file is too large")


if __name__ == "__main__":
    unittest.main()
