from __future__ import annotations

import subprocess
import unittest
import tempfile
from unittest import mock

from speed_of_cinnamon.command_chain import (
    CommandChainError,
    MAX_COMMAND_SEGMENT_TOKENS,
    MAX_COMMAND_SEGMENTS,
    MAX_COMMAND_INPUT_CHARS,
    MAX_COMMAND_OUTPUT_CHARS,
    _contains_escaped_null,
    _filesize,
    _read_file_head,
    run_command_chain,
    split_command_chain,
)
from speed_of_cinnamon.personalization import MAX_PERSONAL_CONTEXT_CHARS, MAX_VOCABULARY_CHARS


class CommandChainTest(unittest.TestCase):
    def test_split_command_chain_rejects_non_text_command(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command must be text"):
            split_command_chain(123)

    def test_split_command_chain_rejects_non_text_label_type(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "label must be text"):
            split_command_chain("printf hello", label=True)

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "value must be text"):
            _contains_escaped_null(123)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "value must be text"):
            _contains_escaped_null(True)  # type: ignore[arg-type]

    def test_split_command_chain_rejects_non_text_label(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "label must be text"):
            split_command_chain("printf hello", label=123)

    def test_split_command_chain_supports_and_and_rejects_unsupported_operators(self) -> None:
        self.assertEqual(
            split_command_chain("printf hello && printf world"),
            [["printf", "hello"], ["printf", "world"]],
        )

        with self.assertRaisesRegex(CommandChainError, "unsupported shell operator"):
            split_command_chain("printf hello | printf world")

        with self.assertRaisesRegex(CommandChainError, "unsupported shell operator"):
            split_command_chain("python3 -c \"print(1)\" 2> /tmp/log")

    def test_split_command_chain_rejects_null_bytes(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid command command: contains invalid null byte"):
            split_command_chain("printf hello\x00world")

    def test_split_command_chain_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid command command: contains invalid null byte"):
            split_command_chain("printf hello\\\\x00world")

    def test_split_command_chain_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "contains control characters"):
            split_command_chain("printf hello\nworld")

    def test_split_command_chain_rejects_too_long_command(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command too long"):
            split_command_chain("x " + ("arg " * 8192))

    def test_split_command_chain_rejects_too_long_command_bytes(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command too long"):
            split_command_chain("cmd " + ("😀 " * 2048))

    def test_split_command_chain_rejects_too_many_segments(self) -> None:
        command = " && ".join(["printf a"] * 33)
        with self.assertRaisesRegex(CommandChainError, "too many segments"):
            split_command_chain(command)

    def test_split_command_chain_rejects_too_many_tokens_in_segment(self) -> None:
        command = " ".join(["printf"] + ["a"] * 129)
        with self.assertRaisesRegex(CommandChainError, "segment is too long"):
            split_command_chain(command)

    def test_run_command_chain_executes_segments_with_stdin(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            command = args[0] if args else kwargs.get("args")
            cmd_bytes = kwargs.get("input")
            stdout_file = kwargs["stdout"]
            cmd_text = cmd_bytes.decode("utf-8") if isinstance(cmd_bytes, bytes) else None
            assert isinstance(stdout_file, object)
            assert isinstance(command, list)
            calls.append((command, cmd_text))
            if len(calls) == 1:
                stdout_file.write("segment-1\n".encode("utf-8"))
            else:
                stdout_file.write(f"{cmd_text}\n".encode("utf-8"))
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        def which(command: str) -> str | None:
            return {"first": "first", "second": "second"}.get(command)

        with (
            mock.patch("speed_of_cinnamon.command_chain.command_environment", return_value={"SPEED_OF_CINNAMON_CONTEXT": "test"}),
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", side_effect=which),
            mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=fake_run),
        ):
            output = run_command_chain([
                ("first",),
                ("second",),
            ], "seed", label="chain")

        self.assertEqual(output, "segment-1")
        self.assertEqual(calls[0][0][0], "first")
        self.assertEqual(calls[0][1], "seed")
        self.assertEqual(calls[1][0][0], "second")
        self.assertEqual(calls[1][1], "segment-1")

    def test_run_command_chain_rejects_large_output(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout_file = kwargs["stdout"]
            stdout_file.write(b"x" * 10)
            return subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(CommandChainError, "output exceeded"):
                run_command_chain([("cmd",)], "", label="post-process", max_output_chars=5)

    def test_run_command_chain_rejects_invalid_command_input_utf8(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "input is not valid UTF-8"):
            run_command_chain([("cmd",)], "\udcff", label="post-process")

    def test_run_command_chain_rejects_invalid_command_output_utf8(self) -> None:
        def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            stdout_file = kwargs["stdout"]
            stdout_file.write(b"\xff")
            return subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b"")

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(CommandChainError, "not valid UTF-8"):
                run_command_chain([("cmd",)], "seed", label="post-process")

    def test_run_command_chain_rejects_too_many_segments(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "too many segments"):
            run_command_chain([("cmd",)] * (MAX_COMMAND_SEGMENTS + 1), "", label="post-process")

    def test_run_command_chain_rejects_too_many_tokens_in_segment(self) -> None:
        segment: list[str] = ["cmd"] + ["a"] * (MAX_COMMAND_SEGMENT_TOKENS + 1)
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"):
            with self.assertRaisesRegex(CommandChainError, "segment is too long"):
                run_command_chain([tuple(segment)], "", label="post-process")

    def test_run_command_chain_accepts_max_tokens_per_segment(self) -> None:
        segment: list[str] = ["cmd"] + ["a"] * (MAX_COMMAND_SEGMENT_TOKENS - 2)
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"):
            with mock.patch(
                "speed_of_cinnamon.command_chain.subprocess.run",
                return_value=subprocess.CompletedProcess(["cmd"], 0, stdout=b"", stderr=b""),
            ):
                output = run_command_chain([tuple(segment)], "", label="post-process")
        self.assertEqual(output, "")

    def test_run_command_chain_reports_command_not_found(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="missing"):
            with mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=FileNotFoundError("missing")):
                with self.assertRaisesRegex(CommandChainError, "command not found"):
                    run_command_chain([("missing",)], "", label="transcriber")

    def test_run_command_chain_rejects_missing_command(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value=None):
            with self.assertRaisesRegex(CommandChainError, "is not available"):
                run_command_chain([("missing",)], "", label="transcriber")

    def test_run_command_chain_reports_timeout(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.command_chain.shutil.which",
            return_value="slow",
        ), mock.patch(
            "speed_of_cinnamon.command_chain.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=0.01),
        ):
            with self.assertRaisesRegex(CommandChainError, "timed out"):
                run_command_chain([("slow",)], "", label="post-process", timeout_seconds=1)

    def test_run_command_chain_rejects_empty_chain(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command chain is empty"):
            run_command_chain([], "seed", label="post-process")

    def test_run_command_chain_rejects_invalid_limits(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "max_output_chars must be positive"):
            run_command_chain([("cmd",)], "", label="post-process", max_output_chars=0)
        with self.assertRaisesRegex(CommandChainError, "max_output_chars must not exceed"):
            run_command_chain([("cmd",)], "", label="post-process", max_output_chars=MAX_COMMAND_OUTPUT_CHARS + 1)
        with self.assertRaisesRegex(CommandChainError, "max_output_chars must be positive"):
            run_command_chain([("cmd",)], "", label="post-process", max_output_chars=-1)
        with self.assertRaisesRegex(CommandChainError, "max_input_chars must not exceed"):
            run_command_chain([("cmd",)], "", label="post-process", max_input_chars=MAX_COMMAND_INPUT_CHARS + 1)

        with self.assertRaisesRegex(CommandChainError, "max_input_chars must be non-negative"):
            run_command_chain([("cmd",)], "", label="post-process", max_input_chars=-1)
        with self.assertRaisesRegex(CommandChainError, "personal context is too large"):
            run_command_chain([("cmd",)], "", label="post-process", personal_context="x" * (MAX_PERSONAL_CONTEXT_CHARS + 1))
        with self.assertRaisesRegex(CommandChainError, "vocabulary is too large"):
            run_command_chain([("cmd",)], "", label="post-process", vocabulary="x" * (MAX_VOCABULARY_CHARS + 1))

    def test_run_command_chain_rejects_non_text_values(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "segments must be a sequence"):
            run_command_chain("cmd", "", label="post-process")  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "segments must contain sequences"):
            run_command_chain([("cmd",), 123], "", label="post-process")  # type: ignore[list-item]
        with self.assertRaisesRegex(CommandChainError, "input text must be text"):
            run_command_chain([("cmd",)], 123, label="post-process")  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "personal context must be text"):
            run_command_chain([("cmd",)], "", label="post-process", personal_context=123)  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "vocabulary must be text"):
            run_command_chain([("cmd",)], "", label="post-process", vocabulary=123)  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "label must be text"):
            run_command_chain([("cmd",)], "", label=123)  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "label must be text"):
            run_command_chain([("cmd",)], "", label=True)  # type: ignore[arg-type]

    def test_run_command_chain_rejects_non_int_limits(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "max_output_chars must be an integer"):
            run_command_chain([("cmd",)], "", label="post-process", max_output_chars="1")  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "max_input_chars must be an integer"):
            run_command_chain([("cmd",)], "", label="post-process", max_input_chars=True)  # type: ignore[arg-type]
        with self.assertRaisesRegex(CommandChainError, "timeout_seconds must be an integer"):
            run_command_chain([("cmd",)], "", label="post-process", timeout_seconds=False)  # type: ignore[arg-type]

    def test_run_command_chain_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "timeout_seconds must be positive"):
            run_command_chain([("cmd",)], "", label="post-process", timeout_seconds=0)

    def test_run_command_chain_rejects_null_bytes_in_input(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid null byte"):
            run_command_chain([("cmd",)], "hello\x00", label="post-process")

    def test_run_command_chain_rejects_escaped_null_in_input(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid null byte"):
            run_command_chain([("cmd",)], "hello\\\\x00", label="post-process")

    def test_run_command_chain_rejects_null_bytes_in_command_segment(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid null byte"):
            run_command_chain([("cmd\x00",)], "", label="post-process")

    def test_run_command_chain_rejects_escaped_null_in_command_segment(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid null byte"):
            run_command_chain([("cmd\\\\x00",)], "", label="post-process")

    def test_run_command_chain_rejects_control_chars_in_command_segment(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid control character"):
            run_command_chain([("cmd\nname",)], "", label="post-process")

    def test_run_command_chain_rejects_control_chars_in_command_argument(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid control character"):
            run_command_chain([("cmd", "arg\rvalue")], "", label="post-process")

    def test_run_command_chain_rejects_too_large_input(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "input exceeded"):
            run_command_chain([("cmd",)], "x" * 1_000_001, label="post-process", max_input_chars=1_000_000)

    def test_read_file_head_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok\xff")
            with self.assertRaisesRegex(CommandChainError, "not valid UTF-8"):
                _read_file_head(handle, 10)

    def test_read_file_head_rejects_invalid_file(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "file must be a binary file handle"):
            _read_file_head(object(), 10)

    def test_read_file_head_rejects_invalid_max_chars(self) -> None:
        with tempfile.TemporaryFile() as handle:
            with self.assertRaisesRegex(CommandChainError, "max_chars must be an integer"):
                _read_file_head(handle, "10")  # type: ignore[arg-type]

    def test_filesize_rejects_invalid_file(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "file must be a binary file handle"):
            _filesize(object())  # type: ignore[arg-type]

    def test_read_file_head_rejects_escaped_null(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write("ok\\x00end".encode("utf-8"))
            with self.assertRaisesRegex(CommandChainError, "contains invalid null byte"):
                _read_file_head(handle, 10)


if __name__ == "__main__":
    unittest.main()
