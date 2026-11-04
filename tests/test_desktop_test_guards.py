from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DesktopTestGuardTests(unittest.TestCase):
    def _shell_function_slice(self, source: str, name: str) -> str:
        start = source.index(f"{name}() {{")
        next_function = re.search(
            r"\n[A-Za-z_][A-Za-z0-9_]*\(\) \{", source[start + 1 :]
        )
        end = start + 1 + next_function.start() if next_function else len(source)
        return source[start:end]

    def _namespace_guard(self) -> str:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        body = source.split("verify_private_namespace() {", 1)[1].split("\nPY", 1)[0]
        return body[body.index("import os") :]

    def _run_namespace_guard(
        self, complete: bool, *, nested: bool = False
    ) -> subprocess.CompletedProcess[str]:
        bwrap = "/usr/bin/bwrap"
        flags = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--ro-bind",
            "/usr",
            "/usr",
            "--symlink",
            "usr/bin",
            "/bin",
            "--symlink",
            "usr/sbin",
            "/sbin",
            "--symlink",
            "usr/lib",
            "/lib",
            "--symlink",
            "usr/lib64",
            "/lib64",
            "--ro-bind",
            "/etc",
            "/etc",
            "--dir",
            "/var",
            "--ro-bind",
            "/var/lib",
            "/var/lib",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/tmp/.X11-unix",
            "--tmpfs",
            "/run",
            "--dir",
            "/run/soc-work",
            "--dir",
            "/run/soc-auth",
            "--dir",
            "/run/soc-controller",
        ]
        if complete:
            flags.extend(
                [
                    "--dir",
                    "/run/soc-controller/host-tmp",
                    "--dir",
                    "/run/soc-controller/nested-tmp",
                    "--dir",
                    "/run/soc-controller/dbus",
                ]
            )
        guard = self._namespace_guard()
        if nested:
            guard = (
                "import os\n"
                "child = os.fork()\n"
                "if child == 0:\n"
                f"    os.execv('/usr/bin/python3', ['python3', '-I', '-S', '-c', {guard!r}])\n"
                "_, status = os.waitpid(child, 0)\n"
                "raise SystemExit(os.waitstatus_to_exitcode(status))\n"
            )
        flags.extend(
            [
                "--setenv",
                "PATH",
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "--setenv",
                "HOME",
                "/run/soc-work/home",
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--setenv",
                "LC_ALL",
                "C",
                "--chdir",
                "/",
                "--",
                "/usr/bin/python3",
                "-I",
                "-S",
                "-c",
                guard,
            ]
        )
        return subprocess.run(
            flags, capture_output=True, text=True, check=False, timeout=8
        )

    def test_real_outer_bwrap_namespace_guard_accepts_inner_nspid_one(self) -> None:
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("bubblewrap is unavailable")
        result = self._run_namespace_guard(True, nested=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_namespace_guard_rejects_incomplete_private_mounts_and_forged_views(
        self,
    ) -> None:
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("bubblewrap is unavailable")
        result = self._run_namespace_guard(False)
        self.assertNotEqual(result.returncode, 0)
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("os.getpid() <= 1", source)
        self.assertIn("current_pid = os.getpid()", source)
        self.assertIn("process_parent(current_pid)", source)
        self.assertIn("nspids == [1]", source)
        self.assertIn("nspids[-1] == 1", source)
        self.assertIn('mounts.get("/proc") != "proc"', source)
        self.assertIn('mounts.get("/run") != "tmpfs"', source)
        self.assertIn('mounts.get("/tmp") != "tmpfs"', source)
        for marker in (
            "PROC_FLAGS = os.O_RDONLY | os.O_NOFOLLOW",
            'line.rfind(b") ")',
            "parent <= 0",
            "current_pid in seen",
            "range(32)",
            "current_pid != 1",
            "namespace_stat.items()",
        ):
            self.assertIn(marker, source)

    def test_namespace_guard_reason_codes_are_fixed_and_non_dynamic(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        codes = (
            "NSG_PROC_STAT",
            "NSG_PROC_PARSE",
            "NSG_PROC_PARENT",
            "NSG_PROC_VISIBILITY",
            "NSG_NAMESPACE",
            "NSG_UID_MAP",
            "NSG_PID",
            "NSG_ANCESTOR",
            "NSG_ANCESTOR_LIMIT",
            "NSG_NSPID",
            "NSG_MOUNT",
            "NSG_DIR",
            "NSG_ENV",
            "NSG_HOST_X11",
        )
        for code in codes:
            self.assertIn(f'fail("{code}")', source)
        self.assertIn('print(f"NSG_FAIL {code}", file=sys.stderr)', source)
        self.assertNotIn('fail(f"', source)

    def test_bwrap_server_helpers_forward_target_in_same_argv(self) -> None:
        script = REPO_ROOT / "scripts" / "applet-crash-safety.sh"
        source = script.read_text(encoding="utf-8")
        dbus_start = source.index("run_dbus_server() {")
        dbus_end = source.index("\nrun_xephyr_server() {", dbus_start)
        xephyr_start = dbus_end + 1
        xephyr_end = source.index("\nrun_gdbus() {", xephyr_start)
        helpers = source[dbus_start:dbus_end] + "\n" + source[xephyr_start:xephyr_end]

        with tempfile.TemporaryDirectory(dir="/tmp") as temporary:
            root = Path(temporary)
            log = root / "argv.jsonl"
            fake_bwrap = root / "bwrap"
            fake_bwrap.write_text(
                "#!/usr/bin/python3\n"
                "import json, os, sys\n"
                "with open(os.environ['SOC_BWRAP_LOG'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n",
                encoding="utf-8",
            )
            fake_bwrap.chmod(0o700)
            probe = f"""
set -euo pipefail
bwrap_bin={str(fake_bwrap)!r}
controller_root=/controller
host_display=:70
host_xauthority=/host.Xauthority
nested_xauthority=/nested.Xauthority
TRUSTED_COMMAND_PATH=/usr/bin:/bin
export SOC_BWRAP_LOG={str(log)!r}
{helpers}
run_dbus_server /usr/bin/printf dbus-target
run_xephyr_server /usr/bin/printf xephyr-target
"""
            result = subprocess.run(
                ["bash", "-c", probe],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][-3:], ["--", "/usr/bin/printf", "dbus-target"])
            self.assertEqual(calls[1][-3:], ["--", "/usr/bin/printf", "xephyr-target"])

    def test_real_e2e_requires_explicit_desktop_opt_in(self) -> None:
        source = (REPO_ROOT / "scripts" / "real-e2e-acceptance.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("SOC_RUN_GUI_LIVE_TESTS:-0", source)
        self.assertIn("Refusing desktop acceptance test.", source)

    def test_crash_safety_requires_explicit_desktop_opt_in(self) -> None:
        script_path = REPO_ROOT / "scripts" / "applet-crash-safety.sh"
        source = script_path.read_text(encoding="utf-8")
        self.assertTrue(os.access(script_path, os.X_OK))
        self.assertIn("SOC_RUN_GUI_LIVE_TESTS:-0", source)
        self.assertIn("Refusing desktop crash-safety test.", source)

    def test_crash_safety_requires_explicit_nonzero_distinct_displays(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('host_display=":70"', source)
        self.assertIn('display_number="90"', source)
        self.assertNotIn('host_display="${APPLET_CRASH_SAFETY_HOST_DISPLAY:-}"', source)
        self.assertNotIn("APPLET_CRASH_SAFETY_HOST_DISPLAY:-${DISPLAY:-}", source)
        self.assertIn("^:[1-9][0-9]*$", source)
        self.assertIn(
            'if [[ ${namespace_guard_probe} != "1" && ${host_display} == "${nested_display}" ]]',
            source,
        )
        self.assertIn(
            "unset DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY XAUTHORITY", source
        )

    def test_crash_safety_binds_private_distinct_xauthority_files(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            'host_xauthority="/run/soc-auth/host.Xauthority"',
            'nested_xauthority="/run/soc-auth/nested.Xauthority"',
            "stat.S_ISREG(entry.st_mode)",
            "entry.st_uid != os.geteuid()",
            "entry.st_nlink != 1",
            "entry.st_mode & 0o077",
            "os.path.samestat(entries[0], entries[1])",
            "cookie = secrets.token_hex(16)",
            "input=payload",
            "pass_fds=(directory_fd,)",
            '--setenv XAUTHORITY "${host_xauthority}"',
            '-auth "${nested_xauthority}"',
            'export XAUTHORITY="${nested_xauthority}"',
        ):
            self.assertIn(marker, source)
        nested_start = source.index("run_nested() {")
        nested_end = source.index("\nrun_dbus_server() {", nested_start)
        nested_source = source[nested_start:nested_end]
        self.assertNotIn('--ro-bind "${host_xauthority}"', nested_source)
        self.assertEqual(nested_source.count('--ro-bind "${nested_xauthority}"'), 2)
        self.assertIn("--unshare-pid --unshare-net", nested_source)
        self.assertIn("os.getppid() != 1", nested_source)
        self.assertIn('f"/proc/1/root{controller}"', nested_source)
        self.assertIn(
            'probe.connect("\\0/tmp/.X11-unix/X" + host_display)', nested_source
        )

    def test_crash_safety_keeps_tmpdir_inside_private_test_root(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('test_root="/run/soc-work/session"', source)
        self.assertIn('tmp_dir="${test_root}/tmp"', source)
        self.assertIn("chmod 700 ", source)
        self.assertIn('export TMPDIR="${tmp_dir}"', source)
        self.assertIn('TMPDIR="${tmp_dir}"', source)

    def test_crash_safety_smoke_is_fixed_and_rejects_skips(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("APPLET_CRASH_SAFETY_SMOKE_MODE:-none", source)
        self.assertIn("must be none or clipboard-popup", source)
        allowed_tests = (
            "test_cinnamon_applet_pastes_clipboard_text_into_focused_xed_file",
            "test_cinnamon_applet_menu_click_path_pastes_into_previously_focused_xed_file",
            "test_cinnamon_applet_opens_and_cancels_non_text_clipboard_overwrite_popup",
        )
        for name in allowed_tests:
            self.assertEqual(source.count(name), 1)
        self.assertIn("if result.skipped:", source)
        self.assertIn("raise SystemExit(1)", source)
        self.assertNotIn("SMOKE_COMMAND", source)
        self.assertNotIn('bash -c "${smoke_mode}"', source)

    def test_crash_safety_runs_smoke_before_at_least_one_reload(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("[[ ! ${cycles} =~ ^[1-9][0-9]*$", source)
        self.assertLess(
            source.index("run_isolated_smoke &"),
            source.index('for cycle in $(seq 1 "${cycles}")'),
        )

    def test_crash_safety_uses_read_only_private_bus_and_rejects_forged_host_env(
        self,
    ) -> None:
        script = REPO_ROOT / "scripts" / "applet-crash-safety.sh"
        source = script.read_text(encoding="utf-8")
        for marker in (
            "/usr/bin/dbus-daemon --session --nofork --nopidfile --nosyslog",
            'dbus_socket="${dbus_dir}/session.sock"',
            'dbus_dir="${controller_root}/dbus"',
            "expected_socket_identity",
            'os.listdir(f"/proc/{daemon_pid}/fd")',
            'with open("/proc/net/unix"',
            "columns[7] == bound_path",
            "run_nested bus",
            '--ro-bind "${controller_root}/dbus" /run/soc-bus',
            'export DBUS_SESSION_BUS_ADDRESS="${dbus_address}"',
        ):
            self.assertIn(marker, source)
        self.assertIn(
            "unset DBUS_SESSION_BUS_ADDRESS DISPLAY WAYLAND_DISPLAY XAUTHORITY", source
        )
        self.assertNotIn("dbus-run-session", source)
        self.assertNotIn("APPLET_CRASH_SAFETY_INSIDE", source)
        self.assertNotIn("APPLET_CRASH_SAFETY_INNER", source)
        self.assertNotIn("APPLET_CRASH_SAFETY_HANDSHAKE", source)

        env = {
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            "SOC_RUN_GUI_LIVE_TESTS": "1",
            "APPLET_CRASH_SAFETY_INNER": "1",
            "APPLET_CRASH_SAFETY_HANDSHAKE_FD": "9",
            "APPLET_CRASH_SAFETY_HANDSHAKE_NONCE": "a" * 64,
            "APPLET_CRASH_SAFETY_BWRAP": "/usr/bin/bwrap",
            "APPLET_CRASH_SAFETY_CONTROLLER_ROOT": "/run/soc-controller",
            "APPLET_CRASH_SAFETY_TEST_ROOT": "/run/soc-work/session",
            "APPLET_CRASH_SAFETY_HOST_DISPLAY": ":88",
            "APPLET_CRASH_SAFETY_DISPLAY": "99",
            "APPLET_CRASH_SAFETY_HOST_XAUTHORITY": "/tmp/forged-host.Xauthority",
            "APPLET_CRASH_SAFETY_NESTED_XAUTHORITY": "/tmp/forged-nested.Xauthority",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus",
        }
        result = subprocess.run(
            [str(script)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        self.assertEqual(result.returncode, 2)
        self.assertRegex(result.stderr, r"^NSG_FAIL NSG_[A-Z_]+\n$")

    def test_crash_safety_binds_xephyr_identity_socket_and_lock_across_probe(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            'xephyr_identity="$(read_process_identity "${xephyr_pid}")"',
            'owned_process_state "${xephyr_pid}" "${xephyr_start_time}"',
            'read_direct_child_identity "${xephyr_server_pid}" "${xephyr_pid}"',
            '${lock_pid} == "${xephyr_server_pid}',
            "socket_before=",
            "socket_after=",
            "lock_before=",
            "lock_after=",
            'xprop -display "${nested_display}" -root',
            '${socket_before} == "${socket_after}"',
            '${lock_before} == "${lock_after}"',
        ):
            self.assertIn(marker, source)

    def test_crash_safety_cleanup_is_pidfd_identity_bound_and_tracks_descendants(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            "os.pidfd_open(pid, 0)",
            "signal.pidfd_send_signal(pidfd, signum, None, 0)",
            "rebound = identity()",
            'terminate_owned_process "${dbus_server_pid}" "${dbus_server_start_time}"',
            'terminate_owned_process "${dbus_pid}" "${dbus_start_time}"',
            'terminate_owned_process "${smoke_pid}" "${smoke_start_time}"',
            'terminate_owned_process "${cinnamon_pid}" "${cinnamon_start_time}"',
            'terminate_owned_process "${xephyr_pid}" "${xephyr_start_time}"',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("terminate_owned_tree", source)
        self.assertNotIn('kill -TERM "${pid}"', source)
        self.assertNotIn('kill -KILL "${pid}"', source)

    def test_crash_safety_revalidates_xephyr_and_private_bus_around_consumers(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(source.count("verify_xephyr_binding"), 8)
        self.assertIn("capture_private_dbus_binding", source)
        self.assertIn(
            'read_single_direct_child_identity "${dbus_pid}" "${dbus_start_time}"',
            source,
        )
        self.assertIn("Xephyr identity changed before Cinnamon start", source)
        self.assertIn("Xephyr identity changed during Cinnamon start", source)
        self.assertIn("Xephyr identity changed before smoke start", source)
        self.assertIn("Xephyr identity changed during smoke", source)
        self.assertIn("Xephyr identity changed before reload cycle", source)

    def test_crash_safety_hides_controller_sources_from_every_untrusted_client(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            "run_nested setup env SPEED_OF_CINNAMON_TEST_HOME=1",
            "run_nested target dconf write",
            "run_nested target cinnamon --replace",
            "run_nested target env SOC_RUN_GUI_LIVE_TESTS=1",
            "--tmpfs /run",
            "--tmpfs /tmp",
            '--ro-bind "${controller_root}/nested-tmp/.X11-unix/X${display_number}"',
            '--ro-bind "${controller_root}/dbus" /run/soc-bus',
        ):
            self.assertIn(marker, source)
        self.assertNotIn("APPLET_CRASH_SAFETY_INSIDE", source)
        self.assertNotIn("dbus-run-session", source)

    def test_crash_safety_uses_only_ephemeral_outer_namespace_storage(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for marker in (
            'test_root="/run/soc-work/session"',
            'host_xauthority="/run/soc-auth/host.Xauthority"',
            'nested_xauthority="/run/soc-auth/nested.Xauthority"',
            'controller_root="/run/soc-controller"',
            'mounts.get("/proc") != "proc"',
            'mounts.get("/run") != "tmpfs"',
            'mounts.get("/tmp") != "tmpfs"',
            'line.startswith("NSpid:")',
            'probe.connect("\\0/tmp/.X11-unix/X0")',
            '"DBUS_SESSION_BUS_ADDRESS"',
            '"LD_PRELOAD"',
        ):
            self.assertIn(marker, source)
        for forbidden in (
            "safe_fs=",
            "cleanup_test_root",
            "mktemp",
            "APPLET_CRASH_SAFETY_TEST_ROOT:-",
            "APPLET_CRASH_SAFETY_HOST_XAUTHORITY:-",
            "APPLET_CRASH_SAFETY_NESTED_XAUTHORITY:-",
            "remove applet-crash-safety",
        ):
            self.assertNotIn(forbidden, source)

    def test_crash_safety_generates_nested_cookie_only_over_private_stdin(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("cookie = secrets.token_hex(16)", source)
        self.assertIn('payload = f"add {display} MIT-MAGIC-COOKIE-1 {cookie}', source)
        self.assertIn("input=payload", source)
        self.assertIn("stdout=subprocess.DEVNULL", source)
        self.assertIn("stderr=subprocess.DEVNULL", source)
        self.assertNotIn("--setenv SOC_XAUTH_COOKIE", source)

    def test_core_xauth_gate_accepts_only_namespace_root_or_overflow_under_ro_usr(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("or executable.st_uid not in (0, 65534)", source)
        self.assertIn("executable.st_mode & 0o022", source)
        self.assertIn('"ro" not in mount_options.get("/usr", ())', source)
        self.assertIn("printf 'SETUP_XAUTH_TRUST\\n'", source)
        self.assertLess(
            source.index('"ro" not in mount_options.get("/usr", ())'),
            source.index("os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW"),
        )
        self.assertNotIn("executable.st_uid != os.geteuid()", source)
        self.assertNotIn("executable.st_uid == 1000", source)

    def test_private_dbus_has_fixed_codes_trust_gate_and_probe_stop(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for code in (
            "PARENT",
            "DIR",
            "SOCKET_META",
            "SOCKET_NET",
            "FD",
            "PROC_NET_UNIX",
            "IDENTITY",
        ):
            self.assertIn(f'fail("{code}")', source)
        self.assertIn('checked_lstat(socket_path, "SOCKET_LSTAT")', source)
        for code in (
            "SOCKET_SERVER_EXIT",
            "SOCKET_SERVER_NO_BIND",
            "SOCKET_CORE_NO_VIEW",
        ):
            self.assertIn(f'dbus_socket_lstat_failure_code="{code}"', source)
            self.assertIn(
                code,
                source[
                    source.index("record_dbus_failure_code() {") : source.index(
                        "capture_private_dbus_binding()"
                    )
                ],
            )
        self.assertIn("classify_socket_lstat_failure()", source)
        self.assertIn("run_bounded_server_socket_test()", source)
        self.assertIn("reap_direct_probe_child_without_starttime()", source)
        self.assertNotIn('terminate_owned_process "${probe_pid}" "0"', source)
        self.assertNotIn(
            'terminate_owned_process "${probe_pid}" "${probe_start}" 1 || true\n  wait "${probe_pid}"',
            source,
        )
        self.assertIn('owned_process_state "${dbus_pid}" "${dbus_start_time}"', source)
        self.assertIn(
            "run_dbus_server /usr/bin/test -S /run/soc-bus/session.sock", source
        )
        self.assertIn(
            'terminate_direct_probe_child "${probe_pid}" "${probe_owner}" "${probe_start}"',
            source,
        )
        self.assertIn('wait "${probe_pid}"', source)

    def test_private_dbus_daemon_descendants_are_bounded_and_launcher_bound(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        verifier = self._shell_function_slice(source, "verify_private_dbus_binding")
        for marker in (
            'f"/proc/{target_pid}/task/{target_pid}/children"',
            "queue = [(pid, process_before[2], 0)]",
            "seen = {(pid, process_before[2])}",
            "chain_edges = []",
            "chain_edges.append((current_pid, current_start, child_pid, child_start))",
            "for _node in range(16)",
            "if depth >= 4:",
            'fail("DAEMON_NONE")',
            'fail("DAEMON_MULTIPLE")',
            'fail("DAEMON_IDENTITY")',
            'fail("DAEMON_LIMIT")',
            "child_before[1] != current_pid",
            "parent_after != parent_identity",
            "child_after != child_before",
            "os.path.samestat(child_entry, trusted_exe)",
            "read_uid(child_pid)",
            "os.pidfd_open(daemon_pid, 0)",
            'fail("DAEMON_IDENTITY")',
            "for edge_parent, edge_parent_start, edge_child, edge_child_start in chain_edges:",
            "poller = select.poll()",
            "poller.register(daemon_fd, select.POLLIN | select.POLLHUP | select.POLLERR | select.POLLNVAL)",
            "if poller.poll(0):",
            "except OSError:",
            "os.close(daemon_fd)",
            "daemon_after[2] != daemon_start",
            'os.listdir(f"/proc/{daemon_pid}/fd")',
            "def count_private_dbus_matches(lines, socket_inodes, bound_path):",
            "count += 1",
            "bound_matches = count_private_dbus_matches(handle, socket_inodes, bound_path)",
            "if bound_matches > 1:",
            'fail("SOCKET_MULTIPLE")',
        ):
            self.assertIn(marker, verifier)
        self.assertNotIn('os.listdir(f"/proc/{pid}/fd")', verifier)
        self.assertNotIn('os.listdir("/proc")', verifier)
        self.assertNotIn('glob("/proc', verifier)
        self.assertIn(
            'terminate_owned_process "${dbus_server_pid}" "${dbus_server_start_time}"',
            source,
        )

    def test_private_dbus_proc_view_model_invariants_resolve_and_reject_shapes(
        self,
    ) -> None:
        def resolve(
            view: dict[str, dict], *, max_depth: int = 4, max_nodes: int = 16
        ) -> str:
            queue = [("launcher", 1, 0)]
            seen = {("launcher", 1)}
            candidates = []
            visited = 0
            while queue:
                current, start, depth = queue.pop(0)
                visited += 1
                if visited > max_nodes:
                    return "DAEMON_LIMIT"
                node = view.get(current)
                if node is None or node["start"] != start:
                    return "DAEMON_IDENTITY"
                if depth >= max_depth and node["children"]:
                    return "DAEMON_LIMIT"
                for child in node["children"]:
                    child_node = view.get(child)
                    if child_node is None or child_node["parent"] != current:
                        return "DAEMON_IDENTITY"
                    if child_node["start"] != child_node["recheck_start"]:
                        return "DAEMON_IDENTITY"
                    if child_node["exe"] == "/usr/bin/dbus-daemon":
                        if child_node["uid"] != 0 or child_node["state"] == "Z":
                            return "DAEMON_IDENTITY"
                        candidates.append(child)
                    key = (child, child_node["start"])
                    if key not in seen:
                        seen.add(key)
                        queue.append((child, child_node["start"], depth + 1))
            if not candidates:
                return "DAEMON_NONE"
            return "DAEMON_MULTIPLE" if len(candidates) != 1 else "OK"

        def node(
            parent: str,
            *,
            exe: str = "bwrap",
            uid: int = 0,
            children: list[str] | None = None,
            start: int = 1,
            recheck_start: int | None = None,
            state: str = "S",
        ) -> dict:
            return {
                "parent": parent,
                "exe": exe,
                "uid": uid,
                "children": children or [],
                "start": start,
                "recheck_start": start if recheck_start is None else recheck_start,
                "state": state,
            }

        two_stage = {
            "launcher": node("outer", children=["daemon"], start=1),
            "daemon": node("launcher", exe="/usr/bin/dbus-daemon"),
        }
        three_stage = {
            "launcher": node("outer", children=["bridge"]),
            "bridge": node("launcher", children=["daemon"]),
            "daemon": node("bridge", exe="/usr/bin/dbus-daemon"),
        }
        self.assertEqual(resolve(two_stage), "OK")
        self.assertEqual(resolve(three_stage), "OK")
        self.assertEqual(
            resolve(
                {
                    "launcher": node("outer", children=["wrong"]),
                    "wrong": node("launcher", exe="other"),
                }
            ),
            "DAEMON_NONE",
        )
        multiple = dict(three_stage)
        multiple["launcher"] = node("outer", children=["a", "b"])
        multiple["a"] = node("launcher", exe="/usr/bin/dbus-daemon")
        multiple["b"] = node("launcher", exe="/usr/bin/dbus-daemon")
        self.assertEqual(resolve(multiple), "DAEMON_MULTIPLE")
        self.assertEqual(
            resolve(
                {
                    "launcher": node("outer", children=["bad"]),
                    "bad": node("other", exe="/usr/bin/dbus-daemon"),
                }
            ),
            "DAEMON_IDENTITY",
        )
        self.assertEqual(
            resolve(
                {
                    "launcher": node("outer", children=["changed"]),
                    "changed": node(
                        "launcher", exe="/usr/bin/dbus-daemon", start=2, recheck_start=3
                    ),
                }
            ),
            "DAEMON_IDENTITY",
        )
        limited = {"launcher": node("outer", children=["n1"])}
        for index in range(1, 6):
            name = f"n{index}"
            limited[name] = node(
                "launcher" if index == 1 else f"n{index - 1}",
                children=[f"n{index + 1}"] if index < 5 else [],
            )
        self.assertEqual(resolve(limited, max_depth=3), "DAEMON_LIMIT")

    def test_private_dbus_socket_filter_executes_exact_production_function(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        function_start = source.index("verify_private_dbus_binding() {")
        heredoc_start = source.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        heredoc_end = source.index("\nPY\n}", heredoc_start)
        embedded = source[heredoc_start:heredoc_end]
        tree = ast.parse(
            embedded, filename="applet-crash-safety:verify_private_dbus_binding"
        )
        function = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "count_private_dbus_matches"
        )
        namespace: dict[str, object] = {}
        exec(
            compile(
                ast.Module(body=[function], type_ignores=[]),
                "production-filter",
                "exec",
            ),
            namespace,
        )
        matching = namespace["count_private_dbus_matches"]
        valid = "000: 000: 000: 00010000 0001 01 700 /run/soc-bus/session.sock\n"
        self.assertEqual(matching([valid], {"700"}, "/run/soc-bus/session.sock"), 1)
        self.assertEqual(matching([], {"700"}, "/run/soc-bus/session.sock"), 0)
        self.assertEqual(
            matching([valid, valid], {"700"}, "/run/soc-bus/session.sock"), 2
        )
        for index, replacement in (
            (3, "bad"),
            (4, "0002"),
            (5, "02"),
            (6, "701"),
            (7, "/wrong"),
        ):
            columns = valid.rstrip("\n").split()
            columns[index] = replacement
            self.assertEqual(
                matching([" ".join(columns)], {"700"}, "/run/soc-bus/session.sock"), 0
            )
        self.assertIn(
            "bound_matches = count_private_dbus_matches(handle, socket_inodes, bound_path)",
            embedded,
        )

    def test_private_dbus_embedded_verifier_heredoc_compiles_exactly(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        function_start = source.index("verify_private_dbus_binding() {")
        heredoc_start = source.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        heredoc_end = source.index("\nPY\n}", heredoc_start)
        embedded = source[heredoc_start:heredoc_end]
        compile(embedded, "applet-crash-safety:verify_private_dbus_binding", "exec")

    def test_private_dbus_embedded_heredoc_imports_all_used_modules(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        function_start = source.index("verify_private_dbus_binding() {")
        heredoc_start = source.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        heredoc_end = source.index("\nPY\n}", heredoc_start)
        embedded = source[heredoc_start:heredoc_end]
        tree = ast.parse(
            embedded, filename="applet-crash-safety:verify_private_dbus_binding"
        )
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        )
        self.assertTrue({"os", "stat", "sys", "select"}.issubset(imported))
        self.assertIn("import select", embedded)

    def test_private_dbus_embedded_verifier_executes_imports_and_initialization(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        function_start = source.index("verify_private_dbus_binding() {")
        heredoc_start = source.index("<<'PY'\n", function_start) + len("<<'PY'\n")
        heredoc_end = source.index("\nPY\n}", heredoc_start)
        embedded = source[heredoc_start:heredoc_end]
        result = subprocess.run(
            [
                "python",
                "-I",
                "-S",
                "-c",
                embedded,
                "/run/soc-controller",
                "/run/soc-controller/dbus",
                "/run/soc-controller/dbus/session.sock",
                "/run/soc-bus/session.sock",
                "1",
                "0",
                "1",
                "0",
                "0",
                "",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        self.assertEqual(result.returncode, 19)
        self.assertNotIn("ModuleNotFoundError", result.stderr)
        self.assertNotIn("SyntaxError", result.stderr)

    def test_unknown_probe_starttime_sends_no_signal_or_wait_and_returns_bounded(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        function = self._shell_function_slice(
            source, "reap_direct_probe_child_without_starttime"
        )
        command = (
            """
read_process_identity() {
  printf 'S 1\\n'
}
kill() { printf 'SIGNAL\\n'; return 1; }
wait() { printf 'WAIT\\n'; return 1; }
"""
            + function
            + """
(sleep 30) & child=$!
started=$SECONDS
reap_direct_probe_child_without_starttime "$child"
status=$?
elapsed=$((SECONDS - started))
unset -f kill wait
/bin/kill -KILL "$child" 2>/dev/null || true
wait "$child" 2>/dev/null || true
printf 'status=%s elapsed=%s\\n' "$status" "$elapsed"
exit "$([[ $status -eq 1 && $elapsed -le 3 ]] && echo 0 || echo 1)"
"""
        )
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("status=1", result.stdout)
        self.assertNotIn("SIGNAL", result.stdout)
        self.assertNotIn("WAIT", result.stdout)

    def test_probe_state_read_error_waits_only_after_proc_disappears(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        function = self._shell_function_slice(source, "run_bounded_server_socket_test")
        state_read = function.index("if ! probe_identity=")
        failure_branch = function[
            state_read : function.index(
                '\t\tif [[ ${probe_state} == "Z" ]]', state_read
            )
        ]
        self.assertIn('if [[ ! -e "/proc/${probe_pid}" ]]; then', failure_branch)
        self.assertIn('wait "${probe_pid}"', failure_branch)
        self.assertTrue(
            failure_branch.index('if [[ ! -e "/proc/${probe_pid}" ]]')
            < failure_branch.index('wait "${probe_pid}"')
        )
        self.assertIn("return 1", failure_branch[failure_branch.index("fi") + 2 :])

    def test_direct_probe_identity_binds_and_revalidates_pid_parent_starttime(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        identity = self._shell_function_slice(source, "read_direct_probe_identity")
        wrapper = self._shell_function_slice(source, "terminate_direct_probe_child")
        self.assertIn('rest="${stat_line##*) }"', identity)
        self.assertIn('${fields[1]} == "${expected_parent}"', identity)
        self.assertIn("${fields[19]} =~ ^[1-9][0-9]*$", identity)
        self.assertGreaterEqual(
            wrapper.count('read_direct_probe_identity "${child_pid}" "${owner_pid}"'), 4
        )
        self.assertIn(
            'signal_owned_process "${child_pid}" "${expected_start}" SIGTERM', wrapper
        )
        self.assertIn(
            'signal_owned_process "${child_pid}" "${expected_start}" SIGKILL', wrapper
        )
        self.assertIn('[[ ${child_parent} == "${owner_pid}"', wrapper)
        self.assertIn(
            "--die-with-parent",
            source[
                source.index("run_dbus_server() {") : source.index(
                    "\nrun_xephyr_server() {"
                )
            ],
        )
        self.assertIn("printf 'DBUS_FAIL %s\\n'", source)
        self.assertIn("record_dbus_failure_code CLIENT_TIMEOUT", source)
        self.assertIn('dbus_last_failure_code="SOCKET_LSTAT"', source)
        self.assertNotIn("str(socket_entry.st_ino) not in socket_inodes", source)
        self.assertIn("columns[7] == bound_path", source)
        self.assertIn("read_uid(child_pid)", source)
        exit_codes_start = source.index("FAILURE_EXIT_CODES = {")
        exit_codes = source[
            exit_codes_start : source.index("def fail(code):", exit_codes_start)
        ]
        for code in (
            "DAEMON_NONE",
            "DAEMON_MULTIPLE",
            "DAEMON_IDENTITY",
            "DAEMON_LIMIT",
        ):
            self.assertIn(f'fail("{code}")', source)
            self.assertIn(code, exit_codes)
        self.assertIn('fail("SOCKET_MULTIPLE")', source)
        self.assertIn(
            "SOCKET_MULTIPLE",
            source[
                source.index("record_dbus_failure_code() {") : source.index(
                    "reap_direct_probe_child_without_starttime() {"
                )
            ],
        )
        self.assertIn('path = "/usr/bin/dbus-daemon"', source)
        self.assertIn("entry.st_uid not in (0, 65534)", source)
        self.assertIn("entry.st_nlink != 1", source)
        self.assertIn("entry.st_mode & 0o022", source)
        self.assertIn("run_dbus_server /usr/bin/dbus-daemon", source)
        self.assertIn('[[ ${dbus_probe} == "1" ]]', source)
        self.assertLess(
            source.index('[[ ${dbus_probe} == "1" ]]'),
            source.index("run_xephyr_server Xephyr"),
        )
        for marker in (
            'terminate_owned_process "${dbus_server_pid}"',
            'terminate_owned_process "${dbus_pid}"',
            "trap bootstrap_cleanup EXIT",
        ):
            self.assertIn(marker, source)

    def test_private_dbus_failure_code_sequences_are_last_causal_wins(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        start = source.index("record_dbus_failure_code() {")
        function = source[start : source.index("capture_private_dbus_binding()", start)]
        command = (
            function
            + "dbus_last_failure_code=SOCKET_NET; record_dbus_failure_code CLIENT_TIMEOUT; record_dbus_failure_code SOCKET_NET; printf '%s\\n' \"${dbus_last_failure_code}\"; dbus_last_failure_code=CLIENT_TIMEOUT; record_dbus_failure_code SOCKET_NET; record_dbus_failure_code CLIENT_TIMEOUT; printf '%s\\n' \"${dbus_last_failure_code}\""
        )
        result = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ["SOCKET_NET", "CLIENT_TIMEOUT"])

    def test_private_dbus_capture_is_direct_global_and_clears_stale_identity(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        capture = self._shell_function_slice(source, "capture_private_dbus_binding")
        self.assertIn('dbus_socket_identity=""', capture)
        self.assertIn('dbus_binding_failure_code=""', capture)
        self.assertIn("if verify_private_dbus_binding >/dev/null 2>&1; then", capture)
        self.assertNotIn('result="$(verify_private_dbus_binding', capture)
        self.assertNotIn("printf", capture)
        self.assertNotIn('capture_private_dbus_binding)"', source)
        self.assertIn("capture_private_dbus_binding; then", source)
        self.assertIn(
            "if ! capture_private_dbus_binding || [[ -z ${dbus_socket_identity} ]]; then",
            source,
        )
        for status, code in ((10, "PARENT"), (19, "IDENTITY")):
            self.assertIn(f"{status}) dbus_binding_failure_code={code}", capture)
        self.assertIn(
            '13)\n\t\tclassify_socket_lstat_failure\n\t\tdbus_binding_failure_code="${dbus_socket_lstat_failure_code}"',
            capture,
        )

    def test_private_dbus_success_clears_lstat_cache_before_next_failure(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        capture = self._shell_function_slice(source, "capture_private_dbus_binding")
        success_identity = capture.index('dbus_socket_identity="${socket_identity}"')
        clear_lstat = capture.index(
            'dbus_socket_lstat_failure_code=""', success_identity
        )
        self.assertLess(success_identity, clear_lstat)
        self.assertIn("classify_socket_lstat_failure", capture)

    def test_private_dbus_unexpected_verifier_exit_is_fixed_verify_code(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        capture = self._shell_function_slice(source, "capture_private_dbus_binding")
        self.assertIn("*) dbus_binding_failure_code=VERIFY ;;", capture)
        allowlist = source[
            source.index("record_dbus_failure_code() {") : source.index(
                "reap_direct_probe_child_without_starttime() {"
            )
        ]
        self.assertIn("| VERIFY)", allowlist)
        self.assertIn(
            'record_dbus_failure_code "${dbus_binding_failure_code}"', capture
        )

    def test_private_dbus_capture_maps_known_and_unknown_verifier_statuses(
        self,
    ) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        capture = self._shell_function_slice(source, "capture_private_dbus_binding")
        command = (
            """
dbus_socket_identity=stale
dbus_binding_failure_code=stale
dbus_expected_socket_identity=stale
dbus_socket_lstat_failure_code=stale
dbus_last_failure_code=stale
verify_private_dbus_binding() { return "${simulated_status}"; }
record_dbus_failure_code() { dbus_last_failure_code="$1"; }
stat() { printf '1:2:0:0:1\\n'; }
"""
            + capture
            + """
for status in 10 19 77; do
  dbus_socket_identity=stale
  simulated_status=$status
  capture_private_dbus_binding "$status" || true
  printf '%s:%s:%s\\n' "$status" "$dbus_binding_failure_code" "$dbus_socket_identity"
done
"""
        )
        result = subprocess.run(
            ["bash", "-c", command], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            result.stdout.splitlines(), ["10:PARENT:", "19:IDENTITY:", "77:VERIFY:"]
        )

    def test_namespace_guard_core_accepts_only_exact_probe_argument(self) -> None:
        source = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        parser = source.index('if [[ $# -eq 1 && $1 == "--namespace-guard-probe" ]]')
        self.assertEqual(source.count("namespace_guard_probe=0"), 1)
        self.assertEqual(source.count("namespace_guard_probe=1"), 1)
        self.assertIn('[[ $# -eq 1 && $1 == "--namespace-guard-probe" ]]', source)
        self.assertGreater(source.index("namespace_guard_probe=1", parser), parser)
        self.assertNotIn("namespace_guard_probe=0", source[parser:])
        self.assertIn("elif [[ $# -ne 0 ]]", source)
        verify_start = source.index("if ! verify_private_namespace; then")
        probe_end = source.index("if [[ ! -d", verify_start)
        probe_path = source[verify_start:probe_end]
        self.assertIn('if [[ ${namespace_guard_probe} == "1" ]]; then', probe_path)
        self.assertIn("exit 0", probe_path)
        self.assertLess(
            source.index("exit 0", verify_start),
            source.index("if [[ ! -d", verify_start),
        )
        for forbidden in (
            "xauth",
            "xvfb",
            "dbus",
            "xephyr",
            "cinnamon",
            "clipboard",
            "mkdir",
            "install",
        ):
            namespace_branch = probe_path.split(
                'if [[ ${namespace_guard_probe} == "1" ]]; then', 1
            )[1].split("\nfi", 1)[0]
            self.assertNotIn(forbidden, namespace_branch.lower())

    def test_install_guard_core_codes_and_early_safe_fs_probe_are_fixed(self) -> None:
        core = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        safe = (REPO_ROOT / "scripts" / "safe-local-fs.py").read_text(encoding="utf-8")
        for code in (
            "ROOT_META",
            "ROOT_EUID",
            "ROOT_UID_MAP",
            "ROOT_FDINFO",
            "ROOT_MOUNT",
        ):
            self.assertIn(code, safe)
        self.assertIn('print(f"ROOT_FAIL {exc.code}", file=sys.stderr)', safe)
        self.assertNotIn("print(exc", safe)
        self.assertIn("install_guard_probe=0", core)
        self.assertIn("install_guard_probe=1", core)
        self.assertIn(
            'safe-local-fs.py" assert-private-chain install "${session_home}" --allow-missing',
            core,
        )
        probe_start = core.rindex('if [[ ${install_guard_probe} == "1" ]]; then')
        setup_start = core.index('mkdir -p -- "${test_root}" "${session_home}"')
        self.assertLess(setup_start, probe_start)
        installer = 'bash "${repo_dir}/scripts/install-local.sh"'
        self.assertIn(installer, core)
        for action in (
            "run_dbus_server /usr/bin/dbus-daemon",
            installer,
            "run_xephyr_server Xephyr",
            "run_nested target cinnamon --replace",
        ):
            self.assertGreater(core.index(action), probe_start)
        self.assertLess(
            core.index("exit 0", probe_start),
            core.index("run_dbus_server /usr/bin/dbus-daemon"),
        )

    def test_install_guard_failure_classes_are_fixed_and_secret_free(self) -> None:
        adapter = (REPO_ROOT / "scripts" / "run-isolated-soc-gui.sh").read_text(
            encoding="utf-8"
        )
        core = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        for code in ("ARG", "OPTIN", "ENV"):
            self.assertIn(f"install_guard_fail {code}", adapter)
        self.assertIn("INSTALL_GUARD_FAIL NAMESPACE", core)
        self.assertNotIn("printf 'INSTALL_GUARD_FAIL CORE\\n'", core)
        self.assertIn("INSTALL_GUARD_FAIL SUPERVISOR", adapter)
        self.assertIn("EARLY_INSTALL_FAILURES", adapter)
        self.assertIn("install_failure(bytes(stderr_bytes))", adapter)

    def test_install_guard_maps_only_reserved_root_statuses_and_suppresses_child_stderr(
        self,
    ) -> None:
        core = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        probe = core[core.rindex('if [[ ${install_guard_probe} == "1" ]]; then') :]
        self.assertIn("probe_status=$?", probe)
        self.assertIn("2>/dev/null", probe)
        self.assertIn('exec {status_fd}<>"${status_path}"', probe)
        self.assertIn('status_fd_before="$(stat -Lc', probe)
        self.assertIn('--status-fd "${status_fd}"', probe)
        self.assertIn("parse-status-fd", probe)
        self.assertIn('--child-exit-status "${probe_status}"', probe)
        self.assertIn("INSTALL_GUARD_FAIL CORE_PARSER_EXIT", probe)
        self.assertIn("INSTALL_GUARD_FAIL CORE_PARSER_EMPTY", probe)
        self.assertIn("INSTALL_GUARD_FAIL CORE_PARSER_MULTIPLE", probe)
        self.assertIn("INSTALL_GUARD_FAIL CORE_PARSER_CONTROL", probe)
        self.assertIn("INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN", probe)
        self.assertIn("parser_exit=$?", probe)
        self.assertNotIn(
            'parse-status-fd \\\n    --status-fd "${status_fd}" --child-exit-status "${probe_status}" 2>/dev/null || true',
            probe,
        )
        self.assertNotIn('status_frame="$(head -c 129', probe)
        self.assertNotIn("status_frame", probe)
        self.assertIn("ROOT_FAIL_ROOT_META", probe)
        self.assertIn("CHAIN_FAIL_OWNER", probe)
        for code in (
            "CORE_FD",
            "CORE_RESULT_STATUS_FD",
            "CORE_RESULT_STATUS_SPAWN",
            "CORE_RESULT_STATUS_FRAME",
        ):
            self.assertIn(f"INSTALL_GUARD_FAIL {code}", probe)
        self.assertIn(
            "CORE_STATUS_OK_NONZERO | CORE_STATUS_FAIL_ZERO | CORE_STATUS_ROOT_EXIT | CORE_STATUS_CHAIN_EXIT | CORE_STATUS_MULTIPLE",
            probe,
        )
        self.assertIn("printf 'INSTALL_GUARD_FAIL %s\\n' \"${status_token}\"", probe)
        for code in (
            "ROOT_META",
            "ROOT_EUID",
            "ROOT_UID_MAP",
            "ROOT_FDINFO",
            "ROOT_MOUNT",
        ):
            self.assertIn(f"ROOT_FAIL_{code}", probe)
        self.assertNotIn("70) printf", probe)
        self.assertNotIn("71) printf", probe)
        self.assertNotIn("72) printf", probe)
        self.assertNotIn("73) printf", probe)
        self.assertNotIn("74) printf", probe)

    def test_parser_removes_only_final_newline_before_multiple_classification(
        self,
    ) -> None:
        core = (REPO_ROOT / "scripts" / "applet-crash-safety.sh").read_text(
            encoding="utf-8"
        )
        probe = core[core.rindex('if [[ ${install_guard_probe} == "1" ]]; then') :]
        self.assertIn("status_token=\"${parser_output%$'\\n'}\"", probe)
        self.assertIn("if [[ ${status_token} == *$'\\n'* ]]; then", probe)
        final_newline = probe.index("if [[ ${parser_output} != *$'\\n' ]]")
        remove_final = probe.index("status_token=\"${parser_output%$'\\n'}\"")
        remaining_newline = probe.index("if [[ ${status_token} == *$'\\n'* ]]")
        self.assertLess(final_newline, remove_final)
        self.assertLess(remove_final, remaining_newline)
        self.assertNotIn("parser_output}\" = $'\\n' ||", probe)


if __name__ == "__main__":
    unittest.main()
