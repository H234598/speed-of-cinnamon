const Applet = imports.ui.applet;
const Main = imports.ui.main;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const St = imports.gi.St;
const Util = imports.misc.util;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const Mainloop = imports.mainloop;

const UUID = "speed-of-cinnamon@H234598";
const HOTKEY_ID = "speed-of-cinnamon-toggle";
const PRIMARY_HOTKEY_ID = "speed-of-cinnamon-primary-language";
const SECONDARY_HOTKEY_ID = "speed-of-cinnamon-secondary-language";
const DEFAULT_CLI = GLib.build_filenamev([GLib.get_home_dir(), ".local", "bin", "speed-of-cinnamon"]);
const SYSTEM_CLI = "/usr/bin/speed-of-cinnamon";
const RUNBOOK_URL = "https://gist.github.com/H234598/b95129e13ac0b09c9777edd41aeedfa0";
const PASTE_FOCUS_DELAY_MS = 120;
const ALARM_CHECK_SECONDS = 60;
const MAX_CLI_ARG_BYTES = 4096;
const MAX_CLI_ARG_COUNT = 128;
const MAX_CLI_COMMAND_BYTES = 32768;
const MAX_TEXT_INSERT_CHARS = 120000;
const MAX_TYPE_COMMAND_CHARS = 4000;
const MAX_SPAWN_JSON_BYTES = 262144;
const CLI_COMMAND_TIMEOUT_MS = 300000;
const STATUS_COMMAND_TIMEOUT_MS = 10000;
const DOCTOR_COMMAND_TIMEOUT_MS = 20000;
const PANEL_STATUS_CLASSES = [
  "speed-of-cinnamon-recording",
  "speed-of-cinnamon-processing",
  "speed-of-cinnamon-ready",
  "speed-of-cinnamon-recorded",
  "speed-of-cinnamon-error",
  "speed-of-cinnamon-setup"
];
const OUTPUT_METHODS = [
  "clipboard-paste",
  "clipboard",
  "type",
  "none"
];
const RECORDER_METHODS = [
  "auto",
  "pw-record",
  "parecord",
  "arecord"
];
const RECORDING_LIMIT_SECONDS = [
  15,
  30,
  60,
  120,
  300
];
const EXPORTABLE_SETTINGS = [
  ["toggle-keybinding", "toggleKeybinding"],
  ["primary-language-keybinding", "primaryLanguageKeybinding"],
  ["secondary-language-keybinding", "secondaryLanguageKeybinding"],
  ["show-panel-label", "showPanelLabel"],
  ["language", "language"],
  ["secondary-language", "secondaryLanguage"],
  ["max-seconds", "maxSeconds"],
  ["auto-transcribe-timeout", "autoTranscribeTimeout"],
  ["keep-recording-artifacts", "keepRecordingArtifacts"],
  ["recorder", "recorder"],
  ["input-device", "inputDevice"],
  ["personal-context", "personalContext"],
  ["vocabulary", "vocabulary"],
  ["notify-recording", "notifyRecording"],
  ["notify-complete", "notifyComplete"],
  ["notify-error", "notifyError"],
  ["insert-method", "insertMethod"],
  ["append-space", "appendSpace"],
  ["typing-delay-ms", "typingDelayMs"],
  ["sanitize-special-chars", "sanitizeSpecialChars"],
  ["transcriber", "transcriber"],
  ["whisper-model", "whisperModel"],
  ["transcriber-command", "transcriberCommand"],
  ["post-process-backend", "postProcessBackend"],
  ["post-process-command", "postProcessCommand"],
  ["ollama-url", "ollamaUrl"],
  ["ollama-model", "ollamaModel"],
  ["openai-compatible-url", "openaiCompatibleUrl"],
  ["openai-compatible-model", "openaiCompatibleModel"],
  ["post-process-prompt", "postProcessPrompt"]
];

function _(text) {
  return text;
}

function MyApplet(metadata, orientation, panelHeight, instanceId) {
  this._init(metadata, orientation, panelHeight, instanceId);
}

MyApplet.prototype = {
  __proto__: Applet.TextIconApplet.prototype,

  _init: function(metadata, orientation, panelHeight, instanceId) {
    Applet.TextIconApplet.prototype._init.call(this, orientation, panelHeight, instanceId);

    this.metadata = metadata;
    this.orientation = orientation;
    this.instanceId = instanceId;
    this.toggleKeybinding = "<Super>z::";
    this.primaryLanguageKeybinding = "";
    this.secondaryLanguageKeybinding = "";
    this.showPanelLabel = true;
    this.language = "en";
    this.secondaryLanguage = "de";
    this.activeLanguage = "en";
    this.maxSeconds = 30;
    this.autoTranscribeTimeout = true;
    this.keepRecordingArtifacts = false;
    this.recorder = "auto";
    this.inputDevice = "";
    this.insertMethod = "clipboard-paste";
    this.appendSpace = true;
    this.typingDelayMs = 8;
    this.sanitizeSpecialChars = false;
    this.cliPath = "";
    this.transcriber = "auto";
    this.whisperModel = "";
    this.transcriberCommand = "";
    this.postProcessBackend = "command";
    this.postProcessCommand = "";
    this.ollamaUrl = "http://127.0.0.1:11434";
    this.ollamaModel = "";
    this.openaiCompatibleUrl = "http://127.0.0.1:8000/v1";
    this.openaiCompatibleModel = "";
    this.postProcessPrompt = "";
    this.personalContext = "";
    this.vocabulary = "";
    this.notifyRecording = false;
    this.notifyComplete = true;
    this.notifyError = true;
    this.status = "idle";
    this.lastTranscript = "";
    this.lastMessage = "";
    this.isCommandRunning = false;
    this._statusCommandRunning = false;
    this._doctorCommandRunning = false;
    this.notificationSessionActive = false;
    this.lastNotificationKey = "";
    this.autoTranscribeRecordingKey = "";
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = 0;
    this.targetWindow = null;
    this.clipboard = St.Clipboard.get_default();
    this.statusTimer = 0;
    this.displayTimer = 0;
    this.setupCheckTimer = 0;
    this.pasteTimer = 0;
    this.alarmTimer = 0;

    this.set_applet_icon_path(this.metadata.path + "/icon.svg");
    this.set_applet_label("");
    this.set_applet_tooltip(_("Speed of Cinnamon"));

    this.settings = new Settings.AppletSettings(this, UUID, instanceId);
    this._bindSettings();
    this._syncActiveLanguage();
    this._buildMenu();
    this._registerHotkeys();
    this._refreshStatus();
    this._scheduleSetupCheck();
    this._scheduleAlarmCheck(5);
  },

  _bindSettings: function() {
    this.settings.bindProperty(Settings.BindingDirection.IN, "toggle-keybinding", "toggleKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "primary-language-keybinding", "primaryLanguageKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "secondary-language-keybinding", "secondaryLanguageKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "show-panel-label", "showPanelLabel", this._updatePanel, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "language", "language", this._onLanguageSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "secondary-language", "secondaryLanguage", this._onLanguageSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", this._onRecordingLimitSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "auto-transcribe-timeout", "autoTranscribeTimeout", this._onRecordingOptionsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "keep-recording-artifacts", "keepRecordingArtifacts", this._onRecordingOptionsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "recorder", "recorder", this._onRecorderSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "input-device", "inputDevice", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "insert-method", "insertMethod", this._onOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "append-space", "appendSpace", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "typing-delay-ms", "typingDelayMs", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "sanitize-special-chars", "sanitizeSpecialChars", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "cli-path", "cliPath", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "transcriber", "transcriber", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "whisper-model", "whisperModel", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "transcriber-command", "transcriberCommand", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-backend", "postProcessBackend", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-command", "postProcessCommand", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "ollama-url", "ollamaUrl", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "ollama-model", "ollamaModel", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-url", "openaiCompatibleUrl", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-model", "openaiCompatibleModel", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-prompt", "postProcessPrompt", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "personal-context", "personalContext", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "vocabulary", "vocabulary", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "notify-recording", "notifyRecording", this._onNotificationSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "notify-complete", "notifyComplete", this._onNotificationSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "notify-error", "notifyError", this._onNotificationSettingsChanged, null);
  },

  _buildMenu: function() {
    this.menuManager = new PopupMenu.PopupMenuManager(this);
    this.menu = new Applet.AppletPopupMenu(this, this.orientation);
    this.menuManager.addMenu(this.menu);

    this.toggleItem = new PopupMenu.PopupIconMenuItem(_("Start dictation"), "audio-input-microphone-symbolic", St.IconType.SYMBOLIC);
    this.toggleItem.connect("activate", () => this._toggleRecording());
    this.menu.addMenuItem(this.toggleItem);

    this.cancelItem = new PopupMenu.PopupIconMenuItem(_("Cancel recording"), "process-stop-symbolic", St.IconType.SYMBOLIC);
    this.cancelItem.setSensitive(false);
    this.cancelItem.connect("activate", () => this._cancelRecording());
    this.menu.addMenuItem(this.cancelItem);

    this.statusItem = new PopupMenu.PopupMenuItem(_("Status: idle"));
    this.statusItem.setSensitive(false);
    this.menu.addMenuItem(this.statusItem);

    this.languageItem = new PopupMenu.PopupSubMenuMenuItem(_("Language: en"));
    this.languageItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateLanguageMenu();
      }
    });
    this.menu.addMenuItem(this.languageItem);
    this._populateLanguageMenu();

    this.recorderItem = new PopupMenu.PopupSubMenuMenuItem(_("Recorder: Automatic"));
    this.recorderItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecorderMenu();
      }
    });
    this.menu.addMenuItem(this.recorderItem);
    this._populateRecorderMenu();

    this.recordingLimitItem = new PopupMenu.PopupSubMenuMenuItem(_("Duration: 30s"));
    this.recordingLimitItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecordingLimitMenu();
      }
    });
    this.menu.addMenuItem(this.recordingLimitItem);
    this._populateRecordingLimitMenu();

    this.recordingOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording options"));
    this.recordingOptionsItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecordingOptionsMenu();
      }
    });
    this.menu.addMenuItem(this.recordingOptionsItem);
    this._populateRecordingOptionsMenu();

    this.notificationOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Notifications"));
    this.notificationOptionsItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateNotificationOptionsMenu();
      }
    });
    this.menu.addMenuItem(this.notificationOptionsItem);
    this._populateNotificationOptionsMenu();

    this.alarmItem = new PopupMenu.PopupSubMenuMenuItem(_("Alarms"));
    this.alarmItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshAlarmMenu();
      }
    });
    this.menu.addMenuItem(this.alarmItem);
    this._populateAlarmMenu([], _("Open menu to load alarms"));

    this.shortcutItem = new PopupMenu.PopupSubMenuMenuItem(_("Keyboard shortcuts"));
    this.shortcutItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateShortcutMenu();
      }
    });
    this.menu.addMenuItem(this.shortcutItem);
    this._populateShortcutMenu();

    this.outputMethodItem = new PopupMenu.PopupSubMenuMenuItem(_("Output: Clipboard and paste"));
    this.menu.addMenuItem(this.outputMethodItem);
    this._populateOutputMethodMenu();

    this.textOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Text options"));
    this.textOptionsItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateTextOptionsMenu();
      }
    });
    this.menu.addMenuItem(this.textOptionsItem);
    this._populateTextOptionsMenu();

    this.transcriptItem = new PopupMenu.PopupMenuItem(_("No transcript yet"));
    this.transcriptItem.setSensitive(false);
    this.menu.addMenuItem(this.transcriptItem);

    this.copyLastItem = new PopupMenu.PopupIconMenuItem(_("Copy last transcript"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this.copyLastItem.setSensitive(false);
    this.copyLastItem.connect("activate", () => this._copyLastTranscript());
    this.menu.addMenuItem(this.copyLastItem);

    this.insertLastItem = new PopupMenu.PopupIconMenuItem(_("Insert last transcript"), "edit-paste-symbolic", St.IconType.SYMBOLIC);
    this.insertLastItem.setSensitive(false);
    this.insertLastItem.connect("activate", () => this._insertLastTranscript());
    this.menu.addMenuItem(this.insertLastItem);

    this.historyItem = new PopupMenu.PopupSubMenuMenuItem(_("Recent transcripts"));
    this.historyItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshHistory();
      }
    });
    this.menu.addMenuItem(this.historyItem);
    this._populateHistoryMenu([]);

    this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let statusNow = new PopupMenu.PopupIconMenuItem(_("Refresh status"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    statusNow.connect("activate", () => this._refreshStatus());
    this.menu.addMenuItem(statusNow);

    let doctor = new PopupMenu.PopupIconMenuItem(_("Run doctor"), "dialog-information-symbolic", St.IconType.SYMBOLIC);
    doctor.connect("activate", () => this._runDoctor());
    this.menu.addMenuItem(doctor);

    let openSettings = new PopupMenu.PopupIconMenuItem(_("Open applet settings"), "preferences-system-symbolic", St.IconType.SYMBOLIC);
    openSettings.connect("activate", () => this._openAppletSettings());
    this.menu.addMenuItem(openSettings);

    let openGuide = new PopupMenu.PopupIconMenuItem(_("Open setup guide"), "help-browser-symbolic", St.IconType.SYMBOLIC);
    openGuide.connect("activate", () => this._openSetupGuide());
    this.menu.addMenuItem(openGuide);

    let setupPlan = new PopupMenu.PopupIconMenuItem(_("Copy setup plan"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    setupPlan.connect("activate", () => this._copySetupPlan());
    this.menu.addMenuItem(setupPlan);

    let setupCommands = new PopupMenu.PopupIconMenuItem(_("Copy setup commands"), "utilities-terminal-symbolic", St.IconType.SYMBOLIC);
    setupCommands.connect("activate", () => this._copySetupCommands());
    this.menu.addMenuItem(setupCommands);

    let diagnostics = new PopupMenu.PopupIconMenuItem(_("Copy diagnostics"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    diagnostics.connect("activate", () => this._copyDiagnostics());
    this.menu.addMenuItem(diagnostics);

    let saveDiagnostics = new PopupMenu.PopupIconMenuItem(_("Save diagnostics"), "document-save-symbolic", St.IconType.SYMBOLIC);
    saveDiagnostics.connect("activate", () => this._saveDiagnostics());
    this.menu.addMenuItem(saveDiagnostics);

    this.inputSourceItem = new PopupMenu.PopupSubMenuMenuItem(_("Input source"));
    this.inputSourceItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshInputSourceMenu();
      }
    });
    this.menu.addMenuItem(this.inputSourceItem);
    this._populateInputSourceMenu([]);

    this.modelItem = new PopupMenu.PopupSubMenuMenuItem(_("Voice model"));
    this.modelItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshModelMenu();
      }
    });
    this.menu.addMenuItem(this.modelItem);
    this._populateModelMenu([]);

    this.textModelItem = new PopupMenu.PopupSubMenuMenuItem(_("Text model"));
    this.textModelItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshTextModelMenu();
      }
    });
    this.menu.addMenuItem(this.textModelItem);
    this._populateTextModelMenu([], _("Open menu to load local text models"));

    let transcripts = new PopupMenu.PopupIconMenuItem(_("Open transcripts"), "folder-documents-symbolic", St.IconType.SYMBOLIC);
    transcripts.connect("activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_state_dir(), "speed-of-cinnamon", "transcripts"]), _("Opened transcripts"));
    });
    this.menu.addMenuItem(transcripts);

    let cleanupPreview = new PopupMenu.PopupIconMenuItem(_("Preview cleanup"), "edit-find-symbolic", St.IconType.SYMBOLIC);
    cleanupPreview.connect("activate", () => this._previewCleanup());
    this.menu.addMenuItem(cleanupPreview);

    let cleanup = new PopupMenu.PopupIconMenuItem(_("Clean old files"), "edit-clear-symbolic", St.IconType.SYMBOLIC);
    cleanup.connect("activate", () => this._cleanupOldFiles());
    this.menu.addMenuItem(cleanup);

    let exportSettings = new PopupMenu.PopupIconMenuItem(_("Export settings"), "document-save-symbolic", St.IconType.SYMBOLIC);
    exportSettings.connect("activate", () => this._exportSettings());
    this.menu.addMenuItem(exportSettings);

    let importSettings = new PopupMenu.PopupIconMenuItem(_("Import settings"), "document-open-symbolic", St.IconType.SYMBOLIC);
    importSettings.connect("activate", () => this._importSettings());
    this.menu.addMenuItem(importSettings);
  },

  _hotkeyName: function(id) {
    return id + "-" + this.instanceId;
  },

  _registerHotkey: function(id, binding, callback) {
    let name = this._hotkeyName(id);
    Main.keybindingManager.removeHotKey(name);
    let accelerator = String(binding || "").trim();
    if (accelerator === "") {
      return;
    }
    Main.keybindingManager.addHotKey(name, accelerator, callback);
  },

  _registerHotkeys: function() {
    this._registerHotkey(HOTKEY_ID, this.toggleKeybinding, () => {
      this._rememberFocusedWindow();
      this._toggleRecording();
    });
    this._registerHotkey(PRIMARY_HOTKEY_ID, this.primaryLanguageKeybinding, () => {
      this._rememberFocusedWindow();
      this._startWithLanguage(this._primaryLanguage());
    });
    this._registerHotkey(SECONDARY_HOTKEY_ID, this.secondaryLanguageKeybinding, () => {
      this._rememberFocusedWindow();
      this._startWithLanguage(this._secondaryLanguage());
    });
  },

  _onHotkeyChanged: function() {
    this._registerHotkeys();
    this._populateShortcutMenu();
  },

  _onOutputSettingsChanged: function() {
    this.insertMethod = this._normalizeOutputMethod(this.insertMethod);
    this._populateOutputMethodMenu();
    this._updatePanel();
  },

  _onTextOutputSettingsChanged: function() {
    this._populateTextOptionsMenu();
    this._updatePanel();
  },

  _onRecorderSettingsChanged: function() {
    this.recorder = this._normalizeRecorder(this.recorder);
    this._populateRecorderMenu();
    this._updatePanel();
  },

  _onRecordingLimitSettingsChanged: function() {
    this.maxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this._populateRecordingLimitMenu();
    this._updatePanel();
  },

  _onRecordingOptionsChanged: function() {
    this._populateRecordingOptionsMenu();
    this._updatePanel();
  },

  _onNotificationSettingsChanged: function() {
    this._populateNotificationOptionsMenu();
    this._updatePanel();
  },

  on_applet_clicked: function() {
    this._rememberFocusedWindow();
    this.menu.toggle();
  },

  on_applet_removed_from_panel: function() {
    this._clearStatusTimer();
    this._clearDisplayTimer();
    this._clearSetupCheckTimer();
    this._clearPasteTimer();
    this._clearAlarmTimer();
    Main.keybindingManager.removeHotKey(this._hotkeyName(HOTKEY_ID));
    Main.keybindingManager.removeHotKey(this._hotkeyName(PRIMARY_HOTKEY_ID));
    Main.keybindingManager.removeHotKey(this._hotkeyName(SECONDARY_HOTKEY_ID));
    if (this.settings) {
      this.settings.finalize();
    }
  },

  _baseArgs: function(command) {
    let args = [
      this._cliCommand(),
      command,
      "--json",
      "--language", String(this._currentLanguage()),
      "--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds)),
      "--recorder", String(this.recorder || "auto"),
      "--transcriber", String(this.transcriber || "auto"),
      "--post-process-backend", String(this.postProcessBackend || "command"),
      "--insert-method", "none",
      "--typing-delay-ms", String(this.typingDelayMs || 8)
    ];
    if (this.appendSpace) {
      args.push("--append-space");
    }
    if (this.sanitizeSpecialChars) {
      args.push("--sanitize-special-chars");
    }
    if (this.keepRecordingArtifacts) {
      args.push("--keep-recording-artifacts");
    }
    if (this.inputDevice && this.inputDevice.trim() !== "") {
      args.push("--input-device", this.inputDevice);
    }
    if (this.transcriberCommand && this.transcriberCommand.trim() !== "") {
      args.push("--transcriber-command", this.transcriberCommand);
    }
    if (this.postProcessCommand && this.postProcessCommand.trim() !== "") {
      args.push("--post-process-command", this.postProcessCommand);
    }
    if (this.ollamaUrl && this.ollamaUrl.trim() !== "") {
      args.push("--ollama-url", this.ollamaUrl);
    }
    if (this.ollamaModel && this.ollamaModel.trim() !== "") {
      args.push("--ollama-model", this.ollamaModel);
    }
    if (this.openaiCompatibleUrl && this.openaiCompatibleUrl.trim() !== "") {
      args.push("--openai-compatible-url", this.openaiCompatibleUrl);
    }
    if (this.openaiCompatibleModel && this.openaiCompatibleModel.trim() !== "") {
      args.push("--openai-compatible-model", this.openaiCompatibleModel);
    }
    if (this.postProcessPrompt && this.postProcessPrompt.trim() !== "") {
      args.push("--post-process-prompt", this.postProcessPrompt);
    }
    if (this.whisperModel && this.whisperModel.trim() !== "") {
      args.push("--whisper-model", this.whisperModel);
    }
    if (this.personalContext && this.personalContext.trim() !== "") {
      args.push("--personal-context", this.personalContext);
    }
    if (this.vocabulary && this.vocabulary.trim() !== "") {
      args.push("--vocabulary", this.vocabulary);
    }
    return args;
  },

  _statusArgs: function() {
    return [this._cliCommand(), "status", "--json"];
  },

  _doctorArgs: function() {
    return [this._cliCommand(), "doctor", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshot()), "--json"];
  },

  _setupArgs: function() {
    return [this._cliCommand(), "setup", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshot()), "--json"];
  },

  _diagnosticsArgs: function() {
    return [this._cliCommand(), "diagnostics", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshot()), "--json"];
  },

  _diagnosticsSaveArgs: function() {
    return [this._cliCommand(), "diagnostics", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshot()), "--save", "--json"];
  },

  _alarmListArgs: function() {
    return [this._cliCommand(), "alarms", "list", "--json"];
  },

  _alarmCheckArgs: function() {
    return [this._cliCommand(), "alarms", "check", "--mark", "--json"];
  },

  _alarmEnableArgs: function(id, enabled) {
    return [this._cliCommand(), "alarms", enabled ? "enable" : "disable", String(id || ""), "--json"];
  },

  _alarmRemoveArgs: function(id) {
    return [this._cliCommand(), "alarms", "remove", String(id || ""), "--json"];
  },

  _cancelArgs: function() {
    return [this._cliCommand(), "cancel", "--json"];
  },

  _historyArgs: function() {
    return [this._cliCommand(), "history", "--limit", "5", "--json"];
  },

  _cleanupArgs: function() {
    return [this._cliCommand(), "cleanup", "--keep-transcripts", "100", "--keep-recordings", "25", "--json"];
  },

  _cleanupPreviewArgs: function() {
    return [this._cliCommand(), "cleanup", "--keep-transcripts", "100", "--keep-recordings", "25", "--dry-run", "--json"];
  },

  _listInputsArgs: function() {
    return [this._cliCommand(), "list-inputs", "--json"];
  },

  _modelsArgs: function() {
    return [this._cliCommand(), "models", "--json"];
  },

  _textModelsArgs: function() {
    let args = [this._cliCommand(), "text-models", "--json"];
    let backend = String(this.postProcessBackend || "");
    if (backend === "openai-compatible") {
      args.push("--backend", "openai-compatible");
      if (this.openaiCompatibleUrl && this.openaiCompatibleUrl.trim() !== "") {
        args.push("--openai-compatible-url", this.openaiCompatibleUrl);
      }
      return args;
    }
    args.push("--backend", "ollama");
    if (this.ollamaUrl && this.ollamaUrl.trim() !== "") {
      args.push("--ollama-url", this.ollamaUrl);
    }
    return args;
  },

  _downloadModelArgs: function(model) {
    return [this._cliCommand(), "download-model", String(model || "tiny.en"), "--json"];
  },

  _removeModelArgs: function(model) {
    return [this._cliCommand(), "remove-model", String(model || "tiny.en"), "--json"];
  },

  _settingsExportArgs: function() {
    return [this._cliCommand(), "settings-export", "--settings-json", JSON.stringify(this._settingsSnapshot()), "--json"];
  },

  _settingsImportArgs: function() {
    return [this._cliCommand(), "settings-import", "--json"];
  },

  _cliCommand: function() {
    let configured = String(this.cliPath || "").trim();
    if (configured !== "") {
      if (configured.indexOf("~/") === 0) {
        configured = GLib.build_filenamev([GLib.get_home_dir(), configured.substring(2)]);
      }
      return configured;
    }
    if (GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE)) {
      return DEFAULT_CLI;
    }
    if (GLib.file_test(SYSTEM_CLI, GLib.FileTest.IS_EXECUTABLE)) {
      return SYSTEM_CLI;
    }
    return "speed-of-cinnamon";
  },

  _outputMethodLabel: function(method) {
    if (method === "clipboard") return _("Clipboard only");
    if (method === "type") return _("Direct typing");
    if (method === "none") return _("Do not insert");
    return _("Clipboard and paste");
  },

  _normalizeOutputMethod: function(method) {
    let value = String(method || "").trim();
    return OUTPUT_METHODS.indexOf(value) >= 0 ? value : "clipboard-paste";
  },

  _recorderLabel: function(method) {
    if (method === "pw-record") return _("PipeWire pw-record");
    if (method === "parecord") return _("PulseAudio parecord");
    if (method === "arecord") return _("ALSA arecord");
    return _("Automatic");
  },

  _normalizeRecorder: function(method) {
    let value = String(method || "").trim();
    return RECORDER_METHODS.indexOf(value) >= 0 ? value : "auto";
  },

  _populateRecorderMenu: function() {
    if (!this.recorderItem) {
      return;
    }
    this.recorderItem.menu.removeAll();
    let current = this._normalizeRecorder(this.recorder);
    for (let method of RECORDER_METHODS) {
      let label = (current === method ? "[x] " : "[ ] ") + this._recorderLabel(method);
      let item = new PopupMenu.PopupMenuItem(label);
      item.connect("activate", () => this._selectRecorder(method));
      this.recorderItem.menu.addMenuItem(item);
    }
  },

  _selectRecorder: function(method) {
    this.recorder = this._normalizeRecorder(method);
    this.settings.setValue("recorder", this.recorder);
    this._populateRecorderMenu();
    let label = this._recorderLabel(this.recorder);
    if (this._hasActiveRecordingState()) {
      this.lastMessage = _("Recorder for next recording: ") + label;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", _("Recorder: ") + label, this.lastTranscript);
  },

  _normalizeRecordingLimit: function(seconds) {
    let value = Math.floor(Number(seconds || 30));
    if (!isFinite(value)) {
      value = 30;
    }
    return Math.max(5, Math.min(300, value));
  },

  _populateRecordingLimitMenu: function() {
    if (!this.recordingLimitItem) {
      return;
    }
    this.recordingLimitItem.menu.removeAll();
    let current = this._normalizeRecordingLimit(this.maxSeconds);
    let hasPreset = RECORDING_LIMIT_SECONDS.indexOf(current) >= 0;
    if (!hasPreset) {
      let currentItem = new PopupMenu.PopupMenuItem("[x] " + _("Current: ") + this._formatSeconds(current));
      currentItem.setSensitive(false);
      this.recordingLimitItem.menu.addMenuItem(currentItem);
      this.recordingLimitItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    }
    for (let seconds of RECORDING_LIMIT_SECONDS) {
      let label = (current === seconds ? "[x] " : "[ ] ") + this._formatSeconds(seconds);
      let item = new PopupMenu.PopupMenuItem(label);
      item.connect("activate", () => this._selectRecordingLimit(seconds));
      this.recordingLimitItem.menu.addMenuItem(item);
    }
  },

  _selectRecordingLimit: function(seconds) {
    this.maxSeconds = this._normalizeRecordingLimit(seconds);
    this.settings.setValue("max-seconds", this.maxSeconds);
    this._populateRecordingLimitMenu();
    let label = this._formatSeconds(this.maxSeconds);
    if (this._hasActiveRecordingState()) {
      this.lastMessage = _("Duration for next recording: ") + label;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", _("Duration: ") + label, this.lastTranscript);
  },

  _populateRecordingOptionsMenu: function() {
    if (!this.recordingOptionsItem) {
      return;
    }
    this.recordingOptionsItem.menu.removeAll();

    let autoTranscribe = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.autoTranscribeTimeout), _("Auto-transcribe at time limit")));
    autoTranscribe.connect("activate", () => this._toggleAutoTranscribeTimeout());
    this.recordingOptionsItem.menu.addMenuItem(autoTranscribe);

    let keepArtifacts = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.keepRecordingArtifacts), _("Keep recording files")));
    keepArtifacts.connect("activate", () => this._toggleKeepRecordingArtifacts());
    this.recordingOptionsItem.menu.addMenuItem(keepArtifacts);
  },

  _setRecordingOptionStatus: function(message) {
    if (this._hasActiveRecordingState()) {
      this.lastMessage = message;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _toggleAutoTranscribeTimeout: function() {
    this.autoTranscribeTimeout = !Boolean(this.autoTranscribeTimeout);
    this.settings.setValue("auto-transcribe-timeout", this.autoTranscribeTimeout);
    this._populateRecordingOptionsMenu();
    this._setRecordingOptionStatus(
      this.autoTranscribeTimeout ? _("Auto-transcribe at time limit enabled") : _("Auto-transcribe at time limit disabled")
    );
  },

  _toggleKeepRecordingArtifacts: function() {
    this.keepRecordingArtifacts = !Boolean(this.keepRecordingArtifacts);
    this.settings.setValue("keep-recording-artifacts", this.keepRecordingArtifacts);
    this._populateRecordingOptionsMenu();
    this._setRecordingOptionStatus(
      this.keepRecordingArtifacts ? _("Recording files will be kept") : _("Recording files will be discarded")
    );
  },

  _populateNotificationOptionsMenu: function() {
    if (!this.notificationOptionsItem) {
      return;
    }
    this.notificationOptionsItem.menu.removeAll();

    let recording = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.notifyRecording), _("Recording start and limit")));
    recording.connect("activate", () => this._toggleNotifyRecording());
    this.notificationOptionsItem.menu.addMenuItem(recording);

    let complete = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.notifyComplete), _("Dictation complete")));
    complete.connect("activate", () => this._toggleNotifyComplete());
    this.notificationOptionsItem.menu.addMenuItem(complete);

    let errors = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.notifyError), _("Dictation errors")));
    errors.connect("activate", () => this._toggleNotifyError());
    this.notificationOptionsItem.menu.addMenuItem(errors);
  },

  _setNotificationOptionStatus: function(message) {
    if (this._hasActiveRecordingState()) {
      this.lastMessage = message;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _toggleNotifyRecording: function() {
    this.notifyRecording = !Boolean(this.notifyRecording);
    this.settings.setValue("notify-recording", this.notifyRecording);
    this._populateNotificationOptionsMenu();
    this._setNotificationOptionStatus(
      this.notifyRecording ? _("Recording notifications enabled") : _("Recording notifications disabled")
    );
  },

  _toggleNotifyComplete: function() {
    this.notifyComplete = !Boolean(this.notifyComplete);
    this.settings.setValue("notify-complete", this.notifyComplete);
    this._populateNotificationOptionsMenu();
    this._setNotificationOptionStatus(
      this.notifyComplete ? _("Completion notifications enabled") : _("Completion notifications disabled")
    );
  },

  _toggleNotifyError: function() {
    this.notifyError = !Boolean(this.notifyError);
    this.settings.setValue("notify-error", this.notifyError);
    this._populateNotificationOptionsMenu();
    this._setNotificationOptionStatus(
      this.notifyError ? _("Error notifications enabled") : _("Error notifications disabled")
    );
  },

  _populateOutputMethodMenu: function() {
    if (!this.outputMethodItem) {
      return;
    }
    this.outputMethodItem.menu.removeAll();
    let current = this._normalizeOutputMethod(this.insertMethod);
    for (let method of OUTPUT_METHODS) {
      let label = (current === method ? "[x] " : "[ ] ") + this._outputMethodLabel(method);
      let item = new PopupMenu.PopupMenuItem(label);
      item.connect("activate", () => this._selectOutputMethod(method));
      this.outputMethodItem.menu.addMenuItem(item);
    }
  },

  _selectOutputMethod: function(method) {
    this.insertMethod = this._normalizeOutputMethod(method);
    this.settings.setValue("insert-method", this.insertMethod);
    this._populateOutputMethodMenu();
    let message = _("Output: ") + this._outputMethodLabel(this.insertMethod);
    if (this.status === "recording" || this.status === "processing") {
      this.lastMessage = message;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _optionLabel: function(enabled, label) {
    return (enabled ? "[x] " : "[ ] ") + label;
  },

  _populateTextOptionsMenu: function() {
    if (!this.textOptionsItem) {
      return;
    }
    this.textOptionsItem.menu.removeAll();
    let append = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.appendSpace), _("Append trailing space")));
    append.connect("activate", () => this._toggleAppendSpace());
    this.textOptionsItem.menu.addMenuItem(append);

    let sanitize = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.sanitizeSpecialChars), _("Replace accents before output")));
    sanitize.connect("activate", () => this._toggleSanitizeSpecialChars());
    this.textOptionsItem.menu.addMenuItem(sanitize);
  },

  _setTextOptionStatus: function(message) {
    if (this.status === "recording" || this.status === "processing") {
      this.lastMessage = message;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _toggleAppendSpace: function() {
    this.appendSpace = !Boolean(this.appendSpace);
    this.settings.setValue("append-space", this.appendSpace);
    this._populateTextOptionsMenu();
    this._setTextOptionStatus(this.appendSpace ? _("Append trailing space enabled") : _("Append trailing space disabled"));
  },

  _toggleSanitizeSpecialChars: function() {
    this.sanitizeSpecialChars = !Boolean(this.sanitizeSpecialChars);
    this.settings.setValue("sanitize-special-chars", this.sanitizeSpecialChars);
    this._populateTextOptionsMenu();
    this._setTextOptionStatus(
      this.sanitizeSpecialChars ? _("Accent replacement enabled") : _("Accent replacement disabled")
    );
  },

  _normalizeLanguage: function(value, fallback) {
    let language = String(value || "").trim();
    return language === "" ? fallback : language;
  },

  _currentLanguage: function() {
    return this._normalizeLanguage(this.activeLanguage, this._normalizeLanguage(this.language, "en"));
  },

  _primaryLanguage: function() {
    return this._normalizeLanguage(this.language, "en");
  },

  _secondaryLanguage: function() {
    return this._normalizeLanguage(this.secondaryLanguage, this._primaryLanguage());
  },

  _syncActiveLanguage: function() {
    let primary = this._primaryLanguage();
    let secondary = this._secondaryLanguage();
    let current = this._currentLanguage();
    if (current !== primary && current !== secondary) {
      this.activeLanguage = primary;
    }
  },

  _onLanguageSettingsChanged: function() {
    this._syncActiveLanguage();
    this._populateLanguageMenu();
    this._updatePanel();
  },

  _hasActiveRecordingState: function() {
    return this.status === "recording" || this.status === "recorded" || this.status === "processing";
  },

  _setActiveLanguage: function(language, message) {
    let nextLanguage = this._normalizeLanguage(language, this._primaryLanguage());
    if (this._hasActiveRecordingState()) {
      this._setStatus(this.status, _("Finish the current recording before changing language"), this.lastTranscript);
      return false;
    }
    this.activeLanguage = nextLanguage;
    this._setStatus("ready", message || _("Language: ") + this._currentLanguage(), this.lastTranscript);
    return true;
  },

  _switchLanguage: function() {
    let primary = this._primaryLanguage();
    let secondary = this._secondaryLanguage();
    let nextLanguage = this._currentLanguage() === primary ? secondary : primary;
    this._setActiveLanguage(nextLanguage, _("Language: ") + nextLanguage);
  },

  _startWithLanguage: function(language) {
    if (!this._hasActiveRecordingState()) {
      this.activeLanguage = this._normalizeLanguage(language, this._primaryLanguage());
      this._updatePanel();
    }
    this._toggleRecording();
  },

  _populateLanguageMenu: function() {
    if (!this.languageItem) {
      return;
    }
    this.languageItem.menu.removeAll();
    let primary = this._primaryLanguage();
    let secondary = this._secondaryLanguage();
    let current = this._currentLanguage();

    let selectPrimary = new PopupMenu.PopupMenuItem((current === primary ? "[x] " : "[ ] ") + _("Use primary: ") + primary);
    selectPrimary.connect("activate", () => this._setActiveLanguage(primary, _("Language: ") + primary));
    this.languageItem.menu.addMenuItem(selectPrimary);

    let selectSecondary = new PopupMenu.PopupMenuItem((current === secondary ? "[x] " : "[ ] ") + _("Use secondary: ") + secondary);
    selectSecondary.connect("activate", () => this._setActiveLanguage(secondary, _("Language: ") + secondary));
    this.languageItem.menu.addMenuItem(selectSecondary);

    this.languageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let startPrimary = new PopupMenu.PopupIconMenuItem(_("Start primary: ") + primary, "media-record-symbolic", St.IconType.SYMBOLIC);
    startPrimary.connect("activate", () => this._startWithLanguage(primary));
    this.languageItem.menu.addMenuItem(startPrimary);

    let startSecondary = new PopupMenu.PopupIconMenuItem(_("Start secondary: ") + secondary, "media-record-symbolic", St.IconType.SYMBOLIC);
    startSecondary.connect("activate", () => this._startWithLanguage(secondary));
    this.languageItem.menu.addMenuItem(startSecondary);

    this.languageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let switchItem = new PopupMenu.PopupIconMenuItem(_("Switch primary/secondary"), "preferences-desktop-locale-symbolic", St.IconType.SYMBOLIC);
    switchItem.connect("activate", () => this._switchLanguage());
    this.languageItem.menu.addMenuItem(switchItem);
  },

  _formatKeybinding: function(binding) {
    let value = String(binding || "").trim();
    if (value === "") {
      return _("not set");
    }
    return value.replace(/::$/, "");
  },

  _shortcutRows: function() {
    return [
      [_("Start or stop dictation"), this._formatKeybinding(this.toggleKeybinding)],
      [_("Start primary language"), this._formatKeybinding(this.primaryLanguageKeybinding)],
      [_("Start secondary language"), this._formatKeybinding(this.secondaryLanguageKeybinding)],
      [_("Cancel recording"), _("Applet menu only")],
      [_("Switch language"), _("Applet menu only")]
    ];
  },

  _populateShortcutMenu: function() {
    if (!this.shortcutItem) {
      return;
    }
    this.shortcutItem.menu.removeAll();
    for (let row of this._shortcutRows()) {
      let item = new PopupMenu.PopupMenuItem(row[0] + ": " + row[1]);
      item.setSensitive(false);
      this.shortcutItem.menu.addMenuItem(item);
    }
    this.shortcutItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let copy = new PopupMenu.PopupIconMenuItem(_("Copy shortcut reference"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    copy.connect("activate", () => this._copyShortcutReference());
    this.shortcutItem.menu.addMenuItem(copy);
  },

  _shortcutReferenceText: function() {
    let lines = [_("Speed of Cinnamon keyboard shortcuts")];
    for (let row of this._shortcutRows()) {
      lines.push(row[0] + ": " + row[1]);
    }
    return lines.join("\n");
  },

  _copyShortcutReference: function() {
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._shortcutReferenceText());
    this._setStatus("done", _("Copied shortcut reference"), this.lastTranscript);
  },

  _toggleRecording: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.notificationSessionActive = true;
    this.lastNotificationKey = "";
    this.autoTranscribeRecordingKey = "";
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = Number(this.maxSeconds || 30);
    this.isCommandRunning = true;
    this._setStatus("processing", _("Working..."), "");
    this._spawnJson(this._baseArgs("toggle"), (payload) => {
      this.isCommandRunning = false;
      this._applyPayload(payload);
    });
  },

  _refreshStatus: function() {
    if (this._statusCommandRunning) {
      return;
    }
    this._statusCommandRunning = true;
    this._spawnJson(this._statusArgs(), (payload) => {
      try {
        this._applyPayload(payload);
      } finally {
        this._statusCommandRunning = false;
      }
    }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS });
  },

  _cancelRecording: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.isCommandRunning = true;
    this.autoTranscribeRecordingKey = "";
    this._setStatus("processing", _("Cancelling..."), this.lastTranscript);
    this._spawnJson(this._cancelArgs(), (payload) => {
      this.isCommandRunning = false;
      this._applyPayload(payload);
    });
  },

  _runDoctor: function(startupCheck) {
    if (this._doctorCommandRunning) {
      return;
    }
    this._doctorCommandRunning = true;
    this._spawnJson(this._doctorArgs(), (payload) => {
      try {
        if (payload.configured) {
          this._applyDoctorPayload(payload, Boolean(startupCheck));
          return;
        }
        this._applyLegacyDoctorPayload(payload, Boolean(startupCheck));
      } finally {
        this._doctorCommandRunning = false;
      }
    }, { timeoutMs: DOCTOR_COMMAND_TIMEOUT_MS });
  },

  _applyDoctorPayload: function(payload, startupCheck) {
    let configured = payload.configured || {};
    let missing = [];
    for (let name of ["recorder", "transcriber", "output", "postprocessor"]) {
      let section = configured[name] || {};
      if (!section.ok) {
        missing.push(name + ": " + (section.detail || "not ready"));
      }
    }
    if (!payload.ok) {
      let message = _("Setup needed: ") + missing.join("; ");
      this._setStatus(startupCheck ? "setup" : "error", message, this.lastTranscript);
      return;
    }
    let warnings = configured.warnings || [];
    if (warnings.length > 0) {
      this._setStatus("ready", _("Doctor: ready; ") + warnings.join("; "), this.lastTranscript);
      return;
    }
    this._setStatus("ready", _("Doctor: configured pipeline ready"), this.lastTranscript);
  },

  _applyLegacyDoctorPayload: function(payload, startupCheck) {
    let missing = [];
    for (let check of payload.checks || []) {
      if (!check.ok) {
        missing.push(check.name);
      }
    }
    if (payload.ok) {
      this._setStatus("ready", _("Doctor: core OK; optional missing: ") + missing.join(", "), this.lastTranscript);
    } else {
      this._setStatus(startupCheck ? "setup" : "error", _("Missing: ") + missing.join(", "), this.lastTranscript);
    }
  },

  _openAppletSettings: function() {
    if (!GLib.find_program_in_path("cinnamon-settings")) {
      this._setStatus("error", _("cinnamon-settings command not found"), this.lastTranscript);
      return;
    }
    Util.spawn(["cinnamon-settings", "applets"]);
    this._setStatus("ready", _("Opened Cinnamon applet settings"), this.lastTranscript);
  },

  _openSetupGuide: function() {
    this._openUri(RUNBOOK_URL, _("Opened setup guide"));
  },

  _openUri: function(uri, successMessage) {
    try {
      Gio.AppInfo.launch_default_for_uri(uri, null);
      this._setStatus("ready", successMessage, this.lastTranscript);
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Could not open link: ") + err.message, this.lastTranscript);
    }
  },

  _openFolder: function(path, successMessage) {
    try {
      GLib.mkdir_with_parents(path, 0o755);
      if (!GLib.file_test(path, GLib.FileTest.IS_DIR)) {
        throw new Error("folder is not available: " + path);
      }
      this._openUri(GLib.filename_to_uri(path, null), successMessage);
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Could not open folder: ") + err.message, this.lastTranscript);
    }
  },

  _copySetupPlan: function() {
    this._spawnJson(this._setupArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, String(payload.text || JSON.stringify(payload, null, 2)));
      this._setStatus("done", _("Copied setup plan"), this.lastTranscript);
    });
  },

  _setupCommandsText: function(payload) {
    let commands = payload.commands || [];
    if (!Array.isArray(commands)) {
      return "";
    }

    let seen = {};
    let lines = [];
    for (let i = 0; i < commands.length; i++) {
      let text = String(commands[i] || "").trim();
      if (text === "" || seen[text]) {
        continue;
      }
      seen[text] = true;
      lines.push(text);
    }
    return lines.join("\n");
  },

  _copySetupCommands: function() {
    this._spawnJson(this._setupArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }

      let text = this._setupCommandsText(payload);
      if (text === "") {
        this._setStatus("ready", _("No setup commands needed"), this.lastTranscript);
        return;
      }

      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
      this._setStatus("done", _("Copied setup commands"), this.lastTranscript);
    });
  },

  _copyDiagnostics: function() {
    this._spawnJson(this._diagnosticsArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, JSON.stringify(payload, null, 2));
      this._setStatus("done", _("Copied diagnostics"), this.lastTranscript);
    });
  },

  _saveDiagnostics: function() {
    this._spawnJson(this._diagnosticsSaveArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let path = payload.saved_path || "";
      if (path) {
        this.clipboard.set_text(St.ClipboardType.CLIPBOARD, path);
      }
      this._setStatus("done", _("Saved diagnostics: ") + path, this.lastTranscript);
    });
  },

  _setAlarmOptionStatus: function(message) {
    if (this.status === "recording" || this.status === "processing") {
      this.lastMessage = message;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _refreshAlarmMenu: function() {
    if (!this.alarmItem) {
      return;
    }
    this._populateAlarmMenu([], _("Loading alarms..."));
    this._spawnJson(this._alarmListArgs(), (payload) => {
      if (payload.error) {
        this._populateAlarmMenu([], payload.error);
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._populateAlarmMenu(payload.alarms || [], payload.summary || "");
    });
  },

  _populateAlarmMenu: function(alarms, summary, message) {
    if (!this.alarmItem) {
      return;
    }
    this.alarmItem.menu.removeAll();

    let summaryText = message || summary || _("No alarms configured");
    let summaryItem = new PopupMenu.PopupMenuItem(summaryText);
    summaryItem.setSensitive(false);
    this.alarmItem.menu.addMenuItem(summaryItem);

    let checkNow = new PopupMenu.PopupIconMenuItem(_("Check alarms now"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    checkNow.connect("activate", () => this._checkAlarms(true));
    this.alarmItem.menu.addMenuItem(checkNow);

    let copyCommands = new PopupMenu.PopupIconMenuItem(_("Copy alarm commands"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    copyCommands.connect("activate", () => this._copyAlarmCommands());
    this.alarmItem.menu.addMenuItem(copyCommands);

    let openFolder = new PopupMenu.PopupIconMenuItem(_("Open alarm store"), "folder-symbolic", St.IconType.SYMBOLIC);
    openFolder.connect("activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon"]), _("Opened alarm store"));
    });
    this.alarmItem.menu.addMenuItem(openFolder);

    this.alarmItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (message) {
      return;
    }
    if (!alarms || alarms.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No alarms configured"));
      empty.setSensitive(false);
      this.alarmItem.menu.addMenuItem(empty);
      return;
    }
    for (let alarm of alarms) {
      this._addAlarmMenuEntry(alarm);
    }
  },

  _addAlarmMenuEntry: function(alarm) {
    let id = String(alarm.id || "");
    if (id === "") {
      return;
    }
    let label = (alarm.enabled ? "[x] " : "[ ] ") + String(alarm.label || alarm.time || id);
    let summary = String(alarm.summary || "");
    if (summary !== "") {
      label += " - " + summary;
    }
    let entry = new PopupMenu.PopupSubMenuMenuItem(label);
    this.alarmItem.menu.addMenuItem(entry);

    let details = new PopupMenu.PopupMenuItem(id);
    details.setSensitive(false);
    entry.menu.addMenuItem(details);

    let toggle = new PopupMenu.PopupIconMenuItem(alarm.enabled ? _("Disable alarm") : _("Enable alarm"), alarm.enabled ? "media-playback-pause-symbolic" : "media-playback-start-symbolic", St.IconType.SYMBOLIC);
    toggle.connect("activate", () => this._setAlarmEnabled(id, !alarm.enabled));
    entry.menu.addMenuItem(toggle);

    let remove = new PopupMenu.PopupIconMenuItem(_("Remove alarm"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
    remove.connect("activate", () => this._removeAlarm(id));
    entry.menu.addMenuItem(remove);
  },

  _copyAlarmCommands: function() {
    let text = [
      "speed-of-cinnamon alarms add --time 09:00 --name \"Standup\" --days weekdays --json",
      "speed-of-cinnamon alarms list --json",
      "speed-of-cinnamon alarms check --mark --json"
    ].join("\n");
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
    this._setStatus("done", _("Copied alarm commands"), this.lastTranscript);
  },

  _setAlarmEnabled: function(id, enabled) {
    this._setAlarmOptionStatus(enabled ? _("Enabling alarm...") : _("Disabling alarm..."));
    this._spawnJson(this._alarmEnableArgs(id, enabled), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._setAlarmOptionStatus(enabled ? _("Alarm enabled") : _("Alarm disabled"));
      this._refreshAlarmMenu();
    });
  },

  _removeAlarm: function(id) {
    this._setAlarmOptionStatus(_("Removing alarm..."));
    this._spawnJson(this._alarmRemoveArgs(id), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._setAlarmOptionStatus(payload.removed ? _("Alarm removed") : _("Alarm not found"));
      this._refreshAlarmMenu();
    });
  },

  _checkAlarms: function(manual) {
    this._spawnJson(this._alarmCheckArgs(), (payload) => {
      if (payload.error) {
        if (manual) {
          this._setStatus("error", payload.error, this.lastTranscript);
        }
        return;
      }
      let due = payload.due || [];
      for (let alarm of due) {
        if (alarm.notify === false) {
          continue;
        }
        this._notify(_("Speed of Cinnamon alarm"), alarm.body || alarm.label || _("Alarm due"), Boolean(alarm.critical));
      }
      if (due.length > 0) {
        let first = due[0] || {};
        if (manual || this.status === "idle" || this.status === "ready" || this.status === "done") {
          this._setAlarmOptionStatus(_("Alarm: ") + String(first.label || first.time || due.length));
        }
      } else if (manual) {
        this._setAlarmOptionStatus(_("No alarms due"));
      }
      if (manual) {
        this._refreshAlarmMenu();
      }
    });
  },

  _refreshInputSourceMenu: function() {
    if (!this.inputSourceItem) {
      return;
    }
    this._populateInputSourceMenu([], _("Loading input sources..."));
    this._spawnJson(this._listInputsArgs(), (payload) => {
      if (payload.error) {
        this._populateInputSourceMenu([], payload.error);
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._populateInputSourceMenu(payload.sources || []);
    });
  },

  _populateInputSourceMenu: function(sources, message) {
    if (!this.inputSourceItem) {
      return;
    }
    this.inputSourceItem.menu.removeAll();
    let current = String(this.inputDevice || "");
    let defaultLabel = (current === "" ? "[x] " : "[ ] ") + _("System default");
    let defaultItem = new PopupMenu.PopupMenuItem(defaultLabel);
    defaultItem.connect("activate", () => this._selectInputSource("", _("system default")));
    this.inputSourceItem.menu.addMenuItem(defaultItem);

    if (message) {
      let messageItem = new PopupMenu.PopupMenuItem(message);
      messageItem.setSensitive(false);
      this.inputSourceItem.menu.addMenuItem(messageItem);
      return;
    }
    if (!sources || sources.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No input sources found"));
      empty.setSensitive(false);
      this.inputSourceItem.menu.addMenuItem(empty);
      return;
    }
    for (let source of sources) {
      let sourceName = String(source.name || "");
      if (sourceName === "") {
        continue;
      }
      let label = source.description || sourceName;
      if (source.default) {
        label += _(" (system default)");
      }
      let itemLabel = (current === sourceName ? "[x] " : "[ ] ") + label;
      let item = new PopupMenu.PopupMenuItem(itemLabel);
      item.connect("activate", () => this._selectInputSource(sourceName, label));
      this.inputSourceItem.menu.addMenuItem(item);
    }
  },

  _selectInputSource: function(name, label) {
    this.inputDevice = String(name || "");
    this.settings.setValue("input-device", this.inputDevice);
    this._refreshInputSourceMenu();
    let message = this.inputDevice === ""
      ? _("Input device: system default")
      : _("Input device: ") + label;
    if (this.status === "recording" || this.status === "processing") {
      this.lastMessage = _("Input device for next recording: ") + label;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _refreshModelMenu: function() {
    if (!this.modelItem) {
      return;
    }
    this._populateModelMenu([], _("Loading voice models..."));
    this._spawnJson(this._modelsArgs(), (payload) => {
      if (payload.error) {
        this._populateModelMenu([], payload.error);
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._populateModelMenu(payload.models || []);
    });
  },

  _populateModelMenu: function(models, message) {
    if (!this.modelItem) {
      return;
    }
    this.modelItem.menu.removeAll();

    let autoActive = String(this.transcriber || "auto") === "auto" && String(this.whisperModel || "") === "";
    let automatic = new PopupMenu.PopupMenuItem((autoActive ? "[x] " : "[ ] ") + _("Automatic ASR backend"));
    automatic.connect("activate", () => this._selectAutomaticVoiceBackend());
    this.modelItem.menu.addMenuItem(automatic);

    let download = new PopupMenu.PopupIconMenuItem(_("Download starter model"), "folder-download-symbolic", St.IconType.SYMBOLIC);
    download.connect("activate", () => this._downloadStarterModel());
    this.modelItem.menu.addMenuItem(download);

    let openFolder = new PopupMenu.PopupIconMenuItem(_("Open model folder"), "folder-symbolic", St.IconType.SYMBOLIC);
    openFolder.connect("activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "whisper.cpp"]), _("Opened model folder"));
    });
    this.modelItem.menu.addMenuItem(openFolder);

    this.modelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (message) {
      let messageItem = new PopupMenu.PopupMenuItem(message);
      messageItem.setSensitive(false);
      this.modelItem.menu.addMenuItem(messageItem);
      return;
    }
    if (!models || models.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No models in catalog"));
      empty.setSensitive(false);
      this.modelItem.menu.addMenuItem(empty);
      return;
    }
    for (let model of models) {
      this._addModelMenuEntry(model);
    }
  },

  _addModelMenuEntry: function(model) {
    let name = String(model.name || "");
    if (name === "") {
      return;
    }
    let downloaded = Boolean(model.downloaded);
    let current = downloaded && this.whisperModel && String(model.path || "") === String(this.whisperModel);
    let label = (current ? "[x] " : "[ ] ") + name + " (" + String(model.size || "?") + ")";
    if (!downloaded) {
      label += _(" - not downloaded");
    }
    let entry = new PopupMenu.PopupSubMenuMenuItem(label);
    this.modelItem.menu.addMenuItem(entry);

    let description = new PopupMenu.PopupMenuItem(String(model.description || ""));
    description.setSensitive(false);
    entry.menu.addMenuItem(description);

    if (downloaded) {
      let useItem = new PopupMenu.PopupIconMenuItem(_("Use this model"), "emblem-ok-symbolic", St.IconType.SYMBOLIC);
      useItem.setSensitive(!current);
      useItem.connect("activate", () => this._selectVoiceModel(model));
      entry.menu.addMenuItem(useItem);

      let removeItem = new PopupMenu.PopupIconMenuItem(_("Remove model"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
      removeItem.connect("activate", () => this._removeVoiceModel(model));
      entry.menu.addMenuItem(removeItem);
      return;
    }

    let downloadItem = new PopupMenu.PopupIconMenuItem(_("Download model"), "folder-download-symbolic", St.IconType.SYMBOLIC);
    downloadItem.connect("activate", () => this._downloadVoiceModel(model));
    entry.menu.addMenuItem(downloadItem);
  },

  _downloadStarterModel: function() {
    this._downloadVoiceModel({ name: "tiny.en" });
  },

  _downloadVoiceModel: function(model) {
    if (this.isCommandRunning) {
      return;
    }
    let name = String(model.name || "tiny.en");
    this.isCommandRunning = true;
    this._setStatus("processing", _("Downloading model: ") + name, this.lastTranscript);
    this._spawnJson(this._downloadModelArgs(name), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        this._refreshModelMenu();
        return;
      }
      this._selectVoiceModel(payload);
      this._refreshModelMenu();
    });
  },

  _removeVoiceModel: function(model) {
    if (this.isCommandRunning) {
      return;
    }
    let name = String(model.name || "");
    let path = String(model.path || "");
    if (name === "") {
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Removing model: ") + name, this.lastTranscript);
    this._spawnJson(this._removeModelArgs(name), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        this._refreshModelMenu();
        return;
      }
      if (path !== "" && path === String(this.whisperModel || "")) {
        this.transcriber = "auto";
        this.whisperModel = "";
        this.settings.setValue("transcriber", this.transcriber);
        this.settings.setValue("whisper-model", this.whisperModel);
      }
      this._setStatus("done", payload.message || _("Removed model: ") + name, this.lastTranscript);
      this._refreshModelMenu();
    });
  },

  _selectVoiceModel: function(model) {
    let path = String(model.path || "");
    let name = String(model.name || "whisper.cpp");
    if (path === "") {
      return;
    }
    this.transcriber = "whisper-cpp";
    this.whisperModel = path;
    this.settings.setValue("transcriber", this.transcriber);
    this.settings.setValue("whisper-model", this.whisperModel);
    this._setStatus("ready", _("Voice model: ") + name, this.lastTranscript);
  },

  _selectAutomaticVoiceBackend: function() {
    this.transcriber = "auto";
    this.whisperModel = "";
    this.settings.setValue("transcriber", this.transcriber);
    this.settings.setValue("whisper-model", this.whisperModel);
    this._refreshModelMenu();
    this._setStatus("ready", _("Voice backend: automatic"), this.lastTranscript);
  },

  _refreshTextModelMenu: function() {
    if (!this.textModelItem) {
      return;
    }
    this._populateTextModelMenu([], _("Loading local text models..."));
    this._spawnJson(this._textModelsArgs(), (payload) => {
      if (payload.error) {
        this._populateTextModelMenu([], payload.error);
        return;
      }
      this._populateTextModelMenu(payload.models || [], payload.available === false ? payload.message : "", payload.backend || "ollama");
    });
  },

  _populateTextModelMenu: function(models, message, provider) {
    if (!this.textModelItem) {
      return;
    }
    this.textModelItem.menu.removeAll();
    let backend = String(this.postProcessBackend || "command");
    let activeProvider = String(provider || (backend === "openai-compatible" ? "openai-compatible" : "ollama"));

    let disabled = new PopupMenu.PopupMenuItem((backend === "none" ? "[x] " : "[ ] ") + _("Disabled"));
    disabled.connect("activate", () => this._selectTextModelBackend("none", "", _("Text polishing disabled")));
    this.textModelItem.menu.addMenuItem(disabled);

    let custom = new PopupMenu.PopupMenuItem((backend === "command" || backend === "custom" ? "[x] " : "[ ] ") + _("Custom command"));
    custom.connect("activate", () => this._selectTextModelBackend("command", "", _("Text polishing: custom command")));
    this.textModelItem.menu.addMenuItem(custom);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let ollama = new PopupMenu.PopupMenuItem((backend === "ollama" ? "[x] " : "[ ] ") + _("Ollama local model"));
    ollama.connect("activate", () => this._selectTextModelBackend("ollama", this.ollamaModel, _("Text polishing: Ollama")));
    this.textModelItem.menu.addMenuItem(ollama);

    let openaiCompatible = new PopupMenu.PopupMenuItem((backend === "openai-compatible" ? "[x] " : "[ ] ") + _("OpenAI-compatible local server"));
    openaiCompatible.connect("activate", () => this._selectTextModelBackend("openai-compatible", this.openaiCompatibleModel, _("Text polishing: OpenAI-compatible local server")));
    this.textModelItem.menu.addMenuItem(openaiCompatible);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (message) {
      let messageItem = new PopupMenu.PopupMenuItem(message);
      messageItem.setSensitive(false);
      this.textModelItem.menu.addMenuItem(messageItem);
      return;
    }
    if (!models || models.length === 0) {
      let emptyLabel = activeProvider === "openai-compatible"
        ? _("No OpenAI-compatible local models found")
        : _("No local Ollama models found");
      let empty = new PopupMenu.PopupMenuItem(emptyLabel);
      empty.setSensitive(false);
      this.textModelItem.menu.addMenuItem(empty);
      return;
    }
    for (let model of models) {
      this._addTextModelMenuEntry(model, activeProvider);
    }
  },

  _addTextModelMenuEntry: function(model, backend) {
    let name = String(model.name || "");
    if (name === "") {
      return;
    }
    let provider = String(backend || "ollama");
    let currentModel = provider === "openai-compatible" ? String(this.openaiCompatibleModel || "") : String(this.ollamaModel || "");
    let current = String(this.postProcessBackend || "") === provider && currentModel === name;
    let details = String(model.description || model.size_label || "");
    let label = (current ? "[x] " : "[ ] ") + name;
    if (details !== "") {
      label += " (" + details + ")";
    }
    let item = new PopupMenu.PopupMenuItem(label);
    item.connect("activate", () => this._selectTextModelBackend(provider, name, _("Text model: ") + name));
    this.textModelItem.menu.addMenuItem(item);
  },

  _selectTextModelBackend: function(backend, model, message) {
    this.postProcessBackend = String(backend || "command");
    this.settings.setValue("post-process-backend", this.postProcessBackend);
    if (this.postProcessBackend === "ollama") {
      this.ollamaModel = String(model || "");
      this.settings.setValue("ollama-model", this.ollamaModel);
    }
    if (this.postProcessBackend === "openai-compatible") {
      this.openaiCompatibleModel = String(model || "");
      this.settings.setValue("openai-compatible-model", this.openaiCompatibleModel);
    }
    this._refreshTextModelMenu();
    this._setStatus("ready", message, this.lastTranscript);
  },

  _refreshHistory: function() {
    this._spawnJson(this._historyArgs(), (payload) => {
      if (payload.error) {
        this._populateHistoryMenu([]);
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._populateHistoryMenu(payload.transcripts || []);
    });
  },

  _cleanupCount: function(payload, dryRun) {
    if (dryRun) {
      return Number(payload.would_delete_transcripts || 0) + Number(payload.would_delete_recordings || 0) + Number(payload.would_delete_logs || 0);
    }
    return Number(payload.deleted_transcripts || 0) + Number(payload.deleted_recordings || 0) + Number(payload.deleted_logs || 0);
  },

  _previewCleanup: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Previewing cleanup..."), this.lastTranscript);
    this._spawnJson(this._cleanupPreviewArgs(), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._setStatus("ready", _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), this.lastTranscript);
    });
  },

  _cleanupOldFiles: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Cleaning old files..."), this.lastTranscript);
    this._spawnJson(this._cleanupArgs(), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let deleted = this._cleanupCount(payload, false);
      this._setStatus("done", _("Cleaned old files: ") + String(deleted), this.lastTranscript);
      this._refreshHistory();
    });
  },

  _settingsSnapshot: function() {
    let snapshot = {};
    for (let item of EXPORTABLE_SETTINGS) {
      snapshot[item[0]] = this[item[1]];
    }
    return snapshot;
  },

  _exportSettings: function() {
    this._setStatus("processing", _("Exporting settings..."), this.lastTranscript);
    this._spawnJson(this._settingsExportArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._setStatus("done", _("Exported settings: ") + payload.path, this.lastTranscript);
    });
  },

  _importSettings: function() {
    this._setStatus("processing", _("Importing settings..."), this.lastTranscript);
    this._spawnJson(this._settingsImportArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let applied = this._applyImportedSettings(payload.settings || {});
      this._setStatus("done", _("Imported settings: ") + String(applied), this.lastTranscript);
    });
  },

  _applyImportedSettings: function(settings) {
    let applied = 0;
    for (let item of EXPORTABLE_SETTINGS) {
      let key = item[0];
      let prop = item[1];
      if (!Object.prototype.hasOwnProperty.call(settings, key)) {
        continue;
      }
      this[prop] = settings[key];
      this.settings.setValue(key, settings[key]);
      applied++;
    }
    this._syncActiveLanguage();
    this.recorder = this._normalizeRecorder(this.recorder);
    this._populateRecorderMenu();
    this.maxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this._populateRecordingLimitMenu();
    this._populateRecordingOptionsMenu();
    this._populateNotificationOptionsMenu();
    this.insertMethod = this._normalizeOutputMethod(this.insertMethod);
    this._populateOutputMethodMenu();
    this._registerHotkeys();
    this._updatePanel();
    return applied;
  },

  _coerceSpawnArgs: function(args) {
    if (!Array.isArray(args)) {
      throw new Error("Backend command arguments are invalid");
    }
    if (args.length <= 0) {
      throw new Error("Backend command arguments are empty");
    }
    if (args.length > MAX_CLI_ARG_COUNT) {
      throw new Error("Too many backend command arguments");
    }
    if (String(args[0] || "").trim() === "") {
      throw new Error("Backend command is empty");
    }
    let normalized = [];
    let totalBytes = 0;
    for (let i = 0; i < args.length; i++) {
      if (args[i] === null || args[i] === undefined) {
        throw new Error("Backend command argument is missing");
      }
      let value = String(args[i]);
      if (i === 0) {
        value = value.trim();
      }
      if (value.indexOf("\u0000") >= 0) {
        throw new Error("Backend command argument contains invalid bytes");
      }
      if (value.length > MAX_CLI_ARG_BYTES) {
        throw new Error("Backend command argument is too large");
      }
      totalBytes += value.length;
      normalized.push(value);
    }
    if (totalBytes > MAX_CLI_COMMAND_BYTES) {
      throw new Error("Backend command is too large");
    }
    if (!this._isAllowedCliCommand(normalized[0])) {
      throw new Error("Backend command is not executable");
    }
    return normalized;
  },

  _isAllowedCliCommand: function(command) {
    let value = String(command || "").trim();
    if (value === "") {
      return false;
    }
    if (value.indexOf("/") >= 0) {
      return GLib.file_test(value, GLib.FileTest.IS_EXECUTABLE);
    }
    return GLib.find_program_in_path(value) !== null;
  },

  _parseSpawnOutput: function(stdout) {
    let output = String(stdout || "");
    if (output.length > MAX_SPAWN_JSON_BYTES) {
      return { status: "error", error: "Backend response is too large" };
    }
    try {
      let parsed = JSON.parse(output || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return { status: "error", error: "Invalid backend response: expected JSON object" };
      }
      return parsed;
    } catch (err) {
      return { status: "error", error: "Invalid backend response: " + err };
    }
  },

  _spawnJson: function(args, callback, options) {
    options = options || {};
    let timeoutId = 0;
    let done = false;
    let normalizedArgs;
    let callbackFn = typeof callback === "function" ? callback : function() {};

    const finalize = function(payload) {
      if (done) {
        return;
      }
      done = true;
      if (timeoutId) {
        Mainloop.source_remove(timeoutId);
        timeoutId = 0;
      }
      try {
        callbackFn(payload || {});
      } catch (err) {
        global.logError(err);
      }
    };

    try {
      normalizedArgs = this._coerceSpawnArgs(args);
      let timeoutMs = Number(options.timeoutMs || CLI_COMMAND_TIMEOUT_MS);
      if (timeoutMs > 0) {
        timeoutId = Mainloop.timeout_add(Math.max(250, timeoutMs), () => {
          finalize({ status: "error", error: "Backend command timed out" });
          return false;
        });
      }
      Util.spawn_async(normalizedArgs, (stdout) => {
        if (done) {
          return;
        }
        finalize(this._parseSpawnOutput(stdout));
      });
    } catch (err) {
      finalize({ status: "error", error: String(err) });
    }
  },

  _applyPayload: function(payload) {
    let status = payload.status || (payload.error ? "error" : "idle");
    this._applyPayloadLanguage(payload);
    this._updateRecordingTiming(payload, status);
    if (payload.error) {
      this._setStatus("error", payload.error, this.lastTranscript);
      return;
    }
    if (payload.status === "done" && payload.transcript) {
      this._finishAppletTextInsert(payload);
      return;
    }
    let message = payload.message || status;
    let transcript = payload.transcript || this.lastTranscript || "";
    this._setStatus(status, message, transcript);
    this._maybeAutoTranscribeRecorded(payload);
  },

  _applyPayloadLanguage: function(payload) {
    let language = String(payload.language || "").trim();
    if (language !== "") {
      this.activeLanguage = language;
    }
  },

  _updateRecordingTiming: function(payload, status) {
    if (status !== "recording") {
      this.recordingStartedAtMs = 0;
      this.recordingMaxSeconds = 0;
      return;
    }
    let started = this._parseDateMs(payload.started_at);
    if (started > 0) {
      this.recordingStartedAtMs = started;
    } else if (this.recordingStartedAtMs <= 0) {
      this.recordingStartedAtMs = Date.now();
    }
    let maxSeconds = Number(payload.max_seconds || this.maxSeconds || 30);
    this.recordingMaxSeconds = maxSeconds > 0 ? maxSeconds : 30;
  },

  _parseDateMs: function(value) {
    if (!value) {
      return 0;
    }
    let parsed = Date.parse(String(value));
    return isNaN(parsed) ? 0 : parsed;
  },

  _maybeAutoTranscribeRecorded: function(payload) {
    if (!this.autoTranscribeTimeout || !this.notificationSessionActive || this.isCommandRunning) {
      return;
    }
    if ((payload.status || "") !== "recorded") {
      return;
    }
    let recordingKey = String(payload.audio_path || payload.audio || "recorded");
    if (this.autoTranscribeRecordingKey === recordingKey) {
      return;
    }
    this.autoTranscribeRecordingKey = recordingKey;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Transcribing timed-out recording..."), this.lastTranscript);
    this._spawnJson(this._baseArgs("stop"), (nextPayload) => {
      this.isCommandRunning = false;
      this._applyPayload(nextPayload);
    });
  },

  _clearStatusTimer: function() {
    if (this.statusTimer) {
      Mainloop.source_remove(this.statusTimer);
      this.statusTimer = 0;
    }
  },

  _clearDisplayTimer: function() {
    if (this.displayTimer) {
      Mainloop.source_remove(this.displayTimer);
      this.displayTimer = 0;
    }
  },

  _clearSetupCheckTimer: function() {
    if (this.setupCheckTimer) {
      Mainloop.source_remove(this.setupCheckTimer);
      this.setupCheckTimer = 0;
    }
  },

  _clearPasteTimer: function() {
    if (this.pasteTimer) {
      Mainloop.source_remove(this.pasteTimer);
      this.pasteTimer = 0;
    }
  },

  _clearAlarmTimer: function() {
    if (this.alarmTimer) {
      Mainloop.source_remove(this.alarmTimer);
      this.alarmTimer = 0;
    }
  },

  _scheduleSetupCheck: function() {
    this._clearSetupCheckTimer();
    this.setupCheckTimer = Mainloop.timeout_add_seconds(2, () => {
      this.setupCheckTimer = 0;
      if (this.status === "idle") {
        this._runDoctor(true);
      }
      return false;
    });
  },

  _scheduleAlarmCheck: function(delaySeconds) {
    this._clearAlarmTimer();
    this.alarmTimer = Mainloop.timeout_add_seconds(Math.max(5, Number(delaySeconds || ALARM_CHECK_SECONDS)), () => {
      this.alarmTimer = 0;
      this._checkAlarms(false);
      this._scheduleAlarmCheck(ALARM_CHECK_SECONDS);
      return false;
    });
  },

  _scheduleStatusPoll: function() {
    this._clearStatusTimer();
    if (this.status !== "recording" && this.status !== "processing") {
      return;
    }
    this.statusTimer = Mainloop.timeout_add_seconds(2, () => {
      this.statusTimer = 0;
      this._refreshStatus();
      return false;
    });
  },

  _scheduleDisplayTick: function() {
    this._clearDisplayTimer();
    if (this.status !== "recording") {
      return;
    }
    this.displayTimer = Mainloop.timeout_add_seconds(1, () => {
      this.displayTimer = 0;
      if (this.status === "recording") {
        this._updatePanel();
        this._scheduleDisplayTick();
      }
      return false;
    });
  },

  _isUsableTargetWindow: function(window) {
    if (!window) {
      return false;
    }
    try {
      if (window.is_skip_taskbar && window.is_skip_taskbar()) {
        return false;
      }
    } catch (err) {
      return false;
    }
    return true;
  },

  _rememberFocusedWindow: function() {
    let window = global.display ? global.display.focus_window : null;
    if (this._isUsableTargetWindow(window)) {
      this.targetWindow = window;
      return true;
    }
    return false;
  },

  _restoreTargetWindowForPaste: function() {
    if (!this._isUsableTargetWindow(this.targetWindow)) {
      return false;
    }
    try {
      Main.activateWindow(this.targetWindow, global.get_current_time());
      return true;
    } catch (err) {
      global.logError(err);
      return false;
    }
  },

  _pasteClipboardAfterFocus: function() {
    this._spawnKeyboardAfterFocus(["xdotool", "key", "--clearmodifiers", "ctrl+v"]);
  },

  _typeTextAfterFocus: function(text) {
    let delay = Math.max(0, Math.floor(Number(this.typingDelayMs || 0)));
    let typedText = this._coerceTypeText(text);
    if (typedText === null) {
      return false;
    }
    this._spawnKeyboardAfterFocus(["xdotool", "type", "--clearmodifiers", "--delay", String(delay), typedText]);
    return true;
  },

  _coerceTypeText: function(text) {
    let value = String(text || "");
    if (value.indexOf("\u0000") >= 0) {
      value = value.replace(/\u0000/g, "");
    }
    if (value.length > MAX_TYPE_COMMAND_CHARS) {
      this._setStatus("error", _("Text too long for keyboard typing"), this.lastTranscript);
      return null;
    }
    return value;
  },

  _spawnKeyboardAfterFocus: function(args) {
    this._clearPasteTimer();
    this.pasteTimer = Mainloop.timeout_add(PASTE_FOCUS_DELAY_MS, () => {
      this.pasteTimer = 0;
      try {
        Util.spawn(this._coerceSpawnArgs(args));
      } catch (err) {
        global.logError(err);
        this._setStatus("error", _("Keyboard insert failed") + ": " + String(err), this.lastTranscript);
      }
      return false;
    });
  },

  _finishAppletTextInsert: function(payload) {
    this._insertTranscriptText(payload.transcript);
  },

  _insertTranscriptText: function(transcript) {
    let method = this._normalizeOutputMethod(this.insertMethod);
    let text = this._preparedTranscriptText(transcript);
    if (method === "none") {
      this._setStatus("done", _("Insertion disabled"), transcript);
      return;
    }
    if (method === "type") {
      if (GLib.find_program_in_path("xdotool")) {
        let restored = this._restoreTargetWindowForPaste();
        if (this._typeTextAfterFocus(text)) {
          this._setStatus("done", restored ? _("Typed into target window") : _("Typed text"), transcript);
        }
      } else {
        this._setStatus("error", _("Install xdotool for direct typing"), transcript);
      }
      return;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
    if (method === "clipboard") {
      this._setStatus("done", _("Copied to clipboard"), transcript);
      return;
    }
    if (GLib.find_program_in_path("xdotool")) {
      let restored = this._restoreTargetWindowForPaste();
      this._pasteClipboardAfterFocus();
      this._setStatus("done", restored ? _("Copied and pasted into target window") : _("Copied and pasted"), transcript);
    } else {
      this._setStatus("done", _("Copied to clipboard; install xdotool for automatic paste"), transcript);
    }
  },

  _preparedTranscriptText: function(transcript) {
    let text = transcript || "";
    if (this.sanitizeSpecialChars) {
      text = this._sanitizeSpecialChars(text);
    }
    text = String(text || "");
    if (text.indexOf("\u0000") >= 0) {
      text = text.replace(/\u0000/g, "");
    }
    if (text.length > MAX_TEXT_INSERT_CHARS) {
      text = text.slice(0, MAX_TEXT_INSERT_CHARS);
    }
    if (this.appendSpace && text && !/\s$/.test(text)) {
      text += " ";
    }
    return text;
  },

  _sanitizeSpecialChars: function(text) {
    let map = {
      "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
      "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
      "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D", "ñ": "n", "Ñ": "N",
      "ç": "c", "Ç": "C", "¿": "", "¡": ""
    };
    return String(text || "").replace(/[^\u0000-\u007E]/g, (char) => {
      if (Object.prototype.hasOwnProperty.call(map, char)) {
        return map[char];
      }
      let normalized = char.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
      return /^[\u0000-\u007E]*$/.test(normalized) ? normalized : char;
    });
  },

  _copyLastTranscript: function() {
    if (!this.lastTranscript) {
      this._setStatus(this.status, _("No transcript yet"), this.lastTranscript);
      return;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._preparedTranscriptText(this.lastTranscript));
    this._setStatus("done", _("Copied last transcript"), this.lastTranscript);
  },

  _insertLastTranscript: function() {
    if (!this.lastTranscript) {
      this._setStatus(this.status, _("No transcript yet"), this.lastTranscript);
      return;
    }
    this._insertTranscriptText(this.lastTranscript);
  },

  _populateHistoryMenu: function(transcripts) {
    if (!this.historyItem) {
      return;
    }
    this.historyItem.menu.removeAll();
    if (!transcripts || transcripts.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No transcripts yet"));
      empty.setSensitive(false);
      this.historyItem.menu.addMenuItem(empty);
      return;
    }
    for (let transcript of transcripts) {
      let label = transcript.preview || transcript.name || _("Transcript");
      let entry = new PopupMenu.PopupSubMenuMenuItem(label);
      this.historyItem.menu.addMenuItem(entry);

      let insertItem = new PopupMenu.PopupIconMenuItem(_("Insert transcript"), "edit-paste-symbolic", St.IconType.SYMBOLIC);
      insertItem.connect("activate", () => this._insertHistoryTranscript(transcript.text || ""));
      entry.menu.addMenuItem(insertItem);

      let copyItem = new PopupMenu.PopupIconMenuItem(_("Copy transcript"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
      copyItem.connect("activate", () => this._copyHistoryTranscript(transcript.text || ""));
      entry.menu.addMenuItem(copyItem);
    }
  },

  _copyHistoryTranscript: function(text) {
    if (!text) {
      return;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._preparedTranscriptText(text));
    this._setStatus("done", _("Copied transcript"), text);
  },

  _insertHistoryTranscript: function(text) {
    if (!text) {
      return;
    }
    this._insertTranscriptText(text);
  },

  _setStatus: function(status, message, transcript) {
    let previousStatus = this.status;
    this.status = status;
    this.lastMessage = message || "";
    if (transcript) {
      this.lastTranscript = transcript;
    }
    if (this.copyLastItem) {
      this.copyLastItem.setSensitive(Boolean(this.lastTranscript));
    }
    if (this.insertLastItem) {
      this.insertLastItem.setSensitive(Boolean(this.lastTranscript));
    }
    if (this.cancelItem) {
      this.cancelItem.setSensitive(this.status === "recording" || this.status === "recorded");
    }
    this._updatePanel();
    this._maybeNotify(previousStatus, this.status, this.lastMessage);
    this._scheduleStatusPoll();
    this._scheduleDisplayTick();
  },

  _maybeNotify: function(previousStatus, status, message) {
    if (!this.notificationSessionActive || status === "processing") {
      return;
    }
    let key = status + "\n" + String(message || "");
    if (key === this.lastNotificationKey) {
      return;
    }
    if (status === "recording") {
      if (this.notifyRecording) {
        this._notify(_("Speed of Cinnamon"), _("Recording started: ") + this._currentLanguage(), false);
        this.lastNotificationKey = key;
      }
      return;
    }
    if (status === "recorded") {
      if (this.notifyRecording) {
        this._notify(_("Speed of Cinnamon"), message || _("Recording ready to transcribe"), false);
        this.lastNotificationKey = key;
      }
      return;
    }
    if (status === "done") {
      if (this.notifyComplete) {
        this._notify(_("Speed of Cinnamon"), message || _("Transcript ready"), false);
        this.lastNotificationKey = key;
      }
      this.notificationSessionActive = false;
      return;
    }
    if (status === "error") {
      if (this.notifyError) {
        this._notify(_("Speed of Cinnamon"), message || _("Dictation failed"), true);
        this.lastNotificationKey = key;
      }
      this.notificationSessionActive = false;
      return;
    }
    if (status === "idle" && previousStatus !== "idle") {
      this.notificationSessionActive = false;
    }
  },

  _notify: function(title, body, critical) {
    try {
      if (critical && Main.criticalNotify) {
        Main.criticalNotify(title, body);
      } else if (Main.notify) {
        Main.notify(title, body);
      }
    } catch (err) {
      global.logError(err);
    }
  },

  _shortTranscript: function() {
    if (!this.lastTranscript) {
      return _("No transcript yet");
    }
    let clean = this.lastTranscript.replace(/\s+/g, " ").trim();
    return clean.length > 80 ? clean.slice(0, 77) + "..." : clean;
  },

  _formatSeconds: function(seconds) {
    let value = Math.max(0, Math.floor(Number(seconds || 0)));
    if (value < 60) {
      return String(value) + "s";
    }
    let minutes = Math.floor(value / 60);
    let rest = value % 60;
    return String(minutes) + ":" + (rest < 10 ? "0" : "") + String(rest);
  },

  _recordingElapsedSeconds: function() {
    if (this.recordingStartedAtMs <= 0) {
      return 0;
    }
    let elapsed = Math.floor((Date.now() - this.recordingStartedAtMs) / 1000);
    return Math.max(0, elapsed);
  },

  _recordingProgressText: function() {
    let maxSeconds = Number(this.recordingMaxSeconds || this.maxSeconds || 30);
    let elapsed = this._recordingElapsedSeconds();
    if (maxSeconds > 0) {
      elapsed = Math.min(elapsed, maxSeconds);
      return this._formatSeconds(elapsed) + " / " + this._formatSeconds(maxSeconds);
    }
    return this._formatSeconds(elapsed);
  },

  _panelStyleClassForStatus: function(status) {
    if (status === "recording") return "speed-of-cinnamon-recording";
    if (status === "processing") return "speed-of-cinnamon-processing";
    if (status === "recorded") return "speed-of-cinnamon-recorded";
    if (status === "error") return "speed-of-cinnamon-error";
    if (status === "setup") return "speed-of-cinnamon-setup";
    return "speed-of-cinnamon-ready";
  },

  _applyPanelStyle: function(status) {
    if (!this.actor || !this.actor.add_style_class_name || !this.actor.remove_style_class_name) {
      return;
    }
    for (let styleClass of PANEL_STATUS_CLASSES) {
      this.actor.remove_style_class_name(styleClass);
    }
    this.actor.add_style_class_name(this._panelStyleClassForStatus(status));
  },

  _updatePanel: function() {
    let label = "";
    let tooltip = "Speed of Cinnamon";
    let statusText = this.status || "idle";
    if (this.status === "recording") {
      let progress = this._recordingProgressText();
      label = "REC " + this._formatSeconds(this._recordingElapsedSeconds());
      tooltip = _("Recording...") + " " + progress;
      statusText = "recording " + progress;
      if (this.toggleItem) this.toggleItem.label.text = _("Stop dictation");
    } else if (this.status === "processing") {
      label = "...";
      tooltip = this.lastMessage || _("Processing...");
      if (this.toggleItem) this.toggleItem.label.text = _("Working...");
    } else if (this.status === "error") {
      label = "ERR";
      tooltip = this.lastMessage || _("Error");
      if (this.toggleItem) this.toggleItem.label.text = _("Start dictation");
    } else if (this.status === "recorded") {
      label = "RDY";
      tooltip = this.lastMessage || _("Ready to transcribe");
      if (this.toggleItem) this.toggleItem.label.text = _("Transcribe recording");
    } else if (this.status === "setup") {
      label = "SET";
      tooltip = this.lastMessage || _("Setup needed");
      if (this.toggleItem) this.toggleItem.label.text = _("Start dictation");
    } else {
      label = "SOC";
      tooltip = this.lastMessage || _("Ready");
      if (this.toggleItem) this.toggleItem.label.text = _("Start dictation");
    }
    this._applyPanelStyle(this.status);
    this.set_applet_label(this.showPanelLabel ? label : "");
    this.set_applet_tooltip(tooltip + "\n" + this._shortTranscript());
    if (this.statusItem) {
      this.statusItem.label.text = _("Status: ") + statusText;
    }
    if (this.languageItem) {
      this.languageItem.label.text = _("Language: ") + this._currentLanguage();
    }
    if (this.recorderItem) {
      this.recorderItem.label.text = _("Recorder: ") + this._recorderLabel(this._normalizeRecorder(this.recorder));
    }
    if (this.recordingLimitItem) {
      this.recordingLimitItem.label.text = _("Duration: ") + this._formatSeconds(this._normalizeRecordingLimit(this.maxSeconds));
    }
    if (this.outputMethodItem) {
      this.outputMethodItem.label.text = _("Output: ") + this._outputMethodLabel(this._normalizeOutputMethod(this.insertMethod));
    }
    if (this.transcriptItem) {
      this.transcriptItem.label.text = this._shortTranscript();
    }
  }
};

function main(metadata, orientation, panelHeight, instanceId) {
  return new MyApplet(metadata, orientation, panelHeight, instanceId);
}
