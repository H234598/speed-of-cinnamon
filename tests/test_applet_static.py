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
        self.assertIn('args.push("--openai-compatible-url", safeOpenAiCompatibleUrl)', source)
        self.assertIn('args.push("--openai-compatible-model", safeOpenAiCompatibleModel)', source)
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

        self.assertIn('this.activeLanguage = "";', source)
        self.assertIn("this.activeLanguageExplicit = false;", source)
        self.assertIn('this.languageItem = new PopupMenu.PopupSubMenuMenuItem(_("Language: en"))', source)
        self.assertIn("_populateLanguageMenu: function()", source)
        self.assertIn('new PopupMenu.PopupMenuItem((current === primary ? "[x] " : "[ ] ") + _("Use primary: ") + primary)', source)
        self.assertIn('new PopupMenu.PopupMenuItem((current === secondary ? "[x] " : "[ ] ") + _("Use secondary: ") + secondary)', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Start primary: ") + primary', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Start secondary: ") + secondary', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Switch primary/secondary")', source)
        self.assertIn("selectPrimary.connect(\"activate\", () => this._setActiveLanguage(primary", source)
        self.assertIn("startSecondary.connect(\"activate\", () => this._startWithLanguage(secondary))", source)
        self.assertIn("if (!this.activeLanguageExplicit || (current !== primary && current !== secondary))", source)
        self.assertIn("this.activeLanguageExplicit = false;", source)
        self.assertIn("this.activeLanguageExplicit = true;", source)
        self.assertIn('status === "recording" || status === "recorded" || status === "processing"', source)
        self.assertIn("this._populateLanguageMenu();", source)

    def test_language_settings_offer_broader_whisper_codes(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))
        primary = schema["language"]["options"]
        secondary = schema["secondary-language"]["options"]

        self.assertEqual(primary, secondary)
        for label, code in {
            "Arabic": "ar",
            "Chinese": "zh",
            "Czech": "cs",
            "Danish": "da",
            "English": "en",
            "Finnish": "fi",
            "German": "de",
            "Greek": "el",
            "Hindi": "hi",
            "Japanese": "ja",
            "Korean": "ko",
            "Portuguese": "pt",
            "Russian": "ru",
            "Spanish": "es",
            "Turkish": "tr",
            "Ukrainian": "uk",
        }.items():
            self.assertEqual(primary[label], code)

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

        self.assertEqual(schema["max-seconds"]["min"], 0)
        self.assertEqual(schema["max-seconds"]["max"], 3600)
        self.assertIn("const RECORDING_LIMIT_SECONDS = [", source)
        for seconds in ["15", "30", "60", "120", "300", "600", "900", "1200", "1800", "3600"]:
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

    def test_typing_delay_has_backend_limits(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["typing-delay-ms"]["min"], 0)
        self.assertEqual(schema["typing-delay-ms"]["max"], 10000)
        self.assertIn("_normalizeTypingDelayMs: function(delay)", source)
        self.assertIn("_normalizeTypingDelayMs(this.typingDelayMs)", source)
        self.assertIn('"--typing-delay-ms", String(this._normalizeTypingDelayMs(this.typingDelayMs))', source)
        self.assertIn('_typeTextAfterFocus: function(text) {', source)

    def test_recording_progress_path_uses_recording_limit_normalizer(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.recordingMaxSeconds = this._normalizeRecordingLimit(this.maxSeconds);', source)
        self.assertIn(
            'this.recordingMaxSeconds !== undefined && this.recordingMaxSeconds !== null ? this.recordingMaxSeconds : this.maxSeconds',
            source,
        )
        self.assertIn("_recordingProgressText: function() {", source)

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

    def test_applet_exposes_notification_options_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertFalse(schema["notify-recording"]["default"])
        self.assertTrue(schema["notify-complete"]["default"])
        self.assertTrue(schema["notify-error"]["default"])
        self.assertIn('this.notificationOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Notifications"))', source)
        self.assertIn("_populateNotificationOptionsMenu: function()", source)
        self.assertIn("_toggleNotifyRecording: function()", source)
        self.assertIn("_toggleNotifyComplete: function()", source)
        self.assertIn("_toggleNotifyError: function()", source)
        self.assertIn("_setNotificationOptionStatus: function(message)", source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "notify-recording", "notifyRecording", this._onNotificationSettingsChanged, null)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "notify-complete", "notifyComplete", this._onNotificationSettingsChanged, null)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "notify-error", "notifyError", this._onNotificationSettingsChanged, null)', source)
        self.assertIn('this.settings.setValue("notify-recording", this.notifyRecording)', source)
        self.assertIn('this.settings.setValue("notify-complete", this.notifyComplete)', source)
        self.assertIn('this.settings.setValue("notify-error", this.notifyError)', source)
        self.assertIn('_("Recording start and limit")', source)
        self.assertIn('_("Dictation complete")', source)
        self.assertIn('_("Dictation errors")', source)

    def test_applet_exposes_cinnamon_alarm_menu_and_timer(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const ALARM_CHECK_SECONDS = 60;", source)
        self.assertIn('this.alarmItem = new PopupMenu.PopupSubMenuMenuItem(_("Alarms"))', source)
        self.assertIn("_refreshAlarmMenu: function()", source)
        self.assertIn("_populateAlarmMenu: function(alarms, summary, message)", source)
        self.assertIn("_addAlarmMenuEntry: function(alarm)", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Check alarms now"), "view-refresh-symbolic"', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy alarm commands"), "edit-copy-symbolic"', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Open alarm store"), "folder-symbolic"', source)
        self.assertIn('return [this._cliCommand(), "alarms", "list", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "alarms", "check", "--mark", "--json"];', source)
        self.assertIn("_scheduleAlarmCheck(5)", source)
        self.assertIn("_clearAlarmTimer()", source)
        self.assertIn("this._notify(_(\"Speed of Cinnamon alarm\")", source)

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

    def test_status_refresh_deduplicates_overlapping_cli_calls(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this._statusCommandRunning = false;", source)
        self.assertIn("_refreshStatus: function() {", source)
        self.assertIn("if (this._statusCommandRunning) {", source)
        self.assertIn("this._statusCommandRunning = true;", source)
        self.assertIn("try {", source)
        self.assertIn("} finally {", source)
        self.assertIn("this._statusCommandRunning = false;", source)

    def test_status_checks_use_spawn_json_timeout(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const STATUS_COMMAND_TIMEOUT_MS = 10000;", source)
        self.assertIn("_refreshStatus: function() {", source)
        self.assertIn("}, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS });", source)
        self.assertIn("_spawnJson: function(args, callback, options) {", source)
        self.assertIn("timeoutMs = Number(options.timeoutMs || CLI_COMMAND_TIMEOUT_MS);", source)
        self.assertIn("if (timeoutMs > 0) {", source)

    def test_doctor_checks_use_spawn_json_timeout(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const DOCTOR_COMMAND_TIMEOUT_MS = 20000;", source)
        self.assertIn("_runDoctor: function(startupCheck) {", source)
        self.assertIn("}, { timeoutMs: DOCTOR_COMMAND_TIMEOUT_MS });", source)
        self.assertIn('this.doctorSummaryItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Doctor: not checked"))', source)
        self.assertIn('this.diagnosticsMenuItem.menu.addMenuItem(this.doctorSummaryItem)', source)
        self.assertIn('_setDoctorSummary(_("Doctor: checking..."))', source)
        self.assertIn("_presentDoctorResult: function(message, critical, startupCheck)", source)
        self.assertIn('this._notify(_("Speed of Cinnamon doctor")', source)
        self.assertIn("_doctorSummary: function(payload)", source)
        self.assertIn('this.doctorSummaryItem.label.text = this.doctorSummaryText || _("Doctor: not checked")', source)

    def test_recording_status_shows_microphone_level(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.microphoneLevelItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Microphone: idle"))', source)
        self.assertIn("this._applyMicrophoneLevel(payload.microphone_level, status);", source)
        self.assertIn("_microphoneLevelText: function()", source)
        self.assertIn("_levelBar: function(percent)", source)
        self.assertIn('this.microphoneLevelItem.label.text = this._microphoneLevelText();', source)

    def test_left_click_menu_uses_compact_top_level_groups(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.recordingMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording"))', source)
        self.assertIn('this.textOutputMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Text and output"))', source)
        self.assertIn('this.transcriptsMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Transcripts"))', source)
        self.assertIn('this.toolsMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Tools"))', source)
        self.assertIn("this.recordingMenuItem.menu.addMenuItem(this.recorderItem);", source)
        self.assertIn("this.recordingMenuItem.menu.addMenuItem(this.inputSourceItem);", source)
        self.assertIn("this.recordingMenuItem.menu.addMenuItem(this.modelItem);", source)
        self.assertIn("this.textOutputMenuItem.menu.addMenuItem(this.outputMethodItem);", source)
        self.assertIn("this.textOutputMenuItem.menu.addMenuItem(this.textModelItem);", source)
        self.assertIn("this.transcriptsMenuItem.menu.addMenuItem(this.historyItem);", source)
        self.assertIn("this.toolsMenuItem.menu.addMenuItem(this.alarmItem);", source)
        self.assertIn("this.maintenanceMenuItem.menu.addMenuItem(exportSettings);", source)

    def test_large_selection_menus_get_more_width_and_trim_long_rows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const Pango = imports.gi.Pango;", source)
        self.assertIn("const MENU_MIN_WIDTH_EM = 30;", source)
        self.assertIn("const MENU_LABEL_WIDTH_EM = 32;", source)
        self.assertIn("const SELECTION_MENU_MIN_WIDTH_EM = 42;", source)
        self.assertIn("_styleWideMenus: function()", source)
        self.assertIn("this._styleSelectionSubmenu(this.recordingMenuItem);", source)
        self.assertIn("this._styleSelectionSubmenu(this.toolsMenuItem);", source)
        self.assertIn("this._styleSelectionSubmenu(this.inputSourceItem);", source)
        self.assertIn("this._styleSelectionSubmenu(this.modelItem);", source)
        self.assertIn("this._styleSelectionSubmenu(this.textModelItem);", source)
        self.assertIn("_selectionMenuItem: function(label)", source)
        self.assertIn("_shortMenuText: function(value, maxChars)", source)
        self.assertIn("item.label.clutter_text.ellipsize = options.wrap ? Pango.EllipsizeMode.NONE : Pango.EllipsizeMode.END", source)

    def test_applet_adds_frontend_validation_for_long_or_invalid_text_fields(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const CLI_TEXT_SETTINGS = {", source)
        self.assertIn("const MAX_SETTING_TEXT_CHARS = 4096;", source)
        self.assertIn("_coerceCliTextArg: function(value, fieldName)", source)
        self.assertIn('"personal-context": "personal context"', source)
        self.assertIn('"vocabulary": "vocabulary"', source)
        self.assertIn("let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this.openaiCompatibleUrl, \"openai-compatible URL\")", source)
        self.assertIn("safePersonalContext = this._coerceCliTextArg(this.personalContext, \"personal context\")", source)
        self.assertIn("safeVocabulary = this._coerceCliTextArg(this.vocabulary, \"vocabulary\")", source)
        self.assertIn("for (let key in CLI_TEXT_SETTINGS)", source)
        self.assertIn("let safeOllamaUrl = this._coerceCliTextArg(this.ollamaUrl, \"ollama URL\")", source)
        self.assertIn("let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this.openaiCompatibleUrl, \"openai-compatible URL\")", source)
        self.assertIn("_coerceImportedSetting: function(key, value, fallback)", source)

    def test_applet_settings_schema_mentions_frontend_limits(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("Max 4096 chars", schema["personal-context"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["vocabulary"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["transcriber-command"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["post-process-command"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["post-process-prompt"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["input-device"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["whisper-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["ollama-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["ollama-url"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-url"]["tooltip"])

    def test_starter_voice_model_matches_current_language(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_starterVoiceModelName: function()", source)
        self.assertIn("_voiceModelLanguage: function()", source)
        self.assertIn("return this._primaryLanguage();", source)
        self.assertIn('return this._isEnglishLanguage(this._voiceModelLanguage()) ? "tiny.en" : "tiny";', source)
        self.assertIn("_ensureVoiceModelCompatibleWithPrimaryLanguage(false);", source)
        self.assertIn("_ensureVoiceModelCompatibleWithCurrentLanguage(true)", source)
        self.assertIn('_("Active: ") + this._currentLanguage() + _(", primary: ") + this._voiceModelLanguage()', source)
        self.assertIn('String(model || this._starterVoiceModelName())', source)
        self.assertIn("_voiceModelSupportsCurrentLanguage: function(model)", source)
        self.assertIn("English-only model cannot transcribe primary language", source)

    def test_applet_exposes_restart_button(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const Extension = imports.ui.extension;", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Restart applet"), "view-refresh-symbolic"', source)
        self.assertIn("restartApplet.connect(\"activate\", () => this._restartApplet())", source)
        self.assertIn("_restartApplet: function()", source)
        self.assertIn("Extension.reloadExtension(UUID, Extension.Type.APPLET)", source)

    def test_cli_command_expands_home_directory_shortcut(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("configured.indexOf(\"~/\") === 0", source)
        self.assertIn("GLib.build_filenamev([GLib.get_home_dir(), configured.substring(2)]);", source)

    def test_spawn_json_hardens_arguments_and_output(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const MAX_CLI_ARG_BYTES = 4096;", source)
        self.assertIn("const MAX_CLI_ARG_COUNT = 128;", source)
        self.assertIn("const MAX_CLI_COMMAND_BYTES = 32768;", source)
        self.assertIn("const MAX_TEXT_INSERT_CHARS = 120000;", source)
        self.assertIn("const MAX_TYPE_COMMAND_CHARS = 4000;", source)
        self.assertIn("const MAX_SPAWN_JSON_BYTES = 262144;", source)
        self.assertIn("const CLI_COMMAND_TIMEOUT_MS = 300000;", source)
        self.assertIn("_coerceSpawnArgs: function(args) {", source)
        self.assertIn("if (!Array.isArray(args)) {", source)
        self.assertIn("if (args[i] === null || args[i] === undefined) {", source)
        self.assertIn('throw new Error("Backend command argument is missing");', source)
        self.assertIn("if (i === 0) {", source)
        self.assertIn("value = value.trim();", source)
        self.assertIn("if (value.indexOf(\"\\u0000\") >= 0) {", source)
        self.assertIn("if (value.length > MAX_CLI_ARG_BYTES) {", source)
        self.assertIn("let totalBytes = 0;", source)
        self.assertIn("totalBytes += value.length;", source)
        self.assertIn("if (totalBytes > MAX_CLI_COMMAND_BYTES) {", source)
        self.assertIn("if (String(args[0] || \"\").trim() === \"\") {", source)
        self.assertIn("_isAllowedCliCommand: function(command) {", source)
        self.assertIn("_parseSpawnOutput: function(stdout) {", source)
        self.assertIn("if (output.length > MAX_SPAWN_JSON_BYTES) {", source)
        self.assertIn("if (!parsed || typeof parsed !== \"object\" || Array.isArray(parsed)) {", source)
        self.assertIn("let callbackFn = typeof callback === \"function\" ? callback : function() {};", source)
        self.assertIn("let done = false;", source)
        self.assertIn("if (done) {", source)
        self.assertIn("callbackFn(payload || {});", source)
        self.assertIn("if (args.length > MAX_CLI_ARG_COUNT) {", source)
        self.assertIn("Mainloop.timeout_add(Math.max(250, timeoutMs)", source)
        self.assertIn("finalize({ status: \"error\", error: \"Backend command timed out\" });", source)

    def test_text_output_is_hardened_before_keyboard_typing(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_coerceTypeText: function(text) {", source)
        self.assertIn('if (value.indexOf("\\u0000") >= 0) {', source)
        self.assertIn("value = value.replace(/\\u0000/g, \"\");", source)
        self.assertIn('if (value.length > MAX_TYPE_COMMAND_CHARS) {', source)
        self.assertIn("Text too long for keyboard typing", source)
        self.assertIn("_spawnKeyboardAfterFocus: function(args) {", source)
        self.assertIn("Util.spawn(this._coerceSpawnArgs(args));", source)

    def test_doctor_check_deduplicates_overlapping_cli_calls(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this._doctorCommandRunning = false;", source)
        self.assertIn("_runDoctor: function(startupCheck) {", source)
        self.assertIn("if (this._doctorCommandRunning) {", source)
        self.assertIn("this._doctorCommandRunning = true;", source)
        self.assertIn("} finally {", source)
        self.assertIn("this._doctorCommandRunning = false;", source)

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

    def test_applet_copies_setup_commands_without_installing_packages(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy setup commands"), "utilities-terminal-symbolic"', source)
        self.assertIn("_setupCommandsText: function(payload)", source)
        self.assertIn("_copySetupCommands: function()", source)
        self.assertIn("let commands = payload.commands || [];", source)
        self.assertIn("if (!Array.isArray(commands))", source)
        self.assertIn("lines.join(\"\\n\")", source)
        self.assertIn("this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text)", source)
        self.assertIn('this._setStatus("ready", _("No setup commands needed"), this.lastTranscript)', source)
        self.assertIn('this._setStatus("done", _("Copied setup commands"), this.lastTranscript)', source)

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
        self.assertIn("if (this._typeTextAfterFocus(text)) {", source)
        self.assertIn("Util.spawn(this._coerceSpawnArgs(args));", source)
        self.assertIn('["xdotool", "type", "--clearmodifiers", "--delay", String(delay), typedText]', source)
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

    def test_cleanup_can_be_previewed_before_deleting_files(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Preview cleanup"), "edit-find-symbolic"', source)
        self.assertIn("cleanupPreview.connect(\"activate\", () => this._previewCleanup())", source)
        self.assertIn("_cleanupPreviewArgs: function()", source)
        self.assertIn('"--dry-run", "--json"', source)
        self.assertIn("_cleanupCount: function(payload, dryRun)", source)
        self.assertIn("payload.would_delete_transcripts", source)
        self.assertIn("_previewCleanup: function()", source)
        self.assertIn('this._setStatus("processing", _("Previewing cleanup..."), this.lastTranscript)', source)
        self.assertIn('this._setStatus("ready", _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), this.lastTranscript)', source)
        self.assertIn("let deleted = this._cleanupCount(payload, false);", source)

    def test_voice_model_menu_can_return_to_automatic_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_("Automatic ASR backend")', source)
        self.assertIn("_selectAutomaticVoiceBackend: function()", source)
        self.assertIn('this.settings.setValue("transcriber", this.transcriber)', source)
        self.assertIn('this.settings.setValue("whisper-model", this.whisperModel)', source)
        self.assertIn('this._setStatus("ready", _("Voice backend: automatic"), this.lastTranscript)', source)
        self.assertIn("this._refreshModelMenu();", source)
