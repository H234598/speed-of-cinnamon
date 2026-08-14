from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli


class BackupSecurityTest(unittest.TestCase):
    def test_backup_settings_export_uses_staging_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            default_path = root / "state" / "settings-export.json"
            staged_path = root / "private-stage" / "settings-export.json"
            staged_path.parent.mkdir(mode=0o700)
            with mock.patch.object(cli, "default_settings_export_file", return_value=default_path):
                sources, source_roots = cli._backup_inputs(
                    config=True,
                    transcripts=False,
                    audio=False,
                    settings={"auto-backup-enabled": True},
                    alarm_store={},
                    settings_path=staged_path,
                )

            self.assertFalse(default_path.exists())
            self.assertEqual([source.archive_path for source in sources], ["config/settings-export.json"])
            self.assertEqual(source_roots[0], staged_path.parent)
            self.assertTrue(staged_path.is_file())
            self.assertEqual(staged_path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
