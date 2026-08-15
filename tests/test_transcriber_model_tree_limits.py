from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import transcriber


class CTranslate2ModelTreeLimitTests(unittest.TestCase):
    def test_model_tree_scan_accepts_small_nested_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            (nested / "model.bin").write_bytes(b"model")

            transcriber._validate_ctranslate2_model_tree(root, field_name="model")

    def test_model_tree_scan_rejects_entry_budget_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "one.bin").write_bytes(b"one")
            (root / "two.bin").write_bytes(b"two")

            with mock.patch.object(transcriber, "MAX_CTRANSLATE2_MODEL_TREE_ENTRIES", 1):
                with self.assertRaisesRegex(transcriber.TranscriptionError, r"too many entries \(max 1\)"):
                    transcriber._validate_ctranslate2_model_tree(root, field_name="model")


if __name__ == "__main__":
    unittest.main()
