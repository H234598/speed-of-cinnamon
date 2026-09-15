import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import models


class SourceAttestationScanLimitTests(unittest.TestCase):
    def test_source_attestation_fails_closed_at_directory_entry_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "src"
            source.mkdir()
            (source / "one.py").write_text("one", encoding="utf-8")
            (source / "two.py").write_text("two", encoding="utf-8")
            with (
                mock.patch.object(models, "_LOCAL_MODEL_ATTESTATION_SOURCE_ROOTS", ("src",)),
                mock.patch.object(models, "MAX_SOURCE_ATTESTATION_DIRECTORY_ENTRIES", 1),
            ):
                with self.assertRaisesRegex(models.ModelError, "directory entries"):
                    models.source_attestation_snapshot(root)


if __name__ == "__main__":
    unittest.main()
