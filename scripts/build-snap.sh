#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin:/var/lib/snapd/snap/bin"
export PATH="${TRUSTED_COMMAND_PATH}"
readonly SNAPCRAFT_TIMEOUT_SECONDS=3600
readonly SNAP_FINALIZE_TIMEOUT_SECONDS=120
readonly SNAP_FINALIZE_KILL_AFTER_SECONDS=10
readonly SNAP_FINALIZE_LOCK_TIMEOUT_SECONDS=30
readonly LXD_PROBE_TIMEOUT_SECONDS=30
readonly SNAP_CLEANUP_TIMEOUT_SECONDS=30
readonly SNAP_STARTUP_SWEEP_TIMEOUT_SECONDS=30
readonly SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS=1
readonly SNAP_STARTUP_SWEEP_MAX_ENTRIES=256
readonly SNAP_STARTUP_SWEEP_MAX_STALE=32
readonly SNAP_SCAN_MAX_ENTRIES=4096
readonly SNAP_SCAN_MAX_CANDIDATES=2
readonly SNAP_SCAN_MAX_CANDIDATE_PATH_BYTES=4096
readonly SNAP_SCAN_MAX_CANDIDATE_TOTAL_BYTES=16384

if ((
  SNAP_FINALIZE_TIMEOUT_SECONDS <= 0
  || SNAP_FINALIZE_KILL_AFTER_SECONDS <= 0
  || SNAP_FINALIZE_KILL_AFTER_SECONDS >= SNAP_FINALIZE_TIMEOUT_SECONDS
)); then
  printf 'invalid Snap finalizer watchdog budget\n' >&2
  exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "${repo_dir}"
snapcraft_base="${SNAPCRAFT_BASE:-core22}"
if [[ ! "${snapcraft_base}" =~ ^[a-z][a-z0-9-]*$ ]]; then
  printf 'invalid SNAPCRAFT_BASE value: %s\n' "${snapcraft_base}" >&2
  exit 1
fi
snapcraft_mode="${SNAPCRAFT_MODE:-auto}"
case "${snapcraft_mode}" in
  auto|destructive|lxd) ;;
  *)
    printf 'invalid SNAPCRAFT_MODE value: %s (expected auto, destructive, or lxd)\n' "${snapcraft_mode}" >&2
    exit 1
    ;;
esac

host_id=""
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  host_id="${ID:-}"
fi
if [[ "${snapcraft_mode}" == "auto" ]]; then
  if [[ "${host_id}" == "ubuntu" ]]; then
    snapcraft_mode="destructive"
  elif command -v -- lxc >/dev/null 2>&1 && timeout --signal=TERM --kill-after=5s "${LXD_PROBE_TIMEOUT_SECONDS}s" lxc info >/dev/null 2>&1; then
    snapcraft_mode="lxd"
  else
    printf 'Snap build needs Ubuntu destructive mode or LXD on non-Ubuntu hosts; install LXD or set up an Ubuntu builder.\n' >&2
    exit 1
  fi
fi
if [[ "${snapcraft_mode}" == "destructive" && "${host_id}" != "ubuntu" ]]; then
  printf 'SNAPCRAFT_MODE=destructive is supported only on Ubuntu; use SNAPCRAFT_MODE=lxd on this host.\n' >&2
  exit 1
fi
if [[ "${snapcraft_mode}" == "lxd" ]]; then
  require_lxc="true"
  snapcraft_args=(--use-lxd)
else
  require_lxc="false"
  snapcraft_args=(--destructive-mode)
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

activate_snap_output() {
  local lock_path=$1
  local staging_path=$2
  local final_path=$3
  local previous_path=$4
  local previous_recovery_path=$5

  timeout --signal=TERM \
    --kill-after="${SNAP_FINALIZE_KILL_AFTER_SECONDS}s" \
    "$((SNAP_FINALIZE_TIMEOUT_SECONDS - SNAP_FINALIZE_KILL_AFTER_SECONDS))s" \
    python3 - "$lock_path" "$safe_fs" "$staging_path" "$final_path" "$previous_path" "$previous_recovery_path" "$SNAP_FINALIZE_TIMEOUT_SECONDS" "$SNAP_FINALIZE_LOCK_TIMEOUT_SECONDS" <<'PY'
import os
import re
import stat
import subprocess
import sys
import time

try:
    import fcntl
except ModuleNotFoundError:
    print("fcntl is required for safe snap finalization", file=sys.stderr)
    raise SystemExit(1)

lock_path, safe_fs, staging_path, final_path, previous_path, previous_recovery_path, finalize_timeout, lock_timeout = sys.argv[1:]
finalize_timeout_seconds = int(finalize_timeout)
lock_timeout_seconds = int(lock_timeout)
finalizer_deadline = time.monotonic() + finalize_timeout_seconds
lock_parent = os.path.dirname(lock_path)
lock_name = os.path.basename(lock_path)


def _run_safe_fs(*arguments):
    remaining = finalizer_deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError("snap finalization deadline exceeded")
    try:
        subprocess.run(
            [sys.executable, safe_fs, *arguments],
            check=True,
            timeout=remaining,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            "snap finalization deadline exceeded during safe filesystem operation"
        ) from exc
    if finalizer_deadline - time.monotonic() <= 0:
        raise RuntimeError("snap finalization deadline exceeded after safe filesystem operation")


def _safe_fs_identity(path_stat):
    return f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"


def _directory_identity(path_stat):
    return (path_stat.st_dev, path_stat.st_ino, path_stat.st_mode)


def _validate_private_directory(path_stat, label):
    if not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a directory: {lock_parent}")
    if path_stat.st_uid != os.geteuid() or path_stat.st_mode & 0o022:
        raise RuntimeError(f"{label} must be private and owned by the current user: {lock_parent}")


def _revalidate_lock_parent(parent_fd, expected_identity):
    locked_stat = os.fstat(parent_fd)
    _validate_private_directory(locked_stat, "snap finalization lock parent")
    if _directory_identity(locked_stat) != expected_identity:
        raise RuntimeError(f"snap finalization lock parent descriptor changed: {lock_parent}")
    try:
        path_stat = os.stat(lock_parent, follow_symlinks=False)
    except OSError as exc:
        raise RuntimeError(f"failed to revalidate snap finalization lock parent: {lock_parent}") from exc
    _validate_private_directory(path_stat, "snap finalization lock parent path")
    if _directory_identity(path_stat) != _directory_identity(locked_stat):
        raise RuntimeError(f"snap finalization lock parent path changed: {lock_parent}")


def _lstat_named(parent_fd, name):
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _validate_regular_file(path_stat, label, path):
    if path_stat is None:
        raise RuntimeError(f"{label} is missing: {path}")
    if stat.S_ISLNK(path_stat.st_mode):
        raise RuntimeError(f"{label} must not be a symlink: {path}")
    if not stat.S_ISREG(path_stat.st_mode):
        raise RuntimeError(f"{label} must be a regular file: {path}")
    if path_stat.st_uid != os.geteuid() or path_stat.st_mode & 0o022:
        raise RuntimeError(f"{label} must be owned and not group/other writable: {path}")
    if getattr(path_stat, "st_nlink", 1) != 1:
        raise RuntimeError(f"{label} must not be hardlinked: {path}")


def _regular_file_named(parent_fd, name, label, *, required):
    path = os.path.join(lock_parent, name)
    path_stat = _lstat_named(parent_fd, name)
    if path_stat is None:
        if required:
            raise RuntimeError(f"{label} is missing: {path}")
        return None
    _validate_regular_file(path_stat, label, path)
    return path_stat


def _verified_recovery_path(parent_fd, path, expected_fs_identity):
    if expected_fs_identity is None:
        return None
    if os.path.dirname(path) != lock_parent:
        return None
    name = os.path.basename(path)
    claim_pattern = re.compile(rf"^{re.escape(name)}\.safe-delete-[0-9a-f]{{32}}$")

    def matching_identity(candidate_name, candidate_path):
        try:
            current = _lstat_named(parent_fd, candidate_name)
            if current is None:
                return None
            _validate_regular_file(current, "snap recovery backup", candidate_path)
        except Exception:
            return None
        if _safe_fs_identity(current) != expected_fs_identity:
            return None
        return candidate_path

    verified_path = matching_identity(name, path)
    if verified_path is not None:
        return verified_path
    try:
        with os.scandir(parent_fd) as entries:
            scanned_entries = 0
            for entry in entries:
                scanned_entries += 1
                if scanned_entries > 256:
                    return None
                candidate_name = os.fsdecode(entry.name)
                if claim_pattern.fullmatch(candidate_name) is None:
                    continue
                verified_path = matching_identity(
                    candidate_name,
                    os.path.join(lock_parent, candidate_name),
                )
                if verified_path is not None:
                    return verified_path
    except Exception:
        return None
    return None


def _report_recovery_path(parent_fd, label, candidates):
    for path, expected_fs_identity in candidates:
        recovery_path = _verified_recovery_path(parent_fd, path, expected_fs_identity)
        if recovery_path is not None:
            print(f"{label}: {recovery_path}", file=sys.stderr)
            return
    print(f"{label}: no identity-verified recovery path available", file=sys.stderr)


if not lock_name:
    print(f"snap finalization lock path is invalid: {lock_path}", file=sys.stderr)
    raise SystemExit(1)

parent_flags = os.O_RDONLY
if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
    raise RuntimeError("snap finalization needs O_DIRECTORY and O_NOFOLLOW")
parent_flags |= os.O_DIRECTORY | os.O_NOFOLLOW
parent_flags |= getattr(os, "O_CLOEXEC", 0)
try:
    parent_fd = os.open(lock_parent, parent_flags)
except OSError as exc:
    print(f"failed to open snap finalization lock parent safely: {lock_parent}: {exc}", file=sys.stderr)
    raise SystemExit(1)

try:
    parent_stat = os.fstat(parent_fd)
    _validate_private_directory(parent_stat, "snap finalization lock parent")
    parent_identity = _directory_identity(parent_stat)
    _revalidate_lock_parent(parent_fd, parent_identity)
    lock_deadline = min(finalizer_deadline, time.monotonic() + lock_timeout_seconds)
    while True:
        try:
            fcntl.flock(parent_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except InterruptedError:
            now = time.monotonic()
            if now >= lock_deadline:
                if now >= finalizer_deadline:
                    raise RuntimeError("snap finalization deadline exceeded while waiting for lock")
                raise RuntimeError("snap finalization lock timed out")
            continue
        except BlockingIOError:
            now = time.monotonic()
            remaining = lock_deadline - now
            if remaining <= 0:
                if now >= finalizer_deadline:
                    raise RuntimeError("snap finalization deadline exceeded while waiting for lock")
                raise RuntimeError("snap finalization lock timed out")
            time.sleep(min(0.05, remaining))
    _revalidate_lock_parent(parent_fd, parent_identity)
    final_parent = os.path.dirname(final_path)
    final_name = os.path.basename(final_path)
    stage_name = os.path.basename(staging_path)
    previous_name = os.path.basename(previous_path)
    recovery_name = os.path.basename(previous_recovery_path)
    if final_parent != lock_parent:
        raise RuntimeError("snap finalization paths escaped lock parent")
    if (
        os.path.dirname(staging_path) != final_parent
        or os.path.dirname(previous_path) != final_parent
        or os.path.dirname(previous_recovery_path) != final_parent
        or not final_name
        or final_name in {".", ".."}
    ):
        raise RuntimeError("snap finalization path layout is invalid")

    token = r"[0-9a-f]{32}"
    stage_prefix = f".{final_name}.staging-"
    recovery_prefix = f".{final_name}.previous-recovery-"
    stage_pattern = re.compile(rf"^{re.escape(stage_prefix)}{token}$")
    stage_claim_pattern = re.compile(
        rf"^{re.escape(stage_prefix)}{token}\.safe-delete-{token}$"
    )
    previous_claim_pattern = re.compile(
        rf"^{re.escape(final_name + '.previous')}\.safe-delete-{token}$"
    )
    recovery_pattern = re.compile(rf"^{re.escape(recovery_prefix)}{token}$")
    recovery_claim_pattern = re.compile(
        rf"^{re.escape(recovery_prefix)}{token}\.safe-delete-{token}$"
    )
    legacy_backup_pattern = re.compile(
        rf"^{re.escape(final_name)}\.[0-9]+\.[0-9a-f]{{16}}\.backup"
        rf"(?:\.safe-delete-{token})?$"
    )
    if stage_pattern.fullmatch(stage_name) is None:
        raise RuntimeError(f"snap staging path name is invalid: {staging_path}")
    if previous_name != f"{final_name}.previous":
        raise RuntimeError(f"snap previous path is invalid: {previous_path}")
    if recovery_pattern.fullmatch(recovery_name) is None:
        raise RuntimeError(f"snap recovery path name is invalid: {previous_recovery_path}")

    MAX_STARTUP_SCAN_ENTRIES = 256
    MAX_STALE_STARTUP_ARTIFACTS = 32
    STARTUP_ARTIFACT_MAX_AGE_NS = 24 * 60 * 60 * 1_000_000_000
    stale = []
    previous_stat = None
    try:
        with os.scandir(parent_fd) as entries:
            scanned_entries = 0
            for entry in entries:
                scanned_entries += 1
                if scanned_entries > MAX_STARTUP_SCAN_ENTRIES:
                    raise RuntimeError(
                        f"snap publish artifact scan exceeds max {MAX_STARTUP_SCAN_ENTRIES}"
                    )
                name = os.fsdecode(entry.name)
                is_stage = stage_pattern.fullmatch(name) is not None
                is_stage_claim = stage_claim_pattern.fullmatch(name) is not None
                is_previous = name == previous_name
                is_previous_claim = previous_claim_pattern.fullmatch(name) is not None
                is_recovery = recovery_pattern.fullmatch(name) is not None
                is_recovery_claim = recovery_claim_pattern.fullmatch(name) is not None
                is_legacy_backup = legacy_backup_pattern.fullmatch(name) is not None
                if not (
                    is_stage
                    or is_stage_claim
                    or is_previous
                    or is_previous_claim
                    or is_recovery
                    or is_recovery_claim
                    or is_legacy_backup
                ):
                    continue
                path = os.path.join(final_parent, name)
                path_stat = _lstat_named(parent_fd, name)
                if path_stat is None:
                    raise RuntimeError(f"snap publish artifact disappeared: {path}")
                _validate_regular_file(path_stat, "snap publish artifact", path)
                if is_recovery or is_recovery_claim or is_legacy_backup:
                    raise RuntimeError(
                        f"snap previous recovery requires manual recovery: {path}"
                    )
                if is_previous:
                    previous_stat = path_stat
                    continue
                if is_stage and name == stage_name:
                    continue
                age_ns = time.time_ns() - path_stat.st_mtime_ns
                if age_ns < STARTUP_ARTIFACT_MAX_AGE_NS:
                    raise RuntimeError(
                        f"recent snap publish artifact requires recovery: {path}"
                    )
                if len(stale) >= MAX_STALE_STARTUP_ARTIFACTS:
                    raise RuntimeError(
                        f"snap publish artifact sweep exceeds max {MAX_STALE_STARTUP_ARTIFACTS}"
                    )
                stale.append(
                    (
                        path,
                        _safe_fs_identity(path_stat),
                    )
                )
    except FileNotFoundError as exc:
        raise RuntimeError(f"snap publish parent is missing: {final_parent}") from exc

    for stale_path, stale_fs_identity in stale:
        stale_name = os.path.basename(stale_path)
        current_stale = _lstat_named(parent_fd, stale_name)
        if current_stale is None or _safe_fs_identity(current_stale) != stale_fs_identity:
            raise RuntimeError(f"snap publish artifact identity changed before cleanup: {stale_path}")
        print(f"removing stale snap publish artifact: {stale_path}", file=sys.stderr)
        _run_safe_fs(
            "remove-leaf",
            "build-snap",
            stale_path,
            "--expected-identity",
            stale_fs_identity,
        )

    recovery_identity = None
    if _lstat_named(parent_fd, recovery_name) is not None:
        raise RuntimeError(
            f"snap previous recovery requires manual recovery: {previous_recovery_path}"
        )
    if previous_stat is not None:
        previous_fs_identity = _safe_fs_identity(previous_stat)
        _run_safe_fs(
            "replace",
            "build-snap",
            previous_path,
            previous_recovery_path,
            "--src-kind",
            "file",
            "--dst-must-not-exist",
            "--expected-src-identity",
            previous_fs_identity,
        )
        recovery_stat = _regular_file_named(
            parent_fd,
            recovery_name,
            "created snap recovery backup",
            required=True,
        )
        recovery_identity = _safe_fs_identity(recovery_stat)
        if recovery_identity != previous_fs_identity:
            raise RuntimeError(
                f"snap recovery identity did not match previous output: {previous_recovery_path}"
            )

    staging_stat = _regular_file_named(parent_fd, stage_name, "staged snap", required=True)
    final_stat = _regular_file_named(parent_fd, final_name, "existing snap output", required=False)
    staging_fs_identity = _safe_fs_identity(staging_stat)
    final_fs_identity = _safe_fs_identity(final_stat) if final_stat is not None else None
    if final_stat is None:
        try:
            _run_safe_fs(
                "replace",
                "build-snap",
                staging_path,
                final_path,
                "--src-kind",
                "file",
                "--dst-must-not-exist",
                "--expected-src-identity",
                staging_fs_identity,
            )
        except BaseException:
            _report_recovery_path(
                parent_fd,
                "snap first publish recovery",
                ((staging_path, staging_fs_identity), (final_path, staging_fs_identity)),
            )
            raise
        final_after_publish = _regular_file_named(
            parent_fd,
            final_name,
            "published snap output",
            required=True,
        )
        if _safe_fs_identity(final_after_publish) != staging_fs_identity:
            raise RuntimeError(f"snap first publish identity changed: {final_path}")
    else:
        try:
            _run_safe_fs(
                "exchange",
                "build-snap",
                staging_path,
                final_path,
                "--kind",
                "file",
                "--expected-source-identity",
                staging_fs_identity,
                "--expected-target-identity",
                final_fs_identity,
            )
        except BaseException:
            _report_recovery_path(
                parent_fd,
                "snap exchange recovery",
                (
                    (staging_path, final_fs_identity),
                    (staging_path, staging_fs_identity),
                    (final_path, staging_fs_identity),
                ),
            )
            raise
        old_final_stat = _regular_file_named(
            parent_fd,
            stage_name,
            "exchanged snap output",
            required=True,
        )
        old_final_fs_identity = _safe_fs_identity(old_final_stat)
        if old_final_fs_identity != final_fs_identity:
            raise RuntimeError(f"snap exchange old final identity changed: {staging_path}")
        try:
            _run_safe_fs(
                "replace",
                "build-snap",
                staging_path,
                previous_path,
                "--src-kind",
                "file",
                "--dst-must-not-exist",
                "--expected-src-identity",
                old_final_fs_identity,
            )
        except BaseException:
            _report_recovery_path(
                parent_fd,
                "snap exchange recovery",
                ((previous_path, old_final_fs_identity), (staging_path, old_final_fs_identity)),
            )
            raise
        previous_after_publish = _regular_file_named(
            parent_fd,
            previous_name,
            "published snap previous output",
            required=True,
        )
        if _safe_fs_identity(previous_after_publish) != old_final_fs_identity:
            raise RuntimeError(f"snap previous identity changed: {previous_path}")

    if recovery_identity is not None:
        # Cleanup failure leaves new snap active; never risk rollback after commit.
        try:
            _run_safe_fs(
                "remove-leaf",
                "build-snap",
                previous_recovery_path,
                "--expected-identity",
                recovery_identity,
            )
        except Exception:
            recovery_path = _verified_recovery_path(
                parent_fd,
                previous_recovery_path,
                recovery_identity,
            )
            if recovery_path is not None:
                print(
                    f"warning: active snap output preserved; identity-verified previous recovery path: {recovery_path}",
                    file=sys.stderr,
                )
            else:
                print(
                    "warning: active snap output preserved; no identity-verified previous recovery path available",
                    file=sys.stderr,
                )
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(parent_fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-snap finalization descriptor cleanup failed")
        else:
            raise SystemExit("build-snap finalization descriptor cleanup failed") from cleanup_error
PY
}

for tool in python3 snapcraft timeout cp mktemp mkdir find realpath stat chmod grep basename; do
  require_cmd "${tool}"
done
if [[ "${require_lxc}" == "true" ]]; then
  require_cmd lxc
fi
snap_dir="${repo_dir}/snap"
safe_fs="${repo_dir}/scripts/safe-local-fs.py"
safe_fs_cmd=(python3 "${safe_fs}")

if ! timeout --signal=TERM --kill-after=30s "${SNAPCRAFT_TIMEOUT_SECONDS}s" snapcraft --version >/dev/null 2>&1; then
  printf 'snapcraft is installed but did not execute successfully.\n' >&2
  exit 1
fi

if [[ -L "${snap_dir}" ]]; then
  printf 'snap directory must not be a symlink: %s\n' "${snap_dir}" >&2
  exit 1
fi

if [[ ! -f "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest missing: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
if [[ -L "${snap_dir}/snapcraft.yaml" ]]; then
  printf 'snapcraft manifest must not be a symlink: %s\n' "${snap_dir}/snapcraft.yaml" >&2
  exit 1
fi
snapcraft_file="${snap_dir}/snapcraft.yaml"
require_regular_source_file "${snapcraft_file}" "snapcraft manifest"
require_regular_source_file "${safe_fs}" "safe local filesystem helper"
require_regular_source_file "${repo_dir}/pyproject.toml" "project metadata"
require_regular_source_file "${repo_dir}/README.md" "project README"
require_regular_source_file \
  "${repo_dir}/.github/requirements/ci-project.txt" \
  "project dependency lockfile"
require_regular_source_file \
  "${snap_dir}/local/requirements.txt" \
  "Snap dependency lockfile"
require_regular_source_file \
  "${snap_dir}/local/bin/speed-of-cinnamon" \
  "Snap command wrapper"

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
if [[ -z "${version}" || ! "${version}" =~ ^[0-9]+(\.[0-9]+){0,2}([0-9A-Za-z.+-]*)?$ ]]; then
  printf 'invalid project version: %s\n' "${version}" >&2
  exit 1
fi

validate_snap_tmp_root() {
  local requested_root=$1
  local root_abs
  local root_owner
  local root_mode
  local root_mode_bits
  if [[ ! "${requested_root}" == /* ]]; then
    printf 'temporary root must be an absolute path: %s\n' "${requested_root}" >&2
    return 1
  fi
  if [[ -L "${requested_root}" ]]; then
    printf 'temporary root must not be a symlink: %s\n' "${requested_root}" >&2
    return 1
  fi
  if [[ ! -d "${requested_root}" || ! -w "${requested_root}" ]]; then
    printf 'temporary root is not a writable directory: %s\n' "${requested_root}" >&2
    return 1
  fi
  if ! root_abs="$(realpath "${requested_root}")"; then
    printf 'failed to resolve temporary root: %s\n' "${requested_root}" >&2
    return 1
  fi
  if [[ "${root_abs}" == "${repo_dir}" || "${root_abs}" == "${repo_dir}/"* ]]; then
    printf 'snap temporary root must be outside repository: %s\n' "${requested_root}" >&2
    return 1
  fi
  root_owner="$(stat -c '%u' "${root_abs}")"
  root_mode="$(stat -c '%a' "${root_abs}")"
  root_mode_bits=$((8#${root_mode}))
  if [[ "${root_owner}" == "0" &&
        ( "${root_abs}" == "/tmp" || "${root_abs}" == "/var/tmp" ) ]] &&
      (( (root_mode_bits & 01000) != 0 && (root_mode_bits & 0002) != 0 )); then
    :
  elif [[ "${root_owner}" == "${EUID}" ]] &&
      (( (root_mode_bits & 0077) == 0 && (root_mode_bits & 0200) != 0 )); then
    :
  else
    printf 'temporary root must be euid-owned private or root-owned sticky standard temp: %s\n' "${requested_root}" >&2
    return 1
  fi
  repo_tmp_root="${root_abs}"
}

repo_tmp_root="${TMPDIR:-/tmp}"
if ! validate_snap_tmp_root "${repo_tmp_root}"; then
  exit 1
fi

snap_tmp_parent="${repo_tmp_root}/speed-of-cinnamon-snap-${EUID}"
if [[ -L "${snap_tmp_parent}" || ( -e "${snap_tmp_parent}" && ! -d "${snap_tmp_parent}" ) ]]; then
  printf 'snap temporary parent must be a private directory: %s\n' "${snap_tmp_parent}" >&2
  exit 1
fi
if [[ ! -e "${snap_tmp_parent}" ]]; then
  if ! "${safe_fs_cmd[@]}" mkdirs build-snap "${snap_tmp_parent}"; then
    printf 'failed to create private snap temporary parent: %s\n' "${snap_tmp_parent}" >&2
    exit 1
  fi
fi
if [[ -L "${snap_tmp_parent}" || ! -d "${snap_tmp_parent}" ]]; then
  printf 'snap temporary parent is not a directory: %s\n' "${snap_tmp_parent}" >&2
  exit 1
fi
if ! snap_tmp_parent_abs="$(realpath "${snap_tmp_parent}")"; then
  printf 'failed to resolve private snap temporary parent: %s\n' "${snap_tmp_parent}" >&2
  exit 1
fi
if [[ "${snap_tmp_parent_abs}" != "${snap_tmp_parent}" ]]; then
  printf 'private snap temporary parent path changed: %s\n' "${snap_tmp_parent}" >&2
  exit 1
fi
snap_tmp_parent="${snap_tmp_parent_abs}"
snap_tmp_parent_owner="$(stat -c '%u' "${snap_tmp_parent}")"
snap_tmp_parent_mode="$(stat -c '%a' "${snap_tmp_parent}")"
snap_tmp_parent_mode_bits=$((8#${snap_tmp_parent_mode}))
if [[ "${snap_tmp_parent_owner}" != "${EUID}" ]] ||
    (( (snap_tmp_parent_mode_bits & 0077) != 0 || (snap_tmp_parent_mode_bits & 0200) == 0 )); then
  printf 'private snap temporary parent is unsafe: %s\n' "${snap_tmp_parent}" >&2
  exit 1
fi

if ! timeout --signal=TERM --kill-after="${SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS}s" \
    "$((SNAP_STARTUP_SWEEP_TIMEOUT_SECONDS - SNAP_STARTUP_SWEEP_KILL_AFTER_SECONDS))s" \
    python3 - "${snap_tmp_parent}" "${safe_fs}" \
    "${SNAP_STARTUP_SWEEP_TIMEOUT_SECONDS}" \
    "${SNAP_STARTUP_SWEEP_MAX_ENTRIES}" \
    "${SNAP_STARTUP_SWEEP_MAX_STALE}" <<'PY'
import fcntl
import os
import re
import stat
import subprocess
import sys
import time

tmp_parent, safe_fs, timeout_seconds, max_entries, max_stale = sys.argv[1:]
timeout_seconds = int(timeout_seconds)
max_entries = int(max_entries)
max_stale = int(max_stale)
deadline = time.monotonic() + timeout_seconds
workspace_pattern = re.compile(r"^speed-of-cinnamon-snap-tree-[A-Za-z0-9]{6}$")
claim_pattern = re.compile(
    r"^speed-of-cinnamon-snap-tree-[A-Za-z0-9]{6}\.safe-delete-[0-9a-f]{32}$"
)
tombstone_pattern = re.compile(
    r"^\.?speed-of-cinnamon-snap-tree-[A-Za-z0-9]{6}"
    r"(?:\.final-[0-9a-f]{32})+$"
)


def remaining_timeout():
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("snap temporary workspace sweep deadline exceeded")
    return remaining


if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
    raise SystemExit("snap temporary workspace sweep needs O_DIRECTORY and O_NOFOLLOW")
parent_fd = os.open(
    tmp_parent,
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
        raise SystemExit(f"snap temporary parent is not private: {tmp_parent}")
    path_stat = os.stat(tmp_parent, follow_symlinks=False)
    if (
        path_stat.st_dev != parent_stat.st_dev
        or path_stat.st_ino != parent_stat.st_ino
        or path_stat.st_mode != parent_stat.st_mode
    ):
        raise SystemExit(f"snap temporary parent changed before lock: {tmp_parent}")
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
    path_stat = os.stat(tmp_parent, follow_symlinks=False)
    if (
        path_stat.st_dev != parent_stat.st_dev
        or path_stat.st_ino != parent_stat.st_ino
        or path_stat.st_mode != parent_stat.st_mode
    ):
        raise SystemExit(f"snap temporary parent changed after lock: {tmp_parent}")

    stale = []
    with os.scandir(parent_fd) as entries:
        scanned_entries = 0
        for entry in entries:
            scanned_entries += 1
            if scanned_entries > max_entries:
                raise SystemExit(f"snap temporary workspace scan exceeds max {max_entries}")
            name = os.fsdecode(entry.name)
            if (
                workspace_pattern.fullmatch(name) is None
                and claim_pattern.fullmatch(name) is None
                and tombstone_pattern.fullmatch(name) is None
            ):
                continue
            path = os.path.join(tmp_parent, name)
            path_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(path_stat.st_mode)
                or stat.S_ISLNK(path_stat.st_mode)
                or path_stat.st_uid != os.geteuid()
                or path_stat.st_mode & 0o077
                or getattr(path_stat, "st_nlink", 1) < 1
            ):
                raise SystemExit(f"unresolved snap temporary workspace: {path}")
            age_ns = time.time_ns() - path_stat.st_mtime_ns
            if age_ns < 24 * 60 * 60 * 1_000_000_000:
                raise SystemExit(f"recent snap temporary workspace requires recovery: {path}")
            if len(stale) >= max_stale:
                raise SystemExit(f"snap temporary workspace sweep exceeds max {max_stale}")
            stale.append((path, f"{path_stat.st_dev}:{path_stat.st_ino}:{path_stat.st_mode}"))

    remaining_timeout()
    for path, expected_identity in stale:
        name = os.path.basename(path)
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        current_identity = f"{current.st_dev}:{current.st_ino}:{current.st_mode}"
        if current_identity != expected_identity:
            raise SystemExit(f"snap temporary workspace identity changed before cleanup: {path}")
        subprocess.run(
            [
                sys.executable,
                safe_fs,
                "remove",
                "build-snap",
                path,
                "--kind",
                "dir",
                "--expected-identity",
                expected_identity,
            ],
            check=True,
            timeout=remaining_timeout(),
        )
        remaining_timeout()
finally:
    os.close(parent_fd)
PY
then
  printf 'failed to sweep snap temporary workspaces safely.\n' >&2
  exit 1
fi

snap_workspace="$(mktemp -d "${snap_tmp_parent}/speed-of-cinnamon-snap-tree-XXXXXX")"
if [[ -L "${snap_workspace}" ]]; then
  printf 'temporary snap workspace must not be a symlink: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! snap_workspace_abs="$(realpath "${snap_workspace}")"; then
  printf 'failed to resolve temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if [[ "${snap_workspace_abs}" != "${snap_tmp_parent}/speed-of-cinnamon-snap-tree-"* ]]; then
  printf 'temporary snap workspace escaped temporary root: %s\n' "${snap_workspace}" >&2
  exit 1
fi
snap_workspace="${snap_workspace_abs}"
snap_workspace_owner="$(stat -c '%u' "${snap_workspace}")"
snap_workspace_mode="$(stat -c '%a' "${snap_workspace}")"
snap_workspace_mode_bits=$((8#${snap_workspace_mode}))
if [[ "${snap_workspace_owner}" != "${EUID}" ]] ||
    (( (snap_workspace_mode_bits & 0077) != 0 || (snap_workspace_mode_bits & 0200) == 0 )); then
  printf 'temporary snap workspace is unsafe: %s\n' "${snap_workspace}" >&2
  exit 1
fi
snapcraft_file_rendered="${snap_workspace}/snap/snapcraft.yaml"
snap_source="${snap_workspace}/.snap-source"
snap_source_staging="${snap_workspace}/.snap-source.staging"
snap_source_staging_identity=""
snap_workspace_dist="${snap_workspace}/dist/snap"
snap_workspace_identity=""
snap_stage_path=""
snap_stage_identity=""
cleanup_deadline_us=0
run_cleanup_safe_fs() {
  local now_us
  local remaining_us
  local remaining_seconds
  local status=0
  now_us="${EPOCHREALTIME/./}"
  remaining_us=$((cleanup_deadline_us - now_us))
  if (( remaining_us <= 0 )); then
    printf 'snap EXIT cleanup deadline exceeded before safe-FS call\n' >&2
    return 124
  fi
  printf -v remaining_seconds '%d.%06d' "$((remaining_us / 1000000))" "$((remaining_us % 1000000))"
  timeout --signal=KILL "${remaining_seconds}s" "${safe_fs_cmd[@]}" "$@" || status=$?
  now_us="${EPOCHREALTIME/./}"
  if (( now_us >= cleanup_deadline_us )); then
    printf 'snap EXIT cleanup deadline exceeded\n' >&2
    return 124
  fi
  return "${status}"
}

report_cleanup_path_if_identity() {
  local path=$1
  local expected_identity=$2
  local kind=$3
  local label=$4
  local actual_identity
  if actual_identity="$(run_cleanup_safe_fs identity build-snap "${path}" --kind "${kind}")" &&
      [[ "${actual_identity}" == "${expected_identity}" ]]; then
    printf '%s: %s\n' "${label}" "${path}" >&2
  else
    printf '%s; no identity-verified path available\n' "${label}" >&2
  fi
}

cleanup_tmpdir() {
  local primary_status=$?
  local cleanup_failed=0
  local cleanup_started_us="${EPOCHREALTIME/./}"
  cleanup_deadline_us=$((cleanup_started_us + SNAP_CLEANUP_TIMEOUT_SECONDS * 1000000))
  if [[ -n "${snap_stage_path}" && -n "${snap_stage_identity}" ]]; then
    if ! run_cleanup_safe_fs remove-leaf build-snap "${snap_stage_path}" \
      --expected-identity "${snap_stage_identity}"; then
      printf 'snap EXIT cleanup failed for staged output\n' >&2
      report_cleanup_path_if_identity \
        "${snap_stage_path}" "${snap_stage_identity}" file \
        'snap EXIT staged output residue'
      cleanup_failed=1
    fi
  elif [[ -n "${snap_stage_path}" ]]; then
    printf 'refusing snap staged output cleanup without verified identity; no identity-verified path available\n' >&2
    cleanup_failed=1
  fi
  if [[ -n "${snap_workspace}" && -n "${snap_workspace_identity}" ]]; then
    if ! run_cleanup_safe_fs remove build-snap "${snap_workspace}" --kind dir \
      --expected-identity "${snap_workspace_identity}"; then
      printf 'snap EXIT cleanup failed for workspace\n' >&2
      report_cleanup_path_if_identity \
        "${snap_workspace}" "${snap_workspace_identity}" dir \
        'snap EXIT workspace residue'
      cleanup_failed=1
    fi
  elif [[ -n "${snap_workspace}" ]]; then
    printf 'refusing snap workspace cleanup without verified identity; no identity-verified path available\n' >&2
    cleanup_failed=1
  fi
  if (( cleanup_failed )); then
    if (( primary_status == 0 )); then
      printf 'snap EXIT cleanup failed; final output status changed to failure\n' >&2
      exit 1
    fi
    printf 'snap EXIT cleanup also failed; preserving primary exit status: %d\n' "${primary_status}" >&2
  fi
  exit "${primary_status}"
}
trap cleanup_tmpdir EXIT

if ! snap_workspace_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_workspace}" --kind dir)"; then
  printf 'failed to capture temporary snap workspace identity: %s\n' "${snap_workspace}" >&2
  exit 1
fi

if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/snap" "${snap_workspace}/snap" "snap source tree"; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" mkdirs build-snap "${snap_source_staging}"; then
  printf 'failed to create private Snap source staging directory: %s\n' "${snap_source_staging}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" install-tree build-snap "${repo_dir}/src" "${snap_source_staging}/src" "Python source tree"; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
remove_python_bytecode_from_snap_source() {
  local candidate candidate_identity

  while IFS= read -r -d '' candidate; do
    if ! candidate_identity="$("${safe_fs_cmd[@]}" identity build-snap "${candidate}" --kind dir)"; then
      printf 'failed to capture bytecode directory identity: %s\n' "${candidate}" >&2
      return 1
    fi
    if ! "${safe_fs_cmd[@]}" remove build-snap "${candidate}" --kind dir \
      --expected-identity "${candidate_identity}"; then
      printf 'failed to remove bytecode directory: %s\n' "${candidate}" >&2
      return 1
    fi
  done < <(find "${snap_source_staging}/src" -type d -name "__pycache__" -print0)

  while IFS= read -r -d '' candidate; do
    if ! candidate_identity="$("${safe_fs_cmd[@]}" identity build-snap "${candidate}" --kind file)"; then
      printf 'failed to capture bytecode file identity: %s\n' "${candidate}" >&2
      return 1
    fi
    if ! "${safe_fs_cmd[@]}" remove-leaf build-snap "${candidate}" \
      --expected-identity "${candidate_identity}"; then
      printf 'failed to remove bytecode file: %s\n' "${candidate}" >&2
      return 1
    fi
  done < <(find "${snap_source_staging}/src" -type f \( -name "*.pyc" -o -name "*.pyo" \) -print0)
}
if ! remove_python_bytecode_from_snap_source; then
  printf 'failed to remove stale Python bytecode from Snap source tree.\n' >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${repo_dir}/pyproject.toml" "${snap_source_staging}/pyproject.toml" 0644; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${repo_dir}/README.md" "${snap_source_staging}/README.md" 0644; then
  printf 'failed to prepare temporary snap workspace: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" mkdirs build-snap "${snap_source_staging}/.github/requirements"; then
  printf 'failed to prepare temporary snap lockfile directory: %s\n' "${snap_workspace}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" copy-file build-snap \
  "${repo_dir}/.github/requirements/ci-project.txt" \
  "${snap_source_staging}/.github/requirements/ci-project.txt" 0644 --dst-must-not-exist; then
  printf 'failed to prepare temporary snap lockfile: %s\n' "${snap_workspace}" >&2
  exit 1
fi
python3 - "${snapcraft_file_rendered}" "${snapcraft_file_rendered}" "${version}" "${snapcraft_base}" <<'PYCODE'
import os
import pathlib
import secrets
import sys

path = pathlib.Path(sys.argv[1])
output_path = pathlib.Path(sys.argv[2])
version = sys.argv[3]
base = sys.argv[4]
MAX_SNAPCRAFT_TEMPLATE_BYTES = 1 << 20
with path.open("rb") as handle:
    payload = handle.read(MAX_SNAPCRAFT_TEMPLATE_BYTES + 1)
if len(payload) > MAX_SNAPCRAFT_TEMPLATE_BYTES:
    raise SystemExit("snapcraft manifest is too large")
text = payload.decode("utf-8")
out = []
replaced = False
base_replaced = False
for line in text.splitlines():
    if line.startswith("version:"):
        out.append(f"version: \"{version}\"")
        replaced = True
    elif line.startswith("base:"):
        out.append(f"base: {base}")
        base_replaced = True
    else:
        out.append(line)
if not replaced:
    raise SystemExit("snapcraft version field not found")
if not base_replaced:
    raise SystemExit("snapcraft base field not found")
payload = ("\n".join(out) + "\n").encode("utf-8")
parent_fd = os.open(output_path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
tmp_name = f".{output_path.name}.{secrets.token_hex(8)}.tmp"
fd = -1
try:
    fd = os.open(tmp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent_fd)
    with os.fdopen(fd, "wb", closefd=True) as handle:
        fd = -1
        handle.write(payload)
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        os.fsync(handle.fileno())
    os.replace(tmp_name, output_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
    tmp_name = ""
finally:
    primary_error = sys.exc_info()[1]
    cleanup_errors = []
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
    try:
        os.close(parent_fd)
    except BaseException as cleanup_error:
        cleanup_errors.append(cleanup_error)
    if cleanup_errors:
        if primary_error is not None:
            primary_error.add_note("build-snap manifest descriptor cleanup failed")
        else:
            raise SystemExit("build-snap manifest descriptor cleanup failed") from cleanup_errors[0]
PYCODE
snapcraft_mode="$(stat -c '%a' "${snapcraft_file}")"
if ! snapcraft_rendered_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snapcraft_file_rendered}" --kind file)"; then
  printf 'failed to capture rendered snapcraft manifest identity: %s\n' "${snapcraft_file_rendered}" >&2
  exit 1
fi
python3 - "${snapcraft_file_rendered}" "${snapcraft_mode}" "${snapcraft_rendered_identity}" <<'PY'
import os
import stat
import sys

path, mode_text, expected_identity = sys.argv[1:]
flags = os.O_RDWR
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
try:
    fd = os.open(path, flags)
except OSError as exc:
    print(f"failed to open rendered snapcraft manifest safely: {path}: {exc}", file=sys.stderr)
    raise SystemExit(1)
try:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode) or getattr(file_stat, "st_nlink", 1) != 1:
        print(f"rendered snapcraft manifest must be a private regular file: {path}", file=sys.stderr)
        raise SystemExit(1)
    actual_identity = f"{file_stat.st_dev}:{file_stat.st_ino}:{file_stat.st_mode}"
    if actual_identity != expected_identity:
        print(f"rendered snapcraft manifest changed before chmod: {path}", file=sys.stderr)
        raise SystemExit(1)
    os.fchmod(fd, int(mode_text, 8))
    os.fsync(fd)
finally:
    primary_error = sys.exc_info()[1]
    try:
        os.close(fd)
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note("build-snap chmod descriptor cleanup failed")
        else:
            raise SystemExit("build-snap chmod descriptor cleanup failed") from cleanup_error
PY

if ! "${safe_fs_cmd[@]}" install-tree build-snap \
  "${snap_workspace}/snap" "${snap_source_staging}/snap" \
  "rendered Snap source tree"; then
  printf 'failed to stage rendered Snap source tree: %s\n' "${snap_source_staging}" >&2
  exit 1
fi
if ! snap_source_staging_identity="$(
  "${safe_fs_cmd[@]}" identity build-snap "${snap_source_staging}" --kind dir
)"; then
  printf 'failed to capture Snap source staging identity: %s\n' "${snap_source_staging}" >&2
  exit 1
fi
if ! "${safe_fs_cmd[@]}" replace build-snap \
  "${snap_source_staging}" "${snap_source}" \
  --src-kind dir --dst-must-not-exist \
  --expected-src-identity "${snap_source_staging_identity}"; then
  printf 'failed to activate allowlisted Snap source: %s\n' "${snap_source}" >&2
  exit 1
fi
if [[ "$("${safe_fs_cmd[@]}" identity build-snap "${snap_source}" --kind dir)" != \
      "${snap_source_staging_identity}" ]]; then
  printf 'activated Snap source identity changed: %s\n' "${snap_source}" >&2
  exit 1
fi

if ! (
  cd "${snap_workspace}"
  umask 022
  timeout --signal=TERM --kill-after=30s "${SNAPCRAFT_TIMEOUT_SECONDS}s" snapcraft pack "${snapcraft_args[@]}"
); then
  printf 'snapcraft build failed.\n' >&2
  exit 1
fi

if ! "${safe_fs_cmd[@]}" remove build-snap "${snap_workspace_dist}" --kind dir --expected-identity missing; then
  printf 'failed to prepare temporary snap output directory: %s\n' "${snap_workspace_dist}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-snap "${snap_workspace_dist}"

dist_parent="${repo_dir}/dist"
if [[ -L "${dist_parent}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_parent}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-snap "${dist_parent}"
if [[ -L "${dist_parent}" ]]; then
  printf 'dist directory must not be a symlink: %s\n' "${dist_parent}" >&2
  exit 1
fi
dist_dir="${dist_parent}/snap"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi
"${safe_fs_cmd[@]}" mkdirs build-snap "${dist_dir}"
if [[ -L "${dist_dir}" ]]; then
  printf 'dist snap directory must not be a symlink: %s\n' "${dist_dir}" >&2
  exit 1
fi

if ! snap_file="$(
  python3 - \
    "${SNAP_SCAN_MAX_ENTRIES}" \
    "${SNAP_SCAN_MAX_CANDIDATES}" \
    "${SNAP_SCAN_MAX_CANDIDATE_PATH_BYTES}" \
    "${SNAP_SCAN_MAX_CANDIDATE_TOTAL_BYTES}" \
    "${version}" \
    "${snap_workspace}" \
    "${snap_workspace_dist}" <<'PY'
import os
import stat
import sys

max_entries, max_candidates, max_path_bytes, max_total_bytes = (
    int(value) for value in sys.argv[1:5]
)
max_candidates = min(max_candidates, 2)
version = sys.argv[5]
directories = sys.argv[6:]
if not directories:
    raise SystemExit("snap candidate scan needs at least one directory")
if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
    raise SystemExit("snap candidate scan needs O_DIRECTORY and O_NOFOLLOW")

prefix = f"speed-of-cinnamon_{version}_"
candidates = []
total_path_bytes = 0
scanned_entries = 0
for directory in directories:
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        directory_stat = os.fstat(directory_fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise SystemExit(f"snap candidate directory is not a directory: {directory}")
        if directory_stat.st_uid != os.geteuid() or directory_stat.st_mode & 0o022:
            raise SystemExit(f"snap candidate directory is not private: {directory}")
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                scanned_entries += 1
                if scanned_entries > max_entries:
                    raise SystemExit("snap candidate scan entry limit exceeded")
                name = os.fsdecode(entry.name)
                if not name.startswith(prefix) or not name.endswith(".snap"):
                    continue
                candidate_path = os.path.join(directory, name)
                encoded_path = os.fsencode(candidate_path)
                if len(encoded_path) > max_path_bytes:
                    raise SystemExit("snap candidate path length limit exceeded")
                if b"\n" in encoded_path or b"\r" in encoded_path:
                    raise SystemExit("snap candidate path contains a line break")
                total_path_bytes += len(encoded_path) + 1
                if total_path_bytes > max_total_bytes:
                    raise SystemExit("snap candidate path byte limit exceeded")
                try:
                    candidate_stat = os.stat(
                        name,
                        dir_fd=directory_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError as exc:
                    raise SystemExit("snap candidate changed during bounded scan") from exc
                if stat.S_ISLNK(candidate_stat.st_mode):
                    raise SystemExit("snap candidate must not be a symlink")
                if not stat.S_ISREG(candidate_stat.st_mode):
                    continue
                if getattr(candidate_stat, "st_nlink", 1) != 1:
                    raise SystemExit("snap candidate must not be hardlinked")
                if len(candidates) >= max_candidates:
                    raise SystemExit("snap candidate limit exceeded")
                candidates.append(candidate_path)
    finally:
        os.close(directory_fd)

if len(candidates) != 1:
    raise SystemExit(f"expected exactly one new snap package, found {len(candidates)}")
sys.stdout.write(candidates[0])
PY
)"; then
  printf 'failed to locate exactly one bounded snap package.\n' >&2
  exit 1
fi

path="${snap_file}"
if [[ -L "${path}" ]]; then
  printf 'snap package must not be a symlink: %s\n' "${path}" >&2
  exit 1
fi
absolute="$(realpath "${path}")"
if [[ "${absolute}" != "${snap_workspace}/speed-of-cinnamon_${version}_"* &&
      "${absolute}" != "${snap_workspace_dist}/speed-of-cinnamon_${version}_"* ]]; then
  printf 'snap package path is unexpected: %s\n' "${path}" >&2
  exit 1
fi
filename="$(basename "${path}")"
if [[ ! "${filename}" == "speed-of-cinnamon_${version}_"* ]]; then
  printf 'unexpected snap file name: %s\n' "${filename}" >&2
  exit 1
fi
if [[ ! -s "${path}" ]]; then
  printf 'snap package is empty: %s\n' "${path}" >&2
  exit 1
fi

snap_filename="$(basename "${snap_file}")"
snap_stage_path="${dist_dir}/.${snap_filename}.staging-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
if ! "${safe_fs_cmd[@]}" copy-file build-snap "${snap_file}" "${snap_stage_path}" 0644 --dst-must-not-exist; then
  printf 'failed to copy built snap into output filesystem: %s\n' "${snap_file}" >&2
  exit 1
fi
snap_stage_identity="$("${safe_fs_cmd[@]}" identity build-snap "${snap_stage_path}" --kind file)"
output_path="${dist_dir}/${snap_filename}"
snap_previous_path="${output_path}.previous"
snap_previous_recovery_path="${dist_dir}/.${snap_filename}.previous-recovery-$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
activate_snap_output "${dist_dir}/.build-snap.finalize.lock" "${snap_stage_path}" "${output_path}" \
  "${snap_previous_path}" "${snap_previous_recovery_path}"
snap_stage_path=""
snap_stage_identity=""
printf 'Built %s\n' "${output_path}" >&2
printf '%s\n' "${output_path}"
