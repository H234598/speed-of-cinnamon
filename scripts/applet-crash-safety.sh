#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'
readonly TRUSTED_COMMAND_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH="${TRUSTED_COMMAND_PATH}"

if [[ ${SOC_RUN_GUI_LIVE_TESTS:-0} != "1" ]]; then
	if [[ ${1:-} == "--install-guard-probe" ]]; then
		printf 'INSTALL_GUARD_FAIL ENV\n' >&2
		exit 2
	fi
	printf 'Refusing desktop crash-safety test. Set SOC_RUN_GUI_LIVE_TESTS=1 explicitly.\n' >&2
	exit 2
fi

namespace_guard_probe=0
dbus_probe=0
install_guard_probe=0
if [[ $# -eq 1 && $1 == "--namespace-guard-probe" ]]; then
	namespace_guard_probe=1
elif [[ $# -eq 1 && $1 == "--dbus-probe" ]]; then
	dbus_probe=1
elif [[ $# -eq 1 && $1 == "--install-guard-probe" ]]; then
	install_guard_probe=1
elif [[ $# -ne 0 ]]; then
	if [[ ${1:-} == --install-guard-probe* ]]; then
		printf 'INSTALL_GUARD_FAIL ARG\n' >&2
		exit 2
	fi
	printf 'applet-crash-safety: arguments are not accepted.\n' >&2
	exit 2
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
uuid="speed-of-cinnamon@H234598"
cycles="${APPLET_CRASH_SAFETY_CYCLES:-100}"
max_cycles=1000
reload_mode="${APPLET_CRASH_SAFETY_RELOAD_MODE:-module}"
display_number="90"
heartbeat_limit_ms="${APPLET_CRASH_SAFETY_HEARTBEAT_MS:-250}"
max_heartbeat_limit_ms=60000
dbus_timeout_seconds="${APPLET_CRASH_SAFETY_DBUS_TIMEOUT_SECONDS:-10}"
max_dbus_timeout_seconds=60
fault_injection="${APPLET_CRASH_SAFETY_FAULT_INJECTION:-0}"
force_gc="${APPLET_CRASH_SAFETY_FORCE_GC:-1}"
host_display=":70"
host_xauthority="/run/soc-auth/host.Xauthority"
nested_xauthority="/run/soc-auth/nested.Xauthority"
smoke_mode="${APPLET_CRASH_SAFETY_SMOKE_MODE:-none}"
bwrap_bin="/usr/bin/bwrap"
controller_root="/run/soc-controller"
test_root="/run/soc-work/session"
session_home="${test_root}/home"
runtime_dir="${test_root}/runtime"
config_dir="${test_root}/config"
cache_dir="${test_root}/cache"
data_dir="${session_home}/.local/share"
state_dir="${test_root}/state"
tmp_dir="${test_root}/tmp"
display_log="${test_root}/xephyr.log"
cinnamon_log="${test_root}/cinnamon.log"

if [[ ${namespace_guard_probe} != "1" ]]; then
	for tool in Xephyr dbus-daemon dconf gdbus timeout cinnamon ps sed awk date xprop rg python3 env stat tr id bwrap xauth; do
		if ! command -v -- "${tool}" >/dev/null 2>&1; then
			printf 'applet-crash-safety: required tool missing: %s\n' "${tool}" >&2
			exit 2
		fi
	done
fi
if [[ ${namespace_guard_probe} != "1" && (! -x ${bwrap_bin} || -L ${bwrap_bin}) ]]; then
	printf 'applet-crash-safety: private namespace contract is unavailable.\n' >&2
	exit 2
fi

validate_xauthority_files() {
	python3 - "$host_xauthority" "$nested_xauthority" <<'PY'
import os
import stat
import sys

entries = []
for path in sys.argv[1:]:
    if not path.startswith("/") or "\x00" in path:
        raise SystemExit(1)
    current = "/"
    for component in path.split("/"):
        if component in ("", "."):
            continue
        if component == "..":
            raise SystemExit(1)
        current = os.path.join(current, component)
        try:
            entry = os.lstat(current)
        except OSError:
            raise SystemExit(1)
        if stat.S_ISLNK(entry.st_mode):
            raise SystemExit(1)
    entry = os.lstat(path)
    parent = os.lstat(os.path.dirname(path))
    if (
        not stat.S_ISREG(entry.st_mode)
        or entry.st_uid != os.geteuid()
        or entry.st_nlink != 1
        or entry.st_mode & 0o077
        or not os.access(path, os.R_OK)
        or not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != os.geteuid()
        or parent.st_mode & 0o077
    ):
        raise SystemExit(1)
    entries.append(entry)
if os.path.samestat(entries[0], entries[1]):
    raise SystemExit(1)
PY
}

read_process_identity() {
	local pid="$1"
	local stat_line rest
	local -a fields
	[[ ${pid} =~ ^[1-9][0-9]*$ && -r "/proc/${pid}/stat" ]] || return 1
	stat_line="$(<"/proc/${pid}/stat")" || return 1
	rest="${stat_line##*) }"
	local IFS=' '
	read -r -a fields <<<"${rest}"
	((${#fields[@]} >= 20)) || return 1
	[[ ${fields[19]} =~ ^[1-9][0-9]*$ ]] || return 1
	printf '%s %s\n' "${fields[0]}" "${fields[19]}"
}

read_direct_probe_identity() {
	local pid="$1" expected_parent="$2" stat_line rest
	local -a fields
	[[ ${pid} =~ ^[1-9][0-9]*$ && ${expected_parent} =~ ^[1-9][0-9]*$ &&
		-r "/proc/${pid}/stat" ]] || return 1
	stat_line="$(<"/proc/${pid}/stat")" || return 1
	rest="${stat_line##*) }"
	local IFS=' '
	read -r -a fields <<<"${rest}"
	((${#fields[@]} >= 20)) || return 1
	[[ ${fields[1]} == "${expected_parent}" &&
		${fields[19]} =~ ^[1-9][0-9]*$ ]] || return 1
	printf '%s %s %s\n' "${fields[0]}" "${fields[1]}" "${fields[19]}"
}

owned_process_state() {
	local pid="$1"
	local expected_start="$2"
	local identity state start
	identity="$(read_process_identity "${pid}")" || return 1
	IFS=' ' read -r state start <<<"${identity}"
	[[ ${start} == "${expected_start}" ]] || return 1
	printf '%s\n' "${state}"
}

owned_process_parent_pid() {
	local pid="$1"
	local expected_start="$2"
	local stat_line rest
	local -a fields
	[[ ${pid} =~ ^[1-9][0-9]*$ && -r "/proc/${pid}/stat" ]] || return 1
	stat_line="$(<"/proc/${pid}/stat")" || return 1
	rest="${stat_line##*) }"
	local IFS=' '
	read -r -a fields <<<"${rest}"
	((${#fields[@]} >= 20)) || return 1
	[[ ${fields[1]} =~ ^[1-9][0-9]*$ && ${fields[19]} == "${expected_start}" ]] || return 1
	printf '%s\n' "${fields[1]}"
}

read_direct_child_identity() {
	local child_pid="$1"
	local parent_pid="$2"
	local parent_start="$3"
	local first_line second_line first_rest second_rest parent_state
	local -a first_fields second_fields
	[[ ${child_pid} =~ ^[1-9][0-9]*$ ]] || return 1
	parent_state="$(owned_process_state "${parent_pid}" "${parent_start}")" || return 1
	[[ ${parent_state} != "Z" && -r "/proc/${child_pid}/stat" ]] || return 1
	first_line="$(<"/proc/${child_pid}/stat")" || return 1
	first_rest="${first_line##*) }"
	local IFS=' '
	read -r -a first_fields <<<"${first_rest}"
	((${#first_fields[@]} >= 20)) || return 1
	[[ ${first_fields[1]} == "${parent_pid}" && ${first_fields[19]} =~ ^[1-9][0-9]*$ ]] || return 1
	parent_state="$(owned_process_state "${parent_pid}" "${parent_start}")" || return 1
	[[ ${parent_state} != "Z" && -r "/proc/${child_pid}/stat" ]] || return 1
	second_line="$(<"/proc/${child_pid}/stat")" || return 1
	second_rest="${second_line##*) }"
	read -r -a second_fields <<<"${second_rest}"
	((${#second_fields[@]} >= 20)) || return 1
	[[ ${second_fields[1]} == "${parent_pid}" &&
		${second_fields[19]} == "${first_fields[19]}" ]] || return 1
	printf '%s %s\n' "${second_fields[0]}" "${second_fields[19]}"
}

read_single_direct_child_identity() {
	local parent_pid="$1"
	local parent_start="$2"
	local children_line identity state start
	local -a children
	owned_process_state "${parent_pid}" "${parent_start}" >/dev/null || return 1
	[[ -r "/proc/${parent_pid}/task/${parent_pid}/children" ]] || return 1
	children_line="$(<"/proc/${parent_pid}/task/${parent_pid}/children")" || return 1
	local IFS=' '
	read -r -a children <<<"${children_line}"
	((${#children[@]} == 1)) || return 1
	identity="$(read_direct_child_identity "${children[0]}" "${parent_pid}" "${parent_start}")" || return 1
	IFS=' ' read -r state start <<<"${identity}"
	printf '%s %s %s\n' "${children[0]}" "${state}" "${start}"
}

wait_owned_process_reap() {
	local pid="$1"
	local expected_start="$2"
	local grace="$3"
	local owner_pid="$4"
	local state parent_pid deadline=$((SECONDS + grace))
	while ((SECONDS <= deadline)); do
		if ! state="$(owned_process_state "${pid}" "${expected_start}")"; then
			if [[ ! -e "/proc/${pid}" ]]; then
				wait "${pid}" >/dev/null 2>&1 || true
				return 0
			fi
			return 1
		fi
		[[ ${state} == "Z" ]] || return 1
		parent_pid="$(owned_process_parent_pid "${pid}" "${expected_start}")" || return 1
		[[ ${parent_pid} == "${owner_pid}" ]] || return 1
		wait "${pid}" >/dev/null 2>&1 || true
		[[ ! -e "/proc/${pid}" ]] && return 0
		sleep 0.05
	done
	return 1
}

signal_owned_process() {
	local pid="$1"
	local expected_start="$2"
	local signal_name="$3"
	python3 -I -S - "${pid}" "${expected_start}" "${signal_name}" <<'PY'
import os
import signal
import sys

pid = int(sys.argv[1])
expected_start = int(sys.argv[2])
signum = getattr(signal, sys.argv[3])


def identity():
    try:
        with open(f"/proc/{pid}/stat", "rb", buffering=0) as handle:
            fields = handle.read(65536).rsplit(b") ", 1)[1].split()
        return fields[0], int(fields[19])
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError, OSError):
        return None


before = identity()
if before is None or before[0] == b"Z" or before[1] != expected_start:
    raise SystemExit(0)
try:
    pidfd = os.pidfd_open(pid, 0)
except ProcessLookupError:
    raise SystemExit(0)
try:
    rebound = identity()
    if rebound is None or rebound[0] == b"Z" or rebound[1] != expected_start:
        raise SystemExit(0)
    signal.pidfd_send_signal(pidfd, signum, None, 0)
except ProcessLookupError:
    pass
finally:
    os.close(pidfd)
PY
}

terminate_owned_process() {
	local pid="$1"
	local expected_start="$2"
	local grace="${3:-4}"
	local state deadline parent_pid
	local owner_pid="${BASHPID}"
	local own_child=0
	[[ -n ${pid} && -n ${expected_start} ]] || return 0
	if ! state="$(owned_process_state "${pid}" "${expected_start}")"; then
		if [[ ! -e "/proc/${pid}" ]]; then
			return 0
		fi
		return 1
	fi
	parent_pid="$(owned_process_parent_pid "${pid}" "${expected_start}")" || return 1
	[[ ${parent_pid} == "${owner_pid}" ]] && own_child=1
	if [[ ${state} == "Z" ]]; then
		((own_child == 0)) && return 0
		wait_owned_process_reap "${pid}" "${expected_start}" "${grace}" "${owner_pid}"
		return
	fi
	signal_owned_process "${pid}" "${expected_start}" SIGTERM || return 1
	deadline=$((SECONDS + grace))
	while ((SECONDS <= deadline)); do
		if ! state="$(owned_process_state "${pid}" "${expected_start}")"; then
			if [[ ! -e "/proc/${pid}" ]]; then
				((own_child == 0)) || wait "${pid}" >/dev/null 2>&1 || true
				return 0
			fi
			return 1
		fi
		if [[ ${state} == "Z" ]]; then
			((own_child == 0)) && return 0
			wait_owned_process_reap "${pid}" "${expected_start}" "${grace}" "${owner_pid}"
			return
		fi
		sleep 0.05
	done
	signal_owned_process "${pid}" "${expected_start}" SIGKILL || return 1
	deadline=$((SECONDS + grace))
	while ((SECONDS <= deadline)); do
		if ! state="$(owned_process_state "${pid}" "${expected_start}")"; then
			if [[ ! -e "/proc/${pid}" ]]; then
				((own_child == 0)) || wait "${pid}" >/dev/null 2>&1 || true
				return 0
			fi
			return 1
		fi
		if [[ ${state} == "Z" ]]; then
			((own_child == 0)) && return 0
			wait_owned_process_reap "${pid}" "${expected_start}" "${grace}" "${owner_pid}"
			return
		fi
		sleep 0.05
	done
	return 1
}

if [[ ${namespace_guard_probe} != "1" ]] &&
	{ [[ ! ${cycles} =~ ^[1-9][0-9]*$ || ${#cycles} -gt 4 ]] ||
		((cycles > max_cycles)); }; then
	printf 'APPLET_CRASH_SAFETY_CYCLES must be an integer from 1 to %s.\n' "${max_cycles}" >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" && (${reload_mode} != "instance" && ${reload_mode} != "module") ]]; then
	printf 'APPLET_CRASH_SAFETY_RELOAD_MODE must be instance or module.\n' >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" && ! ${display_number} =~ ^[1-9][0-9]*$ ]]; then
	printf 'APPLET_CRASH_SAFETY_DISPLAY must be a positive display number.\n' >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" && ! ${host_display} =~ ^:[1-9][0-9]*$ ]]; then
	printf 'APPLET_CRASH_SAFETY_HOST_DISPLAY must be a nonzero local X11 display.\n' >&2
	exit 2
fi
nested_display=":${display_number}"
if [[ ${namespace_guard_probe} != "1" && ${host_display} == "${nested_display}" ]]; then
	printf 'Host and nested X11 displays must be different.\n' >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" ]] &&
	{ [[ ! ${heartbeat_limit_ms} =~ ^[1-9][0-9]*$ || ${#heartbeat_limit_ms} -gt 5 ]] ||
		((heartbeat_limit_ms > max_heartbeat_limit_ms)); }; then
	printf 'APPLET_CRASH_SAFETY_HEARTBEAT_MS must be an integer from 1 to %s.\n' "${max_heartbeat_limit_ms}" >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" ]] &&
	{ [[ ! ${dbus_timeout_seconds} =~ ^[1-9][0-9]*$ || ${#dbus_timeout_seconds} -gt 2 ]] ||
		((dbus_timeout_seconds > max_dbus_timeout_seconds)); }; then
	printf 'APPLET_CRASH_SAFETY_DBUS_TIMEOUT_SECONDS must be an integer from 1 to %s.\n' "${max_dbus_timeout_seconds}" >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" && ${smoke_mode} != "none" && ${smoke_mode} != "clipboard-popup" ]]; then
	printf 'APPLET_CRASH_SAFETY_SMOKE_MODE must be none or clipboard-popup.\n' >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" && ${fault_injection} != "0" && ${fault_injection} != "1" ]]; then
	printf 'APPLET_CRASH_SAFETY_FAULT_INJECTION must be 0 or 1.\n' >&2
	exit 2
fi
if [[ ${namespace_guard_probe} != "1" && ${force_gc} != "0" && ${force_gc} != "1" ]]; then
	printf 'APPLET_CRASH_SAFETY_FORCE_GC must be 0 or 1.\n' >&2
	exit 2
fi

verify_private_namespace() {
	python3 -I -S - <<'PY'
import os
import socket
import stat
import sys

def fail(code):
    print(f"NSG_FAIL {code}", file=sys.stderr)
    raise SystemExit(1)

PROC_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
proc_root = "/proc"

def process_parent(pid):
    try:
        descriptor = os.open(f"{proc_root}/{pid}/stat", PROC_FLAGS)
    except OSError:
        fail("NSG_PROC_STAT")
    try:
        line = os.read(descriptor, 65536)
    finally:
        os.close(descriptor)
    split = line.rfind(b") ")
    if split <= 0 or line[:split].split(maxsplit=1)[0] != str(pid).encode("ascii"):
        fail("NSG_PROC_PARSE")
    fields = line[split + 2:].split()
    if len(fields) < 2 or fields[0] not in (b"R", b"S", b"D", b"T", b"t", b"W", b"X", b"K", b"P", b"I", b"Z"):
        fail("NSG_PROC_PARSE")
    try:
        parent = int(fields[1], 10)
    except ValueError:
        fail("NSG_PROC_PARSE")
    if parent <= 0:
        fail("NSG_PROC_PARENT")
    return parent

def visible_private_process(pid, namespace_stat):
    process_path = f"{proc_root}/{pid}"
    try:
        entry = os.stat(process_path, follow_symlinks=False)
    except OSError:
        fail("NSG_PROC_VISIBILITY")
    if not stat.S_ISDIR(entry.st_mode):
        fail("NSG_PROC_VISIBILITY")
    for name, expected in namespace_stat.items():
        try:
            same = os.path.samestat(os.stat(f"{process_path}/ns/{name}"), expected)
        except OSError:
            fail("NSG_NAMESPACE")
        if not same:
            fail("NSG_NAMESPACE")

uid_map = open("/proc/self/uid_map", encoding="ascii").read().strip().splitlines()
if uid_map == ["0          0 4294967295"] or not uid_map:
    fail("NSG_UID_MAP")
if os.getpid() <= 1:
    fail("NSG_PID")
namespace_stat = {
    name: os.stat(f"{proc_root}/1/ns/{name}")
    for name in ("pid", "mnt", "user")
}
current_pid = os.getpid()
seen = set()
for _step in range(32):
    if current_pid <= 0 or current_pid in seen:
        fail("NSG_ANCESTOR")
    seen.add(current_pid)
    visible_private_process(current_pid, namespace_stat)
    if current_pid == 1:
        break
    current_pid = process_parent(current_pid)
else:
    fail("NSG_ANCESTOR_LIMIT")
if current_pid != 1:
    fail("NSG_ANCESTOR")
with open("/proc/1/status", encoding="ascii") as handle:
    nspid_line = next((line for line in handle if line.startswith("NSpid:")), "")
nspids = [int(value) for value in nspid_line.split()[1:]]
if not (nspids == [1] or (len(nspids) >= 2 and nspids[-1] == 1)):
    fail("NSG_NSPID")
if not os.path.samestat(os.stat("/proc/self/ns/pid"), os.stat("/proc/1/ns/pid")):
    fail("NSG_NAMESPACE")
mounts = {}
mount_options = {}
with open("/proc/self/mountinfo", encoding="utf-8") as handle:
    for line in handle:
        before, after = line.rstrip("\n").split(" - ", 1)
        fields = before.split()
        mounts[fields[4]] = after.split()[0]
        mount_options[fields[4]] = fields[5].split(",")
if (
    mounts.get("/proc") != "proc"
    or mounts.get("/run") != "tmpfs"
    or mounts.get("/tmp") != "tmpfs"
    or "ro" not in mount_options.get("/usr", ())
):
    fail("NSG_MOUNT")
for path in (
    "/run/soc-controller",
    "/run/soc-controller/host-tmp",
    "/run/soc-controller/nested-tmp",
    "/run/soc-controller/dbus",
    "/run/soc-auth",
    "/run/soc-work",
):
    entry = os.lstat(path)
    if (
        stat.S_ISLNK(entry.st_mode)
        or not stat.S_ISDIR(entry.st_mode)
        or entry.st_uid != os.geteuid()
    ):
        fail("NSG_DIR")
for variable in (
    "APPLET_CRASH_SAFETY_REPO",
    "APPLET_CRASH_SAFETY_TEST_ROOT",
    "APPLET_CRASH_SAFETY_HOST_DISPLAY",
    "APPLET_CRASH_SAFETY_DISPLAY",
    "APPLET_CRASH_SAFETY_HOST_XAUTHORITY",
    "APPLET_CRASH_SAFETY_NESTED_XAUTHORITY",
    "APPLET_CRASH_SAFETY_CONTROLLER_ROOT",
    "APPLET_CRASH_SAFETY_BWRAP",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "BASH_ENV",
    "ENV",
):
    if variable in os.environ:
        fail("NSG_ENV")
if os.path.lexists("/tmp/.X11-unix/X0"):
    fail("NSG_HOST_X11")
probe = socket.socket(socket.AF_UNIX)
try:
    probe.connect("\0/tmp/.X11-unix/X0")
except OSError:
    pass
else:
    fail("NSG_HOST_X11")
finally:
    probe.close()
PY
}

if ! verify_private_namespace; then
	if [[ ${install_guard_probe} == "1" ]]; then
		printf 'INSTALL_GUARD_FAIL NAMESPACE\n' >&2
	fi
	exit 2
fi

if [[ ${namespace_guard_probe} == "1" ]]; then
	exit 0
fi

if [[ ! -d "${repo_dir}/files/${uuid}" || -L "${repo_dir}/files/${uuid}" ]]; then
	printf 'applet-crash-safety: applet source directory is missing or unsafe.\n' >&2
	exit 2
fi

mkdir -p -- "${test_root}" "${session_home}" "${runtime_dir}" "${config_dir}/autostart" \
	"${cache_dir}" "${data_dir}" "${state_dir}" "${tmp_dir}"
chmod 700 "${controller_root}" "${controller_root}/host-tmp" \
	"${controller_root}/nested-tmp" "${controller_root}/dbus" "/run/soc-auth" \
	"/run/soc-work" "${test_root}" "${session_home}" "${runtime_dir}" "${config_dir}" \
	"${config_dir}/autostart" "${cache_dir}" "${data_dir}" "${state_dir}" "${tmp_dir}"

export HOME="${session_home}"
export XDG_CONFIG_HOME="${config_dir}"
export XDG_CACHE_HOME="${cache_dir}"
export XDG_DATA_HOME="${data_dir}"
export XDG_STATE_HOME="${state_dir}"
export XDG_RUNTIME_DIR="${runtime_dir}"
export TMPDIR="${tmp_dir}"
export XDG_CURRENT_DESKTOP="X-Cinnamon"
export XDG_SESSION_DESKTOP="cinnamon"
export XDG_SESSION_TYPE="x11"
export GSETTINGS_BACKEND="dconf"
export NO_AT_BRIDGE=1
export GTK_USE_PORTAL=0
export GIO_USE_VFS=local

run_nested() {
	local profile="$1"
	shift
	local target_profile="0"
	local outer_pid_namespace=""
	local -a sandbox=(
		"${bwrap_bin}" --die-with-parent --new-session --clearenv
		--ro-bind / / --proc /proc --dev /dev
		--tmpfs /tmp --tmpfs /run --dir /run/soc-auth --dir /run/soc-work
		--bind /run/soc-work /run/soc-work
	)
	case "${profile}" in
	setup)
		;;
	bus)
		sandbox+=(--dir /run/soc-bus --ro-bind "${controller_root}/dbus" /run/soc-bus
			--setenv DBUS_SESSION_BUS_ADDRESS "unix:path=/run/soc-bus/session.sock")
		;;
	x)
		sandbox+=(--dir /tmp/.X11-unix
			--ro-bind "${controller_root}/nested-tmp/.X11-unix/X${display_number}" "/tmp/.X11-unix/X${display_number}"
			--ro-bind "${nested_xauthority}" "${nested_xauthority}"
			--setenv DISPLAY "${nested_display}" --setenv XAUTHORITY "${nested_xauthority}")
		;;
	target)
		outer_pid_namespace="$(stat -Lc '%d:%i' -- /proc/self/ns/pid)" || return 1
		target_profile="1"
		sandbox+=(--unshare-pid --unshare-net
			--dir /run/soc-bus --ro-bind "${controller_root}/dbus" /run/soc-bus
			--dir /tmp/.X11-unix
			--ro-bind "${controller_root}/nested-tmp/.X11-unix/X${display_number}" "/tmp/.X11-unix/X${display_number}"
			--ro-bind "${nested_xauthority}" "${nested_xauthority}"
			--setenv DISPLAY "${nested_display}" --setenv XAUTHORITY "${nested_xauthority}"
			--setenv DBUS_SESSION_BUS_ADDRESS "unix:path=/run/soc-bus/session.sock")
		;;
	*)
		return 2
		;;
	esac
	sandbox+=(
		--setenv PATH "${TRUSTED_COMMAND_PATH}"
		--setenv HOME "${HOME}"
		--setenv XDG_CONFIG_HOME "${XDG_CONFIG_HOME}"
		--setenv XDG_CACHE_HOME "${XDG_CACHE_HOME}"
		--setenv XDG_DATA_HOME "${XDG_DATA_HOME}"
		--setenv XDG_STATE_HOME "${XDG_STATE_HOME}"
		--setenv XDG_RUNTIME_DIR "${XDG_RUNTIME_DIR}"
		--setenv TMPDIR "${TMPDIR}"
		--setenv XDG_CURRENT_DESKTOP X-Cinnamon
		--setenv XDG_SESSION_DESKTOP cinnamon
		--setenv XDG_SESSION_TYPE x11
		--setenv GSETTINGS_BACKEND dconf
		--setenv NO_AT_BRIDGE 1
		--setenv GTK_USE_PORTAL 0
		--setenv GIO_USE_VFS local
		--setenv LC_ALL C
		--chdir "${repo_dir}" --
	)
	if [[ ${target_profile} == "1" ]]; then
		"${sandbox[@]}" python3 -I -S -c '
import os
import socket
import stat
import sys

if len(sys.argv) < 8 or sys.argv[6] != "--":
    raise SystemExit(125)
outer_namespace, controller, host_authority, nested_authority, host_display = sys.argv[1:6]
command = sys.argv[7:]
if not command or os.getppid() != 1:
    raise SystemExit(125)
namespace_entry = os.stat("/proc/self/ns/pid")
if f"{namespace_entry.st_dev}:{namespace_entry.st_ino}" == outer_namespace:
    raise SystemExit(125)
mounts = {}
with open("/proc/self/mountinfo", encoding="utf-8") as handle:
    for line in handle:
        before, after = line.rstrip("\n").split(" - ", 1)
        fields = before.split()
        mounts[fields[4]] = after.split()[0]
if mounts.get("/proc") != "proc":
    raise SystemExit(125)
if any(os.path.lexists(path) for path in (
    controller,
    f"/proc/1/root{controller}",
    host_authority,
    f"/tmp/.X11-unix/X{host_display}",
)):
    raise SystemExit(125)
nested_entry = os.lstat(nested_authority)
if not stat.S_ISREG(nested_entry.st_mode) or nested_entry.st_nlink != 1:
    raise SystemExit(125)
probe = socket.socket(socket.AF_UNIX)
try:
    probe.connect("\0/tmp/.X11-unix/X" + host_display)
except OSError:
    pass
else:
    raise SystemExit(125)
finally:
    probe.close()
os.execvpe(command[0], command, os.environ)
' "${outer_pid_namespace}" "${controller_root}" "${host_xauthority}" \
			"${nested_xauthority}" "${host_display#:}" -- "$@"
		return
	fi
	"${sandbox[@]}" "$@"
}

if [[ ${install_guard_probe} == "1" ]]; then
	status_path="${controller_root}/install-status"
	result_path="${controller_root}/install-result"
	status_fd=""
	result_fd=""
	status_before=""
	result_before=""
	result_frame='INSTALL_GUARD_FAIL CORE_RESULT_INIT'
	result_written=0
	# shellcheck disable=SC2317,SC2329
	write_install_result() {
		((result_written == 0)) || return 1
		[[ -n ${result_fd} ]] || return 1
		case "${result_frame}" in
		OK | ROOT_FAIL\ ROOT_META | ROOT_FAIL\ ROOT_EUID | ROOT_FAIL\ ROOT_UID_MAP | ROOT_FAIL\ ROOT_FDINFO | ROOT_FAIL\ ROOT_MOUNT | \
			CHAIN_FAIL\ OWNER | CHAIN_FAIL\ MODE | CHAIN_FAIL\ META | CHAIN_FAIL\ FD | CHAIN_FAIL\ MISSING | \
			INSTALL_GUARD_FAIL\ CORE_FD | INSTALL_GUARD_FAIL\ CORE_FRAME | INSTALL_GUARD_FAIL\ CORE_STATUS | \
			INSTALL_GUARD_FAIL\ CORE_STATUS_OK_NONZERO | INSTALL_GUARD_FAIL\ CORE_STATUS_FAIL_ZERO | \
			INSTALL_GUARD_FAIL\ CORE_STATUS_ROOT_EXIT | INSTALL_GUARD_FAIL\ CORE_STATUS_CHAIN_EXIT | \
			INSTALL_GUARD_FAIL\ CORE_STATUS_MULTIPLE | INSTALL_GUARD_FAIL\ CORE_PARSER_EXIT | \
			INSTALL_GUARD_FAIL\ CORE_PARSER_EMPTY | INSTALL_GUARD_FAIL\ CORE_PARSER_MULTIPLE | \
			INSTALL_GUARD_FAIL\ CORE_PARSER_CONTROL | INSTALL_GUARD_FAIL\ CORE_PARSER_UNKNOWN | \
			INSTALL_GUARD_FAIL\ CORE_RESULT_INIT | INSTALL_GUARD_FAIL\ CORE_RESULT_MISSING | \
			INSTALL_GUARD_FAIL\ CORE_RESULT_EMPTY | INSTALL_GUARD_FAIL\ CORE_RESULT_META | \
			INSTALL_GUARD_FAIL\ CORE_RESULT_FRAME | INSTALL_GUARD_FAIL\ CORE_RESULT_CODE | \
			INSTALL_GUARD_FAIL\ CORE_RESULT_STATUS_FD | INSTALL_GUARD_FAIL\ CORE_RESULT_STATUS_SPAWN | \
			INSTALL_GUARD_FAIL\ CORE_RESULT_STATUS_FRAME | INSTALL_GUARD_FAIL\ CORE_RESULT_WRITE)
			;;
		*) result_frame='INSTALL_GUARD_FAIL CORE_RESULT_WRITE' ;;
		esac
		printf '%s\n' "${result_frame}" >&"${result_fd}" || return 1
		result_written=1
		local result_now
		result_now="$(stat -Lc '%d:%i:%u:%g:%a:%h:%s' -- "/proc/self/fd/${result_fd}")" || return 1
		[[ ${result_now%:*} == "${result_before%:*}" && ${result_now##*:} -le 128 ]]
	}
	# shellcheck disable=SC2317,SC2329
	status_cleanup() {
		local status=$?
		if ((status == 0)); then
			result_frame='OK'
		fi
		write_install_result || status=1
		if [[ -n ${status_fd} ]]; then
			exec {status_fd}>&- || true
			status_fd=""
		fi
		if [[ -n ${result_fd} ]]; then
			exec {result_fd}>&- || true
			result_fd=""
		fi
		if [[ -n ${status_before} && -f ${status_path} && ! -L ${status_path} ]]; then
			current_status="$(stat -c '%d:%i:%u:%g:%a:%h:%s' -- "${status_path}" 2>/dev/null || true)"
			if [[ ${status_before%:*} == "${current_status%:*}" ]]; then
				rm -f -- "${status_path}"
			fi
		fi
		exit "${status}"
	}
	trap status_cleanup EXIT
	result_frame='INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FD'
	mkdir -p -- "${test_root}" "${session_home}"
	chmod 700 "${test_root}" "${session_home}"
	[[ ! -e ${status_path} && ! -L ${status_path} ]] || exit 1
	[[ ! -e ${result_path} && ! -L ${result_path} ]] || exit 1
	(
		set -C
		: >"${status_path}"
	) 2>/dev/null || exit 1
	(
		set -C
		: >"${result_path}"
	) 2>/dev/null || exit 1
	chmod 600 "${status_path}" || exit 1
	chmod 600 "${result_path}" || exit 1
	status_before="$(stat -c '%d:%i:%u:%g:%a:%h:%s' -- "${status_path}")" || exit 1
	result_before="$(stat -c '%d:%i:%u:%g:%a:%h:%s' -- "${result_path}")" || exit 1
	exec {status_fd}<>"${status_path}" || exit 1
	exec {result_fd}<>"${result_path}" || exit 1
	status_fd_before="$(stat -Lc '%d:%i:%u:%g:%a:%h:%s' -- "/proc/self/fd/${status_fd}")" || exit 1
	[[ ${status_before} == "${status_fd_before}" ]] || exit 1
	result_fd_before="$(stat -Lc '%d:%i:%u:%g:%a:%h:%s' -- "/proc/self/fd/${result_fd}")" || exit 1
	[[ ${result_before} == "${result_fd_before}" ]] || exit 1
	# shellcheck disable=SC2034
	install_status_fd="${status_fd}"
	result_frame='INSTALL_GUARD_FAIL CORE_RESULT_STATUS_SPAWN'
	if run_nested setup env SPEED_OF_CINNAMON_TEST_HOME=1 HOME="${session_home}" \
		python3 -I -S "${repo_dir}/scripts/safe-local-fs.py" assert-private-chain install "${session_home}" --allow-missing --status-fd "${status_fd}" \
		2>/dev/null; then
		probe_status=0
	else
		probe_status=$?
	fi
	result_frame='INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FRAME'
	parser_output=""
	if parser_output="$(
		python3 -I -S "${repo_dir}/scripts/safe-local-fs.py" parse-status-fd \
			--status-fd "${status_fd}" --child-exit-status "${probe_status}" 2>/dev/null
		parser_exit=$?
		printf '\036'
		exit "${parser_exit}"
	)"; then
		:
	else
		result_frame='INSTALL_GUARD_FAIL CORE_PARSER_EXIT'
		printf 'INSTALL_GUARD_FAIL CORE_PARSER_EXIT\n' >&2
		exit 1
	fi
	parser_output="${parser_output%$'\036'}"
	if [[ -z ${parser_output} ]]; then
		result_frame='INSTALL_GUARD_FAIL CORE_PARSER_EMPTY'
		printf 'INSTALL_GUARD_FAIL CORE_PARSER_EMPTY\n' >&2
		exit 1
	fi
	if [[ ${parser_output} == *$'\r'* || ${parser_output} =~ [$'\x00'-'\x1f''\x7f'-'\x9f'] ]]; then
		result_frame='INSTALL_GUARD_FAIL CORE_PARSER_CONTROL'
		printf 'INSTALL_GUARD_FAIL CORE_PARSER_CONTROL\n' >&2
		exit 1
	fi
	if [[ ${parser_output} != *$'\n' ]]; then
		result_frame='INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN'
		printf 'INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN\n' >&2
		exit 1
	fi
	status_token="${parser_output%$'\n'}"
	if [[ ${status_token} == *$'\n'* ]]; then
		result_frame='INSTALL_GUARD_FAIL CORE_PARSER_MULTIPLE'
		printf 'INSTALL_GUARD_FAIL CORE_PARSER_MULTIPLE\n' >&2
		exit 1
	fi
	case "${status_token}" in
	OK | ROOT_FAIL_ROOT_META | ROOT_FAIL_ROOT_EUID | ROOT_FAIL_ROOT_UID_MAP | ROOT_FAIL_ROOT_FDINFO | ROOT_FAIL_ROOT_MOUNT | CHAIN_FAIL_OWNER | CHAIN_FAIL_MODE | CHAIN_FAIL_META | CHAIN_FAIL_FD | CHAIN_FAIL_MISSING | CORE_FD | CORE_FRAME | CORE_STATUS_OK_NONZERO | CORE_STATUS_FAIL_ZERO | CORE_STATUS_ROOT_EXIT | CORE_STATUS_CHAIN_EXIT | CORE_STATUS_MULTIPLE)
		;;
	*)
		result_frame='INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN'
		printf 'INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN\n' >&2
		exit 1
		;;
	esac
	case "${status_token}" in
	OK)
		exit 0
		;;
	ROOT_FAIL_ROOT_META | ROOT_FAIL_ROOT_EUID | ROOT_FAIL_ROOT_UID_MAP | ROOT_FAIL_ROOT_FDINFO | ROOT_FAIL_ROOT_MOUNT)
		result_frame="ROOT_FAIL ${status_token#ROOT_FAIL_}"
		printf 'ROOT_FAIL %s\n' "${status_token#ROOT_FAIL_}" >&2
		exit 1
		;;
	CHAIN_FAIL_OWNER | CHAIN_FAIL_MODE | CHAIN_FAIL_META | CHAIN_FAIL_FD | CHAIN_FAIL_MISSING)
		result_frame="CHAIN_FAIL ${status_token#CHAIN_FAIL_}"
		printf 'CHAIN_FAIL %s\n' "${status_token#CHAIN_FAIL_}" >&2
		exit 1
		;;
	CORE_FD)
		result_frame='INSTALL_GUARD_FAIL CORE_FD'
		printf 'INSTALL_GUARD_FAIL CORE_FD\n' >&2
		exit 1
		;;
	CORE_FRAME)
		if ((probe_status != 0)); then
			result_frame='INSTALL_GUARD_FAIL CORE_RESULT_STATUS_SPAWN'
			printf 'INSTALL_GUARD_FAIL CORE_RESULT_STATUS_SPAWN\n' >&2
		else
			result_frame='INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FRAME'
			printf 'INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FRAME\n' >&2
		fi
		exit 1
		;;
	CORE_STATUS_OK_NONZERO | CORE_STATUS_FAIL_ZERO | CORE_STATUS_ROOT_EXIT | CORE_STATUS_CHAIN_EXIT | CORE_STATUS_MULTIPLE)
		result_frame="INSTALL_GUARD_FAIL ${status_token}"
		printf 'INSTALL_GUARD_FAIL %s\n' "${status_token}" >&2
		exit 1
		;;
	*)
		result_frame='INSTALL_GUARD_FAIL CORE_RESULT_WRITE'
		printf 'INSTALL_GUARD_FAIL CORE_RESULT_WRITE\n' >&2
		exit 1
		;;
	esac
fi

if ! python3 -I -S - "${nested_xauthority}" "${nested_display}" <<'PY'; then
import os
import secrets
import stat
import subprocess
import sys

authority, display = sys.argv[1:]
auth_dir = "/run/soc-auth"
xauth = "/usr/bin/xauth"
directory_fd = os.open(
    auth_dir,
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
)
try:
    directory_entry = os.fstat(directory_fd)
    executable = os.lstat(xauth)
    if (
        not stat.S_ISDIR(directory_entry.st_mode)
        or directory_entry.st_uid != os.geteuid()
        or stat.S_IMODE(directory_entry.st_mode) != 0o700
        or not stat.S_ISREG(executable.st_mode)
        or executable.st_uid not in (0, 65534)
        or executable.st_mode & 0o022
        or not executable.st_mode & 0o111
    ):
        print("SETUP_XAUTH_TRUST", file=sys.stderr)
        raise SystemExit(1)
    name = os.path.basename(authority)
    if authority != f"{auth_dir}/{name}" or name != "nested.Xauthority":
        print("SETUP_XAUTH_TRUST", file=sys.stderr)
        raise SystemExit(1)
    file_fd = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=directory_fd,
    )
    os.close(file_fd)
    cookie = secrets.token_hex(16)
    payload = f"add {display} MIT-MAGIC-COOKIE-1 {cookie}\n".encode("ascii")
    completed = subprocess.run(
        [xauth, "-q", "-f", f"/proc/self/fd/{directory_fd}/{name}", "source", "-"],
        input=payload,
        stdin=None,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        pass_fds=(directory_fd,),
        env={"PATH": "/usr/bin:/bin", "HOME": "/run/soc-work/session/home", "LC_ALL": "C"},
        timeout=3,
        check=False,
    )
    cookie = ""
    payload = b""
    if completed.returncode != 0:
        raise SystemExit(1)
    authority_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory_fd)
    try:
        entry = os.fstat(authority_fd)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_uid != os.geteuid()
            or entry.st_nlink != 1
            or stat.S_IMODE(entry.st_mode) != 0o600
        ):
            raise SystemExit(1)
        os.fsync(authority_fd)
    finally:
        os.close(authority_fd)
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
	printf 'SETUP_XAUTH_TRUST\n' >&2
	exit 2
fi

if ! validate_xauthority_files; then
	printf 'SETUP_XAUTH_TRUST\n' >&2
	exit 2
fi

run_dbus_server() {
	"${bwrap_bin}" --die-with-parent --new-session --clearenv \
		--ro-bind / / --proc /proc --dev /dev --tmpfs /tmp \
		--tmpfs /run --dir /run/soc-bus --bind "${controller_root}/dbus" /run/soc-bus \
		--setenv PATH "${TRUSTED_COMMAND_PATH}" --setenv HOME /tmp --setenv LC_ALL C -- \
		"$@"
}

run_xephyr_server() {
	"${bwrap_bin}" --die-with-parent --new-session --clearenv \
		--ro-bind / / --proc /proc --dev /dev \
		--bind "${controller_root}/nested-tmp" /tmp \
		--ro-bind "${controller_root}/host-tmp/.X11-unix/X${host_display#:}" "/tmp/.X11-unix/X${host_display#:}" \
		--tmpfs /run --dir /run/soc-auth \
		--ro-bind "${host_xauthority}" "${host_xauthority}" \
		--ro-bind "${nested_xauthority}" "${nested_xauthority}" \
		--setenv PATH "${TRUSTED_COMMAND_PATH}" --setenv HOME /tmp \
		--setenv DISPLAY "${host_display}" --setenv XAUTHORITY "${host_xauthority}" --setenv LC_ALL C -- \
		"$@"
}

run_gdbus() {
	run_nested bus timeout --signal=TERM --kill-after=2s "${dbus_timeout_seconds}s" \
		gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon "$@"
}

validate_core_dbus_daemon() {
	python3 -I -S - <<'PY'
import os
import stat
import sys

path = "/usr/bin/dbus-daemon"
try:
    entry = os.lstat(path)
except OSError:
    print("DBUS_FAIL TRUST", file=sys.stderr)
    raise SystemExit(1)
if (
    not stat.S_ISREG(entry.st_mode)
    or entry.st_uid not in (0, 65534)
    or entry.st_nlink != 1
    or entry.st_mode & 0o022
    or not entry.st_mode & 0o111
):
    print("DBUS_FAIL TRUST", file=sys.stderr)
    raise SystemExit(1)
PY
}

eval_cinnamon() {
	local script="$1"
	run_gdbus --method org.Cinnamon.Eval "${script}"
}

verify_private_dbus_binding() {
	python3 -I -S - "${controller_root}" "${dbus_dir}" "${dbus_socket}" \
		"/run/soc-bus/session.sock" "${dbus_pid}" "${dbus_start_time}" \
		"${dbus_server_pid}" "${dbus_server_start_time}" "$(id -u)" \
		"${dbus_expected_socket_identity:-}" <<'PY'
import os
import select
import stat
import sys

controller, bus_dir, socket_path, bound_path = sys.argv[1:5]
parent_pid = int(sys.argv[5])
expected_parent_start = int(sys.argv[6])
pid = int(sys.argv[7])
expected_start = int(sys.argv[8])
expected_uid = int(sys.argv[9])
expected_socket_identity = sys.argv[10]
daemon_fd = None

FAILURE_EXIT_CODES = {
    "PARENT": 10,
    "UID": 11,
    "DIR": 12,
    "SOCKET_LSTAT": 13,
    "SOCKET_META": 14,
    "SOCKET_NET": 16,
    "FD": 17,
    "PROC_NET_UNIX": 18,
    "IDENTITY": 19,
    "DAEMON_NONE": 20,
    "DAEMON_MULTIPLE": 21,
    "DAEMON_IDENTITY": 22,
    "DAEMON_LIMIT": 23,
    "SOCKET_MULTIPLE": 24,
}

def fail(code):
    global daemon_fd
    if daemon_fd is not None:
        os.close(daemon_fd)
        daemon_fd = None
    raise SystemExit(FAILURE_EXIT_CODES[code])


def process_identity(target_pid):
    try:
        with open(f"/proc/{target_pid}/stat", "rb", buffering=0) as handle:
            fields = handle.read(65536).rsplit(b") ", 1)[1].split()
        return fields[0], int(fields[1]), int(fields[19])
    except (OSError, IndexError, ValueError):
        fail("PARENT")

def checked_lstat(path, code):
    try:
        return os.lstat(path)
    except OSError:
        fail(code)


parent_before = process_identity(parent_pid)
process_before = process_identity(pid)
if (
    parent_before[0] == b"Z"
    or parent_before[2] != expected_parent_start
    or process_before[0] == b"Z"
    or process_before[1] != parent_pid
    or process_before[2] != expected_start
):
    fail("IDENTITY")

controller_entry = checked_lstat(controller, "DIR")
if (
    stat.S_ISLNK(controller_entry.st_mode)
    or not stat.S_ISDIR(controller_entry.st_mode)
    or controller_entry.st_uid != expected_uid
    or controller_entry.st_mode & 0o077
):
    fail("DIR")
if bus_dir != controller + "/dbus" or os.path.dirname(socket_path) != bus_dir:
    fail("DIR")
directory_entry = checked_lstat(bus_dir, "DIR")
if (
    stat.S_ISLNK(directory_entry.st_mode)
    or not stat.S_ISDIR(directory_entry.st_mode)
    or directory_entry.st_uid != expected_uid
    or directory_entry.st_mode & 0o077
):
    fail("DIR")
socket_entry = checked_lstat(socket_path, "SOCKET_LSTAT")
if (
    stat.S_ISLNK(socket_entry.st_mode)
    or not stat.S_ISSOCK(socket_entry.st_mode)
    or socket_entry.st_uid != expected_uid
    or socket_entry.st_nlink != 1
):
    fail("SOCKET_META")
socket_identity = (
    f"{socket_entry.st_dev}:{socket_entry.st_ino}:{socket_entry.st_uid}:"
    f"{socket_entry.st_gid}:{socket_entry.st_nlink}"
)
if expected_socket_identity and socket_identity != expected_socket_identity:
    fail("IDENTITY")

def read_uid(target_pid):
    try:
        with open(f"/proc/{target_pid}/status", encoding="ascii") as handle:
            uid_line = next((line for line in handle if line.startswith("Uid:")), "")
        values = uid_line.split()[1:]
        if not values or any(int(value) != expected_uid for value in values):
            fail("DAEMON_IDENTITY")
    except (OSError, UnicodeError, ValueError):
        fail("DAEMON_IDENTITY")

def children(target_pid):
    try:
        with open(f"/proc/{target_pid}/task/{target_pid}/children", encoding="ascii") as handle:
            values = handle.read().split()
        if len(values) > 16:
            fail("DAEMON_LIMIT")
        result = [int(value) for value in values]
    except (OSError, UnicodeError, ValueError):
        fail("DAEMON_IDENTITY")
    if any(value <= 0 for value in result):
        fail("DAEMON_IDENTITY")
    return result

def count_private_dbus_matches(lines, socket_inodes, bound_path):
    count = 0
    for line in lines:
        columns = line.rstrip("\n").split(None, 7)
        if (
            len(columns) == 8
            and columns[3] == "00010000"
            and columns[4] == "0001"
            and columns[5] == "01"
            and columns[6] in socket_inodes
            and columns[7] == bound_path
        ):
            count += 1
    return count

try:
    trusted_exe = os.stat("/usr/bin/dbus-daemon", follow_symlinks=True)
except OSError:
    fail("DAEMON_IDENTITY")

queue = [(pid, process_before[2], 0)]
seen = {(pid, process_before[2])}
chain_edges = []
candidates = []
for _node in range(16):
    if not queue:
        break
    current_pid, current_start, depth = queue.pop(0)
    if depth >= 4:
        if children(current_pid):
            fail("DAEMON_LIMIT")
        continue
    parent_identity = process_identity(current_pid)
    if parent_identity[0] == b"Z" or parent_identity[2] != current_start:
        fail("DAEMON_IDENTITY")
    for child_pid in children(current_pid):
        child_before = process_identity(child_pid)
        if child_before[0] == b"Z" or child_before[1] != current_pid:
            fail("DAEMON_IDENTITY")
        child_start = child_before[2]
        child_entry = None
        try:
            child_entry = os.stat(f"/proc/{child_pid}/exe", follow_symlinks=True)
        except OSError:
            fail("DAEMON_IDENTITY")
        if child_before[0] != b"Z" and child_entry is not None and os.path.samestat(child_entry, trusted_exe):
            read_uid(child_pid)
            candidates.append((child_pid, child_start))
        parent_after = process_identity(current_pid)
        child_after = process_identity(child_pid)
        if (
            parent_after != parent_identity
            or child_after != child_before
            or child_after[1] != current_pid
        ):
            fail("DAEMON_IDENTITY")
        chain_edges.append((current_pid, current_start, child_pid, child_start))
        key = (child_pid, child_start)
        if key not in seen:
            seen.add(key)
            queue.append((child_pid, child_start, depth + 1))
else:
    fail("DAEMON_LIMIT")
if len(candidates) == 0:
    fail("DAEMON_NONE")
if len(candidates) != 1:
    fail("DAEMON_MULTIPLE")
daemon_pid, daemon_start = candidates[0]
try:
    daemon_fd = os.pidfd_open(daemon_pid, 0)
except (AttributeError, OSError):
    fail("DAEMON_IDENTITY")
try:
    daemon_after = process_identity(daemon_pid)
    daemon_exe_after = os.stat(f"/proc/{daemon_pid}/exe", follow_symlinks=True)
    if (
        daemon_after[0] == b"Z"
        or daemon_after[2] != daemon_start
        or not os.path.samestat(daemon_exe_after, trusted_exe)
    ):
        fail("DAEMON_IDENTITY")
    read_uid(daemon_pid)
except OSError:
    fail("DAEMON_IDENTITY")
finally:
    pass

try:
    socket_inodes = set()
    for name in os.listdir(f"/proc/{daemon_pid}/fd"):
        try:
            target = os.readlink(f"/proc/{daemon_pid}/fd/{name}")
        except OSError:
            continue
        if target.startswith("socket:[") and target.endswith("]"):
            socket_inodes.add(target[8:-1])
except OSError:
    fail("FD")
try:
    with open("/proc/net/unix", encoding="ascii") as handle:
        next(handle, None)
        bound_matches = count_private_dbus_matches(handle, socket_inodes, bound_path)
except (OSError, UnicodeError):
    fail("PROC_NET_UNIX")
if bound_matches == 0:
    fail("SOCKET_NET")
if bound_matches > 1:
    fail("SOCKET_MULTIPLE")
socket_after = checked_lstat(socket_path, "SOCKET_LSTAT")
socket_identity_after = (
    f"{socket_after.st_dev}:{socket_after.st_ino}:{socket_after.st_uid}:"
    f"{socket_after.st_gid}:{socket_after.st_nlink}"
)
if socket_identity_after != socket_identity:
    fail("IDENTITY")
if process_identity(parent_pid) != parent_before or process_identity(pid) != process_before:
    fail("IDENTITY")
for edge_parent, edge_parent_start, edge_child, edge_child_start in chain_edges:
    edge_parent_after = process_identity(edge_parent)
    edge_child_after = process_identity(edge_child)
    if (
        edge_parent_after[2] != edge_parent_start
        or edge_child_after[1] != edge_parent
        or edge_child_after[2] != edge_child_start
    ):
        fail("DAEMON_IDENTITY")
daemon_final = process_identity(daemon_pid)
try:
    daemon_exe_final = os.stat(f"/proc/{daemon_pid}/exe", follow_symlinks=True)
except OSError:
    fail("DAEMON_IDENTITY")
if (
    daemon_final[0] == b"Z"
    or daemon_final[2] != daemon_start
    or not os.path.samestat(daemon_exe_final, trusted_exe)
):
    fail("DAEMON_IDENTITY")
read_uid(daemon_pid)
if daemon_fd is None:
    fail("DAEMON_IDENTITY")
try:
    poller = select.poll()
    poller.register(daemon_fd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)
    if poller.poll(0):
        fail("DAEMON_IDENTITY")
except (OSError, ValueError):
    fail("DAEMON_IDENTITY")
os.close(daemon_fd)
daemon_fd = None
print(socket_identity)
PY
}

unset DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY XAUTHORITY
dbus_dir="${controller_root}/dbus"
dbus_socket="${dbus_dir}/session.sock"
dbus_log="${test_root}/dbus-daemon.log"
chmod 700 "${dbus_dir}"
dbus_pid=""
dbus_start_time=""
dbus_server_pid=""
dbus_server_start_time=""
dbus_socket_identity=""
dbus_expected_socket_identity=""
dbus_binding_failure_code=""
dbus_socket_lstat_failure_code=""
dbus_last_failure_code="SOCKET_LSTAT"
record_dbus_failure_code() {
	case "$1" in
	PARENT | UID | DIR | SOCKET_LSTAT | SOCKET_SERVER_EXIT | SOCKET_SERVER_NO_BIND | SOCKET_CORE_NO_VIEW | SOCKET_META | SOCKET_NET | SOCKET_MULTIPLE | FD | PROC_NET_UNIX | IDENTITY | DAEMON_NONE | DAEMON_MULTIPLE | DAEMON_IDENTITY | DAEMON_LIMIT | CLIENT_TIMEOUT | VERIFY)
		dbus_last_failure_code="$1"
		;;
	esac
}
reap_direct_probe_child_without_starttime() {
	local child_pid="$1" child_state identity
	local _child_start
	for _ in $(seq 1 20); do
		if ! identity="$(read_process_identity "${child_pid}")"; then
			return 1
		fi
		IFS=' ' read -r child_state _child_start <<<"${identity}"
		if [[ ${child_state} == "Z" ]]; then
			return 1
		fi
		sleep 0.05
	done
	return 1
}
terminate_direct_probe_child() {
	local child_pid="$1" owner_pid="$2" expected_start="$3"
	local identity child_state child_parent child_start
	local deadline
	identity="$(read_direct_probe_identity "${child_pid}" "${owner_pid}")" || return 1
	IFS=' ' read -r child_state child_parent child_start <<<"${identity}"
	[[ ${child_parent} == "${owner_pid}" && ${child_start} == "${expected_start}" &&
		${child_state} != "Z" ]] || return 1
	signal_owned_process "${child_pid}" "${expected_start}" SIGTERM || return 1
	deadline=$((SECONDS + 1))
	while ((SECONDS <= deadline)); do
		if ! identity="$(read_direct_probe_identity "${child_pid}" "${owner_pid}")"; then
			[[ ! -e "/proc/${child_pid}" ]] || return 1
			wait "${child_pid}" >/dev/null 2>&1 || true
			return 0
		fi
		IFS=' ' read -r child_state child_parent child_start <<<"${identity}"
		[[ ${child_parent} == "${owner_pid}" && ${child_start} == "${expected_start}" ]] || return 1
		if [[ ${child_state} == "Z" ]]; then
			wait "${child_pid}" >/dev/null 2>&1 || true
			return 0
		fi
		sleep 0.05
	done
	identity="$(read_direct_probe_identity "${child_pid}" "${owner_pid}")" || return 1
	IFS=' ' read -r child_state child_parent child_start <<<"${identity}"
	[[ ${child_parent} == "${owner_pid}" && ${child_start} == "${expected_start}" &&
		${child_state} != "Z" ]] || return 1
	signal_owned_process "${child_pid}" "${expected_start}" SIGKILL || return 1
	deadline=$((SECONDS + 1))
	while ((SECONDS <= deadline)); do
		if ! identity="$(read_direct_probe_identity "${child_pid}" "${owner_pid}")"; then
			[[ ! -e "/proc/${child_pid}" ]] || return 1
			wait "${child_pid}" >/dev/null 2>&1 || true
			return 0
		fi
		IFS=' ' read -r child_state child_parent child_start <<<"${identity}"
		[[ ${child_parent} == "${owner_pid}" && ${child_start} == "${expected_start}" ]] || return 1
		if [[ ${child_state} == "Z" ]]; then
			wait "${child_pid}" >/dev/null 2>&1 || true
			return 0
		fi
		sleep 0.05
	done
	return 1
}
run_bounded_server_socket_test() {
	local probe_pid probe_owner probe_start probe_identity probe_state probe_parent probe_status
	run_dbus_server /usr/bin/test -S /run/soc-bus/session.sock >/dev/null 2>&1 &
	probe_pid=$!
	probe_owner="${BASHPID}"
	probe_start=""
	for _ in $(seq 1 20); do
		if probe_identity="$(read_direct_probe_identity "${probe_pid}" "${probe_owner}")"; then
			IFS=' ' read -r _ probe_parent probe_start <<<"${probe_identity}"
			break
		fi
		if ! kill -0 "${probe_pid}" 2>/dev/null; then
			probe_status=0
			wait "${probe_pid}" >/dev/null 2>&1 || probe_status=$?
			return "${probe_status}"
		fi
		sleep 0.05
	done
	if [[ -z ${probe_start} ]]; then
		reap_direct_probe_child_without_starttime "${probe_pid}" || true
		return 1
	fi
	for _ in $(seq 1 20); do
		if ! probe_identity="$(read_direct_probe_identity "${probe_pid}" "${probe_owner}")"; then
			if [[ ! -e "/proc/${probe_pid}" ]]; then
				probe_status=0
				wait "${probe_pid}" >/dev/null 2>&1 || probe_status=$?
				return "${probe_status}"
			fi
			return 1
		fi
		IFS=' ' read -r probe_state probe_parent probe_start <<<"${probe_identity}"
		[[ ${probe_parent} == "${probe_owner}" ]] || return 1
		if [[ ${probe_state} == "Z" ]]; then
			probe_status=0
			wait "${probe_pid}" >/dev/null 2>&1 || probe_status=$?
			return "${probe_status}"
		fi
		sleep 0.05
	done
	terminate_direct_probe_child "${probe_pid}" "${probe_owner}" "${probe_start}" || return 1
	return 1
}
classify_socket_lstat_failure() {
	if [[ -n ${dbus_socket_lstat_failure_code} ]]; then
		return 0
	fi
	local launcher_state=""
	if ! launcher_state="$(owned_process_state "${dbus_pid}" "${dbus_start_time}")" ||
		[[ ${launcher_state} == "Z" ]]; then
		dbus_socket_lstat_failure_code="SOCKET_SERVER_EXIT"
	elif run_bounded_server_socket_test; then
		dbus_socket_lstat_failure_code="SOCKET_CORE_NO_VIEW"
	else
		dbus_socket_lstat_failure_code="SOCKET_SERVER_NO_BIND"
	fi
}
capture_private_dbus_binding() {
	local verifier_status socket_identity
	local expected_identity="${dbus_socket_identity}"
	dbus_socket_identity=""
	dbus_binding_failure_code=""
	dbus_expected_socket_identity="${expected_identity}"
	if verify_private_dbus_binding >/dev/null 2>&1; then
		socket_identity="$(stat -c '%d:%i:%u:%g:%h' -- "${dbus_socket}")" || return 1
		[[ -n ${socket_identity} ]] || return 1
		dbus_socket_identity="${socket_identity}"
		dbus_socket_lstat_failure_code=""
		dbus_expected_socket_identity=""
		return 0
	else
		verifier_status=$?
	fi
	case "${verifier_status}" in
	10) dbus_binding_failure_code=PARENT ;;
	11) dbus_binding_failure_code=UID ;;
	12) dbus_binding_failure_code=DIR ;;
	13)
		classify_socket_lstat_failure
		dbus_binding_failure_code="${dbus_socket_lstat_failure_code}"
		;;
	14) dbus_binding_failure_code=SOCKET_META ;;
	16) dbus_binding_failure_code=SOCKET_NET ;;
	17) dbus_binding_failure_code=FD ;;
	18) dbus_binding_failure_code=PROC_NET_UNIX ;;
	19) dbus_binding_failure_code=IDENTITY ;;
	20) dbus_binding_failure_code=DAEMON_NONE ;;
	21) dbus_binding_failure_code=DAEMON_MULTIPLE ;;
	22) dbus_binding_failure_code=DAEMON_IDENTITY ;;
	23) dbus_binding_failure_code=DAEMON_LIMIT ;;
	24) dbus_binding_failure_code=SOCKET_MULTIPLE ;;
	*) dbus_binding_failure_code=VERIFY ;;
	esac
	record_dbus_failure_code "${dbus_binding_failure_code}"
	dbus_expected_socket_identity=""
	return 1
}
bootstrap_cleanup() {
	local status=$?
	local cleanup_failed=0
	trap - EXIT INT TERM HUP
	set +e
	terminate_owned_process "${dbus_server_pid}" "${dbus_server_start_time}" 4 || cleanup_failed=1
	terminate_owned_process "${dbus_pid}" "${dbus_start_time}" 4 || cleanup_failed=1
	if ((cleanup_failed != 0 && status == 0)); then
		status=1
	fi
	exit "${status}"
}
trap bootstrap_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
validate_core_dbus_daemon || exit 2
run_dbus_server /usr/bin/dbus-daemon --session --nofork --nopidfile --nosyslog \
	--address="unix:path=/run/soc-bus/session.sock" >"${dbus_log}" 2>&1 &
dbus_pid=$!
dbus_identity="$(read_process_identity "${dbus_pid}")" || {
	printf 'Private D-Bus exited before process identity capture.\n' >&2
	exit 1
}
IFS=' ' read -r _ dbus_start_time <<<"${dbus_identity}"
dbus_address="unix:path=/run/soc-bus/session.sock"
for _ in $(seq 1 100); do
	if [[ -z ${dbus_server_pid} ]] &&
		dbus_server_identity="$(read_single_direct_child_identity "${dbus_pid}" "${dbus_start_time}")"; then
		IFS=' ' read -r dbus_server_pid dbus_server_state dbus_server_start_time <<<"${dbus_server_identity}"
		if [[ ${dbus_server_state} == "Z" ]]; then
			dbus_server_pid=""
			dbus_server_start_time=""
		fi
	fi
	if [[ -n ${dbus_server_pid} ]] && capture_private_dbus_binding; then
		if run_nested bus timeout --signal=KILL 1s env -i PATH="${TRUSTED_COMMAND_PATH}" HOME="${HOME}" \
			DBUS_SESSION_BUS_ADDRESS="${dbus_address}" LC_ALL=C \
			gdbus call --address "${dbus_address}" --dest org.freedesktop.DBus \
			--object-path /org/freedesktop/DBus --method org.freedesktop.DBus.ListNames \
			>/dev/null 2>&1; then
			expected_socket_identity="${dbus_socket_identity}"
			if capture_private_dbus_binding && [[ ${dbus_socket_identity} == "${expected_socket_identity}" ]]; then
				break
			fi
			dbus_socket_identity=""
		else
			record_dbus_failure_code CLIENT_TIMEOUT
		fi
	fi
	sleep 0.05
done
if ! capture_private_dbus_binding || [[ -z ${dbus_socket_identity} ]]; then
	printf 'DBUS_FAIL %s\n' "${dbus_last_failure_code}" >&2
	exit 1
fi
if [[ ${dbus_probe} == "1" ]]; then
	(
		if manager_owner="$(
			set -o pipefail
			run_nested bus timeout --signal=TERM --kill-after=2s "${dbus_timeout_seconds}s" \
				gdbus call --address "${dbus_address}" --dest org.freedesktop.DBus \
				--object-path /org/freedesktop/DBus \
				--method org.freedesktop.DBus.NameHasOwner org.freedesktop.systemd1 2>/dev/null |
				python3 -I -S -c 'import sys
reply = sys.stdin.buffer.read(33)
owner = {b"(true,)\n": "true", b"(false,)\n": "false"}.get(reply)
if owner is None:
    raise SystemExit(1)
sys.stdout.write(owner)' 2>/dev/null
		)"; then
			printf 'PRIVATE_DBUS_OK MANAGER_OWNER %s\n' "${manager_owner}"
		else
			printf '%s\n' 'PRIVATE_DBUS_OK MANAGER_PROBE_ERROR' >&2
			exit 1
		fi
	)
	exit 0
fi
export DBUS_SESSION_BUS_ADDRESS="${dbus_address}"

unset WAYLAND_DISPLAY
export DISPLAY="${nested_display}"
export XAUTHORITY="${nested_xauthority}"

cinnamon_pid=""
cinnamon_start_time=""
xephyr_pid=""
xephyr_start_time=""
xephyr_server_pid=""
xephyr_server_start_time=""
smoke_pid=""
smoke_start_time=""
xephyr_socket_identity=""
xephyr_lock_identity=""

verify_xephyr_binding() {
	local state lock_pid socket_before socket_after lock_before lock_after expected_uid identity current_start
	local expected_dbus_identity="${dbus_socket_identity}"
	capture_private_dbus_binding || return 1
	[[ -n ${dbus_socket_identity} && ${dbus_socket_identity} == "${expected_dbus_identity}" ]] || return 1
	[[ -n ${xephyr_pid} && -n ${xephyr_start_time} &&
		-n ${xephyr_server_pid} && -n ${xephyr_server_start_time} &&
		-n ${xephyr_socket_identity} && -n ${xephyr_lock_identity} ]] || return 1
	state="$(owned_process_state "${xephyr_pid}" "${xephyr_start_time}")" || return 1
	[[ ${state} != "Z" && -S ${xephyr_socket_path} && ! -L ${xephyr_socket_path} &&
		-f ${xephyr_lock_path} && ! -L ${xephyr_lock_path} ]] || return 1
	identity="$(read_direct_child_identity "${xephyr_server_pid}" "${xephyr_pid}" "${xephyr_start_time}")" || return 1
	IFS=' ' read -r state current_start <<<"${identity}"
	[[ ${state} != "Z" && ${current_start} == "${xephyr_server_start_time}" ]] || return 1
	expected_uid="$(id -u)" || return 1
	lock_pid="$(tr -d '[:space:]' <"${xephyr_lock_path}")" || return 1
	[[ ${lock_pid} == "${xephyr_server_pid}" ]] || return 1
	[[ "$(stat -c '%u' -- "${xephyr_socket_path}")" == "${expected_uid}" ]] || return 1
	[[ "$(stat -c '%u:%h' -- "${xephyr_lock_path}")" == "${expected_uid}:1" ]] || return 1
	socket_before="$(stat -c '%d:%i:%u:%g' -- "${xephyr_socket_path}")" || return 1
	lock_before="$(stat -c '%d:%i:%u:%h' -- "${xephyr_lock_path}")" || return 1
	[[ ${socket_before} == "${xephyr_socket_identity}" &&
		${lock_before} == "${xephyr_lock_identity}" ]] || return 1
	run_nested x timeout --signal=KILL 1s env -i PATH="${TRUSTED_COMMAND_PATH}" HOME="${HOME}" \
		DISPLAY="${nested_display}" XAUTHORITY="${nested_xauthority}" LC_ALL=C \
		xprop -display "${nested_display}" -root >/dev/null 2>&1 || return 1
	socket_after="$(stat -c '%d:%i:%u:%g' -- "${xephyr_socket_path}")" || return 1
	lock_after="$(stat -c '%d:%i:%u:%h' -- "${xephyr_lock_path}")" || return 1
	identity="$(read_direct_child_identity "${xephyr_server_pid}" "${xephyr_pid}" "${xephyr_start_time}")" || return 1
	IFS=' ' read -r state current_start <<<"${identity}"
	lock_pid="$(tr -d '[:space:]' <"${xephyr_lock_path}")" || return 1
	[[ ${state} != "Z" && ${current_start} == "${xephyr_server_start_time}" &&
		${lock_pid} == "${xephyr_server_pid}" &&
		${socket_after} == "${xephyr_socket_identity}" &&
		${lock_after} == "${xephyr_lock_identity}" ]]
}

cleanup() {
	local status=$?
	local cleanup_failed=0
	trap - EXIT INT TERM HUP
	set +e
	terminate_owned_process "${smoke_pid}" "${smoke_start_time}" 4 || cleanup_failed=1
	terminate_owned_process "${cinnamon_pid}" "${cinnamon_start_time}" 4 || cleanup_failed=1
	terminate_owned_process "${xephyr_server_pid}" "${xephyr_server_start_time}" 4 || cleanup_failed=1
	terminate_owned_process "${xephyr_pid}" "${xephyr_start_time}" 4 || cleanup_failed=1
	terminate_owned_process "${dbus_server_pid}" "${dbus_server_start_time}" 4 || cleanup_failed=1
	terminate_owned_process "${dbus_pid}" "${dbus_start_time}" 4 || cleanup_failed=1
	if ((cleanup_failed != 0 && status == 0)); then
		status=1
	fi
	exit "${status}"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

run_nested setup env SPEED_OF_CINNAMON_TEST_HOME=1 HOME="${session_home}" XDG_CONFIG_HOME="${config_dir}" XDG_DATA_HOME="${data_dir}" XDG_STATE_HOME="${state_dir}" \
	bash "${repo_dir}/scripts/install-local.sh" >/dev/null

run_xephyr_server Xephyr "${nested_display}" -screen 1024x768x24 -nolisten tcp -auth "${nested_xauthority}" >"${display_log}" 2>&1 &
xephyr_pid=$!
xephyr_identity="$(read_process_identity "${xephyr_pid}")" || {
	printf 'Xephyr exited before process identity capture.\n' >&2
	exit 1
}
IFS=' ' read -r _ xephyr_start_time <<<"${xephyr_identity}"
xephyr_socket_path="${controller_root}/nested-tmp/.X11-unix/X${display_number}"
xephyr_lock_path="${controller_root}/nested-tmp/.X${display_number}-lock"
xephyr_ready="0"
for _ in $(seq 1 100); do
	xephyr_state="$(owned_process_state "${xephyr_pid}" "${xephyr_start_time}")" || break
	[[ ${xephyr_state} != "Z" ]] || break
	if [[ -S ${xephyr_socket_path} && ! -L ${xephyr_socket_path} &&
		-f ${xephyr_lock_path} && ! -L ${xephyr_lock_path} ]]; then
		lock_pid="$(tr -d '[:space:]' <"${xephyr_lock_path}")" || break
		socket_owner="$(stat -c '%u' -- "${xephyr_socket_path}")" || break
		lock_owner="$(stat -c '%u' -- "${xephyr_lock_path}")" || break
		lock_links="$(stat -c '%h' -- "${xephyr_lock_path}")" || break
		if [[ ${socket_owner} == "$(id -u)" && ${lock_owner} == "$(id -u)" &&
		${lock_links} == "1" ]] &&
			xephyr_server_identity="$(read_direct_child_identity "${lock_pid}" "${xephyr_pid}" "${xephyr_start_time}")"; then
			IFS=' ' read -r xephyr_server_state xephyr_server_start_time <<<"${xephyr_server_identity}"
			[[ ${xephyr_server_state} != "Z" ]] || break
			xephyr_server_pid="${lock_pid}"
			socket_before="$(stat -c '%d:%i:%u:%g' -- "${xephyr_socket_path}")" || break
			lock_before="$(stat -c '%d:%i:%u:%h' -- "${xephyr_lock_path}")" || break
			if run_nested x timeout --signal=KILL 1s env -i PATH="${TRUSTED_COMMAND_PATH}" HOME="${HOME}" \
				DISPLAY="${nested_display}" XAUTHORITY="${nested_xauthority}" LC_ALL=C \
				xprop -display "${nested_display}" -root >/dev/null 2>&1; then
				socket_after="$(stat -c '%d:%i:%u:%g' -- "${xephyr_socket_path}")" || break
				lock_after="$(stat -c '%d:%i:%u:%h' -- "${xephyr_lock_path}")" || break
				xephyr_server_identity="$(read_direct_child_identity "${xephyr_server_pid}" "${xephyr_pid}" "${xephyr_start_time}")" || break
				IFS=' ' read -r xephyr_server_state current_server_start <<<"${xephyr_server_identity}"
				lock_pid="$(tr -d '[:space:]' <"${xephyr_lock_path}")" || break
				if [[ ${xephyr_server_state} != "Z" && ${current_server_start} == "${xephyr_server_start_time}" &&
					${lock_pid} == "${xephyr_server_pid}" &&
					${socket_before} == "${socket_after}" && ${lock_before} == "${lock_after}" ]]; then
					xephyr_socket_identity="${socket_after}"
					xephyr_lock_identity="${lock_after}"
					xephyr_ready="1"
					break
				fi
			fi
		fi
	fi
	sleep 0.1
done
if [[ ${xephyr_ready} != "1" ]]; then
	printf 'Xephyr did not become ready.\n' >&2
	sed -n '1,120p' "${display_log}" >&2 || true
	exit 1
fi

verify_xephyr_binding || {
	printf 'Xephyr identity changed before Cinnamon start.\n' >&2
	exit 1
}
run_nested target dconf write /org/cinnamon/enabled-applets "[\"panel1:left:0:menu@cinnamon.org:0\",\"panel1:right:1:${uuid}:1\"]"
run_nested target dconf write /org/cinnamon/panels-enabled '["1:0:bottom"]'
run_nested target cinnamon --replace >"${cinnamon_log}" 2>&1 &
cinnamon_pid=$!
cinnamon_identity="$(read_process_identity "${cinnamon_pid}")" || {
	printf 'Cinnamon exited before process identity capture.\n' >&2
	exit 1
}
IFS=' ' read -r _ cinnamon_start_time <<<"${cinnamon_identity}"
verify_xephyr_binding || {
	printf 'Xephyr identity changed during Cinnamon start.\n' >&2
	exit 1
}

for _ in $(seq 1 180); do
	verify_xephyr_binding || {
		printf 'Xephyr identity changed while Cinnamon was starting.\n' >&2
		exit 1
	}
	if run_gdbus --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
		break
	fi
	if ! cinnamon_state="$(owned_process_state "${cinnamon_pid}" "${cinnamon_start_time}")" || [[ ${cinnamon_state} == "Z" ]]; then
		printf 'Cinnamon session exited before the D-Bus service became ready.\n' >&2
		sed -n '1,200p' "${cinnamon_log}" >&2 || true
		exit 1
	fi
	sleep 0.25
done
if ! run_gdbus --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
	printf 'Cinnamon D-Bus service did not become ready.\n' >&2
	sed -n '1,200p' "${cinnamon_log}" >&2 || true
	exit 1
fi

force_cinnamon_gc() {
	eval_cinnamon 'imports.system.gc(); "ok"' >/dev/null
}

reload_applet_instance() {
	if [[ ${reload_mode} == "module" ]]; then
		run_gdbus --method org.Cinnamon.ReloadXlet "${uuid}" APPLET >/dev/null
		return
	fi

	eval_cinnamon 'global.settings.set_strv("enabled-applets", ["panel1:left:0:menu@cinnamon.org:0"]); "removed"' >/dev/null
	local removed_state="present"
	for _ in $(seq 1 80); do
		removed_state="$(eval_cinnamon "imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\").length===0?\"removed\":\"present\"" 2>/dev/null || true)"
		if [[ ${removed_state} == *"removed"* ]]; then
			break
		fi
		sleep 0.125
	done
	[[ ${removed_state} == *"removed"* ]] || {
		printf 'applet instance did not leave the panel.\n' >&2
		exit 1
	}
	eval_cinnamon "global.settings.set_strv(\"enabled-applets\", [\"panel1:left:0:menu@cinnamon.org:0\",\"panel1:right:1:${uuid}:1\"]); \"added\"" >/dev/null
}

for _ in $(seq 1 120); do
	if [[ "$(eval_cinnamon "imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\").length > 0 ? \"ok\" : \"missing\"" 2>/dev/null)" == *"ok"* ]]; then
		break
	fi
	sleep 0.25
done
if [[ "$(eval_cinnamon "imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\").length > 0 ? \"ok\" : \"missing\"" 2>/dev/null)" != *"ok"* ]]; then
	printf 'Speed of Cinnamon applet did not load in isolated Cinnamon.\n' >&2
	sed -n '1,240p' "${cinnamon_log}" >&2 || true
	exit 1
fi

run_isolated_smoke() {
	if [[ ${smoke_mode} == "none" ]]; then
		return 0
	fi
	run_nested target env SOC_RUN_GUI_LIVE_TESTS=1 SOC_ISOLATED_GUI_SESSION=1 PYTHONDONTWRITEBYTECODE=1 \
		python3 -B - <<'PY'
import sys
import unittest

names = (
    "tests.test_live_paste_editor.LivePasteEditorTest.test_cinnamon_applet_pastes_clipboard_text_into_focused_xed_file",
    "tests.test_live_paste_editor.LivePasteEditorTest.test_cinnamon_applet_menu_click_path_pastes_into_previously_focused_xed_file",
    "tests.test_live_paste_editor.LivePasteEditorTest.test_cinnamon_applet_opens_and_cancels_non_text_clipboard_overwrite_popup",
)
suite = unittest.defaultTestLoader.loadTestsFromNames(names)
result = unittest.TextTestRunner(verbosity=2).run(suite)
if result.skipped:
    print("isolated GUI acceptance does not permit skipped smoke tests", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(0 if result.wasSuccessful() else 1)
PY
}

verify_xephyr_binding || {
	printf 'Xephyr identity changed before smoke start.\n' >&2
	exit 1
}
run_isolated_smoke &
smoke_pid=$!
smoke_identity="$(read_process_identity "${smoke_pid}")" || {
	printf 'Isolated smoke exited before process identity capture.\n' >&2
	exit 1
}
IFS=' ' read -r _ smoke_start_time <<<"${smoke_identity}"
verify_xephyr_binding || {
	printf 'Xephyr identity changed during smoke start.\n' >&2
	exit 1
}
smoke_deadline=$((SECONDS + 60))
while true; do
	if ! smoke_state="$(owned_process_state "${smoke_pid}" "${smoke_start_time}")"; then
		[[ ! -e "/proc/${smoke_pid}" ]] || {
			printf 'Isolated smoke identity became unavailable.\n' >&2
			exit 1
		}
		break
	fi
	[[ ${smoke_state} != "Z" ]] || break
	verify_xephyr_binding || {
		printf 'Xephyr identity changed during smoke.\n' >&2
		exit 1
	}
	if ((SECONDS >= smoke_deadline)); then
		printf 'Isolated smoke exceeded its bounded deadline.\n' >&2
		exit 1
	fi
	sleep 0.05
done
set +e
wait "${smoke_pid}"
smoke_status=$?
set -e
smoke_pid=""
smoke_start_time=""
((smoke_status == 0)) || exit "${smoke_status}"

if [[ ${fault_injection} == "1" ]]; then
	fault_result="$(eval_cinnamon "let xs=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\");let a=xs[0];for(let i=0;i<3;i++){a._runGuarded(\"fault-injection\",function(){throw new Error(\"explicit-test-fault\")},null)};a._disabledErrorGroups[\"fault-injection\"]===true?\"ok\":\"fail\"")"
	[[ ${fault_result} == *'ok'* ]] || {
		printf 'fault-injection wrapper did not disable its group: %s\n' "${fault_result}" >&2
		exit 1
	}
fi

if [[ ${force_gc} == "1" ]]; then
	force_cinnamon_gc
fi

initial_rss_kb="$(ps -o rss= -p "${cinnamon_pid}" | awk '{print $1}' | head -n1)"
initial_rss_kb="${initial_rss_kb:-0}"
max_rss_kb="${initial_rss_kb}"

for cycle in $(seq 1 "${cycles}"); do
	verify_xephyr_binding || {
		printf 'Xephyr identity changed before reload cycle %s.\n' "${cycle}" >&2
		exit 1
	}
	started_ms="$(date +%s%3N)"
	reload_applet_instance
	finished_ms="$(date +%s%3N)"
	verify_xephyr_binding || {
		printf 'Xephyr identity changed during reload cycle %s.\n' "${cycle}" >&2
		exit 1
	}
	elapsed_ms=$((finished_ms - started_ms))
	if ((elapsed_ms > heartbeat_limit_ms * 20)); then
		printf 'Cinnamon reload stalled at cycle %s (%sms).\n' "${cycle}" "${elapsed_ms}" >&2
		exit 1
	fi
	run_gdbus --method org.freedesktop.DBus.Peer.Ping >/dev/null
	state="missing"
	for _ in $(seq 1 80); do
		state="$(eval_cinnamon "let xs=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\");xs.length===1?String(xs[0].lifecycleState):\"missing\"" 2>/dev/null || true)"
		if [[ ${state} == *"RUNNING"* ]]; then
			break
		fi
		sleep 0.125
	done
	[[ ${state} == *"RUNNING"* ]] || {
		printf 'unexpected applet state after cycle %s: %s\n' "${cycle}" "${state}" >&2
		exit 1
	}
	if [[ ${force_gc} == "1" ]]; then
		force_cinnamon_gc
	fi
	current_rss_kb="$(ps -o rss= -p "${cinnamon_pid}" | awk '{print $1}' | head -n1)"
	current_rss_kb="${current_rss_kb:-0}"
	if ((current_rss_kb > max_rss_kb)); then
		max_rss_kb="${current_rss_kb}"
	fi
done

crash_error_log="${test_root}/cinnamon-errors.log"
if rg -n -i 'segmentation fault|segfault|core dumped|out of memory|oom-kill|Cjs-CRITICAL|JS ERROR' "${cinnamon_log}" >"${crash_error_log}" 2>/dev/null; then
	printf 'Cinnamon logged a crash or GJS error during the isolated run.\n' >&2
	sed -n '1,120p' "${crash_error_log}" >&2 || true
	exit 1
fi

final_rss_kb="$(ps -o rss= -p "${cinnamon_pid}" | awk '{print $1}' | head -n1)"
final_rss_kb="${final_rss_kb:-0}"
if ((final_rss_kb - initial_rss_kb > 51200 || max_rss_kb - initial_rss_kb > 51200)); then
	printf 'Cinnamon RSS grew by more than 50 MiB (initial=%sKB final=%sKB max=%sKB).\n' "${initial_rss_kb}" "${final_rss_kb}" "${max_rss_kb}" >&2
	exit 1
fi

printf 'Applet crash-safety passed: %s isolated reload cycles, Cinnamon remained reachable, RSS delta <= 50 MiB.\n' "${cycles}"
