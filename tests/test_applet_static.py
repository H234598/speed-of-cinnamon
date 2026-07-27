from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLET_DIR = REPO_ROOT / "files" / "speed-of-cinnamon@H234598"


class AppletStaticTest(unittest.TestCase):
    def test_settings_page_includes_responsive_header_and_footer_logos(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))
        widget_source = (APPLET_DIR / "SettingsLogo.py").read_text(encoding="utf-8")

        sections = schema["layout"]["main-page"]["sections"]
        self.assertEqual(sections[0], "settings-logo-header-section")
        self.assertEqual(sections[-1], "settings-logo-footer-section")
        self.assertEqual(schema["layout"]["settings-logo-header-section"]["keys"], ["settings-logo-header"])
        self.assertEqual(schema["layout"]["settings-logo-footer-section"]["keys"], ["settings-logo-footer"])
        self.assertEqual(schema["settings-logo-header"]["type"], "custom")
        self.assertEqual(schema["settings-logo-header"]["file"], "SettingsLogo.py")
        self.assertEqual(schema["settings-logo-header"]["widget"], "HeaderLogo")
        self.assertEqual(schema["settings-logo-footer"]["type"], "custom")
        self.assertEqual(schema["settings-logo-footer"]["file"], "SettingsLogo.py")
        self.assertEqual(schema["settings-logo-footer"]["widget"], "FooterLogo")
        self.assertTrue((APPLET_DIR / "assets" / "settings-header-logo.png").is_file())
        self.assertTrue((APPLET_DIR / "assets" / "settings-footer-logo.png").is_file())
        self.assertIn("class HeaderLogo(_ResponsiveLogo):", widget_source)
        self.assertIn("class FooterLogo(_ResponsiveLogo):", widget_source)
        self.assertIn('github_url = "https://github.com/H234598/speed-of-cinnamon"', widget_source)
        self.assertIn('Gtk.show_uri_on_window(None, self.github_url, Gtk.get_current_event_time())', widget_source)
        self.assertIn('event_box.connect("button-press-event", self._open_project_repository)', widget_source)
        self.assertIn('box.connect("size-allocate", self._on_size_allocate)', widget_source)
        self.assertIn("self._source_pixbuf.scale_simple(", widget_source)
        self.assertIn("max_width = 720", widget_source)
        self.assertIn("max_height = 220", widget_source)
        self.assertIn("height_limited_width = max(1, int(self.max_height * source_width / source_height))", widget_source)
        self.assertIn("target_height = max(1, min(int(self.max_height)", widget_source)
        self.assertIn("def _show_fallback(self):", widget_source)
        self.assertIn("fallback = Gtk.Label(label=self._fallback_text)", widget_source)
        self.assertIn("self._show_fallback()", widget_source)
        self.assertNotIn("subprocess", widget_source)

    def test_applet_auto_detects_user_and_system_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('const SYSTEM_CLI = "/usr/bin/speed-of-cinnamon";', source)
        self.assertIn("GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE)", source)
        self.assertIn("GLib.file_test(SYSTEM_CLI, GLib.FileTest.IS_EXECUTABLE)", source)
        self.assertIn('this._logLifecycleError("cli-command", err);', source)
        self.assertIn('configured.charAt(0) === "/" && GLib.file_test(configured, GLib.FileTest.IS_EXECUTABLE)', source)
        self.assertIn('return "";', source)
        self.assertNotIn('      return "";\n    }\n    if (GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE))', source)
        self.assertNotIn('return "speed-of-cinnamon";', source)
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
        self.assertEqual(schema["post-process-preset"]["default"], "minimal")
        self.assertTrue(schema["post-process-preserve-code"]["default"])
        self.assertTrue(schema["post-process-never-add-content"]["default"])
        self.assertFalse(schema["post-process-mask-sensitive-data"]["default"])
        self.assertIn("text-polishing-section", schema["layout"]["main-page"]["sections"])
        self.assertIn("text-polishing-help", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("post-process-preset", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("post-process-preset-help-minimal", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("post-process-preset-help-custom", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("post-process-preserve-code", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("post-process-never-add-content", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("post-process-mask-sensitive-data", schema["layout"]["text-polishing-section"]["keys"])
        self.assertIn("openai-compatible-flex-processing", schema["layout"]["backend-section"]["keys"])
        self.assertNotIn("this.maintenanceMenuItem.menu.addMenuItem(this.openAiFlexProcessingItem);", source)
        self.assertIn('this.postProcessBackend = "none"', source)
        self.assertIn('this.postProcessPreset = TEXT_POLISHING_SAFE_PRESET;', source)
        self.assertIn('this.postProcessPreserveCode = true;', source)
        self.assertIn('this.postProcessNeverAddContent = true;', source)
        self.assertIn('this.postProcessMaskSensitiveData = false;', source)
        self.assertIn('["openai-compatible-url", "openaiCompatibleUrl"]', source)
        self.assertIn('["openai-compatible-text-model", "openaiCompatibleTextModel"]', source)
        self.assertIn('["openai-compatible-flex-processing", "openaiCompatibleFlexProcessing"]', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-model", "openaiCompatibleModel", this._onVoiceBackendSettingsChanged, null);', source)
        self.assertNotIn('this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-model", "openaiCompatibleModel", this._onTextModelSettingsChanged, null);', source)
        self.assertIn('["post-process-preset", "postProcessPreset"]', source)
        self.assertIn('["post-process-preserve-code", "postProcessPreserveCode"]', source)
        self.assertIn('["post-process-never-add-content", "postProcessNeverAddContent"]', source)
        self.assertIn('["post-process-mask-sensitive-data", "postProcessMaskSensitiveData"]', source)
        self.assertIn('args.push("--backend", "openai-compatible")', source)
        self.assertIn('args.push("--openai-compatible-url", safeOpenAiCompatibleUrl)', source)
        self.assertIn('this._appendCliOptionWithinBudget(args, "--openai-compatible-model", safeOpenAiCompatibleModel)', source)
        self.assertIn('this._appendCliOptionWithinBudget(args, "--openai-compatible-text-model", safeOpenAiCompatibleTextModel)', source)
        self.assertIn('this._appendCliFlagWithinBudget(args, "--no-openai-compatible-flex-processing")', source)
        self.assertNotIn('this.openAiFlexProcessingItem = new PopupMenu.PopupMenuItem("")', source)
        self.assertIn('let textCommandConfigured = String(this.postProcessCommand || "").trim() !== "";', source)
        self.assertIn('let customLabel = _("Custom command") + (textCommandConfigured ? "" : _(" - configure in settings"));', source)
        self.assertIn('let custom = this._selectionMenuItem((backend === "command" || backend === "custom" ? "[x] " : "[ ] ") + customLabel);', source)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Configure custom text command in applet settings"), this.lastTranscript);', source)
        self.assertNotIn('this._setStatus("ready", _("Configure custom text command in applet settings"), this.lastTranscript);', source)
        self.assertIn('this._connectSafe(openaiCompatible, "activate", () => this._openExternalApiEnvEditor("text"));', source)
        self.assertIn('let presetMenu = new PopupMenu.PopupSubMenuMenuItem(_("Polishing preset: ") + this._textPolishingPresetLabel(this.postProcessPreset));', source)
        self.assertIn("_populateTextPolishingPresetMenu: function(parentMenu)", source)
        self.assertIn("_selectTextPolishingPreset: function(preset)", source)
        self.assertIn("const TEXT_POLISHING_PRESETS = [", source)
        self.assertIn('this._commitSettingValue("postProcessPreset", "post-process-preset"', source)
        self.assertIn('let safetyMenu = new PopupMenu.PopupSubMenuMenuItem(_("Polishing safety"));', source)
        self.assertIn("_populateTextPolishingSafetyMenu: function(parentMenu)", source)
        self.assertIn("_toggleTextPolishingSafetyFlag: function(settingKey, propertyName, label)", source)
        self.assertIn('this._toggleTextPolishingSafetyFlag("post-process-preserve-code", "postProcessPreserveCode", _("Preserve commands and code"))', source)
        self.assertIn('this._toggleTextPolishingSafetyFlag("post-process-never-add-content", "postProcessNeverAddContent", _("Never add content"))', source)
        self.assertIn('this._toggleTextPolishingSafetyFlag("post-process-mask-sensitive-data", "postProcessMaskSensitiveData", _("Mask sensitive data"))', source)
        self.assertIn('this.postProcessBackend = "openai-compatible";', source)
        self.assertIn('this._commitSettingsBatch(settingsWrites, "settings-text-model"', source)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);', source)
        self.assertIn('this._commitSettingValue("postProcessPreset", "post-process-preset"', source)
        self.assertIn("this._commitSettingValue(propertyName, settingKey, nextValue", source)
        self.assertIn("_rollbackSettingsBatch: function(writes)", source)
        self.assertIn("_commitSettingsBatch: function(writes, group, errorMessage, preserveRecording)", source)
        self.assertIn('this._commitSettingsBatch(settingsWrites, "settings-text-model"', source)
        self.assertIn('this._commitSettingsBatch(settingsWrites, "settings-text-polishing"', source)
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
        self.assertEqual(schema["layout"]["backend-section"]["title"], "Voice model (menu: Recording > Voice model)")
        self.assertIn("voice-model-help", schema["layout"]["backend-section"]["keys"])
        self.assertIn("transcriber", schema["layout"]["backend-section"]["keys"])
        self.assertIn("whisper-model", schema["layout"]["backend-section"]["keys"])
        self.assertIn("transcriber-command", schema["layout"]["backend-section"]["keys"])
        self.assertIn("cli-path", schema["layout"]["backend-section"]["keys"])
        self.assertNotIn("openai-compatible-api-key", schema["layout"]["backend-section"]["keys"])
        self.assertIn("Recording > Voice model", schema["voice-model-help"]["description"])
        self.assertIn("No option was removed", schema["voice-model-help"]["tooltip"])
        self.assertIn('this.openaiCompatibleApiKey = "";', source)
        self.assertIn('this.externalApiEnvApiKey = "";', source)
        self.assertIn('"openai-compatible-api-key": "openai-compatible API key"', source)
        self.assertIn('"openai-compatible-api-key", "openaiCompatibleApiKey"', source)
        self.assertNotIn('this.settings.setValue("openai-compatible-api-key", this.openaiCompatibleApiKey);', source)
        self.assertIn(
            'this._setSettingValueOrThrow(\n        "openai-compatible-api-key",\n        "",',
            source,
        )
        self.assertNotIn('args.push("--openai-compatible-api-key"', source)
        self.assertIn('"SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY"', source)
        self.assertIn("_shouldExposeOpenAiCompatibleApiKeyToBackend: function(args)", source)
        self.assertIn('["toggle", "start", "stop", "transcribe-file"]', source)
        self.assertIn('command === "text-models"', source)
        self.assertIn('this._argValue(args, "--backend") === "openai-compatible"', source)
        self.assertIn("_runWithBackendEnvironment(this._shouldExposeOpenAiCompatibleApiKeyToBackend(normalizedArgs), (backendEnv) => {", source)
        self.assertIn("_spawnJsonWithBackendEnvironment: function(args, env, callback, inputText, options)", source)
        self.assertIn("_runBoundedSubprocess: function(args, env, options, callback)", source)
        self.assertIn("read_bytes_async", source)
        self.assertIn("process.wait_check_async", source)
        self.assertIn("source.wait_check_finish(result)", source)
        self.assertIn("if (waitResult !== true)", source)
        self.assertIn('throw new Error("Subprocess exit status check failed");', source)
        self.assertIn("let finishWhenReady = () => {", source)
        self.assertIn('finish({ error: "Subprocess exited unsuccessfully" }, false);', source)
        self.assertIn("SUBPROCESS_READ_CHUNK_BYTES", source)
        self.assertIn("stdoutBytes += size;", source)
        self.assertIn("stderrBytes += size;", source)
        self.assertNotIn("stdoutBytes += chunkBytes;", source)
        self.assertNotIn("stderrBytes += chunkBytes;", source)
        self.assertIn("Gio.SubprocessLauncher", source)
        self.assertNotIn("Gio.SubprocessFlags.SEARCH_PATH", source)
        self.assertNotIn("SEARCH_PATH_FROM_ENVP", source)
        self.assertIn("launcher.setenv(key, String(env[key] || \"\"), true);", source)
        self.assertNotIn("GLib.setenv", source)
        self.assertNotIn("GLib.unsetenv", source)
        self.assertNotIn('snapshot["openai-compatible-api-key"]', source)
        self.assertIn('let externalMenu = new PopupMenu.PopupSubMenuMenuItem(_("External API"));', source)
        self.assertIn("this._populateExternalApiVoiceMenu(externalMenu.menu);", source)
        self.assertIn("_populateExternalApiVoiceMenu: function(parentMenu)", source)
        self.assertIn('let whisperCommand = this._selectionMenuItem((whisperCommandActive ? "[x] " : "[ ] ") + _("OpenAI Whisper command"));', source)
        self.assertIn('this._connectSafe(whisperCommand, "activate", () => this._selectStaticVoiceBackend("whisper", _("Voice model: OpenAI Whisper command")));', source)
        self.assertIn('let customCommandConfigured = String(this.transcriberCommand || "").trim() !== "";', source)
        self.assertIn('let customCommandLabel = _("Custom command") + (customCommandConfigured ? "" : _(" - configure in settings"));', source)
        self.assertIn('let customCommand = this._selectionMenuItem((customCommandActive ? "[x] " : "[ ] ") + customCommandLabel);', source)
        self.assertIn('this._openAppletSettings();', source)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Configure custom voice command in applet settings"), this.lastTranscript);', source)
        self.assertIn("_selectStaticVoiceBackend: function(transcriber, message)", source)
        self.assertIn("_commitVoiceBackendSettings: function(transcriber, whisperModel, group, errorMessage, preserveRecording)", source)
        self.assertIn('"voice-static"', source)
        self.assertIn("_selectExternalApiVoiceBackend: function()", source)
        self.assertIn('"external-api-voice"', source)
        self.assertIn('return _("Voice: External API ") + this._shortMenuText(externalModel, 96);', source)

    def test_applet_exposes_artifact_encryption_submenu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(
            set(schema["artifact-encryption"]["options"].values()),
            {"keyring", "passphrase", "off"},
        )
        self.assertIn("const ARTIFACT_ENCRYPTION_MODES = [", source)
        self.assertIn('this.artifactEncryptionItem = new PopupMenu.PopupSubMenuMenuItem(_("Encryption: Secret Service keyring"))', source)
        self.assertIn("this.textOutputMenuItem.menu.addMenuItem(this.artifactEncryptionItem);", source)
        self.assertIn("_populateArtifactEncryptionMenu: function()", source)
        self.assertIn("_artifactEncryptionLabel: function(method)", source)
        self.assertIn("_selectArtifactEncryptionMode: function(mode)", source)
        self.assertIn('this._commitSettingValue("artifactEncryption", "artifact-encryption"', source)
        self.assertIn('args.push("--artifact-encryption", this._normalizeArtifactEncryption(this.artifactEncryption));', source)
        self.assertIn("this._populateArtifactEncryptionMenu();", source)

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
        sync_start = source.index("_syncExternalApiConfigOnStartup: function()")
        sync_end = source.index("\n  _clearPersistedOpenAiCompatibleApiKey:", sync_start)
        sync_block = source[sync_start:sync_end]
        self.assertIn("let settingsWrites = [];", sync_block)
        self.assertIn('this._commitSettingsBatch(\n        settingsWrites,\n        "settings-external-api"', sync_block)
        self.assertIn("let defaultsCommitted = true;", sync_block)
        self.assertIn("this.openaiCompatibleUrl = nextUrl;", sync_block)
        self.assertIn("this.openaiCompatibleModel = nextModel;", sync_block)
        self.assertLess(sync_block.index("this._commitSettingsBatch("), sync_block.index("this.openaiCompatibleUrl = nextUrl;"))
        self.assertNotIn('this.settings.setValue("openai-compatible-url", this.openaiCompatibleUrl);', sync_block)
        self.assertIn("if (envPath && this._applyExternalApiEnvFile(false))", source)
        self.assertIn("let mkdirResult = GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);", source)
        self.assertIn('throw new Error("External API config directory could not be created");', source)
        self.assertIn("return null;", source)
        self.assertIn("_externalApiEnvPath: function()", source)
        self.assertIn('"external-api.env"', source)
        self.assertIn("OPENAI_COMPATIBLE_URL=", source)
        self.assertIn("OPENAI_COMPATIBLE_STT_MODEL=", source)
        self.assertIn("OPENAI_COMPATIBLE_TEXT_MODEL=", source)
        self.assertIn("OPENAI_COMPATIBLE_API_KEY=", source)
        self.assertIn("this._clearPersistedOpenAiCompatibleApiKey();", source)
        self.assertIn("_writeExternalApiEnvFile: function()", source)
        self.assertIn("_writeExternalApiEnvFileContents: function(path, content)", source)
        self.assertIn("this._writeExternalApiEnvFileContents(path, this._externalApiEnvContent());", source)
        self.assertIn('this._setStatusPreservingRecording("error", _("External API config file could not be written"), this.lastTranscript);', source)
        self.assertIn("_migrateExternalApiEnvFile: function(path)", source)
        self.assertIn('assignment.key === "OPENAI_COMPATIBLE_URL" && assignment.value === LEGACY_OPENAI_COMPATIBLE_URL', source)
        self.assertIn("let assignmentPattern = /^(\\s*)(OPENAI_COMPATIBLE_URL|OPENAI_COMPATIBLE_STT_MODEL|OPENAI_COMPATIBLE_MODEL|OPENAI_COMPATIBLE_TEXT_MODEL)\\s*=/;", source)
        self.assertIn("lines[assignment.index] = lines[assignment.index].replace(", source)
        self.assertIn('"$1OPENAI_COMPATIBLE_STT_MODEL$2"', source)
        self.assertIn('!assignments.some((assignment) => assignment.key === "OPENAI_COMPATIBLE_TEXT_MODEL")', source)
        self.assertIn("values.OPENAI_COMPATIBLE_STT_MODEL || values.OPENAI_COMPATIBLE_MODEL", source)
        self.assertIn("this.externalApiEnvApplyTarget = \"voice\";", source)
        self.assertIn("_openExternalApiEnvEditor: function(target)", source)
        self.assertIn("_applyExternalApiEnvTarget: function(target)", source)
        self.assertIn('this._connectSafe(useItem, "activate", () => this._openExternalApiEnvEditor("voice"));', source)
        self.assertIn('this._connectSafe(openaiCompatible, "activate", () => this._openExternalApiEnvEditor("text"));', source)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);', source)
        self.assertIn('this._refreshTextModelMenuForBackend("openai-compatible");', source)
        self.assertIn("if (!this._writeExternalApiEnvFile()) {", source)
        self.assertTrue(
            "this._refreshTextModelMenu();\n        return;\n      }" in source or
            "this._refreshTextModelMenu();\n          return;\n        }" in source
        )
        self.assertIn("this._selectExternalApiVoiceBackend();", source)
        self.assertNotIn('this._selectTextModelBackend("openai-compatible", this.openaiCompatibleModel, _("Text polishing: OpenAI-compatible API"));', source)
        self.assertIn("_watchExternalApiEnvFile: function(path)", source)
        editor_start = source.index("_openExternalApiEnvEditor: function(target)")
        editor_end = source.index("\n  _applyExternalApiEnvTarget:", editor_start)
        editor_block = source[editor_start:editor_end]
        self.assertIn("if (!path) {", editor_block)
        self.assertLess(editor_block.index("if (!path)"), editor_block.index("this._watchExternalApiEnvFile(path)"))
        self.assertIn("_disconnectTrackedSignalsForTarget: function(target)", source)
        self.assertIn("this._disconnectTrackedSignalsForTarget(monitor)", source)
        self.assertIn("_clearMenuItems: function(menu)", source)
        self.assertIn("if (!this._clearMenuItems(this.recorderItem.menu))", source)
        self.assertIn("addTarget(item);", source)
        self.assertIn("addTarget(item.menu);", source)
        self.assertIn("Gio.FileMonitorEvent.CHANGES_DONE_HINT", source)
        self.assertIn("_applyExternalApiEnvFile: function(showStatus)", source)
        self.assertIn("_validateExternalApiUrl: function(value, fieldName)", source)
        self.assertIn("_validatedExternalApiConfig: function(values)", source)
        self.assertIn('let normalized = typeof value === "string" ? value.trim() : "";', source)
        self.assertIn('let safeFallback = typeof fallback === "string" ? fallback : "";', source)
        self.assertIn('let apiKeyValue = typeof values.apiKey === "string" ? values.apiKey : "";', source)
        self.assertIn('must not contain userinfo', source)
        self.assertIn('must use https:// unless host is local loopback', source)
        self.assertIn('let config;\n    try {\n      config = this._validatedExternalApiConfig', source)
        self.assertIn('this._setStatusPreservingRecording("error", _("External API config contains invalid values"), this.lastTranscript);', source)
        self.assertIn('throw new Error("External API config does not contain the persisted API key");', source)
        self.assertIn("let legacyApiKey = this._coerceCliTextArg(", source)
        self.assertIn("let migratedValues = this._parseExternalApiEnvText(migrated);", source)
        self.assertIn('"OPENAI_COMPATIBLE_API_KEY=" + this._externalApiEnvEncodeValue(legacyApiKey)', source)
        self.assertIn("let rollbackSettings = (writes) =>", source)
        self.assertIn('if (!this._clearPersistedOpenAiCompatibleApiKey())', source)
        self.assertIn('this._setStatusPreservingRecording("error", _("External API settings could not be finalized")', source)
        self.assertIn("let previousConfig = {", source)
        self.assertIn("let settingsWrites = [", source)
        self.assertIn("let attemptedWrites = [];", source)
        self.assertIn("if (result === false)", source)
        self.assertIn(
            'this._setSettingValueOrThrow(setting[0], setting[2], "External API setting rollback failed");',
            source,
        )
        self.assertIn('this._setStatusPreservingRecording("error", _("External API settings could not be saved"), this.lastTranscript);', source)
        clear_start = source.index("_clearPersistedOpenAiCompatibleApiKey: function()")
        clear_end = source.index("\n  _ensureExternalApiEnvFile:", clear_start)
        clear_block = source[clear_start:clear_end]
        self.assertIn("let previousApiKey = this.openaiCompatibleApiKey;", clear_block)
        self.assertIn('this._setSettingValueOrThrow(\n        "openai-compatible-api-key",\n        "",', clear_block)
        self.assertIn("this.openaiCompatibleApiKey = previousApiKey;", clear_block)
        self.assertIn("return false;", clear_block)
        target_start = source.index("_applyExternalApiEnvTarget: function(target)")
        target_end = source.index("\n  _selectExternalApiVoiceBackend:", target_start)
        target_block = source[target_start:target_end]
        self.assertIn('this._setSettingValueOrThrow(\n          "post-process-backend",\n          "openai-compatible",', target_block)
        self.assertIn("this.postProcessBackend = previousBackend;", target_block)
        self.assertIn("return false;", target_block)
        voice_start = source.index("_commitVoiceBackendSettings: function(transcriber, whisperModel, group, errorMessage, preserveRecording)")
        voice_end = source.index("\n  _ensureVoiceModelCompatibleWithPrimaryLanguage:", voice_start)
        voice_block = source[voice_start:voice_end]
        self.assertIn("let previousTranscriber = this.transcriber;", voice_block)
        self.assertIn("let previousWhisperModel = this.whisperModel;", voice_block)
        self.assertIn("let attemptedWrites = [];", voice_block)
        self.assertIn("this.transcriber = transcriber;", voice_block)
        self.assertIn("this.whisperModel = whisperModel;", voice_block)
        apply_index = source.index("_applyExternalApiEnvFile: function(showStatus)")
        set_index = source.index("let settingsWrites = [", apply_index)
        validate_index = source.index("config = this._validatedExternalApiConfig", apply_index)
        self.assertLess(validate_index, set_index)
        self.assertIn("const MAX_EXTERNAL_API_ENV_BYTES = 65536;", source)
        self.assertIn("_externalApiEnvFileInfo: function(path, allowMissing)", source)
        self.assertIn('query_info("standard::type,standard::size,unix::mode", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null)', source)
        self.assertIn("info.get_file_type() !== Gio.FileType.REGULAR", source)
        self.assertIn("External API config file is too large", source)
        self.assertIn("let info = this._externalApiEnvFileInfo(path, true);", source)
        ensure_start = source.index("_ensureExternalApiEnvFile: function()")
        ensure_end = source.index("\n  _migrateExternalApiEnvFile:", ensure_start)
        ensure_block = source[ensure_start:ensure_end]
        self.assertIn("let info = this._externalApiEnvFileInfo(path, true);", ensure_block)
        self.assertIn("if (!info)", ensure_block)
        self.assertNotIn("GLib.file_test(path, GLib.FileTest.EXISTS)", ensure_block)
        self.assertIn("let setPrivateMode = () =>", source)
        self.assertIn("let modeResult = Gio.File.new_for_path(path).set_attribute_uint32(", source)
        self.assertIn('throw new Error("External API config file mode could not be secured");', source)
        self.assertIn("Gio.File.new_for_path(path).replace_contents(", source)
        self.assertIn("let replaceResult = Gio.File.new_for_path(path).replace_contents(", source)
        self.assertIn("let replaceSucceeded = Array.isArray(replaceResult) ? replaceResult[0] : replaceResult;", source)
        self.assertIn('throw new Error("External API config file could not be replaced");', source)
        self.assertIn("ByteArray.fromString(text)", source)
        self.assertIn("Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION", source)
        self.assertNotIn("GLib.umask", source)
        self.assertIn("this._externalApiEnvFileInfo(path, false);", source)
        self.assertIn("this._readExternalApiEnvFile(path)", source)
        self.assertIn("ByteArray.toString(contents)", source)

    def test_external_api_env_rejects_symlinked_directory_ancestors(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_assertExternalApiEnvDirectoryChainSafe: function(path)")
        end = source.index("\n  _externalApiEnvContent:", start)
        block = source[start:end]
        self.assertIn("Gio.File.new_for_path(GLib.path_get_dirname(path))", block)
        self.assertIn("Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS", block)
        self.assertIn("Gio.FileType.SYMBOLIC_LINK", block)
        self.assertIn("current = current.get_parent();", block)
        self.assertIn("this._assertExternalApiEnvDirectoryChainSafe(path);", source)
        read_start = source.index("_readExternalApiEnvFile: function(path)")
        read_end = source.index("\n  _writeExternalApiEnvFileContents:", read_start)
        self.assertIn("this._assertExternalApiEnvDirectoryChainSafe(path);", source[read_start:read_end])

    def test_external_api_env_values_are_quoted_and_unescaped_symmetrically(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        content_start = source.index("_externalApiEnvContent: function()")
        content_end = source.index("\n  _externalApiEnvFileInfo:", content_start)
        content_block = source[content_start:content_end]
        self.assertIn("_externalApiEnvEncodeValue: function(value)", source)
        self.assertIn("this._externalApiEnvEncodeValue(config.url)", content_block)
        self.assertIn("this._externalApiEnvEncodeValue(config.model)", content_block)
        self.assertIn("this._externalApiEnvEncodeValue(config.textModel)", content_block)
        self.assertIn("this._externalApiEnvEncodeValue(config.apiKey)", content_block)

        parse_start = source.index("_parseExternalApiEnvText: function(text)")
        parse_end = source.index("\n  _applyExternalApiEnvFile:", parse_start)
        parse_block = source[parse_start:parse_end]
        self.assertIn("value.slice(1, -1).replace(/", parse_block)
        self.assertIn('(["\\\\])/g, "$1")', parse_block)

    def test_external_api_urls_reject_out_of_range_ports_and_fake_loopback(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_validateExternalApiUrl: function(value, fieldName)")
        end = source.index("\n  _validatedExternalApiConfig:", start)
        block = source[start:end]
        self.assertIn("let numericPort = Number(port.slice(1));", block)
        self.assertIn("numericPort > 65535", block)
        self.assertIn("let ipv4Loopback =", block)
        self.assertIn("Number(ipv4Loopback[index]) > 255", block)
        self.assertIn("let validIpv4Loopback = Boolean(ipv4Loopback);", block)

    def test_external_api_urls_reject_malformed_bracket_hosts(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_validateExternalApiUrl: function(value, fieldName)")
        end = source.index("\n  _validatedExternalApiConfig:", start)
        block = source[start:end]
        self.assertIn('let bracketHost = closing >= 0 ? authority.slice(1, closing) : "";', block)
        self.assertIn("closing <= 1", block)
        self.assertIn("!/^[0-9A-Fa-f:.]+$/.test(bracketHost)", block)
        self.assertIn('authority.indexOf("[", 1)', block)
        self.assertIn('authority.indexOf("]", closing + 1)', block)
        self.assertIn('authority.indexOf("]") >= 0', block)

    def test_openai_compatible_settings_urls_are_validated_before_cli_spawn(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("_validatedExternalApiUrlOrFallback: function(value, fieldName, fallback)", source)
        helper_start = source.index("_validatedExternalApiUrlOrFallback: function(value, fieldName, fallback)")
        helper_end = source.index("\n  _validatedExternalApiConfig:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("this._validateExternalApiUrl(value, fieldName)", helper_block)
        self.assertIn('this._recordLifecycleError("settings-url", error);', helper_block)
        self.assertIn("return safeFallback;", helper_block)
        base_start = source.index("_baseArgs: function(command, languageOverride)")
        base_end = source.index("\n  _appendCliOptionWithinBudget:", base_start)
        self.assertIn('this._validatedExternalApiUrlOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL)', source[base_start:base_end])
        models_start = source.index("_textModelsArgs: function(backendOverride)")
        models_end = source.index("\n  _tryTextModelsArgs:", models_start)
        self.assertIn('this._validatedExternalApiUrlOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL)', source[models_start:models_end])

    def test_external_env_monitor_is_cleaned_when_signal_connection_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_watchExternalApiEnvFile: function(path)")
        end = source.index("\n  _openExternalApiEnvEditor:", start)
        block = source[start:end]
        self.assertIn("let monitor = file.monitor_file", block)
        self.assertIn("this.externalApiEnvMonitor = monitor;", block)
        self.assertIn("this._trackMonitor(monitor);", block)
        self.assertIn('let connectionId = this._connectSafe(monitor, "changed",', block)
        self.assertIn("if (!connectionId) {\n        this._clearExternalApiEnvMonitor();", block)
        self.assertIn("} catch (err) {\n      this._clearExternalApiEnvMonitor();", block)
        self.assertIn('let applyTarget = this.externalApiEnvApplyTarget || "voice";', block)
        self.assertIn("this._applyExternalApiEnvTarget(applyTarget);", block)

    def test_external_env_monitor_ignores_stale_changed_signals(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_watchExternalApiEnvFile: function(path)")
        end = source.index("\n  _openExternalApiEnvEditor:", start)
        block = source[start:end]
        self.assertIn('"changed", (changedMonitor, fileObj, otherFile, eventType) =>', block)
        guard = "if (this.appletRemoved || !this._lifecycleAllowsWork() || this.externalApiEnvMonitor !== monitor || changedMonitor !== monitor)"
        self.assertIn(guard, block)
        self.assertIn("this.externalApiEnvMonitor !== monitor", block)
        self.assertLess(block.index(guard), block.index("this._applyExternalApiEnvFile(true)"))
        self.assertLess(block.index('let applyTarget = this.externalApiEnvApplyTarget || "voice";'), block.index('"changed", (changedMonitor'))
        self.assertLess(block.index("this._applyExternalApiEnvFile(true)"), block.index("this._applyExternalApiEnvTarget(applyTarget);"))

    def test_signal_rollback_restores_registry_when_orphan_tracking_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_connectSafe: function(target, signal, callback, group)")
        end = source.index("\n  _trackOrphanedSignal:", start)
        block = source[start:end]
        self.assertIn("let orphanTracked = true;", block)
        self.assertIn("this._trackOrphanedSignal(target, connectionId, signalDisconnected) === true", block)
        self.assertIn("if (!signalDisconnected && !orphanTracked)", block)
        self.assertIn("let signalIndex = signals.indexOf(signalEntry);", block)
        self.assertIn("signals[restoreIndex] = signalEntry;", block)
        self.assertIn('this._recordLifecycleError("signal-registration-rollback", fallbackError);', block)

    def test_failed_external_env_monitor_cancel_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearExternalApiEnvMonitor: function()")
        end = source.index("\n  _watchExternalApiEnvFile:", start)
        block = source[start:end]
        self.assertIn("let result = monitor.cancel();", block)
        self.assertIn('if (result === false)', block)
        self.assertIn('this._recordLifecycleError("monitor-cancel", err);', block)
        self.assertIn("return false;", block)
        self.assertIn("this._untrackMonitor(monitor)", block)
        self.assertIn("this._clearExternalApiEnvMonitorReference(monitor)", block)
        self.assertIn("let clearReference = () =>", block)

    def test_failed_external_env_monitor_signal_disconnect_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_disconnectTrackedSignalsForTarget: function(target)")
        end = source.index("\n  _clearMenuItems:", start)
        signal_block = source[start:end]
        self.assertIn("let success = true;", signal_block)
        self.assertIn("success = false;", signal_block)
        self.assertIn("return success;", signal_block)

        start = source.index("_clearExternalApiEnvMonitor: function()")
        end = source.index("\n  _watchExternalApiEnvFile:", start)
        monitor_block = source[start:end]
        self.assertIn("if (!this._disconnectTrackedSignalsForTarget(monitor))", monitor_block)
        self.assertIn("return false;", monitor_block)
        self.assertIn("clearReference();", monitor_block)
        self.assertLess(
            monitor_block.index("_disconnectTrackedSignalsForTarget(monitor)"),
            monitor_block.index("monitor.cancel()"),
        )

    def test_monitor_retry_reconstructs_pending_entries_from_registry_and_reference(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_retryOrphanedMonitors: function(includeTracked)")
        end = source.index("\n  _clearExternalApiEnvMonitorReference:", start)
        block = source[start:end]
        self.assertIn("let pendingMonitors = [];", block)
        self.assertIn("let addPendingMonitor = (monitor, cancelSucceeded) =>", block)
        self.assertIn("Monitor orphan registry is unavailable", block)
        self.assertIn("let monitors = this._resourceRegistry && this._resourceRegistry.monitors;", block)
        self.assertIn("addPendingMonitor(monitor, false);", block)
        self.assertIn("addPendingMonitor(this.externalApiEnvMonitor", block)
        self.assertIn("let includeTrackedMonitors = includeTracked === true || inTeardown;", block)
        self.assertIn("if (includeTrackedMonitors && Array.isArray(monitors))", block)
        self.assertIn("if (includeTrackedMonitors) {\n      addPendingMonitor(this.externalApiEnvMonitor", block)
        self.assertIn("for (let index = pendingMonitors.length - 1;", block)

    def test_monitor_retry_reconciles_registry_with_existing_orphan_list(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_retryOrphanedMonitors: function(includeTracked)")
        end = source.index("\n  _clearExternalApiEnvMonitorReference:", start)
        block = source[start:end]
        self.assertIn("let monitors = this._resourceRegistry && this._resourceRegistry.monitors;", block)
        self.assertIn("if (includeTrackedMonitors && Array.isArray(monitors))", block)
        self.assertNotIn("if (!Array.isArray(this._orphanedMonitors) && Array.isArray(monitors))", block)
        self.assertLess(block.index("if (includeTrackedMonitors && Array.isArray(monitors))"), block.index("if (pendingMonitors.length === 0)"))

    def test_external_env_monitor_cleanup_retries_cancel_and_registry_phases(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedMonitor: function(monitor, cancelSucceeded)")
        end = source.index("\n  _nextResourceToken:", start)
        block = source[start:end]
        self.assertIn("this._orphanedMonitors = [];", block)
        self.assertIn("entry.cancelSucceeded === true", block)
        self.assertIn("let entry = {", block)
        self.assertIn("this._orphanedMonitors.push(entry);", block)
        self.assertIn('throw new Error("Monitor orphan entry could not be tracked");', block)
        self.assertIn("this._scheduleProcessCleanupRetry();", block)
        self.assertIn("entry.monitor.cancel()", block)
        self.assertIn("this._untrackMonitor(entry.monitor)", block)
        self.assertIn("this._orphanedMonitors.splice(index, 1);", block)

        clear_start = source.index("_clearExternalApiEnvMonitor: function()")
        clear_end = source.index("\n  _watchExternalApiEnvFile:", clear_start)
        clear_block = source[clear_start:clear_end]
        self.assertIn("this._externalApiEnvMonitorCancelSucceeded === true", clear_block)
        self.assertIn("this._trackOrphanedMonitor(monitor, false);", clear_block)
        self.assertIn("this._trackOrphanedMonitor(monitor, true);", clear_block)
        self.assertIn("this._untrackOrphanedMonitor(monitor)", clear_block)

        self.assertIn('this._runTeardownGuarded("teardown-orphaned-monitors", () => this._retryOrphanedMonitors());', source)

    def test_failed_external_env_monitor_untrack_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_untrackMonitor: function(monitor)")
        end = source.index("\n  _nextResourceToken:", start)
        untrack_block = source[start:end]
        self.assertIn('this._recordLifecycleError("monitor-untrack", error);', untrack_block)
        self.assertIn("let removed = this._resourceRegistry.monitors.splice(index, 1);", untrack_block)
        self.assertIn('throw new Error("Monitor registry entry could not be removed");', untrack_block)
        self.assertIn("this._resourceRegistry.monitors.indexOf(monitor) >= 0", untrack_block)
        self.assertIn("return false;", untrack_block)

        track_start = source.index("_trackMonitor: function(monitor)")
        track_end = source.index("\n  _untrackMonitor:", track_start)
        track_block = source[track_start:track_end]
        self.assertIn("let removed = monitors.pop();", track_block)
        self.assertIn("let removed = monitors.splice(index, 1);", track_block)
        self.assertIn('throw new Error("Monitor registry rollback did not remove the entry");', track_block)

        orphan_start = source.index("_untrackOrphanedMonitor: function(monitor)")
        orphan_end = source.index("\n  _retryOrphanedMonitors:", orphan_start)
        orphan_block = source[orphan_start:orphan_end]
        self.assertIn("let removed = this._orphanedMonitors.splice(index, 1);", orphan_block)
        self.assertIn("removed[0] !== entry", orphan_block)
        self.assertIn('throw new Error("Monitor orphan entry could not be removed");', orphan_block)

        retry_start = source.index("_retryOrphanedMonitors: function(includeTracked)")
        retry_end = source.index("\n  _nextResourceToken:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("this._untrackOrphanedMonitor(entry.monitor)", retry_block)
        self.assertIn("this._clearExternalApiEnvMonitorReference(entry.monitor)", retry_block)
        self.assertIn("this._trackOrphanedMonitor(entry.monitor, true);", retry_block)
        self.assertLess(untrack_block.index("try {"), untrack_block.index("this._resourceRegistry.monitors.indexOf(monitor)"))

        start = source.index("_clearExternalApiEnvMonitor: function()")
        end = source.index("\n  _watchExternalApiEnvFile:", start)
        clear_block = source[start:end]
        self.assertIn("!this._untrackMonitor(monitor)", clear_block)
        self.assertIn("this._clearExternalApiEnvMonitorReference(monitor)", clear_block)
        self.assertIn("this._trackOrphanedMonitor(monitor, true);", clear_block)
        self.assertIn("return false;", clear_block)
        self.assertIn("clearReference();", clear_block)
        clear_reference_index = clear_block.rindex("this._clearExternalApiEnvMonitorReference(monitor)")
        self.assertLess(clear_block.index("this._untrackMonitor(monitor)"), clear_reference_index)
        self.assertLess(clear_block.index("this._untrackOrphanedMonitor(monitor)"), clear_reference_index)

    def test_failed_dialog_untrack_does_not_escape_dialog_close(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_untrackDialog: function(dialog)")
        end = source.index("\n  _trackMonitor:", start)
        untrack_block = source[start:end]
        self.assertIn('this._recordLifecycleError("dialog-untrack", error);', untrack_block)
        self.assertIn("return false;", untrack_block)
        self.assertLess(untrack_block.index("try {"), untrack_block.index("this._resourceRegistry.dialogs.indexOf(dialog)"))

        start = source.index("_dialogClose: function(dialog, group)")
        end = source.index("\n  _dialogOpen:", start)
        close_block = source[start:end]
        self.assertIn("this._untrackDialog(dialog);", close_block)
        self.assertIn("this._runTeardownOperation", close_block)
        self.assertIn('this._recordLifecycleError("dialog-close", error);', close_block)
        orphan_check = close_block.index("if (isOrphaned)")
        close_attempt = close_block.index("let closeSucceeded = this._runTeardownOperation(")
        self.assertLess(orphan_check, close_attempt)
        self.assertNotIn("return true;", close_block[orphan_check:close_attempt])

        orphan_start = source.index("_untrackOrphanedDialog: function(dialog)")
        orphan_end = source.index("\n  _retryOrphanedDialogs:", orphan_start)
        orphan_block = source[orphan_start:orphan_end]
        self.assertIn("let removed = this._orphanedDialogs.splice(index, 1);", orphan_block)
        self.assertIn("removed[0] !== entry", orphan_block)
        self.assertIn('throw new Error("Dialog orphan entry could not be removed");', orphan_block)

        retry_start = source.index("_retryOrphanedDialogs: function()")
        retry_end = source.index("\n  _newSafeDialog:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("this._untrackOrphanedDialog(entry.dialog)", retry_block)
        self.assertIn("this._clearDialogReferences(entry.dialog);", retry_block)

        reference_start = source.index("_clearDialogReferences: function(dialog)")
        reference_end = source.index("\n  _trackOrphanedDialog:", reference_start)
        reference_block = source[reference_start:reference_end]
        for reference in [
            "this.clipboardOverwriteDialog",
            "this._clearClipboardOverwriteApproval();",
            "this.textInsertToken = null;",
            "this._forgetAutoInsertFingerprint(pendingInsertFingerprint)",
            "this.autoRelistenManualStopRequested = true;",
            "this.cleanupPreviewDialog",
            "this.cleanupPreviewDialogToken",
            "this.transcriptListPromptDialog",
            "this.transcriptListPromptToken",
        ]:
            self.assertIn(reference, reference_block)

        track_start = source.index("_trackOrphanedDialog: function(dialog, group, closeSucceeded, destroySucceeded)")
        track_end = source.index("\n  _untrackOrphanedDialog:", track_start)
        track_block = source[track_start:track_end]
        self.assertIn("this._scheduleProcessCleanupRetry();", track_block)

    def test_menu_orphan_untracking_verifies_registry_removal(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_untrackOrphanedMenu: function(menu)")
        end = source.index("\n  _retryOrphanedMenus:", start)
        block = source[start:end]
        self.assertIn("let removed = this._orphanedMenus.splice(index, 1);", block)
        self.assertIn("removed[0] !== entry", block)
        self.assertIn('throw new Error("Menu orphan entry could not be removed");', block)

        track_start = source.index("_trackOrphanedMenu: function(menu, propertyName, group, needsClose, signalsSucceeded, closeSucceeded, destroySucceeded)")
        track_end = source.index("\n  _untrackOrphanedMenu:", track_start)
        track_block = source[track_start:track_end]
        self.assertIn("let entry = {", track_block)
        self.assertIn("this._orphanedMenus.push(entry);", track_block)
        self.assertIn('throw new Error("Menu orphan entry could not be tracked");', track_block)

        retry_start = source.index("_retryOrphanedMenus: function()")
        retry_end = source.index("\n  _destroyAppletTooltip:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("this._clearDestroyedMenuReference(entry.menu, entry.propertyName, \"menu-orphan\")", retry_block)
        self.assertLess(retry_block.index("this._clearDestroyedMenuReference(entry.menu, entry.propertyName, \"menu-orphan\")"), retry_block.index("this._untrackOrphanedMenu(entry.menu)"))
        self.assertIn("let pendingMenus = [];", retry_block)
        self.assertIn("let addPendingMenu = (menu, propertyName, group, needsClose, signalsSucceeded, closeSucceeded, destroySucceeded) =>", retry_block)
        self.assertIn('needsClose === true || typeof menu.close === "function"', retry_block)
        self.assertIn("Menu orphan registry is unavailable", retry_block)
        self.assertIn('addPendingMenu(this.menu, "menu", "menu", true, false, false, false);', retry_block)
        self.assertIn('addPendingMenu(this._applet_context_menu, "_applet_context_menu", "context-menu", true, false, false, false);', retry_block)
        self.assertIn('addPendingMenu(this.menuManager, "menuManager", "menu-manager", false, false, true, false);', retry_block)
        self.assertIn('addPendingMenu(this._menuManager, "_menuManager", "private-menu-manager", false, false, true, false);', retry_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVING ||", retry_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVED;", retry_block)
        self.assertIn('if (!Array.isArray(this._orphanedMenus) || inTeardown) {', retry_block)
        self.assertIn("for (let index = pendingMenus.length - 1;", retry_block)
        self.assertLess(
            retry_block.index('"_closeMenuSafely",'),
            retry_block.index('"teardown-orphaned-menus", entry.menu, "disconnectAllSignals"'),
        )
        self.assertLess(
            retry_block.index('"teardown-orphaned-menus", entry.menu, "_ungrab"'),
            retry_block.index('"teardown-orphaned-menus", entry.menu, "disconnectAllSignals"'),
        )

        reference_start = source.index("_clearDestroyedMenuReference: function(menu, propertyName, errorGroup)")
        reference_end = source.index("\n  _trackOrphanedMenu:", reference_start)
        reference_block = source[reference_start:reference_end]
        self.assertIn("this[propertyName] = null;", reference_block)
        self.assertIn('throw new Error("Menu reference could not be cleared");', reference_block)
        self.assertIn('this._recordLifecycleError(errorGroup || "menu-orphan", error);', reference_block)

    def test_external_env_monitor_registration_failure_rolls_back_monitor(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_watchExternalApiEnvFile: function(path)")
        end = source.index("\n  _openExternalApiEnvEditor:", start)
        block = source[start:end]
        self.assertIn("if (!this._clearExternalApiEnvMonitor()) {", block)
        self.assertIn("let monitor = file.monitor_file", block)
        self.assertIn("this.externalApiEnvMonitor = monitor;", block)
        self.assertIn("this._trackMonitor(monitor);", block)
        self.assertLess(block.index("this.externalApiEnvMonitor = monitor;"), block.index("this._trackMonitor(monitor);"))
        self.assertIn("this._clearExternalApiEnvMonitor();", block)

    def test_dialog_and_monitor_registration_require_valid_resource_lists(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackDialog: function(dialog)")
        end = source.index("\n  _untrackDialog:", start)
        dialog_block = source[start:end]
        self.assertIn("if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.dialogs))", dialog_block)
        self.assertIn('throw new Error("Dialog registry is unavailable");', dialog_block)
        self.assertIn("if (dialogs.indexOf(dialog) < 0)", dialog_block)
        self.assertIn('throw new Error("Dialog could not be registered");', dialog_block)
        self.assertIn("let added = false;", dialog_block)
        self.assertIn("dialogs.pop();", dialog_block)
        self.assertIn("let previousLength = dialogs.length;", dialog_block)
        self.assertIn('throw new Error("Dialog registry rollback did not remove the entry");', dialog_block)
        self.assertIn('this._recordLifecycleError("dialog-registration-rollback", rollbackError);', dialog_block)

        start = source.index("_trackMonitor: function(monitor)")
        end = source.index("\n  _untrackMonitor:", start)
        monitor_block = source[start:end]
        self.assertIn("if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.monitors))", monitor_block)
        self.assertIn('throw new Error("Monitor registry is unavailable");', monitor_block)
        self.assertIn("if (monitors.indexOf(monitor) < 0)", monitor_block)
        self.assertIn('throw new Error("Monitor could not be registered");', monitor_block)
        self.assertIn("let added = false;", monitor_block)
        self.assertIn("monitors.pop();", monitor_block)
        self.assertIn('this._recordLifecycleError("monitor-registration-rollback", rollbackError);', monitor_block)

    def test_error_status_displays_backend_error_message(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('statusText = "error";', source)
        self.assertIn('statusText += " - " + this._shortMenuText(this.lastMessage, 140);', source)

    def test_applet_redacts_sensitive_local_error_messages_before_display(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const SENSITIVE_ERROR_RE =", source)
        self.assertIn("const LOCAL_PATH_ERROR_RE =", source)
        self.assertIn(r"\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b", source)
        self.assertIn(r"[a-z][a-z0-9+.-]*:\/\/[^/@\s]+@", source)
        self.assertIn(r"\/(?:home|root|run|tmp|var|etc|usr|opt|mnt|media|dev|proc|sys)\/", source)
        self.assertNotIn(r"[a-z][a-z0-9+.-]*:\/\/[^/@\s:]+:[^@\s]+@", source)
        self.assertNotIn(r"[a-z][a-z0-9+.-]*:\/\/[^/@\s:]+:[^/@\s]+@", source)
        self.assertIn("_sanitizeErrorMessage: function(value)", source)
        self.assertIn('let text = typeof value === "string" ? value : "";', source)
        self.assertIn('if (value instanceof Error && typeof value.message === "string")', source)
        self.assertIn("_payloadMessage: function(payload, fallback)", source)
        self.assertIn('if (payload && typeof payload.message === "string" && payload.message.trim() !== "")', source)
        self.assertNotIn('if (payload && payload.message) {', source)
        self.assertIn("_payloadErrorMessage: function(payload, fallback)", source)
        self.assertIn('typeof payload.error === "string" && payload.error.trim() !== ""', source)
        self.assertIn('let errorMessage = this._payloadErrorMessage(payload, _("Backend reported an error"));', source)
        self.assertNotIn('let errorMessage = payload.error || payload.message || _("Backend reported an error");', source)
        self.assertIn('return "[redacted error details]";', source)
        self.assertIn('this.lastMessage = status === "error"', source)
        self.assertIn('(typeof message === "string" ? message : "")', source)
        self.assertIn('if (typeof transcript === "string" && transcript !== "")', source)
        self.assertIn("let safeBody = this._sanitizeErrorMessage(body);", source)
        self.assertIn('let message = _("Doctor failed: ") + this._sanitizeErrorMessage(payload.error);', source)
        self.assertNotIn('let message = _("Doctor failed: ") + payload.error;', source)
        self.assertIn("this._sanitizeErrorMessage(payload.message)", source)
        self.assertIn('this._payloadMessage(payload, _("Text model backend is unavailable"))', source)
        self.assertIn('this._payloadMessage(payload, _("Ollama is not installed or not reachable"))', source)
        self.assertNotIn('payload.available === false && payload.message', source)
        self.assertNotIn('payload.message + "; " + _("opening installer...")', source)
        self.assertIn("let safeError = this._sanitizeErrorMessage(payload.error);", source)
        self.assertIn("this._populateModelMenu([], safeError);", source)
        self.assertNotIn("this._populateModelMenu([], payload.error);", source)
        self.assertIn("this._populateAlarmMenu([], safeError);", source)
        self.assertIn("this._populateInputSourceMenu([], this._sanitizeErrorMessage(payload.error));", source)
        self.assertNotIn("this._populateAlarmMenu([], payload.error);", source)
        self.assertNotIn("this._populateInputSourceMenu([], payload.error);", source)
        self.assertIn('this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);', source)
        self.assertNotIn('this._setStatus("error", payload.error, this.lastTranscript);', source)
        self.assertIn('let message = _("Ollama model installed: ") + installedModel;', source)
        self.assertNotIn('let message = payload.message || _("Ollama model installed");', source)
        self.assertNotIn("let message = payload.message || status;", source)
        self.assertNotIn('let message = String(payload.message || _("Benchmark complete"));', source)
        self.assertIn("Main.criticalNotify(title, safeBody);", source)
        self.assertIn("Main.notify(title, safeBody);", source)
        self.assertIn('safeLevel.detail = typeof safeLevel.detail === "string"', source)
        self.assertIn(': "";', source[source.index("safeLevel.detail = typeof safeLevel.detail"):source.index("safeLevel.detail = typeof safeLevel.detail") + 180])
        self.assertIn('_("Auto-Submit self-protection blocked a protected target")', source)
        self.assertNotIn('_("Auto-Submit self-protection blocked target: ") + detail', source)
        self.assertIn('setStatus("error", _("Could not open link"), this.lastTranscript);', source)
        self.assertNotIn('_("Could not open link: ") + err.message', source)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not restart applet"), this.lastTranscript);', source)
        self.assertNotIn('_("Could not restart applet: ") + String(err)', source)
        self.assertNotIn('this._notify(_("Could not open terminal"), String(err), true);', source)
        self.assertNotIn('this._notify(_("Could not start install terminal"), String(err), true);', source)
        self.assertNotIn('this._notify(_("Could not start uninstall terminal"), String(err), true);', source)
        self.assertNotIn('this._notify(_("Could not start setup terminal"), String(err), true);', source)
        self.assertIn('this._notify(_("Could not open terminal"), safeError, true);', source)
        self.assertIn('this._notify(_("Could not start install terminal"), safeError, true);', source)
        self.assertIn('this._notify(_("Could not start uninstall terminal"), safeError, true);', source)
        self.assertIn('this._notify(_("Could not start setup terminal"), safeError, true);', source)

    def test_settings_export_status_does_not_render_local_path(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertNotIn('_("Exported settings: ") + payload.path', source)
        self.assertIn('_("Exported settings")', source)

    def test_applet_registers_optional_language_specific_hotkeys(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("on_applet_clicked: function()", source)
        self.assertIn('this._runStateGuarded("menu-toggle", () => {', source)
        self.assertIn("let menu = this.menu;", source)
        self.assertIn('typeof menu.open !== "function" || typeof menu.close !== "function"', source)
        self.assertIn("this._closeMenuSafely(menu, true, true);", source)
        self.assertIn("this._closeMenuSafely(menu, false, true);", source)
        self.assertIn("this._rememberFocusedWindow();", source)
        activation_keys = schema["layout"]["activation-section"]["keys"]
        self.assertIn("primary-language-keybinding", activation_keys)
        self.assertIn("secondary-language-keybinding", activation_keys)
        self.assertIn("cancel-keybinding", activation_keys)
        self.assertEqual(schema["primary-language-keybinding"]["default"], "")
        self.assertEqual(schema["secondary-language-keybinding"]["default"], "")
        self.assertEqual(schema["cancel-keybinding"]["default"], "")
        self.assertIn('const PRIMARY_HOTKEY_ID = "speed-of-cinnamon-primary-language";', source)
        self.assertIn('const SECONDARY_HOTKEY_ID = "speed-of-cinnamon-secondary-language";', source)
        self.assertIn('const CANCEL_HOTKEY_ID = "speed-of-cinnamon-cancel";', source)
        self.assertIn('["primary-language-keybinding", "primaryLanguageKeybinding"]', source)
        self.assertIn('["cancel-keybinding", "cancelKeybinding"]', source)
        self.assertIn('this._registerHotkey(PRIMARY_HOTKEY_ID, this.primaryLanguageKeybinding', source)
        self.assertIn('this._registerHotkey(SECONDARY_HOTKEY_ID, this.secondaryLanguageKeybinding', source)
        self.assertIn('this._registerHotkey(CANCEL_HOTKEY_ID, this.cancelKeybinding', source)
        self.assertIn('let accelerator = typeof binding === "string" ? binding.trim() : "";', source)
        self.assertIn('let value = typeof binding === "string" ? binding.trim() : "";', source)
        self.assertIn("this._hotkeyDefinitions = {};", source)
        self.assertIn('return Main.keybindingManager.addHotKey(name, accelerator, this._guardStateCallback("hotkeys", callback, undefined)) === true;', source)
        self.assertIn('let hasBinding = accelerator.split("::").some((part) => String(part || "").trim() !== "");', source)
        self.assertIn('let previous = this._hotkeyDefinitions && this._hotkeyDefinitions[name]', source)
        self.assertIn('this._hotkeyDefinitions[name] = previous;', source)
        self.assertIn('this._startWithLanguage(this._primaryLanguage())', source)
        self.assertIn('this._startWithLanguage(this._secondaryLanguage())', source)
        self.assertIn('this._cancelRecording();', source)
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
        self.assertIn("this._connectSafe(selectPrimary, \"activate\", () => this._setActiveLanguage(primary", source)
        self.assertIn("this._connectSafe(startSecondary, \"activate\", () => this._startWithLanguage(secondary, true))", source)
        self.assertIn("if (!this.activeLanguageExplicit || (current !== primary && current !== secondary))", source)
        self.assertIn('let language = typeof value === "string" ? value.trim().toLowerCase() : "";', source)
        self.assertIn('LANGUAGE_CODES.indexOf(language) >= 0 ? language : safeFallback', source)
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
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "recorder", "recorder", this._onRecorderSettingsChanged, null)', source)
        self.assertIn('this._commitSettingValue("recorder", "recorder"', source)
        self.assertIn('this._setMenuItemLabelSafely(this.recorderItem, _("Recorder: ") + this._recorderLabel(this._normalizeRecorder(this.recorder)))', source)
        self.assertIn('this._setStatusPreservingRecording(this.status, _("Recorder for next recording: ") + label', source)

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
        self.assertIn('typeof seconds === "number" && isFinite(seconds)', source)
        self.assertIn("_selectRecordingLimit: function(seconds)", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem((hasPreset ? "[ ] " : "[x] ") + _("Custom seconds...")', source)
        self.assertIn('this._connectSafe(custom, "activate", () => this._promptCustomRecordingLimit())', source)
        self.assertIn("_customRecordingLimitPromptArgs: function()", source)
        self.assertIn('"--title=Duration"', source)
        self.assertIn('"--entry-text=" + current', source)
        self.assertIn("_parseCustomRecordingLimit: function(value)", source)
        self.assertIn('this.lastMessage = _("Duration must be whole seconds.")', source)
        self.assertIn('this.lastMessage = _("Duration must be between 0 and 3600 seconds.")', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", this._onRecordingLimitSettingsChanged, null)', source)
        self.assertIn('this._commitSettingValue("maxSeconds", "max-seconds"', source)
        self.assertIn('"--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this._setMenuItemLabelSafely(this.recordingLimitItem, _("Duration: ") + this._formatSeconds(this._normalizeRecordingLimit(this.maxSeconds)))', source)
        self.assertIn('this._setStatusPreservingRecording(this.status, _("Duration for next recording: ") + label', source)


    def test_applet_exposes_auto_paste_title_marker(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("auto-paste-window-title", schema["layout"]["output-section"]["keys"])
        self.assertEqual(schema["auto-paste-window-title"]["default"], "codex")
        self.assertIn("Built-in marker names match known window classes/app IDs", schema["auto-paste-window-title"]["tooltip"])
        self.assertIn("codex matches known terminal identities and the window title", schema["auto-paste-window-title"]["tooltip"])
        self.assertIn("custom strings match the full window title case-insensitively", schema["auto-paste-window-title"]["tooltip"])
        self.assertIn("not after Clipboard only", schema["auto-paste-window-title"]["tooltip"])
        self.assertIn("Empty disables Auto-Submit", schema["auto-paste-window-title"]["tooltip"])
        self.assertIn('const DEFAULT_AUTO_PASTE_TITLE = "codex";', source)
        self.assertIn('const AUTO_PASTE_TITLE_PRESETS = [\n  "codex",\n  "Terminal",\n  "PDF",\n  "Excel",\n  "Telegram",\n  "Teams"\n];', source)
        self.assertIn("const AUTO_PASTE_IDENTITY_MARKERS = {", source)
        self.assertIn('"Terminal"', source)
        self.assertIn('"PDF"', source)
        self.assertIn('"pdf": [', source)
        self.assertIn('"org.kde.okular"', source)
        self.assertIn('"org.gnome.evince"', source)
        self.assertIn('"org.pwmt.zathura"', source)
        self.assertIn('"mupdf"', source)
        self.assertIn('"qpdfview"', source)
        self.assertIn('"sioyek"', source)
        self.assertIn('"xpdf"', source)
        self.assertIn('"Excel"', source)
        self.assertIn('"excel": [', source)
        self.assertIn('"libreoffice-calc"', source)
        self.assertIn('"microsoft excel"', source)
        self.assertIn('"onlyoffice-desktopeditors"', source)
        self.assertIn('"planmaker"', source)
        self.assertIn('"Teams"', source)
        self.assertIn('"teams": [', source)
        self.assertIn('"com.microsoft.teams"', source)
        self.assertIn('"dev.wrapbox.teamsforlinux"', source)
        self.assertIn('"com.github.ismaelmartinez.teams_for_linux"', source)
        self.assertIn('"msteams"', source)
        self.assertIn('"Telegram"', source)
        self.assertIn('"telegram": [', source)
        self.assertIn('"org.telegram.desktop"', source)
        self.assertIn('"telegramdesktop"', source)
        self.assertIn('let allowed = AUTO_PASTE_IDENTITY_MARKERS[key] || null;', source)
        self.assertIn('if (!allowed) {\n      return false;\n    }', source)
        self.assertIn('_normalizedAutoPasteWindowTitle: function(value)', source)
        self.assertIn('this.autoPasteWindowTitle = DEFAULT_AUTO_PASTE_TITLE;', source)
        self.assertIn('["auto-paste-window-title", "autoPasteWindowTitle"]', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "auto-paste-window-title", "autoPasteWindowTitle", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('this.autoPasteItem = new PopupMenu.PopupSubMenuMenuItem(_("Auto-Submit: codex"))', source)
        self.assertIn('_populateAutoPasteMenu: function()', source)
        self.assertIn('for (let preset of AUTO_PASTE_TITLE_PRESETS)', source)
        self.assertIn('this._connectSafe(disabled, "activate", () => this._setAutoPasteTitles([]))', source)
        self.assertIn('let custom = new PopupMenu.PopupIconMenuItem(_("Custom string...")', source)
        self.assertIn("_autoPastePromptArgs: function()", source)
        self.assertIn('"--entry"', source)
        self.assertIn('"--title=Auto-Submit"', source)
        self.assertIn('Built-in marker names match known window classes/app IDs; codex matches known terminal identities and the window title. Custom strings match the full window title case-insensitively. Empty disables Auto-Submit.', source)
        self.assertIn('"--entry-text=" + current', source)
        self.assertIn('if (!this._findTrustedProgramInPath("zenity"))', source)
        self.assertIn('this._spawnText(promptArgs, (output, result) => {', source)
        self.assertIn("result.startupFailed === true", source)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not open Auto-Submit prompt")', source)
        self.assertIn('this._setAutoPasteTitles(this._autoPasteTitleValues(output));', source)
        self.assertIn('_autoPasteTitleValues: function(value)', source)
        self.assertIn('raw.split(/[,\\n\\r]+/)', source)
        self.assertIn('_normalizeAutoPasteTitle: function(value)', source)
        self.assertIn('let raw = typeof value === "string" ? value.replace(NUL_RE, "").slice(0, MAX_SETTING_TEXT_CHARS) : "";', source)
        self.assertIn("let seen = Object.create(null);", source)
        self.assertIn('return (typeof value === "string" ? value : "").replace(NUL_RE, "").trim().toLowerCase();', source)
        self.assertIn('_toggleAutoPasteTitle: function(value)', source)
        self.assertIn('this._setAutoPasteTitles([])', source)
        self.assertIn('_windowTitleMatchesAutoPaste: function()', source)
        self.assertIn('_markerAllowsAutoPasteIdentity: function(marker)', source)
        self.assertIn('if (key === "codex") {\n          if (title.indexOf(key) >= 0 || this._windowIdentityMatchesAutoPaste(marker)) {', source)
        self.assertIn('if (AUTO_PASTE_IDENTITY_MARKERS[key]) {', source)
        self.assertIn('if (key === "codex") {', source)
        self.assertIn('if (this._windowIdentityMatchesAutoPaste(marker)) {', source)
        self.assertIn('_windowIdentityMatchesAutoPaste: function(marker)', source)
        self.assertIn('this._windowProbeValue(this.targetWindow, "get_title")', source)
        self.assertIn('let title = this._normalizedAutoPasteWindowTitle(this._windowProbeValue(this.targetWindow, "get_title") || this.targetWindowXTitle || "");', source)
        self.assertIn('if (title === key) {', source)
        self.assertIn('AUTO_PASTE_IDENTITY_MARKERS[key] || null', source)
        self.assertIn('for (let marker of markers)', source)
        self.assertIn('String(marker || "").trim().toLowerCase()', source)
        self.assertIn('let autoPasteTarget = method === "clipboard-paste" && this._windowTitleMatchesAutoPaste();', source)
        self.assertIn('let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;', source)
        self.assertIn('let suppressAutoPasteEnter = method !== "clipboard-paste" || submitWithReturn;', source)
        self.assertIn('let text = this._preparedTranscriptText(transcript, suppressAutoPasteEnter);', source)
        self.assertIn('_copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)', source)
        self.assertIn('_pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {', source)
        self.assertIn("completeOnce(pasteCompleted);", source)
        self.assertIn('_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, operationGuard)', source)
        self.assertIn('_spawnKeyboardWhenClipboardReady(args, followUpArgs, expectedClipboardText, Date.now() + CLIPBOARD_READY_TIMEOUT_MS, expectedTargetWindow, complete, isCurrentOperation);', source)
        self.assertIn('_spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback, operationGuard)', source)
        self.assertIn('completionCallback(false);', source)
        self.assertIn('return null;', source)
        self.assertNotIn('if (method === "clipboard-paste" && !autoPasteTarget) {', source)
        self.assertNotIn('Copied to clipboard; Auto-Paste target not enabled', source)
        self.assertNotIn('Auto-Enter', source)
        self.assertIn('this._normalizedAutoPasteWindowTitle(this._windowProbeValue(this.targetWindow, "get_title") || this.targetWindowXTitle || "")', source)
        self.assertIn('this._pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {', source)
        self.assertIn("CLIPBOARD_READY_RETRY_MS", source)
        self.assertIn("CLIPBOARD_READY_TIMEOUT_MS", source)
        self.assertIn("_spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback, operationGuard)", source)
        self.assertIn('this._setStatus("error", _("Clipboard did not confirm new text before automatic paste"), this.lastTranscript);', source)
        self.assertNotIn('this._preparedTranscriptText(transcript, submitWithReturn)', source)

    def test_typing_delay_has_backend_limits(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertEqual(schema["typing-delay-ms"]["min"], 0)
        self.assertEqual(schema["typing-delay-ms"]["max"], 10000)
        self.assertIn("_normalizeTypingDelayMs: function(delay)", source)
        self.assertIn('typeof delay === "number" && isFinite(delay)', source)
        self.assertIn("_normalizeTypingDelayMs(this.typingDelayMs)", source)
        self.assertIn('"--typing-delay-ms", String(this._normalizeTypingDelayMs(this.typingDelayMs))', source)
        self.assertIn('_typeTextAfterFocus: function(text, completionCallback, operationGuard) {', source)

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
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Configure shortcuts"), "preferences-desktop-keyboard-symbolic"', source)
        self.assertIn("_openShortcutSettings: function()", source)
        self.assertIn('this._openAppletSettings(_("Opened Cinnamon shortcut settings"));', source)
        self.assertIn('let xletSettings = this._findTrustedProgramInPath("xlet-settings");', source)
        self.assertIn('let args = [xletSettings, "applet", UUID];', source)
        self.assertIn("this._runBoundedSubprocess(this._coerceSpawnArgs(args), {}, {", source)
        self.assertNotIn('let args = ["xlet-settings", "applet", UUID];', source)
        self.assertIn("_populateShortcutMenu: function()", source)
        self.assertIn("_shortcutRows: function()", source)
        self.assertIn("_formatKeybinding: function(binding)", source)
        self.assertIn("_shortcutReferenceText: function()", source)
        self.assertIn("_copyShortcutReference: function()", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy shortcut reference"), "edit-copy-symbolic"', source)
        self.assertIn("this._setClipboardText(this._shortcutReferenceText())", source)
        self.assertIn('this._setStatusPreservingRecording("done", _("Copied shortcut reference"), this.lastTranscript)', source)
        self.assertIn("this._populateShortcutMenu();", source)

    def test_ui_subprocess_launchers_handle_async_exit_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        settings_start = source.index("_openAppletSettings: function()")
        settings_end = source.index("\n  _openSetupGuide:", settings_start)
        settings_block = source[settings_start:settings_end]
        self.assertIn('_("Cinnamon applet settings process exited unexpectedly")', settings_block)
        self.assertIn("result.error || result.timedOut || result.outputTooLarge", settings_block)
        self.assertIn("let settingsToken = {};", settings_block)
        self.assertIn("if (this.settingsWindowToken)", settings_block)
        self.assertIn("this.settingsWindowToken !== settingsToken", settings_block)
        self.assertNotIn("}, function() {});", settings_block)

        terminal_start = source.index("_runTerminalWorkflow: function(title, command, openedMessage, cancelOllamaFlow, ollamaFlowToken)")
        terminal_end = source.index("\n  _terminalWorkflowScript:", terminal_start)
        terminal_block = source[terminal_start:terminal_end]
        self.assertIn("let ollamaFlowContinues = cancelOllamaFlow === true", terminal_block)
        self.assertIn("this.ollamaModelFlowToken === ollamaFlowToken", terminal_block)
        self.assertIn("if (this._hasActiveRecordingState() && !ollamaFlowContinues)", terminal_block)
        self.assertIn("if (!ollamaFlowContinues && this._hasLocalProcessingWorkflow())", terminal_block)
        self.assertIn('this._setStatus(this.status, _("Finish the current recording before starting a terminal workflow"), this.lastTranscript);', terminal_block)
        self.assertIn('_("Terminal process exited unexpectedly")', terminal_block)
        self.assertIn("result.error || result.timedOut || result.outputTooLarge", terminal_block)
        self.assertIn(
            "if (this.terminalWorkflowToken !== terminalWorkflowToken) {\n"
            "          if (!this.terminalWorkflowToken) {\n"
            "            this.terminalWorkflowRunning = false;\n"
            "          }\n"
            "          return;\n"
            "        }\n"
            "        this.terminalWorkflowRunning = false;",
            terminal_block,
        )
        self.assertNotIn("}, function() {});", terminal_block)

    def test_history_refresh_ignores_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        refresh_start = source.index("_refreshHistory: function()")
        refresh_end = source.index("\n  _listAllTranscripts:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("let refreshToken = {};", refresh_block)
        self.assertIn("this.historyRefreshToken = refreshToken;", refresh_block)
        self.assertIn("this.historyRefreshToken !== refreshToken", refresh_block)
        self.assertIn("!this._canMutateMenu(this.historyItem)", refresh_block)
        self.assertIn('this._terminateProcessesByGroup("history-refresh")', refresh_block)
        self.assertIn('resourceGroup: "history-refresh"', refresh_block)
        self.assertLess(
            refresh_block.index('this._terminateProcessesByGroup("history-refresh")'),
            refresh_block.index("let refreshToken = {};")
        )
        self.assertIn("let canReportHistoryStatus = () => !this.isCommandRunning &&", refresh_block)
        self.assertIn("if (canReportHistoryStatus())", refresh_block)

    def test_alarm_refresh_ignores_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        refresh_start = source.index("_refreshAlarmMenu: function()")
        refresh_end = source.index("\n  _populateAlarmMenu:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("let refreshToken = {};", refresh_block)
        self.assertIn("this.alarmMenuRefreshToken = refreshToken;", refresh_block)
        self.assertIn("this.alarmMenuRefreshToken !== refreshToken", refresh_block)
        self.assertIn("this.alarmActionToken || this.alarmCheckToken", refresh_block)
        self.assertIn("!this._canMutateMenu(this.alarmItem)", refresh_block)
        self.assertIn('this._terminateProcessesByGroup("alarm-menu-refresh")', refresh_block)
        self.assertIn('resourceGroup: "alarm-menu-refresh"', refresh_block)
        self.assertLess(
            refresh_block.index('this._terminateProcessesByGroup("alarm-menu-refresh")'),
            refresh_block.index("let refreshToken = {};")
        )
        self.assertIn("if (this.alarmMenuRefreshToken || this.alarmActionToken || this.alarmCheckToken)", refresh_block)
        self.assertIn("this.alarmMenuRefreshToken = null;", refresh_block)
        self.assertLess(refresh_block.index("if (this.alarmMenuRefreshToken || this.alarmActionToken || this.alarmCheckToken)"), refresh_block.index("let refreshToken = {};"))
        self.assertLess(refresh_block.index("this.alarmMenuRefreshToken = null;"), refresh_block.index("if (payload.error)"))
        self.assertIn("let canReportAlarmStatus = () => !this.isCommandRunning &&", refresh_block)
        self.assertIn("if (canReportAlarmStatus())", refresh_block)

    def test_alarm_checks_do_not_spawn_concurrent_backend_processes(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_checkAlarms: function(manual)")
        end = source.index("\n  _refreshInputSourceMenu:", start)
        block = source[start:end]
        guard = "manual && (this._hasActiveRecordingState() || this.isCommandRunning || this._hasLocalProcessingWorkflow())"
        self.assertIn("this.alarmMenuRefreshToken || this._statusCommandRunning ||", block)
        self.assertIn("this._hasLocalProcessingWorkflow() ||", block)
        self.assertIn(guard, block)
        self.assertIn("let checkToken = {};", block)
        self.assertLess(block.index(guard), block.index("let checkToken = {};"))

    def test_alarm_actions_do_not_spawn_concurrent_backend_processes(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, token in [
            ("_setAlarmEnabled: function(id, enabled)", "\n  _removeAlarm:", "this.alarmActionToken"),
            ("_removeAlarm: function(id)", "\n  _checkAlarms:", "this.alarmActionToken"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(
                f"if ({token} || this.alarmCheckToken || this.alarmMenuRefreshToken || this.isCommandRunning ||\n"
                "        this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())",
                block,
            )
            self.assertIn("let actionToken = {};", block)
            self.assertLess(
                block.index(f"if ({token} || this.alarmCheckToken || this.alarmMenuRefreshToken || this.isCommandRunning ||"),
                block.index("let actionToken = {};"),
            )

    def test_alarm_enable_reports_when_backend_cannot_find_alarm(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_setAlarmEnabled: function(id, enabled)")
        end = source.index("\n  _removeAlarm:", start)
        block = source[start:end]
        self.assertIn("let alarmFound = Boolean(", block)
        self.assertIn('payload.alarm &&', block)
        self.assertIn('typeof payload.alarm === "object"', block)
        self.assertIn("!Array.isArray(payload.alarm)", block)
        self.assertIn(': _("Alarm not found")', block)

    def test_interactive_dialogs_do_not_spawn_concurrent_flows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, guard in [
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:", "this.customLimitPromptToken"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:", "this.customLimitPromptToken"),
            ("_configureAutoPaste: function()", "\n  _setAutoPasteTitles:", "this.autoPastePromptToken"),
            ("_selectBenchmarkAudioFile: function()", "\n  _benchmarkDownloadedModels:", "this.benchmarkFlowToken"),
            ("_activateOllamaTextModelFlow: function()", "\n  _ollamaModelPromptArgs:", "this.ollamaModelFlowToken"),
            ("_chooseOllamaTextModel: function()", "\n  _promptChooseOllamaTextModel:", "this.ollamaModelFlowToken"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(guard, block)
            self.assertLess(block.index(guard), block.index('if (!this._findTrustedProgramInPath("zenity"))'))

    def test_interactive_prompt_tokens_release_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, builder_name, token_name, error_group in [
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:", "recordingPromptArgs", "_customRecordingLimitPromptArgs", "customLimitPromptToken", "recording-limit-prompt"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:", "transcriptPromptArgs", "_customTranscriptLimitPromptArgs", "customLimitPromptToken", "transcript-limit-prompt"),
            ("_configureAutoPaste: function()", "\n  _setAutoPasteTitles:", "promptArgs", "_autoPastePromptArgs", "autoPastePromptToken", "auto-paste-prompt"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {args_name};", block)
            self.assertIn(f"{args_name} = this.{builder_name}();", block)
            self.assertIn(f"this.{token_name} = null;", block)
            self.assertIn(f'this._recordLifecycleError("{error_group}", error);', block)

    def test_output_settings_cancel_stale_insert_and_prompt_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_cancelTextInsertForSettingsChange: function()", source)
        cancel_start = source.index("_cancelTextInsertForSettingsChange: function()")
        cancel_end = source.index("\n  on_applet_clicked:", cancel_start)
        cancel_block = source[cancel_start:cancel_end]
        self.assertIn("this.textInsertToken = null;", cancel_block)
        self.assertIn('let pendingInsertFingerprint = String(this.autoInsertPendingFingerprint || "");', cancel_block)
        self.assertIn("let fingerprintCleanupSucceeded = true;", cancel_block)
        self.assertIn("fingerprintCleanupSucceeded = this._forgetAutoInsertFingerprint(pendingInsertFingerprint) !== false;", cancel_block)
        self.assertIn("if (fingerprintCleanupSucceeded && this.autoInsertPendingFingerprint === pendingInsertFingerprint)", cancel_block)
        self.assertIn("this._clearClipboardOverwriteApproval();", cancel_block)
        self.assertIn("let dialogCleanupSucceeded = true;", cancel_block)
        self.assertIn("this._dialogClose(this.clipboardOverwriteDialog, \"clipboard-overwrite\")", cancel_block)
        self.assertIn("this._setStatusPreservingRecording(\"error\", _(\"Clipboard overwrite prompt could not be stopped\")", cancel_block)
        self.assertIn("let pasteTimerCleanupSucceeded = this._clearPasteTimer() !== false;", cancel_block)
        self.assertIn('this._terminateProcessesByGroup("keyboard") === false', cancel_block)
        self.assertIn('this._terminateProcessesByGroup("clipboard") === false', cancel_block)
        self.assertIn('this._terminateProcessesByGroup("x11") === false', cancel_block)
        self.assertIn("if (!fingerprintCleanupSucceeded)", cancel_block)
        self.assertIn("if (!pasteTimerCleanupSucceeded)", cancel_block)
        self.assertIn("if (!dialogCleanupSucceeded)", cancel_block)
        self.assertIn("let cancellationSucceeded = true;", cancel_block)
        self.assertIn("this.textInsertCancellationFailed = !cancellationSucceeded;", cancel_block)
        self.assertIn("return cancellationSucceeded;", cancel_block)
        self.assertIn("if (hadInsertToken && this.autoRelistenPending)", cancel_block)
        self.assertIn("this.autoRelistenPendingToken = \"\";", cancel_block)
        self.assertIn("this.autoRelistenPendingLanguage = \"\";", cancel_block)
        self.assertIn("this.autoRelistenManualStopRequested = true;", cancel_block)

        finish_start = source.index("_finishAppletTextInsert: function(payload)")
        finish_end = source.index("\n  _ensureAutoRelistenPendingForDonePayload:", finish_start)
        finish_block = source[finish_start:finish_end]
        self.assertIn("this.autoInsertPendingFingerprint = insertFingerprint;", finish_block)
        self.assertIn("let clearPendingFingerprint = () =>", finish_block)
        self.assertIn("let releaseFingerprint = () =>", finish_block)
        self.assertIn("let released = this._forgetAutoInsertFingerprint(insertFingerprint) !== false;", finish_block)
        self.assertIn("this.textInsertCancellationFailed = true;", finish_block)
        self.assertIn("releaseFingerprint();", finish_block)
        self.assertIn("clearPendingFingerprint();", finish_block)
        self.assertIn("if (result) {\n        clearPendingFingerprint();\n        inserted = true;", finish_block)

    def test_failed_text_insert_cancellation_blocks_new_insertions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        helper_start = source.index("_hasTrackedProcessGroup: function(group)")
        helper_end = source.index("\n  _cancelAllCancellables:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn('this._recordLifecycleError("process-state", error);', helper_block)
        insert_start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        insert_end = source.index("\n  _restartRelistenRecording:", insert_start)
        insert_block = source[insert_start:insert_end]
        self.assertIn("if (this.textInsertCancellationFailed)", insert_block)
        helper_start = source.index("_hasPendingTextInsertCleanup: function()")
        helper_end = source.index("\n  _hasLocalProcessingWorkflow:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn('String(this.autoInsertPendingFingerprint || "") !== ""', helper_block)
        self.assertIn("this._hasPendingTextInsertResources()", helper_block)
        self.assertIn("_hasPendingTextInsertResources: function()", helper_block)
        self.assertIn("this._hasPendingTextInsertResources())", insert_block)
        self.assertIn("let timerCleanupStillPending = false;", insert_block)
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedTimers();", insert_block)
        self.assertIn("Boolean(this.pasteTimer)", insert_block)
        self.assertIn("Boolean(timers && timers.paste)", insert_block)
        self.assertIn("let fingerprintCleanupStillPending = false;", insert_block)
        self.assertIn('let protectedFingerprint = String(protectedInsertFingerprint || "");', insert_block)
        self.assertIn('let pendingInsertFingerprint = String(this.autoInsertPendingFingerprint || "");', insert_block)
        self.assertIn('pendingInsertFingerprint !== "" && pendingInsertFingerprint !== protectedFingerprint', insert_block)
        self.assertIn("let fingerprintCleanupSucceeded = this._forgetAutoInsertFingerprint(pendingInsertFingerprint) !== false;", insert_block)
        self.assertIn("fingerprintCleanupStillPending = !fingerprintCleanupSucceeded ||", insert_block)
        self.assertIn("Boolean(this.clipboardOverwriteDialog)", insert_block)
        self.assertIn("let cancellationStillPending = timerCleanupStillPending || fingerprintCleanupStillPending ||", insert_block)
        self.assertIn("[\"keyboard\", \"clipboard\", \"x11\"].some", insert_block)
        self.assertIn('_("Previous text insertion is still stopping; try again shortly")', insert_block)

        output_start = source.index("_onOutputSettingsChanged: function()")
        output_end = source.index("\n  _onTextOutputSettingsChanged:", output_start)
        self.assertIn("this._cancelTextInsertForSettingsChange();", source[output_start:output_end])

        text_output_start = source.index("_onTextOutputSettingsChanged: function()")
        text_output_end = source.index("\n  _onTranscriptRetentionSettingsChanged:", text_output_start)
        text_output_block = source[text_output_start:text_output_end]
        self.assertIn("this._cancelTextInsertForSettingsChange();", text_output_block)
        self.assertIn("this.customLimitPromptToken = null;", text_output_block)
        self.assertIn("this.autoPastePromptToken = null;", text_output_block)
        self.assertIn('this._terminateProcessesByGroup("settings-prompt")', text_output_block)
        self.assertLess(
            text_output_block.index("this.customLimitPromptToken = null;"),
            text_output_block.index('this._terminateProcessesByGroup("settings-prompt")')
        )

        for method, next_method in [
            ("_onRecordingLimitSettingsChanged: function()", "\n  _onRecordingOptionsChanged:"),
            ("_onTranscriptRetentionSettingsChanged: function()", "\n  _onRecorderSettingsChanged:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this.customLimitPromptToken = null;", block)
            self.assertIn("this.autoPastePromptToken = null;", block)
            self.assertIn('this._terminateProcessesByGroup("settings-prompt")', block)
            self.assertLess(
                block.index("this.customLimitPromptToken = null;"),
                block.index('this._terminateProcessesByGroup("settings-prompt")')
            )

    def test_settings_prompts_use_a_shared_cancellable_process_group(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:"),
            ("_configureAutoPaste: function()", "\n  _setAutoPasteTitles:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn('resourceGroup: "settings-prompt"', block)

    def test_clipboard_keyboard_chain_honors_insert_operation_guard(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for signature in [
            "_pasteClipboardAfterFocus: function(sendEnter, expectedClipboardText, completionCallback, operationGuard)",
            "_typeTextAfterFocus: function(text, completionCallback, operationGuard)",
            "_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, operationGuard)",
            "_spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback, operationGuard)",
            "_spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback, operationGuard)",
        ]:
            self.assertIn(signature, source)
        self.assertIn("if (!isCurrentOperation() || !this._lifecycleAllowsWork()", source)
        self.assertIn("if (this.appletRemoved || !isCurrentOperation())", source)
        self.assertIn("completionCallback, isCurrentOperation", source)

    def test_keyboard_insert_timer_failures_complete_the_operation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_completeKeyboardInsertFailure: function(completionCallback, message, error)", source)
        focus_start = source.index("_spawnKeyboardAfterFocus: function(")
        focus_end = source.index("\n  _spawnKeyboardWhenClipboardReady:", focus_start)
        focus_block = source[focus_start:focus_end]
        self.assertIn("try {\n        this._spawnKeyboardWhenClipboardReady", focus_block)
        self.assertIn('this._completeKeyboardInsertFailure(complete, _("Keyboard insert failed"), error);', focus_block)
        self.assertIn('this._recordLifecycleError("keyboard-insert-completion", error);', focus_block)

        for method, next_method in [
            ("_spawnKeyboardWhenClipboardReady: function(", "\n  _spawnKeyboardProcess:"),
            ("_spawnKeyboardArgs: function(", "\n  _finishAppletTextInsert:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("let failAsync = (error, message)", block)
            self.assertIn("failAsync(error);", block)

    def test_keyboard_helper_probe_failures_complete_the_operation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_pasteClipboardAfterFocus: function(", "\n  _typeTextAfterFocus:"),
            ("_typeTextAfterFocus: function(", "\n  _coerceTypeText:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("try {", block)
            self.assertIn('this._findTrustedProgramInPath("xdotool")', block)
            self.assertIn('this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);', block)
            self.assertIn("return false;", block)

    def test_menu_payload_arrays_and_entries_are_shape_safe(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, variable in [
            ("_populateAlarmMenu: function(alarms, summary, message)", "\n  _addAlarmMenuEntry:", "alarms"),
            ("_populateInputSourceMenu: function(sources, message)", "\n  _selectInputSource:", "sources"),
            ("_populateModelMenu: function(models, message)", "\n  _populateExternalApiVoiceMenu:", "models"),
            ("_populateTextModelMenu: function(models, message, provider)", "\n  _canMutateMenu:", "models"),
            ("_populateHistoryMenu: function(transcripts)", "\n  _copyHistoryTranscript:", "transcripts"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"{variable} = Array.isArray({variable}) ? {variable} : [];", block)
            self.assertIn(f"{variable} = {variable}.filter(", block)
            self.assertIn(f"if (!{variable[:-1] if variable.endswith('s') else variable} || typeof {variable[:-1] if variable.endswith('s') else variable} !== \"object\")", block)

        for method, next_method in [
            ("_populateInputSourceMenu: function(sources, message)", "\n  _selectInputSource:"),
            ("_populateModelMenu: function(models, message)", "\n  _populateExternalApiVoiceMenu:"),
            ("_populateTextModelMenu: function(models, message, provider)", "\n  _canMutateMenu:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn('let messageText = typeof message === "string" ? message.trim() : "";', block)
            self.assertIn('if (messageText !== "")', block)

        for method, next_method, parameter in [
            ("_addAlarmMenuEntry: function(alarm)", "\n  _copyAlarmCommands:", "alarm"),
            ("_addModelMenuEntry: function(model, parentMenu)", "\n  _isEnglishLanguage:", "model"),
            ("_addTextModelMenuEntry: function(model, backend)", "\n  _textPolishingPresetLabel:", "model"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"if (!{parameter} || typeof {parameter} !== \"object\")", block)

        alarm_start = source.index("_addAlarmMenuEntry: function(alarm)")
        alarm_end = source.index("\n  _copyAlarmCommands:", alarm_start)
        alarm_block = source[alarm_start:alarm_end]
        self.assertIn('id = this._coerceCliTextArg(alarm.id, "alarm id").trim();', alarm_block)

        check_start = source.index("_checkAlarms: function(manual)")
        check_end = source.index("\n  _refreshInputSourceMenu:", check_start)
        check_block = source[check_start:check_end]
        self.assertIn("let due = Array.isArray(payload.due)", check_block)
        self.assertIn('payload.due.filter((alarm) => alarm && typeof alarm === "object")', check_block)

    def test_backend_text_is_bounded_before_ui_display(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const MAX_UI_MESSAGE_CHARS = 512;", source)
        self.assertIn("_uiMessageText: function(value)", source)
        status_start = source.index("_setStatus: function(status, message, transcript)")
        status_end = source.index("\n  _maybeNotify:", status_start)
        status_block = source[status_start:status_end]
        self.assertIn("let safeMessage = (typeof message === \"string\" ? message : \"\");", status_block)
        self.assertIn("this._uiMessageText(this._sanitizeErrorMessage(safeMessage))", status_block)
        self.assertIn("this._uiMessageText(safeMessage)", status_block)

        doctor_start = source.index("_setDoctorSummary: function(message)")
        doctor_end = source.index("\n  _openAppletSettings:", doctor_start)
        self.assertIn("this.doctorSummaryText = this._uiMessageText(String(message || \"\"));", source[doctor_start:doctor_end])

        for method, next_method in [
            ("_populateAlarmMenu: function(alarms, summary, message)", "\n  _addAlarmMenuEntry:"),
            ("_addAlarmMenuEntry: function(alarm)", "\n  _copyAlarmCommands:"),
            ("_addModelMenuEntry: function(model, parentMenu)", "\n  _isEnglishLanguage:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            self.assertIn("_uiMessageText", source[start:end])
        text_status_start = source.index("_setTextOptionStatus: function(message)")
        text_status_end = source.index("\n  _toggleAppendSpace:", text_status_start)
        self.assertIn('this._setStatusPreservingRecording("ready", message, this.lastTranscript);', source[text_status_start:text_status_end])

    def test_nested_backend_payloads_are_shape_safe(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        doctor_start = source.index("_applyDoctorPayload: function(payload, startupCheck)")
        doctor_end = source.index("\n  _applyLegacyDoctorPayload:", doctor_start)
        self.assertIn("let warnings = Array.isArray(configured.warnings)", source[doctor_start:doctor_end])
        self.assertIn('let section = configured[name] && typeof configured[name] === "object" ? configured[name] : {};', source[doctor_start:doctor_end])
        self.assertIn('let detail = typeof section.detail === "string" ? section.detail.trim() : "";', source[doctor_start:doctor_end])
        self.assertIn('missing.push(name + ": " + (detail || "not ready"));', source[doctor_start:doctor_end])
        self.assertNotIn('missing.push(name + ": " + (section.detail || "not ready"));', source[doctor_start:doctor_end])

        legacy_start = source.index("_applyLegacyDoctorPayload: function(payload, startupCheck)")
        legacy_end = source.index("\n  _presentDoctorResult:", legacy_start)
        legacy_block = source[legacy_start:legacy_end]
        self.assertIn("let checks = Array.isArray(payload.checks) ? payload.checks : [];", legacy_block)
        self.assertIn('if (!check || typeof check !== "object")', legacy_block)
        self.assertIn('let name = typeof check.name === "string" ? check.name.trim() : "";', legacy_block)
        self.assertNotIn('let name = String(check.name || "").trim();', legacy_block)

        language_start = source.index("_voiceModelSupportsCurrentLanguage: function(model)")
        language_end = source.index("\n  _languageMatches:", language_start)
        language_block = source[language_start:language_end]
        self.assertIn("let languages = Array.isArray(model.languages)", language_block)
        self.assertIn('model.languages.filter((language) => typeof language === "string" && language.trim() !== "")', language_block)
        match_start = source.index("_languageMatches: function(language, allowed)")
        match_end = source.index("\n  _voiceModelSupportsLanguage:", match_start)
        match_block = source[match_start:match_end]
        self.assertIn('if (typeof allowed !== "string" || allowed.trim() === "")', match_block)

        ollama_start = source.index("_ollamaModelChoiceArgs: function(models)")
        ollama_end = source.index("\n  _chooseOllamaTextModel:", ollama_start)
        ollama_block = source[ollama_start:ollama_end]
        self.assertIn("for (let model of (Array.isArray(models) ? models : []))", ollama_block)
        self.assertIn('if (!model || typeof model !== "object")', ollama_block)

    def test_backend_status_values_are_normalized(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('const PAYLOAD_STATUSES = ["idle", "recording", "recorded", "processing", "done", "error", "setup"];', source)
        normalize_start = source.index("_normalizePayloadStatus: function(value, hasError)")
        normalize_end = source.index("\n  _hotkeyName:", normalize_start)
        normalize_block = source[normalize_start:normalize_end]
        self.assertIn('if (typeof value !== "string")', normalize_block)
        self.assertIn('if (normalized === "finalizing")', normalize_block)
        self.assertIn('return "processing";', normalize_block)
        self.assertIn("return PAYLOAD_STATUSES.indexOf(normalized) >= 0 ? normalized : \"error\";", normalize_block)

        apply_start = source.index("_applyPayload: function(payload, statusRefreshToken)")
        apply_end = source.index("\n  _artifactEncryptionWarningKey:", apply_start)
        apply_block = source[apply_start:apply_end]
        self.assertIn("let status = this._normalizePayloadStatus(payload.status, Boolean(payload.error));", apply_block)
        self.assertIn("this._applyPayloadLanguage(payload, status);", apply_block)
        self.assertIn("if (status === \"done\")", apply_block)
        self.assertIn("this._maybeAutoTranscribeRecorded(payload, status);", apply_block)

        language_start = source.index("_applyPayloadLanguage: function(payload, statusOverride)")
        language_end = source.index("\n  _updateRecordingTiming:", language_start)
        language_block = source[language_start:language_end]
        self.assertIn('payload && typeof payload.language === "string"', language_block)
        self.assertIn('payload.language.trim().toLowerCase()', language_block)
        self.assertIn('if (LANGUAGE_CODES.indexOf(language) < 0)', language_block)
        timing_start = source.index("_updateRecordingTiming: function(payload, status)")
        timing_end = source.index("\n  _parseDateMs:", timing_start)
        timing_block = source[timing_start:timing_end]
        self.assertIn('typeof payload.max_seconds === "number" && isFinite(payload.max_seconds)', timing_block)
        self.assertNotIn('let maxSeconds = Number(payload.max_seconds);', timing_block)

    def test_backend_boolean_fields_require_explicit_json_booleans(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("if (section.ok !== true)", source)
        self.assertIn("if (payload.ok !== true)", source)
        self.assertIn("if (check.ok !== true)", source)
        self.assertIn("if (payload.ok === true)", source)
        self.assertIn("payload.ok === true ?", source)
        self.assertIn("section.ok === true ?", source)
        self.assertIn("let enabled = alarm.enabled === true;", source)
        self.assertIn("payload.removed === true", source)
        self.assertIn("let notifications = due.filter((alarm) => alarm.notify === true);", source)
        self.assertIn("alarm.critical === true", source)
        self.assertIn("let downloaded = model.downloaded === true;", source)
        self.assertIn('let modelFormat = typeof model.model_format === "string" ? model.model_format.trim().toLowerCase() : "";', source)
        self.assertIn('let modelBackend = typeof model.backend === "string" ? model.backend.trim().toLowerCase() : "";', source)
        self.assertIn("payload.truncated === true", source)
        self.assertIn("payload.plaintext !== false", source)
        self.assertIn("source.default === true", source)
        self.assertIn("payload.available !== true", source)
        self.assertIn("payload.available === true", source)
        self.assertIn("if (level.ok !== true)", source)
        self.assertIn('typeof level.percent === "number" && isFinite(level.percent)', source)
        self.assertNotIn("Boolean(alarm.critical)", source)
        self.assertNotIn("Boolean(model.downloaded)", source)
        self.assertNotIn("Boolean(payload.truncated)", source)
        self.assertNotIn("Boolean(payload.plaintext)", source)

    def test_voice_model_payloads_derive_redacted_paths_from_safe_metadata(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        path_start = source.index("_modelPathFromPayload: function(model)")
        path_end = source.index("\n  _isUsableVoiceModelPayload:", path_start)
        path_block = source[path_start:path_end]
        self.assertIn('typeof model.filename !== "string"', path_block)
        self.assertIn('typeof model.model_format !== "string"', path_block)
        self.assertIn('filename === "." || filename === ".."', path_block)
        self.assertIn("filename.length > 255", path_block)
        self.assertIn("/^[A-Za-z0-9._-]+$/", path_block)
        self.assertIn('directory = "whisper.cpp";', path_block)
        self.assertIn('directory = "ctranslate2";', path_block)
        self.assertIn('"speed-of-cinnamon",', path_block)

        usable_start = source.index("_isUsableVoiceModelPayload: function(model)")
        usable_end = source.index("\n  _addModelMenuEntry:", usable_start)
        usable_block = source[usable_start:usable_end]
        self.assertIn("model.downloaded === true", usable_block)
        self.assertIn('let expectedBackend = modelFormat === "ggml"', usable_block)
        self.assertIn('modelFormat === "ctranslate2"', usable_block)
        self.assertIn("backend === expectedBackend", usable_block)
        self.assertIn("this._modelPathFromPayload(model)", usable_block)

        select_start = source.index("_selectVoiceModel: function(model, preserveRecording)")
        select_end = source.index("\n  _selectAutomaticVoiceBackend:", select_start)
        select_block = source[select_start:select_end]
        self.assertIn("let path = this._modelPathFromPayload(model);", select_block)
        self.assertIn("let setStatus = preserveRecording === false", select_block)
        self.assertIn("preserveRecording", select_block)
        self.assertIn("return false;", select_block)
        self.assertIn("return true;", select_block)
        self.assertNotIn("String(model.path || \"\")", select_block)

        remove_start = source.index("_removeVoiceModel: function(model)")
        remove_end = source.index("\n  _selectVoiceModel:", remove_start)
        remove_block = source[remove_start:remove_end]
        self.assertIn("let path = this._modelPathFromPayload(model);", remove_block)

    def test_voice_model_download_handles_missing_model_payload(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_downloadVoiceModel: function(model)")
        end = source.index("\n  _removeVoiceModel:", start)
        block = source[start:end]
        self.assertIn("this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()", block)
        self.assertIn('model && typeof model.name === "string"', block)
        self.assertIn('let name = model && typeof model.name === "string" ? model.name.trim() : "";', block)
        self.assertIn('if (name === "")', block)
        self.assertIn("name = this._starterVoiceModelName();", block)
        self.assertIn("this._selectVoiceModel(payload, false);", block)

    def test_voice_model_download_ignores_stale_settings_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_downloadVoiceModel: function(model)")
        end = source.index("\n  _removeVoiceModel:", start)
        block = source[start:end]
        self.assertIn("let actionToken = {};", block)
        self.assertIn("this.voiceModelActionToken = actionToken;", block)
        self.assertIn("this.voiceModelActionToken !== actionToken", block)
        self.assertIn("this.voiceModelActionToken = null;", block)
        self.assertIn('resourceGroup: "voice-model"', block)

        model_refresh_start = source.index("_refreshModelMenu: function()")
        model_refresh_end = source.index("\n  _populateModelMenu:", model_refresh_start)
        self.assertIn('resourceGroup: "model-menu-refresh"', source[model_refresh_start:model_refresh_end])

        for method, next_method in [
            ("_onLanguageSettingsChanged: function()", "\n  _hasActiveRecordingState:"),
            ("_onVoiceBackendSettingsChanged: function()", "\n  _onTextModelSettingsChanged:"),
        ]:
            settings_start = source.index(method)
            settings_end = source.index(next_method, settings_start)
            settings_block = source[settings_start:settings_end]
            self.assertIn("this.voiceModelActionToken = null;", settings_block)
            self.assertIn('this._terminateProcessesByGroup("voice-model")', settings_block)
            self.assertLess(
                settings_block.index("this.voiceModelActionToken = null;"),
                settings_block.index('this._terminateProcessesByGroup("voice-model")')
            )
            self.assertIn('this._terminateProcessesByGroup("model-menu-refresh")', settings_block)
            self.assertLess(
                settings_block.index("this.modelMenuRefreshToken = null;"),
                settings_block.index('this._terminateProcessesByGroup("model-menu-refresh")')
            )

        input_start = source.index("_onInputSourceSettingsChanged: function()")
        input_end = source.index("\n  _onVoiceBackendSettingsChanged:", input_start)
        input_block = source[input_start:input_end]
        self.assertIn("this.inputSourceMenuRefreshToken = null;", input_block)
        self.assertIn('this._terminateProcessesByGroup("input-source-refresh")', input_block)
        self.assertLess(
            input_block.index("this.inputSourceMenuRefreshToken = null;"),
            input_block.index('this._terminateProcessesByGroup("input-source-refresh")')
        )

        input_refresh_start = source.index("_refreshInputSourceMenu: function()")
        input_refresh_end = source.index("\n  _populateInputSourceMenu:", input_refresh_start)
        self.assertIn('resourceGroup: "input-source-refresh"', source[input_refresh_start:input_refresh_end])

    def test_voice_model_callbacks_fail_closed_on_processing_exceptions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, message in [
            ("_downloadVoiceModel: function(model)", "\n  _removeVoiceModel:", "downloadArgs", "Could not complete model download"),
            ("_removeVoiceModel: function(model)", "\n  _selectVoiceModel:", "removeArgs", "Could not complete model removal"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnJson({args_name}, (payload) => {{", block)
            self.assertIn("try {\n        this.isCommandRunning = false;", block)
            self.assertIn('this._recordLifecycleError("model-action", error);', block)
            self.assertIn(f'_("{message}")', block)

    def test_voice_model_selection_cannot_mutate_during_model_action(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, result in [
            ("_selectVoiceModel: function(model, preserveRecording)", "\n  _selectAutomaticVoiceBackend:", "return false;"),
            ("_selectAutomaticVoiceBackend: function()", "\n  _selectStaticVoiceBackend:", "return;"),
            ("_selectStaticVoiceBackend: function(transcriber, message)", "\n  _externalApiEnvPath:", "return;"),
            ("_selectExternalApiVoiceBackend: function()", "\n  _refreshTextModelMenu:", "return false;"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("if (this.voiceModelActionToken)", block)
            self.assertIn(result, block)

        voice_start = source.index("_selectExternalApiVoiceBackend: function()")
        voice_end = source.index("\n  _refreshTextModelMenu:", voice_start)
        voice_block = source[voice_start:voice_end]
        self.assertIn('this._setStatusPreservingRecording("error", _("Voice model operation is still running")', voice_block)

        voice_start = source.index("_onVoiceBackendSettingsChanged: function()")
        voice_end = source.index("\n  _onTextModelSettingsChanged:", voice_start)
        voice_block = source[voice_start:voice_end]
        self.assertIn("this.modelMenuRefreshToken = null;", voice_block)

        text_start = source.index("_onTextModelSettingsChanged: function()")
        text_end = source.index("\n  _onOpenAiFlexProcessingSettingsChanged:", text_start)
        text_block = source[text_start:text_end]
        self.assertIn("this._cancelOllamaInstallWatch() !== false;", text_block)
        self.assertIn("this._clearOllamaModelFlow", text_block)
        self.assertIn("this.textModelMenuRefreshToken = null;", text_block)
        self.assertIn('this._terminateProcessesByGroup("text-model-refresh")', text_block)
        self.assertLess(
            text_block.index("this.textModelMenuRefreshToken = null;"),
            text_block.index('this._terminateProcessesByGroup("text-model-refresh")')
        )

    def test_text_model_argument_errors_do_not_overwrite_local_workflow_status(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_tryTextModelsArgs: function(backendOverride)")
        end = source.index("\n  _downloadModelArgs:", start)
        block = source[start:end]
        self.assertIn(
            "if (!this.isCommandRunning && !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow())",
            block,
        )
        self.assertIn(
            'this._setStatusPreservingRecording("error", _("Could not prepare text model request: ") + safeError',
            block,
        )

    def test_text_model_payload_names_must_be_strings(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        populate_start = source.index("_populateTextModelMenu: function(models, message, provider)")
        populate_end = source.index("\n  _canMutateMenu:", populate_start)
        populate_block = source[populate_start:populate_end]
        self.assertIn('typeof model.name === "string"', populate_block)
        self.assertIn("model.name.trim()", populate_block)

        entry_start = source.index("_addTextModelMenuEntry: function(model, backend)")
        entry_end = source.index("\n  _textPolishingPresetLabel:", entry_start)
        entry_block = source[entry_start:entry_end]
        self.assertIn('typeof model.name === "string"', entry_block)
        self.assertIn('model.name === "string" ? model.name.trim() : ""', entry_block)

        choice_start = source.index("_ollamaModelChoiceArgs: function(models)")
        choice_end = source.index("\n  _chooseOllamaTextModel:", choice_start)
        choice_block = source[choice_start:choice_end]
        self.assertIn('typeof model.name !== "string"', choice_block)
        self.assertIn('model.name.trim()', choice_block)

        self.assertIn('provider === "openai-compatible" || provider === "ollama"', populate_block)
        self.assertIn('this._populateTextModelMenu(payload.models || [], availabilityMessage, provider);', source)
        self.assertIn('let provider = backend === "openai-compatible" || backend === "ollama" ? backend : "";', entry_block)
        self.assertIn('if (provider === "")', entry_block)

        install_start = source.index("_installOllamaTextModel: function(model)")
        install_end = source.index("\n  _refreshHistory:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn('typeof payload.model === "string"', install_block)
        self.assertIn("payload.model.trim()", install_block)
        self.assertIn('String(model || "").trim()', install_block)

    def test_alarm_summary_payloads_are_string_checked_before_menu_creation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_populateAlarmMenu: function(alarms, summary, message)")
        end = source.index("\n  _addAlarmMenuEntry:", start)
        block = source[start:end]
        self.assertIn('typeof message === "string"', block)
        self.assertIn('typeof summary === "string"', block)
        self.assertIn("let summaryLabel = messageText || summaryText", block)
        self.assertIn('if (messageText !== "")', block)
        self.assertIn("new PopupMenu.PopupMenuItem(summaryLabel)", block)

    def test_alarm_entry_payload_text_fields_are_string_checked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        menu_start = source.index("_populateAlarmMenu: function(alarms, summary, message)")
        menu_end = source.index("\n  _addAlarmMenuEntry:", menu_start)
        menu_block = source[menu_start:menu_end]
        self.assertIn('typeof alarm.id === "string"', menu_block)

        entry_start = source.index("_addAlarmMenuEntry: function(alarm)")
        entry_end = source.index("\n  _copyAlarmCommands:", entry_start)
        entry_block = source[entry_start:entry_end]
        self.assertIn('id = this._coerceCliTextArg(alarm.id, "alarm id").trim();', entry_block)
        self.assertIn('typeof alarm.label === "string"', entry_block)
        self.assertIn('typeof alarm.time === "string"', entry_block)
        self.assertIn('typeof alarm.summary === "string"', entry_block)

        check_start = source.index("_checkAlarms: function(manual)")
        check_end = source.index("\n  _refreshInputSourceMenu:", check_start)
        check_block = source[check_start:check_end]
        self.assertIn('typeof alarm.body === "string"', check_block)
        self.assertIn('typeof alarm.label === "string"', check_block)

    def test_invalid_downloaded_voice_model_cannot_be_selected_from_menu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_addModelMenuEntry: function(model, parentMenu)")
        end = source.index("\n  _isEnglishLanguage:", start)
        block = source[start:end]
        self.assertIn("let usable = downloaded && this._isUsableVoiceModelPayload(model);", block)
        self.assertIn("let current = usable && this.whisperModel", block)
        self.assertIn('label += _(" - invalid metadata");', block)
        self.assertIn("useItem.setSensitive(!current && compatible && usable);", block)

    def test_voice_model_remove_requires_explicit_backend_confirmation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_removeVoiceModel: function(model)")
        end = source.index("\n  _selectVoiceModel:", start)
        block = source[start:end]
        self.assertIn("this.modelMenuRefreshToken || this._hasLocalProcessingWorkflow()", block)
        self.assertIn("let actionToken = {};", block)
        self.assertIn("this.voiceModelActionToken = actionToken;", block)
        self.assertIn("this.voiceModelActionToken !== actionToken", block)
        self.assertIn("this.voiceModelActionToken = null;", block)
        self.assertIn("if (payload.removed !== true)", block)
        self.assertIn('_("Model was not downloaded: ") + name', block)
        self.assertLess(block.index("if (payload.removed !== true)"), block.index("if (path !== \"\""))

    def test_voice_model_refresh_serializes_backend_requests(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        refresh_start = source.index("_refreshModelMenu: function()")
        refresh_end = source.index("\n  _populateModelMenu:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("if (this.modelMenuRefreshToken || this.voiceModelActionToken)", refresh_block)
        self.assertIn('this._terminateProcessesByGroup("model-menu-refresh")', refresh_block)
        self.assertIn("this.modelMenuRefreshToken = null;", refresh_block)
        self.assertLess(refresh_block.index("if (this.modelMenuRefreshToken || this.voiceModelActionToken)"), refresh_block.index("let refreshToken = {};"))
        self.assertLess(refresh_block.index('this._terminateProcessesByGroup("model-menu-refresh")'), refresh_block.index("let refreshToken = {};"))
        self.assertLess(refresh_block.index("this.modelMenuRefreshToken = null;"), refresh_block.index("if (payload.error)"))

        for method, next_method in [
            ("_downloadVoiceModel: function(model)", "\n  _removeVoiceModel:"),
            ("_removeVoiceModel: function(model)", "\n  _selectVoiceModel:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            action_block = source[start:end]
            self.assertIn("this.voiceModelActionToken", action_block)
            self.assertIn("this.modelMenuRefreshToken", action_block)

    def test_input_source_names_and_descriptions_are_string_checked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_populateInputSourceMenu: function(sources, message)")
        end = source.index("\n  _selectInputSource:", start)
        block = source[start:end]
        self.assertIn('typeof source.name === "string"', block)
        self.assertIn('sourceName = this._coerceCliTextArg(source.name, "input device");', block)
        self.assertIn('typeof source.description === "string"', block)
        self.assertIn("let label = description || sourceName;", block)
        select_start = source.index("_selectInputSource: function(name, label)")
        select_end = source.index("\n  _selectDefaultInputSource:", select_start)
        select_block = source[select_start:select_end]
        self.assertIn('if (typeof name !== "string")', select_block)
        self.assertIn('nextInputDevice = this._coerceCliTextArg(name, "input device");', select_block)
        self.assertIn('this._commitSettingValue("inputDevice", "input-device"', select_block)

    def test_optional_model_metadata_is_string_checked_before_display(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        voice_start = source.index("_addModelMenuEntry: function(model, parentMenu)")
        voice_end = source.index("\n  _isEnglishLanguage:", voice_start)
        voice_block = source[voice_start:voice_end]
        self.assertIn('typeof model.size === "string"', voice_block)
        self.assertIn('typeof model.description === "string"', voice_block)
        self.assertIn("let label = (current ? \"[x] \" : \"[ ] \") + name + \" (\" + (size || \"?\") + \")\";", voice_block)

        text_start = source.index("_addTextModelMenuEntry: function(model, backend)")
        text_end = source.index("\n  _textPolishingPresetLabel:", text_start)
        text_block = source[text_start:text_end]
        self.assertIn('typeof model.description === "string"', text_block)
        self.assertIn('typeof model.size_label === "string"', text_block)

        choice_start = source.index("_ollamaModelChoiceArgs: function(models)")
        choice_end = source.index("\n  _chooseOllamaTextModel:", choice_start)
        choice_block = source[choice_start:choice_end]
        self.assertIn('typeof model.description === "string"', choice_block)
        self.assertIn('typeof model.size_label === "string"', choice_block)

    def test_redacted_history_entries_disable_plaintext_actions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_populateHistoryMenu: function(transcripts)")
        end = source.index("\n  _copyHistoryTranscript:", start)
        block = source[start:end]
        self.assertIn('typeof transcript.preview === "string"', block)
        self.assertIn('typeof transcript.name === "string"', block)
        self.assertIn('typeof transcript.text === "string"', block)
        self.assertIn("let hasTranscriptText = transcriptText !== \"\" && !this._isEmptyTranscriptText(transcriptText);", block)
        self.assertIn("insertItem.setSensitive(hasTranscriptText);", block)
        self.assertIn("copyItem.setSensitive(hasTranscriptText);", block)
        self.assertIn("Transcript content hidden; use List all Transcripts", block)

    def test_history_menu_fanout_is_bounded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const MAX_HISTORY_MENU_ENTRIES = 128;", source)
        start = source.index("_populateHistoryMenu: function(transcripts)")
        end = source.index("\n  _copyHistoryTranscript:", start)
        block = source[start:end]
        self.assertIn("let historyWasTruncated = transcripts.length > MAX_HISTORY_MENU_ENTRIES;", block)
        self.assertIn("transcripts = transcripts.slice(0, MAX_HISTORY_MENU_ENTRIES);", block)
        self.assertIn('_("Transcript list truncated for safety")', block)

    def test_invalid_ollama_model_input_cannot_stick_command_running_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_installOllamaTextModel: function(model)")
        end = source.index("\n  _refreshHistory:", start)
        block = source[start:end]
        self.assertIn("let installArgs;", block)
        self.assertIn("installArgs = this._installTextModelArgs(model);", block)
        self.assertIn("} catch (err) {", block)
        self.assertIn('this._setStatus("error", _("Could not prepare Ollama model installation: ") + safeError', block)
        self.assertLess(block.index("let installArgs;"), block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(installArgs,", block)

    def test_invalid_ollama_choice_values_are_skipped_before_zenity_spawn(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_ollamaModelChoiceArgs: function(models)")
        end = source.index("\n  _chooseOllamaTextModel:", start)
        block = source[start:end]
        self.assertIn("let name;", block)
        self.assertIn("name = this._coerceCliTextArg(model.name.trim(), \"ollama model\");", block)
        self.assertIn("this._safeLogError(err);", block)
        self.assertIn("continue;", block)
        self.assertIn('"ollama model details"', block)

    def test_invalid_recording_settings_cannot_stick_toggle_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_toggleRecording: function()")
        end = source.index("\n  _restartApplet:", start)
        block = source[start:end]
        self.assertIn("let toggleArgs;", block)
        self.assertIn('toggleArgs = this._baseArgs("toggle");', block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not prepare recording command: ") + safeError', block)
        self.assertIn("_coerceCliTextArgOrFallback", source)
        self.assertIn("_appendCliOptionWithinBudget", source)
        self.assertIn("_appendCliFlagWithinBudget", source)
        self.assertIn("optional CLI setting exceeds command limit", source)
        self.assertIn('throw new Error("OpenAI-compatible Flex setting exceeds command limit");', source)
        self.assertIn("let transcriberCommandIncluded = safeTranscriberCommand.trim() === \"\";", source)
        self.assertIn("let ollamaUrlIncluded = safeOllamaUrl.trim() === \"\" || safeOllamaUrl.trim() === DEFAULT_OLLAMA_URL;", source)
        self.assertIn("let openAiCompatibleUrlIncluded", source)
        self.assertIn("let openAiCompatibleModelIncluded", source)
        self.assertIn("let openAiCompatibleTextModelIncluded", source)
        self.assertIn('args[args.indexOf("--transcriber") + 1] = "auto";', source)
        self.assertIn('args[args.indexOf("--post-process-backend") + 1] = "none";', source)
        self.assertIn('if (safeTranscriber === "openai-compatible" && (!openAiCompatibleUrlIncluded || !openAiCompatibleModelIncluded))', source)
        self.assertIn('if (safePostProcessBackend === "openai-compatible" && (!openAiCompatibleUrlIncluded || !openAiCompatibleTextModelIncluded))', source)
        self.assertIn('if (!ollamaUrlIncluded && safePostProcessBackend === "ollama")', source)
        self.assertIn('let safePostProcessPrompt = this._coerceCliTextArgOrFallback(this._effectivePostProcessPrompt(), "post-process prompt", "");', source)
        self.assertIn('let safeTranscriber = TRANSCRIBER_METHODS.indexOf(String(this.transcriber || "")) >= 0', source)
        self.assertIn('let safePostProcessBackend = POST_PROCESS_BACKENDS.indexOf(String(this.postProcessBackend || "")) >= 0', source)
        self.assertLess(block.index("let toggleArgs;"), block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(toggleArgs,", block)

    def test_recording_command_callbacks_ignore_stale_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("this._recordingCommandToken = null;", source)

        recording_blocks = []
        for method, next_method in (
            ("_toggleRecording: function()", "\n  _restartApplet:"),
            ("_cancelRecording: function(statusOverride)", "\n  _invalidateBackgroundCallbacksForRecording:"),
            ("_maybeAutoTranscribeRecorded: function(payload, statusOverride)", "\n  _clearStatusTimer:"),
            ("_restartRelistenRecording: function()", "\n  _preparedTranscriptText:"),
        ):
            start = source.index(method)
            end = source.index(next_method, start)
            recording_blocks.append(source[start:end])

        for block in recording_blocks:
            self.assertIn("let recordingCommandToken = {};", block)
            self.assertIn("this._recordingCommandToken = recordingCommandToken;", block)
            self.assertIn("this._recordingCommandToken !== recordingCommandToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)
            self.assertIn("this._recordingCommandToken = null;", block)
            self.assertLess(
                block.index("if (this._recordingCommandToken !== recordingCommandToken"),
                block.index("this.isCommandRunning = false;")
            )

    def test_relisten_restart_callback_fails_closed_on_processing_exceptions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_restartRelistenRecording: function()")
        end = source.index("\n  _preparedTranscriptText:", start)
        block = source[start:end]
        self.assertIn("this._spawnJson(startArgs, (payload) => {", block)
        self.assertIn("try {\n        this._recordingCommandToken = null;", block)
        self.assertIn("if (this._recordingCommandToken === recordingCommandToken) {", block)
        self.assertIn('this._recordLifecycleError("recording-relisten", error);', block)
        self.assertIn('_("Could not start next recording")', block)
        self.assertIn('this.autoRelistenPendingToken = "";', block)

    def test_auto_recording_commands_validate_settings_before_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        relisten_start = source.index("_restartRelistenRecording: function()")
        relisten_end = source.index("\n  _preparedTranscriptText:", relisten_start)
        relisten_block = source[relisten_start:relisten_end]
        self.assertIn("let startArgs;", relisten_block)
        self.assertIn('startArgs = this._baseArgs("start", relistenLanguage);', relisten_block)
        self.assertIn("let relistenLanguage = this._normalizeLanguage(this.autoRelistenPendingLanguage, this._currentLanguage());", relisten_block)
        self.assertIn('this._setStatus("error", _("Could not prepare relisten command: ") + safeError', relisten_block)
        self.assertLess(relisten_block.index("let startArgs;"), relisten_block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(startArgs,", relisten_block)

        auto_start = source.index("_maybeAutoTranscribeRecorded: function(payload, statusOverride)")
        auto_end = source.index("\n  _clearStatusTimer:", auto_start)
        auto_block = source[auto_start:auto_end]
        self.assertIn("let stopArgs;", auto_block)
        self.assertIn('stopArgs = this._baseArgs("stop");', auto_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not prepare timed recording command: ") + safeError', auto_block)
        self.assertLess(auto_block.index("let stopArgs;"), auto_block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(stopArgs,", auto_block)

    def test_auto_transcribe_does_not_compete_with_local_processing_workflow(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_maybeAutoTranscribeRecorded: function(payload, statusOverride)")
        end = source.index("\n  _clearStatusTimer:", start)
        block = source[start:end]
        self.assertIn("this.isCommandRunning || this._hasLocalProcessingWorkflow(false)", block)
        self.assertLess(block.index("this._hasLocalProcessingWorkflow(false)"), block.index('if (status !== "recorded")'))

    def test_input_source_refresh_ignores_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        refresh_start = source.index("_refreshInputSourceMenu: function()")
        refresh_end = source.index("\n  _populateInputSourceMenu:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("let refreshToken = {};", refresh_block)
        self.assertIn("this.inputSourceMenuRefreshToken = refreshToken;", refresh_block)
        self.assertIn("this.inputSourceMenuRefreshToken !== refreshToken", refresh_block)
        self.assertIn("!this._canMutateMenu(this.inputSourceItem)", refresh_block)

    def test_input_source_refresh_serializes_backend_requests(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_refreshInputSourceMenu: function()")
        end = source.index("\n  _populateInputSourceMenu:", start)
        block = source[start:end]
        self.assertIn("if (this.inputSourceMenuRefreshToken)", block)
        self.assertIn('this._terminateProcessesByGroup("input-source-refresh")', block)
        self.assertIn("this.inputSourceMenuRefreshToken = null;", block)
        self.assertLess(block.index("if (this.inputSourceMenuRefreshToken)"), block.index("let refreshToken = {};"))
        self.assertLess(block.index('this._terminateProcessesByGroup("input-source-refresh")'), block.index("let refreshToken = {};"))
        self.assertLess(block.index("this.inputSourceMenuRefreshToken = null;"), block.index("if (payload.error)"))

    def test_menu_refresh_callbacks_fail_closed_on_processing_exceptions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, token_name, message in [
            ("_refreshAlarmMenu: function()", "\n  _populateAlarmMenu:", "alarmListArgs", "alarmMenuRefreshToken", "Could not refresh alarm list"),
            ("_refreshInputSourceMenu: function()", "\n  _populateInputSourceMenu:", "inputSourceArgs", "inputSourceMenuRefreshToken", "Could not refresh input source list"),
            ("_refreshModelMenu: function()", "\n  _populateModelMenu:", "modelArgs", "modelMenuRefreshToken", "Could not refresh voice model list"),
            ("_refreshTextModelMenuForBackend: function(backendOverride)", "\n  _populateTextModelMenu:", "textModelArgs", "textModelMenuRefreshToken", "Could not refresh text model list"),
            ("_refreshHistory: function()", "\n  _listAllTranscripts:", "historyArgs", "historyRefreshToken", "Could not refresh transcript history"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnJson({args_name}, (payload) => {{", block)
            self.assertIn("try {\n        this.", block)
            self.assertIn(f"if (this.{token_name} === refreshToken) {{", block)
            self.assertIn('this._recordLifecycleError("menu-refresh", error);', block)
            self.assertIn(f'_("{message}")', block)
            if method == "_refreshTextModelMenuForBackend: function(backendOverride)":
                self.assertIn('resourceGroup: "text-model-refresh"', block)

    def test_menu_refresh_errors_do_not_overwrite_local_workflow_status(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, helper in [
            ("_refreshInputSourceMenu: function()", "\n  _populateInputSourceMenu:", "canReportInputSourceStatus"),
            ("_refreshModelMenu: function()", "\n  _populateModelMenu:", "canReportModelStatus"),
            ("_refreshTextModelMenuForBackend: function(backendOverride)", "\n  _populateTextModelMenu:", "canReportTextModelStatus"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {helper} = () => !this.isCommandRunning &&", block)
            self.assertIn("!this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();", block)
            self.assertIn(f"if ({helper}())", block)

    def test_menu_backend_tokens_release_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, builder_name, token_name in [
            ("_refreshInputSourceMenu: function()", "\n  _populateInputSourceMenu:", "inputSourceArgs", "_listInputsArgs", "inputSourceMenuRefreshToken"),
            ("_refreshModelMenu: function()", "\n  _populateModelMenu:", "modelArgs", "_modelsArgs", "modelMenuRefreshToken"),
            ("_downloadVoiceModel: function(model)", "\n  _removeVoiceModel:", "downloadArgs", "_downloadModelArgs", "voiceModelActionToken"),
            ("_removeVoiceModel: function(model)", "\n  _selectVoiceModel:", "removeArgs", "_removeModelArgs", "voiceModelActionToken"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {args_name};", block)
            self.assertIn(f"{args_name} = this.{builder_name}", block)
            self.assertIn(f"if (this.{token_name} ===", block)
            self.assertIn(f"this.{token_name} = null;", block)
            self.assertIn("this._recordLifecycleError(", block)
        for method, next_method in [
            ("_downloadVoiceModel: function(model)", "\n  _removeVoiceModel:"),
            ("_removeVoiceModel: function(model)", "\n  _selectVoiceModel:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this.isCommandRunning = false;", block)

    def test_ollama_model_checks_ignore_stale_flow_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method in [
            ("_activateOllamaTextModelFlow: function()", "\n  _ollamaModelPromptArgs:"),
            ("_chooseOllamaTextModel: function()", "\n  _promptChooseOllamaTextModel:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("let flowToken = {};", block)
            self.assertIn("this.ollamaModelFlowToken = flowToken;", block)
            self.assertIn("this.ollamaModelFlowToken !== flowToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)

    def test_ollama_install_watch_ignores_stale_poll_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        watch_start = source.index("_watchOllamaInstallThenChoose: function()")
        watch_end = source.index("\n  _scheduleSetupCheck:", watch_start)
        watch_block = source[watch_start:watch_end]
        self.assertIn("if (this._cancelOllamaInstallWatch() === false)", watch_block)
        self.assertIn("this._clearOllamaModelFlow();", watch_block)
        self.assertIn('this._setStatus("error", _("Ollama operation could not be stopped")', watch_block)
        self.assertIn("return false;", watch_block)
        self.assertIn("return true;", watch_block)
        self.assertIn("let watchToken = {};", watch_block)
        self.assertIn("this.ollamaInstallWatchToken = watchToken;", watch_block)
        self.assertIn("this.ollamaInstallWatchToken !== watchToken", watch_block)
        self.assertIn("this._scheduleOllamaInstallWatchPoll(watchToken);", watch_block)
        self.assertIn("this.ollamaInstallWatchToken = null;", watch_block)
        self.assertIn('this._setStatus("error", _("Could not continue Ollama installation watch")', watch_block)
        self.assertIn('this._setStatus("error", _("Ollama status check failed: ") + safeError, this.lastTranscript);', watch_block)

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
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "auto-transcribe-timeout", "autoTranscribeTimeout", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "auto-relisten", "autoRelisten", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "keep-recording-artifacts", "keepRecordingArtifacts", this._onRecordingOptionsChanged, null)', source)
        self.assertIn('this._commitSettingValue("autoTranscribeTimeout", "auto-transcribe-timeout"', source)
        self.assertIn('this._commitSettingValue("autoRelisten", "auto-relisten"', source)
        self.assertIn('this._commitSettingValue("keepRecordingArtifacts", "keep-recording-artifacts"', source)
        self.assertIn('args.push("--skip-silent-auto-relisten");', source)
        self.assertIn('_("Auto-transcribe at time limit")', source)
        self.assertIn('_("Auto Relisten")', source)
        self.assertIn('_("Keep recording files")', source)
        self.assertIn('this._populateRecordingOptionsMenu();', source)
        self.assertIn('this.autoRelistenPending = false;', source)
        self.assertIn('this.autoRelistenPendingToken = "";', source)
        self.assertIn('this.autoRelistenPendingLanguage = "";', source)
        self.assertIn('this.autoRelistenManualStopRequested = false;', source)
        self.assertIn("this.cancelPendingWhileCommandRunning = false;", source)
        self.assertIn("this.autoRelistenSequence = 0;", source)
        self.assertIn('let shouldRelisten = this.autoRelistenPending;', source)
        self.assertIn('relistenToken = String(this.autoRelistenSequence) + ":" + recordingKey;', source)
        self.assertIn('this.autoRelistenPending = Boolean(relistenToken);', source)
        self.assertIn('this.autoRelistenPendingToken = relistenToken;', source)
        self.assertIn('if (relistenToken && this.autoRelistenPendingToken !== relistenToken) {\n        this.isCommandRunning = false;\n        if (this.cancelPendingWhileCommandRunning) {\n          this._applyPayloadSafely(nextPayload, undefined, true);\n        }\n        return;\n      }', source)
        self.assertIn('if (nextPayload && nextPayload.error) {\n        this.autoTranscribeRecordingKey = "";\n      }', source)
        self.assertIn('const EMPTY_TRANSCRIPT_MARKERS = [', source)
        self.assertIn('"leere aufnahme"', source)
        self.assertIn("_isEmptyTranscriptText: function(transcript)", source)
        self.assertIn('let hasTranscript = typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript);', source)
        self.assertIn('if (status === "done" && payload.silence_detected === true)', source)
        self.assertIn('if (status === "done" && hasTranscript)', source)
        self.assertIn('if (status === "done" && this.autoRelistenPending)', source)
        self.assertIn('typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript)', source)
        self.assertIn("_ensureAutoRelistenPendingForDonePayload: function(payload)", source)
        self.assertIn("this._ensureAutoRelistenPendingForDonePayload(payload);", source)
        self.assertIn("if (this.autoRelistenManualStopRequested) {\n      return;\n    }", source)
        self.assertIn('this.autoRelistenPendingToken = String(this.autoRelistenSequence) + ":done:" + marker;', source)
        self.assertIn("let previousNotificationSessionActive = this.notificationSessionActive;", source)
        self.assertIn("this.notificationSessionActive = true;\n      relistenStarted = this._restartRelistenRecording();", source)
        self.assertIn("this.notificationSessionActive = previousNotificationSessionActive;", source)
        self.assertIn('let relistenStarted = false;', source)
        self.assertIn('if (shouldRelisten) {', source)
        self.assertIn('relistenStarted = this._restartRelistenRecording();', source)
        self.assertIn('this._insertTranscriptText(transcript,', source)
        self.assertIn("if (this._isEmptyTranscriptText(transcript)) {\n      this._finishEmptyRelistenDone(payload);\n      return;\n    }", source)
        self.assertIn('if (this._isEmptyTranscriptText(transcript) || this._isEmptyTranscriptText(text))', source)
        self.assertIn('this._reserveAutoInsertFingerprint(insertFingerprint)', source)
        finish_start = source.index("_finishAppletTextInsert: function(payload)")
        finish_end = source.index("\n  _ensureAutoRelistenPendingForDonePayload:", finish_start)
        finish_block = source[finish_start:finish_end]
        self.assertIn("let relistenStarted = this._finishPendingRelisten();", finish_block)
        self.assertIn('(this.status === "recording" || this.status === "recorded" || this.status === "processing")', finish_block)
        self.assertIn("!this.isCommandRunning && !this._hasLocalProcessingWorkflow()", finish_block)
        self.assertIn('this._setStatus("done", this._payloadMessage(payload, _("Transcript inserted")), transcript);', finish_block)
        self.assertIn('if (relistenStarted) {', source)
        self.assertIn('return true;', source)
        self.assertIn('return false;', source)
        self.assertIn('this._spawnJson(startArgs, (payload) => {', source)
        self.assertIn('this._restartRelistenRecording();', source)
        self.assertIn('(!this.autoTranscribeTimeout && !this.autoRelisten)', source)
        self.assertIn('if (!this.autoRelisten) {', source)
        self.assertIn('this.autoTranscribeRecordingKey = "";', source)
        self.assertIn('_recordingOptionsLabel: function()', source)
        self.assertIn('_("relisten")', source)

    def test_auto_relisten_done_payload_routing_is_ordered(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        silent_index = source.index('if (status === "done" && payload.silence_detected === true)')
        transcript_index = source.index('if (status === "done" && hasTranscript)')
        empty_index = source.index('if (status === "done" && this.autoRelistenPending)')
        finish_index = source.index("_finishPendingRelisten: function()")
        restart_index = source.index("relistenStarted = this._restartRelistenRecording();", finish_index)
        status_index = source.index('this._payloadMessage(payload, _("Recording finished without transcript")', empty_index)

        self.assertLess(silent_index, transcript_index)
        self.assertLess(transcript_index, empty_index)
        self.assertLess(empty_index, finish_index)
        self.assertNotIn("this.autoRelistenPending = false;", source[finish_index:restart_index])
        self.assertLess(restart_index, status_index)

    def test_auto_relisten_pending_token_is_not_cleared_during_running_command(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        apply_index = source.index("_applyPayload: function(payload, statusRefreshToken)")
        maybe_index = source.index("this._maybeAutoTranscribeRecorded(payload, status);", apply_index)
        guarded_reset = source.index(
            "if (!this.isCommandRunning && !this.autoRelistenManualStopRequested) {",
            apply_index,
        )
        reset_index = source.index("this.autoRelistenPending = false;", guarded_reset)

        self.assertLess(guarded_reset, maybe_index)
        self.assertLess(reset_index, maybe_index)

    def test_spawn_callbacks_ignore_removed_applet(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this.appletRemoved = false;", source)
        self.assertIn("this.spawnGeneration = 0;", source)
        self.assertIn("this.appletRemoved = true;", source)
        self.assertIn("this.spawnGeneration += 1;", source)
        self.assertIn("let generation = this.spawnGeneration;", source)
        self.assertIn("let processToken;", source)
        self.assertIn("processToken = this._registerProcess(process, generation, options.resourceGroup);", source)
        self.assertIn('if (suppressCallback || this.appletRemoved || this.spawnGeneration !== generation || typeof callback !== "function")', source)

    def test_subprocess_registration_failures_terminate_spawned_processes(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        launch_start = source.index("let process = null;")
        start = source.index("process = launcher.spawnv(spawnArgs);", launch_start)
        launch_end = source.index("let generation = this.spawnGeneration;", start)
        end = source.index("let timeoutKey = \"process-timeout-\" + processToken;", launch_end)
        launch_block = source[launch_start:launch_end]
        block = source[start:end]
        self.assertIn("try {", launch_block)
        self.assertIn("let launcher = new Gio.SubprocessLauncher({ flags: flags });", launch_block)
        self.assertIn("launcher.setenv(key, String(env[key] || \"\"), true);", launch_block)
        self.assertIn('this._recordLifecycleError("process-spawn", error);', launch_block)
        self.assertIn("return null;", launch_block)
        self.assertIn("try {", block)
        self.assertIn("processToken = this._registerProcess(process, generation, options.resourceGroup);", block)
        self.assertIn("cancellable = new Gio.Cancellable();", block)
        self.assertIn("cancellableToken = this._registerCancellable(cancellable);", block)
        self.assertIn("if (!this._unregisterProcess(processToken))", block)
        self.assertIn("let orphanCancellableCleanupSucceeded = this._retryOrphanedCancellables();", block)
        self.assertIn("this._orphanedCancellables.length > 0", block)
        self.assertIn("this._terminateProcess(process);", block)
        self.assertIn("error.processToken", block)
        self.assertIn("processToken = error.processToken;", block)
        self.assertIn("this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken);", block)
        self.assertIn("let processTerminated = this._terminateProcess(process);", block)
        self.assertLess(block.index("let processTerminated = this._terminateProcess(process);"), block.index("this._unregisterProcess(processToken)"))
        self.assertIn(
            "this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, true);\n"
            "          this._scheduleProcessCleanupRetry();",
            block,
        )
        self.assertIn(
            "this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, false);\n"
            "        this._scheduleProcessCleanupRetry();",
            block,
        )

    def test_cancellable_registration_failure_clears_orphan_after_unregister(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("let cancellable = null;")
        end = source.index("let timeoutKey = \"process-timeout-\" + processToken;", start)
        block = source[start:end]
        self.assertIn("if (!cancellableToken && error && (typeof error === \"object\" || typeof error === \"function\") && error.cancellableToken)", block)
        self.assertIn("cancellableToken = error.cancellableToken;", block)
        self.assertIn("let cancellableCleanupSucceeded = false;", block)
        self.assertIn(
            "try {\n"
            "        cancellableCleanupSucceeded = this._unregisterCancellable(cancellableToken);\n"
            "      } catch (cleanupError) {\n"
            "        this._recordLifecycleError(\"cancellable-unregister\", cleanupError);\n"
            "      }",
            block,
        )
        self.assertIn("if (!cancellableCleanupSucceeded) {", block)
        self.assertIn("this._trackOrphanedCancellable(cancellableToken, false);", block)
        self.assertIn("} else if (!this._untrackOrphanedCancellable(cancellableToken)) {", block)
        self.assertIn('new Error("Cancellable orphan cleanup could not be completed")', block)
        self.assertLess(
            block.index("let cancellableCleanupSucceeded ="),
            block.index("let orphanCancellableCleanupSucceeded = this._retryOrphanedCancellables();")
        )
        self.assertLess(
            block.index("let processTerminated = this._terminateProcess(process);"),
            block.index("throw error;"),
        )

    def test_process_registration_failure_removes_successfully_terminated_orphans(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        register_start = source.index("_registerProcess: function(process, generation, group)")
        register_end = source.index("\n  _unregisterProcess:", register_start)
        register_block = source[register_start:register_end]
        self.assertIn("error.processToken = token;", register_block)

        start = source.index("let processToken;")
        end = source.index("let cancellable = null;", start)
        block = source[start:end]
        self.assertIn("error.processToken", block)
        self.assertIn("processToken = error.processToken;", block)
        self.assertIn("let processTerminated = this._terminateProcess(process);", block)
        self.assertIn("if (processTerminated) {", block)
        self.assertIn("let processCleanupSucceeded = this._unregisterProcess(processToken);", block)
        self.assertIn("let orphanTracked = this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, true);", block)
        self.assertIn("let orphanCleanupSucceeded = orphanTracked && this._retryOrphanedProcesses();", block)
        self.assertIn("this._scheduleProcessCleanupRetry();", block)
        self.assertIn("} else {\n        this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken);", block)
        self.assertLess(block.index("let processTerminated ="), block.index("let processCleanupSucceeded ="))
        self.assertLess(block.index("let processCleanupSucceeded ="), block.index("let orphanTracked ="))
        self.assertLess(block.index("let orphanTracked ="), block.index("this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken);"))

    def test_orphaned_processes_are_retried_and_block_new_spawns(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedProcess: function(process, generation, group, registryToken, terminationSucceeded)")
        end = source.index("\n  _terminateProcess:", start)
        orphan_block = source[start:end]
        self.assertIn("this._orphanedProcesses = [];", orphan_block)
        self.assertIn("entry.process === process", orphan_block)
        self.assertIn("let registryEntry = registry && registry[key];", orphan_block)
        self.assertIn("registryEntry.processGroupIdentity", orphan_block)
        self.assertIn("if (!processGroupIdentity) {\n        processGroupIdentity = this._readProcessGroupIdentity(process);", orphan_block)
        self.assertIn("let entry = {", orphan_block)
        self.assertIn("this._orphanedProcesses.push(entry);", orphan_block)
        self.assertIn('throw new Error("Process orphan entry could not be tracked");', orphan_block)
        self.assertIn("registryToken: key", orphan_block)
        self.assertIn("terminationSucceeded: terminationSucceeded === true", orphan_block)
        self.assertIn("_retryOrphanedProcesses: function(group)", orphan_block)
        self.assertIn("let wantedGroup = group === undefined ? null : String(group || \"process\");", orphan_block)
        self.assertIn("wantedGroup !== null && String(entry.group || \"process\") !== wantedGroup", orphan_block)
        self.assertIn("this._terminateProcess(entry.process)", orphan_block)
        self.assertIn("this._unregisterProcess(entry.registryToken)", orphan_block)
        self.assertIn("let removed = this._orphanedProcesses.splice(index, 1);", orphan_block)
        self.assertIn("removed[0] !== entry", orphan_block)
        self.assertIn('throw new Error("Process orphan entry could not be removed");', orphan_block)
        self.assertIn("this._untrackOrphanedProcess(entry.process)", orphan_block)

        terminate_start = source.index("_terminateProcessesByGroup: function(group, notifyCallback)")
        terminate_end = source.index("\n  _hasTrackedProcessGroup:", terminate_start)
        terminate_block = source[terminate_start:terminate_end]
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedProcesses(wanted);", terminate_block)
        self.assertIn("this._orphanedProcesses.some(", terminate_block)
        self.assertIn("String(entry.group || \"process\") === wanted", terminate_block)
        self.assertIn("if (!allSucceeded || this._processCleanupStillPending())", terminate_block)
        self.assertIn("this._scheduleProcessCleanupRetry();", terminate_block)

        bounded_start = source.index("_runBoundedSubprocess: function(args, env, options, callback)")
        bounded_end = source.index("\n  _spawnJsonWithBackendEnvironment:", bounded_start)
        bounded_block = source[bounded_start:bounded_end]
        self.assertIn("if (!Array.isArray(this._orphanedProcesses))", bounded_block)
        self.assertIn("if (!Array.isArray(this._orphanedCancellables))", bounded_block)
        self.assertIn("if (!Array.isArray(this._orphanedTimers))", bounded_block)
        self.assertIn("Array.isArray(this._orphanedProcesses)", bounded_block)
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedProcesses();", bounded_block)
        self.assertIn('this._recordLifecycleError("process-state", new Error("An orphaned process is still pending"));', bounded_block)
        self.assertIn("return null;", bounded_block)
        self.assertIn("let orphanCancellableCleanupSucceeded = this._retryOrphanedCancellables();", bounded_block)
        self.assertIn('this._recordLifecycleError("cancellable-state", new Error("An orphaned cancellable is still pending"));', bounded_block)
        self.assertIn("let orphanTimerCleanupSucceeded = this._retryOrphanedTimers();", bounded_block)
        self.assertIn('this._recordLifecycleError("timer-state", new Error("An orphaned timer is still pending"));', bounded_block)
        self.assertIn('this._runTeardownGuarded("teardown-orphaned-processes", () => this._retryOrphanedProcesses());', source)

    def test_orphaned_timer_cleanup_can_schedule_its_own_retry(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        track_start = source.index("_trackOrphanedTimer: function(")
        track_end = source.index("\n  _untrackOrphanedTimer:", track_start)
        track_block = source[track_start:track_end]
        self.assertIn('key !== "process-cleanup-retry"', track_block)
        self.assertIn("this._scheduleProcessCleanupRetry();", track_block)
        schedule_start = source.index("_scheduleTrackedTimer: function(")
        schedule_end = source.index("\n  _init:", schedule_start)
        schedule_block = source[schedule_start:schedule_end]
        self.assertIn('key !== "process-cleanup-retry"', schedule_block)

    def test_failed_process_cleanup_retries_before_releasing_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        pending_start = source.index("_processCleanupStillPending: function()")
        pending_end = source.index("\n  _releaseBusyStateAfterProcessCleanup:", pending_start)
        pending_block = source[pending_start:pending_end]
        self.assertIn("this._orphanedProcesses", pending_block)
        self.assertIn("this._orphanedCancellables", pending_block)
        self.assertIn("this._orphanedTimers", pending_block)
        self.assertIn("this._orphanedSignals", pending_block)
        self.assertIn("this._orphanedMonitors", pending_block)
        self.assertIn("this._orphanedHotkeys", pending_block)
        self.assertIn("this._orphanedDialogs", pending_block)

        workflow_start = source.index("_hasPendingProcessCleanup: function()")
        workflow_end = source.index("\n  _hasPendingDialogCleanup:", workflow_start)
        workflow_block = source[workflow_start:workflow_end]
        for registry_name in (
            "_orphanedProcesses",
            "_orphanedCancellables",
            "_orphanedTimers",
            "_orphanedSignals",
            "_orphanedMonitors",
            "_orphanedHotkeys",
        ):
            self.assertIn(f'"{registry_name}"', workflow_block)

        release_start = source.index("_releaseBusyStateAfterProcessCleanup: function(group, marker, releaseRequested)")
        release_end = source.index("\n  _scheduleProcessCleanupRetry:", release_start)
        release_block = source[release_start:release_end]
        self.assertIn("releaseRequested === true", release_block)
        self.assertIn("this._hasTrackedProcessGroup(wanted)", release_block)
        self.assertIn("String(entry.group || \"process\") === wanted", release_block)
        self.assertIn('if (wanted === "ollama" && !this.ollamaModelFlowToken)', release_block)
        self.assertIn("this.ollamaModelInstallToken = null;", release_block)
        self.assertIn("this._hasLocalProcessingWorkflow()", release_block)
        self.assertIn("anotherCommandRunning", release_block)
        self.assertIn("this.isCommandRunning = false;", release_block)

        group_start = source.index("_hasTrackedProcessGroup: function(group)")
        group_end = source.index("\n  _cancelAllCancellables:", group_start)
        group_block = source[group_start:group_end]
        self.assertIn("this._orphanedProcesses", group_block)
        self.assertIn("entry.process", group_block)
        self.assertIn("Process orphan registry is unavailable", group_block)

        retry_start = source.index("_scheduleProcessCleanupRetry: function()")
        retry_end = source.index("\n  _clearProcessCleanupRetryTimer:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("try {", retry_block)
        self.assertIn('this._recordLifecycleError("process-cleanup-retry", error);', retry_block)
        self.assertIn("return true;\n      }\n    }, false, \"processCleanupRetryTimer\")", retry_block)
        self.assertIn("this._retryOrphanedProcesses()", retry_block)
        self.assertIn("this._retryOrphanedCancellables()", retry_block)
        self.assertIn("let signalCleanupSucceeded = this._disconnectOrphanedSignals();", retry_block)
        self.assertIn("let monitorCleanupSucceeded = this._retryOrphanedMonitors();", retry_block)
        self.assertIn("let hotkeyCleanupSucceeded = this._retryOrphanedHotkeys();", retry_block)
        self.assertIn("let timerCleanupSucceeded = this._retryOrphanedTimers();", retry_block)
        self.assertIn("let dialogCleanupSucceeded = this._retryOrphanedDialogs();", retry_block)
        self.assertIn("!timerCleanupSucceeded", retry_block)
        self.assertIn("!signalCleanupSucceeded", retry_block)
        self.assertIn("!monitorCleanupSucceeded", retry_block)
        self.assertIn("!hotkeyCleanupSucceeded", retry_block)
        self.assertIn("!dialogCleanupSucceeded", retry_block)
        self.assertIn("this._processCleanupStillPending()", retry_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup("voice-model", "voiceModelCleanupFailed");', retry_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup("benchmark", "benchmarkCleanupFailed");', retry_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed");', retry_block)
        self.assertIn('!this.isCommandRunning && !this._statusCommandRunning && !this._hasLocalProcessingWorkflow()', retry_block)
        self.assertIn("this._scheduleStatusPoll();", retry_block)
        self.assertIn('this._clearTrackedTimer("process-cleanup-retry", "processCleanupRetryTimer")', source)
        self.assertIn('this._scheduleProcessCleanupRetry();', source)

        for marker in ["voiceModelCleanupFailed", "benchmarkCleanupFailed", "processCleanupRetryTimer"]:
            self.assertIn(f"this.{marker}", source)

    def test_process_and_cancellable_registration_verify_registry_writes(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_registerCancellable: function(cancellable)")
        end = source.index("\n  _unregisterCancellable:", start)
        cancellable_block = source[start:end]
        self.assertIn("if (!this._resourceRegistry || !this._resourceRegistry.cancellables)", cancellable_block)
        self.assertIn('throw new Error("Cancellable registry is unavailable");', cancellable_block)
        self.assertIn("registry[token] = cancellable;", cancellable_block)
        self.assertIn("registry[token] !== cancellable", cancellable_block)
        self.assertIn('throw new Error("Cancellable could not be registered");', cancellable_block)
        self.assertIn("let registry = null;", cancellable_block)
        self.assertIn("let registrationAttempted = false;", cancellable_block)
        self.assertIn("registrationAttempted = true;", cancellable_block)
        self.assertIn("let rollbackFailed = false;", cancellable_block)
        self.assertIn("let deleted = delete registry[token];", cancellable_block)
        self.assertIn("rollbackFailed = true;", cancellable_block)
        self.assertIn('this._recordLifecycleError("cancellable-registration-rollback", rollbackError);', cancellable_block)
        self.assertIn("if (rollbackFailed) {\n        this._trackOrphanedCancellable(token, false);", cancellable_block)
        self.assertIn("error.cancellableToken = token;", cancellable_block)

        start = source.index("_registerProcess: function(process, generation, group)")
        end = source.index("\n  _unregisterProcess:", start)
        process_block = source[start:end]
        self.assertIn("if (!this._resourceRegistry || !this._resourceRegistry.processes)", process_block)
        self.assertIn('throw new Error("Process registry is unavailable");', process_block)
        self.assertIn("let entry = {", process_block)
        self.assertIn("registry[token] = entry;", process_block)
        self.assertIn("registry[token] !== entry", process_block)
        self.assertIn('throw new Error("Process could not be registered");', process_block)
        self.assertIn("let registry = null;", process_block)
        self.assertIn("let registrationAttempted = false;", process_block)
        self.assertIn("registrationAttempted = true;", process_block)
        self.assertIn("let rollbackFailed = false;", process_block)
        self.assertIn("let deleted = delete registry[token];", process_block)
        self.assertIn("rollbackFailed = true;", process_block)
        self.assertIn('this._recordLifecycleError("process-registration-rollback", rollbackError);', process_block)
        self.assertIn("if (rollbackFailed) {\n        this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, false);", process_block)

    def test_process_group_cancellation_ignores_malformed_entries(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_terminateProcessesByGroup: function(group, notifyCallback)")
        end = source.index("\n  _cancelAllCancellables:", start)
        block = source[start:end]
        self.assertIn("let entry = null;", block)
        self.assertIn("let selected = false;", block)
        self.assertIn('if (!entry || typeof entry !== "object" || String(entry.group || "process") !== wanted)', block)
        self.assertIn('throw new Error("Process registry is unavailable");', block)
        self.assertIn('this._recordLifecycleError("process-cancel", error);', block)
        self.assertIn("let cleanupSucceeded = false;", block)
        self.assertIn("if (selected && cleanupSucceeded) {", block)
        self.assertIn("if (!this._unregisterProcess(token))", block)
        self.assertIn("if (!this._untrackOrphanedProcess(entry.process))", block)
        self.assertIn("allSucceeded = false;", block)

    def test_process_teardown_fails_closed_when_registry_is_unavailable(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_terminateAllProcesses: function()")
        end = source.index("\n  _terminateProcessesByGroup:", start)
        block = source[start:end]
        self.assertIn('this._recordLifecycleError("process-state", new Error("Process registry is unavailable"));', block)
        self.assertIn("return false;", block)
        self.assertIn("let processes = this._resourceRegistry.processes;", block)

    def test_process_and_cancellable_teardown_report_partial_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_terminateAllProcesses: function()")
        end = source.index("\n  _terminateProcessesByGroup:", start)
        process_block = source[start:end]
        self.assertIn("let allSucceeded = true;", process_block)
        self.assertIn("allSucceeded = false;", process_block)
        self.assertIn("return allSucceeded;", process_block)

        start = source.index("_cancelAllCancellables: function()")
        end = source.index("\n  _trackTimer:", start)
        cancellable_block = source[start:end]
        self.assertIn("let allSucceeded = true;", cancellable_block)
        self.assertIn("allSucceeded = false;", cancellable_block)
        self.assertIn("return allSucceeded;", cancellable_block)

    def test_cancellable_teardown_fails_closed_when_registry_is_unavailable(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_cancelAllCancellables: function()")
        end = source.index("\n  _trackTimer:", start)
        block = source[start:end]
        self.assertIn('this._recordLifecycleError("cancellable-state", new Error("Cancellable registry is unavailable"));', block)
        self.assertIn("return false;", block)
        self.assertIn("let cancellables = this._resourceRegistry.cancellables;", block)

    def test_dialog_teardown_fails_closed_when_registry_is_unavailable(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyTrackedDialogs: function()")
        end = source.index("\n  _destroyMenus:", start)
        block = source[start:end]
        self.assertIn('this._recordLifecycleError("dialog-state", new Error("Dialog registry is unavailable"));', block)
        self.assertIn("return false;", block)
        self.assertIn("let dialogs = this._resourceRegistry.dialogs;", block)

    def test_dialog_teardown_reports_partial_cleanup_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyTrackedDialogs: function()")
        end = source.index("\n  _destroyMenus:", start)
        block = source[start:end]
        self.assertIn("let success = true;", block)
        self.assertIn("success = false;", block)
        self.assertIn("if (!this._trackOrphanedDialog(dialog, \"teardown\", closeSucceeded, destroySucceeded))", block)
        self.assertIn("return success;", block)
        self.assertIn("return false;", block)

    def test_process_and_cancellable_unregistration_contains_delete_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_unregisterCancellable: function(token)")
        end = source.index("\n  _registerProcess:", start)
        cancellable_block = source[start:end]
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.cancellables, token)", cancellable_block)
        self.assertIn('throw new Error("Cancellable registry is unavailable");', cancellable_block)
        self.assertIn('this._recordLifecycleError("cancellable-unregister", error);', cancellable_block)
        self.assertIn("return false;", cancellable_block)
        self.assertLess(cancellable_block.index("try {"), cancellable_block.index("this._resourceRegistry.cancellables"))

        start = source.index("_unregisterProcess: function(token)")
        end = source.index("\n  _terminateProcess:", start)
        process_block = source[start:end]
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.processes, token)", process_block)
        self.assertIn('throw new Error("Process registry is unavailable");', process_block)
        self.assertIn('this._recordLifecycleError("process-unregister", error);', process_block)
        self.assertIn("return false;", process_block)
        self.assertLess(process_block.index("try {"), process_block.index("this._resourceRegistry.processes"))

    def test_orphaned_cancellables_are_retried_after_cancel_or_unregister_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedCancellable: function(token, cancelSucceeded)")
        end = source.index("\n  _registerProcess:", start)
        orphan_block = source[start:end]
        self.assertIn("this._orphanedCancellables = [];", orphan_block)
        self.assertIn("entry.token === key", orphan_block)
        self.assertIn("cancelSucceeded: cancelSucceeded === true", orphan_block)
        self.assertIn("let entry = {", orphan_block)
        self.assertIn("this._orphanedCancellables.push(entry);", orphan_block)
        self.assertIn('throw new Error("Cancellable orphan entry could not be tracked");', orphan_block)
        self.assertIn("_retryOrphanedCancellables: function()", orphan_block)
        self.assertIn("let registry = this._resourceRegistry && this._resourceRegistry.cancellables;", orphan_block)
        self.assertIn("if (!registry)", orphan_block)
        self.assertIn('this._recordLifecycleError("cancellable-state", new Error("Cancellable registry is unavailable"));', orphan_block)
        self.assertIn("let cancellable = registry[entry.token];", orphan_block)
        self.assertIn("this._unregisterCancellable(entry.token)", orphan_block)
        self.assertIn("let removed = this._orphanedCancellables.splice(index, 1);", orphan_block)
        self.assertIn("removed[0] !== entry", orphan_block)
        self.assertIn('throw new Error("Cancellable orphan entry could not be removed");', orphan_block)
        self.assertIn("this._untrackOrphanedCancellable(entry.token)", orphan_block)
        self.assertIn("if (entry.cancelSucceeded === true)", orphan_block)
        self.assertIn('new Error("Orphaned cancellable is missing from registry")', orphan_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVING ||", orphan_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVED;", orphan_block)
        self.assertIn('this._trackOrphanedCancellable(token, false)', orphan_block)

        start = source.index("_retryOrphanedProcesses: function(group)")
        end = source.index("\n  _terminateProcess: function(process)", start)
        process_orphan_block = source[start:end]
        self.assertIn("let registry = this._resourceRegistry && this._resourceRegistry.processes;", process_orphan_block)
        self.assertIn("if (!registry)", process_orphan_block)
        self.assertIn('this._recordLifecycleError("process-state", new Error("Process registry is unavailable"));', process_orphan_block)
        self.assertIn("this._unregisterProcess(entry.registryToken)", process_orphan_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVING ||", process_orphan_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVED;", process_orphan_block)
        self.assertIn('this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, false)', process_orphan_block)

        start = source.index("_cancelAllCancellables: function()")
        end = source.index("\n  _trackTimer:", start)
        block = source[start:end]
        self.assertIn("if (!this._trackOrphanedCancellable(token, true))", block)
        self.assertIn("if (!this._trackOrphanedCancellable(token, false))", block)
        self.assertIn("if (!this._untrackOrphanedCancellable(token))", block)
        self.assertIn('this._runTeardownGuarded("teardown-orphaned-cancellables", () => this._retryOrphanedCancellables());', source)

    def test_lifecycle_timers_ignore_removed_applet(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for marker in (
            "_scheduleSetupCheck: function()",
            "_scheduleAlarmCheck: function(delaySeconds)",
            "_scheduleStatusPoll: function()",
            "_scheduleDisplayTick: function()",
            "_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, operationGuard)",
            "_watchExternalApiEnvFile: function(path)",
        ):
            with self.subTest(marker=marker):
                start = source.index(marker)
                end = source.find("\n  _", start + len(marker))
                block = source[start:] if end == -1 else source[start:end]
                self.assertIn("this.appletRemoved", block)

    def test_applet_has_fault_containment_lifecycle(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for state in ("INITIALIZING", "RUNNING", "DEGRADED", "REMOVING", "REMOVED"):
            self.assertIn('"' + state + '"', source)
        for marker in (
            "_startLifecycle: function()",
            "_runGuarded: function(group, callback, fallback)",
            "_runStateGuarded: function(group, callback, fallback)",
            "_guardCallback: function(group, callback, fallback)",
            "_guardStateCallback: function(group, callback, fallback)",
            "_handleInitializationFailure: function(error)",
            "_beginTeardown: function()",
            "_finishTeardown: function()",
            "this._recordLifecycleError(\"init\", error);",
            'this._runTeardownGuarded("init-teardown", () => this.on_applet_removed_from_panel());',
            "return !this._initFailed &&",
            "this._disabledErrorGroups[key] = true;",
            "this.lifecycleState = LIFECYCLE_DEGRADED;",
            "this._runGuarded(\"panel-style\"",
            "this._runGuarded(\"panel-update\"",
        ):
            self.assertIn(marker, source)

        self.assertIn("LIFECYCLE_ERROR_WINDOW_MS = 60000", source)
        self.assertIn("LIFECYCLE_ERROR_THRESHOLD = 3", source)
        self.assertIn("entries = entries.slice(-LIFECYCLE_ERROR_THRESHOLD);", source)
        self.assertIn("if (!this._beginTeardown())", source)
        self.assertIn("this._finishTeardown();", source)
        self.assertIn("connectionId = target.connect(signal, this._guardStateCallback", source)
        self.assertNotIn("connectionId = this._connectSafe(target, signal", source)
        self.assertIn('let safeCallback = callback ? this._guardStateCallback("settings", callback, undefined) : null;', source)
        self.assertIn("_trackMonitor: function(monitor)", source)
        self.assertIn("_removeHotkey: function(id)", source)

    def test_initialization_failure_reports_before_destroying_tooltip(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_handleInitializationFailure: function(error)")
        end = source.index("\n  _beginTeardown:", start)
        block = source[start:end]
        self.assertLess(block.index("this.set_applet_tooltip"), block.index('this._runTeardownGuarded("init-teardown"'))

    def test_lifecycle_error_recording_cannot_escape_when_diagnostics_fail(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_recordLifecycleError: function(group, error)")
        end = source.index("\n  _runGuarded:", start)
        block = source[start:end]

        self.assertIn('let key = "unknown";', block)
        self.assertIn("let log = (value) =>", block)
        self.assertIn("catch (recordError)", block)
        self.assertIn("this._lifecycleErrors[key] = entries;", block)
        self.assertIn("this._lifecycleErrorCounts[key] = entries.length;", block)
        self.assertIn("log(error);", block)
        self.assertIn("log(recordError);", block)
        self.assertLess(block.index("try {"), block.index("this._lifecycleErrors[key] = entries;"))

    def test_signal_registration_failure_disconnects_new_connection(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_connectSafe: function(target, signal, callback, group)")
        end = source.index("\n  _disconnectAllSignals:", start)
        block = source[start:end]
        self.assertIn("if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.signals))", block)
        self.assertIn('throw new Error("Signal registry is unavailable");', block)
        self.assertIn("let signalEntry = { target: target, id: connectionId };", block)
        self.assertIn("this._resourceRegistry.signals.indexOf(signalEntry)", block)
        self.assertIn("let removed = signals.splice(index, 1);", block)
        self.assertIn("registryEntryRemovalSucceeded = true;", block)
        self.assertIn("signalDisconnected = true;", block)
        self.assertIn("target.disconnect(connectionId);", block)
        self.assertIn('this._recordLifecycleError("signal-disconnect", disconnectError);', block)
        self.assertIn('this._recordLifecycleError("signal-registration-rollback", rollbackError);', block)
        self.assertIn("throw registryError;", block)
        self.assertLess(block.index("try {"), block.index("target.connect(signal"))

    def test_signal_registration_tracks_failed_disconnect_rollbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_connectSafe: function(target, signal, callback, group)")
        end = source.index("\n  _trackOrphanedSignal:", start)
        block = source[start:end]
        self.assertIn('typeof target.disconnect !== "function"', block)
        self.assertIn("let disconnectResult = target.disconnect(connectionId);", block)
        self.assertIn("disconnectResult === false", block)
        self.assertIn("this._trackOrphanedSignal(target, connectionId, signalDisconnected) === true", block)
        self.assertIn("if (!signalDisconnected && !orphanTracked)", block)
        self.assertIn("signals[restoreIndex] = signalEntry;", block)
        orphan_start = source.index("_trackOrphanedSignal: function(target, id, disconnected)")
        orphan_end = source.index("\n  _disconnectOrphanedSignals:", orphan_start)
        orphan_block = source[orphan_start:orphan_end]
        self.assertIn('throw new Error("Signal orphan is invalid");', orphan_block)
        self.assertIn("let entry = {", orphan_block)
        self.assertIn("this._orphanedSignals.push(entry);", orphan_block)
        self.assertIn('throw new Error("Signal orphan entry could not be tracked");', orphan_block)
        self.assertIn("this._scheduleProcessCleanupRetry();", orphan_block)
        self.assertIn("_disconnectOrphanedSignals", source)

    def test_signal_cleanup_fails_closed_when_registry_is_unavailable(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_disconnectOrphanedSignals: function(target)")
        end = source.index("\n  _disconnectAllSignals:", start)
        orphan_block = source[start:end]
        self.assertIn('this._recordLifecycleError("signal-state", new Error("Signal orphan registry is unavailable"));', orphan_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVING ||", orphan_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVED);", orphan_block)
        self.assertIn('this._trackOrphanedSignal(connection.target, connection.id, false)', orphan_block)
        self.assertIn('new Error("Signal orphan entry is invalid")', orphan_block)
        self.assertIn("connection.id === undefined || connection.id === null", orphan_block)
        self.assertIn("return false;", orphan_block)

        start = source.index("_disconnectTrackedSignalsForTarget: function(target)")
        end = source.index("\n  _untrackSignal:", start)
        tracked_block = source[start:end]
        self.assertIn('this._recordLifecycleError("signal-state", new Error("Signal registry is unavailable"));', tracked_block)
        self.assertIn("return false;", tracked_block)

        start = source.index("_disconnectAllSignals: function()")
        end = source.index("\n  _disconnectTrackedSignalsForTarget:", start)
        all_block = source[start:end]
        self.assertIn("let success = true;", all_block)
        self.assertIn('this._recordLifecycleError("signal-state", new Error("Signal registry is unavailable"));', all_block)
        self.assertIn("return this._disconnectOrphanedSignals() === true;", all_block)
        self.assertIn("if (!this._trackOrphanedSignal(connection && connection.target, connection && connection.id, false))", all_block)
        self.assertIn("if (!this._disconnectOrphanedSignals())", all_block)
        self.assertIn('this._recordLifecycleError("signal-state", new Error("Orphaned signals remain after teardown"));', all_block)
        self.assertIn("return success;", all_block)

    def test_signal_teardown_reports_partial_disconnect_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_disconnectAllSignals: function()")
        end = source.index("\n  _disconnectTrackedSignalsForTarget:", start)
        block = source[start:end]
        self.assertIn("success = false;", block)
        self.assertIn("this._trackOrphanedSignal(connection && connection.target, connection && connection.id, true)", block)
        self.assertIn('new Error("Orphaned signals remain after teardown")', block)
        self.assertIn("return false;", block)

    def test_signal_registration_blocks_pending_orphans(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_connectSafe: function(target, signal, callback, group)")
        end = source.index("\n  _disconnectAllSignals:", start)
        block = source[start:end]
        self.assertIn("if (!Array.isArray(this._orphanedSignals))", block)
        self.assertIn('this._recordLifecycleError("signal-state", new Error("Signal orphan registry is unavailable"));', block)
        self.assertIn("if (this._orphanedSignals.length > 0)", block)
        self.assertIn("let orphanCleanupSucceeded = this._disconnectOrphanedSignals();", block)
        self.assertIn('this._recordLifecycleError("signal-state", new Error("An orphaned signal is still pending"));', block)
        self.assertLess(block.index("let orphanCleanupSucceeded = this._disconnectOrphanedSignals();"), block.index("target.connect(signal"))
        self.assertLess(block.index('new Error("An orphaned signal is still pending")'), block.index("target.connect(signal"))

    def test_signal_teardown_retries_disconnect_and_registry_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedSignal: function(target, id, disconnected)")
        end = source.index("\n  _clearMenuItems:", start)
        block = source[start:end]
        self.assertIn("disconnected: disconnected === true", block)
        self.assertIn("connection.disconnected === true", block)
        self.assertIn("this._untrackSignal(connection.target, connection.id)", block)
        self.assertIn("this._untrackOrphanedSignal(connection)", block)
        self.assertIn("if (!this._trackOrphanedSignal(connection && connection.target, connection && connection.id, false))", block)
        self.assertIn("if (!this._trackOrphanedSignal(connection && connection.target, connection && connection.id, true))", block)
        self.assertIn("_untrackSignal: function(target, id, connection)", block)
        self.assertIn("_untrackOrphanedSignal: function(connection)", block)
        self.assertIn("let removed = this._orphanedSignals.splice(index, 1);", block)
        self.assertIn("removed[0] !== entry", block)
        self.assertIn('throw new Error("Signal orphan entry could not be removed");', block)
        self.assertIn("let removed = signals.splice(index, 1);", block)
        self.assertIn('this._recordLifecycleError("signal-untrack", error);', block)

    def test_state_callbacks_are_not_suppressed_by_disabled_error_groups(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        state_start = source.index("_runStateGuarded: function(group, callback, fallback)")
        state_end = source.index("\n  _runTeardownGuarded:", state_start)
        state_block = source[state_start:state_end]
        self.assertIn("!this._lifecycleAllowsWork()", state_block)
        self.assertNotIn("_lifecycleGroupEnabled", state_block)
        callback_start = source.index("_guardStateCallback: function(group, callback, fallback)")
        callback_end = source.index("\n  _handleInitializationFailure:", callback_start)
        callback_block = source[callback_start:callback_end]
        self.assertIn("this._runStateGuarded(key, () => callback.apply(this, args), fallback)", callback_block)
        self.assertIn("this._recordLifecycleError(key, error);", state_block)

    def test_lifecycle_guards_contain_precondition_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for marker, precondition in (
            ("_runGuarded: function(group, callback, fallback)", "this._lifecycleAllowsWork()"),
            ("_runStateGuarded: function(group, callback, fallback)", "this._lifecycleAllowsWork()"),
        ):
            with self.subTest(marker=marker):
                start = source.index(marker)
                end = source.find("\n  _", start + len(marker))
                block = source[start:] if end == -1 else source[start:end]
                self.assertLess(block.index("try {"), block.index(precondition))
                self.assertIn("this._recordLifecycleError(key, error);", block)

    def test_menu_cleanup_is_not_suppressed_by_disabled_error_groups(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearMenuItems: function(menu)")
        end = source.index("\n  _trackDialog:", start)
        block = source[start:end]
        self.assertIn('this._runStateGuarded("menu-items"', block)
        self.assertNotIn('this._runGuarded("menu-items"', block)

    def test_menu_item_collection_contains_invalid_and_cyclic_menu_guards(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearMenuItems: function(menu)")
        end = source.index("\n  _trackDialog:", start)
        block = source[start:end]

        self.assertIn('throw new Error("Menu actor is unavailable or finalized");', block)
        self.assertIn("let visited = [];", block)
        self.assertIn("let collectionSucceeded = true;", block)
        self.assertIn("if (visited.indexOf(current) >= 0)", block)
        self.assertIn("visited.push(current);", block)
        self.assertIn('typeof current._getMenuItems !== "function"', block)
        self.assertIn('throw new Error("Menu item enumeration is unavailable");', block)
        self.assertIn("if (!Array.isArray(items))", block)
        self.assertIn('throw new Error("Menu items are unavailable");', block)
        self.assertIn('this._recordLifecycleError("menu-items", error);', block)
        self.assertIn("collectionSucceeded = false;", block)
        self.assertIn("if (!collectionSucceeded) {\n      return false;", block)
        self.assertIn('typeof menu.removeAll !== "function"', block)
        self.assertIn("let result = menu.removeAll();", block)
        self.assertIn('throw new Error("Menu items could not be removed");', block)
        self.assertLess(block.index("try {"), block.index("for (let item of items)"))

    def test_menu_teardown_reports_partial_cleanup_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyMenus: function()")
        end = source.index("\n  _clearDestroyedMenuReference:", start)
        block = source[start:end]
        self.assertIn("let success = true;", block)
        self.assertIn("success = false;", block)
        self.assertIn("if (!this._trackOrphanedMenu(menu, propertyName, group, true, signalsSucceeded, closeSucceeded, destroySucceeded))", block)
        self.assertIn("if (!this._trackOrphanedMenu(manager, propertyName, group, false, ungrabSucceeded && signalsSucceeded, true, destroySucceeded))", block)
        self.assertLess(
            block.index('"teardown-" + group + "-close"'),
            block.index('"teardown-" + group + "-signals"'),
        )
        self.assertIn('"_closeMenuSafely", [menu, false, true]', block)
        self.assertIn("let signalsSucceeded = closeSucceeded &&", block)
        self.assertIn("let destroySucceeded = closeSucceeded && signalsSucceeded &&", block)
        self.assertIn('let ungrabSucceeded = manager.grabbed === true', block)
        self.assertIn('let destroySucceeded = ungrabSucceeded && signalsSucceeded && this._runTeardownOperation("teardown-" + group + "-destroy"', block)
        self.assertIn("return success;", block)

    def test_menu_close_preflights_cinnamon_menu_stack(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_closeMenuSafely: function(menu, animate, requireGlobalMenuStack)")
        end = source.index("\n  _clearMenuItems:", start)
        block = source[start:end]
        self.assertIn("if (!actor ||", block)
        self.assertIn('throw new Error("Menu actor is already finalized");', block)
        self.assertIn("menu.isOpen === true", block)
        self.assertIn("global.menuStack", block)
        self.assertIn("stack.indexOf(menu)", block)
        self.assertIn("stack.lastIndexOf(menu)", block)
        self.assertIn('throw new Error("Open menu is missing or duplicated in Cinnamon menu stack");', block)
        self.assertIn('throw new Error("Open menu is not topmost in Cinnamon menu stack");', block)
        self.assertIn('throw new Error("Closed menu remains in Cinnamon menu stack");', block)
        self.assertLess(
            block.index("this._closeNestedMenusSafely(menu);"),
            block.index('throw new Error("Open menu is not topmost in Cinnamon menu stack");'),
        )

        teardown_start = source.index("_retryOrphanedMenus: function()")
        teardown_end = source.index("\n  _destroyAppletTooltip:", teardown_start)
        teardown_block = source[teardown_start:teardown_end]
        self.assertIn('"_closeMenuSafely",', teardown_block)
        self.assertLess(
            teardown_block.index('"_closeMenuSafely",'),
            teardown_block.index('"disconnectAllSignals"'),
        )

    def test_menu_refreshes_abort_when_menu_reset_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for line in source.splitlines():
            if "_clearMenuItems(" not in line or "function(menu)" in line:
                continue
            self.assertIn("if (!this._clearMenuItems(", line)

    def test_dialog_construction_failure_cleans_up_created_dialog(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_newSafeDialog: function(group)")
        end = source.index("\n  _dialogAddChild:", start)
        block = source[start:end]
        self.assertIn("let dialog = null;", block)
        self.assertIn("dialog = new ModalDialog.ModalDialog();", block)
        self.assertIn("if (dialog) {", block)
        self.assertIn("this._runTeardownOperation(cleanupGroup, dialog, \"close\")", block)
        self.assertIn("let destroySucceeded = closeSucceeded && this._destroyDialogAfterClose(dialog, cleanupGroup);", block)
        self.assertIn("if (closeSucceeded && destroySucceeded)", block)
        self.assertIn("if (!this._untrackDialog(dialog))", block)
        self.assertIn("this._trackOrphanedDialog(dialog, group, closeSucceeded, destroySucceeded);", block)

    def test_dialog_close_destroys_dialog_that_never_opened(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_dialogClose: function(dialog, group)")
        end = source.index("\n  _dialogOpen:", start)
        block = source[start:end]
        self.assertIn("let closeSucceeded = this._runTeardownOperation(", block)
        self.assertIn("let destroySucceeded = this._destroyDialogAfterClose(", block)
        self.assertIn("let closeStateSafe = this._dialogCloseState(dialog) !== null;", block)
        self.assertIn("this._trackOrphanedDialog(dialog, group, true, false);", block)

    def test_dialog_close_keeps_unrecognized_state_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_dialogClose: function(dialog, group)")
        end = source.index("\n  _dialogOpen:", start)
        block = source[start:end]
        self.assertIn("let closeStateSafe = this._dialogCloseState(dialog) !== null;", block)
        self.assertIn("this._trackOrphanedDialog(dialog, group, closeStateSafe, false);", block)
        helper_start = source.index("_dialogCloseState: function(dialog)")
        helper_end = source.index("\n  _trackOrphanedDialog:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn('return "closed";', helper_block)
        self.assertIn('return "closing";', helper_block)
        self.assertIn("return null;", helper_block)

    def test_dialog_orphan_tracking_retries_unknown_close_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedDialog: function(dialog, group, closeSucceeded, destroySucceeded)")
        end = source.index("\n  _untrackOrphanedDialog:", start)
        block = source[start:end]
        self.assertIn("let normalizedCloseSucceeded = closeSucceeded === true;", block)
        self.assertIn("this._dialogCloseState(dialog) === null", block)
        self.assertIn("normalizedCloseSucceeded = false;", block)
        self.assertIn("knownEntry.closeSucceeded = false;", block)

        retry_start = source.index("_retryOrphanedDialogs: function()")
        retry_end = source.index("\n  _destroyDialogAfterClose:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("if (closeSucceeded && this._dialogCloseState(entry.dialog) === null)", retry_block)
        self.assertIn("entry.closeSucceeded = false;", retry_block)

    def test_dialog_teardown_destroys_faded_out_dialogs(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyDialogAfterClose: function(dialog, group)")
        end = source.index("\n  _newSafeDialog:", start)
        block = source[start:end]
        self.assertIn('let closeState = this._dialogCloseState(dialog);', block)
        self.assertIn('closeState === "closed"', block)
        self.assertIn('closeState === "closing"', block)

    def test_orphaned_dialog_cleanup_is_retried_and_blocks_new_dialogs(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedDialog: function(dialog, group, closeSucceeded, destroySucceeded)")
        end = source.index("\n  _newSafeDialog:", start)
        orphan_block = source[start:end]
        self.assertIn("this._orphanedDialogs = [];", orphan_block)
        self.assertIn("entry.dialog === dialog", orphan_block)
        self.assertIn("let entry = {", orphan_block)
        self.assertIn("this._orphanedDialogs.push(entry);", orphan_block)
        self.assertIn('throw new Error("Dialog orphan entry could not be tracked");', orphan_block)
        self.assertIn("closeSucceeded: closeSucceeded === true", orphan_block)
        self.assertIn("destroySucceeded: destroySucceeded === true", orphan_block)
        self.assertIn("_retryOrphanedDialogs: function()", orphan_block)
        self.assertIn('"close"', orphan_block)
        self.assertIn('"destroy"', orphan_block)
        self.assertIn("this._untrackDialog(entry.dialog)", orphan_block)
        self.assertIn("this._orphanedDialogs.splice(index, 1);", orphan_block)

        start = source.index("_newSafeDialog: function(group)")
        end = source.index("\n  _dialogAddChild:", start)
        block = source[start:end]
        self.assertIn("Array.isArray(this._orphanedDialogs)", block)
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedDialogs();", block)
        self.assertIn('this._recordLifecycleError("dialog-state", new Error("An orphaned dialog is still pending"));', block)
        self.assertIn("return null;", block)
        self.assertIn('this._runTeardownGuarded("teardown-orphaned-dialogs", () => this._retryOrphanedDialogs());', source)

    def test_dialog_retry_reconstructs_pending_entries_from_registry(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_retryOrphanedDialogs: function()")
        end = source.index("\n  _newSafeDialog: function(group)", start)
        block = source[start:end]
        self.assertIn("let pendingDialogs = [];", block)
        self.assertIn("let addPendingDialog = (dialog, group, closeSucceeded, destroySucceeded) =>", block)
        self.assertIn("Dialog orphan registry is unavailable", block)
        self.assertIn("let dialogs = this._resourceRegistry && this._resourceRegistry.dialogs;", block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVING ||", block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVED;", block)
        self.assertIn("if ((!Array.isArray(this._orphanedDialogs) || inTeardown) && Array.isArray(dialogs))", block)
        self.assertIn('addPendingDialog(dialog, "dialog-registry", false, false);', block)
        self.assertIn("for (let index = pendingDialogs.length - 1;", block)
        self.assertIn("this._destroyDialogAfterClose(entry.dialog, \"teardown-orphaned-dialogs\")", block)
        self.assertIn("this._untrackDialog(entry.dialog)", block)

        helper_start = source.index("_destroyDialogAfterClose: function(dialog, group)")
        helper_end = source.index("\n  _newSafeDialog:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("this._dialogCloseState(dialog)", helper_block)
        state_start = source.index("_dialogCloseState: function(dialog)")
        state_end = source.index("\n  _trackOrphanedDialog:", state_start)
        state_block = source[state_start:state_end]
        self.assertIn("dialog.state === ModalDialog.State.CLOSED", state_block)
        self.assertIn("dialog.state === ModalDialog.State.CLOSING", state_block)
        self.assertIn("Cinnamon ModalDialog destroys itself after asynchronous close animation.", block)

    def test_dialog_child_preconditions_are_guarded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_dialogAddChild: function(dialog, child, group)")
        end = source.index("\n  _newSafeLabel:", start)
        block = source[start:end]
        self.assertLess(block.index("_runGuarded"), block.index("dialog.contentLayout"))
        self.assertIn("let result = dialog.contentLayout.add_child(child);", block)
        self.assertIn('throw new Error("Dialog child could not be added");', block)

    def test_dialog_action_preconditions_are_guarded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        buttons_start = source.index("_dialogSetButtons: function(dialog, buttons, group)")
        buttons_end = source.index("\n  _dialogClose:", buttons_start)
        buttons_block = source[buttons_start:buttons_end]
        self.assertLess(buttons_block.index("_runGuarded"), buttons_block.index("dialog.setButtons"))
        self.assertIn("let result = dialog.setButtons(safeButtons);", buttons_block)
        self.assertIn('throw new Error("Dialog buttons could not be set");', buttons_block)
        open_start = source.index("_dialogOpen: function(dialog, group)")
        open_end = source.index("\n  _destroyTrackedDialogs:", open_start)
        open_block = source[open_start:open_end]
        self.assertLess(open_block.index("_runGuarded"), open_block.index("dialog.open"))
        self.assertIn("return dialog.open() !== false;", open_block)

    def test_clipboard_set_preconditions_are_guarded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_setClipboardText: function(text)")
        end = source.index("\n  _describeNonTextClipboardPayload:", start)
        block = source[start:end]
        self.assertLess(block.index("try {"), block.index("this.clipboard.set_text"))
        self.assertIn("let result = this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);", block)
        self.assertIn('throw new Error("Clipboard text could not be set");', block)

    def test_menu_teardown_retains_handles_after_cleanup_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyMenus: function()")
        end = source.index("\n  _destroyAppletTooltip:", start)
        block = source[start:end]
        self.assertIn("let cleanupMenu = (menu, group, propertyName) => {", block)
        self.assertIn('let ungrabSucceeded = manager.grabbed === true', block)
        self.assertIn('this._runTeardownOperation("teardown-" + group + "-ungrab", manager, "_ungrab")', block)
        self.assertIn("this._runTeardownOperation(\"teardown-\" + group + \"-signals\", menu, \"disconnectAllSignals\", [], true)", block)
        self.assertIn("this._runTeardownOperation(\"teardown-\" + group + \"-close\"", block)
        self.assertIn("let destroySucceeded = closeSucceeded && signalsSucceeded && this._runTeardownOperation(\"teardown-\" + group + \"-destroy\"", block)
        self.assertIn("if (!this._trackOrphanedMenu(menu, propertyName, group, true, signalsSucceeded, closeSucceeded, destroySucceeded))", block)
        self.assertIn("if (cleanupMenu(menu, \"menu\", \"menu\"))", block)
        self.assertIn("if (cleanupMenu(contextMenu, \"context-menu\", \"_applet_context_menu\"))", block)
        self.assertIn("let cleanupManager = (manager, group, propertyName) => {", block)
        self.assertIn("_retryOrphanedMenus: function()", block)
        self.assertIn("this._runTeardownGuarded(\"teardown-orphaned-menus\", () => this._retryOrphanedMenus());", source)

    def test_root_menu_close_preflights_nested_menu_actors(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        nested_start = source.index("_closeNestedMenusSafely: function(menu)")
        nested_end = source.index("\n  _closeMenuSafely:", nested_start)
        nested_block = source[nested_start:nested_end]
        self.assertIn("let nestedMenus = [];", nested_block)
        self.assertIn("let visited = [];", nested_block)
        self.assertIn("current._getMenuItems()", nested_block)
        self.assertIn("this._closeMenuSafely(nestedMenus[index], false, false)", nested_block)

        close_start = source.index("_closeMenuSafely: function(menu, animate, requireGlobalMenuStack)")
        close_end = source.index("\n  _clearMenuItems:", close_start)
        close_block = source[close_start:close_end]
        self.assertIn("this._closeNestedMenusSafely(menu);", close_block)
        self.assertLess(close_block.index("this._closeNestedMenusSafely(menu);"), close_block.index("let result = menu.close(animate);"))

    def test_teardown_operations_fail_closed_on_false_or_missing_methods(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        helper_start = source.index("_runTeardownOperation: function(group, target, method, args, allowMissing)")
        helper_end = source.index("\n  _guardCallback:", helper_start)
        helper = source[helper_start:helper_end]
        self.assertIn("typeof operation !== \"function\"", helper)
        self.assertIn("allowMissing === true", helper)
        self.assertIn("if (result === false)", helper)
        self.assertIn("return succeeded;", helper)

        signal_start = source.index("_disconnectAllSignals: function()")
        signal_end = source.index("\n  _disconnectTrackedSignalsForTarget:", signal_start)
        signal_block = source[signal_start:signal_end]
        self.assertIn("_runTeardownOperation", signal_block)
        self.assertIn("continue;", signal_block)

        dialog_start = source.index("_destroyTrackedDialogs: function()")
        dialog_end = source.index("\n  _destroyMenus:", dialog_start)
        dialog_block = source[dialog_start:dialog_end]
        self.assertIn("!dialog || this._runTeardownOperation", dialog_block)
        self.assertIn("let previousLength = dialogs.length;", dialog_block)
        self.assertIn("let removed = dialogs.splice(index, 1);", dialog_block)
        self.assertIn('throw new Error("Dialog registry invalid entry could not be removed");', dialog_block)

        tooltip_start = source.index("_destroyAppletTooltip: function()")
        tooltip_end = source.index("\n  _trackMonitor:", tooltip_start)
        tooltip_block = source[tooltip_start:tooltip_end]
        self.assertIn("this._runTeardownOperation(\"teardown-tooltip\"", tooltip_block)
        self.assertIn("this._runTeardownOperation(\"teardown-orphaned-tooltip\"", tooltip_block)

    def test_menu_open_state_tolerates_missing_context_menu(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index('this._connectSafe(this.menu, "open-state-changed"')
        end = source.index("\n    this._connectSafe(this, \"orientation-changed\"", start)
        block = source[start:end]
        self.assertIn("if (!this._applet_context_menu || !this._applet_context_menu.isOpen)", block)

    def test_tooltip_teardown_retains_handle_after_destroy_failure(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyAppletTooltip: function()")
        end = source.index("\n  _trackMonitor:", start)
        block = source[start:end]
        self.assertIn("let destroyed = this._runTeardownOperation(\"teardown-tooltip\"", block)
        self.assertIn("if (destroyed)", block)
        self.assertIn("this._applet_tooltip = null;", block)
        self.assertIn("_clearDestroyedTooltip: function(tooltip)", block)
        self.assertIn('throw new Error("Tooltip reference could not be cleared");', block)
        self.assertIn('this._recordLifecycleError("teardown-tooltip", error);', block)
        self.assertIn("this._orphanedTooltip = true;", block)
        self.assertIn('this._runTeardownGuarded("teardown-orphaned-tooltip", () => this._retryOrphanedTooltip());', source)

    def test_tooltip_retry_uses_existing_reference_even_without_orphan_flag(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_retryOrphanedTooltip: function()")
        end = source.index("\n  _trackMonitor:", start)
        block = source[start:end]
        self.assertIn("let tooltip = this._applet_tooltip;", block)
        self.assertIn("if (!this._orphanedTooltip && !tooltip)", block)
        self.assertIn('this._runTeardownOperation("teardown-orphaned-tooltip", tooltip, "destroy")', block)

    def test_hotkey_mutations_are_not_suppressed_by_disabled_error_groups(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_registerHotkey: function(id, binding, callback)")
        end = source.index("\n  _removeHotkey:", start)
        register_block = source[start:end]
        self.assertNotIn('this._runGuarded("hotkeys"', register_block)
        self.assertIn('this._runStateGuarded("hotkeys"', register_block)

        start = source.index("_registerHotkeys: function()")
        end = source.index("\n  _onHotkeyChanged:", start)
        self.assertIn('this._runStateGuarded("hotkeys"', source[start:end])

    def test_hotkey_remove_failures_preserve_existing_definition(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_registerHotkey: function(id, binding, callback)")
        end = source.index("\n  _removeHotkey:", start)
        block = source[start:end]
        self.assertIn("let removed = this._runStateGuarded(\"hotkeys\"", block)
        self.assertIn("if (removeResult === false)", block)
        self.assertIn('throw new Error("Hotkey could not be removed")', block)
        self.assertIn("return true;", block)
        self.assertIn("if (!removed) {", block)
        self.assertIn("return;", block)

    def test_hotkey_registry_write_failures_roll_back_external_binding(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_registerHotkey: function(id, binding, callback)")
        end = source.index("\n  _removeHotkey:", start)
        block = source[start:end]
        self.assertIn('let tracked = this._runStateGuarded("hotkeys", () => {', block)
        self.assertIn('if (!this._resourceRegistry || !this._resourceRegistry.hotkeys || !this._hotkeyDefinitions)', block)
        self.assertIn("this._resourceRegistry.hotkeys[name] = true;", block)
        self.assertIn("let definition = { binding: accelerator, callback: callback };", block)
        self.assertIn("this._hotkeyDefinitions[name] = definition;", block)
        self.assertIn("Main.keybindingManager.removeHotKey(name);", block)
        self.assertIn('delete this._resourceRegistry.hotkeys[name];', block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)", block)
        self.assertIn('Hotkey registry cleanup could not be completed', block)
        self.assertIn('Hotkey definition cleanup could not be completed', block)
        self.assertIn("let removedExternally = false;", block)
        self.assertIn("if (removedExternally && previous)", block)
        self.assertIn('let restored = this._runStateGuarded("hotkeys", () => {', block)
        self.assertIn("this._orphanedHotkeyStates[name] = false;", block)
        self.assertLess(
            block.index("let restored = this._runStateGuarded(\"hotkeys\", () => {"),
            block.index("this._orphanedHotkeyStates[name] = false;"),
        )
        self.assertIn('throw new Error("Hotkey registry entry could not be removed");', block)

    def test_hotkey_registry_writes_are_verified_and_failed_rollbacks_are_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_registerHotkey: function(id, binding, callback)")
        end = source.index("\n  _trackOrphanedHotkey:", start)
        block = source[start:end]
        self.assertIn('throw new Error("Hotkey could not be registered");', block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)", block)
        self.assertIn('throw new Error("Hotkey rollback removal failed");', block)
        self.assertIn('throw new Error("Hotkey rollback orphan cleanup failed");', block)
        self.assertIn('throw new Error("Previous hotkey rollback orphan cleanup failed");', block)
        self.assertIn('throw new Error("Existing hotkey orphan cleanup failed");', block)
        self.assertIn('throw new Error("Previous hotkey could not be restored");', block)
        self.assertIn("this._trackOrphanedHotkey(name);", block)

    def test_orphaned_hotkeys_are_retried_during_teardown(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedHotkey: function(name, externallyRemoved)")
        end = source.index("\n  _removeHotkey:", start)
        block = source[start:end]
        self.assertIn("this._orphanedHotkeys = [];", block)
        self.assertIn("this._orphanedHotkeyStates = {};", block)
        self.assertIn("this._orphanedHotkeys.indexOf(key)", block)
        self.assertIn('throw new Error("Hotkey orphan entry could not be tracked");', block)
        self.assertIn("externallyRemoved === true", block)
        self.assertIn("this._scheduleProcessCleanupRetry();", block)
        self.assertIn('"teardown-orphaned-hotkeys"', block)
        self.assertIn('"removeHotKey"', block)
        self.assertIn("Hotkey registry entry could not be removed during orphan cleanup", block)
        self.assertIn("Hotkey definition could not be removed during orphan cleanup", block)
        self.assertIn("let entry = this._orphanedHotkeys[index];", block)
        self.assertIn("let removedFromArray = false;", block)
        self.assertIn("let removed = this._orphanedHotkeys.splice(index, 1);", block)
        self.assertIn("removed[0] !== entry", block)
        self.assertIn("removedFromArray = true;", block)
        self.assertIn('throw new Error("Hotkey orphan entry could not be removed");', block)
        self.assertIn('throw new Error("Hotkey orphan rollback could not restore the entry");', block)
        self.assertIn('this._recordLifecycleError("hotkey-orphan-rollback", rollbackError);', block)
        self.assertIn("this._runTeardownOperation(", block)
        self.assertIn("this._retryOrphanedHotkeys()", source)

    def test_hotkey_retry_reconstructs_pending_names_from_authoritative_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_retryOrphanedHotkeys: function(includeTracked)")
        end = source.index("\n  _removeHotkey: function(id)", start)
        block = source[start:end]
        self.assertIn("let pendingNames = [];", block)
        self.assertIn("let addPendingName = (value) =>", block)
        self.assertIn("let collectPendingNames = (values) =>", block)
        self.assertIn("Hotkey orphan registry is unavailable", block)
        self.assertIn("if (!Array.isArray(this._orphanedHotkeys))", block)
        self.assertIn("let includeTrackedHotkeys = includeTracked === true || inTeardown;", block)
        self.assertIn("collectPendingNames(this._orphanedHotkeyStates);", block)
        self.assertIn("if (includeTrackedHotkeys) {", block)
        self.assertIn("collectPendingNames(this._resourceRegistry && this._resourceRegistry.hotkeys);", block)
        self.assertIn("collectPendingNames(this._hotkeyDefinitions);", block)
        self.assertLess(
            block.index("if (!Array.isArray(this._orphanedHotkeys))"),
            block.index("collectPendingNames(this._orphanedHotkeyStates);"),
        )
        self.assertIn("for (let index = pendingNames.length - 1;", block)

    def test_hotkey_retry_reconciles_authoritative_state_with_existing_orphan_list(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_retryOrphanedHotkeys: function(includeTracked)")
        end = source.index("\n  _removeHotkey: function(id)", start)
        block = source[start:end]
        self.assertIn("if (Array.isArray(this._orphanedHotkeys))", block)
        self.assertIn("collectPendingNames(this._orphanedHotkeyStates);", block)
        self.assertIn("if (includeTrackedHotkeys) {", block)
        self.assertIn("collectPendingNames(this._resourceRegistry && this._resourceRegistry.hotkeys);", block)
        self.assertIn("collectPendingNames(this._hotkeyDefinitions);", block)
        self.assertIn("let trackedLiveHotkey = !inTeardown && !externallyRemoved && Boolean(", block)
        self.assertIn("if (trackedLiveHotkey) {", block)
        self.assertIn("this._untrackOrphanedHotkey(name)", block)
        self.assertLess(block.index("collectPendingNames(this._resourceRegistry && this._resourceRegistry.hotkeys);"), block.index("if (pendingNames.length === 0)"))

    def test_hotkey_teardown_registry_failures_do_not_escape(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_removeHotkey: function(id)")
        end = source.index("\n  _registerHotkeys:", start)
        block = source[start:end]
        self.assertIn('this._runTeardownGuarded("teardown-hotkeys", () => {', block)
        self.assertIn('throw new Error("Hotkey registry entry could not be removed during teardown");', block)
        self.assertIn('throw new Error("Hotkey definition could not be removed during teardown");', block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)", block)

    def test_empty_hotkey_binding_contains_definition_delete_failure(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index('let accelerator = typeof binding === "string"')
        end = source.index("\n    let hasBinding", start)
        block = source[start:end]
        self.assertIn('let definitionCleanupSucceeded = this._runStateGuarded("hotkeys"', block)
        self.assertIn('this._runStateGuarded("hotkeys", () => {', block)
        self.assertIn("let deleted = delete this._hotkeyDefinitions[name];", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)", block)
        self.assertIn('throw new Error("Hotkey definition could not be removed");', block)
        self.assertIn("return true;", block)
        self.assertIn("if (!definitionCleanupSucceeded)", block)
        self.assertIn("this._trackOrphanedHotkey(name, true);", block)

        retry_start = source.index("_retryOrphanedHotkeys: function(includeTracked)")
        retry_end = source.index("\n  _removeHotkey: function(id)", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("let externallyRemoved = this._orphanedHotkeyStates && this._orphanedHotkeyStates[name] === true;", retry_block)
        self.assertIn("!inTeardown && !externallyRemoved && Boolean(", retry_block)

    def test_menu_toggle_remains_recoverable_after_guarded_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("on_applet_clicked: function()")
        end = source.index("\n  on_applet_removed_from_panel:", start)
        block = source[start:end]
        self.assertIn('this._runStateGuarded("menu-toggle"', block)
        self.assertNotIn('this._runGuarded("menu-toggle"', block)
        self.assertIn("menu.open(true);", block)
        self.assertNotIn("menu.toggle();", block)

    def test_doctor_cannot_overwrite_an_active_recording_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_runDoctor: function(startupCheck)")
        end = source.index("\n  _applyDoctorPayload:", start)
        block = source[start:end]
        self.assertIn("if (!startupCheck && this._hasActiveRecordingState())", block)
        self.assertIn('this._setStatus(this.status, _("Finish the current recording before running doctor"), this.lastTranscript);', block)
        self.assertIn("this.isCommandRunning || this._statusCommandRunning || this._hasLocalProcessingWorkflow()", block)
        self.assertIn("this.alarmCheckToken || this.alarmActionToken || this.alarmMenuRefreshToken", block)
        self.assertLess(block.index("if (!startupCheck && this._hasActiveRecordingState())"), block.index("if (this._doctorCommandRunning)"))
        self.assertLess(
            block.index("if (this._doctorCommandRunning)"),
            block.index("this.isCommandRunning || this._statusCommandRunning || this._hasLocalProcessingWorkflow()")
        )

    def test_applet_teardown_owns_popup_menus_and_tooltip(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this.menu = new PopupMenu.PopupMenu(this.actor, this.orientation);", source)
        self.assertIn("Main.uiGroup.add_actor(this.menu.actor);", source)
        self.assertIn("_destroyMenus: function()", source)
        self.assertIn("_destroyAppletTooltip: function()", source)
        self.assertIn("this.disconnectAllSignals()", source)
        self.assertIn("this._runTeardownGuarded(\"teardown-menus\", () => this._destroyMenus());", source)
        self.assertIn("this._destroyAppletTooltip();", source)
        self.assertNotIn("new Applet.AppletPopupMenu(this, this.orientation)", source)
        teardown_start = source.index("on_applet_removed_from_panel: function()")
        teardown_end = source.index("\n  _baseArgs: function", teardown_start)
        teardown_block = source[teardown_start:teardown_end]
        self.assertIn("this.terminalWorkflowRunning = false;\n    this.terminalWorkflowToken = null;", teardown_block)

    def test_failed_target_signal_disconnect_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_disconnectTrackedSignalsForTarget: function(target)")
        end = source.index("\n  _clearMenuItems:", start)
        block = source[start:end]
        self.assertIn("let signals = this._resourceRegistry.signals;", block)
        self.assertIn("for (let index = signals.length - 1; index >= 0; index--)", block)
        self.assertIn("this._untrackSignal(target, connection.id, connection)", block)
        self.assertLess(block.index("try {"), block.index("Array.isArray(this._resourceRegistry.signals)"))
        self.assertIn('this._recordLifecycleError("teardown-target-signals", error);', block)

    def test_menu_items_are_not_removed_when_signal_cleanup_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearMenuItems: function(menu)")
        end = source.index("\n  _trackDialog:", start)
        block = source[start:end]
        self.assertIn("let signalsCleanupSucceeded = true;", block)
        self.assertIn("if (!this._disconnectTrackedSignalsForTarget(target))", block)
        self.assertIn("signalsCleanupSucceeded = false;", block)
        self.assertIn("if (!signalsCleanupSucceeded) {\n      return false;", block)
        self.assertIn("let nestedMenus = [];", block)
        self.assertIn("nestedMenus.push(item.menu);", block)
        self.assertIn("return this._closeMenuSafely(nestedMenu, false, false);", block)
        self.assertIn("let nestedMenuCleanupSucceeded = true;", block)
        self.assertIn("if (!nestedMenuCleanupSucceeded) {\n      return false;", block)
        self.assertLess(block.index("if (!signalsCleanupSucceeded)"), block.index("menu.removeAll()"))
        self.assertLess(block.index("_closeMenuSafely(nestedMenu"), block.index("menu.removeAll()"))

    def test_failed_signal_teardown_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_disconnectAllSignals: function()")
        end = source.index("\n  _disconnectTrackedSignalsForTarget:", start)
        block = source[start:end]
        self.assertIn("let signals = this._resourceRegistry.signals;", block)
        self.assertIn("for (let index = signals.length - 1; index >= 0; index--)", block)
        self.assertIn("this._untrackSignal(connection && connection.target, connection && connection.id, connection)", block)
        self.assertIn('this._recordLifecycleError("teardown-signals", error);', block)
        self.assertLess(block.index("try {"), block.index("Array.isArray(this._resourceRegistry.signals)"))

    def test_failed_dialog_close_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_dialogClose: function(dialog, group)")
        end = source.index("\n  _dialogOpen:", start)
        block = source[start:end]
        self.assertIn("return true;", block)
        self.assertIn("let closeSucceeded = this._runTeardownOperation(", block)
        self.assertIn('"close"', block)
        self.assertIn("if (!closeSucceeded) {", block)
        self.assertIn("let destroySucceeded = this._destroyDialogAfterClose(", block)
        self.assertIn("this._untrackDialog(dialog);", block)
        self.assertIn("this._trackOrphanedDialog(dialog, group, true, false);", block)
        self.assertIn("this._trackOrphanedDialog(dialog, group, false, false);", block)
        self.assertIn("return true;", block)
        self.assertLess(block.index("try {"), block.index("Array.isArray(this._resourceRegistry.dialogs)"))

    def test_dialog_close_reconciles_orphaned_dialogs_before_reporting_success(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_dialogClose: function(dialog, group)")
        end = source.index("\n  _dialogOpen:", start)
        block = source[start:end]
        self.assertIn('new Error("Dialog orphan registry is unavailable")', block)
        self.assertIn("let isOrphaned = this._orphanedDialogs.some((entry) => entry && entry.dialog === dialog);", block)
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedDialogs();", block)
        self.assertIn("let orphanStillPending = this._orphanedDialogs.some((entry) => entry && entry.dialog === dialog);", block)
        self.assertIn("return orphanCleanupSucceeded && !orphanStillPending;", block)

    def test_transcript_confirmation_does_not_advance_when_close_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_confirmPlaintextTranscriptList: function(completionCallback)")
        end = source.index("\n  _loadAllTranscriptsDocument:", start)
        block = source[start:end]
        self.assertIn("let failToOpen = () => {", block)
        self.assertIn('let closed = this._dialogClose(dialog, "transcript-list");', block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);', block)
        self.assertIn("let complete = (result, releasePrompt) =>", block)
        self.assertIn("if (ownsPrompt && releasePrompt !== false)", block)
        self.assertIn("complete(false, closed);", block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be closed")', block)
        show_index = block.index('label: _("Show transcripts")')
        show_block = block[show_index:]
        self.assertIn('let closed = this._dialogClose(dialog, "transcript-list");', show_block)
        self.assertIn('if (!closed)', show_block)
        self.assertIn('complete(false, false);', show_block)
        self.assertIn('return;\n          }\n          complete(true);', show_block)

    def test_dialog_teardown_untracks_only_after_close_and_destroy(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_destroyTrackedDialogs: function()")
        end = source.index("\n  _destroyMenus:", start)
        block = source[start:end]
        self.assertNotIn("dialogs.splice(0)", block)
        self.assertIn("for (let index = dialogs.length - 1; index >= 0; index--)", block)
        self.assertIn("!dialog || this._runTeardownOperation(\"teardown-dialog-close\"", block)
        self.assertIn("!dialog || (closeSucceeded && this._destroyDialogAfterClose(dialog, \"teardown-dialog-destroy\"))", block)
        self.assertIn("if (closeSucceeded && destroySucceeded)", block)
        self.assertIn("this._untrackDialog(dialog);", block)
        self.assertIn("dialogs.length !== previousLength - 1", block)
        self.assertIn('if (!this._trackOrphanedDialog(dialog, "teardown", true, true))', block)
        self.assertIn('if (!this._trackOrphanedDialog(dialog, "teardown", closeSucceeded, destroySucceeded))', block)
        self.assertLess(block.index("try {"), block.index("Array.isArray(this._resourceRegistry.dialogs)"))

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
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "notify-recording", "notifyRecording", this._onNotificationSettingsChanged, null)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "notify-complete", "notifyComplete", this._onNotificationSettingsChanged, null)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "notify-error", "notifyError", this._onNotificationSettingsChanged, null)', source)
        self.assertIn('this._commitSettingValue("notifyRecording", "notify-recording"', source)
        self.assertIn('this._commitSettingValue("notifyComplete", "notify-complete"', source)
        self.assertIn('this._commitSettingValue("notifyError", "notify-error"', source)
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
        alarm_start = source.index("_scheduleAlarmCheck: function(delaySeconds)")
        alarm_end = source.index("\n  _scheduleStatusPoll:", alarm_start)
        alarm_block = source[alarm_start:alarm_end]
        self.assertIn("try {\n        this._checkAlarms(false);\n      } finally {", alarm_block)
        self.assertIn("this._scheduleAlarmCheck(ALARM_CHECK_SECONDS);", alarm_block)

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
        self.assertIn("actor.remove_style_class_name(styleClass)", source)
        self.assertIn("actor.add_style_class_name(this._panelStyleClassForStatus(status))", source)
        self.assertIn("this._applyPanelStyle(this.status)", source)

    def test_panel_actor_mutations_skip_finalized_actors(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        style_start = source.index("_applyPanelStyle: function(status)")
        style_end = source.index("\n  _updatePanel: function()", style_start)
        style_block = source[style_start:style_end]
        self.assertIn("let actor = this.actor;", style_block)
        self.assertIn("actor.is_finalized", style_block)
        self.assertIn("actor.remove_style_class_name(styleClass)", style_block)
        self.assertIn("actor.add_style_class_name(this._panelStyleClassForStatus(status))", style_block)

        update_start = source.index("_updatePanel: function()")
        update_end = source.index("\n};\n\nfunction main", update_start)
        update_block = source[update_start:update_end]
        self.assertIn("let panelActor = this.actor;", update_block)
        self.assertIn("panelActor.is_finalized", update_block)
        self.assertIn("typeof this.set_applet_label === \"function\"", update_block)
        self.assertIn("typeof this.set_applet_tooltip === \"function\"", update_block)

    def test_menu_open_state_skips_finalized_applet_actor(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index('this._connectSafe(this.menu, "open-state-changed"')
        end = source.index('}, "menu-open-state");', start)
        block = source[start:end]
        self.assertIn("let actor = this.actor;", block)
        self.assertIn("actor.is_finalized", block)
        self.assertIn("typeof actor.change_style_pseudo_class === \"function\"", block)
        self.assertIn('actor.change_style_pseudo_class("checked", open);', block)

    def test_status_refresh_deduplicates_overlapping_cli_calls(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this._statusCommandRunning = false;", source)
        self.assertIn("_refreshStatus: function(fromStatusTimer) {", source)
        self.assertIn("if (this._statusCommandRunning) {", source)
        self.assertIn("if (this.isCommandRunning) {", source)
        self.assertIn("this._statusCommandRunning = true;", source)
        self.assertIn("try {", source)
        self.assertIn("} catch (err) {", source)
        self.assertIn('this._setStatusPreservingRecording("error", _("Status refresh failed: ") + safeError', source)
        self.assertIn("} finally {", source)
        self.assertIn("this._statusCommandRunning = false;", source)
        refresh_start = source.index("_refreshStatus: function(fromStatusTimer) {")
        refresh_end = source.index("\n  _hasCancelableRecordingWork:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn('if (this.status === "recording" || this.status === "processing") {', refresh_block)
        self.assertIn("this._scheduleStatusPoll();", refresh_block)
        self.assertIn('return this.status === "recording" || this.status === "processing";', refresh_block)
        status_reset = refresh_block.index("this._statusCommandRunning = false;")
        self.assertLess(
            status_reset,
            refresh_block.index("this._scheduleStatusPoll();", status_reset),
        )

    def test_stale_status_callback_cannot_release_newer_request(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        refresh_start = source.index("_refreshStatus: function(fromStatusTimer) {")
        refresh_end = source.index("\n  _hasCancelableRecordingWork:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("let statusCommandToken = {};", refresh_block)
        self.assertIn("this._statusCommandToken = statusCommandToken;", refresh_block)
        self.assertIn("if (this._statusCommandToken === statusCommandToken) {", refresh_block)
        self.assertIn("if (this._statusCommandToken !== statusCommandToken) {", refresh_block)
        self.assertIn("this._statusCommandToken = null;", refresh_block)
        self.assertLess(
            refresh_block.index("this._statusCommandToken = statusCommandToken;"),
            refresh_block.index("this._statusCommandRunning = true;"),
        )
        invalidate_start = source.index("_invalidateBackgroundCallbacksForRecording: function()")
        invalidate_end = source.index("\n  _runDoctor:", invalidate_start)
        invalidate_block = source[invalidate_start:invalidate_end]
        self.assertIn("this._statusCommandToken = null;", invalidate_block)

    def test_teardown_invalidates_status_refresh_owner(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("on_applet_removed_from_panel: function()")
        end = source.index("\n  _baseArgs:", start)
        block = source[start:end]
        self.assertIn("this._statusRefreshToken++;", block)
        self.assertIn("this._statusCommandToken = null;", block)
        self.assertIn("this._statusCommandRunning = false;", block)
        self.assertLess(
            block.index("this._statusCommandToken = null;"),
            block.index("this._terminateAllProcesses()"),
        )

    def test_status_refresh_respects_local_processing_workflows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_refreshStatus: function(fromStatusTimer) {")
        end = source.index("\n  _hasCancelableRecordingWork:", start)
        block = source[start:end]
        self.assertIn('let localStatusOwner = this.status === "processing" && this._hasLocalProcessingWorkflow();', block)
        helper_start = source.index("_hasLocalProcessingWorkflow: function(includePendingCleanup)")
        helper_end = source.index("\n  _setActiveLanguage:", helper_start)
        helper_block = source[helper_start:helper_end]
        for token in [
            "this.voiceModelActionToken",
            "this.alarmCheckToken",
            "this.alarmActionToken",
            "this.alarmMenuRefreshToken",
            "this.textInsertToken",
            "this.maintenanceCleanupFailed",
            "this.voiceModelCleanupFailed",
            "this.benchmarkCleanupFailed",
            "this.ollamaModelCleanupFailed",
            "this._hasPendingProcessCleanup()",
            "this._hasPendingDialogCleanup()",
            "this._hasPendingTextInsertCleanup()",
            "this.clipboardOverwriteDialog",
            "this._cleanupCommandToken",
            "this.settingsTransferToken",
            "this.setupDiagnosticsToken",
            "this.cleanupPreviewDialogToken",
            "this.cleanupPreviewDialog",
            "this.customLimitPromptToken",
            "this.autoPastePromptToken",
            "this.transcriptListPromptToken",
            "this.transcriptListPromptDialog",
            "this._doctorCommandRunning",
            "this.doctorCommandToken",
            "this.benchmarkFlowToken",
            "this.ollamaModelFlowToken",
            "this.ollamaModelInstallToken",
            "this.ollamaModelInstallRunning",
            "this.ollamaInstallWatchToken",
        ]:
            self.assertIn(token, helper_block)
        self.assertIn("this.terminalWorkflowToken", helper_block)
        self.assertNotIn("this.terminalWorkflowRunning ||", helper_block)
        self.assertIn("if (this.isCommandRunning || localStatusOwner) {", block)
        busy_guard = block.index("if (this.isCommandRunning || localStatusOwner) {")
        self.assertIn('return this.status === "recording" || this.status === "processing";', block[busy_guard:])

    def test_text_insert_cleanup_only_counts_paste_timer_orphans(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_hasPendingTextInsertCleanup: function()")
        end = source.index("\n  _hasLocalProcessingWorkflow:", start)
        block = source[start:end]
        self.assertIn("_hasPendingTextInsertResources: function()", block)
        self.assertIn("if (!Array.isArray(this._orphanedTimers))", block)
        self.assertIn("this._orphanedTimers.some((entry) => entry &&", block)
        self.assertIn('entry.name === "paste"', block)
        self.assertIn('entry.propertyName === "pasteTimer"', block)
        self.assertNotIn("this._orphanedTimers.length > 0", block)

    def test_invalidated_terminal_workflow_does_not_own_status_polling(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        helper_start = source.index("_hasLocalProcessingWorkflow: function(includePendingCleanup)")
        helper_end = source.index("\n  _setActiveLanguage:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("this.terminalWorkflowToken", helper_block)
        self.assertNotIn("this.terminalWorkflowRunning ||", helper_block)
        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        toggle_block = source[toggle_start:toggle_end]
        self.assertIn("this.terminalWorkflowToken = null;", toggle_block)
        self.assertNotIn("this.terminalWorkflowRunning = false;", toggle_block)

    def test_cancel_does_not_start_backend_cancel_for_local_processing_workflow(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_hasCancelableRecordingWork: function(statusOverride)")
        end = source.index("\n  _updateRecordingArtifactState:", start)
        block = source[start:end]
        self.assertIn('let localWorkflowOwnsProcessing = effectiveStatus === "processing"', block)
        self.assertIn("let localTextInsertAllowsCancel = Boolean(this.autoRelistenPending && this.textInsertToken);", block)
        self.assertIn("let localTextInsertOwnsRecording = Boolean(this.textInsertToken && !localTextInsertAllowsCancel);", block)
        self.assertIn("!localTextInsertAllowsCancel", block)
        self.assertIn("!localTextInsertOwnsRecording", block)
        self.assertIn("!this._recordingCommandToken", block)
        self.assertIn("this.recordingArtifactsPresent && !localWorkflowOwnsProcessing", block)
        self.assertIn("this._hasLocalProcessingWorkflow()", block)
        self.assertIn("(this.autoRelistenPending && !localWorkflowOwnsProcessing)", block)

    def test_duplicate_terminal_notification_still_closes_session(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_maybeNotify: function(previousStatus, status, message)")
        end = source.index("\n  _notify:", start)
        block = source[start:end]
        duplicate_index = block.index("if (key === this.lastNotificationKey) {")
        cleanup_index = block.index("this.notificationSessionActive = false;", duplicate_index)
        self.assertIn('status === "done" || status === "error"', block[duplicate_index:cleanup_index])
        self.assertIn('(status === "idle" && previousStatus !== "idle")', block[duplicate_index:cleanup_index])
        self.assertLess(duplicate_index, cleanup_index)

    def test_voice_model_settings_change_does_not_hide_busy_cleanup(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method in ["_onVoiceBackendSettingsChanged: function()", "_onLanguageSettingsChanged: function()"]:
            start = source.index(method)
            end = source.index("\n  _", start + len(method))
            block = source[start:end]
            release_index = block.index("let busyStateReleased = this._releaseBusyStateAfterProcessCleanup(")
            ready_index = block.index('this._setStatus("ready", _("Voice model operation cancelled by settings change")', release_index)
            self.assertIn("busyStateReleased &&", block[release_index:ready_index])
            self.assertIn("!this._recordingCommandToken", block[release_index:ready_index])
            self.assertIn("!this.isCommandRunning", block[release_index:ready_index])

    def test_text_model_settings_change_releases_finished_ollama_flow(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_onTextModelSettingsChanged: function()")
        end = source.index("\n  _onOpenAiFlexProcessingSettingsChanged:", start)
        block = source[start:end]
        self.assertIn("let hadOllamaOperation = Boolean(", block)
        for token in [
            "this.ollamaModelFlowToken",
            "this.ollamaInstallWatchToken",
            "this.ollamaModelInstallRunning",
            "this.ollamaModelInstallToken",
            "this.ollamaModelCleanupFailed",
        ]:
            self.assertIn(token, block)
        self.assertIn('this._setStatus("ready", _("Ollama operation cancelled by settings change")', block)
        self.assertIn("!this.notificationSessionActive", block)
        self.assertIn("!this._recordingCommandToken", block)
        self.assertIn("!this.isCommandRunning", block)
        self.assertIn("!this._hasLocalProcessingWorkflow()", block)
        self.assertLess(block.index("this._clearOllamaModelFlow();"), block.index('this._setStatus("ready"'))

    def test_initial_status_refresh_starts_after_lifecycle_is_running(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        init_start = source.index("_init: function(metadata, orientation, panelHeight, instanceId)")
        init_end = source.index("\n  _bindSettings:", init_start)
        init_block = source[init_start:init_end]
        self.assertLess(
            init_block.index("this.lifecycleState = LIFECYCLE_RUNNING;"),
            init_block.index("this._buildMenu();"),
        )
        self.assertLess(
            init_block.index("this.lifecycleState = LIFECYCLE_RUNNING;"),
            init_block.index("this._registerHotkeys();"),
        )
        self.assertLess(
            init_block.index("this.lifecycleState = LIFECYCLE_RUNNING;"),
            init_block.index("this._refreshStatus();"),
        )
        settings_index = init_block.index("this.settings = new Settings.AppletSettings(this, UUID, instanceId);")
        readiness_index = init_block.index("this.settings.isReady !== true", settings_index)
        bind_index = init_block.index("this._bindSettings();", settings_index)
        self.assertLess(readiness_index, bind_index)

    def test_setting_bindings_fail_closed(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_bindSetting: function(direction, key, propertyName, callback, callbackThis)")
        end = source.index("\n  _commitSettingValue:", start)
        block = source[start:end]

        self.assertIn("let bound = this.settings.bindProperty(", block)
        self.assertIn("if (bound !== true)", block)
        self.assertIn('throw new Error("Setting binding failed: " + String(key || "unknown"));', block)

    def test_setting_writes_reject_missing_keys_before_native_setter(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_setSettingValueOrThrow: function(key, value, errorMessage)")
        end = source.index("\n  _commitSettingValue:", start)
        block = source[start:end]

        self.assertIn("let settingsData = this.settings && this.settings.settingsData;", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(settingsData, settingKey)", block)
        self.assertIn("let result = this.settings.setValue(settingKey, value);", block)
        self.assertLess(
            block.index("Object.prototype.hasOwnProperty.call(settingsData, settingKey)"),
            block.index("this.settings.setValue(settingKey, value)"),
        )
        self.assertEqual(source.count("this.settings.setValue("), 1)

    def test_all_bound_settings_exist_in_schema(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        bound_keys = re.findall(
            r'_bindSetting\(Settings\.BindingDirection\.IN,\s*"([^"]+)"',
            source,
        )
        self.assertEqual(len(bound_keys), len(set(bound_keys)))
        self.assertEqual(
            [key for key in bound_keys if key not in schema],
            [],
        )

    def test_status_refresh_applies_only_latest_response(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this._statusRefreshToken = 0;", source)
        refresh_index = source.index("_refreshStatus: function(fromStatusTimer) {")
        self.assertIn("let statusRefreshToken = ++this._statusRefreshToken;", source[refresh_index:source.index("},", refresh_index)])
        self.assertIn("this._applyPayload(payload, statusRefreshToken);", source[refresh_index:source.index("},", refresh_index)])

        apply_index = source.index("_applyPayload: function(payload, statusRefreshToken) {")
        self.assertIn("if (typeof statusRefreshToken === \"number\" && statusRefreshToken !== this._statusRefreshToken) {", source)
        guard_return = source.index("return;", apply_index)
        guard_end = source.index("let status = this._normalizePayloadStatus", apply_index)
        self.assertLess(guard_return, guard_end)
        self.assertIn('if (typeof statusRefreshToken !== "number") {', source[apply_index:guard_end])
        self.assertIn("this._statusRefreshToken++;", source[apply_index:guard_end])

        spawn_index = source.index("_spawnJson: function(args, callback, options) {")
        spawn_end = source.index("_spawnText: function(args, callback, options) {", spawn_index)
        self.assertIn("_isStatusCommandArgs: function(args) {", source[:spawn_index])
        status_helper_index = source.index("_isStatusCommandArgs: function(args) {")
        status_helper_end = source.index("\n  _spawnJson:", status_helper_index)
        self.assertIn('args.length > 1 && String(args[1] || "") === "status"', source[status_helper_index:status_helper_end])
        self.assertNotIn("for (let i = 0; i < args.length; i++)", source[status_helper_index:status_helper_end])
        self.assertIn("if (!this._isStatusCommandArgs(normalizedArgs)) {", source[spawn_index:spawn_end])
        self.assertIn("this._statusRefreshToken++;", source[spawn_index:spawn_end])

    def test_status_refresh_reschedules_after_stale_response(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_refreshStatus: function(fromStatusTimer) {")
        end = source.index("\n  _hasCancelableRecordingWork:", start)
        block = source[start:end]
        finally_index = block.index("} finally {")
        self.assertIn("this._statusCommandRunning = false;", block[finally_index:])
        self.assertIn(
            'if (this.status === "recording" || this.status === "processing") {',
            block[finally_index:],
        )
        self.assertIn("this._scheduleStatusPoll();", block[finally_index:])
        catch_index = block.index("} catch (err) {")
        self.assertIn("if (fromStatusTimer === true) {", block[catch_index:])
        self.assertIn("return true;", block[catch_index:])

    def test_status_refresh_transport_errors_preserve_active_recording_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        apply_index = source.index("_applyPayload: function(payload, statusRefreshToken) {")
        error_index = source.index('if (payload.error || status === "error") {', apply_index)
        error_end = source.index("let hasTranscript", error_index)
        error_block = source[error_index:error_end]

        self.assertIn('let preserveRecordingOnError = arguments.length > 2 && arguments[2] === true;', error_block)
        self.assertIn('let activeBackendStatus = status === "recording" || status === "recorded" || status === "processing";', error_block)
        self.assertIn('payload.transport_error === true &&', error_block)
        self.assertIn('(preserveRecordingOnError || (typeof statusRefreshToken === "number" && this._hasActiveRecordingState()))', error_block)
        self.assertIn('(preserveRecordingOnError && activeBackendStatus);', error_block)
        self.assertIn('(typeof statusRefreshToken === "number" && this._hasActiveRecordingState())', error_block)
        self.assertIn('this._setStatusPreservingRecording("error", errorMessage, this.lastTranscript);', error_block)
        self.assertIn("this._scheduleStatusPoll();", error_block)
        self.assertIn('this._setStatus("error", errorMessage, this.lastTranscript);', error_block)
        self.assertLess(
            error_block.index("if (preserveActiveRecordingState)"),
            error_block.index('this._setStatus("error", errorMessage, this.lastTranscript);')
        )
        self.assertLess(
            error_block.index("if (preserveActiveRecordingState)"),
            error_block.index("this.cancelPendingWhileCommandRunning = false;")
        )

    def test_recording_command_errors_keep_active_lifecycle_polling(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        toggle_block = source[toggle_start:toggle_end]
        self.assertIn("let hasExistingRecordingWork = this._hasActiveRecordingState();", toggle_block)
        self.assertIn("this._applyPayloadSafely(", toggle_block)
        self.assertIn("undefined,\n        true", toggle_block)

        cancel_start = source.index("_cancelRecording: function(statusOverride)")
        cancel_end = source.index("\n  _invalidateBackgroundCallbacksForRecording:", cancel_start)
        cancel_block = source[cancel_start:cancel_end]
        self.assertIn("this._applyPayloadSafely(payload, undefined, true);", cancel_block)

        apply_start = source.index("_applyPayload: function(payload, statusRefreshToken)")
        apply_end = source.index("\n  _artifactEncryptionWarningKey:", apply_start)
        error_block = source[apply_start:apply_end]
        self.assertIn("if (arguments.length > 2) {", source[source.index("_applyPayloadSafely:"):apply_start])
        self.assertIn("payload.transport_error === true", error_block)
        self.assertIn("this._scheduleStatusPoll();", error_block)

    def test_status_refresh_error_does_not_clear_active_recording_metrics(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        apply_start = source.index("_applyPayload: function(payload, statusRefreshToken) {")
        apply_end = source.index("\n  _artifactEncryptionWarningKey:", apply_start)
        block = source[apply_start:apply_end]

        error_index = block.index('if (payload.error || status === "error") {')
        first_state_update = min(
            block.index("this._applyPayloadLanguage(payload, status);"),
            block.index("this._updateRecordingTiming(payload, status);"),
            block.index("this._applyMicrophoneLevel(payload.microphone_level, status);"),
        )
        self.assertGreater(first_state_update, error_index)

    def test_repeating_tracked_timers_remain_teardown_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        end = source.index("\n  _init:", start)
        block = source[start:end]
        self.assertIn("keepTimer = this._runStateGuarded(\"timer-\" + key, callback, false) === true;", block)
        self.assertIn("let previousActiveTimer = this._activeTrackedTimer;", block)
        self.assertIn("this._activeTrackedTimer = activeTimer;", block)
        self.assertIn("this._activeTrackedTimer === activeTimer", block)
        self.assertIn("let registryOwnsTimer = Boolean(", block)
        self.assertIn("let propertyOwnsTimer = Boolean(propertyName && this[propertyName] === sourceId);", block)
        self.assertIn("let timerIsCurrent = registryOwnsTimer && (!propertyName || propertyOwnsTimer);", block)
        self.assertIn("if (!timerIsCurrent) {", block)
        self.assertIn("let timerWasReplaced = !(", block)
        self.assertIn("if (timerWasReplaced) {", block)
        self.assertIn("if (!keepTimer) {", block)
        self.assertIn("let retireTimer = () => {", block)
        self.assertIn("let registryUntracked = this._untrackTimer(key, sourceId, propertyName);", block)
        self.assertIn("let orphanUntracked = this._untrackOrphanedTimer(key, sourceId);", block)
        self.assertIn("this._trackOrphanedTimer(key, sourceId, propertyName, true)", block)
        self.assertIn('this._recordLifecycleError("timer-state", new Error("Expired timer cleanup could not be tracked"));', block)
        self.assertIn("retireTimer();", block)
        self.assertLess(block.index("let keepTimer"), block.index("if (!keepTimer)"))
        self.assertIn("let deleted = delete this._resourceRegistry.timers[key];", block)
        self.assertIn("Timer rollback registry entry could not be removed", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.timers, key)", block)

    def test_tracked_timer_rejects_non_finite_delays_before_mainloop(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        end = source.index("\n  _init:", start)
        block = source[start:end]
        self.assertIn("let normalizedDelay;", block)
        self.assertIn("normalizedDelay = Number(delay === undefined || delay === null ? 1 : delay);", block)
        self.assertIn("if (!Number.isFinite(normalizedDelay))", block)
        self.assertIn('new Error("Timer delay is invalid")', block)
        self.assertIn('this._recordLifecycleError("timer-schedule", error);', block)
        self.assertIn("Mainloop.timeout_add_seconds(normalizedDelay, timerCallback)", block)
        self.assertIn("Mainloop.timeout_add(normalizedDelay, timerCallback)", block)

    def test_malformed_orphan_registries_fail_closed_before_new_resources(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        timer_start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        timer_end = source.index("\n  _init:", timer_start)
        timer_block = source[timer_start:timer_end]
        self.assertIn("if (!Array.isArray(this._orphanedTimers))", timer_block)
        self.assertIn('new Error("Timer orphan registry is unavailable")', timer_block)
        self.assertIn("return 0;", timer_block)

        dialog_start = source.index("_newSafeDialog: function(group)")
        dialog_end = source.index("\n  _dialogAddChild:", dialog_start)
        dialog_block = source[dialog_start:dialog_end]
        self.assertIn("if (!Array.isArray(this._orphanedDialogs))", dialog_block)
        self.assertIn('new Error("Dialog orphan registry is unavailable")', dialog_block)
        self.assertIn("return null;", dialog_block)

        monitor_start = source.index("_watchExternalApiEnvFile: function(path)")
        monitor_end = source.index("\n  _openExternalApiEnvEditor:", monitor_start)
        monitor_block = source[monitor_start:monitor_end]
        self.assertIn("if (!Array.isArray(this._orphanedMonitors))", monitor_block)
        self.assertIn('new Error("Monitor orphan registry is unavailable")', monitor_block)
        self.assertIn("return;", monitor_block)

        insert_start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        insert_end = source.index("\n  _restartRelistenRecording:", insert_start)
        insert_block = source[insert_start:insert_end]
        self.assertIn("if (!Array.isArray(this._orphanedTimers))", insert_block)
        self.assertIn('throw new Error("Timer orphan registry is unavailable")', insert_block)

    def test_failed_timer_removal_remains_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearTrackedTimer: function(name, propertyName, sourceAlreadyRemoved)")
        end = source.index("\n  _scheduleTrackedTimer:", start)
        block = source[start:end]
        self.assertIn("let sourceId = 0;", block)
        self.assertIn("let sourceRemovalSucceeded = sourceAlreadyRemoved === true;", block)
        self.assertIn("if (sourceAlreadyRemoved !== true && !sourceIsDispatching)", block)
        self.assertIn("let activeTimer = this._activeTrackedTimer;", block)
        self.assertIn("sourceIsDispatching", block)
        self.assertIn("if (sourceAlreadyRemoved !== true && !sourceIsDispatching)", block)
        self.assertIn("let removed = Mainloop.source_remove(sourceId);", block)
        self.assertIn('if (removed === false) {', block)
        self.assertIn("if (sourceId)", block)
        self.assertIn("this._trackOrphanedTimer(key, sourceId, propertyName, sourceRemovalSucceeded);", block)
        self.assertIn('this._recordLifecycleError("timer-clear", error);', block)
        self.assertIn("return false;", block)
        self.assertLess(block.index("Mainloop.source_remove(sourceId)"), block.index("delete this._resourceRegistry.timers[key]"))

    def test_timer_cleanup_handles_missing_registry_map(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearTrackedTimer: function(name, propertyName, sourceAlreadyRemoved)")
        end = source.index("\n  _scheduleTrackedTimer:", start)
        block = source[start:end]

        self.assertIn('let key = "timer";', block)
        self.assertIn("this._resourceRegistry && this._resourceRegistry.timers", block)
        self.assertIn(": 0;", block)
        self.assertIn('this._recordLifecycleError("timer-clear", error);', block)
        self.assertLess(block.index("try {"), block.index("Mainloop.source_remove(sourceId)"))

    def test_timer_clear_wrappers_propagate_cleanup_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for timer_name, property_name in [
            ("status", "statusTimer"),
            ("display", "displayTimer"),
            ("setup", "setupCheckTimer"),
            ("paste", "pasteTimer"),
            ("alarm", "alarmTimer"),
            ("ollama-install", "ollamaInstallWatchTimer"),
        ]:
            expected = f'return this._clearTrackedTimer("{timer_name}", "{property_name}");'
            self.assertIn(expected, source)

    def test_timer_registry_delete_failures_do_not_escape_cleanup(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearTrackedTimer: function(name, propertyName, sourceAlreadyRemoved)")
        end = source.index("\n  _scheduleTrackedTimer:", start)
        block = source[start:end]
        self.assertIn("let deleted = delete this._resourceRegistry.timers[key];", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.timers, key)", block)
        self.assertIn('throw new Error("Timer registry entry could not be removed");', block)
        self.assertIn('this._recordLifecycleError("timer-clear", error);', block)

    def test_timer_untracking_contains_registry_delete_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_untrackTimer: function(name, sourceId, propertyName)")
        end = source.index("\n  _clearTrackedTimer:", start)
        block = source[start:end]
        self.assertIn("let deleted = delete this._resourceRegistry.timers[key];", block)
        self.assertIn("Object.prototype.hasOwnProperty.call(this._resourceRegistry.timers, key)", block)
        self.assertIn('this._recordLifecycleError("timer-untrack", error);', block)
        self.assertIn("return false;", block)

    def test_keyboard_menu_close_contains_logging_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_closeMenuForKeyboardInsert: function()")
        end = source.index("\n  _clearTargetWindowXid:", start)
        block = source[start:end]
        self.assertIn("this._closeMenuSafely(this.menu, false, true);", block)
        self.assertIn("if (this.menu) {", block)
        self.assertNotIn("this.menu && this.menu.isOpen", block)
        self.assertIn('this._recordLifecycleError("keyboard-menu-close", err);', block)
        self.assertNotIn("global.logError(err);", block)

    def test_keyboard_clipboard_read_failures_complete_insert(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_spawnKeyboardWhenClipboardReady: function(")
        end = source.index("\n  _spawnKeyboardProcess:", start)
        block = source[start:end]
        self.assertIn("this._completeKeyboardInsertFailure(", block)
        self.assertIn('_("Clipboard could not be verified before automatic paste")', block)
        self.assertIn('if (!this.clipboard || !this.clipboard.get_text)', block)
        self.assertNotIn("global.logError(err);", block)
        self.assertLess(block.index("try {"), block.index("this.clipboard.get_text"))

        args_start = source.index("_spawnKeyboardArgs: function(")
        args_end = source.index("\n  _finishAppletTextInsert:", args_start)
        args_block = source[args_start:args_end]
        self.assertIn('this._completeKeyboardInsertFailure(completionCallback, _("Clipboard could not be verified before automatic paste"));', args_block)
        self.assertLess(args_block.index("try {"), args_block.index("this.clipboard.get_text"))

    def test_timer_reschedule_aborts_when_previous_timer_cannot_be_removed(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        end = source.index("\n  _init:", start)
        block = source[start:end]
        self.assertIn("if (this._clearTrackedTimer(key, propertyName) === false) {", block)
        self.assertIn("return 0;", block)
        self.assertLess(block.index("this._clearTrackedTimer(key, propertyName)"), block.index("let generation = this.spawnGeneration;"))

    def test_timer_registration_failure_rolls_back_created_source(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackTimer: function(name, sourceId, propertyName)")
        end = source.index("\n  _untrackTimer:", start)
        track_block = source[start:end]
        self.assertIn("if (!this._resourceRegistry || !this._resourceRegistry.timers)", track_block)
        self.assertIn('throw new Error("Timer registry is unavailable");', track_block)

        start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        end = source.index("\n  _init:", start)
        block = source[start:end]
        self.assertIn("let trackedSourceId = this._trackTimer(key, sourceId, propertyName);", block)
        self.assertIn("let registryHasTimer = !this._resourceRegistry", block)
        self.assertIn("throw new Error(\"Timer could not be registered\");", block)
        self.assertIn("let removed = Mainloop.source_remove(sourceId);", block)
        self.assertIn('this._recordLifecycleError("timer-cleanup", cleanupError);', block)
        self.assertIn("let sourceRemovalSucceeded = false;", block)
        self.assertIn("if (sourceId)", block)
        self.assertIn("this._trackOrphanedTimer(key, sourceId, propertyName, sourceRemovalSucceeded);", block)

    def test_orphaned_timer_cleanup_is_retried_and_blocks_new_schedules(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_trackOrphanedTimer: function(name, sourceId, propertyName, sourceRemoved)")
        end = source.index("\n  _clearTrackedTimer:", start)
        orphan_block = source[start:end]
        self.assertIn("this._orphanedTimers = [];", orphan_block)
        self.assertIn("entry.sourceId === sourceId", orphan_block)
        self.assertIn("let entry = {", orphan_block)
        self.assertIn("this._orphanedTimers.push(entry);", orphan_block)
        self.assertIn('throw new Error("Timer orphan entry could not be tracked");', orphan_block)
        self.assertIn("sourceRemoved: sourceRemoved === true", orphan_block)
        self.assertIn("_retryOrphanedTimers: function()", orphan_block)
        self.assertIn("Mainloop.source_remove(entry.sourceId)", orphan_block)
        self.assertIn("if (entry.sourceRemoved !== true)", orphan_block)
        self.assertIn("let untracked = this._untrackTimer(entry.name, entry.sourceId, entry.propertyName);", orphan_block)
        self.assertIn("let removed = this._orphanedTimers.splice(index, 1);", orphan_block)
        self.assertIn("removed[0] !== entry", orphan_block)
        self.assertIn('throw new Error("Timer orphan entry could not be removed");', orphan_block)
        self.assertIn("this._untrackOrphanedTimer(entry.name, entry.sourceId)", orphan_block)

        retry_start = source.index("_retryOrphanedTimers: function()")
        retry_end = source.index("\n  _clearTrackedTimer:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("let pendingTimers = [];", retry_block)
        self.assertIn("let addPendingTimer = (name, sourceId, propertyName, sourceRemoved) =>", retry_block)
        self.assertIn("Timer orphan registry is unavailable", retry_block)
        self.assertIn("let timers = this._resourceRegistry && this._resourceRegistry.timers;", retry_block)
        self.assertIn("let inTeardown = this.appletRemoved ||", retry_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVING ||", retry_block)
        self.assertIn("this.lifecycleState === LIFECYCLE_REMOVED;", retry_block)
        self.assertIn("addPendingTimer(name, timers[name], \"\", false);", retry_block)
        self.assertIn("if ((!Array.isArray(this._orphanedTimers) || inTeardown) && timers", retry_block)
        self.assertIn("for (let index = pendingTimers.length - 1;", retry_block)

        start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        end = source.index("\n  _init:", start)
        block = source[start:end]
        self.assertIn("Array.isArray(this._orphanedTimers)", block)
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedTimers();", block)
        self.assertIn('this._recordLifecycleError("timer-state", new Error("An orphaned timer is still pending"));', block)
        self.assertIn("return 0;", block)
        self.assertIn('this._runTeardownGuarded("teardown-orphaned-timers", () => this._retryOrphanedTimers());', source)

    def test_timer_orphan_deduplication_includes_timer_name(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        track_start = source.index("_trackOrphanedTimer: function(name, sourceId, propertyName, sourceRemoved)")
        track_end = source.index("\n  _clearTrackedTimer:", track_start)
        track_block = source[track_start:track_end]
        self.assertIn("entry.sourceId === sourceId && entry.name === key", track_block)

        retry_start = source.index("_retryOrphanedTimers: function()")
        retry_end = source.index("\n  _clearTrackedTimer:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn('let key = String(name || propertyName || "timer");', retry_block)
        self.assertIn("let sourceIdWasReusedForDifferentTimer = (name, sourceId) =>", retry_block)
        self.assertIn("let sourceIdWasReused = sourceIdWasReusedForDifferentTimer(entry.name, entry.sourceId);", retry_block)
        self.assertIn('sourceIdWasReused ? "" : entry.propertyName', retry_block)
        self.assertIn("entry.sourceRemoved === true || sourceIdWasReused", retry_block)
        self.assertIn("entry.sourceId === sourceId && entry.name === key", retry_block)

    def test_local_status_updates_invalidate_inflight_status_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        preserving_start = source.index("_setStatusPreservingRecording: function(status, message, transcript)")
        preserving_end = source.index("\n  _setStatus: function", preserving_start)
        preserving_block = source[preserving_start:preserving_end]
        self.assertIn("this._statusRefreshToken++;", preserving_block)

        set_status_index = source.index("_setStatus: function(status, message, transcript)")
        set_status_end = source.index("\n  _maybeNotify:", set_status_index)
        set_status_block = source[set_status_index:set_status_end]
        self.assertIn("try {", set_status_block)
        self.assertIn('this._recordLifecycleError("status-update", error);', set_status_block)
        self.assertIn("this._statusRefreshToken++;", set_status_block)
        self.assertLess(
            set_status_block.index("this._statusRefreshToken++;"),
            set_status_block.index("let previousStatus = this.status;"),
        )

        poll_start = source.index("_scheduleStatusPoll: function()")
        poll_end = source.index("\n  _scheduleDisplayTick:", poll_start)
        poll_block = source[poll_start:poll_end]
        self.assertIn('let timerId = this._scheduleTrackedTimer("status"', poll_block)
        self.assertIn("return this._refreshStatus(true) === true;", poll_block)
        self.assertIn("if (!timerId && (this.status === \"recording\" || this.status === \"processing\"))", poll_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Status polling timer could not be scheduled")', poll_block)

        display_start = source.index("_scheduleDisplayTick: function()")
        display_end = source.index("\n  _isUsableTargetWindow:", display_start)
        display_block = source[display_start:display_end]
        self.assertIn('let timerId = this._scheduleTrackedTimer("display"', display_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Recording display timer could not be scheduled")', display_block)

        setup_start = source.index("_scheduleSetupCheck: function()")
        setup_end = source.index("\n  _scheduleAlarmCheck:", setup_start)
        setup_block = source[setup_start:setup_end]
        self.assertIn('let timerId = this._scheduleTrackedTimer("setup"', setup_block)
        self.assertIn("let setupBusy = this._statusCommandRunning || this.isCommandRunning ||", setup_block)
        self.assertIn("this.alarmCheckToken || this.alarmActionToken || this.alarmMenuRefreshToken ||", setup_block)
        self.assertIn("if (setupBusy || this._hasActiveRecordingState()) {\n        this._scheduleSetupCheck();", setup_block)
        self.assertIn("this._runDoctor(true);", setup_block)
        self.assertIn('this._setStatusPreservingRecording("setup", _("Setup check timer could not be scheduled")', setup_block)

        alarm_start = source.index("_scheduleAlarmCheck: function(delaySeconds)")
        alarm_end = source.index("\n  _scheduleStatusPoll:", alarm_start)
        alarm_block = source[alarm_start:alarm_end]
        self.assertIn('let timerId = this._scheduleTrackedTimer("alarm"', alarm_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Alarm timer could not be scheduled")', alarm_block)

    def test_status_checks_use_spawn_json_timeout(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const STATUS_COMMAND_TIMEOUT_MS = 10000;", source)
        self.assertIn("_refreshStatus: function(fromStatusTimer) {", source)
        self.assertIn('}, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS, resourceGroup: "status" });', source)
        self.assertIn("_spawnJson: function(args, callback, options) {", source)
        self.assertIn('Object.prototype.hasOwnProperty.call(options, "timeoutMs")', source)
        self.assertIn("if (!done && !setupFailed && timeoutMs > 0 && !this._scheduleTrackedTimer", source)

    def test_text_spawn_invalidates_stale_status_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        spawn_text_index = source.index("_spawnText: function(args, callback, options) {")
        spawn_text_end = source.index("\n  _applyPayload:", spawn_text_index)
        spawn_text_block = source[spawn_text_index:spawn_text_end]
        self.assertIn("this._statusRefreshToken++;", spawn_text_block)
        self.assertIn('callbackFn("", result || {});', spawn_text_block)
        self.assertIn('callbackFn(utf8ByteLength(output) > MAX_SPAWN_TEXT_BYTES ? "" : output, result || {});', spawn_text_block)
        self.assertIn("this._scheduleTrackedTimer(timeoutKey", source)
        self.assertIn("this._terminateProcess(process);", source)
        self.assertIn("this._unregisterProcess(processToken);", source)
        self.assertNotIn("Util.spawn_async(normalizedArgs, handleOutput);", source)

    def test_doctor_checks_use_spawn_json_timeout(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const DOCTOR_COMMAND_TIMEOUT_MS = 20000;", source)
        self.assertIn("_runDoctor: function(startupCheck) {", source)
        self.assertIn("inputText: inputOption.inputText", source)
        self.assertIn("timeoutMs: DOCTOR_COMMAND_TIMEOUT_MS", source)
        self.assertIn('this.doctorSummaryItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Doctor: not checked"))', source)
        self.assertIn('this.diagnosticsMenuItem.menu.addMenuItem(this.doctorSummaryItem)', source)
        self.assertIn('_setDoctorSummary(_("Doctor: checking..."))', source)
        self.assertIn("_presentDoctorResult: function(message, critical, startupCheck)", source)
        self.assertIn('this._notify(_("Speed of Cinnamon doctor")', source)
        self.assertIn("_doctorSummary: function(payload)", source)
        self.assertIn('this._setMenuItemLabelSafely(this.doctorSummaryItem, this.doctorSummaryText || _("Doctor: not checked"))', source)

    def test_doctor_releases_state_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_runDoctor: function(startupCheck)")
        end = source.index("\n  _applyDoctorPayload:", start)
        block = source[start:end]
        self.assertIn("let doctorArgs;", block)
        self.assertIn("try {\n      doctorArgs = this._doctorArgs();", block)
        self.assertIn("if (this.doctorCommandToken === doctorToken) {", block)
        self.assertIn("this.doctorCommandToken = null;", block)
        self.assertIn("this._doctorCommandRunning = false;", block)
        self.assertIn('this._recordLifecycleError("doctor", error);', block)
        self.assertIn('let message = startupCheck ? _("Doctor could not be prepared")', block)

    def test_diagnostics_menu_can_benchmark_downloaded_models_from_selected_audio_file(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Benchmark downloaded models")', source)
        self.assertIn("_selectBenchmarkAudioFile: function()", source)
        self.assertIn("_benchmarkAudioFileDialogArgs: function()", source)
        self.assertIn("--file-selection", source)
        self.assertIn("--file-filter=Audio files | *.wav *.WAV *.flac *.FLAC *.mp3 *.MP3 *.ogg *.OGG *.oga *.OGA *.opus *.OPUS *.m4a *.M4A *.aac *.AAC *.webm *.WEBM", source)
        self.assertIn("_benchmarkDownloadedModels: function(audioPath, flowToken)", source)
        self.assertIn('return [this._cliCommand(), "benchmark-models", String(audioPath || ""), "--language", String(this._currentLanguage()), "--json"]', source)
        self.assertIn("_benchmarkDownloadedModels(audioPath, flowToken)", source)
        self.assertIn("BENCHMARK_COMMAND_TIMEOUT_MS", source)

    def test_benchmark_audio_selection_ignores_stale_dialog_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        select_start = source.index("_selectBenchmarkAudioFile: function()")
        select_end = source.index("\n  _benchmarkDownloadedModels:", select_start)
        select_block = source[select_start:select_end]
        self.assertIn("if (this.isCommandRunning || this._hasActiveRecordingState() || this.benchmarkFlowToken || this._hasLocalProcessingWorkflow())", select_block)
        self.assertIn("let flowToken = {};", select_block)
        self.assertIn("this.benchmarkFlowToken = flowToken;", select_block)
        self.assertIn("this.benchmarkFlowToken !== flowToken", select_block)
        self.assertIn('this._spawnText(audioDialogArgs, (output, result) => {', select_block)
        self.assertIn("result.startupFailed === true", select_block)
        self.assertIn("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))", select_block)
        self.assertIn('this._setStatus("error", _("Could not open benchmark audio selection")', select_block)
        self.assertLess(
            select_block.index("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))"),
            select_block.index('let audioPath = String(output || "").trim();')
        )
        self.assertIn("this._benchmarkDownloadedModels(audioPath, flowToken);", select_block)
        self.assertIn('resourceGroup: "benchmark"', select_block)

        benchmark_start = source.index("_benchmarkDownloadedModels: function(audioPath, flowToken)")
        benchmark_end = source.index("\n  _setAlarmOptionStatus:", benchmark_start)
        benchmark_block = source[benchmark_start:benchmark_end]
        self.assertIn("flowToken = flowToken || this.benchmarkFlowToken;", benchmark_block)
        self.assertIn("if (!flowToken || this.benchmarkFlowToken !== flowToken)", benchmark_block)
        self.assertIn("if (this.isCommandRunning || (this._hasActiveRecordingState() && this.status !== \"processing\"))", benchmark_block)
        self.assertIn("let benchmarkArgs;", benchmark_block)
        self.assertIn("benchmarkArgs = this._benchmarkArgs(audioPath);", benchmark_block)
        self.assertIn('this._setStatus("error", _("Could not prepare benchmark command: ") + safeError', benchmark_block)
        self.assertIn("this.isCommandRunning = true;", benchmark_block)
        self.assertIn("this.isCommandRunning = false;", benchmark_block)
        self.assertIn("this.benchmarkFlowToken = null;", benchmark_block)
        self.assertIn("this.benchmarkFlowToken !== flowToken", benchmark_block)
        self.assertIn('resourceGroup: "benchmark"', benchmark_block)
        self.assertIn("this.benchmarkFlowToken = null;", benchmark_block)
        self.assertIn('let fastest = typeof payload.fastest_model === "string" ? payload.fastest_model.trim() : "";', benchmark_block)
        self.assertNotIn('let fastest = String(payload.fastest_model || "").trim();', benchmark_block)

    def test_benchmark_flow_releases_token_when_dialog_arguments_fail(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_selectBenchmarkAudioFile: function()")
        end = source.index("\n  _benchmarkDownloadedModels:", start)
        block = source[start:end]
        self.assertIn("let audioDialogArgs;", block)
        self.assertIn("try {\n      audioDialogArgs = this._benchmarkAudioFileDialogArgs();", block)
        self.assertIn("this._spawnText(audioDialogArgs,", block)
        self.assertIn("if (this.benchmarkFlowToken === flowToken) {", block)
        self.assertIn("this.benchmarkFlowToken = null;", block)
        self.assertIn('this._recordLifecycleError("benchmark-flow", error);', block)

    def test_stale_model_and_benchmark_callbacks_preserve_new_command_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, token_name, token_value in [
            ("_benchmarkDownloadedModels: function(audioPath, flowToken)", "\n  _setAlarmOptionStatus:", "benchmarkFlowToken", "flowToken"),
            ("_downloadVoiceModel: function(model)", "\n  _removeVoiceModel:", "voiceModelActionToken", "actionToken"),
            ("_removeVoiceModel: function(model)", "\n  _selectVoiceModel:", "voiceModelActionToken", "actionToken"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            guard = f"if (this.{token_name} !== {token_value} || !this._lifecycleAllowsWork())"
            self.assertIn(guard, block)
            guard_index = block.index(guard)
            stale_block = block[guard_index:block.index("return;", guard_index) + len("return;")]
            self.assertNotIn("this.isCommandRunning = false;", stale_block)
            self.assertIn("this._releaseBusyStateAfterProcessCleanup", stale_block)

    def test_saved_diagnostics_does_not_copy_or_display_full_path(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_setStatus("done", _("Saved diagnostics"), this.lastTranscript)', source)
        self.assertNotIn("payload.saved_path", source)
        self.assertNotIn('Saved diagnostics: "', source)

    def test_transcript_export_status_does_not_render_local_path(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('let message = _("Exported encrypted transcript bundle");', source)
        self.assertNotIn('_("Exported encrypted transcript bundle: ") + path', source)
        self.assertIn('this._openFolder(GLib.path_get_dirname(path), _("Opened transcript export folder"));', source)

    def test_imported_settings_are_type_hardened_before_persistence(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const BOOLEAN_IMPORT_SETTINGS = {", source)
        self.assertIn("const IMPORT_TEXT_SETTINGS = {", source)
        self.assertIn("const LANGUAGE_CODES = [", source)
        self.assertIn("const TRANSCRIBER_METHODS = [", source)
        self.assertIn("const POST_PROCESS_BACKENDS = [", source)
        self.assertIn('typeof value === "boolean" ? value : Boolean(fallback)', source)
        self.assertIn('key === "max-seconds"', source)
        self.assertIn('key === "typing-delay-ms"', source)
        self.assertIn("this._coerceImportedEnumSetting(value, LANGUAGE_CODES, fallback)", source)
        self.assertIn("this._coerceImportedEnumSetting(value, RECORDER_METHODS, fallback)", source)
        self.assertIn("this._coerceImportedEnumSetting(value, OUTPUT_METHODS, fallback)", source)
        self.assertIn('key === "personal-context" || key === "vocabulary"', source)
        self.assertIn('if (typeof value !== "string")', source)
        self.assertIn("_coerceImportedEnumSetting: function(value, allowedValues, fallback)", source)

    def test_imported_settings_commit_persistently_before_local_mutation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_applyImportedSettings: function(settings)")
        end = source.index("\n  _coerceSpawnArgs:", start)
        block = source[start:end]
        self.assertIn('settings = settings && typeof settings === "object" ? settings : {};', block)
        self.assertIn("let pending = [];", block)
        self.assertIn("let attemptedWrites = [];", block)
        self.assertIn(
            'this._setSettingValueOrThrow(item.key, item.value, "Imported setting could not be saved");',
            block,
        )
        self.assertIn(
            'this._setSettingValueOrThrow(item.key, item.previous, "Imported setting rollback failed");',
            block,
        )
        self.assertIn("this[item.prop] = item.value;", block)
        self.assertLess(
            block.index("_setSettingValueOrThrow(item.key, item.value"),
            block.index("this[item.prop] = item.value;"),
        )

    def test_simple_setting_choices_commit_before_local_mutation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        helper_start = source.index("_commitSettingValue: function(propertyName, key, value, group, errorMessage)")
        helper_end = source.index("\n  _connectSafe:", helper_start)
        helper = source[helper_start:helper_end]
        self.assertIn("let previous = this[propertyName];", helper)
        self.assertIn('this._setSettingValueOrThrow(key, value, "Setting could not be saved");', helper)
        self.assertIn("this[propertyName] = value;", helper)
        self.assertIn("this[propertyName] = previous;", helper)
        self.assertIn("_setSettingValueOrThrow", helper)

        for method, next_method, property_name in [
            ("_selectRecorder: function(method)", "\n  _normalizeRecordingLimit:", "recorder"),
            ("_selectRecordingLimit: function(seconds)", "\n  _customRecordingLimitPromptArgs:", "maxSeconds"),
            ("_selectTranscriptStorageLimit: function(limit)", "\n  _customTranscriptLimitPromptArgs:", "maxTranscriptFiles"),
            ("_selectInputSource: function(name, label)", "\n  _selectDefaultInputSource:", "inputDevice"),
            ("_selectOutputMethod: function(method)", "\n  _optionLabel:", "insertMethod"),
            ("_setAutoPasteTitles: function(values)", "\n  _toggleAutoPasteTitle:", "autoPasteWindowTitle"),
            ("_toggleOpenAiFlexProcessing: function()", "\n  _normalizeLanguage:", "openaiCompatibleFlexProcessing"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f'this._commitSettingValue("{property_name}"', block)

    def test_settings_export_uses_stdin_not_process_arguments_for_snapshot(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('return [this._cliCommand(), "doctor", "--applet", "--settings-json-stdin", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "setup", "--applet", "--settings-json-stdin", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "diagnostics", "--applet", "--settings-json-stdin", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "diagnostics", "--applet", "--settings-json-stdin", "--save", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "settings-export", "--settings-json-stdin", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "settings-import", "--confirm-plaintext-settings-output", "--json"];', source)
        self.assertNotIn('"settings-export", "--settings-json", JSON.stringify', source)
        self.assertNotIn('"doctor", "--applet", "--settings-json", JSON.stringify', source)
        self.assertNotIn('"setup", "--applet", "--settings-json", JSON.stringify', source)
        self.assertNotIn('"diagnostics", "--applet", "--settings-json", JSON.stringify', source)
        self.assertIn("_appletLifecycleDiagnostics: function()", source)
        self.assertIn("error_counts: errorCounts", source)
        self.assertIn("disabled_groups: disabledGroups", source)
        self.assertIn("process_groups: processGroups", source)
        self.assertIn("let orphanedResourceValues = {};", source)
        self.assertIn("orphaned_signals: orphanedResourceCounts.signals", source)
        self.assertIn("orphaned_hotkeys: orphanedResourceCounts.hotkeys", source)
        self.assertIn("orphaned_processes: orphanedResourceCounts.processes", source)
        self.assertIn("orphaned_timers: orphanedResourceCounts.timers", source)
        self.assertIn("orphaned_dialogs: orphanedResourceCounts.dialogs", source)
        self.assertIn("orphaned_monitors: orphanedResourceCounts.monitors", source)
        self.assertIn("orphaned_cancellables: orphanedResourceCounts.cancellables", source)
        self.assertIn("orphaned_tooltip: orphanedTooltip ? 1 : 0", source)
        self.assertIn("orphaned_total: orphanedTotal", source)
        self.assertIn("let registryValue = (name, fallback) =>", source)
        self.assertIn('let processes = registryValue("processes", {});', source)
        self.assertIn("let processEntry = processes[token];", source)
        self.assertIn('if (!processEntry || typeof processEntry !== "object")', source)
        self.assertIn("let lifecycleErrorCounts = {};", source)
        self.assertIn("let disabledErrorGroups = {};", source)
        self.assertIn('this._recordLifecycleError("diagnostics", error);', source)
        self.assertIn("let countArrayEntries = (value) =>", source)
        self.assertIn("_settingsSnapshotInputOption: function(includeLifecycle, preserveMultilineText)", source)
        self.assertIn("_settingsSnapshotInputOptionOrNull: function(includeLifecycle, errorStatus, preserveMultilineText)", source)
        self.assertIn("let inputOption = this._settingsSnapshotInputOptionOrNull(false, undefined, true);", source)
        self.assertIn('snapshot["applet-lifecycle"] = this._appletLifecycleDiagnostics();', source)
        self.assertIn("this._spawnJson(doctorArgs, (payload) => {", source)
        self.assertIn("let inputOption = this._settingsSnapshotInputOptionOrNull(false);", source)
        self.assertIn("let inputOption = this._settingsSnapshotInputOptionOrNull(true);", source)
        self.assertIn("}, inputOption);", source)
        self.assertIn("let hasInput = options.inputText !== null && options.inputText !== undefined;", source)
        self.assertIn("flags |= Gio.SubprocessFlags.STDIN_PIPE;", source)
        self.assertIn("stdin.write_all_async", source)
        self.assertIn("let assertInputWriteSucceeded = (writeResult) =>", source)
        self.assertIn("assertInputWriteSucceeded(stream.write_all_finish(result));", source)
        self.assertIn("assertInputWriteSucceeded(stdin.write_all(inputBytes, null));", source)
        self.assertIn("let closeInput = (stream) =>", source)
        self.assertIn('throw new Error("Subprocess input close failed");', source)

    def test_snapshot_bound_actions_preflight_before_setting_action_tokens(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        methods = [
            ("_runDoctor: function(startupCheck)", "this._doctorCommandRunning = true;"),
            ("_openProfanityFilterList: function()", "this.setupDiagnosticsToken = actionToken;"),
            ("_copySetupPlan: function()", "this.setupDiagnosticsToken = actionToken;"),
            ("_copySetupCommands: function()", "this.setupDiagnosticsToken = actionToken;"),
            ("_copyDiagnostics: function()", "this.setupDiagnosticsToken = actionToken;"),
            ("_saveDiagnostics: function()", "this.setupDiagnosticsToken = actionToken;"),
            ("_exportSettings: function()", "this.settingsTransferToken = transferToken;"),
        ]
        for index, (method, token_assignment) in enumerate(methods):
            start = source.index(method)
            end = source.index("\n  " + methods[index + 1][0].split(":", 1)[0] + ":" if index + 1 < len(methods) else "\n  _importSettings:", start)
            block = source[start:end]
            self.assertIn("_settingsSnapshotInputOptionOrNull", block)
            self.assertIn("if (!inputOption)", block)
            self.assertLess(block.index("_settingsSnapshotInputOptionOrNull"), block.index(token_assignment))

    def test_recording_status_shows_microphone_level(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.microphoneLevelItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Microphone: idle"))', source)
        self.assertIn("this._applyMicrophoneLevel(payload.microphone_level, status);", source)
        self.assertIn("_microphoneLevelText: function()", source)
        self.assertIn("_levelBar: function(percent)", source)
        self.assertIn('this._setMenuItemLabelSafely(this.microphoneLevelItem, this._microphoneLevelText());', source)

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
        self.assertIn('let text = typeof value === "string" ? value.replace(/\\s+/g, " ").trim() : "";', source)
        self.assertIn('typeof text !== "string" || !this._lifecycleAllowsWork()', source)
        self.assertIn('typeof label === "string" ? label : ""', source)
        self.assertIn("item.label.clutter_text.ellipsize = options.wrap ? Pango.EllipsizeMode.NONE : Pango.EllipsizeMode.END", source)
        style_start = source.index("_styleMenuItemLabel: function(item, options)")
        style_end = source.index("\n  _selectionMenuItem:", style_start)
        style_block = source[style_start:style_end]
        self.assertLess(style_block.index("_runGuarded"), style_block.index("item.label"))
        submenu_start = source.index("_styleSelectionSubmenu: function(menuItem)")
        submenu_end = source.index("\n  _styleMenuItemLabel:", submenu_start)
        submenu_block = source[submenu_start:submenu_end]
        self.assertLess(submenu_block.index("_runGuarded"), submenu_block.index("menuItem.menu"))

    def test_applet_adds_frontend_validation_for_long_or_invalid_text_fields(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const CLI_TEXT_SETTINGS = {", source)
        self.assertIn("const MAX_SETTING_TEXT_CHARS = 4096;", source)
        self.assertIn('const CLI_RUNTIME_TEXT_LIMITS = {', source)
        self.assertIn('"input device": 256,', source)
        self.assertIn('"whisper model": 240', source)
        self.assertIn("_coerceCliTextArg: function(value, fieldName, allowNewlines)", source)
        self.assertIn("_coerceCliTextArgOrFallback: function(value, fieldName, fallback)", source)
        self.assertIn("_appendCliOptionWithinBudget: function(args, flag, value)", source)
        self.assertIn('if (value !== undefined && value !== null && typeof value !== "string")', source)
        self.assertIn('let normalized = typeof value === "string" ? value : "";', source)
        self.assertIn('"personal-context": "personal context"', source)
        self.assertIn('"vocabulary": "vocabulary"', source)
        self.assertIn('let safeOpenAiCompatibleUrl = this._validatedExternalApiUrlOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL);', source)
        self.assertIn('let safePersonalContext = this._coerceCliTextArgOrFallback(this._singleLineCliTextValue(this.personalContext), "personal context", "");', source)
        self.assertIn('let safeVocabulary = this._coerceCliTextArgOrFallback(this._singleLineCliTextValue(this.vocabulary), "vocabulary", "");', source)
        self.assertIn("for (let key in CLI_TEXT_SETTINGS)", source)
        self.assertIn('let safeOllamaUrl = this._validatedExternalApiUrlOrFallback(this.ollamaUrl, "ollama URL", DEFAULT_OLLAMA_URL);', source)
        self.assertIn('let safeOpenAiCompatibleUrl = this._validatedExternalApiUrlOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL);', source)
        self.assertIn("_coerceImportedSetting: function(key, value, fallback)", source)

    def test_text_model_menu_can_install_ollama_model(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Choose Ollama text model")', source)
        self.assertIn("this._connectSafe(installOllamaModel, \"activate\", () => this._chooseOllamaTextModel());", source)
        self.assertNotIn('_selectionMenuItem(_("Install Ollama model"))', source)
        self.assertNotIn('new PopupMenu.PopupIconMenuItem(_("Install Ollama text model")', source)
        self.assertIn("_chooseOllamaTextModel: function()", source)
        self.assertIn('_tryTextModelsArgs("ollama")', source)
        self.assertIn("_tryTextModelsArgs: function(backendOverride)", source)
        self.assertIn("_activateOllamaTextModelFlow: function()", source)
        self.assertIn("this._connectSafe(ollama, \"activate\", () => this._activateOllamaTextModelFlow());", source)
        self.assertIn("this._installOllamaRuntime(true);", source)
        self.assertIn("_cancelOllamaInstallWatch: function()", source)
        self.assertIn("_watchOllamaInstallThenChoose: function()", source)
        self.assertIn("_scheduleOllamaInstallWatchPoll: function(watchToken)", source)
        self.assertIn("OLLAMA_INSTALL_POLL_SECONDS", source)
        self.assertIn("OLLAMA_INSTALL_MAX_POLLS", source)
        self.assertIn("this._clearOllamaInstallWatchTimer();", source)
        self.assertIn("_ollamaModelChoiceArgs: function(models)", source)
        self.assertIn("--title=Choose Ollama text model", source)
        self.assertIn("Add another model...", source)
        self.assertIn('if (choice === "ADD")', source)
        self.assertIn('if (choice.indexOf("SELECT:") === 0)', source)
        self.assertIn("let finish = (message) =>", source)
        self.assertIn("this.ollamaModelFlowToken = null;", source)
        self.assertIn('finish(_("Ollama model selection was invalid"));', source)
        self.assertIn("let knownModel = false;", source)
        self.assertIn('this._coerceCliTextArg(candidate.name.trim(), "ollama model")', source)
        self.assertIn('this._selectTextModelBackend("ollama", model, _("Text model: ") + model, false);', source)
        self.assertIn("_promptInstallOllamaTextModel: function(flowToken)", source)
        self.assertIn("_ollamaModelPromptArgs: function()", source)
        self.assertIn("--entry-text=llama3.2:3b", source)
        self.assertIn("_installOllamaTextModel: function(model)", source)
        self.assertIn('"install-text-model", "--backend", "ollama", "--model", safeModel, "--json"', source)
        self.assertIn('typeof payload.model === "string" && payload.model.trim() !== ""', source)
        self.assertIn('let installedModel = payload && typeof payload.model === "string"', source)
        self.assertIn('String(model || "").trim()', source)
        self.assertIn('if (!this._selectTextModelBackend("ollama", installedModel, message, false))', source)
        self.assertIn('this._notify(_("Ollama model installation failed"), safeError, true)', source)
        self.assertIn('this._notify(_("Could not check Ollama"), safeError, true)', source)
        self.assertIn('this._notify(_("Could not load Ollama models"), safeError, true)', source)
        self.assertNotIn('String(payload.error), true)', source)
        self.assertIn('this._notify(_("Ollama model installed"), installedModel, false)', source)

    def test_ollama_model_actions_preserve_active_recording_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_activateOllamaTextModelFlow: function()", "\n  _ollamaModelPromptArgs:"),
            ("_chooseOllamaTextModel: function()", "\n  _promptChooseOllamaTextModel:"),
            ("_installOllamaTextModel: function(model)", "\n  _refreshHistory:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this._hasActiveRecordingState()", block)
            self.assertIn("return;", block)
        install_start = source.index("_installOllamaTextModel: function(model)")
        install_end = source.index("\n  _refreshHistory:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn('if (this._hasActiveRecordingState() && this.status !== "processing")', install_block)

    def test_text_model_requests_preflight_urls_before_flow_tokens(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, token_assignment in [
            ("_refreshTextModelMenuForBackend: function(backendOverride)", "\n  _populateTextModelMenu:", "let refreshToken = {};"),
            ("_activateOllamaTextModelFlow: function()", "\n  _ollamaModelPromptArgs:", "let flowToken = {};"),
            ("_chooseOllamaTextModel: function()", "\n  _promptChooseOllamaTextModel:", "let flowToken = {};"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this._tryTextModelsArgs", block)
            self.assertIn("if (!textModelArgs)", block)
            self.assertLess(block.index("this._tryTextModelsArgs"), block.index(token_assignment))

        refresh_start = source.index("_refreshTextModelMenuForBackend: function(backendOverride)")
        refresh_end = source.index("\n  _populateTextModelMenu:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("if (this.textModelMenuRefreshToken && !backendOverride)", refresh_block)
        self.assertLess(refresh_block.index("if (this.textModelMenuRefreshToken && !backendOverride)"), refresh_block.index("let refreshToken = {};"))
        self.assertIn("this.textModelMenuRefreshToken = null;", refresh_block)
        self.assertLess(refresh_block.index("this.textModelMenuRefreshToken = null;"), refresh_block.index("this._tryTextModelsArgs"))
        self.assertIn('this._terminateProcessesByGroup("text-model-refresh")', refresh_block)
        self.assertLess(
            refresh_block.index('this._terminateProcessesByGroup("text-model-refresh")'),
            refresh_block.index("this._tryTextModelsArgs")
        )

        ollama_guard = "if (this.ollamaModelFlowToken || this.ollamaInstallWatchToken || this.ollamaModelInstallToken || this.ollamaModelInstallRunning || this.ollamaModelCleanupFailed)"
        self.assertIn(ollama_guard, refresh_block)
        self.assertLess(refresh_block.index(ollama_guard), refresh_block.index("let refreshToken = {};"))

        watch_start = source.index("_scheduleOllamaInstallWatchPoll: function(watchToken)")
        watch_end = source.index("\n  _scheduleSetupCheck:", watch_start)
        watch_block = source[watch_start:watch_end]
        self.assertIn('this._tryTextModelsArgs("ollama")', watch_block)
        self.assertIn("this.ollamaInstallWatchToken = null;", watch_block)
        self.assertIn("let timerId = this._scheduleTrackedTimer", watch_block)
        self.assertIn("if (!timerId && this.ollamaInstallWatchToken === watchToken)", watch_block)
        self.assertIn('this._setStatus("error", _("Ollama installation watch could not be scheduled")', watch_block)
        self.assertIn("return false;", watch_block)

    def test_ollama_model_dialogs_ignore_stale_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        choose_start = source.index("_promptChooseOllamaTextModel: function(models, flowToken)")
        choose_end = source.index("\n  _promptInstallOllamaTextModel:", choose_start)
        choose_block = source[choose_start:choose_end]
        self.assertIn("this.ollamaModelFlowToken !== flowToken", choose_block)
        self.assertIn("!this._lifecycleAllowsWork()", choose_block)
        self.assertIn('this._spawnText(choiceArgs, (output, result) => {', choose_block)
        self.assertIn("result.startupFailed === true", choose_block)
        self.assertIn("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))", choose_block)
        self.assertLess(
            choose_block.index("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))"),
            choose_block.index('let choice = String(output || "").trim();')
        )
        self.assertIn('this._setStatus("error", _("Could not open Ollama model selection")', choose_block)
        self.assertIn("this._promptInstallOllamaTextModel(flowToken);", choose_block)

        install_start = source.index("_promptInstallOllamaTextModel: function(flowToken)")
        install_end = source.index("\n  _installOllamaTextModel:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn("this.ollamaModelFlowToken !== flowToken", install_block)
        self.assertIn("!this._lifecycleAllowsWork()", install_block)
        self.assertIn('this._spawnText(promptArgs, (output, result) => {', install_block)
        self.assertIn("result.startupFailed === true", install_block)
        self.assertIn("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))", install_block)
        self.assertLess(
            install_block.index("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))"),
            install_block.index('let model = String(output || "").trim();')
        )
        self.assertIn('this._setStatus("error", _("Could not open Ollama model prompt")', install_block)

    def test_ollama_dialog_cleanup_failures_do_not_report_ready(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        choose_start = source.index("_promptChooseOllamaTextModel: function(models, flowToken)")
        choose_end = source.index("\n  _promptInstallOllamaTextModel:", choose_start)
        choose_block = source[choose_start:choose_end]
        self.assertIn("let clearFlow = () =>", choose_block)
        self.assertIn("if (this._clearOllamaModelFlow(flowToken))", choose_block)
        self.assertIn('this._setStatus("error", _("Ollama operation could not be stopped")', choose_block)
        self.assertLess(
            choose_block.index('this._setStatus("error", _("Ollama operation could not be stopped")'),
            choose_block.index('this._setStatus("ready", message')
        )

        install_start = source.index("_promptInstallOllamaTextModel: function(flowToken)")
        install_end = source.index("\n  _installOllamaTextModel:", install_start)
        install_block = source[install_start:install_end]
        cancelled = install_block.index('if (model === "")')
        cleanup = install_block.index("if (!this._clearOllamaModelFlow(flowToken))", cancelled)
        ready = install_block.index('this._setStatus("ready", _("Ollama model installation cancelled")', cancelled)
        self.assertLess(cleanup, ready)
        self.assertIn('this._setStatus("error", _("Ollama operation could not be stopped")', install_block)

    def test_stale_ollama_install_callback_cannot_clear_new_command_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_installOllamaTextModel: function(model)")
        end = source.index("\n  _refreshHistory:", start)
        block = source[start:end]
        self.assertIn("let installToken = {};", block)
        self.assertIn("this.ollamaModelInstallToken = installToken;", block)
        guard = block.index("if (this.ollamaModelInstallToken !== installToken)")
        flow_guard = block.index("if (!flowToken || this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork())")
        release = block.index('this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed", true);', guard)
        reset = block.index("this.isCommandRunning = false;", flow_guard)
        self.assertLess(guard, release)
        self.assertLess(flow_guard, release)
        self.assertLess(flow_guard, reset)

    def test_ollama_model_checks_release_flow_when_processing_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, message in [
            ("_activateOllamaTextModelFlow: function()", "\n  _ollamaModelPromptArgs:", "Could not check Ollama"),
            ("_chooseOllamaTextModel: function()", "\n  _promptChooseOllamaTextModel:", "Could not load Ollama models"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this._spawnJson(textModelArgs, (payload) => {\n      try {", block)
            self.assertIn("if (this.ollamaModelFlowToken === flowToken) {\n          this.ollamaModelFlowToken = null;", block)
            self.assertIn('this._recordLifecycleError("ollama-flow", error);', block)
            self.assertIn(f'this._setStatus("error", _("{message}")', block)

    def test_failed_ollama_install_cancellation_keeps_command_busy(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_clearOllamaModelFlow: function(flowToken)")
        end = source.index("\n  _cancelOllamaFlowForRecording:", start)
        block = source[start:end]
        self.assertIn("let installToken = this.ollamaModelInstallToken;", block)
        self.assertIn("terminationSucceeded = this._terminateProcessesByGroup(\"ollama\");", block)
        self.assertIn('if (terminationSucceeded && this._hasTrackedProcessGroup("ollama"))', block)
        self.assertLess(
            block.index('if (terminationSucceeded && this._hasTrackedProcessGroup("ollama"))'),
            block.index("this.ollamaModelCleanupFailed = !terminationSucceeded;"),
        )
        self.assertIn("if (this.ollamaModelInstallToken === installToken)", block)
        self.assertIn("this.ollamaModelInstallRunning = true;", block)
        self.assertIn("this.isCommandRunning = true;", block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed", true);', block)
        self.assertIn("return terminationSucceeded;", block)

    def test_failed_ollama_flow_cleanup_blocks_parallel_flows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("this.ollamaModelCleanupFailed = false;", source)

        helper_start = source.index("_clearOllamaModelFlow: function(flowToken)")
        helper_end = source.index("\n  _ollamaCleanupStillPending:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn('this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed", true);', helper_block)
        self.assertIn("this.ollamaModelCleanupFailed = !terminationSucceeded;", helper_block)

        pending_start = source.index("_ollamaCleanupStillPending: function()")
        pending_end = source.index("\n  _cancelOllamaFlowForRecording:", pending_start)
        pending_block = source[pending_start:pending_end]
        self.assertIn('this._hasTrackedProcessGroup("ollama")', pending_block)
        self.assertIn("let cleanupReleased = this._releaseBusyStateAfterProcessCleanup(\"ollama\", \"ollamaModelCleanupFailed\");", pending_block)
        self.assertIn("!this.ollamaModelInstallToken", pending_block)
        self.assertIn("!this.ollamaModelInstallRunning", pending_block)
        self.assertIn('_("Previous Ollama operation is still stopping; try again shortly")', pending_block)

        for method, next_method in [
            ("_activateOllamaTextModelFlow: function()", "\n  _ollamaModelPromptArgs:"),
            ("_chooseOllamaTextModel: function()", "\n  _promptChooseOllamaTextModel:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("if (this._ollamaCleanupStillPending())", block)
            self.assertLess(block.index("_ollamaCleanupStillPending"), block.index("if (this.ollamaModelFlowToken)"))

        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        toggle_block = source[toggle_start:toggle_end]
        self.assertIn("this.ollamaModelCleanupFailed", toggle_block)

        editor_start = source.index("_openExternalApiEnvEditor: function(target)")
        editor_end = source.index("\n  _applyExternalApiEnvTarget:", editor_start)
        editor_block = source[editor_start:editor_end]
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", editor_block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", editor_block)
        self.assertIn("if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded)", editor_block)
        self.assertIn('this._setStatusPreservingRecording("error", _(', editor_block)

    def test_text_model_settings_invalidate_refresh_before_ollama_cleanup(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_onTextModelSettingsChanged: function()")
        end = source.index("\n  _onOpenAiFlexProcessingSettingsChanged:", start)
        block = source[start:end]
        self.assertIn("this.textModelMenuRefreshToken = null;", block)
        self.assertIn("this._clearOllamaModelFlow()", block)
        self.assertLess(
            block.index("this.textModelMenuRefreshToken = null;"),
            block.index("this._clearOllamaModelFlow()")
        )

    def test_ollama_model_flow_clears_terminal_and_install_failure_states(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        helper_start = source.index("_clearOllamaModelFlow: function(flowToken)")
        helper_end = source.index("\n  _activateOllamaTextModelFlow:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("if (flowToken && this.ollamaModelFlowToken !== flowToken)", helper_block)
        self.assertIn("let hadOllamaModelInstall = Boolean(this.ollamaModelInstallRunning);", helper_block)
        self.assertIn("let hadOllamaTerminalWorkflow = Boolean(", helper_block)
        self.assertIn("this.ollamaModelFlowToken &&", helper_block)
        self.assertIn("this.ollamaModelFlowToken = null;", helper_block)
        self.assertIn("if (hadOllamaTerminalWorkflow)", helper_block)
        self.assertNotIn("this.terminalWorkflowRunning = false;", helper_block.split("if (hadOllamaTerminalWorkflow)", 1)[1].split("if (hadOllamaModelInstall)", 1)[0])
        self.assertIn("if (hadOllamaTerminalWorkflow && terminationSucceeded)", helper_block)
        self.assertIn('this._terminateProcessesByGroup("ollama");', helper_block)
        self.assertIn("let terminationSucceeded = true;", helper_block)
        self.assertIn("return terminationSucceeded;", helper_block)
        self.assertIn("this.ollamaModelInstallRunning = false;", helper_block)
        self.assertIn("if (hadOllamaModelInstall)", helper_block)

        runtime_start = source.index("_installOllamaRuntime: function(openChooserAfterInstall)")
        runtime_end = source.index("\n  _uninstallOllamaRuntime:", runtime_start)
        runtime_block = source[runtime_start:runtime_end]
        self.assertIn("let continueOllamaFlow = openChooserAfterInstall === true && Boolean(this.ollamaModelFlowToken);", runtime_block)
        self.assertIn("let ollamaFlowToken = continueOllamaFlow ? this.ollamaModelFlowToken : null;", runtime_block)
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", runtime_block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", runtime_block)
        self.assertIn("if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded)", runtime_block)
        self.assertIn("return opened;", runtime_block)

        install_start = source.index("_installOllamaTextModel: function(model)")
        install_end = source.index("\n  _refreshHistory:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn("let flowToken = this.ollamaModelFlowToken;", install_block)
        self.assertIn('_("Another command is already running")', install_block)
        self.assertIn("this.ollamaModelFlowToken !== flowToken", install_block)
        self.assertIn("if (!flowToken || this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork())", install_block)
        self.assertIn("this.ollamaModelInstallRunning = true;", install_block)
        self.assertIn("this.ollamaModelInstallRunning = false;", install_block)
        self.assertIn("this._clearOllamaModelFlow(flowToken);", install_block)
        callback_guard = install_block.index("if (this.ollamaModelInstallToken !== installToken)")
        callback_reset = install_block.index("this.isCommandRunning = false;", callback_guard)
        self.assertLess(callback_guard, callback_reset)

        watch_start = source.index("_scheduleOllamaInstallWatchPoll: function(watchToken)")
        watch_end = source.index("\n  _scheduleSetupCheck:", watch_start)
        watch_block = source[watch_start:watch_end]
        self.assertIn("this._clearOllamaModelFlow();", watch_block)

        retry_start = source.index("_scheduleProcessCleanupRetry: function()")
        retry_end = source.index("\n  _clearProcessCleanupRetryTimer:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn(
            'if (this.terminalWorkflowRunning && !this.terminalWorkflowToken &&\n'
            '            !this._hasTrackedProcessGroup("terminal") && !this._hasTrackedProcessGroup("ollama"))',
            retry_block,
        )
        self.assertIn("this.terminalWorkflowRunning = false;", retry_block)

    def test_text_model_catalogs_are_bounded_before_menu_or_zenity_creation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_MODEL_MENU_ENTRIES = 128;", source)

        menu_start = source.index("_populateTextModelMenu: function(models, message, provider)")
        menu_end = source.index("\n  _canMutateMenu:", menu_start)
        menu_block = source[menu_start:menu_end]
        self.assertIn("let modelListWasTruncated = models.length > MAX_MODEL_MENU_ENTRIES;", menu_block)
        self.assertIn("models = models.slice(0, MAX_MODEL_MENU_ENTRIES);", menu_block)
        self.assertIn("selectedOllamaModel", menu_block)
        self.assertIn('description: _("selected")', menu_block)
        self.assertIn('_("Model list truncated for safety")', menu_block)

        choice_start = source.index("_ollamaModelChoiceArgs: function(models)")
        choice_end = source.index("\n  _chooseOllamaTextModel:", choice_start)
        choice_block = source[choice_start:choice_end]
        self.assertIn("let maxModelChoices = Math.min(MAX_MODEL_MENU_ENTRIES", choice_block)
        self.assertIn("MAX_CLI_ARG_COUNT", choice_block)
        self.assertIn("MAX_CLI_COMMAND_BYTES - MAX_CLI_ARG_BYTES", choice_block)
        self.assertIn("let listWasTruncated = false;", choice_block)
        self.assertIn('args[3] = "--text="', choice_block)
        self.assertIn('_("model list truncated for safety")', choice_block)

    def test_alarm_fanout_is_bounded_before_menu_or_notification_creation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_ALARM_MENU_ENTRIES = 128;", source)
        self.assertIn("const MAX_ALARM_NOTIFICATIONS = 32;", source)

        menu_start = source.index("_populateAlarmMenu: function(alarms, summary, message)")
        menu_end = source.index("\n  _addAlarmMenuEntry:", menu_start)
        menu_block = source[menu_start:menu_end]
        self.assertIn("let alarmsWereTruncated = alarms.length > MAX_ALARM_MENU_ENTRIES;", menu_block)
        self.assertIn("alarms = alarms.slice(0, MAX_ALARM_MENU_ENTRIES);", menu_block)
        self.assertIn('_("Alarm list truncated for safety")', menu_block)

        check_start = source.index("_checkAlarms: function(manual)")
        check_end = source.index("\n  _refreshInputSourceMenu:", check_start)
        check_block = source[check_start:check_end]
        self.assertIn("let dueCount = due.length;", check_block)
        self.assertIn("let notifications = due.filter((alarm) => alarm.notify === true);", check_block)
        self.assertIn("let notificationsWereTruncated = notifications.length > MAX_ALARM_NOTIFICATIONS;", check_block)
        self.assertIn("notifications = notifications.slice(0, MAX_ALARM_NOTIFICATIONS);", check_block)
        self.assertLess(
            check_block.index("let notifications = due.filter((alarm) => alarm.notify === true);"),
            check_block.index("notifications = notifications.slice(0, MAX_ALARM_NOTIFICATIONS);"),
        )
        self.assertIn('_("some notifications suppressed for safety")', check_block)

    def test_input_source_menu_fanout_is_bounded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_INPUT_SOURCE_MENU_ENTRIES = 128;", source)

        start = source.index("_populateInputSourceMenu: function(sources, message)")
        end = source.index("\n  _selectInputSource:", start)
        block = source[start:end]
        self.assertIn("let sourcesWereTruncated = sources.length > MAX_INPUT_SOURCE_MENU_ENTRIES;", block)
        self.assertIn("sources = sources.slice(0, MAX_INPUT_SOURCE_MENU_ENTRIES);", block)
        self.assertIn('_("Input source list truncated for safety")', block)

    def test_voice_model_menu_fanout_is_bounded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("const MAX_VOICE_MODEL_MENU_ENTRIES = 128;", source)

        start = source.index("_populateModelMenu: function(models, message)")
        end = source.index("\n  _populateExternalApiVoiceMenu:", start)
        block = source[start:end]
        self.assertIn("let voiceModelsWereTruncated = models.length > MAX_VOICE_MODEL_MENU_ENTRIES;", block)
        self.assertIn("models = models.slice(0, MAX_VOICE_MODEL_MENU_ENTRIES);", block)
        self.assertIn('_("Voice model list truncated for safety")', block)

    def test_settings_derived_model_labels_are_bounded_before_cinnamon_display(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_activeVoiceModelSummary: function()", "\n  _whisperModelSupportsLanguage:"),
            ("_populateExternalApiVoiceMenu: function(parentMenu)", "\n  _modelPathFromPayload:"),
            ("_voiceBackendLabel: function()", "\n  _textModelLabel:"),
            ("_textModelLabel: function()", "\n  _panelStyleClassForStatus:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this._shortMenuText", block)

    def test_transcript_list_command_respects_existing_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_loadAllTranscriptsDocument: function()")
        end = source.index("\n  _showTranscriptsWindow:", start)
        block = source[start:end]
        self.assertIn("if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())", block)
        self.assertIn("return;", block)
        self.assertLess(block.index("if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())"), block.index("this.isCommandRunning = true;"))

    def test_transcript_window_releases_token_when_program_probe_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_showTranscriptsWindow: function(content, count, truncated)")
        end = source.index("\n  _exportAllTranscripts:", start)
        block = source[start:end]
        self.assertIn("if (this.transcriptWindowToken)", block)
        self.assertIn("let windowToken = {};", block)
        self.assertIn("let zenity;", block)
        self.assertIn('zenity = this._findTrustedProgramInPath("zenity");', block)
        self.assertIn("releaseWindow();", block)
        self.assertIn('this._recordLifecycleError("transcript-window", error);', block)
        self.assertIn('_("Could not prepare transcript list window")', block)
        self.assertIn('this._setStatus("done", message, this.lastTranscript);', block)
        self.assertIn("let setTranscriptWindowError = (message) =>", block)
        self.assertIn('this._setStatusPreservingRecording("error", message, this.lastTranscript);', block)
        self.assertIn('this._setStatus("error", message, this.lastTranscript);', block)

    def test_transcript_window_blocks_other_local_workflows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        workflow_start = source.index("_hasLocalProcessingWorkflow: function(includePendingCleanup)")
        workflow_end = source.index("\n  _setActiveLanguage:", workflow_start)
        workflow_block = source[workflow_start:workflow_end]
        self.assertIn("this.transcriptWindowToken ||", workflow_block)

        window_start = source.index("_showTranscriptsWindow: function(content, count, truncated)")
        window_end = source.index("\n  _exportAllTranscripts:", window_start)
        window_block = source[window_start:window_end]
        self.assertIn('resourceGroup: "maintenance"', window_block)

        invalidate_start = source.index("_invalidateBackgroundCallbacksForRecording: function()")
        invalidate_end = source.index("\n  _runDoctor:", invalidate_start)
        invalidate_block = source[invalidate_start:invalidate_end]
        self.assertIn("this.transcriptWindowToken = null;", invalidate_block)
        self.assertLess(
            invalidate_block.index("this.transcriptWindowToken = null;"),
            invalidate_block.index('this._terminateProcessesByGroup("maintenance")')
        )

    def test_busy_backend_actions_prepare_arguments_before_setting_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, builder_name, message in [
            ("_loadAllTranscriptsDocument: function()", "\n  _showTranscriptsWindow:", "historyDocumentArgs", "_allHistoryArgs", "Could not prepare transcript list"),
            ("_exportAllTranscripts: function()", "\n  _safePayloadCount:", "exportArgs", "_transcriptsExportArgs", "Could not prepare transcript export"),
            ("_previewCleanup: function()", "\n  _cleanupOldFiles:", "cleanupPreviewArgs", "_cleanupPreviewArgs", "Could not prepare cleanup preview"),
            ("_cleanupOldFiles: function()", "\n  _settingsSnapshot:", "cleanupArgs", "_cleanupArgs", "Could not prepare cleanup"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {args_name};", block)
            self.assertIn(f"{args_name} = this.{builder_name}();", block)
            self.assertIn(f"this._spawnJson({args_name},", block)
            self.assertIn(f'_("{message}")', block)
            self.assertLess(block.index(f"{args_name} = this.{builder_name}();"), block.index("this.isCommandRunning = true;"))

        maintenance_blocks = []
        for method, next_method in (
            ("_loadAllTranscriptsDocument: function()", "\n  _showTranscriptsWindow:"),
            ("_exportAllTranscripts: function()", "\n  _safePayloadCount:"),
            ("_previewCleanup: function()", "\n  _cleanupOldFiles:"),
            ("_cleanupOldFiles: function()", "\n  _settingsSnapshot:"),
        ):
            start = source.index(method)
            end = source.index(next_method, start)
            maintenance_blocks.append(source[start:end])
        for block in maintenance_blocks:
            self.assertIn("let cleanupToken = {};", block)
            self.assertIn("this._cleanupCommandToken = cleanupToken;", block)
            self.assertIn("this._cleanupCommandToken !== cleanupToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)
            self.assertIn('resourceGroup: "maintenance"', block)

    def test_maintenance_callbacks_fail_closed_on_processing_exceptions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, message in [
            ("_loadAllTranscriptsDocument: function()", "\n  _showTranscriptsWindow:", "historyDocumentArgs", "Could not complete transcript list"),
            ("_exportAllTranscripts: function()", "\n  _safePayloadCount:", "exportArgs", "Could not complete transcript export"),
            ("_previewCleanup: function()", "\n  _cleanupOldFiles:", "cleanupPreviewArgs", "Could not complete cleanup preview"),
            ("_cleanupOldFiles: function()", "\n  _settingsSnapshot:", "cleanupArgs", "Could not complete cleanup"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnJson({args_name}, (payload) => {{", block)
            self.assertIn("try {\n        this._cleanupCommandToken = null;", block)
            self.assertIn("if (this._cleanupCommandToken === cleanupToken) {", block)
            self.assertIn('this._recordLifecycleError("maintenance-command", error);', block)
            self.assertIn(f'_("{message}")', block)

    def test_doctor_payload_processing_fails_closed_on_unexpected_exceptions(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_runDoctor: function(startupCheck)")
        end = source.index("\n  _applyDoctorPayload:", start)
        block = source[start:end]
        self.assertIn("} catch (err) {", block)
        self.assertIn('let message = _("Doctor failed: ") + safeError;', block)
        self.assertIn("this._setStatus(startupCheck ? \"setup\" : \"error\", message", block)
        self.assertIn("} finally {", block)
        self.assertIn("this._doctorCommandRunning = false;", block)

        summary_start = source.index("_setDoctorSummary: function(message)")
        summary_end = source.index("\n  _doctorSummary:", summary_start)
        summary_block = source[summary_start:summary_end]
        self.assertIn("try {", summary_block)
        self.assertIn('this._recordLifecycleError("doctor-summary", error);', summary_block)

    def test_recording_payload_callbacks_use_fail_closed_handler(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        helper_start = source.index("_applyPayloadSafely: function(payload, statusRefreshToken)")
        helper_end = source.index("\n  _applyPayload: function(payload, statusRefreshToken)", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("try {", helper_block)
        self.assertIn("this._applyPayload(payload, statusRefreshToken);", helper_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Backend response handling failed: ") + safeError', helper_block)
        self.assertIn('if (!this.isCommandRunning && (this.status === "recording" || this.status === "processing"))', helper_block)
        self.assertIn("this._scheduleStatusPoll();", helper_block)
        for marker in [
            "this._applyPayloadSafely(payload);",
            "this._applyPayloadSafely(nextPayload, undefined, true);",
        ]:
            self.assertIn(marker, source)

    def test_text_backend_choices_invalidate_stale_ollama_flows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_selectTextModelBackend: function(backend, model, message, preserveRecording)", "\n  _activateOllamaTextModelFlow:"),
            ("_openExternalApiEnvEditor: function(target)", "\n  _applyExternalApiEnvTarget:"),
            ("_applyExternalApiEnvTarget: function(target)", "\n  _selectTextModelBackend:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            if method in [
                "_selectTextModelBackend: function(backend, model, message, preserveRecording)",
                "_openExternalApiEnvEditor: function(target)",
                "_applyExternalApiEnvTarget: function(target)",
            ]:
                self.assertIn("this._cancelOllamaInstallWatch() !== false;", block)
            else:
                self.assertIn("this._cancelOllamaInstallWatch();", block)
            self.assertIn("this._clearOllamaModelFlow", block)

    def test_text_backend_changes_abort_when_ollama_cleanup_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_selectTextModelBackend: function(backend, model, message, preserveRecording)")
        end = source.index("\n  _activateOllamaTextModelFlow:", start)
        block = source[start:end]
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", block)
        self.assertIn("if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded)", block)
        self.assertIn("this._rollbackSettingsBatch(settingsWrites);", block)
        self.assertIn("this.postProcessBackend = previousBackend;", block)
        self.assertIn("this.ollamaModel = previousOllamaModel;", block)
        self.assertIn("this.openaiCompatibleTextModel = previousExternalTextModel;", block)
        self.assertIn('setStatus("error", _("Ollama operation could not be stopped")', block)
        self.assertIn("return false;", block)

        settings_start = source.index("_onTextModelSettingsChanged: function()")
        settings_end = source.index("\n  _onOpenAiFlexProcessingSettingsChanged:", settings_start)
        settings_block = source[settings_start:settings_end]
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", settings_block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", settings_block)
        self.assertIn("if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded)", settings_block)

        watch_start = source.index("_cancelOllamaInstallWatch: function()")
        watch_end = source.index("\n  _watchOllamaInstallThenChoose:", watch_start)
        watch_block = source[watch_start:watch_end]
        self.assertIn("return this._clearOllamaInstallWatchTimer();", watch_block)

    def test_text_backend_persistence_validates_model_names(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_selectTextModelBackend: function(backend, model, message, preserveRecording)")
        end = source.index("\n  _activateOllamaTextModelFlow:", start)
        block = source[start:end]
        self.assertIn("let safeModel;", block)
        self.assertIn('safeModel = this._coerceCliTextArg(model === undefined || model === null ? "" : model, "text model");', block)
        self.assertIn("let setStatus = preserveRecording === false", block)
        self.assertIn("return false;", block)
        self.assertIn("this.ollamaModel = safeModel;", block)
        self.assertIn("this.openaiCompatibleTextModel = safeModel;", block)
        self.assertIn("return true;", block)

        install_start = source.index("_installOllamaTextModel: function(model)")
        install_end = source.index("\n  _refreshHistory:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn('if (!this._selectTextModelBackend("ollama", installedModel, message, false))', install_block)
        self.assertIn('this._notify(_("Ollama model installed"), installedModel, false);', install_block)

    def test_custom_limit_dialogs_ignore_stale_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method in [
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("let promptToken = {};", block)
            self.assertIn("this.customLimitPromptToken = promptToken;", block)
            self.assertIn("this.customLimitPromptToken !== promptToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)

    def test_auto_paste_dialog_ignores_stale_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_configureAutoPaste: function()")
        end = source.index("\n  _setAutoPasteTitles:", start)
        block = source[start:end]
        self.assertIn("let promptToken = {};", block)
        self.assertIn("this.autoPastePromptToken = promptToken;", block)
        self.assertIn("this.autoPastePromptToken !== promptToken", block)
        self.assertIn("!this._lifecycleAllowsWork()", block)

    def test_text_prompt_startup_failures_do_not_change_settings(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, message in [
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:", "recordingPromptArgs", "Could not open custom duration prompt"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:", "transcriptPromptArgs", "Could not open custom transcript limit prompt"),
            ("_configureAutoPaste: function()", "\n  _setAutoPasteTitles:", "promptArgs", "Could not open Auto-Submit prompt"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnText({args_name}, (output, result) => {{", block)
            self.assertIn("if (result && result.startupFailed === true)", block)
            self.assertIn("if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))", block)
            self.assertIn(f'this._setStatusPreservingRecording("error", _("{message}")', block)

    def test_text_prompt_failures_do_not_apply_output(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, apply_marker in [
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:", "this._parseCustomRecordingLimit(output)"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:", "this._parseCustomTranscriptLimit(output)"),
            ("_configureAutoPaste: function()", "\n  _setAutoPasteTitles:", "this._setAutoPasteTitles(this._autoPasteTitleValues(output))"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            guard = "if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge))"
            self.assertLess(block.index(guard), block.index(apply_marker))

    def test_process_group_cancellation_suppresses_stale_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        bounded_start = source.index("_runBoundedSubprocess: function(args, env, options, callback)")
        bounded_end = source.index("\n  _spawnJsonWithBackendEnvironment:", bounded_start)
        bounded_block = source[bounded_start:bounded_end]
        self.assertIn("let finish = (result, terminate, suppressCallback, timeoutAlreadyRemoved)", bounded_block)
        self.assertIn('if (hasInput && typeof options.inputText !== "string")', bounded_block)
        self.assertIn('typeof options.maxStdoutBytes === "number" && isFinite(options.maxStdoutBytes)', bounded_block)
        self.assertIn('typeof options.maxStderrBytes === "number" && isFinite(options.maxStderrBytes)', bounded_block)
        self.assertIn('typeof options.timeoutMs === "number" && isFinite(options.timeoutMs)', bounded_block)
        self.assertIn('typeof options.minimumTimeoutMs === "number" && isFinite(options.minimumTimeoutMs)', bounded_block)
        self.assertIn("suppressCallback || this.appletRemoved", bounded_block)
        self.assertIn("let processEntry = this._resourceRegistry && this._resourceRegistry.processes", bounded_block)
        self.assertIn("let cancelCallback = (notifyCallback) => finish(", bounded_block)
        self.assertIn("processEntry.cancel = cancelCallback;", bounded_block)
        self.assertIn("processEntry.cancel !== cancelCallback", bounded_block)
        self.assertIn("notifyCallback === true ? false : true", bounded_block)
        self.assertIn("this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, terminationSucceeded);", bounded_block)
        self.assertIn("this._trackOrphanedCancellable(cancellableToken, cancellationSucceeded);", bounded_block)
        self.assertIn('"process-cancel-registration"', bounded_block)
        stdin_start = bounded_block.rindex("if (hasInput) {")
        stdin_block = bounded_block[stdin_start:]
        self.assertIn("try {", stdin_block)
        self.assertIn("let stdin = process.get_stdin_pipe();", stdin_block)
        self.assertIn("inputPending = false;", stdin_block)
        self.assertIn("finishWhenReady();", stdin_block)
        self.assertIn("finish({ error: error }, true);", stdin_block)
        self.assertIn("if (done) {\n              return;\n            }\n            try {", stdin_block)

        group_start = source.index("_terminateProcessesByGroup: function(group, notifyCallback)")
        group_end = source.index("\n  _cancelAllCancellables:", group_start)
        group_block = source[group_start:group_end]
        self.assertIn("typeof entry.cancel === \"function\"", group_block)
        self.assertIn("entry.cancel(Boolean(notifyCallback));", group_block)
        self.assertIn('this._recordLifecycleError("process-cancel", error);', group_block)
        self.assertIn("let cleanupSucceeded = false;", group_block)
        self.assertIn("if (selected && cleanupSucceeded) {", group_block)

        all_start = source.index("_terminateAllProcesses: function()")
        all_end = source.index("\n  _terminateProcessesByGroup:", all_start)
        all_block = source[all_start:all_end]
        self.assertIn('this._recordLifecycleError("process-cancel", error);', all_block)
        self.assertIn("let cleanupSucceeded = false;", all_block)
        self.assertIn("if (cleanupSucceeded) {", all_block)
        self.assertIn("if (!this._unregisterProcess(token))", all_block)
        self.assertIn("if (!this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, true))", all_block)
        self.assertIn('if (result === false) {\n                throw new Error("Process cancellation failed");', all_block)

    def test_bounded_subprocess_retries_registry_cleanup_after_callback(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_runBoundedSubprocess: function(args, env, options, callback)")
        end = source.index("\n  _spawnJsonWithBackendEnvironment:", start)
        block = source[start:end]
        self.assertIn("let cleanupComplete = false;", block)
        self.assertIn("let callbackDelivered = false;", block)
        self.assertIn("let setupFailed = false;", block)
        self.assertIn("let inputPending = hasInput;", block)
        self.assertIn("let timeoutSourceAlreadyRemoved = false;", block)
        self.assertIn('callback("", "", { error: "Subprocess cleanup failed" });', block)
        self.assertIn("!suppressCallback && !this.appletRemoved", block)
        self.assertIn("!ended.stdout || !ended.stderr || inputPending", block)
        self.assertIn("if (setupFailed || done) {\n      return null;", block)
        self.assertIn("let cleanupResources = (timeoutCleanupSucceeded) =>", block)
        self.assertIn("if (done) {\n        return cleanupResources();", block)
        self.assertIn("if (!callbackDelivered)", block)
        self.assertIn("if (timeoutCleanupSucceeded === undefined) {\n        timeoutCleanupSucceeded = this._clearTrackedTimer(timeoutKey, undefined, timeoutSourceAlreadyRemoved) !== false;", block)
        self.assertIn("let timerRetrySucceeded = this._retryOrphanedTimers();", block)
        self.assertIn("timerRetrySucceeded &&", block)
        self.assertIn("let timeoutCleanupSucceeded = this._clearTrackedTimer(timeoutKey, undefined, timeoutSourceAlreadyRemoved) !== false;", block)
        self.assertIn("let finish = (result, terminate, suppressCallback, timeoutAlreadyRemoved) =>", block)
        self.assertIn("timeoutAlreadyRemoved === true", block)
        self.assertIn("finish({ timedOut: true }, true, false, true);", block)
        self.assertIn("if (!done && !setupFailed && timeoutMs > 0 && !this._scheduleTrackedTimer(timeoutKey", block)
        self.assertIn("let cancellableCleanupSucceeded = this._unregisterCancellable(cancellableToken);", block)
        self.assertIn("let cancellableOrphanCleanupSucceeded = true;", block)
        self.assertIn("cancellableOrphanCleanupSucceeded = this._untrackOrphanedCancellable(cancellableToken);", block)
        self.assertIn("if (!cancellableCleanupSucceeded) {\n        this._trackOrphanedCancellable(cancellableToken, true);", block)
        self.assertIn("let processCleanupSucceeded = this._unregisterProcess(processToken);", block)
        self.assertIn("let processOrphanCleanupSucceeded = true;", block)
        self.assertIn("processOrphanCleanupSucceeded = this._untrackOrphanedProcess(process);", block)
        self.assertIn("timeoutCleanupSucceeded && cancellableCleanupSucceeded && cancellableOrphanCleanupSucceeded &&", block)
        self.assertIn("processOrphanCleanupSucceeded", block)
        self.assertIn("let cleanupSucceeded = cleanupResources(timeoutCleanupSucceeded);", block)
        self.assertIn('let callbackResult = cleanupSucceeded ? (result || {}) : { error: "Subprocess cleanup failed" };', block)
        self.assertIn("callback(stdoutText, stderrText, callbackResult);", block)

    def test_subprocess_output_reassembles_bytes_before_utf8_decode(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_runBoundedSubprocess: function(args, env, options, callback)")
        end = source.index("\n  _spawnJsonWithBackendEnvironment:", start)
        block = source[start:end]
        self.assertIn("function _decodeSubprocessOutputChunks(chunks)", source)
        self.assertIn("let contents = new Uint8Array(totalBytes);", source)
        self.assertIn("stdoutText = _decodeSubprocessOutputChunks(stdoutParts);", block)
        self.assertIn("stderrText = _decodeSubprocessOutputChunks(stderrParts);", block)
        self.assertIn("let chunk = new Uint8Array(data || []);", block)
        self.assertNotIn('ByteArray.toString(data || "")', block)
        self.assertIn('callbackResult = { error: "Subprocess output is not valid UTF-8" };', block)

    def test_teardown_uses_safe_process_and_cancellable_unregistration(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_cancelAllCancellables: function()")
        end = source.index("\n  _trackTimer:", start)
        block = source[start:end]
        self.assertIn("if (!this._unregisterCancellable(token))", block)
        self.assertNotIn("delete cancellables[token];", block)
        self.assertIn("let cleanupSucceeded = false;", block)
        self.assertIn('throw new Error("Cancellable cancellation is unavailable");', block)
        self.assertIn('throw new Error("Cancellable cancellation failed");', block)

    def test_process_termination_uses_force_exit_without_invalid_exit_probe(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_terminateProcess: function(process)")
        end = source.index("\n  _terminateAllProcesses:", start)
        block = source[start:end]
        self.assertIn('throw new Error("Process termination API is unavailable");', block)
        self.assertIn("process.force_exit();", block)
        self.assertNotIn("get_if_exited", block)
        self.assertNotIn("result === false", block)
        self.assertIn('let hasIdentifier = typeof process.get_identifier === "function";', block)
        self.assertIn('let processIdentifier = hasIdentifier ? String(process.get_identifier() || "").trim() : "";', block)
        self.assertIn('if (hasIdentifier && !processIdentifier && !processGroupIdentity) {\n        return true;', block)
        self.assertIn(
            'if (!processGroupIdentity && hasIdentifier) {\n'
            '        processGroupIdentity = this._readProcessGroupIdentity(process);\n'
            '        if (!processGroupIdentity) {\n'
            '          return false;\n'
            '        }\n'
            '      }',
            block,
        )
        self.assertIn('let groupState = this._processGroupState(processGroupIdentity);', block)
        self.assertIn('if (groupState === "stopped") {\n            return true;', block)
        self.assertIn("return true;", block)
        self.assertIn("return false;", block)

    def test_process_termination_kills_live_private_session_after_leader_exit(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_terminateProcess: function(process)")
        end = source.index("\n  _terminateAllProcesses:", start)
        block = source[start:end]

        leader_exit_start = block.index("if (!currentProcessGroupIdentity && processIdentifier)")
        leader_exit_end = block.index("\n        }", leader_exit_start) + len("\n        }")
        leader_exit_block = block[leader_exit_start:leader_exit_end]
        self.assertIn('let groupState = this._processGroupState(processGroupIdentity);', leader_exit_block)
        self.assertIn('if (groupState === "stopped") {', leader_exit_block)
        self.assertIn('if (groupState === "live" && this._killProcessGroup(process, processGroupIdentity)) {', leader_exit_block)

    def test_subprocess_tree_cleanup_uses_identity_checked_private_session(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        termination_start = source.index("_terminateProcess: function(process)")
        termination_end = source.index("\n  _terminateAllProcesses:", termination_start)
        termination_block = source[termination_start:termination_end]
        self.assertIn("this._findTrackedProcessGroupIdentity(process)", termination_block)
        self.assertIn("this._readProcessGroupIdentity(process)", termination_block)
        self.assertIn("let currentProcessGroupIdentity = this._readProcessGroupIdentity(process);", termination_block)
        self.assertIn("currentProcessGroupIdentity.startTime !== processGroupIdentity.startTime", termination_block)
        self.assertIn("return false;", termination_block)
        self.assertIn("this._killProcessGroup(process, processGroupIdentity)", termination_block)
        self.assertIn("process.force_exit();", termination_block)
        self.assertIn(
            "if (this._killProcessGroup(process, processGroupIdentity)) {\n"
            "          return true;\n"
            "        }\n"
            "        return false;\n"
            "      }\n"
            "      if (typeof process.force_exit !== \"function\")",
            termination_block,
        )

        wrap_start = source.index("_wrapSubprocessArgs: function(args)")
        wrap_end = source.index("\n  _coerceCliTextArg:", wrap_start)
        wrap_block = source[wrap_start:wrap_end]
        self.assertIn('this._findTrustedProgramInPath("setsid")', wrap_block)
        self.assertIn('throw new Error("setsid is unavailable; refusing ungrouped subprocess");', wrap_block)
        self.assertIn('return [setsid, "--"].concat(args);', wrap_block)

        identity_start = source.index("_readProcessGroupIdentity: function(process)")
        identity_end = source.index("\n  _killProcessGroup:", identity_start)
        identity_block = source[identity_start:identity_end]
        self.assertIn('"/proc/" + pid + "/stat"', identity_block)
        self.assertIn('stat.lastIndexOf(") ")', identity_block)
        self.assertIn("fields[2] !== pid || fields[3] !== pid", identity_block)
        self.assertIn("fields[19]", identity_block)
        self.assertIn("currentProcessGroupIdentity.startTime !== processGroupIdentity.startTime", source)
        self.assertIn("_processGroupState: function(identity)", source)
        self.assertIn('return sessionMemberFound ? "live" : "stopped";', source)
        self.assertIn("_processSessionGroupIds: function(identity)", source)
        self.assertIn("let sessionGroupIds = this._processSessionGroupIds(identity);", source)
        self.assertIn("for (let processGroupId of sessionGroupIds)", source)
        self.assertIn("let groupState = this._processGroupState(identity);", source)
        self.assertIn('"-" + processGroupId', source)

    def test_process_group_cleanup_handles_disappearing_proc_entries(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        retry_start = source.index("_retryOrphanedProcesses: function(group)")
        retry_end = source.index("\n  _processCleanupStillPending:", retry_start)
        retry_block = source[retry_start:retry_end]
        self.assertIn("if (terminationSucceeded && entry.processGroupIdentity)", retry_block)
        self.assertIn('if (groupState === "live")', retry_block)
        self.assertIn('} else if (groupState !== "stopped")', retry_block)
        self.assertIn("entry.terminationSucceeded = false;", retry_block)

        state_start = source.index("_processGroupState: function(identity)")
        state_end = source.index("\n  _processSessionGroupIds:", state_start)
        state_block = source[state_start:state_end]
        self.assertIn("let leaderStillExists = false;", state_block)
        self.assertIn("leaderStillExists = GLib.file_test(procPath, GLib.FileTest.EXISTS);", state_block)
        self.assertIn("leaderPathExists = false;", state_block)
        self.assertIn('leaderFields[0] !== "Z" && leaderFields[0] !== "X" && leaderFields[0] !== "x"', state_block)
        self.assertLess(state_block.index('leaderFields[0] !== "Z"'), state_block.index('return "live";'))

        session_start = source.index("_processSessionGroupIds: function(identity)")
        session_end = source.index("\n  _killProcessGroup:", session_start)
        session_block = source[session_start:session_end]
        self.assertIn('let memberStatPath = "/proc/" + memberPid + "/stat";', session_block)
        self.assertIn("let memberStillExists = false;", session_block)
        self.assertIn("memberStillExists = GLib.file_test(memberStatPath, GLib.FileTest.EXISTS);", session_block)
        self.assertIn("if (!memberStillExists) {\n              continue;", session_block)

    def test_process_group_cleanup_skips_unrelated_zero_process_groups(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_processSessionGroupIds: function(identity)")
        end = source.index("\n  _killProcessGroup:", start)
        block = source[start:end]

        fields_guard = block.index("if (fields.length <= 19) {")
        session_filter = block.index('if (fields[3] !== groupPid) {', fields_guard)
        pgrp_guard = block.index('if (!/^[1-9][0-9]*$/.test(fields[2])) {', session_filter)
        self.assertLess(fields_guard, session_filter)
        self.assertLess(session_filter, pgrp_guard)

    def test_process_group_kill_requires_post_kill_stopped_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_killProcessGroup: function(process, identity)")
        end = source.index("\n  _terminateAllProcesses:", start)
        block = source[start:end]
        self.assertIn('let finalGroupState = this._processGroupState(identity);', block)
        self.assertIn('return finalGroupState === "stopped";', block)
        self.assertLess(block.index('let finalGroupState ='), block.index('return finalGroupState ==='))

    def test_keyboard_group_cancel_notifies_active_insert_cleanup(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        remember_start = source.index("_rememberFocusedWindow: function(preserveOnFailure)")
        remember_end = source.index("\n  _closeMenuForKeyboardInsert:", remember_start)
        remember_block = source[remember_start:remember_end]
        keyboard_start = source.index("_spawnKeyboardProcess: function(args, completionCallback)")
        keyboard_end = source.index("\n  _spawnKeyboardArgs:", keyboard_start)
        keyboard_block = source[keyboard_start:keyboard_end]

        self.assertIn('for (let group of ["keyboard", "x11", "clipboard"])', remember_block)
        self.assertIn('this._terminateProcessesByGroup(group, true) === false', remember_block)
        self.assertIn("let completeOnce = (result) =>", keyboard_block)
        self.assertIn("if (!handle) {\n        completeOnce(false);", keyboard_block)
        self.assertIn("result.cancelled", keyboard_block)

    def test_target_capture_fails_closed_when_insert_cleanup_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        remember_start = source.index("_rememberFocusedWindow: function(preserveOnFailure)")
        remember_end = source.index("\n  _closeMenuForKeyboardInsert:", remember_start)
        remember_block = source[remember_start:remember_end]
        self.assertIn("let processCleanupSucceeded = true;", remember_block)
        self.assertIn("if (!processCleanupSucceeded) {", remember_block)
        self.assertIn("this.textInsertCancellationFailed = true;", remember_block)
        self.assertIn("this.targetWindow = null;", remember_block)
        self.assertIn("return false;", remember_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Previous text insertion could not be stopped")', remember_block)

        hotkey_start = source.index('this._registerHotkey(HOTKEY_ID, this.toggleKeybinding, () => {')
        hotkey_end = source.index('this._registerHotkey(PRIMARY_HOTKEY_ID', hotkey_start)
        hotkey_block = source[hotkey_start:hotkey_end]
        self.assertIn('!this._hasActiveRecordingState() && !this.isCommandRunning && !this._rememberFocusedWindow()', hotkey_block)
        self.assertIn("return;", hotkey_block)

        language_start = source.index("_startWithLanguage: function(language, preserveTargetOnFailure)")
        language_end = source.index("\n  _populateLanguageMenu:", language_start)
        language_block = source[language_start:language_end]
        self.assertIn("if (!this._rememberFocusedWindow(Boolean(preserveTargetOnFailure)))", language_block)
        self.assertIn("return;", language_block)

    def test_target_window_generation_invalidates_stale_insert_resources(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        remember_start = source.index("_rememberFocusedWindow: function(preserveOnFailure)")
        remember_end = source.index("\n  _closeMenuForKeyboardInsert:", remember_start)
        remember_block = source[remember_start:remember_end]
        self.assertIn('for (let group of ["keyboard", "x11", "clipboard"])', remember_block)
        self.assertIn('this._terminateProcessesByGroup(group, true) === false', remember_block)
        self.assertIn('if (!preserveOnFailure) {\n      this._clearTargetWindowXid();\n    }', remember_block)

        snapshot_start = source.index("_targetXWindowSnapshot: function()")
        snapshot_end = source.index("\n  _targetXWindowMatchesSnapshot:", snapshot_start)
        snapshot_block = source[snapshot_start:snapshot_end]
        self.assertIn("targetWindowGeneration: Number(this.targetWindowGeneration || 0)", snapshot_block)

        match_start = source.index("_targetXWindowMatchesSnapshot: function(snapshot, completionCallback)")
        match_end = source.index("\n  _windowProbeValue:", match_start)
        match_block = source[match_start:match_end]
        self.assertIn("let expectedGeneration = snapshot && snapshot.targetWindowGeneration !== undefined", match_block)
        self.assertIn("let generationMatches = () =>", match_block)
        self.assertIn("if (!generationMatches())", match_block)
        self.assertIn('let expectedClass = String(snapshot.windowClass || "").trim().toLowerCase();', match_block)
        self.assertIn('let expectedTitle = String(snapshot.windowTitle || "").trim().toLowerCase();', match_block)
        self.assertIn('if (expectedClass === "" && expectedTitle === "")', match_block)
        self.assertIn("complete(false);\n      return false;", match_block)

        title_start = source.index("_targetXWindowMatchesSnapshotTitle: function(snapshot, xid, completionCallback, deadlineMs)")
        title_end = source.index("\n  _windowProbeValue:", title_start)
        title_block = source[title_start:title_end]
        self.assertIn('this._xdotoolOutput(["getwindowname", xid]', title_block)
        self.assertIn("this._xWindowLooksLikeSpeedOfCinnamon(activeTitle, snapshot.windowClass)", title_block)
        self.assertIn('if (expectedTitle === "")', title_block)

        probe_start = source.index("_windowProbeValue: function(window, methodName)")
        probe_end = source.index("\n  _windowLooksLikeSpeedOfCinnamon:", probe_start)
        probe_block = source[probe_start:probe_end]
        self.assertLess(probe_block.index("try {"), probe_block.index("window[methodName]"))

        activate_start = source.index("_activateTargetXWindow: function(completionCallback)")
        activate_end = source.index("\n  _targetXWindowSnapshot:", activate_start)
        activate_block = source[activate_start:activate_end]
        self.assertIn("let targetGeneration = Number(this.targetWindowGeneration || 0);", activate_block)
        self.assertIn("targetGeneration === Number(this.targetWindowGeneration || 0) && output !== null", activate_block)

        insert_start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        insert_end = source.index("\n  _restartRelistenRecording:", insert_start)
        insert_block = source[insert_start:insert_end]
        self.assertIn("let insertTargetGeneration = Number(this.targetWindowGeneration || 0);", insert_block)
        self.assertIn("let isCurrentInsert = () =>", insert_block)
        self.assertIn("insertTargetGeneration === Number(this.targetWindowGeneration || 0)", insert_block)
        self.assertIn("complete(false);", insert_block)

    def test_alarm_actions_ignore_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method in [
            ("_setAlarmEnabled: function(id, enabled)", "\n  _removeAlarm:"),
            ("_removeAlarm: function(id)", "\n  _checkAlarms:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(
                "if (this.alarmActionToken || this.alarmCheckToken || this.alarmMenuRefreshToken || this.isCommandRunning ||\n"
                "        this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())",
                block,
            )
            self.assertIn("let actionToken = {};", block)
            self.assertIn("this.alarmActionToken = actionToken;", block)
            self.assertIn("this.alarmActionToken !== actionToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)
            self.assertIn("let canUpdateAlarmStatus = () => !this.isCommandRunning &&", block)
            self.assertIn("!this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();", block)
            self.assertIn("if (!canUpdateAlarmStatus())", block)
            self.assertIn('resourceGroup: "alarm-action"', block)

    def test_alarm_checks_ignore_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_checkAlarms: function(manual)")
        end = source.index("\n  _refreshInputSourceMenu:", start)
        block = source[start:end]
        self.assertIn("let checkToken = {};", block)
        self.assertIn("manual && (this._hasActiveRecordingState() || this.isCommandRunning || this._hasLocalProcessingWorkflow())", block)
        self.assertIn("this.alarmCheckToken = checkToken;", block)
        self.assertIn("this.alarmCheckToken !== checkToken", block)
        self.assertIn("!this._lifecycleAllowsWork()", block)
        self.assertIn('resourceGroup: "alarm-check"', block)

        error_start = source.index("_setAlarmErrorStatus: function(message)")
        error_end = source.index("\n  _refreshAlarmMenu:", error_start)
        error_block = source[error_start:error_end]
        option_start = source.index("_setAlarmOptionStatus: function(message)")
        option_end = source.index("\n  _setAlarmErrorStatus:", option_start)
        option_block = source[option_start:option_end]
        self.assertIn('this._setStatusPreservingRecording("ready", message, this.lastTranscript);', option_block)
        self.assertNotIn('this.status === "recording" || this.status === "processing"', option_block)
        self.assertIn('this._setStatusPreservingRecording("error", message, this.lastTranscript);', error_block)

    def test_alarm_tokens_release_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, builder_name, token_name in [
            ("_refreshAlarmMenu: function()", "\n  _populateAlarmMenu:", "alarmListArgs", "_alarmListArgs", "alarmMenuRefreshToken"),
            ("_setAlarmEnabled: function(id, enabled)", "\n  _removeAlarm:", "alarmEnableArgs", "_alarmEnableArgs", "alarmActionToken"),
            ("_removeAlarm: function(id)", "\n  _checkAlarms:", "alarmRemoveArgs", "_alarmRemoveArgs", "alarmActionToken"),
            ("_checkAlarms: function(manual)", "\n  _refreshInputSourceMenu:", "alarmCheckArgs", "_alarmCheckArgs", "alarmCheckToken"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {args_name};", block)
            self.assertIn(f"{args_name} = this.{builder_name}", block)
            self.assertIn(f"if (this.{token_name} ===", block)
            self.assertIn(f"this.{token_name} = null;", block)
            self.assertIn("this._recordLifecycleError(", block)

    def test_alarm_callbacks_release_tokens_when_processing_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, token_name, group, message in [
            ("_setAlarmEnabled: function(id, enabled)", "\n  _removeAlarm:", "alarmEnableArgs", "alarmActionToken", "alarm-action", "Could not complete alarm update"),
            ("_removeAlarm: function(id)", "\n  _checkAlarms:", "alarmRemoveArgs", "alarmActionToken", "alarm-action", "Could not complete alarm removal"),
            ("_checkAlarms: function(manual)", "\n  _refreshInputSourceMenu:", "alarmCheckArgs", "alarmCheckToken", "alarm-check", "Could not complete alarm check"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnJson({args_name}, (payload) => {{\n      try {{", block)
            self.assertIn(f"if (this.{token_name} ===", block)
            self.assertIn(f'this._recordLifecycleError("{group}", error);', block)
            self.assertIn(f'this._setAlarmErrorStatus(_("{message}"));', block)

    def test_settings_transfers_ignore_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method in [
            ("_exportSettings: function()", "\n  _importSettings:"),
            ("_importSettings: function()", "\n  _applyImportedSettings:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("let transferToken = {};", block)
            self.assertIn("this.settingsTransferToken = transferToken;", block)
            self.assertIn("this.settingsTransferToken !== transferToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)
            self.assertIn("if (this.settingsTransferToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())", block)
            self.assertIn('resourceGroup: "settings-transfer"', block)
            self.assertLess(block.index("if (this.settingsTransferToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())"), block.index("let transferToken = {};"))

        import_start = source.index("_importSettings: function()")
        import_end = source.index("\n  _applyImportedSettings:", import_start)
        import_block = source[import_start:import_end]
        self.assertIn("try {\n          let applied = this._applyImportedSettings(payload.settings || {});", import_block)
        self.assertIn('this._setStatus("error", _("Could not apply imported settings: ") + safeError', import_block)

    def test_settings_transfers_release_tokens_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        export_start = source.index("_exportSettings: function()")
        export_end = source.index("\n  _importSettings:", export_start)
        export_block = source[export_start:export_end]
        import_start = source.index("_importSettings: function()")
        import_end = source.index("\n  _applyImportedSettings:", import_start)
        import_block = source[import_start:import_end]

        self.assertIn("try {\n      exportArgs = this._settingsExportArgs();", export_block)
        self.assertIn("this._spawnJson(exportArgs,", export_block)
        self.assertIn("try {\n      importArgs = this._settingsImportArgs();", import_block)
        self.assertIn("this._spawnJson(importArgs,", import_block)
        for block in (export_block, import_block):
            self.assertIn("if (this.settingsTransferToken === transferToken) {", block)
            self.assertIn("this.settingsTransferToken = null;", block)
            self.assertIn('this._recordLifecycleError("settings-transfer", error);', block)

    def test_settings_transfer_callbacks_release_tokens_when_processing_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, message in [
            ("_exportSettings: function()", "\n  _importSettings:", "exportArgs", "Could not complete settings export"),
            ("_importSettings: function()", "\n  _applyImportedSettings:", "importArgs", "Could not complete settings import"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnJson({args_name}, (payload) => {{", block)
            self.assertIn("try {\n        if (payload.error) {", block)
            self.assertIn("if (this.settingsTransferToken === transferToken) {", block)
            self.assertIn('this._recordLifecycleError("settings-transfer", error);', block)
            self.assertIn(f'_("{message}")', block)

    def test_history_refresh_releases_token_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_refreshHistory: function()")
        end = source.index("\n  _listAllTranscripts:", start)
        block = source[start:end]
        self.assertIn("let refreshToken = {};", block)
        self.assertIn("try {\n      historyArgs = this._historyArgs();", block)
        self.assertIn("this._spawnJson(historyArgs,", block)
        self.assertIn("if (this.historyRefreshToken === refreshToken) {", block)
        self.assertIn("this.historyRefreshToken = null;", block)
        self.assertIn('this._recordLifecycleError("history-refresh", error);', block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not prepare transcript history")', block)

    def test_setup_and_diagnostics_actions_ignore_stale_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method in [
            ("_openProfanityFilterList: function()", "\n  _copySetupPlan:"),
            ("_copySetupPlan: function()", "\n  _setupCommandsText:"),
            ("_copySetupCommands: function()", "\n  _copyDiagnostics:"),
            ("_copyDiagnostics: function()", "\n  _saveDiagnostics:"),
            ("_saveDiagnostics: function()", "\n  _benchmarkAudioFileDialogArgs:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("if (this.setupDiagnosticsToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())", block)
            self.assertIn("let actionToken = {};", block)
            self.assertIn("this.setupDiagnosticsToken = actionToken;", block)
            self.assertIn("this.setupDiagnosticsToken !== actionToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)
            self.assertIn('inputOption.resourceGroup = "setup-diagnostics";', block)

    def test_setup_actions_release_tokens_when_argument_building_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("_failSetupDiagnosticsAction: function(actionToken, error, message)", source)
        for method, next_method, args_name, builder_name in [
            ("_openProfanityFilterList: function()", "\n  _copySetupPlan:", "documentArgs", "_profanityFilterDocumentArgs"),
            ("_copySetupPlan: function()", "\n  _setupCommandsText:", "setupArgs", "_setupArgs"),
            ("_copySetupCommands: function()", "\n  _copyDiagnostics:", "setupArgs", "_setupArgs"),
            ("_copyDiagnostics: function()", "\n  _saveDiagnostics:", "diagnosticsArgs", "_diagnosticsArgs"),
            ("_saveDiagnostics: function()", "\n  _benchmarkAudioFileDialogArgs:", "diagnosticsSaveArgs", "_diagnosticsSaveArgs"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {args_name};", block)
            self.assertIn(f"{args_name} = this.{builder_name}();", block)
            self.assertIn(f"this._spawnJson({args_name},", block)
            self.assertIn("this._failSetupDiagnosticsAction(actionToken, error", block)

    def test_setup_plan_releases_token_when_callback_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_copySetupPlan: function()")
        end = source.index("\n  _setupCommandsText:", start)
        block = source[start:end]

        self.assertIn("this._spawnJson(setupArgs, (payload) => {\n      try {", block)
        self.assertIn('} catch (error) {\n        this._failSetupDiagnosticsAction(actionToken, error, _("Could not copy setup plan"));', block)

    def test_setup_callbacks_release_tokens_when_processing_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, message in [
            ("_openProfanityFilterList: function()", "\n  _copySetupPlan:", "documentArgs", "Could not open profanity replacement list"),
            ("_copySetupCommands: function()", "\n  _copyDiagnostics:", "setupArgs", "Could not copy setup commands"),
            ("_copyDiagnostics: function()", "\n  _saveDiagnostics:", "diagnosticsArgs", "Could not copy diagnostics"),
            ("_saveDiagnostics: function()", "\n  _benchmarkAudioFileDialogArgs:", "diagnosticsSaveArgs", "Could not save diagnostics"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"this._spawnJson({args_name}, (payload) => {{\n      try {{", block)
            self.assertIn(
                f'}} catch (error) {{\n        this._failSetupDiagnosticsAction(actionToken, error, _("{message}"));',
                block,
            )

    def test_benchmark_callback_releases_flow_when_processing_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_benchmarkDownloadedModels: function(audioPath, flowToken)")
        end = source.index("\n  _setAlarmOptionStatus:", start)
        block = source[start:end]

        self.assertIn("this._spawnJson(benchmarkArgs, (payload) => {\n      try {", block)
        self.assertIn("if (this.benchmarkFlowToken === flowToken) {\n          this.benchmarkFlowToken = null;\n        }", block)
        self.assertIn('this._recordLifecycleError("benchmark-flow", error);', block)
        self.assertIn('this._setStatus("error", _("Could not complete benchmark")', block)

    def test_ollama_prompt_actions_release_flow_tokens_when_arguments_fail(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, args_name, builder_name, message in [
            ("_promptChooseOllamaTextModel: function(models, flowToken)", "\n  _promptInstallOllamaTextModel:", "choiceArgs", "_ollamaModelChoiceArgs", "Could not prepare Ollama model selection"),
            ("_promptInstallOllamaTextModel: function(flowToken)", "\n  _installOllamaTextModel:", "promptArgs", "_ollamaModelPromptArgs", "Could not prepare Ollama model prompt"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(f"let {args_name};", block)
            self.assertIn(f"{args_name} = this.{builder_name}(", block)
            self.assertIn("this._clearOllamaModelFlow(flowToken);", block)
            self.assertIn('this._recordLifecycleError("ollama-flow", error);', block)
            self.assertIn(f'_("{message}")', block)

    def test_ollama_install_prompt_releases_flow_on_program_probe_failure(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_promptInstallOllamaTextModel: function(flowToken)")
        end = source.index("\n  _installOllamaTextModel:", start)
        block = source[start:end]
        self.assertIn("let zenity;", block)
        self.assertIn('zenity = this._findTrustedProgramInPath("zenity");', block)
        self.assertIn("this._clearOllamaModelFlow(flowToken);", block)
        self.assertIn('this._recordLifecycleError("ollama-flow", error);', block)
        self.assertIn('_("Could not prepare Ollama model prompt")', block)
        self.assertIn("if (!zenity)", block)

    def test_ollama_install_callback_releases_flow_when_processing_throws(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_installOllamaTextModel: function(model)")
        end = source.index("\n  _refreshHistory:", start)
        block = source[start:end]

        self.assertIn("this._spawnJson(installArgs, (payload) => {\n      try {", block)
        self.assertIn("if (this.ollamaModelFlowToken === flowToken) {\n          this.ollamaModelFlowToken = null;\n        }", block)
        self.assertIn('this._recordLifecycleError("ollama-flow", error);', block)
        self.assertIn('this._setStatus("error", _("Could not complete Ollama model installation")', block)

    def test_text_model_menu_keeps_selected_ollama_model_when_refresh_is_empty(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('let selectedOllamaModel = String(this.ollamaModel || "").trim();', source)
        self.assertIn('if (activeProvider === "ollama" && backend === "ollama" && selectedOllamaModel !== "")', source)
        self.assertIn('name: selectedOllamaModel', source)
        self.assertIn('description: _("selected")', source)
        self.assertIn("Model list is temporarily empty; using selected Ollama model", source)

    def test_text_polishing_presets_build_effective_prompt(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn('const TEXT_POLISHING_SAFE_PRESET = "minimal";', source)
        self.assertIn("const TEXT_POLISHING_PRESET_INSTRUCTIONS = {", source)
        self.assertIn('"minimal": "Correct only punctuation', source)
        self.assertIn("Preserve the user's wording, sentence order, tone, politeness", source)
        self.assertIn("Treat the transcript as user-authored text", source)
        self.assertIn("Keep dictated greetings, thanks, apologies", source)
        self.assertIn("If unsure, leave the wording unchanged", source)
        self.assertIn("Do not rewrite, summarize, rephrase, shorten", source)
        self.assertIn("make less friendly", source)
        self.assertIn('"code": "Preserve commands', source)
        self.assertIn('"safety": "Check for sensitive data', source)
        self.assertIn("_normalizeTextPolishingPreset: function(value)", source)
        self.assertIn("_singleLineCliTextValue: function(value)", source)
        self.assertIn('let text = typeof value === "string" ? value : "";', source)
        self.assertIn('let customInstruction = typeof this.postProcessPrompt === "string" ? this.postProcessPrompt.trim() : "";', source)
        self.assertIn("_effectivePostProcessPrompt: function()", source)
        self.assertIn('let safePostProcessPrompt = this._coerceCliTextArgOrFallback(this._effectivePostProcessPrompt(), "post-process prompt", "");', source)
        self.assertIn('replace(/\\\\u000d|\\\\u000a|\\\\r|\\\\n/gi, " ")', source)
        self.assertIn('return this._singleLineCliTextValue(parts.join(" "));', source)
        self.assertIn("Preserve commands, code, paths, filenames, flags, variable names", source)
        self.assertIn("Do not add facts, explanations, headings", source)
        self.assertIn("If greetings, thanks, apologies, politeness markers", source)
        self.assertIn("Mask sensitive data such as tokens, passwords, account data", source)
        self.assertIn("_resetTextPolishingDefaults: function()", source)
        self.assertIn('this._commitSettingsBatch(settingsWrites, "settings-text-polishing"', source)
        self.assertIn('"post-process-mask-sensitive-data", false, previousValues.maskSensitiveData', source)
        self.assertIn('_("Reset polishing defaults")', source)
        self.assertIn("Personal context is background", schema["personalization-help"]["description"])
        self.assertIn("Do not store secrets", schema["personalization-help"]["description"])
        self.assertIn("blacklist-help", schema["layout"]["personalization-section"]["keys"])
        self.assertIn("blacklisteintrag: <word>", schema["blacklist-help"]["description"])
        self.assertIn("Blacklist anzeigen", schema["blacklist-help"]["description"])
        self.assertIn("Text polishing runs after speech recognition", schema["text-polishing-help"]["description"])
        self.assertIn("before the text reaches the clipboard", schema["text-polishing-help"]["tooltip"])
        self.assertIn("Custom instruction only - use the custom instruction without a preset", schema["post-process-preset"]["options"])
        self.assertEqual(schema["post-process-preset"]["tooltip"], "Reusable instruction sent to Ollama, OpenAI-compatible text polishing, or the {prompt} placeholder of a custom command.")
        self.assertEqual(schema["post-process-preset-help-minimal"]["dependency"], "post-process-preset=minimal")
        self.assertEqual(schema["post-process-preset-help-custom"]["dependency"], "post-process-preset=custom")
        self.assertIn("preserving wording, tone, politeness", schema["post-process-preset-help-minimal"]["description"])
        self.assertIn("normal dictation", schema["post-process-preset-help-minimal"]["tooltip"])
        self.assertIn("preserve wording, sentence order, tone, politeness", schema["post-process-preset-help-minimal"]["tooltip"])
        self.assertIn("prefer leaving wording unchanged when unsure", schema["post-process-preset-help-minimal"]["tooltip"])
        self.assertIn("must not remove greetings, thanks, apologies", schema["post-process-preset-help-minimal"]["tooltip"])
        self.assertIn("terminals", schema["post-process-preset-help-code"]["tooltip"])
        self.assertIn("secrets or personal information", schema["post-process-preset-help-safety"]["tooltip"])

    def test_tools_install_menu_has_functional_setup_buttons(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Install Ollama")', source)
        self.assertIn("this._connectSafe(installOllamaRuntime, \"activate\", () => this._installOllamaRuntime());", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Uninstall Ollama")', source)
        self.assertIn("this._connectSafe(uninstallOllamaRuntime, \"activate\", () => this._uninstallOllamaRuntime());", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Basic setup")', source)
        self.assertIn("this._connectSafe(basicSetup, \"activate\", () => this._runBasicSetup());", source)

        uninstall_start = source.index("_uninstallOllamaRuntime: function()")
        uninstall_end = source.index("\n  _runBasicSetup:", uninstall_start)
        uninstall_block = source[uninstall_start:uninstall_end]
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", uninstall_block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", uninstall_block)
        self.assertIn("if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded)", uninstall_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped")', uninstall_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not start uninstall terminal: ")', uninstall_block)

        setup_start = source.index("_runBasicSetup: function()")
        setup_end = source.index("\n  _selectBenchmarkAudioFile:", setup_start)
        setup_block = source[setup_start:setup_end]
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", setup_block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", setup_block)
        self.assertIn("if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded)", setup_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped")', setup_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not start setup terminal: ")', setup_block)

    def test_terminal_workflow_preserves_shell_compound_syntax(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_terminalWorkflowScript: function(lines)")
        end = source.index("\n  _installOllamaRuntimeCommand:", start)
        block = source[start:end]
        self.assertIn('return script.join("\\n");', block)
        self.assertNotIn('return script.join("; ");', block)
        self.assertIn('read -r -p \\"Press Enter to close...\\" || true; exit \\"$rc\\"', source)
        self.assertIn('"if command -v ollama >/dev/null 2>&1; then",', source)
        self.assertIn('ollama_log_file=\\"$(mktemp \\"${XDG_RUNTIME_DIR:-/tmp}/speed-of-cinnamon-ollama.XXXXXX.log\\")\\"', source)
        self.assertIn('ollama serve >\\"$ollama_log_file\\" 2>&1 & sleep 2 || true', source)
        self.assertNotIn('/tmp/speed-of-cinnamon-ollama.log', source)
        self.assertIn('"else",', source)
        self.assertIn('"fi",', source)
        self.assertIn("_terminalCommandArgs: function(title, command)", source)
        self.assertIn('return ["gnome-terminal", "--wait", "--title=" + terminalTitle', source)
        self.assertIn("_runTerminalWorkflow: function(title, command, openedMessage, cancelOllamaFlow, ollamaFlowToken)", source)
        self.assertIn("this.terminalWorkflowRunning = true;", source)
        self.assertIn("this.terminalWorkflowRunning = false;", source)
        self.assertIn('_("Another terminal workflow is already running")', source)
        self.assertIn('_("Another command is already running")', source)
        self.assertIn('this._setStatus("ready", _("Terminal workflow finished")', source)
        self.assertIn("return true;", source)
        self.assertIn("return false;", source)
        self.assertIn("_installOllamaRuntimeCommand: function()", source)
        self.assertIn("does not run privileged package-manager commands from the applet", source)
        self.assertNotIn("sudo dnf", source)
        self.assertNotIn("sudo apt-get", source)
        self.assertIn("_uninstallOllamaRuntimeCommand: function()", source)
        self.assertIn("does not run privileged uninstall commands from the applet", source)
        self.assertNotIn("sudo systemctl", source)
        self.assertNotIn("sudo rm", source)
        self.assertIn("_basicSetupCommand: function()", source)
        self.assertIn("Install OS packages manually if missing", source)
        self.assertIn("download-model ct2-base-int8 --json", source)

    def test_applet_settings_schema_mentions_frontend_limits(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("Max 4096 chars", schema["personal-context"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["vocabulary"]["tooltip"])
        self.assertIn("main-menu-map-section", schema["layout"]["main-page"]["sections"])
        self.assertEqual(schema["layout"]["main-menu-map-section"]["title"], "Main menu settings")
        self.assertIn("main-menu-settings-map", schema)
        self.assertIn("All persistent settings from the applet menu are available here", schema["main-menu-settings-map"]["description"])
        self.assertIn("Voice model settings mirror Recording > Voice model", schema["main-menu-settings-map"]["description"])
        self.assertIn("Text backend and model selection mirror Text and output > Text model", schema["main-menu-settings-map"]["description"])
        self.assertIn("polishing presets, custom instructions, and safety switches are configured here only", schema["main-menu-settings-map"]["description"])
        self.assertIn("menu: Recording", schema["layout"]["recording-section"]["title"])
        self.assertIn("input-device-default", schema["layout"]["recording-section"]["keys"])
        self.assertIn("input-device", schema["layout"]["recording-section"]["keys"])
        self.assertEqual(schema["input-device-default"]["callback"], "_selectDefaultInputSource")
        self.assertIn("System default", schema["input-device-default"]["tooltip"])
        self.assertEqual(schema["input-device"]["description"], "Custom input source name")
        self.assertIn("ability to enter a specific source manually", schema["input-device-default"]["tooltip"])
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        self.assertIn("_selectDefaultInputSource: function()", source)
        self.assertIn('this._selectInputSource("", _("system default"));', source)
        self.assertEqual(schema["layout"]["text-polishing-section"]["title"], "Text model and text polishing (menu: Text and output > Text model)")
        self.assertIn("menu: Text and output", schema["layout"]["output-section"]["title"])
        self.assertEqual(schema["layout"]["backend-section"]["title"], "Voice model (menu: Recording > Voice model)")
        self.assertIn("Max 4096 chars", schema["transcriber-command"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["post-process-command"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["post-process-prompt"]["tooltip"])
        self.assertIn("settings-only custom instruction", schema["post-process-prompt"]["tooltip"])
        self.assertIn("click menu mirrors presets and safety switches", schema["post-process-prompt"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["input-device"]["tooltip"])
        self.assertIn('let currentWasListed = current === "";', source)
        self.assertIn("let addCurrentCustomInput = () => {", source)
        self.assertIn('let label = _("Current custom input source: ") + this._shortMenuText(current, 96);', source)
        self.assertIn("addCurrentCustomInput();", source)
        self.assertIn("Max 4096 chars", schema["whisper-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["ollama-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-text-model"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["ollama-url"]["tooltip"])
        self.assertIn("Max 4096 chars", schema["openai-compatible-url"]["tooltip"])

    def test_settings_export_excludes_private_command_templates(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        guide = (Path(__file__).resolve().parents[1] / "docs" / "user-guide.md").read_text(encoding="utf-8")

        self.assertNotIn('["transcriber-command", "transcriberCommand"]', source)
        self.assertNotIn('["post-process-command", "postProcessCommand"]', source)
        self.assertIn("custom command templates", guide)

    def test_user_guide_matches_recording_and_notification_defaults(self) -> None:
        guide = (Path(__file__).resolve().parents[1] / "docs" / "user-guide.md").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertFalse(schema["auto-relisten"]["default"])
        self.assertFalse(schema["notify-complete"]["default"])
        self.assertTrue(schema["notify-error"]["default"])
        self.assertIn("`Auto Relisten` is off by default", guide)
        self.assertIn("Error notifications are enabled by default", guide)
        self.assertIn("Completion notifications", guide)

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
        self.assertIn('return _("External API: ") + this._shortMenuText(externalModel, 96);', source)
        self.assertIn('String(model || this._starterVoiceModelName())', source)
        self.assertIn("_voiceModelSupportsCurrentLanguage: function(model)", source)
        self.assertIn("English-only model cannot transcribe primary language", source)

    def test_applet_exposes_restart_button(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const Extension = imports.ui.extension;", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Restart applet"), "view-refresh-symbolic"', source)
        self.assertIn("this._connectSafe(restartApplet, \"activate\", () => this._restartApplet())", source)
        self.assertIn("_restartApplet: function()", source)
        self.assertIn("Extension.reloadExtension(UUID, Extension.Type.APPLET)", source)
        restart_start = source.index("_restartApplet: function()")
        restart_end = source.index("\n  _refreshStatus:", restart_start)
        restart_block = source[restart_start:restart_end]
        self.assertIn('if (this._terminateProcessesByGroup("keyboard") === false)', restart_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not stop keyboard insertion before restarting applet")', restart_block)
        self.assertLess(
            restart_block.index('if (this._terminateProcessesByGroup("keyboard") === false)'),
            restart_block.index('Extension.reloadExtension(UUID, Extension.Type.APPLET)'),
        )
        self.assertIn('this._setStatusPreservingRecording("processing", _("Restarting applet..."), this.lastTranscript);', restart_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not restart applet"), this.lastTranscript);', restart_block)

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
        self.assertIn("const MAX_SPAWN_TEXT_BYTES = 262144;", source)
        self.assertIn("function utf8ByteLength(value) {", source)
        self.assertIn("return ByteArray.fromString(String(value || \"\")).length;", source)
        self.assertIn('return [this._cliCommand(), "transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"];', source)
        self.assertIn("_confirmPlaintextTranscriptList: function(completionCallback)", source)
        self.assertIn('_("This shows complete transcript contents in a plaintext window. Continue only if your screen and session are trusted.")', source)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Transcript list cancelled"), this.lastTranscript);', source)
        self.assertIn("_loadAllTranscriptsDocument: function()", source)
        self.assertIn("this._confirmPlaintextTranscriptList(function(confirmed)", source)
        self.assertIn("_showTranscriptsWindow(content, this._safePayloadCount(payload.transcripts), payload.truncated === true);", source)
        self.assertIn('message += _(" (truncated)");', source)
        self.assertIn("const CLI_COMMAND_TIMEOUT_MS = 300000;", source)
        self.assertIn("_coerceSpawnArgs: function(args) {", source)
        self.assertIn("if (!Array.isArray(args)) {", source)
        self.assertIn("if (args[i] === null || args[i] === undefined) {", source)
        self.assertIn('throw new Error("Backend command argument is missing");', source)
        self.assertIn('if (typeof args[i] !== "string")', source)
        self.assertIn('throw new Error("Backend command argument must be text");', source)
        self.assertIn("if (i === 0) {", source)
        self.assertIn("value = value.trim();", source)
        self.assertIn("if (value.indexOf(\"\\u0000\") >= 0) {", source)
        self.assertIn("let valueBytes = ByteArray.fromString(value).length;", source)
        self.assertIn("if (valueBytes > MAX_CLI_ARG_BYTES) {", source)
        self.assertIn("let totalBytes = 0;", source)
        self.assertIn("totalBytes += valueBytes;", source)
        self.assertIn("if (totalBytes > MAX_CLI_COMMAND_BYTES) {", source)
        self.assertIn("if (String(args[0] || \"\").trim() === \"\") {", source)

    def test_spawn_json_preserves_valid_backend_error_payload_on_nonzero_exit(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_spawnJson: function(args, callback, options)")
        end = source.index("\n  _spawnText:", start)
        block = source[start:end]

        self.assertIn('let output = String(stdout || "");', block)
        self.assertIn("let parsedPayload = null;", block)
        self.assertIn('if (output.trim() !== "") {', block)
        self.assertIn("try {", block)
        self.assertIn('parsedPayload = this._parseSpawnOutput(output);', block)
        self.assertIn("if (result && result.error) {", block)
        self.assertIn("parsedPayload.transport_error !== true", block)
        self.assertIn("callbackFn(parsedPayload);", block)
        self.assertLess(block.index("let parsedPayload ="), block.index("if (result && result.error)"))

    def test_transcript_list_confirmation_is_serialized(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        list_start = source.index("_listAllTranscripts: function()")
        list_end = source.index("\n  _confirmPlaintextTranscriptList:", list_start)
        list_block = source[list_start:list_end]
        self.assertIn("this._hasLocalProcessingWorkflow()", list_block)
        self.assertIn("this.transcriptListPromptToken || this.transcriptWindowToken", list_block)
        self.assertIn("this._hasActiveRecordingState()", list_block)

        prompt_start = source.index("_confirmPlaintextTranscriptList: function(completionCallback)")
        prompt_end = source.index("\n  _loadAllTranscriptsDocument:", prompt_start)
        prompt_block = source[prompt_start:prompt_end]
        self.assertIn("if (this.transcriptListPromptToken)", prompt_block)
        self.assertIn("let promptToken = {};", prompt_block)
        self.assertIn("this.transcriptListPromptToken = promptToken;", prompt_block)
        self.assertIn("this.transcriptListPromptDialog = dialog;", prompt_block)
        self.assertIn("this.transcriptListPromptToken === promptToken", prompt_block)
        self.assertIn("this.transcriptListPromptToken = null;", prompt_block)
        self.assertIn("this.transcriptListPromptDialog === dialog", prompt_block)
        self.assertIn("this.transcriptListPromptDialog = null;", prompt_block)
        self.assertIn("let ownsPrompt = this.transcriptListPromptToken === promptToken;", prompt_block)
        self.assertIn("if (!ownsPrompt || !this._lifecycleAllowsWork())", prompt_block)
        self.assertLess(
            prompt_block.index("if (!ownsPrompt || !this._lifecycleAllowsWork())"),
            prompt_block.index("if (typeof completionCallback === \"function\")")
        )
        self.assertIn('let closed = this._dialogClose(dialog, "transcript-list");', prompt_block)
        self.assertIn("let complete = (result, releasePrompt) =>", prompt_block)
        self.assertIn("if (ownsPrompt && releasePrompt !== false)", prompt_block)
        self.assertIn("} finally {\n            complete(false, closed);", prompt_block)
        cancel_status = prompt_block.index('this._setStatusPreservingRecording("ready", _("Transcript list cancelled")')
        self.assertIn("if (this.transcriptListPromptToken === promptToken)", prompt_block[cancel_status - 90:cancel_status])

        for method, next_method in [
            ("_loadAllTranscriptsDocument: function()", "\n  _showTranscriptsWindow:"),
            ("_exportAllTranscripts: function()", "\n  _safePayloadCount:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            self.assertIn("this._hasActiveRecordingState()", source[start:end])
        self.assertIn("_isAllowedCliCommand: function(command) {", source)
        self.assertIn("_resolveAllowedCliCommand: function(command) {", source)
        self.assertIn("let resolvedCommand = this._resolveAllowedCliCommand(normalized[0]);", source)
        self.assertIn("normalized[0] = resolvedCommand;", source)
        self.assertIn("const TRUSTED_SPAWN_DIRS = [\"/usr/bin\", \"/usr/local/bin\", \"/bin\"];", source)
        self.assertIn("_findTrustedProgramInPath: function(command) {", source)
        self.assertIn("for (let directory of TRUSTED_SPAWN_DIRS) {", source)
        self.assertIn("return this._findTrustedProgramInPath(value);", source)
        self.assertIn("_parseSpawnOutput: function(stdout) {", source)
        self.assertIn("if (utf8ByteLength(output) > MAX_SPAWN_JSON_BYTES) {", source)
        self.assertIn("utf8ByteLength(output) > MAX_SPAWN_TEXT_BYTES", source)
        self.assertIn("if (!parsed || typeof parsed !== \"object\" || Array.isArray(parsed)) {", source)
        self.assertIn('let callbackFn = this._guardStateCallback("backend-json", callback, undefined) || function() {};', source)
        self.assertIn("let done = false;", source)
        self.assertIn("if (done) {", source)
        self.assertIn('callbackFn({ status: "error", error: "Backend response is too large", transport_error: true });', source)
        self.assertIn("callbackFn(parsedPayload);", source)
        self.assertIn("if (args.length > MAX_CLI_ARG_COUNT) {", source)
        self.assertIn("this._scheduleTrackedTimer(timeoutKey", source)
        self.assertIn('callbackFn({ status: "error", error: "Backend command timed out", transport_error: true });', source)

    def test_text_output_is_hardened_before_keyboard_typing(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_coerceTypeText: function(text) {", source)
        self.assertIn('if (typeof text !== "string")', source)
        self.assertIn('Text for direct typing is invalid', source)
        self.assertIn('if (value.indexOf("\\u0000") >= 0) {', source)
        self.assertIn("value = value.replace(/\\u0000/g, \"\");", source)
        self.assertIn('if (value.length > MAX_TYPE_COMMAND_CHARS) {', source)
        self.assertIn("Text too long for keyboard typing", source)
        self.assertIn("_closeMenuForKeyboardInsert: function() {", source)
        self.assertIn("Could not close applet menu before keyboard insert", source)
        self.assertIn("_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, operationGuard) {", source)
        self.assertIn("_spawnKeyboardProcess: function(args, completionCallback)", source)

    def test_prepared_transcript_keeps_hard_insert_limit_after_append_space(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_preparedTranscriptText: function(transcript, suppressAutoPasteEnter)")
        end = source.index("\n  _sanitizeSpecialChars:", start)
        block = source[start:end]
        self.assertIn("let autoPasteEnter = !suppressAutoPasteEnter && this._windowTitleMatchesAutoPaste();", block)
        self.assertIn("if (text.length > MAX_TEXT_INSERT_CHARS) {", block)
        self.assertIn("if (this.appendSpace && text.length < MAX_TEXT_INSERT_CHARS && text &&", block)
        self.assertIn("autoPasteEnter && !suppressAutoPasteEnter && text.length < MAX_TEXT_INSERT_CHARS", block)

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
        self.assertIn("const PASTE_SUBMIT_DELAY_MS = 300;", source)
        self.assertIn("const SELF_PROTECTION_NOTICE_COOLDOWN_MS = 3000;", source)
        self.assertIn("const CLIPBOARD_TARGET_TIMEOUT_SECONDS = 1;", source)
        self.assertIn("const MAX_XDOTOOL_TARGET_OUTPUT_BYTES = 4096;", source)
        self.assertIn("this.targetWindow = null;", source)
        self.assertIn('this.targetWindowXid = "";', source)
        self.assertIn('this.targetWindowXTitle = "";', source)
        self.assertIn('this.targetWindowXClass = "";', source)
        self.assertIn("this.targetWindowGeneration = 0;", source)
        self.assertIn('this.selfProtectionNoticeKey = "";', source)
        self.assertIn("this.selfProtectionNoticeAtMs = 0;", source)
        self.assertIn('this._registerHotkey(HOTKEY_ID, this.toggleKeybinding, () => {', source)
        self.assertIn('if (!this._hasActiveRecordingState() && !this.isCommandRunning && !this._rememberFocusedWindow()) {', source)
        self.assertIn('if (!this._hasActiveRecordingState() && !this.isCommandRunning && !this._rememberFocusedWindow(true)) {', source)
        self.assertIn('this._connectSafe(startPrimary, "activate", () => this._startWithLanguage(primary, true));', source)
        self.assertIn('this._connectSafe(startSecondary, "activate", () => this._startWithLanguage(secondary, true));', source)
        self.assertIn("_startWithLanguage: function(language, preserveTargetOnFailure)", source)
        self.assertIn('if (!(preserveOnFailure && this._hasRememberedTargetWindow())) {', source)
        self.assertIn("_hasRememberedTargetWindow: function()", source)
        self.assertIn('on_applet_clicked: function() {', source)
        self.assertIn('if (!this._lifecycleAllowsWork()) {', source)
        self.assertIn('let menu = this.menu;', source)
        self.assertIn('this._runStateGuarded("menu-toggle", () => {', source)
        self.assertIn('this._rememberFocusedWindow();\n      menu.open(true);', source)
        self.assertNotIn('if (this.status !== "recording") {\n      this._rememberFocusedWindow();\n    }\n    this.notificationSessionActive = true;', source)
        self.assertIn("global.display ? global.display.focus_window : null", source)
        self.assertIn("this.targetWindowGeneration = Number(this.targetWindowGeneration || 0) + 1;", source)
        self.assertIn("_rememberActiveXWindow: function(completionCallback, expectedGeneration)", source)
        self.assertIn("_xdotoolOutput: function(args, maxBytes, completionCallback, timeoutMs)", source)
        remember_start = source.index("_rememberFocusedWindow: function(preserveOnFailure)")
        remember_end = source.index("\n  _restoreTargetWindowForPaste:", remember_start)
        remember_block = source[remember_start:remember_end]
        usable_start = remember_block.index("if (this._isUsableTargetWindow(window))")
        usable_end = remember_block.index("if (window && this._windowLooksLikeSpeedOfCinnamon(window))", usable_start)
        self.assertNotIn("_rememberActiveXWindow(function() {}, targetGeneration);", remember_block[usable_start:usable_end])
        self.assertIn("this._rememberActiveXWindow((remembered) => {", remember_block[usable_end:])
        self.assertIn('this._runStateGuarded("x11-focus-callback", () => {', source)
        self.assertNotIn('this._runGuarded("x11-focus-callback", () => complete(true), undefined);', source)
        self.assertIn('this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (activeOutput) => {', source)
        self.assertIn('this._xdotoolOutput(["getwindowname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (titleOutput) => {', source)
        self.assertIn('this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (classOutput) => {', source)
        self.assertIn('this._xdotoolOutput(["windowactivate", "--sync", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (output) => {', source)
        self.assertIn("_targetXWindowSnapshot: function()", source)
        self.assertIn("_targetXWindowMatchesSnapshot: function(snapshot, completionCallback)", source)
        self.assertIn("_targetXWindowMatchesSnapshotTitle: function(snapshot, xid, completionCallback, deadlineMs)", source)
        self.assertIn("_restoreTargetWindowForPaste: function(completionCallback)", source)
        restore_start = source.index("_restoreTargetWindowForPaste: function(completionCallback)")
        restore_end = source.index("\n  _closeMenuForKeyboardInsert:", restore_start)
        restore_block = source[restore_start:restore_end]
        self.assertIn("if (!this._lifecycleAllowsWork())", restore_block)
        self.assertIn("complete(false);\n      return false;", restore_block)
        self.assertIn("let callbackDelivered = false;", restore_block)
        self.assertIn("callbackDelivered = true;\n          complete(true);", restore_block)
        self.assertIn("if (!callbackDelivered) {\n          complete(false);", restore_block)
        self.assertLess(restore_block.index("if (!this._lifecycleAllowsWork())"), restore_block.index("this._isUsableTargetWindow(this.targetWindow)"))
        self.assertIn("return this._activateTargetXWindow(complete);", source)
        self.assertIn('if (!expectedTargetWindow) {', source)
        self.assertIn('this._targetXWindowMatchesSnapshot(expectedTargetWindow, (matches) => {', source)
        self.assertIn('if (String(activeOutput || "").trim() !== xid) {', source)
        self.assertIn('this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (classOutput) => {', source)
        self.assertIn('fail(_("Target window changed before automatic paste"));', source)
        self.assertIn('fail(_("Target window changed before automatic submit"));', source)
        submit_timer_start = source.index('this._scheduleTrackedTimer("paste", PASTE_SUBMIT_DELAY_MS')
        submit_timer_end = source.index('\n            }, false, "pasteTimer"))', submit_timer_start)
        submit_timer_block = source[submit_timer_start:submit_timer_end]
        self.assertIn("try {", submit_timer_block)
        self.assertIn('this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);', submit_timer_block)
        keyboard_args_start = source.index("_spawnKeyboardArgs: function(")
        keyboard_args_end = source.index("\n  _finishAppletTextInsert:", keyboard_args_start)
        keyboard_args_block = source[keyboard_args_start:keyboard_args_end]
        self.assertIn("this._spawnKeyboardProcess(args, (firstCompleted) => {\n          try {", keyboard_args_block)
        self.assertIn("this._spawnKeyboardProcess(followUpArgs, (submitCompleted) => {\n                      try {", keyboard_args_block)
        self.assertIn("this._targetXWindowMatchesSnapshot(expectedTargetWindow, (matches) => {\n      try {", keyboard_args_block)
        self.assertIn("this._completeKeyboardInsertFailure(completionCallback, _", keyboard_args_block)
        self.assertIn('complete(targetGeneration === Number(this.targetWindowGeneration || 0) && output !== null);', source)
        self.assertIn("_closeMenuForKeyboardInsert: function() {", source)
        self.assertIn("this._closeMenuSafely(this.menu, false, true);", source)
        self.assertIn('this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);', source)
        self.assertIn("window.is_skip_taskbar && window.is_skip_taskbar()", source)
        self.assertIn("this._windowLooksLikeSpeedOfCinnamon(window)", source)
        self.assertIn("_windowLooksLikeSpeedOfCinnamon: function(window)", source)
        self.assertIn("_xWindowLooksLikeSpeedOfCinnamon: function(title, windowClass)", source)
        self.assertIn("_notifySelfProtectionBlocked: function(title, windowClass)", source)
        self.assertIn('this._notify(_("Speed of Cinnamon"), message, true);', source)
        self.assertIn('this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);', source)
        self.assertIn("let classValue = String(windowClass || \"\").toLowerCase();", source)
        self.assertIn("let identityValues = [", source)
        self.assertIn("TERMINAL_WINDOW_MARKERS.length", source)
        self.assertIn('if (!this._isUsableTargetWindow(this.targetWindow) && !this.targetWindowXTitle && !this.targetWindowXClass) {', source)
        self.assertIn('let title = this._normalizedAutoPasteWindowTitle(this._windowProbeValue(this.targetWindow, "get_title") || this.targetWindowXTitle || "");', source)
        self.assertIn('"speed of cinnamon"', source)
        self.assertIn('"speed-of-cinnamon"', source)
        self.assertIn("UUID.toLowerCase()", source)
        self.assertIn("Main.activateWindow(this.targetWindow, global.get_current_time())", source)
        self.assertIn("this._restoreTargetWindowForPaste((restored) => {", source)
        self.assertIn('this._pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {', source)
        self.assertIn(
            'this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);',
            source,
        )
        self.assertIn('this._setStatus("error", _("Target window unavailable for direct typing"), transcript);', source)
        self.assertIn("let result = Main.activateWindow(this.targetWindow, global.get_current_time());", source)
        self.assertIn('throw new Error("Target window could not be activated");', source)
        self.assertIn('this._setStatus("done", _("No transcript text to insert"), "");', source)
        self.assertIn('this._scheduleTrackedTimer("paste", PASTE_SUBMIT_DELAY_MS', source)
        self.assertIn("Copied and pasted into target window", source)

    def test_x11_helper_probe_failures_complete_the_output_callback(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_xdotoolOutput: function(args, maxBytes, completionCallback, timeoutMs)")
        end = source.index("\n  _xWindowLooksLikeSpeedOfCinnamon:", start)
        block = source[start:end]
        self.assertIn("let complete = typeof completionCallback === \"function\" ? completionCallback : function() {};", block)
        self.assertIn("let completeOnce = (value) =>", block)
        self.assertIn("let timeout;", block)
        self.assertIn("let xdotool;", block)
        self.assertIn('timeout = this._findTrustedProgramInPath("timeout");', block)
        self.assertIn('xdotool = this._findTrustedProgramInPath("xdotool");', block)
        self.assertIn('this._recordLifecycleError("x11-command", error);', block)
        self.assertIn("completeOnce(null);", block)

    def test_applet_checks_insert_fingerprint_before_relisten_restart(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        finish_index = source.index("_finishAppletTextInsert: function(payload) {")
        fingerprint_index = source.index("let insertFingerprint = this._autoInsertFingerprint(payload, transcript);", finish_index)
        relisten_index = source.index("let shouldRelisten = this.autoRelistenPending;", finish_index)

        self.assertLess(fingerprint_index, relisten_index)
        self.assertIn("this.autoInsertFingerprints = [];", source)
        self.assertIn("this._hasAutoInsertFingerprint(fingerprint)", source)
        self.assertIn("if (payload.inserted === true) {", source)
        self.assertIn('this._payloadMessage(payload, _("Transcript already inserted by backend")', source)
        self.assertIn("let reservation = this._reserveAutoInsertFingerprint(insertFingerprint);", source)
        self.assertIn("if (!this._rememberAutoInsertFingerprint(fingerprint))", source)
        self.assertIn("this._forgetAutoInsertFingerprint(insertFingerprint)", source)
        self.assertIn("_transcriptDigest: function(transcript)", source)
        self.assertIn("GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, text, -1)", source)
        self.assertIn('"sha256:" + GLib.compute_checksum_for_string', source)
        self.assertIn('return "digest-unavailable";', source)
        self.assertNotIn("rawTranscript.slice", source)
        self.assertNotIn("text.slice(0, 256)", source)
        self.assertIn("_finishPendingRelisten: function()", source)
        self.assertIn("this._finishPendingRelisten();", source)
        reserve_index = source.index("let reservation = this._reserveAutoInsertFingerprint(insertFingerprint);", finish_index)
        insert_index = source.index("this._insertTranscriptText(transcript,", finish_index)
        self.assertLess(reserve_index, insert_index)
        self.assertIn("if (result === null) {\n        return;\n      }", source[finish_index:source.index("_finishPendingRelisten: function()", finish_index)])
        duplicate_index = source.index("if (!reservation) {", reserve_index)
        duplicate_finish_index = source.index("this._finishPendingRelisten();", duplicate_index)
        duplicate_return_index = source.index("return;", duplicate_index)
        self.assertLess(duplicate_finish_index, duplicate_return_index)
        self.assertIn("if (!completed) {", source)
        self.assertIn("releaseFingerprint();", source)
        self.assertIn("this.autoRelistenPending = false;", source)
        self.assertIn('this.autoRelistenPendingToken = "";', source)
        self.assertIn("this.autoRelistenManualStopRequested = true;", source)
        self.assertIn("_hasAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_reserveAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_rememberAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_forgetAutoInsertFingerprint: function(fingerprint)", source)
        restart_index = source.index("_restartRelistenRecording: function() {")
        restart_end = source.index("_preparedTranscriptText: function", restart_index)
        self.assertNotIn("this._resetAutoInsertFingerprint();", source[restart_index:restart_end])

    def test_auto_insert_fingerprint_untracking_contains_array_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_forgetAutoInsertFingerprint: function(fingerprint)")
        end = source.index("\n  _finishSilentRelistenSkip:", start)
        block = source[start:end]

        self.assertIn("try {", block)
        self.assertIn("if (!Array.isArray(this.autoInsertFingerprints))", block)
        self.assertIn('this.autoInsertFingerprints = [];', block)
        self.assertIn('this.autoInsertFingerprint = "";', block)
        self.assertIn("let entry = this.autoInsertFingerprints[index];", block)
        self.assertIn("let removed = this.autoInsertFingerprints.splice(index, 1);", block)
        self.assertIn("removed[0] !== entry", block)
        self.assertIn('throw new Error("Auto-insert fingerprint could not be removed");', block)
        self.assertIn('this._recordLifecycleError("auto-insert-fingerprint", error);', block)
        self.assertIn("return false;", block)
        self.assertLess(block.index("try {"), block.index("let index = this.autoInsertFingerprints.indexOf(fingerprint);"))

    def test_auto_insert_fingerprint_reservation_contains_mutation_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        finish_start = source.index("_finishAppletTextInsert: function(payload)")
        finish_end = source.index("\n  _ensureAutoRelistenPendingForDonePayload:", finish_start)
        finish_block = source[finish_start:finish_end]
        reserve_start = source.index("_reserveAutoInsertFingerprint: function(fingerprint)")
        reserve_end = source.index("\n  _rememberAutoInsertFingerprint:", reserve_start)
        reserve_block = source[reserve_start:reserve_end]
        remember_start = reserve_end + 3
        remember_end = source.index("\n  _forgetAutoInsertFingerprint:", remember_start)
        remember_block = source[remember_start:remember_end]

        self.assertIn("let reservation = this._reserveAutoInsertFingerprint(insertFingerprint);", finish_block)
        self.assertIn("if (reservation === null)", finish_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not prepare transcript insertion")', finish_block)
        self.assertIn("try {", reserve_block)
        self.assertIn("if (!this._rememberAutoInsertFingerprint(fingerprint))", reserve_block)
        self.assertIn("return null;", reserve_block)
        self.assertIn('this._recordLifecycleError("auto-insert-fingerprint", error);', reserve_block)
        self.assertIn("let previousFingerprint = this.autoInsertFingerprint;", remember_block)
        self.assertIn("this.autoInsertFingerprints.push(fingerprint);", remember_block)
        self.assertIn('throw new Error("Auto-insert fingerprint could not be remembered");', remember_block)
        self.assertIn("this.autoInsertFingerprints.shift();", remember_block)
        self.assertIn("let previousLength = this.autoInsertFingerprints.length;", remember_block)
        self.assertIn('throw new Error("Auto-insert fingerprint history could not be bounded");', remember_block)
        self.assertIn("this.autoInsertFingerprint = previousFingerprint;", remember_block)
        self.assertIn('this._recordLifecycleError("auto-insert-fingerprint-rollback", rollbackError);', remember_block)
        self.assertIn('this._recordLifecycleError("auto-insert-fingerprint", error);', remember_block)
        self.assertIn("return false;", remember_block)

    def test_successful_relisten_restart_skips_done_status(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        silent_index = source.index("_finishSilentRelistenSkip: function(payload)")
        silent_done_index = source.index('this._setStatus("done", this._payloadMessage(payload, _("Silent recording skipped")', silent_index)
        empty_index = source.index("_finishEmptyRelistenDone: function(payload)")
        empty_done_index = source.index('this._setStatus("done", this._payloadMessage(payload, _("Recording finished without transcript")', empty_index)

        self.assertIn("this._ensureAutoRelistenPendingForDonePayload(payload);", source[silent_index:silent_done_index])
        self.assertIn("this._ensureAutoRelistenPendingForDonePayload(payload);", source[empty_index:empty_done_index])
        self.assertIn("if (this._finishPendingRelisten()) {\n      return;\n    }", source[silent_index:silent_done_index])
        self.assertIn("if (this._finishPendingRelisten()) {\n      return;\n    }", source[empty_index:empty_done_index])

    def test_done_payload_can_start_relisten_without_recorded_polling(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        finish_index = source.index("_finishAppletTextInsert: function(payload)")
        ensure_index = source.index("this._ensureAutoRelistenPendingForDonePayload(payload);", finish_index)
        insert_index = source.index("this._insertTranscriptText(transcript,", finish_index)
        pending_index = source.index("_ensureAutoRelistenPendingForDonePayload: function(payload)")
        pending_end = source.index("_finishPendingRelisten: function()", pending_index)
        finish_pending_index = pending_end
        finish_pending_end = source.index("_transcriptDigest: function", finish_pending_index)

        self.assertLess(ensure_index, insert_index)
        self.assertIn("if (this.autoRelistenPending)", source[pending_index:pending_end])
        self.assertIn("if (!this.autoRelisten || !this.notificationSessionActive)", source[pending_index:pending_end])
        self.assertIn('let payloadLanguage = payload && typeof payload.language === "string"', source[pending_index:pending_end])
        self.assertIn("LANGUAGE_CODES.indexOf(payloadLanguage) < 0", source[pending_index:pending_end])
        self.assertIn('this.autoRelistenPendingToken = String(this.autoRelistenSequence) + ":done:" + marker;', source[pending_index:pending_end])
        self.assertIn("this.autoRelistenPendingLanguage = payloadLanguage;", source[pending_index:pending_end])
        self.assertIn("let previousNotificationSessionActive = this.notificationSessionActive;", source[finish_pending_index:finish_pending_end])
        self.assertIn("this.notificationSessionActive = true;\n      relistenStarted = this._restartRelistenRecording();", source[finish_pending_index:finish_pending_end])
        self.assertIn("this.autoRelistenManualStopRequested = false;", source[finish_pending_index:finish_pending_end])
        self.assertIn("this.notificationSessionActive = previousNotificationSessionActive;", source[finish_pending_index:finish_pending_end])
        self.assertIn("let relistenFailedWithError = false;", source[finish_pending_index:finish_pending_end])
        self.assertIn('relistenFailedWithError = !relistenStarted && this.status === "error";', source[finish_pending_index:finish_pending_end])
        self.assertIn("if (relistenFailedWithError) {", source[finish_pending_index:finish_pending_end])

    def test_manual_toggle_suppresses_next_auto_relisten_restart(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        toggle_index = source.index("_toggleRecording: function()")
        toggle_end = source.index("_restartApplet: function()", toggle_index)
        cancel_index = source.index("_cancelRecording: function(statusOverride)")
        cancel_end = source.index("_runDoctor: function", cancel_index)
        ensure_index = source.index("_ensureAutoRelistenPendingForDonePayload: function(payload)")
        ensure_end = source.index("_finishPendingRelisten: function()", ensure_index)

        self.assertIn("let manualRelistenStopRequested = Boolean(", source[toggle_index:toggle_end])
        self.assertIn('this.status === "recording" || this.status === "recorded" || this.autoRelistenPending', source[toggle_index:toggle_end])
        self.assertIn("if (this.autoRelisten && this.notificationSessionActive && this._recordingCommandToken)", source[toggle_index:toggle_end])
        self.assertNotIn("if (this.autoRelisten && this.notificationSessionActive) {", source[toggle_index:toggle_end])
        self.assertIn("this.autoRelistenManualStopRequested = manualRelistenStopRequested;", source[toggle_index:toggle_end])
        self.assertIn('this._setStatus("processing", _("Stopping Auto Relisten..."), this.lastTranscript);', source[toggle_index:toggle_end])
        self.assertIn("if (this.isCommandRunning) {", source[cancel_index:cancel_end])
        self.assertIn("this.cancelPendingWhileCommandRunning = true;", source[cancel_index:cancel_end])
        self.assertIn("this.autoRelistenManualStopRequested = true;", source[cancel_index:cancel_end])
        self.assertIn('this._setStatus("processing", _("Stopping Auto Relisten..."), this.lastTranscript);', source[cancel_index:cancel_end])
        self.assertIn("_hasCancelableRecordingWork: function(statusOverride)", source)
        cancel_work_start = source.index("_hasCancelableRecordingWork: function(statusOverride)")
        cancel_work_end = source.index("\n  _cancelRecording:", cancel_work_start)
        cancel_work_block = source[cancel_work_start:cancel_work_end]
        self.assertIn('effectiveStatus === "recording" || effectiveStatus === "recorded"', cancel_work_block)
        self.assertIn("this.autoRelistenPending", cancel_work_block)
        self.assertIn("this.isCommandRunning && this.notificationSessionActive && Boolean(this._recordingCommandToken)", cancel_work_block)
        self.assertNotIn("this.isCommandRunning && this.notificationSessionActive);", cancel_work_block)
        self.assertNotIn("return this.notificationSessionActive ||", cancel_work_block)
        self.assertIn("if (!this._hasCancelableRecordingWork(statusOverride))", source[cancel_index:cancel_end])
        self.assertIn("if (this.autoRelistenManualStopRequested) {\n      return;\n    }", source[ensure_index:ensure_end])

    def test_cancel_menu_uses_same_work_predicate_as_cancel_action(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        status_start = source.index("_setStatus: function(status, message, transcript)")
        status_end = source.index("\n  _maybeNotify:", status_start)
        status_block = source[status_start:status_end]
        work_start = source.index("_hasCancelableRecordingWork: function(statusOverride)")
        work_end = source.index("\n  _cancelRecording:", work_start)
        work_block = source[work_start:work_end]
        cancel_start = source.index("_cancelRecording: function(statusOverride)")
        cancel_end = source.index("\n  _runDoctor:", cancel_start)
        cancel_block = source[cancel_start:cancel_end]

        self.assertIn("this._setMenuItemSensitiveSafely(this.cancelItem, this._hasCancelableRecordingWork());", status_block)
        self.assertIn("if (!this._hasCancelableRecordingWork(statusOverride))", cancel_block)
        self.assertIn("if (!this.isCommandRunning && this.autoRelistenPending && this.textInsertToken)", cancel_block)
        self.assertIn("if (!this._cancelTextInsertForSettingsChange())", cancel_block)
        self.assertIn('this._setStatus("ready", _("Auto Relisten cancelled"), this.lastTranscript);', cancel_block)
        self.assertIn("let effectiveStatus = typeof statusOverride === \"string\" ? statusOverride : this.status;", work_block)
        self.assertIn('(effectiveStatus === "error" && this.recordingArtifactsPresent && !localTextInsertOwnsRecording)', work_block)
        self.assertIn('(effectiveStatus === "processing" && this.recordingArtifactsPresent && !localWorkflowOwnsProcessing)', work_block)

        apply_start = source.index("_applyPayload: function(payload, statusRefreshToken)")
        apply_end = source.index("\n  _artifactEncryptionWarningKey:", apply_start)
        self.assertIn("this._updateRecordingArtifactState(payload, status);", source[apply_start:apply_end])
        artifact_start = source.index("_updateRecordingArtifactState: function(payload, status)")
        artifact_end = source.index("\n  _cancelRecording:", artifact_start)
        artifact_block = source[artifact_start:artifact_end]
        self.assertIn('"audio_path_present"', artifact_block)
        self.assertIn('"process_identity_present"', artifact_block)
        self.assertIn("payload.audio_deleted === false", artifact_block)
        self.assertIn("let cleanupFailurePresent = payload.audio_deleted === false ||", artifact_block)
        self.assertIn("this.recordingArtifactsPresent = cleanupFailurePresent;", artifact_block)
        self.assertIn('if (status === "idle" || status === "done")', artifact_block)
        self.assertLess(
            artifact_block.index('if (status === "idle" || status === "done")'),
            artifact_block.index("this.recordingArtifactsPresent = cleanupFailurePresent;")
        )

        preserving_start = source.index("_setStatusPreservingRecording: function(status, message, transcript)")
        preserving_end = source.index("\n  _setStatus: function", preserving_start)
        self.assertIn("this._setMenuItemSensitiveSafely(this.cancelItem, this._hasCancelableRecordingWork());", source[preserving_start:preserving_end])
        self.assertIn("this.recordingArtifactsPresent = true;", source[apply_start:apply_end])

        active_start = source.index("_hasActiveRecordingState: function()")
        active_end = source.index("\n  _setActiveLanguage:", active_start)
        active_block = source[active_start:active_end]
        self.assertIn('(this.status === "error" && this.recordingArtifactsPresent)', active_block)

    def test_cancel_prepares_arguments_before_setting_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_cancelRecording: function(statusOverride)")
        end = source.index("\n  _runDoctor:", start)
        block = source[start:end]
        self.assertIn("let cancelArgs;", block)
        self.assertIn("cancelArgs = this._cancelArgs();", block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not prepare cancellation command: ")', block)
        self.assertIn("this._spawnJson(cancelArgs,", block)
        self.assertLess(block.index("cancelArgs = this._cancelArgs();"), block.index("this.isCommandRunning = true;"))

    def test_async_keyboard_insert_reports_menu_close_failure(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_copyAndMaybePasteTranscriptText: function(")
        end = source.index("\n  _confirmClipboardOverwriteForPaste:", start)
        block = source[start:end]
        failure = block.index('if (!this._closeMenuForKeyboardInsert())')
        failure_end = block.index("return false;", failure)

        self.assertIn('this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);', block[failure:failure_end])
        self.assertIn("completeOnce(false);", block[failure:failure_end])
        self.assertIn("let completionFinished = false;", block)
        self.assertIn("if (completionFinished)", block)
        self.assertIn("completionFinished = true;", block)

    def test_x11_and_clipboard_wrappers_complete_when_subprocess_does_not_start(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, error_group in [
            ("_xdotoolOutput: function(", "\n  _xWindowLooksLikeSpeedOfCinnamon:", "x11-command"),
            ("_clipboardTargetList: function(", "\n  _clipboardNonTextPayloadTargets:", "clipboard-command"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("let completed = false;", block)
            if method == "_clipboardTargetList: function(":
                self.assertIn("let completeOnce = (value, resolvedProgram) =>", block)
                self.assertIn("complete(value, resolvedProgram);", block)
            else:
                self.assertIn("let completeOnce = (value) =>", block)
            if method == "_clipboardTargetList: function(":
                self.assertIn("let subprocessCallbackDelivered = false;", block)
                self.assertIn("let fallbackStarted = false;", block)
                self.assertIn("subprocessCallbackDelivered = true;", block)
                self.assertIn("if (!handle && !subprocessCallbackDelivered)", block)
                self.assertIn("return Boolean(handle) || fallbackStarted;", block)
            else:
                self.assertIn("if (!handle) {\n        completeOnce(null);\n      }", block)
            self.assertIn(f'this._recordLifecycleError("{error_group}", error);', block)
            self.assertIn("completeOnce(null);", block)

    def test_json_subprocess_wrapper_reports_missing_process_handles_once(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_spawnJsonWithBackendEnvironment: function(")
        end = source.index("\n  _isStatusCommandArgs:", start)
        block = source[start:end]

        self.assertIn("let completed = false;", block)
        self.assertIn("let completeOnce = (stdout, result, stderr) =>", block)
        self.assertIn("let handle = this._runBoundedSubprocess(", block)
        self.assertIn('if (!handle) {\n      completeOnce("", { error: "Subprocess could not be started", startupFailed: true }, "");\n    }', block)
        self.assertIn("return handle;", block)

    def test_cancel_pending_during_command_suppresses_done_transcript_insert(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        apply_index = source.index("_applyPayload: function(payload, statusRefreshToken)")
        apply_end = source.index("_applyMicrophoneLevel: function", apply_index)
        block = source[apply_index:apply_end]

        cancel_done_index = block.index('if (this.cancelPendingWhileCommandRunning && status === "done")')
        finish_insert_index = block.index('if (status === "done" && hasTranscript)')
        self.assertLess(cancel_done_index, finish_insert_index)
        self.assertIn('this._setStatus("ready", _("Cancel applied; transcript not inserted"), this.lastTranscript);', block)
        self.assertIn("this.cancelPendingWhileCommandRunning = false;", block)
        self.assertIn("this._cancelRecording(status);", block)

    def test_auto_transcribe_cancel_race_consumes_expected_stop_response(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_maybeAutoTranscribeRecorded: function(payload, statusOverride)")
        end = source.index("\n  _clearStatusTimer:", start)
        block = source[start:end]

        mismatch = block.index("if (relistenToken && this.autoRelistenPendingToken !== relistenToken)")
        mismatch_end = block.index("return;", mismatch)
        self.assertIn("this.isCommandRunning = false;", block[mismatch:mismatch_end])
        self.assertIn("if (this.cancelPendingWhileCommandRunning)", block[mismatch:mismatch_end])
        self.assertIn("this._applyPayloadSafely(nextPayload, undefined, true);", block[mismatch:mismatch_end])
        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        self.assertIn("this.cancelPendingWhileCommandRunning = false;", source[toggle_start:toggle_end])

    def test_failed_insert_stops_auto_relisten_restart(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        finish_index = source.index("_finishAppletTextInsert: function(payload)")
        finish_end = source.index("_ensureAutoRelistenPendingForDonePayload: function(payload)", finish_index)
        finish_block = source[finish_index:finish_end]

        self.assertIn("this.autoRelistenPending = false;", finish_block)
        self.assertIn('this.autoRelistenPendingToken = "";', finish_block)
        self.assertIn("this.autoRelistenManualStopRequested = true;", finish_block)
        failed_insert_index = finish_block.index("if (!inserted) {")
        failed_insert_return_index = finish_block.index("return;", failed_insert_index)
        final_relisten_index = finish_block.rindex("this._finishPendingRelisten();")
        self.assertLess(failed_insert_return_index, final_relisten_index)

    def test_manual_relisten_stop_finishes_recording_that_started_while_command_was_running(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        apply_index = source.index("_applyPayload: function(payload, statusRefreshToken)")
        apply_end = source.index("_applyMicrophoneLevel: function", apply_index)

        self.assertIn('(status === "recording" || status === "recorded")', source[apply_index:apply_end])
        self.assertIn("this.autoRelistenManualStopRequested &&", source[apply_index:apply_end])
        self.assertIn("this._toggleRecording();", source[apply_index:apply_end])
        self.assertIn("if (!this.isCommandRunning && !this.autoRelistenManualStopRequested) {", source[apply_index:apply_end])

    def test_apply_payload_does_not_clear_manual_relisten_stop_for_payload_error(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        apply_index = source.index("_applyPayload: function(payload, statusRefreshToken) {")
        error_index = source.index('if (payload.error || status === "error") {', apply_index)
        error_end = source.index("let hasTranscript", error_index)
        error_block = source[error_index:error_end]

        self.assertIn('if (payload.error || status === "error") {', error_block)
        self.assertIn('let errorMessage = this._payloadErrorMessage(payload, _("Backend reported an error"));', error_block)
        self.assertIn('this.autoTranscribeRecordingKey = "";', error_block)
        self.assertNotIn("this.autoRelistenManualStopRequested = false;", error_block)

    def test_relisten_restart_clears_pending_only_after_restart_resolution(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        finish_index = source.index("_finishPendingRelisten: function()")
        return_index = source.index("return relistenStarted;", finish_index)
        block = source[finish_index:return_index]
        started_index = block.index("if (relistenStarted) {")
        failed_index = block.index("} else if (shouldRelisten) {", started_index)
        started_block = block[started_index:failed_index]
        restart_index = source.index("_restartRelistenRecording: function()")
        restart_end = source.index("_preparedTranscriptText: function", restart_index)
        restart_block = source[restart_index:restart_end]

        self.assertNotIn("this.autoRelistenPending = false;", started_block)
        self.assertNotIn('this.autoRelistenPendingToken = "";', started_block)
        self.assertIn("} else if (shouldRelisten) {\n      this.autoRelistenPending = false;", block)
        self.assertIn('this.autoRelistenPendingToken = "";', block)
        self.assertNotIn("} else if (!shouldRelisten) {", block)
        self.assertIn("if (payload.error) {\n          this.autoRelistenPending = false;", restart_block)
        self.assertIn("this._applyPayloadSafely(payload, undefined, true);", restart_block)
        self.assertIn("let voiceModelCompatible = this.autoRelistenPendingLanguage", restart_block)
        self.assertIn('this._ensureVoiceModelCompatibleForLanguage(relistenLanguage, true, _("relisten language"))', restart_block)
        self.assertIn('startArgs = this._baseArgs("start", relistenLanguage);', restart_block)
        self.assertIn('this.autoRelistenPendingLanguage = "";', restart_block)
        self.assertIn('nextStatus === "recording" || nextStatus === "recorded"', restart_block)
        self.assertIn('this.autoRelistenPendingToken = "";', restart_block)
        self.assertIn("let startHandle = this._spawnJson(startArgs,", restart_block)
        self.assertIn("if (!startHandle) {\n      if (this._recordingCommandToken === recordingCommandToken) {", restart_block)
        self.assertIn("this.isCommandRunning = false;", restart_block)
        self.assertIn('this._setStatus("error", _("Could not start next recording")', restart_block)
        apply_index = restart_block.index("this._applyPayloadSafely(payload);")
        self.assertNotIn("this.autoRelistenManualStopRequested = false;", restart_block[:apply_index])

    def test_relisten_restart_does_not_compete_with_local_processing_workflow(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_restartRelistenRecording: function()")
        end = source.index("\n  _preparedTranscriptText: function", start)
        block = source[start:end]
        self.assertIn("this._hasLocalProcessingWorkflow()", block)
        self.assertIn("this.textInsertToken", block)
        self.assertIn("return false;", block)

    def test_silent_and_empty_done_keep_relisten_start_errors(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_finishSilentRelistenSkip: function(payload)", "\n  _finishEmptyRelistenDone:"),
            ("_finishEmptyRelistenDone: function(payload)", "\n  _insertTranscriptText:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("let hadPendingRelisten = this.autoRelistenPending;", block)
            self.assertIn("if (this._finishPendingRelisten())", block)
            self.assertIn('if (hadPendingRelisten && this.status === "error")', block)
            self.assertLess(block.index("this._ensureAutoRelistenPendingForDonePayload(payload);"), block.index("let hadPendingRelisten = this.autoRelistenPending;"))
            self.assertLess(block.index('if (hadPendingRelisten && this.status === "error")'), block.index('this._setStatus("done"'))

    def test_applet_uses_gio_for_desktop_links_and_folders(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const Gio = imports.gi.Gio;", source)
        self.assertIn("Gio.AppInfo.launch_default_for_uri(uri, null)", source)
        self.assertIn("let opened = Gio.AppInfo.launch_default_for_uri(uri, null);", source)
        self.assertIn('throw new Error("URI could not be opened");', source)
        self.assertIn("GLib.filename_to_uri(path, null)", source)
        self.assertIn("GLib.mkdir_with_parents(path, 0o755)", source)
        self.assertIn("if (mkdirResult !== 0)", source)
        self.assertIn('throw new Error("folder could not be created");', source)
        self.assertIn('throw new Error("External API config directory could not be created");', source)
        self.assertIn('query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null)', source)
        self.assertIn("info.get_file_type() !== Gio.FileType.DIRECTORY", source)
        self.assertIn('query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null)', source)
        self.assertIn("info.get_file_type() !== Gio.FileType.REGULAR", source)
        self.assertIn('this._openUri(RUNBOOK_URL, _("Opened setup guide"))', source)
        self.assertIn('this._openFolder(GLib.build_filenamev([GLib.get_user_state_dir(), "speed-of-cinnamon", "transcripts"])', source)
        self.assertIn('this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "whisper.cpp"])', source)
        self.assertNotIn('Util.spawn(["xdg-open"', source)

    def test_open_file_and_folder_errors_do_not_render_local_paths(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this._setStatusPreservingRecording("error", _("Could not open folder"), this.lastTranscript);', source)
        self.assertIn('setStatus("error", _("Could not open file"), this.lastTranscript);', source)
        self.assertNotIn('_("Could not open folder: ") + err.message', source)
        self.assertNotIn('_("Could not open file: ") + err.message', source)

    def test_open_file_rejects_directories_and_symlinks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_openFile: function(path, successMessage, preserveRecording)")
        end = source.index("\n  _failSetupDiagnosticsAction:", start)
        block = source[start:end]
        self.assertIn("let file = Gio.File.new_for_path(path);", block)
        self.assertIn('file.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null)', block)
        self.assertIn("info.get_file_type() !== Gio.FileType.REGULAR", block)
        self.assertIn("let setStatus = preserveRecording === false", block)
        self.assertIn("this._openUri(GLib.filename_to_uri(path, null), successMessage, preserveRecording);", block)
        self.assertNotIn("GLib.FileTest.EXISTS", block)

    def test_setup_file_open_clears_its_processing_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        setup_start = source.index("_openProfanityFilterList: function()")
        setup_end = source.index("\n  _copySetupPlan:", setup_start)
        setup_block = source[setup_start:setup_end]
        self.assertIn('this._setStatus("processing",', setup_block)
        self.assertIn('this._openFile(path, _("Opened profanity replacement list: ") + String(this._safePayloadCount(payload.entries)), false);', setup_block)

        uri_start = source.index("_openUri: function(uri, successMessage, preserveRecording)")
        uri_end = source.index("\n  _openFolder:", uri_start)
        uri_block = source[uri_start:uri_end]
        self.assertIn("let setStatus = preserveRecording === false", uri_block)
        self.assertIn('setStatus("ready", successMessage, this.lastTranscript);', uri_block)

    def test_open_folder_rejects_symlinked_directories(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_openFolder: function(path, successMessage)")
        end = source.index("\n  _openFile:", start)
        block = source[start:end]
        self.assertIn("let folder = Gio.File.new_for_path(path);", block)
        self.assertIn('folder.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null)', block)
        self.assertIn("info.get_file_type() !== Gio.FileType.DIRECTORY", block)

    def test_applet_copies_setup_commands_without_installing_packages(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy setup commands"), "utilities-terminal-symbolic"', source)
        self.assertIn("_setupCommandsText: function(payload)", source)
        self.assertIn("_copySetupCommands: function()", source)
        self.assertIn("let commands = payload.commands || [];", source)
        self.assertIn("if (!Array.isArray(commands))", source)
        self.assertIn('let text = typeof commands[i] === "string" ? commands[i].trim() : "";', source)
        self.assertIn("let seen = Object.create(null);", source)
        self.assertIn('let planText = typeof payload.text === "string" && payload.text.trim() !== ""', source)
        self.assertNotIn('String(commands[i] || "").trim()', source)
        self.assertNotIn('String(payload.text || JSON.stringify(payload, null, 2))', source)
        self.assertIn("lines.join(\"\\n\")", source)
        self.assertIn("this._setClipboardText(text)", source)
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
        self.assertEqual(schema["insert-method"]["default"], "clipboard-paste")
        self.assertIn('this.insertMethod = "clipboard-paste";', source)
        self.assertIn('return OUTPUT_METHODS.indexOf(value) >= 0 ? value : "none";', source)
        self.assertNotIn('return OUTPUT_METHODS.indexOf(value) >= 0 ? value : "clipboard-paste";', source)
        self.assertIn('_selectOutputMethod: function(method)', source)
        self.assertIn('this._commitSettingValue("insertMethod", "insert-method"', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "insert-method", "insertMethod", this._onOutputSettingsChanged, null)', source)
        self.assertIn('this._setMenuItemLabelSafely(this.outputMethodItem, _("Output: ") + this._outputMethodLabel(this._normalizeOutputMethod(this.insertMethod)))', source)
        self.assertIn('"--insert-method", "none"', source)
        self.assertNotIn("_usesCinnamonClipboard", source)

    def test_applet_exposes_quick_text_output_options(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn('this.textOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Text options"))', source)
        self.assertIn("_populateTextOptionsMenu: function()", source)
        self.assertIn("_toggleAppendSpace: function()", source)
        self.assertIn("_toggleSanitizeSpecialChars: function()", source)
        self.assertIn("_toggleSoftenProfanity: function()", source)
        self.assertIn('this._commitSettingValue("appendSpace", "append-space"', source)
        self.assertIn('this._commitSettingValue("sanitizeSpecialChars", "sanitize-special-chars"', source)
        self.assertIn('this._commitSettingValue("softenProfanity", "soften-profanity"', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "append-space", "appendSpace", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "sanitize-special-chars", "sanitizeSpecialChars", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "soften-profanity", "softenProfanity", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('_("Append trailing space")', source)
        self.assertIn('_("Replace accents before output")', source)
        self.assertIn('_("Replace profanity with harmless words")', source)
        self.assertIn('args.push("--soften-profanity")', source)
        self.assertIn("_profanityFilterDocumentArgs: function()", source)
        self.assertIn('return [this._cliCommand(), "profanity-filter-document", "--json"];', source)
        self.assertIn("_openProfanityFilterList: function()", source)
        self.assertIn('let path = typeof payload.path === "string" ? payload.path.trim() : "";', source)
        self.assertIn("this._openFile(path, _(\"Opened profanity replacement list: \") + String(this._safePayloadCount(payload.entries)), false);", source)
        self.assertIn("show-profanity-filter-list", schema["layout"]["output-section"]["keys"])
        self.assertEqual(schema["show-profanity-filter-list"]["type"], "button")
        self.assertEqual(schema["show-profanity-filter-list"]["description"], "Edit profanity replacement list")
        self.assertEqual(schema["show-profanity-filter-list"]["callback"], "_openProfanityFilterList")

    def test_applet_exposes_artifact_encryption_dropdown(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("artifact-encryption", schema["layout"]["output-section"]["keys"])
        self.assertNotIn("artifact-encryption-help", schema["layout"]["output-section"]["keys"])
        self.assertIn("artifact-encryption-help-keyring", schema["layout"]["output-section"]["keys"])
        self.assertIn("artifact-encryption-help-passphrase", schema["layout"]["output-section"]["keys"])
        self.assertIn("artifact-encryption-help-off", schema["layout"]["output-section"]["keys"])
        self.assertEqual(schema["artifact-encryption"]["default"], "keyring")
        self.assertEqual(
            set(schema["artifact-encryption"]["options"].values()),
            {"keyring", "passphrase", "off"},
        )
        self.assertIn("Keyring failure fails closed", schema["artifact-encryption"]["tooltip"])
        self.assertIn("instead of silently downgrading to passphrase mode", schema["artifact-encryption"]["tooltip"])
        self.assertIn("fails visibly", schema["openai-compatible-flex-processing"]["tooltip"])
        self.assertIn("instead of silently retrying without Flex", schema["openai-compatible-flex-processing"]["tooltip"])
        self.assertEqual(schema["artifact-encryption-help-keyring"]["type"], "label")
        self.assertEqual(schema["artifact-encryption-help-keyring"]["dependency"], "artifact-encryption=keyring")
        self.assertIn("fail closed", schema["artifact-encryption-help-keyring"]["description"])
        self.assertIn("Choose Passphrase explicitly", schema["artifact-encryption-help-keyring"]["description"])
        self.assertNotIn("generate the default key file", schema["artifact-encryption-help-keyring"]["description"])
        self.assertIn("critical warning popup", schema["artifact-encryption-help-keyring"]["description"])
        self.assertEqual(schema["artifact-encryption-help-passphrase"]["type"], "label")
        self.assertEqual(schema["artifact-encryption-help-passphrase"]["dependency"], "artifact-encryption=passphrase")
        self.assertIn("Weak key handling", schema["artifact-encryption-help-passphrase"]["description"])
        self.assertIn("critical warning popup", schema["artifact-encryption-help-passphrase"]["description"])
        self.assertEqual(schema["artifact-encryption-help-off"]["type"], "label")
        self.assertEqual(schema["artifact-encryption-help-off"]["dependency"], "artifact-encryption=off")
        self.assertIn("plaintext", schema["artifact-encryption-help-off"]["description"])
        self.assertIn('const DEFAULT_ARTIFACT_ENCRYPTION = "keyring";', source)
        self.assertIn('const ARTIFACT_ENCRYPTION_MODES = [', source)
        self.assertIn('this.artifactEncryption = DEFAULT_ARTIFACT_ENCRYPTION;', source)
        self.assertIn('this.lastArtifactEncryptionWarningKey = "";', source)
        self.assertIn('this.lastRejectedArtifactPassphraseWarningKey = "";', source)
        self.assertIn('["artifact-encryption", "artifactEncryption"]', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "artifact-encryption", "artifactEncryption", this._onTextOutputSettingsChanged, null)', source)
        self.assertIn('args.push("--artifact-encryption", this._normalizeArtifactEncryption(this.artifactEncryption));', source)
        self.assertIn('args.push("--confirm-plaintext-output");', source)
        self.assertIn("_normalizeArtifactEncryption: function(method)", source)
        self.assertIn("_isRejectedArtifactPassphraseError: function(message)", source)
        self.assertIn("_maybeWarnRejectedArtifactPassphrase: function(message)", source)
        self.assertIn("this._maybeWarnRejectedArtifactPassphrase(payload.error);", source)
        self.assertIn("_maybeWarnUnencryptedArtifactStorage: function(payload, statusOverride)", source)
        self.assertIn('let mode = this._normalizeArtifactEncryption(payload.artifact_encryption || this.artifactEncryption);', source)
        self.assertIn('let transcriptPath = typeof payload.transcript_path === "string" ? payload.transcript_path.trim() : "";', source)
        self.assertIn('let transcriptStoredPlaintext = transcriptPath !== "" && payload.transcript_encrypted === false;', source)
        self.assertIn("_payloadStringMarker: function(payload, keys, fallback)", source)
        self.assertIn('let marker = this._payloadStringMarker(payload, ["transcript_path", "audio_path", "audio", "stopped_at", "started_at"], "");', source)
        self.assertIn('let recordingStoredPlaintext = payload.recording_artifacts_kept === true && payload.recording_encrypted === false;', source)
        self.assertIn('this._notify(_("Speed of Cinnamon encryption warning"), message, true);', source)
        self.assertIn("this._maybeWarnUnencryptedArtifactStorage(payload, status);", source)
        self.assertIn('if (key === "artifact-encryption")', source)

    def test_dynamic_model_menus_guard_fast_expand_clicks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_canMutateMenu: function(item)", source)
        self.assertIn("this._lifecycleAllowsWork()", source)
        self.assertIn("let itemActor = item && item.actor;", source)
        self.assertIn("typeof itemActor.is_finalized !== \"function\" || !itemActor.is_finalized()", source)
        self.assertIn("typeof actor.is_finalized !== \"function\" || !actor.is_finalized()", source)
        self.assertIn('this._recordLifecycleError("menu-state", error);', source)
        self.assertIn('typeof menu.removeAll === "function"', source)
        self.assertIn('typeof menu.addMenuItem === "function"', source)
        self.assertIn("if (!this._canMutateMenu(this.modelItem))", source)
        self.assertIn("if (!this._canMutateMenu(this.textModelItem))", source)
        self.assertIn("this.modelMenuRefreshToken = refreshToken;", source)
        self.assertIn("this.textModelMenuRefreshToken = refreshToken;", source)
        self.assertIn("if (this.modelMenuRefreshToken !== refreshToken)", source)
        self.assertIn("if (this.textModelMenuRefreshToken !== refreshToken)", source)

    def test_applet_can_reinsert_last_transcript_with_current_output_mode(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this.insertLastItem = new PopupMenu.PopupIconMenuItem(_("Insert last transcript")', source)
        self.assertIn("this.insertLastItem.setSensitive(false)", source)
        self.assertIn("this._connectSafe(this.insertLastItem, \"activate\", () => this._insertLastTranscript())", source)
        self.assertIn("this._setMenuItemSensitiveSafely(this.insertLastItem, Boolean(this.lastTranscript));", source)
        self.assertIn("_insertLastTranscript: function()", source)
        insert_last_start = source.index("_insertLastTranscript: function()")
        insert_last_end = source.index("\n  _populateHistoryMenu:", insert_last_start)
        insert_last_block = source[insert_last_start:insert_last_end]
        self.assertIn("if (this._hasActiveRecordingState())", insert_last_block)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Finish the current recording before inserting another transcript")', insert_last_block)
        self.assertIn("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)", source)
        self.assertIn("_finishAppletTextInsert: function(payload)", source)
        self.assertIn("_finishPendingRelisten: function()", source)
        self.assertIn("let shouldRelisten = this.autoRelistenPending;", source)
        self.assertIn("let relistenStarted = false;", source)
        self.assertIn("if (shouldRelisten) {", source)
        self.assertIn("relistenStarted = this._restartRelistenRecording();", source)
        self.assertIn("this._insertTranscriptText(transcript,", source)
        self.assertIn("this._reserveAutoInsertFingerprint(insertFingerprint)", source)
        self.assertIn("if (payload.inserted === true) {", source)
        self.assertIn("if (relistenStarted) {", source)
        self.assertIn("this.autoRelistenPending = false;", source)
        self.assertIn('this.autoRelistenPendingToken = "";', source)
        self.assertIn("_finishSilentRelistenSkip: function(payload)", source)
        self.assertIn("_finishEmptyRelistenDone: function(payload)", source)
        self.assertIn('if (status === "done" && payload.silence_detected === true)', source)
        self.assertIn("this.notificationSessionActive = true;", source)
        self.assertIn('if (status === "done" && hasTranscript)', source)
        self.assertIn('this._payloadMessage(payload, _("Recording finished without transcript")', source)
        insert_start = source.index("_finishAppletTextInsert: function(payload)")
        insert_end = source.index("\n  _ensureAutoRelistenPendingForDonePayload:", insert_start)
        insert_block = source[insert_start:insert_end]
        self.assertIn("try {\n        result = this._insertTranscriptText", insert_block)
        self.assertIn("}, insertFingerprint);", insert_block)
        self.assertIn('this._recordLifecycleError("payload-insert", error);', insert_block)
        self.assertIn("releaseFingerprint();", insert_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not insert transcript")', insert_block)
        self.assertIn('if (method === "none")', source)
        self.assertIn('if (method === "type")', source)
        self.assertIn("this.textInsertToken = null;", source)
        insert_start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        insert_end = source.index("_restartRelistenRecording: function()", insert_start)
        insert_block = source[insert_start:insert_end]
        self.assertIn("if (!this._lifecycleAllowsWork())", insert_block)
        self.assertIn('if (method === "none") {', insert_block)
        self.assertLess(insert_block.index('if (method === "none") {'), insert_block.index("let autoPasteTarget ="))
        self.assertIn("if (this.textInsertCancellationFailed)", insert_block)
        self.assertIn("let hadInsertToken = Boolean(this.textInsertToken);", insert_block)
        self.assertIn("this.textInsertToken = null;", insert_block)
        self.assertIn("this.autoRelistenManualStopRequested = true;", insert_block)
        self.assertIn('this._dialogClose(this.clipboardOverwriteDialog, "clipboard-overwrite")', insert_block)
        self.assertIn("let timerCleanupStillPending = false;", insert_block)
        self.assertIn("let orphanCleanupSucceeded = this._retryOrphanedTimers();", insert_block)
        self.assertIn('Boolean(this.clipboardOverwriteDialog)', insert_block)
        self.assertIn("this.clipboardOverwriteDialog = null;", insert_block)
        self.assertIn("if (this.textInsertToken || this.clipboardOverwriteDialog)", insert_block)
        self.assertLess(
            insert_block.index("if (this.textInsertCancellationFailed)"),
            insert_block.index("if (this.textInsertToken || this.clipboardOverwriteDialog)")
        )
        self.assertLess(
            insert_block.index('this._dialogClose(this.clipboardOverwriteDialog, "clipboard-overwrite")'),
            insert_block.index("let timerCleanupStillPending = false;")
        )
        self.assertIn("this.textInsertToken = insertToken;", insert_block)
        self.assertIn("if (!isCurrentInsert())", insert_block)
        self.assertIn("let complete = (result) =>", insert_block)
        self.assertIn('this._recordLifecycleError("text-insert-completion", error);', insert_block)
        self.assertIn("if (!this._typeTextAfterFocus(text, (completed) => {", source)
        self.assertIn('if (typeCompleted && isCurrentInsert())', source)
        self.assertIn('if (!this._closeMenuForKeyboardInsert()) {', source)
        self.assertIn('this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);', source)
        self.assertIn('this._restoreTargetWindowForPaste((restored) => {', source)
        self.assertIn("_spawnKeyboardProcess: function(args, completionCallback)", source)
        self.assertIn('let xdotool;', source)
        self.assertIn('xdotool = this._findTrustedProgramInPath("xdotool");', source)
        self.assertIn('[xdotool, "type", "--clearmodifiers", "--delay", String(delay), "--", typedText]', source)
        self.assertIn("_isTerminalTargetWindow: function()", source)
        self.assertIn('let autoPasteTarget = method === "clipboard-paste" && this._windowTitleMatchesAutoPaste();', source)
        self.assertIn('let canPasteWithKeyboard = method === "clipboard-paste" &&', source)
        self.assertIn('(this._findTrustedProgramInPath("xdotool") || this._findTrustedProgramInPath("wtype"));', source)
        self.assertIn('let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;', source)
        self.assertIn('let terminalPaste = this._isTerminalTargetWindow();', source)
        self.assertIn('let hasXdotool;', source)
        self.assertIn('hasXdotool = this._findTrustedProgramInPath("xdotool");', source)
        self.assertIn('let hasWtype;', source)
        self.assertIn('hasWtype = this._findTrustedProgramInPath("wtype");', source)
        self.assertIn('if (!this._isUsableTargetWindow(this.targetWindow) && !this.targetWindowXClass && !this.targetWindowXTitle) {', source)
        self.assertIn('String(this.targetWindowXClass || "").toLowerCase()', source)
        self.assertIn('String(this.targetWindowXTitle || "").toLowerCase()', source)
        self.assertIn('if (hasXdotool) {', source)
        self.assertIn('let pasteKey = terminalPaste ? "ctrl+shift+v" : "ctrl+v";', source)
        self.assertIn('[hasXdotool, "key", "--clearmodifiers", pasteKey]', source)
        self.assertIn('} else if (hasWtype) {', source)
        self.assertIn('[hasWtype, "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]', source)
        self.assertIn('[hasWtype, "-M", "ctrl", "v", "-m", "ctrl"]', source)
        self.assertIn('if (sendEnter) {', source)
        self.assertIn('followUpArgs = [hasWtype, "-k", "Return"];', source)
        self.assertIn("let expectedTargetWindow = this._targetXWindowSnapshot();", source)
        self.assertIn('if (!expectedTargetWindow) {\n      this._setStatus("error", _("Target window unavailable for automatic paste"), this.lastTranscript);', source)
        self.assertIn("return this._spawnKeyboardAfterFocus(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, isCurrentOperation);", source)
        self.assertIn('this._setStatus("error", _("Target window unavailable for direct typing"), this.lastTranscript);', source)
        self.assertIn('[xdotool, "type", "--clearmodifiers", "--delay", String(delay), "--", typedText], null, null, expectedTargetWindow, completionCallback, isCurrentOperation)', source)
        self.assertIn('if (!isCurrentOperation() || !this._lifecycleAllowsWork()) {', source)
        self.assertIn("this._completeKeyboardInsertFailure(", source)
        self.assertIn('this._setStatus("error", _("Clipboard changed before automatic paste"), this.lastTranscript);', source)
        self.assertIn("return false;", source)
        self.assertIn("return true;", source)

    def test_applet_checks_clipboard_targets_before_overwriting_clipboard_for_auto_paste(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_clipboardProgramSpecs: function()", source)
        self.assertIn("_clipboardProgramSpec: function()", source)
        self.assertIn("_clipboardPayloadArgs: function(spec, targetName)", source)
        self.assertIn("_clipboardFallbackSpec: function(program, args, attemptedPrograms)", source)
        self.assertIn("_clipboardTargetList: function(program, args, completionCallback, timeoutMs, attemptedPrograms, deadlineMs)", source)
        self.assertIn('let timeout = this._findTrustedProgramInPath("timeout");', source)
        self.assertIn("let helper = this._findTrustedProgramInPath(program);", source)
        self.assertIn("if (!helper) {\n      if (tryFallback()) {\n        return true;\n      }\n      completeOnce(null);\n      return false;\n    }", source)
        self.assertIn("if (!handle && !subprocessCallbackDelivered)", source)
        self.assertIn("return Boolean(handle) || fallbackStarted;", source)
        self.assertIn('let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), helper];', source)
        self.assertIn("_clipboardNonTextPayloadTargets: function(targets)", source)
        self.assertIn("_clipboardPayloadSnapshot: function()", source)
        self.assertIn("_clipboardPayloadSnapshotAsync: function(completionCallback)", source)
        self.assertIn("_clipboardPayloadFingerprintFromTargetsAsync: function(spec, targets, completionCallback, deadlineMs)", source)
        target_list_start = source.index("_clipboardTargetList: function(program, args, completionCallback, timeoutMs, attemptedPrograms, deadlineMs)")
        target_list_end = source.index("\n  _clipboardNonTextPayloadTargets:", target_list_start)
        target_list_block = source[target_list_start:target_list_end]
        self.assertIn("let completeOnce = (value, resolvedProgram) =>", target_list_block)
        self.assertIn("complete(value, resolvedProgram);", target_list_block)
        self.assertIn('completeOnce(String(stdout || ""), program);', target_list_block)
        self.assertIn("const CLIPBOARD_COMMAND_TIMEOUT_MS = 1500;", source)
        self.assertIn("const CLIPBOARD_MAX_TARGETS = 16;", source)
        self.assertIn("maxStdoutBytes: MAX_CLIPBOARD_TARGET_OUTPUT_BYTES", source)
        self.assertIn("minimumTimeoutMs: 1", source)
        self.assertIn('resourceGroup: "clipboard"', source)
        self.assertIn("if (result && result.cancelled) {", source)
        self.assertIn("let remainingMs = commandDeadlineMs - Date.now();", source)
        self.assertIn("this._clipboardTargetList(\n          fallback.program,", source)
        snapshot_start = source.index("_clipboardPayloadSnapshotAsync: function(completionCallback)")
        snapshot_end = source.index("\n  _clipboardPayloadFingerprintFromTargetsAsync:", snapshot_start)
        snapshot_block = source[snapshot_start:snapshot_end]
        self.assertIn("(targets, resolvedProgram) =>", snapshot_block)
        self.assertIn("let resolvedSpec = spec;", snapshot_block)
        self.assertIn("this._clipboardPayloadFingerprintFromTargetsAsync(resolvedSpec, targetText", snapshot_block)
        self.assertIn("let nonTextTargets = [];", source)
        self.assertIn("_clipboardPayloadDescriptionFromTargets: function(targets)", source)
        self.assertIn("nonTextTargets.slice(0, 6).join(\", \")", source)
        self.assertIn("this._shortMenuText(description, 160)", source)
        self.assertIn("_copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)", source)
        copy_index = source.index("_copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)")
        copy_end = source.index("_confirmClipboardOverwriteForPaste: function", copy_index)
        copy_body = source[copy_index:copy_end]
        restore_index = copy_body.index("this._restoreTargetWindowForPaste((restored) => {")
        guarded_clipboard_index = copy_body.index("this._setClipboardText(text)", restore_index)
        self.assertIn('this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);', copy_body)
        self.assertIn(
            'this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);',
            copy_body,
        )
        self.assertLess(restore_index, guarded_clipboard_index)
        self.assertIn('  _describeNonTextClipboardPayload: function(completionCallback) {', source)
        self.assertIn('_confirmClipboardOverwriteForPaste: function(clipboardSnapshot, transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)', source)
        self.assertIn('if (method === "clipboard-paste" && !canPasteWithKeyboard) {', source)
        self.assertIn('this._setStatus("error", _("Clipboard-paste requires a keyboard helper (xdotool or wtype)"), transcript);', source)
        self.assertIn('this._clipboardPayloadSnapshotAsync((clipboardSnapshot) => {', source)
        self.assertIn('if (clipboardSnapshot.hasNonTextPayload) {', source)
        self.assertIn('this._confirmClipboardOverwriteForPaste(', source)
        self.assertIn('_("Clipboard contains non-text payload (%s).").replace("%s", String(nonTextDescription || _("unknown")))', source)
        self.assertIn('_("Clipboard overwrite cancelled"), transcript);', source)
        self.assertIn('_("Replace clipboard content and continue paste?"),', source)
        self.assertIn('_("Overwrite clipboard")', source)
        self.assertNotIn("skippig", source)
        self.assertNotIn("speef", source.lower())
        self.assertIn('targetArgs: ["-selection", "clipboard", "-t", "TARGETS", "-out"]', source)
        self.assertIn('targetArgs: ["--clipboard", "--output", "--target", "TARGETS"]', source)
        self.assertIn('targetArgs: ["--list-types"]', source)
        self.assertIn('complete(this._clipboardUnknownPayloadSnapshot());', source)
        self.assertIn('Copied to clipboard; automatic paste command could not be started', source)

    def test_applet_blocks_auto_paste_when_clipboard_targets_unknown_or_empty(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('if (targets === null || targets === undefined) {\n            unknown();', source)
        self.assertIn('if (targetLines.length > CLIPBOARD_MAX_TARGETS) {\n            unknown();', source)
        self.assertIn('if (payloadFingerprint === "unknown") {\n                unknown();', source)
        self.assertIn('let originalPayloadFingerprint = clipboardSnapshot && clipboardSnapshot.payloadFingerprint ? clipboardSnapshot.payloadFingerprint : "unknown";', source)
        self.assertIn('if (originalClipboardSignature === "unknown" || originalPayloadFingerprint === "unknown") {', source)
        self.assertIn('this._setStatus("ready", _("Clipboard state unavailable; overwrite cancelled"), transcript);', source)
        self.assertIn('this._clipboardPayloadSnapshotAsync((clipboardSnapshot) => {', source)

    def test_applet_prompts_before_overwriting_non_text_clipboard_payload(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_describeNonTextClipboardPayload: function(completionCallback) {', source)
        self.assertIn('this._setStatus("ready", _("Clipboard overwrite cancelled"), transcript);', source)
        self.assertIn('this._setStatus("ready", _("Clipboard changed; overwrite cancelled"), transcript);', source)
        self.assertIn('_("Clipboard contains non-text payload (%s).").replace("%s", String(nonTextDescription || _("unknown")))', source)
        self.assertIn('_("Replace clipboard content and continue paste?"),', source)
        self.assertIn("this._dialogAddChild(dialog, this._newSafeLabel", source)
        self.assertIn("_newSafeLabel: function(text, options, group)", source)
        self.assertIn("if (!dialog || !child || !dialog.contentLayout", source)
        self.assertIn('this._dialogSetButtons(dialog, [', source)
        self.assertIn('safeButton.action = this._guardStateCallback("dialog-" + String(group || "action"), button.action, undefined);', source)
        self.assertNotIn('safeButton.action = this._guardCallback("dialog-" + String(group || "action"), button.action, undefined);', source)
        self.assertIn('key: Clutter.KEY_Escape,', source)
        self.assertIn('let completed = false;', source)
        self.assertIn('let originalClipboardSignature = clipboardSnapshot && clipboardSnapshot.signature ? clipboardSnapshot.signature : "unknown";', source)
        self.assertIn('let originalPayloadFingerprint = clipboardSnapshot && clipboardSnapshot.payloadFingerprint ? clipboardSnapshot.payloadFingerprint : "unknown";', source)
        self.assertIn('if (originalClipboardSignature === "unknown" || originalPayloadFingerprint === "unknown") {', source)
        self.assertIn('this._setStatus("ready", _("Clipboard state unavailable; overwrite cancelled"), transcript);', source)
        self.assertIn('this._clipboardPayloadSnapshotAsync((currentClipboardSnapshot) => {', source)
        self.assertIn('if (!this._clipboardPayloadSignaturesMatch(clipboardSnapshot, currentClipboardSnapshot)) {', source)
        self.assertIn('if (completed) {\n        return;\n      }', source)
        self.assertIn('completed = true;', source)
        self.assertIn('complete(false);', source)
        self.assertNotIn('this.clipboard.set_text(St.ClipboardType.CLIPBOARD, "");', source)
        self.assertIn("this._setClipboardText(text)", source)
        self.assertIn('this._setClipboardOverwriteApproval(currentClipboardSnapshot);', source)
        self.assertIn("let result = this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, complete, operationGuard);", source)
        self.assertIn('if (!this._dialogOpen(dialog, "clipboard-overwrite")) {', source)
        self.assertIn('this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);', source)
        self.assertIn("if (result === null) {\n        return;\n      }", source)

    def test_applet_tracks_explicit_clipboard_overwrite_approval_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const CLIPBOARD_OVERWRITE_APPROVAL_TTL_MS = 5000;", source)
        self.assertIn("this._clipboardOverwriteApproval = null;", source)
        self.assertIn("_setClipboardOverwriteApproval: function(snapshot) {", source)
        self.assertIn("_hasValidClipboardOverwriteApproval: function(snapshot) {", source)
        self.assertIn("_clearClipboardOverwriteApproval: function() {", source)
        self.assertIn("if (this._hasValidClipboardOverwriteApproval(clipboardSnapshot)) {", source)
        self.assertIn("this._clearClipboardOverwriteApproval();", source)

    def test_clipboard_overwrite_approval_rejects_empty_or_invalid_fingerprints(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_setClipboardOverwriteApproval: function(snapshot)")
        end = source.index("\n  _setClipboardText:", start)
        block = source[start:end]
        self.assertIn('let signature = snapshot && typeof snapshot.signature === "string" ? snapshot.signature.trim() : "";', block)
        self.assertIn('let payloadFingerprint = snapshot && typeof snapshot.payloadFingerprint === "string" ? snapshot.payloadFingerprint.trim() : "";', block)
        self.assertIn('signature === "" || payloadFingerprint === ""', block)
        self.assertIn("let expiresAtMs = Number(approval.expiresAtMs);", block)
        self.assertIn("!isFinite(expiresAtMs)", block)
        self.assertIn("approvalSignature === \"\" || approvalPayloadFingerprint === \"\"", block)

    def test_applet_tracks_non_text_payload_fingerprint_beyond_targets(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_clipboardPayloadFingerprintFromTargetsAsync: function(spec, targets, completionCallback, deadlineMs)", source)
        self.assertIn("_clipboardPayloadFingerprintFromText: function(payload, targetLabel)", source)
        self.assertIn("_clipboardPayloadSignaturesMatch: function(snapshotA, snapshotB)", source)
        self.assertIn("this._clipboardPayloadFingerprintFromTargetsAsync(resolvedSpec, targetText", source)
        self.assertIn("payloadFingerprint: \"unknown\",", source)
        self.assertIn('if (snapshotA.payloadFingerprint === "unknown" || snapshotB.payloadFingerprint === "unknown") {', source)
        self.assertIn("let sortedTargets;", source)
        self.assertIn("sortedTargets = nonTextTargets.slice().sort().slice(0, CLIPBOARD_MAX_TARGETS);", source)
        self.assertIn("let readNext = (index) => {", source)
        self.assertIn('complete(fingerprints.join("|"));', source)
        self.assertIn('if (fingerprint === "unknown") {\n              complete("unknown");', source)
        self.assertNotIn("let sampleTarget = String(nonTextTargets[0]);", source)
        self.assertIn('let data = String(payload || "");', source)
        self.assertIn('if (typeof digest !== "string" || digest.trim() === "") {\n        return "unknown";', source)
        self.assertIn("GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, data, -1)", source)
        self.assertIn(
            'return String(targetLabel || "") + ":sha256:" + digest;',
            source,
        )
        self.assertNotIn(':unavailable";', source)
        self.assertNotIn("step = Math.max(1", source)
        self.assertNotIn("rollingHash = ((rollingHash * 31) + data[i]) >>> 0;", source)

    def test_applet_rechecks_clipboard_text_before_keyboard_spawn(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn(
            "_spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback, operationGuard)",
            source,
        )
        self.assertIn(
            "_spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback, operationGuard)",
            source,
        )
        self.assertIn('if (expectedClipboardText !== undefined && expectedClipboardText !== null) {', source)
        self.assertIn('String(clipboardText || "") !== expected', source)
        self.assertIn(
            'this._setStatus("error", _("Clipboard changed before automatic paste"), this.lastTranscript);',
            source,
        )

    def test_applet_prevents_false_success_when_automatic_paste_could_not_start(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        fn_index = source.index(
            "_copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)"
        )
        confirm_index = source.index(
            "_confirmClipboardOverwriteForPaste: function(clipboardSnapshot, transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)",
            fn_index,
        )
        fn_body = source[fn_index:confirm_index]

        close_menu_index = fn_body.index("if (!this._closeMenuForKeyboardInsert()) {")
        restore_index = fn_body.index("this._restoreTargetWindowForPaste((restored) => {")
        paste_command_index = fn_body.index('if (!this._pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {')

        self.assertNotIn("this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);", fn_body)
        self.assertIn("this._setClipboardText(text)", fn_body[restore_index:paste_command_index])
        self.assertIn(
            'if (!restored) {\n          if (this._setClipboardText(text)) {\n            this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);\n          } else {\n            this._setStatus("error", _("Could not copy to clipboard"), transcript);\n          }\n          completeOnce(false);\n          return;\n        }',
            fn_body,
        )
        self.assertIn('} catch (error) {\n        this._completeKeyboardInsertFailure(completeOnce, _("Keyboard insert failed"), error);', fn_body)
        self.assertIn(
            'this._setStatus("error", _("Copied to clipboard; automatic paste command could not be started"), transcript);\n          completeOnce(false);',
            fn_body,
        )

    def test_history_entries_can_be_copied_or_inserted(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('let transcripts = new PopupMenu.PopupIconMenuItem(_("Open transcripts"), "folder-documents-symbolic"', source)
        self.assertIn("this.maintenanceMenuItem.menu.addMenuItem(transcripts);", source)
        self.assertNotIn("this.transcriptsMenuItem.menu.addMenuItem(transcripts);", source)
        self.assertIn('let listTranscripts = new PopupMenu.PopupIconMenuItem(_("List all Transcripts"), "view-list-symbolic"', source)
        self.assertIn("this._connectSafe(listTranscripts, \"activate\", () => this._listAllTranscripts())", source)
        self.assertIn("this.maintenanceMenuItem.menu.addMenuItem(listTranscripts);", source)
        self.assertIn('let exportTranscripts = new PopupMenu.PopupIconMenuItem(_("Export all Transcripts"), "document-save-symbolic"', source)
        self.assertIn("this._connectSafe(exportTranscripts, \"activate\", () => this._exportAllTranscripts())", source)
        self.assertIn("this.maintenanceMenuItem.menu.addMenuItem(exportTranscripts);", source)
        self.assertNotIn("this.maintenanceMenuItem.menu.addMenuItem(this.transcriptStorageItem);", source)
        self.assertNotIn("this.maintenanceMenuItem.menu.addMenuItem(this.openAiFlexProcessingItem);", source)
        self.assertIn("_allHistoryArgs: function()", source)
        self.assertIn('return [this._cliCommand(), "transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"];', source)
        self.assertIn("_transcriptsExportArgs: function()", source)
        self.assertIn('"transcripts-export"', source)
        self.assertIn('if (mode === "off")', source)
        self.assertNotIn("_allTranscriptsText: function(transcripts)", source)
        self.assertNotIn("_showAllTranscriptsDialog: function(transcripts)", source)
        self.assertIn("_listAllTranscripts: function()", source)
        self.assertIn('if (!this._findTrustedProgramInPath("zenity")) {', source)
        self.assertIn('this._setStatus("processing", _("Preparing transcript list..."), this.lastTranscript)', source)
        self.assertIn("_showTranscriptsWindow: function(content, count, truncated)", source)
        self.assertIn('let content = typeof payload.content === "string" ? payload.content : "";', source)
        self.assertIn('if (content.trim() === "")', source)
        self.assertNotIn('let content = String(payload.content || "");', source)
        self.assertIn('let zenity;', source)
        self.assertIn('zenity = this._findTrustedProgramInPath("zenity");', source)
        self.assertIn("Gio.SubprocessFlags.STDIN_PIPE", source)
        self.assertIn("this._runBoundedSubprocess(args, {}, {", source)
        self.assertIn("this.transcriptWindowToken = windowToken;", source)
        self.assertIn("if (!isCurrentWindow())", source)
        self.assertNotIn("communicate_utf8_async", source)
        self.assertIn("_exportAllTranscripts: function()", source)
        self.assertIn('let path = typeof payload.path === "string" ? payload.path.trim() : "";', source)
        self.assertIn('let encryptionMode = typeof payload.encryption === "string" ? payload.encryption.trim() : "";', source)
        self.assertIn('let encryptedMode = encryptionMode === "keyring" || encryptionMode === "passphrase";', source)
        self.assertIn("if (payload.encrypted !== true || payload.plaintext !== false || !encryptedMode)", source)
        self.assertNotIn('String(payload.encryption || "") === "off"', source)
        self.assertIn('let message = _("Transcript export was not encrypted");', source)
        self.assertIn('this._setStatus("processing", _("Exporting transcripts..."), this.lastTranscript)', source)
        self.assertIn('this._notify(_("Speed of Cinnamon transcript export"), message, false);', source)
        self.assertNotIn('this._openFile(path, _("Opened transcript document: ") + String(payload.transcripts || 0));', source)
        self.assertIn("let entry = new PopupMenu.PopupSubMenuMenuItem(label)", source)
        self.assertIn('let transcriptText = typeof transcript.text === "string" ? transcript.text : "";', source)
        self.assertIn('let label = this._shortMenuText(preview || name || _("Transcript"), 80);', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Insert transcript"), "edit-paste-symbolic"', source)
        self.assertIn("insertItem.setSensitive(hasTranscriptText);", source)
        self.assertIn("this._connectSafe(insertItem, \"activate\", () => this._insertHistoryTranscript(transcriptText))", source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Copy transcript"), "edit-copy-symbolic"', source)
        self.assertIn("copyItem.setSensitive(hasTranscriptText);", source)
        self.assertIn("this._connectSafe(copyItem, \"activate\", () => this._copyHistoryTranscript(transcriptText))", source)
        self.assertIn("_insertHistoryTranscript: function(text)", source)
        insert_history_start = source.index("_insertHistoryTranscript: function(text)")
        insert_history_end = source.index("\n  _setStatusPreservingRecording:", insert_history_start)
        insert_history_block = source[insert_history_start:insert_history_end]
        self.assertIn("if (this._hasActiveRecordingState())", insert_history_block)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Finish the current recording before inserting another transcript")', insert_history_block)
        self.assertIn("this._insertTranscriptText(text);", source)
        copy_history_start = source.index("_copyHistoryTranscript: function(text)")
        copy_history_end = source.index("\n  _insertHistoryTranscript:", copy_history_start)
        copy_history_block = source[copy_history_start:copy_history_end]
        self.assertIn('let statusTranscript = this._hasActiveRecordingState() ? "" : text;', copy_history_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not copy transcript"), statusTranscript);', copy_history_block)
        self.assertIn('this._setStatusPreservingRecording("done", _("Copied transcript"), statusTranscript);', copy_history_block)
        self.assertIn("this._preparedTranscriptText(text, true)", source)
        self.assertIn("this._preparedTranscriptText(this.lastTranscript, true)", source)

    def test_history_refresh_serializes_backend_requests(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_refreshHistory: function()")
        end = source.index("\n  _listAllTranscripts:", start)
        block = source[start:end]
        self.assertIn("if (this.historyRefreshToken)", block)
        self.assertIn("this.historyRefreshToken = refreshToken;", block)
        self.assertIn("this.historyRefreshToken = null;", block)
        self.assertLess(block.index("if (this.historyRefreshToken)"), block.index("let refreshToken = {};"))
        callback_block = block[block.index("this._spawnJson(historyArgs,"):]
        self.assertLess(callback_block.index("this.historyRefreshToken !== refreshToken"), callback_block.index("this.historyRefreshToken = null;"))
        self.assertLess(callback_block.index("this.historyRefreshToken = null;"), callback_block.index("if (payload.error)"))

    def test_cleanup_can_be_previewed_before_deleting_files(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))

        self.assertIn("max-transcript-files", schema["layout"]["output-section"]["keys"])
        self.assertEqual(schema["max-transcript-files"]["default"], 500)
        self.assertEqual(schema["max-transcript-files"]["min"], 1)
        self.assertEqual(schema["max-transcript-files"]["max"], 1000)
        self.assertIn("After each successful transcription", schema["max-transcript-files"]["tooltip"])
        self.assertIn("const DEFAULT_MAX_TRANSCRIPT_FILES = 500;", source)
        self.assertIn("const TRANSCRIPT_STORAGE_LIMITS = [20, 50, 100, 200, 500, 1000];", source)
        self.assertIn("this.maxTranscriptFiles = DEFAULT_MAX_TRANSCRIPT_FILES;", source)
        self.assertIn('["max-transcript-files", "maxTranscriptFiles"]', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "max-transcript-files", "maxTranscriptFiles", this._onTranscriptRetentionSettingsChanged, null)', source)
        self.assertNotIn('this.transcriptStorageItem = new PopupMenu.PopupSubMenuMenuItem(_("Store transcripts: 500"))', source)
        self.assertNotIn("this.maintenanceMenuItem.menu.addMenuItem(this.transcriptStorageItem);", source)
        self.assertIn("--keep-transcripts\", String(this._normalizeTranscriptLimit(this.maxTranscriptFiles))", source)
        self.assertIn('if (key === "max-transcript-files")', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Preview cleanup"), "edit-find-symbolic"', source)
        self.assertIn('new PopupMenu.PopupIconMenuItem(_("Clean all old files"), "edit-clear-symbolic"', source)
        self.assertIn("this._connectSafe(cleanupPreview, \"activate\", () => this._previewCleanup())", source)
        self.assertIn("_cleanupPreviewArgs: function()", source)
        self.assertNotIn('"--keep-transcripts", "100"', source)
        self.assertIn('return [this._cliCommand(), "cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"];', source)
        self.assertIn('return [this._cliCommand(), "cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--dry-run", "--json"];', source)
        self.assertIn("_(\"Clean all old files preview\")", source)
        self.assertIn('"--dry-run", "--json"', source)
        self.assertIn("_cleanupCount: function(payload, dryRun)", source)
        self.assertIn("payload.would_delete_transcripts", source)
        self.assertIn("_cleanupPreviewText: function(payload)", source)
        self.assertIn("_showCleanupPreviewDialog: function(payload)", source)
        preview_dialog_start = source.index("_showCleanupPreviewDialog: function(payload)")
        preview_dialog_end = source.index("\n  _normalizeTextPolishingPreset:", preview_dialog_start)
        preview_dialog_block = source[preview_dialog_start:preview_dialog_end]
        self.assertIn("if (this.cleanupPreviewDialogToken)", preview_dialog_block)
        self.assertIn("let releaseDialog = () =>", preview_dialog_block)
        self.assertIn("this.cleanupPreviewDialog === dialog", preview_dialog_block)
        self.assertIn("this.cleanupPreviewDialog = null;", preview_dialog_block)
        self.assertIn("if (closed) {\n        releaseDialog();", preview_dialog_block)
        self.assertIn("let failToOpen = () =>", preview_dialog_block)
        self.assertIn("if (!dialog) {", preview_dialog_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Cleanup preview could not be opened")', preview_dialog_block)
        self.assertIn('if (closeDialog(dialog)) {', preview_dialog_block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Cleanup preview could not be closed")', preview_dialog_block)
        fail_start = preview_dialog_block.index("let failToOpen = () =>")
        fail_block = preview_dialog_block[fail_start:]
        self.assertIn("releaseDialog();", fail_block[fail_block.index("if (!dialog) {"):fail_block.index("if (closeDialog(dialog)) {")])
        self.assertNotIn("releaseDialog();", fail_block[fail_block.index("if (closeDialog(dialog)) {"):])
        self.assertIn('payload.would_delete_paths.filter((path) => typeof path === "string" && path.trim() !== "")', source)
        self.assertIn("let hiddenPathCount = this._safePayloadCount(payload.would_delete_path_count) + this._safePayloadCount(payload.failed_path_count) + this._safePayloadCount(payload.skipped_active_path_count);", source)
        self.assertIn("_safePayloadCount: function(value)", source)
        self.assertIn('typeof limit === "number" && isFinite(limit)', source)
        self.assertIn('let count = typeof value === "number" ? value : NaN;', source)
        self.assertNotIn("let count = Number(value);", source)
        self.assertIn('lines.push(_("File paths are hidden for privacy; counts are shown instead."));', source)
        self.assertIn("addPaths(_(\"Planned files:\"), plannedPaths);", source)
        self.assertIn('this._newSafeDialog("cleanup-preview")', source)
        self.assertIn("this._dialogAddChild(dialog, this._newSafeLabel(this._cleanupPreviewText(payload)", source)
        self.assertIn('this._dialogSetButtons(dialog, [', source)
        self.assertIn('this._dialogOpen(dialog, "cleanup-preview")', source)
        self.assertIn("_previewCleanup: function()", source)
        self.assertIn('this._setStatus("processing", _("Previewing cleanup..."), this.lastTranscript)', source)
        self.assertIn('this._setStatus("ready", _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), this.lastTranscript)', source)
        self.assertIn("this._showCleanupPreviewDialog(payload);", source)
        preview_start = source.index("_previewCleanup: function()")
        preview_end = source.index("\n  _cleanupOldFiles:", preview_start)
        self.assertIn("this.cleanupPreviewDialogToken || this._hasLocalProcessingWorkflow()", source[preview_start:preview_end])
        self.assertIn("let deleted = this._cleanupCount(payload, false);", source)
        cleanup_start = source.index("_cleanupOldFiles: function()")
        cleanup_end = source.index("\n  _settingsSnapshot:", cleanup_start)
        cleanup_block = source[cleanup_start:cleanup_end]
        self.assertIn("if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow())", cleanup_block)
        helper_start = source.index("_hasLocalProcessingWorkflow: function(includePendingCleanup)")
        helper_end = source.index("\n  _setActiveLanguage:", helper_start)
        self.assertIn("this.cleanupPreviewDialogToken", source[helper_start:helper_end])

    def test_voice_model_menu_can_return_to_automatic_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_("Automatic voice model")', source)
        self.assertIn("_selectAutomaticVoiceBackend: function()", source)
        self.assertIn('"voice-automatic"', source)
        self.assertIn('"voice-static"', source)
        self.assertIn('"external-api-voice"', source)
        self.assertIn('this._setStatusPreservingRecording("ready", _("Voice model: automatic"), this.lastTranscript)', source)
        self.assertIn("this._refreshModelMenu();", source)

    def test_voice_model_remove_status_does_not_render_backend_path_message(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this._setStatus("done", _("Removed model: ") + name, this.lastTranscript);', source)
        remove_start = source.index("_removeVoiceModel: function(model)")
        remove_end = source.index("\n  _selectVoiceModel:", remove_start)
        remove_block = source[remove_start:remove_end]
        self.assertIn('_("Removed model, but voice settings could not be updated"),\n            false', remove_block)
        self.assertNotIn('payload.message || _("Removed model: ") + name', source)

    def test_auto_paste_matches_identity_markers_with_bounded_short_tokens(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        excelStart = source.index('"excel": [')
        excelEnd = source.index('"teams": [', excelStart)
        self.assertNotIn('"et",', source[excelStart:excelEnd])
        self.assertIn('"excel": [', source)
        self.assertIn('"microsoft excel"', source)
        self.assertIn("_windowIdentityValueMatchesMarker: function(value, marker)", source)
        self.assertIn('if (markerValue.length > 3 || /[^a-z0-9]/.test(markerValue)) {', source)
        self.assertIn('let boundaryBefore = before === "" || /[^a-z0-9]/.test(before);', source)
        self.assertIn('_windowIdentityMatchesAutoPaste: function(marker)', source)
        self.assertIn('windowTitle: String(this.targetWindowXTitle || "").trim().toLowerCase(),', source)
        self.assertIn('let expectedTitle = String(snapshot.windowTitle || "").trim().toLowerCase();', source)
        self.assertIn('let activeTitle = this._shortMenuText(String(titleOutput || "").trim(), 160).toLowerCase();', source)

    def test_applet_marks_text_uri_clipboard_targets_as_non_text(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('const NON_TEXT_TEXT_CLIPBOARD_TARGETS = {', source)
        self.assertIn('"text/html": true,', source)
        self.assertIn('"text/rtf": true,', source)
        self.assertIn('"text/uri-list": true,', source)
        self.assertIn('"text/x-moz-url": true', source)
        self.assertIn('let rawTarget = String(lines[i]).trim().toLowerCase();', source)
        self.assertIn('let target = rawTarget.split(";", 1)[0];', source)
        self.assertIn('if (NON_TEXT_TEXT_CLIPBOARD_TARGETS[target]) {', source)
        self.assertIn('nonTextTargets.push(target);', source)

    def test_tooltip_shows_private_transcript_length_only(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_shortTranscript: function() {", source)
        self.assertIn('let transcriptLength = String(this.lastTranscript).length;', source)
        self.assertIn('return _("Transcript preview hidden (length: ") + String(transcriptLength) + " chars)";', source)
        self.assertIn('set_applet_tooltip(tooltip + "\\n" + this._shortTranscript())', source)
        self.assertNotIn('let clean = this.lastTranscript.replace(/\\s+/g, " ").trim();', source)
        self.assertNotIn('clean.length > 80 ? clean.slice(0, 77) + "..." : clean;', source)

        update_start = source.index("_updateAutoPasteItem: function()")
        update_end = source.index("\n  _windowTitleMatchesAutoPaste:", update_start)
        update_block = source[update_start:update_end]
        self.assertIn("this._setMenuItemLabelSafely(this.autoPasteItem, this._autoPasteLabel());", update_block)
        self.assertNotIn("this._populateAutoPasteMenu();", update_block)

        status_start = source.index("_setStatusPreservingRecording: function(status, message, transcript)")
        status_end = source.index("\n  _setStatus: function", status_start)
        status_block = source[status_start:status_end]
        self.assertIn("if (!this._hasActiveRecordingState())", status_block)
        self.assertIn("if (!this._lifecycleAllowsWork())", status_block)
        self.assertIn("try {", status_block)
        self.assertIn('this._recordLifecycleError("status-update", error);', status_block)
        self.assertIn("this._updatePanel();", status_block)

    def test_setting_choices_preserve_recorded_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method in [
            ("_selectTranscriptStorageLimit: function(limit)", "\n  _customTranscriptLimitPromptArgs:"),
            ("_selectArtifactEncryptionMode: function(mode)", "\n  _selectOutputMethod:"),
            ("_selectOutputMethod: function(method)", "\n  _optionLabel:"),
            ("_selectInputSource: function(name, label)", "\n  _selectDefaultInputSource:"),
            ("_selectVoiceModel: function(model, preserveRecording)", "\n  _selectAutomaticVoiceBackend:"),
            ("_selectAutomaticVoiceBackend: function()", "\n  _selectStaticVoiceBackend:"),
            ("_selectStaticVoiceBackend: function(transcriber, message)", "\n  _externalApiEnvPath:"),
            ("_selectTextPolishingPreset: function(preset)", "\n  _populateTextPolishingSafetyMenu:"),
            ("_toggleTextPolishingSafetyFlag: function(settingKey, propertyName, label)", "\n  _selectTextModelBackend:"),
            ("_selectTextModelBackend: function(backend, model, message, preserveRecording)", "\n  _activateOllamaTextModelFlow:"),
            ("_promptCustomRecordingLimit: function()", "\n  _parseCustomRecordingLimit:"),
            ("_parseCustomRecordingLimit: function(value)", "\n  _transcriptStorageLabel:"),
            ("_promptCustomTranscriptLimit: function()", "\n  _parseCustomTranscriptLimit:"),
            ("_parseCustomTranscriptLimit: function(value)", "\n  _populateRecordingOptionsMenu:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("_setStatusPreservingRecording", block, method)

    def test_recording_option_status_helpers_use_safe_state_updates(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, expected in [
            ("_selectRecorder: function(method)", "\n  _normalizeRecordingLimit:", "this._setStatusPreservingRecording(this.status"),
            ("_selectRecordingLimit: function(seconds)", "\n  _customRecordingLimitPromptArgs:", "this._setStatusPreservingRecording(this.status"),
            ("_setRecordingOptionStatus: function(message)", "\n  _toggleAutoTranscribeTimeout:", "this._setStatusPreservingRecording(\"ready\""),
            ("_setNotificationOptionStatus: function(message)", "\n  _toggleNotifyRecording:", "this._setStatusPreservingRecording(\"ready\""),
            ("_setTextOptionStatus: function(message)", "\n  _toggleAppendSpace:", "this._setStatusPreservingRecording(\"ready\""),
            ("_toggleOpenAiFlexProcessing: function()", "\n  _normalizeLanguage:", "this._setStatusPreservingRecording(this.status"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(expected, block, method)
            self.assertNotIn("this._uiMessageText(message)", block, method)

        input_start = source.index("_selectInputSource: function(name, label)")
        input_end = source.index("\n  _selectDefaultInputSource:", input_start)
        input_block = source[input_start:input_end]
        self.assertIn('let safeLabel = typeof label === "string" ? label : "";', input_block)
        self.assertIn('this._setStatusPreservingRecording(this.status, _("Input device for next recording: ") + safeLabel', input_block)
        self.assertNotIn("this._uiMessageText(label)", input_block)

    def test_manual_alarm_check_cannot_overwrite_local_workflow_status(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_checkAlarms: function(manual)")
        end = source.index("\n  _refreshInputSourceMenu:", start)
        block = source[start:end]
        guard = block.index("if (this.alarmCheckToken")
        self.assertIn("this.isCommandRunning", block[guard:block.index("let checkToken")])
        self.assertIn("this._hasLocalProcessingWorkflow()", block[guard:block.index("let checkToken")])
        self.assertIn("let localWorkflowBusy = this.isCommandRunning || this._hasLocalProcessingWorkflow();", block)
        self.assertIn("let canUpdateManualStatus = () => manual && !this.isCommandRunning &&", block)
        self.assertIn("let manualStatusAllowed = canUpdateManualStatus();", block)
        self.assertIn("if (manualStatusAllowed || (!localWorkflowBusy &&", block)
        self.assertIn("} else if (manualStatusAllowed)", block)
        self.assertIn("if (manualStatusAllowed) {\n          this._refreshAlarmMenu();", block)
        self.assertIn("if (canUpdateManualStatus()) {\n            this._setAlarmErrorStatus", block)

        copy_start = source.index("_copyAlarmCommands: function()")
        copy_end = source.index("\n  _setAlarmEnabled:", copy_start)
        copy_block = source[copy_start:copy_end]
        self.assertIn("if (this.isCommandRunning || this._hasLocalProcessingWorkflow())", copy_block)

    def test_ollama_flows_are_cancelled_before_recording_starts(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        toggle_block = source[toggle_start:toggle_end]
        self.assertLess(
            toggle_block.index("this._cancelOllamaFlowForRecording()"),
            toggle_block.index("if (this.isCommandRunning)"),
        )

        cancel_start = source.index("_cancelOllamaFlowForRecording: function()")
        cancel_end = source.index("\n  _activateOllamaTextModelFlow:", cancel_start)
        cancel_block = source[cancel_start:cancel_end]
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", cancel_block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", cancel_block)
        self.assertIn("return ollamaWatchCleanupSucceeded && ollamaFlowCleanupSucceeded;", cancel_block)
        self.assertIn("this.ollamaModelInstallRunning", cancel_block)

        self.assertIn('}, { resourceGroup: "ollama" });', source)
        self.assertIn('resourceGroup: options.resourceGroup,', source)

        terminal_start = source.index("_runTerminalWorkflow: function(")
        terminal_end = source.index("\n  _terminalWorkflowScript:", terminal_start)
        terminal_block = source[terminal_start:terminal_end]
        self.assertIn("cancelOllamaFlow === true", terminal_block)
        self.assertIn("this.ollamaModelFlowToken !== ollamaFlowToken", terminal_block)
        self.assertIn('resourceGroup: cancelOllamaFlow === true ? "ollama" : "terminal",', terminal_block)

    def test_terminal_workflow_cannot_overwrite_new_recording_status(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        toggle_block = source[toggle_start:toggle_end]
        self.assertLess(
            toggle_block.index("this.terminalWorkflowToken = null;"),
            toggle_block.index("if (this.isCommandRunning)"),
        )

        workflow_start = source.index("_runTerminalWorkflow: function(")
        workflow_end = source.index("\n  _terminalWorkflowScript:", workflow_start)
        workflow_block = source[workflow_start:workflow_end]
        self.assertIn("let terminalWorkflowToken = {};", workflow_block)
        self.assertIn("this.terminalWorkflowToken = terminalWorkflowToken;", workflow_block)
        self.assertIn("if (this.terminalWorkflowToken !== terminalWorkflowToken)", workflow_block)
        self.assertIn("this.terminalWorkflowToken = null;", workflow_block)

    def test_terminal_ollama_cleanup_failures_are_preserved(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_runTerminalWorkflow: function(")
        end = source.index("\n  _terminalWorkflowScript:", start)
        block = source[start:end]
        self.assertIn("let ollamaCleanupFailed = false;", block)
        self.assertIn("let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;", block)
        self.assertIn("let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();", block)
        self.assertIn("ollamaCleanupFailed = !ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded;", block)
        self.assertIn("if (ollamaCleanupFailed)", block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped")', block)

    def test_recording_start_invalidates_stale_background_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        toggle_start = source.index("_toggleRecording: function()")
        toggle_end = source.index("\n  _restartApplet:", toggle_start)
        toggle_block = source[toggle_start:toggle_end]
        self.assertIn("this._invalidateBackgroundCallbacksForRecording()", toggle_block)
        self.assertLess(
            toggle_block.index("this._invalidateBackgroundCallbacksForRecording()"),
            toggle_block.index("if (this.isCommandRunning)"),
        )
        self.assertIn("if (!this._invalidateBackgroundCallbacksForRecording())", toggle_block)

        helper_start = source.index("_invalidateBackgroundCallbacksForRecording: function()")
        helper_end = source.index("\n  _runDoctor:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("this._statusRefreshToken++;", helper_block)
        self.assertIn("this._statusCommandRunning = false;", helper_block)
        self.assertIn('this._terminateProcessesByGroup("status")', helper_block)
        self.assertIn("return statusCleanupSucceeded &&", helper_block)
        for token in [
            "historyRefreshToken",
            "inputSourceMenuRefreshToken",
            "modelMenuRefreshToken",
            "voiceModelActionToken",
            "textModelMenuRefreshToken",
            "alarmMenuRefreshToken",
            "alarmActionToken",
            "alarmCheckToken",
            "benchmarkFlowToken",
            "settingsTransferToken",
            "setupDiagnosticsToken",
            "doctorCommandToken",
            "customLimitPromptToken",
            "autoPastePromptToken",
            "transcriptListPromptToken",
            "cleanupPreviewDialogToken",
        ]:
            self.assertIn(f"this.{token} = null;", helper_block)
        self.assertIn("let cleanupPreviewCleanupSucceeded = true;", helper_block)
        self.assertIn("this._dialogClose(this.cleanupPreviewDialog, \"cleanup-preview\")", helper_block)
        self.assertIn("this._setStatusPreservingRecording(\"error\", _(\"Cleanup preview could not be stopped\")", helper_block)
        self.assertIn("this.cleanupPreviewDialogToken = null;", helper_block)
        self.assertIn("&& cleanupPreviewCleanupSucceeded", helper_block)
        self.assertIn("let transcriptPromptCleanupSucceeded = true;", helper_block)
        self.assertIn("this._dialogClose(this.transcriptListPromptDialog, \"transcript-list\")", helper_block)
        self.assertIn("this._setStatusPreservingRecording(\"error\", _(\"Transcript list confirmation could not be stopped\")", helper_block)
        self.assertIn("&& transcriptPromptCleanupSucceeded && orphanedDialogCleanupSucceeded;", helper_block)
        self.assertIn('this._terminateProcessesByGroup("history-refresh")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("input-source-refresh")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("model-menu-refresh")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("voice-model")', helper_block)
        self.assertIn("let hadVoiceModelAction = Boolean(this.voiceModelActionToken);", helper_block)
        self.assertIn("let hadVoiceModelCleanupFailure = this.voiceModelCleanupFailed === true;", helper_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup(\n        "voice-model",', helper_block)
        self.assertIn('this._terminateProcessesByGroup("text-model-refresh")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("alarm-menu-refresh")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("alarm-action")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("alarm-check")', helper_block)
        self.assertIn("let hadBenchmarkFlow = Boolean(this.benchmarkFlowToken);", helper_block)
        self.assertIn("let hadBenchmarkCleanupFailure = this.benchmarkCleanupFailed === true;", helper_block)
        self.assertIn('this._terminateProcessesByGroup("benchmark")', helper_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup(\n        "benchmark",', helper_block)
        self.assertIn('this._terminateProcessesByGroup("doctor")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("settings-transfer")', helper_block)
        self.assertIn('this._terminateProcessesByGroup("setup-diagnostics")', helper_block)
        self.assertIn("let hadCleanupCommand = Boolean(this._cleanupCommandToken);", helper_block)
        self.assertIn('this._terminateProcessesByGroup("maintenance")', helper_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup(\n        "maintenance",', helper_block)
        self.assertIn("&& maintenanceCleanupSucceeded", helper_block)
        self.assertNotIn("modelMenuCleanupSucceeded", helper_block)
        self.assertNotIn("alarmMenuCleanupSucceeded", helper_block)
        self.assertIn('this._terminateProcessesByGroup("settings-prompt")', helper_block)
        self.assertIn('for (let group of ["keyboard", "clipboard", "x11"])', helper_block)
        self.assertIn("this._terminateProcessesByGroup(group) === false", helper_block)
        self.assertIn("this.textInsertCancellationFailed = true;", helper_block)
        self.assertIn('let ollamaWatchTimerCleanupSucceeded = this._clearOllamaInstallWatchTimer() !== false;', helper_block)
        self.assertIn('this._terminateProcessesByGroup("ollama")', helper_block)
        self.assertIn("this.ollamaModelCleanupFailed = true;", helper_block)
        self.assertIn("this.ollamaModelInstallToken = null;", helper_block)
        self.assertIn("if (!ollamaWatchTimerCleanupSucceeded)", helper_block)
        self.assertIn("ollamaWatchTimerCleanupSucceeded && ollamaCleanupSucceeded", helper_block)
        self.assertIn('this._releaseBusyStateAfterProcessCleanup("maintenance", "maintenanceCleanupFailed");', source)
        self.assertIn("this.transcriptWindowToken = null;", helper_block)
        self.assertNotIn("this.settingsWindowToken = null;", helper_block)

    def test_doctor_callback_cannot_overwrite_new_recording_status(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_runDoctor: function(startupCheck)")
        end = source.index("\n  _applyDoctorPayload:", start)
        block = source[start:end]
        self.assertIn("let doctorToken = {};", block)
        self.assertIn("this.doctorCommandToken = doctorToken;", block)
        self.assertIn("if (this.doctorCommandToken !== doctorToken", block)
        self.assertIn("this.doctorCommandToken = null;", block)
        self.assertIn('resourceGroup: "doctor"', block)

    def test_status_refresh_is_cancelled_before_recording_starts(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        refresh_start = source.index("_refreshStatus: function(fromStatusTimer)")
        refresh_end = source.index("\n  _hasCancelableRecordingWork:", refresh_start)
        self.assertIn('resourceGroup: "status"', source[refresh_start:refresh_end])

    def test_recording_start_cancels_stale_text_insert(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_toggleRecording: function()")
        end = source.index("\n  _restartApplet:", start)
        block = source[start:end]
        self.assertIn("if (!this._cancelTextInsertForSettingsChange())", block)
        self.assertNotIn("if (this.textInsertToken)", block)
        self.assertLess(
            block.index("if (!this._cancelTextInsertForSettingsChange())"),
            block.index("if (this.isCommandRunning)"),
        )

    def test_recording_stop_is_not_blocked_by_model_compatibility_check(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_toggleRecording: function()")
        end = source.index("\n  _restartApplet:", start)
        block = source[start:end]
        self.assertIn("let hasExistingRecordingWork = this._hasActiveRecordingState();", block)
        self.assertIn("if (!hasExistingRecordingWork && !this._ensureVoiceModelCompatibleWithCurrentLanguage(true))", block)
        self.assertLess(
            block.index("let hasExistingRecordingWork = this._hasActiveRecordingState();"),
            block.index('toggleArgs = this._baseArgs("toggle");'),
        )

    def test_clipboard_overwrite_cancel_respects_insert_token(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_confirmClipboardOverwriteForPaste: function(")
        end = source.index("\n  _pasteClipboardAfterFocus:", start)
        block = source[start:end]
        cancel = block.index('this._setStatus("ready", _("Clipboard overwrite cancelled")')
        self.assertIn("if (isCurrentOperation())", block[cancel - 80:cancel])
        self.assertIn('if (!this._dialogClose(dialog, "clipboard-overwrite"))', block)
        self.assertIn("this.textInsertCancellationFailed = true;", block)
        self.assertIn('this._setStatus("error", _("Clipboard overwrite prompt could not be closed"), transcript);', block)
        overwrite = block.index('label: _("Overwrite clipboard")')
        overwrite_block = block[overwrite:]
        self.assertIn('if (!this._dialogClose(dialog, "clipboard-overwrite"))', overwrite_block)
        self.assertIn('return;\n            }\n            this.clipboardOverwriteDialog = null;\n            if (!isCurrentOperation())', overwrite_block)
        self.assertIn('this._recordLifecycleError("clipboard-overwrite", error);', block)

    def test_clipboard_overwrite_does_not_continue_when_dialog_close_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_confirmClipboardOverwriteForPaste: function(")
        end = source.index("\n  _pasteClipboardAfterFocus:", start)
        block = source[start:end]
        overwrite = block.index('label: _("Overwrite clipboard")')
        overwrite_block = block[overwrite:]
        self.assertLess(
            overwrite_block.index('if (!this._dialogClose(dialog, "clipboard-overwrite"))'),
            overwrite_block.index("this._clipboardPayloadSnapshotAsync"),
        )
        self.assertIn("this.textInsertCancellationFailed = true;", overwrite_block)
        self.assertIn('this._setStatus("error", _("Clipboard overwrite prompt could not be closed"), transcript);', overwrite_block)

    def test_clipboard_overwrite_prompt_releases_insert_when_open_cleanup_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_confirmClipboardOverwriteForPaste: function(")
        end = source.index("\n  _pasteClipboardAfterFocus:", start)
        block = source[start:end]
        self.assertIn("let failToOpen = () => {", block)
        self.assertIn('let closed = this._dialogClose(dialog, "clipboard-overwrite");', block)
        self.assertIn("if (closed) {", block)
        self.assertIn("this.clipboardOverwriteDialog = null;", block)
        self.assertIn("this.textInsertCancellationFailed = true;", block)
        self.assertIn('this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);\n      complete(false);', block)
        fail_start = block.index("let failToOpen = () => {")
        open_failure = block.index('if (!this._dialogOpen(dialog, "clipboard-overwrite"))')
        self.assertLess(block.index("complete(false);", fail_start), open_failure)

    def test_transcript_list_prompt_keeps_token_when_open_cleanup_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_confirmPlaintextTranscriptList: function(completionCallback)")
        end = source.index("\n  _loadAllTranscriptsDocument:", start)
        block = source[start:end]
        self.assertIn("let failToOpen = () => {", block)
        self.assertIn('let closed = this._dialogClose(dialog, "transcript-list");', block)
        self.assertIn("let complete = (result, releasePrompt) =>", block)
        self.assertIn("complete(false, closed);", block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);', block)
        self.assertIn("failToOpen();", block)
        open_failure = block.index('if (!this._dialogOpen(dialog, "transcript-list"))')
        self.assertLess(block.index("complete(false, closed);", block.index("let failToOpen")), open_failure)

    def test_transcript_list_prompt_keeps_token_when_close_fails(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index('label: _(\"Show transcripts\"),')
        end = source.index("\n        }.bind(this),", start)
        block = source[start:end]
        self.assertIn('let closed = this._dialogClose(dialog, "transcript-list");', block)
        close_failure = block.index("if (!closed)")
        self.assertLess(block.index("complete(false, false);", close_failure), block.index("return;", close_failure))

    def test_transcript_list_prompt_completion_is_guarded(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_confirmPlaintextTranscriptList: function(completionCallback)")
        end = source.index("\n  _loadAllTranscriptsDocument:", start)
        block = source[start:end]
        completion = block.index("if (typeof completionCallback === \"function\")")
        completion_block = block[completion:]
        self.assertIn("try {\n          completionCallback(result === true);", completion_block)
        self.assertIn('this._recordLifecycleError("transcript-list-completion", error);', completion_block)

    def test_text_insert_releases_token_on_sync_snapshot_failure(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        end = source.index("\n  _restartRelistenRecording:", start)
        block = source[start:end]
        snapshot_start = block.index("this._clipboardPayloadSnapshotAsync")
        snapshot_block = block[snapshot_start - 20:]
        self.assertIn("try {", snapshot_block)
        self.assertIn("failPreparation(error, true);", snapshot_block)
        failure_start = block.index("let failPreparation = (error, notifyCompletion) => {")
        failure_end = block.index('    if (this._isEmptyTranscriptText(transcript)', failure_start)
        failure_block = block[failure_start:failure_end]
        self.assertIn("release();", failure_block)
        self.assertIn('this._recordLifecycleError("text-insert", error);', block)
        self.assertIn('this._setStatusPreservingRecording("error", _("Could not prepare text insertion")', block)
        self.assertIn("return false;", block)
        self.assertIn("let failPreparation = (error, notifyCompletion) => {", block)
        self.assertIn("this.textInsertToken !== insertToken", block)
        self.assertIn("try {\n        let result = this._copyAndMaybePasteTranscriptText", block)

    def test_async_text_insert_preparation_failure_completes_pending_workflow(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        end = source.index("\n  _restartRelistenRecording:", start)
        block = source[start:end]
        failure_start = block.index("let failPreparation = (error, notifyCompletion) => {")
        failure_end = block.index('    if (this._isEmptyTranscriptText(transcript)', failure_start)
        failure_block = block[failure_start:failure_end]
        self.assertIn("if (notifyCompletion === true && typeof completionCallback === \"function\")", failure_block)
        self.assertIn("try {\n          completionCallback(false);", failure_block)
        self.assertIn('this._recordLifecycleError("text-insert-completion", callbackError);', failure_block)
        self.assertIn("failPreparation(error, true);", block)

    def test_sync_clipboard_insert_completes_pending_relisten(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint)")
        end = source.index("\n  _restartRelistenRecording:", start)
        block = source[start:end]
        snapshot_start = block.index("this._clipboardPayloadSnapshotAsync")
        snapshot_block = block[snapshot_start:]
        self.assertEqual(snapshot_block.count("let result = this._copyAndMaybePasteTranscriptText"), 2)
        self.assertEqual(snapshot_block.count("complete(result);"), 2)
        self.assertNotIn("if (result !== null) {\n                release();", snapshot_block)

    def test_text_insert_cancellation_invalidates_x11_target_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_cancelTextInsertForSettingsChange: function()")
        end = source.index("\n  on_applet_clicked:", start)
        block = source[start:end]
        self.assertIn("this.targetWindowGeneration = Number(this.targetWindowGeneration || 0) + 1;", block)
        generation_index = block.index("this.targetWindowGeneration =")
        for group in ["keyboard", "clipboard", "x11"]:
            cleanup_index = block.index('this._terminateProcessesByGroup("' + group + '")')
            self.assertLess(generation_index, cleanup_index)

    def test_async_clipboard_snapshot_failures_complete_with_unknown_payload(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        snapshot_start = source.index("_clipboardPayloadSnapshotAsync: function(completionCallback)")
        snapshot_end = source.index("\n  _clipboardPayloadFingerprintFromTargetsAsync:", snapshot_start)
        snapshot_block = source[snapshot_start:snapshot_end]
        self.assertIn("let completed = false;", snapshot_block)
        self.assertIn("let unknown = () => complete(this._clipboardUnknownPayloadSnapshot());", snapshot_block)
        self.assertIn('this._recordLifecycleError("clipboard-query", error);', snapshot_block)
        self.assertIn("try {\n          if (targets === null || targets === undefined)", snapshot_block)

        fingerprint_start = source.index("_clipboardPayloadFingerprintFromTargetsAsync: function(")
        fingerprint_end = source.index("\n  _clipboardPayloadFingerprintFromText:", fingerprint_start)
        fingerprint_block = source[fingerprint_start:fingerprint_end]
        self.assertIn("let completed = false;", fingerprint_block)
        self.assertIn('let fail = (error) =>', fingerprint_block)
        self.assertIn('complete("unknown");', fingerprint_block)
        self.assertIn('this._recordLifecycleError("clipboard-query-completion", error);', fingerprint_block)

    def test_dynamic_menu_errors_preserve_active_recording_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for method, next_method, expected in [
            ("_refreshAlarmMenu: function()", "\n  _populateAlarmMenu:", "this._setAlarmErrorStatus(safeError);"),
            ("_refreshInputSourceMenu: function()", "\n  _populateInputSourceMenu:", "this._setStatusPreservingRecording"),
            ("_refreshHistory: function()", "\n  _listAllTranscripts:", "this._setStatusPreservingRecording"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn(expected, block, method)

        for method, next_method in [
            ("_setAlarmEnabled: function(id, enabled)", "\n  _removeAlarm:"),
            ("_removeAlarm: function(id)", "\n  _checkAlarms:"),
            ("_checkAlarms: function(manual)", "\n  _refreshInputSourceMenu:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertNotIn("this._setAlarmErrorStatus(payload.error);", block)
            self.assertIn("this._sanitizeErrorMessage(payload.error)", block)

    def test_all_dynamic_menu_populators_guard_menu_actor_before_mutation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, item in [
            ("_populateAlarmMenu: function(alarms, summary, message)", "\n  _setAlarm", "alarmItem"),
            ("_populateInputSourceMenu: function(sources, message)", "\n  _setStatus", "inputSourceItem"),
            ("_populateHistoryMenu: function(transcripts)", "\n  _copyHistoryTranscript", "historyItem"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("if (!this._canMutateMenu(this." + item + "))", block)
            self.assertLess(block.index("_canMutateMenu"), block.index("_clearMenuItems"))

    def test_all_static_menu_populators_guard_menu_actor_before_mutation(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method, item in [
            ("_populateRecorderMenu: function()", "_populateRecordingLimitMenu", "recorderItem"),
            ("_populateRecordingLimitMenu: function()", "_populateTranscriptStorageMenu", "recordingLimitItem"),
            ("_populateTranscriptStorageMenu: function()", "_populateRecordingOptionsMenu", "transcriptStorageItem"),
            ("_populateRecordingOptionsMenu: function()", "_populateNotificationOptionsMenu", "recordingOptionsItem"),
            ("_populateNotificationOptionsMenu: function()", "_populateOutputMethodMenu", "notificationOptionsItem"),
            ("_populateOutputMethodMenu: function()", "_populateArtifactEncryptionMenu", "outputMethodItem"),
            ("_populateArtifactEncryptionMenu: function()", "_populateTextOptionsMenu", "artifactEncryptionItem"),
            ("_populateTextOptionsMenu: function()", "_populateAutoPasteMenu", "textOptionsItem"),
            ("_populateAutoPasteMenu: function()", "_populateLanguageMenu", "autoPasteItem"),
            ("_populateLanguageMenu: function()", "_populateShortcutMenu", "languageItem"),
            ("_populateShortcutMenu: function()", "_refreshAlarmMenu", "shortcutItem"),
        ]:
            start = source.index(method)
            end = source.index("\n  " + next_method + ": function", start)
            block = source[start:end]
            self.assertIn("if (!this._canMutateMenu(this." + item + "))", block)
            self.assertLess(block.index("_canMutateMenu"), block.index("_clearMenuItems"))

    def test_menu_label_updates_skip_finalized_cinnamon_actors(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_setMenuItemLabelSafely: function(item, text)")
        end = source.index("\n  _addTextModelMenuEntry:", start)
        block = source[start:end]
        self.assertIn('this._runGuarded("menu-label"', block)
        self.assertIn("itemActor.is_finalized", block)
        self.assertIn("label.is_finalized", block)
        self.assertIn('typeof label.set_text !== "function"', block)
        self.assertNotIn(".label.text", source)

    def test_menu_sensitivity_updates_skip_finalized_cinnamon_actors(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_setMenuItemSensitiveSafely: function(item, sensitive)")
        end = source.index("\n  _addTextModelMenuEntry:", start)
        block = source[start:end]
        self.assertIn('this._runGuarded("menu-sensitive"', block)
        self.assertIn("item.actor.is_finalized", block)
        self.assertIn("typeof item.setSensitive !== \"function\"", block)
        self.assertIn("item.setSensitive(Boolean(sensitive));", block)
        self.assertEqual(source.count("this.cancelItem.setSensitive"), 1)
