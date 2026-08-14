from __future__ import annotations

import argparse
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli


class AutoBackupGateTest(unittest.TestCase):
    def test_audio_backup_requires_directory_and_enabled_success_mode(self) -> None:
        self.assertIsNone(cli._auto_backup_configuration({}))
        with self.assertRaisesRegex(RuntimeError, "requires a backup directory"):
            cli._auto_backup_configuration(
                {
                    "auto-backup-enabled": True,
                    "auto-backup-on-success": True,
                    "auto-backup-audio": True,
                }
            )
        self.assertIsNone(
            cli._auto_backup_configuration(
                {
                    "auto-backup-enabled": True,
                    "auto-backup-on-success": False,
                    "auto-backup-audio": True,
                    "auto-backup-directory": "/tmp/backups",
                }
            )
        )

    def test_inline_audio_backup_forces_audio_selection_and_uses_settings_override(self) -> None:
        settings = {
            "auto-backup-enabled": True,
            "auto-backup-on-success": True,
            "auto-backup-audio": True,
            "auto-backup-directory": "/tmp/backups",
            "auto-backup-config": False,
            "auto-backup-transcripts": True,
            "auto-backup-encryption": "keyring",
        }
        with mock.patch.object(
            cli,
            "command_backup_create",
            return_value={"status": "done", "archive_present": True},
        ) as create:
            result = cli._run_inline_auto_backup(argparse.Namespace(), settings)

        self.assertEqual(result["status"], "done")
        backup_args = create.call_args.args[0]
        self.assertEqual(backup_args.directory, "/tmp/backups")
        self.assertFalse(backup_args.config)
        self.assertTrue(backup_args.transcripts)
        self.assertTrue(backup_args.audio)
        self.assertEqual(backup_args.artifact_encryption, "keyring")
        self.assertIs(backup_args._settings_override, settings)

    def test_source_orders_inline_backup_before_cleanup(self) -> None:
        source = Path(cli.__file__).read_text(encoding="utf-8")
        backup_index = source.index("automatic_backup_result = _run_inline_auto_backup(args, automatic_backup_settings)")
        cleanup_index = source.index("cleanup_failures: list[tuple[str, str, str]] = []", backup_index)
        self.assertLess(backup_index, cleanup_index)


if __name__ == "__main__":
    unittest.main()
