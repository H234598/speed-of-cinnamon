#!/usr/bin/env bash
set -euo pipefail
umask 077
IFS=$'\n\t'

repo_dir="${APPLET_CRASH_SAFETY_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)}"
uuid="speed-of-cinnamon@H234598"
cycles="${APPLET_CRASH_SAFETY_CYCLES:-100}"
reload_mode="${APPLET_CRASH_SAFETY_RELOAD_MODE:-module}"
display_number="${APPLET_CRASH_SAFETY_DISPLAY:-99}"
heartbeat_limit_ms="${APPLET_CRASH_SAFETY_HEARTBEAT_MS:-250}"
fault_injection="${APPLET_CRASH_SAFETY_FAULT_INJECTION:-0}"
force_gc="${APPLET_CRASH_SAFETY_FORCE_GC:-1}"
host_display="${APPLET_CRASH_SAFETY_HOST_DISPLAY:-${DISPLAY:-}}"

for tool in Xephyr dbus-run-session dconf gdbus cinnamon mktemp ps sed awk date xprop rg python3; do
  if ! command -v -- "${tool}" >/dev/null 2>&1; then
    printf 'applet-crash-safety: required tool missing: %s\n' "${tool}" >&2
    exit 2
  fi
done

if [[ ! "${cycles}" =~ ^[1-9][0-9]*$ || "${cycles}" -gt 1000 ]]; then
  printf 'APPLET_CRASH_SAFETY_CYCLES must be an integer from 1 to 1000.\n' >&2
  exit 2
fi
if [[ "${reload_mode}" != "instance" && "${reload_mode}" != "module" ]]; then
  printf 'APPLET_CRASH_SAFETY_RELOAD_MODE must be instance or module.\n' >&2
  exit 2
fi
if [[ ! "${display_number}" =~ ^[1-9][0-9]*$ ]]; then
  printf 'APPLET_CRASH_SAFETY_DISPLAY must be a positive display number.\n' >&2
  exit 2
fi
if [[ ! "${heartbeat_limit_ms}" =~ ^[1-9][0-9]*$ ]]; then
  printf 'APPLET_CRASH_SAFETY_HEARTBEAT_MS must be a positive integer.\n' >&2
  exit 2
fi
if [[ -z "${host_display}" ]]; then
  printf 'APPLET_CRASH_SAFETY_HOST_DISPLAY or DISPLAY must identify the host X display.\n' >&2
  exit 2
fi
if [[ "${fault_injection}" != "0" && "${fault_injection}" != "1" ]]; then
  printf 'APPLET_CRASH_SAFETY_FAULT_INJECTION must be 0 or 1.\n' >&2
  exit 2
fi
if [[ "${force_gc}" != "0" && "${force_gc}" != "1" ]]; then
  printf 'APPLET_CRASH_SAFETY_FORCE_GC must be 0 or 1.\n' >&2
  exit 2
fi

if [[ ! -d "${repo_dir}/files/${uuid}" || -L "${repo_dir}/files/${uuid}" ]]; then
  printf 'applet-crash-safety: applet source directory is missing or unsafe.\n' >&2
  exit 2
fi

if [[ -n "${APPLET_CRASH_SAFETY_TEST_ROOT:-}" ]]; then
  test_root="${APPLET_CRASH_SAFETY_TEST_ROOT}"
  test_root_owned="0"
else
  test_root="$(mktemp -d "${TMPDIR:-/tmp}/speed-of-cinnamon-crash-safety.XXXXXX")"
  test_root_owned="1"
fi
session_home="${test_root}/home"
runtime_dir="${test_root}/runtime"
config_dir="${test_root}/config"
cache_dir="${test_root}/cache"
data_dir="${session_home}/.local/share"
state_dir="${test_root}/state"
display_log="${test_root}/xephyr.log"
cinnamon_log="${test_root}/cinnamon.log"

cleanup_test_root() {
  if [[ "${test_root_owned}" == "1" ]]; then
    python3 "${repo_dir}/scripts/safe-local-fs.py" remove applet-crash-safety "${test_root}" --kind dir >/dev/null 2>&1 || true
  fi
}

mkdir -p -- "${session_home}" "${runtime_dir}" "${config_dir}/autostart" "${cache_dir}" "${data_dir}" "${state_dir}"
chmod 700 "${session_home}" "${runtime_dir}" "${config_dir}" "${cache_dir}" "${data_dir}" "${state_dir}"

export HOME="${session_home}"
export XDG_CONFIG_HOME="${config_dir}"
export XDG_CACHE_HOME="${cache_dir}"
export XDG_DATA_HOME="${data_dir}"
export XDG_STATE_HOME="${state_dir}"
export XDG_RUNTIME_DIR="${runtime_dir}"
export XDG_CURRENT_DESKTOP="X-Cinnamon"
export XDG_SESSION_DESKTOP="cinnamon"
export XDG_SESSION_TYPE="x11"
export GSETTINGS_BACKEND="dconf"
export NO_AT_BRIDGE=1
export GTK_USE_PORTAL=0
export GIO_USE_VFS=local
nested_display=":${display_number}"
export DISPLAY="${nested_display}"

if [[ "${APPLET_CRASH_SAFETY_INSIDE:-0}" != "1" ]]; then
  if dbus-run-session -- env \
      APPLET_CRASH_SAFETY_INSIDE=1 \
      APPLET_CRASH_SAFETY_REPO="${repo_dir}" \
      APPLET_CRASH_SAFETY_TEST_ROOT="${test_root}" \
      APPLET_CRASH_SAFETY_HOST_DISPLAY="${host_display}" \
      bash "${BASH_SOURCE[0]}" "$@"; then
    nested_status=0
  else
    nested_status=$?
  fi
  cleanup_test_root
  exit "${nested_status}"
fi

cinnamon_pid=""
xephyr_pid=""

cleanup() {
  set +e
  if [[ -n "${cinnamon_pid}" ]]; then
    kill "${cinnamon_pid}" >/dev/null 2>&1 || true
    wait "${cinnamon_pid}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${xephyr_pid}" ]]; then
    kill "${xephyr_pid}" >/dev/null 2>&1 || true
    wait "${xephyr_pid}" >/dev/null 2>&1 || true
  fi
  cleanup_test_root
}
trap cleanup EXIT INT TERM

nested_display=":${display_number}"
export DISPLAY="${nested_display}"

SPEED_OF_CINNAMON_TEST_HOME=1 HOME="${session_home}" XDG_CONFIG_HOME="${config_dir}" XDG_DATA_HOME="${data_dir}" XDG_STATE_HOME="${state_dir}" \
  bash "${repo_dir}/scripts/install-local.sh" >/dev/null

DISPLAY="${host_display}" Xephyr "${nested_display}" -screen 1024x768x24 -nolisten tcp >"${display_log}" 2>&1 &
xephyr_pid=$!
for _ in $(seq 1 100); do
  if xprop -root >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
if ! xprop -root >/dev/null 2>&1; then
  printf 'Xephyr did not become ready.\n' >&2
  sed -n '1,120p' "${display_log}" >&2 || true
  exit 1
fi

dconf write /org/cinnamon/enabled-applets "[\"panel1:left:0:menu@cinnamon.org:0\",\"panel1:right:1:${uuid}:1\"]"
dconf write /org/cinnamon/panels-enabled "[\"1:0:bottom\"]"
cinnamon --replace >"${cinnamon_log}" 2>&1 &
cinnamon_pid=$!

for _ in $(seq 1 180); do
  if gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
    break
  fi
  if ! kill -0 "${cinnamon_pid}" >/dev/null 2>&1; then
    printf 'Cinnamon session exited before the D-Bus service became ready.\n' >&2
    sed -n '1,200p' "${cinnamon_log}" >&2 || true
    exit 1
  fi
  sleep 0.25
done
if ! gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
  printf 'Cinnamon D-Bus service did not become ready.\n' >&2
  sed -n '1,200p' "${cinnamon_log}" >&2 || true
  exit 1
fi

eval_cinnamon() {
  local script="$1"
  gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.Cinnamon.Eval "${script}"
}

force_cinnamon_gc() {
  eval_cinnamon 'imports.system.gc(); "ok"' >/dev/null
}

reload_applet_instance() {
  if [[ "${reload_mode}" == "module" ]]; then
    gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.Cinnamon.ReloadXlet "${uuid}" APPLET >/dev/null
    return
  fi

  eval_cinnamon 'global.settings.set_strv("enabled-applets", ["panel1:left:0:menu@cinnamon.org:0"]); "removed"' >/dev/null
  local removed_state="present"
  for _ in $(seq 1 80); do
    removed_state="$(eval_cinnamon "imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\").length===0?\"removed\":\"present\"" 2>/dev/null || true)"
    if [[ "${removed_state}" == *"removed"* ]]; then
      break
    fi
    sleep 0.125
  done
  [[ "${removed_state}" == *"removed"* ]] || { printf 'applet instance did not leave the panel.\n' >&2; exit 1; }
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

if [[ "${fault_injection}" == "1" ]]; then
  fault_result="$(eval_cinnamon "let xs=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\");let a=xs[0];for(let i=0;i<3;i++){a._runGuarded(\"fault-injection\",function(){throw new Error(\"explicit-test-fault\")},null)};a._disabledErrorGroups[\"fault-injection\"]===true?\"ok\":\"fail\"")"
  [[ "${fault_result}" == *'ok'* ]] || {
    printf 'fault-injection wrapper did not disable its group: %s\n' "${fault_result}" >&2
    exit 1
  }
fi

if [[ "${force_gc}" == "1" ]]; then
  force_cinnamon_gc
fi

initial_rss_kb="$(ps -o rss= -p "${cinnamon_pid}" | awk '{print $1}' | head -n1)"
initial_rss_kb="${initial_rss_kb:-0}"
max_rss_kb="${initial_rss_kb}"

for cycle in $(seq 1 "${cycles}"); do
  started_ms="$(date +%s%3N)"
  reload_applet_instance
  finished_ms="$(date +%s%3N)"
  elapsed_ms=$((finished_ms - started_ms))
  if (( elapsed_ms > heartbeat_limit_ms * 20 )); then
    printf 'Cinnamon reload stalled at cycle %s (%sms).\n' "${cycle}" "${elapsed_ms}" >&2
    exit 1
  fi
  gdbus call --session --dest org.Cinnamon --object-path /org/Cinnamon --method org.freedesktop.DBus.Peer.Ping >/dev/null
  state="missing"
  for _ in $(seq 1 80); do
    state="$(eval_cinnamon "let xs=imports.ui.appletManager.getRunningInstancesForUuid(\"${uuid}\");xs.length===1?String(xs[0].lifecycleState):\"missing\"" 2>/dev/null || true)"
    if [[ "${state}" == *"RUNNING"* ]]; then
      break
    fi
    sleep 0.125
  done
  [[ "${state}" == *"RUNNING"* ]] || { printf 'unexpected applet state after cycle %s: %s\n' "${cycle}" "${state}" >&2; exit 1; }
  if [[ "${force_gc}" == "1" ]]; then
    force_cinnamon_gc
  fi
  current_rss_kb="$(ps -o rss= -p "${cinnamon_pid}" | awk '{print $1}' | head -n1)"
  current_rss_kb="${current_rss_kb:-0}"
  if (( current_rss_kb > max_rss_kb )); then
    max_rss_kb="${current_rss_kb}"
  fi
done

crash_error_log="${test_root}/cinnamon-errors.log"
if rg -n -i 'segmentation fault|segfault|core dumped|out of memory|oom-kill|Cjs-CRITICAL|JS ERROR' "${cinnamon_log}" >"${crash_error_log}" 2>/dev/null; then
  printf 'Cinnamon logged a crash or GJS error during the isolated run.\n' >&2
  sed -n '1,120p' "${crash_error_log}" >&2 || true
  exit 1
fi
rm -f -- "${crash_error_log}"

final_rss_kb="$(ps -o rss= -p "${cinnamon_pid}" | awk '{print $1}' | head -n1)"
final_rss_kb="${final_rss_kb:-0}"
if (( final_rss_kb - initial_rss_kb > 51200 || max_rss_kb - initial_rss_kb > 51200 )); then
  printf 'Cinnamon RSS grew by more than 50 MiB (initial=%sKB final=%sKB max=%sKB).\n' "${initial_rss_kb}" "${final_rss_kb}" "${max_rss_kb}" >&2
  exit 1
fi

printf 'Applet crash-safety passed: %s isolated reload cycles, Cinnamon remained reachable, RSS delta <= 50 MiB.\n' "${cycles}"
