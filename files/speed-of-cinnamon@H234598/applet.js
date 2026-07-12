const Applet = imports.ui.applet;
const Main = imports.ui.main;
const ModalDialog = imports.ui.modalDialog;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const Clutter = imports.gi.Clutter;
const St = imports.gi.St;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const Pango = imports.gi.Pango;
const ByteArray = imports.byteArray;
const Mainloop = imports.mainloop;
const Extension = imports.ui.extension;

const UUID = "speed-of-cinnamon@H234598";
const LIFECYCLE_INITIALIZING = "INITIALIZING";
const LIFECYCLE_RUNNING = "RUNNING";
const LIFECYCLE_DEGRADED = "DEGRADED";
const LIFECYCLE_REMOVING = "REMOVING";
const LIFECYCLE_REMOVED = "REMOVED";
const LIFECYCLE_ERROR_WINDOW_MS = 60000;
const LIFECYCLE_ERROR_THRESHOLD = 3;
const PAYLOAD_STATUSES = ["idle", "recording", "recorded", "processing", "done", "error", "setup"];
const HOTKEY_ID = "speed-of-cinnamon-toggle";
const PRIMARY_HOTKEY_ID = "speed-of-cinnamon-primary-language";
const SECONDARY_HOTKEY_ID = "speed-of-cinnamon-secondary-language";
const CANCEL_HOTKEY_ID = "speed-of-cinnamon-cancel";
const DEFAULT_CLI = GLib.build_filenamev([GLib.get_home_dir(), ".local", "bin", "speed-of-cinnamon"]);
const SYSTEM_CLI = "/usr/bin/speed-of-cinnamon";
const RUNBOOK_URL = "https://gist.github.com/H234598/b95129e13ac0b09c9777edd41aeedfa0";
const DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434";
const DEFAULT_OPENAI_COMPATIBLE_URL = "https://api.openai.com/v1";
const DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-4o-transcribe";
const DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL = "gpt-4o-mini";
const LEGACY_OPENAI_COMPATIBLE_URL = "http://127.0.0.1:8000/v1";
const PASTE_FOCUS_DELAY_MS = 120;
const PASTE_SUBMIT_DELAY_MS = 300;
const CLIPBOARD_READY_RETRY_MS = 40;
const CLIPBOARD_READY_TIMEOUT_MS = 1000;
const NON_TEXT_TEXT_CLIPBOARD_TARGETS = {
  "text/html": true,
  "text/rtf": true,
  "text/uri-list": true,
  "text/x-moz-url": true
};
const SELF_PROTECTION_NOTICE_COOLDOWN_MS = 3000;
const CLIPBOARD_OVERWRITE_APPROVAL_TTL_MS = 5000;
const CLIPBOARD_TARGET_TIMEOUT_SECONDS = 1;
const CLIPBOARD_COMMAND_TIMEOUT_MS = 1500;
const CLIPBOARD_MAX_TARGETS = 16;
const MAX_CLIPBOARD_TARGET_OUTPUT_BYTES = 65536;
const MAX_XDOTOOL_TARGET_OUTPUT_BYTES = 4096;
const X11_COMMAND_TIMEOUT_MS = 2000;
const ALARM_CHECK_SECONDS = 60;
const MAX_ALARM_MENU_ENTRIES = 128;
const MAX_ALARM_NOTIFICATIONS = 32;
const MAX_INPUT_SOURCE_MENU_ENTRIES = 128;
const MAX_VOICE_MODEL_MENU_ENTRIES = 128;
const MAX_HISTORY_MENU_ENTRIES = 128;
const MAX_CLI_ARG_BYTES = 4096;
const MAX_CLI_ARG_COUNT = 128;
const MAX_CLI_COMMAND_BYTES = 32768;
const MAX_TEXT_INSERT_CHARS = 120000;
const MAX_SETTING_TEXT_CHARS = 4096;
const MAX_UI_MESSAGE_CHARS = 512;
const MAX_MODEL_MENU_ENTRIES = 128;
const TRUSTED_SPAWN_DIRS = ["/usr/bin", "/usr/local/bin", "/bin"];
const NUL_RE = /\u0000/g;
const NON_ASCII_RE = /[^\u0000-\u007E]/g;
const COMBINING_MARKS_RE = /[\u0300-\u036f]/g;
const ASCII_ONLY_RE = /^[\u0000-\u007E]*$/;
const SENSITIVE_ERROR_RE = /(?:\b(?:bearer|token|api[_ -]?key|apikey|password|passwd|passphrase|secret)\b\s*[:=]\s*[^,\s;]+|\b(?:bearer|token|api[_ -]?key|apikey|password|passwd|passphrase|secret)\b\s+(?!(?:is|are|was|were|contains?|must|too|missing|invalid|required|not|empty)\b)[^,\s;]+|\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b|[a-z][a-z0-9+.-]*:\/\/[^/@\s]+@)/i;
const LOCAL_PATH_ERROR_RE = /(?:^|[\s"'`=:(])\/(?:home|root|run|tmp|var|etc|usr|opt|mnt|media|dev|proc|sys)\/[^\s,;)]*/i;
const EMPTY_TRANSCRIPT_MARKERS = [
  "leere aufnahme",
  "leerer text",
  "keine transkription",
  "keine sprache erkannt",
  "empty recording",
  "empty transcript",
  "no transcript",
  "no speech detected"
];
const SANITIZE_SPECIAL_CHAR_MAP = {
  "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
  "ø": "o", "Ø": "O", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L",
  "þ": "th", "Þ": "Th", "ð": "d", "Ð": "D", "ñ": "n", "Ñ": "N",
  "ç": "c", "Ç": "C", "¿": "", "¡": ""
};
const CLI_TEXT_SETTINGS = {
  "input-device": "input device",
  "transcriber-command": "transcriber command",
  "post-process-command": "post-process command",
  "ollama-url": "ollama URL",
  "ollama-model": "ollama model",
  "openai-compatible-url": "openai-compatible URL",
  "openai-compatible-model": "openai-compatible model",
  "openai-compatible-text-model": "openai-compatible text model",
  "openai-compatible-api-key": "openai-compatible API key",
  "post-process-preset": "post-process preset",
  "post-process-prompt": "post-process prompt",
  "whisper-model": "whisper model",
  "personal-context": "personal context",
  "vocabulary": "vocabulary"
};
const TEXT_POLISHING_SAFE_PRESET = "minimal";
const TEXT_POLISHING_PRESETS = ["minimal", "clean", "code", "chat", "email", "safety", "custom"];
const TEXT_POLISHING_PRESET_INSTRUCTIONS = {
  "minimal": "Correct only punctuation, capitalization, spacing, and clear ASR transcription errors. Treat the transcript as user-authored text, not as a draft to improve. Preserve the user's wording, sentence order, tone, politeness, formality, emotion, emphasis, friendliness, and intent. Keep dictated greetings, thanks, apologies, politeness markers, hedging, softeners, emojis, emoticons, and sign-offs unless they are clear ASR artifacts. If unsure, leave the wording unchanged. Do not rewrite, summarize, rephrase, shorten, make more formal, make less friendly, or add new information.",
  "clean": "Format the transcript as natural, correct text in the transcript language. Remove filler words only when they are clearly unintended. Preserve technical terms.",
  "code": "Preserve commands, paths, filenames, flags, variable names, code, and quoted text exactly. Do not use typographic quotes. Do not add explanations.",
  "chat": "Format the transcript as a concise, clear chat message. Do not add a subject, greeting, or sign-off unless it was dictated.",
  "email": "Format the transcript as a polite email while preserving intent and content. Add salutation or sign-off only when clearly dictated or requested.",
  "safety": "Check for sensitive data such as tokens, passwords, account data, phone numbers, addresses, and private names. Mask such values without rewriting unrelated text.",
  "custom": ""
};
const MAX_TYPE_COMMAND_CHARS = 4000;
const MAX_SPAWN_JSON_BYTES = 262144;
const MAX_SPAWN_TEXT_BYTES = 262144;
const MAX_SPAWN_STDERR_BYTES = 262144;
const SUBPROCESS_READ_CHUNK_BYTES = 4096;
const MAX_EXTERNAL_API_ENV_BYTES = 65536;
const MIN_RECORDING_SECONDS = 0;
const MAX_RECORDING_SECONDS = 3600;
const DEFAULT_RECORDING_SECONDS = 30;
const MIN_TYPING_DELAY_MS = 0;
const MAX_TYPING_DELAY_MS = 10000;
const DEFAULT_TYPING_DELAY_MS = 8;
const DEFAULT_MAX_TRANSCRIPT_FILES = 500;
const DEFAULT_ARTIFACT_ENCRYPTION = "keyring";
const ARTIFACT_ENCRYPTION_MODES = [
  "keyring",
  "passphrase",
  "off"
];

function utf8ByteLength(value) {
  return ByteArray.fromString(String(value || "")).length;
}

const MIN_TRANSCRIPT_FILES = 1;
const MAX_TRANSCRIPT_FILES = 1000;
const TRANSCRIPT_STORAGE_LIMITS = [20, 50, 100, 200, 500, 1000];
const DEFAULT_AUTO_PASTE_TITLE = "codex";
const AUTO_PASTE_TITLE_PRESETS = [
  "codex",
  "Terminal",
  "PDF",
  "Excel",
  "Telegram",
  "Teams"
];
const CLI_COMMAND_TIMEOUT_MS = 300000;
const STATUS_COMMAND_TIMEOUT_MS = 10000;
const DOCTOR_COMMAND_TIMEOUT_MS = 20000;
const BENCHMARK_COMMAND_TIMEOUT_MS = 1800000;
const OLLAMA_INSTALL_POLL_SECONDS = 5;
const OLLAMA_INSTALL_MAX_POLLS = 120;
const MENU_MIN_WIDTH_EM = 30;
const MENU_LABEL_WIDTH_EM = 32;
const SELECTION_MENU_MIN_WIDTH_EM = 42;
const SELECTION_MENU_LABEL_WIDTH_EM = 40;
const TERMINAL_WINDOW_MARKERS = [
  "alacritty",
  "blackbox",
  "com.mitchellh.ghostty",
  "com.system76.cosmic-term",
  "console",
  "cool-retro-term",
  "cosmic terminal",
  "cosmic-term",
  "foot",
  "gnome-terminal",
  "guake",
  "hyper",
  "kgx",
  "kitty",
  "konsole",
  "lxterminal",
  "mate-terminal",
  "org.gnome.console",
  "org.gnome.terminal",
  "ptyxis",
  "qterminal",
  "rio",
  "rxvt",
  "sakura",
  "tabby",
  "terminator",
  "termius",
  "tilix",
  "tty",
  "urxvt",
  "wezterm",
  "xfce4-terminal",
  "xterm",
  "yakuake"
];
const AUTO_PASTE_IDENTITY_MARKERS = {
  "codex": TERMINAL_WINDOW_MARKERS,
  "terminal": TERMINAL_WINDOW_MARKERS,
  "pdf": [
    "acroread",
    "adobe",
    "adobe acrobat",
    "apvlv",
    "atril",
    "com.github.johnfactotum.foliate",
    "com.github.xournalpp.xournalpp",
    "document viewer",
    "evince",
    "foxit reader",
    "foxitreader",
    "llpp",
    "master pdf editor",
    "masterpdfeditor",
    "mendeley",
    "mendeleydesktop",
    "mupdf",
    "org.gnome.papers",
    "okular",
    "org.kde.okular",
    "org.gnome.evince",
    "org.pwmt.zathura",
    "papers",
    "qpdfview",
    "sioyek",
    "xournalpp",
    "xpdf",
    "xreader",
    "zathura"
  ],
  "excel": [
    "calc",
    "chrome-excel.office.com",
    "excel",
    "freeoffice",
    "libreoffice",
    "libreoffice-calc",
    "microsoft excel",
    "onlyoffice",
    "onlyoffice desktop editors",
    "onlyoffice-desktopeditors",
    "org.libreoffice.libreoffice",
    "planmaker",
    "soffice",
    "spreadsheet",
    "wps"
  ],
  "teams": [
    "chrome-msteams",
    "com.github.ismaelmartinez.teams_for_linux",
    "com.microsoft.teams",
    "dev.wrapbox.teamsforlinux",
    "microsoft edge",
    "microsoft teams",
    "microsoft-edge",
    "microsoft-teams",
    "ms-teams",
    "msteams",
    "teams",
    "teams for linux",
    "teams-for-linux"
  ],
  "telegram": [
    "org.telegram.desktop",
    "telegram desktop",
    "telegram-desktop",
    "telegramdesktop",
    "telegram"
  ]
};
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
const LANGUAGE_CODES = [
  "ar", "zh", "cs", "da", "en", "fi", "de", "fr", "el", "hi",
  "it", "ja", "ko", "nl", "no", "pl", "pt", "ru", "es", "sv",
  "tr", "uk"
];
const TRANSCRIBER_METHODS = [
  "auto",
  "whisper",
  "faster-whisper",
  "whisper-cpp",
  "openai-compatible",
  "command"
];
const POST_PROCESS_BACKENDS = [
  "none",
  "command",
  "ollama",
  "openai-compatible"
];
const BOOLEAN_IMPORT_SETTINGS = {
  "show-panel-label": true,
  "auto-transcribe-timeout": true,
  "auto-relisten": true,
  "keep-recording-artifacts": true,
  "notify-recording": true,
  "notify-complete": true,
  "notify-error": true,
  "append-space": true,
  "sanitize-special-chars": true,
  "soften-profanity": false,
  "post-process-preserve-code": true,
  "post-process-never-add-content": true,
  "post-process-mask-sensitive-data": false,
  "openai-compatible-flex-processing": true
};
const IMPORT_TEXT_SETTINGS = {
  "toggle-keybinding": "toggle keybinding",
  "primary-language-keybinding": "primary language keybinding",
  "secondary-language-keybinding": "secondary language keybinding",
  "cancel-keybinding": "cancel recording keybinding",
  "input-device": "input device",
  "personal-context": "personal context",
  "vocabulary": "vocabulary",
  "auto-paste-window-title": "auto-paste window title",
  "whisper-model": "whisper model",
  "transcriber-command": "transcriber command",
  "post-process-command": "post-process command",
  "ollama-url": "ollama URL",
  "ollama-model": "ollama model",
  "openai-compatible-url": "openai-compatible URL",
  "openai-compatible-model": "openai-compatible model",
  "openai-compatible-text-model": "openai-compatible text model",
  "openai-compatible-api-key": "openai-compatible API key",
  "post-process-preset": "post-process preset",
  "post-process-prompt": "post-process prompt"
};
const RECORDING_LIMIT_SECONDS = [
  15,
  30,
  60,
  120,
  300,
  600,
  900,
  1200,
  1800,
  3600
];
const EXPORTABLE_SETTINGS = [
  ["toggle-keybinding", "toggleKeybinding"],
  ["primary-language-keybinding", "primaryLanguageKeybinding"],
  ["secondary-language-keybinding", "secondaryLanguageKeybinding"],
  ["cancel-keybinding", "cancelKeybinding"],
  ["show-panel-label", "showPanelLabel"],
  ["language", "language"],
  ["secondary-language", "secondaryLanguage"],
  ["max-seconds", "maxSeconds"],
  ["auto-transcribe-timeout", "autoTranscribeTimeout"],
  ["auto-relisten", "autoRelisten"],
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
  ["soften-profanity", "softenProfanity"],
  ["max-transcript-files", "maxTranscriptFiles"],
  ["artifact-encryption", "artifactEncryption"],
  ["auto-paste-window-title", "autoPasteWindowTitle"],
  ["transcriber", "transcriber"],
  ["whisper-model", "whisperModel"],
  ["post-process-backend", "postProcessBackend"],
  ["ollama-url", "ollamaUrl"],
  ["ollama-model", "ollamaModel"],
  ["openai-compatible-url", "openaiCompatibleUrl"],
  ["openai-compatible-model", "openaiCompatibleModel"],
  ["openai-compatible-text-model", "openaiCompatibleTextModel"],
  ["openai-compatible-flex-processing", "openaiCompatibleFlexProcessing"],
  ["post-process-preset", "postProcessPreset"],
  ["post-process-preserve-code", "postProcessPreserveCode"],
  ["post-process-never-add-content", "postProcessNeverAddContent"],
  ["post-process-mask-sensitive-data", "postProcessMaskSensitiveData"],
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

  _startLifecycle: function() {
    this.lifecycleState = LIFECYCLE_INITIALIZING;
    this._lifecycleErrors = {};
    this._lifecycleErrorCounts = {};
    this._disabledErrorGroups = {};
    this._resourceRegistry = {
      timers: {},
      signals: [],
      hotkeys: {},
      monitors: [],
      dialogs: [],
      processes: {},
      cancellables: {},
    };
    this._hotkeyDefinitions = {};
    this._teardownComplete = false;
    this._initFailed = false;
    this.appletRemoved = false;
    this.spawnGeneration = 0;
    this.targetWindowGeneration = 0;
    this.terminalWorkflowToken = null;
    this.doctorCommandToken = null;
    this.settingsWindowToken = null;
  },

  _lifecycleAllowsWork: function() {
    return !this._initFailed &&
      this.lifecycleState !== LIFECYCLE_REMOVING &&
      this.lifecycleState !== LIFECYCLE_REMOVED &&
      !this.appletRemoved;
  },

  _lifecycleGroupEnabled: function(group) {
    return !this._disabledErrorGroups || !this._disabledErrorGroups[String(group || "unknown")];
  },

  _lifecycleErrorText: function(error) {
    let raw = "unknown error";
    try {
      raw = error && error.message ? String(error.message) : String(error || raw);
    } catch (ignored) {
      raw = "unknown error";
    }
    try {
      return this._sanitizeErrorMessage(raw) || "unknown error";
    } catch (ignored) {
      return raw.slice(0, MAX_SETTING_TEXT_CHARS);
    }
  },

  _logLifecycleError: function(group, error) {
    try {
      global.logError("Speed of Cinnamon [" + String(group || "unknown") + "]: " + this._lifecycleErrorText(error));
    } catch (ignored) {
      // Logging must never become a second failure during recovery.
    }
  },

  _safeLogError: function(error) {
    try {
      global.logError(error);
    } catch (ignored) {
      // Best-effort diagnostics must never escape a recovery path.
    }
  },

  _recordLifecycleError: function(group, error) {
    let key = "unknown";
    let log = (value) => {
      try {
        this._logLifecycleError(key, value);
      } catch (ignored) {
        // Lifecycle diagnostics must never become a second failure.
      }
    };
    try {
      key = String(group || "unknown");
      let now = Date.now();
      let entries = Array.isArray(this._lifecycleErrors[key]) ? this._lifecycleErrors[key] : [];
      entries = entries.filter((timestamp) => now - timestamp <= LIFECYCLE_ERROR_WINDOW_MS);
      entries.push(now);
      if (entries.length > LIFECYCLE_ERROR_THRESHOLD) {
        entries = entries.slice(-LIFECYCLE_ERROR_THRESHOLD);
      }
      this._lifecycleErrors[key] = entries;
      this._lifecycleErrorCounts[key] = entries.length;
      log(error);
      if (entries.length >= LIFECYCLE_ERROR_THRESHOLD) {
        this._disabledErrorGroups[key] = true;
        if (this.lifecycleState === LIFECYCLE_RUNNING) {
          this.lifecycleState = LIFECYCLE_DEGRADED;
        }
      }
    } catch (recordError) {
      log(error);
      log(recordError);
    }
  },

  _runGuarded: function(group, callback, fallback) {
    let key = "unknown";
    try {
      key = String(group || "unknown");
      if (!this._lifecycleAllowsWork() || !this._lifecycleGroupEnabled(key) || typeof callback !== "function") {
        return fallback;
      }
      return callback();
    } catch (error) {
      this._recordLifecycleError(key, error);
      return fallback;
    }
  },

  _runStateGuarded: function(group, callback, fallback) {
    let key = "state";
    try {
      key = String(group || "state");
      if (!this._lifecycleAllowsWork() || typeof callback !== "function") {
        return fallback;
      }
      return callback();
    } catch (error) {
      this._recordLifecycleError(key, error);
      return fallback;
    }
  },

  _runTeardownGuarded: function(group, callback) {
    if (typeof callback !== "function") {
      return;
    }
    try {
      callback();
    } catch (error) {
      this._recordLifecycleError(group || "teardown", error);
    }
  },

  _runTeardownOperation: function(group, target, method, args, allowMissing) {
    let succeeded = false;
    this._runTeardownGuarded(group, () => {
      if (!target) {
        throw new Error("Teardown operation target is unavailable");
      }
      let operation = target[method];
      if (typeof operation !== "function") {
        if (allowMissing === true) {
          succeeded = true;
          return;
        }
        throw new Error("Teardown operation is unavailable");
      }
      let result = operation.apply(target, Array.isArray(args) ? args : []);
      if (result === false) {
        throw new Error("Teardown operation failed");
      }
      succeeded = true;
    });
    return succeeded;
  },

  _guardCallback: function(group, callback, fallback) {
    if (typeof callback !== "function") {
      return null;
    }
    return (...args) => this._runGuarded(group, () => callback.apply(this, args), fallback);
  },

  _guardStateCallback: function(group, callback, fallback) {
    if (typeof callback !== "function") {
      return null;
    }
    let key = String(group || "state-callback");
    return (...args) => this._runStateGuarded(key, () => callback.apply(this, args), fallback);
  },

  _handleInitializationFailure: function(error) {
    this._initFailed = true;
    this._recordLifecycleError("init", error);
    this.lifecycleState = LIFECYCLE_DEGRADED;
    this._runTeardownGuarded("init-teardown", () => this.on_applet_removed_from_panel());
    try {
      if (this.set_applet_label) {
        this.set_applet_label("ERR");
      }
      if (this.set_applet_tooltip) {
        this.set_applet_tooltip(_("Speed of Cinnamon could not initialize"));
      }
    } catch (ignored) {
      // A partially initialized applet may not have an actor yet.
    }
  },

  _beginTeardown: function() {
    if (this._teardownComplete || this.lifecycleState === LIFECYCLE_REMOVED || this.lifecycleState === LIFECYCLE_REMOVING) {
      return false;
    }
    this.lifecycleState = LIFECYCLE_REMOVING;
    this.appletRemoved = true;
    this.spawnGeneration += 1;
    return true;
  },

  _finishTeardown: function() {
    this.lifecycleState = LIFECYCLE_REMOVED;
    this._teardownComplete = true;
  },

  _bindSetting: function(direction, key, propertyName, callback, callbackThis) {
    let safeCallback = callback ? this._guardStateCallback("settings", callback, undefined) : null;
    return this.settings.bindProperty(direction, key, propertyName, safeCallback, callbackThis);
  },

  _commitSettingValue: function(propertyName, key, value, group, errorMessage) {
    let previous = this[propertyName];
    try {
      let result = this.settings.setValue(key, value);
      if (result === false) {
        throw new Error("Setting could not be saved");
      }
      this[propertyName] = value;
      return true;
    } catch (err) {
      this[propertyName] = previous;
      this._recordLifecycleError(group || "settings", err);
      if (errorMessage) {
        this._setStatusPreservingRecording("error", errorMessage, this.lastTranscript);
      }
      return false;
    }
  },

  _connectSafe: function(target, signal, callback, group) {
    let signalGroup = "signal-callback";
    try {
      if (!this._lifecycleAllowsWork() || !target || typeof target.connect !== "function") {
        return 0;
      }
      signalGroup = "signal-" + String(group || signal || "callback");
      let connectionId = 0;
      connectionId = target.connect(signal, this._guardStateCallback(signalGroup, callback, undefined));
      if (this._resourceRegistry && connectionId) {
        let signalEntry = { target: target, id: connectionId };
        let registryWriteAttempted = false;
        try {
          if (!Array.isArray(this._resourceRegistry.signals)) {
            throw new Error("Signal registry is unavailable");
          }
          registryWriteAttempted = true;
          this._resourceRegistry.signals.push(signalEntry);
          if (this._resourceRegistry.signals.indexOf(signalEntry) < 0) {
            throw new Error("Signal could not be registered");
          }
        } catch (registryError) {
          if (registryWriteAttempted) {
            try {
              let signals = this._resourceRegistry.signals;
              let lastIndex = signals.length - 1;
              if (lastIndex >= 0 && signals[lastIndex] === signalEntry) {
                signals.pop();
              } else {
                let index = signals.indexOf(signalEntry);
                if (index >= 0) {
                  signals.splice(index, 1);
                }
              }
            } catch (rollbackError) {
              this._recordLifecycleError("signal-registration-rollback", rollbackError);
            }
          }
          try {
            if (target.disconnect) {
              target.disconnect(connectionId);
            }
          } catch (disconnectError) {
            this._recordLifecycleError("signal-disconnect", disconnectError);
          }
          throw registryError;
        }
      }
      return connectionId;
    } catch (error) {
      this._recordLifecycleError(signalGroup, error);
      return 0;
    }
  },

  _disconnectAllSignals: function() {
    try {
      if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.signals)) {
        return;
      }
      let signals = this._resourceRegistry.signals;
      for (let index = signals.length - 1; index >= 0; index--) {
        let connection = signals[index];
        if (!this._runTeardownOperation(
          "teardown-signals",
          connection && connection.target,
          "disconnect",
          [connection && connection.id]
        )) {
          continue;
        }
        try {
          signals.splice(index, 1);
        } catch (error) {
          this._recordLifecycleError("teardown-signals", error);
        }
      }
    } catch (error) {
      this._recordLifecycleError("teardown-signals", error);
    }
  },

  _disconnectTrackedSignalsForTarget: function(target) {
    if (!target || !this._resourceRegistry) {
      return true;
    }
    let success = true;
    try {
      if (!Array.isArray(this._resourceRegistry.signals)) {
        return true;
      }
      let signals = this._resourceRegistry.signals;
      for (let index = signals.length - 1; index >= 0; index--) {
        let connection = signals[index];
        if (!connection || connection.target !== target) {
          continue;
        }
        if (!this._runTeardownOperation("teardown-target-signals", target, "disconnect", [connection.id])) {
          success = false;
          continue;
        }
        try {
          signals.splice(index, 1);
        } catch (error) {
          this._recordLifecycleError("teardown-target-signals", error);
          success = false;
        }
      }
      return success;
    } catch (error) {
      this._recordLifecycleError("teardown-target-signals", error);
      return false;
    }
  },

  _clearMenuItems: function(menu) {
    if (!menu) {
      return;
    }
    let targets = [];
    let visited = [];
    let addTarget = (target) => {
      if (target && targets.indexOf(target) < 0) {
        targets.push(target);
      }
    };
    let collect = (current) => {
      if (!current) {
        return;
      }
      if (visited.indexOf(current) >= 0) {
        return;
      }
      visited.push(current);
      let items = [];
      try {
        if (current._getMenuItems) {
          items = current._getMenuItems();
        }
        if (!Array.isArray(items)) {
          throw new Error("Menu items are unavailable");
        }
        for (let item of items) {
          addTarget(item);
          if (item && item.menu) {
            addTarget(item.menu);
            collect(item.menu);
          }
        }
      } catch (error) {
        this._recordLifecycleError("menu-items", error);
      }
    };
    collect(menu);
    for (let target of targets) {
      this._disconnectTrackedSignalsForTarget(target);
    }
    this._runStateGuarded("menu-items", () => {
      if (menu.removeAll) {
        menu.removeAll();
      }
    }, undefined);
  },

  _trackDialog: function(dialog) {
    if (!dialog) {
      return dialog;
    }
    if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.dialogs)) {
      throw new Error("Dialog registry is unavailable");
    }
    let dialogs = this._resourceRegistry.dialogs;
    let added = false;
    try {
      if (dialogs.indexOf(dialog) < 0) {
        dialogs.push(dialog);
        added = true;
        if (dialogs.indexOf(dialog) < 0) {
          throw new Error("Dialog could not be registered");
        }
      }
      return dialog;
    } catch (error) {
      if (added) {
        try {
          let lastIndex = dialogs.length - 1;
          if (lastIndex >= 0 && dialogs[lastIndex] === dialog) {
            dialogs.pop();
          } else {
            let index = dialogs.indexOf(dialog);
            if (index >= 0) {
              dialogs.splice(index, 1);
            }
          }
        } catch (rollbackError) {
          this._recordLifecycleError("dialog-registration-rollback", rollbackError);
        }
      }
      throw error;
    }
  },

  _untrackDialog: function(dialog) {
    if (!dialog || !this._resourceRegistry) {
      return true;
    }
    try {
      if (!Array.isArray(this._resourceRegistry.dialogs)) {
        return true;
      }
      let index = this._resourceRegistry.dialogs.indexOf(dialog);
      if (index < 0) {
        return true;
      }
      this._resourceRegistry.dialogs.splice(index, 1);
      return true;
    } catch (error) {
      this._recordLifecycleError("dialog-untrack", error);
      return false;
    }
  },

  _newSafeDialog: function(group) {
    if (!this._lifecycleAllowsWork()) {
      return null;
    }
    let dialog = null;
    try {
      dialog = new ModalDialog.ModalDialog();
      return this._trackDialog(dialog);
    } catch (error) {
      if (dialog) {
        let cleanupGroup = "dialog-" + String(group || "create") + "-cleanup";
        let closeSucceeded = this._runTeardownOperation(cleanupGroup, dialog, "close");
        let destroySucceeded = this._runTeardownOperation(cleanupGroup, dialog, "destroy");
        if (closeSucceeded && destroySucceeded) {
          this._untrackDialog(dialog);
        }
      }
      this._recordLifecycleError("dialog-" + String(group || "create"), error);
      return null;
    }
  },

  _dialogAddChild: function(dialog, child, group) {
    return this._runGuarded("dialog-" + String(group || "content"), () => {
      if (!dialog || !child || !dialog.contentLayout || !dialog.contentLayout.add_child) {
        return false;
      }
      dialog.contentLayout.add_child(child);
      return true;
    }, false) === true;
  },

  _newSafeLabel: function(text, options, group) {
    return this._runGuarded("dialog-" + String(group || "label"), () => {
      let properties = Object.assign({}, options || {});
      properties.text = String(text || "");
      return new St.Label(properties);
    }, null);
  },

  _dialogSetButtons: function(dialog, buttons, group) {
    if (!dialog || !Array.isArray(buttons)) {
      return false;
    }
    let safeButtons;
    try {
      safeButtons = buttons.map((button) => {
        let safeButton = Object.assign({}, button);
        if (button && typeof button.action === "function") {
          safeButton.action = this._guardStateCallback("dialog-" + String(group || "action"), button.action, undefined);
        }
        return safeButton;
      });
    } catch (error) {
      this._recordLifecycleError("dialog-" + String(group || "buttons"), error);
      return false;
    }
    return this._runGuarded("dialog-" + String(group || "buttons"), () => {
      if (typeof dialog.setButtons !== "function") {
        return false;
      }
      dialog.setButtons(safeButtons);
      return true;
    }, false) === true;
  },

  _dialogClose: function(dialog, group) {
    if (!dialog) {
      return;
    }
    try {
      if (this._resourceRegistry && Array.isArray(this._resourceRegistry.dialogs)) {
        if (this._resourceRegistry.dialogs.indexOf(dialog) < 0) {
          return;
        }
      }
    } catch (error) {
      this._recordLifecycleError("dialog-close", error);
      return;
    }
    let closed = false;
    this._runTeardownGuarded("dialog-" + String(group || "close"), () => {
      if (typeof dialog.close !== "function") {
        throw new Error("Dialog close operation is unavailable");
      }
      let result = dialog.close();
      if (result === false) {
        throw new Error("Dialog close operation failed");
      }
      closed = true;
    });
    if (closed) {
      this._untrackDialog(dialog);
    }
  },

  _dialogOpen: function(dialog, group) {
    if (!dialog || !this._lifecycleAllowsWork()) {
      return false;
    }
    return this._runGuarded("dialog-" + String(group || "open"), () => {
      if (typeof dialog.open !== "function") {
        return false;
      }
      return dialog.open() !== false;
    }, false) === true;
  },

  _destroyTrackedDialogs: function() {
    try {
      if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.dialogs)) {
        return;
      }
      let dialogs = this._resourceRegistry.dialogs;
      for (let index = dialogs.length - 1; index >= 0; index--) {
        let dialog = dialogs[index];
        let closeSucceeded = !dialog || this._runTeardownOperation("teardown-dialog-close", dialog, "close");
        let destroySucceeded = !dialog || this._runTeardownOperation("teardown-dialog-destroy", dialog, "destroy");
        if (closeSucceeded && destroySucceeded) {
          if (dialog) {
            this._untrackDialog(dialog);
          } else {
            dialogs.splice(index, 1);
          }
        }
      }
    } catch (error) {
      this._recordLifecycleError("teardown-dialogs", error);
    }
  },

  _destroyMenus: function() {
    let cleanupMenu = (menu, group) => {
      if (!menu) {
        return true;
      }
      let signalsSucceeded = this._runTeardownOperation("teardown-" + group + "-signals", menu, "disconnectAllSignals", [], true);
      let closeSucceeded = this._runTeardownOperation("teardown-" + group + "-close", menu, "close", [false]);
      let destroySucceeded = this._runTeardownOperation("teardown-" + group + "-destroy", menu, "destroy");
      return signalsSucceeded && closeSucceeded && destroySucceeded;
    };
    let menu = this.menu;
    if (cleanupMenu(menu, "menu")) {
      this.menu = null;
    }
    let contextMenu = this._applet_context_menu;
    if (cleanupMenu(contextMenu, "context-menu")) {
      this._applet_context_menu = null;
    }
    let cleanupManager = (manager, group) => {
      if (!manager) {
        return true;
      }
      let signalsSucceeded = this._runTeardownOperation("teardown-" + group + "-signals", manager, "disconnectAllSignals", [], true);
      let destroySucceeded = this._runTeardownOperation("teardown-" + group + "-destroy", manager, "destroy");
      return signalsSucceeded && destroySucceeded;
    };
    let menuManager = this.menuManager;
    if (cleanupManager(menuManager, "menu-manager")) {
      this.menuManager = null;
    }
    let privateMenuManager = this._menuManager;
    if (cleanupManager(privateMenuManager, "private-menu-manager")) {
      this._menuManager = null;
    }
  },

  _destroyAppletTooltip: function() {
    let tooltip = this._applet_tooltip;
    let destroyed = !tooltip || this._runTeardownOperation("teardown-tooltip", tooltip, "destroy");
    if (destroyed) {
      this._applet_tooltip = null;
    }
  },

  _trackMonitor: function(monitor) {
    if (!monitor) {
      return monitor;
    }
    if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.monitors)) {
      throw new Error("Monitor registry is unavailable");
    }
    let monitors = this._resourceRegistry.monitors;
    let added = false;
    try {
      if (monitors.indexOf(monitor) < 0) {
        monitors.push(monitor);
        added = true;
        if (monitors.indexOf(monitor) < 0) {
          throw new Error("Monitor could not be registered");
        }
      }
      return monitor;
    } catch (error) {
      if (added) {
        try {
          let lastIndex = monitors.length - 1;
          if (lastIndex >= 0 && monitors[lastIndex] === monitor) {
            monitors.pop();
          } else {
            let index = monitors.indexOf(monitor);
            if (index >= 0) {
              monitors.splice(index, 1);
            }
          }
        } catch (rollbackError) {
          this._recordLifecycleError("monitor-registration-rollback", rollbackError);
        }
      }
      throw error;
    }
  },

  _untrackMonitor: function(monitor) {
    if (!monitor || !this._resourceRegistry) {
      return true;
    }
    try {
      if (!Array.isArray(this._resourceRegistry.monitors)) {
        return true;
      }
      let index = this._resourceRegistry.monitors.indexOf(monitor);
      if (index < 0) {
        return true;
      }
      this._resourceRegistry.monitors.splice(index, 1);
      return true;
    } catch (error) {
      this._recordLifecycleError("monitor-untrack", error);
      return false;
    }
  },

  _nextResourceToken: function(prefix) {
    this._resourceTokenSequence = Number(this._resourceTokenSequence || 0) + 1;
    return String(prefix || "resource") + "-" + String(this._resourceTokenSequence);
  },

  _registerCancellable: function(cancellable) {
    let token = this._nextResourceToken("cancellable");
    if (!cancellable) {
      return token;
    }
    let registry = null;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.cancellables) {
        throw new Error("Cancellable registry is unavailable");
      }
      registry = this._resourceRegistry.cancellables;
      registry[token] = cancellable;
      if (registry[token] !== cancellable) {
        throw new Error("Cancellable could not be registered");
      }
      return token;
    } catch (error) {
      if (registry) {
        try {
          if (Object.prototype.hasOwnProperty.call(registry, token)) {
            let deleted = delete registry[token];
            if (deleted === false || Object.prototype.hasOwnProperty.call(registry, token)) {
              throw new Error("Cancellable registration rollback failed");
            }
          }
        } catch (rollbackError) {
          this._recordLifecycleError("cancellable-registration-rollback", rollbackError);
        }
      }
      throw error;
    }
  },

  _unregisterCancellable: function(token) {
    if (!this._resourceRegistry || !token) {
      return true;
    }
    try {
      if (!this._resourceRegistry.cancellables) {
        return true;
      }
      if (!Object.prototype.hasOwnProperty.call(this._resourceRegistry.cancellables, token)) {
        return true;
      }
      let deleted = delete this._resourceRegistry.cancellables[token];
      if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.cancellables, token)) {
        throw new Error("Cancellable could not be unregistered");
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("cancellable-unregister", error);
      return false;
    }
  },

  _registerProcess: function(process, generation, group) {
    let token = this._nextResourceToken("process");
    if (!process) {
      return token;
    }
    let entry = {
      process: process,
      generation: generation,
      group: String(group || "process"),
    };
    let registry = null;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.processes) {
        throw new Error("Process registry is unavailable");
      }
      registry = this._resourceRegistry.processes;
      registry[token] = entry;
      if (registry[token] !== entry) {
        throw new Error("Process could not be registered");
      }
      return token;
    } catch (error) {
      if (registry) {
        try {
          if (Object.prototype.hasOwnProperty.call(registry, token)) {
            let deleted = delete registry[token];
            if (deleted === false || Object.prototype.hasOwnProperty.call(registry, token)) {
              throw new Error("Process registration rollback failed");
            }
          }
        } catch (rollbackError) {
          this._recordLifecycleError("process-registration-rollback", rollbackError);
        }
      }
      throw error;
    }
  },

  _unregisterProcess: function(token) {
    if (!this._resourceRegistry || !token) {
      return true;
    }
    try {
      if (!this._resourceRegistry.processes) {
        return true;
      }
      if (!Object.prototype.hasOwnProperty.call(this._resourceRegistry.processes, token)) {
        return true;
      }
      let deleted = delete this._resourceRegistry.processes[token];
      if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.processes, token)) {
        throw new Error("Process could not be unregistered");
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("process-unregister", error);
      return false;
    }
  },

  _terminateProcess: function(process) {
    if (!process) {
      return;
    }
    try {
      if (!process.get_if_exited() && process.force_exit) {
        process.force_exit();
      }
    } catch (error) {
      this._recordLifecycleError("process-kill", error);
    }
  },

  _terminateAllProcesses: function() {
    try {
      let processes = this._resourceRegistry && this._resourceRegistry.processes
        ? this._resourceRegistry.processes
        : {};
      for (let token in processes) {
        if (Object.prototype.hasOwnProperty.call(processes, token)) {
          try {
            if (processes[token] && typeof processes[token].cancel === "function") {
              processes[token].cancel();
            } else if (processes[token]) {
              this._terminateProcess(processes[token].process);
            }
          } catch (error) {
            this._recordLifecycleError("process-cancel", error);
          } finally {
            this._unregisterProcess(token);
          }
        }
      }
    } catch (error) {
      this._recordLifecycleError("process-cancel", error);
    }
  },

  _terminateProcessesByGroup: function(group, notifyCallback) {
    let wanted = String(group || "process");
    let processes = {};
    try {
      processes = this._resourceRegistry && this._resourceRegistry.processes
        ? this._resourceRegistry.processes
        : {};
      for (let token in processes) {
        if (!Object.prototype.hasOwnProperty.call(processes, token)) {
          continue;
        }
        let entry = null;
        let selected = false;
        try {
          entry = processes[token];
          if (!entry || typeof entry !== "object" || String(entry.group || "process") !== wanted) {
            continue;
          }
          selected = true;
          if (typeof entry.cancel === "function") {
            entry.cancel(Boolean(notifyCallback));
          } else {
            this._terminateProcess(entry.process);
          }
        } catch (error) {
          this._recordLifecycleError("process-cancel", error);
        } finally {
          if (selected) {
            this._unregisterProcess(token);
          }
        }
      }
    } catch (error) {
      this._recordLifecycleError("process-cancel", error);
    }
  },

  _cancelAllCancellables: function() {
    try {
      let cancellables = this._resourceRegistry && this._resourceRegistry.cancellables
        ? this._resourceRegistry.cancellables
        : {};
      for (let token in cancellables) {
        if (!Object.prototype.hasOwnProperty.call(cancellables, token)) {
          continue;
        }
        try {
          if (cancellables[token] && cancellables[token].cancel) {
            cancellables[token].cancel();
          }
        } catch (error) {
          this._recordLifecycleError("teardown-cancellable", error);
        }
        this._unregisterCancellable(token);
      }
    } catch (error) {
      this._recordLifecycleError("teardown-cancellable", error);
    }
  },

  _trackTimer: function(name, sourceId, propertyName) {
    if (!sourceId) {
      return sourceId;
    }
    if (!this._resourceRegistry || !this._resourceRegistry.timers) {
      throw new Error("Timer registry is unavailable");
    }
    let key = String(name || propertyName || "timer");
    this._resourceRegistry.timers[key] = sourceId;
    if (propertyName) {
      this[propertyName] = sourceId;
    }
    return sourceId;
  },

  _untrackTimer: function(name, sourceId, propertyName) {
    let key = String(name || propertyName || "timer");
    try {
      if (this._resourceRegistry && this._resourceRegistry.timers[key] === sourceId) {
        let deleted = delete this._resourceRegistry.timers[key];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.timers, key)) {
          throw new Error("Timer registry entry could not be untracked");
        }
      }
      if (propertyName && (!sourceId || this[propertyName] === sourceId)) {
        this[propertyName] = 0;
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("timer-untrack", error);
      return false;
    }
  },

  _clearTrackedTimer: function(name, propertyName) {
    let key = "timer";
    try {
      key = String(name || propertyName || "timer");
      let sourceId = this._resourceRegistry && this._resourceRegistry.timers
        ? this._resourceRegistry.timers[key]
        : 0;
      if (!sourceId && propertyName) {
        sourceId = this[propertyName];
      }
      if (!sourceId) {
        if (propertyName) {
          this[propertyName] = 0;
        }
        return true;
      }
      let removed = Mainloop.source_remove(sourceId);
      if (removed === false) {
        throw new Error("Timer source could not be removed");
      }
      if (this._resourceRegistry && this._resourceRegistry.timers[key] === sourceId) {
        let deleted = delete this._resourceRegistry.timers[key];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.timers, key)) {
          throw new Error("Timer registry entry could not be removed");
        }
      }
      if (propertyName && this[propertyName] === sourceId) {
        this[propertyName] = 0;
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("timer-clear", error);
      return false;
    }
  },

  _scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName) {
    if (!this._lifecycleAllowsWork() || typeof callback !== "function") {
      return 0;
    }
    let key = String(name || propertyName || "timer");
    if (this._clearTrackedTimer(key, propertyName) === false) {
      return 0;
    }
    let generation = this.spawnGeneration;
    let sourceId = 0;
    let timerCallback = () => {
      if (this.appletRemoved || this.spawnGeneration !== generation) {
        this._untrackTimer(key, sourceId, propertyName);
        return false;
      }
    let keepTimer = this._runStateGuarded("timer-" + key, callback, false) === true;
      if (!keepTimer) {
        this._untrackTimer(key, sourceId, propertyName);
      }
      return keepTimer;
    };
    try {
      sourceId = useSeconds
        ? Mainloop.timeout_add_seconds(Math.max(1, Number(delay || 1)), timerCallback)
        : Mainloop.timeout_add(Math.max(1, Number(delay || 1)), timerCallback);
      let trackedSourceId = this._trackTimer(key, sourceId, propertyName);
      let registryHasTimer = !this._resourceRegistry ||
        (this._resourceRegistry.timers && this._resourceRegistry.timers[key] === sourceId);
      let propertyHasTimer = !propertyName || this[propertyName] === sourceId;
      if (!sourceId || trackedSourceId !== sourceId || !registryHasTimer || !propertyHasTimer) {
        throw new Error("Timer could not be registered");
      }
      return trackedSourceId;
    } catch (error) {
      if (sourceId) {
        try {
          let removed = Mainloop.source_remove(sourceId);
          if (removed === false) {
            throw new Error("Timer rollback could not remove source");
          }
          if (this._resourceRegistry && this._resourceRegistry.timers && this._resourceRegistry.timers[key] === sourceId) {
            let deleted = delete this._resourceRegistry.timers[key];
            if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.timers, key)) {
              throw new Error("Timer rollback registry entry could not be removed");
            }
          }
          if (propertyName && this[propertyName] === sourceId) {
            this[propertyName] = 0;
          }
        } catch (cleanupError) {
          this._recordLifecycleError("timer-cleanup", cleanupError);
        }
      }
      this._recordLifecycleError("timer-" + key, error);
      return 0;
    }
  },

  _init: function(metadata, orientation, panelHeight, instanceId) {
    this._startLifecycle();
    try {
      Applet.TextIconApplet.prototype._init.call(this, orientation, panelHeight, instanceId);

    this.metadata = metadata;
    this.orientation = orientation;
    this.instanceId = instanceId;
    this.toggleKeybinding = "<Super>z::";
    this.primaryLanguageKeybinding = "";
    this.secondaryLanguageKeybinding = "";
    this.cancelKeybinding = "";
    this.showPanelLabel = true;
    this.language = "en";
    this.secondaryLanguage = "de";
    this.activeLanguage = "";
    this.activeLanguageExplicit = false;
    this.maxSeconds = DEFAULT_RECORDING_SECONDS;
    this.autoTranscribeTimeout = true;
    this.autoRelisten = false;
    this.keepRecordingArtifacts = false;
    this.recorder = "auto";
    this.inputDevice = "";
    this.insertMethod = "clipboard-paste";
    this.appendSpace = true;
    this.typingDelayMs = DEFAULT_TYPING_DELAY_MS;
    this.sanitizeSpecialChars = false;
    this.softenProfanity = false;
    this.maxTranscriptFiles = DEFAULT_MAX_TRANSCRIPT_FILES;
    this.artifactEncryption = DEFAULT_ARTIFACT_ENCRYPTION;
    this.autoPasteWindowTitle = DEFAULT_AUTO_PASTE_TITLE;
    this.cliPath = "";
    this.transcriber = "auto";
    this.whisperModel = "";
    this.transcriberCommand = "";
    this.postProcessBackend = "none";
    this.postProcessCommand = "";
    this.ollamaUrl = DEFAULT_OLLAMA_URL;
    this.ollamaModel = "";
    this.openaiCompatibleUrl = DEFAULT_OPENAI_COMPATIBLE_URL;
    this.openaiCompatibleModel = DEFAULT_OPENAI_COMPATIBLE_MODEL;
    this.openaiCompatibleTextModel = DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL;
    this.openaiCompatibleFlexProcessing = true;
    this.openaiCompatibleApiKey = "";
    this.externalApiEnvApiKey = "";
    this.postProcessPreset = TEXT_POLISHING_SAFE_PRESET;
    this.postProcessPreserveCode = true;
    this.postProcessNeverAddContent = true;
    this.postProcessMaskSensitiveData = false;
    this.postProcessPrompt = "";
    this.personalContext = "";
    this.vocabulary = "";
    this.notifyRecording = false;
    this.notifyComplete = false;
    this.notifyError = true;
    this.status = "idle";
    this.lastTranscript = "";
    this.lastMessage = "";
    this.isCommandRunning = false;
    this.terminalWorkflowRunning = false;
    this.terminalWorkflowToken = null;
    this.settingsWindowToken = null;
    this.cancelPendingWhileCommandRunning = false;
    this._statusRefreshToken = 0;
    this._statusCommandRunning = false;
    this._doctorCommandRunning = false;
    this.doctorCommandToken = null;
    this.microphoneLevel = null;
    this.doctorSummaryText = "";
    this.notificationSessionActive = false;
    this.lastNotificationKey = "";
    this.lastArtifactEncryptionWarningKey = "";
    this.lastRejectedArtifactPassphraseWarningKey = "";
    this.selfProtectionNoticeKey = "";
    this.selfProtectionNoticeAtMs = 0;
    this.autoTranscribeRecordingKey = "";
    this.cancelPendingWhileCommandRunning = false;
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenManualStopRequested = false;
    this.autoRelistenSequence = 0;
    this.autoInsertFingerprint = "";
    this.autoInsertFingerprints = [];
    this.transcriptListPromptToken = null;
    this.textInsertToken = null;
    this.voiceModelActionToken = null;
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = 0;
    this.transcriptWindowToken = null;
    this.targetWindow = null;
    this.targetWindowXid = "";
    this.targetWindowXTitle = "";
    this.targetWindowXClass = "";
    this.clipboard = St.Clipboard.get_default();
    this.statusTimer = 0;
    this.displayTimer = 0;
    this.setupCheckTimer = 0;
    this.pasteTimer = 0;
    this._clipboardOverwriteApproval = null;
    this.alarmTimer = 0;
    this.ollamaInstallWatchTimer = 0;
    this.ollamaInstallWatchPolls = 0;
    this.ollamaModelInstallRunning = false;
    this.externalApiEnvMonitor = null;
    this.externalApiEnvApplyTarget = "voice";
    this.set_applet_icon_path(this.metadata.path + "/icon.svg");
    this.set_applet_label("");
    this.set_applet_tooltip(_("Speed of Cinnamon"));

    this.settings = new Settings.AppletSettings(this, UUID, instanceId);
    this._bindSettings();
    this._syncExternalApiConfigOnStartup();
    this._syncActiveLanguage();
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this._buildMenu();
    this._registerHotkeys();
    this._refreshStatus();
    this._scheduleSetupCheck();
    this._scheduleAlarmCheck(5);
      this.lifecycleState = LIFECYCLE_RUNNING;
    } catch (error) {
      this._handleInitializationFailure(error);
    }
  },

  _bindSettings: function() {
    this._bindSetting(Settings.BindingDirection.IN, "toggle-keybinding", "toggleKeybinding", this._onHotkeyChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "primary-language-keybinding", "primaryLanguageKeybinding", this._onHotkeyChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "secondary-language-keybinding", "secondaryLanguageKeybinding", this._onHotkeyChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "cancel-keybinding", "cancelKeybinding", this._onHotkeyChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "show-panel-label", "showPanelLabel", this._updatePanel, null);
    this._bindSetting(Settings.BindingDirection.IN, "language", "language", this._onLanguageSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "secondary-language", "secondaryLanguage", this._onLanguageSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", this._onRecordingLimitSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "auto-transcribe-timeout", "autoTranscribeTimeout", this._onRecordingOptionsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "auto-relisten", "autoRelisten", this._onRecordingOptionsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "keep-recording-artifacts", "keepRecordingArtifacts", this._onRecordingOptionsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "recorder", "recorder", this._onRecorderSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "input-device", "inputDevice", this._onInputSourceSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "insert-method", "insertMethod", this._onOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "append-space", "appendSpace", this._onTextOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "typing-delay-ms", "typingDelayMs", this._onTextOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "sanitize-special-chars", "sanitizeSpecialChars", this._onTextOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "soften-profanity", "softenProfanity", this._onTextOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "max-transcript-files", "maxTranscriptFiles", this._onTranscriptRetentionSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "artifact-encryption", "artifactEncryption", this._onTextOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "auto-paste-window-title", "autoPasteWindowTitle", this._onTextOutputSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "cli-path", "cliPath", null, null);
    this._bindSetting(Settings.BindingDirection.IN, "transcriber", "transcriber", this._onVoiceBackendSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "whisper-model", "whisperModel", this._onVoiceBackendSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "transcriber-command", "transcriberCommand", this._onVoiceBackendSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-backend", "postProcessBackend", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-command", "postProcessCommand", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "ollama-url", "ollamaUrl", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "ollama-model", "ollamaModel", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-url", "openaiCompatibleUrl", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-model", "openaiCompatibleModel", this._onVoiceBackendSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-text-model", "openaiCompatibleTextModel", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-flex-processing", "openaiCompatibleFlexProcessing", this._onOpenAiFlexProcessingSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "openai-compatible-api-key", "openaiCompatibleApiKey", this._onVoiceBackendSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-preset", "postProcessPreset", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-preserve-code", "postProcessPreserveCode", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-never-add-content", "postProcessNeverAddContent", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-mask-sensitive-data", "postProcessMaskSensitiveData", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "post-process-prompt", "postProcessPrompt", this._onTextModelSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "personal-context", "personalContext", null, null);
    this._bindSetting(Settings.BindingDirection.IN, "vocabulary", "vocabulary", null, null);
    this._bindSetting(Settings.BindingDirection.IN, "notify-recording", "notifyRecording", this._onNotificationSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "notify-complete", "notifyComplete", this._onNotificationSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "notify-error", "notifyError", this._onNotificationSettingsChanged, null);
  },

  _buildMenu: function() {
    this.menuManager = new PopupMenu.PopupMenuManager(this);
    this.menu = new PopupMenu.PopupMenu(this.actor, this.orientation);
    Main.uiGroup.add_actor(this.menu.actor);
    this.menu.actor.hide();
    this.menuManager.addMenu(this.menu);
    this._connectSafe(this.menu, "open-state-changed", (menu, open) => {
      if (!this._applet_context_menu || !this._applet_context_menu.isOpen) {
        this.actor.change_style_pseudo_class("checked", open);
      }
    }, "menu-open-state");
    this._connectSafe(this, "orientation-changed", (applet, orientation) => {
      if (this.menu && this.menu.setOrientation) {
        this.menu.setOrientation(orientation);
      }
    }, "menu-orientation");

    this.toggleItem = new PopupMenu.PopupIconMenuItem(_("Start dictation"), "audio-input-microphone-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(this.toggleItem, "activate", () => {
      this._rememberFocusedWindow(true);
      this._toggleRecording();
    });
    this.menu.addMenuItem(this.toggleItem);

    this.cancelItem = new PopupMenu.PopupIconMenuItem(_("Cancel recording"), "process-stop-symbolic", St.IconType.SYMBOLIC);
    this.cancelItem.setSensitive(false);
    this._connectSafe(this.cancelItem, "activate", () => this._cancelRecording());
    this.menu.addMenuItem(this.cancelItem);

    this.statusItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Status: idle")), { maxWidthEm: MENU_LABEL_WIDTH_EM });
    this.statusItem.setSensitive(false);
    this.menu.addMenuItem(this.statusItem);

    this.microphoneLevelItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Microphone: idle")), { maxWidthEm: MENU_LABEL_WIDTH_EM });
    this.microphoneLevelItem.setSensitive(false);
    this.menu.addMenuItem(this.microphoneLevelItem);

    this.doctorSummaryItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("Doctor: not checked")), { maxWidthEm: MENU_LABEL_WIDTH_EM, wrap: true });
    this.doctorSummaryItem.setSensitive(false);

    this.languageItem = new PopupMenu.PopupSubMenuMenuItem(_("Language: en"));
    this._connectSafe(this.languageItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateLanguageMenu();
      }
    });
    this.menu.addMenuItem(this.languageItem);
    this._populateLanguageMenu();

    this.recordingMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording"));
    this.menu.addMenuItem(this.recordingMenuItem);

    this.textOutputMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Text and output"));
    this.menu.addMenuItem(this.textOutputMenuItem);

    this.transcriptsMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Transcripts"));
    this.menu.addMenuItem(this.transcriptsMenuItem);

    this.toolsMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Tools"));
    this.menu.addMenuItem(this.toolsMenuItem);

    this.recorderItem = new PopupMenu.PopupSubMenuMenuItem(_("Recorder: Automatic"));
    this._connectSafe(this.recorderItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecorderMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.recorderItem);
    this._populateRecorderMenu();

    this.recordingLimitItem = new PopupMenu.PopupSubMenuMenuItem(_("Duration: 30s"));
    this._connectSafe(this.recordingLimitItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecordingLimitMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.recordingLimitItem);
    this._populateRecordingLimitMenu();

    this.recordingOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording options"));
    this._connectSafe(this.recordingOptionsItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecordingOptionsMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.recordingOptionsItem);
    this._populateRecordingOptionsMenu();

    this.notificationOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Notifications"));
    this._connectSafe(this.notificationOptionsItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateNotificationOptionsMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.notificationOptionsItem);
    this._populateNotificationOptionsMenu();

    this.alarmItem = new PopupMenu.PopupSubMenuMenuItem(_("Alarms"));
    this._connectSafe(this.alarmItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._refreshAlarmMenu();
      }
    });
    this.toolsMenuItem.menu.addMenuItem(this.alarmItem);
    this._populateAlarmMenu([], _("Open menu to load alarms"));

    this.shortcutItem = new PopupMenu.PopupSubMenuMenuItem(_("Keyboard shortcuts"));
    this._connectSafe(this.shortcutItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateShortcutMenu();
      }
    });
    this.toolsMenuItem.menu.addMenuItem(this.shortcutItem);
    this._populateShortcutMenu();

    this.outputMethodItem = new PopupMenu.PopupSubMenuMenuItem(_("Output: Clipboard and paste"));
    this.textOutputMenuItem.menu.addMenuItem(this.outputMethodItem);
    this._populateOutputMethodMenu();

    this.artifactEncryptionItem = new PopupMenu.PopupSubMenuMenuItem(_("Encryption: Secret Service keyring"));
    this._connectSafe(this.artifactEncryptionItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateArtifactEncryptionMenu();
      }
    });
    this.textOutputMenuItem.menu.addMenuItem(this.artifactEncryptionItem);
    this._populateArtifactEncryptionMenu();

    this.textOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Text options"));
    this._connectSafe(this.textOptionsItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateTextOptionsMenu();
      }
    });
    this.textOutputMenuItem.menu.addMenuItem(this.textOptionsItem);
    this._populateTextOptionsMenu();

    this.autoPasteItem = new PopupMenu.PopupSubMenuMenuItem(_("Auto-Submit: codex"));
    this._connectSafe(this.autoPasteItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._populateAutoPasteMenu();
      }
    });
    this.textOutputMenuItem.menu.addMenuItem(this.autoPasteItem);
    this._populateAutoPasteMenu();

    this.transcriptItem = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(_("No transcript yet")), { maxWidthEm: MENU_LABEL_WIDTH_EM });
    this.transcriptItem.setSensitive(false);
    this.transcriptsMenuItem.menu.addMenuItem(this.transcriptItem);

    this.copyLastItem = new PopupMenu.PopupIconMenuItem(_("Copy last transcript"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this.copyLastItem.setSensitive(false);
    this._connectSafe(this.copyLastItem, "activate", () => this._copyLastTranscript());
    this.transcriptsMenuItem.menu.addMenuItem(this.copyLastItem);

    this.insertLastItem = new PopupMenu.PopupIconMenuItem(_("Insert last transcript"), "edit-paste-symbolic", St.IconType.SYMBOLIC);
    this.insertLastItem.setSensitive(false);
    this._connectSafe(this.insertLastItem, "activate", () => this._insertLastTranscript());
    this.transcriptsMenuItem.menu.addMenuItem(this.insertLastItem);

    this.historyItem = new PopupMenu.PopupSubMenuMenuItem(_("Recent transcripts"));
    this._connectSafe(this.historyItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._refreshHistory();
      }
    });
    this.transcriptsMenuItem.menu.addMenuItem(this.historyItem);
    this._populateHistoryMenu([]);

    let statusNow = new PopupMenu.PopupIconMenuItem(_("Refresh status"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(statusNow, "activate", () => this._refreshStatus());
    this.toolsMenuItem.menu.addMenuItem(statusNow);

    let restartApplet = new PopupMenu.PopupIconMenuItem(_("Restart applet"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(restartApplet, "activate", () => this._restartApplet());
    this.toolsMenuItem.menu.addMenuItem(restartApplet);

    let doctor = new PopupMenu.PopupIconMenuItem(_("Run doctor"), "dialog-information-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(doctor, "activate", () => this._runDoctor());
    this.toolsMenuItem.menu.addMenuItem(doctor);

    let openSettings = new PopupMenu.PopupIconMenuItem(_("Open applet settings"), "preferences-system-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(openSettings, "activate", () => this._openAppletSettings());
    this.toolsMenuItem.menu.addMenuItem(openSettings);

    let openGuide = new PopupMenu.PopupIconMenuItem(_("Open setup guide"), "help-browser-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(openGuide, "activate", () => this._openSetupGuide());
    this.toolsMenuItem.menu.addMenuItem(openGuide);

    this.installMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Install"));
    this.toolsMenuItem.menu.addMenuItem(this.installMenuItem);

    let installOllamaRuntime = new PopupMenu.PopupIconMenuItem(_("Install Ollama"), "system-software-install-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(installOllamaRuntime, "activate", () => this._installOllamaRuntime());
    this.installMenuItem.menu.addMenuItem(installOllamaRuntime);

    let uninstallOllamaRuntime = new PopupMenu.PopupIconMenuItem(_("Uninstall Ollama"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(uninstallOllamaRuntime, "activate", () => this._uninstallOllamaRuntime());
    this.installMenuItem.menu.addMenuItem(uninstallOllamaRuntime);

    let basicSetup = new PopupMenu.PopupIconMenuItem(_("Basic setup"), "emblem-system-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(basicSetup, "activate", () => this._runBasicSetup());
    this.installMenuItem.menu.addMenuItem(basicSetup);

    let installOllamaModel = new PopupMenu.PopupIconMenuItem(_("Choose Ollama text model"), "view-list-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(installOllamaModel, "activate", () => this._chooseOllamaTextModel());
    this.installMenuItem.menu.addMenuItem(installOllamaModel);

    this.diagnosticsMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Diagnostics"));
    this.toolsMenuItem.menu.addMenuItem(this.diagnosticsMenuItem);
    this.diagnosticsMenuItem.menu.addMenuItem(this.doctorSummaryItem);

    let setupPlan = new PopupMenu.PopupIconMenuItem(_("Copy setup plan"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(setupPlan, "activate", () => this._copySetupPlan());
    this.diagnosticsMenuItem.menu.addMenuItem(setupPlan);

    let setupCommands = new PopupMenu.PopupIconMenuItem(_("Copy setup commands"), "utilities-terminal-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(setupCommands, "activate", () => this._copySetupCommands());
    this.diagnosticsMenuItem.menu.addMenuItem(setupCommands);

    let diagnostics = new PopupMenu.PopupIconMenuItem(_("Copy diagnostics"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(diagnostics, "activate", () => this._copyDiagnostics());
    this.diagnosticsMenuItem.menu.addMenuItem(diagnostics);

    let saveDiagnostics = new PopupMenu.PopupIconMenuItem(_("Save diagnostics"), "document-save-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(saveDiagnostics, "activate", () => this._saveDiagnostics());
    this.diagnosticsMenuItem.menu.addMenuItem(saveDiagnostics);

    let benchmark = new PopupMenu.PopupIconMenuItem(_("Benchmark downloaded models"), "utilities-system-monitor-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(benchmark, "activate", () => this._selectBenchmarkAudioFile());
    this.diagnosticsMenuItem.menu.addMenuItem(benchmark);

    this.inputSourceItem = new PopupMenu.PopupSubMenuMenuItem(_("Input source"));
    this._connectSafe(this.inputSourceItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._refreshInputSourceMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.inputSourceItem);
    this._populateInputSourceMenu([], _("Open menu to load input sources"));

    this.modelItem = new PopupMenu.PopupSubMenuMenuItem(_("Voice model"));
    this._connectSafe(this.modelItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._refreshModelMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.modelItem);
    this._populateModelMenu([], _("Open menu to load voice models"));

    this.textModelItem = new PopupMenu.PopupSubMenuMenuItem(_("Text model"));
    this._connectSafe(this.textModelItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._refreshTextModelMenu();
      }
    });
    this.textOutputMenuItem.menu.addMenuItem(this.textModelItem);
    this._populateTextModelMenu([], _("Open menu to load local text models"));

    this.maintenanceMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Files and settings"));
    this.toolsMenuItem.menu.addMenuItem(this.maintenanceMenuItem);

    let transcripts = new PopupMenu.PopupIconMenuItem(_("Open transcripts"), "folder-documents-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(transcripts, "activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_state_dir(), "speed-of-cinnamon", "transcripts"]), _("Opened transcripts"));
    });
    this.maintenanceMenuItem.menu.addMenuItem(transcripts);

    let listTranscripts = new PopupMenu.PopupIconMenuItem(_("List all Transcripts"), "view-list-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(listTranscripts, "activate", () => this._listAllTranscripts());
    this.maintenanceMenuItem.menu.addMenuItem(listTranscripts);

    let exportTranscripts = new PopupMenu.PopupIconMenuItem(_("Export all Transcripts"), "document-save-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(exportTranscripts, "activate", () => this._exportAllTranscripts());
    this.maintenanceMenuItem.menu.addMenuItem(exportTranscripts);

    let cleanupPreview = new PopupMenu.PopupIconMenuItem(_("Preview cleanup"), "edit-find-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(cleanupPreview, "activate", () => this._previewCleanup());
    this.maintenanceMenuItem.menu.addMenuItem(cleanupPreview);

    let cleanup = new PopupMenu.PopupIconMenuItem(_("Clean all old files"), "edit-clear-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(cleanup, "activate", () => this._cleanupOldFiles());
    this.maintenanceMenuItem.menu.addMenuItem(cleanup);

    let exportSettings = new PopupMenu.PopupIconMenuItem(_("Export settings"), "document-save-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(exportSettings, "activate", () => this._exportSettings());
    this.maintenanceMenuItem.menu.addMenuItem(exportSettings);

    let importSettings = new PopupMenu.PopupIconMenuItem(_("Import settings"), "document-open-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(importSettings, "activate", () => this._importSettings());
    this.maintenanceMenuItem.menu.addMenuItem(importSettings);

    this._styleWideMenus();
  },

  _styleWideMenus: function() {
    if (this.menu && this.menu.box && this.menu.box.set_style) {
      this.menu.box.set_style("min-width: " + String(MENU_MIN_WIDTH_EM) + "em;");
    }
    this._styleSelectionSubmenu(this.recordingMenuItem);
    this._styleSelectionSubmenu(this.textOutputMenuItem);
    this._styleSelectionSubmenu(this.transcriptsMenuItem);
    this._styleSelectionSubmenu(this.toolsMenuItem);
    this._styleSelectionSubmenu(this.installMenuItem);
    this._styleSelectionSubmenu(this.diagnosticsMenuItem);
    this._styleSelectionSubmenu(this.maintenanceMenuItem);
    this._styleSelectionSubmenu(this.inputSourceItem);
    this._styleSelectionSubmenu(this.modelItem);
    this._styleSelectionSubmenu(this.textModelItem);
    this._styleSelectionSubmenu(this.autoPasteItem);
    this._styleSelectionSubmenu(this.artifactEncryptionItem);
  },

  _styleSelectionSubmenu: function(menuItem) {
    return this._runGuarded("menu-style", () => {
      if (!menuItem || !menuItem.menu) {
        return;
      }
      let style = "min-width: " + String(SELECTION_MENU_MIN_WIDTH_EM) + "em;";
      if (menuItem.menu.box && menuItem.menu.box.set_style) {
        menuItem.menu.box.set_style(style);
      }
      if (menuItem.menu.actor && menuItem.menu.actor.set_style) {
        menuItem.menu.actor.set_style(style);
      }
    }, undefined);
  },

  _styleMenuItemLabel: function(item, options) {
    return this._runGuarded("menu-style", () => {
      options = options || {};
      if (!item || !item.label) {
        return item;
      }
      let maxWidth = Number(options.maxWidthEm || SELECTION_MENU_LABEL_WIDTH_EM);
      if (item.label.set_style) {
        item.label.set_style("max-width: " + String(maxWidth) + "em;");
      }
      try {
        if (item.label.clutter_text) {
          item.label.clutter_text.ellipsize = options.wrap ? Pango.EllipsizeMode.NONE : Pango.EllipsizeMode.END;
          item.label.clutter_text.line_wrap = Boolean(options.wrap);
          if (options.wrap) {
            item.label.clutter_text.line_wrap_mode = Pango.WrapMode.WORD_CHAR;
          }
        }
      } catch (err) {
        this._safeLogError(err);
      }
      return item;
    }, item);
  },

  _selectionMenuItem: function(label) {
    return this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(typeof label === "string" ? label : ""));
  },

  _selectionInfoItem: function(label) {
    let item = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(typeof label === "string" ? label : ""), { wrap: true });
    item.setSensitive(false);
    return item;
  },

  _shortMenuText: function(value, maxChars) {
    let text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
    let limit = Math.max(16, Number(maxChars || 72));
    if (text.length <= limit) {
      return text;
    }
    let head = Math.max(8, Math.floor((limit - 3) * 0.55));
    let tail = Math.max(5, limit - 3 - head);
    return text.slice(0, head) + "..." + text.slice(text.length - tail);
  },

  _uiMessageText: function(value) {
    return this._shortMenuText(typeof value === "string" ? value : "", MAX_UI_MESSAGE_CHARS);
  },

  _sanitizeErrorMessage: function(value) {
    let text = typeof value === "string" ? value : "";
    if (value instanceof Error && typeof value.message === "string") {
      text = value.message;
    }
    text = text.replace(NUL_RE, "");
    if (SENSITIVE_ERROR_RE.test(text) || LOCAL_PATH_ERROR_RE.test(text)) {
      return "[redacted error details]";
    }
    if (text.length > MAX_SETTING_TEXT_CHARS) {
      return text.slice(0, MAX_SETTING_TEXT_CHARS) + "...";
    }
    return text;
  },

  _payloadMessage: function(payload, fallback) {
    if (payload && typeof payload.message === "string" && payload.message.trim() !== "") {
      return this._sanitizeErrorMessage(payload.message);
    }
    return this._sanitizeErrorMessage(fallback || "");
  },

  _payloadErrorMessage: function(payload, fallback) {
    let value = payload && typeof payload.error === "string" && payload.error.trim() !== ""
      ? payload.error
      : payload && typeof payload.message === "string" && payload.message.trim() !== ""
        ? payload.message
        : fallback || "";
    return this._sanitizeErrorMessage(value);
  },

  _normalizePayloadStatus: function(value, hasError) {
    if (value === undefined || value === null || value === "") {
      return hasError ? "error" : "idle";
    }
    if (typeof value !== "string") {
      return "error";
    }
    let normalized = value.trim().toLowerCase();
    if (normalized === "") {
      return hasError ? "error" : "idle";
    }
    if (normalized === "finalizing") {
      return "processing";
    }
    return PAYLOAD_STATUSES.indexOf(normalized) >= 0 ? normalized : "error";
  },

  _hotkeyName: function(id) {
    return id + "-" + this.instanceId;
  },

  _registerHotkey: function(id, binding, callback) {
    if (!this._lifecycleAllowsWork()) {
      return;
    }
    let name = this._hotkeyName(id);
    let previous = this._hotkeyDefinitions && this._hotkeyDefinitions[name]
      ? this._hotkeyDefinitions[name]
      : null;
    let removedExternally = false;
    let removed = this._runStateGuarded("hotkeys", () => {
      Main.keybindingManager.removeHotKey(name);
      removedExternally = true;
      if (this._resourceRegistry) {
        let deleted = delete this._resourceRegistry.hotkeys[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
          throw new Error("Hotkey registry entry could not be removed");
        }
      }
      return true;
    }, false) === true;
    if (!removed) {
      if (removedExternally && previous) {
        this._runStateGuarded("hotkeys", () => {
          Main.keybindingManager.addHotKey(
            name,
            previous.binding,
            this._guardStateCallback("hotkeys", previous.callback, undefined)
          );
        }, undefined);
      }
      return;
    }
    let accelerator = typeof binding === "string" ? binding.trim() : "";
    if (accelerator === "") {
      if (this._hotkeyDefinitions) {
        this._runStateGuarded("hotkeys", () => {
          let deleted = delete this._hotkeyDefinitions[name];
          if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
            throw new Error("Hotkey definition could not be removed");
          }
        }, undefined);
      }
      return;
    }
    let hasBinding = accelerator.split("::").some((part) => String(part || "").trim() !== "");
    let registered = false;
    if (hasBinding) {
      registered = this._runStateGuarded("hotkeys", () => {
        return Main.keybindingManager.addHotKey(name, accelerator, this._guardStateCallback("hotkeys", callback, undefined)) === true;
      }, false) === true;
    }
    if (registered) {
      let tracked = this._runStateGuarded("hotkeys", () => {
        if (!this._resourceRegistry || !this._resourceRegistry.hotkeys || !this._hotkeyDefinitions) {
          throw new Error("Hotkey registry is unavailable");
        }
        this._resourceRegistry.hotkeys[name] = true;
        this._hotkeyDefinitions[name] = { binding: accelerator, callback: callback };
        return true;
      }, false) === true;
      if (tracked) {
        return;
      }
      this._runStateGuarded("hotkeys", () => {
        Main.keybindingManager.removeHotKey(name);
      }, undefined);
      registered = false;
    }
    if (previous) {
      let restored = this._runStateGuarded("hotkeys", () => {
        return Main.keybindingManager.addHotKey(
          name,
          previous.binding,
          this._guardStateCallback("hotkeys", previous.callback, undefined)
        ) === true;
      }, false) === true;
      if (restored) {
        let tracked = this._runStateGuarded("hotkeys", () => {
          if (this._resourceRegistry) {
            this._resourceRegistry.hotkeys[name] = true;
          }
          this._hotkeyDefinitions[name] = previous;
          return true;
        }, false) === true;
        if (tracked) {
          return;
        }
        this._runStateGuarded("hotkeys", () => {
          Main.keybindingManager.removeHotKey(name);
        }, undefined);
      }
    }
    if (this._resourceRegistry) {
      this._runStateGuarded("hotkeys", () => {
        let deleted = delete this._resourceRegistry.hotkeys[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
          throw new Error("Hotkey registry cleanup could not be completed");
        }
      }, undefined);
    }
    if (this._hotkeyDefinitions) {
      this._runStateGuarded("hotkeys", () => {
        let deleted = delete this._hotkeyDefinitions[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
          throw new Error("Hotkey definition cleanup could not be completed");
        }
      }, undefined);
    }
  },

  _removeHotkey: function(id) {
    let name = this._hotkeyName(id);
    let removed = false;
    this._runTeardownGuarded("teardown-hotkeys", () => {
      Main.keybindingManager.removeHotKey(name);
      removed = true;
    });
    if (!removed) {
      return;
    }
    if (this._resourceRegistry) {
      this._runTeardownGuarded("teardown-hotkeys", () => {
        let deleted = delete this._resourceRegistry.hotkeys[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
          throw new Error("Hotkey registry entry could not be removed during teardown");
        }
      });
    }
    if (this._hotkeyDefinitions) {
      this._runTeardownGuarded("teardown-hotkeys", () => {
        let deleted = delete this._hotkeyDefinitions[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
          throw new Error("Hotkey definition could not be removed during teardown");
        }
      });
    }
  },

  _registerHotkeys: function() {
    this._runStateGuarded("hotkeys", () => {
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
      this._registerHotkey(CANCEL_HOTKEY_ID, this.cancelKeybinding, () => {
        this._cancelRecording();
      });
    }, undefined);
  },

  _onHotkeyChanged: function() {
    this._registerHotkeys();
    this._populateShortcutMenu();
  },

  _onOutputSettingsChanged: function() {
    this._cancelTextInsertForSettingsChange();
    this.insertMethod = this._normalizeOutputMethod(this.insertMethod);
    this._populateOutputMethodMenu();
    this._updatePanel();
  },

  _onTextOutputSettingsChanged: function() {
    this._cancelTextInsertForSettingsChange();
    this.autoPastePromptToken = null;
    this.typingDelayMs = this._normalizeTypingDelayMs(this.typingDelayMs);
    this.artifactEncryption = this._normalizeArtifactEncryption(this.artifactEncryption);
    this._populateArtifactEncryptionMenu();
    this._populateTextOptionsMenu();
    this._updateAutoPasteItem();
    this._populateAutoPasteMenu();
    this._updatePanel();
  },

  _onTranscriptRetentionSettingsChanged: function() {
    this.customLimitPromptToken = null;
    this.maxTranscriptFiles = this._normalizeTranscriptLimit(this.maxTranscriptFiles);
    this._populateTranscriptStorageMenu();
    this._updatePanel();
  },

  _onRecorderSettingsChanged: function() {
    this.recorder = this._normalizeRecorder(this.recorder);
    this._populateRecorderMenu();
    this._updatePanel();
  },

  _onRecordingLimitSettingsChanged: function() {
    this.customLimitPromptToken = null;
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

  _onInputSourceSettingsChanged: function() {
    this.inputSourceMenuRefreshToken = null;
    this._populateInputSourceMenu([], _("Open menu to load input sources"));
    this._updatePanel();
  },

  _onVoiceBackendSettingsChanged: function() {
    this.modelMenuRefreshToken = null;
    this.voiceModelActionToken = null;
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this._populateModelMenu([], _("Open menu to load voice models"));
    this._updatePanel();
  },

  _onTextModelSettingsChanged: function() {
    this._cancelOllamaInstallWatch();
    this._clearOllamaModelFlow();
    this.textModelMenuRefreshToken = null;
    this._populateTextModelMenu([], _("Open menu to load local text models"));
    this._updatePanel();
  },

  _onOpenAiFlexProcessingSettingsChanged: function() {
    this.openaiCompatibleFlexProcessing = Boolean(this.openaiCompatibleFlexProcessing);
    this._updateOpenAiFlexProcessingItem();
    this._updatePanel();
  },

  _cancelTextInsertForSettingsChange: function() {
    this._clearClipboardOverwriteApproval();
    if (!this.textInsertToken) {
      return;
    }
    this.textInsertToken = null;
    this._clearPasteTimer();
    this._terminateProcessesByGroup("keyboard");
    this._terminateProcessesByGroup("clipboard");
    this._terminateProcessesByGroup("x11");
    if (this.autoRelistenPending) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
    }
  },

  on_applet_clicked: function() {
    return this._runStateGuarded("menu-toggle", () => {
      let menu = this.menu;
      if (!menu || typeof menu.toggle !== "function") {
        return;
      }
      if (!menu.isOpen) {
        this._rememberFocusedWindow();
      }
      menu.toggle();
    }, undefined);
  },

  on_applet_removed_from_panel: function() {
    if (!this._beginTeardown()) {
      return;
    }
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenManualStopRequested = false;
    this.terminalWorkflowRunning = false;
    this.modelMenuRefreshToken = null;
    this.textModelMenuRefreshToken = null;
    this.historyRefreshToken = null;
    this.alarmMenuRefreshToken = null;
    this.inputSourceMenuRefreshToken = null;
    this.voiceModelActionToken = null;
    this.ollamaModelFlowToken = null;
    this.ollamaInstallWatchToken = null;
    this.ollamaModelInstallRunning = false;
    this.benchmarkFlowToken = null;
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    this.transcriptListPromptToken = null;
    this.transcriptWindowToken = null;
    this.textInsertToken = null;
    this.settingsWindowToken = null;
    this.alarmActionToken = null;
    this.alarmCheckToken = null;
    this.settingsTransferToken = null;
    this.setupDiagnosticsToken = null;
    this.doctorCommandToken = null;
    this._doctorCommandRunning = false;
    this._runTeardownGuarded("teardown-processes", () => this._terminateAllProcesses());
    this._runTeardownGuarded("teardown-cancellables", () => this._cancelAllCancellables());
    this._runTeardownGuarded("teardown-dialogs", () => this._destroyTrackedDialogs());
    this._runTeardownGuarded("teardown-timer", () => this._clearStatusTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearDisplayTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearSetupCheckTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearPasteTimer());
    this._runTeardownGuarded("teardown-clipboard", () => this._clearClipboardOverwriteApproval());
    this._runTeardownGuarded("teardown-timer", () => this._clearAlarmTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearOllamaInstallWatchTimer());
    this._runTeardownGuarded("teardown-monitor", () => this._clearExternalApiEnvMonitor());
    this._runTeardownGuarded("teardown-signals", () => this._disconnectAllSignals());
    this._runTeardownGuarded("teardown-applet-signals", () => {
      if (this.disconnectAllSignals) {
        this.disconnectAllSignals();
      }
    });
    this._removeHotkey(HOTKEY_ID);
    this._removeHotkey(PRIMARY_HOTKEY_ID);
    this._removeHotkey(SECONDARY_HOTKEY_ID);
    this._removeHotkey(CANCEL_HOTKEY_ID);
    this._runTeardownGuarded("teardown-menus", () => this._destroyMenus());
    if (this.settings) {
      this._runTeardownGuarded("teardown-settings", () => this.settings.finalize());
    }
    this._finishTeardown();
    this._destroyAppletTooltip();
  },

  _baseArgs: function(command) {
    let safeInputDevice = this._coerceCliTextArgOrFallback(this.inputDevice, "input device", "");
    let safeTranscriberCommand = this._coerceCliTextArgOrFallback(this.transcriberCommand, "transcriber command", "");
    let safePostProcessCommand = this._coerceCliTextArgOrFallback(this.postProcessCommand, "post-process command", "");
    let safeOllamaUrl = this._coerceCliTextArgOrFallback(this.ollamaUrl, "ollama URL", DEFAULT_OLLAMA_URL);
    let safeOllamaModel = this._coerceCliTextArgOrFallback(this.ollamaModel, "ollama model", "");
    let safeOpenAiCompatibleUrl = this._coerceCliTextArgOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL);
    let safeOpenAiCompatibleModel = this._coerceCliTextArgOrFallback(this.openaiCompatibleModel, "openai-compatible model", DEFAULT_OPENAI_COMPATIBLE_MODEL);
    let safeOpenAiCompatibleTextModel = this._coerceCliTextArgOrFallback(this.openaiCompatibleTextModel, "openai-compatible text model", DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL);
    let safePostProcessPrompt = this._coerceCliTextArgOrFallback(this._effectivePostProcessPrompt(), "post-process prompt", "");
    let safeWhisperModel = this._coerceCliTextArgOrFallback(this.whisperModel, "whisper model", "");
    let safePersonalContext = this._coerceCliTextArgOrFallback(this._singleLineCliTextValue(this.personalContext), "personal context", "");
    let safeVocabulary = this._coerceCliTextArgOrFallback(this._singleLineCliTextValue(this.vocabulary), "vocabulary", "");
    let safeRecorder = this._normalizeRecorder(this.recorder);
    let safeTranscriber = TRANSCRIBER_METHODS.indexOf(String(this.transcriber || "")) >= 0
      ? String(this.transcriber)
      : "auto";
    let safePostProcessBackend = POST_PROCESS_BACKENDS.indexOf(String(this.postProcessBackend || "")) >= 0
      ? String(this.postProcessBackend)
      : "none";
    if (safeTranscriber === "command" && safeTranscriberCommand.trim() === "") {
      safeTranscriber = "auto";
    }
    if ((safeTranscriber === "whisper-cpp" || safeTranscriber === "faster-whisper") && safeWhisperModel.trim() === "") {
      safeTranscriber = "auto";
    }
    if (safePostProcessBackend === "command" && safePostProcessCommand.trim() === "") {
      safePostProcessBackend = "none";
    }
    if (safePostProcessBackend === "ollama" && safeOllamaModel.trim() === "") {
      safePostProcessBackend = "none";
    }

    let args = [
      this._cliCommand(),
      command,
      "--json",
      "--language", String(this._currentLanguage()),
      "--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds)),
      "--recorder", safeRecorder,
      "--transcriber", safeTranscriber,
      "--post-process-backend", safePostProcessBackend,
      "--insert-method", "none",
      "--typing-delay-ms", String(this._normalizeTypingDelayMs(this.typingDelayMs))
    ];
    args.push("--keep-transcripts", String(this._normalizeTranscriptLimit(this.maxTranscriptFiles)));
    args.push("--artifact-encryption", this._normalizeArtifactEncryption(this.artifactEncryption));
    args.push("--confirm-plaintext-output");
    if (this.appendSpace) {
      args.push("--append-space");
    }
    if (this.sanitizeSpecialChars) {
      args.push("--sanitize-special-chars");
    }
    if (this.softenProfanity) {
      args.push("--soften-profanity");
    }
    if (this.keepRecordingArtifacts) {
      args.push("--keep-recording-artifacts");
    }
    if (this.autoRelisten) {
      args.push("--skip-silent-auto-relisten");
    }
    let transcriberCommandIncluded = safeTranscriberCommand.trim() === "";
    let postProcessCommandIncluded = safePostProcessCommand.trim() === "";
    let ollamaModelIncluded = safeOllamaModel.trim() === "";
    let whisperModelIncluded = safeWhisperModel.trim() === "";
    let ollamaUrlIncluded = safeOllamaUrl.trim() === "" || safeOllamaUrl.trim() === DEFAULT_OLLAMA_URL;
    let openAiCompatibleUrlIncluded = safeOpenAiCompatibleUrl.trim() === "" || safeOpenAiCompatibleUrl.trim() === DEFAULT_OPENAI_COMPATIBLE_URL;
    let openAiCompatibleModelIncluded = safeOpenAiCompatibleModel.trim() === "" || safeOpenAiCompatibleModel.trim() === DEFAULT_OPENAI_COMPATIBLE_MODEL;
    let openAiCompatibleTextModelIncluded = safeOpenAiCompatibleTextModel.trim() === "" || safeOpenAiCompatibleTextModel.trim() === DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL;
    if (safeInputDevice.trim() !== "") {
      this._appendCliOptionWithinBudget(args, "--input-device", safeInputDevice);
    }
    if (safeTranscriberCommand.trim() !== "") {
      transcriberCommandIncluded = this._appendCliOptionWithinBudget(args, "--transcriber-command", safeTranscriberCommand);
    }
    if (safePostProcessCommand.trim() !== "") {
      postProcessCommandIncluded = this._appendCliOptionWithinBudget(args, "--post-process-command", safePostProcessCommand);
    }
    if (safeOllamaUrl.trim() !== "" && safeOllamaUrl.trim() !== DEFAULT_OLLAMA_URL) {
      ollamaUrlIncluded = this._appendCliOptionWithinBudget(args, "--ollama-url", safeOllamaUrl);
    }
    if (safeOllamaModel.trim() !== "") {
      ollamaModelIncluded = this._appendCliOptionWithinBudget(args, "--ollama-model", safeOllamaModel);
    }
    if (safeOpenAiCompatibleUrl.trim() !== "" && safeOpenAiCompatibleUrl.trim() !== DEFAULT_OPENAI_COMPATIBLE_URL) {
      openAiCompatibleUrlIncluded = this._appendCliOptionWithinBudget(args, "--openai-compatible-url", safeOpenAiCompatibleUrl);
    }
    if (safeOpenAiCompatibleModel.trim() !== "") {
      if (safeOpenAiCompatibleModel.trim() === DEFAULT_OPENAI_COMPATIBLE_MODEL) {
        openAiCompatibleModelIncluded = true;
      } else {
        openAiCompatibleModelIncluded = this._appendCliOptionWithinBudget(args, "--openai-compatible-model", safeOpenAiCompatibleModel);
      }
    }
    if (safeOpenAiCompatibleTextModel.trim() !== "") {
      if (safeOpenAiCompatibleTextModel.trim() === DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL) {
        openAiCompatibleTextModelIncluded = true;
      } else {
        openAiCompatibleTextModelIncluded = this._appendCliOptionWithinBudget(args, "--openai-compatible-text-model", safeOpenAiCompatibleTextModel);
      }
    }
    if (!Boolean(this.openaiCompatibleFlexProcessing)) {
      args.push("--no-openai-compatible-flex-processing");
    }
    if (safePostProcessPrompt.trim() !== "") {
      this._appendCliOptionWithinBudget(args, "--post-process-prompt", safePostProcessPrompt);
    }
    if (safeWhisperModel.trim() !== "") {
      whisperModelIncluded = this._appendCliOptionWithinBudget(args, "--whisper-model", safeWhisperModel);
    }
    if (safePersonalContext.trim() !== "") {
      this._appendCliOptionWithinBudget(args, "--personal-context", safePersonalContext);
    }
    if (safeVocabulary.trim() !== "") {
      this._appendCliOptionWithinBudget(args, "--vocabulary", safeVocabulary);
    }
    if (!transcriberCommandIncluded && safeTranscriber === "command") {
      args[args.indexOf("--transcriber") + 1] = "auto";
    }
    if (!whisperModelIncluded && (safeTranscriber === "whisper-cpp" || safeTranscriber === "faster-whisper")) {
      args[args.indexOf("--transcriber") + 1] = "auto";
    }
    if (!postProcessCommandIncluded && safePostProcessBackend === "command") {
      args[args.indexOf("--post-process-backend") + 1] = "none";
    }
    if (!ollamaModelIncluded && safePostProcessBackend === "ollama") {
      args[args.indexOf("--post-process-backend") + 1] = "none";
    }
    if (!ollamaUrlIncluded && safePostProcessBackend === "ollama") {
      args[args.indexOf("--post-process-backend") + 1] = "none";
    }
    if (safeTranscriber === "openai-compatible" && (!openAiCompatibleUrlIncluded || !openAiCompatibleModelIncluded)) {
      args[args.indexOf("--transcriber") + 1] = "auto";
    }
    if (safePostProcessBackend === "openai-compatible" && (!openAiCompatibleUrlIncluded || !openAiCompatibleTextModelIncluded)) {
      args[args.indexOf("--post-process-backend") + 1] = "none";
    }
    return args;
  },

  _appendCliOptionWithinBudget: function(args, flag, value) {
    if (!Array.isArray(args) || typeof flag !== "string" || typeof value !== "string") {
      return false;
    }
    let flagBytes = utf8ByteLength(flag);
    let valueBytes = utf8ByteLength(value);
    if (flagBytes > MAX_CLI_ARG_BYTES || valueBytes > MAX_CLI_ARG_BYTES) {
      this._logLifecycleError("settings-value", new Error("optional CLI setting exceeds argument limit"));
      return false;
    }
    let totalBytes = 0;
    for (let arg of args) {
      totalBytes += utf8ByteLength(arg);
    }
    if (totalBytes + flagBytes + valueBytes > MAX_CLI_COMMAND_BYTES) {
      this._logLifecycleError("settings-value", new Error("optional CLI setting exceeds command limit"));
      return false;
    }
    args.push(flag, value);
    return true;
  },

  _statusArgs: function() {
    return [this._cliCommand(), "status", "--json"];
  },

  _doctorArgs: function() {
    return [this._cliCommand(), "doctor", "--applet", "--settings-json-stdin", "--json"];
  },

  _setupArgs: function() {
    return [this._cliCommand(), "setup", "--applet", "--settings-json-stdin", "--json"];
  },

  _diagnosticsArgs: function() {
    return [this._cliCommand(), "diagnostics", "--applet", "--settings-json-stdin", "--json"];
  },

  _diagnosticsSaveArgs: function() {
    return [this._cliCommand(), "diagnostics", "--applet", "--settings-json-stdin", "--save", "--json"];
  },

  _profanityFilterDocumentArgs: function() {
    return [this._cliCommand(), "profanity-filter-document", "--json"];
  },

  _benchmarkArgs: function(audioPath) {
    return [this._cliCommand(), "benchmark-models", String(audioPath || ""), "--language", String(this._currentLanguage()), "--json"];
  },

  _installTextModelArgs: function(model) {
    let safeOllamaUrl = this._coerceCliTextArg(this.ollamaUrl, "ollama URL");
    let safeModel = this._coerceCliTextArg(model, "ollama model");
    let args = [this._cliCommand(), "install-text-model", "--backend", "ollama", "--model", safeModel, "--json"];
    if (safeOllamaUrl.trim() !== "") {
      args.push("--ollama-url", safeOllamaUrl);
    }
    return args;
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

  _allHistoryArgs: function() {
    return [this._cliCommand(), "transcripts-document", "--limit", "1000", "--confirm-plaintext", "--json"];
  },

  _transcriptsExportArgs: function() {
    let mode = this._normalizeArtifactEncryption(this.artifactEncryption);
    if (mode === "off") {
      mode = "keyring";
    }
    return [this._cliCommand(), "transcripts-export", "--limit", "1000", "--artifact-encryption", mode, "--json"];
  },

  _cleanupArgs: function() {
    return [this._cliCommand(), "cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--json"];
  },

  _cleanupPreviewArgs: function() {
    return [this._cliCommand(), "cleanup", "--keep-transcripts", "0", "--keep-recordings", "0", "--dry-run", "--json"];
  },

  _listInputsArgs: function() {
    return [this._cliCommand(), "list-inputs", "--json"];
  },

  _modelsArgs: function() {
    return [this._cliCommand(), "models", "--json"];
  },

  _textModelsArgs: function(backendOverride) {
    let safeOllamaUrl = this._coerceCliTextArg(this.ollamaUrl, "ollama URL");
    let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this.openaiCompatibleUrl, "openai-compatible URL");

    let args = [this._cliCommand(), "text-models", "--json"];
    let backend = String(backendOverride || this.postProcessBackend || "");
    if (backend === "openai-compatible") {
      args.push("--backend", "openai-compatible");
      if (safeOpenAiCompatibleUrl.trim() !== "") {
        args.push("--openai-compatible-url", safeOpenAiCompatibleUrl);
      }
      return args;
    }
    args.push("--backend", "ollama");
    if (safeOllamaUrl.trim() !== "") {
      args.push("--ollama-url", safeOllamaUrl);
    }
    return args;
  },

  _tryTextModelsArgs: function(backendOverride) {
    try {
      return this._textModelsArgs(backendOverride);
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Could not prepare text model request: ") + safeError, this.lastTranscript);
      return null;
    }
  },

  _downloadModelArgs: function(model) {
    return [this._cliCommand(), "download-model", String(model || this._starterVoiceModelName()), "--json"];
  },

  _removeModelArgs: function(model) {
    return [this._cliCommand(), "remove-model", String(model || this._starterVoiceModelName()), "--json"];
  },

  _settingsExportArgs: function() {
    return [this._cliCommand(), "settings-export", "--settings-json-stdin", "--json"];
  },

  _settingsImportArgs: function() {
    return [this._cliCommand(), "settings-import", "--confirm-plaintext-settings-output", "--json"];
  },

  _cliCommand: function() {
    try {
      let configured = String(this.cliPath || "").trim();
      if (configured !== "") {
        if (configured.indexOf("~/") === 0) {
          configured = GLib.build_filenamev([GLib.get_home_dir(), configured.substring(2)]);
        }
        if (configured.charAt(0) === "/" && GLib.file_test(configured, GLib.FileTest.IS_EXECUTABLE)) {
          return configured;
        }
      }
      if (GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE)) {
        return DEFAULT_CLI;
      }
      if (GLib.file_test(SYSTEM_CLI, GLib.FileTest.IS_EXECUTABLE)) {
        return SYSTEM_CLI;
      }
      return "";
    } catch (err) {
      this._logLifecycleError("cli-command", err);
      return "";
    }
  },

  _outputMethodLabel: function(method) {
    if (method === "clipboard") return _("Clipboard only");
    if (method === "type") return _("Direct typing");
    if (method === "none") return _("Do not insert");
    return _("Clipboard and paste");
  },

  _normalizeOutputMethod: function(method) {
    let value = String(method || "").trim();
    return OUTPUT_METHODS.indexOf(value) >= 0 ? value : "none";
  },

  _normalizeArtifactEncryption: function(method) {
    let value = String(method || "").trim();
    return ARTIFACT_ENCRYPTION_MODES.indexOf(value) >= 0 ? value : DEFAULT_ARTIFACT_ENCRYPTION;
  },

  _artifactEncryptionLabel: function(method) {
    let mode = this._normalizeArtifactEncryption(method);
    if (mode === "passphrase") return _("Passphrase");
    if (mode === "off") return _("Off");
    return _("Secret Service keyring");
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
    this._clearMenuItems(this.recorderItem.menu);
    let current = this._normalizeRecorder(this.recorder);
    for (let method of RECORDER_METHODS) {
      let label = (current === method ? "[x] " : "[ ] ") + this._recorderLabel(method);
      let item = new PopupMenu.PopupMenuItem(label);
      this._connectSafe(item, "activate", () => this._selectRecorder(method));
      this.recorderItem.menu.addMenuItem(item);
    }
  },

  _selectRecorder: function(method) {
    let nextRecorder = this._normalizeRecorder(method);
    if (!this._commitSettingValue("recorder", "recorder", nextRecorder, "settings-recorder", _("Recorder setting could not be saved"))) {
      return;
    }
    this._populateRecorderMenu();
    let label = this._recorderLabel(this.recorder);
    if (this._hasActiveRecordingState()) {
      this._setStatusPreservingRecording(this.status, _("Recorder for next recording: ") + label, this.lastTranscript);
      return;
    }
    this._setStatus("ready", _("Recorder: ") + label, this.lastTranscript);
  },

  _normalizeRecordingLimit: function(seconds) {
    let value = typeof seconds === "number" && isFinite(seconds) ? Math.floor(seconds) : NaN;
    if (!isFinite(value)) {
      value = DEFAULT_RECORDING_SECONDS;
    }
    return Math.max(MIN_RECORDING_SECONDS, Math.min(MAX_RECORDING_SECONDS, value));
  },

  _normalizeTypingDelayMs: function(delay) {
    let value = typeof delay === "number" && isFinite(delay) ? Math.floor(delay) : NaN;
    if (!isFinite(value)) {
      value = DEFAULT_TYPING_DELAY_MS;
    }
    return Math.max(MIN_TYPING_DELAY_MS, Math.min(MAX_TYPING_DELAY_MS, value));
  },

  _normalizeTranscriptLimit: function(limit) {
    let value = typeof limit === "number" && isFinite(limit) ? Math.floor(limit) : NaN;
    if (!isFinite(value)) {
      value = DEFAULT_MAX_TRANSCRIPT_FILES;
    }
    return Math.max(MIN_TRANSCRIPT_FILES, Math.min(MAX_TRANSCRIPT_FILES, value));
  },

  _populateRecordingLimitMenu: function() {
    if (!this.recordingLimitItem) {
      return;
    }
    this._clearMenuItems(this.recordingLimitItem.menu);
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
      this._connectSafe(item, "activate", () => this._selectRecordingLimit(seconds));
      this.recordingLimitItem.menu.addMenuItem(item);
    }
    this.recordingLimitItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let custom = new PopupMenu.PopupIconMenuItem((hasPreset ? "[ ] " : "[x] ") + _("Custom seconds..."), "document-edit-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(custom, "activate", () => this._promptCustomRecordingLimit());
    this.recordingLimitItem.menu.addMenuItem(custom);
  },

  _selectRecordingLimit: function(seconds) {
    let nextSeconds = this._normalizeRecordingLimit(seconds);
    if (!this._commitSettingValue("maxSeconds", "max-seconds", nextSeconds, "settings-recording-limit", _("Recording duration setting could not be saved"))) {
      return;
    }
    this._populateRecordingLimitMenu();
    let label = this._formatSeconds(this.maxSeconds);
    if (this._hasActiveRecordingState()) {
      this._setStatusPreservingRecording(this.status, _("Duration for next recording: ") + label, this.lastTranscript);
      return;
    }
    this._setStatus("ready", _("Duration: ") + label, this.lastTranscript);
  },

  _customRecordingLimitPromptArgs: function() {
    let current = String(this._normalizeRecordingLimit(this.maxSeconds));
    return [
      "zenity",
      "--entry",
      "--title=Duration",
      "--text=Enter maximum recording length in seconds (0 disables the limit).",
      "--entry-text=" + current
    ];
  },

  _promptCustomRecordingLimit: function() {
    if (this.customLimitPromptToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      this.lastMessage = _("Install zenity to enter a custom duration.");
      this._setStatusPreservingRecording("ready", this.lastMessage, this.lastTranscript);
      return;
    }
    let promptToken = {};
    this.customLimitPromptToken = promptToken;
    let recordingPromptArgs;
    try {
      recordingPromptArgs = this._customRecordingLimitPromptArgs();
    } catch (error) {
      if (this.customLimitPromptToken === promptToken) {
        this.customLimitPromptToken = null;
      }
      this._recordLifecycleError("recording-limit-prompt", error);
      this._setStatusPreservingRecording("error", _("Could not prepare custom duration prompt"), this.lastTranscript);
      return;
    }
    this._spawnText(recordingPromptArgs, (output) => {
      if (this.customLimitPromptToken !== promptToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.customLimitPromptToken = null;
      let seconds = this._parseCustomRecordingLimit(output);
      if (seconds === null) {
        return;
      }
      this._selectRecordingLimit(seconds);
    });
  },

  _parseCustomRecordingLimit: function(value) {
    let text = String(value === undefined || value === null ? "" : value).trim();
    if (text === "") {
      return null;
    }
    if (!/^[0-9]+$/.test(text)) {
      this.lastMessage = _("Duration must be whole seconds.");
      this._setStatusPreservingRecording("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    let seconds = Math.floor(Number(text));
    if (!isFinite(seconds) || seconds < MIN_RECORDING_SECONDS || seconds > MAX_RECORDING_SECONDS) {
      this.lastMessage = _("Duration must be between 0 and 3600 seconds.");
      this._setStatusPreservingRecording("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    return seconds;
  },

  _transcriptStorageLabel: function() {
    return _("Store transcripts: ") + String(this._normalizeTranscriptLimit(this.maxTranscriptFiles));
  },

  _updateTranscriptStorageItem: function() {
    if (this.transcriptStorageItem) {
      this.transcriptStorageItem.label.text = this._transcriptStorageLabel();
    }
  },

  _populateTranscriptStorageMenu: function() {
    if (!this.transcriptStorageItem) {
      return;
    }
    this._clearMenuItems(this.transcriptStorageItem.menu);
    let current = this._normalizeTranscriptLimit(this.maxTranscriptFiles);
    let hasPreset = TRANSCRIPT_STORAGE_LIMITS.indexOf(current) >= 0;
    if (!hasPreset) {
      let currentItem = new PopupMenu.PopupMenuItem("[x] " + _("Keep a maximum of ") + String(current) + _(" transcript files"));
      currentItem.setSensitive(false);
      this.transcriptStorageItem.menu.addMenuItem(currentItem);
      this.transcriptStorageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    }
    for (let limit of TRANSCRIPT_STORAGE_LIMITS) {
      let label = (current === limit ? "[x] " : "[ ] ") + _("Keep a maximum of ") + String(limit) + _(" transcript files");
      let item = new PopupMenu.PopupMenuItem(label);
      this._connectSafe(item, "activate", () => this._selectTranscriptStorageLimit(limit));
      this.transcriptStorageItem.menu.addMenuItem(item);
    }
    this.transcriptStorageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let custom = new PopupMenu.PopupIconMenuItem((hasPreset ? "[ ] " : "[x] ") + _("Custom transcript limit..."), "document-edit-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(custom, "activate", () => this._promptCustomTranscriptLimit());
    this.transcriptStorageItem.menu.addMenuItem(custom);
    this._updateTranscriptStorageItem();
  },

  _selectTranscriptStorageLimit: function(limit) {
    let nextLimit = this._normalizeTranscriptLimit(limit);
    if (!this._commitSettingValue("maxTranscriptFiles", "max-transcript-files", nextLimit, "settings-transcript-limit", _("Transcript storage setting could not be saved"))) {
      return;
    }
    this._populateTranscriptStorageMenu();
    this._setStatusPreservingRecording("ready", _("Keep a maximum of ") + String(this.maxTranscriptFiles) + _(" transcript files"), this.lastTranscript);
  },

  _customTranscriptLimitPromptArgs: function() {
    let current = String(this._normalizeTranscriptLimit(this.maxTranscriptFiles));
    return [
      "zenity",
      "--entry",
      "--title=Store transcripts",
      "--text=Keep a maximum of this many transcript files (1-1000).",
      "--entry-text=" + current
    ];
  },

  _promptCustomTranscriptLimit: function() {
    if (this.customLimitPromptToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      this.lastMessage = _("Install zenity to enter a custom transcript limit.");
      this._setStatusPreservingRecording("ready", this.lastMessage, this.lastTranscript);
      return;
    }
    let promptToken = {};
    this.customLimitPromptToken = promptToken;
    let transcriptPromptArgs;
    try {
      transcriptPromptArgs = this._customTranscriptLimitPromptArgs();
    } catch (error) {
      if (this.customLimitPromptToken === promptToken) {
        this.customLimitPromptToken = null;
      }
      this._recordLifecycleError("transcript-limit-prompt", error);
      this._setStatusPreservingRecording("error", _("Could not prepare custom transcript limit prompt"), this.lastTranscript);
      return;
    }
    this._spawnText(transcriptPromptArgs, (output) => {
      if (this.customLimitPromptToken !== promptToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.customLimitPromptToken = null;
      let limit = this._parseCustomTranscriptLimit(output);
      if (limit === null) {
        return;
      }
      this._selectTranscriptStorageLimit(limit);
    });
  },

  _parseCustomTranscriptLimit: function(value) {
    let text = String(value === undefined || value === null ? "" : value).trim();
    if (text === "") {
      return null;
    }
    if (!/^[0-9]+$/.test(text)) {
      this.lastMessage = _("Transcript limit must be a whole number.");
      this._setStatusPreservingRecording("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    let limit = Math.floor(Number(text));
    if (!isFinite(limit) || limit < MIN_TRANSCRIPT_FILES || limit > MAX_TRANSCRIPT_FILES) {
      this.lastMessage = _("Transcript limit must be between 1 and 1000.");
      this._setStatusPreservingRecording("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    return limit;
  },

  _populateRecordingOptionsMenu: function() {
    if (!this.recordingOptionsItem) {
      return;
    }
    this._clearMenuItems(this.recordingOptionsItem.menu);

    let autoTranscribe = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.autoTranscribeTimeout), _("Auto-transcribe at time limit")));
    this._connectSafe(autoTranscribe, "activate", () => this._toggleAutoTranscribeTimeout());
    this.recordingOptionsItem.menu.addMenuItem(autoTranscribe);

    let autoRelisten = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.autoRelisten), _("Auto Relisten")));
    this._connectSafe(autoRelisten, "activate", () => this._toggleAutoRelisten());
    this.recordingOptionsItem.menu.addMenuItem(autoRelisten);

    let keepArtifacts = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.keepRecordingArtifacts), _("Keep recording files")));
    this._connectSafe(keepArtifacts, "activate", () => this._toggleKeepRecordingArtifacts());
    this.recordingOptionsItem.menu.addMenuItem(keepArtifacts);
  },

  _setRecordingOptionStatus: function(message) {
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _toggleAutoTranscribeTimeout: function() {
    let nextValue = !Boolean(this.autoTranscribeTimeout);
    if (!this._commitSettingValue("autoTranscribeTimeout", "auto-transcribe-timeout", nextValue, "settings-recording-options", _("Recording option could not be saved"))) {
      return;
    }
    this._populateRecordingOptionsMenu();
    this._setRecordingOptionStatus(
      this.autoTranscribeTimeout ? _("Auto-transcribe at time limit enabled") : _("Auto-transcribe at time limit disabled")
    );
  },

  _toggleAutoRelisten: function() {
    let nextValue = !Boolean(this.autoRelisten);
    if (!this._commitSettingValue("autoRelisten", "auto-relisten", nextValue, "settings-recording-options", _("Recording option could not be saved"))) {
      return;
    }
    this._populateRecordingOptionsMenu();
    this._setRecordingOptionStatus(
      this.autoRelisten ? _("Auto Relisten enabled") : _("Auto Relisten disabled")
    );
  },

  _toggleKeepRecordingArtifacts: function() {
    let nextValue = !Boolean(this.keepRecordingArtifacts);
    if (!this._commitSettingValue("keepRecordingArtifacts", "keep-recording-artifacts", nextValue, "settings-recording-options", _("Recording option could not be saved"))) {
      return;
    }
    this._populateRecordingOptionsMenu();
    this._setRecordingOptionStatus(
      this.keepRecordingArtifacts ? _("Recording files will be kept") : _("Recording files will be discarded")
    );
  },

  _populateNotificationOptionsMenu: function() {
    if (!this.notificationOptionsItem) {
      return;
    }
    this._clearMenuItems(this.notificationOptionsItem.menu);

    let recording = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.notifyRecording), _("Recording start and limit")));
    this._connectSafe(recording, "activate", () => this._toggleNotifyRecording());
    this.notificationOptionsItem.menu.addMenuItem(recording);

    let complete = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.notifyComplete), _("Dictation complete")));
    this._connectSafe(complete, "activate", () => this._toggleNotifyComplete());
    this.notificationOptionsItem.menu.addMenuItem(complete);

    let errors = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.notifyError), _("Dictation errors")));
    this._connectSafe(errors, "activate", () => this._toggleNotifyError());
    this.notificationOptionsItem.menu.addMenuItem(errors);
  },

  _setNotificationOptionStatus: function(message) {
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _toggleNotifyRecording: function() {
    let nextValue = !Boolean(this.notifyRecording);
    if (!this._commitSettingValue("notifyRecording", "notify-recording", nextValue, "settings-notifications", _("Notification option could not be saved"))) {
      return;
    }
    this._populateNotificationOptionsMenu();
    this._setNotificationOptionStatus(
      this.notifyRecording ? _("Recording notifications enabled") : _("Recording notifications disabled")
    );
  },

  _toggleNotifyComplete: function() {
    let nextValue = !Boolean(this.notifyComplete);
    if (!this._commitSettingValue("notifyComplete", "notify-complete", nextValue, "settings-notifications", _("Notification option could not be saved"))) {
      return;
    }
    this._populateNotificationOptionsMenu();
    this._setNotificationOptionStatus(
      this.notifyComplete ? _("Completion notifications enabled") : _("Completion notifications disabled")
    );
  },

  _toggleNotifyError: function() {
    let nextValue = !Boolean(this.notifyError);
    if (!this._commitSettingValue("notifyError", "notify-error", nextValue, "settings-notifications", _("Notification option could not be saved"))) {
      return;
    }
    this._populateNotificationOptionsMenu();
    this._setNotificationOptionStatus(
      this.notifyError ? _("Error notifications enabled") : _("Error notifications disabled")
    );
  },

  _populateOutputMethodMenu: function() {
    if (!this.outputMethodItem) {
      return;
    }
    this._clearMenuItems(this.outputMethodItem.menu);
    let current = this._normalizeOutputMethod(this.insertMethod);
    for (let method of OUTPUT_METHODS) {
      let label = (current === method ? "[x] " : "[ ] ") + this._outputMethodLabel(method);
      let item = new PopupMenu.PopupMenuItem(label);
      this._connectSafe(item, "activate", () => this._selectOutputMethod(method));
      this.outputMethodItem.menu.addMenuItem(item);
    }
  },

  _populateArtifactEncryptionMenu: function() {
    if (!this.artifactEncryptionItem) {
      return;
    }
    this.artifactEncryption = this._normalizeArtifactEncryption(this.artifactEncryption);
    this.artifactEncryptionItem.label.text = _("Encryption: ") + this._artifactEncryptionLabel(this.artifactEncryption);
    this._clearMenuItems(this.artifactEncryptionItem.menu);
    for (let mode of ARTIFACT_ENCRYPTION_MODES) {
      let label = (this.artifactEncryption === mode ? "[x] " : "[ ] ") + this._artifactEncryptionLabel(mode);
      let item = this._selectionMenuItem(label);
      this._connectSafe(item, "activate", () => this._selectArtifactEncryptionMode(mode));
      this.artifactEncryptionItem.menu.addMenuItem(item);
    }
  },

  _selectArtifactEncryptionMode: function(mode) {
    let nextMode = this._normalizeArtifactEncryption(mode);
    if (!this._commitSettingValue("artifactEncryption", "artifact-encryption", nextMode, "settings-encryption", _("Encryption setting could not be saved"))) {
      return;
    }
    this._populateArtifactEncryptionMenu();
    let message = _("Encryption: ") + this._artifactEncryptionLabel(this.artifactEncryption);
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _selectOutputMethod: function(method) {
    let nextMethod = this._normalizeOutputMethod(method);
    if (!this._commitSettingValue("insertMethod", "insert-method", nextMethod, "settings-output", _("Output setting could not be saved"))) {
      return;
    }
    this._populateOutputMethodMenu();
    let message = _("Output: ") + this._outputMethodLabel(this.insertMethod);
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _optionLabel: function(enabled, label) {
    return (enabled ? "[x] " : "[ ] ") + label;
  },

  _populateTextOptionsMenu: function() {
    if (!this.textOptionsItem) {
      return;
    }
    this._clearMenuItems(this.textOptionsItem.menu);
    let append = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.appendSpace), _("Append trailing space")));
    this._connectSafe(append, "activate", () => this._toggleAppendSpace());
    this.textOptionsItem.menu.addMenuItem(append);

    let sanitize = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.sanitizeSpecialChars), _("Replace accents before output")));
    this._connectSafe(sanitize, "activate", () => this._toggleSanitizeSpecialChars());
    this.textOptionsItem.menu.addMenuItem(sanitize);

    let soften = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.softenProfanity), _("Replace profanity with harmless words")));
    this._connectSafe(soften, "activate", () => this._toggleSoftenProfanity());
    this.textOptionsItem.menu.addMenuItem(soften);
  },

  _setTextOptionStatus: function(message) {
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _toggleAppendSpace: function() {
    let nextValue = !Boolean(this.appendSpace);
    if (!this._commitSettingValue("appendSpace", "append-space", nextValue, "settings-text-options", _("Text option could not be saved"))) {
      return;
    }
    this._populateTextOptionsMenu();
    this._setTextOptionStatus(this.appendSpace ? _("Append trailing space enabled") : _("Append trailing space disabled"));
  },

  _toggleSanitizeSpecialChars: function() {
    let nextValue = !Boolean(this.sanitizeSpecialChars);
    if (!this._commitSettingValue("sanitizeSpecialChars", "sanitize-special-chars", nextValue, "settings-text-options", _("Text option could not be saved"))) {
      return;
    }
    this._populateTextOptionsMenu();
    this._setTextOptionStatus(
      this.sanitizeSpecialChars ? _("Accent replacement enabled") : _("Accent replacement disabled")
    );
  },

  _toggleSoftenProfanity: function() {
    let nextValue = !Boolean(this.softenProfanity);
    if (!this._commitSettingValue("softenProfanity", "soften-profanity", nextValue, "settings-text-options", _("Text option could not be saved"))) {
      return;
    }
    this._populateTextOptionsMenu();
    this._setTextOptionStatus(
      this.softenProfanity ? _("Profanity replacement enabled") : _("Profanity replacement disabled")
    );
  },

  _autoPasteTitleValues: function(value) {
    let raw = typeof value === "string" ? value.replace(NUL_RE, "").slice(0, MAX_SETTING_TEXT_CHARS) : "";
    let values = [];
    let seen = Object.create(null);
    for (let item of raw.split(/[,\n\r]+/)) {
      let title = typeof item === "string" ? item.trim() : "";
      let key = title.toLowerCase();
      if (title === "" || seen[key]) {
        continue;
      }
      seen[key] = true;
      values.push(title);
    }
    return values;
  },

  _normalizeAutoPasteTitle: function(value) {
    return this._autoPasteTitleValues(value).join(", ");
  },

  _normalizedAutoPasteWindowTitle: function(value) {
    return (typeof value === "string" ? value : "").replace(NUL_RE, "").trim().toLowerCase();
  },

  _autoPastePromptArgs: function() {
    let current = this._normalizeAutoPasteTitle(this.autoPasteWindowTitle) || DEFAULT_AUTO_PASTE_TITLE;
    return [
      "zenity",
      "--entry",
      "--title=Auto-Submit",
      "--text=Built-in marker names match known window classes/app IDs; codex matches known terminal identities and the window title. Custom strings match the full window title case-insensitively. Empty disables Auto-Submit.",
      "--entry-text=" + current
    ];
  },

  _autoPasteEnabled: function() {
    return this._autoPasteTitleValues(this.autoPasteWindowTitle).length > 0;
  },

  _autoPasteLabel: function() {
    let titles = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    if (titles.length === 0) {
      return _("Auto-Submit: off");
    }
    return _("Auto-Submit: ") + this._shortMenuText(titles.join(", "), 48);
  },

  _configureAutoPaste: function() {
    if (this.autoPastePromptToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      this._setTextOptionStatus(_("Install zenity to enter a custom Auto-Submit string"));
      return;
    }
    let promptToken = {};
    this.autoPastePromptToken = promptToken;
    this._setTextOptionStatus(_("Enter custom Auto-Submit window title text..."));
    let promptArgs;
    try {
      promptArgs = this._autoPastePromptArgs();
    } catch (error) {
      if (this.autoPastePromptToken === promptToken) {
        this.autoPastePromptToken = null;
      }
      this._recordLifecycleError("auto-paste-prompt", error);
      this._setTextOptionStatus(_("Could not prepare Auto-Submit prompt"));
      return;
    }
    this._spawnText(promptArgs, (output) => {
      if (this.autoPastePromptToken !== promptToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.autoPastePromptToken = null;
      this._setAutoPasteTitles(this._autoPasteTitleValues(output));
    }, { timeoutMs: 0 });
  },

  _setAutoPasteTitles: function(values) {
    let nextTitle = this._normalizeAutoPasteTitle((values || []).join(", "));
    if (!this._commitSettingValue("autoPasteWindowTitle", "auto-paste-window-title", nextTitle, "settings-auto-paste", _("Auto-Submit setting could not be saved"))) {
      return;
    }
    this._populateAutoPasteMenu();
    let message = this._autoPasteEnabled()
      ? _("Auto-Submit targets: ") + this.autoPasteWindowTitle
      : _("Auto-Submit disabled");
    this._setTextOptionStatus(message);
  },

  _toggleAutoPasteTitle: function(value) {
    let title = String(value || "").trim();
    if (title === "") {
      return;
    }
    let values = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    let lower = title.toLowerCase();
    let next = [];
    let removed = false;
    for (let item of values) {
      if (item.toLowerCase() === lower) {
        removed = true;
        continue;
      }
      next.push(item);
    }
    if (!removed) {
      next.push(title);
    }
    this._setAutoPasteTitles(next);
  },

  _populateAutoPasteMenu: function() {
    if (!this.autoPasteItem) {
      return;
    }
    this._clearMenuItems(this.autoPasteItem.menu);
    let currentValues = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    let current = {};
    for (let value of currentValues) {
      current[value.toLowerCase()] = true;
    }
    for (let preset of AUTO_PASTE_TITLE_PRESETS) {
      let label = (current[String(preset).toLowerCase()] ? "[x] " : "[ ] ") + String(preset);
      let item = new PopupMenu.PopupMenuItem(label);
      this._connectSafe(item, "activate", () => this._toggleAutoPasteTitle(preset));
      this.autoPasteItem.menu.addMenuItem(item);
    }
    this.autoPasteItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let disabled = new PopupMenu.PopupMenuItem((currentValues.length === 0 ? "[x] " : "[ ] ") + _("Disabled"));
    this._connectSafe(disabled, "activate", () => this._setAutoPasteTitles([]));
    this.autoPasteItem.menu.addMenuItem(disabled);
    this.autoPasteItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let custom = new PopupMenu.PopupIconMenuItem(_("Custom string..."), "document-edit-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(custom, "activate", () => this._configureAutoPaste());
    this.autoPasteItem.menu.addMenuItem(custom);
  },

  _updateAutoPasteItem: function() {
    if (this.autoPasteItem) {
      this.autoPasteItem.label.text = this._autoPasteLabel();
    }
  },

  _windowTitleMatchesAutoPaste: function() {
    let markers = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    if (markers.length === 0) {
      return false;
    }
    if (!this._isUsableTargetWindow(this.targetWindow) && !this.targetWindowXTitle && !this.targetWindowXClass) {
      return false;
    }
    let title = this._normalizedAutoPasteWindowTitle(this._windowProbeValue(this.targetWindow, "get_title") || this.targetWindowXTitle || "");
    for (let marker of markers) {
      let key = String(marker || "").trim().toLowerCase();
      if (!key) {
        continue;
      }
      if (AUTO_PASTE_IDENTITY_MARKERS[key]) {
        if (key === "codex") {
          if (title.indexOf(key) >= 0 || this._windowIdentityMatchesAutoPaste(marker)) {
            return true;
          }
          continue;
        }
        if (this._windowIdentityMatchesAutoPaste(marker)) {
          return true;
        }
        continue;
      }
      if (title === key) {
        return true;
      }
    }
    return false;
  },

  _updateOpenAiFlexProcessingItem: function() {
    if (!this.openAiFlexProcessingItem) {
      return;
    }
    this.openAiFlexProcessingItem.label.text = this._optionLabel(
      Boolean(this.openaiCompatibleFlexProcessing),
      _("OpenAI Flex processing")
    );
  },

  _toggleOpenAiFlexProcessing: function() {
    let nextValue = !Boolean(this.openaiCompatibleFlexProcessing);
    if (!this._commitSettingValue("openaiCompatibleFlexProcessing", "openai-compatible-flex-processing", nextValue, "settings-openai-flex", _("OpenAI Flex setting could not be saved"))) {
      return;
    }
    this._updateOpenAiFlexProcessingItem();
    let message = this.openaiCompatibleFlexProcessing
      ? _("OpenAI Flex processing enabled")
      : _("OpenAI Flex processing disabled");
    if (this._hasActiveRecordingState()) {
      this._setStatusPreservingRecording(this.status, message, this.lastTranscript);
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
  },

  _normalizeLanguage: function(value, fallback) {
    let language = typeof value === "string" ? value.trim().toLowerCase() : "";
    let safeFallback = typeof fallback === "string" ? fallback.trim().toLowerCase() : "";
    if (LANGUAGE_CODES.indexOf(safeFallback) < 0) {
      safeFallback = "en";
    }
    return LANGUAGE_CODES.indexOf(language) >= 0 ? language : safeFallback;
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
    if (!this.activeLanguageExplicit || (current !== primary && current !== secondary)) {
      this.activeLanguage = primary;
    }
  },

  _onLanguageSettingsChanged: function() {
    this.activeLanguageExplicit = false;
    this._syncActiveLanguage();
    this.modelMenuRefreshToken = null;
    this.voiceModelActionToken = null;
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this._populateLanguageMenu();
    this._populateModelMenu([], _("Open menu to load voice models"));
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
    this.activeLanguageExplicit = true;
    this._setStatus("ready", message || _("Language: ") + this._currentLanguage(), this.lastTranscript);
    return true;
  },

  _switchLanguage: function() {
    let primary = this._primaryLanguage();
    let secondary = this._secondaryLanguage();
    let nextLanguage = this._currentLanguage() === primary ? secondary : primary;
    this._setActiveLanguage(nextLanguage, _("Language: ") + nextLanguage);
  },

  _startWithLanguage: function(language, preserveTargetOnFailure) {
    if (!this._hasActiveRecordingState()) {
      this._rememberFocusedWindow(Boolean(preserveTargetOnFailure));
      this.activeLanguage = this._normalizeLanguage(language, this._primaryLanguage());
      this.activeLanguageExplicit = true;
      this._updatePanel();
    }
    this._toggleRecording();
  },

  _populateLanguageMenu: function() {
    if (!this.languageItem) {
      return;
    }
    this._clearMenuItems(this.languageItem.menu);
    let primary = this._primaryLanguage();
    let secondary = this._secondaryLanguage();
    let current = this._currentLanguage();

    let selectPrimary = new PopupMenu.PopupMenuItem((current === primary ? "[x] " : "[ ] ") + _("Use primary: ") + primary);
    this._connectSafe(selectPrimary, "activate", () => this._setActiveLanguage(primary, _("Language: ") + primary));
    this.languageItem.menu.addMenuItem(selectPrimary);

    let selectSecondary = new PopupMenu.PopupMenuItem((current === secondary ? "[x] " : "[ ] ") + _("Use secondary: ") + secondary);
    this._connectSafe(selectSecondary, "activate", () => this._setActiveLanguage(secondary, _("Language: ") + secondary));
    this.languageItem.menu.addMenuItem(selectSecondary);

    this.languageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let startPrimary = new PopupMenu.PopupIconMenuItem(_("Start primary: ") + primary, "media-record-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(startPrimary, "activate", () => this._startWithLanguage(primary, true));
    this.languageItem.menu.addMenuItem(startPrimary);

    let startSecondary = new PopupMenu.PopupIconMenuItem(_("Start secondary: ") + secondary, "media-record-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(startSecondary, "activate", () => this._startWithLanguage(secondary, true));
    this.languageItem.menu.addMenuItem(startSecondary);

    this.languageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let switchItem = new PopupMenu.PopupIconMenuItem(_("Switch primary/secondary"), "preferences-desktop-locale-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(switchItem, "activate", () => this._switchLanguage());
    this.languageItem.menu.addMenuItem(switchItem);
  },

  _formatKeybinding: function(binding) {
    let value = typeof binding === "string" ? binding.trim() : "";
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
      [_("Cancel recording"), this._formatKeybinding(this.cancelKeybinding)],
      [_("Switch language"), _("Applet menu only")]
    ];
  },

  _populateShortcutMenu: function() {
    if (!this.shortcutItem) {
      return;
    }
    this._clearMenuItems(this.shortcutItem.menu);
    for (let row of this._shortcutRows()) {
      let item = new PopupMenu.PopupMenuItem(row[0] + ": " + row[1]);
      item.setSensitive(false);
      this.shortcutItem.menu.addMenuItem(item);
    }
    this.shortcutItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let configure = new PopupMenu.PopupIconMenuItem(_("Configure shortcuts"), "preferences-desktop-keyboard-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(configure, "activate", () => this._openShortcutSettings());
    this.shortcutItem.menu.addMenuItem(configure);
    let copy = new PopupMenu.PopupIconMenuItem(_("Copy shortcut reference"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(copy, "activate", () => this._copyShortcutReference());
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
    if (!this._setClipboardText(this._shortcutReferenceText())) {
      this._setStatusPreservingRecording("error", _("Could not copy shortcut reference"), this.lastTranscript);
      return;
    }
    this._setStatusPreservingRecording("done", _("Copied shortcut reference"), this.lastTranscript);
  },

  _openShortcutSettings: function() {
    this._openAppletSettings(_("Opened Cinnamon shortcut settings"));
  },

  _toggleRecording: function() {
    if (this.ollamaModelFlowToken || this.ollamaInstallWatchToken) {
      this._cancelOllamaFlowForRecording();
    }
    if (this.terminalWorkflowRunning || this.terminalWorkflowToken) {
      this.terminalWorkflowToken = null;
    }
    this._invalidateBackgroundCallbacksForRecording();
    if (this.textInsertToken) {
      this._cancelTextInsertForSettingsChange();
    }
    if (this.isCommandRunning) {
      if (this.autoRelisten && this.notificationSessionActive) {
        this.autoRelistenManualStopRequested = true;
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this._setStatus("processing", _("Stopping Auto Relisten..."), this.lastTranscript);
      }
      return;
    }
    let hasExistingRecordingWork = this._hasActiveRecordingState();
    if (!hasExistingRecordingWork && !this._ensureVoiceModelCompatibleWithCurrentLanguage(true)) {
      return;
    }
    let toggleArgs;
    try {
      toggleArgs = this._baseArgs("toggle");
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Could not prepare recording command: ") + safeError, this.lastTranscript);
      return;
    }
    let manualRelistenStopRequested = Boolean(
      this.autoRelisten &&
      this.notificationSessionActive &&
      (this.status === "recording" || this.status === "recorded" || this.autoRelistenPending)
    );
    this.notificationSessionActive = true;
    this.lastNotificationKey = "";
    this.autoTranscribeRecordingKey = "";
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenManualStopRequested = manualRelistenStopRequested;
    this.autoInsertFingerprint = "";
    this.autoInsertFingerprints = [];
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this.cancelPendingWhileCommandRunning = false;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Working..."), "");
    this._spawnJson(toggleArgs, (payload) => {
      this.isCommandRunning = false;
      this._applyPayloadSafely(payload);
    });
  },

  _restartApplet: function() {
    this._terminateProcessesByGroup("keyboard");
    this._setStatusPreservingRecording("processing", _("Restarting applet..."), this.lastTranscript);
    try {
      Extension.reloadExtension(UUID, Extension.Type.APPLET);
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("Could not restart applet"), this.lastTranscript);
    }
  },

  _refreshStatus: function() {
    if (this._statusCommandRunning) {
      return;
    }
    if (this.isCommandRunning) {
      return;
    }
    this._statusCommandRunning = true;
    let statusRefreshToken = ++this._statusRefreshToken;
    try {
      this._spawnJson(this._statusArgs(), (payload) => {
        let statusApplyFailed = false;
        try {
          this._applyPayload(payload, statusRefreshToken);
        } catch (err) {
          statusApplyFailed = true;
          let safeError = this._sanitizeErrorMessage(err);
          this._setStatusPreservingRecording("error", _("Status refresh failed: ") + safeError, this.lastTranscript);
        } finally {
          this._statusCommandRunning = false;
          if (statusApplyFailed && (this.status === "recording" || this.status === "processing")) {
            this._scheduleStatusPoll();
          }
        }
      }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS });
    } catch (err) {
      this._statusCommandRunning = false;
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Status refresh failed: ") + safeError, this.lastTranscript);
      if (this.status === "recording" || this.status === "processing") {
        this._scheduleStatusPoll();
      }
    }
  },

  _hasCancelableRecordingWork: function(statusOverride) {
    let effectiveStatus = typeof statusOverride === "string" ? statusOverride : this.status;
    return effectiveStatus === "recording" || effectiveStatus === "recorded" ||
      this.autoRelistenPending ||
      (this.isCommandRunning && this.notificationSessionActive);
  },

  _cancelRecording: function(statusOverride) {
    if (!this._hasCancelableRecordingWork(statusOverride)) {
      return;
    }
    if (!this.isCommandRunning && this.autoRelistenPending && this.textInsertToken) {
      this._cancelTextInsertForSettingsChange();
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatus("ready", _("Auto Relisten cancelled"), this.lastTranscript);
      return;
    }
    if (this.isCommandRunning) {
      this.autoTranscribeRecordingKey = "";
      this.cancelPendingWhileCommandRunning = true;
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatus("processing", _("Stopping Auto Relisten..."), this.lastTranscript);
      return;
    }
    let cancelArgs;
    try {
      cancelArgs = this._cancelArgs();
    } catch (error) {
      let safeError = this._sanitizeErrorMessage(error);
      this._setStatusPreservingRecording("error", _("Could not prepare cancellation command: ") + safeError, this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this.autoTranscribeRecordingKey = "";
    this.cancelPendingWhileCommandRunning = false;
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenManualStopRequested = true;
    this._setStatus("processing", _("Cancelling..."), this.lastTranscript);
    this._spawnJson(cancelArgs, (payload) => {
      this.isCommandRunning = false;
      this._applyPayloadSafely(payload);
    });
  },

  _invalidateBackgroundCallbacksForRecording: function() {
    this.historyRefreshToken = null;
    this.inputSourceMenuRefreshToken = null;
    this.modelMenuRefreshToken = null;
    this.textModelMenuRefreshToken = null;
    this.alarmMenuRefreshToken = null;
    this.alarmActionToken = null;
    this.alarmCheckToken = null;
    this.benchmarkFlowToken = null;
    this.settingsTransferToken = null;
    this.setupDiagnosticsToken = null;
    this.doctorCommandToken = null;
    this._doctorCommandRunning = false;
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    this.transcriptListPromptToken = null;
    this.transcriptWindowToken = null;
    this.settingsWindowToken = null;
  },

  _runDoctor: function(startupCheck) {
    if (!startupCheck && this._hasActiveRecordingState()) {
      this._setStatus(this.status, _("Finish the current recording before running doctor"), this.lastTranscript);
      return;
    }
    if (this._doctorCommandRunning) {
      if (!startupCheck) {
        this._setDoctorSummary(_("Doctor: already running"));
        this._setStatus(this._hasActiveRecordingState() ? this.status : "ready", _("Doctor: already running"), this.lastTranscript);
      }
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false, startupCheck ? "setup" : "error");
    if (!inputOption) {
      return;
    }
    let doctorToken = {};
    this.doctorCommandToken = doctorToken;
    this._doctorCommandRunning = true;
    if (!startupCheck) {
      this._setDoctorSummary(_("Doctor: checking..."));
      this._setStatus(this._hasActiveRecordingState() ? this.status : "processing", _("Doctor: checking..."), this.lastTranscript);
    }
    let doctorArgs;
    try {
      doctorArgs = this._doctorArgs();
    } catch (error) {
      if (this.doctorCommandToken === doctorToken) {
        this.doctorCommandToken = null;
      }
      this._doctorCommandRunning = false;
      this._recordLifecycleError("doctor", error);
      let message = startupCheck ? _("Doctor could not be prepared") : _("Doctor could not be prepared");
      this._setDoctorSummary(message);
      this._setStatus(startupCheck ? "setup" : "error", message, this.lastTranscript);
      return;
    }
    this._spawnJson(doctorArgs, (payload) => {
      if (this.doctorCommandToken !== doctorToken || !this._lifecycleAllowsWork()) {
        return;
      }
      try {
        if (payload.error) {
          let message = _("Doctor failed: ") + this._sanitizeErrorMessage(payload.error);
          this._setDoctorSummary(message);
          this._setStatus(startupCheck ? "setup" : "error", message, this.lastTranscript);
          this._presentDoctorResult(message, true, Boolean(startupCheck));
          return;
        }
        if (payload.configured) {
          this._applyDoctorPayload(payload, Boolean(startupCheck));
          return;
        }
        this._applyLegacyDoctorPayload(payload, Boolean(startupCheck));
      } catch (err) {
        let safeError = this._sanitizeErrorMessage(err);
        let message = _("Doctor failed: ") + safeError;
        this._setDoctorSummary(message);
        this._setStatus(startupCheck ? "setup" : "error", message, this.lastTranscript);
        this._presentDoctorResult(message, true, Boolean(startupCheck));
      } finally {
        if (this.doctorCommandToken === doctorToken) {
          this.doctorCommandToken = null;
          this._doctorCommandRunning = false;
        }
      }
    }, {
      inputText: inputOption.inputText,
      timeoutMs: DOCTOR_COMMAND_TIMEOUT_MS
    });
  },

  _applyDoctorPayload: function(payload, startupCheck) {
    let configured = payload.configured || {};
    let summary = this._doctorSummary(payload);
    this._setDoctorSummary(summary);
    let missing = [];
    for (let name of ["recorder", "transcriber", "output", "postprocessor"]) {
      let section = configured[name] && typeof configured[name] === "object" ? configured[name] : {};
      let detail = typeof section.detail === "string" ? section.detail.trim() : "";
      if (section.ok !== true) {
        missing.push(name + ": " + (detail || "not ready"));
      }
    }
    if (payload.ok !== true) {
      let message = _("Setup needed: ") + missing.join("; ");
      this._setStatus(startupCheck ? "setup" : "error", message, this.lastTranscript);
      this._presentDoctorResult(message, true, Boolean(startupCheck));
      return;
    }
    let warnings = Array.isArray(configured.warnings)
      ? configured.warnings.filter((warning) => typeof warning === "string" && warning.trim() !== "")
      : [];
    if (warnings.length > 0) {
      let message = summary + "; " + warnings.join("; ");
      this._setStatus("ready", message, this.lastTranscript);
      this._presentDoctorResult(message, false, Boolean(startupCheck));
      return;
    }
    this._setStatus("ready", summary, this.lastTranscript);
    this._presentDoctorResult(summary, false, Boolean(startupCheck));
  },

  _applyLegacyDoctorPayload: function(payload, startupCheck) {
    let missing = [];
    let checks = Array.isArray(payload.checks) ? payload.checks : [];
    for (let check of checks) {
      if (!check || typeof check !== "object") {
        continue;
      }
      if (check.ok !== true) {
        let name = typeof check.name === "string" ? check.name.trim() : "";
        if (name !== "") {
          missing.push(name);
        }
      }
    }
    if (payload.ok === true) {
      let message = _("Doctor: core OK; optional missing: ") + missing.join(", ");
      this._setDoctorSummary(message);
      this._setStatus("ready", message, this.lastTranscript);
      this._presentDoctorResult(message, false, Boolean(startupCheck));
    } else {
      let message = _("Missing: ") + missing.join(", ");
      this._setDoctorSummary(message);
      this._setStatus(startupCheck ? "setup" : "error", message, this.lastTranscript);
      this._presentDoctorResult(message, true, Boolean(startupCheck));
    }
  },

  _presentDoctorResult: function(message, critical, startupCheck) {
    if (startupCheck) {
      return;
    }
    this._notify(_("Speed of Cinnamon doctor"), String(message || _("Doctor finished")), Boolean(critical));
  },

  _setDoctorSummary: function(message) {
    try {
      this.doctorSummaryText = this._uiMessageText(String(message || ""));
      if (this.doctorSummaryItem) {
        this.doctorSummaryItem.label.text = this.doctorSummaryText || _("Doctor: not checked");
      }
    } catch (error) {
      this._recordLifecycleError("doctor-summary", error);
    }
  },

  _doctorSummary: function(payload) {
    let configured = payload.configured || {};
    let rows = [
      this._doctorSectionText("Rec", configured.recorder),
      this._doctorSectionText("ASR", configured.transcriber),
      this._doctorSectionText("Out", configured.output),
      this._doctorSectionText("Text", configured.postprocessor)
    ];
    return (payload.ok === true ? _("Doctor: ready - ") : _("Doctor: setup needed - ")) + rows.join(", ");
  },

  _doctorSectionText: function(label, section) {
    section = section || {};
    return label + " " + (section.ok === true ? "OK" : "FAIL");
  },

  _openAppletSettings: function() {
    let openedMessage = arguments.length > 0 ? String(arguments[0] || "") : _("Opened Cinnamon applet settings");
    let xletSettings = this._findTrustedProgramInPath("xlet-settings");
    if (!xletSettings) {
      this._setStatusPreservingRecording("error", _("xlet-settings command not found"), this.lastTranscript);
      return;
    }
    let args = [xletSettings, "applet", UUID];
    let instanceId = String(this.instanceId || "").trim();
    if (instanceId !== "") {
      args.push("--id", instanceId);
    }
    let settingsToken = {};
    this.settingsWindowToken = settingsToken;
    try {
      let handle = this._runBoundedSubprocess(this._coerceSpawnArgs(args), {}, {
        timeoutMs: 0,
        maxStdoutBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
      }, (stdout, stderr, result) => {
        if (this.settingsWindowToken !== settingsToken || !this._lifecycleAllowsWork()) {
          return;
        }
        this.settingsWindowToken = null;
        if (result && (result.error || result.timedOut || result.outputTooLarge)) {
          this._setStatusPreservingRecording("error", _("Cinnamon applet settings process exited unexpectedly"), this.lastTranscript);
        }
      });
      if (!handle) {
        throw new Error("xlet-settings process could not be started");
      }
    } catch (err) {
      if (this.settingsWindowToken !== settingsToken) {
        return;
      }
      this.settingsWindowToken = null;
      this._setStatusPreservingRecording("error", this._sanitizeErrorMessage(err), this.lastTranscript);
      return;
    }
    this._setStatusPreservingRecording("ready", openedMessage, this.lastTranscript);
  },

  _openSetupGuide: function() {
    this._openUri(RUNBOOK_URL, _("Opened setup guide"));
  },

  _openUri: function(uri, successMessage) {
    try {
      let opened = Gio.AppInfo.launch_default_for_uri(uri, null);
      if (opened === false) {
        throw new Error("URI could not be opened");
      }
      this._setStatusPreservingRecording("ready", successMessage, this.lastTranscript);
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("Could not open link"), this.lastTranscript);
    }
  },

  _openFolder: function(path, successMessage) {
    try {
      let mkdirResult = GLib.mkdir_with_parents(path, 0o755);
      if (mkdirResult !== 0) {
        throw new Error("folder could not be created");
      }
      if (!GLib.file_test(path, GLib.FileTest.IS_DIR)) {
        throw new Error("folder is not available: " + path);
      }
      this._openUri(GLib.filename_to_uri(path, null), successMessage);
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("Could not open folder"), this.lastTranscript);
    }
  },

  _openFile: function(path, successMessage) {
    try {
      if (!GLib.file_test(path, GLib.FileTest.EXISTS)) {
        throw new Error("file is not available: " + path);
      }
      this._openUri(GLib.filename_to_uri(path, null), successMessage);
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("Could not open file"), this.lastTranscript);
    }
  },

  _failSetupDiagnosticsAction: function(actionToken, error, message) {
    if (this.setupDiagnosticsToken !== actionToken) {
      return;
    }
    this.setupDiagnosticsToken = null;
    this._recordLifecycleError("setup-diagnostics", error);
    this._setStatus("error", message || _("Could not prepare setup diagnostics action"), this.lastTranscript);
  },

  _openProfanityFilterList: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    let actionToken = {};
    this.setupDiagnosticsToken = actionToken;
    this._setStatus("processing", _("Preparing profanity replacement list..."), this.lastTranscript);
    let documentArgs;
    try {
      documentArgs = this._profanityFilterDocumentArgs();
    } catch (error) {
      this._failSetupDiagnosticsAction(actionToken, error, _("Could not prepare profanity replacement list"));
      return;
    }
    this._spawnJson(documentArgs, (payload) => {
      if (this.setupDiagnosticsToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      let path = typeof payload.path === "string" ? payload.path.trim() : "";
      if (path === "") {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", _("Profanity replacement list was not generated"), this.lastTranscript);
        return;
      }
      this.setupDiagnosticsToken = null;
      this._openFile(path, _("Opened profanity replacement list: ") + String(this._safePayloadCount(payload.entries)));
    }, inputOption);
  },

  _copySetupPlan: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    let actionToken = {};
    this.setupDiagnosticsToken = actionToken;
    let setupArgs;
    try {
      setupArgs = this._setupArgs();
    } catch (error) {
      this._failSetupDiagnosticsAction(actionToken, error, _("Could not prepare setup plan"));
      return;
    }
    this._spawnJson(setupArgs, (payload) => {
      if (this.setupDiagnosticsToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      let planText = typeof payload.text === "string" && payload.text.trim() !== ""
        ? payload.text
        : JSON.stringify(payload, null, 2);
      if (!this._setClipboardText(planText)) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", _("Could not copy setup plan"), this.lastTranscript);
        return;
      }
      this.setupDiagnosticsToken = null;
      this._setStatus("done", _("Copied setup plan"), this.lastTranscript);
    }, inputOption);
  },

  _setupCommandsText: function(payload) {
    let commands = payload.commands || [];
    if (!Array.isArray(commands)) {
      return "";
    }

    let seen = Object.create(null);
    let lines = [];
    for (let i = 0; i < commands.length; i++) {
      let text = typeof commands[i] === "string" ? commands[i].trim() : "";
      if (text === "" || seen[text]) {
        continue;
      }
      seen[text] = true;
      lines.push(text);
    }
    return lines.join("\n");
  },

  _copySetupCommands: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    let actionToken = {};
    this.setupDiagnosticsToken = actionToken;
    let setupArgs;
    try {
      setupArgs = this._setupArgs();
    } catch (error) {
      this._failSetupDiagnosticsAction(actionToken, error, _("Could not prepare setup commands"));
      return;
    }
    this._spawnJson(setupArgs, (payload) => {
      if (this.setupDiagnosticsToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }

      let text = this._setupCommandsText(payload);
      if (text === "") {
        this.setupDiagnosticsToken = null;
        this._setStatus("ready", _("No setup commands needed"), this.lastTranscript);
        return;
      }

      if (!this._setClipboardText(text)) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", _("Could not copy setup commands"), this.lastTranscript);
        return;
      }
      this.setupDiagnosticsToken = null;
      this._setStatus("done", _("Copied setup commands"), this.lastTranscript);
    }, inputOption);
  },

  _copyDiagnostics: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(true);
    if (!inputOption) {
      return;
    }
    let actionToken = {};
    this.setupDiagnosticsToken = actionToken;
    let diagnosticsArgs;
    try {
      diagnosticsArgs = this._diagnosticsArgs();
    } catch (error) {
      this._failSetupDiagnosticsAction(actionToken, error, _("Could not prepare diagnostics"));
      return;
    }
    this._spawnJson(diagnosticsArgs, (payload) => {
      if (this.setupDiagnosticsToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      if (!this._setClipboardText(JSON.stringify(payload, null, 2))) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", _("Could not copy diagnostics"), this.lastTranscript);
        return;
      }
      this.setupDiagnosticsToken = null;
      this._setStatus("done", _("Copied diagnostics"), this.lastTranscript);
    }, inputOption);
  },

  _saveDiagnostics: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(true);
    if (!inputOption) {
      return;
    }
    let actionToken = {};
    this.setupDiagnosticsToken = actionToken;
    let diagnosticsSaveArgs;
    try {
      diagnosticsSaveArgs = this._diagnosticsSaveArgs();
    } catch (error) {
      this._failSetupDiagnosticsAction(actionToken, error, _("Could not prepare diagnostics save"));
      return;
    }
    this._spawnJson(diagnosticsSaveArgs, (payload) => {
      if (this.setupDiagnosticsToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.setupDiagnosticsToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      this.setupDiagnosticsToken = null;
      this._setStatus("done", _("Saved diagnostics"), this.lastTranscript);
    }, inputOption);
  },

  _benchmarkAudioFileDialogArgs: function() {
    return [
      "zenity",
      "--file-selection",
      "--title=Choose benchmark audio file",
      "--file-filter=Audio files | *.wav *.WAV *.flac *.FLAC *.mp3 *.MP3 *.ogg *.OGG *.oga *.OGA *.opus *.OPUS *.m4a *.M4A *.aac *.AAC *.webm *.WEBM"
    ];
  },

  _shellQuote: function(value) {
    return "'" + String(value || "").replace(/'/g, "'\"'\"'") + "'";
  },

  _terminalCommandQuote: function(value, fieldName) {
    let command = String(value || "").trim();
    if (command === "") {
      throw new Error((fieldName || "command") + " is empty");
    }
    if (!this._isAllowedCliCommand(command)) {
      throw new Error((fieldName || "command") + " is not executable");
    }
    return this._shellQuote(command);
  },

  _terminalCommandArgs: function(title, command) {
    let terminalTitle = String(title || "Speed of Cinnamon");
    if (this._findTrustedProgramInPath("gnome-terminal")) {
      return ["gnome-terminal", "--title=" + terminalTitle, "--", "bash", "-lc", command];
    }
    if (this._findTrustedProgramInPath("x-terminal-emulator")) {
      return ["x-terminal-emulator", "-e", "bash", "-lc", command];
    }
    if (this._findTrustedProgramInPath("xterm")) {
      return ["xterm", "-T", terminalTitle, "-e", "bash", "-lc", command];
    }
    return [];
  },

  _runTerminalWorkflow: function(title, command, openedMessage, cancelOllamaFlow, ollamaFlowToken) {
    if (this._hasActiveRecordingState()) {
      this._setStatus(this.status, _("Finish the current recording before starting a terminal workflow"), this.lastTranscript);
      return false;
    }
    if (this.terminalWorkflowRunning) {
      this._setStatus("error", _("Another terminal workflow is already running"), this.lastTranscript);
      return false;
    }
    if (this.isCommandRunning) {
      this._setStatus("error", _("Another command is already running"), this.lastTranscript);
      return false;
    }
    let terminalArgs = this._terminalCommandArgs(title, command);
    if (terminalArgs.length === 0) {
      this._setStatus("error", _("No supported terminal found"), this.lastTranscript);
      this._notify(_("No supported terminal found"), _("Install gnome-terminal, x-terminal-emulator, or xterm."), true);
      return false;
    }
    try {
      this.terminalWorkflowRunning = true;
      let terminalWorkflowToken = {};
      this.terminalWorkflowToken = terminalWorkflowToken;
      let handle = this._runBoundedSubprocess(this._coerceSpawnArgs(terminalArgs), {}, {
        timeoutMs: 0,
        maxStdoutBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
      }, (stdout, stderr, result) => {
        this.terminalWorkflowRunning = false;
        if (this.terminalWorkflowToken !== terminalWorkflowToken) {
          return;
        }
        this.terminalWorkflowToken = null;
        if (cancelOllamaFlow === true && (!ollamaFlowToken || this.ollamaModelFlowToken !== ollamaFlowToken)) {
          return;
        }
        if (result && (result.error || result.timedOut || result.outputTooLarge)) {
          if (cancelOllamaFlow === true && ollamaFlowToken && this.ollamaModelFlowToken === ollamaFlowToken) {
            this._cancelOllamaInstallWatch();
            this._clearOllamaModelFlow();
          }
          this._setStatus("error", _("Terminal process exited unexpectedly"), this.lastTranscript);
        } else if (cancelOllamaFlow !== true && this._lifecycleAllowsWork()) {
          this._setStatus("ready", _("Terminal workflow finished"), this.lastTranscript);
        }
      });
      if (!handle) {
        throw new Error("terminal process could not be started");
      }
      this._setStatus("processing", openedMessage, this.lastTranscript);
      return true;
    } catch (err) {
      this.terminalWorkflowRunning = false;
      this.terminalWorkflowToken = null;
      this._safeLogError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not open terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not open terminal"), safeError, true);
      return false;
    }
  },

  _terminalWorkflowScript: function(lines) {
    let script = [
      "set -eu",
      "rc=0",
      "trap 'rc=$?; printf \"\\nFinished with exit code %s.\\n\" \"$rc\"; read -r -p \"Press Enter to close...\"; exit \"$rc\"' EXIT"
    ].concat(lines || []);
    return script.join("\n");
  },

  _installOllamaRuntimeCommand: function() {
    return this._terminalWorkflowScript([
      "echo 'Installing Ollama runtime...'",
      "if command -v ollama >/dev/null 2>&1; then",
      "  echo 'Ollama is already installed.'",
      "  ollama --version || true",
      "else",
      "  printf 'Speed of Cinnamon does not run privileged package-manager commands from the applet.\\n' >&2",
      "  printf 'Install Ollama manually with your distribution package manager, then rerun this step.\\n' >&2",
      "  exit 1",
      "fi",
      "if command -v ollama >/dev/null 2>&1; then",
      "  ollama_log_file=\"$(mktemp \"${XDG_RUNTIME_DIR:-/tmp}/speed-of-cinnamon-ollama.XXXXXX.log\")\"",
      "  ollama serve >\"$ollama_log_file\" 2>&1 & sleep 2 || true",
      "fi",
      "if command -v ollama >/dev/null 2>&1; then ollama list >/dev/null 2>&1 && echo 'Ollama is reachable on 127.0.0.1:11434.' || { echo 'Ollama installed, but the local API is not reachable yet.'; exit 1; }; fi"
    ]);
  },

  _uninstallOllamaRuntimeCommand: function() {
    return this._terminalWorkflowScript([
      "echo 'Uninstalling Ollama runtime...'",
      "if command -v ollama >/dev/null 2>&1; then",
      "  printf 'Speed of Cinnamon does not run privileged uninstall commands from the applet.\\n' >&2",
      "  printf 'Remove Ollama manually with your distribution package manager or service manager.\\n' >&2",
      "  exit 1",
      "fi",
      "echo 'Ollama runtime removed.'"
    ]);
  },

  _basicSetupCommand: function() {
    let cli = this._terminalCommandQuote(this._cliCommand(), "CLI command");
    return this._terminalWorkflowScript([
      "echo 'Running Speed of Cinnamon basic setup...'",
      "echo 'Install OS packages manually if missing: zenity xdotool xclip xsel wl-clipboard pipewire-utils pulseaudio-utils alsa-utils python3-pip.'",
      "if command -v python3 >/dev/null 2>&1; then python3 -m pip install --user --upgrade faster-whisper; fi",
      cli + " download-model ct2-base-int8 --json",
      "echo 'Basic setup finished.'"
    ]);
  },

  _installOllamaRuntime: function(openChooserAfterInstall) {
    let continueOllamaFlow = openChooserAfterInstall === true && Boolean(this.ollamaModelFlowToken);
    let ollamaFlowToken = continueOllamaFlow ? this.ollamaModelFlowToken : null;
    if (!continueOllamaFlow) {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
    }
    let opened = false;
    try {
      opened = this._runTerminalWorkflow(_("Install Ollama"), this._installOllamaRuntimeCommand(), _("Ollama install terminal opened"), continueOllamaFlow, ollamaFlowToken);
    } catch (err) {
      this._safeLogError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not start install terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start install terminal"), safeError, true);
      if (continueOllamaFlow) {
        this._cancelOllamaInstallWatch();
        this._clearOllamaModelFlow();
      }
      return false;
    }
    if (opened && continueOllamaFlow) {
      this._watchOllamaInstallThenChoose();
    } else if (continueOllamaFlow) {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
    }
    return opened;
  },

  _uninstallOllamaRuntime: function() {
    this._cancelOllamaInstallWatch();
    this._clearOllamaModelFlow();
    try {
      this._runTerminalWorkflow(_("Uninstall Ollama"), this._uninstallOllamaRuntimeCommand(), _("Ollama uninstall terminal opened"));
    } catch (err) {
      this._safeLogError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not start uninstall terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start uninstall terminal"), safeError, true);
    }
  },

  _runBasicSetup: function() {
    this._cancelOllamaInstallWatch();
    this._clearOllamaModelFlow();
    try {
      this._runTerminalWorkflow(_("Speed of Cinnamon basic setup"), this._basicSetupCommand(), _("Basic setup terminal opened"));
    } catch (err) {
      this._safeLogError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not start setup terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start setup terminal"), safeError, true);
    }
  },

  _selectBenchmarkAudioFile: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this.benchmarkFlowToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      this._setStatus("error", _("Install zenity to choose a benchmark audio file"), this.lastTranscript);
      return;
    }
    let flowToken = {};
    this.benchmarkFlowToken = flowToken;
    this._setStatus("processing", _("Choose benchmark audio file..."), this.lastTranscript);
    let audioDialogArgs;
    try {
      audioDialogArgs = this._benchmarkAudioFileDialogArgs();
    } catch (error) {
      if (this.benchmarkFlowToken === flowToken) {
        this.benchmarkFlowToken = null;
      }
      this._recordLifecycleError("benchmark-flow", error);
      this._setStatus("error", _("Could not prepare benchmark audio selection"), this.lastTranscript);
      return;
    }
    this._spawnText(audioDialogArgs, (output) => {
      if (this.benchmarkFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      let audioPath = String(output || "").trim();
      if (audioPath === "") {
        this.benchmarkFlowToken = null;
        this._setStatus("ready", _("Benchmark cancelled"), this.lastTranscript);
        return;
      }
      this._benchmarkDownloadedModels(audioPath, flowToken);
    }, { timeoutMs: 0 });
  },

  _benchmarkDownloadedModels: function(audioPath, flowToken) {
    flowToken = flowToken || {};
    if (this.isCommandRunning || this._hasActiveRecordingState()) {
      this.benchmarkFlowToken = null;
      return;
    }
    let benchmarkArgs;
    try {
      benchmarkArgs = this._benchmarkArgs(audioPath);
    } catch (err) {
      this.benchmarkFlowToken = null;
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatus("error", _("Could not prepare benchmark command: ") + safeError, this.lastTranscript);
      return;
    }
    this.benchmarkFlowToken = flowToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Benchmarking downloaded models..."), this.lastTranscript);
    this._spawnJson(benchmarkArgs, (payload) => {
      this.isCommandRunning = false;
      if (this.benchmarkFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.benchmarkFlowToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      let results = Array.isArray(payload.results) ? payload.results : [];
      if (!this._setClipboardText(JSON.stringify(payload, null, 2))) {
        this.benchmarkFlowToken = null;
        this._setStatus("error", _("Could not copy benchmark results"), this.lastTranscript);
        return;
      }
      let fastest = typeof payload.fastest_model === "string" ? payload.fastest_model.trim() : "";
      let message = this._payloadMessage(payload, _("Benchmark complete"));
      if (fastest !== "") {
        message += "; " + _("fastest: ") + fastest;
      }
      this.benchmarkFlowToken = null;
      this._setStatus("done", message + _("; copied results") + " (" + String(results.length) + ")", this.lastTranscript);
    }, { timeoutMs: BENCHMARK_COMMAND_TIMEOUT_MS });
  },

  _setAlarmOptionStatus: function(message) {
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _setAlarmErrorStatus: function(message) {
    this._setStatusPreservingRecording("error", message, this.lastTranscript);
  },

  _refreshAlarmMenu: function() {
    if (!this._canMutateMenu(this.alarmItem)) {
      return;
    }
    if (this.alarmMenuRefreshToken) {
      return;
    }
    let refreshToken = {};
    this.alarmMenuRefreshToken = refreshToken;
    this._populateAlarmMenu([], _("Loading alarms..."));
    let alarmListArgs;
    try {
      alarmListArgs = this._alarmListArgs();
    } catch (error) {
      if (this.alarmMenuRefreshToken === refreshToken) {
        this.alarmMenuRefreshToken = null;
      }
      this._recordLifecycleError("alarm-refresh", error);
      this._populateAlarmMenu([], "", _("Could not prepare alarm list"));
      this._setAlarmErrorStatus(_("Could not prepare alarm list"));
      return;
    }
    this._spawnJson(alarmListArgs, (payload) => {
      if (this.alarmMenuRefreshToken !== refreshToken) {
        return;
      }
      this.alarmMenuRefreshToken = null;
      if (!this._canMutateMenu(this.alarmItem)) {
        return;
      }
      if (payload.error) {
        this._populateAlarmMenu([], this._sanitizeErrorMessage(payload.error));
        this._setAlarmErrorStatus(payload.error);
        return;
      }
      this._populateAlarmMenu(payload.alarms || [], payload.summary || "");
    });
  },

  _populateAlarmMenu: function(alarms, summary, message) {
    if (!this.alarmItem) {
      return;
    }
    alarms = Array.isArray(alarms) ? alarms : [];
    alarms = alarms.filter((alarm) => alarm && typeof alarm === "object" && typeof alarm.id === "string" && alarm.id.trim() !== "");
    let alarmsWereTruncated = alarms.length > MAX_ALARM_MENU_ENTRIES;
    if (alarmsWereTruncated) {
      alarms = alarms.slice(0, MAX_ALARM_MENU_ENTRIES);
    }
    this._clearMenuItems(this.alarmItem.menu);

    let messageText = typeof message === "string" ? message.trim() : "";
    let summaryText = typeof summary === "string" ? summary.trim() : "";
    let summaryLabel = messageText || summaryText || _("No alarms configured");
    summaryLabel = this._uiMessageText(summaryLabel);
    let summaryItem = new PopupMenu.PopupMenuItem(summaryLabel);
    summaryItem.setSensitive(false);
    this.alarmItem.menu.addMenuItem(summaryItem);

    let checkNow = new PopupMenu.PopupIconMenuItem(_("Check alarms now"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(checkNow, "activate", () => this._checkAlarms(true));
    this.alarmItem.menu.addMenuItem(checkNow);

    let copyCommands = new PopupMenu.PopupIconMenuItem(_("Copy alarm commands"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(copyCommands, "activate", () => this._copyAlarmCommands());
    this.alarmItem.menu.addMenuItem(copyCommands);

    let openFolder = new PopupMenu.PopupIconMenuItem(_("Open alarm store"), "folder-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(openFolder, "activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon"]), _("Opened alarm store"));
    });
    this.alarmItem.menu.addMenuItem(openFolder);

    this.alarmItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (messageText !== "") {
      return;
    }
    if (!alarms || alarms.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No alarms configured"));
      empty.setSensitive(false);
      this.alarmItem.menu.addMenuItem(empty);
      return;
    }
    for (let alarm of alarms) {
      if (!alarm || typeof alarm !== "object") {
        continue;
      }
      this._addAlarmMenuEntry(alarm);
    }
    if (alarmsWereTruncated) {
      this.alarmItem.menu.addMenuItem(this._selectionInfoItem(_("Alarm list truncated for safety")));
    }
  },

  _addAlarmMenuEntry: function(alarm) {
    if (!alarm || typeof alarm !== "object") {
      return;
    }
    let id;
    try {
      id = this._coerceCliTextArg(alarm.id, "alarm id").trim();
    } catch (err) {
      this._safeLogError(err);
      return;
    }
    if (id === "") {
      return;
    }
    let enabled = alarm.enabled === true;
    let alarmLabel = typeof alarm.label === "string" ? alarm.label.trim() : "";
    let alarmTime = typeof alarm.time === "string" ? alarm.time.trim() : "";
    let label = (enabled ? "[x] " : "[ ] ") + (alarmLabel || alarmTime || id);
    let summary = typeof alarm.summary === "string" ? alarm.summary.trim() : "";
    if (summary !== "") {
      label += " - " + summary;
    }
    label = this._uiMessageText(label);
    let entry = new PopupMenu.PopupSubMenuMenuItem(label);
    this.alarmItem.menu.addMenuItem(entry);

    let details = new PopupMenu.PopupMenuItem(this._uiMessageText(id));
    details.setSensitive(false);
    entry.menu.addMenuItem(details);

    let toggle = new PopupMenu.PopupIconMenuItem(enabled ? _("Disable alarm") : _("Enable alarm"), enabled ? "media-playback-pause-symbolic" : "media-playback-start-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(toggle, "activate", () => this._setAlarmEnabled(id, !enabled));
    entry.menu.addMenuItem(toggle);

    let remove = new PopupMenu.PopupIconMenuItem(_("Remove alarm"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(remove, "activate", () => this._removeAlarm(id));
    entry.menu.addMenuItem(remove);
  },

  _copyAlarmCommands: function() {
    let text = [
      "speed-of-cinnamon alarms add --time 09:00 --name \"Standup\" --days weekdays --json",
      "speed-of-cinnamon alarms list --json",
      "speed-of-cinnamon alarms check --mark --json"
    ].join("\n");
    if (!this._setClipboardText(text)) {
      this._setStatusPreservingRecording("error", _("Could not copy alarm commands"), this.lastTranscript);
      return;
    }
    this._setStatusPreservingRecording("done", _("Copied alarm commands"), this.lastTranscript);
  },

  _setAlarmEnabled: function(id, enabled) {
    if (this.alarmActionToken || this._hasActiveRecordingState()) {
      return;
    }
    this.alarmMenuRefreshToken = null;
    let actionToken = {};
    this.alarmActionToken = actionToken;
    this._setAlarmOptionStatus(enabled ? _("Enabling alarm...") : _("Disabling alarm..."));
    let alarmEnableArgs;
    try {
      alarmEnableArgs = this._alarmEnableArgs(id, enabled);
    } catch (error) {
      if (this.alarmActionToken === actionToken) {
        this.alarmActionToken = null;
      }
      this._recordLifecycleError("alarm-action", error);
      this._setAlarmErrorStatus(_("Could not prepare alarm update"));
      return;
    }
    this._spawnJson(alarmEnableArgs, (payload) => {
      if (this.alarmActionToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.alarmActionToken = null;
        this._setAlarmErrorStatus(payload.error);
        return;
      }
      this.alarmActionToken = null;
      this.alarmMenuRefreshToken = null;
      this._setAlarmOptionStatus(enabled ? _("Alarm enabled") : _("Alarm disabled"));
      this._refreshAlarmMenu();
    });
  },

  _removeAlarm: function(id) {
    if (this.alarmActionToken || this._hasActiveRecordingState()) {
      return;
    }
    this.alarmMenuRefreshToken = null;
    let actionToken = {};
    this.alarmActionToken = actionToken;
    this._setAlarmOptionStatus(_("Removing alarm..."));
    let alarmRemoveArgs;
    try {
      alarmRemoveArgs = this._alarmRemoveArgs(id);
    } catch (error) {
      if (this.alarmActionToken === actionToken) {
        this.alarmActionToken = null;
      }
      this._recordLifecycleError("alarm-action", error);
      this._setAlarmErrorStatus(_("Could not prepare alarm removal"));
      return;
    }
    this._spawnJson(alarmRemoveArgs, (payload) => {
      if (this.alarmActionToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.alarmActionToken = null;
        this._setAlarmErrorStatus(payload.error);
        return;
      }
      this.alarmActionToken = null;
      this.alarmMenuRefreshToken = null;
      this._setAlarmOptionStatus(payload.removed === true ? _("Alarm removed") : _("Alarm not found"));
      this._refreshAlarmMenu();
    });
  },

  _checkAlarms: function(manual) {
    if (this.alarmCheckToken || (manual && this._hasActiveRecordingState())) {
      return;
    }
    this.alarmMenuRefreshToken = null;
    let checkToken = {};
    this.alarmCheckToken = checkToken;
    let alarmCheckArgs;
    try {
      alarmCheckArgs = this._alarmCheckArgs();
    } catch (error) {
      if (this.alarmCheckToken === checkToken) {
        this.alarmCheckToken = null;
      }
      this._recordLifecycleError("alarm-check", error);
      if (manual) {
        this._setAlarmErrorStatus(_("Could not prepare alarm check"));
      }
      return;
    }
    this._spawnJson(alarmCheckArgs, (payload) => {
      if (this.alarmCheckToken !== checkToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.alarmCheckToken = null;
        if (manual) {
          this._setAlarmErrorStatus(payload.error);
        }
        return;
      }
      this.alarmCheckToken = null;
      let due = Array.isArray(payload.due)
        ? payload.due.filter((alarm) => alarm && typeof alarm === "object")
        : [];
      let dueCount = due.length;
      let dueWasTruncated = dueCount > MAX_ALARM_NOTIFICATIONS;
      if (dueWasTruncated) {
        due = due.slice(0, MAX_ALARM_NOTIFICATIONS);
      }
      for (let alarm of due) {
        if (alarm.notify !== true) {
          continue;
        }
        let body = typeof alarm.body === "string" ? alarm.body.trim() : "";
        let label = typeof alarm.label === "string" ? alarm.label.trim() : "";
        this._notify(_("Speed of Cinnamon alarm"), body || label || _("Alarm due"), alarm.critical === true);
      }
      if (due.length > 0) {
        let first = due[0] || {};
        if (manual || this.status === "idle" || this.status === "ready" || this.status === "done") {
          let firstLabel = typeof first.label === "string" ? first.label.trim() : "";
          let firstTime = typeof first.time === "string" ? first.time.trim() : "";
          let alarmStatusLabel = firstLabel || firstTime || String(dueCount);
          if (dueWasTruncated) {
            alarmStatusLabel += " (" + _("some notifications suppressed for safety") + ")";
          }
          this._setAlarmOptionStatus(_("Alarm: ") + alarmStatusLabel);
        }
      } else if (manual) {
        this._setAlarmOptionStatus(_("No alarms due"));
      }
      if (manual) {
        this.alarmMenuRefreshToken = null;
        this._refreshAlarmMenu();
      }
    });
  },

  _refreshInputSourceMenu: function() {
    if (!this._canMutateMenu(this.inputSourceItem)) {
      return;
    }
    if (this.inputSourceMenuRefreshToken) {
      return;
    }
    let refreshToken = {};
    this.inputSourceMenuRefreshToken = refreshToken;
    this._populateInputSourceMenu([], _("Loading input sources..."));
    let inputSourceArgs;
    try {
      inputSourceArgs = this._listInputsArgs();
    } catch (error) {
      if (this.inputSourceMenuRefreshToken === refreshToken) {
        this.inputSourceMenuRefreshToken = null;
      }
      this._recordLifecycleError("input-source-refresh", error);
      this._populateInputSourceMenu([], _("Could not prepare input source list"));
      this._setStatusPreservingRecording("error", _("Could not prepare input source list"), this.lastTranscript);
      return;
    }
    this._spawnJson(inputSourceArgs, (payload) => {
      if (this.inputSourceMenuRefreshToken !== refreshToken) {
        return;
      }
      this.inputSourceMenuRefreshToken = null;
      if (!this._canMutateMenu(this.inputSourceItem)) {
        return;
      }
      if (payload.error) {
        this._populateInputSourceMenu([], this._sanitizeErrorMessage(payload.error));
        this._setStatusPreservingRecording("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      this._populateInputSourceMenu(payload.sources || []);
    });
  },

  _populateInputSourceMenu: function(sources, message) {
    if (!this.inputSourceItem) {
      return;
    }
    sources = Array.isArray(sources) ? sources : [];
    sources = sources.filter((source) => source && typeof source === "object" && typeof source.name === "string" && source.name.trim() !== "");
    let sourcesWereTruncated = sources.length > MAX_INPUT_SOURCE_MENU_ENTRIES;
    if (sourcesWereTruncated) {
      sources = sources.slice(0, MAX_INPUT_SOURCE_MENU_ENTRIES);
    }
    let messageText = typeof message === "string" ? message.trim() : "";
    messageText = this._uiMessageText(messageText);
    this._clearMenuItems(this.inputSourceItem.menu);
    let current = String(this.inputDevice || "");
    let currentWasListed = current === "";
    let defaultLabel = (current === "" ? "[x] " : "[ ] ") + _("System default");
    let defaultItem = this._selectionMenuItem(defaultLabel);
    this._connectSafe(defaultItem, "activate", () => this._selectInputSource("", _("system default")));
    this.inputSourceItem.menu.addMenuItem(defaultItem);

    let addCurrentCustomInput = () => {
      if (current === "" || currentWasListed) {
        return;
      }
      let label = _("Current custom input source: ") + this._shortMenuText(current, 96);
      let item = this._selectionMenuItem("[x] " + label);
      this._connectSafe(item, "activate", () => this._selectInputSource(current, label));
      this.inputSourceItem.menu.addMenuItem(item);
      currentWasListed = true;
    };

    if (messageText !== "") {
      addCurrentCustomInput();
      this.inputSourceItem.menu.addMenuItem(this._selectionInfoItem(messageText));
      return;
    }
    if (!sources || sources.length === 0) {
      addCurrentCustomInput();
      this.inputSourceItem.menu.addMenuItem(this._selectionInfoItem(_("No input sources found")));
      return;
    }
    for (let source of sources) {
      if (!source || typeof source !== "object") {
        continue;
      }
      let sourceName;
      try {
        sourceName = this._coerceCliTextArg(source.name, "input source");
      } catch (err) {
        this._safeLogError(err);
        continue;
      }
      if (sourceName.trim() === "") {
        continue;
      }
      let description = typeof source.description === "string" ? source.description.trim() : "";
      let label = description || sourceName;
      if (source.default === true) {
        label += _(" (system default)");
      }
      if (current === sourceName) {
        currentWasListed = true;
      }
      let itemLabel = (current === sourceName ? "[x] " : "[ ] ") + this._shortMenuText(label + " - " + sourceName, 96);
      let item = this._selectionMenuItem(itemLabel);
      this._connectSafe(item, "activate", () => this._selectInputSource(sourceName, label));
      this.inputSourceItem.menu.addMenuItem(item);
    }
    addCurrentCustomInput();
    if (sourcesWereTruncated) {
      this.inputSourceItem.menu.addMenuItem(this._selectionInfoItem(_("Input source list truncated for safety")));
    }
  },

  _selectInputSource: function(name, label) {
    if (typeof name !== "string") {
      this._setStatusPreservingRecording("error", _("Input source is invalid"), this.lastTranscript);
      return;
    }
    let nextInputDevice;
    try {
      nextInputDevice = this._coerceCliTextArg(name, "input device");
    } catch (err) {
      this._setStatusPreservingRecording("error", _("Input source is invalid"), this.lastTranscript);
      return;
    }
    if (!this._commitSettingValue("inputDevice", "input-device", nextInputDevice, "settings-input-source", _("Input source setting could not be saved"))) {
      return;
    }
    this._refreshInputSourceMenu();
    let safeLabel = typeof label === "string" ? label : "";
    let message = this.inputDevice === ""
      ? _("Input device: system default")
      : _("Input device: ") + safeLabel;
    if (this._hasActiveRecordingState()) {
      this._setStatusPreservingRecording(this.status, _("Input device for next recording: ") + safeLabel, this.lastTranscript);
      return;
    }
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _selectDefaultInputSource: function() {
    this._selectInputSource("", _("system default"));
  },

  _refreshModelMenu: function() {
    if (!this._canMutateMenu(this.modelItem)) {
      return;
    }
    if (this.modelMenuRefreshToken) {
      return;
    }
    let refreshToken = {};
    this.modelMenuRefreshToken = refreshToken;
    this._populateModelMenu([], _("Loading voice models..."));
    let modelArgs;
    try {
      modelArgs = this._modelsArgs();
    } catch (error) {
      if (this.modelMenuRefreshToken === refreshToken) {
        this.modelMenuRefreshToken = null;
      }
      this._recordLifecycleError("model-refresh", error);
      this._populateModelMenu([], _("Could not prepare voice model list"));
      this._setStatusPreservingRecording("error", _("Could not prepare voice model list"), this.lastTranscript);
      return;
    }
    this._spawnJson(modelArgs, (payload) => {
      if (this.modelMenuRefreshToken !== refreshToken) {
        return;
      }
      this.modelMenuRefreshToken = null;
      if (!this._canMutateMenu(this.modelItem)) {
        return;
      }
      if (payload.error) {
        let safeError = this._sanitizeErrorMessage(payload.error);
        this._populateModelMenu([], safeError);
        this._setStatusPreservingRecording("error", safeError, this.lastTranscript);
        return;
      }
      this._populateModelMenu(payload.models || []);
    });
  },

  _populateModelMenu: function(models, message) {
    if (!this._canMutateMenu(this.modelItem)) {
      return;
    }
    models = Array.isArray(models) ? models : [];
    models = models.filter((model) => model && typeof model === "object" && typeof model.name === "string" && model.name.trim() !== "" && this._modelPathFromPayload(model) !== "");
    let voiceModelsWereTruncated = models.length > MAX_VOICE_MODEL_MENU_ENTRIES;
    if (voiceModelsWereTruncated) {
      models = models.slice(0, MAX_VOICE_MODEL_MENU_ENTRIES);
    }
    let messageText = typeof message === "string" ? message.trim() : "";
    messageText = this._uiMessageText(messageText);
    this._clearMenuItems(this.modelItem.menu);

    let autoActive = String(this.transcriber || "auto") === "auto" && String(this.whisperModel || "") === "";
    let automatic = this._selectionMenuItem((autoActive ? "[x] " : "[ ] ") + _("Automatic voice model"));
    this._connectSafe(automatic, "activate", () => this._selectAutomaticVoiceBackend());
    this.modelItem.menu.addMenuItem(automatic);

    let whisperCommandActive = String(this.transcriber || "") === "whisper" && String(this.whisperModel || "") === "";
    let whisperCommand = this._selectionMenuItem((whisperCommandActive ? "[x] " : "[ ] ") + _("OpenAI Whisper command"));
    this._connectSafe(whisperCommand, "activate", () => this._selectStaticVoiceBackend("whisper", _("Voice model: OpenAI Whisper command")));
    this.modelItem.menu.addMenuItem(whisperCommand);

    let customCommandActive = String(this.transcriber || "") === "command" && String(this.whisperModel || "") === "";
    let customCommandConfigured = String(this.transcriberCommand || "").trim() !== "";
    let customCommandLabel = _("Custom command") + (customCommandConfigured ? "" : _(" - configure in settings"));
    let customCommand = this._selectionMenuItem((customCommandActive ? "[x] " : "[ ] ") + customCommandLabel);
    this._connectSafe(customCommand, "activate", () => {
      if (customCommandConfigured) {
        this._selectStaticVoiceBackend("command", _("Voice model: custom command"));
        return;
      }
      this._openAppletSettings();
      this._setStatusPreservingRecording("ready", _("Configure custom voice command in applet settings"), this.lastTranscript);
    });
    this.modelItem.menu.addMenuItem(customCommand);

    this.modelItem.menu.addMenuItem(this._selectionInfoItem(_("Active: ") + this._activeVoiceModelSummary()));

    let download = this._styleMenuItemLabel(
      new PopupMenu.PopupIconMenuItem(_("Download starter model") + ": " + this._starterVoiceModelName(), "folder-download-symbolic", St.IconType.SYMBOLIC)
    );
    this._connectSafe(download, "activate", () => this._downloadStarterModel());
    this.modelItem.menu.addMenuItem(download);

    let openFolder = new PopupMenu.PopupIconMenuItem(_("Open GGML model folder"), "folder-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(openFolder, "activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "whisper.cpp"]), _("Opened model folder"));
    });
    this.modelItem.menu.addMenuItem(openFolder);

    let openCt2Folder = new PopupMenu.PopupIconMenuItem(_("Open CTranslate2 model folder"), "folder-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(openCt2Folder, "activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "ctranslate2"]), _("Opened model folder"));
    });
    this.modelItem.menu.addMenuItem(openCt2Folder);

    this.modelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (messageText !== "") {
      this.modelItem.menu.addMenuItem(this._selectionInfoItem(messageText));
      return;
    }
    if (!models || models.length === 0) {
      this.modelItem.menu.addMenuItem(this._selectionInfoItem(_("No models in catalog")));
      return;
    }
    let ct2Menu = new PopupMenu.PopupSubMenuMenuItem(_("CTranslate2"));
    let ggmlMenu = new PopupMenu.PopupSubMenuMenuItem(_("GGML"));
    let externalMenu = new PopupMenu.PopupSubMenuMenuItem(_("External API"));
    this._styleMenuItemLabel(ct2Menu);
    this._styleMenuItemLabel(ggmlMenu);
    this._styleMenuItemLabel(externalMenu);
    this._styleSelectionSubmenu(ct2Menu);
    this._styleSelectionSubmenu(ggmlMenu);
    this._styleSelectionSubmenu(externalMenu);
    this.modelItem.menu.addMenuItem(ct2Menu);
    this.modelItem.menu.addMenuItem(ggmlMenu);
    this.modelItem.menu.addMenuItem(externalMenu);

    this._populateExternalApiVoiceMenu(externalMenu.menu);

    let ct2Count = 0;
    let ggmlCount = 0;
    for (let model of models) {
      if (!model || typeof model !== "object") {
        continue;
      }
      let modelFormat = typeof model.model_format === "string" ? model.model_format.trim().toLowerCase() : "";
      let modelBackend = typeof model.backend === "string" ? model.backend.trim().toLowerCase() : "";
      if (modelFormat === "ctranslate2" || modelBackend === "faster-whisper") {
        this._addModelMenuEntry(model, ct2Menu.menu);
        ct2Count++;
      } else {
        this._addModelMenuEntry(model, ggmlMenu.menu);
        ggmlCount++;
      }
    }
    if (ct2Count === 0) {
      ct2Menu.menu.addMenuItem(this._selectionInfoItem(_("No CTranslate2 models in catalog")));
    }
    if (ggmlCount === 0) {
      ggmlMenu.menu.addMenuItem(this._selectionInfoItem(_("No GGML models in catalog")));
    }
    if (voiceModelsWereTruncated) {
      this.modelItem.menu.addMenuItem(this._selectionInfoItem(_("Voice model list truncated for safety")));
    }
  },

  _populateExternalApiVoiceMenu: function(parentMenu) {
    let active = String(this.transcriber || "") === "openai-compatible";
    let model = this._shortMenuText(String(this.openaiCompatibleModel || "").trim(), 96);
    let url = this._shortMenuText(String(this.openaiCompatibleUrl || "").trim(), 96);
    let useItem = new PopupMenu.PopupIconMenuItem((active ? "[x] " : "[ ] ") + _("Use OpenAI-compatible API"), "network-server-symbolic", St.IconType.SYMBOLIC);
    this._styleMenuItemLabel(useItem);
    this._connectSafe(useItem, "activate", () => this._openExternalApiEnvEditor("voice"));
    parentMenu.addMenuItem(useItem);

    parentMenu.addMenuItem(this._selectionInfoItem(_("Endpoint: ") + (url || _("not configured"))));
    parentMenu.addMenuItem(this._selectionInfoItem(_("Model: ") + (model || _("not configured"))));
    parentMenu.addMenuItem(this._selectionInfoItem(_("Configure URL, model, and optional API key in applet settings.")));
  },

  _modelPathFromPayload: function(model) {
    if (!model || typeof model !== "object" || typeof model.filename !== "string" || typeof model.model_format !== "string") {
      return "";
    }
    let filename = model.filename.trim();
    let modelFormat = model.model_format.trim().toLowerCase();
    if (filename === "" || !/^[A-Za-z0-9._-]+$/.test(filename)) {
      return "";
    }
    let directory = "";
    if (modelFormat === "ggml") {
      directory = "whisper.cpp";
    } else if (modelFormat === "ctranslate2") {
      directory = "ctranslate2";
    } else {
      return "";
    }
    return GLib.build_filenamev([
      GLib.get_user_data_dir(),
      "speed-of-cinnamon",
      "models",
      directory,
      filename,
    ]);
  },

  _isUsableVoiceModelPayload: function(model) {
    let backend = model && typeof model.backend === "string" ? model.backend.trim() : "";
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    let modelFormat = model && typeof model.model_format === "string" ? model.model_format.trim().toLowerCase() : "";
    let expectedBackend = modelFormat === "ggml"
      ? "whisper-cpp"
      : modelFormat === "ctranslate2"
        ? "faster-whisper"
        : "";
    return Boolean(model && typeof model === "object" && model.downloaded === true && name !== "" &&
      this._modelPathFromPayload(model) !== "" &&
      backend === expectedBackend);
  },

  _addModelMenuEntry: function(model, parentMenu) {
    if (!model || typeof model !== "object") {
      return;
    }
    let name = typeof model.name === "string" ? model.name.trim() : "";
    if (name === "") {
      return;
    }
    let path = this._modelPathFromPayload(model);
    if (path === "") {
      return;
    }
    let downloaded = model.downloaded === true;
    let usable = downloaded && this._isUsableVoiceModelPayload(model);
    let current = usable && this.whisperModel && path === String(this.whisperModel);
    let compatible = this._voiceModelSupportsCurrentLanguage(model);
    let size = typeof model.size === "string" ? model.size.trim() : "";
    let description = typeof model.description === "string" ? model.description.trim() : "";
    description = this._uiMessageText(description);
    let label = (current ? "[x] " : "[ ] ") + name + " (" + (size || "?") + ")";
    if (!compatible) {
      label += _(" - English only");
    }
    if (!downloaded) {
      label += _(" - not downloaded");
    } else if (!usable) {
      label += _(" - invalid metadata");
    }
    label = this._uiMessageText(label);
    let entry = new PopupMenu.PopupSubMenuMenuItem(label);
    this._styleMenuItemLabel(entry);
    this._styleSelectionSubmenu(entry);
    parentMenu.addMenuItem(entry);

    entry.menu.addMenuItem(this._selectionInfoItem(description));
    if (!compatible) {
      entry.menu.addMenuItem(this._selectionInfoItem(_("Not suitable for primary language: ") + this._voiceModelLanguage()));
    }

    if (downloaded) {
      let useItem = new PopupMenu.PopupIconMenuItem(_("Use this model"), "emblem-ok-symbolic", St.IconType.SYMBOLIC);
      this._styleMenuItemLabel(useItem);
      useItem.setSensitive(!current && compatible && usable);
      this._connectSafe(useItem, "activate", () => this._selectVoiceModel(model));
      entry.menu.addMenuItem(useItem);

      let removeItem = new PopupMenu.PopupIconMenuItem(_("Remove model"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
      this._connectSafe(removeItem, "activate", () => this._removeVoiceModel(model));
      entry.menu.addMenuItem(removeItem);
      return;
    }

    let downloadItem = this._styleMenuItemLabel(new PopupMenu.PopupIconMenuItem(_("Download model"), "folder-download-symbolic", St.IconType.SYMBOLIC));
    this._connectSafe(downloadItem, "activate", () => this._downloadVoiceModel(model));
    entry.menu.addMenuItem(downloadItem);
  },

  _isEnglishLanguage: function(language) {
    let value = String(language || "").trim().toLowerCase().replace("_", "-");
    return value === "" || value === "en" || value === "eng" || value === "english" || value.indexOf("en-") === 0;
  },

  _voiceModelSupportsCurrentLanguage: function(model) {
    let languages = Array.isArray(model.languages)
      ? model.languages.filter((language) => typeof language === "string" && language.trim() !== "")
      : [];
    if (languages.length > 0) {
      for (let language of languages) {
        if (this._languageMatches(this._voiceModelLanguage(), language)) {
          return true;
        }
      }
      return false;
    }
    let name = String(model.name || "").trim().toLowerCase();
    let filename = String(model.filename || model.path || "").trim().toLowerCase();
    return this._voiceModelSupportsLanguage(name, filename, this._voiceModelLanguage());
  },

  _languageMatches: function(language, allowed) {
    if (typeof allowed !== "string" || allowed.trim() === "") {
      return false;
    }
    let current = String(language || "").trim().toLowerCase().replace("_", "-");
    let expected = String(allowed || "").trim().toLowerCase().replace("_", "-");
    if (expected === "") {
      return true;
    }
    if (expected === "en") {
      return this._isEnglishLanguage(current);
    }
    return current === expected || current.indexOf(expected + "-") === 0;
  },

  _voiceModelSupportsLanguage: function(name, filename, language) {
    let modelName = String(name || "").trim().toLowerCase();
    let modelFile = String(filename || "").trim().toLowerCase();
    let englishOnly = modelName.endsWith(".en") || modelFile.indexOf(".en.") >= 0 || modelFile.endsWith(".en.bin");
    return this._isEnglishLanguage(language) || !englishOnly;
  },

  _voiceModelLanguage: function() {
    return this._primaryLanguage();
  },

  _starterVoiceModelName: function() {
    return "ct2-base-int8";
  },

  _activeVoiceModelSummary: function() {
    let backend = String(this.transcriber || "auto");
    let model = String(this.whisperModel || "").trim();
    if ((backend === "whisper-cpp" || backend === "faster-whisper") && model !== "") {
      return this._shortMenuText(GLib.path_get_basename(model), 96);
    }
    if (backend === "command") return _("custom command");
    if (backend === "whisper") return _("Whisper command");
    if (backend === "openai-compatible") {
      let externalModel = String(this.openaiCompatibleModel || "").trim() || _("not configured");
      return _("External API: ") + this._shortMenuText(externalModel, 96);
    }
    if (backend === "whisper-cpp") return _("local model file");
    if (backend === "faster-whisper") return _("local model directory");
    return this._currentLanguage() + _(", primary: ") + this._voiceModelLanguage() + _(", starter: ") + this._starterVoiceModelName();
  },

  _whisperModelSupportsLanguage: function(language) {
    let model = String(this.whisperModel || "").trim();
    if (model === "") {
      return true;
    }
    return this._voiceModelSupportsLanguage("", model, language);
  },

  _commitVoiceBackendSettings: function(transcriber, whisperModel, group, errorMessage) {
    let previousTranscriber = this.transcriber;
    let previousWhisperModel = this.whisperModel;
    let settingsWrites = [
      ["transcriber", transcriber, previousTranscriber],
      ["whisper-model", whisperModel, previousWhisperModel],
    ];
    let attemptedWrites = [];
    try {
      for (let setting of settingsWrites) {
        attemptedWrites.push(setting);
        let result = this.settings.setValue(setting[0], setting[1]);
        if (result === false) {
          throw new Error("Voice backend setting could not be saved");
        }
      }
    } catch (err) {
      for (let index = attemptedWrites.length - 1; index >= 0; index--) {
        let setting = attemptedWrites[index];
        try {
          let rollbackResult = this.settings.setValue(setting[0], setting[2]);
          if (rollbackResult === false) {
            throw new Error("Voice backend setting rollback failed");
          }
        } catch (rollbackErr) {
          this._safeLogError(rollbackErr);
        }
      }
      this.transcriber = previousTranscriber;
      this.whisperModel = previousWhisperModel;
      this._recordLifecycleError(group || "voice-settings", err);
      if (errorMessage) {
        this._setStatusPreservingRecording("error", errorMessage, this.lastTranscript);
      }
      return false;
    }
    this.transcriber = transcriber;
    this.whisperModel = whisperModel;
    return true;
  },

  _ensureVoiceModelCompatibleWithPrimaryLanguage: function(showStatus) {
    return this._ensureVoiceModelCompatibleForLanguage(this._voiceModelLanguage(), showStatus, _("primary language"));
  },

  _ensureVoiceModelCompatibleWithCurrentLanguage: function(showStatus) {
    return this._ensureVoiceModelCompatibleForLanguage(this._currentLanguage(), showStatus, _("current language"));
  },

  _ensureVoiceModelCompatibleForLanguage: function(language, showStatus, label) {
    if (this._whisperModelSupportsLanguage(language)) {
      return true;
    }
    if (!this._commitVoiceBackendSettings(
      "auto",
      "",
      "voice-model-language",
      _("Voice model settings could not be saved")
    )) {
      return false;
    }
    this._refreshModelMenu();
    if (showStatus) {
      this._setStatus("error", _("English-only model was disabled because it does not support ") + label + ": " + language, this.lastTranscript);
    }
    return false;
  },

  _downloadStarterModel: function() {
    this._downloadVoiceModel({ name: this._starterVoiceModelName() });
  },

  _downloadVoiceModel: function(model) {
    if (this.isCommandRunning || this._hasActiveRecordingState()) {
      return;
    }
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    if (name === "") {
      name = this._starterVoiceModelName();
    }
    this.modelMenuRefreshToken = null;
    let actionToken = {};
    this.voiceModelActionToken = actionToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Downloading model: ") + name, this.lastTranscript);
    let downloadArgs;
    try {
      downloadArgs = this._downloadModelArgs(name);
    } catch (error) {
      if (this.voiceModelActionToken === actionToken) {
        this.voiceModelActionToken = null;
      }
      this.isCommandRunning = false;
      this._recordLifecycleError("model-action", error);
      this._setStatus("error", _("Could not prepare model download"), this.lastTranscript);
      return;
    }
    this._spawnJson(downloadArgs, (payload) => {
      this.isCommandRunning = false;
      if (this.voiceModelActionToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.voiceModelActionToken = null;
      if (payload.error) {
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        this._refreshModelMenu();
        return;
      }
      if (!this._isUsableVoiceModelPayload(payload)) {
        this._setStatus("error", _("Downloaded model response was invalid"), this.lastTranscript);
        this._refreshModelMenu();
        return;
      }
      this._selectVoiceModel(payload);
      this._refreshModelMenu();
    });
  },

  _removeVoiceModel: function(model) {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this.voiceModelActionToken) {
      return;
    }
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    let path = this._modelPathFromPayload(model);
    if (name === "") {
      return;
    }
    this.modelMenuRefreshToken = null;
    let actionToken = {};
    this.voiceModelActionToken = actionToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Removing model: ") + name, this.lastTranscript);
    let removeArgs;
    try {
      removeArgs = this._removeModelArgs(name);
    } catch (error) {
      if (this.voiceModelActionToken === actionToken) {
        this.voiceModelActionToken = null;
      }
      this.isCommandRunning = false;
      this._recordLifecycleError("model-action", error);
      this._setStatus("error", _("Could not prepare model removal"), this.lastTranscript);
      return;
    }
    this._spawnJson(removeArgs, (payload) => {
      this.isCommandRunning = false;
      if (this.voiceModelActionToken !== actionToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.voiceModelActionToken = null;
      if (payload.error) {
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        this._refreshModelMenu();
        return;
      }
      if (payload.removed !== true) {
        this._setStatus("ready", _("Model was not downloaded: ") + name, this.lastTranscript);
        this._refreshModelMenu();
        return;
      }
      if (path !== "" && path === String(this.whisperModel || "")) {
        if (!this._commitVoiceBackendSettings(
          "auto",
          "",
          "voice-model-remove",
          _("Removed model, but voice settings could not be updated")
        )) {
          this._refreshModelMenu();
          return;
        }
      }
      this._setStatus("done", _("Removed model: ") + name, this.lastTranscript);
      this._refreshModelMenu();
    });
  },

  _selectVoiceModel: function(model) {
    if (this.voiceModelActionToken) {
      return false;
    }
    let path = this._modelPathFromPayload(model);
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    let backend = model && typeof model.backend === "string" ? model.backend.trim() : "";
    if (!this._isUsableVoiceModelPayload(model)) {
      return false;
    }
    if (!this._voiceModelSupportsCurrentLanguage(model)) {
      this._setStatusPreservingRecording("error", _("English-only model cannot transcribe primary language: ") + this._voiceModelLanguage(), this.lastTranscript);
      return false;
    }
    if (!this._commitVoiceBackendSettings(
      backend,
      path,
      "voice-model-select",
      _("Voice model settings could not be saved")
    )) {
      return false;
    }
    this._setStatusPreservingRecording("ready", _("Voice model: ") + name, this.lastTranscript);
    return true;
  },

  _selectAutomaticVoiceBackend: function() {
    if (this.voiceModelActionToken) {
      return;
    }
    if (!this._commitVoiceBackendSettings(
      "auto",
      "",
      "voice-automatic",
      _("Voice model settings could not be saved")
    )) {
      return false;
    }
    this._refreshModelMenu();
    this._setStatusPreservingRecording("ready", _("Voice model: automatic"), this.lastTranscript);
    return true;
  },

  _selectStaticVoiceBackend: function(transcriber, message) {
    if (this.voiceModelActionToken) {
      return;
    }
    if (!this._commitVoiceBackendSettings(
      String(transcriber || "auto"),
      "",
      "voice-static",
      _("Voice model settings could not be saved")
    )) {
      return false;
    }
    this._refreshModelMenu();
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
    return true;
  },

  _externalApiEnvPath: function() {
    return GLib.build_filenamev([GLib.get_user_config_dir(), "speed-of-cinnamon", "external-api.env"]);
  },

  _externalApiEnvValue: function(value, fallback) {
    let normalized = typeof value === "string" ? value.trim() : "";
    let safeFallback = typeof fallback === "string" ? fallback : "";
    if (normalized === LEGACY_OPENAI_COMPATIBLE_URL) {
      return DEFAULT_OPENAI_COMPATIBLE_URL;
    }
    return normalized || safeFallback;
  },

  _validateExternalApiUrl: function(value, fieldName) {
    let field = String(fieldName || "openai-compatible URL");
    let normalized = this._coerceCliTextArg(value, field).trim();
    let match = /^([a-z][a-z0-9+.-]*):\/\/([^\/?#]+)(\/[^?#]*)?$/i.exec(normalized);
    if (!match || (match[1].toLowerCase() !== "http" && match[1].toLowerCase() !== "https")) {
      throw new Error(field + " must use http:// or https://");
    }
    let authority = match[2];
    if (authority.indexOf("@") >= 0) {
      throw new Error(field + " must not contain userinfo");
    }
    let host = authority;
    let port = "";
    if (authority.charAt(0) === "[") {
      let closing = authority.indexOf("]");
      if (closing < 0) {
        throw new Error(field + " has invalid host");
      }
      host = authority.slice(0, closing + 1);
      port = authority.slice(closing + 1);
    } else {
      let colon = authority.lastIndexOf(":");
      if (colon >= 0) {
        if (authority.indexOf(":") !== colon) {
          throw new Error(field + " has invalid host");
        }
        host = authority.slice(0, colon);
        port = authority.slice(colon);
      }
    }
    if (host === "") {
      throw new Error(field + " requires a host");
    }
    if (port !== "" && !/^:[0-9]{1,5}$/.test(port)) {
      throw new Error(field + " has invalid port");
    }
    let normalizedHost = host.toLowerCase();
    let localHost = normalizedHost === "localhost" || normalizedHost === "[::1]" || /^127\.(?:[0-9]{1,3}\.){2}[0-9]{1,3}$/.test(normalizedHost);
    if (match[1].toLowerCase() === "http" && !localHost) {
      throw new Error(field + " must use https:// unless host is local loopback");
    }
    return normalized;
  },

  _validatedExternalApiConfig: function(values) {
    values = values || {};
    let url = this._validateExternalApiUrl(
      this._externalApiEnvValue(values.url, DEFAULT_OPENAI_COMPATIBLE_URL),
      "openai-compatible URL"
    );
    let model = this._coerceCliTextArg(
      this._externalApiEnvValue(values.model, DEFAULT_OPENAI_COMPATIBLE_MODEL),
      "openai-compatible model"
    ).trim();
    let textModel = this._coerceCliTextArg(
      this._externalApiEnvValue(values.textModel, DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL),
      "openai-compatible text model"
    ).trim();
    let apiKeyValue = typeof values.apiKey === "string" ? values.apiKey : "";
    let apiKey = this._coerceCliTextArg(apiKeyValue, "openai-compatible API key").trim();
    if (model === "" || textModel === "") {
      throw new Error("openai-compatible model is required");
    }
    return { url: url, model: model, textModel: textModel, apiKey: apiKey };
  },

  _externalApiEnvContent: function() {
    let config = this._validatedExternalApiConfig({
      url: this.openaiCompatibleUrl,
      model: this.openaiCompatibleModel,
      textModel: this.openaiCompatibleTextModel,
      apiKey: this.externalApiEnvApiKey || this.openaiCompatibleApiKey || "",
    });
    return [
      "OPENAI_COMPATIBLE_URL=" + config.url,
      "OPENAI_COMPATIBLE_STT_MODEL=" + config.model,
      "OPENAI_COMPATIBLE_TEXT_MODEL=" + config.textModel,
      "OPENAI_COMPATIBLE_API_KEY=" + config.apiKey,
      ""
    ].join("\n");
  },

  _externalApiEnvFileInfo: function(path, allowMissing) {
    let file = Gio.File.new_for_path(path);
    try {
      let info = file.query_info("standard::type,standard::size,unix::mode", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
      if (info.get_file_type() !== Gio.FileType.REGULAR) {
        throw new Error("External API config file must be a regular file");
      }
      if (Number(info.get_size()) > MAX_EXTERNAL_API_ENV_BYTES) {
        throw new Error("External API config file is too large");
      }
      return info;
    } catch (err) {
      if (allowMissing && !GLib.file_test(path, GLib.FileTest.EXISTS) && !GLib.file_test(path, GLib.FileTest.IS_SYMLINK)) {
        return null;
      }
      throw err;
    }
  },

  _readExternalApiEnvFile: function(path) {
    this._externalApiEnvFileInfo(path, false);
    let ok;
    let contents;
    [ok, contents] = GLib.file_get_contents(path);
    if (!ok) {
      return "";
    }
    if (contents.length > MAX_EXTERNAL_API_ENV_BYTES) {
      throw new Error("External API config file is too large");
    }
    return ByteArray.toString(contents);
  },

  _writeExternalApiEnvFileContents: function(path, content) {
    let text = String(content || "");
    if (ByteArray.fromString(text).length > MAX_EXTERNAL_API_ENV_BYTES) {
      throw new Error("External API config file is too large");
    }
    let mkdirResult = GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
    if (mkdirResult !== 0) {
      throw new Error("External API config directory could not be created");
    }
    let setPrivateMode = () => {
      let modeResult = Gio.File.new_for_path(path).set_attribute_uint32(
        "unix::mode",
        0o600,
        Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
        null
      );
      if (modeResult === false) {
        throw new Error("External API config file mode could not be secured");
      }
    };
    let info = this._externalApiEnvFileInfo(path, true);
    if (info) {
      setPrivateMode();
    }
    let replaceResult = Gio.File.new_for_path(path).replace_contents(
      ByteArray.fromString(text),
      null,
      false,
      Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION,
      null
    );
    let replaceSucceeded = Array.isArray(replaceResult) ? replaceResult[0] : replaceResult;
    if (replaceSucceeded === false) {
      throw new Error("External API config file could not be replaced");
    }
    this._externalApiEnvFileInfo(path, false);
    setPrivateMode();
  },

  _writeExternalApiEnvFile: function() {
    let path = this._externalApiEnvPath();
    try {
      this._writeExternalApiEnvFileContents(path, this._externalApiEnvContent());
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("External API config file could not be written"), this.lastTranscript);
      return false;
    }
    return true;
  },

  _syncExternalApiConfigOnStartup: function() {
    let changed = false;
    if (String(this.openaiCompatibleUrl || "").trim() === LEGACY_OPENAI_COMPATIBLE_URL) {
      this.openaiCompatibleUrl = DEFAULT_OPENAI_COMPATIBLE_URL;
      this.settings.setValue("openai-compatible-url", this.openaiCompatibleUrl);
      changed = true;
    }
    if (String(this.openaiCompatibleModel || "").trim() === "") {
      this.openaiCompatibleModel = DEFAULT_OPENAI_COMPATIBLE_MODEL;
      this.settings.setValue("openai-compatible-model", this.openaiCompatibleModel);
      changed = true;
    }
    let hasLegacyApiKey = String(this.openaiCompatibleApiKey || "").trim() !== "";
    if (GLib.file_test(this._externalApiEnvPath(), GLib.FileTest.EXISTS)) {
      let envPath = this._ensureExternalApiEnvFile();
      if (envPath && this._applyExternalApiEnvFile(false)) {
        this._clearPersistedOpenAiCompatibleApiKey();
      }
      return;
    }
    if (changed || hasLegacyApiKey) {
      let envPath = this._ensureExternalApiEnvFile();
      if (envPath && this._applyExternalApiEnvFile(false)) {
        this._clearPersistedOpenAiCompatibleApiKey();
      }
    }
  },

  _clearPersistedOpenAiCompatibleApiKey: function() {
    if (String(this.openaiCompatibleApiKey || "").trim() === "") {
      return true;
    }
    let previousApiKey = this.openaiCompatibleApiKey;
    try {
      let result = this.settings.setValue("openai-compatible-api-key", "");
      if (result === false) {
        throw new Error("Persisted External API key could not be cleared");
      }
      this.openaiCompatibleApiKey = "";
      return true;
    } catch (err) {
      this.openaiCompatibleApiKey = previousApiKey;
      this._safeLogError(err);
      return false;
    }
  },

  _ensureExternalApiEnvFile: function() {
    let path;
    try {
      path = this._externalApiEnvPath();
      let mkdirResult = GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
      if (mkdirResult !== 0) {
        throw new Error("External API config directory could not be created");
      }
      if (!GLib.file_test(path, GLib.FileTest.EXISTS)) {
        this._writeExternalApiEnvFileContents(path, this._externalApiEnvContent());
      } else {
        this._migrateExternalApiEnvFile(path);
      }
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("External API config file could not be written"), this.lastTranscript);
      return null;
    }
    return path;
  },

  _migrateExternalApiEnvFile: function(path) {
    let text;
    try {
      text = this._readExternalApiEnvFile(path);
    } catch (err) {
      this._safeLogError(err);
      return;
    }
    if (text === "") {
      return;
    }
    let migrated = text;
    if (migrated.indexOf("OPENAI_COMPATIBLE_URL=" + LEGACY_OPENAI_COMPATIBLE_URL) >= 0) {
      migrated = migrated.replace("OPENAI_COMPATIBLE_URL=" + LEGACY_OPENAI_COMPATIBLE_URL, "OPENAI_COMPATIBLE_URL=" + DEFAULT_OPENAI_COMPATIBLE_URL);
    }
    if (migrated.indexOf("OPENAI_COMPATIBLE_STT_MODEL=") < 0 && migrated.indexOf("OPENAI_COMPATIBLE_MODEL=") >= 0) {
      migrated = migrated.replace("OPENAI_COMPATIBLE_MODEL=", "OPENAI_COMPATIBLE_STT_MODEL=");
    }
    if (migrated.indexOf("OPENAI_COMPATIBLE_STT_MODEL=") < 0) {
      let suffix = migrated.lastIndexOf("\n") === migrated.length - 1 ? "" : "\n";
      migrated += suffix + "OPENAI_COMPATIBLE_STT_MODEL=" + this._externalApiEnvValue(this.openaiCompatibleModel, DEFAULT_OPENAI_COMPATIBLE_MODEL) + "\n";
    }
    if (migrated.indexOf("OPENAI_COMPATIBLE_TEXT_MODEL=") < 0) {
      let suffix = migrated.lastIndexOf("\n") === migrated.length - 1 ? "" : "\n";
      migrated += suffix + "OPENAI_COMPATIBLE_TEXT_MODEL=" + this._externalApiEnvValue(this.openaiCompatibleTextModel, DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL) + "\n";
    }
    let legacyApiKey = this._coerceCliTextArg(
      this.openaiCompatibleApiKey || "",
      "openai-compatible API key"
    ).trim();
    let migratedValues = this._parseExternalApiEnvText(migrated);
    let migratedApiKey = typeof migratedValues.OPENAI_COMPATIBLE_API_KEY === "string"
      ? migratedValues.OPENAI_COMPATIBLE_API_KEY.trim()
      : "";
    if (legacyApiKey !== "" && migratedApiKey === "") {
      let suffix = migrated.lastIndexOf("\n") === migrated.length - 1 ? "" : "\n";
      migrated += suffix + "OPENAI_COMPATIBLE_API_KEY=" + legacyApiKey + "\n";
    }
    if (migrated !== text) {
      try {
        this._writeExternalApiEnvFileContents(path, migrated);
      } catch (err) {
        this._safeLogError(err);
        this._setStatusPreservingRecording("error", _("External API config file could not be written"), this.lastTranscript);
      }
    }
  },

  _parseExternalApiEnvText: function(text) {
    let values = {};
    for (let line of String(text || "").split(/\r?\n/)) {
      let trimmed = line.trim();
      if (trimmed === "" || trimmed.indexOf("#") === 0) {
        continue;
      }
      let pos = trimmed.indexOf("=");
      if (pos <= 0) {
        continue;
      }
      let key = trimmed.slice(0, pos).trim();
      let value = trimmed.slice(pos + 1).trim();
      if ((value.indexOf('"') === 0 && value.lastIndexOf('"') === value.length - 1) || (value.indexOf("'") === 0 && value.lastIndexOf("'") === value.length - 1)) {
        value = value.slice(1, -1);
      }
      values[key] = value;
    }
    return values;
  },

  _applyExternalApiEnvFile: function(showStatus) {
    let path = this._externalApiEnvPath();
    let text;
    try {
      text = this._readExternalApiEnvFile(path);
    } catch (err) {
      this._safeLogError(err);
      return false;
    }
    if (text === "") {
      return false;
    }
    let values = this._parseExternalApiEnvText(text);
    let config;
    try {
      config = this._validatedExternalApiConfig({
        url: values.OPENAI_COMPATIBLE_URL || "",
        model: values.OPENAI_COMPATIBLE_STT_MODEL || values.OPENAI_COMPATIBLE_MODEL || "",
        textModel: values.OPENAI_COMPATIBLE_TEXT_MODEL || "",
        apiKey: values.OPENAI_COMPATIBLE_API_KEY || "",
      });
      if (String(this.openaiCompatibleApiKey || "").trim() !== "" && config.apiKey.trim() === "") {
        throw new Error("External API config does not contain the persisted API key");
      }
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("External API config contains invalid values"), this.lastTranscript);
      return false;
    }
    let previousConfig = {
      url: this.openaiCompatibleUrl,
      model: this.openaiCompatibleModel,
      textModel: this.openaiCompatibleTextModel,
      apiKey: this.externalApiEnvApiKey,
    };
    let settingsWrites = [
      ["openai-compatible-url", config.url, previousConfig.url],
      ["openai-compatible-model", config.model, previousConfig.model],
      ["openai-compatible-text-model", config.textModel, previousConfig.textModel],
    ];
    let attemptedWrites = [];
    try {
      for (let setting of settingsWrites) {
        attemptedWrites.push(setting);
        let result = this.settings.setValue(setting[0], setting[1]);
        if (result === false) {
          throw new Error("External API setting could not be saved");
        }
      }
    } catch (err) {
      for (let index = attemptedWrites.length - 1; index >= 0; index--) {
        let setting = attemptedWrites[index];
        try {
          let rollbackResult = this.settings.setValue(setting[0], setting[2]);
          if (rollbackResult === false) {
            throw new Error("External API setting rollback failed");
          }
        } catch (rollbackErr) {
          this._safeLogError(rollbackErr);
        }
      }
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("External API settings could not be saved"), this.lastTranscript);
      return false;
    }
    this.openaiCompatibleUrl = config.url;
    this.openaiCompatibleModel = config.model;
    this.openaiCompatibleTextModel = config.textModel;
    this.externalApiEnvApiKey = config.apiKey;
    this._clearPersistedOpenAiCompatibleApiKey();
    if (showStatus) {
      this._setStatusPreservingRecording("ready", _("External API config loaded: ") + (this.openaiCompatibleModel || _("not configured")), this.lastTranscript);
    }
    return true;
  },

  _clearExternalApiEnvMonitor: function() {
    if (!this.externalApiEnvMonitor) {
      return true;
    }
    let monitor = this.externalApiEnvMonitor;
    if (!this._disconnectTrackedSignalsForTarget(monitor)) {
      return false;
    }
    try {
      let result = monitor.cancel();
      if (result === false) {
        throw new Error("External API monitor could not be cancelled");
      }
    } catch (err) {
      this._recordLifecycleError("monitor-cancel", err);
      return false;
    }
    if (!this._untrackMonitor(monitor)) {
      return false;
    }
    this.externalApiEnvMonitor = null;
    return true;
  },

  _watchExternalApiEnvFile: function(path) {
    if (!this._clearExternalApiEnvMonitor()) {
      return;
    }
    try {
      let file = Gio.File.new_for_path(path);
      let monitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
      this.externalApiEnvMonitor = monitor;
      this._trackMonitor(monitor);
      let connectionId = this._connectSafe(monitor, "changed", (monitor, fileObj, otherFile, eventType) => {
        if (this.appletRemoved) {
          return;
        }
        if (eventType === Gio.FileMonitorEvent.CHANGES_DONE_HINT || eventType === Gio.FileMonitorEvent.CREATED) {
          if (this._applyExternalApiEnvFile(true)) {
            if (this.appletRemoved) {
              return;
            }
            this._applyExternalApiEnvTarget(this.externalApiEnvApplyTarget || "voice");
          }
        }
      });
      if (!connectionId) {
        this._clearExternalApiEnvMonitor();
      }
    } catch (err) {
      this._clearExternalApiEnvMonitor();
      this._safeLogError(err);
    }
  },

  _openExternalApiEnvEditor: function(target) {
    this.externalApiEnvApplyTarget = target || "voice";
    if (this.externalApiEnvApplyTarget === "text") {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
    }
    let path = this._ensureExternalApiEnvFile();
    if (!path) {
      return;
    }
    if (this._applyExternalApiEnvFile(false)) {
      this._applyExternalApiEnvTarget(this.externalApiEnvApplyTarget);
    }
    this._watchExternalApiEnvFile(path);
    this._openFile(path, _("Opened External API .env"));
  },

  _applyExternalApiEnvTarget: function(target) {
    if (target === "text") {
      let previousBackend = this.postProcessBackend;
      try {
        let result = this.settings.setValue("post-process-backend", "openai-compatible");
        if (result === false) {
          throw new Error("External API text backend setting could not be saved");
        }
      } catch (err) {
        this.postProcessBackend = previousBackend;
        this._safeLogError(err);
        this._setStatusPreservingRecording("error", _("External API text backend could not be selected"), this.lastTranscript);
        return false;
      }
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
      this.postProcessBackend = "openai-compatible";
      this._refreshTextModelMenuForBackend("openai-compatible");
      this._setStatusPreservingRecording("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);
      return true;
    }
    return this._selectExternalApiVoiceBackend();
  },

  _selectExternalApiVoiceBackend: function() {
    if (this.voiceModelActionToken) {
      return;
    }
    if (!this._commitVoiceBackendSettings(
      "openai-compatible",
      "",
      "external-api-voice",
      _("External API voice backend could not be selected")
    )) {
      return false;
    }
    this._refreshModelMenu();
    let model = String(this.openaiCompatibleModel || "").trim();
    if (model === "") {
      this._setStatusPreservingRecording("error", _("External API speech model is not configured"), this.lastTranscript);
      return true;
    }
    this._setStatusPreservingRecording("ready", _("Voice model: External API ") + model, this.lastTranscript);
    return true;
  },

  _refreshTextModelMenu: function() {
    this._refreshTextModelMenuForBackend("");
  },

  _refreshTextModelMenuForBackend: function(backendOverride) {
    if (!this._canMutateMenu(this.textModelItem)) {
      return;
    }
    if (this.textModelMenuRefreshToken && !backendOverride) {
      return;
    }
    this.textModelMenuRefreshToken = null;
    let textModelArgs = this._tryTextModelsArgs(backendOverride);
    if (!textModelArgs) {
      return;
    }
    let refreshToken = {};
    this.textModelMenuRefreshToken = refreshToken;
    let backend = String(backendOverride || this.postProcessBackend || "");
    let provider = backend === "openai-compatible" ? "openai-compatible" : "ollama";
    let loadingMessage = provider === "openai-compatible"
      ? _("Loading OpenAI-compatible text models...")
      : _("Loading local text models...");
    this._populateTextModelMenu([], loadingMessage, provider);
    this._spawnJson(textModelArgs, (payload) => {
      if (this.textModelMenuRefreshToken !== refreshToken) {
        return;
      }
      this.textModelMenuRefreshToken = null;
      if (!this._canMutateMenu(this.textModelItem)) {
        return;
      }
      if (payload.error) {
        this._populateTextModelMenu([], this._sanitizeErrorMessage(payload.error), provider);
        return;
      }
      let available = payload.available === true;
      let availabilityMessage = available
        ? ""
        : this._payloadMessage(payload, _("Text model backend is unavailable"));
      this._populateTextModelMenu(payload.models || [], availabilityMessage, provider);
    });
  },

  _populateTextModelMenu: function(models, message, provider) {
    if (!this._canMutateMenu(this.textModelItem)) {
      return;
    }
    models = Array.isArray(models) ? models : [];
    models = models.filter((model) => model && typeof model === "object" && typeof model.name === "string" && model.name.trim() !== "");
    let modelListWasTruncated = models.length > MAX_MODEL_MENU_ENTRIES;
    if (modelListWasTruncated) {
      models = models.slice(0, MAX_MODEL_MENU_ENTRIES);
    }
    let messageText = typeof message === "string" ? message.trim() : "";
    messageText = this._uiMessageText(messageText);
    this._clearMenuItems(this.textModelItem.menu);
    let backend = String(this.postProcessBackend || "none");
    let activeProvider = provider === "openai-compatible" || provider === "ollama"
      ? provider
      : (backend === "openai-compatible" ? "openai-compatible" : "ollama");
    let selectedOllamaModel = String(this.ollamaModel || "").trim();

    if (modelListWasTruncated && activeProvider === "ollama" && backend === "ollama" && selectedOllamaModel !== "") {
      let selectedModelListed = false;
      for (let model of models) {
        if (model && typeof model.name === "string" && model.name.trim() === selectedOllamaModel) {
          selectedModelListed = true;
          break;
        }
      }
      if (!selectedModelListed && models.length > 0) {
        models[models.length - 1] = {
          name: selectedOllamaModel,
          model: selectedOllamaModel,
          description: _("selected")
        };
      }
    }

    let disabled = this._selectionMenuItem((backend === "none" ? "[x] " : "[ ] ") + _("Disabled"));
    this._connectSafe(disabled, "activate", () => this._selectTextModelBackend("none", "", _("Text polishing disabled")));
    this.textModelItem.menu.addMenuItem(disabled);

    let textCommandConfigured = String(this.postProcessCommand || "").trim() !== "";
    let customLabel = _("Custom command") + (textCommandConfigured ? "" : _(" - configure in settings"));
    let custom = this._selectionMenuItem((backend === "command" || backend === "custom" ? "[x] " : "[ ] ") + customLabel);
    this._connectSafe(custom, "activate", () => {
      if (textCommandConfigured) {
        this._selectTextModelBackend("command", "", _("Text polishing: custom command"));
        return;
      }
      this._openAppletSettings();
      this._setStatusPreservingRecording("ready", _("Configure custom text command in applet settings"), this.lastTranscript);
    });
    this.textModelItem.menu.addMenuItem(custom);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let ollama = this._selectionMenuItem((backend === "ollama" ? "[x] " : "[ ] ") + _("Ollama local model"));
    this._connectSafe(ollama, "activate", () => this._activateOllamaTextModelFlow());
    this.textModelItem.menu.addMenuItem(ollama);

    let openaiCompatible = this._selectionMenuItem((backend === "openai-compatible" ? "[x] " : "[ ] ") + _("OpenAI-compatible API"));
    this._connectSafe(openaiCompatible, "activate", () => this._openExternalApiEnvEditor("text"));
    this.textModelItem.menu.addMenuItem(openaiCompatible);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let presetMenu = new PopupMenu.PopupSubMenuMenuItem(_("Polishing preset: ") + this._textPolishingPresetLabel(this.postProcessPreset));
    this._styleMenuItemLabel(presetMenu);
    this._styleSelectionSubmenu(presetMenu);
    this.textModelItem.menu.addMenuItem(presetMenu);
    this._populateTextPolishingPresetMenu(presetMenu.menu);

    let safetyMenu = new PopupMenu.PopupSubMenuMenuItem(_("Polishing safety"));
    this._styleMenuItemLabel(safetyMenu);
    this._styleSelectionSubmenu(safetyMenu);
    this.textModelItem.menu.addMenuItem(safetyMenu);
    this._populateTextPolishingSafetyMenu(safetyMenu.menu);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let reset = this._selectionMenuItem(_("Reset polishing defaults"));
    this._connectSafe(reset, "activate", () => this._resetTextPolishingDefaults());
    this.textModelItem.menu.addMenuItem(reset);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (messageText !== "") {
      this.textModelItem.menu.addMenuItem(this._selectionInfoItem(messageText));
      return;
    }
    if (!models || models.length === 0) {
      if (activeProvider === "ollama" && backend === "ollama" && selectedOllamaModel !== "") {
        this._addTextModelMenuEntry({
          name: selectedOllamaModel,
          model: selectedOllamaModel,
          description: _("selected")
        }, "ollama");
        this.textModelItem.menu.addMenuItem(this._selectionInfoItem(_("Model list is temporarily empty; using selected Ollama model")));
        return;
      }
      let emptyLabel = activeProvider === "openai-compatible"
        ? _("No OpenAI-compatible text models found")
        : _("No local Ollama models found");
      this.textModelItem.menu.addMenuItem(this._selectionInfoItem(emptyLabel));
      return;
    }
    for (let model of models) {
      if (!model || typeof model !== "object") {
        continue;
      }
      this._addTextModelMenuEntry(model, activeProvider);
    }
    if (modelListWasTruncated) {
      this.textModelItem.menu.addMenuItem(this._selectionInfoItem(_("Model list truncated for safety")));
    }
  },

  _canMutateMenu: function(item) {
    return Boolean(
      !this.appletRemoved &&
      item &&
      item.menu &&
      typeof item.menu.removeAll === "function" &&
      typeof item.menu.addMenuItem === "function"
    );
  },

  _addTextModelMenuEntry: function(model, backend) {
    if (!model || typeof model !== "object") {
      return;
    }
    let name = typeof model.name === "string" ? model.name.trim() : "";
    if (name === "") {
      return;
    }
    let provider = backend === "openai-compatible" || backend === "ollama" ? backend : "";
    if (provider === "") {
      return;
    }
    let currentModel = provider === "openai-compatible" ? String(this.openaiCompatibleTextModel || this.openaiCompatibleModel || "") : String(this.ollamaModel || "");
    let current = String(this.postProcessBackend || "") === provider && currentModel === name;
    let detailsValue = typeof model.description === "string"
      ? model.description
      : (typeof model.size_label === "string" ? model.size_label : "");
    let details = detailsValue.trim();
    let label = (current ? "[x] " : "[ ] ") + name;
    if (details !== "") {
      label += " (" + details + ")";
    }
    let item = this._selectionMenuItem(this._shortMenuText(label, 96));
    this._connectSafe(item, "activate", () => this._selectTextModelBackend(provider, name, _("Text model: ") + name));
    this.textModelItem.menu.addMenuItem(item);
  },

  _textPolishingPresetLabel: function(preset) {
    let value = this._normalizeTextPolishingPreset(preset);
    if (value === "clean") return _("Clean natural text");
    if (value === "code") return _("Preserve commands and code");
    if (value === "chat") return _("Short chat message");
    if (value === "email") return _("Polite email");
    if (value === "safety") return _("Sensitive-data masking");
    if (value === "custom") return _("Custom instruction only");
    return _("Safe default: minimal corrections");
  },

  _populateTextPolishingPresetMenu: function(parentMenu) {
    let current = this._normalizeTextPolishingPreset(this.postProcessPreset);
    for (let preset of TEXT_POLISHING_PRESETS) {
      let item = this._selectionMenuItem((current === preset ? "[x] " : "[ ] ") + this._textPolishingPresetLabel(preset));
      this._connectSafe(item, "activate", () => this._selectTextPolishingPreset(preset));
      parentMenu.addMenuItem(item);
    }
  },

  _selectTextPolishingPreset: function(preset) {
    let nextPreset = this._normalizeTextPolishingPreset(preset);
    if (!this._commitSettingValue("postProcessPreset", "post-process-preset", nextPreset, "settings-text-polishing", _("Polishing preset could not be saved"))) {
      return;
    }
    this._refreshTextModelMenu();
    this._setStatusPreservingRecording("ready", _("Polishing preset: ") + this._textPolishingPresetLabel(this.postProcessPreset), this.lastTranscript);
  },

  _populateTextPolishingSafetyMenu: function(parentMenu) {
    let preserveCode = this._selectionMenuItem(this._optionLabel(Boolean(this.postProcessPreserveCode), _("Preserve commands and code")));
    this._connectSafe(preserveCode, "activate", () => this._toggleTextPolishingSafetyFlag("post-process-preserve-code", "postProcessPreserveCode", _("Preserve commands and code")));
    parentMenu.addMenuItem(preserveCode);

    let neverAddContent = this._selectionMenuItem(this._optionLabel(Boolean(this.postProcessNeverAddContent), _("Never add content")));
    this._connectSafe(neverAddContent, "activate", () => this._toggleTextPolishingSafetyFlag("post-process-never-add-content", "postProcessNeverAddContent", _("Never add content")));
    parentMenu.addMenuItem(neverAddContent);

    let maskSensitiveData = this._selectionMenuItem(this._optionLabel(Boolean(this.postProcessMaskSensitiveData), _("Mask sensitive data")));
    this._connectSafe(maskSensitiveData, "activate", () => this._toggleTextPolishingSafetyFlag("post-process-mask-sensitive-data", "postProcessMaskSensitiveData", _("Mask sensitive data")));
    parentMenu.addMenuItem(maskSensitiveData);
  },

  _toggleTextPolishingSafetyFlag: function(settingKey, propertyName, label) {
    let nextValue = !Boolean(this[propertyName]);
    if (!this._commitSettingValue(propertyName, settingKey, nextValue, "settings-text-polishing", label + _(" could not be saved"))) {
      return;
    }
    this._refreshTextModelMenu();
    this._setStatusPreservingRecording("ready", label + ": " + (this[propertyName] ? _("enabled") : _("disabled")), this.lastTranscript);
  },

  _selectTextModelBackend: function(backend, model, message) {
    this._cancelOllamaInstallWatch();
    this._clearOllamaModelFlow();
    let safeModel;
    try {
      safeModel = this._coerceCliTextArg(model === undefined || model === null ? "" : model, "text model");
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Text model is invalid: ") + safeError, this.lastTranscript);
      return false;
    }
    this.postProcessBackend = String(backend || "none");
    this.settings.setValue("post-process-backend", this.postProcessBackend);
    if (this.postProcessBackend === "ollama") {
      this.ollamaModel = safeModel;
      this.settings.setValue("ollama-model", this.ollamaModel);
    }
    if (this.postProcessBackend === "openai-compatible") {
      this.openaiCompatibleTextModel = safeModel;
      this.settings.setValue("openai-compatible-text-model", this.openaiCompatibleTextModel);
      if (!this._writeExternalApiEnvFile()) {
        this._refreshTextModelMenu();
        return false;
      }
    }
    this._refreshTextModelMenu();
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
    return true;
  },

  _clearOllamaModelFlow: function(flowToken) {
    if (flowToken && this.ollamaModelFlowToken !== flowToken) {
      return false;
    }
    let hadOllamaModelInstall = Boolean(this.ollamaModelInstallRunning);
    this.ollamaModelFlowToken = null;
    this._terminateProcessesByGroup("ollama", true);
    this.ollamaModelInstallRunning = false;
    if (hadOllamaModelInstall) {
      this.isCommandRunning = false;
    }
    return true;
  },

  _cancelOllamaFlowForRecording: function() {
    if (!this.ollamaModelFlowToken && !this.ollamaInstallWatchToken && !this.ollamaModelInstallRunning) {
      return false;
    }
    this._cancelOllamaInstallWatch();
    this._clearOllamaModelFlow();
    this._terminateProcessesByGroup("ollama");
    return true;
  },

  _activateOllamaTextModelFlow: function() {
    if (this._hasActiveRecordingState()) {
      return;
    }
    if (this.ollamaModelFlowToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
      this._setStatus("error", _("Install zenity to choose an Ollama model"), this.lastTranscript);
      return;
    }
    let textModelArgs = this._tryTextModelsArgs("ollama");
    if (!textModelArgs) {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
      return;
    }
    this._cancelOllamaInstallWatch();
    let flowToken = {};
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Checking Ollama..."), this.lastTranscript);
    this._spawnJson(textModelArgs, (payload) => {
      if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        let safeError = this._sanitizeErrorMessage(payload.error);
        this._clearOllamaModelFlow(flowToken);
        this._setStatus("error", safeError, this.lastTranscript);
        this._notify(_("Could not check Ollama"), safeError, true);
        return;
      }
      let models = Array.isArray(payload.models) ? payload.models : [];
      if (payload.available !== true) {
        let safeMessage = payload.available === false
          ? this._payloadMessage(payload, _("Ollama is not installed or not reachable"))
          : _("Ollama is not installed or not reachable");
        this._setStatus("processing", safeMessage + "; " + _("opening installer..."), this.lastTranscript);
        this._installOllamaRuntime(true);
        return;
      }
      if (models.length === 0) {
        this._promptInstallOllamaTextModel(flowToken);
        return;
      }
      this._promptChooseOllamaTextModel(models, flowToken);
    }, { resourceGroup: "ollama" });
  },

  _ollamaModelPromptArgs: function() {
    return [
      "zenity",
      "--entry",
      "--title=Install Ollama text model",
      "--text=Ollama model name",
      "--entry-text=llama3.2:3b"
    ];
  },

  _ollamaModelChoiceArgs: function(models) {
    let args = [
      "zenity",
      "--list",
      "--title=Choose Ollama text model",
      "--text=Choose an installed Ollama model or add another one",
      "--column=Action",
      "--column=Model",
      "--hide-column=1",
      "--print-column=1",
      "--width=640",
      "--height=360",
      "ADD",
      _("Add another model...")
    ];
    let maxModelChoices = Math.min(MAX_MODEL_MENU_ENTRIES, Math.floor((MAX_CLI_ARG_COUNT - args.length) / 2));
    let argumentBytes = 0;
    for (let argument of args) {
      argumentBytes += utf8ByteLength(argument);
    }
    let modelCount = 0;
    let listWasTruncated = false;
    for (let model of (Array.isArray(models) ? models : [])) {
      if (!model || typeof model !== "object") {
        continue;
      }
      if (typeof model.name !== "string") {
        continue;
      }
      let name;
      try {
        name = this._coerceCliTextArg(model.name.trim(), "ollama model");
      } catch (err) {
        this._safeLogError(err);
        continue;
      }
      if (name.trim() === "") {
        continue;
      }
      if (modelCount >= maxModelChoices) {
        listWasTruncated = true;
        continue;
      }
      let selection = "SELECT:" + name;
      if (utf8ByteLength(selection) > MAX_CLI_ARG_BYTES) {
        listWasTruncated = true;
        continue;
      }
      let details = "";
      try {
        let detailsValue = typeof model.description === "string"
          ? model.description
          : (typeof model.size_label === "string" ? model.size_label : "");
        details = this._coerceCliTextArg(detailsValue.trim(), "ollama model details").trim();
      } catch (err) {
        this._safeLogError(err);
      }
      let label = details ? name + " (" + details + ")" : name;
      if (utf8ByteLength(label) > MAX_CLI_ARG_BYTES) {
        label = name;
      }
      if (utf8ByteLength(label) > MAX_CLI_ARG_BYTES) {
        listWasTruncated = true;
        continue;
      }
      let additionBytes = utf8ByteLength(selection) + utf8ByteLength(label);
      if (argumentBytes + additionBytes > MAX_CLI_COMMAND_BYTES - MAX_CLI_ARG_BYTES) {
        listWasTruncated = true;
        continue;
      }
      args.push("SELECT:" + name, label);
      argumentBytes += additionBytes;
      modelCount++;
    }
    if (listWasTruncated) {
      args[3] = "--text=" + _("Choose an installed Ollama model or add another one") + " (" + _("model list truncated for safety") + ")";
    }
    return args;
  },

  _chooseOllamaTextModel: function() {
    if (this._hasActiveRecordingState()) {
      return;
    }
    if (this.ollamaModelFlowToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
      this._setStatus("error", _("Install zenity to choose an Ollama model"), this.lastTranscript);
      return;
    }
    let textModelArgs = this._tryTextModelsArgs("ollama");
    if (!textModelArgs) {
      this._cancelOllamaInstallWatch();
      this._clearOllamaModelFlow();
      return;
    }
    this._cancelOllamaInstallWatch();
    let flowToken = {};
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Loading Ollama text models..."), this.lastTranscript);
    this._spawnJson(textModelArgs, (payload) => {
      if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        let safeError = this._sanitizeErrorMessage(payload.error);
        this._clearOllamaModelFlow(flowToken);
        this._setStatus("error", safeError, this.lastTranscript);
        this._notify(_("Could not load Ollama models"), safeError, true);
        return;
      }
      if (payload.available !== true) {
        let safeMessage = payload.available === false
          ? this._payloadMessage(payload, _("Ollama is not installed or not reachable"))
          : _("Ollama is not installed or not reachable");
        this._setStatus("processing", safeMessage + "; " + _("opening installer..."), this.lastTranscript);
        this._installOllamaRuntime(true);
        return;
      }
      let models = Array.isArray(payload.models) ? payload.models : [];
      if (models.length === 0) {
        this._promptInstallOllamaTextModel(flowToken);
        return;
      }
      this._promptChooseOllamaTextModel(models, flowToken);
    }, { resourceGroup: "ollama" });
  },

  _promptChooseOllamaTextModel: function(models, flowToken) {
    flowToken = flowToken || this.ollamaModelFlowToken || {};
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Choose Ollama text model..."), this.lastTranscript);
    let choiceArgs;
    try {
      choiceArgs = this._ollamaModelChoiceArgs(models);
    } catch (error) {
      this._clearOllamaModelFlow(flowToken);
      this._recordLifecycleError("ollama-flow", error);
      this._setStatus("error", _("Could not prepare Ollama model selection"), this.lastTranscript);
      return;
    }
    this._spawnText(choiceArgs, (output) => {
      if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      let finish = (message) => {
        this._clearOllamaModelFlow(flowToken);
        this._setStatus("ready", message, this.lastTranscript);
      };
      let choice = String(output || "").trim();
      if (choice === "") {
        finish(_("Ollama model selection cancelled"));
        return;
      }
      if (choice === "ADD") {
        this._promptInstallOllamaTextModel(flowToken);
        return;
      }
      if (choice.indexOf("SELECT:") === 0) {
        let model = choice.slice("SELECT:".length).trim();
        let knownModel = false;
        for (let candidate of (Array.isArray(models) ? models : [])) {
          if (!candidate || typeof candidate !== "object" || typeof candidate.name !== "string") {
            continue;
          }
          try {
            if (this._coerceCliTextArg(candidate.name.trim(), "ollama model").trim() === model) {
              knownModel = true;
              break;
            }
          } catch (err) {
            continue;
          }
        }
        if (model !== "" && knownModel) {
          this._clearOllamaModelFlow(flowToken);
          this._selectTextModelBackend("ollama", model, _("Text model: ") + model);
          return;
        }
      }
      finish(_("Ollama model selection was invalid"));
    }, { timeoutMs: 0, resourceGroup: "ollama" });
  },

  _promptInstallOllamaTextModel: function(flowToken) {
    flowToken = flowToken || this.ollamaModelFlowToken || {};
    let zenity;
    try {
      zenity = this._findTrustedProgramInPath("zenity");
    } catch (error) {
      this._clearOllamaModelFlow(flowToken);
      this._recordLifecycleError("ollama-flow", error);
      this._setStatusPreservingRecording("error", _("Could not prepare Ollama model prompt"), this.lastTranscript);
      return;
    }
    if (!zenity) {
      this._clearOllamaModelFlow(flowToken);
      this._setStatus("error", _("Install zenity to enter an Ollama model name"), this.lastTranscript);
      return;
    }
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Choose Ollama text model..."), this.lastTranscript);
    let promptArgs;
    try {
      promptArgs = this._ollamaModelPromptArgs();
    } catch (error) {
      this._clearOllamaModelFlow(flowToken);
      this._recordLifecycleError("ollama-flow", error);
      this._setStatus("error", _("Could not prepare Ollama model prompt"), this.lastTranscript);
      return;
    }
    this._spawnText(promptArgs, (output) => {
      if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      let model = String(output || "").trim();
      if (model === "") {
        this._clearOllamaModelFlow(flowToken);
        this._setStatus("ready", _("Ollama model installation cancelled"), this.lastTranscript);
        return;
      }
      this._installOllamaTextModel(model);
    }, { timeoutMs: 0, resourceGroup: "ollama" });
  },

  _installOllamaTextModel: function(model) {
    let flowToken = this.ollamaModelFlowToken;
    if (this._hasActiveRecordingState()) {
      this._clearOllamaModelFlow(flowToken);
      return;
    }
    if (this.isCommandRunning) {
      this._clearOllamaModelFlow(flowToken);
      this._setStatus("error", _("Another command is already running"), this.lastTranscript);
      return;
    }
    let installArgs;
    try {
      installArgs = this._installTextModelArgs(model);
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._clearOllamaModelFlow(flowToken);
      this._setStatus("error", _("Could not prepare Ollama model installation: ") + safeError, this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this.ollamaModelInstallRunning = true;
    this._setStatus("processing", _("Installing Ollama model: ") + model, this.lastTranscript);
    this._spawnJson(installArgs, (payload) => {
      this.isCommandRunning = false;
      this.ollamaModelInstallRunning = false;
      if (!flowToken || this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        let safeError = this._sanitizeErrorMessage(payload.error);
        this._clearOllamaModelFlow(flowToken);
        this._setStatus("error", safeError, this.lastTranscript);
        this._notify(_("Ollama model installation failed"), safeError, true);
        this._refreshTextModelMenu();
        return;
      }
      let installedModel = payload && typeof payload.model === "string" && payload.model.trim() !== ""
        ? payload.model.trim()
        : String(model || "").trim();
      if (installedModel === "") {
        this._clearOllamaModelFlow(flowToken);
        this._setStatus("error", _("Ollama installation returned no model name"), this.lastTranscript);
        this._refreshTextModelMenu();
        return;
      }
      let message = _("Ollama model installed: ") + installedModel;
      this._clearOllamaModelFlow(flowToken);
      if (!this._selectTextModelBackend("ollama", installedModel, message)) {
        this._refreshTextModelMenu();
        return;
      }
      this._notify(_("Ollama model installed"), installedModel, false);
    }, { timeoutMs: BENCHMARK_COMMAND_TIMEOUT_MS, resourceGroup: "ollama" });
  },

  _refreshHistory: function() {
    if (!this._canMutateMenu(this.historyItem)) {
      return;
    }
    if (this.historyRefreshToken) {
      return;
    }
    let refreshToken = {};
    this.historyRefreshToken = refreshToken;
    let historyArgs;
    try {
      historyArgs = this._historyArgs();
    } catch (error) {
      if (this.historyRefreshToken === refreshToken) {
        this.historyRefreshToken = null;
      }
      this._recordLifecycleError("history-refresh", error);
      this._populateHistoryMenu([]);
      this._setStatusPreservingRecording("error", _("Could not prepare transcript history"), this.lastTranscript);
      return;
    }
    this._spawnJson(historyArgs, (payload) => {
      if (this.historyRefreshToken !== refreshToken) {
        return;
      }
      this.historyRefreshToken = null;
      if (!this._canMutateMenu(this.historyItem)) {
        return;
      }
      if (payload.error) {
        this._populateHistoryMenu([]);
        this._setStatusPreservingRecording("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      this._populateHistoryMenu(payload.transcripts || []);
    });
  },

  _listAllTranscripts: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this.transcriptListPromptToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      let message = _("Install zenity to show the transcript list without writing a plaintext file.");
      this._setStatusPreservingRecording("error", message, this.lastTranscript);
      this._notify(_("Speed of Cinnamon"), message, true);
      return;
    }
    this._confirmPlaintextTranscriptList(function(confirmed) {
      if (confirmed) {
        this._loadAllTranscriptsDocument();
      }
    }.bind(this));
  },

  _confirmPlaintextTranscriptList: function(completionCallback) {
    if (this.transcriptListPromptToken) {
      return;
    }
    let promptToken = {};
    this.transcriptListPromptToken = promptToken;
    let dialog = this._newSafeDialog("transcript-list");
    let completed = false;
    let complete = (result) => {
      if (completed) {
        return;
      }
      completed = true;
      if (this.transcriptListPromptToken === promptToken) {
        this.transcriptListPromptToken = null;
      }
      if (typeof completionCallback === "function") {
        completionCallback(result === true);
      }
    };
    if (!dialog || !this._dialogAddChild(dialog, this._newSafeLabel(_("List all transcripts?"), { x_expand: true }, "transcript-list"), "transcript-list") ||
      !this._dialogAddChild(dialog, this._newSafeLabel(
      _("This shows complete transcript contents in a plaintext window. Continue only if your screen and session are trusted."),
      { x_expand: true },
      "transcript-list"
    ), "transcript-list")) {
      this._dialogClose(dialog, "transcript-list");
      this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);
      complete(false);
      return;
    }
    if (!this._dialogSetButtons(dialog, [
      {
        label: _("Cancel"),
        key: Clutter.KEY_Escape,
        action: function() {
          try {
            this._dialogClose(dialog, "transcript-list");
            if (this.transcriptListPromptToken === promptToken) {
              this._setStatusPreservingRecording("ready", _("Transcript list cancelled"), this.lastTranscript);
            }
          } finally {
            complete(false);
          }
        }.bind(this),
      },
      {
        label: _("Show transcripts"),
        action: function() {
          this._dialogClose(dialog, "transcript-list");
          complete(true);
        }.bind(this),
      }
    ], "transcript-list")) {
      this._dialogClose(dialog, "transcript-list");
      this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);
      complete(false);
      return;
    }
    if (!this._dialogOpen(dialog, "transcript-list")) {
      this._dialogClose(dialog, "transcript-list");
      this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);
      this._notify(_("Speed of Cinnamon"), _("Transcript list confirmation could not be opened"), true);
      complete(false);
    }
  },

  _loadAllTranscriptsDocument: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState()) {
      return;
    }
    let historyDocumentArgs;
    try {
      historyDocumentArgs = this._allHistoryArgs();
    } catch (error) {
      this._setStatus("error", _("Could not prepare transcript list"), this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Preparing transcript list..."), this.lastTranscript);
    this._spawnJson(historyDocumentArgs, (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      let content = typeof payload.content === "string" ? payload.content : "";
      if (content.trim() === "") {
        this._setStatus("error", _("Transcript list is empty"), this.lastTranscript);
        return;
      }
      this._showTranscriptsWindow(content, this._safePayloadCount(payload.transcripts), payload.truncated === true);
    });
  },

  _showTranscriptsWindow: function(content, count, truncated) {
    let windowToken = {};
    this.transcriptWindowToken = windowToken;
    let isCurrentWindow = () => this.transcriptWindowToken === windowToken && this._lifecycleAllowsWork();
    let releaseWindow = () => {
      if (this.transcriptWindowToken !== windowToken) {
        return false;
      }
      this.transcriptWindowToken = null;
      return true;
    };
    let zenity;
    try {
      zenity = this._findTrustedProgramInPath("zenity");
    } catch (error) {
      releaseWindow();
      this._recordLifecycleError("transcript-window", error);
      let message = _("Could not prepare transcript list window");
      this._setStatusPreservingRecording("error", message, this.lastTranscript);
      this._notify(_("Could not open transcript list"), message, true);
      return;
    }
    if (!zenity) {
      releaseWindow();
      let message = _("Install zenity to show the transcript list without writing a plaintext file.");
      this._setStatusPreservingRecording("error", message, this.lastTranscript);
      this._notify(_("Speed of Cinnamon"), message, true);
      return;
    }
    let args = [
      zenity,
      "--text-info",
      "--title=Speed of Cinnamon transcripts",
      "--width=900",
      "--height=700",
    ];
    try {
      let handle = this._runBoundedSubprocess(args, {}, {
        inputText: String(content || ""),
        timeoutMs: 0,
        maxStdoutBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
      }, (stdout, stderr, result) => {
        if (!isCurrentWindow()) {
          return;
        }
        releaseWindow();
        if (result && result.error && !result.cancelled) {
          this._setStatusPreservingRecording("error", _("Transcript list window closed unexpectedly"), this.lastTranscript);
        }
      });
      if (!handle) {
        throw new Error("Transcript list process could not be started");
      }
      let message = _("Opened transcript list: ") + String(count);
      if (truncated) {
        message += _(" (truncated)");
        this._notify(
          _("Transcript list truncated"),
          _("Only the newest transcripts that fit the secure display limit were shown."),
          false
        );
      }
      this._setStatusPreservingRecording("done", message, this.lastTranscript);
    } catch (err) {
      if (!isCurrentWindow()) {
        return;
      }
      releaseWindow();
      let safeError = this._sanitizeErrorMessage(String(err && err.message ? err.message : err));
      this._setStatusPreservingRecording("error", _("Could not open transcript list: ") + safeError, this.lastTranscript);
      this._notify(_("Could not open transcript list"), safeError, true);
    }
  },

  _exportAllTranscripts: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState()) {
      return;
    }
    let exportArgs;
    try {
      exportArgs = this._transcriptsExportArgs();
    } catch (error) {
      this._setStatus("error", _("Could not prepare transcript export"), this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Exporting transcripts..."), this.lastTranscript);
    this._spawnJson(exportArgs, (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        this._maybeWarnRejectedArtifactPassphrase(payload.error);
        return;
      }
      let path = typeof payload.path === "string" ? payload.path.trim() : "";
      if (path === "") {
        this._setStatus("error", _("Transcript export path is empty"), this.lastTranscript);
        return;
      }
      let encryptionMode = typeof payload.encryption === "string" ? payload.encryption.trim() : "";
      let encryptedMode = encryptionMode === "keyring" || encryptionMode === "passphrase";
      if (payload.encrypted !== true || payload.plaintext !== false || !encryptedMode) {
        let message = _("Transcript export was not encrypted");
        this._setStatus("error", message, this.lastTranscript);
        this._notify(_("Speed of Cinnamon transcript export"), message, true);
        return;
      }
      let message = _("Exported encrypted transcript bundle");
      this._setStatus("done", message, this.lastTranscript);
      this._notify(_("Speed of Cinnamon transcript export"), message, false);
      this._openFolder(GLib.path_get_dirname(path), _("Opened transcript export folder"));
    });
  },

  _safePayloadCount: function(value) {
    let count = typeof value === "number" ? value : NaN;
    if (!isFinite(count) || count < 0) {
      return 0;
    }
    return Math.floor(count);
  },

  _cleanupCount: function(payload, dryRun) {
    payload = payload && typeof payload === "object" ? payload : {};
    if (dryRun) {
      return this._safePayloadCount(payload.would_delete_transcripts) + this._safePayloadCount(payload.would_delete_recordings) + this._safePayloadCount(payload.would_delete_logs);
    }
    return this._safePayloadCount(payload.deleted_transcripts) + this._safePayloadCount(payload.deleted_recordings) + this._safePayloadCount(payload.deleted_logs);
  },

  _cleanupPreviewText: function(payload) {
    payload = payload && typeof payload === "object" ? payload : {};
    let plannedPaths = Array.isArray(payload.would_delete_paths)
      ? payload.would_delete_paths.filter((path) => typeof path === "string" && path.trim() !== "")
      : [];
    let failedPaths = Array.isArray(payload.failed_paths)
      ? payload.failed_paths.filter((path) => typeof path === "string" && path.trim() !== "")
      : [];
    let skippedPaths = Array.isArray(payload.skipped_active_paths)
      ? payload.skipped_active_paths.filter((path) => typeof path === "string" && path.trim() !== "")
      : [];
    let lines = [
      _("Clean all old files preview"),
      "",
      _("Files that would be deleted: ") + String(this._cleanupCount(payload, true)),
      _("Transcripts: ") + String(this._safePayloadCount(payload.would_delete_transcripts)),
      _("Recordings: ") + String(this._safePayloadCount(payload.would_delete_recordings)),
      _("Logs: ") + String(this._safePayloadCount(payload.would_delete_logs))
    ];
    let hiddenPathCount = this._safePayloadCount(payload.would_delete_path_count) + this._safePayloadCount(payload.failed_path_count) + this._safePayloadCount(payload.skipped_active_path_count);
    if (hiddenPathCount > 0 && plannedPaths.length === 0 && failedPaths.length === 0 && skippedPaths.length === 0) {
      lines.push("");
      lines.push(_("File paths are hidden for privacy; counts are shown instead."));
    }
    let addPaths = (title, paths) => {
      if (paths.length === 0) {
        return;
      }
      lines.push("");
      lines.push(title);
      let limit = Math.min(paths.length, 12);
      for (let i = 0; i < limit; i++) {
        lines.push("- " + this._shortMenuText(String(paths[i] || ""), 140));
      }
      if (paths.length > limit) {
        lines.push("... +" + String(paths.length - limit));
      }
    };
    addPaths(_("Planned files:"), plannedPaths);
    addPaths(_("Skipped active files:"), skippedPaths);
    addPaths(_("Failed files:"), failedPaths);
    return lines.join("\n");
  },

  _showCleanupPreviewDialog: function(payload) {
    let dialog = this._newSafeDialog("cleanup-preview");
    if (!dialog || !this._dialogAddChild(dialog, this._newSafeLabel(this._cleanupPreviewText(payload), { x_expand: true }, "cleanup-preview"), "cleanup-preview") ||
      !this._dialogSetButtons(dialog, [
      {
        label: _("Close"),
        key: Clutter.KEY_Escape,
        action: function() {
          this._dialogClose(dialog, "cleanup-preview");
        }.bind(this),
      }
    ], "cleanup-preview")) {
      this._dialogClose(dialog, "cleanup-preview");
      this._notify(_("Speed of Cinnamon"), _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), false);
      return;
    }
    if (!this._dialogOpen(dialog, "cleanup-preview")) {
      this._dialogClose(dialog, "cleanup-preview");
      this._notify(_("Speed of Cinnamon"), _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), false);
    }
  },

  _normalizeTextPolishingPreset: function(value) {
    let key = String(value || "").trim();
    if (Object.prototype.hasOwnProperty.call(TEXT_POLISHING_PRESET_INSTRUCTIONS, key)) {
      return key;
    }
    return TEXT_POLISHING_SAFE_PRESET;
  },

  _singleLineCliTextValue: function(value) {
    let text = typeof value === "string" ? value : "";
    return text
      .replace(NUL_RE, "")
      .replace(/\\u000d|\\u000a|\\r|\\n/gi, " ")
      .replace(/[\u0001-\u001f\u007f]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  },

  _effectivePostProcessPrompt: function() {
    let parts = [];
    let preset = this._normalizeTextPolishingPreset(this.postProcessPreset);
    let presetInstruction = TEXT_POLISHING_PRESET_INSTRUCTIONS[preset] || "";
    if (presetInstruction !== "") {
      parts.push("Preset instruction: " + presetInstruction);
    }
    let customInstruction = typeof this.postProcessPrompt === "string" ? this.postProcessPrompt.trim() : "";
    if (customInstruction !== "") {
      parts.push("Custom instruction: " + customInstruction);
    }
    if (Boolean(this.postProcessPreserveCode)) {
      parts.push("Preserve commands, code, paths, filenames, flags, variable names, identifiers, and quoted text exactly unless the user explicitly asks for rewriting.");
    }
    if (Boolean(this.postProcessNeverAddContent)) {
      parts.push("Do not add facts, explanations, headings, or extra content that was not dictated or explicitly requested. If greetings, thanks, apologies, politeness markers, hedging, softeners, emojis, emoticons, or sign-offs were dictated, keep them.");
    }
    if (Boolean(this.postProcessMaskSensitiveData)) {
      parts.push("Mask sensitive data such as tokens, passwords, account data, phone numbers, addresses, and private names before returning the final text.");
    }
    return this._singleLineCliTextValue(parts.join(" "));
  },

  _resetTextPolishingDefaults: function() {
    this.postProcessPreset = TEXT_POLISHING_SAFE_PRESET;
    this.postProcessPrompt = "";
    this.postProcessPreserveCode = true;
    this.postProcessNeverAddContent = true;
    this.postProcessMaskSensitiveData = false;
    this.settings.setValue("post-process-preset", this.postProcessPreset);
    this.settings.setValue("post-process-prompt", this.postProcessPrompt);
    this.settings.setValue("post-process-preserve-code", this.postProcessPreserveCode);
    this.settings.setValue("post-process-never-add-content", this.postProcessNeverAddContent);
    this.settings.setValue("post-process-mask-sensitive-data", this.postProcessMaskSensitiveData);
    this._refreshTextModelMenu();
    this._setStatusPreservingRecording("ready", _("Text polishing defaults restored"), this.lastTranscript);
  },

  _previewCleanup: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState()) {
      return;
    }
    let cleanupPreviewArgs;
    try {
      cleanupPreviewArgs = this._cleanupPreviewArgs();
    } catch (error) {
      this._setStatus("error", _("Could not prepare cleanup preview"), this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Previewing cleanup..."), this.lastTranscript);
    this._spawnJson(cleanupPreviewArgs, (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      this._setStatus("ready", _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), this.lastTranscript);
      this._showCleanupPreviewDialog(payload);
    });
  },

  _cleanupOldFiles: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState()) {
      return;
    }
    let cleanupArgs;
    try {
      cleanupArgs = this._cleanupArgs();
    } catch (error) {
      this._setStatus("error", _("Could not prepare cleanup"), this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Cleaning old files..."), this.lastTranscript);
    this._spawnJson(cleanupArgs, (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
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

  _appletLifecycleDiagnostics: function() {
    let recordDiagnosticError = (error) => {
      try {
        this._recordLifecycleError("diagnostics", error);
      } catch (ignored) {
        this._safeLogError(error);
      }
    };
    let registry = {};
    try {
      registry = this._resourceRegistry || {};
    } catch (error) {
      recordDiagnosticError(error);
    }
    let registryValue = (name, fallback) => {
      try {
        let value = registry && registry[name];
        return value === undefined || value === null ? fallback : value;
      } catch (error) {
        recordDiagnosticError(error);
        return fallback;
      }
    };
    let timers = registryValue("timers", {});
    let signals = registryValue("signals", []);
    let hotkeys = registryValue("hotkeys", {});
    let monitors = registryValue("monitors", []);
    let dialogs = registryValue("dialogs", []);
    let processes = registryValue("processes", {});
    let cancellables = registryValue("cancellables", {});
    let countEntries = (value) => {
      try {
        return value && typeof value === "object" ? Object.keys(value).length : 0;
      } catch (error) {
        recordDiagnosticError(error);
        return 0;
      }
    };
    let countArrayEntries = (value) => {
      try {
        return Array.isArray(value) ? value.length : 0;
      } catch (error) {
        recordDiagnosticError(error);
        return 0;
      }
    };
    let errorCounts = {};
    let lifecycleErrorCounts = {};
    try {
      lifecycleErrorCounts = this._lifecycleErrorCounts || {};
    } catch (error) {
      recordDiagnosticError(error);
    }
    try {
      for (let key in lifecycleErrorCounts) {
        try {
          if (Object.prototype.hasOwnProperty.call(lifecycleErrorCounts, key)) {
            let safeKey = String(key || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64);
            let count = Number(lifecycleErrorCounts[key] || 0);
            errorCounts[safeKey || "unknown"] = isFinite(count)
              ? Math.max(0, Math.min(100000, count))
              : 0;
          }
        } catch (error) {
          recordDiagnosticError(error);
        }
      }
    } catch (error) {
      recordDiagnosticError(error);
    }
    let disabledGroups = [];
    let disabledErrorGroups = {};
    try {
      disabledErrorGroups = this._disabledErrorGroups || {};
    } catch (error) {
      recordDiagnosticError(error);
    }
    try {
      for (let key in disabledErrorGroups) {
        try {
          if (Object.prototype.hasOwnProperty.call(disabledErrorGroups, key) && disabledErrorGroups[key]) {
            disabledGroups.push(String(key || "unknown").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64));
          }
        } catch (error) {
          recordDiagnosticError(error);
        }
      }
    } catch (error) {
      recordDiagnosticError(error);
    }
    disabledGroups = disabledGroups.filter((value, index, values) => value && values.indexOf(value) === index).sort().slice(0, 64);
    let processGroups = {};
    try {
      for (let token in processes || {}) {
        try {
          if (!Object.prototype.hasOwnProperty.call(processes, token)) {
            continue;
          }
          let processEntry = processes[token];
          if (!processEntry || typeof processEntry !== "object") {
            continue;
          }
          let group = String(processEntry.group || "process").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64) || "process";
          processGroups[group] = Number(processGroups[group] || 0) + 1;
        } catch (error) {
          recordDiagnosticError(error);
        }
      }
    } catch (error) {
      recordDiagnosticError(error);
    }
    return {
      state: String(this.lifecycleState || LIFECYCLE_INITIALIZING),
      error_counts: errorCounts,
      disabled_groups: disabledGroups,
      resources: {
        timers: countEntries(timers),
        signals: countArrayEntries(signals),
        hotkeys: countEntries(hotkeys),
        monitors: countArrayEntries(monitors),
        dialogs: countArrayEntries(dialogs),
        processes: countEntries(processes),
        cancellables: countEntries(cancellables),
      },
      process_groups: processGroups,
    };
  },

  _settingsSnapshotForCli: function(includeLifecycle) {
    let snapshot = this._settingsSnapshot();
    for (let key in CLI_TEXT_SETTINGS) {
      if (Object.prototype.hasOwnProperty.call(CLI_TEXT_SETTINGS, key) && Object.prototype.hasOwnProperty.call(snapshot, key)) {
        snapshot[key] = this._coerceCliTextArg(snapshot[key], CLI_TEXT_SETTINGS[key]);
      }
    }
    if (includeLifecycle) {
      snapshot["applet-lifecycle"] = this._appletLifecycleDiagnostics();
    }
    return snapshot;
  },

  _settingsSnapshotInputOption: function(includeLifecycle) {
    return { inputText: JSON.stringify(this._settingsSnapshotForCli(Boolean(includeLifecycle))) };
  },

  _settingsSnapshotInputOptionOrNull: function(includeLifecycle, errorStatus) {
    try {
      return this._settingsSnapshotInputOption(Boolean(includeLifecycle));
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatus(errorStatus || "error", _("Could not prepare settings for backend: ") + safeError, this.lastTranscript);
      return null;
    }
  },

  _exportSettings: function() {
    if (this.settingsTransferToken || this._hasActiveRecordingState()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    let transferToken = {};
    this.settingsTransferToken = transferToken;
    this._setStatus("processing", _("Exporting settings..."), this.lastTranscript);
    let exportArgs;
    try {
      exportArgs = this._settingsExportArgs();
    } catch (error) {
      if (this.settingsTransferToken === transferToken) {
        this.settingsTransferToken = null;
      }
      this._recordLifecycleError("settings-transfer", error);
      this._setStatus("error", _("Could not prepare settings export"), this.lastTranscript);
      return;
    }
    this._spawnJson(exportArgs, (payload) => {
      if (this.settingsTransferToken !== transferToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.settingsTransferToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      this.settingsTransferToken = null;
      this._setStatus("done", _("Exported settings"), this.lastTranscript);
    }, inputOption);
  },

  _importSettings: function() {
    if (this.settingsTransferToken || this._hasActiveRecordingState()) {
      return;
    }
    let transferToken = {};
    this.settingsTransferToken = transferToken;
    this._setStatus("processing", _("Importing settings..."), this.lastTranscript);
    let importArgs;
    try {
      importArgs = this._settingsImportArgs();
    } catch (error) {
      if (this.settingsTransferToken === transferToken) {
        this.settingsTransferToken = null;
      }
      this._recordLifecycleError("settings-transfer", error);
      this._setStatus("error", _("Could not prepare settings import"), this.lastTranscript);
      return;
    }
    this._spawnJson(importArgs, (payload) => {
      if (this.settingsTransferToken !== transferToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (payload.error) {
        this.settingsTransferToken = null;
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      this.settingsTransferToken = null;
      try {
        let applied = this._applyImportedSettings(payload.settings || {});
        this._setStatus("done", _("Imported settings: ") + String(applied), this.lastTranscript);
      } catch (err) {
        let safeError = this._sanitizeErrorMessage(err);
        this._setStatus("error", _("Could not apply imported settings: ") + safeError, this.lastTranscript);
      }
    });
  },

  _applyImportedSettings: function(settings) {
    settings = settings && typeof settings === "object" ? settings : {};
    let pending = [];
    for (let item of EXPORTABLE_SETTINGS) {
      let key = item[0];
      let prop = item[1];
      if (!Object.prototype.hasOwnProperty.call(settings, key)) {
        continue;
      }
      pending.push({
        key: key,
        prop: prop,
        value: this._coerceImportedSetting(key, settings[key], this[prop]),
        previous: this[prop],
      });
    }
    let attemptedWrites = [];
    try {
      for (let item of pending) {
        attemptedWrites.push(item);
        let result = this.settings.setValue(item.key, item.value);
        if (result === false) {
          throw new Error("Imported setting could not be saved");
        }
      }
    } catch (err) {
      for (let index = attemptedWrites.length - 1; index >= 0; index--) {
        let item = attemptedWrites[index];
        try {
          let rollbackResult = this.settings.setValue(item.key, item.previous);
          if (rollbackResult === false) {
            throw new Error("Imported setting rollback failed");
          }
        } catch (rollbackErr) {
          this._safeLogError(rollbackErr);
        }
      }
      throw err;
    }
    for (let item of pending) {
      this[item.prop] = item.value;
    }
    this._syncActiveLanguage();
    this.recorder = this._normalizeRecorder(this.recorder);
    this._populateRecorderMenu();
    this.maxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this.typingDelayMs = this._normalizeTypingDelayMs(this.typingDelayMs);
    this.maxTranscriptFiles = this._normalizeTranscriptLimit(this.maxTranscriptFiles);
    this.artifactEncryption = this._normalizeArtifactEncryption(this.artifactEncryption);
    this._populateRecordingLimitMenu();
    this._populateTranscriptStorageMenu();
    this._populateRecordingOptionsMenu();
    this._populateNotificationOptionsMenu();
    this._populateArtifactEncryptionMenu();
    this._updateOpenAiFlexProcessingItem();
    this.insertMethod = this._normalizeOutputMethod(this.insertMethod);
    this._populateOutputMethodMenu();
    this._populateTextOptionsMenu();
    this._updateAutoPasteItem();
    this._populateAutoPasteMenu();
    this._registerHotkeys();
    this._updatePanel();
    return pending.length;
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
      if (typeof args[i] !== "string") {
        throw new Error("Backend command argument must be text");
      }
      let value = args[i];
      if (i === 0) {
        value = value.trim();
      }
      if (this._containsCliControlChars(value)) {
        throw new Error("Backend command argument contains invalid control character");
      }
      if (value.indexOf("\u0000") >= 0) {
        throw new Error("Backend command argument contains invalid bytes");
      }
      let valueBytes = ByteArray.fromString(value).length;
      if (valueBytes > MAX_CLI_ARG_BYTES) {
        throw new Error("Backend command argument is too large");
      }
      totalBytes += valueBytes;
      normalized.push(value);
    }
    if (totalBytes > MAX_CLI_COMMAND_BYTES) {
      throw new Error("Backend command is too large");
    }
    let resolvedCommand = this._resolveAllowedCliCommand(normalized[0]);
    if (resolvedCommand === null) {
      throw new Error("Backend command is not executable");
    }
    normalized[0] = resolvedCommand;
    return normalized;
  },

  _coerceCliTextArg: function(value, fieldName) {
    if (value !== undefined && value !== null && typeof value !== "string") {
      throw new Error(String(fieldName || "value") + " must be text");
    }
    let normalized = typeof value === "string" ? value : "";
    if (normalized.indexOf("\u0000") >= 0) {
      throw new Error(String(fieldName || "value") + " contains invalid bytes");
    }
    if (this._containsCliControlChars(normalized)) {
      throw new Error(String(fieldName || "value") + " contains invalid control character");
    }
    if (normalized.length > MAX_SETTING_TEXT_CHARS) {
      throw new Error(String(fieldName || "value") + " is too long");
    }
    return normalized;
  },

  _coerceCliTextArgOrFallback: function(value, fieldName, fallback) {
    try {
      return this._coerceCliTextArg(value, fieldName);
    } catch (err) {
      this._logLifecycleError("settings-value", err);
      return typeof fallback === "string" ? fallback : "";
    }
  },

  _containsCliControlChars: function(value) {
    let normalized = String(value || "").toLowerCase();
    if (
      normalized.indexOf("\u000d") >= 0
      || normalized.indexOf("\u000a") >= 0
      || normalized.indexOf("\\r") >= 0
      || normalized.indexOf("\\n") >= 0
      || normalized.indexOf("\\u000d") >= 0
      || normalized.indexOf("\\u000a") >= 0
    ) {
      return true;
    }
    for (let i = 0; i < normalized.length; i++) {
      const code = normalized.charCodeAt(i);
      if (code < 0x20 || code === 0x7f) {
        return true;
      }
    }
    return false;
  },

  _coerceImportedSetting: function(key, value, fallback) {
    if (Object.prototype.hasOwnProperty.call(BOOLEAN_IMPORT_SETTINGS, key)) {
      return typeof value === "boolean" ? value : Boolean(fallback);
    }
    if (key === "max-seconds") {
      return typeof value === "number" ? this._normalizeRecordingLimit(value) : this._normalizeRecordingLimit(fallback);
    }
    if (key === "typing-delay-ms") {
      return typeof value === "number" ? this._normalizeTypingDelayMs(value) : this._normalizeTypingDelayMs(fallback);
    }
    if (key === "max-transcript-files") {
      return typeof value === "number" ? this._normalizeTranscriptLimit(value) : this._normalizeTranscriptLimit(fallback);
    }
    if (key === "language" || key === "secondary-language") {
      return this._coerceImportedEnumSetting(value, LANGUAGE_CODES, fallback);
    }
    if (key === "recorder") {
      return this._coerceImportedEnumSetting(value, RECORDER_METHODS, fallback);
    }
    if (key === "insert-method") {
      return this._coerceImportedEnumSetting(value, OUTPUT_METHODS, fallback);
    }
    if (key === "artifact-encryption") {
      return this._coerceImportedEnumSetting(value, ARTIFACT_ENCRYPTION_MODES, fallback);
    }
    if (key === "transcriber") {
      return this._coerceImportedEnumSetting(value, TRANSCRIBER_METHODS, fallback);
    }
    if (key === "post-process-backend") {
      return this._coerceImportedEnumSetting(value, POST_PROCESS_BACKENDS, fallback);
    }
    if (!Object.prototype.hasOwnProperty.call(IMPORT_TEXT_SETTINGS, key)) {
      return fallback;
    }
    if (typeof value !== "string") {
      return fallback;
    }
    try {
      return this._coerceCliTextArg(value, IMPORT_TEXT_SETTINGS[key]);
    } catch (err) {
      this._safeLogError(err);
      return fallback;
    }
  },

  _coerceImportedEnumSetting: function(value, allowedValues, fallback) {
    let normalized = String(value || "").trim();
    return allowedValues.indexOf(normalized) >= 0 ? normalized : fallback;
  },

  _isAllowedCliCommand: function(command) {
    return this._resolveAllowedCliCommand(command) !== null;
  },

  _findTrustedProgramInPath: function(command) {
    let name = String(command || "").trim();
    if (name === "" || name.indexOf("/") >= 0 || name.indexOf("\u0000") >= 0 || this._containsCliControlChars(name)) {
      return null;
    }
    for (let directory of TRUSTED_SPAWN_DIRS) {
      let candidate = GLib.build_filenamev([directory, name]);
      if (GLib.file_test(candidate, GLib.FileTest.IS_EXECUTABLE)) {
        return candidate;
      }
    }
    return null;
  },

  _resolveAllowedCliCommand: function(command) {
    let value = String(command || "").trim();
    if (value === "") {
      return null;
    }
    if (value.indexOf("/") >= 0) {
      if (value.charAt(0) !== "/") {
        return null;
      }
      return GLib.file_test(value, GLib.FileTest.IS_EXECUTABLE) ? value : null;
    }
    return this._findTrustedProgramInPath(value);
  },

  _parseSpawnOutput: function(stdout) {
    let output = String(stdout || "");
    if (utf8ByteLength(output) > MAX_SPAWN_JSON_BYTES) {
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

  _argValue: function(args, flag) {
    let index = args.indexOf(flag);
    if (index < 0 || index + 1 >= args.length) {
      return "";
    }
    return String(args[index + 1] || "");
  },

  _shouldExposeOpenAiCompatibleApiKeyToBackend: function(args) {
    let command = args.length > 1 ? String(args[1] || "") : "";
    if (command === "text-models") {
      return this._argValue(args, "--backend") === "openai-compatible";
    }
    if (["toggle", "start", "stop", "transcribe-file"].indexOf(command) < 0) {
      return false;
    }
    return this._argValue(args, "--transcriber") === "openai-compatible" ||
      this._argValue(args, "--post-process-backend") === "openai-compatible";
  },

  _openAiCompatibleApiKeyForBackend: function() {
    return this._coerceCliTextArg(this.externalApiEnvApiKey || this.openaiCompatibleApiKey || "", "openai-compatible API key").trim();
  },

  _runWithBackendEnvironment: function(includeOpenAiCompatibleApiKey, fn) {
    if (!includeOpenAiCompatibleApiKey) {
      return fn(null);
    }
    let apiKey = this._openAiCompatibleApiKeyForBackend();
    if (apiKey === "") {
      return fn(null);
    }
    return fn({
      "SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY": apiKey,
    });
  },

  _runBoundedSubprocess: function(args, env, options, callback) {
    options = options || {};
    if (!this._lifecycleAllowsWork()) {
      return null;
    }
    let hasInput = options.inputText !== null && options.inputText !== undefined;
    if (hasInput && typeof options.inputText !== "string") {
      throw new Error("Subprocess input must be text");
    }
    if (hasInput && utf8ByteLength(options.inputText) > MAX_SPAWN_JSON_BYTES) {
      throw new Error("Subprocess input is too large");
    }
    let maxStdoutBytes = typeof options.maxStdoutBytes === "number" && isFinite(options.maxStdoutBytes)
      ? Math.max(1, options.maxStdoutBytes)
      : MAX_SPAWN_JSON_BYTES;
    let maxStderrBytes = typeof options.maxStderrBytes === "number" && isFinite(options.maxStderrBytes)
      ? Math.max(1, options.maxStderrBytes)
      : MAX_SPAWN_STDERR_BYTES;
    let timeoutMs = typeof options.timeoutMs === "number" && isFinite(options.timeoutMs) ? Math.max(0, options.timeoutMs) : 0;
    let minimumTimeoutMs = typeof options.minimumTimeoutMs === "number" && isFinite(options.minimumTimeoutMs)
      ? Math.max(1, options.minimumTimeoutMs)
      : 250;
    let flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE;
    if (hasInput) {
      flags |= Gio.SubprocessFlags.STDIN_PIPE;
    }
    let launcher = new Gio.SubprocessLauncher({ flags: flags });
    env = env || {};
    for (let key in env) {
      if (Object.prototype.hasOwnProperty.call(env, key)) {
        launcher.setenv(key, String(env[key] || ""), true);
      }
    }
    let process = launcher.spawnv(args);
    let generation = this.spawnGeneration;
    let processToken;
    try {
      processToken = this._registerProcess(process, generation, options.resourceGroup);
    } catch (error) {
      this._terminateProcess(process);
      throw error;
    }
    let cancellable = null;
    let cancellableToken = null;
    try {
      cancellable = new Gio.Cancellable();
      cancellableToken = this._registerCancellable(cancellable);
    } catch (error) {
      this._unregisterCancellable(cancellableToken);
      this._unregisterProcess(processToken);
      this._terminateProcess(process);
      throw error;
    }
    let timeoutKey = "process-timeout-" + processToken;
    let done = false;
    let stdoutParts = [];
    let stderrParts = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let ended = { stdout: false, stderr: false };
    let processExited = false;
    let processSuccessful = false;
    let processWaitError = null;

    let finish = (result, terminate, suppressCallback) => {
      if (done) {
        return;
      }
      done = true;
      this._clearTrackedTimer(timeoutKey);
      if (terminate) {
        this._terminateProcess(process);
      }
      try {
        cancellable.cancel();
      } catch (ignored) {
        // Cancellation is best effort during normal EOF and teardown.
      }
      this._unregisterProcess(processToken);
      this._unregisterCancellable(cancellableToken);
      if (suppressCallback || this.appletRemoved || this.spawnGeneration !== generation || typeof callback !== "function") {
        return;
      }
      try {
        callback(stdoutParts.join(""), stderrParts.join(""), result || {});
      } catch (error) {
        this._recordLifecycleError("process-callback", error);
      }
    };
    try {
      let processEntry = this._resourceRegistry && this._resourceRegistry.processes
        ? this._resourceRegistry.processes[processToken]
        : null;
      if (!processEntry) {
        throw new Error("Process registry entry is unavailable");
      }
      let cancelCallback = (notifyCallback) => finish(
          { cancelled: true },
          true,
          notifyCallback === true ? false : true
        );
      processEntry.cancel = cancelCallback;
      if (processEntry.cancel !== cancelCallback) {
        throw new Error("Process cancellation callback could not be registered");
      }
    } catch (error) {
      this._runTeardownGuarded("process-cancel-registration", () => this._recordLifecycleError("process-cancel-registration", error));
      finish({ error: error }, true, true);
      return null;
    }

    let finishWhenReady = () => {
      if (!processExited || !ended.stdout || !ended.stderr) {
        return;
      }
      if (processWaitError) {
        finish({ error: processWaitError }, false);
        return;
      }
      if (!processSuccessful) {
        finish({ error: "Subprocess exited unsuccessfully" }, false);
        return;
      }
      finish({ completed: true }, false);
    };

    let readStream = (stream, name, maxBytes, chunks) => {
      if (done || !stream || !stream.read_bytes_async) {
        finish({ error: "Subprocess output stream unavailable" }, true);
        return;
      }
      try {
        stream.read_bytes_async(SUBPROCESS_READ_CHUNK_BYTES, GLib.PRIORITY_DEFAULT, cancellable, (source, result) => {
          if (done) {
            return;
          }
          try {
            let bytes = source.read_bytes_finish(result);
            let size = bytes && bytes.get_size ? Number(bytes.get_size()) : Number(bytes && bytes.length || 0);
            if (!size) {
              ended[name] = true;
              finishWhenReady();
              return;
            }
            let data = bytes && bytes.get_data ? bytes.get_data() : bytes;
            let chunk = ByteArray.toString(data || "");
            if (name === "stdout") {
              stdoutBytes += size;
            } else {
              stderrBytes += size;
            }
            if ((name === "stdout" ? stdoutBytes : stderrBytes) > maxBytes) {
              finish({ outputTooLarge: true, stream: name }, true);
              return;
            }
            chunks.push(chunk);
            readStream(stream, name, maxBytes, chunks);
          } catch (error) {
            finish({ error: error }, true);
          }
        });
      } catch (error) {
        finish({ error: error }, true);
      }
    };

    if (timeoutMs > 0 && !this._scheduleTrackedTimer(timeoutKey, Math.max(minimumTimeoutMs, timeoutMs), () => {
      finish({ timedOut: true }, true);
      return false;
    }, false)) {
      finish({ error: "Subprocess timeout could not be scheduled" }, true);
      return null;
    }

    try {
      if (!process.wait_check_async || !process.wait_check_finish) {
        finish({ error: "Subprocess exit status API unavailable" }, true);
      } else {
        process.wait_check_async(cancellable, (source, result) => {
          if (done) {
            return;
          }
          processExited = true;
          try {
            let waitResult = source.wait_check_finish(result);
            if (waitResult !== true) {
              throw new Error("Subprocess exit status check failed");
            }
            processSuccessful = true;
          } catch (error) {
            processWaitError = error;
          }
          finishWhenReady();
        });
      }
    } catch (error) {
      processExited = true;
      processWaitError = error;
      finishWhenReady();
    }

    try {
      readStream(process.get_stdout_pipe(), "stdout", maxStdoutBytes, stdoutParts);
      readStream(process.get_stderr_pipe(), "stderr", maxStderrBytes, stderrParts);
    } catch (error) {
      finish({ error: error }, true);
    }
    if (hasInput) {
      try {
        let assertInputWriteSucceeded = (writeResult) => {
          let successful = Array.isArray(writeResult) ? writeResult[0] : writeResult;
          if (successful === false) {
            throw new Error("Subprocess input write failed");
          }
        };
        let closeInput = (stream) => {
          if (!stream || !stream.close) {
            return;
          }
          if (stream.close(null) === false) {
            throw new Error("Subprocess input close failed");
          }
        };
        let stdin = process.get_stdin_pipe();
        let inputBytes = ByteArray.fromString(options.inputText);
        if (stdin && stdin.write_all_async) {
          stdin.write_all_async(inputBytes, GLib.PRIORITY_DEFAULT, cancellable, (stream, result) => {
            try {
              assertInputWriteSucceeded(stream.write_all_finish(result));
              closeInput(stream);
            } catch (error) {
              finish({ error: error }, true);
            }
          });
        } else if (stdin && stdin.write_all) {
          assertInputWriteSucceeded(stdin.write_all(inputBytes, null));
          closeInput(stdin);
        } else {
          finish({ error: "Subprocess input stream unavailable" }, true);
        }
      } catch (error) {
        finish({ error: error }, true);
      }
    }
    return {
      token: processToken,
      process: process,
      cancellable: cancellable,
      cancel: () => finish({ cancelled: true }, true),
    };
  },

  _spawnJsonWithBackendEnvironment: function(args, env, callback, inputText, options) {
    options = options || {};
    let completed = false;
    let completeOnce = (stdout, result, stderr) => {
      if (completed) {
        return;
      }
      completed = true;
      if (typeof callback === "function") {
        callback(stdout, result, stderr);
      }
    };
    let handle = this._runBoundedSubprocess(args, env, {
      inputText: inputText,
      timeoutMs: options.timeoutMs,
      maxStdoutBytes: options.maxStdoutBytes || MAX_SPAWN_JSON_BYTES,
      maxStderrBytes: options.maxStderrBytes || MAX_SPAWN_STDERR_BYTES,
      resourceGroup: options.resourceGroup,
    }, (stdout, stderr, result) => {
      completeOnce(String(stdout || ""), result || {}, String(stderr || ""));
    });
    if (!handle) {
      completeOnce("", { error: "Subprocess could not be started" }, "");
    }
    return handle;
  },

  _isStatusCommandArgs: function(args) {
    return Array.isArray(args) && args.length > 1 && String(args[1] || "") === "status";
  },

  _spawnJson: function(args, callback, options) {
    options = options || {};
    let normalizedArgs;
    let callbackFn = this._guardStateCallback("backend-json", callback, undefined) || function() {};

    try {
      normalizedArgs = this._coerceSpawnArgs(args);
      if (!this._isStatusCommandArgs(normalizedArgs)) {
        this._statusRefreshToken++;
      }
      let timeoutMs = Object.prototype.hasOwnProperty.call(options, "timeoutMs")
        ? Number(options.timeoutMs)
        : CLI_COMMAND_TIMEOUT_MS;
      let inputText = Object.prototype.hasOwnProperty.call(options, "inputText")
        ? String(options.inputText || "")
        : null;
      return this._runWithBackendEnvironment(this._shouldExposeOpenAiCompatibleApiKeyToBackend(normalizedArgs), (backendEnv) => {
        return this._spawnJsonWithBackendEnvironment(normalizedArgs, backendEnv || {}, (stdout, result) => {
          if (result && result.timedOut) {
            callbackFn({ status: "error", error: "Backend command timed out" });
            return;
          }
          if (result && result.outputTooLarge) {
            callbackFn({ status: "error", error: "Backend response is too large" });
            return;
          }
          if (result && result.error) {
            callbackFn({ status: "error", error: "Backend command failed" });
            return;
          }
          if (String(stdout || "").trim() === "") {
            callbackFn({ status: "error", error: "Backend returned no response" });
            return;
          }
          callbackFn(this._parseSpawnOutput(stdout));
        }, inputText, {
          timeoutMs: timeoutMs,
          maxStdoutBytes: MAX_SPAWN_JSON_BYTES,
          maxStderrBytes: MAX_SPAWN_STDERR_BYTES,
          resourceGroup: options.resourceGroup,
        });
      });
    } catch (error) {
      this._recordLifecycleError("backend-json-spawn", error);
      callbackFn({ status: "error", error: this._lifecycleErrorText(error) });
      return null;
    }
  },

  _spawnText: function(args, callback, options) {
    options = options || {};
    let callbackFn = this._guardStateCallback("backend-text", callback, undefined) || function() {};

    try {
      let normalizedArgs = this._coerceSpawnArgs(args);
      this._statusRefreshToken++;
      let timeoutMs = Object.prototype.hasOwnProperty.call(options, "timeoutMs")
        ? Number(options.timeoutMs)
        : CLI_COMMAND_TIMEOUT_MS;
      return this._spawnJsonWithBackendEnvironment(normalizedArgs, {}, (stdout, result) => {
        if ((result && (result.timedOut || result.outputTooLarge || result.error))) {
          callbackFn("");
          return;
        }
        let output = String(stdout || "");
        callbackFn(utf8ByteLength(output) > MAX_SPAWN_TEXT_BYTES ? "" : output);
      }, null, {
        timeoutMs: timeoutMs,
        maxStdoutBytes: MAX_SPAWN_TEXT_BYTES,
        maxStderrBytes: MAX_SPAWN_STDERR_BYTES,
        resourceGroup: options.resourceGroup,
      });
    } catch (error) {
      this._recordLifecycleError("backend-text-spawn", error);
      callbackFn("");
      return null;
    }
  },

  _applyPayloadSafely: function(payload, statusRefreshToken) {
    try {
      this._applyPayload(payload, statusRefreshToken);
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Backend response handling failed: ") + safeError, this.lastTranscript);
    }
  },

  _applyPayload: function(payload, statusRefreshToken) {
    if (typeof statusRefreshToken === "number" && statusRefreshToken !== this._statusRefreshToken) {
      return;
    }
    if (typeof statusRefreshToken !== "number") {
      this._statusRefreshToken++;
    }
    let status = this._normalizePayloadStatus(payload.status, Boolean(payload.error));
    this._applyPayloadLanguage(payload, status);
    this._updateRecordingTiming(payload, status);
    this._applyMicrophoneLevel(payload.microphone_level, status);
    if (payload.error || status === "error") {
      let errorMessage = this._payloadErrorMessage(payload, _("Backend reported an error"));
      let preserveActiveRecordingState = typeof statusRefreshToken === "number" && this._hasActiveRecordingState();
      if (preserveActiveRecordingState) {
        this._setStatusPreservingRecording("error", errorMessage, this.lastTranscript);
        this._scheduleStatusPoll();
      } else {
        this.cancelPendingWhileCommandRunning = false;
        this.autoTranscribeRecordingKey = "";
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this._setStatus("error", errorMessage, this.lastTranscript);
      }
      this._maybeWarnRejectedArtifactPassphrase(errorMessage);
      return;
    }
    let hasTranscript = typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript);
    if (status === "done") {
      this._maybeWarnUnencryptedArtifactStorage(payload, status);
    }
    if (this.cancelPendingWhileCommandRunning && status === "done") {
      this.cancelPendingWhileCommandRunning = false;
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatus("ready", _("Cancel applied; transcript not inserted"), this.lastTranscript);
      return;
    }
    if (
      this.cancelPendingWhileCommandRunning &&
      (status === "recording" || status === "recorded") &&
      !this.isCommandRunning
    ) {
      this.cancelPendingWhileCommandRunning = false;
      this._cancelRecording(status);
      return;
    }
    if (this.cancelPendingWhileCommandRunning && !this.isCommandRunning) {
      this.cancelPendingWhileCommandRunning = false;
    }
    if (status === "done" && payload.silence_detected === true) {
      this._finishSilentRelistenSkip(payload);
      return;
    }
    if (status === "done" && hasTranscript) {
      this._finishAppletTextInsert(payload);
      return;
    }
    if (status === "done" && this.autoRelistenPending) {
      this._finishEmptyRelistenDone(payload);
      return;
    }
    if (!this.isCommandRunning && !this.autoRelistenManualStopRequested) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
    }
    let message = this._payloadMessage(payload, status);
    let transcript = typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript)
      ? payload.transcript
      : this.lastTranscript || "";
    this._setStatus(status, message, transcript);
    if (
      (status === "recording" || status === "recorded") &&
      this.autoRelistenManualStopRequested &&
      !this.isCommandRunning
    ) {
      this._toggleRecording();
      return;
    }
    this._maybeAutoTranscribeRecorded(payload, status);
  },

  _artifactEncryptionWarningKey: function(payload) {
    let marker = this._payloadStringMarker(payload, ["transcript_path", "audio_path", "audio", "stopped_at", "started_at"], "");
    if (marker === "") {
      marker = this._payloadStringMarker(payload, ["status"], "done");
    }
    return marker;
  },

  _payloadStringMarker: function(payload, keys, fallback) {
    if (!payload || typeof payload !== "object" || !Array.isArray(keys)) {
      return typeof fallback === "string" ? fallback : "";
    }
    for (let key of keys) {
      if (typeof key !== "string") {
        continue;
      }
      let value = payload[key];
      if (typeof value === "string" && value.trim() !== "") {
        return value.trim();
      }
    }
    return typeof fallback === "string" ? fallback : "";
  },

  _isRejectedArtifactPassphraseError: function(message) {
    let normalized = String(message || "").toLowerCase();
    if (normalized.indexOf("artifact encryption passphrase") < 0) {
      return false;
    }
    return (
      normalized.indexOf("not strong enough") >= 0 ||
      normalized.indexOf("could not be read") >= 0 ||
      normalized.indexOf("could not be generated") >= 0 ||
      normalized.indexOf("must be private") >= 0 ||
      normalized.indexOf("must be owned by the current user") >= 0 ||
      normalized.indexOf("must not be a symlink") >= 0 ||
      normalized.indexOf("must not be hardlinked") >= 0
    );
  },

  _maybeWarnRejectedArtifactPassphrase: function(message) {
    if (!this._isRejectedArtifactPassphraseError(message)) {
      return;
    }
    let safeMessage = this._sanitizeErrorMessage(message);
    let warningKey = String(safeMessage || "");
    if (warningKey !== "" && warningKey === this.lastRejectedArtifactPassphraseWarningKey) {
      return;
    }
    this.lastRejectedArtifactPassphraseWarningKey = warningKey;
    this._notify(
      _("Speed of Cinnamon encryption warning"),
      _("Artifact encryption passphrase was rejected: ") + safeMessage,
      true
    );
  },

  _maybeWarnUnencryptedArtifactStorage: function(payload, statusOverride) {
    let status = statusOverride || this._normalizePayloadStatus(payload && payload.status, Boolean(payload && payload.error));
    if (!payload || status !== "done") {
      return;
    }
    let mode = this._normalizeArtifactEncryption(payload.artifact_encryption || this.artifactEncryption);
    if (mode === "off") {
      return;
    }
    let transcriptPath = typeof payload.transcript_path === "string" ? payload.transcript_path.trim() : "";
    let transcriptStoredPlaintext = transcriptPath !== "" && payload.transcript_encrypted === false;
    let recordingStoredPlaintext = payload.recording_artifacts_kept === true && payload.recording_encrypted === false;
    if (!transcriptStoredPlaintext && !recordingStoredPlaintext) {
      return;
    }
    let warningKey = this._artifactEncryptionWarningKey(payload);
    if (warningKey !== "" && warningKey === this.lastArtifactEncryptionWarningKey) {
      return;
    }
    this.lastArtifactEncryptionWarningKey = warningKey;
    let details = [];
    if (transcriptStoredPlaintext) {
      details.push(_("transcript"));
    }
    if (recordingStoredPlaintext) {
      details.push(_("recording"));
    }
    let subject = details.length > 0 ? details.join(", ") : _("stored artifact");
    let message = _("Encryption is selected, but the backend reported unencrypted stored data: ") + subject + ". " + _("Check Secret Service/keyring or passphrase configuration.");
    this.lastMessage = message;
    this._notify(_("Speed of Cinnamon encryption warning"), message, true);
  },

  _emptyTranscriptMarker: function(transcript) {
    return String(transcript || "").toLowerCase().replace(/[\W_]+/g, " ").trim();
  },

  _isEmptyTranscriptText: function(transcript) {
    let marker = this._emptyTranscriptMarker(transcript);
    return marker === "" || EMPTY_TRANSCRIPT_MARKERS.indexOf(marker) >= 0;
  },

  _applyMicrophoneLevel: function(level, status) {
    if (status !== "recording" && status !== "recorded") {
      this.microphoneLevel = null;
      return;
    }
    if (!level || typeof level !== "object") {
      this.microphoneLevel = null;
      return;
    }
    let safeLevel = Object.assign({}, level);
    safeLevel.detail = typeof safeLevel.detail === "string"
      ? this._shortMenuText(this._sanitizeErrorMessage(safeLevel.detail), 160)
      : "";
    this.microphoneLevel = safeLevel;
  },

  _applyPayloadLanguage: function(payload, statusOverride) {
    let language = payload && typeof payload.language === "string"
      ? payload.language.trim().toLowerCase()
      : "";
    if (LANGUAGE_CODES.indexOf(language) < 0) {
      language = "";
    }
    let status = statusOverride || this._normalizePayloadStatus(payload.status, Boolean(payload.error));
    if (language !== "" && (status === "recording" || status === "recorded" || status === "processing")) {
      this.activeLanguage = language;
      this.activeLanguageExplicit = true;
      return;
    }
    this._syncActiveLanguage();
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
    let maxSeconds = typeof payload.max_seconds === "number" && isFinite(payload.max_seconds)
      ? payload.max_seconds
      : this.maxSeconds;
    this.recordingMaxSeconds = this._normalizeRecordingLimit(maxSeconds);
  },

  _parseDateMs: function(value) {
    if (!value) {
      return 0;
    }
    let parsed = Date.parse(String(value));
    return isNaN(parsed) ? 0 : parsed;
  },

  _maybeAutoTranscribeRecorded: function(payload, statusOverride) {
    if ((!this.autoTranscribeTimeout && !this.autoRelisten) || !this.notificationSessionActive || this.isCommandRunning) {
      return;
    }
    let status = statusOverride || this._normalizePayloadStatus(payload.status, Boolean(payload.error));
    if (status !== "recorded") {
      return;
    }
    let recordingKey = this._payloadStringMarker(payload, ["audio_path", "audio"], "recorded");
    if (this.autoTranscribeRecordingKey === recordingKey) {
      return;
    }
    let stopArgs;
    try {
      stopArgs = this._baseArgs("stop");
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Could not prepare timed recording command: ") + safeError, this.lastTranscript);
      return;
    }
    this.autoTranscribeRecordingKey = recordingKey;
    let relistenToken = "";
    if (this.autoRelisten) {
      this.autoRelistenSequence += 1;
      relistenToken = String(this.autoRelistenSequence) + ":" + recordingKey;
    }
    this.autoRelistenPending = Boolean(relistenToken);
    this.autoRelistenPendingToken = relistenToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Transcribing timed-out recording..."), this.lastTranscript);
    this._spawnJson(stopArgs, (nextPayload) => {
      if (relistenToken && this.autoRelistenPendingToken !== relistenToken) {
        this.isCommandRunning = false;
        if (this.cancelPendingWhileCommandRunning) {
          this._applyPayloadSafely(nextPayload);
        }
        return;
      }
      if (nextPayload && nextPayload.error) {
        this.autoTranscribeRecordingKey = "";
      }
      this.isCommandRunning = false;
      this._applyPayloadSafely(nextPayload);
    });
  },

  _clearStatusTimer: function() {
    this._clearTrackedTimer("status", "statusTimer");
  },

  _clearDisplayTimer: function() {
    this._clearTrackedTimer("display", "displayTimer");
  },

  _clearSetupCheckTimer: function() {
    this._clearTrackedTimer("setup", "setupCheckTimer");
  },

  _clearPasteTimer: function() {
    this._clearTrackedTimer("paste", "pasteTimer");
  },

  _clearAlarmTimer: function() {
    this._clearTrackedTimer("alarm", "alarmTimer");
  },

  _clearOllamaInstallWatchTimer: function() {
    this._clearTrackedTimer("ollama-install", "ollamaInstallWatchTimer");
  },

  _cancelOllamaInstallWatch: function() {
    this.ollamaInstallWatchToken = null;
    this._clearOllamaInstallWatchTimer();
  },

  _watchOllamaInstallThenChoose: function() {
    this._cancelOllamaInstallWatch();
    let watchToken = {};
    this.ollamaInstallWatchToken = watchToken;
    this.ollamaInstallWatchPolls = 0;
    this._setStatus("processing", _("Waiting for Ollama installation..."), this.lastTranscript);
    this._scheduleOllamaInstallWatchPoll(watchToken);
  },

  _scheduleOllamaInstallWatchPoll: function(watchToken) {
    if (!watchToken || this.ollamaInstallWatchToken !== watchToken || !this._lifecycleAllowsWork()) {
      return;
    }
    let timerId = this._scheduleTrackedTimer("ollama-install", OLLAMA_INSTALL_POLL_SECONDS, () => {
      if (this.ollamaInstallWatchToken !== watchToken || !this._lifecycleAllowsWork()) {
        return false;
      }
      this.ollamaInstallWatchPolls++;
      let textModelArgs = this._tryTextModelsArgs("ollama");
      if (!textModelArgs) {
        this.ollamaInstallWatchToken = null;
        this._clearOllamaModelFlow();
        return false;
      }
      this._spawnJson(textModelArgs, (payload) => {
        if (this.ollamaInstallWatchToken !== watchToken || !this._lifecycleAllowsWork()) {
          return;
        }
        try {
          if (payload.error) {
            this.ollamaInstallWatchToken = null;
            this._clearOllamaModelFlow();
            this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
            return;
          }
          if (payload.available === true) {
            this.ollamaInstallWatchToken = null;
            let models = Array.isArray(payload.models) ? payload.models : [];
            this._setStatus("ready", _("Ollama is ready"), this.lastTranscript);
            if (models.length > 0) {
              this._promptChooseOllamaTextModel(models, this.ollamaModelFlowToken);
            } else {
              this._promptInstallOllamaTextModel(this.ollamaModelFlowToken);
            }
            return;
          }
          if (this.ollamaInstallWatchPolls >= OLLAMA_INSTALL_MAX_POLLS) {
            this.ollamaInstallWatchToken = null;
            this._clearOllamaModelFlow();
            this._setStatus("error", _("Ollama installation did not become reachable"), this.lastTranscript);
            this._notify(_("Ollama is not reachable"), _("Install finished or was cancelled, but 127.0.0.1:11434 is still unavailable."), true);
            return;
          }
          this._scheduleOllamaInstallWatchPoll(watchToken);
        } catch (err) {
          this.ollamaInstallWatchToken = null;
          this._clearOllamaModelFlow();
          let safeError = this._sanitizeErrorMessage(err);
          this._setStatusPreservingRecording("error", _("Ollama status check failed: ") + safeError, this.lastTranscript);
        }
      }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS, resourceGroup: "ollama" });
      return false;
    }, true, "ollamaInstallWatchTimer");
    if (!timerId && this.ollamaInstallWatchToken === watchToken) {
      this.ollamaInstallWatchToken = null;
      this._clearOllamaModelFlow();
      this._setStatusPreservingRecording("error", _("Ollama installation watch could not be scheduled"), this.lastTranscript);
    }
  },

  _scheduleSetupCheck: function() {
    this._clearSetupCheckTimer();
    if (this.appletRemoved) {
      return;
    }
    let timerId = this._scheduleTrackedTimer("setup", 2, () => {
      if (this.status === "idle") {
        this._runDoctor(true);
      }
      return false;
    }, true, "setupCheckTimer");
    if (!timerId && this._lifecycleAllowsWork() && !this.appletRemoved) {
      this._setStatusPreservingRecording("setup", _("Setup check timer could not be scheduled"), this.lastTranscript);
    }
  },

  _scheduleAlarmCheck: function(delaySeconds) {
    this._clearAlarmTimer();
    if (this.appletRemoved) {
      return;
    }
    let timerId = this._scheduleTrackedTimer("alarm", Math.max(5, Number(delaySeconds || ALARM_CHECK_SECONDS)), () => {
      try {
        this._checkAlarms(false);
      } finally {
        if (!this.appletRemoved) {
          this._scheduleAlarmCheck(ALARM_CHECK_SECONDS);
        }
      }
      return false;
    }, true, "alarmTimer");
    if (!timerId && this._lifecycleAllowsWork() && !this.appletRemoved) {
      this._setStatusPreservingRecording("error", _("Alarm timer could not be scheduled"), this.lastTranscript);
    }
  },

  _scheduleStatusPoll: function() {
    this._clearStatusTimer();
    if (this.appletRemoved) {
      return;
    }
    if (this.status !== "recording" && this.status !== "processing") {
      return;
    }
    let timerId = this._scheduleTrackedTimer("status", 2, () => {
      this._refreshStatus();
      return false;
    }, true, "statusTimer");
    if (!timerId && (this.status === "recording" || this.status === "processing")) {
      this._setStatusPreservingRecording("error", _("Status polling timer could not be scheduled"), this.lastTranscript);
    }
  },

  _scheduleDisplayTick: function() {
    this._clearDisplayTimer();
    if (this.appletRemoved) {
      return;
    }
    if (this.status !== "recording") {
      return;
    }
    let timerId = this._scheduleTrackedTimer("display", 1, () => {
      if (this.status === "recording") {
        this._updatePanel();
        if (!this.appletRemoved) {
          this._scheduleDisplayTick();
        }
      }
      return false;
    }, true, "displayTimer");
    if (!timerId && this._lifecycleAllowsWork() && this.status === "recording") {
      this._setStatusPreservingRecording("error", _("Recording display timer could not be scheduled"), this.lastTranscript);
    }
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
    if (this._windowLooksLikeSpeedOfCinnamon(window)) {
      return false;
    }
    return true;
  },

  _hasRememberedTargetWindow: function() {
    return this._isUsableTargetWindow(this.targetWindow) || /^[0-9]+$/.test(String(this.targetWindowXid || "").trim());
  },

  _rememberFocusedWindow: function(preserveOnFailure) {
    this.targetWindowGeneration = Number(this.targetWindowGeneration || 0) + 1;
    let targetGeneration = this.targetWindowGeneration;
    this._terminateProcessesByGroup("keyboard", true);
    this._terminateProcessesByGroup("x11", true);
    this._terminateProcessesByGroup("clipboard", true);
    let window = global.display ? global.display.focus_window : null;
    if (this._isUsableTargetWindow(window)) {
      this.targetWindow = window;
      this._clearTargetWindowXid();
      this._rememberActiveXWindow(function() {}, targetGeneration);
      return true;
    }
    if (window && this._windowLooksLikeSpeedOfCinnamon(window)) {
      this._notifySelfProtectionBlocked(
        this._windowProbeValue(window, "get_title"),
        this._windowProbeValue(window, "get_wm_class") ||
          this._windowProbeValue(window, "get_wm_class_instance") ||
          this._windowProbeValue(window, "get_gtk_application_id")
      );
    }
    this.targetWindow = null;
    if (!preserveOnFailure) {
      this._clearTargetWindowXid();
    }
    this._rememberActiveXWindow((remembered) => {
      if (targetGeneration !== this.targetWindowGeneration) {
        return;
      }
      if (remembered) {
        return;
      }
      if (!(preserveOnFailure && this._hasRememberedTargetWindow())) {
        this._clearTargetWindowXid();
      }
    }, targetGeneration);
    return true;
  },

  _restoreTargetWindowForPaste: function(completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    if (this._isUsableTargetWindow(this.targetWindow)) {
      let activated = false;
      try {
        Main.activateWindow(this.targetWindow, global.get_current_time());
        activated = true;
      } catch (err) {
        this._recordLifecycleError("x11-focus", err);
      }
      if (activated) {
        this._runStateGuarded("x11-focus-callback", () => complete(true), undefined);
        return true;
      }
    }
    return this._activateTargetXWindow(complete);
  },

  _closeMenuForKeyboardInsert: function() {
    try {
      if (this.menu && this.menu.isOpen) {
        this.menu.close();
      }
      return true;
    } catch (err) {
      this._recordLifecycleError("keyboard-menu-close", err);
      return false;
    }
  },

  _clearTargetWindowXid: function() {
    this.targetWindowXid = "";
    this.targetWindowXTitle = "";
    this.targetWindowXClass = "";
  },

  _xdotoolOutput: function(args, maxBytes, completionCallback, timeoutMs) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let completed = false;
    let completeOnce = (value) => {
      if (completed) {
        return;
      }
      completed = true;
      complete(value);
    };
    let timeout;
    let xdotool;
    try {
      timeout = this._findTrustedProgramInPath("timeout");
      xdotool = this._findTrustedProgramInPath("xdotool");
    } catch (error) {
      this._recordLifecycleError("x11-command", error);
      completeOnce(null);
      return false;
    }
    if (!timeout || !xdotool || !this._lifecycleAllowsWork()) {
      completeOnce(null);
      return false;
    }
    let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), xdotool];
    args = args || [];
    for (let i = 0; i < args.length; i++) {
      command.push(args[i]);
    }
    try {
      let handle = this._runBoundedSubprocess(this._coerceSpawnArgs(command), {}, {
        timeoutMs: Math.max(1, Number(timeoutMs || X11_COMMAND_TIMEOUT_MS)),
        minimumTimeoutMs: 1,
        maxStdoutBytes: Math.max(1, Number(maxBytes || MAX_XDOTOOL_TARGET_OUTPUT_BYTES)),
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        resourceGroup: "x11",
      }, (stdout, stderr, result) => {
        if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
          completeOnce(null);
          return;
        }
        completeOnce(String(stdout || ""));
      });
      if (!handle) {
        completeOnce(null);
      }
      return Boolean(handle);
    } catch (error) {
      this._recordLifecycleError("x11-command", error);
      completeOnce(null);
      return false;
    }
  },

  _xWindowLooksLikeSpeedOfCinnamon: function(title, windowClass) {
    let classValue = String(windowClass || "").toLowerCase();
    for (let i = 0; i < TERMINAL_WINDOW_MARKERS.length; i++) {
      if (classValue.indexOf(TERMINAL_WINDOW_MARKERS[i]) >= 0) {
        return false;
      }
    }
    let value = (String(title || "") + "\n" + String(windowClass || "")).toLowerCase();
    return value.indexOf("speed of cinnamon") >= 0 ||
      value.indexOf("speed-of-cinnamon") >= 0 ||
      value.indexOf(UUID.toLowerCase()) >= 0;
  },

  _rememberActiveXWindow: function(completionCallback, expectedGeneration) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let targetGeneration = expectedGeneration === undefined
      ? Number(this.targetWindowGeneration || 0)
      : Number(expectedGeneration);
    let isCurrent = () => targetGeneration === Number(this.targetWindowGeneration || 0) && this._lifecycleAllowsWork();
    let deadlineMs = Date.now() + X11_COMMAND_TIMEOUT_MS;
    this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (activeOutput) => {
      if (!isCurrent()) {
        complete(false);
        return;
      }
      let xid = String(activeOutput || "").trim();
      if (!/^[0-9]+$/.test(xid)) {
        this._clearTargetWindowXid();
        complete(false);
        return;
      }
      let title = "";
      let windowClass = "";
      this._xdotoolOutput(["getwindowname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (titleOutput) => {
        if (!isCurrent()) {
          complete(false);
          return;
        }
        title = String(titleOutput || "").trim();
        this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (classOutput) => {
          if (!isCurrent()) {
            complete(false);
            return;
          }
          windowClass = String(classOutput || "").trim();
          if (this._xWindowLooksLikeSpeedOfCinnamon(title, windowClass)) {
            this._notifySelfProtectionBlocked(title, windowClass);
            this._clearTargetWindowXid();
            complete(false);
            return;
          }
          this.targetWindowXid = xid;
          this.targetWindowXTitle = this._shortMenuText(title, 160);
          this.targetWindowXClass = this._shortMenuText(windowClass, 160);
          complete(true);
        }, Math.max(1, deadlineMs - Date.now()));
      }, Math.max(1, deadlineMs - Date.now()));
    }, Math.max(1, deadlineMs - Date.now()));
    return true;
  },

  _activateTargetXWindow: function(completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let xid = String(this.targetWindowXid || "").trim();
    let targetGeneration = Number(this.targetWindowGeneration || 0);
    if (!/^[0-9]+$/.test(xid)) {
      complete(false);
      return false;
    }
    return this._xdotoolOutput(["windowactivate", "--sync", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (output) => {
      complete(targetGeneration === Number(this.targetWindowGeneration || 0) && output !== null);
    });
  },

  _targetXWindowSnapshot: function() {
    let xid = String(this.targetWindowXid || "").trim();
    if (!/^[0-9]+$/.test(xid)) {
      if (this._isUsableTargetWindow(this.targetWindow)) {
        return {
          window: this.targetWindow,
          windowClass: this._windowProbeValue(this.targetWindow, "get_wm_class"),
          windowTitle: this._windowProbeValue(this.targetWindow, "get_title"),
          targetWindowGeneration: Number(this.targetWindowGeneration || 0),
        };
      }
      return null;
    }
    return {
      xid: xid,
      windowClass: String(this.targetWindowXClass || "").trim().toLowerCase(),
      windowTitle: String(this.targetWindowXTitle || "").trim().toLowerCase(),
      targetWindowGeneration: Number(this.targetWindowGeneration || 0),
    };
  },

  _targetXWindowMatchesSnapshot: function(snapshot, completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let expectedGeneration = snapshot && snapshot.targetWindowGeneration !== undefined
      ? Number(snapshot.targetWindowGeneration)
      : null;
    let generationMatches = () => expectedGeneration === null ||
      (isFinite(expectedGeneration) && expectedGeneration === Number(this.targetWindowGeneration || 0));
    if (!generationMatches()) {
      complete(false);
      return false;
    }
    if (!snapshot || !snapshot.xid) {
      if (snapshot && snapshot.window && this._isUsableTargetWindow(snapshot.window)) {
        let matches = Boolean(global.display && global.display.focus_window === snapshot.window) && generationMatches();
        complete(matches);
        return matches;
      }
      complete(false);
      return false;
    }
    let xid = String(snapshot.xid || "").trim();
    if (!/^[0-9]+$/.test(xid)) {
      complete(false);
      return false;
    }
    let deadlineMs = Date.now() + X11_COMMAND_TIMEOUT_MS;
    this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (activeOutput) => {
      if (!generationMatches()) {
        complete(false);
        return;
      }
      if (String(activeOutput || "").trim() !== xid) {
        complete(false);
        return;
      }
      let expectedClass = String(snapshot.windowClass || "").trim().toLowerCase();
      if (expectedClass === "") {
        this._targetXWindowMatchesSnapshotTitle(snapshot, xid, complete, deadlineMs);
        return;
      }
      this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (classOutput) => {
        if (!generationMatches()) {
          complete(false);
          return;
        }
        if (String(classOutput || "").trim().toLowerCase() !== expectedClass) {
          complete(false);
          return;
        }
        this._targetXWindowMatchesSnapshotTitle(snapshot, xid, complete, deadlineMs);
      }, Math.max(1, deadlineMs - Date.now()));
    }, Math.max(1, deadlineMs - Date.now()));
    return true;
  },

  _targetXWindowMatchesSnapshotTitle: function(snapshot, xid, completionCallback, deadlineMs) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let expectedGeneration = snapshot && snapshot.targetWindowGeneration !== undefined
      ? Number(snapshot.targetWindowGeneration)
      : null;
    let generationMatches = () => expectedGeneration === null ||
      (isFinite(expectedGeneration) && expectedGeneration === Number(this.targetWindowGeneration || 0));
    if (!generationMatches()) {
      complete(false);
      return;
    }
    let expectedTitle = String(snapshot.windowTitle || "").trim().toLowerCase();
    if (expectedTitle === "") {
      complete(generationMatches());
      return;
    }
    this._xdotoolOutput(["getwindowname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (titleOutput) => {
      if (!generationMatches()) {
        complete(false);
        return;
      }
      let activeTitle = this._shortMenuText(String(titleOutput || "").trim(), 160).toLowerCase();
      complete(activeTitle === expectedTitle);
    }, Math.max(1, deadlineMs ? deadlineMs - Date.now() : X11_COMMAND_TIMEOUT_MS));
  },

  _windowProbeValue: function(window, methodName) {
    if (!window) {
      return "";
    }
    try {
      if (!window[methodName]) {
        return "";
      }
      return String(window[methodName]() || "").toLowerCase();
    } catch (err) {
      return "";
    }
  },

  _windowLooksLikeSpeedOfCinnamon: function(window) {
    let identityValues = [
      this._windowProbeValue(window, "get_wm_class"),
      this._windowProbeValue(window, "get_wm_class_instance"),
      this._windowProbeValue(window, "get_gtk_application_id")
    ];
    for (let i = 0; i < identityValues.length; i++) {
      let value = identityValues[i];
      for (let j = 0; j < TERMINAL_WINDOW_MARKERS.length; j++) {
        if (value.indexOf(TERMINAL_WINDOW_MARKERS[j]) >= 0) {
          return false;
        }
      }
    }
    let values = [
      this._windowProbeValue(window, "get_title"),
      identityValues[0],
      identityValues[1],
      identityValues[2]
    ];
    let markers = [
      "speed of cinnamon",
      "speed-of-cinnamon",
      UUID.toLowerCase()
    ];
    for (let i = 0; i < values.length; i++) {
      let value = values[i];
      for (let j = 0; j < markers.length; j++) {
        if (value.indexOf(markers[j]) >= 0) {
          return true;
        }
      }
    }
    return false;
  },

  _notifySelfProtectionBlocked: function(title, windowClass) {
    if (!this._lifecycleAllowsWork()) {
      return;
    }
    let key = "self-protection\n" + String(windowClass || "");
    let now = Date.now();
    if (key === this.selfProtectionNoticeKey && now - this.selfProtectionNoticeAtMs < SELF_PROTECTION_NOTICE_COOLDOWN_MS) {
      return;
    }
    this.selfProtectionNoticeKey = key;
    this.selfProtectionNoticeAtMs = now;
    let message = _("Auto-Submit self-protection blocked a protected target");
    this.lastMessage = message;
    this._updatePanel();
    this._notify(_("Speed of Cinnamon"), message, true);
  },

  _windowIdentityMatchesAutoPaste: function(marker) {
    let key = String(marker || "").trim().toLowerCase();
    let allowed = AUTO_PASTE_IDENTITY_MARKERS[key] || null;
    if (!allowed) {
      return false;
    }
    let values = [
      this._windowProbeValue(this.targetWindow, "get_wm_class"),
      this._windowProbeValue(this.targetWindow, "get_wm_class_instance"),
      this._windowProbeValue(this.targetWindow, "get_gtk_application_id"),
      String(this.targetWindowXClass || "").toLowerCase(),
      String(this.targetWindowXTitle || "").toLowerCase()
    ];
    for (let i = 0; i < values.length; i++) {
      let value = values[i];
      for (let j = 0; j < allowed.length; j++) {
        if (this._windowIdentityValueMatchesMarker(value, allowed[j])) {
          return true;
        }
      }
    }
    return false;
  },

  _windowIdentityValueMatchesMarker: function(value, marker) {
    let haystack = String(value || "").toLowerCase();
    let markerValue = String(marker || "").trim().toLowerCase();
    if (!haystack || !markerValue) {
      return false;
    }
    if (haystack.indexOf(markerValue) < 0) {
      return false;
    }
    if (markerValue.length > 3 || /[^a-z0-9]/.test(markerValue)) {
      return true;
    }
    let index = haystack.indexOf(markerValue);
    while (index >= 0) {
      let before = index === 0 ? "" : haystack[index - 1];
      let after = index + markerValue.length >= haystack.length ? "" : haystack[index + markerValue.length];
      let boundaryBefore = before === "" || /[^a-z0-9]/.test(before);
      let boundaryAfter = after === "" || /[^a-z0-9]/.test(after);
      if (boundaryBefore && boundaryAfter) {
        return true;
      }
      index = haystack.indexOf(markerValue, index + markerValue.length);
    }
    return false;
  },

  _markerAllowsAutoPasteIdentity: function(marker) {
    let key = String(marker || "").trim().toLowerCase();
    if (!key) {
      return false;
    }
    if (!AUTO_PASTE_IDENTITY_MARKERS[key]) {
      return true;
    }
    return this._windowIdentityMatchesAutoPaste(marker);
  },

  _isTerminalTargetWindow: function() {
    if (!this._isUsableTargetWindow(this.targetWindow) && !this.targetWindowXClass && !this.targetWindowXTitle) {
      return false;
    }
    let values = [
      this._windowProbeValue(this.targetWindow, "get_wm_class"),
      this._windowProbeValue(this.targetWindow, "get_wm_class_instance"),
      this._windowProbeValue(this.targetWindow, "get_gtk_application_id"),
      String(this.targetWindowXClass || "").toLowerCase(),
      String(this.targetWindowXTitle || "").toLowerCase()
    ];
    for (let i = 0; i < values.length; i++) {
      let value = values[i];
      for (let j = 0; j < TERMINAL_WINDOW_MARKERS.length; j++) {
        if (value.indexOf(TERMINAL_WINDOW_MARKERS[j]) >= 0) {
          return true;
        }
      }
    }
    return false;
  },

  _clipboardProgramSpec: function() {
    if (this._findTrustedProgramInPath("xclip")) {
      return {
        program: "xclip",
        targetArgs: ["-selection", "clipboard", "-t", "TARGETS", "-out"],
      };
    }
    if (this._findTrustedProgramInPath("xsel")) {
      return {
        program: "xsel",
        targetArgs: ["--clipboard", "--output", "--target", "TARGETS"],
      };
    }
    if (this._findTrustedProgramInPath("wl-paste")) {
      return {
        program: "wl-paste",
        targetArgs: ["--list-types"],
      };
    }
    return null;
  },

  _clipboardPayloadArgs: function(spec, targetName) {
    if (!spec) {
      return null;
    }
    if (spec.program === "xclip") {
      return ["-selection", "clipboard", "-t", String(targetName || ""), "-out"];
    }
    if (spec.program === "xsel") {
      return ["--clipboard", "--output", "--target", String(targetName || "")];
    }
    return ["--type", String(targetName || "")];
  },

  _clipboardTargetList: function(program, args, completionCallback, timeoutMs) {
    args = args || [];
    let timeout = this._findTrustedProgramInPath("timeout");
    let helper = this._findTrustedProgramInPath(program);
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let completed = false;
    let completeOnce = (value) => {
      if (completed) {
        return;
      }
      completed = true;
      complete(value);
    };
    if (!timeout || !helper || !this._lifecycleAllowsWork()) {
      completeOnce(null);
      return false;
    }
    let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), helper];
    for (let i = 0; i < args.length; i++) {
      command.push(args[i]);
    }
    try {
      let handle = this._runBoundedSubprocess(this._coerceSpawnArgs(command), {}, {
        timeoutMs: Math.max(1, Number(timeoutMs || CLIPBOARD_COMMAND_TIMEOUT_MS)),
        minimumTimeoutMs: 1,
        maxStdoutBytes: MAX_CLIPBOARD_TARGET_OUTPUT_BYTES,
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        resourceGroup: "clipboard",
      }, (stdout, stderr, result) => {
        if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
          completeOnce(null);
          return;
        }
        completeOnce(String(stdout || ""));
      });
      if (!handle) {
        completeOnce(null);
      }
      return Boolean(handle);
    } catch (error) {
      this._recordLifecycleError("clipboard-command", error);
      completeOnce(null);
      return false;
    }
  },

  _clipboardNonTextPayloadTargets: function(targets) {
    let ignored = {
      targets: true,
      multiple: true,
      timestamp: true,
      save_targets: true,
    };
    let knownTextTargets = {
      "compound_text": true,
      text: true,
      string: true,
      utf8_string: true,
    };
    if (targets === null || targets === undefined) {
      return ["clipboard"];
    }
    if (String(targets || "").trim() === "") {
      return ["clipboard"];
    }
    let lines = String(targets || "").split("\n");
    let nonTextTargets = [];
    for (let i = 0; i < lines.length; i++) {
      let rawTarget = String(lines[i]).trim().toLowerCase();
      let target = rawTarget.split(";", 1)[0];
      if (!target || ignored[target]) {
        continue;
      }
      if (NON_TEXT_TEXT_CLIPBOARD_TARGETS[target]) {
        nonTextTargets.push(target);
        continue;
      }
      if (knownTextTargets[target] || target.indexOf("text/") === 0) {
        continue;
      }
      nonTextTargets.push(target);
    }
    return nonTextTargets;
  },

  _clipboardTargetsContainNonTextPayload: function(targets) {
    return this._clipboardNonTextPayloadTargets(targets).length > 0;
  },

  _clipboardHasNonTextPayload: function(completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    this._clipboardPayloadSnapshotAsync((snapshot) => {
      complete(!snapshot || snapshot.hasNonTextPayload !== false);
    });
  },

  _clipboardUnknownPayloadSnapshot: function() {
    return {
      signature: "unknown",
      hasNonTextPayload: true,
      description: _("clipboard contents"),
      payloadFingerprint: "unknown",
    };
  },

  _clipboardPayloadSnapshot: function() {
    return this._clipboardUnknownPayloadSnapshot();
  },

  _clipboardPayloadSnapshotAsync: function(completionCallback) {
    let guardedComplete = this._guardStateCallback("clipboard-query", completionCallback, this._clipboardUnknownPayloadSnapshot()) || function() {};
    let completed = false;
    let complete = (snapshot) => {
      if (completed) {
        return;
      }
      completed = true;
      guardedComplete(snapshot);
    };
    let unknown = () => complete(this._clipboardUnknownPayloadSnapshot());
    let spec;
    try {
      spec = this._clipboardProgramSpec();
    } catch (error) {
      this._recordLifecycleError("clipboard-query", error);
      unknown();
      return false;
    }
    if (!spec) {
      unknown();
      return false;
    }
    let deadlineMs = Date.now() + CLIPBOARD_COMMAND_TIMEOUT_MS;
    try {
      this._clipboardTargetList(spec.program, spec.targetArgs, (targets) => {
        try {
          if (targets === null || targets === undefined) {
            unknown();
            return;
          }
          let targetText = String(targets || "");
          let targetLines = targetText.split("\n").filter((line) => String(line || "").trim() !== "");
          if (targetLines.length > CLIPBOARD_MAX_TARGETS) {
            unknown();
            return;
          }
          let nonTextTargets = this._clipboardNonTextPayloadTargets(targetText);
          if (nonTextTargets.length > CLIPBOARD_MAX_TARGETS) {
            unknown();
            return;
          }
          this._clipboardPayloadFingerprintFromTargetsAsync(spec, targetText, (payloadFingerprint) => {
            try {
              if (payloadFingerprint === "unknown") {
                unknown();
                return;
              }
              complete({
                signature: targetText,
                hasNonTextPayload: nonTextTargets.length > 0,
                payloadFingerprint: payloadFingerprint,
                description: this._clipboardPayloadDescriptionFromTargets(targetText),
              });
            } catch (error) {
              this._recordLifecycleError("clipboard-query", error);
              unknown();
            }
          }, deadlineMs);
        } catch (error) {
          this._recordLifecycleError("clipboard-query", error);
          unknown();
        }
      }, Math.max(1, deadlineMs - Date.now()));
    } catch (error) {
      this._recordLifecycleError("clipboard-query", error);
      unknown();
      return false;
    }
    return true;
  },

  _clipboardPayloadFingerprintFromTargetsAsync: function(spec, targets, completionCallback, deadlineMs) {
    let callback = typeof completionCallback === "function" ? completionCallback : function() {};
    let completed = false;
    let complete = (value) => {
      if (completed) {
        return;
      }
      completed = true;
      try {
        callback(value);
      } catch (error) {
        this._recordLifecycleError("clipboard-query-completion", error);
      }
    };
    let fail = (error) => {
      if (error) {
        this._recordLifecycleError("clipboard-query", error);
      }
      complete("unknown");
    };
    let nonTextTargets;
    try {
      nonTextTargets = this._clipboardNonTextPayloadTargets(targets);
      if (!Array.isArray(nonTextTargets) || nonTextTargets.length === 0) {
        complete("no-nontext");
        return;
      }
    } catch (error) {
      fail(error);
      return;
    }
    let fingerprints = [];
    let sortedTargets;
    try {
      sortedTargets = nonTextTargets.slice().sort().slice(0, CLIPBOARD_MAX_TARGETS);
    } catch (error) {
      fail(error);
      return;
    }
    let readNext = (index) => {
      try {
        if (index >= sortedTargets.length) {
          complete(fingerprints.join("|"));
          return;
        }
        if (deadlineMs && Date.now() >= deadlineMs) {
          complete("unknown");
          return;
        }
        let targetName = String(sortedTargets[index] || "");
        this._clipboardTargetList(spec.program, this._clipboardPayloadArgs(spec, targetName), (payload) => {
          try {
            if (payload === null || payload === undefined) {
              complete("unknown");
              return;
            }
            fingerprints.push(this._clipboardPayloadFingerprintFromText(String(payload), targetName));
            readNext(index + 1);
          } catch (error) {
            fail(error);
          }
        }, Math.max(1, deadlineMs ? deadlineMs - Date.now() : CLIPBOARD_COMMAND_TIMEOUT_MS));
      } catch (error) {
        fail(error);
      }
    };
    try {
      readNext(0);
    } catch (error) {
      fail(error);
    }
  },

  _clipboardPayloadFingerprintFromText: function(payload, targetLabel) {
    let data = String(payload || "");
    try {
      return String(targetLabel || "") + ":sha256:" + String(GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, data, -1));
    } catch (err) {
      return String(targetLabel || "") + ":unavailable";
    }
  },

  _clipboardPayloadSignaturesMatch: function(snapshotA, snapshotB) {
    if (!snapshotA || !snapshotB) {
      return false;
    }
    if (snapshotA.signature === "unknown" || snapshotB.signature === "unknown") {
      return false;
    }
    if (snapshotA.payloadFingerprint === "unknown" || snapshotB.payloadFingerprint === "unknown") {
      return false;
    }
    return (
      String(snapshotA.signature) === String(snapshotB.signature) &&
      String(snapshotA.payloadFingerprint) === String(snapshotB.payloadFingerprint)
    );
  },

  _setClipboardOverwriteApproval: function(snapshot) {
    if (!snapshot || snapshot.signature === "unknown" || snapshot.payloadFingerprint === "unknown") {
      return;
    }
    this._clipboardOverwriteApproval = {
      signature: String(snapshot.signature || ""),
      payloadFingerprint: String(snapshot.payloadFingerprint || ""),
      expiresAtMs: Date.now() + CLIPBOARD_OVERWRITE_APPROVAL_TTL_MS,
    };
  },

  _hasValidClipboardOverwriteApproval: function(snapshot) {
    if (!this._clipboardOverwriteApproval) {
      return false;
    }
    if (Date.now() > this._clipboardOverwriteApproval.expiresAtMs) {
      this._clearClipboardOverwriteApproval();
      return false;
    }
    if (!snapshot || snapshot.signature === "unknown" || snapshot.payloadFingerprint === "unknown") {
      return false;
    }
    return (
      String(this._clipboardOverwriteApproval.signature) === String(snapshot.signature) &&
      String(this._clipboardOverwriteApproval.payloadFingerprint) === String(snapshot.payloadFingerprint)
    );
  },

  _clearClipboardOverwriteApproval: function() {
    this._clipboardOverwriteApproval = null;
  },

  _setClipboardText: function(text) {
    if (typeof text !== "string" || !this._lifecycleAllowsWork()) {
      return false;
    }
    try {
      if (!this.clipboard || !this.clipboard.set_text) {
        return false;
      }
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
      return true;
    } catch (error) {
      this._recordLifecycleError("clipboard-set", error);
      return false;
    }
  },

  _describeNonTextClipboardPayload: function(completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    this._clipboardPayloadSnapshotAsync((snapshot) => {
      complete(snapshot && snapshot.description ? snapshot.description : _("clipboard contents"));
    });
  },

  _clipboardPayloadDescriptionFromTargets: function(targets) {
    let nonTextTargets = this._clipboardNonTextPayloadTargets(targets);
    if (nonTextTargets.length === 0) {
      return _("text");
    }
    let description = nonTextTargets.slice(0, 6).join(", ");
    if (nonTextTargets.length > 6) {
      description += ", +" + String(nonTextTargets.length - 6);
    }
    return this._shortMenuText(description, 160);
  },

  _copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    let completionFinished = false;
    let completeOnce = (result) => {
      if (completionFinished) {
        return;
      }
      completionFinished = true;
      if (typeof completionCallback === "function") {
        completionCallback(result === true);
      }
    };
    if (!isCurrentOperation()) {
      completeOnce(false);
      return false;
    }
    if (method === "clipboard") {
      if (!this._setClipboardText(text)) {
        this._setStatus("error", _("Could not copy to clipboard"), transcript);
        completeOnce(false);
        return false;
      }
      this._setStatus("done", _("Copied to clipboard"), transcript);
      return true;
    }
    if (!canPasteWithKeyboard) {
      if (!this._setClipboardText(text)) {
        this._setStatus("error", _("Could not copy to clipboard"), transcript);
        completeOnce(false);
        return false;
      }
      this._setStatus("done", _("Copied to clipboard; install xdotool or wtype for automatic paste"), transcript);
      return true;
    }
    if (!this._closeMenuForKeyboardInsert()) {
      this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);
      completeOnce(false);
      return false;
    }
    this._restoreTargetWindowForPaste((restored) => {
      if (!isCurrentOperation()) {
        completeOnce(false);
        return;
      }
      if (!restored) {
        this._setClipboardText(text);
        this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);
        completeOnce(false);
        return;
      }
      if (!isCurrentOperation()) {
        completeOnce(false);
        return;
      }
      if (!this._setClipboardText(text)) {
        this._setStatus("error", _("Could not copy to clipboard"), transcript);
        completeOnce(false);
        return;
      }
      if (!this._pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {
        if (!isCurrentOperation()) {
          completeOnce(false);
          return;
        }
        if (completed) {
          this._setStatus("done", _("Copied and pasted into target window"), transcript);
        }
        completeOnce(completed === true);
      }, isCurrentOperation)) {
        this._setStatus("error", _("Copied to clipboard; automatic paste command could not be started"), transcript);
        completeOnce(false);
      }
    });
    return null;
  },

  _confirmClipboardOverwriteForPaste: function(clipboardSnapshot, transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    if (!isCurrentOperation()) {
      if (typeof completionCallback === "function") completionCallback(false);
      return;
    }
    let nonTextDescription = clipboardSnapshot && clipboardSnapshot.description ? clipboardSnapshot.description : _("unknown");
    let originalClipboardSignature = clipboardSnapshot && clipboardSnapshot.signature ? clipboardSnapshot.signature : "unknown";
    let originalPayloadFingerprint = clipboardSnapshot && clipboardSnapshot.payloadFingerprint ? clipboardSnapshot.payloadFingerprint : "unknown";
    if (originalClipboardSignature === "unknown" || originalPayloadFingerprint === "unknown") {
      this._setStatus("ready", _("Clipboard state unavailable; overwrite cancelled"), transcript);
      if (typeof completionCallback === "function") {
        completionCallback(false);
      }
      return;
    }
    let message = _("Clipboard contains non-text payload (%s).").replace("%s", String(nonTextDescription || _("unknown")));
    let dialog = this._newSafeDialog("clipboard-overwrite");
    let completed = false;
    let complete = (result) => {
      if (completed) {
        return;
      }
      completed = true;
      if (typeof completionCallback === "function") {
        completionCallback(result === true);
      }
    };
    if (!dialog || !this._dialogAddChild(dialog, this._newSafeLabel(message, { x_expand: true }, "clipboard-overwrite"), "clipboard-overwrite") ||
      !this._dialogAddChild(dialog, this._newSafeLabel(_("Replace clipboard content and continue paste?"), { x_expand: true }, "clipboard-overwrite"), "clipboard-overwrite")) {
      this._dialogClose(dialog, "clipboard-overwrite");
      this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);
      complete(false);
      return;
    }
    if (!this._dialogSetButtons(dialog, [
      {
        label: _("Cancel"),
        key: Clutter.KEY_Escape,
        action: function() {
          try {
            this._dialogClose(dialog, "clipboard-overwrite");
            if (isCurrentOperation()) {
              this._setStatus("ready", _("Clipboard overwrite cancelled"), transcript);
            }
          } finally {
            complete(false);
          }
        }.bind(this),
      },
      {
        label: _("Overwrite clipboard"),
        action: function() {
          try {
            this._dialogClose(dialog, "clipboard-overwrite");
            if (!isCurrentOperation()) {
              complete(false);
              return;
            }
            this._clipboardPayloadSnapshotAsync((currentClipboardSnapshot) => {
              try {
                if (!isCurrentOperation()) {
                  complete(false);
                  return;
                }
                if (!this._clipboardPayloadSignaturesMatch(clipboardSnapshot, currentClipboardSnapshot)) {
                  this._setStatus("ready", _("Clipboard changed; overwrite cancelled"), transcript);
                  complete(false);
                  return;
                }
                this._setClipboardOverwriteApproval(currentClipboardSnapshot);
                let result = this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, complete, operationGuard);
                if (result !== null) {
                  complete(result);
                }
              } catch (error) {
                this._recordLifecycleError("clipboard-overwrite", error);
                complete(false);
              }
            });
          } catch (error) {
            this._recordLifecycleError("clipboard-overwrite", error);
            complete(false);
          }
        }.bind(this),
      }
    ], "clipboard-overwrite")) {
      this._dialogClose(dialog, "clipboard-overwrite");
      this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);
      complete(false);
      return;
    }
    if (!this._dialogOpen(dialog, "clipboard-overwrite")) {
      this._dialogClose(dialog, "clipboard-overwrite");
      this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);
      complete(false);
    }
  },

  _pasteClipboardAfterFocus: function(sendEnter, expectedClipboardText, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    if (!isCurrentOperation()) {
      if (typeof completionCallback === "function") completionCallback(false);
      return false;
    }
    let terminalPaste = this._isTerminalTargetWindow();
    let expectedTargetWindow = this._targetXWindowSnapshot();
    if (!expectedTargetWindow) {
      this._setStatus("error", _("Target window unavailable for automatic paste"), this.lastTranscript);
      return false;
    }
    let hasXdotool;
    let hasWtype;
    try {
      hasXdotool = this._findTrustedProgramInPath("xdotool");
      hasWtype = this._findTrustedProgramInPath("wtype");
    } catch (error) {
      this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
      return false;
    }
    let args = null;
    let followUpArgs = null;
    if (hasXdotool) {
      let pasteKey = terminalPaste ? "ctrl+shift+v" : "ctrl+v";
      args = [hasXdotool, "key", "--clearmodifiers", pasteKey];
      if (sendEnter) {
        followUpArgs = [hasXdotool, "key", "--clearmodifiers", "Return"];
      }
    } else if (hasWtype) {
      args = terminalPaste
        ? [hasWtype, "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]
        : [hasWtype, "-M", "ctrl", "v", "-m", "ctrl"];
      if (sendEnter) {
        followUpArgs = [hasWtype, "-k", "Return"];
      }
    }
    if (!args) {
      return false;
    }
    return this._spawnKeyboardAfterFocus(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, isCurrentOperation);
  },

  _typeTextAfterFocus: function(text, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    if (!isCurrentOperation()) {
      if (typeof completionCallback === "function") completionCallback(false);
      return false;
    }
    let delay = this._normalizeTypingDelayMs(this.typingDelayMs);
    let typedText = this._coerceTypeText(text);
    if (typedText === null) {
      return false;
    }
    let xdotool;
    try {
      xdotool = this._findTrustedProgramInPath("xdotool");
    } catch (error) {
      this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
      return false;
    }
    if (!xdotool) {
      return false;
    }
    let expectedTargetWindow = this._targetXWindowSnapshot();
    if (!expectedTargetWindow) {
      this._setStatus("error", _("Target window unavailable for direct typing"), this.lastTranscript);
      return false;
    }
    return this._spawnKeyboardAfterFocus([xdotool, "type", "--clearmodifiers", "--delay", String(delay), "--", typedText], null, null, expectedTargetWindow, completionCallback, isCurrentOperation);
  },

  _coerceTypeText: function(text) {
    if (typeof text !== "string") {
      this._setStatus("error", _("Text for direct typing is invalid"), this.lastTranscript);
      return null;
    }
    let value = text;
    if (value.indexOf("\u0000") >= 0) {
      value = value.replace(/\u0000/g, "");
    }
    if (value.length > MAX_TYPE_COMMAND_CHARS) {
      this._setStatus("error", _("Text too long for keyboard typing"), this.lastTranscript);
      return null;
    }
    return value;
  },

  _completeKeyboardInsertFailure: function(completionCallback, message, error) {
    if (error) {
      this._recordLifecycleError("keyboard-insert", error);
    }
    try {
      this._setStatus("error", message || _("Keyboard insert failed"), this.lastTranscript);
    } catch (statusError) {
      this._recordLifecycleError("keyboard-insert-status", statusError);
    }
    if (typeof completionCallback === "function") {
      try {
        completionCallback(false);
      } catch (callbackError) {
        this._recordLifecycleError("keyboard-insert-completion", callbackError);
      }
    }
  },

  _spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    this._clearPasteTimer();
    let completed = false;
    let complete = (result) => {
      if (completed) {
        return;
      }
      completed = true;
      if (typeof completionCallback === "function") {
        completionCallback(result === true);
      }
    };
    if (this.appletRemoved || !isCurrentOperation()) {
      complete(false);
      return false;
    }
    if (!this._scheduleTrackedTimer("paste", PASTE_FOCUS_DELAY_MS, () => {
      if (this.appletRemoved || !isCurrentOperation()) {
        complete(false);
        return false;
      }
      try {
        this._spawnKeyboardWhenClipboardReady(args, followUpArgs, expectedClipboardText, Date.now() + CLIPBOARD_READY_TIMEOUT_MS, expectedTargetWindow, complete, isCurrentOperation);
      } catch (error) {
        this._completeKeyboardInsertFailure(complete, _("Keyboard insert failed"), error);
      }
      return false;
    }, false, "pasteTimer")) {
      this._setStatus("error", _("Keyboard insert failed: timer could not be scheduled"), this.lastTranscript);
      complete(false);
      return false;
    }
    return true;
  },

  _spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    let failAsync = (error, message) => this._completeKeyboardInsertFailure(
      completionCallback,
      message || _("Keyboard insert failed"),
      error
    );
    if (!isCurrentOperation() || !this._lifecycleAllowsWork()) {
      if (typeof completionCallback === "function") completionCallback(false);
      return;
    }
    if (expectedClipboardText === undefined || expectedClipboardText === null) {
      try {
        this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, null, null, completionCallback, isCurrentOperation);
      } catch (error) {
        failAsync(error);
      }
      return;
    }
    let expected = String(expectedClipboardText);
    try {
      if (!this.clipboard || !this.clipboard.get_text) {
        if (typeof completionCallback === "function") completionCallback(false);
        return;
      }
      this.clipboard.get_text(St.ClipboardType.CLIPBOARD, this._guardStateCallback("clipboard-read", (clipboard, clipboardText) => {
        try {
          if (this.appletRemoved || !isCurrentOperation()) {
            if (typeof completionCallback === "function") {
              completionCallback(false);
            }
            return;
          }
          if (String(clipboardText || "") === expected) {
            try {
              this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, expected, deadlineMs, completionCallback, isCurrentOperation);
            } catch (error) {
              failAsync(error);
            }
            return;
          }
          if (Date.now() >= deadlineMs) {
            this._setStatus("error", _("Clipboard did not confirm new text before automatic paste"), this.lastTranscript);
            if (typeof completionCallback === "function") {
              completionCallback(false);
            }
            return;
          }
          if (!this._scheduleTrackedTimer("paste", CLIPBOARD_READY_RETRY_MS, () => {
            if (this.appletRemoved || !isCurrentOperation()) {
              if (typeof completionCallback === "function") {
                completionCallback(false);
              }
              return false;
            }
            try {
              this._spawnKeyboardWhenClipboardReady(args, followUpArgs, expected, deadlineMs, expectedTargetWindow, completionCallback, isCurrentOperation);
            } catch (error) {
              failAsync(error);
            }
            return false;
          }, false, "pasteTimer")) {
            this._setStatus("error", _("Keyboard insert failed: retry timer could not be scheduled"), this.lastTranscript);
            if (typeof completionCallback === "function") {
              completionCallback(false);
            }
          }
        } catch (error) {
          failAsync(error);
        }
      }, undefined));
    } catch (err) {
      this._completeKeyboardInsertFailure(
        completionCallback,
        _("Clipboard could not be verified before automatic paste"),
        err
      );
    }
  },

  _spawnKeyboardProcess: function(args, completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let completed = false;
    let completeOnce = (result) => {
      if (completed) {
        return;
      }
      completed = true;
      complete(result === true);
    };
    if (!this._lifecycleAllowsWork()) {
      completeOnce(false);
      return false;
    }
    try {
      let handle = this._runBoundedSubprocess(this._coerceSpawnArgs(args), {}, {
        timeoutMs: X11_COMMAND_TIMEOUT_MS,
        maxStdoutBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        resourceGroup: "keyboard",
      }, (stdout, stderr, result) => {
        completeOnce(!(result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)));
      });
      if (!handle) {
        completeOnce(false);
        return false;
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("keyboard-process", error);
      completeOnce(false);
      return false;
    }
  },

  _spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback, operationGuard) {
    let isCurrentOperation = typeof operationGuard === "function" ? operationGuard : function() { return true; };
    let failAsync = (error, message) => this._completeKeyboardInsertFailure(
      completionCallback,
      message || _("Keyboard insert failed"),
      error
    );
    if (!isCurrentOperation() || !this._lifecycleAllowsWork()) {
      if (typeof completionCallback === "function") completionCallback(false);
      return;
    }
    if (expectedClipboardText !== undefined && expectedClipboardText !== null) {
      let expected = String(expectedClipboardText);
      try {
        if (!this.clipboard || !this.clipboard.get_text) {
          if (typeof completionCallback === "function") completionCallback(false);
          return;
        }
        this.clipboard.get_text(St.ClipboardType.CLIPBOARD, this._guardStateCallback("clipboard-read", (clipboard, clipboardText) => {
          try {
            if (this.appletRemoved || !isCurrentOperation()) {
              if (typeof completionCallback === "function") {
                completionCallback(false);
              }
              return;
            }
            if (String(clipboardText || "") !== expected) {
              if (expectedClipboardDeadlineMs && Date.now() >= expectedClipboardDeadlineMs) {
                this._setStatus("error", _("Clipboard changed before automatic paste"), this.lastTranscript);
                if (typeof completionCallback === "function") {
                  completionCallback(false);
                }
                return;
              }
              if (!this._scheduleTrackedTimer("paste", CLIPBOARD_READY_RETRY_MS, () => {
                if (this.appletRemoved || !isCurrentOperation()) {
                  if (typeof completionCallback === "function") {
                    completionCallback(false);
                  }
                  return false;
                }
                try {
                  this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, expected, expectedClipboardDeadlineMs, completionCallback, isCurrentOperation);
                } catch (error) {
                  failAsync(error);
                }
                return false;
              }, false, "pasteTimer")) {
                if (typeof completionCallback === "function") {
                  completionCallback(false);
                }
              }
              return;
            }
            try {
              this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, null, null, completionCallback, isCurrentOperation);
            } catch (error) {
              failAsync(error);
            }
          } catch (error) {
            failAsync(error);
          }
        }, undefined));
      } catch (error) {
        if (!isCurrentOperation() || !this._lifecycleAllowsWork()) {
          if (typeof completionCallback === "function") {
            completionCallback(false);
          }
          return;
        }
        failAsync(error, _("Clipboard changed before automatic paste"));
      }
      return;
    }
    if (!expectedTargetWindow) {
      this._setStatus("error", _("Target window unavailable for automatic paste"), this.lastTranscript);
      if (typeof completionCallback === "function") {
        completionCallback(false);
      }
      return;
    }
    let fail = (message) => {
      if (message) {
        this._setStatus("error", message, this.lastTranscript);
      }
      if (typeof completionCallback === "function") {
        completionCallback(false);
      }
    };
    this._targetXWindowMatchesSnapshot(expectedTargetWindow, (matches) => {
      if (!isCurrentOperation()) {
        fail();
        return;
      }
      if (!matches) {
        fail(_("Target window changed before automatic paste"));
        return;
      }
      if (!this._spawnKeyboardProcess(args, (firstCompleted) => {
        if (!firstCompleted) {
          fail(_("Keyboard insert failed"));
          return;
        }
        if (!isCurrentOperation()) {
          fail();
          return;
        }
        if (!followUpArgs) {
          if (typeof completionCallback === "function") completionCallback(true);
          return;
        }
        if (!this._scheduleTrackedTimer("paste", PASTE_SUBMIT_DELAY_MS, () => {
          if (this.appletRemoved || !isCurrentOperation()) {
            if (typeof completionCallback === "function") completionCallback(false);
            return false;
          }
          this._targetXWindowMatchesSnapshot(expectedTargetWindow, (submitTargetMatches) => {
            if (!isCurrentOperation()) {
              fail();
              return;
            }
            if (!submitTargetMatches) {
              fail(_("Target window changed before automatic submit"));
              return;
            }
            if (!this._spawnKeyboardProcess(followUpArgs, (submitCompleted) => {
              if (!isCurrentOperation()) {
                fail();
                return;
              }
              if (!submitCompleted) {
                fail(_("Keyboard insert failed"));
                return;
              }
              if (typeof completionCallback === "function") completionCallback(true);
            })) {
              fail(_("Keyboard insert failed"));
            }
          });
          return false;
        }, false, "pasteTimer")) {
          fail(_("Keyboard insert failed: submit timer could not be scheduled"));
        }
      })) {
        fail(_("Keyboard insert failed"));
      }
    });
  },

  _finishAppletTextInsert: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    let transcript = String(payload.transcript || "");
    if (this._isEmptyTranscriptText(transcript)) {
      this._finishEmptyRelistenDone(payload);
      return;
    }
    let insertFingerprint = this._autoInsertFingerprint(payload, transcript);
    let reservation = this._reserveAutoInsertFingerprint(insertFingerprint);
    if (reservation === null) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatusPreservingRecording("error", _("Could not prepare transcript insertion"), transcript);
      return;
    }
    if (!reservation) {
      this._setStatus("done", this._payloadMessage(payload, _("Transcript already inserted")), transcript);
      this._finishPendingRelisten();
      return;
    }
    let inserted = false;
    if (payload.inserted === true) {
      inserted = true;
      this._setStatus("done", this._payloadMessage(payload, _("Transcript already inserted by backend")), transcript);
    } else {
      let result;
      try {
        result = this._insertTranscriptText(transcript, (completed) => {
          if (!completed) {
            this._forgetAutoInsertFingerprint(insertFingerprint);
            this.autoRelistenPending = false;
            this.autoRelistenPendingToken = "";
            this.autoRelistenManualStopRequested = true;
            return;
          }
          this._finishPendingRelisten();
        });
      } catch (error) {
        this._recordLifecycleError("payload-insert", error);
        this._forgetAutoInsertFingerprint(insertFingerprint);
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenManualStopRequested = true;
        this._setStatusPreservingRecording("error", _("Could not insert transcript"), transcript);
        return;
      }
      if (result === null) {
        return;
      }
      if (result) {
        inserted = true;
      }
    }
    if (!inserted) {
      this._forgetAutoInsertFingerprint(insertFingerprint);
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      return;
    }
    this._finishPendingRelisten();
  },

  _ensureAutoRelistenPendingForDonePayload: function(payload) {
    if (this.autoRelistenManualStopRequested) {
      return;
    }
    if (this.autoRelistenPending || !this.autoRelisten || !this.notificationSessionActive) {
      return;
    }
    let marker = this._payloadStringMarker(payload, ["audio_path", "audio", "transcript_path", "stopped_at", "started_at"], "done");
    this.autoRelistenSequence += 1;
    this.autoRelistenPending = true;
    this.autoRelistenPendingToken = String(this.autoRelistenSequence) + ":done:" + marker;
  },

  _finishPendingRelisten: function() {
    let shouldRelisten = this.autoRelistenPending;
    let previousNotificationSessionActive = this.notificationSessionActive;
    let relistenStarted = false;
    if (shouldRelisten) {
      this.notificationSessionActive = true;
      relistenStarted = this._restartRelistenRecording();
    }
    if (relistenStarted) {
      this.notificationSessionActive = true;
    } else if (shouldRelisten) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = false;
      this.notificationSessionActive = previousNotificationSessionActive;
    } else {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = false;
    }
    return relistenStarted;
  },

  _transcriptDigest: function(transcript) {
    let text = String(transcript || "");
    try {
      return "sha256:" + GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, text, -1);
    } catch (err) {
      return "digest-unavailable";
    }
  },

  _autoInsertFingerprint: function(payload, transcript) {
    let rawTranscript = String(transcript || "");
    let marker = this._payloadStringMarker(payload, ["audio_path", "audio", "transcript_path"], "");
    let digest = this._transcriptDigest(rawTranscript);
    if (marker === "") {
      marker = this._payloadStringMarker(payload, ["started_at", "stopped_at"], "");
    }
    if (marker === "") {
      return "len:" + String(rawTranscript.length) + ":" + digest;
    }
    let compactMarker = String(marker).trim();
    return compactMarker + "|" + String(rawTranscript.length) + "|" + digest;
  },

  _resetAutoInsertFingerprint: function() {
    this.autoInsertFingerprint = "";
    this.autoInsertFingerprints = [];
  },

  _hasAutoInsertFingerprint: function(fingerprint) {
    if (!fingerprint) {
      return false;
    }
    if (fingerprint === this.autoInsertFingerprint) {
      return true;
    }
    return this.autoInsertFingerprints && this.autoInsertFingerprints.indexOf(fingerprint) >= 0;
  },

  _reserveAutoInsertFingerprint: function(fingerprint) {
    if (!fingerprint) {
      return true;
    }
    try {
      if (this._hasAutoInsertFingerprint(fingerprint)) {
        return false;
      }
      if (!this._rememberAutoInsertFingerprint(fingerprint)) {
        return null;
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("auto-insert-fingerprint", error);
      return null;
    }
  },

  _rememberAutoInsertFingerprint: function(fingerprint) {
    if (!fingerprint) {
      return true;
    }
    let previousFingerprint = this.autoInsertFingerprint;
    try {
      this.autoInsertFingerprint = fingerprint;
      if (!this.autoInsertFingerprints) {
        this.autoInsertFingerprints = [];
      }
      if (this.autoInsertFingerprints.indexOf(fingerprint) < 0) {
        this.autoInsertFingerprints.push(fingerprint);
      }
      while (this.autoInsertFingerprints.length > 20) {
        this.autoInsertFingerprints.shift();
      }
      return true;
    } catch (error) {
      try {
        this.autoInsertFingerprint = previousFingerprint;
      } catch (rollbackError) {
        this._recordLifecycleError("auto-insert-fingerprint-rollback", rollbackError);
      }
      this._recordLifecycleError("auto-insert-fingerprint", error);
      return false;
    }
  },

  _forgetAutoInsertFingerprint: function(fingerprint) {
    if (!fingerprint || !this.autoInsertFingerprints) {
      return;
    }
    try {
      let index = this.autoInsertFingerprints.indexOf(fingerprint);
      if (index >= 0) {
        this.autoInsertFingerprints.splice(index, 1);
      }
      if (this.autoInsertFingerprint === fingerprint) {
        this.autoInsertFingerprint = this.autoInsertFingerprints.length > 0
          ? this.autoInsertFingerprints[this.autoInsertFingerprints.length - 1]
          : "";
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("auto-insert-fingerprint", error);
      return false;
    }
  },

  _finishSilentRelistenSkip: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    if (this._finishPendingRelisten()) {
      return;
    }
    this._setStatus("done", this._payloadMessage(payload, _("Silent recording skipped")), this.lastTranscript);
  },

  _finishEmptyRelistenDone: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    if (this._finishPendingRelisten()) {
      return;
    }
    this._setStatus("done", this._payloadMessage(payload, _("Recording finished without transcript")), this.lastTranscript);
  },

  _insertTranscriptText: function(transcript, completionCallback) {
    if (!this._lifecycleAllowsWork() || this.textInsertToken) {
      return false;
    }
    let method = this._normalizeOutputMethod(this.insertMethod);
    let autoPasteTarget = this._windowTitleMatchesAutoPaste();
    let canPasteWithKeyboard = this._findTrustedProgramInPath("xdotool") || this._findTrustedProgramInPath("wtype");
    let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;
    let suppressAutoPasteEnter = method !== "clipboard-paste" || submitWithReturn;
    let text = this._preparedTranscriptText(transcript, suppressAutoPasteEnter);
    let insertToken = {};
    let insertTargetGeneration = Number(this.targetWindowGeneration || 0);
    this.textInsertToken = insertToken;
    let isCurrentInsert = () => this.textInsertToken === insertToken &&
      insertTargetGeneration === Number(this.targetWindowGeneration || 0) &&
      this._lifecycleAllowsWork();
    let release = () => {
      if (this.textInsertToken === insertToken) {
        this.textInsertToken = null;
        return true;
      }
      return false;
    };
    let complete = (result) => {
      if (!release()) {
        return;
      }
      if (typeof completionCallback === "function") {
        completionCallback(result === true);
      }
    };
    let failPreparation = (error) => {
      if (this.textInsertToken !== insertToken) {
        return false;
      }
      release();
      this._recordLifecycleError("text-insert", error);
      this._setStatusPreservingRecording("error", _("Could not prepare text insertion"), this.lastTranscript);
      return false;
    };
    if (method === "none") {
      this._setStatus("done", _("Insertion disabled"), transcript);
      release();
      return true;
    }
    if (this._isEmptyTranscriptText(transcript) || this._isEmptyTranscriptText(text)) {
      this._setStatus("done", _("No transcript text to insert"), "");
      release();
      return true;
    }
    if (method === "type") {
      try {
        if (this._findTrustedProgramInPath("xdotool")) {
          if (!this._closeMenuForKeyboardInsert()) {
            this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);
            release();
            return false;
          }
          this._restoreTargetWindowForPaste((restored) => {
            if (!isCurrentInsert()) {
              complete(false);
              return;
            }
            if (!restored) {
              this._setStatus("error", _("Target window unavailable for direct typing"), transcript);
              complete(false);
              return;
            }
            if (!this._typeTextAfterFocus(text, (completed) => {
              if (completed && isCurrentInsert()) {
                this._setStatus("done", _("Typed into target window"), transcript);
              }
              complete(completed === true);
            }, isCurrentInsert)) {
              complete(false);
            }
          });
          return null;
        } else {
          this._setStatus("error", _("Install xdotool for direct typing"), transcript);
        }
      } catch (error) {
        return failPreparation(error);
      }
      release();
      return false;
    }
    if (method === "clipboard-paste" && !canPasteWithKeyboard) {
      this._setStatus("error", _("Clipboard-paste requires a keyboard helper (xdotool or wtype)"), transcript);
      release();
      return false;
    }
    if (method !== "clipboard-paste") {
      try {
        let result = this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, complete, isCurrentInsert);
        if (result !== null) {
          release();
        }
        return result;
      } catch (error) {
        return failPreparation(error);
      }
    }
    try {
      this._clipboardPayloadSnapshotAsync((clipboardSnapshot) => {
        try {
          if (!isCurrentInsert()) {
            complete(false);
            return;
          }
          if (clipboardSnapshot.hasNonTextPayload) {
            if (this._hasValidClipboardOverwriteApproval(clipboardSnapshot)) {
              this._clearClipboardOverwriteApproval();
              let result = this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, complete, isCurrentInsert);
              if (result !== null) {
                release();
              }
              return;
            }
            this._clearClipboardOverwriteApproval();
            this._confirmClipboardOverwriteForPaste(
              clipboardSnapshot,
              transcript,
              text,
              method,
              canPasteWithKeyboard,
              submitWithReturn,
              complete,
              isCurrentInsert
            );
            return;
          }
          let result = this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, complete, isCurrentInsert);
          if (result !== null) {
            release();
          }
        } catch (error) {
          failPreparation(error);
        }
      });
    } catch (error) {
      return failPreparation(error);
    }
    return null;
  },

  _restartRelistenRecording: function() {
    if (!this.notificationSessionActive || this.isCommandRunning) {
      return false;
    }
    if (!this.autoRelisten) {
      return false;
    }
    if (!this._ensureVoiceModelCompatibleWithCurrentLanguage(true)) {
      return false;
    }
    let startArgs;
    try {
      startArgs = this._baseArgs("start");
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatus("error", _("Could not prepare relisten command: ") + safeError, this.lastTranscript);
      return false;
    }
    this.isCommandRunning = true;
    this.autoTranscribeRecordingKey = "";
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this._setStatus("processing", _("Starting next recording..."), this.lastTranscript);
    this._spawnJson(startArgs, (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
        return;
      }
      let nextStatus = this._normalizePayloadStatus(payload && payload.status, Boolean(payload && payload.error));
      if (nextStatus === "recording" || nextStatus === "recorded") {
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
      }
      this._applyPayloadSafely(payload);
    });
    return true;
  },

  _preparedTranscriptText: function(transcript, suppressAutoPasteEnter) {
    let text = String(transcript || "");
    let autoPasteEnter = this._windowTitleMatchesAutoPaste();
    if (!this.sanitizeSpecialChars && !this.appendSpace && !autoPasteEnter && text.length <= MAX_TEXT_INSERT_CHARS && text.indexOf("\u0000") < 0) {
      return text;
    }
    if (this.sanitizeSpecialChars) {
      text = this._sanitizeSpecialChars(text);
    }
    if (text.indexOf("\u0000") >= 0) {
      text = text.replace(NUL_RE, "");
    }
    if (text.length > MAX_TEXT_INSERT_CHARS) {
      text = text.slice(0, MAX_TEXT_INSERT_CHARS);
    }
    if (this.appendSpace && text && !" \t\n\r\f\v".includes(text[text.length - 1])) {
      text += " ";
    }
    if (autoPasteEnter && !suppressAutoPasteEnter && text && text[text.length - 1] !== "\n") {
      text += "\n";
    }
    return text;
  },

  _sanitizeSpecialChars: function(text) {
    return String(text || "").replace(NON_ASCII_RE, (char) => {
      let replacement = SANITIZE_SPECIAL_CHAR_MAP[char];
      if (replacement !== undefined) {
        return replacement;
      }
      let normalized = char.normalize("NFKD").replace(COMBINING_MARKS_RE, "");
      return ASCII_ONLY_RE.test(normalized) ? normalized : char;
    });
  },

  _copyLastTranscript: function() {
    if (!this.lastTranscript) {
      this._setStatusPreservingRecording(this.status, _("No transcript yet"), this.lastTranscript);
      return;
    }
    if (!this._setClipboardText(this._preparedTranscriptText(this.lastTranscript, true))) {
      this._setStatusPreservingRecording("error", _("Could not copy last transcript"), this.lastTranscript);
      return;
    }
    this._setStatusPreservingRecording("done", _("Copied last transcript"), this.lastTranscript);
  },

  _insertLastTranscript: function() {
    if (this._hasActiveRecordingState()) {
      this._setStatusPreservingRecording("ready", _("Finish the current recording before inserting another transcript"), this.lastTranscript);
      return;
    }
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
    transcripts = Array.isArray(transcripts) ? transcripts : [];
    transcripts = transcripts.filter((transcript) => {
      if (!transcript || typeof transcript !== "object") {
        return false;
      }
      let preview = typeof transcript.preview === "string" ? transcript.preview.trim() : "";
      let name = typeof transcript.name === "string" ? transcript.name.trim() : "";
      let text = typeof transcript.text === "string" ? transcript.text.trim() : "";
      return preview !== "" || name !== "" || text !== "";
    });
    let historyWasTruncated = transcripts.length > MAX_HISTORY_MENU_ENTRIES;
    if (historyWasTruncated) {
      transcripts = transcripts.slice(0, MAX_HISTORY_MENU_ENTRIES);
    }
    this._clearMenuItems(this.historyItem.menu);
    if (!transcripts || transcripts.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No transcripts yet"));
      empty.setSensitive(false);
      this.historyItem.menu.addMenuItem(empty);
      return;
    }
    for (let transcript of transcripts) {
      if (!transcript || typeof transcript !== "object") {
        continue;
      }
      let preview = typeof transcript.preview === "string" ? transcript.preview.trim() : "";
      let name = typeof transcript.name === "string" ? transcript.name.trim() : "";
      let transcriptText = typeof transcript.text === "string" ? transcript.text : "";
      let hasTranscriptText = transcriptText !== "" && !this._isEmptyTranscriptText(transcriptText);
      let label = this._shortMenuText(preview || name || _("Transcript"), 80);
      let entry = new PopupMenu.PopupSubMenuMenuItem(label);
      this.historyItem.menu.addMenuItem(entry);

      let insertItem = new PopupMenu.PopupIconMenuItem(_("Insert transcript"), "edit-paste-symbolic", St.IconType.SYMBOLIC);
      insertItem.setSensitive(hasTranscriptText);
      this._connectSafe(insertItem, "activate", () => this._insertHistoryTranscript(transcriptText));
      entry.menu.addMenuItem(insertItem);

      let copyItem = new PopupMenu.PopupIconMenuItem(_("Copy transcript"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
      copyItem.setSensitive(hasTranscriptText);
      this._connectSafe(copyItem, "activate", () => this._copyHistoryTranscript(transcriptText));
      entry.menu.addMenuItem(copyItem);
      if (!hasTranscriptText) {
        entry.menu.addMenuItem(this._selectionInfoItem(_("Transcript content hidden; use List all Transcripts")));
      }
    }
    if (historyWasTruncated) {
      this.historyItem.menu.addMenuItem(this._selectionInfoItem(_("Transcript list truncated for safety")));
    }
  },

  _copyHistoryTranscript: function(text) {
    if (!text) {
      return;
    }
    let statusTranscript = this._hasActiveRecordingState() ? "" : text;
    if (!this._setClipboardText(this._preparedTranscriptText(text, true))) {
      this._setStatusPreservingRecording("error", _("Could not copy transcript"), statusTranscript);
      return;
    }
    this._setStatusPreservingRecording("done", _("Copied transcript"), statusTranscript);
  },

  _insertHistoryTranscript: function(text) {
    if (!text) {
      return;
    }
    if (this._hasActiveRecordingState()) {
      this._setStatusPreservingRecording("ready", _("Finish the current recording before inserting another transcript"), this.lastTranscript);
      return;
    }
    this._insertTranscriptText(text);
  },

  _setStatusPreservingRecording: function(status, message, transcript) {
    if (!this._lifecycleAllowsWork()) {
      return;
    }
    if (!this._hasActiveRecordingState()) {
      this._setStatus(status, message, transcript);
      return;
    }
    try {
      let safeMessage = typeof message === "string" ? message : "";
      this.lastMessage = status === "error"
        ? this._uiMessageText(this._sanitizeErrorMessage(safeMessage))
        : this._uiMessageText(safeMessage);
      if (typeof transcript === "string" && transcript !== "") {
        this.lastTranscript = transcript;
      }
      this._updatePanel();
    } catch (error) {
      this._recordLifecycleError("status-update", error);
    }
  },

  _setStatus: function(status, message, transcript) {
    if (!this._lifecycleAllowsWork()) {
      return;
    }
    try {
      // A local UI transition supersedes any status response that is still in flight.
      this._statusRefreshToken++;
      let previousStatus = this.status;
      this.status = status;
      let safeMessage = (typeof message === "string" ? message : "");
      this.lastMessage = status === "error"
        ? this._uiMessageText(this._sanitizeErrorMessage(safeMessage))
        : this._uiMessageText(safeMessage);
      if (typeof transcript === "string" && transcript !== "") {
        this.lastTranscript = transcript;
      }
      if (this.copyLastItem) {
        this.copyLastItem.setSensitive(Boolean(this.lastTranscript));
      }
      if (this.insertLastItem) {
        this.insertLastItem.setSensitive(Boolean(this.lastTranscript));
      }
      if (this.cancelItem) {
        this.cancelItem.setSensitive(this._hasCancelableRecordingWork());
      }
      this._updatePanel();
      this._maybeNotify(previousStatus, this.status, this.lastMessage);
      this._scheduleStatusPoll();
      this._scheduleDisplayTick();
    } catch (error) {
      this._recordLifecycleError("status-update", error);
    }
  },

  _maybeNotify: function(previousStatus, status, message) {
    if (!this._lifecycleAllowsWork() || !this.notificationSessionActive || status === "processing") {
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
    if (!this._lifecycleAllowsWork()) {
      return;
    }
    try {
      let safeBody = this._sanitizeErrorMessage(body);
      if (critical && Main.criticalNotify) {
        Main.criticalNotify(title, safeBody);
      } else if (Main.notify) {
        Main.notify(title, safeBody);
      }
    } catch (err) {
      this._safeLogError(err);
    }
  },

  _shortTranscript: function() {
    if (!this.lastTranscript) {
      return _("No transcript yet");
    }
    let transcriptLength = String(this.lastTranscript).length;
    return _("Transcript preview hidden (length: ") + String(transcriptLength) + " chars)";
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
    let maxSeconds = this._normalizeRecordingLimit(
      this.recordingMaxSeconds !== undefined && this.recordingMaxSeconds !== null ? this.recordingMaxSeconds : this.maxSeconds
    );
    let elapsed = this._recordingElapsedSeconds();
    if (maxSeconds > 0) {
      elapsed = Math.min(elapsed, maxSeconds);
      return this._formatSeconds(elapsed) + " / " + this._formatSeconds(maxSeconds);
    }
    return this._formatSeconds(elapsed);
  },

  _microphoneLevelText: function() {
    if (this.status !== "recording" && this.status !== "recorded") {
      return _("Microphone: idle");
    }
    let level = this.microphoneLevel || {};
    if (level.ok !== true) {
      return _("Microphone: ") + String(level.detail || _("waiting for audio"));
    }
    let percent = typeof level.percent === "number" && isFinite(level.percent) ? level.percent : 0;
    percent = Math.max(0, Math.min(100, Math.round(percent)));
    return _("Microphone: ") + String(percent) + "% " + this._levelBar(percent);
  },

  _levelBar: function(percent) {
    let filled = Math.max(0, Math.min(10, Math.round(Number(percent || 0) / 10)));
    return "[" + "#".repeat(filled) + "-".repeat(10 - filled) + "]";
  },

  _recordingOptionsLabel: function() {
    let timeout = this.autoTranscribeTimeout ? _("auto stop") : _("manual stop");
    let relisten = this.autoRelisten ? _("relisten") : _("no relisten");
    let artifacts = this.keepRecordingArtifacts ? _("keep files") : _("discard files");
    return _("Recording: ") + timeout + ", " + relisten + ", " + artifacts;
  },

  _notificationOptionsLabel: function() {
    let enabled = [];
    if (this.notifyRecording) enabled.push(_("recording"));
    if (this.notifyComplete) enabled.push(_("done"));
    if (this.notifyError) enabled.push(_("errors"));
    return _("Notifications: ") + (enabled.length > 0 ? enabled.join(", ") : _("off"));
  },

  _textOptionsLabel: function() {
    let space = this.appendSpace ? _("space") : _("no space");
    let accents = this.sanitizeSpecialChars ? _("ASCII") : _("accents");
    return _("Text: ") + space + ", " + accents;
  },

  _inputSourceLabel: function() {
    let value = String(this.inputDevice || "").trim();
    if (value === "") {
      return _("Input: system default");
    }
    return _("Input: ") + (value.length > 32 ? value.slice(0, 29) + "..." : value);
  },

  _voiceBackendLabel: function() {
    let backend = String(this.transcriber || "auto");
    let model = String(this.whisperModel || "").trim();
    if ((backend === "whisper-cpp" || backend === "faster-whisper") && model !== "") {
      return _("Voice: ") + this._shortMenuText(GLib.path_get_basename(model), 96);
    }
    if (backend === "command") return _("Voice: custom command");
    if (backend === "whisper") return _("Voice: Whisper command");
    if (backend === "openai-compatible") {
      let externalModel = String(this.openaiCompatibleModel || "").trim() || _("not configured");
      return _("Voice: External API ") + this._shortMenuText(externalModel, 96);
    }
    if (backend === "whisper-cpp") return _("Voice: local model file");
    if (backend === "faster-whisper") return _("Voice: local model directory");
    return _("Voice: automatic");
  },

  _textModelLabel: function() {
    let backend = String(this.postProcessBackend || "none");
    if (backend === "none") return _("Text model: disabled");
    if (backend === "ollama") {
      let ollamaModel = String(this.ollamaModel || "").trim() || _("Ollama");
      return _("Text model: ") + this._shortMenuText(ollamaModel, 96);
    }
    if (backend === "openai-compatible") {
      let externalModel = String(this.openaiCompatibleTextModel || this.openaiCompatibleModel || "").trim() || _("OpenAI-compatible");
      return _("Text model: ") + this._shortMenuText(externalModel, 96);
    }
    return _("Text model: custom command");
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
    return this._runGuarded("panel-style", () => {
      if (!this.actor || !this.actor.add_style_class_name || !this.actor.remove_style_class_name) {
        return;
      }
      for (let styleClass of PANEL_STATUS_CLASSES) {
        this.actor.remove_style_class_name(styleClass);
      }
      this.actor.add_style_class_name(this._panelStyleClassForStatus(status));
    }, undefined);
  },

  _updatePanel: function() {
    return this._runGuarded("panel-update", () => {
      let label = "";
      let tooltip = "Speed of Cinnamon";
      let statusText = this.status || "idle";
      if (this.status === "recording") {
        let progress = this._recordingProgressText();
        let mic = this._microphoneLevelText();
        label = "REC " + this._formatSeconds(this._recordingElapsedSeconds());
        tooltip = _("Recording...") + " " + progress + "\n" + mic;
        statusText = "recording " + progress + "; " + mic;
        if (this.toggleItem) this.toggleItem.label.text = _("Stop dictation");
      } else if (this.status === "processing") {
        label = "...";
        tooltip = this.lastMessage || _("Processing...");
        if (this.toggleItem) this.toggleItem.label.text = _("Working...");
      } else if (this.status === "error") {
        label = "ERR";
        tooltip = this.lastMessage || _("Error");
        statusText = "error";
        if (this.lastMessage) {
          statusText += " - " + this._shortMenuText(this.lastMessage, 140);
        }
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
      if (this.microphoneLevelItem) {
        this.microphoneLevelItem.label.text = this._microphoneLevelText();
      }
      if (this.doctorSummaryItem) {
        this.doctorSummaryItem.label.text = this.doctorSummaryText || _("Doctor: not checked");
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
      if (this.recordingOptionsItem) {
        this.recordingOptionsItem.label.text = this._recordingOptionsLabel();
      }
      if (this.notificationOptionsItem) {
        this.notificationOptionsItem.label.text = this._notificationOptionsLabel();
      }
      if (this.outputMethodItem) {
        this.outputMethodItem.label.text = _("Output: ") + this._outputMethodLabel(this._normalizeOutputMethod(this.insertMethod));
      }
      if (this.textOptionsItem) {
        this.textOptionsItem.label.text = this._textOptionsLabel();
      }
      this._updateAutoPasteItem();
      this._updateTranscriptStorageItem();
      if (this.inputSourceItem) {
        this.inputSourceItem.label.text = this._inputSourceLabel();
      }
      if (this.modelItem) {
        this.modelItem.label.text = this._voiceBackendLabel();
      }
      if (this.textModelItem) {
        this.textModelItem.label.text = this._textModelLabel();
      }
      if (this.transcriptItem) {
        this.transcriptItem.label.text = this._shortTranscript();
      }
    }, undefined);
  }
};

function main(metadata, orientation, panelHeight, instanceId) {
  return new MyApplet(metadata, orientation, panelHeight, instanceId);
}
