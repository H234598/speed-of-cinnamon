from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from speed_of_cinnamon.output import (
    OutputError,
    MAX_OUTPUT_CHARS,
    _run_with_input,
    insert_text,
    paste_from_clipboard,
    set_clipboard,
    type_text,
)


class OutputTest(unittest.TestCase):
    def test_set_clipboard_prefers_xclip(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.run") as mocked_run,
        ):
            mocked_which.side_effect = lambda command: "found" if command == "xclip" else None
            mocked_run.return_value = subprocess.CompletedProcess(["xclip"], 0)

            method = set_clipboard("hello")

            self.assertEqual(method, "xclip")
            mocked_run.assert_called_once()

    def test_set_clipboard_falls_back_to_xsel(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.output.shutil.which") as mocked_which,
            mock.patch("speed_of_cinnamon.output.subprocess.run") as mocked_run,
        ):
            mocked_which.side_effect = lambda command: {
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

    def test_run_with_input_rejects_command_error_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stderr = kwargs["stderr"]
            stderr.write(b"boom")
            return subprocess.CompletedProcess(["cmd"], 1, stdout=b"", stderr=b"")

        with mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(OutputError, "failed: boom"):
                _run_with_input(["cmd"], "input")

    def test_run_with_input_rejects_oversized_text(self) -> None:
        with self.assertRaisesRegex(OutputError, "command input is too large"):
            _run_with_input(["cmd"], "x" * (1_000_001))

    def test_run_with_input_rejects_negative_output_limit(self) -> None:
        with self.assertRaisesRegex(OutputError, "max_output_chars must be non-negative"):
            _run_with_input(["cmd"], "input", max_output_chars=-1)

    def test_run_with_input_rejects_missing_command(self) -> None:
        with mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=FileNotFoundError("missing")):
            with self.assertRaisesRegex(OutputError, "is not available"):
                _run_with_input(["missing"], "input")

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
            stdout = kwargs["stdout"]
            stdout.write(b"x" * 200)
            return subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")

        with mock.patch("speed_of_cinnamon.output.MAX_OUTPUT_CHARS", 100):
            with mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run):
                with self.assertRaisesRegex(OutputError, "too much output"):
                    _run_with_input(["cmd"], "input")

    def test_paste_without_helper_is_error(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value=None):
            with self.assertRaisesRegex(OutputError, "no keyboard helper"):
                paste_from_clipboard()

    def test_type_text_rejects_null_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"):
            with self.assertRaisesRegex(OutputError, "command input contains invalid null byte"):
                type_text("hello\x00", 8)

    def test_type_text_with_invalid_delay_clamps_to_zero(self) -> None:
        calls: list[list[str]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            called = list(args[0]) if args and isinstance(args[0], list) else list(kwargs.get("args", []))
            calls.append(called)
            return subprocess.CompletedProcess(["xdotool"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.output.shutil.which", return_value="xdotool"),
            mock.patch("speed_of_cinnamon.output.subprocess.run", side_effect=fake_run),
        ):
            self.assertTrue(insert_text("hello", "type", delay_ms=-10))

        self.assertIn(["xdotool", "type", "--clearmodifiers", "--delay", "0", "hello"], calls)


if __name__ == "__main__":
    unittest.main()
