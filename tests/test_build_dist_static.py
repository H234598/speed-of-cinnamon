from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_DIST = REPO_ROOT / "scripts" / "build-dist.sh"
VERIFY_DIST = REPO_ROOT / "scripts" / "verify-dist.sh"


class BuildDistStaticTest(unittest.TestCase):
    def _build_function(self) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        function_start = source.index("build_staged_tarball() {")
        start = source.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        end = source.index("\nPY\n}", start)
        return source[function_start : end + len("\nPY\n}")]

    def _finalizer_program(self) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        function_start = source.index("replace_with_finalize_lock() {")
        start = source.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        end = source.index("\nPY\n}", start)
        return source[start:end]

    def _finalizer_function(self) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        function_start = source.index("replace_with_finalize_lock() {")
        end = source.index("\nPY\n}", function_start) + len("\nPY\n}")
        return source[function_start:end]

    def _run_build_stage(
        self,
        function: str,
        work_dir: Path,
        package: str,
        stage: Path,
        identity: str,
        *,
        env: dict[str, str] | None = None,
        timeout: int = 3,
        max_archive_bytes: int = 128 * 1024 * 1024,
    ) -> subprocess.CompletedProcess[str]:
        checksum = stage.with_name(f"{stage.name}.sha256")
        checksum.touch(mode=0o600)
        checksum_stat = checksum.stat()
        checksum_identity = f"{checksum_stat.st_dev}:{checksum_stat.st_ino}:{checksum_stat.st_mode}"
        script = (
            "set -euo pipefail\n"
            f"DIST_BUILD_TIMEOUT_SECONDS={timeout}\n"
            "DIST_BUILD_KILL_AFTER_SECONDS=1\n"
            f"DIST_MAX_ARCHIVE_BYTES={max_archive_bytes}\n"
            f"{function}\n"
            'build_staged_tarball "$1" "$2" "$3" "$4" "$5" "$6"\n'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "build-stage",
                str(work_dir),
                package,
                str(stage),
                identity,
                str(checksum),
                checksum_identity,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=None if env is None else {**os.environ, **env},
            check=False,
            timeout=10,
        )

    def _write_executable(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")
        path.chmod(0o700)

    @staticmethod
    def _pid_is_live(pid: int) -> bool:
        if pid <= 1:
            return False
        try:
            record = Path(f"/proc/{pid}/stat").read_bytes()
        except OSError:
            return False
        marker = record.rfind(b") ")
        if marker < 0:
            return False
        fields = record[marker + 2 :].split()
        return bool(fields) and fields[0] not in (b"Z", b"X")

    def _startup_top(
        self,
        *,
        timeout_seconds: int = 2,
        kill_after_seconds: int = 1,
        reserve_ns: int = 500_000_000,
        probe_timeout_seconds: float | None = None,
    ) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        top = source[: source.index("\nrepo_dir=")]
        top = top.replace("DIST_BUILD_TIMEOUT_SECONDS=3600", f"DIST_BUILD_TIMEOUT_SECONDS={timeout_seconds}", 1)
        top = top.replace("DIST_BUILD_KILL_AFTER_SECONDS=2", f"DIST_BUILD_KILL_AFTER_SECONDS={kill_after_seconds}", 1)
        top = top.replace("DIST_BUILD_STARTUP_RESERVE_NS=3000000000", f"DIST_BUILD_STARTUP_RESERVE_NS={reserve_ns}", 1)
        if probe_timeout_seconds is not None:
            top = top.replace(
                "DIST_BUILD_INITIAL_PROBE_TIMEOUT_SECONDS=0.25",
                f"DIST_BUILD_INITIAL_PROBE_TIMEOUT_SECONDS={probe_timeout_seconds}",
                1,
            )
        return top

    def _write_startup_fd_launcher(self, path: Path) -> None:
        self._write_executable(
            path,
            "#!/usr/bin/env bash\n"
            "exec 198<&3\n"
            "exec 3<&-\n"
            "exec \"$@\"\n",
        )

    def _start_valid_handoff(
        self,
        harness: Path,
        launcher: Path,
        *,
        deadline: int | None = None,
        stdout: int | None = subprocess.DEVNULL,
        stderr: int | None = subprocess.DEVNULL,
    ) -> tuple[subprocess.Popen[str], int]:
        lock_fd = os.memfd_create("test-build-dist-handoff", os.MFD_CLOEXEC)
        os.fchmod(lock_fd, 0o600)
        handoff_stat = os.fstat(lock_fd)
        handoff_identity = ":".join(
            str(value)
            for value in (
                handoff_stat.st_dev,
                handoff_stat.st_ino,
                handoff_stat.st_mode,
                handoff_stat.st_uid,
                handoff_stat.st_nlink,
            )
        )
        nonce = "b" * 64
        requested_deadline = deadline or time.monotonic_ns() + 10**18
        payload = (
            f"{nonce}\n{requested_deadline}\n{handoff_identity}\n{os.getpid()}\n".encode("ascii")
        )
        view = memoryview(payload)
        while view:
            written = os.write(lock_fd, view)
            self.assertGreater(written, 0)
            view = view[written:]
        os.fsync(lock_fd)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        child_fd = os.open(f"/proc/self/fd/{lock_fd}", os.O_RDWR | os.O_CLOEXEC)
        saved_fd3 = None
        try:
            try:
                saved_fd3 = os.dup(3)
            except OSError:
                saved_fd3 = None
            if child_fd == 3:
                child_fd = os.dup(child_fd)
            os.dup2(child_fd, 3)
            process = subprocess.Popen(
                [str(launcher), str(harness)],
                pass_fds=(3,),
                stdout=stdout,
                stderr=stderr,
                text=stdout == subprocess.PIPE or stderr == subprocess.PIPE,
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                start_new_session=True,
            )
        finally:
            if saved_fd3 is None:
                os.close(3)
            else:
                os.dup2(saved_fd3, 3)
                os.close(saved_fd3)
            os.close(child_fd)
        return process, lock_fd

    def _run_finalizer(
        self,
        program: str,
        safe_fs: Path,
        lock: Path,
        stage: Path | str,
        final: Path | str,
        checksum_stage: Path | str,
        checksum_final: Path | str,
        *,
        finalize_timeout: int = 30,
        lock_timeout: int = 2,
        env: dict[str, str] | None = None,
        expected_metadata: tuple[str, str, str, str, str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if expected_metadata is None and stage and checksum_stage:
            stage_path = Path(stage)
            checksum_stage_path = Path(checksum_stage)
            if stage_path.is_file() and checksum_stage_path.is_file():
                expected_metadata = self._file_metadata(stage_path) + self._file_metadata(checksum_stage_path)
        if expected_metadata is None:
            expected_metadata = ("", "", "", "", "", "")
        return subprocess.run(
            [
                sys.executable,
                "-",
                str(lock),
                str(safe_fs),
                str(stage),
                str(final),
                str(checksum_stage),
                str(checksum_final),
                *expected_metadata,
                str(finalize_timeout),
                str(lock_timeout),
            ],
            input=program,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=None if env is None else {**os.environ, **env},
            check=False,
            timeout=30,
        )

    def _run_finalizer_shell(
        self,
        function: str,
        safe_fs: Path,
        lock: Path,
        stage: Path,
        final: Path,
        checksum_stage: Path,
        checksum_final: Path,
        metadata: tuple[str, str, str, str, str, str],
        *,
        phase: str,
    ) -> subprocess.CompletedProcess[str]:
        script = (
            "set +e\n"
            'safe_fs="$1"\n'
            "DIST_FINALIZE_TIMEOUT_SECONDS=30\n"
            "DIST_FINALIZE_KILL_AFTER_SECONDS=2\n"
            "DIST_FINALIZE_LOCK_TIMEOUT_SECONDS=2\n"
            f"{function}\n"
            'replace_with_finalize_lock "$2" "$3" "$4" "$5" "$6" '
            '"$7" "$8" "$9" "${10}" "${11}" "${12}"\n'
            'status=$?\nprintf "status=%s\\n" "$status"\n'
            'exit "$status"\n'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "finalizer-shell",
                str(safe_fs),
                str(lock),
                str(stage),
                str(final),
                str(checksum_stage),
                str(checksum_final),
                *metadata,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**os.environ, "BUILD_DIST_ABORT_PHASE": phase},
            check=False,
            timeout=10,
        )

    def _write_pair(self, archive: Path, checksum: Path, payload: bytes, name: str) -> None:
        archive.write_bytes(payload)
        checksum.write_text(
            f"{hashlib.sha256(payload).hexdigest()}  {name}\n",
            encoding="utf-8",
        )

    def _file_metadata(self, path: Path) -> tuple[str, str, str]:
        path_stat = path.stat()
        identity = f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"
        payload = path.read_bytes()
        return identity, str(path_stat.st_size), hashlib.sha256(payload).hexdigest()

    def _assert_pair(self, archive: Path, checksum: Path) -> None:
        name = archive.name
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.assertEqual(checksum.read_text(encoding="utf-8"), f"{digest}  {name}\n")

    def _run_recovery(
        self,
        program: str,
        safe_fs: Path,
        lock: Path,
    ) -> subprocess.CompletedProcess[str]:
        return self._run_finalizer(program, safe_fs, lock, "", "", "", "")

    def _workspace_sweep_program(self) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        marker = (
            'if ! timeout --signal=TERM --kill-after='
            '"${DIST_STARTUP_SWEEP_KILL_AFTER_SECONDS}s"'
        )
        marker_start = source.index(marker)
        start = source.index("<<'PY'\n", marker_start) + len("<<'PY'\n")
        end = source.index("\nPY\nthen", start)
        return source[start:end]

    def _run_workspace_sweep(
        self,
        program: str,
        workspace_base: Path,
        safe_fs: Path,
        *,
        timeout_seconds: int = 30,
        max_entries: int = 256,
        max_stale: int = 32,
        max_age_seconds: int = 24 * 60 * 60,
        process_timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-",
                str(workspace_base),
                str(workspace_base),
                str(safe_fs),
                str(timeout_seconds),
                str(max_entries),
                str(max_stale),
                str(max_age_seconds),
            ],
            input=program,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=process_timeout,
        )

    def _tmp_validation_program(self) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        start = source.index("validate_dist_tmp_root() {")
        end = source.index("\n}\n\nwork_root=", start) + 2
        return source[start:end]

    def _run_tmp_validation(self, program: str, root: Path) -> subprocess.CompletedProcess[str]:
        script = (
            "set -u\n"
            'repo_dir="$1"\n'
            'work_root=""\n'
            f"{program}\n"
            'if validate_dist_tmp_root "$2"; then exit 0; else exit 1; fi\n'
        )
        return subprocess.run(
            ["bash", "-c", script, "tmp", str(REPO_ROOT), str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=10,
        )

    def _write_cleanup_swap_wrapper(self, path: Path, *, real: Path) -> None:
        path.write_text(
            "#!/usr/bin/env bash\n"
            "set -u\n"
            "if [[ \"${1:-}\" == remove-leaf ]]; then\n"
            "    target=\"$3\"\n"
            "    replacement=\"${target}.replacement\"\n"
            "    printf 'replacement\\n' > \"${replacement}\"\n"
            "    python3 - \"${target}\" \"${replacement}\" <<'PY'\n"
            "import os, sys\n"
            "os.replace(sys.argv[2], sys.argv[1])\n"
            "PY\n"
            "fi\n"
            f"exec python3 {str(real)!r} \"$@\"\n",
            encoding="utf-8",
        )
        path.chmod(0o700)

    def _write_cleanup_failure_wrapper(self, path: Path, *, real: Path) -> None:
        path.write_text(
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'remove':\n"
            "    raise SystemExit(77)\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *args])\n",
            encoding="utf-8",
        )

    def _write_delayed_safe_fs_wrapper(self, path: Path, *, real: Path, delay: float) -> None:
        path.write_text(
            "import os, sys, time\n"
            f"time.sleep({delay!r})\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )

    def _write_no_external_stage_rename_wrapper(self, path: Path, *, real: Path) -> None:
        path.write_text(
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['replace', 'build-dist stage claim']:\n"
            "    raise SystemExit('external stage rename forbidden')\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *args])\n",
            encoding="utf-8",
        )

    def _cleanup_program(self) -> str:
        source = BUILD_DIST.read_text(encoding="utf-8")
        start = source.index("cleanup() {")
        end = source.index("\n}\ntrap cleanup EXIT", start) + 2
        return source[start:end]

    def _run_exit_cleanup(
        self,
        cleanup_function: str,
        stub: Path,
        tarball: Path,
        checksum: Path,
        staging_dir: Path,
        work_dir: Path,
        primary_status: int,
        identities: tuple[str, str, str, str] | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        tarball_identity, checksum_identity, staging_dir_identity, work_dir_identity = identities or (
            "tarball-id",
            "checksum-id",
            "staging-dir-id",
            "work-dir-id",
        )
        script = (
            "set -u\n"
            'safe_fs_cmd=(bash "$1")\n'
            "stop_handoff_monitor() { :; }\n"
            'staging_tarball="$2"\n'
            'staging_tarball_identity="$7"\n'
            'staging_checksum="$3"\n'
            'staging_checksum_identity="$8"\n'
            'dist_staging_dir="$4"\n'
            'dist_staging_dir_identity="$9"\n'
            'work_dir="$5"\n'
            'work_dir_identity="${10}"\n'
            f"{cleanup_function}\n"
            "trap cleanup EXIT\n"
            'exit "$6"\n'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "cleanup",
                str(stub),
                str(tarball),
                str(checksum),
                str(staging_dir),
                str(work_dir),
                str(primary_status),
                tarball_identity,
                checksum_identity,
                staging_dir_identity,
                work_dir_identity,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=None if env is None else {**os.environ, **env},
            check=False,
            timeout=10,
        )

    def _prepare_transaction(self, dist: Path, name: str = "archive.tar.gz") -> tuple[Path, Path, Path]:
        transaction = dist / ".build-dist-transaction-archive-123456"
        transaction.mkdir(mode=0o700)
        stage = transaction / "stage.tar.gz"
        checksum_stage = transaction / "stage.tar.gz.sha256"
        payload = b"new archive\n"
        self._write_pair(stage, checksum_stage, payload, name)
        return transaction, stage, checksum_stage

    def test_dist_first_publish_and_upgrade_preserve_pair_hash(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            final = dist / "archive.tar.gz"
            checksum_final = dist / "archive.tar.gz.sha256"
            _, stage, checksum_stage = self._prepare_transaction(dist)

            first = self._run_finalizer(program, safe_fs, lock, stage, final, checksum_stage, checksum_final)

            self.assertEqual(first.returncode, 0, first.stderr)
            self._assert_pair(final, checksum_final)
            self.assertEqual(list(dist.glob(".build-dist-transaction-*")), [])

            _, stage, checksum_stage = self._prepare_transaction(dist)
            second = self._run_finalizer(program, safe_fs, lock, stage, final, checksum_stage, checksum_final)

            self.assertEqual(second.returncode, 0, second.stderr)
            self._assert_pair(final, checksum_final)
            self.assertEqual(list(dist.glob(".build-dist-transaction-*")), [])

    def test_dist_external_stage_is_copied_not_renamed_across_filesystems(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        if not Path("/dev/shm").is_dir():
            self.skipTest("cross-filesystem temporary root unavailable")
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory(dir="/dev/shm") as cross_fs_tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            external = Path(cross_fs_tmp)
            stage = external / "stage.tar.gz"
            checksum_stage = external / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"cross-fs archive\n", "archive.tar.gz")
            if stage.stat().st_dev == dist.stat().st_dev:
                self.skipTest("temporary roots are on one filesystem")
            final = dist / "archive.tar.gz"
            checksum_final = dist / "archive.tar.gz.sha256"
            guarded_safe_fs = root / "safe-fs-no-external-rename.py"
            self._write_no_external_stage_rename_wrapper(guarded_safe_fs, real=safe_fs)

            result = self._run_finalizer(
                program,
                guarded_safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                checksum_stage,
                checksum_final,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self._assert_pair(final, checksum_final)
            self.assertTrue(stage.exists())
            self.assertTrue(checksum_stage.exists())
            self.assertEqual(list(dist.glob(".build-dist-transaction-*")), [])

    def test_dist_external_stage_replacement_after_producer_fails_closed(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            checksum_stage = root / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"producer archive\n", "archive.tar.gz")
            expected_metadata = self._file_metadata(stage) + self._file_metadata(checksum_stage)
            replacement = root / "replacement.tar.gz"
            replacement.write_bytes(b"replaced archive\n")
            replacement.replace(stage)

            result = self._run_finalizer(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                dist / "archive.tar.gz",
                checksum_stage,
                dist / "archive.tar.gz.sha256",
                expected_metadata=expected_metadata,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse((dist / "archive.tar.gz").exists())
            self.assertTrue(stage.exists())
            self.assertEqual(stage.read_bytes(), b"replaced archive\n")

    def test_dist_external_checksum_replacement_after_producer_fails_closed(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            checksum_stage = root / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"producer archive\n", "archive.tar.gz")
            expected_metadata = self._file_metadata(stage) + self._file_metadata(checksum_stage)
            replacement = root / "replacement.sha256"
            replacement.write_bytes(b"replaced checksum\n")
            replacement.replace(checksum_stage)

            result = self._run_finalizer(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                dist / "archive.tar.gz",
                checksum_stage,
                dist / "archive.tar.gz.sha256",
                expected_metadata=expected_metadata,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse((dist / "archive.tar.gz").exists())
            self.assertTrue(stage.exists())
            self.assertEqual(checksum_stage.read_bytes(), b"replaced checksum\n")

    def test_dist_external_stage_replacement_after_fd_processing_fails_closed(self) -> None:
        program = self._finalizer_program()
        marker = "        source_after = os.fstat(source_fd)\n"
        self.assertEqual(program.count(marker), 1)
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            checksum_stage = root / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"producer archive\n", "archive.tar.gz")
            expected_metadata = self._file_metadata(stage) + self._file_metadata(checksum_stage)
            replacement = root / "stage.tar.gz.replacement"
            replacement.write_bytes(b"post-open replacement\n")
            program = program.replace(
                marker,
                marker + f"        os.replace({str(replacement)!r}, {str(stage)!r})\n",
                1,
            )

            result = self._run_finalizer(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                dist / "archive.tar.gz",
                checksum_stage,
                dist / "archive.tar.gz.sha256",
                expected_metadata=expected_metadata,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertFalse((dist / "archive.tar.gz").exists())
            self.assertEqual(stage.read_bytes(), b"post-open replacement\n")
            self.assertEqual(len(list(dist.glob(".build-dist-transaction-*"))), 1)

    def test_dist_external_stage_growth_never_exceeds_archive_limit(self) -> None:
        program = self._finalizer_program().replace(
            "MAX_ARCHIVE_BYTES = 128 * 1024 * 1024",
            "MAX_ARCHIVE_BYTES = 1024",
            1,
        )
        marker = "        source_signature = _file_signature(source_stat)\n"
        self.assertEqual(program.count(marker), 1)
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            checksum_stage = root / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"a" * 1024, "archive.tar.gz")
            expected_metadata = self._file_metadata(stage) + self._file_metadata(checksum_stage)
            program = program.replace(
                marker,
                marker
                + f"        with open({str(stage)!r}, 'ab') as growth:\n"
                + "            growth.write(b'x')\n",
                1,
            )

            result = self._run_finalizer(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                dist / "archive.tar.gz",
                checksum_stage,
                dist / "archive.tar.gz.sha256",
                expected_metadata=expected_metadata,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertIn("exceeds maximum size", result.stderr)
            transactions = list(dist.glob(".build-dist-transaction-*"))
            self.assertEqual(len(transactions), 1)
            copied_stage = transactions[0] / "stage.tar.gz"
            self.assertLessEqual(copied_stage.stat().st_size, 1024)

    def test_dist_build_deadline_covers_consumer_fsync_and_hash(self) -> None:
        build_function = self._build_function()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = root / "work"
            work_dir.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            stage.touch(mode=0o600)
            identity = subprocess.run(
                [sys.executable, str(safe_fs), "identity", "build-dist", str(stage), "--kind", "file"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            fake_bin = root / "bin"
            fake_bin.mkdir(mode=0o700)
            normal_tar = fake_bin / "tar"
            self._write_executable(normal_tar, "#!/bin/sh\nprintf 'tar output\\n'\n")
            blocking_tar = fake_bin / "tar-blocking"
            self._write_executable(blocking_tar, "#!/bin/sh\nexec yes x\n")
            env = {"PATH": f"{fake_bin}:{os.environ['PATH']}"}

            normal = self._run_build_stage(
                build_function,
                work_dir,
                "package",
                stage,
                identity,
                env=env,
            )
            self.assertEqual(normal.returncode, 0, normal.stderr)
            metadata = tuple(normal.stdout.strip().split("\t"))
            self.assertEqual(len(metadata), 6)
            self.assertEqual(metadata, self._file_metadata(stage) + self._file_metadata(stage.with_name("stage.tar.gz.sha256")))
            self.assertEqual(stage.read_bytes(), b"tar output\n")

            fault_programs = {
                "consumer": (
                    build_function.replace(
                        "        events = selector.select(remaining)",
                        "        time.sleep(10)\n        events = selector.select(remaining)",
                        1,
                    ),
                    blocking_tar,
                ),
                "fsync": (
                    build_function.replace(
                        "    os.fsync(stage_fd)",
                        "    time.sleep(10)\n    os.fsync(stage_fd)",
                        1,
                    ),
                    normal_tar,
                ),
                "hash": (
                    build_function.replace(
                        "        digest.update(chunk)",
                        "        time.sleep(10)\n        digest.update(chunk)",
                        1,
                    ),
                    normal_tar,
                ),
            }
            for fault, (fault_function, tar_command) in fault_programs.items():
                with self.subTest(fault=fault):
                    using_blocking_tar = tar_command == blocking_tar
                    if using_blocking_tar:
                        normal_tar.rename(fake_bin / "tar-normal")
                        (fake_bin / "tar").symlink_to(blocking_tar.name)
                    started = time.monotonic()
                    result = self._run_build_stage(
                        fault_function,
                        work_dir,
                        "package",
                        stage,
                        identity,
                        env=env,
                    )
                    elapsed = time.monotonic() - started
                    if using_blocking_tar:
                        (fake_bin / "tar").unlink()
                        (fake_bin / "tar-normal").rename(normal_tar)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertLess(elapsed, 5, result.stderr)

    def test_dist_build_rejects_archive_stdout_over_hard_limit(self) -> None:
        build_function = self._build_function()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = root / "work"
            work_dir.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            stage.touch(mode=0o600)
            stage_identity = self._file_metadata(stage)[0]
            fake_bin = root / "bin"
            fake_bin.mkdir(mode=0o700)
            self._write_executable(
                fake_bin / "tar",
                "#!/bin/sh\nexec python3 -c 'import sys; sys.stdout.buffer.write(bytes([120]) * 4096)'\n",
            )
            result = self._run_build_stage(
                build_function,
                work_dir,
                "package",
                stage,
                stage_identity,
                env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
                max_archive_bytes=1024,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stderr), 2000)
            self.assertLessEqual(stage.stat().st_size, 1024)

    def test_dist_build_bounds_tar_stderr(self) -> None:
        build_function = self._build_function()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = root / "work"
            work_dir.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            stage.touch(mode=0o600)
            stage_identity = self._file_metadata(stage)[0]
            fake_bin = root / "bin"
            fake_bin.mkdir(mode=0o700)
            self._write_executable(
                fake_bin / "tar",
                "#!/bin/sh\nexec python3 -c 'import sys; sys.stderr.write(chr(101) * 1048576); sys.exit(23)'\n",
            )
            result = self._run_build_stage(
                build_function,
                work_dir,
                "package",
                stage,
                stage_identity,
                env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertLess(len(result.stderr), 10000)
            self.assertIn("tar stderr truncated", result.stderr)

    def test_dist_partial_final_pair_is_replaced_as_one_recoverable_transaction(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            final = dist / "archive.tar.gz"
            final.write_bytes(b"old archive\n")
            checksum_final = dist / "archive.tar.gz.sha256"
            _, stage, checksum_stage = self._prepare_transaction(dist)

            result = self._run_finalizer(program, safe_fs, lock, stage, final, checksum_stage, checksum_final)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final.read_bytes(), b"new archive\n")
            self._assert_pair(final, checksum_final)

    def test_dist_mismatched_existing_pair_fails_closed(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            final = dist / "archive.tar.gz"
            checksum_final = dist / "archive.tar.gz.sha256"
            old = b"old archive\n"
            self._write_pair(final, checksum_final, old, final.name)
            checksum_final.write_text("0" * 64 + "  archive.tar.gz\n", encoding="ascii")
            transaction, stage, checksum_stage = self._prepare_transaction(dist)

            result = self._run_finalizer(program, safe_fs, lock, stage, final, checksum_stage, checksum_final)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(final.read_bytes(), old)
            self.assertEqual(checksum_final.read_text(encoding="ascii"), "0" * 64 + "  archive.tar.gz\n")
            self.assertTrue(transaction.exists())
            self.assertFalse((transaction / "journal").exists())

    def test_dist_committed_cleanup_failure_leaves_recoverable_journal(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            final = dist / "archive.tar.gz"
            old = b"old archive\n"
            self._write_pair(final, dist / "archive.tar.gz.sha256", old, final.name)
            _, stage, checksum_stage = self._prepare_transaction(dist)
            failing_safe_fs = root / "safe-fs-failure.py"
            self._write_cleanup_failure_wrapper(failing_safe_fs, real=safe_fs)

            result = self._run_finalizer(
                program,
                failing_safe_fs,
                lock,
                stage,
                final,
                checksum_stage,
                dist / "archive.tar.gz.sha256",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self._assert_pair(final, dist / "archive.tar.gz.sha256")
            self.assertTrue(list(dist.glob(".build-dist-transaction-*")))
            recovered = self._run_recovery(program, safe_fs, lock)
            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertEqual(list(dist.glob(".build-dist-transaction-*")), [])

    def test_dist_recovery_pair_replacement_after_read_fails_closed(self) -> None:
        program = self._finalizer_program()
        marker = '        try:\n            checksum_text = checksum_data.decode("ascii")\n'
        self.assertEqual(program.count(marker), 1)
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            final = dist / "archive.tar.gz"
            checksum_final = dist / "archive.tar.gz.sha256"
            transaction, stage, checksum_stage = self._prepare_transaction(dist)

            killed = self._run_finalizer(
                program,
                safe_fs,
                lock,
                stage,
                final,
                checksum_stage,
                checksum_final,
                env={"BUILD_DIST_ABORT_PHASE": "committed"},
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)

            replacement_archive = root / "replacement.tar.gz"
            replacement_checksum = root / "replacement.tar.gz.sha256"
            self._write_pair(
                replacement_archive,
                replacement_checksum,
                b"same-uid replacement\n",
                final.name,
            )
            recovery_program = program.replace(
                marker,
                f"        os.replace({str(replacement_archive)!r}, archive_name, dst_dir_fd=directory_fd)\n"
                f"        os.replace({str(replacement_checksum)!r}, checksum_name, dst_dir_fd=directory_fd)\n"
                + marker,
                1,
            )

            recovered = self._run_recovery(recovery_program, safe_fs, lock)

            self.assertNotEqual(recovered.returncode, 0, recovered.stderr)
            self.assertTrue(transaction.exists())
            self.assertEqual(final.read_bytes(), b"same-uid replacement\n")
            self._assert_pair(final, checksum_final)

    def test_dist_finalizer_uses_one_absolute_deadline_for_multiple_safe_fs_calls(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            stage = root / "stage.tar.gz"
            stage.write_bytes(b"new archive\n")
            final = dist / "archive.tar.gz"
            final.write_bytes(b"old archive\n")
            checksum_stage = root / "stage.sha256"
            checksum_stage.write_text(
                f"{hashlib.sha256(stage.read_bytes()).hexdigest()}  archive.tar.gz\n",
                encoding="utf-8",
            )
            checksum_final = dist / "archive.tar.gz.sha256"
            checksum_final.write_text(
                f"{hashlib.sha256(final.read_bytes()).hexdigest()}  archive.tar.gz\n",
                encoding="utf-8",
            )
            delayed_safe_fs = root / "safe-fs-delayed.py"
            self._write_delayed_safe_fs_wrapper(delayed_safe_fs, real=safe_fs, delay=0.6)

            started = time.monotonic()
            result = self._run_finalizer(
                program,
                delayed_safe_fs,
                lock,
                stage,
                final,
                checksum_stage,
                checksum_final,
                finalize_timeout=1,
            )
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 2.5, result.stderr)
            self.assertIn("dist finalization deadline exceeded", result.stderr)

    def test_dist_finalizer_watchdog_budget_includes_kill_grace(self) -> None:
        function = self._finalizer_function()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake_bin = root / "bin"
            fake_bin.mkdir(mode=0o700)
            self._write_executable(fake_bin / "python3", "#!/bin/sh\nexec /bin/sleep 10\n")
            script = (
                "set -u\n"
                "DIST_FINALIZE_TIMEOUT_SECONDS=3\n"
                "DIST_FINALIZE_KILL_AFTER_SECONDS=1\n"
                "DIST_FINALIZE_LOCK_TIMEOUT_SECONDS=2\n"
                "safe_fs=/dev/null\n"
                f"{function}\n"
                f"replace_with_finalize_lock {str(root / 'dist' / '.lock')!r} '' '' '' ''\n"
            )
            started = time.monotonic()
            result = subprocess.run(
                ["bash", "-c", script],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
                check=False,
                timeout=8,
            )
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 5, result.stderr)

    def test_dist_exit_cleanup_reports_failure_and_preserves_primary_status(self) -> None:
        cleanup_function = self._cleanup_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = root / "cleanup-failure.sh"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'stub cleanup failure\\n' >&2\n"
                "exit 42\n",
                encoding="utf-8",
            )
            tarball = root / "archive.tar.gz"
            checksum = root / "archive.tar.gz.sha256"
            staging_dir = root / "staging"
            work_dir = root / "work"

            for primary_status, expected_status in ((0, 1), (7, 7)):
                result = self._run_exit_cleanup(
                    cleanup_function,
                    stub,
                    tarball,
                    checksum,
                    staging_dir,
                    work_dir,
                    primary_status,
                )

                self.assertEqual(result.returncode, expected_status, result.stderr)
                self.assertIn("stub cleanup failure", result.stderr)
                if primary_status == 0:
                    self.assertIn("final output status changed to failure", result.stderr)
                else:
                    self.assertIn("preserving primary exit status: 7", result.stderr)

    def test_dist_exit_cleanup_preserves_workspace_after_leaf_identity_change(self) -> None:
        cleanup_function = self._cleanup_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work_dir = root / "work"
            staging_dir = work_dir / "staging"
            staging_dir.mkdir(parents=True, mode=0o700)
            tarball = staging_dir / "stage.tar.gz"
            checksum = staging_dir / "stage.tar.gz.sha256"
            tarball.write_bytes(b"stage\n")
            checksum.write_text("checksum\n", encoding="ascii")
            def identity(path: Path, kind: str) -> str:
                return subprocess.run(
                    [sys.executable, str(safe_fs), "identity", "build-dist", str(path), "--kind", kind],
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
            identities = (
                identity(tarball, "file"),
                identity(checksum, "file"),
                identity(staging_dir, "dir"),
                identity(work_dir, "dir"),
            )
            swap = root / "safe-fs-swap.py"
            self._write_cleanup_swap_wrapper(swap, real=safe_fs)

            result = self._run_exit_cleanup(
                cleanup_function,
                swap,
                tarball,
                checksum,
                staging_dir,
                work_dir,
                0,
                identities=identities,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("preserving build-dist workspace", result.stderr)
            self.assertTrue(work_dir.exists())
            self.assertTrue(staging_dir.exists())
            self.assertEqual(tarball.read_text(encoding="utf-8"), "replacement\n")
            self.assertEqual(checksum.read_text(encoding="utf-8"), "replacement\n")

    def test_dist_exit_cleanup_uses_global_build_deadline(self) -> None:
        cleanup_function = self._cleanup_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            delayed = root / "delayed-cleanup.sh"
            self._write_executable(delayed, "#!/usr/bin/env bash\nsleep 3\n")
            deadline = str(time.monotonic_ns() + 1_000_000_000)
            started = time.monotonic()

            result = self._run_exit_cleanup(
                cleanup_function,
                delayed,
                root / "stage.tar.gz",
                root / "stage.tar.gz.sha256",
                root / "staging",
                root / "workspace",
                0,
                env={"DIST_BUILD_DEADLINE": deadline},
            )
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertLess(elapsed, 2.5, result.stderr)
            self.assertIn("failed for staged tarball", result.stderr)

    def test_dist_finalization_rejects_parent_path_exchange_after_open(self) -> None:
        program = self._finalizer_program()
        needle = "    _revalidate_lock_parent(parent_fd, parent_identity)"
        self.assertEqual(program.count(needle), 2)
        program = program.replace(
            needle,
            "    os.rename(lock_parent, lock_parent + '.moved')\n"
            "    os.mkdir(lock_parent)\n"
            f"{needle}",
            1,
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            lock.write_text("lock\n", encoding="utf-8")

            result = self._run_finalizer(
                program,
                safe_fs,
                lock,
                root / "stage.tar.gz",
                dist / "archive.tar.gz",
                root / "stage.sha256",
                dist / "archive.tar.gz.sha256",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dist finalization lock parent path changed", result.stderr)

    def test_dist_finalization_rejects_intermediate_symlink_ancestor(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_parent = root / "real" / "dist"
            real_parent.mkdir(mode=0o700, parents=True)
            link_parent = root / "link"
            link_parent.symlink_to(root / "real", target_is_directory=True)
            stage = root / "stage.tar.gz"
            checksum_stage = root / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"new\n", "archive.tar.gz")

            result = self._run_finalizer(
                program,
                safe_fs,
                link_parent / "dist" / ".finalize.lock",
                stage,
                link_parent / "dist" / "archive.tar.gz",
                checksum_stage,
                link_parent / "dist" / "archive.tar.gz.sha256",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((real_parent / "archive.tar.gz").exists())

    def test_dist_finalization_rejects_writable_intermediate_ancestor(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            unsafe_parent = root / "unsafe"
            unsafe_parent.mkdir(mode=0o770)
            unsafe_parent.chmod(0o770)
            dist = unsafe_parent / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage.tar.gz"
            checksum_stage = root / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"new\n", "archive.tar.gz")

            result = self._run_finalizer(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                dist / "archive.tar.gz",
                checksum_stage,
                dist / "archive.tar.gz.sha256",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((dist / "archive.tar.gz").exists())

    def test_dist_finalization_directory_fd_flock_survives_lockfile_rename_recreate(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            lock.write_text("original\n", encoding="utf-8")
            parent_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                lock.rename(dist / ".finalize.lock.old")
                lock.write_text("recreated\n", encoding="utf-8")
                result = self._run_finalizer(
                    program,
                    safe_fs,
                    lock,
                    root / "stage.tar.gz",
                    dist / "archive.tar.gz",
                    root / "stage.sha256",
                    dist / "archive.tar.gz.sha256",
                    lock_timeout=0,
                )
            finally:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
                os.close(parent_fd)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dist finalization lock timed out", result.stderr)
            self.assertEqual(lock.read_text(encoding="utf-8"), "recreated\n")

    def test_dist_finalization_directory_fd_lock_is_busy(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            parent_fd = os.open(dist, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self._run_finalizer(
                    program,
                    safe_fs,
                    lock,
                    root / "stage.tar.gz",
                    dist / "archive.tar.gz",
                    root / "stage.sha256",
                    dist / "archive.tar.gz.sha256",
                    lock_timeout=0,
                )
            finally:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
                os.close(parent_fd)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("dist finalization lock timed out", result.stderr)

    def test_dist_sigkill_after_each_phase_recovers_deterministically(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        phases = (
            "prepared",
            "backup-archive",
            "backup-archive-complete",
            "backup-checksum",
            "backup-checksum-complete",
            "backups-complete",
            "activate-archive",
            "archive-activated",
            "activate-checksum",
            "pair-activated",
            "committed",
        )
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                dist = root / "dist"
                dist.mkdir(mode=0o700)
                lock = dist / ".finalize.lock"
                transaction, stage, checksum_stage = self._prepare_transaction(dist)
                final = dist / "archive.tar.gz"
                checksum_final = dist / "archive.tar.gz.sha256"
                self._write_pair(final, checksum_final, b"old archive\n", final.name)

                killed = self._run_finalizer(
                    program,
                    safe_fs,
                    lock,
                    stage,
                    final,
                    checksum_stage,
                    checksum_final,
                    env={"BUILD_DIST_ABORT_PHASE": phase},
                )

                self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
                self.assertTrue((transaction / "journal").is_file())
                journal = (transaction / "journal").read_text(encoding="ascii")
                self.assertLessEqual(len(journal.encode("ascii")), 64 * 1024)
                self.assertIn('"members"', journal)
                self.assertIn('"transaction_identity"', journal)

                recovered = self._run_recovery(program, safe_fs, lock)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                expected = b"new archive\n" if phase in {"pair-activated", "committed"} else b"old archive\n"
                self.assertEqual(final.read_bytes(), expected)
                self._assert_pair(final, checksum_final)
                self.assertFalse(transaction.exists())

    def test_dist_outer_shell_sigkill_leaves_recoverable_journal_residue(self) -> None:
        function = self._finalizer_function()
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        for phase in ("archive-activated", "committed"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                dist = root / "dist"
                dist.mkdir(mode=0o700)
                transaction, stage, checksum_stage = self._prepare_transaction(dist)
                final = dist / "archive.tar.gz"
                checksum_final = dist / "archive.tar.gz.sha256"
                self._write_pair(final, checksum_final, b"old archive\n", final.name)
                metadata = self._file_metadata(stage) + self._file_metadata(checksum_stage)

                killed = self._run_finalizer_shell(
                    function,
                    safe_fs,
                    dist / ".finalize.lock",
                    stage,
                    final,
                    checksum_stage,
                    checksum_final,
                    metadata,
                    phase=phase,
                )

                self.assertNotEqual(killed.returncode, 0, killed.stderr)
                self.assertIn("status=", killed.stdout)
                self.assertTrue((transaction / "journal").is_file())
                recovered = self._run_recovery(program, safe_fs, dist / ".finalize.lock")
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                expected = b"new archive\n" if phase == "committed" else b"old archive\n"
                self.assertEqual(final.read_bytes(), expected)
                self._assert_pair(final, checksum_final)
                self.assertFalse(transaction.exists())

    def test_dist_transaction_hash_growth_never_exceeds_archive_limit(self) -> None:
        program = self._finalizer_program().replace(
            "MAX_ARCHIVE_BYTES = 128 * 1024 * 1024",
            "MAX_ARCHIVE_BYTES = 1024",
            1,
        )
        marker = "        digest, total_bytes = _hash_fd(file_fd, name, label, max_bytes)\n"
        self.assertEqual(program.count(marker), 1)
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            transaction = dist / ".build-dist-transaction-archive-123456"
            transaction.mkdir(mode=0o700)
            stage = transaction / "stage.tar.gz"
            checksum_stage = transaction / "stage.tar.gz.sha256"
            self._write_pair(stage, checksum_stage, b"a" * 1024, "archive.tar.gz")

            killed = self._run_finalizer(
                program,
                safe_fs,
                lock,
                stage,
                dist / "archive.tar.gz",
                checksum_stage,
                dist / "archive.tar.gz.sha256",
                env={"BUILD_DIST_ABORT_PHASE": "prepared"},
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)

            recovery_program = program.replace(
                marker,
                f"        if name == 'stage.tar.gz':\n"
                f"            with open({str(stage)!r}, 'ab') as growth:\n"
                f"                growth.write(b'x')\n"
                + marker,
                1,
            )
            recovered = self._run_recovery(recovery_program, safe_fs, lock)

            self.assertNotEqual(recovered.returncode, 0, recovered.stderr)
            self.assertIn("exceeds maximum size", recovered.stderr)
            self.assertTrue(transaction.exists())
            self.assertFalse((dist / "archive.tar.gz").exists())

    def test_dist_recovery_rejects_manipulated_upgrade_backup(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            final = dist / "archive.tar.gz"
            checksum_final = dist / "archive.tar.gz.sha256"
            old = b"old archive\n"
            self._write_pair(final, checksum_final, old, final.name)
            transaction, stage, checksum_stage = self._prepare_transaction(dist)

            killed = self._run_finalizer(
                program,
                safe_fs,
                lock,
                stage,
                final,
                checksum_stage,
                checksum_final,
                env={"BUILD_DIST_ABORT_PHASE": "backup-archive-complete"},
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
            journal = (transaction / "journal").read_text(encoding="ascii")
            self.assertIn('"backup_sha256":"', journal)
            (transaction / "backup.tar.gz").write_bytes(b"tampered archive\n")

            recovered = self._run_recovery(program, safe_fs, lock)

            self.assertNotEqual(recovered.returncode, 0)
            self.assertTrue(transaction.exists())
            self.assertEqual((transaction / "backup.tar.gz").read_bytes(), b"tampered archive\n")
            self.assertFalse(final.exists())
            self.assertEqual(
                checksum_final.read_text(encoding="ascii"),
                f"{hashlib.sha256(old).hexdigest()}  {final.name}\n",
            )

    def test_dist_journal_schema_requires_builtin_int_one(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        for invalid_schema in (True, 1.0):
            with self.subTest(schema=repr(invalid_schema)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                dist = root / "dist"
                dist.mkdir(mode=0o700)
                lock = dist / ".finalize.lock"
                transaction, stage, checksum_stage = self._prepare_transaction(dist)
                final = dist / "archive.tar.gz"
                checksum_final = dist / "archive.tar.gz.sha256"
                self._write_pair(final, checksum_final, b"old archive\n", final.name)

                killed = self._run_finalizer(
                    program,
                    safe_fs,
                    lock,
                    stage,
                    final,
                    checksum_stage,
                    checksum_final,
                    env={"BUILD_DIST_ABORT_PHASE": "prepared"},
                )
                self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
                journal = transaction / "journal"
                record = json.loads(journal.read_text(encoding="ascii"))
                record["schema"] = invalid_schema
                journal.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="ascii")

                recovered = self._run_recovery(program, safe_fs, lock)

                self.assertNotEqual(recovered.returncode, 0)
                self.assertTrue(transaction.exists())

    def test_dist_startup_sweep_preserves_journalless_stage_transaction(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            _, stage, checksum_stage = self._prepare_transaction(dist)
            stage.write_bytes(b"partial\n")

            recovered = self._run_recovery(program, safe_fs, lock)

            self.assertNotEqual(recovered.returncode, 0)
            self.assertTrue(stage.exists())
            self.assertTrue(checksum_stage.exists())

    def test_dist_startup_sweep_removes_only_empty_own_transaction(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            transaction, stage, checksum_stage = self._prepare_transaction(dist)
            stage.unlink()
            checksum_stage.unlink()

            recovered = self._run_recovery(program, safe_fs, lock)

            self.assertEqual(recovered.returncode, 0, recovered.stderr)
            self.assertFalse(transaction.exists())

    def test_dist_recovery_preserves_transaction_with_foreign_child(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            transaction, _, _ = self._prepare_transaction(dist)
            foreign = transaction / "foreign.bin"
            foreign.write_bytes(b"keep\n")

            recovered = self._run_recovery(program, safe_fs, lock)

            self.assertNotEqual(recovered.returncode, 0)
            self.assertTrue(transaction.exists())
            self.assertEqual(foreign.read_bytes(), b"keep\n")

    def test_dist_recovery_preserves_chained_final_tombstone_tree(self) -> None:
        """Unknown non-empty tombstone chains must stay recoverable, never be swept."""
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            transaction_name = ".build-dist-transaction-archive-123456"
            tombstone = dist / f"{transaction_name}.final-{'a' * 32}.final-{'b' * 32}"
            tombstone.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(tombstone, (old_time, old_time))
            payload = tombstone / "foreign-payload"
            payload.write_bytes(b"must survive\n")

            recovered = self._run_recovery(program, safe_fs, lock)

            self.assertNotEqual(recovered.returncode, 0)
            self.assertTrue(tombstone.is_dir())
            self.assertEqual(payload.read_bytes(), b"must survive\n")

    def test_dist_workspace_sweep_removes_only_old_own_names(self) -> None:
        program = self._workspace_sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_base = root / "speed-of-cinnamon-build-dist-999"
            workspace_base.mkdir(mode=0o700)
            stale = workspace_base / "speed-of-cinnamon-build-dist-tree-aaaaaa"
            stale.mkdir(mode=0o700)
            (stale / "owned-payload").write_bytes(b"remove\n")
            foreign = workspace_base / "foreign-data"
            foreign.write_bytes(b"keep\n")
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))

            result = self._run_workspace_sweep(program, workspace_base, safe_fs)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual(foreign.read_bytes(), b"keep\n")

    def test_dist_workspace_sweep_preserves_recent_and_nonempty_tombstones(self) -> None:
        program = self._workspace_sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_base = root / "speed-of-cinnamon-build-dist-999"
            workspace_base.mkdir(mode=0o700)
            tombstone = workspace_base / (
                ".speed-of-cinnamon-build-dist-tree-aaaaaa"
                f".final-{'a' * 32}.final-{'b' * 32}"
            )
            tombstone.mkdir(mode=0o700)
            payload = tombstone / "foreign-payload"
            payload.write_bytes(b"keep\n")
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(tombstone, (old_time, old_time))

            result = self._run_workspace_sweep(program, workspace_base, safe_fs)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(tombstone.exists())
            self.assertEqual(payload.read_bytes(), b"keep\n")

            recent = workspace_base / "speed-of-cinnamon-build-dist-tree-bbbbbb"
            recent.mkdir(mode=0o700)
            result = self._run_workspace_sweep(program, workspace_base, safe_fs)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(recent.exists())

    def test_dist_workspace_sweep_rejects_identity_change_before_cleanup(self) -> None:
        program = self._workspace_sweep_program().replace(
            "    remaining_timeout()\n    for name, expected_identity, is_tombstone in stale:\n",
            "    remaining_timeout()\n"
            "    if stale:\n"
            "        os.rename(os.path.join(workspace_base, stale[0][0]), os.path.join(workspace_base, stale[0][0] + '.replacement'))\n"
            "        os.mkdir(os.path.join(workspace_base, stale[0][0]), 0o700)\n"
            "    for name, expected_identity, is_tombstone in stale:\n",
            1,
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_base = root / "speed-of-cinnamon-build-dist-999"
            workspace_base.mkdir(mode=0o700)
            stale = workspace_base / "speed-of-cinnamon-build-dist-tree-aaaaaa"
            stale.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))

            result = self._run_workspace_sweep(program, workspace_base, safe_fs)

            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(stale.exists())
            self.assertTrue((workspace_base / f"{stale.name}.replacement").exists())

    def test_dist_workspace_sweep_honors_own_fd_lock_and_bounds_scan(self) -> None:
        program = self._workspace_sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_base = root / "speed-of-cinnamon-build-dist-999"
            workspace_base.mkdir(mode=0o700)
            lock_fd = os.open(workspace_base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = self._run_workspace_sweep(
                    program,
                    workspace_base,
                    safe_fs,
                    timeout_seconds=1,
                    process_timeout=5,
                )
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("workspace sweep deadline exceeded", result.stderr)

            overflow = root / "overflow"
            overflow.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            for index in range(3):
                item = overflow / f"speed-of-cinnamon-build-dist-tree-{index:06d}"
                item.mkdir(mode=0o700)
                os.utime(item, (old_time, old_time))
            result = self._run_workspace_sweep(program, overflow, safe_fs, max_stale=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("workspace sweep exceeds max 2", result.stderr)

    def test_dist_outer_watchdog_kills_blocking_shell_within_global_budget(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")
        top = source[: source.index("\nrepo_dir=")]
        top = top.replace("DIST_BUILD_TIMEOUT_SECONDS=3600", "DIST_BUILD_TIMEOUT_SECONDS=2", 1)
        top = top.replace("DIST_BUILD_KILL_AFTER_SECONDS=2", "DIST_BUILD_KILL_AFTER_SECONDS=1", 1)
        top = top.replace("DIST_BUILD_STARTUP_RESERVE_NS=3000000000", "DIST_BUILD_STARTUP_RESERVE_NS=500000000", 1)
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "outer-watchdog.sh"
            self._write_executable(harness, f"{top}\nsleep 60\n")
            environment = os.environ.copy()
            environment["DIST_BUILD_DEADLINE"] = "999999999999999999999999"
            started = time.monotonic()
            result = subprocess.run(
                [str(harness)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env=environment,
                check=False,
                timeout=6,
            )
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 4, result.stderr)

    def test_dist_startup_rejects_forged_marker_deadline_and_fd(self) -> None:
        marker = "__build_dist_deadline_child__"
        forged_deadline = str(time.monotonic_ns() + 60 * 1_000_000_000)
        environment = {**os.environ, "DIST_BUILD_DEADLINE": forged_deadline}
        top = self._startup_top()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "marker-forwarded.sh"
            self._write_executable(harness, f"{top}\nsleep 60\n")
            started = time.monotonic()
            missing = subprocess.run(
                [str(harness), marker],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env=environment,
                check=False,
                timeout=5,
            )
            elapsed = time.monotonic() - started
            self.assertNotEqual(missing.returncode, 0)
            self.assertLess(elapsed, 4, missing.stderr)

            forged_fd_path = root / "forged-handoff"
            forged_fd_path.write_bytes(b"not a build-dist handoff\n")
            forged_launcher = root / "forged-fd-launcher.sh"
            self._write_executable(
                forged_launcher,
                "#!/usr/bin/env bash\n"
                "exec 198<\"$1\"\n"
                "shift\n"
                "exec \"$@\"\n",
            )
            forged = subprocess.run(
                [str(forged_launcher), str(forged_fd_path), str(harness), marker],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env=environment,
                check=False,
                timeout=5,
            )
            self.assertNotEqual(forged.returncode, 0)
            self.assertIn("startup handoff", forged.stderr)

    def test_dist_startup_clamps_valid_handoff_deadline(self) -> None:
        top = self._startup_top()
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "clamped-deadline.sh"
            self._write_executable(harness, f"{top}\nsleep 60\n")
            launcher = Path(tmp) / "fd-launcher.sh"
            self._write_startup_fd_launcher(launcher)
            process = None
            lock_fd = None
            try:
                process, lock_fd = self._start_valid_handoff(harness, launcher)
                started = time.monotonic()
                returncode = process.wait(timeout=5)
                elapsed = time.monotonic() - started

                self.assertNotEqual(returncode, 0)
                self.assertLess(elapsed, 4)
            finally:
                if process is not None and process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if lock_fd is not None:
                    os.close(lock_fd)

    def test_dist_startup_initial_probe_is_hard_capped(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")
        top = source[: source.index("\nrepo_dir=")]
        top = top.replace("DIST_BUILD_TIMEOUT_SECONDS=3600", "DIST_BUILD_TIMEOUT_SECONDS=3", 1)
        top = top.replace("DIST_BUILD_KILL_AFTER_SECONDS=2", "DIST_BUILD_KILL_AFTER_SECONDS=1", 1)
        top = top.replace("DIST_BUILD_STARTUP_RESERVE_NS=3000000000", "DIST_BUILD_STARTUP_RESERVE_NS=500000000", 1)
        top = top.replace(
            "DIST_BUILD_INITIAL_PROBE_TIMEOUT_SECONDS=0.25",
            "DIST_BUILD_INITIAL_PROBE_TIMEOUT_SECONDS=0.2",
            1,
        )
        top = top.replace(
            "        nonce = secrets.token_hex(32)",
            "        time.sleep(60)\n        nonce = secrets.token_hex(32)",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "initial-probe-hang.sh"
            self._write_executable(harness, f"{top}\nsleep 60\n")
            started = time.monotonic()
            result = subprocess.run(
                [str(harness)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                check=False,
                timeout=5,
            )
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("initial monotonic probe", result.stderr)
            self.assertLess(elapsed, 2, result.stderr)

    def test_dist_startup_parent_death_terminates_child(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")
        top = source[: source.index("\nrepo_dir=")]
        top = top.replace("DIST_BUILD_TIMEOUT_SECONDS=3600", "DIST_BUILD_TIMEOUT_SECONDS=30", 1)
        top = top.replace("DIST_BUILD_KILL_AFTER_SECONDS=2", "DIST_BUILD_KILL_AFTER_SECONDS=1", 1)
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "parent.pid"
            harness = Path(tmp) / "parent-death.sh"
            self._write_executable(
                harness,
                f"{top}\nprintf '%s\\n' \"$DIST_BUILD_SUPERVISOR_PID\" > '{pid_path}'\nsleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_deadline = time.monotonic() + 3
                pid_text = ""
                while time.monotonic() < wait_deadline:
                    if pid_path.exists():
                        try:
                            pid_text = pid_path.read_text(encoding="ascii").strip()
                        except (OSError, UnicodeDecodeError):
                            pid_text = ""
                        if pid_text:
                            break
                    time.sleep(0.02)
                self.assertTrue(pid_text)
                parent_pid = int(pid_text)
                os.kill(parent_pid, signal.SIGKILL)
                started = time.monotonic()
                returncode = process.wait(timeout=5)
                elapsed = time.monotonic() - started

                self.assertNotEqual(returncode, 0)
                self.assertLess(elapsed, 4)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)

    def test_dist_startup_watchdog_death_is_fail_closed(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")
        top = source[: source.index("\nrepo_dir=")]
        top = top.replace("DIST_BUILD_TIMEOUT_SECONDS=3600", "DIST_BUILD_TIMEOUT_SECONDS=30", 1)
        top = top.replace("DIST_BUILD_KILL_AFTER_SECONDS=2", "DIST_BUILD_KILL_AFTER_SECONDS=1", 1)
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "child.pid"
            harness = Path(tmp) / "watchdog-death.sh"
            self._write_executable(
                harness,
                f"{top}\nprintf '%s %s\\n' \"$$\" \"$PPID\" > '{pid_path}'\nsleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            child_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                pid_parts = []
                while time.monotonic() < wait_deadline:
                    if pid_path.exists():
                        try:
                            pid_parts = pid_path.read_text(encoding="ascii").split()
                        except (OSError, UnicodeDecodeError):
                            pid_parts = []
                        if len(pid_parts) >= 2:
                            break
                    time.sleep(0.02)
                self.assertGreaterEqual(len(pid_parts), 2)
                child_pid = int(pid_parts[0])
                os.kill(process.pid, signal.SIGKILL)
                started = time.monotonic()
                returncode = process.wait(timeout=5)

                def process_alive(pid: int) -> bool:
                    try:
                        record = Path(f"/proc/{pid}/stat").read_bytes()
                    except FileNotFoundError:
                        return False
                    marker = record.rfind(b") ")
                    return marker >= 0 and record[marker + 2 :].split()[0] != b"Z"

                while process_alive(child_pid) and time.monotonic() - started < 4:
                    time.sleep(0.02)
                elapsed = time.monotonic() - started

                self.assertNotEqual(returncode, 0)
                self.assertFalse(process_alive(child_pid))
                self.assertLess(elapsed, 4)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if child_pid is not None:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_dist_startup_watchdog_identity_is_starttime_bound(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")
        startup = source[: source.index("\nrepo_dir=")]

        self.assertIn("watchdog_info = process_info(watchdog_pid)", startup)
        self.assertIn("watchdog_starttime", startup)
        self.assertIn("info[4] != watchdog_starttime", startup)
        self.assertIn("start_new_session=True", startup)

    def test_dist_startup_watchdog_starttime_mismatch_fails_closed(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        top = top.replace(
            "watchdog_starttime = watchdog_info[4]",
            "watchdog_starttime = watchdog_info[4] + 1",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "watchdog-identity-mismatch.sh"
            self._write_executable(harness, f"{top}\nsleep 0.2\n")
            started = time.monotonic()
            result = subprocess.run(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 4, result.stderr)

    def test_dist_startup_monitor_closes_report_fd_immediately(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "monitor-fd-closed.sh"
            self._write_executable(
                harness,
                f'{top}\n'
                'monitor_fd="/proc/${handoff_monitor_pid}/fd/197"\n'
                'if [[ -e "${monitor_fd}" || -L "${monitor_fd}" ]]; then\n'
                '  printf "monitor retained FD197\\n" >&2\n'
                '  exit 1\n'
                'fi\n'
                'printf "monitor-fd-closed\\n"\n',
            )
            result = subprocess.run(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("monitor-fd-closed", result.stdout)

    def test_dist_outer_abort_kills_term_resistant_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "resistant.pid"
            harness = Path(tmp) / "group-abort.sh"
            top = self._startup_top(timeout_seconds=3, kill_after_seconds=1)
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)' &\n"
                "resistant_pid=$!\n"
                f"printf '%s\\n' \"$resistant_pid\" > '{pid_path}'\n"
                "sleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            resistant_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                while time.monotonic() < wait_deadline and not pid_path.exists():
                    time.sleep(0.02)
                self.assertTrue(pid_path.exists())
                resistant_pid = int(pid_path.read_text(encoding="ascii").strip())
                started = time.monotonic()
                returncode = process.wait(timeout=6)
                elapsed = time.monotonic() - started

                while self._pid_is_live(resistant_pid) and time.monotonic() - started < 5:
                    time.sleep(0.02)
                self.assertNotEqual(returncode, 0)
                self.assertFalse(self._pid_is_live(resistant_pid))
                self.assertLess(elapsed, 5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if resistant_pid is not None and self._pid_is_live(resistant_pid):
                    os.kill(resistant_pid, signal.SIGKILL)

    def test_dist_watchdog_kill_monitor_removes_term_resistant_descendant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "watchdog-resistant.pid"
            harness = Path(tmp) / "watchdog-group-abort.sh"
            top = self._startup_top(timeout_seconds=30, kill_after_seconds=1)
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)' &\n"
                "resistant_pid=$!\n"
                f"printf '%s\\n' \"$resistant_pid\" > '{pid_path}'\n"
                "sleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            resistant_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                while time.monotonic() < wait_deadline and not pid_path.exists():
                    time.sleep(0.02)
                self.assertTrue(pid_path.exists())
                resistant_pid = int(pid_path.read_text(encoding="ascii").strip())
                os.kill(process.pid, signal.SIGKILL)
                started = time.monotonic()
                returncode = process.wait(timeout=6)

                while self._pid_is_live(resistant_pid) and time.monotonic() - started < 5:
                    time.sleep(0.02)
                self.assertNotEqual(returncode, 0)
                self.assertFalse(self._pid_is_live(resistant_pid))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if resistant_pid is not None and self._pid_is_live(resistant_pid):
                    os.kill(resistant_pid, signal.SIGKILL)

    def test_dist_outer_abort_kills_descendant_when_monitor_is_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "stopped-monitor-resistant.pid"
            harness = Path(tmp) / "stopped-monitor-group-abort.sh"
            top = self._startup_top(timeout_seconds=3, kill_after_seconds=1)
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)' &\n"
                "resistant_pid=$!\n"
                f"printf '%s\\n' \"$resistant_pid\" > '{pid_path}'\n"
                'kill -STOP -- "$handoff_monitor_pid"\n'
                "sleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            resistant_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                while time.monotonic() < wait_deadline and not pid_path.exists():
                    time.sleep(0.02)
                self.assertTrue(pid_path.exists())
                resistant_pid = int(pid_path.read_text(encoding="ascii").strip())
                started = time.monotonic()
                returncode = process.wait(timeout=6)
                elapsed = time.monotonic() - started

                while self._pid_is_live(resistant_pid) and time.monotonic() - started < 5:
                    time.sleep(0.02)
                self.assertNotEqual(returncode, 0)
                self.assertFalse(self._pid_is_live(resistant_pid))
                self.assertLess(elapsed, 5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if resistant_pid is not None and self._pid_is_live(resistant_pid):
                    os.kill(resistant_pid, signal.SIGKILL)

    def test_dist_combined_supervisor_monitor_death_kills_descendant_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "combined-resistant.pid"
            harness = Path(tmp) / "combined-group-abort.sh"
            top = self._startup_top(timeout_seconds=30, kill_after_seconds=1)
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)' &\n"
                "resistant_pid=$!\n"
                f"printf '%s\\n' \"$resistant_pid\" > '{pid_path}'\n"
                'kill -KILL -- "$handoff_monitor_pid"\n'
                'kill -TERM -- "$DIST_BUILD_SUPERVISOR_PID"\n'
                "sleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            resistant_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                while time.monotonic() < wait_deadline and not pid_path.exists():
                    time.sleep(0.02)
                self.assertTrue(pid_path.exists())
                resistant_pid = int(pid_path.read_text(encoding="ascii").strip())
                started = time.monotonic()
                returncode = process.wait(timeout=6)
                elapsed = time.monotonic() - started

                while self._pid_is_live(resistant_pid) and time.monotonic() - started < 5:
                    time.sleep(0.02)
                self.assertNotEqual(returncode, 0)
                self.assertFalse(self._pid_is_live(resistant_pid))
                self.assertLess(elapsed, 5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if resistant_pid is not None and self._pid_is_live(resistant_pid):
                    os.kill(resistant_pid, signal.SIGKILL)

    def test_dist_startup_monitor_death_fails_closed(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "monitor-death.sh"
            self._write_executable(
                harness,
                f'{top}\nkill -KILL -- "$handoff_monitor_pid"\nsleep 60\n',
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                started = time.monotonic()
                returncode = process.wait(timeout=5)
                elapsed = time.monotonic() - started
                self.assertNotEqual(returncode, 0)
                self.assertLess(elapsed, 4)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_dist_startup_monitor_identity_change_fails_closed(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "monitor-identity.sh"
            launcher = root / "fd-launcher.sh"
            self._write_executable(
                harness,
                f'{top}\nhandoff_monitor_starttime=0\nsleep 0.1 &\nwait $!\nsleep 60\n',
            )
            self._write_startup_fd_launcher(launcher)
            process, lock_fd = self._start_valid_handoff(harness, launcher)
            try:
                started = time.monotonic()
                returncode = process.wait(timeout=5)
                elapsed = time.monotonic() - started
                self.assertNotEqual(returncode, 0)
                self.assertLess(elapsed, 4)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                os.close(lock_fd)

    def test_dist_startup_handoff_fd_is_closed_before_core_descendants(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "fd-closed.sh"
            launcher = root / "fd-launcher.sh"
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import os,sys; sys.exit(int(any(os.path.exists(f\"/proc/self/fd/{fd}\") for fd in (197,198))))'\n"
                "printf 'fd-closed\\n'\n",
            )
            self._write_startup_fd_launcher(launcher)
            process, lock_fd = self._start_valid_handoff(
                harness,
                launcher,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertIn("fd-closed", stdout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                os.close(lock_fd)

    def test_dist_direct_handoff_stopped_monitor_fails_closed(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            monitor_path = root / "monitor.pid"
            harness = root / "direct-stopped-monitor.sh"
            launcher = root / "fd-launcher.sh"
            self._write_executable(
                harness,
                f"{top}\n"
                f"printf '%s\\n' \"$handoff_monitor_pid\" > '{monitor_path}'\n"
                'kill -STOP -- "$handoff_monitor_pid"\n'
                "sleep 0.2\n",
            )
            self._write_startup_fd_launcher(launcher)
            process, lock_fd = self._start_valid_handoff(harness, launcher)
            monitor_pid = None
            try:
                returncode = process.wait(timeout=5)
                self.assertNotEqual(returncode, 0)
                if monitor_path.exists():
                    monitor_pid = int(monitor_path.read_text(encoding="ascii").strip())
                if monitor_pid is not None:
                    deadline = time.monotonic() + 2
                    while self._pid_is_live(monitor_pid) and time.monotonic() < deadline:
                        time.sleep(0.02)
                    self.assertFalse(self._pid_is_live(monitor_pid))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if monitor_pid is not None and self._pid_is_live(monitor_pid):
                    os.kill(monitor_pid, signal.SIGKILL)
                os.close(lock_fd)

    def test_dist_killpg_requires_fresh_full_group_identity(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")
        startup = source[: source.index("\nrepo_dir=")]
        supervisor = startup[: startup.index("\nPY\nfi")]
        monitor = startup[startup.index("coproc handoff_monitor_proc") :]

        self.assertIn("def child_group_identity", startup)
        self.assertIn("def signal_child_group", startup)
        self.assertIn("child_parent_pid", startup)
        self.assertIn("child_starttime", startup)
        self.assertIn("info[1] != child_parent_pid", startup)
        self.assertIn("info[2] != child.pid", startup)
        self.assertIn("info[3] != child.pid", startup)
        self.assertIn("info[4] != child_starttime", startup)
        self.assertIn("info[0] in (b\"Z\", b\"X\", b\"T\", b\"t\")", startup)
        self.assertIn("preexec_fn=install_child_parent_death_signal", supervisor)
        self.assertIn("pdeath_signal = 1", supervisor)
        self.assertIn("os.getppid() != child_parent_pid", supervisor)
        self.assertIn("current_parent_info[1:] != child_parent_identity", supervisor)
        self.assertEqual(supervisor.count("os.killpg("), 1)
        self.assertEqual(monitor.count("os.killpg("), 1)

    def test_dist_killpg_rejects_identity_change_between_term_and_kill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor_path = root / "supervisor.pid"
            signal_log = root / "signals.log"
            harness = root / "identity-change.sh"
            top = self._startup_top(timeout_seconds=30, kill_after_seconds=1)
            supervisor_hook = (
                "real_killpg = os.killpg\n"
                "def logged_killpg(pgid, signum):\n"
                f"    with open({str(signal_log)!r}, 'a', encoding='ascii') as handle:\n"
                "        handle.write(f'{pgid}:{signum}\\n')\n"
                "    return real_killpg(pgid, signum)\n"
                "os.killpg = logged_killpg\n"
            )
            top = top.replace(
                "import subprocess\n",
                f"import subprocess\n{supervisor_hook}",
                1,
            )
            top = top.replace(
                "        if not signal_child_group(signal.SIGKILL):\n",
                "        child_parent_pid += 1\n"
                "        child_starttime += 1\n"
                "        if not signal_child_group(signal.SIGKILL):\n",
                1,
            )
            self._write_executable(
                harness,
                f"{top}\nprintf '%s\\n' \"$DIST_BUILD_SUPERVISOR_PID\" > '{supervisor_path}'\nsleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                wait_deadline = time.monotonic() + 3
                supervisor_pid = None
                while time.monotonic() < wait_deadline:
                    if supervisor_path.exists():
                        try:
                            supervisor_pid = int(
                                supervisor_path.read_text(encoding="ascii").strip()
                            )
                        except (OSError, ValueError, UnicodeDecodeError):
                            supervisor_pid = None
                        if supervisor_pid is not None:
                            break
                    time.sleep(0.02)
                self.assertIsNotNone(supervisor_pid)
                os.kill(supervisor_pid, signal.SIGTERM)
                returncode = process.wait(timeout=5)

                self.assertNotEqual(returncode, 0)
                signals = [
                    line.rsplit(":", 1)[-1]
                    for line in signal_log.read_text(encoding="ascii").splitlines()
                ]
                self.assertEqual(signals, [str(signal.SIGTERM)])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)

    def test_dist_proxy_reaps_group_after_abnormal_core_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resistant_path = root / "resistant.pid"
            harness = root / "abnormal-core.sh"
            top = self._startup_top(timeout_seconds=30, kill_after_seconds=1)
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); print(os.getpid(), flush=True); time.sleep(60)' &\n"
                "resistant_pid=$!\n"
                f"printf '%s\\n' \"$resistant_pid\" > '{resistant_path}'\n"
                "kill -KILL -- \"$$\"\n"
                "sleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            resistant_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                while time.monotonic() < wait_deadline:
                    if resistant_path.exists():
                        try:
                            resistant_pid = int(resistant_path.read_text(encoding="ascii").strip())
                        except (OSError, ValueError, UnicodeDecodeError):
                            resistant_pid = None
                        if resistant_pid is not None:
                            break
                    time.sleep(0.02)
                self.assertIsNotNone(resistant_pid)
                returncode = process.wait(timeout=8)
                reap_deadline = time.monotonic() + 3
                while time.monotonic() < reap_deadline and self._pid_is_live(resistant_pid):
                    time.sleep(0.02)
                self.assertEqual(returncode, 137)
                self.assertFalse(self._pid_is_live(resistant_pid))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                if resistant_pid is not None and self._pid_is_live(resistant_pid):
                    os.kill(resistant_pid, signal.SIGKILL)

    def test_dist_supervisor_process_info_retries_eintr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "eintr-supervisor.sh"
            script = self._startup_top(timeout_seconds=20)
            script = script.replace(
                "    def process_info(pid):\n",
                "    process_info_eintr = 32\n\n"
                "    def process_info(pid):\n",
                1,
            )
            script = script.replace(
                '    def process_info(pid):\n'
                '        while True:\n'
                '            try:\n'
                '                with open(f"/proc/{pid}/stat", "rb") as handle:\n',
                '    def process_info(pid):\n'
                '        while True:\n'
                '            try:\n'
                "                global process_info_eintr\n"
                "                if process_info_eintr:\n"
                "                    process_info_eintr -= 1\n"
                "                    raise InterruptedError\n"
                '                with open(f"/proc/{pid}/stat", "rb") as handle:\n',
                1,
            )
            self._write_executable(harness, f"{script}\nprintf 'eintr-supervisor-ok\\n'\n")
            result = subprocess.run(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("eintr-supervisor-ok", result.stdout)

    def test_dist_proxy_process_info_retries_eintr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = Path(tmp) / "eintr-proxy.sh"
            script = self._startup_top(timeout_seconds=20)
            script = script.replace(
                "proxy_parent_pid = os.getppid()\n\n\n",
                "proxy_parent_pid = os.getppid()\n\n"
                "process_info_eintr = 32\n\n\n",
                1,
            )
            script = script.replace(
                'def process_info(pid):\n'
                '    while True:\n'
                '        try:\n'
                '            with open(f"/proc/{pid}/stat", "rb") as handle:\n',
                'def process_info(pid):\n'
                '    while True:\n'
                '        try:\n'
                "            global process_info_eintr\n"
                "            if process_info_eintr:\n"
                "                process_info_eintr -= 1\n"
                "                raise InterruptedError\n"
                '            with open(f"/proc/{pid}/stat", "rb") as handle:\n',
                1,
            )
            self._write_executable(harness, f"{script}\nprintf 'eintr-proxy-ok\\n'\n")
            result = subprocess.run(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("eintr-proxy-ok", result.stdout)

    def test_dist_sigkill_supervisor_and_monitor_still_kill_descendant_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "watchers-and-resistant.pid"
            harness = Path(tmp) / "double-sigkill.sh"
            top = self._startup_top(timeout_seconds=30, kill_after_seconds=1)
            self._write_executable(
                harness,
                f"{top}\n"
                "python3 -c 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); signal.signal(signal.SIGHUP, signal.SIG_IGN); time.sleep(60)' &\n"
                "resistant_pid=$!\n"
                f"printf '%s %s %s\\n' \"$resistant_pid\" \"$handoff_monitor_pid\" \"$DIST_BUILD_SUPERVISOR_PID\" > '{state_path}'\n"
                "sleep 60\n",
            )
            process = subprocess.Popen(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            resistant_pid = None
            monitor_pid = None
            try:
                wait_deadline = time.monotonic() + 3
                state_parts: list[str] = []
                while time.monotonic() < wait_deadline:
                    if state_path.exists():
                        try:
                            state_parts = state_path.read_text(encoding="ascii").split()
                        except (OSError, UnicodeDecodeError):
                            state_parts = []
                        if len(state_parts) == 3:
                            break
                    time.sleep(0.02)
                self.assertEqual(len(state_parts), 3)
                resistant_pid, monitor_pid, supervisor_pid = (
                    int(value) for value in state_parts
                )
                os.kill(monitor_pid, signal.SIGKILL)
                os.kill(supervisor_pid, signal.SIGKILL)
                started = time.monotonic()
                try:
                    returncode = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.kill(process.pid, signal.SIGKILL)
                    returncode = process.wait(timeout=3)

                while self._pid_is_live(resistant_pid) and time.monotonic() - started < 4:
                    time.sleep(0.02)
                self.assertNotEqual(returncode, 0)
                self.assertFalse(self._pid_is_live(resistant_pid))
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for pid in (resistant_pid, monitor_pid):
                    if pid is not None and self._pid_is_live(pid):
                        os.kill(pid, signal.SIGKILL)

    def test_dist_startup_monitor_survives_eintr_signal_storm(self) -> None:
        top = self._startup_top(timeout_seconds=30)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "monitor-eintr.sh"
            self._write_executable(
                harness,
                f"{top}\n"
                "for _ in $(seq 1 200); do\n"
                "  kill -USR1 \"$handoff_monitor_pid\"\n"
                "  kill -USR1 \"$DIST_BUILD_SUPERVISOR_PID\"\n"
                "done\n"
                "sleep 0.2\n"
                "kill -0 \"$handoff_monitor_pid\"\n"
                "printf 'eintr-ok\\n'\n",
            )
            result = subprocess.run(
                [str(harness)],
                cwd=REPO_ROOT,
                env=os.environ.copy(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("eintr-ok", result.stdout)

    def test_dist_tmp_root_accepts_private_and_rejects_unsafe_shapes(self) -> None:
        program = self._tmp_validation_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accepted = self._run_tmp_validation(program, root)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            root.chmod(0o777)
            rejected = self._run_tmp_validation(program, root)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("euid-owned private or root-owned sticky standard temp", rejected.stderr)

            target = root / "target"
            target.mkdir(mode=0o700)
            link = root / "tmp-link"
            link.symlink_to(target, target_is_directory=True)
            rejected_link = self._run_tmp_validation(program, link)
            self.assertNotEqual(rejected_link.returncode, 0)
            self.assertIn("not a symlink", rejected_link.stderr)

    def test_dist_workspace_has_private_lock_and_bounded_identity_sweep_contract(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        workspace_setup = source[: source.index("cleanup() {")]
        self.assertIn("workspace_base", workspace_setup)
        self.assertIn("workspace_lock", workspace_setup)
        self.assertIn("O_NOFOLLOW", workspace_setup)
        self.assertIn("flock", workspace_setup)
        self.assertIn("DIST_STARTUP_SWEEP_MAX_STALE", workspace_setup)
        self.assertIn("startup_sweep", workspace_setup)
        self.assertIn("st_mtime", workspace_setup)
        self.assertIn("st_ino", workspace_setup)
        self.assertIn("st_dev", workspace_setup)
        self.assertIn("st_uid", workspace_setup)
        self.assertIn("foreign", workspace_setup.lower())

    def test_dist_deadline_starts_before_workspace_and_bounds_find_cleanup(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        deadline_marker = "build_start_monotonic"
        self.assertIn(deadline_marker, source)
        deadline_at = source.index(deadline_marker)
        workspace_at = source.index('work_dir="$(mktemp')
        self.assertLess(deadline_at, workspace_at)
        self.assertIn("DIST_BUILD_DEADLINE", source)
        self.assertIn("build_remaining_ns", source)
        self.assertIn("cleanup_deadline", source)
        self.assertIn("timeout --signal=TERM --kill-after=", source)
        self.assertIn("signal.setitimer", source)
        self.assertIn("DIST_BUILD_STARTUP_RESERVE_NS", source)
        self.assertIn("DIST_BUILD_HANDOFF_FD=198", source)
        self.assertIn("memfd_create", source)
        self.assertIn("pass_fds", source)
        self.assertIn("effective_deadline = min", source)
        self.assertIn("coproc handoff_monitor_proc", source)
        self.assertIn("trap check_handoff_monitor CHLD", source)
        self.assertIn("handoff_monitor_starttime", source)
        self.assertIn("process_starttime", source)
        self.assertIn("InterruptedError", source)
        self.assertIn("exec 198>&-", source)
        self.assertNotIn("DIST_BUILD_WATCHDOG_ARG", source)
        self.assertNotIn("__build_dist_deadline_child__", source)
        self.assertNotIn("done < <(\n    find ", source)

    def test_dist_outer_shell_kill_contract_keeps_recovery_residue(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn("BUILD_DIST_ABORT_PHASE", source)
        self.assertIn('SIGKILL', source)
        self.assertIn('"journal"', source)
        self.assertIn('"rolled-back"', source)
        self.assertIn("preserving", source)
        self.assertIn("trap cleanup EXIT", source)

    def test_build_dist_uses_safe_fs_for_source_copying(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn('safe_fs="${repo_dir}/scripts/safe-local-fs.py"', source)
        self.assertIn('python3 "${safe_fs}" install-tree build-dist "${source_path}" "${target_path}"', source)
        self.assertIn("distribution_tree_excludes=", source)
        self.assertIn("for tool in python3 tar sha256sum mktemp find grep git stat realpath timeout;", source)
        self.assertIn('--exclude-name __pycache__', source)
        self.assertIn('"${distribution_tree_excludes[@]}"', source)
        self.assertIn('python3 "${safe_fs}" copy-file build-dist "${source_path}" "${target_path}" 0644', source)
        self.assertNotIn('cp -a "${repo_dir}/${path}" "${work_dir}/${package}/"', source)

    def test_verify_dist_checks_companion_checksum(self) -> None:
        source = VERIFY_DIST.read_text(encoding="utf-8")

        self.assertIn('for tool in realpath stat tar awk mktemp find grep python3 sha256sum timeout;', source)
        self.assertIn('checksum_path="$tarball.sha256"', source)
        self.assertIn('archive checksum file target does not match archive', source)
        self.assertIn('sha256sum "${tarball}"', source)
        self.assertIn('archive checksum mismatch:', source)

    def test_build_dist_cleanup_requires_expected_identity(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn(
            'work_dir_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${work_dir}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${work_dir_identity}"', source)
        self.assertIn('--expected-identity "${staging_tarball_identity}"', source)
        self.assertIn('--expected-identity "${staging_checksum_identity}"', source)
        self.assertIn('--expected-identity "${dist_staging_dir_identity}"', source)
        self.assertIn(
            'cache_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${cache_dir}" --kind dir)"',
            source,
        )
        self.assertIn(
            'bytecode_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${bytecode_file}" --kind file)"',
            source,
        )

    def test_dist_activation_requires_original_stage_and_empty_destination(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        activation_start = source.index(
            'journal_identity = _persist_phase(record, transaction_fd, journal_identity, "activate-archive")'
        )
        activation_end = source.index(
            'journal_identity = _persist_phase(record, transaction_fd, journal_identity, "archive-activated")',
            activation_start,
        )
        activation = source[activation_start:activation_end]
        self.assertIn('"stage_identity": _identity(stage_archive_stat)', source)
        self.assertIn('"--expected-src-identity",', activation)
        self.assertIn('archive_member["stage_identity"]', activation)
        self.assertIn('"--expected-dst-identity",', activation)
        self.assertIn('"missing",', activation)

    def test_project_metadata_reads_are_bounded(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("pyproject.toml project.name is invalid", source)
        self.assertIn("pyproject.toml project.version is invalid", source)
        self.assertIn("RecursionError, MemoryError", source)

    def test_archive_and_finalization_processes_are_time_bounded(self) -> None:
        source = BUILD_DIST.read_text(encoding="utf-8")

        self.assertIn("readonly DIST_BUILD_TIMEOUT_SECONDS=3600", source)
        self.assertIn("readonly DIST_BUILD_KILL_AFTER_SECONDS=2", source)
        self.assertIn("readonly DIST_FINALIZE_TIMEOUT_SECONDS=120", source)
        self.assertIn("readonly DIST_FINALIZE_LOCK_TIMEOUT_SECONDS=30", source)
        self.assertIn("readonly DIST_FINALIZE_KILL_AFTER_SECONDS=2", source)
        self.assertIn("readonly DIST_CLEANUP_TIMEOUT_SECONDS=30", source)
        self.assertIn('timeout --signal=TERM --kill-after="${DIST_BUILD_KILL_AFTER_SECONDS}s"', source)
        self.assertIn("build_staged_tarball()", source)
        self.assertIn("build_deadline = time.monotonic() + int(timeout_seconds)", source)
        self.assertIn("stdin=subprocess.DEVNULL", source)
        self.assertIn("os.set_blocking(stdout_fd, False)", source)
        self.assertIn("os.set_blocking(stderr_fd, False)", source)
        self.assertIn("MAX_TAR_STDERR_BYTES = 4096", source)
        self.assertIn("DIST_MAX_ARCHIVE_BYTES", source)
        self.assertIn("staged dist archive exceeds maximum size", source)
        self.assertIn("producer_metadata", source)
        self.assertIn("finalize_run_seconds", source)
        self.assertIn("os.fsync(stage_fd)", source)
        self.assertNotIn(' | write_regular_file_from_stdin "${staging_tarball}"', source)
        self.assertNotIn("| write_regular_file_from_stdin", source)
        self.assertIn(
            "finalizer_deadline = time.monotonic() + finalize_timeout_seconds",
            source,
        )
        self.assertIn("remaining = finalizer_deadline - time.monotonic()", source)
        self.assertIn("timeout=remaining", source)
        self.assertIn("subprocess.TimeoutExpired", source)
        self.assertIn("lock_deadline = min(finalizer_deadline", source)
        self.assertNotIn("timeout=finalize_timeout_seconds", source)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", source)
        self.assertIn("fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", source)
        self.assertIn("parent_identity = parent_chain_identities[-1]", source)
        self.assertIn("_revalidate_lock_parent(parent_fd, parent_identity)", source)
        self.assertIn("dist finalization lock timed out", source)
        self.assertIn("backup_identity", source)
        self.assertIn("backup_sha256", source)
        self.assertIn("stage_size", source)
        self.assertIn("stage_sha256", source)
        self.assertIn("_hash_regular_at(\n                transaction_fd,\n                member[\"backup_name\"]", source)
        self.assertIn('"--expected-identity",', source)
        self.assertIn("_verified_recovery_path", source)
        self.assertIn("suppress_output=True", source)
        self.assertIn("identity-verified recovery backup", source)
        self.assertIn("no identity-verified recovery backup available", source)
        self.assertIn("BUILD_DIST_ABORT_PHASE", source)
        self.assertIn("_write_journal", source)
        self.assertIn("_recover_transactions", source)
        self.assertIn("MAX_SCAN_ENTRIES = 256", source)
        self.assertIn("MAX_TRANSACTIONS = 32", source)
        self.assertIn("TRANSACTION_RE", source)
        self.assertIn("os.scandir(parent_fd)", source)
        self.assertIn('"--dst-must-not-exist",', source)
        self.assertIn("_copy_external_stage", source)
        self.assertNotIn('"build-dist stage claim"', source)
        self.assertIn("_open_lock_parent_chain", source)
        self.assertIn("os.O_NOFOLLOW", source)
        self.assertIn('assert-private-chain build-dist "${dist_dir}" --allow-missing', source)
        self.assertIn('assert-private-chain build-dist "${dist_dir}"', source)
        self.assertNotIn("lock_fd = os.open", source)
        self.assertNotIn("os.fdopen(lock_fd", source)
        self.assertNotIn("os.stat(lock_name", source)
        cleanup = self._cleanup_program()
        self.assertIn("local primary_status=$?", cleanup)
        self.assertIn("cleanup_deadline", cleanup)
        self.assertIn("timeout --signal=TERM", cleanup)
        self.assertIn("cleanup_failed=1", cleanup)
        self.assertNotIn("/dev/null", cleanup)
        self.assertNotIn("|| true", cleanup)


if __name__ == "__main__":
    unittest.main()
