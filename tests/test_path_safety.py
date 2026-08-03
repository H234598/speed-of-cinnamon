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
    def _conditional_symbols(self):
        expected_target = getattr(path_safety, "ExpectedTarget", None)
        replace = getattr(path_safety, "replace_bytes_atomically_if_identity", None)
        unlink = getattr(path_safety, "unlink_file_if_identity", None)
        self.assertIsNotNone(expected_target)
        self.assertIsNotNone(replace)
        self.assertIsNotNone(unlink)
        return expected_target, replace, unlink

    def _capture_expected(self, expected_target, path: Path, *, require_same_version: bool = True):
        fd = path_safety.open_file_without_following_symlinks(path, os.O_RDONLY)
        try:
            return self._capture_expected_fd(
                expected_target,
                fd,
                require_same_version=require_same_version,
            )
        finally:
            os.close(fd)

    def _capture_expected_fd(self, expected_target, fd: int, *, require_same_version: bool = True):
        try:
            return expected_target.captured(fd, require_same_version=require_same_version)
        except TypeError as exc:
            self.fail(f"ExpectedTarget.captured must accept a secure file descriptor: {exc}")

    def test_cleanup_note_helper_preserves_primary_legacy_exception(self) -> None:
        class LegacyBaseException(BaseException):
            pass

        primary = LegacyBaseException("primary")
        cleanup_error = OSError("cleanup")

        path_safety._note_cleanup_failure(primary, cleanup_error)

        self.assertIsInstance(primary, LegacyBaseException)
        self.assertEqual(getattr(primary, "__notes__", ()), ["secure path cleanup failed"])

    def test_exception_note_helper_survives_hostile_add_note_without_args_mutation(self) -> None:
        class RuntimeNoteException(BaseException):
            def add_note(self, note):
                raise RuntimeError("note hook failed")

        class InterruptNoteException(BaseException):
            def add_note(self, note):
                raise KeyboardInterrupt("note hook interrupted")

        for exception_type in (RuntimeNoteException, InterruptNoteException):
            with self.subTest(exception_type=exception_type):
                error = exception_type("primary")
                original_args = error.args

                path_safety._add_exception_note(error, "safe note")

                self.assertEqual(error.args, original_args)
                self.assertEqual(getattr(error, "__notes__", ()), ["safe note"])

    def test_exception_note_helper_rejects_non_string_control_and_oversized_notes(self) -> None:
        for invalid_note in (b"bytes", "line\nfeed", "x" * 257):
            with self.subTest(invalid_note=invalid_note):
                error = OSError("primary")
                path_safety._add_exception_note(error, invalid_note)
                self.assertEqual(error.args, ("primary",))
                self.assertEqual(getattr(error, "__notes__", ()), ())

    def test_cleanup_note_does_not_expose_cleanup_error_details(self) -> None:
        primary = OSError("primary")
        cleanup_error = OSError("failed /secret/token=abc123 with password=hidden")

        path_safety._note_cleanup_failure(primary, cleanup_error)

        self.assertEqual(primary.args, ("primary",))
        notes = getattr(primary, "__notes__", ())
        self.assertEqual(notes, ["secure path cleanup failed"])
        self.assertNotIn("secret", repr(notes))
        self.assertNotIn("abc123", repr(notes))
        self.assertNotIn("hidden", repr(notes))

    def test_fsync_retries_interrupted_calls(self) -> None:
        with mock.patch.object(path_safety.os, "fsync", side_effect=[InterruptedError(), None]) as mocked_fsync:
            path_safety._fsync_fd(123)

        self.assertEqual(mocked_fsync.call_args_list, [mock.call(123), mock.call(123)])

    def test_open_file_without_following_symlinks_rejects_relative_paths(self) -> None:
        with self.assertRaisesRegex(OSError, "must be absolute"):
            path_safety.open_file_without_following_symlinks(Path("settings.json"), os.O_RDONLY)

    def test_secure_open_callers_reject_invalid_nofollow_before_os_open(self) -> None:
        callers = (
            lambda: path_safety.open_file_without_following_symlinks(Path("/settings.json"), os.O_RDONLY),
            lambda: path_safety.ensure_directory_without_following_symlinks(Path("/settings")),
            lambda: path_safety.write_text_atomically_without_following_symlinks(Path("/settings.json"), "{}"),
        )
        invalid_flags = (None, 0, False, True, "1", 1.0)
        for invalid_flag in invalid_flags:
            for caller in callers:
                with self.subTest(invalid_flag=invalid_flag, caller=caller):
                    with (
                        mock.patch.object(path_safety.os, "O_NOFOLLOW", invalid_flag, create=True),
                        mock.patch.object(
                            path_safety.os,
                            "open",
                            side_effect=OSError("os.open called before validation"),
                        ) as mocked_open,
                    ):
                        with self.assertRaisesRegex(OSError, "secure no-follow flag is invalid"):
                            caller()
                    mocked_open.assert_not_called()

    def test_secure_open_accepts_positive_integer_nofollow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("{}", encoding="utf-8")
            fd = path_safety.open_file_without_following_symlinks(target, os.O_RDONLY)
            try:
                self.assertEqual(os.read(fd, 2), b"{}")
            finally:
                os.close(fd)

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
            with self.assertRaisesRegex(OSError, "secure cleanup failed"):
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
            with self.assertRaisesRegex(OSError, "secure cleanup failed"):
                path_safety.open_file_without_following_symlinks(Path("/tmp/settings.json"), os.O_RDONLY)

        self.assertEqual(close_calls, [100, 101])

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
            with self.assertRaisesRegex(OSError, "secure cleanup failed") as caught:
                path_safety.open_file_without_following_symlinks(Path("/tmp/settings.json"), os.O_RDONLY)

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertEqual(close_calls, [100, 101])

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
            with self.assertRaisesRegex(OSError, "secure cleanup failed"):
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

        self.assertEqual(close_calls, [100, 101])

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
            with self.assertRaisesRegex(OSError, "secure cleanup failed") as caught:
                path_safety.ensure_directory_without_following_symlinks(Path("/tmp/settings"))

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertEqual(close_calls, [100, 101])

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
            self.assertRaisesRegex(OSError, "secure no-follow flag is invalid"),
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

    def test_atomic_write_fails_closed_on_quarantine_hardlink_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_unlink = path_safety.os.unlink
            injected = False

            def add_hardlink_before_unlink(name, *args, **kwargs):
                nonlocal injected
                if not injected and isinstance(name, str) and name.endswith(".cleanup"):
                    os.link(
                        name,
                        ".attacker-hardlink",
                        src_dir_fd=kwargs["dir_fd"],
                        dst_dir_fd=kwargs["dir_fd"],
                        follow_symlinks=False,
                    )
                    injected = True
                return real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=add_hardlink_before_unlink):
                with self.assertRaises(OSError):
                    path_safety.write_text_atomically_without_following_symlinks(
                        target,
                        "new",
                        field_name="settings path",
                    )

            self.assertTrue(injected)

    def test_atomic_quarantine_and_restore_use_held_source_fd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_rename = path_safety._rename_without_replacing
            quarantine_fds = []
            restore_fds = []

            def observe_rename(source, destination, *args, **kwargs):
                if source == target.name and isinstance(destination, str) and destination.endswith(".cleanup"):
                    quarantine_fds.append(kwargs.get("expected_source_fd"))
                elif isinstance(source, str) and source.endswith(".cleanup") and destination == target.name:
                    restore_fds.append(kwargs.get("expected_source_fd"))
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=observe_rename):
                path_safety.write_text_atomically_without_following_symlinks(
                    target,
                    "new",
                    field_name="settings path",
                )

            self.assertTrue(quarantine_fds)
            self.assertTrue(all(isinstance(fd, int) for fd in quarantine_fds))

            target.write_text("old", encoding="utf-8")
            real_unlink = path_safety.os.unlink
            failed = False

            def fail_quarantine_unlink(name, *args, **kwargs):
                nonlocal failed
                if not failed and isinstance(name, str) and name.endswith(".cleanup"):
                    failed = True
                    raise OSError("quarantine unlink failed")
                return real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=fail_quarantine_unlink):
                with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=observe_rename):
                    with self.assertRaisesRegex(OSError, "quarantine unlink failed"):
                        path_safety.write_text_atomically_without_following_symlinks(
                            target,
                            "new",
                            field_name="settings path",
                        )

            self.assertTrue(restore_fds)
            self.assertTrue(all(isinstance(fd, int) for fd in restore_fds))
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

    def test_atomic_write_rejects_quarantine_replacement_without_restoring_foreign_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "settings.json"
            foreign = root / "foreign.txt"
            target.write_text("old", encoding="utf-8")
            foreign.write_text("foreign", encoding="utf-8")
            real_rename = path_safety._rename_without_replacing
            raced = False

            def replace_quarantine(source, destination, *args, **kwargs):
                nonlocal raced
                result = real_rename(source, destination, *args, **kwargs)
                if not raced and source == target.name and str(destination).endswith(".cleanup"):
                    foreign.replace(root / str(destination))
                    raced = True
                return result

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=replace_quarantine):
                with self.assertRaisesRegex(OSError, "changed before cleanup"):
                    path_safety.write_text_atomically_without_following_symlinks(target, "new")

            self.assertTrue(raced)
            self.assertEqual(target.read_text(encoding="utf-8"), "old")

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
        self.assertNotIn("cleanup denied", "\n".join(caught.exception.__notes__))

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

    def test_expected_target_model_is_explicit_and_immutable(self) -> None:
        expected_target, _replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"
            target.write_bytes(b"old")
            captured = self._capture_expected(expected_target, target)

        missing = expected_target.missing()
        unknown = expected_target.unknown()
        self.assertEqual(missing.kind.value, "missing")
        self.assertEqual(captured.kind.value, "captured")
        self.assertEqual(unknown.kind.value, "unknown")
        self.assertTrue(captured.require_same_version)
        with self.assertRaises(AttributeError):
            captured.require_same_version = False

    def test_expected_target_model_rejects_ambiguous_or_unsafe_capture(self) -> None:
        expected_target, _replace, _unlink = self._conditional_symbols()
        with self.assertRaises(TypeError):
            expected_target.captured(None)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            regular = root / "regular"
            regular.write_bytes(b"data")
            regular_fd = path_safety.open_file_without_following_symlinks(regular, os.O_RDONLY)
            try:
                with self.assertRaises(TypeError):
                    expected_target.captured(regular_fd, require_same_version=1)
            finally:
                os.close(regular_fd)

            symlink = root / "symlink"
            symlink.symlink_to(regular)
            if not hasattr(os, "O_PATH") or not hasattr(os, "O_NOFOLLOW"):
                self.skipTest("O_PATH/O_NOFOLLOW unavailable")
            symlink_fd = os.open(symlink, os.O_PATH | os.O_NOFOLLOW)
            try:
                with self.assertRaisesRegex(ValueError, "regular file"):
                    self._capture_expected_fd(expected_target, symlink_fd)
            finally:
                os.close(symlink_fd)

            hardlink = root / "hardlink"
            os.link(regular, hardlink)
            hardlink_fd = path_safety.open_file_without_following_symlinks(regular, os.O_RDONLY)
            try:
                with self.assertRaisesRegex(ValueError, "hardlinked"):
                    self._capture_expected_fd(expected_target, hardlink_fd)
            finally:
                os.close(hardlink_fd)

    def test_expected_target_version_capture_rejects_oversized_file(self) -> None:
        expected_target, _replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"
            target.write_bytes(b"x" * (path_safety.DEFAULT_MAX_TEXT_READ_BYTES + 1))
            fd = path_safety.open_file_without_following_symlinks(target, os.O_RDONLY)
            try:
                with self.assertRaisesRegex(OSError, "too large"):
                    self._capture_expected_fd(expected_target, fd)
            finally:
                os.close(fd)

    def test_conditional_replace_missing_creates_without_clobbering(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            replace(target, b"new", expected_target.missing())
            self.assertEqual(target.read_bytes(), b"new")

            target.write_bytes(b"foreign")
            with self.assertRaisesRegex(OSError, "expected target to be missing"):
                replace(target, b"replacement", expected_target.missing())
            self.assertEqual(target.read_bytes(), b"foreign")

    def test_conditional_apis_fail_closed_for_unknown_target(self) -> None:
        expected_target, replace, unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            for operation in (
                lambda: replace(target, b"new", expected_target.unknown()),
                lambda: unlink(target, expected_target.unknown()),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(OSError, "expected target is unknown"):
                        operation()
                    self.assertEqual(target.read_bytes(), b"old")
                    self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_legitimate_renames_do_not_change_identity(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)

            replace(target, b"new", expected)

            self.assertEqual(target.read_bytes(), b"new")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_accepts_large_staged_payload_without_digest(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            payload = b"x" * (path_safety.DEFAULT_MAX_TEXT_READ_BYTES + 1)

            replace(target, payload, expected)

            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_rejects_mutated_large_staged_payload(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            payload = b"x" * (path_safety.DEFAULT_MAX_TEXT_READ_BYTES + 1)
            replacement_payload = b"y" * len(payload)
            real_rename = path_safety._rename_without_replacing
            real_write = os.write
            mutated = False

            def mutate_staged(source, destination, *args, **kwargs):
                nonlocal mutated
                if not mutated and source == "staged" and destination == target.name:
                    fd = os.open(
                        source,
                        os.O_WRONLY | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0),
                        dir_fd=kwargs["directory_fd"],
                    )
                    try:
                        view = memoryview(replacement_payload)
                        while view:
                            written = real_write(fd, view)
                            if written <= 0:
                                raise OSError("staged mutation made no progress")
                            view = view[written:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                    mutated = True
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=mutate_staged):
                with self.assertRaisesRegex(OSError, "changed after activation"):
                    replace(target, payload, expected)

            self.assertTrue(mutated)

    def test_conditional_replace_removes_partial_staged_payload_after_failure(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_write = os.write
            first_write = True

            def write_then_fail(fd, data):
                nonlocal first_write
                if first_write:
                    first_write = False
                    return real_write(fd, data[:1])
                raise OSError("staged write failed")

            with mock.patch.object(path_safety.os, "write", side_effect=write_then_fail):
                with self.assertRaisesRegex(OSError, "staged write failed"):
                    replace(target, b"new staged payload", expected)

            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_stage_private_bytes_does_not_retry_ambiguous_close(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_open = path_safety.os.open
            real_close = path_safety.os.close
            staged_fd = None
            staged_identity = None
            staged_close_failed = False
            close_attempts = []

            def track_open(path, *args, **kwargs):
                nonlocal staged_fd, staged_identity
                fd = real_open(path, *args, **kwargs)
                if os.fsdecode(path) == "staged":
                    staged_fd = fd
                    staged_identity = os.fstat(fd)
                return fd

            def fail_staged_close(fd):
                nonlocal staged_close_failed
                if staged_fd is not None and fd == staged_fd:
                    staged_close_failed = True
                    close_attempts.append(fd)
                    raise OSError("staged close failed")
                return real_close(fd)

            try:
                with mock.patch.object(path_safety.os, "open", side_effect=track_open):
                    with mock.patch.object(path_safety.os, "close", side_effect=fail_staged_close):
                        with self.assertRaisesRegex(OSError, "secure cleanup failed"):
                            replace(target, b"new", expected)
            finally:
                if staged_fd is not None and staged_close_failed and staged_identity is not None:
                    try:
                        current_identity = os.fstat(staged_fd)
                    except OSError:
                        pass
                    else:
                        if (
                            current_identity.st_dev,
                            current_identity.st_ino,
                        ) == (staged_identity.st_dev, staged_identity.st_ino):
                            real_close(staged_fd)

            self.assertIsNotNone(staged_fd)
            self.assertEqual(close_attempts, [staged_fd])
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_reports_transaction_cleanup_failures_after_commit(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        close_factories = (
            ("oserror", lambda: OSError("/secret/rmdir"), OSError, "secure postcommit cleanup failed"),
            ("valueerror", lambda: ValueError("/secret/rmdir"), ValueError, "secure postcommit cleanup failed"),
            (
                "keyboardinterrupt",
                lambda: KeyboardInterrupt("/secret/rmdir"),
                KeyboardInterrupt,
                "secure postcommit cleanup interrupted",
            ),
            ("systemexit", lambda: SystemExit("/secret/rmdir"), SystemExit, "secure postcommit cleanup interrupted"),
            ("generatorexit", lambda: GeneratorExit("/secret/rmdir"), GeneratorExit, "secure postcommit cleanup interrupted"),
        )

        for primary_present in (False, True):
            for label, make_close_error, expected_type, expected_message in close_factories:
                with self.subTest(primary=primary_present, close=label), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    target = root / "target.bin"
                    target.write_bytes(b"old")
                    expected = self._capture_expected(expected_target, target)
                    real_rmdir = path_safety.os.rmdir
                    real_fsync = path_safety._fsync_fd
                    real_unlink_leaf = path_safety._unlink_verified_leaf
                    rmdir_calls = []
                    cleanup_completed = False

                    def fail_transaction_rmdir(name, *args, **kwargs):
                        if isinstance(name, str) and name.startswith(".target.bin.") and name.endswith(".txn"):
                            rmdir_calls.append(name)
                            raise make_close_error()
                        return real_rmdir(name, *args, **kwargs)

                    def mark_cleanup_complete(*args, **kwargs):
                        nonlocal cleanup_completed
                        result = real_unlink_leaf(*args, **kwargs)
                        cleanup_completed = True
                        return result

                    def fail_after_commit_fsync(fd):
                        if primary_present and cleanup_completed:
                            raise OSError("primary failure")
                        return real_fsync(fd)

                    try:
                        with (
                            mock.patch.object(path_safety.os, "rmdir", side_effect=fail_transaction_rmdir),
                            mock.patch.object(path_safety, "_unlink_verified_leaf", side_effect=mark_cleanup_complete),
                            mock.patch.object(path_safety, "_fsync_fd", side_effect=fail_after_commit_fsync),
                        ):
                            if primary_present:
                                with self.assertRaisesRegex(OSError, "primary failure") as caught:
                                    replace(target, b"new", expected)
                                self.assertEqual(type(caught.exception), OSError)
                                self.assertIn("secure path cleanup failed", "\n".join(getattr(caught.exception, "__notes__", ())))
                                self.assertNotIn("/secret", "\n".join(getattr(caught.exception, "__notes__", ())))
                            else:
                                with self.assertRaises(expected_type) as caught:
                                    replace(target, b"new", expected)
                                self.assertEqual(str(caught.exception), expected_message)
                                self.assertIsNone(caught.exception.__cause__)
                                self.assertIsNone(caught.exception.__context__)
                                self.assertNotIn("/secret", repr(caught.exception))
                                self.assertNotIn("/secret", "\n".join(getattr(caught.exception, "__notes__", ())))
                                if expected_type in (KeyboardInterrupt, SystemExit, GeneratorExit):
                                    self.assertIn(
                                        "mutation already committed; no rollback attempted",
                                        "\n".join(getattr(caught.exception, "__notes__", ())),
                                    )
                    finally:
                        self.assertEqual(rmdir_calls, [rmdir_calls[0]] if rmdir_calls else [])

                    self.assertEqual(target.read_bytes(), b"new")
                    self.assertEqual(len(rmdir_calls), 1)
                    self.assertEqual(len(list(root.glob(".target.bin.*.txn"))), 1)

    def test_conditional_verification_opens_use_close_on_exec(self) -> None:
        if not getattr(os, "O_CLOEXEC", 0):
            self.skipTest("O_CLOEXEC unavailable")
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)

            with mock.patch.object(path_safety.os, "open", wraps=os.open) as opened:
                replace(target, b"new", expected)

            dir_fd_flags = [
                call.args[1]
                for call in opened.call_args_list
                if call.kwargs.get("dir_fd") is not None and len(call.args) > 1
            ]
            self.assertTrue(dir_fd_flags)
            self.assertTrue(all(flags & os.O_CLOEXEC for flags in dir_fd_flags))

    def test_conditional_replace_surfaces_post_commit_cleanup_failure(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_unlink = path_safety.os.unlink

            def fail_claim_cleanup(name, *args, **kwargs):
                if name == "claimed" and kwargs.get("dir_fd") is not None:
                    raise OSError("quarantine cleanup denied")
                return real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=fail_claim_cleanup):
                with self.assertRaisesRegex(OSError, "quarantine cleanup denied") as caught:
                    replace(target, b"new", expected)

            self.assertTrue(any("cleanup pending" in note for note in caught.exception.__notes__))
            self.assertEqual(target.read_bytes(), b"new")
            transactions = list(root.glob(".target.bin.*.txn"))
            self.assertEqual(len(transactions), 1)
            self.assertEqual((transactions[0] / "claimed").read_bytes(), b"old")

    def test_conditional_replace_propagates_post_commit_cleanup_base_exception(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_unlink = path_safety.os.unlink

            def interrupt_claim_cleanup(name, *args, **kwargs):
                if name == "claimed" and kwargs.get("dir_fd") is not None:
                    raise KeyboardInterrupt("quarantine cleanup interrupted")
                return real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=interrupt_claim_cleanup):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    replace(target, b"new", expected)

            self.assertTrue(any("cleanup pending" in note for note in caught.exception.__notes__))
            self.assertEqual(target.read_bytes(), b"new")
            transactions = list(root.glob(".target.bin.*.txn"))
            self.assertEqual(len(transactions), 1)
            self.assertEqual((transactions[0] / "claimed").read_bytes(), b"old")

    def test_conditional_replace_rejects_in_place_version_change(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            target.write_bytes(b"changed")

            with self.assertRaisesRegex(OSError, "does not match expected target"):
                replace(target, b"new", expected)

            self.assertEqual(target.read_bytes(), b"changed")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_detects_same_size_mutation_with_restored_mtime(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"
            target.write_bytes(b"before")
            original_stat = os.stat(target)
            expected = self._capture_expected(expected_target, target)
            target.write_bytes(b"hidden")
            os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

            with self.assertRaisesRegex(OSError, "does not match expected target"):
                replace(target, b"new", expected)

            self.assertEqual(target.read_bytes(), b"hidden")

    def test_conditional_replace_can_ignore_version_for_same_inode(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target, require_same_version=False)
            target.write_bytes(b"changed")

            replace(target, b"new", expected)

            self.assertEqual(target.read_bytes(), b"new")

    def test_conditional_replace_preserves_replacement_inserted_before_claim(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            saved = root / "saved-old"
            replacement = root / "replacement"
            target.write_bytes(b"old")
            replacement.write_bytes(b"foreign")
            expected = self._capture_expected(expected_target, target)
            real_rename = path_safety._rename_without_replacing
            raced = False

            def replace_before_claim(source, destination, *args, **kwargs):
                nonlocal raced
                if not raced and source == target.name and kwargs.get("target_directory_fd") is not None:
                    target.replace(saved)
                    replacement.replace(target)
                    raced = True
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=replace_before_claim):
                with self.assertRaisesRegex(OSError, "does not match expected target"):
                    replace(target, b"new", expected)

            self.assertTrue(raced)
            self.assertEqual(target.read_bytes(), b"foreign")
            self.assertEqual(saved.read_bytes(), b"old")

    def test_conditional_replace_detects_version_change_after_claim(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_rename = path_safety._rename_without_replacing
            raced = False

            def mutate_after_claim(source, destination, *args, **kwargs):
                nonlocal raced
                result = real_rename(source, destination, *args, **kwargs)
                target_directory_fd = kwargs.get("target_directory_fd")
                if not raced and source == target.name and target_directory_fd is not None:
                    fd = os.open(destination, os.O_WRONLY | os.O_TRUNC, dir_fd=target_directory_fd)
                    try:
                        os.write(fd, b"changed")
                    finally:
                        os.close(fd)
                    raced = True
                return result

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=mutate_after_claim):
                with self.assertRaisesRegex(OSError, "does not match expected target"):
                    replace(target, b"new", expected)

            self.assertTrue(raced)
            self.assertFalse(target.exists())
            transactions = list(root.glob(".target.bin.*.txn"))
            self.assertEqual(len(transactions), 1)
            self.assertEqual((transactions[0] / "claimed").read_bytes(), b"changed")

    def test_repeated_verification_error_is_chain_free_at_helper_and_boundary(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            fd = os.open(target, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))

            def fail_verify(opened_fd, expected_value):
                raise OSError("/secret/transcript/path")

            try:
                with mock.patch.object(
                    path_safety,
                    "_verify_expected_target_strict",
                    side_effect=fail_verify,
                ):
                    with self.assertRaisesRegex(OSError, "verification failed") as direct:
                        path_safety._verify_expected_target_with_retry(
                            fd,
                            expected,
                            field_name="safe target",
                        )
                    with self.assertRaisesRegex(OSError, "verification failed") as boundary:
                        replace(target, b"new", expected)
                    missing_target = root / "missing.bin"
                    with self.assertRaisesRegex(OSError, "changed after activation") as missing:
                        replace(missing_target, b"new", expected_target.missing())
            finally:
                os.close(fd)

            for caught in (direct.exception, boundary.exception, missing.exception):
                self.assertIn(
                    str(caught),
                    {
                        "safe target verification failed",
                        "path source verification failed",
                        "path changed after activation",
                    },
                )
                self.assertIsNone(caught.__cause__)
                self.assertIsNone(caught.__context__)
                self.assertNotIn("/secret/transcript", repr(caught))
                self.assertNotIn("/secret/transcript", "\n".join(getattr(caught, "__notes__", ())))

    def test_missing_outer_verification_error_is_chain_free(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"

            def fail_retry(fd, expected_value, *, field_name):
                raise OSError("/secret/missing/verification")

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_with_retry",
                side_effect=fail_retry,
            ):
                with self.assertRaisesRegex(OSError, "changed after activation") as caught:
                    replace(target, b"new", expected_target.missing())

            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn("/secret/missing", repr(caught.exception))
            self.assertNotIn("/secret/missing", "\n".join(getattr(caught.exception, "__notes__", ())))
            self.assertEqual(target.read_bytes(), b"new")

    def test_rename_preserves_primary_when_source_fstat_and_close_fail(self) -> None:
        for primary_type in (ValueError, KeyboardInterrupt, SystemExit):
            for close_type in (ValueError, KeyboardInterrupt, SystemExit):
                with self.subTest(primary=primary_type.__name__, close=close_type.__name__), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    source = root / "source"
                    destination = root / "destination"
                    source.write_bytes(b"payload")
                    directory_fd = os.open(
                        root,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_CLOEXEC", 0),
                    )
                    real_open = path_safety.os.open
                    real_fstat = path_safety.os.fstat
                    real_close = path_safety.os.close
                    opened = []
                    leaked_fd = None
                    close_attempts = {}

                    def track_open(*args, **kwargs):
                        fd = real_open(*args, **kwargs)
                        opened.append(fd)
                        return fd

                    def fail_fstat(fd):
                        if fd in opened:
                            raise primary_type("primary failure")
                        return real_fstat(fd)

                    def fail_close(fd):
                        nonlocal leaked_fd
                        close_attempts[fd] = close_attempts.get(fd, 0) + 1
                        if fd in opened:
                            if close_attempts[fd] == 1:
                                leaked_fd = fd
                                raise close_type("close failure")
                            raise OSError(9, "Bad file descriptor")
                        return real_close(fd)

                    try:
                        with (
                            mock.patch.object(path_safety.os, "open", side_effect=track_open),
                            mock.patch.object(path_safety.os, "fstat", side_effect=fail_fstat),
                            mock.patch.object(path_safety.os, "close", side_effect=fail_close),
                        ):
                            with self.assertRaises(primary_type) as caught:
                                path_safety._rename_without_replacing(
                                    "source",
                                    "destination",
                                    directory_fd=directory_fd,
                                    target_directory_fd=directory_fd,
                                    expected_source_stat=source.stat(),
                                    field_name="rename",
                                )
                        self.assertEqual(str(caught.exception), "primary failure")
                        self.assertTrue(source.exists())
                        self.assertFalse(destination.exists())
                        self.assertEqual(close_attempts.get(leaked_fd), 1)
                    finally:
                        if leaked_fd is not None:
                            real_close(leaked_fd)
                        real_close(directory_fd)

    def test_rename_preserves_precommit_close_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(b"payload")
            directory_fd = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            real_open = path_safety.os.open
            real_close = path_safety.os.close
            opened = []
            leaked_fd = None

            def track_open(*args, **kwargs):
                fd = real_open(*args, **kwargs)
                opened.append(fd)
                return fd

            def fail_close(fd):
                nonlocal leaked_fd
                if fd in opened and leaked_fd is None:
                    leaked_fd = fd
                    raise KeyboardInterrupt("post-commit close failure")
                return real_close(fd)

            try:
                with (
                    mock.patch.object(path_safety.os, "open", side_effect=track_open),
                    mock.patch.object(path_safety.os, "close", side_effect=fail_close),
                ):
                    path_safety._rename_without_replacing(
                        "source",
                        "destination",
                        directory_fd=directory_fd,
                        target_directory_fd=directory_fd,
                        expected_source_stat=source.stat(),
                        field_name="rename",
                    )
                self.fail("expected precommit close failure")
            except KeyboardInterrupt as caught:
                self.assertEqual(str(caught), "secure cleanup interrupted")
                self.assertTrue(leaked_fd is not None)
                self.assertTrue(source.exists())
                self.assertFalse(destination.exists())
            finally:
                if leaked_fd is not None:
                    real_close(leaked_fd)
                real_close(directory_fd)

    def test_rename_expected_source_fd_close_attempt_is_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(b"payload")
            directory_fd = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            real_open = path_safety.os.open
            real_fstat = path_safety.os.fstat
            real_close = path_safety.os.close
            opened = []
            leaked_fd = None
            close_attempts = {}

            def track_open(*args, **kwargs):
                fd = real_open(*args, **kwargs)
                opened.append(fd)
                return fd

            def fail_fstat(fd):
                if fd in opened:
                    raise ValueError("primary failure")
                return real_fstat(fd)

            def fail_close(fd):
                nonlocal leaked_fd
                close_attempts[fd] = close_attempts.get(fd, 0) + 1
                if fd in opened:
                    if close_attempts[fd] == 1:
                        leaked_fd = fd
                        raise KeyboardInterrupt("close failure")
                    raise OSError(9, "Bad file descriptor")
                return real_close(fd)

            try:
                with (
                    mock.patch.object(path_safety.os, "open", side_effect=track_open),
                    mock.patch.object(path_safety.os, "fstat", side_effect=fail_fstat),
                    mock.patch.object(path_safety.os, "close", side_effect=fail_close),
                ):
                    with self.assertRaises(ValueError) as caught:
                        path_safety._rename_without_replacing(
                            "source",
                            "destination",
                            directory_fd=directory_fd,
                            target_directory_fd=directory_fd,
                            expected_source_fd=source_fd,
                            expected_source_stat=source.stat(),
                            field_name="rename",
                        )
                self.assertEqual(str(caught.exception), "primary failure")
                self.assertEqual(close_attempts.get(leaked_fd), 1)
                self.assertTrue(source.exists())
                self.assertFalse(destination.exists())
            finally:
                if leaked_fd is not None:
                    real_close(leaked_fd)
                real_close(source_fd)
                real_close(directory_fd)

    def test_conditional_replace_retries_transient_source_verification(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_verify = path_safety._verify_expected_target_strict
            attempts = 0

            def transient_verify(fd, expected_value):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("/secret/source transient failure")
                return real_verify(fd, expected_value)

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_strict",
                side_effect=transient_verify,
            ):
                replace(target, b"new", expected)

            self.assertEqual(attempts, 4)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_does_not_classify_unrelated_cleanup_suffix_error(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)

            def unrelated_error(*args, **kwargs):
                raise OSError("/secret/independent changed before cleanup")

            with mock.patch.object(
                path_safety,
                "_rename_without_replacing",
                side_effect=unrelated_error,
            ):
                with self.assertRaisesRegex(OSError, "verification failed") as caught:
                    replace(target, b"new", expected)

            self.assertNotIn("does not match expected target", str(caught.exception))
            self.assertNotIn("/secret/independent", str(caught.exception))
            self.assertNotIn("/secret/independent", repr(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn("/secret/independent", "\n".join(getattr(caught.exception, "__notes__", ())))
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_open_file_rotation_close_failure_is_exact_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "target.txt"
            target.write_text("payload", encoding="utf-8")
            real_open = path_safety.os.open
            real_close = path_safety.os.close
            opened = []
            close_attempts = {}
            failed_fd = None

            def track_open(*args, **kwargs):
                fd = real_open(*args, **kwargs)
                opened.append(fd)
                return fd

            def fail_rotation_close(fd):
                nonlocal failed_fd
                close_attempts[fd] = close_attempts.get(fd, 0) + 1
                if failed_fd is None and fd in opened and stat.S_ISDIR(os.fstat(fd).st_mode):
                    failed_fd = fd
                    raise ValueError("rotation close failure")
                if fd == failed_fd and close_attempts[fd] > 1:
                    raise OSError(9, "Bad file descriptor")
                return real_close(fd)

            with (
                mock.patch.object(path_safety.os, "open", side_effect=track_open),
                mock.patch.object(path_safety.os, "close", side_effect=fail_rotation_close),
            ):
                with self.assertRaisesRegex(ValueError, "secure cleanup failed"):
                    path_safety.open_file_without_following_symlinks(target, os.O_RDONLY)

            self.assertIsNotNone(failed_fd)
            self.assertEqual(close_attempts[failed_fd], 1)

    def test_open_file_final_close_failure_closes_result_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "nested"
            nested.mkdir()
            target = nested / "target.txt"
            target.write_text("payload", encoding="utf-8")
            real_open = path_safety.os.open
            real_close = path_safety.os.close
            directory_flag = getattr(os, "O_DIRECTORY", 0)
            opened = []
            close_attempts = {}
            active_generation = {}
            generation = 0
            result_token = None
            failed_directory_token = None

            def track_open(*args, **kwargs):
                nonlocal generation, result_token
                fd = real_open(*args, **kwargs)
                generation += 1
                token = (fd, generation)
                active_generation[fd] = token
                opened.append(fd)
                if stat.S_ISREG(os.fstat(fd).st_mode):
                    result_token = token
                return fd

            def fail_final_close(fd):
                nonlocal failed_directory_token
                token = active_generation[fd]
                close_attempts[token] = close_attempts.get(token, 0) + 1
                if (
                    result_token is not None
                    and failed_directory_token is None
                    and token != result_token
                    and stat.S_ISDIR(os.fstat(fd).st_mode)
                ):
                    failed_directory_token = token
                    raise ValueError("final directory close failure")
                if token == failed_directory_token and close_attempts[token] > 1:
                    raise OSError(9, "Bad file descriptor")
                result = real_close(fd)
                active_generation.pop(fd, None)
                return result

            with (
                mock.patch.object(path_safety.os, "open", side_effect=track_open),
                mock.patch.object(path_safety.os, "close", side_effect=fail_final_close),
            ):
                with self.assertRaisesRegex(ValueError, "secure cleanup failed"):
                    path_safety.open_file_without_following_symlinks(target, os.O_RDONLY)

            self.assertIsNotNone(result_token)
            self.assertIsNotNone(failed_directory_token)
            self.assertEqual(close_attempts[result_token], 1)
            self.assertEqual(close_attempts[failed_directory_token], 1)

    def test_low_level_rename_failure_redacts_target_name_and_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            secret_target = "secret-token-target"
            source.write_bytes(b"payload")
            directory_fd = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )

            def fail_rename(*args):
                return -1

            try:
                with mock.patch.object(path_safety, "_resolve_renameat2", return_value=fail_rename):
                    with self.assertRaises(OSError) as caught:
                        path_safety._rename_without_replacing(
                            "source",
                            secret_target,
                            directory_fd=directory_fd,
                            target_directory_fd=directory_fd,
                            expected_source_stat=source.stat(),
                            field_name="rename",
                        )
                self.assertNotIn(secret_target, str(caught.exception))
                self.assertNotIn(secret_target, repr(caught.exception))
                self.assertNotIn(secret_target, repr(caught.exception.args))
                self.assertIsNone(caught.exception.__cause__)
                self.assertIsNone(caught.exception.__context__)
                self.assertNotIn(secret_target, "\n".join(getattr(caught.exception, "__notes__", ())))
            finally:
                os.close(directory_fd)

    def test_atomic_write_failure_redacts_target_name_and_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            secret_target = Path(tmp) / "secret-token-target"

            def fail_rename(*args):
                return -1

            with mock.patch.object(path_safety, "_resolve_renameat2", return_value=fail_rename):
                with self.assertRaises(OSError) as caught:
                    path_safety.write_text_atomically_without_following_symlinks(
                        secret_target,
                        "payload",
                    )
            for rendered in (str(caught.exception), repr(caught.exception), repr(caught.exception.args)):
                self.assertNotIn("secret-token-target", rendered)
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertNotIn("secret-token-target", "\n".join(getattr(caught.exception, "__notes__", ())))

    def test_close_helpers_have_fixed_primary_and_postcommit_semantics(self) -> None:
        close_factories = (
            ("oserror", lambda: OSError("/secret/close"), OSError),
            ("valueerror", lambda: ValueError("/secret/close"), ValueError),
            ("keyboardinterrupt", lambda: KeyboardInterrupt("/secret/close"), KeyboardInterrupt),
            ("systemexit", lambda: SystemExit("/secret/close"), SystemExit),
            ("generatorexit", lambda: GeneratorExit("/secret/close"), GeneratorExit),
        )

        class Closeable:
            def __init__(self, error):
                self.error = error

            def close(self):
                raise self.error

        def invoke(helper_kind, close_error, primary_error, committed):
            if helper_kind == "fd":
                with mock.patch.object(path_safety.os, "close", side_effect=close_error):
                    path_safety._close_fd_preserving_primary(
                        123,
                        primary_error=primary_error,
                        committed=committed,
                    )
            else:
                path_safety._close_handle_preserving_primary(
                    Closeable(close_error),
                    primary_error=primary_error,
                    committed=committed,
                )

        for helper_kind in ("fd", "handle"):
            for committed in (False, True):
                for primary_present in (False, True):
                    for label, make_error, expected_type in close_factories:
                        with self.subTest(
                            helper=helper_kind,
                            committed=committed,
                            primary=primary_present,
                            close=label,
                        ):
                            primary = OSError("/secret/primary") if primary_present else None
                            caught = None
                            try:
                                invoke(helper_kind, make_error(), primary, committed)
                            except BaseException as exc:
                                caught = exc

                            is_control_flow = expected_type in (KeyboardInterrupt, SystemExit, GeneratorExit)
                            if primary_present:
                                self.assertIsNone(caught)
                                self.assertEqual(str(primary), "/secret/primary")
                                primary_notes = "\n".join(getattr(primary, "__notes__", ()))
                                self.assertIn("secure path cleanup failed", primary_notes)
                                self.assertNotIn("/secret", primary_notes)
                                continue

                            if committed and not is_control_flow and not primary_present:
                                self.assertIsNone(caught)
                                continue

                            self.assertIsNotNone(caught)
                            self.assertIs(type(caught), expected_type)
                            expected_message = (
                                "secure postcommit cleanup interrupted"
                                if committed and is_control_flow
                                else "secure cleanup interrupted"
                                if is_control_flow
                                else "secure cleanup failed"
                            )
                            self.assertEqual(str(caught), expected_message)
                            self.assertIsNone(caught.__cause__)
                            self.assertIsNone(caught.__context__)
                            self.assertNotIn("/secret", repr(caught))
                            self.assertNotIn("/secret", "\n".join(getattr(caught, "__notes__", ())))
                            if committed and is_control_flow:
                                self.assertIn(
                                    "mutation already committed; no rollback attempted",
                                    "\n".join(getattr(caught, "__notes__", ())),
                                )

    def test_conditional_replace_repeated_source_verification_error_is_redacted(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)

            def fail_verify(fd, expected_value):
                raise OSError("/secret/source repeated failure")

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_strict",
                side_effect=fail_verify,
            ):
                with self.assertRaisesRegex(OSError, "verification failed") as caught:
                    replace(target, b"new", expected)

            self.assertNotIn("/secret/source", str(caught.exception))
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_retries_transient_missing_activation_verification(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            real_verify = path_safety._verify_expected_target_strict
            attempts = 0

            def transient_verify(fd, expected_value):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError("/secret/missing activation transient failure")
                return real_verify(fd, expected_value)

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_strict",
                side_effect=transient_verify,
            ):
                replace(target, b"new", expected_target.missing())

            self.assertEqual(attempts, 2)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_retries_transient_replacement_activation_verification(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_verify = path_safety._verify_expected_target_strict
            attempts = 0

            def transient_verify(fd, expected_value):
                nonlocal attempts
                attempts += 1
                if attempts == 3:
                    raise OSError("/secret/replacement activation transient failure")
                return real_verify(fd, expected_value)

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_strict",
                side_effect=transient_verify,
            ):
                replace(target, b"new", expected)

            self.assertEqual(attempts, 4)
            self.assertEqual(target.read_bytes(), b"new")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_preserves_primary_when_source_close_fails(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        for close_error in (ValueError("close failed"), KeyboardInterrupt(), SystemExit()):
            with self.subTest(close_error=type(close_error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "target.bin"
                target.write_bytes(b"old")
                expected = self._capture_expected(expected_target, target)
                target.write_bytes(b"changed")
                real_close = path_safety.os.close
                injected = False

                def fail_source_close(fd):
                    nonlocal injected
                    try:
                        current = os.fstat(fd)
                    except OSError:
                        return real_close(fd)
                    if not injected and current.st_ino == expected.snapshot.inode:
                        injected = True
                        raise close_error
                    return real_close(fd)

                with mock.patch.object(path_safety.os, "close", side_effect=fail_source_close):
                    with self.assertRaisesRegex(OSError, "does not match expected target") as caught:
                        replace(target, b"new", expected)

                self.assertTrue(injected)
                self.assertNotIn("close failed", str(caught.exception))
                self.assertEqual(target.read_bytes(), b"changed")

    def test_conditional_replace_preserves_success_when_committed_fd_close_fails(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        for close_error in (
            ValueError("post-commit close failed"),
            KeyboardInterrupt(),
            SystemExit(),
            GeneratorExit(),
        ):
            with self.subTest(close_error=type(close_error).__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "target.bin"
                target.write_bytes(b"old")
                expected = self._capture_expected(expected_target, target)
                real_close = path_safety.os.close
                leaked_fd = None

                def fail_committed_close(fd):
                    nonlocal leaked_fd
                    try:
                        current = os.fstat(fd)
                    except OSError:
                        return real_close(fd)
                    if leaked_fd is None and stat.S_ISREG(current.st_mode) and current.st_nlink == 0:
                        leaked_fd = fd
                        raise close_error
                    return real_close(fd)

                with mock.patch.object(path_safety.os, "close", side_effect=fail_committed_close):
                    if isinstance(close_error, (KeyboardInterrupt, SystemExit, GeneratorExit)):
                        with self.assertRaises(type(close_error)) as caught:
                            replace(target, b"new", expected)
                        self.assertEqual(
                            str(caught.exception),
                            "secure postcommit cleanup interrupted",
                        )
                        self.assertIsNone(caught.exception.__cause__)
                        self.assertIsNone(caught.exception.__context__)
                        self.assertIn(
                            "mutation already committed; no rollback attempted",
                            "\n".join(getattr(caught.exception, "__notes__", ())),
                        )
                    else:
                        replace(target, b"new", expected)

                if leaked_fd is not None:
                    real_close(leaked_fd)
                self.assertEqual(target.read_bytes(), b"new")
                self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_retries_transient_postclaim_verification_and_restores(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_verify = path_safety._verify_expected_target_strict
            real_rename = path_safety._rename_without_replacing
            verification_failures = 0
            verification_attempts = 0

            def transient_verify(fd, expected_value):
                nonlocal verification_attempts, verification_failures
                if expected_value is expected:
                    verification_attempts += 1
                if expected_value is expected and verification_attempts == 2:
                    verification_failures += 1
                    raise OSError("transient verifier failure")
                return real_verify(fd, expected_value)

            def fail_activation(source, destination, *args, **kwargs):
                if source == "staged" and destination == target.name:
                    raise OSError("activation failed")
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_verify_expected_target_strict", side_effect=transient_verify):
                with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=fail_activation):
                    with self.assertRaisesRegex(OSError, "activation failed"):
                        replace(target, b"new", expected)

            self.assertEqual(verification_failures, 1)
            self.assertEqual(target.read_bytes(), b"old")
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_replace_missing_rejects_post_activation_unsafe_targets(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        for race_kind in ("foreign", "symlink", "hardlink"):
            with self.subTest(race_kind=race_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                target = root / "target.bin"
                foreign = root / "foreign.bin"
                foreign.write_bytes(b"foreign")
                real_rename = path_safety._rename_without_replacing

                def replace_after_activation(source, destination, *args, **kwargs):
                    result = real_rename(source, destination, *args, **kwargs)
                    if source == "staged" and destination == target.name:
                        target.unlink()
                        if race_kind == "foreign":
                            foreign.replace(target)
                        elif race_kind == "symlink":
                            target.symlink_to(foreign)
                        else:
                            os.link(foreign, target)
                    return result

                with mock.patch.object(
                    path_safety,
                    "_rename_without_replacing",
                    side_effect=replace_after_activation,
                ):
                    with self.assertRaisesRegex(OSError, "changed after activation"):
                        replace(target, b"new", expected_target.missing())

                if race_kind == "symlink":
                    self.assertTrue(target.is_symlink())
                else:
                    self.assertEqual(target.read_bytes(), b"foreign")
                if race_kind == "hardlink":
                    self.assertGreaterEqual(os.stat(target).st_nlink, 2)

    def test_conditional_replace_missing_surfaces_post_activation_verification_error(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"

            def fail_verification(fd, expected_value):
                raise OSError("post-activation verification failed")

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_strict",
                side_effect=fail_verification,
            ):
                with self.assertRaisesRegex(OSError, "changed after activation"):
                    replace(target, b"new", expected_target.missing())

            self.assertEqual(target.read_bytes(), b"new")

    def test_conditional_replace_never_restores_foreign_claim(self) -> None:
        if not Path("/proc/self/fd").is_dir():
            self.skipTest("procfs fd paths unavailable")
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_verify = path_safety._verify_expected_target_strict
            calls = 0

            def replace_claim(fd, expected_value):
                nonlocal calls
                calls += 1
                if calls == 2:
                    claim_path = Path(os.readlink(f"/proc/self/fd/{fd}"))
                    claim_path.unlink()
                    claim_path.write_bytes(b"foreign")
                    return False
                return real_verify(fd, expected_value)

            with mock.patch.object(
                path_safety,
                "_verify_expected_target_strict",
                side_effect=replace_claim,
            ):
                with self.assertRaisesRegex(OSError, "does not match expected target"):
                    replace(target, b"new", expected)

            self.assertFalse(target.exists())
            transactions = list(root.glob(".target.bin.*.txn"))
            self.assertEqual(len(transactions), 1)
            self.assertEqual((transactions[0] / "claimed").read_bytes(), b"foreign")

    def test_conditional_replace_preserves_concurrent_target_and_recovery(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            racer = root / "racer"
            target.write_bytes(b"old")
            racer.write_bytes(b"foreign")
            expected = self._capture_expected(expected_target, target)
            real_rename = path_safety._rename_without_replacing
            raced = False

            def race_activation(source, destination, *args, **kwargs):
                nonlocal raced
                if source == "staged" and destination == target.name:
                    racer.replace(target)
                    raced = True
                return real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=race_activation):
                with self.assertRaises(OSError):
                    replace(target, b"new", expected)

            self.assertTrue(raced)
            self.assertEqual(target.read_bytes(), b"foreign")
            transactions = list(root.glob(".target.bin.*.txn"))
            self.assertEqual(len(transactions), 1)
            self.assertEqual((transactions[0] / "claimed").read_bytes(), b"old")

    def test_conditional_unlink_fails_closed_on_claim_hardlink_race(self) -> None:
        expected_target, _replace, unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            real_unlink = path_safety.os.unlink
            injected = False

            def add_hardlink_before_unlink(name, *args, **kwargs):
                nonlocal injected
                if not injected and name == "claimed" and kwargs.get("dir_fd") is not None:
                    os.link(
                        name,
                        "attacker-hardlink",
                        src_dir_fd=kwargs["dir_fd"],
                        dst_dir_fd=kwargs["dir_fd"],
                        follow_symlinks=False,
                    )
                    injected = True
                return real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=add_hardlink_before_unlink):
                with self.assertRaisesRegex(OSError, "link count"):
                    unlink(target, expected)

            self.assertTrue(injected)

    def test_conditional_replace_preserves_claim_on_restore_collision(self) -> None:
        expected_target, replace, _unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            racer = root / "racer"
            target.write_bytes(b"old")
            racer.write_bytes(b"foreign")
            expected = self._capture_expected(expected_target, target)
            real_rename = path_safety._rename_without_replacing
            raced = False

            def mutate_claim_and_fill_target(source, destination, *args, **kwargs):
                nonlocal raced
                result = real_rename(source, destination, *args, **kwargs)
                target_directory_fd = kwargs.get("target_directory_fd")
                if source == target.name and target_directory_fd is not None:
                    fd = os.open(destination, os.O_WRONLY | os.O_TRUNC, dir_fd=target_directory_fd)
                    try:
                        os.write(fd, b"changed")
                    finally:
                        os.close(fd)
                    racer.replace(target)
                    raced = True
                return result

            with mock.patch.object(
                path_safety,
                "_rename_without_replacing",
                side_effect=mutate_claim_and_fill_target,
            ):
                with self.assertRaisesRegex(OSError, "does not match expected target"):
                    replace(target, b"new", expected)

            self.assertTrue(raced)
            self.assertEqual(target.read_bytes(), b"foreign")
            transactions = list(root.glob(".target.bin.*.txn"))
            self.assertEqual(len(transactions), 1)
            self.assertEqual((transactions[0] / "claimed").read_bytes(), b"changed")

    def test_conditional_unlink_removes_only_captured_target(self) -> None:
        expected_target, _replace, unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)

            self.assertTrue(unlink(target, expected))

            self.assertFalse(target.exists())
            self.assertFalse(list(root.glob(".target.bin.*.txn")))

    def test_conditional_unlink_missing_is_explicit(self) -> None:
        expected_target, _replace, unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.bin"
            self.assertFalse(unlink(target, expected_target.missing()))

            target.write_bytes(b"foreign")
            with self.assertRaisesRegex(OSError, "expected target to be missing"):
                unlink(target, expected_target.missing())
            self.assertEqual(target.read_bytes(), b"foreign")

    def test_conditional_unlink_preserves_source_racer_after_claim_verification(self) -> None:
        expected_target, _replace, unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            racer = root / "racer"
            target.write_bytes(b"old")
            racer.write_bytes(b"foreign")
            expected = self._capture_expected(expected_target, target)
            real_unlink = path_safety.os.unlink
            raced = False

            def create_source_racer_before_quarantine_unlink(name, *args, **kwargs):
                nonlocal raced
                if not raced and name == "claimed" and kwargs.get("dir_fd") is not None:
                    racer.replace(target)
                    raced = True
                return real_unlink(name, *args, **kwargs)

            with mock.patch.object(path_safety.os, "unlink", side_effect=create_source_racer_before_quarantine_unlink):
                self.assertTrue(unlink(target, expected))

            self.assertTrue(raced)
            self.assertEqual(target.read_bytes(), b"foreign")

    def test_conditional_apis_fail_before_target_change_without_renameat2(self) -> None:
        expected_target, replace, unlink = self._conditional_symbols()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.bin"
            target.write_bytes(b"old")
            expected = self._capture_expected(expected_target, target)
            for operation in (
                lambda: replace(target, b"new", expected),
                lambda: unlink(target, expected),
            ):
                with self.subTest(operation=operation):
                    with (
                        mock.patch.object(path_safety.ctypes, "CDLL", side_effect=OSError("unavailable")),
                        self.assertRaisesRegex(OSError, "no-clobber"),
                    ):
                        operation()
                    self.assertEqual(target.read_bytes(), b"old")
                    self.assertFalse(list(root.glob(".target.bin.*.txn")))

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
        self.assertNotIn("close failure", "\n".join(caught.exception.__notes__))

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

        self.assertIn("secure path cleanup failed", "\n".join(caught.exception.__notes__))
        self.assertNotIn("temporary handle close failed", "\n".join(caught.exception.__notes__))

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
                    with self.assertRaisesRegex(OSError, "secure cleanup failed"):
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
                    with self.assertRaisesRegex(KeyboardInterrupt, "secure postcommit cleanup interrupted") as caught:
                        path_safety.write_text_atomically_without_following_symlinks(target, "new")
            finally:
                for fd in parent_fds:
                    real_close(fd)

            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)
            self.assertIn(
                "mutation already committed; no rollback attempted",
                "\n".join(getattr(caught.exception, "__notes__", ())),
            )
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

    def test_atomic_quarantine_source_fd_closes_when_fstat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_open = path_safety.os.open
            real_fstat = path_safety.os.fstat
            real_close = path_safety.os.close
            source_fd = None
            source_closed = False
            close_calls = []

            def track_open(path, *args, **kwargs):
                nonlocal source_fd
                fd = real_open(path, *args, **kwargs)
                if source_fd is None and path == target.name and kwargs.get("dir_fd") is not None:
                    source_fd = fd
                return fd

            def fail_source_fstat(fd):
                if source_fd is not None and fd == source_fd:
                    raise OSError("source fstat failed")
                return real_fstat(fd)

            def count_source_close(fd):
                nonlocal source_closed
                if source_fd is not None and fd == source_fd and not source_closed:
                    close_calls.append(fd)
                    result = real_close(fd)
                    source_closed = True
                    return result
                return real_close(fd)

            with mock.patch.object(path_safety.os, "open", side_effect=track_open):
                with mock.patch.object(path_safety.os, "fstat", side_effect=fail_source_fstat):
                    with mock.patch.object(path_safety.os, "close", side_effect=count_source_close):
                        with self.assertRaisesRegex(OSError, "source fstat failed"):
                            path_safety.write_text_atomically_without_following_symlinks(
                                target,
                                "new",
                                field_name="settings path",
                            )

            self.assertIsNotNone(source_fd)
            self.assertEqual(close_calls, [source_fd])

    def test_atomic_quarantine_source_fd_close_error_is_not_retried_after_keyboard_interrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "settings.json"
            target.write_text("old", encoding="utf-8")
            real_open = path_safety.os.open
            real_close = path_safety.os.close
            real_rename = path_safety._rename_without_replacing
            source_fd = None
            close_calls = []
            interrupted = False

            def track_open(path, *args, **kwargs):
                nonlocal source_fd
                fd = real_open(path, *args, **kwargs)
                if source_fd is None and path == target.name and kwargs.get("dir_fd") is not None:
                    source_fd = fd
                return fd

            def interrupt_quarantine(source, destination, *args, **kwargs):
                nonlocal interrupted
                if (
                    not interrupted
                    and source == target.name
                    and isinstance(destination, str)
                    and destination.endswith(".cleanup")
                ):
                    interrupted = True
                    raise KeyboardInterrupt("quarantine rename interrupted")
                return real_rename(source, destination, *args, **kwargs)

            def fail_source_close(fd):
                if source_fd is not None and fd == source_fd:
                    close_calls.append(fd)
                    raise OSError("source close failed")
                return real_close(fd)

            with mock.patch.object(path_safety.os, "open", side_effect=track_open):
                with mock.patch.object(path_safety, "_rename_without_replacing", side_effect=interrupt_quarantine):
                    with mock.patch.object(path_safety.os, "close", side_effect=fail_source_close):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            path_safety.write_text_atomically_without_following_symlinks(
                                target,
                                "new",
                                field_name="settings path",
                            )

            self.assertTrue(interrupted)
            self.assertIsNotNone(source_fd)
            self.assertEqual(close_calls, [source_fd])
            notes = getattr(caught.exception, "__notes__", ())
            self.assertIn("secure path cleanup failed", "\n".join(notes))
            self.assertNotIn("source close failed", "\n".join(notes))


if __name__ == "__main__":
    unittest.main()
