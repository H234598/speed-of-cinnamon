from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLET_DIR = REPO_ROOT / "files" / "speed-of-cinnamon@H234598"


class AppletStaticTest(unittest.TestCase):
    def test_applet_auto_detects_user_and_system_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('const SYSTEM_CLI = "/usr/bin/speed-of-cinnamon";', source)
        self.assertIn("GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE)", source)
        self.assertIn("GLib.file_test(SYSTEM_CLI, GLib.FileTest.IS_EXECUTABLE)", source)
        self.assertIn('return "speed-of-cinnamon";', source)
        self.assertNotIn("this.cliPath || DEFAULT_CLI", source)

    def test_backend_setting_documents_auto_detection_order(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))
        tooltip = schema["cli-path"]["tooltip"]

        self.assertIn("~/.local/bin/speed-of-cinnamon", tooltip)
        self.assertIn("/usr/bin/speed-of-cinnamon", tooltip)

    def test_text_polishing_supports_openai_compatible_local_server(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["post-process-backend"]["options"]["OpenAI-compatible local server"], "openai-compatible")
        self.assertEqual(schema["openai-compatible-url"]["default"], "http://127.0.0.1:8000/v1")
        self.assertIn('["openai-compatible-url", "openaiCompatibleUrl"]', source)
        self.assertIn('args.push("--backend", "openai-compatible")', source)
        self.assertIn('args.push("--openai-compatible-url", this.openaiCompatibleUrl)', source)
        self.assertIn('args.push("--openai-compatible-model", this.openaiCompatibleModel)', source)
        self.assertIn('_selectTextModelBackend("openai-compatible"', source)

    def test_applet_registers_optional_language_specific_hotkeys(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        activation_keys = schema["layout"]["activation-section"]["keys"]
        self.assertIn("primary-language-keybinding", activation_keys)
        self.assertIn("secondary-language-keybinding", activation_keys)
        self.assertEqual(schema["primary-language-keybinding"]["default"], "")
        self.assertEqual(schema["secondary-language-keybinding"]["default"], "")
        self.assertIn('const PRIMARY_HOTKEY_ID = "speed-of-cinnamon-primary-language";', source)
        self.assertIn('const SECONDARY_HOTKEY_ID = "speed-of-cinnamon-secondary-language";', source)
        self.assertIn('["primary-language-keybinding", "primaryLanguageKeybinding"]', source)
        self.assertIn('this._registerHotkey(PRIMARY_HOTKEY_ID, this.primaryLanguageKeybinding', source)
        self.assertIn('this._registerHotkey(SECONDARY_HOTKEY_ID, this.secondaryLanguageKeybinding', source)
        self.assertIn('this._startWithLanguage(this._primaryLanguage())', source)
        self.assertIn('this._startWithLanguage(this._secondaryLanguage())', source)
        self.assertIn('this._hasActiveRecordingState()', source)

    def test_recording_artifact_retention_is_optional(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        recording_keys = schema["layout"]["recording-section"]["keys"]
        self.assertIn("keep-recording-artifacts", recording_keys)
        self.assertFalse(schema["keep-recording-artifacts"]["default"])
        self.assertIn('["keep-recording-artifacts", "keepRecordingArtifacts"]', source)
        self.assertIn('args.push("--keep-recording-artifacts");', source)

    def test_panel_status_style_classes_are_applied(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        stylesheet = (APPLET_DIR / "stylesheet.css").read_text(encoding="utf-8")

        for status_class in [
            "speed-of-cinnamon-recording",
            "speed-of-cinnamon-processing",
            "speed-of-cinnamon-recorded",
            "speed-of-cinnamon-ready",
            "speed-of-cinnamon-setup",
            "speed-of-cinnamon-error",
        ]:
            self.assertIn(status_class, source)
            self.assertIn(f".{status_class}", stylesheet)
        self.assertIn("const PANEL_STATUS_CLASSES = [", source)
        self.assertIn("this.actor.remove_style_class_name(styleClass)", source)
        self.assertIn("this.actor.add_style_class_name(this._panelStyleClassForStatus(status))", source)
        self.assertIn("this._applyPanelStyle(this.status)", source)

    def test_applet_restores_target_window_before_clipboard_paste(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const PASTE_FOCUS_DELAY_MS = 120;", source)
        self.assertIn("this.targetWindow = null;", source)
        self.assertIn("this._rememberFocusedWindow();", source)
        self.assertIn("global.display ? global.display.focus_window : null", source)
        self.assertIn("window.is_skip_taskbar && window.is_skip_taskbar()", source)
        self.assertIn("Main.activateWindow(this.targetWindow, global.get_current_time())", source)
        self.assertIn("this._restoreTargetWindowForPaste()", source)
        self.assertIn("this._pasteClipboardAfterFocus();", source)
        self.assertIn("Copied and pasted into target window", source)

    def test_applet_uses_gio_for_desktop_links_and_folders(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const Gio = imports.gi.Gio;", source)
        self.assertIn("Gio.AppInfo.launch_default_for_uri(uri, null)", source)
        self.assertIn("GLib.filename_to_uri(path, null)", source)
        self.assertIn("GLib.mkdir_with_parents(path, 0o755)", source)
        self.assertIn("GLib.file_test(path, GLib.FileTest.IS_DIR)", source)
        self.assertIn('this._openUri(RUNBOOK_URL, _("Opened setup guide"))', source)
        self.assertIn('this._openFolder(GLib.build_filenamev([GLib.get_user_state_dir(), "speed-of-cinnamon", "transcripts"])', source)
        self.assertIn('this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "whisper.cpp"])', source)
        self.assertNotIn('Util.spawn(["xdg-open"', source)
