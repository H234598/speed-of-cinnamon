from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ADAPTER = REPO_ROOT / "scripts" / "run-isolated-soc-gui.sh"
CORE = REPO_ROOT / "scripts" / "applet-crash-safety.sh"
SUPERVISOR = REPO_ROOT / "scripts" / "gui-session-supervisor.py"
TRUSTED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
RELEVANT_PROCESSES = frozenset(("Xvfb", "Xephyr", "cinnamon", "dbus-daemon", "bwrap"))
REAL_PROBE_ENV_KEYS = frozenset(
    ("PATH", "HOME", "LC_ALL", "SOC_RUN_GUI_LIVE_TESTS", "SOC_RUN_REAL_DBUS_PROBE")
)
RUN_SNAPSHOT_LIMIT = 64


def fixed_harness_home() -> Path:
    source = ADAPTER.read_text(encoding="utf-8")
    matches = re.findall(r'^readonly EXPECTED_HOME="([^"\n]+)"$', source, re.MULTILINE)
    if len(matches) != 1:
        raise AssertionError("fixed harness home binding must be unique")
    home_text = matches[0]
    home = Path(home_text)
    if (
        not re.fullmatch(r"/[A-Za-z0-9._/-]+", home_text)
        or str(home) != home_text
        or ".." in home.parts
    ):
        raise AssertionError(
            "fixed harness home binding must be a canonical absolute path"
        )
    if source.count('readonly XVFB_BIN="${EXPECTED_HOME}/.local/bin/Xvfb"') != 1:
        raise AssertionError("fixed harness Xvfb binding must be unique")
    return home


def proc_identity(proc_entry: Path) -> tuple[int, str, int]:
    line = (proc_entry / "stat").read_bytes()
    marker = line.rfind(b") ")
    if marker <= 0:
        raise ValueError("malformed proc stat")
    pid_bytes, comm_bytes = line[:marker].split(b" (", 1)
    fields = line[marker + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("short proc stat")
    return (
        int(pid_bytes),
        comm_bytes.decode("utf-8", "surrogateescape"),
        int(fields[19]),
    )


def relevant_process_snapshot() -> frozenset[tuple[int, str, int]]:
    snapshot: set[tuple[int, str, int]] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            identity = proc_identity(entry)
        except (FileNotFoundError, PermissionError, UnicodeError, ValueError, OSError):
            continue
        if identity[1] in RELEVANT_PROCESSES:
            snapshot.add(identity)
    return frozenset(snapshot)


def run_metadata_snapshot(
    root: str | Path = "/run",
) -> dict[str, tuple[int, int, int, int, int, int, int, int]]:
    snapshot = {}
    soc_count = 0
    try:
        entries = os.scandir(root)
    except OSError:
        return snapshot
    with entries:
        for entry in entries:
            if not entry.name.startswith("soc-"):
                continue
            soc_count += 1
            if soc_count > RUN_SNAPSHOT_LIMIT:
                raise AssertionError("SOC_RUN_SNAPSHOT_LIMIT")
            try:
                metadata = entry.stat(follow_symlinks=False)
            except (FileNotFoundError, PermissionError, OSError):
                continue
            snapshot[entry.name] = (
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_uid,
                metadata.st_gid,
                metadata.st_nlink,
                metadata.st_size,
                metadata.st_mtime_ns,
            )
    return snapshot


def real_probe_environment() -> dict[str, str]:
    return {
        "PATH": TRUSTED_PATH,
        "HOME": str(fixed_harness_home()),
        "LC_ALL": "C",
        "SOC_RUN_GUI_LIVE_TESTS": "1",
        "SOC_RUN_REAL_DBUS_PROBE": "1",
    }


class IsolatedGuiAdapterTest(unittest.TestCase):
    def _production_install_failure(self):
        source = ADAPTER.read_text(encoding="utf-8")
        heredoc = source.rsplit("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        module = ast.parse(heredoc)
        selected = []
        names = {
            "ROOT_FAILURE_RE",
            "CHAIN_FAILURE_RE",
            "CORE_FAILURE_RE",
            "EARLY_INSTALL_FAILURES",
        }
        for node in module.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                selected.append(node)
            elif isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in names
                for target in node.targets
            ):
                selected.append(node)
            elif isinstance(node, ast.FunctionDef) and node.name == "install_failure":
                selected.append(node)
        selected_module = ast.Module(body=selected, type_ignores=[])
        ast.fix_missing_locations(selected_module)
        namespace = {"stderr_bytes": bytearray(), "result_bytes": b""}
        exec(compile(selected_module, str(ADAPTER), "exec"), namespace)
        namespace["helper_path"] = str(SUPERVISOR)

        class ResultSubprocess:
            DEVNULL = object()
            PIPE = object()

            @staticmethod
            def run(*args, **kwargs):
                return type(
                    "Result", (), {"returncode": 0, "stdout": namespace["result_bytes"]}
                )()

        namespace["subprocess"] = ResultSubprocess
        return namespace

    def test_install_parser_uses_production_path_for_all_fixed_frames(self):
        namespace = self._production_install_failure()
        cases = {
            **{
                f"ROOT_FAIL {code}\n": f"ROOT_FAIL {code}\n"
                for code in (
                    "ROOT_META",
                    "ROOT_EUID",
                    "ROOT_UID_MAP",
                    "ROOT_FDINFO",
                    "ROOT_MOUNT",
                )
            },
            **{
                f"CHAIN_FAIL {code}\n": f"CHAIN_FAIL {code}\n"
                for code in ("OWNER", "MODE", "META", "FD", "MISSING")
            },
            **{
                f"INSTALL_GUARD_FAIL {code}\n": f"INSTALL_GUARD_FAIL {code}\n"
                for code in (
                    "CORE_FD",
                    "CORE_FRAME",
                    "CORE_STATUS",
                    "CORE_STATUS_OK_NONZERO",
                    "CORE_STATUS_FAIL_ZERO",
                    "CORE_STATUS_ROOT_EXIT",
                    "CORE_STATUS_CHAIN_EXIT",
                    "CORE_STATUS_MULTIPLE",
                )
            },
            "OK\n": "INSTALL_GUARD_FAIL CORE_STATUS\n",
            "": "INSTALL_GUARD_FAIL CORE_FRAME\n",
            "garbage\n": "INSTALL_GUARD_FAIL CORE_FRAME\n",
            "OK\nSECOND\n": "INSTALL_GUARD_FAIL CORE_FRAME\n",
            "ROOT_FAIL ROOT_META\x01\n": "INSTALL_GUARD_FAIL CORE_FRAME\n",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                namespace["result_bytes"] = raw.encode()
                self.assertEqual(
                    namespace["install_failure"](namespace["result_bytes"]),
                    expected.encode(),
                )

    def test_install_parser_fixed_output_ignores_sanitizer_failure(self):
        namespace = self._production_install_failure()
        namespace["result_bytes"] = b"ROOT_FAIL ROOT_MOUNT\n"
        namespace["stderr_bytes"] = b"arbitrary controller stderr noise\n"
        namespace["sanitized_stderr"] = b""
        self.assertEqual(
            namespace["install_failure"](namespace["result_bytes"]),
            b"ROOT_FAIL ROOT_MOUNT\n",
        )

    def test_install_completion_timeout_and_exception_always_use_supervisor(self):
        source = ADAPTER.read_text(encoding="utf-8")
        heredoc = source.rsplit("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        module = ast.parse(heredoc)
        main_try = next(
            node
            for node in module.body
            if isinstance(node, ast.Try)
            and any(
                isinstance(handler.type, ast.Name) and handler.type.id == "TimeoutError"
                for handler in node.handlers
            )
        )
        timeout_handler = next(
            handler
            for handler in main_try.handlers
            if isinstance(handler.type, ast.Name) and handler.type.id == "TimeoutError"
        )
        exception_handler = next(
            handler
            for handler in main_try.handlers
            if isinstance(handler.type, ast.Name) and handler.type.id == "BaseException"
        )
        timeout_text = ast.get_source_segment(heredoc, timeout_handler) or ""
        exception_text = ast.get_source_segment(heredoc, exception_handler) or ""
        self.assertIn('b"INSTALL_GUARD_FAIL SUPERVISOR\\n"', timeout_text)
        self.assertIn('b"INSTALL_GUARD_FAIL SUPERVISOR\\n"', exception_text)
        self.assertNotIn("install_failure()", timeout_text)
        self.assertNotIn("install_failure()", exception_text)
        mismatch = next(
            node
            for node in ast.walk(main_try)
            if isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and isinstance(node.exc.func, ast.Name)
            and node.exc.func.id == "RuntimeError"
            and node.exc.args
            and isinstance(node.exc.args[0], ast.Constant)
            and node.exc.args[0].value == "completion frame mismatch"
        )
        self.assertEqual(mismatch.exc.args[0].value, "completion frame mismatch")

    def _harness(
        self, root: Path, behavior: str = "success", *, probe_timeout: int = 10
    ) -> dict[str, Path]:
        repo = root / "repo"
        scripts = repo / "scripts"
        scripts.mkdir(parents=True, mode=0o700)
        adapter = scripts / ADAPTER.name
        supervisor = scripts / SUPERVISOR.name
        shutil.copy2(ADAPTER, adapter)
        shutil.copy2(SUPERVISOR, supervisor)
        core = scripts / CORE.name
        shutil.copy2(CORE, core)
        real_supervisor = root / "real-gui-session-supervisor.py"
        shutil.copy2(SUPERVISOR, real_supervisor)
        log = root / "bwrap.json"
        pid_file = root / "bwrap.pid"
        helper_pid_file = root / "helper.pid"
        fake_bwrap = root / "bwrap"
        stderr_action = "pass"
        exit_action = "pass"
        completion_action = (
            "os.write(1, f'SOC_GUI_COMPLETE {nonce} 0\\n'.encode('ascii'))"
        )
        sleep_seconds = "0.15"
        if behavior == "no-frame":
            completion_action = "pass"
        elif behavior == "bad-frame":
            completion_action = "os.write(1, b'SOC_GUI_COMPLETE forged 0\\n')"
        elif behavior == "large-stderr":
            stderr_action = "os.write(2, b'x' * 100000)"
        elif behavior == "large-stderr-no-frame":
            stderr_action = "os.write(2, b'\\x1b[31m' + b'x' * 100000 + b'\\x00\\x01')"
            completion_action = "pass"
            exit_action = "sys.exit(1)"
            sleep_seconds = "0"
        elif behavior == "timeout":
            completion_action = "pass"
            sleep_seconds = "10"
        elif behavior == "helper-timeout":
            stderr_action = "os.write(2, b'x')"
            completion_action = "pass"
            exit_action = "sys.exit(1)"
            sleep_seconds = "0"
        elif behavior == "helper-failure":
            stderr_action = "pass"
            completion_action = "pass"
            exit_action = "pass"
            sleep_seconds = "0"
        else:
            self.assertEqual(behavior, "success")
        if behavior == "helper-failure":
            bwrap_action = (
                "os.write(1, f'SOC_GUI_COMPLETE {nonce} 1\\n'.encode('ascii'))\n"
                "command = sys.argv[sys.argv.index('--') + 1:]\n"
                f"command = [str({str(adapter)!r}) if value.endswith('/run-isolated-soc-gui.sh') else value for value in command]\n"
                "os.execv(command[0], command)\n"
            )
        else:
            bwrap_action = (
                f"{stderr_action}\n"
                f"{completion_action}\n"
                f"{exit_action}\n"
                f"time.sleep({sleep_seconds})\n"
            )
        fake_bwrap.write_text(
            "#!/usr/bin/python3\n"
            "import json, os, pathlib, sys, time\n"
            f"log = pathlib.Path({str(log)!r})\n"
            f"pid_file = pathlib.Path({str(pid_file)!r})\n"
            "args = sys.argv[1:]\n"
            "nonce = args[args.index('--inner-supervisor') + 2]\n"
            "log.write_text(json.dumps({'argv': args, 'env': {name: os.environ.get(name) for name in "
            "('DISPLAY','WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','XAUTHORITY','HOME','XDG_RUNTIME_DIR')}}), encoding='utf-8')\n"
            "pid_file.write_text(str(os.getpid()), encoding='ascii')\n" + bwrap_action,
            encoding="utf-8",
        )
        fake_bwrap.chmod(0o700)
        if behavior == "helper-timeout":
            supervisor.write_text(
                "#!/usr/bin/python3\n"
                "import os, pathlib, time\n"
                f"pathlib.Path({str(helper_pid_file)!r}).write_text(str(os.getpid()), encoding='ascii')\n"
                "time.sleep(10)\n",
                encoding="utf-8",
            )
            supervisor.chmod(0o700)
        elif behavior == "helper-failure":
            supervisor.write_text(
                "#!/usr/bin/python3\n"
                "import os, sys\n"
                "if '--print-install-guard-status' in sys.argv: os._exit(7)\n"
                "os._exit(1)\n",
                encoding="utf-8",
            )
            supervisor.chmod(0o700)
            core.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            core.chmod(0o700)
        text = adapter.read_text(encoding="utf-8")
        text = text.replace(
            'readonly BWRAP_BIN="/usr/bin/bwrap"', f'readonly BWRAP_BIN="{fake_bwrap}"'
        )
        text = text.replace(
            'readonly PYTHON_BIN="/usr/bin/python3.14"',
            f'readonly PYTHON_BIN="{Path(sys.executable).resolve()}"',
        )
        if probe_timeout != 10:
            text = text.replace(
                "readonly READY_TIMEOUT_SECONDS=10",
                f"readonly READY_TIMEOUT_SECONDS={probe_timeout}",
            )
            text = text.replace(
                "readonly SESSION_KILL_AFTER_SECONDS=2",
                "readonly SESSION_KILL_AFTER_SECONDS=1",
            )
            text = text.replace(
                "readonly DRAIN_MARGIN_SECONDS=5", "readonly DRAIN_MARGIN_SECONDS=1"
            )
            text = text.replace(
                "readonly PROBE_OUTER_TIMEOUT_SECONDS=30",
                "readonly PROBE_OUTER_TIMEOUT_SECONDS=5",
            )
        adapter.write_text(text, encoding="utf-8")
        adapter.chmod(0o700)
        home = root / "home"
        runtime = root / "runtime"
        home.mkdir(mode=0o700)
        runtime.mkdir(mode=0o700)
        return {
            "adapter": adapter,
            "home": home,
            "runtime": runtime,
            "log": log,
            "pid": pid_file,
            "helper_pid": helper_pid_file,
        }

    def _run(
        self, harness: dict[str, Path], *args: str, timeout: float = 8.0
    ) -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": TRUSTED_PATH,
            "HOME": str(harness["home"]),
            "XDG_RUNTIME_DIR": str(harness["runtime"]),
            "SOC_RUN_GUI_LIVE_TESTS": "1",
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus",
            "XAUTHORITY": "/tmp/host-secret.Xauthority",
        }
        if "--dbus-probe" in args:
            env["HOME"] = str(fixed_harness_home())
        if "--install-guard-probe" in args:
            env["HOME"] = str(fixed_harness_home())
            env["SOC_RUN_INSTALL_GUARD_PROBE"] = "1"
        return subprocess.run(
            [str(harness["adapter"]), *args],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    def test_static_outer_bwrap_contract_is_fixed_and_ephemeral(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        outer = source.split("bwrap_command=(", 1)[1].split("probe_env=()", 1)[0]
        ordered = (
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
            "--ro-bind /usr /usr",
            "--proc /proc",
            "--dev /dev",
            "--tmpfs /tmp",
            "--tmpfs /run",
        )
        positions = [outer.index(marker) for marker in ordered]
        self.assertEqual(positions, sorted(positions))
        for marker in (
            "--dir /run/soc-work",
            "--dir /run/soc-auth",
            "--dir /run/soc-controller",
            '--ro-bind "${repo_dir}" "${INNER_REPO}"',
            '--ro-bind "${XVFB_BIN}" "${INNER_XVFB}"',
            '--ro-bind "${XPROP_BIN}" "${INNER_XPROP}"',
            "exec 3>&1",
        ):
            self.assertIn(marker, source)
        self.assertNotIn("--as-pid-1", source)
        self.assertNotIn("--ro-bind / /", outer)
        self.assertNotRegex(outer, r"(?m)^\s*--bind\s")
        self.assertIn("--sanitize-stdin", source)
        self.assertNotIn("def sanitized_tail", source)

    def test_outer_invokes_sanitizer_helper_with_explicit_budget(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn(
            '[sys.executable, helper_path, "--sanitize-stdin", "--max-output-bytes", str(payload_limit)]',
            source,
        )
        self.assertIn("DIAGNOSTIC_HEADER + sanitized_stderr", source)

    def test_full_and_xvfb_probe_share_x_tool_mounts(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("append_xvfb_tool_mounts()", source)
        self.assertEqual(source.count("append_xvfb_tool_mounts"), 2)
        self.assertIn(
            'if [[ ${outer_mode} == "full" || ${outer_mode} == "xvfb-probe" || ${outer_mode} == "namespace-guard-probe" || ${outer_mode} == "dbus-probe" || ${outer_mode} == "install-guard-probe" ]]; then\n'
            "\tappend_xvfb_tool_mounts",
            source,
        )
        mount_body = source.split("append_xvfb_tool_mounts() {", 1)[1].split("\n}", 1)[
            0
        ]
        self.assertIn('--ro-bind "${XVFB_BIN}" "${INNER_XVFB}"', mount_body)
        self.assertIn('--ro-bind "${XPROP_BIN}" "${INNER_XPROP}"', mount_body)

    def test_xvfb_probe_static_path_has_no_gui_dependencies(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        probe = source.split('if [[ ${SOC_XVFB_PROBE:-0} == "1" ]]; then', 1)[1].split(
            "\n\tfi", 1
        )[0]
        for forbidden in ("dbus", "cinnamon", "applet-crash-safety", "clipboard"):
            self.assertNotIn(forbidden, probe.lower())

    def test_static_has_no_host_runtime_store_or_safe_local_fs(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        for forbidden in (
            "safe-local-fs",
            "private-tempdir",
            "create-soc-gui-residue",
            "prepare-soc-gui-root",
            "verify-soc-gui-root",
            "scrub-soc-gui-root",
            "XDG_RUNTIME_DIR:-",
            "mktemp",
            "rm -rf",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("eval ", source)
        self.assertNotIn("/bin/sh -c", source)

    def test_static_completion_and_outer_identity_are_bound(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        for marker in (
            "os.pipe2(os.O_CLOEXEC)",
            "read_fd, write_fd = os.pipe2(os.O_CLOEXEC)",
            "stdout=write_fd",
            "os.pidfd_open(process.pid, 0)",
            "signal.pidfd_send_signal(pidfd, signum, None, 0)",
            "SOC_GUI_COMPLETE",
            "completion frame mismatch",
            "isolated namespace deadline exceeded",
            "MAX_STDERR = 8192",
        ):
            self.assertIn(marker, source)

    def test_static_core_interface_uses_private_inner_supervisor(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        for marker in (
            "--inner-supervisor",
            '"${INNER_REPO}/scripts/gui-session-supervisor.py"',
            "--completion-fd 3",
            "--stdout-log /run/soc-work/controller.stdout",
            "--stderr-log /run/soc-work/controller.stderr",
            'child_mode="--namespace-child"',
            'child_mode="--namespace-probe-child"',
            'child_mode="--namespace-guard-probe-child"',
        ):
            self.assertIn(marker, source)
        self.assertIn("create_private_xauthority", source)
        self.assertIn("secrets.token_hex(16)", source)

    def test_namespace_guard_child_calls_core_with_exact_argument(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("namespace_child_main guard-probe", source)
        self.assertIn('scripts/applet-crash-safety.sh" --namespace-guard-probe', source)
        self.assertNotIn("SOC_NAMESPACE_GUARD_PROBE", source)

    def test_namespace_guard_probe_uses_full_inner_path_without_gui_calls(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn('outer_mode="namespace-guard-probe"', source)
        self.assertIn('child_mode="--namespace-guard-probe-child"', source)
        probe = source.split(
            'if [[ ${namespace_child_mode} == "guard-probe" ]]; then', 1
        )[1].split("\n\tfi", 1)[0]
        for forbidden in ("xvfb", "xprop", "dbus", "xephyr", "cinnamon", "clipboard"):
            self.assertNotIn(forbidden, probe.lower())

    def test_install_guard_probe_uses_core_only_before_all_runtime_components(
        self,
    ) -> None:
        adapter = ADAPTER.read_text(encoding="utf-8")
        self.assertIn('outer_mode="install-guard-probe"', adapter)
        self.assertIn('${mode} == "install-guard-probe"', adapter)
        self.assertIn('child_mode="--install-guard-probe-child"', adapter)
        child = adapter.split(
            'if [[ ${namespace_child_mode} == "install-guard-probe" ]]; then', 1
        )[1].split("\n\tfi", 1)[0]
        self.assertIn('scripts/applet-crash-safety.sh" --install-guard-probe', child)
        for forbidden in (
            "xauth",
            "xvfb",
            "dbus",
            "xephyr",
            "cinnamon --replace",
            "clipboard",
        ):
            self.assertNotIn(forbidden, child.lower())
        core = CORE.read_text(encoding="utf-8")
        self.assertIn('elif [[ $# -eq 1 && $1 == "--install-guard-probe" ]]', core)
        probe_start = core.rindex('if [[ ${install_guard_probe} == "1" ]]; then')
        probe_end = core.index("\nfi", probe_start) + len("\nfi")
        probe = core[probe_start:probe_end]
        self.assertIn(
            'run_nested setup env SPEED_OF_CINNAMON_TEST_HOME=1 HOME="${session_home}"',
            probe,
        )
        self.assertIn(
            'safe-local-fs.py" assert-private-chain install "${session_home}" --allow-missing',
            probe,
        )
        self.assertNotIn("install-local.sh", probe)
        for forbidden in (
            "run_xauth",
            "run_xvfb",
            "run_dbus_server",
            "run_xephyr_server",
            "cinnamon --replace",
            "clipboard",
        ):
            self.assertNotIn(forbidden, probe.lower())
        for action in (
            "run_dbus_server /usr/bin/dbus-daemon",
            'bash "${repo_dir}/scripts/install-local.sh"',
            "run_xephyr_server Xephyr",
            "run_nested target cinnamon --replace",
        ):
            self.assertGreater(core.index(action), probe_start)
        for code in ("ARG", "OPTIN", "ENV"):
            self.assertIn(f"install_guard_fail {code}", adapter)
        self.assertIn("INSTALL_GUARD_FAIL NAMESPACE", core)
        self.assertNotIn("printf 'INSTALL_GUARD_FAIL CORE\\n'", core)
        self.assertIn("INSTALL_GUARD_FAIL SUPERVISOR", adapter)
        self.assertIn("install_failure(data)", adapter)
        self.assertIn(
            'sys.argv[4] not in {"full", "probe", "xvfb-probe", "namespace-guard-probe", "dbus-probe", "install-guard-probe"}',
            adapter,
        )

    def test_install_guard_outer_heredoc_has_exact_controller_argv_contract(
        self,
    ) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        heredoc = source.rsplit("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(heredoc, filename=str(ADAPTER))
        guard = next(node for node in tree.body if isinstance(node, ast.If))
        condition = ast.unparse(guard.test)
        self.assertIn("len(sys.argv) < 9", condition)
        self.assertIn("sys.argv[4]", condition)
        self.assertIn("sys.argv[5]", condition)
        self.assertIn("sys.argv[7]", condition)
        assignments = {
            target.id: ast.unparse(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertEqual(assignments["nonce"], "sys.argv[3]")
        self.assertEqual(assignments["outer_mode"], "sys.argv[4]")
        self.assertEqual(assignments["helper_path"], "sys.argv[6]")
        self.assertEqual(assignments["command"], "sys.argv[8:]")
        parse_positions = {
            ast.unparse(node.args[0])
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "float"
        }
        self.assertEqual(parse_positions, {"sys.argv[1]", "sys.argv[2]"})
        outer = source.split("outer_python_command=(", 1)[1].split(
            ")\nrun_outer_python", 1
        )[0]
        elements = [line.strip() for line in outer.splitlines() if line.strip()]
        self.assertEqual(
            elements.count(
                '"${ENV_BIN}" -i PATH="${TRUSTED_PATH}" LC_ALL=C PYTHONDONTWRITEBYTECODE=1'
            ),
            1,
        )
        self.assertEqual(
            elements.count(
                '"${PYTHON_BIN}" -I -S - "${outer_timeout}" "${CLEANUP_RESERVE_SECONDS}" "${completion_nonce}" "${outer_mode}" --'
            ),
            1,
        )
        self.assertEqual(elements.count('"${supervisor_path}" --'), 1)
        self.assertIn("readonly PROBE_OUTER_TIMEOUT_SECONDS=30", source)
        self.assertIn("readonly DRAIN_MARGIN_SECONDS=5", source)
        self.assertIn(
            "inner_timeout + 2 * inner_cleanup_grace + DRAIN_MARGIN_SECONDS < outer_timeout",
            source,
        )
        self.assertIn('inner_supervisor_main "$2" "$3" "$4" "$5"', source)
        self.assertIn('--timeout "${inner_timeout}"', source)
        self.assertIn('--term-grace "${inner_cleanup_grace}"', source)
        self.assertIn('"${inner_timeout}"\n\t"${inner_cleanup_grace}"', source)
        bwrap = source.split("bwrap_command=(", 1)[1].split(")\nprobe_env=", 1)[0]
        self.assertIn("--clearenv", bwrap)
        self.assertNotIn("SOC_RUN_INSTALL_GUARD_PROBE", bwrap)
        self.assertNotIn(
            "SOC_RUN_INSTALL_GUARD_PROBE", CORE.read_text(encoding="utf-8")
        )

    def test_install_guard_nonzero_uses_status_printer_and_not_sanitized_tail(
        self,
    ) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        install_branch = source.split(
            'if [[ ${child_mode} == "--install-guard-probe-child" ]]; then', 1
        )[1].split("\n\telif", 1)[0]
        self.assertNotIn("--print-install-guard-status", install_branch)
        self.assertNotIn("controller.stderr", install_branch)
        self.assertNotIn("--print-sanitized-tail", install_branch)
        self.assertIn("install_result_line", install_branch)
        self.assertIn("install-result", source)
        self.assertIn("--print-install-guard-result", source)

    def test_install_guard_top_level_capture_is_private_and_mode_aware(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("mode_aware_preflight_fail()", source)
        self.assertIn("run_outer_python()", source)
        self.assertIn("--run-install-guard-outer", source)
        self.assertIn("--term-grace", source)
        self.assertNotIn("set -o noclobber", source)
        self.assertNotIn("install_status_root", source)
        self.assertNotIn("install_status_path", source)
        self.assertNotIn(
            "--print-install-guard-status /run/soc-work/controller.stderr >&2 || true",
            source,
        )
        self.assertIn("/run/soc-controller/install-result", source)

    def test_core_frame_preserves_spawn_vs_frame_phase(self) -> None:
        source = CORE.read_text(encoding="utf-8")
        frame_case = source.split("\tCORE_FRAME)\n", 1)[1].split(
            "\tCORE_STATUS_OK_NONZERO", 1
        )[0]
        self.assertIn("if ((probe_status != 0)); then", frame_case)
        self.assertIn("INSTALL_GUARD_FAIL CORE_RESULT_STATUS_SPAWN", frame_case)
        self.assertIn("INSTALL_GUARD_FAIL CORE_RESULT_STATUS_FRAME", frame_case)
        self.assertLess(
            frame_case.index("probe_status != 0"),
            frame_case.index("CORE_RESULT_STATUS_SPAWN"),
        )
        self.assertLess(
            frame_case.index("CORE_RESULT_STATUS_SPAWN"),
            frame_case.index("CORE_RESULT_STATUS_FRAME"),
        )
        self.assertNotIn("CORE_FD\\n", frame_case)

    def test_install_guard_helper_nonzero_emits_only_outer_helper(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-helper-failure.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            helper = root / "nonzero-helper"
            helper.write_text("#!/usr/bin/env bash\nexit 7\n", encoding="utf-8")
            helper.chmod(0o700)
            source = ADAPTER.read_text(encoding="utf-8")
            block = source.split(
                'if [[ ${child_mode} == "--install-guard-probe-child" ]]; then', 1
            )[1].split("\n\telif", 1)[0]
            script = (
                "#!/usr/bin/env bash\n"
                "run_install_failure() {\n"
                '  local child_mode="--install-guard-probe-child"\n'
                "  local supervisor_status=1\n"
                f"  PYTHON_BIN={str(helper)!r}\n"
                f"  INNER_REPO={str(root)!r}\n"
                f"{block}\n"
                "}\n"
                "run_install_failure\n"
            )
            harness = root / "run-block"
            harness.write_text(script, encoding="utf-8")
            harness.chmod(0o700)
            result = subprocess.run(
                ["/usr/bin/bash", str(harness)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "INSTALL_GUARD_FAIL CORE_FRAME\n")

    def test_install_guard_root_code_selection_is_strict_and_deadline_is_fixed(
        self,
    ) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn("/run/soc-controller/install-result", source)
        self.assertIn("EARLY_INSTALL_FAILURES", source)
        self.assertIn('if data == b"OK\\n":', source)
        self.assertIn("sys.stderr.buffer.write(failure)", source)
        self.assertNotIn('b"INSTALL_GUARD_FAIL CORE\\n"', source)
        self.assertIn('b"INSTALL_GUARD_FAIL SUPERVISOR\\n"', source)
        self.assertIn("except TimeoutError:", source)
        self.assertIn(
            'if outer_mode == "install-guard-probe":\n        sys.stderr.buffer.write(b"INSTALL_GUARD_FAIL SUPERVISOR\\n")',
            source,
        )
        self.assertIn("if stderr_bytes:", source)
        self.assertIn("def install_failure(data):", source)
        core = CORE.read_text(encoding="utf-8")
        for code in (
            "CORE_PARSER_EXIT",
            "CORE_PARSER_EMPTY",
            "CORE_PARSER_MULTIPLE",
            "CORE_PARSER_CONTROL",
            "CORE_PARSER_UNKNOWN",
        ):
            self.assertIn(f"INSTALL_GUARD_FAIL {code}", source + core)
        self.assertIn("parse-status-fd", core)
        self.assertIn('--child-exit-status "${probe_status}"', core)
        self.assertNotIn("head -c 129", core)

    def test_real_install_guard_probe_is_explicitly_opt_in(self) -> None:
        if os.environ.get("SOC_RUN_REAL_INSTALL_GUARD_PROBE") != "1":
            self.skipTest(
                "set SOC_RUN_REAL_INSTALL_GUARD_PROBE=1 to run the real isolated install guard probe"
            )
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("bubblewrap is unavailable")
        before = relevant_process_snapshot()
        before_soc = {path.name for path in Path("/run").glob("soc-*")}
        result = subprocess.run(
            [str(ADAPTER), "--install-guard-probe"],
            cwd=REPO_ROOT,
            env={
                "PATH": TRUSTED_PATH,
                "HOME": str(fixed_harness_home()),
                "LC_ALL": "C",
                "SOC_RUN_GUI_LIVE_TESTS": "1",
                "SOC_RUN_INSTALL_GUARD_PROBE": "1",
            },
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(relevant_process_snapshot() - before, frozenset())
        self.assertEqual({path.name for path in Path("/run").glob("soc-*")}, before_soc)

    def test_fake_probe_runs_once_with_exact_mount_and_fd_contract(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-test.", dir="/tmp"
        ) as temporary:
            harness = self._harness(Path(temporary))
            result = self._run(harness, "--probe")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            argv = json.loads(harness["log"].read_text(encoding="utf-8"))["argv"]
            self.assertEqual(argv.count("--unshare-all"), 1)
            self.assertEqual(argv.count("--clearenv"), 1)
            self.assertNotIn("--preserve-fds", argv)
            self.assertNotIn("--as-pid-1", argv)
            self.assertNotIn("--bind", argv)
            index = argv.index("--inner-supervisor")
            self.assertEqual(argv[index + 1], "probe")
            self.assertRegex(argv[index + 2], r"^[0-9a-f]{64}$")
            self.assertEqual(argv[index + 3], "10")
            self.assertEqual(argv[index + 4], "2")
            self.assertLess(10 + 2 * 2 + 5, 30)

    def test_host_display_bus_authority_and_runtime_are_not_forwarded(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-env.", dir="/tmp"
        ) as temporary:
            harness = self._harness(Path(temporary))
            before_home = list(harness["home"].iterdir())
            before_runtime = list(harness["runtime"].iterdir())
            result = self._run(harness, "--probe")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(harness["log"].read_text(encoding="utf-8"))
            self.assertEqual(
                record["env"],
                {
                    "DISPLAY": None,
                    "WAYLAND_DISPLAY": None,
                    "DBUS_SESSION_BUS_ADDRESS": None,
                    "XAUTHORITY": None,
                    "HOME": None,
                    "XDG_RUNTIME_DIR": None,
                },
            )
            self.assertEqual(list(harness["home"].iterdir()), before_home)
            self.assertEqual(list(harness["runtime"].iterdir()), before_runtime)

    def test_full_core_invocation_uses_only_allowed_env(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        invocation = source.split("SOC_RUN_GUI_LIVE_TESTS=1", 1)[1].split(
            "core_pid=$!", 1
        )[0]
        for forbidden in (
            "APPLET_CRASH_SAFETY_REPO",
            "APPLET_CRASH_SAFETY_TEST_ROOT",
            "APPLET_CRASH_SAFETY_HOST_DISPLAY",
            "APPLET_CRASH_SAFETY_DISPLAY",
            "APPLET_CRASH_SAFETY_HOST_XAUTHORITY",
            "APPLET_CRASH_SAFETY_NESTED_XAUTHORITY",
            "APPLET_CRASH_SAFETY_CONTROLLER_ROOT",
            "APPLET_CRASH_SAFETY_BWRAP",
        ):
            self.assertNotIn(forbidden, invocation)
        self.assertIn("APPLET_CRASH_SAFETY_SMOKE_MODE", invocation)
        outer = source.split("bwrap_command=(", 1)[1].split("probe_env=()", 1)[0]
        self.assertIn("--clearenv", outer)
        for allowed in (
            "--setenv PATH",
            "--setenv HOME",
            "--setenv TMPDIR",
            "--setenv LC_ALL",
            "--setenv SOC_RUN_GUI_LIVE_TESTS",
        ):
            self.assertIn(allowed, source)
        for forbidden in (
            "--setenv APPLET_CRASH_SAFETY_REPO",
            "--setenv APPLET_CRASH_SAFETY_TEST_ROOT",
            "--setenv APPLET_CRASH_SAFETY_HOST_DISPLAY",
            "--setenv APPLET_CRASH_SAFETY_DISPLAY",
            "--setenv APPLET_CRASH_SAFETY_HOST_XAUTHORITY",
            "--setenv APPLET_CRASH_SAFETY_NESTED_XAUTHORITY",
            "--setenv APPLET_CRASH_SAFETY_CONTROLLER_ROOT",
            "--setenv APPLET_CRASH_SAFETY_BWRAP",
        ):
            self.assertNotIn(forbidden, source)

    def test_outer_xauth_trust_gate_is_fixed_root_owned_and_read_only_mounted(
        self,
    ) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        gate = source.split("validate_trusted_xauth() {", 1)[1].split("\n}\n", 1)[0]
        self.assertIn('"/usr/bin/xauth"', gate)
        self.assertIn("! -L", gate)
        self.assertIn("-f", gate)
        self.assertIn("-x", gate)
        self.assertIn("stat -c '%u:%h:%a'", gate)
        self.assertIn('${uid} == "0"', gate)
        self.assertIn('${links} == "1"', gate)
        self.assertIn("022", gate)
        self.assertIn(
            'validate_trusted_xauth || mode_aware_preflight_fail 2 "SETUP_XAUTH_TRUST"',
            source,
        )
        outer = source.split("bwrap_command=(", 1)[1].split("probe_env=()", 1)[0]
        self.assertIn("--ro-bind /usr /usr", outer)

        xauth = Path("/usr/bin/xauth")
        self.assertTrue(
            xauth.is_file() and not xauth.is_symlink() and os.access(xauth, os.X_OK)
        )
        metadata = xauth.lstat()
        self.assertEqual(metadata.st_uid, 0)
        self.assertEqual(metadata.st_nlink, 1)
        for mode, accepted in (("0755", True), ("0775", False), ("0757", False)):
            result = subprocess.run(
                ["bash", "-c", 'mode="$1"; (( (0${mode} & 022) == 0 ))', "mode", mode],
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode == 0, accepted, mode)

    def test_outer_dbus_trust_gate_and_dbus_probe_use_fixed_private_path(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn('readonly DBUS_DAEMON_BIN="/usr/bin/dbus-daemon"', source)
        gate = source.split("validate_trusted_dbus() {", 1)[1].split("\n}\n", 1)[0]
        for marker in (
            '"/usr/bin/dbus-daemon"',
            "! -L",
            "-f",
            "-x",
            "stat -c '%u:%h:%a'",
            '${uid} == "0"',
            '${links} == "1"',
            "022",
        ):
            self.assertIn(marker, gate)
        self.assertIn(
            'validate_trusted_dbus || mode_aware_preflight_fail 2 "SETUP_DBUS_TRUST"',
            source,
        )
        self.assertIn('outer_mode="dbus-probe"', source)
        self.assertIn('child_mode="--dbus-probe-child"', source)
        self.assertIn("core_args=(--dbus-probe)", source)
        self.assertIn(
            '"${INNER_REPO}/scripts/applet-crash-safety.sh" "${core_args[@]}"', source
        )
        self.assertIn(
            "run_dbus_server /usr/bin/dbus-daemon", CORE.read_text(encoding="utf-8")
        )

        self.assertIn('elif [[ $# -eq 1 && $1 == "--dbus-probe" ]]', source)

    def test_inner_budget_reaches_bash_u_child_and_rejects_invalid_values(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        self.assertIn('export SOC_GUI_INNER_TIMEOUT_SECONDS="${inner_timeout}"', source)
        self.assertIn(
            'local inner_timeout="${SOC_GUI_INNER_TIMEOUT_SECONDS:-}"', source
        )
        self.assertIn("local core_deadline=$((SECONDS + inner_timeout))", source)
        self.assertIn("restore_signals=True", SUPERVISOR.read_text(encoding="utf-8"))
        for value in (None, "0", "101", "invalid"):
            with self.subTest(value=value):
                environment = {
                    "PATH": TRUSTED_PATH,
                    "HOME": os.environ["HOME"],
                    "SOC_RUN_GUI_LIVE_TESTS": "1",
                }
                if value is not None:
                    environment["SOC_GUI_INNER_TIMEOUT_SECONDS"] = value
                result = subprocess.run(
                    [
                        "/usr/bin/bash",
                        "-u",
                        str(ADAPTER),
                        "--namespace-guard-probe-child",
                    ],
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=2,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("invalid inner supervisor budget", result.stderr)

    def test_real_dbus_probe_integration_is_explicitly_opt_in(self) -> None:
        if os.environ.get("SOC_RUN_REAL_DBUS_PROBE") != "1":
            self.skipTest(
                "set SOC_RUN_REAL_DBUS_PROBE=1 to run the real isolated probe"
            )
        xvfb = fixed_harness_home() / ".local" / "bin" / "Xvfb"
        required = (
            Path("/usr/bin/bwrap"),
            Path("/usr/bin/xauth"),
            Path("/usr/bin/dbus-daemon"),
            Path("/usr/bin/xprop"),
            xvfb,
        )
        if not all(path.is_file() for path in required):
            self.skipTest("real isolated DBus probe prerequisites are unavailable")
        before = relevant_process_snapshot()
        before_soc = {path.name for path in Path("/run").glob("soc-*")}
        env = real_probe_environment()
        result = subprocess.run(
            [str(ADAPTER), "--dbus-probe"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("Xephyr", result.stdout + result.stderr)
        self.assertNotIn("Cinnamon", result.stdout + result.stderr)
        after = relevant_process_snapshot()
        self.assertEqual(after - before, frozenset())
        self.assertEqual({path.name for path in Path("/run").glob("soc-*")}, before_soc)

    def test_real_probe_snapshot_tracks_pid_and_comm_without_pgrep(self) -> None:
        snapshot = relevant_process_snapshot()
        self.assertTrue(all(isinstance(pid, int) and pid > 0 for pid, _, _ in snapshot))
        self.assertTrue(all(comm in RELEVANT_PROCESSES for _, comm, _ in snapshot))
        self.assertTrue(
            all(isinstance(start, int) and start > 0 for _, _, start in snapshot)
        )

    def test_proc_identity_rejects_pid_reuse_with_same_pid_and_comm(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-proc-stat.", dir="/tmp"
        ) as temporary:
            entry = Path(temporary)

            def write_stat(starttime: int) -> None:
                fields = ["S", "1"] + ["0"] * 17 + [str(starttime)]
                (entry / "stat").write_bytes(
                    ("4242 (same name with spaces) " + " ".join(fields) + "\n").encode(
                        "ascii"
                    )
                )

            write_stat(100)
            first = proc_identity(entry)
            write_stat(200)
            second = proc_identity(entry)
        self.assertEqual(first[:2], second[:2])
        self.assertNotEqual(first[2], second[2])

    def test_proc_identity_parses_parentheses_and_unusual_comm(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-proc-stat.", dir="/tmp"
        ) as temporary:
            entry = Path(temporary)
            fields = ["S", "1"] + ["0"] * 17 + ["987654"]
            (entry / "stat").write_bytes(
                ("77 (odd ) name (x)\t) " + " ".join(fields) + "\n").encode("ascii")
            )
            self.assertEqual(proc_identity(entry), (77, "odd ) name (x)\t", 987654))

    def test_proc_identity_race_or_missing_stat_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-proc-stat.", dir="/tmp"
        ) as temporary:
            with self.assertRaises(FileNotFoundError):
                proc_identity(Path(temporary))

    def test_real_probe_environment_is_minimal_and_injection_free(self) -> None:
        environment = real_probe_environment()
        self.assertEqual(environment.keys(), REAL_PROBE_ENV_KEYS)
        for forbidden in (
            "BASH_ENV",
            "ENV",
            "CDPATH",
            "SHELLOPTS",
            "BASHOPTS",
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XAUTHORITY",
            "DBUS_STARTER_ADDRESS",
            "GDK_BACKEND",
            "GTK_MODULES",
        ):
            self.assertNotIn(forbidden, environment)

    def test_authority_ownership_is_host_before_core_and_nested_exclusive_in_core(
        self,
    ) -> None:
        adapter = ADAPTER.read_text(encoding="utf-8")
        child = adapter.split("namespace_child_main() {", 1)[1].split("\n}\n\n", 1)[0]
        host_create = 'create_private_xauthority "${INNER_HOST_AUTHORITY}"'
        nested_create = 'create_private_xauthority "${INNER_NESTED_AUTHORITY}"'
        self.assertEqual(child.count(host_create), 1)
        self.assertNotIn(nested_create, child)
        self.assertLess(
            child.index(host_create), child.index("SOC_RUN_GUI_LIVE_TESTS=1")
        )
        core = CORE.read_text(encoding="utf-8")
        self.assertEqual(core.count('name != "nested.Xauthority"'), 1)
        self.assertIn(
            "os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW",
            core,
        )
        self.assertNotIn("os.O_TRUNC", core)
        self.assertNotIn("os.O_APPEND", core)

    def test_missing_completion_frame_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-frame.", dir="/tmp"
        ) as temporary:
            result = self._run(self._harness(Path(temporary), "no-frame"), "--probe")
            self.assertEqual(result.returncode, 125)
            self.assertIn("isolated namespace failed closed", result.stderr)

    def test_forged_completion_frame_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-frame.", dir="/tmp"
        ) as temporary:
            result = self._run(self._harness(Path(temporary), "bad-frame"), "--probe")
            self.assertEqual(result.returncode, 125)
            self.assertIn("isolated namespace failed closed", result.stderr)

    def test_stderr_is_drained_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-stderr.", dir="/tmp"
        ) as temporary:
            result = self._run(
                self._harness(Path(temporary), "large-stderr"), "--probe"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLessEqual(len(result.stderr.encode("utf-8")), 8192)

    def test_install_failure_uses_bounded_bwrap_stderr_pipe(self) -> None:
        source = ADAPTER.read_text(encoding="utf-8")
        heredoc = source.rsplit("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        tree = ast.parse(heredoc, filename=str(ADAPTER))
        popen_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.func.attr == "Popen"
            and any(
                keyword.arg == "stderr"
                and isinstance(keyword.value, ast.Attribute)
                and isinstance(keyword.value.value, ast.Name)
                and keyword.value.value.id == "subprocess"
                and keyword.value.attr == "PIPE"
                for keyword in node.keywords
            )
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id == "command"
        ]
        self.assertEqual(len(popen_calls), 1)
        stderr_keyword = next(
            keyword for keyword in popen_calls[0].keywords if keyword.arg == "stderr"
        )
        self.assertEqual(ast.unparse(stderr_keyword.value), "subprocess.PIPE")
        self.assertIn("os.read(process.stderr.fileno(), 4096)", heredoc)
        self.assertIn(
            'failure = install_failure(bytes(stderr_bytes)) if outer_mode == "install-guard-probe" else None',
            heredoc,
        )
        namespace = self._production_install_failure()
        namespace["stderr_bytes"] = b"inner controller unknown detail\n"
        self.assertEqual(
            namespace["install_failure"](b"inner controller unknown detail\n"),
            b"INSTALL_GUARD_FAIL CORE_FRAME\n",
        )
        self.assertNotIn(
            b"inner controller unknown detail",
            namespace["install_failure"](b"inner controller unknown detail\n"),
        )

    def test_no_frame_stderr_tail_is_sanitized_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-tail.", dir="/tmp"
        ) as temporary:
            result = self._run(
                self._harness(Path(temporary), "large-stderr-no-frame"), "--probe"
            )
            self.assertEqual(result.returncode, 125)
            self.assertLessEqual(len(result.stderr.encode("utf-8")), 8192)
            self.assertNotIn("\x1b", result.stderr)
            self.assertNotIn("\x00", result.stderr)
            self.assertNotIn("\x01", result.stderr)
            self.assertIn("isolated namespace failed closed", result.stderr)

    def test_sanitize_helper_timeout_is_terminated_reaped_and_pipes_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-helper-timeout.", dir="/tmp"
        ) as temporary:
            harness = self._harness(Path(temporary), "helper-timeout")
            result = self._run(harness, "--probe", timeout=6)
            self.assertEqual(result.returncode, 125)
            helper_pid = int(harness["helper_pid"].read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{helper_pid}").exists())
            self.assertLessEqual(len(result.stderr.encode("utf-8")), 8192)

    def test_deadline_kills_and_reaps_bound_bwrap(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-timeout.", dir="/tmp"
        ) as temporary:
            harness = self._harness(Path(temporary), "timeout", probe_timeout=1)
            started = time.monotonic()
            result = self._run(harness, "--probe", timeout=8)
            self.assertEqual(result.returncode, 124, result.stdout + result.stderr)
            self.assertLess(time.monotonic() - started, 7)
            pid = int(harness["pid"].read_text(encoding="ascii"))
            self.assertFalse(Path(f"/proc/{pid}").exists())
            argv = json.loads(harness["log"].read_text(encoding="utf-8"))["argv"]
            index = argv.index("--inner-supervisor")
            self.assertEqual(argv[index + 3], "1")
            self.assertEqual(argv[index + 4], "1")
            self.assertLess(1 + 2 * 1 + 1, 5)

    def test_external_arguments_are_rejected_without_spawn(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="soc-adapter-args.", dir="/tmp"
        ) as temporary:
            harness = self._harness(Path(temporary))
            result = self._run(harness, "--command", "/usr/bin/true")
            self.assertEqual(result.returncode, 2)
            self.assertFalse(harness["log"].exists())

    def test_direct_inner_mode_is_rejected_on_host(self) -> None:
        result = subprocess.run(
            [str(ADAPTER), "--namespace-probe-child"],
            env={"PATH": TRUSTED_PATH, "SOC_RUN_GUI_LIVE_TESTS": "1"},
            capture_output=True,
            text=True,
            check=False,
            timeout=3,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("private namespace verification failed", result.stderr)

    def test_real_minimal_bwrap_probe_has_no_host_artifacts(self) -> None:
        bwrap = Path("/usr/bin/bwrap")
        if not bwrap.is_file():
            self.skipTest("bubblewrap is unavailable")
        preflight = subprocess.run(
            [
                str(bwrap),
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--clearenv",
                "--tmpfs",
                "/",
                "--ro-bind",
                "/usr",
                "/usr",
                "--symlink",
                "usr/bin",
                "/bin",
                "--symlink",
                "usr/lib",
                "/lib",
                "--symlink",
                "usr/lib64",
                "/lib64",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run",
                "--",
                "/usr/bin/true",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if preflight.returncode != 0:
            capability_markers = (
                "Operation not permitted",
                "No permissions",
                "Creating new namespace failed",
                "Permission denied",
            )
            if any(marker in preflight.stderr for marker in capability_markers):
                self.skipTest("host blocks required bubblewrap namespace capability")
            self.fail(preflight.stderr)
        with tempfile.TemporaryDirectory(
            prefix="soc-real-probe.", dir="/tmp"
        ) as temporary:
            sentinel = Path(temporary)
            before = {entry.name for entry in sentinel.iterdir()}
            result = subprocess.run(
                [str(ADAPTER), "--probe"],
                env={
                    "PATH": TRUSTED_PATH,
                    "HOME": str(sentinel),
                    "XDG_RUNTIME_DIR": str(sentinel),
                    "SOC_RUN_GUI_LIVE_TESTS": "1",
                },
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual({entry.name for entry in sentinel.iterdir()}, before)

    def test_run_snapshot_filters_non_soc_before_applying_limit(self):
        with tempfile.TemporaryDirectory(
            prefix="soc-run-snapshot.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            for index in range(RUN_SNAPSHOT_LIMIT + 1):
                (root / f"ordinary-{index}").mkdir()
            soc_entry = root / "soc-kept"
            soc_entry.mkdir()
            snapshot = run_metadata_snapshot(root)
            self.assertEqual(set(snapshot), {soc_entry.name})

    def test_run_snapshot_rejects_more_than_limit_soc_entries(self):
        with tempfile.TemporaryDirectory(
            prefix="soc-run-snapshot.", dir="/tmp"
        ) as temporary:
            root = Path(temporary)
            for index in range(RUN_SNAPSHOT_LIMIT + 1):
                (root / f"soc-{index}").mkdir()
            with self.assertRaisesRegex(AssertionError, r"^SOC_RUN_SNAPSHOT_LIMIT$"):
                run_metadata_snapshot(root)

    def test_install_guard_nonzero_end_to_end_core_parser_empty(self):
        """Exercise the real outer/inner/core/result pipeline without a GUI."""
        xvfb = fixed_harness_home() / ".local" / "bin" / "Xvfb"
        trusted_tools = {
            "/usr/bin/bwrap": 0,
            "/usr/bin/python3.14": 0,
            "/usr/bin/xauth": 0,
            "/usr/bin/dbus-daemon": 0,
            "/usr/bin/xprop": 0,
            "/usr/bin/timeout": 0,
            str(xvfb): os.getuid(),
        }
        missing = [path for path in trusted_tools if not os.path.lexists(path)]
        if missing:
            self.skipTest("required isolated test tool is unavailable")
        for path, expected_uid in trusted_tools.items():
            metadata = os.lstat(path)
            self.assertTrue(
                stat.S_ISREG(metadata.st_mode) and metadata.st_nlink == 1,
                "trusted test tool metadata rejected",
            )
            self.assertEqual(
                metadata.st_uid, expected_uid, "trusted test tool owner rejected"
            )
            self.assertTrue(
                metadata.st_mode & stat.S_IXUSR, "trusted test tool is not executable"
            )
            self.assertEqual(
                metadata.st_mode & 0o022, 0, "trusted test tool is writable"
            )
        with xvfb.open("rb") as xvfb_file:
            xvfb_digest = hashlib.file_digest(xvfb_file, "sha256").hexdigest()
        self.assertEqual(
            xvfb_digest,
            "743d451bafa4ebd6b4ad6302c66aa4c655d245bd2c3ba1b48092857421f8e145",
            "trusted Xvfb digest rejected",
        )
        bwrap = Path("/usr/bin/bwrap")
        if not bwrap.is_file():
            self.skipTest("mandatory E2E test requires bubblewrap")
        capability = subprocess.run(
            [
                str(bwrap),
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--clearenv",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run",
                "--",
                "/usr/bin/true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        if capability.returncode != 0 and any(
            marker in capability.stderr
            for marker in (
                b"Operation not permitted",
                b"Permission denied",
                b"namespace",
            )
        ):
            self.skipTest("host blocks required bubblewrap namespace capability")
        self.assertEqual(
            capability.returncode, 0, "bubblewrap capability preflight failed"
        )

        before_processes = relevant_process_snapshot()
        before_run = run_metadata_snapshot()

        def assert_no_residues():
            after_processes = relevant_process_snapshot()
            after_run = run_metadata_snapshot()
            self.assertFalse(after_processes - before_processes)
            self.assertEqual(after_run, before_run)

        self.addCleanup(assert_no_residues)
        with tempfile.TemporaryDirectory(prefix="soc-install-e2e-") as temporary:
            copied_repo = Path(temporary)
            copied_repo.chmod(0o700)
            files_dir = copied_repo / "files"
            fixture_dir = files_dir / "speed-of-cinnamon@H234598"
            files_dir.mkdir(mode=0o700)
            fixture_dir.mkdir(mode=0o700)
            for directory in (files_dir, fixture_dir):
                metadata = os.lstat(directory)
                self.assertTrue(stat.S_ISDIR(metadata.st_mode))
                self.assertFalse(stat.S_ISLNK(metadata.st_mode))
                self.assertEqual(metadata.st_uid, os.getuid())
                self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o700)
            copied_scripts = copied_repo / "scripts"
            copied_scripts.mkdir(parents=True, mode=0o700)
            for source in (
                ADAPTER,
                SUPERVISOR,
                CORE,
                REPO_ROOT / "scripts" / "safe-local-fs.py",
            ):
                destination = copied_scripts / source.name
                shutil.copy2(source, destination)
                destination.chmod(0o700)
            copied_scripts / ADAPTER.name
            copied_core = copied_scripts / CORE.name
            core_source = copied_core.read_text(encoding="utf-8")
            noise_marker = "printf 'INSTALL_GUARD_FAIL CORE_PARSER_EMPTY\\n' >&2"
            self.assertEqual(core_source.count(noise_marker), 1)
            copied_core.write_text(
                core_source.replace(
                    noise_marker,
                    "printf 'E2E_CORE_NOISE\\n' >&2\n" + noise_marker,
                    1,
                ),
                encoding="utf-8",
            )
            copied_safe_fs = copied_repo / "scripts" / "safe-local-fs.py"
            copied_safe_fs.write_text(
                "import os\n"
                "import sys\n"
                "fd = int(sys.argv[sys.argv.index('--status-fd') + 1])\n"
                "os.write(fd, b'STATUS_READY\\n')\n"
                "os.close(fd)\n"
                "os._exit(0)\n",
                encoding="ascii",
            )
            copied_safe_fs.chmod(0o700)

            environment = {
                "PATH": TRUSTED_PATH,
                "HOME": str(fixed_harness_home()),
                "LC_ALL": "C",
                "SOC_RUN_GUI_LIVE_TESTS": "1",
                "SOC_RUN_INSTALL_GUARD_PROBE": "1",
            }
            result = subprocess.run(
                [str(copied_repo / "scripts" / ADAPTER.name), "--install-guard-probe"],
                cwd=copied_repo,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
            )

            status = result.stderr.decode("ascii", "ignore")
            if not re.fullmatch(r"INSTALL_GUARD_FAIL [A-Z0-9_]+\n", status):
                status = "UNSAFE_STDERR"
            self.assertEqual(result.returncode, 1, status)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"INSTALL_GUARD_FAIL CORE_PARSER_EMPTY\n")
            self.assertNotIn(b"E2E_CORE_NOISE", result.stderr)

    def test_install_guard_success_end_to_end_writes_and_reads_ok_result(self):
        """Exercise the real outer/inner/core/result success path without a GUI."""
        bwrap = Path("/usr/bin/bwrap")
        if not bwrap.is_file():
            self.skipTest("mandatory E2E test requires bubblewrap")
        capability = subprocess.run(
            [
                str(bwrap),
                "--die-with-parent",
                "--new-session",
                "--unshare-all",
                "--clearenv",
                "--ro-bind",
                "/",
                "/",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--tmpfs",
                "/run",
                "--",
                "/usr/bin/true",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
            timeout=5,
        )
        if capability.returncode != 0 and any(
            marker in capability.stderr
            for marker in (
                b"Operation not permitted",
                b"Permission denied",
                b"namespace",
            )
        ):
            self.skipTest("host blocks required bubblewrap namespace capability")
        self.assertEqual(
            capability.returncode, 0, "bubblewrap capability preflight failed"
        )

        before_processes = relevant_process_snapshot()
        before_run = run_metadata_snapshot()

        def assert_no_residues():
            self.assertFalse(relevant_process_snapshot() - before_processes)
            self.assertEqual(run_metadata_snapshot(), before_run)

        self.addCleanup(assert_no_residues)
        with tempfile.TemporaryDirectory(
            prefix="soc-install-ok-e2e-", dir="/tmp"
        ) as temporary:
            copied_repo = Path(temporary)
            copied_repo.chmod(0o700)
            (copied_repo / "files").mkdir(mode=0o700)
            (copied_repo / "files" / "speed-of-cinnamon@H234598").mkdir(mode=0o700)
            copied_scripts = copied_repo / "scripts"
            copied_scripts.mkdir(mode=0o700)
            for source in (
                ADAPTER,
                SUPERVISOR,
                CORE,
                REPO_ROOT / "scripts" / "safe-local-fs.py",
            ):
                destination = copied_scripts / source.name
                shutil.copy2(source, destination)
                destination.chmod(0o700)
            result = subprocess.run(
                [str(copied_scripts / ADAPTER.name), "--install-guard-probe"],
                cwd=copied_repo,
                env={
                    "PATH": TRUSTED_PATH,
                    "HOME": str(fixed_harness_home()),
                    "LC_ALL": "C",
                    "SOC_RUN_GUI_LIVE_TESTS": "1",
                    "SOC_RUN_INSTALL_GUARD_PROBE": "1",
                },
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
            )
            fixed_status = "UNSAFE_STDERR"
            if re.fullmatch(
                rb"(?:INSTALL_GUARD_FAIL (?:ARG|OPTIN|ENV|SUPERVISOR|NAMESPACE|CORE_RESULT_[A-Z0-9_]+|CORE_STATUS_[A-Z0-9_]+)|ROOT_FAIL (?:ROOT_META|ROOT_EUID|ROOT_UID_MAP|ROOT_FDINFO|ROOT_MOUNT)|CHAIN_FAIL (?:OWNER|MODE|META|FD|MISSING))\n",
                result.stderr,
            ):
                fixed_status = result.stderr.decode("ascii").rstrip("\n")
            self.assertEqual(result.returncode, 0, fixed_status)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"")


if __name__ == "__main__":
    unittest.main()
