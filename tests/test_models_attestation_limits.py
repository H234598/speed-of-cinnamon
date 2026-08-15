from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import models


class SourceAttestationLimitTests(unittest.TestCase):
    def test_source_attestation_accepts_small_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "module.py").write_text("print('ok')\n", encoding="utf-8")

            with mock.patch.object(models, "_LOCAL_MODEL_ATTESTATION_SOURCE_ROOTS", ("src",)):
                snapshot = models.source_attestation_snapshot(root)

        self.assertEqual([entry["path"] for entry in snapshot], ["src/module.py"])

    def test_source_attestation_rejects_file_count_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "one.py").write_text("1\n", encoding="utf-8")
            (source / "two.py").write_text("2\n", encoding="utf-8")

            with (
                mock.patch.object(models, "_LOCAL_MODEL_ATTESTATION_SOURCE_ROOTS", ("src",)),
                mock.patch.object(models, "MAX_SOURCE_ATTESTATION_FILES", 1),
            ):
                with self.assertRaisesRegex(models.ModelError, "exceeds 1 files"):
                    models.source_attestation_snapshot(root)

    def test_source_attestation_rejects_byte_budget_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "module.py").write_bytes(b"12345")

            with (
                mock.patch.object(models, "_LOCAL_MODEL_ATTESTATION_SOURCE_ROOTS", ("src",)),
                mock.patch.object(models, "MAX_SOURCE_ATTESTATION_BYTES", 4),
            ):
                with self.assertRaisesRegex(models.ModelError, "exceeds 4 bytes"):
                    models.source_attestation_snapshot(root)


if __name__ == "__main__":
    unittest.main()
