from __future__ import annotations

import ast
import ctypes
import errno
import math
import os
import re
import signal
import select
import shutil
import stat
import unittest
from pathlib import Path
import subprocess
import sys
import tempfile
import time


REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_RPM = REPO_ROOT / "scripts" / "build-rpm.sh"

_PID_RECORD_MISSING = "missing"
_PID_RECORD_MALFORMED = "malformed"
_PID_RECORD_VALID = "valid"
_PID_RECORD_MAX_BYTES = 256
_MAX_CLEANUP_ERRORS = 32


class _PidfdBindingError(RuntimeError):
    """PIDFD acquisition failed after record ownership preflight."""


class BuildRpmStaticTest(unittest.TestCase):
    @staticmethod
    def _append_bounded_error(
        errors: list[BaseException], error: BaseException, *, label: str
    ) -> None:
        if len(errors) < _MAX_CLEANUP_ERRORS - 1:
            errors.append(error)
        elif len(errors) == _MAX_CLEANUP_ERRORS - 1:
            errors.append(
                RuntimeError(
                    f"{label} diagnostics truncated after {_MAX_CLEANUP_ERRORS - 1} entries"
                )
            )

    def _embedded_python_program(self, marker: str) -> str:
        source = BUILD_RPM.read_text(encoding="utf-8")
        marker_start = source.index(marker)
        heredoc_start = source.index("<<'PY'", marker_start)
        start = source.index("\n", heredoc_start) + 1
        end = source.index("\nPY", start)
        return source[start:end]

    def _finalize_program(self) -> str:
        return self._embedded_python_program("activate_with_finalize_lock() {")

    def _startup_sweep_program(self) -> str:
        return self._embedded_python_program(
            'timeout --foreground --signal=TERM --kill-after="${RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS}s"'
        )

    def _output_program(self) -> str:
        return self._embedded_python_program(
            'timeout --foreground --signal=TERM --kill-after="${RPM_OUTPUT_KILL_AFTER_SECONDS}s"'
        )

    def _spec_rewrite_program(self) -> str:
        return self._embedded_python_program(
            '"${python_bin}" -I -B - <<\'PY\' "${spec_file}"'
        )

    def _rpmbuild_setup_program(self) -> str:
        return self._embedded_python_program('rpmbuild_bin="$(command -v -- rpmbuild)"')

    def _run_finalize(
        self,
        program: str,
        safe_fs: Path,
        lock: Path,
        stage: Path,
        final: Path,
        publish: Path,
        previous: Path,
        recovery: Path,
        lock_timeout: int = 2,
        finalize_timeout: int = 30,
        stage_identity: str | None = None,
        process_timeout: float = 30,
    ) -> subprocess.CompletedProcess[str]:
        if stage_identity is None:
            stat_result = stage.stat()
            stage_identity = f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}"
        for root_name in ("RPMS", "SRPMS"):
            (stage / root_name).mkdir(exist_ok=True)
        if final.exists() and final.is_dir() and not final.is_symlink():
            for root_name in ("RPMS", "SRPMS"):
                (final / root_name).mkdir(exist_ok=True)
        return self._run_bounded_harness(
            [
                sys.executable,
                "-",
                str(lock),
                str(safe_fs),
                str(stage),
                str(final),
                str(publish),
                str(previous),
                str(recovery),
                stage_identity,
                str(REPO_ROOT / "scripts" / "rpm-lifecycle-supervisor.py"),
                str(finalize_timeout),
                str(lock_timeout),
            ],
            cwd=REPO_ROOT,
            timeout=process_timeout,
            input_text=program,
        )

    def _read_proc_identity(self, pid: int) -> tuple[int, int, int, int]:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw.rsplit(") ", 1)[1].split()
        if len(fields) <= 19:
            raise ValueError(f"invalid /proc stat for PID {pid}")
        return int(fields[19]), int(fields[1]), int(fields[2]), int(fields[3])

    def _is_descendant_of(self, pid: int, ancestor_pid: int) -> bool:
        seen: set[int] = set()
        current_pid = pid
        for _ in range(64):
            if current_pid == ancestor_pid:
                return True
            if current_pid <= 1 or current_pid in seen:
                return False
            seen.add(current_pid)
            try:
                current_pid = self._read_proc_identity(current_pid)[1]
            except (FileNotFoundError, ProcessLookupError, ValueError):
                return False
        return False

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

    @staticmethod
    def _read_direct_child_ids() -> set[int]:
        descriptor = os.open(
            f"/proc/self/task/{os.getpid()}/children",
            os.O_RDONLY | os.O_CLOEXEC,
        )
        try:
            payload = os.read(descriptor, 4097)
        finally:
            os.close(descriptor)
        if len(payload) > 4096:
            raise RuntimeError("harness direct-child snapshot exceeds byte limit")
        try:
            return {int(token) for token in payload.split()}
        except ValueError as error:
            raise RuntimeError("harness direct-child snapshot is malformed") from error

    @staticmethod
    def _process_state(process_id: int) -> str | None:
        try:
            payload = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
        except (FileNotFoundError, ProcessLookupError):
            return None
        return payload.rsplit(") ", 1)[1].split(maxsplit=1)[0]

    def _read_pid_record(
        self, record: Path
    ) -> tuple[str, tuple[int, int, int, int, int] | None]:
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC
        descriptor: int | None = None
        try:
            descriptor = os.open(record, flags)
        except FileNotFoundError:
            return _PID_RECORD_MISSING, None
        except OSError:
            return _PID_RECORD_MALFORMED, None
        primary_error: BaseException | None = None
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > _PID_RECORD_MAX_BYTES:
                return _PID_RECORD_MALFORMED, None
            path_before = os.stat(record, follow_symlinks=False)
            if (
                not stat.S_ISREG(path_before.st_mode)
                or (path_before.st_dev, path_before.st_ino, path_before.st_mode)
                != (before.st_dev, before.st_ino, before.st_mode)
            ):
                return _PID_RECORD_MALFORMED, None
            payload = os.read(descriptor, _PID_RECORD_MAX_BYTES + 1)
            after = os.fstat(descriptor)
            path_after = os.stat(record, follow_symlinks=False)
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_size > _PID_RECORD_MAX_BYTES
                or len(payload) > _PID_RECORD_MAX_BYTES
                or (after.st_dev, after.st_ino, after.st_mode)
                != (before.st_dev, before.st_ino, before.st_mode)
                or (path_after.st_dev, path_after.st_ino, path_after.st_mode)
                != (after.st_dev, after.st_ino, after.st_mode)
                or after.st_size != len(payload)
            ):
                return _PID_RECORD_MALFORMED, None
            if payload.count(b"\n") != 1 or not payload.endswith(b"\n"):
                return _PID_RECORD_MALFORMED, None
            fields = payload[:-1].split(b":")
            if len(fields) != 5 or any(
                not field or any(byte < ord("0") or byte > ord("9") for byte in field)
                for field in fields
            ):
                return _PID_RECORD_MALFORMED, None
            values = tuple(int(field) for field in fields)
            if values[0] <= 0 or any(value < 0 for value in values[1:]):
                return _PID_RECORD_MALFORMED, None
            return _PID_RECORD_VALID, values  # type: ignore[return-value]
        except (FileNotFoundError, OSError, UnicodeError, ValueError) as error:
            primary_error = error
            return _PID_RECORD_MALFORMED, None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except BaseException as close_error:
                    close_failure = RuntimeError(
                        f"PID record close failed: {type(close_error).__name__}: {close_error}"
                    )
                    if primary_error is not None:
                        close_failure.add_note(
                            f"PID record read failed: {type(primary_error).__name__}: {primary_error}"
                        )
                        raise close_failure from primary_error
                    raise close_failure from close_error

    def _bound_pid_records(self) -> dict[Path, tuple[int, int, int, int, int]]:
        records = getattr(self, "_bound_pid_record_identities", None)
        if records is None:
            records = {}
            self._bound_pid_record_identities = records
        return records

    @staticmethod
    def _record_key(record: Path) -> Path:
        return record.absolute()

    def _remember_bound_pid_record(
        self, record: Path, fields: tuple[int, int, int, int, int]
    ) -> None:
        self._bound_pid_records()[self._record_key(record)] = fields

    def _has_bound_pid_record(
        self, record: Path, fields: tuple[int, int, int, int, int]
    ) -> bool:
        return self._bound_pid_records().get(self._record_key(record)) == fields

    def _forget_bound_pid_record(self, record: Path) -> None:
        self._bound_pid_records().pop(self._record_key(record), None)

    def _open_owned_pidfd(
        self,
        record: Path,
        *,
        expected_parent_pid: int | None = None,
        expected_ancestor_pid: int | None = None,
        expected_process_group_id: int | None = None,
        expected_session_id: int | None = None,
        require_bound: bool = False,
    ) -> int | None:
        state, fields = self._read_pid_record(record)
        if state != _PID_RECORD_VALID or fields is None:
            return None
        pid, start_time, parent_pid, process_group_id, session_id = fields
        if require_bound and not self._has_bound_pid_record(record, fields):
            return None

        def validate_identity(identity: tuple[int, int, int, int]) -> bool:
            if identity[0] != start_time or identity[2:] != (
                process_group_id,
                session_id,
            ):
                return False
            if expected_parent_pid is not None and identity[1] != expected_parent_pid:
                return False
            if expected_ancestor_pid is not None and identity[1] != parent_pid:
                return False
            if (
                expected_process_group_id is not None
                and identity[2] != expected_process_group_id
            ):
                return False
            if expected_session_id is not None and identity[3] != expected_session_id:
                return False
            if expected_ancestor_pid is not None and not self._is_descendant_of(
                pid, expected_ancestor_pid
            ):
                return False
            return True

        pidfd: int | None = None
        keep_fd = False
        try:
            if not validate_identity(self._read_proc_identity(pid)):
                return None
        except (FileNotFoundError, ProcessLookupError, ValueError):
            return None
        except OSError as error:
            raise RuntimeError(f"could not read owned PID record identity: {record}") from error

        try:
            try:
                pidfd = os.pidfd_open(pid, 0)
            except OSError as error:
                raise _PidfdBindingError(
                    f"could not open owned PIDFD for record: {record}"
                ) from error
            if not validate_identity(self._read_proc_identity(pid)):
                return None
        except (FileNotFoundError, ProcessLookupError, ValueError):
            return None
        except OSError as error:
            raise RuntimeError(f"could not revalidate owned PID record identity: {record}") from error
        except _PidfdBindingError:
            raise
        else:
            self._remember_bound_pid_record(record, fields)
            keep_fd = True
            return pidfd
        finally:
            primary_error = sys.exc_info()[1]
            if not keep_fd and pidfd is not None:
                descriptor = pidfd
                pidfd = None
                try:
                    os.close(descriptor)
                except BaseException as close_error:
                    close_failure = RuntimeError(
                        f"owned PIDFD close failed for record: {record}"
                    )
                    if primary_error is not None:
                        primary_error.add_note(
                            f"owned PIDFD close failed: {type(close_error).__name__}: {close_error}"
                        )
                    else:
                        raise close_failure from close_error

    def _pidfd_exited(self, pidfd: int, *, timeout: float) -> bool:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        timeout_ms = max(0, int(timeout * 1000))
        return bool(poller.poll(timeout_ms))

    def _signal_pidfd(self, pidfd: int, signum: signal.Signals) -> None:
        try:
            signal.pidfd_send_signal(pidfd, signum, None, 0)
        except ProcessLookupError:
            pass

    def _open_process_pidfd(
        self, process: subprocess.Popen[str]
    ) -> tuple[int, tuple[int, int, int, int]]:
        pidfd = os.pidfd_open(process.pid, 0)
        try:
            identity = self._read_proc_identity(process.pid)
            if identity[2] != process.pid or identity[3] != process.pid:
                raise AssertionError("harness process did not start in its own session")
            return pidfd, identity
        except BaseException as primary_error:
            descriptor = pidfd
            pidfd = -1
            try:
                os.close(descriptor)
            except BaseException as close_error:
                primary_error.add_note(
                    f"harness process pidfd close failed: {type(close_error).__name__}: {close_error}"
                )
            raise

    @staticmethod
    def _readline_bounded(stream: object, *, timeout: float, max_bytes: int = 4096) -> str:
        """Read one pipe line without allowing a partial line to block forever."""
        descriptor = stream.fileno()  # type: ignore[union-attr]
        deadline = time.monotonic() + timeout
        payload = bytearray()
        while len(payload) < max_bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("bounded readiness read timed out")
            try:
                readable, _, _ = select.select([descriptor], [], [], remaining)
            except InterruptedError:
                continue
            if not readable:
                raise TimeoutError("bounded readiness read timed out")
            chunk = os.read(descriptor, 1)
            if not chunk:
                break
            payload.extend(chunk)
            if chunk == b"\n":
                return payload.decode("utf-8")
        raise RuntimeError("bounded readiness line exceeded limit or ended early")

    def _signal_owned_process_group(
        self,
        process: subprocess.Popen[str],
        identity: tuple[int, int, int, int],
        signum: signal.Signals,
    ) -> None:
        if identity[2] != process.pid or identity[3] != process.pid:
            return
        # Popen child remains unreaped until cleanup ends, so its PID keeps
        # owning this process group even after it exits.
        try:
            os.killpg(identity[2], signum)
        except ProcessLookupError:
            pass

    def _cleanup_timed_out_process(
        self,
        process: subprocess.Popen[str],
        process_pidfd: int,
        process_identity: tuple[int, int, int, int],
        owned_pid_files: tuple[Path, ...],
        baseline_children: set[int] | None = None,
    ) -> list[BaseException]:
        cleanup_deadline = time.monotonic() + 1.5
        errors: list[BaseException] = []

        bind_deadline = max(time.monotonic(), cleanup_deadline - 0.25)
        try:
            owned_pidfds, binding_errors = self._bind_owned_pid_records(
                process,
                owned_pid_files,
                deadline=bind_deadline,
                retain_pidfds=True,
            )
        except BaseException as binding_error:
            owned_pidfds = []
            binding_errors = [binding_error]
        errors.extend(binding_errors)

        def attempt(label: str, operation: object) -> None:
            try:
                operation()  # type: ignore[operator]
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(
                    RuntimeError(
                        f"{label}: {type(error).__name__}: {error}"
                    )
                )

        poll_errors: set[str] = set()

        def pidfd_exited(pidfd: int, label: str) -> bool:
            try:
                return self._pidfd_exited(pidfd, timeout=0)
            except BaseException as error:
                if label not in poll_errors:
                    errors.append(
                        RuntimeError(
                            f"{label}: {type(error).__name__}: {error}"
                        )
                    )
                    poll_errors.add(label)
                return False

        kill_at = max(time.monotonic(), cleanup_deadline - 0.25)
        while time.monotonic() < kill_at:
            pending = []
            if not pidfd_exited(process_pidfd, "harness root exit poll"):
                pending.append(process_pidfd)
            for pidfd in owned_pidfds:
                if not pidfd_exited(pidfd, f"harness child {pidfd} exit poll"):
                    pending.append(pidfd)
            if not pending:
                break
            try:
                time.sleep(min(0.02, kill_at - time.monotonic()))
            except BaseException as error:
                errors.append(error)
                break

        attempt(
            "harness process-group SIGTERM",
            lambda: self._signal_owned_process_group(
                process, process_identity, signal.SIGTERM
            ),
        )
        attempt(
            "harness root SIGTERM",
            lambda: self._signal_pidfd(process_pidfd, signal.SIGTERM),
        )
        for pidfd in tuple(owned_pidfds):
            attempt(
                f"harness child {pidfd} SIGTERM",
                lambda pidfd=pidfd: self._signal_pidfd(pidfd, signal.SIGTERM),
            )

        while time.monotonic() < cleanup_deadline:
            exited = True
            for pidfd, label in [
                (process_pidfd, "harness root exit poll"),
                *((pidfd, f"harness child {pidfd} exit poll") for pidfd in owned_pidfds),
            ]:
                try:
                    if not self._pidfd_exited(pidfd, timeout=0):
                        exited = False
                except BaseException as error:
                    errors.append(
                        RuntimeError(
                            f"{label}: {type(error).__name__}: {error}"
                        )
                    )
                    exited = False
            if exited:
                break
            try:
                time.sleep(0.02)
            except BaseException as error:
                errors.append(error)
                break

        for pidfd in tuple(owned_pidfds):
            if not pidfd_exited(pidfd, f"harness child {pidfd} exit poll"):
                attempt(
                    f"harness child {pidfd} SIGKILL",
                    lambda pidfd=pidfd: self._signal_pidfd(pidfd, signal.SIGKILL),
                )
        attempt(
            "harness process-group SIGKILL",
            lambda: self._signal_owned_process_group(
                process, process_identity, signal.SIGKILL
            ),
        )
        if not pidfd_exited(process_pidfd, "harness root exit poll"):
            attempt(
                "harness root SIGKILL",
                lambda: self._signal_pidfd(process_pidfd, signal.SIGKILL),
            )
        unconfirmed = set(owned_pidfds)
        root_unconfirmed = True
        while time.monotonic() < cleanup_deadline:
            root_unconfirmed = not pidfd_exited(process_pidfd, "harness root exit poll")
            unconfirmed = {
                pidfd
                for pidfd in owned_pidfds
                if not pidfd_exited(pidfd, f"harness child {pidfd} exit poll")
            }
            if not root_unconfirmed and not unconfirmed:
                break
            try:
                time.sleep(min(0.02, cleanup_deadline - time.monotonic()))
            except BaseException as error:
                errors.append(error)
                break
        if root_unconfirmed:
            errors.append(RuntimeError("harness root exit remained unconfirmed"))
        for pidfd in sorted(unconfirmed):
            errors.append(RuntimeError(f"harness child {pidfd} exit remained unconfirmed"))
        try:
            process.wait(timeout=max(0.25, cleanup_deadline - time.monotonic()))
        except subprocess.TimeoutExpired as error:
            errors.append(error)
            attempt(
                "harness root final SIGKILL",
                lambda: self._signal_pidfd(process_pidfd, signal.SIGKILL),
            )
            try:
                process.wait(timeout=0.5)
            except BaseException as wait_error:
                errors.append(wait_error)
        except BaseException as error:
            errors.append(error)

        self._close_process_streams(process, errors)
        if baseline_children is not None:
            errors.extend(
                self._cleanup_launched_process(
                    process,
                    baseline_children=baseline_children,
                )
            )
        for index, pidfd in enumerate(tuple(owned_pidfds)):
            owned_pidfds[index] = -1
            try:
                os.close(pidfd)
            except BaseException as error:
                errors.append(error)
        return errors

    def _bind_owned_pid_records(
        self,
        process: subprocess.Popen[str],
        owned_pid_files: tuple[Path, ...],
        *,
        deadline: float,
        retain_pidfds: bool,
    ) -> tuple[list[int], list[BaseException]]:
        """Bind all records under one fair absolute deadline."""
        errors: list[BaseException] = []
        pidfds: list[int] = []
        pending = list(dict.fromkeys(owned_pid_files))
        last_errors: dict[Path, BaseException] = {}
        bound_records: set[Path] = set()
        while pending and time.monotonic() < deadline:
            next_pending: list[Path] = []
            for index, record in enumerate(pending):
                if time.monotonic() >= deadline:
                    next_pending.extend(pending[index:])
                    break
                try:
                    pidfd = self._open_owned_pidfd(
                        record,
                        expected_ancestor_pid=process.pid,
                    )
                except _PidfdBindingError as error:
                    self._append_bounded_error(
                        errors,
                        error,
                        label="owned PID record binding",
                    )
                    bound_records.add(record)
                    continue
                except BaseException as error:
                    last_errors[record] = error
                    next_pending.append(record)
                    continue
                if pidfd is None:
                    next_pending.append(record)
                    continue
                if retain_pidfds:
                    pidfds.append(pidfd)
                    bound_records.add(record)
                    last_errors.pop(record, None)
                    continue
                descriptor = pidfd
                pidfd = -1
                try:
                    os.close(descriptor)
                except BaseException as close_error:
                    self._forget_bound_pid_record(record)
                    self._append_bounded_error(
                        errors,
                        RuntimeError(
                            f"owned PIDFD close failed for record: {record}: "
                            f"{type(close_error).__name__}: {close_error}"
                        ),
                        label="owned PID record binding",
                    )
                    bound_records.add(record)
                    continue
                bound_records.add(record)
                last_errors.pop(record, None)
            pending = next_pending
            if pending and time.monotonic() < deadline:
                try:
                    time.sleep(min(0.01, deadline - time.monotonic()))
                except BaseException as error:
                    self._append_bounded_error(
                        errors,
                        error,
                        label="owned PID record binding",
                    )
                    break

        for index, record in enumerate(owned_pid_files):
            if time.monotonic() >= deadline:
                for pending_record in owned_pid_files[index:]:
                    previous_error = last_errors.pop(pending_record, None)
                    if previous_error is not None:
                        self._append_bounded_error(
                            errors,
                            previous_error,
                            label="owned PID record cleanup",
                        )
                self._append_bounded_error(
                    errors,
                    RuntimeError(
                        "owned PID record cleanup deadline exhausted; "
                        f"unresolved record count: {len(owned_pid_files) - index}"
                    ),
                    label="owned PID record cleanup",
                )
                break
            if record in bound_records:
                continue
            previous_error = last_errors.pop(record, None)
            if previous_error is not None:
                self._append_bounded_error(
                    errors,
                    previous_error,
                    label="owned PID record cleanup",
                )
            try:
                state, _fields = self._read_pid_record(record)
            except BaseException as error:
                self._append_bounded_error(
                    errors,
                    RuntimeError(
                        f"owned PID record reread failed during cleanup: {record}: "
                        f"{type(error).__name__}: {error}"
                    ),
                    label="owned PID record cleanup",
                )
                continue
            message = (
                "owned PID record unresolved during timeout cleanup"
                if retain_pidfds
                else "owned PID record unresolved during launch"
            )
            if state == _PID_RECORD_VALID:
                message = (
                    "owned PID record identity could not be revalidated during "
                    + ("timeout cleanup" if retain_pidfds else "launch")
                )
            self._append_bounded_error(
                errors,
                RuntimeError(f"{message}: {record}"),
                label="owned PID record cleanup",
            )
        return pidfds, errors

    def _bind_owned_pid_files(
        self,
        process: subprocess.Popen[str],
        owned_pid_files: tuple[Path, ...],
        *,
        deadline: float,
    ) -> list[BaseException]:
        """Bind record ownership while Popen ancestor is still unreaped."""
        _pidfds, errors = self._bind_owned_pid_records(
            process,
            owned_pid_files,
            deadline=deadline,
            retain_pidfds=False,
        )
        return errors

    def _cleanup_launched_process(
        self,
        process: subprocess.Popen[str],
        *,
        baseline_children: set[int] | None = None,
    ) -> list[BaseException]:
        """Clean a Popen child when pidfd binding failed before ownership setup."""
        errors: list[BaseException] = []
        cleanup_deadline = time.monotonic() + 1.5
        baseline = set() if baseline_children is None else set(baseline_children)
        tracked: dict[int, tuple[int, int, int, int]] = {}
        root_pidfd: int | None = None
        root_owned = False

        if process.returncode is None:
            root_owned = True
            waited_pid: int | None = None
            wait_status: int | None = None
            for attempt in range(3):
                try:
                    waited_pid, wait_status = os.waitpid(process.pid, os.WNOHANG)
                except InterruptedError as wait_error:
                    if attempt < 2 and time.monotonic() < cleanup_deadline:
                        continue
                    errors.append(wait_error)
                    break
                except ChildProcessError:
                    root_owned = False
                    break
                except OSError as wait_error:
                    errors.append(wait_error)
                    break
                except BaseException as wait_error:
                    errors.append(wait_error)
                    break
                break
            if root_owned and waited_pid == process.pid and wait_status is not None:
                process.returncode = os.waitstatus_to_exitcode(wait_status)
                root_owned = False
            if root_owned:
                try:
                    root_pidfd = os.pidfd_open(process.pid, 0)
                    fdinfo = Path(f"/proc/self/fdinfo/{root_pidfd}").read_bytes()
                    target_lines = re.findall(
                        rb"^Pid:\s+([0-9]+)$", fdinfo, re.MULTILINE
                    )
                    if len(target_lines) != 1 or int(target_lines[0]) != process.pid:
                        raise RuntimeError("harness root PIDFD target mismatch")
                except BaseException as pidfd_error:
                    if root_pidfd is not None:
                        descriptor = root_pidfd
                        root_pidfd = None
                        try:
                            os.close(descriptor)
                        except BaseException as close_error:
                            pidfd_error.add_note(
                                f"harness root PIDFD close failed: {close_error}"
                            )
                    errors.append(pidfd_error)
                try:
                    root_identity = self._read_proc_identity(process.pid)
                except BaseException as identity_error:
                    root_identity = None
                    errors.append(identity_error)
                if root_identity is not None:
                    tracked[process.pid] = root_identity

        def collect_owned_children() -> None:
            try:
                direct_children = self._read_direct_child_ids()
            except BaseException as error:
                errors.append(error)
                return
            for process_id in direct_children - baseline:
                if process_id in tracked:
                    continue
                try:
                    identity = self._read_proc_identity(process_id)
                except (FileNotFoundError, ProcessLookupError, ValueError):
                    continue
                except BaseException as error:
                    errors.append(error)
                    continue
                if identity[1] == os.getpid():
                    tracked[process_id] = identity

        def reap(process_id: int) -> bool:
            try:
                waited_pid, status = os.waitpid(process_id, os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                tracked.pop(process_id, None)
                return True
            except InterruptedError:
                return False
            except BaseException as error:
                errors.append(error)
                return False
            if waited_pid == process_id:
                tracked.pop(process_id, None)
                if process_id == process.pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                return True
            return False

        def root_is_alive() -> bool:
            nonlocal root_owned
            if not root_owned:
                return False
            for attempt in range(3):
                try:
                    waited_pid, status = os.waitpid(process.pid, os.WNOHANG)
                except InterruptedError as wait_error:
                    if attempt < 2 and time.monotonic() < cleanup_deadline:
                        continue
                    errors.append(wait_error)
                    return True
                except ChildProcessError:
                    root_owned = False
                    return False
                except OSError as wait_error:
                    errors.append(wait_error)
                    return True
                except BaseException as wait_error:
                    errors.append(wait_error)
                    return True
                if waited_pid == process.pid:
                    process.returncode = os.waitstatus_to_exitcode(status)
                    root_owned = False
                    tracked.pop(process.pid, None)
                    return False
                return True
            return True

        def signal_root(signum: signal.Signals) -> None:
            if not root_is_alive():
                return
            try:
                if root_pidfd is not None:
                    self._signal_pidfd(root_pidfd, signum)
                else:
                    process.send_signal(signum)
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)

        def signal_owned(process_id: int, expected: tuple[int, int, int, int], signum: signal.Signals) -> None:
            try:
                current = self._read_proc_identity(process_id)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                return
            if current[:2] != expected[:2] or current[1] != os.getpid():
                errors.append(RuntimeError(f"unbound harness child {process_id} changed identity"))
                return
            try:
                if current[2] == expected[2] and current[3] == expected[3]:
                    os.killpg(current[2], signum)
                else:
                    os.kill(process_id, signum)
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)

        collect_owned_children()

        signal_root(signal.SIGTERM)
        for process_id, identity in tuple(tracked.items()):
            if process_id == process.pid:
                continue
            signal_owned(process_id, identity, signal.SIGTERM)
        term_deadline = min(cleanup_deadline, time.monotonic() + 0.2)
        while tracked and time.monotonic() < term_deadline:
            collect_owned_children()
            for process_id in tuple(tracked):
                reap(process_id)
            if not tracked:
                break
            try:
                time.sleep(min(0.02, term_deadline - time.monotonic()))
            except BaseException as error:
                errors.append(error)
                break

        collect_owned_children()
        signal_root(signal.SIGKILL)
        for process_id, identity in tuple(tracked.items()):
            if process_id == process.pid:
                continue
            signal_owned(process_id, identity, signal.SIGKILL)
        while tracked and time.monotonic() < cleanup_deadline:
            collect_owned_children()
            for process_id in tuple(tracked):
                reap(process_id)
            if not tracked:
                break
            try:
                time.sleep(min(0.02, cleanup_deadline - time.monotonic()))
            except BaseException as error:
                errors.append(error)
                break
        try:
            process.wait(timeout=max(0.0, cleanup_deadline - time.monotonic()))
        except BaseException as error:
            errors.append(error)
        # Detached children can be adopted only after Popen root reaping.
        collect_owned_children()
        for process_id, identity in tuple(tracked.items()):
            if process_id != process.pid:
                signal_owned(process_id, identity, signal.SIGKILL)
        while tracked and time.monotonic() < cleanup_deadline:
            collect_owned_children()
            for process_id in tuple(tracked):
                reap(process_id)
            if not tracked:
                break
            try:
                time.sleep(min(0.02, cleanup_deadline - time.monotonic()))
            except BaseException as error:
                errors.append(error)
                break
        if tracked:
            errors.append(
                RuntimeError(
                    f"unbound harness cleanup left {len(tracked)} owned process(es) unconfirmed"
                )
            )
        self._close_process_streams(process, errors)
        if root_pidfd is not None:
            descriptor = root_pidfd
            root_pidfd = None
            try:
                os.close(descriptor)
            except BaseException as error:
                errors.append(error)
        return errors

    @staticmethod
    def _close_process_streams(
        process: subprocess.Popen[str], errors: list[BaseException]
    ) -> None:
        attempted = getattr(process, "_rpm_stream_close_attempted", set())
        setattr(process, "_rpm_stream_close_attempted", attempted)
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is None:
                continue
            if name in attempted:
                if getattr(stream, "closed", False):
                    setattr(process, name, None)
                continue
            attempted.add(name)
            try:
                stream.close()
            except BaseException as error:
                errors.append(error)
                if getattr(stream, "closed", False):
                    setattr(process, name, None)
            else:
                setattr(process, name, None)

    def _communicate_bounded_process(
        self,
        process: subprocess.Popen[str],
        *,
        process_pidfd: int,
        process_identity: tuple[int, int, int, int],
        timeout: float,
        owned_pid_files: tuple[Path, ...] = (),
        input_text: str | None = None,
        baseline_children: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            stdout, stderr = process.communicate(input=input_text, timeout=timeout)
        except BaseException as primary_error:
            try:
                cleanup_errors = self._cleanup_timed_out_process(
                    process,
                    process_pidfd,
                    process_identity,
                    owned_pid_files,
                    baseline_children,
                )
            except BaseException as cleanup_error:
                cleanup_errors = [cleanup_error]
            for cleanup_error in cleanup_errors:
                primary_error.add_note(
                    f"harness communication cleanup failed: "
                    f"{type(cleanup_error).__name__}: {cleanup_error}"
                )
            raise
        return subprocess.CompletedProcess(
            process.args, process.returncode, stdout, stderr
        )

    def _run_bounded_harness(
        self,
        command: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: dict[str, str] | None = None,
        input_text: str | None = None,
        owned_pid_files: tuple[Path, ...] = (),
    ) -> subprocess.CompletedProcess[str]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("bounded harness timeout must be finite and positive")
        operation_deadline = time.monotonic() + timeout
        baseline_children = self._read_direct_child_ids()
        self._set_test_subreaper(True)
        subreaper_enabled = True
        if time.monotonic() >= operation_deadline:
            self._set_test_subreaper(False)
            raise TimeoutError("bounded harness deadline exhausted before Popen")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.PIPE if input_text is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                text=True,
            )
        except BaseException as launch_error:
            try:
                self._set_test_subreaper(False)
            except BaseException as restore_error:
                launch_error.add_note(
                    f"harness subreaper restoration failed: {restore_error}"
                )
            raise
        process_pidfd: int | None = None
        primary_error: BaseException | None = None
        setup_cleanup_errors: list[BaseException] = []
        communication_started = False
        launch_cleanup_done = False
        completed_result: subprocess.CompletedProcess[str] | None = None
        try:
            try:
                process_pidfd, process_identity = self._open_process_pidfd(process)
            except BaseException as open_error:
                primary_error = open_error
                launch_cleanup_done = True
                for cleanup_error in self._cleanup_launched_process(
                    process,
                    baseline_children=baseline_children,
                ):
                    open_error.add_note(
                        f"harness launch cleanup failed: {cleanup_error}"
                    )
                raise
            setup_cleanup_errors.extend(
                self._bind_owned_pid_files(
                    process,
                    owned_pid_files,
                    deadline=operation_deadline,
                )
            )
            remaining = operation_deadline - time.monotonic()
            communication_started = True
            completed_result = self._communicate_bounded_process(
                process,
                process_pidfd=process_pidfd,
                process_identity=process_identity,
                timeout=max(0.0, remaining),
                owned_pid_files=owned_pid_files,
                input_text=input_text,
                baseline_children=baseline_children,
            )
            return completed_result
        except BaseException as error:
            primary_error = primary_error or error
            if not communication_started and not launch_cleanup_done:
                launch_cleanup_done = True
                for cleanup_error in self._cleanup_launched_process(
                    process,
                    baseline_children=baseline_children,
                ):
                    error.add_note(f"harness setup cleanup failed: {cleanup_error}")
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            deferred_cleanup_error: BaseException | None = None
            if process_pidfd is not None:
                descriptor = process_pidfd
                process_pidfd = None
                try:
                    os.close(descriptor)
                except BaseException as error:
                    cleanup_errors.append(error)
            self._close_process_streams(process, cleanup_errors)
            if cleanup_errors:
                cleanup_errors.extend(setup_cleanup_errors)
                if primary_error is not None:
                    for cleanup_error in cleanup_errors:
                        primary_error.add_note(
                            f"harness final cleanup failed: {cleanup_error}"
                        )
                else:
                    primary_cleanup_error = cleanup_errors[0]
                    for cleanup_error in cleanup_errors[1:]:
                        primary_cleanup_error.add_note(
                            f"harness additional cleanup failed: {cleanup_error}"
                        )
                    if completed_result is not None and completed_result.returncode != 0:
                        for cleanup_error in cleanup_errors:
                            print(
                                f"harness cleanup diagnostic: {cleanup_error}",
                                file=sys.stderr,
                            )
                    else:
                        deferred_cleanup_error = primary_cleanup_error
            elif setup_cleanup_errors:
                if primary_error is not None:
                    for cleanup_error in setup_cleanup_errors:
                        primary_error.add_note(
                            f"harness ownership cleanup failed: {cleanup_error}"
                        )
                else:
                    if completed_result is not None and completed_result.returncode != 0:
                        for cleanup_error in setup_cleanup_errors:
                            print(
                                f"harness ownership cleanup diagnostic: {cleanup_error}",
                                file=sys.stderr,
                            )
                    else:
                        deferred_cleanup_error = setup_cleanup_errors[0]
            if subreaper_enabled:
                try:
                    self._set_test_subreaper(False)
                except BaseException as restore_error:
                    if primary_error is not None:
                        primary_error.add_note(
                            f"harness subreaper restoration failed: {restore_error}"
                        )
                    else:
                        deferred_cleanup_error = restore_error
            if deferred_cleanup_error is not None:
                raise deferred_cleanup_error

    def _pidfd_record_exited(self, record: Path, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state, fields = self._read_pid_record(record)
            if state == _PID_RECORD_MISSING:
                time.sleep(0.02)
                continue
            if state != _PID_RECORD_VALID or fields is None:
                return False
            pidfd = self._open_owned_pidfd(record, require_bound=True)
            if pidfd is not None:
                exited = False
                try:
                    exited = self._pidfd_exited(pidfd, timeout=0)
                except (OSError, ValueError):
                    return False
                finally:
                    primary_error = sys.exc_info()[1]
                    descriptor = pidfd
                    pidfd = -1
                    try:
                        os.close(descriptor)
                    except BaseException as close_error:
                        if primary_error is not None:
                            primary_error.add_note(
                                "PID record PIDFD close failed: "
                                f"{type(close_error).__name__}: {close_error}"
                            )
                        else:
                            raise RuntimeError(
                                f"PID record PIDFD close failed: {record}"
                            ) from close_error
                if exited:
                    return True
            elif self._has_bound_pid_record(record, fields):
                try:
                    self._read_proc_identity(fields[0])
                except (FileNotFoundError, ProcessLookupError):
                    return True
                except (OSError, ValueError):
                    return False
                return False
            time.sleep(0.02)
        return False

    def _assert_pidfd_record_exited(
        self, record: Path, *, timeout: float, message: str
    ) -> None:
        if self._pidfd_record_exited(record, timeout=timeout):
            return
        state, fields = self._read_pid_record(record)
        pidfd = (
            self._open_owned_pidfd(record, require_bound=True)
            if state == _PID_RECORD_VALID and fields is not None
            else None
        )
        close_error: BaseException | None = None
        signal_error: BaseException | None = None
        if pidfd is not None:
            try:
                self._signal_pidfd(pidfd, signal.SIGKILL)
            except BaseException as error:
                signal_error = error
            finally:
                descriptor = pidfd
                pidfd = None
                try:
                    os.close(descriptor)
                except BaseException as error:
                    close_error = error
        if signal_error is not None:
            if close_error is not None:
                signal_error.add_note(f"cleanup close failed: {close_error}")
            raise signal_error
        if close_error is not None:
            message = f"{message}; cleanup close failed: {close_error}"
        self.fail(message)

    def _run_outer_cleanup_harness(self, root: Path, mode: str) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, mode=0o700)
        source = BUILD_RPM.read_text(encoding="utf-8")
        start = source.index("cleanup_now_ns() {")
        end = source.index("trap cleanup_on_exit EXIT", start) + len("trap cleanup_on_exit EXIT")
        cleanup_block = source[start:end]
        log = root / "safe-fs.log"
        stub = root / "safe-fs-stub.sh"
        real_helper = REPO_ROOT / "scripts" / "safe-local-fs.py"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"${SAFE_FS_LOG}\"\n"
            "if [[ -n \"${SAFE_FS_DELAY:-}\" ]]; then exec sleep \"${SAFE_FS_DELAY}\"; fi\n"
            "if [[ -n \"${SAFE_FS_STATUS:-}\" ]]; then exit \"${SAFE_FS_STATUS}\"; fi\n"
            f"exec python3 {str(real_helper)!r} \"$@\"\n",
            encoding="utf-8",
        )
        stub.chmod(0o700)
        clock_command = "(python3 -I -B)"
        if mode == "clock-blocked":
            clock_stub = root / "clock-stub.sh"
            clock_stub.write_text(
                "#!/usr/bin/env bash\n"
                "exec sleep 60\n",
                encoding="utf-8",
            )
            clock_stub.chmod(0o700)
            clock_command = f"(bash {str(clock_stub)!r})"
        elif mode == "clock-recovers":
            clock_stub = root / "clock-stub.sh"
            clock_state = root / "clock-state"
            clock_stub.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "state=$1\n"
                "count=0\n"
                "if [[ -f \"$state\" ]]; then count=$(<\"$state\"); fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$state\"\n"
                "if (( count == 1 )); then\n"
                "  python3 -I -B -c 'import time; print(time.monotonic_ns() + 4950000000)'\n"
                "elif (( count == 2 )); then\n"
                "  exec sleep 60\n"
                "else\n"
                "  python3 -I -B -c 'import time; print(time.monotonic_ns())'\n"
                "fi\n",
                encoding="utf-8",
            )
            clock_stub.chmod(0o700)
            clock_command = f"(bash {str(clock_stub)!r} {str(clock_state)!r})"
        elif mode == "clock-delayed":
            clock_stub = root / "clock-stub.sh"
            clock_state = root / "clock-state"
            clock_base = root / "clock-base"
            clock_stub.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "state=$1\n"
                "base_path=$2\n"
                "count=0\n"
                "if [[ -f \"$state\" ]]; then count=$(<\"$state\"); fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$state\"\n"
                "if (( count == 1 )); then\n"
                "  python3 - \"$base_path\" \"${4:-}\" <<'PY'\n"
                "import re\n"
                "import sys\n"
                "import time\n"
                "from pathlib import Path\n"
                "\n"
                "base_path = Path(sys.argv[1])\n"
                "code = sys.argv[2]\n"
                "match = re.search(r'\\+\\s+(\\d+)\\)', code)\n"
                "if match is None:\n"
                "    raise SystemExit(2)\n"
                "base = time.monotonic_ns()\n"
                "time.sleep(0.05)\n"
                "base_path.write_text(f'{base}\\n', encoding='ascii')\n"
                "print(base + int(match.group(1)))\n"
                "PY\n"
                "elif (( count == 2 )); then\n"
                "  base=$(<\"$base_path\")\n"
                "  printf '%s\\n' \"$((base + 4800000000))\"\n"
                "else\n"
                "  exit 1\n"
                "fi\n",
                encoding="utf-8",
            )
            clock_stub.chmod(0o700)
            clock_command = f"(bash {str(clock_stub)!r} {str(clock_state)!r} {str(clock_base)!r})"
        elif mode == "clock-post-blocked":
            clock_stub = root / "clock-stub.sh"
            clock_state = root / "clock-state"
            clock_stub.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "state=$1\n"
                "shift\n"
                "count=0\n"
                "if [[ -f \"$state\" ]]; then count=$(<\"$state\"); fi\n"
                "count=$((count + 1))\n"
                "printf '%s\\n' \"$count\" > \"$state\"\n"
                "if (( count == 3 )); then exec sleep 60; else python3 -I -B \"$@\"; fi\n",
                encoding="utf-8",
            )
            clock_stub.chmod(0o700)
            clock_command = f"(bash {str(clock_stub)!r} {str(clock_state)!r})"
        publish = root / "publish-workspace"
        recovery = root / "previous-recovery"
        stage = root / "stage"
        workspace = root / "workspace"
        for path in (publish, recovery, stage, workspace):
            path.mkdir(mode=0o700)
            (path / "marker").write_text("owned\n", encoding="utf-8")
        stage_stat = stage.stat()
        stage_identity = f"{stage_stat.st_dev}:{stage_stat.st_ino}:{stage_stat.st_mode}"
        workspace_stat = workspace.stat()
        workspace_identity = f"{workspace_stat.st_dev}:{workspace_stat.st_ino}:{workspace_stat.st_mode}"
        if mode == "normal":
            for path in (publish, recovery):
                (path / "marker").unlink()
                path.rmdir()
        harness = (
            "set -euo pipefail\n"
            f"{cleanup_block}\n"
            f"safe_fs_cmd=({str(stub)!r})\n"
            f"cleanup_timeout_command={str(shutil.which('timeout'))!r}\n"
            f"cleanup_clock_command={clock_command}\n"
            "RPM_CLEANUP_TIMEOUT_SECONDS=5\n"
            "RPM_CLEANUP_KILL_AFTER_SECONDS=1\n"
            "RPM_CLEANUP_LAUNCH_MARGIN_NS=50000000\n"
            "RPM_CLEANUP_CLOCK_TIMEOUT_SECONDS=0.25\n"
            "RPM_CLEANUP_CLOCK_TIMEOUT_NS=250000000\n"
            "cleanup_deadline_ns=''\n"
            f"export SAFE_FS_LOG={str(log)!r}\n"
            f"publish_workspace={str(publish)!r}\n"
            f"publish_recovery={str(recovery)!r}\n"
            f"stage_topdir={str(stage)!r}\n"
            f"stage_topdir_identity={stage_identity!r}\n"
            f"rpmbuild_tmpdir={str(workspace)!r}\n"
            f"rpmbuild_tmpdir_identity={workspace_identity!r}\n"
            "publish_committed=1\n"
            "trap cleanup_on_exit EXIT\n"
            "printf 'ready\\n'\n"
        )
        if mode == "normal":
            harness += (
                "publish_workspace=''\n"
                "publish_recovery=''\n"
                "exit 0\n"
            )
        elif mode == "blocked":
            harness += (
                "publish_committed=0\n"
                "export SAFE_FS_DELAY=60\n"
                "printf '\\n'\n"
                "exit 0\n"
            )
        elif mode == "primary":
            harness += "exit 7\n"
        elif mode == "clock-blocked":
            harness += "exit 0\n"
        elif mode == "clock-post-blocked":
            harness += (
                "publish_committed=0\n"
                "export SAFE_FS_STATUS=77\n"
                "exit 7\n"
            )
        elif mode == "stage-unverified":
            harness += (
                "publish_committed=0\n"
                "publish_workspace=''\n"
                "publish_recovery=''\n"
                "stage_topdir_identity=''\n"
                "exit 0\n"
            )
        elif mode == "stage-failed":
            harness += (
                "publish_committed=0\n"
                "publish_workspace=''\n"
                "publish_recovery=''\n"
                "export SAFE_FS_STATUS=77\n"
                "exit 0\n"
            )
        elif mode == "unverified":
            harness += (
                "stage_topdir=''\n"
                "stage_topdir_identity=''\n"
                "rpmbuild_tmpdir_identity=''\n"
                "exit 0\n"
            )
        else:
            harness += "IFS= read -r _\n"
        baseline_children = self._read_direct_child_ids()
        self._set_test_subreaper(True)
        subreaper_enabled = True
        process = subprocess.Popen(
            ["bash", "-c", harness],
            cwd=root,
            start_new_session=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        process_pidfd: int | None = None
        process_identity: tuple[int, int, int, int] | None = None
        primary_error: BaseException | None = None
        try:
            try:
                process_pidfd, process_identity = self._open_process_pidfd(process)
            except BaseException as open_error:
                primary_error = open_error
                for cleanup_error in self._cleanup_launched_process(
                    process,
                    baseline_children=baseline_children,
                ):
                    open_error.add_note(f"readiness launch cleanup failed: {cleanup_error}")
                raise
            if mode == "term":
                self.assertIsNotNone(process.stdout)
                self.assertEqual(self._readline_bounded(process.stdout, timeout=2), "ready\n")
                self._signal_pidfd(process_pidfd, signal.SIGTERM)
            elif mode == "kill":
                self.assertIsNotNone(process.stdout)
                self.assertEqual(self._readline_bounded(process.stdout, timeout=2), "ready\n")
                self._signal_pidfd(process_pidfd, signal.SIGKILL)
            elif mode == "blocked":
                self.assertIsNotNone(process.stdout)
                self.assertEqual(self._readline_bounded(process.stdout, timeout=2), "ready\n")
            return self._communicate_bounded_process(
                process,
                process_pidfd=process_pidfd,
                process_identity=process_identity,  # type: ignore[arg-type]
                timeout=10,
                baseline_children=baseline_children,
            )
        except BaseException as error:
            primary_error = primary_error or error
            if process_pidfd is not None and process_identity is not None:
                for cleanup_error in self._cleanup_timed_out_process(
                    process,
                    process_pidfd,
                    process_identity,
                    (),
                    baseline_children,
                ):
                    error.add_note(f"readiness cleanup failed: {cleanup_error}")
            raise
        finally:
            cleanup_errors: list[BaseException] = []
            deferred_cleanup_error: BaseException | None = None
            if process_pidfd is not None:
                try:
                    os.close(process_pidfd)
                except BaseException as error:
                    cleanup_errors.append(error)
            self._close_process_streams(process, cleanup_errors)
            if cleanup_errors:
                if primary_error is not None:
                    for cleanup_error in cleanup_errors:
                        primary_error.add_note(f"readiness final cleanup failed: {cleanup_error}")
                else:
                    deferred_cleanup_error = cleanup_errors[0]
            if subreaper_enabled:
                try:
                    self._set_test_subreaper(False)
                except BaseException as restore_error:
                    if primary_error is not None:
                        primary_error.add_note(
                            f"readiness subreaper restoration failed: {restore_error}"
                        )
                    else:
                        deferred_cleanup_error = restore_error
            if deferred_cleanup_error is not None:
                raise deferred_cleanup_error

    def _run_outer_inline_finalizer_harness(
        self, root: Path, *, block_scandir: bool = False
    ) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, exist_ok=True)
        source = BUILD_RPM.read_text(encoding="utf-8")
        start = source.index("activate_with_finalize_lock() {")
        end = source.index("\nrequire_cmd python3", start)
        function_block = source[start:end]
        if block_scandir:
            function_block = function_block.replace(
                "import time\n",
                "import time\n"
                "import signal\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "def blocking_scandir(*args, **kwargs):\n"
                "    time.sleep(60)\n"
                "os.scandir = blocking_scandir\n",
                1,
            )
        blocking = root / "blocking-safe-fs.py"
        blocking.write_text(
            "import ctypes\n"
            "import signal\n"
            "import time\n"
            "ctypes.CDLL(None).prctl(1, signal.SIGTERM)\n"
            "time.sleep(60)\n",
            encoding="utf-8",
        )
        dist = root / "dist"
        dist.mkdir(mode=0o700)
        stage = root / "stage"
        stage.mkdir()
        stage_stat = stage.stat()
        stage_identity = f"{stage_stat.st_dev}:{stage_stat.st_ino}:{stage_stat.st_mode}"
        lock = dist / ".finalize.lock"
        final = dist / "rpmbuild"
        publish = dist / ".rpmbuild.publish-workspace-block" / "candidate"
        previous = dist / "rpmbuild.previous"
        recovery = dist / ".rpmbuild.previous-recovery-block"
        harness = (
            "set +e\n"
            f"python_bin={sys.executable!r}\n"
            f"safe_fs={str(blocking)!r}\n"
            "RPM_FINALIZE_TIMEOUT_SECONDS=2\n"
            "RPM_FINALIZE_LOCK_TIMEOUT_SECONDS=1\n"
            "RPM_FINALIZE_KILL_AFTER_SECONDS=1\n"
            f"{function_block}\n"
            f"activate_with_finalize_lock {str(lock)!r} {str(stage)!r} {str(final)!r} "
            f"{str(publish)!r} {str(previous)!r} {str(recovery)!r} {stage_identity!r} "
            f"{str(REPO_ROOT / 'scripts' / 'rpm-lifecycle-supervisor.py')!r}\n"
            "status=$?\n"
            "printf 'status=%s\\n' \"$status\"\n"
            "exit \"$status\"\n"
        )
        started = time.monotonic()
        result = self._run_bounded_harness(
            ["bash", "-c", harness],
            cwd=root,
            timeout=10,
        )
        self.assertLess(time.monotonic() - started, 4)
        return result

    def _run_post_commit_cleanup_harness(self, root: Path) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, exist_ok=True)
        source = BUILD_RPM.read_text(encoding="utf-8")
        helper_start = source.index("cleanup_now_ns() {")
        helper_end = source.index("trap cleanup_on_exit EXIT", helper_start)
        helper_block = source[helper_start:helper_end]
        post_start = source.index("stage_cleanup_status=0\n", source.index("# RPM output enumeration stays bounded"))
        post_block = source[post_start:]
        log = root / "safe-fs.log"
        stub = root / "blocking-safe-fs.sh"
        real_helper = REPO_ROOT / "scripts" / "safe-local-fs.py"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"${SAFE_FS_LOG}\"\n"
            "exec sleep 60\n"
            f"exec python3 {str(real_helper)!r} \"$@\"\n",
            encoding="utf-8",
        )
        stub.chmod(0o700)
        stage = root / "stage"
        stage.mkdir()
        harness = (
            "set -u\n"
            f"{helper_block}\n"
            f"safe_fs_cmd=({str(stub)!r})\n"
            f"cleanup_timeout_command={str(shutil.which('timeout'))!r}\n"
            "cleanup_clock_command=(python3 -I -B)\n"
            "RPM_CLEANUP_TIMEOUT_SECONDS=1\n"
            "RPM_CLEANUP_KILL_AFTER_SECONDS=1\n"
            "RPM_CLEANUP_LAUNCH_MARGIN_NS=20000000\n"
            "RPM_CLEANUP_CLOCK_TIMEOUT_SECONDS=0.25\n"
            "RPM_CLEANUP_CLOCK_TIMEOUT_NS=250000000\n"
            "cleanup_deadline_ns=''\n"
            "stage_cleanup_unconfirmed=0\n"
            "stage_cleanup_status=0\n"
            "stage_cleanup_invoked=0\n"
            "cleanup_stage_message_emitted=0\n"
            "cleanup_parent_skip_reported=0\n"
            "output_status=0\n"
            f"export SAFE_FS_LOG={str(log)!r}\n"
            f"stage_topdir={str(stage)!r}\n"
            "stage_topdir_identity='stage-id'\n"
            "rpmbuild_tmpdir=''\n"
            "rpmbuild_tmpdir_identity=''\n"
            "publish_workspace=''\n"
            "publish_recovery=''\n"
            "publish_committed=1\n"
            f"{post_block}\n"
            "cleanup_tmpdir\n"
            "cleanup_status=$?\n"
            "printf 'cleanup-status=%s\\n' \"$cleanup_status\"\n"
        )
        started = time.monotonic()
        result = self._run_bounded_harness(
            ["bash", "-c", harness],
            cwd=root,
            timeout=8,
        )
        self.assertLess(time.monotonic() - started, 3)
        return result

    def _run_post_commit_output_harness(
        self,
        root: Path,
        *,
        block_scandir: bool = False,
        extra_entries: int = 0,
        output_byte_limit: int | None = None,
        final_symlink: bool = False,
        final_replacement: bool = False,
        leaf_race: str | None = None,
        output_root_race: bool = False,
        output_root_preopen_race: str | None = None,
        propagate_output_status: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, exist_ok=True)
        source = BUILD_RPM.read_text(encoding="utf-8")
        output_program = self._output_program()
        if block_scandir:
            output_program = output_program.replace(
                "import sys\n",
                "import sys\n"
                "import signal\n"
                "import time\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "def blocking_scandir(*args, **kwargs):\n"
                "    time.sleep(60)\n"
                "os.scandir = blocking_scandir\n",
                1,
            )
        if output_byte_limit is not None:
            output_program = output_program.replace(
                "MAX_RPM_OUTPUT_BYTES = 1 << 20",
                f"MAX_RPM_OUTPUT_BYTES = {output_byte_limit}",
                1,
            )
        if leaf_race is not None:
            if leaf_race == "content":
                mutation = (
                    "                with open(path, 'w', encoding='utf-8') as handle:\n"
                    "                    handle.write('mutated\\n')\n"
                )
            elif leaf_race == "replacement":
                mutation = (
                    "                os.rename(path, path + '.race-old')\n"
                    "                with open(path, 'w', encoding='utf-8') as handle:\n"
                    "                    handle.write('replacement\\n')\n"
                )
            else:
                raise ValueError(f"unknown leaf race: {leaf_race}")
            output_program = output_program.replace(
                "                output_bytes_for_path = len(os.fsencode(path)) + 1\n",
                mutation + "                output_bytes_for_path = len(os.fsencode(path)) + 1\n",
                1,
            )
        if output_root_race:
            output_program = output_program.replace(
                "for path in sorted(rpm_paths):\n    print(path, flush=True)\n",
                "output_root_race_done = False\n"
                "for path in sorted(rpm_paths):\n"
                "    print(path, flush=True)\n"
                "    if not output_root_race_done:\n"
                "        os.rename(os.path.join(final_path, 'RPMS'), os.path.join(final_path, 'RPMS.output-race'))\n"
                "        os.mkdir(os.path.join(final_path, 'RPMS'))\n"
                "        output_root_race_done = True\n",
                1,
            )
        if output_root_preopen_race is not None:
            if output_root_preopen_race not in {"RPMS", "SRPMS"}:
                raise ValueError(f"unknown output root race: {output_root_preopen_race}")
            output_program = output_program.replace(
                "check_final_directory()\ncheck_output_roots()\nfor root_name in (\"RPMS\", \"SRPMS\"):\n",
                "check_final_directory()\n"
                f"os.rename(os.path.join(final_path, {output_root_preopen_race!r}), "
                f"os.path.join(final_path, {output_root_preopen_race + '.pre-open-race'!r}))\n"
                f"os.mkdir(os.path.join(final_path, {output_root_preopen_race!r}))\n"
                f"with open(os.path.join(final_path, {output_root_preopen_race!r}, 'attacker.rpm'), 'w', encoding='utf-8') as handle:\n"
                "    handle.write('attacker\\n')\n"
                "check_output_roots()\nfor root_name in (\"RPMS\", \"SRPMS\"):\n",
                1,
            )
        output_start = source.index("# RPM output enumeration stays bounded")
        output_end = source.index("\nstage_cleanup_status=0", output_start)
        output_block = source[output_start:output_end].replace(
            self._output_program(), output_program, 1
        )
        final = root / "final"
        final_contents = root / "final-target" if final_symlink else final
        (final_contents / "RPMS").mkdir(parents=True)
        (final_contents / "SRPMS").mkdir()
        (final_contents / "RPMS" / "z.rpm").write_text("z\n", encoding="utf-8")
        (final_contents / "SRPMS" / "a.src.rpm").write_text("a\n", encoding="utf-8")
        for index in range(extra_entries):
            (final_contents / "RPMS" / f"unrelated-{index}").write_text("x\n", encoding="utf-8")
        if final_symlink:
            final.symlink_to(final_contents.name, target_is_directory=True)
        final_stat = final_contents.stat()
        final_identity = f"{final_stat.st_dev}:{final_stat.st_ino}:{final_stat.st_mode}"
        rpms_stat = (final_contents / "RPMS").stat()
        rpms_identity = f"{rpms_stat.st_dev}:{rpms_stat.st_ino}:{rpms_stat.st_mode}"
        srpms_stat = (final_contents / "SRPMS").stat()
        srpms_identity = f"{srpms_stat.st_dev}:{srpms_stat.st_ino}:{srpms_stat.st_mode}"
        cleanup = root / "cleanup-ran"
        replacement = (
            f"mv {str(final)!r} {str(final.with_name('final.replaced'))!r}\n"
            f"mkdir {str(final)!r}\n"
            if final_replacement
            else ""
        )
        harness = (
            "set +e\n"
            f"python_bin={sys.executable!r}\n"
            "RPM_OUTPUT_TIMEOUT_SECONDS=2\n"
            "RPM_OUTPUT_KILL_AFTER_SECONDS=1\n"
            f"final_topdir={str(final)!r}\n"
            f"final_topdir_identity={final_identity!r}\n"
            f"final_rpms_identity={rpms_identity!r}\n"
            f"final_srpms_identity={srpms_identity!r}\n"
            "exec {final_topdir_fd}<\"${final_topdir}\"\n"
            f"{replacement}"
            f"trap 'touch {str(cleanup)!r}' EXIT\n"
            f"{output_block}\n"
            "printf 'status=%s\\n' \"$output_status\"\n"
            + ('exit "$output_status"\n' if propagate_output_status else "")
        )
        return self._run_bounded_harness(
            ["bash", "-c", harness],
            cwd=root,
            timeout=8,
        )

    def test_build_rpm_outer_exit_term_and_kill_cleanup_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normal = self._run_outer_cleanup_harness(root / "normal", "normal")
            self.assertEqual(normal.returncode, 0, normal.stderr)
            normal_log = (root / "normal" / "safe-fs.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(normal_log), 2)
            self.assertIn("--kind dir", normal_log[0])
            self.assertFalse((root / "normal" / "stage").exists())
            self.assertFalse((root / "normal" / "workspace").exists())

            term_root = root / "term"
            term = self._run_outer_cleanup_harness(term_root, "term")
            self.assertNotEqual(term.returncode, 0)
            self.assertFalse((term_root / "stage").exists())
            self.assertFalse((term_root / "workspace").exists())
            self.assertTrue((term_root / "publish-workspace").exists())
            self.assertTrue((term_root / "previous-recovery").exists())
            self.assertIn("publish workspace cleanup accounting retained", term.stderr)
            self.assertIn("previous recovery cleanup accounting retained", term.stderr)

            kill_root = root / "kill"
            kill = self._run_outer_cleanup_harness(kill_root, "kill")
            self.assertEqual(kill.returncode, -signal.SIGKILL)
            self.assertTrue((kill_root / "stage").exists())
            self.assertTrue((kill_root / "workspace").exists())

    def test_rpm_lifecycle_watchdog_bounds_blocking_body(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            result = self._run_lifecycle_watchdog_harness(Path(tmp))
            elapsed = time.monotonic() - started

        self.assertIn(result.returncode, {-signal.SIGKILL, 124, 137, 143})
        self.assertLess(elapsed, 4)

    def test_harness_timeout_cleans_term_resistant_detached_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child_script = root / "detached-child.py"
            child_record = root / "detached-child.pid"
            child_script.write_text(
                "import os, signal, sys, time\n"
                "from pathlib import Path\n"
                "os.setsid()\n"
                "raw = Path(f'/proc/{os.getpid()}/stat').read_text(encoding='ascii')\n"
                "fields = raw.rsplit(') ', 1)[1].split()\n"
                "record = ':'.join((str(os.getpid()), fields[19], fields[1], fields[2], fields[3]))\n"
                "Path(sys.argv[1]).write_text(record + '\\n', encoding='ascii')\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            command = [
                "bash",
                "-c",
                f"{sys.executable!r} {str(child_script)!r} {str(child_record)!r} & wait",
            ]
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                self._run_bounded_harness(
                    command,
                    cwd=root,
                    timeout=0.25,
                    owned_pid_files=(child_record,),
                )

            self.assertGreater(caught.exception.timeout, 0)
            self.assertLessEqual(caught.exception.timeout, 0.25)
            self.assertLess(time.monotonic() - started, 3)
            self.assertTrue(self._pidfd_record_exited(child_record, timeout=1))

    def test_detached_pidfd_status_is_polled_after_kill_until_deadline(self) -> None:
        original_open = self._open_process_pidfd
        original_exited = self._pidfd_exited
        root_pidfd: list[int] = []

        def tracking_open(process: subprocess.Popen[str]) -> tuple[int, tuple[int, int, int, int]]:
            pidfd, identity = original_open(process)
            root_pidfd.append(pidfd)
            return pidfd, identity

        def hide_detached_exit(pidfd: int, *, timeout: float) -> bool:
            if root_pidfd and pidfd != root_pidfd[0]:
                return False
            return original_exited(pidfd, timeout=timeout)

        self._open_process_pidfd = tracking_open
        self._pidfd_exited = hide_detached_exit
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child_script = root / "detached-child.py"
                child_record = root / "detached-child.pid"
                child_script.write_text(
                    "import os, signal, sys, time\n"
                    "from pathlib import Path\n"
                    "os.setsid()\n"
                    "raw = Path(f'/proc/{os.getpid()}/stat').read_text(encoding='ascii')\n"
                    "fields = raw.rsplit(') ', 1)[1].split()\n"
                    "Path(sys.argv[1]).write_text(':'.join((str(os.getpid()), fields[19], fields[1], fields[2], fields[3])) + '\\n', encoding='ascii')\n"
                    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                    "time.sleep(60)\n",
                    encoding="utf-8",
                )
                command = [
                    "bash",
                    "-c",
                    f"{sys.executable!r} {str(child_script)!r} {str(child_record)!r} & wait",
                ]
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    self._run_bounded_harness(
                        command,
                        cwd=root,
                        timeout=0.2,
                        owned_pid_files=(child_record,),
                    )
                notes = "\n".join(raised.exception.__notes__)
                self.assertIn("harness child", notes)
                self.assertIn("exit remained unconfirmed", notes)
                self._pidfd_exited = original_exited
                self.assertTrue(self._pidfd_record_exited(child_record, timeout=1))
        finally:
            self._open_process_pidfd = original_open
            self._pidfd_exited = original_exited

    def test_pid_record_assertions_fail_closed_without_bound_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.pid"
            malformed = root / "malformed.pid"
            malformed.write_text("not-a-pid-record\n", encoding="ascii")
            foreign = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            try:
                identity = self._read_proc_identity(foreign.pid)
                foreign_record = root / "foreign.pid"
                foreign_record.write_text(
                    f"{foreign.pid}:{identity[0]}:{identity[1]}:{identity[2]}:{identity[3]}\n",
                    encoding="ascii",
                )
                stale_record = root / "stale.pid"
                stale_record.write_text(
                    f"{foreign.pid}:{identity[0] + 1}:{identity[1]}:{identity[2]}:{identity[3]}\n",
                    encoding="ascii",
                )

                for record in (missing, malformed, stale_record, foreign_record):
                    with self.subTest(record=record.name):
                        self.assertFalse(self._pidfd_record_exited(record, timeout=0.05))

                signal_calls: list[int] = []
                original_signal = self._signal_pidfd

                def unexpected_signal(pidfd: int, signum: signal.Signals) -> None:
                    signal_calls.append(pidfd)
                    original_signal(pidfd, signum)

                self._signal_pidfd = unexpected_signal
                try:
                    with self.assertRaises(AssertionError):
                        self._assert_pidfd_record_exited(
                            foreign_record,
                            timeout=0.05,
                            message="unbound foreign PID was accepted",
                        )
                finally:
                    self._signal_pidfd = original_signal
                self.assertEqual(signal_calls, [])
            finally:
                if foreign.poll() is None:
                    foreign.kill()
                foreign.wait(timeout=2)

    def test_pid_record_reader_is_bounded_regular_and_nonblocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "valid.pid"
            valid.write_text("123:456:7:8:9\n", encoding="ascii")
            fifo = root / "record.fifo"
            os.mkfifo(fifo, 0o600)
            symlink = root / "record.link"
            symlink.symlink_to(valid)
            oversized = root / "oversized.pid"
            oversized.write_bytes(b"1" * (_PID_RECORD_MAX_BYTES + 1))
            malformed_records = {
                "missing-newline": b"123:456:7:8:9",
                "double-newline": b"123:456:7:8:9\n\n",
                "whitespace": b"123:456:7:8:9 \n",
            }
            original_open = os.open
            observed_flags: list[int] = []

            def tracking_open(path: os.PathLike[str] | str, flags: int, *args: object, **kwargs: object) -> int:
                if Path(path) == valid:
                    observed_flags.append(flags)
                return original_open(path, flags, *args, **kwargs)

            os.open = tracking_open
            try:
                self.assertEqual(self._read_pid_record(valid)[0], _PID_RECORD_VALID)
            finally:
                os.open = original_open
            self.assertEqual(len(observed_flags), 1)
            self.assertTrue(observed_flags[0] & os.O_NONBLOCK)
            self.assertTrue(observed_flags[0] & os.O_NOFOLLOW)
            self.assertTrue(observed_flags[0] & os.O_CLOEXEC)
            self.assertEqual(self._read_pid_record(fifo)[0], _PID_RECORD_MALFORMED)
            self.assertEqual(self._read_pid_record(symlink)[0], _PID_RECORD_MALFORMED)
            self.assertEqual(self._read_pid_record(oversized)[0], _PID_RECORD_MALFORMED)
            for name, payload in malformed_records.items():
                with self.subTest(record=name):
                    path = root / f"{name}.pid"
                    path.write_bytes(payload)
                    self.assertEqual(self._read_pid_record(path)[0], _PID_RECORD_MALFORMED)

    def test_pidfd_open_failure_does_not_cache_record_ownership(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            try:
                identity = self._read_proc_identity(process.pid)
                record = root / "owned.pid"
                record.write_text(
                    f"{process.pid}:{identity[0]}:{identity[1]}:{identity[2]}:{identity[3]}\n",
                    encoding="ascii",
                )
                original_pidfd_open = os.pidfd_open

                def fail_pidfd_open(_pid: int, _flags: int) -> int:
                    raise OSError(errno.EMFILE, "injected PIDFD exhaustion")

                os.pidfd_open = fail_pidfd_open
                try:
                    with self.assertRaisesRegex(_PidfdBindingError, "could not open owned PIDFD"):
                        self._open_owned_pidfd(record, expected_parent_pid=os.getpid())
                finally:
                    os.pidfd_open = original_pidfd_open
                record_fields = (process.pid, *identity)
                self.assertFalse(self._has_bound_pid_record(record, record_fields))
                pidfd = self._open_owned_pidfd(record, expected_parent_pid=os.getpid())
                self.assertIsNotNone(pidfd)
                self.assertTrue(self._has_bound_pid_record(record, record_fields))
                if pidfd is not None:
                    os.close(pidfd)
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=2)

    def test_readiness_line_reader_times_out_on_partial_pipe(self) -> None:
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", closefd=True)
        try:
            os.write(write_fd, b"partial")
            started = time.monotonic()
            with self.assertRaisesRegex(TimeoutError, "readiness read timed out"):
                self._readline_bounded(reader, timeout=0.05)
            self.assertLess(time.monotonic() - started, 0.5)
        finally:
            reader.close()
            os.close(write_fd)

    def test_timeout_cleanup_isolates_signals_and_notes_original_timeout(self) -> None:
        original_group_signal = self._signal_owned_process_group
        original_pidfd_signal = self._signal_pidfd
        original_close_streams = self._close_process_streams

        def fail_term_group(*args: object, **kwargs: object) -> None:
            if args[-1] == signal.SIGTERM:
                raise OSError("injected group TERM failure")
            original_group_signal(*args, **kwargs)

        def fail_term_pidfd(pidfd: int, signum: signal.Signals) -> None:
            if signum == signal.SIGTERM:
                raise OSError("injected pidfd TERM failure")
            original_pidfd_signal(pidfd, signum)

        def fail_stream_close(
            process: subprocess.Popen[str], errors: list[BaseException]
        ) -> None:
            errors.append(OSError("injected stream close failure"))
            original_close_streams(process, errors)

        self._signal_owned_process_group = fail_term_group
        self._signal_pidfd = fail_term_pidfd
        self._close_process_streams = fail_stream_close
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(subprocess.TimeoutExpired) as caught:
                    self._run_bounded_harness(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=Path(tmp),
                        timeout=0.2,
                    )
        finally:
            self._signal_owned_process_group = original_group_signal
            self._signal_pidfd = original_pidfd_signal
            self._close_process_streams = original_close_streams

        notes = "\n".join(caught.exception.__notes__)
        self.assertIn("injected group TERM failure", notes)
        self.assertIn("injected pidfd TERM failure", notes)
        self.assertIn("injected stream close failure", notes)

    def test_unexpected_communicate_error_gets_bounded_cleanup(self) -> None:
        original_communicate = subprocess.Popen.communicate
        original_popen = subprocess.Popen
        launched: list[subprocess.Popen[str]] = []
        streams: list[object] = []

        def tracking_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = original_popen(*args, **kwargs)
            launched.append(process)
            streams.extend(stream for stream in (process.stdout, process.stderr) if stream is not None)
            return process

        def fail_communicate(
            _process: subprocess.Popen[str], *args: object, **kwargs: object
        ) -> tuple[str, str]:
            raise OSError(errno.EIO, "injected communicate failure")

        subprocess.Popen = tracking_popen
        original_popen.communicate = fail_communicate  # type: ignore[method-assign]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(OSError, "injected communicate failure") as raised:
                    self._run_bounded_harness(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=Path(tmp),
                        timeout=0.2,
                    )
        finally:
            original_popen.communicate = original_communicate  # type: ignore[method-assign]
            subprocess.Popen = original_popen
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].returncode)
        self.assertTrue(all(getattr(stream, "closed", False) for stream in streams))
        self.assertEqual(str(raised.exception), "[Errno 5] injected communicate failure")

    def test_pid_record_reread_failure_cannot_escape_timeout_cleanup(self) -> None:
        original_reader = self._read_pid_record

        def fail_reader(_record: Path) -> tuple[str, tuple[int, int, int, int, int] | None]:
            raise OSError(errno.EIO, "injected PID record reread failure")

        self._read_pid_record = fail_reader
        try:
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    self._run_bounded_harness(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=Path(tmp),
                        timeout=0.1,
                        owned_pid_files=(Path(tmp) / "owned.pid",),
                    )
        finally:
            self._read_pid_record = original_reader
        notes = "\n".join(raised.exception.__notes__)
        self.assertIn("injected PID record reread failure", notes)

    def test_many_pid_records_share_one_launch_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records = tuple(root / f"missing-{index}.pid" for index in range(301))
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                self._run_bounded_harness(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    cwd=root,
                    timeout=0.05,
                    owned_pid_files=records,
                )
        self.assertLess(time.monotonic() - started, 3)
        notes = "\n".join(raised.exception.__notes__)
        self.assertTrue(
            "owned PID record unresolved during launch" in notes
            or "owned PID record cleanup deadline exhausted" in notes
        )

    def test_slow_many_pid_records_stop_at_one_absolute_deadline(self) -> None:
        original_reader = self._read_pid_record

        def slow_missing(_record: Path) -> tuple[str, tuple[int, int, int, int, int] | None]:
            time.sleep(0.005)
            return _PID_RECORD_MISSING, None

        self._read_pid_record = slow_missing
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                records = tuple(root / f"slow-{index}.pid" for index in range(301))
                started = time.monotonic()
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    self._run_bounded_harness(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=root,
                        timeout=0.05,
                        owned_pid_files=records,
                    )
        finally:
            self._read_pid_record = original_reader
        self.assertLess(time.monotonic() - started, 3)
        self.assertIn("deadline exhausted", "\n".join(raised.exception.__notes__))

    def test_stream_close_failure_is_not_retried_or_clears_open_owner(self) -> None:
        class FailingStream:
            closed = False

            def __init__(self) -> None:
                self.calls = 0

            def close(self) -> None:
                self.calls += 1
                raise OSError(errno.EIO, "injected open stream close failure")

        class Process:
            stdin = None
            stderr = None

            def __init__(self, stream: FailingStream) -> None:
                self.stdout = stream

        stream = FailingStream()
        process = Process(stream)
        errors: list[BaseException] = []
        self._close_process_streams(process, errors)  # type: ignore[arg-type]
        self._close_process_streams(process, errors)  # type: ignore[arg-type]
        self.assertEqual(stream.calls, 1)
        self.assertIs(process.stdout, stream)
        self.assertEqual(len(errors), 1)
        self.assertIn("injected open stream close failure", str(errors[0]))

    def test_pidfd_binding_failure_after_popen_cleans_root_and_streams(self) -> None:
        original_open = self._open_process_pidfd
        original_popen = subprocess.Popen
        launched: list[subprocess.Popen[str]] = []
        streams: list[object] = []

        def fail_open(_process: subprocess.Popen[str]) -> tuple[int, tuple[int, int, int, int]]:
            raise RuntimeError("injected harness pidfd bind failure")

        def tracking_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = original_popen(*args, **kwargs)
            launched.append(process)
            if process.stdout is not None:
                streams.append(process.stdout)
            if process.stderr is not None:
                streams.append(process.stderr)
            return process

        self._open_process_pidfd = fail_open
        subprocess.Popen = tracking_popen
        try:
            with tempfile.TemporaryDirectory() as tmp:
                started = time.monotonic()
                with self.assertRaisesRegex(
                    RuntimeError, "injected harness pidfd bind failure"
                ):
                    self._run_bounded_harness(
                        [sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=Path(tmp),
                        timeout=5,
                    )
                self.assertLess(time.monotonic() - started, 3)
        finally:
            self._open_process_pidfd = original_open
            subprocess.Popen = original_popen

        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].returncode)
        self.assertEqual(len(streams), 2)
        self.assertTrue(all(getattr(stream, "closed", False) for stream in streams))

    def test_pidfd_open_failure_immediately_after_popen_cleans_root(self) -> None:
        original_pidfd_open = os.pidfd_open
        original_popen = subprocess.Popen
        original_read_identity = self._read_proc_identity
        launched: list[subprocess.Popen[str]] = []
        ready_record: Path | None = None

        def fail_pidfd_open(_pid: int, _flags: int) -> int:
            if ready_record is not None:
                ready_deadline = time.monotonic() + 1
                while time.monotonic() < ready_deadline and not ready_record.exists():
                    time.sleep(0.01)
            raise OSError(errno.EMFILE, "injected pidfd_open after Popen failure")

        def tracking_popen(*args: object, **kwargs: object) -> subprocess.Popen[str]:
            process = original_popen(*args, **kwargs)
            launched.append(process)
            return process

        def fail_root_identity(process_id: int) -> tuple[int, int, int, int]:
            if launched and process_id == launched[0].pid:
                raise OSError(errno.EIO, "injected persistent root identity read failure")
            return original_read_identity(process_id)

        os.pidfd_open = fail_pidfd_open
        subprocess.Popen = tracking_popen
        self._read_proc_identity = fail_root_identity
        try:
            with tempfile.TemporaryDirectory() as tmp:
                hidden_record = Path(tmp) / "hidden.pid"
                ready_record = hidden_record
                with self.assertRaisesRegex(OSError, "pidfd_open after Popen failure"):
                    self._run_bounded_harness(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import os,signal,sys,time; "
                                "child=os.fork(); "
                                "os.setsid() if child == 0 else None; "
                                "signal.signal(signal.SIGTERM, signal.SIG_IGN) if child == 0 else None; "
                                "(lambda f: (f.write(str(os.getpid())), f.close()))(open(sys.argv[1], 'w')) if child == 0 else None; "
                                "[time.sleep(0.01) for _ in range(100) if not os.path.exists(sys.argv[1])] if child != 0 else None; "
                                "time.sleep(60)"
                            ),
                            str(hidden_record),
                            ],
                            cwd=Path(tmp),
                            timeout=2,
                        )
                self.assertTrue(hidden_record.exists())
                hidden_pid = int(hidden_record.read_text(encoding="ascii"))
                self.assertIsNone(self._process_state(hidden_pid))
        finally:
            os.pidfd_open = original_pidfd_open
            subprocess.Popen = original_popen
            self._read_proc_identity = original_read_identity
            cleanup_errors: list[BaseException] = []
            for process in launched:
                cleanup_errors.extend(self._cleanup_launched_process(process))
            if cleanup_errors:
                primary = sys.exc_info()[1]
                detail = "; ".join(str(error) for error in cleanup_errors[:4])
                if primary is not None:
                    primary.add_note(f"test root cleanup failed: {detail}")
                else:
                    self.fail(f"test root cleanup failed: {detail}")
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].returncode)

    def test_cleanup_launched_process_retains_popen_authority_after_waitpid_eio(self) -> None:
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        original_waitpid = os.waitpid
        calls = [0]

        def fail_once(process_id: int, options: int) -> tuple[int, int]:
            if process_id == process.pid and calls[0] == 0:
                calls[0] += 1
                raise OSError(errno.EIO, "injected initial root waitpid EIO")
            return original_waitpid(process_id, options)

        os.waitpid = fail_once
        cleanup_returncode: int | None = None
        try:
            errors = self._cleanup_launched_process(process)
            cleanup_returncode = process.returncode
        finally:
            os.waitpid = original_waitpid
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
        self.assertEqual(calls[0], 1)
        self.assertIsNotNone(cleanup_returncode)
        self.assertTrue(
            any("injected initial root waitpid EIO" in str(error) for error in errors)
        )

    def test_pidfd_binding_rejects_stale_identity_and_foreign_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / "foreign.pid"
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
            )
            process_pidfd = os.pidfd_open(process.pid, 0)
            try:
                identity = self._read_proc_identity(process.pid)
                record.write_text(
                    f"{process.pid}:{identity[0] + 1}:{identity[1]}:{identity[2]}:{identity[3]}\n",
                    encoding="ascii",
                )
                self.assertIsNone(
                    self._open_owned_pidfd(record, expected_parent_pid=process.pid)
                )
                self.assertFalse(self._pidfd_exited(process_pidfd, timeout=0.05))

                record.write_text(
                    f"{process.pid}:{identity[0]}:{identity[1]}:{identity[2]}:{identity[3]}\n",
                    encoding="ascii",
                )
                self.assertIsNone(
                    self._open_owned_pidfd(record, expected_parent_pid=process.pid)
                )
                self.assertFalse(self._pidfd_exited(process_pidfd, timeout=0.05))
            finally:
                self._signal_pidfd(process_pidfd, signal.SIGTERM)
                process.wait(timeout=2)
                os.close(process_pidfd)

    def test_rpm_nested_timeout_descendant_is_killed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run_lifecycle_watchdog_harness(root, nested_timeout=True)
            self._assert_pidfd_record_exited(
                root / "nested-timeout.pid",
                timeout=2,
                message="watchdog left nested GNU timeout descendant alive",
            )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rpm_malformed_fd9_enters_bounded_outer_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff = root / "malformed-handoff"
            handoff.write_bytes(b"not-a-lifecycle-token\n")
            result = self._run_lifecycle_watchdog_harness(
                root,
                handoff_file=handoff,
            )

        self.assertIn(result.returncode, {-signal.SIGKILL, 124, 137, 143})

    def test_rpm_large_nul_fd9_enters_bounded_outer_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handoff = root / "large-nul-handoff"
            handoff.write_bytes(b"\0" * (1024 * 1024))
            result = self._run_lifecycle_watchdog_harness(
                root,
                handoff_file=handoff,
            )

        self.assertIn(result.returncode, {-signal.SIGKILL, 124, 137, 143})

    def test_rpm_entrypoint_blocks_shell_startup_files(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")
        self.assertEqual(
            source.splitlines()[0],
            "#!/usr/bin/env -S BASH_ENV= ENV= /bin/bash --noprofile --norc -p",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            startup = root / "startup.sh"
            startup.write_text("exit 77\n", encoding="utf-8")
            probe = root / "probe.sh"
            probe.write_text(
                source.splitlines()[0] + "\n" + "printf 'probe\\n'\n",
                encoding="utf-8",
            )
            probe.chmod(0o700)
            environment = os.environ.copy()
            environment["BASH_ENV"] = str(startup)
            environment["ENV"] = str(startup)
            result = self._run_bounded_harness(
                [str(probe)],
                cwd=root,
                timeout=3,
                env=environment,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "probe\n")

    def test_rpm_privileged_watchdog_ignores_function_and_option_injection(self) -> None:
        environment = os.environ.copy()
        injected = "() { printf 'injected\\n' >&2; exit 77; }"
        for name in (
            "BASH_FUNC_exec%%",
            "BASH_FUNC_command%%",
            "BASH_FUNC_timeout%%",
        ):
            environment[name] = injected
        environment["SHELLOPTS"] = "errexit:nounset:xtrace"
        environment["BASHOPTS"] = "expand_aliases:extdebug"
        environment["BASH_ENV"] = str(Path(tempfile.gettempdir()) / "missing-rpm-startup")
        environment["ENV"] = environment["BASH_ENV"]

        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_lifecycle_watchdog_harness(
                Path(tmp),
                environment=environment,
                function_probe=True,
            )

        self.assertIn(result.returncode, {124, 137, 143})
        self.assertIn("function-probe-ok\n", result.stdout)
        self.assertNotIn("injected", result.stderr)
        self.assertNotIn("+ ", result.stderr)

    def test_rpm_lifecycle_handoff_ignores_caller_marker_and_kills_descendant(self) -> None:
        environment = os.environ.copy()
        environment["RPM_LIFECYCLE_WATCHDOG_ACTIVE"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run_lifecycle_watchdog_harness(
                root,
                descendant=True,
                environment=environment,
            )
            self._assert_pidfd_record_exited(
                root / "descendant.pid",
                timeout=2,
                message="watchdog left normal process-group descendant alive",
            )

        self.assertIn(result.returncode, {-signal.SIGKILL, 124, 137, 143})

    def test_rpmbuild_setup_passes_inherited_procfd_tree_and_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run_rpmbuild_setup_harness(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            argv = (root / "rpmbuild-argv").read_text(encoding="utf-8")
            self.assertIn("_topdir /proc/self/fd/", argv)
            self.assertIn("_builddir /proc/self/fd/", argv)
            self.assertIn("_buildrootdir /proc/self/fd/", argv)
            self.assertIn("_rpmdir /proc/self/fd/", argv)
            self.assertIn("_sourcedir /proc/self/fd/", argv)
            self.assertIn("_specdir /proc/self/fd/", argv)
            self.assertIn("_srcrpmdir /proc/self/fd/", argv)
            self.assertIn("_tmppath /proc/self/fd/", argv)
            self.assertRegex(argv, r"-ba\n/proc/self/fd/\d+\n")

    def test_rpmbuild_setup_rejects_subpath_symlinks(self) -> None:
        for symlink in ("SOURCES", "spec"):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self._run_rpmbuild_setup_harness(root, symlink=symlink)

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((root / "rpmbuild-argv").exists())
                if symlink == "SOURCES":
                    self.assertIn("RPM SOURCES directory is not a directory", result.stderr)
                else:
                    self.assertIn("RPM spec is not a private regular file", result.stderr)

    def test_rpm_spec_rewrite_uses_bound_fds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = self._run_spec_rewrite_harness(root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "Version:        1.2.3",
                (root / "SPECS" / "speed-of-cinnamon.spec").read_text(encoding="utf-8"),
            )

    def test_rpm_spec_rewrite_rejects_external_symlinks(self) -> None:
        for symlink in ("spec", "parent"):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = self._run_spec_rewrite_harness(root, symlink=symlink)

                self.assertNotEqual(result.returncode, 0)
                if symlink == "spec":
                    self.assertTrue((root / "SPECS" / "speed-of-cinnamon.spec").is_symlink())
                    self.assertIn(
                        "Version:        external",
                        (root / "external.spec").read_text(encoding="utf-8"),
                    )
                else:
                    self.assertTrue((root / "SPECS").is_symlink())
                    self.assertIn(
                        "Version:        external",
                        (root / "external-SPECS" / "speed-of-cinnamon.spec").read_text(encoding="utf-8"),
                    )

    def test_rpm_cleanup_uses_one_absolute_deadline_for_safe_fs_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            result = self._run_outer_cleanup_harness(Path(tmp) / "blocked", "blocked")
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("RPM stage cleanup failed", result.stderr)
            self.assertLessEqual(elapsed, 5.0)
            log = (Path(tmp) / "blocked" / "safe-fs.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(log), 1)
            self.assertTrue((Path(tmp) / "blocked" / "stage").exists())
            self.assertTrue((Path(tmp) / "blocked" / "workspace").exists())

    def test_rpm_output_enumeration_precedes_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output-order"
            process = self._run_post_commit_output_harness(root)

            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(
                process.stdout,
                f"{root / 'final' / 'RPMS' / 'z.rpm'}\n"
                f"{root / 'final' / 'SRPMS' / 'a.src.rpm'}\n"
                "status=0\n",
            )
            self.assertTrue((root / "cleanup-ran").exists())

    def test_rpm_output_enumeration_watchdog_releases_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output-blocked"
            started = time.monotonic()
            process = self._run_post_commit_output_harness(root, block_scandir=True)
            elapsed = time.monotonic() - started

            self.assertIn(process.returncode, {124, 137})
            self.assertRegex(process.stdout, r"status=(?:124|137)\n")
            self.assertLess(elapsed, 4)
            self.assertTrue((root / "cleanup-ran").exists())

    def test_rpm_output_enumeration_limits_entries_and_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            overflow = self._run_post_commit_output_harness(
                Path(tmp) / "entry-overflow", extra_entries=256
            )
            byte_overflow = self._run_post_commit_output_harness(
                Path(tmp) / "byte-overflow", output_byte_limit=1
            )

        self.assertEqual(overflow.returncode, 1, overflow.stderr)
        self.assertIn("status=1\n", overflow.stdout)
        self.assertIn("RPM output enumeration exceeds max 256 entries", overflow.stderr)
        self.assertEqual(byte_overflow.returncode, 1, byte_overflow.stderr)
        self.assertIn("status=1\n", byte_overflow.stdout)
        self.assertIn("RPM output enumeration exceeds max 1 bytes", byte_overflow.stderr)

    def test_rpm_output_enumeration_rejects_final_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output-symlink"
            process = self._run_post_commit_output_harness(
                root,
                final_symlink=True,
                propagate_output_status=True,
            )

        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(process.stdout, "status=1\n")
        self.assertIn("RPM final build directory changed during output enumeration", process.stderr)

    def test_rpm_output_enumeration_rejects_final_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output-replacement"
            process = self._run_post_commit_output_harness(
                root,
                final_replacement=True,
                propagate_output_status=True,
            )

        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(process.stdout, "status=1\n")
        self.assertIn("RPM final build directory changed during output enumeration", process.stderr)

    def test_rpm_output_enumeration_rejects_root_replacement_during_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output-root-race"
            process = self._run_post_commit_output_harness(
                root,
                output_root_race=True,
                propagate_output_status=True,
            )
            self.assertTrue((root / "final" / "RPMS.output-race").exists())

        self.assertEqual(process.returncode, 1, process.stderr)
        self.assertEqual(process.stdout.splitlines()[-1], "status=1")
        self.assertIn("RPM output root changed during output enumeration", process.stderr)

    def test_rpm_output_rejects_root_replacement_before_first_open(self) -> None:
        for root_name in ("RPMS", "SRPMS"):
            with self.subTest(root_name=root_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / root_name.lower()
                process = self._run_post_commit_output_harness(
                    root,
                    output_root_preopen_race=root_name,
                    propagate_output_status=True,
                )

                self.assertEqual(process.returncode, 1, process.stderr)
                self.assertEqual(process.stdout, "status=1\n")
                self.assertNotIn("attacker.rpm", process.stdout)
                self.assertIn("RPM output root changed during output enumeration", process.stderr)

    def test_rpm_same_uid_output_leaf_race_is_accepted_after_final_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "output-leaf-race"
            process = self._run_post_commit_output_harness(root, leaf_race="replacement")
            self.assertTrue((root / "final" / "RPMS" / "z.rpm.race-old").exists())

        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn(f"{root / 'final' / 'RPMS' / 'z.rpm'}\n", process.stdout)
        self.assertIn(
            "same-uid content mutation or leaf swap after final check cannot be authenticated",
            BUILD_RPM.read_text(encoding="utf-8").lower(),
        )

    def test_rpm_cleanup_post_io_clock_failure_latches_and_keeps_primary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clock-post-blocked"
            started = time.monotonic()
            result = self._run_outer_cleanup_harness(root, "clock-post-blocked")
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 7, result.stderr)
            self.assertLessEqual(elapsed, 5.0)
            log = (root / "safe-fs.log").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(log), 1)
            self.assertEqual((root / "clock-state").read_text(encoding="utf-8"), "3\n")
            self.assertIn("RPM stage cleanup failed after safe-FS invocation; safe-FS residue report is authoritative", result.stderr)
            self.assertIn("RPM workspace cleanup skipped because stage cleanup is unconfirmed", result.stderr)
            self.assertTrue((root / "stage").exists())
            self.assertTrue((root / "workspace").exists())

    def test_rpm_cleanup_skips_parent_after_missing_stage_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stage-unverified"
            result = self._run_outer_cleanup_harness(root, "stage-unverified")
            stage = root / "stage"
            workspace = root / "workspace"

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(
                result.stderr.splitlines(),
                [
                    f"refusing RPM stage cleanup without verified identity: {stage}",
                    "RPM workspace cleanup skipped because stage cleanup is unconfirmed; "
                    f"residue path retained: {workspace}",
                ],
            )
            self.assertFalse((root / "safe-fs.log").exists())
            self.assertTrue(stage.exists())
            self.assertTrue(workspace.exists())

    def test_rpm_cleanup_skips_parent_after_stage_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "stage-failed"
            result = self._run_outer_cleanup_harness(root, "stage-failed")
            stage = root / "stage"
            workspace = root / "workspace"
            log = (root / "safe-fs.log").read_text(encoding="utf-8").splitlines()

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(len(log), 1)
            self.assertEqual(
                result.stderr.splitlines(),
                [
                    "RPM stage cleanup failed after safe-FS invocation; safe-FS residue report is "
                    f"authoritative: {stage}",
                    "RPM workspace cleanup skipped because stage cleanup is unconfirmed; "
                    f"residue path retained: {workspace}",
                ],
            )
            self.assertTrue(stage.exists())
            self.assertTrue(workspace.exists())

    def test_rpm_cleanup_refuses_unverified_workspace_without_fs_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "unverified"
            result = self._run_outer_cleanup_harness(root, "unverified")

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn("without verified identity", result.stderr)
            self.assertFalse((root / "safe-fs.log").exists())
            self.assertTrue((root / "workspace").exists())

    def test_rpm_cleanup_preserves_primary_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_outer_cleanup_harness(Path(tmp) / "primary", "primary")

            self.assertEqual(result.returncode, 7, result.stderr)

    def test_rpm_cleanup_clock_probe_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clock-blocked"
            started = time.monotonic()
            result = self._run_outer_cleanup_harness(root, "clock-blocked")
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertLess(elapsed, 3)
            self.assertIn("RPM cleanup monotonic clock is unavailable", result.stderr)
            self.assertNotIn("safe-FS residue report is authoritative", result.stderr)
            stage = root / "stage"
            workspace = root / "workspace"
            self.assertEqual(
                [
                    line
                    for line in result.stderr.splitlines()
                    if "stage cleanup not attempted" in line or "workspace cleanup skipped" in line
                ],
                [
                    "RPM stage cleanup not attempted; safe-FS was not invoked; residue path retained: "
                    f"{stage}",
                    "RPM workspace cleanup skipped because stage cleanup is unconfirmed; "
                    f"residue path retained: {workspace}",
                ],
            )
            self.assertTrue((root / "stage").exists())
            self.assertTrue((root / "workspace").exists())

    def test_rpm_cleanup_clock_failure_latches_deadline_before_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clock-recovers"
            started = time.monotonic()
            result = self._run_outer_cleanup_harness(root, "clock-recovers")
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertLess(elapsed, 3)
            self.assertIn("RPM cleanup deadline exhausted before safe-FS call", result.stderr)
            self.assertEqual((root / "clock-state").read_text(encoding="utf-8"), "2\n")
            self.assertFalse((root / "safe-fs.log").exists())

    def test_rpm_cleanup_reserves_delayed_initial_clock_probe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "clock-delayed"
            started = time.monotonic()
            result = self._run_outer_cleanup_harness(root, "clock-delayed")
            elapsed = time.monotonic() - started

            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertLess(elapsed, 3)
            self.assertIn("RPM cleanup deadline exhausted before safe-FS call", result.stderr)
            self.assertEqual((root / "clock-state").read_text(encoding="utf-8"), "2\n")
            self.assertTrue((root / "clock-base").exists())
            self.assertFalse((root / "safe-fs.log").exists())

    def test_post_commit_cleanup_is_bounded_and_not_retried_after_deadline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_post_commit_cleanup_harness(Path(tmp))
            log = (Path(tmp) / "safe-fs.log").read_text(encoding="utf-8").splitlines()

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(log), 1)
            self.assertIn("stage cleanup failed after commit", result.stderr)
            self.assertIn("cleanup-status=0", result.stdout)
            self.assertTrue((Path(tmp) / "stage").exists())

    def test_rpm_outer_inline_finalizer_timeout_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run_outer_inline_finalizer_harness(Path(tmp))

        self.assertIn(result.returncode, {124, 125, 137})
        self.assertRegex(result.stdout, r"status=12[4-5]\n")

    def test_rpm_outer_inline_finalizer_watchdog_bounds_blocking_scandir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            result = self._run_outer_inline_finalizer_harness(
                Path(tmp), block_scandir=True
            )

        self.assertIn(result.returncode, {124, 125, 137})
        self.assertRegex(result.stdout, r"status=(?:124|137)\n")
        self.assertLess(time.monotonic() - started, 4)

    def _write_safe_fs_wrapper(self, path: Path, *, real: Path, failure: str, target: Path) -> None:
        path.write_text(
            "import os, sys\n"
            f"real = {str(real)!r}\n"
            f"target = {str(target)!r}\n"
            "args = sys.argv[1:]\n"
            f"if args and args[0] == {failure!r} and len(args) > 2 and args[2] == target:\n"
            "    raise SystemExit(77)\n"
            "os.execv(sys.executable, [sys.executable, real, *args])\n",
            encoding="utf-8",
        )

    def _write_strict_safe_fs_wrapper(
        self,
        path: Path,
        *,
        real: Path,
        log: Path,
        failure_target: Path | None = None,
    ) -> None:
        failure = (
            f"    if len(args) > 2 and args[2] == {str(failure_target)!r}:\n"
            "        raise SystemExit('injected strict safe-FS failure')\n"
            if failure_target is not None
            else ""
        )
        path.write_text(
            "import os, sys\n"
            "from pathlib import Path\n"
            f"real = {str(real)!r}\n"
            f"log = Path({str(log)!r})\n"
            "args = sys.argv[1:]\n"
            "with log.open('a', encoding='utf-8') as handle:\n"
            "    handle.write(repr(args) + '\\n')\n"
            "if args and args[0] == 'remove':\n"
            "    if args.count('--kind') != 1 or args[args.index('--kind') + 1] != 'dir':\n"
            "        raise SystemExit('remove missing exact --kind dir')\n"
            + failure
            + "os.execv(sys.executable, [sys.executable, real, *args])\n",
            encoding="utf-8",
        )
        path.chmod(0o700)

    def _write_replacing_safe_fs_wrapper(self, path: Path, *, real: Path, target: Path) -> None:
        path.write_text(
            "import os, sys\n"
            "from pathlib import Path\n"
            f"real = {str(real)!r}\n"
            f"target = Path({str(target)!r})\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'install-tree':\n"
            "    os.rename(target, target.with_name(target.name + '.replaced'))\n"
            "    target.mkdir(mode=0o700)\n"
            "    (target / 'payload.txt').write_text('replacement\\n', encoding='utf-8')\n"
            "os.execv(sys.executable, [sys.executable, real, *args])\n",
            encoding="utf-8",
        )

    def _write_mutating_safe_fs_wrapper(self, path: Path, *, real: Path, target: Path) -> None:
        path.write_text(
            "import os, subprocess, sys\n"
            "from pathlib import Path\n"
            f"real = {str(real)!r}\n"
            f"target = Path({str(target)!r})\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'install-tree':\n"
            "    subprocess.run([sys.executable, real, *args], check=True)\n"
            "    (target / 'payload.txt').write_text('mutated\\n', encoding='utf-8')\n"
            "    raise SystemExit(0)\n"
            "os.execv(sys.executable, [sys.executable, real, *args])\n",
            encoding="utf-8",
        )

    def _write_renaming_safe_fs_wrapper(self, path: Path, *, real: Path, target: Path) -> None:
        path.write_text(
            "import os, sys\n"
            "from pathlib import Path\n"
            f"real = {str(real)!r}\n"
            f"target = Path({str(target)!r})\n"
            "args = sys.argv[1:]\n"
            "if args and args[0] == 'remove' and len(args) > 2 and args[2] == str(target):\n"
            "    target.rename(target.with_name(target.name + '.residue'))\n"
            "    raise SystemExit(77)\n"
            "os.execv(sys.executable, [sys.executable, real, *args])\n",
            encoding="utf-8",
        )

    def _inject_pinned_rename_failure(self, program: str, expression: str) -> str:
        marker = "def rename_pinned(source_fd, source_name, target_fd, target_name, *, flags, action):\n"
        injection = (
            marker
            + f"            if {expression}:\n"
            "                raise OSError('injected pinned rename failure')\n"
        )
        return program.replace(marker, injection, 1)

    def _inject_parent_replacement_before_first_publication(self, program: str) -> str:
        # Parent replacement must happen after candidate tuple validation so
        # pinned rename can prove it does not publish through the new parent.
        tuple_call = (
            "                revalidate_output_tuple(\n"
            "                    publish_fd,\n"
            "                    publish_path,\n"
            "                    candidate_final_tuple,\n"
            "                    \"RPM publish candidate before first publication\",\n"
            "                )\n"
        )
        replacement = (
            tuple_call
            + "                os.rename(lock_parent, lock_parent + '.replaced')\n"
            + "                os.mkdir(lock_parent, 0o700)\n"
            + "                rename_pinned(\n"
        )
        return program.replace(tuple_call + "                rename_pinned(\n", replacement, 1)

    def _inject_candidate_replacement_before_publication(self, program: str, *, exchange: bool) -> str:
        phase = "exchange" if exchange else "first publication"
        tuple_call = (
            "                revalidate_output_tuple(\n"
            "                    publish_fd,\n"
            "                    publish_path,\n"
            "                    candidate_final_tuple,\n"
            f'                    "RPM publish candidate before {phase}",\n'
            "                )\n"
        )
        marker = f'                revalidate_parent("RPM finalization parent before {phase}")\n' + tuple_call
        replacement = (
            f'                revalidate_parent("RPM finalization parent before {phase}")\n'
            + tuple_call
            + "                os.rename(publish_path, publish_path + \".replaced\")\n"
            + "                os.mkdir(publish_path, 0o700)\n"
            + '                with open(os.path.join(publish_path, "payload.txt"), "w", encoding="utf-8") as handle:\n'
            + '                    handle.write("attacker\\n")\n'
            + "                rename_pinned(\n"
        )
        return program.replace(marker + "                rename_pinned(\n", replacement, 1)

    def _inject_candidate_root_replacement_before_publication(self, program: str) -> str:
        tuple_call = (
            "                revalidate_output_tuple(\n"
            "                    publish_fd,\n"
            "                    publish_path,\n"
            "                    candidate_final_tuple,\n"
            '                    "RPM publish candidate before first publication",\n'
            "                )\n"
        )
        marker = (
            '                revalidate_parent("RPM finalization parent before first publication")\n'
            + tuple_call
            + "                rename_pinned(\n"
        )
        replacement = (
            '                revalidate_parent("RPM finalization parent before first publication")\n'
            + tuple_call
            + '                os.rename(os.path.join(publish_path, "RPMS"), os.path.join(publish_path, "RPMS.replaced"))\n'
            + '                os.mkdir(os.path.join(publish_path, "RPMS"), 0o700)\n'
            + '                with open(os.path.join(publish_path, "RPMS", "attacker.rpm"), "w", encoding="utf-8") as handle:\n'
            + '                    handle.write("attacker\\n")\n'
            + "                rename_pinned(\n"
        )
        return program.replace(marker, replacement, 1)

    def _inject_quarantine_name_collision(self, program: str) -> str:
        marker = (
            "                published_final_identity = identity_text(os.fstat(final_fd))\n"
            "                published_final_tuple = (\n"
            "                    published_final_identity,\n"
            "                    expected_output_root_identities[\"RPMS\"],\n"
            "                    expected_output_root_identities[\"SRPMS\"],\n"
            "                )\n"
            "                if published_final_tuple != candidate_final_tuple:\n"
        )
        replacement = (
            "                published_final_identity = identity_text(os.fstat(final_fd))\n"
            "                published_final_tuple = (\n"
            "                    published_final_identity,\n"
            "                    expected_output_root_identities[\"RPMS\"],\n"
            "                    expected_output_root_identities[\"SRPMS\"],\n"
            "                )\n"
            "                for occupied_name in (recovery_name, f\"{recovery_name}.candidate-mismatch\"):\n"
            "                    os.mkdir(occupied_name, 0o700, dir_fd=parent_fd)\n"
            "                if published_final_tuple != candidate_final_tuple:\n"
        )
        return program.replace(marker, replacement, 1)

    def _inject_late_final_replacement(self, program: str) -> str:
        marker = '            revalidate_parent("RPM finalization parent before completion")\n'
        replacement = (
            marker +
            '            os.rename(final_path, final_path + ".late-old")\n'
            '            os.mkdir(final_path, 0o700)\n'
            '            with open(os.path.join(final_path, "payload.txt"), "w", encoding="utf-8") as handle:\n'
            '                handle.write("attacker\\n")\n'
        )
        return program.replace(marker, replacement, 1)

    def _inject_rollback_final_replacement(self, program: str) -> str:
        marker = '                sync_directory(workspace_fd, "RPM exchange rollback workspace")\n'
        replacement = (
            marker +
            '                os.rename(final_path, final_path + ".rollback-old")\n'
            '                os.mkdir(final_path, 0o700)\n'
            '                with open(os.path.join(final_path, "payload.txt"), "w", encoding="utf-8") as handle:\n'
            '                    handle.write("attacker\\n")\n'
        )
        return program.replace(marker, replacement, 1)

    def _inject_rollback_final_replacement_after_revalidation(self, program: str) -> str:
        marker = (
            "                        )\n"
            "                        if rolled_back:\n"
        )
        replacement = (
            "                        )\n"
            "                        if rolled_back:\n"
            '                            os.rename(final_path, final_path + ".rollback-late-old")\n'
            "                            os.mkdir(final_path, 0o700)\n"
            '                            with open(os.path.join(final_path, "payload.txt"), "w", encoding="utf-8") as handle:\n'
            '                                handle.write("attacker\\n")\n'
        )
        return program.replace(marker, replacement, 1)

    def _inject_rollback_root_replacement_after_revalidation(self, program: str) -> str:
        marker = (
            "                        )\n"
            "                        if rolled_back:\n"
        )
        replacement = (
            "                        )\n"
            "                        if rolled_back:\n"
            '                            os.rename(os.path.join(final_path, "RPMS"), os.path.join(final_path, "RPMS.rollback-old"))\n'
            '                            os.mkdir(os.path.join(final_path, "RPMS"), 0o700)\n'
            '                            with open(os.path.join(final_path, "RPMS", "attacker.rpm"), "w", encoding="utf-8") as handle:\n'
            '                                handle.write("attacker\\n")\n'
        )
        return program.replace(marker, replacement, 1)

    def _inject_workspace_cleanup_replacement(self, program: str) -> str:
        marker = (
            '            revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, '
            '"RPM publish workspace")\n'
            '            run_safe_fs(\n'
            '                "rmdir",\n'
        )
        replacement = (
            '            os.rename(workspace_path, workspace_path + ".replaced")\n'
            '            os.mkdir(workspace_path, 0o700)\n'
            + marker
        )
        return program.replace(marker, replacement, 1)

    def _run_lifecycle_watchdog_harness(
        self,
        root: Path,
        *,
        descendant: bool = False,
        nested_timeout: bool = False,
        handoff_file: Path | None = None,
        environment: dict[str, str] | None = None,
        function_probe: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        if descendant and nested_timeout:
            raise ValueError("descendant and nested_timeout are mutually exclusive")
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        source = BUILD_RPM.read_text(encoding="utf-8")
        start = source.index("# RPM lifecycle watchdog starts")
        end = source.index("# RPM lifecycle watchdog ends.", start) + len(
            "# RPM lifecycle watchdog ends."
        )
        watchdog = source[start:end]
        watchdog = watchdog.replace(
            "RPM_LIFECYCLE_TIMEOUT_SECONDS=3600", "RPM_LIFECYCLE_TIMEOUT_SECONDS=2", 1
        ).replace(
            "RPM_LIFECYCLE_KILL_AFTER_SECONDS=30", "RPM_LIFECYCLE_KILL_AFTER_SECONDS=1", 1
        )
        script = root / "lifecycle-watchdog.sh"
        shutil.copy2(REPO_ROOT / "scripts" / "rpm-lifecycle-supervisor.py", root / "rpm-lifecycle-supervisor.py")
        descendant_pid: Path | None = None
        if nested_timeout:
            descendant_pid = root / "nested-timeout.pid"
            nested_code = (
                "import os,signal,sys,time; "
                "from pathlib import Path; "
                "raw=Path(f'/proc/{os.getpid()}/stat').read_text(encoding='ascii'); "
                "fields=raw.rsplit(') ', 1)[1].split(); "
                "Path(sys.argv[1]).write_text(':'.join((str(os.getpid()), fields[19], fields[1], fields[2], fields[3]))+'\\n', encoding='ascii'); "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
            )
            lifecycle_body = (
                f"timeout --foreground --signal=TERM --kill-after=1s 60s {sys.executable!r} "
                f"-c {nested_code!r} {str(descendant_pid)!r} &\n"
                "child_pid=$!\n"
                f"for _ in {{1..100}}; do [[ -s {str(descendant_pid)!r} ]] && break; sleep 0.01; done\n"
                f"[[ -s {str(descendant_pid)!r} ]] || exit 78\n"
                "exit 0\n"
            )
        elif descendant:
            descendant_pid = root / "descendant.pid"
            lifecycle_body = (
                "( trap '' TERM; exec sleep 60 ) &\n"
                "child_pid=$!\n"
                f"{sys.executable!r} -I -B - \"$child_pid\" {str(descendant_pid)!r} <<'PY'\n"
                "from pathlib import Path\n"
                "import sys\n"
                "pid=int(sys.argv[1])\n"
                "raw=Path(f'/proc/{pid}/stat').read_text(encoding='ascii')\n"
                "fields=raw.rsplit(') ', 1)[1].split()\n"
                "Path(sys.argv[2]).write_text(':'.join((str(pid), fields[19], fields[1], fields[2], fields[3]))+'\\n', encoding='ascii')\n"
                "PY\n"
                "trap '' TERM\n"
                "wait \"$child_pid\"\n"
            )
        else:
            lifecycle_body = "trap '' TERM\nsleep 60\n"
        if function_probe:
            lifecycle_body = (
                "command -v timeout >/dev/null\n"
                "timeout --foreground --signal=TERM --kill-after=0.1s 1s true\n"
                "printf 'function-probe-ok\\n'\n"
                "trap '' TERM\n"
                "sleep 60\n"
            )
        script.write_text(
            source.splitlines()[0] + "\n"
            "set -euo pipefail\n"
            "umask 077\n"
            f"{watchdog}\n"
            f"{lifecycle_body}",
            encoding="utf-8",
        )
        script.chmod(0o700)
        command = [str(script)]
        if handoff_file is not None:
            launcher = root / "launch-lifecycle-watchdog.sh"
            launcher.write_text(
                "#!/usr/bin/env bash\n"
                f"exec 9< {str(handoff_file)!r}\n"
                f"exec {str(script)!r}\n",
                encoding="utf-8",
            )
            launcher.chmod(0o700)
            command = [str(launcher)]
        started = time.monotonic()
        result = self._run_bounded_harness(
            command,
            cwd=root,
            timeout=8,
            env=environment,
            owned_pid_files=(descendant_pid,) if descendant_pid is not None else (),
        )
        self.assertLess(time.monotonic() - started, 4)
        return result

    def _run_rpmbuild_setup_harness(
        self, root: Path, *, symlink: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        stage = root / "stage"
        workspace = root / "workspace"
        stage.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        for name in ("BUILD", "BUILDROOT", "RPMS", "SOURCES", "SPECS", "SRPMS"):
            (stage / name).mkdir(mode=0o700)
        spec = stage / "SPECS" / "speed-of-cinnamon.spec"
        spec.write_text("Name: speed-of-cinnamon\n", encoding="utf-8")

        if symlink == "SOURCES":
            target = root / "sources-target"
            target.mkdir(mode=0o700)
            (stage / "SOURCES").rmdir()
            (stage / "SOURCES").symlink_to(target, target_is_directory=True)
        elif symlink == "spec":
            target = root / "spec-target"
            target.write_text("Name: replacement\n", encoding="utf-8")
            spec.unlink()
            spec.symlink_to(target)

        record = root / "rpmbuild-argv"
        fake = root / "fake-rpmbuild"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "import sys\n"
            f"record = Path({str(record)!r})\n"
            "for argument in sys.argv[1:]:\n"
            "    for part in argument.split():\n"
            "        if part.startswith('/proc/self/fd/') and not os.path.exists(part):\n"
            "            raise SystemExit(f'closed procfd: {part}')\n"
            "record.write_text('\\n'.join(sys.argv[1:]) + '\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        fake.chmod(0o700)
        stage_stat = stage.stat()
        workspace_stat = workspace.stat()
        stage_identity = f"{stage_stat.st_dev}:{stage_stat.st_ino}:{stage_stat.st_mode}"
        workspace_identity = f"{workspace_stat.st_dev}:{workspace_stat.st_ino}:{workspace_stat.st_mode}"
        return self._run_bounded_harness(
            [
                sys.executable,
                "-",
                str(fake),
                sys.executable,
                str(stage),
                stage_identity,
                str(workspace),
                workspace_identity,
                spec.name,
            ],
            cwd=REPO_ROOT,
            timeout=8,
            input_text=self._rpmbuild_setup_program(),
        )

    def _run_spec_rewrite_harness(
        self,
        root: Path,
        *,
        symlink: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        parent = root / "SPECS"
        parent.mkdir(mode=0o700)
        spec = parent / "speed-of-cinnamon.spec"
        spec.write_text("Name: speed-of-cinnamon\nVersion:        0.0\n", encoding="utf-8")

        if symlink == "spec":
            target = root / "external.spec"
            target.write_text("Name: external\nVersion:        external\n", encoding="utf-8")
            spec.unlink()
            spec.symlink_to(target)
        elif symlink == "parent":
            target = root / "external-SPECS"
            target.mkdir(mode=0o700)
            (target / spec.name).write_text(
                "Name: external\nVersion:        external\n", encoding="utf-8"
            )
            spec.unlink()
            parent.rmdir()
            parent.symlink_to(target, target_is_directory=True)

        return self._run_bounded_harness(
            [sys.executable, "-I", "-B", "-", str(spec), "1.2.3"],
            cwd=REPO_ROOT,
            timeout=8,
            env=environment,
            input_text=self._spec_rewrite_program(),
        )

    def test_build_rpm_cleanup_requires_expected_identity(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn(
            'rpmbuild_tmpdir_identity="$(bounded_safe_fs_identity "${rpmbuild_tmpdir}" dir)"',
            source,
        )
        self.assertIn(
            'stage_topdir_identity="$(bounded_safe_fs_identity "${stage_topdir}" dir)"',
            source,
        )
        self.assertIn('--expected-identity "${rpmbuild_tmpdir_identity}"', source)
        self.assertIn('--expected-identity "${stage_topdir_identity}"', source)

    def test_project_and_spec_reads_are_bounded(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn("MAX_PROJECT_METADATA_BYTES = 1 << 20", source)
        self.assertIn("MAX_RPM_SPEC_BYTES = 1 << 20", source)
        self.assertIn("handle.read(MAX_PROJECT_METADATA_BYTES + 1)", source)
        self.assertIn("handle.read(MAX_RPM_SPEC_BYTES + 1)", source)
        self.assertIn("pyproject.toml project.version is invalid", source)
        self.assertIn("RecursionError, MemoryError", source)
        self.assertIn("parent_fd = os.open(\n        spec_path.parent", source)
        self.assertIn("os.fdopen(spec_fd, \"rb\", closefd=True)", source)
        self.assertIn("os.stat(spec_path.name, dir_fd=parent_fd, follow_symlinks=False)", source)
        self.assertIn("os.replace(tmp_name, spec_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)", source)
        self.assertIn('require_parent_identity("before replace")', source)
        self.assertIn('require_parent_identity("after replace")', source)

    def test_trusted_python_invocations_are_isolated_and_resolved(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn('lifecycle_python="$(command -v -- python3 || true)"', source)
        self.assertNotIn("python3 -", source)
        self.assertIn('safe_fs_cmd=("${lifecycle_python}" -I -B "${safe_fs}")', source)
        self.assertIn('cleanup_clock_command=("${lifecycle_python}" -I -B)', source)
        self.assertIn(
            '"${lifecycle_python}" -I -B - "${rpm_tmp_parent}" "${safe_fs}"',
            source,
        )
        python_invocations = [
            line for line in source.splitlines() if '"${python_bin}"' in line
        ]
        self.assertGreaterEqual(len(python_invocations), 6)
        self.assertTrue(all(' -I -B ' in line for line in python_invocations))
        self.assertIn('safe_fs_base = [sys.executable, "-I", "-B", safe_fs]', source)
        self.assertIn(
            'identity_helper_base = [sys.executable, "-I", "-B", lifecycle_supervisor]',
            source,
        )

    def test_python_startup_environment_cannot_inject_sitecustomize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            marker = root / "sitecustomize-ran"
            sitecustomize = root / "sitecustomize.py"
            sitecustomize.write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n"
                "raise SystemExit('sitecustomize executed')\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment.update(
                {
                    "PYTHONPATH": str(root),
                    "PYTHONHOME": str(root / "not-a-python-home"),
                    "PYTHONSTARTUP": str(sitecustomize),
                    "PYTHONINSPECT": "1",
                    "PYTHONWARNINGS": "error",
                }
            )
            result = self._run_spec_rewrite_harness(root / "rewrite", environment=environment)
            rewritten_spec = (
                root / "rewrite" / "SPECS" / "speed-of-cinnamon.spec"
            ).read_text(encoding="utf-8")
            sitecustomize_ran = marker.exists()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("sitecustomize executed", result.stderr)
        self.assertFalse(sitecustomize_ran)
        self.assertIn("Version:        1.2.3", rewritten_spec)

    def test_rpm_build_and_finalization_are_bounded(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn("RPM_LIFECYCLE_TIMEOUT_SECONDS=3600", source)
        self.assertIn("RPM_LIFECYCLE_KILL_AFTER_SECONDS=30", source)
        self.assertIn("RPM_LIFECYCLE_HANDOFF_FD=9", source)
        self.assertIn("same-UID callers can forge inherited FDs, an accepted risk", source)
        self.assertNotIn("RPM_LIFECYCLE_WATCHDOG_ACTIVE", source)
        self.assertNotIn("export RPM_LIFECYCLE", source)
        self.assertNotIn("timeout --signal", source)
        self.assertNotIn("read -r -t", source)
        self.assertIn("--check-handoff-fd", source)
        self.assertIn("--handoff-max-bytes=256", source)
        self.assertIn('lifecycle_supervisor="${lifecycle_script_dir}/rpm-lifecycle-supervisor.py"', source)
        self.assertIn('exec "${lifecycle_python}" -I -B "${lifecycle_supervisor}"', source)
        self.assertIn('--timeout="${RPM_LIFECYCLE_TIMEOUT_SECONDS}"', source)
        self.assertIn('--kill-after="${RPM_LIFECYCLE_KILL_AFTER_SECONDS}"', source)
        self.assertIn('--handoff-fd="${RPM_LIFECYCLE_HANDOFF_FD}"', source)
        self.assertIn('-- /bin/bash --noprofile --norc -p', source)
        self.assertTrue(
            source.startswith(
                '#!/usr/bin/env -S BASH_ENV= ENV= /bin/bash --noprofile --norc -p\n'
            )
        )
        self.assertLess(
            source.index("# RPM lifecycle watchdog starts"),
            source.index("repo_dir="),
        )
        self.assertIn("RPM_BUILD_TIMEOUT_SECONDS=3600", source)
        self.assertIn("RPM_FINALIZE_TIMEOUT_SECONDS=120", source)
        self.assertIn("RPM_FINALIZE_LOCK_TIMEOUT_SECONDS=30", source)
        self.assertIn("RPM_FINALIZE_KILL_AFTER_SECONDS=2", source)
        self.assertIn("RPM_FINALIZE_TIMEOUT_SECONDS <= 0", source)
        self.assertIn("RPM_STARTUP_SWEEP_TIMEOUT_SECONDS <= 0", source)
        self.assertIn("RPM_OUTPUT_TIMEOUT_SECONDS <= 0", source)
        self.assertIn('local staging_identity=$7', source)
        self.assertIn('local lifecycle_supervisor_path=$8', source)
        self.assertIn('require_cmd timeout', source)
        self.assertIn(
            'timeout --foreground --signal=TERM --kill-after=30s "${RPM_BUILD_TIMEOUT_SECONDS}s" \\\n  "${python_bin}" -I -B - "${rpmbuild_bin}"',
            source,
        )
        self.assertIn('timeout = remaining_finalize_timeout()', source)
        self.assertIn('"--identity-max-bytes",', source)
        self.assertIn('IDENTITY_PATTERN', (REPO_ROOT / "scripts" / "rpm-lifecycle-supervisor.py").read_text(encoding="utf-8"))
        self.assertIn("global_deadline = time.monotonic() + finalize_timeout_seconds", source)
        self.assertIn("lock_deadline = min(global_deadline, time.monotonic() + lock_timeout_seconds)", source)
        self.assertIn("finalize_deadline = global_deadline", source)
        self.assertIn(
            'timeout --foreground --signal=TERM --kill-after="${RPM_FINALIZE_KILL_AFTER_SECONDS}s"',
            source,
        )
        self.assertIn('primary_error = sys.exc_info()[1]', source)
        self.assertIn("safe_fs_invoked = False", source)
        self.assertIn("global safe_fs_invoked, safe_fs_result", source)
        self.assertIn(
            "def report_cleanup_failure(label, path, operation_invoked, operation_result):",
            source,
        )
        self.assertIn("operation_result is not None", source)
        self.assertIn("fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)", source)
        self.assertIn('os.stat(lock_parent, follow_symlinks=False)', source)
        self.assertIn('revalidate_parent("finalization lock parent after flock")', source)
        self.assertIn('if lock_deadline - time.monotonic() <= 0:', source)
        self.assertIn('bind_directory_fd "${final_topdir}"', source)
        self.assertIn('exec {final_topdir_fd}<&-', source)
        self.assertIn("rename_pinned(", source)
        self.assertIn("flags=2", source)
        self.assertIn("def rollback_exchange_if_needed", source)
        self.assertIn('action="RPM exchange rollback"', source)
        self.assertIn('"RPM exchange rollback final"', source)
        self.assertIn("def quarantine_unexpected_final", source)
        self.assertIn("MAX_QUARANTINE_ATTEMPTS = 16", source)
        self.assertIn("secrets.token_hex(16)", source)
        self.assertIn("capture_output_root_identity", source)
        self.assertIn("expected_output_root_identities", source)
        self.assertIn("final_rpms_identity", source)
        self.assertIn("final_srpms_identity", source)
        self.assertIn('"${final_rpms_identity}"', source)
        self.assertIn('"${final_srpms_identity}"', source)
        self.assertIn("${#finalization_result} > 256", source)
        self.assertNotIn("root_identities[root_name] =", source)
        self.assertIn("trusted_final_tuples", source)
        self.assertIn("candidate_final_tuple", source)
        self.assertIn("def revalidate_output_tuple", source)
        self.assertIn("RPM exchange rollback final after recovery", source)
        self.assertIn('rpmbuild_bin="$(command -v -- rpmbuild)"', source)
        self.assertIn("directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW", source)
        self.assertIn("file_flags = os.O_RDONLY | os.O_NOFOLLOW", source)
        self.assertIn("def open_child_directory(parent_fd, name, path, label):", source)
        self.assertIn("def open_spec_file(specs_fd, name, path):", source)
        self.assertIn("os.set_inheritable(descriptor, True)", source)
        self.assertIn('f"_topdir {procfd(stage_fd)}"', source)
        self.assertIn('f"_builddir {procfd(build_fd)}"', source)
        self.assertIn('f"_buildrootdir {procfd(buildroot_fd)}"', source)
        self.assertIn('f"_rpmdir {procfd(rpms_fd)}"', source)
        self.assertIn('f"_sourcedir {procfd(sources_fd)}"', source)
        self.assertIn('f"_specdir {procfd(specs_fd)}"', source)
        self.assertIn('f"_srcrpmdir {procfd(srpms_fd)}"', source)
        self.assertIn('f"_tmppath {procfd(workspace_fd)}"', source)
        self.assertIn('os.execv(rpmbuild_path, command)', source)
        self.assertIn('procfd(spec_fd)', source)
        self.assertNotIn('stage_topdir_fd_path', source)
        self.assertNotIn('rpmbuild_tmpdir_fd_path', source)
        self.assertNotIn('os.open(lock_name', source)
        self.assertNotIn('lock.fileno()', source)
        self.assertIn("RPM finalization lock timed out", source)

    def test_rpm_primary_error_survives_parent_close_failure(self) -> None:
        program = self._finalize_program()
        close_failure = program.replace(
            "try:\n    parent_stat = os.fstat(parent_fd)",
            "_real_close = os.close\n"
            "def _close_with_failure(fd):\n"
            "    if fd == parent_fd:\n"
            "        raise OSError('injected close failure')\n"
            "    return _real_close(fd)\n"
            "os.close = _close_with_failure\n"
            "try:\n    parent_stat = os.fstat(parent_fd)",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir()
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-close" / "candidate"
            previous = dist / "rpmbuild.previous"
            recovery = dist / ".rpmbuild.previous-recovery-close"
            failing = root / "safe-fs-fail-identity.py"
            self._write_safe_fs_wrapper(failing, real=REPO_ROOT / "scripts" / "safe-local-fs.py", failure="identity", target=publish)
            result = self._run_finalize(
                close_failure,
                failing,
                lock,
                stage,
                final,
                publish,
                previous,
                recovery,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not verify RPM dir identity", result.stderr)
        self.assertNotIn("SystemExit: build-rpm finalization descriptor cleanup failed", result.stderr)

    def test_publish_fd_close_failure_never_retries_reused_descriptor(self) -> None:
        program = self._finalize_program()
        marker = (
            "            descriptor = publish_fd\n"
            "            publish_fd = None\n"
            "            os.close(descriptor)\n"
        )
        replacement = (
            "            import atexit\n"
            "            _publish_close_real = os.close\n"
            "            _publish_close_fd = publish_fd\n"
            "            _replacement_read = -1\n"
            "            def _check_publish_replacement():\n"
            "                if _replacement_read >= 0:\n"
            "                    try:\n"
            "                        os.fstat(_replacement_read)\n"
            "                    except OSError:\n"
            "                        os._exit(92)\n"
            "            atexit.register(_check_publish_replacement)\n"
            "            def _close_publish(fd):\n"
            "                global _replacement_read, _replacement_write\n"
            "                if fd == _publish_close_fd:\n"
            "                    _publish_close_real(fd)\n"
            "                    _replacement_read, _replacement_write = os.pipe()\n"
            "                    if _replacement_read != fd:\n"
            "                        raise RuntimeError('publish descriptor was not reused')\n"
            "                    os.close = _publish_close_real\n"
            "                    raise OSError(errno.EINTR, 'injected publish close EINTR')\n"
            "                return _publish_close_real(fd)\n"
            "            os.close = _close_publish\n"
            + marker
        )
        self.assertIn(marker, program)
        program = program.replace(marker, replacement, 1)
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir(mode=0o700)
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            publish = dist / ".rpmbuild.publish-workspace-close-reuse" / "candidate"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-close-reuse",
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.returncode, 92)
        self.assertIn("injected publish close EINTR", result.stderr)

    def test_embedded_bind_close_failures_keep_primary_and_concrete_diagnostic(self) -> None:
        finalize = self._finalize_program()
        rpmbuild = self._rpmbuild_setup_program()
        for program in (finalize, rpmbuild):
            with self.subTest(program="finalize" if program is finalize else "rpmbuild"):
                self.assertIn("def close_descriptor_once", program)
                self.assertNotIn("except BaseException:\n                pass", program)
                self.assertIn("type(close_error).__name__", program)
        self.assertIn("while bound_fds:", rpmbuild)
        self.assertIn("descriptor = bound_fds.pop()", rpmbuild)

    def test_rpm_publish_is_candidate_exchange_and_recovery_bound(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn('local publish_path=$4', source)
        self.assertIn('local previous_path=$5', source)
        self.assertIn('local previous_recovery_path=$6', source)
        self.assertIn('local staging_identity=$7', source)
        self.assertIn('"install-tree",', source)
        self.assertIn('publish_path,', source)
        self.assertIn("flags=1", source)
        self.assertIn("flags=2", source)
        self.assertIn("atomic pinned rename is not supported", source)
        self.assertNotIn('"exchange",', source)
        self.assertIn('previous_path,', source)
        self.assertIn('previous_recovery_path,', source)
        self.assertIn('previous-recovery-', source)
        self.assertIn('publish-workspace-', source)
        self.assertIn('PUBLISH_WORKSPACE_MAX_AGE_NS', source)
        self.assertIn('RPM previous recovery requires manual recovery', source)
        self.assertIn('unresolved RPM publish workspace', source)
        self.assertIn('preserving RPM publish workspace after exchange', source)
        self.assertIn('publish_workspace=""', source)
        publish_commit = source.index("publish_committed=1\n")
        self.assertLess(
            publish_commit,
            source.index('publish_workspace=""', publish_commit),
        )
        self.assertLess(
            publish_commit,
            source.index('publish_recovery=""', publish_commit),
        )
        self.assertIn('open_bound_directory(staging_path, staging_identity', source)
        self.assertIn('open_named_directory(\n            parent_fd,\n            workspace_name,\n            workspace_path,', source)
        self.assertIn('revalidate_bound_directory(staging_fd', source)
        self.assertIn('revalidate_bound_directory(workspace_fd', source)
        self.assertIn('rpmbuild_bin="$(command -v -- rpmbuild)"', source)
        self.assertIn('os.open(name, directory_flags, dir_fd=parent_fd)', source)
        self.assertIn('os.open(name, file_flags, dir_fd=specs_fd)', source)
        self.assertIn('os.set_inheritable(descriptor, True)', source)
        self.assertIn('f"_topdir {procfd(stage_fd)}"', source)
        self.assertIn('f"_specdir {procfd(specs_fd)}"', source)
        self.assertIn('f"_tmppath {procfd(workspace_fd)}"', source)
        self.assertIn('procfd(spec_fd)', source)
        self.assertIn('os.execv(rpmbuild_path, command)', source)
        self.assertNotIn('stage_topdir_fd_path', source)
        self.assertNotIn('rpmbuild_tmpdir_fd_path', source)
        self.assertNotIn('install-tree", "build-rpm", staging_path, final_path', source)

    def test_rpm_finalizer_remove_calls_require_exact_directory_kind(self) -> None:
        calls = []
        for node in ast.walk(ast.parse(self._finalize_program())):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "run_safe_fs" or not node.args:
                continue
            if isinstance(node.args[0], ast.Constant) and node.args[0].value == "remove":
                calls.append([arg.value if isinstance(arg, ast.Constant) else None for arg in node.args])

        self.assertGreaterEqual(len(calls), 2)
        for arguments in calls:
            self.assertEqual(arguments.count("--kind"), 1)
            kind_index = arguments.index("--kind")
            self.assertEqual(arguments[kind_index + 1], "dir")

    def test_rpm_finalizer_strict_safe_fs_rejects_mutated_file_kind(self) -> None:
        program = self._finalize_program().replace(
            '                "--kind",\n                "dir",\n                "--expected-identity",\n',
            '                "--kind",\n                "file",\n                "--expected-identity",\n',
            1,
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stale = dist / ".rpmbuild.publish-stale"
            stale.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            strict = root / "strict-safe-fs.py"
            log = root / "safe-fs.log"
            self._write_strict_safe_fs_wrapper(strict, real=safe_fs, log=log)
            result = self._run_finalize(
                program,
                strict,
                dist / ".finalize.lock",
                stage,
                dist / "rpmbuild",
                dist / ".rpmbuild.publish-workspace-new" / "candidate",
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-new",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("remove missing exact --kind dir", result.stderr)
            self.assertIn("'file'", log.read_text(encoding="utf-8"))
            self.assertFalse((dist / "rpmbuild").exists())

    def test_rpm_install_tree_fails_closed_on_stage_replacement(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("original\n", encoding="utf-8")
            stage_stat = stage.stat()
            stage_identity = f"{stage_stat.st_dev}:{stage_stat.st_ino}:{stage_stat.st_mode}"
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-stage-replace" / "candidate"
            replacement = root / "safe-fs-replace-stage.py"
            self._write_replacing_safe_fs_wrapper(replacement, real=safe_fs, target=stage)
            result = self._run_finalize(
                program,
                replacement,
                lock,
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-stage-replace",
                stage_identity=stage_identity,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RPM staging directory changed during finalization", result.stderr)
            self.assertFalse(final.exists())
            self.assertEqual((stage / "payload.txt").read_text(encoding="utf-8"), "replacement\n")
            self.assertTrue(stage.with_name("stage.replaced").exists())

    def test_rpm_install_tree_fails_closed_on_workspace_replacement(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("original\n", encoding="utf-8")
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-workspace-replace" / "candidate"
            workspace = publish.parent
            replacement = root / "safe-fs-replace-workspace.py"
            self._write_replacing_safe_fs_wrapper(replacement, real=safe_fs, target=workspace)
            result = self._run_finalize(
                program,
                replacement,
                lock,
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-workspace-replace",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("RPM publish workspace changed during finalization", result.stderr)
            self.assertFalse(final.exists())
            self.assertEqual((workspace / "payload.txt").read_text(encoding="utf-8"), "replacement\n")
            self.assertTrue(workspace.with_name(workspace.name + ".replaced").exists())

    def test_rpm_same_uid_candidate_content_mutation_is_accepted_risk(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("original\n", encoding="utf-8")
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-content-race" / "candidate"
            mutating = root / "safe-fs-mutate-candidate.py"
            self._write_mutating_safe_fs_wrapper(mutating, real=safe_fs, target=publish)

            result = self._run_finalize(
                program,
                mutating,
                lock,
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-content-race",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "mutated\n")

    def test_rpm_parent_replacement_between_pin_and_publication_fails_closed(self) -> None:
        program = self._inject_parent_replacement_before_first_publication(self._finalize_program())
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                dist / ".rpmbuild.publish-workspace-parent-replacement" / "candidate",
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-parent-replacement",
            )
            replaced_dist = root / "dist.replaced"
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("finalization parent after first publication changed during finalization", result.stderr)
            self.assertFalse(final.exists())
            self.assertEqual((replaced_dist / "rpmbuild" / "payload.txt").read_text(encoding="utf-8"), "new\n")

    def test_rpm_first_publication_quarantines_replaced_candidate(self) -> None:
        program = self._inject_quarantine_name_collision(
            self._inject_candidate_replacement_before_publication(
                self._finalize_program(), exchange=False
            )
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-candidate-replace" / "candidate"
            recovery = dist / ".rpmbuild.previous-recovery-candidate-replace"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                recovery,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(final.exists())
            self.assertTrue(recovery.is_dir())
            self.assertTrue(recovery.with_name(recovery.name + ".candidate-mismatch").is_dir())
            quarantined = sorted(dist.glob(recovery.name + ".candidate-mismatch-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / "payload.txt").read_text(encoding="utf-8"),
                "attacker\n",
            )
            self.assertEqual(
                (publish.with_name(publish.name + ".replaced") / "payload.txt").read_text(
                    encoding="utf-8"
                ),
                "new\n",
            )
            self.assertIn("quarantined unexpected RPM final directory", result.stderr)

    def test_rpm_candidate_root_replacement_after_snapshot_never_stays_final(self) -> None:
        program = self._inject_candidate_root_replacement_before_publication(
            self._finalize_program()
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                dist / "rpmbuild",
                dist / ".rpmbuild.publish-workspace-root-replace" / "candidate",
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-root-replace",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((dist / "rpmbuild").exists())
            self.assertTrue(
                any(
                    (candidate / "RPMS" / "attacker.rpm").exists()
                    for candidate in dist.glob(".rpmbuild.previous-recovery-root-replace.candidate-mismatch-*")
                )
            )

    def test_rpm_exchange_rolls_back_replaced_candidate(self) -> None:
        program = self._inject_candidate_replacement_before_publication(
            self._finalize_program(), exchange=True
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir(mode=0o700)
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            publish = dist / ".rpmbuild.publish-workspace-candidate-exchange" / "candidate"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-candidate-exchange",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((publish / "payload.txt").read_text(encoding="utf-8"), "attacker\n")
            self.assertEqual(
                (publish.with_name(publish.name + ".replaced") / "payload.txt").read_text(
                    encoding="utf-8"
                ),
                "new\n",
            )

    def test_rpm_exchange_rollback_revalidates_and_quarantines_replacement(self) -> None:
        program = self._inject_rollback_final_replacement(
            self._inject_candidate_replacement_before_publication(
                self._finalize_program(), exchange=True
            )
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir(mode=0o700)
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            publish = dist / ".rpmbuild.publish-workspace-rollback-race" / "candidate"
            recovery = dist / ".rpmbuild.previous-recovery-rollback-race"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                recovery,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(final.exists())
            self.assertEqual(
                (final.with_name(final.name + ".rollback-old") / "payload.txt").read_text(
                    encoding="utf-8"
                ),
                "old\n",
            )
            self.assertEqual((publish / "payload.txt").read_text(encoding="utf-8"), "attacker\n")
            quarantined = sorted(dist.glob(recovery.name + ".candidate-mismatch-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / "payload.txt").read_text(encoding="utf-8"),
                "attacker\n",
            )
            self.assertIn("quarantined unexpected RPM final directory", result.stderr)

    def test_rpm_exchange_rollback_late_replacement_is_quarantined(self) -> None:
        program = self._inject_rollback_final_replacement_after_revalidation(
            self._inject_candidate_replacement_before_publication(
                self._finalize_program(), exchange=True
            )
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir(mode=0o700)
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            recovery = dist / ".rpmbuild.previous-recovery-rollback-late"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                dist / ".rpmbuild.publish-workspace-rollback-late" / "candidate",
                dist / "rpmbuild.previous",
                recovery,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(final.exists())
            self.assertEqual(
                (final.with_name(final.name + ".rollback-late-old") / "payload.txt").read_text(
                    encoding="utf-8"
                ),
                "old\n",
            )
            quarantined = sorted(dist.glob(recovery.name + ".candidate-mismatch-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / "payload.txt").read_text(encoding="utf-8"),
                "attacker\n",
            )

    def test_rpm_exchange_rollback_root_replacement_is_quarantined(self) -> None:
        program = self._inject_rollback_root_replacement_after_revalidation(
            self._inject_candidate_replacement_before_publication(
                self._finalize_program(), exchange=True
            )
        )
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir(mode=0o700)
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            recovery = dist / ".rpmbuild.previous-recovery-rollback-root"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                dist / ".rpmbuild.publish-workspace-rollback-root" / "candidate",
                dist / "rpmbuild.previous",
                recovery,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(final.exists())
            quarantined = sorted(dist.glob(recovery.name + ".candidate-mismatch-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertTrue((quarantined[0] / "RPMS" / "attacker.rpm").exists())

    def test_rpm_late_final_replacement_is_quarantined_before_return(self) -> None:
        program = self._inject_late_final_replacement(self._finalize_program())
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            recovery = dist / ".rpmbuild.previous-recovery-late-race"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                dist / ".rpmbuild.publish-workspace-late-race" / "candidate",
                dist / "rpmbuild.previous",
                recovery,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(final.exists())
            self.assertEqual(
                (final.with_name(final.name + ".late-old") / "payload.txt").read_text(
                    encoding="utf-8"
                ),
                "new\n",
            )
            quarantined = sorted(dist.glob(recovery.name + ".candidate-mismatch-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(
                (quarantined[0] / "payload.txt").read_text(encoding="utf-8"),
                "attacker\n",
            )
            self.assertIn("quarantined unexpected RPM final directory", result.stderr)

    def test_rpm_startup_sweeps_are_bounded_and_private(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn('MAX_SCAN_ENTRIES = 256', source)
        self.assertIn('MAX_STALE_WORKSPACES = 32', source)
        self.assertIn('RPM_STARTUP_SWEEP_TIMEOUT_SECONDS=30', source)
        self.assertIn('RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS=1', source)
        self.assertIn(
            'timeout --foreground --signal=TERM --kill-after="${RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS}s"',
            source,
        )
        self.assertIn('WORKSPACE_MAX_AGE_NS = 24 * 60 * 60 * 1_000_000_000', source)
        self.assertIn('PREFIX = "speed-of-cinnamon-rpm-tmp-"', source)
        self.assertIn('rpm_runtime_root=', source)
        self.assertIn('${HOME}/.cache/speed-of-cinnamon/rpm-${EUID}', source)
        self.assertIn('assert-private-chain build-rpm "${HOME}"', source)
        self.assertIn('assert-private-chain build-rpm "${rpm_runtime_root}"', source)
        self.assertIn('assert-private-chain build-rpm "${rpm_tmp_parent}"', source)
        self.assertIn('assert-private-chain build-rpm "${rpm_runtime_root}" --allow-missing', source)
        self.assertIn('assert-private-chain build-rpm "${rpm_tmp_parent}" --allow-missing', source)
        self.assertIn('runtime root is not private to current user', source)
        self.assertNotIn('direct_root=True', source)
        self.assertNotIn('repo_tmp_root', source)
        self.assertNotIn('${TMPDIR:-/tmp}', source)
        self.assertIn('os.path.lexists(path)', source)
        self.assertEqual(source.count('os.path.lexists(path)'), 1)
        self.assertIn('RPM temporary workspace scan exceeds max', source)
        self.assertIn('RPM temporary workspace snapshot is ambiguous', source)
        self.assertNotIn('except FileNotFoundError:\n                    continue', source)

    def test_rpm_runtime_accepts_private_xdg_without_home(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")
        start = source.index('if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then')
        end = source.index('\nfi\nif [[ ! "${rpm_runtime_root}" == /*', start) + len("\nfi")
        runtime_block = source[start:end]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            runtime.mkdir(mode=0o700)
            safe_fs = root / "safe-fs-stub.sh"
            safe_fs.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            safe_fs.chmod(0o700)
            environment = os.environ.copy()
            environment.pop("HOME", None)
            environment["XDG_RUNTIME_DIR"] = str(runtime)
            harness = (
                "set -euo pipefail\n"
                f"safe_fs_cmd=({str(safe_fs)!r})\n"
                f"{runtime_block}\n"
                "printf '%s\\n' \"$rpm_runtime_root\"\n"
            )
            result = self._run_bounded_harness(
                ["bash", "-c", harness],
                cwd=root,
                timeout=3,
                env=environment,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"{runtime}\n")

    def test_rpm_exit_cleanup_has_one_bounded_absolute_budget(self) -> None:
        source = BUILD_RPM.read_text(encoding="utf-8")

        self.assertIn("RPM_CLEANUP_TIMEOUT_SECONDS=5", source)
        self.assertIn("RPM_CLEANUP_KILL_AFTER_SECONDS=1", source)
        self.assertIn("RPM_CLEANUP_LAUNCH_MARGIN_NS=50000000", source)
        self.assertIn("RPM_CLEANUP_CLOCK_TIMEOUT_SECONDS=0.25", source)
        self.assertIn("RPM_CLEANUP_CLOCK_TIMEOUT_NS=250000000", source)
        self.assertNotRegex(source, r"(?<![A-Za-z0-9_])SECONDS(?![A-Za-z0-9_])")
        self.assertIn("cleanup_deadline_probe()", source)
        self.assertIn(
            "deadline_budget_ns=$((RPM_CLEANUP_TIMEOUT_SECONDS * 1000000000 - RPM_CLEANUP_CLOCK_TIMEOUT_NS - RPM_CLEANUP_LAUNCH_MARGIN_NS))",
            source,
        )
        self.assertIn("time.monotonic_ns() + ${deadline_budget_ns}", source)
        self.assertIn("cleanup_remaining_ns", source)
        self.assertIn("cleanup_remaining_value_ns", source)
        self.assertIn("if ! cleanup_remaining_ns; then", source)
        self.assertNotIn('remaining_ns="$(cleanup_remaining_ns)"', source)
        self.assertIn("cleanup_timeout_value", source)
        self.assertIn('"${cleanup_timeout_command}" --foreground --signal=KILL', source)
        self.assertIn("cleanup_on_exit()", source)
        self.assertIn("trap cleanup_on_exit EXIT", source)
        self.assertNotIn("trap cleanup_tmpdir EXIT", source)
        self.assertLess(
            source.index("trap cleanup_on_exit EXIT"),
            source.index('rpmbuild_tmpdir="$(mktemp -d', source.index("trap cleanup_on_exit EXIT")),
        )
        self.assertIn('"${term_timeout}s" "${safe_fs_cmd[@]}"', source)
        self.assertIn('"${kill_after_timeout}s"', source)
        self.assertIn("cleanup_deadline_ns=0", source)
        self.assertIn("cleanup_safe_fs_invoked=0", source)
        self.assertIn('return "${primary_status}"', source)
        cleanup_start = source.index("cleanup_tmpdir() {")
        self.assertLess(
            source.index('local inherited_status="$?"', cleanup_start),
            source.index("local cleanup_failed=0", cleanup_start),
        )
        cleanup_end = source.index("cleanup_on_exit() {", cleanup_start)
        cleanup_block = source[cleanup_start:cleanup_end]
        self.assertNotRegex(cleanup_block, r"\[\[.*-e")
        self.assertNotRegex(cleanup_block, r"\[\[.*-L")

        post_start = source.index("publish_committed=1\n")
        output_start = source.index("# RPM output enumeration stays bounded", post_start)
        cleanup_start = source.index("stage_cleanup_status=0\n", output_start)
        self.assertLess(output_start, cleanup_start)
        self.assertIn("RPM_OUTPUT_TIMEOUT_SECONDS=30", source)
        self.assertIn("RPM_OUTPUT_KILL_AFTER_SECONDS=1", source)
        self.assertIn("MAX_RPM_OUTPUT_ENTRIES = 256", source)
        self.assertIn("MAX_RPM_OUTPUT_BYTES = 1 << 20", source)
        self.assertIn(
            'timeout --foreground --signal=TERM --kill-after="${RPM_OUTPUT_KILL_AFTER_SECONDS}s"',
            source,
        )
        self.assertNotIn('find "${final_topdir}/RPMS"', source)
        self.assertNotIn("| sort", source)
        self.assertNotIn("if start_cleanup_deadline && cleanup_remaining_ns;", source)
        self.assertNotIn('[[ ! -e "${publish_workspace}"', source)
        self.assertNotIn('[[ ! -e "${publish_recovery}"', source)
        post_commit = source[cleanup_start:]
        self.assertIn("run_cleanup_safe_fs remove", post_commit)
        self.assertNotIn('"${safe_fs_cmd[@]}" remove', post_commit)
        self.assertIn("stage_cleanup_unconfirmed=1", cleanup_block)
        self.assertIn("cleanup_safe_fs_invoked=1", source)
        self.assertIn("workspace cleanup skipped because stage cleanup is unconfirmed", cleanup_block)

    def test_rpm_startup_sweep_handles_old_claim_and_preserves_recent(self) -> None:
        sweep = self._startup_sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "shared"
            private = shared / "speed-of-cinnamon-rpm-1000"
            private.mkdir(parents=True, mode=0o700)
            old_claim = private / "speed-of-cinnamon-rpm-tmp-old.safe-rmdir-claim"
            old_claim.mkdir(mode=0o700)
            old_final = private / "speed-of-cinnamon-rpm-tmp-old.final-token"
            old_final.mkdir(mode=0o700)
            shared_claim = shared / "speed-of-cinnamon-rpm-tmp-shared.safe-rmdir-claim"
            shared_claim.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(old_claim, (old_time, old_time))
            os.utime(old_final, (old_time, old_time))
            os.utime(shared_claim, (old_time, old_time))
            safe_fs_log = root / "safe-fs.log"
            strict_safe_fs = root / "strict-safe-fs.py"
            self._write_strict_safe_fs_wrapper(
                strict_safe_fs,
                real=safe_fs,
                log=safe_fs_log,
            )

            cleaned = self._run_bounded_harness(
                [sys.executable, "-", str(private), str(strict_safe_fs)],
                cwd=root,
                timeout=8,
                input_text=sweep,
            )
            self.assertEqual(cleaned.returncode, 0, cleaned.stderr)
            self.assertFalse(old_claim.exists())
            self.assertFalse(old_final.exists())
            self.assertTrue(shared_claim.exists())

            recent = private / "speed-of-cinnamon-rpm-tmp-recent.safe-rmdir-claim"
            recent.mkdir(mode=0o700)
            blocked = self._run_bounded_harness(
                [sys.executable, "-", str(private), str(strict_safe_fs)],
                cwd=root,
                timeout=8,
                input_text=sweep,
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertTrue(recent.exists())
            remove_calls = [
                line
                for line in safe_fs_log.read_text(encoding="utf-8").splitlines()
                if line.startswith("['remove',")
            ]
            self.assertGreaterEqual(len(remove_calls), 2)
            self.assertTrue(all("'--kind', 'dir'" in line for line in remove_calls))

    def test_rpm_startup_sweep_watchdog_bounds_blocking_scandir(self) -> None:
        sweep = self._startup_sweep_program()
        sweep = sweep.replace(
            "import time\n",
            "import time\n"
            "import signal\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "def blocking_scandir(*args, **kwargs):\n"
            "    time.sleep(60)\n"
            "os.scandir = blocking_scandir\n",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent = root / "private"
            parent.mkdir(mode=0o700)
            harness = (
                "set +e\n"
                "RPM_STARTUP_SWEEP_TIMEOUT_SECONDS=2\n"
                "RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS=1\n"
                f"timeout --foreground --signal=TERM --kill-after=\"${{RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS}}s\" "
                f"\"${{RPM_STARTUP_SWEEP_TIMEOUT_SECONDS}}s\" python3 - {str(parent)!r} "
                f"{str(REPO_ROOT / 'scripts' / 'safe-local-fs.py')!r} <<'PY'\n"
                f"{sweep}\n"
                "PY\n"
                "status=$?\n"
                "printf 'status=%s\\n' \"$status\"\n"
                "exit \"$status\"\n"
            )
            started = time.monotonic()
            result = self._run_bounded_harness(
                ["bash", "-c", harness],
                cwd=root,
                timeout=8,
            )

        self.assertIn(result.returncode, {124, 125, 137})
        self.assertRegex(result.stdout, r"status=(?:124|137)\n")
        self.assertLess(time.monotonic() - started, 4)

    def test_rpm_startup_sweep_reports_helper_failure_after_rename(self) -> None:
        sweep = self._startup_sweep_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            private.mkdir(mode=0o700)
            stale = private / "speed-of-cinnamon-rpm-tmp-old"
            stale.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))
            failing = root / "safe-fs-rename-residue.py"
            self._write_renaming_safe_fs_wrapper(failing, real=safe_fs, target=stale)

            result = self._run_bounded_harness(
                [sys.executable, "-", str(private), str(failing)],
                cwd=root,
                timeout=8,
                input_text=sweep,
            )
            residue = stale.with_name(stale.name + ".residue")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("cleanup is ambiguous after safe-FS failure", result.stderr)
            self.assertIn(str(stale), result.stderr)
            self.assertTrue(residue.exists())

    def test_rpm_startup_sweep_rejects_missing_snapshot_residue(self) -> None:
        sweep = self._startup_sweep_program()
        sweep = sweep.replace(
            "    for path, identity in stale:\n"
            "        remaining_timeout()\n"
            "        if not os.path.lexists(path):\n",
            "    for path, identity in stale:\n"
            "        remaining_timeout()\n"
            "        os.rename(path, path + '.renamed')\n"
            "        if not os.path.lexists(path):\n",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            private.mkdir(mode=0o700)
            stale = private / "speed-of-cinnamon-rpm-tmp-old"
            stale.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))

            result = self._run_bounded_harness(
                [sys.executable, "-", str(private), str(REPO_ROOT / "scripts" / "safe-local-fs.py")],
                cwd=root,
                timeout=8,
                input_text=sweep,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("disappeared or was renamed", result.stderr)
            self.assertIn(str(stale), result.stderr)
            self.assertTrue(stale.with_name(stale.name + ".renamed").exists())

    def test_rpm_startup_sweep_rejects_snapshot_stat_disappearance(self) -> None:
        sweep = self._startup_sweep_program()
        sweep = sweep.replace(
            "                try:\n"
            "                    path_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)\n",
            "                os.rename(entry.name, entry.name + '.renamed', src_dir_fd=directory_fd, dst_dir_fd=directory_fd)\n"
            "                try:\n"
            "                    path_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)\n",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            private.mkdir(mode=0o700)
            stale = private / "speed-of-cinnamon-rpm-tmp-old"
            stale.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))

            result = self._run_bounded_harness(
                [sys.executable, "-", str(private), str(REPO_ROOT / "scripts" / "safe-local-fs.py")],
                cwd=root,
                timeout=8,
                input_text=sweep,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snapshot is ambiguous", result.stderr)
            self.assertIn(str(stale), result.stderr)
            self.assertTrue(stale.with_name(stale.name + ".renamed").exists())

    def test_rpm_startup_sweep_rejects_same_name_snapshot_replacement(self) -> None:
        sweep = self._startup_sweep_program()
        sweep = sweep.replace(
            "                try:\n"
            "                    path_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)\n",
            "                os.rename(entry.name, entry.name + '.old', src_dir_fd=directory_fd, dst_dir_fd=directory_fd)\n"
            "                os.mkdir(entry.name, 0o700, dir_fd=directory_fd)\n"
            "                try:\n"
            "                    path_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)\n",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            private = root / "private"
            private.mkdir(mode=0o700)
            stale = private / "speed-of-cinnamon-rpm-tmp-old"
            stale.mkdir(mode=0o700)
            old_time = time.time() - 2 * 24 * 60 * 60
            os.utime(stale, (old_time, old_time))

            result = self._run_bounded_harness(
                [sys.executable, "-", str(private), str(REPO_ROOT / "scripts" / "safe-local-fs.py")],
                cwd=root,
                timeout=8,
                input_text=sweep,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snapshot is ambiguous", result.stderr)
            self.assertIn(str(stale), result.stderr)
            self.assertTrue(stale.exists())
            self.assertTrue(stale.with_name(stale.name + ".old").exists())

    def test_rpm_finalize_lock_blocks_and_recovers(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            lock.write_text("legacy lock inode\n", encoding="utf-8")
            renamed_lock = dist / ".finalize.lock.renamed"
            stage = root / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("locked\n", encoding="utf-8")
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-lock" / "candidate"
            previous = dist / "rpmbuild.previous"
            recovery = dist / ".rpmbuild.previous-recovery-lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import fcntl, os, sys; handle=os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY); "
                    "fcntl.flock(handle, fcntl.LOCK_EX); print('ready', flush=True); sys.stdin.read()",
                    str(dist),
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            holder_pidfd: int | None = None
            primary_error: BaseException | None = None
            try:
                try:
                    holder_pidfd, _ = self._open_process_pidfd(holder)
                except BaseException as open_error:
                    primary_error = open_error
                    for cleanup_error in self._cleanup_launched_process(holder):
                        open_error.add_note(f"holder launch cleanup failed: {cleanup_error}")
                    raise
                self.assertIsNotNone(holder.stdout)
                self.assertEqual(self._readline_bounded(holder.stdout, timeout=2), "ready\n")
                lock.rename(renamed_lock)
                started = time.monotonic()
                global_deadline_blocked = self._run_finalize(
                    program,
                    safe_fs,
                    lock,
                    stage,
                    final,
                    publish,
                    previous,
                    recovery,
                    lock_timeout=30,
                    finalize_timeout=1,
                )
                self.assertLess(time.monotonic() - started, 3)
                self.assertNotEqual(global_deadline_blocked.returncode, 0)
                self.assertIn("RPM finalization lock timed out", global_deadline_blocked.stderr)
                blocked = self._run_finalize(
                    program,
                    safe_fs,
                    lock,
                    stage,
                    final,
                    publish,
                    previous,
                    recovery,
                    lock_timeout=0,
                )
                self.assertNotEqual(blocked.returncode, 0)
            except BaseException as error:
                primary_error = primary_error or error
                raise
            finally:
                cleanup_errors: list[BaseException] = []
                if holder.stdin is not None:
                    try:
                        holder.stdin.close()
                    except BaseException as close_error:
                        cleanup_errors.append(close_error)
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if holder_pidfd is not None:
                        try:
                            self._signal_pidfd(holder_pidfd, signal.SIGKILL)
                        except BaseException as signal_error:
                            cleanup_errors.append(signal_error)
                    try:
                        holder.wait(timeout=1)
                    except BaseException as wait_error:
                        cleanup_errors.append(wait_error)
                except BaseException as wait_error:
                    cleanup_errors.append(wait_error)
                self._close_process_streams(holder, cleanup_errors)
                if holder_pidfd is not None:
                    try:
                        os.close(holder_pidfd)
                    except BaseException as close_error:
                        cleanup_errors.append(close_error)
                if cleanup_errors:
                    if primary_error is not None:
                        for cleanup_error in cleanup_errors:
                            primary_error.add_note(f"holder cleanup failed: {cleanup_error}")
                    else:
                        raise cleanup_errors[0]

            completed = self._run_finalize(
                program, safe_fs, lock, stage, final, publish, previous, recovery
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "locked\n")

    def test_rpm_finalize_lock_eintr_storm_rechecks_deadline(self) -> None:
        program = self._finalize_program()
        program = program.replace(
            "lock_path, safe_fs, staging_path, final_path, publish_path, previous_path, previous_recovery_path, staging_identity, lifecycle_supervisor, finalize_timeout, lock_timeout = sys.argv[1:]",
            "def interrupted_flock(*args, **kwargs):\n"
            "    raise InterruptedError()\n"
            "fcntl.flock = interrupted_flock\n\n"
            "lock_path, safe_fs, staging_path, final_path, publish_path, previous_path, previous_recovery_path, staging_identity, lifecycle_supervisor, finalize_timeout, lock_timeout = sys.argv[1:]",
            1,
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            stage = root / "stage"
            stage.mkdir()
            result = self._run_finalize(
                program,
                REPO_ROOT / "scripts" / "safe-local-fs.py",
                dist / ".finalize.lock",
                stage,
                dist / "rpmbuild",
                dist / ".rpmbuild.publish-workspace-eintr" / "candidate",
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-eintr",
                lock_timeout=1,
                finalize_timeout=2,
                process_timeout=4,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("RPM finalization lock timed out", result.stderr)

    def test_existing_previous_recovery_is_preserved_and_blocks(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir()
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            previous = dist / "rpmbuild.previous"
            recovery = dist / ".rpmbuild.previous-recovery-existing"
            recovery.mkdir()
            (recovery / "payload.txt").write_text("recovery\n", encoding="utf-8")
            result = self._run_finalize(
                program,
                safe_fs,
                lock,
                stage,
                final,
                dist / ".rpmbuild.publish-workspace-recovery" / "candidate",
                previous,
                dist / ".rpmbuild.previous-recovery-new",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((recovery / "payload.txt").read_text(encoding="utf-8"), "recovery\n")

    def test_rpm_cleanup_status_is_local_to_failed_operation(self) -> None:
        program = self._inject_workspace_cleanup_replacement(self._finalize_program())
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir(mode=0o700)
            stage = root / "stage"
            stage.mkdir(mode=0o700)
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            publish = dist / ".rpmbuild.publish-workspace-status" / "candidate"
            result = self._run_finalize(
                program,
                safe_fs,
                dist / ".finalize.lock",
                stage,
                final,
                publish,
                dist / "rpmbuild.previous",
                dist / ".rpmbuild.previous-recovery-status",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("publish workspace cleanup not attempted; safe-FS was not invoked", result.stderr)
            self.assertNotIn("publish workspace cleanup failed after safe-FS invocation", result.stderr)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertTrue(publish.parent.with_name(publish.parent.name + ".replaced").exists())

    def test_rpm_exchange_failure_preserves_old_final_and_candidate_recovery(self) -> None:
        program = self._finalize_program()
        program = self._inject_pinned_rename_failure(program, "flags == 2")
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("new\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir()
            (final / "payload.txt").write_text("old\n", encoding="utf-8")
            previous = dist / "rpmbuild.previous"
            recovery = dist / ".rpmbuild.previous-recovery-exchange"
            publish = dist / ".rpmbuild.publish-workspace-exchange" / "candidate"
            result = self._run_finalize(program, safe_fs, lock, stage, final, publish, previous, recovery)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((publish / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertIn(str(publish), result.stderr)

    def test_rpm_post_commit_cleanup_failure_preserves_committed_state(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage = root / "stage"
            stage.mkdir()
            (stage / "payload.txt").write_text("new-final\n", encoding="utf-8")
            final = dist / "rpmbuild"
            final.mkdir()
            (final / "payload.txt").write_text("old-final\n", encoding="utf-8")
            previous = dist / "rpmbuild.previous"
            previous.mkdir()
            (previous / "payload.txt").write_text("old-previous\n", encoding="utf-8")
            recovery = dist / ".rpmbuild.previous-recovery-cleanup"
            publish = dist / ".rpmbuild.publish-workspace-cleanup" / "candidate"
            failing = root / "safe-fs-fail-cleanup.py"
            safe_fs_log = root / "safe-fs.log"
            self._write_strict_safe_fs_wrapper(
                failing,
                real=safe_fs,
                log=safe_fs_log,
                failure_target=recovery,
            )
            result = self._run_finalize(program, failing, lock, stage, final, publish, previous, recovery)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "new-final\n")
            self.assertEqual((previous / "payload.txt").read_text(encoding="utf-8"), "old-final\n")
            self.assertEqual((recovery / "payload.txt").read_text(encoding="utf-8"), "old-previous\n")
            self.assertIn("previous recovery cleanup failed after safe-FS invocation", result.stderr)
            remove_calls = [
                line
                for line in safe_fs_log.read_text(encoding="utf-8").splitlines()
                if line.startswith("['remove',")
            ]
            self.assertTrue(remove_calls)
            self.assertTrue(all("'--kind', 'dir'" in line for line in remove_calls))

    def test_rpm_publish_first_exchange_previous_and_fail_closed_recovery(self) -> None:
        program = self._finalize_program()
        safe_fs = REPO_ROOT / "scripts" / "safe-local-fs.py"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dist = root / "dist"
            dist.mkdir()
            lock = dist / ".finalize.lock"
            stage_one = root / "stage-one"
            stage_one.mkdir()
            (stage_one / "payload.txt").write_text("old\n", encoding="utf-8")
            (stage_one / "SOURCES").mkdir()
            (stage_one / "SOURCES" / "old-source.rpm").write_bytes(b"old-source\n")
            final = dist / "rpmbuild"
            publish_one = dist / ".rpmbuild.publish-workspace-one" / "candidate"
            previous = dist / "rpmbuild.previous"
            recovery_one = dist / ".rpmbuild.previous-recovery-one"
            stale = dist / ".rpmbuild.publish-stale"
            stale.mkdir()
            (stale / "recovery.txt").write_text("keep\n", encoding="utf-8")

            blocked_by_stale = self._run_finalize(
                program, safe_fs, lock, stage_one, final, publish_one, previous, recovery_one
            )
            self.assertNotEqual(blocked_by_stale.returncode, 0)
            self.assertEqual((stale / "recovery.txt").read_text(encoding="utf-8"), "keep\n")
            stale.joinpath("recovery.txt").unlink()
            stale.rmdir()

            first = self._run_finalize(
                program, safe_fs, lock, stage_one, final, publish_one, previous, recovery_one
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            result_identities = first.stdout.strip().split()
            self.assertEqual(len(result_identities), 3)
            self.assertEqual(
                result_identities[1],
                f"{(final / 'RPMS').stat().st_dev}:{(final / 'RPMS').stat().st_ino}:{(final / 'RPMS').stat().st_mode}",
            )
            self.assertEqual(
                result_identities[2],
                f"{(final / 'SRPMS').stat().st_dev}:{(final / 'SRPMS').stat().st_ino}:{(final / 'SRPMS').stat().st_mode}",
            )
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse(publish_one.parent.exists())
            self.assertFalse(previous.exists())

            stage_two = root / "stage-two"
            stage_two.mkdir()
            (stage_two / "payload.txt").write_text("new\n", encoding="utf-8")
            (stage_two / "SOURCES").mkdir()
            (stage_two / "SOURCES" / "new-source.rpm").write_bytes(b"new-source\n")
            publish_two = dist / ".rpmbuild.publish-workspace-two" / "candidate"
            recovery_two = dist / ".rpmbuild.previous-recovery-two"
            second = self._run_finalize(
                program, safe_fs, lock, stage_two, final, publish_two, previous, recovery_two
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((previous / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual((previous / "SOURCES" / "old-source.rpm").read_bytes(), b"old-source\n")
            self.assertFalse(publish_two.parent.exists())

            blocked_program = self._inject_pinned_rename_failure(
                program, "flags == 1 and target_name == recovery_name"
            )
            stage_three = root / "stage-three"
            stage_three.mkdir()
            (stage_three / "payload.txt").write_text("blocked\n", encoding="utf-8")
            blocked = self._run_finalize(
                blocked_program,
                safe_fs,
                lock,
                stage_three,
                final,
                dist / ".rpmbuild.publish-workspace-three" / "candidate",
                previous,
                dist / ".rpmbuild.previous-recovery-three",
            )
            self.assertNotEqual(blocked.returncode, 0)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((previous / "payload.txt").read_text(encoding="utf-8"), "old\n")

            failed_after_exchange_program = self._inject_pinned_rename_failure(
                program, "flags == 1 and target_name == previous_name"
            )
            publish_four = dist / ".rpmbuild.publish-workspace-four" / "candidate"
            stage_four = root / "stage-four"
            stage_four.mkdir()
            (stage_four / "payload.txt").write_text("later\n", encoding="utf-8")
            failed_after_exchange = self._run_finalize(
                failed_after_exchange_program,
                safe_fs,
                lock,
                stage_four,
                final,
                publish_four,
                previous,
                dist / ".rpmbuild.previous-recovery-four",
            )
            self.assertNotEqual(failed_after_exchange.returncode, 0)
            self.assertEqual((final / "payload.txt").read_text(encoding="utf-8"), "later\n")
            self.assertEqual((publish_four / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((publish_four / "SOURCES" / "new-source.rpm").read_bytes(), b"new-source\n")
            self.assertIn(str(publish_four), failed_after_exchange.stderr)


if __name__ == "__main__":
    unittest.main()
