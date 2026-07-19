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
    def test_fsync_retries_interrupted_calls(self) -> None:
        with mock.patch.object(path_safety.os, "fsync", side_effect=[InterruptedError(), None]) as mocked_fsync:
            path_safety._fsync_fd(123)

        self.assertEqual(mocked_fsync.call_args_list, [mock.call(123), mock.call(123)])

    def test_open_file_without_following_symlinks_rejects_relative_paths(self) -> None:
        with self.assertRaisesRegex(OSError, "must be absolute"):
            path_safety.open_file_without_following_symlinks(Path("settings.json"), os.O_RDONLY)

    def test_open_file_closes_leaf_when_directory_close_fails(self) -> None:
        close_calls: list[int] = []

        def fail_directory_close(fd: int) -> None:
            close_calls.append(fd)
            if fd == 100:
                raise OSError("directory close failed")

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_directory_close),
        ):
            with self.assertRaisesRegex(OSError, "directory close failed"):
                path_safety.open_file_without_following_symlinks(Path("/settings.json"), os.O_RDONLY)

        self.assertEqual(close_calls, [100, 101])

    def test_open_file_closes_leaf_when_directory_close_is_interrupted(self) -> None:
        close_calls: list[int] = []

        def interrupt_directory_close(fd: int) -> None:
            close_calls.append(fd)
            if fd == 100:
                raise KeyboardInterrupt

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=interrupt_directory_close),
        ):
            with self.assertRaises(KeyboardInterrupt):
                path_safety.open_file_without_following_symlinks(Path("/settings.json"), os.O_RDONLY)

        self.assertEqual(close_calls, [100, 101])

    def test_open_file_closes_next_directory_when_previous_close_fails(self) -> None:
        close_calls: list[int] = []

        def fail_first_directory_close(fd: int) -> None:
            close_calls.append(fd)
            if fd == 100:
                raise OSError("directory close failed")

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_first_directory_close),
        ):
            with self.assertRaisesRegex(OSError, "directory close failed"):
                path_safety.open_file_without_following_symlinks(Path("/tmp/settings.json"), os.O_RDONLY)

        self.assertEqual(close_calls, [100, 101, 100])

    def test_open_file_preserves_directory_close_error_when_next_close_is_interrupted(self) -> None:
        close_calls: list[int] = []

        def fail_both_closes(fd: int) -> None:
            close_calls.append(fd)
            if fd == 100:
                raise OSError("directory close failed")
            if fd == 101:
                raise KeyboardInterrupt

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_both_closes),
        ):
            with self.assertRaisesRegex(OSError, "directory close failed") as caught:
                path_safety.open_file_without_following_symlinks(Path("/tmp/settings.json"), os.O_RDONLY)

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertEqual(close_calls, [100, 101, 100])

    def test_open_file_closes_next_directory_when_previous_close_is_interrupted(self) -> None:
        close_calls: list[int] = []
        directory_close_attempts = 0

        def fail_first_directory_close(fd: int) -> None:
            nonlocal directory_close_attempts
            close_calls.append(fd)
            if fd == 100 and directory_close_attempts == 0:
                directory_close_attempts += 1
                raise KeyboardInterrupt

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_first_directory_close),
        ):
            with self.assertRaises(KeyboardInterrupt):
                path_safety.open_file_without_following_symlinks(Path("/tmp/settings.json"), os.O_RDONLY)

        self.assertIn(101, close_calls)

    def test_ensure_directory_closes_next_directory_when_previous_close_fails(self) -> None:
        close_calls: list[int] = []

        def fail_first_directory_close(fd: int) -> None:
            close_calls.append(fd)
            if fd == 100:
                raise OSError("directory close failed")

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_first_directory_close),
        ):
            with self.assertRaisesRegex(OSError, "directory close failed"):
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

        self.assertEqual(close_calls, [100, 101, 100])

    def test_ensure_directory_preserves_directory_close_error_when_next_close_is_interrupted(self) -> None:
        close_calls: list[int] = []

        def fail_both_closes(fd: int) -> None:
            close_calls.append(fd)
            if fd == 100:
                raise OSError("directory close failed")
            if fd == 101:
                raise KeyboardInterrupt

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_both_closes),
        ):
            with self.assertRaisesRegex(OSError, "directory close failed") as caught:
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertEqual(close_calls, [100, 101, 100])

    def test_ensure_directory_closes_fd_when_open_raises_value_error(self) -> None:
        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, ValueError("bad path")]),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaisesRegex(ValueError, "bad path"):
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

        mocked_close.assert_called_once_with(100)

    def test_ensure_directory_preserves_open_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, ValueError("open failed")]),
            mock.patch.object(path_safety.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(ValueError, "open failed"):
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

    def test_ensure_directory_closes_next_directory_when_previous_close_is_interrupted(self) -> None:
        close_calls: list[int] = []
        directory_close_attempts = 0

        def fail_first_directory_close(fd: int) -> None:
            nonlocal directory_close_attempts
            close_calls.append(fd)
            if fd == 100 and directory_close_attempts == 0:
                directory_close_attempts += 1
                raise KeyboardInterrupt

        with (
            mock.patch.object(path_safety.os, "open", side_effect=[100, 101]),
            mock.patch.object(path_safety.os, "close", side_effect=fail_first_directory_close),
        ):
            with self.assertRaises(KeyboardInterrupt):
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

        self.assertIn(101, close_calls)

    def test_safe_path_components_reject_null_bytes(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "invalid null byte"):
            path_safety.assert_safe_path_components(Path("/tmp/bad\x00name"))

    def test_atomic_write_rejects_relative_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be absolute"):
            path_safety.write_text_atomically_without_following_symlinks(Path("settings.json"), "{}")

    def test_atomic_write_fails_closed_without_nofollow(self) -> None:
        with (
            mock.patch.object(path_safety.os, "O_NOFOLLOW", None, create=True),
            self.assertRaisesRegex(OSError, "secure atomic write is not supported"),
        ):
            path_safety.write_text_atomically_without_following_symlinks(Path("/tmp/settings.json"), "{}")

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

    def test_atomic_write_does_not_overwrite_existing_recovery_backup_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            racing_candidate = Path(tmp) / ".settings.json.fixed.bak"
            racing_candidate.write_text("racing backup", encoding="utf-8")

            with mock.patch.object(
                path_safety.secrets,
                "token_hex",
                side_effect=["temp", "fixed", "free", "target-cleanup", "cleanup", "final-cleanup"],
            ):
                path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(racing_candidate.read_text(encoding="utf-8"), "racing backup")
            self.assertFalse((Path(tmp) / ".settings.json.free.bak").exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_atomic_write_removes_its_recovery_symlink_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("foreign", encoding="utf-8")

            def link_as_symlink(_source: object, destination: object, **_kwargs: object) -> None:
                (Path(tmp) / str(destination)).symlink_to(replacement)

            with mock.patch.object(path_safety.os, "link", side_effect=link_as_symlink):
                with self.assertRaisesRegex(OSError, "path changed during backup activation"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertFalse(list(Path(tmp).glob(".settings.json.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".settings.json.*.tmp")))

    def test_atomic_write_restores_existing_target_when_activation_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            directory_fsyncs = 0
            real_fsync = os.fsync

            def fail_activation_fsync(fd: int) -> None:
                nonlocal directory_fsyncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_fsyncs += 1
                    if directory_fsyncs == 2:
                        raise OSError("activation fsync failed")
                real_fsync(fd)

            with mock.patch.object(path_safety.os, "fsync", side_effect=fail_activation_fsync):
                with self.assertRaisesRegex(OSError, "activation fsync failed"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.bak")), [])
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.tmp")), [])

    def test_atomic_write_restores_target_when_backup_unlink_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_unlink = path_safety.os.unlink
            interrupted = False

            def unlink_then_interrupt(name: object, *args: object, **kwargs: object) -> None:
                nonlocal interrupted
                if isinstance(name, str) and name.endswith(".cleanup") and not interrupted:
                    interrupted = True
                    real_unlink(name, *args, **kwargs)
                    raise KeyboardInterrupt
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=unlink_then_interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertTrue(interrupted)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.bak")), [])
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.tmp")), [])

    def test_atomic_write_restores_existing_target_when_post_activation_inspection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_stat = os.stat
            target_stats = 0

            def fail_post_activation_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal target_stats
                if path == target.name and kwargs.get("dir_fd") is not None:
                    target_stats += 1
                    if target_stats == 5:
                        raise OSError("post-activation inspection failed")
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(path_safety.os, "stat", side_effect=fail_post_activation_stat):
                with self.assertRaisesRegex(OSError, "could not be inspected after activation"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "old")
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.bak")), [])
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.tmp")), [])

    def test_atomic_write_preserves_in_place_target_change_after_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_stat = os.stat
            target_stats = 0

            def mutate_before_post_activation_stat(
                path: object,
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                nonlocal target_stats
                if path == target.name and kwargs.get("dir_fd") is not None:
                    target_stats += 1
                    if target_stats == 5:
                        target.write_text("xyz", encoding="utf-8")
                        raise OSError("post-activation inspection failed")
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(path_safety.os, "stat", side_effect=mutate_before_post_activation_stat):
                with self.assertRaisesRegex(OSError, "could not be inspected after activation"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "xyz")
            self.assertTrue(list(Path(tmp).glob(".settings.json.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".settings.json.*.tmp")))

    def test_atomic_write_preserves_target_replacement_after_activation_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("racing target", encoding="utf-8")
            real_stat = os.stat
            target_stats = 0

            def stat_then_swap(path: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal target_stats
                if path == target.name and kwargs.get("dir_fd") is not None:
                    target_stats += 1
                    if target_stats == 5:
                        replacement.replace(target)
                return real_stat(path, *args, **kwargs)

            with mock.patch.object(path_safety.os, "stat", side_effect=stat_then_swap):
                with self.assertRaisesRegex(OSError, "changed after activation"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "racing target")
            self.assertTrue(list(Path(tmp).glob(".settings.json.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".settings.json.*.tmp")))

    def test_atomic_write_fails_closed_when_temp_file_cannot_be_made_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with mock.patch.object(path_safety.os, "fchmod", side_effect=OSError("chmod denied")):
                with self.assertRaisesRegex(OSError, "temporary file could not be made private"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_atomic_write_closes_temp_fd_when_identity_check_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            real_open = os.open
            real_close = os.close
            events: list[tuple[str, int, str]] = []

            def capture_open(*args, **kwargs):
                fd = real_open(*args, **kwargs)
                name = args[0] if args and isinstance(args[0], str) else ""
                events.append(("open", fd, name))
                return fd

            def capture_close(fd: int) -> None:
                events.append(("close", fd, ""))
                real_close(fd)

            with (
                mock.patch.object(path_safety.os, "open", side_effect=capture_open),
                mock.patch.object(path_safety.os, "close", side_effect=capture_close),
                mock.patch.object(path_safety.os, "fstat", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            temp_open_index = next(
                index
                for index, (event, _fd, name) in enumerate(events)
                if event == "open" and name.endswith(".tmp")
            )
            temp_fd = events[temp_open_index][1]
            self.assertIn(("close", temp_fd, ""), events[temp_open_index + 1 :])

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

    def test_atomic_write_preserves_primary_error_when_temp_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            real_rename = path_safety._rename_without_replacing

            def fail_activation(source: object, destination: object, *args: object, **kwargs: object) -> None:
                if str(source).endswith(".tmp") and destination == target.name:
                    raise OSError("disk full")
                real_rename(source, destination, *args, **kwargs)

            with (
                mock.patch.object(path_safety, "_rename_without_replacing", side_effect=fail_activation),
                mock.patch.object(path_safety.os, "unlink", side_effect=OSError("cleanup denied")),
            ):
                with self.assertRaisesRegex(OSError, "disk full") as caught:
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}", field_name="settings file")

            self.assertFalse(target.exists())
            self.assertTrue(any(child.name.startswith(".settings.json.") and child.name.endswith(".tmp") for child in Path(tmp).iterdir()))
        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("cleanup denied", "\n".join(caught.exception.__notes__))

    def test_atomic_write_preserves_primary_error_when_temp_cleanup_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with (
                mock.patch.object(path_safety, "_rename_without_replacing", side_effect=OSError("disk full")),
                mock.patch.object(path_safety.os, "unlink", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaisesRegex(OSError, "disk full") as caught:
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}")

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))

    def test_atomic_write_does_not_clobber_target_created_during_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("racing target", encoding="utf-8")
            real_rename = path_safety._rename_without_replacing

            def rename_then_create_target(
                source: str,
                destination: str,
                *args: object,
                **kwargs: object,
            ) -> None:
                if destination == target.name:
                    replacement.replace(target)
                real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=rename_then_create_target):
                with self.assertRaises(OSError):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "racing target")
            self.assertTrue(list(Path(tmp).glob(".settings.json.*.bak")))
            self.assertFalse(list(Path(tmp).glob(".settings.json.*.tmp")))

    def test_atomic_write_preserves_target_replacement_after_backup_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            replacement = Path(tmp) / "replacement.json"
            replacement.write_text("racing target", encoding="utf-8")
            real_rename = path_safety._rename_without_replacing

            def rename_then_swap(
                source: object,
                destination: object,
                *args: object,
                **kwargs: object,
            ) -> None:
                if source == target.name and str(destination).endswith(".cleanup"):
                    target.unlink()
                    replacement.replace(target)
                real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=rename_then_swap):
                with self.assertRaisesRegex(OSError, "changed before cleanup"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "racing target")
            self.assertFalse(list(Path(tmp).glob(".settings.json.*.bak")))

    def test_atomic_write_preserves_replaced_temp_after_activation_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            replacement = Path(tmp) / "replacement.tmp"
            replacement.write_text("racing temp", encoding="utf-8")
            real_rename = path_safety._rename_without_replacing

            def fail_activation_after_temp_swap(
                source: object,
                destination: object,
                *args: object,
                **kwargs: object,
            ) -> None:
                if str(source).endswith(".tmp") and destination == target.name:
                    replacement.replace(Path(tmp) / str(source))
                    raise OSError("activation failed")
                real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(
                path_safety,
                "_rename_without_replacing",
                side_effect=fail_activation_after_temp_swap,
            ):
                with self.assertRaisesRegex(OSError, "activation failed"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            leftovers = list(Path(tmp).glob(".settings.json.*.tmp"))
            self.assertEqual(len(leftovers), 1)
            self.assertEqual(leftovers[0].read_text(encoding="utf-8"), "racing temp")

    def test_atomic_write_does_not_clobber_target_created_during_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            racing = Path(tmp) / "racing.json"
            real_rename = path_safety._rename_without_replacing

            def rename_then_race(
                source: str,
                destination: str,
                *args: object,
                **kwargs: object,
            ) -> None:
                if source.endswith(".bak") and destination == target.name:
                    racing.write_text("racing target", encoding="utf-8")
                    racing.replace(target)
                real_rename(source, destination, *args, **kwargs)

            directory_fsyncs = 0
            real_fsync = os.fsync

            def fail_activation_fsync(fd: int) -> None:
                nonlocal directory_fsyncs
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    directory_fsyncs += 1
                    if directory_fsyncs == 2:
                        raise OSError("activation fsync failed")
                real_fsync(fd)

            with (
                mock.patch.object(path_safety, "_rename_without_replacing", side_effect=rename_then_race),
                mock.patch.object(path_safety.os, "fsync", side_effect=fail_activation_fsync),
            ):
                with self.assertRaisesRegex(OSError, "activation fsync failed"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "racing target")
            self.assertTrue(list(Path(tmp).glob(".settings.json.*.bak")))

    def test_atomic_text_write_removes_temp_file_when_encoding_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            with self.assertRaises(UnicodeEncodeError):
                path_safety.write_text_atomically_without_following_symlinks(target, "\udcff")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_atomic_write_preserves_temp_open_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            real_open = os.open

            def fail_temp_open(path, flags, mode=0o777, **kwargs):
                if flags & os.O_EXCL:
                    raise PermissionError("temp open denied")
                return real_open(path, flags, mode, **kwargs)

            with mock.patch.object(path_safety.os, "open", side_effect=fail_temp_open):
                with self.assertRaisesRegex(PermissionError, "temp open denied"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_atomic_write_closes_fd_when_fdopen_fails(self) -> None:
        with (
            mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(path_safety.os, "open", return_value=123),
            mock.patch.object(path_safety.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(path_safety.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(path_safety.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(path_safety.os, "unlink"),
            mock.patch.object(path_safety.os, "fsync"),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaisesRegex(ValueError, "bad fd"):
                path_safety.write_text_atomically_without_following_symlinks(
                    Path("/does-not-matter.txt"), "{}"
                )

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)

    def test_atomic_write_wraps_fdopen_memory_error(self) -> None:
        with (
            mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(path_safety.os, "open", return_value=123),
            mock.patch.object(path_safety.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(path_safety.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(path_safety.os, "fdopen", side_effect=MemoryError("open exhausted")),
            mock.patch.object(path_safety.os, "unlink"),
            mock.patch.object(path_safety.os, "fsync"),
            mock.patch.object(path_safety.os, "close"),
        ):
            with self.assertRaisesRegex(OSError, "temporary file could not be opened"):
                path_safety.write_text_atomically_without_following_symlinks(
                    Path("/does-not-matter.txt"), "{}"
                )

    def test_atomic_write_wraps_temporary_write_memory_error(self) -> None:
        class _FailingHandle:
            def __init__(self, fd: int) -> None:
                self.fd = fd

            def fileno(self) -> int:
                return self.fd

            def write(self, _payload: str) -> int:
                raise MemoryError("write exhausted")

            def flush(self) -> None:
                return None

            def close(self) -> None:
                os.close(self.fd)

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"

            def fdopen(fd: int, _mode: str, **_kwargs: object) -> _FailingHandle:
                return _FailingHandle(fd)

            with mock.patch.object(path_safety.os, "fdopen", side_effect=fdopen):
                with self.assertRaisesRegex(OSError, "temporary file could not be written"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.tmp")), [])

    def test_atomic_write_closes_fd_when_fdopen_is_interrupted(self) -> None:
        with (
            mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(path_safety.os, "open", return_value=123),
            mock.patch.object(path_safety.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(path_safety.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(path_safety.os, "fdopen", side_effect=KeyboardInterrupt),
            mock.patch.object(path_safety.os, "unlink"),
            mock.patch.object(path_safety.os, "fsync"),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                path_safety.write_text_atomically_without_following_symlinks(
                    Path("/does-not-matter.txt"), "{}"
                )

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)

    def test_read_text_without_following_symlinks_does_not_double_close_fd_on_read_error(self) -> None:
        class _FailingHandle:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def read(self, _size: int = -1):
                raise OSError("read failure")

            def close(self):
                return None

        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", return_value=_FailingHandle()),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaises(OSError):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        mocked_close.assert_not_called()

    def test_read_text_wraps_read_memory_error(self) -> None:
        class _FailingHandle:
            def read(self, _size: int = -1):
                raise MemoryError("read exhausted")

            def close(self):
                return None

        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", return_value=_FailingHandle()),
        ):
            with self.assertRaisesRegex(OSError, "could not be read"):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

    def test_read_text_preserves_read_interrupt_when_handle_close_fails(self) -> None:
        class _FailingHandle:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                raise OSError("close failure")

            def read(self, _size: int = -1):
                raise KeyboardInterrupt("read interrupted")

            def close(self):
                raise OSError("close failure")

        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", return_value=_FailingHandle()),
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "read interrupted") as caught:
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertIn("close failure", "\n".join(caught.exception.__notes__))

    def test_read_text_rejects_expected_file_swap_before_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("original\n", encoding="utf-8")
            path.chmod(0o600)
            expected_stat = path.lstat()
            path.rename(Path(tmp) / "state-original.txt")
            path.write_text("replacement\n", encoding="utf-8")

            with self.assertRaisesRegex(OSError, "changed before reading"):
                path_safety.read_text_without_following_symlinks(path, expected_stat=expected_stat)

    def test_read_text_rejects_same_inode_mutation_after_initial_check(self) -> None:
        class _MutatingHandle:
            def __init__(self, fd: int) -> None:
                self.fd = fd

            def fileno(self) -> int:
                return self.fd

            def read(self, _size: int = -1) -> bytes:
                path.write_text("changed-after-check\n", encoding="utf-8")
                path.chmod(0o600)
                return b"original\n"

            def close(self) -> None:
                os.close(self.fd)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.txt"
            path.write_text("original\n", encoding="utf-8")
            path.chmod(0o600)
            expected_stat = path.lstat()

            with mock.patch.object(path_safety.os, "fdopen", side_effect=lambda fd, _mode: _MutatingHandle(fd)):
                with self.assertRaisesRegex(OSError, "changed while reading"):
                    path_safety.read_text_without_following_symlinks(path, expected_stat=expected_stat)

    def test_read_text_closes_fd_when_fdopen_fails(self) -> None:
        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaisesRegex(ValueError, "bad fd"):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        mocked_close.assert_called_once_with(123)

    def test_read_text_wraps_fdopen_memory_error(self) -> None:
        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", side_effect=MemoryError("open exhausted")),
            mock.patch.object(path_safety.os, "close"),
        ):
            with self.assertRaisesRegex(OSError, "could not be opened"):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

    def test_read_text_closes_fd_when_fdopen_is_interrupted(self) -> None:
        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", side_effect=KeyboardInterrupt),
            mock.patch.object(path_safety.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        mocked_close.assert_called_once_with(123)

    def test_read_text_preserves_fdopen_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(path_safety.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(ValueError, "bad fd") as caught:
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))

    def test_read_text_preserves_fdopen_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(path_safety, "open_file_without_following_symlinks", return_value=123),
            mock.patch.object(path_safety, "assert_fd_is_regular_private_file"),
            mock.patch.object(path_safety.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(path_safety.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(ValueError, "bad fd") as caught:
                path_safety.read_text_without_following_symlinks(Path("/does-not-matter.txt"))

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))

    def test_atomic_write_preserves_fdopen_error_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(path_safety.os, "open", return_value=123),
            mock.patch.object(path_safety.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(path_safety.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(path_safety.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(path_safety.os, "unlink"),
            mock.patch.object(path_safety.os, "fsync"),
            mock.patch.object(path_safety.os, "close", side_effect=OSError("close failed")),
        ):
            with self.assertRaisesRegex(ValueError, "bad fd") as caught:
                path_safety.write_text_atomically_without_following_symlinks(
                    Path("/does-not-matter.txt"), "{}"
                )

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))

    def test_atomic_write_preserves_fdopen_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(path_safety.os, "open", return_value=123),
            mock.patch.object(path_safety.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(path_safety.os, "stat", side_effect=FileNotFoundError),
            mock.patch.object(path_safety.os, "fdopen", side_effect=ValueError("bad fd")),
            mock.patch.object(path_safety.os, "unlink"),
            mock.patch.object(path_safety.os, "fsync"),
            mock.patch.object(path_safety.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(ValueError, "bad fd") as caught:
                path_safety.write_text_atomically_without_following_symlinks(
                    Path("/does-not-matter.txt"), "{}"
                )

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))

    def test_atomic_write_preserves_primary_error_when_temporary_handle_close_fails(self) -> None:
        class _Handle:
            def __init__(self, fd: int) -> None:
                self.fd = fd

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self.close()
                return False

            def fileno(self) -> int:
                return self.fd

            def write(self, payload: str) -> int:
                return len(payload)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                raise OSError("temporary handle close failed")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            real_fsync = os.fsync
            handle: _Handle | None = None

            def fdopen(fd: int, mode: str, **kwargs: object) -> _Handle:
                nonlocal handle
                del mode, kwargs
                handle = _Handle(fd)
                return handle

            def fail_file_fsync(fd: int) -> None:
                if handle is not None and fd == handle.fd:
                    raise OSError("temporary file sync failed")
                real_fsync(fd)

            try:
                with (
                    mock.patch.object(path_safety.os, "fdopen", side_effect=fdopen),
                    mock.patch.object(path_safety.os, "fsync", side_effect=fail_file_fsync),
                ):
                    with self.assertRaisesRegex(OSError, "temporary file sync failed") as caught:
                        path_safety.write_text_atomically_without_following_symlinks(target, "new")
            finally:
                if handle is not None:
                    os.close(handle.fd)

            self.assertIn("temporary handle close failed", "\n".join(caught.exception.__notes__))

    def test_atomic_write_aborts_when_temporary_handle_close_fails_without_primary_error(self) -> None:
        class _Handle:
            def __init__(self, fd: int) -> None:
                self.fd = fd

            def fileno(self) -> int:
                return self.fd

            def write(self, payload: str) -> int:
                return len(payload)

            def flush(self) -> None:
                return None

            def close(self) -> None:
                raise OSError("temporary handle close failed")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            handle: _Handle | None = None

            def fdopen(fd: int, mode: str, **kwargs: object) -> _Handle:
                nonlocal handle
                del mode, kwargs
                handle = _Handle(fd)
                return handle

            try:
                with mock.patch.object(path_safety.os, "fdopen", side_effect=fdopen):
                    with self.assertRaisesRegex(OSError, "temporary handle close failed"):
                        path_safety.write_text_atomically_without_following_symlinks(target, "new")
            finally:
                if handle is not None:
                    try:
                        os.close(handle.fd)
                    except OSError:
                        pass

            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).glob(".settings.json.*.tmp")), [])

    def test_atomic_write_preserves_success_when_parent_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            real_ensure = path_safety.ensure_directory_without_following_symlinks
            real_close = os.close
            parent_fds: list[int] = []

            def ensure_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_ensure(*args, **kwargs)
                parent_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                if fd in parent_fds:
                    raise OSError("parent close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", side_effect=ensure_wrapper),
                    mock.patch.object(path_safety.os, "close", side_effect=close_wrapper),
                ):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")
            finally:
                for fd in parent_fds:
                    real_close(fd)

            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_atomic_write_preserves_success_when_parent_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            real_ensure = path_safety.ensure_directory_without_following_symlinks
            real_close = os.close
            parent_fds: list[int] = []

            def ensure_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_ensure(*args, **kwargs)
                parent_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                if fd in parent_fds:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.object(path_safety, "ensure_directory_without_following_symlinks", side_effect=ensure_wrapper),
                    mock.patch.object(path_safety.os, "close", side_effect=close_wrapper),
                ):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")
            finally:
                for fd in parent_fds:
                    real_close(fd)

            self.assertEqual(target.read_text(encoding="utf-8"), "new")

    def test_atomic_write_preserves_success_when_recovery_backup_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_unlink = path_safety.os.unlink

            def fail_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and ".bak." in name and name.endswith(".cleanup"):
                    raise OSError("backup cleanup failed")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=fail_backup_cleanup):
                path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(len(list(Path(tmp).glob(".settings.json.*.bak"))), 1)

    def test_atomic_write_preserves_success_when_recovery_backup_cleanup_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_unlink = path_safety.os.unlink

            def interrupt_backup_cleanup(name: object, *args: object, **kwargs: object) -> None:
                if isinstance(name, str) and ".bak." in name and name.endswith(".cleanup"):
                    raise KeyboardInterrupt("backup cleanup interrupted")
                real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=interrupt_backup_cleanup):
                path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(len(list(Path(tmp).glob(".settings.json.*.bak"))), 1)

    def test_atomic_write_preserves_success_when_recovery_backup_changes_after_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            replacement = Path(tmp) / "replacement.txt"
            replacement.write_text("foreign backup", encoding="utf-8")
            real_stat = path_safety.os.stat
            backup_stats = 0

            def stat_then_replace(name: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal backup_stats
                result = real_stat(name, *args, **kwargs)
                if isinstance(name, str) and name.endswith(".bak"):
                    backup_stats += 1
                    if backup_stats == 2:
                        backup_path = Path(tmp) / name
                        backup_path.unlink()
                        replacement.replace(backup_path)
                        return real_stat(name, *args, **kwargs)
                return result

            with mock.patch.object(path_safety.os, "stat", side_effect=stat_then_replace):
                path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            backups = list(Path(tmp).glob(".settings.json.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "foreign backup")

    def test_atomic_write_preserves_recovery_backup_when_changed_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            replacement = Path(tmp) / "replacement.txt"
            replacement.write_text("foreign backup", encoding="utf-8")
            real_stat = path_safety.os.stat
            backup_stats = 0

            def stat_then_replace_after_check(name: object, *args: object, **kwargs: object) -> os.stat_result:
                nonlocal backup_stats
                result = real_stat(name, *args, **kwargs)
                if isinstance(name, str) and name.endswith(".bak"):
                    backup_stats += 1
                    if backup_stats == 2:
                        backup_path = Path(tmp) / name
                        backup_path.unlink()
                        replacement.replace(backup_path)
                return result

            with mock.patch.object(path_safety.os, "stat", side_effect=stat_then_replace_after_check):
                path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            backups = list(Path(tmp).glob(".settings.json.*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "foreign backup")

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
