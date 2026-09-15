from __future__ import annotations

import errno
import os
import json
import resource
import shutil
import signal
import subprocess
import sys
import time
import unittest
import tempfile
import threading
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import command_chain as command_chain_module
from speed_of_cinnamon import process_priority
from speed_of_cinnamon.command_chain import (
    CommandChainError,
    MAX_COMMAND_LENGTH_CHARS,
    MAX_COMMAND_TIMEOUT_SECONDS,
    MAX_COMMAND_SEGMENT_TOKENS,
    MAX_COMMAND_SEGMENTS,
    MAX_COMMAND_INPUT_CHARS,
    MAX_COMMAND_OUTPUT_CHARS,
    _command_path,
    _contains_escaped_null,
    _filtered_environment,
    _LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
    _filesize,
    _read_file_head,
    run_process_bounded_output,
    run_command_chain,
    split_command_chain,
)
from speed_of_cinnamon.personalization import MAX_PERSONAL_CONTEXT_CHARS, MAX_VOCABULARY_CHARS


def _scope_manager_unavailable(stderr: bytes) -> bool:
    detail = stderr.decode("utf-8", errors="replace").lower()
    return any(
        marker in detail
        for marker in (
            "failed to connect to bus: no medium found",
            "failed to connect to bus: no such file or directory",
            "failed to create bus connection: no medium found",
            "failed to create bus connection: no such file or directory",
        )
    )


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
        with self.assertRaisesRegex(CommandChainError, "invalid command command: contains control characters"):
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

    def test_run_command_chain_wraps_each_process_once_when_requested(self) -> None:
        wrapped: list[list[str]] = []
        started: list[list[str]] = []

        def fake_priority(argv: list[str]) -> list[str]:
            wrapped.append(argv)
            return ["nice", "--adjustment", "10", *argv]

        def fake_run(argv: list[str], *_args: object, **_kwargs: object) -> tuple[int, bytes, bytes]:
            started.append(argv)
            return 0, b"", b""

        with (
            mock.patch(
                "speed_of_cinnamon.command_chain._command_path",
                side_effect=["/usr/bin/first", "/usr/bin/second"],
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_scope_probe_command",
                return_value=["scope-probe"],
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_command",
                side_effect=fake_priority,
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                side_effect=fake_run,
            ),
        ):
            run_command_chain(
                [("first", "one"), ("second", "two")],
                "seed",
                label="custom",
                local_model_priority=True,
            )

        self.assertEqual(
            wrapped,
            [["/usr/bin/first", "one"], ["/usr/bin/second", "two"]],
        )
        self.assertEqual(
            started,
            [
                ["scope-probe"],
                ["nice", "--adjustment", "10", "/usr/bin/first", "one"],
                ["nice", "--adjustment", "10", "/usr/bin/second", "two"],
            ],
        )

    def test_scope_probe_failure_uses_direct_priority_once_and_cleans_environment(self) -> None:
        calls: list[tuple[list[str], bytes, dict[str, object]]] = []

        def fake_run(
            argv: list[str], input_bytes: bytes, **kwargs: object
        ) -> tuple[int, bytes, bytes]:
            calls.append((argv, input_bytes, kwargs))
            if argv == ["scope-probe"]:
                return 1, b"", b"Operation not permitted"
            return 0, b"direct-result\n", b""

        with (
            mock.patch(
                "speed_of_cinnamon.command_chain.command_environment",
                return_value={
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                    "XDG_RUNTIME_DIR": "/run/user/1000",
                },
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain._command_path",
                return_value="/usr/bin/tool",
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_scope_probe_command",
                return_value=["scope-probe"],
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_command"
            ) as scoped,
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_direct_command",
                return_value=["direct", "/usr/bin/tool"],
            ) as direct,
            mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                side_effect=fake_run,
            ),
        ):
            output = run_command_chain(
                [("tool", "argument")],
                "stdin-secret",
                label="local-model",
                local_model_priority=True,
            )

        self.assertEqual(output, "direct-result")
        scoped.assert_not_called()
        direct.assert_called_once_with(["/usr/bin/tool", "argument"])
        self.assertEqual(
            [call[0] for call in calls],
            [["scope-probe"], ["direct", "/usr/bin/tool"]],
        )
        self.assertEqual(calls[0][1], b"")
        self.assertEqual(calls[1][1], b"stdin-secret")
        self.assertTrue(calls[0][2]["preserve_user_systemd_environment"])
        self.assertFalse(calls[1][2]["preserve_user_systemd_environment"])
        self.assertEqual(calls[0][2]["deadline"], calls[1][2]["deadline"])
        direct_env = calls[1][2]["env"]
        self.assertIsInstance(direct_env, dict)
        self.assertNotIn("DBUS_SESSION_BUS_ADDRESS", direct_env)
        self.assertNotIn("XDG_RUNTIME_DIR", direct_env)

    def test_successful_scope_probe_never_retries_real_scope_failure(self) -> None:
        with (
            mock.patch(
                "speed_of_cinnamon.command_chain._command_path",
                return_value="/usr/bin/tool",
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_scope_probe_command",
                return_value=["scope-probe"],
            ),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_command",
                return_value=["scoped", "/usr/bin/tool", "argv-secret"],
            ) as scoped,
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_direct_command"
            ) as direct,
            mock.patch(
                "speed_of_cinnamon.command_chain.run_process_bounded_output",
                side_effect=[
                    (0, b"", b""),
                    (1, b"stdout-secret", b"stderr-secret"),
                ],
            ) as run_process,
        ):
            with self.assertRaises(CommandChainError) as context:
                run_command_chain(
                    [("tool", "argv-secret")],
                    "stdin-secret",
                    label="local-model",
                    local_model_priority=True,
                )

        detail = str(context.exception)
        self.assertIn("command output redacted", detail)
        for secret in ("argv-secret", "stdin-secret", "stdout-secret", "stderr-secret"):
            self.assertNotIn(secret, detail)
            self.assertNotIn(secret, repr(context.exception))
        self.assertEqual(run_process.call_count, 2)
        scoped.assert_called_once()
        direct.assert_not_called()

    def test_scope_probe_timeout_signal_or_unclear_cleanup_never_falls_back(self) -> None:
        failures: tuple[BaseException, ...] = (
            CommandChainError("local model priority probe timed out"),
            CommandChainError("local model priority probe process cleanup was not confirmed"),
            KeyboardInterrupt(),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                with (
                    mock.patch(
                        "speed_of_cinnamon.command_chain._command_path",
                        return_value="/usr/bin/tool",
                    ),
                    mock.patch(
                        "speed_of_cinnamon.command_chain.local_model_scope_probe_command",
                        return_value=["scope-probe"],
                    ),
                    mock.patch(
                        "speed_of_cinnamon.command_chain.local_model_direct_command"
                    ) as direct,
                    mock.patch(
                        "speed_of_cinnamon.command_chain.run_process_bounded_output",
                        side_effect=failure,
                    ),
                ):
                    with self.assertRaises(type(failure)):
                        run_command_chain(
                            [("tool",)],
                            "input",
                            label="local-model",
                            local_model_priority=True,
                        )
                direct.assert_not_called()

    def test_run_command_chain_real_local_priority_preserves_json_stdout(self) -> None:
        ionice = shutil.which("ionice", path=command_chain_module._TRUSTED_COMMAND_PATH)
        nice = shutil.which("nice", path=command_chain_module._TRUSTED_COMMAND_PATH)
        systemd_run = shutil.which("systemd-run", path=command_chain_module._TRUSTED_COMMAND_PATH)
        if not ionice or not nice or not systemd_run:
            self.skipTest("trusted local-priority helpers are unavailable")
        probe = (
            "import json, os, subprocess, sys; from pathlib import Path; "
            "marker=Path(sys.argv[1]); "
            "marker_fd=os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); "
            "os.write(marker_fd, str(os.getpid()).encode('ascii')); os.close(marker_fd); "
            "stdin_value=sys.stdin.read(); "
            f"result=subprocess.check_output([{ionice!r}, '--pid', str(os.getpid())], text=True); "
            "relative=next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() "
            "if line.startswith('0::')); "
            "cgroup=Path('/sys/fs/cgroup') / relative.lstrip('/'); "
            "cpu_weight=(cgroup / 'cpu.weight').read_text().strip() if (cgroup / 'cpu.weight').is_file() else None; "
            "io_weight=(cgroup / 'io.weight').read_text().strip() if (cgroup / 'io.weight').is_file() else None; "
            "print(json.dumps({'pid': os.getpid(), 'stdin': stdin_value, "
            "'nice': os.getpriority(os.PRIO_PROCESS, 0), 'io': result.strip(), "
            "'cgroup': relative, 'cpu_weight': cpu_weight, 'io_weight': io_weight}))"
        )
        input_text = '{"stdin":"preserved"}'

        def children() -> list[str]:
            with open(
                f"/proc/{os.getpid()}/task/{os.getpid()}/children",
                encoding="ascii",
            ) as stream:
                return stream.read().split()

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            marker = os.path.join(temporary_directory, "target-started")
            observations: list[tuple[str, int]] = []

            def observed_runner(*args, **kwargs):
                result = run_process_bounded_output(*args, **kwargs)
                observations.append((str(kwargs.get("label")), result[0]))
                return result

            parent_cgroup = next(
                line[3:]
                for line in Path("/proc/self/cgroup").read_text().splitlines()
                if line.startswith("0::")
            )
            fd_before = len(os.listdir("/proc/self/fd"))
            children_before = children()
            try:
                with mock.patch.object(
                    command_chain_module,
                    "run_process_bounded_output",
                    side_effect=observed_runner,
                ):
                    output = run_command_chain(
                        [("python3", "-c", probe, marker)],
                        input_text,
                        label="local-model",
                        timeout_seconds=5,
                        max_output_chars=2000,
                        local_model_priority=True,
                    )
            except CommandChainError:
                cleanup_confirmed = (
                    len(os.listdir("/proc/self/fd")) == fd_before
                    and children() == children_before
                )
                systemd_failed_cleanly = (
                    len(observations) == 2
                    and observations[0][0] == "local model priority probe"
                    and observations[0][1] > 0
                    and observations[1][0] == "local-model"
                    and observations[1][1] > 0
                    and cleanup_confirmed
                    and not os.path.lexists(marker)
                )
                if systemd_failed_cleanly:
                    direct_fd_before = len(os.listdir("/proc/self/fd"))
                    direct_children_before = children()
                    try:
                        direct_command = process_priority.local_model_direct_command(
                            ["/usr/bin/true"]
                        )
                    except process_priority.LocalModelPriorityError as exc:
                        self.skipTest(
                            "systemd scope and direct priority capabilities are unavailable: "
                            f"{exc}"
                        )
                    direct_returncode, _, _ = run_process_bounded_output(
                        direct_command,
                        b"",
                        timeout_seconds=3,
                        max_output_bytes=1024,
                        env={},
                        label="local model direct capability probe",
                    )
                    if (
                        direct_returncode > 0
                        and len(os.listdir("/proc/self/fd")) == direct_fd_before
                        and children() == direct_children_before
                    ):
                        self.skipTest(
                            "systemd scope and verified direct priority execution "
                            "are unavailable"
                        )
                raise

            payload = json.loads(output)
            self.assertEqual(
                observations[0][0],
                "local model priority probe",
            )
            self.assertEqual(observations[1], ("local-model", 0))
            self.assertEqual(len(observations), 2)
            self.assertGreaterEqual(payload["nice"], 10)
            self.assertIn("best-effort: prio 7", payload["io"])
            self.assertEqual(payload["stdin"], input_text)
            self.assertEqual(Path(marker).read_text(), str(payload["pid"]))
            marker_stat = os.stat(marker, follow_symlinks=False)
            self.assertEqual(marker_stat.st_mode & 0o777, 0o600)
            self.assertEqual(marker_stat.st_nlink, 1)
            self.assertEqual(os.listdir(temporary_directory), ["target-started"])
            self.assertFalse(os.path.exists(f"/proc/{payload['pid']}"))
            self.assertEqual(len(os.listdir("/proc/self/fd")), fd_before)
            self.assertEqual(children(), children_before)

            if observations[0][1] == 0:
                self.assertNotEqual(payload["cgroup"], parent_cgroup)
                self.assertEqual(payload["cpu_weight"], "10")
                self.assertEqual(payload["io_weight"], "default 10")
            else:
                self.assertGreater(observations[0][1], 0)
                self.assertEqual(payload["cgroup"], parent_cgroup)

    def test_scope_preflight_does_not_skip_unrelated_nonzero_host_error(self) -> None:
        self.assertFalse(_scope_manager_unavailable(b"systemd-run: operation not supported\n"))
        self.assertFalse(_scope_manager_unavailable(b"systemd-run: not supported\n"))

    def test_run_command_chain_fails_closed_before_spawn_when_priority_is_unavailable(self) -> None:
        from speed_of_cinnamon.process_priority import LocalModelPriorityError

        with (
            mock.patch("speed_of_cinnamon.command_chain._command_path", return_value="/usr/bin/tool"),
            mock.patch(
                "speed_of_cinnamon.command_chain.local_model_scope_probe_command",
                side_effect=LocalModelPriorityError("local model priority helper is unavailable: nice"),
            ),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output") as run_process,
        ):
            with self.assertRaisesRegex(CommandChainError, "local model priority helper is unavailable: nice"):
                run_command_chain(
                    [("tool",)],
                    "input",
                    label="local-model",
                    local_model_priority=True,
                )

        run_process.assert_not_called()

    def test_run_command_chain_rejects_non_boolean_local_model_priority(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "local_model_priority must be a boolean"):
            run_command_chain(
                [("printf",)],
                "",
                label="custom",
                local_model_priority=1,  # type: ignore[arg-type]
            )

    def test_run_command_chain_uses_one_total_deadline(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str], *_args: object, **_kwargs: object) -> tuple[int, bytes, bytes]:
            calls.append(argv)
            return 0, b"next\n", b""

        with (
            mock.patch("speed_of_cinnamon.command_chain.shutil.which", return_value="cmd"),
            mock.patch("speed_of_cinnamon.command_chain.run_process_bounded_output", side_effect=fake_run),
            mock.patch("speed_of_cinnamon.command_chain.time.monotonic", side_effect=[100.0, 100.0, 101.1]),
        ):
            with self.assertRaisesRegex(CommandChainError, "timed out after 1s"):
                run_command_chain(
                    [("cmd",), ("cmd",)],
                    "seed",
                    label="post-process",
                    timeout_seconds=1,
                )

        self.assertEqual(calls, [["cmd"]])

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

    def test_bounded_runner_preserves_only_user_systemd_connection_environment_when_requested(self) -> None:
        probe = (
            "import os; print(os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')); "
            "print(os.environ.get('XDG_RUNTIME_DIR', ''))"
        )
        with mock.patch.dict(
            os.environ,
            {
                "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                "XDG_RUNTIME_DIR": "/run/user/1000",
            },
            clear=False,
        ):
            returncode, stdout, stderr = run_process_bounded_output(
                [sys.executable, "-c", probe],
                timeout_seconds=5,
                max_output_bytes=4096,
                env={},
                label="systemd-scope-probe",
                preserve_user_systemd_environment=True,
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout.decode("utf-8"), "unix:path=/run/user/1000/bus\n/run/user/1000\n")
        self.assertEqual(stderr, b"")

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

    def test_bounded_timeout_and_output_cleanup_require_complete_scans(self) -> None:
        real_cleanup = command_chain_module._terminate_bounded_process
        cases = (
            ([sys.executable, "-c", "import time; time.sleep(30)"], "timed out"),
            ([sys.executable, "-c", "import sys; sys.stdout.write('x' * 4096)"], "output exceeded"),
        )
        for argv, expected_text in cases:
            with self.subTest(expected_text=expected_text):
                with mock.patch.object(
                    command_chain_module,
                    "_terminate_bounded_process",
                    wraps=real_cleanup,
                ) as mocked_cleanup:
                    with self.assertRaisesRegex(CommandChainError, expected_text):
                        run_process_bounded_output(
                            argv,
                            timeout_seconds=1,
                            max_output_bytes=32,
                            env={},
                            label="post-process",
                        )
                self.assertEqual(mocked_cleanup.call_count, 1)
                self.assertTrue(mocked_cleanup.call_args.kwargs["require_complete_scan"])

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
            with self.assertRaisesRegex(CommandChainError, "timeout_seconds must not exceed"):
                run_process_bounded_output(
                    ["command"],
                    timeout_seconds=MAX_COMMAND_TIMEOUT_SECONDS + 1,
                    max_output_bytes=128,
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
            mock.patch(
                "speed_of_cinnamon.command_chain._launch_bounded_process",
                return_value=command_chain_module._BoundedPopenResult(
                    proc,
                    command_chain_module._POPEN_OUTCOME_READY,
                    None,
                    None,
                ),
            ),
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

        mocked_cleanup.assert_called_once_with(proc, require_complete_scan=True)

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
            mock.patch(
                "speed_of_cinnamon.command_chain._launch_bounded_process",
                return_value=command_chain_module._BoundedPopenResult(
                    proc,
                    command_chain_module._POPEN_OUTCOME_READY,
                    None,
                    None,
                ),
            ),
            mock.patch("speed_of_cinnamon.command_chain._clipboard_lock_identity_for_pid", return_value="boot:123"),
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain._kill_output_process_with_pidfd", return_value=True) as mocked_pidfd,
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

        mocked_pidfd.assert_called_once_with(1234, "123")
        proc.wait.assert_called_once_with(timeout=1)
        self.assertIn(
            "post-process command process cleanup was not confirmed",
            " ".join(getattr(caught.exception, "__notes__", ())),
        )

    def test_run_process_bounded_output_cleans_real_process_when_selector_constructor_fails(self) -> None:
        started: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)
            started.append(process)
            return process

        with (
            mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen", side_effect=capture_popen),
            mock.patch("speed_of_cinnamon.command_chain.selectors.DefaultSelector", side_effect=OSError("selector constructor failed")),
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value={}),
        ):
            with self.assertRaisesRegex(OSError, "selector constructor failed"):
                run_process_bounded_output(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

        self.assertEqual(len(started), 1)
        process = started[0]
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout is None or process.stdout.closed)
        self.assertTrue(process.stderr is None or process.stderr.closed)

    def test_selector_baseexception_marks_incomplete_descendant_and_pipe_scans(self) -> None:
        started: list[subprocess.Popen[bytes]] = []
        real_popen = subprocess.Popen

        def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)
            started.append(process)
            return process

        with (
            mock.patch("speed_of_cinnamon.command_chain.subprocess.Popen", side_effect=capture_popen),
            mock.patch(
                "speed_of_cinnamon.command_chain.selectors.DefaultSelector",
                side_effect=KeyboardInterrupt("selector interrupted"),
            ),
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain._process_pipe_holder_identities", return_value=None),
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                run_process_bounded_output(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    timeout_seconds=1,
                    max_output_bytes=128,
                    env={},
                    label="post-process",
                )

        self.assertIn(
            "post-process command process cleanup was not confirmed",
            " ".join(getattr(caught.exception, "__notes__", ())),
        )
        self.assertEqual(len(started), 1)
        process = started[0]
        self.assertIsNotNone(process.poll())
        self.assertTrue(process.stdout is None or process.stdout.closed)
        self.assertTrue(process.stderr is None or process.stderr.closed)

    def test_run_process_bounded_output_retries_interrupted_pipe_read(self) -> None:
        real_read = command_chain_module.os.read
        real_popen = command_chain_module.subprocess.Popen
        active = False
        attempts = 0

        def interrupted_once(fd, size):
            nonlocal attempts
            if not active:
                return real_read(fd, size)
            attempts += 1
            if attempts == 1:
                raise InterruptedError()
            return real_read(fd, size)

        def start_process(*args, **kwargs):
            nonlocal active
            process = real_popen(*args, **kwargs)
            active = True
            return process

        with (
            mock.patch.object(command_chain_module.os, "read", side_effect=interrupted_once),
            mock.patch.object(command_chain_module.subprocess, "Popen", side_effect=start_process),
        ):
            returncode, stdout, stderr = run_process_bounded_output(
                [sys.executable, "-c", "print('ok')"],
                timeout_seconds=2,
                max_output_bytes=128,
                env={},
                label="post-process",
            )

        self.assertEqual(returncode, 0)
        self.assertEqual(stdout, b"ok\n")
        self.assertEqual(stderr, b"")
        self.assertGreaterEqual(attempts, 2)

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

    def test_process_scan_retries_transient_incomplete_result(self) -> None:
        scans = iter((None, {1234: "4567"}))

        result = command_chain_module._retry_process_scan(lambda: next(scans))

        self.assertEqual(result, {1234: "4567"})

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

    def test_terminate_bounded_process_kills_process_with_pidfd(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        proc.returncode = None
        proc._soc_process_identity = "boot:123"

        with (
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value={}),
            mock.patch("speed_of_cinnamon.command_chain._kill_output_process_with_pidfd", return_value=True) as mocked_pidfd,
        ):
            self.assertTrue(command_chain_module._terminate_bounded_process(proc))

        mocked_pidfd.assert_called_once_with(1234, "123")
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

    def test_terminate_bounded_process_fails_closed_without_identity(self) -> None:
        proc = mock.Mock()
        proc.pid = 1234
        with (
            mock.patch("speed_of_cinnamon.command_chain._process_tree_descendant_identities", return_value=None),
            mock.patch("speed_of_cinnamon.command_chain._output_process_identity_is_current", return_value=True),
            mock.patch("speed_of_cinnamon.command_chain._kill_output_process_with_pidfd") as mocked_pidfd,
        ):
            self.assertFalse(command_chain_module._terminate_bounded_process(proc))

        mocked_pidfd.assert_not_called()
        proc.kill.assert_not_called()
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
        with self.assertRaisesRegex(CommandChainError, "timeout_seconds must not exceed"):
            run_command_chain(
                [("cmd",)],
                "",
                label="post-process",
                timeout_seconds=MAX_COMMAND_TIMEOUT_SECONDS + 1,
            )
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

    def test_run_command_chain_preserves_literal_null_escape_in_input(self) -> None:
        value = r"hello \x00"
        self.assertEqual(run_command_chain([("cat",)], value, label="post-process"), value)

    def test_run_command_chain_preserves_literal_null_escapes_in_personalization_environment(self) -> None:
        for value in (r"literal \x00 text", r"literal \u0000 text"):
            with self.subTest(value=value):
                result = run_command_chain(
                    [("sh", "-c", 'printf "%s" "$SPEED_OF_CINNAMON_CONTEXT"')],
                    "",
                    label="post-process",
                    personal_context=value,
                    include_personalization_env=True,
                )
                self.assertEqual(result, value)

    def test_run_command_chain_rejects_null_bytes_in_command_segment(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid null byte"):
            run_command_chain([("cmd\x00",)], "", label="post-process")

    def test_run_command_chain_rejects_escaped_null_in_command_segment(self) -> None:
        with self.assertRaisesRegex(CommandChainError, "command contains invalid control character"):
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

    def test_read_file_head_preserves_literal_null_escape(self) -> None:
        with tempfile.TemporaryFile() as handle:
            handle.write("ok\\x00end".encode("utf-8"))
            self.assertEqual(_read_file_head(handle, 10), r"ok\x00end")

    def _direct_wrapper(self, script: str, *arguments: str) -> list[str]:
        for helper in ("ionice", "nice"):
            if not shutil.which(helper, path=command_chain_module._TRUSTED_COMMAND_PATH):
                self.skipTest(f"trusted {helper} helper is unavailable")
        if (
            sys.platform != "linux"
            or os.uname().machine != "x86_64"
            or not callable(getattr(os, "pidfd_open", None))
            or not callable(getattr(signal, "pidfd_send_signal", None))
        ):
            self.skipTest("direct supervisor Linux capabilities are unavailable")
        return process_priority.local_model_direct_command(
            [os.path.realpath(sys.executable), "-c", script, *arguments]
        )

    def test_past_deadline_never_calls_popen(self) -> None:
        with (
            mock.patch.object(command_chain_module.subprocess, "Popen") as popen,
            self.assertRaisesRegex(CommandChainError, "timed out"),
        ):
            run_process_bounded_output(
                ["/usr/bin/true"],
                timeout_seconds=1,
                max_output_bytes=128,
                env={},
                label="local-model",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                deadline=time.monotonic() - 1.0,
            )
        popen.assert_not_called()

    def test_direct_supervisor_enforces_limits_seccomp_and_fork_inheritance(self) -> None:
        script = r'''
import ctypes, errno, json, os, resource
libc = ctypes.CDLL(None, use_errno=True)
def set_io(number):
    ctypes.set_errno(0)
    result = libc.syscall(number, 1, 0, 2 << 13)
    return [result, ctypes.get_errno()]
def state():
    status = open('/proc/self/status', encoding='ascii').read()
    values = dict(line.split(':', 1) for line in status.splitlines() if ':' in line)
    return {
        'native': set_io(251),
        'x32': set_io(0x40000000 | 251),
        'nnp': values['NoNewPrivs'].strip(),
        'seccomp': values['Seccomp'].strip(),
        'nice_limit': resource.getrlimit(resource.RLIMIT_NICE),
        'rt_limit': resource.getrlimit(resource.RLIMIT_RTPRIO),
        'nproc_limit': resource.getrlimit(resource.RLIMIT_NPROC),
    }
read_fd, write_fd = os.pipe()
child = os.fork()
if child == 0:
    os.close(read_fd)
    os.write(write_fd, json.dumps(state()).encode('ascii'))
    os.close(write_fd)
    os._exit(0)
os.close(write_fd)
child_data = os.read(read_fd, 4096)
os.close(read_fd)
os.waitpid(child, 0)
marker_fd = os.open(os.environ['SOC_TEST_MARKER'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(marker_fd, b'once')
os.close(marker_fd)
print(json.dumps({'root': state(), 'child': json.loads(child_data)}))
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            marker = os.path.join(temporary_directory, "launched")
            command = self._direct_wrapper(script)
            returncode, stdout, stderr = run_process_bounded_output(
                command,
                timeout_seconds=5,
                max_output_bytes=8192,
                env={"SOC_TEST_MARKER": marker},
                label="local-model",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
            self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
            result = json.loads(stdout)
            self.assertEqual(Path(marker).read_bytes(), b"once")
            for state in (result["root"], result["child"]):
                self.assertEqual(state["native"], [-1, errno.EPERM])
                self.assertEqual(state["x32"], [-1, errno.EPERM])
                self.assertEqual(state["nnp"], "1")
                self.assertEqual(state["seccomp"], "2")
                self.assertEqual(state["nice_limit"], [0, 0])
                self.assertEqual(state["rt_limit"], [0, 0])
                self.assertGreater(state["nproc_limit"][0], 0)
                self.assertLessEqual(
                    state["nproc_limit"][0],
                    process_priority._LOCAL_MODEL_DIRECT_MAX_CHILDREN,
                )
                self.assertEqual(
                    state["nproc_limit"][0],
                    state["nproc_limit"][1],
                )

    def test_direct_target_cannot_disable_or_signal_supervisor(self) -> None:
        script = r'''
import ctypes, errno, json, os, signal, sys, time
libc = ctypes.CDLL(None, use_errno=True)
supervisor = os.getppid()
def attempt(number, *arguments):
    ctypes.set_errno(0)
    result = libc.syscall(number, *arguments)
    return [result, ctypes.get_errno()]
def attacks():
    calls = {
        'prctl': (157, 1, 0, 0, 0, 0),
        'kill-stop': (62, supervisor, signal.SIGSTOP),
        'kill-kill': (62, supervisor, signal.SIGKILL),
        'kill-group': (62, -supervisor, 0),
        'kill-all': (62, -1, 0),
        'tkill': (200, supervisor, signal.SIGSTOP),
        'tgkill': (234, supervisor, supervisor, signal.SIGSTOP),
        'rt-queue': (129, supervisor, 0, 0),
        'rt-tg-queue': (297, supervisor, supervisor, 0, 0),
        'rt-queue-x32-real': (0x40000000 | 524, supervisor, 0, 0),
        'rt-tg-queue-x32-real': (0x40000000 | 536, supervisor, supervisor, 0, 0),
        'pidfd-open': (434, supervisor, 0),
        'pidfd-send': (424, -1, signal.SIGSTOP, 0, 0),
        'setpgid': (109, 0, supervisor),
    }
    return {
        f'{name}-{abi}': attempt(number | bit, *arguments)
        for name, (number, *arguments) in calls.items()
        for abi, bit in (
            (('exact', 0),)
            if number & 0x40000000
            else (('native', 0), ('x32', 0x40000000))
        )
    }
read_fd, write_fd = os.pipe()
child = os.fork()
if child == 0:
    os.close(read_fd)
    os.setsid()
    child_attacks = attacks()
    grandchild = os.fork()
    if grandchild == 0:
        os.close(write_fd)
        null = os.open('/dev/null', os.O_WRONLY)
        os.dup2(null, 1); os.dup2(null, 2); os.close(null)
        time.sleep(0.8)
        fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        os._exit(0)
    os.write(write_fd, json.dumps({'attacks': child_attacks, 'pid': grandchild}).encode('ascii'))
    os.close(write_fd)
    os._exit(0)
os.close(write_fd)
child_data = os.read(read_fd, 65536)
os.close(read_fd)
os.waitpid(child, 0)
print(json.dumps({
    'root': attacks(),
    'child': json.loads(child_data),
    'supervisor': supervisor,
    'root_group': os.getpgrp(),
}))
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            unused = os.path.join(temporary_directory, "unused")
            marker = os.path.join(temporary_directory, "late")
            command = self._direct_wrapper(script, unused, marker)
            returncode, stdout, stderr = run_process_bounded_output(
                command,
                timeout_seconds=5,
                max_output_bytes=65536,
                env={},
                label="local-model",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
            self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
            result = json.loads(stdout)
            self.assertNotEqual(result["root_group"], result["supervisor"])
            for attacks in (result["root"], result["child"]["attacks"]):
                for name, syscall_result in attacks.items():
                    with self.subTest(name=name):
                        self.assertEqual(syscall_result, [-1, errno.EPERM])
            escaped_pid = result["child"]["pid"]
            time.sleep(0.9)
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(os.path.exists(f"/proc/{escaped_pid}"))

    def test_direct_target_exit_and_signal_codes_are_mirrored(self) -> None:
        cases = (
            ("pass", 0),
            ("raise SystemExit(7)", 7),
            ("import os, signal; os.kill(os.getpid(), signal.SIGTERM)", -signal.SIGTERM),
            ("import os, signal; os.kill(os.getpid(), signal.SIGKILL)", -signal.SIGKILL),
        )
        for script, expected in cases:
            with self.subTest(expected=expected):
                returncode, _, stderr = run_process_bounded_output(
                    self._direct_wrapper(script),
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="local-model",
                    _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                )
                self.assertEqual(
                    returncode,
                    expected,
                    stderr.decode(errors="replace"),
                )

    def test_direct_supervisor_drains_controlled_fork_storm(self) -> None:
        script = r'''
import os, signal, sys, time
count = 32
pid_fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
read_fd, write_fd = os.pipe()
for _ in range(count):
    child = os.fork()
    if child == 0:
        os.close(read_fd)
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        os.write(pid_fd, f'{os.getpid()}\n'.encode('ascii'))
        os.close(pid_fd)
        os.write(write_fd, b'R'); os.close(write_fd)
        null = os.open('/dev/null', os.O_WRONLY)
        os.dup2(null, 1); os.dup2(null, 2); os.close(null)
        time.sleep(0.9)
        fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.write(fd, b'x'); os.close(fd)
        os._exit(0)
os.close(pid_fd)
os.close(write_fd)
ready = bytearray()
while len(ready) < count:
    chunk = os.read(read_fd, count - len(ready))
    if not chunk: os._exit(67)
    ready.extend(chunk)
os.close(read_fd)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            pid_file = os.path.join(temporary_directory, "pids")
            marker = os.path.join(temporary_directory, "late")
            returncode, _, stderr = run_process_bounded_output(
                self._direct_wrapper(script, pid_file, marker),
                timeout_seconds=5,
                max_output_bytes=4096,
                env={},
                label="local-model",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
            self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
            process_ids = {
                int(line)
                for line in Path(pid_file).read_text(encoding="ascii").splitlines()
            }
            self.assertEqual(len(process_ids), 32)
            time.sleep(1.0)
            self.assertFalse(os.path.exists(marker))
            self.assertTrue(
                all(not os.path.exists(f"/proc/{process_id}") for process_id in process_ids)
            )

    def test_signal_during_supervisor_finalization_is_not_swallowed(self) -> None:
        script = r'''
import os, signal, sys, time
read_fd, write_fd = os.pipe()
child = os.fork()
if child == 0:
    os.close(read_fd)
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, str(os.getpid()).encode('ascii')); os.close(fd)
    os.write(write_fd, b'R'); os.close(write_fd)
    null = os.open('/dev/null', os.O_WRONLY)
    os.dup2(null, 1); os.dup2(null, 2); os.close(null)
    time.sleep(0.9)
    fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
    os._exit(0)
os.close(write_fd)
if os.read(read_fd, 1) != b'R': os._exit(66)
os.close(read_fd)
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            pid_file = os.path.join(temporary_directory, "pid")
            ready = os.path.join(temporary_directory, "ready")
            late = os.path.join(temporary_directory, "late")
            processes = []
            senders = []
            real_popen = subprocess.Popen

            def launch_and_signal(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)

                def send_during_drain():
                    end = time.monotonic() + 2
                    while time.monotonic() < end and not os.path.exists(ready):
                        time.sleep(0.005)
                    time.sleep(0.05)
                    try:
                        os.kill(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

                sender = threading.Thread(target=send_during_drain)
                sender.start()
                senders.append(sender)
                return process

            with (
                mock.patch.object(
                    command_chain_module.subprocess,
                    "Popen",
                    side_effect=launch_and_signal,
                ),
                self.assertRaisesRegex(CommandChainError, "supervisor failed"),
            ):
                run_process_bounded_output(
                    self._direct_wrapper(script, pid_file, ready, late),
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="local-model",
                    _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                )
            for sender in senders:
                sender.join(timeout=2)
            self.assertEqual(len(processes), 1)
            self.assertEqual(processes[0].returncode, -signal.SIGTERM)
            escaped_pid = int(Path(pid_file).read_text(encoding="ascii"))
            time.sleep(1.0)
            self.assertFalse(os.path.exists(late))
            self.assertFalse(os.path.exists(f"/proc/{escaped_pid}"))

    def test_invalid_direct_frame_still_cleans_known_identity_tree(self) -> None:
        process = mock.Mock()
        process.pid = 123
        process.returncode = process_priority._LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT
        vars(process)["_soc_local_model_direct_status"] = None
        known_tree = {321: "boot-id:456"}
        with (
            mock.patch.object(
                command_chain_module,
                "_process_pipe_holder_identities",
                return_value={},
            ),
            mock.patch.object(
                command_chain_module,
                "_kill_output_process_tree",
                return_value=True,
            ) as kill_tree,
            mock.patch.object(
                command_chain_module,
                "_wait_for_output_process_tree_stop",
                return_value=True,
            ) as wait_tree,
        ):
            self.assertFalse(
                command_chain_module._terminate_bounded_process(
                    process,
                    process_tree=known_tree,
                )
            )
        kill_tree.assert_called_once_with(known_tree)
        wait_tree.assert_called_once_with(known_tree)

    def test_direct_supervisor_reaps_setsid_double_fork_before_return(self) -> None:
        script = r'''
import os, signal, sys, time
child = os.fork()
if child == 0:
    os.setsid()
    grandchild = os.fork()
    if grandchild:
        fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, str(grandchild).encode('ascii'))
        os.close(fd)
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    null = os.open('/dev/null', os.O_WRONLY)
    os.dup2(null, 1); os.dup2(null, 2); os.close(null)
    time.sleep(0.7)
    fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    os._exit(0)
os.waitpid(child, 0)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            pid_file = os.path.join(temporary_directory, "pid")
            marker = os.path.join(temporary_directory, "late")
            before_fds = len(os.listdir("/proc/self/fd"))
            command = self._direct_wrapper(script, pid_file, marker)
            returncode, _, stderr = run_process_bounded_output(
                command,
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="local-model",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
            self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
            escaped_pid = int(Path(pid_file).read_text(encoding="ascii"))
            time.sleep(0.8)
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(os.path.exists(f"/proc/{escaped_pid}"))
            self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_direct_supervisor_timeout_cleans_escaped_descendants(self) -> None:
        script = r'''
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = os.fork()
if child == 0:
    os.setsid(); signal.signal(signal.SIGTERM, signal.SIG_IGN)
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, str(os.getpid()).encode('ascii')); os.close(fd)
    time.sleep(1.5)
    fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
    os._exit(0)
time.sleep(2)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            pid_file = os.path.join(temporary_directory, "pid")
            marker = os.path.join(temporary_directory, "late")
            command = self._direct_wrapper(script, pid_file, marker)
            with self.assertRaisesRegex(CommandChainError, "timed out"):
                run_process_bounded_output(
                    command,
                    timeout_seconds=1,
                    max_output_bytes=4096,
                    env={},
                    label="local-model",
                    _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                )
            time.sleep(0.7)
            self.assertFalse(os.path.exists(marker))
            if os.path.exists(pid_file):
                escaped_pid = int(Path(pid_file).read_text(encoding="ascii"))
                self.assertFalse(os.path.exists(f"/proc/{escaped_pid}"))

    def test_direct_supervisor_signal_and_keyboard_interrupt_cleanup(self) -> None:
        script = r'''
import os, sys, time
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
time.sleep(1)
fd = os.open(sys.argv[2], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            started = os.path.join(temporary_directory, "started")
            late = os.path.join(temporary_directory, "late")
            command = self._direct_wrapper(script, started, late)
            real_popen = subprocess.Popen
            processes: list[subprocess.Popen[bytes]] = []
            timers: list[threading.Timer] = []

            def launch_and_signal(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                processes.append(process)
                timer = threading.Timer(0.2, os.kill, (process.pid, signal.SIGTERM))
                timer.start()
                timers.append(timer)
                return process

            with (
                mock.patch.object(
                    command_chain_module.subprocess,
                    "Popen",
                    side_effect=launch_and_signal,
                ),
                self.assertRaisesRegex(CommandChainError, "supervisor failed"),
            ):
                run_process_bounded_output(
                    command,
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="local-model",
                    _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                )
            for timer in timers:
                timer.join(timeout=1)
            self.assertEqual(len(processes), 1)
            time.sleep(1.1)
            self.assertFalse(os.path.exists(late))

            interrupt_started = os.path.join(temporary_directory, "interrupt-started")
            interrupt_late = os.path.join(temporary_directory, "interrupt-late")
            interrupt_command = self._direct_wrapper(
                script,
                interrupt_started,
                interrupt_late,
            )
            before_fds = len(os.listdir("/proc/self/fd"))
            with (
                mock.patch.object(
                    command_chain_module.selectors,
                    "DefaultSelector",
                    side_effect=KeyboardInterrupt("synthetic interrupt"),
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                run_process_bounded_output(
                    interrupt_command,
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="local-model",
                    _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                )
            time.sleep(1.1)
            self.assertFalse(os.path.exists(interrupt_late))
            self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_internal_wrapper_isolated_mode_blocks_site_startup_hooks(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            site_directory = os.path.join(temporary_directory, "site")
            os.mkdir(site_directory)
            site_marker = os.path.join(temporary_directory, "site-marker")
            pth_marker = os.path.join(temporary_directory, "pth-marker")
            Path(site_directory, "sitecustomize.py").write_text(
                f"open({site_marker!r}, 'x').close()\n",
                encoding="ascii",
            )
            Path(site_directory, "poison.pth").write_text(
                f"import builtins; builtins.open({pth_marker!r}, 'x').close()\n",
                encoding="ascii",
            )
            wrapper = process_priority._local_model_direct_exec_wrapper_command(
                ["/usr/bin/true"]
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = site_directory
            completed = subprocess.run(
                wrapper,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=environment,
                timeout=4,
                check=False,
            )
            self.assertEqual(
                completed.returncode,
                process_priority._LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT,
            )
            self.assertFalse(os.path.exists(site_marker))
            self.assertFalse(os.path.exists(pth_marker))


    def _run_isolated_direct_controller(
        self,
        result_path: str,
        target_script: str,
        *target_arguments: str,
    ) -> dict[str, object]:
        self._direct_wrapper("pass")
        source_root = str(Path(command_chain_module.__file__).resolve().parents[1])
        controller_script = r'''
import json, os, resource, sys
sys.path.insert(0, sys.argv[1])
from speed_of_cinnamon import process_priority
from speed_of_cinnamon.command_chain import (
    _LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
    run_process_bounded_output,
)
names = ('RLIMIT_NOFILE', 'RLIMIT_CPU', 'RLIMIT_AS')
before = {name: resource.getrlimit(getattr(resource, name)) for name in names}
result = {'controller': os.getpid(), 'controller_group': os.getpgrp(), 'before': before}
try:
    command = process_priority.local_model_direct_command([
        os.path.realpath(sys.executable), '-c', sys.argv[3],
        str(os.getpid()), str(os.getpgrp()), *sys.argv[4:]
    ])
    returncode, stdout, stderr = run_process_bounded_output(
        command, timeout_seconds=6, max_output_bytes=131072,
        env={}, label='local-model', _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY
    )
    result.update(returncode=returncode, stdout=stdout.decode('utf-8'), stderr_size=len(stderr))
except BaseException as error:
    result['error'] = type(error).__name__
result['after'] = {name: resource.getrlimit(getattr(resource, name)) for name in names}
with open(sys.argv[2], 'x', encoding='ascii') as handle:
    json.dump(result, handle)
'''
        environment = {
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "TMPDIR": "/tmp",
        }
        controller = subprocess.Popen(
            [
                os.path.realpath(sys.executable),
                "-I",
                "-B",
                "-c",
                controller_script,
                source_root,
                result_path,
                target_script,
                *target_arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=source_root,
            env=environment,
            start_new_session=True,
        )
        try:
            stdout, stderr = controller.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(controller.pid, signal.SIGKILL)
            controller.wait(timeout=2)
            self.fail("isolated direct controller timed out")
        self.assertEqual(
            controller.returncode,
            0,
            (stdout + stderr).decode(errors="replace"),
        )
        self.assertTrue(os.path.exists(result_path))
        result = json.loads(Path(result_path).read_text(encoding="ascii"))
        self.assertNotIn("error", result)
        return result

    def test_target_and_descendant_cannot_signal_controller(self) -> None:
        target_script = r'''
import ctypes, errno, json, os, signal, sys, time
libc = ctypes.CDLL(None, use_errno=True)
controller = int(sys.argv[1]); controller_group = int(sys.argv[2])
def attempt(number, *arguments):
    ctypes.set_errno(0)
    result = libc.syscall(number, *arguments)
    return [result, ctypes.get_errno()]
def attacks():
    calls = {
        'kill-stop': (62, controller, signal.SIGSTOP),
        'kill-kill': (62, controller, signal.SIGKILL),
        'kill-group-stop': (62, -controller_group, signal.SIGSTOP),
        'kill-group-kill': (62, -controller_group, signal.SIGKILL),
        'tkill': (200, controller, signal.SIGKILL),
        'tgkill': (234, controller, controller, signal.SIGKILL),
        'rt-queue': (129, controller, signal.SIGKILL, 0),
        'rt-tg-queue': (297, controller, controller, signal.SIGKILL, 0),
        'rt-queue-x32-real': (0x40000000 | 524, controller, signal.SIGKILL, 0),
        'rt-tg-queue-x32-real': (0x40000000 | 536, controller, controller, signal.SIGKILL, 0),
        'pidfd-open': (434, controller, 0),
    }
    return {
        f'{name}-{abi}': attempt(number | bit, *arguments)
        for name, (number, *arguments) in calls.items()
        for abi, bit in (
            (('exact', 0),)
            if number & 0x40000000
            else (('native', 0), ('x32', 0x40000000))
        )
    }
read_fd, write_fd = os.pipe(); child = os.fork()
if child == 0:
    os.close(read_fd); os.setsid(); child_attacks = attacks()
    grandchild = os.fork()
    if grandchild == 0:
        os.close(write_fd)
        null = os.open('/dev/null', os.O_WRONLY)
        os.dup2(null, 1); os.dup2(null, 2); os.close(null)
        time.sleep(0.8)
        fd = os.open(sys.argv[4], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd); os._exit(0)
    fd = os.open(sys.argv[3], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, str(grandchild).encode('ascii')); os.close(fd)
    os.write(write_fd, json.dumps(child_attacks).encode('ascii'))
    os.close(write_fd); os._exit(0)
os.close(write_fd); child_data = os.read(read_fd, 65536); os.close(read_fd)
os.waitpid(child, 0)
print(json.dumps({'root': attacks(), 'child': json.loads(child_data)}))
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            result_path = os.path.join(temporary_directory, "result")
            pid_file = os.path.join(temporary_directory, "pid")
            marker = os.path.join(temporary_directory, "late")
            result = self._run_isolated_direct_controller(
                result_path,
                target_script,
                pid_file,
                marker,
            )
            self.assertEqual(result["returncode"], 0)
            attacks = json.loads(result["stdout"])
            for origin in ("root", "child"):
                for name, syscall_result in attacks[origin].items():
                    with self.subTest(origin=origin, name=name):
                        self.assertEqual(syscall_result, [-1, errno.EPERM])
            self.assertEqual(result["before"], result["after"])
            escaped_pid = int(Path(pid_file).read_text(encoding="ascii"))
            time.sleep(0.9)
            self.assertFalse(os.path.exists(marker))
            self.assertFalse(os.path.exists(f"/proc/{escaped_pid}"))

    def test_prlimit_cannot_sabotage_supervisor_or_controller(self) -> None:
        target_script = r'''
import ctypes, json, os, resource, sys
libc = ctypes.CDLL(None, use_errno=True)
controller = int(sys.argv[1]); supervisor = os.getppid()
class Limit(ctypes.Structure):
    _fields_ = [('current', ctypes.c_ulong), ('maximum', ctypes.c_ulong)]
def attempt(number, process_id, resource_number, new_limit, old_limit):
    ctypes.set_errno(0)
    result = libc.syscall(number, process_id, resource_number, new_limit, old_limit)
    return [result, ctypes.get_errno()]
old_limit = Limit()
self_result = attempt(302, 0, resource.RLIMIT_NOFILE, 0, ctypes.byref(old_limit))
new_limit = Limit(1, 1)
def attacks():
    return {
        f'{owner}-{name}-{abi}': attempt(
            302 | bit, process_id, resource_number, ctypes.byref(new_limit), 0
        )
        for owner, process_id in (('supervisor', supervisor), ('controller', controller))
        for name, resource_number in (
            ('nofile', resource.RLIMIT_NOFILE),
            ('cpu', resource.RLIMIT_CPU),
            ('as', resource.RLIMIT_AS),
        )
        for abi, bit in (('native', 0), ('x32', 0x40000000))
    }
read_fd, write_fd = os.pipe(); child = os.fork()
if child == 0:
    os.close(read_fd); os.write(write_fd, json.dumps(attacks()).encode('ascii'))
    os.close(write_fd); os._exit(0)
os.close(write_fd); child_data = os.read(read_fd, 65536); os.close(read_fd)
os.waitpid(child, 0)
print(json.dumps({'self': self_result, 'root': attacks(), 'child': json.loads(child_data)}))
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            result = self._run_isolated_direct_controller(
                os.path.join(temporary_directory, "result"),
                target_script,
            )
            self.assertEqual(result["returncode"], 0)
            outcomes = json.loads(result["stdout"])
            self.assertEqual(outcomes["self"][0], 0)
            for origin in ("root", "child"):
                for name, syscall_result in outcomes[origin].items():
                    with self.subTest(origin=origin, name=name):
                        self.assertEqual(syscall_result, [-1, errno.EPERM])
            self.assertEqual(result["before"], result["after"])

    def test_direct_target_token_arguments_are_preserved(self) -> None:
        token = process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN
        runtime, entry = process_priority._scope_exec_wrapper_paths()
        decoy = (runtime, "-I", entry, token)
        script = r'''
import json, os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, b'once'); os.close(fd)
print(json.dumps(sys.argv[2:]))
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            for index, expected in enumerate(((), decoy, decoy + decoy)):
                marker = os.path.join(temporary_directory, f"marker-{index}")
                returncode, stdout, stderr = run_process_bounded_output(
                    self._direct_wrapper(script, marker, *expected),
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="local-model",
                    _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                )
                self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
                self.assertEqual(json.loads(stdout), list(expected))
                self.assertEqual(Path(marker).read_bytes(), b"once")

    def test_structural_direct_argv_without_internal_signal_gets_no_status_fd(self) -> None:
        script = r'''
import os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, b'unexpected'); os.close(fd)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            marker = os.path.join(temporary_directory, "marker")
            command = self._direct_wrapper(script, marker)
            real_popen = subprocess.Popen
            with mock.patch.object(
                command_chain_module.subprocess,
                "Popen",
                wraps=real_popen,
            ) as popen:
                returncode, stdout, stderr = run_process_bounded_output(
                    command,
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="foreign-command",
                )
            self.assertEqual(returncode, 65)
            self.assertEqual(stdout, b"")
            self.assertEqual(stderr, b"")
            self.assertFalse(os.path.exists(marker))
            self.assertNotIn("pass_fds", popen.call_args.kwargs)
            self.assertNotIn(
                process_priority._LOCAL_MODEL_DIRECT_STATUS_FD_ENV,
                popen.call_args.kwargs["env"],
            )

    def test_post_popen_interrupts_have_single_confirmed_cleanup(self) -> None:
        script = r'''
import os, sys, time
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, str(os.getpid()).encode('ascii')); os.close(fd)
time.sleep(5)
'''
        stages = tuple(
            (stage, error_type)
            for stage in (
                "status-binding",
                "identity-capture",
                "identity-binding",
                "selector",
            )
            for error_type in (OSError, KeyboardInterrupt)
        )
        for stage, error_type in stages:
            with self.subTest(stage=stage, error=error_type.__name__), tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
                pid_file = os.path.join(temporary_directory, "pid")
                command = self._direct_wrapper(script, pid_file)
                before_fds = len(os.listdir("/proc/self/fd"))
                real_popen = subprocess.Popen
                real_identity = command_chain_module._clipboard_lock_identity_for_pid
                launched = []
                identity_calls = 0

                def capture_popen(*args, **kwargs):
                    process = real_popen(*args, **kwargs)
                    launched.append(process)
                    return process

                def interrupt_identity_once(process_id):
                    nonlocal identity_calls
                    identity_calls += 1
                    if identity_calls == 1:
                        raise error_type(stage)
                    return real_identity(process_id)

                if stage == "status-binding":
                    interruption = mock.patch.object(
                        command_chain_module,
                        "_bind_local_model_direct_status_descriptor",
                        side_effect=error_type(stage),
                    )
                elif stage == "identity-capture":
                    interruption = mock.patch.object(
                        command_chain_module,
                        "_clipboard_lock_identity_for_pid",
                        side_effect=interrupt_identity_once,
                    )
                elif stage == "identity-binding":
                    interruption = mock.patch.object(
                        command_chain_module,
                        "_bind_bounded_process_identity",
                        side_effect=error_type(stage),
                    )
                else:
                    interruption = mock.patch.object(
                        command_chain_module.selectors,
                        "DefaultSelector",
                        side_effect=error_type(stage),
                    )
                real_terminate = command_chain_module._terminate_bounded_process
                real_unidentified = command_chain_module._terminate_unidentified_bounded_process
                with (
                    interruption,
                    mock.patch.object(
                        command_chain_module.subprocess,
                        "Popen",
                        side_effect=capture_popen,
                    ),
                    mock.patch.object(
                        command_chain_module,
                        "_terminate_bounded_process",
                        wraps=real_terminate,
                    ) as terminate,
                    mock.patch.object(
                        command_chain_module,
                        "_terminate_unidentified_bounded_process",
                        wraps=real_unidentified,
                    ) as terminate_unidentified,
                    self.assertRaises(error_type),
                ):
                    run_process_bounded_output(
                        command,
                        timeout_seconds=4,
                        max_output_bytes=4096,
                        env={},
                        label="local-model",
                        _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                    )
                self.assertEqual(len(launched), 1)
                self.assertEqual(
                    terminate.call_count + terminate_unidentified.call_count,
                    1,
                )
                self.assertIsNotNone(launched[0].poll())
                self.assertTrue(
                    launched[0].stdout is None or launched[0].stdout.closed
                )
                self.assertTrue(
                    launched[0].stderr is None or launched[0].stderr.closed
                )
                if os.path.exists(pid_file):
                    target_pid = int(Path(pid_file).read_text(encoding="ascii"))
                    self.assertFalse(os.path.exists(f"/proc/{target_pid}"))
                self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_popen_pending_sigint_after_real_return_cleans_owned_child(self) -> None:
        script = r'''
import os, sys, time
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, str(os.getpid()).encode('ascii')); os.close(fd)
time.sleep(5)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            pid_file = os.path.join(temporary_directory, "pid")
            command = self._direct_wrapper(script, pid_file)
            real_popen = subprocess.Popen
            launched: list[subprocess.Popen[bytes]] = []
            popen_returned = threading.Event()
            release_worker = threading.Event()
            sender_done = threading.Event()

            def capture_popen(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                launched.append(process)
                popen_returned.set()
                release_worker.wait(timeout=2)
                return process

            def send_after_popen() -> None:
                if popen_returned.wait(timeout=2):
                    os.kill(os.getpid(), signal.SIGINT)
                release_worker.set()
                sender_done.set()

            sender = threading.Thread(
                target=send_after_popen,
                name="test-sigint-sender",
                daemon=False,
            )
            sender.start()
            before_fds = len(os.listdir("/proc/self/fd"))
            try:
                with (
                    mock.patch.object(
                        command_chain_module.subprocess,
                        "Popen",
                        side_effect=capture_popen,
                    ),
                    self.assertRaises(KeyboardInterrupt),
                ):
                    run_process_bounded_output(
                        command,
                        timeout_seconds=4,
                        max_output_bytes=4096,
                        env={},
                        label="local-model",
                        _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                    )
            finally:
                release_worker.set()
                sender.join(timeout=2)

            self.assertTrue(sender_done.is_set())
            self.assertTrue(popen_returned.is_set())
            self.assertEqual(len(launched), 1)
            self.assertIsNotNone(launched[0].poll())
            if os.path.exists(pid_file):
                target_pid = int(Path(pid_file).read_text(encoding="ascii"))
                self.assertFalse(os.path.exists(f"/proc/{target_pid}"))
            self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_popen_thread_start_interrupt_cleans_launch_resources(self) -> None:
        before_fds = len(os.listdir("/proc/self/fd"))
        with (
            mock.patch.object(
                command_chain_module.threading.Thread,
                "start",
                side_effect=KeyboardInterrupt("thread start interrupt"),
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "thread start interrupt"),
        ):
            run_process_bounded_output(
                ["/usr/bin/true"],
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="thread-start",
            )
        self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_popen_thread_start_transition_interrupt_joins_native_worker(self) -> None:
        command = self._direct_wrapper("import time; time.sleep(5)")
        start_joinable_thread = getattr(
            command_chain_module.threading,
            "_start_joinable_thread",
            None,
        )
        contextvars = getattr(command_chain_module.threading, "_contextvars", None)
        limbo_lock = getattr(command_chain_module.threading, "_active_limbo_lock", None)
        limbo = getattr(command_chain_module.threading, "_limbo", None)
        if (
            start_joinable_thread is None
            or contextvars is None
            or limbo_lock is None
            or limbo is None
        ):
            self.skipTest("CPython Thread.start handshake internals unavailable")

        def start_after_native_creation(thread):
            with limbo_lock:
                limbo[thread] = thread
                thread._context = contextvars.Context()
                start_joinable_thread(
                    thread._bootstrap,
                    handle=thread._os_thread_handle,
                    daemon=thread.daemon,
                )
            raise KeyboardInterrupt("start transition interrupt")

        before_threads = len(threading.enumerate())
        with (
            mock.patch.object(
                command_chain_module.threading.Thread,
                "start",
                new=start_after_native_creation,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "start transition interrupt"),
        ):
            run_process_bounded_output(
                command,
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="thread-start-transition",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
        self.assertEqual(len(threading.enumerate()), before_threads)

    def test_popen_wait_interrupt_cleans_published_child(self) -> None:
        command = self._direct_wrapper("import time; time.sleep(5)")
        before_fds = len(os.listdir("/proc/self/fd"))
        real_wait = command_chain_module.threading.Event.wait
        wait_calls = 0

        def interrupt_first_wait(event, timeout=None):
            nonlocal wait_calls
            wait_calls += 1
            if wait_calls == 1:
                raise KeyboardInterrupt("thread wait interrupt")
            return real_wait(event, timeout)

        with (
            mock.patch.object(
                command_chain_module.threading.Event,
                "wait",
                new=interrupt_first_wait,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "thread wait interrupt"),
        ):
            run_process_bounded_output(
                command,
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="thread-wait",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
        self.assertGreaterEqual(wait_calls, 1)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_popen_multiple_interrupts_keep_first_primary(self) -> None:
        command = self._direct_wrapper("import time; time.sleep(5)")
        real_thread_join = command_chain_module.threading.Thread.join
        join_calls = 0
        before_threads = len(threading.enumerate())

        def interrupt_first_join(thread, timeout=None):
            nonlocal join_calls
            join_calls += 1
            if join_calls == 1:
                raise KeyboardInterrupt("first spawn interrupt")
            if join_calls == 2:
                raise KeyboardInterrupt("second spawn interrupt")
            return real_thread_join(thread, timeout)

        with (
            mock.patch.object(
                command_chain_module.threading.Thread,
                "join",
                new=interrupt_first_join,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "first spawn interrupt"),
        ):
            run_process_bounded_output(
                command,
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="multi-interrupt",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
        self.assertGreaterEqual(join_calls, 1)
        self.assertEqual(len(threading.enumerate()), before_threads)

    def test_popen_deadline_is_latched_while_blocking_worker_join(self) -> None:
        real_popen = command_chain_module.subprocess.Popen
        popen_entered = threading.Event()
        release_popen = threading.Event()
        result: list[BaseException] = []

        def blocking_popen(*args, **kwargs):
            popen_entered.set()
            release_popen.wait(timeout=2)
            return real_popen(*args, **kwargs)

        def invoke() -> None:
            try:
                run_process_bounded_output(
                    ["/usr/bin/true"],
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="blocking-popen",
                    deadline=time.monotonic() + 0.05,
                )
            except BaseException as error:
                result.append(error)

        caller = threading.Thread(target=invoke, name="test-blocking-popen", daemon=False)
        before_threads = len(threading.enumerate())
        with mock.patch.object(
            command_chain_module.subprocess,
            "Popen",
            side_effect=blocking_popen,
        ):
            caller.start()
            self.assertTrue(popen_entered.wait(timeout=2))
            time.sleep(0.1)
            self.assertTrue(caller.is_alive())
            release_popen.set()
            caller.join(timeout=2)
        self.assertFalse(caller.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], CommandChainError)
        self.assertEqual(len(threading.enumerate()), before_threads)

    def test_popen_worker_errors_publish_without_pid_guessing(self) -> None:
        before_fds = len(os.listdir("/proc/self/fd"))
        for error_type in (OSError, MemoryError, KeyboardInterrupt, SystemExit):
            with self.subTest(error=error_type.__name__):
                failure = error_type("worker Popen failure")
                with (
                    mock.patch.object(
                        command_chain_module.subprocess,
                        "Popen",
                        side_effect=failure,
                    ),
                    self.assertRaises(error_type) as raised,
                ):
                    run_process_bounded_output(
                        ["/usr/bin/true"],
                        timeout_seconds=4,
                        max_output_bytes=4096,
                        env={},
                        label="worker-error",
                    )
                self.assertIsNot(raised.exception, failure)
                self.assertNotIn("worker Popen failure", str(raised.exception))
                traceback_names: list[str] = []
                traceback = raised.exception.__traceback__
                while traceback is not None:
                    traceback_names.append(traceback.tb_frame.f_code.co_name)
                    traceback = traceback.tb_next
                self.assertNotIn("_worker", traceback_names)
                self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_worker_popen_has_no_preexec_and_preserves_caller_mask(self) -> None:
        caller_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        script = (
            "import signal; mask = signal.pthread_sigmask(signal.SIG_BLOCK, set()); "
            "print(int(signal.SIGINT in mask), int(signal.SIGTERM in mask))"
        )
        real_popen = subprocess.Popen
        with mock.patch.object(
            command_chain_module.subprocess,
            "Popen",
            wraps=real_popen,
        ) as popen:
            returncode, stdout, stderr = run_process_bounded_output(
                [sys.executable, "-c", script],
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="caller-mask",
            )
        self.assertEqual(returncode, 0, stderr.decode(errors="replace"))
        self.assertEqual(
            stdout.decode("ascii").strip(),
            f"{int(signal.SIGINT in caller_mask)} {int(signal.SIGTERM in caller_mask)}",
        )
        self.assertNotIn("preexec_fn", popen.call_args.kwargs)

    def test_hundred_harmless_launches_return_thread_fd_child_baseline(self) -> None:
        def child_snapshot() -> tuple[str, ...]:
            children_file = Path(
                f"/proc/{os.getpid()}/task/{threading.get_native_id()}/children"
            )
            try:
                return tuple(children_file.read_text(encoding="ascii").split())
            except OSError:
                return ()

        before_threads = len(threading.enumerate())
        before_fds = len(os.listdir("/proc/self/fd"))
        before_children = child_snapshot()
        durations: list[float] = []
        for _ in range(100):
            started = time.perf_counter()
            returncode, stdout, stderr = run_process_bounded_output(
                ["/usr/bin/true"],
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="harmless-launch",
            )
            durations.append(time.perf_counter() - started)
            self.assertEqual((returncode, stdout, stderr), (0, b"", b""))

        ordered = sorted(durations)
        p50 = ordered[len(ordered) // 2]
        p95 = ordered[(len(ordered) * 95 + 99) // 100 - 1]
        self.assertGreater(p50, 0.0)
        self.assertGreaterEqual(p95, p50)
        self.assertEqual(len(threading.enumerate()), before_threads)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)
        self.assertEqual(child_snapshot(), before_children)

    def test_popen_oserror_does_not_guess_pid_or_run_process_cleanup(self) -> None:
        before_fds = len(os.listdir("/proc/self/fd"))
        with mock.patch.object(
            command_chain_module,
            "_terminate_bounded_process",
            wraps=command_chain_module._terminate_bounded_process,
        ) as terminate:
            with self.assertRaises(FileNotFoundError):
                run_process_bounded_output(
                    ["/definitely/missing/command-chain-executable"],
                    timeout_seconds=4,
                    max_output_bytes=4096,
                    env={},
                    label="missing-command",
                )
        terminate.assert_not_called()
        self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_cleanup_interrupt_retries_once_and_preserves_original_exception(self) -> None:
        command = self._direct_wrapper("import time; time.sleep(5)")
        before_fds = len(os.listdir("/proc/self/fd"))
        real_terminate = command_chain_module._terminate_bounded_process
        cleanup_calls = 0

        def interrupt_first_cleanup(*args, **kwargs):
            nonlocal cleanup_calls
            cleanup_calls += 1
            if cleanup_calls == 1:
                raise KeyboardInterrupt("cleanup interrupt")
            return real_terminate(*args, **kwargs)

        with (
            mock.patch.object(
                command_chain_module.selectors,
                "DefaultSelector",
                side_effect=KeyboardInterrupt("original interrupt"),
            ),
            mock.patch.object(
                command_chain_module,
                "_terminate_bounded_process",
                side_effect=interrupt_first_cleanup,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "original interrupt"),
        ):
            run_process_bounded_output(
                command,
                timeout_seconds=4,
                max_output_bytes=4096,
                env={},
                label="local-model",
                _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
            )
        self.assertEqual(cleanup_calls, 2)
        self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_pre_popen_handoff_failures_close_all_owned_resources(self) -> None:
        class SyntheticStdin:
            def __init__(self, failure_stage, failure):
                self.failure_stage = failure_stage
                self.failure = failure
                self.closed = False

            def write(self, _payload):
                if self.failure_stage == "write":
                    raise self.failure

            def seek(self, _offset):
                if self.failure_stage == "seek":
                    raise self.failure

            def close(self):
                self.closed = True

        command = self._direct_wrapper("pass")
        for stage in ("pipe", "temporary-file", "write", "seek", "popen"):
            for error_type in (OSError, KeyboardInterrupt):
                with self.subTest(stage=stage, error=error_type.__name__):
                    before_fds = len(os.listdir("/proc/self/fd"))
                    failure = error_type(stage)
                    synthetic_stdin = SyntheticStdin(stage, failure)
                    if stage == "pipe":
                        patcher = mock.patch.object(
                            command_chain_module.os,
                            "pipe2",
                            side_effect=failure,
                        )
                    elif stage == "temporary-file":
                        patcher = mock.patch.object(
                            command_chain_module.tempfile,
                            "TemporaryFile",
                            side_effect=failure,
                        )
                    elif stage in {"write", "seek"}:
                        patcher = mock.patch.object(
                            command_chain_module.tempfile,
                            "TemporaryFile",
                            return_value=synthetic_stdin,
                        )
                    else:
                        patcher = mock.patch.object(
                            command_chain_module.subprocess,
                            "Popen",
                            side_effect=failure,
                        )
                    with patcher, self.assertRaises(BaseException):
                        run_process_bounded_output(
                            command,
                            timeout_seconds=4,
                            max_output_bytes=4096,
                            env={},
                            label="local-model",
                            _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                        )
                    if stage in {"write", "seek"}:
                        self.assertTrue(synthetic_stdin.closed)
                    self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_direct_supervisor_capability_rejects_caller_values(self) -> None:
        script = r'''
import os, sys
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, b'target-ran')
os.close(fd)
'''
        with tempfile.TemporaryDirectory(dir="/tmp") as temporary_directory:
            for forged in (True, False, object()):
                with self.subTest(forged=repr(forged)):
                    marker = os.path.join(
                        temporary_directory,
                        f"marker-{len(os.listdir(temporary_directory))}",
                    )
                    command = self._direct_wrapper(script, marker)
                    launched: list[subprocess.Popen[bytes]] = []
                    real_popen = subprocess.Popen

                    def capture_popen(*args, **kwargs):
                        process = real_popen(*args, **kwargs)
                        launched.append(process)
                        return process

                    with mock.patch.object(
                        command_chain_module.subprocess,
                        "Popen",
                        side_effect=capture_popen,
                    ) as popen:
                        returncode, stdout, stderr = run_process_bounded_output(
                            command,
                            timeout_seconds=4,
                            max_output_bytes=4096,
                            env={},
                            label="foreign-command",
                            _local_model_direct_supervisor=forged,
                        )
                    self.assertEqual(returncode, 65)
                    self.assertEqual(stdout, b"")
                    self.assertEqual(stderr, b"")
                    self.assertFalse(os.path.exists(marker))
                    self.assertEqual(len(launched), 1)
                    self.assertIsNotNone(launched[0].poll())
                    self.assertNotIn("pass_fds", popen.call_args.kwargs)
                    self.assertNotIn(
                        process_priority._LOCAL_MODEL_DIRECT_STATUS_FD_ENV,
                        popen.call_args.kwargs["env"],
                    )

    def test_run_command_chain_passes_direct_capability_once(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(*args, **kwargs):
            calls.append(kwargs)
            if kwargs.get("label") == "local model priority probe":
                return 1, b"", b""
            return 0, b"", b""

        with (
            mock.patch.object(
                command_chain_module,
                "local_model_scope_probe_command",
                return_value=["probe"],
            ),
            mock.patch.object(
                command_chain_module,
                "local_model_direct_command",
                return_value=["direct"],
            ),
            mock.patch.object(
                command_chain_module.shutil,
                "which",
                return_value="/usr/bin/cmd",
            ),
            mock.patch.object(
                command_chain_module,
                "run_process_bounded_output",
                side_effect=fake_run,
            ),
        ):
            run_command_chain(
                [("cmd",)],
                "",
                label="capability-test",
                local_model_priority=True,
            )

        direct_calls = [
            call
            for call in calls
            if call.get("label") == "capability-test"
        ]
        self.assertEqual(len(direct_calls), 1)
        self.assertIs(
            direct_calls[0]["_local_model_direct_supervisor"],
            _LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
        )

    def test_direct_status_read_detaches_before_interrupted_close(self) -> None:
        from types import SimpleNamespace

        status_read, status_write = os.pipe()
        os.close(status_write)
        status_owner = command_chain_module._OwnedLaunchDescriptorPair(
            owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
        )
        status_owner.acquire_one(0, status_read)
        proc = SimpleNamespace(
            _soc_local_model_direct_status_owner=status_owner,
            returncode=65,
        )
        real_close = command_chain_module.os.close
        close_calls = 0

        def close_status_fd(fd: int) -> None:
            nonlocal close_calls
            if fd == status_read:
                close_calls += 1
                if close_calls == 1:
                    raise KeyboardInterrupt("status read cleanup")
            real_close(fd)

        try:
            with (
                mock.patch.object(
                    command_chain_module.os,
                    "close",
                    side_effect=close_status_fd,
                ),
                self.assertRaises(KeyboardInterrupt),
            ):
                command_chain_module._local_model_direct_status(proc)
        finally:
            try:
                real_close(status_read)
            except OSError:
                pass

        self.assertEqual(close_calls, 1)
        self.assertNotIn(
            "_soc_local_model_direct_status_owner",
            vars(proc),
        )

    def test_pipe2_pair_handoff_failures_close_both_parent_fds(self) -> None:
        command = self._direct_wrapper("pass")
        pair_type = command_chain_module._OwnedLaunchDescriptorPair

        for fail_index in (0, 1):
            for error_type in (KeyboardInterrupt, MemoryError):
                with self.subTest(
                    fail_index=fail_index,
                    error=error_type.__name__,
                ):
                    before_fds = len(os.listdir("/proc/self/fd"))

                    def fail_pair_member(*args):
                        index = args[-2]
                        if index == fail_index:
                            raise error_type("pair handoff")

                    with (
                        mock.patch.object(
                            pair_type,
                            "_stage_pair_member",
                            side_effect=fail_pair_member,
                        ),
                        self.assertRaises(error_type),
                    ):
                        run_process_bounded_output(
                            command,
                            timeout_seconds=4,
                            max_output_bytes=4096,
                            env={},
                            label="local-model",
                            _local_model_direct_supervisor=_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY,
                        )
                    self.assertEqual(len(os.listdir("/proc/self/fd")), before_fds)

    def test_owned_fd_close_does_not_close_reused_descriptor(self) -> None:
        owner = command_chain_module._OwnedLaunchDescriptor()
        old_fd = os.open("/dev/null", os.O_RDONLY)
        owner.acquire(old_fd)
        real_close = command_chain_module.os.close
        real_close(old_fd)
        replacement_fd = os.open("/dev/null", os.O_RDONLY)
        self.assertEqual(replacement_fd, old_fd)
        close_calls = 0

        def interrupt_stale_close(fd: int) -> None:
            nonlocal close_calls
            if fd == old_fd:
                close_calls += 1
                raise KeyboardInterrupt("stale descriptor close")
            real_close(fd)

        try:
            with mock.patch.object(
                command_chain_module.os,
                "close",
                side_effect=interrupt_stale_close,
            ):
                cleanup_error = owner.close()
            self.assertIsInstance(cleanup_error, KeyboardInterrupt)
            self.assertEqual(close_calls, 1)
            os.fstat(replacement_fd)
        finally:
            try:
                real_close(replacement_fd)
            except OSError:
                pass

    def test_owned_fd_close_surfaces_post_syscall_error_without_retry(self) -> None:
        owner = command_chain_module._OwnedLaunchDescriptor()
        old_fd = os.open("/dev/null", os.O_RDONLY)
        owner.acquire(old_fd)
        real_close = command_chain_module.os.close
        replacement_fd: int | None = None
        close_calls = 0

        def close_then_raise(fd: int) -> None:
            nonlocal close_calls, replacement_fd
            close_calls += 1
            real_close(fd)
            replacement_fd = os.open("/dev/null", os.O_RDONLY)
            self.assertEqual(replacement_fd, fd)
            raise OSError("close completed before reporting failure")

        try:
            with mock.patch.object(
                command_chain_module.os,
                "close",
                side_effect=close_then_raise,
            ):
                cleanup_error = owner.close()
            self.assertIsInstance(cleanup_error, OSError)
            self.assertEqual(close_calls, 1)
            self.assertEqual(owner.value, -1)
            self.assertIsNotNone(replacement_fd)
            os.fstat(replacement_fd)
        finally:
            if replacement_fd is not None:
                try:
                    real_close(replacement_fd)
                except OSError:
                    pass

    def test_trusted_stream_and_tempfile_close_under_mask_and_detach(self) -> None:
        from types import SimpleNamespace

        class MaskedStream:
            def __init__(self) -> None:
                self.close_calls = 0
                self.closed_under_mask = False

            def close(self) -> None:
                self.close_calls += 1
                current_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
                self.closed_under_mask = signal.SIGINT in current_mask

        stdout = MaskedStream()
        stderr = MaskedStream()
        proc = SimpleNamespace(stdout=stdout, stderr=stderr)
        self.assertEqual(
            command_chain_module._close_bounded_process_streams(proc),
            [],
        )
        self.assertEqual(stdout.close_calls, 1)
        self.assertEqual(stderr.close_calls, 1)
        self.assertTrue(stdout.closed_under_mask)
        self.assertTrue(stderr.closed_under_mask)
        self.assertIsNone(proc.stdout)
        self.assertIsNone(proc.stderr)

        resources = command_chain_module._BoundedProcessLaunchResources({})
        stdin_file = MaskedStream()
        resources.stdin_file = stdin_file
        self.assertEqual(resources.close(), [])
        self.assertEqual(stdin_file.close_calls, 1)
        self.assertTrue(stdin_file.closed_under_mask)
        self.assertIsNone(resources.stdin_file)

    def test_fd_signal_mask_setup_failure_does_not_enter_launch_body(self) -> None:
        body_entered = False
        with mock.patch.object(
            command_chain_module.signal,
            "pthread_sigmask",
            side_effect=MemoryError("signal mask setup"),
        ), mock.patch.object(command_chain_module.os, "pipe2") as pipe2:
            with self.assertRaises(MemoryError):
                with command_chain_module._bounded_process_launch_resources(
                    direct_supervisor=True,
                    environment={},
                    deadline=command_chain_module.time.monotonic() + 1,
                ):
                    body_entered = True
        self.assertFalse(body_entered)
        pipe2.assert_not_called()

    def test_fd_signal_mask_restore_preserves_primary_exception_and_mask(self) -> None:
        previous_mask = object()
        calls: list[tuple[object, object]] = []

        def fake_pthread_sigmask(how, mask):
            calls.append((how, mask))
            if how == command_chain_module.signal.SIG_BLOCK:
                return previous_mask
            raise KeyboardInterrupt("mask restore")

        primary = ValueError("body failure")
        with mock.patch.object(
            command_chain_module.signal,
            "pthread_sigmask",
            side_effect=fake_pthread_sigmask,
        ):
            with self.assertRaises(ValueError) as raised:
                with command_chain_module._bounded_fd_critical_section():
                    raise primary

        self.assertIs(raised.exception, primary)
        self.assertEqual(calls[-1][0], command_chain_module.signal.SIG_SETMASK)
        self.assertIs(calls[-1][1], previous_mask)
        self.assertTrue(
            any("signal-mask restore" in note for note in getattr(primary, "__notes__", ()))
        )

    def test_fd_signal_mask_restore_without_primary_exception_is_raised(self) -> None:
        previous_mask = object()
        calls: list[tuple[object, object]] = []

        def fake_pthread_sigmask(how, mask):
            calls.append((how, mask))
            if how == command_chain_module.signal.SIG_BLOCK:
                return previous_mask
            raise KeyboardInterrupt("mask restore")

        with mock.patch.object(
            command_chain_module.signal,
            "pthread_sigmask",
            side_effect=fake_pthread_sigmask,
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "mask restore"):
                with command_chain_module._bounded_fd_critical_section():
                    pass

        self.assertEqual(calls[-1][0], command_chain_module.signal.SIG_SETMASK)
        self.assertIs(calls[-1][1], previous_mask)

    def test_final_cleanup_continues_after_signal_mask_restore_errors(self) -> None:
        from types import SimpleNamespace

        class Stream:
            def __init__(self) -> None:
                self.calls = 0

            def close(self) -> None:
                self.calls += 1

        class Selector:
            def __init__(self, streams) -> None:
                self.streams = streams
                self.close_calls = 0

            def get_map(self):
                return {
                    index: SimpleNamespace(fileobj=stream)
                    for index, stream in enumerate(self.streams)
                }

            def unregister(self, _stream) -> None:
                pass

            def close(self) -> None:
                self.close_calls += 1

        stdout = Stream()
        stderr = Stream()
        selector = Selector((stdout, stderr))

        def fake_pthread_sigmask(how, _mask):
            if how == command_chain_module.signal.SIG_BLOCK:
                return set()
            raise KeyboardInterrupt("mask restore")

        with mock.patch.object(
            command_chain_module.signal,
            "pthread_sigmask",
            side_effect=fake_pthread_sigmask,
        ):
            cleanup_errors = command_chain_module._close_bounded_process_final_resources(
                None,
                selector,
            )

        self.assertEqual(len(cleanup_errors), 2)
        self.assertTrue(all(isinstance(error, KeyboardInterrupt) for error in cleanup_errors))
        self.assertEqual(stdout.calls, 1)
        self.assertEqual(stderr.calls, 1)
        self.assertEqual(selector.close_calls, 1)

    def test_status_owner_closes_after_reader_transfer_baseexception(self) -> None:
        from types import SimpleNamespace

        pair_type = command_chain_module._OwnedLaunchDescriptorPair
        for error_type in (MemoryError, KeyboardInterrupt):
            with self.subTest(error=error_type.__name__):
                status_read, status_write = os.pipe()
                os.close(status_write)
                status_owner = pair_type(
                    owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
                )
                status_owner.acquire_one(0, status_read)
                proc = SimpleNamespace(
                    _soc_local_model_direct_status_owner=status_owner,
                    returncode=0,
                )
                with (
                    mock.patch.object(
                        pair_type,
                        "_value",
                        side_effect=error_type("status owner transfer"),
                    ),
                    self.assertRaises(error_type),
                ):
                    command_chain_module._local_model_direct_status(proc)
                self.assertEqual(status_owner._values[0], -1)
                replacement_fd = os.open("/dev/null", os.O_RDONLY)
                try:
                    self.assertEqual(replacement_fd, status_read)
                    os.fstat(replacement_fd)
                finally:
                    os.close(replacement_fd)

    def test_status_owner_setter_failures_keep_shared_owner(self) -> None:
        from types import SimpleNamespace

        owner_type = command_chain_module._OwnedLaunchDescriptorPair
        owner_attribute = (
            command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE
        )

        class FailingProc(SimpleNamespace):
            def __init__(self, fail_after: bool) -> None:
                super().__init__()
                self.fail_after = fail_after
                self.failed = False

            def __setattr__(self, name, value) -> None:
                if name == owner_attribute and not self.failed:
                    if self.fail_after:
                        object.__setattr__(self, name, value)
                        self.failed = True
                        raise KeyboardInterrupt("status owner setter")
                    self.failed = True
                    raise MemoryError("status owner setter")
                object.__setattr__(self, name, value)

        for fail_after in (False, True):
            with self.subTest(fail_after=fail_after):
                status_read, status_write = os.pipe()
                os.close(status_write)
                owner = owner_type(
                    owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
                )
                owner.acquire_one(0, status_read)
                proc = FailingProc(fail_after)
                with self.assertRaises(
                    KeyboardInterrupt if fail_after else MemoryError
                ):
                    command_chain_module._bind_local_model_direct_status_descriptor(
                        proc,
                        owner,
                    )
                self.assertIs(
                    vars(proc).get(owner_attribute),
                    owner if fail_after else None,
                )
                self.assertEqual(owner.close(), [])
                self.assertEqual(owner._values[0], -1)
                replacement_fd = os.open("/dev/null", os.O_RDONLY)
                try:
                    self.assertEqual(replacement_fd, status_read)
                    os.fstat(replacement_fd)
                finally:
                    os.close(replacement_fd)

    def test_status_reader_rejects_numeric_structural_and_wrong_pair_owner(self) -> None:
        from types import SimpleNamespace

        owner_attribute = (
            command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE
        )

        class StructuralOwner:
            pass

        class PairSubclass(command_chain_module._OwnedLaunchDescriptorPair):
            pass

        candidates = (
            0,
            StructuralOwner(),
            command_chain_module._OwnedLaunchDescriptorPair(),
            PairSubclass(
                owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
            ),
        )
        for candidate in candidates:
            with self.subTest(candidate=type(candidate).__name__):
                status_read, status_write = os.pipe()
                os.close(status_write)
                proc = SimpleNamespace(
                    **{owner_attribute: candidate},
                    returncode=0,
                )
                result = command_chain_module._local_model_direct_status(proc)
                self.assertIsNone(result)
                os.fstat(status_read)
                os.close(status_read)
                replacement_fd = os.open("/dev/null", os.O_RDONLY)
                try:
                    self.assertEqual(replacement_fd, status_read)
                    os.fstat(replacement_fd)
                finally:
                    os.close(replacement_fd)

    def test_status_read_primary_exception_survives_owner_close_failure(self) -> None:
        from types import SimpleNamespace

        status_read, status_write = os.pipe()
        os.close(status_write)
        status_owner = command_chain_module._OwnedLaunchDescriptorPair(
            owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
        )
        status_owner.acquire_one(0, status_read)
        proc = SimpleNamespace(
            _soc_local_model_direct_status_owner=status_owner,
            returncode=0,
        )
        close_calls = 0
        real_close = command_chain_module.os.close

        def fail_close(fd: int) -> None:
            nonlocal close_calls
            if fd == status_read:
                close_calls += 1
                raise KeyboardInterrupt("status close")
            real_close(fd)

        try:
            with mock.patch.object(
                command_chain_module.os,
                "read",
                side_effect=ValueError("status read"),
            ), mock.patch.object(
                command_chain_module.os,
                "close",
                side_effect=fail_close,
            ):
                with self.assertRaises(ValueError) as raised:
                    command_chain_module._local_model_direct_status(proc)
        finally:
            try:
                real_close(status_read)
            except OSError:
                pass

        self.assertEqual(close_calls, 1)
        self.assertTrue(
            any("status FD cleanup" in note for note in raised.exception.__notes__)
        )

    def test_status_transfer_after_setattr_keeps_target_owner(self) -> None:
        status_read, status_write = os.pipe()
        os.close(status_write)

        class Target:
            def __setattr__(self, name, value) -> None:
                object.__setattr__(self, name, value)
                if name == "status_fd":
                    raise MemoryError("target assignment")

        owner = command_chain_module._OwnedLaunchDescriptor()
        owner.acquire(status_read)
        target = Target()
        with self.assertRaises(MemoryError):
            owner.transfer_to(target, "status_fd")
        self.assertEqual(owner.value, -1)
        try:
            self.assertEqual(target.status_fd, status_read)
            os.fstat(target.status_fd)
        finally:
            os.close(target.status_fd)

    def test_final_cleanup_reports_real_pipe_close_failure_and_continues(self) -> None:
        from types import SimpleNamespace

        class Stream:
            def __init__(self) -> None:
                self.calls = 0

            def close(self) -> None:
                self.calls += 1

        class Selector:
            def __init__(self) -> None:
                self.close_calls = 0

            def get_map(self):
                return {}

            def close(self) -> None:
                self.close_calls += 1

        status_read, status_write = os.pipe()
        os.close(status_write)
        status_owner = command_chain_module._OwnedLaunchDescriptorPair(
            owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
        )
        status_owner.acquire_one(0, status_read)
        stdout = Stream()
        stderr = Stream()
        selector = Selector()
        proc = SimpleNamespace(
            _soc_local_model_direct_status_owner=status_owner,
            stdout=stdout,
            stderr=stderr,
        )
        real_close = command_chain_module.os.close
        pipe_close_calls = 0

        def fail_pipe_close(fd: int) -> None:
            nonlocal pipe_close_calls
            if fd == status_read:
                pipe_close_calls += 1
                raise OSError("pipe FD close failure")
            real_close(fd)

        try:
            with mock.patch.object(
                command_chain_module.os,
                "close",
                side_effect=fail_pipe_close,
            ):
                cleanup_errors = command_chain_module._close_bounded_process_final_resources(
                    proc,
                    selector,
                )
        finally:
            try:
                real_close(status_read)
            except OSError:
                pass

        self.assertEqual(pipe_close_calls, 1)
        self.assertTrue(
            any(
                isinstance(error, OSError) and "pipe FD close failure" in str(error)
                for error in cleanup_errors
            )
        )
        self.assertEqual(stdout.calls, 1)
        self.assertEqual(stderr.calls, 1)
        self.assertEqual(selector.close_calls, 1)

    def test_final_cleanup_closes_each_step_once_and_continues(self) -> None:
        from types import SimpleNamespace

        class Stream:
            def __init__(self) -> None:
                self.calls = 0
                self.closed = False

            def close(self) -> None:
                self.calls += 1
                if self.calls == 1:
                    raise KeyboardInterrupt("stream cleanup")
                self.closed = True

        class Selector:
            def __init__(self) -> None:
                self.close_calls = 0
                self.closed = False

            def get_map(self):
                return {}

            def close(self) -> None:
                self.close_calls += 1
                if self.close_calls == 1:
                    raise KeyboardInterrupt("selector cleanup")
                self.closed = True

        status_read, status_write = os.pipe()
        os.close(status_write)
        status_owner = command_chain_module._OwnedLaunchDescriptorPair(
            owner_token=command_chain_module._LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
        )
        status_owner.acquire_one(0, status_read)
        stdout = Stream()
        stderr = Stream()
        selector = Selector()
        proc = SimpleNamespace(
            _soc_local_model_direct_status_owner=status_owner,
            stdout=stdout,
            stderr=stderr,
        )
        real_close = command_chain_module.os.close
        status_close_calls = 0

        def close_status_fd(fd: int) -> None:
            nonlocal status_close_calls
            if fd == status_read:
                status_close_calls += 1
                if status_close_calls == 1:
                    raise KeyboardInterrupt("status cleanup")
            real_close(fd)

        try:
            with mock.patch.object(
                command_chain_module.os,
                "close",
                side_effect=close_status_fd,
            ):
                cleanup_errors = command_chain_module._close_bounded_process_final_resources(
                    proc,
                    selector,
                )
        finally:
            try:
                real_close(status_read)
            except OSError:
                pass

        self.assertEqual(len(cleanup_errors), 4)
        self.assertTrue(all(isinstance(error, KeyboardInterrupt) for error in cleanup_errors))
        self.assertEqual(status_close_calls, 1)
        self.assertEqual(stdout.calls, 1)
        self.assertEqual(stderr.calls, 1)
        self.assertEqual(selector.close_calls, 1)
        self.assertFalse(stdout.closed)
        self.assertFalse(stderr.closed)
        self.assertFalse(selector.closed)
        self.assertNotIn(
            "_soc_local_model_direct_status_owner",
            vars(proc),
        )

    def test_final_cleanup_does_not_retry_successful_steps(self) -> None:
        from types import SimpleNamespace

        class Stream:
            def __init__(self) -> None:
                self.calls = 0

            def close(self) -> None:
                self.calls += 1

        class Selector:
            def __init__(self) -> None:
                self.close_calls = 0

            def get_map(self):
                return {}

            def close(self) -> None:
                self.close_calls += 1

        stdout = Stream()
        stderr = Stream()
        selector = Selector()
        proc = SimpleNamespace(stdout=stdout, stderr=stderr)
        cleanup_errors = command_chain_module._close_bounded_process_final_resources(
            proc,
            selector,
        )

        self.assertEqual(cleanup_errors, [])
        self.assertEqual(stdout.calls, 1)
        self.assertEqual(stderr.calls, 1)
        self.assertEqual(selector.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
