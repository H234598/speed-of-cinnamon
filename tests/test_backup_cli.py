from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from speed_of_cinnamon import backup as backup_module
from speed_of_cinnamon import cli
from speed_of_cinnamon.backup_state import BackupStateStore


class BackupCliTests(unittest.TestCase):
    def test_parser_exposes_backup_subcommands_and_independent_selection(self) -> None:
        args = cli.build_parser().parse_args(
            [
                "backup",
                "create",
                "--directory",
                "/tmp/backups",
                "--no-config",
                "--no-audio",
                "--artifact-encryption",
                "off",
                "--json",
            ]
        )
        self.assertIs(args.handler, cli.command_backup_create)
        self.assertFalse(args.config)
        self.assertTrue(args.transcripts)
        self.assertFalse(args.audio)

    def test_cli_creates_and_verifies_selected_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "transcripts"
            target = root / "backups"
            source.mkdir(mode=0o700)
            transcript = source / "one.txt"
            transcript.write_text("hello\n", encoding="utf-8")
            state_store = BackupStateStore(root / "state" / "backup-state.json")
            args = SimpleNamespace(
                directory=str(target),
                config=False,
                transcripts=True,
                audio=False,
                artifact_encryption="off",
                settings_json="{}",
                settings_json_stdin=False,
                open=False,
            )
            with (
                mock.patch.object(cli, "ensure_runtime_dirs"),
                mock.patch.object(cli, "_safe_transcript_artifact_files", return_value=[transcript]),
                mock.patch.object(cli, "recording_artifact_files", return_value=[]),
                mock.patch.object(cli, "transcript_dir", return_value=source),
                mock.patch.object(cli, "recordings_dir", return_value=root / "recordings"),
                mock.patch.object(cli, "default_settings_export_file", return_value=root / "config" / "settings-export.json"),
                mock.patch.object(backup_module, "BackupStateStore", return_value=state_store),
            ):
                result = cli.command_backup_create(args)
            self.assertEqual(result["status"], "done")
            archive = next(target.glob("*.socbackup"))
            verify = cli.command_backup_verify(SimpleNamespace(archive_path=str(archive)))
            self.assertTrue(verify["verified"])
            self.assertEqual(verify["artifacts"], 1)


if __name__ == "__main__":
    unittest.main()
