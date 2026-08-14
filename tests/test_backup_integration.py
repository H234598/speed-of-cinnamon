from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
import tarfile
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon import artifact_crypto
from speed_of_cinnamon.backup import (
    BackupError,
    BackupInput,
    create_backup,
    restore_dry_run,
    verify_backup,
)
from speed_of_cinnamon.backup_state import BackupStateStore


class BackupIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir(mode=0o700)
        self.target.mkdir(mode=0o700)
        self.config = self.source / "settings.json"
        self.transcript = self.source / "one.txt"
        self.audio = self.source / "one.flac"
        self.config.write_text('{"safe":true}\n', encoding="utf-8")
        self.transcript.write_text("hello\n", encoding="utf-8")
        self.audio.write_bytes(b"audio\0bytes")
        self.ledger = BackupStateStore(self.root / "state" / "backup-state.json")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _inputs(self) -> list[BackupInput]:
        return [
            BackupInput("config", "config/settings.json", "config-settings", self.config),
            BackupInput("transcript", "transcripts/one.txt", "transcript-one", self.transcript),
            BackupInput("audio", "audio/one.flac", "audio-one", self.audio),
        ]

    def _selection(self, *, config: bool = True, transcripts: bool = True, audio: bool = False) -> dict[str, bool]:
        return {"config": config, "transcripts": transcripts, "audio": audio}

    def test_selective_bundle_is_verified_and_contains_only_selected_categories(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="selective",
            state_store=self.ledger,
        )
        self.assertIsNotNone(result.archive_path)
        assert result.archive_path is not None
        manifest = verify_backup(result.archive_path)
        self.assertEqual([artifact.kind for artifact in manifest.artifacts], ["transcript"])
        with tarfile.open(result.archive_path, "r:") as archive:
            self.assertEqual(sorted(member.name for member in archive.getmembers()), ["manifest.json", "transcripts/one.txt"])
        self.assertEqual(self.transcript.read_text(encoding="utf-8"), "hello\n")

    def test_existing_archive_is_not_overwritten_and_temporary_files_are_removed(self) -> None:
        first = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(),
            app_version="0.2.5",
            job_id="same-job",
            created_at_utc="2026-08-14T00:00:00Z",
            state_store=self.ledger,
        )
        assert first.archive_path is not None
        original = first.archive_path.read_bytes()
        with self.assertRaises(BackupError):
            create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(),
                app_version="0.2.5",
                job_id="same-job",
                created_at_utc="2026-08-14T00:00:00Z",
                state_store=self.ledger,
            )
        self.assertEqual(first.archive_path.read_bytes(), original)
        self.assertEqual(list(self.target.glob(".socbackup-stage-*")), [])
        self.assertEqual(list(self.target.glob("*.tmp")), [])

    def test_unsafe_source_fails_without_deleting_source(self) -> None:
        unsafe = self.source / "link.txt"
        unsafe.symlink_to(self.transcript)
        with self.assertRaises(BackupError):
            create_backup(
                self.target,
                sources=(BackupInput("transcript", "transcripts/link.txt", "link", unsafe),),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                state_store=self.ledger,
            )
        self.assertTrue(unsafe.is_symlink())
        self.assertEqual(list(self.target.glob(".socbackup-stage-*")), [])
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "failed")

    def test_running_ledger_entry_survives_crash(self) -> None:
        self.ledger.record_job(job_id="crashed", status="running", created_at_utc="2026-08-14T00:00:00Z")
        self.assertEqual(self.ledger.load()["jobs"][0]["status"], "running")

    def test_duplicate_content_is_copied_once(self) -> None:
        duplicate = self.source / "duplicate.txt"
        duplicate.write_text("hello\n", encoding="utf-8")
        result = create_backup(
            self.target,
            sources=(
                BackupInput("transcript", "transcripts/one.txt", "one", self.transcript),
                BackupInput("transcript", "transcripts/duplicate.txt", "duplicate", duplicate),
            ),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="dedupe",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        manifest = verify_backup(result.archive_path)
        self.assertEqual(len(manifest.artifacts), 1)

    def test_unchanged_config_can_be_skipped_from_repeated_job(self) -> None:
        first = create_backup(
            self.target,
            sources=(BackupInput("config", "config/settings.json", "settings", self.config),),
            source_roots=(self.source,),
            selection=self._selection(config=True, transcripts=False),
            app_version="0.2.5",
            job_id="config-one",
            state_store=self.ledger,
        )
        assert first.archive_path is not None
        self.assertTrue(self.ledger.has_unchanged_artifact(
            kind="config", source_identity="settings", size=self.config.stat().st_size,
            sha256=hashlib.sha256(self.config.read_bytes()).hexdigest(),
            mtime_ns=self.config.stat().st_mtime_ns,
        ))
        second = create_backup(
            self.target,
            sources=(BackupInput("config", "config/settings.json", "settings", self.config),),
            source_roots=(self.source,),
            selection=self._selection(config=True, transcripts=False),
            app_version="0.2.5",
            job_id="config-two",
            state_store=self.ledger,
        )
        self.assertTrue(second.skipped)
        self.assertIsNone(second.archive_path)

    def test_restore_dry_run_does_not_create_destination(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(),
            app_version="0.2.5",
            job_id="restore",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        destination = self.root / "restore-target"
        plan = restore_dry_run(result.archive_path, destination, source_roots=(self.source,))
        self.assertFalse(destination.exists())
        self.assertIn("manifest.json", plan.archive_members)

    def test_target_inside_source_is_rejected(self) -> None:
        with self.assertRaises(BackupError):
            create_backup(
                self.source / "nested-target",
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(),
                app_version="0.2.5",
                state_store=self.ledger,
            )

    def test_passphrase_encrypted_bundle_has_no_plaintext_sibling_and_verifies(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
                artifact_crypto.PASSPHRASE_FILE_ENV: "",
            },
            clear=False,
        ):
            result = create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                encryption_mode="passphrase",
                job_id="encrypted-passphrase",
                state_store=self.ledger,
            )
            assert result.archive_path is not None
            self.assertTrue(result.archive_path.name.endswith(".socbackup.socenc"))
            self.assertFalse((self.target / result.archive_path.name.removesuffix(".socenc")).exists())
            self.assertNotIn(b"hello", result.archive_path.read_bytes())
            self.assertEqual(verify_backup(result.archive_path).encryption_mode, "passphrase")

    def test_keyring_encrypted_bundle_uses_existing_crypto_contract(self) -> None:
        key = bytes(range(32))
        with (
            mock.patch.object(artifact_crypto, "_load_keyring_key", return_value=key),
            mock.patch.object(artifact_crypto, "_lookup_keyring_key", return_value=key),
        ):
            result = create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                encryption_mode="keyring",
                job_id="encrypted-keyring",
                state_store=self.ledger,
            )
            assert result.archive_path is not None
            self.assertEqual(verify_backup(result.archive_path).encryption_mode, "keyring")

    def test_tampered_encrypted_bundle_fails_closed(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
                artifact_crypto.PASSPHRASE_FILE_ENV: "",
            },
            clear=False,
        ):
            result = create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                encryption_mode="passphrase",
                job_id="tampered-encrypted",
                state_store=self.ledger,
            )
            assert result.archive_path is not None
            envelope = json.loads(result.archive_path.read_text(encoding="utf-8"))
            ciphertext = envelope["ciphertext"]
            envelope["ciphertext"] = ("A" if ciphertext[0] != "A" else "B") + ciphertext[1:]
            result.archive_path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
            result.archive_path.chmod(0o600)
            with self.assertRaises(BackupError):
                verify_backup(result.archive_path)

    def test_parallel_writers_keep_ledger_valid_and_do_not_apply_retention(self) -> None:
        def run(index: int) -> Path:
            result = create_backup(
                self.target,
                sources=(BackupInput("transcript", "transcripts/one.txt", "one", self.transcript),),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                job_id=f"parallel-{index}",
                state_store=self.ledger,
            )
            assert result.archive_path is not None
            return result.archive_path

        with ThreadPoolExecutor(max_workers=2) as executor:
            archives = list(executor.map(run, range(2)))
        self.assertTrue(all(path.is_file() for path in archives))
        self.assertEqual(len(list(self.target.glob("*.socbackup"))), 2)
        state = self.ledger.load()
        self.assertEqual({job["status"] for job in state["jobs"]}, {"success"})


if __name__ == "__main__":
    unittest.main()
