from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import path_safety


class PathSafetyTest(unittest.TestCase):
    def test_atomic_write_creates_parent_without_pathlib_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "settings.json"
            with mock.patch.object(Path, "mkdir", side_effect=AssertionError("unsafe mkdir")):
                path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_atomic_write_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            os.symlink(real, link)

            with self.assertRaises(OSError):
                path_safety.write_text_atomically_without_following_symlinks(link / "settings.json", "{}")

    def test_atomic_bytes_write_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            os.symlink(real, link)

            with self.assertRaises(OSError):
                path_safety.write_bytes_atomically_without_following_symlinks(link / "transcript.txt", b"old")

            self.assertFalse((real / "transcript.txt").exists())

    def test_atomic_bytes_write_creates_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "transcript.txt"
            path_safety.write_bytes_atomically_without_following_symlinks(target, b"old transcript")

            self.assertEqual(target.read_bytes(), b"old transcript")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_read_text_without_following_symlinks_does_not_double_close_fd_on_read_error(self) -> None:
        class _FailingHandle:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self, _size: int = -1):
                raise OSError("read failure")

        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety.os, "fdopen", return_value=_FailingHandle()),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaises(OSError):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        mocked_close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
