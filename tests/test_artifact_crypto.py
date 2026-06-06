from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import artifact_crypto


class ArtifactCryptoTest(unittest.TestCase):
    def test_passphrase_encrypts_and_decrypts_payload(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "correct horse battery staple"}, clear=False):
            encrypted, mode = artifact_crypto.encrypt_bytes(b"private transcript", "passphrase", kind="transcript")
            self.assertEqual(mode, "passphrase")
            self.assertNotIn(b"private transcript", encrypted)
            self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"private transcript")

    def test_passphrase_can_come_from_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "passphrase.txt"
            path.write_text("file-passphrase\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_FILE_ENV: str(path)}, clear=False):
                encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
                self.assertEqual(mode, "passphrase")
                self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_keyring_mode_falls_back_to_passphrase_when_cli_keyring_fails(self) -> None:
        with (
            mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "fallback passphrase"}, clear=False),
            mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
        ):
            encrypted, mode = artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")
            self.assertEqual(mode, "passphrase")
            self.assertIn(b'"fallback_from":"keyring"', encrypted)
            self.assertEqual(artifact_crypto.decrypt_bytes(encrypted, kind="transcript"), b"payload")

    def test_keyring_mode_fails_closed_without_passphrase_fallback(self) -> None:
        with (
            mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "", artifact_crypto.PASSPHRASE_FILE_ENV: ""}, clear=False),
            mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
        ):
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "passphrase fallback is not configured"):
                artifact_crypto.encrypt_bytes(b"payload", "keyring", kind="transcript")

    def test_keyring_secret_must_decode_to_32_bytes(self) -> None:
        bad_secret = artifact_crypto._b64encode(b"too short").encode("ascii")
        with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "invalid length"):
            artifact_crypto._parse_keyring_secret(bad_secret)

    def test_decryption_rejects_wrong_artifact_kind(self) -> None:
        with mock.patch.dict(os.environ, {artifact_crypto.PASSPHRASE_ENV: "kind passphrase"}, clear=False):
            encrypted, _ = artifact_crypto.encrypt_bytes(b"payload", "passphrase", kind="transcript")
            with self.assertRaisesRegex(artifact_crypto.ArtifactCryptoError, "kind does not match"):
                artifact_crypto.decrypt_bytes(encrypted, kind="recording")


if __name__ == "__main__":
    unittest.main()
