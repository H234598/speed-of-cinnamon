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

    def test_text_polishing_supports_openai_compatible_api(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["post-process-backend"]["options"]["OpenAI-compatible API"], "openai-compatible")
        self.assertNotIn("OpenAI-compatible local server", schema["post-process-backend"]["options"])
        self.assertEqual(schema["post-process-backend"]["default"], "none")
        self.assertEqual(schema["openai-compatible-url"]["default"], "https://api.openai.com/v1")
        self.assertEqual(schema["openai-compatible-model"]["default"], "gpt-4o-transcribe")
        self.assertEqual(schema["openai-compatible-text-model"]["default"], "gpt-4o-mini")
        self.assertTrue(schema["openai-compatible-flex-processing"]["default"])
        self.assertIn("openai-compatible-flex-processing", schema["layout"]["backend-section"]["keys"])
        self.assertIn('this.postProcessBackend = "none"', source)
        self.assertIn('["openai-compatible-url", "openaiCompatibleUrl"]', source)
        self.assertIn('["openai-compatible-text-model", "openaiCompatibleTextModel"]', source)
        self.assertIn('["openai-compatible-flex-processing", "openaiCompatibleFlexProcessing"]', source)
        self.assertIn('args.push("--backend", "openai-compatible")', source)
        self.assertIn('args.push("--openai-compatible-url", safeOpenAiCompatibleUrl)', source)
        self.assertIn('args.push("--openai-compatible-model", safeOpenAiCompatibleModel)', source)
        self.assertIn('args.push("--openai-compatible-text-model", safeOpenAiCompatibleTextModel)', source)
        self.assertIn('args.push("--no-openai-compatible-flex-processing")', source)
        self.assertIn('this.openAiFlexProcessingItem = new PopupMenu.PopupMenuItem("")', source)
        self.assertIn('_("OpenAI Flex processing")', source)
        self.assertIn('this.settings.setValue("openai-compatible-flex-processing", this.openaiCompatibleFlexProcessing);', source)
        self.assertIn('openaiCompatible.connect("activate", () => this._openExternalApiEnvEditor("text"));', source)
        self.assertIn('this.postProcessBackend = "openai-compatible";', source)
        self.assertIn('this.settings.setValue("post-process-backend", this.postProcessBackend);', source)
        self.assertIn('this._setStatus("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);', source)
        self.assertIn("_refreshTextModelMenuForBackend: function(backendOverride)", source)
        self.assertIn('this._refreshTextModelMenuForBackend("openai-compatible");', source)
        self.assertIn('_("Loading OpenAI-compatible text models...")', source)
        self.assertNotIn('_selectTextModelBackend("openai-compatible"', source)
        self.assertIn('_("OpenAI-compatible API")', source)
        self.assertNotIn("Text polishing: OpenAI-compatible local server", source)

    def test_voice_model_menu_supports_external_openai_compatible_api(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["transcriber"]["options"]["OpenAI-compatible external API"], "openai-compatible")
        self.assertIn("openai-compatible-api-key", schema["layout"]["backend-section"]["keys"])
        self.assertIn('this.openaiCompatibleApiKey = "";', source)
        self.assertIn('"openai-compatible-api-key": "openai-compatible API key"', source)
        self.assertIn('"openai-compatible-api-key", "openaiCompatibleApiKey"', source)
        self.assertIn('args.push("--openai-compatible-api-key", safeOpenAiCompatibleApiKey)', source)
        self.assertIn('let externalMenu = new PopupMenu.PopupSubMenuMenuItem(_("External API"));', source)
        self.assertIn("this._populateExternalApiVoiceMenu(externalMenu.menu);", source)
        self.assertIn("_populateExternalApiVoiceMenu: function(parentMenu)", source)
        self.assertIn("_selectExternalApiVoiceBackend: function()", source)
        self.assertIn('this.transcriber = "openai-compatible";', source)
        self.assertIn('return _("Voice: External API ") + (String(this.openaiCompatibleModel || "").trim() || _("not configured"));', source)

    def test_external_api_voice_menu_opens_env_file_for_configuration(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('const DEFAULT_OPENAI_COMPATIBLE_URL = "https://api.openai.com/v1";', source)
        self.assertIn('const DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-4o-transcribe";', source)
        self.assertIn('const DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL = "gpt-4o-mini";', source)
        self.assertIn('const LEGACY_OPENAI_COMPATIBLE_URL = "http://127.0.0.1:8000/v1";', source)
        self.assertIn("this.openaiCompatibleUrl = DEFAULT_OPENAI_COMPATIBLE_URL;", source)
        self.assertIn("this.openaiCompatibleModel = DEFAULT_OPENAI_COMPATIBLE_MODEL;", source)
        self.assertIn("this.openaiCompatibleTextModel = DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL;", source)
        self.assertIn("this._syncExternalApiConfigOnStartup();", source)
        self.assertIn("_syncExternalApiConfigOnStartup: function()", source)
        self.assertIn('this.settings.setValue("openai-compatible-url", this.openaiCompatibleUrl);', source)
        self.assertIn("_externalApiEnvPath: function()", source)
        self.assertIn('"external-api.env"', source)
        self.assertIn("OPENAI_COMPATIBLE_URL=", source)
        self.assertIn("OPENAI_COMPATIBLE_STT_MODEL=", source)
        self.assertIn("OPENAI_COMPATIBLE_TEXT_MODEL=", source)
        self.assertIn("OPENAI_COMPATIBLE_API_KEY=", source)
        self.assertIn("_writeExternalApiEnvFile: function()", source)
        self.assertIn("GLib.file_set_contents(path, this._externalApiEnvContent());", source)
        self.assertIn("_migrateExternalApiEnvFile: function(path)", source)
        self.assertIn('"OPENAI_COMPATIBLE_URL=" + LEGACY_OPENAI_COMPATIBLE_URL', source)
        self.assertIn('migrated.replace("OPENAI_COMPATIBLE_MODEL=", "OPENAI_COMPATIBLE_STT_MODEL=")', source)
        self.assertIn('migrated.indexOf("OPENAI_COMPATIBLE_TEXT_MODEL=") < 0', source)
        self.assertIn("values.OPENAI_COMPATIBLE_STT_MODEL || values.OPENAI_COMPATIBLE_MODEL", source)
        self.assertIn("this.externalApiEnvApplyTarget = \"voice\";", source)
        self.assertIn("_openExternalApiEnvEditor: function(target)", source)
        self.assertIn("_applyExternalApiEnvTarget: function(target)", source)
        self.assertIn('useItem.connect("activate", () => this._openExternalApiEnvEditor("voice"));', source)
        self.assertIn('openaiCompatible.connect("activate", () => this._openExternalApiEnvEditor("text"));', source)
        self.assertIn('this._setStatus("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);', source)
        self.assertIn('this._refreshTextModelMenuForBackend("openai-compatible");', source)
        self.assertIn("this._writeExternalApiEnvFile();", source)
        self.assertIn("this._selectExternalApiVoiceBackend();", source)
        self.assertNotIn('this._selectTextModelBackend("openai-compatible", this.openaiCompatibleModel, _("Text polishing: OpenAI-compatible API"));', source)
        self.assertIn("_watchExternalApiEnvFile: function(path)", source)
        self.assertIn("Gio.FileMonitorEvent.CHANGES_DONE_HINT", source)
        self.assertIn("_applyExternalApiEnvFile: function(showStatus)", source)
        self.assertIn("ByteArray.toString(contents)", source)

    def test_error_status_displays_backend_error_message(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('statusText = "error";', source)
        self.assertIn('statusText += " - " + this._shortMenuText(this.lastMessage, 140);', source)

    def test_applet_registers_optional_language_specific_hotkeys(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("on_applet_clicked: function()", source)
        self.assertIn("if (!this.menu.isOpen) {", source)
        self.assertIn("this._rememberFocusedWindow();", source)
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
        self.assertIn('new PopupMenu.PopupIconMenuItem((hasPreset ? "[ ] " : "[x] ") + _("Custom seconds...")', source)
        self.assertIn('custom.connect("activate", () => this._promptCustomRecordingLimit())', source)
        self.assertIn("_customRecordingLimitPromptArgs: function()", source)
        self.assertIn('"--title=Duration"', source)
        self.assertIn('"--entry-text=" + current', source)
        self.assertIn("_parseCustomRecordingLimit: function(value)", source)
        self.assertIn('this.lastMessage = _("Duration must be whole seconds.")', source)
        self.assertIn('this.lastMessage = _("Duration must be between 0 and 3600 seconds.")', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", this._onRecordingLimitSettingsChanged, null)', source)
        self.assertIn('this.settings.setValue("max-seconds", this.maxSeconds)', source)
        self.assertIn('"--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this.recordingLimitItem.label.text = _("Duration: ") + this._formatSeconds(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this.lastMessage = _("Duration for next recording: ") + label', source)


    def test_applet_exposes_auto_paste_title_marker(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("auto-paste-window-title", schema["layout"]["output-section"]["keys"])
        self.assertEqual(schema["auto-paste-window-title"]["default"], "codex")
        self.assertIn('const DEFAULT_AUTO_PASTE_TITLE = "codex";', source)
        self.assertIn("const AUTO_PASTE_TITLE_PRESETS = [", source)
        self.assertIn('"Terminal"', source)
        self.assertIn('"PDF"', source)
        self.assertIn('"Excel"', source)
        self.assertIn('this.autoPasteWindowTitle = DEFAULT_AUTO_PASTE_TITLE;', source)
        self.assertIn('["auto-paste-window-title", "autoPasteWindowTitle"]', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "auto-paste-window-title", "autoPasteWindowTitle", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('this.autoPasteItem = new PopupMenu.PopupSubMenuMenuItem(_("Auto-Paste: codex"))', source)
        self.assertIn('_populateAutoPasteMenu: function()', source)
        self.assertIn('for (let preset of AUTO_PASTE_TITLE_PRESETS)', source)
        self.assertIn('disabled.connect("activate", () => this._setAutoPasteTitles([]))', source)
        self.assertIn('let custom = new PopupMenu.PopupIconMenuItem(_("Custom string...")', source)
        self.assertIn("_autoPastePromptArgs: function()", source)
        self.assertIn('"--entry"', source)
        self.assertIn('"--title=Auto-Paste"', source)
        self.assertIn('"--entry-text=" + current', source)
        self.assertIn('if (!GLib.find_program_in_path("zenity"))', source)
        self.assertIn('this._spawnText(this._autoPastePromptArgs(), (output) => {', source)
        self.assertIn('this._setAutoPasteTitles(this._autoPasteTitleValues(output));', source)
        self.assertIn('_autoPasteTitleValues: function(value)', source)
        self.assertIn('raw.split(/[,\\n\\r]+/)', source)
        self.assertIn('_normalizeAutoPasteTitle: function(value)', source)
        self.assertIn('_toggleAutoPasteTitle: function(value)', source)
        self.assertIn('this._setAutoPasteTitles([])', source)
        self.assertIn('_windowTitleMatchesAutoPaste: function()', source)
        self.assertIn('this._windowProbeValue(this.targetWindow, "get_title")', source)
        self.assertIn('for (let marker of markers)', source)
        self.assertIn('marker.toLowerCase()', source)
        self.assertIn('submitWithReturn', source)
        self.assertIn('String(this._windowProbeValue(this.targetWindow, "get_title") || "").toLowerCase()', source)
        self.assertIn('this._pasteClipboardAfterFocus(submitWithReturn)', source)
        self.assertIn('this._preparedTranscriptText(transcript, submitWithReturn)', source)

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
        self.assertFalse(schema["auto-relisten"]["default"])
        self.assertFalse(schema["keep-recording-artifacts"]["default"])
        self.assertIn('this.recordingOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording options"))', source)
        self.assertIn("_populateRecordingOptionsMenu: function()", source)
        self.assertIn("_toggleAutoTranscribeTimeout: function()", source)
        self.assertIn("_toggleAutoRelisten: function()", source)
        self.assertIn("_toggleKeepRecordingArtifacts: function()", source)
        self.assertIn("_setRecordingOptionStatus: function(message)", source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "auto-transcribe-timeout", "autoTranscribeTimeout", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "auto-relisten", "autoRelisten", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this.settings.bindProperty(Settings.BindingDirection.IN, "keep-recording-artifacts", "keepRecordingArtifacts", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this.settings.setValue("auto-transcribe-timeout", this.autoTranscribeTimeout)', source)
        self.assertIn('this.settings.setValue("auto-relisten", this.autoRelisten)', source)
        self.assertIn('this.settings.setValue("keep-recording-artifacts", this.keepRecordingArtifacts)', source)
        self.assertIn('args.push("--skip-silent-auto-relisten");', source)
        self.assertIn('_("Auto-transcribe at time limit")', source)
        self.assertIn('_("Auto Relisten")', source)
        self.assertIn('_("Keep recording files")', source)
        self.assertIn('this._populateRecordingOptionsMenu();', source)
        self.assertIn('this.autoRelistenPending = false;', source)
        self.assertIn('this.autoRelistenPendingToken = "";', source)
        self.assertIn("this.autoRelistenSequence = 0;", source)
        self.assertIn('let shouldRelisten = this.autoRelistenPending;', source)
        self.assertIn('relistenToken = String(this.autoRelistenSequence) + ":" + recordingKey;', source)
        self.assertIn('this.autoRelistenPending = Boolean(relistenToken);', source)
        self.assertIn('this.autoRelistenPendingToken = relistenToken;', source)
        self.assertIn('if (relistenToken && this.autoRelistenPendingToken !== relistenToken) {', source)
        self.assertIn('let hasTranscript = typeof payload.transcript === "string" && payload.transcript.length > 0;', source)
        self.assertIn('if (payload.status === "done" && payload.silence_detected)', source)
        self.assertIn('if (payload.status === "done" && hasTranscript)', source)
        self.assertIn('if (payload.status === "done" && this.autoRelistenPending)', source)
        self.assertIn('let transcript = typeof payload.transcript === "string" ? payload.transcript : this.lastTranscript || "";', source)
        self.assertIn('let relistenStarted = false;', source)
        self.assertIn('if (shouldRelisten) {', source)
        self.assertIn('relistenStarted = this._restartRelistenRecording();', source)
        self.assertIn('this._insertTranscriptText(transcript)', source)
        self.assertIn('this._reserveAutoInsertFingerprint(insertFingerprint)', source)
        self.assertIn('if (relistenStarted) {', source)
        self.assertIn('return true;', source)
        self.assertIn('return false;', source)
        self.assertIn('this._spawnJson(this._baseArgs("start"), (payload) => {', source)
        self.assertIn('this._restartRelistenRecording();', source)
        self.assertIn('(!this.autoTranscribeTimeout && !this.autoRelisten)', source)
        self.assertIn('if (!this.autoRelisten) {', source)
        self.assertIn('this.autoTranscribeRecordingKey = "";', source)
        self.assertIn('_recordingOptionsLabel: function()', source)
        self.assertIn('_("relisten")', source)

    def test_auto_relisten_done_payload_routing_is_ordered(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        silent_index = source.index('if (payload.status === "done" && payload.silence_detected)')
        transcript_index = source.index('if (payload.status === "done" && hasTranscript)')
        empty_index = source.index('if (payload.status === "done" && this.autoRelistenPending)')
        reset_index = source.index("this.autoRelistenPending = false;", empty_index)
        restart_index = source.index("relistenStarted = this._restartRelistenRecording();", empty_index)
        status_index = source.index('payload.message || _("Recording finished without transcript")', empty_index)

        self.assertLess(silent_index, transcript_index)
        self.assertLess(transcript_index, empty_index)
        self.assertLess(empty_index, reset_index)
        self.assertLess(reset_index, restart_index)
        self.assertLess(restart_index, status_index)

    def test_spawn_callbacks_ignore_removed_applet(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this.appletRemoved = false;", source)
        self.assertIn("this.spawnGeneration = 0;", source)
        self.assertIn("this.appletRemoved = true;", source)
        self.assertIn("this.spawnGeneration += 1;", source)
        self.assertIn("!this.appletRemoved &&", source)
        self.assertIn("let applet = this;", source)
        self.assertIn("let spawnGeneration = this.spawnGeneration;", source)
        self.assertIn("if (applet.appletRemoved || applet.spawnGeneration !== spawnGeneration) {", source)

    def test_applet_exposes_notification_options_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertFalse(schema["notify-recording"]["default"])
        self.assertFalse(schema["notify-complete"]["default"])
        self.assertTrue(schema["notify-error"]["default"])
        self.assertIn("this.notifyComplete = false", source)
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
        self.assertIn('Object.prototype.hasOwnProperty.call(options, "timeoutMs")', source)
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

    def test_diagnostics_menu_can_benchmark_downloaded_models_from_selected_audio_file(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Benchmark downloaded models")', source)
        self.assertIn("_selectBenchmarkAudioFile: function()", source)
        self.assertIn("_benchmarkAudioFileDialogArgs: function()", source)
        self.assertIn("--file-selection", source)
        self.assertIn("--file-filter=Audio files | *.wav *.WAV *.flac *.FLAC *.mp3 *.MP3 *.ogg *.OGG *.oga *.OGA *.opus *.OPUS *.m4a *.M4A *.aac *.AAC *.webm *.WEBM", source)
        self.assertIn("_benchmarkDownloadedModels: function(audioPath)", source)
        self.assertIn('return [this._cliCommand(), "benchmark-models", String(audioPath || ""), "--language", String(this._currentLanguage()), "--json"]', source)
        self.assertIn("_benchmarkDownloadedModels(audioPath)", source)
        self.assertIn("BENCHMARK_COMMAND_TIMEOUT_MS", source)

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
        self.assertIn('this.installMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Install"))', source)
        self.assertIn("this.toolsMenuItem.menu.addMenuItem(this.installMenuItem);", source)
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
        self.assertIn("this._styleSelectionSubmenu(this.installMenuItem);", source)
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

    def test_text_model_menu_can_install_ollama_model(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Choose Ollama text model")', source)
        self.assertIn("installOllamaModel.connect(\"activate\", () => this._chooseOllamaTextModel());", source)
        self.assertNotIn('_selectionMenuItem(_("Install Ollama model"))', source)
        self.assertNotIn('new PopupMenu.PopupIconMenuItem(_("Install Ollama text model")', source)
        self.assertIn("_chooseOllamaTextModel: function()", source)
        self.assertIn('_textModelsArgs("ollama")', source)
        self.assertIn("_activateOllamaTextModelFlow: function()", source)
        self.assertIn("ollama.connect(\"activate\", () => this._activateOllamaTextModelFlow());", source)
        self.assertIn("this._installOllamaRuntime(true);", source)
        self.assertIn("_watchOllamaInstallThenChoose: function()", source)
        self.assertIn("_scheduleOllamaInstallWatchPoll: function()", source)
        self.assertIn("OLLAMA_INSTALL_POLL_SECONDS", source)
        self.assertIn("OLLAMA_INSTALL_MAX_POLLS", source)
        self.assertIn("this._clearOllamaInstallWatchTimer();", source)
        self.assertIn("_ollamaModelChoiceArgs: function(models)", source)
        self.assertIn("--title=Choose Ollama text model", source)
        self.assertIn("Add another model...", source)
        self.assertIn('if (choice === "ADD")', source)
        self.assertIn('if (choice.indexOf("SELECT:") === 0)', source)
        self.assertIn('this._selectTextModelBackend("ollama", model, _("Text model: ") + model);', source)
        self.assertIn("_promptInstallOllamaTextModel: function()", source)
        self.assertIn("_ollamaModelPromptArgs: function()", source)
        self.assertIn("--entry-text=llama3.2:3b", source)
        self.assertIn("_installOllamaTextModel: function(model)", source)
        self.assertIn('"install-text-model", "--backend", "ollama", "--model", safeModel, "--json"', source)
        self.assertIn('let installedModel = String(payload.model || model);', source)
        self.assertIn('this._selectTextModelBackend("ollama", installedModel, message);', source)
        self.assertIn('this._notify(_("Ollama model installation failed"), String(payload.error), true)', source)
        self.assertIn('this._notify(_("Ollama model installed"), installedModel, false)', source)

    def test_text_model_menu_keeps_selected_ollama_model_when_refresh_is_empty(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('let selectedOllamaModel = String(this.ollamaModel || "").trim();', source)
        self.assertIn('if (activeProvider === "ollama" && backend === "ollama" && selectedOllamaModel !== "")', source)
        self.assertIn('name: selectedOllamaModel', source)
        self.assertIn('description: _("selected")', source)
        self.assertIn("Model list is temporarily empty; using selected Ollama model", source)

    def test_tools_install_menu_has_functional_setup_buttons(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Install Ollama")', source)
        self.assertIn("installOllamaRuntime.connect(\"activate\", () => this._installOllamaRuntime());", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Uninstall Ollama")', source)
        self.assertIn("uninstallOllamaRuntime.connect(\"activate\", () => this._uninstallOllamaRuntime());", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Basic setup")', source)
        self.assertIn("basicSetup.connect(\"activate\", () => this._runBasicSetup());", source)
        self.assertIn("_terminalCommandArgs: function(title, command)", source)
        self.assertIn("_runTerminalWorkflow: function(title, command, openedMessage)", source)
        self.assertIn("return true;", source)
        self.assertIn("return false;", source)
        self.assertIn("_installOllamaRuntimeCommand: function()", source)
        self.assertIn("sudo dnf install -y ollama", source)
        self.assertIn("sudo apt-get install -y ollama", source)
        self.assertIn("No supported package manager found (dnf/apt-get). Install Ollama manually and rerun this step.", source)
        self.assertIn("_uninstallOllamaRuntimeCommand: function()", source)
        self.assertIn("sudo systemctl disable --now ollama", source)
        self.assertIn("_basicSetupCommand: function()", source)
        self.assertIn("download-model ct2-base-int8 --json", source)

    def test_applet_settings_schema_mentions_frontend_limits(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("Max 4096 chars", schema["personal-context"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["vocabulary"]["tooltip"])
        self.assertIn("main-menu-map-section", schema["layout"]["main-page"]["sections"])
        self.assertEqual(schema["layout"]["main-menu-map-section"]["title"], "Main menu settings")
        self.assertIn("main-menu-settings-map", schema)
        self.assertIn("All persistent settings from the applet menu are available here", schema["main-menu-settings-map"]["description"])
        self.assertIn("menu: Recording", schema["layout"]["recording-section"]["title"])
        self.assertIn("menu: Text and output", schema["layout"]["output-section"]["title"])
        self.assertIn("Voice model", schema["layout"]["backend-section"]["title"])
        self.assertIn("Max 4096 chars", schema["transcriber-command"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["post-process-command"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["post-process-prompt"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["input-device"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["whisper-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["ollama-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-text-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["ollama-url"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-url"]["tooltip"])

    def test_starter_voice_model_matches_current_language(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_starterVoiceModelName: function()", source)
        self.assertIn("_voiceModelLanguage: function()", source)
        self.assertIn("return this._primaryLanguage();", source)
        self.assertIn('return "ct2-base-int8";', source)
        self.assertIn("_ensureVoiceModelCompatibleWithPrimaryLanguage(false);", source)
        self.assertIn("_ensureVoiceModelCompatibleWithCurrentLanguage(true)", source)
        self.assertIn('_("Active: ") + this._activeVoiceModelSummary()', source)
        self.assertIn("_activeVoiceModelSummary: function()", source)
        self.assertIn('if (backend === "openai-compatible")', source)
        self.assertIn('_("External API: ") + (String(this.openaiCompatibleModel || "").trim() || _("not configured"))', source)
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
        self.assertIn('this._pasteClipboardAfterFocus(submitWithReturn)', source)
        self.assertIn("Copied and pasted into target window", source)

    def test_applet_checks_insert_fingerprint_before_relisten_restart(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        finish_index = source.index("_finishAppletTextInsert: function(payload) {")
        fingerprint_index = source.index("let insertFingerprint = this._autoInsertFingerprint(payload, transcript);", finish_index)
        relisten_index = source.index("let shouldRelisten = this.autoRelistenPending;", finish_index)

        self.assertLess(fingerprint_index, relisten_index)
        self.assertIn("this.autoInsertFingerprints = [];", source)
        self.assertIn("this._hasAutoInsertFingerprint(fingerprint)", source)
        self.assertIn("if (payload.inserted === true) {", source)
        self.assertIn('payload.message || _("Transcript already inserted by backend")', source)
        self.assertIn("if (!this._reserveAutoInsertFingerprint(insertFingerprint))", source)
        self.assertIn("this._rememberAutoInsertFingerprint(fingerprint);", source)
        self.assertIn("this._forgetAutoInsertFingerprint(insertFingerprint);", source)
        self.assertIn("_finishPendingRelisten: function()", source)
        self.assertIn("this._finishPendingRelisten();", source)
        reserve_index = source.index("if (!this._reserveAutoInsertFingerprint(insertFingerprint))", finish_index)
        insert_index = source.index("this._insertTranscriptText(transcript)", finish_index)
        self.assertLess(reserve_index, insert_index)
        duplicate_index = reserve_index
        duplicate_finish_index = source.index("this._finishPendingRelisten();", duplicate_index)
        duplicate_return_index = source.index("return;", duplicate_index)
        self.assertLess(duplicate_finish_index, duplicate_return_index)
        self.assertIn("_hasAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_reserveAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_rememberAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_forgetAutoInsertFingerprint: function(fingerprint)", source)
        restart_index = source.index("_restartRelistenRecording: function() {")
        restart_end = source.index("_preparedTranscriptText: function", restart_index)
        self.assertNotIn("this._resetAutoInsertFingerprint();", source[restart_index:restart_end])

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

    def test_dynamic_model_menus_guard_fast_expand_clicks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_canMutateMenu: function(item)", source)
        self.assertIn('typeof item.menu.removeAll === "function"', source)
        self.assertIn('typeof item.menu.addMenuItem === "function"', source)
        self.assertIn("if (!this._canMutateMenu(this.modelItem))", source)
        self.assertIn("if (!this._canMutateMenu(this.textModelItem))", source)
        self.assertIn("this.modelMenuRefreshToken = refreshToken;", source)
        self.assertIn("this.textModelMenuRefreshToken = refreshToken;", source)
        self.assertIn("if (this.modelMenuRefreshToken !== refreshToken || !this._canMutateMenu(this.modelItem))", source)
        self.assertIn("if (this.textModelMenuRefreshToken !== refreshToken || !this._canMutateMenu(this.textModelItem))", source)

    def test_applet_can_reinsert_last_transcript_with_current_output_mode(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.insertLastItem = new PopupMenu.PopupIconMenuItem(_("Insert last transcript")', source)
        self.assertIn("this.insertLastItem.setSensitive(false)", source)
        self.assertIn("this.insertLastItem.connect(\"activate\", () => this._insertLastTranscript())", source)
        self.assertIn("this.insertLastItem.setSensitive(Boolean(this.lastTranscript))", source)
        self.assertIn("_insertLastTranscript: function()", source)
        self.assertIn("_insertTranscriptText: function(transcript)", source)
        self.assertIn("_finishAppletTextInsert: function(payload)", source)
        self.assertIn("_finishPendingRelisten: function()", source)
        self.assertIn("let shouldRelisten = this.autoRelistenPending;", source)
        self.assertIn("let relistenStarted = false;", source)
        self.assertIn("if (shouldRelisten) {", source)
        self.assertIn("relistenStarted = this._restartRelistenRecording();", source)
        self.assertIn("this._insertTranscriptText(transcript)", source)
        self.assertIn("this._reserveAutoInsertFingerprint(insertFingerprint)", source)
        self.assertIn("if (payload.inserted === true) {", source)
        self.assertIn("if (relistenStarted) {", source)
        self.assertIn("} else if (!shouldRelisten) {", source)
        self.assertIn("_finishSilentRelistenSkip: function(payload)", source)
        self.assertIn("_finishEmptyRelistenDone: function(payload)", source)
        self.assertIn('if (payload.status === "done" && payload.silence_detected)', source)
        self.assertIn("this.notificationSessionActive = true;", source)
        self.assertIn('if (payload.status === "done" && hasTranscript)', source)
        self.assertIn('payload.message || _("Recording finished without transcript")', source)
        self.assertIn('if (method === "none")', source)
        self.assertIn('if (method === "type")', source)
        self.assertIn("if (this._typeTextAfterFocus(text)) {", source)
        self.assertIn("Util.spawn(this._coerceSpawnArgs(args));", source)
        self.assertIn('["xdotool", "type", "--clearmodifiers", "--delay", String(delay), "--", typedText]', source)
        self.assertIn("_isTerminalTargetWindow: function()", source)
        self.assertIn('let pasteKey = this._isTerminalTargetWindow() ? "ctrl+shift+v" : "ctrl+v";', source)
        self.assertIn('["xdotool", "key", "--clearmodifiers", pasteKey]', source)
        self.assertIn('if (sendEnter) {', source)
        self.assertIn('args.push("Return");', source)

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
        self.assertIn('"--keep-recordings", "20"', source)
        self.assertIn('"--dry-run", "--json"', source)
        self.assertIn("_cleanupCount: function(payload, dryRun)", source)
        self.assertIn("payload.would_delete_transcripts", source)
        self.assertIn("_previewCleanup: function()", source)
        self.assertIn('this._setStatus("processing", _("Previewing cleanup..."), this.lastTranscript)', source)
        self.assertIn('this._setStatus("ready", _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), this.lastTranscript)', source)
        self.assertIn("let deleted = this._cleanupCount(payload, false);", source)

    def test_voice_model_menu_can_return_to_automatic_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_("Automatic voice model")', source)
        self.assertIn("_selectAutomaticVoiceBackend: function()", source)
        self.assertIn('this.settings.setValue("transcriber", this.transcriber)', source)
        self.assertIn('this.settings.setValue("whisper-model", this.whisperModel)', source)
        self.assertIn('this._setStatus("ready", _("Voice model: automatic"), this.lastTranscript)', source)
        self.assertIn("this._refreshModelMenu();", source)
