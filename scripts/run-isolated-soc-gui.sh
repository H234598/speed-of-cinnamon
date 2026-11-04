#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

readonly TRUSTED_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
readonly EXPECTED_HOME="/home/teladi"
readonly XVFB_BIN="${EXPECTED_HOME}/.local/bin/Xvfb"
readonly EXPECTED_XVFB_SHA256="743d451bafa4ebd6b4ad6302c66aa4c655d245bd2c3ba1b48092857421f8e145"
readonly BWRAP_BIN="/usr/bin/bwrap"
readonly XAUTH_BIN="/usr/bin/xauth"
readonly DBUS_DAEMON_BIN="/usr/bin/dbus-daemon"
readonly XPROP_BIN="/usr/bin/xprop"
readonly TIMEOUT_BIN="/usr/bin/timeout"
readonly ENV_BIN="/usr/bin/env"
readonly PYTHON_BIN="/usr/bin/python3.14"
readonly TOTAL_TIMEOUT_SECONDS=120
readonly FULL_INNER_TIMEOUT_SECONDS=100
readonly CLEANUP_RESERVE_SECONDS=8
readonly READY_TIMEOUT_SECONDS=10
readonly PROBE_OUTER_TIMEOUT_SECONDS=30
readonly DRAIN_MARGIN_SECONDS=5
readonly SESSION_KILL_AFTER_SECONDS=2
readonly PROCESS_TERM_GRACE_SECONDS=2
readonly SMOKE_MODE="clipboard-popup"
readonly HOST_DISPLAY_NUMBER="70"
# shellcheck disable=SC2034
readonly NESTED_DISPLAY_NUMBER="90"
readonly INNER_REPO="/opt/soc-repo"
readonly INNER_XVFB="/opt/soc-tools/Xvfb"
readonly INNER_XPROP="/opt/soc-tools/xprop"
readonly INNER_HOST_AUTHORITY="/run/soc-auth/host.Xauthority"
# shellcheck disable=SC2034
readonly INNER_NESTED_AUTHORITY="/run/soc-auth/nested.Xauthority"
readonly INNER_WORK_ROOT="/run/soc-work"
readonly INNER_CONTROLLER_ROOT="/run/soc-controller"

export PATH="${TRUSTED_PATH}"

die() {
	local status="$1"
	shift
	printf 'run-isolated-soc-gui: %s\n' "$*" >&2
	exit "${status}"
}

install_guard_fail() {
	printf 'INSTALL_GUARD_FAIL %s\n' "$1" >&2
	exit 2
}

mode_aware_preflight_fail() {
	if [[ ${outer_mode:-} == "install-guard-probe" ]]; then
		printf '%s\n' 'INSTALL_GUARD_FAIL OUTER_HELPER' >&2
		exit 2
	fi
	die "$1" "${*:2}"
}

canonical_dir() {
	local path="$1"
	[[ -d ${path} && ! -L ${path} ]] || return 1
	[[ "$(/usr/bin/realpath -e -- "${path}")" == "${path}" ]]
}

validate_owned_private_dir() {
	local path="$1"
	local mode
	canonical_dir "${path}" || return 1
	[[ "$(/usr/bin/stat -c '%u' -- "${path}")" == "$(/usr/bin/id -u)" ]] || return 1
	mode="$(/usr/bin/stat -c '%a' -- "${path}")"
	(((0${mode} & 077) == 0))
}

validate_fixed_tool() {
	[[ -f $1 && ! -L $1 && -x $1 ]]
}

validate_trusted_xauth() {
	local metadata uid links mode
	[[ ${XAUTH_BIN} == "/usr/bin/xauth" && -f ${XAUTH_BIN} && ! -L ${XAUTH_BIN} && -x ${XAUTH_BIN} ]] || return 1
	metadata="$(/usr/bin/stat -c '%u:%h:%a' -- "${XAUTH_BIN}")" || return 1
	IFS=':' read -r uid links mode <<<"${metadata}"
	[[ ${uid} == "0" && ${links} == "1" ]] || return 1
	(((0${mode} & 022) == 0))
}

validate_trusted_dbus() {
	local metadata uid links mode
	[[ ${DBUS_DAEMON_BIN} == "/usr/bin/dbus-daemon" && -f ${DBUS_DAEMON_BIN} && ! -L ${DBUS_DAEMON_BIN} && -x ${DBUS_DAEMON_BIN} ]] || return 1
	metadata="$(/usr/bin/stat -c '%u:%h:%a' -- "${DBUS_DAEMON_BIN}")" || return 1
	IFS=':' read -r uid links mode <<<"${metadata}"
	[[ ${uid} == "0" && ${links} == "1" ]] || return 1
	(((0${mode} & 022) == 0))
}

validate_xvfb() {
	local digest mode
	[[ ${XVFB_BIN} == "${EXPECTED_HOME}/.local/bin/Xvfb" ]] || return 1
	validate_owned_private_dir "${EXPECTED_HOME}" || return 1
	validate_owned_private_dir "${EXPECTED_HOME}/.local" || return 1
	validate_owned_private_dir "${EXPECTED_HOME}/.local/bin" || return 1
	[[ -f ${XVFB_BIN} && ! -L ${XVFB_BIN} && -x ${XVFB_BIN} ]] || return 1
	[[ "$(/usr/bin/realpath -e -- "${XVFB_BIN}")" == "${XVFB_BIN}" ]] || return 1
	[[ "$(/usr/bin/stat -c '%u' -- "${XVFB_BIN}")" == "$(/usr/bin/id -u)" ]] || return 1
	mode="$(/usr/bin/stat -c '%a' -- "${XVFB_BIN}")"
	[[ ${mode} == "755" && "$(/usr/bin/stat -c '%h' -- "${XVFB_BIN}")" == "1" ]] || return 1
	digest="$(/usr/bin/sha256sum -- "${XVFB_BIN}")" || return 1
	[[ ${digest%% *} == "${EXPECTED_XVFB_SHA256}" ]]
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
		/usr/bin/sleep 0.05
	done
	return 1
}

signal_owned_process() {
	local pid="$1"
	local expected_start="$2"
	local signal_name="$3"
	"${PYTHON_BIN}" -I -S - "${pid}" "${expected_start}" "${signal_name}" <<'PY'
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
    raise SystemExit(1)
pidfd = os.pidfd_open(pid, 0)
try:
    rebound = identity()
    if rebound is None or rebound[0] == b"Z" or rebound[1] != expected_start:
        raise SystemExit(1)
    signal.pidfd_send_signal(pidfd, signum, None, 0)
finally:
    os.close(pidfd)
PY
}

terminate_owned_process() {
	local pid="$1"
	local expected_start="$2"
	local grace="${3:-${PROCESS_TERM_GRACE_SECONDS}}"
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
		/usr/bin/sleep 0.05
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
		/usr/bin/sleep 0.05
	done
	return 1
}

verify_namespace_child() {
	"${PYTHON_BIN}" -I -S - <<'PY'
import os
import stat

if os.getpid() <= 1 or os.getppid() <= 0:
    raise SystemExit(1)
uid_map = open("/proc/self/uid_map", encoding="ascii").read().strip().splitlines()
if uid_map == ["0          0 4294967295"] or not uid_map:
    raise SystemExit(1)
if os.readlink("/proc/1/ns/pid") != os.readlink("/proc/self/ns/pid"):
    raise SystemExit(1)
mounts = {}
with open("/proc/self/mountinfo", encoding="utf-8") as handle:
    for line in handle:
        before, after = line.rstrip("\n").split(" - ", 1)
        fields = before.split()
        mounts[fields[4]] = after.split()[0]
if mounts.get("/proc") != "proc" or mounts.get("/tmp") != "tmpfs" or mounts.get("/run") != "tmpfs":
    raise SystemExit(1)
for path in ("/tmp/.X11-unix", "/run/soc-controller", "/run/soc-work", "/run/soc-auth"):
    entry = os.lstat(path)
    if stat.S_ISLNK(entry.st_mode) or not stat.S_ISDIR(entry.st_mode):
        raise SystemExit(1)
if os.path.lexists("/tmp/.X11-unix/X0"):
    raise SystemExit(1)
for name in ("DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY"):
    if os.environ.get(name):
        raise SystemExit(1)
PY
}

run_host_client() {
	"${BWRAP_BIN}" --die-with-parent --new-session --clearenv \
		--ro-bind / / --proc /proc --dev /dev --tmpfs /tmp --dir /tmp/.X11-unix \
		--tmpfs /run --dir /run/soc-auth \
		--ro-bind "${INNER_HOST_AUTHORITY}" "${INNER_HOST_AUTHORITY}" \
		--ro-bind "${INNER_CONTROLLER_ROOT}/host-tmp/.X11-unix/X${HOST_DISPLAY_NUMBER}" "/tmp/.X11-unix/X${HOST_DISPLAY_NUMBER}" \
		--setenv PATH "${TRUSTED_PATH}" --setenv HOME /tmp \
		--setenv DISPLAY ":${HOST_DISPLAY_NUMBER}" --setenv XAUTHORITY "${INNER_HOST_AUTHORITY}" \
		--setenv LC_ALL C -- "${INNER_XPROP}" -display ":${HOST_DISPLAY_NUMBER}" -root
}

create_private_xauthority() {
	local authority_path="$1"
	local display="$2"
	local cookie
	cookie="$(
		"${PYTHON_BIN}" -I -S - <<'PY'
import secrets
print(secrets.token_hex(16))
PY
	)" || return 1
	[[ ${cookie} =~ ^[0-9a-f]{32}$ ]] || return 1
	"${ENV_BIN}" -i PATH="${TRUSTED_PATH}" HOME="${INNER_WORK_ROOT}/home" LC_ALL=C \
		"${XAUTH_BIN}" -f "${authority_path}" add "${display}" MIT-MAGIC-COOKIE-1 "${cookie}" \
		>/dev/null 2>&1 || return 1
	cookie=""
	/usr/bin/chmod 600 "${authority_path}" || return 1
	"${PYTHON_BIN}" -I -S - "${authority_path}" <<'PY'
import os
import stat
import sys

entry = os.lstat(sys.argv[1])
if (
    not stat.S_ISREG(entry.st_mode)
    or stat.S_IMODE(entry.st_mode) != 0o600
    or entry.st_uid != os.geteuid()
    or entry.st_nlink != 1
):
    raise SystemExit(1)
PY
}

namespace_child_main() {
	local namespace_child_mode="${1:-}"
	local inner_timeout="${SOC_GUI_INNER_TIMEOUT_SECONDS:-}"
	# shellcheck disable=SC2015
	[[ ${inner_timeout} =~ ^[1-9][0-9]*$ && ${#inner_timeout} -le 3 ]] &&
		((inner_timeout <= FULL_INNER_TIMEOUT_SECONDS)) || die 2 "invalid inner supervisor budget"
	if [[ ${namespace_child_mode} != "guard-probe" ]]; then
		verify_namespace_child || die 2 "private namespace verification failed"
	fi
	/usr/bin/mkdir -p \
		"${INNER_CONTROLLER_ROOT}/host-tmp/.X11-unix" \
		"${INNER_CONTROLLER_ROOT}/nested-tmp/.X11-unix" \
		"${INNER_CONTROLLER_ROOT}/dbus" \
		"${INNER_WORK_ROOT}/home" \
		"${INNER_WORK_ROOT}/tmp" \
		"${INNER_WORK_ROOT}/session" \
		/run/soc-auth
	/usr/bin/chmod 700 \
		"${INNER_CONTROLLER_ROOT}" \
		"${INNER_WORK_ROOT}" \
		"${INNER_WORK_ROOT}/home" \
		"${INNER_WORK_ROOT}/tmp" \
		"${INNER_WORK_ROOT}/session" \
		"${INNER_CONTROLLER_ROOT}/host-tmp" \
		"${INNER_CONTROLLER_ROOT}/nested-tmp" \
		"${INNER_CONTROLLER_ROOT}/dbus" \
		/run/soc-auth
	/usr/bin/chmod 1777 \
		"${INNER_CONTROLLER_ROOT}/host-tmp/.X11-unix" \
		"${INNER_CONTROLLER_ROOT}/nested-tmp/.X11-unix"
	if [[ ${namespace_child_mode} == "guard-probe" ]]; then
		/usr/bin/bash "${INNER_REPO}/scripts/applet-crash-safety.sh" --namespace-guard-probe
		exit $?
	fi
	if [[ ${namespace_child_mode} == "install-guard-probe" ]]; then
		/usr/bin/bash "${INNER_REPO}/scripts/applet-crash-safety.sh" --install-guard-probe
		exit $?
	fi
	create_private_xauthority "${INNER_HOST_AUTHORITY}" ":${HOST_DISPLAY_NUMBER}" ||
		die 2 "private host Xauthority creation failed"

	local host_pid=""
	local host_start_time=""
	local host_server_pid=""
	local host_server_start_time=""
	local core_pid=""
	local core_start_time=""
	local host_socket="${INNER_CONTROLLER_ROOT}/host-tmp/.X11-unix/X${HOST_DISPLAY_NUMBER}"
	local host_lock="${INNER_CONTROLLER_ROOT}/host-tmp/.X${HOST_DISPLAY_NUMBER}-lock"
	local host_socket_identity=""
	local host_lock_identity=""
	local cleanup_failed=0

	# shellcheck disable=SC2317,SC2329
	namespace_cleanup() {
		local status=$?
		trap - EXIT INT TERM HUP
		set +e
		terminate_owned_process "${core_pid}" "${core_start_time}" 3 || cleanup_failed=1
		terminate_owned_process "${host_server_pid}" "${host_server_start_time}" 3 || cleanup_failed=1
		terminate_owned_process "${host_pid}" "${host_start_time}" 3 || cleanup_failed=1
		if ((cleanup_failed != 0 && status == 0)); then
			status=1
		fi
		exit "${status}"
	}
	trap namespace_cleanup EXIT
	trap 'exit 129' HUP
	trap 'exit 130' INT
	trap 'exit 143' TERM

	"${BWRAP_BIN}" --die-with-parent --new-session --clearenv \
		--ro-bind / / --proc /proc --dev /dev \
		--bind "${INNER_CONTROLLER_ROOT}/host-tmp" /tmp \
		--tmpfs /run --dir /run/soc-auth \
		--ro-bind "${INNER_HOST_AUTHORITY}" "${INNER_HOST_AUTHORITY}" \
		--setenv PATH "${TRUSTED_PATH}" --setenv HOME /tmp \
		--setenv XAUTHORITY "${INNER_HOST_AUTHORITY}" --setenv LC_ALL C -- \
		"${INNER_XVFB}" ":${HOST_DISPLAY_NUMBER}" -screen 0 1280x800x24 -nolisten tcp \
		-auth "${INNER_HOST_AUTHORITY}" -noreset >"${INNER_WORK_ROOT}/xvfb.log" 2>&1 &
	host_pid=$!
	# shellcheck disable=SC2034
	local identity state lock_pid readiness_deadline socket_before lock_before
	identity="$(read_process_identity "${host_pid}")" || die 1 "Xvfb exited before ownership capture"
	IFS=' ' read -r _ host_start_time <<<"${identity}"

	verify_host_binding() {
		local current_identity current_state current_start current_lock socket_now lock_now
		[[ -n ${host_pid} && -n ${host_start_time} &&
			-n ${host_server_pid} && -n ${host_server_start_time} &&
			-n ${host_socket_identity} && -n ${host_lock_identity} ]] || return 1
		current_identity="$(read_direct_child_identity "${host_server_pid}" "${host_pid}" "${host_start_time}")" || return 1
		IFS=' ' read -r current_state current_start <<<"${current_identity}"
		[[ ${current_state} != "Z" && ${current_start} == "${host_server_start_time}" ]] || return 1
		current_lock="$(/usr/bin/tr -d '[:space:]' <"${host_lock}")" || return 1
		[[ ${current_lock} == "${host_server_pid}" ]] || return 1
		socket_now="$(/usr/bin/stat -c '%d:%i:%u:%g' -- "${host_socket}")" || return 1
		lock_now="$(/usr/bin/stat -c '%d:%i:%u:%h' -- "${host_lock}")" || return 1
		[[ ${socket_now} == "${host_socket_identity}" && ${lock_now} == "${host_lock_identity}" ]] || return 1
		run_host_client >/dev/null 2>&1 || return 1
		current_identity="$(read_direct_child_identity "${host_server_pid}" "${host_pid}" "${host_start_time}")" || return 1
		IFS=' ' read -r current_state current_start <<<"${current_identity}"
		[[ ${current_state} != "Z" && ${current_start} == "${host_server_start_time}" ]] || return 1
		current_lock="$(/usr/bin/tr -d '[:space:]' <"${host_lock}")" || return 1
		socket_now="$(/usr/bin/stat -c '%d:%i:%u:%g' -- "${host_socket}")" || return 1
		lock_now="$(/usr/bin/stat -c '%d:%i:%u:%h' -- "${host_lock}")" || return 1
		[[ ${current_lock} == "${host_server_pid}" &&
			${socket_now} == "${host_socket_identity}" &&
			${lock_now} == "${host_lock_identity}" ]]
	}

	readiness_deadline=$((SECONDS + READY_TIMEOUT_SECONDS))
	while ((SECONDS <= readiness_deadline)); do
		state="$(owned_process_state "${host_pid}" "${host_start_time}")" || die 1 "Xvfb identity became unavailable"
		[[ ${state} != "Z" ]] || die 1 "Xvfb exited before readiness"
		if [[ -S ${host_socket} && ! -L ${host_socket} && -f ${host_lock} && ! -L ${host_lock} ]]; then
			lock_pid="$(/usr/bin/tr -d '[:space:]' <"${host_lock}")" || die 1 "Xvfb lock is unreadable"
			if [[ "$(/usr/bin/stat -c '%u' -- "${host_socket}")" == "$(/usr/bin/id -u)" && "$(/usr/bin/stat -c '%u:%h' -- "${host_lock}")" == "$(/usr/bin/id -u):1" ]] &&
				identity="$(read_direct_child_identity "${lock_pid}" "${host_pid}" "${host_start_time}")"; then
				IFS=' ' read -r state host_server_start_time <<<"${identity}"
				[[ ${state} != "Z" ]] || die 1 "Xvfb server exited before readiness"
				host_server_pid="${lock_pid}"
				host_socket_identity="$(/usr/bin/stat -c '%d:%i:%u:%g' -- "${host_socket}")" || die 1 "Xvfb socket identity failed"
				host_lock_identity="$(/usr/bin/stat -c '%d:%i:%u:%h' -- "${host_lock}")" || die 1 "Xvfb lock identity failed"
				if verify_host_binding; then
					break
				fi
				host_server_pid=""
				host_server_start_time=""
				host_socket_identity=""
				host_lock_identity=""
			fi
		fi
		/usr/bin/sleep 0.05
	done
	[[ -n ${host_socket_identity} && -n ${host_lock_identity} ]] || die 1 "Xvfb readiness timed out"
	verify_host_binding || die 1 "Xvfb binding changed before session start"

	if [[ ${SOC_XVFB_PROBE:-0} == "1" ]]; then
		run_host_client >/dev/null 2>&1 || die 1 "private Xvfb xprop probe failed"
		terminate_owned_process "${host_server_pid}" "${host_server_start_time}" 3 ||
			die 1 "Xvfb server cleanup failed"
		host_server_pid=""
		host_server_start_time=""
		terminate_owned_process "${host_pid}" "${host_start_time}" 3 ||
			die 1 "Xvfb cleanup failed"
		host_pid=""
		host_start_time=""
		trap - EXIT INT TERM HUP
		exit 0
	fi

	local -a core_args=()
	if [[ ${namespace_child_mode} == "dbus-probe" ]]; then
		core_args=(--dbus-probe)
	fi
	SOC_RUN_GUI_LIVE_TESTS=1 APPLET_CRASH_SAFETY_SMOKE_MODE="${SMOKE_MODE}" \
		/usr/bin/bash "${INNER_REPO}/scripts/applet-crash-safety.sh" "${core_args[@]}" &
	core_pid=$!
	identity="$(read_process_identity "${core_pid}")" || die 1 "session core exited before ownership capture"
	IFS=' ' read -r _ core_start_time <<<"${identity}"
	verify_host_binding || die 1 "Xvfb binding changed after session start"
	local core_deadline=$((SECONDS + inner_timeout))
	while true; do
		if ! state="$(owned_process_state "${core_pid}" "${core_start_time}")"; then
			[[ ! -e "/proc/${core_pid}" ]] || die 1 "session core identity became unavailable"
			break
		fi
		[[ ${state} != "Z" ]] || break
		verify_host_binding || die 1 "Xvfb binding changed during session"
		if ((SECONDS >= core_deadline)); then
			die 124 "session core exceeded bounded deadline"
		fi
		/usr/bin/sleep 0.05
	done
	set +e
	wait "${core_pid}"
	local core_status=$?
	set -e
	core_pid=""
	core_start_time=""
	terminate_owned_process "${host_server_pid}" "${host_server_start_time}" 3 || die 1 "Xvfb server cleanup failed"
	host_server_pid=""
	host_server_start_time=""
	terminate_owned_process "${host_pid}" "${host_start_time}" 3 || die 1 "Xvfb cleanup failed"
	host_pid=""
	host_start_time=""
	trap - EXIT INT TERM HUP
	exit "${core_status}"
}

if [[ ${SOC_RUN_GUI_LIVE_TESTS:-0} != "1" ]]; then
	die 2 "refusing GUI test; set SOC_RUN_GUI_LIVE_TESTS=1 explicitly"
fi

inner_supervisor_main() {
	local mode="${1:-}"
	local nonce="${2:-}"
	local inner_timeout="${3:-}"
	local inner_cleanup_grace="${4:-}"
	local child_mode
	[[ ${mode} == "full" || ${mode} == "probe" || ${mode} == "xvfb-probe" || ${mode} == "namespace-guard-probe" || ${mode} == "dbus-probe" || ${mode} == "install-guard-probe" ]] || die 2 "invalid inner supervisor mode"
	[[ ${nonce} =~ ^[0-9a-f]{64}$ ]] || die 2 "invalid inner supervisor nonce"
	[[ ${inner_timeout} =~ ^[1-9][0-9]*$ && ${inner_cleanup_grace} =~ ^[1-9][0-9]*$ ]] || die 2 "invalid inner supervisor budget"
	if [[ ${mode} != "namespace-guard-probe" ]]; then
		verify_namespace_child || die 2 "private namespace verification failed"
	fi
	/usr/bin/chmod 700 /run/soc-work /run/soc-auth /run/soc-controller
	/usr/bin/mkdir -p /run/soc-work/home
	/usr/bin/chmod 700 /run/soc-work/home
	if [[ ${mode} == "probe" ]]; then
		child_mode="--namespace-probe-child"
	elif [[ ${mode} == "xvfb-probe" ]]; then
		child_mode="--namespace-child"
	elif [[ ${mode} == "namespace-guard-probe" ]]; then
		child_mode="--namespace-guard-probe-child"
	elif [[ ${mode} == "dbus-probe" ]]; then
		child_mode="--dbus-probe-child"
	elif [[ ${mode} == "install-guard-probe" ]]; then
		child_mode="--install-guard-probe-child"
	else
		child_mode="--namespace-child"
	fi
	exec 3>&1
	exec 1>/dev/null
	export SOC_GUI_INNER_TIMEOUT_SECONDS="${inner_timeout}"
	"${PYTHON_BIN}" -I -S "${INNER_REPO}/scripts/gui-session-supervisor.py" \
		--timeout "${inner_timeout}" \
		--term-grace "${inner_cleanup_grace}" \
		--completion-fd 3 \
		--completion-nonce "${nonce}" \
		--stdout-log /run/soc-work/controller.stdout \
		--stderr-log /run/soc-work/controller.stderr \
		-- /usr/bin/bash "${INNER_REPO}/scripts/run-isolated-soc-gui.sh" "${child_mode}" &
	local supervisor_pid=$!
	exec 3>&-
	set +e
	wait "${supervisor_pid}"
	local supervisor_status=$?
	set -e
	if [[ ${child_mode} == "--install-guard-probe-child" ]]; then
		set +e
		install_result_line="$(${PYTHON_BIN} -I -S "${INNER_REPO}/scripts/gui-session-supervisor.py" \
			--print-install-guard-result /run/soc-controller/install-result 2>/dev/null)"
		install_result_status=$?
		set -e
		if ((install_result_status != 0)); then
			printf '%s\n' 'INSTALL_GUARD_FAIL CORE_FRAME' >&2
			supervisor_status=1
		elif ((supervisor_status == 0)); then
			[[ ${install_result_line} == "OK" ]] || {
				printf '%s\n' 'INSTALL_GUARD_FAIL CORE_STATUS' >&2
				supervisor_status=1
			}
		else
			case "${install_result_line}" in
			OK | ROOT_FAIL\ ROOT_META | ROOT_FAIL\ ROOT_EUID | ROOT_FAIL\ ROOT_UID_MAP | ROOT_FAIL\ ROOT_FDINFO | ROOT_FAIL\ ROOT_MOUNT | CHAIN_FAIL\ OWNER | CHAIN_FAIL\ MODE | CHAIN_FAIL\ META | CHAIN_FAIL\ FD | CHAIN_FAIL\ MISSING | INSTALL_GUARD_FAIL\ CORE_FD | INSTALL_GUARD_FAIL\ CORE_FRAME | INSTALL_GUARD_FAIL\ CORE_STATUS | INSTALL_GUARD_FAIL\ CORE_STATUS_OK_NONZERO | INSTALL_GUARD_FAIL\ CORE_STATUS_FAIL_ZERO | INSTALL_GUARD_FAIL\ CORE_STATUS_ROOT_EXIT | INSTALL_GUARD_FAIL\ CORE_STATUS_CHAIN_EXIT | INSTALL_GUARD_FAIL\ CORE_STATUS_MULTIPLE | INSTALL_GUARD_FAIL\ CORE_PARSER_EXIT | INSTALL_GUARD_FAIL\ CORE_PARSER_EMPTY | INSTALL_GUARD_FAIL\ CORE_PARSER_MULTIPLE | INSTALL_GUARD_FAIL\ CORE_PARSER_CONTROL | INSTALL_GUARD_FAIL\ CORE_PARSER_UNKNOWN | INSTALL_GUARD_FAIL\ CORE_RESULT_MISSING | INSTALL_GUARD_FAIL\ CORE_RESULT_EMPTY | INSTALL_GUARD_FAIL\ CORE_RESULT_META | INSTALL_GUARD_FAIL\ CORE_RESULT_FRAME | INSTALL_GUARD_FAIL\ CORE_RESULT_CODE | INSTALL_GUARD_FAIL\ CORE_RESULT_INIT | INSTALL_GUARD_FAIL\ CORE_RESULT_STATUS_FD | INSTALL_GUARD_FAIL\ CORE_RESULT_STATUS_SPAWN | INSTALL_GUARD_FAIL\ CORE_RESULT_STATUS_FRAME | INSTALL_GUARD_FAIL\ CORE_RESULT_WRITE)
				printf '%s\n' "${install_result_line}" >&2
				;;
			*)
				printf '%s\n' 'INSTALL_GUARD_FAIL CORE_FRAME' >&2
				;;
			esac
		fi
	elif ((supervisor_status != 0)) && [[ -f /run/soc-work/controller.stderr ]]; then
		"${PYTHON_BIN}" -I -S "${INNER_REPO}/scripts/gui-session-supervisor.py" \
			--print-sanitized-tail /run/soc-work/controller.stderr >&2 || true
	fi
	exit "${supervisor_status}"
}

if [[ $# -eq 1 && $1 == "--namespace-child" ]]; then
	namespace_child_main
fi
if [[ $# -eq 1 && $1 == "--namespace-probe-child" ]]; then
	verify_namespace_child || die 2 "private namespace verification failed"
	/usr/bin/sleep 0.05
	exit 0
fi
if [[ $# -eq 1 && $1 == "--namespace-guard-probe-child" ]]; then
	namespace_child_main guard-probe
fi
if [[ $# -eq 1 && $1 == "--dbus-probe-child" ]]; then
	namespace_child_main dbus-probe
fi
if [[ $# -eq 1 && $1 == "--install-guard-probe-child" ]]; then
	namespace_child_main install-guard-probe
fi
if [[ $# -eq 1 && $1 == "--xvfb-probe-child" ]]; then
	SOC_XVFB_PROBE=1 namespace_child_main
fi
if [[ $# -eq 5 && $1 == "--inner-supervisor" ]]; then
	inner_supervisor_main "$2" "$3" "$4" "$5"
fi

outer_mode="full"
if [[ $# -eq 1 && $1 == "--probe" ]]; then
	outer_mode="probe"
elif [[ $# -eq 1 && $1 == "--xvfb-probe" ]]; then
	outer_mode="xvfb-probe"
elif [[ $# -eq 1 && $1 == "--namespace-guard-probe" ]]; then
	outer_mode="namespace-guard-probe"
elif [[ $# -eq 1 && $1 == "--dbus-probe" ]]; then
	outer_mode="dbus-probe"
elif [[ $# -eq 1 && $1 == "--install-guard-probe" ]]; then
	outer_mode="install-guard-probe"
elif [[ $# -ne 0 ]]; then
	if [[ ${1:-} == --install-guard-probe* ]]; then
		install_guard_fail ARG
	fi
	die 2 "arguments are not accepted"
fi

if [[ ${outer_mode} == "install-guard-probe" ]]; then
	[[ ${SOC_RUN_INSTALL_GUARD_PROBE:-0} == "1" ]] || install_guard_fail OPTIN
	[[ ${SOC_RUN_GUI_LIVE_TESTS:-0} == "1" ]] || install_guard_fail ENV
	[[ ${HOME:-} == "${EXPECTED_HOME}" ]] || install_guard_fail ENV
fi

inner_timeout="${FULL_INNER_TIMEOUT_SECONDS}"
outer_timeout="${TOTAL_TIMEOUT_SECONDS}"
inner_cleanup_grace="${SESSION_KILL_AFTER_SECONDS}"
if [[ ${outer_mode} != "full" ]]; then
	inner_timeout="${READY_TIMEOUT_SECONDS}"
	outer_timeout="${PROBE_OUTER_TIMEOUT_SECONDS}"
fi
((inner_timeout + 2 * inner_cleanup_grace + DRAIN_MARGIN_SECONDS < outer_timeout)) ||
	mode_aware_preflight_fail 2 "invalid mode deadline"

script_path="$(/usr/bin/realpath -e -- "${BASH_SOURCE[0]}")" || mode_aware_preflight_fail 2 "adapter path is unavailable"
repo_dir="$(/usr/bin/dirname -- "$(/usr/bin/dirname -- "${script_path}")")"
canonical_dir "${repo_dir}" || mode_aware_preflight_fail 2 "repository path is unsafe"
supervisor_path="${repo_dir}/scripts/gui-session-supervisor.py"
[[ -f ${supervisor_path} && ! -L ${supervisor_path} ]] || mode_aware_preflight_fail 2 "GUI supervisor is missing or unsafe"
validate_fixed_tool "${BWRAP_BIN}" || mode_aware_preflight_fail 2 "bubblewrap is missing or unsafe"
validate_fixed_tool "${PYTHON_BIN}" || mode_aware_preflight_fail 2 "Python runtime is missing or unsafe"

if [[ ${outer_mode} == "full" || ${outer_mode} == "xvfb-probe" || ${outer_mode} == "namespace-guard-probe" || ${outer_mode} == "dbus-probe" || ${outer_mode} == "install-guard-probe" ]]; then
	[[ ${HOME:-} == "${EXPECTED_HOME}" ]] || mode_aware_preflight_fail 2 "HOME is not the fixed account home"
	validate_xvfb || mode_aware_preflight_fail 2 "trusted Xvfb validation failed"
	validate_trusted_xauth || mode_aware_preflight_fail 2 "SETUP_XAUTH_TRUST"
	validate_trusted_dbus || mode_aware_preflight_fail 2 "SETUP_DBUS_TRUST"
	validate_fixed_tool "${XPROP_BIN}" || mode_aware_preflight_fail 2 "xprop is missing or unsafe"
	validate_fixed_tool "${TIMEOUT_BIN}" || mode_aware_preflight_fail 2 "timeout is missing or unsafe"
fi

completion_nonce="$(
	"${PYTHON_BIN}" -I -S - <<'PY'
import secrets
print(secrets.token_hex(32))
PY
)" || mode_aware_preflight_fail 2 "completion nonce generation failed"
[[ ${completion_nonce} =~ ^[0-9a-f]{64}$ ]] || mode_aware_preflight_fail 2 "completion nonce generation failed"

bwrap_command=(
	"${BWRAP_BIN}"
	--die-with-parent
	--new-session
	--unshare-all
	--clearenv
	--ro-bind /usr /usr
	--symlink usr/bin /bin
	--symlink usr/sbin /sbin
	--symlink usr/lib /lib
	--symlink usr/lib64 /lib64
	--ro-bind /etc /etc
	--dir /var
	--ro-bind /var/lib /var/lib
	--proc /proc
	--dev /dev
	--tmpfs /tmp
	--dir /tmp/.X11-unix
	--tmpfs /run
	--dir /run/soc-work
	--dir /run/soc-auth
	--dir /run/soc-controller
	--dir /opt
	--ro-bind "${repo_dir}" "${INNER_REPO}"
)
probe_env=()
if [[ ${outer_mode} == "xvfb-probe" ]]; then
	probe_env+=(--setenv SOC_XVFB_PROBE 1)
fi
append_xvfb_tool_mounts() {
	bwrap_command+=(
		--dir /opt/soc-tools
		--ro-bind "${XVFB_BIN}" "${INNER_XVFB}"
		--ro-bind "${XPROP_BIN}" "${INNER_XPROP}"
	)
}
if [[ ${outer_mode} == "full" || ${outer_mode} == "xvfb-probe" || ${outer_mode} == "namespace-guard-probe" || ${outer_mode} == "dbus-probe" || ${outer_mode} == "install-guard-probe" ]]; then
	append_xvfb_tool_mounts
fi
bwrap_command+=(
	--setenv PATH "${TRUSTED_PATH}"
	--setenv HOME "${INNER_WORK_ROOT}/home"
	--setenv TMPDIR /tmp
	--setenv LC_ALL C
	--setenv SOC_RUN_GUI_LIVE_TESTS 1
	"${probe_env[@]}"
	--chdir "${INNER_REPO}"
	--
	/usr/bin/bash
	"${INNER_REPO}/scripts/run-isolated-soc-gui.sh"
	--inner-supervisor
	"${outer_mode}"
	"${completion_nonce}"
	"${inner_timeout}"
	"${inner_cleanup_grace}"
)

unset DISPLAY WAYLAND_DISPLAY DBUS_SESSION_BUS_ADDRESS XAUTHORITY
outer_python_command=(
	"${ENV_BIN}" -i PATH="${TRUSTED_PATH}" LC_ALL=C PYTHONDONTWRITEBYTECODE=1
	"${PYTHON_BIN}" -I -S - "${outer_timeout}" "${CLEANUP_RESERVE_SECONDS}" "${completion_nonce}" "${outer_mode}" --
	"${supervisor_path}" --
	"${bwrap_command[@]}"
)
run_outer_python() {
	if [[ ${outer_mode} == "install-guard-probe" ]]; then
		"${PYTHON_BIN}" -I -S "${supervisor_path}" \
			--run-install-guard-outer --timeout "${outer_timeout}" \
			--term-grace "${SESSION_KILL_AFTER_SECONDS}" -- "${outer_python_command[@]}"
	else
		"${outer_python_command[@]}"
	fi
}
if [[ ${outer_mode} == "install-guard-probe" ]]; then
	set +e
fi
run_outer_python <<'PY'
import os
import select
import signal
import subprocess
import sys
import time

EXIT_INTERNAL = 125
MAX_COMPLETION = 256
MAX_STDERR = 8192
DIAGNOSTIC_HEADER = b"run-isolated-soc-gui: isolated stderr (bounded):\n"
TRUNCATED_MARKER = b"\n[truncated]\n"
POLL = 0.02
EARLY_INSTALL_FAILURES = {
    b"INSTALL_GUARD_FAIL ARG",
    b"INSTALL_GUARD_FAIL OPTIN",
    b"INSTALL_GUARD_FAIL ENV",
    b"INSTALL_GUARD_FAIL SUPERVISOR",
    b"INSTALL_GUARD_FAIL NAMESPACE",
}

if len(sys.argv) < 9 or sys.argv[4] not in {"full", "probe", "xvfb-probe", "namespace-guard-probe", "dbus-probe", "install-guard-probe"} or sys.argv[5] != "--" or sys.argv[7] != "--":
    raise SystemExit(2)
try:
    timeout = float(sys.argv[1])
    grace = float(sys.argv[2])
except ValueError:
    raise SystemExit(2) from None
nonce = sys.argv[3]
outer_mode = sys.argv[4]
helper_path = sys.argv[6]
command = sys.argv[8:]
if timeout <= 0 or grace <= 0 or len(nonce) != 64 or any(c not in "0123456789abcdef" for c in nonce) or not command:
    raise SystemExit(2)

def identity(pid):
    try:
        with open(f"/proc/{pid}/stat", "rb", buffering=0) as handle:
            fields = handle.read(65536).rsplit(b") ", 1)[1].split()
        return fields[0], int(fields[19])
    except (FileNotFoundError, ProcessLookupError, PermissionError, IndexError, ValueError, OSError):
        return None

def signal_bound(pid, start, pidfd, signum):
    current = identity(pid)
    if current is None or current[0] == b"Z" or current[1] != start:
        return
    signal.pidfd_send_signal(pidfd, signum, None, 0)

def terminate_helper(helper):
    info = identity(helper.pid)
    pidfd = None
    try:
        if info is not None and info[0] != b"Z":
            try:
                pidfd = os.pidfd_open(helper.pid, 0)
                signal_bound(helper.pid, info[1], pidfd, signal.SIGTERM)
            except OSError:
                helper.terminate()
        else:
            helper.terminate()
        deadline = time.monotonic() + 0.25
        while helper.poll() is None and time.monotonic() < deadline:
            time.sleep(POLL)
        if helper.poll() is None:
            if pidfd is not None:
                signal_bound(helper.pid, info[1], pidfd, signal.SIGKILL)
            else:
                helper.kill()
            deadline = time.monotonic() + 0.5
            while helper.poll() is None and time.monotonic() < deadline:
                time.sleep(POLL)
        if helper.poll() is None:
            helper.kill()
        helper.wait(timeout=1.0)
    except BaseException:
        try:
            helper.kill()
            helper.wait(timeout=1.0)
        except BaseException:
            pass
    finally:
        if pidfd is not None:
            os.close(pidfd)
        for stream in (helper.stdin, helper.stdout, helper.stderr):
            if stream is not None:
                stream.close()

def sanitized_stderr(truncated):
    helper = None
    try:
        payload_limit = MAX_STDERR - len(DIAGNOSTIC_HEADER)
        if truncated:
            payload_limit -= len(TRUNCATED_MARKER)
        helper = subprocess.Popen(
            [sys.executable, helper_path, "--sanitize-stdin", "--max-output-bytes", str(payload_limit)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        output, _ = helper.communicate(bytes(stderr_bytes), timeout=1.0)
        return output if helper.returncode == 0 else b""
    except subprocess.TimeoutExpired:
        if helper is not None:
            terminate_helper(helper)
        return b""

    except BaseException:
        if helper is not None:
            terminate_helper(helper)
        return b""

def install_failure(data):
    result_lines = (
        b"OK\n", b"ROOT_FAIL ROOT_META\n", b"ROOT_FAIL ROOT_EUID\n",
        b"ROOT_FAIL ROOT_UID_MAP\n", b"ROOT_FAIL ROOT_FDINFO\n",
        b"ROOT_FAIL ROOT_MOUNT\n", b"CHAIN_FAIL OWNER\n", b"CHAIN_FAIL MODE\n",
        b"CHAIN_FAIL META\n", b"CHAIN_FAIL FD\n", b"CHAIN_FAIL MISSING\n",
        b"INSTALL_GUARD_FAIL CORE_FD\n", b"INSTALL_GUARD_FAIL CORE_FRAME\n",
        b"INSTALL_GUARD_FAIL CORE_STATUS\n", b"INSTALL_GUARD_FAIL CORE_STATUS_OK_NONZERO\n",
        b"INSTALL_GUARD_FAIL CORE_STATUS_FAIL_ZERO\n", b"INSTALL_GUARD_FAIL CORE_STATUS_ROOT_EXIT\n",
        b"INSTALL_GUARD_FAIL CORE_STATUS_CHAIN_EXIT\n", b"INSTALL_GUARD_FAIL CORE_STATUS_MULTIPLE\n",
        b"INSTALL_GUARD_FAIL CORE_PARSER_EXIT\n", b"INSTALL_GUARD_FAIL CORE_PARSER_EMPTY\n",
        b"INSTALL_GUARD_FAIL CORE_PARSER_MULTIPLE\n", b"INSTALL_GUARD_FAIL CORE_PARSER_CONTROL\n",
        b"INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN\n",
        b"INSTALL_GUARD_FAIL CORE_RESULT_MISSING\n", b"INSTALL_GUARD_FAIL CORE_RESULT_EMPTY\n",
        b"INSTALL_GUARD_FAIL CORE_RESULT_META\n", b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n",
        b"INSTALL_GUARD_FAIL CORE_RESULT_CODE\n",
        b"INSTALL_GUARD_FAIL CORE_RESULT_INIT\n", b"INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FD\n",
        b"INSTALL_GUARD_FAIL CORE_RESULT_STATUS_SPAWN\n", b"INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FRAME\n",
        b"INSTALL_GUARD_FAIL CORE_RESULT_WRITE\n",
    )
    if data not in result_lines:
        return b"INSTALL_GUARD_FAIL CORE_FRAME\n"
    if data == b"OK\n":
        return b"INSTALL_GUARD_FAIL CORE_STATUS\n"
    return data

read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
process = None
pidfd = None
stderr_bytes = bytearray()
stderr_truncated = False

def drain_stderr():
    global stderr_truncated
    if process is None or process.stderr is None:
        return
    while True:
        try:
            chunk = os.read(process.stderr.fileno(), 4096)
        except BlockingIOError:
            return
        except OSError:
            return
        if not chunk:
            return
        room = MAX_STDERR - len(stderr_bytes)
        if room > 0:
            stderr_bytes.extend(chunk[:room])
        if len(chunk) > room:
            stderr_truncated = True

def bounded_wait(deadline):
    while process is not None and process.poll() is None and time.monotonic() < deadline:
        drain_stderr()
        time.sleep(POLL)
    drain_stderr()
    return process is not None and process.poll() is not None

def read_runner_input(deadline):
    descriptor = sys.stdin.buffer.fileno()
    os.set_blocking(descriptor, False)
    payload = bytearray()
    while time.monotonic() < deadline:
        readable, _, _ = select.select([descriptor], [], [], min(POLL, deadline - time.monotonic()))
        if not readable:
            continue
        chunk = os.read(descriptor, 4096)
        if not chunk:
            return bytes(payload)
        payload.extend(chunk)
        if len(payload) > RUNNER_INPUT_BYTES:
            raise RuntimeError("runner input limit")
    raise TimeoutError

try:
    payload = read_runner_input(time.monotonic() + timeout)
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=write_fd,
        stderr=subprocess.PIPE,
        close_fds=True,
        restore_signals=True,
    )
    os.close(write_fd)
    write_fd = -1
    initial = identity(process.pid)
    if initial is None or initial[0] == b"Z":
        raise RuntimeError("bwrap identity unavailable")
    start = initial[1]
    pidfd = os.pidfd_open(process.pid, 0)
    rebound = identity(process.pid)
    if rebound is None or rebound[0] == b"Z" or rebound[1] != start:
        raise RuntimeError("bwrap identity changed")
    os.set_blocking(process.stderr.fileno(), False)
    process.stdin.write(payload)
    process.stdin.close()
    if not bounded_wait(time.monotonic() + timeout):
        signal_bound(process.pid, start, pidfd, signal.SIGTERM)
        if not bounded_wait(time.monotonic() + grace):
            signal_bound(process.pid, start, pidfd, signal.SIGKILL)
            if not bounded_wait(time.monotonic() + grace):
                raise RuntimeError("bwrap reap deadline exceeded")
        raise TimeoutError
    status = process.returncode
    os.set_blocking(read_fd, False)
    frame = bytearray()
    frame_deadline = time.monotonic() + grace
    while time.monotonic() < frame_deadline:
        readable, _, _ = select.select([read_fd], [], [], min(POLL, frame_deadline - time.monotonic()))
        if not readable:
            continue
        chunk = os.read(read_fd, MAX_COMPLETION + 1)
        if not chunk:
            break
        frame.extend(chunk)
        if len(frame) > MAX_COMPLETION:
            raise RuntimeError("completion frame too large")
    expected = f"SOC_GUI_COMPLETE {nonce} {status}\n".encode("ascii")
    if status < 0 or bytes(frame) != expected:
        raise RuntimeError("completion frame mismatch")
    if status != 0:
        failure = install_failure(bytes(stderr_bytes)) if outer_mode == "install-guard-probe" else None
        if outer_mode == "install-guard-probe":
            if failure is None:
                failure = b"INSTALL_GUARD_FAIL OUTER_HELPER\n"
            sys.stderr.buffer.write(failure)
            raise SystemExit(status)
        if failure is not None:
            sys.stderr.buffer.write(failure)
            raise SystemExit(status)
        if stderr_bytes:
            sys.stderr.buffer.write(DIAGNOSTIC_HEADER + sanitized_stderr(stderr_truncated))
            if stderr_truncated:
                sys.stderr.buffer.write(TRUNCATED_MARKER)
        raise SystemExit(status)
except TimeoutError:
    if outer_mode == "install-guard-probe":
        sys.stderr.buffer.write(b"INSTALL_GUARD_FAIL SUPERVISOR\n")
    else:
        sys.stderr.write("run-isolated-soc-gui: isolated namespace deadline exceeded\n")
    raise SystemExit(124) from None
except BaseException as error:
    if process is not None and process.poll() is None and pidfd is not None:
        try:
            current = identity(process.pid)
            if current is not None:
                signal_bound(process.pid, current[1], pidfd, signal.SIGKILL)
            bounded_wait(time.monotonic() + grace)
        except BaseException:
            pass
    if type(error) is SystemExit:
        raise
    if outer_mode == "install-guard-probe":
        sys.stderr.buffer.write(b"INSTALL_GUARD_FAIL SUPERVISOR\n")
        raise SystemExit(EXIT_INTERNAL) from None
    sys.stderr.write("run-isolated-soc-gui: isolated namespace failed closed\n")
    if stderr_bytes:
        sys.stderr.buffer.write(DIAGNOSTIC_HEADER + sanitized_stderr(stderr_truncated))
        if stderr_truncated:
            sys.stderr.buffer.write(TRUNCATED_MARKER)
    raise SystemExit(EXIT_INTERNAL) from None
finally:
    if write_fd >= 0:
        try:
            os.close(write_fd)
        except OSError:
            pass
    if pidfd is not None:
        try:
            os.close(pidfd)
        except OSError:
            pass
    try:
        os.close(read_fd)
    except OSError:
        pass
    if process is not None and process.stderr is not None:
        process.stderr.close()
PY
outer_status=$?
if [[ ${outer_mode} == "install-guard-probe" ]]; then
	set -e
fi
exit "${outer_status}"
