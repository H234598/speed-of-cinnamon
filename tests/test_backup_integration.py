from __future__ import annotations

import hashlib
import json
import errno
import os
from concurrent.futures import ThreadPoolExecutor
import tarfile
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from speed_of_cinnamon import artifact_crypto
from speed_of_cinnamon import backup as backup_module
from speed_of_cinnamon import backup_state as backup_state_module
from speed_of_cinnamon.backup import (
    BackupError,
    BackupInput,
    create_backup,
    restore_backup,
    restore_dry_run,
    verify_backup,
)
from speed_of_cinnamon.backup_state import BackupStateError, BackupStateStore


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

    def test_all_nonempty_category_combinations_are_verified(self) -> None:
        category_keys = ("config", "transcripts", "audio")
        category_kinds = {"config": "config", "transcripts": "transcript", "audio": "audio"}
        for mask in range(1, 1 << len(category_keys)):
            with self.subTest(mask=mask):
                selection = {
                    key: bool(mask & (1 << index))
                    for index, key in enumerate(category_keys)
                }
                result = create_backup(
                    self.target,
                    sources=self._inputs(),
                    source_roots=(self.source,),
                    selection=selection,
                    app_version="0.2.5",
                    job_id=f"category-matrix-{mask}",
                    created_at_utc=f"2026-08-14T00:00:{mask:02d}Z",
                    state_store=BackupStateStore(self.root / "state" / f"matrix-{mask}.json"),
                )
                self.assertIsNotNone(result.archive_path)
                assert result.archive_path is not None
                manifest = verify_backup(result.archive_path)
                expected_kinds = {
                    category_kinds[key]
                    for key in category_keys
                    if selection[key]
                }
                self.assertEqual({artifact.kind for artifact in manifest.artifacts}, expected_kinds)

    def test_copy_retries_interrupted_reads_and_writes(self) -> None:
        real_pread = os.pread
        real_write = os.write
        state = {"pread_interrupt": True, "write_interrupt": True, "copy_started": False}

        def flaky_pread(fd: int, size: int, offset: int) -> bytes:
            if state["pread_interrupt"]:
                state["pread_interrupt"] = False
                raise InterruptedError("read interrupted")
            state["copy_started"] = True
            return real_pread(fd, size, offset)

        def flaky_write(fd: int, payload: bytes) -> int:
            if state["copy_started"] and state["write_interrupt"]:
                state["write_interrupt"] = False
                raise InterruptedError("write interrupted")
            return real_write(fd, payload)

        with (
            mock.patch.object(backup_module.os, "pread", side_effect=flaky_pread),
            mock.patch.object(backup_module.os, "write", side_effect=flaky_write),
        ):
            result = create_backup(
                self.target,
                sources=(BackupInput("transcript", "transcripts/one.txt", "one", self.transcript),),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                job_id="eintr-copy",
                state_store=self.ledger,
            )

        self.assertIsNotNone(result.archive_path)
        assert result.archive_path is not None
        self.assertFalse(state["pread_interrupt"])
        self.assertFalse(state["write_interrupt"])
        self.assertEqual(
            verify_backup(result.archive_path).artifacts[0].sha256,
            "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03",
        )

    def test_archive_member_limit_is_checked_during_iteration(self) -> None:
        members = [tarfile.TarInfo(f"entries/{index}") for index in range(backup_module.MAX_BACKUP_MEMBER_COUNT + 1)]

        class BoundedArchive:
            def __iter__(self):
                return iter(members)

            def getmembers(self):
                raise AssertionError("archive members must not be materialized")

        with self.assertRaisesRegex(BackupError, "member count is invalid"):
            backup_module._verify_tar_archive(BoundedArchive())

    def test_plain_archive_size_is_bounded_before_tar_read(self) -> None:
        archive_path = self.root / "oversized.tar"
        with archive_path.open("wb") as stream:
            stream.truncate(backup_module.MAX_BACKUP_ARCHIVE_BYTES + 1)
        with self.assertRaisesRegex(BackupError, "archive is too large"):
            verify_backup(archive_path)

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

    def test_temporary_cleanup_failure_is_reported_fail_closed(self) -> None:
        create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="cleanup-denied",
            created_at_utc="2026-08-14T00:00:00Z",
            state_store=self.ledger,
        )
        real_unlink = os.unlink

        def deny_unlink(*_args: object, **_kwargs: object) -> None:
            path = str(_args[0]) if _args else ""
            if "soc-backup-" in path and path.endswith(".tmp"):
                raise OSError(errno.EACCES, "cleanup denied")
            real_unlink(*_args, **_kwargs)

        with (
            mock.patch(
                "speed_of_cinnamon.backup._secure_remove_temporary_file",
                side_effect=OSError(errno.EACCES, "secure cleanup denied"),
            ),
            mock.patch("speed_of_cinnamon.backup.os.unlink", side_effect=deny_unlink),
        ):
            with self.assertRaisesRegex(BackupError, "backup job failed") as raised:
                create_backup(
                    self.target,
                    sources=self._inputs(),
                    source_roots=(self.source,),
                    selection=self._selection(config=False),
                    app_version="0.2.5",
                    job_id="cleanup-denied",
                    created_at_utc="2026-08-14T00:00:00Z",
                    state_store=self.ledger,
                )
        self.assertIn("backup temporary cleanup failed", raised.exception.__notes__)
        self.assertTrue(list(self.target.glob(".*.tmp")))
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "failed")

    def test_staging_cleanup_warning_does_not_hide_published_archive(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.backup.shutil.rmtree",
            side_effect=OSError(errno.EACCES, "staging cleanup denied"),
        ):
            result = create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                job_id="published-cleanup-warning",
                state_store=self.ledger,
            )
        self.assertEqual(result.warnings, ("backup temporary cleanup failed",))
        self.assertIsNotNone(result.archive_path)
        assert result.archive_path is not None
        self.assertTrue(result.archive_path.is_file())
        self.assertTrue(verify_backup(result.archive_path).artifacts)
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "success")

    def test_staging_cleanup_refuses_unsafe_rmtree(self) -> None:
        unsafe_rmtree = mock.Mock()
        unsafe_rmtree.avoids_symlink_attacks = False
        with mock.patch.object(backup_module.shutil, "rmtree", unsafe_rmtree):
            result = create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                job_id="unsafe-staging-cleanup",
                created_at_utc="2026-08-14T00:00:00Z",
                state_store=self.ledger,
            )
        self.assertEqual(result.warnings, ("backup temporary cleanup failed",))
        unsafe_rmtree.assert_not_called()

    def test_cleanup_failure_does_not_hide_primary_backup_failure(self) -> None:
        with (
            mock.patch(
                "speed_of_cinnamon.backup._build_archive",
                side_effect=RuntimeError("primary failure"),
            ),
            mock.patch(
                "speed_of_cinnamon.backup.shutil.rmtree",
                side_effect=OSError(errno.EACCES, "staging cleanup denied"),
            ),
        ):
            with self.assertRaisesRegex(BackupError, "backup job failed") as raised:
                create_backup(
                    self.target,
                    sources=self._inputs(),
                    source_roots=(self.source,),
                    selection=self._selection(config=False),
                    app_version="0.2.5",
                    job_id="primary-failure-cleanup",
                    state_store=self.ledger,
                )
        self.assertIsInstance(raised.exception.__cause__, RuntimeError)
        self.assertIn("backup temporary cleanup failed", raised.exception.__notes__)

    def test_post_publish_ledger_failure_does_not_trigger_retry(self) -> None:
        original_record_job = self.ledger.record_job
        success_attempts = 0

        def record_job(**kwargs: object) -> dict[str, object]:
            nonlocal success_attempts
            if kwargs.get("status") == "success":
                success_attempts += 1
                if success_attempts == 1:
                    raise RuntimeError("ledger temporarily unavailable")
            return original_record_job(**kwargs)

        with mock.patch.object(self.ledger, "record_job", side_effect=record_job):
            result = create_backup(
                self.target,
                sources=self._inputs(),
                source_roots=(self.source,),
                selection=self._selection(config=False),
                app_version="0.2.5",
                job_id="ledger-warning",
                state_store=self.ledger,
            )
        self.assertEqual(result.warnings, ("backup published but post-publish bookkeeping failed",))
        self.assertIsNotNone(result.archive_path)
        assert result.archive_path is not None
        self.assertTrue(result.archive_path.is_file())
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "success")

    def test_persisted_running_entry_is_marked_failed_when_start_cleanup_fails(self) -> None:
        original_record_job = self.ledger.record_job

        def record_job(**kwargs: object) -> dict[str, object]:
            entry = original_record_job(**kwargs)
            if kwargs.get("status") == "running":
                raise BackupStateError("backup state lock cleanup failed")
            return entry

        with mock.patch.object(self.ledger, "record_job", side_effect=record_job):
            with self.assertRaisesRegex(BackupError, "backup job failed"):
                create_backup(
                    self.target,
                    sources=self._inputs(),
                    source_roots=(self.source,),
                    selection=self._selection(config=False),
                    app_version="0.2.5",
                    job_id="running-cleanup-failure",
                    state_store=self.ledger,
                )
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "failed")

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
        current_stat = self.config.stat()
        os.utime(self.config, ns=(current_stat.st_atime_ns, current_stat.st_mtime_ns + 1))
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
        self.assertEqual(plan.conflicts, ())

    def test_restore_publishes_verified_archive_into_new_destination(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False, audio=True),
            app_version="0.2.5",
            job_id="restore-apply",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        destination = self.root / "restored"

        plan = restore_backup(result.archive_path, destination, source_roots=(self.source,))

        self.assertEqual(plan.conflicts, ())
        self.assertTrue((destination / "manifest.json").is_file())
        self.assertEqual((destination / "transcripts" / "one.txt").read_text(encoding="utf-8"), "hello\n")
        self.assertEqual((destination / "audio" / "one.flac").read_bytes(), b"audio\0bytes")

    def test_restore_rejects_existing_destination_without_writing(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="restore-existing",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        destination = self.root / "restored-existing"
        destination.mkdir(mode=0o700)

        with self.assertRaisesRegex(BackupError, "restore destination already exists|restore destination contains existing"):
            restore_backup(result.archive_path, destination, source_roots=(self.source,))
        self.assertEqual(list(destination.iterdir()), [])

    def test_restore_reads_verified_passphrase_encrypted_archive(self) -> None:
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
                job_id="restore-passphrase",
                state_store=self.ledger,
            )
            assert result.archive_path is not None
            destination = self.root / "restored-passphrase"

            restore_backup(result.archive_path, destination, source_roots=(self.source,))

        self.assertEqual((destination / "transcripts" / "one.txt").read_text(encoding="utf-8"), "hello\n")

    def test_restore_dry_run_reports_existing_member_conflicts_without_writing(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="restore-conflict",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        destination = self.root / "restore-conflict-target"
        (destination / "transcripts").mkdir(parents=True, mode=0o700)
        (destination / "manifest.json").write_text("existing manifest\n", encoding="utf-8")
        (destination / "transcripts" / "one.txt").write_text("existing\n", encoding="utf-8")
        plan = restore_dry_run(result.archive_path, destination, source_roots=(self.source,))
        self.assertEqual(plan.conflicts, ("manifest.json", "transcripts/one.txt"))
        self.assertEqual((destination / "transcripts" / "one.txt").read_text(encoding="utf-8"), "existing\n")

    def test_restore_dry_run_rejects_existing_non_directory_destination(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="restore-file-target",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        destination = self.root / "restore-file-target"
        destination.write_text("not a directory\n", encoding="utf-8")
        with self.assertRaises(BackupError):
            restore_dry_run(result.archive_path, destination, source_roots=(self.source,))

    def test_restore_dry_run_rejects_symlink_member_path(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            job_id="restore-symlink",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        destination = self.root / "restore-symlink-target"
        (destination / "transcripts").mkdir(parents=True, mode=0o700)
        outside = self.root / "outside.txt"
        outside.write_text("must remain\n", encoding="utf-8")
        (destination / "transcripts" / "one.txt").symlink_to(outside)
        with self.assertRaises(BackupError):
            restore_dry_run(result.archive_path, destination, source_roots=(self.source,))
        self.assertEqual(outside.read_text(encoding="utf-8"), "must remain\n")

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

    def test_encrypted_archive_write_failure_cleans_partial_ciphertext(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {
                    artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
                    artifact_crypto.PASSPHRASE_FILE_ENV: "",
                },
                clear=False,
            ),
            mock.patch(
                "speed_of_cinnamon.backup._write_fd_all",
                side_effect=OSError(errno.ENOSPC, "disk full"),
            ),
        ):
            with self.assertRaisesRegex(BackupError, "backup job failed"):
                create_backup(
                    self.target,
                    sources=self._inputs(),
                    source_roots=(self.source,),
                    selection=self._selection(config=False),
                    app_version="0.2.5",
                    encryption_mode="passphrase",
                    job_id="encrypted-write-failure",
                    state_store=self.ledger,
                )
        self.assertEqual(list(self.target.glob(".*.tmp")), [])
        self.assertEqual(list(self.target.glob("*.socbackup*")), [])
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "failed")

    def test_backup_interrupt_records_failure_and_cleans_temporary_files(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.backup._build_archive",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                create_backup(
                    self.target,
                    sources=self._inputs(),
                    source_roots=(self.source,),
                    selection=self._selection(config=False),
                    app_version="0.2.5",
                    job_id="interrupt-failure",
                    state_store=self.ledger,
                )
        self.assertEqual(list(self.target.glob(".*.tmp")), [])
        self.assertEqual(list(self.target.glob("*.socbackup*")), [])
        self.assertEqual(self.ledger.load()["jobs"][-1]["status"], "failed")

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

    def test_wrong_passphrase_fails_closed(self) -> None:
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
                job_id="wrong-passphrase",
                state_store=self.ledger,
            )
        assert result.archive_path is not None
        with mock.patch.dict(
            os.environ,
            {
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32, 64))),
                artifact_crypto.PASSPHRASE_FILE_ENV: "",
            },
            clear=False,
        ):
            with self.assertRaises(BackupError):
                verify_backup(result.archive_path)

    def test_corrupt_plaintext_bundle_fails_closed(self) -> None:
        result = create_backup(
            self.target,
            sources=self._inputs(),
            source_roots=(self.source,),
            selection=self._selection(config=False),
            app_version="0.2.5",
            encryption_mode="off",
            job_id="corrupt-plaintext",
            state_store=self.ledger,
        )
        assert result.archive_path is not None
        result.archive_path.write_bytes(b"not a backup archive\n")
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

    def test_backup_state_lock_preserves_primary_error_when_parent_close_fails(self):
        with (
            mock.patch.object(backup_state_module, "ensure_directory_without_following_symlinks", return_value=123),
            mock.patch.object(
                backup_state_module,
                "assert_fd_is_private_directory",
                side_effect=RuntimeError("directory not private"),
            ),
            mock.patch.object(
                backup_state_module.os,
                "close",
                side_effect=OSError("private state path leaked"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "directory not private") as caught:
                with self.ledger._locked():
                    pass
        notes = "\n".join(getattr(caught.exception, "__notes__", ()))
        self.assertIn("backup state lock cleanup failed", notes)
        self.assertNotIn("private state path leaked", notes)

    def test_backup_state_lock_fails_closed_without_no_follow_support(self):
        with (
            mock.patch.object(backup_state_module, "ensure_directory_without_following_symlinks", return_value=123),
            mock.patch.object(backup_state_module, "assert_fd_is_private_directory"),
            mock.patch.object(backup_state_module.os, "O_NOFOLLOW", None, create=True),
            mock.patch.object(backup_state_module.os, "open") as mocked_open,
        ):
            with self.assertRaisesRegex(BackupStateError, "secure no-follow"):
                with self.ledger._locked():
                    pass
        mocked_open.assert_not_called()

    def test_backup_state_read_retries_interrupted_read(self):
        self.ledger.record_job(
            job_id="eintr-read",
            status="success",
            created_at_utc="2026-08-15T00:00:00Z",
        )
        real_read = backup_state_module.os.read
        attempts = 0

        def interrupted_once(fd, size):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise InterruptedError()
            return real_read(fd, size)

        with mock.patch.object(backup_state_module.os, "read", side_effect=interrupted_once):
            state = self.ledger.load()
        self.assertEqual(attempts, 2)
        self.assertEqual(state["jobs"][0]["job_id"], "eintr-read")

    def test_backup_state_lock_closes_parent_after_lock_close_failure(self):
        close_calls = []

        def fail_lock_close(fd):
            close_calls.append(fd)
            if fd == 456:
                raise OSError("lock fd close failed")

        with (
            mock.patch.object(backup_state_module, "ensure_directory_without_following_symlinks", return_value=123),
            mock.patch.object(backup_state_module, "assert_fd_is_private_directory"),
            mock.patch.object(
                backup_state_module,
                "assert_fd_is_regular_private_file",
                side_effect=RuntimeError("lock file not private"),
            ),
            mock.patch.object(backup_state_module.os, "open", return_value=456),
            mock.patch.object(backup_state_module.fcntl, "flock"),
            mock.patch.object(backup_state_module.os, "close", side_effect=fail_lock_close),
        ):
            with self.assertRaisesRegex(RuntimeError, "lock file not private") as caught:
                with self.ledger._locked():
                    pass
        self.assertEqual(close_calls, [456, 123])
        notes = "\n".join(getattr(caught.exception, "__notes__", ()))
        self.assertIn("backup state lock cleanup failed", notes)

    def test_backup_state_lock_cleanup_failure_is_reported_without_primary_error(self):
        with (
            mock.patch.object(backup_state_module, "ensure_directory_without_following_symlinks", return_value=123),
            mock.patch.object(backup_state_module, "assert_fd_is_private_directory"),
            mock.patch.object(backup_state_module, "assert_fd_is_regular_private_file"),
            mock.patch.object(backup_state_module.os, "open", return_value=456),
            mock.patch.object(backup_state_module.fcntl, "flock"),
            mock.patch.object(backup_state_module.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(BackupStateError, "backup state lock cleanup failed"):
                with self.ledger._locked():
                    pass


if __name__ == "__main__":
    unittest.main()
