from __future__ import annotations

import json
import os
import stat
import subprocess
import unittest
import tempfile
import time
from pathlib import Path
from typing import BinaryIO, cast
from unittest import mock

from speed_of_cinnamon import output as output_module
from speed_of_cinnamon.output import (
    OutputError,
    PasteNotAttemptedError,
    MAX_OUTPUT_CHARS,
    MAX_TYPE_DELAY_MS,
    _contains_escaped_null,
    _filesize,
    _read_file_head,
    _run_stdout,
    _validate_text_input,
    _active_window_paste_key,
    _acquire_clipboard_dedup_lock,
    _release_clipboard_dedup_lock,
    _looks_like_terminal,
    _run_with_input,
    insert_text,
    paste_from_clipboard,
    set_clipboard,
    type_text,
)


class _FakePopen:
    def __init__(self, result: subprocess.CompletedProcess[bytes]) -> None:
        self._result = result
        self.pid = 12345
        self.returncode = result.returncode

    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        return self._result.stdout, self._result.stderr

    def kill(self) -> None:
        self.returncode = -9

    def wait(self, timeout: int | None = None) -> int:
        return self.returncode


class _TimeoutPopen(_FakePopen):
    def __init__(self) -> None:
        super().__init__(subprocess.CompletedProcess(["cmd"], 0))
        self.returncode = None

    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        if timeout is not None:
            raise subprocess.TimeoutExpired(cmd=["cmd"], timeout=timeout)
        return b"", b""


class _InterruptPopen(_FakePopen):
    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        raise KeyboardInterrupt("process wait interrupted")


class _OSErrorPopen(_FakePopen):
    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        raise OSError("process wait failed")


class _FileNotFoundPopen(_FakePopen):
    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        raise FileNotFoundError("process wait failed")


class _RunnerPopen(_FakePopen):
    def __init__(self, runner: object, args: tuple[object, ...], kwargs: dict[str, object]) -> None:
        super().__init__(subprocess.CompletedProcess([], 0, stdout=b"", stderr=b""))
        self._runner = runner
        self._args = args
        self._kwargs = kwargs
        self._completed = False

    def communicate(self, input: bytes | None = None, timeout: int | None = None) -> tuple[bytes | None, bytes | None]:
        if not self._completed:
            call_kwargs = dict(self._kwargs)
            call_kwargs["input"] = input
            result = self._runner(*self._args, **call_kwargs)  # type: ignore[operator]
            assert isinstance(result, subprocess.CompletedProcess)
            self._result = result
            self.returncode = result.returncode
            self._completed = True
        return self._result.stdout, self._result.stderr


def _popen_from_run(runner: object):
    def factory(*args: object, **kwargs: object) -> _FakePopen:
        return _RunnerPopen(runner, args, kwargs)

    return factory


class OutputTest(unittest.TestCase):
    def setUp(self) -> None:
        output_module._LAST_CLIPBOARD_TEXT = ""
        output_module._LAST_CLIPBOARD_METHOD = None
        output_module._LAST_CLIPBOARD_INSERTION = 0.0
        output_module._LAST_CLIPBOARD_CONTEXT = None
        self._default_active_window_snapshot_patch = mock.patch(
            "speed_of_cinnamon.output._active_x_window_snapshot",
            return_value=("123", "Editor", "Xed"),
        )
        self._default_active_window_snapshot_patch.start()

    def tearDown(self) -> None:
        if self._default_active_window_snapshot_patch is not None:
            self._default_active_window_snapshot_patch.stop()

    def _use_real_active_window_snapshot(self) -> None:
        self._default_active_window_snapshot_patch.stop()
        self._default_active_window_snapshot_patch = None

    def _expected_paste_fingerprint(self, text: str) -> str:
        return output_module._clipboard_insertion_fingerprint(
            text,
            output_module._clipboard_method_dedupe_context(
                "clipboard-paste",
                output_module._LAST_CLIPBOARD_CONTEXT,
            ),
        )

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(OutputError, "value must be text"):
            _contains_escaped_null(12)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(OutputError, "value must be text"):
            _contains_escaped_null(True)  # type: ignore[arg-type]

    def test_clipboard_dedup_state_writes_through_secure_temp_fd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
            with (
                mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root),
                mock.patch("speed_of_cinnamon.output.Path.open", side_effect=AssertionError("reopened temp path")),
            ):
                written = output_module._write_clipboard_dedup_state("secret text", 123.0)

            content = path.read_text(encoding="utf-8")
            mode = path.stat().st_mode & 0o777

        self.assertTrue(written)
        self.assertNotIn("secret text", content)
        self.assertEqual(
            json.loads(content),
            {"sha256": output_module._clipboard_text_fingerprint("secret text"), "at": 123.0},
        )
        self.assertEqual(mode, 0o600)

    def test_clipboard_dedup_state_clears_legacy_plaintext_state_after_method_scoping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
            path.write_text(json.dumps({"text": "legacy secret", "at": 42.0}), encoding="utf-8")
            with mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root):
                trusted, snapshot = output_module._read_trusted_clipboard_dedup_state()

        self.assertFalse(trusted)
        self.assertEqual(snapshot, ("", 0.0))
        self.assertFalse(path.exists())

    def test_clipboard_dedup_state_rejects_invalid_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
            path.write_text(json.dumps({"sha256": "not-a-valid-fingerprint", "at": 42.0}), encoding="utf-8")
            with mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root):
                trusted, snapshot = output_module._read_trusted_clipboard_dedup_state()

        self.assertFalse(trusted)
        self.assertEqual(snapshot, ("", 0.0))

    def test_clipboard_dedup_state_rejects_non_finite_timestamp(self) -> None:
        fingerprint = output_module._clipboard_text_fingerprint("secret text")
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                state_root = Path(tmp)
                path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
                path.write_text(json.dumps({"sha256": fingerprint, "at": value}), encoding="utf-8")
                with mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root):
                    trusted, snapshot = output_module._read_trusted_clipboard_dedup_state()

            self.assertFalse(trusted)
            self.assertEqual(snapshot, ("", 0.0))

    def test_clipboard_dedup_state_rejects_non_finite_timestamp_on_write(self) -> None:
        fingerprint = output_module._clipboard_text_fingerprint("secret text")
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            with mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root):
                for value in (float("nan"), float("inf"), float("-inf")):
                    self.assertFalse(output_module._write_clipboard_dedup_fingerprint_state(fingerprint, value))
            self.assertFalse((state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE).exists())

    def test_clipboard_dedup_state_fails_closed_when_atomic_write_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            with (
                mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root),
                mock.patch("speed_of_cinnamon.output.write_text_atomically_without_following_symlinks", side_effect=OSError("full")),
            ):
                written = output_module._write_clipboard_dedup_state("secret text", 123.0)

        self.assertFalse(written)

    def test_clipboard_dedup_state_fails_closed_when_state_probe_fails(self) -> None:
        with mock.patch.object(output_module.Path, "exists", side_effect=OSError("state probe failed")):
            self.assertEqual(
                output_module._read_clipboard_dedup_state_entry(),
                (False, ("", 0.0), False),
            )

    def test_set_clipboard_prefers_xclip(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.Popen") as mocked_run,
        ):
            mocked_which.side_effect = lambda command, path=None: "found" if command == "xclip" else None
            mocked_run.return_value = _FakePopen(subprocess.CompletedProcess(["xclip"], 0))

            method = set_clipboard("hello")

            self.assertEqual(method, "xclip")
            mocked_run.assert_called_once()

    def test_set_clipboard_falls_back_when_preferred_helper_fails(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run_with_input,
        ):
            mocked_which.side_effect = lambda command, path=None: f"/usr/bin/{command}" if command in {"xclip", "xsel"} else None
            mocked_run_with_input.side_effect = [OutputError("xclip failed"), None]

            method = set_clipboard("hello")

        self.assertEqual(method, "xsel")
        self.assertEqual(mocked_run_with_input.call_count, 2)
        self.assertEqual(mocked_run_with_input.call_args_list[0].args[0][0], "xclip")
        self.assertEqual(mocked_run_with_input.call_args_list[1].args[0][0], "xsel")

    def test_set_clipboard_uses_resolved_xclip_path(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xclip") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(set_clipboard("hello"), "xclip")

        self.assertEqual(calls[0][0], "/usr/bin/xclip")
        self.assertEqual(mocked_which.call_count, 1)

    def test_set_clipboard_falls_back_to_xsel(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.Popen") as mocked_run,
        ):
            mocked_which.side_effect = lambda command, path=None: {
                "xclip": None,
                "xsel": "found",
                "wl-copy": None,
            }.get(command)
            mocked_run.return_value = _FakePopen(subprocess.CompletedProcess(["xsel"], 0))

            method = set_clipboard("hello")

            self.assertEqual(method, "xsel")
            mocked_run.assert_called_once()

    def test_set_clipboard_errors_without_helper(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value=None):
            with self.assertRaisesRegex(OutputError, "no clipboard helper found"):
                set_clipboard("hello")

    def test_read_text_clipboard_falls_back_after_xclip_failure(self) -> None:
        calls: list[list[str]] = []

        def fake_which(command: str) -> str | None:
            return f"/usr/bin/{command}" if command in {"xclip", "xsel"} else None

        def fake_run(argv: list[str], **_kwargs: object) -> str | None:
            calls.append(argv)
            return None if argv[0] == "xclip" else "fallback text"

        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._run_stdout_raw", side_effect=fake_run),
        ):
            self.assertEqual(output_module._read_text_clipboard(), "fallback text")

        self.assertEqual([call[0] for call in calls], ["xclip", "xsel"])

    def test_text_clipboard_snapshot_falls_back_after_xclip_failure(self) -> None:
        calls: list[list[str]] = []

        def fake_which(command: str) -> str | None:
            return f"/usr/bin/{command}" if command in {"xclip", "xsel"} else None

        def fake_run(argv: list[str], **_kwargs: object) -> str | None:
            calls.append(argv)
            return None if argv[0] == "xclip" else "fallback text\n"

        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._run_stdout_raw", side_effect=fake_run),
        ):
            self.assertEqual(output_module._read_text_clipboard_snapshot(), (True, "fallback text\n"))

        self.assertEqual([call[0] for call in calls], ["xclip", "xsel"])

    def test_run_with_input_rejects_non_sequence_argv(self) -> None:
        with self.assertRaisesRegex(OutputError, "argv must be a sequence"):
            _run_with_input("echo", "input")  # type: ignore[arg-type]

    def test_run_with_input_rejects_non_text_argument(self) -> None:
        with self.assertRaisesRegex(OutputError, "command arguments must be text"):
            _run_with_input(["echo", 12], "input")  # type: ignore[list-item]

    def test_run_with_input_rejects_blank_executable(self) -> None:
        with self.assertRaisesRegex(OutputError, "command is empty"):
            _run_with_input(["   "], "input")

    def test_run_with_input_rejects_command_with_path_separator(self) -> None:
        with self.assertRaisesRegex(OutputError, "path separators"):
            _run_with_input(["bin/cmd"], "input")

    def test_run_with_input_rejects_non_int_timeout(self) -> None:
        with self.assertRaisesRegex(OutputError, "timeout must be an integer"):
            _run_with_input(["sleep"], "", timeout="1")  # type: ignore[arg-type]

    def test_run_with_input_rejects_non_int_output_limit(self) -> None:
        with self.assertRaisesRegex(OutputError, "max_output_chars must be an integer"):
            _run_with_input(["sleep"], "", max_output_chars=True)  # type: ignore[arg-type]

    def test_run_with_input_rejects_excessive_output_limit(self) -> None:
        with self.assertRaisesRegex(OutputError, "max_output_chars must not exceed"):
            _run_with_input(["sleep"], "", max_output_chars=MAX_OUTPUT_CHARS + 1)

    def test_run_with_input_rejects_non_text_input(self) -> None:
        with self.assertRaisesRegex(OutputError, "text must be text"):
            _run_with_input(["echo"], 123)  # type: ignore[arg-type]

    def test_validate_text_input_rejects_unpaired_surrogate(self) -> None:
        with self.assertRaisesRegex(OutputError, "command input contains invalid Unicode"):
            _validate_text_input("\ud800")

    def test_run_with_input_accepts_tuple_argv(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/echo"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["echo"], 0, stdout=b"", stderr=b"")),
            ),
        ):
            _run_with_input(("echo", "x"), "in")

    def test_run_with_input_wraps_output_capture_read_failure(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/echo"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["echo"], 0, stdout=b"", stderr=b"")),
            ),
            mock.patch("speed_of_cinnamon.output._filesize", side_effect=OSError("capture read failed")),
        ):
            with self.assertRaisesRegex(OutputError, "output could not be read"):
                _run_with_input(("echo", "x"), "in")

    def test_run_with_input_preserves_process_error_when_capture_close_fails(self) -> None:
        stdout_file = mock.MagicMock()
        stdout_file.tell.return_value = 0
        stderr_file = mock.MagicMock()
        stderr_file.tell.return_value = 0
        stderr_file.close.side_effect = OSError("stderr close failed")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["cmd"], 1, stdout=b"", stderr=b"")),
            ),
            mock.patch("speed_of_cinnamon.output.tempfile.TemporaryFile", side_effect=[stdout_file, stderr_file]),
        ):
            with self.assertRaisesRegex(OutputError, "failed with exit code 1") as caught:
                _run_with_input(["cmd"], "input")

        self.assertIn("stderr close failed", "\n".join(caught.exception.__notes__))

    def test_run_with_input_wraps_capture_creation_failure(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch(
                "speed_of_cinnamon.output.tempfile.TemporaryFile",
                side_effect=OSError("capture create failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "failed to prepare output capture"):
                _run_with_input(["cmd"], "input")

    def test_run_with_input_wraps_capture_close_failure(self) -> None:
        stdout_file = mock.MagicMock()
        stdout_file.tell.return_value = 0
        stdout_file.close.side_effect = OSError("stdout close failed")
        stderr_file = mock.MagicMock()
        stderr_file.tell.return_value = 0

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")),
            ),
            mock.patch("speed_of_cinnamon.output.tempfile.TemporaryFile", side_effect=[stdout_file, stderr_file]),
        ):
            with self.assertRaisesRegex(OutputError, "output cleanup failed"):
                _run_with_input(["cmd"], "input")

    def test_run_with_input_rejects_command_error_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stderr = cast(BinaryIO, kwargs["stderr"])
            stderr.write(b"boom")
            return subprocess.CompletedProcess(["cmd"], 1, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            with self.assertRaisesRegex(OutputError, "failed with exit code 1"):
                _run_with_input(["cmd"], "input")

    def test_run_with_input_wraps_process_argument_value_error(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=ValueError("invalid process argument")),
        ):
            with self.assertRaisesRegex(OutputError, "cmd failed to execute"):
                _run_with_input(["cmd"], "input")

    def test_run_with_input_redacts_command_error_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stderr = cast(BinaryIO, kwargs["stderr"])
            stderr.write(b"Authorization: Bearer secret-token")
            return subprocess.CompletedProcess(["cmd"], 1, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            with self.assertRaisesRegex(OutputError, "failed with exit code 1") as raised:
                _run_with_input(["cmd"], "input")

        self.assertNotIn("secret-token", str(raised.exception))

    def test_run_with_input_rejects_oversized_text(self) -> None:
        with self.assertRaisesRegex(OutputError, "command input is too large"):
            with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"):
                _run_with_input(["cmd"], "x" * (1_000_001))

    def test_run_with_input_rejects_oversized_text_bytes(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.MAX_INPUT_CHARS", 4),
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
        ):
            with self.assertRaisesRegex(OutputError, "command input is too large"):
                _run_with_input(["cmd"], "😀" * 2)

    def test_run_with_input_rejects_negative_output_limit(self) -> None:
        with self.assertRaisesRegex(OutputError, "max_output_chars must be non-negative"):
            _run_with_input(["cmd"], "input", max_output_chars=-1)

    def test_run_with_input_rejects_missing_command(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value=None):
            with self.assertRaisesRegex(OutputError, "is not available"):
                _run_with_input(["missing"], "input")

    def test_run_with_input_resolves_command_from_which(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            calls.append(command)
            stdout = cast(BinaryIO, kwargs["stdout"])
            stdout.write(b"ok")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            _run_with_input(["cmd", "arg"], "input")

        self.assertEqual(calls, [["/usr/bin/cmd", "arg"]])

    def test_run_with_input_filters_dangerous_environment_variables(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            return subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch.dict(
                "speed_of_cinnamon.output.os.environ",
                {
                    "LD_PRELOAD": "malicious-lib.so",
                    "PYTHONPATH": "/tmp/evil",
                    "HOME": "/tmp/home",
                    "LANG": "en_US.UTF-8",
                    "DISPLAY": ":0",
                    "WAYLAND_DISPLAY": "wayland-0",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                },
                clear=True,
            ),
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            _run_with_input(["cmd"], "hello")

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertEqual(captured_env["DISPLAY"], ":0")
        self.assertEqual(captured_env["WAYLAND_DISPLAY"], "wayland-0")
        self.assertEqual(captured_env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(captured_env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_run_with_input_skips_non_text_environment_values(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            stdout = kwargs["stdout"]
            stdout.write(b"ok")
            return subprocess.CompletedProcess(["echo"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.os.environ.__getitem__", return_value=123),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            _run_with_input(["echo"], "hello")

        self.assertNotIn("HOME", captured_env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", captured_env)
        self.assertIn("PATH", captured_env)

    def test_run_with_input_rejects_missing_command_when_resolved_path_missing(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/missing"):
            with mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=FileNotFoundError("missing")):
                with self.assertRaisesRegex(OutputError, "missing is not available"):
                    _run_with_input(["missing"], "input")

    def test_run_with_input_rejects_null_byte_in_command_argument(self) -> None:
        with self.assertRaisesRegex(OutputError, "command argument contains invalid null byte"):
            _run_with_input(["cmd", "bad\x00arg"], "input")

    def test_run_with_input_rejects_escaped_null_in_command_argument(self) -> None:
        with self.assertRaisesRegex(OutputError, "command argument contains invalid null byte"):
            _run_with_input(["cmd", "bad\\x00arg"], "input")

    def test_run_with_input_rejects_control_characters_in_command_argument(self) -> None:
        with self.assertRaisesRegex(OutputError, "command argument contains invalid control character"):
            _run_with_input(["cmd", "bad\\narg"], "input")

    def test_run_with_input_rejects_empty_executable(self) -> None:
        with self.assertRaisesRegex(OutputError, "command is empty"):
            _run_with_input([""], "input")

    def test_run_with_input_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(OutputError, "timeout must be positive"):
            _run_with_input(["sleep"], "", timeout=0)

    def test_run_with_input_rejects_timeout(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=_TimeoutPopen()),
            mock.patch("speed_of_cinnamon.output._reap_timed_out_output_process", return_value=True),
        ):
            with self.assertRaisesRegex(OutputError, "timed out"):
                _run_with_input(["sleep"], "", timeout=1)

    def test_reap_does_not_signal_already_reaped_process(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.poll.return_value = 0
        process.returncode = 0
        process.communicate.return_value = (b"", b"")
        with mock.patch("speed_of_cinnamon.output.os.killpg") as mocked_killpg:
            self.assertTrue(output_module._reap_timed_out_output_process(process))

        mocked_killpg.assert_not_called()
        process.communicate.assert_called_once_with(timeout=None)

    def test_reap_kills_unreaped_process_group_before_waiting(self) -> None:
        process = mock.Mock()
        process.pid = 1234
        process.returncode = None
        process.poll.side_effect = AssertionError("must not reap leader before group kill")
        process.communicate.return_value = (b"", b"")
        with mock.patch("speed_of_cinnamon.output.os.killpg") as mocked_killpg:
            self.assertTrue(output_module._reap_timed_out_output_process(process))

        mocked_killpg.assert_called_once_with(1234, output_module.signal.SIGKILL)
        process.communicate.assert_called_once_with(timeout=None)

    def test_reaped_process_group_cleanup_kills_live_descendants(self) -> None:
        process = subprocess.Popen(
            ["/bin/sh", "-c", "sleep 30 & child=$!; echo $child; exit 0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        child_pid = int(process.stdout.readline())
        process.wait()

        def child_is_live() -> bool:
            try:
                state = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
            except OSError:
                return False
            return state not in {"Z", "X", "x"}

        try:
            self.assertTrue(child_is_live())
            self.assertTrue(output_module._terminate_output_process_group(process))
            deadline = time.monotonic() + 2
            while child_is_live() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(child_is_live())
        finally:
            try:
                if child_is_live():
                    os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            process.communicate()

    def test_output_process_is_reaped_when_wait_is_interrupted(self) -> None:
        for invoke in (
            lambda: _run_with_input(["cmd"], "input", resolved_command="/usr/bin/cmd"),
            lambda: output_module._run_stdout_raw(["cmd"], timeout=1, resolved_command="/usr/bin/cmd"),
        ):
            with self.subTest(invoke=invoke):
                process = _InterruptPopen(subprocess.CompletedProcess(["cmd"], 0))
                with (
                    mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=process),
                    mock.patch("speed_of_cinnamon.output._reap_timed_out_output_process") as mocked_reap,
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        invoke()
                mocked_reap.assert_called_once_with(process)

    def test_output_process_is_reaped_when_wait_fails(self) -> None:
        for invoke in (
            lambda: _run_with_input(["cmd"], "input", resolved_command="/usr/bin/cmd"),
            lambda: output_module._run_stdout_raw(["cmd"], timeout=1, resolved_command="/usr/bin/cmd"),
        ):
            with self.subTest(invoke=invoke):
                process = _OSErrorPopen(subprocess.CompletedProcess(["cmd"], 0))
                with (
                    mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=process),
                    mock.patch("speed_of_cinnamon.output._reap_timed_out_output_process") as mocked_reap,
                ):
                    try:
                        invoke()
                    except OutputError:
                        pass
                mocked_reap.assert_called_once_with(process)

    def test_output_process_is_reaped_when_wait_reports_missing_file(self) -> None:
        process = _FileNotFoundPopen(subprocess.CompletedProcess(["cmd"], 0))
        with (
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=process),
            mock.patch("speed_of_cinnamon.output._reap_timed_out_output_process") as mocked_reap,
        ):
            with self.assertRaisesRegex(OutputError, "is not available"):
                _run_with_input(["cmd"], "input", resolved_command="/usr/bin/cmd")
        mocked_reap.assert_called_once_with(process)

    def test_timeout_preserves_primary_error_when_process_cleanup_is_interrupted(self) -> None:
        process = _TimeoutPopen()
        with (
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=process),
            mock.patch(
                "speed_of_cinnamon.output._reap_timed_out_output_process",
                side_effect=KeyboardInterrupt("cleanup interrupted"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "timed out") as caught:
                _run_with_input(["cmd"], "input", resolved_command="/usr/bin/cmd")

        self.assertIsInstance(caught.exception.__cause__, subprocess.TimeoutExpired)
        self.assertIn("cleanup interrupted", "\n".join(caught.exception.__cause__.__notes__))

        process = _TimeoutPopen()
        with (
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=process),
            mock.patch(
                "speed_of_cinnamon.output._reap_timed_out_output_process",
                side_effect=KeyboardInterrupt("cleanup interrupted"),
            ),
        ):
            self.assertIsNone(
                output_module._run_stdout_raw(["cmd"], timeout=1, resolved_command="/usr/bin/cmd")
            )

    def test_run_stdout_timeout_kills_process_group_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child.pid"
            command = f"sleep 30 & child=$!; echo $child > {marker}; wait $child"
            result = output_module._run_stdout_raw(["sh", "-c", command], timeout=1)
            child = int(marker.read_text(encoding="ascii"))

            deadline = time.monotonic() + 2
            live = True
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child}/stat").read_text(encoding="ascii")
                    state = raw.rsplit(")", 1)[1].split()[0]
                    live = state not in {"Z", "X", "x"}
                except OSError:
                    live = False
                if not live:
                    break
                time.sleep(0.01)
            if live:
                try:
                    os.kill(child, 9)
                except ProcessLookupError:
                    pass

        self.assertIsNone(result)
        self.assertFalse(live)

    def test_run_with_input_timeout_kills_process_group_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child.pid"
            command = f"sleep 30 & child=$!; echo $child > {marker}; wait $child"
            with self.assertRaisesRegex(OutputError, "timed out"):
                _run_with_input(["sh", "-c", command], "", timeout=1)
            child = int(marker.read_text(encoding="ascii"))

            deadline = time.monotonic() + 2
            live = True
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child}/stat").read_text(encoding="ascii")
                    state = raw.rsplit(")", 1)[1].split()[0]
                    live = state not in {"Z", "X", "x"}
                except OSError:
                    live = False
                if not live:
                    break
                time.sleep(0.01)
            if live:
                try:
                    os.kill(child, 9)
                except ProcessLookupError:
                    pass

        self.assertFalse(live)

    def test_run_with_input_limits_output_size(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = cast(BinaryIO, kwargs["stdout"])
            stdout.write(b"x" * 200)
            return subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")

        with mock.patch("speed_of_cinnamon.output.MAX_OUTPUT_CHARS", 100):
            with (
                mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
                mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
            ):
                with self.assertRaisesRegex(OutputError, "too much output"):
                    _run_with_input(["cmd"], "input")

    def test_run_stdout_rejects_non_sequence_argv(self) -> None:
        with self.assertRaisesRegex(OutputError, "argv must be a sequence"):
            _run_stdout("xdotool", timeout=1)  # type: ignore[arg-type]

    def test_run_stdout_rejects_non_text_argv(self) -> None:
        with self.assertRaisesRegex(OutputError, "command arguments must be text"):
            _run_stdout(["xdotool", 12], timeout=1)  # type: ignore[list-item]

    def test_run_stdout_rejects_empty_executable(self) -> None:
        with self.assertRaisesRegex(OutputError, "command is empty"):
            _run_stdout(["   "], timeout=1)

    def test_run_stdout_rejects_command_with_path_separator(self) -> None:
        with self.assertRaisesRegex(OutputError, "path separators"):
            _run_stdout(["bin/cmd"], timeout=1)

    def test_run_stdout_rejects_non_int_timeout(self) -> None:
        with self.assertRaisesRegex(OutputError, "timeout must be an integer"):
            _run_stdout(["xdotool"], timeout="1")  # type: ignore[arg-type]

    def test_run_stdout_rejects_bool_timeout(self) -> None:
        with self.assertRaisesRegex(OutputError, "timeout must be an integer"):
            _run_stdout(["xdotool"], timeout=True)  # type: ignore[arg-type]

    def test_run_stdout_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(OutputError, "timeout must be positive"):
            _run_stdout(["xdotool"], timeout=0)

    def test_run_stdout_rejects_null_byte_argument(self) -> None:
        with self.assertRaisesRegex(OutputError, "command argument contains invalid null byte"):
            _run_stdout(["xdotool", "bad\x00arg"], timeout=1)

    def test_run_stdout_rejects_control_chars_in_argument(self) -> None:
        with self.assertRaisesRegex(OutputError, "command argument contains invalid control character"):
            _run_stdout(["xdotool", "bad\\x85arg"], timeout=1)

    def test_run_stdout_resolves_command_from_which(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            _run_stdout(["xdotool", "-h"])

        self.assertEqual(calls[0][0], "/usr/bin/xdotool")

    def test_run_stdout_returns_empty_when_process_argument_is_invalid(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                side_effect=ValueError("invalid process argument"),
            ),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_returns_empty_when_output_capture_read_fails(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["xdotool"], 0, stdout=b"ok", stderr=b"")),
            ),
            unittest.mock.patch("speed_of_cinnamon.output._filesize", side_effect=OSError("capture read failed")),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_returns_empty_when_capture_close_fails(self) -> None:
        stdout_file = mock.MagicMock()
        stdout_file.tell.return_value = 0
        stdout_file.read.return_value = b""
        stderr_file = mock.MagicMock()
        stderr_file.tell.return_value = 0
        stderr_file.read.return_value = b""
        stderr_file.close.side_effect = OSError("stderr close failed")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["xdotool"], 0, stdout=b"", stderr=b"")),
            ),
            unittest.mock.patch(
                "speed_of_cinnamon.output.tempfile.TemporaryFile",
                side_effect=[stdout_file, stderr_file],
            ),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_returns_empty_when_capture_close_is_interrupted(self) -> None:
        stdout_file = mock.MagicMock()
        stdout_file.tell.return_value = 0
        stdout_file.read.return_value = b""
        stderr_file = mock.MagicMock()
        stderr_file.tell.return_value = 0
        stderr_file.read.return_value = b""
        stderr_file.close.side_effect = KeyboardInterrupt("stderr close interrupted")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["xdotool"], 0, stdout=b"", stderr=b"")),
            ),
            unittest.mock.patch(
                "speed_of_cinnamon.output.tempfile.TemporaryFile",
                side_effect=[stdout_file, stderr_file],
            ),
        ):
            self.assertEqual(output_module._run_stdout_raw(["xdotool", "--help"]), None)

    def test_run_stdout_uses_file_backed_output_capture(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            stdout = cast(BinaryIO, kwargs["stdout"])
            stderr = cast(BinaryIO, kwargs["stderr"])
            self.assertIsNot(stdout, subprocess.PIPE)
            self.assertIsNot(stderr, subprocess.PIPE)
            stdout.write(b"file output\n")
            return subprocess.CompletedProcess(command, 0, stdout=b"ignored", stderr=b"")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_run_stdout(["xdotool", "-h"]), "file output")

    def test_run_stdout_rejects_file_backed_oversized_stdout(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            stdout = cast(BinaryIO, kwargs["stdout"])
            stdout.write(b"x" * (MAX_OUTPUT_CHARS + 1))
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_accepts_tuple_argv(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["xdotool"], 0, stdout=b"x", stderr=b"")),
            ),
        ):
            self.assertEqual(_run_stdout(("xdotool", "-h")), "x")

    def test_run_stdout_rejects_oversized_stdout(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            return subprocess.CompletedProcess(command, 0, stdout=b"x" * (MAX_OUTPUT_CHARS + 1), stderr=b"")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_rejects_oversized_stderr(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"x" * (MAX_OUTPUT_CHARS + 1))

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_rejects_invalid_utf8_output(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["xdotool"], 0, stdout=b"ok\xff", stderr=b"")),
            ),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_rejects_output_with_null_byte(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.Popen",
                return_value=_FakePopen(subprocess.CompletedProcess(["xdotool"], 0, stdout=b"abc\x00def", stderr=b"")),
            ),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_read_file_head_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok\xff")
            with self.assertRaisesRegex(OutputError, "not valid UTF-8"):
                _read_file_head(handle, 10)

    def test_read_file_head_rejects_escaped_null(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write("ok\\x00end".encode("utf-8"))
            with self.assertRaisesRegex(OutputError, "contains invalid null byte"):
                _read_file_head(handle, 10)

    def test_paste_without_helper_is_error(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value=None):
            with self.assertRaisesRegex(OutputError, "automatic paste helper"):
                paste_from_clipboard(expected_window_snapshot=("123", "Editor", "xed"))

    def test_paste_from_clipboard_avoids_duplicate_xdotool_lookup(self) -> None:
        which_calls: list[str] = []

        def fake_which(command: str, path: str | None = None) -> str | None:
            which_calls.append(command)
            return "/usr/bin/xdotool" if command == "xdotool" else None

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            if "getactivewindow" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"123\n", stderr=b"")
            if "getwindowclassname" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"Firefox\n", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=True),
        ):
            paste_from_clipboard(expected_window_snapshot=("123", "Firefox", "Firefox"))

        self.assertEqual(which_calls.count("xdotool"), 1)
        self.assertEqual(which_calls.count("wtype"), 0)

    def test_paste_from_clipboard_rejects_wtype_without_verifiable_window(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            return "/usr/bin/wtype" if command == "wtype" else None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            with self.assertRaisesRegex(OutputError, "without verifiable active window"):
                paste_from_clipboard()

        mocked_run.assert_not_called()

    def test_paste_from_clipboard_does_not_fallback_after_xdotool_key_error(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            if command == "xdotool":
                return "/usr/bin/xdotool"
            if command == "wtype":
                return "/usr/bin/wtype"
            return None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=True),
            mock.patch("speed_of_cinnamon.output._run_with_input", side_effect=OutputError("xdotool failed")) as mocked_run,
            mock.patch("speed_of_cinnamon.output.log_event") as mocked_log,
        ):
            with self.assertRaisesRegex(OutputError, "xdotool failed"):
                paste_from_clipboard(expected_window_snapshot=("1", "Terminal", "Gnome-terminal"))

        mocked_log.assert_not_called()
        mocked_run.assert_called_once_with(
            ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"],
            "",
            timeout=10,
            resolved_command="/usr/bin/xdotool",
        )

    def test_paste_from_clipboard_does_not_fallback_to_wtype_if_xdotool_is_unreliable(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            if command == "xdotool":
                return "/usr/bin/xdotool"
            if command == "wtype":
                return "/usr/bin/wtype"
            return None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", side_effect=OutputError("xdotool unavailable")),
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            with self.assertRaisesRegex(OutputError, "xdotool unavailable"):
                paste_from_clipboard(expected_window_snapshot=("1", "Editor", "xed"))

        mocked_run.assert_not_called()

    def test_paste_from_clipboard_reports_xdotool_error_without_wtype_fallback(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            return "/usr/bin/xdotool" if command == "xdotool" else None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=True),
            mock.patch("speed_of_cinnamon.output._run_with_input", side_effect=OutputError("xdotool failed")),
        ):
            with self.assertRaisesRegex(OutputError, "xdotool failed"):
                paste_from_clipboard(expected_window_snapshot=("1", "Editor", "xed"))

    def test_active_window_paste_key_uses_shift_for_terminal_class(self) -> None:
        self._use_real_active_window_snapshot()

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            if "getactivewindow" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"123\n", stderr=b"")
            if "getwindowclassname" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"Gnome-terminal\n", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_active_window_paste_key(), "ctrl+shift+v")

    def test_active_window_paste_key_falls_back_to_normal_paste(self) -> None:
        self._use_real_active_window_snapshot()

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            if "getactivewindow" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"123\n", stderr=b"")
            if "getwindowclassname" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"Firefox\n", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_active_window_paste_key(), "ctrl+v")

    def test_active_window_paste_key_uses_shift_for_terminal_title(self) -> None:
        self._use_real_active_window_snapshot()

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            if "getactivewindow" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"123\n", stderr=b"")
            if "getwindowname" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"codex terminal session\n", stderr=b"")
            if "getwindowclassname" in command:
                return subprocess.CompletedProcess(command, 0, stdout=b"Firefox\n", stderr=b"")
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
        ):
            self.assertEqual(_active_window_paste_key(), "ctrl+shift+v")

    def test_terminal_marker_matching_is_conservative(self) -> None:
        self.assertTrue(_looks_like_terminal("Codex"))
        self.assertTrue(_looks_like_terminal("org.gnome.Terminal"))
        self.assertTrue(_looks_like_terminal("Termius"))
        self.assertTrue(_looks_like_terminal("COSMIC Terminal"))
        self.assertTrue(_looks_like_terminal("tty"))
        self.assertFalse(_looks_like_terminal("firefox"))

    def test_type_text_rejects_null_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"):
            with self.assertRaisesRegex(OutputError, "command input contains invalid null byte"):
                type_text("hello\x00", 8)

    def test_type_text_rejects_non_int_delay(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"):
            with self.assertRaisesRegex(OutputError, "typing delay must be an integer"):
                type_text("hello", "8")  # type: ignore[arg-type]

    def test_type_text_requires_verifiable_window_snapshot(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            with self.assertRaisesRegex(OutputError, "without verifiable active window"):
                type_text("hello", 8)

        mocked_run.assert_not_called()

    def test_type_text_rejects_changed_window_snapshot(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=False),
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            with self.assertRaisesRegex(OutputError, "active window changed"):
                type_text("hello", 8, expected_window_snapshot=("123", "Editor", "xed"))

        mocked_run.assert_not_called()

    def test_insert_text_rejects_non_text_method(self) -> None:
        with self.assertRaisesRegex(OutputError, "method must be text"):
            insert_text("hello", 1)  # type: ignore[arg-type]

    def test_insert_text_rejects_control_character_method(self) -> None:
        with self.assertRaisesRegex(OutputError, "method contains invalid control character"):
            insert_text("hello", "\x85clipboard-paste")

    def test_insert_text_rejects_escaped_control_character_method(self) -> None:
        with self.assertRaisesRegex(OutputError, "method contains invalid control character"):
            insert_text("hello", "\\x85clipboard-paste")

    def test_insert_text_avoids_duplicate_clipboard_insertion(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard"),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=1.0),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))
            self.assertFalse(insert_text("wiederholung", "clipboard-paste"))

    def test_insert_text_skips_empty_clipboard_text(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            self.assertFalse(insert_text("", "clipboard"))

        mocked_clipboard.assert_not_called()

    def test_insert_text_allows_whitespace_only_clipboard_text(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            self.assertTrue(insert_text(" \t\n", "clipboard"))

        mocked_clipboard.assert_called_once_with(" \t\n")

    def test_insert_text_allows_distinct_clipboard_whitespace_variation(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard"),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=1.0),
        ):
            self.assertTrue(insert_text("  wiederholung  ", "clipboard-paste"))
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

    def test_insert_text_avoids_duplicate_raw_clipboard_insertion(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=2.0),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard"))
            self.assertFalse(insert_text("wiederholung", "clipboard"))

    def test_insert_text_avoids_duplicate_across_clipboard_methods(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard"),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=3.0),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard"))
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

    def test_insert_text_reserves_duplicate_state_before_paste(self) -> None:
        calls: list[str] = []

        def fake_paste(*args: object, **kwargs: object) -> None:
            calls.append("paste")
            self.assertFalse(insert_text("wiederholung", "clipboard-paste"))

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("100", "Editor", "Xed")),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard", side_effect=fake_paste),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=3.0),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual(calls, ["paste"])

    def test_insert_text_dedupe_uses_exact_clipboard_text(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=3.0),
        ):
            self.assertTrue(insert_text("eins\nzwei", "clipboard"))
            self.assertTrue(insert_text("eins zwei", "clipboard"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["eins\nzwei", "eins zwei"])

    def test_insert_text_reads_persistent_clipboard_dedupe_state_once(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard"),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output._read_clipboard_dedup_state_entry",
                wraps=output_module._read_clipboard_dedup_state_entry,
            ) as mocked_read,
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual(mocked_read.call_count, 1)

    def test_insert_text_keeps_pending_duplicate_state_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=[OutputError("paste failed"), None],
            ) as mocked_paste,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=4.0),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("wiederholung", "clipboard-paste")
            self.assertFalse(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["wiederholung", ""])
        self.assertEqual(mocked_paste.call_count, 1)

    def test_insert_text_marks_dedupe_state_pending_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.time.time", return_value=5.0),
        ):
            self.assertTrue(output_module._write_clipboard_dedup_state("previous text", 1.0))
            initial_state = output_module._read_clipboard_dedup_state()
            with (
                mock.patch("speed_of_cinnamon.output.set_clipboard"),
                mock.patch("speed_of_cinnamon.output.paste_from_clipboard", side_effect=OutputError("paste failed")),
                mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
                mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
                mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
                mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            ):
                with self.assertRaisesRegex(OutputError, "paste failed"):
                    insert_text("wiederholung", "clipboard-paste")

            self.assertEqual(
                output_module._read_clipboard_dedup_state_entry(),
                (
                    True,
                    (
                        self._expected_paste_fingerprint("wiederholung"),
                        5.0,
                    ),
                    True,
                ),
            )

    def test_insert_text_clipboard_paste_requires_keyboard_helper_before_clipboard_write(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._which", return_value=None),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False) as mocked_non_text,
        ):
            with self.assertRaisesRegex(OutputError, "automatic paste helper"):
                insert_text("wiederholung", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()
        mocked_non_text.assert_not_called()

    def test_clipboard_paste_refuses_wl_copy_only_when_using_xdotool(self) -> None:
        def fake_which(command: str) -> str | None:
            return {
                "xdotool": "/usr/bin/xdotool",
                "wl-copy": "/usr/bin/wl-copy",
                "wl-paste": "/usr/bin/wl-paste",
            }.get(command)

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("1", "Editor", "Xed")),
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            with self.assertRaisesRegex(OutputError, "X11 clipboard helper"):
                insert_text("wiederholung", "clipboard-paste")

        mocked_run.assert_not_called()

    def test_insert_text_commits_dedupe_state_when_paste_succeeds(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.time.time", return_value=9.5),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))
            expected = self._expected_paste_fingerprint("wiederholung")
            self.assertEqual(
                output_module._read_clipboard_dedup_state(),
                (expected, 9.5),
            )
            self.assertEqual(mocked_clipboard.call_count, 1)
            self.assertEqual(mocked_paste.call_count, 1)

    def test_insert_text_marks_pending_before_paste_and_commits_after_success(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.time", return_value=11.0),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            state_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_STATE_FILE

            def fake_paste(*args: object, **kwargs: object) -> None:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertTrue(payload.get("pending"))
                self.assertEqual(
                    payload["sha256"],
                    self._expected_paste_fingerprint("wiederholung"),
                )

            with mock.patch("speed_of_cinnamon.output.paste_from_clipboard", side_effect=fake_paste):
                self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                {
                    "sha256": self._expected_paste_fingerprint("wiederholung"),
                    "at": 11.0,
                },
            )
            mocked_clipboard.assert_called_once_with("wiederholung", allowed_helpers=("xclip", "xsel"))

    def test_insert_text_keeps_pending_clipboard_state_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.time.time", return_value=5.0),
        ):
            self.assertTrue(output_module._write_clipboard_dedup_state("previous text", 1.0))
            initial_state = output_module._read_clipboard_dedup_state()
            state_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_STATE_FILE

            def fake_paste(*args: object, **kwargs: object) -> None:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                self.assertTrue(payload.get("pending"))
                self.assertEqual(
                    payload["sha256"],
                    self._expected_paste_fingerprint("wiederholung"),
                )
                raise OutputError("paste failed")

            with (
                mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
                mock.patch("speed_of_cinnamon.output.paste_from_clipboard", side_effect=fake_paste) as mocked_paste,
                mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value="previous text"),
                mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
                mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
                mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            ):
                with self.assertRaisesRegex(OutputError, "paste failed"):
                    insert_text("wiederholung", "clipboard-paste")
                self.assertEqual(mocked_paste.call_count, 1)

            self.assertEqual(
                output_module._read_clipboard_dedup_state_entry(),
                (
                    True,
                    (
                        self._expected_paste_fingerprint("wiederholung"),
                        5.0,
                    ),
                    True,
                ),
            )
            self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["wiederholung", "previous text"])

    def test_clipboard_dedup_state_read_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                state_root = Path(tmp) / "speed-of-cinnamon"
                state_root.mkdir()
                state_path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
                state_path.write_text("abcde", encoding="utf-8")

                with mock.patch.object(output_module, "MAX_CLIPBOARD_DEDUP_STATE_BYTES", 4):
                    entry = output_module._read_clipboard_dedup_state_entry()

        self.assertEqual(entry, (False, ("", 0.0), False))

    def test_clipboard_dedup_lock_read_rejects_oversized_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / output_module.CLIPBOARD_DEDUP_LOCK_FILE
            lock_path.write_text("12345\n", encoding="utf-8")

            with mock.patch.object(output_module, "MAX_CLIPBOARD_DEDUP_LOCK_BYTES", 4):
                pid = output_module._read_clipboard_dedup_lock_pid(lock_path)
                identity = output_module._read_clipboard_dedup_lock_identity(lock_path)

        self.assertIsNone(pid)
        self.assertIsNone(identity)

    def test_clipboard_lock_read_preserves_error_when_fd_close_is_interrupted(self) -> None:
        with (
            mock.patch.object(output_module.os, "open", return_value=123),
            mock.patch.object(output_module.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(output_module.os, "read", side_effect=RuntimeError("read failed")),
            mock.patch.object(output_module.os, "close", side_effect=KeyboardInterrupt),
        ):
            with self.assertRaisesRegex(RuntimeError, "read failed") as caught:
                output_module._read_clipboard_dedup_lock_lines_at(456, "lock")

        self.assertIn("clipboard lock cleanup failed", "\n".join(caught.exception.__notes__))

    def test_clipboard_lock_read_fails_closed_when_fd_close_fails(self) -> None:
        with (
            mock.patch.object(output_module.os, "open", return_value=123),
            mock.patch.object(output_module.os, "fstat", return_value=os.stat(__file__)),
            mock.patch.object(output_module.os, "read", return_value=b"123\n"),
            mock.patch.object(output_module.os, "close", side_effect=OSError("close failed")),
        ):
            self.assertIsNone(output_module._read_clipboard_dedup_lock_lines_at(456, "lock"))

    def test_insert_text_ignores_expired_pending_duplicate_when_stale_lock_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                state_root = Path(tmp) / "speed-of-cinnamon"
                state_root.mkdir()
                state_path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
                now = output_module.time.time()
                stale = now - output_module.MAX_DUPLICATE_LOCK_SECONDS - 1.0
                state_path.write_text(
                    json.dumps(
                        {
                            "sha256": output_module._clipboard_text_fingerprint("wiederholung"),
                            "at": stale,
                            "pending": True,
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                lock_path = state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.write_text("999999999\n", encoding="utf-8")
                os.utime(lock_path, (stale, stale))

                with (
                    mock.patch("speed_of_cinnamon.output._clipboard_lock_pid_is_running", return_value=False),
                    mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
                    mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "wiederholung")),
                    mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
                    mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
                ):
                    self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

            mocked_clipboard.assert_called_once_with("wiederholung", allowed_helpers=("xclip", "xsel"))
            mocked_paste.assert_called_once()

    def test_insert_text_fails_closed_without_readable_text_clipboard_snapshot(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(False, "")),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            with self.assertRaisesRegex(OutputError, "readable text clipboard snapshot"):
                insert_text("new text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_refuses_clipboard_paste_without_verifiable_window(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            if command == "wtype":
                return "/usr/bin/wtype"
            if command == "xclip":
                return "/usr/bin/xclip"
            return None

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            with self.assertRaisesRegex(OutputError, "automatic paste helper"):
                insert_text("new text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_fails_closed_when_clipboard_changes_before_overwrite(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, "previous text"), (True, "external change")],
            ),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=False),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            with self.assertRaisesRegex(OutputError, "clipboard changed before automatic paste"):
                insert_text("new text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_fails_closed_when_active_window_changes_before_paste(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            if command == "xdotool":
                return "/usr/bin/xdotool"
            if command == "xclip":
                return "/usr/bin/xclip"
            return None

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            with self.assertRaisesRegex(OutputError, "active window changed before automatic paste"):
                insert_text("new text", "clipboard-paste")

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", "previous text"])
        mocked_run.assert_not_called()

    def test_insert_text_does_not_restore_previous_text_clipboard_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, "previous text"), (True, "previous text"), (True, "new text")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=OutputError("paste failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", "previous text"])

    def test_insert_text_preserves_paste_error_when_clipboard_restore_is_interrupted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_helper_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_writer_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch(
                "speed_of_cinnamon.output.set_clipboard",
                side_effect=[None, KeyboardInterrupt("clipboard restore interrupted")],
            ),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard", side_effect=OutputError("paste failed")),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")

    def test_insert_text_preserves_paste_error_when_clipboard_restore_check_is_interrupted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_helper_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_writer_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous text")),
            mock.patch(
                "speed_of_cinnamon.output._clipboard_still_contains_inserted_text",
                side_effect=KeyboardInterrupt("clipboard restore check interrupted"),
            ),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard", side_effect=OutputError("paste failed")),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")

        mocked_clipboard.assert_called_once_with("new text", allowed_helpers=("xclip", "xsel"))

    def test_insert_text_restores_text_clipboard_snapshot_without_stripping(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, " previous text \n"), (True, " previous text \n"), (True, "new text")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=OutputError("paste failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", " previous text \n"])

    def test_text_clipboard_snapshot_preserves_helper_whitespace(self) -> None:
        proc = subprocess.CompletedProcess(["xclip"], 0, stdout=b" previous text \n\n", stderr=b"")
        with (
            mock.patch.object(output_module, "_which", side_effect=lambda name: "/usr/bin/xclip" if name == "xclip" else None),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", return_value=_FakePopen(proc)),
        ):
            available, text = output_module._read_text_clipboard_snapshot()

        self.assertTrue(available)
        self.assertEqual(text, " previous text \n\n")

    def test_insert_text_restores_empty_text_clipboard_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, ""), (True, ""), (True, "new text")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=OutputError("paste failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", ""])

    def test_insert_text_keeps_pending_guard_after_ambiguous_paste_failure(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                return_value=(True, "previous"),
            ),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch(
                "speed_of_cinnamon.output.set_clipboard",
                return_value=None,
            ) as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=OutputError("paste failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")
            trusted, state, pending = output_module._read_clipboard_dedup_state_entry()
            self.assertTrue(trusted)
            self.assertEqual(
                state[0],
                self._expected_paste_fingerprint("new text"),
            )
            self.assertTrue(pending)

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", "previous"])
        self.assertEqual(output_module._LAST_CLIPBOARD_TEXT, "new text")
        self.assertEqual(output_module._LAST_CLIPBOARD_METHOD, "clipboard-paste")

    def test_insert_text_keeps_duplicate_guard_when_paste_commit_fails_after_paste(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "previous")),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
            mock.patch("speed_of_cinnamon.output._commit_clipboard_insertion", return_value=False),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
        ):
            with self.assertRaisesRegex(OutputError, "failed to commit clipboard-paste insertion state"):
                insert_text("new text", "clipboard-paste")
            trusted, snapshot, pending = output_module._read_clipboard_dedup_state_entry()
            self.assertTrue(trusted)
            self.assertEqual(
                snapshot[0],
                self._expected_paste_fingerprint("new text"),
            )
            self.assertTrue(pending)
            self.assertTrue(
                output_module._should_skip_clipboard_memory_duplicate(
                    "new text",
                    "clipboard-paste",
                    dedupe_context=output_module._LAST_CLIPBOARD_CONTEXT,
                )
            )

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", "previous"])
        mocked_paste.assert_called_once()

    def test_insert_text_does_not_restore_stale_clipboard_after_paste_failure(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, "previous"), (True, "previous"), (True, "external change")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=OutputError("paste failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("new text", "clipboard-paste")

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text"])

    def test_insert_text_clipboard_rolls_back_duplicate_state_when_set_clipboard_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard", side_effect=[OutputError("copy failed"), None]),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=10.0),
        ):
            with self.assertRaisesRegex(OutputError, "copy failed"):
                insert_text("copy text", "clipboard")
            self.assertTrue(insert_text("copy text", "clipboard"))

    def test_insert_text_refuses_to_overwrite_non_text_clipboard_when_paste_would_need_rollback(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=True),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            with self.assertRaisesRegex(OutputError, "non-text clipboard"):
                insert_text("new text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_keeps_memory_when_dedupe_state_cannot_persist_after_paste(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
            self.assertTrue(output_module._write_clipboard_dedup_state("previous state", 1.0))
            with (
                mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("100", "Editor", "Xed")),
                mock.patch("speed_of_cinnamon.output._commit_clipboard_insertion", return_value=False),
                mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
                mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
                mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value="previous text"),
                mock.patch(
                    "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                    return_value=(True, "previous text"),
                ),
                mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
                mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
                mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=17.0),
                mock.patch("speed_of_cinnamon.output.time.time", return_value=17.0),
            ):
                with self.assertRaisesRegex(OutputError, "failed to commit clipboard-paste insertion state"):
                    insert_text("wiederholung", "clipboard-paste")
                self.assertFalse(insert_text("wiederholung", "clipboard-paste"))
            trusted, final_state, pending = output_module._read_clipboard_dedup_state_entry()

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["wiederholung", "previous text"])
        self.assertEqual(mocked_paste.call_count, 1)
        self.assertTrue(trusted)
        self.assertEqual(
            final_state[0],
            self._expected_paste_fingerprint("wiederholung"),
        )
        self.assertTrue(pending)
        self.assertEqual(output_module._LAST_CLIPBOARD_TEXT, "wiederholung")
        self.assertEqual(output_module._LAST_CLIPBOARD_METHOD, "clipboard-paste")

    def test_clipboard_targets_treat_rich_text_as_non_text_payload(self) -> None:
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload(""))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("TARGETS\nTIMESTAMP\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("text/html\ntext/plain\n"))
        self.assertFalse(output_module._clipboard_targets_contain_non_text_payload("text/plain;charset=UTF-16\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("text/rtf\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("text/uri-list\ntext/plain\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("text/uri-list;charset=utf-8\ntext/plain;charset=UTF-16\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("text/x-moz-url\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("image/png\ntext/plain\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("image/bmp\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("application/x-qt-image\n"))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("x-special/gnome-copied-files\n"))
        self.assertFalse(output_module._clipboard_targets_contain_non_text_payload("UTF8_STRING\ntext/plain\n"))

    def test_clipboard_non_text_detection_checks_xsel_targets(self) -> None:
        calls: list[list[str]] = []

        def fake_run_stdout(argv: list[str], **_kwargs: object) -> str:
            calls.append(argv)
            return "image/png\n"

        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=lambda command: "/usr/bin/xsel" if command == "xsel" else None),
            mock.patch("speed_of_cinnamon.output._run_stdout_raw", side_effect=fake_run_stdout),
        ):
            self.assertTrue(output_module._clipboard_has_non_text_payload())

        self.assertEqual(calls, [["xsel", "--clipboard", "--output", "--target", "TARGETS"]])

    def test_clipboard_non_text_detection_falls_back_after_xclip_failure(self) -> None:
        calls: list[list[str]] = []

        def fake_which(command: str) -> str | None:
            return f"/usr/bin/{command}" if command in {"xclip", "xsel"} else None

        def fake_run(argv: list[str], **_kwargs: object) -> str | None:
            calls.append(argv)
            return None if argv[0] == "xclip" else "text/plain\n"

        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._run_stdout_raw", side_effect=fake_run),
        ):
            self.assertFalse(output_module._clipboard_has_non_text_payload())

        self.assertEqual([call[0] for call in calls], ["xclip", "xsel"])

    def test_clipboard_non_text_detection_fails_closed_on_empty_targets(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=lambda command: "/usr/bin/xclip" if command == "xclip" else None),
            mock.patch("speed_of_cinnamon.output._run_stdout_raw", return_value=""),
        ):
            self.assertTrue(output_module._clipboard_has_non_text_payload())

    def test_clipboard_non_text_detection_fails_closed_without_target_helpers(self) -> None:
        with mock.patch("speed_of_cinnamon.output._which", return_value=None):
            self.assertTrue(output_module._clipboard_has_non_text_payload())

    def test_active_window_snapshot_match_requires_same_class(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.output._active_x_window_snapshot",
            return_value=("123", "Editor", "Terminal"),
        ):
            self.assertFalse(output_module._active_x_window_matches_snapshot(("123", "Editor", "")))
            self.assertFalse(output_module._active_x_window_matches_snapshot(("123", "Editor", "Code")))
            self.assertTrue(output_module._active_x_window_matches_snapshot(("123", "Editor", "Terminal")))

    def test_active_window_snapshot_match_rejects_title_change_when_known(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.output._active_x_window_snapshot",
            return_value=("123", "Other", "Terminal"),
        ):
            self.assertFalse(output_module._active_x_window_matches_snapshot(("123", "Editor", "Terminal")))

    def test_active_window_snapshot_requires_window_class(self) -> None:
        self._use_real_active_window_snapshot()

        with (
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._run_stdout", side_effect=["123", "Editor", ""]),
        ):
            self.assertIsNone(output_module._active_x_window_snapshot())

    def test_clipboard_paste_duplicate_guard_is_bound_to_target_window(self) -> None:
        first_target = ("100", "Editor A", "Xed")
        second_target = ("200", "Editor B", "Xed")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_helper_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", side_effect=[first_target, second_target]),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "old")),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            self.assertTrue(insert_text("same text", "clipboard-paste"))
            self.assertTrue(insert_text("same text", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["same text", "same text"])
        self.assertEqual(mocked_paste.call_count, 2)

    def test_clipboard_paste_duplicate_guard_survives_title_change_in_same_window(self) -> None:
        first_target = ("100", "Editor A", "Xed")
        renamed_target = ("100", "Editor A - changed", "Xed")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_helper_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", side_effect=[first_target, renamed_target]),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "old")),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            self.assertTrue(insert_text("same text", "clipboard-paste"))
            self.assertFalse(insert_text("same text", "clipboard-paste"))

        mocked_clipboard.assert_called_once_with("same text", allowed_helpers=("xclip", "xsel"))
        mocked_paste.assert_called_once()

    def test_clipboard_copy_does_not_suppress_followup_clipboard_paste(self) -> None:
        target = ("100", "Editor", "Xed")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=target),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "old")),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            self.assertTrue(insert_text("same text", "clipboard"))
            self.assertTrue(insert_text("same text", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["same text", "same text"])
        mocked_paste.assert_called_once()

    def test_clipboard_restore_does_not_overwrite_new_non_text_payload_with_same_text(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=True),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            output_module._restore_clipboard_snapshot_after_failed_paste("same text", True, "old text")

        mocked_clipboard.assert_not_called()

    def test_clipboard_paste_duplicate_guard_skips_same_target_window(self) -> None:
        target = ("100", "Editor A", "Xed")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._clipboard_paste_helper_available", return_value=True),
            mock.patch("speed_of_cinnamon.output._which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=target),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "old")),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            self.assertTrue(insert_text("same text", "clipboard-paste"))
            self.assertFalse(insert_text("same text", "clipboard-paste"))

        mocked_clipboard.assert_called_once_with("same text", allowed_helpers=("xclip", "xsel"))
        mocked_paste.assert_called_once()

    def test_insert_text_empty_clipboard_text_is_noop(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            self.assertFalse(insert_text("", "clipboard"))
            self.assertFalse(insert_text("", "clipboard-paste"))

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_fails_closed_when_dedupe_state_is_malformed(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            state_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_STATE_FILE
            state_path.parent.mkdir(parents=True)
            state_path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(OutputError, "untrusted clipboard dedupe state"):
                insert_text("secure text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_releases_dedupe_lock_when_state_read_is_interrupted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_clipboard_dedup_state_entry",
                side_effect=KeyboardInterrupt,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                insert_text("secure text", "clipboard")

            lock_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_LOCK_FILE
            self.assertFalse(lock_path.exists())

    def test_insert_text_releases_dedupe_lock_when_memory_reservation_is_interrupted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._reserve_clipboard_insertion_memory",
                side_effect=KeyboardInterrupt,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                insert_text("secure text", "clipboard")

            lock_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_LOCK_FILE
            self.assertFalse(lock_path.exists())

    def test_clipboard_memory_reservation_is_unchanged_when_time_read_is_interrupted(self) -> None:
        output_module._LAST_CLIPBOARD_TEXT = "old text"
        output_module._LAST_CLIPBOARD_METHOD = "clipboard"
        output_module._LAST_CLIPBOARD_INSERTION = 4.0
        output_module._LAST_CLIPBOARD_CONTEXT = "old context"
        with mock.patch("speed_of_cinnamon.output.time.monotonic", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                output_module._reserve_clipboard_insertion_memory("new text", "clipboard")

        self.assertEqual(output_module._clipboard_insertion_snapshot(), ("old text", "clipboard", 4.0, "old context"))

    def test_insert_text_fails_closed_when_dedupe_state_is_invalid_utf8(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            state_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_STATE_FILE
            state_path.parent.mkdir(parents=True)
            state_path.write_bytes(b"{\xff")
            with self.assertRaisesRegex(OutputError, "untrusted clipboard dedupe state"):
                insert_text("secure text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_clipboard_dedup_state_fails_closed_on_json_recursion_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
            path.write_text("{}", encoding="utf-8")
            with (
                mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root),
                mock.patch.object(output_module.json, "loads", side_effect=RecursionError("too deep")),
            ):
                entry = output_module._read_clipboard_dedup_state_entry()

        self.assertEqual(entry, (False, ("", 0.0), False))

    def test_insert_text_fails_closed_when_dedupe_state_is_symlink(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
        ):
            state_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_STATE_FILE
            target_path = Path(tmp) / "target-state.json"
            state_path.parent.mkdir(parents=True)
            target_path.write_text(
                json.dumps({"sha256": output_module._clipboard_text_fingerprint("secure text"), "at": 1.0}),
                encoding="utf-8",
            )
            state_path.symlink_to(target_path)
            with self.assertRaisesRegex(OutputError, "untrusted clipboard dedupe state"):
                insert_text("secure text", "clipboard-paste")

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_clipboard_paste_untrusted_dedupe_does_not_mask_missing_window(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._read_trusted_clipboard_dedup_state", return_value=(False, ("", 0.0))),
            mock.patch("speed_of_cinnamon.output._which", return_value=None),
        ):
            with self.assertRaisesRegex(OutputError, "no automatic paste helper found"):
                insert_text("secure text", "clipboard-paste")

    def test_insert_text_clipboard_fails_closed_when_dedupe_state_cannot_persist(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._write_clipboard_dedup_fingerprint_state", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            with self.assertRaisesRegex(OutputError, "failed to reserve clipboard insertion state"):
                insert_text("secure text", "clipboard")

        mocked_clipboard.assert_not_called()

    def test_insert_text_releases_dedupe_lock_when_state_restore_is_interrupted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._write_clipboard_dedup_fingerprint_state",
                return_value=False,
            ),
            mock.patch(
                "speed_of_cinnamon.output._restore_clipboard_dedup_state",
                side_effect=KeyboardInterrupt,
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                insert_text("secure text", "clipboard")

            lock_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_LOCK_FILE
            self.assertFalse(lock_path.exists())

    def test_insert_text_preserves_clipboard_error_when_state_restore_is_interrupted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._begin_clipboard_insertion",
                return_value=(Path(tmp) / "lock", ("a" * 64, 1.0), False),
            ),
            mock.patch(
                "speed_of_cinnamon.output._write_clipboard_dedup_fingerprint_state",
                side_effect=[True, KeyboardInterrupt("state restore interrupted")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard", side_effect=OutputError("copy failed")),
            mock.patch("speed_of_cinnamon.output._release_clipboard_dedup_lock"),
        ):
            with self.assertRaisesRegex(OutputError, "copy failed"):
                insert_text("secure text", "clipboard")

    def test_clipboard_dedup_restore_ignores_logging_failure(self) -> None:
        with (
            mock.patch(
                "speed_of_cinnamon.output._write_clipboard_dedup_fingerprint_state",
                side_effect=OSError("state restore failed"),
            ),
            mock.patch("speed_of_cinnamon.output.log_event", side_effect=RuntimeError("logging failed")),
        ):
            output_module._restore_clipboard_dedup_state(("a" * 64, 1.0))

    def test_insert_text_clipboard_paste_fails_closed_when_dedupe_state_cannot_persist(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("100", "Editor", "Xed")),
            mock.patch("speed_of_cinnamon.output._commit_clipboard_insertion", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value="secure text"),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                return_value=(True, "old clipboard"),
            ),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
        ):
            with self.assertRaisesRegex(OutputError, "failed to commit clipboard-paste insertion state"):
                insert_text("secure text", "clipboard-paste")
            self.assertFalse(insert_text("secure text", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["secure text", "old clipboard"])
        self.assertEqual(mocked_paste.call_count, 1)

    def test_insert_text_does_not_restore_dedupe_state_when_paste_set_succeeds_but_commit_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._write_clipboard_dedup_fingerprint_state",
                side_effect=[True, False],
            ),
            mock.patch("speed_of_cinnamon.output.time.time", return_value=21.0),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            with self.assertRaisesRegex(OutputError, "failed to commit clipboard insertion state"):
                insert_text("secure text", "clipboard")
            self.assertFalse(insert_text("secure text", "clipboard"))

        mocked_clipboard.assert_called_once_with("secure text")

    def test_clipboard_dedupe_state_uses_atomic_nofollow_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                with mock.patch(
                    "speed_of_cinnamon.output.write_text_atomically_without_following_symlinks",
                    wraps=output_module.write_text_atomically_without_following_symlinks,
                ) as mocked_write:
                    self.assertTrue(output_module._write_clipboard_dedup_state("secure text", 1.0))

                mocked_write.assert_called_once()
                called_path, called_text = mocked_write.call_args.args[:2]
                self.assertEqual(called_path, output_module.state_dir() / output_module.CLIPBOARD_DEDUP_STATE_FILE)
                self.assertIn(output_module._clipboard_text_fingerprint("secure text"), called_text)
                self.assertEqual(mocked_write.call_args.kwargs["field_name"], "clipboard dedupe state")

    def test_clipboard_dedupe_lock_blocks_parallel_insert(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = _acquire_clipboard_dedup_lock()
                try:
                    self.assertIsNotNone(lock_path)
                    with (
                        mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
                        mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
                    ):
                        with self.assertRaisesRegex(OutputError, "clipboard dedupe lock unavailable"):
                            insert_text("anderer text", "clipboard-paste")
                finally:
                    _release_clipboard_dedup_lock(lock_path)

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    @mock.patch("speed_of_cinnamon.output.os.open", wraps=os.open)
    def test_clipboard_dedupe_lock_uses_secure_directory_fd_open(self, mocked_open: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                acquired = _acquire_clipboard_dedup_lock()
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
                    _release_clipboard_dedup_lock(acquired)

    @mock.patch("speed_of_cinnamon.output.os.unlink", wraps=os.unlink)
    def test_clipboard_dedupe_lock_release_uses_directory_fd_unlink(self, mocked_unlink: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")

                _release_clipboard_dedup_lock(lock_path)

        mocked_unlink.assert_called_once()
        args, kwargs = mocked_unlink.call_args
        self.assertEqual(args[0], lock_path.name)
        self.assertIsInstance(kwargs.get("dir_fd"), int)

    def test_clipboard_dedupe_lock_short_write_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.output.os.write", return_value=0),
            ):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE

                acquired = _acquire_clipboard_dedup_lock()

                self.assertIsNone(acquired)
                self.assertFalse(lock_path.exists())

    def test_clipboard_dedupe_lock_partial_write_cleans_up_own_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            real_write = os.write

            def partial_write_then_fail(fd: int, payload: bytes | bytearray | memoryview) -> int:
                real_write(fd, bytes(payload[:1]))
                raise OSError("write failed after partial payload")

            with (
                mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.output.os.write", side_effect=partial_write_then_fail),
            ):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE

                self.assertIsNone(_acquire_clipboard_dedup_lock())
                self.assertFalse(lock_path.exists())

    def test_clipboard_lock_pid_range_errors_fail_closed(self) -> None:
        for error in (OverflowError("out of range"), ValueError("invalid pid")):
            with self.subTest(error=type(error).__name__), mock.patch(
                "speed_of_cinnamon.output.os.kill", side_effect=error
            ):
                self.assertFalse(output_module._clipboard_lock_pid_is_running(1234))

    def test_clipboard_dedupe_lock_closes_fd_when_creation_stat_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.output.os.fstat", side_effect=OSError("stat failed")),
                mock.patch("speed_of_cinnamon.output.os.close", wraps=os.close) as mocked_close,
            ):
                self.assertIsNone(_acquire_clipboard_dedup_lock())

            self.assertGreaterEqual(mocked_close.call_count, 2)
            self.assertTrue(
                Path(tmp, "speed-of-cinnamon", output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists()
            )

    def test_clipboard_dedupe_lock_fails_closed_when_child_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "speed-of-cinnamon"
            state_root.mkdir()
            parent_fd = os.open(state_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close
            child_close_failed = False
            parent_closed = False

            def close(fd: int) -> None:
                nonlocal child_close_failed, parent_closed
                if fd != parent_fd and not child_close_failed:
                    child_close_failed = True
                    raise OSError("child close failed")
                if fd == parent_fd:
                    parent_closed = True
                real_close(fd)

            try:
                with (
                    mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                    mock.patch.object(
                        output_module,
                        "ensure_directory_without_following_symlinks",
                        return_value=parent_fd,
                    ),
                    mock.patch.object(output_module.os, "close", side_effect=close),
                ):
                    self.assertIsNone(_acquire_clipboard_dedup_lock())
            finally:
                if not parent_closed:
                    real_close(parent_fd)

            self.assertTrue(child_close_failed)
            self.assertFalse((state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists())

    def test_clipboard_dedupe_lock_fails_closed_when_parent_fd_close_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "speed-of-cinnamon"
            state_root.mkdir()
            parent_fd = os.open(state_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close
            parent_close_attempts = 0
            parent_closed = False

            def close(fd: int) -> None:
                nonlocal parent_close_attempts, parent_closed
                if fd == parent_fd:
                    parent_close_attempts += 1
                    if parent_close_attempts == 1:
                        raise OSError("parent close failed")
                    parent_closed = True
                real_close(fd)

            try:
                with (
                    mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                    mock.patch.object(
                        output_module,
                        "ensure_directory_without_following_symlinks",
                        return_value=parent_fd,
                    ),
                    mock.patch.object(output_module.os, "close", side_effect=close),
                ):
                    self.assertIsNone(_acquire_clipboard_dedup_lock())
            finally:
                if not parent_closed:
                    real_close(parent_fd)

            self.assertEqual(parent_close_attempts, 2)
            self.assertFalse((state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists())

    def test_clipboard_dedupe_lock_closes_fd_when_creation_stat_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.output.os.fstat", side_effect=KeyboardInterrupt),
                mock.patch("speed_of_cinnamon.output.os.close", wraps=os.close) as mocked_close,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    _acquire_clipboard_dedup_lock()

            self.assertGreaterEqual(mocked_close.call_count, 2)
            self.assertTrue(
                Path(tmp, "speed-of-cinnamon", output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists()
            )

    def test_clipboard_dedupe_lock_preserves_creation_interrupt_when_parent_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "speed-of-cinnamon"
            state_root.mkdir()
            parent_fd = os.open(state_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close(fd: int) -> None:
                if fd == parent_fd:
                    raise KeyboardInterrupt("parent close interrupted")
                real_close(fd)

            try:
                with (
                    mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                    mock.patch.object(
                        output_module,
                        "ensure_directory_without_following_symlinks",
                        return_value=parent_fd,
                    ),
                    mock.patch.object(
                        output_module.os,
                        "fstat",
                        side_effect=KeyboardInterrupt("stat interrupted"),
                    ),
                    mock.patch.object(output_module.os, "close", side_effect=close),
                ):
                    with self.assertRaisesRegex(KeyboardInterrupt, "stat"):
                        _acquire_clipboard_dedup_lock()
            finally:
                real_close(parent_fd)

            self.assertTrue((state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists())

    def test_clipboard_dedupe_lock_releases_when_parent_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "speed-of-cinnamon"
            state_root.mkdir()
            parent_fd = os.open(state_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close(fd: int) -> None:
                if fd == parent_fd:
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                    mock.patch.object(output_module, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(output_module.os, "close", side_effect=close),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        _acquire_clipboard_dedup_lock()
            finally:
                real_close(parent_fd)

            self.assertFalse((state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists())

    def test_clipboard_dedupe_lock_releases_when_child_close_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "speed-of-cinnamon"
            state_root.mkdir()
            parent_fd = os.open(state_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close
            interrupted = False

            def close(fd: int) -> None:
                nonlocal interrupted
                if fd == parent_fd:
                    return
                if not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt
                real_close(fd)

            try:
                with (
                    mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                    mock.patch.object(output_module, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(output_module.os, "close", side_effect=close),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        _acquire_clipboard_dedup_lock()
            finally:
                real_close(parent_fd)

            self.assertFalse((state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE).exists())

    def test_clipboard_dedupe_lock_cleans_up_when_lock_write_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                mock.patch.object(output_module, "_write_all", side_effect=KeyboardInterrupt),
                mock.patch("speed_of_cinnamon.output.os.close", wraps=os.close) as mocked_close,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    _acquire_clipboard_dedup_lock()

            lock_path = Path(tmp) / "speed-of-cinnamon" / output_module.CLIPBOARD_DEDUP_LOCK_FILE
            self.assertFalse(lock_path.exists())
            self.assertGreaterEqual(mocked_close.call_count, 2)

    def test_clipboard_dedupe_lock_acquire_fsyncs_lock_and_parent(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                mock.patch("speed_of_cinnamon.output.os.fsync", side_effect=record_fsync),
            ):
                lock_path = _acquire_clipboard_dedup_lock()
                try:
                    self.assertIsNotNone(lock_path)
                finally:
                    _release_clipboard_dedup_lock(lock_path)

        self.assertTrue(any(stat.S_ISREG(mode) for mode in fsync_modes))
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsync_modes))

    def test_clipboard_dedupe_lock_release_fsyncs_parent_directory(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = _acquire_clipboard_dedup_lock()
                self.assertIsNotNone(lock_path)
                with mock.patch("speed_of_cinnamon.output.os.fsync", side_effect=record_fsync):
                    _release_clipboard_dedup_lock(lock_path)

                self.assertFalse(lock_path.exists())

        self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsync_modes))

    def test_clipboard_dedupe_lock_release_does_not_delete_replaced_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = _acquire_clipboard_dedup_lock()
                self.assertIsNotNone(lock_path)
                assert lock_path is not None
                lock_path.unlink()
                lock_path.write_text("999999999\nforeign-identity\n", encoding="utf-8")

                _release_clipboard_dedup_lock(lock_path)

                self.assertEqual(lock_path.read_text(encoding="utf-8"), "999999999\nforeign-identity\n")

    def test_clipboard_dedupe_lock_release_does_not_delete_foreign_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\nforeign-identity\n", encoding="utf-8")

                _release_clipboard_dedup_lock(lock_path)

                self.assertEqual(lock_path.read_text(encoding="utf-8"), f"{os.getpid()}\nforeign-identity\n")

    def test_clipboard_dedupe_lock_release_ignores_parent_setup_interrupt(self) -> None:
        with mock.patch.object(
            output_module,
            "ensure_directory_without_following_symlinks",
            side_effect=KeyboardInterrupt("lock release interrupted"),
        ):
            _release_clipboard_dedup_lock(Path("/tmp/clipboard.lock"))

    def test_clipboard_state_unlink_ignores_parent_setup_failure(self) -> None:
        with mock.patch.object(
            output_module,
            "ensure_directory_without_following_symlinks",
            side_effect=RuntimeError("state directory changed"),
        ):
            self.assertFalse(output_module._unlink_clipboard_state_file(Path("/tmp/clipboard-state")))

    def test_insert_text_preserves_paste_error_when_lock_release_is_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "speed-of-cinnamon"
            state_root.mkdir()
            lock_path = state_root / output_module.CLIPBOARD_DEDUP_LOCK_FILE
            lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
            parent_fd = os.open(state_root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_close = os.close

            def close(fd: int) -> None:
                if fd == parent_fd:
                    raise KeyboardInterrupt("lock release interrupted")
                real_close(fd)

            try:
                with (
                    mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
                    mock.patch(
                        "speed_of_cinnamon.output._begin_clipboard_insertion",
                        return_value=(lock_path, ("", 0.0), False),
                    ),
                    mock.patch(
                        "speed_of_cinnamon.output._reserve_clipboard_insertion_memory",
                        return_value=("", None, 0.0, None),
                    ),
                    mock.patch("speed_of_cinnamon.output.set_clipboard", side_effect=OutputError("paste failed")),
                    mock.patch("speed_of_cinnamon.output._restore_clipboard_insertion_snapshot"),
                    mock.patch("speed_of_cinnamon.output._restore_clipboard_dedup_state"),
                    mock.patch.object(output_module, "ensure_directory_without_following_symlinks", return_value=parent_fd),
                    mock.patch.object(output_module.os, "close", side_effect=close),
                ):
                    with self.assertRaisesRegex(OutputError, "paste failed"):
                        insert_text("secure text", "clipboard")
            finally:
                real_close(parent_fd)

    def test_clear_clipboard_dedup_state_uses_dir_fd_unlink_and_fsync(self) -> None:
        fsync_modes: list[int] = []
        real_fsync = os.fsync

        def record_fsync(fd: int) -> None:
            fsync_modes.append(os.fstat(fd).st_mode)
            real_fsync(fd)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                state_path = output_module._clipboard_dedup_state_path()
                self.assertTrue(output_module._write_clipboard_dedup_state("secret text", 123.0))
                with (
                    mock.patch("speed_of_cinnamon.output.os.unlink", wraps=os.unlink) as mocked_unlink,
                    mock.patch("speed_of_cinnamon.output.os.fsync", side_effect=record_fsync),
                ):
                    output_module._clear_clipboard_dedup_state()

                self.assertFalse(state_path.exists())

        mocked_unlink.assert_called_once()
        args, kwargs = mocked_unlink.call_args
        self.assertEqual(args[0], state_path.name)
        self.assertIsInstance(kwargs.get("dir_fd"), int)
        self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsync_modes))

    def test_clear_clipboard_dedup_state_does_not_delete_replaced_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                state_path = output_module._clipboard_dedup_state_path()
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text("old state\n", encoding="utf-8")
                replacement = state_path.with_name("replacement-state.json")
                replacement.write_text("replacement state\n", encoding="utf-8")
                real_stat = output_module.os.stat
                calls = 0

                def stat_then_replace(path: object, *args: object, **kwargs: object) -> os.stat_result:
                    nonlocal calls
                    result = real_stat(path, *args, **kwargs)
                    if isinstance(path, str) and path == state_path.name:
                        calls += 1
                        if calls == 1:
                            state_path.unlink()
                            replacement.replace(state_path)
                    return result

                with mock.patch.object(output_module.os, "stat", side_effect=stat_then_replace):
                    self.assertFalse(output_module._unlink_clipboard_state_file(state_path))

                self.assertEqual(state_path.read_text(encoding="utf-8"), "replacement state\n")

    def test_clipboard_dedupe_lock_rejects_hardlinked_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                backing = Path(tmp) / "foreign-lock"
                backing.write_text("999999999\n", encoding="utf-8")
                os.link(backing, lock_path)
                old = output_module.time.time() - output_module.MAX_DUPLICATE_LOCK_SECONDS - 10
                os.utime(lock_path, (old, old))

                self.assertIsNone(_acquire_clipboard_dedup_lock())
                self.assertTrue(lock_path.exists())
                self.assertTrue(backing.exists())

    def test_clipboard_dedupe_lock_does_not_reclaim_recent_pid_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
                old = output_module.time.time() - (output_module.MAX_DUPLICATE_LOCK_SECONDS - 1)
                os.utime(lock_path, (old, old))

                self.assertIsNone(_acquire_clipboard_dedup_lock())
                self.assertTrue(lock_path.exists())

    def test_clipboard_dedupe_lock_does_not_reclaim_live_owner_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text("12345\nowner-identity\n", encoding="utf-8")

                def fake_identity(pid: int) -> str | None:
                    return "owner-identity" if pid == 12345 else "self-identity"

                with (
                    mock.patch("speed_of_cinnamon.output._clipboard_lock_pid_is_running", return_value=True),
                    mock.patch("speed_of_cinnamon.output._clipboard_lock_identity_for_pid", side_effect=fake_identity),
                ):
                    self.assertIsNone(_acquire_clipboard_dedup_lock())

                self.assertEqual(lock_path.read_text(encoding="utf-8"), "12345\nowner-identity\n")

    def test_clipboard_dedupe_lock_reclaims_zombie_owner(self) -> None:
        process = subprocess.Popen(["true"])
        try:
            stat_path = Path(f"/proc/{process.pid}/stat")
            deadline = output_module.time.monotonic() + 2
            process_state = ""
            while output_module.time.monotonic() < deadline:
                try:
                    raw = stat_path.read_text(encoding="ascii")
                    process_state = raw.rsplit(")", 1)[1].split()[0]
                except OSError:
                    break
                if process_state == "Z":
                    break
                output_module.time.sleep(0.01)
            self.assertEqual(process_state, "Z")

            with tempfile.TemporaryDirectory() as tmp:
                with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                    lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                    lock_path.parent.mkdir(parents=True, exist_ok=True)
                    identity = output_module._clipboard_lock_identity_for_pid(process.pid)
                    self.assertIsNotNone(identity)
                    lock_path.write_text(f"{process.pid}\n{identity}\n", encoding="ascii")
                    lock_path.chmod(0o600)

                    acquired = _acquire_clipboard_dedup_lock()
                    try:
                        self.assertEqual(acquired, lock_path)
                    finally:
                        _release_clipboard_dedup_lock(acquired)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()

    def test_clipboard_dedupe_lock_reclaims_stale_pid_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text("999999999\n", encoding="utf-8")
                old = output_module.time.time() - output_module.MAX_DUPLICATE_LOCK_SECONDS - 10
                os.utime(lock_path, (old, old))

                with mock.patch("speed_of_cinnamon.output._clipboard_lock_pid_is_running", return_value=False):
                    acquired = _acquire_clipboard_dedup_lock()
                try:
                    self.assertEqual(acquired, lock_path)
                    self.assertIn(str(os.getpid()), lock_path.read_text(encoding="utf-8").splitlines()[0])
                finally:
                    _release_clipboard_dedup_lock(acquired)

    def test_clipboard_dedupe_lock_does_not_reclaim_stale_live_pid_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
                old = output_module.time.time() - output_module.MAX_DUPLICATE_LOCK_SECONDS - 10
                os.utime(lock_path, (old, old))

                self.assertIsNone(_acquire_clipboard_dedup_lock())
                self.assertTrue(lock_path.exists())

    def test_clipboard_dedupe_lock_reclaims_pid_with_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\nnot-current-identity\n", encoding="utf-8")
                old = output_module.time.time() - output_module.MAX_DUPLICATE_LOCK_SECONDS - 10
                os.utime(lock_path, (old, old))

                acquired = _acquire_clipboard_dedup_lock()
                try:
                    self.assertEqual(acquired, lock_path)
                    self.assertIn(str(os.getpid()), lock_path.read_text(encoding="utf-8").splitlines()[0])
                finally:
                    _release_clipboard_dedup_lock(acquired)

    def test_clipboard_dedupe_lock_does_not_reclaim_recent_live_pid_with_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\nnot-current-identity\n", encoding="utf-8")

                self.assertIsNone(_acquire_clipboard_dedup_lock())
                self.assertEqual(lock_path.read_text(encoding="utf-8"), f"{os.getpid()}\nnot-current-identity\n")

    def test_clipboard_dedupe_lock_does_not_delete_replaced_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text("12345\n", encoding="utf-8")

                def replace_lock(_parent_fd: int, _name: str) -> int:
                    lock_path.unlink()
                    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
                    return 12345

                with (
                    mock.patch("speed_of_cinnamon.output._read_clipboard_dedup_lock_pid_at", side_effect=replace_lock),
                    mock.patch("speed_of_cinnamon.output._clipboard_lock_pid_is_running", return_value=False),
                ):
                    self.assertIsNone(_acquire_clipboard_dedup_lock())

                self.assertEqual(lock_path.read_text(encoding="utf-8").strip(), str(os.getpid()))

    def test_clipboard_dedupe_lock_write_failure_does_not_delete_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE

                def replace_then_fail(_fd: int, _payload: bytes) -> int:
                    lock_path.unlink()
                    lock_path.write_text("999999999\nforeign-identity\n", encoding="utf-8")
                    raise OSError("write failed")

                with mock.patch("speed_of_cinnamon.output.os.write", side_effect=replace_then_fail):
                    self.assertIsNone(_acquire_clipboard_dedup_lock())

                self.assertEqual(lock_path.read_text(encoding="utf-8"), "999999999\nforeign-identity\n")

    def test_type_text_with_invalid_delay_clamps_to_zero(self) -> None:
        calls: list[list[str]] = []
        typed_payloads: list[str] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            input_payload = kwargs.get("input", b"")
            if args and isinstance(args[0], list):
                called = list(args[0])
            else:
                raw_args = kwargs.get("args", [])
                assert isinstance(raw_args, list)
                called = list(raw_args)
            calls.append(called)
            if "--file" in called:
                self.assertEqual(called[called.index("--file") + 1], "/dev/stdin")
                assert isinstance(input_payload, bytes)
                typed_payloads.append(input_payload.decode("utf-8"))
            return subprocess.CompletedProcess(["xdotool"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.Popen", side_effect=_popen_from_run(fake_run)),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=True),
        ):
            self.assertTrue(insert_text("hello", "type", delay_ms=-10))

        type_calls = [call for call in calls if call[:4] == ["xdotool", "type", "--clearmodifiers", "--delay"]]
        self.assertEqual(len(type_calls), 1)
        self.assertEqual(type_calls[0][4], "0")
        self.assertIn("--file", type_calls[0])
        self.assertNotIn("hello", type_calls[0])
        self.assertEqual(typed_payloads, ["hello"])

    def test_insert_text_restores_dedupe_state_when_paste_was_not_attempted(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=[PasteNotAttemptedError("active window changed"), None],
            ) as mocked_paste,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=4.0),
        ):
            with self.assertRaisesRegex(OutputError, "active window changed"):
                insert_text("wiederholung", "clipboard-paste")
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["wiederholung", "", "wiederholung"])
        self.assertEqual(mocked_paste.call_count, 2)

    def test_clipboard_paste_restore_after_failed_paste_does_not_fallback_to_wl_copy(self) -> None:
        calls: list[str] = []

        def fake_which(command: str) -> str | None:
            return {
                "xclip": "/usr/bin/xclip",
                "wl-copy": "/usr/bin/wl-copy",
            }.get(command)

        def fake_run(command: list[str], *_args: object, **_kwargs: object) -> None:
            calls.append(command[0])
            if command[0] == "xclip":
                raise OutputError("xclip failed")

        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._run_with_input", side_effect=fake_run),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
        ):
            output_module._restore_clipboard_snapshot_after_failed_paste(
                "new text",
                True,
                "old text",
                allowed_helpers=("xclip", "xsel"),
            )

        self.assertEqual(calls, ["xclip"])

    def test_clipboard_restore_ignores_logging_failure(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard", side_effect=OutputError("restore failed")),
            mock.patch("speed_of_cinnamon.output.log_event", side_effect=RuntimeError("logging failed")),
        ):
            output_module._restore_clipboard_snapshot_after_failed_paste("new text", True, "old text")

    def test_insert_text_restores_dedupe_state_when_paste_helper_exec_fails_before_keypress(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=True),
            mock.patch("speed_of_cinnamon.output._paste_key_for_window_snapshot", return_value="ctrl+v"),
            mock.patch(
                "speed_of_cinnamon.output._run_with_input",
                side_effect=[OutputError("xdotool failed to execute: denied"), None],
            ) as mocked_run,
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
            mock.patch("speed_of_cinnamon.output._read_text_clipboard_snapshot", return_value=(True, "")),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=4.0),
        ):
            with self.assertRaisesRegex(OutputError, "failed to execute"):
                insert_text("wiederholung", "clipboard-paste")
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["wiederholung", "", "wiederholung"])
        self.assertEqual(mocked_run.call_count, 2)

    def test_insert_text_type_empty_returns_false(self) -> None:
        with mock.patch("speed_of_cinnamon.output.type_text") as mocked_type:
            self.assertFalse(insert_text("", "type"))
        self.assertFalse(mocked_type.called)

    def test_type_text_rejects_overly_large_delay(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output._active_x_window_snapshot", return_value=("123", "Editor", "xed")),
            mock.patch("speed_of_cinnamon.output._active_x_window_matches_snapshot", return_value=True),
        ):
            with self.assertRaisesRegex(OutputError, "typing delay must be at most"):
                insert_text("hello", "type", delay_ms=MAX_TYPE_DELAY_MS + 10)

    def test_validate_text_input_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(OutputError, "contains invalid null byte"):
            _run_with_input(["echo"], "ok\\x00")

    def test_validate_text_input_rejects_non_text_input(self) -> None:
        with self.assertRaisesRegex(OutputError, "text must be text"):
            _validate_text_input(123)  # type: ignore[arg-type]

    def test_read_file_head_rejects_invalid_file(self) -> None:
        with self.assertRaisesRegex(OutputError, "file must be a binary file handle"):
            _read_file_head(object(), 10)  # type: ignore[arg-type]

    def test_read_file_head_rejects_invalid_max_chars(self) -> None:
        with tempfile.TemporaryFile() as handle:
            with self.assertRaisesRegex(OutputError, "max_chars must be an integer"):
                _read_file_head(handle, "10")  # type: ignore[arg-type]

    def test_filesize_rejects_invalid_file(self) -> None:
        with self.assertRaisesRegex(OutputError, "file must be a binary file handle"):
            _filesize(object())  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
