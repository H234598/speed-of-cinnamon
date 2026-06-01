from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from speed_of_cinnamon.state import StateStore


class StateStoreTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()

