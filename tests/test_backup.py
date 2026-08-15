from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import backup, backup_manifest


class BackupManifestTest(unittest.TestCase):
    def _artifact(
        self,
        *,
        archive_path: str = "transcripts/001.txt",
        source_identity: str = "transcript-001",
    ) -> backup_manifest.BackupArtifact:
        return backup_manifest.BackupArtifact(
            kind="transcript",
            archive_path=archive_path,
            source_identity=source_identity,
            size=7,
            sha256="a" * 64,
            mtime_ns=123,
        )

    def _manifest(self, artifact: backup_manifest.BackupArtifact | None = None) -> backup_manifest.BackupManifest:
        return backup_manifest.create_manifest(
            job_id="job-001",
            created_at_utc="2026-08-14T12:00:00Z",
            app_version="0.2.5",
            encryption_mode="off",
            selection={"config": False, "transcripts": True, "audio": False},
            artifacts=(artifact or self._artifact(),),
        )

    def test_close_fd_strict_mode_surfaces_close_failure(self) -> None:
        with mock.patch.object(backup.os, "close", side_effect=OSError("close denied")):
            backup._close_fd(42)
            with self.assertRaises(OSError):
                backup._close_fd(42, strict=True)

    def test_manifest_roundtrip_is_canonical(self) -> None:
        manifest = self._manifest()
        rendered = backup_manifest.serialize_manifest(manifest)
        self.assertEqual(backup_manifest.serialize_manifest(backup_manifest.parse_manifest(rendered)), rendered)
        self.assertNotIn(b"/home/", rendered)
        self.assertNotIn(b"secret", rendered)

    def test_manifest_rejects_unknown_version_duplicate_keys_and_non_finite_numbers(self) -> None:
        manifest = self._manifest().to_dict()
        manifest["schema_version"] = 99
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "schema version"):
            backup_manifest.BackupManifest.from_mapping(manifest)

        duplicate = b'{"schema_version":1,"schema_version":1}'
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "duplicate"):
            backup_manifest.parse_manifest(duplicate)
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "non-finite"):
            backup_manifest.parse_manifest(b'{"schema_version":NaN}')

    def test_manifest_rejects_wrong_json_field_types_without_leaking_type_errors(self) -> None:
        document = self._manifest().to_dict()
        document["encryption"]["mode"] = []
        with self.assertRaises(backup_manifest.BackupManifestError):
            backup_manifest.BackupManifest.from_mapping(document)
        document = self._manifest().to_dict()
        document["encryption"]["envelope_version"] = []
        with self.assertRaises(backup_manifest.BackupManifestError):
            backup_manifest.BackupManifest.from_mapping(document)

    def test_manifest_rejects_duplicate_or_mismatched_archive_paths(self) -> None:
        first = self._artifact()
        second = self._artifact(source_identity="transcript-002")
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "duplicate"):
            backup_manifest.create_manifest(
                job_id="job-001",
                created_at_utc="2026-08-14T12:00:00Z",
                app_version="0.2.5",
                encryption_mode="off",
                selection={"config": False, "transcripts": True, "audio": False},
                artifacts=(first, second),
            )
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "kind"):
            self._artifact(archive_path="audio/001.flac")

    def test_manifest_accepts_unicode_archive_name_and_rejects_unsafe_names(self) -> None:
        artifact = self._artifact(archive_path="transcripts/e\u0301.txt")
        self.assertEqual(artifact.archive_path, "transcripts/\u00e9.txt")
        for path in ("/transcripts/x", "transcripts/../x", "transcripts/x\\y", "transcripts//x"):
            with self.subTest(path=path):
                with self.assertRaises(backup_manifest.BackupManifestError):
                    self._artifact(archive_path=path)

    def test_manifest_rejects_unselected_artifact_and_plaintext_inconsistent_encryption(self) -> None:
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "unselected"):
            backup_manifest.create_manifest(
                job_id="job-001",
                created_at_utc="2026-08-14T12:00:00Z",
                app_version="0.2.5",
                encryption_mode="off",
                selection={"config": True, "transcripts": False, "audio": False},
                artifacts=(self._artifact(),),
            )
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "inconsistent"):
            backup_manifest.BackupManifest(
                job_id="job-001",
                created_at_utc="2026-08-14T12:00:00Z",
                app_version="0.2.5",
                encryption_enabled=True,
                encryption_mode="off",
                envelope_version=0,
                selection=(("config", False), ("transcripts", True), ("audio", False)),
                artifacts=(self._artifact(),),
            )

    def test_hash_and_verify_regular_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "transcript.txt"
            source.write_bytes(b"payload")
            artifact = backup_manifest.collect_artifact(
                kind="transcript",
                archive_path="transcripts/001.txt",
                source_identity="transcript-001",
                source_path=source,
            )
            backup_manifest.verify_artifact_source(artifact, source)
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(backup_manifest.BackupManifestError, "mismatch"):
                backup_manifest.verify_artifact_source(artifact, source)

    def test_hash_rejects_source_changed_during_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "transcript.txt"
            source.write_bytes(b"payload")
            real_pread = backup_manifest.os.pread
            changed = False

            def mutate_after_read(fd: int, size: int, offset: int) -> bytes:
                nonlocal changed
                chunk = real_pread(fd, size, offset)
                if not changed:
                    changed = True
                    source.write_bytes(b"changed")
                return chunk

            with mock.patch.object(backup_manifest.os, "pread", side_effect=mutate_after_read):
                with self.assertRaisesRegex(backup_manifest.BackupManifestError, "changed"):
                    backup_manifest.hash_regular_file(source)

    def test_hash_rejects_size_limit_and_manifest_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "transcript.txt"
            source.write_bytes(b"payload")
            with self.assertRaisesRegex(backup_manifest.BackupManifestError, "size limit"):
                backup_manifest.hash_regular_file(source, max_bytes=1)
        with self.assertRaisesRegex(backup_manifest.BackupManifestError, "too large"):
            backup_manifest.parse_manifest(b"{}" + b" " * backup_manifest.MAX_MANIFEST_BYTES)


if __name__ == "__main__":
    unittest.main()
