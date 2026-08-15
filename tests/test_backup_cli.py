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
    def test_parser_exposes_non_overwriting_restore(self) -> None:
        args = cli.build_parser().parse_args(
            ["backup", "restore", "/tmp/archive.socbackup", "/tmp/restore", "--json"]
        )
        self.assertIs(args.handler, cli.command_backup_restore)
        self.assertEqual(args.archive_path, "/tmp/archive.socbackup")
        self.assertEqual(args.destination_directory, "/tmp/restore")

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
            self.assertEqual(result["archive_path"], str(archive))
            verify = cli.command_backup_verify(SimpleNamespace(archive_path=str(archive)))
            self.assertTrue(verify["verified"])
            self.assertEqual(verify["artifacts"], 1)

    def test_cli_exposes_post_publish_warnings_without_changing_done_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "backups" / "soc-backup-warning.socbackup.socenc"
            backup_result = SimpleNamespace(
                archive_path=archive,
                skipped=False,
                manifest=SimpleNamespace(encryption_enabled=True, artifacts=(object(),)),
                warnings=("backup published but post-publish bookkeeping failed",),
            )
            locked_store = mock.MagicMock()
            locked_store.__enter__.return_value = root / "alarms.json"
            locked_store.__exit__.return_value = False
            args = SimpleNamespace(
                directory=str(root / "backups"),
                config=False,
                transcripts=True,
                audio=False,
                artifact_encryption="keyring",
                settings_json="{}",
                settings_json_stdin=False,
                open=False,
            )
            with (
                mock.patch.object(cli, "ensure_runtime_dirs"),
                mock.patch.object(cli, "_coerce_path", return_value=root / "backups"),
                mock.patch.object(cli, "_locked_alarm_store", return_value=locked_store),
                mock.patch.object(cli, "load_alarm_store", return_value={}),
                mock.patch.object(cli, "_backup_inputs", return_value=((), ())),
                mock.patch.object(cli, "create_backup", return_value=backup_result),
            ):
                result = cli.command_backup_create(args)
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["warnings"], ["backup published but post-publish bookkeeping failed"])
        self.assertIn("cleanup warnings", result["message"])

    def test_restore_dry_run_reports_conflicts_in_json_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "backup.socbackup"
            destination = root / "restore-target"
            plan = SimpleNamespace(
                manifest=SimpleNamespace(encryption_enabled=False, artifacts=[object()]),
                archive_members=("manifest.json", "transcripts/one.txt"),
                conflicts=("transcripts/one.txt",),
            )
            with (
                mock.patch.object(cli, "_coerce_path", side_effect=[archive, destination]),
                mock.patch.object(cli, "default_settings_export_file", return_value=root / "config" / "settings.json"),
                mock.patch.object(cli, "transcript_dir", return_value=root / "transcripts"),
                mock.patch.object(cli, "recordings_dir", return_value=root / "recordings"),
                mock.patch.object(cli, "restore_dry_run", return_value=plan),
            ):
                result = cli.command_backup_restore_dry_run(
                    SimpleNamespace(archive_path=str(archive), destination_directory=str(destination))
                )
        self.assertEqual(result["conflicts"], ["transcripts/one.txt"])
        self.assertEqual(result["conflict_count"], 1)


if __name__ == "__main__":
    unittest.main()
