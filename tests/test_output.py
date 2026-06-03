from __future__ import annotations

import json
import os
import subprocess
import unittest
import tempfile
from pathlib import Path
from typing import BinaryIO, cast
from unittest import mock

from speed_of_cinnamon import output as output_module
from speed_of_cinnamon.output import (
    OutputError,
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


class OutputTest(unittest.TestCase):
    def setUp(self) -> None:
        output_module._LAST_CLIPBOARD_TEXT = ""
        output_module._LAST_CLIPBOARD_METHOD = None
        output_module._LAST_CLIPBOARD_INSERTION = 0.0

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

    def test_clipboard_dedup_state_migrates_legacy_plaintext_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
            path.write_text(json.dumps({"text": "legacy secret", "at": 42.0}), encoding="utf-8")
            with mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root):
                trusted, snapshot = output_module._read_trusted_clipboard_dedup_state()
                content = path.read_text(encoding="utf-8")

        expected = output_module._clipboard_text_fingerprint("legacy secret")
        self.assertTrue(trusted)
        self.assertEqual(snapshot, (expected, 42.0))
        self.assertNotIn("legacy secret", content)
        self.assertEqual(json.loads(content), {"sha256": expected, "at": 42.0})

    def test_clipboard_dedup_state_rejects_invalid_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            path = state_root / output_module.CLIPBOARD_DEDUP_STATE_FILE
            path.write_text(json.dumps({"sha256": "not-a-valid-fingerprint", "at": 42.0}), encoding="utf-8")
            with mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root):
                trusted, snapshot = output_module._read_trusted_clipboard_dedup_state()

        self.assertFalse(trusted)
        self.assertEqual(snapshot, ("", 0.0))

    def test_clipboard_dedup_state_fails_closed_when_tempfile_creation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp)
            with (
                mock.patch("speed_of_cinnamon.output.state_dir", return_value=state_root),
                mock.patch("speed_of_cinnamon.output.tempfile.mkstemp", side_effect=OSError("full")),
            ):
                written = output_module._write_clipboard_dedup_state("secret text", 123.0)

        self.assertFalse(written)

    def test_set_clipboard_prefers_xclip(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.run") as mocked_run,
        ):
            mocked_which.side_effect = lambda command, path=None: "found" if command == "xclip" else None
            mocked_run.return_value = subprocess.CompletedProcess(["xclip"], 0)

            method = set_clipboard("hello")

            self.assertEqual(method, "xclip")
            mocked_run.assert_called_once()

    def test_set_clipboard_uses_resolved_xclip_path(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xclip"),
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertEqual(set_clipboard("hello"), "xclip")

        self.assertEqual(calls[0][0], "/usr/bin/xclip")

    def test_set_clipboard_falls_back_to_xsel(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.run") as mocked_run,
        ):
            mocked_which.side_effect = lambda command, path=None: {
                "xclip": None,
                "xsel": "found",
                "wl-copy": None,
            }.get(command)
            mocked_run.return_value = subprocess.CompletedProcess(["xsel"], 0)

            method = set_clipboard("hello")

            self.assertEqual(method, "xsel")
            mocked_run.assert_called_once()

    def test_set_clipboard_errors_without_helper(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value=None):
            with self.assertRaisesRegex(OutputError, "no clipboard helper found"):
                set_clipboard("hello")

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

    def test_run_with_input_accepts_tuple_argv(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/echo"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.run",
                return_value=subprocess.CompletedProcess(["echo"], 0, stdout=b"", stderr=b""),
            ),
        ):
            _run_with_input(("echo", "x"), "in")

    def test_run_with_input_rejects_command_error_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stderr = cast(BinaryIO, kwargs["stderr"])
            stderr.write(b"boom")
            return subprocess.CompletedProcess(["cmd"], 1, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(OutputError, "failed: boom"):
                _run_with_input(["cmd"], "input")

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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            _run_with_input(["echo"], "hello")

        self.assertNotIn("HOME", captured_env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", captured_env)
        self.assertIn("PATH", captured_env)

    def test_run_with_input_rejects_missing_command_when_resolved_path_missing(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/missing"):
            with mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=FileNotFoundError("missing")):
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
        with mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=1)):
            with self.assertRaisesRegex(OutputError, "timed out"):
                _run_with_input(["sleep"], "", timeout=1)

    def test_run_with_input_limits_output_size(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout = cast(BinaryIO, kwargs["stdout"])
            stdout.write(b"x" * 200)
            return subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")

        with mock.patch("speed_of_cinnamon.output.MAX_OUTPUT_CHARS", 100):
            with (
                mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/cmd"),
                mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
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
            _run_stdout(["xdotool", "bad\\rarg"], timeout=1)

    def test_run_stdout_resolves_command_from_which(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            unittest.mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            _run_stdout(["xdotool", "-h"])

        self.assertEqual(calls[0][0], "/usr/bin/xdotool")

    def test_run_stdout_accepts_tuple_argv(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.run",
                return_value=subprocess.CompletedProcess(["xdotool"], 0, stdout=b"x", stderr=b""),
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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_rejects_oversized_stderr(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs["args"]
            assert isinstance(command, list)
            return subprocess.CompletedProcess(command, 0, stdout=b"ok", stderr=b"x" * (MAX_OUTPUT_CHARS + 1))

        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_rejects_invalid_utf8_output(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.run",
                return_value=subprocess.CompletedProcess(["xdotool"], 0, stdout=b"ok\xff", stderr=b""),
            ),
        ):
            self.assertEqual(_run_stdout(["xdotool", "--help"]), "")

    def test_run_stdout_rejects_output_with_null_byte(self) -> None:
        with (
            unittest.mock.patch("speed_of_cinnamon.output.shutil.which", return_value="/usr/bin/xdotool"),
            mock.patch(
                "speed_of_cinnamon.output.subprocess.run",
                return_value=subprocess.CompletedProcess(["xdotool"], 0, stdout=b"abc\x00def", stderr=b""),
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
            with self.assertRaisesRegex(OutputError, "no keyboard helper"):
                paste_from_clipboard()

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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            paste_from_clipboard()

        self.assertEqual(which_calls.count("xdotool"), 1)
        self.assertEqual(which_calls.count("wtype"), 0)

    def test_paste_from_clipboard_uses_shift_paste_for_wtype(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            return "/usr/bin/wtype" if command == "wtype" else None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._run_with_input") as mocked_run,
        ):
            paste_from_clipboard()

        mocked_run.assert_called_once_with(
            ["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"],
            "",
            timeout=10,
            resolved_command="/usr/bin/wtype",
        )

    def test_paste_from_clipboard_falls_back_to_wtype_on_xdotool_output_error(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            if command == "xdotool":
                return "/usr/bin/xdotool"
            if command == "wtype":
                return "/usr/bin/wtype"
            return None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._active_window_paste_key", return_value="ctrl+shift+v"),
            mock.patch(
                "speed_of_cinnamon.output._run_with_input",
                side_effect=[OutputError("xdotool failed"), None],
            ) as mocked_run,
            mock.patch("speed_of_cinnamon.output.log_event") as mocked_log,
        ):
            paste_from_clipboard()

        mocked_log.assert_called_once_with(
            "warning",
            "clipboard_paste_xdotool_failed_falling_back_to_wtype",
            error="xdotool failed",
        )
        mocked_run.assert_has_calls(
            [
                mock.call(
                    ["xdotool", "key", "--clearmodifiers", "ctrl+shift+v"],
                    "",
                    timeout=10,
                    resolved_command="/usr/bin/xdotool",
                ),
                mock.call(
                    ["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"],
                    "",
                    timeout=10,
                    resolved_command="/usr/bin/wtype",
                ),
            ],
            any_order=False,
        )

    def test_paste_from_clipboard_reports_xdotool_error_without_wtype_fallback(self) -> None:
        def fake_which(command: str, path: str | None = None) -> str | None:
            del path
            return "/usr/bin/xdotool" if command == "xdotool" else None

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", side_effect=fake_which),
            mock.patch("speed_of_cinnamon.output._active_window_paste_key", return_value="ctrl+v"),
            mock.patch("speed_of_cinnamon.output._run_with_input", side_effect=OutputError("xdotool failed")),
        ):
            with self.assertRaisesRegex(OutputError, "xdotool failed"):
                paste_from_clipboard()

    def test_active_window_paste_key_uses_shift_for_terminal_class(self) -> None:
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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertEqual(_active_window_paste_key(), "ctrl+shift+v")

    def test_active_window_paste_key_falls_back_to_normal_paste(self) -> None:
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
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertEqual(_active_window_paste_key(), "ctrl+v")

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

    def test_insert_text_rejects_non_text_method(self) -> None:
        with self.assertRaisesRegex(OutputError, "method must be text"):
            insert_text("hello", 1)  # type: ignore[arg-type]

    def test_insert_text_avoids_duplicate_clipboard_insertion(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, ""), (True, "wiederholung"), (True, "")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard"),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=1.0),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))
            self.assertFalse(insert_text("wiederholung", "clipboard-paste"))

    def test_insert_text_allows_empty_clipboard_text(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            self.assertTrue(insert_text("", "clipboard"))

        mocked_clipboard.assert_called_once_with("")

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
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, ""), (True, "wiederholung"), (True, "")],
            ),
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
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=3.0),
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard"))
            self.assertFalse(insert_text("wiederholung", "clipboard-paste"))

    def test_insert_text_reserves_duplicate_state_before_paste(self) -> None:
        calls: list[str] = []

        def fake_paste() -> None:
            calls.append("paste")
            self.assertFalse(insert_text("wiederholung", "clipboard-paste"))

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, ""), (True, "wiederholung"), (True, "")],
            ),
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
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, ""), (True, "wiederholung"), (True, "")],
            ),
            mock.patch("speed_of_cinnamon.output.set_clipboard"),
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard"),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output._read_trusted_clipboard_dedup_state",
                wraps=output_module._read_trusted_clipboard_dedup_state,
            ) as mocked_read,
        ):
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual(mocked_read.call_count, 1)

    def test_insert_text_rolls_back_duplicate_state_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=[OutputError("paste failed"), None],
            ) as mocked_paste,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value=None),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, ""), (True, "wiederholung"), (True, "")],
            ),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=4.0),
        ):
            with self.assertRaisesRegex(OutputError, "paste failed"):
                insert_text("wiederholung", "clipboard-paste")
            self.assertTrue(insert_text("wiederholung", "clipboard-paste"))

        self.assertEqual(mocked_clipboard.call_count, 3)
        self.assertEqual(mocked_paste.call_count, 2)

    def test_insert_text_does_not_commit_dedupe_state_when_paste_fails(self) -> None:
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
                mock.patch(
                    "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                    side_effect=[(True, "previous text"), (True, "wiederholung")],
                ),
                mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            ):
                with self.assertRaisesRegex(OutputError, "paste failed"):
                    insert_text("wiederholung", "clipboard-paste")

            self.assertEqual(output_module._read_clipboard_dedup_state(), initial_state)

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
            self.assertEqual(
                output_module._read_clipboard_dedup_state(),
                (output_module._clipboard_text_fingerprint("wiederholung"), 9.5),
            )
            self.assertEqual(mocked_clipboard.call_count, 1)
            self.assertEqual(mocked_paste.call_count, 1)

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

    def test_insert_text_restores_previous_text_clipboard_when_paste_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, "previous text"), (True, "new text")],
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

    def test_insert_text_restores_text_clipboard_snapshot_without_stripping(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, " previous text \n"), (True, "new text")],
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
            mock.patch("speed_of_cinnamon.output.subprocess.run", return_value=proc),
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
                side_effect=[(True, ""), (True, "new text")],
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

    def test_insert_text_reports_clipboard_rollback_failure(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, "previous"), (True, "new text")],
            ),
            mock.patch(
                "speed_of_cinnamon.output.set_clipboard",
                side_effect=[None, OutputError("restore failed")],
            ) as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
            mock.patch(
                "speed_of_cinnamon.output.paste_from_clipboard",
                side_effect=OutputError("paste failed"),
            ),
        ):
            with self.assertRaisesRegex(OutputError, "failed to restore previous clipboard"):
                insert_text("new text", "clipboard-paste")
            self.assertEqual(output_module._read_clipboard_dedup_state(), ("", 0.0))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["new text", "previous"])
        self.assertEqual(output_module._LAST_CLIPBOARD_TEXT, "")
        self.assertIsNone(output_module._LAST_CLIPBOARD_METHOD)

    def test_insert_text_does_not_restore_stale_clipboard_after_paste_failure(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                side_effect=[(True, "previous"), (True, "external change")],
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

    def test_insert_text_restores_memory_when_dedupe_state_cannot_persist_after_paste(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
            self.assertTrue(output_module._write_clipboard_dedup_state("previous state", 1.0))
            initial_state = output_module._read_clipboard_dedup_state()
            with (
                mock.patch("speed_of_cinnamon.output._write_clipboard_dedup_state", return_value=False),
                mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
                mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
                mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value="previous text"),
                mock.patch(
                    "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                    return_value=(True, "previous text"),
                ),
                mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
                mock.patch("speed_of_cinnamon.output.time.monotonic", return_value=17.0),
                mock.patch("speed_of_cinnamon.output.time.time", return_value=17.0),
            ):
                with self.assertRaisesRegex(OutputError, "failed to commit clipboard-paste insertion state"):
                    insert_text("wiederholung", "clipboard-paste")
                with self.assertRaisesRegex(OutputError, "failed to commit clipboard-paste insertion state"):
                    insert_text("wiederholung", "clipboard-paste")
            final_state = output_module._read_clipboard_dedup_state()

        self.assertEqual(mocked_clipboard.call_count, 2)
        self.assertEqual(mocked_paste.call_count, 2)
        self.assertEqual(final_state, initial_state)
        self.assertEqual(output_module._LAST_CLIPBOARD_TEXT, "")
        self.assertIsNone(output_module._LAST_CLIPBOARD_METHOD)

    def test_clipboard_targets_treat_rich_text_as_non_text_payload(self) -> None:
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload(""))
        self.assertTrue(output_module._clipboard_targets_contain_non_text_payload("TARGETS\nTIMESTAMP\n"))
        self.assertFalse(output_module._clipboard_targets_contain_non_text_payload("text/html\ntext/plain\n"))
        self.assertFalse(output_module._clipboard_targets_contain_non_text_payload("text/plain;charset=UTF-16\n"))
        self.assertFalse(output_module._clipboard_targets_contain_non_text_payload("text/rtf\n"))
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
            mock.patch("speed_of_cinnamon.output._run_stdout", side_effect=fake_run_stdout),
        ):
            self.assertTrue(output_module._clipboard_has_non_text_payload())

        self.assertEqual(calls, [["xsel", "--clipboard", "--output", "--target", "TARGETS"]])

    def test_clipboard_non_text_detection_fails_closed_on_empty_targets(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output._which", side_effect=lambda command: "/usr/bin/xclip" if command == "xclip" else None),
            mock.patch("speed_of_cinnamon.output._run_stdout", return_value=""),
        ):
            self.assertTrue(output_module._clipboard_has_non_text_payload())

    def test_clipboard_non_text_detection_fails_closed_without_target_helpers(self) -> None:
        with mock.patch("speed_of_cinnamon.output._which", return_value=None):
            self.assertTrue(output_module._clipboard_has_non_text_payload())

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
            self.assertFalse(insert_text("secure text", "clipboard-paste"))

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

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
            self.assertFalse(insert_text("secure text", "clipboard-paste"))

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

    def test_insert_text_clipboard_fails_closed_when_dedupe_state_cannot_persist(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._write_clipboard_dedup_state", return_value=False),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            with self.assertRaisesRegex(OutputError, "failed to commit clipboard insertion state"):
                insert_text("secure text", "clipboard")

        mocked_clipboard.assert_called_once_with("secure text")

    def test_insert_text_clipboard_paste_fails_closed_when_dedupe_state_cannot_persist(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._write_clipboard_dedup_state", side_effect=[False, True]),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
            mock.patch("speed_of_cinnamon.output.paste_from_clipboard") as mocked_paste,
            mock.patch("speed_of_cinnamon.output._read_text_clipboard", return_value="secure text"),
            mock.patch(
                "speed_of_cinnamon.output._read_text_clipboard_snapshot",
                return_value=(True, "old clipboard"),
            ),
            mock.patch("speed_of_cinnamon.output._clipboard_still_contains_inserted_text", return_value=True),
            mock.patch("speed_of_cinnamon.output._clipboard_has_non_text_payload", return_value=False),
        ):
            with self.assertRaisesRegex(OutputError, "failed to commit clipboard-paste insertion state"):
                insert_text("secure text", "clipboard-paste")
            self.assertTrue(insert_text("secure text", "clipboard-paste"))

        self.assertEqual([call.args[0] for call in mocked_clipboard.call_args_list], ["secure text", "old clipboard", "secure text"])
        self.assertEqual(mocked_paste.call_count, 2)

    def test_insert_text_does_not_restore_dedupe_state_when_paste_set_succeeds_but_commit_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}),
            mock.patch("speed_of_cinnamon.output._write_clipboard_dedup_state", return_value=False),
            mock.patch("speed_of_cinnamon.output.time.time", return_value=21.0),
            mock.patch("speed_of_cinnamon.output.set_clipboard") as mocked_clipboard,
        ):
            with self.assertRaisesRegex(OutputError, "failed to commit clipboard insertion state"):
                insert_text("secure text", "clipboard")
            self.assertFalse(insert_text("secure text", "clipboard"))

        mocked_clipboard.assert_called_once_with("secure text")

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
                        self.assertFalse(insert_text("anderer text", "clipboard-paste"))
                finally:
                    _release_clipboard_dedup_lock(lock_path)

        mocked_clipboard.assert_not_called()
        mocked_paste.assert_not_called()

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

    def test_clipboard_dedupe_lock_reclaims_stale_pid_only_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
                old = output_module.time.time() - output_module.MAX_DUPLICATE_LOCK_SECONDS - 10
                os.utime(lock_path, (old, old))

                acquired = _acquire_clipboard_dedup_lock()
                try:
                    self.assertEqual(acquired, lock_path)
                    self.assertIn(str(os.getpid()), lock_path.read_text(encoding="utf-8").splitlines()[0])
                finally:
                    _release_clipboard_dedup_lock(acquired)

    def test_clipboard_dedupe_lock_reclaims_pid_with_identity_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text(f"{os.getpid()}\nnot-current-identity\n", encoding="utf-8")

                acquired = _acquire_clipboard_dedup_lock()
                try:
                    self.assertEqual(acquired, lock_path)
                    self.assertIn(str(os.getpid()), lock_path.read_text(encoding="utf-8").splitlines()[0])
                finally:
                    _release_clipboard_dedup_lock(acquired)

    def test_clipboard_dedupe_lock_does_not_delete_replaced_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"XDG_STATE_HOME": tmp}):
                lock_path = output_module.state_dir() / output_module.CLIPBOARD_DEDUP_LOCK_FILE
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock_path.write_text("12345\n", encoding="utf-8")

                def replace_lock(_path: Path) -> int:
                    lock_path.unlink()
                    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8")
                    return 12345

                with (
                    mock.patch("speed_of_cinnamon.output._read_clipboard_dedup_lock_pid", side_effect=replace_lock),
                    mock.patch("speed_of_cinnamon.output._clipboard_lock_pid_is_running", return_value=False),
                ):
                    self.assertIsNone(_acquire_clipboard_dedup_lock())

                self.assertEqual(lock_path.read_text(encoding="utf-8").strip(), str(os.getpid()))

    def test_type_text_with_invalid_delay_clamps_to_zero(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            if args and isinstance(args[0], list):
                called = list(args[0])
            else:
                raw_args = kwargs.get("args", [])
                assert isinstance(raw_args, list)
                called = list(raw_args)
            calls.append(called)
            return subprocess.CompletedProcess(["xdotool"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertTrue(insert_text("hello", "type", delay_ms=-10))

        self.assertIn(["xdotool", "type", "--clearmodifiers", "--delay", "0", "hello"], calls)

    def test_type_text_rejects_overly_large_delay(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"):
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
