import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import cli, secure_delete


class SecureArtifactDeleteTest(unittest.TestCase):
    def test_secure_wipe_rejects_invalid_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "recording.flac"
            artifact.write_bytes(b"secret")
            expected_stat = artifact.stat()
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaisesRegex(RuntimeError, "parent is invalid"):
                    secure_delete.secure_wipe_regular_file_at(
                        True,
                        artifact.name,
                        expected_stat,
                        field_name="recording",
                    )
                with self.assertRaisesRegex(RuntimeError, "name is invalid"):
                    secure_delete.secure_wipe_regular_file_at(
                        parent_fd,
                        "../recording.flac",
                        expected_stat,
                        field_name="recording",
                    )
                with self.assertRaisesRegex(RuntimeError, "identity is invalid"):
                    secure_delete.secure_wipe_regular_file_at(
                        parent_fd,
                        artifact.name,
                        object(),
                        field_name="recording",
                    )
            finally:
                os.close(parent_fd)

    def test_secure_wipe_rejects_hardlink_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "recording.flac"
            alias = Path(tmp) / "alias.flac"
            artifact.write_bytes(b"secret")
            os.link(artifact, alias)
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaisesRegex(RuntimeError, "changed before secure deletion"):
                    secure_delete.secure_wipe_regular_file_at(
                        parent_fd,
                        artifact.name,
                        artifact.stat(),
                        field_name="recording",
                    )
            finally:
                os.close(parent_fd)
            self.assertEqual(artifact.read_bytes(), b"secret")

    def test_secure_wipe_uses_lseek_write_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "recording.flac"
            artifact.write_bytes(b"secret audio")
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with (
                    mock.patch.object(secure_delete, "_OS_PWRITE", None),
                    mock.patch.object(secure_delete, "_OS_LSEEK", wraps=os.lseek) as lseek,
                    mock.patch.object(secure_delete, "_OS_WRITE", wraps=os.write) as write,
                ):
                    secure_delete.secure_wipe_regular_file_at(
                        parent_fd,
                        artifact.name,
                        artifact.stat(),
                        field_name="recording",
                    )
            finally:
                os.close(parent_fd)
            self.assertEqual(artifact.read_bytes(), b"\x00" * len(b"secret audio"))
            self.assertTrue(lseek.called)
            self.assertTrue(write.called)

    def test_secure_wipe_removes_concurrent_append_before_sync(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "recording.flac"
            secret = b"secret audio"
            artifact.write_bytes(secret)
            writer_fd = os.open(artifact, os.O_WRONLY)
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_pwrite = os.pwrite

            def wipe_then_append(fd: int, payload: bytes, offset: int) -> int:
                written = real_pwrite(fd, payload, offset)
                os.lseek(writer_fd, 0, os.SEEK_END)
                os.write(writer_fd, b"late secret")
                return written

            try:
                with mock.patch.object(secure_delete, "_OS_PWRITE", side_effect=wipe_then_append):
                    secure_delete.secure_wipe_regular_file_at(
                        parent_fd,
                        artifact.name,
                        artifact.stat(),
                        field_name="recording",
                    )
            finally:
                os.close(writer_fd)
                os.close(parent_fd)

            self.assertEqual(artifact.read_bytes(), b"\x00" * len(secret))

    def test_delete_artifact_wipes_claimed_content_before_unlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "recording.wav"
            secret = b"private audio and transcript data"
            artifact.write_bytes(secret)
            observed: list[bytes] = []
            real_unlink = cli.os.unlink

            def observe_unlink(name: str, *, dir_fd: int = -1) -> None:
                fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
                try:
                    observed.append(os.read(fd, len(secret)))
                finally:
                    os.close(fd)
                real_unlink(name, dir_fd=dir_fd)

            with mock.patch.object(cli.os, "unlink", side_effect=observe_unlink):
                self.assertTrue(cli.delete_artifact(artifact))

        self.assertEqual(observed, [b"\x00" * len(secret)])
        self.assertFalse(artifact.exists())

    def test_recording_and_transcriber_cleanup_paths_use_shared_wipe(self):
        for module_name in ("recorder.py", "transcriber.py"):
            source = (
                Path(__file__).resolve().parents[1]
                / "src/speed_of_cinnamon"
                / module_name
            ).read_text(encoding="utf-8")
            self.assertIn("secure_wipe_regular_file_at", source)

    def test_secure_wipe_redacts_close_failure_note(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "recording.flac"
            artifact.write_bytes(b"secret audio")
            parent_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                expected_stat = artifact.stat()
                real_close = os.close

                def close_and_fail(fd: int) -> None:
                    real_close(fd)
                    raise OSError("/private/secret-recording.flac")

                with (
                    mock.patch.object(secure_delete, "_OS_FSYNC", side_effect=OSError("wipe failed")),
                    mock.patch.object(secure_delete, "_OS_CLOSE", side_effect=close_and_fail),
                ):
                    with self.assertRaises(OSError) as raised:
                        secure_delete.secure_wipe_regular_file_at(
                            parent_fd,
                            artifact.name,
                            expected_stat,
                            field_name="recording",
                        )
            finally:
                os.close(parent_fd)

        notes = "\n".join(getattr(raised.exception, "__notes__", ()))
        self.assertEqual(notes, "recording secure deletion close failed")
        self.assertNotIn("secret-recording.flac", notes)


if __name__ == "__main__":
    unittest.main()
