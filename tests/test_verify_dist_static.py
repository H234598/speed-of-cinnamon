from __future__ import annotations

import fcntl
import hashlib
import os
import re
import select
import signal
import shutil
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VERIFY_DIST = REPO_ROOT / "scripts" / "verify-dist.sh"

ARCHIVE_PATHS = (
    "README.md",
    "LICENSE",
    "RELEASE-MANIFEST.txt",
    "Makefile",
    "pyproject.toml",
    "packaging/speed-of-cinnamon.spec",
    "docs/architecture.md",
    "docs/cli-reference.md",
    "docs/development.md",
    "docs/fedora-cinnamon-runbook.md",
    "docs/man/speed-of-cinnamon.1",
    "docs/man/speed-of-cinnamon-alarms.1",
    "docs/user-guide.md",
    "docs/wiki/Home.md",
    "files/speed-of-cinnamon@H234598/applet.js",
    "files/speed-of-cinnamon@H234598/metadata.json",
    "files/speed-of-cinnamon@H234598/settings-schema.json",
    "scripts/install-local.sh",
    "scripts/local-model-e2e-acceptance.sh",
    "scripts/export-release-attestations.sh",
    "scripts/real-e2e-acceptance.sh",
    "scripts/verify-release-attestation.py",
    "scripts/safe-local-fs.py",
    "scripts/verify-local-model-e2e-attestation.sh",
    "scripts/verify-real-e2e-attestation.sh",
    "scripts/publish-github-release.sh",
    "scripts/verify-authorship.sh",
    "scripts/verify-rpm.sh",
    "src/speed_of_cinnamon/alarms.py",
    "src/speed_of_cinnamon/cli.py",
    "src/speed_of_cinnamon/setup_plan.py",
    "tests/test_alarms.py",
    "tests/test_ci_static.py",
    "tests/test_cli.py",
)


class VerifyDistStaticTest(unittest.TestCase):
    def _fixture(self, root: Path, extra_python_files: int = 0) -> tuple[Path, Path, Path]:
        repo = root / "repo"
        dist = repo / "dist"
        scripts = repo / "scripts"
        tmp_root = root / "tmp"
        scripts.mkdir(parents=True)
        dist.mkdir(mode=0o700)
        tmp_root.mkdir(mode=0o700)
        shutil.copy2(VERIFY_DIST, scripts / "verify-dist.sh")
        shutil.copy2(REPO_ROOT / "scripts" / "safe-local-fs.py", scripts / "safe-local-fs.py")

        package = root / "package"
        for relative in ARCHIVE_PATHS:
            path = package / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            if relative == "scripts/safe-local-fs.py":
                shutil.copy2(REPO_ROOT / "scripts" / "safe-local-fs.py", path)
            else:
                path.write_bytes(b"")
        generated = package / "src" / "generated"
        generated.mkdir(parents=True, exist_ok=True)
        for index in range(extra_python_files):
            (generated / f"module_{index:04d}.py").write_text("value = 1\n", encoding="ascii")

        archive = dist / "speed-of-cinnamon-test.tar.gz"
        with tarfile.open(archive, "w:gz") as handle:
            handle.add(package, arcname=package.name, recursive=True)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        (dist / f"{archive.name}.sha256").write_text(
            f"{digest}  {archive.name}\n",
            encoding="ascii",
        )
        return repo, dist, tmp_root

    def _environment(self, tmp_root: Path, timeout_seconds: int = 30) -> dict[str, str]:
        environment = os.environ.copy()
        environment["TMPDIR"] = str(tmp_root)
        environment["VERIFY_DIST_TIMEOUT_SECONDS"] = str(timeout_seconds)
        for key in (
            "VERIFY_DIST_PARENT_LOCKED",
            "VERIFY_DIST_PARENT_FD",
            "VERIFY_DIST_PARENT_IDENTITY",
            "VERIFY_DIST_PARENT_CHAIN_IDENTITIES",
            "VERIFY_DIST_DEADLINE_MONOTONIC",
        ):
            environment.pop(key, None)
        return environment

    def _command(self, repo: Path, archive: Path) -> list[str]:
        return [str(repo / "scripts" / "verify-dist.sh"), f"dist/{archive.name}"]

    def _directory_chain(self, directory: Path) -> str:
        paths = list(reversed(directory.parents)) + [directory]
        return ";".join(
            f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}"
            for stat_result in (path.stat() for path in paths)
        )

    def _verification_directories(self, tmp_root: Path) -> list[Path]:
        return sorted(
            path
            for path in tmp_root.iterdir()
            if path.name.startswith("speed-of-cinnamon-dist-verify-")
        )

    def _wait_for_exclusive_lock(
        self,
        process: subprocess.Popen[str],
        dist: Path,
        timeout_seconds: float = 10,
    ) -> int | None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline and process.poll() is None:
            lock_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(lock_fd)
                time.sleep(0.01)
                continue
            if process.poll() is None:
                return lock_fd
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
            return None
        return None

    def _tar_helper_source(self) -> str:
        source = VERIFY_DIST.read_text(encoding="utf-8")
        match = re.search(r"run_duplex_command_bounded\(\) \{.*?<<'PY'\n(.*?)\nPY\n\}", source, re.DOTALL)
        self.assertIsNotNone(match)
        return match.group(1)

    def _run_tar_helper(
        self,
        tar_body: str,
        stdout_limit: int,
        stderr_limit: int,
        deadline_seconds: float = 5,
        environment: dict[str, str] | None = None,
        ready_file: Path | None = None,
        signal_after_ready: signal.Signals | None = None,
        wait_for_pid_gone: Path | None = None,
        wait_for_identities_gone_after_pid: tuple[Path, str] | None = None,
        outer_timeout_seconds: float | None = None,
        pass_fds: tuple[int, ...] = (),
        helper_source: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        helper = self._tar_helper_source() if helper_source is None else helper_source
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            fake_tar = directory / "tar"
            fake_tar.write_text(f"#!/bin/sh\n{tar_body}\n", encoding="utf-8")
            fake_tar.chmod(0o700)
            environment_overrides = {
                "PATH": str(directory),
                "VERIFY_DIST_DEADLINE_MONOTONIC": str(time.monotonic() + deadline_seconds),
            }
            if environment is not None:
                environment_overrides.update(environment)
                if "PATH" in environment:
                    environment_overrides["PATH"] = f"{directory}:{environment_overrides['PATH']}"
            command = [
                "/usr/bin/python3",
                "-I",
                "-S",
                "-B",
                "-",
                "tar",
                str(stdout_limit),
                str(stderr_limit),
                "-tzf",
                "unused.tar.gz",
            ]
            if outer_timeout_seconds is not None:
                command = [
                    "/usr/bin/timeout",
                    "--signal=KILL",
                    str(outer_timeout_seconds),
                    *command,
                ]
            if ready_file is None:
                return subprocess.run(
                    command,
                    input=helper,
                    capture_output=True,
                    text=True,
                    env=environment_overrides,
                    pass_fds=pass_fds,
                    timeout=5,
                    check=False,
                )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment_overrides,
                pass_fds=pass_fds,
            )
            try:
                self.assertIsNotNone(process.stdin)
                process.stdin.write(helper)
                process.stdin.close()
                process.stdin = None
                ready_deadline = time.monotonic() + 5
                while (
                    time.monotonic() < ready_deadline
                    and process.poll() is None
                    and not ready_file.exists()
                ):
                    time.sleep(0.01)
                self.assertTrue(ready_file.exists(), "helper readiness was not observed")
                if wait_for_pid_gone is not None:
                    watched_pid = int(wait_for_pid_gone.read_text(encoding="ascii"))
                    gone_deadline = time.monotonic() + 5
                    while (
                        time.monotonic() < gone_deadline
                        and Path(f"/proc/{watched_pid}").exists()
                    ):
                        time.sleep(0.01)
                    self.assertFalse(Path(f"/proc/{watched_pid}").exists())
                if wait_for_identities_gone_after_pid is not None:
                    identities_file, expected_fragment = (
                        wait_for_identities_gone_after_pid
                    )
                    identities = self._read_pid_identities(identities_file)
                    identities_deadline = time.monotonic() + 2
                    while time.monotonic() < identities_deadline:
                        if not any(
                            self._is_process_with_identity(
                                pid,
                                expected_fragment,
                                expected_start_time=start_time,
                            )
                            for pid, start_time in identities
                        ):
                            break
                        time.sleep(0.01)
                    self.assertFalse(
                        any(
                            self._is_process_with_identity(
                                pid,
                                expected_fragment,
                                expected_start_time=start_time,
                            )
                            for pid, start_time in identities
                        ),
                        "descendants survived 2 seconds after root exit",
                    )
                if signal_after_ready is not None:
                    self.assertIsNone(process.poll())
                    process.send_signal(signal_after_ready)
                stdout, stderr = process.communicate(timeout=5)
                return subprocess.CompletedProcess(
                    command, process.returncode, stdout, stderr
                )
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    @staticmethod
    def _process_info(pid: int) -> tuple[int, str, int, int, bytes] | None:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        except (FileNotFoundError, PermissionError):
            return None
        right_paren = stat.rfind(")")
        if right_paren < 0:
            return None
        fields = stat[right_paren + 2 :].split()
        if len(fields) < 20:
            return None
        try:
            command_path = Path(f"/proc/{pid}/cmdline").read_bytes()
            return (int(pid), fields[0], int(fields[2]), int(fields[19]), command_path)
        except (FileNotFoundError, IndexError, PermissionError, ValueError):
            return None

    @staticmethod
    def _is_process_with_identity(
        pid: int,
        expected_fragment: str,
        expected_pgrp: int | None = None,
        expected_start_time: int | None = None,
    ) -> bool:
        process = VerifyDistStaticTest._process_info(pid)
        if process is None:
            return False
        _pid, state, process_pgrp, process_start_time, process_cmdline = process
        if expected_pgrp is not None and process_pgrp != expected_pgrp:
            return False
        if expected_start_time is not None and process_start_time != expected_start_time:
            return False
        if state == "Z":
            return True
        return expected_fragment.encode("utf-8") in process_cmdline

    @staticmethod
    def _read_pid_identities(path: Path) -> list[tuple[int, int]]:
        identities = []
        for line in path.read_text(encoding="ascii").splitlines():
            pid, start_time = line.split()
            identities.append((int(pid), int(start_time)))
        return identities

    @staticmethod
    def _kill_process_if_identity(pid: int, expected_start_time: int) -> None:
        try:
            pidfd = os.pidfd_open(pid)
        except (AttributeError, OSError, ValueError):
            return
        try:
            process = VerifyDistStaticTest._process_info(pid)
            if process is None or process[3] != expected_start_time:
                return
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL)
            except (AttributeError, ProcessLookupError):
                pass
        finally:
            try:
                os.close(pidfd)
            except OSError:
                pass

    def test_high_load_cleanup_uses_process_identity(self) -> None:
        source = Path(__file__).read_text(encoding="utf-8")
        self.assertIn("_kill_process_if_identity", source)
        fanout_start = source.index(
            "    def test_tar_helper_reaps_1100_term_resistant_descendants"
        )
        watchdog_start = source.index(
            "    def test_tar_helper_watchdog_exception_cleans_double_fork_with_inherited_fds"
        )
        next_test = source.index(
            "    def test_shared_lock_is_released_before_verification_finishes",
            watchdog_start,
        )
        self.assertNotIn("os.kill(", source[fanout_start:watchdog_start])
        self.assertNotIn("os.kill(", source[watchdog_start:next_test])

    def test_cleanup_without_pidfd_skips_raw_pid_signal(self) -> None:
        process = subprocess.Popen(["/usr/bin/sleep", "30"])
        try:
            process_info = self._process_info(process.pid)
            self.assertIsNotNone(process_info)
            assert process_info is not None
            with (
                mock.patch.object(
                    os,
                    "pidfd_open",
                    create=True,
                    side_effect=AttributeError,
                ),
                mock.patch.object(os, "kill") as kill,
            ):
                self._kill_process_if_identity(process.pid, process_info[3])
            kill.assert_not_called()
            self.assertIsNone(process.poll())
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)

    def test_embedded_python_blocks_compile(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")
        blocks = re.findall(r"<<'PY'\n(.*?)\nPY", source, re.DOTALL)
        self.assertGreaterEqual(len(blocks), 7)
        for index, block in enumerate(blocks, 1):
            compile(block, f"<verify-dist-embedded-{index}>", "exec")

        command_blocks = re.findall(r"run_python_bounded -c '\n(.*?)\n' ", source, re.DOTALL)
        self.assertGreaterEqual(len(command_blocks), 1)
        for index, block in enumerate(command_blocks, 1):
            compile(block, f"<verify-dist-command-{index}>", "exec")

    def test_shared_lock_blocks_then_validates_pair_after_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary))
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            lock_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process = subprocess.Popen(
                    self._command(repo, archive),
                    cwd=repo,
                    env=self._environment(tmp_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.25)
                self.assertIsNone(process.poll())
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                stdout, stderr = process.communicate(timeout=15)
            finally:
                os.close(lock_fd)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("Verified", stdout)

    def test_shared_lock_timeout_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary))
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            lock_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process = subprocess.Popen(
                    self._command(repo, archive),
                    cwd=repo,
                    env=self._environment(tmp_root, timeout_seconds=1),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, stderr = process.communicate(timeout=8)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(stdout, "")
            self.assertIn("verify-dist shared lock timed out", stderr)

    def test_parent_rename_to_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary))
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            lock_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                process = subprocess.Popen(
                    self._command(repo, archive),
                    cwd=repo,
                    env=self._environment(tmp_root),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                time.sleep(0.25)
                old_dist = repo / "dist-old"
                dist.rename(old_dist)
                dist.symlink_to(old_dist.name, target_is_directory=True)
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                stdout, stderr = process.communicate(timeout=15)
            finally:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(lock_fd)
            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn("Verified", stdout)
            self.assertIn("parent", stderr.lower())

    def test_forged_marker_and_fd_do_not_bypass_shared_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary))
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            lock_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            forged_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                chain = self._directory_chain(dist)
                environment = self._environment(tmp_root, timeout_seconds=1)
                environment.update(
                    {
                        "VERIFY_DIST_PARENT_LOCKED": "1",
                        "VERIFY_DIST_PARENT_FD": str(forged_fd),
                        "VERIFY_DIST_PARENT_IDENTITY": chain.split(";")[-1],
                        "VERIFY_DIST_PARENT_CHAIN_IDENTITIES": chain,
                        "VERIFY_DIST_DEADLINE_MONOTONIC": str(time.monotonic() + 120),
                    }
                )
                process = subprocess.Popen(
                    self._command(repo, archive),
                    cwd=repo,
                    env=environment,
                    pass_fds=(forged_fd,),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, stderr = process.communicate(timeout=8)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(forged_fd)
                os.close(lock_fd)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(stdout, "")
            self.assertIn("shared lock timed out", stderr)

    def test_forged_internal_deadline_is_hard_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary))
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            parent_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                chain = self._directory_chain(dist)
                process = subprocess.Popen(
                    [
                        str(repo / "scripts" / "verify-dist.sh"),
                        f"dist/{archive.name}",
                        "__verify-dist-locked-v1",
                        str(parent_fd),
                        chain.split(";")[-1],
                        chain,
                        str(time.monotonic() + 3600),
                    ],
                    cwd=repo,
                    env=self._environment(tmp_root),
                    pass_fds=(parent_fd,),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                stdout, stderr = process.communicate(timeout=8)
            finally:
                os.close(parent_fd)
            self.assertNotEqual(process.returncode, 0)
            self.assertEqual(stdout, "")
            self.assertIn("deadline", stderr.lower())

    def test_python_helpers_ignore_path_and_python_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary))
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            fake_bin = Path(temporary) / "fake-bin"
            fake_bin.mkdir()
            fake_python = fake_bin / "python3"
            fake_python.write_text("#!/bin/sh\nexec /bin/sleep 30\n", encoding="ascii")
            fake_python.chmod(0o700)
            (fake_bin / "bash").symlink_to("/bin/bash")
            environment = self._environment(tmp_root)
            environment.update(
                {
                    "PATH": str(fake_bin),
                    "PYTHONHOME": str(fake_bin / "invalid-home"),
                    "PYTHONPATH": str(fake_bin / "invalid-path"),
                    "PYTHONSTARTUP": str(fake_bin / "invalid-startup"),
                }
            )
            result = subprocess.run(
                self._command(repo, archive),
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Verified", result.stdout)

    def test_handoff_revalidate_uses_locked_dist_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            repo, dist, tmp_root = self._fixture(temporary_path)
            alternate_cwd = temporary_path / "alternate-cwd"
            alternate_cwd.mkdir()
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            result = subprocess.run(
                self._command(repo, archive),
                cwd=alternate_cwd,
                env=self._environment(tmp_root),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Verified ./speed-of-cinnamon-test.tar.gz", result.stdout)

    def test_tar_helper_bounds_both_streams_and_preserves_status(self) -> None:
        hung = self._run_tar_helper("exec /bin/sleep 30", 4096, 4096, deadline_seconds=0.25)
        self.assertEqual(hung.returncode, 124)

        flooded = self._run_tar_helper(
            "/usr/bin/dd if=/dev/zero bs=1024 count=128 2>/dev/null\n"
            "/usr/bin/dd if=/dev/zero bs=1024 count=128 >&2\n"
            "exit 7",
            stdout_limit=4096,
            stderr_limit=4096,
        )
        self.assertEqual(flooded.returncode, 125)
        self.assertLessEqual(len(flooded.stdout.encode()), 4096)
        self.assertLessEqual(len(flooded.stderr.encode()), 4096)

        failed = self._run_tar_helper("printf out\nprintf err >&2\nexit 7", 4096, 4096)
        self.assertEqual(failed.returncode, 7)
        self.assertEqual(failed.stdout, "out")
        self.assertEqual(failed.stderr, "err")

    def test_tar_helper_reaps_child_process_group_on_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            grandchild = temporary_path / "verify_dist_fake_grandchild.sh"
            grandchild.write_text("#!/bin/sh\n/usr/bin/sleep 30\n", encoding="utf-8")
            grandchild.chmod(0o700)
            pid_file = temporary_path / "grandchild.pid"
            info_file = temporary_path / "grandchild.info"
            helper_body = """#!/bin/sh
if [ -z "${VERIFY_DIST_TEST_GRANDCHILD_PID_FILE}" ] || [ -z "${VERIFY_DIST_TEST_GRANDCHILD_HELPER}" ]; then
  exit 1
fi
(
  "${VERIFY_DIST_TEST_GRANDCHILD_HELPER}" &
  child=$!
  /usr/bin/python3 - "$child" "${VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE}" <<'PY'
import os
import sys

pid = int(sys.argv[1])
info_path = sys.argv[2]
with open(f"/proc/{pid}/stat", "r", encoding="ascii") as handle:
    stat_line = handle.read()
right_paren = stat_line.rfind(")")
if right_paren < 0:
    raise SystemExit(1)
fields = stat_line[right_paren + 2 :].split()
if len(fields) < 20:
    raise SystemExit(1)
start_time = int(fields[19])
pgrp = os.getpgid(pid)
with open(info_path, "w", encoding="utf-8") as handle:
    handle.write(f"{pid} {pgrp} {start_time}\\n")
PY
      printf '%s\\n' "$child" > "${VERIFY_DIST_TEST_GRANDCHILD_PID_FILE}"
      : > "${VERIFY_DIST_TEST_GRANDCHILD_READY_FILE}"
      wait
) &
/usr/bin/sleep 60
"""
            result = self._run_tar_helper(
                helper_body,
                1024,
                1024,
                deadline_seconds=1.0,
                environment={
                    "VERIFY_DIST_TEST_GRANDCHILD_PID_FILE": str(pid_file),
                    "VERIFY_DIST_TEST_GRANDCHILD_HELPER": str(grandchild),
                    "VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE": str(info_file),
                    "VERIFY_DIST_TEST_GRANDCHILD_READY_FILE": str(
                        temporary_path / "grandchild.ready"
                    ),
                },
                ready_file=temporary_path / "grandchild.ready",
            )
            self.assertEqual(result.returncode, 124)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and not pid_file.exists():
                time.sleep(0.01)
            self.assertTrue(pid_file.exists(), "grandchild pid file was not created")
            grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
            while time.monotonic() < deadline and not info_file.exists():
                time.sleep(0.01)
            self.assertTrue(info_file.exists(), "grandchild info file was not created")
            info_parts = info_file.read_text(encoding="utf-8").strip().split()
            self.assertEqual(len(info_parts), 3)
            _, expected_pgrp, expected_start_time = info_parts
            expected_pgrp = int(expected_pgrp)
            expected_start_time = int(expected_start_time)
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline and self._is_process_with_identity(
                grandchild_pid,
                str(grandchild),
                expected_pgrp,
                expected_start_time,
            ):
                time.sleep(0.02)
            self.assertFalse(
                self._is_process_with_identity(
                    grandchild_pid,
                    str(grandchild),
                    expected_pgrp,
                    expected_start_time,
                ),
                f"grandchild process {grandchild_pid} still alive in process group",
            )
            self.assertFalse(
                Path(f"/proc/{grandchild_pid}").exists(),
                f"grandchild process {grandchild_pid} path still exists",
            )

    def test_tar_helper_reaps_orphaned_setsid_child_after_root_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            grandchild = temporary_path / "verify_dist_orphaned_grandchild.py"
            grandchild.write_text(
                "#!/usr/bin/python3\n"
                "import os\n"
                "import time\n"
                "\n"
                "os.setsid()\n"
                "pid = os.getpid()\n"
                "stat_line = open('/proc/%d/stat' % pid, encoding='ascii').read()\n"
                "right_paren = stat_line.rfind(')')\n"
                "fields = stat_line[right_paren + 2:].split()\n"
                "with open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_PID_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write(str(pid))\n"
                "with open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write('%d %d %d\\n' % (pid, os.getpgid(pid), int(fields[19])))\n"
                "open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_READY_FILE'], 'w').close()\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            grandchild.chmod(0o700)
            pid_file = temporary_path / "orphaned-grandchild.pid"
            info_file = temporary_path / "orphaned-grandchild.info"
            ready_file = temporary_path / "orphaned-grandchild.ready"
            result = self._run_tar_helper(
                """#!/bin/sh
"${VERIFY_DIST_TEST_GRANDCHILD_HELPER}" &
exit 0
""",
                1024,
                1024,
                deadline_seconds=1.0,
                environment={
                    "VERIFY_DIST_TEST_GRANDCHILD_HELPER": str(grandchild),
                    "VERIFY_DIST_TEST_GRANDCHILD_PID_FILE": str(pid_file),
                    "VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE": str(info_file),
                    "VERIFY_DIST_TEST_GRANDCHILD_READY_FILE": str(ready_file),
                },
                ready_file=ready_file,
            )
            self.assertEqual(result.returncode, 124)
            self.assertTrue(pid_file.exists())
            self.assertTrue(info_file.exists())
            grandchild_pid = int(pid_file.read_text(encoding="ascii"))
            info_parts = info_file.read_text(encoding="ascii").strip().split()
            self.assertEqual(len(info_parts), 3)
            _, expected_pgrp, expected_start_time = info_parts
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline and self._is_process_with_identity(
                grandchild_pid,
                str(grandchild),
                int(expected_pgrp),
                int(expected_start_time),
            ):
                time.sleep(0.01)
            self.assertFalse(
                self._is_process_with_identity(
                    grandchild_pid,
                    str(grandchild),
                    int(expected_pgrp),
                    int(expected_start_time),
                )
            )

    def test_tar_helper_external_kill_cleans_backpressured_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            producer = temporary_path / "verify_dist_backpressure.py"
            producer.write_text(
                "#!/usr/bin/python3\n"
                "import os\n"
                "import time\n"
                "\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                "    os.setsid()\n"
                "    pid = os.getpid()\n"
                "    stat_line = open('/proc/%d/stat' % pid, encoding='ascii').read()\n"
                "    right_paren = stat_line.rfind(')')\n"
                "    fields = stat_line[right_paren + 2:].split()\n"
                "    with open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE'], 'w', encoding='ascii') as handle:\n"
                "        handle.write('%d %d %d\\n' % (pid, os.getpgid(pid), int(fields[19])))\n"
                "    open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_READY_FILE'], 'w').close()\n"
                "    while True:\n"
                "        time.sleep(1)\n"
                "while True:\n"
                "    try:\n"
                "        os.write(1, b'x' * 4096)\n"
                "        os.write(2, b'y' * 4096)\n"
                "    except OSError:\n"
                "        os._exit(0)\n"
                "    time.sleep(0.01)\n",
                encoding="ascii",
            )
            producer.chmod(0o700)
            info_file = temporary_path / "backpressure.info"
            ready_file = temporary_path / "backpressure.ready"
            result = self._run_tar_helper(
                f'exec /usr/bin/python3 "{producer}"',
                16 * 1024 * 1024,
                16 * 1024 * 1024,
                deadline_seconds=10,
                environment={
                    "VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE": str(info_file),
                    "VERIFY_DIST_TEST_GRANDCHILD_READY_FILE": str(ready_file),
                },
                ready_file=ready_file,
                outer_timeout_seconds=1.0,
            )
            self.assertEqual(result.returncode, -signal.SIGKILL)
            self.assertTrue(info_file.exists())
            pid, expected_pgrp, expected_start_time = info_file.read_text(
                encoding="ascii"
            ).strip().split()
            watched_pid = int(pid)
            gone_deadline = time.monotonic() + 2
            while time.monotonic() < gone_deadline and Path(
                f"/proc/{watched_pid}"
            ).exists():
                time.sleep(0.01)
            self.assertFalse(
                self._is_process_with_identity(
                    watched_pid,
                    str(producer),
                    int(expected_pgrp),
                    int(expected_start_time),
                )
            )
            self.assertFalse(Path(f"/proc/{watched_pid}").exists())

    def test_tar_helper_root_killing_launcher_is_fail_closed_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            killer = temporary_path / "verify_dist_kill_launcher.py"
            killer.write_text(
                "#!/usr/bin/python3\n"
                "import os\n"
                "import signal\n"
                "import time\n"
                "\n"
                "pid = os.getpid()\n"
                "stat_line = open('/proc/%d/stat' % pid, encoding='ascii').read()\n"
                "right_paren = stat_line.rfind(')')\n"
                "fields = stat_line[right_paren + 2:].split()\n"
                "with open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write('%d %d %d\\n' % (pid, os.getpgid(pid), int(fields[19])))\n"
                "open(os.environ['VERIFY_DIST_TEST_GRANDCHILD_READY_FILE'], 'w').close()\n"
                "os.kill(os.getppid(), signal.SIGKILL)\n"
                "time.sleep(30)\n",
                encoding="ascii",
            )
            killer.chmod(0o700)
            info_file = temporary_path / "launcher-killer.info"
            ready_file = temporary_path / "launcher-killer.ready"
            result = self._run_tar_helper(
                f'exec /usr/bin/python3 "{killer}"',
                1024,
                1024,
                deadline_seconds=1.0,
                environment={
                    "VERIFY_DIST_TEST_GRANDCHILD_INFO_FILE": str(info_file),
                    "VERIFY_DIST_TEST_GRANDCHILD_READY_FILE": str(ready_file),
                },
                ready_file=ready_file,
            )
            self.assertEqual(result.returncode, 124)
            pid, expected_pgrp, expected_start_time = info_file.read_text(
                encoding="ascii"
            ).strip().split()
            watched_pid = int(pid)
            gone_deadline = time.monotonic() + 2
            while time.monotonic() < gone_deadline and Path(
                f"/proc/{watched_pid}"
            ).exists():
                time.sleep(0.01)
            self.assertFalse(
                self._is_process_with_identity(
                    watched_pid,
                    str(killer),
                    int(expected_pgrp),
                    int(expected_start_time),
                )
            )
            self.assertFalse(Path(f"/proc/{watched_pid}").exists())

    def test_tar_helper_keyboard_interrupt_after_reap_cleans_300_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            fanout = temporary_path / "verify_dist_fanout.py"
            fanout.write_text(
                "#!/usr/bin/python3\n"
                "import os\n"
                "import time\n"
                "\n"
                "with open(os.environ['VERIFY_DIST_TEST_ROOT_PID_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write(str(os.getpid()))\n"
                "pids = []\n"
                "for _ in range(300):\n"
                "    pid = os.fork()\n"
                "    if pid == 0:\n"
                "        for descriptor in (0, 1, 2):\n"
                "            try:\n"
                "                os.close(descriptor)\n"
                "            except OSError:\n"
                "                pass\n"
                "        while True:\n"
                "            time.sleep(1)\n"
                "    pids.append(pid)\n"
                "with open(os.environ['VERIFY_DIST_TEST_CHILD_PID_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write('\\n'.join(str(pid) for pid in pids))\n"
                "open(os.environ['VERIFY_DIST_TEST_READY_FILE'], 'w').close()\n"
                "os._exit(0)\n",
                encoding="ascii",
            )
            fanout.chmod(0o700)
            root_pid_file = temporary_path / "fanout-root.pid"
            child_pid_file = temporary_path / "fanout-children.pid"
            ready_file = temporary_path / "fanout.ready"
            result = self._run_tar_helper(
                f'exec /usr/bin/python3 "{fanout}"',
                1024,
                1024,
                deadline_seconds=5.0,
                environment={
                    "VERIFY_DIST_TEST_ROOT_PID_FILE": str(root_pid_file),
                    "VERIFY_DIST_TEST_CHILD_PID_FILE": str(child_pid_file),
                    "VERIFY_DIST_TEST_READY_FILE": str(ready_file),
                },
                ready_file=ready_file,
                signal_after_ready=signal.SIGINT,
                wait_for_pid_gone=root_pid_file,
            )
            self.assertEqual(result.returncode, -signal.SIGINT, result.stderr)
            child_pids = [
                int(value)
                for value in child_pid_file.read_text(encoding="ascii").split()
            ]
            self.assertEqual(len(child_pids), 300)
            self.assertTrue(
                all(not Path(f"/proc/{pid}").exists() for pid in child_pids)
            )

    def test_tar_helper_reaps_1100_term_resistant_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            fanout = temporary_path / "verify_dist_fanout_1100.py"
            fanout.write_text(
                "#!/usr/bin/python3\n"
                "import os\n"
                "import signal\n"
                "import time\n"
                "\n"
                "with open(os.environ['VERIFY_DIST_TEST_ROOT_PID_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write(str(os.getpid()))\n"
                "pids = []\n"
                "for _ in range(1100):\n"
                "    pid = os.fork()\n"
                "    if pid == 0:\n"
                "        for descriptor in (0, 1, 2):\n"
                "            try:\n"
                "                os.close(descriptor)\n"
                "            except OSError:\n"
                "                pass\n"
                "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "        time.sleep(30)\n"
                "        os._exit(0)\n"
                "    pids.append(pid)\n"
                "def process_start_time(pid):\n"
                "    with open(f'/proc/{pid}/stat', encoding='ascii') as stat_handle:\n"
                "        stat = stat_handle.read()\n"
                "    right_paren = stat.rfind(')')\n"
                "    return int(stat[right_paren + 2:].split()[19])\n"
                "with open(os.environ['VERIFY_DIST_TEST_CHILD_PID_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write('\\n'.join(f'{pid} {process_start_time(pid)}' for pid in pids))\n"
                "open(os.environ['VERIFY_DIST_TEST_READY_FILE'], 'w').close()\n"
                "os._exit(0)\n",
                encoding="ascii",
            )
            fanout.chmod(0o700)
            root_pid_file = temporary_path / "fanout-1100-root.pid"
            child_pid_file = temporary_path / "fanout-1100-children.pid"
            ready_file = temporary_path / "fanout-1100.ready"
            child_identities: list[tuple[int, int]] = []
            try:
                result = self._run_tar_helper(
                    f'exec /usr/bin/python3 "{fanout}"',
                    1024,
                    1024,
                    deadline_seconds=5.0,
                    environment={
                        "VERIFY_DIST_TEST_ROOT_PID_FILE": str(root_pid_file),
                        "VERIFY_DIST_TEST_CHILD_PID_FILE": str(child_pid_file),
                        "VERIFY_DIST_TEST_READY_FILE": str(ready_file),
                    },
                    ready_file=ready_file,
                    wait_for_pid_gone=root_pid_file,
                    wait_for_identities_gone_after_pid=(child_pid_file, str(fanout)),
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                child_identities = self._read_pid_identities(child_pid_file)
                self.assertEqual(len(child_identities), 1100)

                def identities_remain() -> bool:
                    return any(
                        self._is_process_with_identity(
                            pid,
                            str(fanout),
                            expected_start_time=start_time,
                        )
                        for pid, start_time in child_identities
                    )

                gone_deadline = time.monotonic() + 2
                while identities_remain() and time.monotonic() < gone_deadline:
                    time.sleep(0.01)
                self.assertFalse(identities_remain())
            finally:
                if not child_identities and child_pid_file.exists():
                    child_identities = self._read_pid_identities(child_pid_file)
                for pid, start_time in child_identities:
                    self._kill_process_if_identity(pid, start_time)

    def test_tar_helper_watchdog_exception_cleans_double_fork_with_inherited_fds(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            double_fork = temporary_path / "verify_dist_double_fork.py"
            double_fork.write_text(
                "#!/usr/bin/python3\n"
                "import os\n"
                "import signal\n"
                "import time\n"
                "\n"
                "first = os.fork()\n"
                "if first:\n"
                "    os._exit(0)\n"
                "os.setsid()\n"
                "second = os.fork()\n"
                "if second:\n"
                "    os._exit(0)\n"
                "for descriptor in (0, 1, 2):\n"
                "    try:\n"
                "        os.close(descriptor)\n"
                "    except OSError:\n"
                "        pass\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "def process_start_time(pid):\n"
                "    with open(f'/proc/{pid}/stat', encoding='ascii') as stat_handle:\n"
                "        stat = stat_handle.read()\n"
                "    right_paren = stat.rfind(')')\n"
                "    return int(stat[right_paren + 2:].split()[19])\n"
                "with open(os.environ['VERIFY_DIST_TEST_CHILD_PID_FILE'], 'w', encoding='ascii') as handle:\n"
                "    handle.write(f'{os.getpid()} {process_start_time(os.getpid())}')\n"
                "open(os.environ['VERIFY_DIST_TEST_READY_FILE'], 'w').close()\n"
                "time.sleep(30)\n",
                encoding="ascii",
            )
            double_fork.chmod(0o700)
            child_pid_file = temporary_path / "double-fork-child.pid"
            exception_file = temporary_path / "watchdog-exception.marker"
            ready_file = temporary_path / "double-fork.ready"
            inherited_fds = [os.open("/dev/null", os.O_RDONLY) for _ in range(1100)]
            child_identities: list[tuple[int, int]] = []
            try:
                helper_source = self._tar_helper_source()
                selector_wait = "            return wait_selector.select(wait_for)\n"
                self.assertEqual(helper_source.count(selector_wait), 1)
                helper_source = helper_source.replace(
                    selector_wait,
                    "            if (os.environ.get(\"VERIFY_DIST_TEST_INJECT_WATCHDOG_EXCEPTION\") == \"1\"\n"
                    "                    and finish_requested):\n"
                    "                with open(os.environ[\"VERIFY_DIST_TEST_EXCEPTION_FILE\"],\n"
                    "                           \"w\", encoding=\"ascii\") as marker:\n"
                    "                    marker.write(\"raised\")\n"
                    "                raise BaseException(\"injected watchdog selector failure\")\n"
                    + selector_wait,
                )
                result = self._run_tar_helper(
                    f'exec /usr/bin/python3 "{double_fork}"',
                    1024,
                    1024,
                    deadline_seconds=5.0,
                    environment={
                        "VERIFY_DIST_TEST_CHILD_PID_FILE": str(child_pid_file),
                        "VERIFY_DIST_TEST_READY_FILE": str(ready_file),
                        "VERIFY_DIST_TEST_INJECT_WATCHDOG_EXCEPTION": "1",
                        "VERIFY_DIST_TEST_EXCEPTION_FILE": str(exception_file),
                    },
                    ready_file=ready_file,
                    pass_fds=tuple(inherited_fds),
                    helper_source=helper_source,
                )
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertEqual(exception_file.read_text(encoding="ascii"), "raised")
                child_identities = self._read_pid_identities(child_pid_file)
                self.assertEqual(len(child_identities), 1)
                child_pid, child_start_time = child_identities[0]
                self.assertFalse(
                    self._is_process_with_identity(
                        child_pid,
                        str(double_fork),
                        expected_start_time=child_start_time,
                    )
                )
            finally:
                if not child_identities and child_pid_file.exists():
                    child_identities = self._read_pid_identities(child_pid_file)
                for pid, start_time in child_identities:
                    self._kill_process_if_identity(pid, start_time)
                for descriptor in inherited_fds:
                    os.close(descriptor)

    def test_shared_lock_is_released_before_verification_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary), extra_python_files=1800)
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            process = subprocess.Popen(
                self._command(repo, archive),
                cwd=repo,
                env=self._environment(tmp_root, timeout_seconds=30),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            lock_fd = None
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline and process.poll() is None:
                    if self._verification_directories(tmp_root):
                        break
                    time.sleep(0.01)
                self.assertTrue(self._verification_directories(tmp_root))
                lock_fd = self._wait_for_exclusive_lock(process, dist)
                self.assertIsNotNone(lock_fd)
                stdout, stderr = process.communicate(timeout=30)
            finally:
                if lock_fd is not None:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn("Verified", stdout)

    def test_sigkill_residue_is_swept_without_foreign_deletion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary), extra_python_files=1800)
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            process = subprocess.Popen(
                self._command(repo, archive),
                cwd=repo,
                env=self._environment(tmp_root, timeout_seconds=30),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            residue = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                directories = self._verification_directories(tmp_root)
                if directories:
                    residue = directories[0]
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(residue)
            process.send_signal(signal.SIGKILL)
            process.wait(timeout=10)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            assert residue is not None
            self.assertTrue(residue.exists())
            old_time = time.time() - 2 * 60 * 60
            os.utime(residue, (old_time, old_time))
            recent = tmp_root / "speed-of-cinnamon-dist-verify-NEW123"
            recent.mkdir(mode=0o700)
            foreign = tmp_root / "foreign-verification-residue"
            foreign.mkdir(mode=0o700)
            os.utime(foreign, (old_time, old_time))

            result = subprocess.run(
                self._command(repo, archive),
                cwd=repo,
                env=self._environment(tmp_root),
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(residue.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(foreign.exists())

    def test_sigterm_residue_is_not_removed_without_identity_and_age_proof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary), extra_python_files=1800)
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            process = subprocess.Popen(
                self._command(repo, archive),
                cwd=repo,
                env=self._environment(tmp_root, timeout_seconds=30),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            residue = None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                directories = self._verification_directories(tmp_root)
                if directories:
                    residue = directories[0]
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertIsNotNone(residue)
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=15)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            assert residue is not None
            self.assertTrue(residue.exists())

    def test_cleanup_failure_after_success_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo, dist, tmp_root = self._fixture(Path(temporary), extra_python_files=1800)
            archive = dist / "speed-of-cinnamon-test.tar.gz"
            process = subprocess.Popen(
                self._command(repo, archive),
                cwd=repo,
                env=self._environment(tmp_root, timeout_seconds=30),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertIsNotNone(process.stdout)
            ready, _writable, _exceptional = select.select([process.stdout], [], [], 30)
            self.assertTrue(ready)
            first_line = process.stdout.readline()
            self.assertIn("Verified", first_line)
            directories = self._verification_directories(tmp_root)
            self.assertEqual(len(directories), 1)
            current = directories[0]
            moved = tmp_root / "moved-after-verify"
            current.rename(moved)
            current.symlink_to(moved.name, target_is_directory=True)
            rest_stdout, stderr = process.communicate(timeout=15)
            stdout = first_line + rest_stdout
            self.assertEqual(process.returncode, 1)
            self.assertIn("Verified", stdout)
            self.assertIn("cleanup failed", stderr)

    def test_verification_cleanup_requires_expected_identity(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn(
            'tmp_dir_identity="$("${safe_fs_cmd[@]}" identity verify-dist "${tmp_dir}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${tmp_dir_identity}"', source)

    def test_snapshot_copy_enforces_archive_size_limit(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn(
            'copy-file verify-dist "${tarball}" "${tarball_snapshot}" 0644 \\\n  --max-bytes "${MAX_DIST_ARCHIVE_BYTES}"',
            source,
        )
        self.assertIn(
            'snapshot_bytes="$(run_command_bounded stat -c \'%s\' "${tarball_snapshot}")"',
            source,
        )
        self.assertIn('raw = handle.read(4097)', source)

    def test_member_limit_is_checked_during_tar_iteration(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn("for member in archive:\n", source)
        self.assertNotIn("for member in archive.getmembers():", source)

    def test_extracted_tree_scan_is_bounded_and_not_rglob(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn("MAX_PACKAGE_ENTRIES = 100_000", source)
        self.assertIn("os.scandir(current)", source)
        self.assertIn("archive expansion entry budget exceeded", source)
        self.assertNotIn("package_root.rglob", source)

    def test_archive_tools_are_time_and_output_bounded(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn("readonly MAX_DIST_LISTING_BYTES=$((16 * 1024 * 1024))", source)
        self.assertIn("readonly DIST_VERIFY_TIMEOUT_SECONDS=120", source)
        self.assertIn("timeout; do", source)
        self.assertIn("run_tar_bounded()", source)
        self.assertIn("run_python_bounded()", source)
        self.assertIn('timeout --signal=TERM --kill-after=10s "${DIST_VERIFY_TIMEOUT_SECONDS}s" tar "$@"', source)
        self.assertIn('timeout --signal=TERM --kill-after=10s "${DIST_VERIFY_TIMEOUT_SECONDS}s" python3 "$@"', source)
        self.assertIn('tar_listing_bytes="$(stat -c \'%s\' "${tar_listing}")"', source)
        self.assertIn("find \"${tmp_dir}\" -mindepth 1 -maxdepth 1 -type d -print0", source)


if __name__ == "__main__":
    unittest.main()
