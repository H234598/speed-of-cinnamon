from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import artifact_crypto


STRONG_PASSPHRASE = artifact_crypto._b64encode(bytes(range(32)))
SECOND_STRONG_PASSPHRASE = artifact_crypto._b64encode(bytes(range(32, 64)))


class ArtifactCryptoTest(unittest.TestCase):
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

    def test_default_passphrase_rotation_failure_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            path.write_text("short\n", encoding="utf-8")
            path.chmod(0o600)
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto.os.replace", side_effect=OSError("replace failed")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file could not be generated"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

            self.assertEqual(path.read_text(encoding="utf-8"), "short\n")

    def test_passphrase_file_must_be_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text("file-passphrase\n", encoding="utf-8")
            path.chmod(0o644)
            with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: str(path)}, clear=False):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase file must be private"):
                    artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")

    def test_keyring_mode_falls_back_to_passphrase_when_cli_keyring_fails(self) -> None:
        with (
            mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: SECOND_STRONG_PASSPHRASE}, clear=False),
            mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
        ):
            encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")
            self.assertEqual(mode, "passphrase")
            self.assertIn(b'"fallback_from":"keyring"', encrypted)
            self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_keyring_mode_does_not_generate_default_passphrase_fallback_when_cli_keyring_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.key"
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=path),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase fallback failed"):
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
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase fallback failed"):
                    artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_mode_fails_closed_when_passphrase_fallback_is_weak(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "weak", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
                mock.patch("speed_of_cinnamon.artifact_crypto.default_passphrase_file", return_value=Path(tmp) / "missing.key"),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
            ):
                with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase fallback failed"):
                    artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_secret_must_decode_to_32_bytes(self) -> None:
        bad_secret = artifact_crypto._b64encode(b"too short").encode("ascii")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "invalid length"):
            artifact_crypto._parse_keyring_secret(bad_secret)

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

    def test_decryption_rejects_wrong_artifact_kind(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: STRONG_PASSPHRASE}, clear=False):
            encrypted, _ = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "kind does not match"):
                artifact_crypto.decrypt_bytes(encrypted, kind="recording")

    def test_decryption_can_require_encrypted_payload(self) -> None:
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "envelope is missing"):
            artifact_crypto.decrypt_bytes(b"plaintext", kind="transcript", require_encrypted=True)


if __name__ == "__main__":
    unittest.main()
