#!/usr/bin/env python3
"""Run one RPM lifecycle in a bounded, reaped process group."""

from __future__ import annotations

import argparse
import ctypes
import dataclasses
import errno
import math
import os
import re
import select
import signal
import subprocess
import sys
import time


PR_SET_CHILD_SUBREAPER = 36
POLL_INTERVAL_SECONDS = 0.05
REAP_RESERVE_SECONDS = 0.25
HANDOFF_TOKEN_PATTERN = re.compile(rb"rpm-lifecycle-[0-9]+-[0-9]+-[0-9]+-[0-9]+\n\Z")
MAX_PROC_STAT_BYTES = 4096
MAX_PIDFD_INFO_BYTES = 512
# This is a per-read bound, not a total children-list limit.  /proc may
# contain more children than fit in one read; the parser below consumes the
# complete authenticated snapshot in fixed-size chunks.
MAX_DIRECT_CHILDREN_BYTES = 4096
MAX_DIRECT_CHILD_TOKEN_BYTES = 32
DIRECT_CHILDREN_BATCH_SIZE = 32
MAX_DRAIN_ROUNDS = 256
DIRECT_CHILDREN_CLEANUP_RESERVE_SECONDS = 2.0
IDENTITY_STDERR_MAX_BYTES = 4096
MAX_IDENTITY_OUTPUT_BYTES = 4096
MAX_HANDOFF_BYTES = 4096
MAX_SIGNAL_ERROR_REPORTS = 8
MAX_CLEANUP_DIAGNOSTICS = 32
PIDFD_RESOURCE_ERRNOS = frozenset({errno.EMFILE, errno.ENFILE})
IDENTITY_PATTERN = re.compile(rb"[0-9]+:[0-9]+:[0-9]+\n?\Z")


class SupervisorInterrupted(Exception):
    """The supervisor caught a signal and completed bounded child cleanup."""

    def __init__(self, signum: int) -> None:
        super().__init__(f"RPM lifecycle supervisor interrupted by signal {signum}")
        self.signum = signum


class DirectChildrenSnapshotError(RuntimeError):
    """Snapshot failure carrying every valid PID observed before failure."""

    def __init__(self, message: str, observed: set[int]) -> None:
        super().__init__(message)
        self.observed = observed


class DirectChildrenParseError(DirectChildrenSnapshotError):
    """Malformed snapshot with all valid PIDs observed before the error."""


class DirectChildrenReadError(DirectChildrenSnapshotError):
    """Read failure with all valid PIDs observed before the error."""


CHILD_ALREADY_REAPED = object()


@dataclasses.dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: int
    parent_pid: int
    process_group_id: int
    session_id: int
    state: str


@dataclasses.dataclass
class ChildBinding:
    identity: ProcessIdentity
    pidfd: int
    term_sent: bool = False
    kill_sent: bool = False
    owned_direct: bool = False


def set_child_subreaper() -> None:
    if sys.platform != "linux":
        raise RuntimeError("RPM lifecycle supervisor requires Linux subreaper support")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    except (AttributeError, OSError) as exc:
        raise RuntimeError("RPM lifecycle supervisor cannot access prctl") from exc
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def require_kernel_primitives() -> object:
    required = ("pidfd_open", "waitid", "P_PID", "WNOWAIT", "WEXITED", "WNOHANG")
    if sys.platform != "linux" or any(not hasattr(os, name) for name in required):
        raise RuntimeError("RPM lifecycle supervisor requires Linux pidfd and waitid support")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        pidfd_send_signal = libc.pidfd_send_signal
    except (AttributeError, OSError) as exc:
        raise RuntimeError("RPM lifecycle supervisor cannot access pidfd_send_signal") from exc
    pidfd_send_signal.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    pidfd_send_signal.restype = ctypes.c_int
    return pidfd_send_signal


def validate_pidfd_send_signal(pidfd_send_signal: object) -> None:
    """Exercise pidfd_send_signal against this process before launching a child."""
    pidfd = os.pidfd_open(os.getpid(), 0)
    try:
        result = pidfd_send_signal(pidfd, 0, None, 0)
        if result != 0:
            error_number = ctypes.get_errno() or errno.EIO
            raise OSError(error_number, os.strerror(error_number))
    finally:
        primary_error = sys.exc_info()[1]
        try:
            os.close(pidfd)
        except BaseException as close_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "RPM lifecycle pidfd validation close failed: "
                f"{type(close_error).__name__}: {close_error}"
            )


def read_proc_identity(process_id: int) -> ProcessIdentity:
    proc_path = f"/proc/{process_id}/stat"
    descriptor = os.open(proc_path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        payload = os.read(descriptor, MAX_PROC_STAT_BYTES + 1)
    finally:
        primary_error = sys.exc_info()[1]
        try:
            os.close(descriptor)
        except BaseException as close_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                f"RPM lifecycle process identity close failed for {proc_path}: "
                f"{type(close_error).__name__}: {close_error}"
            )
    if len(payload) > MAX_PROC_STAT_BYTES:
        raise RuntimeError(f"RPM lifecycle process identity is too large: {proc_path}")
    fields = payload.rstrip(b"\n").rpartition(b") ")[2].split()
    if len(fields) <= 19:
        raise RuntimeError(f"RPM lifecycle process identity is malformed: {proc_path}")
    try:
        return ProcessIdentity(
            pid=process_id,
            start_time=int(fields[19]),
            parent_pid=int(fields[1]),
            process_group_id=int(fields[2]),
            session_id=int(fields[3]),
            state=fields[0].decode("ascii"),
        )
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"RPM lifecycle process identity is malformed: {proc_path}") from exc


def _finish_direct_child_token(token: bytearray, seen: set[int], batch: list[int]) -> None:
    if not token:
        return
    if not token.isdigit():
        raise DirectChildrenParseError(
            "RPM lifecycle direct-child list is malformed",
            seen,
        )
    try:
        process_id = int(token)
    except ValueError as exc:
        raise DirectChildrenParseError(
            "RPM lifecycle direct-child list is malformed",
            seen,
        ) from exc
    if process_id <= 0:
        raise DirectChildrenParseError(
            "RPM lifecycle direct-child list contains invalid PID",
            seen,
        )
    if process_id in seen:
        raise DirectChildrenParseError(
            "RPM lifecycle direct-child list contains duplicate PID",
            seen,
        )
    seen.add(process_id)
    batch.append(process_id)


def iter_direct_child_batches(descriptor: int, *, deadline: float | None = None):
    """Parse one children FD in fixed-size reads and bounded batches."""
    token = bytearray()
    seen: set[int] = set()
    batch: list[int] = []
    whitespace = b" \t\r\n\v\f"
    parse_error: DirectChildrenParseError | None = None
    discard_token = False
    while True:
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise DirectChildrenReadError(
                    "RPM lifecycle direct-child list read deadline exceeded",
                    seen,
                )
            payload = os.read(descriptor, MAX_DIRECT_CHILDREN_BYTES)
        except InterruptedError as exc:
            if deadline is not None and time.monotonic() >= deadline:
                raise DirectChildrenReadError(
                    "RPM lifecycle direct-child list read deadline exceeded",
                    seen,
                ) from exc
            yield_after_eintr(deadline)
            continue
        except OSError as exc:
            # A token without whitespace or EOF is not authenticated.  Keep
            # only tokens that were already completed before the I/O failure.
            raise DirectChildrenReadError(
                "RPM lifecycle direct-child list read failed",
                seen,
            ) from exc
        if not payload:
            break
        for value in payload:
            if value in whitespace:
                if not discard_token:
                    try:
                        _finish_direct_child_token(token, seen, batch)
                    except DirectChildrenParseError as error:
                        parse_error = parse_error or error
                token.clear()
                discard_token = False
                if len(batch) == DIRECT_CHILDREN_BATCH_SIZE:
                    yield batch
                    batch = []
                continue
            if len(token) >= MAX_DIRECT_CHILD_TOKEN_BYTES:
                parse_error = parse_error or DirectChildrenParseError(
                    "RPM lifecycle direct-child PID token is too large",
                    seen,
                )
                discard_token = True
                continue
            if discard_token:
                continue
            token.append(value)
    if not discard_token:
        try:
            _finish_direct_child_token(token, seen, batch)
        except DirectChildrenParseError as error:
            parse_error = parse_error or error
    if batch:
        yield batch
    if parse_error is not None:
        parse_error.observed = seen
        raise parse_error


def read_direct_children_batches(*, deadline: float | None = None):
    proc_path = f"/proc/self/task/{os.getpid()}/children"
    descriptor = os.open(
        proc_path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        yield from iter_direct_child_batches(descriptor, deadline=deadline)
    finally:
        primary_error = sys.exc_info()[1]
        try:
            os.close(descriptor)
        except BaseException as close_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "RPM lifecycle direct-child snapshot close failed: "
                f"{type(close_error).__name__}: {close_error}"
            )


def read_direct_children(*, deadline: float | None = None) -> set[int]:
    children = set()
    for batch in read_direct_children_batches(deadline=deadline):
        children.update(batch)
    return children


def read_direct_children_snapshot(
    *, deadline: float | None = None
) -> tuple[set[int], BaseException | None]:
    children: set[int] = set()
    try:
        for batch in read_direct_children_batches(deadline=deadline):
            children.update(batch)
    except DirectChildrenSnapshotError as error:
        children.update(error.observed)
        return children, error
    except BaseException as error:
        return children, error
    return children, None


def direct_children_read_deadline(*, cleanup_deadline: float, deadline: float) -> float:
    """Give each bounded snapshot a fresh EINTR retry window."""
    now = time.monotonic()
    retry_deadline = now + POLL_INTERVAL_SECONDS
    if cleanup_deadline > now:
        retry_deadline = min(retry_deadline, cleanup_deadline)
    return min(deadline, retry_deadline)


def direct_children_cleanup_reserve(timeout: float) -> float:
    return min(
        DIRECT_CHILDREN_CLEANUP_RESERVE_SECONDS,
        max(REAP_RESERVE_SECONDS, timeout / 4),
    )


def ensure_launch_budget(launch_deadline: float, *, phase: str) -> None:
    """Refuse child launch or binding after its bounded startup budget."""
    if time.monotonic() >= launch_deadline:
        raise TimeoutError(f"RPM {phase} launch budget exhausted before child start")


def yield_after_eintr(deadline: float | None) -> None:
    if deadline is None:
        time.sleep(POLL_INTERVAL_SECONDS)
        return
    remaining = deadline - time.monotonic()
    if remaining > 0:
        time.sleep(min(POLL_INTERVAL_SECONDS, remaining))


def record_direct_children_error(
    error: BaseException | None,
    snapshot_errors: list[BaseException] | None,
) -> None:
    if error is None:
        return
    if snapshot_errors is None:
        raise error
    append_bounded_diagnostic(
        snapshot_errors,
        error,
        label="RPM lifecycle snapshot",
    )


def append_bounded_diagnostic(
    errors: list[BaseException] | None,
    error: BaseException,
    *,
    label: str,
    limit: int = MAX_CLEANUP_DIAGNOSTICS,
) -> None:
    if errors is None:
        return
    if len(errors) < limit - 1:
        errors.append(error)
    elif len(errors) == limit - 1:
        errors.append(
            RuntimeError(f"{label} diagnostics truncated after {limit - 1} entries")
        )


def cleanup_budget_available(
    deadline: float,
    errors: list[BaseException] | None,
    *,
    phase: str,
) -> bool:
    if time.monotonic() < deadline:
        return True
    append_bounded_diagnostic(
        errors,
        TimeoutError(f"RPM lifecycle {phase} cleanup deadline exhausted"),
        label=f"RPM lifecycle {phase}",
    )
    return False


def record_unresolved_children(
    errors: list[BaseException] | None,
    *,
    phase: str,
    count: int,
) -> None:
    if count <= 0:
        return
    append_bounded_diagnostic(
        errors,
        RuntimeError(
            f"RPM lifecycle {phase} cleanup stopped with unresolved child count: {count}"
        ),
        label=f"RPM lifecycle {phase}",
    )


def is_pidfd_resource_error(error: BaseException) -> bool:
    """Allow exact-PID ownership fallback only for pidfd resource exhaustion."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        marker = id(current)
        if marker in seen:
            continue
        seen.add(marker)
        if isinstance(current, OSError) and current.errno in PIDFD_RESOURCE_ERRNOS:
            return True
        for related in (current.__cause__, current.__context__):
            if related is not None:
                pending.append(related)
    return False


def iter_child_batches(children: set[int]):
    batch: list[int] = []
    for process_id in children:
        batch.append(process_id)
        if len(batch) == DIRECT_CHILDREN_BATCH_SIZE:
            yield batch
            batch = []
    if batch:
        yield batch


def describe_error(error: BaseException, *, limit: int = 256) -> str:
    return f"{type(error).__name__}: {str(error).replace(chr(10), ' ')[:limit]}"


def read_pidfd_target_pid(pidfd: int) -> int:
    """Read and strictly validate Linux pidfd fdinfo target PID."""
    proc_path = f"/proc/self/fdinfo/{pidfd}"
    descriptor = os.open(
        proc_path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        payload = os.read(descriptor, MAX_PIDFD_INFO_BYTES + 1)
    finally:
        primary_error = sys.exc_info()[1]
        try:
            os.close(descriptor)
        except BaseException as close_error:
            if primary_error is None:
                raise
            primary_error.add_note(
                "RPM lifecycle pidfd fdinfo close failed: "
                f"{type(close_error).__name__}: {close_error}"
            )
    if len(payload) > MAX_PIDFD_INFO_BYTES:
        raise RuntimeError("RPM lifecycle pidfd fdinfo is too large")
    pid_lines = [line for line in payload.splitlines() if line.startswith(b"Pid:")]
    if len(pid_lines) != 1:
        raise RuntimeError("RPM lifecycle pidfd fdinfo has no unique target PID")
    _label, separator, value = pid_lines[0].partition(b":")
    value = value.strip()
    if not separator or not value.isdigit():
        raise RuntimeError("RPM lifecycle pidfd fdinfo target PID is malformed")
    target_pid = int(value)
    if target_pid <= 0:
        raise RuntimeError("RPM lifecycle pidfd fdinfo target PID is invalid")
    return target_pid


def read_authenticated_direct_child_identity(
    process_id: int,
    *,
    expected_parent_pid: int,
    deadline: float | None = None,
) -> ProcessIdentity:
    """Prove direct-child ownership before using an exact PID fallback."""
    snapshot_deadline = deadline
    if deadline is not None:
        snapshot_deadline = direct_children_read_deadline(
            cleanup_deadline=deadline,
            deadline=deadline,
        )
    direct_children, snapshot_error = read_direct_children_snapshot(
        deadline=snapshot_deadline,
    )
    if snapshot_error is not None:
        raise snapshot_error
    if process_id not in direct_children:
        raise RuntimeError(
            f"RPM lifecycle PID {process_id} is not an authenticated direct child"
        )
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError(
            f"RPM lifecycle direct-child identity deadline exceeded for PID {process_id}"
        )
    identity = read_proc_identity(process_id)
    if identity.parent_pid != expected_parent_pid:
        raise RuntimeError(f"RPM lifecycle child PID {process_id} changed parent")
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError(
            f"RPM lifecycle direct-child identity deadline exceeded for PID {process_id}"
        )
    return identity


def close_pidfd_after_bind_failure(
    pidfd: int,
    primary_error: BaseException,
    *,
    label: str,
) -> None:
    """Close one locally-owned pidfd without retaining a reusable number."""
    descriptor = pidfd
    try:
        os.close(descriptor)
    except BaseException as close_error:
        primary_error.add_note(
            f"{label} close failed: {type(close_error).__name__}: {close_error}"
        )


def bind_child_exact(process_id: int) -> ChildBinding:
    try:
        pidfd = os.pidfd_open(process_id, 0)
    except OSError as exc:
        raise RuntimeError(f"RPM lifecycle cannot bind child PID {process_id}") from exc
    try:
        if read_pidfd_target_pid(pidfd) != process_id:
            raise RuntimeError(f"RPM lifecycle pidfd target changed for PID {process_id}")
        identity = read_proc_identity(process_id)
        return ChildBinding(identity=identity, pidfd=pidfd)
    except BaseException as primary_error:
        close_pidfd_after_bind_failure(
            pidfd,
            primary_error,
            label="RPM lifecycle child binding",
        )
        raise


def bind_owned_child_for_cleanup(process_id: int, *, expected_parent_pid: int) -> ChildBinding:
    """Bind still-owned child after direct and pidfd identity proof."""
    try:
        pidfd = os.pidfd_open(process_id, 0)
    except OSError as exc:
        raise RuntimeError(f"RPM lifecycle cannot bind owned child PID {process_id}") from exc
    try:
        if read_pidfd_target_pid(pidfd) != process_id:
            raise RuntimeError(f"RPM lifecycle pidfd target changed for PID {process_id}")
        identity = read_authenticated_direct_child_identity(
            process_id,
            expected_parent_pid=expected_parent_pid,
        )
        return ChildBinding(identity=identity, pidfd=pidfd, owned_direct=True)
    except BaseException as primary_error:
        close_pidfd_after_bind_failure(
            pidfd,
            primary_error,
            label="RPM lifecycle owned-child binding",
        )
        raise


def bind_recovered_child(
    process_id: int,
    *,
    expected_parent_pid: int,
    deadline: float,
) -> ChildBinding:
    """Recover a root binding only across two direct-child/PIDFD proofs."""
    before = read_authenticated_direct_child_identity(
        process_id,
        expected_parent_pid=expected_parent_pid,
        deadline=deadline,
    )
    try:
        pidfd = os.pidfd_open(process_id, 0)
    except OSError as exc:
        raise RuntimeError(f"RPM lifecycle cannot recover child PID {process_id}") from exc
    try:
        if read_pidfd_target_pid(pidfd) != process_id:
            raise RuntimeError(f"RPM lifecycle recovery pidfd target changed for PID {process_id}")
        recovered = read_authenticated_direct_child_identity(
            process_id,
            expected_parent_pid=expected_parent_pid,
            deadline=deadline,
        )
        if (
            recovered.start_time != before.start_time
            or recovered.parent_pid != before.parent_pid
            or recovered.process_group_id != before.process_group_id
            or recovered.session_id != before.session_id
        ):
            raise RuntimeError(f"RPM lifecycle child PID {process_id} changed during recovery")
        if read_pidfd_target_pid(pidfd) != process_id:
            raise RuntimeError(f"RPM lifecycle recovery pidfd target changed for PID {process_id}")
        return ChildBinding(identity=recovered, pidfd=pidfd, owned_direct=True)
    except BaseException as primary_error:
        close_pidfd_after_bind_failure(
            pidfd,
            primary_error,
            label="RPM lifecycle recovery binding",
        )
        raise


def make_verified_owned_child_binding(
    process_id: int,
    *,
    expected_parent_pid: int,
    deadline: float | None = None,
) -> ChildBinding:
    identity = read_authenticated_direct_child_identity(
        process_id,
        expected_parent_pid=expected_parent_pid,
        deadline=deadline,
    )
    return ChildBinding(identity=identity, pidfd=-1, owned_direct=True)


def bind_unbound_popen_root_for_cleanup(
    child: subprocess.Popen[object],
    *,
    expected_parent_pid: int,
    deadline: float,
) -> ChildBinding:
    """Authenticate a Popen root when every pidfd binding path failed."""
    process_id = child.pid
    if child.poll() is not None:
        raise ChildProcessError(f"RPM lifecycle Popen root {process_id} already exited")
    if time.monotonic() >= deadline:
        raise TimeoutError("RPM lifecycle unbound root binding deadline exhausted")
    first = read_authenticated_direct_child_identity(
        process_id,
        expected_parent_pid=expected_parent_pid,
        deadline=deadline,
    )
    if first.process_group_id != process_id or first.session_id != process_id:
        raise RuntimeError("RPM lifecycle unbound root process group identity is invalid")
    if time.monotonic() >= deadline:
        raise TimeoutError("RPM lifecycle unbound root revalidation deadline exhausted")
    second = read_authenticated_direct_child_identity(
        process_id,
        expected_parent_pid=expected_parent_pid,
        deadline=deadline,
    )
    if child.poll() is not None:
        raise ChildProcessError(f"RPM lifecycle Popen root {process_id} exited during binding")
    if (
        second.start_time != first.start_time
        or second.parent_pid != first.parent_pid
        or second.process_group_id != first.process_group_id
        or second.session_id != first.session_id
    ):
        raise RuntimeError("RPM lifecycle unbound root identity changed during recovery")
    return ChildBinding(identity=second, pidfd=-1, owned_direct=True)


def cleanup_unbound_popen_root(
    child: subprocess.Popen[object],
    *,
    expected_parent_pid: int,
    pidfd_send_signal: object,
    bindings: dict[int, ChildBinding],
    baseline_children: set[int] | None,
    deadline: float,
    signal_errors: list[BaseException],
    cleanup_errors: list[BaseException],
) -> bool:
    """Bound cleanup for a Popen root when no root binding survived."""
    root_pid = child.pid
    root_identity: ProcessIdentity | None = None

    def root_is_alive() -> bool:
        if child.poll() is not None:
            return False
        for attempt in range(3):
            try:
                waited_pid, status = os.waitpid(root_pid, os.WNOHANG)
            except InterruptedError as wait_error:
                if attempt < 2 and time.monotonic() < deadline:
                    continue
                append_bounded_diagnostic(
                    cleanup_errors,
                    wait_error,
                    label="RPM lifecycle unbound Popen root wait",
                )
                return True
            except ChildProcessError:
                return False
            except OSError as wait_error:
                append_bounded_diagnostic(
                    cleanup_errors,
                    wait_error,
                    label="RPM lifecycle unbound Popen root wait",
                )
                return True
            if waited_pid == root_pid:
                child.returncode = os.waitstatus_to_exitcode(status)
                return False
            return True
        return True

    try:
        if root_is_alive():
            first = read_authenticated_direct_child_identity(
                root_pid,
                expected_parent_pid=expected_parent_pid,
                deadline=deadline,
            )
            if first.process_group_id != root_pid or first.session_id != root_pid:
                raise RuntimeError("RPM lifecycle unbound Popen root group identity is invalid")
            second = read_authenticated_direct_child_identity(
                root_pid,
                expected_parent_pid=expected_parent_pid,
                deadline=deadline,
            )
            if (
                second.start_time != first.start_time
                or second.parent_pid != first.parent_pid
                or second.process_group_id != first.process_group_id
                or second.session_id != first.session_id
            ):
                raise RuntimeError("RPM lifecycle unbound Popen root identity changed")
            root_identity = second
    except BaseException as identity_error:
        append_bounded_diagnostic(
            cleanup_errors,
            identity_error,
            label="RPM lifecycle unbound Popen root identity",
        )

    if root_is_alive():
        if root_identity is not None:
            try:
                os.killpg(root_identity.process_group_id, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as group_error:
                append_bounded_diagnostic(
                    cleanup_errors,
                    group_error,
                    label="RPM lifecycle unbound Popen root group",
                )
        if root_is_alive() and cleanup_budget_available(
            deadline,
            cleanup_errors,
            phase="unbound Popen root signal",
        ):
            try:
                # Popen's unreaped direct-child authority prevents PID reuse.
                child.send_signal(signal.SIGKILL)
            except ProcessLookupError:
                pass
            except BaseException as root_error:
                append_bounded_diagnostic(
                    cleanup_errors,
                    root_error,
                    label="RPM lifecycle unbound Popen root signal",
                )

    while root_is_alive() and cleanup_budget_available(
        deadline,
        cleanup_errors,
        phase="unbound Popen root reap",
    ):
        time.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    if root_is_alive():
        append_bounded_diagnostic(
            cleanup_errors,
            RuntimeError(f"unbound Popen root {root_pid} remained unreaped"),
            label="RPM lifecycle unbound Popen root",
        )
        return False

    if baseline_children is None:
        append_bounded_diagnostic(
            cleanup_errors,
            RuntimeError("unbound Popen descendant ownership baseline unavailable"),
            label="RPM lifecycle unbound Popen descendants",
        )
        return True

    clean_rounds = 0
    for _ in range(MAX_DRAIN_ROUNDS):
        if not cleanup_budget_available(
            deadline,
            cleanup_errors,
            phase="unbound Popen descendant snapshot",
        ):
            record_unresolved_children(
                cleanup_errors,
                phase="unbound Popen descendant snapshot",
                count=sum(process_id != root_pid for process_id in bindings),
            )
            break
        direct_children, snapshot_error = read_direct_children_snapshot(
            deadline=deadline
        )
        if snapshot_error is not None:
            append_bounded_diagnostic(
                cleanup_errors,
                snapshot_error,
                label="RPM lifecycle unbound Popen descendant snapshot",
            )
            record_unresolved_children(
                cleanup_errors,
                phase="unbound Popen descendant snapshot",
                count=len(direct_children),
            )
            break
        candidates = direct_children - baseline_children - {root_pid}
        for process_id in candidates:
            if process_id in bindings:
                continue
            try:
                binding = bind_child(
                    process_id,
                    expected_parent_pid=expected_parent_pid,
                )
            except BaseException as bind_error:
                if not is_pidfd_resource_error(bind_error):
                    append_bounded_diagnostic(
                        cleanup_errors,
                        bind_error,
                        label="RPM lifecycle unbound Popen descendant binding",
                    )
                    continue
                try:
                    binding = make_owned_child_binding(
                        process_id,
                        expected_parent_pid=expected_parent_pid,
                    )
                except BaseException as fallback_error:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        fallback_error,
                        label="RPM lifecycle unbound Popen descendant binding",
                    )
                    continue
            bindings[process_id] = binding
        if bindings:
            try:
                _, final_errors = finalize_adopted_children(
                    bindings,
                    pidfd_send_signal,
                    root_pid=root_pid,
                    expected_parent_pid=expected_parent_pid,
                    deadline=deadline,
                    signal_errors=signal_errors,
                )
                for final_error in final_errors:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        final_error,
                        label="RPM lifecycle unbound Popen descendant cleanup",
                    )
            except BaseException as final_error:
                append_bounded_diagnostic(
                    cleanup_errors,
                    final_error,
                    label="RPM lifecycle unbound Popen descendant cleanup",
                )
        remaining = set(bindings) - {root_pid}
        if not candidates and not remaining:
            clean_rounds += 1
            if clean_rounds >= 2:
                break
        else:
            clean_rounds = 0
        if cleanup_budget_available(
            deadline,
            cleanup_errors,
            phase="unbound Popen descendant settle",
        ):
            time.sleep(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
        else:
            break
    remaining = set(bindings) - {root_pid}
    if remaining:
        record_unresolved_children(
            cleanup_errors,
            phase="unbound Popen descendants",
            count=len(remaining),
        )
    return True


def make_owned_child_binding(process_id: int, *, expected_parent_pid: int) -> ChildBinding:
    """Bind an unreaped direct child by kernel parent ownership and exact PID."""
    return ChildBinding(
        identity=ProcessIdentity(
            pid=process_id,
            start_time=-1,
            parent_pid=expected_parent_pid,
            process_group_id=-1,
            session_id=-1,
            state="?",
        ),
        pidfd=-1,
        owned_direct=True,
    )


def bind_child(process_id: int, *, expected_parent_pid: int) -> ChildBinding:
    binding = bind_child_exact(process_id)
    if binding.identity.parent_pid != expected_parent_pid:
        primary_error = RuntimeError(f"RPM lifecycle child PID {process_id} changed parent")
        try:
            close_binding(binding)
        except BaseException as close_error:
            primary_error.add_note(
                "RPM lifecycle child binding close failed: "
                f"{type(close_error).__name__}: {close_error}"
            )
        raise primary_error
    return binding


def verify_child(binding: ChildBinding, *, expected_parent_pid: int) -> ProcessIdentity:
    identity = read_proc_identity(binding.identity.pid)
    if binding.owned_direct and binding.identity.start_time < 0:
        if identity.parent_pid != expected_parent_pid:
            raise RuntimeError(f"RPM lifecycle child PID {binding.identity.pid} changed parent")
        return identity
    if (
        identity.start_time != binding.identity.start_time
        or identity.parent_pid != expected_parent_pid
    ):
        raise RuntimeError(f"RPM lifecycle child PID {binding.identity.pid} changed identity")
    return identity


def signal_pidfd(
    pidfd_send_signal: object,
    binding: ChildBinding,
    signum: int,
    *,
    expected_parent_pid: int | None = None,
    signal_errors: list[BaseException] | None = None,
) -> None:
    """Signal bound child, falling back only after an identity-safe exact-PID check."""
    if binding.pidfd < 0:
        if not binding.owned_direct:
            raise RuntimeError("RPM lifecycle child has no safe signal handle")
        if expected_parent_pid is None:
            raise RuntimeError("RPM lifecycle owned child has no parent identity")
        try:
            identity = verify_child(binding, expected_parent_pid=expected_parent_pid)
        except FileNotFoundError:
            return
        if binding.identity.start_time < 0:
            binding.identity = identity
        try:
            os.kill(binding.identity.pid, signum)
        except ProcessLookupError:
            pass
        return

    signal_error: BaseException | None = None
    try:
        result = pidfd_send_signal(binding.pidfd, signum, None, 0)
        if result == 0:
            return
        error_number = ctypes.get_errno() or errno.EIO
        if error_number == errno.ESRCH:
            return
        signal_error = OSError(error_number, os.strerror(error_number))
    except Exception as exc:
        signal_error = exc

    if binding.owned_direct:
        if expected_parent_pid is None:
            raise RuntimeError("RPM lifecycle owned child has no parent identity")
        try:
            identity = verify_child(binding, expected_parent_pid=expected_parent_pid)
        except FileNotFoundError:
            return
        except BaseException as fallback_error:
            append_bounded_diagnostic(
                signal_errors,
                signal_error,
                label="RPM lifecycle signal",
                limit=MAX_SIGNAL_ERROR_REPORTS,
            )
            fallback_error.add_note(
                "RPM lifecycle pidfd signal failed: "
                f"{describe_error(signal_error)}"
            )
            raise
        if binding.identity.start_time < 0:
            binding.identity = identity
        try:
            os.kill(binding.identity.pid, signum)
        except ProcessLookupError:
            return
        except BaseException as fallback_error:
            append_bounded_diagnostic(
                signal_errors,
                signal_error,
                label="RPM lifecycle signal",
                limit=MAX_SIGNAL_ERROR_REPORTS,
            )
            fallback_error.add_note(
                "RPM lifecycle pidfd signal failed: "
                f"{describe_error(signal_error)}"
            )
            raise
        append_bounded_diagnostic(
            signal_errors,
            signal_error,
            label="RPM lifecycle signal",
            limit=MAX_SIGNAL_ERROR_REPORTS,
        )
        return
    if expected_parent_pid is None:
        raise signal_error
    try:
        verify_child(binding, expected_parent_pid=expected_parent_pid)
    except FileNotFoundError:
        return
    except BaseException as fallback_error:
        append_bounded_diagnostic(
            signal_errors,
            signal_error,
            label="RPM lifecycle signal",
            limit=MAX_SIGNAL_ERROR_REPORTS,
        )
        fallback_error.add_note(
            "RPM lifecycle pidfd signal failed: "
            f"{describe_error(signal_error)}"
        )
        raise
    try:
        os.kill(binding.identity.pid, signum)
    except ProcessLookupError:
        return
    except BaseException as fallback_error:
        append_bounded_diagnostic(
            signal_errors,
            signal_error,
            label="RPM lifecycle signal",
            limit=MAX_SIGNAL_ERROR_REPORTS,
        )
        fallback_error.add_note(
            "RPM lifecycle pidfd signal failed: "
            f"{describe_error(signal_error)}"
        )
        raise
    append_bounded_diagnostic(
        signal_errors,
        signal_error,
        label="RPM lifecycle signal",
        limit=MAX_SIGNAL_ERROR_REPORTS,
    )


def report_signal_errors(signal_errors: list[BaseException]) -> None:
    if not signal_errors:
        return
    details = "; ".join(
        f"{type(error).__name__}: {str(error).replace(chr(10), ' ')[:256]}"
        for error in signal_errors[:MAX_SIGNAL_ERROR_REPORTS]
    )
    print(
        f"RPM lifecycle pidfd signal failed; exact-PID fallback used: {details}",
        file=sys.stderr,
    )


def note_signal_errors(
    target: BaseException, signal_errors: list[BaseException], *, prefix: str
) -> None:
    for error in signal_errors[:MAX_SIGNAL_ERROR_REPORTS]:
        target.add_note(
            f"{prefix}: {type(error).__name__}: "
            f"{str(error).replace(chr(10), ' ')[:256]}"
        )


def report_cleanup_errors(
    cleanup_errors: list[BaseException],
    *,
    prefix: str,
) -> None:
    for error in cleanup_errors[:MAX_CLEANUP_DIAGNOSTICS]:
        print(f"{prefix}: {describe_error(error)}", file=sys.stderr)


def signal_verified_group(binding: ChildBinding, signum: int, *, expected_parent_pid: int) -> bool:
    if binding.identity.start_time < 0:
        return False
    try:
        identity = verify_child(binding, expected_parent_pid=expected_parent_pid)
    except FileNotFoundError:
        return False
    if (
        identity.process_group_id != binding.identity.process_group_id
        or identity.session_id != binding.identity.session_id
    ):
        return False
    try:
        os.killpg(identity.process_group_id, signum)
    except ProcessLookupError:
        pass
    return True


def peek_child(process_id: int) -> object | None:
    try:
        result = os.waitid(
            os.P_PID,
            process_id,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except InterruptedError:
        return None
    if result is None or result.si_pid == 0:
        return None
    if result.si_pid != process_id:
        raise RuntimeError(f"RPM lifecycle wait returned unexpected PID {result.si_pid}")
    return result


def reap_child_nonblocking(process_id: int) -> object | None:
    """Reap an adopted child only when its wait result is already available."""
    try:
        result = os.waitid(os.P_PID, process_id, os.WEXITED | os.WNOHANG)
    except InterruptedError:
        return None
    except ChildProcessError:
        # This authenticated child was already reaped by this supervisor.
        # Explicit sentinel prevents callers from treating it as a wait
        # status; bindings can close without signalling a reused PID.
        return CHILD_ALREADY_REAPED
    if result is None or result.si_pid == 0:
        return None
    if result.si_pid != process_id:
        raise RuntimeError(f"RPM lifecycle wait returned unexpected PID {result.si_pid}")
    return result


def reap_ready_children(
    bindings: dict[int, ChildBinding],
    *,
    root_pid: int,
    close_errors: list[BaseException] | None = None,
    deadline: float | None = None,
) -> set[int]:
    """Reap ready adopted children with one pidfd poll per bounded round."""
    poller = select.poll()
    pidfd_to_pid: dict[int, int] = {}
    for process_id, binding in bindings.items():
        if deadline is not None and not cleanup_budget_available(
            deadline,
            close_errors,
            phase="reap",
        ):
            record_unresolved_children(
                close_errors,
                phase="reap",
                count=sum(pid != root_pid for pid in bindings),
            )
            break
        if process_id == root_pid or binding.pidfd < 0:
            continue
        try:
            poller.register(binding.pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        except BaseException as error:
            if close_errors is None:
                raise
            append_bounded_diagnostic(
                close_errors,
                error,
                label="RPM lifecycle reap",
            )
            continue
        pidfd_to_pid[binding.pidfd] = process_id
    try:
        if deadline is not None and not cleanup_budget_available(
            deadline,
            close_errors,
            phase="reap",
        ):
            return set()
        ready = {
            pidfd_to_pid[descriptor]
            for descriptor, _events in poller.poll(0)
            if descriptor in pidfd_to_pid
        }
    except BaseException as error:
        if close_errors is None:
            raise
        append_bounded_diagnostic(
            close_errors,
            error,
            label="RPM lifecycle reap",
        )
        ready = set()
    reaped: set[int] = set()
    for process_id, binding in list(bindings.items()):
        if deadline is not None and not cleanup_budget_available(
            deadline,
            close_errors,
            phase="reap",
        ):
            record_unresolved_children(
                close_errors,
                phase="reap",
                count=sum(pid != root_pid for pid in bindings),
            )
            break
        if process_id == root_pid:
            continue
        if binding.pidfd < 0:
            ready.add(process_id)
        if process_id not in ready:
            continue
        try:
            result = reap_child_nonblocking(process_id)
        except BaseException as error:
            if close_errors is None:
                raise
            append_bounded_diagnostic(
                close_errors,
                error,
                label="RPM lifecycle reap",
            )
            continue
        if result is not None:
            try:
                close_binding(binding, errors=close_errors)
            except BaseException as error:
                if close_errors is None:
                    raise
                append_bounded_diagnostic(
                    close_errors,
                    error,
                    label="RPM lifecycle reap",
                )
            finally:
                del bindings[process_id]
            reaped.add(process_id)
    return reaped


def reap_child(process_id: int, *, deadline: float | None = None) -> object:
    while True:
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(f"RPM lifecycle reap deadline exceeded for PID {process_id}")
        try:
            result = os.waitid(os.P_PID, process_id, os.WEXITED)
        except InterruptedError:
            yield_after_eintr(deadline)
            continue
        if result is None or result.si_pid != process_id:
            raise RuntimeError(f"RPM lifecycle reap returned unexpected PID {process_id}")
        return result


def status_from_wait_result(result: object) -> int:
    if result is CHILD_ALREADY_REAPED:
        raise RuntimeError("RPM lifecycle wait status is unavailable after prior reap")
    if result.si_code == os.CLD_EXITED:
        return result.si_status
    if result.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
        return -result.si_status
    raise RuntimeError(f"RPM lifecycle returned unsupported wait status {result.si_code}")


def close_binding(
    binding: ChildBinding,
    *,
    errors: list[BaseException] | None = None,
) -> None:
    pidfd = binding.pidfd
    binding.pidfd = -1
    if pidfd < 0:
        return
    try:
        os.close(pidfd)
    except BaseException as error:
        if errors is None:
            raise
        append_bounded_diagnostic(
            errors,
            error,
            label="RPM lifecycle close",
        )


def drain_adopted_children(
    root_binding: ChildBinding,
    bindings: dict[int, ChildBinding],
    pidfd_send_signal: object,
    *,
    cleanup_deadline: float,
    deadline: float,
    signal_errors: list[BaseException] | None = None,
    snapshot_errors: list[BaseException] | None = None,
    cleanup_errors: list[BaseException] | None = None,
) -> bool:
    supervisor_pid = os.getpid()
    diagnostics = cleanup_errors if cleanup_errors is not None else snapshot_errors
    for _ in range(MAX_DRAIN_ROUNDS):
        if not cleanup_budget_available(deadline, diagnostics, phase="adopted drain"):
            record_unresolved_children(
                diagnostics,
                phase="adopted drain",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return False
        direct_set, snapshot_error = read_direct_children_snapshot(
            deadline=direct_children_read_deadline(
                cleanup_deadline=cleanup_deadline,
                deadline=deadline,
            )
        )
        record_direct_children_error(snapshot_error, snapshot_errors)
        if root_binding.identity.pid not in direct_set:
            if snapshot_error is not None:
                # Continue with every authenticated PID already parsed from
                # this failed snapshot.  Root presence cannot be assumed,
                # but observed children must still enter cleanup.
                pass
            else:
                raise RuntimeError("RPM lifecycle root child disappeared before reap")
        reaped_children = reap_ready_children(
            bindings,
            root_pid=root_binding.identity.pid,
            close_errors=cleanup_errors,
            deadline=deadline,
        )
        for batch in iter_child_batches(direct_set):
            for process_id in batch:
                if not cleanup_budget_available(
                    deadline,
                    diagnostics,
                    phase="adopted bind",
                ):
                    record_unresolved_children(
                        diagnostics,
                        phase="adopted bind",
                        count=sum(
                            candidate != root_binding.identity.pid
                            for candidate in direct_set
                        ),
                    )
                    return False
                if process_id == root_binding.identity.pid:
                    continue
                if process_id in reaped_children:
                    continue
                if process_id not in bindings:
                    try:
                        bindings[process_id] = bind_child(
                            process_id,
                            expected_parent_pid=supervisor_pid,
                        )
                    except BaseException as bind_error:
                        if not is_pidfd_resource_error(bind_error):
                            raise
                        # The authenticated children file names only direct
                        # unreaped children of this supervisor.  Such a PID
                        # cannot be reused; parent revalidation below keeps
                        # the exact-PID fallback bound to our child.
                        bindings[process_id] = make_owned_child_binding(
                            process_id,
                            expected_parent_pid=supervisor_pid,
                        )
                        verify_child(
                            bindings[process_id],
                            expected_parent_pid=supervisor_pid,
                        )

        for process_id in list(bindings):
            if not cleanup_budget_available(
                deadline,
                diagnostics,
                phase="adopted reap",
            ):
                record_unresolved_children(
                    diagnostics,
                    phase="adopted reap",
                    count=sum(pid != root_binding.identity.pid for pid in bindings),
                )
                return False
            if process_id == root_binding.identity.pid or process_id in direct_set:
                continue
            binding = bindings[process_id]
            if not cleanup_budget_available(deadline, diagnostics, phase="adopted reap"):
                record_unresolved_children(
                    diagnostics,
                    phase="adopted reap",
                    count=sum(pid != root_binding.identity.pid for pid in bindings),
                )
                return False
            if peek_child(process_id) is None:
                raise RuntimeError(f"RPM lifecycle child PID {process_id} left direct-child set")
            if not cleanup_budget_available(deadline, diagnostics, phase="adopted reap"):
                record_unresolved_children(
                    diagnostics,
                    phase="adopted reap",
                    count=sum(pid != root_binding.identity.pid for pid in bindings),
                )
                return False
            reap_child(process_id, deadline=deadline)
            if not cleanup_budget_available(deadline, diagnostics, phase="adopted close"):
                record_unresolved_children(
                    diagnostics,
                    phase="adopted close",
                    count=sum(pid != root_binding.identity.pid for pid in bindings),
                )
                return False
            close_binding(binding, errors=cleanup_errors)
            del bindings[process_id]

        cleanup_due = snapshot_error is not None or time.monotonic() >= cleanup_deadline
        for batch in iter_child_batches(direct_set):
            for process_id in batch:
                if not cleanup_budget_available(
                    deadline,
                    diagnostics,
                    phase="adopted signal",
                ):
                    record_unresolved_children(
                        diagnostics,
                        phase="adopted signal",
                        count=sum(
                            candidate != root_binding.identity.pid
                            for candidate in bindings
                        ),
                    )
                    return False
                if process_id == root_binding.identity.pid:
                    continue
                binding = bindings.get(process_id)
                if binding is None:
                    continue
                result = None
                if not cleanup_due:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="adopted reap",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="adopted reap",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    result = reap_child_nonblocking(process_id)
                if result is not None:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="adopted close",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="adopted close",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    close_binding(binding, errors=cleanup_errors)
                    del bindings[process_id]
                    continue
                if binding.kill_sent:
                    continue
                if not binding.term_sent and not cleanup_due:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="adopted signal",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="adopted signal",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    signal_pidfd(
                        pidfd_send_signal,
                        binding,
                        signal.SIGTERM,
                        expected_parent_pid=supervisor_pid,
                        signal_errors=signal_errors,
                    )
                    binding.term_sent = True
                if cleanup_due:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="adopted signal",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="adopted signal",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    signal_pidfd(
                        pidfd_send_signal,
                        binding,
                        signal.SIGKILL,
                        expected_parent_pid=supervisor_pid,
                        signal_errors=signal_errors,
                    )
                    binding.kill_sent = True

        # Do not make a large child set wait for another full polling round
        # before KILL becomes effective when the reserve boundary is crossed
        # during binding/stat/signalling.
        if not cleanup_due and time.monotonic() >= cleanup_deadline:
            for batch in iter_child_batches(direct_set):
                for process_id in batch:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="adopted kill",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="adopted kill",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    if process_id == root_binding.identity.pid:
                        continue
                    binding = bindings.get(process_id)
                    if binding is None or binding.kill_sent:
                        continue
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="adopted kill",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="adopted kill",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    signal_pidfd(
                        pidfd_send_signal,
                        binding,
                        signal.SIGKILL,
                        expected_parent_pid=supervisor_pid,
                        signal_errors=signal_errors,
                    )
                    binding.kill_sent = True

        if cleanup_due:
            reap_ready_children(
                bindings,
                root_pid=root_binding.identity.pid,
                close_errors=cleanup_errors,
                deadline=deadline,
            )

        if not cleanup_budget_available(deadline, diagnostics, phase="adopted snapshot"):
            record_unresolved_children(
                diagnostics,
                phase="adopted snapshot",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return False
        final_children, final_error = read_direct_children_snapshot(
            deadline=direct_children_read_deadline(
                cleanup_deadline=cleanup_deadline,
                deadline=deadline,
            )
        )
        record_direct_children_error(final_error, snapshot_errors)
        if (
            snapshot_error is None
            and final_error is None
            and final_children == {root_binding.identity.pid}
            and set(bindings) == {root_binding.identity.pid}
            and bindings.get(root_binding.identity.pid) is root_binding
        ):
            # Let kernel reparenting settle before root can be reaped.  This
            # second bounded observation prevents a late adopted child from
            # appearing after the root PID has become reusable.
            remaining = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS) - time.monotonic()
            if remaining > 0 and cleanup_budget_available(
                deadline,
                diagnostics,
                phase="adopted settle",
            ):
                time.sleep(remaining)
            elif remaining > 0:
                return False
            if not cleanup_budget_available(deadline, diagnostics, phase="adopted snapshot"):
                return False
            settled_children, settled_error = read_direct_children_snapshot(
                deadline=direct_children_read_deadline(
                    cleanup_deadline=cleanup_deadline,
                    deadline=deadline,
                )
            )
            record_direct_children_error(settled_error, snapshot_errors)
            if (
                snapshot_error is None
                and settled_error is None
                and settled_children == {root_binding.identity.pid}
                and set(bindings) == {root_binding.identity.pid}
                and bindings.get(root_binding.identity.pid) is root_binding
            ):
                return True
        remaining = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS) - time.monotonic()
        if remaining > 0 and cleanup_budget_available(
            deadline,
            diagnostics,
            phase="adopted drain wait",
        ):
            time.sleep(remaining)
        elif remaining > 0:
            record_unresolved_children(
                diagnostics,
                phase="adopted drain wait",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return False
    return False


def emergency_direct_cleanup(
    root_binding: ChildBinding,
    bindings: dict[int, ChildBinding],
    pidfd_send_signal: object,
    *,
    deadline: float,
    signal_errors: list[BaseException] | None = None,
    snapshot_errors: list[BaseException] | None = None,
    cleanup_errors: list[BaseException] | None = None,
) -> bool:
    """Kill only direct children observed through the subreaper children file."""
    supervisor_pid = os.getpid()
    diagnostics = cleanup_errors if cleanup_errors is not None else snapshot_errors
    clean_stable_rounds = 0
    for _ in range(MAX_DRAIN_ROUNDS):
        if not cleanup_budget_available(deadline, diagnostics, phase="emergency drain"):
            record_unresolved_children(
                diagnostics,
                phase="emergency drain",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return False
        try:
            direct_children, snapshot_error = read_direct_children_snapshot(
                deadline=direct_children_read_deadline(
                    cleanup_deadline=deadline,
                    deadline=deadline,
                )
            )
        except BaseException as error:
            record_direct_children_error(error, cleanup_errors)
            return False
        record_direct_children_error(snapshot_error, snapshot_errors)
        if root_binding.identity.pid not in direct_children:
            if snapshot_error is None:
                return False
            # The read failed after a partial authenticated snapshot.  Cleanup
            # every PID we did observe even though root presence is unknown.
        reaped_children = reap_ready_children(
            bindings,
            root_pid=root_binding.identity.pid,
            close_errors=cleanup_errors,
            deadline=deadline,
        )
        for batch in iter_child_batches(direct_children):
            for process_id in batch:
                if not cleanup_budget_available(
                    deadline,
                    diagnostics,
                    phase="emergency bind",
                ):
                    record_unresolved_children(
                        diagnostics,
                        phase="emergency bind",
                        count=sum(
                            candidate != root_binding.identity.pid
                            for candidate in direct_children
                        ),
                    )
                    return False
                if process_id == root_binding.identity.pid:
                    continue
                if process_id in reaped_children:
                    continue
                binding = bindings.get(process_id)
                if binding is None:
                    try:
                        binding = bind_child(process_id, expected_parent_pid=supervisor_pid)
                    except BaseException as bind_error:
                        if not is_pidfd_resource_error(bind_error):
                            if cleanup_errors is not None:
                                append_bounded_diagnostic(
                                    cleanup_errors,
                                    bind_error,
                                    label="RPM lifecycle emergency binding",
                                )
                            continue
                        # Direct-child ownership is independent of root's
                        # pidfd binding mode.  Keep cleanup exact-PID bound
                        # when pidfd acquisition is temporarily unavailable.
                        try:
                            binding = make_owned_child_binding(
                                process_id,
                                expected_parent_pid=supervisor_pid,
                            )
                        except BaseException as fallback_error:
                            if cleanup_errors is not None:
                                append_bounded_diagnostic(
                                    cleanup_errors,
                                    fallback_error,
                                    label="RPM lifecycle emergency binding",
                                )
                            continue
                    bindings[process_id] = binding
                if not cleanup_budget_available(
                    deadline,
                    diagnostics,
                    phase="emergency signal",
                ):
                    record_unresolved_children(
                        diagnostics,
                        phase="emergency signal",
                        count=sum(
                            candidate != root_binding.identity.pid
                            for candidate in bindings
                        ),
                    )
                    return False
                try:
                    signal_pidfd(
                        pidfd_send_signal,
                        binding,
                        signal.SIGKILL,
                        expected_parent_pid=supervisor_pid,
                        signal_errors=signal_errors,
                    )
                    binding.kill_sent = True
                except BaseException as signal_error:
                    if cleanup_errors is not None:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            signal_error,
                            label="RPM lifecycle emergency signal",
                        )
                    continue
                if not cleanup_budget_available(
                    deadline,
                    diagnostics,
                    phase="emergency reap",
                ):
                    record_unresolved_children(
                        diagnostics,
                        phase="emergency reap",
                        count=sum(
                            candidate != root_binding.identity.pid
                            for candidate in bindings
                        ),
                    )
                    return False
                try:
                    result = reap_child_nonblocking(process_id)
                except BaseException as reap_error:
                    if cleanup_errors is not None:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            reap_error,
                            label="RPM lifecycle emergency reap",
                        )
                    continue
                if result is not None:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="emergency close",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="emergency close",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    try:
                        close_binding(binding, errors=cleanup_errors)
                    except BaseException as close_error:
                        if cleanup_errors is not None:
                            append_bounded_diagnostic(
                                cleanup_errors,
                                close_error,
                                label="RPM lifecycle emergency close",
                            )
                    del bindings[process_id]
        for process_id in list(bindings):
            if not cleanup_budget_available(
                deadline,
                diagnostics,
                phase="emergency known-child cleanup",
            ):
                record_unresolved_children(
                    diagnostics,
                    phase="emergency known-child cleanup",
                    count=sum(pid != root_binding.identity.pid for pid in bindings),
                )
                return False
            if process_id == root_binding.identity.pid or process_id in direct_children:
                continue
            binding = bindings[process_id]
            try:
                result = reap_child_nonblocking(process_id)
                if result is None:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="emergency signal",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="emergency signal",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    signal_pidfd(
                        pidfd_send_signal,
                        binding,
                        signal.SIGKILL,
                        expected_parent_pid=supervisor_pid,
                        signal_errors=signal_errors,
                    )
                    binding.kill_sent = True
                    result = reap_child_nonblocking(process_id)
                if result is not None:
                    if not cleanup_budget_available(
                        deadline,
                        diagnostics,
                        phase="emergency close",
                    ):
                        record_unresolved_children(
                            diagnostics,
                            phase="emergency close",
                            count=sum(
                                candidate != root_binding.identity.pid
                                for candidate in bindings
                            ),
                        )
                        return False
                    close_binding(binding, errors=cleanup_errors)
                    del bindings[process_id]
            except BaseException as child_error:
                if cleanup_errors is not None:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        child_error,
                        label="RPM lifecycle emergency child cleanup",
                    )
                continue
        try:
            if not cleanup_budget_available(
                deadline,
                diagnostics,
                phase="emergency final snapshot",
            ):
                record_unresolved_children(
                    diagnostics,
                    phase="emergency final snapshot",
                    count=sum(pid != root_binding.identity.pid for pid in bindings),
                )
                return False
            final_children, final_error = read_direct_children_snapshot(
                deadline=direct_children_read_deadline(
                    cleanup_deadline=deadline,
                    deadline=deadline,
                )
            )
        except BaseException as error:
            record_direct_children_error(error, cleanup_errors)
            return False
        record_direct_children_error(final_error, snapshot_errors)
        if (
            snapshot_error is None
            and final_error is None
            and final_children == {root_binding.identity.pid}
            and set(bindings) == {root_binding.identity.pid}
            and bindings.get(root_binding.identity.pid) is root_binding
        ):
            clean_stable_rounds += 1
            if clean_stable_rounds >= 2:
                return True
        else:
            clean_stable_rounds = 0
        remaining = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS) - time.monotonic()
        if remaining > 0 and cleanup_budget_available(
            deadline,
            diagnostics,
            phase="emergency drain wait",
        ):
            time.sleep(remaining)
        elif remaining > 0:
            record_unresolved_children(
                diagnostics,
                phase="emergency drain wait",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return False
    return False


def finalize_adopted_children(
    bindings: dict[int, ChildBinding],
    pidfd_send_signal: object,
    *,
    root_pid: int,
    expected_parent_pid: int,
    deadline: float,
    signal_errors: list[BaseException],
) -> tuple[set[int], list[BaseException]]:
    """Kill and reap known adopted children without claiming unconfirmed work."""
    cleanup_errors: list[BaseException] = []
    for process_id, binding in list(bindings.items()):
        if process_id == root_pid:
            continue
        if not cleanup_budget_available(deadline, cleanup_errors, phase="finalizer signal"):
            record_unresolved_children(
                cleanup_errors,
                phase="finalizer signal",
                count=sum(pid != root_pid for pid in bindings),
            )
            break
        try:
            signal_pidfd(
                pidfd_send_signal,
                binding,
                signal.SIGKILL,
                expected_parent_pid=expected_parent_pid,
                signal_errors=signal_errors,
            )
        except BaseException as error:
            append_bounded_diagnostic(
                cleanup_errors,
                error,
                label="RPM lifecycle finalizer signal",
            )

    for _ in range(MAX_DRAIN_ROUNDS):
        if not cleanup_budget_available(deadline, cleanup_errors, phase="finalizer reap"):
            record_unresolved_children(
                cleanup_errors,
                phase="finalizer reap",
                count=sum(pid != root_pid for pid in bindings),
            )
            break
        before = len(bindings)
        try:
            reap_ready_children(
                bindings,
                root_pid=root_pid,
                close_errors=cleanup_errors,
                deadline=deadline,
            )
        except BaseException as error:
            append_bounded_diagnostic(
                cleanup_errors,
                error,
                label="RPM lifecycle finalizer reap",
            )
            for process_id, binding in list(bindings.items()):
                if process_id == root_pid:
                    continue
                if not cleanup_budget_available(
                    deadline,
                    cleanup_errors,
                    phase="finalizer fallback reap",
                ):
                    record_unresolved_children(
                        cleanup_errors,
                        phase="finalizer fallback reap",
                        count=sum(pid != root_pid for pid in bindings),
                    )
                    break
                try:
                    result = reap_child_nonblocking(process_id)
                except BaseException as child_error:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        child_error,
                        label="RPM lifecycle finalizer fallback reap",
                    )
                    continue
                if result is not None:
                    try:
                        close_binding(binding, errors=cleanup_errors)
                    finally:
                        del bindings[process_id]
        remaining_children = any(process_id != root_pid for process_id in bindings)
        if not remaining_children:
            break
        if len(bindings) == before:
            remaining = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS) - time.monotonic()
            if remaining > 0 and cleanup_budget_available(
                deadline,
                cleanup_errors,
                phase="finalizer wait",
            ):
                time.sleep(remaining)
            elif remaining > 0:
                record_unresolved_children(
                    cleanup_errors,
                    phase="finalizer wait",
                    count=sum(pid != root_pid for pid in bindings),
                )
                break
    return (
        {process_id for process_id in bindings if process_id != root_pid},
        cleanup_errors,
    )


def run_bounded_identity(command: list[str], *, timeout: float, max_bytes: int) -> str:
    """Run trusted identity helper and emit only one small validated identity."""
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or max_bytes <= 0
        or max_bytes > MAX_IDENTITY_OUTPUT_BYTES
    ):
        raise ValueError("identity timeout and byte limit must be positive")
    deadline = time.monotonic() + timeout
    grace = min(0.2, max(0.01, timeout / 4))
    terminate_at = deadline - grace - direct_children_cleanup_reserve(timeout) - (
        2 * POLL_INTERVAL_SECONDS
    )
    pidfd_send_signal = require_kernel_primitives()
    validate_pidfd_send_signal(pidfd_send_signal)
    set_child_subreaper()
    supervisor_pid = os.getpid()
    previous_handlers: dict[int, object] = {}
    installed_handlers: list[int] = []
    received_signal: list[int | None] = [None]
    child: subprocess.Popen[bytes] | None = None
    root_binding: ChildBinding | None = None
    bindings: dict[int, ChildBinding] = {}
    root_result: object | None = None
    root_reaped = False
    output = bytearray()
    error_output = bytearray()
    output_fd: int | None = None
    error_fd: int | None = None
    poller = select.poll()
    streams: dict[int, bytearray] = {}
    stream_limits: dict[int, int] = {}
    term_sent = False
    kill_sent = False
    timed_out = False
    drained = False
    failure: BaseException | None = None
    signal_errors: list[BaseException] = []
    snapshot_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []
    cleanup_deadline = deadline
    baseline_children: set[int] | None = None
    try:
        baseline, baseline_error = read_direct_children_snapshot(
            deadline=time.monotonic() + POLL_INTERVAL_SECONDS
        )
        if baseline_error is None:
            baseline_children = baseline
    except BaseException:
        baseline_children = None

    def snapshot_primary(current: BaseException | None) -> BaseException | None:
        if not snapshot_errors:
            return current
        primary = snapshot_errors[0]
        for secondary in snapshot_errors[1:]:
            if secondary is not primary:
                primary.add_note(
                    "RPM identity additional snapshot failure: "
                    f"{type(secondary).__name__}: "
                    f"{str(secondary).replace(chr(10), ' ')[:256]}"
                )
        if current is not None and current is not primary:
            primary.add_note(
                "RPM identity cleanup error: "
                f"{type(current).__name__}: {str(current).replace(chr(10), ' ')[:256]}"
            )
        return primary

    def remember_signal(signum: int, _frame: object) -> None:
        if received_signal[0] is None:
            received_signal[0] = signum

    def signal_child(signum: int) -> None:
        if root_binding is None:
            return
        if not signal_verified_group(
            root_binding,
            signum,
            expected_parent_pid=supervisor_pid,
        ):
            signal_pidfd(
                pidfd_send_signal,
                root_binding,
                signum,
                expected_parent_pid=supervisor_pid,
                signal_errors=signal_errors,
            )

    try:
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP if hasattr(signal, "SIGHUP") else None):
            if signum is None:
                continue
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, remember_signal)
            installed_handlers.append(signum)
        ensure_launch_budget(terminate_at, phase="RPM identity helper")
        child = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            close_fds=True,
        )
        ensure_launch_budget(terminate_at, phase="RPM identity helper bind")
        try:
            ensure_launch_budget(terminate_at, phase="RPM identity helper bind")
            root_binding = bind_child(child.pid, expected_parent_pid=supervisor_pid)
        except BaseException as bind_error:
            if is_pidfd_resource_error(bind_error):
                try:
                    root_binding = bind_recovered_child(
                        child.pid,
                        expected_parent_pid=supervisor_pid,
                        deadline=terminate_at,
                    )
                    bindings[child.pid] = root_binding
                except BaseException as recovery_error:
                    bind_error.add_note(
                        "RPM identity recovery bind failed: "
                        f"{describe_error(recovery_error)}"
                    )
            raise
        bindings[child.pid] = root_binding
        ensure_launch_budget(terminate_at, phase="RPM identity helper bind")
        if (
            root_binding.identity.process_group_id != child.pid
            or root_binding.identity.session_id != child.pid
        ):
            raise RuntimeError("RPM identity helper process group identity is invalid")
        if child.stdout is None or child.stderr is None:
            raise RuntimeError("RPM identity helper pipes are unavailable")
        output_fd = child.stdout.fileno()
        error_fd = child.stderr.fileno()
        for descriptor, buffer in ((output_fd, output), (error_fd, error_output)):
            os.set_blocking(descriptor, False)
            poller.register(descriptor, select.POLLIN | select.POLLHUP | select.POLLERR)
            streams[descriptor] = buffer
            stream_limits[descriptor] = max_bytes if buffer is output else IDENTITY_STDERR_MAX_BYTES

        while True:
            now = time.monotonic()
            if now >= deadline and failure is None:
                failure = TimeoutError("RPM identity helper deadline exceeded")
            if now >= terminate_at:
                # Reserve boundary is itself a timeout.  A child that only
                # produces valid output from its TERM handler cannot turn a
                # deadline-driven termination into success.
                timed_out = True
            if (
                failure is not None
                or received_signal[0] is not None
                or now >= terminate_at
            ) and not term_sent:
                signal_child(signal.SIGTERM)
                term_sent = True
                cleanup_deadline = min(deadline, now + grace)
            if term_sent and now >= cleanup_deadline and not kill_sent:
                signal_child(signal.SIGKILL)
                kill_sent = True
                if failure is None and received_signal[0] is None:
                    timed_out = True

            for descriptor, _events in poller.poll(0):
                buffer = streams[descriptor]
                limit = stream_limits[descriptor]
                try:
                    chunk = os.read(descriptor, min(8192, limit + 1 - len(buffer)))
                except BlockingIOError:
                    continue
                except InterruptedError:
                    yield_after_eintr(deadline)
                    continue
                except OSError as stream_error:
                    stream_failure = RuntimeError("RPM identity helper pipe failed")
                    stream_failure.__cause__ = stream_error
                    stream_failure.add_note(
                        "RPM identity helper pipe read failed: "
                        f"{type(stream_error).__name__}: "
                        f"{str(stream_error).replace(chr(10), ' ')[:256]}"
                    )
                    if failure is None:
                        failure = stream_failure
                    else:
                        failure.add_note(
                            "RPM identity helper pipe read failed: "
                            f"{type(stream_error).__name__}: "
                            f"{str(stream_error).replace(chr(10), ' ')[:256]}"
                        )
                    append_bounded_diagnostic(
                        cleanup_errors,
                        stream_failure,
                        label="RPM identity cleanup",
                    )
                    try:
                        poller.unregister(descriptor)
                    except KeyError:
                        pass
                    streams.pop(descriptor, None)
                    continue
                if chunk:
                    buffer.extend(chunk)
                    if len(buffer) > limit:
                        failure = failure or RuntimeError("RPM identity helper output exceeds byte limit")
                else:
                    try:
                        poller.unregister(descriptor)
                    except KeyError:
                        pass
                    streams.pop(descriptor, None)

            root_result = peek_child(root_binding.identity.pid)
            if root_result is not None and not drained:
                if not term_sent:
                    cleanup_deadline = min(deadline, time.monotonic() + grace)
                try:
                    drained = drain_adopted_children(
                        root_binding,
                        bindings,
                        pidfd_send_signal,
                        cleanup_deadline=cleanup_deadline,
                        deadline=deadline,
                        signal_errors=signal_errors,
                        snapshot_errors=snapshot_errors,
                        cleanup_errors=cleanup_errors,
                    )
                except BaseException as drain_error:
                    failure = failure or drain_error
                    append_bounded_diagnostic(
                        cleanup_errors,
                        drain_error,
                        label="RPM identity drain",
                    )
                    try:
                        signal_verified_group(
                            root_binding,
                            signal.SIGKILL,
                            expected_parent_pid=supervisor_pid,
                        )
                    except BaseException as signal_error:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            signal_error,
                            label="RPM identity emergency signal",
                        )
                    try:
                        drained = emergency_direct_cleanup(
                            root_binding,
                            bindings,
                            pidfd_send_signal,
                            deadline=deadline,
                            signal_errors=signal_errors,
                            snapshot_errors=snapshot_errors,
                            cleanup_errors=cleanup_errors,
                        )
                    except BaseException as emergency_error:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            emergency_error,
                            label="RPM identity emergency drain",
                        )
                        drained = False
            if root_result is not None and drained and not streams:
                reap_result = reap_child(root_binding.identity.pid, deadline=deadline)
                child.returncode = status_from_wait_result(reap_result)
                root_reaped = True
                failure = snapshot_primary(failure)
                break
            if time.monotonic() >= deadline:
                failure = snapshot_primary(
                    failure
                    or TimeoutError("RPM identity helper cleanup deadline exceeded")
                )
                raise failure
            wait_seconds = min(POLL_INTERVAL_SECONDS, deadline - time.monotonic())
            if wait_seconds > 0:
                if streams:
                    poller.poll(max(1, int(wait_seconds * 1000)))
                else:
                    time.sleep(wait_seconds)

        root_status = status_from_wait_result(root_result)
        failure = snapshot_primary(failure)
        if received_signal[0] is not None:
            raise SupervisorInterrupted(received_signal[0])
        if failure is not None:
            raise failure
        if timed_out:
            raise snapshot_primary(TimeoutError("RPM identity helper deadline exceeded"))
        if root_status != 0:
            detail = error_output.decode("utf-8", "replace").strip()
            raise RuntimeError(f"RPM identity helper failed with status {root_status}: {detail}")
        if not IDENTITY_PATTERN.fullmatch(output):
            raise RuntimeError("RPM identity helper returned invalid identity")
        return output.rstrip(b"\n").decode("ascii")
    finally:
        primary_error = sys.exc_info()[1]
        snapshot_primary_error = snapshot_errors[0] if snapshot_errors else None
        note_target = snapshot_primary_error or primary_error
        adopted_cleanup_errors = cleanup_errors
        unreaped_adopted: set[int] = set()
        if child is not None and not root_reaped:
            # A delayed bind/stream setup can cross the operation deadline only
            # after Popen has created an owned child.  Reserve a fresh bounded
            # cleanup window so launch failure cannot strand that child.
            deadline = max(
                deadline,
                time.monotonic()
                + max(grace, direct_children_cleanup_reserve(timeout)),
            )
        if child is not None and root_binding is None:
            try:
                root_binding = make_verified_owned_child_binding(
                    child.pid,
                    expected_parent_pid=supervisor_pid,
                    deadline=deadline,
                )
                bindings[child.pid] = root_binding
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM identity root binding",
                )
                try:
                    root_binding = bind_unbound_popen_root_for_cleanup(
                        child,
                        expected_parent_pid=supervisor_pid,
                        deadline=deadline,
                    )
                    bindings[child.pid] = root_binding
                except BaseException as unbound_error:
                    append_bounded_diagnostic(
                        adopted_cleanup_errors,
                        unbound_error,
                        label="RPM identity unbound root",
                    )
                    try:
                        root_reaped = cleanup_unbound_popen_root(
                            child,
                            expected_parent_pid=supervisor_pid,
                            pidfd_send_signal=pidfd_send_signal,
                            bindings=bindings,
                            baseline_children=baseline_children,
                            deadline=deadline,
                            signal_errors=signal_errors,
                            cleanup_errors=adopted_cleanup_errors,
                        )
                    except BaseException as direct_cleanup_error:
                        append_bounded_diagnostic(
                            adopted_cleanup_errors,
                            direct_cleanup_error,
                            label="RPM identity unbound root cleanup",
                        )
        if root_binding is not None and not root_reaped:
            try:
                signal_child(signal.SIGKILL)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM identity root cleanup",
                )
            for _ in range(MAX_DRAIN_ROUNDS):
                if time.monotonic() >= deadline:
                    break
                try:
                    root_result = peek_child(root_binding.identity.pid)
                except BaseException as cleanup_error:
                    append_bounded_diagnostic(
                        adopted_cleanup_errors,
                        cleanup_error,
                        label="RPM identity root reap",
                    )
                    root_result = None
                if root_result is not None:
                    if not drained:
                        try:
                            drained = drain_adopted_children(
                                root_binding,
                                bindings,
                                pidfd_send_signal,
                                cleanup_deadline=time.monotonic(),
                                deadline=deadline,
                                signal_errors=signal_errors,
                                snapshot_errors=snapshot_errors,
                                cleanup_errors=adopted_cleanup_errors,
                            )
                        except BaseException as drain_error:
                            append_bounded_diagnostic(
                                adopted_cleanup_errors,
                                drain_error,
                                label="RPM identity emergency drain",
                            )
                            try:
                                signal_verified_group(
                                    root_binding,
                                    signal.SIGKILL,
                                    expected_parent_pid=supervisor_pid,
                                )
                            except BaseException as signal_error:
                                append_bounded_diagnostic(
                                    adopted_cleanup_errors,
                                    signal_error,
                                    label="RPM identity emergency signal",
                                )
                            try:
                                drained = emergency_direct_cleanup(
                                    root_binding,
                                    bindings,
                                    pidfd_send_signal,
                                    deadline=deadline,
                                    signal_errors=signal_errors,
                                    snapshot_errors=snapshot_errors,
                                    cleanup_errors=adopted_cleanup_errors,
                                )
                            except BaseException as cleanup_error:
                                append_bounded_diagnostic(
                                    adopted_cleanup_errors,
                                    cleanup_error,
                                    label="RPM identity emergency drain",
                                )
                                drained = False
                    if drained:
                        try:
                            reap_result = reap_child_nonblocking(root_binding.identity.pid)
                        except BaseException as cleanup_error:
                            append_bounded_diagnostic(
                                adopted_cleanup_errors,
                                cleanup_error,
                                label="RPM identity root reap",
                            )
                        else:
                            if reap_result is None:
                                continue
                            if reap_result is CHILD_ALREADY_REAPED:
                                if child is not None:
                                    child.returncode = status_from_wait_result(root_result)
                            elif child is not None:
                                child.returncode = status_from_wait_result(reap_result)
                            root_reaped = True
                            break
                remaining = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS) - time.monotonic()
                if remaining > 0:
                    time.sleep(remaining)
        if root_binding is not None:
            try:
                unreaped_adopted, final_cleanup_errors = finalize_adopted_children(
                    bindings,
                    pidfd_send_signal,
                    root_pid=root_binding.identity.pid,
                    expected_parent_pid=supervisor_pid,
                    deadline=deadline,
                    signal_errors=signal_errors,
                )
                for final_cleanup_error in final_cleanup_errors:
                    append_bounded_diagnostic(
                        adopted_cleanup_errors,
                        final_cleanup_error,
                        label="RPM identity finalizer",
                    )
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM identity finalizer",
                )
        if (
            root_binding is not None
            and not root_reaped
            and snapshot_errors
            and not any(process_id != root_binding.identity.pid for process_id in bindings)
        ):
            # All known adopted bindings are gone, but failed snapshots never
            # prove that no unknown child existed.  Reap the authenticated
            # zombie to avoid a test/process leak while reporting the drain as
            # unconfirmed; this is not a successful root-only drain.
            try:
                root_result = peek_child(root_binding.identity.pid)
                if root_result is not None:
                    reap_result = reap_child_nonblocking(root_binding.identity.pid)
                    if reap_result is not None:
                        root_reaped = True
                        if child is not None and child.returncode is None:
                            child.returncode = status_from_wait_result(
                                root_result if reap_result is CHILD_ALREADY_REAPED else reap_result
                            )
                        append_bounded_diagnostic(
                            adopted_cleanup_errors,
                            RuntimeError(
                                "RPM identity root reaped with unconfirmed adopted-child "
                                "drain after direct-child snapshot failure"
                            ),
                            label="RPM identity root cleanup",
                        )
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM identity snapshot cleanup",
                )
        for process_id, binding in list(bindings.items()):
            if root_binding is not None and process_id == root_binding.identity.pid:
                continue
            try:
                close_binding(binding, errors=adopted_cleanup_errors)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM identity close",
                )
            del bindings[process_id]
        if root_binding is not None:
            try:
                close_binding(root_binding, errors=adopted_cleanup_errors)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM identity root close",
                )
        if snapshot_primary_error is None and snapshot_errors:
            snapshot_primary_error = snapshot_errors[0]
            if primary_error is not None:
                for note in getattr(primary_error, "__notes__", ()):
                    snapshot_primary_error.add_note(note)
            note_target = snapshot_primary_error
        if unreaped_adopted:
            unreaped_error = RuntimeError(
                "RPM identity cleanup left unconfirmed adopted children: "
                f"{len(unreaped_adopted)}"
            )
            append_bounded_diagnostic(
                adopted_cleanup_errors,
                unreaped_error,
                label="RPM identity cleanup",
            )
        if root_binding is not None and not root_reaped:
            append_bounded_diagnostic(
                adopted_cleanup_errors,
                RuntimeError(
                    "RPM identity cleanup left root child unconfirmed: "
                    f"{root_binding.identity.pid}"
                ),
                label="RPM identity cleanup",
            )
        for stream in (getattr(child, "stdout", None), getattr(child, "stderr", None)):
            if stream is not None:
                try:
                    stream.close()
                except BaseException as cleanup_error:
                    append_bounded_diagnostic(
                        adopted_cleanup_errors,
                        cleanup_error,
                        label="RPM identity stream close",
                    )
        handler_restore_errors = []
        for signum in reversed(installed_handlers):
            try:
                signal.signal(signum, previous_handlers[signum])
            except BaseException as cleanup_error:
                handler_restore_errors.append(cleanup_error)
        if handler_restore_errors:
            for handler_error in handler_restore_errors:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    handler_error,
                    label="RPM identity handler restore",
                )
        for cleanup_error in adopted_cleanup_errors:
            if note_target is not None:
                note_target.add_note(
                    f"RPM identity adopted-child cleanup failed: {cleanup_error}"
                )
        if signal_errors:
            if note_target is not None:
                note_signal_errors(
                    note_target,
                    signal_errors,
                    prefix="RPM identity pidfd signal failed; exact-PID fallback used",
                )
            else:
                report_signal_errors(signal_errors)
        if adopted_cleanup_errors and note_target is None:
            primary_cleanup_error = adopted_cleanup_errors[0]
            for cleanup_error in adopted_cleanup_errors[1:]:
                primary_cleanup_error.add_note(
                    f"RPM identity additional cleanup failure: {cleanup_error}"
                )
            raise primary_cleanup_error
        if snapshot_primary_error is not None and primary_error is not snapshot_primary_error:
            if primary_error is not None:
                snapshot_primary_error.add_note(
                    "RPM identity primary failure: "
                    f"{type(primary_error).__name__}: "
                    f"{str(primary_error).replace(chr(10), ' ')[:256]}"
                )
            raise snapshot_primary_error


def read_handoff_token(*, handoff_fd: int, timeout: float, max_bytes: int) -> bool:
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or max_bytes <= 0
        or max_bytes > MAX_HANDOFF_BYTES
    ):
        return False
    try:
        os.fstat(handoff_fd)
        os.set_blocking(handoff_fd, False)
    except OSError:
        return False

    deadline = time.monotonic() + timeout
    payload = bytearray()
    while len(payload) < max_bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            chunk = os.read(handoff_fd, max_bytes - len(payload))
        except BlockingIOError:
            try:
                readable, _, _ = select.select([handoff_fd], [], [], remaining)
            except (OSError, ValueError):
                return False
            if not readable:
                return False
            continue
        except OSError:
            return False
        if not chunk:
            return bool(HANDOFF_TOKEN_PATTERN.fullmatch(payload))
        payload.extend(chunk)
    return False


def check_handoff_fd(*, handoff_fd: int, timeout: float, max_bytes: int) -> int:
    print(
        "valid"
        if read_handoff_token(
            handoff_fd=handoff_fd,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        else "invalid"
    )
    return 0


def run(command: list[str], *, timeout: float, kill_after: float, handoff_fd: int) -> int:
    if (
        not math.isfinite(timeout)
        or not math.isfinite(kill_after)
        or timeout <= 0
        or kill_after <= 0
        or timeout <= kill_after + REAP_RESERVE_SECONDS
    ):
        raise ValueError("lifecycle timeout must exceed positive kill grace")

    deadline = time.monotonic() + timeout
    terminate_at = deadline - kill_after - direct_children_cleanup_reserve(timeout) - (
        2 * POLL_INTERVAL_SECONDS
    )
    pidfd_send_signal = None
    supervisor_pid = os.getpid()
    previous_handlers: dict[int, object] = {}
    installed_handlers: list[int] = []
    child: subprocess.Popen[bytes] | None = None
    root_binding: ChildBinding | None = None
    bindings: dict[int, ChildBinding] = {}
    root_result: object | None = None
    root_status: int | None = None
    root_reaped = False
    termination_started = False
    root_term_sent = False
    root_kill_sent = False
    group_verified = True
    cleanup_deadline = deadline
    timed_out = False
    received_signal: list[int | None] = [None]
    signal_errors: list[BaseException] = []
    snapshot_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []
    baseline_children: set[int] | None = None
    try:
        baseline, baseline_error = read_direct_children_snapshot(
            deadline=time.monotonic() + POLL_INTERVAL_SECONDS
        )
        if baseline_error is None:
            baseline_children = baseline
    except BaseException:
        baseline_children = None
    result_status: int | None = None
    primary_cleanup_error: BaseException | None = None

    def snapshot_primary(current: BaseException | None) -> BaseException | None:
        if not snapshot_errors:
            return current
        primary = snapshot_errors[0]
        for secondary in snapshot_errors[1:]:
            if secondary is not primary:
                primary.add_note(
                    "RPM lifecycle additional snapshot failure: "
                    f"{type(secondary).__name__}: "
                    f"{str(secondary).replace(chr(10), ' ')[:256]}"
                )
        if current is not None and current is not primary:
            primary.add_note(
                "RPM lifecycle cleanup error: "
                f"{type(current).__name__}: {str(current).replace(chr(10), ' ')[:256]}"
            )
        return primary

    def remember_signal(signum: int, _frame: object) -> None:
        if received_signal[0] is None:
            received_signal[0] = signum

    def start_termination(now: float, *, timeout_triggered: bool) -> None:
        nonlocal termination_started, cleanup_deadline, timed_out
        if termination_started:
            return
        termination_started = True
        timed_out = timeout_triggered
        cleanup_deadline = min(deadline - REAP_RESERVE_SECONDS, now + kill_after)

    def signal_root(signum: int) -> None:
        nonlocal group_verified
        if root_binding is None:
            raise RuntimeError("RPM lifecycle root is not bound")
        if group_verified:
            group_verified = signal_verified_group(
                root_binding,
                signum,
                expected_parent_pid=supervisor_pid,
            )
        if not group_verified:
            signal_pidfd(
                pidfd_send_signal,
                root_binding,
                signum,
                expected_parent_pid=supervisor_pid,
                signal_errors=signal_errors,
            )

    def emergency_reap_root() -> None:
        nonlocal root_result, root_reaped
        if root_binding is None or root_reaped or pidfd_send_signal is None:
            return
        emergency_children_attempted = False
        # Root stays unreaped.  Its bound start time keeps this group identity
        # valid until all adopted children have been drained.
        if not cleanup_budget_available(deadline, cleanup_errors, phase="root signal"):
            record_unresolved_children(
                cleanup_errors,
                phase="root signal",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return
        try:
            signal_verified_group(
                root_binding,
                signal.SIGKILL,
                expected_parent_pid=supervisor_pid,
            )
        except BaseException as signal_error:
            append_bounded_diagnostic(
                cleanup_errors,
                signal_error,
                label="RPM lifecycle root signal",
            )
        if not cleanup_budget_available(deadline, cleanup_errors, phase="root signal"):
            record_unresolved_children(
                cleanup_errors,
                phase="root signal",
                count=sum(process_id != root_binding.identity.pid for process_id in bindings),
            )
            return
        try:
            signal_pidfd(
                pidfd_send_signal,
                root_binding,
                signal.SIGKILL,
                expected_parent_pid=supervisor_pid,
                signal_errors=signal_errors,
            )
        except BaseException as signal_error:
            append_bounded_diagnostic(
                cleanup_errors,
                signal_error,
                label="RPM lifecycle root signal",
            )
        for _ in range(MAX_DRAIN_ROUNDS):
            if not cleanup_budget_available(deadline, cleanup_errors, phase="root reap"):
                record_unresolved_children(
                    cleanup_errors,
                    phase="root reap",
                    count=sum(process_id != root_binding.identity.pid for process_id in bindings),
                )
                return
            try:
                root_result = peek_child(root_binding.identity.pid)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle root reap",
                )
                root_result = None
            if root_result is None and not emergency_children_attempted:
                emergency_children_attempted = True
                try:
                    emergency_direct_cleanup(
                        root_binding,
                        bindings,
                        pidfd_send_signal,
                        deadline=deadline,
                        signal_errors=signal_errors,
                        snapshot_errors=snapshot_errors,
                        cleanup_errors=cleanup_errors,
                    )
                except BaseException as cleanup_error:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        cleanup_error,
                        label="RPM lifecycle emergency drain",
                    )
            if root_result is not None:
                try:
                    drained = drain_adopted_children(
                        root_binding,
                        bindings,
                        pidfd_send_signal,
                        cleanup_deadline=time.monotonic(),
                        deadline=deadline,
                        signal_errors=signal_errors,
                        snapshot_errors=snapshot_errors,
                        cleanup_errors=cleanup_errors,
                    )
                except BaseException as drain_error:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        drain_error,
                        label="RPM lifecycle drain",
                    )
                    drained = False
                if not drained:
                    try:
                        signal_verified_group(
                            root_binding,
                            signal.SIGKILL,
                            expected_parent_pid=supervisor_pid,
                        )
                    except BaseException as signal_error:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            signal_error,
                            label="RPM lifecycle emergency signal",
                        )
                    try:
                        drained = emergency_direct_cleanup(
                            root_binding,
                            bindings,
                            pidfd_send_signal,
                            deadline=deadline,
                            signal_errors=signal_errors,
                            snapshot_errors=snapshot_errors,
                            cleanup_errors=cleanup_errors,
                        )
                    except BaseException as cleanup_error:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            cleanup_error,
                            label="RPM lifecycle emergency drain",
                        )
                        drained = False
                if drained:
                    try:
                        if not cleanup_budget_available(
                            deadline,
                            cleanup_errors,
                            phase="root reap",
                        ):
                            record_unresolved_children(
                                cleanup_errors,
                                phase="root reap",
                                count=sum(
                                    process_id != root_binding.identity.pid
                                    for process_id in bindings
                                ),
                            )
                            return
                        reap_result = reap_child_nonblocking(root_binding.identity.pid)
                    except BaseException as cleanup_error:
                        append_bounded_diagnostic(
                            cleanup_errors,
                            cleanup_error,
                            label="RPM lifecycle root reap",
                        )
                        reap_result = None
                    if reap_result is not None:
                        root_reaped = True
                        return
            remaining = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS) - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

    try:
        pidfd_send_signal = require_kernel_primitives()
        validate_pidfd_send_signal(pidfd_send_signal)
        set_child_subreaper()
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP if hasattr(signal, "SIGHUP") else None):
            if signum is None:
                continue
            previous_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, remember_signal)
            installed_handlers.append(signum)
        os.fstat(handoff_fd)
        ensure_launch_budget(terminate_at, phase="RPM lifecycle")
        child = subprocess.Popen(
            command,
            pass_fds=(handoff_fd,),
            start_new_session=True,
            close_fds=True,
        )
        ensure_launch_budget(terminate_at, phase="RPM lifecycle bind")
        try:
            ensure_launch_budget(terminate_at, phase="RPM lifecycle bind")
            root_binding = bind_child(child.pid, expected_parent_pid=supervisor_pid)
        except BaseException as bind_error:
            # Popen has handed us an unreaped child.  Re-open its pidfd by the
            # still-owned PID so a failed initial bind cannot strand it.
            if is_pidfd_resource_error(bind_error):
                try:
                    root_binding = bind_recovered_child(
                        child.pid,
                        expected_parent_pid=supervisor_pid,
                        deadline=terminate_at,
                    )
                    bindings[child.pid] = root_binding
                except BaseException as recovery_error:
                    bind_error.add_note(
                        "RPM lifecycle recovery bind failed: "
                        f"{describe_error(recovery_error)}"
                    )
            raise
        bindings[child.pid] = root_binding
        ensure_launch_budget(terminate_at, phase="RPM lifecycle bind")
        if (
            root_binding.identity.process_group_id != child.pid
            or root_binding.identity.session_id != child.pid
        ):
            raise RuntimeError("RPM lifecycle root process group identity is invalid")

        while time.monotonic() < deadline:
            now = time.monotonic()
            root_result = peek_child(root_binding.identity.pid)
            if root_result is None:
                if received_signal[0] is not None or now >= terminate_at:
                    start_termination(now, timeout_triggered=received_signal[0] is None)
                    if not root_term_sent:
                        signal_root(signal.SIGTERM)
                        root_term_sent = True
                    if now >= cleanup_deadline and not root_kill_sent:
                        signal_root(signal.SIGKILL)
                        root_kill_sent = True
            else:
                start_termination(now, timeout_triggered=False)
                if not root_term_sent:
                    signal_root(signal.SIGTERM)
                    root_term_sent = True
                if now >= cleanup_deadline and not root_kill_sent:
                    signal_root(signal.SIGKILL)
                    root_kill_sent = True
            if root_result is None and root_kill_sent:
                root_result = peek_child(root_binding.identity.pid)
            try:
                drained = (
                    root_result is not None
                    and drain_adopted_children(
                        root_binding,
                        bindings,
                        pidfd_send_signal,
                        cleanup_deadline=cleanup_deadline,
                        deadline=deadline,
                        signal_errors=signal_errors,
                        snapshot_errors=snapshot_errors,
                        cleanup_errors=cleanup_errors,
                    )
                )
            except BaseException as drain_error:
                if primary_cleanup_error is None:
                    primary_cleanup_error = drain_error
                append_bounded_diagnostic(
                    cleanup_errors,
                    drain_error,
                    label="RPM lifecycle drain",
                )
                drained = False
                try:
                    signal_verified_group(
                        root_binding,
                        signal.SIGKILL,
                        expected_parent_pid=supervisor_pid,
                    )
                except BaseException as signal_error:
                    append_bounded_diagnostic(
                        cleanup_errors,
                        signal_error,
                        label="RPM lifecycle emergency signal",
                    )
                try:
                    drained = emergency_direct_cleanup(
                        root_binding,
                        bindings,
                        pidfd_send_signal,
                        deadline=deadline,
                        signal_errors=signal_errors,
                        snapshot_errors=snapshot_errors,
                        cleanup_errors=cleanup_errors,
                    )
                except BaseException as emergency_error:
                    if primary_cleanup_error is None:
                        primary_cleanup_error = emergency_error
                    append_bounded_diagnostic(
                        cleanup_errors,
                        emergency_error,
                        label="RPM lifecycle emergency drain",
                    )
            if root_result is not None and drained:
                root_status = status_from_wait_result(root_result)
                reap_child(root_binding.identity.pid, deadline=deadline)
                child.returncode = root_status
                root_reaped = True
                break
            sleep_until = min(deadline, time.monotonic() + POLL_INTERVAL_SECONDS)
            remaining = sleep_until - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)

        if root_status is None:
            raise snapshot_primary(TimeoutError("RPM lifecycle cleanup deadline exceeded"))
        if timed_out:
            result_status = 124
        elif received_signal[0] is not None:
            result_status = 128 + received_signal[0]
        elif root_status < 0:
            result_status = 128 + -root_status
        else:
            result_status = root_status
        if snapshot_errors and result_status == 0:
            raise snapshot_primary(None)
        if primary_cleanup_error is not None and result_status == 0:
            raise primary_cleanup_error
        return result_status
    finally:
        primary_error = sys.exc_info()[1]
        snapshot_primary_error = snapshot_errors[0] if snapshot_errors else None
        note_target = snapshot_primary_error or primary_error
        adopted_cleanup_errors = cleanup_errors
        unreaped_adopted: set[int] = set()
        if child is not None and not root_reaped:
            # A delayed bind can cross the operation deadline only after Popen
            # has created an owned child.  Cleanup gets one fresh bounded
            # reserve; the child never gets a new execution budget.
            deadline = max(
                deadline,
                time.monotonic()
                + max(kill_after, direct_children_cleanup_reserve(timeout)),
            )
        if child is not None and root_binding is None:
            try:
                root_binding = make_verified_owned_child_binding(
                    child.pid,
                    expected_parent_pid=supervisor_pid,
                    deadline=deadline,
                )
                bindings[child.pid] = root_binding
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle root binding",
                )
                try:
                    root_binding = bind_unbound_popen_root_for_cleanup(
                        child,
                        expected_parent_pid=supervisor_pid,
                        deadline=deadline,
                    )
                    bindings[child.pid] = root_binding
                except BaseException as unbound_error:
                    append_bounded_diagnostic(
                        adopted_cleanup_errors,
                        unbound_error,
                        label="RPM lifecycle unbound root",
                    )
                    try:
                        root_reaped = cleanup_unbound_popen_root(
                            child,
                            expected_parent_pid=supervisor_pid,
                            pidfd_send_signal=pidfd_send_signal,
                            bindings=bindings,
                            baseline_children=baseline_children,
                            deadline=deadline,
                            signal_errors=signal_errors,
                            cleanup_errors=adopted_cleanup_errors,
                        )
                    except BaseException as direct_cleanup_error:
                        append_bounded_diagnostic(
                            adopted_cleanup_errors,
                            direct_cleanup_error,
                            label="RPM lifecycle unbound root cleanup",
                        )
        if root_binding is not None and not root_reaped:
            try:
                emergency_reap_root()
                if root_reaped and child is not None and child.returncode is None:
                    child.returncode = status_from_wait_result(root_result)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle finalizer",
                )
        if root_binding is not None:
            try:
                unreaped_adopted, final_cleanup_errors = finalize_adopted_children(
                    bindings,
                    pidfd_send_signal,
                    root_pid=root_binding.identity.pid,
                    expected_parent_pid=supervisor_pid,
                    deadline=deadline,
                    signal_errors=signal_errors,
                )
                for final_cleanup_error in final_cleanup_errors:
                    append_bounded_diagnostic(
                        adopted_cleanup_errors,
                        final_cleanup_error,
                        label="RPM lifecycle finalizer",
                    )
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle finalizer",
                )
        if (
            root_binding is not None
            and not root_reaped
            and snapshot_errors
            and not any(process_id != root_binding.identity.pid for process_id in bindings)
        ):
            # Snapshot failure blocks drain confirmation, but an authenticated
            # zombie root still needs bounded reap and explicit uncertainty.
            try:
                root_result = peek_child(root_binding.identity.pid)
                if root_result is not None:
                    reap_result = reap_child_nonblocking(root_binding.identity.pid)
                    if reap_result is not None:
                        root_reaped = True
                        if child is not None and child.returncode is None:
                            child.returncode = status_from_wait_result(
                                root_result if reap_result is CHILD_ALREADY_REAPED else reap_result
                            )
                        append_bounded_diagnostic(
                            adopted_cleanup_errors,
                            RuntimeError(
                                "RPM lifecycle root reaped with unconfirmed adopted-child "
                                "drain after direct-child snapshot failure"
                            ),
                            label="RPM lifecycle root cleanup",
                        )
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle snapshot cleanup",
                )
        for process_id, binding in list(bindings.items()):
            if root_binding is not None and process_id == root_binding.identity.pid:
                continue
            try:
                close_binding(binding, errors=adopted_cleanup_errors)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle close",
                )
            del bindings[process_id]
        if root_binding is not None:
            try:
                close_binding(root_binding, errors=adopted_cleanup_errors)
            except BaseException as cleanup_error:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    cleanup_error,
                    label="RPM lifecycle root close",
                )
        try:
            os.close(handoff_fd)
        except BaseException as cleanup_error:
            append_bounded_diagnostic(
                adopted_cleanup_errors,
                cleanup_error,
                label="RPM lifecycle handoff close",
            )
        if snapshot_primary_error is None and snapshot_errors:
            snapshot_primary_error = snapshot_errors[0]
            if primary_error is not None:
                for note in getattr(primary_error, "__notes__", ()):
                    snapshot_primary_error.add_note(note)
            note_target = snapshot_primary_error
        if unreaped_adopted:
            unreaped_error = RuntimeError(
                "RPM lifecycle cleanup left unconfirmed adopted children: "
                f"{len(unreaped_adopted)}"
            )
            append_bounded_diagnostic(
                adopted_cleanup_errors,
                unreaped_error,
                label="RPM lifecycle cleanup",
            )
        if root_binding is not None and not root_reaped:
            append_bounded_diagnostic(
                adopted_cleanup_errors,
                RuntimeError(
                    "RPM lifecycle cleanup left root child unconfirmed: "
                    f"{root_binding.identity.pid}"
                ),
                label="RPM lifecycle cleanup",
            )
        handler_restore_errors = []
        for signum in reversed(installed_handlers):
            try:
                signal.signal(signum, previous_handlers[signum])
            except BaseException as cleanup_error:
                handler_restore_errors.append(cleanup_error)
        if handler_restore_errors:
            for handler_error in handler_restore_errors:
                append_bounded_diagnostic(
                    adopted_cleanup_errors,
                    handler_error,
                    label="RPM lifecycle handler restore",
                )
        for cleanup_error in adopted_cleanup_errors:
            if note_target is not None:
                note_target.add_note(f"RPM lifecycle adopted-child cleanup failed: {cleanup_error}")
        if signal_errors:
            if note_target is not None:
                note_signal_errors(
                    note_target,
                    signal_errors,
                    prefix="RPM lifecycle pidfd signal failed; exact-PID fallback used",
                )
            else:
                report_signal_errors(signal_errors)
        if result_status is not None and result_status != 0:
            if snapshot_errors:
                report_cleanup_errors(
                    snapshot_errors,
                    prefix="RPM lifecycle snapshot cleanup diagnostic",
                )
            if adopted_cleanup_errors:
                report_cleanup_errors(
                    adopted_cleanup_errors,
                    prefix="RPM lifecycle cleanup diagnostic",
                )
            if signal_errors and note_target is not None:
                report_signal_errors(signal_errors)
        elif isinstance(primary_error, SupervisorInterrupted) and adopted_cleanup_errors:
            report_cleanup_errors(
                adopted_cleanup_errors,
                prefix="RPM lifecycle cleanup diagnostic",
            )
        if adopted_cleanup_errors and note_target is None and result_status in (None, 0):
            primary_cleanup_error = adopted_cleanup_errors[0]
            for cleanup_error in adopted_cleanup_errors[1:]:
                primary_cleanup_error.add_note(
                    f"RPM lifecycle additional cleanup failure: {cleanup_error}"
                )
            raise primary_cleanup_error
        if (
            snapshot_primary_error is not None
            and primary_error is not snapshot_primary_error
            and result_status in (None, 0)
        ):
            if primary_error is not None:
                snapshot_primary_error.add_note(
                    "RPM lifecycle primary failure: "
                    f"{type(primary_error).__name__}: "
                    f"{str(primary_error).replace(chr(10), ' ')[:256]}"
                )
            raise snapshot_primary_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--kill-after", type=float)
    handoff_group = parser.add_mutually_exclusive_group(required=True)
    handoff_group.add_argument("--handoff-fd", type=int)
    handoff_group.add_argument("--check-handoff-fd", type=int)
    handoff_group.add_argument("--identity", action="store_true")
    parser.add_argument("--handoff-timeout", type=float, default=0.1)
    parser.add_argument("--handoff-max-bytes", type=int, default=256)
    parser.add_argument("--identity-timeout", type=float, default=5.0)
    parser.add_argument("--identity-max-bytes", type=int, default=256)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    for option_name, option_label in (
        ("timeout", "--timeout"),
        ("kill_after", "--kill-after"),
        ("handoff_timeout", "--handoff-timeout"),
        ("identity_timeout", "--identity-timeout"),
    ):
        value = getattr(arguments, option_name)
        if value is not None and not math.isfinite(value):
            parser.error(f"{option_label} must be finite")
    if not 0 < arguments.handoff_max_bytes <= MAX_HANDOFF_BYTES:
        parser.error(
            f"--handoff-max-bytes must be between 1 and {MAX_HANDOFF_BYTES}"
        )
    if not 0 < arguments.identity_max_bytes <= MAX_IDENTITY_OUTPUT_BYTES:
        parser.error(
            f"--identity-max-bytes must be between 1 and {MAX_IDENTITY_OUTPUT_BYTES}"
        )
    if arguments.command[:1] == ["--"]:
        arguments.command = arguments.command[1:]
    if arguments.identity:
        if not arguments.command:
            parser.error("missing identity helper command")
        if arguments.timeout is not None or arguments.kill_after is not None:
            parser.error("identity helper does not accept lifecycle timeout options")
        return arguments
    if arguments.check_handoff_fd is not None:
        if arguments.command:
            parser.error("handoff check does not accept a lifecycle command")
        if arguments.timeout is not None or arguments.kill_after is not None:
            parser.error("handoff check does not accept lifecycle timeout options")
        return arguments
    if arguments.timeout is None or arguments.kill_after is None:
        parser.error("--timeout and --kill-after are required for lifecycle supervision")
    if not arguments.command:
        parser.error("missing lifecycle command")
    return arguments


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.identity:
            print(
                run_bounded_identity(
                    arguments.command,
                    timeout=arguments.identity_timeout,
                    max_bytes=arguments.identity_max_bytes,
                ),
                flush=True,
            )
            return 0
        if arguments.check_handoff_fd is not None:
            return check_handoff_fd(
                handoff_fd=arguments.check_handoff_fd,
                timeout=arguments.handoff_timeout,
                max_bytes=arguments.handoff_max_bytes,
            )
        return run(
            arguments.command,
            timeout=arguments.timeout,
            kill_after=arguments.kill_after,
            handoff_fd=arguments.handoff_fd,
        )
    except SupervisorInterrupted as exc:
        return 128 + exc.signum
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"RPM lifecycle supervisor failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
