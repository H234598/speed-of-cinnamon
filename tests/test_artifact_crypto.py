from __future__ import annotations

import contextlib
import json
import os
import socket
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from speed_of_cinnamon import artifact_crypto


STRONG_PASSPHRASE = artifact_crypto._b64encode(bytes(range(32)))
SECOND_STRONG_PASSPHRASE = artifact_crypto._b64encode(bytes(range(32, 64)))


class ArtifactCryptoTest(unittest.TestCase):
    def test_artifact_encryption_mode_rejects_booleans(self) -> None:
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "must be text"):
            artifact_crypto.normalize_artifact_encryption(True)
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "must be text"):
            artifact_crypto.normalize_artifact_encryption(False)
        self.assertEqual(artifact_crypto.normalize_artifact_encryption(None), "off")

    def test_encrypted_path_for_rejects_unsafe_path_components(self) -> None:
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "path is not safe"):
            artifact_crypto.encrypted_path_for(Path("artifact\nspoof.txt"))

    def test_encrypted_path_helpers_accept_case_insensitive_suffix(self) -> None:
        path = Path("/tmp/recording.FLAC.SOCENC")

        self.assertTrue(artifact_crypto.is_encrypted_path(path))
        self.assertEqual(artifact_crypto.encrypted_path_for(path), path)

    def test_base64_decoder_rejects_non_alphabet_characters(self) -> None:
        for value in ("YWJj!", "YW Jj"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "probe is invalid"):
                    artifact_crypto._b64decode(value, field_name="probe")

    def test_decryption_rejects_tampered_base64_envelope_field(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

        envelope = json.loads(encrypted.decode("utf-8"))
        envelope["ciphertext"] += "!"
        tampered = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "ciphertext is invalid"):
            artifact_crypto.decrypt_bytes(tampered, kind="transcript")

    def test_decryption_rejects_boolean_envelope_version(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
            envelope = json.loads(encrypted.decode("utf-8"))
            envelope["version"] = True
            tampered = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "version is unsupported"):
                artifact_crypto.decrypt_bytes(tampered, kind="transcript")

    def test_passphrase_encrypts_and_decrypts_payload(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, mode = artifact_crypto.encrypt_bytes(b"private transcript", "passphrase", kind="transcript")
            self.assertEqual(mode, "passphrase")
            self.assertNotIn(b"private transcript", encrypted)
            self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"private transcript")

    def test_passphrase_can_come_from_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: str(path)}, clear=False):
                encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
                self.assertEqual(mode, "passphrase")
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_explicit_passphrase_file_path_rejects_unsafe_values(self) -> None:
        unsafe_paths = [
            ("/tmp/passphrase\nkey", "contains invalid control character"),
            ("relative-passphrase.key", "must be absolute"),
            ("/" + ("a" * (artifact_crypto.MAX_PASSPHRASE_FILE_PATH_CHARS + 1)), "path is too large"),
        ]
        for file_path, message in unsafe_paths:
            with self.subTest(file_path=file_path):
                with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: file_path}, clear=False):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, message):
                        artifact_crypto._explicit_passphrase_file()

    def test_passphrase_can_come_from_default_private_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
                self.assertEqual(mode, "passphrase")
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_passphrase_env_is_used_before_generating_default_key_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            with (
                mock.patch.dict(
                    os.environ,
                    {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE, artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                    clear=False,
                ),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
                self.assertEqual(mode, "passphrase")
                self.assertFalse(path.exists())
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_weak_environment_passphrase_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "short", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path(tmp) / "missing.key"),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase is not strong enough"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_environment_passphrase_rejects_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(
                    os.environ,
                    {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE + "\n", artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                    clear=False,
                ),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path(tmp) / "missing.key"),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase contains invalid control characters"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_environment_passphrase_rejects_oversized_utf8_bytes(self) -> None:
        oversized = "A1!a" + ("😀" * ((artifact_crypto.MAX_PASSPHRASE_CHARS // 4) + 1))
        self.assertLessEqual(len(oversized), artifact_crypto.MAX_PASSPHRASE_CHARS)
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(
                    os.environ,
                    {artifact_crypto.PASSPHRASE_ENV: oversized, artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                    clear=False,
                ),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path(tmp) / "missing.key"),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase is too large"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_private_passphrase_file_rejects_internal_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text(STRONG_PASSPHRASE[:10] + "\n" + STRONG_PASSPHRASE[10:] + "\n", encoding="utf-8")
            path.chmod(0o600)
            with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: str(path)}, clear=False):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file is not strong enough"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_missing_default_passphrase_file_is_generated_securely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.fsync") as mocked_fsync,
            ):
                encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
                self.assertEqual(mode, "passphrase")
                self.assertTrue(path.exists())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")
                self.assertGreaterEqual(mocked_fsync.call_count, 2)

    def test_weak_default_passphrase_file_is_regenerated_securely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
                self.assertEqual(mode, "passphrase")
                self.assertNotEqual(path.read_text(encoding="utf-8").strip(), "short")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_weak_default_rotation_rolls_back_after_activation_parent_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_fsync = artifact_crypto._fsync_fd
            directory_syncs = 0

            def fail_activation_sync(fd: int) -> None:
                nonlocal directory_syncs
                mode = os.fstat(fd).st_mode
                if stat.S_ISDIR(mode):
                    directory_syncs += 1
                    if directory_syncs == 2:
                        raise OSError("activation directory sync failed")
                real_fsync(fd)

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_fsync_fd", side_effect=fail_activation_sync),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
            leftovers = [child for child in Path(tmp).iterdir() if child.name.startswith(".artifact.key.") and child.name.endswith(".bak")]
            self.assertEqual(leftovers, [])

    def test_weak_default_rotation_rolls_back_after_backup_cleanup_fsync_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_fsync = artifact_crypto._fsync_fd
            directory_syncs = 0

            def fail_backup_cleanup_sync(fd: int) -> None:
                nonlocal directory_syncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 3:
                        raise OSError("backup cleanup directory sync failed")
                real_fsync(fd)

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_fsync_fd", side_effect=fail_backup_cleanup_sync),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.tmp")))

    def test_default_rotation_keeps_new_target_when_post_commit_sync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_fsync = artifact_crypto._fsync_fd
            directory_syncs = 0

            def fail_post_commit_sync(fd: int) -> None:
                nonlocal directory_syncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 4:
                        raise OSError("post-commit directory sync failed")
                real_fsync(fd)

            with (
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_fsync_fd", side_effect=fail_post_commit_sync),
            ):
                with self.assertRaisesRegex(
                    artifact_crypto.ArtifactCryptoError,
                    "passphrase file could not be generated",
                ):
                    artifact_crypto._generate_default_passphrase_file(path, replace=True)

            self.assertNotEqual(path.read_text(encoding="utf-8"), "short\n")
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.bak")))

    def test_default_passphrase_generation_failure_leaves_no_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            fsync_modes: list[int] = []
            real_fsync = os.fsync

            def record_fsync(fd: int) -> None:
                fsync_modes.append(os.fstat(fd).st_mode)
                real_fsync(fd)

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.write", side_effect=OSError("disk full")),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.fsync", side_effect=record_fsync),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertFalse(path.exists())
            self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsync_modes))

    def test_default_passphrase_generation_cleanup_failure_is_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.write", side_effect=OSError("disk full")),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.unlink", side_effect=OSError("cleanup denied")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated") as caught:
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertFalse(path.exists())
            self.assertTrue(any(child.name.startswith(".artifact.key.") and child.name.endswith(".tmp") for child in Path(tmp).iterdir()))
            self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))
            self.assertNotIn("cleanup denied", "\n".join(caught.exception.__notes__))

    def test_default_passphrase_generation_closes_parent_after_temp_close_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            close_modes: list[int | None] = []
            leaked_fds: set[int] = set()
            real_close = artifact_crypto.os.close
            real_fstat = artifact_crypto.os.fstat

            def flaky_close(fd: int) -> None:
                try:
                    mode: int | None = real_fstat(fd).st_mode
                except OSError:
                    mode = None
                close_modes.append(mode)
                if mode is not None and stat.S_ISREG(mode):
                    leaked_fds.add(fd)
                    raise OSError("temp close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.dict(
                        os.environ,
                        {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                        clear=False,
                    ),
                    mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                    mock.patch("speed_of_cinnamon.artifact_crypto.os.close", side_effect=flaky_close),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                        artifact_crypto._generate_default_passphrase_file(path)
            finally:
                for fd in leaked_fds:
                    with contextlib.suppress(OSError):
                        real_close(fd)

            self.assertTrue(any(mode is not None and stat.S_ISDIR(mode) for mode in close_modes))
            self.assertFalse(any(child.name.startswith(".artifact.key.") and child.name.endswith(".tmp") for child in Path(tmp).iterdir()))

    def test_default_passphrase_generation_does_not_retry_temp_fd_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            real_close = artifact_crypto.os.close
            real_fstat = artifact_crypto.os.fstat
            close_calls: list[int] = []
            post_interrupt_calls: list[int] = []
            interrupted_fd: int | None = None
            interrupted_fingerprint: tuple[int, int] | None = None
            reopened_fds: set[int] = set()
            real_open = artifact_crypto.os.open

            def tracked_open(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if interrupted_fd is not None and fd == interrupted_fd:
                    reopened_fds.add(fd)
                return fd

            def close_after_close(fd: int) -> None:
                nonlocal interrupted_fd, interrupted_fingerprint
                close_calls.append(fd)
                try:
                    current_stat = real_fstat(fd)
                except OSError:
                    return real_close(fd)
                fingerprint = (current_stat.st_dev, current_stat.st_ino)
                if (
                    interrupted_fingerprint is not None
                    and fingerprint == interrupted_fingerprint
                    and fd not in reopened_fds
                ):
                    post_interrupt_calls.append(fd)
                if stat.S_ISREG(current_stat.st_mode) and interrupted_fd is None:
                    real_close(fd)
                    interrupted_fd = fd
                    interrupted_fingerprint = fingerprint
                    raise OSError("temp close interrupted after close")
                real_close(fd)

            try:
                with (
                    mock.patch.dict(
                        os.environ,
                        {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                        clear=False,
                    ),
                    mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                    mock.patch.object(artifact_crypto.os, "open", side_effect=tracked_open),
                    mock.patch.object(artifact_crypto.os, "close", side_effect=close_after_close),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                        artifact_crypto._generate_default_passphrase_file(path)
            finally:
                if interrupted_fd is not None:
                    with contextlib.suppress(OSError):
                        real_close(interrupted_fd)

            self.assertIsNotNone(interrupted_fd)
            self.assertEqual(post_interrupt_calls, [])

    def test_default_passphrase_generation_cleanup_failure_truncates_temp_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto._fsync_fd", side_effect=OSError("fsync failed")),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.unlink", side_effect=OSError("cleanup denied")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated") as caught:
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            leftovers = [child for child in Path(tmp).iterdir() if child.name.startswith(".artifact.key.") and child.name.endswith(".tmp")]
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(leftovers[0].read_bytes(), b"")
            self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))
            self.assertNotIn("cleanup denied", "\n".join(caught.exception.__notes__))

    def test_default_passphrase_generation_preserves_race_read_error_after_rename_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            real_close = artifact_crypto.os.close
            real_rename = artifact_crypto._rename_without_replacing
            parent_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            leaked_fds: list[int] = []

            def close_parent_with_error(fd: int) -> None:
                if fd == parent_fd:
                    leaked_fds.append(fd)
                    raise OSError("parent close failed")
                real_close(fd)

            def rename_or_conflict(
                source: str,
                target: str,
                *,
                directory_fd: int,
                field_name: str,
            ) -> None:
                if target == path.name:
                    raise FileExistsError("target appeared")
                real_rename(source, target, directory_fd=directory_fd, field_name=field_name)

            try:
                with (
                    mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                    mock.patch.object(artifact_crypto, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(artifact_crypto, "_rename_without_replacing", side_effect=rename_or_conflict),
                    mock.patch.object(
                        artifact_crypto,
                        "_read_private_passphrase_file",
                        side_effect=artifact_crypto.ArtifactCryptoError("raced passphrase read failed"),
                    ),
                    mock.patch.object(artifact_crypto.os, "close", side_effect=close_parent_with_error),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "raced passphrase read failed") as caught:
                        artifact_crypto._generate_default_passphrase_file(path)
            finally:
                for fd in leaked_fds:
                    with contextlib.suppress(OSError):
                        real_close(fd)

            self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))
            self.assertNotIn("parent close failed", "\n".join(caught.exception.__notes__))
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.tmp")))

    def test_default_passphrase_generation_preserves_race_read_error_after_temp_creation_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            real_close = artifact_crypto.os.close
            parent_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            leaked_fds: list[int] = []

            def close_parent_with_error(fd: int) -> None:
                if fd == parent_fd:
                    leaked_fds.append(fd)
                    raise OSError("parent close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                    mock.patch.object(artifact_crypto, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(
                        artifact_crypto,
                        "_create_private_temp_passphrase_file",
                        side_effect=FileExistsError("temporary name appeared"),
                    ),
                    mock.patch.object(
                        artifact_crypto,
                        "_read_private_passphrase_file",
                        side_effect=artifact_crypto.ArtifactCryptoError("raced passphrase read failed"),
                    ),
                    mock.patch.object(artifact_crypto.os, "close", side_effect=close_parent_with_error),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "raced passphrase read failed") as caught:
                        artifact_crypto._generate_default_passphrase_file(path)
            finally:
                for fd in leaked_fds:
                    with contextlib.suppress(OSError):
                        real_close(fd)

            self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))
            self.assertNotIn("parent close failed", "\n".join(caught.exception.__notes__))

    def test_default_passphrase_generation_does_not_return_before_deferred_cleanup_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            real_close = artifact_crypto.os.close
            real_rename = artifact_crypto._rename_without_replacing
            parent_fd = os.open(tmp, os.O_RDONLY | os.O_DIRECTORY)
            leaked_fds: list[int] = []

            def close_parent_with_error(fd: int) -> None:
                if fd == parent_fd:
                    leaked_fds.append(fd)
                    raise OSError("parent close failed")
                real_close(fd)

            def rename_or_conflict(
                source: str,
                target: str,
                *,
                directory_fd: int,
                field_name: str,
            ) -> None:
                if target == path.name:
                    path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
                    path.chmod(0o600)
                    raise FileExistsError("target appeared")
                real_rename(source, target, directory_fd=directory_fd, field_name=field_name)

            try:
                with (
                    mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                    mock.patch.object(artifact_crypto, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(artifact_crypto, "_rename_without_replacing", side_effect=rename_or_conflict),
                    mock.patch.object(artifact_crypto.os, "close", side_effect=close_parent_with_error),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "temporary file could not be removed"):
                        artifact_crypto._generate_default_passphrase_file(path)
            finally:
                for fd in leaked_fds:
                    with contextlib.suppress(OSError):
                        real_close(fd)

            self.assertEqual(path.read_text(encoding="utf-8"), STRONG_PASSPHRASE + "\n")

    def test_default_passphrase_generation_does_not_remove_replaced_temp_file_after_activation_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "artifact.key"
            victim = root / "victim.txt"
            victim.write_text("must survive", encoding="utf-8")
            replaced_temp_name: str | None = None

            def conflict(source_name: str, target_name: str, *, directory_fd: int, field_name: str) -> None:
                nonlocal replaced_temp_name
                replaced_temp_name = source_name
                path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
                path.chmod(0o600)
                (root / source_name).unlink()
                victim.rename(root / source_name)
                raise FileExistsError("target appeared")

            with (
                mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_rename_without_replacing", side_effect=conflict),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto._generate_default_passphrase_file(path)

            self.assertIsNotNone(replaced_temp_name)
            replaced_temp = root / replaced_temp_name
            self.assertTrue(replaced_temp.exists())
            self.assertEqual(replaced_temp.read_text(encoding="utf-8"), "must survive")

    def test_scrub_temp_passphrase_preserves_inspection_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(artifact_crypto.os, "open", return_value=123),
            mock.patch.object(artifact_crypto.os, "fstat", side_effect=OSError("inspect failed")),
            mock.patch.object(artifact_crypto.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(OSError, "inspect failed") as caught:
                artifact_crypto._scrub_temp_passphrase_file(456, ".artifact.key.tmp")

        self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))

    def test_scrub_temp_passphrase_rejects_missing_nofollow(self) -> None:
        with (
            mock.patch.object(artifact_crypto.os, "O_NOFOLLOW", None, create=True),
            self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "secure artifact encryption passphrase temporary file scrubbing"),
        ):
            artifact_crypto._scrub_temp_passphrase_file(456, ".artifact.key.tmp")

    def test_scrub_temp_passphrase_opens_nonblocking(self) -> None:
        with (
            mock.patch.object(artifact_crypto.os, "open", return_value=123) as mocked_open,
            mock.patch.object(
                artifact_crypto.os,
                "fstat",
                return_value=mock.Mock(st_mode=stat.S_IFREG, st_size=0),
            ),
            mock.patch.object(artifact_crypto.os, "ftruncate"),
            mock.patch.object(artifact_crypto.os, "close"),
        ):
            artifact_crypto._scrub_temp_passphrase_file(456, ".artifact.key.tmp")

        flags = mocked_open.call_args.args[1]
        self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))

    def test_scrub_temp_passphrase_retries_interrupted_writes(self) -> None:
        with (
            mock.patch.object(artifact_crypto.os, "open", return_value=123),
            mock.patch.object(
                artifact_crypto.os,
                "fstat",
                return_value=mock.Mock(st_mode=stat.S_IFREG, st_size=3, st_dev=1, st_ino=2, st_nlink=1),
            ),
            mock.patch.object(artifact_crypto.os, "lseek"),
            mock.patch.object(artifact_crypto.os, "write", side_effect=[InterruptedError(), 3]) as mocked_write,
            mock.patch.object(artifact_crypto.os, "ftruncate"),
            mock.patch.object(artifact_crypto.os, "close"),
        ):
            artifact_crypto._scrub_temp_passphrase_file(456, ".artifact.key.tmp")

        self.assertEqual(mocked_write.call_count, 2)

    def test_write_all_retries_interrupted_writes(self) -> None:
        with mock.patch.object(artifact_crypto.os, "write", side_effect=[InterruptedError(), 3]) as mocked_write:
            artifact_crypto._write_all(123, b"abc")

        self.assertEqual(mocked_write.call_count, 2)

    def test_fsync_retries_interrupted_calls(self) -> None:
        with mock.patch.object(artifact_crypto.os, "fsync", side_effect=[InterruptedError(), None]) as mocked_fsync:
            artifact_crypto._fsync_fd(123)

        self.assertEqual(mocked_fsync.call_count, 2)

    def test_scrub_temp_passphrase_retries_interrupted_truncate(self) -> None:
        with (
            mock.patch.object(artifact_crypto.os, "open", return_value=123),
            mock.patch.object(
                artifact_crypto.os,
                "fstat",
                return_value=mock.Mock(st_mode=stat.S_IFREG, st_size=0),
            ),
            mock.patch.object(
                artifact_crypto.os,
                "ftruncate",
                side_effect=[InterruptedError(), None],
            ) as mocked_ftruncate,
            mock.patch.object(artifact_crypto.os, "close"),
        ):
            artifact_crypto._scrub_temp_passphrase_file(456, ".artifact.key.tmp")

        self.assertEqual(mocked_ftruncate.call_count, 2)

    def test_scrub_temp_passphrase_rejects_hardlink_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            secret = root / "secret.tmp"
            alias = root / "alias.tmp"
            secret.write_bytes(b"secret")
            expected_stat = secret.stat()
            os.link(secret, alias)
            parent_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaisesRegex(OSError, "changed before scrubbing"):
                    artifact_crypto._scrub_temp_passphrase_file(
                        parent_fd,
                        secret.name,
                        expected_stat=expected_stat,
                    )
            finally:
                os.close(parent_fd)

            self.assertEqual(secret.read_bytes(), b"secret")
            self.assertEqual(alias.read_bytes(), b"secret")

    def test_default_passphrase_rotation_rejects_target_swap_during_backup_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            replacement = Path(tmp) / "replacement.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            replacement.write_text("replacement\n", encoding="utf-8")
            real_link = os.link

            def link_then_swap(source: object, destination: object, *args: object, **kwargs: object) -> None:
                real_link(source, destination, *args, **kwargs)
                if isinstance(destination, str) and destination.endswith(".bak"):
                    replacement.replace(path)

            with (
                mock.patch.dict(
                    os.environ,
                    {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                    clear=False,
                ),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto.os, "link", side_effect=link_then_swap),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertEqual(path.read_text(encoding="utf-8"), "replacement\n")
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.tmp")))

    def test_default_passphrase_rotation_failure_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_rename = artifact_crypto._rename_without_replacing
            rename_calls = 0

            def fail_activation_once(*args: object, **kwargs: object) -> None:
                nonlocal rename_calls
                rename_calls += 1
                if rename_calls == 1:
                    raise OSError("replace failed")
                real_rename(*args, **kwargs)

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto._rename_without_replacing", side_effect=fail_activation_once),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")

    def test_default_passphrase_rotation_reports_target_change_during_activation_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            replacement = Path(tmp) / "replacement.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            replacement.write_text("replacement\n", encoding="utf-8")
            replacement.chmod(0o600)
            real_fsync = artifact_crypto._fsync_fd
            directory_syncs = 0

            def fail_after_activation_sync(fd: int) -> None:
                nonlocal directory_syncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 2:
                        replacement.replace(path)
                        raise OSError("activation directory sync failed")
                real_fsync(fd)

            with (
                mock.patch.dict(
                    os.environ,
                    {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""},
                    clear=False,
                ),
                mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_fsync_fd", side_effect=fail_after_activation_sync),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated") as caught:
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            notes = "\n".join(caught.exception.__notes__)
            self.assertIn("artifact encryption cleanup failed", notes)
            self.assertNotIn("target changed during rollback", notes)
            self.assertNotIn("cannot access local variable", notes)
            self.assertEqual(path.read_text(encoding="utf-8"), "replacement\n")

    def test_default_passphrase_rotation_preserves_replaced_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "artifact.key"
            replacement = root / "replacement.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            replacement.write_text("must survive", encoding="utf-8")
            real_fsync = artifact_crypto._fsync_fd
            directory_syncs = 0

            def fail_after_activation_sync(fd: int) -> None:
                nonlocal directory_syncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_syncs += 1
                    if directory_syncs == 2:
                        backups = list(root.glob(".artifact.key.*.bak"))
                        self.assertEqual(len(backups), 1)
                        backup = backups[0]
                        backup.unlink()
                        replacement.rename(backup)
                        raise OSError("activation directory sync failed")
                real_fsync(fd)
            with (
                mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_fsync_fd", side_effect=fail_after_activation_sync),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated") as caught:
                    artifact_crypto._generate_default_passphrase_file(path, replace=True)

            notes = "\n".join(caught.exception.__notes__)
            self.assertIn("artifact encryption cleanup failed", notes)
            self.assertNotIn("recovery backup changed before cleanup", notes)
            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
            backups = list(root.glob(".artifact.key.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "must survive")

    def test_default_passphrase_rotation_restores_target_when_backup_unlink_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_unlink = artifact_crypto.os.unlink
            interrupted = False

            def unlink_then_interrupt(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if isinstance(name, str) and name.endswith(".cleanup") and not interrupted:
                    interrupted = True
                    real_unlink(name, *args, **kwargs)
                    raise KeyboardInterrupt
                real_unlink(name, *args, **kwargs)

            with (
                mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto.os, "unlink", side_effect=unlink_then_interrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    artifact_crypto._generate_default_passphrase_file(path, replace=True)

            self.assertTrue(interrupted)
            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.tmp")))

    def test_default_passphrase_rotation_preserves_backup_changed_after_cleanup_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "artifact.key"
            replacement = root / "replacement.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            replacement.write_text("must survive", encoding="utf-8")
            replacement.chmod(0o600)
            real_stat = artifact_crypto.os.stat
            backup_stats = 0

            def stat_then_replace_after_cleanup_check(
                name: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal backup_stats
                result = real_stat(name, *args, **kwargs)
                if isinstance(name, str) and name.endswith(".bak"):
                    backup_stats += 1
                    if backup_stats == 2:
                        backup_path = root / name
                        backup_path.unlink()
                        replacement.replace(backup_path)
                return result

            with mock.patch.object(
                artifact_crypto,
                "default_passphrase_file",
                return_value=path,
            ), mock.patch.object(artifact_crypto.os, "stat", side_effect=stat_then_replace_after_cleanup_check):
                with self.assertRaisesRegex(
                    artifact_crypto.ArtifactCryptoError,
                    "passphrase file could not be generated",
                ):
                    artifact_crypto._generate_default_passphrase_file(path, replace=True)

            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
            backups = list(root.glob(".artifact.key.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "must survive")

    def test_default_passphrase_rotation_replace_failure_removes_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_rename = artifact_crypto._rename_without_replacing
            rename_calls = 0

            def fail_activation_once(*args: object, **kwargs: object) -> None:
                nonlocal rename_calls
                rename_calls += 1
                if rename_calls == 1:
                    raise OSError("replace failed")
                real_rename(*args, **kwargs)

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto._rename_without_replacing", side_effect=fail_activation_once),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")
            leftovers = [child for child in Path(tmp).iterdir() if child.name.startswith(".artifact.key.") and child.name.endswith(".bak")]
            self.assertEqual(leftovers, [])

    def test_default_passphrase_rotation_activation_conflict_removes_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            real_rename = artifact_crypto._rename_without_replacing

            def conflict(source: str, target: str, **kwargs: object) -> None:
                path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
                path.chmod(0o600)
                if target == path.name:
                    raise FileExistsError("target appeared")
                real_rename(source, target, **kwargs)

            with mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=path), mock.patch.object(
                artifact_crypto, "_rename_without_replacing", side_effect=conflict
            ):
                result = artifact_crypto._generate_default_passphrase_file(path, replace=True)

            self.assertEqual(result, STRONG_PASSPHRASE)
            self.assertFalse(list(Path(tmp).glob(".artifact.key.*.bak")))

    def test_passphrase_file_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text("file-passphrase\n", encoding="utf-8")
            path.chmod(0o644)
            with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: str(path)}, clear=False):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file must be private"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_keyring_mode_fails_closed_instead_of_falling_back_to_explicit_env(self) -> None:
        with (
            mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: SECOND_STRONG_PASSPHRASE}, clear=False),
            mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "Secret Service keyring is unavailable"):
                artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_mode_fails_closed_instead_of_falling_back_to_explicit_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: str(path)}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "Secret Service keyring is unavailable"):
                    artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_mode_does_not_generate_default_passphrase_fallback_when_cli_keyring_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "Secret Service keyring is unavailable"):
                    artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

            self.assertFalse(path.exists())

    def test_keyring_mode_does_not_use_existing_default_passphrase_for_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "Secret Service keyring is unavailable"):
                    artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_mode_fails_closed_without_evaluating_weak_passphrase_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "weak", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path(tmp) / "missing.key"),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "Secret Service keyring is unavailable"):
                    artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_secret_must_decode_to_32_bytes(self) -> None:
        bad_secret = artifact_crypto._b64encode(b"too short").encode("ascii")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "invalid length"):
            artifact_crypto._parse_keyring_secret(bad_secret)

    def test_keyring_lookup_invalid_payloads_keep_parser_error_contract(self) -> None:
        key = bytes(range(artifact_crypto.KEY_SIZE_BYTES))
        with mock.patch.object(artifact_crypto, "_load_keyring_key", return_value=key):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

        cases = (
            (b"\xff\xfe-secret", "invalid UTF-8"),
            (b"not-base64!\n", "encrypted artifact artifact is invalid"),
            (artifact_crypto._b64encode(b"too short").encode("ascii") + b"\n", "invalid length"),
        )
        for raw, message in cases:
            with self.subTest(message=message):
                response = subprocess.CompletedProcess(["secret-tool", "lookup"], 0, raw, b"")
                with mock.patch.object(artifact_crypto, "_run_secret_tool", return_value=response):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, message) as caught:
                        artifact_crypto.decrypt_bytes(encrypted, kind="transcript")

                exception = caught.exception
                rendered = "".join(__import__("traceback").format_exception(exception))
                self.assertIsNone(exception.__cause__)
                self.assertIsNone(exception.__context__)
                self.assertNotIn(repr(raw), repr(exception))
                self.assertNotIn(repr(raw), repr(exception.args))
                self.assertNotIn(repr(raw), rendered)
                self.assertNotIn(repr(raw), "\n".join(getattr(exception, "__notes__", ())))

    def test_keyring_lookup_failure_does_not_create_or_replace_key(self) -> None:
        failure = subprocess.CompletedProcess(
            ["secret-tool", "lookup"],
            1,
            b"",
            b"Could not connect: Connection refused\n",
        )
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._run_secret_tool", return_value=failure),
            mock.patch("speed_of_cinnamon.artifact_crypto._store_keyring_key") as mocked_store,
            mock.patch("speed_of_cinnamon.artifact_crypto.secrets.token_bytes", return_value=b"k" * 32),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "keyring lookup failed"):
                artifact_crypto._load_keyring_key()

        mocked_store.assert_not_called()

    def _pipe_reader(self, payload: bytes) -> object:
        read_fd, write_fd = os.pipe()
        os.write(write_fd, payload)
        os.close(write_fd)
        return os.fdopen(read_fd, "rb", buffering=0)

    def test_secret_tool_uses_pipe_output_capture(self) -> None:
        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.stdin = None
                self.stdout = self_outer._pipe_reader(b"stored-secret\n")
                self.stderr = self_outer._pipe_reader(b"warning\n")

            def wait(self, timeout: int | None = None) -> int:
                return self.returncode

            def kill(self) -> None:
                self.returncode = -9

        self_outer = self

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen) as mocked_popen,
        ):
            proc = artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, b"stored-secret\n")
        self.assertEqual(proc.stderr, b"warning\n")
        self.assertEqual(mocked_popen.call_args.kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(mocked_popen.call_args.kwargs["stderr"], subprocess.PIPE)
        self.assertEqual(mocked_popen.call_args.kwargs["stdin"], None)
        self.assertFalse(mocked_popen.call_args.kwargs["shell"])

    def test_secret_tool_start_value_error_is_controlled(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch(
                "speed_of_cinnamon.artifact_crypto.subprocess.Popen",
                side_effect=ValueError("bad process arguments"),
            ),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "helper could not be started"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

    def test_secret_tool_closed_pipe_failure_is_controlled(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class BrokenStream:
            def fileno(self) -> int:
                raise ValueError("closed pipe")

            def close(self) -> None:
                return None

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.stdin = None
                self.stdout = BrokenStream()
                self.stderr = BrokenStream()
                self.killed = False
                self.wait_calls = 0
                fake_proc_holder["proc"] = self

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                return self.returncode

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "output could not be captured safely"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertTrue(getattr(fake_proc_holder["proc"], "killed"))
        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_calls"), 1)

    def test_secret_tool_input_failure_is_controlled(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class BrokenInput:
            def write(self, _payload: bytes) -> None:
                raise ValueError("closed stdin")

            def close(self) -> None:
                raise ValueError("stdin close failed")

        class DummyStream:
            def close(self) -> None:
                return None

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.stdin = BrokenInput()
                self.stdout = DummyStream()
                self.stderr = DummyStream()
                self.killed = False
                self.wait_calls = 0
                fake_proc_holder["proc"] = self

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                return self.returncode

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "input could not be sent safely"):
                artifact_crypto._run_secret_tool(["store", "application", "test"], input_text="secret")

        self.assertTrue(getattr(fake_proc_holder["proc"], "killed"))
        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_calls"), 1)

    def test_secret_tool_kills_process_when_pipe_capture_is_interrupted(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class DummyStream:
            def close(self) -> None:
                return None

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.stdin = DummyStream()
                self.stdout = DummyStream()
                self.stderr = DummyStream()
                self.killed = False
                self.wait_calls = 0
                fake_proc_holder["proc"] = self

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                return self.returncode

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
            mock.patch(
                "speed_of_cinnamon.artifact_crypto._read_secret_tool_pipes_bounded",
                side_effect=KeyboardInterrupt,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertTrue(getattr(fake_proc_holder["proc"], "killed"))
        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_calls"), 1)

    def test_secret_tool_pipe_reader_retries_interrupted_reads(self) -> None:
        class DummyStream:
            def __init__(self, fd: int) -> None:
                self._fd = fd

            def fileno(self) -> int:
                return self._fd

        process = mock.Mock(stdout=DummyStream(10), stderr=DummyStream(11))
        with (
            mock.patch.object(artifact_crypto.os, "set_blocking"),
            mock.patch.object(
                artifact_crypto.os,
                "read",
                side_effect=[InterruptedError(), b"", b""],
            ) as mocked_read,
        ):
            result = artifact_crypto._read_secret_tool_pipes_bounded(process, deadline=artifact_crypto.time.monotonic() + 1)

        self.assertEqual(result, (b"", b""))
        self.assertEqual(mocked_read.call_count, 3)

    def test_secret_tool_environment_skips_control_character_values(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"DISPLAY": ":0", "HOME": "bad\nhome", "XDG_RUNTIME_DIR": "relative-runtime"},
            clear=True,
        ):
            env = artifact_crypto._filtered_environment()

        self.assertNotIn("DISPLAY", env)
        self.assertNotIn("XDG_RUNTIME_DIR", env)
        self.assertNotIn("HOME", env)
        self.assertEqual(env["PATH"], artifact_crypto._TRUSTED_COMMAND_PATH)

    def test_secret_tool_environment_skips_unencodable_values(self) -> None:
        with mock.patch.object(artifact_crypto.os, "environ", {"HOME": "bad\ud800"}):
            env = artifact_crypto._filtered_environment()

        self.assertNotIn("HOME", env)
        self.assertEqual(env["PATH"], artifact_crypto._TRUSTED_COMMAND_PATH)

    def test_secret_tool_environment_keeps_only_pinned_session_bus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            runtime.chmod(0o700)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bus_socket:
                bus_socket.bind(str(runtime / "bus"))
                with (
                    mock.patch("speed_of_cinnamon.artifact_crypto._canonical_xdg_runtime_dir", return_value=runtime),
                    mock.patch.dict(
                        os.environ,
                        {
                            "XDG_RUNTIME_DIR": str(runtime),
                            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime / 'bus'}",
                            "DISPLAY": ":0",
                        },
                        clear=True,
                    ),
                ):
                    env = artifact_crypto._filtered_environment()

        self.assertEqual(env["XDG_RUNTIME_DIR"], str(runtime))
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], f"unix:path={runtime / 'bus'}")
        self.assertNotIn("DISPLAY", env)

    def test_secret_tool_environment_drops_noncanonical_runtime_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            runtime.chmod(0o700)
            with mock.patch.dict(
                os.environ,
                {
                    "XDG_RUNTIME_DIR": str(runtime),
                    "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime / 'bus'}",
                },
                clear=True,
            ):
                env = artifact_crypto._filtered_environment()

        self.assertNotIn("XDG_RUNTIME_DIR", env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_secret_tool_environment_drops_unpinned_session_bus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            runtime.chmod(0o700)
            with (
                mock.patch("speed_of_cinnamon.artifact_crypto._canonical_xdg_runtime_dir", return_value=runtime),
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_RUNTIME_DIR": str(runtime),
                        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/attacker-bus",
                    },
                    clear=True,
                ),
            ):
                    env = artifact_crypto._filtered_environment()

        self.assertEqual(env["XDG_RUNTIME_DIR"], str(runtime))
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_secret_tool_environment_drops_non_socket_session_bus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            runtime.chmod(0o700)
            (runtime / "bus").write_text("", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.artifact_crypto._canonical_xdg_runtime_dir", return_value=runtime),
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_RUNTIME_DIR": str(runtime),
                        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime / 'bus'}",
                    },
                    clear=True,
                ),
            ):
                env = artifact_crypto._filtered_environment()

        self.assertEqual(env["XDG_RUNTIME_DIR"], str(runtime))
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_secret_tool_rejects_unsafe_arguments_before_start(self) -> None:
        unsafe_invocations = [
            (["lookup", "bad\nargument"], None, "argument contains invalid control character"),
            (["delete", "application", "test"], None, "command is not allowed"),
            ([], None, "arguments must be a non-empty list"),
            (["store", "application", "test"], "bad\nsecret", "input contains invalid control character"),
        ]
        for args, input_text, message in unsafe_invocations:
            with self.subTest(args=args):
                with (
                    mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path") as mocked_secret_tool_path,
                    self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, message),
                ):
                    artifact_crypto._run_secret_tool(args, input_text=input_text)  # type: ignore[arg-type]
                mocked_secret_tool_path.assert_not_called()

    def test_secret_tool_stop_does_not_signal_already_reaped_process(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.poll.return_value = 0
        with mock.patch("speed_of_cinnamon.artifact_crypto.os.killpg") as mocked_killpg:
            artifact_crypto._stop_secret_tool_process(process)

        mocked_killpg.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_not_called()

    def test_secret_tool_stop_fails_closed_when_pid_identity_changes(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.returncode = None
        process._soc_process_identity = "owner-identity"

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._output_process_identity_is_current", return_value=False),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.killpg") as mocked_killpg,
        ):
            artifact_crypto._stop_secret_tool_process(process)

        mocked_killpg.assert_not_called()
        process.kill.assert_not_called()
        process.wait.assert_not_called()

    def test_secret_tool_stop_kills_same_session_child_process_group(self) -> None:
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                "import os,time; child=os.fork(); (os.setpgid(0,0) if child == 0 else print(child, flush=True)); time.sleep(30)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        process._soc_process_identity = artifact_crypto._clipboard_lock_identity_for_pid(process.pid)
        self.assertTrue(process._soc_process_identity)

        def child_is_live() -> bool:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
            except OSError:
                return False
            return state not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            artifact_crypto._stop_secret_tool_process(process)
            process.wait(timeout=2)
            deadline = artifact_crypto.time.monotonic() + 2
            while child_is_live() and artifact_crypto.time.monotonic() < deadline:
                artifact_crypto.time.sleep(0.01)
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_secret_tool_stop_kills_child_group_after_leader_exit(self) -> None:
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                "import os,time; child=os.fork(); (os.setpgid(0,0) if child == 0 else print(child, flush=True)); "
                "(time.sleep(30) if child == 0 else None)",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        process._soc_process_identity = artifact_crypto._clipboard_lock_identity_for_pid(process.pid)
        self.assertTrue(process._soc_process_identity)
        process.wait()

        def child_is_live() -> bool:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
            except OSError:
                return False
            return state not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            artifact_crypto._stop_secret_tool_process(process)
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            process.communicate()

    def test_secret_tool_stop_kills_child_that_created_new_session(self) -> None:
        process = subprocess.Popen(
            [
                "python3",
                "-c",
                "import os,time; read_fd,write_fd=os.pipe(); child=os.fork(); "
                "(os.close(read_fd), os.setsid(), os.write(write_fd, str(os.getpid()).encode()), "
                "os.close(write_fd), time.sleep(30)) if child == 0 else "
                "(os.close(write_fd), print(os.read(read_fd, 32).decode(), flush=True), "
                "os.close(read_fd), time.sleep(30))",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        process._soc_process_identity = artifact_crypto._clipboard_lock_identity_for_pid(process.pid)
        self.assertTrue(process._soc_process_identity)

        def child_is_live() -> bool:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
            except OSError:
                return False
            return state not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            artifact_crypto._stop_secret_tool_process(process)
            process.wait(timeout=2)
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            if process.poll() is None:
                process.kill()
            process.communicate()

    def test_secret_tool_process_state_decode_errors_fail_closed(self) -> None:
        decode_error = UnicodeDecodeError("ascii", b"\xff", 0, 1, "invalid byte")
        proc_entry = mock.Mock()
        proc_entry.name = "1234"
        stat_entry = mock.Mock()
        stat_entry.read_text.side_effect = decode_error
        proc_entry.joinpath.return_value = stat_entry

        with mock.patch.object(artifact_crypto.Path, "iterdir", return_value=(proc_entry,)):
            self.assertIsNone(artifact_crypto._secret_tool_process_group_has_live_descendants(1234))
        with mock.patch.object(artifact_crypto.Path, "read_text", side_effect=decode_error):
            self.assertFalse(artifact_crypto._secret_tool_leader_is_gone_or_zombie(1234))

    def test_secret_tool_process_scan_reports_live_same_session_different_group(self) -> None:
        with (
            mock.patch.object(artifact_crypto.Path, "iterdir", return_value=(Path("/proc/100"),)),
            mock.patch.object(artifact_crypto.Path, "read_text", return_value="100 (child) S 1 9999 1234"),
        ):
            self.assertTrue(artifact_crypto._secret_tool_process_group_has_live_descendants(1234))

    def test_secret_tool_rejects_oversized_output(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.killed = False
                self.wait_calls = 0
                self.stdin = None
                self.stdout = self_outer._pipe_reader(b"x" * 17)
                self.stderr = self_outer._pipe_reader(b"")
                fake_proc_holder["proc"] = self

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                return self.returncode

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

        self_outer = self

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.MAX_SECRET_TOOL_OUTPUT_BYTES", 16),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "exceeded safe output limit"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])
        self.assertTrue(getattr(fake_proc_holder["proc"], "killed"))
        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_calls"), 1)

    def test_secret_tool_timeout_survives_kill_failure(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.wait_calls = 0
                self.stdin = None
                self.stdout = self_outer._pipe_reader(b"")
                self.stderr = self_outer._pipe_reader(b"")
                fake_proc_holder["proc"] = self

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                if self.wait_calls == 1:
                    raise subprocess.TimeoutExpired(self.command, timeout)
                return self.returncode

            def kill(self) -> None:
                raise ValueError("process already closed")

        self_outer = self

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "request timed out"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_calls"), 2)

    def test_secret_tool_wait_uses_remaining_request_deadline(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.returncode = 0
                self.wait_timeout: float | None = None
                self.stdin = None
                self.stdout = self_outer._pipe_reader(b"")
                self.stderr = self_outer._pipe_reader(b"")
                fake_proc_holder["proc"] = self

            def wait(self, timeout: float | None = None) -> int:
                self.wait_timeout = timeout
                return self.returncode

        self_outer = self

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto._SECRET_TOOL_TIMEOUT_SECONDS", 10.0),
            mock.patch(
                "speed_of_cinnamon.artifact_crypto._read_secret_tool_pipes_bounded",
                return_value=(b"", b""),
            ),
            mock.patch("speed_of_cinnamon.artifact_crypto.time.monotonic", side_effect=[100.0, 104.0]),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
        ):
            artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_timeout"), 6.0)

    def test_secret_tool_timeout_kills_child_process_group(self) -> None:
        if not Path("/proc").is_dir():
            self.skipTest("process state inspection requires /proc")
        child_pid: int | None = None
        child_state: str | None = None
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "secret-tool-helper"
            child_pid_path = Path(tmp) / "child.pid"
            helper.write_text(
                "#!/bin/sh\n"
                "/bin/sleep 30 &\n"
                "child_pid=$!\n"
                "printf '%s\\n' \"$child_pid\" > \"$2\"\n"
                "wait \"$child_pid\"\n",
                encoding="ascii",
            )
            helper.chmod(0o700)
            try:
                with (
                    mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value=str(helper)),
                    mock.patch("speed_of_cinnamon.artifact_crypto._SECRET_TOOL_TIMEOUT_SECONDS", 0.1),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "request timed out"):
                        artifact_crypto._run_secret_tool(["lookup", str(child_pid_path)])

                deadline = artifact_crypto.time.monotonic() + 2
                while artifact_crypto.time.monotonic() < deadline:
                    try:
                        child_pid = int(child_pid_path.read_text(encoding="ascii").strip())
                        raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                        child_state = raw.rsplit(")", 1)[1].split()[0]
                    except (OSError, ValueError):
                        child_state = None
                        break
                    if child_state in {"Z", "X", "x"}:
                        break
                    artifact_crypto.time.sleep(0.01)

                self.assertIsNotNone(child_pid)
                self.assertIn(child_state, {None, "Z", "X", "x"})
            finally:
                if child_pid is not None:
                    with contextlib.suppress(OSError):
                        os.kill(child_pid, 9)

    def test_secret_tool_timeout_kills_descendants_after_leader_exit(self) -> None:
        if not Path("/proc").is_dir():
            self.skipTest("process state inspection requires /proc")
        child_pid: int | None = None
        child_state: str | None = None
        with tempfile.TemporaryDirectory() as tmp:
            helper = Path(tmp) / "secret-tool-helper"
            child_pid_path = Path(tmp) / "child.pid"
            helper.write_text(
                "#!/bin/sh\n"
                "/bin/sleep 30 &\n"
                "child_pid=$!\n"
                "printf '%s\\n' \"$child_pid\" > \"$2\"\n"
                "exit 0\n",
                encoding="ascii",
            )
            helper.chmod(0o700)
            try:
                with (
                    mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value=str(helper)),
                    mock.patch("speed_of_cinnamon.artifact_crypto._SECRET_TOOL_TIMEOUT_SECONDS", 0.1),
                ):
                    with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "request timed out"):
                        artifact_crypto._run_secret_tool(["lookup", str(child_pid_path)])

                deadline = artifact_crypto.time.monotonic() + 2
                while artifact_crypto.time.monotonic() < deadline:
                    try:
                        child_pid = int(child_pid_path.read_text(encoding="ascii").strip())
                        raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                        child_state = raw.rsplit(")", 1)[1].split()[0]
                    except (OSError, ValueError):
                        child_state = None
                        break
                    if child_state in {"Z", "X", "x"}:
                        break
                    artifact_crypto.time.sleep(0.01)

                self.assertIsNotNone(child_pid)
                self.assertIn(child_state, {None, "Z", "X", "x"})
            finally:
                if child_pid is not None:
                    with contextlib.suppress(OSError):
                        os.kill(child_pid, 9)

    def test_secret_tool_wait_failure_is_controlled(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.stdin = None
                self.stdout = self_outer._pipe_reader(b"")
                self.stderr = self_outer._pipe_reader(b"")
                self.killed = False
                self.wait_calls = 0
                fake_proc_holder["proc"] = self

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                raise OSError("waitpid failed")

            def kill(self) -> None:
                self.killed = True

        self_outer = self

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "could not be reaped safely"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertTrue(getattr(fake_proc_holder["proc"], "killed"))
        self.assertEqual(getattr(fake_proc_holder["proc"], "wait_calls"), 2)

    def test_secret_tool_stream_cleanup_interrupt_does_not_mask_wait_failure(self) -> None:
        fake_proc_holder: dict[str, object] = {}

        class BrokenStream:
            def close(self) -> None:
                raise KeyboardInterrupt

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.stdin = None
                self.stdout = BrokenStream()
                self.stderr = BrokenStream()
                self.killed = False
                self.wait_calls = 0
                fake_proc_holder["proc"] = self

            def wait(self, timeout: int | None = None) -> int:
                self.wait_calls += 1
                raise OSError("waitpid failed")

            def kill(self) -> None:
                self.killed = True

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
            mock.patch("speed_of_cinnamon.artifact_crypto._read_secret_tool_pipes_bounded", return_value=(b"", b"")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "could not be reaped safely"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

        self.assertTrue(getattr(fake_proc_holder["proc"], "killed"))

    def test_secret_tool_stream_cleanup_failure_is_controlled(self) -> None:
        class BrokenStream:
            def close(self) -> None:
                raise OSError("stream close failed")

        class FakePopen:
            def __init__(self, command: list[str], **kwargs: object) -> None:
                self.command = command
                self.stdin = None
                self.stdout = BrokenStream()
                self.stderr = BrokenStream()

            def wait(self, timeout: int | None = None) -> int:
                return 0

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch("speed_of_cinnamon.artifact_crypto.subprocess.Popen", side_effect=FakePopen),
            mock.patch("speed_of_cinnamon.artifact_crypto._read_secret_tool_pipes_bounded", return_value=(b"", b"")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "streams could not be closed safely"):
                artifact_crypto._run_secret_tool(["lookup", "application", "test"])

    def test_keyring_decryption_does_not_create_missing_keyring_key(self) -> None:
        key = bytes(range(32))
        with mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", return_value=key):
            encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")
        self.assertEqual(mode, "keyring")

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._lookup_keyring_key", return_value=None),
            mock.patch("speed_of_cinnamon.artifact_crypto._store_keyring_key") as mocked_store,
            mock.patch("speed_of_cinnamon.artifact_crypto.secrets.token_bytes") as mocked_token_bytes,
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "keyring does not contain"):
                artifact_crypto.decrypt_bytes(encrypted, kind="transcript")

        mocked_store.assert_not_called()
        mocked_token_bytes.assert_not_called()

    def test_v1_keyring_envelope_remains_decryptable(self) -> None:
        key = bytes(range(artifact_crypto.KEY_SIZE_BYTES))
        nonce = bytes(range(artifact_crypto.NONCE_SIZE_BYTES))
        _invalid_tag, aesgcm, _scrypt = artifact_crypto._crypto_backend()
        ciphertext = aesgcm(key).encrypt(nonce, b"legacy-keyring", artifact_crypto._aad("transcript", version=1))
        envelope = {
            "magic": artifact_crypto.ENVELOPE_MAGIC,
            "version": 1,
            "algorithm": artifact_crypto.ENVELOPE_ALGORITHM,
            "mode": artifact_crypto.ARTIFACT_ENCRYPTION_KEYRING,
            "kind": "transcript",
            "nonce": artifact_crypto._b64encode(nonce),
            "ciphertext": artifact_crypto._b64encode(ciphertext),
            "kdf": "none",
        }
        payload = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with mock.patch.object(artifact_crypto, "_lookup_keyring_key", return_value=key):
            self.assertEqual(artifact_crypto.decrypt_bytes(payload, kind="transcript"), b"legacy-keyring")

    def test_decryption_rejects_wrong_artifact_kind(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _ = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "kind does not match"):
                artifact_crypto.decrypt_bytes(encrypted, kind="recording")

    def test_encrypted_envelope_canonicalizes_artifact_kind(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _ = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind=" Transcript ")

        envelope = json.loads(encrypted.decode("utf-8"))
        self.assertEqual(envelope["kind"], "transcript")
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_decryption_can_require_encrypted_payload(self) -> None:
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope is missing"):
            artifact_crypto.decrypt_bytes(b"plaintext", kind="transcript", require_encrypted=True)

    def test_is_encrypted_payload_returns_false_for_oversized_payload(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            self.assertFalse(artifact_crypto.is_encrypted_payload(b" " * 5))

    def test_is_encrypted_payload_handles_json_recursion_error(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.json.loads", side_effect=RecursionError("too deep")):
            self.assertFalse(artifact_crypto.is_encrypted_payload(b'{"magic":"SOCENC1"}'))

    def test_decrypt_bytes_wraps_json_recursion_error(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto.is_encrypted_payload", return_value=True),
            mock.patch("speed_of_cinnamon.artifact_crypto.json.loads", side_effect=RecursionError("too deep")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope is malformed"):
                artifact_crypto.decrypt_bytes(b'{"magic":"SOCENC1"}', kind="transcript")

    def test_encrypted_payload_probe_handles_memory_error(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.json.loads", side_effect=MemoryError("out of memory")):
            self.assertFalse(artifact_crypto.is_encrypted_payload(b'{"magic":"SOCENC1"}'))

    def test_decrypt_bytes_wraps_json_memory_error(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto.is_encrypted_payload", return_value=True),
            mock.patch("speed_of_cinnamon.artifact_crypto.json.loads", side_effect=MemoryError("out of memory")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope is malformed"):
                artifact_crypto.decrypt_bytes(b'{"magic":"SOCENC1"}', kind="transcript")

    def test_encrypt_bytes_wraps_json_render_memory_error(self) -> None:
        with (
            mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False),
            mock.patch("speed_of_cinnamon.artifact_crypto.json.dumps", side_effect=MemoryError("out of memory")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope could not be rendered"):
                artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_encrypt_bytes_allows_oversized_payload_in_off_mode(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            data = b"12345"
            payload, mode = artifact_crypto.encrypt_bytes(data, "off", kind="transcript")
            self.assertEqual(mode, "off")
            self.assertEqual(payload, data)

    def test_encrypt_bytes_rejects_oversized_payload_with_encryption_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with mock.patch.dict(
                os.environ,
                {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: str(path)},
                clear=False,
            ), mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "payload is too large"):
                    artifact_crypto.encrypt_bytes(b"12345", "passphrase", kind="transcript")

    def test_decrypt_bytes_rejects_oversized_encrypted_payload_without_requirements(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "artifact payload is too large"):
                artifact_crypto.decrypt_bytes(b'{"magic":"SOCENC1","version":1,"ciphertext":"x"}', kind="transcript")

    def test_decrypt_bytes_rejects_oversized_json_like_payload_without_requirements(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "artifact payload is too large"):
                artifact_crypto.decrypt_bytes(b'{"magic":"SOCENC1","version":1}', kind="transcript")

    def test_decrypt_bytes_rejects_plaintext_by_default(self) -> None:
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope is missing"):
            artifact_crypto.decrypt_bytes(b"plaintext", kind="transcript")

    def test_read_decrypted_bytes_from_file_rejects_plaintext_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.txt"
            path.write_bytes(b"plaintext")
            path.chmod(0o600)

            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope is missing"):
                artifact_crypto.read_decrypted_bytes_from_file(path, kind="transcript", field_name="artifact")

            self.assertEqual(
                artifact_crypto.read_decrypted_bytes_from_file(
                    path,
                    kind="transcript",
                    field_name="artifact",
                    require_encrypted=False,
                ),
                b"plaintext",
            )

    def test_read_private_bytes_error_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-artifact.socenc"

            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "failed to read artifact") as raised:
                artifact_crypto.read_private_bytes(path, field_name="artifact")
            self.assertNotIn(str(path), str(raised.exception))

    def test_explicit_passphrase_path_home_resolution_failure_is_controlled(self) -> None:
        with (
            mock.patch.dict(
                os.environ,
                {artifact_crypto.PASSPHRASE_FILE_ENV: "~/passphrase.key"},
                clear=False,
            ),
            mock.patch.object(Path, "expanduser", side_effect=RuntimeError("home unavailable")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "path could not be resolved"):
                artifact_crypto._explicit_passphrase_file()

    def test_private_passphrase_home_resolution_failure_is_controlled(self) -> None:
        with mock.patch.object(Path, "expanduser", side_effect=RuntimeError("home unavailable")):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "path could not be resolved"):
                artifact_crypto._read_private_passphrase_file(Path("~/passphrase.key"))

    def test_read_private_bytes_io_error_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-artifact.socenc"
            path.write_bytes(b"payload")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", side_effect=OSError(f"boom {path}")):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "failed to read artifact") as raised:
                    artifact_crypto.read_private_bytes(path, field_name="artifact")
            self.assertNotIn(str(path), str(raised.exception))

    def test_read_private_bytes_fdopen_value_error_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-artifact.socenc"
            path.write_bytes(b"payload")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", side_effect=ValueError("bad fd")):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "failed to read artifact"):
                    artifact_crypto.read_private_bytes(path, field_name="artifact")

    def test_read_private_bytes_fd_cleanup_interrupt_does_not_mask_read_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-artifact.socenc"
            path.write_bytes(b"payload")
            path.chmod(0o600)
            with (
                mock.patch("speed_of_cinnamon.artifact_crypto.assert_no_symlink_ancestors"),
                mock.patch("speed_of_cinnamon.artifact_crypto.open_file_without_following_symlinks", return_value=123),
                mock.patch("speed_of_cinnamon.artifact_crypto.assert_fd_is_regular_private_file"),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", side_effect=ValueError("bad fd")),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.close", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "failed to read artifact"):
                    artifact_crypto.read_private_bytes(path, field_name="artifact")

    def test_read_private_bytes_handle_cleanup_interrupt_does_not_mask_read_error(self) -> None:
        class FailingHandle:
            def read(self, _limit: int) -> bytes:
                raise OSError("read failed")

            def close(self) -> None:
                raise KeyboardInterrupt("close interrupted")

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_no_symlink_ancestors"),
            mock.patch("speed_of_cinnamon.artifact_crypto.open_file_without_following_symlinks", return_value=123),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_fd_is_regular_private_file"),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", return_value=FailingHandle()),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "failed to read artifact") as caught:
                artifact_crypto.read_private_bytes(Path("/does-not-matter/artifact"), field_name="artifact")

        self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertNotIn("close interrupted", "\n".join(caught.exception.__notes__))

    def test_private_passphrase_fdopen_value_error_is_wrapped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.key"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", side_effect=ValueError("bad fd")):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be read"):
                    artifact_crypto._read_private_passphrase_file(path)

    def test_private_passphrase_open_is_nonblocking(self) -> None:
        with (
            mock.patch.object(artifact_crypto, "default_passphrase_file", return_value=Path("/other/default.key")),
            mock.patch.object(artifact_crypto, "assert_no_symlink_ancestors"),
            mock.patch.object(artifact_crypto, "_stat_private_passphrase_parent"),
            mock.patch.object(
                artifact_crypto,
                "open_file_without_following_symlinks",
                return_value=123,
            ) as mocked_open,
            mock.patch.object(artifact_crypto, "assert_fd_is_regular_private_file"),
            mock.patch.object(artifact_crypto, "_assert_no_posix_acl"),
            mock.patch.object(
                artifact_crypto.os,
                "fstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid()),
            ),
            mock.patch.object(artifact_crypto.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(artifact_crypto.os, "close"),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be read"):
                artifact_crypto._read_private_passphrase_file(Path("/does-not-matter/passphrase.key"))

        flags = mocked_open.call_args.args[1]
        self.assertTrue(flags & getattr(os, "O_NONBLOCK", 0))

    def test_private_passphrase_fd_cleanup_does_not_mask_read_error(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path("/other/default.key")),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_no_symlink_ancestors"),
            mock.patch("speed_of_cinnamon.artifact_crypto._stat_private_passphrase_parent"),
            mock.patch("speed_of_cinnamon.artifact_crypto.open_file_without_following_symlinks", return_value=123),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_fd_is_regular_private_file"),
            mock.patch("speed_of_cinnamon.artifact_crypto._assert_no_posix_acl"),
            mock.patch(
                "speed_of_cinnamon.artifact_crypto.os.fstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid()),
            ),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", side_effect=ValueError("bad fd")),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.close", side_effect=OSError("close failed")) as mocked_close,
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be read"):
                artifact_crypto._read_private_passphrase_file(Path("/does-not-matter/passphrase.key"))

        mocked_close.assert_called_once_with(123)

    def test_private_passphrase_fd_cleanup_interrupt_does_not_mask_read_error(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path("/other/default.key")),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_no_symlink_ancestors"),
            mock.patch("speed_of_cinnamon.artifact_crypto._stat_private_passphrase_parent"),
            mock.patch("speed_of_cinnamon.artifact_crypto.open_file_without_following_symlinks", return_value=123),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_fd_is_regular_private_file"),
            mock.patch("speed_of_cinnamon.artifact_crypto._assert_no_posix_acl"),
            mock.patch(
                "speed_of_cinnamon.artifact_crypto.os.fstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid()),
            ),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", side_effect=ValueError("bad fd")),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be read"):
                artifact_crypto._read_private_passphrase_file(Path("/does-not-matter/passphrase.key"))

    def test_private_passphrase_handle_cleanup_interrupt_does_not_mask_read_error(self) -> None:
        class FailingHandle:
            def read(self, _limit: int) -> bytes:
                raise OSError("read failed")

            def close(self) -> None:
                raise KeyboardInterrupt("close interrupted")

        with (
            mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path("/other/default.key")),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_no_symlink_ancestors"),
            mock.patch("speed_of_cinnamon.artifact_crypto._stat_private_passphrase_parent"),
            mock.patch("speed_of_cinnamon.artifact_crypto.open_file_without_following_symlinks", return_value=123),
            mock.patch("speed_of_cinnamon.artifact_crypto.assert_fd_is_regular_private_file"),
            mock.patch("speed_of_cinnamon.artifact_crypto._assert_no_posix_acl"),
            mock.patch(
                "speed_of_cinnamon.artifact_crypto.os.fstat",
                return_value=SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=os.getuid()),
            ),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.fdopen", return_value=FailingHandle()),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be read") as caught:
                artifact_crypto._read_private_passphrase_file(Path("/does-not-matter/passphrase.key"))

        self.assertIn("artifact encryption cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertNotIn("close interrupted", "\n".join(caught.exception.__notes__))

    def test_write_encrypted_bytes_error_does_not_leak_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "secret-artifact.txt"
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False),
                mock.patch(
                    "speed_of_cinnamon.artifact_crypto.write_bytes_atomically_without_following_symlinks",
                    side_effect=OSError(f"boom {path}"),
                ),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "failed to write encrypted artifact") as raised:
                    artifact_crypto.write_encrypted_bytes_atomically(
                        path,
                        b"payload",
                        "passphrase",
                        kind="transcript",
                        field_name="artifact",
                    )
            self.assertNotIn(str(path), str(raised.exception))

    def test_decrypt_bytes_allows_oversized_non_json_payload_when_explicitly_not_required(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            payload = b"-----BEGIN PRIVATE KEY-----"
            self.assertEqual(artifact_crypto.decrypt_bytes(payload, kind="transcript", require_encrypted=False), payload)

    def test_decrypt_bytes_still_enforces_size_for_required_encrypted_payloads(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "payload is too large"):
                artifact_crypto.decrypt_bytes(b"12345", kind="transcript", require_encrypted=True)

    def test_encrypt_bytes_rejects_envelope_larger_than_artifact_limit(self) -> None:
        with (
            mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False),
            mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 128),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "encrypted artifact payload is too large"):
                artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_read_decrypted_bytes_from_file_rejects_explicit_zero_max_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.bin"
            with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
                encrypted, _mode = artifact_crypto.encrypt_bytes(b"private transcript", "passphrase", kind="transcript")
                path.write_bytes(encrypted)
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "is too large"):
                    artifact_crypto.read_decrypted_bytes_from_file(path, kind="transcript", field_name="artifact", max_bytes=0)

    def test_read_private_bytes_rejects_max_bytes_above_artifact_limit(self) -> None:
        with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "must not exceed 4 bytes"):
                artifact_crypto.read_private_bytes(
                    Path("/does-not-matter/artifact"),
                    field_name="artifact",
                    max_bytes=5,
                )

    def test_encrypt_with_surrogate_passphrase_raises_controlled_error(self) -> None:
        surrogate_passphrase = "".join(chr(0xD800 + (index % 128)) for index in range(40))
        with mock.patch(
            "speed_of_cinnamon.artifact_crypto.os.environ",
            {
                artifact_crypto.PASSPHRASE_ENV: surrogate_passphrase,
                artifact_crypto.PASSPHRASE_FILE_ENV: "",
            },
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "valid UTF-8"):
                artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_explicit_passphrase_path_with_surrogate_raises_controlled_error(self) -> None:
        bad_path = "".join(chr(0xD800 + (index % 128)) for index in range(2))
        with mock.patch("speed_of_cinnamon.artifact_crypto.os.environ", {artifact_crypto.PASSPHRASE_FILE_ENV: bad_path}):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "valid UTF-8"):
                artifact_crypto._explicit_passphrase_file()

    def test_secret_tool_text_with_surrogate_raises_controlled_error(self) -> None:
        bad_text = "".join(chr(0xD800 + (index % 128)) for index in range(2))
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "valid UTF-8"):
            artifact_crypto._validate_secret_tool_text(bad_text, field_name="argument")

    def test_read_private_bytes_default_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.bin"
            path.write_bytes(b"12345")
            path.chmod(0o600)
            with mock.patch("speed_of_cinnamon.artifact_crypto.MAX_ENCRYPTED_ARTIFACT_BYTES", 4):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "is too large"):
                    artifact_crypto.read_private_bytes(path, field_name="artifact")

    def test_v2_envelope_binds_metadata_and_rejects_unknown_fields(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

        envelope = json.loads(encrypted.decode("utf-8"))
        self.assertEqual(envelope["version"], 2)
        envelope["scrypt_n"] = artifact_crypto.SCRYPT_N + 1
        tampered = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "metadata"):
            artifact_crypto.decrypt_bytes(tampered, kind="transcript")

    def test_unknown_envelope_version_is_rejected_as_unsupported(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
        envelope = json.loads(encrypted.decode("utf-8"))
        envelope["version"] = "future"
        tampered = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "version is unsupported"):
            artifact_crypto.decrypt_bytes(tampered, kind="transcript")

    def test_v1_envelope_remains_decryptable_after_v2_default(self) -> None:
        salt = bytes(range(artifact_crypto.SALT_SIZE_BYTES))
        nonce = bytes(range(artifact_crypto.NONCE_SIZE_BYTES))
        _invalid_tag, aesgcm, _scrypt = artifact_crypto._crypto_backend()
        key = artifact_crypto._derive_passphrase_key(STRONG_PASSPHRASE, salt)
        ciphertext = aesgcm(key).encrypt(nonce, b"legacy", artifact_crypto._aad("transcript", version=1))
        envelope = {
            "magic": artifact_crypto.ENVELOPE_MAGIC,
            "version": 1,
            "algorithm": artifact_crypto.ENVELOPE_ALGORITHM,
            "mode": artifact_crypto.ARTIFACT_ENCRYPTION_PASSPHRASE,
            "kind": "transcript",
            "nonce": artifact_crypto._b64encode(nonce),
            "ciphertext": artifact_crypto._b64encode(ciphertext),
            "kdf": "scrypt",
            "salt": artifact_crypto._b64encode(salt),
            "scrypt_n": artifact_crypto.SCRYPT_N,
            "scrypt_r": artifact_crypto.SCRYPT_R,
            "scrypt_p": artifact_crypto.SCRYPT_P,
        }
        payload = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            self.assertEqual(artifact_crypto.decrypt_bytes(payload, kind="transcript"), b"legacy")

    def test_weak_default_rotation_preserves_one_bounded_previous_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            salt = bytes(range(artifact_crypto.SALT_SIZE_BYTES))
            nonce = bytes(range(artifact_crypto.NONCE_SIZE_BYTES))
            _invalid_tag, aesgcm, _scrypt = artifact_crypto._crypto_backend()
            old_key = artifact_crypto._derive_passphrase_key("short", salt)
            ciphertext = aesgcm(old_key).encrypt(nonce, b"old", artifact_crypto._aad("transcript", version=1))
            legacy = {
                "magic": artifact_crypto.ENVELOPE_MAGIC,
                "version": 1,
                "algorithm": artifact_crypto.ENVELOPE_ALGORITHM,
                "mode": artifact_crypto.ARTIFACT_ENCRYPTION_PASSPHRASE,
                "kind": "transcript",
                "nonce": artifact_crypto._b64encode(nonce),
                "ciphertext": artifact_crypto._b64encode(ciphertext),
                "kdf": "scrypt",
                "salt": artifact_crypto._b64encode(salt),
                "scrypt_n": artifact_crypto.SCRYPT_N,
                "scrypt_r": artifact_crypto.SCRYPT_R,
                "scrypt_p": artifact_crypto.SCRYPT_P,
            }
            old_payload = (json.dumps(legacy, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                artifact_crypto.encrypt_bytes(b"new", "passphrase", kind="transcript")
                history = path.with_name("artifact.key.history")
                self.assertTrue(history.exists())
                self.assertNotIn(b"short", history.read_bytes())
                self.assertLessEqual(history.stat().st_size, 8192)
                self.assertEqual(history.stat().st_mode & 0o777, 0o600)
                self.assertEqual(history.stat().st_nlink, 1)
                self.assertEqual(artifact_crypto.decrypt_bytes(old_payload, kind="transcript"), b"old")

    def test_untrusted_default_history_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "artifact.key"
            history = root / "artifact.key.history"
            victim = root / "victim.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            victim.write_text("victim secret", encoding="utf-8")
            victim.chmod(0o600)
            history.symlink_to(victim.name)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "history"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
            self.assertTrue(history.is_symlink())
            self.assertEqual(victim.read_text(encoding="utf-8"), "victim secret")
            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")

    def test_secret_tool_stop_kills_owned_live_process_when_identity_is_unknown(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.returncode = None
        process.poll.return_value = None
        process._soc_process_identity = ""
        with (
            mock.patch("speed_of_cinnamon.artifact_crypto._process_tree_descendant_identities") as tree,
            mock.patch.object(artifact_crypto.os, "getpgid", side_effect=ProcessLookupError),
            mock.patch("speed_of_cinnamon.artifact_crypto.os.killpg") as killpg,
        ):
            artifact_crypto._stop_secret_tool_process(process)

        process.kill.assert_called_once_with()
        process.wait.assert_called_once()
        tree.assert_not_called()
        killpg.assert_not_called()

    def test_passphrase_temp_creation_requests_cloexec(self) -> None:
        with (
            mock.patch.object(artifact_crypto.os, "open", return_value=123) as mocked_open,
            mock.patch.object(artifact_crypto.secrets, "token_hex", return_value="fixed"),
        ):
            fd, name = artifact_crypto._create_private_temp_passphrase_file(9, "artifact.key")

        self.assertEqual((fd, name), (123, ".artifact.key.fixed.tmp"))
        self.assertTrue(mocked_open.call_args.args[1] & getattr(os, "O_CLOEXEC", 0))

    def test_passphrase_scrub_requests_cloexec(self) -> None:
        fake_stat = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=2, st_nlink=1, st_size=0)
        with (
            mock.patch.object(artifact_crypto.os, "open", return_value=123) as mocked_open,
            mock.patch.object(artifact_crypto.os, "fstat", return_value=fake_stat),
            mock.patch.object(artifact_crypto.os, "ftruncate"),
            mock.patch.object(artifact_crypto.os, "close"),
        ):
            artifact_crypto._scrub_temp_passphrase_file(9, "temp.key")

        self.assertTrue(mocked_open.call_args.args[1] & getattr(os, "O_CLOEXEC", 0))

    def test_default_generation_restores_claim_after_cleanup_open_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            real_open = artifact_crypto.os.open

            def fail_cleanup_open(name: object, *args: object, **kwargs: object) -> int:
                if isinstance(name, str) and name.endswith(".cleanup"):
                    raise OSError("cleanup open failed")
                return real_open(name, *args, **kwargs)

            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch.object(artifact_crypto, "_write_all", side_effect=OSError("write failed")),
                mock.patch.object(artifact_crypto.os, "open", side_effect=fail_cleanup_open),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto._generate_default_passphrase_file(path)

            temporary_files = list(Path(tmp).glob(".artifact.key.*.tmp"))
            self.assertEqual(len(temporary_files), 1)
            self.assertEqual(temporary_files[0].read_bytes(), b"")
            self.assertFalse(list(Path(tmp).glob("*.cleanup")))

    def test_invalid_default_history_does_not_block_current_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            history = Path(tmp) / "artifact.key.history"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            history.write_text("not-json", encoding="utf-8")
            history.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                encrypted, _mode = artifact_crypto.encrypt_bytes(b"current", "passphrase", kind="transcript")
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"current")
            self.assertEqual(history.read_text(encoding="utf-8"), "not-json")

    def test_invalid_history_after_current_key_failure_is_ignored_chain_free(self) -> None:
        wrong_passphrase = "WrongStrongPassphrase!9876543210"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            history = Path(tmp) / "artifact.key.history"
            victim = Path(tmp) / "history-victim"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                encrypted, _mode = artifact_crypto.encrypt_bytes(b"current", "passphrase", kind="transcript")

            path.write_text(wrong_passphrase + "\n", encoding="utf-8")
            path.chmod(0o600)
            history.write_text("not-json", encoding="utf-8")
            history.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "authentication failed") as caught:
                    artifact_crypto.decrypt_bytes(encrypted, kind="transcript")

            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            rendered = "".join(__import__("traceback").format_exception(caught.exception))
            self.assertNotIn("not-json", rendered)
            self.assertNotIn(wrong_passphrase, rendered)

            victim.write_text("victim", encoding="utf-8")
            victim.chmod(0o600)
            history.unlink()
            history.symlink_to(victim.name)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "authentication failed") as caught:
                    artifact_crypto.decrypt_bytes(encrypted, kind="transcript")

            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertTrue(history.is_symlink())
            self.assertEqual(victim.read_text(encoding="utf-8"), "victim")

    def test_default_history_rejects_boolean_and_float_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            history = Path(tmp) / "artifact.key.history"
            for version in (True, 1.0):
                history.write_text(
                    json.dumps({"version": version, "keys": []}) + "\n",
                    encoding="utf-8",
                )
                history.chmod(0o600)
                with self.assertRaisesRegex(artifact_crypto._PassphraseHistoryError, "invalid"):
                    artifact_crypto._read_default_passphrase_history(path)

    def test_default_history_key_derivation_is_lazy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
            ):
                encrypted, _mode = artifact_crypto.encrypt_bytes(b"current", "passphrase", kind="transcript")
                real_derive = artifact_crypto._derive_passphrase_key
                with mock.patch.object(
                    artifact_crypto,
                    "_derive_passphrase_key",
                    side_effect=real_derive,
                ) as derive:
                    self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"current")
                self.assertEqual(derive.call_count, 1)

    def test_v2_downgrade_and_nonce_tamper_are_rejected(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
        envelope = json.loads(encrypted.decode("utf-8"))

        downgraded = dict(envelope)
        downgraded["version"] = 1
        payload = (json.dumps(downgraded, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "authentication failed"):
            artifact_crypto.decrypt_bytes(payload, kind="transcript")

        tampered = dict(envelope)
        tampered["nonce"] = artifact_crypto._b64encode(b"x" * artifact_crypto.NONCE_SIZE_BYTES)
        payload = (json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "authentication failed"):
            artifact_crypto.decrypt_bytes(payload, kind="transcript")

    def test_unknown_numeric_envelope_version_is_rejected(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
        envelope = json.loads(encrypted.decode("utf-8"))
        envelope["version"] = 99
        payload = (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "version is unsupported"):
            artifact_crypto.decrypt_bytes(payload, kind="transcript")

    def test_secret_tool_unknown_identity_uses_confirmed_private_group(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.returncode = None
        process.poll.return_value = None
        process._soc_process_identity = ""
        with (
            mock.patch.object(artifact_crypto.os, "getpgid", return_value=1234),
            mock.patch.object(artifact_crypto.os, "killpg") as killpg,
        ):
            artifact_crypto._stop_secret_tool_process(process)
        killpg.assert_called_once_with(1234, artifact_crypto.signal.SIGKILL)
        process.kill.assert_not_called()
        process.wait.assert_called_once()

    def test_secret_tool_unknown_identity_falls_back_to_leader_when_group_unconfirmed(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.returncode = None
        process.poll.return_value = None
        process._soc_process_identity = ""
        with (
            mock.patch.object(artifact_crypto.os, "getpgid", side_effect=OSError("permission denied")),
            mock.patch.object(artifact_crypto.os, "killpg") as killpg,
        ):
            artifact_crypto._stop_secret_tool_process(process)
        killpg.assert_not_called()
        process.kill.assert_called_once_with()
        process.wait.assert_called_once()

    def test_history_error_boundary_is_chain_free(self) -> None:
        secret = "/srv/private/passphrase-secret"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            history = Path(tmp) / "artifact.key.history"
            path.write_text(STRONG_PASSPHRASE + "\n", encoding="utf-8")
            path.chmod(0o600)
            history.write_text("{}\n", encoding="utf-8")
            history.chmod(0o600)
            with mock.patch.object(
                artifact_crypto,
                "read_private_bytes",
                side_effect=artifact_crypto.ArtifactCryptoError(secret),
            ):
                try:
                    artifact_crypto._read_default_passphrase_history(path)
                except artifact_crypto.ArtifactCryptoError as caught:
                    rendered = "".join(__import__("traceback").format_exception(caught))
                    self.assertEqual(caught.__cause__, None)
                    self.assertEqual(caught.__context__, None)
                    self.assertNotIn(secret, str(caught))
                    self.assertNotIn(secret, repr(caught))
                    self.assertNotIn(secret, repr(caught.args))
                    self.assertNotIn(secret, "\n".join(getattr(caught, "__notes__", ())))
                    self.assertNotIn(secret, rendered)
                else:
                    self.fail("history error was not raised")

    def test_read_error_boundary_does_not_retain_path_exception(self) -> None:
        secret = "/private/audio-key"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.bin"
            path.write_bytes(b"payload")
            path.chmod(0o600)
            with mock.patch.object(
                artifact_crypto,
                "open_file_without_following_symlinks",
                side_effect=OSError(secret),
            ):
                with self.assertRaises(artifact_crypto.ArtifactCryptoError) as caught:
                    artifact_crypto.read_private_bytes(path, field_name="artifact")
        rendered = "".join(__import__("traceback").format_exception(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))
        self.assertNotIn(secret, repr(caught.exception.args))
        self.assertNotIn(secret, rendered)

    def test_decrypt_json_boundary_is_chain_free(self) -> None:
        secret = "/private/json-passphrase-payload"
        decode_error = json.JSONDecodeError(secret, "{}", 0)
        with (
            mock.patch.object(artifact_crypto, "is_encrypted_payload", return_value=True),
            mock.patch.object(artifact_crypto.json, "loads", side_effect=decode_error),
        ):
            with self.assertRaises(artifact_crypto.ArtifactCryptoError) as caught:
                artifact_crypto.decrypt_bytes(b"{}", kind="transcript")
        rendered = "".join(__import__("traceback").format_exception(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))
        self.assertNotIn(secret, repr(caught.exception.args))
        self.assertNotIn(secret, rendered)

    def test_base64_boundary_is_chain_free(self) -> None:
        secret = "/private/base64-passphrase"
        with self.assertRaises(artifact_crypto.ArtifactCryptoError) as caught:
            artifact_crypto._b64decode(secret, field_name="ciphertext")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))
        self.assertNotIn(secret, repr(caught.exception.args))

    def test_secret_tool_start_boundary_is_chain_free(self) -> None:
        secret = "/run/user/1000/secret-tool-service"
        with (
            mock.patch.object(artifact_crypto, "_secret_tool_path", return_value="/usr/bin/secret-tool"),
            mock.patch.object(artifact_crypto.subprocess, "Popen", side_effect=OSError(secret)),
        ):
            with self.assertRaises(artifact_crypto.ArtifactCryptoError) as caught:
                artifact_crypto._run_secret_tool(["lookup", "application", artifact_crypto.APP_ID])
        rendered = "".join(__import__("traceback").format_exception(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, repr(caught.exception))
        self.assertNotIn(secret, repr(caught.exception.args))
        self.assertNotIn(secret, rendered)

if __name__ == "__main__":
    unittest.main()
