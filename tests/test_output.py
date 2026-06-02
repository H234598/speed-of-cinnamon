from __future__ import annotations

import subprocess
import unittest
import tempfile
from typing import BinaryIO, cast
from unittest import mock

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
    _looks_like_terminal,
    _run_with_input,
    insert_text,
    paste_from_clipboard,
    set_clipboard,
    type_text,
)


class OutputTest(unittest.TestCase):
    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(OutputError, "value must be text"):
            _contains_escaped_null(12)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(OutputError, "value must be text"):
            _contains_escaped_null(True)  # type: ignore[arg-type]

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
