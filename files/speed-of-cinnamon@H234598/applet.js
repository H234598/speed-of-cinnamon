const Applet = imports.ui.applet;
const Main = imports.ui.main;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const St = imports.gi.St;
const Util = imports.misc.util;
const GLib = imports.gi.GLib;
const Mainloop = imports.mainloop;

const UUID = "speed-of-cinnamon@H234598";
const HOTKEY_ID = "speed-of-cinnamon-toggle";
const DEFAULT_CLI = GLib.build_filenamev([GLib.get_home_dir(), ".local", "bin", "speed-of-cinnamon"]);
const EXPORTABLE_SETTINGS = [
  ["toggle-keybinding", "toggleKeybinding"],
  ["show-panel-label", "showPanelLabel"],
  ["language", "language"],
  ["secondary-language", "secondaryLanguage"],
  ["max-seconds", "maxSeconds"],
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
  ["transcriber", "transcriber"],
  ["whisper-model", "whisperModel"],
  ["transcriber-command", "transcriberCommand"],
  ["post-process-command", "postProcessCommand"]
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
    this.showPanelLabel = true;
    this.language = "en";
    this.secondaryLanguage = "de";
    this.activeLanguage = "en";
    this.maxSeconds = 30;
    this.recorder = "auto";
    this.inputDevice = "";
    this.insertMethod = "clipboard-paste";
    this.appendSpace = true;
    this.typingDelayMs = 8;
    this.cliPath = DEFAULT_CLI;
    this.transcriber = "auto";
    this.whisperModel = "";
    this.transcriberCommand = "";
    this.postProcessCommand = "";
    this.personalContext = "";
    this.vocabulary = "";
    this.notifyRecording = false;
    this.notifyComplete = true;
    this.notifyError = true;
    this.status = "idle";
    this.lastTranscript = "";
    this.lastMessage = "";
    this.isCommandRunning = false;
    this.notificationSessionActive = false;
    this.lastNotificationKey = "";
    this.clipboard = St.Clipboard.get_default();
    this.statusTimer = 0;

    this.set_applet_icon_path(this.metadata.path + "/icon.svg");
    this.set_applet_label("");
    this.set_applet_tooltip(_("Speed of Cinnamon"));

    this.settings = new Settings.AppletSettings(this, UUID, instanceId);
    this._bindSettings();
    this._syncActiveLanguage();
    this._buildMenu();
    this._registerHotkey();
    this._refreshStatus();
  },

  _bindSettings: function() {
    this.settings.bindProperty(Settings.BindingDirection.IN, "toggle-keybinding", "toggleKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "show-panel-label", "showPanelLabel", this._updatePanel, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "language", "language", this._onLanguageSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "secondary-language", "secondaryLanguage", this._onLanguageSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "recorder", "recorder", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "input-device", "inputDevice", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "insert-method", "insertMethod", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "append-space", "appendSpace", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "typing-delay-ms", "typingDelayMs", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "cli-path", "cliPath", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "transcriber", "transcriber", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "whisper-model", "whisperModel", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "transcriber-command", "transcriberCommand", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-command", "postProcessCommand", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "personal-context", "personalContext", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "vocabulary", "vocabulary", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "notify-recording", "notifyRecording", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "notify-complete", "notifyComplete", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "notify-error", "notifyError", null, null);
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

    this.languageItem = new PopupMenu.PopupIconMenuItem(_("Language: en"), "preferences-desktop-locale-symbolic", St.IconType.SYMBOLIC);
    this.languageItem.connect("activate", () => this._switchLanguage());
    this.menu.addMenuItem(this.languageItem);

    this.transcriptItem = new PopupMenu.PopupMenuItem(_("No transcript yet"));
    this.transcriptItem.setSensitive(false);
    this.menu.addMenuItem(this.transcriptItem);

    this.copyLastItem = new PopupMenu.PopupIconMenuItem(_("Copy last transcript"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this.copyLastItem.setSensitive(false);
    this.copyLastItem.connect("activate", () => this._copyLastTranscript());
    this.menu.addMenuItem(this.copyLastItem);

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

    let diagnostics = new PopupMenu.PopupIconMenuItem(_("Copy diagnostics"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    diagnostics.connect("activate", () => this._copyDiagnostics());
    this.menu.addMenuItem(diagnostics);

    let inputs = new PopupMenu.PopupIconMenuItem(_("Show input source"), "audio-input-microphone-symbolic", St.IconType.SYMBOLIC);
    inputs.connect("activate", () => this._showInputSource());
    this.menu.addMenuItem(inputs);

    let transcripts = new PopupMenu.PopupIconMenuItem(_("Open transcripts"), "folder-documents-symbolic", St.IconType.SYMBOLIC);
    transcripts.connect("activate", () => {
      Util.spawn(["xdg-open", GLib.build_filenamev([GLib.get_user_state_dir(), "speed-of-cinnamon", "transcripts"])]);
    });
    this.menu.addMenuItem(transcripts);

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

  _hotkeyName: function() {
    return HOTKEY_ID + "-" + this.instanceId;
  },

  _registerHotkey: function() {
    Main.keybindingManager.removeHotKey(this._hotkeyName());
    Main.keybindingManager.addHotKey(this._hotkeyName(), this.toggleKeybinding, () => this._toggleRecording());
  },

  _onHotkeyChanged: function() {
    this._registerHotkey();
  },

  on_applet_clicked: function() {
    this.menu.toggle();
  },

  on_applet_removed_from_panel: function() {
    this._clearStatusTimer();
    Main.keybindingManager.removeHotKey(this._hotkeyName());
    if (this.settings) {
      this.settings.finalize();
    }
  },

  _baseArgs: function(command) {
    let backendInsertMethod = this._usesCinnamonClipboard() ? "none" : String(this.insertMethod || "clipboard-paste");
    let args = [
      this.cliPath || DEFAULT_CLI,
      command,
      "--json",
      "--language", String(this._currentLanguage()),
      "--max-seconds", String(this.maxSeconds || 30),
      "--recorder", String(this.recorder || "auto"),
      "--transcriber", String(this.transcriber || "auto"),
      "--insert-method", backendInsertMethod,
      "--typing-delay-ms", String(this.typingDelayMs || 8)
    ];
    if (this.appendSpace) {
      args.push("--append-space");
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
    return [this.cliPath || DEFAULT_CLI, "status", "--json"];
  },

  _doctorArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "doctor", "--json"];
  },

  _diagnosticsArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "diagnostics", "--json"];
  },

  _cancelArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "cancel", "--json"];
  },

  _historyArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "history", "--limit", "5", "--json"];
  },

  _cleanupArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "cleanup", "--keep-transcripts", "100", "--keep-recordings", "25", "--json"];
  },

  _listInputsArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "list-inputs", "--json"];
  },

  _settingsExportArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "settings-export", "--settings-json", JSON.stringify(this._settingsSnapshot()), "--json"];
  },

  _settingsImportArgs: function() {
    return [this.cliPath || DEFAULT_CLI, "settings-import", "--json"];
  },

  _usesCinnamonClipboard: function() {
    return this.insertMethod === "clipboard" || this.insertMethod === "clipboard-paste";
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
    this._updatePanel();
  },

  _switchLanguage: function() {
    let primary = this._primaryLanguage();
    let secondary = this._secondaryLanguage();
    this.activeLanguage = this._currentLanguage() === primary ? secondary : primary;
    this._setStatus("ready", _("Language: ") + this._currentLanguage(), this.lastTranscript);
  },

  _toggleRecording: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.notificationSessionActive = true;
    this.lastNotificationKey = "";
    this.isCommandRunning = true;
    this._setStatus("processing", _("Working..."), "");
    this._spawnJson(this._baseArgs("toggle"), (payload) => {
      this.isCommandRunning = false;
      this._applyPayload(payload);
    });
  },

  _refreshStatus: function() {
    this._spawnJson(this._statusArgs(), (payload) => this._applyPayload(payload));
  },

  _cancelRecording: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Cancelling..."), this.lastTranscript);
    this._spawnJson(this._cancelArgs(), (payload) => {
      this.isCommandRunning = false;
      this._applyPayload(payload);
    });
  },

  _runDoctor: function() {
    this._spawnJson(this._doctorArgs(), (payload) => {
      let missing = [];
      for (let check of payload.checks || []) {
        if (!check.ok) {
          missing.push(check.name);
        }
      }
      if (payload.ok) {
        if (missing.length > 0) {
          this._setStatus("ready", _("Doctor: core OK; optional missing: ") + missing.join(", "), this.lastTranscript);
        } else {
          this._setStatus("ready", _("Doctor: all checked helpers found"), this.lastTranscript);
        }
      } else {
        this._setStatus("error", _("Missing: ") + missing.join(", "), this.lastTranscript);
      }
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

  _showInputSource: function() {
    if (this.inputDevice && this.inputDevice.trim() !== "") {
      this._setStatus("ready", _("Input device: ") + this.inputDevice, this.lastTranscript);
      return;
    }
    this._spawnJson(this._listInputsArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let sources = payload.sources || [];
      if (sources.length === 0) {
        this._setStatus("error", _("No input sources found"), this.lastTranscript);
        return;
      }
      let selected = sources[0];
      for (let source of sources) {
        if (source.default) {
          selected = source;
          break;
        }
      }
      this._setStatus("ready", _("Default input: ") + (selected.description || selected.name), this.lastTranscript);
    });
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
      let deleted = Number(payload.deleted_transcripts || 0) + Number(payload.deleted_recordings || 0) + Number(payload.deleted_logs || 0);
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
    this._registerHotkey();
    this._updatePanel();
    return applied;
  },

  _spawnJson: function(args, callback) {
    try {
      Util.spawn_async(args, (stdout) => {
        let payload;
        try {
          payload = JSON.parse(stdout || "{}");
        } catch (err) {
          payload = { status: "error", error: "Invalid backend response: " + err };
        }
        callback(payload);
      });
    } catch (err) {
      callback({ status: "error", error: String(err) });
    }
  },

  _applyPayload: function(payload) {
    if (payload.error) {
      this._setStatus("error", payload.error, this.lastTranscript);
      return;
    }
    if (payload.status === "done" && payload.transcript && this._usesCinnamonClipboard()) {
      this._finishCinnamonClipboardInsert(payload);
      return;
    }
    let status = payload.status || "idle";
    let message = payload.message || status;
    let transcript = payload.transcript || this.lastTranscript || "";
    this._setStatus(status, message, transcript);
  },

  _clearStatusTimer: function() {
    if (this.statusTimer) {
      Mainloop.source_remove(this.statusTimer);
      this.statusTimer = 0;
    }
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

  _finishCinnamonClipboardInsert: function(payload) {
    let text = this._preparedTranscriptText(payload.transcript);
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
    if (this.insertMethod === "clipboard") {
      this._setStatus("done", _("Copied to clipboard"), payload.transcript);
      return;
    }
    if (GLib.find_program_in_path("xdotool")) {
      Util.spawn(["xdotool", "key", "--clearmodifiers", "ctrl+v"]);
      this._setStatus("done", _("Copied and pasted"), payload.transcript);
    } else {
      this._setStatus("done", _("Copied to clipboard; install xdotool for automatic paste"), payload.transcript);
    }
  },

  _preparedTranscriptText: function(transcript) {
    let text = transcript || "";
    if (this.appendSpace && text && !/\s$/.test(text)) {
      text += " ";
    }
    return text;
  },

  _copyLastTranscript: function() {
    if (!this.lastTranscript) {
      this._setStatus(this.status, _("No transcript yet"), this.lastTranscript);
      return;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._preparedTranscriptText(this.lastTranscript));
    this._setStatus("done", _("Copied last transcript"), this.lastTranscript);
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
      let item = new PopupMenu.PopupMenuItem(transcript.preview || transcript.name || _("Transcript"));
      item.connect("activate", () => this._copyHistoryTranscript(transcript.text || ""));
      this.historyItem.menu.addMenuItem(item);
    }
  },

  _copyHistoryTranscript: function(text) {
    if (!text) {
      return;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._preparedTranscriptText(text));
    this._setStatus("done", _("Copied transcript"), text);
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
    if (this.cancelItem) {
      this.cancelItem.setSensitive(this.status === "recording" || this.status === "recorded");
    }
    this._updatePanel();
    this._maybeNotify(previousStatus, this.status, this.lastMessage);
    this._scheduleStatusPoll();
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

  _updatePanel: function() {
    let label = "";
    let tooltip = "Speed of Cinnamon";
    if (this.status === "recording") {
      label = "REC";
      tooltip = _("Recording...");
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
    } else {
      label = "SOC";
      tooltip = this.lastMessage || _("Ready");
      if (this.toggleItem) this.toggleItem.label.text = _("Start dictation");
    }
    this.set_applet_label(this.showPanelLabel ? label : "");
    this.set_applet_tooltip(tooltip + "\n" + this._shortTranscript());
    if (this.statusItem) {
      this.statusItem.label.text = _("Status: ") + (this.status || "idle");
    }
    if (this.languageItem) {
      this.languageItem.label.text = _("Language: ") + this._currentLanguage();
    }
    if (this.transcriptItem) {
      this.transcriptItem.label.text = this._shortTranscript();
    }
  }
};

function main(metadata, orientation, panelHeight, instanceId) {
  return new MyApplet(metadata, orientation, panelHeight, instanceId);
}
