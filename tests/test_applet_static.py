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

    def test_applet_exposes_language_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.languageItem = new PopupMenu.PopupSubMenuMenuItem(_("Language: en"))', source)
        self.assertIn("_populateLanguageMenu: function()", source)
        self.assertIn('new PopupMenu.PopupMenuItem((current === primary ? "[x] " : "[ ] ") + _("Use primary: ") + primary)', source)
        self.assertIn('new PopupMenu.PopupMenuItem((current === secondary ? "[x] " : "[ ] ") + _("Use secondary: ") + secondary)', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Start primary: ") + primary', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Start secondary: ") + secondary', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Switch primary/secondary")', source)
        self.assertIn("selectPrimary.connect(\"activate\", () => this._setActiveLanguage(primary", source)
        self.assertIn("startSecondary.connect(\"activate\", () => this._startWithLanguage(secondary))", source)
        self.assertIn("this._populateLanguageMenu();", source)

    def test_applet_exposes_recorder_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(
            set(schema["recorder"]["options"].values()),
            {"auto", "pw-record", "parecord", "arecord"},
        )
        self.assertIn("const RECORDER_METHODS = [", source)
        self.assertIn('this.recorderItem = new PopupMenu.PopupSubMenuMenuItem(_("Recorder: Automatic"))', source)
        self.assertIn("_populateRecorderMenu: function()", source)
        self.assertIn("_normalizeRecorder: function(method)", source)
        self.assertIn("_recorderLabel: function(method)", source)
        self.assertIn("_selectRecorder: function(method)", source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "recorder", "recorder", this._onRecorderSettingsChanged, null)', source)
        self.assertIn('this.settings.setValue("recorder", this.recorder)', source)
        self.assertIn('this.recorderItem.label.text = _("Recorder: ") + this._recorderLabel(this._normalizeRecorder(this.recorder))', source)
        self.assertIn('this.lastMessage = _("Recorder for next recording: ") + label', source)

    def test_applet_exposes_recording_duration_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["max-seconds"]["min"], 5)
        self.assertEqual(schema["max-seconds"]["max"], 300)
        self.assertIn("const RECORDING_LIMIT_SECONDS = [", source)
        for seconds in ["15", "30", "60", "120", "300"]:
            self.assertIn(seconds, source)
        self.assertIn('this.recordingLimitItem = new PopupMenu.PopupSubMenuMenuItem(_("Duration: 30s"))', source)
        self.assertIn("_populateRecordingLimitMenu: function()", source)
        self.assertIn("_normalizeRecordingLimit: function(seconds)", source)
        self.assertIn("_selectRecordingLimit: function(seconds)", source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", this._onRecordingLimitSettingsChanged, null)', source)
        self.assertIn('this.settings.setValue("max-seconds", this.maxSeconds)', source)
        self.assertIn('"--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this.recordingLimitItem.label.text = _("Duration: ") + this._formatSeconds(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this.lastMessage = _("Duration for next recording: ") + label', source)

    def test_applet_exposes_shortcut_reference_menu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.shortcutItem = new PopupMenu.PopupSubMenuMenuItem(_("Keyboard shortcuts"))', source)
        self.assertIn("_populateShortcutMenu: function()", source)
        self.assertIn("_shortcutRows: function()", source)
        self.assertIn("_formatKeybinding: function(binding)", source)
        self.assertIn("_shortcutReferenceText: function()", source)
        self.assertIn("_copyShortcutReference: function()", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy shortcut reference"), "edit-copy-symbolic"', source)
        self.assertIn("this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._shortcutReferenceText())", source)
        self.assertIn('this._setStatus("done", _("Copied shortcut reference"), this.lastTranscript)', source)
        self.assertIn("this._populateShortcutMenu();", source)

    def test_recording_artifact_retention_is_optional(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        recording_keys = schema["layout"]["recording-section"]["keys"]
        self.assertIn("keep-recording-artifacts", recording_keys)
        self.assertFalse(schema["keep-recording-artifacts"]["default"])
        self.assertIn('["keep-recording-artifacts", "keepRecordingArtifacts"]', source)
        self.assertIn('args.push("--keep-recording-artifacts");', source)

    def test_applet_exposes_recording_options_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertTrue(schema["auto-transcribe-timeout"]["default"])
        self.assertFalse(schema["keep-recording-artifacts"]["default"])
        self.assertIn('this.recordingOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording options"))', source)
        self.assertIn("_populateRecordingOptionsMenu: function()", source)
        self.assertIn("_toggleAutoTranscribeTimeout: function()", source)
        self.assertIn("_toggleKeepRecordingArtifacts: function()", source)
        self.assertIn("_setRecordingOptionStatus: function(message)", source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "auto-transcribe-timeout", "autoTranscribeTimeout", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "keep-recording-artifacts", "keepRecordingArtifacts", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this.settings.setValue("auto-transcribe-timeout", this.autoTranscribeTimeout)', source)
        self.assertIn('this.settings.setValue("keep-recording-artifacts", this.keepRecordingArtifacts)', source)
        self.assertIn('_("Auto-transcribe at time limit")', source)
        self.assertIn('_("Keep recording files")', source)
        self.assertIn('this._populateRecordingOptionsMenu();', source)

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

    def test_applet_exposes_quick_output_method_menu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(
            set(schema["insert-method"]["options"].values()),
            {"clipboard-paste", "clipboard", "type", "none"},
        )
        self.assertIn('const OUTPUT_METHODS = [', source)
        self.assertIn('this.outputMethodItem = new PopupMenu.PopupSubMenuMenuItem(_("Output: Clipboard and paste"))', source)
        self.assertIn("this._populateOutputMethodMenu();", source)
        self.assertIn('_outputMethodLabel: function(method)', source)
        self.assertIn('_normalizeOutputMethod: function(method)', source)
        self.assertIn('_selectOutputMethod: function(method)', source)
        self.assertIn('this.settings.setValue("insert-method", this.insertMethod)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "insert-method", "insertMethod", this._onOutputSettingsChanged, null)', source)
        self.assertIn('this.outputMethodItem.label.text = _("Output: ") + this._outputMethodLabel(this._normalizeOutputMethod(this.insertMethod))', source)
        self.assertIn('"--insert-method", "none"', source)
        self.assertNotIn("_usesCinnamonClipboard", source)

    def test_applet_exposes_quick_text_output_options(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.textOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Text options"))', source)
        self.assertIn("_populateTextOptionsMenu: function()", source)
        self.assertIn("_toggleAppendSpace: function()", source)
        self.assertIn("_toggleSanitizeSpecialChars: function()", source)
        self.assertIn('this.settings.setValue("append-space", this.appendSpace)', source)
        self.assertIn('this.settings.setValue("sanitize-special-chars", this.sanitizeSpecialChars)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "append-space", "appendSpace", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "sanitize-special-chars", "sanitizeSpecialChars", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('_("Append trailing space")', source)
        self.assertIn('_("Replace accents before output")', source)

    def test_applet_can_reinsert_last_transcript_with_current_output_mode(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.insertLastItem = new PopupMenu.PopupIconMenuItem(_("Insert last transcript")', source)
        self.assertIn("this.insertLastItem.setSensitive(false)", source)
        self.assertIn("this.insertLastItem.connect(\"activate\", () => this._insertLastTranscript())", source)
        self.assertIn("this.insertLastItem.setSensitive(Boolean(this.lastTranscript))", source)
        self.assertIn("_insertLastTranscript: function()", source)
        self.assertIn("_insertTranscriptText: function(transcript)", source)
        self.assertIn("_finishAppletTextInsert: function(payload)", source)
        self.assertIn("this._insertTranscriptText(payload.transcript);", source)
        self.assertIn("if (payload.status === \"done\" && payload.transcript)", source)
        self.assertIn('if (method === "none")', source)
        self.assertIn('if (method === "type")', source)
        self.assertIn('this._typeTextAfterFocus(text);', source)
        self.assertIn('Util.spawn(args);', source)
        self.assertIn('["xdotool", "type", "--clearmodifiers", "--delay", String(delay), text]', source)
        self.assertIn('["xdotool", "key", "--clearmodifiers", "ctrl+v"]', source)

    def test_history_entries_can_be_copied_or_inserted(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("let entry = new PopupMenu.PopupSubMenuMenuItem(label)", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Insert transcript"), "edit-paste-symbolic"', source)
        self.assertIn("insertItem.connect(\"activate\", () => this._insertHistoryTranscript(transcript.text || \"\"))", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy transcript"), "edit-copy-symbolic"', source)
        self.assertIn("copyItem.connect(\"activate\", () => this._copyHistoryTranscript(transcript.text || \"\"))", source)
        self.assertIn("_insertHistoryTranscript: function(text)", source)
        self.assertIn("this._insertTranscriptText(text);", source)

    def test_voice_model_menu_can_return_to_automatic_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_("Automatic ASR backend")', source)
        self.assertIn("_selectAutomaticVoiceBackend: function()", source)
        self.assertIn('this.settings.setValue("transcriber", this.transcriber)', source)
        self.assertIn('this.settings.setValue("whisper-model", this.whisperModel)', source)
        self.assertIn('this._setStatus("ready", _("Voice backend: automatic"), this.lastTranscript)', source)
        self.assertIn("this._refreshModelMenu();", source)
