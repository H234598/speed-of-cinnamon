from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ISOLATION_VARIABLES = (
    "TMPDIR",
    "TEMP",
    "TMP",
    "XDG_STATE_HOME",
    "XDG_CACHE_HOME",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
    "SPEED_OF_CINNAMON_TEST_ROOT",
    "SPEED_OF_CINNAMON_TEST_MODE",
    "SPEED_OF_CINNAMON_TEST_ROOT_OWNER",
)


def _file_signature(path: Path) -> tuple[int, int, int, int, int, int, int, int, str] | None:
    try:
        current = path.stat()
    except FileNotFoundError:
        return None
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return (
        current.st_dev,
        current.st_ino,
        current.st_mode,
        current.st_uid,
        current.st_nlink,
        current.st_size,
        current.st_mtime_ns,
        current.st_ctime_ns,
        digest,
    )


def _subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for variable in _ISOLATION_VARIABLES:
        environment.pop(variable, None)
    environment.pop("PYTHONPATH", None)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run_python(
    source: str,
    environment: dict[str, str],
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-B", "-c", source],
        cwd=_REPO_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
        text=True,
    )


class TestDefaultIsolation(unittest.TestCase):
    def test_automatic_root_uses_exact_tmp_despite_repo_tmpdir_and_stale_cache(self) -> None:
        environment = _subprocess_environment()
        environment["TMPDIR"] = os.fspath(_REPO_ROOT)
        result = _run_python(
            "import json, tempfile; "
            f"tempfile.tempdir = {os.fspath(_REPO_ROOT)!r}; "
            "from tests._test_env import isolate_user_state; "
            "root = isolate_user_state(); "
            "print(json.dumps({'root': root, 'tempdir': tempfile.tempdir}), flush=True)",
            environment,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        root = Path(payload["root"])
        self.assertEqual(root.parent, Path("/tmp"))
        self.assertEqual(Path(payload["tempdir"]), root / "tmp")
        self.assertFalse(root.exists())

    def test_test_root_requires_explicit_mode_and_private_non_symlink_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            safe_root = base / "safe"
            safe_root.mkdir(mode=0o700)
            unsafe_root = base / "unsafe"
            unsafe_root.mkdir(mode=0o755)
            link_root = base / "link"
            link_root.symlink_to(safe_root, target_is_directory=True)

            candidates = (
                (safe_root, None),
                (unsafe_root, "1"),
                (link_root, "1"),
                (_REPO_ROOT, "1"),
                (_REPO_ROOT / "tests", "1"),
            )
            for candidate, test_mode in candidates:
                with self.subTest(candidate=candidate, test_mode=test_mode):
                    environment = _subprocess_environment()
                    environment["SPEED_OF_CINNAMON_TEST_ROOT"] = os.fspath(candidate)
                    if test_mode is not None:
                        environment["SPEED_OF_CINNAMON_TEST_MODE"] = test_mode
                    result = _run_python(
                        "from tests._test_env import isolate_user_state; "
                        "print(isolate_user_state(), flush=True)",
                        environment,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    automatic_root = Path(result.stdout.strip())
                    self.assertNotEqual(automatic_root, candidate)
                    self.assertEqual(automatic_root.parent, Path("/tmp"))
                    self.assertFalse(automatic_root.exists())

    def test_unsafe_and_symlink_tmpdir_do_not_select_automatic_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            unsafe = base / "unsafe"
            unsafe.mkdir(mode=0o700)
            unsafe.chmod(0o777)
            safe = base / "safe"
            safe.mkdir(mode=0o700)
            link = base / "link"
            link.symlink_to(safe, target_is_directory=True)

            for candidate in (unsafe, link):
                with self.subTest(candidate=candidate):
                    environment = _subprocess_environment()
                    environment["TMPDIR"] = os.fspath(candidate)
                    result = _run_python(
                        "from tests._test_env import isolate_user_state; "
                        "print(isolate_user_state(), flush=True)",
                        environment,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    automatic_root = Path(result.stdout.strip())
                    self.assertEqual(automatic_root.parent, Path("/tmp"))
                    self.assertFalse(automatic_root.exists())

    def test_external_root_is_reused_but_never_cleaned_by_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "external"
            root.mkdir(mode=0o700)
            marker = root / "caller-owned"
            marker.write_text("keep", encoding="utf-8")
            environment = _subprocess_environment()
            environment["SPEED_OF_CINNAMON_TEST_ROOT"] = os.fspath(root)
            environment["SPEED_OF_CINNAMON_TEST_MODE"] = "1"
            result = _run_python(
                "import json, tempfile; "
                "from tests._test_env import isolate_user_state; "
                "root = isolate_user_state(); "
                "print(json.dumps({'root': root, 'tempdir': tempfile.tempdir}), flush=True)",
                environment,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(Path(payload["root"]), root)
            self.assertEqual(Path(payload["tempdir"]), root / "tmp")
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertTrue(root.exists())
            for name in ("tmp", "state", "cache", "data", "config"):
                current = (root / name).lstat()
                self.assertEqual(current.st_uid, os.getuid())
                self.assertEqual(stat.S_IMODE(current.st_mode), 0o700)

    def test_exception_cleanup_does_not_follow_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "outside"
            outside.mkdir(mode=0o700)
            marker = outside / "marker"
            marker.write_text("keep", encoding="utf-8")
            environment = _subprocess_environment()
            environment["SOC_TEST_OUTSIDE"] = os.fspath(outside)
            result = _run_python(
                "import os; from pathlib import Path; "
                "from tests._test_env import isolate_user_state; "
                "root = Path(isolate_user_state()); "
                "(root / 'outside-link').symlink_to(os.environ['SOC_TEST_OUTSIDE'], "
                "target_is_directory=True); "
                "print(root, flush=True); raise RuntimeError('expected test exception')",
                environment,
            )

            self.assertNotEqual(result.returncode, 0)
            automatic_root = Path(result.stdout.strip())
            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(automatic_root.exists())

    def test_exec_and_fork_children_own_distinct_roots(self) -> None:
        environment = _subprocess_environment()
        exec_result = _run_python(
            "import json, subprocess, sys; "
            "from tests._test_env import isolate_user_state; "
            "parent = isolate_user_state(); "
            "child = subprocess.run([sys.executable, '-B', '-c', "
            "'from tests._test_env import isolate_user_state; "
            "print(isolate_user_state(), flush=True)'], "
            "stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False); "
            "assert child.returncode == 0, child.stderr; "
            "print(json.dumps({'parent': parent, 'child': child.stdout.strip()}), flush=True)",
            environment,
        )
        self.assertEqual(exec_result.returncode, 0, exec_result.stderr)
        exec_roots = json.loads(exec_result.stdout)
        self.assertNotEqual(exec_roots["parent"], exec_roots["child"])
        self.assertFalse(Path(exec_roots["parent"]).exists())
        self.assertFalse(Path(exec_roots["child"]).exists())

        if not hasattr(os, "fork"):
            self.skipTest("fork is unavailable")
        fork_result = _run_python(
            """
import json
import os
from tests._test_env import isolate_user_state

parent_root = isolate_user_state()
read_fd, write_fd = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(read_fd)
    child_root = isolate_user_state()
    os.write(write_fd, child_root.encode())
    os.close(write_fd)
    raise SystemExit(0)
os.close(write_fd)
child_root = os.read(read_fd, 4096).decode()
os.close(read_fd)
_, status = os.waitpid(pid, 0)
print(json.dumps({
    'parent': parent_root,
    'child': child_root,
    'child_exit': os.waitstatus_to_exitcode(status),
}), flush=True)
""",
            environment,
        )
        self.assertEqual(fork_result.returncode, 0, fork_result.stderr)
        fork_roots = json.loads(fork_result.stdout)
        self.assertEqual(fork_roots["child_exit"], 0)
        self.assertNotEqual(fork_roots["parent"], fork_roots["child"])
        self.assertFalse(Path(fork_roots["parent"]).exists())
        self.assertFalse(Path(fork_roots["child"]).exists())

    def test_parallel_processes_reap_without_fd_or_root_leaks(self) -> None:
        environment = _subprocess_environment()
        source = (
            "import json, os; from tests._test_env import isolate_user_state; "
            "root = isolate_user_state(); before = len(os.listdir('/proc/self/fd')); "
            "[isolate_user_state() for _ in range(8)]; "
            "after = len(os.listdir('/proc/self/fd')); "
            "print(json.dumps({'root': root, 'before': before, 'after': after}), flush=True)"
        )
        processes = [
            subprocess.Popen(  # nosec B603
                [sys.executable, "-B", "-c", source],
                cwd=_REPO_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for _ in range(4)
        ]
        payloads: list[dict[str, object]] = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIsNotNone(process.poll())
            payloads.append(json.loads(stdout))
        roots = [Path(str(payload["root"])) for payload in payloads]
        self.assertEqual(len(set(roots)), len(roots))
        self.assertTrue(all(payload["before"] == payload["after"] for payload in payloads))
        self.assertTrue(all(not root.exists() for root in roots))

    def test_cleanup_refuses_replaced_root_inode(self) -> None:
        result = _run_python(
            """
import json
from pathlib import Path
from tests import _test_env

root = Path(_test_env.isolate_user_state())
moved = root.with_name(root.name + '-moved')
root.rename(moved)
root.mkdir(mode=0o700)
_test_env._cleanup_owned_root()
preserved = root.exists() and moved.exists()
root.rmdir()
for name in ('tmp', 'state', 'cache', 'data', 'config'):
    (moved / name).rmdir()
moved.rmdir()
print(json.dumps({'preserved': preserved, 'root': str(root), 'moved': str(moved)}), flush=True)
""",
            _subprocess_environment(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["preserved"])
        self.assertFalse(Path(payload["root"]).exists())
        self.assertFalse(Path(payload["moved"]).exists())

    def test_real_product_subprocess_isolates_error_logs_from_canary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canary_state_home = base / "canary-state"
            canary_logs = canary_state_home / "speed-of-cinnamon" / "logs"
            canary_logs.mkdir(parents=True, mode=0o700)
            canary_paths = (canary_logs / "errors.log", canary_logs / "errors.md")
            canary_paths[0].write_text("canary-json\n", encoding="utf-8")
            canary_paths[1].write_text("canary-markdown\n", encoding="utf-8")
            for path in canary_paths:
                path.chmod(0o600)
            canary_before = tuple(_file_signature(path) for path in canary_paths)

            test_root = base / "explicit-test-root"
            test_root.mkdir(mode=0o700)
            environment = os.environ.copy()
            for variable in _ISOLATION_VARIABLES:
                environment.pop(variable, None)
            environment.pop("PYTHONPATH", None)
            environment["XDG_STATE_HOME"] = os.fspath(canary_state_home)
            environment["SPEED_OF_CINNAMON_TEST_ROOT"] = os.fspath(test_root)
            environment["SPEED_OF_CINNAMON_TEST_MODE"] = "1"
            environment["SPEED_OF_CINNAMON_LOG_LEVEL"] = "error"
            result = subprocess.run(  # nosec B603
                [
                    sys.executable,
                    "-c",
                    "from tests._test_env import isolate_user_state; isolate_user_state(); "
                    "from speed_of_cinnamon import cli; "
                    "raise SystemExit(cli.run(['status', '--state-file', "
                    "'/proc/1/speed-of-cinnamon-negative-state.json', '--json']))",
                ],
                cwd=_REPO_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr, b"")
            self.assertEqual(
                tuple(_file_signature(path) for path in canary_paths),
                canary_before,
            )

    def test_filtered_alarm_discovery_isolates_error_logs_from_canary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            canary_state_home = base / "canary-state"
            canary_logs = canary_state_home / "speed-of-cinnamon" / "logs"
            canary_logs.mkdir(parents=True, mode=0o700)
            canary_error_log = canary_logs / "errors.log"
            canary_error_markdown = canary_logs / "errors.md"
            canary_error_log.write_text("canary-json\n", encoding="utf-8")
            canary_error_markdown.write_text("canary-markdown\n", encoding="utf-8")
            canary_paths = (canary_error_log, canary_error_markdown)
            for path in canary_paths:
                path.chmod(0o600)
            canary_before = tuple(_file_signature(path) for path in canary_paths)

            test_root = base / "explicit-test-root"
            test_root.mkdir(mode=0o700)
            environment = os.environ.copy()
            for variable in _ISOLATION_VARIABLES:
                environment.pop(variable, None)
            environment.pop("PYTHONPATH", None)
            environment["XDG_STATE_HOME"] = os.fspath(canary_state_home)
            environment["SPEED_OF_CINNAMON_TEST_ROOT"] = os.fspath(test_root)
            environment["SPEED_OF_CINNAMON_TEST_MODE"] = "1"
            environment["SPEED_OF_CINNAMON_LOG_LEVEL"] = "error"
            result = subprocess.run(  # nosec B603
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_alarms.py",
                    "-k",
                    "test_cli_alarm_import_error_uses_module_bootstrap_environment",
                ],
                cwd=_REPO_ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                0,
                result.stderr.decode(errors="replace"),
            )
            self.assertIn(b"OK", result.stderr)
            self.assertEqual(
                tuple(_file_signature(path) for path in canary_paths),
                canary_before,
            )

            isolated_logs = test_root / "state" / "speed-of-cinnamon" / "logs"
            isolated_error_log = isolated_logs / "errors.log"
            isolated_error_markdown = isolated_logs / "errors.md"
            isolated_signatures = tuple(
                _file_signature(path)
                for path in (isolated_error_log, isolated_error_markdown)
            )
            self.assertTrue(all(signature is not None for signature in isolated_signatures))
            error_records = tuple(
                json.loads(line)
                for line in isolated_error_log.read_text(encoding="utf-8").splitlines()
                if line
            )
            self.assertTrue(
                any(
                    record.get("level") == "error"
                    and record.get("event") == "command_exception"
                    and record.get("error_type") == "RuntimeError"
                    and record.get("error_message")
                    == "alarm JSON could not be parsed"
                    for record in error_records
                )
            )
            error_markdown = isolated_error_markdown.read_text(encoding="utf-8")
            self.assertIn("command_exception", error_markdown)
            self.assertIn("alarm JSON could not be parsed", error_markdown)
