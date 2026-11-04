from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLET = REPO_ROOT / "scripts" / "applet-crash-safety.sh"
SUPERVISOR = REPO_ROOT / "scripts" / "gui-session-supervisor.py"
NONCE = "ab" * 32


class _BinaryStderr:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def write(self, data: str) -> int:
        return len(data)

    def flush(self) -> None:
        return None


class GuiSessionSupervisorTest(unittest.TestCase):
    def _module(self):
        spec = importlib.util.spec_from_file_location(
            "gui_session_supervisor_tail", SUPERVISOR
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_install_guard_status_cli_is_bounded_exact_and_secret_free(self) -> None:
        valid = "ROOT_FAIL ROOT_META\n"
        cases = (
            (valid, valid),
            ("", "INSTALL_GUARD_FAIL OUTER_EMPTY\n"),
            (
                "ROOT_FAIL ROOT_META\nROOT_FAIL ROOT_META\n",
                "INSTALL_GUARD_FAIL OUTER_DUPLICATE\n",
            ),
            (
                "ROOT_FAIL ROOT_META\nCHAIN_FAIL OWNER\n",
                "INSTALL_GUARD_FAIL OUTER_MULTIPLE\n",
            ),
            (
                "run-isolated-soc-gui: controller.stderr tail:\n" + valid,
                "INSTALL_GUARD_FAIL OUTER_MULTIPLE\n",
            ),
            ("ROOT_FAIL SECRET\n", "INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"),
            ("ROOT_FAIL ROOT_META\x01\n", "INSTALL_GUARD_FAIL OUTER_CONTROL\n"),
            ("ROOT_FAIL ROOT_META\xff\n", "INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"),
            ("INSTALL_GUARD_FAIL OUTER_HELPER\n", "INSTALL_GUARD_FAIL OUTER_HELPER\n"),
        )
        for content, expected in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory(
                    prefix="soc-status.", dir="/tmp"
                ) as temporary:
                    path = Path(temporary) / "controller.stderr"
                    path.write_bytes(content.encode("latin1"))
                    path.chmod(0o600)
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SUPERVISOR),
                            "--print-install-guard-status",
                            str(path),
                        ],
                        capture_output=True,
                        text=False,
                        check=False,
                    )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, expected.encode())
                self.assertNotIn(b"SECRET", result.stderr)

    def test_install_guard_status_cli_open_failure_is_fixed(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--print-install-guard-status",
                "/tmp/no-such-controller.stderr",
            ],
            capture_output=True,
            text=False,
            check=False,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n")

    def test_install_guard_result_cli_is_exact_bounded_and_identity_safe(self) -> None:
        cases = (
            (b"OK\n", b"OK\n"),
            (b"ROOT_FAIL ROOT_META\n", b"ROOT_FAIL ROOT_META\n"),
            (b"CHAIN_FAIL OWNER\n", b"CHAIN_FAIL OWNER\n"),
            (b"INSTALL_GUARD_FAIL CORE_FRAME\n", b"INSTALL_GUARD_FAIL CORE_FRAME\n"),
            *(
                (
                    f"INSTALL_GUARD_FAIL CORE_PARSER_{code}\n".encode("ascii"),
                    f"INSTALL_GUARD_FAIL CORE_PARSER_{code}\n".encode("ascii"),
                )
                for code in ("EXIT", "EMPTY", "MULTIPLE", "CONTROL", "UNKNOWN")
            ),
            (b"", b"INSTALL_GUARD_FAIL CORE_RESULT_EMPTY\n"),
            (b"x", b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n"),
            (b"\n", b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n"),
            (b"UNKNOWN\n", b"INSTALL_GUARD_FAIL CORE_RESULT_CODE\n"),
            (b"OK\nSECOND\n", b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n"),
            (b"OK\x01\n", b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n"),
        )
        for content, expected in cases:
            with self.subTest(content=content):
                with tempfile.TemporaryDirectory(
                    prefix="soc-result.", dir="/tmp"
                ) as temporary:
                    path = Path(temporary) / "install-result"
                    path.write_bytes(content)
                    path.chmod(0o600)
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SUPERVISOR),
                            "--print-install-guard-result",
                            str(path),
                        ],
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, expected)
                    self.assertEqual(result.stderr, b"")
                    self.assertFalse(path.exists())

    def test_install_result_writer_allowlist_has_no_reader_drift(self) -> None:
        source = APPLET.read_text(encoding="utf-8")
        writer_start = source.index("\twrite_install_result() {")
        writer_end = source.index("\n\tstatus_cleanup()", writer_start)
        writer = source[writer_start:writer_end]
        case_start = writer.index('case "${result_frame}" in')
        case_end = writer.index("\n\t\t\t;;", case_start)
        patterns = writer[case_start:case_end].split(" in", 1)[1]
        patterns = patterns.replace("\\\n", "").replace("\n", "").removesuffix(")")
        writer_lines = frozenset(
            token.replace("\\ ", " ").strip()
            for token in patterns.split("|")
            if token.strip()
        )
        self.assertTrue(writer_lines)
        module = self._module()
        reader_lines = frozenset(
            line.decode("ascii") for line in module.INSTALL_GUARD_RESULT_LINES
        )
        self.assertEqual(writer_lines - reader_lines, frozenset())
        self.assertEqual(reader_lines - writer_lines, frozenset())
        for line in sorted(writer_lines):
            with self.subTest(line=line):
                with tempfile.TemporaryDirectory(
                    prefix="soc-writer-drift.", dir="/tmp"
                ) as temporary:
                    path = Path(temporary) / "install-result"
                    path.write_bytes((line + "\n").encode("ascii"))
                    path.chmod(0o600)
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SUPERVISOR),
                            "--print-install-guard-result",
                            str(path),
                        ],
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.stdout, (line + "\n").encode("ascii"))

    def test_install_guard_result_rejects_symlink_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-result-attack.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(b"OK\n")
            target.chmod(0o600)
            symlink = root / "install-result-symlink"
            symlink.symlink_to(target)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--print-install-guard-result",
                    str(symlink),
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.stdout, b"INSTALL_GUARD_FAIL CORE_RESULT_META\n")
            self.assertTrue(symlink.is_symlink())

            hardlink = root / "install-result-hardlink"
            hardlink.hardlink_to(target)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--print-install-guard-result",
                    str(hardlink),
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.stdout, b"INSTALL_GUARD_FAIL CORE_RESULT_META\n")
            self.assertTrue(target.exists())

    def test_install_guard_result_reader_distinguishes_missing_and_metadata_failures(
        self,
    ) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--print-install-guard-result",
                "/tmp/no-such-install-result",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.stdout, b"INSTALL_GUARD_FAIL CORE_RESULT_MISSING\n")
        with tempfile.TemporaryDirectory(
            prefix="soc-result-errors.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_bytes(b"OK\n")
            target.chmod(0o600)
            loop = root / "loop"
            loop.symlink_to(loop)
            result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--print-install-guard-result",
                    str(loop),
                ],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.stdout, b"INSTALL_GUARD_FAIL CORE_RESULT_META\n")
            denied = root / "denied"
            denied.mkdir(mode=0o700)
            denied.chmod(0o000)
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(SUPERVISOR),
                        "--print-install-guard-result",
                        str(denied / "result"),
                    ],
                    capture_output=True,
                    check=False,
                )
            finally:
                denied.chmod(0o700)
            self.assertEqual(result.stdout, b"INSTALL_GUARD_FAIL CORE_RESULT_META\n")

    def test_real_bwrap_install_result_reader_ignores_controller_stderr_noise(
        self,
    ) -> None:
        bwrap = Path("/usr/bin/bwrap")
        if not bwrap.is_file():
            self.skipTest("bubblewrap is unavailable")
        payload = r"""
import os, subprocess, sys
result = "/run/soc-controller/install-result"
fd = os.open(result, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
try:
    os.write(fd, b"INSTALL_GUARD_FAIL CORE_PARSER_EMPTY\n")
finally:
    os.close(fd)
with open("/run/soc-work/controller.stderr", "wb") as stream:
    stream.write(b"untrusted diagnostic noise\n")
checked = subprocess.run(
    [sys.executable, sys.argv[1], "--print-install-guard-result", result],
    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    check=False,
)
if checked.returncode != 0 or checked.stderr or os.path.lexists(result):
    raise SystemExit(3)
sys.stdout.buffer.write(checked.stdout)
"""
        result = subprocess.run(
            [
                str(bwrap),
                "--unshare-all",
                "--new-session",
                "--die-with-parent",
                "--clearenv",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/run",
                "--dir",
                "/run/soc-controller",
                "--dir",
                "/run/soc-work",
                "--chdir",
                str(REPO_ROOT),
                "--setenv",
                "PATH",
                "/usr/bin:/bin",
                "--setenv",
                "LC_ALL",
                "C",
                "--",
                "/usr/bin/python3.14",
                "-I",
                "-S",
                "-c",
                payload,
                str(SUPERVISOR),
            ],
            capture_output=True,
            check=False,
            timeout=8,
        )
        if result.returncode != 0 and any(
            marker in result.stderr
            for marker in (
                b"Operation not permitted",
                b"Permission denied",
                b"Creating new namespace failed",
            )
        ):
            self.skipTest("host blocks required bubblewrap namespace capability")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"INSTALL_GUARD_FAIL CORE_PARSER_EMPTY\n")
        self.assertEqual(result.stderr, b"")

    def _run_install_outer(
        self, command: list[str], payload: bytes = b"outer-payload\n"
    ) -> subprocess.CompletedProcess[bytes]:
        if len(command) >= 3 and command[1] == "-c" and payload == b"outer-payload\n":
            code = command[2]
            if len(command) > 3:
                code = code.replace("sys.argv[1]", repr(command[3]))
            payload = code.encode("utf-8")
            command = [
                "/usr/bin/env",
                "-i",
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "LC_ALL=C",
                "PYTHONDONTWRITEBYTECODE=1",
                "/usr/bin/python3.14",
                "-I",
                "-S",
                "-",
            ]
        return subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--run-install-guard-outer",
                "--timeout",
                "1",
                "--term-grace",
                "0.1",
                "--",
                *command,
            ],
            input=payload,
            capture_output=True,
            check=False,
        )

    def _assert_runner_children_empty(self) -> None:
        children = Path(f"/proc/{os.getpid()}/task/{os.getpid()}/children").read_bytes()
        self.assertEqual(children.strip(), b"")
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        self.assertEqual(pid, 0)

    def test_install_outer_runner_success_passes_payload_and_stdout(self) -> None:
        result = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)",
            ]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")

    def test_runner_builder_preserves_exact_adapter_stdin_and_outer_arguments(
        self,
    ) -> None:
        module = self._module()
        suffix = [
            "30",
            "5",
            "ab" * 32,
            "install-guard-probe",
            "--",
            str(SUPERVISOR),
            "--",
            "fixed-child",
        ]
        adapter_command = [
            "/usr/bin/env",
            "-i",
            "PATH=" + module.RUNNER_TRUSTED_PATH,
            "LC_ALL=C",
            "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3.14",
            "-I",
            "-S",
            "-",
            *suffix,
        ]
        built = module._runner_bwrap_command(adapter_command)
        contained = built[built.index("--") + 1 :]
        self.assertEqual(
            contained[:9],
            [
                "/usr/bin/env",
                "-i",
                "PATH=" + module.RUNNER_TRUSTED_PATH,
                "LC_ALL=C",
                "PYTHONDONTWRITEBYTECODE=1",
                "/usr/bin/python3.14",
                "-I",
                "-S",
                "-",
            ],
        )
        self.assertEqual(contained.count("-"), 1)
        self.assertEqual(contained[9:], suffix)

    def test_runner_builder_home_setup_precedes_xvfb_bind(self) -> None:
        module = self._module()
        command = [
            "/usr/bin/env",
            "-i",
            "PATH=" + module.RUNNER_TRUSTED_PATH,
            "LC_ALL=C",
            "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3.14",
            "-I",
            "-S",
            "-",
            "30",
            "5",
            "ab" * 32,
            "install-guard-probe",
            "--",
            str(SUPERVISOR),
            "--",
            "fixed-child",
        ]
        built = module._runner_bwrap_command(command, 9)
        self.assertIn(
            ["--info-fd", "9"],
            [built[index : index + 2] for index in range(len(built) - 1)],
        )
        self.assertIn(
            ["--tmpfs", "/home"],
            [built[index : index + 2] for index in range(len(built) - 1)],
        )
        self.assertNotIn(
            ["--dir", "/home"],
            [built[index : index + 2] for index in range(len(built) - 1)],
        )
        xfvb_index = built.index(module.RUNNER_XVFB)
        for directory in (
            "/home/teladi",
            "/home/teladi/.local",
            "/home/teladi/.local/bin",
        ):
            self.assertLess(built.index(directory), xfvb_index)

    def test_runner_info_is_documented_json_and_bounded(self) -> None:
        module = self._module()
        valid = (
            b'{\n  "child-pid": 42,\n  "cgroup-namespace": 2,\n'
            b'  "ipc-namespace": 3,\n  "mnt-namespace": 4,\n'
            b'  "net-namespace": 5,\n  "pid-namespace": 6,\n'
            b'  "uts-namespace": 7\n}\n'
        )
        cases = (
            (valid, True),
            (b"", False),
            (valid + valid, False),
            (b"x" * (module.RUNNER_INFO_MAX_BYTES + 1), False),
            (valid[:-2], False),
            (valid.replace(b'"child-pid": 42', b'"child-pid": true'), False),
            (valid.replace(b'"child-pid": 42', b'"child-pid": 0'), False),
            (
                valid.replace(b'"child-pid": 42', b'"child-pid": 18446744073709551616'),
                False,
            ),
            (
                valid.replace(
                    b'  "uts-namespace": 7', b'  "uts-namespace": 7,\n  "unknown": 8'
                ),
                False,
            ),
            (
                valid.replace(
                    b'  "uts-namespace": 7', b'  "uts-namespace": 7,\n  "child-pid": 43'
                ),
                False,
            ),
            (valid.replace(b'"child-pid": 42', b'"child-pid": "\\u0001"'), False),
            (b'{"child-pid":' + b"{" * 1500 + b"0" + b"}" * 1500 + b"}", False),
        )
        for payload, expected in cases:
            with self.subTest(payload=payload):
                read_fd, write_fd = os.pipe()
                try:
                    os.write(write_fd, payload)
                    os.close(write_fd)
                    write_fd = -1
                    deadline = time.monotonic() + 0.5
                    info = module._runner_read_info(read_fd, deadline)
                    self.assertEqual(
                        info is not None,
                        expected,
                    )
                finally:
                    os.close(read_fd)
                    if write_fd >= 0:
                        os.close(write_fd)

    def test_deep_info_json_recursion_is_exact_launch_failure(self) -> None:
        module = self._module()
        payload = b'{"a":' * 680 + b"0" + b"}" * 680
        self.assertLessEqual(len(payload), module.RUNNER_INFO_MAX_BYTES)

        def recursive_pairs(pairs):
            def descend(depth):
                if depth == 0:
                    return pairs
                return descend(depth - 1)

            return descend(2000)

        with self.assertRaises(RecursionError):
            json.loads(payload.decode("ascii"), object_pairs_hook=recursive_pairs)
        with mock.patch.object(module, "_runner_unique_pairs", recursive_pairs):
            self.assertIsNone(module._runner_info_object(payload))

        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, payload)
            os.close(write_fd)
            write_fd = -1
            info = module._runner_read_info(read_fd, time.monotonic() + 0.5)
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        self.assertIsNone(info)

        process = mock.Mock(pid=4242, stderr=mock.Mock(), stdin=None)
        process.poll.return_value = 0
        captured = _BinaryStderr()
        with mock.patch.object(
            module, "_runner_read_stdin", return_value=b""
        ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
            module, "_runner_has_no_children", return_value=True
        ), mock.patch.object(
            module,
            "_runner_bwrap_command",
            return_value=[module.RUNNER_BWRAP, "--info-fd"],
        ), mock.patch.object(
            module.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
        ), mock.patch.object(
            module.os, "pidfd_open", return_value=3
        ), mock.patch.object(
            module, "_runner_read_info", return_value=info
        ), mock.patch.object(
            module, "_runner_pump"
        ) as pump, mock.patch.object(
            module, "_runner_reap_children"
        ), mock.patch.object(
            module, "_runner_collect", return_value=True
        ), mock.patch.object(
            module, "_runner_live_handles", return_value=(False, False)
        ), mock.patch.object(
            module, "_runner_cleanup", return_value=True
        ), mock.patch.object(
            module.os, "close"
        ), contextlib.redirect_stderr(
            captured
        ):
            status = module._run_install_guard_outer(
                [
                    "--timeout",
                    "1",
                    "--term-grace",
                    "0.1",
                    "--",
                    "ignored",
                ]
            )
        self.assertEqual(status, 2)
        self.assertEqual(
            captured.buffer.getvalue(), b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH\n"
        )
        pump.assert_not_called()

    def test_runner_info_changes_unknown_child_code_only(self) -> None:
        module = self._module()
        self.assertEqual(
            module._classify_install_guard_bytes(b"unknown\n", ready=True),
            b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n",
        )
        self.assertEqual(
            module._classify_install_guard_bytes(
                b"INSTALL_GUARD_FAIL RUNNER_SPAWN\n", ready=True
            ),
            b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n",
        )

    def test_controller_helper_codes_survive_ready_and_stay_disjoint(self) -> None:
        module = self._module()
        helper_codes = (
            "OUTER_EMPTY",
            "OUTER_CONTROL",
            "OUTER_DUPLICATE",
            "OUTER_MULTIPLE",
            "OUTER_UNKNOWN",
        )
        helper_lines = {
            f"INSTALL_GUARD_FAIL {code}".encode("ascii") for code in helper_codes
        }
        for line in helper_lines:
            with self.subTest(line=line):
                self.assertEqual(
                    module._classify_install_guard_bytes(line + b"\n", ready=True),
                    line + b"\n",
                )
        launch = b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH"
        self.assertNotIn(launch, helper_lines)
        self.assertIn(launch, module.RUNNER_LOCAL_STATUS_LINES)
        self.assertNotIn(launch, module.INSTALL_GUARD_STATUS_LINES)
        self.assertEqual(
            module._classify_install_guard_bytes(launch + b"\n", ready=True),
            b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n",
        )

    def test_helper_mapper_codes_are_single_source_and_complete(self) -> None:
        module = self._module()
        expected = {
            f"INSTALL_GUARD_FAIL {code}".encode("ascii")
            for code in module.INSTALL_GUARD_HELPER_CODES
        }
        actual = {
            line
            for line in module.INSTALL_GUARD_STATUS_LINES
            if line.startswith(b"INSTALL_GUARD_FAIL OUTER_")
        }
        self.assertEqual(
            actual - expected,
            {b"INSTALL_GUARD_FAIL OUTER_HELPER", b"INSTALL_GUARD_FAIL OUTER_STATUS"},
        )
        self.assertTrue(expected <= actual)
        self.assertTrue(actual.isdisjoint(module.RUNNER_LOCAL_STATUS_LINES))
        self.assertIn(
            b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH", module.RUNNER_LOCAL_STATUS_LINES
        )
        self.assertNotIn(
            b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH", module.INSTALL_GUARD_STATUS_LINES
        )

    def test_runner_info_binding_requires_exact_child_identity(self) -> None:
        module = self._module()
        info = {
            "child-pid": 20,
            "cgroup-namespace": 2,
            "ipc-namespace": 3,
            "mnt-namespace": 4,
            "net-namespace": 5,
            "pid-namespace": 6,
            "uts-namespace": 7,
        }
        namespaces = {key: info[key] for key in module.RUNNER_INFO_NAMESPACE_KEYS}
        valid = (
            (
                [(20, (b"R", 4242, 20))],
                [
                    (b"R", os.getpid(), 10),
                    (b"R", 4242, 20),
                    (b"R", os.getpid(), 10),
                    (b"R", 4242, 20),
                ],
            ),
        )
        with self.subTest(case="valid"):
            with mock.patch.object(
                module, "_runner_children", return_value=[20]
            ), mock.patch.object(
                module, "_runner_proc_info", side_effect=valid[0][1]
            ), mock.patch.object(
                module, "_runner_namespace_inodes", return_value=namespaces
            ), mock.patch.object(
                module.os, "pidfd_open", return_value=33
            ):
                self.assertEqual(module._runner_bind_info(info, 4242, 10), (20, 20, 33))
        cases = (
            ("wrong-pid", [21], [(b"R", os.getpid(), 10), (b"R", 4242, 20)]),
            ("wrong-ppid", [20], [(b"R", os.getpid(), 10), (b"R", 99, 20)]),
            (
                "pid-reuse",
                [20],
                [
                    (b"R", os.getpid(), 10),
                    (b"R", 4242, 20),
                    (b"R", os.getpid(), 10),
                    (b"R", 4242, 21),
                ],
            ),
        )
        for name, children, proc_infos in cases:
            with self.subTest(case=name):
                with mock.patch.object(
                    module, "_runner_children", return_value=children
                ), mock.patch.object(
                    module, "_runner_proc_info", side_effect=proc_infos
                ), mock.patch.object(
                    module, "_runner_namespace_inodes", return_value=namespaces
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=33
                ), mock.patch.object(
                    module.os, "close"
                ):
                    self.assertIsNone(module._runner_bind_info(info, 4242, 10))

    def test_runner_info_binding_revalidates_every_namespace_inode(self) -> None:
        module = self._module()
        info = {
            "child-pid": 20,
            "cgroup-namespace": 2,
            "ipc-namespace": 3,
            "mnt-namespace": 4,
            "net-namespace": 5,
            "pid-namespace": 6,
            "uts-namespace": 7,
        }
        expected = {key: info[key] for key in module.RUNNER_INFO_NAMESPACE_KEYS}
        for namespace_key in module.RUNNER_INFO_NAMESPACE_KEYS:
            with self.subTest(namespace=namespace_key):
                changed = dict(expected)
                changed[namespace_key] += 1
                with mock.patch.object(
                    module, "_runner_children", return_value=[20]
                ), mock.patch.object(
                    module,
                    "_runner_proc_info",
                    side_effect=[
                        (b"R", os.getpid(), 10),
                        (b"R", 4242, 20),
                        (b"R", os.getpid(), 10),
                        (b"R", 4242, 20),
                    ],
                ), mock.patch.object(
                    module, "_runner_namespace_inodes", side_effect=[expected, changed]
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=33
                ), mock.patch.object(
                    module.os, "close"
                ):
                    self.assertIsNone(module._runner_bind_info(info, 4242, 10))

    def test_runner_does_not_pump_before_complete_info_validation(self) -> None:
        module = self._module()
        process = mock.Mock(pid=4242, stderr=mock.Mock(), stdin=None)
        process.poll.return_value = 0
        captured = _BinaryStderr()
        with mock.patch.object(
            module, "_runner_read_stdin", return_value=b""
        ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
            module, "_runner_has_no_children", return_value=True
        ), mock.patch.object(
            module,
            "_runner_bwrap_command",
            return_value=[module.RUNNER_BWRAP, "--info-fd"],
        ), mock.patch.object(
            module.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
        ), mock.patch.object(
            module.os, "pidfd_open", return_value=3
        ), mock.patch.object(
            module, "_runner_read_info", return_value=None
        ), mock.patch.object(
            module, "_runner_pump"
        ) as pump, mock.patch.object(
            module, "_runner_reap_children"
        ), mock.patch.object(
            module, "_runner_collect", return_value=True
        ), mock.patch.object(
            module, "_runner_live_handles", return_value=(False, False)
        ), mock.patch.object(
            module, "_runner_cleanup", return_value=True
        ), mock.patch.object(
            module.os, "close"
        ), contextlib.redirect_stderr(
            captured
        ):
            status = module._run_install_guard_outer(
                [
                    "--timeout",
                    "1",
                    "--term-grace",
                    "0.1",
                    "--",
                    "ignored",
                ]
            )
        self.assertEqual(status, 2)
        self.assertEqual(
            captured.buffer.getvalue(), b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH\n"
        )
        pump.assert_not_called()

    def test_runner_cleanup_uses_at_most_two_bounded_grace_phases(self) -> None:
        module = self._module()
        process = mock.Mock(pid=4242)
        process.poll.return_value = None
        clock = mock.Mock(side_effect=[0.0, 1.0, 2.0, 3.0, 4.0])
        with mock.patch.object(module.time, "monotonic", clock), mock.patch.object(
            module.time, "sleep"
        ), mock.patch.object(module, "_runner_signal_all"), mock.patch.object(
            module, "_runner_signal_direct"
        ), mock.patch.object(
            module, "_runner_signal_group"
        ), mock.patch.object(
            module, "_runner_reap_children"
        ), mock.patch.object(
            module, "_runner_collect", return_value=False
        ), mock.patch.object(
            module, "_runner_live_handles", return_value=(True, False)
        ), mock.patch.object(
            module, "_runner_waitpid_empty", return_value=False
        ):
            self.assertFalse(
                module._runner_cleanup(
                    {4242: (7, 3, os.getpid())}, 4242, 0.1, process, 7
                )
            )
        self.assertLessEqual(clock.call_count, 5)

    def test_runner_invalid_arguments_emit_exact_arg_once(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SUPERVISOR), "--run-install-guard-outer"],
            input=b"",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL ARG\n")

    def test_runner_spawn_and_identity_failures_are_exact(self) -> None:
        module = self._module()
        for phase, process_info, expected in (
            ("spawn", None, b"INSTALL_GUARD_FAIL RUNNER_SPAWN\n"),
            ("identity", None, b"INSTALL_GUARD_FAIL RUNNER_IDENTITY\n"),
        ):
            with self.subTest(phase=phase):
                captured = _BinaryStderr()
                process = mock.Mock(pid=4242, stderr=mock.Mock(), stdin=None)
                process.poll.return_value = 0
                popen = mock.patch.object(
                    module.subprocess,
                    "Popen",
                    side_effect=OSError if phase == "spawn" else None,
                )
                if phase == "identity":
                    popen = mock.patch.object(
                        module.subprocess, "Popen", return_value=process
                    )
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), mock.patch.object(
                    module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
                ), popen, mock.patch.object(
                    module, "_runner_proc_info", return_value=process_info
                ), mock.patch.object(
                    module, "_runner_cleanup", return_value=True
                ), mock.patch.object(
                    module.os, "close"
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, 2)
                self.assertEqual(captured.buffer.getvalue(), expected)

    def test_runner_cleanup_failure_replaces_child_status(self) -> None:
        module = self._module()
        process = mock.Mock(pid=4242, stderr=mock.Mock(), stdin=None)
        process.poll.return_value = 0
        captured = _BinaryStderr()
        with mock.patch.object(
            module, "_runner_read_stdin", return_value=b""
        ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
            module, "_runner_has_no_children", return_value=True
        ), mock.patch.object(
            module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
        ), mock.patch.object(
            module.subprocess, "Popen", return_value=process
        ), mock.patch.object(
            module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
        ), mock.patch.object(
            module.os, "pidfd_open", return_value=3
        ), mock.patch.object(
            module, "_runner_pidfd_exited", return_value=False
        ), mock.patch.object(
            module,
            "_runner_pump",
            return_value=(1, b"ROOT_FAIL ROOT_META\n", False, False),
        ), mock.patch.object(
            module, "_runner_reap_children"
        ), mock.patch.object(
            module, "_runner_collect", return_value=True
        ), mock.patch.object(
            module, "_runner_live_handles", return_value=(False, False)
        ), mock.patch.object(
            module, "_runner_cleanup", return_value=False
        ), mock.patch.object(
            module.os, "close"
        ), contextlib.redirect_stderr(
            captured
        ):
            status = module._run_install_guard_outer(
                [
                    "--timeout",
                    "1",
                    "--term-grace",
                    "0.1",
                    "--",
                    "ignored",
                ]
            )
            self.assertEqual(status, 2)
            self.assertEqual(
                captured.buffer.getvalue(), b"INSTALL_GUARD_FAIL RUNNER_CLEANUP\n"
            )

    def test_install_outer_runner_forwards_only_fixed_child_code(self) -> None:
        result = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('ROOT_FAIL ROOT_META\\n'); sys.exit(1)",
            ]
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"ROOT_FAIL ROOT_META\n")

    def test_install_outer_runner_hides_raw_stderr_and_rejects_noncanonical_command(
        self,
    ) -> None:
        raw = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('secret raw\\n'); sys.exit(1)",
            ]
        )
        self.assertEqual(raw.returncode, 1)
        self.assertEqual(raw.stderr, b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n")
        missing = self._run_install_outer(["/tmp/no-such-install-guard-command"])
        self.assertEqual(missing.returncode, 2)
        self.assertEqual(missing.stderr, b"INSTALL_GUARD_FAIL RUNNER_CONTRACT\n")

    def test_install_outer_runner_classifies_untrusted_child_output_exactly(
        self,
    ) -> None:
        cases = (
            ("import sys; sys.exit(1)", b"INSTALL_GUARD_FAIL RUNNER_CHILD_EMPTY\n"),
            (
                "import sys; sys.stderr.buffer.write(b'\\x01\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_CONTROL\n",
            ),
            (
                "import sys; sys.stderr.write('not-terminal') ; sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_FRAME\n",
            ),
            (
                "import sys; sys.stderr.write('one\\ntwo\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_FRAME\n",
            ),
            (
                "import sys; sys.stderr.write('INSTALL_GUARD_FAIL RUNNER_SPAWN\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n",
            ),
            (
                "import sys; sys.stderr.write('INSTALL_GUARD_FAIL UNKNOWN\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_STATUS_UNKNOWN\n",
            ),
            (
                "import sys; sys.stderr.write('bwrap: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_BWRAP\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/env: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_EXEC\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/python3.14: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_EXEC\n",
            ),
            (
                "import sys; sys.stderr.write('bwrap:setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n",
            ),
            (
                "import sys; sys.stderr.write('bwrap: '); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_FRAME\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/env:setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/envx: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/env: setup é\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n",
            ),
            (
                "import sys; sys.stderr.buffer.write(b'bwrap: setup\\x01\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_CONTROL\n",
            ),
            (
                "import sys; sys.stderr.write('INSTALL_GUARD_FAIL UNKNOWN\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_STATUS_UNKNOWN\n",
            ),
            (
                "import sys; sys.stderr.write('bwrap: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_BWRAP\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/env: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_EXEC\n",
            ),
            (
                "import sys; sys.stderr.write('/usr/bin/python3.14: setup failed\\n'); sys.exit(1)",
                b"INSTALL_GUARD_FAIL RUNNER_CHILD_EXEC\n",
            ),
        )
        for code, expected in cases:
            with self.subTest(expected=expected):
                result = self._run_install_outer([sys.executable, "-c", code])
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, expected)

    def test_install_outer_runner_overflow_is_runner_child_frame(self) -> None:
        result = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import os,sys; os.write(2, b'x' * 8193); sys.exit(1)",
            ]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL RUNNER_CHILD_FRAME\n")

    def test_runner_pump_handles_stderr_eof_before_exit(self) -> None:
        module = self._module()
        for exit_status, delay, timed_out in (
            (0, 0.05, False),
            (1, 0.05, False),
            (0, 1.0, True),
        ):
            with self.subTest(exit_status=exit_status, timed_out=timed_out):
                command = [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    "import os,sys,time; sys.stdin.buffer.read(); os.close(2); "
                    f"time.sleep({delay}); sys.exit({exit_status})",
                ]
                with subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                ) as process:
                    try:
                        status, data, actual_timeout, overflow = module._runner_pump(
                            process,
                            b"",
                            time.monotonic() + 0.4,
                        )
                        self.assertEqual(
                            (data, actual_timeout, overflow), (b"", timed_out, False)
                        )
                        if not timed_out:
                            self.assertEqual(status, exit_status)
                    finally:
                        if process.poll() is None:
                            process.kill()

    def test_install_outer_runner_exit_zero_with_stderr_is_fixed_status(self) -> None:
        result = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('unexpected\\n')",
            ]
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL OUTER_STATUS\n")

    def test_install_outer_runner_timeout_is_supervisor_and_reaped(self) -> None:
        result = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import time; time.sleep(2)",
            ]
        )
        self.assertEqual(result.returncode, 124)
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL SUPERVISOR\n")
        self._assert_runner_children_empty()

    def test_install_outer_runner_reaps_double_fork_setsid_descendant(self) -> None:
        comm = "sc" + secrets.token_hex(6)
        self.assertLessEqual(len(comm.encode("ascii")), 15)
        payload = (
            "import ctypes,os,time; "
            "libc=ctypes.CDLL(None); "
            f"libc.prctl(15,{comm.encode('ascii')!r},0,0,0); "
            "first=os.fork(); "
            "(time.sleep(10), os._exit(0)) if first else None; "
            "second=os.fork(); "
            "(os._exit(0) if second else (os.setsid(), libc.prctl(15,"
            f"{comm.encode('ascii')!r},0,0,0), time.sleep(10)))"
        ).encode("ascii")
        command = [
            sys.executable,
            str(SUPERVISOR),
            "--run-install-guard-outer",
            "--timeout",
            "1",
            "--term-grace",
            "0.2",
            "--",
            "/usr/bin/env",
            "-i",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "LC_ALL=C",
            "PYTHONDONTWRITEBYTECODE=1",
            "/usr/bin/python3.14",
            "-I",
            "-S",
            "-",
        ]
        runner = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert runner.stdin is not None
        runner.stdin.write(payload)
        runner.stdin.close()
        found = None
        for _ in range(100):
            for entry in os.scandir("/proc"):
                if not entry.name.isdigit():
                    continue
                try:
                    current_comm = Path(entry.path, "comm").read_text().rstrip("\n")
                    if current_comm != comm:
                        continue
                    fields = (
                        Path(entry.path, "stat")
                        .read_bytes()
                        .rsplit(b") ", 1)[1]
                        .split()
                    )
                    found = (int(entry.name), int(fields[19]))
                    break
                except (OSError, ValueError, IndexError):
                    continue
            if found is not None:
                break
            time.sleep(0.01)
        self.assertIsNotNone(found)
        runner.wait(timeout=5)
        stderr = runner.stderr.read() if runner.stderr is not None else b""
        self.assertEqual(runner.returncode, 124)
        self.assertEqual(stderr, b"INSTALL_GUARD_FAIL SUPERVISOR\n")
        pid, start = found
        if Path(f"/proc/{pid}").exists():
            current_comm = Path(f"/proc/{pid}/comm").read_text().rstrip("\n")
            fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b") ", 1)[1].split()
            self.assertFalse((pid, int(fields[19]), current_comm) == (pid, start, comm))
        self._assert_runner_children_empty()

    def test_runner_restchild_or_uncertainty_overrides_terminal_classification(
        self,
    ) -> None:
        module = self._module()
        for live, uncertain in ((True, False), (False, True)):
            with self.subTest(live=live, uncertain=uncertain):
                process = mock.Mock(
                    pid=4242, returncode=0, stderr=mock.Mock(), stdin=None
                )
                process.poll.return_value = 0
                captured = _BinaryStderr()
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), mock.patch.object(
                    module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
                ), mock.patch.object(
                    module.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=3
                ), mock.patch.object(
                    module,
                    "_runner_pump",
                    return_value=(1, b"ROOT_FAIL ROOT_META\n", False, False),
                ), mock.patch.object(
                    module, "_runner_reap_children"
                ), mock.patch.object(
                    module, "_runner_collect", return_value=True
                ), mock.patch.object(
                    module, "_runner_live_handles", return_value=(live, uncertain)
                ), mock.patch.object(
                    module, "_runner_cleanup", return_value=True
                ) as cleanup, mock.patch.object(
                    module.os, "close"
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, 124)
                self.assertEqual(
                    captured.buffer.getvalue(), b"INSTALL_GUARD_FAIL SUPERVISOR\n"
                )
                cleanup.assert_called_once()

    def test_runner_specific_status_survives_cleanup_failure(self) -> None:
        module = self._module()
        cases = (
            (0, b"unexpected\n", b"INSTALL_GUARD_FAIL RUNNER_CLEANUP\n", 2),
            *(
                (
                    1,
                    f"INSTALL_GUARD_FAIL {code}\n".encode("ascii"),
                    b"INSTALL_GUARD_FAIL RUNNER_CLEANUP\n",
                    2,
                )
                for code in module.INSTALL_GUARD_HELPER_CODES
            ),
            (-1, b"", b"INSTALL_GUARD_FAIL SUPERVISOR\n", 124),
        )
        for child_status, child_stderr, expected, expected_status in cases:
            with self.subTest(expected=expected):
                process = mock.Mock(
                    pid=4242, returncode=child_status, stderr=mock.Mock(), stdin=None
                )
                process.poll.return_value = 0
                captured = _BinaryStderr()
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), mock.patch.object(
                    module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
                ), mock.patch.object(
                    module.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=3
                ), mock.patch.object(
                    module, "_runner_pidfd_exited", return_value=False
                ), mock.patch.object(
                    module,
                    "_runner_pump",
                    return_value=(
                        child_status,
                        child_stderr,
                        child_status == -1,
                        False,
                    ),
                ), mock.patch.object(
                    module, "_runner_reap_children"
                ), mock.patch.object(
                    module, "_runner_collect", return_value=True
                ), mock.patch.object(
                    module, "_runner_live_handles", return_value=(False, False)
                ), mock.patch.object(
                    module, "_runner_cleanup", return_value=False
                ), mock.patch.object(
                    module.os, "close"
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, expected_status)
                self.assertEqual(captured.buffer.getvalue(), expected)

    def test_child_diagnostic_survives_successful_cleanup_unchanged(self) -> None:
        module = self._module()
        for code in module.INSTALL_GUARD_HELPER_CODES:
            expected = f"INSTALL_GUARD_FAIL {code}\n".encode("ascii")
            with self.subTest(code=code):
                process = mock.Mock(
                    pid=4242, returncode=1, stderr=mock.Mock(), stdin=None
                )
                process.poll.return_value = 0
                captured = _BinaryStderr()
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), mock.patch.object(
                    module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
                ), mock.patch.object(
                    module.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=3
                ), mock.patch.object(
                    module, "_runner_pidfd_exited", return_value=False
                ), mock.patch.object(
                    module, "_runner_pump", return_value=(1, expected, False, False)
                ), mock.patch.object(
                    module, "_runner_reap_children"
                ), mock.patch.object(
                    module, "_runner_collect", return_value=True
                ), mock.patch.object(
                    module, "_runner_live_handles", return_value=(False, False)
                ), mock.patch.object(
                    module, "_runner_cleanup", return_value=True
                ), mock.patch.object(
                    module.os, "close"
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, 1)
                self.assertEqual(captured.buffer.getvalue(), expected)

    def test_every_local_runner_status_as_child_is_untrusted_code(self) -> None:
        module = self._module()
        for line in sorted(module.RUNNER_LOCAL_STATUS_LINES):
            with self.subTest(line=line):
                process = mock.Mock(
                    pid=4242, returncode=1, stderr=mock.Mock(), stdin=None
                )
                process.poll.return_value = 0
                captured = _BinaryStderr()
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), mock.patch.object(
                    module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
                ), mock.patch.object(
                    module.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=3
                ), mock.patch.object(
                    module, "_runner_pidfd_exited", return_value=False
                ), mock.patch.object(
                    module, "_runner_pump", return_value=(1, line + b"\n", False, False)
                ), mock.patch.object(
                    module, "_runner_reap_children"
                ), mock.patch.object(
                    module, "_runner_collect", return_value=True
                ), mock.patch.object(
                    module, "_runner_live_handles", return_value=(False, False)
                ), mock.patch.object(
                    module, "_runner_cleanup", return_value=True
                ), mock.patch.object(
                    module.os, "close"
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, 1)
                self.assertEqual(
                    captured.buffer.getvalue(),
                    b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n",
                )

    def test_every_child_status_is_overridden_by_cleanup_failure(self) -> None:
        module = self._module()
        child_lines = (
            module.INSTALL_GUARD_STATUS_LINES | module.RUNNER_LOCAL_STATUS_LINES
        )
        for line in sorted(child_lines):
            with self.subTest(line=line):
                process = mock.Mock(
                    pid=4242, returncode=1, stderr=mock.Mock(), stdin=None
                )
                process.poll.return_value = 0
                captured = _BinaryStderr()
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), mock.patch.object(module, "_enable_subreaper"), mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), mock.patch.object(
                    module, "_runner_bwrap_command", return_value=[module.RUNNER_BWRAP]
                ), mock.patch.object(
                    module.subprocess, "Popen", return_value=process
                ), mock.patch.object(
                    module, "_runner_proc_info", return_value=(b"R", os.getpid(), 7)
                ), mock.patch.object(
                    module.os, "pidfd_open", return_value=3
                ), mock.patch.object(
                    module, "_runner_pidfd_exited", return_value=False
                ), mock.patch.object(
                    module, "_runner_pump", return_value=(1, line + b"\n", False, False)
                ), mock.patch.object(
                    module, "_runner_reap_children"
                ), mock.patch.object(
                    module, "_runner_collect", return_value=True
                ), mock.patch.object(
                    module, "_runner_live_handles", return_value=(False, False)
                ), mock.patch.object(
                    module, "_runner_cleanup", return_value=False
                ), mock.patch.object(
                    module.os, "close"
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, 2)
                self.assertEqual(
                    captured.buffer.getvalue(), b"INSTALL_GUARD_FAIL RUNNER_CLEANUP\n"
                )

    def test_local_runner_failures_are_not_child_statuses(self) -> None:
        module = self._module()
        cases = (
            ("subreaper", "RUNNER_SUBREAPER"),
            ("contract", "RUNNER_CONTRACT"),
        )
        for phase, code in cases:
            with self.subTest(phase=phase):
                captured = _BinaryStderr()
                subreaper = mock.patch.object(
                    module, "_enable_subreaper", side_effect=OSError
                )
                builder = mock.patch.object(
                    module, "_runner_bwrap_command", side_effect=OSError
                )
                if phase == "contract":
                    subreaper = mock.patch.object(module, "_enable_subreaper")
                with mock.patch.object(
                    module, "_runner_read_stdin", return_value=b""
                ), subreaper, builder, mock.patch.object(
                    module, "_runner_has_no_children", return_value=True
                ), contextlib.redirect_stderr(
                    captured
                ):
                    status = module._run_install_guard_outer(
                        [
                            "--timeout",
                            "1",
                            "--term-grace",
                            "0.1",
                            "--",
                            "ignored",
                        ]
                    )
                self.assertEqual(status, 2)
                self.assertEqual(
                    captured.buffer.getvalue(),
                    f"INSTALL_GUARD_FAIL {code}\n".encode("ascii"),
                )

    def test_child_runner_status_spoof_is_runner_child(self) -> None:
        result = self._run_install_outer(
            [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('INSTALL_GUARD_FAIL RUNNER_SPAWN\\n'); sys.exit(1)",
            ]
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n")

    def test_install_outer_runner_requires_subreaper_before_spawn(self) -> None:
        module = self._module()
        stderr = _BinaryStderr()
        with mock.patch.object(
            module, "_enable_subreaper", side_effect=OSError
        ), mock.patch.object(module.subprocess, "Popen") as popen, mock.patch.object(
            module, "_runner_read_stdin", return_value=b""
        ), contextlib.redirect_stderr(
            stderr
        ):
            status = module._run_install_guard_outer(
                [
                    "--timeout",
                    "1",
                    "--term-grace",
                    "0.1",
                    "--",
                    sys.executable,
                    "-c",
                    "pass",
                ]
            )
        self.assertEqual(status, 2)
        self.assertFalse(popen.called)
        self.assertEqual(
            stderr.buffer.getvalue(), b"INSTALL_GUARD_FAIL RUNNER_SUBREAPER\n"
        )

    def test_install_outer_runner_pidfd_failure_cleans_started_leader(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory(prefix="soc-runner.", dir="/tmp") as temporary:
            marker = Path(temporary) / "leader.pid"
            command = [
                sys.executable,
                "-c",
                "import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid())); time.sleep(10)",
                str(marker),
            ]
            stderr = _BinaryStderr()
            real_popen = module.subprocess.Popen
            marker_pid = []

            def spawn_and_wait_for_marker(*args, **kwargs):
                process = real_popen(*args, **kwargs)
                for _ in range(100):
                    if marker.exists():
                        marker_pid.append(int(marker.read_text()))
                        return process
                    time.sleep(0.005)
                process.kill()
                process.wait(timeout=1)
                raise AssertionError("runner child marker was not created")

            with mock.patch.object(
                module.subprocess, "Popen", side_effect=spawn_and_wait_for_marker
            ), mock.patch.object(
                module.os, "pidfd_open", side_effect=OSError
            ), mock.patch.object(
                module, "_runner_has_no_children", return_value=True
            ), mock.patch.object(
                module, "_enable_subreaper", return_value=None
            ), mock.patch.object(
                module, "_runner_read_stdin", return_value=b""
            ), mock.patch.object(
                module, "_runner_bwrap_command", return_value=command
            ), contextlib.redirect_stderr(
                stderr
            ):
                status = module._run_install_guard_outer(
                    [
                        "--timeout",
                        "1",
                        "--term-grace",
                        "0.2",
                        "--",
                        *command,
                    ]
                )
            self.assertEqual(status, 2)
            self.assertEqual(
                stderr.buffer.getvalue(), b"INSTALL_GUARD_FAIL RUNNER_PIDFD\n"
            )
            self.assertEqual(len(marker_pid), 1)
            self.assertFalse(Path(f"/proc/{marker_pid[0]}").exists())
            self._assert_runner_children_empty()

    def test_install_outer_runner_collect_is_fail_closed_on_parse_and_node_limits(
        self,
    ) -> None:
        module = self._module()
        handles = {}
        with mock.patch.object(module, "_runner_children", side_effect=OSError):
            self.assertFalse(module._runner_collect(handles, os.getpid()))
        fake_children = list(range(100, 100 + module.RUNNER_NODE_LIMIT + 1))
        with mock.patch.object(
            module, "_runner_children", return_value=fake_children
        ), mock.patch.object(
            module, "_runner_proc_info", return_value=(b"R", os.getpid(), 1)
        ), mock.patch.object(
            module.os, "pidfd_open", return_value=3
        ):
            self.assertFalse(module._runner_collect({}, os.getpid()))

    def test_runner_stdin_accepts_regular_file_larger_than_heredoc_pipe(self) -> None:
        module = self._module()
        payload = b"x" * 16384
        with tempfile.TemporaryFile(mode="w+", encoding="ascii") as stream:
            stream.write(payload.decode("ascii"))
            stream.seek(0)
            self.assertTrue(stat.S_ISREG(os.fstat(stream.fileno()).st_mode))
            with mock.patch.object(module.sys, "stdin", stream):
                self.assertEqual(module._runner_read_stdin(), payload)

    def test_install_outer_runner_rejects_oversized_input_without_child(self) -> None:
        result = self._run_install_outer(
            [sys.executable, "-c", "raise SystemExit(9)"],
            b"x" * (256 * 1024 + 1),
        )
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL RUNNER_INPUT\n")

    def test_install_guard_status_cli_forwards_core_parser_codes_unchanged(
        self,
    ) -> None:
        for code in ("EXIT", "EMPTY", "MULTIPLE", "CONTROL", "UNKNOWN"):
            with self.subTest(code=code):
                expected = f"INSTALL_GUARD_FAIL CORE_PARSER_{code}\n".encode("ascii")
                with tempfile.TemporaryDirectory(
                    prefix="soc-status.", dir="/tmp"
                ) as temporary:
                    path = Path(temporary) / "controller.stderr"
                    path.write_bytes(expected)
                    path.chmod(0o600)
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SUPERVISOR),
                            "--print-install-guard-status",
                            str(path),
                        ],
                        capture_output=True,
                        text=False,
                        check=False,
                    )
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, expected)

    def test_install_guard_status_rejects_symlink_and_hardlink(self) -> None:
        with tempfile.TemporaryDirectory(prefix="soc-status.", dir="/tmp") as temporary:
            directory = Path(temporary)
            target = directory / "target"
            target.write_bytes(b"ROOT_FAIL ROOT_META\n")
            target.chmod(0o600)

            symlink = directory / "symlink"
            symlink.symlink_to(target)
            symlink_result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--print-install-guard-status",
                    str(symlink),
                ],
                capture_output=True,
                text=False,
                check=False,
            )
            self.assertEqual(symlink_result.returncode, 0)
            self.assertEqual(symlink_result.stdout, b"")
            self.assertEqual(
                symlink_result.stderr, b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"
            )

            hardlink = directory / "hardlink"
            os.link(target, hardlink)
            hardlink_result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--print-install-guard-status",
                    str(hardlink),
                ],
                capture_output=True,
                text=False,
                check=False,
            )
            self.assertEqual(hardlink_result.returncode, 0)
            self.assertEqual(hardlink_result.stdout, b"")
            self.assertEqual(
                hardlink_result.stderr, b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"
            )

    def test_install_guard_status_rejects_fifo_without_reading(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory(prefix="soc-status.", dir="/tmp") as temporary:
            fifo = Path(temporary) / "fifo"
            os.mkfifo(fifo, 0o600)

            def reject_fifo(path: str, flags: int) -> int:
                self.assertEqual(path, str(fifo))
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_RDONLY == os.O_RDONLY)
                raise OSError("fifo rejected")

            with mock.patch.object(module.os, "open", side_effect=reject_fifo):
                output = module._install_guard_status_output(str(fifo))
        self.assertEqual(output, b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n")

    def _run(
        self, code: str, *args: str, timeout: float = 6.0
    ) -> tuple[subprocess.CompletedProcess[str], bytes]:
        with tempfile.TemporaryDirectory(
            prefix="soc-supervisor.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            stdout_log = root / "target.stdout"
            stderr_log = root / "target.stderr"
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
            command = [
                sys.executable,
                "-I",
                "-S",
                str(SUPERVISOR),
                "--timeout",
                "1.0",
                "--term-grace",
                "0.5",
                "--completion-fd",
                str(write_fd),
                "--completion-nonce",
                NONCE,
                "--stdout-log",
                str(stdout_log),
                "--stderr-log",
                str(stderr_log),
                "--",
                sys.executable,
                "-I",
                "-S",
                "-c",
                code,
                *args,
            ]
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                close_fds=True,
                pass_fds=(write_fd,),
            )
            os.close(write_fd)
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                payload = bytearray()
                while True:
                    chunk = os.read(read_fd, 512)
                    if not chunk:
                        break
                    payload.extend(chunk)
                result = subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
                expected = f"SOC_GUI_COMPLETE {NONCE} {result.returncode}\n".encode(
                    "ascii"
                )
                self.assertEqual(bytes(payload), expected, msg=stdout + stderr)
                self.assertLessEqual(len(payload), 256)
                return result, bytes(payload)
            finally:
                os.close(read_fd)

    def test_mirrors_bounded_root_exit(self) -> None:
        result, _payload = self._run("raise SystemExit(7)")
        self.assertEqual(result.returncode, 7, msg=result.stdout + result.stderr)

    def test_fast_double_fork_setsid_is_adopted_killed_and_reaped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pid_path = root / "orphan.pid"
            marker_path = root / "escaped.marker"
            code = """
import os, sys, time
pid_path, marker_path = sys.argv[1:]
first = os.fork()
if first == 0:
    second = os.fork()
    if second == 0:
        os.setsid()
        with open(pid_path, 'w', encoding='ascii') as stream:
            stream.write(str(os.getpid()))
        time.sleep(2.0)
        with open(marker_path, 'w', encoding='ascii') as stream:
            stream.write('escaped')
        os._exit(0)
    os._exit(0)
os._exit(0)
"""
            result, _payload = self._run(code, str(pid_path), str(marker_path))
            self.assertEqual(result.returncode, 125, msg=result.stdout + result.stderr)
            deadline = time.monotonic() + 1.0
            while not pid_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(pid_path.exists())
            orphan_pid = int(pid_path.read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{orphan_pid}").exists())
            time.sleep(0.1)
            self.assertFalse(marker_path.exists())

    def test_timeout_cleans_double_fork_before_return(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pid_path = root / "orphan.pid"
            marker_path = root / "escaped.marker"
            code = """
import os, sys, time
pid_path, marker_path = sys.argv[1:]
if os.fork() == 0:
    if os.fork() == 0:
        os.setsid()
        with open(pid_path, 'w', encoding='ascii') as stream:
            stream.write(str(os.getpid()))
        time.sleep(2.0)
        with open(marker_path, 'w', encoding='ascii') as stream:
            stream.write('escaped')
        os._exit(0)
    os._exit(0)
time.sleep(5.0)
"""
            result, _payload = self._run(code, str(pid_path), str(marker_path))
            self.assertEqual(result.returncode, 124, msg=result.stdout + result.stderr)
            orphan_pid = int(pid_path.read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{orphan_pid}").exists())
            time.sleep(0.1)
            self.assertFalse(marker_path.exists())

    def test_target_does_not_inherit_completion_pipe(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-supervisor-fd.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
            pipe_entry = os.fstat(write_fd)
            code = """
import os, sys
fd, expected_dev, expected_ino = map(int, sys.argv[1:])
try:
    entry = os.fstat(fd)
except OSError:
    raise SystemExit(0)
raise SystemExit(91 if (entry.st_dev, entry.st_ino) == (expected_dev, expected_ino) else 0)
"""
            command = [
                sys.executable,
                "-I",
                "-S",
                str(SUPERVISOR),
                "--timeout",
                "1",
                "--term-grace",
                "0.2",
                "--completion-fd",
                str(write_fd),
                "--completion-nonce",
                NONCE,
                "--stdout-log",
                str(root / "stdout"),
                "--stderr-log",
                str(root / "stderr"),
                "--",
                sys.executable,
                "-I",
                "-S",
                "-c",
                code,
                str(write_fd),
                str(pipe_entry.st_dev),
                str(pipe_entry.st_ino),
            ]
            process = subprocess.Popen(command, close_fds=True, pass_fds=(write_fd,))
            os.close(write_fd)
            try:
                self.assertEqual(process.wait(timeout=4), 0)
                payload = os.read(read_fd, 512)
                self.assertEqual(
                    payload, f"SOC_GUI_COMPLETE {NONCE} 0\n".encode("ascii")
                )
                self.assertEqual(os.read(read_fd, 1), b"")
            finally:
                os.close(read_fd)

    def test_forged_tree_empty_file_is_not_a_completion_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            forged = Path(temporary) / "TREE_EMPTY"
            result, payload = self._run(
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('TREE_EMPTY 1 1\\n'); raise SystemExit(0)",
                str(forged),
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(forged.read_text(encoding="ascii"), "TREE_EMPTY 1 1\n")
            self.assertTrue(payload.startswith(b"SOC_GUI_COMPLETE "))

    def test_supervisor_sigkill_cannot_produce_success_frame(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-supervisor-kill.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            marker = root / "started"
            read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
            code = """
import ctypes, os, signal, sys, time
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
    raise SystemExit(70)
Path = __import__('pathlib').Path
Path(sys.argv[1]).write_text(str(os.getpid()), encoding='ascii')
time.sleep(5)
"""
            command = [
                sys.executable,
                "-I",
                "-S",
                str(SUPERVISOR),
                "--timeout",
                "5",
                "--term-grace",
                "0.2",
                "--completion-fd",
                str(write_fd),
                "--completion-nonce",
                NONCE,
                "--stdout-log",
                str(root / "stdout"),
                "--stderr-log",
                str(root / "stderr"),
                "--",
                sys.executable,
                "-I",
                "-S",
                "-c",
                code,
                str(marker),
            ]
            process = subprocess.Popen(command, close_fds=True, pass_fds=(write_fd,))
            os.close(write_fd)
            try:
                deadline = time.monotonic() + 2
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(marker.exists())
                os.kill(process.pid, signal.SIGKILL)
                self.assertEqual(process.wait(timeout=2), -signal.SIGKILL)
                self.assertEqual(os.read(read_fd, 512), b"")
                child_pid = int(marker.read_text(encoding="ascii"))
                deadline = time.monotonic() + 2
                while (
                    Path(f"/proc/{child_pid}").exists() and time.monotonic() < deadline
                ):
                    time.sleep(0.01)
                self.assertFalse(Path(f"/proc/{child_pid}").exists())
            finally:
                os.close(read_fd)

    def test_identity_failure_cleanup_loop_is_bounded(self) -> None:
        spec = importlib.util.spec_from_file_location(
            "gui_session_supervisor", SUPERVISOR
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        supervisor = module.Supervisor(
            1.0,
            0.05,
            write_fd,
            NONCE,
            "/tmp/unused-stdout",
            "/tmp/unused-stderr",
            ["/usr/bin/true"],
        )
        started = time.monotonic()
        try:
            with (
                mock.patch.object(module, "_identity", return_value=None),
                mock.patch.object(supervisor, "_reap"),
                mock.patch.object(supervisor, "_scan"),
                mock.patch.object(supervisor, "_send"),
                mock.patch.object(supervisor, "_children_empty", return_value=False),
            ):
                self.assertFalse(supervisor._drain())
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            supervisor.close()
            os.close(read_fd)

    def test_contract_uses_pipe_subreaper_pidfds_and_no_numeric_kill(self) -> None:
        source = SUPERVISOR.read_text(encoding="utf-8")
        for marker in (
            "PR_SET_PDEATHSIG",
            "PR_SET_CHILD_SUBREAPER",
            "PR_GET_CHILD_SUBREAPER",
            "os.pidfd_open(pid, 0)",
            "signal.pidfd_send_signal(pidfd, signum, None, 0)",
            "os.waitpid(-1, os.WNOHANG)",
            "_write_completion",
            "close_fds=True",
            "SOC_GUI_COMPLETE",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("completion-file", source)
        self.assertNotIn("TREE_EMPTY", source)
        self.assertNotIn("os.kill(", source)
        self.assertIn("process.wait(timeout=", source)

    def test_supervisor_contract_keeps_bounded_completion_and_no_raw_error_telemetry(
        self,
    ) -> None:
        source = SUPERVISOR.read_text(encoding="utf-8")
        self.assertIn("MAX_TAIL_BYTES = 8192", source)
        self.assertIn("_sanitize_bytes", source)
        self.assertNotIn("print(exc", source)

    def test_controller_tail_rejects_symlink_fifo_and_hardlink(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory(
            prefix="soc-tail-safe.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            regular = root / "stderr"
            regular.write_bytes(b"safe")
            symlink = root / "symlink"
            symlink.symlink_to(regular)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            hardlink = root / "hardlink"
            os.link(regular, hardlink)
            with self.assertRaises(OSError):
                module._read_sanitized_tail(str(symlink))
            writer = {}

            def open_fifo_writer() -> None:
                writer["fd"] = os.open(fifo, os.O_WRONLY)

            thread = threading.Thread(target=open_fifo_writer, daemon=True)
            thread.start()
            try:
                with self.assertRaises(OSError):
                    module._read_sanitized_tail(str(fifo))
            finally:
                thread.join(timeout=1)
                if "fd" in writer:
                    os.close(writer["fd"])
            with self.assertRaises(OSError):
                module._read_sanitized_tail(str(hardlink))

    def test_controller_tail_checks_owner_and_open_flags(self) -> None:
        module = self._module()
        with tempfile.TemporaryDirectory(
            prefix="soc-tail-owner.", dir="/tmp"
        ) as temporary:
            path = Path(temporary) / "stderr"
            path.write_bytes(b"safe")
            real_open = module.os.open
            with mock.patch.object(module.os, "open", wraps=real_open) as opened:
                self.assertEqual(module._read_sanitized_tail(str(path)), b"safe")
                flags = opened.call_args.args[1]
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_CLOEXEC)
            os.stat(path)
            fake_entry = mock.Mock(
                st_mode=stat.S_IFREG | 0o600,
                st_uid=os.geteuid() + 1,
                st_nlink=1,
                st_size=4,
            )
            with mock.patch.object(module.os, "fstat", return_value=fake_entry):
                with self.assertRaises(OSError):
                    module._read_sanitized_tail(str(path))

    def test_controller_tail_redacts_controls_and_limits_final_bytes(self) -> None:
        module = self._module()
        secrets = (
            "API_KEY=api-value Authorization Bearer bearer-value token=token-value "
            "access_token=access-value refresh_token=refresh-value password=pass-value "
            "passphrase=phrase-value cookie=cookie-value nonce=nonce-value secret=secret-value "
            "authority=authority-value xauth=xauth-value sk-abcdefghijk ghp_abcdefghijk "
            "QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB"
        )
        raw = (
            "x" * 1000 + secrets + "\x9b31m" + "visible\x7f" + "\udcff" + "\n\t"
        ).encode("utf-8", "surrogatepass")
        with tempfile.TemporaryDirectory(
            prefix="soc-tail-redact.", dir="/tmp"
        ) as temporary:
            path = Path(temporary) / "stderr"
            path.write_bytes(raw)
            result = module._read_sanitized_tail(str(path))
            displayed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(SUPERVISOR),
                    "--print-sanitized-tail",
                    str(path),
                ],
                capture_output=True,
                check=False,
            )
            self.assertLessEqual(len(displayed.stderr), 8192)
        self.assertLessEqual(len(result), 8192)
        self.assertNotIn(b"api-value", result)
        self.assertNotIn(b"bearer-value", result)
        self.assertNotIn(b"access-value", result)
        self.assertNotIn(b"secret-value", result)
        self.assertNotIn(b"sk-abcdefghijk", result)
        self.assertNotIn(b"ghp_abcdefghijk", result)
        self.assertNotIn(b"\x9b", result)
        self.assertNotIn(b"\x7f", result)
        self.assertNotIn(b"\xff", result)

    def test_stdin_and_file_tail_share_identical_safe_bytes(self) -> None:
        module = self._module()
        payload = (
            "prefix "
            + "Authorization Bearer bearer-value "
            + "\x1bPsecret\x1b\\ "
            + "€" * 2000
        ).encode()
        with tempfile.TemporaryDirectory(
            prefix="soc-tail-equal.", dir="/tmp"
        ) as temporary:
            path = Path(temporary) / "stderr"
            path.write_bytes(payload)
            file_result = module._read_sanitized_tail(str(path))
        helper = subprocess.run(
            [sys.executable, "-I", "-S", str(SUPERVISOR), "--sanitize-stdin"],
            input=payload,
            capture_output=True,
            check=False,
        )
        self.assertEqual(helper.returncode, 0, helper.stderr)
        self.assertEqual(file_result, helper.stdout)
        self.assertLessEqual(len(helper.stdout), 8192)
        helper.stdout.decode("utf-8")

    def test_multibyte_boundary_is_valid_and_byte_bounded(self) -> None:
        module = self._module()
        payload = ("a" * 8190 + "€€€").encode("utf-8")
        result = module._sanitize_bytes(payload)
        self.assertLessEqual(len(result), 8192)
        result.decode("utf-8")

    def test_outer_helper_argv_redacts_with_reduced_budget(self) -> None:
        header = b"run-isolated-soc-gui: isolated stderr (bounded):\n"
        budget = 8192 - len(header)
        payload = b"API_KEY=raw-api-value token=raw-token " + ("€" * 5000).encode()
        helper_argv = [
            sys.executable,
            "-I",
            "-S",
            str(SUPERVISOR),
            "--sanitize-stdin",
            "--max-output-bytes",
            str(budget),
        ]
        result = subprocess.run(
            helper_argv, input=payload, capture_output=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        frame = header + result.stdout
        self.assertLessEqual(len(frame), 8192)
        frame.decode("utf-8")
        self.assertNotIn(b"raw-api-value", frame)
        self.assertNotIn(b"raw-token", frame)

    def test_sanitize_stdin_cli_rejects_invalid_contracts(self) -> None:
        invalid = (
            ("--max-output-bytes",),
            ("--max-output-bytes", "0"),
            ("--max-output-bytes", "-1"),
            ("--max-output-bytes", "8193"),
            ("--max-output-bytes", "+1"),
            ("--max-output-bytes", "1.0"),
            ("--max-output-bytes", "1", "--max-output-bytes", "2"),
            ("--unknown", "1"),
        )
        for arguments in invalid:
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(SUPERVISOR),
                    "--sanitize-stdin",
                    *arguments,
                ],
                input=b"diagnostic",
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2, arguments)

    def test_sanitize_stdin_cli_default_and_file_tail_contracts(self) -> None:
        default = subprocess.run(
            [sys.executable, "-I", "-S", str(SUPERVISOR), "--sanitize-stdin"],
            input=b"default",
            capture_output=True,
            check=False,
        )
        self.assertEqual(default.returncode, 0)
        self.assertEqual(default.stdout, b"default")
        extra = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                str(SUPERVISOR),
                "--print-sanitized-tail",
                "/dev/null",
                "extra",
            ],
            capture_output=True,
            check=False,
        )
        self.assertEqual(extra.returncode, 2)


if __name__ == "__main__":
    unittest.main()
