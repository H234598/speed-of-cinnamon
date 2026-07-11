from __future__ import annotations

import json
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
        self.assertIn('args.push("--openai-compatible-model", safeOpenAiCompatibleModel)', source)
        self.assertIn('args.push("--openai-compatible-text-model", safeOpenAiCompatibleTextModel)', source)
        self.assertIn('args.push("--no-openai-compatible-flex-processing")', source)
        self.assertNotIn('this.openAiFlexProcessingItem = new PopupMenu.PopupMenuItem("")', source)
        self.assertIn('let textCommandConfigured = String(this.postProcessCommand || "").trim() !== "";', source)
        self.assertIn('let customLabel = _("Custom command") + (textCommandConfigured ? "" : _(" - configure in settings"));', source)
        self.assertIn('let custom = this._selectionMenuItem((backend === "command" || backend === "custom" ? "[x] " : "[ ] ") + customLabel);', source)
        self.assertIn('this._setStatus("ready", _("Configure custom text command in applet settings"), this.lastTranscript);', source)
        self.assertIn('this._connectSafe(openaiCompatible, "activate", () => this._openExternalApiEnvEditor("text"));', source)
        self.assertIn('let presetMenu = new PopupMenu.PopupSubMenuMenuItem(_("Polishing preset: ") + this._textPolishingPresetLabel(this.postProcessPreset));', source)
        self.assertIn("_populateTextPolishingPresetMenu: function(parentMenu)", source)
        self.assertIn("_selectTextPolishingPreset: function(preset)", source)
        self.assertIn("const TEXT_POLISHING_PRESETS = [", source)
        self.assertIn('this.settings.setValue("post-process-preset", this.postProcessPreset);', source)
        self.assertIn('let safetyMenu = new PopupMenu.PopupSubMenuMenuItem(_("Polishing safety"));', source)
        self.assertIn("_populateTextPolishingSafetyMenu: function(parentMenu)", source)
        self.assertIn("_toggleTextPolishingSafetyFlag: function(settingKey, propertyName, label)", source)
        self.assertIn('this._toggleTextPolishingSafetyFlag("post-process-preserve-code", "postProcessPreserveCode", _("Preserve commands and code"))', source)
        self.assertIn('this._toggleTextPolishingSafetyFlag("post-process-never-add-content", "postProcessNeverAddContent", _("Never add content"))', source)
        self.assertIn('this._toggleTextPolishingSafetyFlag("post-process-mask-sensitive-data", "postProcessMaskSensitiveData", _("Mask sensitive data"))', source)
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
        self.assertIn('this.settings.setValue("openai-compatible-api-key", "");', source)
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
        self.assertIn('this._setStatus("ready", _("Configure custom voice command in applet settings"), this.lastTranscript);', source)
        self.assertIn("_selectStaticVoiceBackend: function(transcriber, message)", source)
        self.assertIn('this.settings.setValue("transcriber", this.transcriber);', source)
        self.assertIn('this.settings.setValue("whisper-model", this.whisperModel);', source)
        self.assertIn("_selectExternalApiVoiceBackend: function()", source)
        self.assertIn('this.transcriber = "openai-compatible";', source)
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
        self.assertIn('this.settings.setValue("artifact-encryption", this.artifactEncryption);', source)
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
        self.assertIn('this.settings.setValue("openai-compatible-url", this.openaiCompatibleUrl);', source)
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
        self.assertIn('this._setStatus("error", _("External API config file could not be written"), this.lastTranscript);', source)
        self.assertIn("_migrateExternalApiEnvFile: function(path)", source)
        self.assertIn('"OPENAI_COMPATIBLE_URL=" + LEGACY_OPENAI_COMPATIBLE_URL', source)
        self.assertIn('migrated.replace("OPENAI_COMPATIBLE_MODEL=", "OPENAI_COMPATIBLE_STT_MODEL=")', source)
        self.assertIn('migrated.indexOf("OPENAI_COMPATIBLE_TEXT_MODEL=") < 0', source)
        self.assertIn("values.OPENAI_COMPATIBLE_STT_MODEL || values.OPENAI_COMPATIBLE_MODEL", source)
        self.assertIn("this.externalApiEnvApplyTarget = \"voice\";", source)
        self.assertIn("_openExternalApiEnvEditor: function(target)", source)
        self.assertIn("_applyExternalApiEnvTarget: function(target)", source)
        self.assertIn('this._connectSafe(useItem, "activate", () => this._openExternalApiEnvEditor("voice"));', source)
        self.assertIn('this._connectSafe(openaiCompatible, "activate", () => this._openExternalApiEnvEditor("text"));', source)
        self.assertIn('this._setStatus("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);', source)
        self.assertIn('this._refreshTextModelMenuForBackend("openai-compatible");', source)
        self.assertIn("if (!this._writeExternalApiEnvFile()) {", source)
        self.assertIn("this._refreshTextModelMenu();\n        return;\n      }", source)
        self.assertIn("this._selectExternalApiVoiceBackend();", source)
        self.assertNotIn('this._selectTextModelBackend("openai-compatible", this.openaiCompatibleModel, _("Text polishing: OpenAI-compatible API"));', source)
        self.assertIn("_watchExternalApiEnvFile: function(path)", source)
        self.assertIn("_disconnectTrackedSignalsForTarget: function(target)", source)
        self.assertIn("this._disconnectTrackedSignalsForTarget(monitor);", source)
        self.assertIn("_clearMenuItems: function(menu)", source)
        self.assertIn("this._clearMenuItems(this.recorderItem.menu);", source)
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
        self.assertIn('this._setStatus("error", _("External API config contains invalid values"), this.lastTranscript);', source)
        apply_index = source.index("_applyExternalApiEnvFile: function(showStatus)")
        set_index = source.index('this.settings.setValue("openai-compatible-url", this.openaiCompatibleUrl);', apply_index)
        validate_index = source.index("config = this._validatedExternalApiConfig", apply_index)
        self.assertLess(validate_index, set_index)
        self.assertIn("const MAX_EXTERNAL_API_ENV_BYTES = 65536;", source)
        self.assertIn("_externalApiEnvFileInfo: function(path, allowMissing)", source)
        self.assertIn('query_info("standard::type,standard::size,unix::mode", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null)', source)
        self.assertIn("info.get_file_type() !== Gio.FileType.REGULAR", source)
        self.assertIn("External API config file is too large", source)
        self.assertIn("this._externalApiEnvFileInfo(path, true);", source)
        self.assertIn('Gio.File.new_for_path(path).set_attribute_uint32("unix::mode", 0o600, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);', source)
        self.assertIn("Gio.File.new_for_path(path).replace_contents(", source)
        self.assertIn("ByteArray.fromString(text)", source)
        self.assertIn("Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION", source)
        self.assertNotIn("GLib.umask", source)
        self.assertIn("this._externalApiEnvFileInfo(path, false);", source)
        self.assertIn("this._readExternalApiEnvFile(path)", source)
        self.assertIn("ByteArray.toString(contents)", source)

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
        self.assertIn("this._populateAlarmMenu([], this._sanitizeErrorMessage(payload.error));", source)
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
        self.assertIn('this._setStatus("error", _("Could not open link"), this.lastTranscript);', source)
        self.assertNotIn('_("Could not open link: ") + err.message', source)
        self.assertIn('this._setStatus("error", _("Could not restart applet"), this.lastTranscript);', source)
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
        self.assertIn('if (!this._lifecycleAllowsWork() || !this.menu || typeof this.menu.toggle !== "function") {', source)
        self.assertIn('this._runGuarded("menu-toggle", () => {', source)
        self.assertIn("if (!menu.isOpen) {", source)
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
        self.assertIn('return Main.keybindingManager.addHotKey(name, accelerator, this._guardCallback("hotkeys", callback, undefined)) === true;', source)
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
        self.assertIn('this.settings.setValue("max-seconds", this.maxSeconds)', source)
        self.assertIn('"--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this.recordingLimitItem.label.text = _("Duration: ") + this._formatSeconds(this._normalizeRecordingLimit(this.maxSeconds))', source)
        self.assertIn('this.lastMessage = _("Duration for next recording: ") + label', source)


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
        self.assertIn('this._spawnText(this._autoPastePromptArgs(), (output) => {', source)
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
        self.assertIn('let autoPasteTarget = this._windowTitleMatchesAutoPaste();', source)
        self.assertIn('let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;', source)
        self.assertIn('let suppressAutoPasteEnter = method !== "clipboard-paste" || submitWithReturn;', source)
        self.assertIn('let text = this._preparedTranscriptText(transcript, suppressAutoPasteEnter);', source)
        self.assertIn('_copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard)', source)
        self.assertIn('_pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {', source)
        self.assertIn('completeOnce(completed === true);', source)
        self.assertIn('_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback)', source)
        self.assertIn('_spawnKeyboardWhenClipboardReady(args, followUpArgs, expectedClipboardText, Date.now() + CLIPBOARD_READY_TIMEOUT_MS, expectedTargetWindow, complete);', source)
        self.assertIn('_spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback)', source)
        self.assertIn('completionCallback(false);', source)
        self.assertIn('return null;', source)
        self.assertNotIn('if (method === "clipboard-paste" && !autoPasteTarget) {', source)
        self.assertNotIn('Copied to clipboard; Auto-Paste target not enabled', source)
        self.assertNotIn('Auto-Enter', source)
        self.assertIn('this._normalizedAutoPasteWindowTitle(this._windowProbeValue(this.targetWindow, "get_title") || this.targetWindowXTitle || "")', source)
        self.assertIn('this._pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {', source)
        self.assertIn("CLIPBOARD_READY_RETRY_MS", source)
        self.assertIn("CLIPBOARD_READY_TIMEOUT_MS", source)
        self.assertIn("_spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback)", source)
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
        self.assertIn('_typeTextAfterFocus: function(text, completionCallback) {', source)

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
        self.assertIn('this._setStatus("done", _("Copied shortcut reference"), this.lastTranscript)', source)
        self.assertIn("this._populateShortcutMenu();", source)

    def test_ui_subprocess_launchers_handle_async_exit_failures(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        settings_start = source.index("_openAppletSettings: function()")
        settings_end = source.index("\n  _openSetupGuide:", settings_start)
        settings_block = source[settings_start:settings_end]
        self.assertIn('_("Cinnamon applet settings process exited unexpectedly")', settings_block)
        self.assertIn("result.error || result.timedOut || result.outputTooLarge", settings_block)
        self.assertNotIn("}, function() {});", settings_block)

        terminal_start = source.index("_runTerminalWorkflow: function(title, command, openedMessage, cancelOllamaFlow, ollamaFlowToken)")
        terminal_end = source.index("\n  _terminalWorkflowScript:", terminal_start)
        terminal_block = source[terminal_start:terminal_end]
        self.assertIn('_("Terminal process exited unexpectedly")', terminal_block)
        self.assertIn("result.error || result.timedOut || result.outputTooLarge", terminal_block)
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

    def test_alarm_refresh_ignores_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        refresh_start = source.index("_refreshAlarmMenu: function()")
        refresh_end = source.index("\n  _populateAlarmMenu:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("let refreshToken = {};", refresh_block)
        self.assertIn("this.alarmMenuRefreshToken = refreshToken;", refresh_block)
        self.assertIn("this.alarmMenuRefreshToken !== refreshToken", refresh_block)
        self.assertIn("!this._canMutateMenu(this.alarmItem)", refresh_block)

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
            ("_setTextOptionStatus: function(message)", "\n  _toggleAppendSpace:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            self.assertIn("_uiMessageText", source[start:end])

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
        self.assertIn("if (alarm.notify !== true)", source)
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

        select_start = source.index("_selectVoiceModel: function(model)")
        select_end = source.index("\n  _selectAutomaticVoiceBackend:", select_start)
        select_block = source[select_start:select_end]
        self.assertIn("let path = this._modelPathFromPayload(model);", select_block)
        self.assertIn("return false;", select_block)
        self.assertIn("return true;", select_block)
        self.assertNotIn("String(model.path || \"\")", select_block)

        remove_start = source.index("_removeVoiceModel: function(model)")
        remove_end = source.index("\n  _selectVoiceModel:", remove_start)
        remove_block = source[remove_start:remove_end]
        self.assertIn("let path = this._modelPathFromPayload(model);", remove_block)

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
        self.assertIn("if (payload.removed !== true)", block)
        self.assertIn('_("Model was not downloaded: ") + name', block)
        self.assertLess(block.index("if (payload.removed !== true)"), block.index("if (path !== \"\""))

    def test_input_source_names_and_descriptions_are_string_checked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_populateInputSourceMenu: function(sources, message)")
        end = source.index("\n  _selectInputSource:", start)
        block = source[start:end]
        self.assertIn('typeof source.name === "string"', block)
        self.assertIn('sourceName = this._coerceCliTextArg(source.name, "input source");', block)
        self.assertIn('typeof source.description === "string"', block)
        self.assertIn("let label = description || sourceName;", block)
        select_start = source.index("_selectInputSource: function(name, label)")
        select_end = source.index("\n  _selectDefaultInputSource:", select_start)
        select_block = source[select_start:select_end]
        self.assertIn('if (typeof name !== "string")', select_block)
        self.assertIn('this.inputDevice = this._coerceCliTextArg(name, "input device");', select_block)

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
        self.assertIn("global.logError(err);", block)
        self.assertIn("continue;", block)
        self.assertIn('"ollama model details"', block)

    def test_invalid_recording_settings_cannot_stick_toggle_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_toggleRecording: function()")
        end = source.index("\n  _restartApplet:", start)
        block = source[start:end]
        self.assertIn("let toggleArgs;", block)
        self.assertIn('toggleArgs = this._baseArgs("toggle");', block)
        self.assertIn('this._setStatus("error", _("Could not prepare recording command: ") + safeError', block)
        self.assertLess(block.index("let toggleArgs;"), block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(toggleArgs,", block)

    def test_auto_recording_commands_validate_settings_before_busy_state(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        relisten_start = source.index("_restartRelistenRecording: function()")
        relisten_end = source.index("\n  _preparedTranscriptText:", relisten_start)
        relisten_block = source[relisten_start:relisten_end]
        self.assertIn("let startArgs;", relisten_block)
        self.assertIn('startArgs = this._baseArgs("start");', relisten_block)
        self.assertIn('this._setStatus("error", _("Could not prepare relisten command: ") + safeError', relisten_block)
        self.assertLess(relisten_block.index("let startArgs;"), relisten_block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(startArgs,", relisten_block)

        auto_start = source.index("_maybeAutoTranscribeRecorded: function(payload, statusOverride)")
        auto_end = source.index("\n  _clearStatusTimer:", auto_start)
        auto_block = source[auto_start:auto_end]
        self.assertIn("let stopArgs;", auto_block)
        self.assertIn('stopArgs = this._baseArgs("stop");', auto_block)
        self.assertIn('this._setStatus("error", _("Could not prepare timed recording command: ") + safeError', auto_block)
        self.assertLess(auto_block.index("let stopArgs;"), auto_block.index("this.isCommandRunning = true;"))
        self.assertIn("this._spawnJson(stopArgs,", auto_block)

    def test_input_source_refresh_ignores_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        refresh_start = source.index("_refreshInputSourceMenu: function()")
        refresh_end = source.index("\n  _populateInputSourceMenu:", refresh_start)
        refresh_block = source[refresh_start:refresh_end]
        self.assertIn("let refreshToken = {};", refresh_block)
        self.assertIn("this.inputSourceMenuRefreshToken = refreshToken;", refresh_block)
        self.assertIn("this.inputSourceMenuRefreshToken !== refreshToken", refresh_block)
        self.assertIn("!this._canMutateMenu(this.inputSourceItem)", refresh_block)

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
        self.assertIn("let watchToken = {};", watch_block)
        self.assertIn("this.ollamaInstallWatchToken = watchToken;", watch_block)
        self.assertIn("this.ollamaInstallWatchToken !== watchToken", watch_block)
        self.assertIn("this._scheduleOllamaInstallWatchPoll(watchToken);", watch_block)
        self.assertIn("this.ollamaInstallWatchToken = null;", watch_block)

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
        self.assertIn('this.autoRelistenManualStopRequested = false;', source)
        self.assertIn("this.cancelPendingWhileCommandRunning = false;", source)
        self.assertIn("this.autoRelistenSequence = 0;", source)
        self.assertIn('let shouldRelisten = this.autoRelistenPending;', source)
        self.assertIn('relistenToken = String(this.autoRelistenSequence) + ":" + recordingKey;', source)
        self.assertIn('this.autoRelistenPending = Boolean(relistenToken);', source)
        self.assertIn('this.autoRelistenPendingToken = relistenToken;', source)
        self.assertIn('if (relistenToken && this.autoRelistenPendingToken !== relistenToken) {\n        this.isCommandRunning = false;\n        if (this.cancelPendingWhileCommandRunning) {\n          this._applyPayloadSafely(nextPayload);\n        }\n        return;\n      }', source)
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
        self.assertIn("let processToken = this._registerProcess(process, generation, options.resourceGroup);", source)
        self.assertIn('if (suppressCallback || this.appletRemoved || this.spawnGeneration !== generation || typeof callback !== "function")', source)

    def test_lifecycle_timers_ignore_removed_applet(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for marker in (
            "_scheduleSetupCheck: function()",
            "_scheduleAlarmCheck: function(delaySeconds)",
            "_scheduleStatusPoll: function()",
            "_scheduleDisplayTick: function()",
            "_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback)",
            "_watchExternalApiEnvFile: function(path)",
        ):
            with self.subTest(marker=marker):
                start = source.index(marker)
                end = source.find("\n  _", start + len(marker))
                block = source[start:] if end == -1 else source[start:end]
                self.assertIn("if (this.appletRemoved)", block)

    def test_applet_has_fault_containment_lifecycle(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        for state in ("INITIALIZING", "RUNNING", "DEGRADED", "REMOVING", "REMOVED"):
            self.assertIn('"' + state + '"', source)
        for marker in (
            "_startLifecycle: function()",
            "_runGuarded: function(group, callback, fallback)",
            "_guardCallback: function(group, callback, fallback)",
            "_handleInitializationFailure: function(error)",
            "_beginTeardown: function()",
            "_finishTeardown: function()",
            "this._recordLifecycleError(\"init\", error);",
            "return !this._initFailed &&",
            "this._disabledErrorGroups[key] = true;",
            "this.lifecycleState = LIFECYCLE_DEGRADED;",
            "this._runGuarded(\"panel-style\"",
            "this._runGuarded(\"panel-update\"",
        ):
            self.assertIn(marker, source)

        self.assertIn("LIFECYCLE_ERROR_WINDOW_MS = 60000", source)
        self.assertIn("LIFECYCLE_ERROR_THRESHOLD = 3", source)
        self.assertIn("if (!this._beginTeardown())", source)
        self.assertIn("this._finishTeardown();", source)
        self.assertIn("connectionId = target.connect(signal, this._guardCallback", source)
        self.assertNotIn("connectionId = this._connectSafe(target, signal", source)
        self.assertIn("_trackMonitor: function(monitor)", source)
        self.assertIn("_removeHotkey: function(id)", source)

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
        self.assertIn("if (this.isCommandRunning) {", source)
        self.assertIn("this._statusCommandRunning = true;", source)
        self.assertIn("try {", source)
        self.assertIn("} catch (err) {", source)
        self.assertIn('this._setStatus("error", _("Status refresh failed: ") + safeError', source)
        self.assertIn("} finally {", source)
        self.assertIn("this._statusCommandRunning = false;", source)

    def test_status_refresh_applies_only_latest_response(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("this._statusRefreshToken = 0;", source)
        refresh_index = source.index("_refreshStatus: function() {")
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

    def test_repeating_tracked_timers_remain_teardown_tracked(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName)")
        end = source.index("\n  _init:", start)
        block = source[start:end]
        self.assertIn("let keepTimer = this._runGuarded(\"timer-\" + key, callback, false) === true;", block)
        self.assertIn("if (!keepTimer) {", block)
        self.assertIn("this._untrackTimer(key, sourceId, propertyName);", block)
        self.assertLess(block.index("let keepTimer"), block.index("if (!keepTimer)"))

    def test_local_status_updates_invalidate_inflight_status_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        set_status_index = source.index("_setStatus: function(status, message, transcript)")
        set_status_end = source.index("\n  _maybeNotify:", set_status_index)
        set_status_block = source[set_status_index:set_status_end]
        self.assertIn("this._statusRefreshToken++;", set_status_block)
        self.assertLess(
            set_status_block.index("this._statusRefreshToken++;"),
            set_status_block.index("let previousStatus = this.status;"),
        )

    def test_status_checks_use_spawn_json_timeout(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const STATUS_COMMAND_TIMEOUT_MS = 10000;", source)
        self.assertIn("_refreshStatus: function() {", source)
        self.assertIn("}, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS });", source)
        self.assertIn("_spawnJson: function(args, callback, options) {", source)
        self.assertIn('Object.prototype.hasOwnProperty.call(options, "timeoutMs")', source)
        self.assertIn("if (timeoutMs > 0 && !this._scheduleTrackedTimer", source)

    def test_text_spawn_invalidates_stale_status_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        spawn_text_index = source.index("_spawnText: function(args, callback, options) {")
        spawn_text_end = source.index("\n  _applyPayload:", spawn_text_index)
        spawn_text_block = source[spawn_text_index:spawn_text_end]
        self.assertIn("this._statusRefreshToken++;", spawn_text_block)
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
        self.assertIn('this.doctorSummaryItem.label.text = this.doctorSummaryText || _("Doctor: not checked")', source)

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
        self.assertIn("let flowToken = {};", select_block)
        self.assertIn("this.benchmarkFlowToken = flowToken;", select_block)
        self.assertIn("this.benchmarkFlowToken !== flowToken", select_block)
        self.assertIn("this._benchmarkDownloadedModels(audioPath, flowToken);", select_block)

        benchmark_start = source.index("_benchmarkDownloadedModels: function(audioPath, flowToken)")
        benchmark_end = source.index("\n  _setAlarmOptionStatus:", benchmark_start)
        benchmark_block = source[benchmark_start:benchmark_end]
        self.assertIn("this.benchmarkFlowToken !== flowToken", benchmark_block)
        self.assertIn("this.benchmarkFlowToken = null;", benchmark_block)
        self.assertIn('let fastest = typeof payload.fastest_model === "string" ? payload.fastest_model.trim() : "";', benchmark_block)
        self.assertNotIn('let fastest = String(payload.fastest_model || "").trim();', benchmark_block)

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
        self.assertIn("this._coerceCliTextArg(value, IMPORT_TEXT_SETTINGS[key])", source)
        self.assertIn('if (typeof value !== "string")', source)
        self.assertIn("_coerceImportedEnumSetting: function(value, allowedValues, fallback)", source)

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
        self.assertIn("_settingsSnapshotInputOption: function(includeLifecycle)", source)
        self.assertIn("_settingsSnapshotInputOptionOrNull: function(includeLifecycle, errorStatus)", source)
        self.assertIn('snapshot["applet-lifecycle"] = this._appletLifecycleDiagnostics();', source)
        self.assertIn("this._spawnJson(this._doctorArgs(), (payload) => {", source)
        self.assertIn("let inputOption = this._settingsSnapshotInputOptionOrNull(false);", source)
        self.assertIn("let inputOption = this._settingsSnapshotInputOptionOrNull(true);", source)
        self.assertIn("}, inputOption);", source)
        self.assertIn("let hasInput = options.inputText !== null && options.inputText !== undefined;", source)
        self.assertIn("flags |= Gio.SubprocessFlags.STDIN_PIPE;", source)
        self.assertIn("stdin.write_all_async", source)
        self.assertIn("stream.write_all_finish(result);", source)

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
        self.assertIn('let text = typeof value === "string" ? value.replace(/\\s+/g, " ").trim() : "";', source)
        self.assertIn('typeof text !== "string" || !this._lifecycleAllowsWork()', source)
        self.assertIn('typeof label === "string" ? label : ""', source)
        self.assertIn("item.label.clutter_text.ellipsize = options.wrap ? Pango.EllipsizeMode.NONE : Pango.EllipsizeMode.END", source)

    def test_applet_adds_frontend_validation_for_long_or_invalid_text_fields(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("const CLI_TEXT_SETTINGS = {", source)
        self.assertIn("const MAX_SETTING_TEXT_CHARS = 4096;", source)
        self.assertIn("_coerceCliTextArg: function(value, fieldName)", source)
        self.assertIn('if (value !== undefined && value !== null && typeof value !== "string")', source)
        self.assertIn('let normalized = typeof value === "string" ? value : "";', source)
        self.assertIn('"personal-context": "personal context"', source)
        self.assertIn('"vocabulary": "vocabulary"', source)
        self.assertIn("let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this.openaiCompatibleUrl, \"openai-compatible URL\")", source)
        self.assertIn("safePersonalContext = this._coerceCliTextArg(this._singleLineCliTextValue(this.personalContext), \"personal context\")", source)
        self.assertIn("safeVocabulary = this._coerceCliTextArg(this._singleLineCliTextValue(this.vocabulary), \"vocabulary\")", source)
        self.assertIn("for (let key in CLI_TEXT_SETTINGS)", source)
        self.assertIn("let safeOllamaUrl = this._coerceCliTextArg(this.ollamaUrl, \"ollama URL\")", source)
        self.assertIn("let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this.openaiCompatibleUrl, \"openai-compatible URL\")", source)
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
        self.assertIn('this._selectTextModelBackend("ollama", model, _("Text model: ") + model);', source)
        self.assertIn("_promptInstallOllamaTextModel: function(flowToken)", source)
        self.assertIn("_ollamaModelPromptArgs: function()", source)
        self.assertIn("--entry-text=llama3.2:3b", source)
        self.assertIn("_installOllamaTextModel: function(model)", source)
        self.assertIn('"install-text-model", "--backend", "ollama", "--model", safeModel, "--json"', source)
        self.assertIn('typeof payload.model === "string" && payload.model.trim() !== ""', source)
        self.assertIn('let installedModel = payload && typeof payload.model === "string"', source)
        self.assertIn('String(model || "").trim()', source)
        self.assertIn('this._selectTextModelBackend("ollama", installedModel, message);', source)
        self.assertIn('this._notify(_("Ollama model installation failed"), safeError, true)', source)
        self.assertIn('this._notify(_("Could not check Ollama"), safeError, true)', source)
        self.assertIn('this._notify(_("Could not load Ollama models"), safeError, true)', source)
        self.assertNotIn('String(payload.error), true)', source)
        self.assertIn('this._notify(_("Ollama model installed"), installedModel, false)', source)

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

        watch_start = source.index("_scheduleOllamaInstallWatchPoll: function(watchToken)")
        watch_end = source.index("\n  _scheduleSetupCheck:", watch_start)
        watch_block = source[watch_start:watch_end]
        self.assertIn('this._tryTextModelsArgs("ollama")', watch_block)
        self.assertIn("this.ollamaInstallWatchToken = null;", watch_block)
        self.assertIn("return false;", watch_block)

    def test_ollama_model_dialogs_ignore_stale_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        choose_start = source.index("_promptChooseOllamaTextModel: function(models, flowToken)")
        choose_end = source.index("\n  _promptInstallOllamaTextModel:", choose_start)
        choose_block = source[choose_start:choose_end]
        self.assertIn("this.ollamaModelFlowToken !== flowToken", choose_block)
        self.assertIn("!this._lifecycleAllowsWork()", choose_block)
        self.assertIn("this._promptInstallOllamaTextModel(flowToken);", choose_block)

        install_start = source.index("_promptInstallOllamaTextModel: function(flowToken)")
        install_end = source.index("\n  _installOllamaTextModel:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn("this.ollamaModelFlowToken !== flowToken", install_block)
        self.assertIn("!this._lifecycleAllowsWork()", install_block)

    def test_ollama_model_flow_clears_terminal_and_install_failure_states(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        helper_start = source.index("_clearOllamaModelFlow: function(flowToken)")
        helper_end = source.index("\n  _activateOllamaTextModelFlow:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("if (flowToken && this.ollamaModelFlowToken !== flowToken)", helper_block)
        self.assertIn("this.ollamaModelFlowToken = null;", helper_block)

        runtime_start = source.index("_installOllamaRuntime: function(openChooserAfterInstall)")
        runtime_end = source.index("\n  _uninstallOllamaRuntime:", runtime_start)
        runtime_block = source[runtime_start:runtime_end]
        self.assertIn("let continueOllamaFlow = openChooserAfterInstall === true && Boolean(this.ollamaModelFlowToken);", runtime_block)
        self.assertIn("let ollamaFlowToken = continueOllamaFlow ? this.ollamaModelFlowToken : null;", runtime_block)
        self.assertIn("this._clearOllamaModelFlow();", runtime_block)
        self.assertIn("return opened;", runtime_block)

        install_start = source.index("_installOllamaTextModel: function(model)")
        install_end = source.index("\n  _refreshHistory:", install_start)
        install_block = source[install_start:install_end]
        self.assertIn("let flowToken = this.ollamaModelFlowToken;", install_block)
        self.assertIn('_("Another command is already running")', install_block)
        self.assertIn("this.ollamaModelFlowToken !== flowToken", install_block)
        self.assertIn("this._clearOllamaModelFlow(flowToken);", install_block)

        watch_start = source.index("_scheduleOllamaInstallWatchPoll: function(watchToken)")
        watch_end = source.index("\n  _scheduleSetupCheck:", watch_start)
        watch_block = source[watch_start:watch_end]
        self.assertIn("this._clearOllamaModelFlow();", watch_block)

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
        self.assertIn("let dueWasTruncated = dueCount > MAX_ALARM_NOTIFICATIONS;", check_block)
        self.assertIn("due = due.slice(0, MAX_ALARM_NOTIFICATIONS);", check_block)
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
        self.assertIn("if (this.isCommandRunning)", block)
        self.assertIn("return;", block)
        self.assertLess(block.index("if (this.isCommandRunning)"), block.index("this.isCommandRunning = true;"))

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

    def test_recording_payload_callbacks_use_fail_closed_handler(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        helper_start = source.index("_applyPayloadSafely: function(payload, statusRefreshToken)")
        helper_end = source.index("\n  _applyPayload: function(payload, statusRefreshToken)", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn("try {", helper_block)
        self.assertIn("this._applyPayload(payload, statusRefreshToken);", helper_block)
        self.assertIn('this._setStatus("error", _("Backend response handling failed: ") + safeError', helper_block)
        for marker in [
            "this._applyPayloadSafely(payload);",
            "this._applyPayloadSafely(nextPayload);",
        ]:
            self.assertIn(marker, source)

    def test_text_backend_choices_invalidate_stale_ollama_flows(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for method, next_method in [
            ("_selectTextModelBackend: function(backend, model, message)", "\n  _activateOllamaTextModelFlow:"),
            ("_openExternalApiEnvEditor: function(target)", "\n  _applyExternalApiEnvTarget:"),
            ("_applyExternalApiEnvTarget: function(target)", "\n  _selectTextModelBackend:"),
        ]:
            start = source.index(method)
            end = source.index(next_method, start)
            block = source[start:end]
            self.assertIn("this._cancelOllamaInstallWatch();", block)
            self.assertIn("this._clearOllamaModelFlow();", block)

    def test_text_backend_persistence_validates_model_names(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        start = source.index("_selectTextModelBackend: function(backend, model, message)")
        end = source.index("\n  _activateOllamaTextModelFlow:", start)
        block = source[start:end]
        self.assertIn("let safeModel;", block)
        self.assertIn('safeModel = this._coerceCliTextArg(model === undefined || model === null ? "" : model, "text model");', block)
        self.assertIn('this._setStatus("error", _("Text model is invalid: ") + safeError', block)
        self.assertIn("this.ollamaModel = safeModel;", block)
        self.assertIn("this.openaiCompatibleTextModel = safeModel;", block)

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

    def test_process_group_cancellation_suppresses_stale_callbacks(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        bounded_start = source.index("_runBoundedSubprocess: function(args, env, options, callback)")
        bounded_end = source.index("\n  _spawnJsonWithBackendEnvironment:", bounded_start)
        bounded_block = source[bounded_start:bounded_end]
        self.assertIn("let finish = (result, terminate, suppressCallback)", bounded_block)
        self.assertIn('if (hasInput && typeof options.inputText !== "string")', bounded_block)
        self.assertIn('typeof options.maxStdoutBytes === "number" && isFinite(options.maxStdoutBytes)', bounded_block)
        self.assertIn('typeof options.maxStderrBytes === "number" && isFinite(options.maxStderrBytes)', bounded_block)
        self.assertIn('typeof options.timeoutMs === "number" && isFinite(options.timeoutMs)', bounded_block)
        self.assertIn('typeof options.minimumTimeoutMs === "number" && isFinite(options.minimumTimeoutMs)', bounded_block)
        self.assertIn("suppressCallback || this.appletRemoved", bounded_block)
        self.assertIn("this._resourceRegistry.processes[processToken].cancel = (notifyCallback) => finish(", bounded_block)
        self.assertIn("notifyCallback === true ? false : true", bounded_block)
        stdin_start = bounded_block.rindex("if (hasInput) {")
        stdin_block = bounded_block[stdin_start:]
        self.assertIn("try {", stdin_block)
        self.assertIn("let stdin = process.get_stdin_pipe();", stdin_block)
        self.assertIn("finish({ error: error }, true);", stdin_block)

        group_start = source.index("_terminateProcessesByGroup: function(group, notifyCallback)")
        group_end = source.index("\n  _cancelAllCancellables:", group_start)
        group_block = source[group_start:group_end]
        self.assertIn("typeof processes[token].cancel === \"function\"", group_block)
        self.assertIn("processes[token].cancel(Boolean(notifyCallback));", group_block)

    def test_keyboard_group_cancel_notifies_active_insert_cleanup(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        remember_start = source.index("_rememberFocusedWindow: function(preserveOnFailure)")
        remember_end = source.index("\n  _closeMenuForKeyboardInsert:", remember_start)
        remember_block = source[remember_start:remember_end]
        keyboard_start = source.index("_spawnKeyboardProcess: function(args, completionCallback)")
        keyboard_end = source.index("\n  _spawnKeyboardArgs:", keyboard_start)
        keyboard_block = source[keyboard_start:keyboard_end]

        self.assertIn('this._terminateProcessesByGroup("keyboard", true);', remember_block)
        self.assertIn("let completeOnce = (result) =>", keyboard_block)
        self.assertIn("if (!handle) {\n        completeOnce(false);", keyboard_block)
        self.assertIn("result.cancelled", keyboard_block)

    def test_target_window_generation_invalidates_stale_insert_resources(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        remember_start = source.index("_rememberFocusedWindow: function(preserveOnFailure)")
        remember_end = source.index("\n  _closeMenuForKeyboardInsert:", remember_start)
        remember_block = source[remember_start:remember_end]
        self.assertIn('this._terminateProcessesByGroup("keyboard", true);', remember_block)
        self.assertIn('this._terminateProcessesByGroup("x11", true);', remember_block)
        self.assertIn('this._terminateProcessesByGroup("clipboard", true);', remember_block)

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

        activate_start = source.index("_activateTargetXWindow: function(completionCallback)")
        activate_end = source.index("\n  _targetXWindowSnapshot:", activate_start)
        activate_block = source[activate_start:activate_end]
        self.assertIn("let targetGeneration = Number(this.targetWindowGeneration || 0);", activate_block)
        self.assertIn("targetGeneration === Number(this.targetWindowGeneration || 0) && output !== null", activate_block)

        insert_start = source.index("_insertTranscriptText: function(transcript, completionCallback)")
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
            self.assertIn("let actionToken = {};", block)
            self.assertIn("this.alarmActionToken = actionToken;", block)
            self.assertIn("this.alarmActionToken !== actionToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)

    def test_alarm_checks_ignore_stale_backend_responses(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_checkAlarms: function(manual)")
        end = source.index("\n  _refreshInputSourceMenu:", start)
        block = source[start:end]
        self.assertIn("let checkToken = {};", block)
        self.assertIn("this.alarmCheckToken = checkToken;", block)
        self.assertIn("this.alarmCheckToken !== checkToken", block)
        self.assertIn("!this._lifecycleAllowsWork()", block)

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

        import_start = source.index("_importSettings: function()")
        import_end = source.index("\n  _applyImportedSettings:", import_start)
        import_block = source[import_start:import_end]
        self.assertIn("try {\n        let applied = this._applyImportedSettings(payload.settings || {});", import_block)
        self.assertIn('this._setStatus("error", _("Could not apply imported settings: ") + safeError', import_block)

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
            self.assertIn("let actionToken = {};", block)
            self.assertIn("this.setupDiagnosticsToken = actionToken;", block)
            self.assertIn("this.setupDiagnosticsToken !== actionToken", block)
            self.assertIn("!this._lifecycleAllowsWork()", block)

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
        self.assertIn('let safePostProcessPrompt = this._coerceCliTextArg(this._effectivePostProcessPrompt(), "post-process prompt");', source)
        self.assertIn('replace(/\\\\u000d|\\\\u000a|\\\\r|\\\\n/gi, " ")', source)
        self.assertIn('return this._singleLineCliTextValue(parts.join(" "));', source)
        self.assertIn("Preserve commands, code, paths, filenames, flags, variable names", source)
        self.assertIn("Do not add facts, explanations, headings", source)
        self.assertIn("If greetings, thanks, apologies, politeness markers", source)
        self.assertIn("Mask sensitive data such as tokens, passwords, account data", source)
        self.assertIn("_resetTextPolishingDefaults: function()", source)
        self.assertIn('this.settings.setValue("post-process-preset", this.postProcessPreset);', source)
        self.assertIn('this.settings.setValue("post-process-mask-sensitive-data", this.postProcessMaskSensitiveData);', source)
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
        self.assertIn("this._cancelOllamaInstallWatch();", uninstall_block)
        self.assertIn("this._clearOllamaModelFlow();", uninstall_block)

        setup_start = source.index("_runBasicSetup: function()")
        setup_end = source.index("\n  _selectBenchmarkAudioFile:", setup_start)
        setup_block = source[setup_start:setup_end]
        self.assertIn("this._cancelOllamaInstallWatch();", setup_block)
        self.assertIn("this._clearOllamaModelFlow();", setup_block)

    def test_terminal_workflow_preserves_shell_compound_syntax(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        start = source.index("_terminalWorkflowScript: function(lines)")
        end = source.index("\n  _installOllamaRuntimeCommand:", start)
        block = source[start:end]
        self.assertIn('return script.join("\\n");', block)
        self.assertNotIn('return script.join("; ");', block)
        self.assertIn('"if command -v ollama >/dev/null 2>&1; then",', source)
        self.assertIn('ollama_log_file=\\"$(mktemp \\"${XDG_RUNTIME_DIR:-/tmp}/speed-of-cinnamon-ollama.XXXXXX.log\\")\\"', source)
        self.assertIn('ollama serve >\\"$ollama_log_file\\" 2>&1 & sleep 2 || true', source)
        self.assertNotIn('/tmp/speed-of-cinnamon-ollama.log', source)
        self.assertIn('"else",', source)
        self.assertIn('"fi",', source)
        self.assertIn("_terminalCommandArgs: function(title, command)", source)
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
        self.assertIn('this._setStatus("ready", _("Transcript list cancelled"), this.lastTranscript);', source)
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
        self.assertIn('let callbackFn = this._guardCallback("backend-json", callback, undefined) || function() {};', source)
        self.assertIn("let done = false;", source)
        self.assertIn("if (done) {", source)
        self.assertIn('callbackFn({ status: "error", error: "Backend response is too large" });', source)
        self.assertIn("callbackFn(this._parseSpawnOutput(stdout));", source)
        self.assertIn("if (args.length > MAX_CLI_ARG_COUNT) {", source)
        self.assertIn("this._scheduleTrackedTimer(timeoutKey", source)
        self.assertIn('callbackFn({ status: "error", error: "Backend command timed out" });', source)

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
        self.assertIn("_spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback) {", source)
        self.assertIn("_spawnKeyboardProcess: function(args, completionCallback)", source)

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
        self.assertIn('this._rememberFocusedWindow();\n        this._toggleRecording();', source)
        self.assertIn('this._connectSafe(this.toggleItem, "activate", () => {\n      this._rememberFocusedWindow(true);\n      this._toggleRecording();\n    });', source)
        self.assertIn('this._connectSafe(startPrimary, "activate", () => this._startWithLanguage(primary, true));', source)
        self.assertIn('this._connectSafe(startSecondary, "activate", () => this._startWithLanguage(secondary, true));', source)
        self.assertIn("_startWithLanguage: function(language, preserveTargetOnFailure)", source)
        self.assertIn('if (!(preserveOnFailure && this._hasRememberedTargetWindow())) {', source)
        self.assertIn("_hasRememberedTargetWindow: function()", source)
        self.assertIn('on_applet_clicked: function() {', source)
        self.assertIn('if (!this._lifecycleAllowsWork()) {', source)
        self.assertIn('let menu = this.menu;', source)
        self.assertIn('this._runGuarded("menu-toggle", () => {', source)
        self.assertIn('this._rememberFocusedWindow();\n      }\n      menu.toggle();', source)
        self.assertNotIn('if (this.status !== "recording") {\n      this._rememberFocusedWindow();\n    }\n    this.notificationSessionActive = true;', source)
        self.assertIn("global.display ? global.display.focus_window : null", source)
        self.assertIn("this.targetWindowGeneration = Number(this.targetWindowGeneration || 0) + 1;", source)
        self.assertIn("this._rememberActiveXWindow(function() {}, targetGeneration);", source)
        self.assertIn("_rememberActiveXWindow: function(completionCallback, expectedGeneration)", source)
        self.assertIn("_xdotoolOutput: function(args, maxBytes, completionCallback, timeoutMs)", source)
        self.assertIn('this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (activeOutput) => {', source)
        self.assertIn('this._xdotoolOutput(["getwindowname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (titleOutput) => {', source)
        self.assertIn('this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (classOutput) => {', source)
        self.assertIn('this._xdotoolOutput(["windowactivate", "--sync", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (output) => {', source)
        self.assertIn("_targetXWindowSnapshot: function()", source)
        self.assertIn("_targetXWindowMatchesSnapshot: function(snapshot, completionCallback)", source)
        self.assertIn("_targetXWindowMatchesSnapshotTitle: function(snapshot, xid, completionCallback, deadlineMs)", source)
        self.assertIn("_restoreTargetWindowForPaste: function(completionCallback)", source)
        self.assertIn("return this._activateTargetXWindow(complete);", source)
        self.assertIn('if (!expectedTargetWindow) {', source)
        self.assertIn('this._targetXWindowMatchesSnapshot(expectedTargetWindow, (matches) => {', source)
        self.assertIn('if (String(activeOutput || "").trim() !== xid) {', source)
        self.assertIn('this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (classOutput) => {', source)
        self.assertIn('fail(_("Target window changed before automatic paste"));', source)
        self.assertIn('fail(_("Target window changed before automatic submit"));', source)
        self.assertIn('complete(targetGeneration === Number(this.targetWindowGeneration || 0) && output !== null);', source)
        self.assertIn("_closeMenuForKeyboardInsert: function() {", source)
        self.assertIn("this.menu.close();", source)
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
        self.assertIn('this._setStatus("done", _("No transcript text to insert"), "");', source)
        self.assertIn('this._scheduleTrackedTimer("paste", PASTE_SUBMIT_DELAY_MS', source)
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
        self.assertIn('this._payloadMessage(payload, _("Transcript already inserted by backend")', source)
        self.assertIn("if (!this._reserveAutoInsertFingerprint(insertFingerprint))", source)
        self.assertIn("this._rememberAutoInsertFingerprint(fingerprint);", source)
        self.assertIn("this._forgetAutoInsertFingerprint(insertFingerprint);", source)
        self.assertIn("_transcriptDigest: function(transcript)", source)
        self.assertIn("GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, text, -1)", source)
        self.assertIn('"sha256:" + GLib.compute_checksum_for_string', source)
        self.assertIn('return "digest-unavailable";', source)
        self.assertNotIn("rawTranscript.slice", source)
        self.assertNotIn("text.slice(0, 256)", source)
        self.assertIn("_finishPendingRelisten: function()", source)
        self.assertIn("this._finishPendingRelisten();", source)
        reserve_index = source.index("if (!this._reserveAutoInsertFingerprint(insertFingerprint))", finish_index)
        insert_index = source.index("this._insertTranscriptText(transcript,", finish_index)
        self.assertLess(reserve_index, insert_index)
        self.assertIn("if (result === null) {\n        return;\n      }", source[finish_index:source.index("_finishPendingRelisten: function()", finish_index)])
        duplicate_index = reserve_index
        duplicate_finish_index = source.index("this._finishPendingRelisten();", duplicate_index)
        duplicate_return_index = source.index("return;", duplicate_index)
        self.assertLess(duplicate_finish_index, duplicate_return_index)
        self.assertIn(
            "if (!completed) {\n          this._forgetAutoInsertFingerprint(insertFingerprint);\n          this.autoRelistenPending = false;\n          this.autoRelistenPendingToken = \"\";\n          this.autoRelistenManualStopRequested = true;\n          return;\n        }",
            source,
        )
        self.assertIn("_hasAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_reserveAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_rememberAutoInsertFingerprint: function(fingerprint)", source)
        self.assertIn("_forgetAutoInsertFingerprint: function(fingerprint)", source)
        restart_index = source.index("_restartRelistenRecording: function() {")
        restart_end = source.index("_preparedTranscriptText: function", restart_index)
        self.assertNotIn("this._resetAutoInsertFingerprint();", source[restart_index:restart_end])

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
        self.assertIn("if (this.autoRelistenPending || !this.autoRelisten || !this.notificationSessionActive)", source[pending_index:pending_end])
        self.assertIn('this.autoRelistenPendingToken = String(this.autoRelistenSequence) + ":done:" + marker;', source[pending_index:pending_end])
        self.assertIn("let previousNotificationSessionActive = this.notificationSessionActive;", source[finish_pending_index:finish_pending_end])
        self.assertIn("this.notificationSessionActive = true;\n      relistenStarted = this._restartRelistenRecording();", source[finish_pending_index:finish_pending_end])
        self.assertIn("this.autoRelistenManualStopRequested = false;", source[finish_pending_index:finish_pending_end])
        self.assertIn("this.notificationSessionActive = previousNotificationSessionActive;", source[finish_pending_index:finish_pending_end])

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
        self.assertIn("this.isCommandRunning && this.notificationSessionActive", cancel_work_block)
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

        self.assertIn("this.cancelItem.setSensitive(this._hasCancelableRecordingWork());", status_block)
        self.assertIn("if (!this._hasCancelableRecordingWork(statusOverride))", cancel_block)
        self.assertIn("if (!this.isCommandRunning && this.autoRelistenPending && this.textInsertToken)", cancel_block)
        self.assertIn('this._setStatus("ready", _("Auto Relisten cancelled"), this.lastTranscript);', cancel_block)
        self.assertIn("let effectiveStatus = typeof statusOverride === \"string\" ? statusOverride : this.status;", work_block)

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
            self.assertIn("let completeOnce = (value) =>", block)
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
        self.assertIn('if (!handle) {\n      completeOnce("", { error: "Subprocess could not be started" }, "");\n    }', block)
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
        self.assertIn("this._applyPayloadSafely(nextPayload);", block[mismatch:mismatch_end])
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
        self.assertIn("if (payload.error) {\n        this.autoRelistenPending = false;", restart_block)
        self.assertIn('nextStatus === "recording" || nextStatus === "recorded"', restart_block)
        self.assertIn('this.autoRelistenPendingToken = "";', restart_block)
        apply_index = restart_block.index("this._applyPayloadSafely(payload);")
        self.assertNotIn("this.autoRelistenManualStopRequested = false;", restart_block[:apply_index])

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

    def test_open_file_and_folder_errors_do_not_render_local_paths(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this._setStatus("error", _("Could not open folder"), this.lastTranscript);', source)
        self.assertIn('this._setStatus("error", _("Could not open file"), this.lastTranscript);', source)
        self.assertNotIn('_("Could not open folder: ") + err.message', source)
        self.assertNotIn('_("Could not open file: ") + err.message', source)

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
        self.assertIn('this.settings.setValue("insert-method", this.insertMethod)', source)
        self.assertIn('this._bindSetting(Settings.BindingDirection.IN, "insert-method", "insertMethod", this._onOutputSettingsChanged, null)', source)
        self.assertIn('this.outputMethodItem.label.text = _("Output: ") + this._outputMethodLabel(this._normalizeOutputMethod(this.insertMethod))', source)
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
        self.assertIn('this.settings.setValue("append-space", this.appendSpace)', source)
        self.assertIn('this.settings.setValue("sanitize-special-chars", this.sanitizeSpecialChars)', source)
        self.assertIn('this.settings.setValue("soften-profanity", this.softenProfanity)', source)
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
        self.assertIn("this._openFile(path, _(\"Opened profanity replacement list: \") + String(this._safePayloadCount(payload.entries)));", source)
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
        self.assertIn("this._connectSafe(this.insertLastItem, \"activate\", () => this._insertLastTranscript())", source)
        self.assertIn("this.insertLastItem.setSensitive(Boolean(this.lastTranscript))", source)
        self.assertIn("_insertLastTranscript: function()", source)
        self.assertIn("_insertTranscriptText: function(transcript, completionCallback)", source)
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
        self.assertIn('if (method === "none")', source)
        self.assertIn('if (method === "type")', source)
        self.assertIn("this.textInsertToken = null;", source)
        insert_start = source.index("_insertTranscriptText: function(transcript, completionCallback)")
        insert_end = source.index("_restartRelistenRecording: function()", insert_start)
        insert_block = source[insert_start:insert_end]
        self.assertIn("if (!this._lifecycleAllowsWork() || this.textInsertToken)", insert_block)
        self.assertIn("this.textInsertToken = insertToken;", insert_block)
        self.assertIn("if (!isCurrentInsert())", insert_block)
        self.assertIn("let complete = (result) =>", insert_block)
        self.assertIn("if (!this._typeTextAfterFocus(text, (completed) => {", source)
        self.assertIn('if (!this._closeMenuForKeyboardInsert()) {\n          this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);\n          release();\n          return false;\n        }\n        this._restoreTargetWindowForPaste((restored) => {', source)
        self.assertIn("_spawnKeyboardProcess: function(args, completionCallback)", source)
        self.assertIn('let xdotool = this._findTrustedProgramInPath("xdotool");', source)
        self.assertIn('[xdotool, "type", "--clearmodifiers", "--delay", String(delay), "--", typedText]', source)
        self.assertIn("_isTerminalTargetWindow: function()", source)
        self.assertIn("let canPasteWithKeyboard = this._findTrustedProgramInPath(\"xdotool\") || this._findTrustedProgramInPath(\"wtype\");", source)
        self.assertIn('let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;', source)
        self.assertIn('let terminalPaste = this._isTerminalTargetWindow();', source)
        self.assertIn('let hasXdotool = this._findTrustedProgramInPath("xdotool");', source)
        self.assertIn('let hasWtype = this._findTrustedProgramInPath("wtype");', source)
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
        self.assertIn("return this._spawnKeyboardAfterFocus(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback);", source)
        self.assertIn('this._setStatus("error", _("Target window unavailable for direct typing"), this.lastTranscript);', source)
        self.assertIn('[xdotool, "type", "--clearmodifiers", "--delay", String(delay), "--", typedText], null, null, expectedTargetWindow, completionCallback)', source)
        self.assertIn("return false;", source)
        self.assertIn("return true;", source)

    def test_applet_checks_clipboard_targets_before_overwriting_clipboard_for_auto_paste(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_clipboardProgramSpec: function()", source)
        self.assertIn("_clipboardPayloadArgs: function(spec, targetName)", source)
        self.assertIn("_clipboardTargetList: function(program, args, completionCallback, timeoutMs)", source)
        self.assertIn('let timeout = this._findTrustedProgramInPath("timeout");', source)
        self.assertIn("let helper = this._findTrustedProgramInPath(program);", source)
        self.assertIn('let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), helper];', source)
        self.assertIn("_clipboardNonTextPayloadTargets: function(targets)", source)
        self.assertIn("_clipboardPayloadSnapshot: function()", source)
        self.assertIn("_clipboardPayloadSnapshotAsync: function(completionCallback)", source)
        self.assertIn("_clipboardPayloadFingerprintFromTargetsAsync: function(spec, targets, completionCallback, deadlineMs)", source)
        self.assertIn("const CLIPBOARD_COMMAND_TIMEOUT_MS = 1500;", source)
        self.assertIn("const CLIPBOARD_MAX_TARGETS = 16;", source)
        self.assertIn("maxStdoutBytes: MAX_CLIPBOARD_TARGET_OUTPUT_BYTES", source)
        self.assertIn("minimumTimeoutMs: 1", source)
        self.assertIn('resourceGroup: "clipboard"', source)
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

        self.assertIn('if (targets === null || targets === undefined) {\n        complete(this._clipboardUnknownPayloadSnapshot());', source)
        self.assertIn('if (targetLines.length > CLIPBOARD_MAX_TARGETS) {\n        complete(this._clipboardUnknownPayloadSnapshot());', source)
        self.assertIn('if (payloadFingerprint === "unknown") {\n          complete(this._clipboardUnknownPayloadSnapshot());', source)
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

    def test_applet_tracks_non_text_payload_fingerprint_beyond_targets(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn("_clipboardPayloadFingerprintFromTargetsAsync: function(spec, targets, completionCallback, deadlineMs)", source)
        self.assertIn("_clipboardPayloadFingerprintFromText: function(payload, targetLabel)", source)
        self.assertIn("_clipboardPayloadSignaturesMatch: function(snapshotA, snapshotB)", source)
        self.assertIn("this._clipboardPayloadFingerprintFromTargetsAsync(spec, targetText", source)
        self.assertIn("payloadFingerprint: \"unknown\",", source)
        self.assertIn('if (snapshotA.payloadFingerprint === "unknown" || snapshotB.payloadFingerprint === "unknown") {', source)
        self.assertIn("let sortedTargets = nonTextTargets.slice().sort().slice(0, CLIPBOARD_MAX_TARGETS);", source)
        self.assertIn("let readNext = (index) => {", source)
        self.assertIn('complete(fingerprints.join("|"));', source)
        self.assertNotIn("let sampleTarget = String(nonTextTargets[0]);", source)
        self.assertIn('let data = String(payload || "");', source)
        self.assertIn("GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, data, -1)", source)
        self.assertIn(
            'return String(targetLabel || "") + ":sha256:" + String(GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, data, -1));',
            source,
        )
        self.assertNotIn("step = Math.max(1", source)
        self.assertNotIn("rollingHash = ((rollingHash * 31) + data[i]) >>> 0;", source)

    def test_applet_rechecks_clipboard_text_before_keyboard_spawn(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn(
            "_spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback)",
            source,
        )
        self.assertIn(
            "_spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback)",
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
            'if (!restored) {\n        this._setClipboardText(text);\n        this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);\n        completeOnce(false);\n        return;\n      }',
            fn_body,
        )
        self.assertIn(
            'this._setStatus("error", _("Copied to clipboard; automatic paste command could not be started"), transcript);\n        completeOnce(false);',
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
        self.assertIn('let zenity = this._findTrustedProgramInPath("zenity");', source)
        self.assertIn("Gio.SubprocessFlags.STDIN_PIPE", source)
        self.assertIn("this._runBoundedSubprocess(args, {}, {", source)
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
        self.assertIn("this._insertTranscriptText(text);", source)
        self.assertIn("this._preparedTranscriptText(text, true)", source)
        self.assertIn("this._preparedTranscriptText(this.lastTranscript, true)", source)

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
        self.assertIn("let deleted = this._cleanupCount(payload, false);", source)

    def test_voice_model_menu_can_return_to_automatic_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('_("Automatic voice model")', source)
        self.assertIn("_selectAutomaticVoiceBackend: function()", source)
        self.assertIn('this.settings.setValue("transcriber", this.transcriber)', source)
        self.assertIn('this.settings.setValue("whisper-model", this.whisperModel)', source)
        self.assertIn('this._setStatus("ready", _("Voice model: automatic"), this.lastTranscript)', source)
        self.assertIn("this._refreshModelMenu();", source)

    def test_voice_model_remove_status_does_not_render_backend_path_message(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('this._setStatus("done", _("Removed model: ") + name, this.lastTranscript);', source)
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
