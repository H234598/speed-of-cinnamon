from __future__ import annotations

import ctypes
import contextlib
import errno
import io
import importlib.util
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = REPO_ROOT / "scripts" / "rpm-lifecycle-supervisor.py"


class RpmLifecycleSupervisorTest(unittest.TestCase):
    def test_supervisor_binds_unreaped_root_and_direct_children(self) -> None:
        source = SUPERVISOR.read_text(encoding="utf-8")
        self.assertIn("os.WNOWAIT", source)
        self.assertIn("os.pidfd_open", source)
        self.assertIn("pidfd_send_signal", source)
        self.assertIn("validate_pidfd_send_signal", source)
        self.assertIn("pidfd_send_signal(pidfd, 0, None, 0)", source)
        self.assertIn("set_child_subreaper()", source)
        self.assertIn("def read_direct_children(", source)
        self.assertIn("iter_direct_child_batches", source)
        self.assertIn("reap_child_nonblocking", source)
        self.assertNotIn("MAX_DIRECT_CHILDREN = ", source)
        self.assertNotIn("MAX_DIRECT_CHILDREN_BYTES + 1", source)
        self.assertNotIn("direct-child list exceeds byte limit", source)
        self.assertNotIn("Popen.poll", source)

    @staticmethod
    def _load_supervisor(name: str) -> object:
        spec = importlib.util.spec_from_file_location(name, SUPERVISOR)
        if spec is None or spec.loader is None:
            raise AssertionError("could not load lifecycle supervisor")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def _run_handoff_check(
        self, handoff_fd: int, *, timeout: float = 0.1, max_bytes: int = 256
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SUPERVISOR),
                "--check-handoff-fd",
                str(handoff_fd),
                "--handoff-timeout",
                str(timeout),
                "--handoff-max-bytes",
                str(max_bytes),
            ],
            pass_fds=(handoff_fd,),
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )

    @staticmethod
    def _read_process_identity(
        process_id: int,
    ) -> tuple[int, int, int, int] | None:
        try:
            status = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            return None
        try:
            fields = status.rsplit(") ", 1)[1].split()
            return int(fields[19]), int(fields[2]), int(fields[3]), int(fields[1])
        except (IndexError, UnicodeError, ValueError):
            return None

    @staticmethod
    def _read_direct_child_ids() -> set[int]:
        path = f"/proc/self/task/{os.getpid()}/children"
        descriptor = os.open(path, os.O_RDONLY)
        payload = bytearray()
        try:
            while len(payload) <= 4096:
                chunk = os.read(descriptor, 4097 - len(payload))
                if not chunk:
                    break
                payload.extend(chunk)
                if len(payload) > 4096:
                    raise RuntimeError("test direct-child snapshot exceeds byte limit")
        finally:
            primary_error = sys.exc_info()[1]
            try:
                os.close(descriptor)
            except BaseException as close_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"test direct-child snapshot close failed: {close_error}"
                )
        try:
            return {int(token) for token in payload.split()}
        except ValueError as error:
            raise RuntimeError("test direct-child snapshot is malformed") from error

    @classmethod
    def _read_known_process_identities(
        cls,
        process_file: Path | None,
        *,
        deadline: float | None = None,
    ) -> list[tuple[int, tuple[int, int, int, int]]]:
        if process_file is None:
            return []
        while True:
            descriptor: int | None = None
            try:
                descriptor = os.open(
                    process_file,
                    os.O_RDONLY
                    | os.O_NONBLOCK
                    | os.O_NOFOLLOW
                    | os.O_CLOEXEC,
                )
                file_stat = os.fstat(descriptor)
                if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_size > 4096:
                    raise RuntimeError("test process record is not a bounded regular file")
                payload = os.read(descriptor, 4097)
                if len(payload) > 4096:
                    raise RuntimeError("test process record exceeds byte limit")
            except FileNotFoundError:
                payload = None
            finally:
                if descriptor is not None:
                    descriptor_to_close = descriptor
                    descriptor = None
                    primary_error = sys.exc_info()[1]
                    try:
                        os.close(descriptor_to_close)
                    except BaseException as close_error:
                        if primary_error is None:
                            raise
                        primary_error.add_note(
                            "test process record close failed: "
                            f"{type(close_error).__name__}: {close_error}"
                        )
            if payload is None:
                if deadline is not None and time.monotonic() < deadline:
                    time.sleep(min(0.01, deadline - time.monotonic()))
                    continue
                return []
            if not re.fullmatch(rb"[0-9]+(?: [0-9]+)*\n", payload):
                if not payload.endswith(b"\n"):
                    if deadline is not None and time.monotonic() < deadline:
                        time.sleep(min(0.01, deadline - time.monotonic()))
                        continue
                raise RuntimeError("test process record is malformed or incomplete")
            process_ids = tuple(int(token) for token in payload[:-1].split(b" "))
            if any(process_id <= 0 for process_id in process_ids):
                raise RuntimeError("test process record contains invalid PID")
            identities = []
            for process_id in dict.fromkeys(process_ids):
                identity = cls._read_process_identity(process_id)
                if identity is not None:
                    identities.append((process_id, identity))
            return identities

    @staticmethod
    def _set_test_subreaper(enabled: bool) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
        prctl.argtypes = [
            ctypes.c_int,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        prctl.restype = ctypes.c_int
        if prctl(36, int(enabled), 0, 0, 0) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number))

    @classmethod
    def _cleanup_timed_out_process_tree(
        cls,
        process: subprocess.Popen[str],
        root_identity: tuple[int, int, int, int] | None,
        process_file: Path | None,
        baseline_children: set[int],
    ) -> list[BaseException]:
        """Kill only this unreaped root group and recorded descendants."""
        errors: list[BaseException] = []
        cleanup_deadline = time.monotonic() + 2
        try:
            known = cls._read_known_process_identities(
                process_file,
                deadline=min(cleanup_deadline, time.monotonic() + 0.2),
            )
        except BaseException as error:
            known = []
            errors.append(error)
        tracked = dict(known)
        owned_pidfds: dict[int, int] = {}

        def close_owned_pidfd(process_id: int) -> None:
            pidfd = owned_pidfds.pop(process_id, None)
            if pidfd is None:
                return
            try:
                os.close(pidfd)
            except BaseException as error:
                errors.append(error)

        def bind_known_pidfd(
            process_id: int,
            expected: tuple[int, int, int, int],
        ) -> bool:
            if process_id == process.pid or process_id in owned_pidfds:
                return process_id in owned_pidfds
            if process_id in baseline_children:
                errors.append(
                    RuntimeError(
                        f"test record PID {process_id} is a baseline foreign child"
                    )
                )
                return False

            def parent_chain_is_owned(parent_pid: int) -> bool:
                seen: set[int] = set()
                current_pid = parent_pid
                for _ in range(64):
                    if current_pid == process.pid:
                        return True
                    if current_pid <= 1 or current_pid in seen:
                        return False
                    seen.add(current_pid)
                    identity = cls._read_process_identity(current_pid)
                    if identity is None:
                        return False
                    current_pid = identity[3]
                return False

            def parent_is_owned(
                current: tuple[int, int, int, int],
            ) -> bool:
                current_parent = current[3]
                expected_parent = expected[3]
                if current_parent == expected_parent:
                    return parent_chain_is_owned(current_parent)
                return (
                    current_parent == os.getpid()
                    and parent_chain_is_owned(expected_parent)
                    and process_id not in baseline_children
                )

            try:
                current = cls._read_process_identity(process_id)
            except BaseException as error:
                errors.append(error)
                return False
            if current is None or current[:3] != expected[:3] or not parent_is_owned(current):
                errors.append(RuntimeError(f"test record PID {process_id} changed identity"))
                return False
            pidfd: int | None = None
            try:
                pidfd = os.pidfd_open(process_id, 0)
                fdinfo = Path(f"/proc/self/fdinfo/{pidfd}").read_bytes()
                target_lines = re.findall(
                    rb"^Pid:\s+([0-9]+)$", fdinfo, re.MULTILINE
                )
                if len(target_lines) != 1 or int(target_lines[0]) != process_id:
                    raise RuntimeError(f"test record PIDFD target mismatch for {process_id}")
                current = cls._read_process_identity(process_id)
                if current is None or current[:3] != expected[:3] or not parent_is_owned(current):
                    raise RuntimeError(f"test record PID {process_id} changed after PIDFD open")
            except BaseException as error:
                if pidfd is not None:
                    try:
                        os.close(pidfd)
                    except BaseException as close_error:
                        error.add_note(f"test record PIDFD close failed: {close_error}")
                errors.append(error)
                return False
            owned_pidfds[process_id] = pidfd
            return True

        for process_id, expected in known:
            bind_known_pidfd(process_id, expected)

        def collect_adopted_children() -> None:
            try:
                direct_children = cls._read_direct_child_ids()
            except BaseException as error:
                errors.append(error)
                return
            for process_id in direct_children - baseline_children - {process.pid}:
                if process_id in tracked:
                    continue
                identity = cls._read_process_identity(process_id)
                if identity is not None:
                    errors.append(
                        RuntimeError(
                            f"unrecorded direct child {process_id} observed; not signaled"
                        )
                    )

        def root_is_alive() -> bool:
            try:
                waited_pid, status = os.waitpid(process.pid, os.WNOHANG)
            except InterruptedError:
                return True
            except ChildProcessError:
                return False
            except OSError as error:
                errors.append(error)
                return True
            if waited_pid == process.pid:
                process.returncode = os.waitstatus_to_exitcode(status)
                return False
            return True

        def reap_known_children() -> None:
            for process_id in tuple(tracked):
                if process_id == process.pid:
                    continue
                pidfd = owned_pidfds.get(process_id)
                if pidfd is None:
                    continue
                try:
                    result = os.waitid(
                        os.P_PIDFD,
                        pidfd,
                        os.WEXITED | os.WNOHANG,
                    )
                except ChildProcessError:
                    tracked.pop(process_id, None)
                    close_owned_pidfd(process_id)
                    continue
                except BaseException as error:
                    errors.append(error)
                    continue
                if result is not None and getattr(result, "si_pid", 0) == process_id:
                    tracked.pop(process_id, None)
                    close_owned_pidfd(process_id)

        def signal_root_group(signum: int) -> None:
            if not root_is_alive():
                return
            try:
                current = cls._read_process_identity(process.pid)
            except BaseException as error:
                current = None
                errors.append(error)
            try:
                if (
                    root_identity is not None
                    and current is not None
                    and current[:3] == root_identity[:3]
                    and current[1] == process.pid
                    and current[2] == process.pid
                ):
                    os.killpg(process.pid, signum)
                else:
                    # Popen's still-unreaped direct child owns this exact PID;
                    # use its authority when group identity is unavailable.
                    if root_is_alive():
                        process.send_signal(signum)
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)

        def signal_known(signum: int) -> None:
            collect_adopted_children()
            for process_id, expected in tuple(tracked.items()):
                if process_id == process.pid:
                    continue
                pidfd = owned_pidfds.get(process_id)
                if pidfd is None:
                    continue
                try:
                    signal.pidfd_send_signal(pidfd, signum, None, 0)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    errors.append(error)

        def known_is_alive() -> bool:
            collect_adopted_children()
            reap_known_children()
            return any(
                process_id in owned_pidfds
                for process_id in tracked
                if process_id != process.pid
            )

        signal_root_group(signal.SIGTERM)
        signal_known(signal.SIGTERM)
        term_deadline = min(cleanup_deadline, time.monotonic() + 0.2)
        while time.monotonic() < term_deadline:
            if not root_is_alive() and not known_is_alive():
                break
            time.sleep(min(0.02, max(0, term_deadline - time.monotonic())))

        signal_root_group(signal.SIGKILL)
        signal_known(signal.SIGKILL)
        while time.monotonic() < cleanup_deadline:
            if not root_is_alive() and not known_is_alive():
                break
            time.sleep(min(0.02, max(0, cleanup_deadline - time.monotonic())))
        if root_is_alive():
            errors.append(RuntimeError(f"timed-out root {process.pid} was not reaped"))
        collect_adopted_children()
        reap_known_children()
        for process_id in tuple(tracked):
            if process_id == process.pid or process_id not in owned_pidfds:
                continue
            try:
                if cls._read_process_identity(process_id) is not None:
                    errors.append(RuntimeError(f"timed-out child {process_id} remains"))
            except BaseException as error:
                errors.append(error)
        for process_id in tuple(owned_pidfds):
            close_owned_pidfd(process_id)
        return errors

    def _run_with_timeout_cleanup(
        self,
        arguments: list[str],
        *,
        process_timeout: float,
        known_process_file: Path | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        baseline_children = self._read_direct_child_ids()
        self._set_test_subreaper(True)
        try:
            process = subprocess.Popen(
                arguments,
                pass_fds=pass_fds,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
                close_fds=True,
            )
            cleanup_performed = False
            root_identity = self._read_process_identity(process.pid)
            try:
                stdout, stderr = process.communicate(timeout=process_timeout)
            except subprocess.TimeoutExpired as timeout_error:
                cleanup_performed = True
                try:
                    cleanup_errors = self._cleanup_timed_out_process_tree(
                        process,
                        root_identity,
                        known_process_file,
                        baseline_children,
                    )
                except BaseException as cleanup_error:
                    cleanup_errors = [cleanup_error]
                try:
                    process.communicate(timeout=0.5)
                except BaseException as drain_error:
                    cleanup_errors.append(drain_error)
                    for stream in (process.stdout, process.stderr):
                        if stream is not None:
                            try:
                                stream.close()
                            except OSError as close_error:
                                cleanup_errors.append(close_error)
                for cleanup_error in cleanup_errors:
                    timeout_error.add_note(f"test timeout cleanup failed: {cleanup_error}")
                raise
            except BaseException as primary_error:
                cleanup_performed = True
                try:
                    cleanup_errors = self._cleanup_timed_out_process_tree(
                        process,
                        root_identity,
                        known_process_file,
                        baseline_children,
                    )
                except BaseException as cleanup_error:
                    cleanup_errors = [cleanup_error]
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(
                        f"test communication cleanup failed: {cleanup_error}"
                    )
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except BaseException as close_error:
                            primary_error.add_note(
                                f"test communication stream cleanup failed: {close_error}"
                            )
                raise
            return subprocess.CompletedProcess(
                arguments,
                process.returncode,
                stdout,
                stderr,
            )
        except BaseException as primary_error:
            if "process" in locals() and not locals().get("cleanup_performed", False):
                try:
                    cleanup_errors = self._cleanup_timed_out_process_tree(
                        process,
                        None,
                        known_process_file,
                        baseline_children,
                    )
                except BaseException as cleanup_error:
                    cleanup_errors = [cleanup_error]
                for cleanup_error in cleanup_errors:
                    primary_error.add_note(
                        f"test launch cleanup failed: {cleanup_error}"
                    )
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except BaseException as close_error:
                            primary_error.add_note(
                                f"test launch stream cleanup failed: {close_error}"
                            )
            raise
        finally:
            primary_error = sys.exc_info()[1]
            try:
                self._set_test_subreaper(False)
            except BaseException as restore_error:
                if primary_error is None:
                    raise
                primary_error.add_note(
                    f"test subreaper restoration failed: {restore_error}"
                )

    def _run_identity(
        self,
        command: list[str],
        *,
        timeout: float = 1.0,
        max_bytes: int = 256,
        process_timeout: float = 3,
        known_process_file: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run_with_timeout_cleanup(
            [
                sys.executable,
                str(SUPERVISOR),
                "--identity",
                "--identity-timeout",
                str(timeout),
                "--identity-max-bytes",
                str(max_bytes),
                "--",
                *command,
            ],
            process_timeout=process_timeout,
            known_process_file=known_process_file,
        )

    def _run_supervisor(
        self,
        root: Path,
        *,
        timeout: float,
        kill_after: float,
        code: str,
        process_timeout: float = 5,
        known_process_file: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        os.close(handoff_write)
        try:
            return self._run_with_timeout_cleanup(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--timeout",
                    str(timeout),
                    "--kill-after",
                    str(kill_after),
                    "--handoff-fd",
                    str(handoff_read),
                    "--",
                    sys.executable,
                    "-c",
                    code,
                    str(handoff_read),
                    str(root / "processes"),
                ],
                pass_fds=(handoff_read,),
                process_timeout=process_timeout,
                known_process_file=known_process_file,
            )
        finally:
            os.close(handoff_read)

    @staticmethod
    def _process_state(process_id: int) -> str | None:
        try:
            status = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            return None
        return status.rsplit(") ", 1)[1].split(maxsplit=1)[0]

    def _assert_reaped(self, process_id: int) -> None:
        for _ in range(40):
            if self._process_state(process_id) is None:
                return
            time.sleep(0.05)
        state = self._process_state(process_id)
        if state is not None:
            self.fail(f"supervisor left process {process_id} in state {state}")

    @staticmethod
    def _read_strict_pid_record(path: Path, *, deadline: float) -> int:
        while time.monotonic() < deadline:
            try:
                payload = path.read_bytes()
            except FileNotFoundError:
                time.sleep(min(0.01, deadline - time.monotonic()))
                continue
            if re.fullmatch(rb"[0-9]+\n", payload):
                return int(payload[:-1])
            if payload.endswith(b"\n"):
                raise RuntimeError("test PID record is malformed")
            time.sleep(min(0.01, deadline - time.monotonic()))
        raise TimeoutError("test PID record did not become complete before deadline")

    @staticmethod
    def _cleanup_known_processes(process_ids: list[int]) -> list[BaseException]:
        """Reap direct children; observe foreign IDs without signalling them.

        Direct-child membership plus PIDFD ownership is required before any
        signal.  PIDFD waitid also survives a test-injected waitpid EINTR storm.
        """
        pending: set[int] = set()
        foreign: set[int] = set()
        errors: list[BaseException] = []
        deadline = time.monotonic() + 2
        owned_pidfds: dict[int, int] = {}
        eintr_counts: dict[int, int] = {}

        def bind_direct_child(process_id: int) -> bool:
            if process_id in owned_pidfds:
                return True
            try:
                direct_children = RpmLifecycleSupervisorTest._read_direct_child_ids()
            except BaseException as error:
                errors.append(error)
                return False
            if process_id not in direct_children:
                foreign.add(process_id)
                return False
            try:
                pidfd = os.pidfd_open(process_id, 0)
            except BaseException as error:
                errors.append(error)
                return False
            owned_pidfds[process_id] = pidfd
            return True

        def signal_owned(process_id: int) -> None:
            pidfd = owned_pidfds.get(process_id)
            if pidfd is None:
                return
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)

        def reap_owned(process_id: int) -> bool:
            pidfd = owned_pidfds.get(process_id)
            if pidfd is None:
                return False
            try:
                result = os.waitid(
                    os.P_PIDFD,
                    pidfd,
                    os.WEXITED | os.WNOHANG,
                )
            except ChildProcessError:
                pending.discard(process_id)
                return True
            except BaseException as error:
                errors.append(error)
                return False
            if result is not None and getattr(result, "si_pid", 0) == process_id:
                pending.discard(process_id)
                return True
            return False

        def waitpid_bounded(process_id: int) -> tuple[int, int] | None:
            while True:
                try:
                    return os.waitpid(process_id, os.WNOHANG)
                except InterruptedError as error:
                    eintr_counts[process_id] = eintr_counts.get(process_id, 0) + 1
                    if eintr_counts[process_id] >= 2:
                        if bind_direct_child(process_id):
                            return (0, 0)
                        if process_id in foreign:
                            return None
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        errors.append(error)
                        return None
                    time.sleep(min(0.05, remaining))

        for process_id in set(process_ids):
            try:
                waited = waitpid_bounded(process_id)
            except ChildProcessError:
                foreign.add(process_id)
                continue
            except ProcessLookupError:
                continue
            except OSError as error:
                errors.append(error)
                continue
            if waited is None:
                continue
            waited_pid, _status = waited
            if waited_pid == process_id:
                continue
            if bind_direct_child(process_id):
                pending.add(process_id)
                signal_owned(process_id)

        while pending and time.monotonic() < deadline:
            for process_id in list(pending):
                try:
                    waited = waitpid_bounded(process_id)
                except ChildProcessError as error:
                    pending.remove(process_id)
                    errors.append(error)
                    continue
                except ProcessLookupError:
                    pending.remove(process_id)
                    continue
                except OSError as error:
                    pending.remove(process_id)
                    errors.append(error)
                    continue
                if waited is not None:
                    waited_pid, _status = waited
                    if waited_pid == process_id:
                        pending.remove(process_id)
                        continue
                if not reap_owned(process_id):
                    signal_owned(process_id)
            if pending:
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))

        for process_id in pending:
            try:
                state = RpmLifecycleSupervisorTest._process_state(process_id)
            except Exception as error:
                errors.append(error)
                continue
            errors.append(
                RuntimeError(
                    f"owned test child {process_id} was not reaped"
                    + (f" (state {state})" if state is not None else "")
                )
            )
        for process_id in foreign:
            try:
                state = RpmLifecycleSupervisorTest._process_state(process_id)
            except Exception as error:
                errors.append(error)
                continue
            if state is not None:
                errors.append(
                    RuntimeError(
                        f"foreign test PID {process_id} remains in state {state}"
                    )
                )
        for process_id, pidfd in list(owned_pidfds.items()):
            owned_pidfds.pop(process_id, None)
            try:
                os.close(pidfd)
            except BaseException as error:
                errors.append(error)
        return errors

    def _cleanup_for_finally(self, process_ids: list[int]) -> None:
        errors = self._cleanup_known_processes(process_ids)
        if not errors:
            return
        primary = sys.exc_info()[1]
        detail = "; ".join(str(error) for error in errors[:4])
        if primary is not None:
            primary.add_note(f"test cleanup failed: {detail}")
            return
        raise AssertionError(f"test cleanup failed: {detail}") from errors[0]

    def test_test_cleanup_reaps_owned_child_and_only_observes_foreign_ids(self) -> None:
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
            os._exit(0)
        try:
            cleanup_errors = self._cleanup_known_processes([child_pid])
            self.assertEqual(cleanup_errors, [])
            self.assertIsNone(self._process_state(child_pid))

            original_kill = os.kill
            foreign_kills: list[tuple[int, int]] = []

            def reject_foreign_signal(process_id: int, signum: int) -> None:
                foreign_kills.append((process_id, signum))
                raise AssertionError("foreign PID was signalled")

            os.kill = reject_foreign_signal
            try:
                cleanup_errors = self._cleanup_known_processes([os.getpid()])
            finally:
                os.kill = original_kill
            self.assertEqual(foreign_kills, [])
            self.assertTrue(cleanup_errors)
            self.assertTrue(any("foreign test PID" in str(error) for error in cleanup_errors))
        finally:
            self._cleanup_for_finally([child_pid])

    def test_test_cleanup_retries_waitpid_eintr_before_signalling_owned_child(self) -> None:
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
            os._exit(0)
        original_waitpid = os.waitpid
        calls = [0]

        def interrupted_waitpid(process_id: int, options: int) -> tuple[int, int]:
            if process_id == child_pid and calls[0] < 2:
                calls[0] += 1
                raise InterruptedError(errno.EINTR, "injected waitpid EINTR")
            return original_waitpid(process_id, options)

        os.waitpid = interrupted_waitpid
        try:
            cleanup_errors = self._cleanup_known_processes([child_pid])
        finally:
            os.waitpid = original_waitpid
            self._cleanup_for_finally([child_pid])
        self.assertEqual(cleanup_errors, [])
        self.assertEqual(calls[0], 2)
        self.assertIsNone(self._process_state(child_pid))

    def test_test_cleanup_persistent_waitpid_eintr_reaps_via_authenticated_pidfd(self) -> None:
        child_pid = os.fork()
        if child_pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(60)
            os._exit(0)
        original_waitpid = os.waitpid
        calls = [0]

        def persistent_eintr(process_id: int, options: int) -> tuple[int, int]:
            if process_id == child_pid:
                calls[0] += 1
                raise InterruptedError(errno.EINTR, "persistent waitpid EINTR")
            return original_waitpid(process_id, options)

        os.waitpid = persistent_eintr
        try:
            cleanup_errors = self._cleanup_known_processes([child_pid])
        finally:
            os.waitpid = original_waitpid
            self._cleanup_for_finally([child_pid])
        self.assertEqual(cleanup_errors, [])
        self.assertGreaterEqual(calls[0], 2)
        self.assertIsNone(self._process_state(child_pid))

    def test_test_harness_cleans_after_identity_and_communicate_faults(self) -> None:
        original_reader = self._read_process_identity
        original_popen = subprocess.Popen
        original_communicate = subprocess.Popen.communicate
        for fault in ("identity", "communicate"):
            with self.subTest(fault=fault):
                launched: list[subprocess.Popen[str]] = []

                def tracking_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
                    process = original_popen(*args, **kwargs)
                    launched.append(process)
                    return process

                subprocess.Popen = tracking_popen
                if fault == "identity":
                    def fail_identity(_process_id: int) -> tuple[int, int, int, int]:
                        raise OSError(errno.EIO, "injected identity-read failure")

                    self._read_process_identity = fail_identity
                else:
                    def fail_communicate(
                        _process: subprocess.Popen[str], *args: object, **kwargs: object
                    ) -> tuple[str, str]:
                        raise OSError(errno.EIO, "injected communicate failure")

                    original_popen.communicate = fail_communicate  # type: ignore[method-assign]
                try:
                    with self.assertRaisesRegex(OSError, f"injected {fault}"):
                        self._run_with_timeout_cleanup(
                            [sys.executable, "-c", "import time; time.sleep(60)"],
                            process_timeout=1,
                        )
                finally:
                    self._read_process_identity = original_reader
                    original_popen.communicate = original_communicate  # type: ignore[method-assign]
                    subprocess.Popen = original_popen
                self.assertEqual(len(launched), 1)
                self.assertIsNotNone(launched[0].returncode)

    def test_timeout_cleanup_never_signals_baseline_foreign_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process_file = Path(tmp) / "processes"
            foreign = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            baseline = self._read_direct_child_ids()
            root = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            root_identity = self._read_process_identity(root.pid)
            process_file.write_text(f"{foreign.pid}\n", encoding="ascii")
            self._set_test_subreaper(True)
            try:
                errors = self._cleanup_timed_out_process_tree(
                    root,
                    root_identity,
                    process_file,
                    baseline,
                )
                self.assertIsNone(foreign.poll())
                self.assertTrue(errors)
                self.assertTrue(
                    any("baseline" in str(error) or "owned descendant" in str(error) for error in errors)
                )
                self.assertIsNotNone(root.returncode)
            finally:
                self._set_test_subreaper(False)
                if root.poll() is None:
                    root.kill()
                root.wait(timeout=2)
                if foreign.poll() is None:
                    foreign.kill()
                foreign.wait(timeout=2)

    def test_timeout_cleanup_revalidates_parent_after_pidfd_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process_file = Path(tmp) / "processes"
            baseline: set[int] = set()
            root: subprocess.Popen[str] | None = None
            child_pid: int | None = None
            original_reader = RpmLifecycleSupervisorTest._read_process_identity
            child_reads = [0]
            self._set_test_subreaper(True)
            try:
                baseline = self._read_direct_child_ids()
                root = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import os,signal,sys,time; "
                            "child=os.fork(); "
                            "os.setsid() if child == 0 else None; "
                            "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
                            "(lambda f: (f.write(str(os.getpid()) + '\\n'), f.close()))(open(sys.argv[1], 'w')) if child == 0 else None; "
                            "time.sleep(60)"
                        ),
                        str(process_file),
                    ],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                record_deadline = time.monotonic() + 1
                while time.monotonic() < record_deadline and not process_file.exists():
                    time.sleep(0.01)
                child_pid = int(process_file.read_text(encoding="ascii"))
                root_identity = self._read_process_identity(root.pid)

                def change_parent_after_pidfd(process_id: int) -> tuple[int, int, int, int] | None:
                    identity = original_reader(process_id)
                    if identity is not None and process_id == child_pid:
                        child_reads[0] += 1
                        if child_reads[0] == 3:
                            return identity[:3] + (os.getpid() + 100000,)
                    return identity

                RpmLifecycleSupervisorTest._read_process_identity = staticmethod(
                    change_parent_after_pidfd
                )
                errors = self._cleanup_timed_out_process_tree(
                    root,
                    root_identity,
                    process_file,
                    baseline,
                )
                self.assertIsNotNone(self._process_state(child_pid))
                self.assertTrue(
                    any("changed after PIDFD open" in str(error) for error in errors)
                )
            finally:
                RpmLifecycleSupervisorTest._read_process_identity = staticmethod(
                    original_reader
                )
                try:
                    if root is not None:
                        if root.poll() is None:
                            root.kill()
                        root.wait(timeout=2)
                    if child_pid is not None:
                        self._cleanup_for_finally([child_pid])
                finally:
                    self._set_test_subreaper(False)

    def test_test_cleanup_error_does_not_mask_assertion_and_is_visible_alone(self) -> None:
        original_waitpid = os.waitpid
        original_kill = os.kill

        def fail_waitpid(_process_id: int, _options: int) -> tuple[int, int]:
            raise PermissionError(errno.EACCES, "injected test cleanup permission failure")

        def fail_signal(_process_id: int, _signum: int) -> None:
            raise AssertionError("cleanup signalled after ownership failure")

        os.waitpid = fail_waitpid
        os.kill = fail_signal
        try:
            with self.assertRaisesRegex(AssertionError, "primary test assertion"):
                try:
                    self.fail("primary test assertion")
                finally:
                    cleanup_errors = self._cleanup_known_processes([os.getpid()])
            self.assertTrue(cleanup_errors)
            self.assertIsInstance(cleanup_errors[0], PermissionError)
        finally:
            os.waitpid = original_waitpid
            os.kill = original_kill

        original_state = RpmLifecycleSupervisorTest._process_state

        def fail_state(_process_id: int) -> str:
            raise PermissionError(errno.EACCES, "injected test state permission failure")

        RpmLifecycleSupervisorTest._process_state = staticmethod(fail_state)
        try:
            cleanup_errors = self._cleanup_known_processes([os.getpid()])
        finally:
            RpmLifecycleSupervisorTest._process_state = staticmethod(original_state)
        self.assertTrue(cleanup_errors)
        self.assertIsInstance(cleanup_errors[0], PermissionError)

    def test_finally_cleanup_preserves_primary_and_reports_alone(self) -> None:
        original_waitpid = os.waitpid

        def fail_waitpid(_process_id: int, _options: int) -> tuple[int, int]:
            raise PermissionError(errno.EACCES, "injected finally cleanup failure")

        os.waitpid = fail_waitpid
        try:
            with self.assertRaisesRegex(AssertionError, "primary finally assertion") as raised:
                try:
                    self.fail("primary finally assertion")
                finally:
                    self._cleanup_for_finally([os.getpid()])
            self.assertTrue(
                any("test cleanup failed" in note for note in raised.exception.__notes__)
            )
            with self.assertRaisesRegex(AssertionError, "test cleanup failed"):
                self._cleanup_for_finally([os.getpid()])
        finally:
            os.waitpid = original_waitpid

    def test_direct_children_parser_handles_chunk_boundaries_and_rejects_bad_tokens(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_children_parser")
        payload = b" " * 4093 + b"12345\t\n6  "
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, payload)
            os.close(write_fd)
            write_fd = -1
            batches = list(module.iter_direct_child_batches(read_fd))
        finally:
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)
        self.assertEqual(batches, [[12345, 6]])

        for bad_payload in (b"abc\n", b"12 12\n", b"0\n", b"-1\n"):
            with self.subTest(payload=bad_payload):
                bad_read, bad_write = os.pipe()
                try:
                    os.write(bad_write, bad_payload)
                    os.close(bad_write)
                    bad_write = -1
                    with self.assertRaisesRegex(RuntimeError, "direct-child list"):
                        list(module.iter_direct_child_batches(bad_read))
                finally:
                    os.close(bad_read)
                    if bad_write >= 0:
                        os.close(bad_write)

    def test_direct_children_read_error_drops_pending_token(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_children_read_error")
        read_fd, write_fd = os.pipe()
        payload = b"101 202 123"
        os.write(write_fd, payload)
        os.close(write_fd)
        original_read = module.os.read
        calls = [0]

        def fail_after_payload(descriptor: int, size: int) -> bytes:
            if descriptor == read_fd and calls[0] == 0:
                calls[0] += 1
                return original_read(descriptor, size)
            if descriptor == read_fd:
                raise OSError(errno.EIO, "injected children read failure")
            return original_read(descriptor, size)

        module.os.read = fail_after_payload
        try:
            with self.assertRaises(module.DirectChildrenReadError) as raised:
                list(module.iter_direct_child_batches(read_fd, deadline=time.monotonic() + 1))
        finally:
            module.os.read = original_read
            os.close(read_fd)
        self.assertEqual(raised.exception.observed, {101, 202})

    def test_snapshot_errors_never_prove_stable_root_only_children(self) -> None:
        for error_type in ("read", "parse"):
            with self.subTest(error_type=error_type):
                module = self._load_supervisor(f"rpm_snapshot_stability_{error_type}")
                root_pid = os.getpid()
                root_binding = module.ChildBinding(
                    module.ProcessIdentity(root_pid, 1, os.getppid(), root_pid, os.getsid(0), "S"),
                    -1,
                    owned_direct=True,
                )
                error_class = (
                    module.DirectChildrenReadError
                    if error_type == "read"
                    else module.DirectChildrenParseError
                )
                snapshot_error = error_class("injected ambiguous snapshot", set())
                original_snapshot = module.read_direct_children_snapshot
                original_reap_ready = module.reap_ready_children
                original_rounds = module.MAX_DRAIN_ROUNDS
                module.read_direct_children_snapshot = lambda **_kwargs: (
                    {root_pid},
                    snapshot_error,
                )
                module.reap_ready_children = lambda *args, **kwargs: set()
                module.MAX_DRAIN_ROUNDS = 3
                snapshot_errors: list[BaseException] = []
                try:
                    drained = module.drain_adopted_children(
                        root_binding,
                        {root_pid: root_binding},
                        lambda *_args: 0,
                        cleanup_deadline=time.monotonic() + 1,
                        deadline=time.monotonic() + 0.16,
                        snapshot_errors=snapshot_errors,
                    )
                finally:
                    module.read_direct_children_snapshot = original_snapshot
                    module.reap_ready_children = original_reap_ready
                    module.MAX_DRAIN_ROUNDS = original_rounds
                self.assertFalse(drained)
                self.assertGreaterEqual(len(snapshot_errors), 2)

    def test_adopted_cleanup_stops_before_linear_many_pid_budget(self) -> None:
        module = self._load_supervisor("rpm_adopted_deadline_many_pids")
        root_pid = os.getpid()
        child_pids = {root_pid + 100001 + index for index in range(301)}
        root_binding = module.ChildBinding(
            module.ProcessIdentity(root_pid, 1, os.getppid(), root_pid, os.getsid(0), "S"),
            -1,
            owned_direct=True,
        )
        bindings = {root_pid: root_binding}
        original_snapshot = module.read_direct_children_snapshot
        original_reap = module.reap_ready_children
        original_bind = module.bind_child
        original_signal = module.signal_pidfd

        def slow_bind(process_id: int, *, expected_parent_pid: int) -> object:
            time.sleep(0.005)
            return module.ChildBinding(
                module.ProcessIdentity(
                    process_id,
                    1,
                    expected_parent_pid,
                    process_id,
                    process_id,
                    "S",
                ),
                -1,
                owned_direct=True,
            )

        module.read_direct_children_snapshot = lambda **_kwargs: (
            set(child_pids) | {root_pid},
            None,
        )
        module.reap_ready_children = lambda *args, **kwargs: set()
        module.bind_child = slow_bind
        module.signal_pidfd = lambda *args, **kwargs: None
        cleanup_errors: list[BaseException] = []
        started = time.monotonic()
        try:
            drained = module.drain_adopted_children(
                root_binding,
                bindings,
                lambda *_args: 0,
                cleanup_deadline=time.monotonic() + 1,
                deadline=time.monotonic() + 0.05,
                cleanup_errors=cleanup_errors,
            )
        finally:
            module.read_direct_children_snapshot = original_snapshot
            module.reap_ready_children = original_reap
            module.bind_child = original_bind
            module.signal_pidfd = original_signal
        self.assertFalse(drained)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertIn("unresolved child count", "\n".join(map(str, cleanup_errors)))

    def test_adopted_bind_identity_errors_never_use_owned_fallback(self) -> None:
        for message in ("identity mismatch", "children read failed"):
            with self.subTest(message=message):
                module = self._load_supervisor(f"rpm_adopted_bind_error_{message[:3]}")
                root_pid = os.getpid()
                child_pid = root_pid + 100000
                root_binding = module.ChildBinding(
                    module.ProcessIdentity(root_pid, 1, os.getppid(), root_pid, os.getsid(0), "S"),
                    -1,
                    owned_direct=True,
                )
                original_snapshot = module.read_direct_children_snapshot
                original_bind = module.bind_child
                original_owned = module.make_owned_child_binding
                module.read_direct_children_snapshot = lambda **_kwargs: (
                    {root_pid, child_pid},
                    None,
                )
                module.bind_child = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError(message)
                )
                owned_calls: list[int] = []

                def unexpected_owned(process_id: int, **_kwargs: object) -> object:
                    owned_calls.append(process_id)
                    raise AssertionError("identity/read error used owned fallback")

                module.make_owned_child_binding = unexpected_owned
                try:
                    with self.assertRaisesRegex(RuntimeError, message):
                        module.drain_adopted_children(
                            root_binding,
                            {root_pid: root_binding},
                            lambda *_args: 0,
                            cleanup_deadline=time.monotonic() + 1,
                            deadline=time.monotonic() + 1,
                        )
                finally:
                    module.read_direct_children_snapshot = original_snapshot
                    module.bind_child = original_bind
                    module.make_owned_child_binding = original_owned
                self.assertEqual(owned_calls, [])

    def test_identity_group_revalidation_error_is_not_pidfd_fallback(self) -> None:
        module = self._load_supervisor("rpm_identity_group_revalidation")
        original_verify = module.verify_child
        original_kill = module.os.killpg
        binding = module.ChildBinding(
            module.ProcessIdentity(os.getpid(), 1, os.getppid(), os.getpgrp(), os.getsid(0), "S"),
            -1,
        )
        try:
            module.verify_child = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError(errno.EIO, "identity revalidation failed")
            )
            module.os.killpg = lambda *_args: (_ for _ in ()).throw(
                AssertionError("group signal should not run after revalidation failure")
            )
            with self.assertRaisesRegex(OSError, "identity revalidation failed"):
                module.signal_verified_group(
                    binding,
                    signal.SIGKILL,
                    expected_parent_pid=os.getppid(),
                )
        finally:
            module.verify_child = original_verify
            module.os.killpg = original_kill

    def test_root_group_and_pidfd_signal_failures_remain_visible(self) -> None:
        module = self._load_supervisor("rpm_root_signal_failures")
        original_group = module.signal_verified_group
        original_pidfd = module.signal_pidfd
        original_popen = module.subprocess.Popen
        launched: list[int] = []

        def fail_group(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("injected root group signal failure")

        def fail_pidfd(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected root pidfd signal failure")

        def tracking_popen(*args: object, **kwargs: object) -> object:
            process = original_popen(*args, **kwargs)
            launched.append(process.pid)
            return process

        module.signal_verified_group = fail_group
        module.signal_pidfd = fail_pidfd
        module.subprocess.Popen = tracking_popen
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        os.close(handoff_write)
        try:
            with self.assertRaisesRegex(RuntimeError, "root group signal failure") as raised:
                module.run(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=1,
                    kill_after=0.2,
                    handoff_fd=handoff_read,
                )
            notes = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn("root pidfd signal failure", notes)
        finally:
            module.signal_verified_group = original_group
            module.signal_pidfd = original_pidfd
            module.subprocess.Popen = original_popen
            try:
                os.close(handoff_read)
            except OSError:
                pass
            self._cleanup_for_finally(launched)

    def test_signal_error_notes_keep_bounded_type_and_message(self) -> None:
        module = self._load_supervisor("rpm_signal_error_note_details")
        primary = RuntimeError("primary failure")
        signal_errors = [OSError(errno.EIO, f"signal failure {index}") for index in range(10)]

        module.note_signal_errors(primary, signal_errors, prefix="injected signal")

        self.assertEqual(len(primary.__notes__), module.MAX_SIGNAL_ERROR_REPORTS)
        self.assertIn("OSError", primary.__notes__[0])
        self.assertIn("signal failure 0", primary.__notes__[0])
        self.assertNotIn("signal failure 8", "\n".join(primary.__notes__))

    def test_owned_pidfd_fallback_preserves_original_and_followup_signal_errors(self) -> None:
        module = self._load_supervisor("rpm_owned_pidfd_signal_error_chain")
        binding = module.ChildBinding(
            module.ProcessIdentity(
                os.getpid(),
                1,
                os.getpid(),
                os.getpgrp(),
                os.getsid(0),
                "R",
            ),
            123,
            owned_direct=True,
        )
        original_verify = module.verify_child

        def fail_verify(*_args: object, **_kwargs: object) -> object:
            raise OSError(errno.EACCES, "injected identity fallback failure")

        def fail_sender(*_args: object) -> object:
            raise OSError(errno.EIO, "injected pidfd signal failure")

        module.verify_child = fail_verify
        try:
            with self.assertRaisesRegex(OSError, "injected identity fallback failure") as raised:
                module.signal_pidfd(
                    fail_sender,
                    binding,
                    signal.SIGKILL,
                    expected_parent_pid=os.getpid(),
                )
        finally:
            module.verify_child = original_verify
        self.assertTrue(
            any(
                "OSError" in note and "injected pidfd signal failure" in note
                for note in raised.exception.__notes__
            )
        )

    def test_nonfinite_deadlines_reject_before_child_launch(self) -> None:
        module = self._load_supervisor("rpm_nonfinite_deadlines")
        original_popen = module.subprocess.Popen
        launches: list[object] = []

        def unexpected_launch(*_args: object, **_kwargs: object) -> object:
            launches.append(object())
            raise AssertionError("child launched with non-finite deadline")

        module.subprocess.Popen = unexpected_launch
        handoff_read, handoff_write = os.pipe()
        os.close(handoff_write)
        try:
            for value in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(value=value):
                    with self.assertRaises(ValueError):
                        module.run(
                            [sys.executable, "-c", "raise SystemExit(0)"],
                            timeout=value,
                            kill_after=0.2,
                            handoff_fd=handoff_read,
                        )
                    with self.assertRaises(ValueError):
                        module.run_bounded_identity(
                            [sys.executable, "-c", "raise SystemExit(0)"],
                            timeout=value,
                            max_bytes=256,
                        )
                    self.assertFalse(
                        module.read_handoff_token(
                            handoff_fd=handoff_read,
                            timeout=value,
                            max_bytes=256,
                        )
                    )
            self.assertEqual(launches, [])
        finally:
            module.subprocess.Popen = original_popen
            os.close(handoff_read)

        original_argv = sys.argv
        try:
            cli_cases = (
                ["supervisor", "--identity", "--identity-timeout", "nan", "--", "true"],
                ["supervisor", "--check-handoff-fd", "0", "--handoff-timeout", "inf"],
                [
                    "supervisor",
                    "--timeout",
                    "2",
                    "--kill-after",
                    "-inf",
                    "--handoff-fd",
                    "0",
                    "--",
                    "true",
                ],
                [
                    "supervisor",
                    "--identity",
                    "--identity-max-bytes",
                    str(module.MAX_IDENTITY_OUTPUT_BYTES + 1),
                    "--",
                    "true",
                ],
                [
                    "supervisor",
                    "--check-handoff-fd",
                    "0",
                    "--handoff-max-bytes",
                    str(module.MAX_HANDOFF_BYTES + 1),
                ],
            )
            for arguments in cli_cases:
                with self.subTest(arguments=arguments):
                    sys.argv = arguments
                    with self.assertRaises(SystemExit) as raised:
                        module.parse_args()
                    self.assertEqual(raised.exception.code, 2)
        finally:
            sys.argv = original_argv

    def test_direct_children_read_eintr_retries_until_deadline(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_children_eintr")
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"101 202\n")
        os.close(write_fd)
        original_read = module.os.read
        calls = [0]

        def always_interrupted(descriptor: int, size: int) -> bytes:
            if descriptor == read_fd:
                calls[0] += 1
                raise InterruptedError(errno.EINTR, "injected EINTR")
            return original_read(descriptor, size)

        module.os.read = always_interrupted
        try:
            started = time.monotonic()
            with self.assertRaises(module.DirectChildrenReadError) as raised:
                list(module.iter_direct_child_batches(read_fd, deadline=started + 0.1))
            elapsed = time.monotonic() - started
        finally:
            module.os.read = original_read
            os.close(read_fd)
        self.assertEqual(raised.exception.observed, set())
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1)
        self.assertLess(calls[0], 1000)

    def test_reap_eintr_uses_bounded_backoff(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_reap_eintr")
        original_waitid = module.os.waitid
        calls = [0]

        class WaitResult:
            si_pid = 123

        def interrupted_then_ready(*_args: object) -> WaitResult:
            calls[0] += 1
            if calls[0] < 3:
                raise InterruptedError(errno.EINTR, "injected wait EINTR")
            return WaitResult()

        module.os.waitid = interrupted_then_ready
        try:
            started = time.monotonic()
            result = module.reap_child(123, deadline=started + 1)
            elapsed = time.monotonic() - started
        finally:
            module.os.waitid = original_waitid
        self.assertIsInstance(result, WaitResult)
        self.assertEqual(calls[0], 3)
        self.assertGreaterEqual(elapsed, 0.08)

    def test_child_process_error_uses_explicit_reap_sentinel_in_both_modes(self) -> None:
        for mode in ("lifecycle", "identity"):
            with self.subTest(mode=mode):
                module = self._load_supervisor(f"rpm_lifecycle_supervisor_sentinel_{mode}")
                original_waitid = module.os.waitid

                def no_wait_status(*_args: object) -> object:
                    raise ChildProcessError()

                module.os.waitid = no_wait_status
                try:
                    result = module.reap_child_nonblocking(123)
                finally:
                    module.os.waitid = original_waitid
                self.assertIs(result, module.CHILD_ALREADY_REAPED)
                self.assertIsNot(result, False)
                with self.assertRaisesRegex(RuntimeError, "status is unavailable"):
                    module.status_from_wait_result(result)

    def test_child_already_reaped_callers_close_binding_without_fallback(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_reaped_callers")
        bindings: dict[int, object] = {}
        child_pids: list[int] = []
        release_writes: list[int] = []
        pidfds: dict[int, int] = {}
        original_reap = module.reap_child_nonblocking
        original_kill = module.os.kill
        reap_results: list[object] = []
        fallback_calls: list[tuple[int, int]] = []

        def record_reap(process_id: int) -> object:
            result = original_reap(process_id)
            reap_results.append(result)
            return result

        def reject_pid_fallback(process_id: int, signum: int) -> None:
            fallback_calls.append((process_id, signum))
            raise AssertionError("reaped child used exact-PID fallback")

        module.reap_child_nonblocking = record_reap
        module.os.kill = reject_pid_fallback
        try:
            for _ in range(2):
                release_read, release_write = os.pipe()
                child_pid = os.fork()
                if child_pid == 0:
                    os.close(release_write)
                    os.read(release_read, 1)
                    os._exit(0)
                os.close(release_read)
                release_writes.append(release_write)
                child_pids.append(child_pid)
                binding = module.bind_child_exact(child_pid)
                bindings[child_pid] = binding
                pidfds[child_pid] = binding.pidfd

            first_pid, second_pid = child_pids
            os.write(release_writes[0], b"x")
            os.close(release_writes[0])
            release_writes[0] = -1
            waited_pid, _status = os.waitpid(first_pid, 0)
            self.assertEqual(waited_pid, first_pid)

            reaped = module.reap_ready_children(bindings, root_pid=-1)
            self.assertEqual(reaped, {first_pid})
            self.assertEqual(reap_results, [module.CHILD_ALREADY_REAPED])
            with self.assertRaisesRegex(RuntimeError, "status is unavailable"):
                module.status_from_wait_result(reap_results[0])
            self.assertNotIn(first_pid, bindings)

            os.write(release_writes[1], b"x")
            os.close(release_writes[1])
            release_writes[1] = -1
            waited_pid, _status = os.waitpid(second_pid, 0)
            self.assertEqual(waited_pid, second_pid)

            sender = module.require_kernel_primitives()
            remaining, errors = module.finalize_adopted_children(
                bindings,
                sender,
                root_pid=-1,
                expected_parent_pid=os.getpid(),
                deadline=time.monotonic() + 1,
                signal_errors=[],
            )
            self.assertEqual(remaining, set())
            self.assertEqual(errors, [])
            self.assertEqual(reap_results, [module.CHILD_ALREADY_REAPED] * 2)
            with self.assertRaisesRegex(RuntimeError, "status is unavailable"):
                module.status_from_wait_result(reap_results[1])
            self.assertEqual(bindings, {})
            self.assertEqual(fallback_calls, [])
            for process_id, pidfd in pidfds.items():
                with self.subTest(process_id=process_id):
                    with self.assertRaises(OSError) as closed:
                        os.fstat(pidfd)
                    self.assertEqual(closed.exception.errno, errno.EBADF)
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(process_id, os.WNOHANG)
                    self.assertIsNone(self._process_state(process_id))
        finally:
            module.reap_child_nonblocking = original_reap
            module.os.kill = original_kill
            for descriptor in release_writes:
                if descriptor >= 0:
                    os.close(descriptor)
            self._cleanup_for_finally(child_pids)
            for binding in bindings.values():
                module.close_binding(binding)

    def test_finalize_adopted_children_reports_binding_close_failure_once(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_binding_close_failure")
        release_read, release_write = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(release_write)
            os.read(release_read, 1)
            os._exit(0)
        os.close(release_read)
        binding = module.bind_child_exact(child_pid)
        pidfd = binding.pidfd
        bindings = {child_pid: binding}
        os.write(release_write, b"x")
        os.close(release_write)
        release_write = -1
        waited_pid, _status = os.waitpid(child_pid, 0)
        self.assertEqual(waited_pid, child_pid)
        original_close = module.os.close
        close_calls: list[int] = []

        def fail_binding_close(descriptor: int) -> None:
            if descriptor == pidfd:
                close_calls.append(descriptor)
                raise OSError(errno.EIO, "injected binding close failure")
            original_close(descriptor)

        module.os.close = fail_binding_close
        try:
            remaining, errors = module.finalize_adopted_children(
                bindings,
                module.require_kernel_primitives(),
                root_pid=-1,
                expected_parent_pid=os.getpid(),
                deadline=time.monotonic() + 1,
                signal_errors=[],
            )
        finally:
            module.os.close = original_close
            if release_write >= 0:
                os.close(release_write)
            try:
                original_close(pidfd)
            except OSError:
                pass
            self._cleanup_for_finally([child_pid])

        self.assertEqual(remaining, set())
        self.assertEqual(bindings, {})
        self.assertEqual(close_calls, [pidfd])
        self.assertTrue(
            any(
                isinstance(error, OSError)
                and "injected binding close failure" in str(error)
                for error in errors
            )
        )
        self.assertIsNone(self._process_state(child_pid))

    def test_identity_fd_close_failures_preserve_primary_and_show_alone(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_fd_close_errors")
        original_pidfd_open = module.os.pidfd_open
        original_open = module.os.open
        original_read = module.os.read
        original_close = module.os.close

        def run_validation_case(*, primary: bool) -> None:
            read_fd, write_fd = os.pipe()
            os.close(write_fd)

            def fake_pidfd_open(_process_id: int, _flags: int) -> int:
                return read_fd

            def fail_close(descriptor: int) -> None:
                if descriptor == read_fd:
                    raise OSError(errno.EIO, "injected validation close failure")
                original_close(descriptor)

            module.os.pidfd_open = fake_pidfd_open
            module.os.close = fail_close
            try:
                if primary:

                    def fail_sender(*_args: object) -> None:
                        raise RuntimeError("injected validation primary failure")

                    with self.assertRaisesRegex(RuntimeError, "validation primary") as raised:
                        module.validate_pidfd_send_signal(fail_sender)
                    self.assertTrue(
                        any(
                            "injected validation close failure" in note
                            for note in raised.exception.__notes__
                        )
                    )
                else:
                    with self.assertRaisesRegex(OSError, "validation close failure"):
                        module.validate_pidfd_send_signal(lambda *_args: 0)
            finally:
                module.os.pidfd_open = original_pidfd_open
                module.os.close = original_close
                original_close(read_fd)

        run_validation_case(primary=True)
        run_validation_case(primary=False)

        def run_identity_case(*, primary: bool) -> None:
            opened: list[int] = []

            def tracking_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
                descriptor = original_open(path, flags, *args, **kwargs)
                opened.append(descriptor)
                return descriptor

            def fail_close(descriptor: int) -> None:
                if descriptor in opened:
                    raise OSError(errno.EIO, "injected identity close failure")
                original_close(descriptor)

            def fail_read(descriptor: int, size: int) -> bytes:
                if primary and descriptor in opened:
                    raise OSError(errno.EIO, "injected identity read failure")
                return original_read(descriptor, size)

            module.os.open = tracking_open
            module.os.close = fail_close
            module.os.read = fail_read
            try:
                if primary:
                    with self.assertRaisesRegex(OSError, "identity read failure") as raised:
                        module.read_proc_identity(os.getpid())
                    self.assertTrue(
                        any(
                            "injected identity close failure" in note
                            for note in raised.exception.__notes__
                        )
                    )
                else:
                    with self.assertRaisesRegex(OSError, "identity close failure"):
                        module.read_proc_identity(os.getpid())
            finally:
                module.os.open = original_open
                module.os.close = original_close
                module.os.read = original_read
                for descriptor in opened:
                    try:
                        original_close(descriptor)
                    except OSError:
                        pass

        run_identity_case(primary=True)
        run_identity_case(primary=False)

    def test_identity_stream_close_failures_are_visible_without_primary(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_stream_close_errors")
        original_popen = module.subprocess.Popen
        wrapped_streams: list[object] = []

        class CloseFailStream:
            def __init__(self, stream: object, label: str) -> None:
                self.stream = stream
                self.label = label

            def fileno(self) -> int:
                return self.stream.fileno()

            def close(self) -> None:
                raise OSError(errno.EIO, f"injected {self.label} close failure")

        def tracking_popen(*args: object, **kwargs: object) -> object:
            process = original_popen(*args, **kwargs)
            for name in ("stdout", "stderr"):
                stream = getattr(process, name)
                wrapped = CloseFailStream(stream, name)
                wrapped_streams.append(stream)
                setattr(process, name, wrapped)
            return process

        module.subprocess.Popen = tracking_popen
        try:
            with self.assertRaisesRegex(OSError, "stdout close failure") as raised:
                module.run_bounded_identity(
                    [sys.executable, "-c", "import os; os.write(1, b'1:2:3\\n')"],
                    timeout=1,
                    max_bytes=256,
                )
        finally:
            module.subprocess.Popen = original_popen
            for stream in wrapped_streams:
                stream.close()
        self.assertTrue(
            any("stderr close failure" in note for note in raised.exception.__notes__)
        )

    def test_identity_stream_read_failure_preserves_exact_oserror(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_stream_read_error")
        original_popen = module.subprocess.Popen
        original_read = module.os.read
        output_fd: list[int] = []
        launched: list[object] = []

        def tracking_popen(*args: object, **kwargs: object) -> object:
            process = original_popen(*args, **kwargs)
            launched.append(process)
            stdout = getattr(process, "stdout")
            assert stdout is not None
            output_fd.append(stdout.fileno())
            return process

        def fail_output_read(descriptor: int, size: int) -> bytes:
            if output_fd and descriptor == output_fd[0]:
                raise OSError(errno.EIO, "injected identity stream read failure")
            return original_read(descriptor, size)

        module.subprocess.Popen = tracking_popen
        module.os.read = fail_output_read
        try:
            with self.assertRaisesRegex(RuntimeError, "identity helper pipe failed") as raised:
                module.run_bounded_identity(
                    [
                        sys.executable,
                        "-c",
                        "import os,time; os.write(1,b'1:2:3\\n'); time.sleep(60)",
                    ],
                    timeout=1,
                    max_bytes=256,
                )
        finally:
            module.subprocess.Popen = original_popen
            module.os.read = original_read
            for process in launched:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertIn("injected identity stream read failure", str(raised.exception.__cause__))
        self.assertTrue(
            any(
                "OSError" in note and "injected identity stream read failure" in note
                for note in raised.exception.__notes__
            )
        )

    def test_identity_finalizer_peek_failure_is_cleanup_diagnostic(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_finalizer_peek_error")
        original_peek = module.peek_child
        original_popen = module.subprocess.Popen
        launched: list[object] = []
        calls = [0]

        def tracking_popen(*args: object, **kwargs: object) -> object:
            process = original_popen(*args, **kwargs)
            launched.append(process)
            return process

        def injected_peek(process_id: int) -> object:
            calls[0] += 1
            if calls[0] == 1:
                return original_peek(process_id)
            if calls[0] == 2:
                raise OSError(errno.EIO, "injected main peek failure")
            raise OSError(errno.EIO, "injected finalizer peek failure")

        module.subprocess.Popen = tracking_popen
        module.peek_child = injected_peek
        try:
            with self.assertRaisesRegex(OSError, "injected main peek failure") as raised:
                module.run_bounded_identity(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    timeout=1,
                    max_bytes=256,
                )
        finally:
            module.subprocess.Popen = original_popen
            module.peek_child = original_peek
            for process in launched:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)
        self.assertTrue(
            any("injected finalizer peek failure" in note for note in raised.exception.__notes__)
        )

    def test_lifecycle_handoff_close_failure_is_visible_without_primary(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_handoff_close_error")
        original_close = module.os.close
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        original_close(handoff_write)

        def fail_handoff_close(descriptor: int) -> None:
            if descriptor == handoff_read:
                raise OSError(errno.EIO, "injected handoff close failure")
            original_close(descriptor)

        module.os.close = fail_handoff_close
        try:
            with self.assertRaisesRegex(OSError, "handoff close failure"):
                module.run(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=1,
                    kill_after=0.2,
                    handoff_fd=handoff_read,
                )
        finally:
            module.os.close = original_close
            try:
                original_close(handoff_read)
            except OSError:
                pass

    def test_direct_children_snapshot_close_failure_is_visible(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_children_close_error")
        original_open = module.os.open
        original_close = module.os.close
        children_path = f"/proc/self/task/{os.getpid()}/children"

        for primary in (False, True):
            with self.subTest(primary=primary):
                read_fd, write_fd = os.pipe()
                os.write(write_fd, b"123\n")
                os.close(write_fd)

                def injected_open(path: str, flags: int, *args: object, **kwargs: object) -> int:
                    if os.fspath(path) == children_path:
                        return read_fd
                    return original_open(path, flags, *args, **kwargs)

                def fail_close(descriptor: int) -> None:
                    if descriptor == read_fd:
                        raise OSError(errno.EIO, "injected children close failure")
                    original_close(descriptor)

                def injected_read(descriptor: int, size: int) -> bytes:
                    if primary and descriptor == read_fd:
                        raise OSError(errno.EIO, "injected children read failure")
                    return original_read(descriptor, size)

                original_read = module.os.read
                module.os.open = injected_open
                module.os.close = fail_close
                if primary:
                    module.os.read = injected_read
                try:
                    children, error = module.read_direct_children_snapshot(
                        deadline=time.monotonic() + 1
                    )
                finally:
                    module.os.open = original_open
                    module.os.close = original_close
                    module.os.read = original_read
                    original_close(read_fd)
                self.assertEqual(children, set() if primary else {123})
                if primary:
                    self.assertIsInstance(error, module.DirectChildrenReadError)
                else:
                    self.assertIsInstance(error, OSError)
                expected_text = "injected children " + (
                    "read failure" if primary else "close failure"
                )
                self.assertIn(
                    expected_text,
                    str(error.__cause__) if primary else str(error),
                )
                if primary:
                    self.assertTrue(
                        any(
                            "injected children close failure" in note
                            for note in error.__notes__
                        )
                    )

    def test_direct_children_eintr_deadline_drops_pending_token(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_children_eintr_token")
        read_fd, write_fd = os.pipe()
        os.write(write_fd, b"101 202 123")
        os.close(write_fd)
        original_read = module.os.read
        calls = [0]

        def payload_then_interrupted(descriptor: int, size: int) -> bytes:
            if descriptor == read_fd and calls[0] == 0:
                calls[0] += 1
                return original_read(descriptor, size)
            if descriptor == read_fd:
                raise InterruptedError(errno.EINTR, "injected EINTR")
            return original_read(descriptor, size)

        module.os.read = payload_then_interrupted
        try:
            with self.assertRaises(module.DirectChildrenReadError) as raised:
                list(module.iter_direct_child_batches(read_fd, deadline=time.monotonic() + 0.1))
        finally:
            module.os.read = original_read
            os.close(read_fd)
        self.assertEqual(raised.exception.observed, {101, 202})

    def test_known_process_record_read_error_preserves_close_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            process_file = Path(tmp) / "processes"
            process_file.write_text(f"{os.getpid()}\n", encoding="ascii")
            original_open = os.open
            original_read = os.read
            original_close = os.close
            record_fd: list[int] = []

            def tracking_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                descriptor = original_open(path, flags, *args, **kwargs)
                if Path(path) == process_file:
                    record_fd.append(descriptor)
                return descriptor

            def fail_read(descriptor: int, size: int) -> bytes:
                if record_fd and descriptor == record_fd[0]:
                    raise OSError(errno.EIO, "injected record read failure")
                return original_read(descriptor, size)

            def close_then_fail(descriptor: int) -> None:
                if record_fd and descriptor == record_fd[0]:
                    original_close(descriptor)
                    raise OSError(errno.EIO, "injected record close failure")
                original_close(descriptor)

            os.open = tracking_open
            os.read = fail_read
            os.close = close_then_fail
            try:
                with self.assertRaisesRegex(OSError, "injected record read failure") as raised:
                    self._read_known_process_identities(process_file)
            finally:
                os.open = original_open
                os.read = original_read
                os.close = original_close
            self.assertTrue(
                any("injected record close failure" in str(note) for note in raised.exception.__notes__)
            )

    def test_early_root_exit_kills_and_reaps_process_group(self) -> None:
        code = """
import os
import signal
import subprocess
import sys
from pathlib import Path

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
child = subprocess.Popen(
    [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(sys.argv[2]).write_text(f"{os.getpid()} {os.getpgrp()} {child.pid}\\n", encoding="ascii")
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = root / "processes"
            process_ids: list[int] = []
            try:
                started = time.monotonic()
                result = self._run_supervisor(root, timeout=2, kill_after=0.2, code=code)
                elapsed = time.monotonic() - started
                process_ids = list(map(int, processes.read_text(encoding="ascii").split()))

                root_pid, process_group_id, child_pid = process_ids
                self.assertEqual(root_pid, process_group_id)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertLess(elapsed, 2)
                self._assert_reaped(root_pid)
                self._assert_reaped(child_pid)
            finally:
                if not process_ids:
                    try:
                        process_ids = list(map(int, processes.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        process_ids = []
                self._cleanup_for_finally(process_ids)

    def test_timeout_kills_term_resistant_process_group(self) -> None:
        code = """
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
child = subprocess.Popen(
    [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
Path(sys.argv[2]).write_text(f"{os.getpid()} {os.getpgrp()} {child.pid}\\n", encoding="ascii")
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = root / "processes"
            process_ids: list[int] = []
            try:
                started = time.monotonic()
                result = self._run_supervisor(root, timeout=1, kill_after=0.2, code=code)
                elapsed = time.monotonic() - started
                process_ids = list(map(int, processes.read_text(encoding="ascii").split()))

                root_pid, process_group_id, child_pid = process_ids
                self.assertEqual(root_pid, process_group_id)
                self.assertEqual(result.returncode, 124, result.stderr)
                self.assertLess(elapsed, 2)
                self._assert_reaped(root_pid)
                self._assert_reaped(child_pid)
            finally:
                if not process_ids:
                    try:
                        process_ids = list(map(int, processes.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        process_ids = []
                self._cleanup_for_finally(process_ids)

    def test_direct_child_batches_reap_65_children_without_touching_unrelated_group(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            for separate_group in (False, True):
                with self.subTest(separate_group=separate_group), tempfile.TemporaryDirectory() as tmp:
                    process_ids: list[int] = []
                    mode = "True" if separate_group else "False"
                    code = f"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
children = []
for _ in range(65):
    child = subprocess.Popen(
        [sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session={mode},
    )
    children.append(child.pid)
Path(sys.argv[2]).write_text(
    f"{{os.getpid()}} {{os.getpgrp()}} " + " ".join(map(str, children)),
    encoding="ascii",
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
                    try:
                        result = self._run_supervisor(
                            Path(tmp), timeout=3, kill_after=0.4, code=code
                        )
                        process_ids = list(
                            map(int, (Path(tmp) / "processes").read_text().split())
                        )
                        self.assertEqual(len(process_ids), 67)
                        self.assertEqual(result.returncode, 124, result.stderr)
                        for process_id in set(process_ids):
                            self._assert_reaped(process_id)
                        self.assertIsNone(unrelated.poll())
                    finally:
                        if not process_ids:
                            try:
                                process_ids = list(
                                    map(int, (Path(tmp) / "processes").read_text().split())
                                )
                            except (FileNotFoundError, ValueError):
                                process_ids = []
                        self._cleanup_for_finally(process_ids)
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_direct_child_batches_reap_300_separate_pgid_children(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        process_ids: list[int] = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                processes = root / "processes"
                code = """
import os
import signal
import sys
import time
from pathlib import Path

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
children = []
for _ in range(300):
    child_pid = os.fork()
    if child_pid == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
        os._exit(0)
    children.append(child_pid)
Path(sys.argv[2]).write_text(
    f"{os.getpid()} {os.getpgrp()} " + " ".join(map(str, children)),
    encoding="ascii",
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
                try:
                    result = self._run_supervisor(
                        root,
                        timeout=10,
                        kill_after=0.4,
                        code=code,
                        process_timeout=15,
                    )
                    process_ids = list(map(int, processes.read_text().split()))
                    self.assertEqual(len(process_ids), 302)
                    self.assertEqual(result.returncode, 124, result.stderr)
                    self.assertIsNone(unrelated.poll())
                    for process_id in process_ids:
                        self._assert_reaped(process_id)
                finally:
                    if not process_ids:
                        try:
                            process_ids = list(map(int, processes.read_text().split()))
                        except (FileNotFoundError, ValueError):
                            process_ids = []
                    self._cleanup_for_finally(process_ids)
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_identity_helper_reaps_300_separate_pgid_children(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        code = """
import os
import signal
import sys
import time

record = sys.argv[1]
children = []
for _ in range(300):
    child_pid = os.fork()
    if child_pid == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
        os._exit(0)
    children.append(child_pid)
payload = (f"{os.getpid()} " + " ".join(map(str, children))).encode("ascii")
fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
try:
    offset = 0
    while offset < len(payload):
        offset += os.write(fd, payload[offset:])
finally:
    os.close(fd)
os.write(1, b"1:2:3\\n")
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
        process_ids: list[int] = []
        try:
            with tempfile.TemporaryDirectory() as tmp:
                record = Path(tmp) / "processes"
                try:
                    result = self._run_identity(
                        [sys.executable, "-c", code, str(record)],
                        timeout=10,
                        max_bytes=256,
                        process_timeout=15,
                    )
                    process_ids = list(map(int, record.read_text().split()))
                    self.assertEqual(len(process_ids), 301)
                    self.assertNotEqual(result.returncode, 0, result.stderr)
                    self.assertIn("deadline", result.stderr)
                    self.assertIsNone(unrelated.poll())
                    for process_id in process_ids:
                        self._assert_reaped(process_id)
                finally:
                    if not process_ids:
                        try:
                            process_ids = list(map(int, record.read_text().split()))
                        except (FileNotFoundError, ValueError):
                            process_ids = []
                    self._cleanup_for_finally(process_ids)
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_direct_children_over_4096_bytes_are_drained_in_both_modes(self) -> None:
        child_code = """
import os
import signal
import sys
import time

record = sys.argv[-1]
if len(sys.argv) > 2:
    if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
        raise SystemExit("handoff was not inherited")
children = []
for _ in range(20):
    child_pid = os.fork()
    if child_pid == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
        os._exit(0)
    children.append(child_pid)
fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, (f"{os.getpid()} " + " ".join(map(str, children))).encode("ascii"))
os.close(fd)
if len(sys.argv) == 2:
    os.write(1, b"1:2:3\\n")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
os._exit(0)
"""
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            for mode in ("lifecycle", "identity"):
                with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    child_record = root / "processes"
                    launched_record = root / "launched"
                    outcome_record = root / "outcome"
                    fake_children = root / "children-overflow"
                    handoff_read: int | None = None
                    process_ids: list[int] = []
                    wrapper = f"""
import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("rpm_overflow", {str(SUPERVISOR)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

real_open = module.os.open
real_read = module.os.read
real_close = module.os.close
children_path = f"/proc/self/task/{{os.getpid()}}/children"
injected_bytes = [0]

def write_fake_children(payload):
    descriptor = real_open(
        {str(fake_children)!r},
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
    finally:
        real_close(descriptor)

def injected_open(path, flags, *args, **kwargs):
    if os.fspath(path) == children_path:
        source = real_open(path, flags, *args, **kwargs)
        try:
            actual = real_read(source, 4096)
        finally:
            real_close(source)
        try:
            root_pid = int(Path({str(launched_record)!r}).read_text(encoding="ascii"))
        except (FileNotFoundError, ValueError):
            root_pid = -1
        if root_pid > 0 and str(root_pid).encode("ascii") in actual.split():
            payload = b" " * 4097 + actual
            write_fake_children(payload)
            injected_bytes[0] = len(payload)
            return real_open(
                {str(fake_children)!r},
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
            )
    return real_open(path, flags, *args, **kwargs)

original_popen = module.subprocess.Popen
def tracking_popen(*args, **kwargs):
    process = original_popen(*args, **kwargs)
    Path({str(launched_record)!r}).write_text(str(process.pid), encoding="ascii")
    return process

module.os.open = injected_open
module.subprocess.Popen = tracking_popen
outcome = "error:unhandled"
try:
    if {mode!r} == "lifecycle":
        root_command = [
            sys.executable,
            "-c",
            {child_code!r},
            str(sys.argv[1]),
            {str(child_record)!r},
        ]
        value = module.run(
            root_command,
            timeout=3,
            kill_after=0.3,
            handoff_fd=int(sys.argv[1]),
        )
        outcome = f"ok:{{value}}"
    else:
        try:
            value = module.run_bounded_identity(
                [sys.executable, "-c", {child_code!r}, {str(child_record)!r}],
                timeout=1,
                max_bytes=256,
            )
            outcome = f"ok:{{value}}"
        except BaseException as exc:
            outcome = f"error:{{type(exc).__name__}}:{{exc}}"
finally:
    module.subprocess.Popen = original_popen
    module.os.open = real_open
    Path({str(outcome_record)!r}).write_text(
        outcome + f"|overflow-bytes:{{injected_bytes[0]}}",
        encoding="utf-8",
    )
"""
                    try:
                        if mode == "lifecycle":
                            handoff_read, handoff_write = os.pipe()
                            os.write(handoff_write, b"rpm-lifecycle-test\n")
                            os.close(handoff_write)
                            wrapper_args = [
                                sys.executable,
                                "-c",
                                wrapper,
                                str(handoff_read),
                            ]
                            pass_fds = (handoff_read,)
                        else:
                            wrapper_args = [sys.executable, "-c", wrapper]
                            pass_fds = ()
                        result = subprocess.run(
                            wrapper_args,
                            pass_fds=pass_fds,
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=8,
                        )
                        outcome = outcome_record.read_text(encoding="utf-8")
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("overflow-bytes:", outcome)
                        self.assertGreater(
                            int(outcome.rsplit("overflow-bytes:", 1)[1]),
                            4096,
                        )
                        if mode == "lifecycle":
                            self.assertEqual(outcome.split("|", 1)[0], "ok:0")
                        else:
                            self.assertTrue(
                                outcome.split("|", 1)[0].startswith("error:TimeoutError:"),
                                outcome,
                            )
                        process_ids = list(map(int, child_record.read_text().split()))
                        self.assertEqual(len(process_ids), 21)
                        self.assertIsNone(unrelated.poll())
                        for process_id in process_ids:
                            self._assert_reaped(process_id)
                    finally:
                        if not process_ids:
                            try:
                                process_ids = list(map(int, child_record.read_text().split()))
                            except (FileNotFoundError, ValueError):
                                process_ids = []
                        self._cleanup_for_finally(process_ids)
                        if handoff_read is not None:
                            try:
                                os.close(handoff_read)
                            except OSError:
                                pass
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def _assert_children_read_fault_case(
        self,
        *,
        mode: str,
        child_count: int,
        fault: str,
        shape: str,
        unrelated: subprocess.Popen[bytes],
    ) -> None:
        child_code = """
import os
import signal
import sys
import time

record = sys.argv[-1]
if len(sys.argv) > 2 and os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
children = []
for _ in range(CHILD_COUNT):
    child_pid = os.fork()
    if child_pid == 0:
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        time.sleep(60)
        os._exit(0)
    children.append(child_pid)
fd = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
payload = (f"{os.getpid()} " + " ".join(map(str, children))).encode("ascii")
offset = 0
while offset < len(payload):
    offset += os.write(fd, payload[offset:])
os.close(fd)
if len(sys.argv) == 2:
    os.write(1, b"1:2:3\\n")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
os._exit(0)
""".replace("CHILD_COUNT", str(child_count))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_record = root / "processes"
            launched_record = root / "launched"
            outcome_record = root / "outcome"
            actions_record = root / "actions"
            fake_children = root / "children-fault"
            wrapper = f"""
import importlib.util
import os
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("rpm_children_read_fault", {str(SUPERVISOR)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

real_open = module.os.open
real_read = module.os.read
real_close = module.os.close
children_path = f"/proc/self/task/{{os.getpid()}}/children"
fake_fds = set()
fake_reads = {{}}
fault_armed = False
actions = []

def write_fake_children(payload):
    descriptor = real_open(
        {str(fake_children)!r},
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
    finally:
        real_close(descriptor)

def injected_open(path, flags, *args, **kwargs):
    global fault_armed
    if os.fspath(path) == children_path:
        if not fault_armed:
            fault_armed = True
            return real_open(path, flags, *args, **kwargs)
        source = real_open(path, flags, *args, **kwargs)
        try:
            actual = real_read(source, 4096)
        finally:
            real_close(source)
        actual = actual.rstrip(b" \\t\\r\\n")
        if {shape!r} == "unterminated":
            actual += b" 12345"
        else:
            actual += b" "
        write_fake_children(actual)
        descriptor = real_open(
            {str(fake_children)!r},
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        fake_fds.add(descriptor)
        fake_reads[descriptor] = 0
        return descriptor
    return real_open(path, flags, *args, **kwargs)

original_bind = module.bind_child
original_bind_exact = module.bind_child_exact
original_make_owned = module.make_owned_child_binding
original_snapshot = module.read_direct_children_snapshot
original_signal = module.signal_pidfd

def tracking_bind(process_id, *args, **kwargs):
    actions.append(f"bind:{{process_id}}")
    return original_bind(process_id, *args, **kwargs)

def tracking_bind_exact(process_id, *args, **kwargs):
    actions.append(f"exact:{{process_id}}")
    return original_bind_exact(process_id, *args, **kwargs)

def tracking_make_owned(process_id, *args, **kwargs):
    actions.append(f"owned:{{process_id}}")
    return original_make_owned(process_id, *args, **kwargs)

def tracking_snapshot(*args, **kwargs):
    children, error = original_snapshot(*args, **kwargs)
    actions.extend(f"observed:{{process_id}}" for process_id in children)
    return children, error

def tracking_signal(sender, binding, *args, **kwargs):
    actions.append(f"signal:{{binding.identity.pid}}")
    return original_signal(sender, binding, *args, **kwargs)

def injected_read(descriptor, size):
    if descriptor not in fake_fds:
        return real_read(descriptor, size)
    if fake_reads[descriptor] == 0:
        fake_reads[descriptor] = 1
        return real_read(descriptor, size)
    if {fault!r} == "oserror":
        raise OSError(5, "injected children read failure")
    raise InterruptedError(4, "injected children EINTR")

original_popen = module.subprocess.Popen
def tracking_popen(*args, **kwargs):
    process = original_popen(*args, **kwargs)
    Path({str(launched_record)!r}).write_text(str(process.pid), encoding="ascii")
    return process

module.os.open = injected_open
module.os.read = injected_read
module.subprocess.Popen = tracking_popen
module.bind_child = tracking_bind
module.bind_child_exact = tracking_bind_exact
module.make_owned_child_binding = tracking_make_owned
module.read_direct_children_snapshot = tracking_snapshot
module.signal_pidfd = tracking_signal
outcome = "error:unhandled"
try:
    if {mode!r} == "lifecycle":
        value = module.run(
            [sys.executable, "-c", {child_code!r}, sys.argv[1], {str(child_record)!r}],
            timeout=2,
            kill_after=0.2,
            handoff_fd=int(sys.argv[1]),
        )
        outcome = f"ok:{{value}}"
    else:
        value = module.run_bounded_identity(
            [sys.executable, "-c", {child_code!r}, {str(child_record)!r}],
            timeout=2,
            max_bytes=256,
        )
        outcome = f"ok:{{value}}"
except BaseException as exc:
    notes = ";".join(str(note) for note in getattr(exc, "__notes__", ()))
    outcome = f"error:{{type(exc).__name__}}:{{exc}}|notes:{{notes}}"
finally:
    module.subprocess.Popen = original_popen
    module.os.open = real_open
    module.os.read = real_read
    module.bind_child = original_bind
    module.bind_child_exact = original_bind_exact
    module.make_owned_child_binding = original_make_owned
    module.read_direct_children_snapshot = original_snapshot
    module.signal_pidfd = original_signal
    for descriptor in list(fake_fds):
        try:
            real_close(descriptor)
        except OSError:
            pass
    Path({str(outcome_record)!r}).write_text(
        outcome,
        encoding="utf-8",
    )
    Path({str(actions_record)!r}).write_text(
        "\\n".join(actions),
        encoding="utf-8",
    )
"""
            handoff_read: int | None = None
            process_ids: list[int] = []
            try:
                if mode == "lifecycle":
                    handoff_read, handoff_write = os.pipe()
                    os.write(handoff_write, b"rpm-lifecycle-test\n")
                    os.close(handoff_write)
                    wrapper_args = [sys.executable, "-c", wrapper, str(handoff_read)]
                    pass_fds = (handoff_read,)
                else:
                    wrapper_args = [sys.executable, "-c", wrapper]
                    pass_fds = ()
                result = subprocess.run(
                    wrapper_args,
                    pass_fds=pass_fds,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=8,
                )
                outcome = outcome_record.read_text(encoding="utf-8")
                actions = actions_record.read_text(encoding="utf-8").splitlines()
                self.assertEqual(result.returncode, 0, result.stderr)
                expected_message = (
                    "RPM lifecycle direct-child list read failed"
                    if fault == "oserror"
                    else "RPM lifecycle direct-child list read deadline exceeded"
                )
                self.assertEqual(
                    outcome.partition("|")[0],
                    f"error:DirectChildrenReadError:{expected_message}",
                )
                if shape == "unterminated":
                    self.assertNotIn("observed:12345", actions)
                    self.assertNotIn("bind:12345", actions)
                    self.assertNotIn("exact:12345", actions)
                    self.assertNotIn("owned:12345", actions)
                    self.assertNotIn("signal:12345", actions)
                process_ids = list(map(int, child_record.read_text().split()))
                self.assertEqual(len(process_ids), child_count + 1)
                self.assertIsNone(unrelated.poll())
                for process_id in process_ids:
                    self._assert_reaped(process_id)
            finally:
                if not process_ids:
                    try:
                        process_ids = list(map(int, child_record.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        process_ids = []
                self._cleanup_for_finally(process_ids)
                if handoff_read is not None:
                    try:
                        os.close(handoff_read)
                    except OSError:
                        pass

    def test_hidden_setsid_child_never_proves_root_drain_in_both_modes(self) -> None:
        child_code = """
import os
import signal
import sys
import time
from pathlib import Path

arguments = sys.argv[1:]
if len(arguments) == 3:
    handoff_fd = int(arguments[0])
    if os.read(handoff_fd, 128) != b"rpm-lifecycle-test\\n":
        raise SystemExit("handoff was not inherited")
    record_path, hidden_path = map(Path, arguments[1:])
else:
    record_path, hidden_path = map(Path, arguments)
hidden_pid = os.fork()
if hidden_pid == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    hidden_path.write_text(str(os.getpid()), encoding="ascii")
    time.sleep(60)
    os._exit(0)
for _ in range(100):
    if hidden_path.exists():
        break
    time.sleep(0.01)
record_path.write_text(f"{os.getpid()} {hidden_pid}", encoding="ascii")
signal.signal(signal.SIGTERM, lambda *_args: os._exit(0))
time.sleep(60)
"""
        for mode in ("lifecycle", "identity"):
            for fault in ("read", "parse"):
                with self.subTest(mode=mode, fault=fault), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    child_record = root / "processes"
                    hidden_record = root / "hidden"
                    launched_record = root / "launched"
                    outcome_record = root / "outcome"
                    wrapper = f"""
import importlib.util
import os
import signal
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("rpm_hidden_snapshot", {str(SUPERVISOR)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
original_popen = module.subprocess.Popen
original_snapshot = module.read_direct_children_snapshot

def tracking_popen(*args, **kwargs):
    process = original_popen(*args, **kwargs)
    Path({str(launched_record)!r}).write_text(str(process.pid), encoding="ascii")
    return process

def persistent_fault(*args, **kwargs):
    root_pid = int(Path({str(launched_record)!r}).read_text(encoding="ascii"))
    error_class = (
        module.DirectChildrenReadError
        if {fault!r} == "read"
        else module.DirectChildrenParseError
    )
    return {{root_pid}}, error_class("injected persistent snapshot fault", {{root_pid}})

module.subprocess.Popen = tracking_popen
module.read_direct_children_snapshot = persistent_fault
outcome = "error:unhandled"
try:
    if {mode!r} == "lifecycle":
        handoff_fd = int(sys.argv[1])
        value = module.run(
            [sys.executable, "-c", {child_code!r}, str(handoff_fd), {str(child_record)!r}, {str(hidden_record)!r}],
            timeout=1.2,
            kill_after=0.15,
            handoff_fd=handoff_fd,
        )
    else:
        value = module.run_bounded_identity(
            [sys.executable, "-c", {child_code!r}, {str(child_record)!r}, {str(hidden_record)!r}],
            timeout=1.2,
            max_bytes=256,
        )
    outcome = f"ok:{{value}}"
except BaseException as exc:
    notes = ";".join(str(note) for note in getattr(exc, "__notes__", ()))
    outcome = f"error:{{type(exc).__name__}}:{{exc}}|notes:{{notes}}"
finally:
    module.subprocess.Popen = original_popen
    module.read_direct_children_snapshot = original_snapshot
    owned = {{}}
    remaining = set()
    try:
        remaining = set(map(int, Path({str(child_record)!r}).read_text().split()))
    except (FileNotFoundError, ValueError):
        remaining = set()
    deadline = time.monotonic() + 2
    while remaining and time.monotonic() < deadline:
        try:
            direct = set(map(int, Path(f"/proc/self/task/{{os.getpid()}}/children").read_text().split()))
        except (FileNotFoundError, ValueError):
            direct = set()
        for process_id in tuple(remaining):
            if process_id not in owned and process_id in direct:
                try:
                    owned[process_id] = os.pidfd_open(process_id, 0)
                except OSError:
                    continue
            pidfd = owned.get(process_id)
            if pidfd is None:
                continue
            try:
                signal.pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
            except ProcessLookupError:
                pass
            try:
                result = os.waitid(os.P_PIDFD, pidfd, os.WEXITED | os.WNOHANG)
            except ChildProcessError:
                remaining.discard(process_id)
            else:
                if result is not None and getattr(result, "si_pid", 0) == process_id:
                    remaining.discard(process_id)
        time.sleep(0.01)
    for process_id, pidfd in owned.items():
        try:
            os.close(pidfd)
        except OSError:
            pass
    Path({str(outcome_record)!r}).write_text(outcome, encoding="utf-8")
"""
                    handoff_read: int | None = None
                    process_ids: list[int] = []
                    try:
                        if mode == "lifecycle":
                            handoff_read, handoff_write = os.pipe()
                            os.write(handoff_write, b"rpm-lifecycle-test\n")
                            os.close(handoff_write)
                            arguments = [sys.executable, "-c", wrapper, str(handoff_read)]
                            pass_fds = (handoff_read,)
                        else:
                            arguments = [sys.executable, "-c", wrapper]
                            pass_fds = ()
                        result = subprocess.run(
                            arguments,
                            pass_fds=pass_fds,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            timeout=6,
                        )
                        outcome = outcome_record.read_text(encoding="utf-8")
                        self.assertEqual(result.returncode, 0)
                        expected_error = (
                            "DirectChildrenReadError"
                            if fault == "read"
                            else "DirectChildrenParseError"
                        )
                        self.assertTrue(outcome.startswith(f"error:{expected_error}"))
                        self.assertNotIn("ok:", outcome)
                        self.assertIn("unconfirmed", outcome)
                        process_ids = list(map(int, child_record.read_text().split()))
                        self.assertEqual(len(process_ids), 2)
                        for process_id in process_ids:
                            self._assert_reaped(process_id)
                    finally:
                        if not process_ids:
                            try:
                                process_ids = list(map(int, child_record.read_text().split()))
                            except (FileNotFoundError, ValueError):
                                process_ids = []
                        self._cleanup_for_finally(process_ids)
                        if handoff_read is not None:
                            try:
                                os.close(handoff_read)
                            except OSError:
                                pass

    def test_reap_and_emergency_faults_do_not_abort_finalizer_in_both_modes(self) -> None:
        child_code = """
import os
import signal
import sys
import time
from pathlib import Path

arguments = sys.argv[1:]
if len(arguments) == 3:
    handoff_fd = int(arguments[0])
    if os.read(handoff_fd, 128) != b"rpm-lifecycle-test\\n":
        raise SystemExit("handoff was not inherited")
    record_path, hidden_path = map(Path, arguments[1:])
else:
    record_path, hidden_path = map(Path, arguments)
hidden_pid = os.fork()
if hidden_pid == 0:
    os.setsid()
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    hidden_path.write_text(str(os.getpid()), encoding="ascii")
    time.sleep(60)
    os._exit(0)
for _ in range(100):
    if hidden_path.exists():
        break
    time.sleep(0.01)
record_path.write_text(f"{os.getpid()} {hidden_pid}", encoding="ascii")
if len(arguments) == 2:
    os.write(1, b"1:2:3\\n")
signal.signal(signal.SIGTERM, lambda *_args: os._exit(0))
time.sleep(60)
"""
        for mode in ("lifecycle", "identity"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                process_record = root / "processes"
                hidden_record = root / "hidden"
                module = self._load_supervisor(f"rpm_reap_emergency_faults_{mode}")
                original_popen = module.subprocess.Popen
                original_reap = module.reap_ready_children
                original_emergency = module.emergency_direct_cleanup
                launched: list[int] = []
                emergency_calls: list[int] = []
                reap_failures = [2]
                emergency_failures = [1]

                def tracking_popen(*args: object, **kwargs: object) -> object:
                    process = original_popen(*args, **kwargs)
                    launched.append(process.pid)
                    return process

                def fail_reap(*_args: object, **_kwargs: object) -> object:
                    if reap_failures[0]:
                        reap_failures[0] -= 1
                        raise OSError(errno.EIO, "injected reap-ready failure")
                    return original_reap(*_args, **_kwargs)

                def fail_emergency(*_args: object, **_kwargs: object) -> object:
                    emergency_calls.append(1)
                    if emergency_failures[0]:
                        emergency_failures[0] = 0
                        raise RuntimeError("injected emergency drain failure")
                    return original_emergency(*_args, **_kwargs)

                module.subprocess.Popen = tracking_popen
                module.reap_ready_children = fail_reap
                module.emergency_direct_cleanup = fail_emergency
                handoff_read: int | None = None
                before_handlers = {
                    signum: signal.getsignal(signum)
                    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
                }
                before_fds = set(os.listdir(f"/proc/{os.getpid()}/fd"))
                process_ids: list[int] = []
                try:
                    if mode == "lifecycle":
                        handoff_read, handoff_write = os.pipe()
                        os.write(handoff_write, b"rpm-lifecycle-test\n")
                        os.close(handoff_write)
                        stderr_capture = io.StringIO()
                        with contextlib.redirect_stderr(stderr_capture):
                            result = module.run(
                                [
                                    sys.executable,
                                    "-c",
                                    child_code,
                                    str(handoff_read),
                                    str(process_record),
                                    str(hidden_record),
                                ],
                                timeout=1.2,
                                kill_after=0.15,
                                handoff_fd=handoff_read,
                            )
                        self.assertEqual(result, 124)
                        self.assertIn("injected reap-ready failure", stderr_capture.getvalue())
                        self.assertIn("injected emergency drain failure", stderr_capture.getvalue())
                        self.assertTrue(emergency_calls)
                    else:
                        with self.assertRaisesRegex(OSError, "injected reap-ready failure") as raised:
                            module.run_bounded_identity(
                                [
                                    sys.executable,
                                    "-c",
                                    child_code,
                                    str(process_record),
                                    str(hidden_record),
                                ],
                                timeout=1.2,
                                max_bytes=256,
                            )
                        notes = "\n".join(getattr(raised.exception, "__notes__", ()))
                        self.assertIn("injected emergency drain failure", notes)
                        self.assertTrue(emergency_calls)
                finally:
                    module.subprocess.Popen = original_popen
                    module.reap_ready_children = original_reap
                    module.emergency_direct_cleanup = original_emergency
                    try:
                        process_ids.extend(map(int, process_record.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        pass
                    process_ids.extend(launched)
                    self._cleanup_for_finally(process_ids)
                    if handoff_read is not None:
                        try:
                            os.close(handoff_read)
                        except OSError:
                            pass
                self.assertEqual(
                    before_handlers,
                    {
                        signum: signal.getsignal(signum)
                        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
                    },
                )
                self.assertEqual(before_fds, set(os.listdir(f"/proc/{os.getpid()}/fd")))
                for process_id in set(process_ids):
                    self._assert_reaped(process_id)

    def test_children_read_faults_cleanup_full_and_partial_batches_in_both_modes(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            for fault in ("oserror", "eintr"):
                for child_count in (40, 20):
                    for shape in ("complete", "unterminated"):
                        for mode in ("lifecycle", "identity"):
                            with self.subTest(
                                fault=fault,
                                child_count=child_count,
                                shape=shape,
                                mode=mode,
                            ):
                                self._assert_children_read_fault_case(
                                    mode=mode,
                                    child_count=child_count,
                                    fault=fault,
                                    shape=shape,
                                    unrelated=unrelated,
                                )
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_non_foreground_timeout_root_return_drains_adopted_descendants(self) -> None:
        code = """
import os
import subprocess
import sys
import time
from pathlib import Path

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
processes = Path(sys.argv[2])
nested_pid = processes.with_name("nested.pid")
inner = (
    "import os,signal,sys,time; "
    "open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid())); "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
)
timeout_process = subprocess.Popen(
    ["timeout", "--signal=TERM", "--kill-after=0.2", "60", sys.executable, "-c", inner, str(nested_pid)],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
for _ in range(100):
    if nested_pid.exists():
        break
    time.sleep(0.01)
timeout_stat = Path(f"/proc/{timeout_process.pid}/stat").read_text(encoding="ascii")
timeout_group = timeout_stat.rsplit(") ", 1)[1].split()[2]
processes.write_text(f"{os.getpid()} {os.getpgrp()} {timeout_process.pid} {timeout_group}\\n", encoding="ascii")
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = root / "processes"
            process_ids: list[int] = []
            nested_pid: int | None = None
            try:
                result = self._run_supervisor(root, timeout=2, kill_after=0.2, code=code)
                process_ids = list(map(int, processes.read_text().split()))
                root_pid, root_group, timeout_pid, timeout_group = process_ids
                nested_pid = int((root / "nested.pid").read_text())

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(root_pid, root_group)
                self.assertNotEqual(root_group, timeout_group)
                self._assert_reaped(root_pid)
                self._assert_reaped(timeout_pid)
                self._assert_reaped(nested_pid)
            finally:
                if not process_ids:
                    try:
                        process_ids = list(map(int, processes.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        process_ids = []
                if nested_pid is not None:
                    process_ids.append(nested_pid)
                self._cleanup_for_finally(process_ids)

    def test_non_foreground_timeout_outer_timeout_drains_adopted_descendants(self) -> None:
        code = """
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
processes = Path(sys.argv[2])
nested_pid = processes.with_name("nested.pid")
inner = (
    "import os,signal,sys,time; "
    "open(sys.argv[1], 'w', encoding='ascii').write(str(os.getpid())); "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
)
timeout_process = subprocess.Popen(
    ["timeout", "--signal=TERM", "--kill-after=0.2", "60", sys.executable, "-c", inner, str(nested_pid)],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
for _ in range(100):
    if nested_pid.exists():
        break
    time.sleep(0.01)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
timeout_stat = Path(f"/proc/{timeout_process.pid}/stat").read_text(encoding="ascii")
timeout_group = timeout_stat.rsplit(") ", 1)[1].split()[2]
processes.write_text(f"{os.getpid()} {os.getpgrp()} {timeout_process.pid} {timeout_group}\\n", encoding="ascii")
time.sleep(60)
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            processes = root / "processes"
            process_ids: list[int] = []
            nested_pid: int | None = None
            try:
                result = self._run_supervisor(root, timeout=1, kill_after=0.2, code=code)
                process_ids = list(map(int, processes.read_text().split()))
                root_pid, root_group, timeout_pid, timeout_group = process_ids
                nested_pid = int((root / "nested.pid").read_text())

                self.assertEqual(result.returncode, 124, result.stderr)
                self.assertEqual(root_pid, root_group)
                self.assertNotEqual(root_group, timeout_group)
                self._assert_reaped(root_pid)
                self._assert_reaped(timeout_pid)
                self._assert_reaped(nested_pid)
            finally:
                if not process_ids:
                    try:
                        process_ids = list(map(int, processes.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        process_ids = []
                if nested_pid is not None:
                    process_ids.append(nested_pid)
                self._cleanup_for_finally(process_ids)

    def test_unrelated_process_group_survives_cleanup(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                result = self._run_supervisor(
                    Path(tmp),
                    timeout=1,
                    kill_after=0.2,
                    code="raise SystemExit(0)",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(unrelated.poll())
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_unverifiable_group_leader_never_gets_stale_group_signal(self) -> None:
        spec = importlib.util.spec_from_file_location("rpm_lifecycle_supervisor_stale", SUPERVISOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        binding = module.ChildBinding(
            module.ProcessIdentity(1234, 1, os.getpid(), 1234, 1234, "Z"),
            -1,
        )
        original_identity = module.verify_child
        original_killpg = module.os.killpg
        called = []
        module.verify_child = lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError())
        module.os.killpg = lambda *args: called.append(args)
        try:
            self.assertFalse(module.signal_verified_group(binding, signal.SIGKILL, expected_parent_pid=os.getpid()))
        finally:
            module.verify_child = original_identity
            module.os.killpg = original_killpg
        self.assertEqual(called, [])

    def test_status_zero_and_seven_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            success = self._run_supervisor(
                root, timeout=1, kill_after=0.2, code="raise SystemExit(0)"
            )
            failure = self._run_supervisor(
                root, timeout=1, kill_after=0.2, code="raise SystemExit(7)"
            )

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(failure.returncode, 7, failure.stderr)

    def test_cleanup_fault_cannot_mask_nonzero_status_or_allow_clean_zero(self) -> None:
        for status in (0, 7):
            with self.subTest(status=status):
                module = self._load_supervisor(f"rpm_status_cleanup_fault_{status}")
                original_close = module.close_binding

                def fail_close(binding: object, **_kwargs: object) -> None:
                    if getattr(binding, "identity").pid != os.getpid():
                        raise OSError(errno.EIO, "injected root close failure")
                    original_close(binding, **_kwargs)

                module.close_binding = fail_close
                handoff_read, handoff_write = os.pipe()
                os.write(handoff_write, b"rpm-lifecycle-test\n")
                os.close(handoff_write)
                stderr_capture = io.StringIO()
                try:
                    if status == 0:
                        with self.assertRaisesRegex(OSError, "injected root close failure"):
                            module.run(
                                [sys.executable, "-c", "raise SystemExit(0)"],
                                timeout=1,
                                kill_after=0.2,
                                handoff_fd=handoff_read,
                            )
                    else:
                        with contextlib.redirect_stderr(stderr_capture):
                            result = module.run(
                                [sys.executable, "-c", "raise SystemExit(7)"],
                                timeout=1,
                                kill_after=0.2,
                                handoff_fd=handoff_read,
                            )
                        self.assertEqual(result, 7)
                        self.assertIn("injected root close failure", stderr_capture.getvalue())
                finally:
                    module.close_binding = original_close
                    try:
                        os.close(handoff_read)
                    except OSError:
                        pass

    def test_large_nul_regular_fd_is_rejected_with_byte_bound(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large-nul"
            path.write_bytes(b"\0" * (1024 * 1024))
            handoff_fd = os.open(path, os.O_RDONLY)
            try:
                started = time.monotonic()
                result = self._run_handoff_check(handoff_fd)
                elapsed = time.monotonic() - started
            finally:
                os.close(handoff_fd)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "invalid\n")
        self.assertLess(elapsed, 1)

    def test_never_ready_pipe_is_rejected_with_deadline(self) -> None:
        handoff_read, handoff_write = os.pipe()
        try:
            started = time.monotonic()
            result = self._run_handoff_check(handoff_read, timeout=0.1)
            elapsed = time.monotonic() - started
        finally:
            os.close(handoff_read)
            os.close(handoff_write)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "invalid\n")
        self.assertGreaterEqual(elapsed, 0.08)
        self.assertLess(elapsed, 1)

    def test_identity_helper_rejects_noisy_output_with_bounded_cleanup(self) -> None:
        code = "import sys,time; sys.stdout.write('x' * (4 * 1024 * 1024)); sys.stdout.flush(); time.sleep(60)"
        result = self._run_identity([sys.executable, "-c", code])

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertIn("output exceeds byte limit", result.stderr)

    def test_identity_helper_rejects_no_eof_and_reaps_adopted_child(self) -> None:
        code = (
            "import os,signal,time; "
            "pid=os.fork(); "
            "(os.close(0), signal.signal(signal.SIGTERM, signal.SIG_IGN), time.sleep(60)) if pid == 0 else None; "
            "os.close(1); os.close(2); os._exit(0)"
        )
        result = self._run_identity([sys.executable, "-c", code], timeout=1)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid identity", result.stderr)

    def test_identity_reserve_timeout_latches_before_term_handler_output(self) -> None:
        code = """
import signal
import sys
import time

def finish_on_term(_signum, _frame):
    sys.stdout.write("1:2:3\\n")
    sys.stdout.flush()
    raise SystemExit(0)

signal.signal(signal.SIGTERM, finish_on_term)
time.sleep(60)
"""
        result = self._run_identity(
            [sys.executable, "-c", code],
            timeout=0.8,
            process_timeout=3,
        )

        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertIn("deadline exceeded", result.stderr)

    def test_harness_timeout_cleans_recorded_detached_term_resistant_child(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        code = """
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

detached = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
    ],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
Path(sys.argv[-1]).write_text(
    f"{os.getpid()} {detached.pid}\\n",
    encoding="ascii",
)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(60)
"""
        try:
            for mode in ("identity", "lifecycle"):
                with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    process_file = root / "processes"
                    process_ids: list[int] = []
                    try:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            if mode == "identity":
                                self._run_identity(
                                    [sys.executable, "-c", code, str(process_file)],
                                    timeout=10,
                                    process_timeout=0.8,
                                    known_process_file=process_file,
                                )
                            else:
                                self._run_supervisor(
                                    root,
                                    timeout=10,
                                    kill_after=1,
                                    code=code,
                                    process_timeout=0.8,
                                    known_process_file=process_file,
                                )
                        process_ids = list(map(int, process_file.read_text().split()))
                        self.assertEqual(len(process_ids), 2)
                        for process_id in process_ids:
                            self._assert_reaped(process_id)
                        self.assertIsNone(unrelated.poll())
                    finally:
                        if not process_ids:
                            try:
                                process_ids = list(map(int, process_file.read_text().split()))
                            except (FileNotFoundError, ValueError):
                                process_ids = []
                        self._cleanup_for_finally(process_ids)
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_identity_snapshot_error_is_primary_and_cleanup_error_is_note(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_snapshot_priority")
        original_drain = module.drain_adopted_children

        def failing_drain(*_args: object, **kwargs: object) -> bool:
            snapshot_errors = kwargs["snapshot_errors"]
            assert isinstance(snapshot_errors, list)
            snapshot_errors.append(OSError(errno.EIO, "injected snapshot read"))
            raise TimeoutError("injected cleanup timeout")

        module.drain_adopted_children = failing_drain
        try:
            with self.assertRaises(OSError) as raised:
                module.run_bounded_identity(
                    [sys.executable, "-c", "import sys; sys.stdout.write('1:2:3\\n')"],
                    timeout=1,
                    max_bytes=256,
                )
        finally:
            module.drain_adopted_children = original_drain
        self.assertIn("injected snapshot read", str(raised.exception))
        self.assertTrue(
            any("injected cleanup timeout" in note for note in raised.exception.__notes__)
        )

    def test_lifecycle_snapshot_error_is_primary_and_cleanup_error_is_note(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_lifecycle_snapshot_priority")
        original_drain = module.drain_adopted_children
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        os.close(handoff_write)

        def failing_drain(*_args: object, **kwargs: object) -> bool:
            snapshot_errors = kwargs["snapshot_errors"]
            assert isinstance(snapshot_errors, list)
            if not snapshot_errors:
                snapshot_errors.append(OSError(errno.EIO, "injected lifecycle snapshot read"))
            raise TimeoutError("injected lifecycle cleanup timeout")

        module.drain_adopted_children = failing_drain
        try:
            with self.assertRaises(OSError) as raised:
                module.run(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=1,
                    kill_after=0.2,
                    handoff_fd=handoff_read,
                )
        finally:
            module.drain_adopted_children = original_drain
        self.assertIn("injected lifecycle snapshot read", str(raised.exception))
        self.assertTrue(
            any("injected lifecycle cleanup timeout" in note for note in raised.exception.__notes__)
        )

    def test_valid_token_on_same_uid_foreign_fd_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foreign-token"
            path.write_bytes(b"rpm-lifecycle-1-2-3-4\n")
            handoff_fd = os.open(path, os.O_RDONLY)
            try:
                result = self._run_handoff_check(handoff_fd)
            finally:
                os.close(handoff_fd)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "valid\n")

    def test_subreaper_failure_closes_handoff_fd(self) -> None:
        spec = importlib.util.spec_from_file_location("rpm_lifecycle_supervisor", SUPERVISOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        handoff_read, handoff_write = os.pipe()
        os.close(handoff_write)

        def fail_subreaper() -> None:
            raise RuntimeError("injected subreaper failure")

        module.set_child_subreaper = fail_subreaper
        with self.assertRaisesRegex(RuntimeError, "injected subreaper failure"):
            module.run(
                [sys.executable, "-c", "raise SystemExit(0)"],
                timeout=1,
                kill_after=0.2,
                handoff_fd=handoff_read,
            )
        with self.assertRaises(OSError):
            os.fstat(handoff_read)

    def test_kernel_primitive_failure_closes_handoff_fd(self) -> None:
        spec = importlib.util.spec_from_file_location("rpm_lifecycle_supervisor_primitives", SUPERVISOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        handoff_read, handoff_write = os.pipe()
        os.close(handoff_write)

        def fail_primitives() -> object:
            raise RuntimeError("injected primitive failure")

        module.require_kernel_primitives = fail_primitives
        with self.assertRaisesRegex(RuntimeError, "injected primitive failure"):
            module.run(
                [sys.executable, "-c", "raise SystemExit(0)"],
                timeout=1,
                kill_after=0.2,
                handoff_fd=handoff_read,
            )
        with self.assertRaises(OSError):
            os.fstat(handoff_read)

    def test_signal_handlers_restore_after_launch_error(self) -> None:
        spec = importlib.util.spec_from_file_location("rpm_lifecycle_supervisor_handlers", SUPERVISOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        handoff_read, handoff_write = os.pipe()
        os.close(handoff_write)
        signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        before = {signum: signal.getsignal(signum) for signum in signals}
        original_popen = module.subprocess.Popen

        def fail_launch(*_args: object, **_kwargs: object) -> object:
            raise OSError("injected launch failure")

        module.subprocess.Popen = fail_launch
        try:
            with self.assertRaisesRegex(OSError, "injected launch failure"):
                module.run(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=1,
                    kill_after=0.2,
                    handoff_fd=handoff_read,
                )
        finally:
            module.subprocess.Popen = original_popen
        self.assertEqual(before, {signum: signal.getsignal(signum) for signum in signals})
        with self.assertRaises(OSError):
            os.fstat(handoff_read)

    def test_bind_failure_after_launch_kills_exact_unreaped_child(self) -> None:
        spec = importlib.util.spec_from_file_location("rpm_lifecycle_supervisor_bind", SUPERVISOR)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        os.close(handoff_write)
        record_fd, record_name = tempfile.mkstemp()
        os.close(record_fd)
        child_record = Path(record_name)
        original_bind = module.bind_child
        launched = []
        bind_calls = [0]
        process_ids: list[int] = []

        def fail_bind(process_id: int, *, expected_parent_pid: int) -> object:
            bind_calls[0] += 1
            launched.append(process_id)
            if bind_calls[0] == 1:
                ready_deadline = time.monotonic() + 0.5
                while time.monotonic() < ready_deadline:
                    try:
                        if child_record.read_text(encoding="ascii").strip():
                            break
                    except FileNotFoundError:
                        pass
                    time.sleep(0.01)
                raise RuntimeError("injected initial bind failure")
            return original_bind(process_id, expected_parent_pid=expected_parent_pid)

        module.bind_child = fail_bind
        try:
            with self.assertRaisesRegex(RuntimeError, "injected initial bind failure"):
                module.run(
                    [
                        sys.executable,
                        "-c",
                        "import os,signal,sys,time; "
                        "child=os.fork(); "
                        "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
                        "time.sleep(60) if child == 0 else None; "
                        "os._exit(0) if child == 0 else None; "
                        "fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600); "
                        "os.write(fd, str(child).encode('ascii')); os.close(fd); "
                        "time.sleep(60)",
                        str(child_record),
                    ],
                    timeout=2,
                        kill_after=0.2,
                        handoff_fd=handoff_read,
                    )
            self.assertGreaterEqual(len(launched), 1)
            process_ids = [launched[0], int(child_record.read_text(encoding="ascii"))]
            for process_id in process_ids:
                self._assert_reaped(process_id)
            with self.assertRaises(OSError):
                os.fstat(handoff_read)
        finally:
            module.bind_child = original_bind
            if launched:
                process_ids.extend(launched)
            try:
                process_ids.append(int(child_record.read_text(encoding="ascii")))
            except (FileNotFoundError, ValueError):
                pass
            self._cleanup_for_finally(process_ids)
            try:
                os.close(handoff_read)
            except OSError:
                pass
            child_record.unlink(missing_ok=True)

    def test_total_pidfd_resource_bind_failure_cleans_root_and_adopted_child_in_both_modes(self) -> None:
        code = (
            "import os,signal,sys,time; "
            "child=os.fork(); "
            "os.setsid() if child == 0 else None; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
            "time.sleep(60) if child == 0 else None; "
            "os._exit(0) if child == 0 else None; "
            "fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600); "
            "os.write(fd, str(child).encode('ascii')); os.close(fd); time.sleep(60)"
        )
        for mode in ("lifecycle", "identity"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                record_fd, record_name = tempfile.mkstemp(dir=tmp)
                os.close(record_fd)
                child_record = Path(record_name)
                module = self._load_supervisor(f"rpm_lifecycle_supervisor_total_{mode}")
                original_bind = module.bind_child
                original_exact = module.bind_child_exact
                original_owned = module.bind_owned_child_for_cleanup
                original_recovered = module.bind_recovered_child
                original_verified = module.make_verified_owned_child_binding
                launched: list[int] = []

                def fail_bind(*_args: object, **_kwargs: object) -> object:
                    if not launched:
                        launched.append(int(_args[0]))
                    ready_deadline = time.monotonic() + 1
                    while time.monotonic() < ready_deadline:
                        if child_record.read_text(encoding="ascii").strip():
                            break
                        time.sleep(0.01)
                    raise OSError(errno.EMFILE, "injected total PIDFD exhaustion")

                module.bind_child = fail_bind
                module.bind_child_exact = fail_bind
                module.bind_owned_child_for_cleanup = fail_bind
                module.bind_recovered_child = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    OSError(errno.EMFILE, "injected recovery PIDFD exhaustion")
                )
                module.make_verified_owned_child_binding = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                    RuntimeError("injected finalizer binding failure")
                )
                handoff_read = None
                process_ids: list[int] = []
                try:
                    if mode == "lifecycle":
                        handoff_read, handoff_write = os.pipe()
                        os.write(handoff_write, b"rpm-lifecycle-test\n")
                        os.close(handoff_write)
                        with self.assertRaisesRegex(OSError, "injected total PIDFD exhaustion"):
                            module.run(
                                [sys.executable, "-c", code, str(child_record)],
                                timeout=3,
                                kill_after=0.2,
                                handoff_fd=handoff_read,
                            )
                    else:
                        with self.assertRaisesRegex(OSError, "injected total PIDFD exhaustion"):
                            module.run_bounded_identity(
                                [sys.executable, "-c", code, str(child_record)],
                                timeout=3,
                                max_bytes=256,
                            )
                    root_pid = next(
                        process_id for process_id in launched if process_id > 0
                    )
                    adopted_pid = int(child_record.read_text(encoding="ascii"))
                    process_ids = [root_pid, adopted_pid]
                    if handoff_read is not None:
                        with self.assertRaises(OSError):
                            os.fstat(handoff_read)
                    self._assert_reaped(root_pid)
                    self._assert_reaped(adopted_pid)
                finally:
                    module.bind_child = original_bind
                    module.bind_child_exact = original_exact
                    module.bind_owned_child_for_cleanup = original_owned
                    module.bind_recovered_child = original_recovered
                    module.make_verified_owned_child_binding = original_verified
                    if not process_ids:
                        process_ids.extend(launched)
                        try:
                            process_ids.append(int(child_record.read_text(encoding="ascii")))
                        except (FileNotFoundError, ValueError):
                            pass
                    self._cleanup_for_finally(process_ids)
                    if handoff_read is not None:
                        try:
                            os.close(handoff_read)
                        except OSError:
                            pass
                    child_record.unlink(missing_ok=True)

    def test_unbound_popen_fallback_reaps_root_and_detached_child_in_both_modes(self) -> None:
        code = (
            "import os,signal,sys,time; "
            "child=os.fork(); "
            "os.setsid() if child == 0 else None; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
            "time.sleep(60) if child == 0 else None; "
            "os._exit(0) if child == 0 else None; "
            "fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600); "
            "os.write(fd, (str(child) + '\\n').encode('ascii')); os.close(fd); time.sleep(60)"
        )
        for mode in ("lifecycle", "identity"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                child_record = Path(tmp) / "child.pid"
                module = self._load_supervisor(f"rpm_lifecycle_supervisor_unbound_{mode}")
                original_bind = module.bind_child
                original_recovered = module.bind_recovered_child
                original_verified = module.make_verified_owned_child_binding
                original_unbound = module.bind_unbound_popen_root_for_cleanup
                bind_calls = [0]
                launched: list[int] = []
                process_ids: list[int] = []

                def fail_initial_then_bind(
                    process_id: int, *, expected_parent_pid: int
                ) -> object:
                    bind_calls[0] += 1
                    if bind_calls[0] == 1:
                        launched.append(process_id)
                        ready_deadline = time.monotonic() + 1
                        while time.monotonic() < ready_deadline:
                            try:
                                if child_record.read_text(encoding="ascii").strip():
                                    break
                            except FileNotFoundError:
                                pass
                            time.sleep(0.01)
                        raise OSError(errno.EMFILE, "injected initial PIDFD exhaustion")
                    return original_bind(
                        process_id,
                        expected_parent_pid=expected_parent_pid,
                    )

                def fail_recovery(*_args: object, **_kwargs: object) -> object:
                    raise OSError(errno.EMFILE, "injected recovery PIDFD exhaustion")

                def fail_finalizer(*_args: object, **_kwargs: object) -> object:
                    raise RuntimeError("injected finalizer binding failure")

                def fail_unbound(*_args: object, **_kwargs: object) -> object:
                    raise RuntimeError("injected unbound binding failure")

                module.bind_child = fail_initial_then_bind
                module.bind_recovered_child = fail_recovery
                module.make_verified_owned_child_binding = fail_finalizer
                module.bind_unbound_popen_root_for_cleanup = fail_unbound
                handoff_read: int | None = None
                try:
                    if mode == "lifecycle":
                        handoff_read, handoff_write = os.pipe()
                        os.write(handoff_write, b"rpm-lifecycle-test\n")
                        os.close(handoff_write)
                        with self.assertRaisesRegex(OSError, "injected initial PIDFD exhaustion"):
                            module.run(
                                [sys.executable, "-c", code, str(child_record)],
                                timeout=2,
                                kill_after=0.2,
                                handoff_fd=handoff_read,
                            )
                    else:
                        with self.assertRaisesRegex(OSError, "injected initial PIDFD exhaustion"):
                            module.run_bounded_identity(
                                [sys.executable, "-c", code, str(child_record)],
                                timeout=2,
                                max_bytes=256,
                            )
                    root_pid = launched[0]
                    child_pid = int(child_record.read_text(encoding="ascii"))
                    process_ids = [root_pid, child_pid]
                    self._assert_reaped(root_pid)
                    self._assert_reaped(child_pid)
                finally:
                    module.bind_child = original_bind
                    module.bind_recovered_child = original_recovered
                    module.make_verified_owned_child_binding = original_verified
                    module.bind_unbound_popen_root_for_cleanup = original_unbound
                    if not process_ids:
                        process_ids.extend(launched)
                        try:
                            process_ids.append(int(child_record.read_text(encoding="ascii")))
                        except (FileNotFoundError, ValueError):
                            pass
                    self._cleanup_for_finally(process_ids)
                    if handoff_read is not None:
                        try:
                            os.close(handoff_read)
                        except OSError:
                            pass

    def test_unbound_popen_cleanup_survives_root_waitpid_error(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_unbound_waitpid_error")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        original_waitpid = module.os.waitpid
        original_identity = module.read_authenticated_direct_child_identity
        original_poll = process.poll
        wait_calls = [0]

        def stable_poll() -> int | None:
            return None

        def injected_waitpid(process_id: int, options: int) -> tuple[int, int]:
            if process_id == process.pid:
                wait_calls[0] += 1
                if wait_calls[0] == 2:
                    raise OSError(errno.EIO, "injected root waitpid EIO")
            return original_waitpid(process_id, options)

        def stable_identity(
            process_id: int,
            *,
            expected_parent_pid: int,
            deadline: float | None = None,
        ) -> object:
            return module.ProcessIdentity(
                pid=process_id,
                start_time=1,
                parent_pid=expected_parent_pid,
                process_group_id=process_id,
                session_id=process_id,
                state="S",
            )

        module.os.waitpid = injected_waitpid
        module.read_authenticated_direct_child_identity = stable_identity
        process.poll = stable_poll  # type: ignore[method-assign]
        cleanup_errors: list[BaseException] = []
        try:
            self.assertTrue(
                module.cleanup_unbound_popen_root(
                    process,
                    expected_parent_pid=os.getpid(),
                    pidfd_send_signal=None,
                    bindings={},
                    baseline_children=set(),
                    deadline=time.monotonic() + 1,
                    signal_errors=[],
                    cleanup_errors=cleanup_errors,
                )
            )
        finally:
            module.os.waitpid = original_waitpid
            module.read_authenticated_direct_child_identity = original_identity
            process.poll = original_poll  # type: ignore[method-assign]
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        self.assertGreaterEqual(wait_calls[0], 3)
        self.assertTrue(
            any("injected root waitpid EIO" in str(error) for error in cleanup_errors)
        )
        self.assertIsNotNone(process.returncode)

    def test_resource_recovery_rejects_redirected_foreign_pidfd_in_both_modes(self) -> None:
        foreign = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        foreign_fd = os.pidfd_open(foreign.pid, 0)
        launched_paths: list[Path] = []
        launched_pids: list[int] = []
        subreaper_enabled = False
        try:
            self._set_test_subreaper(True)
            subreaper_enabled = True
            for mode in ("lifecycle", "identity"):
                with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    outcome_path = root / "outcome"
                    launched_path = root / "launched"
                    launched_paths.append(launched_path)
                    wrapper = f"""
import importlib.util
import os
import signal
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "rpm_foreign_recovery_{mode}", {str(SUPERVISOR)!r}
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
foreign_fd = os.pidfd_open(int(sys.argv[1]), 0)
original_bind = module.bind_child
original_pidfd_open = module.os.pidfd_open
original_popen = module.subprocess.Popen
after_popen = False
redirected = False

def fail_initial_bind(*_args, **_kwargs):
    raise OSError({errno.EMFILE!r}, "injected initial PIDFD exhaustion")

def tracking_popen(*args, **kwargs):
    global after_popen
    process = original_popen(*args, **kwargs)
    Path(sys.argv[3]).write_text(str(process.pid) + "\\n", encoding="ascii")
    after_popen = True
    return process

def redirected_pidfd_open(process_id, flags):
    global redirected
    if after_popen and not redirected:
        redirected = True
        return os.dup(foreign_fd)
    return original_pidfd_open(process_id, flags)

module.bind_child = fail_initial_bind
module.os.pidfd_open = redirected_pidfd_open
module.subprocess.Popen = tracking_popen
outcome = "error:unhandled"
try:
    if {mode!r} == "lifecycle":
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\\n")
        os.close(handoff_write)
        value = module.run(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=2,
            kill_after=0.2,
            handoff_fd=handoff_read,
        )
    else:
        value = module.run_bounded_identity(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            timeout=2,
            max_bytes=256,
        )
    outcome = "ok:%s" % value
except BaseException as exc:
    outcome = "error:%s:%s|notes:%s" % (
        type(exc).__name__,
        exc,
        ";".join(str(note) for note in getattr(exc, "__notes__", ())),
    )
finally:
    module.bind_child = original_bind
    module.os.pidfd_open = original_pidfd_open
    module.subprocess.Popen = original_popen
    try:
        os.close(foreign_fd)
    except OSError:
        pass
Path(sys.argv[2]).write_text(outcome + "\\nredirected=%s\\n" % redirected, encoding="utf-8")
"""
                    try:
                        result = subprocess.run(
                            [
                                sys.executable,
                                "-c",
                                wrapper,
                                str(foreign.pid),
                                str(outcome_path),
                                str(launched_path),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                            timeout=6,
                        )
                        outcome = outcome_path.read_text(encoding="utf-8")
                        launched_pid = self._read_strict_pid_record(
                            launched_path,
                            deadline=time.monotonic() + 1,
                        )
                        launched_pids.append(launched_pid)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn(
                            "error:OSError:[Errno 24] injected initial PIDFD exhaustion",
                            outcome,
                        )
                        self.assertIn("recovery pidfd target changed", outcome)
                        self.assertIn("redirected=True", outcome)
                        self.assertIsNone(foreign.poll())
                        self._assert_reaped(launched_pid)
                    finally:
                        try:
                            cleanup_pid = self._read_strict_pid_record(
                                launched_path,
                                deadline=time.monotonic() + 0.5,
                            )
                        except (FileNotFoundError, RuntimeError, TimeoutError):
                            pass
                        else:
                            if cleanup_pid not in launched_pids:
                                launched_pids.append(cleanup_pid)
        finally:
            cleanup_ids = list(launched_pids)
            for launched_path in launched_paths:
                try:
                    cleanup_ids.append(
                        self._read_strict_pid_record(
                            launched_path,
                            deadline=time.monotonic() + 0.5,
                        )
                    )
                except (FileNotFoundError, RuntimeError, TimeoutError):
                    pass
            try:
                self._cleanup_for_finally(cleanup_ids)
            finally:
                try:
                    os.close(foreign_fd)
                except OSError:
                    pass
                if foreign.poll() is None:
                    foreign.kill()
                foreign.wait(timeout=2)
                if subreaper_enabled:
                    self._set_test_subreaper(False)

    def test_adopted_child_emfile_uses_exact_pid_fallback_in_both_modes(self) -> None:
        code = (
            "import os,signal,sys,time; "
            "os.read(int(sys.argv[1]), 128) if len(sys.argv) > 2 else None; "
            "child=os.fork(); "
            "os.setsid() if child == 0 else None; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
            "time.sleep(60) if child == 0 else None; "
            "os._exit(0) if child == 0 else None; "
            "fd=os.open(sys.argv[-1], os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600); "
            "os.write(fd, f'{os.getpid()} {child}'.encode('ascii')); os.close(fd); "
            "os.write(1, b'1:2:3\\n') if len(sys.argv) == 2 else None; "
            "os._exit(0)"
        )
        for mode in ("lifecycle", "identity"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child_record = root / "processes"
                module = self._load_supervisor(f"rpm_lifecycle_supervisor_emfile_{mode}")
                original_pidfd_open = module.os.pidfd_open
                original_popen = module.subprocess.Popen
                pidfd_calls: list[int] = []
                launched: list[int] = []
                handoff_read: int | None = None

                def selective_pidfd_open(process_id: int, flags: int) -> int:
                    pidfd_calls.append(process_id)
                    try:
                        recorded = list(map(int, child_record.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        recorded = []
                    if len(recorded) >= 2 and process_id == recorded[1]:
                        raise OSError(errno.EMFILE, "injected adopted-child pidfd exhaustion")
                    return original_pidfd_open(process_id, flags)

                def tracking_popen(*args: object, **kwargs: object) -> object:
                    process = original_popen(*args, **kwargs)
                    launched.append(process.pid)
                    return process

                module.os.pidfd_open = selective_pidfd_open
                module.subprocess.Popen = tracking_popen
                try:
                    if mode == "lifecycle":
                        handoff_read, handoff_write = os.pipe()
                        os.write(handoff_write, b"rpm-lifecycle-test\n")
                        os.close(handoff_write)
                        result = module.run(
                            [
                                sys.executable,
                                "-c",
                                code,
                                str(handoff_read),
                                str(child_record),
                            ],
                            timeout=3,
                            kill_after=0.2,
                            handoff_fd=handoff_read,
                        )
                        self.assertEqual(result, 0)
                    else:
                        result = module.run_bounded_identity(
                            [
                                sys.executable,
                                "-c",
                                code,
                                str(child_record),
                            ],
                            timeout=3,
                            max_bytes=256,
                        )
                        self.assertEqual(result, "1:2:3")
                    process_ids = list(map(int, child_record.read_text().split()))
                    self.assertEqual(len(launched), 1)
                    self.assertIn(launched[0], pidfd_calls)
                    self.assertIn(process_ids[1], pidfd_calls)
                    self.assertNotEqual(launched[0], process_ids[1])
                    for process_id in process_ids:
                        self._assert_reaped(process_id)
                    if handoff_read is not None:
                        with self.assertRaises(OSError):
                            os.fstat(handoff_read)
                finally:
                    module.os.pidfd_open = original_pidfd_open
                    module.subprocess.Popen = original_popen
                    known_ids = list(launched)
                    try:
                        known_ids.extend(map(int, child_record.read_text().split()))
                    except (FileNotFoundError, ValueError):
                        pass
                    self._cleanup_for_finally(known_ids)
                    if handoff_read is not None:
                        try:
                            os.close(handoff_read)
                        except OSError:
                            pass

    def test_pidfd_runtime_failure_uses_identity_safe_exact_pid_fallback(self) -> None:
        unrelated = subprocess.Popen(
            ["sleep", "60"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child_record = root / "separate-pgid.pid"
                handoff_read, handoff_write = os.pipe()
                os.write(handoff_write, b"rpm-lifecycle-test\n")
                os.close(handoff_write)
                process_ids: list[int] = []
                code = (
                    "import os,subprocess,sys; "
                    "child=subprocess.Popen([sys.executable, '-c', "
                    "\"import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)\"], "
                    "start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, "
                    "stderr=subprocess.DEVNULL); "
                    "fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600); "
                    "os.write(fd, str(child.pid).encode('ascii')); os.close(fd); "
                    "os._exit(0)"
                )
                wrapper = f"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("rpm_runtime", {str(SUPERVISOR)!r})
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

def failing_sender(*_args):
    raise OSError("injected pidfd_send_signal failure")

module.validate_pidfd_send_signal = lambda _sender: None
module.require_kernel_primitives = lambda: failing_sender
result = module.run(
    [sys.executable, "-c", {code!r}, {str(child_record)!r}],
    timeout=2,
    kill_after=0.2,
    handoff_fd=int(sys.argv[1]),
)
print(result)
"""
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", wrapper, str(handoff_read)],
                        pass_fds=(handoff_read,),
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=5,
                    )
                    process_ids = [int(child_record.read_text(encoding="ascii"))]
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "0\n")
                    self.assertIn("exact-PID fallback used", result.stderr)
                    self._assert_reaped(process_ids[0])
                    self.assertIsNone(unrelated.poll())
                finally:
                    try:
                        os.close(handoff_read)
                    except OSError:
                        pass
                    if not process_ids:
                        try:
                            process_ids = list(map(int, child_record.read_text().split()))
                        except (FileNotFoundError, ValueError):
                            process_ids = []
                    self._cleanup_for_finally(process_ids)
        finally:
            if unrelated.poll() is None:
                unrelated.kill()
            unrelated.wait(timeout=2)

    def test_pidfd_send_signal_is_validated_before_popen(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_pidfd_preflight")
        handoff_read, handoff_write = os.pipe()
        os.close(handoff_write)
        events: list[str] = []
        original_validate = module.validate_pidfd_send_signal
        original_popen = module.subprocess.Popen

        def record_validation(_sender: object) -> None:
            events.append("validate")

        def fail_launch(*_args: object, **_kwargs: object) -> object:
            events.append("popen")
            raise OSError("injected launch failure")

        module.validate_pidfd_send_signal = record_validation
        module.subprocess.Popen = fail_launch
        try:
            with self.assertRaisesRegex(OSError, "injected launch failure"):
                module.run(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=1,
                    kill_after=0.2,
                    handoff_fd=handoff_read,
                )
        finally:
            module.validate_pidfd_send_signal = original_validate
            module.subprocess.Popen = original_popen
        self.assertEqual(events, ["validate", "popen"])
        with self.assertRaises(OSError):
            os.fstat(handoff_read)

    def test_preflight_deadline_blocks_child_launch_in_both_modes(self) -> None:
        for mode in ("lifecycle", "identity"):
            with self.subTest(mode=mode):
                module = self._load_supervisor(
                    f"rpm_lifecycle_supervisor_preflight_deadline_{mode}"
                )
                original_validate = module.validate_pidfd_send_signal
                original_popen = module.subprocess.Popen
                events: list[str] = []
                handoff_read: int | None = None

                def delayed_validation(_sender: object) -> None:
                    events.append("validate")
                    time.sleep(0.7)

                def unexpected_launch(*_args: object, **_kwargs: object) -> object:
                    events.append("popen")
                    raise AssertionError("child launched after startup budget")

                module.validate_pidfd_send_signal = delayed_validation
                module.subprocess.Popen = unexpected_launch
                try:
                    if mode == "lifecycle":
                        handoff_read, handoff_write = os.pipe()
                        os.write(handoff_write, b"rpm-lifecycle-test\n")
                        os.close(handoff_write)
                        with self.assertRaisesRegex(
                            TimeoutError, "launch budget exhausted"
                        ):
                            module.run(
                                [sys.executable, "-c", "raise SystemExit(0)"],
                                timeout=1,
                                kill_after=0.2,
                                handoff_fd=handoff_read,
                            )
                    else:
                        with self.assertRaisesRegex(
                            TimeoutError, "launch budget exhausted"
                        ):
                            module.run_bounded_identity(
                                [sys.executable, "-c", "raise SystemExit(0)"],
                                timeout=1,
                                max_bytes=256,
                            )
                finally:
                    module.validate_pidfd_send_signal = original_validate
                    module.subprocess.Popen = original_popen
                    if handoff_read is not None:
                        try:
                            os.close(handoff_read)
                        except OSError:
                            pass
                self.assertEqual(events, ["validate"])

    def test_slow_bind_phase_is_bounded_and_reaps_root(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_slow_bind")
        original_bind = module.bind_child
        original_popen = module.subprocess.Popen
        launched: list[int] = []
        handoff_read, handoff_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        os.close(handoff_write)

        def tracking_popen(*args: object, **kwargs: object) -> object:
            process = original_popen(*args, **kwargs)
            launched.append(process.pid)
            return process

        def slow_bind(process_id: int, *, expected_parent_pid: int) -> object:
            time.sleep(1.8)
            return original_bind(
                process_id, expected_parent_pid=expected_parent_pid
            )

        module.subprocess.Popen = tracking_popen
        module.bind_child = slow_bind
        try:
            with self.assertRaisesRegex(TimeoutError, "launch budget exhausted"):
                module.run(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    timeout=1.5,
                    kill_after=0.2,
                    handoff_fd=handoff_read,
                )
        finally:
            module.subprocess.Popen = original_popen
            module.bind_child = original_bind
            try:
                os.close(handoff_read)
            except OSError:
                pass
            self._cleanup_for_finally(launched)
        self.assertEqual(len(launched), 1)
        self._assert_reaped(launched[0])

    def test_close_binding_invalidates_pidfd_before_close_failure(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_close_binding")
        read_fd, write_fd = os.pipe()
        binding = module.ChildBinding(
            identity=module.ProcessIdentity(
                pid=os.getpid(),
                start_time=0,
                parent_pid=os.getpid(),
                process_group_id=os.getpgrp(),
                session_id=os.getsid(0),
                state="R",
            ),
            pidfd=read_fd,
        )
        original_close = module.os.close
        close_calls: list[int] = []

        def close_then_fail(descriptor: int) -> None:
            if descriptor == read_fd:
                close_calls.append(descriptor)
                original_close(descriptor)
                raise OSError(errno.EIO, "injected pidfd close failure")
            original_close(descriptor)

        module.os.close = close_then_fail
        errors: list[BaseException] = []
        try:
            module.close_binding(binding, errors=errors)
            replacement_read, replacement_write = os.pipe()
            try:
                module.close_binding(binding, errors=errors)
                self.assertEqual(binding.pidfd, -1)
                self.assertEqual(close_calls, [read_fd])
                self.assertTrue(errors)
                os.fstat(replacement_read)
            finally:
                original_close(replacement_read)
                original_close(replacement_write)
        finally:
            module.os.close = original_close
            try:
                original_close(write_fd)
            except OSError:
                pass

    def test_owned_binding_constructor_failure_closes_pidfd_once(self) -> None:
        module = self._load_supervisor("rpm_owned_binding_constructor_failure")
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        original_binding = module.ChildBinding
        original_close = module.os.close
        original_pidfd_open = module.os.pidfd_open
        pidfd: list[int] = []
        close_calls: list[int] = []
        replacement: list[int] = []

        def fail_binding(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError("injected binding construction failure")

        def close_then_fail(descriptor: int) -> None:
            if pidfd and descriptor == pidfd[0]:
                close_calls.append(descriptor)
                original_close(descriptor)
                replacement.extend(os.pipe())
                raise OSError(errno.EINTR, "injected binding close EINTR")
            original_close(descriptor)

        def tracking_pidfd_open(process_id: int, flags: int) -> int:
            descriptor = original_pidfd_open(process_id, flags)
            pidfd.append(descriptor)
            return descriptor

        module.ChildBinding = fail_binding
        module.os.pidfd_open = tracking_pidfd_open
        module.os.close = close_then_fail
        try:
            with self.assertRaisesRegex(RuntimeError, "injected binding construction failure") as raised:
                module.bind_owned_child_for_cleanup(
                    process.pid,
                    expected_parent_pid=os.getpid(),
                )
            self.assertTrue(replacement)
            os.fstat(replacement[0])
        finally:
            module.ChildBinding = original_binding
            module.os.pidfd_open = original_pidfd_open
            module.os.close = original_close
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            for descriptor in replacement:
                try:
                    original_close(descriptor)
                except OSError:
                    pass
        self.assertEqual(close_calls, pidfd)
        self.assertTrue(
            any(
                "injected binding close EINTR" in note
                for note in raised.exception.__notes__
            )
        )

    def test_identity_signal_returns_signal_status_and_reaps_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_record = root / "identity-child.pid"
            code = (
                "import os,signal,sys,time; "
                "fd=os.open(sys.argv[1], os.O_WRONLY|os.O_CREAT|os.O_TRUNC, 0o600); "
                "os.write(fd, (str(os.getpid()) + '\\n').encode('ascii')); os.close(fd); "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
            )
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--identity",
                    "--identity-timeout",
                    "2",
                    "--identity-max-bytes",
                    "256",
                    "--",
                    sys.executable,
                    "-c",
                    code,
                    str(child_record),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            process_ids: list[int] = []
            try:
                child_pid = self._read_strict_pid_record(
                    child_record,
                    deadline=time.monotonic() + 1,
                )
                process_ids = [child_pid]
                os.kill(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=4)
                self.assertEqual(stdout, "")
                self.assertEqual(process.returncode, 128 + signal.SIGTERM, stderr)
                self._assert_reaped(child_pid)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=2)
                if not process_ids:
                    try:
                        process_ids = [
                            self._read_strict_pid_record(
                                child_record,
                                deadline=time.monotonic() + 0.5,
                            )
                        ]
                    except (FileNotFoundError, TimeoutError, ValueError, RuntimeError):
                        process_ids = []
                self._cleanup_for_finally(process_ids)

    def test_identity_signal_handlers_restore_after_launch_error(self) -> None:
        module = self._load_supervisor("rpm_lifecycle_supervisor_identity_handlers")
        signals = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)
        before = {signum: signal.getsignal(signum) for signum in signals}
        original_popen = module.subprocess.Popen

        def fail_launch(*_args: object, **_kwargs: object) -> object:
            raise OSError("injected identity launch failure")

        module.subprocess.Popen = fail_launch
        try:
            with self.assertRaisesRegex(OSError, "injected identity launch failure"):
                module.run_bounded_identity(
                    [sys.executable, "-c", "raise SystemExit(0)"],
                    timeout=1,
                    max_bytes=256,
                )
        finally:
            module.subprocess.Popen = original_popen
        self.assertEqual(before, {signum: signal.getsignal(signum) for signum in signals})

    def test_unrelated_fd_is_closed_in_child(self) -> None:
        handoff_read, handoff_write = os.pipe()
        unrelated_read, unrelated_write = os.pipe()
        os.write(handoff_write, b"rpm-lifecycle-test\n")
        os.close(handoff_write)
        code = """
import os
import sys

if os.read(int(sys.argv[1]), 128) != b"rpm-lifecycle-test\\n":
    raise SystemExit("handoff was not inherited")
try:
    os.fstat(int(sys.argv[2]))
except OSError:
    pass
else:
    raise SystemExit("unrelated fd leaked")
"""
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SUPERVISOR),
                    "--timeout",
                    "1",
                    "--kill-after",
                    "0.2",
                    "--handoff-fd",
                    str(handoff_read),
                    "--",
                    sys.executable,
                    "-c",
                    code,
                    str(handoff_read),
                    str(unrelated_read),
                ],
                pass_fds=(handoff_read, unrelated_read),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        finally:
            os.close(handoff_read)
            os.close(unrelated_read)
            os.close(unrelated_write)

        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
