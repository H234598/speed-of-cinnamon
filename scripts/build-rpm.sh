#!/usr/bin/env -S BASH_ENV= ENV= /bin/bash --noprofile --norc -p
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

# RPM lifecycle watchdog starts before repository discovery.
readonly RPM_LIFECYCLE_TIMEOUT_SECONDS=3600
readonly RPM_LIFECYCLE_KILL_AFTER_SECONDS=30
if ((
  RPM_LIFECYCLE_TIMEOUT_SECONDS <= 0
  || RPM_LIFECYCLE_KILL_AFTER_SECONDS <= 0
  || RPM_LIFECYCLE_TIMEOUT_SECONDS <= RPM_LIFECYCLE_KILL_AFTER_SECONDS
)); then
  printf 'invalid RPM lifecycle watchdog budget\n' >&2
  exit 1
fi
readonly RPM_LIFECYCLE_HANDOFF_FD=9
lifecycle_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
lifecycle_supervisor="${lifecycle_script_dir}/rpm-lifecycle-supervisor.py"
lifecycle_python="$(command -v -- python3 || true)"
if [[ -z "${lifecycle_python}" || ! -f "${lifecycle_supervisor}" ]]; then
  printf 'RPM lifecycle supervisor is unavailable\n' >&2
  exit 1
fi
lifecycle_handoff_valid=0
# Anonymous pipe is per invocation; same-UID callers can forge inherited FDs, an accepted risk.
if [[ -e "/proc/self/fd/${RPM_LIFECYCLE_HANDOFF_FD}" ]]; then
  lifecycle_handoff_status="$(
    "${lifecycle_python}" -I -B "${lifecycle_supervisor}" \
      --check-handoff-fd="${RPM_LIFECYCLE_HANDOFF_FD}" \
      --handoff-timeout=0.1 \
      --handoff-max-bytes=256
  )" || lifecycle_handoff_status=""
  if [[ "${lifecycle_handoff_status}" == "valid" ]]; then
    lifecycle_handoff_valid=1
  fi
fi
if (( lifecycle_handoff_valid == 1 )); then
  exec 9<&-
else
  if [[ -e "/proc/self/fd/${RPM_LIFECYCLE_HANDOFF_FD}" ]]; then
    exec 9<&-
  fi
  lifecycle_handoff_token="rpm-lifecycle-${BASHPID}-${PPID}-${RANDOM}-${RANDOM}"
  exec 9< <(printf '%s\n' "${lifecycle_handoff_token}")
  # Trusted tools must not call setsid; detached compromised descendants are accepted risk.
  exec "${lifecycle_python}" -I -B "${lifecycle_supervisor}" \
    --timeout="${RPM_LIFECYCLE_TIMEOUT_SECONDS}" \
    --kill-after="${RPM_LIFECYCLE_KILL_AFTER_SECONDS}" \
    --handoff-fd="${RPM_LIFECYCLE_HANDOFF_FD}" \
    -- /bin/bash --noprofile --norc -p "${BASH_SOURCE[0]}" "$@"
fi
# RPM lifecycle watchdog ends.

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=("${lifecycle_python}" -I -B "${safe_fs}")
readonly RPM_IDENTITY_MAX_BYTES=256
readonly RPM_IDENTITY_TIMEOUT_SECONDS=5
bounded_safe_fs_identity() {
  local target=$1
  local kind=$2
  "${lifecycle_python}" -I -B "${lifecycle_supervisor}" \
    --identity \
    --identity-timeout="${RPM_IDENTITY_TIMEOUT_SECONDS}" \
    --identity-max-bytes="${RPM_IDENTITY_MAX_BYTES}" \
    -- "${safe_fs_cmd[@]}" identity build-rpm "${target}" --kind "${kind}"
}
cleanup_timeout_command="$(command -v -- timeout || true)"
cleanup_clock_command=("${lifecycle_python}" -I -B)
readonly RPM_BUILD_TIMEOUT_SECONDS=3600
readonly RPM_FINALIZE_TIMEOUT_SECONDS=120
readonly RPM_FINALIZE_LOCK_TIMEOUT_SECONDS=30
readonly RPM_FINALIZE_KILL_AFTER_SECONDS=2
readonly RPM_STARTUP_SWEEP_TIMEOUT_SECONDS=30
readonly RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS=1
readonly RPM_OUTPUT_TIMEOUT_SECONDS=30
readonly RPM_OUTPUT_KILL_AFTER_SECONDS=1
readonly RPM_CLEANUP_TIMEOUT_SECONDS=5
readonly RPM_CLEANUP_KILL_AFTER_SECONDS=1
readonly RPM_CLEANUP_LAUNCH_MARGIN_NS=50000000
readonly RPM_CLEANUP_CLOCK_TIMEOUT_SECONDS=0.25
readonly RPM_CLEANUP_CLOCK_TIMEOUT_NS=250000000

if ((
  RPM_FINALIZE_TIMEOUT_SECONDS <= 0
  || RPM_FINALIZE_KILL_AFTER_SECONDS <= 0
  || RPM_FINALIZE_TIMEOUT_SECONDS <= RPM_FINALIZE_KILL_AFTER_SECONDS
  || RPM_STARTUP_SWEEP_TIMEOUT_SECONDS <= 0
  || RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS <= 0
  || RPM_STARTUP_SWEEP_TIMEOUT_SECONDS <= RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS
  || RPM_OUTPUT_TIMEOUT_SECONDS <= 0
  || RPM_OUTPUT_KILL_AFTER_SECONDS <= 0
  || RPM_OUTPUT_TIMEOUT_SECONDS <= RPM_OUTPUT_KILL_AFTER_SECONDS
)); then
  printf 'invalid RPM watchdog budget\n' >&2
  exit 1
fi

require_cmd() {
  local tool=$1
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf '%s not found. Install %s.\n' "${tool}" "${tool}" >&2
    exit 1
  fi
}

require_regular_source_file() {
  local path=$1
  local label=$2
  local link_count

  if [[ ! -f "${path}" || -L "${path}" ]]; then
    printf '%s must be a regular file: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
  link_count="$(stat -c '%h' "${path}")"
  if [[ "${link_count}" -ne 1 ]]; then
    printf '%s must not be hardlinked: %s\n' "${label}" "${path}" >&2
    exit 1
  fi
}

bind_directory_fd() {
  local path=$1
  local expected_identity=$2
  local label=$3
  local output_variable=$4
  local directory_fd

  if ! exec {directory_fd}<"${path}"; then
    printf 'failed to open %s for bound use: %s\n' "${label}" "${path}" >&2
    return 1
  fi
  if ! timeout --foreground --signal=TERM --kill-after="${RPM_FINALIZE_KILL_AFTER_SECONDS}s" \
    "$((RPM_FINALIZE_TIMEOUT_SECONDS - RPM_FINALIZE_KILL_AFTER_SECONDS))s" \
    "${python_bin}" -I -B - "${directory_fd}" "${path}" "${expected_identity}" "${label}" <<'PY'
import os
import stat
import sys

fd_number, path, expected_identity, label = sys.argv[1:]
directory_fd = int(fd_number)


def identity(stat_result):
    return f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}"


try:
    descriptor_stat = os.fstat(directory_fd)
    path_stat = os.stat(path, follow_symlinks=False)
except OSError as exc:
    raise SystemExit(f"could not verify {label} before use: {path}: {exc}") from exc
if (
    not stat.S_ISDIR(descriptor_stat.st_mode)
    or identity(descriptor_stat) != expected_identity
    or identity(path_stat) != identity(descriptor_stat)
):
    raise SystemExit(f"{label} changed before bound use: {path}")
PY
  then
    exec {directory_fd}<&-
    return 1
  fi
  printf -v "${output_variable}" '%s' "${directory_fd}"
}

activate_with_finalize_lock() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3
  local publish_path=$4
  local previous_path=$5
  local previous_recovery_path=$6
  local staging_identity=$7
  local lifecycle_supervisor_path=$8

  timeout --foreground --signal=TERM --kill-after="${RPM_FINALIZE_KILL_AFTER_SECONDS}s" \
    "$((RPM_FINALIZE_TIMEOUT_SECONDS - RPM_FINALIZE_KILL_AFTER_SECONDS))s" \
    "${python_bin}" -I -B - "$lock_path" "$safe_fs" "$staging_path" "$final_path" "$publish_path" "$previous_path" "$previous_recovery_path" "$staging_identity" "$lifecycle_supervisor_path" "$RPM_FINALIZE_TIMEOUT_SECONDS" "$RPM_FINALIZE_LOCK_TIMEOUT_SECONDS" <<'PY'
import ctypes
import errno
import os
import re
import subprocess
import stat
import secrets
import sys
import time
from contextlib import nullcontext

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe RPM finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path, publish_path, previous_path, previous_recovery_path, staging_identity, lifecycle_supervisor, finalize_timeout, lock_timeout = sys.argv[1:]
finalize_timeout_seconds = int(finalize_timeout)
lock_timeout_seconds = int(lock_timeout)
global_deadline = time.monotonic() + finalize_timeout_seconds
lock_deadline = min(global_deadline, time.monotonic() + lock_timeout_seconds)
lock_parent = os.path.dirname(lock_path)
lock_name = os.path.basename(lock_path)

if not lock_name:
    print(f"finalization lock path is invalid: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

parent_flags = os.O_RDONLY
if hasattr(os, "O_DIRECTORY"):
    parent_flags |= os.O_DIRECTORY
if hasattr(os, "O_NOFOLLOW"):
    parent_flags |= os.O_NOFOLLOW
if hasattr(os, "O_CLOEXEC"):
    parent_flags |= os.O_CLOEXEC
try:
    parent_fd = os.open(lock_parent, parent_flags)
except OSError as exc:
    print(f"failed to open finalization lock parent safely: {lock_parent}: {exc}", file=sys.stderr)
    raise SystemExit(1)

staging_fd = None
workspace_fd = None
publish_fd = None
final_fd = None
trusted_final_tuples = set()
recovery_name = ""
try:
    parent_stat = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_stat.st_mode):
        print(f"finalization lock parent must be a directory: {lock_parent}", file=sys.stderr)
        raise SystemExit(1)
    if parent_stat.st_uid != os.geteuid() or parent_stat.st_mode & 0o022:
        raise RuntimeError(f"finalization lock parent is not private: {lock_parent}")

    with nullcontext():
        parent_identity = f"{parent_stat.st_dev}:{parent_stat.st_ino}:{parent_stat.st_mode}"

        def revalidate_parent(label):
            try:
                path_parent_stat = os.stat(lock_parent, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"could not revalidate {label}: {lock_parent}") from exc
            if (
                f"{path_parent_stat.st_dev}:{path_parent_stat.st_ino}:{path_parent_stat.st_mode}"
                != parent_identity
            ):
                raise RuntimeError(f"{label} changed during finalization: {lock_parent}")

        revalidate_parent("finalization lock parent before flock")
        while True:
            if lock_deadline - time.monotonic() <= 0:
                raise RuntimeError("RPM finalization lock timed out")
            try:
                fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except InterruptedError:
                if lock_deadline - time.monotonic() <= 0:
                    raise RuntimeError("RPM finalization lock timed out")
                continue
            except BlockingIOError:
                remaining = lock_deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("RPM finalization lock timed out")
                time.sleep(min(0.05, remaining))
        revalidate_parent("finalization lock parent after flock")
        safe_fs_base = [sys.executable, "-I", "-B", safe_fs]
        identity_helper_base = [sys.executable, "-I", "-B", lifecycle_supervisor]
        finalize_deadline = global_deadline
        safe_fs_invoked = False
        safe_fs_result = None

        def remaining_finalize_timeout():
            remaining = finalize_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RPM finalization deadline exceeded")
            return remaining

        def run_safe_fs(*arguments):
            global safe_fs_invoked, safe_fs_result
            safe_fs_invoked = False
            safe_fs_result = None
            try:
                timeout = remaining_finalize_timeout()
            except BaseException as exc:
                safe_fs_result = exc
                raise
            safe_fs_invoked = True
            try:
                subprocess.run(
                    [*safe_fs_base, *arguments],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    timeout=timeout,
                )
                remaining_finalize_timeout()
            except BaseException as exc:
                safe_fs_result = exc
                raise
            safe_fs_result = 0

        def report_cleanup_failure(label, path, operation_invoked, operation_result):
            if operation_invoked and operation_result is not None:
                print(
                    f"RPM {label} cleanup failed after safe-FS invocation; "
                    f"safe-FS residue report is authoritative: {path}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"RPM {label} cleanup not attempted; safe-FS was not invoked; "
                    f"residue path retained: {path}",
                    file=sys.stderr,
                )

        def rename_pinned(source_fd, source_name, target_fd, target_name, *, flags, action):
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                renameat2 = libc.renameat2
            except (AttributeError, OSError) as exc:
                raise OSError(errno.ENOTSUP, f"atomic pinned rename is not supported during {action}") from exc
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(
                source_fd,
                os.fsencode(source_name),
                target_fd,
                os.fsencode(target_name),
                flags,
            )
            if result != 0:
                error_number = ctypes.get_errno()
                raise OSError(error_number, os.strerror(error_number), target_name)

        def quarantine_unexpected_final(expected_tuples, recovery_name, final_path):
            remaining_finalize_timeout()
            try:
                os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return
            expected_tuples = set(expected_tuples)
            current_tuple = None
            current_fd = None
            try:
                current_fd = open_named_directory(
                    parent_fd,
                    final_name,
                    final_path,
                    "RPM final quarantine identity",
                )
                current_tuple = output_identity_tuple(
                    current_fd,
                    final_path,
                    "RPM final quarantine identity",
                )
            except BaseException:
                current_tuple = None
            finally:
                primary_error = sys.exc_info()[1]
                if current_fd is not None:
                    try:
                        os.close(current_fd)
                    except BaseException as cleanup_error:
                        if primary_error is not None:
                            primary_error.add_note("RPM final quarantine descriptor cleanup failed")
                        else:
                            raise
            if current_tuple in expected_tuples:
                return
            MAX_QUARANTINE_ATTEMPTS = 16
            for attempt in range(MAX_QUARANTINE_ATTEMPTS):
                remaining_finalize_timeout()
                quarantine_name = (
                    f"{recovery_name}.candidate-mismatch-{attempt}-{secrets.token_hex(16)}"
                )
                try:
                    rename_pinned(
                        parent_fd,
                        final_name,
                        parent_fd,
                        quarantine_name,
                        flags=1,
                        action="unexpected RPM final quarantine",
                    )
                except FileExistsError:
                    continue
                except FileNotFoundError:
                    return
                sync_directory(parent_fd, "unexpected RPM final quarantine")
                print(
                    f"quarantined unexpected RPM final directory: {final_path} -> "
                    f"{os.path.join(final_parent, quarantine_name)}",
                    file=sys.stderr,
                )
                return
            raise RuntimeError(f"could not quarantine unexpected RPM final directory: {final_path}")

        def sync_directory(directory_fd, label):
            remaining_finalize_timeout()
            try:
                os.fsync(directory_fd)
            except OSError as exc:
                raise RuntimeError(f"could not synchronize {label}") from exc
            remaining_finalize_timeout()

        def safe_fs_identity(path, *, kind):
            timeout = remaining_finalize_timeout()
            result = subprocess.run(
                [
                    *identity_helper_base,
                    "--identity",
                    "--identity-timeout",
                    str(min(timeout, 5.0)),
                    "--identity-max-bytes",
                    "256",
                    "--",
                    *safe_fs_base,
                    "identity",
                    "build-rpm",
                    path,
                    "--kind",
                    kind,
                ],
                check=False,
                capture_output=True,
                timeout=timeout,
            )
            remaining_finalize_timeout()
            if result.returncode != 0:
                raise RuntimeError(f"could not verify RPM {kind} identity: {path}")
            identity = result.stdout.decode("ascii").strip()
            if (
                len(identity) > 256
                or not identity
                or not re.fullmatch(r"[0-9]+:[0-9]+:[0-9]+", identity)
            ):
                raise RuntimeError(f"invalid RPM {kind} identity: {path}")
            return identity

        def identity_text(stat_result):
            return f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}"

        def close_descriptor_once(descriptor, label, primary_error=None):
            descriptor_to_close = descriptor
            descriptor = None
            try:
                os.close(descriptor_to_close)
            except BaseException as close_error:
                diagnostic = (
                    f"{label} close failed: {type(close_error).__name__}: {close_error}"
                )
                if primary_error is not None:
                    primary_error.add_note(diagnostic)
                    return
                raise RuntimeError(diagnostic) from close_error

        def open_bound_directory(path, expected_identity, label):
            remaining_finalize_timeout()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            directory_fd = None
            try:
                directory_fd = os.open(path, flags)
                descriptor_stat = os.fstat(directory_fd)
                path_stat = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                if directory_fd is not None:
                    close_descriptor_once(directory_fd, f"{label} descriptor", exc)
                raise RuntimeError(f"could not bind {label}: {path}") from exc
            if (
                not stat.S_ISDIR(descriptor_stat.st_mode)
                or identity_text(descriptor_stat) != expected_identity
                or identity_text(path_stat) != identity_text(descriptor_stat)
            ):
                failure = RuntimeError(f"{label} changed before bound use: {path}")
                close_descriptor_once(directory_fd, f"{label} descriptor", failure)
                raise failure
            return directory_fd

        def open_named_directory(directory_fd, name, path, label):
            remaining_finalize_timeout()
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            child_fd = None
            try:
                child_fd = os.open(name, flags, dir_fd=directory_fd)
                descriptor_stat = os.fstat(child_fd)
                path_stat = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            except OSError as exc:
                if child_fd is not None:
                    close_descriptor_once(child_fd, f"{label} descriptor", exc)
                raise RuntimeError(f"could not bind {label}: {path}") from exc
            if (
                not stat.S_ISDIR(descriptor_stat.st_mode)
                or identity_text(path_stat) != identity_text(descriptor_stat)
            ):
                failure = RuntimeError(f"{label} changed before bound use: {path}")
                close_descriptor_once(child_fd, f"{label} descriptor", failure)
                raise failure
            return child_fd

        def revalidate_bound_directory(directory_fd, path, expected_identity, label):
            remaining_finalize_timeout()
            try:
                descriptor_stat = os.fstat(directory_fd)
                path_stat = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"could not revalidate {label}: {path}") from exc
            if (
                not stat.S_ISDIR(descriptor_stat.st_mode)
                or identity_text(descriptor_stat) != expected_identity
                or identity_text(path_stat) != identity_text(descriptor_stat)
            ):
                raise RuntimeError(f"{label} changed during finalization: {path}")

        def revalidate_named_directory(parent_directory_fd, name, child_fd, path, expected_identity, label):
            remaining_finalize_timeout()
            try:
                descriptor_stat = os.fstat(child_fd)
                path_stat = os.stat(name, dir_fd=parent_directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"could not revalidate {label}: {path}") from exc
            if (
                not stat.S_ISDIR(descriptor_stat.st_mode)
                or identity_text(descriptor_stat) != expected_identity
                or identity_text(path_stat) != identity_text(descriptor_stat)
            ):
                raise RuntimeError(f"{label} changed during finalization: {path}")

        def revalidate_named_identity(parent_directory_fd, name, path, expected_identity, label):
            remaining_finalize_timeout()
            try:
                path_stat = os.stat(name, dir_fd=parent_directory_fd, follow_symlinks=False)
            except OSError as exc:
                raise RuntimeError(f"could not revalidate {label}: {path}") from exc
            if (
                not stat.S_ISDIR(path_stat.st_mode)
                or identity_text(path_stat) != expected_identity
            ):
                raise RuntimeError(f"{label} changed during finalization: {path}")

        def capture_output_root_identity(candidate_fd, root_name, root_path):
            root_fd = None
            try:
                root_fd = open_named_directory(
                    candidate_fd,
                    root_name,
                    root_path,
                    f"RPM publish {root_name} directory",
                )
                root_identity = identity_text(os.fstat(root_fd))
                revalidate_named_directory(
                    candidate_fd,
                    root_name,
                    root_fd,
                    root_path,
                    root_identity,
                    f"RPM publish {root_name} directory",
                )
                return root_identity
            finally:
                primary_error = sys.exc_info()[1]
                if root_fd is not None:
                    try:
                        os.close(root_fd)
                    except BaseException as cleanup_error:
                        if primary_error is not None:
                            primary_error.add_note(
                                f"RPM publish {root_name} descriptor cleanup failed"
                            )
                        else:
                            raise

        def revalidate_output_root_identities(directory_fd, directory_path, identities):
            for root_name, expected_identity in identities.items():
                root_path = os.path.join(directory_path, root_name)
                root_fd = None
                try:
                    root_fd = open_named_directory(
                        directory_fd,
                        root_name,
                        root_path,
                        f"RPM final {root_name} directory",
                    )
                    revalidate_named_directory(
                        directory_fd,
                        root_name,
                        root_fd,
                        root_path,
                        expected_identity,
                        f"RPM final {root_name} directory",
                    )
                finally:
                    primary_error = sys.exc_info()[1]
                    if root_fd is not None:
                        try:
                            os.close(root_fd)
                        except BaseException as cleanup_error:
                            if primary_error is not None:
                                primary_error.add_note(
                                    f"RPM final {root_name} descriptor cleanup failed"
                                )
                            else:
                                raise

        def output_identity_tuple(directory_fd, directory_path, label):
            top_identity = identity_text(os.fstat(directory_fd))
            identities = {
                root_name: capture_output_root_identity(
                    directory_fd,
                    root_name,
                    os.path.join(directory_path, root_name),
                )
                for root_name in ("RPMS", "SRPMS")
            }
            return (top_identity, identities["RPMS"], identities["SRPMS"])

        def revalidate_output_tuple(directory_fd, directory_path, expected_tuple, label):
            top_identity, rpms_identity, srpms_identity = expected_tuple
            revalidate_bound_directory(directory_fd, directory_path, top_identity, label)
            revalidate_output_root_identities(
                directory_fd,
                directory_path,
                {"RPMS": rpms_identity, "SRPMS": srpms_identity},
            )

        def rollback_exchange_if_needed(expected_tuple, old_tuple, publish_path):
            expected_identity = expected_tuple[0]
            old_identity = old_tuple[0]
            remaining_finalize_timeout()
            try:
                current_stat = os.stat(final_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if identity_text(current_stat) == expected_identity:
                return False
            rollback_fd = None
            try:
                rollback_fd = open_named_directory(
                    workspace_fd,
                    os.path.basename(publish_path),
                    publish_path,
                    "RPM exchange rollback candidate",
                )
                rollback_identity = identity_text(os.fstat(rollback_fd))
                if rollback_identity != old_identity:
                    raise RuntimeError(f"RPM exchange rollback candidate changed: {publish_path}")
                revalidate_output_tuple(
                    rollback_fd,
                    publish_path,
                    old_tuple,
                    "RPM exchange rollback candidate",
                )
                revalidate_parent("RPM finalization parent before exchange rollback")
                rename_pinned(
                    parent_fd,
                    final_name,
                    workspace_fd,
                    os.path.basename(publish_path),
                    flags=2,
                    action="RPM exchange rollback",
                )
                sync_directory(parent_fd, "RPM exchange rollback parent")
                sync_directory(workspace_fd, "RPM exchange rollback workspace")
                revalidate_output_tuple(
                    rollback_fd,
                    final_path,
                    old_tuple,
                    "RPM exchange rollback final",
                )
                return True
            finally:
                primary_error = sys.exc_info()[1]
                if rollback_fd is not None:
                    try:
                        os.close(rollback_fd)
                    except BaseException as cleanup_error:
                        if primary_error is not None:
                            primary_error.add_note("RPM exchange rollback descriptor cleanup failed")
                        else:
                            raise

        def lstat_named(name, *, label):
            remaining_finalize_timeout()
            try:
                return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return None

        def directory_identity(name, *, label):
            path_stat = lstat_named(name, label=label)
            if path_stat is None:
                return None
            if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISDIR(path_stat.st_mode):
                raise RuntimeError(f"RPM {label} must be a directory: {os.path.join(final_parent, name)}")
            return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"

        final_parent = os.path.dirname(final_path)
        final_name = os.path.basename(final_path)
        if final_parent != lock_parent:
            raise RuntimeError("RPM final parent must match finalization lock parent")
        publish_prefix = f".{final_name}.publish-"

        MAX_PUBLISH_SCAN_ENTRIES = 256
        MAX_STALE_PUBLISH_WORKSPACES = 32
        PUBLISH_WORKSPACE_MAX_AGE_NS = 24 * 60 * 60 * 1_000_000_000
        stale = []
        try:
            remaining_finalize_timeout()
            with os.scandir(parent_fd) as entries:
                scanned_entries = 0
                for entry in entries:
                    scanned_entries += 1
                    if scanned_entries > MAX_PUBLISH_SCAN_ENTRIES:
                        raise RuntimeError(f"RPM publish workspace scan exceeds max {MAX_PUBLISH_SCAN_ENTRIES}")
                    if entry.name.startswith(publish_prefix):
                        if len(stale) >= MAX_STALE_PUBLISH_WORKSPACES:
                            raise RuntimeError(f"RPM publish workspace sweep exceeds max {MAX_STALE_PUBLISH_WORKSPACES}")
                        path_stat = lstat_named(entry.name, label="publish workspace")
                        if path_stat is None:
                            raise RuntimeError(f"RPM publish workspace disappeared: {entry.name}")
                        if (
                            not stat.S_ISDIR(path_stat.st_mode)
                            or stat.S_ISLNK(path_stat.st_mode)
                            or path_stat.st_uid != os.geteuid()
                            or path_stat.st_mode & 0o077
                            or getattr(path_stat, "st_nlink", 1) < 1
                        ):
                            raise RuntimeError(f"unresolved RPM publish workspace: {os.path.join(final_parent, entry.name)}")
                        age_ns = time.time_ns() - path_stat.st_mtime_ns
                        if age_ns < PUBLISH_WORKSPACE_MAX_AGE_NS:
                            raise RuntimeError(f"recent RPM publish workspace requires recovery: {os.path.join(final_parent, entry.name)}")
                        stale.append((entry.name, f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"))
                    if entry.name.startswith(f".{final_name}.previous-recovery-"):
                        path_stat = lstat_named(entry.name, label="previous recovery")
                        if path_stat is None:
                            raise RuntimeError(f"RPM previous recovery disappeared: {entry.name}")
                        if (
                            not stat.S_ISDIR(path_stat.st_mode)
                            or stat.S_ISLNK(path_stat.st_mode)
                            or path_stat.st_uid != os.geteuid()
                            or path_stat.st_mode & 0o077
                            or getattr(path_stat, "st_nlink", 1) < 1
                        ):
                            raise RuntimeError(f"unresolved RPM previous recovery: {os.path.join(final_parent, entry.name)}")
                        raise RuntimeError(
                            f"RPM previous recovery requires manual recovery: "
                            f"{os.path.join(final_parent, entry.name)}"
                        )
        except FileNotFoundError as exc:
            raise RuntimeError(f"RPM publish parent is missing: {final_parent}") from exc
        for stale_name, stale_identity in stale:
            run_safe_fs(
                "remove",
                "build-rpm",
                os.path.join(final_parent, stale_name),
                "--kind",
                "dir",
                "--expected-identity",
                stale_identity,
            )

        previous_name = os.path.basename(previous_path)
        recovery_name = os.path.basename(previous_recovery_path)
        if os.path.dirname(previous_path) != final_parent or os.path.dirname(previous_recovery_path) != final_parent:
            raise RuntimeError("RPM recovery paths escaped final parent")
        previous_identity = directory_identity(previous_name, label="previous artifact")
        recovery_identity = directory_identity(recovery_name, label="previous recovery")
        if recovery_identity is not None:
            raise RuntimeError(f"RPM previous recovery path already exists: {previous_recovery_path}")
        if previous_identity is not None:
            revalidate_named_identity(
                parent_fd,
                previous_name,
                previous_path,
                previous_identity,
                "previous artifact",
            )
            revalidate_parent("RPM finalization parent before previous recovery")
            rename_pinned(
                parent_fd,
                previous_name,
                parent_fd,
                recovery_name,
                flags=1,
                action="previous recovery",
            )
            sync_directory(parent_fd, "previous recovery")
            recovery_identity = directory_identity(recovery_name, label="previous recovery")
            if recovery_identity != previous_identity:
                raise RuntimeError(f"previous recovery identity changed: {previous_recovery_path}")

        final_name_from_path = os.path.basename(final_path)
        workspace_path = os.path.dirname(publish_path)
        workspace_name = os.path.basename(workspace_path)
        if (
            os.path.dirname(final_path) != final_parent
            or workspace_path == final_parent
            or os.path.dirname(workspace_path) != final_parent
            or not workspace_name.startswith(publish_prefix)
            or os.path.basename(publish_path) != "candidate"
            or final_name_from_path != final_name
        ):
            raise RuntimeError("RPM publish workspace layout is invalid")
        if lstat_named(workspace_name, label="publish workspace") is not None:
            raise RuntimeError(f"RPM publish workspace already exists: {workspace_path}")
        remaining_finalize_timeout()
        os.mkdir(workspace_name, 0o700, dir_fd=parent_fd)
        remaining_finalize_timeout()
        os.fsync(parent_fd)
        workspace_stat = lstat_named(workspace_name, label="publish workspace")
        if (
            workspace_stat is None
            or not stat.S_ISDIR(workspace_stat.st_mode)
            or workspace_stat.st_uid != os.geteuid()
            or workspace_stat.st_mode & 0o077
            or getattr(workspace_stat, "st_nlink", 1) < 1
        ):
            raise RuntimeError(f"new RPM publish workspace is unsafe: {workspace_path}")
        workspace_identity = f"{workspace_stat.st_dev}:{workspace_stat.st_ino}:{workspace_stat.st_mode}"
        workspace_fd = open_named_directory(
            parent_fd,
            workspace_name,
            workspace_path,
            "RPM publish workspace",
        )
        final_identity = directory_identity(final_name, label="final build directory")
        old_final_tuple = None
        if final_identity is not None:
            old_final_fd = None
            try:
                old_final_fd = open_named_directory(
                    parent_fd,
                    final_name,
                    final_path,
                    "RPM previous final build directory",
                )
                old_final_tuple = output_identity_tuple(
                    old_final_fd,
                    final_path,
                    "RPM previous final build directory",
                )
                if old_final_tuple[0] != final_identity:
                    raise RuntimeError(f"RPM previous final identity changed: {final_path}")
            finally:
                primary_error = sys.exc_info()[1]
                if old_final_fd is not None:
                    try:
                        os.close(old_final_fd)
                    except BaseException as cleanup_error:
                        if primary_error is not None:
                            primary_error.add_note("RPM previous final descriptor cleanup failed")
                        else:
                            raise
        staging_fd = open_bound_directory(staging_path, staging_identity, "RPM staging directory")
        revalidate_bound_directory(staging_fd, staging_path, staging_identity, "RPM staging directory")
        revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, "RPM publish workspace")
        # Same-UID content mutation after this helper returns is not an authenticity boundary.
        run_safe_fs(
            "install-tree",
            "build-rpm",
            staging_path,
            publish_path,
            "RPM build directory",
        )
        revalidate_bound_directory(staging_fd, staging_path, staging_identity, "RPM staging directory")
        revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, "RPM publish workspace")
        publish_fd = open_named_directory(workspace_fd, "candidate", publish_path, "RPM publish candidate")
        publish_identity = identity_text(os.fstat(publish_fd))
        revalidate_named_directory(
            workspace_fd,
            "candidate",
            publish_fd,
            publish_path,
            publish_identity,
            "RPM publish candidate",
        )
        if safe_fs_identity(publish_path, kind="dir") != publish_identity:
            raise RuntimeError(f"RPM publish candidate changed before activation: {publish_path}")
        expected_output_root_identities = {
            root_name: capture_output_root_identity(
                publish_fd,
                root_name,
                os.path.join(publish_path, root_name),
            )
            for root_name in ("RPMS", "SRPMS")
        }
        candidate_final_tuple = (
            publish_identity,
            expected_output_root_identities["RPMS"],
            expected_output_root_identities["SRPMS"],
        )
        trusted_final_tuples = {candidate_final_tuple}
        if old_final_tuple is not None:
            trusted_final_tuples.add(old_final_tuple)
        published_final_tuple = None
        published_final_identity = None
        if final_identity is None:
            try:
                revalidate_bound_directory(staging_fd, staging_path, staging_identity, "RPM staging directory")
                revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, "RPM publish workspace")
                revalidate_named_directory(
                    workspace_fd,
                    "candidate",
                    publish_fd,
                    publish_path,
                    publish_identity,
                    "RPM publish candidate",
                )
                revalidate_parent("RPM finalization parent before first publication")
                revalidate_output_tuple(
                    publish_fd,
                    publish_path,
                    candidate_final_tuple,
                    "RPM publish candidate before first publication",
                )
                rename_pinned(
                    workspace_fd,
                    "candidate",
                    parent_fd,
                    final_name,
                    flags=1,
                    action="first RPM publication",
                )
                sync_directory(workspace_fd, "first RPM publication workspace")
                sync_directory(parent_fd, "first RPM publication parent")
                revalidate_parent("RPM finalization parent after first publication")
                final_fd = open_named_directory(
                    parent_fd,
                    final_name,
                    final_path,
                    "RPM final build directory",
                )
                published_final_identity = identity_text(os.fstat(final_fd))
                published_final_tuple = (
                    published_final_identity,
                    expected_output_root_identities["RPMS"],
                    expected_output_root_identities["SRPMS"],
                )
                if published_final_tuple != candidate_final_tuple:
                    raise RuntimeError(f"RPM first publication identity changed: {final_path}")
                revalidate_output_tuple(
                    final_fd,
                    final_path,
                    candidate_final_tuple,
                    "RPM final build directory after first publication",
                )
                trusted_final_tuples = {candidate_final_tuple}
            except BaseException as primary_error:
                try:
                    quarantine_unexpected_final(
                        trusted_final_tuples,
                        recovery_name,
                        final_path,
                    )
                except BaseException as quarantine_error:
                    primary_error.add_note(f"unexpected RPM final quarantine failed: {quarantine_error}")
                print(f"preserving RPM publish workspace after first publish attempt: {publish_path}", file=sys.stderr)
                raise
        else:
            publication_completed = False
            try:
                revalidate_bound_directory(staging_fd, staging_path, staging_identity, "RPM staging directory")
                revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, "RPM publish workspace")
                revalidate_named_directory(
                    workspace_fd,
                    "candidate",
                    publish_fd,
                    publish_path,
                    publish_identity,
                    "RPM publish candidate",
                )
                revalidate_named_identity(
                    parent_fd,
                    final_name,
                    final_path,
                    final_identity,
                    "RPM final build directory",
                )
                revalidate_parent("RPM finalization parent before exchange")
                revalidate_output_tuple(
                    publish_fd,
                    publish_path,
                    candidate_final_tuple,
                    "RPM publish candidate before exchange",
                )
                rename_pinned(
                    workspace_fd,
                    "candidate",
                    parent_fd,
                    final_name,
                    flags=2,
                    action="RPM publication exchange",
                )
                publication_completed = True
                sync_directory(workspace_fd, "RPM publication exchange workspace")
                sync_directory(parent_fd, "RPM publication exchange parent")
                revalidate_parent("RPM finalization parent after exchange")
                final_fd = open_named_directory(
                    parent_fd,
                    final_name,
                    final_path,
                    "RPM final build directory",
                )
                published_final_identity = identity_text(os.fstat(final_fd))
                published_final_tuple = (
                    published_final_identity,
                    expected_output_root_identities["RPMS"],
                    expected_output_root_identities["SRPMS"],
                )
                if published_final_tuple != candidate_final_tuple:
                    raise RuntimeError(f"RPM exchange final identity changed: {final_path}")
                revalidate_output_tuple(
                    final_fd,
                    final_path,
                    candidate_final_tuple,
                    "RPM final build directory after exchange",
                )
                trusted_final_tuples = {candidate_final_tuple}
            except BaseException as primary_error:
                try:
                    if publication_completed:
                        trusted_final_tuples = {candidate_final_tuple}
                        rolled_back = rollback_exchange_if_needed(
                            candidate_final_tuple,
                            old_final_tuple,
                            publish_path,
                        )
                        if rolled_back:
                            trusted_final_tuples = {old_final_tuple}
                            rollback_final_fd = None
                            try:
                                rollback_final_fd = open_named_directory(
                                    parent_fd,
                                    final_name,
                                    final_path,
                                    "RPM exchange rollback final after recovery",
                                )
                                revalidate_output_tuple(
                                    rollback_final_fd,
                                    final_path,
                                    old_final_tuple,
                                    "RPM exchange rollback final after recovery",
                                )
                            finally:
                                primary_recovery_error = sys.exc_info()[1]
                                if rollback_final_fd is not None:
                                    try:
                                        os.close(rollback_final_fd)
                                    except BaseException as cleanup_error:
                                        if primary_recovery_error is not None:
                                            primary_recovery_error.add_note(
                                                "RPM exchange rollback final descriptor cleanup failed"
                                            )
                                        else:
                                            raise
                    else:
                        trusted_final_tuples = {old_final_tuple}
                        rolled_back = False
                    if not rolled_back:
                        quarantine_unexpected_final(
                            trusted_final_tuples,
                            recovery_name,
                            final_path,
                        )
                except BaseException as recovery_error:
                    try:
                        quarantine_unexpected_final(
                            trusted_final_tuples,
                            recovery_name,
                            final_path,
                        )
                    except BaseException as quarantine_error:
                        recovery_error.add_note(f"unexpected RPM final quarantine failed: {quarantine_error}")
                    primary_error.add_note(f"RPM publication rollback failed: {recovery_error}")
                print(f"preserving RPM publish workspace after exchange attempt: {publish_path}", file=sys.stderr)
                raise
            descriptor = publish_fd
            publish_fd = None
            os.close(descriptor)
            publish_fd = open_named_directory(workspace_fd, "candidate", publish_path, "RPM previous candidate")
            old_final_identity = identity_text(os.fstat(publish_fd))
            revalidate_named_directory(
                workspace_fd,
                "candidate",
                publish_fd,
                publish_path,
                old_final_identity,
                "RPM previous candidate",
            )
            if safe_fs_identity(publish_path, kind="dir") != old_final_identity:
                raise RuntimeError(f"RPM exchange old final identity changed: {publish_path}")
            if old_final_identity != final_identity:
                raise RuntimeError(f"RPM exchange old final identity changed: {publish_path}")
            if old_final_tuple is None or old_final_tuple[0] != old_final_identity:
                raise RuntimeError(f"RPM exchange old final tuple is unavailable: {publish_path}")
            revalidate_output_tuple(
                publish_fd,
                publish_path,
                old_final_tuple,
                "RPM previous candidate",
            )
            try:
                revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, "RPM publish workspace")
                if directory_identity(previous_name, label="previous artifact") is not None:
                    raise RuntimeError(f"previous artifact appeared during finalization: {previous_path}")
                revalidate_named_directory(
                    workspace_fd,
                    "candidate",
                    publish_fd,
                    publish_path,
                    old_final_identity,
                    "RPM previous candidate",
                )
                revalidate_output_tuple(
                    publish_fd,
                    publish_path,
                    old_final_tuple,
                    "RPM previous candidate before publication",
                )
                revalidate_parent("RPM finalization parent before previous publication")
                rename_pinned(
                    workspace_fd,
                    "candidate",
                    parent_fd,
                    previous_name,
                    flags=1,
                    action="previous RPM publication",
                )
                sync_directory(workspace_fd, "previous RPM publication workspace")
                sync_directory(parent_fd, "previous RPM publication parent")
                revalidate_parent("RPM finalization parent after previous publication")
                if directory_identity(previous_name, label="previous artifact") != old_final_identity:
                    raise RuntimeError(f"previous artifact identity changed: {previous_path}")
            except BaseException:
                print(f"preserving RPM publish workspace after exchange: {publish_path}", file=sys.stderr)
                raise
        if publish_fd is not None:
            descriptor = publish_fd
            publish_fd = None
            os.close(descriptor)
        safe_fs_invoked = False
        safe_fs_result = None
        try:
            revalidate_bound_directory(workspace_fd, workspace_path, workspace_identity, "RPM publish workspace")
            run_safe_fs(
                "rmdir",
                "build-rpm",
                workspace_path,
                "--expected-identity",
                workspace_identity,
            )
        except BaseException:
            report_cleanup_failure(
                "publish workspace",
                workspace_path,
                safe_fs_invoked,
                safe_fs_result,
            )
        if recovery_identity is not None:
            safe_fs_invoked = False
            safe_fs_result = None
            try:
                run_safe_fs(
                    "remove",
                    "build-rpm",
                    previous_recovery_path,
                    "--kind",
                    "dir",
                    "--expected-identity",
                    recovery_identity,
                )
            except BaseException:
                report_cleanup_failure(
                    "previous recovery",
                    previous_recovery_path,
                    safe_fs_invoked,
                    safe_fs_result,
                )
        if published_final_identity is None:
            raise RuntimeError("RPM final publication identity is unavailable")
        if published_final_tuple is None:
            raise RuntimeError("RPM final publication tuple is unavailable")
        try:
            revalidate_parent("RPM finalization parent before completion")
            revalidate_output_tuple(
                final_fd,
                final_path,
                published_final_tuple,
                "RPM final build directory before completion",
            )
            print(
                published_final_identity,
                expected_output_root_identities["RPMS"],
                expected_output_root_identities["SRPMS"],
                flush=True,
            )
        except BaseException as primary_error:
            try:
                quarantine_unexpected_final(
                    {published_final_tuple},
                    recovery_name,
                    final_path,
                )
            except BaseException as quarantine_error:
                primary_error.add_note(f"unexpected RPM final quarantine failed: {quarantine_error}")
            raise
except BaseException as primary_error:
    if trusted_final_tuples and recovery_name and "quarantine_unexpected_final" in locals():
        try:
            quarantine_unexpected_final(
                trusted_final_tuples,
                recovery_name,
                final_path,
            )
        except BaseException as quarantine_error:
            primary_error.add_note(f"unexpected RPM final quarantine failed: {quarantine_error}")
    raise
finally:
    primary_error = sys.exc_info()[1]
    descriptor_cleanup_errors = []
    descriptors = (final_fd, publish_fd, workspace_fd, staging_fd)
    final_fd = None
    publish_fd = None
    workspace_fd = None
    staging_fd = None
    for descriptor in descriptors:
        if descriptor is None:
            continue
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            descriptor_cleanup_errors.append(cleanup_error)
    descriptor_cleanup_error = descriptor_cleanup_errors[0] if descriptor_cleanup_errors else None
    if descriptor_cleanup_errors:
        if primary_error is not None:
            primary_error.add_note("build-rpm bound directory cleanup failed")
    parent_cleanup_error = None
    descriptor = parent_fd
    parent_fd = None
    try:
        os.close(descriptor)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-rpm finalization descriptor cleanup failed")
        elif descriptor_cleanup_error is not None:
            descriptor_cleanup_error.add_note("build-rpm finalization descriptor cleanup failed")
        else:
            parent_cleanup_error = cleanup_error
    if primary_error is None:
        if descriptor_cleanup_error is not None:
            raise SystemExit("build-rpm bound directory cleanup failed") from descriptor_cleanup_error
        if parent_cleanup_error is not None:
            raise SystemExit("build-rpm finalization descriptor cleanup failed") from parent_cleanup_error
PY
}

require_cmd python3
require_cmd timeout
if [[ -n "${XDG_RUNTIME_DIR:-}" ]]; then
  rpm_runtime_root="${XDG_RUNTIME_DIR}"
  if [[ ! "${rpm_runtime_root}" == /* || ! -d "${rpm_runtime_root}" || -L "${rpm_runtime_root}" ]]; then
    printf 'XDG_RUNTIME_DIR must be an existing private directory: %s\n' "${rpm_runtime_root}" >&2
    exit 1
  fi
  if ! "${safe_fs_cmd[@]}" assert-private-chain build-rpm "${rpm_runtime_root}"; then
    printf 'XDG_RUNTIME_DIR has an untrusted ancestor chain: %s\n' "${rpm_runtime_root}" >&2
    exit 1
  fi
else
  if [[ -z "${HOME:-}" ]]; then
    printf 'HOME is required for private RPM runtime storage when XDG_RUNTIME_DIR is absent.\n' >&2
    exit 1
  fi
  if ! "${safe_fs_cmd[@]}" assert-private-chain build-rpm "${HOME}"; then
    printf 'HOME has an untrusted ancestor chain for RPM runtime storage: %s\n' "${HOME}" >&2
    exit 1
  fi
  rpm_runtime_root="${HOME}/.cache/speed-of-cinnamon/rpm-${EUID}"
fi
if [[ ! "${rpm_runtime_root}" == /* ]]; then
  printf 'RPM runtime root must be an absolute path: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
if [[ -L "${rpm_runtime_root}" ]]; then
  printf 'RPM runtime root must not be a symlink: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" assert-private-chain build-rpm "${rpm_runtime_root}" --allow-missing; then
  printf 'RPM runtime root has an untrusted ancestor chain: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
if [[ ! -e "${rpm_runtime_root}" ]]; then
  "${safe_fs_cmd[@]}" mkdirs build-rpm "${rpm_runtime_root}"
fi
if ! "${safe_fs_cmd[@]}" assert-private-chain build-rpm "${rpm_runtime_root}"; then
  printf 'RPM runtime root has an untrusted ancestor chain: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
if [[ ! -d "${rpm_runtime_root}" || ! -w "${rpm_runtime_root}" ]]; then
  printf 'RPM runtime root is not a writable directory: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
runtime_owner="$(stat -c '%u' "${rpm_runtime_root}")"
runtime_mode="$(stat -c '%a' "${rpm_runtime_root}")"
if [[ "${runtime_owner}" != "${EUID}" || "${runtime_mode: -2}" != "00" ]]; then
  printf 'RPM runtime root is not private to current user: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
if ! rpm_runtime_root="$(realpath "${rpm_runtime_root}")"; then
  printf 'failed to resolve RPM runtime root: %s\n' "${rpm_runtime_root}" >&2
  exit 1
fi
rpm_tmp_parent="${rpm_runtime_root}/speed-of-cinnamon-rpm-${EUID}"
if ! "${safe_fs_cmd[@]}" assert-private-chain build-rpm "${rpm_tmp_parent}" --allow-missing; then
  printf 'RPM temporary parent has an untrusted ancestor chain: %s\n' "${rpm_tmp_parent}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-rpm "${rpm_tmp_parent}"
if ! "${safe_fs_cmd[@]}" assert-private-chain build-rpm "${rpm_tmp_parent}"; then
  printf 'RPM temporary parent has an untrusted ancestor chain: %s\n' "${rpm_tmp_parent}" >&2
  exit 1
fi
if [[ -L "${rpm_tmp_parent}" ]]; then
  printf 'RPM temporary parent must not be a symlink: %s\n' "${rpm_tmp_parent}" >&2
  exit 1
fi
timeout --foreground --signal=TERM --kill-after="${RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS}s" \
  "$((RPM_STARTUP_SWEEP_TIMEOUT_SECONDS - RPM_STARTUP_SWEEP_KILL_AFTER_SECONDS))s" \
  "${lifecycle_python}" -I -B - "${rpm_tmp_parent}" "${safe_fs}" <<'PY'
import os
import stat
import subprocess
import sys
import time

rpm_tmp_parent, safe_fs = sys.argv[1:]
MAX_SCAN_ENTRIES = 256
MAX_STALE_WORKSPACES = 32
WORKSPACE_MAX_AGE_NS = 24 * 60 * 60 * 1_000_000_000
PREFIX = "speed-of-cinnamon-rpm-tmp-"
SWEEP_TIMEOUT_SECONDS = 30
deadline = time.monotonic() + SWEEP_TIMEOUT_SECONDS


def remaining_timeout():
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("RPM temporary workspace sweep deadline exceeded")
    return remaining

parent_fd = os.open(
    rpm_tmp_parent,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
)
try:
    parent_stat = os.fstat(parent_fd)
    if (
        not stat.S_ISDIR(parent_stat.st_mode)
        or parent_stat.st_uid != os.geteuid()
        or parent_stat.st_mode & 0o077
    ):
        raise SystemExit(f"RPM temporary parent is not private: {rpm_tmp_parent}")
    stale = []
    scanned = [0]

    def collect(directory_fd, directory_path):
        remaining_timeout()
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                remaining_timeout()
                scanned[0] += 1
                if scanned[0] > MAX_SCAN_ENTRIES:
                    raise SystemExit(f"RPM temporary workspace scan exceeds max {MAX_SCAN_ENTRIES}")
                if not entry.name.startswith(PREFIX):
                    continue
                try:
                    entry_stat = entry.stat(follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        "RPM temporary workspace snapshot is ambiguous; "
                        f"residue path disappeared or was renamed: {directory_path}/{entry.name}"
                    ) from exc
                try:
                    path_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError as exc:
                    raise RuntimeError(
                        "RPM temporary workspace snapshot is ambiguous; "
                        f"residue path disappeared or was renamed: {directory_path}/{entry.name}"
                    ) from exc
                if (
                    f"{entry_stat.st_dev}:{entry_stat.st_ino}:{entry_stat.st_mode}"
                    != f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"
                ):
                    raise RuntimeError(
                        "RPM temporary workspace snapshot is ambiguous; "
                        f"residue path was replaced: {directory_path}/{entry.name}"
                    )
                if (
                    not stat.S_ISDIR(path_stat.st_mode)
                    or stat.S_ISLNK(path_stat.st_mode)
                    or path_stat.st_uid != os.geteuid()
                    or path_stat.st_mode & 0o077
                    or getattr(path_stat, "st_nlink", 1) < 1
                ):
                    raise SystemExit(f"unresolved RPM temporary workspace: {directory_path}/{entry.name}")
                age_ns = time.time_ns() - path_stat.st_mtime_ns
                if age_ns < WORKSPACE_MAX_AGE_NS:
                    raise SystemExit(f"recent RPM temporary workspace requires recovery: {directory_path}/{entry.name}")
                if len(stale) >= MAX_STALE_WORKSPACES:
                    raise SystemExit(f"RPM temporary workspace sweep exceeds max {MAX_STALE_WORKSPACES}")
                stale.append(
                    (
                        os.path.join(directory_path, entry.name),
                        f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}",
                    )
                )

    collect(parent_fd, rpm_tmp_parent)

    for path, identity in stale:
        remaining_timeout()
        if not os.path.lexists(path):
            raise RuntimeError(
                "RPM temporary workspace cleanup is ambiguous before safe-FS probe; "
                f"residue path disappeared or was renamed: {path}"
            )
        try:
            subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    safe_fs,
                    "remove",
                    "build-rpm",
                    path,
                    "--kind",
                    "dir",
                    "--expected-identity",
                    identity,
                ],
                check=True,
                timeout=remaining_timeout(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                "RPM temporary workspace cleanup is ambiguous after safe-FS failure; "
                f"residue path retained: {path}"
            ) from exc
        remaining_timeout()
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(parent_fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-rpm startup sweep descriptor cleanup failed")
        else:
            raise
PY
rpmbuild_tmpdir=""
stage_topdir=""
rpmbuild_tmpdir_identity=""
stage_topdir_identity=""
publish_workspace=""
publish_previous=""
publish_recovery=""
publish_committed=0
cleanup_deadline_ns=""
cleanup_safe_fs_invoked=0
stage_cleanup_unconfirmed=0
stage_cleanup_status=0
stage_cleanup_invoked=0
cleanup_stage_message_emitted=0
cleanup_parent_skip_reported=0
cleanup_now_ns() {
  if [[ -z "${cleanup_timeout_command}" ]]; then
    return 1
  fi
  "${cleanup_timeout_command}" --foreground --signal=KILL \
    "${RPM_CLEANUP_CLOCK_TIMEOUT_SECONDS}s" \
    "${cleanup_clock_command[@]}" -c 'import time; print(time.monotonic_ns())'
}
cleanup_deadline_probe() {
  if [[ -z "${cleanup_timeout_command}" ]]; then
    return 1
  fi
  local deadline_budget_ns=$((RPM_CLEANUP_TIMEOUT_SECONDS * 1000000000 - RPM_CLEANUP_CLOCK_TIMEOUT_NS - RPM_CLEANUP_LAUNCH_MARGIN_NS))
  if (( deadline_budget_ns <= 0 )); then
    return 1
  fi
  "${cleanup_timeout_command}" --foreground --signal=KILL \
    "${RPM_CLEANUP_CLOCK_TIMEOUT_SECONDS}s" \
    "${cleanup_clock_command[@]}" -c \
    "import time; print(time.monotonic_ns() + ${deadline_budget_ns})"
}
start_cleanup_deadline() {
  if [[ -n "${cleanup_deadline_ns}" ]]; then
    return 0
  fi
  local deadline_ns
  if ! deadline_ns="$(cleanup_deadline_probe 2>/dev/null)" ||
      [[ ! "${deadline_ns}" =~ ^[0-9]+$ || "${deadline_ns}" == 0 ]]; then
    cleanup_deadline_ns=0
    printf 'RPM cleanup monotonic clock is unavailable\n' >&2
    return 1
  fi
  cleanup_deadline_ns="${deadline_ns}"
}
cleanup_remaining_value_ns=""
cleanup_remaining_ns() {
  cleanup_remaining_value_ns=""
  local now_ns
  if [[ -z "${cleanup_deadline_ns}" || "${cleanup_deadline_ns}" == 0 ]]; then
    cleanup_deadline_ns=0
    return 1
  fi
  if ! now_ns="$(cleanup_now_ns 2>/dev/null)"; then
    cleanup_deadline_ns=0
    return 1
  fi
  if [[ ! "${now_ns}" =~ ^[0-9]+$ ]]; then
    cleanup_deadline_ns=0
    return 1
  fi
  local remaining_ns=$((cleanup_deadline_ns - now_ns))
  if (( remaining_ns <= 0 )); then
    cleanup_deadline_ns=0
    return 1
  fi
  cleanup_remaining_value_ns="${remaining_ns}"
}
cleanup_timeout_value() {
  local nanoseconds=$1
  printf '%d.%09d' "$((nanoseconds / 1000000000))" "$((nanoseconds % 1000000000))"
}
run_cleanup_safe_fs() {
  cleanup_safe_fs_invoked=0
  if [[ -z "${cleanup_timeout_command}" ]]; then
    printf 'RPM cleanup timeout command is unavailable\n' >&2
    return 124
  fi
  if ! start_cleanup_deadline; then
    return 124
  fi
  if ! cleanup_remaining_ns; then
    printf 'RPM cleanup deadline exhausted before safe-FS call\n' >&2
    return 124
  fi
  local remaining_ns="${cleanup_remaining_value_ns}"
  local available_ns=$((remaining_ns - RPM_CLEANUP_LAUNCH_MARGIN_NS))
  if (( available_ns <= 0 )); then
    printf 'RPM cleanup deadline exhausted before safe-FS launch\n' >&2
    return 124
  fi
  local kill_after_ns=$((RPM_CLEANUP_KILL_AFTER_SECONDS * 1000000000))
  if (( kill_after_ns >= available_ns )); then
    kill_after_ns=$((available_ns / 2))
  fi
  local term_ns=$((available_ns - kill_after_ns))
  if (( term_ns <= 0 || kill_after_ns <= 0 )); then
    printf 'RPM cleanup deadline cannot reserve TERM/KILL budget\n' >&2
    return 124
  fi
  local term_timeout
  local kill_after_timeout
  term_timeout="$(cleanup_timeout_value "${term_ns}")"
  kill_after_timeout="$(cleanup_timeout_value "${kill_after_ns}")"
  local status=0
  cleanup_safe_fs_invoked=1
  "${cleanup_timeout_command}" --foreground --signal=TERM --kill-after="${kill_after_timeout}s" \
    "${term_timeout}s" "${safe_fs_cmd[@]}" "$@" || status=$?
  if (( status == 124 || status == 137 || status == 143 )); then
    cleanup_deadline_ns=0
    return 124
  fi
  if ! cleanup_remaining_ns; then
    return 124
  fi
  return "${status}"
}
cleanup_tmpdir() {
  local inherited_status="$?"
  local primary_status="${1:-${inherited_status}}"
  local cleanup_failed=0
  stage_cleanup_unconfirmed="${stage_cleanup_unconfirmed:-0}"
  stage_cleanup_status="${stage_cleanup_status:-0}"
  stage_cleanup_invoked="${stage_cleanup_invoked:-0}"
  cleanup_stage_message_emitted="${cleanup_stage_message_emitted:-0}"
  cleanup_parent_skip_reported="${cleanup_parent_skip_reported:-0}"
  if ! start_cleanup_deadline; then
    cleanup_failed=1
  fi
  if [[ -n "${publish_workspace}" ]]; then
    printf 'RPM publish workspace cleanup accounting retained (no path probe): %s\n' \
      "${publish_workspace}" >&2
  fi
  if [[ -n "${publish_recovery}" ]]; then
    printf 'RPM previous recovery cleanup accounting retained (no path probe): %s\n' \
      "${publish_recovery}" >&2
  fi
  if (( stage_cleanup_unconfirmed == 0 )); then
    if [[ -n "${stage_topdir}" && -n "${stage_topdir_identity}" ]]; then
      stage_cleanup_status=0
      run_cleanup_safe_fs remove build-rpm "${stage_topdir}" \
        --kind dir \
        --expected-identity "${stage_topdir_identity}" || stage_cleanup_status=$?
      stage_cleanup_invoked="${cleanup_safe_fs_invoked}"
      if (( stage_cleanup_status != 0 )); then
        stage_cleanup_unconfirmed=1
        if (( cleanup_stage_message_emitted == 0 )); then
          if (( stage_cleanup_invoked == 1 )); then
            printf 'RPM stage cleanup failed after safe-FS invocation; safe-FS residue report is authoritative: %s\n' \
              "${stage_topdir}" >&2
          else
            printf 'RPM stage cleanup not attempted; safe-FS was not invoked; residue path retained: %s\n' \
              "${stage_topdir}" >&2
          fi
          cleanup_stage_message_emitted=1
        fi
        if [[ "${publish_committed}" != "1" ]]; then
          cleanup_failed=1
        fi
      else
        stage_topdir=""
        stage_topdir_identity=""
      fi
    elif [[ -n "${stage_topdir}" ]]; then
      stage_cleanup_status=1
      stage_cleanup_invoked=0
      stage_cleanup_unconfirmed=1
      if (( cleanup_stage_message_emitted == 0 )); then
        printf 'refusing RPM stage cleanup without verified identity: %s\n' "${stage_topdir}" >&2
        cleanup_stage_message_emitted=1
      fi
      if [[ "${publish_committed}" != "1" ]]; then
        cleanup_failed=1
      fi
    fi
  elif [[ -n "${stage_topdir}" && "${cleanup_stage_message_emitted}" == "0" ]]; then
    if (( stage_cleanup_invoked == 1 )); then
      printf 'RPM stage cleanup failed after safe-FS invocation; safe-FS residue report is authoritative: %s\n' \
        "${stage_topdir}" >&2
    else
      printf 'RPM stage cleanup not attempted; safe-FS was not invoked; residue path retained: %s\n' \
        "${stage_topdir}" >&2
    fi
    cleanup_stage_message_emitted=1
  fi
  if (( stage_cleanup_unconfirmed != 0 )); then
    if [[ -n "${rpmbuild_tmpdir}" && "${cleanup_parent_skip_reported}" == "0" ]]; then
      printf 'RPM workspace cleanup skipped because stage cleanup is unconfirmed; residue path retained: %s\n' \
        "${rpmbuild_tmpdir}" >&2
      cleanup_parent_skip_reported=1
      if [[ "${publish_committed}" != "1" ]]; then
        cleanup_failed=1
      fi
    fi
  elif [[ -n "${rpmbuild_tmpdir}" && -n "${rpmbuild_tmpdir_identity}" ]]; then
    local workspace_cleanup_status=0
    run_cleanup_safe_fs remove build-rpm "${rpmbuild_tmpdir}" --kind dir \
      --expected-identity "${rpmbuild_tmpdir_identity}" || workspace_cleanup_status=$?
    if (( workspace_cleanup_status != 0 )); then
      if (( cleanup_safe_fs_invoked == 1 )); then
        printf 'RPM workspace cleanup failed after safe-FS invocation; safe-FS residue report is authoritative: %s\n' \
          "${rpmbuild_tmpdir}" >&2
      else
        printf 'RPM workspace cleanup not attempted; safe-FS was not invoked; residue path retained: %s\n' \
          "${rpmbuild_tmpdir}" >&2
      fi
      if [[ "${publish_committed}" != "1" ]]; then
        cleanup_failed=1
      fi
    else
      rpmbuild_tmpdir=""
      rpmbuild_tmpdir_identity=""
    fi
  elif [[ -n "${rpmbuild_tmpdir}" ]]; then
    printf 'refusing RPM workspace cleanup without verified identity: %s\n' "${rpmbuild_tmpdir}" >&2
    cleanup_failed=1
  fi
  if (( cleanup_failed != 0 && primary_status == 0 )); then
    return 1
  fi
  return "${primary_status}"
}
cleanup_on_exit() {
  local primary_status="$?"
  local cleanup_status=0
  trap - EXIT
  if cleanup_tmpdir "${primary_status}"; then
    cleanup_status=0
  else
    cleanup_status=$?
  fi
  if (( primary_status != 0 )); then
    exit "${primary_status}"
  fi
  exit "${cleanup_status}"
}
trap cleanup_on_exit EXIT

rpmbuild_tmpdir="$(mktemp -d "${rpm_tmp_parent}/speed-of-cinnamon-rpm-tmp-XXXXXX")"
if [[ -L "${rpmbuild_tmpdir}" ]]; then
  printf 'temporary RPM workspace must not be a symlink: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi
if ! rpmbuild_tmpdir_abs="$(realpath "${rpmbuild_tmpdir}")"; then
  printf 'failed to resolve temporary RPM workspace: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi
if [[ "${rpmbuild_tmpdir_abs}" != "${rpm_tmp_parent}/speed-of-cinnamon-rpm-tmp-"* ]]; then
  printf 'temporary RPM workspace escaped temporary root: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi
rpmbuild_tmpdir="${rpmbuild_tmpdir_abs}"

if ! rpmbuild_tmpdir_identity="$(bounded_safe_fs_identity "${rpmbuild_tmpdir}" dir)"; then
  printf 'failed to capture temporary RPM workspace identity: %s\n' "${rpmbuild_tmpdir}" >&2
  exit 1
fi

profile="${1:-fedora}"
case "${profile}" in
  fedora|generic)
    ;;
  *)
    printf 'unknown rpm profile: %s\n' "${profile}" >&2
    exit 1
    ;;
esac

require_cmd rpmbuild
require_cmd python3
require_cmd realpath
python_bin="${lifecycle_python}"
if [[ ! -x "${repo_dir}/scripts/build-dist.sh" ]]; then
  printf 'build-dist script is missing: %s\n' "${repo_dir}/scripts/build-dist.sh" >&2
  exit 1
fi
require_regular_source_file "${safe_fs}" "safe local filesystem helper"
read -r tarball < <("${repo_dir}/scripts/build-dist.sh")
if [[ -L "${tarball}" ]]; then
  printf 'build-dist output must not be a symlink: %s\n' "${tarball}" >&2
  exit 1
fi
tarball="$(realpath "${tarball}")"
if [[ ! -f "${tarball}" || ! "${tarball}" == "${repo_dir}/dist/"*".tar.gz" ]]; then
  printf 'build-dist output is invalid: %s\n' "${tarball}" >&2
  exit 1
fi

if [[ "${profile}" == "generic" ]]; then
  final_topdir="${repo_dir}/dist/rpmbuild-generic"
  spec_source="${repo_dir}/packaging/speed-of-cinnamon-generic.spec"
else
  final_topdir="${repo_dir}/dist/rpmbuild"
  spec_source="${repo_dir}/packaging/speed-of-cinnamon.spec"
fi
if [[ -L "${final_topdir}" ]]; then
  printf 'RPM build directory must not be a symlink: %s\n' "${final_topdir}" >&2
  exit 1
fi
if [[ -L "${spec_source}" ]]; then
  printf 'spec source file must not be a symlink: %s\n' "${spec_source}" >&2
  exit 1
fi
dist_dir="$(dirname "${final_topdir}")"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist parent directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-rpm "${dist_dir}"
dist_finalize_lock="${dist_dir}/.build-rpm.finalize.lock"
stage_topdir="$(mktemp -d "${rpmbuild_tmpdir}/.$(basename "${final_topdir}").stage.XXXXXX")"
if [[ -L "${stage_topdir}" ]]; then
  printf 'temporary RPM stage directory must not be a symlink: %s\n' "${stage_topdir}" >&2
  exit 1
fi
if ! stage_topdir_abs="$(realpath "${stage_topdir}")"; then
  printf 'failed to resolve temporary RPM stage directory: %s\n' "${stage_topdir}" >&2
  exit 1
fi
if [[ "${stage_topdir_abs}" != "${rpmbuild_tmpdir}/."*".stage."* ]]; then
  printf 'temporary RPM stage directory escaped temporary workspace: %s\n' "${stage_topdir}" >&2
  exit 1
fi
stage_topdir="${stage_topdir_abs}"
if ! stage_topdir_identity="$(bounded_safe_fs_identity "${stage_topdir}" dir)"; then
  printf 'failed to capture temporary RPM stage identity: %s\n' "${stage_topdir}" >&2
  exit 1
fi
spec_file="${stage_topdir}/SPECS/speed-of-cinnamon.spec"

if [[ ! -f "${spec_source}" ]]; then
  printf 'spec source missing: %s\n' "${spec_source}" >&2
  exit 1
fi
require_regular_source_file "${tarball}" "tarball source"
require_regular_source_file "${spec_source}" "spec source"

for rpm_stage_dir in BUILD BUILDROOT RPMS SOURCES SPECS SRPMS; do
  "${safe_fs_cmd[@]}" mkdirs build-rpm "${stage_topdir}/${rpm_stage_dir}"
done
if ! "${safe_fs_cmd[@]}" copy-file build-rpm "${tarball}" "${stage_topdir}/SOURCES/$(basename "${tarball}")" 0644; then
  printf 'failed to copy tarball source into RPM staging: %s\n' "${tarball}" >&2
  exit 1
fi

version="$(
  "${python_bin}" -I -B - "${repo_dir}" <<'PY'
import sys
from pathlib import Path
import tomllib

repo_dir = Path(sys.argv[1])
MAX_PROJECT_METADATA_BYTES = 1 << 20
with (repo_dir / "pyproject.toml").open("rb") as handle:
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
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid version from pyproject.toml: %s\n' "${version}" >&2
  exit 1
fi

if ! "${safe_fs_cmd[@]}" copy-file build-rpm "${spec_source}" "${spec_file}" 0644; then
  printf 'failed to copy spec source into RPM staging: %s\n' "${spec_source}" >&2
  exit 1
fi
"${python_bin}" -I -B - <<'PY' "${spec_file}" "${version}"
import os
from pathlib import Path
import re
import secrets
import stat
import sys

spec_path = Path(sys.argv[1])
version = sys.argv[2]
MAX_RPM_SPEC_BYTES = 1 << 20
parent_fd = -1
spec_fd = -1
fd = -1
tmp_name = ""


def identity(stat_result):
    return f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}"


def require_parent_identity(label):
    descriptor_stat = os.fstat(parent_fd)
    path_stat = os.stat(spec_path.parent, follow_symlinks=False)
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or identity(descriptor_stat) != identity(path_stat)
    ):
        raise SystemExit(f"RPM spec parent changed {label}: {spec_path.parent}")


try:
    parent_fd = os.open(
        spec_path.parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    require_parent_identity("before rewrite")

    spec_fd = os.open(
        spec_path.name,
        os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    spec_descriptor_stat = os.fstat(spec_fd)
    spec_path_stat = os.stat(spec_path.name, dir_fd=parent_fd, follow_symlinks=False)
    spec_identity = identity(spec_descriptor_stat)
    if (
        not stat.S_ISREG(spec_descriptor_stat.st_mode)
        or spec_descriptor_stat.st_nlink != 1
        or identity(spec_path_stat) != spec_identity
    ):
        raise SystemExit(f"RPM spec is not a private regular file: {spec_path}")
    require_parent_identity("before read")
    with os.fdopen(spec_fd, "rb", closefd=True) as handle:
        spec_fd = -1
        payload = handle.read(MAX_RPM_SPEC_BYTES + 1)
    if len(payload) > MAX_RPM_SPEC_BYTES:
        raise SystemExit("RPM spec is too large")
    text = payload.decode("utf-8")
    text = re.sub(r"^Version:\s*.*$", f"Version:        {version}", text, flags=re.M)
    payload = text.encode("utf-8")

    require_parent_identity("before replace")
    current_spec_stat = os.stat(spec_path.name, dir_fd=parent_fd, follow_symlinks=False)
    if identity(current_spec_stat) != spec_identity:
        raise SystemExit(f"RPM spec changed during rewrite: {spec_path}")
    tmp_name = f".{spec_path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        fd = -1
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
        tmp_identity = identity(os.fstat(handle.fileno()))
    os.replace(tmp_name, spec_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    tmp_name = ""
    require_parent_identity("after replace")
    replaced_stat = os.stat(spec_path.name, dir_fd=parent_fd, follow_symlinks=False)
    if (
        not stat.S_ISREG(replaced_stat.st_mode)
        or replaced_stat.st_nlink != 1
        or identity(replaced_stat) != tmp_identity
    ):
        raise SystemExit(f"RPM spec changed during rewrite: {spec_path}")
    os.fsync(parent_fd)
    require_parent_identity("after fsync")
finally:
    primary_error = sys.exc_info()[1]
    cleanup_errors = []
    if spec_fd >= 0:
        try:
            os.close(spec_fd)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if fd >= 0:
        try:
            os.close(fd)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if tmp_name:
        try:
            os.unlink(tmp_name, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if parent_fd >= 0:
        try:
            os.close(parent_fd)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if cleanup_errors:
        if primary_error is not None:
            primary_error.add_note("build-rpm spec descriptor cleanup failed")
        else:
            raise SystemExit("build-rpm spec descriptor cleanup failed") from cleanup_errors[0]
PY

rpmbuild_bin="$(command -v -- rpmbuild)"
rpmbuild_status=0
timeout --foreground --signal=TERM --kill-after=30s "${RPM_BUILD_TIMEOUT_SECONDS}s" \
  "${python_bin}" -I -B - "${rpmbuild_bin}" "${python_bin}" \
  "${stage_topdir}" "${stage_topdir_identity}" \
  "${rpmbuild_tmpdir}" "${rpmbuild_tmpdir_identity}" \
  "$(basename "${spec_file}")" <<'PY' || rpmbuild_status=$?
import os
import stat
import sys

rpmbuild_path, python_bin, stage_path, stage_identity, workspace_path, workspace_identity, spec_name = sys.argv[1:]
bound_fds = []
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)


def identity(stat_result):
    return f"{stat_result.st_dev}:{stat_result.st_ino}:{stat_result.st_mode}"


def fail(message):
    raise SystemExit(message)


def close_descriptor_once(descriptor, label, primary_error=None):
    descriptor_to_close = descriptor
    descriptor = None
    try:
        os.close(descriptor_to_close)
    except BaseException as close_error:
        diagnostic = f"{label} close failed: {type(close_error).__name__}: {close_error}"
        if primary_error is not None:
            primary_error.add_note(diagnostic)
            return
        raise RuntimeError(diagnostic) from close_error


def open_bound_directory(path, expected_identity, label):
    directory_fd = None
    try:
        directory_fd = os.open(path, directory_flags)
        descriptor_stat = os.fstat(directory_fd)
        path_stat = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        if directory_fd is not None:
            failure = SystemExit(f"could not bind {label}: {path}: {exc}")
            close_descriptor_once(directory_fd, f"{label} descriptor", failure)
            raise failure
        fail(f"could not bind {label}: {path}: {exc}")
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or identity(descriptor_stat) != expected_identity
        or identity(path_stat) != identity(descriptor_stat)
    ):
        failure = SystemExit(f"{label} changed before rpmbuild: {path}")
        close_descriptor_once(directory_fd, f"{label} descriptor", failure)
        raise failure
    bound_fds.append(directory_fd)
    return directory_fd


def open_child_directory(parent_fd, name, path, label):
    child_fd = None
    try:
        path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(path_stat.st_mode):
            fail(f"{label} is not a directory: {path}")
        child_fd = os.open(name, directory_flags, dir_fd=parent_fd)
        descriptor_stat = os.fstat(child_fd)
    except OSError as exc:
        if child_fd is not None:
            failure = SystemExit(f"could not bind {label}: {path}: {exc}")
            close_descriptor_once(child_fd, f"{label} descriptor", failure)
            raise failure
        fail(f"could not bind {label}: {path}: {exc}")
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or identity(path_stat) != identity(descriptor_stat)
    ):
        failure = SystemExit(f"{label} changed before rpmbuild: {path}")
        close_descriptor_once(child_fd, f"{label} descriptor", failure)
        raise failure
    bound_fds.append(child_fd)
    return child_fd


def open_spec_file(specs_fd, name, path):
    spec_fd = None
    try:
        path_stat = os.stat(name, dir_fd=specs_fd, follow_symlinks=False)
        if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
            fail(f"RPM spec is not a private regular file: {path}")
        spec_fd = os.open(name, file_flags, dir_fd=specs_fd)
        descriptor_stat = os.fstat(spec_fd)
    except OSError as exc:
        if spec_fd is not None:
            failure = SystemExit(f"could not bind RPM spec: {path}: {exc}")
            close_descriptor_once(spec_fd, "RPM spec descriptor", failure)
            raise failure
        fail(f"could not bind RPM spec: {path}: {exc}")
    if (
        not stat.S_ISREG(descriptor_stat.st_mode)
        or descriptor_stat.st_nlink != 1
        or identity(path_stat) != identity(descriptor_stat)
    ):
        failure = SystemExit(f"RPM spec changed before rpmbuild: {path}")
        close_descriptor_once(spec_fd, "RPM spec descriptor", failure)
        raise failure
    bound_fds.append(spec_fd)
    return spec_fd


if not spec_name or "/" in spec_name or spec_name in {".", ".."}:
    fail(f"invalid RPM spec name: {spec_name}")

try:
    stage_fd = open_bound_directory(stage_path, stage_identity, "RPM staging directory")
    workspace_fd = open_bound_directory(workspace_path, workspace_identity, "temporary RPM workspace")
    build_fd = open_child_directory(stage_fd, "BUILD", os.path.join(stage_path, "BUILD"), "RPM BUILD directory")
    buildroot_fd = open_child_directory(
        stage_fd,
        "BUILDROOT",
        os.path.join(stage_path, "BUILDROOT"),
        "RPM BUILDROOT directory",
    )
    rpms_fd = open_child_directory(stage_fd, "RPMS", os.path.join(stage_path, "RPMS"), "RPM RPMS directory")
    sources_fd = open_child_directory(
        stage_fd,
        "SOURCES",
        os.path.join(stage_path, "SOURCES"),
        "RPM SOURCES directory",
    )
    specs_fd = open_child_directory(stage_fd, "SPECS", os.path.join(stage_path, "SPECS"), "RPM SPECS directory")
    srpms_fd = open_child_directory(
        stage_fd,
        "SRPMS",
        os.path.join(stage_path, "SRPMS"),
        "RPM SRPMS directory",
    )
    spec_fd = open_spec_file(specs_fd, spec_name, os.path.join(stage_path, "SPECS", spec_name))
    for descriptor in bound_fds:
        os.set_inheritable(descriptor, True)

    def procfd(descriptor):
        return f"/proc/self/fd/{descriptor}"

    command = [
        rpmbuild_path,
        "--nodeps",
        "--define",
        f"_topdir {procfd(stage_fd)}",
        "--define",
        f"_builddir {procfd(build_fd)}",
        "--define",
        f"_buildrootdir {procfd(buildroot_fd)}",
        "--define",
        f"_rpmdir {procfd(rpms_fd)}",
        "--define",
        f"_sourcedir {procfd(sources_fd)}",
        "--define",
        f"_specdir {procfd(specs_fd)}",
        "--define",
        f"_srcrpmdir {procfd(srpms_fd)}",
        "--define",
        f"_tmppath {procfd(workspace_fd)}",
        "--define",
        "_smp_build_ncpus 1",
        "--define",
        f"__python3 {python_bin}",
        "--define",
        "py_auto_byte_compile 0",
        "--define",
        "__brp_python_bytecompile %{nil}",
        "--define",
        "__brp_python_hardlink %{nil}",
        "-ba",
        procfd(spec_fd),
    ]
    os.execv(rpmbuild_path, command)
finally:
    primary_error = sys.exc_info()[1]
    cleanup_errors = []
    while bound_fds:
        descriptor = bound_fds.pop()
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            cleanup_errors.append(cleanup_error)
    if cleanup_errors:
        if primary_error is not None:
            primary_error.add_note("build-rpm rpmbuild descriptor cleanup failed")
        else:
            raise SystemExit("build-rpm rpmbuild descriptor cleanup failed") from cleanup_errors[0]
PY
if (( rpmbuild_status != 0 )); then
  exit "${rpmbuild_status}"
fi

publish_workspace="${dist_dir}/.$(basename "${final_topdir}").publish-workspace-${BASHPID}"
publish_candidate="${publish_workspace}/candidate"
publish_previous="${final_topdir}.previous"
publish_recovery="${dist_dir}/.$(basename "${final_topdir}").previous-recovery-${BASHPID}"
finalization_result=""
if ! finalization_result="$(activate_with_finalize_lock \
  "${dist_finalize_lock}" "${stage_topdir}" "${final_topdir}" \
  "${publish_candidate}" "${publish_previous}" "${publish_recovery}" \
  "${stage_topdir_identity}" "${lifecycle_supervisor}")"; then
  printf 'failed to activate RPM build directory: %s\n' "${final_topdir}" >&2
  exit 1
fi
if (( ${#finalization_result} > 256 )) ||
    [[ ! "${finalization_result}" =~ ^([0-9]+:[0-9]+:[0-9]+)[[:space:]]+([0-9]+:[0-9]+:[0-9]+)[[:space:]]+([0-9]+:[0-9]+:[0-9]+)$ ]]; then
  printf 'invalid RPM finalization identity result\n' >&2
  exit 1
fi
final_topdir_identity="${BASH_REMATCH[1]}"
final_rpms_identity="${BASH_REMATCH[2]}"
final_srpms_identity="${BASH_REMATCH[3]}"
publish_committed=1
publish_workspace=""
publish_recovery=""
final_topdir_fd=""
if ! bind_directory_fd "${final_topdir}" "${final_topdir_identity}" \
  "final RPM build directory" final_topdir_fd; then
  exit 1
fi
# RPM output enumeration stays bounded while its process-level watchdog protects the exit trap.
output_status=0
timeout --foreground --signal=TERM --kill-after="${RPM_OUTPUT_KILL_AFTER_SECONDS}s" \
  "$((RPM_OUTPUT_TIMEOUT_SECONDS - RPM_OUTPUT_KILL_AFTER_SECONDS))s" \
  "${python_bin}" -I -B - "${final_topdir_fd}" "${final_topdir}" "${final_topdir_identity}" \
  "${final_rpms_identity}" "${final_srpms_identity}" <<'PY' || output_status=$?
import os
import stat
import sys

(
    final_fd_number,
    final_path,
    expected_final_identity,
    expected_rpms_identity,
    expected_srpms_identity,
) = sys.argv[1:]
final_fd = int(final_fd_number)

MAX_RPM_OUTPUT_ENTRIES = 256
MAX_RPM_OUTPUT_BYTES = 1 << 20
directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
scanned_entries = 0
output_bytes = 0
rpm_paths = []
expected_root_identities = {
    "RPMS": expected_rpms_identity,
    "SRPMS": expected_srpms_identity,
}


def scan_directory(directory_fd, directory_path):
    global output_bytes, scanned_entries
    with os.scandir(directory_fd) as entries:
        for entry in entries:
            scanned_entries += 1
            if scanned_entries > MAX_RPM_OUTPUT_ENTRIES:
                raise SystemExit(f"RPM output enumeration exceeds max {MAX_RPM_OUTPUT_ENTRIES} entries")
            path = os.path.join(directory_path, entry.name)
            entry_stat = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise SystemExit(f"RPM output contains symlink: {path}")
            if stat.S_ISDIR(entry_stat.st_mode):
                child_fd = os.open(entry.name, directory_flags, dir_fd=directory_fd)
                try:
                    child_stat = os.fstat(child_fd)
                    if (
                        not stat.S_ISDIR(child_stat.st_mode)
                        or f"{child_stat.st_dev}:{child_stat.st_ino}:{child_stat.st_mode}"
                        != f"{entry_stat.st_dev}:{entry_stat.st_ino}:{entry_stat.st_mode}"
                    ):
                        raise SystemExit(f"RPM output directory changed: {path}")
                    scan_directory(child_fd, path)
                finally:
                    primary_error = sys.exc_info()[1]
                    try:
                        os.close(child_fd)
                    except BaseException as cleanup_error:
                        if primary_error is not None:
                            primary_error.add_note("build-rpm output child descriptor cleanup failed")
                        else:
                            raise
            elif stat.S_ISREG(entry_stat.st_mode) and entry.name.endswith((".rpm", ".src.rpm")):
                current_stat = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                if (
                    f"{current_stat.st_dev}:{current_stat.st_ino}:{current_stat.st_mode}"
                    != f"{entry_stat.st_dev}:{entry_stat.st_ino}:{entry_stat.st_mode}"
                ):
                    raise SystemExit(f"RPM output file changed: {path}")
                output_bytes_for_path = len(os.fsencode(path)) + 1
                if output_bytes + output_bytes_for_path > MAX_RPM_OUTPUT_BYTES:
                    raise SystemExit(f"RPM output enumeration exceeds max {MAX_RPM_OUTPUT_BYTES} bytes")
                output_bytes += output_bytes_for_path
                rpm_paths.append(path)


def check_final_directory():
    descriptor_stat = os.fstat(final_fd)
    path_stat = os.stat(final_path, follow_symlinks=False)
    if (
        not stat.S_ISDIR(descriptor_stat.st_mode)
        or f"{descriptor_stat.st_dev}:{descriptor_stat.st_ino}:{descriptor_stat.st_mode}" != expected_final_identity
        or f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"
        != f"{descriptor_stat.st_dev}:{descriptor_stat.st_ino}:{descriptor_stat.st_mode}"
    ):
        raise SystemExit(f"RPM final build directory changed during output enumeration: {final_path}")


def check_output_roots():
    for root_name, expected_root_identity in expected_root_identities.items():
        root = os.path.join(final_path, root_name)
        try:
            root_stat = os.stat(root_name, dir_fd=final_fd, follow_symlinks=False)
        except OSError as exc:
            raise SystemExit(f"RPM output root changed during output enumeration: {root}") from exc
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or f"{root_stat.st_dev}:{root_stat.st_ino}:{root_stat.st_mode}"
            != expected_root_identity
        ):
            raise SystemExit(f"RPM output root changed during output enumeration: {root}")


# Same-UID content mutation or leaf swap after final check cannot be authenticated.
check_final_directory()
check_output_roots()
for root_name in ("RPMS", "SRPMS"):
    root = os.path.join(final_path, root_name)
    path_stat = os.stat(root_name, dir_fd=final_fd, follow_symlinks=False)
    if not stat.S_ISDIR(path_stat.st_mode):
        raise SystemExit(f"RPM output root is not a directory: {root}")
    root_fd = os.open(root_name, directory_flags, dir_fd=final_fd)
    try:
        root_stat = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_stat.st_mode)
            or f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"
            != f"{root_stat.st_dev}:{root_stat.st_ino}:{root_stat.st_mode}"
        ):
            raise SystemExit(f"RPM output root is not a directory: {root}")
        scan_directory(root_fd, root)
        path_stat = os.stat(root_name, dir_fd=final_fd, follow_symlinks=False)
        if f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}" != f"{root_stat.st_dev}:{root_stat.st_ino}:{root_stat.st_mode}":
            raise SystemExit(f"RPM output root changed during enumeration: {root}")
    finally:
        primary_error = sys.exc_info()[1]
        try:
            os.close(root_fd)
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note("build-rpm output root descriptor cleanup failed")
            else:
                raise

check_final_directory()
check_output_roots()
for path in sorted(rpm_paths):
    print(path, flush=True)
check_output_roots()
check_final_directory()
PY

exec {final_topdir_fd}<&-
final_topdir_fd=""

stage_cleanup_status=0
run_cleanup_safe_fs remove build-rpm "${stage_topdir}" --kind dir \
  --expected-identity "${stage_topdir_identity}" || stage_cleanup_status=$?
stage_cleanup_invoked="${cleanup_safe_fs_invoked}"
if (( stage_cleanup_status == 0 )); then
  stage_topdir=""
  stage_topdir_identity=""
else
  stage_cleanup_unconfirmed=1
  if (( cleanup_stage_message_emitted == 0 )); then
    if (( stage_cleanup_invoked == 1 )); then
      printf 'RPM stage cleanup failed after commit and safe-FS invocation; safe-FS residue report is authoritative: %s\n' \
        "${stage_topdir}" >&2
    else
      printf 'RPM stage cleanup after commit not attempted; safe-FS was not invoked; residue path retained: %s\n' \
        "${stage_topdir}" >&2
    fi
    cleanup_stage_message_emitted=1
  fi
fi
if (( output_status != 0 )); then
  exit "${output_status}"
fi
