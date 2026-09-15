#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

readonly DIST_BUILD_TIMEOUT_SECONDS=3600
readonly DIST_BUILD_KILL_AFTER_SECONDS=2
readonly DIST_BUILD_CLOCK_TIMEOUT_SECONDS=0.25
readonly DIST_BUILD_LAUNCH_MARGIN_NS=50000000
readonly DIST_BUILD_INITIAL_PROBE_TIMEOUT_SECONDS=0.25
readonly DIST_BUILD_STARTUP_RESERVE_NS=3000000000
readonly DIST_BUILD_HANDOFF_TIMEOUT_SECONDS=1
readonly DIST_BUILD_HANDOFF_FD=198
readonly DIST_FINALIZE_TIMEOUT_SECONDS=120
readonly DIST_FINALIZE_LOCK_TIMEOUT_SECONDS=30
readonly DIST_FINALIZE_KILL_AFTER_SECONDS=2
readonly DIST_CLEANUP_TIMEOUT_SECONDS=30
readonly DIST_CLEANUP_KILL_AFTER_SECONDS=1
readonly DIST_STARTUP_SWEEP_TIMEOUT_SECONDS=30
readonly DIST_STARTUP_SWEEP_KILL_AFTER_SECONDS=1
readonly DIST_STARTUP_SWEEP_MAX_ENTRIES=256
readonly DIST_STARTUP_SWEEP_MAX_STALE=32
readonly DIST_WORKSPACE_MAX_AGE_SECONDS=$((24 * 60 * 60))
readonly DIST_MAX_ARCHIVE_BYTES=$((128 * 1024 * 1024))

if (( DIST_BUILD_TIMEOUT_SECONDS <= DIST_BUILD_KILL_AFTER_SECONDS )); then
  printf 'build-dist timeout is too short for TERM/KILL grace period\n' >&2
  exit 1
fi

dist_process_group_info() {
  local pid=$1
  local record
  local -a fields=()
  if [[ ! "${pid}" =~ ^[0-9]+$ ]] ||
      ! IFS= read -r record < "/proc/${pid}/stat"; then
    return 1
  fi
  record="${record##*) }"
  IFS=' ' read -r -a fields <<< "${record}"
  if (( ${#fields[@]} < 20 )); then
    return 1
  fi
  printf '%s %s %s %s %s' \
    "${fields[0]}" "${fields[1]}" "${fields[2]}" "${fields[3]}" "${fields[19]}"
}
process_starttime() {
  local pid=$1
  local allow_stopped=${2:-}
  local info
  local state
  local parent_pid
  local process_group
  local session
  local starttime
  if ! info="$(dist_process_group_info "${pid}")"; then
    return 1
  fi
  IFS=' ' read -r state parent_pid process_group session starttime <<< "${info}"
  if [[ "${state}" == Z || "${state}" == X ||
      ! "${starttime}" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if [[ ( "${state}" == T || "${state}" == t ) &&
      "${allow_stopped}" != allow-stopped ]]; then
    return 1
  fi
  printf '%s' "${starttime}"
}
dist_group_identity_matches() {
  local pid=$1
  local expected_parent_pid=$2
  local expected_starttime=$3
  local info
  local state
  local parent_pid
  local process_group
  local session
  local starttime
  if ! info="$(dist_process_group_info "${pid}")"; then
    return 1
  fi
  IFS=' ' read -r state parent_pid process_group session starttime <<< "${info}"
  [[ "${state}" != Z && "${state}" != X &&
      "${state}" != T && "${state}" != t &&
      "${parent_pid}" == "${expected_parent_pid}" &&
      "${process_group}" == "${pid}" &&
      "${session}" == "${pid}" &&
      "${starttime}" == "${expected_starttime}" ]]
}
dist_now_monotonic_ns() {
  local now_ns
  if ! now_ns="$(timeout --signal=KILL "${DIST_BUILD_CLOCK_TIMEOUT_SECONDS}s" \
      python3 -I -B -c 'import time; print(time.monotonic_ns())' 2>/dev/null)"; then
    return 1
  fi
  [[ "${now_ns}" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "${now_ns}"
}
dist_wait_until_monotonic_deadline() {
  local deadline_ns=$1
  local now_ns
  local remaining_ns
  local wait_seconds
  if ! now_ns="$(dist_now_monotonic_ns)"; then
    return 1
  fi
  remaining_ns=$((deadline_ns - now_ns))
  if (( remaining_ns <= 0 )); then
    return 0
  fi
  wait_seconds="$((remaining_ns / 1000000000)).$((remaining_ns % 1000000000))"
  timeout --signal=KILL --kill-after=0.1s \
    "${wait_seconds}s" sleep "${wait_seconds}" >/dev/null 2>&1 || true
}
dist_core_group_info="$(dist_process_group_info "$$" 2>/dev/null || true)"
dist_core_group_pid=""
dist_core_parent_pid=""
dist_core_parent_starttime=""
dist_core_group_starttime=""
if [[ -n "${dist_core_group_info}" ]]; then
  IFS=' ' read -r dist_core_group_state dist_core_shell_parent_pid \
    dist_core_group_pid dist_core_group_session dist_core_shell_starttime <<< \
    "${dist_core_group_info}"
  dist_core_group_leader_info="$(dist_process_group_info "${dist_core_group_pid}" \
    2>/dev/null || true)"
  if [[ -n "${dist_core_group_leader_info}" ]]; then
    IFS=' ' read -r dist_core_group_state dist_core_parent_pid \
      dist_core_group_pid dist_core_group_session dist_core_group_leader_starttime <<< \
      "${dist_core_group_leader_info}"
  else
    dist_core_parent_pid="${dist_core_shell_parent_pid}"
  fi
  dist_core_parent_starttime="$(process_starttime "${dist_core_parent_pid}" \
    allow-stopped 2>/dev/null || true)"
  dist_core_group_starttime="$(process_starttime "${dist_core_group_pid}" \
    allow-stopped 2>/dev/null || true)"
fi
dist_parent_death_handler() {
  local group_info
  local shell_info
  local group_state
  local current_parent_pid
  local process_group
  local session
  local current_starttime
  local shell_state
  local shell_parent_pid
  local shell_process_group
  local shell_session
  local shell_starttime
  local expected_parent_pid
  local current_parent_starttime
  local now_ns
  local cleanup_deadline_ns
  if [[ ! "${dist_core_group_pid}" =~ ^[0-9]+$ ||
      ! "${dist_core_group_starttime}" =~ ^[0-9]+$ ||
      ! "${dist_core_parent_pid}" =~ ^[0-9]+$ ||
      ! "${dist_core_parent_starttime}" =~ ^[0-9]+$ ]]; then
    printf 'build-dist parent-death group identity is unavailable\n' >&2
    exit 125
  fi
  if ! group_info="$(dist_process_group_info "${dist_core_group_pid}")" ||
      ! shell_info="$(dist_process_group_info "$$")"; then
    printf 'build-dist parent-death group identity is unavailable\n' >&2
    exit 125
  fi
  IFS=' ' read -r group_state current_parent_pid process_group session current_starttime <<< "${group_info}"
  IFS=' ' read -r shell_state shell_parent_pid shell_process_group shell_session \
    shell_starttime <<< "${shell_info}"
  if [[ "${process_group}" != "${dist_core_group_pid}" ||
      "${session}" != "${dist_core_group_pid}" ||
      "${shell_process_group}" != "${dist_core_group_pid}" ||
      "${shell_session}" != "${dist_core_group_pid}" ]]; then
    printf 'build-dist parent-death group identity changed\n' >&2
    exit 125
  fi
  if [[ "${current_parent_pid}" == "${dist_core_parent_pid}" ]]; then
    current_parent_starttime="$(process_starttime "${dist_core_parent_pid}" \
      allow-stopped 2>/dev/null || true)"
    if [[ ! "${dist_core_parent_starttime}" =~ ^[0-9]+$ ||
        "${current_parent_starttime}" != "${dist_core_parent_starttime}" ]]; then
      printf 'build-dist parent-death parent identity changed\n' >&2
      exit 125
    fi
    expected_parent_pid="${dist_core_parent_pid}"
  elif [[ "${dist_core_parent_starttime}" =~ ^[0-9]+$ ]] &&
      current_parent_starttime="$(process_starttime "${dist_core_parent_pid}" \
        allow-stopped 2>/dev/null || true)" &&
      [[ "${current_parent_starttime}" == "${dist_core_parent_starttime}" ]]; then
    printf 'build-dist parent-death parent identity is still live\n' >&2
    exit 125
  else
    expected_parent_pid="${current_parent_pid}"
  fi
  if ! dist_group_identity_matches "${dist_core_group_pid}" "${expected_parent_pid}" \
      "${dist_core_group_starttime}"; then
    printf 'build-dist parent-death group identity changed\n' >&2
    exit 125
  fi
  trap '' TERM
  if ! kill -TERM -- "-${dist_core_group_pid}" 2>/dev/null; then
    printf 'build-dist parent-death TERM failed\n' >&2
    exit 125
  fi
  if ! now_ns="$(dist_now_monotonic_ns)"; then
    printf 'build-dist parent-death deadline unavailable\n' >&2
    exit 125
  fi
  cleanup_deadline_ns=$((now_ns + DIST_BUILD_KILL_AFTER_SECONDS * 1000000000))
  if [[ "${DIST_BUILD_DEADLINE:-}" =~ ^[0-9]+$ ]] &&
      (( DIST_BUILD_DEADLINE < cleanup_deadline_ns )); then
    cleanup_deadline_ns=${DIST_BUILD_DEADLINE}
  fi
  dist_wait_until_monotonic_deadline "${cleanup_deadline_ns}"
  if ! dist_group_identity_matches "${dist_core_group_pid}" "${expected_parent_pid}" \
      "${dist_core_group_starttime}"; then
    printf 'build-dist parent-death group identity changed before KILL\n' >&2
    exit 125
  fi
  if ! kill -KILL -- "-${dist_core_group_pid}" 2>/dev/null; then
    printf 'build-dist parent-death KILL failed\n' >&2
    exit 125
  fi
  exit 125
}
trap dist_parent_death_handler TERM

# FD198 is private-launcher lifecycle state, not authentication. Same-UID
# callers can forge a valid handoff by contract; argv/env alone cannot select it.
handoff_fd_present=0
if [[ -e "/proc/self/fd/${DIST_BUILD_HANDOFF_FD}" ||
    -L "/proc/self/fd/${DIST_BUILD_HANDOFF_FD}" ]]; then
  handoff_fd_present=1
fi
if (( ! handoff_fd_present )); then
  exec timeout --signal=TERM --kill-after="${DIST_BUILD_KILL_AFTER_SECONDS}s" \
    "$((DIST_BUILD_TIMEOUT_SECONDS - DIST_BUILD_KILL_AFTER_SECONDS))s" \
    python3 -I -B - \
      "${BASH_SOURCE[0]}" \
      "${DIST_BUILD_TIMEOUT_SECONDS}" \
      "${DIST_BUILD_KILL_AFTER_SECONDS}" \
      "${DIST_BUILD_INITIAL_PROBE_TIMEOUT_SECONDS}" \
      "${DIST_BUILD_STARTUP_RESERVE_NS}" \
      -- "$@" <<'PY'
import fcntl
import os
import secrets
import signal
import stat
import subprocess
import sys
import time


(
    script_path,
    timeout_seconds,
    kill_after_seconds,
    probe_timeout_seconds,
    startup_reserve_ns,
    separator,
    *forwarded_args,
) = sys.argv[1:]
if separator != "--":
    print("build-dist startup handoff separator is invalid", file=sys.stderr)
    raise SystemExit(1)
try:
    timeout_seconds = int(timeout_seconds)
    kill_after_seconds = int(kill_after_seconds)
    probe_timeout_seconds = float(probe_timeout_seconds)
    startup_reserve_ns = int(startup_reserve_ns)
except ValueError:
    print("build-dist startup timeout configuration is invalid", file=sys.stderr)
    raise SystemExit(1)
if (
    timeout_seconds <= kill_after_seconds
    or kill_after_seconds <= 0
    or probe_timeout_seconds <= 0
    or startup_reserve_ns <= 0
):
    print("build-dist startup timeout configuration is unsafe", file=sys.stderr)
    raise SystemExit(1)


def fail_probe(_signum, _frame):
    raise TimeoutError("build-dist initial monotonic probe exceeded its hard cap")


signal.signal(signal.SIGALRM, fail_probe)
signal.signal(signal.SIGUSR1, lambda _signum, _frame: None)
try:
    signal.setitimer(signal.ITIMER_REAL, probe_timeout_seconds)
    try:
        build_start_monotonic = time.monotonic_ns()
        nonce = secrets.token_hex(32)
        probe_end_monotonic = time.monotonic_ns()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
except TimeoutError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(124) from exc
finally:
    signal.setitimer(signal.ITIMER_REAL, 0)
probe_elapsed_ns = probe_end_monotonic - build_start_monotonic
if probe_elapsed_ns < 0 or probe_elapsed_ns > int(probe_timeout_seconds * 1_000_000_000):
    print("build-dist initial monotonic probe is invalid", file=sys.stderr)
    raise SystemExit(124)
startup_grace_ns = max(startup_reserve_ns, kill_after_seconds * 1_000_000_000)
deadline = probe_end_monotonic + (
    timeout_seconds * 1_000_000_000
    - probe_elapsed_ns
    - startup_grace_ns
)
if deadline <= probe_end_monotonic + kill_after_seconds * 1_000_000_000:
    print("build-dist startup handoff leaves no safe deadline", file=sys.stderr)
    raise SystemExit(124)

if not hasattr(os, "memfd_create") or not hasattr(os, "MFD_CLOEXEC"):
    print("build-dist startup handoff needs memfd_create", file=sys.stderr)
    raise SystemExit(1)
lock_fd = None
child_fd = None
handoff_fd = 198
handoff_fd_open = False
monitor_read_fd = None
monitor_write_fd = None
monitor_report_fd_open = False
try:
    lock_fd = os.memfd_create("build-dist-startup-handoff", os.MFD_CLOEXEC)
    os.fchmod(lock_fd, 0o600)
    lock_stat = os.fstat(lock_fd)
    if (
        not stat.S_ISREG(lock_stat.st_mode)
        or lock_stat.st_uid != os.geteuid()
        or lock_stat.st_mode & 0o077
        or getattr(lock_stat, "st_nlink", 1) != 0
    ):
        print("build-dist startup handoff FD is unsafe", file=sys.stderr)
        raise SystemExit(1)
    handoff_identity = ":".join(
        str(value)
        for value in (
            lock_stat.st_dev,
            lock_stat.st_ino,
            lock_stat.st_mode,
            lock_stat.st_uid,
            lock_stat.st_nlink,
        )
    )
    parent_pid = os.getpid()
    payload = f"{nonce}\n{deadline}\n{handoff_identity}\n{parent_pid}\n".encode("ascii")
    view = memoryview(payload)
    while view:
        written = os.write(lock_fd, view)
        if written <= 0:
            raise OSError("failed to write build-dist startup handoff")
        view = view[written:]
    os.fsync(lock_fd)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    child_fd = os.open(f"/proc/self/fd/{lock_fd}", os.O_RDWR | os.O_CLOEXEC)
    child_stat = os.fstat(child_fd)
    child_identity = ":".join(
        str(value)
        for value in (
            child_stat.st_dev,
            child_stat.st_ino,
            child_stat.st_mode,
            child_stat.st_uid,
            child_stat.st_nlink,
        )
    )
    if child_identity != handoff_identity or child_stat.st_size != len(payload):
        print("build-dist startup handoff identity changed", file=sys.stderr)
        raise SystemExit(1)
    if hasattr(os, "pipe2") and hasattr(os, "O_CLOEXEC"):
        monitor_read_fd, monitor_write_fd = os.pipe2(os.O_CLOEXEC)
    else:
        monitor_read_fd, monitor_write_fd = os.pipe()
        os.set_inheritable(monitor_read_fd, False)
        os.set_inheritable(monitor_write_fd, False)
    os.dup2(monitor_write_fd, 197, inheritable=True)
    monitor_report_fd_open = True
    os.set_blocking(monitor_read_fd, False)
    os.dup2(child_fd, handoff_fd, inheritable=True)
    handoff_fd_open = True

    def process_info(pid):
        while True:
            try:
                with open(f"/proc/{pid}/stat", "rb") as handle:
                    record = handle.read(4096)
                break
            except InterruptedError:
                if time.monotonic_ns() >= deadline:
                    return None
                continue
            except (OSError, ValueError):
                return None
        marker = record.rfind(b") ")
        if marker < 0:
            return None
        fields = record[marker + 2 :].split()
        if len(fields) < 20:
            return None
        try:
            return (
                fields[0],
                int(fields[1]),
                int(fields[2]),
                int(fields[3]),
                int(fields[19]),
            )
        except ValueError:
            return None

    watchdog_pid = os.getppid()
    watchdog_info = process_info(watchdog_pid)
    if watchdog_info is None or watchdog_info[0] in (b"Z", b"X", b"T", b"t"):
        print("build-dist startup watchdog identity is unavailable", file=sys.stderr)
        raise SystemExit(1)
    watchdog_starttime = watchdog_info[4]
    abort_requested = False

    def request_abort(_signum, _frame):
        global abort_requested
        abort_requested = True

    signal.signal(signal.SIGTERM, request_abort)
    signal.signal(signal.SIGINT, request_abort)

    child_parent_pid = os.getpid()
    child_parent_info = process_info(child_parent_pid)
    if (
        child_parent_info is None
        or child_parent_info[0] in (b"Z", b"X", b"T", b"t")
    ):
        print("build-dist child parent identity is unavailable", file=sys.stderr)
        raise SystemExit(1)
    child_parent_identity = child_parent_info[1:]

    def install_child_parent_death_signal():
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        if os.getppid() != child_parent_pid:
            os._exit(125)
        try:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            pdeath_signal = 1
            if libc.prctl(pdeath_signal, signal.SIGTERM, 0, 0, 0) != 0:
                os._exit(125)
        except (AttributeError, OSError, TypeError):
            os._exit(125)
        if os.getppid() != child_parent_pid:
            os._exit(125)
        current_parent_info = process_info(child_parent_pid)
        if (
            current_parent_info is None
            or current_parent_info[0] in (b"Z", b"X", b"T", b"t")
            or current_parent_info[1:] != child_parent_identity
        ):
            os._exit(125)

    child_environment = os.environ.copy()
    child_environment["DIST_BUILD_DEADLINE"] = str(deadline)
    # Bash defers trapped TERM while waiting for foreground commands. Keep a
    # private Python group leader so PDEATHSIG can run bounded group cleanup.
    child_launcher = r"""
import os
import signal
import subprocess
import sys
import time


try:
    kill_after_seconds = int(sys.argv[1])
    script_path = sys.argv[2]
    forwarded_args = sys.argv[3:]
    proxy_deadline_ns = int(os.environ["DIST_BUILD_DEADLINE"])
except (KeyError, ValueError, IndexError) as exc:
    raise SystemExit(125) from exc
if kill_after_seconds <= 0 or proxy_deadline_ns <= time.monotonic_ns():
    raise SystemExit(125)
proxy_pid = os.getpid()
proxy_parent_pid = os.getppid()


def process_info(pid):
    while True:
        try:
            with open(f"/proc/{pid}/stat", "rb") as handle:
                record = handle.read(4096)
            break
        except InterruptedError:
            if time.monotonic_ns() >= proxy_deadline_ns:
                return None
            continue
        except (OSError, ValueError):
            return None
    marker = record.rfind(b") ")
    if marker < 0:
        return None
    fields = record[marker + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return (
            fields[0],
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            int(fields[19]),
        )
    except ValueError:
        return None


proxy_info = process_info(proxy_pid)
parent_info = process_info(proxy_parent_pid)
if (
    proxy_info is None
    or parent_info is None
    or proxy_info[0] in (b"Z", b"X", b"T", b"t")
    or parent_info[0] in (b"Z", b"X", b"T", b"t")
    or proxy_info[1] != proxy_parent_pid
    or proxy_info[2] != proxy_pid
    or proxy_info[3] != proxy_pid
):
    raise SystemExit(125)


abort_requested = False


def request_abort(_signum, _frame):
    global abort_requested
    abort_requested = True


signal.signal(signal.SIGTERM, request_abort)
signal.signal(signal.SIGINT, request_abort)
signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
if abort_requested:
    raise SystemExit(125)
core_environment = os.environ.copy()
core_environment["DIST_BUILD_SUPERVISOR_PID"] = str(proxy_parent_pid)
core = subprocess.Popen(
    [script_path, *forwarded_args],
    env=core_environment,
    pass_fds=(198, 197),
    start_new_session=True,
)
core_info = process_info(core.pid)
if (
    core_info is None
    or core_info[0] in (b"X", b"T", b"t")
    or core_info[1] != proxy_pid
    or core_info[2] != core.pid
    or core_info[3] != core.pid
):
    try:
        core.wait(timeout=kill_after_seconds)
    except (InterruptedError, subprocess.TimeoutExpired):
        pass
    raise SystemExit(125)
core_starttime = core_info[4]
for descriptor in (197, 198):
    try:
        os.close(descriptor)
    except OSError:
        pass


def core_group_identity():
    info = process_info(core.pid)
    if (
        info is None
        or info[0] in (b"X", b"T", b"t")
        or info[1] != proxy_pid
        or info[2] != core.pid
        or info[3] != core.pid
        or info[4] != core_starttime
    ):
        return None
    return info


def signal_core_group(signum):
    while True:
        if core_group_identity() is None:
            return False
        try:
            os.kill(-core.pid, signum)
            return True
        except InterruptedError:
            if time.monotonic_ns() >= proxy_deadline_ns:
                return False
        except OSError:
            return False


def core_exit_status():
    while True:
        try:
            result = os.waitid(
                os.P_PID,
                core.pid,
                os.WEXITED | os.WNOHANG | os.WNOWAIT,
            )
        except InterruptedError:
            if time.monotonic_ns() >= proxy_deadline_ns:
                raise TimeoutError("build-dist core wait was interrupted too long")
            continue
        except ChildProcessError as exc:
            raise SystemExit(125) from exc
        if result is None or result.si_pid == 0:
            return None
        if result.si_code == os.CLD_EXITED:
            return result.si_status
        if result.si_code in (os.CLD_KILLED, os.CLD_DUMPED):
            return -result.si_status
        return 125


def terminate_group():
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    cleanup_deadline_ns = min(
        proxy_deadline_ns,
        time.monotonic_ns() + kill_after_seconds * 1_000_000_000,
    )
    if not signal_core_group(signal.SIGTERM):
        os._exit(125)
    term_deadline_ns = min(
        cleanup_deadline_ns,
        time.monotonic_ns() + max(100_000_000, kill_after_seconds * 500_000_000),
    )
    while time.monotonic_ns() < term_deadline_ns:
        try:
            if core_exit_status() is not None:
                break
        except TimeoutError:
            os._exit(125)
        try:
            time.sleep(0.02)
        except InterruptedError:
            continue
    if not signal_core_group(signal.SIGKILL):
        os._exit(125)
    while time.monotonic_ns() < cleanup_deadline_ns:
        remaining = max(0.001, (cleanup_deadline_ns - time.monotonic_ns()) / 1_000_000_000)
        try:
            core.wait(timeout=min(0.05, remaining))
            return
        except subprocess.TimeoutExpired:
            continue
        except InterruptedError:
            continue
    os._exit(125)


while True:
    try:
        status = core_exit_status()
    except TimeoutError:
        raise SystemExit(124)
    if status is not None:
        terminate_group()
        raise SystemExit(128 - status if status < 0 else status)
    if abort_requested:
        terminate_group()
        raise SystemExit(124)
    try:
        time.sleep(0.05)
    except InterruptedError:
        continue
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            child_launcher,
            str(kill_after_seconds),
            script_path,
            *forwarded_args,
        ],
        env=child_environment,
        pass_fds=(handoff_fd, 197),
        preexec_fn=install_child_parent_death_signal,
        start_new_session=True,
    )
    child_info = process_info(child.pid)
    if (
        child_info is None
        or child_info[0] in (b"Z", b"X", b"T", b"t")
        or child_info[1] != child_parent_pid
        or child_info[2] != child.pid
        or child_info[3] != child.pid
    ):
        try:
            child.wait(timeout=kill_after_seconds)
        except (InterruptedError, subprocess.TimeoutExpired):
            pass
        print("build-dist child process group identity is invalid", file=sys.stderr)
        raise SystemExit(1)
    child_starttime = child_info[4]
    if monitor_write_fd != 197:
        os.close(monitor_write_fd)
    monitor_write_fd = None
    os.close(197)
    monitor_report_fd_open = False
    monitor_contract = bytearray()
    monitor_identity = None
    monitor_parent_identity = None

    def monitor_process_identity():
        if monitor_identity is None or monitor_parent_identity is None:
            return False
        info = process_info(monitor_identity[0])
        if (
            info is None
            or info[0] in (b"Z", b"X", b"T", b"t")
            or info[1] != monitor_parent_identity[0]
            or info[4] != monitor_identity[1]
        ):
            return False
        parent_info = process_info(monitor_parent_identity[0])
        if (
            parent_info is None
            or parent_info[0] in (b"Z", b"X", b"T", b"t")
            or parent_info[4] != monitor_parent_identity[1]
        ):
            return False
        if info[2] == child.pid and info[3] == child.pid:
            return True
        return (
            info[2] == monitor_parent_identity[0]
            and info[3] == monitor_parent_identity[0]
            and parent_info[1] == child.pid
            and parent_info[2] == monitor_parent_identity[0]
            and parent_info[3] == monitor_parent_identity[0]
        )

    def child_status_after_monitor_loss():
        wait_deadline = min(
            deadline,
            time.monotonic_ns() + kill_after_seconds * 1_000_000_000,
        )
        while True:
            try:
                status = child.poll()
            except InterruptedError:
                status = None
            if status is not None or time.monotonic_ns() >= wait_deadline:
                return status
            try:
                time.sleep(0.01)
            except InterruptedError:
                continue

    def child_group_identity():
        info = process_info(child.pid)
        if (
            info is None
            or info[0] in (b"Z", b"X", b"T", b"t")
            or info[1] != child_parent_pid
            or info[2] != child.pid
            or info[3] != child.pid
            or info[4] != child_starttime
        ):
            return None
        return info

    def signal_child_group(signum):
        if child_group_identity() is None:
            return False
        try:
            os.killpg(child.pid, signum)
        except OSError:
            return False
        return True

    def terminate_child():
        if not signal_child_group(signal.SIGTERM):
            print("build-dist child process group identity changed", file=sys.stderr)
            raise SystemExit(125)
        cleanup_deadline = time.monotonic() + kill_after_seconds
        term_deadline = cleanup_deadline
        while time.monotonic() < term_deadline:
            try:
                if child.poll() is not None:
                    break
            except InterruptedError:
                continue
            try:
                time.sleep(0.02)
            except InterruptedError:
                continue
        try:
            if child.poll() is not None:
                return
        except InterruptedError:
            pass
        if not signal_child_group(signal.SIGKILL):
            print("build-dist child process group identity changed before KILL", file=sys.stderr)
            raise SystemExit(125)
        while time.monotonic() < cleanup_deadline:
            try:
                if child.poll() is not None:
                    return
            except InterruptedError:
                continue
            try:
                time.sleep(0.01)
            except InterruptedError:
                continue

    while True:
        try:
            status = child.poll()
        except InterruptedError:
            continue
        if abort_requested:
            terminate_child()
            raise SystemExit(124)
        if status is not None:
            break
        if monitor_identity is None:
            try:
                monitor_contract.extend(os.read(monitor_read_fd, 128))
            except (BlockingIOError, InterruptedError):
                pass
            if len(monitor_contract) > 128:
                terminate_child()
                raise SystemExit(1)
            if b"\n" in monitor_contract:
                line, trailing = bytes(monitor_contract).split(b"\n", 1)
                if trailing or len(line.split()) != 2:
                    terminate_child()
                    raise SystemExit(1)
                try:
                    monitor_pid, monitor_starttime = (
                        int(value) for value in line.split()
                    )
                except ValueError:
                    terminate_child()
                    raise SystemExit(1)
                if monitor_pid <= 1 or monitor_starttime <= 0:
                    terminate_child()
                    raise SystemExit(1)
                monitor_info = process_info(monitor_pid)
                if (
                    monitor_info is None
                    or monitor_info[0] in (b"Z", b"X", b"T", b"t")
                    or monitor_info[1] <= 1
                ):
                    status = child_status_after_monitor_loss()
                    if status is not None:
                        break
                    terminate_child()
                    raise SystemExit(1)
                monitor_parent_info = process_info(monitor_info[1])
                if (
                    monitor_parent_info is None
                    or monitor_parent_info[0] in (b"Z", b"X", b"T", b"t")
                ):
                    status = child_status_after_monitor_loss()
                    if status is not None:
                        break
                    terminate_child()
                    raise SystemExit(1)
                monitor_identity = (monitor_pid, monitor_starttime)
                monitor_parent_identity = (monitor_info[1], monitor_parent_info[4])
                if not monitor_process_identity():
                    status = child_status_after_monitor_loss()
                    if status is not None:
                        break
                    terminate_child()
                    raise SystemExit(1)
        else:
            if not monitor_process_identity():
                status = child_status_after_monitor_loss()
                if status is not None:
                    break
                terminate_child()
                raise SystemExit(125)
        try:
            current_watchdog_pid = os.getppid()
        except InterruptedError:
            continue
        current_watchdog_info = process_info(watchdog_pid)
        if (
            current_watchdog_pid != watchdog_pid
            or current_watchdog_info is None
            or current_watchdog_info[0] in (b"Z", b"X", b"T", b"t")
            or current_watchdog_info[4] != watchdog_starttime
        ):
            terminate_child()
            raise SystemExit(1)
        try:
            time.sleep(0.05)
        except InterruptedError:
            continue
    raise SystemExit(128 - status if status < 0 else status)
except TimeoutError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(124) from exc
finally:
    for descriptor in (
        monitor_read_fd,
        monitor_write_fd,
        197 if monitor_report_fd_open else None,
        handoff_fd if handoff_fd_open else None,
        child_fd,
        lock_fd,
    ):
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
PY
fi

handoff_monitor_pid=""
handoff_monitor_starttime=""
handoff_monitor_ready=0
handoff_monitor_stopping=0
handoff_monitor_alive() {
  local current_starttime
  if (( ! handoff_monitor_ready )) ||
      [[ ! "${handoff_monitor_pid}" =~ ^[0-9]+$ ]] ||
      [[ ! "${handoff_monitor_starttime}" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if ! current_starttime="$(process_starttime "${handoff_monitor_pid}")"; then
    return 1
  fi
  [[ "${current_starttime}" == "${handoff_monitor_starttime}" ]]
}
check_handoff_monitor() {
  if (( handoff_monitor_stopping || ! handoff_monitor_ready )); then
    return 0
  fi
  if ! handoff_monitor_alive; then
    printf 'build-dist startup monitor died or changed identity\n' >&2
    stop_handoff_monitor
    exit 125
  fi
  return 0
}
stop_handoff_monitor() {
  local monitor_killed=0
  handoff_monitor_stopping=1
  if handoff_monitor_alive; then
    if kill -KILL -- "${handoff_monitor_pid}" 2>/dev/null; then
      monitor_killed=1
    fi
  elif [[ "${handoff_monitor_pid}" =~ ^[0-9]+$ &&
      "${handoff_monitor_starttime}" =~ ^[0-9]+$ ]]; then
    local current_starttime
    if current_starttime="$(process_starttime "${handoff_monitor_pid}" allow-stopped)" &&
        [[ "${current_starttime}" == "${handoff_monitor_starttime}" ]]; then
      if kill -KILL -- "${handoff_monitor_pid}" 2>/dev/null; then
        monitor_killed=1
      fi
    fi
  fi
  if (( monitor_killed )) && [[ "${handoff_monitor_pid}" =~ ^[0-9]+$ ]]; then
    wait "${handoff_monitor_pid}" 2>/dev/null || true
  fi
  handoff_monitor_pid=""
  handoff_monitor_starttime=""
  handoff_monitor_ready=0
  handoff_monitor_stopping=0
}
trap check_handoff_monitor CHLD
coproc handoff_monitor_proc {
  exec python3 -I -B - "${DIST_BUILD_HANDOFF_FD}" "$$" \
    "${DIST_BUILD_TIMEOUT_SECONDS}" "${DIST_BUILD_KILL_AFTER_SECONDS}" \
    "${DIST_BUILD_STARTUP_RESERVE_NS}" "${DIST_BUILD_HANDOFF_TIMEOUT_SECONDS}" <<'PY'
import fcntl
import os
import re
import signal
import stat
import sys
import time

try:
    os.close(197)
except OSError:
    pass

(
    fd_value,
    target_pid,
    max_seconds,
    kill_after_seconds,
    startup_reserve_ns,
    handoff_timeout_seconds,
) = sys.argv[1:]
try:
    fd = int(fd_value)
    target_pid = int(target_pid)
    max_seconds = int(max_seconds)
    kill_after_seconds = int(kill_after_seconds)
    startup_reserve_ns = int(startup_reserve_ns)
    handoff_timeout_seconds = int(handoff_timeout_seconds)
except ValueError as exc:
    raise SystemExit("build-dist startup handoff values are invalid") from exc
if (
    fd != 198
    or target_pid <= 1
    or max_seconds <= kill_after_seconds
    or kill_after_seconds <= 0
    or startup_reserve_ns <= 0
    or handoff_timeout_seconds <= 0
):
    raise SystemExit("build-dist startup handoff values are invalid")


def identity(path_stat):
    return ":".join(
        str(value)
        for value in (
            path_stat.st_dev,
            path_stat.st_ino,
            path_stat.st_mode,
            path_stat.st_uid,
            path_stat.st_nlink,
        )
    )


startup_deadline = time.monotonic_ns() + handoff_timeout_seconds * 1_000_000_000


def retry_eintr(operation, deadline_ns=None):
    limit_ns = startup_deadline if deadline_ns is None else deadline_ns
    while True:
        try:
            return operation()
        except InterruptedError:
            if time.monotonic_ns() >= limit_ns:
                raise TimeoutError("build-dist startup handoff was interrupted too long")


try:
    handoff_stat = retry_eintr(lambda: os.fstat(fd))
except (OSError, TimeoutError) as exc:
    raise SystemExit("build-dist startup handoff FD is not inherited") from exc
if (
    not stat.S_ISREG(handoff_stat.st_mode)
    or handoff_stat.st_uid != os.geteuid()
    or handoff_stat.st_mode & 0o077
    or getattr(handoff_stat, "st_nlink", 1) != 0
    or handoff_stat.st_size <= 0
    or handoff_stat.st_size > 4096
):
    raise SystemExit("build-dist startup handoff FD identity is invalid")
try:
    retry_eintr(lambda: os.lseek(fd, 0, os.SEEK_SET))
    payload = retry_eintr(lambda: os.read(fd, 4097))
except (OSError, TimeoutError) as exc:
    raise SystemExit("build-dist startup handoff payload is unavailable") from exc
try:
    payload_nonce, deadline_value, expected_identity, parent_value, trailing = payload.split(b"\n")
except ValueError as exc:
    raise SystemExit("build-dist startup handoff payload is invalid") from exc
try:
    parent_pid = int(parent_value.decode("ascii"))
    requested_deadline = int(deadline_value.decode("ascii"))
except (UnicodeDecodeError, ValueError) as exc:
    raise SystemExit("build-dist startup handoff payload is invalid") from exc
try:
    payload_nonce.decode("ascii")
    expected_identity.decode("ascii")
except UnicodeDecodeError as exc:
    raise SystemExit("build-dist startup handoff payload is invalid") from exc
if (
    trailing
    or len(payload) > 4096
    or re.fullmatch(r"[0-9a-f]{64}", payload_nonce.decode("ascii")) is None
    or re.fullmatch(r"[0-9]+", deadline_value.decode("ascii")) is None
    or parent_pid <= 1
    or re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+:[0-9]+:[0-9]+", expected_identity.decode("ascii")) is None
    or identity(handoff_stat) != expected_identity.decode("ascii")
):
    raise SystemExit("build-dist startup handoff payload is invalid")
now_ns = time.monotonic_ns()
if requested_deadline <= now_ns:
    raise SystemExit("build-dist startup handoff deadline is expired")
maximum_deadline = now_ns + max_seconds * 1_000_000_000 - max(
    startup_reserve_ns, kill_after_seconds * 1_000_000_000
)
effective_deadline = min(requested_deadline, maximum_deadline)
if effective_deadline <= now_ns + kill_after_seconds * 1_000_000_000:
    raise SystemExit("build-dist startup handoff deadline leaves no grace")


def process_info(pid, deadline_ns=None):
    try:
        def read_record():
            with open(f"/proc/{pid}/stat", "rb") as handle:
                return handle.read(4096)

        record = retry_eintr(read_record, deadline_ns)
    except (OSError, ValueError, TimeoutError):
        return None
    marker = record.rfind(b") ")
    if marker < 0:
        return None
    fields = record[marker + 2 :].split()
    if len(fields) < 20:
        return None
    try:
        return (
            fields[0],
            int(fields[1]),
            int(fields[2]),
            int(fields[3]),
            int(fields[19]),
        )
    except ValueError:
        return None


parent_info = process_info(parent_pid)
if parent_info is None or parent_info[0] in (b"Z", b"X", b"T", b"t"):
    raise SystemExit("build-dist startup handoff parent is invalid")
parent_starttime = parent_info[4]
target_info = process_info(target_pid)
if (
    target_info is None
    or target_info[0] in (b"Z", b"X", b"T", b"t")
    or target_info[2] <= 1
    or target_info[3] != target_info[2]
):
    raise SystemExit("build-dist startup handoff parent is invalid")
target_starttime = target_info[4]
target_group_pid = target_info[2]
target_group_info = process_info(target_group_pid)
if (
    target_group_info is None
    or target_group_info[0] in (b"Z", b"X", b"T", b"t")
    or target_group_info[2] != target_group_pid
    or target_group_info[3] != target_group_pid
    or target_group_info[1] <= 1
):
    raise SystemExit("build-dist startup handoff group is invalid")
if target_group_pid != target_pid and target_info[1] != target_group_pid:
    raise SystemExit("build-dist startup handoff group parent is invalid")
target_group_starttime = target_group_info[4]
expected_target_parent_pid = target_info[1]
target_group_parent_pid = target_group_info[1]
if target_group_parent_pid == parent_pid:
    target_group_parent_starttime = parent_starttime
else:
    target_group_parent_info = process_info(target_group_parent_pid)
    if (
        target_group_parent_info is None
        or target_group_parent_info[0] in (b"Z", b"X", b"T", b"t")
        or target_group_parent_info[1] != parent_pid
    ):
        raise SystemExit("build-dist startup handoff group parent is invalid")
    target_group_parent_starttime = target_group_parent_info[4]
expected_group_parent_pid = target_group_parent_pid
target_parent_starttime = (
    target_group_starttime
    if target_group_pid != target_pid
    else target_group_parent_starttime
)
try:
    liveness_fd = retry_eintr(
        lambda: os.open(f"/proc/self/fd/{fd}", os.O_RDWR | os.O_CLOEXEC)
    )
except (OSError, TimeoutError) as exc:
    raise SystemExit("build-dist startup handoff liveness FD is unavailable") from exc
try:
    try:
        retry_eintr(lambda: fcntl.flock(liveness_fd, fcntl.LOCK_EX | fcntl.LOCK_NB))
    except BlockingIOError:
        pass
    else:
        retry_eintr(lambda: fcntl.flock(liveness_fd, fcntl.LOCK_UN))
        raise SystemExit("build-dist startup handoff parent lock is missing")
except BaseException:
    os.close(liveness_fd)
    raise
try:
    os.close(fd)
except OSError:
    pass


def parent_identity_alive():
    info = process_info(parent_pid, effective_deadline)
    return (
        info is not None
        and info[0] not in (b"Z", b"X", b"T", b"t")
        and info[4] == parent_starttime
    )


def group_parent_identity():
    info = process_info(target_group_parent_pid, effective_deadline)
    if (
        info is None
        or info[0] in (b"Z", b"X", b"T", b"t")
        or info[4] != target_group_parent_starttime
    ):
        return None
    if target_group_parent_pid == parent_pid:
        return info if parent_identity_alive() else None
    if info[1] == parent_pid:
        return info if parent_identity_alive() else None
    return info if not parent_identity_alive() else None


def target_group_identity():
    global expected_group_parent_pid, expected_target_parent_pid
    group_info = process_info(target_group_pid, effective_deadline)
    info = process_info(target_pid, effective_deadline)
    if (
        group_info is None
        or group_info[0] in (b"Z", b"X", b"T", b"t")
        or group_info[2] != target_group_pid
        or group_info[3] != target_group_pid
        or group_info[4] != target_group_starttime
        or info is None
        or info[0] in (b"Z", b"X", b"T", b"t")
        or info[2] != target_group_pid
        or info[3] != target_group_pid
        or info[4] != target_starttime
    ):
        return None
    if group_info[1] == expected_group_parent_pid:
        if group_parent_identity() is None:
            return None
    elif target_group_parent_pid == parent_pid and not parent_identity_alive():
        expected_group_parent_pid = group_info[1]
    else:
        return None
    if target_group_pid == target_pid:
        if info[1] == expected_target_parent_pid:
            if (
                expected_target_parent_pid == parent_pid
                and parent_identity_alive() is False
                and group_info[1] == parent_pid
            ):
                return None
        elif target_group_parent_pid == parent_pid and not parent_identity_alive():
            expected_target_parent_pid = info[1]
        else:
            return None
    if info[1] != expected_target_parent_pid:
        return None
    if target_group_pid != target_pid:
        target_parent_info = process_info(expected_target_parent_pid, effective_deadline)
        if (
            target_parent_info is None
            or target_parent_info[0] in (b"Z", b"X", b"T", b"t")
            or target_parent_info[2] != target_group_pid
            or target_parent_info[3] != target_group_pid
            or target_parent_info[4] != target_parent_starttime
        ):
            return None
    return info


def target_alive():
    return target_group_identity() is not None


def signal_target_group(signum):
    if target_group_identity() is None:
        return False
    try:
        os.killpg(target_group_pid, signum)
    except OSError:
        return False
    return True


def terminate_target():
    if not signal_target_group(signal.SIGTERM):
        return
    end = time.monotonic() + kill_after_seconds
    while target_alive() and time.monotonic() < end:
        try:
            time.sleep(0.01)
        except InterruptedError:
            continue
    signal_target_group(signal.SIGKILL)


signal.signal(signal.SIGTERM, signal.SIG_IGN)
signal.signal(signal.SIGUSR1, lambda _signum, _frame: None)
monitor_info = process_info(os.getpid())
if monitor_info is None or monitor_info[1] != target_pid:
    raise SystemExit("build-dist startup monitor parent is invalid")
print(f"{os.getpid()} {monitor_info[4]} {effective_deadline}", flush=True)
try:
    os.close(1)
except OSError:
    pass
try:
    while target_alive():
        target_info = target_group_identity()
        if target_info is None:
            break
        if (
            (
                target_group_pid == target_pid
                and target_info[1] != expected_target_parent_pid
            )
            or target_info[2] != target_group_pid
            or target_info[3] != target_group_pid
        ):
            terminate_target()
            break
        try:
            retry_eintr(
                lambda: fcntl.flock(liveness_fd, fcntl.LOCK_EX | fcntl.LOCK_NB),
                effective_deadline,
            )
        except BlockingIOError:
            pass
        else:
            retry_eintr(lambda: fcntl.flock(liveness_fd, fcntl.LOCK_UN), effective_deadline)
            terminate_target()
            break
        if time.monotonic_ns() >= effective_deadline:
            terminate_target()
            break
        try:
            time.sleep(0.05)
        except InterruptedError:
            continue
finally:
    os.close(liveness_fd)
PY
}
handoff_monitor_pid="${handoff_monitor_proc_PID:-}"
handoff_read_fd="${handoff_monitor_proc[0]:-}"
if [[ ! "${handoff_monitor_pid}" =~ ^[0-9]+$ ||
    ! "${handoff_read_fd}" =~ ^[0-9]+$ ]]; then
  printf 'build-dist startup monitor could not be started\n' >&2
  stop_handoff_monitor
  exit 1
fi
handoff_monitor_starttime="$(process_starttime "${handoff_monitor_pid}" 2>/dev/null || true)"
monitor_contract_pid=""
monitor_contract_starttime=""
monitor_contract_deadline=""
if ! IFS=' ' read -r -t "${DIST_BUILD_HANDOFF_TIMEOUT_SECONDS}" \
    monitor_contract_pid monitor_contract_starttime monitor_contract_deadline \
    <&"${handoff_read_fd}"; then
  printf 'build-dist startup handoff was rejected\n' >&2
  exec {handoff_read_fd}<&-
  stop_handoff_monitor
  exit 1
fi
exec {handoff_read_fd}<&-
if [[ ! "${monitor_contract_pid}" =~ ^[0-9]+$ ||
    ! "${monitor_contract_starttime}" =~ ^[0-9]+$ ||
    ! "${monitor_contract_deadline}" =~ ^[0-9]+$ ||
    "${monitor_contract_pid}" != "${handoff_monitor_pid}" ||
    ( -n "${handoff_monitor_starttime}" &&
      "${monitor_contract_starttime}" != "${handoff_monitor_starttime}" ) ]]; then
  printf 'build-dist startup handoff was invalid\n' >&2
  stop_handoff_monitor
  exit 1
fi
handoff_monitor_starttime="${monitor_contract_starttime}"
DIST_BUILD_DEADLINE="${monitor_contract_deadline}"
handoff_monitor_ready=1
if ! handoff_monitor_alive; then
  printf 'build-dist startup monitor identity is invalid\n' >&2
  stop_handoff_monitor
  exit 1
fi
if [[ -e "/proc/self/fd/197" || -L "/proc/self/fd/197" ]]; then
  if ! printf '%s %s\n' "${handoff_monitor_pid}" "${handoff_monitor_starttime}" >&197; then
    printf 'build-dist startup monitor report failed\n' >&2
    stop_handoff_monitor
    exit 1
  fi
  exec 197>&-
fi
exec 198>&-
export DIST_BUILD_DEADLINE

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")
distribution_tree_excludes=(
  --exclude-name __pycache__
  --exclude-name .pytest_cache
  --exclude-name .mypy_cache
  --exclude-name .ruff_cache
  --exclude-name .coverage
)

build_remaining_value_ns=""
build_remaining_ns() {
  local now_ns
  if ! now_ns="$(timeout --signal=KILL "${DIST_BUILD_CLOCK_TIMEOUT_SECONDS}s" \
      python3 -I -B -c 'import time; print(time.monotonic_ns())' 2>/dev/null)"; then
    return 1
  fi
  if [[ ! "${now_ns}" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  build_remaining_value_ns=$((DIST_BUILD_DEADLINE - now_ns))
  if (( build_remaining_value_ns <= 0 )); then
    return 1
  fi
}

build_timeout_value() {
  local nanoseconds=$1
  printf '%d.%09d' "$((nanoseconds / 1000000000))" "$((nanoseconds % 1000000000))"
}

build_phase_timeout_seconds() {
  if ! build_remaining_ns; then
    return 1
  fi
  local available_ns=$((build_remaining_value_ns - DIST_BUILD_LAUNCH_MARGIN_NS))
  local kill_after_ns=$((DIST_BUILD_KILL_AFTER_SECONDS * 1000000000))
  if (( available_ns <= kill_after_ns )); then
    return 1
  fi
  local term_ns=$((available_ns - kill_after_ns))
  local term_seconds=$((term_ns / 1000000000))
  if (( term_seconds <= 0 )); then
    return 1
  fi
  printf '%d' "${term_seconds}"
}

run_build_command() {
  if ! build_remaining_ns; then
    printf 'build-dist deadline exceeded before command\n' >&2
    return 124
  fi
  local available_ns=$((build_remaining_value_ns - DIST_BUILD_LAUNCH_MARGIN_NS))
  local kill_after_ns=$((DIST_BUILD_KILL_AFTER_SECONDS * 1000000000))
  if (( available_ns <= kill_after_ns )); then
    printf 'build-dist deadline cannot reserve TERM/KILL grace\n' >&2
    return 124
  fi
  local kill_after_timeout
  local term_timeout
  if (( kill_after_ns >= available_ns )); then
    kill_after_ns=$((available_ns / 2))
  fi
  if (( kill_after_ns <= 0 )); then
    return 124
  fi
  kill_after_timeout="$(build_timeout_value "${kill_after_ns}")"
  term_timeout="$(build_timeout_value "$((available_ns - kill_after_ns))")"
  local command_status=0
  timeout --signal=TERM --kill-after="${kill_after_timeout}s" \
    "${term_timeout}s" "$@" || command_status=$?
  if (( command_status == 124 || command_status == 137 || command_status == 143 )); then
    return 124
  fi
  if ! build_remaining_ns; then
    printf 'build-dist deadline exceeded after command\n' >&2
    return 124
  fi
  return "${command_status}"
}

for tool in python3 tar sha256sum mktemp find grep git stat realpath timeout; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found.\n' "${tool}" >&2
    exit 1
  fi
done

if [[ -L "${safe_fs}" || ! -f "${safe_fs}" || "$(stat -c '%F' "${safe_fs}")" != "regular file" ]]; then
  printf 'safe local filesystem helper is invalid: %s\n' "${safe_fs}" >&2
  exit 1
fi
if [[ "$(stat -c '%h' "${safe_fs}")" -ne 1 ]]; then
  printf 'safe local filesystem helper must not be hardlinked: %s\n' "${safe_fs}" >&2
  exit 1
fi

name="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

MAX_PROJECT_METADATA_BYTES = 1 << 20
with Path("pyproject.toml").open("rb") as handle:
    payload = handle.read(MAX_PROJECT_METADATA_BYTES + 1)
if len(payload) > MAX_PROJECT_METADATA_BYTES:
    raise SystemExit("pyproject.toml is too large")
try:
    data = tomllib.loads(payload.decode("utf-8"))
    name = data["project"]["name"]
except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError, MemoryError) as exc:
    raise SystemExit("pyproject.toml project.name is invalid") from exc
if not isinstance(name, str) or not name:
    raise SystemExit("pyproject.toml project.name is invalid")
print(name)
PY
)"
version="$(
  python3 - <<'PY'
import tomllib
from pathlib import Path

MAX_PROJECT_METADATA_BYTES = 1 << 20
with Path("pyproject.toml").open("rb") as handle:
    payload = handle.read(MAX_PROJECT_METADATA_BYTES + 1)
if len(payload) > MAX_PROJECT_METADATA_BYTES:
    raise SystemExit("pyproject.toml is too large")
try:
    data = tomllib.loads(payload.decode("utf-8"))
    version = data["project"]["version"]
except (KeyError, TypeError, UnicodeDecodeError, tomllib.TOMLDecodeError, RecursionError, MemoryError) as exc:
    raise SystemExit("pyproject.toml project.version is invalid") from exc
if not isinstance(version, str) or not version:
    raise SystemExit("pyproject.toml project.version is invalid")
print(version)
PY
)"
if [[ ! "${name}" == "speed-of-cinnamon" ]]; then
  printf 'unexpected package name: %s\n' "${name}" >&2
  exit 1
fi
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

package="${name}-${version}"
dist_dir="${repo_dir}/dist"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
validate_dist_tmp_root() {
  local requested_root=$1
  local root_abs
  local root_owner
  local root_mode
  local root_mode_bits
  if [[ ! "${requested_root}" == /* ]]; then
    printf 'temporary root must be an absolute path: %s\n' "${requested_root}" >&2
    return 1
  fi
  if [[ -L "${requested_root}" || ! -d "${requested_root}" || ! -w "${requested_root}" ]]; then
    printf 'temporary root must be a writable directory and not a symlink: %s\n' "${requested_root}" >&2
    return 1
  fi
  if ! root_abs="$(realpath "${requested_root}")"; then
    printf 'failed to resolve temporary root: %s\n' "${requested_root}" >&2
    return 1
  fi
  if [[ "${root_abs}" == "${repo_dir}" || "${root_abs}" == "${repo_dir}/"* ]]; then
    printf 'temporary root must be outside repository: %s\n' "${requested_root}" >&2
    return 1
  fi
  root_owner="$(stat -c '%u' "${root_abs}")"
  root_mode="$(stat -c '%a' "${root_abs}")"
  root_mode_bits=$((8#${root_mode}))
  if [[ "${root_owner}" == "${EUID}" ]] &&
      (( (root_mode_bits & 0077) == 0 && (root_mode_bits & 0200) != 0 )); then
    :
  elif [[ "${root_owner}" == "0" &&
      ( "${root_abs}" == "/tmp" || "${root_abs}" == "/var/tmp" || "${root_abs}" == "/dev/shm" ) ]] &&
      (( (root_mode_bits & 01000) != 0 && (root_mode_bits & 0002) != 0 )); then
    :
  else
    printf 'temporary root must be euid-owned private or root-owned sticky standard temp: %s\n' "${requested_root}" >&2
    return 1
  fi
  work_root="${root_abs}"
}

work_root="${TMPDIR:-/tmp}"
if ! validate_dist_tmp_root "${work_root}"; then
  exit 1
fi

workspace_base="${work_root}/speed-of-cinnamon-build-dist-${EUID}"
workspace_lock="${workspace_base}"
if [[ -L "${workspace_base}" || ( -e "${workspace_base}" && ! -d "${workspace_base}" ) ]]; then
  printf 'build-dist workspace base must be a private directory: %s\n' "${workspace_base}" >&2
  exit 1
fi
if ! run_build_command "${safe_fs_cmd[@]}" assert-private-chain build-dist "${workspace_base}" --allow-missing; then
  printf 'build-dist workspace base has an untrusted ancestor chain: %s\n' "${workspace_base}" >&2
  exit 1
fi
if [[ ! -e "${workspace_base}" ]]; then
  if ! run_build_command "${safe_fs_cmd[@]}" mkdirs build-dist "${workspace_base}"; then
    printf 'failed to create private build-dist workspace base: %s\n' "${workspace_base}" >&2
    exit 1
  fi
fi
if ! run_build_command "${safe_fs_cmd[@]}" assert-private-chain build-dist "${workspace_base}"; then
  printf 'build-dist workspace base has an untrusted ancestor chain: %s\n' "${workspace_base}" >&2
  exit 1
fi
if [[ -L "${workspace_base}" || ! -d "${workspace_base}" ]]; then
  printf 'build-dist workspace base is not a directory: %s\n' "${workspace_base}" >&2
  exit 1
fi
workspace_base_owner="$(stat -c '%u' "${workspace_base}")"
workspace_base_mode="$(stat -c '%a' "${workspace_base}")"
workspace_base_mode_bits=$((8#${workspace_base_mode}))
if [[ "${workspace_base_owner}" != "${EUID}" ]] ||
    (( (workspace_base_mode_bits & 0077) != 0 || (workspace_base_mode_bits & 0200) == 0 )); then
  printf 'build-dist workspace base is unsafe: %s\n' "${workspace_base}" >&2
  exit 1
fi

startup_sweep_run_seconds=$((DIST_STARTUP_SWEEP_TIMEOUT_SECONDS - DIST_STARTUP_SWEEP_KILL_AFTER_SECONDS))
if (( startup_sweep_run_seconds <= 0 )); then
  printf 'build-dist startup sweep timeout is too short for TERM/KILL grace period\n' >&2
  exit 1
fi
if build_phase_timeout_seconds_value="$(build_phase_timeout_seconds 2>/dev/null)" &&
    [[ "${build_phase_timeout_seconds_value}" =~ ^[0-9]+$ ]] &&
    (( startup_sweep_run_seconds > build_phase_timeout_seconds_value )); then
  startup_sweep_run_seconds="${build_phase_timeout_seconds_value}"
fi
if (( startup_sweep_run_seconds <= 0 )); then
  printf 'build-dist deadline exhausted before workspace sweep\n' >&2
  exit 1
fi
if ! timeout --signal=TERM --kill-after="${DIST_STARTUP_SWEEP_KILL_AFTER_SECONDS}s" \
    "${startup_sweep_run_seconds}s" \
    python3 - "${workspace_base}" "${workspace_lock}" "${safe_fs}" \
    "${startup_sweep_run_seconds}" "${DIST_STARTUP_SWEEP_MAX_ENTRIES}" \
    "${DIST_STARTUP_SWEEP_MAX_STALE}" "${DIST_WORKSPACE_MAX_AGE_SECONDS}" <<'PY'
import fcntl
import os
import re
import stat
import subprocess
import sys
import time

workspace_base, workspace_lock, safe_fs, timeout_seconds, max_entries, max_stale, max_age_seconds = sys.argv[1:]
timeout_seconds = int(timeout_seconds)
max_entries = int(max_entries)
max_stale = int(max_stale)
max_age_ns = int(max_age_seconds) * 1_000_000_000
deadline = time.monotonic() + timeout_seconds
workspace_pattern = re.compile(r"\Aspeed-of-cinnamon-build-dist-tree-[A-Za-z0-9]{6}\Z")
tombstone_pattern = re.compile(
    r"\A\.?speed-of-cinnamon-build-dist-tree-[A-Za-z0-9]{6}"
    r"(?:\.final-[0-9a-f]{32})+\Z"
)


def remaining_timeout():
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("build-dist workspace sweep deadline exceeded")
    return remaining


def identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


if workspace_lock != workspace_base:
    raise SystemExit("build-dist workspace lock path is invalid")
if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
    raise SystemExit("build-dist workspace sweep needs O_DIRECTORY and O_NOFOLLOW")
parent_fd = os.open(
    workspace_base,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
)
try:
    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o077
        or getattr(parent_stat, "st_nlink", 1) < 1
    ):
        raise SystemExit(f"build-dist workspace base is not private: {workspace_base}")
    path_stat = os.stat(workspace_base, follow_symlinks=False)
    if identity(path_stat) != identity(parent_stat):
        raise SystemExit(f"build-dist workspace base changed before lock: {workspace_base}")
    while True:
        try:
            fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except InterruptedError:
            remaining_timeout()
            continue
        except BlockingIOError:
            remaining = remaining_timeout()
            time.sleep(min(0.05, remaining))
    path_stat = os.stat(workspace_base, follow_symlinks=False)
    if identity(path_stat) != identity(parent_stat):
        raise SystemExit(f"build-dist workspace base changed after lock: {workspace_base}")

    stale = []
    scanned_entries = 0
    with os.scandir(parent_fd) as entries:
        for entry in entries:
            scanned_entries += 1
            if scanned_entries > max_entries:
                raise SystemExit(f"build-dist workspace scan exceeds max {max_entries}")
            name = os.fsdecode(entry.name)
            is_workspace = workspace_pattern.fullmatch(name) is not None
            is_tombstone = tombstone_pattern.fullmatch(name) is not None
            if not (is_workspace or is_tombstone):
                # Foreign and unknown names remain untouched.
                continue
            path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(path_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or path_stat.st_uid != os.geteuid()
                or path_stat.st_mode & 0o077
                or getattr(path_stat, "st_nlink", 1) < 1
            ):
                raise SystemExit(f"unresolved build-dist workspace: {workspace_base}/{name}")
            age_ns = time.time_ns() - path_stat.st_mtime_ns
            if age_ns < max_age_ns:
                raise SystemExit(f"recent build-dist workspace requires recovery: {workspace_base}/{name}")
            if len(stale) >= max_stale:
                raise SystemExit(f"build-dist workspace sweep exceeds max {max_stale}")
            if is_tombstone:
                tombstone_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                try:
                    with os.scandir(tombstone_fd) as children:
                        if next(children, None) is not None:
                            raise SystemExit(
                                f"preserving non-empty build-dist workspace tombstone: {workspace_base}/{name}"
                            )
                finally:
                    os.close(tombstone_fd)
            stale.append((name, identity(path_stat), is_tombstone))

    remaining_timeout()
    for name, expected_identity, is_tombstone in stale:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or current.st_uid != os.geteuid()
            or current.st_mode & 0o077
            or getattr(current, "st_nlink", 1) < 1
        ):
            raise SystemExit(f"unresolved build-dist workspace before cleanup: {workspace_base}/{name}")
        if identity(current) != expected_identity:
            raise SystemExit(f"build-dist workspace identity changed before cleanup: {workspace_base}/{name}")
        path = os.path.join(workspace_base, name)
        if is_tombstone:
            os.rmdir(name, dir_fd=parent_fd)
            os.fsync(parent_fd)
            remaining_timeout()
            continue
        try:
            subprocess.run(
                [
                    sys.executable,
                    safe_fs,
                    "remove",
                    "build-dist",
                    path,
                    "--kind",
                    "dir",
                    "--expected-identity",
                    expected_identity,
                ],
                check=True,
                timeout=remaining_timeout(),
            )
        except subprocess.CalledProcessError:
            if os.path.lexists(path):
                raise
        remaining_timeout()
finally:
    os.close(parent_fd)
PY
then
  printf 'failed to sweep build-dist workspaces safely.\n' >&2
  exit 1
fi

work_dir="$(mktemp -d "${workspace_base}/speed-of-cinnamon-build-dist-tree-XXXXXX")"
if [[ -L "${work_dir}" ]]; then
  printf 'temporary build-dist workspace must not be a symlink: %s\n' "${work_dir}" >&2
  exit 1
fi
if ! work_dir_abs="$(realpath "${work_dir}")"; then
  printf 'failed to resolve temporary build-dist workspace: %s\n' "${work_dir}" >&2
  exit 1
fi
if [[ "${work_dir_abs}" != "${workspace_base}/speed-of-cinnamon-build-dist-tree-"* ]]; then
  printf 'temporary build-dist workspace escaped workspace base: %s\n' "${work_dir}" >&2
  exit 1
fi
work_dir="${work_dir_abs}"
work_dir_owner="$(stat -c '%u' "${work_dir}")"
work_dir_mode="$(stat -c '%a' "${work_dir}")"
work_dir_mode_bits=$((8#${work_dir_mode}))
if [[ "${work_dir_owner}" != "${EUID}" ]] ||
    (( (work_dir_mode_bits & 0077) != 0 || (work_dir_mode_bits & 0200) == 0 )); then
  printf 'temporary build-dist workspace is unsafe: %s\n' "${work_dir}" >&2
  exit 1
fi
staging_tarball=""
staging_tarball_identity=""
staging_tarball_size=""
staging_tarball_digest=""
staging_checksum=""
staging_checksum_identity=""
staging_checksum_size=""
staging_checksum_digest=""
dist_staging_dir=""
dist_staging_dir_identity=""
work_dir_identity=""
dist_finalize_lock="${dist_dir}/.build-dist.finalize.lock"
finalization_started=0
cleanup() {
  local primary_status=$?
  stop_handoff_monitor
  local cleanup_failed=0
  local staging_cleanup_safe=1
  local cleanup_deadline_ns="${DIST_BUILD_DEADLINE:-}"
  local cleanup_remaining_value_ns=""
  cleanup_now_ns() {
    local now_ns
    if ! now_ns="$(timeout --signal=KILL "${DIST_BUILD_CLOCK_TIMEOUT_SECONDS:-0.25}s" \
        python3 -I -B -c 'import time; print(time.monotonic_ns())')"; then
      return 1
    fi
    if [[ ! "${now_ns}" =~ ^[0-9]+$ ]]; then
      return 1
    fi
    printf '%s' "${now_ns}"
  }
  start_cleanup_deadline() {
    local budget_seconds
    local budget_ns
    if [[ -n "${cleanup_deadline_ns}" ]]; then
      return 0
    fi
    budget_seconds="${DIST_CLEANUP_TIMEOUT_SECONDS:-30}"
    budget_ns=$((budget_seconds * 1000000000 - ${DIST_BUILD_LAUNCH_MARGIN_NS:-50000000} - 250000000))
    if (( budget_ns <= 0 )); then
      return 1
    fi
    if ! cleanup_deadline_ns="$(timeout --signal=KILL "${DIST_BUILD_CLOCK_TIMEOUT_SECONDS:-0.25}s" \
        python3 -I -B -c "import time; print(time.monotonic_ns() + ${budget_ns})")"; then
      cleanup_deadline_ns=""
      return 1
    fi
    [[ "${cleanup_deadline_ns}" =~ ^[0-9]+$ ]]
  }
  cleanup_remaining_ns() {
    local now_ns
    if ! now_ns="$(cleanup_now_ns)"; then
      return 1
    fi
    cleanup_remaining_value_ns=$((cleanup_deadline_ns - now_ns))
    if (( cleanup_remaining_value_ns <= 0 )); then
      return 1
    fi
  }
  cleanup_timeout_value() {
    local nanoseconds=$1
    printf '%d.%09d' "$((nanoseconds / 1000000000))" "$((nanoseconds % 1000000000))"
  }
  cleanup_safe_fs() {
    local available_ns
    local kill_after_ns
    local term_ns
    local kill_after_timeout
    local term_timeout
    local command_status=0
    if ! start_cleanup_deadline || ! cleanup_remaining_ns; then
      printf 'build-dist EXIT cleanup deadline exceeded before safe-FS call\n' >&2
      return 124
    fi
    available_ns=$((cleanup_remaining_value_ns - ${DIST_BUILD_LAUNCH_MARGIN_NS:-50000000}))
    kill_after_ns=$((${DIST_CLEANUP_KILL_AFTER_SECONDS:-1} * 1000000000))
    if (( available_ns <= 0 )); then
      printf 'build-dist EXIT cleanup deadline exceeded before safe-FS launch\n' >&2
      return 124
    fi
    if (( kill_after_ns >= available_ns )); then
      kill_after_ns=$((available_ns / 2))
    fi
    term_ns=$((available_ns - kill_after_ns))
    if (( term_ns <= 0 || kill_after_ns <= 0 )); then
      printf 'build-dist EXIT cleanup deadline cannot reserve TERM/KILL grace\n' >&2
      return 124
    fi
    kill_after_timeout="$(cleanup_timeout_value "${kill_after_ns}")"
    term_timeout="$(cleanup_timeout_value "${term_ns}")"
    timeout --signal=TERM --kill-after="${kill_after_timeout}s" \
      "${term_timeout}s" "${safe_fs_cmd[@]}" "$@" || command_status=$?
    if (( command_status == 124 || command_status == 137 || command_status == 143 )); then
      return 124
    fi
    if ! cleanup_remaining_ns; then
      printf 'build-dist EXIT cleanup deadline exceeded after safe-FS call\n' >&2
      return 124
    fi
    return "${command_status}"
  }
  if ! start_cleanup_deadline; then
    printf 'build-dist EXIT cleanup deadline unavailable\n' >&2
    cleanup_failed=1
  fi
  if (( ! ${finalization_started:-0} )) && [[ -n "${staging_tarball}" && -n "${staging_tarball_identity}" ]]; then
    if ! cleanup_safe_fs remove-leaf build-dist "${staging_tarball}" \
      --expected-identity "${staging_tarball_identity}"; then
      printf 'build-dist EXIT cleanup failed for staged tarball: %s\n' "${staging_tarball}" >&2
      cleanup_failed=1
      staging_cleanup_safe=0
    fi
  elif (( ! ${finalization_started:-0} )) && [[ -n "${staging_tarball}" ]]; then
    printf 'refusing staged tarball cleanup without verified identity: %s\n' "${staging_tarball}" >&2
    cleanup_failed=1
    staging_cleanup_safe=0
  fi
  if (( ! ${finalization_started:-0} )) && [[ -n "${staging_checksum}" && -n "${staging_checksum_identity}" ]]; then
    if ! cleanup_safe_fs remove-leaf build-dist "${staging_checksum}" \
      --expected-identity "${staging_checksum_identity}"; then
      printf 'build-dist EXIT cleanup failed for staged checksum: %s\n' "${staging_checksum}" >&2
      cleanup_failed=1
      staging_cleanup_safe=0
    fi
  elif (( ! ${finalization_started:-0} )) && [[ -n "${staging_checksum}" ]]; then
    printf 'refusing staged checksum cleanup without verified identity: %s\n' "${staging_checksum}" >&2
    cleanup_failed=1
    staging_cleanup_safe=0
  fi
  if (( ! ${finalization_started:-0} )) && [[ -n "${dist_staging_dir}" && -n "${dist_staging_dir_identity}" ]]; then
    if (( ! staging_cleanup_safe )); then
      printf 'preserving build-dist staging directory after leaf cleanup failure: %s\n' "${dist_staging_dir}" >&2
    elif ! cleanup_safe_fs remove build-dist "${dist_staging_dir}" --kind dir \
      --expected-identity "${dist_staging_dir_identity}"; then
      printf 'build-dist EXIT cleanup failed for staging directory: %s\n' "${dist_staging_dir}" >&2
      cleanup_failed=1
      staging_cleanup_safe=0
    fi
  elif (( ! ${finalization_started:-0} )) && [[ -n "${dist_staging_dir}" ]]; then
    printf 'refusing dist staging cleanup without verified identity: %s\n' "${dist_staging_dir}" >&2
    cleanup_failed=1
    staging_cleanup_safe=0
  fi
  if [[ -n "${work_dir}" && -n "${work_dir_identity}" ]]; then
    if (( ! ${finalization_started:-0} && ! staging_cleanup_safe )); then
      printf 'preserving build-dist workspace after staging cleanup failure: %s\n' "${work_dir}" >&2
    elif ! cleanup_safe_fs remove build-dist "${work_dir}" --kind dir \
      --expected-identity "${work_dir_identity}"; then
      printf 'build-dist EXIT cleanup failed for workspace: %s\n' "${work_dir}" >&2
      cleanup_failed=1
    fi
  elif [[ -n "${work_dir}" ]]; then
    printf 'refusing build-dist workspace cleanup without verified identity: %s\n' "${work_dir}" >&2
    cleanup_failed=1
  fi
  if (( cleanup_failed )); then
    if (( primary_status == 0 )); then
      printf 'build-dist EXIT cleanup failed; final output status changed to failure\n' >&2
      exit 1
    fi
    printf 'build-dist EXIT cleanup also failed; preserving primary exit status: %d\n' "${primary_status}" >&2
  fi
  exit "${primary_status}"
}
trap cleanup EXIT

if ! work_dir_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${work_dir}" --kind dir)"; then
  printf 'failed to capture temporary build-dist workspace identity: %s\n' "${work_dir}" >&2
  exit 1
fi

fsync_regular_file() {
  local path=$1
  local label=$2
  python3 - "$path" "$label" <<'PY'
import os
import stat
import sys

path, label = sys.argv[1:]
flags = os.O_RDONLY
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
except OSError as exc:
    print(f"failed to open {label} for fsync: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        print(f"{label} must be a regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.fsync(fd)
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-dist fsync descriptor cleanup failed")
        else:
            raise SystemExit("build-dist fsync descriptor cleanup failed") from cleanup_error
PY
}

write_regular_file_from_stdin() {
  local path=$1
  local label=$2
  local expected_identity=${3:-}

  python3 -c '
import os
import stat
import sys

path, label, expected_identity = sys.argv[1:4]
flags = os.O_WRONLY
if not expected_identity:
    flags |= os.O_CREAT
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags, 0o600)
except OSError as exc:
    print(f"failed to open {label} for writing: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        print(f"{label} must be a regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    if getattr(file_stat, "st_nlink", 1) != 1:
        print(f"{label} must not be hardlinked: {path}", file=sys.stderr)
        raise SystemExit(1)
    actual_identity = f"{file_stat.st_dev}:{file_stat.st_ino}:{file_stat.st_mode}"
    if expected_identity and actual_identity != expected_identity:
        print(f"{label} changed before writing: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.ftruncate(fd, 0)
    while True:
        chunk = sys.stdin.buffer.read(1024 * 1024)
        if not chunk:
            break
        view = memoryview(chunk)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                print(f"failed to write {label}: {path}", file=sys.stderr)
                raise SystemExit(1)
            view = view[written:]
    os.fsync(fd)
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-dist write descriptor cleanup failed")
        else:
            raise SystemExit("build-dist write descriptor cleanup failed") from cleanup_error
' "${path}" "${label}" "${expected_identity}"
}

build_staged_tarball() {
  local build_timeout_seconds=$((DIST_BUILD_TIMEOUT_SECONDS - DIST_BUILD_KILL_AFTER_SECONDS))
  if [[ -n "${DIST_BUILD_DEADLINE:-}" ]]; then
    if ! build_timeout_seconds="$(build_phase_timeout_seconds)"; then
      printf 'build-dist deadline exhausted before tar creation\n' >&2
      return 124
    fi
  fi
  if (( build_timeout_seconds <= 0 )); then
    printf 'build-dist build timeout is too short for TERM/KILL grace period\n' >&2
    return 1
  fi
  timeout --signal=TERM --kill-after="${DIST_BUILD_KILL_AFTER_SECONDS}s" \
    "${build_timeout_seconds}s" \
    python3 - "$1" "$2" "$3" "$4" "$5" "$6" "$build_timeout_seconds" "$DIST_MAX_ARCHIVE_BYTES" <<'PY'
import hashlib
import os
import selectors
import stat
import subprocess
import sys
import time

(
    work_dir,
    package,
    staging_path,
    expected_identity,
    checksum_path,
    checksum_identity,
    timeout_seconds,
    max_archive_bytes,
) = sys.argv[1:]
build_deadline = time.monotonic() + int(timeout_seconds)
max_archive_bytes = int(max_archive_bytes)
MAX_TAR_STDERR_BYTES = 4096


def remaining():
    value = build_deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("dist build deadline exceeded")
    return value


def identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


def require_regular(path_stat, label):
    if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
        raise RuntimeError(f"{label} must be a single-link regular file")


stage_fd = None
checksum_fd = None
tar_process = None
selector = None
tar_stderr = bytearray()
tar_stderr_truncated = False
try:
    flags = os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    stage_fd = os.open(staging_path, flags)
    stage_stat = os.fstat(stage_fd)
    require_regular(stage_stat, "staged dist tarball")
    if identity(stage_stat) != expected_identity:
        raise RuntimeError(f"staged dist tarball identity changed: {staging_path}")
    os.ftruncate(stage_fd, 0)

    tar_process = subprocess.Popen(
        [
            "tar",
            "--sort=name",
            "--owner=0",
            "--group=0",
            "--numeric-owner",
            "--mtime=@0",
            "-C",
            work_dir,
            "-czf",
            "-",
            package,
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if tar_process.stdout is None or tar_process.stderr is None:
        raise RuntimeError("failed to open tar output pipes")
    stdout_fd = tar_process.stdout.fileno()
    stderr_fd = tar_process.stderr.fileno()
    os.set_blocking(stdout_fd, False)
    os.set_blocking(stderr_fd, False)
    selector = selectors.DefaultSelector()
    selector.register(stdout_fd, selectors.EVENT_READ, "stdout")
    selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
    digest = hashlib.sha256()
    total_bytes = 0
    stdout_done = False
    stderr_done = False
    while not stdout_done or not stderr_done:
        events = selector.select(remaining())
        if not events:
            raise TimeoutError("dist build consumer deadline exceeded")
        for key, _ in events:
            stream = key.data
            try:
                if stream == "stdout":
                    remaining_bytes = max_archive_bytes - total_bytes
                    read_size = min(1024 * 1024, remaining_bytes) if remaining_bytes > 0 else 1
                else:
                    read_size = 64 * 1024
                chunk = os.read(key.fd, read_size)
            except (BlockingIOError, InterruptedError):
                continue
            if not chunk:
                selector.unregister(key.fd)
                if stream == "stdout":
                    stdout_done = True
                else:
                    stderr_done = True
                continue
            if stream == "stderr":
                if len(tar_stderr) < MAX_TAR_STDERR_BYTES:
                    available = MAX_TAR_STDERR_BYTES - len(tar_stderr)
                    tar_stderr.extend(chunk[:available])
                    if len(chunk) > available:
                        tar_stderr_truncated = True
                else:
                    tar_stderr_truncated = True
                continue
            if total_bytes + len(chunk) > max_archive_bytes:
                raise RuntimeError("staged dist archive exceeds maximum size")
            digest.update(chunk)
            total_bytes += len(chunk)
            view = memoryview(chunk)
            while view:
                try:
                    written = os.write(stage_fd, view)
                except InterruptedError:
                    continue
                if written <= 0:
                    raise OSError("failed to write staged dist tarball")
                view = view[written:]
                remaining()

    try:
        tar_status = tar_process.wait(timeout=remaining())
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("dist build producer deadline exceeded") from exc
    if tar_status != 0:
        stderr_preview = bytes(tar_stderr).decode("utf-8", "replace")
        if tar_stderr_truncated:
            stderr_preview += " [tar stderr truncated]"
        raise RuntimeError(f"tar failed with status {tar_status}: {stderr_preview!r}")
    final_stat = os.fstat(stage_fd)
    require_regular(final_stat, "staged dist tarball")
    if (
        identity(final_stat) != expected_identity
        or final_stat.st_size != total_bytes
        or final_stat.st_size > max_archive_bytes
    ):
        raise RuntimeError("staged dist tarball changed during build")
    os.fsync(stage_fd)
    remaining()
    archive_digest = digest.hexdigest()
    remaining()

    checksum_flags = os.O_WRONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    checksum_fd = os.open(checksum_path, checksum_flags)
    checksum_stat = os.fstat(checksum_fd)
    require_regular(checksum_stat, "staged dist checksum")
    if identity(checksum_stat) != checksum_identity:
        raise RuntimeError(f"staged dist checksum identity changed: {checksum_path}")
    checksum_data = f"{archive_digest}  {package}.tar.gz\n".encode("ascii")
    os.ftruncate(checksum_fd, 0)
    view = memoryview(checksum_data)
    while view:
        remaining()
        written = os.write(checksum_fd, view)
        if written <= 0:
            raise OSError("failed to write staged dist checksum")
        view = view[written:]
    checksum_after = os.fstat(checksum_fd)
    require_regular(checksum_after, "staged dist checksum")
    if identity(checksum_after) != checksum_identity or checksum_after.st_size != len(checksum_data):
        raise RuntimeError("staged dist checksum changed during build")
    os.fsync(checksum_fd)
    remaining()
    final_stage_stat = os.fstat(stage_fd)
    final_checksum_stat = os.fstat(checksum_fd)
    if (
        identity(final_stage_stat) != expected_identity
        or final_stage_stat.st_size != total_bytes
        or identity(final_checksum_stat) != checksum_identity
        or final_checksum_stat.st_size != len(checksum_data)
    ):
        raise RuntimeError("staged dist output changed after fsync")
    checksum_digest = hashlib.sha256(checksum_data).hexdigest()
    remaining()
    print(
        "\t".join(
            (
                identity(final_stage_stat),
                str(final_stage_stat.st_size),
                archive_digest,
                identity(final_checksum_stat),
                str(final_checksum_stat.st_size),
                checksum_digest,
            )
        )
    )
except subprocess.TimeoutExpired as exc:
    print(f"dist build deadline exceeded: {exc}", file=sys.stderr)
    raise SystemExit(124)
except TimeoutError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(124)
except BaseException as exc:
    print(f"dist build staging failed: {exc}", file=sys.stderr)
    raise SystemExit(1)
finally:
    if selector is not None:
        selector.close()
    if tar_process is not None:
        if tar_process.poll() is None:
            tar_process.terminate()
            try:
                tar_process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                tar_process.kill()
                tar_process.wait()
        if tar_process.stdout is not None:
            tar_process.stdout.close()
        if tar_process.stderr is not None:
            tar_process.stderr.close()
    if stage_fd is not None:
        os.close(stage_fd)
    if checksum_fd is not None:
        os.close(checksum_fd)
PY
}

replace_with_finalize_lock() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3
  local staging_checksum_path=$4
  local final_checksum_path=$5
  local expected_stage_identity=${6:-}
  local expected_stage_size=${7:-}
  local expected_stage_digest=${8:-}
  local expected_checksum_identity=${9:-}
  local expected_checksum_size=${10:-}
  local expected_checksum_digest=${11:-}
  local finalize_run_seconds=$((DIST_FINALIZE_TIMEOUT_SECONDS - DIST_FINALIZE_KILL_AFTER_SECONDS))
  if [[ -n "${DIST_BUILD_DEADLINE:-}" ]]; then
    if ! finalize_run_seconds="$(build_phase_timeout_seconds)"; then
      printf 'build-dist deadline exhausted before finalization\n' >&2
      return 124
    fi
  fi
  if (( finalize_run_seconds <= 0 )); then
    printf 'build-dist finalizer timeout is too short for TERM/KILL grace period\n' >&2
    return 1
  fi

  timeout --signal=TERM --kill-after="${DIST_FINALIZE_KILL_AFTER_SECONDS}s" \
    "${finalize_run_seconds}s" \
    python3 - "$lock_path" "$safe_fs" "$staging_path" "$final_path" "$staging_checksum_path" "$final_checksum_path" \
      "$expected_stage_identity" "$expected_stage_size" "$expected_stage_digest" \
      "$expected_checksum_identity" "$expected_checksum_size" "$expected_checksum_digest" \
      "$finalize_run_seconds" "$DIST_FINALIZE_LOCK_TIMEOUT_SECONDS" <<'PY'
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe finalization", file=sys.stderr)
    raise SystemExit(1)

(
    lock_path,
    safe_fs,
    staging_path,
    final_path,
    staging_checksum_path,
    final_checksum_path,
    expected_stage_identity,
    expected_stage_size,
    expected_stage_digest,
    expected_checksum_identity,
    expected_checksum_size,
    expected_checksum_digest,
    finalize_timeout,
    lock_timeout,
) = sys.argv[1:]
finalize_timeout_seconds = int(finalize_timeout)
lock_timeout_seconds = int(lock_timeout)
finalizer_deadline = time.monotonic() + finalize_timeout_seconds
lock_parent = os.path.abspath(os.path.dirname(lock_path))
lock_name = os.path.basename(lock_path)

MAX_JOURNAL_BYTES = 64 * 1024
MAX_CHECKSUM_BYTES = 4096
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_SCAN_ENTRIES = 256
MAX_TRANSACTIONS = 32
MAX_TRANSACTION_CHILDREN = 16
TRANSACTION_RE = re.compile(
    r"\A\.build-dist-transaction-[A-Za-z0-9][A-Za-z0-9.+-]*-[A-Za-z0-9]{6,64}\Z"
)
TRANSACTION_TOMBSTONE_RE = re.compile(
    r"\A\.build-dist-transaction-[A-Za-z0-9][A-Za-z0-9.+-]*-[A-Za-z0-9]{6,64}"
    r"(?:\.final-[0-9a-f]{32})+\Z"
)
TOMBSTONE_MAX_AGE_NS = 24 * 60 * 60 * 1_000_000_000
STAGE_RE = re.compile(r"\Astage\.(?:tar\.gz|tar\.gz\.sha256)(?:\.[A-Za-z0-9]{6,64})?\Z")
JOURNAL_TEMP_RE = re.compile(r"\A\.journal\.[0-9]+\.[A-Za-z0-9]{16,64}\.tmp\Z")
JOURNAL_NAME = "journal"
BACKUP_NAMES = ("backup.tar.gz", "backup.tar.gz.sha256")
PHASES = frozenset(
    {
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
        "rollback",
        "rolled-back",
        "roll-forward",
        "committed",
    }
)
abort_phase = os.environ.get("BUILD_DIST_ABORT_PHASE")


def _remaining():
    remaining = finalizer_deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("dist finalization deadline exceeded")
    return remaining


def _run_safe_fs(*arguments, suppress_output=False):
    remaining = _remaining()
    try:
        result = subprocess.run(
            [sys.executable, safe_fs, *arguments],
            check=True,
            timeout=remaining,
            stdout=subprocess.DEVNULL if suppress_output else None,
            stderr=subprocess.DEVNULL if suppress_output else None,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("dist finalization deadline exceeded during safe filesystem operation") from exc
    _remaining()
    return result


def _copy_external_stage(
    source_path,
    transaction_fd,
    target_name,
    expected_identity,
    expected_size,
    expected_digest,
    max_bytes,
):
    if expected_size > max_bytes:
        raise RuntimeError(f"external build-dist stage exceeds maximum size: {source_path}")
    source_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    target_flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    source_fd = None
    source_validation_fd = None
    target_fd = None
    try:
        source_fd = os.open(source_path, source_flags)
        source_stat = os.fstat(source_fd)
        _require_regular(source_stat, "external build-dist stage", source_path)
        if _identity(source_stat) != expected_identity:
            raise RuntimeError(f"external build-dist stage identity changed: {source_path}")
        if source_stat.st_size != expected_size or source_stat.st_size > max_bytes:
            raise RuntimeError(f"external build-dist stage size changed: {source_path}")
        source_signature = _file_signature(source_stat)
        target_fd = os.open(target_name, target_flags, 0o600, dir_fd=transaction_fd)
        digest = hashlib.sha256()
        total_bytes = 0
        while True:
            _remaining()
            remaining_bytes = expected_size - total_bytes
            read_size = min(1024 * 1024, remaining_bytes) if remaining_bytes > 0 else 1
            try:
                chunk = os.read(source_fd, read_size)
            except InterruptedError:
                continue
            if not chunk:
                break
            if total_bytes + len(chunk) > max_bytes:
                raise RuntimeError(f"external build-dist stage exceeds maximum size: {source_path}")
            digest.update(chunk)
            total_bytes += len(chunk)
            view = memoryview(chunk)
            while view:
                _remaining()
                try:
                    written = os.write(target_fd, view)
                except InterruptedError:
                    continue
                if written <= 0:
                    raise OSError(f"failed to copy external build-dist stage: {source_path}")
                view = view[written:]
        copied_digest = digest.hexdigest()
        if total_bytes != expected_size or copied_digest != expected_digest:
            raise RuntimeError(f"external build-dist stage digest changed: {source_path}")
        target_stat = os.fstat(target_fd)
        _require_regular(target_stat, "copied build-dist stage", target_name)
        if target_stat.st_size != total_bytes or target_stat.st_size > max_bytes:
            raise RuntimeError(f"copied build-dist stage size changed: {target_name}")
        os.fsync(target_fd)
        _remaining()
        source_after = os.fstat(source_fd)
        _require_regular(source_after, "external build-dist stage", source_path)
        if (
            _identity(source_after) != expected_identity
            or source_after.st_size != expected_size
            or source_after.st_size > max_bytes
            or _file_signature(source_after) != source_signature
        ):
            raise RuntimeError(f"external build-dist stage changed while copying: {source_path}")
        target_after = _lstat_at(transaction_fd, target_name)
        _require_regular(target_after, "copied build-dist stage", target_name)
        if (
            _identity(target_after) != _identity(target_stat)
            or target_after.st_size != total_bytes
            or target_after.st_size > max_bytes
        ):
            raise RuntimeError(f"copied build-dist stage identity changed: {target_name}")

        source_path_stat = _regular_path(source_path, "external build-dist stage", required=True)
        if (
            _identity(source_path_stat) != expected_identity
            or source_path_stat.st_size != expected_size
            or source_path_stat.st_size > max_bytes
            or _file_signature(source_path_stat) != source_signature
        ):
            raise RuntimeError(f"external build-dist stage path changed after copy: {source_path}")
        source_validation_fd = os.open(source_path, source_flags)
        source_validation_stat = os.fstat(source_validation_fd)
        _require_regular(source_validation_stat, "external build-dist stage", source_path)
        source_path_after_open = _regular_path(source_path, "external build-dist stage", required=True)
        if (
            _identity(source_validation_stat) != expected_identity
            or source_validation_stat.st_size != expected_size
            or source_validation_stat.st_size > max_bytes
            or _file_signature(source_validation_stat) != source_signature
            or _identity(source_path_after_open) != expected_identity
            or source_path_after_open.st_size != expected_size
            or _file_signature(source_path_after_open) != source_signature
        ):
            raise RuntimeError(f"external build-dist stage path changed after open: {source_path}")
        validation_digest, validation_size = _hash_fd(
            source_validation_fd,
            source_path,
            "external build-dist stage",
            max_bytes,
        )
        source_validation_after = os.fstat(source_validation_fd)
        _require_regular(source_validation_after, "external build-dist stage", source_path)
        source_path_after_hash = _regular_path(source_path, "external build-dist stage", required=True)
        if (
            validation_size != expected_size
            or validation_digest != expected_digest
            or _identity(source_validation_after) != expected_identity
            or source_validation_after.st_size != expected_size
            or _file_signature(source_validation_after) != source_signature
            or _identity(source_path_after_hash) != expected_identity
            or source_path_after_hash.st_size != expected_size
            or _file_signature(source_path_after_hash) != source_signature
        ):
            raise RuntimeError(f"external build-dist stage changed after validation: {source_path}")
        os.fsync(transaction_fd)
        _remaining()
        return _identity(target_after), total_bytes, copied_digest
    finally:
        primary_error = sys.exc_info()[1]
        for descriptor in (target_fd, source_validation_fd, source_fd):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except BaseException as cleanup_error:
                if primary_error is not None:
                    primary_error.add_note("build-dist external stage descriptor cleanup failed")
                else:
                    raise


def _lstat(path):
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None


def _lstat_at(directory_fd, name):
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


def _parse_identity(value, label):
    if not isinstance(value, str):
        raise RuntimeError(f"{label} identity is invalid")
    parts = value.split(":")
    if len(parts) != 3:
        raise RuntimeError(f"{label} identity is invalid")
    try:
        parsed = tuple(int(part, 10) for part in parts)
    except ValueError as exc:
        raise RuntimeError(f"{label} identity is invalid") from exc
    if any(part < 0 for part in parsed):
        raise RuntimeError(f"{label} identity is invalid")
    return value


def _parse_decimal(value, label):
    if re.fullmatch(r"(?:0|[1-9][0-9]*)", value) is None:
        raise RuntimeError(f"{label} is invalid")
    return int(value)


def _parse_digest(value, label):
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RuntimeError(f"{label} is invalid")
    return value


def _parse_expected_stage_metadata(identity_value, size_value, digest_value, label, max_size):
    identity_value = _parse_identity(identity_value, label)
    size = _parse_decimal(size_value, f"{label} size")
    if size > max_size:
        raise RuntimeError(f"{label} size exceeds maximum")
    digest = _parse_digest(digest_value, f"{label} digest")
    return identity_value, size, digest


def _file_signature(path_stat):
    return (
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
        path_stat.st_nlink,
    )


def _require_regular(path_stat, label, path):
    if path_stat is None:
        raise RuntimeError(f"{label} is missing: {path}")
    if stat.S_ISLNK(path_stat.st_mode):
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a regular file: {path}")
    if getattr(path_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{label} must not be hardlinked: {path}")
    return path_stat


def _regular_at(directory_fd, name, label, required=True):
    path_stat = _lstat_at(directory_fd, name)
    if path_stat is None and not required:
        return None
    return _require_regular(path_stat, label, name)


def _regular_path(path, label, required=True):
    path_stat = _lstat(path)
    if path_stat is None and not required:
        return None
    return _require_regular(path_stat, label, path)


def _open_regular_at(directory_fd, name, label, max_bytes, expected_identity=None):
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise RuntimeError(f"failed to open {label}: {name}") from exc
    try:
        file_stat = os.fstat(file_fd)
        _require_regular(file_stat, label, name)
        if file_stat.st_size > max_bytes:
            raise RuntimeError(f"{label} exceeds maximum size: {name}")
        if expected_identity is not None and _identity(file_stat) != expected_identity:
            raise RuntimeError(f"{label} identity changed: {name}")
        path_stat = _regular_at(directory_fd, name, label, required=True)
        if (
            _identity(file_stat) != _identity(path_stat)
            or file_stat.st_size != path_stat.st_size
            or _file_signature(file_stat) != _file_signature(path_stat)
        ):
            raise RuntimeError(f"{label} pathname changed while opening: {name}")
        return file_fd, file_stat
    except BaseException:
        os.close(file_fd)
        raise


def _revalidate_open_snapshot(
    directory_fd,
    name,
    label,
    file_fd,
    start_stat,
    max_bytes,
    expected_identity=None,
):
    end_stat = os.fstat(file_fd)
    _require_regular(end_stat, label, name)
    path_stat = _regular_at(directory_fd, name, label, required=True)
    if expected_identity is not None and _identity(end_stat) != expected_identity:
        raise RuntimeError(f"{label} identity changed after read: {name}")
    if (
        _identity(end_stat) != _identity(start_stat)
        or end_stat.st_size != start_stat.st_size
        or end_stat.st_size > max_bytes
        or _file_signature(end_stat) != _file_signature(start_stat)
    ):
        raise RuntimeError(f"{label} FD changed after read: {name}")
    if (
        _identity(path_stat) != _identity(start_stat)
        or path_stat.st_size != start_stat.st_size
        or path_stat.st_size > max_bytes
        or _file_signature(path_stat) != _file_signature(start_stat)
    ):
        raise RuntimeError(f"{label} pathname changed after read: {name}")
    return end_stat, path_stat


def _read_fd_chunk(file_fd, read_size):
    while True:
        try:
            return os.read(file_fd, read_size)
        except InterruptedError:
            _remaining()


def _hash_fd(file_fd, name, label, max_bytes):
    digest = hashlib.sha256()
    total_bytes = 0
    while True:
        _remaining()
        remaining_bytes = max_bytes - total_bytes
        read_size = min(1024 * 1024, remaining_bytes) if remaining_bytes > 0 else 1
        chunk = _read_fd_chunk(file_fd, read_size)
        if not chunk:
            break
        if total_bytes + len(chunk) > max_bytes:
            raise RuntimeError(f"{label} exceeds maximum size: {name}")
        digest.update(chunk)
        total_bytes += len(chunk)
    return digest.hexdigest(), total_bytes


def _read_fd_bounded(file_fd, name, label, max_bytes):
    chunks = []
    total_bytes = 0
    while True:
        _remaining()
        remaining_bytes = max_bytes - total_bytes
        read_size = min(1024 * 1024, remaining_bytes) if remaining_bytes > 0 else 1
        chunk = _read_fd_chunk(file_fd, read_size)
        if not chunk:
            break
        if total_bytes + len(chunk) > max_bytes:
            raise RuntimeError(f"{label} exceeds maximum size: {name}")
        chunks.append(chunk)
        total_bytes += len(chunk)
    return b"".join(chunks), total_bytes


def _validate_private_directory(path_stat, label, path, mode_mask=0o077):
    if path_stat is None or stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a directory: {path}")
    if path_stat.st_uid != os.geteuid() or path_stat.st_mode & mode_mask:
        raise RuntimeError(f"{label} must be private and owned by the current user: {path}")


TRUSTED_STICKY_SYSTEM_DIRS = frozenset({"/tmp", "/var/tmp", "/dev/shm"})


def _validate_lock_chain_directory(path_stat, path, *, final):
    if path_stat is None or stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeError(f"dist finalization lock parent ancestor is not a directory: {path}")
    if path_stat.st_uid not in {0, os.geteuid()}:
        raise RuntimeError(f"dist finalization lock parent ancestor owner is untrusted: {path}")
    sticky_system_dir = (
        path_stat.st_uid == 0
        and path in TRUSTED_STICKY_SYSTEM_DIRS
        and path_stat.st_mode & stat.S_ISVTX
        and path_stat.st_mode & 0o002
    )
    if path_stat.st_mode & 0o022 and not sticky_system_dir:
        raise RuntimeError(f"dist finalization lock parent ancestor is writable: {path}")
    if final and path_stat.st_uid != os.geteuid():
        raise RuntimeError(f"dist finalization lock parent must be owned by current user: {path}")


def _open_lock_parent_chain(path):
    if not os.path.isabs(path) or path == os.path.sep:
        raise RuntimeError(f"dist finalization lock parent path is invalid: {path}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptors = []
    paths = []
    identities = []
    try:
        current_path = os.path.sep
        current_fd = os.open(current_path, flags)
        descriptors.append(current_fd)
        paths.append(current_path)
        root_stat = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or root_stat.st_uid != 0
            or root_stat.st_mode & 0o022
        ):
            raise RuntimeError("dist finalization lock parent root ancestor is untrusted")
        identities.append(_directory_identity(root_stat))

        components = [component for component in path.split(os.path.sep) if component]
        for index, component in enumerate(components):
            next_fd = os.open(component, flags, dir_fd=current_fd)
            current_path = os.path.join(current_path, component)
            descriptors.append(next_fd)
            paths.append(current_path)
            current_fd = next_fd
            path_stat = os.fstat(current_fd)
            _validate_lock_chain_directory(
                path_stat,
                current_path,
                final=index == len(components) - 1,
            )
            identities.append(_directory_identity(path_stat))
        return descriptors, paths, identities
    except BaseException:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def _directory_identity(path_stat):
    return _identity(path_stat)


def _revalidate_lock_parent(parent_fd, expected_identity):
    for index, (descriptor, path, expected) in enumerate(
        zip(parent_chain_fds, parent_chain_paths, parent_chain_identities)
    ):
        path_stat = os.fstat(descriptor)
        if index == 0:
            if path_stat.st_uid != 0 or path_stat.st_mode & 0o022:
                raise RuntimeError(f"dist finalization lock parent root ancestor changed: {path}")
        else:
            _validate_lock_chain_directory(
                path_stat,
                path,
                final=index == len(parent_chain_fds) - 1,
            )
        if _directory_identity(path_stat) != expected:
            if index == len(parent_chain_fds) - 1:
                raise RuntimeError(f"dist finalization lock parent descriptor changed: {lock_parent}")
            raise RuntimeError(f"dist finalization lock parent ancestor descriptor changed: {path}")

    fresh_fds, fresh_paths, fresh_identities = _open_lock_parent_chain(lock_parent)
    try:
        if fresh_paths != parent_chain_paths or fresh_identities != parent_chain_identities:
            raise RuntimeError(f"dist finalization lock parent path changed: {lock_parent}")
        locked_stat = os.fstat(parent_fd)
        if _directory_identity(locked_stat) != expected_identity:
            raise RuntimeError(f"dist finalization lock parent descriptor changed: {lock_parent}")
    finally:
        for descriptor in reversed(fresh_fds):
            os.close(descriptor)


def _open_transaction(parent_fd, name, expected_identity):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        transaction_fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise RuntimeError(f"failed to open build-dist transaction: {name}") from exc
    try:
        path_stat = os.fstat(transaction_fd)
        _validate_private_directory(path_stat, "build-dist transaction", name)
        actual_identity = _directory_identity(path_stat)
        if actual_identity != expected_identity:
            raise RuntimeError(f"build-dist transaction identity changed: {name}")
        return transaction_fd
    except BaseException:
        os.close(transaction_fd)
        raise


def _new_transaction_name(final_name):
    stem = final_name[: -len(".tar.gz")]
    stem = re.sub(r"[^A-Za-z0-9.+-]", "-", stem)[:80] or "artifact"
    return f".build-dist-transaction-{stem}-{os.getpid()}-{secrets.token_hex(6)}"


def _create_transaction(parent_fd, final_name):
    for _ in range(32):
        name = _new_transaction_name(final_name)
        if _lstat_at(parent_fd, name) is not None:
            continue
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        os.fsync(parent_fd)
        _remaining()
        path_stat = _lstat_at(parent_fd, name)
        _validate_private_directory(path_stat, "new build-dist transaction", name)
        return name, _directory_identity(path_stat)
    raise RuntimeError("could not allocate build-dist transaction directory")


def _write_all(file_fd, data):
    view = memoryview(data)
    while view:
        written = os.write(file_fd, view)
        if written <= 0:
            raise OSError("short journal write")
        view = view[written:]


def _no_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate journal key: {key}")
        result[key] = value
    return result


def _read_regular_at(directory_fd, name, label, max_bytes, expected_identity=None):
    file_fd, file_stat = _open_regular_at(
        directory_fd,
        name,
        label,
        max_bytes,
        expected_identity=expected_identity,
    )
    try:
        data, total_bytes = _read_fd_bounded(file_fd, name, label, max_bytes)
        if total_bytes != file_stat.st_size:
            raise RuntimeError(f"{label} size changed while reading: {name}")
        _revalidate_open_snapshot(
            directory_fd,
            name,
            label,
            file_fd,
            file_stat,
            max_bytes,
            expected_identity=expected_identity,
        )
        return data, file_stat
    finally:
        os.close(file_fd)


def _write_journal(directory_fd, record, expected_identity):
    payload = (
        json.dumps(
            record,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        + b"\n"
    )
    if len(payload) > MAX_JOURNAL_BYTES:
        raise RuntimeError("build-dist transaction journal exceeds maximum size")

    current = _lstat_at(directory_fd, JOURNAL_NAME)
    if expected_identity is None:
        if current is not None:
            raise RuntimeError("build-dist transaction journal already exists")
    elif current is None or _identity(current) != expected_identity:
        raise RuntimeError("build-dist transaction journal identity changed")
    if current is not None:
        _require_regular(current, "build-dist transaction journal", JOURNAL_NAME)

    temporary_name = f".journal.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temporary_fd = None
    temporary_stat = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        temporary_fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
        temporary_stat = os.fstat(temporary_fd)
        _write_all(temporary_fd, payload)
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        _remaining()
        os.close(temporary_fd)
        temporary_fd = None

        current = _lstat_at(directory_fd, JOURNAL_NAME)
        if expected_identity is None:
            if current is not None:
                raise RuntimeError("build-dist transaction journal appeared during write")
        elif current is None or _identity(current) != expected_identity:
            raise RuntimeError("build-dist transaction journal changed during write")
        if current is not None:
            _require_regular(current, "build-dist transaction journal", JOURNAL_NAME)
        os.replace(
            temporary_name,
            JOURNAL_NAME,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        journal_stat = _lstat_at(directory_fd, JOURNAL_NAME)
        _require_regular(journal_stat, "build-dist transaction journal", JOURNAL_NAME)
        os.fsync(directory_fd)
        _remaining()
        return _identity(journal_stat)
    finally:
        if temporary_fd is not None:
            os.close(temporary_fd)
        residue = _lstat_at(directory_fd, temporary_name)
        if residue is not None:
            if temporary_stat is None or _identity(residue) != _identity(temporary_stat):
                raise RuntimeError(f"build-dist journal temporary identity changed: {temporary_name}")
            os.unlink(temporary_name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            _remaining()


def _persist_phase(record, transaction_fd, journal_identity, phase):
    if phase not in PHASES:
        raise RuntimeError(f"invalid build-dist transaction phase: {phase}")
    record["phase"] = phase
    journal_identity = _write_journal(transaction_fd, record, journal_identity)
    if abort_phase == phase:
        os.kill(os.getpid(), signal.SIGKILL)
    return journal_identity


def _validate_final_name(name):
    if (
        not isinstance(name, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+-]*\.tar\.gz", name)
    ):
        raise RuntimeError(f"invalid build-dist final archive name: {name}")


def _validate_journal(record, transaction_name, transaction_identity, parent_identity):
    if not isinstance(record, dict):
        raise RuntimeError("build-dist transaction journal must contain an object")
    required_keys = {
        "schema",
        "transaction",
        "transaction_identity",
        "parent_identity",
        "phase",
        "archive_sha256",
        "members",
    }
    if set(record) != required_keys or type(record.get("schema")) is not int or record["schema"] != 1:
        raise RuntimeError("build-dist transaction journal schema is invalid")
    if record["transaction"] != transaction_name:
        raise RuntimeError("build-dist transaction journal name is invalid")
    if _parse_identity(record["transaction_identity"], "transaction") != transaction_identity:
        raise RuntimeError("build-dist transaction journal identity is invalid")
    if _parse_identity(record["parent_identity"], "transaction parent") != parent_identity:
        raise RuntimeError("build-dist transaction parent identity is invalid")
    if record["phase"] not in PHASES:
        raise RuntimeError("build-dist transaction journal phase is invalid")
    if (
        not isinstance(record["archive_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", record["archive_sha256"]) is None
    ):
        raise RuntimeError("build-dist transaction archive hash is invalid")
    members = record["members"]
    if not isinstance(members, dict) or set(members) != {"archive", "checksum"}:
        raise RuntimeError("build-dist transaction journal members are invalid")

    archive_name = members["archive"].get("final_name") if isinstance(members["archive"], dict) else None
    checksum_name = members["checksum"].get("final_name") if isinstance(members["checksum"], dict) else None
    _validate_final_name(archive_name)
    if checksum_name != archive_name + ".sha256":
        raise RuntimeError("build-dist transaction checksum name is invalid")

    expected_member_keys = {
        "final_name",
        "stage_name",
        "stage_identity",
        "stage_size",
        "stage_sha256",
        "backup_name",
        "backup_identity",
        "backup_sha256",
        "final_identity",
    }
    for key, member in members.items():
        if not isinstance(member, dict) or set(member) != expected_member_keys:
            raise RuntimeError(f"build-dist transaction member is invalid: {key}")
        max_stage_size = MAX_ARCHIVE_BYTES if key == "archive" else MAX_CHECKSUM_BYTES
        stage_name = member["stage_name"]
        if (
            not isinstance(stage_name, str)
            or STAGE_RE.fullmatch(stage_name) is None
            or stage_name in {JOURNAL_NAME, *BACKUP_NAMES}
        ):
            raise RuntimeError(f"build-dist transaction stage name is invalid: {stage_name}")
        if member["backup_name"] != BACKUP_NAMES[0 if key == "archive" else 1]:
            raise RuntimeError(f"build-dist transaction backup name is invalid: {key}")
        _parse_identity(member["stage_identity"], f"{key} stage")
        if (
            not isinstance(member["stage_size"], int)
            or isinstance(member["stage_size"], bool)
            or member["stage_size"] < 0
            or member["stage_size"] > max_stage_size
            or not isinstance(member["stage_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", member["stage_sha256"]) is None
        ):
            raise RuntimeError(f"build-dist transaction stage digest is invalid: {key}")
        final_identity = member["final_identity"]
        if final_identity is not None:
            _parse_identity(final_identity, f"{key} final")
        backup_identity = member["backup_identity"]
        if backup_identity is not None:
            _parse_identity(backup_identity, f"{key} backup")
        if backup_identity != final_identity:
            raise RuntimeError(f"build-dist transaction backup state is invalid: {key}")
        backup_sha256 = member["backup_sha256"]
        if backup_sha256 is not None and re.fullmatch(r"[0-9a-f]{64}", backup_sha256) is None:
            raise RuntimeError(f"build-dist transaction backup digest is invalid: {key}")
        if (final_identity is None) != (backup_sha256 is None):
            raise RuntimeError(f"build-dist transaction backup digest state is invalid: {key}")

    if members["archive"]["stage_name"] == members["checksum"]["stage_name"]:
        raise RuntimeError("build-dist transaction stage names collide")
    return record


def _validate_children(transaction_fd, transaction_name, stage_names=None):
    children = []
    with os.scandir(transaction_fd) as entries:
        for entry in entries:
            if len(children) >= MAX_TRANSACTION_CHILDREN:
                raise RuntimeError(f"build-dist transaction exceeds max {MAX_TRANSACTION_CHILDREN} children")
            name = entry.name
            if not (
                name == JOURNAL_NAME
                or name in BACKUP_NAMES
                or (STAGE_RE.fullmatch(name) and (stage_names is None or name in stage_names))
                or JOURNAL_TEMP_RE.fullmatch(name)
            ):
                raise RuntimeError(f"foreign build-dist transaction child: {transaction_name}/{name}")
            child_stat = _lstat_at(transaction_fd, name)
            _require_regular(child_stat, "build-dist transaction child", name)
            children.append(name)
    return children


def _member_max_bytes(member):
    return MAX_CHECKSUM_BYTES if member["final_name"].endswith(".sha256") else MAX_ARCHIVE_BYTES


def _validate_record_artifacts(transaction_fd, record):
    for member in record["members"].values():
        max_bytes = _member_max_bytes(member)
        stage_stat = _regular_at(
            transaction_fd,
            member["stage_name"],
            "build-dist transaction stage",
            required=False,
        )
        if stage_stat is not None:
            if _identity(stage_stat) != member["stage_identity"]:
                raise RuntimeError(f"build-dist transaction stage identity changed: {member['stage_name']}")
            if stage_stat.st_size != member["stage_size"] or stage_stat.st_size > max_bytes:
                raise RuntimeError(f"build-dist transaction stage size changed: {member['stage_name']}")
            stage_digest = _hash_regular_at(
                transaction_fd,
                member["stage_name"],
                "build-dist transaction stage",
                max_bytes,
                expected_identity=member["stage_identity"],
            )
            if stage_digest != member["stage_sha256"]:
                raise RuntimeError(f"build-dist transaction stage digest changed: {member['stage_name']}")
        backup_stat = _regular_at(
            transaction_fd,
            member["backup_name"],
            "build-dist transaction backup",
            required=False,
        )
        if member["backup_identity"] is None:
            if backup_stat is not None:
                raise RuntimeError(f"unexpected build-dist transaction backup: {member['backup_name']}")
        elif backup_stat is not None:
            if _identity(backup_stat) != member["backup_identity"]:
                raise RuntimeError(f"build-dist transaction backup identity changed: {member['backup_name']}")
            backup_digest = _hash_regular_at(
                transaction_fd,
                member["backup_name"],
                "build-dist transaction backup",
                max_bytes,
                expected_identity=member["backup_identity"],
            )
            if backup_digest != member["backup_sha256"]:
                raise RuntimeError(f"build-dist transaction backup digest changed: {member['backup_name']}")


def _read_journal(transaction_fd, transaction_name, transaction_identity, parent_identity):
    journal_data, journal_stat = _read_regular_at(
        transaction_fd,
        JOURNAL_NAME,
        "build-dist transaction journal",
        MAX_JOURNAL_BYTES,
    )
    try:
        record = json.loads(
            journal_data.decode("ascii"),
            object_pairs_hook=_no_duplicate_pairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError, MemoryError) as exc:
        raise RuntimeError("build-dist transaction journal is invalid") from exc
    _validate_journal(record, transaction_name, transaction_identity, parent_identity)
    return record, _identity(journal_stat)


def _hash_regular_at(directory_fd, name, label, max_bytes, expected_identity=None):
    file_fd, file_stat = _open_regular_at(
        directory_fd,
        name,
        label,
        max_bytes,
        expected_identity=expected_identity,
    )
    try:
        digest, total_bytes = _hash_fd(file_fd, name, label, max_bytes)
        if total_bytes != file_stat.st_size:
            raise RuntimeError(f"{label} size changed while hashing: {name}")
        _revalidate_open_snapshot(
            directory_fd,
            name,
            label,
            file_fd,
            file_stat,
            max_bytes,
            expected_identity=expected_identity,
        )
        return digest
    finally:
        os.close(file_fd)


def _validate_pair_at(
    directory_fd,
    archive_name,
    checksum_name,
    expected_digest=None,
    expected_archive_name=None,
    expected_checksum_digest=None,
):
    archive_fd = None
    checksum_fd = None
    try:
        archive_fd, archive_stat = _open_regular_at(
            directory_fd,
            archive_name,
            "build-dist archive",
            MAX_ARCHIVE_BYTES,
        )
        checksum_fd, checksum_stat = _open_regular_at(
            directory_fd,
            checksum_name,
            "build-dist checksum",
            MAX_CHECKSUM_BYTES,
        )
        digest, archive_size = _hash_fd(
            archive_fd,
            archive_name,
            "build-dist archive",
            MAX_ARCHIVE_BYTES,
        )
        checksum_data, checksum_size = _read_fd_bounded(
            checksum_fd,
            checksum_name,
            "build-dist checksum",
            MAX_CHECKSUM_BYTES,
        )
        if archive_size != archive_stat.st_size:
            raise RuntimeError(f"build-dist archive size changed while hashing: {archive_name}")
        if checksum_size != checksum_stat.st_size:
            raise RuntimeError(f"build-dist checksum size changed while reading: {checksum_name}")
        try:
            checksum_text = checksum_data.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError("build-dist checksum is not ASCII") from exc
        checksum_archive_name = archive_name if expected_archive_name is None else expected_archive_name
        expected_line = f"{digest}  {checksum_archive_name}\n"
        if checksum_text != expected_line:
            raise RuntimeError(f"build-dist checksum does not match {archive_name}")
        if expected_digest is not None and digest != expected_digest:
            raise RuntimeError(f"build-dist archive hash changed: {archive_name}")
        checksum_digest = hashlib.sha256(checksum_data).hexdigest()
        if expected_checksum_digest is not None and checksum_digest != expected_checksum_digest:
            raise RuntimeError(f"build-dist checksum digest changed: {checksum_name}")
        _revalidate_open_snapshot(
            directory_fd,
            archive_name,
            "build-dist archive",
            archive_fd,
            archive_stat,
            MAX_ARCHIVE_BYTES,
        )
        _revalidate_open_snapshot(
            directory_fd,
            checksum_name,
            "build-dist checksum",
            checksum_fd,
            checksum_stat,
            MAX_CHECKSUM_BYTES,
        )
        return digest
    finally:
        for descriptor in (checksum_fd, archive_fd):
            if descriptor is not None:
                os.close(descriptor)


def _final_current(parent_fd, name, label):
    path_stat = _lstat_at(parent_fd, name)
    if path_stat is None:
        return None
    return _require_regular(path_stat, label, name)


def _member_final_state(parent_fd, member):
    current = _final_current(parent_fd, member["final_name"], "build-dist final output")
    if current is None:
        return None
    return _identity(current)


def _all_final_new(parent_fd, record):
    return all(
        _member_final_state(parent_fd, member) == member["stage_identity"]
        for member in record["members"].values()
    )


def _rollback(parent_fd, transaction_fd, transaction_name, record):
    _validate_record_artifacts(transaction_fd, record)
    for member in reversed(tuple(record["members"].values())):
        max_bytes = _member_max_bytes(member)
        final_current = _final_current(parent_fd, member["final_name"], "build-dist final output")
        stage_current = _regular_at(
            transaction_fd,
            member["stage_name"],
            "build-dist transaction stage",
            required=False,
        )
        backup_current = _regular_at(
            transaction_fd,
            member["backup_name"],
            "build-dist transaction backup",
            required=False,
        )
        if stage_current is not None and _identity(stage_current) != member["stage_identity"]:
            raise RuntimeError(f"build-dist transaction stage identity changed: {member['stage_name']}")
        if backup_current is not None and _identity(backup_current) != member["backup_identity"]:
            raise RuntimeError(f"build-dist transaction backup identity changed: {member['backup_name']}")

        final_identity = _identity(final_current) if final_current is not None else None
        if member["final_identity"] is not None:
            if backup_current is not None:
                backup_digest = _hash_regular_at(
                    transaction_fd,
                    member["backup_name"],
                    "build-dist transaction backup",
                    max_bytes,
                    expected_identity=member["backup_identity"],
                )
                if backup_digest != member["backup_sha256"]:
                    raise RuntimeError(f"build-dist backup digest changed: {member['backup_name']}")
                if final_identity is None:
                    destination_guard = ("--expected-dst-identity", "missing")
                elif final_identity == member["stage_identity"]:
                    destination_guard = ("--expected-dst-identity", member["stage_identity"])
                elif final_identity == member["final_identity"]:
                    raise RuntimeError(
                        f"build-dist old final and backup both exist: {member['final_name']}"
                    )
                else:
                    raise RuntimeError(f"build-dist final identity is unexpected: {member['final_name']}")
                _run_safe_fs(
                    "replace",
                    "build-dist rollback",
                    os.path.join(lock_parent, transaction_name, member["backup_name"]),
                    os.path.join(lock_parent, member["final_name"]),
                    "--src-kind",
                    "file",
                    "--expected-src-identity",
                    member["backup_identity"],
                    *destination_guard,
                )
            elif final_identity != member["final_identity"]:
                raise RuntimeError(f"build-dist original final is unavailable: {member['final_name']}")
            else:
                final_digest = _hash_regular_at(
                    parent_fd,
                    member["final_name"],
                    "build-dist original final",
                    max_bytes,
                    expected_identity=member["final_identity"],
                )
                if final_digest != member["backup_sha256"]:
                    raise RuntimeError(f"build-dist original final digest changed: {member['final_name']}")
        elif final_identity is not None:
            if final_identity != member["stage_identity"]:
                raise RuntimeError(f"build-dist first-publish final is unexpected: {member['final_name']}")
            final_digest = _hash_regular_at(
                parent_fd,
                member["final_name"],
                "build-dist first-publish final",
                max_bytes,
                expected_identity=member["stage_identity"],
            )
            if final_digest != member["stage_sha256"]:
                raise RuntimeError(f"build-dist first-publish final digest changed: {member['final_name']}")
            _run_safe_fs(
                "remove-leaf",
                "build-dist rollback",
                os.path.join(lock_parent, member["final_name"]),
                "--expected-identity",
                member["stage_identity"],
            )

        restored = _final_current(parent_fd, member["final_name"], "build-dist final output")
        expected_final = member["final_identity"]
        if (_identity(restored) if restored is not None else None) != expected_final:
            raise RuntimeError(f"build-dist rollback did not restore final: {member['final_name']}")
        if restored is not None:
            restored_digest = _hash_regular_at(
                parent_fd,
                member["final_name"],
                "build-dist restored final",
                max_bytes,
                expected_identity=expected_final,
            )
            if restored_digest != member["backup_sha256"]:
                raise RuntimeError(f"build-dist restored final digest changed: {member['final_name']}")

        remaining_backup = _regular_at(
            transaction_fd,
            member["backup_name"],
            "build-dist transaction backup",
            required=False,
        )
        if remaining_backup is not None:
            if _identity(remaining_backup) != member["backup_identity"]:
                raise RuntimeError(f"build-dist transaction backup changed: {member['backup_name']}")
            raise RuntimeError(f"build-dist rollback backup remains: {member['backup_name']}")


def _verified_recovery_path(path, expected_identity):
    current = _lstat(path)
    if current is None or _identity(current) != expected_identity:
        return None
    return path


def _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd=None, record=None):
    if transaction_fd is not None:
        stage_names = None
        if record is not None:
            stage_names = {
                member["stage_name"] for member in record["members"].values()
            }
            _validate_record_artifacts(transaction_fd, record)
        _validate_children(transaction_fd, transaction_name, stage_names)
    current = _lstat_at(parent_fd, transaction_name)
    if current is None or _identity(current) != transaction_identity:
        raise RuntimeError(f"build-dist transaction identity changed during cleanup: {transaction_name}")
    try:
        cleanup_command = "remove" if record is not None else "rmdir"
        cleanup_arguments = [
            cleanup_command,
            "build-dist transaction cleanup",
            os.path.join(lock_parent, transaction_name),
        ]
        if record is not None:
            cleanup_arguments.extend(("--kind", "dir"))
        cleanup_arguments.extend(("--expected-identity", transaction_identity))
        _run_safe_fs(*cleanup_arguments, suppress_output=True)
    except BaseException:
        recovery_path = _verified_recovery_path(
            os.path.join(lock_parent, transaction_name),
            transaction_identity,
        )
        if recovery_path is not None:
            print(
                "warning: active dist outputs preserved; "
                f"identity-verified recovery backup path: {recovery_path}",
                file=sys.stderr,
            )
        else:
            print(
                "warning: active dist outputs preserved; "
                "no identity-verified recovery backup available",
                file=sys.stderr,
            )
        raise


def _recover_record(
    parent_fd,
    transaction_fd,
    transaction_name,
    transaction_identity,
    parent_identity,
    record,
    journal_identity,
):
    phase = record["phase"]
    _validate_record_artifacts(transaction_fd, record)
    if phase == "committed":
        if not _all_final_new(parent_fd, record):
            raise RuntimeError(f"committed build-dist transaction is not active: {transaction_name}")
        _validate_pair_at(
            parent_fd,
            record["members"]["archive"]["final_name"],
            record["members"]["checksum"]["final_name"],
            record["archive_sha256"],
            expected_checksum_digest=record["members"]["checksum"]["stage_sha256"],
        )
        _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        return
    if phase == "rolled-back":
        _rollback(parent_fd, transaction_fd, transaction_name, record)
        _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        return
    if phase == "roll-forward":
        if not _all_final_new(parent_fd, record):
            raise RuntimeError(f"roll-forward build-dist transaction is incomplete: {transaction_name}")
        _validate_pair_at(
            parent_fd,
            record["members"]["archive"]["final_name"],
            record["members"]["checksum"]["final_name"],
            record["archive_sha256"],
            expected_checksum_digest=record["members"]["checksum"]["stage_sha256"],
        )
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "committed")
        _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        return

    if phase == "rollback":
        _rollback(parent_fd, transaction_fd, transaction_name, record)
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "rolled-back")
        _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        return

    if _all_final_new(parent_fd, record):
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "roll-forward")
        _validate_pair_at(
            parent_fd,
            record["members"]["archive"]["final_name"],
            record["members"]["checksum"]["final_name"],
            record["archive_sha256"],
            expected_checksum_digest=record["members"]["checksum"]["stage_sha256"],
        )
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "committed")
        _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        return

    journal_identity = _persist_phase(record, transaction_fd, journal_identity, "rollback")
    _rollback(parent_fd, transaction_fd, transaction_name, record)
    journal_identity = _persist_phase(record, transaction_fd, journal_identity, "rolled-back")
    _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)


def _recover_transactions(parent_fd, parent_identity, skip_names):
    transaction_names = []
    tombstone_names = []
    scanned_entries = 0
    with os.scandir(parent_fd) as entries:
        for entry in entries:
            scanned_entries += 1
            if scanned_entries > MAX_SCAN_ENTRIES:
                raise RuntimeError(f"build-dist transaction scan exceeds max {MAX_SCAN_ENTRIES}")
            is_transaction = TRANSACTION_RE.fullmatch(entry.name) is not None
            is_tombstone = TRANSACTION_TOMBSTONE_RE.fullmatch(entry.name) is not None
            if not (is_transaction or is_tombstone):
                continue
            if len(transaction_names) + len(tombstone_names) >= MAX_TRANSACTIONS:
                raise RuntimeError(f"build-dist transaction sweep exceeds max {MAX_TRANSACTIONS}")
            path_stat = _lstat_at(parent_fd, entry.name)
            if path_stat is None:
                raise RuntimeError(f"build-dist transaction disappeared: {entry.name}")
            if is_tombstone:
                _validate_private_directory(path_stat, "build-dist transaction tombstone", entry.name)
                if time.time_ns() - path_stat.st_mtime_ns < TOMBSTONE_MAX_AGE_NS:
                    raise RuntimeError(
                        f"recent build-dist transaction tombstone requires recovery: {entry.name}"
                    )
                tombstone_names.append((entry.name, _directory_identity(path_stat)))
            else:
                _validate_private_directory(path_stat, "build-dist transaction", entry.name)
                transaction_names.append((entry.name, _directory_identity(path_stat)))

    for tombstone_name, tombstone_identity in sorted(tombstone_names):
        tombstone_fd = os.open(
            tombstone_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        try:
            tombstone_stat = os.fstat(tombstone_fd)
            _validate_private_directory(
                tombstone_stat,
                "build-dist transaction tombstone",
                tombstone_name,
            )
            if _directory_identity(tombstone_stat) != tombstone_identity:
                raise RuntimeError(
                    f"build-dist transaction tombstone identity changed: {tombstone_name}"
                )
            with os.scandir(tombstone_fd) as children:
                if next(children, None) is not None:
                    raise RuntimeError(
                        f"preserving non-empty build-dist transaction tombstone: {tombstone_name}"
                    )
            current = _lstat_at(parent_fd, tombstone_name)
            if current is None:
                raise RuntimeError(
                    f"build-dist transaction tombstone disappeared before cleanup: {tombstone_name}"
                )
            _validate_private_directory(
                current,
                "build-dist transaction tombstone",
                tombstone_name,
            )
            if _directory_identity(current) != tombstone_identity:
                raise RuntimeError(
                    f"build-dist transaction tombstone identity changed before cleanup: {tombstone_name}"
                )
            os.rmdir(tombstone_name, dir_fd=parent_fd)
            if _lstat_at(parent_fd, tombstone_name) is not None:
                raise RuntimeError(
                    f"build-dist transaction tombstone remains after cleanup: {tombstone_name}"
                )
            os.fsync(parent_fd)
            _remaining()
        finally:
            os.close(tombstone_fd)

    for transaction_name, transaction_identity in sorted(transaction_names):
        if transaction_name in skip_names:
            continue
        transaction_fd = _open_transaction(parent_fd, transaction_name, transaction_identity)
        try:
            children = _validate_children(transaction_fd, transaction_name)
            journal_stat = _lstat_at(transaction_fd, JOURNAL_NAME)
            if journal_stat is None:
                if children:
                    raise RuntimeError(
                        f"build-dist transaction without journal is not empty: {transaction_name}"
                    )
                _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd)
                continue
            _require_regular(journal_stat, "build-dist transaction journal", JOURNAL_NAME)
            record, journal_identity = _read_journal(
                transaction_fd,
                transaction_name,
                transaction_identity,
                parent_identity,
            )
            _validate_children(
                transaction_fd,
                transaction_name,
                {member["stage_name"] for member in record["members"].values()},
            )
            _recover_record(
                parent_fd,
                transaction_fd,
                transaction_name,
                transaction_identity,
                parent_identity,
                record,
                journal_identity,
            )
        finally:
            os.close(transaction_fd)


def _validate_final_paths(parent_fd, parent_identity, archive_path, checksum_path):
    archive_path = os.path.abspath(archive_path)
    checksum_path = os.path.abspath(checksum_path)
    if os.path.dirname(archive_path) != lock_parent or checksum_path != archive_path + ".sha256":
        raise RuntimeError("build-dist final output paths escaped dist directory")
    archive_name = os.path.basename(archive_path)
    _validate_final_name(archive_name)
    final_stat = _regular_at(parent_fd, archive_name, "existing build-dist final", required=False)
    checksum_stat = _regular_at(
        parent_fd,
        archive_name + ".sha256",
        "existing build-dist checksum",
        required=False,
    )
    return archive_name, final_stat, checksum_stat


if not lock_name:
    print(f"finalization lock path is invalid: {lock_path}", file=sys.stderr)
    raise SystemExit(1)
if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
    raise RuntimeError("build-dist finalization needs O_DIRECTORY and O_NOFOLLOW")

parent_chain_fds = []
parent_chain_paths = []
parent_chain_identities = []
try:
    parent_chain_fds, parent_chain_paths, parent_chain_identities = _open_lock_parent_chain(lock_parent)
except (OSError, RuntimeError) as exc:
    print(f"failed to open finalization lock parent safely: {lock_parent}: {exc}", file=sys.stderr)
    raise SystemExit(1)
parent_fd = parent_chain_fds[-1]
parent_identity = parent_chain_identities[-1]

try:
    _revalidate_lock_parent(parent_fd, parent_identity)

    lock_deadline = min(finalizer_deadline, time.monotonic() + lock_timeout_seconds)
    while True:
        try:
            fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except InterruptedError:
            now = time.monotonic()
            if now >= finalizer_deadline:
                raise TimeoutError("dist finalization deadline exceeded while waiting for lock")
            if now >= lock_deadline:
                raise RuntimeError("dist finalization lock timed out")
            continue
        except BlockingIOError:
            remaining = lock_deadline - time.monotonic()
            if remaining <= 0:
                if time.monotonic() >= finalizer_deadline:
                    raise RuntimeError("dist finalization deadline exceeded while waiting for lock")
                raise RuntimeError("dist finalization lock timed out")
            try:
                time.sleep(min(0.05, remaining))
            except InterruptedError:
                now = time.monotonic()
                if now >= finalizer_deadline:
                    raise RuntimeError("dist finalization deadline exceeded while waiting for lock")
                if now >= lock_deadline:
                    raise RuntimeError("dist finalization lock timed out")
                continue
            if time.monotonic() >= finalizer_deadline:
                raise RuntimeError("dist finalization deadline exceeded while waiting for lock")
            if time.monotonic() >= lock_deadline:
                raise RuntimeError("dist finalization lock timed out")
    _revalidate_lock_parent(parent_fd, parent_identity)

    expected_stage_metadata = None
    if staging_path:
        expected_stage_metadata = (
            _parse_expected_stage_metadata(
                expected_stage_identity,
                expected_stage_size,
                expected_stage_digest,
                "build-dist archive stage",
                MAX_ARCHIVE_BYTES,
            ),
            _parse_expected_stage_metadata(
                expected_checksum_identity,
                expected_checksum_size,
                expected_checksum_digest,
                "build-dist checksum stage",
                MAX_CHECKSUM_BYTES,
            ),
        )

    prospective_skip_names = set()
    if staging_path:
        prospective_stage_parent = os.path.abspath(os.path.dirname(staging_path))
        prospective_name = os.path.basename(prospective_stage_parent)
        if os.path.dirname(prospective_stage_parent) == lock_parent and TRANSACTION_RE.fullmatch(prospective_name):
            prospective_skip_names.add(prospective_name)
    _recover_transactions(parent_fd, parent_identity, prospective_skip_names)
    os.fsync(parent_fd)
    _remaining()

    recovery_only = not staging_path and not final_path and not staging_checksum_path and not final_checksum_path
    if recovery_only:
        raise SystemExit(0)
    if bool(staging_path) != bool(final_path) or bool(staging_checksum_path) != bool(final_checksum_path):
        raise RuntimeError("incomplete build-dist finalization pair")
    if not staging_path:
        raise RuntimeError("build-dist finalization paths are incomplete")

    archive_name, final_stat, checksum_stat = _validate_final_paths(
        parent_fd,
        parent_identity,
        final_path,
        final_checksum_path,
    )
    existing_archive_digest = None
    if final_stat is not None and checksum_stat is not None:
        existing_archive_digest = _validate_pair_at(
            parent_fd,
            archive_name,
            archive_name + ".sha256",
        )
    if os.path.abspath(staging_path) in {
        os.path.abspath(final_path),
        os.path.abspath(final_checksum_path),
    } or os.path.abspath(staging_checksum_path) in {
        os.path.abspath(final_path),
        os.path.abspath(final_checksum_path),
    }:
        raise RuntimeError("build-dist stage and final paths collide")

    stage_parent = os.path.abspath(os.path.dirname(staging_path))
    checksum_stage_parent = os.path.abspath(os.path.dirname(staging_checksum_path))
    transaction_name = None
    transaction_identity = None
    transaction_fd = None
    record = None
    journal_identity = None
    claimed_stage = {}
    try:
        if (
            stage_parent == checksum_stage_parent
            and os.path.dirname(stage_parent) == lock_parent
            and TRANSACTION_RE.fullmatch(os.path.basename(stage_parent))
        ):
            transaction_name = os.path.basename(stage_parent)
            transaction_stat = _lstat_at(parent_fd, transaction_name)
            if transaction_stat is None:
                raise RuntimeError(f"build-dist transaction disappeared: {transaction_name}")
            _validate_private_directory(transaction_stat, "build-dist transaction", transaction_name)
            transaction_identity = _directory_identity(transaction_stat)
            transaction_fd = _open_transaction(parent_fd, transaction_name, transaction_identity)
            existing_journal = _lstat_at(transaction_fd, JOURNAL_NAME)
            if existing_journal is not None:
                raise RuntimeError(f"build-dist transaction journal already exists: {transaction_name}")
        else:
            transaction_name, transaction_identity = _create_transaction(parent_fd, archive_name)
            transaction_fd = _open_transaction(parent_fd, transaction_name, transaction_identity)
            for source_path, target_name, metadata, max_bytes in (
                (staging_path, "stage.tar.gz", expected_stage_metadata[0], MAX_ARCHIVE_BYTES),
                (staging_checksum_path, "stage.tar.gz.sha256", expected_stage_metadata[1], MAX_CHECKSUM_BYTES),
            ):
                source_stat = _regular_path(source_path, "external build-dist stage", required=True)
                target_stat = _lstat_at(transaction_fd, target_name)
                if target_stat is not None:
                    raise RuntimeError(f"build-dist transaction stage already exists: {target_name}")
                claimed_stage[target_name] = _copy_external_stage(
                    source_path,
                    transaction_fd,
                    target_name,
                    metadata[0],
                    metadata[1],
                    metadata[2],
                    max_bytes,
                )
            staging_path = os.path.join(lock_parent, transaction_name, "stage.tar.gz")
            staging_checksum_path = os.path.join(lock_parent, transaction_name, "stage.tar.gz.sha256")
        _validate_children(transaction_fd, transaction_name)

        stage_archive_name = os.path.basename(staging_path)
        stage_checksum_name = os.path.basename(staging_checksum_path)
        if (
            os.path.dirname(os.path.abspath(staging_path)) != os.path.join(lock_parent, transaction_name)
            or os.path.dirname(os.path.abspath(staging_checksum_path)) != os.path.join(lock_parent, transaction_name)
            or STAGE_RE.fullmatch(stage_archive_name) is None
            or STAGE_RE.fullmatch(stage_checksum_name) is None
            or stage_archive_name == stage_checksum_name
        ):
            raise RuntimeError("build-dist transaction stage layout is invalid")
        stage_archive_stat = _regular_at(transaction_fd, stage_archive_name, "build-dist archive stage")
        stage_checksum_stat = _regular_at(transaction_fd, stage_checksum_name, "build-dist checksum stage")
        archive_digest = _validate_pair_at(
            transaction_fd,
            stage_archive_name,
            stage_checksum_name,
            expected_digest=None,
            expected_archive_name=archive_name,
            expected_checksum_digest=expected_stage_metadata[1][2],
        )
        checksum_digest = _hash_regular_at(
            transaction_fd,
            stage_checksum_name,
            "build-dist checksum stage",
            MAX_CHECKSUM_BYTES,
            expected_identity=_identity(stage_checksum_stat),
        )
        for stage_name, stage_stat, stage_digest in (
            (stage_archive_name, stage_archive_stat, archive_digest),
            (stage_checksum_name, stage_checksum_stat, checksum_digest),
        ):
            expected = expected_stage_metadata[0 if stage_name == stage_archive_name else 1]
            if stage_stat.st_size != expected[1] or stage_digest != expected[2]:
                raise RuntimeError(f"build-dist stage metadata changed: {stage_name}")
            claimed = claimed_stage.get(stage_name)
            if claimed is None and _identity(stage_stat) != expected[0]:
                raise RuntimeError(f"build-dist stage metadata changed: {stage_name}")
            if claimed is not None and claimed != (
                _identity(stage_stat),
                stage_stat.st_size,
                stage_digest,
            ):
                raise RuntimeError(f"external build-dist stage changed after copy: {stage_name}")

        original_archive_digest = (
            existing_archive_digest
            if existing_archive_digest is not None
            else _hash_regular_at(
                parent_fd,
                archive_name,
                "existing build-dist final",
                MAX_ARCHIVE_BYTES,
                expected_identity=_identity(final_stat),
            )
            if final_stat is not None
            else None
        )
        original_checksum_digest = (
            _hash_regular_at(
                parent_fd,
                archive_name + ".sha256",
                "existing build-dist checksum",
                MAX_CHECKSUM_BYTES,
                expected_identity=_identity(checksum_stat),
            )
            if checksum_stat is not None
            else None
        )

        final_archive_name = archive_name
        final_checksum_name = archive_name + ".sha256"
        record = {
            "schema": 1,
            "transaction": transaction_name,
            "transaction_identity": transaction_identity,
            "parent_identity": parent_identity,
            "phase": "prepared",
            "archive_sha256": archive_digest,
            "members": {
                "archive": {
                    "final_name": final_archive_name,
                    "stage_name": stage_archive_name,
                    "stage_identity": _identity(stage_archive_stat),
                    "stage_size": stage_archive_stat.st_size,
                    "stage_sha256": archive_digest,
                    "backup_name": BACKUP_NAMES[0],
                    "backup_identity": _identity(final_stat) if final_stat is not None else None,
                    "backup_sha256": original_archive_digest,
                    "final_identity": _identity(final_stat) if final_stat is not None else None,
                },
                "checksum": {
                    "final_name": final_checksum_name,
                    "stage_name": stage_checksum_name,
                    "stage_identity": _identity(stage_checksum_stat),
                    "stage_size": stage_checksum_stat.st_size,
                    "stage_sha256": checksum_digest,
                    "backup_name": BACKUP_NAMES[1],
                    "backup_identity": _identity(checksum_stat) if checksum_stat is not None else None,
                    "backup_sha256": original_checksum_digest,
                    "final_identity": _identity(checksum_stat) if checksum_stat is not None else None,
                },
            },
        }
        journal_identity = _persist_phase(record, transaction_fd, None, "prepared")

        archive_member = record["members"]["archive"]
        checksum_member = record["members"]["checksum"]
        for member, phase, complete_phase in (
            (archive_member, "backup-archive", "backup-archive-complete"),
            (checksum_member, "backup-checksum", "backup-checksum-complete"),
        ):
            journal_identity = _persist_phase(record, transaction_fd, journal_identity, phase)
            if member["final_identity"] is not None:
                _run_safe_fs(
                    "replace",
                    "build-dist backup",
                    os.path.join(lock_parent, member["final_name"]),
                    os.path.join(lock_parent, transaction_name, member["backup_name"]),
                    "--src-kind",
                    "file",
                    "--dst-must-not-exist",
                    "--expected-src-identity",
                    member["final_identity"],
                )
                backup_stat = _regular_at(
                    transaction_fd,
                    member["backup_name"],
                    "build-dist transaction backup",
                )
                if _identity(backup_stat) != member["backup_identity"]:
                    raise RuntimeError(f"build-dist backup identity changed: {member['backup_name']}")
                backup_digest = _hash_regular_at(
                    transaction_fd,
                    member["backup_name"],
                    "build-dist transaction backup",
                    _member_max_bytes(member),
                    expected_identity=member["backup_identity"],
                )
                if backup_digest != member["backup_sha256"]:
                    raise RuntimeError(f"build-dist backup digest changed: {member['backup_name']}")
                if _lstat_at(parent_fd, member["final_name"]) is not None:
                    raise RuntimeError(f"build-dist final remained after backup: {member['final_name']}")
            journal_identity = _persist_phase(record, transaction_fd, journal_identity, complete_phase)
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "backups-complete")

        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "activate-archive")
        _run_safe_fs(
            "replace",
            "build-dist activate archive",
            os.path.join(lock_parent, transaction_name, archive_member["stage_name"]),
            os.path.join(lock_parent, archive_member["final_name"]),
            "--src-kind",
            "file",
            "--expected-src-identity",
            archive_member["stage_identity"],
            "--expected-dst-identity",
            "missing",
        )
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "archive-activated")

        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "activate-checksum")
        _run_safe_fs(
            "replace",
            "build-dist activate checksum",
            os.path.join(lock_parent, transaction_name, checksum_member["stage_name"]),
            os.path.join(lock_parent, checksum_member["final_name"]),
            "--src-kind",
            "file",
            "--expected-src-identity",
            checksum_member["stage_identity"],
            "--expected-dst-identity",
            "missing",
        )
        _validate_pair_at(
            parent_fd,
            archive_member["final_name"],
            checksum_member["final_name"],
            record["archive_sha256"],
            expected_checksum_digest=checksum_member["stage_sha256"],
        )
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "pair-activated")
        journal_identity = _persist_phase(record, transaction_fd, journal_identity, "committed")
    except BaseException as exc:
        if record is None or transaction_fd is None:
            raise
        try:
            journal_identity = _persist_phase(record, transaction_fd, journal_identity, "rollback")
        except BaseException as phase_error:
            exc.add_note(f"build-dist rollback journal update failed: {phase_error}")
        try:
            _rollback(parent_fd, transaction_fd, transaction_name, record)
        except BaseException as rollback_error:
            exc.add_note(f"build-dist rollback failed: {rollback_error}")
            raise
        try:
            _persist_phase(record, transaction_fd, journal_identity, "rolled-back")
        except BaseException as phase_error:
            exc.add_note(f"build-dist rollback completion journal update failed: {phase_error}")
        try:
            _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        except BaseException as cleanup_error:
            exc.add_note(f"build-dist rollback cleanup failed: {cleanup_error}")
        raise
    else:
        try:
            _cleanup_transaction(parent_fd, transaction_name, transaction_identity, transaction_fd, record)
        except BaseException:
            # Committed pair remains active; journal and transaction are recovery residue.
            pass
        _remaining()
    finally:
        if transaction_fd is not None:
            os.close(transaction_fd)
finally:
    primary_error = sys.exc_info()[1]
    for descriptor in reversed(parent_chain_fds):
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note("build-dist finalization descriptor cleanup failed")
            else:
                raise SystemExit("build-dist finalization descriptor cleanup failed") from cleanup_error
PY
}

run_build_command "${safe_fs_cmd[@]}" assert-private-chain build-dist "${dist_dir}" --allow-missing
run_build_command "${safe_fs_cmd[@]}" mkdirs build-dist "${dist_dir}"
run_build_command "${safe_fs_cmd[@]}" assert-private-chain build-dist "${dist_dir}"
if ! replace_with_finalize_lock "${dist_finalize_lock}" "" "" "" ""; then
  printf 'build-dist startup recovery failed\n' >&2
  exit 1
fi
run_build_command "${safe_fs_cmd[@]}" mkdirs build-dist "${work_dir}/${package}"

for path in \
  .github \
  docs \
  files \
  packaging \
  snap \
  scripts \
  src \
  tests \
  LICENSE \
  Makefile \
  pyproject.toml \
  README.md
do
  source_path="${repo_dir}/${path}"
  target_path="${work_dir}/${package}/${path}"
  if [[ -d "${source_path}" ]]; then
    if ! run_build_command python3 "${safe_fs}" install-tree build-dist "${source_path}" "${target_path}" "distribution source tree" "${distribution_tree_excludes[@]}"; then
      printf 'failed to copy distribution source tree: %s\n' "${source_path}" >&2
      exit 1
    fi
  else
    if ! run_build_command python3 "${safe_fs}" copy-file build-dist "${source_path}" "${target_path}" 0644; then
      printf 'failed to copy distribution source file: %s\n' "${source_path}" >&2
      exit 1
    fi
  fi
done

find_results="$(mktemp "${work_dir}/.build-dist-find.XXXXXX")"
if ! run_build_command find "${work_dir}/${package}" \
    -type d \( -name __pycache__ -o -name .pytest_cache -o -name .mypy_cache \) \
    -prune -print0 >"${find_results}"; then
  printf 'failed to enumerate distribution cache directories\n' >&2
  exit 1
fi
while IFS= read -r -d '' cache_dir; do
  cache_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${cache_dir}" --kind dir)"
  if ! run_build_command "${safe_fs_cmd[@]}" remove build-dist "${cache_dir}" --kind dir \
      --expected-identity "${cache_identity}"; then
    printf 'failed to remove distribution cache directory: %s\n' "${cache_dir}" >&2
    exit 1
  fi
done <"${find_results}"

if ! run_build_command find "${work_dir}/${package}" \
    -type f \( -name '*.pyc' -o -name '*.pyo' \) -print0 >"${find_results}"; then
  printf 'failed to enumerate distribution bytecode files\n' >&2
  exit 1
fi
while IFS= read -r -d '' bytecode_file; do
  bytecode_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${bytecode_file}" --kind file)"
  if ! run_build_command "${safe_fs_cmd[@]}" remove build-dist "${bytecode_file}" --kind file \
      --expected-identity "${bytecode_identity}"; then
    printf 'failed to remove distribution bytecode file: %s\n' "${bytecode_file}" >&2
    exit 1
  fi
done <"${find_results}"

symlink_path=""
if ! symlink_path="$(run_build_command find "${work_dir}/${package}" -type l -print -quit)"; then
  printf 'failed to inspect distribution symlinks\n' >&2
  exit 1
fi
if [[ -n "${symlink_path}" ]]; then
  printf 'build-dist detected unsupported symlink in package contents.\n' >&2
  exit 1
fi

write_regular_file_from_stdin "${work_dir}/${package}/RELEASE-MANIFEST.txt" "release manifest" <<EOF
${package}

Contains:
- Cinnamon applet files under files/speed-of-cinnamon@H234598/
- Python backend under src/speed_of_cinnamon/
- Snap package configuration under snap/
- local build, verify, install, uninstall, and dependency scripts under scripts/
- tests, CI workflow, README, license, and docs
EOF

final_tarball="${dist_dir}/${package}.tar.gz"
final_checksum="${final_tarball}.sha256"
dist_staging_dir="$(mktemp -d "${work_dir}/dist-staging-XXXXXX")"
if [[ -L "${dist_staging_dir}" ]]; then
  printf 'dist staging directory must not be a symlink: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
if ! dist_staging_dir_abs="$(realpath "${dist_staging_dir}")"; then
  printf 'failed to resolve dist staging directory: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
if [[ "${dist_staging_dir_abs}" != "${work_dir}/dist-staging-"* ]]; then
  printf 'dist staging directory escaped build workspace: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
dist_staging_dir="${dist_staging_dir_abs}"
if ! dist_staging_dir_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${dist_staging_dir}" --kind dir)"; then
  printf 'failed to capture dist staging directory identity: %s\n' "${dist_staging_dir}" >&2
  exit 1
fi
staging_tarball="$(mktemp "${dist_staging_dir}/stage.tar.gz.XXXXXX")"
if ! staging_tarball_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${staging_tarball}" --kind file)"; then
  printf 'failed to capture staged tarball identity: %s\n' "${staging_tarball}" >&2
  exit 1
fi

staging_checksum="$(mktemp "${dist_staging_dir}/stage.tar.gz.sha256.XXXXXX")"
if ! staging_checksum_identity="$(run_build_command "${safe_fs_cmd[@]}" identity build-dist "${staging_checksum}" --kind file)"; then
  printf 'failed to capture staged checksum identity: %s\n' "${staging_checksum}" >&2
  exit 1
fi
producer_metadata="$(
  build_staged_tarball \
    "${work_dir}" \
    "${package}" \
    "${staging_tarball}" \
    "${staging_tarball_identity}" \
    "${staging_checksum}" \
    "${staging_checksum_identity}"
)"
producer_stage_identity=""
producer_stage_size=""
producer_stage_digest=""
producer_checksum_identity=""
producer_checksum_size=""
producer_checksum_digest=""
if [[ "${producer_metadata}" == *$'\n'* ]] || ! IFS=$'\t' read -r \
  producer_stage_identity producer_stage_size producer_stage_digest \
  producer_checksum_identity producer_checksum_size producer_checksum_digest \
  <<< "${producer_metadata}"; then
  printf 'build-dist producer metadata is invalid\n' >&2
  exit 1
fi
if [[ ! "${producer_stage_identity}" =~ ^[0-9]+:[0-9]+:[0-9]+$ \
  || ! "${producer_stage_size}" =~ ^(0|[1-9][0-9]*)$ \
  || ! "${producer_stage_digest}" =~ ^[0-9a-f]{64}$ \
  || ! "${producer_checksum_identity}" =~ ^[0-9]+:[0-9]+:[0-9]+$ \
  || ! "${producer_checksum_size}" =~ ^(0|[1-9][0-9]*)$ \
  || ! "${producer_checksum_digest}" =~ ^[0-9a-f]{64}$ ]]; then
  printf 'build-dist producer metadata is invalid\n' >&2
  exit 1
fi
staging_tarball_identity="${producer_stage_identity}"
staging_tarball_size="${producer_stage_size}"
staging_tarball_digest="${producer_stage_digest}"
staging_checksum_identity="${producer_checksum_identity}"
staging_checksum_size="${producer_checksum_size}"
staging_checksum_digest="${producer_checksum_digest}"
finalization_started=1
replace_with_finalize_lock \
  "${dist_finalize_lock}" \
  "${staging_tarball}" \
  "${final_tarball}" \
  "${staging_checksum}" \
  "${final_checksum}" \
  "${staging_tarball_identity}" \
  "${staging_tarball_size}" \
  "${staging_tarball_digest}" \
  "${staging_checksum_identity}" \
  "${staging_checksum_size}" \
  "${staging_checksum_digest}"
staging_tarball=""
staging_tarball_identity=""
staging_tarball_size=""
staging_tarball_digest=""
staging_checksum=""
staging_checksum_identity=""
staging_checksum_size=""
staging_checksum_digest=""

printf 'Built %s\n' "${final_tarball}" >&2
printf '%s\n' "${final_tarball}"
