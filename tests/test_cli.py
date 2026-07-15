# mypy: ignore-errors
from __future__ import annotations

import argparse
import errno
import io
import json
import os
import subprocess
import time
import tomllib
import tempfile
import unittest
import wave
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import artifact_crypto, cli
from speed_of_cinnamon.alarms import (
    MAX_ALARM_NAME_CHARS,
    MAX_ALARM_ID_CHARS,
    add_alarm,
    list_alarm_payload,
    save_alarm_store,
)
from speed_of_cinnamon.recorder import InputSource, RecorderCommand
from speed_of_cinnamon.state import RecordingState, StateStore


class CliTest(unittest.TestCase):
    def test_print_result_rejects_nonfinite_json_values(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), redirect_stdout(io.StringIO()):
                with self.assertRaises(ValueError):
                    cli.print_result({"status": "done", "value": value}, True)

    def test_run_returns_valid_json_when_payload_contains_nonfinite_value(self) -> None:
        parser = argparse.ArgumentParser()
        parser.parse_args = mock.Mock(
            return_value=argparse.Namespace(
                command="test",
                json=True,
                log_level="INFO",
                handler=lambda _args: {"status": "done", "value": float("inf")},
            )
        )
        stdout = io.StringIO()
        with (
            mock.patch.object(cli, "build_parser", return_value=parser),
            mock.patch.object(cli, "configure_logging"),
            mock.patch.object(cli, "log_event"),
            redirect_stdout(stdout),
        ):
            code = cli.run([])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertNotIn("Infinity", stdout.getvalue())

    def test_temporary_benchmark_path_preserves_result_on_fd_close_failure(self) -> None:
        file_stat = os.stat(__file__)
        with (
            mock.patch.object(cli.tempfile, "mkstemp", return_value=(42, "/tmp/.benchmark-test.tmp.txt")),
            mock.patch.object(cli.os, "fstat", return_value=file_stat),
            mock.patch.object(cli.os, "close", side_effect=OSError("close failed")),
        ):
            result = cli._temporary_benchmark_transcript_path()

        self.assertEqual(result, (Path("/tmp/.benchmark-test.tmp.txt"), file_stat))

    def test_write_json_atomic_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            cli._write_json_atomic(path, {"status": "ok"}, max_bytes=1_000_000)
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_write_text_atomic_sets_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            cli._write_text_atomic(path, "private")
            mode = path.stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_security_post_processing_fails_closed_on_unreadable_blacklist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_bytes(b"\xff")

            with mock.patch("speed_of_cinnamon.cli.blacklist_file", return_value=path):
                with self.assertRaises(ValueError):
                    cli._apply_security_post_processing("sichtbarer text")

    @mock.patch(
        "speed_of_cinnamon.cli.write_text_atomically_without_following_symlinks",
        side_effect=OSError("out of space"),
    )
    def test_write_json_atomic_reports_writer_failure(
        self,
        mocked_write: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "failed to write JSON output"):
            cli._write_json_atomic(Path("/tmp/security.json"), {"status": "ok"}, max_bytes=10_000)
        mocked_write.assert_called_once()

    @mock.patch(
        "speed_of_cinnamon.cli.write_text_atomically_without_following_symlinks",
        side_effect=OSError("out of space"),
    )
    def test_write_text_atomic_reports_writer_failure(
        self,
        mocked_write: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(RuntimeError, "failed to write transcript file"):
            cli._write_text_atomic(Path("/tmp/security.txt"), "private")
        mocked_write.assert_called_once()

    def test_write_text_atomic_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "failed to write transcript file"):
                cli._write_text_atomic(link / "security.txt", "private")

            self.assertFalse((real / "security.txt").exists())

    def test_prepare_private_file_rejects_existing_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            path.write_bytes(b"old")

            with self.assertRaisesRegex(RuntimeError, "failed to prepare recording audio file"):
                cli._prepare_private_file(path, field_name="recording audio file")

            self.assertEqual(path.read_bytes(), b"old")

    def test_prepare_private_file_rejects_symlinked_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real-recordings"
            real.mkdir()
            link = root / "recordings"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "failed to prepare recording audio file"):
                cli._prepare_private_file(link / "recording.wav", field_name="recording audio file")

            self.assertEqual(list(real.iterdir()), [])
            self.assertTrue(link.is_symlink())

    def test_prepare_private_file_closes_descriptor_when_fdopen_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            real_open = os.open
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path.name:
                    target_fds.append(fd)
                return fd

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
            ):
                with self.assertRaisesRegex(cli._PrivateFilePrepareError, "failed to prepare recording audio file") as caught:
                    cli._prepare_private_file(path, field_name="recording audio file")

            self.assertTrue(caught.exception.created)
            self.assertIsNone(caught.exception.errno)
            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_prepare_private_file_closes_descriptor_when_fdopen_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            real_open = os.open
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path.name:
                    target_fds.append(fd)
                return fd

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli._prepare_private_file(path, field_name="recording audio file")

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_prepare_private_file_preserves_fdopen_error_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            real_open = os.open
            real_close = os.close
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path.name:
                    target_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                real_close(fd)
                if fd in target_fds:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
                mock.patch.object(cli.os, "close", side_effect=close_wrapper),
            ):
                with self.assertRaisesRegex(cli._PrivateFilePrepareError, "failed to prepare recording audio file"):
                    cli._prepare_private_file(path, field_name="recording audio file")

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_prepare_private_file_preserves_interrupt_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            real_open = os.open
            real_close = os.close
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path.name:
                    target_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                real_close(fd)
                if fd in target_fds:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=KeyboardInterrupt),
                mock.patch.object(cli.os, "close", side_effect=close_wrapper),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli._prepare_private_file(path, field_name="recording audio file")

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_prepare_transient_transcript_closes_descriptor_when_owner_write_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            with (
                mock.patch.object(cli, "transcript_dir", return_value=root),
                mock.patch.object(cli, "_prepare_private_file"),
                mock.patch.object(cli.os, "open", return_value=42),
                mock.patch.object(cli.os, "fstat", return_value=os.stat(__file__)),
                mock.patch.object(cli, "_write_transient_transcript_owner", side_effect=KeyboardInterrupt),
                mock.patch.object(cli, "_remove_transient_transcript_path") as mocked_remove,
                mock.patch.object(cli.os, "close") as mocked_close,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli._prepare_transient_transcript_path(path, storage_path)

            mocked_close.assert_called_once_with(42)
            mocked_remove.assert_called_once_with(path, storage_path)

    def test_prepare_transient_transcript_cleans_path_when_owner_write_and_fd_close_are_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            with (
                mock.patch.object(cli, "transcript_dir", return_value=root),
                mock.patch.object(cli, "_prepare_private_file"),
                mock.patch.object(cli.os, "open", return_value=42),
                mock.patch.object(cli.os, "fstat", return_value=os.stat(__file__)),
                mock.patch.object(cli, "_write_transient_transcript_owner", side_effect=KeyboardInterrupt),
                mock.patch.object(cli, "_remove_transient_transcript_path") as mocked_remove,
                mock.patch.object(cli.os, "close", side_effect=KeyboardInterrupt) as mocked_close,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli._prepare_transient_transcript_path(path, storage_path)

            mocked_close.assert_called_once_with(42)
            mocked_remove.assert_called_once_with(path, storage_path)

    def test_prepare_private_file_preserves_success_when_parent_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == parent_fd:
                    raise OSError("parent close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    cli._prepare_private_file(path, field_name="recording audio file")
            finally:
                real_close(parent_fd)

            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_prepare_private_file_preserves_success_when_parent_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            parent_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == parent_fd:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    cli._prepare_private_file(path, field_name="recording audio file")
            finally:
                real_close(parent_fd)

            self.assertTrue(path.exists())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_prepare_transient_transcript_preserves_owner_error_when_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            file_stat = os.stat(__file__)
            with (
                mock.patch.object(cli, "transcript_dir", return_value=root),
                mock.patch.object(cli, "_prepare_private_file"),
                mock.patch.object(cli.os, "open", return_value=42),
                mock.patch.object(cli.os, "fstat", return_value=file_stat),
                mock.patch.object(cli, "_write_transient_transcript_owner", side_effect=RuntimeError("owner failed")),
                mock.patch.object(cli.os, "close", side_effect=OSError("close failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "owner failed"):
                    cli._prepare_transient_transcript_path(path, storage_path)

    def test_prepare_transient_transcript_removes_file_after_private_prepare_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            with (
                mock.patch.object(cli, "transcript_dir", return_value=root),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
            ):
                with self.assertRaisesRegex(cli._PrivateFilePrepareError, "failed to prepare transient transcript file"):
                    cli._prepare_transient_transcript_path(path, storage_path)

            self.assertFalse(path.exists())

    def test_prepare_transient_transcript_removes_file_after_owner_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            with (
                mock.patch.object(cli, "transcript_dir", return_value=root),
                mock.patch.object(cli, "_write_transient_transcript_owner", side_effect=RuntimeError("owner failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "owner failed"):
                    cli._prepare_transient_transcript_path(path, storage_path)

            self.assertFalse(path.exists())

    def test_remove_transient_transcript_preserves_success_when_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            path.write_text("temporary transcript\n", encoding="utf-8")
            expected_fd = os.open(path, os.O_RDONLY)
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == expected_fd:
                    raise OSError("close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "transcript_dir", return_value=root),
                    mock.patch.object(cli, "_remove_transient_transcript_owner", return_value=True),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    self.assertTrue(cli._remove_transient_transcript_path(path, storage_path, expected_fd=expected_fd))
            finally:
                real_close(expected_fd)

            self.assertFalse(path.exists())

    def test_remove_transient_transcript_preserves_success_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            path.write_text("temporary transcript\n", encoding="utf-8")
            expected_fd = os.open(path, os.O_RDONLY)
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == expected_fd:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "transcript_dir", return_value=root),
                    mock.patch.object(cli, "_remove_transient_transcript_owner", return_value=True),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    self.assertTrue(cli._remove_transient_transcript_path(path, storage_path, expected_fd=expected_fd))
            finally:
                real_close(expected_fd)

            self.assertFalse(path.exists())

    def test_remove_transient_transcript_preserves_success_when_parent_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / ".transcript.tmp.txt"
            storage_path = root / "transcript.txt"
            path.write_text("temporary transcript\n", encoding="utf-8")
            parent_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == parent_fd:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "transcript_dir", return_value=root),
                    mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(cli, "_remove_transient_transcript_owner", return_value=True) as mocked_owner,
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    self.assertTrue(cli._remove_transient_transcript_path(path, storage_path))
            finally:
                real_close(parent_fd)

            mocked_owner.assert_called_once_with(path)
            self.assertFalse(path.exists())

    def test_ensure_transcript_export_dir_preserves_success_when_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_path = root / "exports" / "all-transcripts.txt"
            directory_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            try:
                with (
                    mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=directory_fd),
                    mock.patch.object(cli.os, "fchmod"),
                    mock.patch.object(cli.os, "close", side_effect=OSError("close failed")),
                ):
                    cli._ensure_transcript_export_dir(export_path)
            finally:
                real_close(directory_fd)

    def test_ensure_transcript_export_dir_preserves_success_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export_path = root / "exports" / "all-transcripts.txt"
            directory_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == directory_fd:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=directory_fd),
                    mock.patch.object(cli.os, "fchmod"),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    cli._ensure_transcript_export_dir(export_path)
            finally:
                real_close(directory_fd)

    def test_safe_directory_entries_preserves_scan_when_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "entry.txt").write_text("entry\n", encoding="utf-8")
            directory_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == directory_fd:
                    raise OSError("close failed")
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "open_directory_without_following_symlinks", return_value=directory_fd),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    entries = cli._safe_directory_entries(root, field_name="test directory")
            finally:
                real_close(directory_fd)

            self.assertEqual([path.name for path, _file_stat in entries], ["entry.txt"])

    def test_safe_directory_entries_preserves_scan_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "entry.txt").write_text("entry\n", encoding="utf-8")
            directory_fd = os.open(tmp, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close_wrapper(fd: int) -> None:
                if fd == directory_fd:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.object(cli, "open_directory_without_following_symlinks", return_value=directory_fd),
                    mock.patch.object(cli.os, "close", side_effect=close_wrapper),
                ):
                    entries = cli._safe_directory_entries(root, field_name="test directory")
            finally:
                real_close(directory_fd)

            self.assertEqual([path.name for path, _file_stat in entries], ["entry.txt"])

    def test_finalization_lock_pid_closes_descriptor_when_fdopen_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".state.finalizing"
            path.write_text("123\n", encoding="ascii")
            path.chmod(0o600)
            real_open = os.open
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == str(path):
                    target_fds.append(fd)
                return fd

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
            ):
                self.assertIsNone(cli._read_finalization_lock_pid(path))

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_finalization_lock_pid_preserves_none_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".state.finalizing"
            path.write_text("123\n", encoding="ascii")
            path.chmod(0o600)
            real_open = os.open
            real_close = os.close
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == str(path):
                    target_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                real_close(fd)
                if fd in target_fds:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
                mock.patch.object(cli.os, "close", side_effect=close_wrapper),
            ):
                self.assertIsNone(cli._read_finalization_lock_pid(path))

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_finalization_lock_pid_preserves_interrupt_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".state.finalizing"
            path.write_text("123\n", encoding="ascii")
            path.chmod(0o600)
            real_open = os.open
            real_close = os.close
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == str(path):
                    target_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                real_close(fd)
                if fd in target_fds:
                    raise KeyboardInterrupt("close interrupted")

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=KeyboardInterrupt("fdopen interrupted")),
                mock.patch.object(cli.os, "close", side_effect=close_wrapper),
            ):
                with self.assertRaisesRegex(KeyboardInterrupt, "fdopen interrupted"):
                    cli._read_finalization_lock_pid(path)

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_finalization_lock_pid_closes_descriptor_when_fdopen_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".state.finalizing"
            path.write_text("123\n", encoding="ascii")
            path.chmod(0o600)
            real_open = os.open
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == str(path):
                    target_fds.append(fd)
                return fd

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli._read_finalization_lock_pid(path)

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_finalization_lock_closes_descriptor_when_creation_stat_is_interrupted(self) -> None:
        state_file = Path("/probe/state.json")
        with (
            mock.patch.object(cli, "assert_no_symlink_ancestors"),
            mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(cli.os, "open", return_value=123),
            mock.patch.object(cli.os, "fstat", side_effect=KeyboardInterrupt),
            mock.patch.object(cli, "_unlink_finalization_lock_at") as mocked_unlink,
            mock.patch.object(cli.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli._acquire_finalization_lock(state_file)

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)
        mocked_unlink.assert_called_once()

    def test_finalization_lock_cleans_up_when_write_is_interrupted(self) -> None:
        state_file = Path("/probe/state.json")
        with (
            mock.patch.object(cli, "assert_no_symlink_ancestors"),
            mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(cli.os, "open", return_value=123),
            mock.patch.object(cli.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(cli, "_finalization_lock_identity_for_pid", return_value=None),
            mock.patch.object(cli, "_write_all", side_effect=KeyboardInterrupt),
            mock.patch.object(cli, "_unlink_finalization_lock_at") as mocked_unlink,
            mock.patch.object(cli.os, "close") as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli._acquire_finalization_lock(state_file)

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)
        mocked_unlink.assert_called_once()

    def test_finalization_lock_cleans_up_when_lock_close_is_interrupted(self) -> None:
        state_file = Path("/probe/state.json")

        def close_fd(fd: int) -> None:
            if fd == 123:
                raise KeyboardInterrupt

        with (
            mock.patch.object(cli, "assert_no_symlink_ancestors"),
            mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(cli.os, "open", return_value=123),
            mock.patch.object(cli.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(cli, "_finalization_lock_identity_for_pid", return_value=None),
            mock.patch.object(cli, "_write_all"),
            mock.patch.object(cli.os, "fsync"),
            mock.patch.object(cli, "_unlink_finalization_lock_at") as mocked_unlink,
            mock.patch.object(cli.os, "close", side_effect=close_fd) as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli._acquire_finalization_lock(state_file)

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)
        mocked_unlink.assert_called_once()

    def test_finalization_lock_cleans_up_when_write_and_fd_close_are_interrupted(self) -> None:
        state_file = Path("/probe/state.json")

        def close_fd(fd: int) -> None:
            if fd == 123:
                raise KeyboardInterrupt

        with (
            mock.patch.object(cli, "assert_no_symlink_ancestors"),
            mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(cli.os, "open", return_value=123),
            mock.patch.object(cli.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(cli, "_finalization_lock_identity_for_pid", return_value=None),
            mock.patch.object(cli, "_write_all", side_effect=KeyboardInterrupt),
            mock.patch.object(cli, "_unlink_finalization_lock_at") as mocked_unlink,
            mock.patch.object(cli.os, "close", side_effect=close_fd) as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli._acquire_finalization_lock(state_file)

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)
        mocked_unlink.assert_called_once()

    def test_finalization_lock_releases_owned_lock_when_parent_close_is_interrupted(self) -> None:
        state_file = Path("/probe/state.json")

        def close_fd(fd: int) -> None:
            if fd == 456:
                raise KeyboardInterrupt

        with (
            mock.patch.object(cli, "assert_no_symlink_ancestors"),
            mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(cli.os, "open", return_value=123),
            mock.patch.object(cli.os, "fstat", return_value=mock.Mock()),
            mock.patch.object(cli, "_finalization_lock_identity_for_pid", return_value=None),
            mock.patch.object(cli, "_write_all"),
            mock.patch.object(cli.os, "fsync"),
            mock.patch.object(cli, "_release_finalization_lock") as mocked_release,
            mock.patch.object(cli.os, "close", side_effect=close_fd) as mocked_close,
        ):
            with self.assertRaises(KeyboardInterrupt):
                cli._acquire_finalization_lock(state_file)

        mocked_close.assert_any_call(123)
        mocked_close.assert_any_call(456)
        mocked_release.assert_called_once_with(cli._finalization_lock_path(state_file))

    def test_ensure_private_text_file_keeps_existing_blacklist_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_text("geheim\n", encoding="utf-8")

            cli._ensure_private_text_file(path)

            self.assertEqual(path.read_text(encoding="utf-8"), "geheim\n")

    def test_ensure_private_text_file_rejects_symlinked_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real-config"
            real.mkdir()
            link = root / "config"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "blacklist file"):
                cli._ensure_private_text_file(link / "blacklist.txt")

            self.assertEqual(list(real.iterdir()), [])
            self.assertTrue(link.is_symlink())

    def test_allocate_recording_artifacts_retries_existing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            root.mkdir(parents=True)
            (root / "20260101-000000-000000.wav").write_bytes(b"old")
            (root / "20260101-000000-000000.log").write_text("old", encoding="utf-8")

            with (
                mock.patch("speed_of_cinnamon.cli.recordings_dir", return_value=root),
                mock.patch("speed_of_cinnamon.cli.timestamp", return_value="20260101-000000-000000"),
            ):
                audio_path, log_path = cli._allocate_recording_artifacts()

            self.assertEqual(audio_path.name, "20260101-000000-000000-01.wav")
            self.assertEqual(log_path.name, "20260101-000000-000000-01.log")
            self.assertTrue(audio_path.exists())
            self.assertFalse(log_path.exists())

    def test_allocate_recording_artifacts_caps_collision_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            root.mkdir(parents=True)
            base = "20260101-000000-000000"
            for index in range(cli.MAX_RECORDING_ARTIFACT_CANDIDATES):
                stem = base if index == 0 else f"{base}-{index:02d}"
                (root / f"{stem}.wav").write_bytes(b"old")

            with (
                mock.patch("speed_of_cinnamon.cli.recordings_dir", return_value=root),
                mock.patch("speed_of_cinnamon.cli.timestamp", side_effect=[base]) as mocked_timestamp,
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to allocate collision-free recording artifacts"):
                    cli._allocate_recording_artifacts()

            mocked_timestamp.assert_called_once_with()

    def test_allocate_recording_artifacts_removes_partial_audio_after_prepare_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            root.mkdir(parents=True)
            attempts = 0

            def fake_prepare(path: Path, *, field_name: str, exclusive: bool = True) -> None:
                nonlocal attempts
                attempts += 1
                path.write_bytes(b"partial")
                if attempts == 1:
                    raise cli._PrivateFilePrepareError("prepare failed", created=True)

            with (
                mock.patch("speed_of_cinnamon.cli.recordings_dir", return_value=root),
                mock.patch("speed_of_cinnamon.cli.timestamp", side_effect=[
                    "20260101-000000-000000",
                    "20260101-000000-000001",
                ]),
                mock.patch("speed_of_cinnamon.cli._prepare_private_file", side_effect=fake_prepare),
            ):
                audio_path, log_path = cli._allocate_recording_artifacts()

            self.assertFalse((root / "20260101-000000-000000.wav").exists())
            self.assertEqual(audio_path.name, "20260101-000000-000001.wav")
            self.assertEqual(log_path.name, "20260101-000000-000001.log")

    def test_allocate_recording_artifacts_keeps_race_created_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            root.mkdir(parents=True)
            attempts = 0
            race_path = root / "20260101-000000-000000.wav"
            real_prepare = cli._prepare_private_file

            def fake_prepare(path: Path, *, field_name: str, exclusive: bool = True) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    path.write_bytes(b"foreign")
                    raise cli._PrivateFilePrepareError("prepare failed", created=False, errno_value=errno.EEXIST)
                real_prepare(path, field_name=field_name, exclusive=exclusive)

            with (
                mock.patch("speed_of_cinnamon.cli.recordings_dir", return_value=root),
                mock.patch("speed_of_cinnamon.cli.timestamp", return_value="20260101-000000-000000"),
                mock.patch("speed_of_cinnamon.cli._prepare_private_file", side_effect=fake_prepare),
            ):
                audio_path, log_path = cli._allocate_recording_artifacts()

            self.assertEqual(race_path.read_bytes(), b"foreign")
            self.assertEqual(audio_path.name, "20260101-000000-000000-01.wav")
            self.assertEqual(log_path.name, "20260101-000000-000000-01.log")

    def test_allocate_recording_artifacts_fails_if_partial_audio_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            root.mkdir(parents=True)

            def fake_prepare(path: Path, *, field_name: str, exclusive: bool = True) -> None:
                path.write_bytes(b"partial")
                raise cli._PrivateFilePrepareError("prepare failed", created=True)

            with (
                mock.patch("speed_of_cinnamon.cli.recordings_dir", return_value=root),
                mock.patch("speed_of_cinnamon.cli.timestamp", return_value="20260101-000000-000000"),
                mock.patch("speed_of_cinnamon.cli._prepare_private_file", side_effect=fake_prepare),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to clean partial recording audio file"):
                    cli._allocate_recording_artifacts()

    def test_write_json_atomic_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "failed to write JSON output"):
                cli._write_json_atomic(link / "security.json", {"status": "ok"}, max_bytes=10_000)

            self.assertFalse((real / "security.json").exists())

    def test_version_option_prints_current_version(self) -> None:
        parser = cli.build_parser()
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            with self.assertRaises(SystemExit) as exc:
                parser.parse_args(["--version"])
        self.assertEqual(exc.exception.code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"speed-of-cinnamon {cli.__version__}")

    def test_coerce_log_level_from_environment(self) -> None:
        with mock.patch.dict("speed_of_cinnamon.cli.os.environ", {"SPEED_OF_CINNAMON_LOG_LEVEL": "INFO"}):
            self.assertEqual(cli._coerce_log_level_from_environment(), "info")
        with mock.patch.dict("speed_of_cinnamon.cli.os.environ", {"SPEED_OF_CINNAMON_LOG_LEVEL": "info\n"}):
            self.assertEqual(cli._coerce_log_level_from_environment(), cli.DEFAULT_LOG_LEVEL)
        with mock.patch.dict("speed_of_cinnamon.cli.os.environ", {"SPEED_OF_CINNAMON_LOG_LEVEL": "trace"}):
            self.assertEqual(cli._coerce_log_level_from_environment(), cli.DEFAULT_LOG_LEVEL)

    def test_diagnostics_desktop_fields_are_sanitized(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "XDG_CURRENT_DESKTOP": "X-Cinnamon\n",
                "XDG_SESSION_TYPE": "x11",
                "DESKTOP_SESSION": "cinnamon\\x00",
            },
        ):
            stdout = io.StringIO()
            with tempfile.TemporaryDirectory() as tmp:
                state_file = Path(tmp) / "state.json"
                with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}, clear=False):
                    with redirect_stdout(stdout):
                        cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["desktop"]["current_desktop"], "")
        self.assertEqual(payload["desktop"]["desktop_session"], "")
        self.assertEqual(payload["desktop"]["session_type"], "x11")

    def test_diagnostics_sanitizes_applet_lifecycle_payload(self) -> None:
        payload = cli._diagnostics_applet_lifecycle_payload({
            "applet-lifecycle": {
                "state": "DEGRADED",
                "error_counts": {"clipboard": 3, "private path": 99, "too_many": 999999},
                "disabled_groups": ["clipboard", "private path", "clipboard"],
                "resources": {"timers": 2, "private path": 77},
                "process_groups": {"keyboard": 1, "private path": 88},
            }
        })

        self.assertTrue(payload["present"])
        self.assertEqual(payload["state"], "DEGRADED")
        self.assertEqual(payload["error_counts"], {"clipboard": 3, "too_many": 100_000})
        self.assertEqual(payload["disabled_groups"], ["clipboard"])
        self.assertEqual(payload["resources"], {"timers": 2})
        self.assertEqual(payload["process_groups"], {"keyboard": 1})

    def test_version_consistency_between_metadata_and_package(self) -> None:
        project_version = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(project_version, cli.__version__)
        applet_metadata = json.loads(Path("files/speed-of-cinnamon@H234598/metadata.json").read_text(encoding="utf-8"))
        applet_version = applet_metadata["version"]
        self.assertEqual(project_version, applet_version)
        self.assertIn(f"Version: {project_version}", applet_metadata["comments"])
        applet_schema = json.loads(Path("files/speed-of-cinnamon@H234598/settings-schema.json").read_text(encoding="utf-8"))
        self.assertIn("about-page", applet_schema["layout"]["pages"])
        self.assertIn("about-version", applet_schema["layout"]["about-section"]["keys"])
        self.assertIn(f"Version: {project_version}", applet_schema["about-version"]["description"])

    def _write_wav(self, path: Path, samples: list[int]) -> None:
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"".join(sample.to_bytes(2, "little", signed=True) for sample in samples))

    def test_insert_text_can_be_disabled(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = cli.run(["insert-text", "hello", "--insert-method", "none", "--json"])
        self.assertEqual(code, 0)

    @mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True)
    def test_insert_text_can_sanitize_special_chars(self, mocked_insert: mock.Mock) -> None:
        with redirect_stdout(io.StringIO()):
            code = cli.run(["insert-text", "Grüße", "--insert-method", "none", "--sanitize-special-chars", "--json"])
        self.assertEqual(code, 0)
        mocked_insert.assert_called_once_with("Grusse", "none", 8)

    def test_insert_text_rejects_overlong_text(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run([
                "insert-text",
                "x" * (cli.MAX_TRANSCRIBER_TEXT_CHARS + 10),
                "--insert-method",
                "none",
                "--json",
            ])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text is too large", payload["error"])

    def test_insert_text_rejects_null_bytes(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["insert-text", "hello\x00", "--insert-method", "none", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_insert_text_rejects_negative_typing_delay(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["insert-text", "hello", "--insert-method", "none", "--typing-delay-ms", "-1", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("typing-delay-ms must be at least 0", payload["error"])

    def test_insert_text_rejects_excessive_typing_delay(self) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run([
                "insert-text",
                "hello",
                "--insert-method",
                "none",
                "--typing-delay-ms",
                str(cli.MAX_TYPING_DELAY_MS + 1),
                "--json",
            ])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("typing-delay-ms must be at most", payload["error"])

    def test_transcribe_file_with_command_template(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf test",
                    "--post-process-command",
                    "python3 -c 'import sys; print(sys.stdin.read().upper())'",
                    "--confirm-plaintext-output",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            saved = Path(payload["transcript_path"]).read_text(encoding="utf-8").strip()
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "TEST")
        self.assertEqual(saved, "TEST")

    def test_transcribe_file_reports_stale_transient_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "input.wav"
            audio.write_bytes(b"audio")
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            transcripts.mkdir(parents=True)
            stale = transcripts / ".stale.abcd.tmp.txt"
            stale.write_text("stale plaintext\n", encoding="utf-8")
            owner = cli._transient_transcript_owner_path(stale)
            owner_target = tmp_path / "foreign-owner"
            owner_target.write_text("foreign owner\n", encoding="utf-8")
            owner.symlink_to(owner_target)
            old_mtime = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            os.utime(stale, (old_mtime, old_mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf test",
                    "--confirm-plaintext-output",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            stale_exists = stale.exists()
            owner_is_symlink = owner.is_symlink()
            target_exists = owner_target.exists()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("failed to scan or delete 1 cleanup artifact", payload["error"])
        self.assertEqual(payload["transcript"], "test")
        self.assertEqual(payload["cleanup_failed_path_count"], 1)
        self.assertNotIn("cleanup_failed_paths", payload)
        self.assertFalse(stale_exists)
        self.assertTrue(owner_is_symlink)
        self.assertTrue(target_exists)

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_accepts_transcriber_aliases(self, mocked_validate: mock.Mock, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "ok")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="polished")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="raw")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_uses_separate_openai_compatible_text_model(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_post_process: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai-compatible",
                    "--post-process-backend",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-text-model",
                    "gpt-4o-mini",
                    "--openai-compatible-api-key",
                    "secret",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "polished")
        self.assertEqual(mocked_transcribe.call_args.kwargs["openai_compatible_model"], "gpt-4o-transcribe")
        self.assertIs(mocked_transcribe.call_args.kwargs["openai_compatible_flex_processing"], True)
        self.assertEqual(mocked_post_process.call_args.args[9], "gpt-4o-mini")
        self.assertEqual(mocked_post_process.call_args.args[11], "secret")
        self.assertIs(mocked_post_process.call_args.args[12], True)

    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="polished")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="raw")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_can_disable_openai_compatible_flex_processing(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_post_process: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai-compatible",
                    "--post-process-backend",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-text-model",
                    "gpt-4o-mini",
                    "--openai-compatible-api-key",
                    "secret",
                    "--no-openai-compatible-flex-processing",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "polished")
        self.assertIs(mocked_transcribe.call_args.kwargs["openai_compatible_flex_processing"], False)
        self.assertIs(mocked_post_process.call_args.args[12], False)

    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="polished")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="raw")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_defaults_openai_compatible_text_model_to_gpt_4o_mini(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_post_process: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "openai-compatible",
                    "--post-process-backend",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-api-key",
                    "secret",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "polished")
        self.assertEqual(mocked_transcribe.call_args.kwargs["openai_compatible_model"], "gpt-4o-transcribe")
        self.assertIs(mocked_transcribe.call_args.kwargs["openai_compatible_flex_processing"], True)
        self.assertEqual(mocked_post_process.call_args.args[9], "gpt-4o-mini")
        self.assertEqual(mocked_post_process.call_args.args[11], "secret")
        self.assertIs(mocked_post_process.call_args.args[12], True)

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_accepts_command_alias(self, mocked_validate: mock.Mock, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "template",
                    "--transcriber-command",
                    "printf ok",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "ok")
        self.assertEqual(mocked_transcribe.call_args.kwargs["backend"], "command")

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_accepts_faster_whisper_alias(self, mocked_validate: mock.Mock, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "faster-whisper",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "ok")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="faster-whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    def test_transcribe_file_passes_personalization_to_post_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            command = "python3 -c \"import sys; print(sys.argv[1] + '|' + sys.argv[2])\" {text} {vocabulary}"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf raw",
                    "--post-process-command",
                    command,
                    "--personal-context",
                    "Use project terms.",
                    "--vocabulary",
                    "PipeWire",
                    "--confirm-plaintext-output",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "raw|PipeWire")

    def test_transcribe_file_rejects_transcript_write_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.path_safety._rename_without_replacing", side_effect=OSError("disk full")),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hello",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("failed to write transcript file", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="encrypted ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_can_encrypt_stored_transcript_with_passphrase(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            stale_plaintext_path = Path(tmp) / "speed-of-cinnamon" / "transcripts" / "input.txt"
            stale_plaintext_path.parent.mkdir(parents=True)
            stale_plaintext_path.write_text("stale plaintext\n", encoding="utf-8")
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf encrypted",
                    "--artifact-encryption",
                    "passphrase",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            encrypted_path = next((Path(tmp) / "speed-of-cinnamon" / "transcripts").glob("input.txt.socenc"))
            plaintext_path = encrypted_path.with_name(encrypted_path.name.removesuffix(".socenc"))
            with mock.patch.dict(os.environ, env, clear=False):
                decrypted = artifact_crypto.read_decrypted_bytes_from_file(
                    encrypted_path,
                    kind="transcript",
                    field_name="transcript file",
                ).decode("utf-8")

                history = cli.read_transcript_history(5)
            encrypted_exists = encrypted_path.exists()
            plaintext_exists = plaintext_path.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "")
        self.assertTrue(payload["transcript_output_redacted"])
        self.assertTrue(payload["transcript_path_present"])
        self.assertNotIn("transcript_path", payload)
        self.assertNotIn(str(encrypted_path), json.dumps(payload))
        self.assertTrue(payload["transcript_encrypted"])
        self.assertEqual(payload["transcript_encryption"], "passphrase")
        self.assertTrue(encrypted_path.name.endswith(".txt.socenc"))
        self.assertTrue(encrypted_exists)
        self.assertFalse(plaintext_exists)
        self.assertEqual(decrypted, "encrypted ok\n")
        self.assertEqual(history[0]["preview"], "encrypted ok")
        self.assertEqual(plaintext_path, stale_plaintext_path)
        mocked_transcribe.assert_called_once()
        self.assertNotEqual(mocked_transcribe.call_args.kwargs["text_path"], plaintext_path)

    def test_stored_transcript_rolls_back_encrypted_file_when_plaintext_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_root = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_root.mkdir(parents=True)
            transcript = transcript_root / "input.txt"
            transcript.write_text("stale plaintext\n", encoding="utf-8")
            encrypted_transcript = Path(f"{transcript}.socenc")
            args = argparse.Namespace(artifact_encryption="passphrase")
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli._remove_transcript_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to remove plaintext transcript artifact"):
                    cli._write_stored_transcript(transcript, "secret transcript\n", args)
            plaintext_exists = transcript.exists()
            encrypted_exists = encrypted_transcript.exists()

        self.assertTrue(plaintext_exists)
        self.assertFalse(encrypted_exists)

    def test_write_stored_transcript_rejects_unencodable_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "input.txt"
            args = argparse.Namespace(artifact_encryption="off")
            with self.assertRaisesRegex(RuntimeError, "failed to write transcript file"):
                cli._write_stored_transcript(transcript, "secret\ud800text", args)
            self.assertFalse(transcript.exists())

    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_prepares_private_transient_transcript_for_encrypted_storage(
        self,
        mocked_validate: mock.Mock,
    ) -> None:
        captured_path: list[Path] = []

        def fake_transcribe(**kwargs: object) -> str:
            text_path = kwargs["text_path"]
            self.assertIsInstance(text_path, Path)
            captured_path.append(text_path)
            self.assertTrue(text_path.exists())
            self.assertEqual(text_path.stat().st_mode & 0o777, 0o600)
            text_path.write_text("encrypted ok\n", encoding="utf-8")
            return "encrypted ok"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            mocked_validate.return_value = audio
            stdout = io.StringIO()
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=fake_transcribe),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf encrypted",
                    "--artifact-encryption",
                    "passphrase",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertTrue(payload["transcript_encrypted"])
        self.assertEqual(len(captured_path), 1)
        self.assertFalse(captured_path[0].exists())

    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_fails_closed_when_transient_transcript_is_replaced(
        self,
        mocked_validate: mock.Mock,
    ) -> None:
        captured_path: list[Path] = []

        def fake_transcribe(**kwargs: object) -> str:
            text_path = kwargs["text_path"]
            self.assertIsInstance(text_path, Path)
            captured_path.append(text_path)
            text_path.unlink()
            text_path.write_text("replacement\n", encoding="utf-8")
            return "replacement"

        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            mocked_validate.return_value = audio
            stdout = io.StringIO()
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=fake_transcribe),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf encrypted",
                    "--artifact-encryption",
                    "passphrase",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            replacement_exists = captured_path[0].exists() if captured_path else False

        self.assertNotEqual(code, 0)
        self.assertIn("failed to delete transient transcript file", payload["error"])
        self.assertEqual(len(captured_path), 1)
        self.assertTrue(replacement_exists)

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="encrypted ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_can_confirm_plaintext_output_with_encrypted_storage(
        self,
        mocked_validate: mock.Mock,
        _mocked_transcribe: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with mock.patch.dict(os.environ, env, clear=False), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf encrypted",
                    "--artifact-encryption",
                    "passphrase",
                    "--confirm-plaintext-output",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "encrypted ok")
        self.assertFalse(payload["transcript_output_redacted"])
        self.assertTrue(payload["transcript_encrypted"])

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="plaintext ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_redacts_plaintext_output_when_artifact_encryption_off_without_confirm(
        self,
        mocked_validate: mock.Mock,
        _mocked_transcribe: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf plaintext",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            transcript_path = Path(tmp) / "speed-of-cinnamon" / "transcripts" / "input.txt"
            transcript_file = transcript_path.read_text(encoding="utf-8").strip()
            transcript_exists = transcript_path.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "")
        self.assertTrue(payload["transcript_output_redacted"])
        self.assertTrue(payload["transcript_path_present"])
        self.assertNotIn("transcript_path", payload)
        self.assertNotIn(str(transcript_path), json.dumps(payload))
        self.assertNotIn("input.txt", json.dumps(payload))
        self.assertFalse(payload["transcript_encrypted"])
        self.assertTrue(transcript_exists)
        self.assertEqual(transcript_file, "plaintext ok")

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="plaintext ok")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_can_confirm_plaintext_output_when_artifact_encryption_off(
        self,
        mocked_validate: mock.Mock,
        _mocked_transcribe: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf plaintext",
                    "--confirm-plaintext-output",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            transcript_path = Path(payload["transcript_path"])
            transcript_file = transcript_path.read_text(encoding="utf-8").strip()
            transcript_exists = transcript_path.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "plaintext ok")
        self.assertFalse(payload["transcript_output_redacted"])
        self.assertFalse(payload["transcript_encrypted"])
        self.assertEqual(transcript_file, "plaintext ok")
        self.assertTrue(transcript_exists)
        self.assertFalse(payload["transcript_path"].endswith(".socenc"))

    def test_reencrypting_socenc_recording_removes_plaintext_sibling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "speed-of-cinnamon" / "recordings" / "recording.flac"
            recording.parent.mkdir(parents=True)
            args = argparse.Namespace(artifact_encryption="passphrase")
            env = {
                "XDG_CACHE_HOME": tmp,
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with mock.patch.dict(os.environ, env, clear=False):
                encrypted_recording, _mode = artifact_crypto.write_encrypted_bytes_atomically(
                    recording,
                    b"encrypted audio",
                    "passphrase",
                    kind="recording",
                    field_name="recording audio file",
                )
                recording.write_bytes(b"stale plaintext audio")

                output_path, effective_mode = cli._encrypt_kept_recording_artifact(encrypted_recording, args)
                decrypted = artifact_crypto.read_decrypted_bytes_from_file(
                    output_path,
                    kind="recording",
                    field_name="recording audio file",
                )

        self.assertEqual(output_path, encrypted_recording)
        self.assertEqual(effective_mode, "passphrase")
        self.assertFalse(recording.exists())
        self.assertEqual(decrypted, b"encrypted audio")

    def test_encrypt_kept_recording_rolls_back_encrypted_file_when_plaintext_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "speed-of-cinnamon" / "recordings" / "recording.flac"
            recording.parent.mkdir(parents=True)
            recording.write_bytes(b"plaintext audio")
            encrypted_recording = Path(f"{recording}.socenc")
            args = argparse.Namespace(artifact_encryption="passphrase")
            env = {
                "XDG_CACHE_HOME": tmp,
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to remove plaintext recording artifact"):
                    cli._encrypt_kept_recording_artifact(recording, args)
            plaintext_exists = recording.exists()
            encrypted_exists = encrypted_recording.exists()

        self.assertTrue(plaintext_exists)
        self.assertFalse(encrypted_exists)

    def test_reencrypting_socenc_recording_requires_encrypted_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recording = Path(tmp) / "recording.flac.socenc"
            recording.write_bytes(b"not encrypted")
            recording.chmod(0o600)
            args = argparse.Namespace(artifact_encryption="passphrase")
            env = {artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32)))}
            with mock.patch.dict(os.environ, env, clear=False):
                with self.assertRaisesRegex(RuntimeError, "envelope is missing"):
                    cli._encrypt_kept_recording_artifact(recording, args)

    def test_recording_level_rejects_encrypted_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "active.flac.socenc"
            audio.write_bytes(b"encrypted")
            state = RecordingState(status="recorded", audio_path=str(audio))
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.read_recording_level") as read_level,
            ):
                payload = cli._recording_level_payload(state)
        self.assertIsNotNone(payload)
        self.assertFalse(payload["ok"])
        self.assertIn("encrypted recording artifacts", payload["detail"])
        read_level.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="must not be stored as plaintext")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_keyring_failure_without_explicit_passphrase_fails_closed(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            mocked_validate.return_value = audio
            transcript_root = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "XDG_CONFIG_HOME": tmp,
                        "XDG_STATE_HOME": tmp,
                        artifact_crypto.PASSPHRASE_ENV: "",
                        artifact_crypto.PASSPHRASE_FILE_ENV: "",
                    },
                    clear=False,
                ),
                mock.patch("speed_of_cinnamon.artifact_crypto._load_keyring_key", side_effect=artifact_crypto.ArtifactCryptoError("no dbus")),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf encrypted",
                    "--artifact-encryption",
                    "keyring",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            plaintext_exists = (transcript_root / "input.txt").exists()
            encrypted_exists = (transcript_root / "input.txt.socenc").exists()
            key_file_exists = (Path(tmp) / "speed-of-cinnamon" / "artifact.key").exists()
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "[redacted error details]")
        self.assertNotIn("must not be stored as plaintext", json.dumps(payload))
        self.assertFalse(plaintext_exists)
        self.assertFalse(encrypted_exists)
        self.assertFalse(key_file_exists)
        mocked_transcribe.assert_called_once()

    @mock.patch("speed_of_cinnamon.cli.insert_text")
    @mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="redacted")
    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="blacklisteintrag: geheim\nHallo")
    @mock.patch("speed_of_cinnamon.cli.apply_security_mode", return_value=("redacted", 1))
    @mock.patch("speed_of_cinnamon.cli.update_blacklist_file", return_value=["geheim"])
    @mock.patch("speed_of_cinnamon.cli.load_blacklist_file", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_finalize_applies_mixed_blacklist_directives_and_security_mode(
        self,
        mocked_validate: mock.Mock,
        mocked_load: mock.Mock,
        mocked_update: mock.Mock,
        mocked_security: mock.Mock,
        mocked_post_process: mock.Mock,
        mocked_prepare: mock.Mock,
        mocked_insert: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args()
            silence = cli.SilenceDetectionResult(False, False, 4.0, 0.0, 3.0, 0.0, "speech detected")
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="blacklisteintrag: geheim\nHallo"),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=audio),
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
        mocked_update.assert_called_once_with(mock.ANY, ["geheim"])
        self.assertGreaterEqual(mocked_security.call_count, 1)
        mocked_security.assert_any_call("Hallo", ["geheim"])
        self.assertEqual(payload["security"]["blacklist_added"], ["[redacted]"])
        self.assertEqual(payload["security"]["blacklist_added_count"], 1)
        self.assertNotIn("geheim", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(payload["transcript"], "redacted")
        self.assertEqual(final_state.transcript, "redacted")
        mocked_insert.assert_called_once_with("redacted", "none", 0)

    @mock.patch("speed_of_cinnamon.cli.insert_text")
    @mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="")
    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="blacklisteintrag: geheim")
    @mock.patch("speed_of_cinnamon.cli.apply_security_mode", return_value=("", 0))
    @mock.patch("speed_of_cinnamon.cli.update_blacklist_file", return_value=["geheim"])
    @mock.patch("speed_of_cinnamon.cli.load_blacklist_file", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_finalize_blacklist_directive_is_not_copied(
        self,
        mocked_validate: mock.Mock,
        mocked_load: mock.Mock,
        mocked_update: mock.Mock,
        mocked_security: mock.Mock,
        mocked_post_process: mock.Mock,
        mocked_prepare: mock.Mock,
        mocked_insert: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False, insert_method="clipboard-paste")
            silence = cli.SilenceDetectionResult(True, False, 4.0, 0.0, 3.0, 0.0, "speech detected")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="blacklisteintrag: geheim"),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=audio),
            ):
                payload = cli.finalize_recording(args, store, store.read())

        final_state = store.read()
        self.assertEqual(payload["transcript"], "")
        self.assertEqual(final_state.transcript, "")
        self.assertEqual(payload["security"]["blacklist_added"], ["[redacted]"])
        self.assertEqual(payload["security"]["blacklist_added_count"], 1)
        self.assertNotIn("geheim", json.dumps(payload, ensure_ascii=False))
        mocked_insert.assert_not_called()
        mocked_prepare.assert_not_called()

    def test_finalize_empty_raw_transcript_skips_parser_and_insert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            text_dir = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings_root.mkdir(parents=True)
            text_dir.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(insert_method="clipboard-paste")
            silence = cli.SilenceDetectionResult(False, False, 4.0, 0.0, 3.0, 0.0, "speech detected")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value=" leere Aufnahme. "),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.transcript_dir", return_value=text_dir),
                mock.patch("speed_of_cinnamon.cli._process_transcript") as mocked_process,
                mock.patch("speed_of_cinnamon.cli.prepare_output_text") as mocked_prepare,
                mock.patch("speed_of_cinnamon.cli.insert_text") as mocked_insert,
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()

        self.assertEqual(payload["message"], "recording finished without transcript")
        self.assertEqual(payload["transcript"], "")
        self.assertEqual(final_state.transcript, "")
        mocked_process.assert_not_called()
        mocked_prepare.assert_not_called()
        mocked_insert.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.load_blacklist_file", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=mock.ANY)
    def test_transcribe_file_applies_security_directives(self, _mock_trim: mock.Mock, _mock_blacklist: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="blacklist anzeigen"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="blacklist anzeigen"),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli._open_blacklist_document", return_value=True),
                mock.patch("speed_of_cinnamon.cli.apply_security_mode", return_value=("", 0)),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf raw",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertTrue(payload["security"]["blacklist_opened"])
        self.assertEqual(payload["transcript"], "")
        self.assertNotIn("blacklist anzeigen", payload["transcript"])

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="Das Wort geheim steht im Protokoll.")
    def test_transcribe_file_uses_disk_blacklist_file_before_output(self, _mock_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            blacklist = Path(tmp) / "speed-of-cinnamon" / "blacklist.txt"
            blacklist.parent.mkdir(parents=True, exist_ok=True)
            blacklist.write_text("geheim\n", encoding="utf-8")
            blacklist.chmod(0o600)
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(tmp), "XDG_STATE_HOME": str(tmp)}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--confirm-plaintext-output",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript"], "Das Wort [redacted blacklist item] steht im Protokoll.")
        self.assertEqual(payload["security"]["blacklist_hits"], 1)

    @mock.patch("speed_of_cinnamon.cli._apply_security_mask_only")
    @mock.patch("speed_of_cinnamon.cli._apply_security_post_processing")
    @mock.patch("speed_of_cinnamon.cli.post_process_text")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_runs_security_post_processing_before_post_processing(
        self,
        mocked_validate: mock.Mock,
        mocked_post: mock.Mock,
        mocked_security: mock.Mock,
        mocked_mask: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            call_order: list[str] = []

            def security_side_effect(text: str) -> tuple[str, dict[str, object]]:
                call_order.append("security")
                return ("sicher", {"blacklist_added": [], "blacklist_opened": False, "redacted_words": [], "blacklist_hits": 0})

            def mask_side_effect(text: str) -> tuple[str, dict[str, object]]:
                call_order.append("mask")
                return (text, {"blacklist_added": [], "blacklist_opened": False, "redacted_words": [], "blacklist_hits": 0})

            def post_process_side_effect(*args: object, **kwargs: object) -> str:
                call_order.append("post")
                return args[0]

            mocked_security.side_effect = security_side_effect
            mocked_mask.side_effect = mask_side_effect
            mocked_post.side_effect = post_process_side_effect
            mocked_validate.return_value = audio
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="roher text"),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--post-process-backend",
                    "openai-compatible",
                    "--ollama-model",
                    "llama3.2:3b",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(call_order, ["security", "post", "mask"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["transcript"], "sicher")

    @mock.patch("speed_of_cinnamon.cli._open_blacklist_document")
    @mock.patch("speed_of_cinnamon.cli.update_blacklist_file")
    @mock.patch("speed_of_cinnamon.cli.load_blacklist_file", return_value=["geheim"])
    @mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="blacklisteintrag: modellwort\nblacklist anzeigen\ntoken: abc123 und geheim")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_masks_remote_post_processing_output_after_model(
        self,
        mocked_validate: mock.Mock,
        _mocked_post: mock.Mock,
        _mocked_load: mock.Mock,
        mocked_update: mock.Mock,
        mocked_open: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            mocked_validate.return_value = audio
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="roher text"),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--post-process-backend",
                    "openai-compatible",
                    "--confirm-plaintext-output",
                    "--json",
                ])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertNotIn("abc123", payload["transcript"])
        self.assertNotIn("geheim", payload["transcript"])
        self.assertNotIn("modellwort", payload["transcript"])
        self.assertNotIn("blacklisteintrag", payload["transcript"])
        self.assertNotIn("blacklist anzeigen", payload["transcript"])
        self.assertIn("[redacted token]", payload["transcript"])
        self.assertIn("[redacted blacklist item]", payload["transcript"])
        self.assertEqual(payload["security"]["blacklist_hits"], 1)
        mocked_update.assert_not_called()
        mocked_open.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli._apply_security_mask_only")
    @mock.patch("speed_of_cinnamon.cli._apply_security_post_processing")
    @mock.patch("speed_of_cinnamon.cli.post_process_text")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_transcribe_file_runs_security_post_processing_after_post_processing(
        self,
        mocked_validate: mock.Mock,
        mocked_post: mock.Mock,
        mocked_security: mock.Mock,
        mocked_mask: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            call_order: list[str] = []

            def security_side_effect(text: str) -> tuple[str, dict[str, object]]:
                call_order.append("security")
                return ("before", {"blacklist_added": [], "blacklist_opened": False, "redacted_words": [], "blacklist_hits": 0})

            def mask_side_effect(text: str) -> tuple[str, dict[str, object]]:
                call_order.append("mask")
                return (text, {"blacklist_added": [], "blacklist_opened": False, "redacted_words": [], "blacklist_hits": 0})

            def post_process_side_effect(*args: object, **kwargs: object) -> str:
                call_order.append("post")
                return "nach"

            mocked_security.side_effect = security_side_effect
            mocked_mask.side_effect = mask_side_effect
            mocked_post.side_effect = post_process_side_effect
            mocked_validate.return_value = audio
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="roher text"),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--post-process-backend",
                    "command",
                    "--post-process-command",
                    "cat",
                    "--confirm-plaintext-output",
                    "--json",
                ])
        self.assertEqual(code, 0)
        self.assertEqual(call_order, ["security", "post", "mask"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["transcript"], "nach")

    @mock.patch("speed_of_cinnamon.cli._process_transcript")
    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="roher text")
    @mock.patch("speed_of_cinnamon.cli.validate_audio_file")
    def test_command_transcribe_file_writes_only_final_text(
        self,
        mocked_validate: mock.Mock,
        mocked_transcribe: mock.Mock,
        mocked_process: mock.Mock,
    ) -> None:
        mocked_process.return_value = (
            "final",
            {
                "blacklist_added": [],
                "blacklist_opened": False,
                "redacted_words": [],
                "blacklist_hits": 0,
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            text_dir = Path(tmp) / "transcripts"
            text_dir.mkdir()
            mocked_validate.return_value = audio
            mocked_transcribe.return_value = "roher text"
            stdout = io.StringIO()
            expected_path = text_dir / "input.txt"
            with (
                mock.patch("speed_of_cinnamon.cli.transcript_dir", return_value=text_dir),
                mock.patch("speed_of_cinnamon.cli._write_text_atomic") as mocked_write,
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf roher text",
                    "--post-process-backend",
                    "none",
                    "--confirm-plaintext-output",
                    "--json",
                ])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["transcript"], "final")
        self.assertEqual(payload["transcript_path"], str(expected_path))
        mocked_write.assert_called_once_with(expected_path, "final\n")

    def test_is_remote_post_process_backend(self) -> None:
        self.assertTrue(cli._is_remote_post_process_backend("openai-compatible"))
        self.assertTrue(cli._is_remote_post_process_backend("openai"))
        self.assertFalse(cli._is_remote_post_process_backend("command"))
        self.assertFalse(cli._is_remote_post_process_backend("\x85openai"))
        self.assertFalse(cli._is_remote_post_process_backend("\\x85openai"))

    def test_effective_post_process_backend_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "post-process backend contains invalid control character"):
            cli._effective_post_process_backend("\x85openai", "")

    @mock.patch("speed_of_cinnamon.cli.apply_security_mode")
    @mock.patch("speed_of_cinnamon.cli.apply_blacklist_mode", return_value=("Hallo geheim", 1))
    @mock.patch("speed_of_cinnamon.cli.update_blacklist_file", return_value=["geheim"])
    @mock.patch("speed_of_cinnamon.cli.load_blacklist_file", return_value=["geheim"])
    def test_security_post_processing_runs_second_pass_for_blacklist_hits(
        self,
        mocked_load: mock.Mock,
        mocked_update: mock.Mock,
        mocked_blacklist: mock.Mock,
        mocked_security: mock.Mock,
    ) -> None:
        mocked_security.side_effect = [
            ("Hallo [redacted blacklist item]", 1),
            ("Hallo [redacted blacklist item]", 0),
        ]

        sanitized, security = cli._apply_security_post_processing("Hallo geheim")

        self.assertEqual(sanitized, "Hallo [redacted blacklist item]")
        self.assertEqual(mocked_security.call_count, 2)
        self.assertEqual(security["blacklist_hits"], 1)
        self.assertEqual(security["blacklist_added"], [])
        mocked_blacklist.assert_called_once_with("Hallo geheim", ["geheim"])
        mocked_load.assert_called_once_with(mock.ANY, strict=True)
        mocked_update.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.list_input_sources")
    def test_list_inputs_outputs_sources(self, mocked_sources: mock.Mock) -> None:
        mocked_sources.return_value = [
            InputSource(
                id="11",
                name="alsa_input.usb-mic.analog-stereo",
                description="USB Microphone",
                driver="PipeWire",
                state="RUNNING",
                default=True,
            )
        ]
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["list-inputs", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["sources"][0]["name"], "alsa_input.usb-mic.analog-stereo")
        self.assertTrue(payload["sources"][0]["default"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value="invalid")
    def test_list_inputs_rejects_non_list_sources(self, mocked_sources: mock.Mock) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["list-inputs", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("input sources must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[object()])
    def test_list_inputs_rejects_invalid_source_entry(self, mocked_sources: mock.Mock) -> None:
        with redirect_stdout(io.StringIO()) as capture:
            code = cli.run(["list-inputs", "--json"])
        payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("input source id must be text", payload["error"])

    def test_open_blacklist_document_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.txt"
            target.write_text("geheim")
            link = Path(tmp) / "blacklist.txt"
            os.symlink(target, link)
            with mock.patch("speed_of_cinnamon.cli.blacklist_file", return_value=link):
                opened = cli._open_blacklist_document()

        self.assertFalse(opened)

    def test_open_blacklist_document_prepares_file_without_plain_write_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            with (
                mock.patch("speed_of_cinnamon.cli.blacklist_file", return_value=path),
                mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"),
                mock.patch("speed_of_cinnamon.cli._which", return_value="xdg-open"),
                mock.patch("speed_of_cinnamon.cli.subprocess.Popen") as mocked_popen,
                mock.patch.dict("os.environ", {"LD_PRELOAD": "bad", "PYTHONPATH": "/tmp/evil"}, clear=False),
                mock.patch("pathlib.Path.write_text", side_effect=AssertionError("plain write_text used")),
            ):
                opened = cli._open_blacklist_document()

            mode = path.stat().st_mode & 0o777

        self.assertTrue(opened)
        self.assertEqual(mode, 0o600)
        mocked_popen.assert_called_once()
        opener_env = mocked_popen.call_args.kwargs["env"]
        self.assertNotIn("LD_PRELOAD", opener_env)
        self.assertNotIn("PYTHONPATH", opener_env)

    def test_open_blacklist_document_wraps_process_argument_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            with (
                mock.patch("speed_of_cinnamon.cli.blacklist_file", return_value=path),
                mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"),
                mock.patch("speed_of_cinnamon.cli._which", side_effect=["xdg-open", None]),
                mock.patch("speed_of_cinnamon.cli.subprocess.Popen", side_effect=ValueError("invalid process argument")),
            ):
                self.assertFalse(cli._open_blacklist_document())

    def test_models_lists_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["models", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertGreater(len(payload["models"]), 0)
        self.assertEqual(payload["models"][0]["name"], "tiny.en")
        self.assertFalse(payload["models"][0]["downloaded"])
        self.assertTrue(payload["models"][0]["path_present"])
        self.assertNotIn("path", payload["models"][0])
        self.assertNotIn(str(tmp), json.dumps(payload))

    @mock.patch("speed_of_cinnamon.cli.list_models", return_value="invalid")
    def test_models_rejects_non_list_models_payload(self, mocked_models: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=["invalid"])
    def test_models_rejects_invalid_model_entry(self, mocked_models: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload entry must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="hallo welt")
    def test_benchmark_models_reports_runtime_and_text(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            model_path = Path(tmp) / "ggml-tiny.bin"
            self._write_wav(audio, [0, 100, -100])
            model_path.write_bytes(b"model")
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.model_path", return_value=model_path),
                mock.patch("speed_of_cinnamon.cli.model_status", return_value={"downloaded": True}),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "benchmark-models",
                    str(audio),
                    "--language",
                    "de",
                    "--models",
                    "tiny",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["fastest_model"], "tiny")
        self.assertTrue(payload["audio_path_present"])
        self.assertNotIn("audio_path", payload)
        self.assertNotIn(str(audio), json.dumps(payload))
        self.assertEqual(payload["results"][0]["model"], "tiny")
        self.assertTrue(payload["results"][0]["path_present"])
        self.assertNotIn("path", payload["results"][0])
        self.assertEqual(payload["results"][0]["transcript"], "")
        self.assertTrue(payload["results"][0]["transcript_output_redacted"])
        self.assertEqual(payload["results"][0]["characters"], len("hallo welt"))
        self.assertEqual(payload["results"][0]["words"], 2)
        self.assertNotIn("hallo welt", json.dumps(payload))
        self.assertTrue(payload["results"][0]["ok"])
        mocked_transcribe.assert_called_once()
        benchmark_text_path = Path(mocked_transcribe.call_args.kwargs["text_path"])
        self.assertIn(".benchmark-", benchmark_text_path.name)
        self.assertFalse(benchmark_text_path.exists())
        self.assertEqual(mocked_transcribe.call_args.kwargs["backend"], "whisper-cpp")
        self.assertEqual(mocked_transcribe.call_args.kwargs["whisper_model"], str(model_path))

    @mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=RuntimeError("token abc123 leaked"))
    def test_benchmark_models_redacts_transcribe_error(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            model_path = Path(tmp) / "ggml-tiny.bin"
            self._write_wav(audio, [0, 100, -100])
            model_path.write_bytes(b"model")
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.model_path", return_value=model_path),
                mock.patch("speed_of_cinnamon.cli.model_status", return_value={"downloaded": True}),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "benchmark-models",
                    str(audio),
                    "--language",
                    "de",
                    "--models",
                    "tiny",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["results"][0]["ok"])
        self.assertNotIn("abc123", payload["results"][0]["error"])
        self.assertIn("redacted", payload["results"][0]["error"].lower())
        mocked_transcribe.assert_called_once()

    @mock.patch("speed_of_cinnamon.cli._unlink_regular_leaf_with_parent_fsync", side_effect=RuntimeError("cleanup token abc123 leaked"))
    @mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=RuntimeError("transcribe token hidden"))
    def test_benchmark_models_reports_cleanup_failure_before_transcribe_error(
        self,
        mocked_transcribe: mock.Mock,
        mocked_unlink: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            model_path = Path(tmp) / "ggml-tiny.bin"
            self._write_wav(audio, [0, 100, -100])
            model_path.write_bytes(b"model")
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.model_path", return_value=model_path),
                mock.patch("speed_of_cinnamon.cli.model_status", return_value={"downloaded": True}),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "benchmark-models",
                    str(audio),
                    "--language",
                    "de",
                    "--models",
                    "tiny",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertFalse(payload["results"][0]["ok"])
        self.assertIn("redacted", payload["results"][0]["error"].lower())
        self.assertNotIn("abc123", payload["results"][0]["error"])
        self.assertNotIn("hidden", payload["results"][0]["error"])
        mocked_transcribe.assert_called_once()
        mocked_unlink.assert_called_once()

    def test_benchmark_models_reports_missing_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "sample.wav"
            model_path = Path(tmp) / "missing.bin"
            self._write_wav(audio, [0, 100, -100])
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.model_path", return_value=model_path),
                mock.patch("speed_of_cinnamon.cli.model_status", return_value={"downloaded": False}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "benchmark-models",
                    str(audio),
                    "--models",
                    "tiny",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["results"][0]["model"], "tiny")
        self.assertIn("not downloaded", payload["results"][0]["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_lists_local_ollama_models(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": True,
            "models": [{"name": "llama3.2:3b"}],
            "message": "Ollama models loaded",
        }
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            redirect_stdout(stdout),
        ):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "ollama")
        self.assertEqual(payload["url"], "http://localhost:11434")
        self.assertEqual(payload["models"][0]["name"], "llama3.2:3b")
        mocked_list.assert_called_once_with("http://localhost:11434")

    def test_is_local_ollama_url_accepts_local_http_endpoints_without_port(self) -> None:
        self.assertTrue(cli._is_local_ollama_url("http://localhost"))
        self.assertTrue(cli._is_local_ollama_url("http://127.0.0.1"))
        self.assertTrue(cli._is_local_ollama_url("http://[::1]"))

    def test_is_local_ollama_url_rejects_non_local_or_credentialed_urls(self) -> None:
        self.assertFalse(cli._is_local_ollama_url("http://api.example.test:11434"))
        self.assertFalse(cli._is_local_ollama_url("http://localhost:11434@evil.com"))
        self.assertFalse(cli._is_local_ollama_url("http://localhost:11434?token=secret"))

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_reports_missing_local_ollama_command(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": False,
            "models": [],
            "message": "Ollama is not reachable at http://127.0.0.1:11434",
        }
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value=None),
            redirect_stdout(stdout),
        ):
            code = cli.run(["text-models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["available"])
        self.assertIn("Ollama command is not available", payload["message"])
        mocked_list.assert_called_once_with(cli.DEFAULT_OLLAMA_URL)

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_reports_missing_local_ollama_command_when_path_validation_fails(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": False,
            "models": [],
            "message": "Ollama is not reachable at http://127.0.0.1:11434",
        }
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli._command_path", side_effect=RuntimeError("command path is not trusted")),
            redirect_stdout(stdout),
        ):
            code = cli.run(["text-models", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["available"])
        self.assertIn("Ollama command is not available", payload["message"])
        mocked_list.assert_called_once_with(cli.DEFAULT_OLLAMA_URL)

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models")
    def test_text_models_rejects_overlong_ollama_url(self, mocked_list: mock.Mock) -> None:
        long_url = "http://localhost:11434/" + ("x" * (cli.MAX_URL_CHARS + 10))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", long_url, "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama url is too large", payload["error"])
        mocked_list.assert_not_called()

    def test_text_models_rejects_null_ollama_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434\x00", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_text_models_rejects_malformed_ollama_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "https://[::1", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama url is invalid", payload["error"])

    def test_text_models_rejects_remote_plain_http_ollama_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://api.example.test:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama url must use https:// unless host is local loopback", payload["error"])

    def test_text_models_rejects_ollama_url_userinfo_or_query(self) -> None:
        for url, expected in (
            ("https://user:secret@api.example.test:11434", "ollama url must not contain userinfo"),
            ("https://api.example.test:11434?token=secret", "ollama url must not contain query or fragment"),
        ):
            with self.subTest(url=url):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli.run(["text-models", "--ollama-url", url, "--json"])
                payload = json.loads(stdout.getvalue())
                self.assertEqual(code, 1)
                self.assertIn(expected, payload["error"])
                self.assertNotIn("user:secret", json.dumps(payload))
                self.assertNotIn("token=secret", json.dumps(payload))

    def test_install_text_model_pulls_ollama_model(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.run_process_bounded_output", return_value=(0, b"ok", b"")) as mocked_run,
            mock.patch.dict("os.environ", {"LD_PRELOAD": "bad", "PYTHONPATH": "/tmp/evil"}, clear=False),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["model"], "llama3.2:3b")
        mocked_run.assert_called_once()
        self.assertEqual(mocked_run.call_args.args[0], ["/usr/bin/ollama", "pull", "llama3.2:3b"])
        self.assertEqual(mocked_run.call_args.kwargs["env"]["OLLAMA_HOST"], "http://localhost:11434")
        self.assertNotIn("LD_PRELOAD", mocked_run.call_args.kwargs["env"])
        self.assertNotIn("PYTHONPATH", mocked_run.call_args.kwargs["env"])

    def test_install_text_model_rejects_oversized_stdout(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch(
                "speed_of_cinnamon.cli.run_process_bounded_output",
                side_effect=cli.CommandChainError(f"ollama pull command output exceeded {cli.MAX_LOG_EXCERPT_CHARS} bytes"),
            ),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull command output exceeded", payload["error"])

    def test_install_text_model_rejects_oversized_stderr(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch(
                "speed_of_cinnamon.cli.run_process_bounded_output",
                side_effect=cli.CommandChainError(f"ollama pull command output exceeded {cli.MAX_LOG_EXCERPT_CHARS} bytes"),
            ),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull command output exceeded", payload["error"])

    def test_install_text_model_rejects_stdout_utf8_errors(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.run_process_bounded_output", return_value=(0, b"\xff", b"")),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull stdout is not valid UTF-8", payload["error"])

    def test_install_text_model_rejects_stderr_null_bytes(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.run_process_bounded_output", return_value=(0, b"ok", b"bad\x00")),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--ollama-url",
                "http://localhost:11434",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull stderr contains invalid null byte", payload["error"])

    def test_install_text_model_redacts_failed_pull_output(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch(
                "speed_of_cinnamon.cli.run_process_bounded_output",
                return_value=(1, b"", b"failed with Bearer sk-secret-token and https://user:pass@example.test/model"),
            ),
            redirect_stdout(stdout),
        ):
            code = cli.run([
                "install-text-model",
                "--model",
                "llama3.2:3b",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull failed", payload["error"])
        self.assertNotIn("sk-secret-token", payload["error"])
        self.assertNotIn("user:pass", payload["error"])

    def test_install_text_model_rejects_missing_ollama_command(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value=None),
            redirect_stdout(stdout),
        ):
            code = cli.run(["install-text-model", "--model", "llama3.2:3b", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama command is not available", payload["error"])

    def test_install_text_model_rejects_untrusted_command_path(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch(
                "speed_of_cinnamon.cli._command_path",
                side_effect=RuntimeError("command path is not trusted"),
            ),
            redirect_stdout(stdout),
        ):
            code = cli.run(["install-text-model", "--model", "llama3.2:3b", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama command is not available", payload["error"])

    def test_install_text_model_reports_ollama_pull_timeout(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch(
                "speed_of_cinnamon.cli.run_process_bounded_output",
                side_effect=cli.CommandChainError("ollama pull command timed out after 600 seconds"),
            ),
            redirect_stdout(stdout),
        ):
            code = cli.run(["install-text-model", "--model", "llama3.2:3b", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("ollama pull command timed out", payload["error"])

    def test_install_text_model_reports_ollama_pull_oserror(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch("speed_of_cinnamon.cli.shutil.which", return_value="/usr/bin/ollama"),
            mock.patch("speed_of_cinnamon.cli.run_process_bounded_output", side_effect=OSError("boom")),
            redirect_stdout(stdout),
        ):
            code = cli.run(["install-text-model", "--model", "llama3.2:3b", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("failed to run ollama pull: boom", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_rejects_overlong_openai_url(self, mocked_list: mock.Mock) -> None:
        long_url = "http://127.0.0.1:8000/" + ("x" * (cli.MAX_URL_CHARS + 10))
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                long_url,
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url is too large", payload["error"])
        mocked_list.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_rejects_overlong_openai_api_key(self, mocked_list: mock.Mock) -> None:
        long_key = "x" * (cli.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1)
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-api-key",
                long_key,
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible API key is too large", payload["error"])
        mocked_list.assert_not_called()

    def test_text_models_rejects_null_openai_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000\x00",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_rejects_control_character_openai_url(self, mocked_list: mock.Mock) -> None:
        for url in (
            "http://127.0.0.1:8000/v1\x85",
            "http://127.0.0.1:8000/v1\\x85",
            "http://127.0.0.1:8000/v1\\u0085",
        ):
            with self.subTest(url=repr(url)):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli.run([
                        "text-models",
                        "--backend",
                        "openai-compatible",
                        "--openai-compatible-url",
                        url,
                        "--json",
                    ])
                payload = json.loads(stdout.getvalue())
                self.assertEqual(code, 1)
                self.assertIn("contains invalid control character", payload["error"])
        mocked_list.assert_not_called()

    def test_text_models_rejects_non_http_openai_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "ftp://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must use http:// or https://", payload["error"])

    def test_text_models_rejects_malformed_openai_url(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "https://[::1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url is invalid", payload["error"])

    def test_text_models_rejects_openai_url_userinfo(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "https://user:secret@api.example.test/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must not contain userinfo", payload["error"])
        self.assertNotIn("user:secret", json.dumps(payload))

    def test_text_models_rejects_openai_url_query_or_fragment(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "https://api.example.test/v1?api_key=secret#token",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must not contain query or fragment", payload["error"])
        self.assertNotIn("api_key=secret", json.dumps(payload))

    def test_text_model_url_validators_reject_empty_userinfo(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ollama url must not contain userinfo"):
            cli._validate_ollama_http_url("http://@127.0.0.1:11434", field_name="ollama url")
        with self.assertRaisesRegex(RuntimeError, "openai-compatible url must not contain userinfo"):
            cli._validate_openai_compatible_http_url("https://@api.example.test/v1", "openai-compatible url")

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_rejects_remote_plain_http_openai_url(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://api.example.test/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must use https:// unless host is local loopback", payload["error"])
        mocked_list.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models")
    def test_text_models_lists_openai_compatible_models(self, mocked_list: mock.Mock) -> None:
        mocked_list.return_value = {
            "available": True,
            "models": [{"name": "local-llama"}],
            "message": "OpenAI-compatible models loaded",
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--openai-compatible-api-key",
                "secret",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["backend"], "openai-compatible")
        self.assertEqual(payload["url"], "http://127.0.0.1:8000/v1")
        self.assertEqual(payload["models"][0]["name"], "local-llama")
        mocked_list.assert_called_once_with("http://127.0.0.1:8000/v1", api_key="secret")

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value="invalid")
    def test_text_models_rejects_non_object_ollama_payload(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": "yes",
        "models": [{"name": "llama3.2:3b"}],
        "message": "Ollama models loaded",
    })
    def test_text_models_rejects_ollama_invalid_available(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload available must be a boolean", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": "invalid",
        "message": "Ollama models loaded",
    })
    def test_text_models_rejects_ollama_invalid_models(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"detail": 1}],
        "message": "Ollama models loaded",
    })
    def test_text_models_rejects_ollama_invalid_model_entry(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model name must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2:3b"}],
        "message": 123,
    })
    def test_text_models_rejects_ollama_invalid_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2:3b"}],
        "message": "contains\x00",
    })
    def test_text_models_rejects_ollama_invalid_message_bytes(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2\x85:3b"}],
        "message": "",
    })
    def test_text_models_rejects_ollama_control_model_name(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model name contains invalid control character", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_ollama_models", return_value={
        "available": True,
        "models": [{"name": "llama3.2:3b"}],
    })
    def test_text_models_rejects_ollama_missing_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["text-models", "--ollama-url", "http://localhost:11434", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value="invalid")
    def test_text_models_rejects_non_object_openai_payload(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": ["invalid"],
        "message": "OpenAI-compatible models loaded",
    })
    def test_text_models_rejects_openai_invalid_model_entry(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload entry must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": "yes",
        "models": [{"name": "local-llama"}],
        "message": "OpenAI-compatible models loaded",
    })
    def test_text_models_rejects_openai_invalid_available(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload available must be a boolean", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": "invalid",
        "message": "OpenAI-compatible models loaded",
    })
    def test_text_models_rejects_openai_invalid_models(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
        "message": "\u0000",
    })
    def test_text_models_rejects_openai_invalid_message_bytes(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
        ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
        "message": "contains\\x85",
    })
    def test_text_models_rejects_openai_escaped_control_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message contains invalid control character", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
        "message": True,
    })
    def test_text_models_rejects_openai_invalid_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_openai_compatible_models", return_value={
        "available": True,
        "models": [{"name": "local-llama"}],
    })
    def test_text_models_rejects_openai_missing_message(self, mocked_list: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "text-models",
                "--backend",
                "openai-compatible",
                "--openai-compatible-url",
                "http://127.0.0.1:8000/v1",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("text models payload message must be text", payload["error"])

    def test_text_models_rejects_invalid_backend(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text models backend must be ollama or openai-compatible"):
            cli.command_text_models(argparse.Namespace(
                backend="openai",
                ollama_url="http://localhost:11434",
                openai_compatible_url="http://127.0.0.1:8000/v1",
            ))

    def test_text_models_rejects_control_character_backend(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text models backend contains invalid control character"):
            cli.command_text_models(argparse.Namespace(
                backend="\x85ollama",
                ollama_url="http://localhost:11434",
                openai_compatible_url="http://127.0.0.1:8000/v1",
            ))

    def test_install_text_model_rejects_control_character_backend(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text model backend contains invalid control character"):
            cli.command_install_text_model(argparse.Namespace(
                backend="\\x85ollama",
                model="llama3.2:3b",
            ))

    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_setup_command_outputs_copyable_plan(self, mocked_doctor: mock.Mock) -> None:
        mocked_doctor.return_value = {
            "ok": False,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {
                    "ok": False,
                    "value": "auto",
                    "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
                },
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["setup", "--applet", "--settings-json", '{"transcriber":"auto"}', "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["ready"])
        self.assertIn("Speed of Cinnamon setup plan", payload["text"])
        self.assertEqual(payload["steps"][0]["id"], "asr-backend")
        mocked_doctor.assert_called_once()

    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_setup_command_accepts_settings_json_from_stdin(self, mocked_doctor: mock.Mock) -> None:
        mocked_doctor.return_value = {
            "ok": True,
            "configured": {
                "recorder": {"ok": True},
                "transcriber": {"ok": True},
                "output": {"ok": True},
                "postprocessor": {"ok": True},
                "warnings": [],
            },
            "desktop": {"cinnamon": True},
        }
        stdout = io.StringIO()
        stdin = io.StringIO('{"transcriber":"auto"}')
        with mock.patch("sys.stdin", stdin), redirect_stdout(stdout):
            code = cli.run(["setup", "--applet", "--settings-json-stdin", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        mocked_doctor.assert_called_once_with({"transcriber": "auto"}, applet=True)

    def test_doctor_rejects_settings_json_from_argv_and_stdin(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO('{"language":"de"}')), redirect_stdout(stdout):
            code = cli.run(["doctor", "--settings-json", '{"language":"en"}', "--settings-json-stdin", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("either --settings-json or stdin", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_doctor_command_rejects_non_boolean_applet(self, mocked_doctor: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            cli.command_doctor(argparse.Namespace(settings_json="{}", applet="yes"))
        mocked_doctor.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.build_setup_plan")
    @mock.patch("speed_of_cinnamon.cli.doctor_report")
    def test_setup_command_rejects_non_boolean_applet(self, mocked_doctor: mock.Mock, mocked_setup_plan: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
            cli.command_setup(argparse.Namespace(settings_json="{}", applet="yes"))
        mocked_doctor.assert_not_called()
        mocked_setup_plan.assert_not_called()

    def test_alarms_check_rejects_non_boolean_mark(self) -> None:
        with mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"):
            with self.assertRaisesRegex(RuntimeError, "mark must be a boolean"):
                cli.command_alarms_check(argparse.Namespace(catch_up_minutes=15, mark="true"))

    @mock.patch("speed_of_cinnamon.cli.add_alarm")
    def test_alarms_add_rejects_non_boolean_disabled(self, mocked_add_alarm: mock.Mock) -> None:
        with mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"):
            with self.assertRaisesRegex(RuntimeError, "disabled must be a boolean"):
                cli.command_alarms_add(argparse.Namespace(time="09:00", name="", days="daily", urgency="normal", disabled="yes"))
            mocked_add_alarm.assert_not_called()

    def test_coerce_bool_rejects_non_boolean_values(self) -> None:
        self.assertTrue(cli._coerce_bool(True, field_name="flag"))
        self.assertFalse(cli._coerce_bool(False, field_name="flag"))
        with self.assertRaisesRegex(RuntimeError, "flag must be a boolean"):
            cli._coerce_bool("true", field_name="flag")
        with self.assertRaisesRegex(RuntimeError, "flag must be a boolean"):
            cli._coerce_bool(1, field_name="flag")

    @mock.patch("speed_of_cinnamon.cli.remove_model")
    def test_remove_model_command(self, mocked_remove: mock.Mock) -> None:
        mocked_remove.return_value = {"status": "done", "removed": True, "name": "tiny.en", "path": "/tmp/private-model"}
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["remove-model", "tiny.en", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["removed"])
        self.assertTrue(payload["path_present"])
        self.assertNotIn("path", payload)
        self.assertNotIn("/tmp/private-model", json.dumps(payload))
        mocked_remove.assert_called_once_with("tiny.en")

    @mock.patch("speed_of_cinnamon.cli.download_model")
    def test_download_model_command_redacts_model_path(self, mocked_download: mock.Mock) -> None:
        mocked_download.return_value = {"status": "done", "downloaded": True, "name": "tiny.en", "path": "/tmp/private-model"}
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["download-model", "tiny.en", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["downloaded"])
        self.assertTrue(payload["path_present"])
        self.assertNotIn("path", payload)
        self.assertNotIn("/tmp/private-model", json.dumps(payload))
        mocked_download.assert_called_once_with("tiny.en", False)

    @mock.patch("speed_of_cinnamon.cli.download_model")
    def test_download_model_command_rejects_non_boolean_force(self, mocked_download: mock.Mock) -> None:
        with mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"):
            with self.assertRaisesRegex(RuntimeError, "force must be a boolean"):
                cli.command_download_model(argparse.Namespace(model="tiny.en", force="yes"))
            mocked_download.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.insert_text")
    def test_insert_text_command_rejects_non_boolean_sanitize_special_chars(self, mocked_insert: mock.Mock) -> None:
        with self.assertRaisesRegex(RuntimeError, "sanitize_special_chars must be a boolean"):
            cli.command_insert_text(argparse.Namespace(
                text="Hello",
                insert_method="none",
                typing_delay_ms=0,
                append_space=False,
                sanitize_special_chars="yes",
            ))
        mocked_insert.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.insert_text")
    def test_insert_text_command_appends_space_after_sanitizing(self, mocked_insert: mock.Mock) -> None:
        mocked_insert.return_value = True
        cli.command_insert_text(argparse.Namespace(
            text="Grüße",
            insert_method="clipboard",
            typing_delay_ms=8,
            append_space=True,
            sanitize_special_chars=True,
        ))
        mocked_insert.assert_called_once_with("Grusse ", "clipboard", 8)

    @mock.patch("speed_of_cinnamon.cli.insert_text", side_effect=RuntimeError("failed to commit clipboard-paste insertion state"))
    def test_insert_text_command_reports_clipboard_paste_commit_failure(self, mocked_insert: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "insert-text",
                "hello",
                "--insert-method",
                "clipboard-paste",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("failed to commit clipboard-paste insertion state", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.insert_text", side_effect=RuntimeError("failed to commit clipboard insertion state"))
    def test_insert_text_command_reports_clipboard_commit_failure(self, mocked_insert: mock.Mock) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run([
                "insert-text",
                "hello",
                "--insert-method",
                "clipboard",
                "--json",
            ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("failed to commit clipboard insertion state", payload["error"])

    def test_settings_export_import_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "settings.json"
            stdout = io.StringIO()
            with (
                mock.patch("sys.stdin", io.StringIO(json.dumps({
                    "language": "de",
                    "auto-transcribe-timeout": False,
                    "notify-complete": False,
                    "sanitize-special-chars": True,
                    "cli-path": "/tmp/local",
                }))),
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                add_alarm("09:00", name="Standup", days="weekdays")
                code = cli.run([
                    "settings-export",
                    "--settings-json-stdin",
                    "--output",
                    str(export_path),
                    "--json",
                ])
            export_payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                save_alarm_store({"alarms": [], "last_checked_at": ""})
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                import_code = cli.run([
                    "settings-import",
                    "--input",
                    str(export_path),
                    "--confirm-plaintext-settings-output",
                    "--json",
                ])
            import_payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                alarms = list_alarm_payload()
        self.assertEqual(code, 0)
        self.assertEqual(import_code, 0)
        self.assertTrue(export_payload["path_present"])
        self.assertNotIn("path", export_payload)
        self.assertNotIn(str(export_path), json.dumps(export_payload))
        self.assertEqual(export_payload["message"], "settings exported")
        self.assertNotIn(str(export_path), export_payload["message"])
        self.assertEqual(export_payload["alarms_count"], 1)
        self.assertEqual(import_payload["message"], "settings imported")
        self.assertTrue(import_payload["path_present"])
        self.assertNotIn("path", import_payload)
        self.assertNotIn(str(export_path), json.dumps(import_payload))
        self.assertNotIn(str(export_path), import_payload["message"])
        self.assertEqual(import_payload["alarms_count"], 1)
        self.assertEqual(import_payload["settings"]["language"], "de")
        self.assertFalse(import_payload["settings"]["auto-transcribe-timeout"])
        self.assertFalse(import_payload["settings"]["notify-complete"])
        self.assertTrue(import_payload["settings"]["sanitize-special-chars"])
        self.assertNotIn("cli-path", import_payload["settings"])
        self.assertEqual(alarms["alarms"][0]["name"], "Standup")

    def test_settings_export_import_supports_home_tilde_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            home.mkdir()
            home.chmod(0o700)
            export_path = home / "settings.json"
            stdout = io.StringIO()
            with (
                mock.patch("sys.stdin", io.StringIO(json.dumps({"language": "en"}))),
                mock.patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                export_code = cli.run([
                    "settings-export",
                    "--settings-json-stdin",
                    "--output",
                    "~/settings.json",
                    "--json",
                ])
            export_payload = json.loads(stdout.getvalue())
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"HOME": str(home), "XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                import_code = cli.run([
                    "settings-import",
                    "--input",
                    "~/settings.json",
                    "--confirm-plaintext-settings-output",
                    "--json",
                ])
            import_payload = json.loads(stdout.getvalue())
            self.assertTrue(export_path.exists())

        self.assertEqual(export_code, 0)
        self.assertEqual(import_code, 0)
        self.assertEqual(export_payload["message"], "settings exported")
        self.assertEqual(import_payload["message"], "settings imported")
        self.assertEqual(import_payload["settings"]["language"], "en")

    def test_settings_export_accepts_settings_json_from_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "settings.json"
            stdout = io.StringIO()
            stdin = io.StringIO(json.dumps({
                "language": "de",
                "personal-context": "private project context",
                "transcriber-command": "custom-asr --token sk-secret",
            }))
            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
                mock.patch("sys.stdin", stdin),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "settings-export",
                    "--settings-json-stdin",
                    "--output",
                    str(export_path),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            exported = json.loads(export_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(exported["settings"]["language"], "de")
        self.assertEqual(exported["settings"]["personal-context"], "private project context")
        self.assertNotIn("transcriber-command", exported["settings"])
        self.assertNotIn("sk-secret", json.dumps(exported, sort_keys=True))

    def test_settings_import_redacts_settings_without_plaintext_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "settings.json"
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                export_code = cli.run([
                    "settings-export",
                    "--settings-json",
                    json.dumps({
                        "language": "de",
                        "personal-context": "hidden-context-token",
                        "vocabulary": "hidden-vocabulary-token",
                    }),
                    "--output",
                    str(export_path),
                    "--json",
                ])
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                import_code = cli.run(["settings-import", "--input", str(export_path), "--json"])
            payload = json.loads(stdout.getvalue())
            encoded = json.dumps(payload, sort_keys=True)

        self.assertEqual(export_code, 0)
        self.assertEqual(import_code, 0)
        self.assertTrue(payload["settings_redacted"])
        self.assertNotIn("settings", payload)
        self.assertNotIn("hidden-context-token", encoded)
        self.assertNotIn("hidden-vocabulary-token", encoded)

    def test_settings_export_rejects_settings_json_from_argv_and_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            export_path = Path(tmp) / "settings.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}),
                mock.patch("sys.stdin", io.StringIO('{"language":"de"}')),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--settings-json-stdin",
                    "--output",
                    str(export_path),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 1)
        self.assertIn("either --settings-json or stdin", payload["error"])

    def test_history_lists_recent_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            older = transcript_dir / "older.txt"
            newer = transcript_dir / "newer.txt"
            older.write_text("older text\n", encoding="utf-8")
            newer.write_text("newer text with more words\n", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)
        self.assertEqual(payload["transcripts"][0]["path"], cli.HISTORY_METADATA_REDACTED_TEXT)
        self.assertEqual(payload["transcripts"][0]["preview"], cli.HISTORY_PREVIEW_REDACTED_TEXT)
        self.assertNotIn("modified_at", payload["transcripts"][0])
        self.assertNotIn("newer.txt", json.dumps(payload, sort_keys=True))
        self.assertNotIn("text", payload["transcripts"][0])

    def test_history_plaintext_previews_require_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            older = transcript_dir / "older.txt"
            newer = transcript_dir / "newer.txt"
            older.write_text("older text\n", encoding="utf-8")
            newer.write_text("newer text with more words\n", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], "newer.txt")
        self.assertIn("newer.txt", payload["transcripts"][0]["path"])
        self.assertEqual(payload["transcripts"][0]["preview"], "newer text with more words")

    def test_transcripts_document_contains_full_transcript_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            older = transcript_dir / "older.txt"
            newer = transcript_dir / "newer.txt"
            hidden_temp = transcript_dir / ".newer.abcd.tmp.txt"
            older_text = "older line one\nolder line two\n"
            newer_text = "newer line one\nnewer line two\nnewer line three\n"
            older.write_text(older_text, encoding="utf-8")
            newer.write_text(newer_text, encoding="utf-8")
            hidden_temp.write_text("temporary plaintext leak\n", encoding="utf-8")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            os.utime(hidden_temp, (300, 300))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
            document = payload["content"]
            legacy_document_path = Path(tmp) / "speed-of-cinnamon" / "all-transcripts.txt"
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcripts"], 2)
        self.assertFalse(payload["truncated"])
        self.assertNotIn("path", payload)
        self.assertFalse(legacy_document_path.exists())
        self.assertIn("===== newer.txt =====", document)
        self.assertIn("===== older.txt =====", document)
        self.assertIn(newer_text.strip(), document)
        self.assertIn(older_text.strip(), document)
        self.assertNotIn("temporary plaintext leak", document)
        self.assertNotIn("Path:", document)
        self.assertNotIn(str(transcript_dir), document)
        self.assertLess(document.index("===== newer.txt ====="), document.index("===== older.txt ====="))

    def test_transcripts_document_escapes_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "ansi.txt").write_text("\x1b[31mALERT\x1b[0m\nsafe\n", encoding="utf-8")
            (transcript_dir / "bad\nname\u2028.txt").write_text("named transcript\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
            document = payload["content"]
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", document)
        self.assertIn("\\u001b[31mALERT\\u001b[0m", document)
        self.assertNotIn("bad\nname\u2028.txt", document)
        self.assertIn("bad\\u000aname\\u2028.txt", document)
        self.assertIn("safe", document)

    def test_history_sanitizes_transcript_file_names_when_plaintext_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "bad\nname\u2029.txt").write_text("named transcript\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["transcripts"][0]["name"], "bad\\u000aname\\u2029.txt")

    def test_transcripts_document_requires_plaintext_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "sample.txt").write_text("sensitive text\n", encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.build_transcripts_document") as mocked_build_document,
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-document", "--limit", "1000", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertNotEqual(code, 0)
        self.assertIn("requires --confirm-plaintext", payload["error"])
        self.assertNotIn("content", payload)
        mocked_build_document.assert_not_called()

    def test_transcripts_document_truncates_before_spawn_json_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            for index in range(8):
                path = transcript_dir / f"transcript-{index}.txt"
                path.write_text(("chunk-%d " % index) + ("x" * 160), encoding="utf-8")
                os.utime(path, (200 + index, 200 + index))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.MAX_TRANSCRIPTS_DOCUMENT_CHARS", 700),
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(payload["truncated"])
        self.assertLess(payload["transcripts"], 8)
        self.assertIn("transcript list truncated", payload["content"])

    def test_transcripts_document_respects_json_output_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "emoji.txt").write_text("\U0001f600" * 500, encoding="utf-8")
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.MAX_TRANSCRIPTS_DOCUMENT_CHARS", 4000),
                mock.patch("speed_of_cinnamon.cli.MAX_TRANSCRIPTS_DOCUMENT_JSON_BYTES", 2500),
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"])
            rendered = stdout.getvalue()
            payload = json.loads(rendered)
        self.assertEqual(code, 0)
        self.assertTrue(payload["truncated"])
        self.assertLessEqual(len(rendered.encode("utf-8")), 2500)

    def test_transcripts_document_fails_closed_on_unreadable_socenc_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "safe.txt").write_text("safe text\n", encoding="utf-8")
            (transcript_dir / f"unsafe{cli.ENCRYPTED_TRANSCRIPT_SUFFIX}").write_text("leaked text\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertNotEqual(code, 0)
        self.assertIn("failed to read transcript", payload["error"])
        self.assertNotIn("content", payload)

    def test_transcripts_document_fails_closed_on_invalid_utf8_plaintext(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "invalid.txt").write_bytes(b"\xff")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertNotEqual(code, 0)
        self.assertIn("failed to read transcript", payload["error"])
        self.assertIn("not valid UTF-8", payload["error"])
        self.assertNotIn("content", payload)

    def test_transcripts_export_rejects_unsafe_modes_before_building_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.build_transcripts_document") as build_document,
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-export", "--artifact-encryption", "off", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertNotEqual(code, 0)
        self.assertIn("requires keyring or passphrase", payload["error"])
        build_document.assert_not_called()

    def test_transcripts_export_writes_encrypted_bundle_explicitly(self) -> None:
        strong_passphrase = artifact_crypto._b64encode(bytes(range(32)))
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / "newer.txt"
            transcript_text = "private transcript export line\n"
            transcript.write_text(transcript_text, encoding="utf-8")
            stale_export = Path(tmp) / "speed-of-cinnamon" / "exports" / "all-transcripts-fixed.txt"
            stale_export.parent.mkdir(parents=True)
            stale_export.write_text("stale plaintext export\n", encoding="utf-8")
            stdout = io.StringIO()
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: strong_passphrase,
                artifact_crypto.PASSPHRASE_FILE_ENV: "",
            }
            with (
                mock.patch.dict(os.environ, env),
                mock.patch("speed_of_cinnamon.cli._transcript_export_path", return_value=stale_export),
                mock.patch(
                    "speed_of_cinnamon.cli._unlink_regular_leaf_with_parent_fsync",
                    wraps=cli._unlink_regular_leaf_with_parent_fsync,
                ) as unlink_leaf,
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-export", "--limit", "1000", "--artifact-encryption", "passphrase", "--json"])
            payload = json.loads(stdout.getvalue())
            export_path = Path(payload["path"])
            encrypted_payload = export_path.read_bytes()
            with mock.patch.dict(os.environ, env):
                decrypted = artifact_crypto.read_decrypted_bytes_from_file(export_path, kind="transcript", field_name="test export").decode("utf-8")
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcripts"], 1)
        self.assertEqual(payload["encryption"], "passphrase")
        self.assertTrue(payload["encrypted"])
        self.assertFalse(payload["plaintext"])
        self.assertTrue(export_path.name.startswith("all-transcripts-"))
        self.assertTrue(export_path.name.endswith(".txt.socenc"))
        self.assertFalse(stale_export.exists())
        unlink_leaf.assert_any_call(stale_export, field_name="transcript export")
        self.assertNotIn(transcript_text.encode("utf-8"), encrypted_payload)
        self.assertIn(transcript_text.strip(), decrypted)
        self.assertNotIn(str(transcript_dir), decrypted)

    def test_plaintext_transcripts_export_escapes_control_characters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "ansi.txt").write_text("\x1b[31mALERT\x1b[0m\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["transcripts-export", "--plaintext", "--confirm-plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
            export_text = Path(payload["path"]).read_text(encoding="utf-8")
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", export_text)
        self.assertIn("\\u001b[31mALERT\\u001b[0m", export_text)

    def test_transcripts_export_redacts_unreadable_transcript_name_without_plaintext_confirmation(self) -> None:
        strong_passphrase = artifact_crypto._b64encode(bytes(range(32)))
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "customer-secret-name.txt.socenc").write_bytes(b"not a valid encrypted transcript")
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {
                    "XDG_STATE_HOME": tmp,
                    artifact_crypto.PASSPHRASE_ENV: strong_passphrase,
                    artifact_crypto.PASSPHRASE_FILE_ENV: "",
                }),
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-export", "--artifact-encryption", "passphrase", "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertNotEqual(code, 0)
        self.assertIn(cli.HISTORY_METADATA_REDACTED_TEXT, payload["error"])
        self.assertNotIn("customer-secret-name", payload["error"])

    def test_read_stored_encrypted_transcript_rejects_decrypted_payload_over_plaintext_cap(self) -> None:
        oversized = b"x" * (cli.MAX_STORED_TRANSCRIPT_BYTES + 1)
        with mock.patch("speed_of_cinnamon.cli.read_decrypted_bytes_from_file", return_value=oversized):
            with self.assertRaisesRegex(RuntimeError, "transcript file is too large"):
                cli._read_stored_transcript_text(Path("/tmp/oversized.txt.socenc"))

    def test_transcripts_export_rolls_back_encrypted_bundle_when_plaintext_cleanup_fails(self) -> None:
        strong_passphrase = artifact_crypto._b64encode(bytes(range(32)))
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / "newer.txt"
            transcript.write_text("private transcript export line\n", encoding="utf-8")
            stale_export = Path(tmp) / "speed-of-cinnamon" / "exports" / "all-transcripts-fixed.txt"
            stale_export.parent.mkdir(parents=True)
            stale_export.write_text("stale plaintext export\n", encoding="utf-8")
            encrypted_export = Path(f"{stale_export}.socenc")
            stdout = io.StringIO()
            env = {
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: strong_passphrase,
                artifact_crypto.PASSPHRASE_FILE_ENV: "",
            }
            real_unlink = cli._unlink_regular_leaf_with_parent_fsync

            def fail_plaintext_cleanup(path: Path, *args: object, **kwargs: object) -> bool:
                if path == stale_export:
                    raise RuntimeError("blocked plaintext cleanup")
                return real_unlink(path, *args, **kwargs)

            with (
                mock.patch.dict(os.environ, env),
                mock.patch("speed_of_cinnamon.cli._transcript_export_path", return_value=stale_export),
                mock.patch("speed_of_cinnamon.cli._unlink_regular_leaf_with_parent_fsync", side_effect=fail_plaintext_cleanup),
                redirect_stdout(stdout),
            ):
                code = cli.run(["transcripts-export", "--limit", "1000", "--artifact-encryption", "passphrase", "--json"])
            payload = json.loads(stdout.getvalue())
            plaintext_exists = stale_export.exists()
            encrypted_exists = encrypted_export.exists()

        self.assertNotEqual(code, 0)
        self.assertIn("failed to remove plaintext transcript export", payload["error"])
        self.assertTrue(plaintext_exists)
        self.assertFalse(encrypted_exists)

    def test_plaintext_transcripts_export_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["transcripts-export", "--plaintext", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertNotEqual(code, 0)
        self.assertIn("confirm-plaintext", payload["error"])

    def test_history_skips_empty_transcripts_when_filling_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            newest_empty = transcript_dir / "newest-empty.txt"
            older = transcript_dir / "older.txt"
            middle = transcript_dir / "middle.txt"
            newest_empty.write_text("\n", encoding="utf-8")
            older.write_text("older\n", encoding="utf-8")
            middle.write_text("middle text\n", encoding="utf-8")
            os.utime(newest_empty, (300, 300))
            os.utime(older, (100, 100))
            os.utime(middle, (200, 200))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "2", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 2)
        self.assertEqual(payload["transcripts"][0]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)
        self.assertEqual(payload["transcripts"][1]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)

    def test_history_reports_corrupt_transcripts_when_filling_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            corrupt = transcript_dir / "corrupt.txt"
            valid = transcript_dir / "valid.txt"
            corrupt.write_bytes(b"\xff")
            valid.write_text("valid transcript\n", encoding="utf-8")
            os.utime(corrupt, (300, 300))
            os.utime(valid, (200, 200))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)
        self.assertEqual(payload["unreadable_count"], 1)

    def test_history_limit_zero_returns_no_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "entry.txt").write_text("text\n", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "0", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcripts"], [])

    def test_history_limits_text_read_to_prevent_large_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            (transcript_dir / "huge.txt").write_text("x" * 5000, encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)
        self.assertNotIn("text", payload["transcripts"][0])
        self.assertLessEqual(len(payload["transcripts"][0]["preview"]), 80)

    def test_history_skips_symlinked_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            real = transcript_dir / "real.txt"
            real.write_text("real transcript\n", encoding="utf-8")
            symlink = transcript_dir / "link.txt"
            symlink.symlink_to(real)
            os.utime(real, (100, 100))
            os.utime(symlink, (200, 200), follow_symlinks=False)
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "5", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["transcripts"]), 1)
        self.assertEqual(payload["transcripts"][0]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)

    def test_history_ignores_symlinked_transcript_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "secret.txt").write_text("secret dictated words\n", encoding="utf-8")
            link = root / "transcripts"
            link.symlink_to(outside, target_is_directory=True)

            with mock.patch("speed_of_cinnamon.cli.transcript_dir", return_value=link):
                entries = cli.read_transcript_history(5)

        self.assertEqual(entries, [])

    def test_history_counts_unscannable_transcript_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing-transcripts"
            with mock.patch("speed_of_cinnamon.cli.transcript_dir", return_value=missing):
                entries, unreadable_count = cli._collect_transcript_history(5)

        self.assertEqual(entries, [])
        self.assertEqual(unreadable_count, 1)

    def test_history_candidates_skip_unsafe_transcript_entries_before_reading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            valid = transcript_dir / "valid.txt"
            valid.write_text("valid\n", encoding="utf-8")
            symlink = transcript_dir / "link.txt"
            symlink.symlink_to(valid)
            directory = transcript_dir / "directory.txt"
            directory.mkdir()
            hardlinked = False
            hardlink_source = transcript_dir / "hardlink-source.txt"
            hardlink_source.write_text("source\n", encoding="utf-8")
            hardlink = transcript_dir / "hardlink.txt"
            try:
                os.link(hardlink_source, hardlink)
                hardlinked = True
            except OSError:
                hardlink_source.unlink()
            if hasattr(os, "mkfifo"):
                fifo = transcript_dir / "fifo.txt"
                try:
                    os.mkfifo(fifo)
                except OSError:
                    pass

            candidates = list(cli._transcript_history_candidates(transcript_dir))

        candidate_names = {path.name for _mtime, path in candidates}
        self.assertEqual(candidate_names, {"valid.txt"})
        if hardlinked:
            self.assertNotIn("hardlink-source.txt", candidate_names)
            self.assertNotIn("hardlink.txt", candidate_names)

    def test_history_rejects_negative_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", "-1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("history limit must be at least 0", payload["error"])

    def test_history_rejects_excessive_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["history", "--limit", str(cli.MAX_HISTORY_LIMIT + 1), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("history limit must be at most", payload["error"])

    def test_cleanup_prunes_old_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            older = transcript_dir / "older.txt"
            middle = transcript_dir / "middle.txt"
            newer = transcript_dir / "newer.txt"
            for path, mtime in [(older, 100), (middle, 200), (newer, 300)]:
                path.write_text(path.stem, encoding="utf-8")
                os.utime(path, (mtime, mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "2", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            older_exists = older.exists()
            middle_exists = middle.exists()
            newer_exists = newer.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_transcripts"], 1)
        self.assertFalse(older_exists)
        self.assertTrue(middle_exists)
        self.assertTrue(newer_exists)

    def test_cleanup_deletes_stale_transient_transcripts_without_touching_fresh_ones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            stale = transcript_dir / ".old.abcd.tmp.txt"
            fresh = transcript_dir / ".fresh.abcd.tmp.txt"
            stale_owner = cli._transient_transcript_owner_path(stale)
            stale.write_text("old plaintext\n", encoding="utf-8")
            fresh.write_text("fresh plaintext\n", encoding="utf-8")
            stale_owner.write_text("999999999\n", encoding="utf-8")
            old_mtime = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            os.utime(stale, (old_mtime, old_mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            stale_exists = stale.exists()
            stale_owner_exists = stale_owner.exists()
            fresh_exists = fresh.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_transient_transcripts"], 1)
        self.assertEqual(payload["deleted_path_count"], 1)
        self.assertEqual(payload["deleted_paths"], [])
        self.assertNotIn(str(stale), json.dumps(payload))
        self.assertFalse(stale_exists)
        self.assertFalse(stale_owner_exists)
        self.assertTrue(fresh_exists)

    def test_cleanup_reports_unsafe_transient_transcript_owner_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcript_dir = tmp_path / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            stale = transcript_dir / ".old.abcd.tmp.txt"
            stale.write_text("old plaintext\n", encoding="utf-8")
            owner = cli._transient_transcript_owner_path(stale)
            target = tmp_path / "owner-target"
            target.write_text("foreign owner\n", encoding="utf-8")
            owner.symlink_to(target)
            old_mtime = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            os.utime(stale, (old_mtime, old_mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            stale_exists = stale.exists()
            owner_is_symlink = owner.is_symlink()
            target_exists = target.exists()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("failed to scan or delete 1 file", payload["error"])
        self.assertEqual(payload["deleted_transient_transcripts"], 1)
        self.assertFalse(stale_exists)
        self.assertEqual(payload["failed_path_count"], 1)
        self.assertEqual(payload["failed_paths"], [])
        self.assertNotIn(str(owner), json.dumps(payload))
        self.assertTrue(owner_is_symlink)
        self.assertTrue(target_exists)

    def test_cleanup_resolves_relative_transcript_paths_against_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            transcript_dir = state_dir / "transcripts"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            transcript_dir.mkdir(parents=True)
            transcript = transcript_dir / "stale.txt"
            transcript.write_text("secret", encoding="utf-8")
            state_file = state_dir / "state.json"
            StateStore(state_file).write(
                RecordingState(
                    status="finalizing",
                    transcript_path="transcripts/stale.txt",
                )
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertTrue(transcript.exists())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "done")

    def test_cleanup_keeps_relative_recording_artifacts_under_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            audio = recordings_dir / "active.wav"
            log = recordings_dir / "active.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            old = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            os.utime(audio, (old, old))
            os.utime(log, (old, old))
            state_file = state_dir / "state.json"
            StateStore(state_file).write(
                RecordingState(
                    status="finalizing",
                    audio_path="recordings/active.wav",
                    log_path="recordings/active.log",
                )
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertTrue(audio.exists())
            self.assertTrue(log.exists())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["deleted_recordings"], 0)
            self.assertEqual(payload["deleted_logs"], 0)

    def test_cleanup_keeps_inflight_trimmed_artifact_for_relative_state_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            trimmed = recordings_dir / "active.trimmed-123.wav"
            trimmed.write_bytes(b"old")
            old = time.time() - 86400
            os.utime(trimmed, (old, old))
            state_file = state_dir / "state.json"
            StateStore(state_file).write(RecordingState(status="finalizing", audio_path="recordings/active.wav"))
            lock = cli._acquire_finalization_lock(state_file)
            self.assertIsNotNone(lock)
            stdout = io.StringIO()
            try:
                with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                    code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            finally:
                cli._release_finalization_lock(lock)
            payload = json.loads(stdout.getvalue())
            self.assertTrue(trimmed.exists())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["deleted_recordings"], 0)
            self.assertGreaterEqual(payload["skipped_active_path_count"], 1)

    def test_recording_artifact_cap_keeps_relative_state_artifact_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            current = recordings_dir / "current.wav"
            old = recordings_dir / "old.wav"
            current.write_bytes(b"current")
            old.write_bytes(b"old")
            state_file = state_dir / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", audio_path="recordings/current.wav", log_path="recordings/current.log"))
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), mock.patch.object(cli, "MAX_TEMP_RECORDING_FILES", 0):
                result = cli._enforce_recording_artifact_cap(store.read(), state_path=state_file)
            self.assertTrue(current.exists())
            self.assertFalse(old.exists())
            self.assertIn(str(current), result["skipped_active_paths"])

    def test_stop_finalizes_relative_recording_artifacts_under_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            audio = recordings_dir / "active.wav"
            log = recordings_dir / "active.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = state_dir / "state.json"
            StateStore(state_file).write(
                RecordingState(
                    status="recorded",
                    pid=1234,
                    process_identity="stale-process-identity",
                    audio_path="recordings/active.wav",
                    log_path="recordings/active.log",
                )
            )
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="hello"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=False),
                redirect_stdout(stdout),
            ):
                code = cli.run(["stop", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = StateStore(state_file).read()
            self.assertFalse(audio.exists())
            self.assertFalse(log.exists())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(final_state.status, "done")
            self.assertFalse(final_state.audio_path)
            self.assertFalse(final_state.log_path)
            self.assertIsNone(final_state.pid)
            self.assertFalse(final_state.process_identity)

    def test_start_locked_promotes_relative_recording_path_to_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            audio = recordings_dir / "active.wav"
            audio.write_bytes(b"audio")
            state_file = state_dir / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="stale-process-identity",
                    audio_path="recordings/active.wav",
                )
            )
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(cli, "_recording_process_verified_alive", return_value=False),
                mock.patch.object(cli, "stop_process", return_value=True) as mocked_stop,
            ):
                result = cli._command_start_locked(argparse.Namespace(), store)
            final_state = store.read()
            self.assertEqual(result["status"], "recorded")
            self.assertEqual(final_state.status, "recorded")
            self.assertIsNone(final_state.pid)
            self.assertFalse(final_state.process_identity)
            mocked_stop.assert_called_once_with(1234, expected_process_identity="stale-process-identity")

    def test_start_locked_preserves_state_when_stale_recorder_group_cannot_be_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            audio = recordings_dir / "active.wav"
            audio.write_bytes(b"audio")
            state_file = state_dir / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="stale-process-identity",
                    audio_path="recordings/active.wav",
                )
            )
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(cli, "_recording_process_verified_alive", return_value=False),
                mock.patch.object(cli, "stop_process", return_value=False) as mocked_stop,
            ):
                result = cli._command_start_locked(argparse.Namespace(), store)

            final_state = store.read()

        self.assertEqual(result["status"], "recording")
        self.assertIn("could not be stopped safely", result["error"])
        self.assertEqual(final_state.status, "recording")
        self.assertEqual(final_state.error, result["error"])
        mocked_stop.assert_called_once_with(1234, expected_process_identity="stale-process-identity")

    def test_start_locked_does_not_promote_directory_to_recorded_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            audio = recordings_dir / "active.wav"
            audio.mkdir()
            state_file = state_dir / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="stale-process-identity",
                    audio_path="recordings/active.wav",
                )
            )
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(cli, "_recording_process_verified_alive", return_value=False),
                mock.patch.object(cli, "stop_process", return_value=True),
            ):
                result = cli._command_start_locked(argparse.Namespace(), store)

            final_state = store.read()

        self.assertEqual(result["status"], "error")
        self.assertIn("recording exited before audio was saved", result["message"])
        self.assertEqual(final_state.status, "error")

    def test_start_locked_rejects_relative_recording_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            (tmp_path / "escape.wav").write_bytes(b"audio")
            state_file = state_dir / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", audio_path="../escape.wav"))
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}):
                result = cli._command_start_locked(argparse.Namespace(), store)
            final_state = store.read()
            self.assertEqual(result["status"], "error")
            self.assertEqual(final_state.status, "error")

    def test_status_reports_microphone_level_for_relative_recording_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            state_dir = tmp_path / "speed-of-cinnamon"
            recordings_dir = state_dir / "recordings"
            state_dir.mkdir(parents=True)
            state_dir.chmod(0o700)
            recordings_dir.mkdir(parents=True)
            audio = recordings_dir / "active.wav"
            with wave.open(str(audio), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(16000)
                wav_file.writeframes(b"\x00\x00" * 32)
            state_file = state_dir / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", audio_path="recordings/active.wav"))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._recording_process_verified_alive", return_value=True),
                redirect_stdout(stdout),
            ):
                code = cli.run(["status", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "recording")
            self.assertIn("microphone_level", payload)

    def test_cleanup_dry_run_reports_unsafe_transient_transcript_owner_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcript_dir = tmp_path / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            stale = transcript_dir / ".old.abcd.tmp.txt"
            stale.write_text("old plaintext\n", encoding="utf-8")
            owner = cli._transient_transcript_owner_path(stale)
            target = tmp_path / "owner-target"
            target.write_text("foreign owner\n", encoding="utf-8")
            owner.symlink_to(target)
            old_mtime = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            os.utime(stale, (old_mtime, old_mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--dry-run", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            stale_exists = stale.exists()
            owner_is_symlink = owner.is_symlink()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["would_delete_transient_transcripts"], 1)
        self.assertEqual(payload["failed_path_count"], 1)
        self.assertEqual(payload["failed_paths"], [])
        self.assertNotIn(str(owner), json.dumps(payload))
        self.assertTrue(stale_exists)
        self.assertTrue(owner_is_symlink)

    def test_cleanup_reports_transcript_directory_scan_failure_in_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            failed_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"

            def fail_scan() -> list[Path]:
                raise cli.DirectoryScanError(failed_dir, field_name="transcript directory")

            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._safe_transcript_artifact_files", side_effect=fail_scan),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--dry-run", "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("failed to scan or delete 1 file", payload["error"])
        self.assertEqual(payload["failed_paths"], [])
        self.assertNotIn(str(failed_dir), json.dumps(payload))

    def test_cleanup_failed_paths_rejects_malformed_cleanup_results(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "missing failed_paths"):
            cli._cleanup_failed_paths({})
        with self.assertRaisesRegex(RuntimeError, "failed_paths must be a list"):
            cli._cleanup_failed_paths({"failed_paths": "bad"})
        with self.assertRaisesRegex(RuntimeError, "entries must be non-empty strings"):
            cli._cleanup_failed_paths({"failed_paths": ["ok", ""]})

    def test_cleanup_skips_stale_transient_transcript_with_live_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            active = transcript_dir / ".active.abcd.tmp.txt"
            active.write_text("active plaintext\n", encoding="utf-8")
            env = {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}
            old_mtime = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            with mock.patch.dict(os.environ, env):
                cli._write_transient_transcript_owner(active)
            os.utime(active, (old_mtime, old_mtime))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, env), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            active_exists = active.exists()
            owner_exists = cli._transient_transcript_owner_path(active).exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_transient_transcripts"], 0)
        self.assertTrue(active_exists)
        self.assertTrue(owner_exists)

    def test_cleanup_prunes_recording_groups_and_skips_active_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"

            def write_group(stem: str, mtime: int) -> tuple[Path, Path]:
                audio = recordings / f"{stem}.wav"
                log = recordings / f"{stem}.log"
                audio.write_bytes(b"audio")
                log.write_text("log", encoding="utf-8")
                os.utime(audio, (mtime, mtime))
                os.utime(log, (mtime, mtime))
                return audio, log

            recent = int(cli.time.time())
            old_audio, old_log = write_group("old", recent - 10)
            new_audio, new_log = write_group("new", recent)
            active_audio, active_log = write_group("active", recent - 20)
            StateStore(state_file).write(
                RecordingState(status="recording", audio_path=str(active_audio), log_path=str(active_log))
            )

            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--state-file",
                    str(state_file),
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "1",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            old_audio_exists = old_audio.exists()
            old_log_exists = old_log.exists()
            new_audio_exists = new_audio.exists()
            new_log_exists = new_log.exists()
            active_audio_exists = active_audio.exists()
            active_log_exists = active_log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_recordings"], 1)
        self.assertEqual(payload["deleted_logs"], 1)
        self.assertFalse(old_audio_exists)
        self.assertFalse(old_log_exists)
        self.assertTrue(new_audio_exists)
        self.assertTrue(new_log_exists)
        self.assertTrue(active_audio_exists)
        self.assertTrue(active_log_exists)
        self.assertEqual(payload["skipped_active_path_count"], 2)
        self.assertEqual(payload["skipped_active_paths"], [])
        encoded = json.dumps(payload)
        self.assertNotIn(str(active_audio), encoded)
        self.assertNotIn(str(active_log), encoded)

    def test_cleanup_defaults_to_keeping_twenty_recording_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            for index in range(21):
                audio = recordings / f"recording-{index:02d}.wav"
                audio.write_bytes(b"audio")
                recent = int(cli.time.time())
                os.utime(audio, (recent + index, recent + index))

            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--json"])
            payload = json.loads(stdout.getvalue())
            oldest_audio_exists = (recordings / "recording-00.wav").exists()
            newest_audio_exists = (recordings / "recording-20.wav").exists()
            remaining_recordings = len(list(recordings.glob("*.wav")))
        self.assertEqual(code, 0)
        self.assertEqual(payload["keep_recordings"], 20)
        self.assertEqual(payload["deleted_recordings"], 1)
        self.assertEqual(payload["deleted_logs"], 0)
        self.assertFalse(oldest_audio_exists)
        self.assertTrue(newest_audio_exists)
        self.assertEqual(remaining_recordings, 20)

    def test_prune_recording_files_keeps_cap_when_active_artifacts_are_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = []
            for index in range(22):
                path = root / f"recording-{index:02d}.wav"
                path.write_bytes(b"audio")
                os.utime(path, (index, index))
                paths.append(path)
            active = {paths[0].resolve(strict=False)}

            result = cli.prune_files_by_mtime(paths, keep=20, active_paths=active, dry_run=True)

        self.assertEqual(len(result["planned_paths"]), 2)
        self.assertIn(str(paths[0]), result["skipped_active_paths"])
        self.assertNotIn(str(paths[0]), result["planned_paths"])

    def test_cleanup_prunes_recording_groups_older_than_one_week(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            old_audio = recordings / "old.wav"
            old_log = recordings / "old.log"
            fresh_audio = recordings / "fresh.wav"
            fresh_log = recordings / "fresh.log"
            old_audio.write_bytes(b"audio")
            old_log.write_text("log", encoding="utf-8")
            fresh_audio.write_bytes(b"audio")
            fresh_log.write_text("log", encoding="utf-8")
            old_mtime = 100
            os.utime(old_audio, (old_mtime, old_mtime))
            os.utime(old_log, (old_mtime, old_mtime))

            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "25",
                    "--recording-max-age-days",
                    "7",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            old_audio_exists = old_audio.exists()
            old_log_exists = old_log.exists()
            fresh_audio_exists = fresh_audio.exists()
            fresh_log_exists = fresh_log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["recording_max_age_days"], 7)
        self.assertEqual(payload["deleted_recordings"], 1)
        self.assertEqual(payload["deleted_logs"], 1)
        self.assertFalse(old_audio_exists)
        self.assertFalse(old_log_exists)
        self.assertTrue(fresh_audio_exists)
        self.assertTrue(fresh_log_exists)

    def test_cleanup_dry_run_plans_old_recording_groups_without_deleting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "old.wav"
            log = recordings / "old.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            os.utime(audio, (100, 100))
            os.utime(log, (100, 100))

            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "25",
                    "--recording-max-age-days",
                    "7",
                    "--dry-run",
                    "--json",
            ])
            payload = json.loads(stdout.getvalue())
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["would_delete_recordings"], 1)
        self.assertEqual(payload["would_delete_logs"], 1)
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)

    def test_cleanup_counts_are_case_insensitive_for_recording_suffixes(self) -> None:
        result = {
            "planned_recordings": 0,
            "planned_logs": 0,
            "deleted_recordings": 0,
            "deleted_logs": 0,
        }
        cli._add_recording_artifact_counts(
            [
                "/tmp/session.WAV",
                "/tmp/session.FLAC",
                "/tmp/session.LOG",
                "/tmp/session.txt",
            ],
            result,
            "planned",
        )
        self.assertEqual(result["planned_recordings"], 2)
        self.assertEqual(result["planned_logs"], 1)

    def test_recording_artifact_scanners_match_suffixes_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            (recordings / "upper.WAV").write_bytes(b"audio")
            (recordings / "upper.FLAC").write_bytes(b"audio")
            (recordings / "upper.LOG").write_bytes(b"log")
            (recordings / "ignore.txt").write_bytes(b"text")
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                groups = cli.recording_groups()
                files = cli.recording_artifact_files()
        self.assertEqual({group["stem"] for group in groups}, {"upper"})
        self.assertEqual({path.name for path in files}, {"upper.WAV", "upper.FLAC", "upper.LOG"})

    def test_recording_artifact_scanners_reject_symlinked_recordings_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outside = root / "outside"
            outside.mkdir()
            (outside / "secret.wav").write_bytes(b"audio")
            (outside / "secret.log").write_text("log", encoding="utf-8")
            link = root / "recordings"
            link.symlink_to(outside, target_is_directory=True)

            with mock.patch("speed_of_cinnamon.cli.recordings_dir", return_value=link):
                with self.assertRaises(cli.DirectoryScanError):
                    cli.recording_groups()
                with self.assertRaises(cli.DirectoryScanError):
                    cli.recording_artifact_files()

    def test_recording_artifact_scanners_include_temporary_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            (recordings / "keep.wav").write_bytes(b"audio")
            (recordings / "keep.trimmed-123.flac").write_bytes(b"audio")
            (recordings / "keep.encoded-456.flac").write_bytes(b"audio")
            (recordings / "session.log").write_bytes(b"log")
            os.utime(recordings / "keep.wav", (300, 300))
            os.utime(recordings / "keep.trimmed-123.flac", (200, 200))
            os.utime(recordings / "keep.encoded-456.flac", (100, 100))
            os.utime(recordings / "session.log", (0, 0))
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                groups = cli.recording_groups()
                files = cli.recording_artifact_files()
                cap = cli.prune_recording_groups(keep=1, active_paths=set(), dry_run=True, max_age_days=36500)
        self.assertEqual({group["stem"] for group in groups}, {"keep", "keep.trimmed-123", "keep.encoded-456", "session"})
        self.assertEqual(
            {path.name for path in files},
            {"keep.wav", "keep.trimmed-123.flac", "keep.encoded-456.flac", "session.log"},
        )
        self.assertNotIn(str(recordings / "keep.wav"), cap["planned_paths"])
        self.assertIn(str(recordings / "keep.trimmed-123.flac"), cap["planned_paths"])
        self.assertIn(str(recordings / "keep.encoded-456.flac"), cap["planned_paths"])
        self.assertIn(str(recordings / "session.log"), cap["planned_paths"])

    def test_recording_groups_ignores_non_regular_recording_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            (recordings / "real.wav").write_bytes(b"audio")
            (recordings / "fake.wav").mkdir()
            (recordings / "fake.log").mkdir()
            os.symlink(recordings / "real.wav", recordings / "link.wav")
            try:
                hardlink_source = Path(tmp) / "hardlink-source.wav"
                hardlink_source.write_bytes(b"audio")
                os.link(hardlink_source, recordings / "hardlink.wav")
            except OSError:
                pass
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
                groups = cli.recording_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["stem"], "real")

    def test_delete_artifact_rejects_symlink_and_hardlink_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            real = recordings / "real.wav"
            real.write_bytes(b"audio")
            link = recordings / "link.wav"
            os.symlink(real, link)
            self.assertFalse(cli.delete_artifact(link))
            self.assertTrue(link.is_symlink())

            hardlink = recordings / "hardlink.wav"
            try:
                os.link(real, hardlink)
            except OSError:
                return

            self.assertFalse(cli.delete_artifact(hardlink))
            self.assertTrue(hardlink.exists())
            self.assertTrue(real.exists())

    def test_delete_artifact_fsyncs_parent_after_delete(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            artifact = recordings / "real.wav"
            artifact.write_bytes(b"audio")

            with mock.patch("speed_of_cinnamon.cli.os.fsync", side_effect=record_fsync):
                self.assertTrue(cli.delete_artifact(artifact))

            self.assertFalse(artifact.exists())

        self.assertTrue(any(cli.stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_delete_artifact_rejects_path_swap_before_delete(self) -> None:
        real_stat = os.stat
        swapped = False

        def stat_with_swap(path: object, *args: object, **kwargs: object) -> os.stat_result:
            nonlocal swapped
            result = real_stat(path, *args, **kwargs)
            if path == "real.wav" and kwargs.get("dir_fd") is not None and not swapped:
                artifact.unlink()
                artifact.write_bytes(b"attacker")
                swapped = True
                return real_stat(path, *args, **kwargs)
            return result

        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            artifact = recordings / "real.wav"
            artifact.write_bytes(b"audio")

            with mock.patch("speed_of_cinnamon.cli.os.stat", side_effect=stat_with_swap):
                self.assertFalse(cli.delete_artifact(artifact))

            self.assertTrue(artifact.exists())
            self.assertEqual(artifact.read_bytes(), b"attacker")

    def test_prune_files_by_mtime_keeps_twenty_latest_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            for index in range(21):
                audio = recordings / f"{index:03d}.wav"
                audio.write_bytes(b"audio")
                os.utime(audio, (100 + index, 100 + index))
            files = [path for path in recordings.iterdir() if path.suffix == ".wav"]
            ordered = sorted(files, key=lambda path: path.stat().st_mtime)
            result = cli.prune_files_by_mtime(files, keep=cli.MAX_TEMP_RECORDING_FILES, active_paths=set(), dry_run=False)
            remaining_files = len([path for path in files if path.exists()])

        self.assertEqual(len(result["deleted_paths"]), 1)
        self.assertIn(str(ordered[0]), result["deleted_paths"])
        self.assertEqual(remaining_files, 20)

    def test_prune_recording_groups_reuses_grouped_artifacts_for_file_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            for index in range(21):
                audio = recordings / f"{index:03d}.wav"
                audio.write_bytes(b"audio")
                os.utime(audio, (100 + index, 100 + index))
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.recording_artifact_files", side_effect=AssertionError("unexpected rescan")),
            ):
                result = cli.prune_recording_groups(
                    keep=cli.MAX_TEMP_RECORDING_FILES,
                    active_paths=set(),
                    dry_run=True,
                    max_age_days=36500,
                )

        self.assertEqual(len(result["planned_paths"]), 1)
        self.assertTrue(str(recordings / "000.wav") in result["planned_paths"])

    def test_prune_recording_groups_excludes_active_group_from_file_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            active_audio = recordings / "active.wav"
            active_log = recordings / "active.log"
            other_audio = recordings / "other.wav"
            active_audio.write_bytes(b"audio")
            active_log.write_text("log", encoding="utf-8")
            other_audio.write_bytes(b"audio")
            os.utime(active_audio, (200, 200))
            os.utime(active_log, (200, 200))
            os.utime(other_audio, (100, 100))
            active_audio_alias = active_audio.parent / "nested" / ".." / active_audio.name
            active_group = {active_audio_alias, active_log}

            def _prune_files_by_mtime(
                paths: list[Path],
                keep: int,
                active_paths: set[Path],
                dry_run: bool,
            ) -> dict[str, object]:
                self.assertTrue(active_group.isdisjoint({path.resolve(strict=False) for path in paths}))
                return {
                    "planned_paths": [],
                    "deleted_paths": [],
                    "failed_paths": [],
                    "skipped_active_paths": [],
                }

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.prune_files_by_mtime", side_effect=_prune_files_by_mtime),
            ):
                result = cli.prune_recording_groups(
                    keep=0,
                    active_paths={active_audio_alias},
                    dry_run=True,
                    max_age_days=36500,
                )

        self.assertIn(str(active_audio), result["skipped_active_paths"])
        self.assertIn(str(active_log), result["skipped_active_paths"])

    def test_cleanup_dry_run_does_not_delete_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_dir.mkdir(parents=True)
            old = transcript_dir / "old.txt"
            old.write_text("old", encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--dry-run", "--json"])
            payload = json.loads(stdout.getvalue())
            old_exists = old.exists()
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["deleted_transcripts"], 0)
        self.assertEqual(payload["would_delete_transcripts"], 1)
        self.assertTrue(old_exists)

    def test_cleanup_deletes_stale_temporary_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            old_regular = recordings / "old.wav"
            old_temp = recordings / "old.trimmed-abc.flac"
            old_regular.write_bytes(b"audio")
            old_temp.write_bytes(b"audio")
            os.utime(old_regular, (100, 100))
            os.utime(old_temp, (100, 100))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "cleanup",
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "0",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            old_regular_exists = old_regular.exists()
            old_temp_exists = old_temp.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_recordings"], 2)
        self.assertFalse(old_regular_exists)
        self.assertFalse(old_temp_exists)

    def test_cleanup_skips_inflight_trimmed_encoded_artifacts_during_active_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            transcripts = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcripts.mkdir(parents=True)
            active_audio = recordings / "active.wav"
            active_log = recordings / "active.log"
            active_trimmed = recordings / "active.trimmed-final.flac"
            active_encoded = recordings / "active.encoded-final.flac"
            active_transcript = transcripts / "active.txt"
            active_encrypted_transcript = transcripts / "active.txt.socenc"
            stale_audio = recordings / "stale.wav"
            stale_txt = transcripts / "stale.txt"
            active_audio.write_bytes(b"audio")
            active_log.write_text("log", encoding="utf-8")
            active_trimmed.write_bytes(b"trimmed")
            active_encoded.write_bytes(b"encoded")
            active_transcript.write_text("active", encoding="utf-8")
            active_encrypted_transcript.write_text("active encrypted", encoding="utf-8")
            stale_audio.write_bytes(b"stale")
            stale_txt.write_text("stale", encoding="utf-8")
            os.utime(active_audio, (100, 100))
            os.utime(active_log, (100, 100))
            os.utime(active_trimmed, (100, 100))
            os.utime(active_encoded, (100, 100))
            os.utime(active_transcript, (100, 100))
            os.utime(active_encrypted_transcript, (100, 100))
            os.utime(stale_audio, (100, 100))
            os.utime(stale_txt, (100, 100))

            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(
                RecordingState(status="finalizing", audio_path=str(active_audio), log_path=str(active_log))
            )
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")
            lock_path.chmod(0o600)

            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "cleanup",
                    "--state-file",
                    str(state_file),
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "0",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

            stale_audio_exists = stale_audio.exists()
            stale_transcript_exists = stale_txt.exists()
            active_audio_exists = active_audio.exists()
            active_log_exists = active_log.exists()
            active_trimmed_exists = active_trimmed.exists()
            active_encoded_exists = active_encoded.exists()
            active_transcript_exists = active_transcript.exists()
            active_encrypted_transcript_exists = active_encrypted_transcript.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_recordings"], 1)
        self.assertEqual(payload["deleted_transcripts"], 1)
        self.assertFalse(stale_audio_exists)
        self.assertFalse(stale_transcript_exists)
        self.assertEqual(payload["skipped_active_path_count"], 6)
        self.assertEqual(payload["skipped_active_paths"], [])
        encoded = json.dumps(payload)
        self.assertNotIn(str(active_trimmed), encoded)
        self.assertNotIn(str(active_encoded), encoded)
        self.assertNotIn(str(active_audio), encoded)
        self.assertNotIn(str(active_log), encoded)
        self.assertNotIn(str(active_transcript), encoded)
        self.assertNotIn(str(active_encrypted_transcript), encoded)
        self.assertTrue(active_audio_exists)
        self.assertTrue(active_log_exists)
        self.assertTrue(active_trimmed_exists)
        self.assertTrue(active_encoded_exists)
        self.assertTrue(active_transcript_exists)
        self.assertTrue(active_encrypted_transcript_exists)

    def test_inflight_recording_artifact_paths_escape_glob_metacharacters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp)
            active_audio = recordings / "active[abc].wav"
            active_trimmed = recordings / "active[abc].trimmed-final.flac"
            unrelated_trimmed = recordings / "activea.trimmed-final.flac"
            active_audio.write_bytes(b"audio")
            active_trimmed.write_bytes(b"trimmed")
            unrelated_trimmed.write_bytes(b"unrelated")

            paths = cli._inflight_recording_artifact_paths(active_audio)

        self.assertEqual(paths, {active_trimmed})

    def test_cleanup_deletes_temporary_recording_artifacts_without_live_finalization_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            transcripts = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcripts.mkdir(parents=True)
            active_audio = recordings / "active.wav"
            active_log = recordings / "active.log"
            active_trimmed = recordings / "active.trimmed-final.flac"
            active_encoded = recordings / "active.encoded-final.flac"
            active_transcript = transcripts / "active.txt"
            active_audio.write_bytes(b"audio")
            active_log.write_text("log", encoding="utf-8")
            active_trimmed.write_bytes(b"trimmed")
            active_encoded.write_bytes(b"encoded")
            active_transcript.write_text("active", encoding="utf-8")
            os.utime(active_audio, (100, 100))
            os.utime(active_log, (100, 100))
            os.utime(active_trimmed, (100, 100))
            os.utime(active_encoded, (100, 100))
            os.utime(active_transcript, (100, 100))

            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(
                RecordingState(status="finalizing", audio_path=str(active_audio), log_path=str(active_log))
            )

            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "cleanup",
                    "--state-file",
                    str(state_file),
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "0",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

            active_audio_exists = active_audio.exists()
            active_log_exists = active_log.exists()
            active_trimmed_exists = active_trimmed.exists()
            active_encoded_exists = active_encoded.exists()
            active_transcript_exists = active_transcript.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["deleted_path_count"], 3)
        self.assertEqual(payload["deleted_paths"], [])
        encoded = json.dumps(payload)
        self.assertNotIn(str(active_trimmed), encoded)
        self.assertNotIn(str(active_encoded), encoded)
        self.assertNotIn(str(active_transcript), encoded)
        self.assertTrue(active_audio_exists)
        self.assertTrue(active_log_exists)
        self.assertFalse(active_trimmed_exists)
        self.assertFalse(active_encoded_exists)
        self.assertFalse(active_transcript_exists)

    def test_finalize_rejects_state_audio_path_outside_recordings_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "outside"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording") as mocked_silence,
                mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=AssertionError("should not transcribe")),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf outside-dir-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("recording audio path is invalid", payload["error"])
        self.assertEqual(final_state.status, "error")
        mocked_silence.assert_not_called()

    def test_finalize_can_keep_stabilized_trimmed_flac_recording_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.flac"
            temp_trimmed = recordings_root / "recording.trimmed-keep.flac"
            log = recordings_root / "recording.log"
            original.write_bytes(b"original-audio")
            temp_trimmed.write_bytes(b"trimmed-audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=temp_trimmed),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            final_audio = Path(final_state.audio_path)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["recording_artifacts_kept"], True)
            self.assertEqual(final_audio.name, "recording.flac")
            self.assertTrue(final_audio.exists())
            self.assertEqual(final_audio.read_bytes(), b"trimmed-audio")
            self.assertFalse(temp_trimmed.exists())

    @mock.patch("speed_of_cinnamon.cli._rename_without_replacing", wraps=cli._rename_without_replacing)
    def test_stabilize_recording_artifact_uses_secure_directory_fd_replace(self, mocked_rename: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings_root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            temp_trimmed = recordings_root / "recording.trimmed-keep.flac"
            temp_trimmed.write_bytes(b"trimmed-audio")

            with mock.patch("speed_of_cinnamon.cli.os.fsync", wraps=os.fsync) as mocked_fsync:
                stable = cli._stabilize_recording_artifact_path(temp_trimmed)

            self.assertEqual(stable, recordings_root / "recording.flac")
            self.assertEqual(stable.read_bytes(), b"trimmed-audio")
            self.assertFalse(temp_trimmed.exists())
            mocked_fsync.assert_called()
            calls = [
                (args, kwargs)
                for args, kwargs in mocked_rename.call_args_list
                if args[:2] == ("recording.trimmed-keep.flac", "recording.flac")
            ]
            self.assertEqual(len(calls), 1)
            _args, kwargs = calls[0]
            self.assertIsInstance(kwargs.get("directory_fd"), int)
            self.assertEqual(kwargs.get("field_name"), "stable recording artifact")

    def test_stabilize_recording_artifact_does_not_overwrite_unrelated_stable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings_root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            temp_trimmed = recordings_root / "recording.trimmed-collision.flac"
            stable = recordings_root / "recording.flac"
            temp_trimmed.write_bytes(b"new-audio")
            stable.write_bytes(b"existing-audio")

            with self.assertRaisesRegex(RuntimeError, "stable recording artifact already exists"):
                cli._stabilize_recording_artifact_path(temp_trimmed)

            self.assertEqual(temp_trimmed.read_bytes(), b"new-audio")
            self.assertEqual(stable.read_bytes(), b"existing-audio")

    def test_stabilize_recording_artifact_does_not_clobber_target_created_during_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings_root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            temp_trimmed = recordings_root / "recording.trimmed-race.flac"
            stable = recordings_root / "recording.flac"
            racing = recordings_root / "racing.flac"
            temp_trimmed.write_bytes(b"new-audio")
            real_rename = cli._rename_without_replacing

            def rename_then_race(source: object, destination: object, *args: object, **kwargs: object) -> None:
                if destination == stable.name:
                    racing.write_bytes(b"racing-audio")
                    racing.replace(stable)
                real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(cli, "_rename_without_replacing", side_effect=rename_then_race):
                with self.assertRaisesRegex(RuntimeError, "failed to stabilize recording artifact path"):
                    cli._stabilize_recording_artifact_path(temp_trimmed)

            self.assertEqual(stable.read_bytes(), b"racing-audio")
            self.assertEqual(temp_trimmed.read_bytes(), b"new-audio")

    def test_stabilize_recording_artifact_restores_no_clobber_backup_after_target_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings_root = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            temp_trimmed = recordings_root / "recording.trimmed-race.wav"
            stable = recordings_root / "recording.wav"
            racing = recordings_root / "racing.wav"
            temp_trimmed.write_bytes(b"new-audio")
            stable.write_bytes(b"old-audio")
            real_rename = cli._rename_without_replacing

            def rename_then_race(source: object, destination: object, *args: object, **kwargs: object) -> None:
                if destination == stable.name:
                    racing.write_bytes(b"racing-audio")
                    racing.replace(stable)
                real_rename(source, destination, *args, **kwargs)

            with mock.patch.object(cli, "_rename_without_replacing", side_effect=rename_then_race):
                with self.assertRaisesRegex(RuntimeError, "failed to stabilize recording artifact path"):
                    cli._stabilize_recording_artifact_path(temp_trimmed, replace_existing_path=stable)

            self.assertEqual(stable.read_bytes(), b"racing-audio")
            self.assertEqual(temp_trimmed.read_bytes(), b"new-audio")
            self.assertTrue(list(recordings_root.glob(".recording.wav.*.bak")))

    def test_cleanup_rejects_boolean_recording_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch(
                    "speed_of_cinnamon.cli.prune_recording_groups",
                    return_value={
                        "planned_recordings": True,
                        "planned_logs": 0,
                        "planned_paths": [],
                        "deleted_recordings": 0,
                        "deleted_logs": 0,
                        "deleted_paths": [],
                        "failed_paths": [],
                        "skipped_active_paths": [],
                    },
                ),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("planned-recordings must be an integer", payload["error"])

    def test_cleanup_rejects_negative_keep_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cleanup", "--keep-transcripts", "-1", "--keep-recordings", "0", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("keep-transcripts must be at least 0", payload["error"])

    def test_cleanup_rejects_excessive_keep_recordings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    str(cli.MAX_KEEP_RECORDINGS + 1),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("keep-recordings must be at most", payload["error"])

    def test_cleanup_rejects_negative_recording_max_age_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "cleanup",
                    "--keep-transcripts",
                    "0",
                    "--keep-recordings",
                    "0",
                    "--recording-max-age-days",
                    "-1",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("recording-max-age-days must be at least 0", payload["error"])

    def test_alarms_check_rejects_negative_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "check", "--catch-up-minutes", "-1", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("catch-up-minutes must be at least 0", payload["error"])

    def test_alarms_check_rejects_excessive_catch_up_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "check",
                    "--catch-up-minutes",
                    str(cli.MAX_ALARM_CATCH_UP_MINUTES + 1),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("catch-up-minutes must be at most", payload["error"])

    def test_alarms_add_rejects_null_byte_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--name",
                    "private\x00alarm",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm name contains invalid null byte", payload["error"])

    def test_alarms_add_rejects_null_byte_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--days",
                    "mon\x00,fri",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm days contains invalid null byte", payload["error"])

    def test_alarms_add_rejects_oversized_days_input(self) -> None:
        days = ",".join(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] * 30)
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--days",
                    days,
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm days is too large", payload["error"])

    def test_alarms_add_rejects_oversized_name_input(self) -> None:
        name = "A" * (MAX_ALARM_NAME_CHARS + 10)
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "alarms",
                    "add",
                    "--time",
                    "09:00",
                    "--name",
                    name,
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm name is too large", payload["error"])

    def test_alarms_remove_rejects_null_byte_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "remove", "alarm\x00id", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm id contains invalid null byte", payload["error"])

    def test_alarms_enable_rejects_null_byte_id(self) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "enable", "alarm\x00id", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm id contains invalid null byte", payload["error"])

    def test_alarms_enable_rejects_oversized_id(self) -> None:
        oversized_id = "X" * (MAX_ALARM_ID_CHARS + 30)
        with tempfile.TemporaryDirectory() as tmp:
            alarm_path = Path(tmp) / "speed-of-cinnamon" / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "alarms": [
                        {
                            "id": oversized_id[:MAX_ALARM_ID_CHARS],
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon"],
                            "enabled": False,
                            "urgency": "normal",
                        }
                    ],
                    "last_checked_at": "",
                },
                alarm_path,
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "enable", oversized_id, "--json"])
            payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                after = list_alarm_payload(alarm_path)
        self.assertEqual(code, 1)
        self.assertIn("alarm id is too large", payload["error"])
        self.assertFalse(after["alarms"][0]["enabled"])

    def test_alarms_remove_rejects_oversized_id_without_removing_matching_entry(self) -> None:
        oversized_id = "X" * (MAX_ALARM_ID_CHARS + 30)
        with tempfile.TemporaryDirectory() as tmp:
            alarm_path = Path(tmp) / "speed-of-cinnamon" / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "alarms": [
                        {
                            "id": oversized_id[:MAX_ALARM_ID_CHARS],
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon"],
                            "enabled": True,
                            "urgency": "normal",
                        }
                    ],
                    "last_checked_at": "",
                },
                alarm_path,
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "remove", oversized_id, "--json"])
            payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                after = list_alarm_payload(alarm_path)
        self.assertEqual(code, 1)
        self.assertIn("alarm id is too large", payload["error"])
        self.assertEqual(len(after["alarms"]), 1)

    def test_alarms_disable_rejects_oversized_id(self) -> None:
        oversized_id = "X" * (MAX_ALARM_ID_CHARS + 30)
        with tempfile.TemporaryDirectory() as tmp:
            alarm_path = Path(tmp) / "speed-of-cinnamon" / "alarms.json"
            save_alarm_store(
                {
                    "version": 1,
                    "alarms": [
                        {
                            "id": oversized_id[:MAX_ALARM_ID_CHARS],
                            "hour": 9,
                            "minute": 0,
                            "days": ["mon"],
                            "enabled": True,
                            "urgency": "normal",
                        }
                    ],
                    "last_checked_at": "",
                },
                alarm_path,
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "disable", oversized_id, "--json"])
            payload = json.loads(stdout.getvalue())
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                after = list_alarm_payload(alarm_path)
        self.assertEqual(code, 1)
        self.assertIn("alarm id is too large", payload["error"])
        self.assertTrue(after["alarms"][0]["enabled"])

    def test_alarms_disable_rejects_null_byte_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["alarms", "disable", "alarm\x00id", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarm id contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources")
    def test_diagnostics_omits_transcript_text(self, mocked_sources: mock.Mock) -> None:
        mocked_sources.return_value = [
            InputSource(id="1", name="alsa_input.test", description="Test Mic", default=True)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            transcript_dir = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            recordings_dir = Path(tmp) / "speed-of-cinnamon" / "recordings"
            transcript_dir.mkdir(parents=True)
            recordings_dir.mkdir(parents=True)
            (transcript_dir / "secret.txt").write_text("secret dictated words\n", encoding="utf-8")
            audio_path = recordings_dir / "secret.flac"
            log_path = recordings_dir / "secret.log"
            transcript_path = transcript_dir / "secret.txt"
            audio_path.write_bytes(b"audio")
            log_path.write_text("log\n", encoding="utf-8")
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(
                RecordingState(
                    status="done",
                    audio_path=str(audio_path),
                    log_path=str(log_path),
                    transcript_path=str(transcript_path),
                    process_identity="private-process-identity",
                    transcript="secret dictated words",
                )
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                add_alarm("09:00", name="private alarm name")
                code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        encoded = json.dumps(payload)
        self.assertEqual(code, 0)
        self.assertEqual(payload["app"]["id"], "speed-of-cinnamon")
        self.assertEqual(payload["inputs"]["sources"][0]["name"], "alsa_input.test")
        self.assertIn("models", payload)
        self.assertTrue(all("path" not in model for model in payload["models"]))
        self.assertTrue(all("path_present" in model for model in payload["models"]))
        self.assertEqual(payload["alarms"]["configured"], 1)
        self.assertIn("recent_transcripts", payload)
        self.assertTrue(payload["paths"]["redacted"])
        self.assertTrue(payload["paths"]["state_dir_present"])
        self.assertNotIn("state_dir", payload["paths"])
        self.assertNotIn("recordings_dir", payload["paths"])
        self.assertEqual(payload["state"]["transcript_length"], len("secret dictated words"))
        self.assertTrue(payload["state"]["audio_path_present"])
        self.assertTrue(payload["state"]["log_path_present"])
        self.assertTrue(payload["state"]["transcript_path_present"])
        self.assertTrue(payload["state"]["process_identity_present"])
        self.assertNotIn("audio_path", payload["state"])
        self.assertNotIn("log_path", payload["state"])
        self.assertNotIn("transcript_path", payload["state"])
        self.assertNotIn("process_identity", payload["state"])
        self.assertEqual(payload["recent_transcripts"][0]["name"], cli.HISTORY_METADATA_REDACTED_TEXT)
        self.assertNotIn("path", payload["recent_transcripts"][0])
        self.assertNotIn("modified_at", payload["recent_transcripts"][0])
        self.assertNotIn(str(tmp), encoded)
        self.assertNotIn("secret.txt", encoded)
        self.assertNotIn("secret dictated words", encoded)
        self.assertNotIn(str(audio_path), encoded)
        self.assertNotIn(str(log_path), encoded)
        self.assertNotIn(str(transcript_dir / "secret.txt"), encoded)
        self.assertNotIn("private-process-identity", encoded)
        self.assertNotIn("private alarm name", encoded)
        self.assertNotIn("preview", encoded)
        self.assertNotIn('"text"', encoded)

    def test_status_json_redacts_state_artifact_paths_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            audio_path = Path(tmp) / "recordings" / "secret.flac"
            log_path = Path(tmp) / "recordings" / "secret.log"
            transcript_path = Path(tmp) / "transcripts" / "secret.txt"
            StateStore(state_file).write(
                RecordingState(
                    status="done",
                    audio_path=str(audio_path),
                    log_path=str(log_path),
                    transcript_path=str(transcript_path),
                    process_identity="private-process-identity",
                    transcript="secret dictated words",
                )
            )
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.run(["status", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        encoded = json.dumps(payload, sort_keys=True)
        self.assertEqual(code, 0)
        self.assertEqual(payload["transcript_length"], len("secret dictated words"))
        self.assertTrue(payload["audio_path_present"])
        self.assertTrue(payload["log_path_present"])
        self.assertTrue(payload["transcript_path_present"])
        self.assertTrue(payload["process_identity_present"])
        self.assertNotIn("audio_path", payload)
        self.assertNotIn("log_path", payload)
        self.assertNotIn("transcript_path", payload)
        self.assertNotIn("process_identity", payload)
        self.assertNotIn(str(audio_path), encoded)
        self.assertNotIn(str(log_path), encoded)
        self.assertNotIn(str(transcript_path), encoded)
        self.assertNotIn("private-process-identity", encoded)
        self.assertNotIn("secret dictated words", encoded)

    @mock.patch("speed_of_cinnamon.cli.list_input_sources", side_effect=RuntimeError("token abc123"))
    def test_diagnostics_redacts_nested_source_errors(self, mocked_sources: mock.Mock) -> None:
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        encoded = json.dumps(payload)
        self.assertEqual(code, 0)
        self.assertNotIn("token abc123", encoded)
        self.assertNotIn("abc123", encoded)

    @mock.patch("speed_of_cinnamon.cli.list_input_sources", side_effect=RuntimeError("token abc123"))
    def test_command_diagnostics_redacts_source_errors_before_global_payload_redaction(self, mocked_sources: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"),
                mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""}),
                mock.patch("speed_of_cinnamon.cli.list_models", return_value=[]),
            ):
                payload = cli.command_diagnostics(argparse.Namespace(
                    settings_json="{}",
                    applet=False,
                    output="",
                    save=False,
                    state_file=str(state_file),
                ))

        encoded = json.dumps(payload)
        self.assertIn("inputs", payload)
        self.assertNotIn("token abc123", encoded)
        self.assertNotIn("abc123", encoded)

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    def test_diagnostics_rejects_non_boolean_applet(self, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.ensure_runtime_dirs"),
                mock.patch("speed_of_cinnamon.cli.list_models", return_value=[]),
            ):
                with self.assertRaisesRegex(RuntimeError, "applet must be a boolean"):
                    cli.command_diagnostics(argparse.Namespace(
                        settings_json="{}",
                        applet="yes",
                        output="",
                        save=False,
                        state_file=str(state_file),
                    ))
        mocked_sources.assert_not_called()
        mocked_alarms.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": "invalid", "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    def test_diagnostics_rejects_non_list_alarms(self, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarms entries must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=[])
    def test_diagnostics_rejects_non_object_alarm_payload(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("alarms payload must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value="invalid")
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=[])
    def test_diagnostics_rejects_non_list_input_sources(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["inputs"]["ok"], False)
        self.assertIn("input sources must be a list", payload["inputs"]["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[object()])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=[])
    def test_diagnostics_rejects_invalid_input_source_entry(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["inputs"]["ok"], False)
        self.assertIn("input source id must be text", payload["inputs"]["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value="invalid")
    def test_diagnostics_rejects_non_list_models_payload(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload must be a list", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_alarm_payload", return_value={"alarms": [], "last_checked_at": ""})
    @mock.patch("speed_of_cinnamon.cli.list_input_sources", return_value=[])
    @mock.patch("speed_of_cinnamon.cli.list_models", return_value=["invalid"])
    def test_diagnostics_rejects_invalid_model_entry(self, mocked_models: mock.Mock, mocked_sources: mock.Mock, mocked_alarms: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                with redirect_stdout(io.StringIO()) as capture:
                    code = cli.run(["diagnostics", "--state-file", str(state_file), "--json"])
            payload = json.loads(capture.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("model payload entry must be an object", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.list_input_sources")
    def test_diagnostics_save_writes_private_report(self, mocked_sources: mock.Mock) -> None:
        mocked_sources.return_value = [
            InputSource(id="1", name="alsa_input.test", description="Test Mic", default=True)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "diagnostics.json"
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="done", transcript="private words"))
            stdout = io.StringIO()
            with (
                mock.patch("sys.stdin", io.StringIO(json.dumps({
                    "transcriber": "command",
                    "transcriber-command": "printf hidden-command-token",
                    "insert-method": "clipboard-paste",
                    "post-process-backend": "ollama",
                    "ollama-model": "llama3.2:3b",
                    "post-process-prompt": "hidden-polish-prompt",
                    "personal-context": "hidden-context-token",
                    "vocabulary": "hidden-vocabulary-token",
                }))),
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "diagnostics",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(output),
                    "--applet",
                    "--settings-json-stdin",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            saved = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertTrue(payload["saved_path_present"])
        self.assertNotIn("saved_path", payload)
        self.assertNotIn(str(output), json.dumps(payload))
        self.assertNotIn("saved_path", saved)
        self.assertEqual(payload["message"], "diagnostics saved")
        self.assertEqual(saved["state"]["transcript_length"], len("private words"))
        encoded = json.dumps(saved)
        self.assertNotIn(str(output), encoded)
        self.assertNotIn("private words", encoded)
        self.assertNotIn("hidden-command-token", encoded)
        self.assertNotIn("hidden-polish-prompt", encoded)
        self.assertNotIn("hidden-context-token", encoded)
        self.assertNotIn("hidden-vocabulary-token", encoded)

    def test_diagnostics_save_rejects_atomic_write_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "diagnostics.json"
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="done", transcript="private words"))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.path_safety._rename_without_replacing", side_effect=OSError("disk full")),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "diagnostics",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(output),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("failed to write JSON output", payload["error"])

    def test_diagnostics_save_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            link.symlink_to(real, target_is_directory=True)
            output = link / "diagnostics.json"
            state_file = root / "state.json"
            StateStore(state_file).write(RecordingState(status="done", transcript="private words"))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "diagnostics",
                    "--state-file",
                    str(state_file),
                    "--output",
                    str(output),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 1)
        self.assertIn("diagnostics output must not pass through a symlink", payload["error"])
        self.assertFalse((real / "diagnostics.json").exists())

    def test_diagnostics_rejects_overlong_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--output", "x" * (cli.MAX_PATH_CHARS + 10), "--save", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("too large", payload["error"])

    def test_diagnostics_rejects_large_settings_json(self) -> None:
        long_json = json.dumps({"payload": "x" * (cli.MAX_SETTINGS_JSON_CHARS + 10)})
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--settings-json", long_json, "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings JSON is too large", payload["error"])

    def test_diagnostics_rejects_non_json_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            output = Path(tmp) / "diagnostics.txt"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["diagnostics", "--save", "--output", str(output), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must end with .json", payload["error"])

    def test_diagnostics_rejects_null_state_file_path(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["diagnostics", "--state-file", "state\x00.json", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_diagnostics_rejects_null_output_path(self) -> None:
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = cli.run(["diagnostics", "--output", "output\x00.json", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_settings_export_rejects_overlong_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    "x" * (cli.MAX_PATH_CHARS + 10),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("too large", payload["error"])

    def test_require_json_path_rejects_overlong_path_before_suffix_validation(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "too large"):
            cli._require_json_path("x" * (cli.MAX_PATH_CHARS + 10), field_name="settings export output")

    def test_settings_export_rejects_non_object_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    "[\"language\", \"de\"]",
                    "--output",
                    str(Path(tmp) / "settings.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings JSON must be an object", payload["error"])

    def test_settings_export_rejects_invalid_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    "{\"language\": \"de\"",
                    "--output",
                    str(Path(tmp) / "settings.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings JSON could not be parsed", payload["error"])

    def test_settings_export_rejects_null_byte_in_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en\\u0000"}',
                    "--output",
                    str(Path(tmp) / "settings.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_settings_export_rejects_non_json_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    str(Path(tmp) / "settings.txt"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must end with .json", payload["error"])

    def test_settings_export_rejects_output_leaf_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            link = Path(tmp) / "link.json"
            link.symlink_to(target)
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    str(link),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must not pass through a symlink", payload["error"])

    def test_settings_import_rejects_overlong_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", "x" * (cli.MAX_PATH_CHARS + 10), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("too large", payload["error"])

    def test_settings_import_rejects_non_json_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(Path(tmp) / "settings.txt"), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must end with .json", payload["error"])

    def test_settings_import_rejects_input_leaf_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.json"
            target.write_text('{"app":"speed-of-cinnamon","version":2,"settings":{},"alarms":{"version":1,"alarms":[],"last_checked_at":""}}\n', encoding="utf-8")
            link = Path(tmp) / "link.json"
            link.symlink_to(target)
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(link), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("must not pass through a symlink", payload["error"])

    def test_settings_import_rejects_null_byte_in_export_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"app":"speed-of-cinnamon","version":2,"settings":{"language":"de\\u0000"}}', encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(path), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("invalid null byte", payload["error"])

    def test_settings_import_rejects_invalid_utf8_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_bytes(b"\xff")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(path), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("settings export could not be read", payload["error"])

    def test_settings_import_skips_invalid_alarm_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                '{"app":"speed-of-cinnamon","version":2,"settings":{"language":"en"},'
                '"alarms":{"version":2,"alarms":[{"id":"good","hour":9,"minute":0,"days":["mon"],"name":"Good"},'
                '{"id":"bad","hour":"not-a-number","minute":0,"days":["mon"],"name":"Bad"}],'
                '"last_checked_at":"2026-06-01T09:00"}}',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", str(path), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["alarms_count"], 1)
        self.assertGreater(payload["settings_count"], 0)

    def test_settings_export_rejects_null_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"en"}',
                    "--output",
                    "settings\x00.json",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_settings_import_rejects_null_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["settings-import", "--input", "settings\x00.json", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_transcribe_file_rejects_invalid_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    "x" * (cli.MAX_PATH_CHARS + 10) + ".wav",
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("audio file path is too long", payload["error"])

    def test_transcribe_file_rejects_missing_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            missing = Path(tmp) / "missing.wav"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(missing),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("audio file is missing or empty", payload["error"])

    def test_transcribe_file_rejects_directory_as_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp)
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("audio path is not a regular file", payload["error"])

    def test_transcribe_file_rejects_null_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    "x\x00.wav",
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_transcribe_file_rejects_null_transcriber_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hi\x00",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_transcribe_file_rejects_control_character_in_transcriber_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hi\nwhoami",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid control character", payload["error"])

    def test_transcribe_file_rejects_escaped_newline_in_transcriber_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf hi\\nwhoami",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid control character", payload["error"])

    def test_transcribe_file_rejects_overlong_personal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--personal-context",
                    "x" * (cli.MAX_TRANSCRIBER_TEXT_CHARS + 10),
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("personal context is too large", payload["error"])

    def test_transcribe_file_rejects_overlong_openai_compatible_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            long_key = "x" * (cli.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "http://127.0.0.1:8000/v1",
                    "--openai-compatible-api-key",
                    long_key,
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible API key is too large", payload["error"])

    def test_transcribe_file_rejects_control_character_in_openai_compatible_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "https://api.openai.com/v1",
                    "--openai-compatible-api-key",
                    "key\\nvalue",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid control character", payload["error"])

    def test_transcribe_file_rejects_overlong_openai_compatible_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            long_model = "x" * (cli.MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-model",
                    long_model,
                    "--openai-compatible-api-key",
                    "secret",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible model is too large", payload["error"])

    def test_transcribe_file_rejects_overlong_openai_compatible_text_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            long_text_model = "x" * (cli.MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1)
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-model",
                    "gpt-4o-transcribe",
                    "--openai-compatible-text-model",
                    long_text_model,
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible text model is too large", payload["error"])

    def test_transcribe_file_rejects_non_http_openai_compatible_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "ftp://127.0.0.1:8000/v1",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("openai-compatible url must use http:// or https://", payload["error"])

    def test_transcribe_file_rejects_openai_compatible_url_with_null_byte(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "input.wav"
            audio.write_bytes(b"audio")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "transcribe-file",
                    str(audio),
                    "--json",
                    "--transcriber",
                    "openai-compatible",
                    "--openai-compatible-url",
                    "http://127.0.0.1:8000/v1\x00",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_stop_rejects_overlong_personal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--personal-context",
                    "x" * (cli.MAX_TRANSCRIBER_TEXT_CHARS + 10),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertIn("personal context is too large", payload["error"])
        self.assertEqual(final_state.status, "processing")

    def test_stop_rejects_invalid_state_audio_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            state_file.write_text('{"status":"processing","audio_path":"x\\u0000.wav"}', encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("state file could not be read", payload["error"])

    def test_remove_file_rejects_null_path(self) -> None:
        self.assertFalse(cli.remove_file("x\x00.wav", suffix=".wav"))

    def test_remove_file_rejects_hardlink_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "recording.wav"
            original.write_bytes(b"audio")
            try:
                hardlink = Path(tmp) / "recording-copy.wav"
                os.link(original, hardlink)
            except OSError:
                return
            self.assertFalse(cli.remove_file(str(hardlink), suffix=".wav"))
            self.assertTrue(hardlink.exists())
            self.assertTrue(original.exists())

    def test_remove_file_rejects_symlink_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            original = Path(tmp) / "recording.wav"
            original.write_bytes(b"audio")
            symlink = Path(tmp) / "recording-link.wav"
            symlink.symlink_to(original)
            self.assertFalse(cli.remove_file(str(symlink), suffix=".wav"))
            self.assertTrue(symlink.is_symlink())

    def test_remove_file_fsyncs_parent_after_delete(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp)
            artifact = recordings / "recording.wav"
            artifact.write_bytes(b"audio")

            with mock.patch("speed_of_cinnamon.cli.os.fsync", side_effect=record_fsync):
                self.assertTrue(cli.remove_file(str(artifact), suffix=".wav", recordings_root=recordings))

            self.assertFalse(artifact.exists())

        self.assertTrue(any(cli.stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_stop_with_invalid_pid_type_is_hardened(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            state_file.write_text(
                json.dumps({
                    "status": "recording",
                    "pid": "not-an-int",
                    "audio_path": str(audio),
                }),
                encoding="utf-8",
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--json",
            ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("state file could not be read", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.finalize_recording", return_value={"status": "done"})
    def test_stop_sets_status_to_finalizing_before_finalization(self, mocked_finalize: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="processing", audio_path=str(audio)))

            args = self._build_finalize_args()
            args.state_file = str(state_file)
            result = cli.command_stop(args)
            final_state = StateStore(state_file).read()

        self.assertEqual(result["status"], "done")
        self.assertEqual(len(mocked_finalize.call_args_list), 1)
        called_state = mocked_finalize.call_args.args[2]
        self.assertEqual(called_state.status, "processing")
        self.assertEqual(final_state.status, "processing")

    def test_finalization_lock_does_not_reclaim_live_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            identity = cli._finalization_lock_identity_for_pid(os.getpid())
            self.assertIsNotNone(identity)
            identity_line = f"{identity}\n" if identity else ""
            lock_path.write_text(f"{os.getpid()}\n{identity_line}", encoding="ascii")
            lock_path.chmod(0o600)

            acquired = cli._acquire_finalization_lock(state_file)

        self.assertIsNone(acquired)

    @mock.patch("speed_of_cinnamon.cli.os.open", wraps=os.open)
    def test_finalization_lock_uses_secure_directory_fd_open(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)

            acquired = cli._acquire_finalization_lock(state_file)
            try:
                self.assertEqual(acquired, lock_path)
                final_opens = [
                    (args, kwargs)
                    for args, kwargs in mocked_open.call_args_list
                    if args and args[0] == lock_path.name
                ]
                self.assertEqual(len(final_opens), 1)
                args, kwargs = final_opens[0]
                self.assertTrue(args[1] & os.O_WRONLY)
                self.assertTrue(args[1] & os.O_CREAT)
                self.assertTrue(args[1] & os.O_EXCL)
                self.assertTrue(args[1] & os.O_NOFOLLOW)
                self.assertIsInstance(kwargs.get("dir_fd"), int)
            finally:
                cli._release_finalization_lock(acquired)

    def test_finalization_lock_release_fsyncs_parent_directory(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._acquire_finalization_lock(state_file)
            self.assertIsNotNone(lock_path)
            with mock.patch("speed_of_cinnamon.cli.os.fsync", side_effect=record_fsync):
                cli._release_finalization_lock(lock_path)

            self.assertFalse(lock_path.exists())

        self.assertTrue(any(cli.stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_finalization_lock_release_survives_parent_close_interrupt(self) -> None:
        lock_path = Path("/probe/state.finalizing")
        current = mock.Mock(st_mode=cli.stat_module.S_IFREG | 0o600, st_nlink=1)

        with (
            mock.patch.object(cli, "ensure_directory_without_following_symlinks", return_value=456),
            mock.patch.object(cli.os, "stat", return_value=current),
            mock.patch.object(cli, "_read_finalization_lock_pid", return_value=os.getpid()),
            mock.patch.object(cli, "_read_finalization_lock_identity", return_value=None),
            mock.patch.object(cli, "_finalization_lock_identity_for_pid", return_value=None),
            mock.patch.object(cli, "_unlink_finalization_lock_at") as mocked_unlink,
            mock.patch.object(cli.os, "close", side_effect=KeyboardInterrupt),
        ):
            cli._release_finalization_lock(lock_path)

        mocked_unlink.assert_called_once_with(456, lock_path, expected_stat=current)

    def test_finalization_lock_acquire_fsyncs_lock_file(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            with mock.patch("speed_of_cinnamon.cli.os.fsync", side_effect=record_fsync):
                lock_path = cli._acquire_finalization_lock(state_file)
            try:
                self.assertIsNotNone(lock_path)
            finally:
                cli._release_finalization_lock(lock_path)

        self.assertTrue(any(cli.stat_module.S_ISREG(mode) for mode in fsync_modes))

    def test_finalization_lock_short_write_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)

            with mock.patch("speed_of_cinnamon.cli.os.write", return_value=0):
                acquired = cli._acquire_finalization_lock(state_file)

            self.assertIsNone(acquired)
            self.assertFalse(lock_path.exists())

    def test_finalization_lock_release_does_not_delete_foreign_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("12345\nforeign-identity\n", encoding="ascii")
            lock_path.chmod(0o600)

            cli._release_finalization_lock(lock_path)

            self.assertTrue(lock_path.exists())
            self.assertEqual(lock_path.read_text(encoding="ascii"), "12345\nforeign-identity\n")

    def test_finalization_lock_rejects_hardlinked_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            backing = Path(tmp) / "foreign-lock"
            backing.write_text("999999999\n", encoding="ascii")
            os.link(backing, lock_path)
            old = time.time() - cli.MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS - 1
            os.utime(lock_path, (old, old))

            acquired = cli._acquire_finalization_lock(state_file)
            self.assertIsNone(acquired)
            self.assertTrue(lock_path.exists())
            self.assertTrue(backing.exists())

    def test_finalization_lock_pid_reader_rejects_hardlinked_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            backing = Path(tmp) / "foreign-lock"
            backing.write_text(f"{os.getpid()}\n", encoding="ascii")
            try:
                os.link(backing, lock_path)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            self.assertIsNone(cli._read_finalization_lock_pid(lock_path))

    def test_finalization_lock_pid_reader_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            os.mkfifo(lock_path)

            self.assertIsNone(cli._read_finalization_lock_pid(lock_path))

    def test_finalization_lock_identity_reader_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("12345\nowner-identity\n", encoding="ascii")
            lock_path.chmod(0o600)

            with mock.patch.object(cli, "MAX_FINALIZATION_LOCK_BYTES", 4):
                identity = cli._read_finalization_lock_identity(lock_path)

        self.assertIsNone(identity)

    def test_finalization_lock_does_not_reclaim_live_foreign_owner_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("12345\nowner-identity\n", encoding="ascii")
            lock_path.chmod(0o600)

            def fake_identity(pid: int) -> str | None:
                return "owner-identity" if pid == 12345 else "self-identity"

            with (
                mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True),
                mock.patch("speed_of_cinnamon.cli._finalization_lock_identity_for_pid", side_effect=fake_identity),
            ):
                acquired = cli._acquire_finalization_lock(state_file)

            self.assertIsNone(acquired)
            self.assertEqual(lock_path.read_text(encoding="ascii"), "12345\nowner-identity\n")

    def test_finalization_lock_does_not_reclaim_stale_live_pid_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")
            lock_path.chmod(0o600)
            old = time.time() - cli.MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS - 10
            os.utime(lock_path, (old, old))

            acquired = cli._acquire_finalization_lock(state_file)

        self.assertIsNone(acquired)

    def test_finalization_lock_does_not_reclaim_live_owner_when_identity_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("12345\nowner-identity\n", encoding="ascii")
            lock_path.chmod(0o600)
            old = time.time() - cli.MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS - 10
            os.utime(lock_path, (old, old))

            with (
                mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True),
                mock.patch("speed_of_cinnamon.cli._finalization_lock_identity_for_pid", return_value=None),
            ):
                acquired = cli._acquire_finalization_lock(state_file)

        self.assertIsNone(acquired)

    def test_finalization_lock_reclaims_dead_owner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("999999999\n", encoding="ascii")
            lock_path.chmod(0o600)

            acquired = cli._acquire_finalization_lock(state_file)
            try:
                self.assertEqual(acquired, lock_path)
                self.assertEqual(lock_path.read_text(encoding="ascii").splitlines()[0], str(os.getpid()))
            finally:
                cli._release_finalization_lock(acquired)

    def test_finalization_lock_does_not_delete_replaced_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("12345\nforeign-identity\n", encoding="ascii")
            lock_path.chmod(0o600)

            def replace_lock(_path: Path) -> int:
                lock_path.unlink()
                lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")
                lock_path.chmod(0o600)
                return 12345

            with (
                mock.patch("speed_of_cinnamon.cli._read_finalization_lock_pid", side_effect=replace_lock),
                mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=False),
            ):
                self.assertIsNone(cli._acquire_finalization_lock(state_file))

            self.assertEqual(lock_path.read_text(encoding="ascii").strip(), str(os.getpid()))

    def test_finalization_lock_reclaims_stale_dead_pid_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("999999999\n", encoding="ascii")
            lock_path.chmod(0o600)
            old = time.time() - cli.MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS - 1
            os.utime(lock_path, (old, old))

            with mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=False):
                acquired = cli._acquire_finalization_lock(state_file)
            try:
                self.assertEqual(acquired, lock_path)
                self.assertEqual(lock_path.read_text(encoding="ascii").splitlines()[0], str(os.getpid()))
            finally:
                cli._release_finalization_lock(acquired)

    def test_finalization_lock_reclaims_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text(f"{os.getpid()}\nother-identity\n", encoding="ascii")
            lock_path.chmod(0o600)

            acquired = cli._acquire_finalization_lock(state_file)
            try:
                self.assertEqual(acquired, lock_path)
                self.assertEqual(lock_path.read_text(encoding="ascii").splitlines()[0], str(os.getpid()))
            finally:
                cli._release_finalization_lock(acquired)

    def test_finalization_lock_reclaims_old_pidless_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("", encoding="ascii")
            lock_path.chmod(0o600)
            old = time.time() - cli.MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS - 1
            os.utime(lock_path, (old, old))

            acquired = cli._acquire_finalization_lock(state_file)
            try:
                self.assertEqual(acquired, lock_path)
                self.assertEqual(lock_path.read_text(encoding="ascii").splitlines()[0], str(os.getpid()))
            finally:
                cli._release_finalization_lock(acquired)

    def test_toggle_rejects_null_personal_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "processing.wav"
            audio.write_bytes(b"audio")
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "toggle",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf transcript",
                    "--post-process-prompt",
                    "ctx\x00",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    @mock.patch("speed_of_cinnamon.cli.command_start")
    def test_toggle_starts_when_idle(self, mocked_start: mock.Mock) -> None:
        mocked_start.return_value = {"status": "recording"}
        with tempfile.TemporaryDirectory() as tmp:
            with redirect_stdout(io.StringIO()):
                code = cli.run(["toggle", "--state-file", str(Path(tmp) / "state.json"), "--json"])
        self.assertEqual(code, 0)
        mocked_start.assert_called_once()

    def test_toggle_does_not_overwrite_error_state_with_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "failed.wav"
            log = recordings / "failed.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="error", audio_path=str(audio), log_path=str(log), error="failed cleanup"))
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.start_recorder", side_effect=AssertionError("recorder started")) as mocked_start,
                redirect_stdout(stdout),
            ):
                code = cli.run(["toggle", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("cleanup is unresolved", payload["error"])
        self.assertTrue(payload["audio_path_present"])
        self.assertTrue(payload["log_path_present"])
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertEqual(final_state.log_path, str(log))
        mocked_start.assert_not_called()

    def test_toggle_finalizes_expired_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "toggle",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf expired-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["transcript"], "expired-transcript")
        self.assertEqual(final_state.status, "done")
        self.assertEqual(final_state.transcript, "expired-transcript")

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    def test_toggle_accepts_transcriber_alias_openai(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=123456789,
                    audio_path=str(audio),
                    language="en",
                )
            )
            with mock.patch("speed_of_cinnamon.cli.remove_file", return_value=True):
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                    code = cli.run([
                        "toggle",
                        "--state-file",
                        str(state_file),
                        "--insert-method",
                        "none",
                        "--transcriber",
                        "openai",
                        "--json",
                    ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    @mock.patch("speed_of_cinnamon.cli.transcribe", return_value="ok")
    def test_toggle_accepts_transcriber_alias_faster_whisper(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=123456789,
                    audio_path=str(audio),
                    language="en",
                )
            )
            with mock.patch("speed_of_cinnamon.cli.remove_file", return_value=True):
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                    code = cli.run([
                        "toggle",
                        "--state-file",
                        str(state_file),
                        "--insert-method",
                        "none",
                        "--transcriber",
                        "faster-whisper",
                        "--json",
                    ])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        mocked_transcribe.assert_called_once_with(
            audio_path=audio,
            language="en",
            text_path=mock.ANY,
            command_template="",
            backend="faster-whisper",
            whisper_model="",
            personal_context="",
            vocabulary="",
        )

    def test_toggle_finalizes_recording_with_saved_language(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=999999999, audio_path=str(audio), language="de"))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "toggle",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf gespeicherte-sprache",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["language"], "de")
        self.assertEqual(final_state.language, "de")

    def test_finalize_discards_recording_artifacts_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            transcript_root = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf private-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "done")
        self.assertFalse(payload["recording_artifacts_kept"])
        self.assertTrue(payload["audio_deleted"])
        self.assertTrue(payload["log_deleted"])
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertEqual(final_state.audio_path, "")
        self.assertEqual(final_state.log_path, "")

    def test_finalize_can_keep_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf retained-transcript",
                    "--keep-recording-artifacts",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            log_exists = log.exists()
            self.assertEqual(code, 0)
            self.assertEqual(payload["status"], "done")
            self.assertTrue(payload["recording_artifacts_kept"])
            self.assertFalse(payload["audio_deleted"])
            self.assertFalse(payload["log_deleted"])
            self.assertTrue(log_exists)
            final_audio_path = Path(final_state.audio_path)
            self.assertIn(final_audio_path.suffix, {".wav", ".flac"})
            self.assertTrue(final_audio_path.exists())
            if final_audio_path.suffix == ".flac":
                self.assertFalse(audio.exists())
            self.assertEqual(final_state.log_path, str(log))

    def test_finalize_silent_recording_keeps_artifacts_if_state_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent")),
                mock.patch.object(store, "update", side_effect=RuntimeError("state write failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "state write failed"):
                    cli.finalize_recording(args, store, store.read())

            self.assertTrue(audio.exists())
            self.assertTrue(log.exists())

    def test_finalize_silent_recording_deletes_original_log_after_state_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent")),
            ):
                payload = cli.finalize_recording(args, store, store.read())

            self.assertEqual(payload["status"], "done")
            self.assertTrue(payload["audio_deleted"])
            self.assertTrue(payload["log_deleted"])
            self.assertFalse(audio.exists())
            self.assertFalse(log.exists())

    def test_finalize_silent_recording_marks_error_when_artifact_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent")),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to delete recording artifact"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertEqual(final_state.audio_path, str(audio))
            self.assertEqual(final_state.log_path, str(log))
            self.assertFalse(final_state.transcript_path)

    def test_finalize_silent_cleanup_failure_does_not_persist_done_if_error_state_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            real_update = store.update
            statuses: list[object] = []

            def fake_update(**kwargs: object) -> RecordingState:
                statuses.append(kwargs.get("status"))
                if kwargs.get("status") == "done":
                    raise AssertionError("done written before cleanup completed")
                if kwargs.get("status") == "error":
                    raise RuntimeError("error write failed")
                return real_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent")),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "error write failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertNotIn("done", statuses)
            self.assertEqual(final_state.status, "finalizing")
            self.assertEqual(final_state.audio_path, str(audio))
            self.assertEqual(final_state.log_path, str(log))

    def test_finalize_silent_artifact_cap_failure_does_not_persist_done_if_error_state_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            real_update = store.update
            statuses: list[object] = []

            def fake_update(**kwargs: object) -> RecordingState:
                statuses.append(kwargs.get("status"))
                if kwargs.get("status") == "done":
                    raise AssertionError("done written before artifact-cap cleanup completed")
                if kwargs.get("status") == "error":
                    raise RuntimeError("error write failed")
                return real_update(**kwargs)

            failed_cleanup = {
                "planned_paths": [],
                "deleted_paths": [],
                "failed_paths": [str(recordings_root / "stale.wav")],
                "skipped_active_paths": [],
            }

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(True, True, 2.0, 2.0, 0.0, 2.0, "silent")),
                mock.patch("speed_of_cinnamon.cli._enforce_recording_artifact_cap", return_value=failed_cleanup),
            ):
                with self.assertRaisesRegex(RuntimeError, "error write failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertNotIn("done", statuses)
            self.assertEqual(final_state.status, "finalizing")
            self.assertEqual(final_state.audio_path, str(audio))

    def test_finalize_non_silent_keeps_artifacts_if_done_and_error_state_updates_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch.object(store, "update", side_effect=[RuntimeError("done write failed"), RuntimeError("error write failed")]),
            ):
                with self.assertRaisesRegex(RuntimeError, "error write failed"):
                    cli.finalize_recording(args, store, store.read())

            self.assertTrue(audio.exists())
            self.assertTrue(log.exists())

    def test_finalize_non_silent_marks_error_when_artifact_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to delete recording artifact"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertEqual(final_state.audio_path, str(audio))
            self.assertEqual(final_state.log_path, str(log))

    def test_finalize_removes_partial_cleanup_backup_when_backup_copy_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)

            def partial_copy(_source: Path, destination: Path, **_kwargs: object) -> None:
                destination.write_bytes(b"partial cleanup backup")
                raise OSError("backup storage failed")

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.shutil.copy2", side_effect=partial_copy),
            ):
                with self.assertRaisesRegex(RuntimeError, "backup storage failed"):
                    cli.finalize_recording(args, store, store.read())

            self.assertEqual(list(recordings_root.glob(".cleanup.*.bak")), [])

    def test_finalize_restores_artifacts_when_delete_reports_post_delete_failure(self) -> None:
        real_unlink = cli._unlink_regular_leaf_with_parent_fsync

        def delete_then_report_failure(
            path: Path,
            *,
            field_name: str,
            expected_stat: object = None,
        ) -> bool:
            real_unlink(path, field_name=field_name, expected_stat=expected_stat)  # type: ignore[arg-type]
            if field_name == "recording artifact":
                raise RuntimeError("parent fsync failed after unlink")
            return True

        for silent in (False, True):
            with self.subTest(silent=silent), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
                recordings_root.mkdir(parents=True)
                audio = recordings_root / "recording.wav"
                log = recordings_root / "recording.log"
                audio.write_bytes(b"audio-before-cleanup")
                log.write_bytes(b"log-before-cleanup")
                state_file = tmp_path / "state.json"
                store = StateStore(state_file)
                store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
                args = self._build_finalize_args(keep_recording_artifacts=False)
                with (
                    mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                    mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                    mock.patch(
                        "speed_of_cinnamon.cli.detect_silent_recording",
                        return_value=cli.SilenceDetectionResult(
                            silent,
                            silent,
                            2.0,
                            2.0 if silent else 1.0,
                            0.0 if silent else 1.0,
                            2.0 if silent else 0.1,
                            "silent" if silent else "not silent",
                        ),
                    ),
                    mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                    mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                    mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                    mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                    mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                    mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                    mock.patch(
                        "speed_of_cinnamon.cli._unlink_regular_leaf_with_parent_fsync",
                        side_effect=delete_then_report_failure,
                    ),
                ):
                    with self.assertRaisesRegex(RuntimeError, "failed to delete recording artifact"):
                        cli.finalize_recording(args, store, store.read())

                final_state = store.read()
                self.assertEqual(final_state.status, "error")
                self.assertEqual(final_state.audio_path, str(audio))
                self.assertEqual(final_state.log_path, str(log))
                self.assertEqual(audio.read_bytes(), b"audio-before-cleanup")
                self.assertEqual(log.read_bytes(), b"log-before-cleanup")
                self.assertEqual(list(recordings_root.glob(".cleanup.*.bak")), [])

    def test_finalize_non_silent_cleanup_failure_does_not_persist_done_if_error_state_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            real_update = store.update
            statuses: list[object] = []

            def fake_update(**kwargs: object) -> RecordingState:
                statuses.append(kwargs.get("status"))
                if kwargs.get("status") == "done":
                    raise AssertionError("done written before cleanup completed")
                if kwargs.get("status") == "error":
                    raise RuntimeError("error write failed")
                return real_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "error write failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertNotIn("done", statuses)
            self.assertEqual(final_state.status, "finalizing")
            self.assertEqual(final_state.audio_path, str(audio))
            self.assertEqual(final_state.log_path, str(log))

    def test_finalize_clears_deleted_artifact_paths_when_done_write_fails_after_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            real_update = store.update

            def fake_update(**kwargs: object) -> RecordingState:
                if kwargs.get("status") == "done":
                    raise RuntimeError("done write failed")
                return real_update(**kwargs)

            def fake_remove(path_value: str | None, **_kwargs: object) -> bool:
                if not path_value:
                    return False
                try:
                    Path(path_value).unlink()
                except FileNotFoundError:
                    pass
                return True

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.remove_file", side_effect=fake_remove),
            ):
                with self.assertRaisesRegex(RuntimeError, "done write failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertEqual(final_state.audio_path, "")
            self.assertEqual(final_state.log_path, "")
            self.assertFalse(audio.exists())
            self.assertFalse(log.exists())

    def test_finalize_transient_cleanup_failure_does_not_persist_done_if_error_state_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            real_update = store.update
            statuses: list[object] = []

            def fake_update(**kwargs: object) -> RecordingState:
                statuses.append(kwargs.get("status"))
                if kwargs.get("status") == "done":
                    raise AssertionError("done written before cleanup completed")
                if kwargs.get("status") == "error":
                    raise RuntimeError("error write failed")
                return real_update(**kwargs)

            failed_cleanup = {
                "planned_paths": [],
                "deleted_paths": [],
                "failed_paths": [str(tmp_path / "speed-of-cinnamon" / "transcripts" / "stale.tmp")],
                "skipped_active_paths": [],
            }

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prune_stale_transient_transcripts", return_value=failed_cleanup),
            ):
                with self.assertRaisesRegex(RuntimeError, "error write failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertNotIn("done", statuses)
            self.assertEqual(final_state.status, "finalizing")
            self.assertEqual(final_state.audio_path, str(audio))

    def test_finalize_removes_written_transcript_if_insert_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            transcript_root = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", side_effect=RuntimeError("paste failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
            ):
                with self.assertRaisesRegex(RuntimeError, "paste failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertEqual(final_state.transcript, "")
            self.assertEqual(final_state.transcript_path, "")
            self.assertEqual(list(transcript_root.glob("*.txt")), [])

    def test_finalize_removes_written_transcript_even_if_error_state_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            transcript_root = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", side_effect=RuntimeError("paste failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch.object(store, "update", side_effect=RuntimeError("error write failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "error write failed"):
                    cli.finalize_recording(args, store, store.read())

            self.assertEqual(list(transcript_root.glob("*.txt")), [])

    def test_finalize_does_not_clear_transcript_state_if_transcript_delete_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            transcript_root = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            update_calls: list[dict[str, object]] = []
            original_update = store.update

            def tracking_update(**kwargs: object) -> RecordingState:
                update_calls.append(kwargs)
                return original_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", side_effect=RuntimeError("paste failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch.object(store, "update", side_effect=tracking_update),
                mock.patch("speed_of_cinnamon.cli._unlink_regular_leaf_with_parent_fsync", side_effect=RuntimeError("unlink failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to delete transcript file"):
                    cli.finalize_recording(args, store, store.read())

            self.assertTrue(list(transcript_root.glob("*.txt")))
            error_calls = [call for call in update_calls if call.get("status") == "error"]
            self.assertEqual(len(error_calls), 1)
            self.assertIn("failed to delete transcript file", str(error_calls[0]["error"]))
            self.assertFalse(any("transcript_path" in call and call["transcript_path"] == "" for call in update_calls))

    def test_remove_transcript_file_rejects_parent_traversal_outside_transcripts_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcript_root = tmp_path / "speed-of-cinnamon" / "transcripts"
            transcript_root.mkdir(parents=True)
            outside = tmp_path / "speed-of-cinnamon" / "outside.txt"
            outside.write_text("do not delete", encoding="utf-8")
            traversal = transcript_root / ".." / "outside.txt"

            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}):
                with self.assertRaisesRegex(RuntimeError, "refusing to delete transcript outside transcript directory"):
                    cli._remove_transcript_file(traversal)

            self.assertTrue(outside.exists())

    def test_remove_transcript_file_fsyncs_parent_after_delete(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            transcript_root = Path(tmp) / "speed-of-cinnamon" / "transcripts"
            transcript_root.mkdir(parents=True)
            transcript = transcript_root / "entry.txt"
            transcript.write_text("secret\n", encoding="utf-8")

            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.os.fsync", side_effect=record_fsync),
            ):
                self.assertTrue(cli._remove_transcript_file(transcript))

            self.assertFalse(transcript.exists())

        self.assertTrue(any(cli.stat_module.S_ISDIR(mode) for mode in fsync_modes))

    def test_finalize_keeps_artifact_paths_in_error_state_when_error_cleanup_delete_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", side_effect=cli.RecorderError("skip encode")),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", side_effect=RuntimeError("prepare failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "prepare failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertEqual(final_state.audio_path, str(audio))
            self.assertEqual(final_state.log_path, str(log))

    def test_finalize_can_keep_stabilized_trimmed_recording_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            temp_trimmed = recordings_root / "recording.trimmed-keep.flac"
            original.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            temp_trimmed.write_bytes(b"trimmed-audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=temp_trimmed),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            final_audio = Path(final_state.audio_path)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["recording_artifacts_kept"], True)
            self.assertEqual(final_audio.name, "recording.flac")
            self.assertTrue(final_audio.exists())
            self.assertFalse(temp_trimmed.exists())
            self.assertFalse(original.exists())
            self.assertEqual(final_state.audio_path, str(final_audio))

    def test_finalize_can_keep_stabilized_encoded_recording_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            temp_encoded = recordings_root / "recording.encoded-final.flac"
            original.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            temp_encoded.write_bytes(b"encoded-audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.reencode_recording_to_flac", return_value=temp_encoded),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
            ):
                payload = cli.finalize_recording(args, store, store.read())
            final_state = store.read()
            final_audio = Path(final_state.audio_path)
            self.assertEqual(payload["status"], "done")
            self.assertEqual(payload["recording_artifacts_kept"], True)
            self.assertEqual(final_audio.name, "recording.flac")
            self.assertTrue(final_audio.exists())
            self.assertFalse(temp_encoded.exists())
            self.assertFalse(original.exists())

    def test_finalize_removes_stabilized_recording_artifact_if_state_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            temp_trimmed = recordings_root / "recording.trimmed-fails.flac"
            final_audio = recordings_root / "recording.flac"
            original.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            temp_trimmed.write_bytes(b"trimmed-audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=temp_trimmed),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch.object(
                    store,
                    "update",
                    side_effect=[RuntimeError("state write failed"), RecordingState(status="error")],
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "state write failed"):
                    cli.finalize_recording(args, store, store.read())

            self.assertFalse(final_audio.exists())
            self.assertFalse(temp_trimmed.exists())
            self.assertTrue(original.exists())

    def test_finalize_removes_plaintext_recording_artifacts_when_kept_recording_encryption_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.flac"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            args.artifact_encryption = "passphrase"
            env = {
                "XDG_CACHE_HOME": tmp,
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: artifact_crypto._b64encode(bytes(range(32))),
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli._encrypt_kept_recording_artifact", side_effect=RuntimeError("encryption failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "encryption failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertEqual(final_state.audio_path, "")
            self.assertEqual(final_state.log_path, "")
            self.assertFalse(audio.exists())
            self.assertFalse(log.exists())

    def test_cleanup_counts_and_deletes_stable_final_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            temp_trimmed = recordings_root / "recording.trimmed-keep.flac"
            original.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            temp_trimmed.write_bytes(b"trimmed-audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(original), log_path=str(log)))
            finalize_args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=temp_trimmed),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
            ):
                cli.finalize_recording(finalize_args, store, store.read())

            finalized = Path(store.read().audio_path)
            self.assertTrue(finalized.exists())
            self.assertFalse(temp_trimmed.exists())
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli.run([
                        "cleanup",
                        "--keep-transcripts",
                        "0",
                        "--keep-recordings",
                        "0",
                        "--dry-run",
                        "--json",
                    ])
            payload = json.loads(stdout.getvalue())
            dry_run_payload = payload
            self.assertEqual(code, 0)
            self.assertEqual(dry_run_payload["would_delete_recordings"], 1)
            self.assertGreaterEqual(dry_run_payload["would_delete_path_count"], 1)
            self.assertEqual(dry_run_payload["would_delete_paths"], [])
            self.assertNotIn(str(finalized), json.dumps(dry_run_payload))
            self.assertTrue(finalized.exists())

            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli.run(["cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"])
                payload = json.loads(stdout.getvalue())
                self.assertEqual(code, 0)
                self.assertEqual(payload["deleted_recordings"], 1)
                self.assertEqual(payload["deleted_path_count"], 3)
                self.assertEqual(payload["deleted_paths"], [])
                self.assertNotIn(str(finalized), json.dumps(payload))
                self.assertFalse(finalized.exists())

    def test_finalize_encrypts_kept_recording_and_transcript_with_passphrase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            temp_trimmed = recordings_root / "recording.trimmed-keep.flac"
            original.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            temp_trimmed.write_bytes(b"trimmed-audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            args.artifact_encryption = "passphrase"
            args.confirm_plaintext_output = True
            strong_passphrase = artifact_crypto._b64encode(bytes(range(32)))
            env = {
                "XDG_CACHE_HOME": tmp,
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: strong_passphrase,
            }
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=temp_trimmed),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            encrypted_audio = Path(final_state.audio_path)
            encrypted_transcript = Path(final_state.transcript_path)
            final_log_path = final_state.log_path
            with mock.patch.dict(os.environ, env, clear=False):
                decrypted_audio = artifact_crypto.read_decrypted_bytes_from_file(
                    encrypted_audio,
                    kind="recording",
                    field_name="recording audio file",
                )
                decrypted_transcript = artifact_crypto.read_decrypted_bytes_from_file(
                    encrypted_transcript,
                    kind="transcript",
                    field_name="transcript file",
                ).decode("utf-8")
            original_exists = original.exists()
            temp_trimmed_exists = temp_trimmed.exists()
            log_exists = log.exists()

        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["transcript"], "transcript")
        self.assertTrue(payload["transcript_encrypted"])
        self.assertTrue(payload["recording_encrypted"])
        self.assertTrue(encrypted_audio.name.endswith(".flac.socenc"))
        self.assertTrue(encrypted_transcript.name.endswith(".txt.socenc"))
        self.assertFalse(original_exists)
        self.assertFalse(temp_trimmed_exists)
        self.assertFalse(log_exists)
        self.assertEqual(final_log_path, "")
        self.assertEqual(decrypted_audio, b"trimmed-audio")
        self.assertEqual(decrypted_transcript, "transcript\n")
        self.assertEqual(final_state.transcript, "")

    def test_finalize_rejects_non_boolean_keep_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = argparse.Namespace(
                language="en",
                transcriber="command",
                transcriber_command="printf transcript",
                whisper_model="",
                post_process_command="",
                post_process_backend="command",
                post_process_prompt="",
                openai_compatible_url=cli.DEFAULT_OPENAI_COMPATIBLE_URL,
                ollama_url=cli.DEFAULT_OLLAMA_URL,
                ollama_model="",
                openai_compatible_model="",
                personal_context="",
                vocabulary="",
                append_space=False,
                sanitize_special_chars=False,
                typing_delay_ms=0,
                insert_method="none",
                keep_recording_artifacts="true",
                skip_silent_auto_relisten=False,
            )
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
            ):
                with self.assertRaises(RuntimeError) as context:
                    cli.finalize_recording(args, store, store.read())
                self.assertIn("must be a boolean", str(context.exception))
            final_state = store.read()
            self.assertEqual(final_state.status, "processing")
            self.assertTrue(audio.exists())
            self.assertTrue(log.exists())

    def _build_finalize_args(
        self,
        *,
        keep_recording_artifacts: bool | str = True,
        append_space: bool = False,
        sanitize_special_chars: bool = False,
        insert_method: str = "none",
        confirm_plaintext_output: bool = True,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            language="en",
            transcriber="command",
            transcriber_command="printf transcript",
            whisper_model="",
            post_process_command="",
            post_process_backend="command",
            post_process_prompt="",
            openai_compatible_url=cli.DEFAULT_OPENAI_COMPATIBLE_URL,
            ollama_url=cli.DEFAULT_OLLAMA_URL,
            ollama_model="",
            openai_compatible_model="",
            personal_context="",
            vocabulary="",
            append_space=append_space,
            sanitize_special_chars=sanitize_special_chars,
            typing_delay_ms=0,
            insert_method=insert_method,
            keep_recording_artifacts=keep_recording_artifacts,
            skip_silent_auto_relisten=False,
            confirm_plaintext_output=confirm_plaintext_output,
        )

    def test_finalize_removes_cleanup_backups_after_done_state_persist_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            real_update = store.update

            def fail_done(*update_args: object, **update_kwargs: object) -> RecordingState:
                if update_kwargs.get("status") == "done":
                    raise RuntimeError("state backend unavailable")
                return real_update(*update_args, **update_kwargs)

            silence = cli.SilenceDetectionResult(False, False, 4.0, 0.0, 3.0, 0.0, "speech detected")
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(cli, "validate_audio_file", return_value=audio),
                mock.patch.object(cli, "detect_silent_recording", return_value=silence),
                mock.patch.object(cli, "transcribe", return_value="transcript"),
                mock.patch.object(cli, "trim_recording_silence", return_value=audio),
                mock.patch.object(store, "update", side_effect=fail_done),
            ):
                with self.assertRaisesRegex(RuntimeError, "state backend unavailable"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            backup_files = sorted(recordings_root.glob(".cleanup.*.bak"))

        self.assertEqual(final_state.status, "error")
        self.assertFalse(audio.exists())
        self.assertFalse(log.exists())
        self.assertEqual(backup_files, [])

    def test_finalize_persists_multiline_transcript_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "speech.wav"
            log = recordings_root / "speech.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            silence = cli.SilenceDetectionResult(False, False, 3.0, 0.0, 2.5, 0.0, "speech detected")
            transcript = "hello\nworld"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value=transcript),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True) as mocked_insert,
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["transcript"], transcript)
        self.assertEqual(final_state.transcript, transcript)
        mocked_insert.assert_called_once_with(transcript, "none", 0)

    def test_finalize_redacts_plaintext_transcript_and_path_without_confirm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "speech.wav"
            log = recordings_root / "speech.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False, confirm_plaintext_output=False)
            silence = cli.SilenceDetectionResult(False, False, 3.0, 0.0, 2.5, 0.0, "speech detected")
            transcript = "private transcript"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value=transcript),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
        self.assertEqual(payload["status"], "done")
        self.assertEqual(payload["transcript"], "")
        self.assertTrue(payload["transcript_output_redacted"])
        self.assertTrue(payload["transcript_path_present"])
        self.assertNotIn("transcript_path", payload)
        self.assertEqual(final_state.transcript, transcript)
        self.assertTrue(final_state.transcript_path)

    def test_finalize_skips_silent_auto_relisten_without_transcribing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "silent.wav"
            log = recordings_root / "silent.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            args.skip_silent_auto_relisten = True
            silence = cli.SilenceDetectionResult(True, True, 3.0, 3.0, 0.0, 0.0, "silent recording")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript") as mocked_transcribe,
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True) as mocked_insert,
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["silence_detected"])
        self.assertEqual(payload["transcript"], "")
        self.assertEqual(payload["speech_duration_seconds"], 0.0)
        self.assertFalse(audio.exists())
        self.assertFalse(log.exists())
        self.assertEqual(final_state.transcript, "")
        mocked_transcribe.assert_not_called()
        mocked_insert.assert_not_called()

    def test_finalize_keeps_silent_auto_relisten_artifacts_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "silent.wav"
            log = recordings_root / "silent.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            args.skip_silent_auto_relisten = True
            silence = cli.SilenceDetectionResult(True, True, 3.0, 3.0, 0.0, 0.0, "silent recording")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript") as mocked_transcribe,
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True) as mocked_insert,
            ):
                payload = cli.finalize_recording(args, store, store.read())

            self.assertEqual(payload["status"], "done")
            self.assertTrue(payload["silence_detected"])
            self.assertEqual(payload["transcript"], "")
            self.assertTrue(audio.exists())
            self.assertTrue(log.exists())
            mocked_transcribe.assert_not_called()
            mocked_insert.assert_not_called()

    def test_finalize_encrypts_kept_silent_recording_with_passphrase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "silent.wav"
            log = recordings_root / "silent.log"
            audio.write_bytes(b"silent-audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            args.skip_silent_auto_relisten = True
            args.artifact_encryption = "passphrase"
            strong_passphrase = artifact_crypto._b64encode(bytes(range(32)))
            env = {
                "XDG_CACHE_HOME": tmp,
                "XDG_STATE_HOME": tmp,
                artifact_crypto.PASSPHRASE_ENV: strong_passphrase,
            }
            silence = cli.SilenceDetectionResult(True, True, 3.0, 3.0, 0.0, 0.0, "silent recording")
            with (
                mock.patch.dict(os.environ, env, clear=False),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript") as mocked_transcribe,
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True) as mocked_insert,
            ):
                payload = cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            encrypted_audio = Path(final_state.audio_path)
            final_log_path = final_state.log_path
            with mock.patch.dict(os.environ, env, clear=False):
                decrypted_audio = artifact_crypto.read_decrypted_bytes_from_file(
                    encrypted_audio,
                    kind="recording",
                    field_name="recording audio file",
                )
            audio_exists = audio.exists()
            log_exists = log.exists()

        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["silence_detected"])
        self.assertEqual(payload["transcript"], "")
        self.assertEqual(payload["artifact_encryption"], "passphrase")
        self.assertEqual(payload["recording_encryption"], "passphrase")
        self.assertTrue(payload["recording_encrypted"])
        self.assertTrue(encrypted_audio.name.endswith(".wav.socenc"))
        self.assertEqual(decrypted_audio, b"silent-audio")
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertEqual(final_log_path, "")
        mocked_transcribe.assert_not_called()
        mocked_insert.assert_not_called()

    def test_finalize_skips_silent_initial_recording_without_transcribing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "silent.wav"
            log = recordings_root / "silent.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            silence = cli.SilenceDetectionResult(True, True, 3.0, 3.0, 0.0, 0.0, "silent recording")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript") as mocked_transcribe,
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True) as mocked_insert,
            ):
                payload = cli.finalize_recording(args, store, store.read())

        self.assertEqual(payload["status"], "done")
        self.assertTrue(payload["silence_detected"])
        self.assertEqual(payload["transcript"], "")
        mocked_transcribe.assert_not_called()
        mocked_insert.assert_not_called()

    def test_finalize_trims_leading_silence_before_transcribing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "speech.wav"
            log = recordings_root / "speech.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            trimmed = recordings_root / "speech.trimmed.flac"
            trimmed.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args()
            silence = cli.SilenceDetectionResult(True, False, 4.0, 1.0, 3.0, 1.0, "speech detected")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=trimmed) as mocked_trim,
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript") as mocked_transcribe,
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
            ):
                payload = cli.finalize_recording(args, store, store.read())

        self.assertEqual(payload["status"], "done")
        self.assertNotIn("silence_detected", payload)
        mocked_trim.assert_called_once_with(audio)
        self.assertEqual(mocked_transcribe.call_args.kwargs["audio_path"], trimmed)

    def test_finalize_transcribes_when_auto_relisten_silence_detection_fails_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "speech.wav"
            log = recordings_root / "speech.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args()
            args.skip_silent_auto_relisten = True
            silence = cli.SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, "ffmpeg missing")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=silence),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript") as mocked_transcribe,
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
            ):
                payload = cli.finalize_recording(args, store, store.read())

        self.assertEqual(payload["status"], "done")
        self.assertNotIn("silence_detected", payload)
        mocked_transcribe.assert_called_once()

    def test_finalize_rejects_non_boolean_append_space(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(append_space="yes")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
            ):
                with self.assertRaisesRegex(RuntimeError, "append_space must be a boolean"):
                    cli.finalize_recording(args, store, store.read())

    def test_finalize_rejects_non_boolean_sanitize_special_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(sanitize_special_chars="yes")
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
            ):
                with self.assertRaisesRegex(RuntimeError, "sanitize_special_chars must be a boolean"):
                    cli.finalize_recording(args, store, store.read())

    def test_cancel_does_not_delete_artifacts_outside_recordings_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "outside.wav"
            log = tmp_path / "outside.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=999999999, audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["message"], "failed to discard recording artifacts")
        self.assertTrue(payload["discarded_audio_path_present"])
        self.assertNotIn("discarded_audio_path", payload)
        self.assertFalse(payload["audio_deleted"])
        self.assertFalse(payload["log_deleted"])
        self.assertTrue(payload["transcript_deleted"])
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)

    def test_cancel_with_only_invalid_audio_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "outside.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="error", audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = StateStore(state_file).read()
            audio_exists = audio.exists()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["discarded_audio_path_present"])
        self.assertFalse(payload["audio_deleted"])
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertTrue(audio_exists)

    def test_cancel_marks_state_finalizing_without_error_during_artifact_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            observed_state: dict[str, object] = {}
            original_remove_recording_artifact = cli._remove_recording_artifact

            def fake_remove_recording_artifact(path_value: str | None) -> bool:
                current = store.read()
                observed_state["status"] = current.status
                observed_state["error"] = current.error
                return original_remove_recording_artifact(path_value)

            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._remove_recording_artifact", side_effect=fake_remove_recording_artifact),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["discarded_audio_path_present"])
        self.assertNotIn("discarded_audio_path", payload)
        self.assertEqual(observed_state["status"], "finalizing")
        self.assertEqual(observed_state["error"], "")

    def test_cancel_retries_finalizing_state_without_lock_discards_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertFalse(audio.exists())
        self.assertFalse(log.exists())
        self.assertEqual(final_state.status, "idle")

    def test_cancel_treats_missing_safe_artifacts_as_already_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings.mkdir(parents=True)
            transcripts.mkdir(parents=True)
            audio = recordings / "missing.wav"
            log = recordings / "missing.log"
            transcript = transcripts / "missing.txt"
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="finalizing",
                    audio_path=str(audio),
                    log_path=str(log),
                    transcript_path=str(transcript),
                )
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["audio_deleted"])
        self.assertTrue(payload["log_deleted"])
        self.assertTrue(payload["transcript_deleted"])
        self.assertEqual(final_state.status, "idle")
        self.assertFalse(final_state.audio_path)
        self.assertFalse(final_state.log_path)
        self.assertFalse(final_state.transcript_path)
        self.assertFalse(final_state.transcript)

    def test_cancel_rejects_recording_symlink_without_deleting_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            target = recordings / "target.wav"
            symlink = recordings / "recording.wav"
            target.write_bytes(b"audio")
            symlink.symlink_to(target)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(symlink)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            symlink_is_symlink = symlink.is_symlink()
            target_exists = target.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["audio_deleted"])
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.audio_path, str(symlink))
        self.assertTrue(symlink_is_symlink)
        self.assertTrue(target_exists)

    def test_cancel_missing_outside_artifacts_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "missing.wav"
            log = tmp_path / "missing.log"
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["message"], "failed to discard recording artifacts")
        self.assertEqual(payload["exit_code"], 0)
        self.assertFalse(payload["audio_deleted"])
        self.assertFalse(payload["log_deleted"])
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertEqual(final_state.log_path, str(log))

    def test_cancel_cleanup_failure_removes_rollback_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            backup_files = sorted(recordings.glob(".*.cleanup.bak"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["message"], "failed to discard recording artifacts")
        self.assertEqual(final_state.status, "error")
        self.assertEqual(backup_files, [])

    def test_cancel_missing_outside_transcript_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcript = tmp_path / "missing.txt"
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", transcript_path=str(transcript)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["message"], "failed to discard recording artifacts")
        self.assertFalse(payload["transcript_deleted"])
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.transcript_path, str(transcript))

    def test_cancel_transcript_symlink_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            transcripts.mkdir(parents=True)
            target = tmp_path / "outside.txt"
            target.write_text("secret", encoding="utf-8")
            transcript = transcripts / "link.txt"
            transcript.symlink_to(target)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", transcript_path=str(transcript)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            transcript_is_symlink = transcript.is_symlink()
            target_exists = target.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["transcript_deleted"])
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.transcript_path, str(transcript))
        self.assertTrue(transcript_is_symlink)
        self.assertTrue(target_exists)

    def test_cancel_transcript_hardlink_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            transcripts.mkdir(parents=True)
            source = transcripts / "source.txt"
            source.write_text("secret", encoding="utf-8")
            transcript = transcripts / "hardlink.txt"
            os.link(source, transcript)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", transcript_path=str(transcript)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            source_exists = source.exists()
            transcript_exists = transcript.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "error")
        self.assertFalse(payload["transcript_deleted"])
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.transcript_path, str(transcript))
        self.assertTrue(source_exists)
        self.assertTrue(transcript_exists)

    def test_cancel_clears_error_state_without_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="error", error="cleanup already resolved"))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(final_state.status, "idle")
        self.assertEqual(final_state.error, "")

    def test_cancel_removes_transcript_artifact_before_idle_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings.mkdir(parents=True)
            transcripts.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            transcript = transcripts / "recording.txt"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            transcript.write_text("secret", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="error", audio_path=str(audio), log_path=str(log), transcript_path=str(transcript)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["audio_deleted"])
        self.assertTrue(payload["log_deleted"])
        self.assertTrue(payload["transcript_deleted"])
        self.assertFalse(transcript.exists())
        self.assertEqual(final_state.transcript_path, "")

    def test_cancel_error_state_only_keeps_failed_artifact_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings.mkdir(parents=True)
            transcripts.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            transcript = transcripts / "recording.txt"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            transcript.write_text("secret", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="error", audio_path=str(audio), log_path=str(log), transcript_path=str(transcript)))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._remove_recording_artifact", return_value=True),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
                mock.patch("speed_of_cinnamon.cli._remove_transcript_file", return_value=True),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertTrue(payload["audio_deleted"])
        self.assertFalse(payload["log_deleted"])
        self.assertTrue(payload["transcript_deleted"])
        self.assertFalse(final_state.audio_path)
        self.assertEqual(final_state.log_path, str(log))
        self.assertEqual(final_state.transcript_path, "")

    def test_start_does_not_overwrite_expired_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            audio = tmp_path / "expired.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "recorded")
        self.assertTrue(payload["audio_path_present"])
        self.assertNotIn("audio_path", payload)

    def test_start_does_not_overwrite_pending_recording_states(self) -> None:
        for status in ("recorded", "processing"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                state_file = Path(tmp) / "state.json"
                store = StateStore(state_file)
                store.write(
                    RecordingState(
                        status=status,
                        audio_path=f"recordings/{status}.wav",
                        log_path=f"recordings/{status}.log",
                    )
                )
                args = argparse.Namespace(
                    max_seconds=30,
                    input_device="",
                    recorder="auto",
                    language="en",
                )
                with mock.patch.object(cli, "choose_recorder") as mocked_choose:
                    result = cli._command_start_locked(args, store)

                self.assertEqual(result["status"], status)
                self.assertIn("previous recording is pending", result["message"])
                self.assertTrue(result["audio_path_present"])
                self.assertTrue(result["log_path_present"])
                self.assertEqual(store.read().status, status)
                mocked_choose.assert_not_called()

    def test_status_includes_microphone_level_for_recording_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "active.wav"
            self._write_wav(audio, [0, 8192, -16384])
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["status", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "recorded")
        self.assertEqual(payload["microphone_level"]["percent"], 50)
        self.assertEqual(payload["microphone_level"]["source"], "recording-file")

    def test_status_redacts_microphone_level_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "active.wav"
            self._write_wav(audio, [0, 8192, -16384])
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(RecordingState(status="recording", pid=999999999, audio_path=str(audio)))
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.read_recording_level", side_effect=cli.RecorderError("token abc123")),
                redirect_stdout(stdout),
            ):
                code = cli.run(["status", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertNotIn("token abc123", payload["microphone_level"]["detail"])
        self.assertNotIn("abc123", payload["microphone_level"]["detail"])

    def test_recording_level_payload_redacts_errors_for_direct_callers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "active.wav"
            self._write_wav(audio, [0, 8192, -16384])
            state = RecordingState(status="recording", pid=999999999, audio_path=str(audio))
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.read_recording_level", side_effect=cli.RecorderError("token abc123")),
            ):
                payload = cli._recording_level_payload(state)

        self.assertIsNotNone(payload)
        self.assertNotIn("token abc123", payload["detail"])
        self.assertNotIn("abc123", payload["detail"])

    def test_start_defaults_language_to_english(self) -> None:
        proc = mock.Mock()
        proc.pid = 12345
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            state = StateStore(state_file).read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["language"], "en")
        self.assertTrue(payload["pid_present"])
        self.assertTrue(payload["audio_path_present"])
        self.assertTrue(payload["process_identity_present"])
        self.assertNotIn("pid", payload)
        self.assertNotIn("audio_path", payload)
        self.assertNotIn("process_identity", payload)
        self.assertEqual(state.language, "en")
        self.assertEqual(state.process_identity, "proc-identity")

    def test_start_prepares_audio_artifact_with_private_permissions(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            audio_path = Path(StateStore(state_file).read().audio_path)
            mode = audio_path.stat().st_mode & 0o777
        self.assertEqual(code, 0)
        self.assertTrue(payload["pid_present"])
        self.assertTrue(payload["audio_path_present"])
        self.assertTrue(payload["process_identity_present"])
        self.assertNotIn("pid", payload)
        self.assertNotIn("audio_path", payload)
        self.assertNotIn("process_identity", payload)
        self.assertEqual(mode, 0o600)

    def test_start_enforces_recording_artifact_cap_after_successful_start(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            for index in range(cli.MAX_TEMP_RECORDING_FILES + 3):
                artifact = recordings / f"old-{index:02d}.wav"
                artifact.write_bytes(b"old")
                os.utime(artifact, (index, index))
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            state = StateStore(state_file).read()
            remaining = list(recordings.glob("*.wav")) + list(recordings.glob("*.flac")) + list(recordings.glob("*.log"))
            audio_exists = Path(state.audio_path).exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "recording")
        self.assertTrue(payload["pid_present"])
        self.assertTrue(payload["audio_path_present"])
        self.assertTrue(payload["process_identity_present"])
        self.assertNotIn("pid", payload)
        self.assertNotIn("audio_path", payload)
        self.assertNotIn("process_identity", payload)
        self.assertLessEqual(len(remaining), cli.MAX_TEMP_RECORDING_FILES)
        self.assertTrue(audio_exists)

    def test_start_reports_recording_artifact_cap_scan_failure(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                mock.patch(
                    "speed_of_cinnamon.cli.recording_artifact_files",
                    side_effect=cli.DirectoryScanError(recordings, field_name="recordings directory"),
                ),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "recording")
        self.assertIn("failed to scan or delete 1 cleanup artifact", payload["message"])
        self.assertEqual(payload["cleanup_failed_path_count"], 1)
        self.assertNotIn("cleanup_failed_paths", payload)
        self.assertEqual(payload["recording_artifact_cap"]["failed_path_count"], 1)
        self.assertEqual(payload["recording_artifact_cap"]["failed_paths"], [])

    def test_start_stops_recorder_and_removes_artifacts_when_state_write_fails(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                mock.patch("speed_of_cinnamon.cli.stop_process") as mocked_stop,
                mock.patch("speed_of_cinnamon.cli.StateStore.write", side_effect=RuntimeError("state write failed")),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            artifacts = list(recordings.glob("*")) if recordings.exists() else []

        self.assertEqual(code, 1)
        self.assertIn("state write failed", payload["error"])
        mocked_stop.assert_called_once_with(23456, expected_process_identity="proc-identity")
        self.assertEqual(artifacts, [])

    def test_start_preserves_artifacts_when_state_write_cleanup_cannot_stop_recorder(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                mock.patch("speed_of_cinnamon.cli.stop_process", return_value=False) as mocked_stop,
                mock.patch("speed_of_cinnamon.cli.StateStore.write", side_effect=RuntimeError("state write failed")),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            artifacts = list(recordings.glob("*")) if recordings.exists() else []

        self.assertEqual(code, 1)
        self.assertIn("state write failed", payload["error"])
        self.assertIn("could not be stopped safely", payload["error"])
        mocked_stop.assert_called_once_with(23456, expected_process_identity="proc-identity")
        self.assertTrue(artifacts)

    def test_start_preserves_artifacts_when_process_identity_cleanup_fails(self) -> None:
        proc = mock.Mock()
        proc.pid = 23456
        proc.poll.return_value = None
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("test-recorder", [])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=proc),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value=None),
                mock.patch("speed_of_cinnamon.cli.stop_process", return_value=False) as mocked_stop,
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            artifacts = list(recordings.glob("*")) if recordings.exists() else []

        self.assertEqual(code, 1)
        self.assertIn("process identity could not be verified", payload["error"])
        self.assertIn("could not be stopped safely", payload["error"])
        mocked_stop.assert_called_once_with(23456, allow_unverified_process=True)
        self.assertTrue(artifacts)

    def test_start_auto_falls_back_when_first_recorder_exits_immediately(self) -> None:
        failed_proc = mock.Mock()
        failed_proc.pid = 23456
        failed_proc.poll.return_value = 1
        failed_proc.returncode = 1
        working_proc = mock.Mock()
        working_proc.pid = 23457
        working_proc.poll.return_value = None
        second_log_existed: list[bool] = []

        def fake_choose(preference: str, *_args: object) -> RecorderCommand:
            return RecorderCommand(preference, [preference])

        def fake_start(command: RecorderCommand, log_path: Path) -> object:
            if command.name == "pw-record":
                log_path.write_text("first recorder failed\n", encoding="utf-8")
                return failed_proc
            second_log_existed.append(log_path.exists())
            return working_proc

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", side_effect=fake_choose) as mocked_choose,
                mock.patch("speed_of_cinnamon.cli.start_recorder", side_effect=fake_start),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="proc-identity"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            state = StateStore(state_file).read()

        self.assertEqual(code, 0)
        self.assertEqual(payload["recorder"], "parecord")
        self.assertEqual(state.recorder, "parecord")
        self.assertEqual([call.args[0] for call in mocked_choose.call_args_list], ["pw-record", "parecord"])
        self.assertEqual(second_log_existed, [False])

    def test_start_auto_fails_closed_when_failed_recorder_artifact_cleanup_fails(self) -> None:
        failed_proc = mock.Mock()
        failed_proc.pid = 23456
        failed_proc.poll.return_value = 1
        failed_proc.returncode = 1

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            recordings = Path(tmp) / "speed-of-cinnamon" / "recordings"
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", return_value=RecorderCommand("pw-record", ["pw-record"])),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=failed_proc),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            artifacts = list(recordings.glob("*")) if recordings.exists() else []

        self.assertEqual(code, 1)
        self.assertIn("failed to clean recording artifacts", payload["error"])
        self.assertTrue(artifacts)

    def test_start_explicit_recorder_reports_immediate_exit_without_fallback(self) -> None:
        failed_proc = mock.Mock()
        failed_proc.pid = 23456
        failed_proc.poll.return_value = 1
        failed_proc.returncode = 1

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch(
                    "speed_of_cinnamon.cli.choose_recorder",
                    return_value=RecorderCommand("pw-record", ["pw-record"]),
                ) as mocked_choose,
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=failed_proc),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--recorder", "pw-record", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            recording_artifacts = list((Path(tmp) / "speed-of-cinnamon" / "recordings").glob("*"))

        self.assertEqual(code, 1)
        self.assertIn("pw-record exited immediately", payload["error"])
        self.assertEqual([call.args[0] for call in mocked_choose.call_args_list], ["pw-record"])
        self.assertEqual(recording_artifacts, [])

    def test_start_explicit_recorder_redacts_immediate_exit_log_tail(self) -> None:
        failed_proc = mock.Mock()
        failed_proc.pid = 23456
        failed_proc.poll.return_value = 1
        failed_proc.returncode = 1

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch(
                    "speed_of_cinnamon.cli.choose_recorder",
                    return_value=RecorderCommand("pw-record", ["pw-record"]),
                ),
                mock.patch("speed_of_cinnamon.cli.start_recorder", return_value=failed_proc),
                mock.patch("speed_of_cinnamon.cli.read_file_tail", return_value="token=sk-secret-token\nBearer ghp_secret"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--recorder", "pw-record", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 1)
        self.assertIn("pw-record exited immediately", payload["error"])
        self.assertNotIn("sk-secret-token", payload["error"])
        self.assertNotIn("ghp_secret", payload["error"])

    def test_start_auto_removes_artifacts_when_all_recorders_fail(self) -> None:
        failed_processes = []
        for returncode in (1, 2, 3):
            proc = mock.Mock()
            proc.pid = 23456 + returncode
            proc.poll.return_value = returncode
            proc.returncode = returncode
            failed_processes.append(proc)

        def fake_choose(preference: str, *_args: object) -> RecorderCommand:
            return RecorderCommand(preference, [preference])

        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.choose_recorder", side_effect=fake_choose),
                mock.patch("speed_of_cinnamon.cli.start_recorder", side_effect=failed_processes),
                redirect_stdout(stdout),
            ):
                code = cli.run(["start", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            recording_artifacts = list((Path(tmp) / "speed-of-cinnamon" / "recordings").glob("*"))

        self.assertEqual(code, 1)
        self.assertIn("no recorder backend started successfully", payload["error"])
        self.assertEqual(recording_artifacts, [])

    def test_start_rejects_negative_max_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["start", "--max-seconds", "-1", "--state-file", str(Path(tmp) / "state.json"), "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("max-seconds must be at least 0", payload["error"])

    def test_start_rejects_excessive_max_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "start",
                    "--max-seconds",
                    str(cli.MAX_RECORDING_SECONDS + 1),
                    "--state-file",
                    str(Path(tmp) / "state.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("max-seconds must be at most", payload["error"])

    def test_start_rejects_escaped_null_in_state_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["start", "--state-file", "state\\x00.json", "--json"])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("contains invalid null byte", payload["error"])

    def test_start_rejects_null_byte_input_device(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run([
                    "start",
                    "--input-device",
                    "alsa\x00bad",
                    "--state-file",
                    str(Path(tmp) / "state.json"),
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("recording input device contains invalid null byte", payload["error"])

    def test_cancel_recorded_discards_files_and_resets_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            log = recordings_root / "recorded.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["message"], "recording discarded")
        self.assertTrue(payload["audio_deleted"])
        self.assertTrue(payload["log_deleted"])
        self.assertFalse(audio.exists())
        self.assertFalse(log.exists())
        self.assertEqual(final_state.status, "idle")
        self.assertEqual(final_state.audio_path, "")

    def test_cancel_persists_redacted_error_state_when_final_idle_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            log = recordings_root / "recorded.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            real_write = StateStore.write
            write_calls = 0

            def flaky_write(self: StateStore, state: RecordingState) -> None:
                nonlocal write_calls
                write_calls += 1
                if write_calls == 2:
                    raise OSError("idle write failed")
                real_write(self, state)

            with (
                mock.patch("speed_of_cinnamon.cli.StateStore.write", new=flaky_write),
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.error, "failed to persist canceled recording state")
        self.assertEqual(final_state.audio_path, "")
        self.assertEqual(final_state.log_path, "")
        self.assertFalse(audio.exists())
        self.assertFalse(log.exists())
        self.assertEqual(write_calls, 3)

    def test_cancel_recorded_discards_encrypted_flac_recording_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.flac.socenc"
            log = recordings_root / "recorded.log"
            audio.write_bytes(b"soc1")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertTrue(payload["audio_deleted"])
        self.assertFalse(audio.exists())

    def test_cancel_does_not_touch_artifacts_while_finalization_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            log = recordings_root / "recorded.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio), log_path=str(log)))
            lock_path = cli._acquire_finalization_lock(state_file)
            self.assertIsNotNone(lock_path)
            try:
                stdout = io.StringIO()
                with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                    code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
                payload = json.loads(stdout.getvalue())
                final_state = store.read()
                audio_exists = audio.exists()
                log_exists = log.exists()
            finally:
                cli._release_finalization_lock(lock_path)

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "finalizing")
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)
        self.assertEqual(final_state.status, "recorded")
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertEqual(final_state.log_path, str(log))

    def test_cancel_reclaimed_finalizing_state_discards_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            log = recordings_root / "recorded.log"
            audio.write_bytes(b"audio")
            log.write_text("log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(audio), log_path=str(log)))
            lock_path = cli._finalization_lock_path(state_file)
            lock_path.write_text("999999999\n", encoding="ascii")
            lock_path.chmod(0o600)
            old = time.time() - cli.MAX_FINALIZATION_PIDLESS_LOCK_AGE_SECONDS - 10
            os.utime(lock_path, (old, old))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()
            lock_exists = lock_path.exists()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertFalse(lock_exists)
        self.assertEqual(final_state.status, "idle")

    @mock.patch("speed_of_cinnamon.cli.stop_process")
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_cancel_running_recording_stops_process(self, mocked_alive: mock.Mock, mocked_stop: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings.mkdir(parents=True)
            transcripts.mkdir(parents=True)
            audio = recordings / "recording.wav"
            audio.write_bytes(b"audio")
            log = recordings / "recording.log"
            log.write_text("recorder log", encoding="utf-8")
            transcript = transcripts / "recording.txt"
            transcript.write_text("transcript", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="owner-identity",
                    audio_path=str(audio),
                    log_path=str(log),
                    transcript_path=str(transcript),
                )
            )
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="owner-identity"),
                redirect_stdout(io.StringIO()),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()
            transcript_exists = transcript.exists()
        self.assertEqual(code, 0)
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_called_once_with(1234, expected_process_identity="owner-identity")
        self.assertEqual(final_state.status, "idle")
        self.assertFalse(final_state.audio_path)
        self.assertFalse(final_state.log_path)
        self.assertFalse(final_state.transcript_path)
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertFalse(transcript_exists)

    @mock.patch("speed_of_cinnamon.cli.stop_process")
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_cancel_running_recording_preserves_state_when_artifact_cleanup_fails(
        self,
        mocked_alive: mock.Mock,
        mocked_stop: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            audio.write_bytes(b"audio")
            log = recordings / "recording.log"
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="owner-identity",
                    audio_path=str(audio),
                    log_path=str(log),
                )
            )
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="owner-identity"),
                mock.patch("speed_of_cinnamon.cli._remove_recording_artifact", return_value=False),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = StateStore(state_file).read()
            audio_exists = audio.exists()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(final_state.status, "error")
        self.assertTrue(final_state.audio_path)
        self.assertTrue(audio_exists)
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_called_once_with(1234, expected_process_identity="owner-identity")

    def test_cancel_discards_inflight_recording_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            trimmed = recordings / "recording.trimmed-cancel.flac"
            encoded = recordings / "recording.encoded-cancel.flac"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            trimmed.write_bytes(b"trimmed")
            encoded.write_bytes(b"encoded")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(
                RecordingState(status="error", audio_path=str(audio), log_path=str(log))
            )
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = StateStore(state_file).read()
            paths_exist = [path.exists() for path in (audio, log, trimmed, encoded)]

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        self.assertEqual(payload["inflight_artifact_count"], 2)
        self.assertTrue(payload["inflight_artifacts_deleted"])
        self.assertEqual(final_state.status, "idle")
        self.assertEqual(paths_exist, [False, False, False, False])

    def test_cancel_preserves_state_when_inflight_artifact_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            trimmed = recordings / "recording.trimmed-cancel.flac"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            trimmed.write_bytes(b"trimmed")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(
                RecordingState(status="error", audio_path=str(audio), log_path=str(log))
            )
            real_remove = cli._remove_recording_artifact

            def fail_trimmed_cleanup(path_value: str | None) -> bool:
                if path_value == str(trimmed):
                    return False
                return real_remove(path_value)

            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._remove_recording_artifact", side_effect=fail_trimmed_cleanup),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = StateStore(state_file).read()
            audio_exists = audio.exists()
            log_exists = log.exists()
            trimmed_exists = trimmed.exists()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["inflight_artifact_count"], 1)
        self.assertFalse(payload["inflight_artifacts_deleted"])
        self.assertEqual(final_state.status, "error")
        self.assertTrue(final_state.audio_path)
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertTrue(trimmed_exists)

    @mock.patch("speed_of_cinnamon.cli.finalize_recording", return_value={"status": "done"})
    @mock.patch("speed_of_cinnamon.cli.stop_process")
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_stop_running_recording_stops_process_with_identity(
        self,
        mocked_alive: mock.Mock,
        mocked_stop: mock.Mock,
        _mocked_finalize: mock.Mock,
    ) -> None:
        state = RecordingState(
            status="recording",
            pid=1234,
            process_identity="owner-identity",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            StateStore(state_file).write(state)
            args = self._build_finalize_args(insert_method="none")
            args.state_file = str(state_file)
            with (
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="owner-identity"),
            ):
                result = cli.command_stop(args)
            final_state = StateStore(state_file).read()
        self.assertEqual(result["status"], "done")
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_called_once_with(1234, expected_process_identity="owner-identity")
        self.assertEqual(final_state.status, "recorded")
        self.assertIsNone(final_state.pid)
        self.assertFalse(final_state.process_identity)

    @mock.patch("speed_of_cinnamon.cli.finalize_recording", return_value={"status": "done"})
    @mock.patch("speed_of_cinnamon.cli.stop_process", return_value=False)
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_stop_running_recording_preserves_state_when_stop_process_fails(
        self,
        mocked_alive: mock.Mock,
        mocked_stop: mock.Mock,
        mocked_finalize: mock.Mock,
    ) -> None:
        state = RecordingState(
            status="recording",
            pid=1234,
            process_identity="owner-identity",
        )
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            store = StateStore(state_file)
            store.write(state)
            args = self._build_finalize_args(insert_method="none")
            args.state_file = str(state_file)
            with mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="owner-identity"):
                result = cli.command_stop(args)
            final_state = store.read()

        self.assertEqual(result["status"], "recording")
        self.assertIn("could not be stopped safely", result["error"])
        self.assertEqual(final_state.status, "recording")
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_called_once_with(1234, expected_process_identity="owner-identity")
        mocked_finalize.assert_not_called()

    def test_stop_holds_finalization_lock_before_recorded_state_can_be_canceled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=1234, audio_path=str(audio)))
            args = self._build_finalize_args(insert_method="none")
            args.state_file = str(state_file)
            cancel_result: dict[str, object] = {}

            def fake_finalize(*_args: object, **kwargs: object) -> dict[str, object]:
                self.assertIsNotNone(kwargs.get("finalization_lock_path"))
                cancel_args = self._build_finalize_args(insert_method="none")
                cancel_args.state_file = str(state_file)
                cancel_result.update(cli.command_cancel(cancel_args))
                return {"status": "done"}

            def fake_process_is_alive(pid: object) -> bool:
                return pid == os.getpid()

            with (
                mock.patch("speed_of_cinnamon.cli.process_is_alive", side_effect=fake_process_is_alive),
                mock.patch("speed_of_cinnamon.cli.finalize_recording", side_effect=fake_finalize),
            ):
                result = cli.command_stop(args)
            audio_exists = audio.exists()

        self.assertEqual(result["status"], "done")
        self.assertEqual(cancel_result["status"], "finalizing")
        self.assertTrue(audio_exists)

    def test_stop_rereads_state_after_finalization_lock_before_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recording", pid=1234, audio_path=str(audio)))
            args = self._build_finalize_args(insert_method="none")
            args.state_file = str(state_file)
            original_acquire_finalization_lock = cli._acquire_finalization_lock

            def acquire_and_complete(state_path: Path) -> Path | None:
                lock_path = original_acquire_finalization_lock(state_path)
                store.write(RecordingState(status="done", transcript="already handled", inserted=True))
                return lock_path

            with (
                mock.patch("speed_of_cinnamon.cli._acquire_finalization_lock", side_effect=acquire_and_complete),
                mock.patch("speed_of_cinnamon.cli.finalize_recording") as mocked_finalize,
            ):
                result = cli.command_stop(args)
            final_state = store.read()

        self.assertEqual(result["status"], "done")
        self.assertEqual(final_state.status, "done")
        self.assertEqual(final_state.transcript, "already handled")
        mocked_finalize.assert_not_called()

    def test_start_refuses_to_spawn_recorder_while_lifecycle_lock_is_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            args = self._build_finalize_args(insert_method="none")
            args.state_file = str(state_file)
            args.max_seconds = 30
            args.input_device = ""
            args.recorder = "auto"
            args.language = "en"

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._acquire_finalization_lock", return_value=None),
                mock.patch("speed_of_cinnamon.cli.start_recorder") as mocked_start_recorder,
            ):
                result = cli.command_start(args)

        self.assertEqual(result["status"], "finalizing")
        self.assertIn("lifecycle", result["message"])
        mocked_start_recorder.assert_not_called()

    def test_stop_rereads_recorded_state_after_lifecycle_lock_before_finalizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recorded.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded", audio_path=str(audio)))
            args = self._build_finalize_args(insert_method="none")
            args.state_file = str(state_file)
            original_acquire_finalization_lock = cli._acquire_finalization_lock

            def acquire_and_complete(state_path: Path) -> Path | None:
                lock_path = original_acquire_finalization_lock(state_path)
                store.write(RecordingState(status="done", transcript="already handled", inserted=True))
                return lock_path

            with (
                mock.patch("speed_of_cinnamon.cli._acquire_finalization_lock", side_effect=acquire_and_complete),
                mock.patch("speed_of_cinnamon.cli.finalize_recording") as mocked_finalize,
            ):
                result = cli.command_stop(args)
            final_state = store.read()

        self.assertEqual(result["status"], "done")
        self.assertEqual(final_state.transcript, "already handled")
        mocked_finalize.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.stop_process")
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_cancel_running_recording_does_not_signal_reused_pid(self, mocked_alive: mock.Mock, mocked_stop: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            audio.write_bytes(b"audio")
            state_file = tmp_path / "state.json"
            StateStore(state_file).write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="old-identity",
                    audio_path=str(audio),
                )
            )
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="foreign-identity"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "idle")
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.stop_process", return_value=False)
    @mock.patch("speed_of_cinnamon.cli.process_is_alive", return_value=True)
    def test_cancel_running_recording_preserves_artifacts_when_stop_process_fails(
        self,
        mocked_alive: mock.Mock,
        mocked_stop: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="recording",
                    pid=1234,
                    process_identity="owner-identity",
                    audio_path=str(audio),
                    log_path=str(log),
                )
            )
            stdout = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli._recording_process_identity_for_pid", return_value="owner-identity"),
                redirect_stdout(stdout),
            ):
                code = cli.run(["cancel", "--state-file", str(state_file), "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()

        self.assertNotEqual(code, 0)
        self.assertEqual(payload["status"], "recording")
        self.assertIn("could not be stopped safely", payload["error"])
        self.assertEqual(final_state.status, "recording")
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)
        mocked_alive.assert_called_once_with(1234)
        mocked_stop.assert_called_once_with(1234, expected_process_identity="owner-identity")

    def test_finalize_error_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(recordings / "missing.wav")))
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(io.StringIO()):
                code = cli.run(["stop", "--state-file", str(state_file), "--insert-method", "none", "--json"])
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertEqual(final_state.status, "error")
        self.assertIn("missing or empty", final_state.error)

    @mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=RuntimeError("transcribe failed"))
    def test_finalize_recovery_clears_stale_process_identity(
        self, mocked_transcribe: mock.Mock
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            (tmp_path / "speed-of-cinnamon").chmod(0o700)
            state_file = tmp_path / "speed-of-cinnamon" / "state.json"
            audio = recordings / "recording.wav"
            audio.write_bytes(b"audio")
            store = StateStore(state_file)
            store.write(
                RecordingState(
                    status="finalizing",
                    pid=424242,
                    process_identity="stale-process-identity",
                    audio_path=str(audio),
                )
            )
            args = self._build_finalize_args()
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.detect_silent_recording", return_value=cli.SilenceDetectionResult(False, False, 1.0, 0.0, 1.0, 0.0, "speech detected")),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("skip trim")),
            ):
                with self.assertRaisesRegex(RuntimeError, "transcribe failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()

        mocked_transcribe.assert_called_once()
        self.assertEqual(final_state.status, "error")
        self.assertIsNone(final_state.pid)
        self.assertFalse(final_state.process_identity)

    def test_stop_recorded_without_audio_path_persists_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="recorded"))
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.run(["stop", "--state-file", str(state_file), "--insert-method", "none", "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("no recording is available", payload["error"])
        self.assertEqual(final_state.status, "error")
        self.assertIn("no recording is available", final_state.error)

    def test_toggle_processing_without_audio_path_does_not_start_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing"))
            stdout = io.StringIO()
            with (
                mock.patch("speed_of_cinnamon.cli.start_recorder") as mocked_start,
                redirect_stdout(stdout),
            ):
                code = cli.run(["toggle", "--state-file", str(state_file), "--insert-method", "none", "--json"])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()

        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("no recording is available", payload["error"])
        self.assertEqual(final_state.status, "error")
        mocked_start.assert_not_called()

    @mock.patch("speed_of_cinnamon.cli.command_status", side_effect=RuntimeError("command failed: Bearer sk-secret token=abc123"))
    @mock.patch("speed_of_cinnamon.cli.log_event")
    def test_cli_run_redacts_exception_error_message(self, mocked_log_event: mock.Mock, mocked_command_status: mock.Mock) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tempfile.gettempdir()}), redirect_stdout(stdout):
            code = cli.run(["status", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("status", payload)
        self.assertIn("error", payload)
        self.assertNotIn("sk-secret", payload["error"])
        self.assertNotIn("token=abc123", payload["error"])
        error_log_calls = [
            call
            for call in mocked_log_event.call_args_list
            if call.args and len(call.args) > 1 and call.args[1] == "command_exception"
        ]
        self.assertEqual(len(error_log_calls), 1)

    @mock.patch("speed_of_cinnamon.cli.command_status", return_value={"status": "error", "message": "command failed: Bearer sk-secret token=abc123"})
    @mock.patch("speed_of_cinnamon.cli.log_event")
    def test_cli_run_redacts_status_error_message_without_error_key(self, mocked_log_event: mock.Mock, mocked_command_status: mock.Mock) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tempfile.gettempdir()}), redirect_stdout(stdout):
            code = cli.run(["status", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(payload["status"], "error")
        self.assertIn("error", payload)
        self.assertNotIn("sk-secret", payload["error"])
        self.assertNotIn("token=abc123", payload["error"])
        self.assertNotIn("sk-secret", payload["message"])
        self.assertNotIn("token=abc123", payload["message"])
        error_log_calls = [
            call
            for call in mocked_log_event.call_args_list
            if call.args and len(call.args) > 1 and call.args[1] == "command_error"
        ]
        self.assertEqual(len(error_log_calls), 1)
        logged_error = error_log_calls[0].kwargs["error_message"]
        self.assertNotIn("sk-secret", logged_error)
        self.assertNotIn("token=abc123", logged_error)

    @mock.patch("speed_of_cinnamon.cli.command_status", side_effect=RuntimeError("command failed: token abc123"))
    @mock.patch("speed_of_cinnamon.cli.log_event")
    def test_cli_run_redacts_bare_token_exception_error_message(
        self, mocked_log_event: mock.Mock, mocked_command_status: mock.Mock
    ) -> None:
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tempfile.gettempdir()}), redirect_stdout(stdout):
            code = cli.run(["status", "--json"])
        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertNotIn("token abc123", payload["error"])
        self.assertNotIn("abc123", payload["error"])
        error_log_calls = [
            call
            for call in mocked_log_event.call_args_list
            if call.args and len(call.args) > 1 and call.args[1] == "command_exception"
        ]
        self.assertEqual(len(error_log_calls), 1)
        logged_error = error_log_calls[0].kwargs["error_message"]
        self.assertNotIn("token abc123", logged_error)
        self.assertNotIn("abc123", logged_error)

    @mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=RuntimeError("openai key sk-leak token=abc123"))
    def test_finalize_redacts_error_for_state_persistence(self, mocked_transcribe: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), redirect_stdout(io.StringIO()):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--json",
                ])
            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()
        self.assertEqual(code, 1)
        self.assertEqual(final_state.status, "error")
        self.assertNotIn("sk-leak", final_state.error)
        self.assertNotIn("token=abc123", final_state.error)
        self.assertNotIn("Bearer", final_state.error)
        self.assertIn("openai key", final_state.error)
        self.assertFalse(audio_exists)
        self.assertFalse(log_exists)
        self.assertEqual(final_state.audio_path, "")
        self.assertEqual(final_state.log_path, "")

    def test_finalize_keeps_audio_artifacts_when_error_state_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args()
            real_update = store.update

            def fake_update(**kwargs: object) -> RecordingState:
                if kwargs.get("status") == "error":
                    raise RuntimeError("state write failed")
                return real_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("trim failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=RuntimeError("transcribe failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "transcribe failed; failed to persist error state: state write failed"):
                    cli.finalize_recording(args, store, store.read())

            audio_exists = audio.exists()
            log_exists = log.exists()

        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)

    def test_finalize_fails_closed_when_transient_transcript_cleanup_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            args.artifact_encryption = "passphrase"
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("trim failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli._unlink_regular_leaf_with_parent_fsync", side_effect=RuntimeError("unlink failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "failed to delete transient transcript file"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()

        self.assertEqual(final_state.status, "error")
        self.assertIn("failed to delete transient transcript file", final_state.error)

    def test_finalize_reports_stale_transient_cleanup_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings.mkdir(parents=True)
            transcripts.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            stale = transcripts / ".stale.abcd.tmp.txt"
            stale.write_text("stale plaintext\n", encoding="utf-8")
            owner = cli._transient_transcript_owner_path(stale)
            owner_target = tmp_path / "foreign-owner"
            owner_target.write_text("foreign owner\n", encoding="utf-8")
            owner.symlink_to(owner_target)
            old_mtime = time.time() - cli.TRANSIENT_TRANSCRIPT_MAX_AGE_SECONDS - 60
            os.utime(stale, (old_mtime, old_mtime))
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("trim failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
            ):
                result = cli.finalize_recording(args, store, store.read())
            final_state = store.read()
            stale_exists = stale.exists()
            owner_is_symlink = owner.is_symlink()
            target_exists = owner_target.exists()

        self.assertEqual(result["status"], "error")
        self.assertIn("failed to scan or delete 1 cleanup artifact", result["error"])
        self.assertEqual(result["cleanup_failed_path_count"], 1)
        self.assertNotIn("cleanup_failed_paths", result)
        self.assertEqual(final_state.status, "error")
        self.assertIn("failed to scan or delete 1 cleanup artifact", final_state.error)
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertEqual(final_state.log_path, str(log))
        self.assertTrue(final_state.transcript_path)
        self.assertFalse(stale_exists)
        self.assertTrue(owner_is_symlink)
        self.assertTrue(target_exists)

    def test_finalize_transcript_cleanup_runs_even_when_error_state_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            transcripts = tmp_path / "speed-of-cinnamon" / "transcripts"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args()
            real_update = store.update

            def fake_update(**kwargs: object) -> RecordingState:
                if kwargs.get("status") == "error":
                    raise RuntimeError("state write failed")
                return real_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("trim failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", side_effect=RuntimeError("insert failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "insert failed; failed to persist error state: state write failed"):
                    cli.finalize_recording(args, store, store.read())

            transcript_files = list(transcripts.glob("*.txt")) if transcripts.exists() else []

        self.assertEqual(transcript_files, [])

    def test_finalize_error_cleanup_does_not_delete_when_cleanup_state_update_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings.mkdir(parents=True)
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            audio = recordings / "recording.wav"
            log = recordings / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=False)
            real_update = store.update

            def fake_update(**kwargs: object) -> RecordingState:
                if kwargs.get("audio_path") == "" and kwargs.get("log_path") == "":
                    raise RuntimeError("cleanup state write failed")
                return real_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fake_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=audio),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", side_effect=cli.RecorderError("trim failed")),
                mock.patch("speed_of_cinnamon.cli.transcribe", side_effect=RuntimeError("transcribe failed")),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "transcribe failed; failed to persist error cleanup state: cleanup state write failed",
                ):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            audio_exists = audio.exists()
            log_exists = log.exists()

        self.assertEqual(final_state.status, "error")
        self.assertEqual(final_state.audio_path, str(audio))
        self.assertEqual(final_state.log_path, str(log))
        self.assertTrue(audio_exists)
        self.assertTrue(log_exists)

    def test_finalize_removes_unstabilized_trimmed_artifact_on_error_when_keeping_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            trimmed = recordings_root / "recording.trimmed-error.flac"
            log = recordings_root / "recording.log"
            original.write_bytes(b"audio")
            trimmed.write_bytes(b"trimmed-audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=trimmed),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", side_effect=RuntimeError("post failed")),
            ):
                with self.assertRaisesRegex(RuntimeError, "post failed"):
                    cli.finalize_recording(args, store, store.read())
            final_state = store.read()
            original_exists = original.exists()
            trimmed_exists = trimmed.exists()
            log_exists = log.exists()

        self.assertEqual(final_state.status, "error")
        self.assertTrue(original_exists)
        self.assertFalse(trimmed_exists)
        self.assertTrue(log_exists)

    def test_finalize_reports_unstabilized_trimmed_cleanup_failure_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            trimmed = recordings_root / "recording.trimmed-error.flac"
            log = recordings_root / "recording.log"
            original.write_bytes(b"audio")
            trimmed.write_bytes(b"trimmed-audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=trimmed),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", side_effect=RuntimeError("post failed")),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "post failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertIn("transient trimmed recording artifact", final_state.error)
            self.assertTrue(trimmed.exists())

    def test_finalize_reports_stabilized_cleanup_failure_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            original = recordings_root / "recording.wav"
            trimmed = recordings_root / "recording.trimmed-error.flac"
            stable = recordings_root / "recording.flac"
            log = recordings_root / "recording.log"
            original.write_bytes(b"audio")
            trimmed.write_bytes(b"trimmed-audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="finalizing", audio_path=str(original), log_path=str(log)))
            args = self._build_finalize_args(keep_recording_artifacts=True)
            real_update = store.update

            def fail_done_update(**kwargs: object) -> RecordingState:
                if kwargs.get("status") == "done":
                    raise RuntimeError("done state write failed")
                return real_update(**kwargs)

            with (
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp, "XDG_STATE_HOME": tmp}),
                mock.patch.object(store, "update", side_effect=fail_done_update),
                mock.patch("speed_of_cinnamon.cli.validate_audio_file", return_value=original),
                mock.patch(
                    "speed_of_cinnamon.cli.detect_silent_recording",
                    return_value=cli.SilenceDetectionResult(False, False, 2.0, 1.0, 1.0, 0.1, "not silent"),
                ),
                mock.patch("speed_of_cinnamon.cli.trim_recording_silence", return_value=trimmed),
                mock.patch("speed_of_cinnamon.cli.transcribe", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.post_process_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.prepare_output_text", return_value="transcript"),
                mock.patch("speed_of_cinnamon.cli.insert_text", return_value=True),
                mock.patch("speed_of_cinnamon.cli.remove_file", return_value=False),
            ):
                with self.assertRaisesRegex(RuntimeError, "done state write failed"):
                    cli.finalize_recording(args, store, store.read())

            final_state = store.read()
            self.assertEqual(final_state.status, "error")
            self.assertIn("stabilized recording artifact", final_state.error)
            self.assertTrue(stable.exists())

    def test_finalize_rejects_transcript_write_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            recordings_root = tmp_path / "speed-of-cinnamon" / "recordings"
            recordings_root.mkdir(parents=True)
            audio = recordings_root / "recording.wav"
            log = recordings_root / "recording.log"
            audio.write_bytes(b"audio")
            log.write_text("recorder log", encoding="utf-8")
            state_file = tmp_path / "state.json"
            store = StateStore(state_file)
            store.write(RecordingState(status="processing", audio_path=str(audio), log_path=str(log)))
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_CACHE_HOME": tmp}), mock.patch(
                "speed_of_cinnamon.cli._write_text_atomic",
                side_effect=RuntimeError("failed to write transcript file: /tmp/transcript.txt"),
            ), redirect_stdout(stdout):
                code = cli.run([
                    "stop",
                    "--state-file",
                    str(state_file),
                    "--insert-method",
                    "none",
                    "--transcriber",
                    "command",
                    "--transcriber-command",
                    "printf finalize-transcript",
                    "--json",
                ])
            payload = json.loads(stdout.getvalue())
            final_state = store.read()
        self.assertEqual(code, 1)
        self.assertEqual(final_state.status, "error")
        self.assertIn("failed to write transcript file", payload["error"])
        self.assertIn("failed to write transcript file", final_state.error)

    def test_read_file_tail_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.txt"
            path.write_bytes(b"bad\xff")
            with self.assertRaisesRegex(ValueError, "failed to decode file as UTF-8"):
                cli.read_file_tail(path, 10)

    def test_read_file_tail_preserves_read_error_when_handle_close_is_interrupted(self) -> None:
        handle = mock.Mock()
        handle.tell.return_value = 4
        handle.read.return_value = b"bad\xff"
        handle.close.side_effect = KeyboardInterrupt
        with (
            mock.patch.object(cli.os, "open", return_value=11),
            mock.patch.object(cli.os, "fdopen", return_value=handle),
            mock.patch.object(cli, "assert_fd_is_regular_private_file"),
        ):
            with self.assertRaisesRegex(ValueError, "failed to decode file as UTF-8"):
                cli.read_file_tail(Path("/tmp/broken.txt"), 10)

        handle.close.assert_called_once()

    def test_read_file_tail_opens_without_following_symlinks(self) -> None:
        captured: dict[str, object] = {}
        handle = mock.Mock()
        handle.read.return_value = b"hello"
        handle.tell.return_value = 5

        def fake_os_open(path: Path, flags: int, mode: int = 0o600) -> int:
            captured["path"] = path
            captured["flags"] = flags
            captured["mode"] = mode
            return 11

        with (
            mock.patch("speed_of_cinnamon.cli.os.open", side_effect=fake_os_open),
            mock.patch("speed_of_cinnamon.cli.os.fdopen", return_value=handle),
            mock.patch("speed_of_cinnamon.cli.assert_fd_is_regular_private_file"),
        ):
            text = cli.read_file_tail(Path("/tmp/sample.txt"), 10)

        self.assertEqual(text, "hello")
        self.assertEqual(captured["path"], Path("/tmp/sample.txt"))
        self.assertEqual(captured["flags"], os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
        handle.close.assert_called_once()

    def test_read_file_tail_closes_descriptor_when_fdopen_rejects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("hello", encoding="utf-8")
            real_open = os.open
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path:
                    target_fds.append(fd)
                return fd

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
            ):
                with self.assertRaisesRegex(ValueError, "invalid descriptor mode"):
                    cli.read_file_tail(path, 10)

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_read_file_tail_closes_descriptor_when_fdopen_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("hello", encoding="utf-8")
            real_open = os.open
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path:
                    target_fds.append(fd)
                return fd

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=KeyboardInterrupt),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    cli.read_file_tail(path, 10)

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_read_file_tail_preserves_validation_error_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("hello", encoding="utf-8")
            real_open = os.open
            real_close = os.close
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path:
                    target_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                real_close(fd)
                if fd in target_fds:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli, "assert_fd_is_regular_private_file", side_effect=OSError("not regular")),
                mock.patch.object(cli.os, "close", side_effect=close_wrapper),
            ):
                with self.assertRaisesRegex(OSError, "not regular"):
                    cli.read_file_tail(path, 10)

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_read_file_tail_preserves_fdopen_error_when_fd_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("hello", encoding="utf-8")
            real_open = os.open
            real_close = os.close
            target_fds: list[int] = []

            def open_wrapper(*args: object, **kwargs: object) -> int:
                fd = real_open(*args, **kwargs)
                if args and args[0] == path:
                    target_fds.append(fd)
                return fd

            def close_wrapper(fd: int) -> None:
                real_close(fd)
                if fd in target_fds:
                    raise KeyboardInterrupt

            with (
                mock.patch.object(cli.os, "open", side_effect=open_wrapper),
                mock.patch.object(cli.os, "fdopen", side_effect=ValueError("invalid descriptor mode")),
                mock.patch.object(cli.os, "close", side_effect=close_wrapper),
            ):
                with self.assertRaisesRegex(ValueError, "invalid descriptor mode"):
                    cli.read_file_tail(path, 10)

            self.assertEqual(len(target_fds), 1)
            with self.assertRaises(OSError):
                os.fstat(target_fds[0])

    def test_read_file_tail_rejects_hardlinked_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("log\n", encoding="utf-8")
            hardlink = Path(tmp) / "log-hardlink.txt"
            try:
                os.link(path, hardlink)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(OSError, "must not be hardlinked"):
                cli.read_file_tail(hardlink, 10)

    def test_read_file_tail_rejects_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("mkfifo unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "log.fifo"
            os.mkfifo(fifo)

            with self.assertRaisesRegex(OSError, "must be a regular file"):
                cli.read_file_tail(fifo, 10)

    @mock.patch("speed_of_cinnamon.cli.os.open", wraps=os.open)
    def test_prepare_private_file_uses_secure_directory_fd_open(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "recording.wav"
            cli._prepare_private_file(path, field_name="recording audio file")

        final_opens = [
            (args, kwargs)
            for args, kwargs in mocked_open.call_args_list
            if args and args[0] == "recording.wav"
        ]
        self.assertEqual(len(final_opens), 1)
        args, kwargs = final_opens[0]
        self.assertTrue(args[1] & os.O_WRONLY)
        self.assertTrue(args[1] & os.O_CREAT)
        self.assertTrue(args[1] & os.O_EXCL)
        self.assertTrue(args[1] & os.O_NOFOLLOW)
        self.assertIsInstance(kwargs.get("dir_fd"), int)

    def test_read_file_tail_rejects_escaped_null(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.txt"
            path.write_text("line\\x00end", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contains invalid null byte"):
                cli.read_file_tail(path, 10)

    def test_read_file_tail_rejects_control_character_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid control character"):
            cli.read_file_tail(Path("log\x85spoof.txt"), 10)

    def test_read_file_tail_rejects_escaped_control_character_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid control character"):
            cli.read_file_tail(Path("log\\x85spoof.txt"), 10)

    def test_normalize_input_sources_rejects_control_characters(self) -> None:
        base = {
            "id": "alsa_input",
            "name": "Microphone",
            "description": "Built-in microphone",
            "driver": "PipeWire",
            "state": "RUNNING",
            "default": False,
            "monitor": False,
        }
        error_fields = {
            "id": "input source id contains invalid control character",
            "name": "input source name contains invalid control character",
            "description": "input source description contains invalid control character",
            "driver": "input source driver contains invalid control character",
            "state": "input source state contains invalid control character",
        }
        for field_name, error in error_fields.items():
            values = dict(base)
            values[field_name] = "bad\x85value"
            source = type("Source", (), values)()
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(RuntimeError, error):
                    cli._normalize_input_sources([source])

    def test_normalize_input_sources_rejects_escaped_control_characters(self) -> None:
        source = type(
            "Source",
            (),
            {
                "id": "alsa_input",
                "name": "Microphone",
                "description": "Built-in microphone",
                "driver": "Pipe\\x85Wire",
                "state": "RUNNING",
                "default": False,
                "monitor": False,
            },
        )()
        with self.assertRaisesRegex(RuntimeError, "input source driver contains invalid control character"):
            cli._normalize_input_sources([source])

    def test_normalize_model_payloads_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "model name contains invalid control character"):
            cli._normalize_model_payloads([{"name": "bad\x85model"}])

    def test_normalize_text_models_payload_rejects_escaped_control_message(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text models payload message contains invalid control character"):
            cli._normalize_text_models_payload({"available": True, "models": [], "message": "bad\\x85message"})

    def test_read_file_tail_rejects_request_exceeding_history_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_chars must be at most"):
                cli.read_file_tail(path, cli.MAX_TRANSCRIPT_HISTORY_TEXT_CHARS + 1)

    def test_read_log_excerpt_rejects_request_exceeding_log_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_chars must be at most"):
                cli.read_log_excerpt(path, cli.MAX_LOG_EXCERPT_CHARS + 1)

    def test_read_log_excerpt_ignores_invalid_file_tail(self) -> None:
        with mock.patch("speed_of_cinnamon.cli.read_file_tail", side_effect=ValueError("bad utf-8")):
            self.assertEqual(cli.read_log_excerpt(Path("/tmp/bad.log")), "")

    def test_coerce_int_rejects_bool(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be an integer"):
            cli._coerce_int(True, field_name="max")

    def test_coerce_int_rejects_float(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be an integer"):
            cli._coerce_int(1.0, field_name="max")  # type: ignore[arg-type]

    def test_assert_clean_text_rejects_non_text_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            cli._assert_clean_text(123, field_name="value", max_chars=10)  # type: ignore[arg-type]

    def test_assert_clean_text_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\nline2", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\nline2", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\rline2", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\x0a", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\x0d", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\u000a", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\x85", field_name="value", max_chars=20)
        with self.assertRaisesRegex(RuntimeError, "contains invalid control character"):
            cli._assert_clean_text("line1\\x85", field_name="value", max_chars=20)

    def test_assert_text_limit_rejects_oversized_bytes(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "is too large"):
            cli._assert_text_limit("😀😀", field_name="value", max_chars=4)

    def test_assert_text_limit_rejects_surrogate_characters(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "contains invalid UTF-8"):
            cli._assert_text_limit("bad\ud800text", field_name="value", max_chars=20)

    def test_coerce_path_rejects_non_text_value(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            cli._coerce_path(123, field_name="path")  # type: ignore[arg-type]

    def test_read_file_tail_rejects_nonpositive_max_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "max_chars must be positive"):
                cli.read_file_tail(path, 0)

    def test_read_file_tail_rejects_invalid_path_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "path must be a Path"):
            cli.read_file_tail(123, 10)  # type: ignore[arg-type]

    def test_read_log_excerpt_rejects_invalid_path_type(self) -> None:
        with self.assertRaisesRegex(TypeError, "path must be a Path"):
            cli.read_log_excerpt(123, 10)  # type: ignore[arg-type]

    def test_read_file_tail_rejects_non_integer_max_chars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log.txt"
            path.write_text("ok", encoding="utf-8")
            with self.assertRaisesRegex(TypeError, "max_chars must be an integer"):
                cli.read_file_tail(path, 1.5)  # type: ignore[arg-type]

    def test_parse_cli_settings_json_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            cli._parse_cli_settings_json({} )  # type: ignore[arg-type]

    def test_parse_cli_settings_json_rejects_overlong_bytes(self) -> None:
        raw = json.dumps({"payload": "😀" * ((cli.MAX_SETTINGS_JSON_CHARS // 4) + 1)})
        with self.assertRaisesRegex(RuntimeError, "too large"):
            cli._parse_cli_settings_json(raw)

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "value must be text"):
            cli._contains_escaped_null(12)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "value must be text"):
            cli._contains_escaped_null(True)  # type: ignore[arg-type]

    def test_append_space_if_needed_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text must be text"):
            cli.append_space_if_needed(123, True)  # type: ignore[arg-type]

    def test_append_space_if_needed_rejects_non_bool_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "append_space must be a boolean"):
            cli.append_space_if_needed("hello", "yes")  # type: ignore[arg-type]

    def test_prepare_output_text_rejects_non_bool_sanitize_flag(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "sanitize must be a boolean"):
            cli.prepare_output_text("hello", True, "yes")  # type: ignore[arg-type]

    def test_prepare_output_text_can_soften_profanity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                self.assertEqual(cli.prepare_output_text("Scheiße, fuck.", False, False, True), "Glitzerkram, Frickelfrosch.")
                self.assertEqual(cli.prepare_output_text("assignment", False, False, True), "assignment")

    def test_prepare_output_text_uses_editable_profanity_filter_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                path = cli._ensure_editable_profanity_filter_file()
                path.write_text("schei(?:ss|ß)e? -> Regenbogenmuffin\n", encoding="utf-8")
                self.assertEqual(cli.prepare_output_text("Scheiße!", False, False, True), "Regenbogenmuffin!")

    def test_prepare_output_text_treats_custom_profanity_patterns_as_literals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp}):
                path = cli._ensure_editable_profanity_filter_file()
                path.write_text("f.*k -> Regenbogenmuffin\n(.+)+ -> Sicherheitskeks\n", encoding="utf-8")
                self.assertEqual(cli.prepare_output_text("fuck f.*k (.+)+", False, False, True), "fuck Regenbogenmuffin Sicherheitskeks")

    def test_soften_profanity_text_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "text must be text"):
            cli.soften_profanity_text(False)  # type: ignore[arg-type]

    def test_profanity_filter_document_command_writes_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout = io.StringIO()
            with mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp, "XDG_STATE_HOME": tmp}), redirect_stdout(stdout):
                code = cli.run(["profanity-filter-document", "--json"])
            payload = json.loads(stdout.getvalue())
            document = Path(payload["path"])
            text = document.read_text(encoding="utf-8")

        self.assertEqual(code, 0)
        self.assertEqual(payload["entries"], len(cli.PROFANITY_REPLACEMENT_PAIRS))
        self.assertTrue(payload["editable"])
        self.assertEqual(document.name, "profanity-filter.txt")
        self.assertIn("Speed of Cinnamon profanity replacement list", text)
        self.assertIn("custom patterns are treated as literal text for safety", text)
        self.assertIn("Glitzerkram", text)
        self.assertIn("Frickelfrosch", text)

    def test_settings_export_rejects_private_settings_in_argv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "settings-export.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                code = cli.run([
                    "settings-export",
                    "--settings-json",
                    '{"language":"de","openai-compatible-api-key":"sk-secret"}',
                    "--output",
                    str(output),
                    "--json",
                ])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 1)
            self.assertIn("private settings must be provided via --settings-json-stdin", payload["error"])
            self.assertNotIn("sk-secret", payload["error"])
            self.assertFalse(output.exists())

    def test_settings_export_allows_private_settings_via_stdin_without_exporting_secret(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "settings-export.json"
            stdout = io.StringIO()
            with (
                mock.patch("sys.stdin", io.StringIO('{"language":"de","openai-compatible-api-key":"sk-secret"}')),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "settings-export",
                    "--settings-json-stdin",
                    "--output",
                    str(output),
                    "--json",
                ])

            payload = json.loads(stdout.getvalue())
            rendered = output.read_text(encoding="utf-8")
            self.assertEqual(code, 0)
            self.assertTrue(payload["path_present"])
            self.assertNotIn("path", payload)
            self.assertNotIn(str(output), json.dumps(payload))
            self.assertNotIn("sk-secret", rendered)
            self.assertNotIn("openai-compatible-api-key", json.loads(rendered)["settings"])

    def test_settings_export_accepts_module_path_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            for index in range(14):
                parent = parent / f"nested-segment-{index:02d}"
                parent.mkdir(mode=0o700)
            output = parent / "settings-export.json"
            self.assertGreater(len(str(output)), cli.MAX_PATH_CHARS)
            self.assertLess(len(str(output)), 4096)

            stdout = io.StringIO()
            with (
                mock.patch("sys.stdin", io.StringIO('{"language":"de"}')),
                redirect_stdout(stdout),
            ):
                code = cli.run([
                    "settings-export",
                    "--settings-json-stdin",
                    "--output",
                    str(output),
                    "--json",
                ])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertTrue(payload["path_present"])
            self.assertNotIn("path", payload)
            self.assertNotIn(str(output), json.dumps(payload))
            self.assertTrue(output.exists())

    def test_history_rejects_non_boolean_confirm_plaintext_direct_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                args = argparse.Namespace(limit=1, confirm_plaintext="false")
                with self.assertRaisesRegex(RuntimeError, "confirm_plaintext must be a boolean"):
                    cli.command_history(args)

    def test_transcripts_document_rejects_non_boolean_confirm_plaintext_direct_arg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                args = argparse.Namespace(limit=1, confirm_plaintext="false")
                with self.assertRaisesRegex(RuntimeError, "confirm_plaintext must be a boolean"):
                    cli.command_transcripts_document(args)

    def test_transcripts_export_rejects_non_boolean_plaintext_direct_args(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                args = argparse.Namespace(
                    limit=1,
                    artifact_encryption="keyring",
                    plaintext="false",
                    confirm_plaintext=False,
                )
                with self.assertRaisesRegex(RuntimeError, "plaintext must be a boolean"):
                    cli.command_transcripts_export(args)

                args = argparse.Namespace(
                    limit=1,
                    artifact_encryption="keyring",
                    plaintext=True,
                    confirm_plaintext="false",
                )
                with self.assertRaisesRegex(RuntimeError, "confirm_plaintext must be a boolean"):
                    cli.command_transcripts_export(args)

    def test_transcripts_document_limit_zero_returns_no_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                transcript_dir = cli.transcript_dir()
                transcript_dir.mkdir(parents=True)
                (transcript_dir / "visible.txt").write_text("private transcript\n", encoding="utf-8")
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli.run(["transcripts-document", "--limit", "0", "--confirm-plaintext", "--json"])

            payload = json.loads(stdout.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["transcripts"], 0)
            self.assertNotIn("private transcript", payload["content"])

    def test_transcripts_export_limit_zero_does_not_include_transcript_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp, "XDG_DATA_HOME": tmp}):
                transcript_dir = cli.transcript_dir()
                transcript_dir.mkdir(parents=True)
                (transcript_dir / "visible.txt").write_text("private transcript\n", encoding="utf-8")
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = cli.run(["transcripts-export", "--limit", "0", "--plaintext", "--confirm-plaintext", "--json"])
                payload = json.loads(stdout.getvalue())
                exported = Path(payload["path"]).read_text(encoding="utf-8")

            self.assertEqual(code, 0)
            self.assertEqual(payload["transcripts"], 0)
            self.assertNotIn("private transcript", exported)

if __name__ == "__main__":
    unittest.main()
