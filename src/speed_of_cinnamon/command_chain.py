from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import contextmanager, suppress
import codecs
import errno
import math
import os
import re
import selectors
import shlex
import signal
import subprocess  # nosec B404
import sys
import tempfile
import threading
import time
import io
import shutil
from pathlib import Path

from .personalization import command_environment
from .proc_safety import _bounded_proc_entries, _read_proc_stat, _read_proc_stat_path
from .output import (
    _clipboard_lock_identity_for_pid,
    _kill_output_process_with_pidfd,
    _kill_output_process_tree,
    _output_process_identity_is_current,
    _process_pipe_holder_identities,
    _process_tree_descendant_identities,
    _wait_for_output_process_tree_stop,
)
from .process_priority import (
    LocalModelPriorityError,
    PriorityScopeError,
    _local_model_direct_supervisor_environment,
    _local_model_direct_supervisor_status_frame_size,
    _parse_local_model_direct_supervisor_status,
    local_model_command,
    local_model_direct_command,
    local_model_scope_probe_command,
)


class CommandChainError(RuntimeError):
    pass


_REDACTED_COMMAND_OUTPUT = "exit code {returncode}; command output redacted"
_PIPE_DRAIN_GRACE_SECONDS = 0.25
_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_PROCESS_TREE_SNAPSHOT_INTERVAL_SECONDS = 0.05
_PROCESS_DISCOVERY_RETRY_COUNT = 3
_PROCESS_DISCOVERY_RETRY_INTERVAL_SECONDS = 0.01
_LOCAL_MODEL_PRIORITY_PROBE_MAX_OUTPUT_BYTES = 4096
_LOCAL_MODEL_DIRECT_SUPERVISOR_FAILURE = "direct priority supervisor failed"
_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BASE_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
}
_USER_SYSTEMD_ENV_KEYS = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")
_DANGEROUS_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")
_DANGEROUS_ENV_KEYS = {
    "ENV",
    "PWD",
    "OLDPWD",
    "CDPATH",
    "PS4",
    "BASH_XTRACEFD",
    "SHELLOPTS",
    "PROMPT_COMMAND",
    "IFS",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "BASH_ENV",
}
_ESCAPED_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


def _is_unsafe_env_var(name: str) -> bool:
    return name in _DANGEROUS_ENV_KEYS or name.startswith(_DANGEROUS_ENV_PREFIXES)


def _coerce_environment_value(name: str) -> str | None:
    if isinstance(name, bool) or not isinstance(name, str):
        return None
    try:
        value = os.environ.__getitem__(name)
    except KeyError:
        return None
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return None
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
        return None
    return value


def _filtered_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = _coerce_environment_value(key)
        if value is not None:
            env[key] = value

    if base is not None:
        if not isinstance(base, dict):
            raise CommandChainError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise CommandChainError("environment keys must be text")
            if isinstance(value, bool):
                raise CommandChainError("environment values must be text")
            if not isinstance(value, str):
                raise CommandChainError("environment base must be a mapping")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise CommandChainError("environment key contains invalid control character")
            if _contains_environment_control_chars(value):
                raise CommandChainError("environment value contains invalid control character")
            if _is_unsafe_env_var(key):
                raise CommandChainError(f"environment key is not allowed: {key}")
            env[key] = value

    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


def _command_failure_detail(returncode: int, stdout_size: int, stderr_size: int) -> str:
    if stdout_size or stderr_size:
        return _REDACTED_COMMAND_OUTPUT.format(returncode=returncode)
    return f"exit code {returncode}"


def _command_timeout_detail(label: str, timeout_seconds: int) -> str:
    return f"{label} command timed out after {timeout_seconds} seconds"


def _process_start_time_from_identity(identity: object) -> str | None:
    if not isinstance(identity, str) or isinstance(identity, bool):
        return None
    boot_id, separator, start_time = identity.rpartition(":")
    if not separator or not boot_id or not start_time.isdecimal():
        return None
    return start_time


def _terminate_bounded_process(
    proc: subprocess.Popen[bytes],
    *,
    process_tree: dict[int, str] | None = None,
    require_complete_scan: bool = True,
) -> bool:
    if (
        _LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE in vars(proc)
        or "_soc_local_model_direct_status" in vars(proc)
    ):
        return _terminate_local_model_direct_supervisor(
            proc,
            process_tree=process_tree,
            require_complete_scan=require_complete_scan,
        )
    pid = getattr(proc, "pid", None)
    root_identity_current = _output_process_identity_is_current(proc)
    root_identity_changed_after_exit = (
        not root_identity_current
        and process_tree is not None
        and isinstance(getattr(proc, "returncode", None), int)
        and not isinstance(getattr(proc, "returncode", None), bool)
    )
    if not root_identity_current and not root_identity_changed_after_exit:
        return False
    tree_cleanup_confirmed = True
    process_wait_confirmed = False
    scan_incomplete = False
    if process_tree is None and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        try:
            process_tree = _process_tree_descendant_identities(pid)
        except BaseException:
            process_tree = None
        if process_tree is None and require_complete_scan:
            scan_incomplete = True
            process_tree = {}
    if require_complete_scan:
        try:
            pipe_holders = _process_pipe_holder_identities(proc)
        except BaseException:
            pipe_holders = None
        if pipe_holders is None:
            scan_incomplete = True
        else:
            if process_tree is None:
                process_tree = {}
            process_tree.update(pipe_holders)
    if process_tree is not None:
        tree_cleanup_confirmed = _kill_output_process_tree(process_tree)
    if not root_identity_changed_after_exit and not _output_process_identity_is_current(proc):
        return False
    # Root identity changed after reaping: never signal the reused PID/group.
    root_reaped = root_identity_changed_after_exit or _output_process_is_reaped(proc)
    if root_reaped or _output_process_is_reaped(proc):
        if process_tree is None:
            tree_cleanup_confirmed = False
    elif isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        expected_start_time = _process_start_time_from_identity(
            vars(proc).get("_soc_process_identity")
        )
        if expected_start_time is None:
            return False
        if _kill_output_process_with_pidfd(pid, expected_start_time) is not True:
            return False
    else:
        return False
    try:
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        process_wait_confirmed = False
    else:
        process_wait_confirmed = True
    if process_tree is not None:
        tree_cleanup_confirmed = (
            tree_cleanup_confirmed and
            _wait_for_output_process_tree_stop(process_tree)
        )
    return process_wait_confirmed and tree_cleanup_confirmed and not scan_incomplete


def _terminate_unidentified_bounded_process(
    proc: subprocess.Popen[bytes],
    *,
    cleanup_errors: list[BaseException] | None = None,
) -> bool:
    """Stop a freshly spawned process when its /proc identity cannot be read."""
    if type(proc).__module__ != "subprocess":
        return _terminate_bounded_process(proc, require_complete_scan=True)
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False

    root_cleanup_confirmed = False
    pidfd: int | None = None
    try:
        if not root_cleanup_confirmed:
            pidfd_open = getattr(os, "pidfd_open", None)
            pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
            if callable(pidfd_open) and callable(pidfd_send_signal):
                try:
                    candidate_pidfd = pidfd_open(pid, 0)
                except (AttributeError, NotImplementedError, TypeError, OSError):
                    candidate_pidfd = None
                if isinstance(candidate_pidfd, int) and not isinstance(candidate_pidfd, bool) and candidate_pidfd >= 0:
                    pidfd = candidate_pidfd
                    try:
                        pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
                    except ProcessLookupError:
                        pass
                    except (AttributeError, NotImplementedError, TypeError, OSError):
                        pass
                    else:
                        root_cleanup_confirmed = True
    finally:
        try:
            with _bounded_fd_critical_section():
                descriptor = pidfd
                pidfd = None
                if descriptor is not None:
                    os.close(descriptor)
        except BaseException as error:
            if cleanup_errors is None:
                raise
            cleanup_errors.append(error)

    if not root_cleanup_confirmed:
        return False
    try:
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        stream_errors = _close_bounded_process_streams(proc)
        if cleanup_errors is None:
            if stream_errors:
                raise stream_errors[0]
        else:
            cleanup_errors.extend(stream_errors)
    return True


@contextmanager
def _bounded_fd_critical_section():
    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    valid_signals = getattr(signal, "valid_signals", None)
    if not callable(pthread_sigmask) or not callable(valid_signals):
        raise RuntimeError("bounded FD signal masking is unavailable")
    blocked_signals = set(valid_signals())
    for signal_name in ("SIGKILL", "SIGSTOP"):
        blocked_signals.discard(getattr(signal, signal_name, None))
    previous_mask = pthread_sigmask(signal.SIG_BLOCK, blocked_signals)
    try:
        try:
            yield previous_mask
        except BaseException as primary:
            try:
                pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            except BaseException as restore_error:
                with suppress(BaseException):
                    primary.add_note(
                        "bounded FD signal-mask restore raised "
                        f"{type(restore_error).__name__}"
                    )
            raise
        else:
            pthread_sigmask(signal.SIG_SETMASK, previous_mask)
    except GeneratorExit:
        raise


_POPEN_OUTCOME_READY = "ready"
_POPEN_OUTCOME_DEADLINE = "deadline"
_POPEN_OUTCOME_FILE_NOT_FOUND = "file_not_found"
_POPEN_OUTCOME_OSERROR = "oserror"
_POPEN_OUTCOME_MEMORY = "memory"
_POPEN_OUTCOME_KEYBOARD_INTERRUPT = "keyboard_interrupt"
_POPEN_OUTCOME_SYSTEM_EXIT = "system_exit"
_POPEN_OUTCOME_INTERNAL = "internal"


def _bounded_popen_errno(error: BaseException) -> int | None:
    try:
        value = error.errno  # type: ignore[attr-defined]
    except BaseException:
        return None
    if type(value) is not int or value < 0 or value > 65535:
        return None
    return value


def _bounded_popen_exception_outcome(error: BaseException) -> tuple[str, int | None]:
    if isinstance(error, KeyboardInterrupt):
        return _POPEN_OUTCOME_KEYBOARD_INTERRUPT, None
    if isinstance(error, SystemExit):
        return _POPEN_OUTCOME_SYSTEM_EXIT, None
    if isinstance(error, MemoryError):
        return _POPEN_OUTCOME_MEMORY, None
    if isinstance(error, FileNotFoundError):
        return _POPEN_OUTCOME_FILE_NOT_FOUND, errno.ENOENT
    if isinstance(error, OSError):
        return _POPEN_OUTCOME_OSERROR, _bounded_popen_errno(error)
    return _POPEN_OUTCOME_INTERNAL, None


def _raise_bounded_popen_outcome(outcome: str, error_number: int | None) -> None:
    if outcome == _POPEN_OUTCOME_FILE_NOT_FOUND:
        raise FileNotFoundError(errno.ENOENT, "bounded process spawn failed")
    if outcome == _POPEN_OUTCOME_OSERROR:
        if error_number is None:
            raise OSError("bounded process spawn failed")
        raise OSError(error_number, "bounded process spawn failed")
    if outcome == _POPEN_OUTCOME_MEMORY:
        raise MemoryError
    if outcome == _POPEN_OUTCOME_KEYBOARD_INTERRUPT:
        raise KeyboardInterrupt
    if outcome == _POPEN_OUTCOME_SYSTEM_EXIT:
        raise SystemExit
    if outcome == _POPEN_OUTCOME_DEADLINE:
        raise CommandChainError("bounded process spawn deadline expired")
    raise CommandChainError("bounded process spawn failed")


class _BoundedPopenOwner:
    """Single owner for worker-published process or fixed launch outcome."""

    __slots__ = ("_lock", "_event", "_process", "_outcome", "_error_number")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._process: subprocess.Popen[bytes] | None = None
        self._outcome: str | None = None
        self._error_number: int | None = None

    def publish_process(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            if self._process is None and self._outcome is None:
                self._process = process
                self._outcome = _POPEN_OUTCOME_READY
        self._event.set()

    def publish_outcome(self, outcome: str, error_number: int | None = None) -> None:
        with self._lock:
            if self._process is None and self._outcome is None:
                self._outcome = outcome
                self._error_number = error_number
        self._event.set()

    def wait(self, timeout: float | None) -> bool:
        return self._event.wait(timeout)

    def snapshot(self) -> tuple[subprocess.Popen[bytes] | None, str | None, int | None]:
        with self._lock:
            return self._process, self._outcome, self._error_number


class _BoundedPopenThread(threading.Thread):
    """Non-daemon Popen worker; raw worker exceptions never cross boundary."""

    def __init__(self, owner: _BoundedPopenOwner, args: tuple, kwargs: dict) -> None:
        super().__init__(name="soc-popen", daemon=False)
        self._owner = owner
        self._popen_args = args
        self._popen_kwargs = kwargs

    def run(self) -> None:
        try:
            try:
                process = subprocess.Popen(*self._popen_args, **self._popen_kwargs)
            except BaseException as error:
                outcome, error_number = _bounded_popen_exception_outcome(error)
                self._owner.publish_outcome(outcome, error_number)
            else:
                try:
                    self._owner.publish_process(process)
                except BaseException:
                    try:
                        _terminate_unidentified_bounded_process(process)
                    except BaseException:
                        pass
        except BaseException:
            try:
                self._owner.publish_outcome(_POPEN_OUTCOME_INTERNAL)
            except BaseException:
                pass


def _thread_may_have_started(thread: threading.Thread) -> bool:
    try:
        if thread.is_alive() or thread.ident is not None:
            return True
        handle = getattr(thread, "_os_thread_handle", None)
        if handle is not None and getattr(handle, "ident", 0) not in (None, 0):
            return True
        started = getattr(thread, "_started", None)
        return bool(started is not None and started.is_set())
    except BaseException:
        return True


class _BoundedPopenResult:
    __slots__ = ("process", "outcome", "error_number", "primary")

    def __init__(
        self,
        process: subprocess.Popen[bytes] | None,
        outcome: str | None,
        error_number: int | None,
        primary: BaseException | None,
    ) -> None:
        self.process = process
        self.outcome = outcome
        self.error_number = error_number
        self.primary = primary


def _launch_bounded_process(
    argv: Sequence[str],
    *,
    stdin_file,
    environment: dict[str, str],
    status_descriptor: int,
    deadline: float,
) -> _BoundedPopenResult:
    popen_kwargs = {
        "stdin": stdin_file,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "env": environment,
        "shell": False,
        "close_fds": True,
        "start_new_session": True,
    }
    if status_descriptor >= 0:
        popen_kwargs["pass_fds"] = (status_descriptor,)

    owner = _BoundedPopenOwner()
    worker = _BoundedPopenThread(owner, (argv,), popen_kwargs)
    primary: BaseException | None = None
    start_attempted = False
    try:
        start_attempted = True
        try:
            worker.start()
        except BaseException as error:
            primary = error
        else:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                primary = CommandChainError("bounded process spawn deadline expired")
            else:
                try:
                    owner.wait(remaining)
                except BaseException as error:
                    primary = error
                else:
                    if time.monotonic() >= deadline:
                        primary = CommandChainError(
                            "bounded process spawn deadline expired"
                        )
    finally:
        if start_attempted and _thread_may_have_started(worker):
            while True:
                try:
                    worker.join()
                except RuntimeError:
                    handle = getattr(worker, "_os_thread_handle", None)
                    handle_join = getattr(handle, "join", None)
                    if callable(handle_join):
                        try:
                            handle_join()
                        except BaseException as error:
                            if primary is None:
                                primary = error
                            continue
                        break
                    if worker.is_alive():
                        continue
                    break
                except BaseException as error:
                    if primary is None:
                        primary = error
                    continue
                break

    process, outcome, error_number = owner.snapshot()
    return _BoundedPopenResult(process, outcome, error_number, primary)

def _close_bounded_process_streams(
    proc: subprocess.Popen[bytes],
) -> list[BaseException]:
    cleanup_errors: list[BaseException] = []
    for attribute in ("stdout", "stderr"):
        try:
            with _bounded_fd_critical_section():
                stream = getattr(proc, attribute, None)
                close = getattr(stream, "close", None)
                if not callable(close):
                    continue
                if _bounded_process_stream_close_was_attempted(proc, stream):
                    continue
                _mark_bounded_process_stream_close_attempted(proc, stream)
                close()
        except BaseException as error:
            cleanup_errors.append(error)
    return cleanup_errors


def _add_bounded_cleanup_notes(
    primary: BaseException,
    cleanup_errors: list[BaseException],
    *,
    prefix: str,
) -> None:
    for cleanup_error in cleanup_errors:
        with suppress(BaseException):
            primary.add_note(
                f"{prefix} raised {type(cleanup_error).__name__}"
            )


def _run_bounded_final_cleanup_step(cleanup, cleanup_errors: list[BaseException]) -> None:
    try:
        cleanup()
    except BaseException as error:
        cleanup_errors.append(error)


def _unregister_bounded_process_stream(selector, stream) -> None:
    try:
        selector.unregister(stream)
    except (KeyError, ValueError):
        pass


def _close_bounded_process_stream(stream, proc) -> None:
    with _bounded_fd_critical_section():
        close = getattr(stream, "close", None)
        if not callable(close):
            return
        if _bounded_process_stream_close_was_attempted(proc, stream):
            return
        _mark_bounded_process_stream_close_attempted(proc, stream)
        close()


def _mark_bounded_process_stream_close_attempted(proc, stream) -> None:
    if proc is None:
        return
    closed_streams = vars(proc).setdefault(
        "_soc_bounded_stream_close_attempted_ids",
        set(),
    )
    closed_streams.add(id(stream))
    for attribute in ("stdout", "stderr"):
        if getattr(proc, attribute, None) is stream:
            setattr(proc, attribute, None)


def _bounded_process_stream_close_was_attempted(proc, stream) -> bool:
    if proc is None:
        return False
    return id(stream) in vars(proc).get(
        "_soc_bounded_stream_close_attempted_ids",
        set(),
    )


def _close_bounded_process_final_resources(
    proc: subprocess.Popen[bytes] | None,
    selector: selectors.BaseSelector,
) -> list[BaseException]:
    cleanup_errors: list[BaseException] = []
    if proc is not None:
        _run_bounded_final_cleanup_step(
            lambda: _close_local_model_direct_status(proc),
            cleanup_errors,
        )

    selector_keys: list[object] = []

    def snapshot_selector() -> None:
        selector_keys.extend(selector.get_map().values())

    _run_bounded_final_cleanup_step(snapshot_selector, cleanup_errors)
    streams: list[object] = []
    closed_stream_ids = set()
    if proc is not None:
        try:
            closed_stream_ids = vars(proc).get(
                "_soc_bounded_stream_close_attempted_ids",
                set(),
            )
        except BaseException as error:
            cleanup_errors.append(error)
    for key in selector_keys:
        try:
            stream = key.fileobj
        except BaseException as error:
            cleanup_errors.append(error)
            continue
        if not any(existing is stream for existing in streams):
            streams.append(stream)
        _run_bounded_final_cleanup_step(
            lambda stream=stream: _unregister_bounded_process_stream(selector, stream),
            cleanup_errors,
        )
    if proc is not None:
        for attribute in ("stdout", "stderr"):
            stream = getattr(proc, attribute, None)
            if stream is not None and not any(existing is stream for existing in streams):
                streams.append(stream)
    for stream in streams:
        if id(stream) in closed_stream_ids:
            continue
        _run_bounded_final_cleanup_step(
            lambda stream=stream: _close_bounded_process_stream(stream, proc),
            cleanup_errors,
        )
    _run_bounded_final_cleanup_step(selector.close, cleanup_errors)
    return cleanup_errors


def _output_process_is_reaped(proc: subprocess.Popen[bytes]) -> bool:
    pid = getattr(proc, "pid", None)
    returncode = getattr(proc, "returncode", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if not isinstance(returncode, int) or isinstance(returncode, bool):
        return False
    try:
        os.stat(f"/proc/{pid}")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _process_has_exited_without_reaping(process_id: int) -> bool:
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        return False
    try:
        raw = _read_proc_stat(process_id)
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return False
    try:
        close = raw.rindex(")")
        process_state = raw[close + 2 :].split()[0]
    except (IndexError, ValueError):
        return False
    return process_state in {"Z", "X", "x"}


def _process_session_descendant_identities(
    process_id: int,
    *,
    expected_process_identity: str | None = None,
) -> dict[int, str] | None:
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        return None
    proc_entries = _bounded_proc_entries()
    if proc_entries is None:
        return None
    descendants: dict[int, str] = {}
    scan_incomplete = False
    root_identity_verified = (
        isinstance(expected_process_identity, str)
        and bool(expected_process_identity)
        and _clipboard_lock_identity_for_pid(process_id) == expected_process_identity
    )
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        member_id = int(proc_entry.name)
        if member_id == process_id:
            continue
        try:
            raw = _read_proc_stat_path(proc_entry.joinpath("stat"))
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_state = fields[0]
            session_id = int(fields[3])
            start_time = fields[19]
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        if session_id != process_id or process_state in {"Z", "X", "x"}:
            continue
        descendants[member_id] = start_time
    if scan_incomplete:
        return None
    if not root_identity_verified and descendants:
        return None
    return descendants


def _retry_process_scan(
    scan: Callable[[], dict[int, str] | None],
) -> dict[int, str] | None:
    for attempt in range(_PROCESS_DISCOVERY_RETRY_COUNT):
        result = scan()
        if result is not None:
            return result
        if attempt + 1 < _PROCESS_DISCOVERY_RETRY_COUNT:
            time.sleep(_PROCESS_DISCOVERY_RETRY_INTERVAL_SECONDS)
    return None


def _close_detached_descriptor_once(descriptor: int) -> BaseException | None:
    # Linux close() may close descriptor before reporting an error. Never retry
    # same numeric descriptor: it may already name unrelated resource.
    try:
        os.close(descriptor)
    except OSError as exc:
        return exc
    except BaseException as exc:
        return exc
    return None


_LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE = (
    "_soc_local_model_direct_status_owner"
)
_LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY = object()


def _close_local_model_direct_status(proc: subprocess.Popen[bytes]) -> None:
    owner = _take_local_model_direct_status_owner(proc)
    if owner is None:
        return
    drop_error: BaseException | None = None
    try:
        close_error = owner.close_one(0)
    except BaseException as error:
        close_error = error
    if owner._value(0) < 0:
        drop_error = _drop_local_model_direct_status_owner(proc, owner)
    if close_error is not None and drop_error is not None:
        with suppress(BaseException):
            close_error.add_note(
                "status owner reference cleanup raised "
                f"{type(drop_error).__name__}"
            )
    if close_error is not None:
        raise close_error
    if drop_error is not None:
        raise drop_error


def _local_model_direct_status(
    proc: subprocess.Popen[bytes],
) -> tuple[str, int | None] | None:
    attributes = vars(proc)
    if "_soc_local_model_direct_status" in attributes:
        cached = attributes["_soc_local_model_direct_status"]
        return cached if isinstance(cached, tuple) else None
    payload = bytearray()
    complete = False
    read_error: BaseException | None = None
    close_error: BaseException | None = None
    drop_error: BaseException | None = None
    owner = _take_local_model_direct_status_owner(proc)
    descriptor = -1
    if owner is not None:
        try:
            descriptor = owner._value(0)
            if type(descriptor) is int and descriptor >= 0:
                while len(payload) <= _local_model_direct_supervisor_status_frame_size():
                    try:
                        chunk = os.read(
                            descriptor,
                            _local_model_direct_supervisor_status_frame_size()
                            + 1
                            - len(payload),
                        )
                    except InterruptedError:
                        continue
                    except BlockingIOError:
                        break
                    if not chunk:
                        complete = True
                        break
                    payload.extend(chunk)
        except OSError:
            pass
        except BaseException as error:
            read_error = error
        try:
            close_error = owner.close_one(0)
        except BaseException as error:
            close_error = error
        if owner._value(0) < 0:
            drop_error = _drop_local_model_direct_status_owner(proc, owner)
        if read_error is not None:
            if close_error is not None:
                with suppress(BaseException):
                    read_error.add_note(
                        f"status FD cleanup raised {type(close_error).__name__}"
                    )
            if drop_error is not None:
                with suppress(BaseException):
                    read_error.add_note(
                        "status owner reference cleanup raised "
                        f"{type(drop_error).__name__}"
                    )
            raise read_error
        if close_error is not None:
            if drop_error is not None:
                with suppress(BaseException):
                    close_error.add_note(
                        "status owner reference cleanup raised "
                        f"{type(drop_error).__name__}"
                    )
            raise close_error
        if drop_error is not None:
            raise drop_error
    returncode = getattr(proc, "returncode", None)
    result = (
        _parse_local_model_direct_supervisor_status(bytes(payload), returncode)
        if complete and type(returncode) is int
        else None
    )
    attributes["_soc_local_model_direct_status"] = result
    return result


def _confirm_local_model_direct_cleanup(
    proc: subprocess.Popen[bytes],
    cleanup_confirmed: bool,
) -> bool:
    if not (
        _LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE in vars(proc)
        or "_soc_local_model_direct_status" in vars(proc)
    ):
        return cleanup_confirmed
    return bool(
        cleanup_confirmed and _local_model_direct_status(proc) is not None
    )


def _bind_local_model_direct_status_descriptor(
    proc: subprocess.Popen[bytes],
    owner: object,
) -> None:
    if (
        type(owner) is not _OwnedLaunchDescriptorPair
        or vars(owner).get("_owner_token")
        is not _LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
    ):
        raise TypeError("invalid local model status owner")
    with _bounded_fd_critical_section():
        setattr(proc, _LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE, owner)


def _set_bounded_launch_descriptor_target(
    target,
    attribute: str,
    descriptor: int,
) -> None:
    if attribute == "_soc_local_model_direct_status_fd":
        _bind_local_model_direct_status_descriptor(target, descriptor)
    else:
        setattr(target, attribute, descriptor)


def _bind_bounded_process_identity(
    proc: subprocess.Popen[bytes],
    identity: str,
) -> None:
    setattr(proc, "_soc_process_identity", identity)


class _OwnedLaunchDescriptor:
    def __init__(self) -> None:
        self.value = -1

    def acquire(self, descriptor: int) -> None:
        with _bounded_fd_critical_section():
            if self.value >= 0:
                raise RuntimeError("launch descriptor already owned")
            self.value = descriptor

    def release(self) -> int:
        with _bounded_fd_critical_section():
            descriptor = self.value
            self.value = -1
            return descriptor

    def transfer_to(self, target, attribute: str) -> int:
        with _bounded_fd_critical_section():
            descriptor = self.value
            if descriptor < 0:
                return descriptor
            missing = object()
            target_attributes = vars(target)
            previous = target_attributes.get(attribute, missing)
            try:
                _set_bounded_launch_descriptor_target(
                    target,
                    attribute,
                    descriptor,
                )
            except BaseException as error:
                current = target_attributes.get(attribute, missing)
                if previous is missing and current == descriptor:
                    self.value = -1
                    raise
                self.value = -1
                close_error = _close_detached_descriptor_once(descriptor)
                if close_error is not None:
                    with suppress(BaseException):
                        error.add_note(
                            "launch descriptor transfer cleanup raised "
                            f"{type(close_error).__name__}"
                        )
                raise
            self.value = -1
            return descriptor

    def close(self) -> BaseException | None:
        close_error: BaseException | None = None
        try:
            with _bounded_fd_critical_section():
                descriptor = self.value
                self.value = -1
                if descriptor < 0:
                    return None
                close_error = _close_detached_descriptor_once(descriptor)
        except BaseException as exc:
            if close_error is not None:
                with suppress(BaseException):
                    close_error.add_note(
                        "launch descriptor mask cleanup raised "
                        f"{type(exc).__name__}"
                    )
                return close_error
            return exc
        return close_error


def _take_local_model_direct_status_owner(
    proc: subprocess.Popen[bytes],
) -> _OwnedLaunchDescriptorPair | None:
    owner: _OwnedLaunchDescriptorPair | None = None
    try:
        with _bounded_fd_critical_section():
            candidate = vars(proc).get(
                _LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE
            )
            if (
                type(candidate) is not _OwnedLaunchDescriptorPair
                or vars(candidate).get("_owner_token")
                is not _LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
            ):
                return None
            owner = candidate
    except BaseException as error:
        if owner is not None:
            close_error: BaseException | None = None
            try:
                close_error = owner.close_one(0)
            except BaseException as cleanup_error:
                close_error = cleanup_error
            if owner._value(0) < 0:
                drop_error = _drop_local_model_direct_status_owner(proc, owner)
                if drop_error is not None:
                    if close_error is None:
                        close_error = drop_error
                    else:
                        with suppress(BaseException):
                            close_error.add_note(
                                "status owner reference cleanup raised "
                                f"{type(drop_error).__name__}"
                            )
            if close_error is not None:
                with suppress(BaseException):
                    error.add_note(
                        "status FD owner cleanup raised "
                        f"{type(close_error).__name__}"
                    )
        raise
    return owner


def _drop_local_model_direct_status_owner(
    proc: subprocess.Popen[bytes],
    owner: _OwnedLaunchDescriptorPair,
) -> BaseException | None:
    try:
        with _bounded_fd_critical_section():
            attributes = vars(proc)
            if attributes.get(_LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE) is owner:
                attributes.pop(_LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE, None)
    except BaseException as error:
        return error
    return None


class _OwnedLaunchDescriptorPair:
    def __init__(self, *, owner_token: object | None = None) -> None:
        self._owner_token = owner_token
        self._values = (-1, -1)

    def acquire_pair(self, descriptors: tuple[int, int]) -> None:
        first, second = descriptors
        if (
            type(first) is not int
            or type(second) is not int
            or first < 0
            or second < 0
            or first == second
        ):
            raise ValueError("invalid launch descriptor pair")
        with _bounded_fd_critical_section():
            if self._values != (-1, -1):
                raise RuntimeError("launch descriptor pair already owned")
            self._stage_pair_member(0, first)
            self._stage_pair_member(1, second)
            self._values = (first, second)

    def _stage_pair_member(self, index: int, descriptor: int) -> None:
        del index, descriptor

    def owns_pair(self, descriptors: tuple[int, int]) -> bool:
        try:
            first, second = descriptors
        except (TypeError, ValueError):
            return False
        return self._values[0] == first and self._values[1] == second

    def acquire_one(self, index: int, descriptor: int) -> None:
        if type(descriptor) is not int or descriptor < 0:
            raise ValueError("invalid launch descriptor")
        with _bounded_fd_critical_section():
            if self._values[index] >= 0:
                raise RuntimeError("launch descriptor already owned")
            if index == 0:
                self._values = (descriptor, self._values[1])
            else:
                self._values = (self._values[0], descriptor)

    def _value(self, index: int) -> int:
        return self._values[index]

    def _detach(self, index: int) -> int:
        if index == 0:
            descriptor = self._values[0]
            self._values = (-1, self._values[1])
        else:
            descriptor = self._values[1]
            self._values = (self._values[0], -1)
        return descriptor

    def close_one(self, index: int) -> BaseException | None:
        try:
            with _bounded_fd_critical_section():
                descriptor = self._detach(index)
                if descriptor < 0:
                    return None
                try:
                    os.close(descriptor)
                except OSError as exc:
                    return None if exc.errno == errno.EBADF else exc
                except BaseException as exc:
                    return exc
        except BaseException as exc:
            return exc

    def release(self, index: int) -> int:
        with _bounded_fd_critical_section():
            return self._detach(index)

    def transfer_to(self, index: int, target, attribute: str) -> int:
        with _bounded_fd_critical_section():
            descriptor = self._value(index)
            if descriptor < 0:
                return descriptor
            missing = object()
            target_attributes = vars(target)
            previous = target_attributes.get(attribute, missing)
            try:
                _set_bounded_launch_descriptor_target(
                    target,
                    attribute,
                    descriptor,
                )
            except BaseException as error:
                current = target_attributes.get(attribute, missing)
                self._detach(index)
                if previous is missing and current == descriptor:
                    raise
                close_error = _close_detached_descriptor_once(descriptor)
                if close_error is not None:
                    with suppress(BaseException):
                        error.add_note(
                            "launch descriptor transfer cleanup raised "
                            f"{type(close_error).__name__}"
                        )
                raise
            self._detach(index)
            return descriptor

    def close(self) -> list[BaseException]:
        errors: list[BaseException] = []
        try:
            with _bounded_fd_critical_section():
                descriptors = self._values
                self._values = (-1, -1)
                for descriptor in descriptors:
                    if descriptor < 0:
                        continue
                    try:
                        os.close(descriptor)
                    except OSError as exc:
                        if exc.errno != errno.EBADF:
                            errors.append(exc)
                    except BaseException as exc:
                        errors.append(exc)
        except BaseException as exc:
            errors.append(exc)
        return errors


def _close_unowned_descriptor_pair_once(
    descriptors: tuple[int, int],
) -> list[BaseException]:
    errors: list[BaseException] = []
    try:
        with _bounded_fd_critical_section():
            closed: set[int] = set()
            for descriptor in descriptors:
                if descriptor in closed or type(descriptor) is not int or descriptor < 0:
                    continue
                closed.add(descriptor)
                try:
                    os.close(descriptor)
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        errors.append(exc)
                except BaseException as exc:
                    errors.append(exc)
    except BaseException as exc:
        errors.append(exc)
    return errors


class _OwnedLaunchDescriptorView:
    def __init__(self, owner: _OwnedLaunchDescriptorPair, index: int) -> None:
        self._owner = owner
        self._index = index

    @property
    def value(self) -> int:
        return self._owner._value(self._index)

    def acquire(self, descriptor: int) -> None:
        self._owner.acquire_one(self._index, descriptor)

    def release(self) -> int:
        return self._owner.release(self._index)

    def transfer_to(self, target, attribute: str) -> int:
        return self._owner.transfer_to(self._index, target, attribute)

    def close(self) -> BaseException | None:
        return self._owner.close_one(self._index)


class _BoundedProcessLaunchResources:
    def __init__(self, launch_environment: dict[str, str]) -> None:
        self.launch_environment = launch_environment
        self.status_pair = _OwnedLaunchDescriptorPair(
            owner_token=_LOCAL_MODEL_DIRECT_STATUS_OWNER_CAPABILITY
        )
        self.status_read = _OwnedLaunchDescriptorView(self.status_pair, 0)
        self.status_write = _OwnedLaunchDescriptorView(self.status_pair, 1)
        self.stdin_file = None

    def close(self) -> list[BaseException]:
        errors: list[BaseException] = self.status_pair.close()
        try:
            with _bounded_fd_critical_section():
                stdin_file = self.stdin_file
                self.stdin_file = None
                if stdin_file is not None:
                    stdin_file.close()
        except BaseException as exc:
            errors.append(exc)
        return errors


@contextmanager
def _bounded_process_launch_resources(
    *,
    direct_supervisor: bool,
    environment: dict[str, str],
    deadline: float,
):
    resources = _BoundedProcessLaunchResources(environment)
    body_entered = False
    try:
        if direct_supervisor:
            pipe2 = getattr(os, "pipe2", None)
            if not callable(pipe2):
                raise OSError(errno.ENOSYS, "pipe2 is unavailable")
            status_descriptors: tuple[int, int] | None = None
            try:
                with _bounded_fd_critical_section():
                    status_descriptors = pipe2(
                        getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NONBLOCK", 0)
                    )
                    resources.status_pair.acquire_pair(status_descriptors)
            except BaseException as exc:
                if (
                    status_descriptors is not None
                    and not resources.status_pair.owns_pair(status_descriptors)
                ):
                    for cleanup_error in _close_unowned_descriptor_pair_once(
                        status_descriptors
                    ):
                        with suppress(BaseException):
                            exc.add_note(
                                "unowned status FD cleanup failed: "
                                f"{type(cleanup_error).__name__}"
                            )
                raise
            status_read_descriptor, status_write_descriptor = status_descriptors
            resources.launch_environment = dict(environment)
            resources.launch_environment.update(
                _local_model_direct_supervisor_environment(
                    status_write_descriptor,
                    deadline,
                )
            )
        resources.stdin_file = tempfile.TemporaryFile()
        body_entered = True
        yield resources
    except BaseException as exc:
        for cleanup_error in resources.close():
            with suppress(BaseException):
                exc.add_note(
                    "bounded process launch resource cleanup failed: "
                    f"{type(cleanup_error).__name__}"
                )
        if (
            not body_entered
            and direct_supervisor
            and isinstance(exc, (OSError, PriorityScopeError))
        ):
            raise CommandChainError(
                _LOCAL_MODEL_DIRECT_SUPERVISOR_FAILURE
            ) from None
        raise
    else:
        cleanup_errors = resources.close()
        if cleanup_errors:
            raise cleanup_errors[0]


def _run_bounded_cleanup_with_interrupt_retry(
    operation: Callable[[], bool],
) -> bool:
    for _attempt in range(2):
        try:
            return bool(operation())
        except BaseException:
            continue
    return False


def _terminate_local_model_direct_supervisor(
    proc: subprocess.Popen[bytes],
    *,
    process_tree: dict[int, str] | None = None,
    require_complete_scan: bool = True,
) -> bool:
    known_tree = dict(process_tree) if process_tree is not None else {}
    scan_incomplete = process_tree is None
    process_id = getattr(proc, "pid", None)
    if type(process_id) is int and process_id > 0:
        if process_tree is None:
            descendants = _retry_process_scan(
                lambda: _process_tree_descendant_identities(process_id)
            )
            if descendants is not None:
                known_tree.update(descendants)
                scan_incomplete = False
        pipe_holders = _retry_process_scan(
            lambda: _process_pipe_holder_identities(proc)
        )
        if pipe_holders is None:
            scan_incomplete = True
        else:
            known_tree.update(pipe_holders)

    def clean_known_tree() -> bool:
        if not known_tree:
            return not (require_complete_scan and scan_incomplete)
        kill_confirmed = _kill_output_process_tree(known_tree)
        stop_confirmed = _wait_for_output_process_tree_stop(known_tree)
        return bool(
            kill_confirmed
            and stop_confirmed
            and not (require_complete_scan and scan_incomplete)
        )

    if type(getattr(proc, "returncode", None)) is int:
        if _confirm_local_model_direct_cleanup(proc, True):
            return True
        clean_known_tree()
        return False
    expected_identity = vars(proc).get("_soc_process_identity")
    opener = getattr(os, "pidfd_open", None)
    sender = getattr(signal, "pidfd_send_signal", None)
    if (
        type(process_id) is not int
        or process_id <= 0
        or not isinstance(expected_identity, str)
        or not callable(opener)
        or not callable(sender)
        or _clipboard_lock_identity_for_pid(process_id) != expected_identity
    ):
        clean_known_tree()
        return False
    descriptor = -1
    try:
        descriptor = opener(process_id, 0)
        if _clipboard_lock_identity_for_pid(process_id) != expected_identity:
            clean_known_tree()
            return False
        try:
            sender(descriptor, signal.SIGTERM, None, 0)
        except ProcessLookupError:
            pass
        proc.wait(timeout=_PROCESS_POLL_INTERVAL_SECONDS + 2.5)
    except (OSError, subprocess.TimeoutExpired):
        if descriptor >= 0:
            try:
                sender(descriptor, signal.SIGKILL, None, 0)
            except (OSError, ProcessLookupError):
                pass
        try:
            proc.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            pass
        clean_known_tree()
        return False
    finally:
        try:
            with _bounded_fd_critical_section():
                pidfd_descriptor = descriptor
                descriptor = -1
                if pidfd_descriptor >= 0:
                    os.close(pidfd_descriptor)
        except OSError:
            pass
    if _confirm_local_model_direct_cleanup(proc, True):
        return True
    clean_known_tree()
    return False


# Identity-only capability. Code sharing this Python process can inspect this
# private object; that is a process-trust boundary, not a sandbox boundary.
_LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY = object()


def run_process_bounded_output(
    argv: Sequence[str],
    input_bytes: bytes = b"",
    *,
    timeout_seconds: int,
    max_output_bytes: int,
    env: dict[str, str],
    label: str,
    deadline: float | None = None,
    preserve_user_systemd_environment: bool = False,
    _local_model_direct_supervisor: object | None = None,
) -> tuple[int, bytes, bytes]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise CommandChainError("argv must be a non-empty sequence")
    if not all(isinstance(item, str) for item in argv):
        raise CommandChainError("argv must contain text")
    if not isinstance(input_bytes, bytes):
        raise CommandChainError("input bytes must be bytes")
    if len(input_bytes) > MAX_BOUNDED_PROCESS_INPUT_BYTES:
        raise CommandChainError(
            f"input bytes must not exceed {MAX_BOUNDED_PROCESS_INPUT_BYTES}"
        )
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if timeout_seconds > MAX_COMMAND_TIMEOUT_SECONDS:
        raise CommandChainError(
            f"timeout_seconds must not exceed {MAX_COMMAND_TIMEOUT_SECONDS}"
        )
    if (
        deadline is not None
        and (isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline))
    ):
        raise CommandChainError("deadline must be finite")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes <= 0:
        raise CommandChainError("max_output_bytes must be positive")
    if max_output_bytes > MAX_BOUNDED_PROCESS_OUTPUT_BYTES:
        raise CommandChainError(
            f"max_output_bytes must not exceed {MAX_BOUNDED_PROCESS_OUTPUT_BYTES}"
        )
    if not isinstance(env, dict):
        raise CommandChainError("environment must be a mapping")
    if not isinstance(preserve_user_systemd_environment, bool):
        raise CommandChainError("preserve_user_systemd_environment must be a boolean")
    _validate_command_label(label)
    env = _filtered_environment(env)
    if preserve_user_systemd_environment:
        for key in _USER_SYSTEMD_ENV_KEYS:
            value = _coerce_environment_value(key)
            if value is not None:
                env[key] = value

    runtime_argv = [*argv]
    process_deadline = (
        time.monotonic() + timeout_seconds if deadline is None else deadline
    )
    direct_supervisor = (
        _local_model_direct_supervisor
        is _LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY
    )
    with _bounded_process_launch_resources(
        direct_supervisor=direct_supervisor,
        environment=env,
        deadline=float(process_deadline),
    ) as launch_resources:
        stdin_file = launch_resources.stdin_file
        launch_environment = launch_resources.launch_environment
        stdin_file.write(input_bytes)
        stdin_file.seek(0)
        proc: subprocess.Popen[bytes] | None = None
        process_identity: str | None = None
        cleanup_attempted = False
        primary_exception: BaseException | None = None
        handoff_cleanup_errors: list[BaseException] = []
        try:
            if time.monotonic() >= process_deadline:
                raise CommandChainError(
                    _command_timeout_detail(label, timeout_seconds)
                )
            launch_result = _launch_bounded_process(
                runtime_argv,
                stdin_file=stdin_file,
                environment=launch_environment,
                status_descriptor=launch_resources.status_write.value,
                deadline=float(process_deadline),
            )
            proc = launch_result.process
            if launch_result.primary is not None:
                raise launch_result.primary
            if launch_result.outcome == _POPEN_OUTCOME_DEADLINE:
                raise CommandChainError(
                    _command_timeout_detail(label, timeout_seconds)
                ) from None
            if launch_result.outcome != _POPEN_OUTCOME_READY:
                _raise_bounded_popen_outcome(
                    launch_result.outcome or _POPEN_OUTCOME_INTERNAL,
                    launch_result.error_number,
                )
            if proc is None:
                raise CommandChainError("bounded process spawn failed")
            if launch_resources.status_write.value >= 0:
                close_error = launch_resources.status_write.close()
                if close_error is not None:
                    raise close_error
                _bind_local_model_direct_status_descriptor(
                    proc,
                    launch_resources.status_pair,
                )
            process_identity = _clipboard_lock_identity_for_pid(proc.pid)
            if not process_identity:
                cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                    lambda: _confirm_local_model_direct_cleanup(
                        proc,
                        _terminate_unidentified_bounded_process(
                            proc,
                            cleanup_errors=handoff_cleanup_errors,
                        ),
                    )
                )
                cleanup_attempted = True
                cleanup_suffix = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                raise CommandChainError(
                    f"{label} command process identity could not be verified{cleanup_suffix}"
                )
            _bind_bounded_process_identity(proc, process_identity)
        except BaseException as exc:
            status_close_error = launch_resources.status_write.close()
            if status_close_error is not None:
                handoff_cleanup_errors.append(status_close_error)
            if proc is not None and not cleanup_attempted:
                if (
                    direct_supervisor
                    and launch_resources.status_pair._value(0) >= 0
                    and _LOCAL_MODEL_DIRECT_STATUS_OWNER_ATTRIBUTE not in vars(proc)
                ):
                    try:
                        _bind_local_model_direct_status_descriptor(
                            proc,
                            launch_resources.status_pair,
                        )
                    except BaseException as transfer_error:
                        handoff_cleanup_errors.append(transfer_error)
                if (
                    isinstance(process_identity, str)
                    and process_identity
                    and "_soc_process_identity" not in vars(proc)
                ):
                    try:
                        setattr(proc, "_soc_process_identity", process_identity)
                    except BaseException:
                        pass
                if "_soc_process_identity" not in vars(proc):
                    try:
                        recovered_identity = _clipboard_lock_identity_for_pid(
                            proc.pid
                        )
                    except BaseException:
                        recovered_identity = None
                    if recovered_identity:
                        try:
                            setattr(
                                proc,
                                "_soc_process_identity",
                                recovered_identity,
                            )
                        except BaseException:
                            pass
                if "_soc_process_identity" in vars(proc):
                    cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                        lambda: _terminate_bounded_process(
                            proc,
                            require_complete_scan=True,
                        )
                    )
                else:
                    cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                        lambda: _confirm_local_model_direct_cleanup(
                            proc,
                            _terminate_unidentified_bounded_process(
                                proc,
                                cleanup_errors=handoff_cleanup_errors,
                            ),
                        )
                    )
                cleanup_attempted = True
                if not cleanup_confirmed:
                    exc.add_note(
                        f"{label} command process cleanup was not confirmed"
                    )
                handoff_cleanup_errors.extend(
                    _close_bounded_process_streams(proc)
                )
            _add_bounded_cleanup_notes(
                exc,
                handoff_cleanup_errors,
                prefix=f"{label} early cleanup",
            )
            raise

        try:
            selector = selectors.DefaultSelector()
        except BaseException as exc:
            # Popen owns the child and both pipes before selector construction.
            # Keep that ownership explicit so a selector backend failure cannot
            # strand a process or its file descriptors.
            cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                lambda: _terminate_bounded_process(
                    proc,
                    require_complete_scan=True,
                )
            )
            cleanup_attempted = True
            if not cleanup_confirmed:
                exc.add_note(f"{label} command process cleanup was not confirmed")
            selector_cleanup_errors = _close_bounded_process_streams(proc)
            _add_bounded_cleanup_notes(
                exc,
                selector_cleanup_errors,
                prefix=f"{label} selector cleanup",
            )
            raise
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_size = 0
        stderr_size = 0
        process_exit_deadline: float | None = None
        process_tree_snapshot: dict[int, str] | None = None
        process_tree_snapshot_scan_incomplete = False
        next_process_tree_snapshot = 0.0
        process_tree_at_exit: dict[int, str] | None = None
        process_tree_at_exit_scan_incomplete = False
        deferred_cleanup_errors: list[BaseException] = []
        def capture_process_tree_at_exit() -> None:
            nonlocal process_tree_at_exit, process_tree_at_exit_scan_incomplete
            if process_tree_at_exit is not None:
                return
            process_tree_at_exit = process_tree_snapshot or {}
            if process_tree_snapshot_scan_incomplete:
                process_tree_at_exit_scan_incomplete = True
            pipe_holders = _retry_process_scan(lambda: _process_pipe_holder_identities(proc))
            if pipe_holders is None:
                process_tree_at_exit_scan_incomplete = True
            else:
                process_tree_at_exit.update(pipe_holders)
            session_descendants = _retry_process_scan(
                lambda: _process_session_descendant_identities(
                    proc.pid,
                    expected_process_identity=vars(proc).get("_soc_process_identity"),
                )
            )
            if session_descendants is None:
                process_tree_at_exit_scan_incomplete = True
            else:
                process_tree_at_exit.update(session_descendants)

        try:
            if proc.stdout is not None:
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            if proc.stderr is not None:
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            while selector.get_map():
                now = time.monotonic()
                if now >= next_process_tree_snapshot:
                    current_process_tree = _retry_process_scan(
                        lambda: _process_tree_descendant_identities(proc.pid)
                    )
                    if current_process_tree is not None:
                        process_tree_snapshot_scan_incomplete = False
                        if process_tree_snapshot is None:
                            process_tree_snapshot = {}
                        process_tree_snapshot.update(current_process_tree)
                    else:
                        process_tree_snapshot_scan_incomplete = True
                    next_process_tree_snapshot = now + _PROCESS_TREE_SNAPSHOT_INTERVAL_SECONDS
                root_exited_before_poll = (
                    proc.returncode is None
                    and _process_has_exited_without_reaping(getattr(proc, "pid", 0))
                )
                if root_exited_before_poll:
                    capture_process_tree_at_exit()
                if proc.poll() is not None:
                    if root_exited_before_poll or _output_process_is_reaped(proc):
                        capture_process_tree_at_exit()
                    if process_exit_deadline is None:
                        process_exit_deadline = min(process_deadline, now + _PIPE_DRAIN_GRACE_SECONDS)
                    active_deadline = process_exit_deadline
                else:
                    active_deadline = process_deadline
                remaining = active_deadline - now
                if remaining <= 0:
                    if process_exit_deadline is not None:
                        break
                    cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                        lambda: _terminate_bounded_process(
                            proc,
                            require_complete_scan=True,
                        )
                    )
                    cleanup_attempted = True
                    cleanup_detail = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                    raise CommandChainError(
                        _command_timeout_detail(label, timeout_seconds) + cleanup_detail
                    )
                events = selector.select(min(remaining, _PROCESS_POLL_INTERVAL_SECONDS))
                if not events:
                    continue
                for key, _event_mask in events:
                    stream = key.fileobj
                    while True:
                        try:
                            data = os.read(stream.fileno(), 65_536)
                            break
                        except InterruptedError:
                            continue
                    if not data:
                        _run_bounded_final_cleanup_step(
                            lambda stream=stream: _unregister_bounded_process_stream(
                                selector,
                                stream,
                            ),
                            deferred_cleanup_errors,
                        )
                        _run_bounded_final_cleanup_step(
                            lambda stream=stream: _close_bounded_process_stream(
                                stream,
                                proc,
                            ),
                            deferred_cleanup_errors,
                        )
                        continue
                    if key.data == "stdout":
                        stdout_chunks.append(data)
                        stdout_size += len(data)
                    else:
                        stderr_chunks.append(data)
                        stderr_size += len(data)
                    if stdout_size + stderr_size > max_output_bytes:
                        cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                            lambda: _terminate_bounded_process(
                                proc,
                                require_complete_scan=True,
                            )
                        )
                        cleanup_attempted = True
                        cleanup_detail = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                        raise CommandChainError(
                            f"{label} command output exceeded {max_output_bytes} bytes" + cleanup_detail
                        )
            try:
                if (
                    proc.returncode is None
                    and _process_has_exited_without_reaping(getattr(proc, "pid", 0))
                ):
                    capture_process_tree_at_exit()
                returncode = proc.poll()
                if returncode is None:
                    returncode = proc.wait(timeout=max(0.0, process_deadline - time.monotonic()))
                if returncode is not None:
                    capture_process_tree_at_exit()
            except subprocess.TimeoutExpired:
                cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                    lambda: _terminate_bounded_process(
                        proc,
                        require_complete_scan=True,
                    )
                )
                cleanup_attempted = True
                cleanup_detail = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                raise CommandChainError(
                    _command_timeout_detail(label, timeout_seconds) + cleanup_detail
                ) from None
            if process_tree_at_exit is not None:
                cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                    lambda: _terminate_bounded_process(
                        proc,
                        process_tree=process_tree_at_exit,
                        require_complete_scan=True,
                    )
                )
                cleanup_attempted = True
                if not cleanup_confirmed:
                    if direct_supervisor and _local_model_direct_status(proc) is None:
                        raise CommandChainError(
                            f"{label} command {_LOCAL_MODEL_DIRECT_SUPERVISOR_FAILURE}"
                        )
                    raise CommandChainError(f"{label} command descendant cleanup was not confirmed")
            if (
                process_tree_at_exit_scan_incomplete
                and not (
                    direct_supervisor
                    and _local_model_direct_status(proc) is not None
                )
            ):
                raise CommandChainError(f"{label} command descendant cleanup scan was incomplete")
            if direct_supervisor:
                status_result = _local_model_direct_status(proc)
                if status_result is None:
                    raise CommandChainError(
                        f"{label} command {_LOCAL_MODEL_DIRECT_SUPERVISOR_FAILURE}"
                    )
                status_kind, status_returncode = status_result
                if status_kind == "deadline":
                    raise CommandChainError(
                        _command_timeout_detail(label, timeout_seconds)
                    )
                if status_kind != "target" or type(status_returncode) is not int:
                    raise CommandChainError(
                        f"{label} command {_LOCAL_MODEL_DIRECT_SUPERVISOR_FAILURE}"
                    )
                returncode = status_returncode
            return returncode, b"".join(stdout_chunks), b"".join(stderr_chunks)
        except BaseException as exc:
            primary_exception = exc
            if not cleanup_attempted:
                cleanup_tree = process_tree_at_exit or process_tree_snapshot
                if process_tree_at_exit is None and process_tree_snapshot_scan_incomplete:
                    cleanup_tree = None
                cleanup_confirmed = _run_bounded_cleanup_with_interrupt_retry(
                    lambda: _terminate_bounded_process(
                        proc,
                        process_tree=cleanup_tree,
                        require_complete_scan=True,
                    )
                )
                cleanup_attempted = True
                if not cleanup_confirmed:
                    exc.add_note(f"{label} command process cleanup was not confirmed")
            raise
        finally:
            cleanup_errors = deferred_cleanup_errors + _close_bounded_process_final_resources(
                proc,
                selector,
            )
            if primary_exception is not None:
                for cleanup_error in cleanup_errors:
                    with suppress(BaseException):
                        primary_exception.add_note(
                            f"{label} final cleanup raised "
                            f"{type(cleanup_error).__name__}"
                        )
            elif cleanup_errors:
                raise cleanup_errors[0]


FORBIDDEN_COMMAND_OPERATORS = {
    "|",
    "||",
    "|&",
    "&",
    ";",
    ";;",
    "<",
    ">",
    ">>",
    "2>",
    "2>>",
    "1>",
    "1>>",
    "&>",
    "2>&1",
    "1>&2",
    "1>&1",
    "2>&2",
    "2<&1",
    "1<&0",
}
DEFAULT_COMMAND_TIMEOUT_SECONDS = 180
MAX_COMMAND_TIMEOUT_SECONDS = 3600
MAX_COMMAND_OUTPUT_CHARS = 1_000_000
MAX_COMMAND_LENGTH_CHARS = 8_192
MAX_COMMAND_SEGMENTS = 32
MAX_COMMAND_SEGMENT_TOKENS = 128
MAX_COMMAND_INPUT_CHARS = 1_000_000
MAX_FILE_READ_FOR_ERROR_CHARS = 4096
MAX_BOUNDED_PROCESS_INPUT_BYTES = (MAX_COMMAND_INPUT_CHARS * 4) + 4096
MAX_BOUNDED_PROCESS_OUTPUT_BYTES = (MAX_COMMAND_OUTPUT_CHARS * 4) + 4096


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise CommandChainError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise CommandChainError("command is empty")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise CommandChainError("command must be a bare command name without path separators")
    if _contains_command_control_chars(command_name):
        raise CommandChainError("command contains invalid control character")
    resolved = _which(command_name)
    if not resolved:
        raise CommandChainError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    return "\x00" in value


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    lowered = (value or "").lower()
    if _ESCAPED_CONTROL_RE.search(lowered):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _contains_environment_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    return any(
        (codepoint := ord(char)) < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F
        for char in value
    )


def _contains_command_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    if _ESCAPED_CONTROL_RE.search(value.lower()):
        return True
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            if ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F:
                return True
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            if char == "\n" and quote is not None:
                continue
            return True
    return False


def _contains_command_argument_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    if _ESCAPED_CONTROL_RE.search(value.lower()):
        return True
    for char in value:
        codepoint = ord(char)
        if char == "\n":
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _contains_command_output_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    for char in value:
        codepoint = ord(char)
        if codepoint in (0x09, 0x0A, 0x0D):
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _validate_command_label(label: str) -> None:
    if isinstance(label, bool) or not isinstance(label, str):
        raise CommandChainError("label must be text")
    if _contains_http_header_control_chars(label):
        raise CommandChainError("label contains invalid control character")


def split_command_chain(command: str, label: str = "command") -> list[list[str]]:
    if isinstance(command, bool) or not isinstance(command, str):
        raise CommandChainError("command must be text")
    _validate_command_label(label)
    if _contains_escaped_null(command):
        raise CommandChainError(f"invalid {label} command: contains invalid null byte")
    if _contains_command_control_chars(command):
        raise CommandChainError(f"invalid {label} command: contains control characters")
    if len(command) > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")
    try:
        command_bytes_len = len(command.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise CommandChainError(f"invalid {label} command: not valid UTF-8") from exc
    if command_bytes_len > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")

    raw_segments = [""]
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            raw_segments[-1] += char
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            raw_segments[-1] += char
            escaped = True
            index += 1
            continue
        if quote is not None:
            raw_segments[-1] += char
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            raw_segments[-1] += char
            quote = char
            index += 1
            continue
        if (
            command.startswith(_CHAIN_SEGMENT_SEPARATOR, index)
            and (index == 0 or command[index - 1].isspace())
            and (index + 2 == len(command) or command[index + 2].isspace())
        ):
            raw_segments.append("")
            index += 2
            continue
        raw_segments[-1] += char
        index += 1

    segments: list[list[str]] = []
    for segment_index, raw_segment in enumerate(raw_segments):
        try:
            tokens = shlex.split(raw_segment)
        except ValueError as exc:
            raise CommandChainError(f"invalid {label} command: {exc}") from exc

        if not tokens:
            if not segments:
                raise CommandChainError(f"{label} command is empty")
            if len(segments) >= MAX_COMMAND_SEGMENTS:
                raise CommandChainError(f"{label} command has too many segments")
            raise CommandChainError(
                f"{label} command ended with &&" if segment_index == len(raw_segments) - 1
                else f"empty {label} command segment before &&"
            )
        if len(segments) >= MAX_COMMAND_SEGMENTS:
            raise CommandChainError(f"{label} command has too many segments")
        if len(tokens) > MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        segments.append(tokens)

    if not segments:
        raise CommandChainError(f"{label} command is empty")
    for segment in segments:
        for token in segment:
            if token in FORBIDDEN_COMMAND_OPERATORS:
                raise CommandChainError(f"unsupported shell operator in {label} command: {token}")
    return segments


def run_command_chain(
    segments: Sequence[Sequence[str]],
    input_text: str,
    *,
    label: str,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_output_chars: int = MAX_COMMAND_OUTPUT_CHARS,
    max_input_chars: int = MAX_COMMAND_INPUT_CHARS,
    personal_context: str = "",
    vocabulary: str = "",
    include_personalization_env: bool = False,
    local_model_priority: bool = False,
) -> str:
    if not isinstance(segments, (list, tuple)):
        raise CommandChainError("segments must be a sequence")
    if not all(isinstance(segment, (list, tuple)) for segment in segments):
        raise CommandChainError("segments must contain sequences")
    if not segments:
        raise CommandChainError(f"{label} command chain is empty")
    _validate_command_label(label)
    if len(segments) > MAX_COMMAND_SEGMENTS:
        raise CommandChainError(f"{label} command has too many segments")
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool):
        raise CommandChainError("max_output_chars must be an integer")
    if max_output_chars <= 0:
        raise CommandChainError("max_output_chars must be positive")
    if max_output_chars > MAX_COMMAND_OUTPUT_CHARS:
        raise CommandChainError(f"max_output_chars must not exceed {MAX_COMMAND_OUTPUT_CHARS}")
    if not isinstance(max_input_chars, int) or isinstance(max_input_chars, bool):
        raise CommandChainError("max_input_chars must be an integer")
    if max_input_chars > MAX_COMMAND_INPUT_CHARS:
        raise CommandChainError(f"max_input_chars must not exceed {MAX_COMMAND_INPUT_CHARS}")
    if max_input_chars < 0:
        raise CommandChainError("max_input_chars must be non-negative")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise CommandChainError("timeout_seconds must be an integer")
    if timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if timeout_seconds > MAX_COMMAND_TIMEOUT_SECONDS:
        raise CommandChainError(
            f"timeout_seconds must not exceed {MAX_COMMAND_TIMEOUT_SECONDS}"
        )
    if not isinstance(input_text, str) or isinstance(input_text, bool):
        raise CommandChainError("input text must be text")
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise CommandChainError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise CommandChainError("vocabulary must be text")
    if not isinstance(include_personalization_env, bool):
        raise CommandChainError("include_personalization_env must be a boolean")
    if not isinstance(local_model_priority, bool):
        raise CommandChainError("local_model_priority must be a boolean")
    if _contains_escaped_null(input_text):
        raise CommandChainError("command input contains invalid null byte")
    try:
        input_bytes = input_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CommandChainError("command input is not valid UTF-8") from exc

    try:
        personalization_env = command_environment(personal_context, vocabulary)
        env = (
            {key: value for key, value in personalization_env.items() if isinstance(key, str) and not _is_unsafe_env_var(key)}
            if include_personalization_env
            else {}
        )
        env = _filtered_environment(env)
    except ValueError as exc:
        raise CommandChainError(str(exc)) from exc
    output = input_text
    chain_deadline = time.monotonic() + timeout_seconds
    local_model_scope_available: bool | None = None
    local_model_direct_supervisor = False

    for segment in segments:
        if time.monotonic() >= chain_deadline:
            raise CommandChainError(f"{label} command timed out after {timeout_seconds}s")
        if len(output) > max_input_chars:
            raise CommandChainError(f"{label} command input exceeded {max_input_chars} characters")

        cmd = list(segment)
        if len(cmd) > MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        if not all(isinstance(item, str) for item in cmd):
            raise CommandChainError(f"{label} command segment contains non-text item")
        if not cmd:
            raise CommandChainError(f"invalid {label} command segment")
        try:
            command_chars_len = sum(len(item) + 1 for item in cmd)
            command_bytes_len = sum(len(item.encode("utf-8")) + 1 for item in cmd)
        except UnicodeEncodeError as exc:
            raise CommandChainError(f"invalid {label} command: not valid UTF-8") from exc
        if command_chars_len > MAX_COMMAND_LENGTH_CHARS or command_bytes_len > MAX_COMMAND_LENGTH_CHARS:
            raise CommandChainError(f"invalid {label} command: command too long")
        executable = str(cmd[0]).strip()
        if not executable:
            raise CommandChainError(f"invalid {label} command segment")
        if _contains_escaped_null(executable) or any(_contains_escaped_null(str(arg)) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid null byte")
        if _contains_command_control_chars(executable) or any(
            _contains_command_argument_control_chars(str(arg)) for arg in cmd[1:]
        ):
            raise CommandChainError(f"{label} command contains invalid control character")
        runtime_command = _command_path(executable)
        runtime_argv = [runtime_command, *cmd[1:]]
        if local_model_priority:
            if local_model_scope_available is None:
                try:
                    probe_argv = local_model_scope_probe_command()
                except LocalModelPriorityError as exc:
                    raise CommandChainError(str(exc)) from exc
                try:
                    probe_returncode, _, _ = run_process_bounded_output(
                        probe_argv,
                        b"",
                        timeout_seconds=timeout_seconds,
                        max_output_bytes=_LOCAL_MODEL_PRIORITY_PROBE_MAX_OUTPUT_BYTES,
                        env=env,
                        label="local model priority probe",
                        deadline=chain_deadline,
                        preserve_user_systemd_environment=True,
                    )
                except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
                    raise CommandChainError(
                        "local model priority probe execution failed"
                    ) from None
                if probe_returncode < 0:
                    raise CommandChainError(
                        "local model priority probe terminated by signal"
                    )
                local_model_scope_available = probe_returncode == 0
            try:
                if local_model_scope_available:
                    runtime_argv = local_model_command(runtime_argv)
                else:
                    runtime_argv = local_model_direct_command(runtime_argv)
                    local_model_direct_supervisor = (
                        _LOCAL_MODEL_DIRECT_SUPERVISOR_CAPABILITY
                    )
            except LocalModelPriorityError as exc:
                raise CommandChainError(str(exc)) from exc
        process_env = env
        preserve_user_systemd_environment = bool(
            local_model_priority and local_model_scope_available
        )
        if local_model_priority and not local_model_scope_available:
            process_env = {
                key: value
                for key, value in env.items()
                if key not in _USER_SYSTEMD_ENV_KEYS
            }
        try:
            max_output_bytes = (max_output_chars * 4) + 4096
            returncode, stdout_data, stderr_data = run_process_bounded_output(
                runtime_argv,
                input_bytes,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                env=process_env,
                label=label,
                deadline=chain_deadline,
                preserve_user_systemd_environment=preserve_user_systemd_environment,
                _local_model_direct_supervisor=local_model_direct_supervisor,
            )
            if returncode != 0:
                detail = _command_failure_detail(returncode, len(stdout_data), len(stderr_data))
                raise CommandChainError(f"{label} command failed: {detail}")
            try:
                decoded_output = stdout_data.decode("utf-8")
            except UnicodeDecodeError:
                raise CommandChainError("command output is not valid UTF-8") from None
            segment_output = decoded_output.rstrip("\r\n")
            if len(segment_output) > max_output_chars:
                raise CommandChainError(f"{label} command output exceeded {max_output_chars} characters")
            if _contains_escaped_null(segment_output):
                raise CommandChainError("command output contains invalid null byte")
            if _contains_command_output_control_chars(segment_output):
                raise CommandChainError("command output contains invalid control character")
            output = segment_output
            try:
                input_bytes = output.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise CommandChainError("command output is not valid UTF-8") from exc
        except FileNotFoundError as exc:
            raise CommandChainError(f"{label} command not found: {executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandChainError(f"{label} command timed out after {timeout_seconds}s") from exc
        except OSError:
            raise CommandChainError(f"{label} command execution failed") from None
    return output


def _filesize(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise CommandChainError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise CommandChainError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise CommandChainError("max_chars must be an integer")
    if max_chars <= 0:
        raise CommandChainError("max_chars must be positive")
    file.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")()
    text_parts: list[str] = []
    text_length = 0
    try:
        while text_length < max_chars:
            raw = file.read(4096)
            if not raw:
                break
            for byte in raw:
                decoded = decoder.decode(bytes((byte,)), final=False)
                if not decoded:
                    continue
                remaining = max_chars - text_length
                text_parts.append(decoded[:remaining])
                text_length += min(len(decoded), remaining)
                if text_length >= max_chars:
                    break
        if text_length < max_chars:
            tail = decoder.decode(b"", final=True)
            if tail:
                text_parts.append(tail[: max_chars - text_length])
        text = "".join(text_parts)
    except UnicodeDecodeError:
        raise CommandChainError("command output is not valid UTF-8") from None
    if _contains_escaped_null(text):
        raise CommandChainError("command output contains invalid null byte")
    return text
_CHAIN_SEGMENT_SEPARATOR = "".join(["&", "&"])
