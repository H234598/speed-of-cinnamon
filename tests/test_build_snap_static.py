from __future__ import annotations

import fcntl
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_SNAP = REPO_ROOT / "scripts" / "build-snap.sh"
SNAPCRAFT_YAML = REPO_ROOT / "snap" / "snapcraft.yaml"


class BuildSnapStaticTest(unittest.TestCase):
    def _require_safe_local_fs_root_identity(self) -> None:
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            root_stat = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != 0
            or root_stat.st_mode & 0o022
        ):
            self.skipTest(
                "safe-local-fs requires / to be a uid-0-owned directory without "
                "group/other write bits; "
                f"observed uid={root_stat.st_uid} "
                f"mode={stat.S_IMODE(root_stat.st_mode):04o}"
            )

    def _finalizer_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        start = source.index("<<'PY'\n") + len("<<'PY'\n")
        end = source.index("\nPY\n}", start)
        return source[start:end]

    def _run_finalizer(
        self,
        program: str,
        safe_fs: Path,
        lock: Path,
        stage: Path,
        final: Path,
        *,
        previous: Path | None = None,
        recovery: Path | None = None,
        finalize_timeout: int = 30,
        lock_timeout: int = 2,
        process_timeout: float = 30,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if previous is None:
            previous = final.with_name(f"{final.name}.previous")
        if recovery is None:
            recovery = final.parent / f".{final.name}.previous-recovery-{'a' * 32}"
        return subprocess.run(
            [
                sys.executable,
                "-",
                str(lock),
                str(safe_fs),
                str(stage),
                str(final),
                str(previous),
                str(recovery),
                str(finalize_timeout),
                str(lock_timeout),
            ],
            input=program,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=env,
            check=False,
            timeout=process_timeout,
        )

    def _scan_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        marker = 'python3 - \\\n    "${SNAP_SCAN_MAX_ENTRIES}"'
        marker_start = source.index(marker)
        start = source.index("<<'PY'\n", marker_start) + len("<<'PY'\n")
        end = source.index('\nPY\n)"; then', start)
        return source[start:end]

    def _sweep_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        marker = (
            'if ! timeout --signal=TERM --kill-after='
            '"${SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS}s"'
        )
        marker_start = source.index(marker)
        start = source.index("<<'PY'\n", marker_start) + len("<<'PY'\n")
        end = source.index("\nPY\nthen\n", start)
        return source[start:end]

    def _run_scan(
        self,
        program: str,
        workspace: Path,
        workspace_dist: Path,
        *,
        max_entries: int = 4096,
        max_candidates: int = 2,
        max_path_bytes: int = 4096,
        max_total_bytes: int = 16384,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-",
                str(max_entries),
                str(max_candidates),
                str(max_path_bytes),
                str(max_total_bytes),
                "1.2.3",
                str(workspace),
                str(workspace_dist),
            ],
            input=program,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=10,
        )

    def _run_sweep(
        self,
        program: str,
        tmp_parent: Path,
        safe_fs: Path,
        *,
        timeout_seconds: int = 30,
        max_entries: int = 256,
        max_stale: int = 32,
        process_timeout: float = 10,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-",
                str(tmp_parent),
                str(safe_fs),
                str(timeout_seconds),
                str(max_entries),
                str(max_stale),
            ],
            input=program,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=process_timeout,
        )

    def _write_cleanup_failure_wrapper(self, path: Path, *, real: Path) -> None:
        path.write_text(
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'remove-leaf':\n"
            "    raise SystemExit(77)\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *args])\n",
            encoding="utf-8",
        )

    def _write_cleanup_disappearing_wrapper(
        self,
        path: Path,
        *,
        real: Path,
        target: Path,
    ) -> None:
        path.write_text(
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            f"if args and args[0] == 'remove-leaf' and args[2] == {str(target)!r}:\n"
            "    os.unlink(args[2])\n"
            "    raise SystemExit(77)\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *args])\n",
            encoding="utf-8",
        )

    def _write_delayed_safe_fs_wrapper(
        self,
        path: Path,
        *,
        real: Path,
        delay: float,
        operation: str | None = None,
        target: Path | None = None,
    ) -> None:
        operation_literal = repr(operation)
        target_literal = repr(str(target))
        path.write_text(
            "import os, sys, time\n"
            "args = sys.argv[1:]\n"
            f"operation = {operation_literal}\n"
            f"target = {target_literal}\n"
            "if operation is None or (args and args[0] == operation and ((operation == 'remove-leaf' and args[2] == target) or (operation in ('replace', 'exchange') and args[3] == target))):\n"
            f"    time.sleep({delay!r})\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *sys.argv[1:]])\n",
            encoding="utf-8",
        )

    def _write_activation_failure_wrapper(self, path: Path, *, real: Path) -> None:
        path.write_text(
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] in {'replace', 'exchange'}:\n"
            "    raise SystemExit(17)\n"
            f"os.execv(sys.executable, [sys.executable, {str(real)!r}, *args])\n",
            encoding="utf-8",
        )

    def _write_first_publish_race_wrapper(
        self,
        path: Path,
        *,
        real: Path,
        target: Path,
    ) -> None:
        path.write_text(
            "import os, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            f"target = {str(target)!r}\n"
            "if args and args[0] == 'replace' and len(args) > 3 and args[3] == target:\n"
            "    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)\n"
            "    with os.fdopen(fd, 'wb') as handle:\n"
            "        handle.write(b'racer\\n')\n"
            f"result = subprocess.run([sys.executable, {str(real)!r}, *args], check=False)\n"
            "raise SystemExit(result.returncode)\n",
            encoding="utf-8",
        )

    def _write_kill_wrapper(
        self,
        path: Path,
        *,
        real: Path,
        operation: str,
        target: Path,
        phase: str,
    ) -> None:
        path.write_text(
            "import os, signal, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            f"operation = {operation!r}\n"
            f"target = {str(target)!r}\n"
            f"phase = {phase!r}\n"
            "matches = args and args[0] == operation and ((args[0] == 'remove-leaf' and args[2] == target) or (args[0] in ('replace', 'exchange') and args[3] == target))\n"
            "if matches and phase == 'before':\n"
            "    os.kill(os.getppid(), signal.SIGKILL)\n"
            "    os._exit(137)\n"
            f"result = subprocess.run([sys.executable, {str(real)!r}, *args], check=False)\n"
            "if matches and phase == 'after' and result.returncode == 0:\n"
            "    os.kill(os.getppid(), signal.SIGKILL)\n"
            "sys.exit(result.returncode)\n",
            encoding="utf-8",
        )

    def _stage_path(self, final: Path, token: str = "a" * 32) -> Path:
        return final.parent / f".{final.name}.staging-{token}"

    def _recovery_path(self, final: Path, token: str = "b" * 32) -> Path:
        return final.parent / f".{final.name}.previous-recovery-{token}"

    def _tmp_validation_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        start = source.index("validate_snap_tmp_root() {")
        end = source.index("\n}\n\nrepo_tmp_root=", start) + 2
        return source[start:end]

    def _run_tmp_validation(self, program: str, root: Path) -> subprocess.CompletedProcess[str]:
        script = (
            "set -u\n"
            'repo_dir="$1"\n'
            'repo_tmp_root=""\n'
            + program
            + "\nif validate_snap_tmp_root \"$2\"; then exit 0; else exit 1; fi\n"
        )
        return subprocess.run(
            ["bash", "-c", script, "tmp", str(REPO_ROOT), str(root)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=10,
        )

    def _cleanup_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        start = source.index("cleanup_deadline_us=0")
        end = source.index("\n}\ntrap cleanup_tmpdir EXIT", start) + 2
        return source[start:end]

    def _publish_tail(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        start = source.index('snap_previous_path="${output_path}.previous"')
        end = source.index("\nprintf '%s", start)
        return source[start:end]

    def _run_publish_harness(
        self,
        stub: Path,
        dist: Path,
        stage: Path,
        activation_body: str,
    ) -> subprocess.CompletedProcess[str]:
        script = (
            "set -euo pipefail\n"
            'safe_fs_cmd=(bash "$1")\n'
            'SNAP_CLEANUP_TIMEOUT_SECONDS=30\n'
            'snap_workspace=""\n'
            'snap_workspace_identity=""\n'
            'dist_dir="$2"\n'
            'dist_parent="$2"\n'
            'snap_filename="published.snap"\n'
            'snap_file="$3"\n'
            'snap_stage_path="$3"\n'
            'snap_stage_identity="stage-id"\n'
            'output_path="${dist_dir}/${snap_filename}"\n'
            "activate_snap_output() { "
            + activation_body
            + "; }\n"
            + self._cleanup_program()
            + "\ntrap cleanup_tmpdir EXIT\n"
            + self._publish_tail()
            + "\nexit 0\n"
        )
        return subprocess.run(
            ["bash", "-c", script, "publish", str(stub), str(dist), str(stage)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=10,
        )

    def _outer_shell_harness_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        activation_start = source.index("activate_snap_output() {")
        activation_end = source.index("\n}\n\nfor tool in", activation_start) + 2
        validation_start = source.index("validate_snap_tmp_root() {")
        validation_end = source.index("\n}\n\nrepo_tmp_root=", validation_start) + 2
        private_start = source.index('snap_tmp_parent="${repo_tmp_root}/')
        sweep_start = source.index(
            'if ! timeout --signal=TERM --kill-after='
            '"${SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS}s"',
            private_start,
        )
        workspace_start = source.index('snap_workspace="$(mktemp -d', sweep_start)
        workspace_end = source.index('snap_stage_identity=""', workspace_start) + len(
            'snap_stage_identity=""'
        )
        identity_start = source.index("if ! snap_workspace_identity=", workspace_end)
        identity_end = source.index("\n\n", identity_start)
        return (
            "set -euo pipefail\n"
            'repo_dir="$1"\n'
            'safe_fs="$2"\n'
            'repo_tmp_root="$3"\n'
            'dist_dir="$4"\n'
            'snap_file="$5"\n'
            'activation_mode="$6"\n'
            'safe_fs_cmd=(python3 "$safe_fs")\n'
            "SNAP_FINALIZE_TIMEOUT_SECONDS=120\n"
            "SNAP_FINALIZE_KILL_AFTER_SECONDS=10\n"
            "SNAP_FINALIZE_LOCK_TIMEOUT_SECONDS=30\n"
            "SNAP_STARTUP_SWEEP_TIMEOUT_SECONDS=30\n"
            "SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS=1\n"
            "SNAP_STARTUP_SWEEP_MAX_ENTRIES=256\n"
            "SNAP_STARTUP_SWEEP_MAX_STALE=32\n"
            + source[validation_start:validation_end]
            + '\nif ! validate_snap_tmp_root "${repo_tmp_root}"; then exit 1; fi\n'
            + source[private_start:sweep_start]
            + source[sweep_start:workspace_start]
            + source[workspace_start:workspace_end]
            + "\nSNAP_CLEANUP_TIMEOUT_SECONDS=30\n"
            + self._cleanup_program()
            + "\ntrap cleanup_tmpdir EXIT\n"
            + source[identity_start:identity_end]
            + '\n'
            + 'snap_filename="published.snap"\n'
            + 'output_path="${dist_dir}/${snap_filename}"\n'
            + 'snap_stage_path="${dist_dir}/.${snap_filename}.staging-cccccccccccccccccccccccccccccccc"\n'
            + 'if ! "${safe_fs_cmd[@]}" copy-file build-snap "${snap_file}" "${snap_stage_path}" 0644 --dst-must-not-exist; then exit 1; fi\n'
            + 'snap_stage_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_stage_path}" --kind file)"\n'
            + source[activation_start:activation_end]
            + "\n"
            + self._publish_tail()
            + "\nexit 0\n"
        )

    def _run_outer_shell_harness(
        self,
        program: str,
        repo_tmp_root: Path,
        dist: Path,
        snap_file: Path,
        activation_mode: str,
        safe_fs: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        if safe_fs is None:
            safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        return subprocess.run(
            [
                "bash",
                "-c",
                program,
                "outer",
                str(REPO_ROOT),
                str(safe_fs),
                str(repo_tmp_root),
                str(dist),
                str(snap_file),
                activation_mode,
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=15,
        )

    def _run_activation_watchdog_harness(
        self,
        activation: str,
        lock: Path,
        stage: Path,
        final: Path,
        previous: Path,
        recovery: Path,
    ) -> subprocess.CompletedProcess[str]:
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        script = (
            "set +e\n"
            'safe_fs="$1"\n'
            "SNAP_FINALIZE_TIMEOUT_SECONDS=2\n"
            "SNAP_FINALIZE_KILL_AFTER_SECONDS=1\n"
            "SNAP_FINALIZE_LOCK_TIMEOUT_SECONDS=1\n"
            + activation
            + '\nactivate_snap_output "$2" "$3" "$4" "$5" "$6"\n'
            + 'status=$?\nprintf "status=%s\\n" "$status"\n'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "watchdog",
                str(safe_fs),
                str(lock),
                str(stage),
                str(final),
                str(previous),
                str(recovery),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=6,
        )

    def _run_exit_cleanup(
        self,
        cleanup_function: str,
        stub: Path,
        stage: Path,
        workspace: Path,
        primary_status: int,
        cleanup_timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        script = (
            "set -u\n"
            'safe_fs_cmd=(bash "$1")\n'
            f"SNAP_CLEANUP_TIMEOUT_SECONDS={cleanup_timeout}\n"
            'snap_stage_path="$2"\n'
            'snap_stage_identity="stage-id"\n'
            'snap_workspace="$3"\n'
            'snap_workspace_identity="workspace-id"\n'
            f"{cleanup_function}\n"
            "trap cleanup_tmpdir EXIT\n"
            'exit "$4"\n'
        )
        return subprocess.run(
            [
                "bash",
                "-c",
                script,
                "cleanup",
                str(stub),
                str(stage),
                str(workspace),
                str(primary_status),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=10,
        )

    def test_snap_scan_accepts_one_regular_candidate(self) -> None:
        program = self._scan_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace_dist = root / "workspace-dist"
            workspace.mkdir()
            workspace_dist.mkdir()
            candidate = workspace / "speed-of-cinnamon_1.2.3_one.snap"
            candidate.write_bytes(b"snap\n")

            result = self._run_scan(program, workspace, workspace_dist)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, str(candidate))

    def test_snap_scan_rejects_more_than_two_candidates(self) -> None:
        program = self._scan_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace_dist = root / "workspace-dist"
            workspace.mkdir()
            workspace_dist.mkdir()
            for suffix in ("one", "two", "three"):
                (workspace / f"speed-of-cinnamon_1.2.3_{suffix}.snap").write_bytes(b"snap\n")

            result = self._run_scan(program, workspace, workspace_dist)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snap candidate limit exceeded", result.stderr)

    def test_snap_scan_rejects_overlong_candidate_path_and_symlink(self) -> None:
        program = self._scan_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace_dist = root / "workspace-dist"
            workspace.mkdir()
            workspace_dist.mkdir()
            candidate = workspace / "speed-of-cinnamon_1.2.3_one.snap"
            candidate.write_bytes(b"snap\n")

            result = self._run_scan(
                program,
                workspace,
                workspace_dist,
                max_path_bytes=len(os.fsencode(str(candidate))) - 1,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("path length limit exceeded", result.stderr)

            candidate.unlink()
            victim = workspace / "victim"
            victim.write_bytes(b"keep\n")
            candidate.symlink_to(victim)
            result = self._run_scan(program, workspace, workspace_dist)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not be a symlink", result.stderr)
            self.assertEqual(victim.read_bytes(), b"keep\n")

    def test_snap_scan_rejects_total_candidate_path_bytes(self) -> None:
        program = self._scan_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = root / "workspace"
            workspace_dist = root / "workspace-dist"
            workspace.mkdir()
            workspace_dist.mkdir()
            first = workspace / "speed-of-cinnamon_1.2.3_one.snap"
            second = workspace_dist / "speed-of-cinnamon_1.2.3_two.snap"
            first.write_bytes(b"snap\n")
            second.write_bytes(b"snap\n")
            total_bytes = len(os.fsencode(str(first))) + 1

            result = self._run_scan(
                program,
                workspace,
                workspace_dist,
                max_total_bytes=total_bytes,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("path byte limit exceeded", result.stderr)

    def test_snap_startup_sweep_is_bounded_and_ignores_foreign_names(self) -> None:
        self._require_safe_local_fs_root_identity()
        program = self._sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tmp_parent = root / "snap-parent"
            tmp_parent.mkdir()
            tmp_parent.chmod(0o700)
            stale = tmp_parent / "speed-of-cinnamon-snap-tree-aaaaaa"
            stale.mkdir()
            stale.chmod(0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))
            foreign = tmp_parent / "speed-of-cinnamon-snap-tree-foreign"
            foreign.write_bytes(b"keep\n")

            result = self._run_sweep(program, tmp_parent, safe_fs)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stale.exists())
            self.assertEqual(foreign.read_bytes(), b"keep\n")

    def test_snap_startup_sweep_handles_directory_tombstones_fail_closed(self) -> None:
        self._require_safe_local_fs_root_identity()
        program = self._sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tmp_parent = root / "tombstone-parent"
            tmp_parent.mkdir()
            tmp_parent.chmod(0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            old_tombstones = (
                tmp_parent / f"speed-of-cinnamon-snap-tree-aaaaaa.final-{'a' * 32}",
                tmp_parent / f".speed-of-cinnamon-snap-tree-bbbbbb.final-{'b' * 32}",
                tmp_parent
                / f"speed-of-cinnamon-snap-tree-cccccc.final-{'c' * 32}.final-{'d' * 32}",
            )
            for tombstone in old_tombstones:
                tombstone.mkdir()
                tombstone.chmod(0o700)
                os.utime(tombstone, (old_time, old_time))
            foreign = tmp_parent / f".foreign-snap-tree-cccccc.final-{'c' * 32}"
            foreign.mkdir()
            foreign.chmod(0o700)

            result = self._run_sweep(program, tmp_parent, safe_fs)

            self.assertEqual(result.returncode, 0, result.stderr)
            for tombstone in old_tombstones:
                self.assertFalse(tombstone.exists())
            self.assertTrue(foreign.exists())

            recent_parent = root / "recent-tombstone-parent"
            recent_parent.mkdir()
            recent_parent.chmod(0o700)
            recent = recent_parent / f".speed-of-cinnamon-snap-tree-dddddd.final-{'d' * 32}"
            recent.mkdir()
            recent.chmod(0o700)

            result = self._run_sweep(program, recent_parent, safe_fs)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("recent snap temporary workspace requires recovery", result.stderr)
            self.assertTrue(recent.exists())

    def test_snap_startup_sweep_eintr_retry_obeys_deadline(self) -> None:
        program = self._sweep_program()
        program = program.replace(
            "deadline = time.monotonic() + timeout_seconds",
            "_clock = [0]\n"
            "def _test_monotonic():\n"
            "    _clock[0] += 1\n"
            "    return float(_clock[0] - 1)\n"
            "time.monotonic = _test_monotonic\n"
            "_real_flock = fcntl.flock\n"
            "def _eintr_flock(fd, operation):\n"
            "    raise InterruptedError\n"
            "fcntl.flock = _eintr_flock\n"
            "deadline = time.monotonic() + timeout_seconds",
            1,
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_parent = Path(tmp) / "eintr-parent"
            tmp_parent.mkdir()
            tmp_parent.chmod(0o700)

            result = self._run_sweep(
                program,
                tmp_parent,
                safe_fs,
                timeout_seconds=1,
                process_timeout=2,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snap temporary workspace sweep deadline exceeded", result.stderr)

    def test_snap_startup_sweep_rejects_recent_artifact_and_stale_overflow(self) -> None:
        program = self._sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            recent_parent = root / "recent-parent"
            recent_parent.mkdir()
            recent_parent.chmod(0o700)
            recent = recent_parent / "speed-of-cinnamon-snap-tree-aaaaaa"
            recent.mkdir()
            recent.chmod(0o700)

            result = self._run_sweep(program, recent_parent, safe_fs)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("recent snap temporary workspace requires recovery", result.stderr)
            self.assertTrue(recent.exists())

            overflow_parent = root / "overflow-parent"
            overflow_parent.mkdir()
            overflow_parent.chmod(0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            for index in range(3):
                artifact = overflow_parent / f"speed-of-cinnamon-snap-tree-{index:06d}"
                artifact.mkdir()
                artifact.chmod(0o700)
                os.utime(artifact, (old_time, old_time))

            result = self._run_sweep(
                program,
                overflow_parent,
                safe_fs,
                max_stale=2,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("sweep exceeds max 2", result.stderr)

    def test_snap_tmp_root_accepts_private_and_rejects_foreign_writable_shape(self) -> None:
        program = self._tmp_validation_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            accepted = self._run_tmp_validation(program, root)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)

            root.chmod(0o777)
            rejected = self._run_tmp_validation(program, root)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("euid-owned private or root-owned sticky standard temp", rejected.stderr)

    def test_snap_publish_kill_at_each_publish_phase_preserves_recovery(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        cases = (
            ("first-before", None, "replace", "final", "before"),
            ("first-after", None, "replace", "final", "after"),
            ("exchange-before", b"old\n", "exchange", "final", "before"),
            ("exchange-after", b"old\n", "exchange", "final", "after"),
            ("previous-before", b"old\n", "replace", "previous", "before"),
            ("previous-after", b"old\n", "replace", "previous", "after"),
            ("recovery-before", b"old-current\n", "replace", "recovery", "before"),
            ("recovery-after", b"old-current\n", "replace", "recovery", "after"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, final_content, operation, target_kind, phase in cases:
                with self.subTest(name=name):
                    case_root = root / name
                    dist = case_root / "dist"
                    dist.mkdir(parents=True)
                    final = dist / "output.snap"
                    stage = self._stage_path(final)
                    stage.write_bytes(b"new\n")
                    if final_content is not None:
                        final.write_bytes(final_content)
                    previous = final.with_name(f"{final.name}.previous")
                    recovery = self._recovery_path(final)
                    if target_kind == "recovery":
                        previous.write_bytes(b"old-previous\n")
                    target = {
                        "final": final,
                        "previous": previous,
                        "recovery": recovery,
                    }[target_kind]
                    wrapper = case_root / "safe-fs-kill.py"
                    self._write_kill_wrapper(
                        wrapper,
                        real=safe_fs,
                        operation=operation,
                        target=target,
                        phase=phase,
                    )

                    result = self._run_finalizer(
                        program,
                        wrapper,
                        dist / ".finalize.lock",
                        stage,
                        final,
                        previous=previous,
                        recovery=recovery,
                    )

                    self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
                    if target_kind == "final" and operation == "replace":
                        if phase == "before":
                            self.assertFalse(final.exists())
                            self.assertEqual(stage.read_bytes(), b"new\n")
                        else:
                            self.assertEqual(final.read_bytes(), b"new\n")
                            self.assertFalse(stage.exists())
                    elif target_kind == "final":
                        if phase == "before":
                            self.assertEqual(final.read_bytes(), b"old\n")
                            self.assertEqual(stage.read_bytes(), b"new\n")
                        else:
                            self.assertEqual(final.read_bytes(), b"new\n")
                            self.assertEqual(stage.read_bytes(), b"old\n")
                    elif target_kind == "previous":
                        self.assertEqual(final.read_bytes(), b"new\n")
                        if phase == "before":
                            self.assertEqual(stage.read_bytes(), b"old\n")
                            self.assertFalse(previous.exists())
                        else:
                            self.assertFalse(stage.exists())
                            self.assertEqual(previous.read_bytes(), b"old\n")
                    else:
                        self.assertEqual(final.read_bytes(), b"old-current\n")
                        self.assertEqual(stage.read_bytes(), b"new\n")
                        if phase == "before":
                            self.assertEqual(previous.read_bytes(), b"old-previous\n")
                            self.assertFalse(recovery.exists())
                        else:
                            self.assertFalse(previous.exists())
                            self.assertEqual(recovery.read_bytes(), b"old-previous\n")

    def test_snap_first_publish_no_clobbers_injected_destination_race(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            final = dist / "output.snap"
            stage = self._stage_path(final)
            stage.write_bytes(b"new\n")
            wrapper = root / "safe-fs-first-publish-race.py"
            self._write_first_publish_race_wrapper(wrapper, real=safe_fs, target=final)

            result = self._run_finalizer(
                program,
                wrapper,
                dist / ".finalize.lock",
                stage,
                final,
            )

            self.assertNotEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final.read_bytes(), b"racer\n")
            self.assertEqual(stage.read_bytes(), b"new\n")

    def test_snap_postcommit_cleanup_timeout_preserves_final_and_recovery(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            final = dist / "output.snap"
            stage = self._stage_path(final)
            previous = final.with_name(f"{final.name}.previous")
            recovery = self._recovery_path(final)
            stage.write_bytes(b"new\n")
            final.write_bytes(b"old-current\n")
            previous.write_bytes(b"old-previous\n")
            delayed_safe_fs = root / "safe-fs-delayed-cleanup.py"
            self._write_delayed_safe_fs_wrapper(
                delayed_safe_fs,
                real=safe_fs,
                delay=3.0,
                operation="remove-leaf",
                target=recovery,
            )

            result = self._run_finalizer(
                program,
                delayed_safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                previous=previous,
                recovery=recovery,
                finalize_timeout=2,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final.read_bytes(), b"new\n")
            self.assertEqual(previous.read_bytes(), b"old-current\n")
            self.assertEqual(recovery.read_bytes(), b"old-previous\n")
            self.assertIn("identity-verified previous recovery path", result.stderr)
            self.assertIn(str(recovery), result.stderr)

    def test_snap_next_start_refuses_unresolved_recovery_without_deleting_it(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            final = dist / "output.snap"
            stage = self._stage_path(final)
            previous = final.with_name(f"{final.name}.previous")
            recovery = self._recovery_path(final)
            stage.write_bytes(b"new\n")
            final.write_bytes(b"old-current\n")
            previous.write_bytes(b"old-previous\n")
            wrapper = root / "safe-fs-kill-recovery.py"
            self._write_kill_wrapper(
                wrapper,
                real=safe_fs,
                operation="replace",
                target=recovery,
                phase="after",
            )

            killed = self._run_finalizer(
                program,
                wrapper,
                dist / ".finalize.lock",
                stage,
                final,
                previous=previous,
                recovery=recovery,
            )
            self.assertEqual(killed.returncode, -signal.SIGKILL, killed.stderr)
            self.assertTrue(recovery.exists())
            self.assertFalse(previous.exists())

            restarted = self._run_finalizer(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                previous=previous,
                recovery=recovery,
            )

            self.assertNotEqual(restarted.returncode, 0)
            self.assertIn("requires manual recovery", restarted.stderr)
            self.assertEqual(final.read_bytes(), b"old-current\n")
            self.assertEqual(recovery.read_bytes(), b"old-previous\n")

    def test_snap_postcommit_cleanup_missing_recovery_does_not_claim_path(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            final = dist / "output.snap"
            stage = self._stage_path(final)
            previous = final.with_name(f"{final.name}.previous")
            recovery = self._recovery_path(final)
            stage.write_bytes(b"new\n")
            final.write_bytes(b"old-current\n")
            previous.write_bytes(b"old-previous\n")
            disappearing_safe_fs = root / "safe-fs-disappearing-cleanup.py"
            self._write_cleanup_disappearing_wrapper(
                disappearing_safe_fs,
                real=safe_fs,
                target=recovery,
            )

            result = self._run_finalizer(
                program,
                disappearing_safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                previous=previous,
                recovery=recovery,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final.read_bytes(), b"new\n")
            self.assertFalse(recovery.exists())
            self.assertIn("no identity-verified previous recovery path available", result.stderr)
            self.assertNotIn(str(recovery), result.stderr)

    def test_snap_upgrade_exchange_preserves_previous_output(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            final = dist / "output.snap"
            stage = self._stage_path(final)
            stage.write_bytes(b"new\n")
            final.write_bytes(b"old\n")

            result = self._run_finalizer(program, safe_fs, lock, stage, final)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final.read_bytes(), b"new\n")
            self.assertFalse(stage.exists())
            previous = final.with_name(f"{final.name}.previous")
            self.assertEqual(previous.read_bytes(), b"old\n")
            self.assertFalse(self._recovery_path(final).exists())

    def test_snap_previous_cleanup_error_keeps_active_output_and_reports_verified_path(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            final = dist / "output.snap"
            stage = self._stage_path(final)
            stage.write_bytes(b"new\n")
            final.write_bytes(b"old-current\n")
            previous = final.with_name(f"{final.name}.previous")
            previous.write_bytes(b"old-previous\n")
            recovery = self._recovery_path(final)
            failing_safe_fs = root / "safe-fs-failure.py"
            self._write_cleanup_failure_wrapper(failing_safe_fs, real=safe_fs)

            result = self._run_finalizer(
                program,
                failing_safe_fs,
                lock,
                stage,
                final,
                previous=previous,
                recovery=recovery,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(final.read_bytes(), b"new\n")
            self.assertEqual(previous.read_bytes(), b"old-current\n")
            self.assertEqual(recovery.read_bytes(), b"old-previous\n")
            self.assertIn("identity-verified previous recovery path", result.stderr)
            self.assertIn(str(recovery), result.stderr)

    def test_snap_publish_clears_stage_before_successful_exit_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            stage = root / "stage.snap"
            stage.write_bytes(b"published\n")
            stub = root / "cleanup-failure.sh"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'cleanup invoked\\n' >&2\n"
                "exit 42\n",
                encoding="utf-8",
            )

            result = self._run_publish_harness(
                stub,
                dist,
                stage,
                'mv -- "$2" "$3"',
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(stage.exists())
            self.assertEqual((dist / "published.snap").read_bytes(), b"published\n")
            self.assertNotIn("cleanup invoked", result.stderr)

    def test_snap_publish_failure_keeps_stage_recovery_and_primary_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            stage = root / "stage.snap"
            stage.write_bytes(b"staged\n")
            stub = root / "cleanup-failure.sh"
            stub.write_text(
                "#!/usr/bin/env bash\n"
                "printf 'cleanup invoked\\n' >&2\n"
                "exit 42\n",
                encoding="utf-8",
            )

            result = self._run_publish_harness(
                stub,
                dist,
                stage,
                'printf "activation failed\\n" >&2; return 17',
            )

            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertTrue(stage.exists())
            self.assertFalse((dist / "published.snap").exists())
            self.assertIn("activation failed", result.stderr)
            self.assertIn("cleanup invoked", result.stderr)
            self.assertIn("preserving primary exit status: 17", result.stderr)

    def test_snap_outer_shell_chain_runs_real_workspace_trap_and_publish(self) -> None:
        program = self._outer_shell_harness_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for activation_mode in ("success", "failure"):
                with self.subTest(activation_mode=activation_mode):
                    self._require_safe_local_fs_root_identity()
                    case_root = root / activation_mode
                    case_root.mkdir()
                    case_root.chmod(0o700)
                    dist = case_root / "dist"
                    dist.mkdir()
                    dist.chmod(0o700)
                    snap_file = case_root / "input.snap"
                    snap_file.write_bytes(b"harness-snap\n")
                    snap_file.chmod(0o600)
                    safe_fs = None
                    if activation_mode == "failure":
                        safe_fs = case_root / "safe-fs-failure.py"
                        self._write_activation_failure_wrapper(
                            safe_fs,
                            real=REPO_ROOT / "scripts" / "safe-local-fs.py",
                        )

                    result = self._run_outer_shell_harness(
                        program,
                        case_root,
                        dist,
                        snap_file,
                        activation_mode,
                        safe_fs,
                    )

                    if activation_mode == "success":
                        self.assertEqual(result.returncode, 0, result.stderr)
                    else:
                        self.assertNotEqual(result.returncode, 0, result.stderr)
                    tmp_parent = case_root / f"speed-of-cinnamon-snap-{os.geteuid()}"
                    self.assertTrue(tmp_parent.is_dir())
                    self.assertEqual(list(tmp_parent.iterdir()), [])
                    if activation_mode == "success":
                        self.assertEqual((dist / "published.snap").read_bytes(), b"harness-snap\n")
                        self.assertFalse(
                            (dist / ".published.snap.staging-cccccccccccccccccccccccccccccccc").exists()
                        )
                    else:
                        self.assertFalse((dist / "published.snap").exists())

    def test_snap_finalizer_watchdog_keeps_term_and_kill_inside_total_budget(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        activation_start = source.index("activate_snap_output() {")
        activation_end = source.index("\n}\n\nfor tool in", activation_start) + 2
        activation = source[activation_start:activation_end].replace(
            "import time\n",
            "import time\n"
            "import signal\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "time.sleep(60)\n",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = dist / ".stage"
            final = dist / "output.snap"
            previous = dist / "output.snap.previous"
            recovery = dist / "output.snap.recovery"

            started = time.monotonic()
            result = self._run_activation_watchdog_harness(
                activation, lock, stage, final, previous, recovery
            )
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("status=137", result.stdout)
            self.assertLess(elapsed, 2.8, result.stderr)

    def test_snap_finalizer_eintr_retry_obeys_lock_deadline(self) -> None:
        program = self._finalizer_program()
        marker = (
            "lock_path, safe_fs, staging_path, final_path, previous_path, "
            "previous_recovery_path, finalize_timeout, lock_timeout = sys.argv[1:]"
        )
        program = program.replace(
            marker,
            "_clock = [0]\n"
            "def _test_monotonic():\n"
            "    _clock[0] += 1\n"
            "    return float(_clock[0] - 1)\n"
            "time.monotonic = _test_monotonic\n"
            "def _eintr_flock(fd, operation):\n"
            "    raise InterruptedError\n"
            "fcntl.flock = _eintr_flock\n"
            + marker,
            1,
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            started = time.monotonic()
            try:
                result = self._run_finalizer(
                    program,
                    safe_fs,
                    lock,
                    root / "stage",
                    dist / "output",
                    lock_timeout=2,
                    process_timeout=2,
                )
            except subprocess.TimeoutExpired as exc:
                self.fail(f"EINTR retry exceeded process bound: {exc}")
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snap finalization lock timed out", result.stderr)
            self.assertLess(elapsed, 1.5, result.stderr)

    def test_snap_finalizer_uses_one_absolute_deadline_for_multiple_safe_fs_calls(self) -> None:
        program = self._finalizer_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            final = dist / "output.snap"
            stage = self._stage_path(final)
            stage.write_bytes(b"new\n")
            final.write_bytes(b"old\n")
            delayed_safe_fs = root / "safe-fs-delayed.py"
            self._write_delayed_safe_fs_wrapper(delayed_safe_fs, real=safe_fs, delay=0.6)

            started = time.monotonic()
            result = self._run_finalizer(
                program,
                delayed_safe_fs,
                lock,
                stage,
                final,
                finalize_timeout=1,
            )
            elapsed = time.monotonic() - started

            self.assertNotEqual(result.returncode, 0)
            self.assertLess(elapsed, 1.5, result.stderr)
            self.assertIn("snap finalization deadline exceeded", result.stderr)

    def test_snap_finalizer_rejects_safe_fs_success_after_deadline(self) -> None:
        program = self._finalizer_program()
        marker = "finalizer_deadline = time.monotonic() + finalize_timeout_seconds"
        program = program.replace(
            marker,
            "_clock = [0]\n"
            "def _test_monotonic():\n"
            "    _clock[0] += 1\n"
            "    return 0.0 if _clock[0] <= 3 else 2.0\n"
            "time.monotonic = _test_monotonic\n"
            + marker,
            1,
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            final = dist / "output.snap"
            stage = self._stage_path(final)
            stage.write_bytes(b"new\n")

            result = self._run_finalizer(
                program,
                safe_fs,
                lock,
                stage,
                final,
                finalize_timeout=1,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "snap finalization deadline exceeded after safe filesystem operation",
                result.stderr,
            )
            self.assertEqual(final.read_bytes(), b"new\n")

    def test_snap_exit_cleanup_reports_failure_and_preserves_primary_status(self) -> None:
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
            stage = root / "stage.snap"
            workspace = root / "workspace"

            for primary_status, expected_status in ((0, 1), (7, 7)):
                result = self._run_exit_cleanup(
                    cleanup_function,
                    stub,
                    stage,
                    workspace,
                    primary_status,
                )

                self.assertEqual(result.returncode, expected_status, result.stderr)
                self.assertIn("stub cleanup failure", result.stderr)
                if primary_status == 0:
                    self.assertIn("final output status changed to failure", result.stderr)
                else:
                    self.assertIn("preserving primary exit status: 7", result.stderr)

    def test_snap_exit_cleanup_uses_one_absolute_deadline(self) -> None:
        cleanup_function = self._cleanup_program()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            delayed_safe_fs = root / "safe-fs-delayed.py"
            delayed_safe_fs.write_text(
                "#!/usr/bin/env bash\n"
                "sleep 0.6\n"
                "exit 0\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            result = self._run_exit_cleanup(
                cleanup_function,
                delayed_safe_fs,
                root / "stage.snap",
                root / "workspace",
                0,
                cleanup_timeout=1,
            )
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertLess(elapsed, 1.8, result.stderr)
            self.assertIn("snap EXIT cleanup deadline exceeded", result.stderr)

    def test_snap_finalization_rejects_parent_path_exchange_after_open(self) -> None:
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

            result = self._run_finalizer(program, safe_fs, lock, root / "stage", dist / "output")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snap finalization lock parent path changed", result.stderr)

    def test_snap_finalization_directory_fd_flock_survives_lockfile_rename_recreate(self) -> None:
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
                    root / "stage",
                    dist / "output",
                    lock_timeout=0,
                )
            finally:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
                os.close(parent_fd)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snap finalization lock timed out", result.stderr)
            self.assertEqual(lock.read_text(encoding="utf-8"), "recreated\n")

    def test_snap_finalization_directory_fd_lock_is_busy(self) -> None:
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
                    root / "stage",
                    dist / "output",
                    lock_timeout=0,
                )
            finally:
                fcntl.flock(parent_fd, fcntl.LOCK_UN)
                os.close(parent_fd)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snap finalization lock timed out", result.stderr)

    def test_snapcraft_processes_are_bounded(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertIn("readonly SNAPCRAFT_TIMEOUT_SECONDS=3600", source)
        self.assertIn("readonly SNAP_FINALIZE_TIMEOUT_SECONDS=120", source)
        self.assertIn("readonly SNAP_FINALIZE_KILL_AFTER_SECONDS=10", source)
        self.assertIn("readonly SNAP_FINALIZE_LOCK_TIMEOUT_SECONDS=30", source)
        self.assertIn("readonly LXD_PROBE_TIMEOUT_SECONDS=30", source)
        self.assertIn("readonly SNAP_CLEANUP_TIMEOUT_SECONDS=30", source)
        self.assertIn("readonly SNAP_STARTUP_SWEEP_TIMEOUT_SECONDS=30", source)
        self.assertIn("readonly SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS=1", source)
        self.assertIn("readonly SNAP_STARTUP_SWEEP_MAX_ENTRIES=256", source)
        self.assertIn("readonly SNAP_STARTUP_SWEEP_MAX_STALE=32", source)
        self.assertIn("readonly SNAP_SCAN_MAX_ENTRIES=4096", source)
        self.assertIn("readonly SNAP_SCAN_MAX_CANDIDATES=2", source)
        self.assertIn("readonly SNAP_SCAN_MAX_CANDIDATE_PATH_BYTES=4096", source)
        self.assertIn("readonly SNAP_SCAN_MAX_CANDIDATE_TOTAL_BYTES=16384", source)
        self.assertIn(
            "for tool in python3 snapcraft timeout cp mktemp mkdir find realpath stat chmod grep basename; do",
            source,
        )
        self.assertIn(
            'timeout --signal=TERM --kill-after=30s "${SNAPCRAFT_TIMEOUT_SECONDS}s" snapcraft --version',
            source,
        )
        self.assertIn(
            'timeout --signal=TERM --kill-after=30s "${SNAPCRAFT_TIMEOUT_SECONDS}s" snapcraft pack',
            source,
        )
        self.assertIn("timeout --signal=TERM", source)
        self.assertIn(
            '--kill-after="${SNAP_FINALIZE_KILL_AFTER_SECONDS}s"',
            source,
        )
        self.assertIn(
            '"$((SNAP_FINALIZE_TIMEOUT_SECONDS - SNAP_FINALIZE_KILL_AFTER_SECONDS))s"',
            source,
        )
        self.assertIn("SNAP_FINALIZE_KILL_AFTER_SECONDS <= 0", source)
        self.assertIn(
            "finalizer_deadline = time.monotonic() + finalize_timeout_seconds",
            source,
        )
        self.assertIn("remaining = finalizer_deadline - time.monotonic()", source)
        self.assertIn(
            "finalizer_deadline - time.monotonic() <= 0",
            source,
        )
        self.assertIn(
            "snap finalization deadline exceeded after safe filesystem operation",
            source,
        )
        self.assertIn("timeout=remaining", source)
        self.assertIn("subprocess.TimeoutExpired", source)
        self.assertIn("lock_deadline = min(finalizer_deadline", source)
        finalizer = self._finalizer_program()
        self.assertIn(
            "except InterruptedError:\n"
            "            now = time.monotonic()\n"
            "            if now >= lock_deadline:",
            finalizer,
        )
        self.assertNotIn("timeout=finalize_timeout_seconds", source)
        self.assertIn("fcntl.LOCK_EX | fcntl.LOCK_NB", source)
        self.assertIn("fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", source)
        self.assertIn("parent_identity = _directory_identity(parent_stat)", source)
        self.assertIn("_revalidate_lock_parent(parent_fd, parent_identity)", source)
        self.assertIn("snap finalization lock timed out", source)
        self.assertIn("with os.scandir(directory_fd) as entries:", source)
        self.assertIn("if len(candidates) >= max_candidates:", source)
        self.assertIn("if len(encoded_path) > max_path_bytes:", source)
        self.assertIn("if total_path_bytes > max_total_bytes:", source)
        self.assertIn("tombstone_pattern", source)
        self.assertIn("follow_symlinks=False", source)
        self.assertNotIn("sort -z", source)
        self.assertNotIn("mapfile", source)
        self.assertNotIn("tmp_output", source)
        self.assertNotIn("lock_fd = os.open", source)
        self.assertNotIn("os.fdopen(lock_fd", source)
        self.assertNotIn("os.stat(lock_name", source)
        self.assertIn('"exchange",', source)
        self.assertIn('"--dst-must-not-exist",', source)
        self.assertIn("previous_claim_pattern", source)
        self.assertIn("recovery_claim_pattern", source)
        self.assertIn("legacy_backup_pattern", source)
        self.assertIn("snap previous recovery requires manual recovery", source)
        self.assertIn("snap temporary workspace scan exceeds max", source)
        self.assertIn(
            'timeout --signal=TERM --kill-after="${SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS}s"',
            source,
        )
        self.assertIn(
            "$((SNAP_STARTUP_SWEEP_TIMEOUT_SECONDS - SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS))s",
            source,
        )
        self.assertIn(
            "except InterruptedError:\n            remaining_timeout()\n            continue",
            source,
        )
        self.assertIn(r"(?:\.final-[0-9a-f]{32})+$", source)
        self.assertIn(
            'timeout --signal=TERM --kill-after=5s "${LXD_PROBE_TIMEOUT_SECONDS}s" lxc info',
            source,
        )

    def test_build_snap_does_not_delete_unrelated_repository_root_artifacts(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertNotIn("cleanup_existing_root_snaps", source)
        self.assertNotIn('find "${repo_dir}" -maxdepth 1 -name "speed-of-cinnamon_${version}_*.snap"', source)
        self.assertNotIn('cleanup_existing_dist_snaps "$(basename "${output_path}")"', source)

    def test_build_snap_cleanup_requires_expected_identity(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertIn(
            'snap_workspace_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_workspace}" --kind dir)"',
            source,
        )
        self.assertIn('--expected-identity "${snap_workspace_identity}"', source)
        self.assertIn('"--expected-identity",', source)
        self.assertIn("_verified_recovery_path", source)
        self.assertIn("identity-verified previous recovery path", source)
        self.assertIn("no identity-verified previous recovery path available", source)
        self.assertIn("snap_previous_path", source)
        self.assertIn("snap_previous_recovery_path", source)
        self.assertIn(
            'remove build-snap "${snap_workspace_dist}" --kind dir --expected-identity missing',
            source,
        )
        cleanup = self._cleanup_program()
        self.assertIn("local primary_status=$?", cleanup)
        self.assertIn("cleanup_failed=1", cleanup)
        self.assertIn("cleanup_deadline_us", cleanup)
        self.assertIn("run_cleanup_safe_fs", cleanup)
        self.assertIn("report_cleanup_path_if_identity", cleanup)
        self.assertIn("EPOCHREALTIME", cleanup)
        self.assertNotIn("/dev/null", cleanup)
        self.assertNotIn("|| true", cleanup)

    def test_build_snap_rejects_unsupported_destructive_hosts(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertIn('snapcraft_mode="${SNAPCRAFT_MODE:-auto}"', source)
        self.assertIn('snapcraft_mode="destructive"', source)
        self.assertIn('snapcraft_mode="lxd"', source)
        self.assertIn(
            'command -v -- lxc >/dev/null 2>&1 && timeout --signal=TERM --kill-after=5s "${LXD_PROBE_TIMEOUT_SECONDS}s" lxc info >/dev/null 2>&1',
            source,
        )
        self.assertIn('snapcraft_args=(--use-lxd)', source)
        self.assertIn('snapcraft_args=(--destructive-mode)', source)
        self.assertIn(
            'SNAPCRAFT_MODE=destructive is supported only on Ubuntu',
            source,
        )
        self.assertNotIn('snapcraft prime --destructive-mode', source)
        self.assertNotIn('snapcraft pack --destructive-mode prime', source)
        self.assertIn('snapcraft pack "${snapcraft_args[@]}"', source)
        self.assertNotIn('snapcraft "${snapcraft_args[@]}" pack', source)

    def test_snap_activation_requires_original_stage_and_empty_destination(self) -> None:
        program = self._finalizer_program()

        self.assertIn('"exchange",', program)
        self.assertIn('"--dst-must-not-exist",', program)
        self.assertIn('"--expected-source-identity",', program)
        self.assertIn('"--expected-target-identity",', program)
        self.assertIn('"--expected-src-identity",', program)
        self.assertNotIn("_rollback", program)

    def test_build_metadata_reads_are_bounded(self) -> None:
        source = BUILD_SNAP.read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("pyproject.toml project.version is invalid", source)
        self.assertIn("RecursionError, MemoryError", source)
        self.assertIn("MAX_SNAPCRAFT_TEMPLATE_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_SNAPCRAFT_TEMPLATE_BYTES + 1)", source)

    def _snap_source_staging_program(self) -> str:
        source = BUILD_SNAP.read_text(encoding="utf-8")
        start = source.index(
            'if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/snap"'
        )
        end = source.index('\nif ! (\n  cd "${snap_workspace}"', start)
        return source[start:end]

    def _write_snap_source_fixture(self, repo: Path) -> None:
        files = {
            "snap/snapcraft.yaml": SNAPCRAFT_YAML.read_text(encoding="utf-8"),
            "snap/local/requirements.txt": "example==1.0 --hash=sha256:abc\n",
            "snap/local/bin/speed-of-cinnamon": "#!/usr/bin/env bash\nexit 0\n",
            "src/speed_of_cinnamon/__init__.py": "VALUE = 1\n",
            "pyproject.toml": '[project]\nname = "example"\nversion = "1.2.3"\n',
            "README.md": "fixture\n",
            ".github/requirements/ci-project.txt": "example==1.0 --hash=sha256:abc\n",
            ".git/config": "secret\n",
            "errors.log": "private log\n",
            "transcript.txt": "private transcript\n",
            "recording.wav": "private recording\n",
            "sys": "foreign root artifact\n",
            "original": "foreign root artifact\n",
            "src/orphan.pyc": "bytecode\n",
            "src/speed_of_cinnamon/__pycache__/module.pyc": "bytecode\n",
        }
        for relative, payload in files.items():
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        (repo / "snap/local/bin/speed-of-cinnamon").chmod(0o755)

    def _run_snap_source_staging(
        self,
        repo: Path,
        workspace: Path,
    ) -> subprocess.CompletedProcess[str]:
        workspace.mkdir(mode=0o700)
        program = (
            "set -euo pipefail\n"
            "umask 077\n"
            "repo_dir=$1\n"
            "snap_workspace=$2\n"
            "safe_fs=$3\n"
            "safe_fs_cmd=(python3 \"${safe_fs}\")\n"
            "snapcraft_file=\"${repo_dir}/snap/snapcraft.yaml\"\n"
            "snapcraft_file_rendered=\"${snap_workspace}/snap/snapcraft.yaml\"\n"
            "snap_source=\"${snap_workspace}/.snap-source\"\n"
            "snap_source_staging=\"${snap_workspace}/.snap-source.staging\"\n"
            "snap_source_staging_identity=\"\"\n"
            "version=1.2.3\n"
            "snapcraft_base=core22\n"
            f"{self._snap_source_staging_program()}\n"
        )
        return subprocess.run(
            [
                "bash",
                "-s",
                "--",
                str(repo),
                str(workspace),
                str(REPO_ROOT / "scripts" / "safe-local-fs.py"),
            ],
            input=program,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
            timeout=15,
        )

    def test_snapcraft_uses_only_atomically_activated_allowlisted_source(self) -> None:
        manifest = SNAPCRAFT_YAML.read_text(encoding="utf-8")
        source = BUILD_SNAP.read_text(encoding="utf-8")
        population_start = source.index('snap_source="${snap_workspace}/.snap-source"')
        population_end = source.index('\nif ! (\n  cd "${snap_workspace}"', population_start)
        population = source[population_start:population_end]

        self.assertIn("    source: .snap-source\n", manifest)
        self.assertNotIn("    source: .\n", manifest)
        self.assertFalse((REPO_ROOT / ".snap-source").exists())
        self.assertIn('snap_source_staging="${snap_workspace}/.snap-source.staging"', source)
        self.assertIn(
            'install-tree build-snap "${repo_dir}/src" "${snap_source_staging}/src"',
            population,
        )
        self.assertIn(
            '"${repo_dir}/pyproject.toml" "${snap_source_staging}/pyproject.toml"',
            population,
        )
        self.assertIn(
            '"${repo_dir}/README.md" "${snap_source_staging}/README.md"',
            population,
        )
        self.assertIn(
            '"${snap_workspace}/snap" "${snap_source_staging}/snap"',
            population,
        )
        self.assertIn(
            '"${snap_source_staging}" "${snap_source}"',
            population,
        )
        self.assertIn('--dst-must-not-exist', population)
        self.assertIn('--expected-src-identity "${snap_source_staging_identity}"', population)
        self.assertNotIn('"${repo_dir}" "${snap_source_staging}"', population)
        for forbidden in ("/.git/", "errors.log", "transcript", "recording", '"${repo_dir}/sys"'):
            self.assertNotIn(forbidden, population)
        self.assertLess(
            source.index('"${snap_source_staging}" "${snap_source}"'),
            source.index('snapcraft pack "${snapcraft_args[@]}"'),
        )

    def test_snap_source_staging_excludes_foreign_files_and_rejects_symlink(self) -> None:
        self._require_safe_local_fs_root_identity()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            self._write_snap_source_fixture(repo)
            workspace = root / "workspace"

            result = self._run_snap_source_staging(repo, workspace)

            self.assertEqual(result.returncode, 0, result.stderr)
            staged = workspace / ".snap-source"
            self.assertTrue(staged.is_dir())
            current = staged.lstat()
            self.assertEqual(current.st_uid, os.geteuid())
            self.assertEqual(stat.S_IMODE(current.st_mode), 0o700)
            self.assertFalse((workspace / ".snap-source.staging").exists())
            entries = {
                path.relative_to(staged).as_posix()
                for path in staged.rglob("*")
            }
            self.assertEqual(
                entries,
                {
                    ".github",
                    ".github/requirements",
                    ".github/requirements/ci-project.txt",
                    "README.md",
                    "pyproject.toml",
                    "snap",
                    "snap/local",
                    "snap/local/bin",
                    "snap/local/bin/speed-of-cinnamon",
                    "snap/local/requirements.txt",
                    "snap/snapcraft.yaml",
                    "src",
                    "src/speed_of_cinnamon",
                    "src/speed_of_cinnamon/__init__.py",
                },
            )
            rendered_manifest = (staged / "snap/snapcraft.yaml").read_text(encoding="utf-8")
            self.assertIn('version: "1.2.3"\n', rendered_manifest)
            self.assertIn("base: core22\n", rendered_manifest)

            bad_repo = root / "bad-repo"
            self._write_snap_source_fixture(bad_repo)
            outside = root / "outside"
            outside.write_text("do not copy\n", encoding="utf-8")
            (bad_repo / "src/speed_of_cinnamon/outside-link").symlink_to(outside)
            bad_workspace = root / "bad-workspace"

            rejected = self._run_snap_source_staging(bad_repo, bad_workspace)

            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("symlink", rejected.stderr.lower())
            self.assertFalse((bad_workspace / ".snap-source").exists())


if __name__ == "__main__":
    unittest.main()
