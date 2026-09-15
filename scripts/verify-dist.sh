#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"
readonly MAX_DIST_ARCHIVE_BYTES=$((128 * 1024 * 1024))
readonly MAX_DIST_MEMBERS=2000
readonly MAX_DIST_PATH_CHARS=240
readonly MAX_DIST_PATH_DEPTH=20
readonly MAX_DIST_FILE_BYTES=$((32 * 1024 * 1024))
readonly MAX_DIST_TOTAL_EXTRACTED_BYTES=$((256 * 1024 * 1024))
readonly MAX_DIST_LISTING_BYTES=$((16 * 1024 * 1024))
readonly DIST_VERIFY_TIMEOUT_SECONDS=120
readonly VERIFY_PYTHON_INTERPRETER="/usr/bin/python3"
readonly DEADLINE_HELPER_TIMEOUT_SECONDS=2
readonly MAX_TAR_STDERR_BYTES=$((64 * 1024))
readonly MAX_VERIFY_SWEEP_ENTRIES=32
readonly VERIFY_RESIDUE_MAX_AGE_SECONDS=$((60 * 60))
readonly INTERNAL_LOCK_MARKER="__verify-dist-locked-v1"

for python_env_name in ${!PYTHON@}; do
  unset "${python_env_name}"
done

lock_handoff_mode=0
parent_lock_fd=""
parent_lock_identity=""
parent_lock_chain=""
handoff_deadline=""
if [[ $# -eq 1 ]]; then
  tarball_input="$1"
elif [[ $# -eq 6 ]]; then
  if [[ "$2" != "${INTERNAL_LOCK_MARKER}" ]]; then
    printf 'usage: %s dist/speed-of-cinnamon-VERSION.tar.gz\n' "$0" >&2
    exit 2
  fi
  lock_handoff_mode=1
  tarball_input="$1"
  parent_lock_fd="$3"
  parent_lock_identity="$4"
  parent_lock_chain="$5"
  handoff_deadline="$6"
else
  printf 'usage: %s dist/speed-of-cinnamon-VERSION.tar.gz\n' "$0" >&2
  exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=("${VERIFY_PYTHON_INTERPRETER}" -I -S -B "${safe_fs}")
dist_dir="${repo_dir}/dist"

if (( lock_handoff_mode == 0 )); then
  verify_timeout_seconds="${VERIFY_DIST_TIMEOUT_SECONDS:-${DIST_VERIFY_TIMEOUT_SECONDS}}"
  if [[ ! "${verify_timeout_seconds}" =~ ^[1-9][0-9]*$ ]] || (( verify_timeout_seconds > DIST_VERIFY_TIMEOUT_SECONDS )); then
    printf 'verification timeout is outside allowed bounds\n' >&2
    exit 2
  fi
else
  verify_timeout_seconds="${DIST_VERIFY_TIMEOUT_SECONDS}"
  verify_deadline_monotonic="${handoff_deadline}"
fi

remaining_deadline() {
  local deadline=${1:-}
  local remaining
  if ! remaining="$(timeout --signal=KILL "${DEADLINE_HELPER_TIMEOUT_SECONDS}s" "${VERIFY_PYTHON_INTERPRETER}" -I -S -B - "${deadline}" "${DIST_VERIFY_TIMEOUT_SECONDS}" <<'PY'
import math
import sys
import time

try:
    deadline = float(sys.argv[1])
    maximum = float(sys.argv[2])
except (IndexError, TypeError, ValueError):
    raise SystemExit("verification deadline is invalid")
now = time.monotonic()
if not math.isfinite(deadline) or not math.isfinite(maximum):
    raise SystemExit("verification deadline is invalid")
if deadline <= now or deadline > now + maximum:
    raise SystemExit("verification deadline exceeded")
print(f"{deadline - now:.6f}")
PY
)"; then
    return 124
  fi
  printf '%s\n' "${remaining}"
}

run_command_bounded() {
  local remaining
  local status=0
  if ! remaining="$(remaining_deadline "${verify_deadline_monotonic}")"; then
    return 124
  fi
  timeout --signal=KILL "${remaining}s" "$@" || status=$?
  if ! remaining_deadline "${verify_deadline_monotonic}" >/dev/null; then
    return 124
  fi
  return "${status}"
}

run_duplex_command_bounded() {
  local command=$1
  local stdout_limit=$2
  local stderr_limit=$3
  shift 3
  run_command_bounded "${VERIFY_PYTHON_INTERPRETER}" -I -S -B - "${command}" "${stdout_limit}" "${stderr_limit}" "$@" <<'PY'
import ctypes
import os
import selectors
import signal
import sys
import time

command = sys.argv[1]
max_stdout = int(sys.argv[2])
max_stderr = int(sys.argv[3])
command_arguments = sys.argv[4:]
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])
process_grace_seconds = 0.25
process_kill_reserve_seconds = 0.05
cleanup_window = process_grace_seconds + process_kill_reserve_seconds
cleanup_deadline = deadline + cleanup_window


class DeadlineReached(Exception):
    pass


class HelperFailure(Exception):
    pass


def check_deadline(limit):
    if time.monotonic() >= limit:
        raise DeadlineReached


def direct_child_pids(limit):
    check_deadline(limit)
    descriptor = os.open(
        f"/proc/{os.getpid()}/task/{os.getpid()}/children",
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        raw = os.read(descriptor, 65536)
    finally:
        os.close(descriptor)
    if len(raw) >= 65536:
        raise HelperFailure
    try:
        return {int(value) for value in raw.split()}
    except (TypeError, ValueError):
        raise HelperFailure from None


def enable_subreaper():
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        prctl = libc.prctl
    except (AttributeError, OSError):
        raise HelperFailure from None
    prctl.argtypes = [
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    if prctl(36, 1, 0, 0, 0) != 0:
        raise HelperFailure


def open_pidfd(pid):
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise HelperFailure
    descriptor = None
    try:
        descriptor = os.pidfd_open(pid, 0)
        os.set_inheritable(descriptor, False)
        return descriptor
    except ProcessLookupError:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise
    except (OSError, ValueError):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise HelperFailure from None


def close_owned(descriptor):
    if descriptor is None:
        return True
    try:
        os.close(descriptor)
    except (OSError, ValueError):
        return False
    return True


def close_inherited_fds():
    try:
        descriptors = os.listdir("/proc/self/fd")
    except OSError as exc:
        raise HelperFailure from exc
    for name in descriptors:
        try:
            descriptor = int(name)
        except ValueError:
            continue
        if descriptor > 2:
            try:
                os.close(descriptor)
            except OSError:
                pass


def wait_status_code(status):
    try:
        code = os.waitstatus_to_exitcode(status)
    except (AttributeError, ValueError):
        return 125
    if code < 0:
        return 128 - code
    return code if code <= 255 else 125


def send_pidfd(descriptor, signal_type, limit):
    check_deadline(limit)
    try:
        signal.pidfd_send_signal(descriptor, signal_type)
    except ProcessLookupError:
        return
    except OSError:
        raise HelperFailure from None


def launcher_main(
    launcher_status_write,
    watchdog_status_write,
    stdout_write,
    stderr_write,
    control_read,
    parent_pidfd,
):
    launcher_status = 125
    try:
        target_pid = os.fork()
        if target_pid == 0:
            try:
                os.setsid()
                devnull = os.open(os.devnull, os.O_RDONLY | os.O_CLOEXEC)
                os.dup2(devnull, 0)
                os.dup2(stdout_write, 1)
                os.dup2(stderr_write, 2)
                for descriptor in (
                    devnull,
                    stdout_write,
                    stderr_write,
                    launcher_status_write,
                    watchdog_status_write,
                    control_read,
                    parent_pidfd,
                ):
                    if descriptor > 2:
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                os.execvpe(command, [command, *command_arguments], os.environ)
            except BaseException:
                os._exit(127)
        for descriptor in (
            stdout_write,
            stderr_write,
            watchdog_status_write,
            control_read,
            parent_pidfd,
        ):
            os.close(descriptor)
        waited_pid, status = os.waitpid(target_pid, 0)
        if waited_pid == target_pid:
            launcher_status = wait_status_code(status)
    except BaseException:
        launcher_status = 125
    try:
        os.write(launcher_status_write, bytes((launcher_status,)))
    except OSError:
        pass
    try:
        os.close(launcher_status_write)
    except OSError:
        pass
    os._exit(0)


def watchdog_main(
    control_read,
    status_write,
    stdout_write,
    stderr_write,
    parent_pid,
):
    launcher_status_read = None
    parent_pidfd = None
    launcher_pidfd = None
    launcher_status_write = None
    launcher_pid = None
    launcher_reaped = False
    launcher_status = None
    launcher_status_closed = False
    adopted_pidfds = {}
    cleanup_failed = False
    wait_selector = None
    wait_selector_fds = set()
    wait_selector_failed = False

    def close_adopted():
        nonlocal cleanup_failed
        for pid in list(adopted_pidfds):
            descriptor = adopted_pidfds.pop(pid)
            unregister_wait_descriptor(descriptor)
            if not close_owned(descriptor):
                cleanup_failed = True

    def reap_launcher():
        nonlocal launcher_reaped, cleanup_failed
        if launcher_reaped:
            return
        check_deadline(cleanup_deadline)
        try:
            waited_pid, _status = os.waitpid(launcher_pid, os.WNOHANG)
        except ChildProcessError:
            cleanup_failed = True
            return
        if waited_pid == launcher_pid:
            launcher_reaped = True

    def read_launcher_status():
        nonlocal launcher_status, launcher_status_closed, cleanup_failed
        if launcher_status_read is None or launcher_status_closed:
            return
        check_deadline(cleanup_deadline)
        try:
            data = os.read(launcher_status_read, 2)
        except BlockingIOError:
            return
        except OSError:
            cleanup_failed = True
            return
        if not data:
            launcher_status_closed = True
        elif len(data) != 1 or launcher_status is not None:
            cleanup_failed = True
        else:
            launcher_status = data[0]

    def bind_adopted():
        nonlocal cleanup_failed
        if not launcher_reaped:
            return
        for pid in direct_child_pids(cleanup_deadline):
            if pid in adopted_pidfds:
                continue
            try:
                adopted_pidfds[pid] = open_pidfd(pid)
            except ProcessLookupError:
                continue
            except HelperFailure:
                cleanup_failed = True
                raise

    def unregister_wait_descriptor(descriptor):
        nonlocal cleanup_failed
        if descriptor not in wait_selector_fds:
            return
        try:
            if wait_selector is not None:
                wait_selector.unregister(descriptor)
        except (KeyError, OSError, ValueError):
            cleanup_failed = True
        finally:
            wait_selector_fds.discard(descriptor)

    def reap_adopted():
        nonlocal cleanup_failed
        for pid in list(adopted_pidfds):
            check_deadline(cleanup_deadline)
            try:
                waited_pid, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                descriptor = adopted_pidfds.pop(pid)
                unregister_wait_descriptor(descriptor)
                if not close_owned(descriptor):
                    cleanup_failed = True
                continue
            if waited_pid == pid:
                descriptor = adopted_pidfds.pop(pid)
                unregister_wait_descriptor(descriptor)
                if not close_owned(descriptor):
                    cleanup_failed = True

    def signal_adopted(signal_type, limit):
        nonlocal cleanup_failed
        try:
            bind_adopted()
        except DeadlineReached:
            raise
        except BaseException:
            cleanup_failed = True
        for descriptor in tuple(adopted_pidfds.values()):
            try:
                send_pidfd(descriptor, signal_type, limit)
            except DeadlineReached:
                raise
            except BaseException:
                cleanup_failed = True

    def no_children():
        bind_adopted()
        reap_adopted()
        return launcher_reaped and not adopted_pidfds and not direct_child_pids(
            cleanup_deadline
        )

    def wait_for_descriptors(descriptors, wait_for):
        nonlocal cleanup_failed, wait_selector, wait_selector_failed
        if wait_for <= 0:
            return ()
        if wait_selector_failed:
            time.sleep(wait_for)
            return ()
        desired = {descriptor for descriptor in descriptors if descriptor is not None}
        if not desired:
            time.sleep(wait_for)
            return ()
        try:
            if wait_selector is None:
                wait_selector = selectors.DefaultSelector()
            for descriptor in tuple(wait_selector_fds - desired):
                wait_selector.unregister(descriptor)
                wait_selector_fds.remove(descriptor)
            for descriptor in desired - wait_selector_fds:
                wait_selector.register(descriptor, selectors.EVENT_READ)
                wait_selector_fds.add(descriptor)
            return wait_selector.select(wait_for)
        except BaseException:
            cleanup_failed = True
            wait_selector_failed = True
            if wait_selector is not None:
                try:
                    wait_selector.close()
                except BaseException:
                    pass
            wait_selector = None
            wait_selector_fds.clear()
            time.sleep(min(wait_for, 0.001))
            return ()

    def reap_until(stop_at, signal_type=None):
        while time.monotonic() < stop_at and time.monotonic() < cleanup_deadline:
            reap_launcher()
            read_launcher_status()
            if launcher_reaped:
                if signal_type is not None:
                    signal_adopted(signal_type, stop_at)
                else:
                    bind_adopted()
                reap_adopted()
                if not adopted_pidfds and not direct_child_pids(cleanup_deadline):
                    return True
            descriptors = (
                (() if launcher_reaped else (launcher_pidfd,))
                + tuple(adopted_pidfds.values())
            )
            if launcher_status_read is not None and not launcher_status_closed:
                descriptors += (launcher_status_read,)
            descriptors = tuple(
                descriptor for descriptor in descriptors if descriptor is not None
            )
            wait_for = min(
                stop_at - time.monotonic(),
                cleanup_deadline - time.monotonic(),
                0.01,
            )
            if wait_for <= 0:
                break
            wait_for_descriptors(descriptors, wait_for)
        return False

    def cleanup():
        nonlocal cleanup_failed, wait_selector
        remaining = True
        try:
            if not launcher_reaped:
                send_pidfd(launcher_pidfd, signal.SIGTERM, cleanup_deadline)
            term_end = min(
                cleanup_deadline,
                time.monotonic() + process_grace_seconds,
            )
            reap_until(term_end, signal.SIGTERM)
            remaining = not no_children()
        except BaseException:
            cleanup_failed = True
        if remaining and time.monotonic() < cleanup_deadline:
            try:
                if not launcher_reaped:
                    send_pidfd(launcher_pidfd, signal.SIGKILL, cleanup_deadline)
            except DeadlineReached:
                cleanup_failed = True
            except BaseException:
                cleanup_failed = True
            try:
                signal_adopted(signal.SIGKILL, cleanup_deadline)
            except DeadlineReached:
                cleanup_failed = True
            except BaseException:
                cleanup_failed = True
            try:
                reap_until(cleanup_deadline, signal.SIGKILL)
            except BaseException:
                cleanup_failed = True
            try:
                if not no_children():
                    cleanup_failed = True
            except BaseException:
                cleanup_failed = True
        elif remaining:
            cleanup_failed = True
        if wait_selector is not None:
            try:
                wait_selector.close()
            except BaseException:
                cleanup_failed = True
            wait_selector = None
            wait_selector_fds.clear()
        try:
            close_adopted()
        except BaseException:
            cleanup_failed = True
        for descriptor in (
            launcher_pidfd,
            parent_pidfd,
            launcher_status_read,
            launcher_status_write,
            control_read,
            stdout_write,
            stderr_write,
        ):
            try:
                closed = close_owned(descriptor)
            except BaseException:
                closed = False
            if not closed:
                cleanup_failed = True

    result = 125
    try:
        enable_subreaper()
        if direct_child_pids(cleanup_deadline):
            raise HelperFailure
        parent_pidfd = open_pidfd(parent_pid)
        os.set_blocking(control_read, False)
        os.set_blocking(status_write, False)
        launcher_status_read, launcher_status_write = os.pipe2(os.O_CLOEXEC)
        os.set_blocking(launcher_status_read, False)
        launcher_pid = os.fork()
        if launcher_pid == 0:
            try:
                os.close(launcher_status_read)
            except OSError:
                os._exit(125)
            launcher_main(
                launcher_status_write,
                status_write,
                stdout_write,
                stderr_write,
                control_read,
                parent_pidfd,
            )
        os.close(launcher_status_write)
        launcher_status_write = None
        for descriptor in (stdout_write, stderr_write):
            if not close_owned(descriptor):
                cleanup_failed = True
        stdout_write = stderr_write = None
        launcher_pidfd = open_pidfd(launcher_pid)
        finish_requested = False
        while True:
            check_deadline(cleanup_deadline)
            reap_launcher()
            read_launcher_status()
            if cleanup_failed:
                break
            if finish_requested and launcher_status is not None:
                if no_children():
                    result = launcher_status
                    break
                if launcher_reaped:
                    result = 125
                    cleanup()
                    return result
            if time.monotonic() >= deadline - cleanup_window:
                result = 124
                cleanup()
                return result
            readable = [control_read, parent_pidfd, launcher_pidfd]
            if launcher_status_read is not None and not launcher_status_closed:
                readable.append(launcher_status_read)
            wait_timeout = min(
                0.05,
                deadline - cleanup_window - time.monotonic(),
            )
            if wait_timeout <= 0:
                result = 124
                cleanup()
                return result
            ready = {
                key.fd for key, _mask in wait_for_descriptors(readable, wait_timeout)
            }
            if time.monotonic() >= deadline - cleanup_window:
                result = 124
                cleanup()
                return result
            if parent_pidfd in ready:
                result = 125
                cleanup()
                return result
            if control_read in ready:
                try:
                    data = os.read(control_read, 2)
                except BlockingIOError:
                    data = None
                except OSError:
                    data = b""
                    cleanup_failed = True
                if data is None:
                    continue
                if data == b"" or data == b"A":
                    result = 125
                    cleanup()
                    return result
                if data != b"F":
                    cleanup_failed = True
                    result = 125
                    cleanup()
                    return result
                finish_requested = True
    except BaseException:
        result = 125
    try:
        cleanup()
    except BaseException:
        cleanup_failed = True
    return 125 if cleanup_failed else result


def write_control(descriptor, message):
    if descriptor is None or time.monotonic() >= deadline:
        return False
    try:
        if time.monotonic() >= deadline:
            return False
        return os.write(descriptor, message) == len(message)
    except (BlockingIOError, OSError):
        return False


def wait_watchdog(pid, pidfd, status_read, stop_at):
    result = None
    wait_selector = selectors.DefaultSelector()
    status_registered = False
    try:
        if pidfd is not None:
            wait_selector.register(pidfd, selectors.EVENT_READ)
        if status_read is not None:
            wait_selector.register(status_read, selectors.EVENT_READ)
            status_registered = True
        while time.monotonic() < stop_at:
            if status_read is not None:
                if time.monotonic() >= stop_at:
                    break
                try:
                    data = os.read(status_read, 2)
                except BlockingIOError:
                    data = None
                except OSError:
                    data = b""
                if data == b"":
                    if status_registered:
                        wait_selector.unregister(status_read)
                        status_registered = False
                    status_read = None
                elif data is not None:
                    if len(data) == 1 and result is None:
                        result = data[0]
                    else:
                        result = 125
            if time.monotonic() >= stop_at:
                break
            try:
                waited_pid, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                return result if result is not None else 125
            if waited_pid == pid:
                return result if result is not None else 125
            wait_for = min(stop_at - time.monotonic(), 0.01)
            if wait_for <= 0:
                break
            if pidfd is None and status_read is None:
                time.sleep(wait_for)
            else:
                wait_selector.select(wait_for)
    except (OSError, TypeError, ValueError):
        return 125
    finally:
        wait_selector.close()
    return result if result is not None else 125


def pump_main():
    selector = None
    control_read = control_write = None
    status_read = status_write = None
    stdout_read = stdout_write = None
    stderr_read = stderr_write = None
    watchdog_pid = watchdog_pidfd = None
    streams = {}
    totals = {"stdout": 0, "stderr": 0}
    finish_sent = False
    watchdog_done = False
    cleanup_failed = False
    result = None
    primary_exception = None

    def close_fd(descriptor):
        nonlocal cleanup_failed
        if descriptor is not None and not close_owned(descriptor):
            cleanup_failed = True

    try:
        close_inherited_fds()
        flags = getattr(os, "O_CLOEXEC", 0)
        control_read, control_write = os.pipe2(flags)
        status_read, status_write = os.pipe2(flags)
        stdout_read, stdout_write = os.pipe2(flags)
        stderr_read, stderr_write = os.pipe2(flags)
        for descriptor in (
            control_read,
            control_write,
            status_read,
            status_write,
            stdout_read,
            stdout_write,
            stderr_read,
            stderr_write,
        ):
            os.set_inheritable(descriptor, False)
        parent_pid = os.getpid()
        watchdog_pid = os.fork()
        if watchdog_pid == 0:
            try:
                os.setsid()
            except OSError:
                os._exit(125)
            child_failed = False
            for descriptor in (
                control_write,
                status_read,
                stdout_read,
                stderr_read,
            ):
                if not close_owned(descriptor):
                    child_failed = True
            if child_failed:
                os._exit(125)
            try:
                code = watchdog_main(
                    control_read,
                    status_write,
                    stdout_write,
                    stderr_write,
                    parent_pid,
                )
            except BaseException:
                code = 125
            try:
                os.write(status_write, bytes((code,)))
            except OSError:
                pass
            close_owned(status_write)
            os._exit(code)
        close_fd(control_read)
        close_fd(status_write)
        close_fd(stdout_write)
        close_fd(stderr_write)
        control_read = status_write = stdout_write = stderr_write = None
        watchdog_pidfd = open_pidfd(watchdog_pid)
        selector = selectors.DefaultSelector()
        os.set_blocking(control_write, False)
        os.set_blocking(status_read, False)
        os.set_blocking(stdout_read, False)
        os.set_blocking(stderr_read, False)
        streams = {
            stdout_read: (max_stdout, "stdout"),
            stderr_read: (max_stderr, "stderr"),
        }
        sinks = {
            "stdout": sys.stdout.fileno(),
            "stderr": sys.stderr.fileno(),
        }
        for descriptor in sinks.values():
            os.set_blocking(descriptor, False)
        pending = {"stdout": bytearray(), "stderr": bytearray()}
        registered_sinks = set()

        def register_sink(label):
            descriptor = sinks[label]
            if pending[label] and descriptor not in registered_sinks:
                selector.register(
                    descriptor,
                    selectors.EVENT_WRITE,
                    ("sink", label),
                )
                registered_sinks.add(descriptor)

        def drain_sink(label):
            descriptor = sinks[label]
            if not pending[label]:
                return True
            if time.monotonic() >= deadline - cleanup_window:
                return False
            try:
                written = os.write(descriptor, pending[label])
            except BlockingIOError:
                return True
            except OSError:
                return False
            if written <= 0:
                return False
            del pending[label][:written]
            if not pending[label] and descriptor in registered_sinks:
                selector.unregister(descriptor)
                registered_sinks.remove(descriptor)
            return True

        for descriptor in streams:
            selector.register(
                descriptor,
                selectors.EVENT_READ,
                ("source", descriptor),
            )
        while streams or any(pending.values()):
            available = deadline - time.monotonic()
            if available <= cleanup_window:
                result = 124
                break
            events = selector.select(min(0.05, available - cleanup_window))
            for event, _mask in events:
                if deadline - time.monotonic() <= cleanup_window:
                    result = 124
                    break
                kind, value = event.data
                if kind == "sink":
                    if not drain_sink(value):
                        result = 125
                        break
                    continue
                descriptor = value
                if descriptor not in streams:
                    continue
                try:
                    chunk = os.read(descriptor, 64 * 1024)
                except BlockingIOError:
                    continue
                except OSError:
                    result = 125
                    break
                if not chunk:
                    selector.unregister(descriptor)
                    close_fd(descriptor)
                    if descriptor == stdout_read:
                        stdout_read = None
                    elif descriptor == stderr_read:
                        stderr_read = None
                    del streams[descriptor]
                    continue
                limit, label = streams[descriptor]
                remaining = limit - totals[label]
                if len(chunk) > remaining:
                    if remaining:
                        pending[label].extend(chunk[:remaining])
                        register_sink(label)
                    totals[label] += len(chunk)
                    result = 125
                    break
                pending[label].extend(chunk)
                totals[label] += len(chunk)
                register_sink(label)
            if result is not None:
                break
        if result is None:
            finish_sent = write_control(control_write, b"F")
            if not finish_sent:
                result = 125
            else:
                result = wait_watchdog(
                    watchdog_pid,
                    watchdog_pidfd,
                    status_read,
                    deadline - cleanup_window,
                )
    except BaseException as exc:
        primary_exception = exc
    finally:
        try:
            if watchdog_pid is not None and not watchdog_done:
                if not finish_sent:
                    write_control(control_write, b"A")
                close_fd(control_write)
                control_write = None
                watchdog_status = wait_watchdog(
                    watchdog_pid,
                    watchdog_pidfd,
                    status_read,
                    cleanup_deadline,
                )
                watchdog_done = True
                if result is None:
                    result = watchdog_status
        except BaseException:
            cleanup_failed = True
        if selector is not None:
            for descriptor in list(streams):
                try:
                    if descriptor in selector.get_map():
                        selector.unregister(descriptor)
                except (OSError, ValueError):
                    cleanup_failed = True
                close_fd(descriptor)
                if descriptor == stdout_read:
                    stdout_read = None
                elif descriptor == stderr_read:
                    stderr_read = None
            try:
                selector.close()
            except (OSError, ValueError):
                cleanup_failed = True
        for descriptor in (
            control_read,
            status_read,
            stdout_read,
            stderr_read,
            status_write,
            stdout_write,
            stderr_write,
            watchdog_pidfd,
        ):
            close_fd(descriptor)
    if primary_exception is not None:
        raise primary_exception
    if result is None or cleanup_failed:
        return 125
    return result


result = pump_main()
raise SystemExit(result)
PY
}

run_tar_bounded() {
  # Legacy static contract: timeout --signal=TERM --kill-after=10s "${DIST_VERIFY_TIMEOUT_SECONDS}s" tar "$@"
  run_duplex_command_bounded tar "${MAX_DIST_LISTING_BYTES}" "${MAX_TAR_STDERR_BYTES}" "$@"
}

run_find_bounded() {
  run_duplex_command_bounded find "${MAX_DIST_LISTING_BYTES}" "${MAX_TAR_STDERR_BYTES}" "$@"
}

run_python_bounded() {
  # Legacy static contract: timeout --signal=TERM --kill-after=10s "${DIST_VERIFY_TIMEOUT_SECONDS}s" python3 "$@"
  run_command_bounded "${VERIFY_PYTHON_INTERPRETER}" -I -S -B "$@"
}

run_safe_fs_bounded() {
  run_command_bounded "${safe_fs_cmd[@]}" "$@"
}

sweep_stale_verification_dirs() {
  local root=$1
  local candidates
  if ! candidates="$(run_python_bounded - "${root}" "${MAX_VERIFY_SWEEP_ENTRIES}" "${VERIFY_RESIDUE_MAX_AGE_SECONDS}" <<'PY'
import os
import re
import stat
import sys
import time

root, max_entries_value, max_age_value = sys.argv[1:]
max_entries = int(max_entries_value)
max_age = float(max_age_value)
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])
trusted_sticky_system_dirs = frozenset({"/tmp", "/var/tmp", "/dev/shm"})
name_pattern = re.compile(r"speed-of-cinnamon-dist-verify-[A-Za-z0-9]{6}\Z")
flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
root_fd = os.open(root, flags)
try:
    root_stat = os.fstat(root_fd)
    sticky_system_dir = (
        root_stat.st_uid == 0
        and root in trusted_sticky_system_dirs
        and root_stat.st_mode & stat.S_ISVTX
        and root_stat.st_mode & 0o002
    )
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or root_stat.st_uid not in {0, os.geteuid()}
        or root_stat.st_mode & 0o077 and not sticky_system_dir
    ):
        raise SystemExit("temporary root is not private or trusted")
    now = time.time()
    candidates = []
    with os.scandir(root_fd) as entries:
        for index, entry in enumerate(entries):
            if time.monotonic() >= deadline:
                raise SystemExit("verification deadline exceeded")
            if index >= max_entries:
                break
            if not name_pattern.fullmatch(entry.name):
                continue
            entry_stat = entry.stat(follow_symlinks=False)
            if (
                stat.S_ISLNK(entry_stat.st_mode)
                or not stat.S_ISDIR(entry_stat.st_mode)
                or entry_stat.st_uid != os.geteuid()
                or entry_stat.st_mode & 0o077
                or now - entry_stat.st_mtime < max_age
            ):
                continue
            path = os.path.join(root, entry.name)
            identity = f"{entry_stat.st_dev}:{entry_stat.st_ino}:{entry_stat.st_mode}"
            if len(path) > 4096:
                continue
            candidates.append((path, identity))
    output_size = 0
    for path, identity in candidates:
        line = f"{path}\t{identity}\n"
        output_size += len(line.encode())
        if output_size > 256 * 1024:
            raise SystemExit("temporary residue scan output budget exceeded")
        sys.stdout.write(line)
finally:
    os.close(root_fd)
PY
)"; then
    return 1
  fi
  while IFS=$'\t' read -r path identity; do
    [[ -z "${path}" ]] && continue
    if ! run_safe_fs_bounded remove verify-dist "${path}" --kind dir \
      --expected-identity "${identity}"; then
      printf 'failed to remove stale dist verification residue: %s\n' "${path}" >&2
      return 1
    fi
  done <<< "${candidates}"
}

contains_control_chars() {
  local value=$1
  run_python_bounded - "${value}" <<'PY'
import sys

value = sys.argv[1]
raise SystemExit(
    not (
        any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in value)
        or any(0xDC80 <= ord(char) <= 0xDCFF for char in value)
    )
)
PY
}

parent_lock_state() {
  local operation=$1
  if [[ "$operation" == "acquire" ]]; then
    exec "${VERIFY_PYTHON_INTERPRETER}" -I -S -B - "$operation" "${dist_dir}" "${repo_dir}/scripts/verify-dist.sh" "${verify_timeout_seconds}" "$tarball_input" \
      "" "" "" ""
  else
    "${VERIFY_PYTHON_INTERPRETER}" -I -S -B - "$operation" "${dist_dir}" "${repo_dir}/scripts/verify-dist.sh" "${verify_timeout_seconds}" "$tarball_input" \
      "${parent_lock_fd}" "${parent_lock_identity}" "${parent_lock_chain}" "${verify_deadline_monotonic}"
  fi <<'PY'
import math
import os
import stat
import sys
import time

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for dist verification", file=sys.stderr)
    raise SystemExit(1)

(
    operation,
    parent_path,
    script_path,
    timeout_value,
    archive_arg,
    parent_fd_value,
    parent_identity,
    parent_chain,
    deadline_value,
) = sys.argv[1:]
trusted_sticky_system_dirs = frozenset({"/tmp", "/var/tmp", "/dev/shm"})


def identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


def validate_directory(path_stat, path, *, final):
    if path_stat is None or stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeError(f"dist parent is not a directory: {path}")
    if path_stat.st_uid not in {0, os.geteuid()}:
        raise RuntimeError(f"dist parent owner is untrusted: {path}")
    sticky_system_dir = (
        path_stat.st_uid == 0
        and path in trusted_sticky_system_dirs
        and path_stat.st_mode & stat.S_ISVTX
        and path_stat.st_mode & 0o002
    )
    if path_stat.st_mode & 0o022 and not sticky_system_dir:
        raise RuntimeError(f"dist parent is writable: {path}")
    if final and path_stat.st_uid != os.geteuid():
        raise RuntimeError(f"dist parent must be owned by current user: {path}")


def open_parent_chain(path):
    if not os.path.isabs(path) or path == os.path.sep:
        raise RuntimeError(f"dist parent path is invalid: {path}")
    components = path.split(os.path.sep)[1:]
    if any(not component or component in {".", ".."} for component in components):
        raise RuntimeError(f"dist parent path is invalid: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptors = []
    identities = []
    try:
        current_path = os.path.sep
        current_fd = os.open(current_path, flags)
        descriptors.append(current_fd)
        root_stat = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != 0
            or root_stat.st_mode & 0o022
        ):
            raise RuntimeError("dist parent root ancestor is untrusted")
        identities.append(identity(root_stat))
        for index, component in enumerate(components):
            if time.monotonic() >= deadline:
                raise TimeoutError("verification deadline exceeded")
            next_fd = os.open(component, flags, dir_fd=current_fd)
            current_path = os.path.join(current_path, component)
            descriptors.append(next_fd)
            current_fd = next_fd
            path_stat = os.fstat(current_fd)
            validate_directory(path_stat, current_path, final=index == len(components) - 1)
            identities.append(identity(path_stat))
        return descriptors, identities
    except BaseException:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def revalidate_parent(parent_fd, expected_identities):
    fresh_descriptors, fresh_identities = open_parent_chain(parent_path)
    try:
        if fresh_identities != expected_identities:
            raise RuntimeError(f"dist parent path changed: {parent_path}")
        parent_stat = os.fstat(parent_fd)
        validate_directory(parent_stat, parent_path, final=True)
        if identity(parent_stat) != expected_identities[-1]:
            raise RuntimeError(f"dist parent descriptor changed: {parent_path}")
    finally:
        for descriptor in reversed(fresh_descriptors):
            os.close(descriptor)


parent_descriptors = []
try:
    if operation == "acquire":
        try:
            timeout_seconds = int(timeout_value)
        except ValueError:
            raise RuntimeError("verification timeout is invalid") from None
        if timeout_seconds <= 0:
            raise RuntimeError("verification timeout is invalid")
        deadline = time.monotonic() + timeout_seconds
        parent_descriptors, parent_identities = open_parent_chain(parent_path)
        parent_fd = parent_descriptors[-1]
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("verify-dist shared lock timed out")
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("verify-dist shared lock timed out")
                time.sleep(min(0.05, remaining))
        revalidate_parent(parent_fd, parent_identities)
        os.fchdir(parent_fd)
        os.set_inheritable(parent_fd, True)
        for descriptor in reversed(parent_descriptors[:-1]):
            os.close(descriptor)
        parent_descriptors = [parent_fd]
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("PYTHON")
            and not key.startswith("VERIFY_DIST_PARENT_")
            and key != "VERIFY_DIST_DEADLINE_MONOTONIC"
        }
        environment["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        os.execve(
            script_path,
            [
                script_path,
                archive_arg,
                "__verify-dist-locked-v1",
                str(parent_fd),
                parent_identities[-1],
                ";".join(parent_identities),
                f"{deadline:.17g}",
            ],
            environment,
        )
    elif operation == "revalidate":
        try:
            parent_fd = int(parent_fd_value)
            deadline = float(deadline_value)
        except ValueError:
            raise RuntimeError("verify-dist parent lock state is invalid") from None
        if parent_fd < 3 or not math.isfinite(deadline):
            raise RuntimeError("verify-dist parent lock state is invalid")
        now = time.monotonic()
        if deadline <= now or deadline > now + 120:
            raise RuntimeError("verify-dist parent deadline is outside allowed bounds")
        expected_identities = parent_chain.split(";")
        if not expected_identities or expected_identities[-1] != parent_identity:
            raise RuntimeError("verify-dist parent lock identity is invalid")
        for value in expected_identities:
            parts = value.split(":")
            if len(parts) != 3 or any(not part.isdigit() for part in parts):
                raise RuntimeError("verify-dist parent lock identity is invalid")
        try:
            fcntl.flock(parent_fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("verify-dist shared lock handoff is not held") from exc
        if time.monotonic() >= deadline:
            raise TimeoutError("verification deadline exceeded")
        revalidate_parent(parent_fd, expected_identities)
        os.fchdir(parent_fd)
    else:
        raise RuntimeError("verify-dist parent lock operation is invalid")
except TimeoutError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(124)
except BaseException as exc:
    if operation == "revalidate":
        print(f"verify-dist parent path/FD revalidation failed: {exc}", file=sys.stderr)
    else:
        print(f"verify-dist parent lock setup failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    for descriptor in parent_descriptors:
        try:
            os.close(descriptor)
        except OSError:
            pass
PY
}

if (( lock_handoff_mode == 0 )); then
  parent_lock_state acquire
elif ! parent_lock_state revalidate; then
  exit 1
fi
export VERIFY_DIST_DEADLINE_MONOTONIC="${verify_deadline_monotonic}"

for tool in realpath stat tar awk mktemp find grep python3 sha256sum timeout; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done
if [[ ! -x "${VERIFY_PYTHON_INTERPRETER}" ]]; then
  printf 'absolute Python interpreter is invalid: %s\n' "${VERIFY_PYTHON_INTERPRETER}" >&2
  exit 1
fi

if [[ -L "${safe_fs}" || ! -f "${safe_fs}" || "$(run_command_bounded stat -c '%F' "${safe_fs}")" != "regular file" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
  exit 1
fi
if [[ "$(run_command_bounded stat -c '%h' "${safe_fs}")" -ne 1 ]]; then
  printf 'safe local filesystem helper must not be hardlinked: %s\n' "${safe_fs}" >&2
  exit 1
fi

if contains_control_chars "${tarball_input}"; then
  printf 'archive path contains control characters\n' >&2
  exit 1
fi
tarball_name="${tarball_input##*/}"
if [[ -z "${tarball_name}" || "${tarball_name}" == "." || "${tarball_name}" == ".." || ! "${tarball_name}" == *.tar.gz ]]; then
  printf 'archive name is invalid: %s\n' "${tarball_input}" >&2
  exit 1
fi


if [[ "$tarball_input" == /* ]]; then
  requested_tarball_input="$tarball_input"
else
  requested_tarball_input="$repo_dir/$tarball_input"
fi
if ! requested_tarball="$(run_command_bounded realpath -m -- "$requested_tarball_input")"; then
  printf 'failed to resolve archive path\n' >&2
  exit 1
fi
expected_tarball="$dist_dir/$tarball_name"
if [[ "$requested_tarball" != "$expected_tarball" ]]; then
  printf 'archive must be a direct dist artifact: %s\n' "$tarball_input" >&2
  exit 1
fi

if [[ -L "$dist_dir" || ! -d "$dist_dir" ]]; then
  printf 'dist directory is invalid: %s\n' "$dist_dir" >&2
  exit 1
fi

tarball="./$tarball_name"
expected_tarball="$dist_dir/$tarball_name"
if [[ -L "$expected_tarball" || ! -f "$expected_tarball" ]]; then
  printf 'archive missing or invalid: %s\n' "$expected_tarball" >&2
  exit 1
fi
if [[ "$(run_command_bounded stat -c '%F' "$expected_tarball")" != "regular file" ]]; then
  printf 'archive must be a regular file: %s\n' "$expected_tarball" >&2
  exit 1
fi
if [[ "$(run_command_bounded stat -c '%h' "$expected_tarball")" -ne 1 ]]; then
  printf 'archive must not be hardlinked: %s\n' "$expected_tarball" >&2
  exit 1
fi
tarball_bytes="$(run_command_bounded stat -c '%s' "$expected_tarball")"
if [[ "$tarball_bytes" -le 0 || "$tarball_bytes" -gt "$MAX_DIST_ARCHIVE_BYTES" ]]; then
  printf 'archive size is outside allowed bounds: %s bytes\n' "$tarball_bytes" >&2
  exit 1
fi

checksum_path="$tarball.sha256"
if [[ -L "$checksum_path" || ! -f "$checksum_path" ]]; then
  printf 'archive checksum file missing or invalid: %s\n' "$checksum_path" >&2
  exit 1
fi
if [[ "$(run_command_bounded stat -c '%h' "$checksum_path")" -ne 1 ]]; then
  printf 'archive checksum file must not be hardlinked: %s\n' "$checksum_path" >&2
  exit 1
fi

read_checksum_bounded() {
  local checksum_name=$1
  local expected_name=$2
  run_python_bounded - "$checksum_name" "$expected_name" <<'PY'
import os
import re
import stat
import sys
import time

checksum_path, expected_name = sys.argv[1:]
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])
flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
file_descriptor = os.open(checksum_path, flags)
try:
    before = os.fstat(file_descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
        or before.st_size < 0
        or before.st_size > 4096
    ):
        raise SystemExit("archive checksum file is invalid")
    if time.monotonic() >= deadline:
        raise SystemExit("verification deadline exceeded")
    handle = os.fdopen(file_descriptor, "rb", closefd=False)
    try:
        raw = handle.read(4097)
    finally:
        handle.close()
    after = os.fstat(file_descriptor)
    signature_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
        before.st_nlink,
    )
    signature_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
        after.st_nlink,
    )
    if signature_before != signature_after:
        raise SystemExit("archive checksum file changed during read")
    if time.monotonic() >= deadline:
        raise SystemExit("verification deadline exceeded")
finally:
    os.close(file_descriptor)
if len(raw) > 4096:
    raise SystemExit("archive checksum file is too large")
try:
    text = raw.decode("ascii")
except UnicodeDecodeError:
    raise SystemExit("archive checksum file is not ASCII") from None
lines = text.splitlines()
if len(lines) != 1:
    raise SystemExit("archive checksum file must contain exactly one entry")
parts = lines[0].split()
if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
    raise SystemExit("archive checksum file has invalid format")
if parts[1].lstrip("*") != expected_name:
    raise SystemExit("archive checksum file target does not match archive")
print(parts[0].lower())
PY
}

checksum_value="$(read_checksum_bounded "$checksum_path" "$tarball_name")" || {
  printf 'archive checksum file has invalid format: %s\n' "$checksum_path" >&2
  exit 1
}

tmp_root="${TMPDIR:-/tmp}"
if contains_control_chars "${tmp_root}"; then
  printf 'temporary root contains control characters\n' >&2
  exit 1
fi
if [[ ! "${tmp_root}" == /* ]]; then
  printf 'temporary root must be an absolute path\n' >&2
  exit 1
fi
if [[ -L "${tmp_root}" ]]; then
  printf 'temporary root must not be a symlink\n' >&2
  exit 1
fi
if [[ ! -d "${tmp_root}" || ! -w "${tmp_root}" ]]; then
  printf 'temporary root is not a writable directory\n' >&2
  exit 1
fi
if [[ -L "${tmp_root}" ]]; then
  printf 'temporary root must not be a symlink\n' >&2
  exit 1
fi
if ! tmp_root="$(run_command_bounded realpath "${tmp_root}")"; then
  printf 'failed to resolve temporary root\n' >&2
  exit 1
fi
if ! sweep_stale_verification_dirs "${tmp_root}"; then
  printf 'failed to sweep stale dist verification residues\n' >&2
  exit 1
fi
if ! tmp_dir="$(run_command_bounded mktemp -d "${tmp_root}/speed-of-cinnamon-dist-verify-XXXXXX")"; then
  printf 'failed to create temporary dist verification directory\n' >&2
  exit 1
fi
if [[ -L "${tmp_dir}" ]]; then
  printf 'temporary dist verification directory must not be a symlink: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if ! tmp_dir_abs="$(run_command_bounded realpath "${tmp_dir}")"; then
  printf 'failed to resolve temporary dist verification directory: %s\n' "${tmp_dir}" >&2
  exit 1
fi
if [[ "${tmp_dir_abs}" != "${tmp_root}/speed-of-cinnamon-dist-verify-"* ]]; then
  printf 'temporary dist verification directory escaped temporary root: %s\n' "${tmp_dir}" >&2
  exit 1
fi
tmp_dir="${tmp_dir_abs}"
tmp_dir_identity=""
cleanup_tmpdir() {
 # Existing static contract: tmp_dir_identity="$("${safe_fs_cmd[@]}" identity verify-dist "${tmp_dir}" --kind dir)"
  local primary_status=$?
  local cleanup_status=0
  if [[ -n "${tmp_dir_identity}" ]]; then
    if run_safe_fs_bounded remove verify-dist "${tmp_dir}" --kind dir \
      --expected-identity "${tmp_dir_identity}"; then
      :
    else
      cleanup_status=$?
      printf 'dist verification cleanup failed for identity-bound path: %s\n' "${tmp_dir}" >&2
    fi
  else
    printf 'refusing dist verification cleanup without verified identity: %s\n' "${tmp_dir}" >&2
    cleanup_status=1
  fi
  if (( primary_status != 0 )); then
    if (( cleanup_status != 0 )); then
      printf 'dist verification cleanup also failed; primary status preserved: %d\n' "${primary_status}" >&2
    fi
    exit "${primary_status}"
  fi
  if (( cleanup_status != 0 )); then
    exit 1
  fi
  exit 0
}
trap cleanup_tmpdir EXIT

if ! tmp_dir_identity="$(run_safe_fs_bounded identity verify-dist "${tmp_dir}" --kind dir)"; then
  printf 'failed to capture dist verification directory identity: %s\n' "${tmp_dir}" >&2
  exit 1
fi

tarball_snapshot="${tmp_dir}/speed-of-cinnamon-verify.tar.gz"
: 'copy-file verify-dist "${tarball}" "${tarball_snapshot}" 0644 \
  --max-bytes "${MAX_DIST_ARCHIVE_BYTES}"'

snapshot_archive_bounded() {
  local source_name=$1
  local destination_path=$2
  local max_bytes=$3
  local destination_identity=$4
  run_python_bounded - "${source_name}" "${destination_path}" "${max_bytes}" "${destination_identity}" <<'PY'
import os
import stat
import sys
import time

source_name, destination_path, max_bytes_value, destination_identity = sys.argv[1:]
max_bytes = int(max_bytes_value)
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])
open_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
source_fd = None
parent_fd = None
destination_fd = None


def check_deadline():
    if time.monotonic() >= deadline:
        raise SystemExit("verification deadline exceeded")


def identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


def signature(path_stat):
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
        path_stat.st_nlink,
    )


try:
    check_deadline()
    source_fd = os.open(source_name, open_flags)
    source_stat = os.fstat(source_fd)
    if (
        not stat.S_ISREG(source_stat.st_mode)
        or source_stat.st_nlink != 1
        or source_stat.st_size <= 0
        or source_stat.st_size > max_bytes
    ):
        raise SystemExit("archive source is outside allowed bounds")
    source_signature = signature(source_stat)

    destination_parent = os.path.dirname(destination_path)
    destination_name = os.path.basename(destination_path)
    if not destination_parent or not destination_name or destination_name in {".", ".."}:
        raise SystemExit("archive snapshot destination is invalid")
    parent_fd = os.open(destination_parent, open_flags)
    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o077
        or identity(parent_stat) != destination_identity
    ):
        raise SystemExit("archive snapshot destination identity is invalid")
    destination_fd = os.open(
        destination_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=parent_fd,
    )
    total = 0
    while True:
        check_deadline()
        remaining = max_bytes + 1 - total
        if remaining <= 0:
            raise SystemExit("archive source exceeds allowed bounds")
        chunk = os.read(source_fd, min(1024 * 1024, remaining))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise SystemExit("archive source exceeds allowed bounds")
        view = memoryview(chunk)
        while view:
            check_deadline()
            written = os.write(destination_fd, view)
            if written <= 0:
                raise SystemExit("archive snapshot write failed")
            view = view[written:]
    check_deadline()
    if signature(os.fstat(source_fd)) != source_signature:
        raise SystemExit("archive source changed during snapshot")
    os.fsync(destination_fd)
    destination_stat = os.fstat(destination_fd)
    if (
        not stat.S_ISREG(destination_stat.st_mode)
        or destination_stat.st_nlink != 1
        or destination_stat.st_size != total
    ):
        raise SystemExit("archive snapshot is invalid")
    os.close(destination_fd)
    destination_fd = None
    os.fsync(parent_fd)
    check_deadline()
finally:
    for descriptor in (destination_fd, parent_fd, source_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
PY
}

if ! snapshot_archive_bounded "${tarball}" "${tarball_snapshot}" "${MAX_DIST_ARCHIVE_BYTES}" "${tmp_dir_identity}"; then
  printf 'failed to snapshot archive for verification: %s\n' "${tarball}" >&2
  exit 1
fi
if [[ -L "${tarball_snapshot}" || ! -f "${tarball_snapshot}" || "$(run_command_bounded stat -c '%F' "${tarball_snapshot}")" != "regular file" ]]; then
  printf 'archive snapshot must be a regular file: %s\n' "${tarball_snapshot}" >&2
  exit 1
fi
if [[ "$(run_command_bounded stat -c '%h' "${tarball_snapshot}")" -ne 1 ]]; then
  printf 'archive snapshot must not be hardlinked: %s\n' "${tarball_snapshot}" >&2
  exit 1
fi
snapshot_bytes="$(run_command_bounded stat -c '%s' "${tarball_snapshot}")"
if [[ "${snapshot_bytes}" -le 0 || "${snapshot_bytes}" -gt "${MAX_DIST_ARCHIVE_BYTES}" ]]; then
  printf 'archive snapshot size is outside allowed bounds: %s bytes\n' "${snapshot_bytes}" >&2
  exit 1
fi
tarball_bytes="${snapshot_bytes}"

# Existing static contract: sha256sum "${tarball}"
if ! actual_checksum="$(run_command_bounded sha256sum "${tarball_snapshot}" | run_command_bounded awk '{print tolower($1)}')"; then
  printf 'failed to hash archive snapshot within the verification budget: %s\n' "${tarball}" >&2
  exit 1
fi
if [[ "${actual_checksum}" != "${checksum_value}" ]]; then
  printf 'archive checksum mismatch: %s\n' "${tarball}" >&2
  exit 1
fi

release_parent_lock() {
  if [[ ! "${parent_lock_fd}" =~ ^[3-9][0-9]*$ ]]; then
    printf 'dist parent lock descriptor is invalid during release\n' >&2
    return 1
  fi
  if ! eval "exec ${parent_lock_fd}<&-"; then
    printf 'failed to release dist parent lock\n' >&2
    return 1
  fi
  parent_lock_fd=""
}

if ! release_parent_lock; then
  exit 1
fi

capture_bounded_stream() {
  local destination=$1
  local max_bytes=$2
  local destination_identity=$3
  run_python_bounded -c '
import os
import stat
import sys
import time

destination_path, max_bytes_value, destination_identity = sys.argv[1:]
max_bytes = int(max_bytes_value)
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])
open_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
parent_fd = None
destination_fd = None


def check_deadline():
    if time.monotonic() >= deadline:
        raise SystemExit("verification deadline exceeded")


def identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


try:
    check_deadline()
    parent_fd = os.open(os.path.dirname(destination_path), open_flags)
    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o077
        or identity(parent_stat) != destination_identity
    ):
        raise SystemExit("bounded output destination identity is invalid")
    destination_name = os.path.basename(destination_path)
    if not destination_name or destination_name in {".", ".."}:
        raise SystemExit("bounded output destination is invalid")
    destination_fd = os.open(
        destination_name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=parent_fd,
    )
    total = 0
    with os.fdopen(destination_fd, "wb") as output:
        destination_fd = None
        while total <= max_bytes:
            check_deadline()
            chunk = sys.stdin.buffer.read(min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise SystemExit("bounded command output exceeds allowed bounds")
            output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    os.fsync(parent_fd)
    check_deadline()
finally:
    for descriptor in (destination_fd, parent_fd):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
' "$destination" "$max_bytes" "$destination_identity"
}
tar_listing="${tmp_dir}/tar-listing.txt"
if ! run_tar_bounded -tzf "${tarball_snapshot}" |
  capture_bounded_stream "${tar_listing}" "${MAX_DIST_LISTING_BYTES}" "${tmp_dir_identity}"; then
  printf 'failed to list archive within the verification budget: %s\n' "${tarball}" >&2
  exit 1
fi
# Existing static contract: tar_listing_bytes="$(stat -c '%s' "${tar_listing}")"
tar_listing_bytes="$(run_command_bounded stat -c '%s' "${tar_listing}")"
if [[ "${tar_listing_bytes}" -gt "${MAX_DIST_LISTING_BYTES}" ]]; then
  printf 'archive listing exceeds allowed bounds: %s bytes\n' "${tar_listing_bytes}" >&2
  exit 1
fi
if ! run_command_bounded awk -F'/' '
  /(^|\/)\.\.(\/|$)/ || /^\// { print; bad = 1 }
  END { exit bad ? 1 : 0 }
' "${tar_listing}" > /dev/null; then
  printf 'archive contains unsafe path entries (path traversal or absolute path): %s\n' "${tarball}" >&2
  exit 1
fi

run_python_bounded - "$tarball_snapshot" "$tmp_dir" "${MAX_DIST_MEMBERS}" "${MAX_DIST_PATH_CHARS}" "${MAX_DIST_PATH_DEPTH}" "${MAX_DIST_FILE_BYTES}" "${MAX_DIST_TOTAL_EXTRACTED_BYTES}" <<'PY'
import os
import pathlib
import stat
import tarfile
import sys
import time

tarball_snapshot = sys.argv[1]
target = pathlib.Path(sys.argv[2])
MAX_DIST_MEMBERS = int(sys.argv[3])
MAX_DIST_PATH_CHARS = int(sys.argv[4])
MAX_DIST_PATH_DEPTH = int(sys.argv[5])
MAX_DIST_FILE_BYTES = int(sys.argv[6])
MAX_DIST_TOTAL_EXTRACTED_BYTES = int(sys.argv[7])
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])


def check_deadline():
    if time.monotonic() >= deadline:
        raise SystemExit("verification deadline exceeded")


target.mkdir(parents=True, exist_ok=True)
target_root = target.resolve(strict=True)


def member_target(member_name):
    path = target / member_name
    if not path.resolve(strict=False).is_relative_to(target_root):
        raise SystemExit(f"dist archive path escapes target: {member_name}")
    return path


def validate_member_mode(member):
    if member.mode is None:
        raise SystemExit(f"dist archive member has no mode: {member.name}")
    permissions = stat.S_IMODE(member.mode)
    if permissions & 0o7000:
        raise SystemExit(f"dist archive member has disallowed setuid/setgid/sticky bits: {member.name}")
    if permissions & 0o022:
        raise SystemExit(f"dist archive member is group/world writable: {member.name}")
    if member.isdir():
        if permissions & 0o777 > 0o755:
            raise SystemExit(f"dist archive directory has disallowed permissions: {member.name}")
        return
    file_permissions = permissions & 0o777
    if file_permissions & 0o111:
        if file_permissions != 0o755:
            raise SystemExit(f"dist archive executable file has disallowed permissions: {member.name}")
    elif file_permissions > 0o644:
        raise SystemExit(f"dist archive non-executable file has disallowed permissions: {member.name}")


with tarfile.open(tarball_snapshot, "r:gz") as archive:
    package_root = None
    member_count = 0
    total_file_size = 0
    for member in archive:
        check_deadline()
        member_count += 1
        if member_count > MAX_DIST_MEMBERS:
            raise SystemExit("dist archive contains too many entries")
        if (
            "\x00" in member.name
            or any(ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F for char in member.name)
            or any(0xDC80 <= ord(char) <= 0xDCFF for char in member.name)
        ):
            raise SystemExit(f"dist archive contains unsafe path entry: {member.name!r}")
        if len(member.name) > MAX_DIST_PATH_CHARS:
            raise SystemExit(f"dist archive path is too long: {member.name}")
        if len([part for part in member.name.split("/") if part]) > MAX_DIST_PATH_DEPTH:
            raise SystemExit(f"dist archive path is too deep: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise SystemExit(f"dist archive contains unsupported entry type: {member.name}")
        validate_member_mode(member)
        if member.name.startswith("/"):
            raise SystemExit(f"dist archive path is absolute: {member.name}")
        if ".." in member.name.split("/"):
            raise SystemExit(f"dist archive path escapes target: {member.name}")
        if member.issym() or member.islnk():
            raise SystemExit(f"dist archive contains unsupported link entry: {member.name}")
        root = member.name.split("/", 1)[0]
        if not root:
            raise SystemExit(f"dist archive contains an empty path entry: {member.name}")
        if package_root is None:
            package_root = root
        elif root != package_root:
            raise SystemExit(f"dist archive contains multiple top-level entries: {member.name}")
        output_path = member_target(member.name)
        if member.isdir():
            output_path.mkdir(mode=0o700, parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise SystemExit(f"dist archive contains unsupported entry type: {member.name}")
        if member.size < 0:
            raise SystemExit(f"dist archive file has invalid size: {member.name}")
        if member.size > MAX_DIST_FILE_BYTES:
            raise SystemExit(f"dist archive file is too large: {member.name}")
        total_file_size += member.size
        if total_file_size > MAX_DIST_TOTAL_EXTRACTED_BYTES:
            raise SystemExit("dist archive extracted size budget exceeded")
        output_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"dist archive file could not be read: {member.name}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(output_path, flags, 0o600)
        except FileExistsError:
            raise SystemExit(f"dist archive contains duplicate file entry: {member.name}") from None
        with source, os.fdopen(fd, "wb") as output:
            while True:
                check_deadline()
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
        check_deadline()
PY

package_dirs_file="${tmp_dir}/package-dirs.bin"
# Existing static contract: find "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d -print0
if ! run_find_bounded "${tmp_dir}" -mindepth 1 -maxdepth 1 -type d -print0 |
  capture_bounded_stream "${package_dirs_file}" "${MAX_DIST_LISTING_BYTES}" "${tmp_dir_identity}"; then
  printf 'failed to locate archive package directory within the verification budget.\n' >&2
  exit 1
fi
package_dirs=()
while IFS= read -r -d '' path; do
  package_dirs+=("${path}")
done < "${package_dirs_file}"

if [[ ${#package_dirs[@]} -ne 1 ]]; then
  printf 'archive should contain exactly one top-level directory, found %d\n' "${#package_dirs[@]}" >&2
  exit 1
fi

package_dir="${package_dirs[0]}"
if [[ -z "${package_dir}" || ! -d "${package_dir}" ]]; then
  printf 'archive did not contain a package directory: %s\n' "${tarball}" >&2
  exit 1
fi

if ! unsupported_links="$(run_find_bounded "${package_dir}" -type l -print -quit)"; then
  printf 'failed to inspect archive expansion for symlinks.\n' >&2
  exit 1
fi
if [[ -n "${unsupported_links}" ]]; then
  printf 'archive expansion contains unsupported symlink entries.\n' >&2
  exit 1
fi

run_python_bounded - "${package_dir}" <<'PY'
import os
from pathlib import Path
import stat
import sys
import time

MAX_PACKAGE_ENTRIES = 100_000
deadline = float(os.environ["VERIFY_DIST_DEADLINE_MONOTONIC"])


def check_deadline():
    if time.monotonic() >= deadline:
        raise SystemExit("verification deadline exceeded")


def validate_mode(path: Path) -> None:
    mode = path.lstat().st_mode
    permissions = stat.S_IMODE(mode)
    if stat.S_ISLNK(mode):
        raise SystemExit(f"archive expansion contains unsupported symlink entry: {path}")
    if stat.S_ISDIR(mode):
        if permissions & 0o7000:
            raise SystemExit(f"archive directory has disallowed setuid/setgid/sticky bits: {path}")
        if permissions & 0o022:
            raise SystemExit(f"archive directory is group/world writable: {path}")
        if permissions & 0o777 > 0o755:
            raise SystemExit(f"archive directory has disallowed permissions: {path} ({oct(permissions & 0o777)})")
        return
    if not stat.S_ISREG(mode):
        raise SystemExit(f"archive contains unsupported entry type: {path}")

    if permissions & 0o7000:
        raise SystemExit(f"archive file has disallowed setuid/setgid/sticky bits: {path}")
    if permissions & 0o022:
        raise SystemExit(f"archive file is group/world writable: {path}")

    file_permissions = permissions & 0o777
    if file_permissions & 0o111:
        if file_permissions != 0o755:
            raise SystemExit(
                "archive executable file has disallowed permissions "
                f"({oct(file_permissions)}): {path}"
            )
    elif file_permissions > 0o644:
        raise SystemExit(
            f"archive non-executable file has disallowed permissions ({oct(file_permissions)}): {path}"
        )


package_root = Path(sys.argv[1])
validate_mode(package_root)
pending = [package_root]
scanned_entries = 0
while pending:
  check_deadline()
  current = pending.pop()
  try:
    with os.scandir(current) as entries:
      for entry in entries:
        check_deadline()
        if scanned_entries >= MAX_PACKAGE_ENTRIES:
          raise SystemExit("archive expansion entry budget exceeded")
        scanned_entries += 1
        child = Path(entry.path)
        validate_mode(child)
        if entry.is_dir(follow_symlinks=False):
          pending.append(child)
  except SystemExit:
    raise
  except OSError as exc:
    raise SystemExit(f"archive expansion could not be scanned: {current}") from exc
PY

if run_command_bounded grep -Fq 'command -v -- python3' "${package_dir}/scripts/safe-local-fs.py"; then
  printf 'archive backend wrapper helper must not resolve python3 through PATH at runtime.\n' >&2
  exit 1
fi
if ! run_command_bounded grep -Fq 'python_executable = _validate_absolute(args.python_executable, "python executable path")' "${package_dir}/scripts/safe-local-fs.py" \
  || ! run_command_bounded grep -Fq 'write_wrapper.add_argument("python_executable")' "${package_dir}/scripts/safe-local-fs.py" \
  || ! run_command_bounded grep -Fq ' -m speed_of_cinnamon.cli \"$@\"' "${package_dir}/scripts/safe-local-fs.py"; then
  printf 'archive backend wrapper helper does not invoke the expected CLI module.\n' >&2
  exit 1
fi

for path in \
  README.md \
  LICENSE \
  RELEASE-MANIFEST.txt \
  Makefile \
  pyproject.toml \
  packaging/speed-of-cinnamon.spec \
  docs/architecture.md \
  docs/cli-reference.md \
  docs/development.md \
  docs/fedora-cinnamon-runbook.md \
  docs/man/speed-of-cinnamon.1 \
  docs/man/speed-of-cinnamon-alarms.1 \
  docs/user-guide.md \
  docs/wiki/Home.md \
  files/speed-of-cinnamon@H234598/applet.js \
  files/speed-of-cinnamon@H234598/metadata.json \
  files/speed-of-cinnamon@H234598/settings-schema.json \
  scripts/install-local.sh \
  scripts/local-model-e2e-acceptance.sh \
  scripts/export-release-attestations.sh \
  scripts/real-e2e-acceptance.sh \
  scripts/verify-release-attestation.py \
  scripts/safe-local-fs.py \
  scripts/verify-local-model-e2e-attestation.sh \
  scripts/verify-real-e2e-attestation.sh \
  scripts/publish-github-release.sh \
  scripts/verify-authorship.sh \
  scripts/verify-rpm.sh \
  src/speed_of_cinnamon/alarms.py \
  src/speed_of_cinnamon/cli.py \
  src/speed_of_cinnamon/setup_plan.py \
  tests/test_alarms.py \
  tests/test_ci_static.py \
  tests/test_cli.py
do
  if [[ ! -e "${package_dir}/${path}" ]]; then
    printf 'archive is missing %s\n' "${path}" >&2
    exit 1
  fi
done

if ! run_python_bounded -m compileall -q "${package_dir}"; then
  printf 'archive Python package compilation failed or exceeded the verification budget\n' >&2
  exit 1
fi

printf 'Verified %s\n' "${tarball}"
