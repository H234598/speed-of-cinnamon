from __future__ import annotations

import os
import stat
import types
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import path_safety


class PathSafetyTest(unittest.TestCase):
    def test_open_file_without_following_symlinks_rejects_relative_paths(self) -> None:
        with self.assertRaisesRegex(OSError, "must be absolute"):
            path_safety.open_file_without_following_symlinks(Path("settings.json"), os.O_RDONLY)

    def test_atomic_write_rejects_relative_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be absolute"):
            path_safety.write_text_atomically_without_following_symlinks(Path("settings.json"), "{}")

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

    def test_atomic_write_rejects_symlink_target_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "settings.json"
            outside = root / "outside.json"
            outside.write_text("outside", encoding="utf-8")
            os.symlink(outside, target)

            with self.assertRaisesRegex(OSError, "settings export path must not be a symlink"):
                path_safety.write_text_atomically_without_following_symlinks(
                    target,
                    "{}",
                    field_name="settings export path",
                )

            self.assertTrue(target.is_symlink())
            self.assertEqual(outside.read_text(encoding="utf-8"), "outside")

    def test_atomic_bytes_write_creates_private_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "transcript.txt"
            path_safety.write_bytes_atomically_without_following_symlinks(target, b"old transcript")

            self.assertEqual(target.read_bytes(), b"old transcript")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_atomic_write_fsyncs_temp_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with mock.patch.object(path_safety.os, "fsync", wraps=os.fsync) as mocked_fsync:
                path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertEqual(target.read_text(encoding="utf-8"), "{}")
            self.assertGreaterEqual(mocked_fsync.call_count, 2)

    def test_atomic_write_removes_temp_file_when_file_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            fsynced_modes: list[int] = []
            real_fsync = os.fsync

            def failing_file_fsync(fd: int) -> None:
                mode = os.fstat(fd).st_mode
                fsynced_modes.append(mode)
                if stat.S_ISREG(mode):
                    raise OSError("sync failed")
                real_fsync(fd)

            with mock.patch.object(path_safety.os, "fsync", side_effect=failing_file_fsync):
                with self.assertRaisesRegex(OSError, "sync failed"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsynced_modes))

    def test_atomic_write_reports_temp_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with (
                mock.patch.object(path_safety.os, "replace", side_effect=OSError("disk full")),
                mock.patch.object(path_safety.os, "unlink", side_effect=OSError("cleanup denied")),
            ):
                with self.assertRaisesRegex(OSError, "failed to remove temporary file for settings file"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}", field_name="settings file")

            self.assertFalse(target.exists())
            self.assertTrue(any(child.name.startswith(".settings.json.") and child.name.endswith(".tmp") for child in Path(tmp).iterdir()))

    def test_atomic_text_write_removes_temp_file_when_encoding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with self.assertRaises(UnicodeEncodeError):
                path_safety.write_text_atomically_without_following_symlinks(target, "\udcff")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

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
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", return_value=_FailingHandle()),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaises(OSError):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        mocked_close.assert_not_called()

    def test_read_text_with_max_bytes_rejects_larger_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("abcde", encoding="utf-8")

            with self.assertRaisesRegex(OSError, "state file path is too large"):
                path_safety.read_text_without_following_symlinks(
                    path,
                    field_name="state file path",
                    max_bytes=4,
                )

    def test_read_text_with_max_bytes_allows_payload_at_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("abcd", encoding="utf-8")

            text = path_safety.read_text_without_following_symlinks(
                path,
                field_name="state file path",
                max_bytes=4,
            )

        self.assertEqual(text, "abcd")

    def test_read_text_default_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("abcde", encoding="utf-8")

            with mock.patch("speed_of_cinnamon.path_safety.DEFAULT_MAX_TEXT_READ_BYTES", 4):
                with self.assertRaisesRegex(OSError, "state file path is too large"):
                    path_safety.read_text_without_following_symlinks(path, field_name="state file path")

    def test_read_text_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("secret\n", encoding="utf-8")
            hardlink = Path(tmp) / "state-hardlink.txt"
            try:
                os.link(path, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(OSError, "must not be hardlinked"):
                path_safety.read_text_without_following_symlinks(hardlink, field_name="state file path")

    def test_read_text_rejects_world_writable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("secret\n", encoding="utf-8")
            path.chmod(0o666)

            with self.assertRaisesRegex(OSError, "must be private"):
                path_safety.read_text_without_following_symlinks(
                    path,
                    field_name="state file path",
                    require_private_mode=True,
                )

    def test_read_text_rejects_file_with_foreign_owner(self) -> None:
        with mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123):
            with mock.patch.object(
                path_safety.os,
                "fstat",
                return_value=types.SimpleNamespace(
                    st_mode=0o100600,
                    st_nlink=1,
                    st_uid=path_safety.os.getuid() + 1,
                ),
            ):
                with mock.patch.object(path_safety.os, "close"):
                    with self.assertRaisesRegex(OSError, "must be owned by the current user"):
                        path_safety.read_text_without_following_symlinks(
                            Path("/state.txt"),
                            field_name="state file path",
                        )

    def test_read_text_rejects_symlink_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real.txt"
            real.write_text("secret\n", encoding="utf-8")
            link = root / "state-link.txt"
            os.symlink(real, link)

            with self.assertRaises(OSError):
                path_safety.read_text_without_following_symlinks(link, field_name="state file path")

    def test_read_text_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "state.fifo"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(OSError, "must be a regular file"):
                path_safety.read_text_without_following_symlinks(fifo, field_name="state file path")


if __name__ == "__main__":
    unittest.main()
