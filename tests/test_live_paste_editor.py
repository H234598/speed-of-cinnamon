from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from pathlib import Path


APPLET_UUID = "speed-of-cinnamon@H234598"
EVAL_TIMEOUT_SECONDS = 5
EDITOR_TIMEOUT_SECONDS = 15
PASTE_TIMEOUT_SECONDS = 8


def _require_display() -> None:
    if not os.environ.get("DISPLAY"):
        raise unittest.SkipTest("live paste test requires an X11 DISPLAY")


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise unittest.SkipTest(f"live paste test requires {name}")
    return path


def _run(
    args: list[str],
    *,
    timeout: int = EVAL_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {args!r}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
    return result


def _cinnamon_eval(script: str) -> object:
    result = _run(
        [
            _require_tool("gdbus"),
            "call",
            "--session",
            "--dest",
            "org.Cinnamon",
            "--object-path",
            "/org/Cinnamon",
            "--method",
            "org.Cinnamon.Eval",
            script,
        ],
        timeout=EVAL_TIMEOUT_SECONDS,
    )
    match = re.fullmatch(r"\((true|false), (.*)\)", result.stdout.strip(), re.DOTALL)
    if not match:
        raise AssertionError(f"unexpected Cinnamon Eval response: {result.stdout!r}")
    payload = ast.literal_eval(match.group(2))
    if match.group(1) != "true":
        raise AssertionError(f"Cinnamon Eval failed: {payload!r}")
    if payload == "":
        return None
    return json.loads(payload)


def _require_cinnamon_applet() -> None:
    try:
        payload = _cinnamon_eval(
            f"""
(() => {{
  const A = imports.ui.appletManager;
  return A.definitions.some(d =>
    (d.real_uuid === {json.dumps(APPLET_UUID)} || d.uuid === {json.dumps(APPLET_UUID)}) &&
    !!d.applet &&
    typeof d.applet._copyAndMaybePasteTranscriptText === "function"
  );
}})()
"""
        )
    except (AssertionError, subprocess.SubprocessError) as exc:
        raise unittest.SkipTest(f"live paste test requires Cinnamon Eval: {exc}") from exc
    if payload is not True:
        raise unittest.SkipTest(f"live paste test requires running applet {APPLET_UUID}")


def _require_clipboard_target_probe() -> None:
    try:
        payload = _cinnamon_eval(
            f"""
(() => {{
  const A = imports.ui.appletManager;
  const applet = A.definitions
    .filter(d => d.real_uuid === {json.dumps(APPLET_UUID)} || d.uuid === {json.dumps(APPLET_UUID)})
    .map(d => d.applet)
    .filter(a => !!a)[0];
  const spec = applet && applet._clipboardProgramSpec ? applet._clipboardProgramSpec() : null;
  return spec ? {{program: String(spec.program || ""), targetArgs: Array.isArray(spec.targetArgs)}} : null;
}})()
"""
        )
    except (AssertionError, subprocess.SubprocessError) as exc:
        raise unittest.SkipTest(f"live paste test requires clipboard target probing: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("program") not in {"xclip", "wl-paste"} or payload.get("targetArgs") is not True:
        raise unittest.SkipTest("live paste test requires a clipboard helper with target probing support")


def _window_ids_for(args: list[str]) -> list[str]:
    result = _run(args, timeout=2, check=False)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()]


def _window_text_property(window_id: str, property_name: str) -> str:
    xdotool = _require_tool("xdotool")
    result = _run([xdotool, property_name, window_id], timeout=2, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _is_owned_test_window(window_id: str, *, expected_title: str, expected_class: str) -> bool:
    actual_title = _window_text_property(window_id, "getwindowname")
    actual_class = _window_text_property(window_id, "getwindowclassname")
    return expected_title in actual_title and actual_class.lower() == expected_class.lower()


def _wait_for_window(title: str, process_pid: int, class_name: str, known_windows: set[str]) -> str:
    xdotool = _require_tool("xdotool")
    deadline = time.monotonic() + EDITOR_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        candidates: list[str] = []
        candidates.extend(_window_ids_for([xdotool, "search", "--onlyvisible", "--pid", str(process_pid)]))
        candidates.extend(_window_ids_for([xdotool, "search", "--onlyvisible", "--name", title]))
        class_windows = set(_window_ids_for([xdotool, "search", "--onlyvisible", "--class", class_name]))
        candidates.extend(sorted(class_windows - known_windows))
        seen: set[str] = set()
        for window_id in candidates:
            if window_id in seen:
                continue
            seen.add(window_id)
            if _is_owned_test_window(window_id, expected_title=title, expected_class=class_name):
                return window_id
        time.sleep(0.2)
    raise AssertionError(f"test window for {title!r} with class {class_name!r} did not appear")


def _wait_for_xed_window(title: str, editor_pid: int, known_windows: set[str]) -> str:
    return _wait_for_window(title, editor_pid, "xed", known_windows)


def _wait_for_terminal_window(title: str, terminal_pid: int, known_windows: set[str]) -> str:
    return _wait_for_window(title, terminal_pid, "Gnome-terminal", known_windows)


def _activate_window(window_id: str) -> None:
    _run([_require_tool("xdotool"), "windowactivate", "--sync", window_id], timeout=5)
    time.sleep(0.2)


def _close_owned_test_window(window_id: str, *, expected_title: str, expected_class: str) -> None:
    if not _is_owned_test_window(window_id, expected_title=expected_title, expected_class=expected_class):
        return
    _run([_require_tool("xdotool"), "windowclose", window_id], timeout=3, check=False)


def _trigger_applet_clipboard_paste(
    text: str,
    *,
    simulate_menu_click: bool = False,
    auto_paste_window_title: str = "",
) -> object:
    if simulate_menu_click:
        target_setup = """
  let menuActivated = false;
  applet._rememberFocusedWindow();
  applet._socLivePasteOriginalToggle = applet._toggleRecording;
  applet._socLivePasteMenuActivated = false;
  applet._toggleRecording = function() {
    applet._socLivePasteMenuActivated = true;
  };
  applet.on_applet_clicked();
  if (!applet.toggleItem || typeof applet.toggleItem.activate !== "function") {
    return {ok: false, reason: "toggleItem.activate unavailable"};
  }
  applet.toggleItem.activate(null);
  applet.menu.close();
"""
    else:
        target_setup = "  applet._rememberFocusedWindow();"
    captured = _cinnamon_eval(
        f"""
(() => {{
  const A = imports.ui.appletManager;
  const applet = A.definitions
    .filter(d => d.real_uuid === {json.dumps(APPLET_UUID)} || d.uuid === {json.dumps(APPLET_UUID)})
    .map(d => d.applet)
    .filter(a => !!a)[0];
  if (!applet) {{
    return {{ok: false, reason: "applet unavailable"}};
  }}
{target_setup}
  return {{ok: true}};
}})()
"""
    )
    if not isinstance(captured, dict) or captured.get("ok") is not True:
        return captured

    deadline = time.monotonic() + EVAL_TIMEOUT_SECONDS
    remembered = False
    while time.monotonic() < deadline:
        remembered = _cinnamon_eval(
            f"""
(() => {{
  const applet = imports.ui.appletManager.getRunningInstancesForUuid({json.dumps(APPLET_UUID)})[0];
  return !!applet && applet._hasRememberedTargetWindow() && !applet._isTargetWindowXLookupPending();
}})()
"""
        ) is True
        if remembered:
            break
        time.sleep(0.05)

    return _cinnamon_eval(
        f"""
(() => {{
  const applet = imports.ui.appletManager.getRunningInstancesForUuid({json.dumps(APPLET_UUID)})[0];
  if (!applet) {{
    return {{ok: false, reason: "applet unavailable"}};
  }}
  const oldInsertMethod = applet.insertMethod;
  const oldAppendSpace = applet.appendSpace;
  const oldAutoPasteWindowTitle = applet.autoPasteWindowTitle;
  const oldClipboardPayloadSnapshotAsync = applet._clipboardPayloadSnapshotAsync;
  applet.insertMethod = "clipboard-paste";
  applet.appendSpace = false;
  applet.autoPasteWindowTitle = {json.dumps(auto_paste_window_title)};
  applet._clipboardPayloadSnapshotAsync = function(callback) {{
    callback({{hasNonTextPayload: false}});
  }};
  let restored = false;
  const restoreSettings = function() {{
    if (restored) return;
    restored = true;
    applet.insertMethod = oldInsertMethod;
    applet.appendSpace = oldAppendSpace;
    applet.autoPasteWindowTitle = oldAutoPasteWindowTitle;
    applet._clipboardPayloadSnapshotAsync = oldClipboardPayloadSnapshotAsync;
  }};
  let inserted = false;
  let autoPasteEnter = false;
  let terminalTarget = false;
  try {{
    inserted = applet._insertTranscriptText({json.dumps(text)}, function() {{ restoreSettings(); }});
    autoPasteEnter = applet._windowTitleMatchesAutoPaste();
    terminalTarget = applet._isTerminalTargetWindow();
  }} finally {{
    if (inserted !== null) restoreSettings();
    if (applet._socLivePasteOriginalToggle) {{
      applet._toggleRecording = applet._socLivePasteOriginalToggle;
      delete applet._socLivePasteOriginalToggle;
    }}
  }}
  return {{
    ok: inserted === true || inserted === null,
    remembered: {json.dumps(remembered)},
    menuActivated: applet._socLivePasteMenuActivated === true,
    autoPasteEnter: autoPasteEnter,
    terminalTarget: terminalTarget,
    lastMessage: String(applet.lastMessage || ""),
    targetWindowXid: String(applet.targetWindowXid || ""),
    targetWindowXTitle: String(applet.targetWindowXTitle || ""),
    targetWindowXClass: String(applet.targetWindowXClass || "")
  }};
}})()
"""
    )


class LivePasteEditorTest(unittest.TestCase):
    def test_cinnamon_applet_pastes_clipboard_text_into_focused_xed_file(self) -> None:
        self._assert_live_paste_into_xed(simulate_menu_click=False)

    def test_cinnamon_applet_menu_click_path_pastes_into_previously_focused_xed_file(self) -> None:
        self._assert_live_paste_into_xed(simulate_menu_click=True)

    def test_cinnamon_applet_pastes_and_submits_into_focused_gnome_terminal(self) -> None:
        self._assert_live_paste_into_gnome_terminal(simulate_menu_click=False)

    def test_cinnamon_applet_menu_click_path_pastes_and_submits_into_focused_gnome_terminal(self) -> None:
        self._assert_live_paste_into_gnome_terminal(simulate_menu_click=True)

    def _assert_live_paste_into_gnome_terminal(self, *, simulate_menu_click: bool) -> None:
        _require_display()
        terminal = _require_tool("gnome-terminal")
        xdotool = _require_tool("xdotool")
        _require_cinnamon_applet()
        _require_clipboard_target_probe()

        paste_text = f"soc-terminal-paste-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="soc-live-terminal-paste-") as tmpdir:
            capture_path = Path(tmpdir, "captured.txt")
            title = f"SoC shell paste {uuid.uuid4().hex}"
            known_terminal_windows = set(_window_ids_for([xdotool, "search", "--onlyvisible", "--class", "Gnome-terminal"]))
            env = dict(os.environ)
            env["SOC_TERMINAL_CAPTURE_PATH"] = str(capture_path)
            terminal_proc = subprocess.Popen(
                [
                    terminal,
                    "--wait",
                    "--title",
                    title,
                    "--",
                    "bash",
                    "-lc",
                    'IFS= read -r line; printf "%s\\n" "$line" > "$SOC_TERMINAL_CAPTURE_PATH"; sleep 0.3',
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            window_id = ""
            try:
                window_id = _wait_for_terminal_window(title, terminal_proc.pid, known_terminal_windows)
                _activate_window(window_id)
                result = _trigger_applet_clipboard_paste(
                    paste_text,
                    simulate_menu_click=simulate_menu_click,
                    auto_paste_window_title="Terminal",
                )
                self.assertIsInstance(result, dict)
                self.assertTrue(result.get("ok"), result)
                self.assertTrue(result.get("remembered"), result)
                self.assertTrue(result.get("terminalTarget"), result)
                self.assertTrue(result.get("autoPasteEnter"), result)
                if simulate_menu_click:
                    self.assertTrue(result.get("menuActivated"), result)

                deadline = time.monotonic() + PASTE_TIMEOUT_SECONDS
                captured = ""
                while time.monotonic() < deadline:
                    if capture_path.exists():
                        captured = capture_path.read_text(encoding="utf-8", errors="replace").strip()
                        if captured == paste_text:
                            return
                    time.sleep(0.25)
                self.fail(
                    "terminal did not receive pasted text; "
                    f"simulate_menu_click={simulate_menu_click!r}, result={result!r}, captured={captured!r}"
                )
            finally:
                if window_id:
                    _close_owned_test_window(window_id, expected_title=title, expected_class="Gnome-terminal")
                terminal_proc.terminate()
                try:
                    terminal_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    terminal_proc.kill()

    def _assert_live_paste_into_xed(self, *, simulate_menu_click: bool) -> None:
        _require_display()
        xed = _require_tool("xed")
        xdotool = _require_tool("xdotool")
        _require_cinnamon_applet()
        _require_clipboard_target_probe()

        paste_text = f"soc-live-paste-{uuid.uuid4().hex}"
        with tempfile.TemporaryDirectory(prefix="soc-live-paste-") as tmpdir:
            target_path = Path(tmpdir, f"{paste_text}.txt")
            target_path.write_text("", encoding="utf-8")
            known_xed_windows = set(_window_ids_for([xdotool, "search", "--onlyvisible", "--class", "xed"]))
            editor = subprocess.Popen(
                [xed, "--standalone", "--new-window", "--wait", str(target_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            window_id = ""
            try:
                window_id = _wait_for_xed_window(target_path.name, editor.pid, known_xed_windows)
                _activate_window(window_id)
                result = _trigger_applet_clipboard_paste(paste_text, simulate_menu_click=simulate_menu_click)
                self.assertIsInstance(result, dict)
                self.assertTrue(result.get("ok"), result)
                self.assertTrue(result.get("remembered"), result)
                if simulate_menu_click:
                    self.assertTrue(result.get("menuActivated"), result)

                deadline = time.monotonic() + PASTE_TIMEOUT_SECONDS
                last_content = ""
                while time.monotonic() < deadline:
                    _activate_window(window_id)
                    _run([xdotool, "key", "--clearmodifiers", "ctrl+s"], timeout=3)
                    last_content = target_path.read_text(encoding="utf-8", errors="replace")
                    if paste_text in last_content:
                        return
                    time.sleep(0.25)
                self.fail(
                    "applet did not paste the expected text into xed; "
                    f"simulate_menu_click={simulate_menu_click!r}, "
                    f"result={result!r}, file_content={last_content!r}"
                )
            finally:
                try:
                    _cinnamon_eval(
                        f"""
(() => {{
  const A = imports.ui.appletManager;
  const applet = A.definitions
    .filter(d => d.real_uuid === {json.dumps(APPLET_UUID)} || d.uuid === {json.dumps(APPLET_UUID)})
    .map(d => d.applet)
    .filter(a => !!a)[0];
  if (applet && applet.menu && applet.menu.isOpen) {{
    applet.menu.close();
  }}
  return true;
}})()
"""
                    )
                except (AssertionError, subprocess.SubprocessError):
                    pass
                if window_id:
                    _close_owned_test_window(window_id, expected_title=target_path.name, expected_class="xed")
                editor.terminate()
                try:
                    editor.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    editor.kill()
