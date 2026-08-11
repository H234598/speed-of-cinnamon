from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
import tempfile
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import command_chain as command_chain_module
from speed_of_cinnamon.command_chain import (
    CommandChainError,
    MAX_COMMAND_LENGTH_CHARS,
    MAX_COMMAND_SEGMENT_TOKENS,
    MAX_COMMAND_SEGMENTS,
    MAX_COMMAND_INPUT_CHARS,
    MAX_COMMAND_OUTPUT_CHARS,
    _command_path,
    _contains_escaped_null,
    _filtered_environment,
    _filesize,
    _read_file_head,
    run_process_bounded_output,
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

    def test_split_command_chain_rejects_control_characters_in_label(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "label contains invalid control character"):
            split_command_chain("printf hello", label="command\nspoof")

    def test_split_command_chain_supports_and_and_rejects_unsupported_operators(self) -> None:
        self.assertEqual(
            split_command_chain("printf hello && printf world"),
            [["printf", "hello"], ["printf", "world"]],
        )

        with self.assertRaisesRegex(CommandChainError, "unsupported shell operator"):
            split_command_chain("printf hello | printf world")
        with self.assertRaisesRegex(CommandChainError, "unsupported shell operator"):
            split_command_chain("printf hello ; printf world")

        with self.assertRaisesRegex(CommandChainError, "unsupported shell operator"):
            split_command_chain("python3 -c \"print(1)\" 2> /tmp/log")

    def test_split_command_chain_preserves_quoted_and_escaped_and_and(self) -> None:
        self.assertEqual(split_command_chain('printf "&&"'), [["printf", "&&"]])
        self.assertEqual(split_command_chain("printf '&&'"), [["printf", "&&"]])
        self.assertEqual(split_command_chain(r"printf \&&"), [["printf", "&&"]])
        self.assertEqual(
            split_command_chain('printf "a && b" && printf c'),
            [["printf", "a && b"], ["printf", "c"]],
        )
        with self.assertRaisesRegex(CommandChainError, "empty command command segment before &&"):
            split_command_chain("printf && && printf c")

    def test_split_command_chain_rejects_null_bytes(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid command command: contains invalid null byte"):
            split_command_chain("printf hello\x00world")

    def test_split_command_chain_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "invalid command command: contains invalid null byte"):
            split_command_chain("printf hello\\\\x00world")

    def test_split_command_chain_rejects_control_characters(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "contains control characters"):
            split_command_chain("printf hello\nworld")

    def test_split_command_chain_allows_newline_inside_quoted_argument(self) -> None:
        self.assertEqual(
            split_command_chain("printf 'hello\nworld'"),
            [["printf", "hello\nworld"]],
        )

    def test_split_command_chain_rejects_escaped_control_characters(self) -> None:
        for command in ("printf hello\\r\\nworld", "printf hello\\x1bworld", "printf hello\\u001bworld", "printf hello\\x85world"):
            with self.subTest(command=command):
                with self.assertRaisesRegex(CommandChainError, "contains control characters"):
                    split_command_chain(command)

    def test_split_command_chain_rejects_other_control_characters(self) -> None:
        for command in ("printf hello\x1bworld", "printf hello\x85world"):
            with self.subTest(command=repr(command)):
                with self.assertRaisesRegex(CommandChainError, "contains control characters"):
                    split_command_chain(command)

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

    def test_split_command_chain_accepts_max_tokens_in_segment(self) -> None:
        command = " ".join(["printf"] + ["a"] * (MAX_COMMAND_SEGMENT_TOKENS - 1))
        self.assertEqual(
            split_command_chain(command),
            [["printf"] + ["a"] * (MAX_COMMAND_SEGMENT_TOKENS - 1)],
        )

    def test_split_command_chain_rejects_max_plus_one_tokens_in_segment(self) -> None:
        command = " ".join(["printf"] + ["a"] * MAX_COMMAND_SEGMENT_TOKENS)
        with self.assertRaisesRegex(CommandChainError, "segment is too long"):
            split_command_chain(command)

    def test_run_command_chain_executes_segments_with_stdin(self) -> None:
        calls: list[tuple[list[str], str | None]] = []

        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del kwargs
            cmd_text = input_bytes.decode("utf-8")
            calls.append((argv, cmd_text))
            if len(calls) == 1:
                return 0, b"segment-1\n", b""
            return 0, f"{cmd_text}\n".encode("utf-8"), b""

        def which(command: str, path: str | None = None) -> str | None:
            return {"first": "first", "second": "second"}.get(command)

        with (
            mock.patch("speed_of_cinnamon.command_chain.command_environment", return_value={"SPEED_OF_CINNAMON_CONTEXT": "test"}),
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", side_effect=which),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
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

    def test_run_command_chain_strips_dangerous_environment_variables(self) -> None:
        captured_env: dict[str, str] = {}

        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes
            env = kwargs.get("env")
            if isinstance(env, dict):
                captured_env.update(env)
            return 0, b"", b""

        with (
            mock.patch(
                "speed_of_cinnamon.command_chain.command_environment",
                return_value={
                    "SPEED_OF_CINNAMON_CONTEXT": "test",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                    "LD_PRELOAD": "malicious-lib.so",
                    "PYTHONPATH": "/tmp/evil",
                },
            ),
            mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": "/run/user/1000"}, clear=False),
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="command"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            run_command_chain([("command",)], "", label="command-chain")

        self.assertNotIn("LD_PRELOAD", captured_env)
        self.assertNotIn("PYTHONPATH", captured_env)
        self.assertNotIn("SPEED_OF_CINNAMON_CONTEXT", captured_env)
        self.assertNotIn("XDG_RUNTIME_DIR", captured_env)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", captured_env)
        self.assertEqual(captured_env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_command_environment_strips_shell_state_variables(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PWD": "/tmp/evil",
                "OLDPWD": "/tmp/older",
                "CDPATH": "/tmp/cd",
                "PS4": "pwn",
                "BASH_XTRACEFD": "9",
                "HOME": "/home/test",
                "LANG": "C.UTF-8",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            },
            clear=True,
        ):
            from speed_of_cinnamon.personalization import command_environment

            env = command_environment("ctx", "vocab")

        self.assertNotIn("PWD", env)
        self.assertNotIn("OLDPWD", env)
        self.assertNotIn("CDPATH", env)
        self.assertNotIn("PS4", env)
        self.assertNotIn("BASH_XTRACEFD", env)
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_module_environment_builders_strip_shell_state_variables(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "PWD": "/tmp/evil",
                "OLDPWD": "/tmp/older",
                "CDPATH": "/tmp/cd",
                "PS4": "pwn",
                "BASH_XTRACEFD": "9",
                "HOME": "/home/test",
                "LANG": "C.UTF-8",
                "XDG_RUNTIME_DIR": "/run/user/1000",
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
            },
            clear=True,
        ):
            from speed_of_cinnamon.cli import _filtered_environment as cli_filtered_environment
            from speed_of_cinnamon.command_chain import _filtered_environment as chain_filtered_environment
            from speed_of_cinnamon.output import _filtered_environment as output_filtered_environment
            from speed_of_cinnamon.personalization import _filtered_environment as personalization_filtered_environment
            from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment
            from speed_of_cinnamon.transcriber import _filtered_environment as transcriber_filtered_environment

            envs = [
                cli_filtered_environment(),
                chain_filtered_environment(),
                output_filtered_environment(),
                personalization_filtered_environment(),
                recorder_filtered_environment(),
                transcriber_filtered_environment(),
            ]

        for env in envs:
            self.assertNotIn("PWD", env)
            self.assertNotIn("OLDPWD", env)
            self.assertNotIn("CDPATH", env)
            self.assertNotIn("PS4", env)
            self.assertNotIn("BASH_XTRACEFD", env)
        self.assertNotIn("XDG_RUNTIME_DIR", envs[1])
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", envs[1])
        self.assertEqual(envs[0]["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(envs[4]["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(envs[5]["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", envs[2])
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", envs[3])
        for env in [envs[0], envs[4], envs[5]]:
            self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")

    def test_command_path_ignores_trusted_path_environment_override(self) -> None:
        captured_path: dict[str, str | None] = {}

        def fake_which(command_name: str, path: str | None = None) -> str | None:
            captured_path["path"] = path
            return f"/usr/bin/{command_name}"

        with (
            mock.patch.dict(os.environ, {"SPEED_OF_CINNAMON_TRUSTED_PATH": "/tmp/evil"}),
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", side_effect=fake_which),
        ):
            resolved = _command_path("printf")

        self.assertEqual(resolved, "/usr/bin/printf")
        self.assertEqual(captured_path["path"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_run_command_chain_rejects_large_output(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                side_effect=CommandChainError("post-process command output exceeded 5 bytes"),
            ),
        ):
            with self.assertRaisesRegex(CommandChainError, "output exceeded"):
                run_command_chain([("cmd",)], "", label="post-process", max_output_chars=5)

    def test_run_command_chain_allows_multibyte_output_within_character_limit(self) -> None:
        captured: dict[str, int] = {}
        output_text = "\U0001f600" * 4
        raw_output_text = f"{output_text}\n"

        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes
            captured["max_output_bytes"] = int(kwargs["max_output_bytes"])
            return 0, raw_output_text.encode("utf-8"), b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            result = run_command_chain([("cmd",)], "", label="post-process", max_output_chars=4)

        self.assertEqual(result, output_text)
        self.assertGreaterEqual(captured["max_output_bytes"], len(raw_output_text.encode("utf-8")))

    def test_run_command_chain_preserves_leading_and_trailing_spaces(self) -> None:
        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes, kwargs
            return 0, b"  spaced \t\r\n", b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            result = run_command_chain([("cmd",)], "", label="post-process")

        self.assertEqual(result, "  spaced \t")

    def test_run_command_chain_rejects_multibyte_output_over_character_limit(self) -> None:
        output_text = "\U0001f600" * 5

        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes, kwargs
            return 0, output_text.encode("utf-8"), b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(CommandChainError, "output exceeded 4 characters"):
                run_command_chain([("cmd",)], "", label="post-process", max_output_chars=4)

    def test_run_command_chain_bounded_runner_rejects_large_live_output(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "output exceeded"):
            run_command_chain(
                [("python3", "-c", "import sys; sys.stdout.write('x' * 10000)")],
                "",
                label="post-process",
                max_output_chars=128,
            )

    def test_split_command_chain_rejects_invalid_utf8_command(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "not valid UTF-8"):
            split_command_chain("printf hello\udcff")

    def test_run_command_chain_rejects_large_stderr_output(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                side_effect=CommandChainError("post-process command output exceeded 5 bytes"),
            ),
        ):
            with self.assertRaisesRegex(CommandChainError, "output exceeded"):
                run_command_chain([("cmd",)], "", label="post-process", max_output_chars=5)

    def test_run_command_chain_redacts_failed_command_output(self) -> None:
        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes, kwargs
            return 2, b"transcript with sk-secret-token\n", b"Bearer private-token\n"

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            with self.assertRaises(CommandChainError) as cm:
                run_command_chain([("cmd",)], "", label="post-process")

        message = str(cm.exception)
        self.assertIn("post-process command failed: exit code 2; command output redacted", message)
        self.assertNotIn("sk-secret-token", message)
        self.assertNotIn("private-token", message)

    def test_run_command_chain_allows_multibyte_input_within_character_limit(self) -> None:
        input_text = "\U0001f600" * 4
        captured: dict[str, bytes] = {}

        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, kwargs
            captured["input_bytes"] = input_bytes
            return 0, b"ok", b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            result = run_command_chain([("cmd",)], input_text, label="post-process", max_input_chars=4)

        self.assertEqual(result, "ok")
        self.assertEqual(captured["input_bytes"], input_text.encode("utf-8"))

    def test_run_command_chain_rejects_multibyte_input_over_character_limit(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output") as mocked_run,
        ):
            with self.assertRaisesRegex(CommandChainError, "input exceeded 4 characters"):
                run_command_chain([("cmd",)], "\U0001f600" * 5, label="post-process", max_input_chars=4)

        mocked_run.assert_not_called()

    def test_run_command_chain_redacts_timed_out_command_argv(self) -> None:
        with self.assertRaises(CommandChainError) as cm:
            run_command_chain(
                [("python3", "-c", "import time; time.sleep(5)", "--api-key", "SECRET_TOKEN")],
                "seed",
                label="post-process",
                timeout_seconds=1,
            )

        message = str(cm.exception)
        self.assertIn("post-process command timed out", message)
        self.assertNotIn("--api-key", message)
        self.assertNotIn("SECRET_TOKEN", message)

    def test_run_process_bounded_output_kills_descendant_that_changes_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "escaped-child.pid"
            code = (
                "import os, sys, time\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    os.setsid()\n"
                "    open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid()))\n"
                "    time.sleep(2)\n"
                "else:\n"
                "    time.sleep(2)\n"
            )
            with self.assertRaisesRegex(CommandChainError, "timed out"):
                run_process_bounded_output(
                    [sys.executable, "-c", code, str(marker)],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text(encoding="ascii"))
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except FileNotFoundError:
                    break
                state = raw[raw.rindex(")") + 2 :].split()[0]
                if state in {"Z", "X", "x"}:
                    break
                time.sleep(0.01)
            else:
                self.fail("session-escaped descendant survived command timeout")

    def test_run_process_bounded_output_starts_new_session(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen", side_effect=FileNotFoundError) as mocked_popen:
            with self.assertRaises(FileNotFoundError):
                run_process_bounded_output(
                    ["/usr/bin/missing"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

        self.assertTrue(mocked_popen.call_args.kwargs["start_new_session"])

    def test_run_process_bounded_output_rejects_dangerous_environment(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen") as mocked_popen:
            with self.assertRaisesRegex(CommandChainError, "environment key is not allowed: LD_PRELOAD"):
                run_process_bounded_output(
                    ["command"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={"LD_PRELOAD": "/tmp/evil.so"},
                    label="post-process",
                )

        mocked_popen.assert_not_called()

    def test_run_process_bounded_output_rejects_unbounded_input_and_output(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen") as mocked_popen:
            with self.assertRaisesRegex(CommandChainError, "input bytes must not exceed"):
                run_process_bounded_output(
                    ["command"],
                    b"x" * (command_chain_module.MAX_BOUNDED_PROCESS_INPUT_BYTES + 1),
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )
            with self.assertRaisesRegex(CommandChainError, "max_output_bytes must not exceed"):
                run_process_bounded_output(
                    ["command"],
                    timeout_seconds=1,
                    max_output_bytes=command_chain_module.MAX_BOUNDED_PROCESS_OUTPUT_BYTES + 1,
                    env={},
                    label="post-process",
                )

        mocked_popen.assert_not_called()

    def test_run_process_bounded_output_rejects_control_characters_in_label(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen") as mocked_popen:
            with self.assertRaisesRegex(CommandChainError, "label contains invalid control character"):
                run_process_bounded_output(
                    ["command"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post\nprocess",
                )

        mocked_popen.assert_not_called()

    def test_run_process_bounded_output_cleans_process_tree_when_identity_is_missing(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        with (
            mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen", return_value=proc),
            mock.patch("speed_of_cinnamon.command_chain._clipboard_lock_identity_for_pid", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain._terminate_bounded_process", return_value=True) as mocked_cleanup,
        ):
            with self.assertRaisesRegex(CommandChainError, "process identity could not be verified"):
                run_process_bounded_output(
                    ["command"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

        mocked_cleanup.assert_called_once_with(proc)

    def test_run_process_bounded_output_terminates_real_process_when_identity_is_missing(self) -> None:
        started: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)
            started.append(process)
            return process

        with (
            mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen", side_effect=capture_popen),
            mock.patch("speed_of_cinnamon.command_chain._clipboard_lock_identity_for_pid", return_value=None),
        ):
            with self.assertRaisesRegex(CommandChainError, "process identity could not be verified"):
                run_process_bounded_output(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

        self.assertEqual(len(started), 1)
        process = started[0]
        try:
            self.assertIsNotNone(process.poll())
            self.assertTrue(process.stdout is None or process.stdout.closed)
            self.assertTrue(process.stderr is None or process.stderr.closed)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=1)

    def test_run_process_bounded_output_cleans_up_when_selector_fails(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc.returncode = None
        proc.stdout = None
        proc.stderr = None
        selector = mock.Mock()
        selector.get_map.return_value = {"stream": mock.Mock(fileobj=mock.Mock())}
        selector.select.side_effect = OSError("selector failed")

        with (
            mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen", return_value=proc),
            mock.patch("speed_of_cinnamon.command_chain._clipboard_lock_identity_for_pid", return_value="current"),
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg,
            mock.patch("speed_of_cinnamon.command_chain.selectors.DefaultSelector", return_value=selector),
        ):
            with self.assertRaisesRegex(OSError, "selector failed") as caught:
                run_process_bounded_output(
                    ["command"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

        mocked_killpg.assert_called_once_with(1234, command_chain_module.signal.SIGKILL)
        proc.wait.assert_called_once_with(timeout=1)
        self.assertNotIn("cleanup", " ".join(getattr(caught.exception, "__notes__", ())))

    def test_run_process_bounded_output_does_not_wait_for_inherited_pipe_after_root_exit(self) -> None:
        code = (
            "import os, time\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            "    time.sleep(3)\n"
            "else:\n"
            "    os._exit(0)\n"
        )
        started = time.monotonic()
        returncode, stdout, stderr = run_process_bounded_output(
            [sys.executable, "-c", code],
            timeout_seconds=2,
            max_output_bytes=128,
            env={},
            label="post-process",
        )

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"")
        self.assertLess(time.monotonic() - started, 2.0)

    def test_run_process_bounded_output_cleans_pipe_holder_after_root_exit_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child.pid"
            code = (
                "import os, sys, time\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid()))\n"
                "    time.sleep(3)\n"
                "else:\n"
                "    while not os.path.exists(sys.argv[1]): time.sleep(0.001)\n"
                "    time.sleep(0.01)\n"
                "    os._exit(0)\n"
            )
            returncode, stdout, stderr = run_process_bounded_output(
                [sys.executable, "-c", code, str(marker)],
                timeout_seconds=2,
                max_output_bytes=128,
                env={},
                label="post-process",
            )

            self.assertEqual(returncode, 0)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text(encoding="ascii"))
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except FileNotFoundError:
                    break
                state = raw[raw.rindex(")") + 2 :].split()[0]
                if state in {"Z", "X", "x"}:
                    break
                time.sleep(0.01)
            else:
                self.fail("pipe-holder descendant survived root-exit cleanup")

    def test_run_process_bounded_output_cleans_session_descendant_after_pipes_close(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child.pid"
            code = (
                "import os, sys, time\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid()))\n"
                "    os.close(1)\n"
                "    os.close(2)\n"
                "    time.sleep(3)\n"
                "else:\n"
                "    while not os.path.exists(sys.argv[1]): time.sleep(0.001)\n"
                "    os._exit(0)\n"
            )
            returncode, stdout, stderr = run_process_bounded_output(
                [sys.executable, "-c", code, str(marker)],
                timeout_seconds=2,
                max_output_bytes=128,
                env={},
                label="post-process",
            )

            self.assertEqual(returncode, 0)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")
            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text(encoding="ascii"))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except FileNotFoundError:
                    break
                state = raw[raw.rindex(")") + 2 :].split()[0]
                if state in {"Z", "X", "x"}:
                    break
                time.sleep(0.01)
            else:
                self.fail("session descendant survived after output pipes closed")

    def test_run_process_bounded_output_fails_closed_when_root_tree_scan_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "child.pid"
            code = (
                "import os, sys, time\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid()))\n"
                "    time.sleep(3)\n"
                "else:\n"
                "    while not os.path.exists(sys.argv[1]): time.sleep(0.001)\n"
                "    os._exit(0)\n"
            )
            with mock.patch(
                "speed_of_cinnamon.command_chain._process_tree_descendant_identities",
                return_value=None,
            ):
                with self.assertRaisesRegex(CommandChainError, "descendant cleanup scan was incomplete"):
                    run_process_bounded_output(
                        [sys.executable, "-c", code, str(marker)],
                        timeout_seconds=2,
                        max_output_bytes=128,
                        env={},
                        label="post-process",
                    )

            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text(encoding="ascii"))
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except FileNotFoundError:
                    break
                state = raw[raw.rindex(")") + 2 :].split()[0]
                if state in {"Z", "X", "x"}:
                    break
                time.sleep(0.01)
            else:
                self.fail("pipe-holder survived incomplete tree-scan cleanup")

    def test_run_process_bounded_output_cleans_session_escaped_descendant_after_root_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "escaped-child.pid"
            code = (
                "import os, sys, time\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    os.setsid()\n"
                "    open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid()))\n"
                "    time.sleep(2)\n"
                "else:\n"
                "    deadline = time.monotonic() + 0.2\n"
                "    while not os.path.exists(sys.argv[1]) and time.monotonic() < deadline:\n"
                "        time.sleep(0.01)\n"
                "    time.sleep(0.2)\n"
                "    os._exit(0)\n"
            )
            returncode, stdout, stderr = run_process_bounded_output(
                [sys.executable, "-c", code, str(marker)],
                timeout_seconds=2,
                max_output_bytes=128,
                env={},
                label="post-process",
            )

            self.assertEqual(returncode, 0)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.01)
            self.assertTrue(marker.exists())
            child_pid = int(marker.read_text(encoding="ascii"))
            while time.monotonic() < deadline:
                try:
                    raw = Path(f"/proc/{child_pid}/stat").read_text(encoding="ascii")
                except FileNotFoundError:
                    break
                state = raw[raw.rindex(")") + 2 :].split()[0]
                if state in {"Z", "X", "x"}:
                    break
                time.sleep(0.01)
            else:
                self.fail("session-escaped descendant survived successful command")

    def test_terminate_bounded_process_kills_process_group(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234

        with mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg:
            command_chain_module._terminate_bounded_process(proc)

        mocked_killpg.assert_called_once_with(1234, command_chain_module.signal.SIGKILL)
        proc.kill.assert_not_called()

    def test_terminate_bounded_process_rejects_reused_process_identity(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc._soc_process_identity = "old-process"

        with (
            mock.patch(
                "speed_of_cinnamon.command_chain._output_process_identity_is_current",
                return_value=False,
            ),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg,
        ):
            self.assertFalse(command_chain_module._terminate_bounded_process(proc))

        mocked_killpg.assert_not_called()
        proc.kill.assert_not_called()

    def test_terminate_bounded_process_fails_closed_when_real_identity_is_missing(self) -> None:
        class UnknownPopen:
            __module__ = "subprocess"

            def __init__(self) -> None:
                self.pid = 1234
                self.returncode = None

            def wait(self, timeout: int | None = None) -> None:
                raise AssertionError("unverified process must not be waited after cleanup abort")

            def kill(self) -> None:
                raise AssertionError("unverified process must not be killed")

        proc = UnknownPopen()
        with mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg:
            self.assertFalse(command_chain_module._terminate_bounded_process(proc))

        mocked_killpg.assert_not_called()

    def test_terminate_bounded_process_cleans_captured_tree_after_root_exit_identity_change(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc.returncode = 0

        with (
            mock.patch(
                "speed_of_cinnamon.command_chain._output_process_identity_is_current",
                return_value=False,
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain._kill_output_process_tree",
                return_value=True,
            ) as mocked_tree_kill,
            mock.patch(
                "speed_of_cinnamon.command_chain._wait_for_output_process_tree_stop",
                return_value=True,
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain._output_process_is_reaped",
                return_value=False,
            ),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg,
        ):
            self.assertTrue(
                command_chain_module._terminate_bounded_process(
                    proc,
                    process_tree={5678: "child-identity"},
                )
            )

        mocked_tree_kill.assert_called_once_with({5678: "child-identity"})
        mocked_killpg.assert_not_called()
        proc.kill.assert_not_called()
        proc.wait.assert_called_once_with(timeout=1)

    def test_terminate_bounded_process_does_not_kill_reused_pid_after_group_failure(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc.returncode = None
        proc._soc_process_identity = "owned-process"
        with (
            mock.patch(
                "speed_of_cinnamon.command_chain._output_process_identity_is_current",
                side_effect=[True, True, False],
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain._output_process_is_reaped",
                return_value=False,
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain._kill_output_process_tree",
                return_value=True,
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain._wait_for_output_process_tree_stop",
                return_value=True,
            ),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg", side_effect=OSError("permission denied")),
        ):
            self.assertFalse(
                command_chain_module._terminate_bounded_process(
                    proc,
                    process_tree={5678: "child-identity"},
                )
            )

        proc.kill.assert_not_called()

    def test_terminate_bounded_process_confirms_root_without_process_tree(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        with (
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg"),
        ):
            self.assertTrue(command_chain_module._terminate_bounded_process(proc))

    def test_terminate_bounded_process_does_not_signal_reaped_root_group(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc.returncode = 0
        with (
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._output_process_is_reaped", return_value=True),
            mock.patch(
                "speed_of_cinnamon.command_chain._process_tree_descendant_identities",
                return_value={5678: "child-identity"},
            ),
            mock.patch("speed_of_cinnamon.command_chain._kill_output_process_tree", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._wait_for_output_process_tree_stop", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg,
        ):
            self.assertTrue(command_chain_module._terminate_bounded_process(proc))

        mocked_killpg.assert_not_called()
        proc.wait.assert_called_once_with(timeout=1)

    def test_terminate_bounded_process_fails_closed_for_reaped_root_with_unknown_tree(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc.returncode = 0
        with (
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._output_process_is_reaped", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain.os.killpg") as mocked_killpg,
        ):
            self.assertFalse(command_chain_module._terminate_bounded_process(proc))

        mocked_killpg.assert_not_called()
        proc.wait.assert_called_once_with(timeout=1)

    def test_run_command_chain_rejects_invalid_command_input_utf8(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "input is not valid UTF-8"):
            run_command_chain([("cmd",)], "\udcff", label="post-process")

    def test_run_command_chain_rejects_invalid_command_output_utf8(self) -> None:
        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes, kwargs
            return 0, b"\xff", b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(CommandChainError, "not valid UTF-8"):
                run_command_chain([("cmd",)], "seed", label="post-process")

    def test_run_command_chain_rejects_control_characters_in_command_output(self) -> None:
        def fake_run(argv: list[str], input_bytes: bytes, **kwargs: object) -> tuple[int, bytes, bytes]:
            del argv, input_bytes, kwargs
            return 0, b"ok\x1b[31mred\x1b[0m", b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
        ):
            with self.assertRaisesRegex(CommandChainError, "invalid control character") as cm:
                run_command_chain([("cmd",)], "seed", label="post-process")

        self.assertNotIn("\x1b", str(cm.exception))

    def test_run_command_chain_rejects_too_many_segments(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "too many segments"):
            run_command_chain([("cmd",)] * (MAX_COMMAND_SEGMENTS + 1), "", label="post-process")

    def test_filtered_environment_rejects_non_mapping_inputs(self) -> None:
        from speed_of_cinnamon.cli import _filtered_environment as cli_filtered_environment
        from speed_of_cinnamon.command_chain import _filtered_environment as chain_filtered_environment
        from speed_of_cinnamon.output import _filtered_environment as output_filtered_environment
        from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment
        from speed_of_cinnamon.transcriber import _filtered_environment as transcriber_filtered_environment

        validators = [
            cli_filtered_environment,
            chain_filtered_environment,
            output_filtered_environment,
            recorder_filtered_environment,
            transcriber_filtered_environment,
        ]
        for validate_env in validators:
            with self.subTest(func=validate_env.__module__):
                with self.assertRaisesRegex(RuntimeError, "environment base must be a mapping"):
                    validate_env(base={"k": 1})  # type: ignore[arg-type]
                with self.assertRaisesRegex(RuntimeError, "environment base must be a mapping"):
                    validate_env(["bad"])  # type: ignore[arg-type]

    def test_filtered_environment_rejects_invalid_items(self) -> None:
        from speed_of_cinnamon.cli import _filtered_environment as cli_filtered_environment
        from speed_of_cinnamon.command_chain import _filtered_environment as chain_filtered_environment
        from speed_of_cinnamon.output import _filtered_environment as output_filtered_environment
        from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment
        from speed_of_cinnamon.transcriber import _filtered_environment as transcriber_filtered_environment

        validators = [
            cli_filtered_environment,
            chain_filtered_environment,
            output_filtered_environment,
            recorder_filtered_environment,
            transcriber_filtered_environment,
        ]
        for validate_env in validators:
            with self.subTest(func=validate_env.__module__):
                with self.assertRaisesRegex(RuntimeError, "environment keys must be text"):
                    validate_env(base={1: "value"})  # type: ignore[dict-key]
                with self.assertRaisesRegex(RuntimeError, "environment values must be text"):
                    validate_env(base={"key": False})  # type: ignore[arg-type]
                with self.assertRaisesRegex(RuntimeError, "environment key contains invalid control character"):
                    validate_env(base={"BAD\nKEY": "value"})
                with self.assertRaisesRegex(RuntimeError, "environment value contains invalid control character"):
                    validate_env(base={"SAFE_KEY": "bad\x00value"})
                with self.assertRaisesRegex(RuntimeError, "environment key is not allowed: LD_PRELOAD"):
                    validate_env(base={"LD_PRELOAD": "x"})

    def test_filtered_environment_skips_non_text_environment_values(self) -> None:
        from speed_of_cinnamon.cli import _filtered_environment as cli_filtered_environment
        from speed_of_cinnamon.command_chain import _filtered_environment as chain_filtered_environment
        from speed_of_cinnamon.output import _filtered_environment as output_filtered_environment
        from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment
        from speed_of_cinnamon.transcriber import _filtered_environment as transcriber_filtered_environment

        validators = [
            cli_filtered_environment,
            chain_filtered_environment,
            output_filtered_environment,
            recorder_filtered_environment,
            transcriber_filtered_environment,
        ]
        for validate_env in validators:
            with self.subTest(func=validate_env.__module__):
                with mock.patch(f"{validate_env.__module__}.os.environ.__getitem__", return_value=123):
                    env = validate_env()
                self.assertNotIn("HOME", env)
                self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_filtered_environment_skips_inherited_control_character_values(self) -> None:
        from speed_of_cinnamon.cli import _filtered_environment as cli_filtered_environment
        from speed_of_cinnamon.command_chain import _filtered_environment as chain_filtered_environment
        from speed_of_cinnamon.output import _filtered_environment as output_filtered_environment
        from speed_of_cinnamon.recorder import _filtered_environment as recorder_filtered_environment
        from speed_of_cinnamon.transcriber import _filtered_environment as transcriber_filtered_environment

        validators = [
            cli_filtered_environment,
            chain_filtered_environment,
            output_filtered_environment,
            recorder_filtered_environment,
            transcriber_filtered_environment,
        ]
        for validate_env in validators:
            with self.subTest(func=validate_env.__module__):
                with mock.patch(f"{validate_env.__module__}.os.environ.__getitem__", return_value="bad\nhome"):
                    env = validate_env()
                self.assertNotIn("HOME", env)
                self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", env)

    def test_run_command_chain_accepts_max_tokens_per_segment(self) -> None:
        segment: list[str] = ["cmd"] + ["a"] * (MAX_COMMAND_SEGMENT_TOKENS - 1)
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"):
            with mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                return_value=(0, b"", b""),
            ):
                output = run_command_chain([tuple(segment)], "", label="post-process")
        self.assertEqual(output, "")

    def test_run_command_chain_rejects_max_plus_one_tokens_in_segment(self) -> None:
        segment: list[str] = ["cmd"] + ["a"] * MAX_COMMAND_SEGMENT_TOKENS
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"):
            with self.assertRaisesRegex(CommandChainError, "segment is too long"):
                run_command_chain([tuple(segment)], "", label="post-process")

    def test_run_command_chain_reports_command_not_found(self) -> None:
        with mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="missing"):
            with mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=FileNotFoundError("missing")):
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
            "speed_of_cinnamon.command_chain.run_process_bounded_output",
            side_effect=subprocess.TimeoutExpired(cmd="cmd", timeout=0.01),
        ):
            with self.assertRaisesRegex(CommandChainError, "timed out"):
                run_command_chain([("slow",)], "", label="post-process", timeout_seconds=1)

    def test_run_command_chain_redacts_execution_error_detail(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                side_effect=OSError("/secret/transcript-token.txt"),
            ),
        ):
            with self.assertRaisesRegex(CommandChainError, "command execution failed") as caught:
                run_command_chain([("cmd",)], "", label="post-process")

        self.assertNotIn("/secret/transcript-token.txt", str(caught.exception))

    def test_run_command_chain_rejects_empty_chain(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command chain is empty"):
            run_command_chain([], "seed", label="post-process")

    def test_run_command_chain_rejects_control_characters_in_label(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "label contains invalid control character"):
            run_command_chain([("printf", "ok")], "seed", label="post\nprocess")

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

    def test_filtered_environment_rejects_control_characters_in_base(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "environment key contains invalid control character"):
            _filtered_environment(base={"BAD\nKEY": "value"})
        with self.assertRaisesRegex(CommandChainError, "environment value contains invalid control character"):
            _filtered_environment(base={"SAFE_KEY": "bad\x00value"})

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

    def test_run_command_chain_rejects_direct_segment_that_is_too_long(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command too long"):
            run_command_chain([("cmd", "x" * MAX_COMMAND_LENGTH_CHARS)], "", label="post-process")

    def test_run_command_chain_rejects_direct_segment_invalid_utf8_argument(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "not valid UTF-8"):
            run_command_chain([("cmd", "\udcff")], "", label="post-process")

    def test_run_command_chain_rejects_escaped_control_chars_in_command_argument(self) -> None:
        for argument in ("arg\\nvalue", "arg\\x1bvalue", "arg\\u001bvalue", "arg\\x85value"):
            with self.subTest(argument=argument):
                with self.assertRaisesRegex(CommandChainError, "command contains invalid control character"):
                    run_command_chain([("cmd", argument)], "", label="post-process")

    def test_run_command_chain_rejects_other_control_characters_in_command_argument(self) -> None:
        for argument in ("arg\x1fvalue", "arg\x85value"):
            with self.subTest(argument=repr(argument)):
                with self.assertRaisesRegex(CommandChainError, "command contains invalid control character"):
                    run_command_chain([("cmd", argument)], "", label="post-process")

    def test_run_command_chain_rejects_command_with_path_separator(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "path separators"):
            run_command_chain([("/usr/bin/cmd",)], "", label="post-process")

    def test_run_command_chain_rejects_too_large_input(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "input exceeded"):
            run_command_chain([("cmd",)], "x" * 1_000_001, label="post-process", max_input_chars=1_000_000)

    def test_read_file_head_rejects_invalid_utf8(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write(b"ok\xff")
            with self.assertRaisesRegex(CommandChainError, "not valid UTF-8") as caught:
                _read_file_head(handle, 10)

        self.assertNotIn("invalid start byte", str(caught.exception))

    def test_read_file_head_handles_multibyte_character_at_limit(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write("😀x".encode("utf-8"))
            self.assertEqual(_read_file_head(handle, 1), "😀")

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
