from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from speed_of_cinnamon.command_chain import CommandChainError, run_command_chain, split_command_chain


class CommandChainTest(unittest.TestCase):
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

    def test_split_command_chain_rejects_too_long_command(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command too long"):
            split_command_chain("x " + ("arg " * 8192))

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

        with (
            mock.patch("speed_of_cinnamon.command_chain.command_environment", return_value={"SPEED_OF_CINNAMON_CONTEXT": "test"}),
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

        with mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(CommandChainError, "output exceeded"):
                run_command_chain([("cmd",)], "", label="post-process", max_output_chars=5)

    def test_run_command_chain_reports_command_not_found(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=FileNotFoundError("missing")):
            with self.assertRaisesRegex(CommandChainError, "command not found"):
                run_command_chain([("missing",)], "", label="transcriber")

    def test_run_command_chain_reports_timeout(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=0.01)):
            with self.assertRaisesRegex(CommandChainError, "timed out"):
                run_command_chain([("slow",)], "", label="post-process", timeout_seconds=1)

    def test_run_command_chain_rejects_empty_chain(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command chain is empty"):
            run_command_chain([], "seed", label="post-process")

    def test_run_command_chain_rejects_invalid_limits(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "max_output_chars must be non-negative"):
            run_command_chain([("cmd",)], "", label="post-process", max_output_chars=-1)

        with self.assertRaisesRegex(CommandChainError, "max_input_chars must be non-negative"):
            run_command_chain([("cmd",)], "", label="post-process", max_input_chars=-1)

    def test_run_command_chain_rejects_non_positive_timeout(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "timeout_seconds must be positive"):
            run_command_chain([("cmd",)], "", label="post-process", timeout_seconds=0)

    def test_run_command_chain_rejects_null_bytes_in_input(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid null byte"):
            run_command_chain([("cmd",)], "hello\x00", label="post-process")

    def test_run_command_chain_rejects_null_bytes_in_command_segment(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid null byte"):
            run_command_chain([("cmd\x00",)], "", label="post-process")

    def test_run_command_chain_rejects_too_large_input(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "input exceeded"):
            run_command_chain([("cmd",)], "x" * 1_000_001, label="post-process", max_input_chars=1_000_000)


if __name__ == "__main__":
    unittest.main()
