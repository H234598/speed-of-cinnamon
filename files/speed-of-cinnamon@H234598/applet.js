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
const HOTKEY_REBIND_MAX_RETRIES = 3;
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
const SELF_PROTECTION_NOTICE_COOLDOWN_MS = 180000;
const CLIPBOARD_OVERWRITE_APPROVAL_TTL_MS = 5000;
const CLIPBOARD_TARGET_TIMEOUT_SECONDS = 1;
const CLIPBOARD_COMMAND_TIMEOUT_MS = 1500;
const CLIPBOARD_PAYLOAD_FINGERPRINT_MAX_BUDGET_MS = 6000;
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
const CLI_RUNTIME_TEXT_LIMITS = {
  "input device": 256,
  "ollama URL": 2048,
  "ollama model": 240,
  "openai-compatible URL": 2048,
  "openai-compatible model": 240,
  "openai-compatible text model": 240,
  "whisper model": 240
};
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
const DEFAULT_AUTO_PASTE_TITLE = "codex";
const AUTO_PASTE_TITLE_PRESETS = [
  "codex",
  "Terminal",
  "PDF",
  "Excel",
  "Telegram",
  "Teams",
  "Obsidian"
];

function _decodeSubprocessOutputChunks(chunks) {
  let totalBytes = 0;
  for (let chunk of chunks || []) {
    if (!chunk || typeof chunk.length !== "number" || !isFinite(chunk.length) || chunk.length < 0) {
      throw new Error("Subprocess output chunk is invalid");
    }
    totalBytes += chunk.length;
  }
  let contents = new Uint8Array(totalBytes);
  let offset = 0;
  for (let chunk of chunks || []) {
    contents.set(new Uint8Array(chunk), offset);
    offset += chunk.length;
  }
  return ByteArray.toString(contents);
}
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
  ],
  "obsidian": [
    "md.obsidian.obsidian",
    "obsidian",
    "obsidian.appimage",
    "obsidian.desktop",
    "md.obsidian.obsidian.desktop",
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
  "show-transcript-text": true,
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
const STATUS_ICON_ALLOWLIST = {};
[
  "ready",
  "recording",
  "processing",
  "recorded",
  "error",
  "setup"
].forEach((family) => {
  for (let i = 1; i <= 51; i++) {
    let index = (i < 10) ? "0" + String(i) : String(i);
    STATUS_ICON_ALLOWLIST[family + "-" + index] = true;
  }
});
STATUS_ICON_ALLOWLIST["soc-original"] = true;
const STATUS_ICON_DEFAULTS = {
  ready: "soc-original",
  recording: "soc-original",
  processing: "soc-original",
  recorded: "soc-original",
  error: "soc-original",
  setup: "soc-original"
};
const EXPORTABLE_SETTINGS = [
  ["toggle-keybinding", "toggleKeybinding"],
  ["primary-language-keybinding", "primaryLanguageKeybinding"],
  ["secondary-language-keybinding", "secondaryLanguageKeybinding"],
  ["cancel-keybinding", "cancelKeybinding"],
  ["show-panel-label", "showPanelLabel"],
  ["show-transcript-text", "showTranscriptText"],
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
  ["status-icon-ready", "statusIconReady"],
  ["status-icon-recording", "statusIconRecording"],
  ["status-icon-processing", "statusIconProcessing"],
  ["status-icon-recorded", "statusIconRecorded"],
  ["status-icon-error", "statusIconError"],
  ["status-icon-setup", "statusIconSetup"],
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
    this._orphanedSignals = [];
    this._orphanedHotkeys = [];
    this._orphanedProcesses = [];
    this._orphanedTimers = [];
    this._orphanedDialogs = [];
    this._orphanedMonitors = [];
    this._orphanedCancellables = [];
    this._orphanedTooltip = false;
    this._orphanedMenus = [];
    this._hotkeyDefinitions = {};
    this._orphanedHotkeyStates = {};
    this._pendingHotkeyRebinds = {};
    this._blockedHotkeyIds = {};
    this._hotkeyCallbacks = {};
    this._hotkeyCallbacks[HOTKEY_ID] = () => {
      if (this._blockedHotkeyIds && this._blockedHotkeyIds[HOTKEY_ID] === true) {
        return;
      }
      if (!this._hasActiveRecordingState() && !this.isCommandRunning && !this._rememberFocusedWindow()) {
        return;
      }
      this._toggleRecording();
    };
    this._hotkeyCallbacks[PRIMARY_HOTKEY_ID] = () => {
      if (this._blockedHotkeyIds && this._blockedHotkeyIds[PRIMARY_HOTKEY_ID] === true) {
        return;
      }
      this._startWithLanguage(this._primaryLanguage());
    };
    this._hotkeyCallbacks[SECONDARY_HOTKEY_ID] = () => {
      if (this._blockedHotkeyIds && this._blockedHotkeyIds[SECONDARY_HOTKEY_ID] === true) {
        return;
      }
      this._startWithLanguage(this._secondaryLanguage());
    };
    this._hotkeyCallbacks[CANCEL_HOTKEY_ID] = () => {
      if (this._blockedHotkeyIds && this._blockedHotkeyIds[CANCEL_HOTKEY_ID] === true) {
        return;
      }
      this._cancelRecording();
    };
    this.autoInsertPendingFingerprint = "";
    this._teardownComplete = false;
    this._initFailed = false;
    this.appletRemoved = false;
    this.spawnGeneration = 0;
    this.targetWindowGeneration = 0;
    this.targetWindowXPendingGeneration = 0;
    this.terminalWorkflowToken = null;
    this.doctorCommandToken = null;
    this.settingsWindowToken = null;
    this._cleanupCommandToken = null;
    this._recordingCommandToken = null;
    this.processCleanupRetryTimer = 0;
    this._externalApiEnvMonitorCancelSucceeded = false;
    this.maintenanceCleanupFailed = false;
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
    this._runTeardownGuarded("init-teardown", () => this.on_applet_removed_from_panel());
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
    let bound = this.settings.bindProperty(direction, key, propertyName, safeCallback, callbackThis);
    if (bound !== true) {
      throw new Error("Setting binding failed: " + String(key || "unknown"));
    }
    return true;
  },

  _setSettingValueOrThrow: function(key, value, errorMessage) {
    let settingKey = String(key || "");
    let settingsData = this.settings && this.settings.settingsData;
    if (!settingsData || !Object.prototype.hasOwnProperty.call(settingsData, settingKey)) {
      throw new Error(errorMessage || ("Setting is unavailable: " + (settingKey || "unknown")));
    }
    let result = this.settings.setValue(settingKey, value);
    if (result === false) {
      throw new Error(errorMessage || "Setting could not be saved");
    }
    return true;
  },

  _commitSettingValue: function(propertyName, key, value, group, errorMessage) {
    let previous = this[propertyName];
    try {
      this._setSettingValueOrThrow(key, value, "Setting could not be saved");
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

  _rollbackSettingsBatch: function(writes) {
    if (!Array.isArray(writes)) {
      return false;
    }
    let rollbackSucceeded = true;
    for (let index = writes.length - 1; index >= 0; index--) {
      let setting = writes[index];
      if (!Array.isArray(setting) || setting.length < 3) {
        rollbackSucceeded = false;
        this._recordLifecycleError("settings-rollback", new Error("Setting rollback batch is invalid"));
        continue;
      }
      try {
        this._setSettingValueOrThrow(setting[0], setting[2], "Setting rollback failed");
      } catch (err) {
        rollbackSucceeded = false;
        this._safeLogError(err);
        this._recordLifecycleError("settings-rollback", err);
      }
    }
    return rollbackSucceeded;
  },

  _commitSettingsBatch: function(writes, group, errorMessage, preserveRecording, result) {
    let setStatus = preserveRecording === false
      ? this._setStatus.bind(this)
      : this._setStatusPreservingRecording.bind(this);
    if (result && typeof result === "object") {
      result.rollbackSucceeded = true;
    }
    if (!Array.isArray(writes)) {
      return false;
    }
    let attemptedWrites = [];
    try {
      for (let setting of writes) {
        if (!Array.isArray(setting) || setting.length < 3) {
          throw new Error("Setting batch is invalid");
        }
        attemptedWrites.push(setting);
        this._setSettingValueOrThrow(setting[0], setting[1], "Setting batch could not be saved");
      }
      return true;
    } catch (err) {
      let rollbackSucceeded = this._rollbackSettingsBatch(attemptedWrites);
      if (result && typeof result === "object") {
        result.rollbackSucceeded = rollbackSucceeded;
      }
      this._recordLifecycleError(group || "settings-batch", err);
      if (!rollbackSucceeded) {
        this._recordLifecycleError(
          String(group || "settings-batch") + "-rollback",
          new Error("Setting batch rollback failed")
        );
      }
      if (errorMessage) {
        setStatus("error", errorMessage, this.lastTranscript);
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
      if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.signals)) {
        throw new Error("Signal registry is unavailable");
      }
      if (!Array.isArray(this._orphanedSignals)) {
        this._recordLifecycleError("signal-state", new Error("Signal orphan registry is unavailable"));
        return 0;
      }
      if (this._orphanedSignals.length > 0) {
        let orphanCleanupSucceeded = this._disconnectOrphanedSignals();
        if (!orphanCleanupSucceeded || this._orphanedSignals.length > 0) {
          this._recordLifecycleError("signal-state", new Error("An orphaned signal is still pending"));
          return 0;
        }
      }
      let connectionId = 0;
      connectionId = target.connect(signal, this._guardStateCallback(signalGroup, callback, undefined));
      if (connectionId) {
        let signalEntry = { target: target, id: connectionId };
        let registryWriteAttempted = false;
        let registryEntryRemovalSucceeded = false;
        try {
          registryWriteAttempted = true;
          this._resourceRegistry.signals.push(signalEntry);
          if (this._resourceRegistry.signals.indexOf(signalEntry) < 0) {
            throw new Error("Signal could not be registered");
          }
        } catch (registryError) {
          if (registryWriteAttempted) {
            try {
              let signals = this._resourceRegistry.signals;
              let index = signals.indexOf(signalEntry);
              if (index >= 0) {
                let removed = signals.splice(index, 1);
                if (!Array.isArray(removed) || removed.length !== 1 || signals.indexOf(signalEntry) >= 0) {
                  throw new Error("Signal registry rollback did not remove the entry");
                }
              }
              registryEntryRemovalSucceeded = true;
            } catch (rollbackError) {
              this._recordLifecycleError("signal-registration-rollback", rollbackError);
            }
          }
          let signalDisconnected = false;
          try {
            if (typeof target.disconnect !== "function") {
              throw new Error("Signal rollback disconnect is unavailable");
            }
            let disconnectResult = target.disconnect(connectionId);
            if (disconnectResult === false) {
              throw new Error("Signal rollback disconnect failed");
            }
            signalDisconnected = true;
          } catch (disconnectError) {
            this._recordLifecycleError("signal-disconnect", disconnectError);
          }
          let orphanTracked = true;
          if (!registryEntryRemovalSucceeded || !signalDisconnected) {
            orphanTracked = this._trackOrphanedSignal(target, connectionId, signalDisconnected) === true;
          }
          if (!signalDisconnected && !orphanTracked) {
            try {
              let signals = this._resourceRegistry && this._resourceRegistry.signals;
              if (!Array.isArray(signals)) {
                throw new Error("Signal registry fallback is unavailable");
              }
              let signalIndex = signals.indexOf(signalEntry);
              if (signalIndex < 0) {
                let restoreIndex = signals.length;
                signals[restoreIndex] = signalEntry;
                if (signals[restoreIndex] !== signalEntry) {
                  throw new Error("Signal registry fallback could not be restored");
                }
              }
            } catch (fallbackError) {
              this._recordLifecycleError("signal-registration-rollback", fallbackError);
            }
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

  _trackOrphanedSignal: function(target, id, disconnected) {
    try {
      if (!target || id === undefined || id === null) {
        throw new Error("Signal orphan is invalid");
      }
      if (!Array.isArray(this._orphanedSignals)) {
        this._orphanedSignals = [];
      }
      let knownEntry = this._orphanedSignals.find((entry) => entry && entry.target === target && entry.id === id);
      if (knownEntry) {
        if (disconnected === true) {
          knownEntry.disconnected = true;
        }
      } else {
        let entry = {
          target: target,
          id: id,
          disconnected: disconnected === true,
        };
        this._orphanedSignals.push(entry);
        if (this._orphanedSignals.indexOf(entry) < 0) {
          throw new Error("Signal orphan entry could not be tracked");
        }
      }
      if (this._lifecycleAllowsWork() && !this.processCleanupRetryTimer) {
        this._scheduleProcessCleanupRetry();
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("signal-orphan", error);
      return false;
    }
  },

  _untrackOrphanedSignal: function(connection) {
    if (!Array.isArray(this._orphanedSignals)) {
      return true;
    }
    let success = true;
    for (let index = this._orphanedSignals.length - 1; index >= 0; index--) {
      let entry = this._orphanedSignals[index];
      if (!entry || entry !== connection) {
        continue;
      }
      try {
        let removed = this._orphanedSignals.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedSignals.indexOf(entry) >= 0) {
          throw new Error("Signal orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("signal-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _disconnectOrphanedSignals: function(target) {
    let success = true;
    let inTeardown = !target && (this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED);
    if (inTeardown) {
      let signals = this._resourceRegistry && this._resourceRegistry.signals;
      if (!Array.isArray(signals)) {
        this._recordLifecycleError("signal-state", new Error("Signal registry is unavailable"));
        success = false;
      } else {
        for (let connection of signals) {
          if (!connection || typeof connection !== "object" || !connection.target ||
              connection.id === undefined || connection.id === null) {
            this._recordLifecycleError("signal-state", new Error("Signal registry entry is invalid"));
            success = false;
            continue;
          }
          if (!this._trackOrphanedSignal(connection.target, connection.id, false)) {
            success = false;
          }
        }
      }
    }
    if (!Array.isArray(this._orphanedSignals)) {
      this._recordLifecycleError("signal-state", new Error("Signal orphan registry is unavailable"));
      return false;
    }
    for (let index = this._orphanedSignals.length - 1; index >= 0; index--) {
      let connection = this._orphanedSignals[index];
      if (!connection || typeof connection !== "object" || !connection.target ||
          connection.id === undefined || connection.id === null) {
        this._recordLifecycleError("signal-orphan", new Error("Signal orphan entry is invalid"));
        success = false;
        continue;
      }
      if (target && connection.target !== target) {
        continue;
      }
      let disconnected = connection.disconnected === true;
      if (!disconnected) {
        disconnected = this._runTeardownOperation("teardown-orphaned-signals", connection.target, "disconnect", [connection.id]);
      }
      if (!disconnected) {
        success = false;
        continue;
      }
      if (!this._untrackSignal(connection.target, connection.id)) {
        success = false;
        continue;
      }
      if (!this._untrackOrphanedSignal(connection)) {
        success = false;
      }
    }
    return success;
  },

  _disconnectAllSignals: function() {
    let success = true;
    try {
      if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.signals)) {
        this._recordLifecycleError("signal-state", new Error("Signal registry is unavailable"));
        return this._disconnectOrphanedSignals() === true;
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
          if (!this._trackOrphanedSignal(connection && connection.target, connection && connection.id, false)) {
            success = false;
          }
          success = false;
          continue;
        }
        if (!this._untrackSignal(connection && connection.target, connection && connection.id, connection)) {
          if (!this._trackOrphanedSignal(connection && connection.target, connection && connection.id, true)) {
            success = false;
          }
          success = false;
        }
      }
      if (!this._disconnectOrphanedSignals()) {
        success = false;
      }
      if (Array.isArray(this._orphanedSignals) && this._orphanedSignals.length > 0) {
        this._recordLifecycleError("signal-state", new Error("Orphaned signals remain after teardown"));
        success = false;
      }
      return success;
    } catch (error) {
      this._recordLifecycleError("teardown-signals", error);
      return false;
    }
  },

  _disconnectTrackedSignalsForTarget: function(target) {
    if (!target) {
      return true;
    }
    if (!this._resourceRegistry) {
      this._recordLifecycleError("signal-state", new Error("Signal registry is unavailable"));
      return false;
    }
    let success = true;
    try {
      if (!Array.isArray(this._resourceRegistry.signals)) {
        this._recordLifecycleError("signal-state", new Error("Signal registry is unavailable"));
        return false;
      }
      let signals = this._resourceRegistry.signals;
      for (let index = signals.length - 1; index >= 0; index--) {
        let connection = signals[index];
        if (!connection || connection.target !== target) {
          continue;
        }
        if (!this._runTeardownOperation("teardown-target-signals", target, "disconnect", [connection.id])) {
          this._trackOrphanedSignal(target, connection.id, false);
          success = false;
          continue;
        }
        if (!this._untrackSignal(target, connection.id, connection)) {
          this._trackOrphanedSignal(target, connection.id, true);
          success = false;
        }
      }
      if (!this._disconnectOrphanedSignals(target)) {
        success = false;
      }
      return success;
    } catch (error) {
      this._recordLifecycleError("teardown-target-signals", error);
      return false;
    }
  },

  _untrackSignal: function(target, id, connection) {
    if (!this._resourceRegistry) {
      return true;
    }
    try {
      if (!Array.isArray(this._resourceRegistry.signals)) {
        return true;
      }
      let signals = this._resourceRegistry.signals;
      let success = true;
      for (let index = signals.length - 1; index >= 0; index--) {
        let entry = signals[index];
        if (!entry || (connection && entry !== connection) || (target && entry.target !== target) ||
            (id !== undefined && entry.id !== id)) {
          continue;
        }
        try {
          let removed = signals.splice(index, 1);
          if (!Array.isArray(removed) || removed.length !== 1 || signals.indexOf(entry) >= 0) {
            throw new Error("Signal registry entry could not be removed");
          }
        } catch (error) {
          this._recordLifecycleError("signal-untrack", error);
          success = false;
        }
      }
      return success;
    } catch (error) {
      this._recordLifecycleError("signal-untrack", error);
      return false;
    }
  },

  _closeNestedMenusSafely: function(menu) {
    if (!menu || typeof menu._getMenuItems !== "function") {
      return true;
    }
    let nestedMenus = [];
    let visited = [];
    let collect = (current) => {
      if (!current || visited.indexOf(current) >= 0) {
        return;
      }
      visited.push(current);
      let items = current._getMenuItems();
      if (!Array.isArray(items)) {
        throw new Error("Nested menu enumeration is unavailable");
      }
      for (let item of items) {
        let childMenu = item && item.menu;
        if (!childMenu) {
          continue;
        }
        if (nestedMenus.indexOf(childMenu) < 0) {
          nestedMenus.push(childMenu);
        }
        collect(childMenu);
      }
    };
    collect(menu);
    for (let index = nestedMenus.length - 1; index >= 0; index--) {
      if (!this._closeMenuSafely(nestedMenus[index], false, false)) {
        throw new Error("Nested menu could not be closed");
      }
    }
    return true;
  },

  _closeMenuSafely: function(menu, animate, requireGlobalMenuStack) {
    if (!menu || typeof menu.close !== "function") {
      throw new Error("Menu close operation is unavailable");
    }
    let actor = menu.actor;
    if (!actor || (typeof actor.is_finalized === "function" && actor.is_finalized())) {
      throw new Error("Menu actor is already finalized");
    }
    if (requireGlobalMenuStack === true && menu.isOpen === true) {
      let stack = typeof global !== "undefined" && global ? global.menuStack : null;
      let firstIndex = Array.isArray(stack) ? stack.indexOf(menu) : -1;
      if (!Array.isArray(stack) || firstIndex < 0 || firstIndex !== stack.lastIndexOf(menu)) {
        throw new Error("Open menu is missing or duplicated in Cinnamon menu stack");
      }
      this._closeNestedMenusSafely(menu);
      stack = typeof global !== "undefined" && global ? global.menuStack : null;
      firstIndex = Array.isArray(stack) ? stack.indexOf(menu) : -1;
      if (!Array.isArray(stack) || firstIndex !== stack.length - 1 || firstIndex !== stack.lastIndexOf(menu)) {
        throw new Error("Open menu is not topmost in Cinnamon menu stack");
      }
    } else if (requireGlobalMenuStack === true) {
      let stack = typeof global !== "undefined" && global ? global.menuStack : null;
      if (Array.isArray(stack) && stack.indexOf(menu) >= 0) {
        throw new Error("Closed menu remains in Cinnamon menu stack");
      }
    }
    if (requireGlobalMenuStack === true && menu.isOpen !== true) {
      this._closeNestedMenusSafely(menu);
    }
    let result = menu.close(animate);
    if (result === false) {
      throw new Error("Menu could not be closed");
    }
    return true;
  },

  _clearMenuItems: function(menu) {
    if (!menu) {
      return false;
    }
    try {
      let actor = menu.actor;
      if (!actor || (typeof actor.is_finalized === "function" && actor.is_finalized())) {
        throw new Error("Menu actor is unavailable or finalized");
      }
    } catch (error) {
      this._recordLifecycleError("menu-items", error);
      return false;
    }
    let targets = [];
    let nestedMenus = [];
    let visited = [];
    let collectionSucceeded = true;
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
        if (typeof current._getMenuItems !== "function") {
          throw new Error("Menu item enumeration is unavailable");
        }
        items = current._getMenuItems();
        if (!Array.isArray(items)) {
          throw new Error("Menu items are unavailable");
        }
        for (let item of items) {
          addTarget(item);
          if (item && item.menu) {
            addTarget(item.menu);
            if (nestedMenus.indexOf(item.menu) < 0) {
              nestedMenus.push(item.menu);
            }
            collect(item.menu);
          }
        }
      } catch (error) {
        this._recordLifecycleError("menu-items", error);
        collectionSucceeded = false;
      }
    };
    collect(menu);
    if (!collectionSucceeded) {
      return false;
    }
    let signalsCleanupSucceeded = true;
    for (let target of targets) {
      if (!this._disconnectTrackedSignalsForTarget(target)) {
        signalsCleanupSucceeded = false;
      }
    }
    if (!signalsCleanupSucceeded) {
      return false;
    }
    let nestedMenuCleanupSucceeded = true;
    for (let index = nestedMenus.length - 1; index >= 0; index--) {
      let nestedMenu = nestedMenus[index];
      if (this._runStateGuarded("menu-items", () => {
        return this._closeMenuSafely(nestedMenu, false, false);
      }, false) !== true) {
        nestedMenuCleanupSucceeded = false;
      }
    }
    if (!nestedMenuCleanupSucceeded) {
      return false;
    }
    return this._runStateGuarded("menu-items", () => {
      if (typeof menu.removeAll !== "function") {
        throw new Error("Menu remove operation is unavailable");
      }
      let result = menu.removeAll();
      if (result === false) {
        throw new Error("Menu items could not be removed");
      }
      return true;
    }, false) === true;
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
            let previousLength = dialogs.length;
            let removed = dialogs.pop();
            if (removed !== dialog || dialogs.length !== previousLength - 1) {
              throw new Error("Dialog registry rollback did not remove the entry");
            }
          } else {
            let index = dialogs.indexOf(dialog);
            if (index >= 0) {
              let previousLength = dialogs.length;
              let removed = dialogs.splice(index, 1);
              if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== dialog || dialogs.length !== previousLength - 1) {
                throw new Error("Dialog registry rollback did not remove the entry");
              }
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
      let removed = this._resourceRegistry.dialogs.splice(index, 1);
      if (!Array.isArray(removed) || removed.length !== 1 || this._resourceRegistry.dialogs.indexOf(dialog) >= 0) {
        throw new Error("Dialog registry entry could not be removed");
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("dialog-untrack", error);
      return false;
    }
  },

  _clearDialogReferences: function(dialog) {
    if (!dialog) {
      return;
    }
    if (this.clipboardOverwriteDialog === dialog) {
      this.clipboardOverwriteDialog = null;
      this._clearClipboardOverwriteApproval();
      let hadInsertToken = Boolean(this.textInsertToken);
      this.textInsertToken = null;
      let pendingInsertFingerprint = String(this.autoInsertPendingFingerprint || "");
      if (pendingInsertFingerprint !== "") {
        let fingerprintCleanupSucceeded = this._forgetAutoInsertFingerprint(pendingInsertFingerprint) !== false;
        if (fingerprintCleanupSucceeded && this.autoInsertPendingFingerprint === pendingInsertFingerprint) {
          this.autoInsertPendingFingerprint = "";
        }
        if (!fingerprintCleanupSucceeded) {
          this.textInsertCancellationFailed = true;
        }
      }
      if (hadInsertToken && this.autoRelistenPending) {
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenPendingLanguage = "";
        this.autoRelistenManualStopRequested = true;
      }
    }
    if (this.cleanupPreviewDialog === dialog) {
      this.cleanupPreviewDialog = null;
      this.cleanupPreviewDialogToken = null;
    }
    if (this.transcriptListPromptDialog === dialog) {
      this.transcriptListPromptDialog = null;
      this.transcriptListPromptToken = null;
    }
  },

  _dialogCloseState: function(dialog) {
    try {
      if (!ModalDialog || !ModalDialog.State) {
        return null;
      }
      if (dialog.state === ModalDialog.State.CLOSED || dialog.state === ModalDialog.State.FADED_OUT) {
        return "closed";
      }
      if (dialog.state === ModalDialog.State.CLOSING) {
        return "closing";
      }
    } catch (error) {
      this._recordLifecycleError("dialog-state", error);
    }
    return null;
  },

  _trackOrphanedDialog: function(dialog, group, closeSucceeded, destroySucceeded) {
    try {
      if (!dialog) {
        throw new Error("Dialog orphan is invalid");
      }
      if (!Array.isArray(this._orphanedDialogs)) {
        this._orphanedDialogs = [];
      }
      let normalizedCloseSucceeded = closeSucceeded === true;
      let normalizedDestroySucceeded = destroySucceeded === true;
      if (normalizedCloseSucceeded && !normalizedDestroySucceeded && this._dialogCloseState(dialog) === null) {
        normalizedCloseSucceeded = false;
      }
      let knownEntry = this._orphanedDialogs.find((entry) => entry && entry.dialog === dialog);
      if (knownEntry) {
        if (normalizedCloseSucceeded) {
          knownEntry.closeSucceeded = true;
        } else if (!normalizedDestroySucceeded) {
          knownEntry.closeSucceeded = false;
        }
        if (normalizedDestroySucceeded) {
          knownEntry.destroySucceeded = true;
        }
      } else {
        let entry = {
          dialog: dialog,
          group: String(group || "dialog"),
          closeSucceeded: normalizedCloseSucceeded,
          destroySucceeded: normalizedDestroySucceeded,
        };
        this._orphanedDialogs.push(entry);
        if (this._orphanedDialogs.indexOf(entry) < 0) {
          throw new Error("Dialog orphan entry could not be tracked");
        }
      }
      if (this._lifecycleAllowsWork() && !this.processCleanupRetryTimer) {
        this._scheduleProcessCleanupRetry();
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("dialog-orphan", error);
      return false;
    }
  },

  _untrackOrphanedDialog: function(dialog) {
    if (!Array.isArray(this._orphanedDialogs)) {
      return true;
    }
    let success = true;
    for (let index = this._orphanedDialogs.length - 1; index >= 0; index--) {
      let entry = this._orphanedDialogs[index];
      if (!entry || entry.dialog !== dialog) {
        continue;
      }
      try {
        let removed = this._orphanedDialogs.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedDialogs.indexOf(entry) >= 0) {
          throw new Error("Dialog orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("dialog-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedDialogs: function() {
    let pendingDialogs = [];
    let addPendingDialog = (dialog, group, closeSucceeded, destroySucceeded) => {
      if (!dialog) {
        return;
      }
      let knownEntry = pendingDialogs.find((entry) => entry && entry.dialog === dialog);
      if (knownEntry) {
        if (closeSucceeded === true) {
          knownEntry.closeSucceeded = true;
        }
        if (destroySucceeded === true) {
          knownEntry.destroySucceeded = true;
        }
        return;
      }
      pendingDialogs.push({
        dialog: dialog,
        group: String(group || "dialog"),
        closeSucceeded: closeSucceeded === true,
        destroySucceeded: destroySucceeded === true,
      });
    };
    let invalidOrphanEntry = false;
    if (Array.isArray(this._orphanedDialogs)) {
      for (let entry of this._orphanedDialogs) {
        if (!entry || !entry.dialog) {
          invalidOrphanEntry = true;
          continue;
        }
        addPendingDialog(entry.dialog, entry.group, entry.closeSucceeded, entry.destroySucceeded);
      }
    } else {
      this._recordLifecycleError("dialog-state", new Error("Dialog orphan registry is unavailable"));
    }
    let dialogs = this._resourceRegistry && this._resourceRegistry.dialogs;
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    if ((!Array.isArray(this._orphanedDialogs) || inTeardown) && Array.isArray(dialogs)) {
      for (let dialog of dialogs) {
        if (!dialog) {
          invalidOrphanEntry = true;
          continue;
        }
        addPendingDialog(dialog, "dialog-registry", false, false);
      }
    }
    if (pendingDialogs.length === 0) {
      return !invalidOrphanEntry && Array.isArray(this._orphanedDialogs);
    }
    let success = !invalidOrphanEntry;
    for (let index = pendingDialogs.length - 1; index >= 0; index--) {
      let entry = pendingDialogs[index];
      let closeSucceeded = entry.closeSucceeded === true;
      if (closeSucceeded && this._dialogCloseState(entry.dialog) === null) {
        closeSucceeded = false;
        entry.closeSucceeded = false;
      }
      if (!closeSucceeded) {
        closeSucceeded = this._runTeardownOperation(
          "teardown-orphaned-dialogs",
          entry.dialog,
          "close"
        );
        if (closeSucceeded) {
          entry.closeSucceeded = true;
        }
      }
      let destroySucceeded = entry.destroySucceeded === true;
      if (closeSucceeded && !destroySucceeded) {
        destroySucceeded = this._destroyDialogAfterClose(entry.dialog, "teardown-orphaned-dialogs");
        if (destroySucceeded) {
          entry.destroySucceeded = true;
        }
      }
      if (!closeSucceeded || !destroySucceeded) {
        success = false;
        continue;
      }
      if (!this._untrackDialog(entry.dialog)) {
        success = false;
        continue;
      }
      if (!this._untrackOrphanedDialog(entry.dialog)) {
        success = false;
      } else {
        this._clearDialogReferences(entry.dialog);
      }
    }
    return success;
  },

  _destroyDialogAfterClose: function(dialog, group) {
    if (!dialog) {
      return true;
    }
    try {
      let closeState = this._dialogCloseState(dialog);
      if (closeState === "closed") {
        return this._runTeardownOperation(group || "teardown-dialog-destroy", dialog, "destroy");
      }
      if (closeState === "closing") {
        // Cinnamon ModalDialog destroys itself after asynchronous close animation.
        return true;
      }
      throw new Error("Dialog did not enter a safe closing state");
    } catch (error) {
      this._recordLifecycleError("dialog-state", error);
      return false;
    }
  },

  _newSafeDialog: function(group) {
    if (!this._lifecycleAllowsWork()) {
      return null;
    }
    if (!Array.isArray(this._orphanedDialogs)) {
      this._recordLifecycleError("dialog-state", new Error("Dialog orphan registry is unavailable"));
      return null;
    }
    if (this._orphanedDialogs.length > 0) {
      let orphanCleanupSucceeded = this._retryOrphanedDialogs();
      if (!orphanCleanupSucceeded || this._orphanedDialogs.length > 0) {
        this._recordLifecycleError("dialog-state", new Error("An orphaned dialog is still pending"));
        return null;
      }
    }
    let dialog = null;
    try {
      dialog = new ModalDialog.ModalDialog();
      return this._trackDialog(dialog);
    } catch (error) {
      if (dialog) {
        let cleanupGroup = "dialog-" + String(group || "create") + "-cleanup";
        let closeSucceeded = this._runTeardownOperation(cleanupGroup, dialog, "close");
        let destroySucceeded = closeSucceeded && this._destroyDialogAfterClose(dialog, cleanupGroup);
        if (closeSucceeded && destroySucceeded) {
          if (!this._untrackDialog(dialog)) {
            this._trackOrphanedDialog(dialog, group, true, true);
          }
        } else {
          this._trackOrphanedDialog(dialog, group, closeSucceeded, destroySucceeded);
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
      let result = dialog.contentLayout.add_child(child);
      if (result === false) {
        throw new Error("Dialog child could not be added");
      }
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
      let result = dialog.setButtons(safeButtons);
      if (result === false) {
        throw new Error("Dialog buttons could not be set");
      }
      return true;
    }, false) === true;
  },

  _dialogClose: function(dialog, group) {
    if (!dialog) {
      return true;
    }
    try {
      if (this._resourceRegistry && Array.isArray(this._resourceRegistry.dialogs)) {
        if (this._resourceRegistry.dialogs.indexOf(dialog) < 0) {
          if (!Array.isArray(this._orphanedDialogs)) {
            this._recordLifecycleError("dialog-state", new Error("Dialog orphan registry is unavailable"));
            return false;
          }
          let isOrphaned = this._orphanedDialogs.some((entry) => entry && entry.dialog === dialog);
          if (isOrphaned) {
            let orphanCleanupSucceeded = this._retryOrphanedDialogs();
            let orphanStillPending = this._orphanedDialogs.some((entry) => entry && entry.dialog === dialog);
            return orphanCleanupSucceeded && !orphanStillPending;
          }
        }
      }
    } catch (error) {
      this._recordLifecycleError("dialog-close", error);
      return false;
    }
    let closeSucceeded = this._runTeardownOperation(
      "dialog-" + String(group || "close"),
      dialog,
      "close"
    );
    if (!closeSucceeded) {
      this._trackOrphanedDialog(dialog, group, false, false);
      return false;
    }
    let destroySucceeded = this._destroyDialogAfterClose(
      dialog,
      "dialog-" + String(group || "destroy")
    );
    if (!destroySucceeded) {
      let closeStateSafe = this._dialogCloseState(dialog) !== null;
      this._trackOrphanedDialog(dialog, group, closeStateSafe, false);
      return false;
    }
    let untracked = this._untrackDialog(dialog);
    let orphanUntracked = this._untrackOrphanedDialog(dialog);
    if (!untracked || !orphanUntracked) {
      this._trackOrphanedDialog(dialog, group, true, false);
      return false;
    }
    return true;
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
    let success = true;
    try {
      if (!this._resourceRegistry || !Array.isArray(this._resourceRegistry.dialogs)) {
        this._recordLifecycleError("dialog-state", new Error("Dialog registry is unavailable"));
        return false;
      }
      let dialogs = this._resourceRegistry.dialogs;
      for (let index = dialogs.length - 1; index >= 0; index--) {
        let dialog = dialogs[index];
        let closeSucceeded = !dialog || this._runTeardownOperation("teardown-dialog-close", dialog, "close");
        let destroySucceeded = !dialog || (closeSucceeded && this._destroyDialogAfterClose(dialog, "teardown-dialog-destroy"));
        if (closeSucceeded && destroySucceeded) {
          if (dialog) {
            let untracked = this._untrackDialog(dialog);
            let orphanUntracked = this._untrackOrphanedDialog(dialog);
            if (!untracked || !orphanUntracked) {
              if (!this._trackOrphanedDialog(dialog, "teardown", true, true)) {
                success = false;
              }
              success = false;
            }
          } else {
            let previousLength = dialogs.length;
            let removed = dialogs.splice(index, 1);
            if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== dialog || dialogs.length !== previousLength - 1) {
              throw new Error("Dialog registry invalid entry could not be removed");
            }
          }
        } else if (dialog) {
          if (!this._trackOrphanedDialog(dialog, "teardown", closeSucceeded, destroySucceeded)) {
            success = false;
          }
          success = false;
        }
      }
      return success;
    } catch (error) {
      this._recordLifecycleError("teardown-dialogs", error);
      return false;
    }
  },

  _destroyMenus: function() {
    let success = true;
    let cleanupMenu = (menu, group, propertyName) => {
      if (!menu) {
        return true;
      }
      let closeSucceeded = this._runTeardownOperation("teardown-" + group + "-close", this, "_closeMenuSafely", [menu, false, true]);
      let signalsSucceeded = closeSucceeded && this._runTeardownOperation("teardown-" + group + "-signals", menu, "disconnectAllSignals", [], true);
      let destroySucceeded = closeSucceeded && signalsSucceeded && this._runTeardownOperation("teardown-" + group + "-destroy", menu, "destroy");
      if (signalsSucceeded && closeSucceeded && destroySucceeded) {
        if (!this._untrackOrphanedMenu(menu)) {
          if (!this._trackOrphanedMenu(menu, propertyName, group, true, true, true, true)) {
            success = false;
          }
          success = false;
          return false;
        }
        return true;
      }
      if (!this._trackOrphanedMenu(menu, propertyName, group, true, signalsSucceeded, closeSucceeded, destroySucceeded)) {
        success = false;
      }
      success = false;
      return false;
    };
    let menu = this.menu;
    if (cleanupMenu(menu, "menu", "menu")) {
      if (!this._clearDestroyedMenuReference(menu, "menu", "teardown-menu")) {
        if (!this._trackOrphanedMenu(menu, "menu", "menu", false, true, true, true)) {
          success = false;
        }
        success = false;
      }
    } else {
      success = false;
    }
    let contextMenu = this._applet_context_menu;
    if (cleanupMenu(contextMenu, "context-menu", "_applet_context_menu")) {
      if (!this._clearDestroyedMenuReference(contextMenu, "_applet_context_menu", "teardown-context-menu")) {
        if (!this._trackOrphanedMenu(contextMenu, "_applet_context_menu", "context-menu", false, true, true, true)) {
          success = false;
        }
        success = false;
      }
    } else {
      success = false;
    }
    let cleanupManager = (manager, group, propertyName) => {
      if (!manager) {
        return true;
      }
      let ungrabSucceeded = manager.grabbed === true
        ? this._runTeardownOperation("teardown-" + group + "-ungrab", manager, "_ungrab")
        : true;
      let signalsSucceeded = ungrabSucceeded && this._runTeardownOperation("teardown-" + group + "-signals", manager, "disconnectAllSignals", [], true);
      let destroySucceeded = ungrabSucceeded && signalsSucceeded && this._runTeardownOperation("teardown-" + group + "-destroy", manager, "destroy");
      if (ungrabSucceeded && signalsSucceeded && destroySucceeded) {
        if (!this._untrackOrphanedMenu(manager)) {
          if (!this._trackOrphanedMenu(manager, propertyName, group, false, true, true, true)) {
            success = false;
          }
          success = false;
          return false;
        }
        return true;
      }
      if (!this._trackOrphanedMenu(manager, propertyName, group, false, ungrabSucceeded && signalsSucceeded, true, destroySucceeded)) {
        success = false;
      }
      success = false;
      return false;
    };
    let menuManager = this.menuManager;
    if (cleanupManager(menuManager, "menu-manager", "menuManager")) {
      if (!this._clearDestroyedMenuReference(menuManager, "menuManager", "teardown-menu-manager")) {
        if (!this._trackOrphanedMenu(menuManager, "menuManager", "menu-manager", false, true, true, true)) {
          success = false;
        }
        success = false;
      }
    } else {
      success = false;
    }
    let privateMenuManager = this._menuManager;
    if (cleanupManager(privateMenuManager, "private-menu-manager", "_menuManager")) {
      if (!this._clearDestroyedMenuReference(privateMenuManager, "_menuManager", "teardown-private-menu-manager")) {
        if (!this._trackOrphanedMenu(privateMenuManager, "_menuManager", "private-menu-manager", false, true, true, true)) {
          success = false;
        }
        success = false;
      }
    } else {
      success = false;
    }
    return success;
  },

  _clearDestroyedMenuReference: function(menu, propertyName, errorGroup) {
    if (!menu || !propertyName || this[propertyName] !== menu) {
      return true;
    }
    try {
      this[propertyName] = null;
      if (this[propertyName] === menu) {
        throw new Error("Menu reference could not be cleared");
      }
      return true;
    } catch (error) {
      this._recordLifecycleError(errorGroup || "menu-orphan", error);
      return false;
    }
  },

  _trackOrphanedMenu: function(menu, propertyName, group, needsClose, signalsSucceeded, closeSucceeded, destroySucceeded) {
    try {
      if (!menu) {
        throw new Error("Menu orphan is invalid");
      }
      if (!Array.isArray(this._orphanedMenus)) {
        this._orphanedMenus = [];
      }
      let knownEntry = this._orphanedMenus.find((entry) => entry && entry.menu === menu);
      if (knownEntry) {
        if (signalsSucceeded === true) {
          knownEntry.signalsSucceeded = true;
        }
        if (closeSucceeded === true) {
          knownEntry.closeSucceeded = true;
        }
        if (destroySucceeded === true) {
          knownEntry.destroySucceeded = true;
        }
      } else {
        let entry = {
          menu: menu,
          propertyName: String(propertyName || ""),
          group: String(group || "menu"),
          needsClose: needsClose === true,
          signalsSucceeded: signalsSucceeded === true,
          closeSucceeded: closeSucceeded === true,
          destroySucceeded: destroySucceeded === true,
        };
        this._orphanedMenus.push(entry);
        if (this._orphanedMenus.indexOf(entry) < 0) {
          throw new Error("Menu orphan entry could not be tracked");
        }
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("menu-orphan", error);
      return false;
    }
  },

  _untrackOrphanedMenu: function(menu) {
    if (!Array.isArray(this._orphanedMenus)) {
      return true;
    }
    let success = true;
    for (let index = this._orphanedMenus.length - 1; index >= 0; index--) {
      let entry = this._orphanedMenus[index];
      if (!entry || entry.menu !== menu) {
        continue;
      }
      try {
        let removed = this._orphanedMenus.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedMenus.indexOf(entry) >= 0) {
          throw new Error("Menu orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("menu-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedMenus: function() {
    let pendingMenus = [];
    let addPendingMenu = (menu, propertyName, group, needsClose, signalsSucceeded, closeSucceeded, destroySucceeded) => {
      if (!menu) {
        return;
      }
      let knownEntry = pendingMenus.find((entry) => entry && entry.menu === menu);
      if (knownEntry) {
        if (propertyName && !knownEntry.propertyName) {
          knownEntry.propertyName = propertyName;
        }
        if (needsClose === true || typeof menu.close === "function") {
          knownEntry.needsClose = true;
        }
        if (signalsSucceeded === true) {
          knownEntry.signalsSucceeded = true;
        }
        if (closeSucceeded === true) {
          knownEntry.closeSucceeded = true;
        }
        if (destroySucceeded === true) {
          knownEntry.destroySucceeded = true;
        }
        return;
      }
      pendingMenus.push({
        menu: menu,
        propertyName: String(propertyName || ""),
        group: String(group || "menu"),
        needsClose: needsClose === true || typeof menu.close === "function",
        signalsSucceeded: signalsSucceeded === true,
        closeSucceeded: closeSucceeded === true,
        destroySucceeded: destroySucceeded === true,
      });
    };
    let invalidOrphanEntry = false;
    if (Array.isArray(this._orphanedMenus)) {
      for (let entry of this._orphanedMenus) {
        if (!entry || !entry.menu) {
          invalidOrphanEntry = true;
          continue;
        }
        addPendingMenu(
          entry.menu,
          entry.propertyName,
          entry.group,
          entry.needsClose,
          entry.signalsSucceeded,
          entry.closeSucceeded,
          entry.destroySucceeded
        );
      }
    } else {
      this._recordLifecycleError("menu-state", new Error("Menu orphan registry is unavailable"));
    }
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    if (!Array.isArray(this._orphanedMenus) || inTeardown) {
      addPendingMenu(this.menu, "menu", "menu", true, false, false, false);
      addPendingMenu(this._applet_context_menu, "_applet_context_menu", "context-menu", true, false, false, false);
      addPendingMenu(this.menuManager, "menuManager", "menu-manager", false, false, true, false);
      addPendingMenu(this._menuManager, "_menuManager", "private-menu-manager", false, false, true, false);
    }
    if (pendingMenus.length === 0) {
      return !invalidOrphanEntry && Array.isArray(this._orphanedMenus);
    }
    let success = !invalidOrphanEntry;
    for (let index = pendingMenus.length - 1; index >= 0; index--) {
      let entry = pendingMenus[index];
      let closeSucceeded = entry.closeSucceeded === true;
      if (entry.needsClose && !closeSucceeded) {
        closeSucceeded = this._runTeardownOperation(
          "teardown-orphaned-menus",
          this,
          "_closeMenuSafely",
          [entry.menu, false, true]
        );
        if (closeSucceeded) {
          entry.closeSucceeded = true;
        }
      } else if (!entry.needsClose) {
        closeSucceeded = true;
      }
      let ungrabSucceeded = true;
      if (!entry.needsClose && entry.menu.grabbed === true) {
        ungrabSucceeded = this._runTeardownOperation("teardown-orphaned-menus", entry.menu, "_ungrab");
      }
      let signalsSucceeded = entry.signalsSucceeded === true;
      if (!signalsSucceeded && closeSucceeded && ungrabSucceeded) {
        signalsSucceeded = this._runTeardownOperation("teardown-orphaned-menus", entry.menu, "disconnectAllSignals", [], true);
        if (signalsSucceeded) {
          entry.signalsSucceeded = true;
        }
      }
      let destroySucceeded = entry.destroySucceeded === true;
      if (signalsSucceeded && closeSucceeded && !destroySucceeded) {
        destroySucceeded = this._runTeardownOperation("teardown-orphaned-menus", entry.menu, "destroy");
        if (destroySucceeded) {
          entry.destroySucceeded = true;
        }
      }
      if (!ungrabSucceeded || !signalsSucceeded || !closeSucceeded || !destroySucceeded) {
        success = false;
        continue;
      }
      if (!this._clearDestroyedMenuReference(entry.menu, entry.propertyName, "menu-orphan")) {
        success = false;
        continue;
      }
      if (!this._untrackOrphanedMenu(entry.menu)) {
        success = false;
        continue;
      }
    }
    return success;
  },

  _destroyAppletTooltip: function() {
    let tooltip = this._applet_tooltip;
    if (!tooltip) {
      this._orphanedTooltip = false;
      return true;
    }
    let destroyed = this._runTeardownOperation("teardown-tooltip", tooltip, "destroy");
    if (destroyed) {
      return this._clearDestroyedTooltip(tooltip);
    }
    this._orphanedTooltip = true;
    return false;
  },

  _clearDestroyedTooltip: function(tooltip) {
    try {
      this._applet_tooltip = null;
      if (this._applet_tooltip === tooltip) {
        throw new Error("Tooltip reference could not be cleared");
      }
      this._orphanedTooltip = false;
      return true;
    } catch (error) {
      this._recordLifecycleError("teardown-tooltip", error);
      try {
        this._orphanedTooltip = true;
      } catch (stateError) {
        this._recordLifecycleError("teardown-tooltip", stateError);
      }
      return false;
    }
  },

  _retryOrphanedTooltip: function() {
    let tooltip = this._applet_tooltip;
    if (!this._orphanedTooltip && !tooltip) {
      return true;
    }
    if (!tooltip) {
      this._orphanedTooltip = false;
      return true;
    }
    let destroyed = this._runTeardownOperation("teardown-orphaned-tooltip", tooltip, "destroy");
    if (!destroyed) {
      return false;
    }
    return this._clearDestroyedTooltip(tooltip);
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
            let removed = monitors.pop();
            if (removed !== monitor || monitors.indexOf(monitor) >= 0) {
              throw new Error("Monitor registry rollback did not remove the entry");
            }
          } else {
            let index = monitors.indexOf(monitor);
            if (index >= 0) {
              let removed = monitors.splice(index, 1);
              if (!Array.isArray(removed) || removed.length !== 1 || monitors.indexOf(monitor) >= 0) {
                throw new Error("Monitor registry rollback did not remove the entry");
              }
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
      let removed = this._resourceRegistry.monitors.splice(index, 1);
      if (!Array.isArray(removed) || removed.length !== 1 || this._resourceRegistry.monitors.indexOf(monitor) >= 0) {
        throw new Error("Monitor registry entry could not be removed");
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("monitor-untrack", error);
      return false;
    }
  },

  _trackOrphanedMonitor: function(monitor, cancelSucceeded) {
    try {
      if (!monitor) {
        throw new Error("Monitor orphan is invalid");
      }
      if (!Array.isArray(this._orphanedMonitors)) {
        this._orphanedMonitors = [];
      }
      let knownEntry = this._orphanedMonitors.find((entry) => entry && entry.monitor === monitor);
      if (knownEntry) {
        if (cancelSucceeded === true) {
          knownEntry.cancelSucceeded = true;
        }
      } else {
        let entry = {
          monitor: monitor,
          cancelSucceeded: cancelSucceeded === true,
        };
        this._orphanedMonitors.push(entry);
        if (this._orphanedMonitors.indexOf(entry) < 0) {
          throw new Error("Monitor orphan entry could not be tracked");
        }
      }
      if (this._lifecycleAllowsWork() && !this.processCleanupRetryTimer) {
        this._scheduleProcessCleanupRetry();
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("monitor-orphan", error);
      return false;
    }
  },

  _untrackOrphanedMonitor: function(monitor) {
    if (!Array.isArray(this._orphanedMonitors)) {
      return true;
    }
    let success = true;
    for (let index = this._orphanedMonitors.length - 1; index >= 0; index--) {
      let entry = this._orphanedMonitors[index];
      if (!entry || entry.monitor !== monitor) {
        continue;
      }
      try {
        let removed = this._orphanedMonitors.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedMonitors.indexOf(entry) >= 0) {
          throw new Error("Monitor orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("monitor-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedMonitors: function(includeTracked) {
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    let includeTrackedMonitors = includeTracked === true || inTeardown;
    let pendingMonitors = [];
    let addPendingMonitor = (monitor, cancelSucceeded) => {
      if (!monitor) {
        return;
      }
      let knownEntry = pendingMonitors.find((entry) => entry && entry.monitor === monitor);
      if (knownEntry) {
        if (cancelSucceeded === true) {
          knownEntry.cancelSucceeded = true;
        }
        return;
      }
      pendingMonitors.push({
        monitor: monitor,
        cancelSucceeded: cancelSucceeded === true,
      });
    };
    let invalidOrphanEntry = false;
    if (Array.isArray(this._orphanedMonitors)) {
      for (let entry of this._orphanedMonitors) {
        if (!entry || !entry.monitor) {
          invalidOrphanEntry = true;
          continue;
        }
        addPendingMonitor(entry.monitor, entry.cancelSucceeded);
      }
    } else {
      this._recordLifecycleError("monitor-state", new Error("Monitor orphan registry is unavailable"));
    }
    let monitors = this._resourceRegistry && this._resourceRegistry.monitors;
    if (includeTrackedMonitors && Array.isArray(monitors)) {
      for (let monitor of monitors) {
        if (!monitor) {
          invalidOrphanEntry = true;
          continue;
        }
        addPendingMonitor(monitor, false);
      }
    }
    if (includeTrackedMonitors) {
      addPendingMonitor(this.externalApiEnvMonitor, this._externalApiEnvMonitorCancelSucceeded === true);
    }
    if (pendingMonitors.length === 0) {
      return !invalidOrphanEntry && Array.isArray(this._orphanedMonitors);
    }
    let success = !invalidOrphanEntry;
    for (let index = pendingMonitors.length - 1; index >= 0; index--) {
      let entry = pendingMonitors[index];
      let cancelSucceeded = entry.cancelSucceeded === true;
      if (!cancelSucceeded) {
        if (!this._disconnectTrackedSignalsForTarget(entry.monitor)) {
          success = false;
          continue;
        }
        try {
          let result = entry.monitor.cancel();
          if (result === false) {
            throw new Error("Orphaned monitor could not be cancelled");
          }
          entry.cancelSucceeded = true;
          cancelSucceeded = true;
        } catch (error) {
          this._recordLifecycleError("monitor-cancel", error);
          success = false;
          continue;
        }
      }
      if (!cancelSucceeded || !this._untrackMonitor(entry.monitor)) {
        success = false;
        continue;
      }
      if (!this._untrackOrphanedMonitor(entry.monitor)) {
        success = false;
        continue;
      }
      if (!this._clearExternalApiEnvMonitorReference(entry.monitor)) {
        this._trackOrphanedMonitor(entry.monitor, true);
        success = false;
      }
    }
    return success;
  },

  _clearExternalApiEnvMonitorReference: function(monitor) {
    if (this.externalApiEnvMonitor !== monitor) {
      return true;
    }
    try {
      this.externalApiEnvMonitor = null;
      if (this.externalApiEnvMonitor === monitor) {
        throw new Error("External API monitor reference could not be cleared");
      }
      this._externalApiEnvMonitorCancelSucceeded = false;
      return true;
    } catch (error) {
      this._recordLifecycleError("monitor-orphan", error);
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
    let registrationAttempted = false;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.cancellables) {
        throw new Error("Cancellable registry is unavailable");
      }
      registry = this._resourceRegistry.cancellables;
      registrationAttempted = true;
      registry[token] = cancellable;
      if (registry[token] !== cancellable) {
        throw new Error("Cancellable could not be registered");
      }
      return token;
    } catch (error) {
      let rollbackFailed = false;
      if (registry && registrationAttempted) {
        try {
          if (Object.prototype.hasOwnProperty.call(registry, token)) {
            let deleted = delete registry[token];
            if (deleted === false || Object.prototype.hasOwnProperty.call(registry, token)) {
              throw new Error("Cancellable registration rollback failed");
            }
          }
        } catch (rollbackError) {
          rollbackFailed = true;
          this._recordLifecycleError("cancellable-registration-rollback", rollbackError);
        }
      }
      if (rollbackFailed) {
        this._trackOrphanedCancellable(token, false);
        try {
          if (!error || (typeof error !== "object" && typeof error !== "function")) {
            error = new Error(String(error || "Cancellable registration failed"));
          }
          error.cancellableToken = token;
        } catch (tokenError) {
          this._recordLifecycleError("cancellable-registration-token", tokenError);
        }
      }
      throw error;
    }
  },

  _unregisterCancellable: function(token) {
    if (!token) {
      return true;
    }
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.cancellables) {
        throw new Error("Cancellable registry is unavailable");
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

  _trackOrphanedCancellable: function(token, cancelSucceeded) {
    try {
      if (!token) {
        throw new Error("Cancellable orphan token is invalid");
      }
      if (!Array.isArray(this._orphanedCancellables)) {
        this._orphanedCancellables = [];
      }
      let key = String(token);
      let knownEntry = this._orphanedCancellables.find((entry) => entry && entry.token === key);
      if (knownEntry) {
        if (cancelSucceeded === true) {
          knownEntry.cancelSucceeded = true;
        }
      } else {
        let entry = {
          token: key,
          cancelSucceeded: cancelSucceeded === true,
        };
        this._orphanedCancellables.push(entry);
        if (this._orphanedCancellables.indexOf(entry) < 0) {
          throw new Error("Cancellable orphan entry could not be tracked");
        }
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("cancellable-orphan", error);
      return false;
    }
  },

  _untrackOrphanedCancellable: function(token) {
    if (!Array.isArray(this._orphanedCancellables)) {
      return true;
    }
    let success = true;
    let key = String(token || "");
    for (let index = this._orphanedCancellables.length - 1; index >= 0; index--) {
      let entry = this._orphanedCancellables[index];
      if (!entry || entry.token !== key) {
        continue;
      }
      try {
        let removed = this._orphanedCancellables.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedCancellables.indexOf(entry) >= 0) {
          throw new Error("Cancellable orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("cancellable-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedCancellables: function() {
    let success = true;
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    if (inTeardown) {
      let registry = this._resourceRegistry && this._resourceRegistry.cancellables;
      if (!registry) {
        this._recordLifecycleError("cancellable-state", new Error("Cancellable registry is unavailable"));
        success = false;
      } else {
        if (!Array.isArray(this._orphanedCancellables)) {
          this._orphanedCancellables = [];
        }
        for (let token in registry) {
          if (!Object.prototype.hasOwnProperty.call(registry, token)) {
            continue;
          }
          if (!this._trackOrphanedCancellable(token, false)) {
            success = false;
          }
        }
      }
    }
    if (!Array.isArray(this._orphanedCancellables)) {
      return success;
    }
    for (let index = this._orphanedCancellables.length - 1; index >= 0; index--) {
      let entry = this._orphanedCancellables[index];
      if (!entry || !entry.token) {
        this._recordLifecycleError("cancellable-orphan", new Error("Cancellable orphan entry is invalid"));
        success = false;
        continue;
      }
      let registry = this._resourceRegistry && this._resourceRegistry.cancellables;
      if (!registry) {
        this._recordLifecycleError("cancellable-state", new Error("Cancellable registry is unavailable"));
        success = false;
        continue;
      }
      let cancellable = registry[entry.token];
      if (!cancellable) {
        if (entry.cancelSucceeded === true) {
          if (!this._untrackOrphanedCancellable(entry.token)) {
            success = false;
          }
        } else {
          this._recordLifecycleError("cancellable-state", new Error("Orphaned cancellable is missing from registry"));
          success = false;
        }
        continue;
      }
      let cancelSucceeded = entry.cancelSucceeded === true;
      if (!cancelSucceeded) {
        try {
          if (typeof cancellable.cancel !== "function") {
            throw new Error("Orphaned cancellable cancellation is unavailable");
          }
          let result = cancellable.cancel();
          if (result === false) {
            throw new Error("Orphaned cancellable cancellation failed");
          }
          entry.cancelSucceeded = true;
          cancelSucceeded = true;
        } catch (error) {
          this._recordLifecycleError("cancellable-cancel", error);
          success = false;
          continue;
        }
      }
      if (!cancelSucceeded || !this._unregisterCancellable(entry.token)) {
        success = false;
        continue;
      }
      if (!this._untrackOrphanedCancellable(entry.token)) {
        success = false;
      }
    }
    return success;
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
      processGroupIdentity: this._readProcessGroupIdentity(process),
    };
    let registry = null;
    let registrationAttempted = false;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.processes) {
        throw new Error("Process registry is unavailable");
      }
      registry = this._resourceRegistry.processes;
      registrationAttempted = true;
      registry[token] = entry;
      if (registry[token] !== entry) {
        throw new Error("Process could not be registered");
      }
      return token;
    } catch (error) {
      let rollbackFailed = false;
      if (registry && registrationAttempted) {
        try {
          if (Object.prototype.hasOwnProperty.call(registry, token)) {
            let deleted = delete registry[token];
            if (deleted === false || Object.prototype.hasOwnProperty.call(registry, token)) {
              throw new Error("Process registration rollback failed");
            }
          }
        } catch (rollbackError) {
          rollbackFailed = true;
          this._recordLifecycleError("process-registration-rollback", rollbackError);
        }
      }
      if (rollbackFailed) {
        this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, false);
        try {
          if (!error || (typeof error !== "object" && typeof error !== "function")) {
            error = new Error(String(error || "Process registration failed"));
          }
          error.processToken = token;
        } catch (tokenError) {
          this._recordLifecycleError("process-registration-token", tokenError);
        }
      }
      throw error;
    }
  },

  _unregisterProcess: function(token) {
    if (!token) {
      return true;
    }
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.processes) {
        throw new Error("Process registry is unavailable");
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

  _trackOrphanedProcess: function(process, generation, group, registryToken, terminationSucceeded) {
    try {
      if (!process) {
        throw new Error("Process orphan is invalid");
      }
      if (!Array.isArray(this._orphanedProcesses)) {
        this._orphanedProcesses = [];
      }
      let key = registryToken ? String(registryToken) : "";
      let processGroupIdentity = null;
      if (key) {
        let registry = this._resourceRegistry && this._resourceRegistry.processes;
        let registryEntry = registry && registry[key];
        if (registryEntry && registryEntry.process === process && registryEntry.processGroupIdentity) {
          processGroupIdentity = registryEntry.processGroupIdentity;
        }
      }
      if (!processGroupIdentity) {
        processGroupIdentity = this._readProcessGroupIdentity(process);
      }
      let knownEntry = this._orphanedProcesses.find((entry) => entry && entry.process === process);
      if (knownEntry) {
        if (key && !knownEntry.registryToken) {
          knownEntry.registryToken = key;
        }
        if (terminationSucceeded === true) {
          knownEntry.terminationSucceeded = true;
        }
        if (!knownEntry.processGroupIdentity && processGroupIdentity) {
          knownEntry.processGroupIdentity = processGroupIdentity;
        }
      } else {
        let entry = {
          process: process,
          generation: generation,
          group: String(group || "process"),
          registryToken: key,
          terminationSucceeded: terminationSucceeded === true,
          processGroupIdentity: processGroupIdentity,
        };
        this._orphanedProcesses.push(entry);
        if (this._orphanedProcesses.indexOf(entry) < 0) {
          throw new Error("Process orphan entry could not be tracked");
        }
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("process-orphan", error);
      return false;
    }
  },

  _untrackOrphanedProcess: function(process) {
    if (!Array.isArray(this._orphanedProcesses)) {
      return true;
    }
    let success = true;
    for (let index = this._orphanedProcesses.length - 1; index >= 0; index--) {
      let entry = this._orphanedProcesses[index];
      if (!entry || entry.process !== process) {
        continue;
      }
      try {
        let removed = this._orphanedProcesses.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedProcesses.indexOf(entry) >= 0) {
          throw new Error("Process orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("process-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedProcesses: function(group) {
    let wantedGroup = group === undefined ? null : String(group || "process");
    let success = true;
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    if (inTeardown) {
      let registry = this._resourceRegistry && this._resourceRegistry.processes;
      if (!registry) {
        this._recordLifecycleError("process-state", new Error("Process registry is unavailable"));
        success = false;
      } else {
        if (!Array.isArray(this._orphanedProcesses)) {
          this._orphanedProcesses = [];
        }
        for (let token in registry) {
          if (!Object.prototype.hasOwnProperty.call(registry, token)) {
            continue;
          }
          let entry = registry[token];
          if (!entry || typeof entry !== "object" || !entry.process) {
            this._recordLifecycleError("process-state", new Error("Process registry entry is unavailable"));
            success = false;
            continue;
          }
          if (wantedGroup !== null && String(entry.group || "process") !== wantedGroup) {
            continue;
          }
          if (!this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, false)) {
            success = false;
          }
        }
      }
    }
    if (!Array.isArray(this._orphanedProcesses)) {
      return success;
    }
    for (let index = this._orphanedProcesses.length - 1; index >= 0; index--) {
      let entry = this._orphanedProcesses[index];
      if (wantedGroup !== null && entry && String(entry.group || "process") !== wantedGroup) {
        continue;
      }
      if (!entry || !entry.process) {
        this._recordLifecycleError("process-orphan", new Error("Process orphan entry is invalid"));
        success = false;
        continue;
      }
      let terminationSucceeded = entry.terminationSucceeded === true;
      if (terminationSucceeded && entry.processGroupIdentity) {
        let groupState = this._processGroupState(entry.processGroupIdentity);
        if (groupState === "live") {
          terminationSucceeded = false;
          entry.terminationSucceeded = false;
        } else if (groupState !== "stopped") {
          success = false;
          continue;
        }
      }
      if (!terminationSucceeded) {
        if (!this._terminateProcess(entry.process)) {
          success = false;
          continue;
        }
        entry.terminationSucceeded = true;
        terminationSucceeded = true;
      }
      if (entry.registryToken) {
        let registry = this._resourceRegistry && this._resourceRegistry.processes;
        if (!registry) {
          this._recordLifecycleError("process-state", new Error("Process registry is unavailable"));
          success = false;
          continue;
        }
        if (!this._unregisterProcess(entry.registryToken)) {
          success = false;
          continue;
        }
      }
      if (!this._untrackOrphanedProcess(entry.process)) {
        success = false;
      }
    }
    return success;
  },

  _processCleanupStillPending: function() {
    return !Array.isArray(this._orphanedProcesses) || this._orphanedProcesses.length > 0 ||
      !Array.isArray(this._orphanedCancellables) || this._orphanedCancellables.length > 0 ||
      !Array.isArray(this._orphanedSignals) || this._orphanedSignals.length > 0 ||
      !Array.isArray(this._orphanedMonitors) || this._orphanedMonitors.length > 0 ||
      !Array.isArray(this._orphanedHotkeys) || this._orphanedHotkeys.length > 0 ||
      !this._pendingHotkeyRebinds || typeof this._pendingHotkeyRebinds !== "object" ||
      Object.keys(this._pendingHotkeyRebinds).length > 0 ||
      !Array.isArray(this._orphanedTimers) || this._orphanedTimers.length > 0 ||
      !Array.isArray(this._orphanedDialogs) || this._orphanedDialogs.length > 0;
  },

  _releaseBusyStateAfterProcessCleanup: function(group, marker, releaseRequested) {
    let wanted = String(group || "process");
    let requested = releaseRequested === true || (marker && this[marker] === true);
    if (!requested || this._hasTrackedProcessGroup(wanted)) {
      return false;
    }
    if (!Array.isArray(this._orphanedProcesses)) {
      this._recordLifecycleError("process-state", new Error("Process orphan registry is unavailable"));
      return false;
    }
    for (let entry of this._orphanedProcesses) {
      if (!entry || !entry.process) {
        return false;
      }
      if (String(entry.group || "process") === wanted) {
        return false;
      }
    }
    if (marker) {
      this[marker] = false;
    }
    if (wanted === "ollama" && !this.ollamaModelFlowToken) {
      this.ollamaModelInstallToken = null;
      this.ollamaModelInstallRunning = false;
    }
    let anotherCommandRunning = Boolean(
      this._recordingCommandToken ||
      this._cleanupCommandToken ||
      this.voiceModelActionToken ||
      this.benchmarkFlowToken ||
      this.ollamaModelFlowToken ||
      this.ollamaModelInstallToken ||
      this._hasLocalProcessingWorkflow()
    );
    if (!anotherCommandRunning) {
      this.isCommandRunning = false;
    }
    return true;
  },

  _scheduleProcessCleanupRetry: function() {
    if (!this._lifecycleAllowsWork()) {
      return false;
    }
    if (!this._processCleanupStillPending()) {
      return true;
    }
    if (this.processCleanupRetryTimer) {
      return true;
    }
    let timerId = this._scheduleTrackedTimer("process-cleanup-retry", 1000, () => {
      try {
        let processCleanupSucceeded = this._retryOrphanedProcesses();
        let cancellableCleanupSucceeded = this._retryOrphanedCancellables();
        let signalCleanupSucceeded = this._disconnectOrphanedSignals();
        let monitorCleanupSucceeded = this._retryOrphanedMonitors();
        let hotkeyCleanupSucceeded = this._retryOrphanedHotkeys();
        let hotkeyRebindSucceeded = this._retryPendingHotkeyRebinds();
        let timerCleanupSucceeded = this._retryOrphanedTimers();
        let dialogCleanupSucceeded = this._retryOrphanedDialogs();
        if (!processCleanupSucceeded || !cancellableCleanupSucceeded || !timerCleanupSucceeded ||
            !signalCleanupSucceeded || !monitorCleanupSucceeded || !hotkeyCleanupSucceeded ||
            !hotkeyRebindSucceeded || !dialogCleanupSucceeded ||
            this._processCleanupStillPending()) {
          return true;
        }
        this._releaseBusyStateAfterProcessCleanup("voice-model", "voiceModelCleanupFailed");
        this._releaseBusyStateAfterProcessCleanup("benchmark", "benchmarkCleanupFailed");
        this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed");
        this._releaseBusyStateAfterProcessCleanup("maintenance", "maintenanceCleanupFailed");
        if (this._doctorCommandRunning && !this.doctorCommandToken && !this._hasTrackedProcessGroup("doctor")) {
          this._doctorCommandRunning = false;
        }
        if (this.terminalWorkflowRunning && !this.terminalWorkflowToken &&
            !this._hasTrackedProcessGroup("terminal") && !this._hasTrackedProcessGroup("ollama")) {
          this.terminalWorkflowRunning = false;
        }
        if ((this.status === "recording" || this.status === "processing") &&
            !this.isCommandRunning && !this._statusCommandRunning && !this._hasLocalProcessingWorkflow()) {
          this._scheduleStatusPoll();
        }
        return false;
      } catch (error) {
        this._recordLifecycleError("process-cleanup-retry", error);
        return true;
      }
    }, false, "processCleanupRetryTimer");
    return Boolean(timerId);
  },

  _clearProcessCleanupRetryTimer: function() {
    return this._clearTrackedTimer("process-cleanup-retry", "processCleanupRetryTimer");
  },

  _terminateProcess: function(process) {
    if (!process) {
      return false;
    }
    try {
      let hasIdentifier = typeof process.get_identifier === "function";
      let processIdentifier = hasIdentifier ? String(process.get_identifier() || "").trim() : "";
      let processGroupIdentity = this._findTrackedProcessGroupIdentity(process);
      if (hasIdentifier && !processIdentifier && !processGroupIdentity) {
        return true;
      }
      if (!processGroupIdentity && hasIdentifier) {
        processGroupIdentity = this._readProcessGroupIdentity(process);
        if (!processGroupIdentity) {
          return false;
        }
      }
      processGroupIdentity = processGroupIdentity || this._readProcessGroupIdentity(process);
      if (processGroupIdentity) {
        let currentProcessGroupIdentity = this._readProcessGroupIdentity(process);
        if (currentProcessGroupIdentity &&
            (currentProcessGroupIdentity.pid !== processGroupIdentity.pid ||
             currentProcessGroupIdentity.startTime !== processGroupIdentity.startTime)) {
          return false;
        }
        if (!currentProcessGroupIdentity && processIdentifier) {
          let groupState = this._processGroupState(processGroupIdentity);
          if (groupState === "stopped") {
            return true;
          }
          if (groupState === "live" && this._killProcessGroup(process, processGroupIdentity)) {
            return true;
          }
          return false;
        }
        if (this._killProcessGroup(process, processGroupIdentity)) {
          return true;
        }
        return false;
      }
      if (typeof process.force_exit !== "function") {
        throw new Error("Process termination API is unavailable");
      }
      process.force_exit();
      return true;
    } catch (error) {
      this._recordLifecycleError("process-kill", error);
      return false;
    }
  },

  _findTrackedProcessGroupIdentity: function(process) {
    let registries = [
      this._resourceRegistry && this._resourceRegistry.processes,
      this._orphanedProcesses,
    ];
    for (let registry of registries) {
      if (!registry) {
        continue;
      }
      for (let token in registry) {
        if (!Object.prototype.hasOwnProperty.call(registry, token)) {
          continue;
        }
        let entry = registry[token];
        if (entry && entry.process === process && entry.processGroupIdentity) {
          return entry.processGroupIdentity;
        }
      }
    }
    return null;
  },

  _readProcessGroupIdentity: function(process) {
    try {
      if (!process || typeof process.get_identifier !== "function") {
        return null;
      }
      let pid = String(process.get_identifier() || "").trim();
      if (!/^[1-9][0-9]*$/.test(pid)) {
        return null;
      }
      let contents = GLib.file_get_contents("/proc/" + pid + "/stat");
      if (!contents || contents[0] !== true) {
        return null;
      }
      let stat = ByteArray.toString(contents[1] || "");
      let commandEnd = stat.lastIndexOf(") ");
      if (commandEnd < 0) {
        return null;
      }
      let fields = stat.slice(commandEnd + 2).trim().split(/\s+/);
      if (fields.length <= 19 || fields[2] !== pid || fields[3] !== pid || !/^[0-9]+$/.test(fields[19])) {
        return null;
      }
      return {
        pid: pid,
        startTime: fields[19],
      };
    } catch (error) {
      return null;
    }
  },

  _processGroupState: function(identity) {
    try {
      if (!identity || !/^[1-9][0-9]*$/.test(String(identity.pid || "")) ||
          !/^[0-9]+$/.test(String(identity.startTime || ""))) {
        return "invalid";
      }
      let groupPid = String(identity.pid);
      let procPath = "/proc/" + groupPid + "/stat";
      let leaderContents = null;
      let leaderPathExists = false;
      try {
        leaderPathExists = GLib.file_test(procPath, GLib.FileTest.EXISTS);
        leaderContents = GLib.file_get_contents(procPath);
      } catch (error) {
        leaderContents = null;
      }
      if ((!leaderContents || leaderContents[0] !== true) && leaderPathExists) {
        let leaderStillExists = false;
        try {
          leaderStillExists = GLib.file_test(procPath, GLib.FileTest.EXISTS);
        } catch (error) {
          return "invalid";
        }
        if (leaderStillExists) {
          return "invalid";
        }
        leaderPathExists = false;
      }
      if (leaderContents && leaderContents[0] === true) {
        let leaderStat = ByteArray.toString(leaderContents[1] || "");
        let leaderEnd = leaderStat.lastIndexOf(") ");
        if (leaderEnd < 0) {
          return "invalid";
        }
        let leaderFields = leaderStat.slice(leaderEnd + 2).trim().split(/\s+/);
        if (leaderFields.length <= 19 || leaderFields[2] !== groupPid || leaderFields[3] !== groupPid ||
            leaderFields[19] !== String(identity.startTime)) {
          return "invalid";
        }
        if (leaderFields[0] !== "Z" && leaderFields[0] !== "X" && leaderFields[0] !== "x") {
          return "live";
        }
      }

      let procDirectory = Gio.File.new_for_path("/proc");
      let enumerator = procDirectory.enumerate_children(
        "standard::name",
        Gio.FileQueryInfoFlags.NONE,
        null
      );
      let sessionMemberFound = false;
      try {
        while (true) {
          let info = enumerator.next_file(null);
          if (!info) {
            break;
          }
          let memberPid = String(info.get_name() || "");
          if (!/^[1-9][0-9]*$/.test(memberPid) || memberPid === groupPid) {
            continue;
          }
          let memberStatPath = "/proc/" + memberPid + "/stat";
          let contents = null;
          try {
            contents = GLib.file_get_contents(memberStatPath);
          } catch (error) {
            contents = null;
          }
          if (!contents || contents[0] !== true) {
            let memberStillExists = false;
            try {
              memberStillExists = GLib.file_test(memberStatPath, GLib.FileTest.EXISTS);
            } catch (error) {
              return "invalid";
            }
            if (!memberStillExists) {
              continue;
            }
            return "invalid";
          }
          let stat = ByteArray.toString(contents[1] || "");
          let commandEnd = stat.lastIndexOf(") ");
          if (commandEnd < 0) {
            return "invalid";
          }
          let fields = stat.slice(commandEnd + 2).trim().split(/\s+/);
          if (fields.length <= 19) {
            return "invalid";
          }
          if (fields[3] === groupPid && fields[0] !== "Z" && fields[0] !== "X" && fields[0] !== "x") {
            sessionMemberFound = true;
          }
        }
      } finally {
        enumerator.close(null);
      }
      return sessionMemberFound ? "live" : "stopped";
    } catch (error) {
      return "invalid";
    }
  },

  _processSessionGroupIds: function(identity) {
    try {
      if (!identity || !/^[1-9][0-9]*$/.test(String(identity.pid || "")) ||
          !/^[0-9]+$/.test(String(identity.startTime || ""))) {
        return null;
      }
      let groupPid = String(identity.pid);
      let groups = {};
      let procDirectory = Gio.File.new_for_path("/proc");
      let enumerator = procDirectory.enumerate_children(
        "standard::name",
        Gio.FileQueryInfoFlags.NONE,
        null
      );
      try {
        while (true) {
          let info = enumerator.next_file(null);
          if (!info) {
            break;
          }
          let memberPid = String(info.get_name() || "");
          if (!/^[1-9][0-9]*$/.test(memberPid)) {
            continue;
          }
          let memberStatPath = "/proc/" + memberPid + "/stat";
          let contents = null;
          try {
            contents = GLib.file_get_contents(memberStatPath);
          } catch (error) {
            contents = null;
          }
          if (!contents || contents[0] !== true) {
            let memberStillExists = false;
            try {
              memberStillExists = GLib.file_test(memberStatPath, GLib.FileTest.EXISTS);
            } catch (error) {
              return null;
            }
            if (!memberStillExists) {
              continue;
            }
            return null;
          }
          let stat = ByteArray.toString(contents[1] || "");
          let commandEnd = stat.lastIndexOf(") ");
          if (commandEnd < 0) {
            return null;
          }
          let fields = stat.slice(commandEnd + 2).trim().split(/\s+/);
          if (fields.length <= 19) {
            return null;
          }
          if (fields[3] !== groupPid) {
            continue;
          }
          if (!/^[1-9][0-9]*$/.test(fields[2])) {
            return null;
          }
          if (memberPid === groupPid) {
            if (fields[2] !== groupPid || fields[19] !== String(identity.startTime)) {
              return null;
            }
          }
          groups[fields[2]] = true;
        }
      } finally {
        enumerator.close(null);
      }
      return Object.keys(groups);
    } catch (error) {
      return null;
    }
  },

  _killProcessGroup: function(process, identity) {
    try {
      let groupState = this._processGroupState(identity);
      if (groupState === "stopped") {
        return true;
      }
      if (groupState !== "live") {
        return false;
      }
      let kill = this._findTrustedProgramInPath("kill");
      if (!kill) {
        return false;
      }
      let sessionGroupIds = this._processSessionGroupIds(identity);
      if (!sessionGroupIds) {
        return false;
      }
      for (let processGroupId of sessionGroupIds) {
        let result = GLib.spawn_sync(null, [kill, "-KILL", "--", "-" + processGroupId], null, 0, null);
        if (!result || result[0] !== true || result[3] !== 0) {
          return false;
        }
      }
      let finalGroupState = this._processGroupState(identity);
      return finalGroupState === "stopped";
    } catch (error) {
      this._recordLifecycleError("process-group-kill", error);
      return false;
    }
  },

  _terminateAllProcesses: function() {
    let allSucceeded = true;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.processes) {
        this._recordLifecycleError("process-state", new Error("Process registry is unavailable"));
        return false;
      }
      let processes = this._resourceRegistry.processes;
      for (let token in processes) {
        if (Object.prototype.hasOwnProperty.call(processes, token)) {
          let cleanupSucceeded = false;
          let entry = null;
          try {
            entry = processes[token];
            if (!entry || typeof entry !== "object" || !entry.process) {
              throw new Error("Process registry entry is unavailable");
            }
            if (entry && typeof entry.cancel === "function") {
              let result = entry.cancel();
              if (result === false) {
                throw new Error("Process cancellation failed");
              }
            } else if (entry && !this._terminateProcess(entry.process)) {
              throw new Error("Process termination failed");
            }
            cleanupSucceeded = true;
          } catch (error) {
            allSucceeded = false;
            this._recordLifecycleError("process-cancel", error);
            if (entry && entry.process) {
              if (!this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, false)) {
                allSucceeded = false;
              }
            }
          }
          if (cleanupSucceeded) {
            if (!this._unregisterProcess(token)) {
              allSucceeded = false;
              if (!this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, true)) {
                allSucceeded = false;
              }
            } else {
              if (!this._untrackOrphanedProcess(entry.process)) {
                allSucceeded = false;
                this._recordLifecycleError("process-cancel", new Error("Process orphan cleanup could not be completed"));
              }
            }
          }
        }
      }
      return allSucceeded;
    } catch (error) {
      this._recordLifecycleError("process-cancel", error);
      return false;
    }
  },

  _terminateProcessesByGroup: function(group, notifyCallback) {
    let wanted = String(group || "process");
    let processes = {};
    let allSucceeded = true;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.processes) {
        throw new Error("Process registry is unavailable");
      }
      processes = this._resourceRegistry.processes;
      for (let token in processes) {
        if (!Object.prototype.hasOwnProperty.call(processes, token)) {
          continue;
        }
        let entry = null;
        let selected = false;
        let cleanupSucceeded = false;
        try {
          entry = processes[token];
          if (!entry || typeof entry !== "object" || String(entry.group || "process") !== wanted) {
            continue;
          }
          selected = true;
          if (typeof entry.cancel === "function") {
            let result = entry.cancel(Boolean(notifyCallback));
            if (result === false) {
              allSucceeded = false;
              let processGroupIdentity = entry.processGroupIdentity ||
                this._findTrackedProcessGroupIdentity(entry.process);
              if (!processGroupIdentity || this._processGroupState(processGroupIdentity) !== "stopped") {
                throw new Error("Process cancellation failed");
              }
            }
          } else if (!this._terminateProcess(entry.process)) {
            throw new Error("Process termination failed");
          }
          cleanupSucceeded = true;
        } catch (error) {
          allSucceeded = false;
          this._recordLifecycleError("process-cancel", error);
          if (selected && entry && entry.process) {
            this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, false);
          }
        }
        if (selected && cleanupSucceeded) {
          if (!this._unregisterProcess(token)) {
            allSucceeded = false;
            this._trackOrphanedProcess(entry.process, entry.generation, entry.group, token, true);
          } else {
            if (!this._untrackOrphanedProcess(entry.process)) {
              allSucceeded = false;
            }
          }
        }
      }
    } catch (error) {
      allSucceeded = false;
      this._recordLifecycleError("process-cancel", error);
    }
    let orphanCleanupSucceeded = this._retryOrphanedProcesses(wanted);
    if (!orphanCleanupSucceeded) {
      allSucceeded = false;
    }
    if (!Array.isArray(this._orphanedProcesses)) {
      allSucceeded = false;
    } else if (this._orphanedProcesses.some(
      (entry) => entry && String(entry.group || "process") === wanted
    )) {
      allSucceeded = false;
    }
    if (!allSucceeded || this._processCleanupStillPending()) {
      this._scheduleProcessCleanupRetry();
    }
    return allSucceeded;
  },

  _hasTrackedProcessGroup: function(group) {
    let wanted = String(group || "process");
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.processes) {
        throw new Error("Process registry is unavailable");
      }
      for (let token in this._resourceRegistry.processes) {
        if (!Object.prototype.hasOwnProperty.call(this._resourceRegistry.processes, token)) {
          continue;
        }
        let entry = this._resourceRegistry.processes[token];
        if (entry && typeof entry === "object" && String(entry.group || "process") === wanted) {
          return true;
        }
      }
      if (!Array.isArray(this._orphanedProcesses)) {
        throw new Error("Process orphan registry is unavailable");
      }
      for (let entry of this._orphanedProcesses) {
        if (!entry || !entry.process) {
          throw new Error("Process orphan entry is unavailable");
        }
        if (String(entry.group || "process") === wanted) {
          return true;
        }
      }
      return false;
    } catch (error) {
      this._recordLifecycleError("process-state", error);
      return true;
    }
  },

  _cancelAllCancellables: function() {
    let allSucceeded = true;
    try {
      if (!this._resourceRegistry || !this._resourceRegistry.cancellables) {
        this._recordLifecycleError("cancellable-state", new Error("Cancellable registry is unavailable"));
        return false;
      }
      let cancellables = this._resourceRegistry.cancellables;
      for (let token in cancellables) {
        if (!Object.prototype.hasOwnProperty.call(cancellables, token)) {
          continue;
        }
        let cleanupSucceeded = false;
        try {
          let cancellable = cancellables[token];
          if (!cancellable || typeof cancellable.cancel !== "function") {
            throw new Error("Cancellable cancellation is unavailable");
          }
          let result = cancellable.cancel();
          if (result === false) {
            throw new Error("Cancellable cancellation failed");
          }
          cleanupSucceeded = true;
        } catch (error) {
          allSucceeded = false;
          this._recordLifecycleError("teardown-cancellable", error);
        }
        if (cleanupSucceeded) {
          if (!this._unregisterCancellable(token)) {
            allSucceeded = false;
            if (!this._trackOrphanedCancellable(token, true)) {
              allSucceeded = false;
            }
          } else {
            if (!this._untrackOrphanedCancellable(token)) {
              allSucceeded = false;
              this._recordLifecycleError("teardown-cancellable", new Error("Cancellable orphan cleanup could not be completed"));
            }
          }
        } else {
          if (!this._trackOrphanedCancellable(token, false)) {
            allSucceeded = false;
          }
        }
      }
      return allSucceeded;
    } catch (error) {
      this._recordLifecycleError("teardown-cancellable", error);
      return false;
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

  _trackOrphanedTimer: function(name, sourceId, propertyName, sourceRemoved) {
    try {
      if (!sourceId) {
        throw new Error("Timer orphan source is invalid");
      }
      if (!Array.isArray(this._orphanedTimers)) {
        this._orphanedTimers = [];
      }
      let key = String(name || propertyName || "timer");
      let knownEntry = this._orphanedTimers.find(
        (entry) => entry && entry.sourceId === sourceId && entry.name === key
      );
      if (knownEntry) {
        if (sourceRemoved === true) {
          knownEntry.sourceRemoved = true;
        }
        if (propertyName && !knownEntry.propertyName) {
          knownEntry.propertyName = propertyName;
        }
      } else {
        let entry = {
          name: key,
          sourceId: sourceId,
          propertyName: propertyName || "",
          sourceRemoved: sourceRemoved === true,
        };
        this._orphanedTimers.push(entry);
        if (this._orphanedTimers.indexOf(entry) < 0) {
          throw new Error("Timer orphan entry could not be tracked");
        }
      }
      if (key !== "process-cleanup-retry" && this._lifecycleAllowsWork() && !this.processCleanupRetryTimer) {
        this._scheduleProcessCleanupRetry();
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("timer-orphan", error);
      return false;
    }
  },

  _untrackOrphanedTimer: function(name, sourceId) {
    if (!Array.isArray(this._orphanedTimers)) {
      return true;
    }
    let success = true;
    let key = String(name || "");
    for (let index = this._orphanedTimers.length - 1; index >= 0; index--) {
      let entry = this._orphanedTimers[index];
      if (!entry || (sourceId && entry.sourceId !== sourceId) || (key && entry.name !== key)) {
        continue;
      }
      try {
        let removed = this._orphanedTimers.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedTimers.indexOf(entry) >= 0) {
          throw new Error("Timer orphan entry could not be removed");
        }
      } catch (error) {
        this._recordLifecycleError("timer-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedTimers: function() {
    let pendingTimers = [];
    let timers = this._resourceRegistry && this._resourceRegistry.timers;
    let sourceIdWasReusedForDifferentTimer = (name, sourceId) => {
      if (!timers || (typeof timers !== "object" && typeof timers !== "function")) {
        return false;
      }
      let key = String(name || "");
      for (let currentName in timers) {
        if (!Object.prototype.hasOwnProperty.call(timers, currentName) || String(currentName) === key) {
          continue;
        }
        if (timers[currentName] === sourceId) {
          return true;
        }
      }
      return false;
    };
    let addPendingTimer = (name, sourceId, propertyName, sourceRemoved) => {
      if (!sourceId) {
        return;
      }
      let key = String(name || propertyName || "timer");
      let knownEntry = pendingTimers.find(
        (entry) => entry && entry.sourceId === sourceId && entry.name === key
      );
      if (knownEntry) {
        if (propertyName && !knownEntry.propertyName) {
          knownEntry.propertyName = propertyName;
        }
        if (sourceRemoved === true) {
          knownEntry.sourceRemoved = true;
        }
        return;
      }
      pendingTimers.push({
        name: key,
        sourceId: sourceId,
        propertyName: propertyName || "",
        sourceRemoved: sourceRemoved === true,
      });
    };
    let invalidOrphanEntry = false;
    if (Array.isArray(this._orphanedTimers)) {
      for (let entry of this._orphanedTimers) {
        if (!entry || !entry.sourceId) {
          invalidOrphanEntry = true;
          continue;
        }
        let sourceIdWasReused = sourceIdWasReusedForDifferentTimer(entry.name, entry.sourceId);
        addPendingTimer(
          entry.name,
          entry.sourceId,
          sourceIdWasReused ? "" : entry.propertyName,
          entry.sourceRemoved === true || sourceIdWasReused
        );
      }
    } else {
      this._recordLifecycleError("timer-state", new Error("Timer orphan registry is unavailable"));
    }
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    if ((!Array.isArray(this._orphanedTimers) || inTeardown) && timers && (typeof timers === "object" || typeof timers === "function")) {
      for (let name in timers) {
        if (!Object.prototype.hasOwnProperty.call(timers, name)) {
          continue;
        }
        if (!timers[name]) {
          invalidOrphanEntry = true;
          continue;
        }
        addPendingTimer(name, timers[name], "", false);
      }
    }
    if (pendingTimers.length === 0) {
      return !invalidOrphanEntry && Array.isArray(this._orphanedTimers);
    }
    let success = !invalidOrphanEntry;
    for (let index = pendingTimers.length - 1; index >= 0; index--) {
      let entry = pendingTimers[index];
      try {
        if (entry.sourceRemoved !== true) {
          let result = Mainloop.source_remove(entry.sourceId);
          if (result === false) {
            throw new Error("Timer orphan removal failed");
          }
          entry.sourceRemoved = true;
        }
        let untracked = this._untrackTimer(entry.name, entry.sourceId, entry.propertyName);
        if (untracked === false) {
          throw new Error("Timer orphan registry cleanup failed");
        }
        if (!this._untrackOrphanedTimer(entry.name, entry.sourceId)) {
          success = false;
        }
      } catch (error) {
        this._recordLifecycleError("timer-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _clearTrackedTimer: function(name, propertyName, sourceAlreadyRemoved) {
    let key = "timer";
    let sourceId = 0;
    let sourceRemovalSucceeded = sourceAlreadyRemoved === true;
    try {
      key = String(name || propertyName || "timer");
      sourceId = this._resourceRegistry && this._resourceRegistry.timers
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
      let activeTimer = this._activeTrackedTimer;
      let sourceIsDispatching = Boolean(
        activeTimer &&
        activeTimer.name === key &&
        activeTimer.sourceId === sourceId &&
        (!propertyName || activeTimer.propertyName === propertyName)
      );
      if (sourceAlreadyRemoved !== true && !sourceIsDispatching) {
        let removed = Mainloop.source_remove(sourceId);
        if (removed === false) {
          throw new Error("Timer source could not be removed");
        }
        sourceRemovalSucceeded = true;
      } else if (sourceIsDispatching) {
        // Current callback returns false after replacement; no source_remove on dispatching source.
        sourceRemovalSucceeded = true;
      }
      let orphanUntracked = this._untrackOrphanedTimer(key, sourceId);
      if (orphanUntracked === false) {
        throw new Error("Timer orphan registry entry could not be removed");
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
      if (sourceId) {
        this._trackOrphanedTimer(key, sourceId, propertyName, sourceRemovalSucceeded);
      }
      this._recordLifecycleError("timer-clear", error);
      return false;
    }
  },

  _scheduleTrackedTimer: function(name, delay, callback, useSeconds, propertyName) {
    if (!this._lifecycleAllowsWork() || typeof callback !== "function") {
      return 0;
    }
    let key = String(name || propertyName || "timer");
    if (!Array.isArray(this._orphanedTimers)) {
      this._recordLifecycleError("timer-state", new Error("Timer orphan registry is unavailable"));
      return 0;
    }
    if (this._orphanedTimers.length > 0 && key !== "process-cleanup-retry") {
      let orphanCleanupSucceeded = this._retryOrphanedTimers();
      if (!orphanCleanupSucceeded || this._orphanedTimers.length > 0) {
        this._recordLifecycleError("timer-state", new Error("An orphaned timer is still pending"));
        return 0;
      }
    }
    if (this._clearTrackedTimer(key, propertyName) === false) {
      return 0;
    }
    let generation = this.spawnGeneration;
    let sourceId = 0;
    let sourceRemovalSucceeded = false;
    let normalizedDelay;
    try {
      normalizedDelay = Number(delay === undefined || delay === null ? 1 : delay);
      if (!Number.isFinite(normalizedDelay)) {
        throw new Error("Timer delay is invalid");
      }
      normalizedDelay = Math.max(1, normalizedDelay);
    } catch (error) {
      this._recordLifecycleError("timer-schedule", error);
      return 0;
    }
    let retireTimer = (sourceRemovedOnFailure) => {
      let orphanUntracked = this._untrackOrphanedTimer(key, sourceId);
      let registryUntracked = orphanUntracked &&
        this._untrackTimer(key, sourceId, propertyName);
      if (registryUntracked && orphanUntracked) {
        return true;
      }
      if (!this._trackOrphanedTimer(
        key,
        sourceId,
        propertyName,
        sourceRemovedOnFailure === true
      )) {
        this._recordLifecycleError("timer-state", new Error("Expired timer cleanup could not be tracked"));
      }
      return false;
    };
    let timerCallback = () => {
      if (this.appletRemoved || this.spawnGeneration !== generation) {
        retireTimer(true);
        return false;
      }
      let registryOwnsTimer = Boolean(
        this._resourceRegistry && this._resourceRegistry.timers &&
        this._resourceRegistry.timers[key] === sourceId
      );
      let propertyOwnsTimer = Boolean(propertyName && this[propertyName] === sourceId);
      let timerIsCurrent = registryOwnsTimer && (!propertyName || propertyOwnsTimer);
      if (!timerIsCurrent) {
        retireTimer(true);
        return false;
      }
      let previousActiveTimer = this._activeTrackedTimer;
      let activeTimer = {
        name: key,
        sourceId: sourceId,
        propertyName: propertyName || "",
      };
      this._activeTrackedTimer = activeTimer;
      let keepTimer;
      try {
        keepTimer = this._runStateGuarded("timer-" + key, callback, false) === true;
      } finally {
        if (this._activeTrackedTimer === activeTimer) {
          this._activeTrackedTimer = previousActiveTimer;
        }
      }
      let timerWasReplaced = !(
        this._resourceRegistry && this._resourceRegistry.timers &&
        this._resourceRegistry.timers[key] === sourceId &&
        (!propertyName || this[propertyName] === sourceId)
      );
      if (timerWasReplaced) {
        // Replacement or explicit clear owns next source; retire dispatching source.
        return false;
      }
      if (!keepTimer) {
        let retryTimerMustRemainActive = key === "process-cleanup-retry";
        let retired = retireTimer(!retryTimerMustRemainActive);
        if (!retired && retryTimerMustRemainActive) {
          return true;
        }
      }
      return keepTimer;
    };
    try {
      sourceId = useSeconds
        ? Mainloop.timeout_add_seconds(normalizedDelay, timerCallback)
        : Mainloop.timeout_add(normalizedDelay, timerCallback);
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
          sourceRemovalSucceeded = true;
          let orphanUntracked = this._untrackOrphanedTimer(key, sourceId);
          if (orphanUntracked === false) {
            throw new Error("Timer rollback orphan entry could not be removed");
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
          if (sourceId) {
            this._trackOrphanedTimer(key, sourceId, propertyName, sourceRemovalSucceeded);
          }
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
    this._statusIconCache = { status: null, icon: null };
    this._panelRenderFingerprint = null;
    this._recordingDisplayFingerprint = null;
    this._historyMenuFingerprint = null;
    this._inputSourceMenuFingerprint = null;
    this._modelMenuFingerprint = null;
    this._textModelMenuFingerprint = null;
    this._textModelMenuProvider = "";
    this._alarmMenuFingerprint = null;
    this.toggleKeybinding = "<Super>z::";
    this.primaryLanguageKeybinding = "";
    this.secondaryLanguageKeybinding = "";
    this.cancelKeybinding = "";
    this.showPanelLabel = true;
    this.showTranscriptText = true;
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
    this.recordingArtifactsPresent = false;
    this.lastTranscript = "";
    this.lastMessage = "";
    this.isCommandRunning = false;
    this.terminalWorkflowRunning = false;
    this.terminalWorkflowToken = null;
    this.settingsWindowToken = null;
    this.cancelPendingWhileCommandRunning = false;
    this.stopPendingWhileCommandRunning = false;
    this._statusRefreshToken = 0;
    this._statusCommandToken = null;
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
    this.autoRelistenPendingLanguage = "";
    this.autoRelistenManualStopRequested = false;
    this.autoRelistenSequence = 0;
    this.autoInsertFingerprint = "";
    this.autoInsertFingerprints = [];
    this.autoInsertPendingFingerprint = "";
    this.transcriptListPromptToken = null;
    this.transcriptListPromptDialog = null;
    this.textInsertToken = null;
    this.voiceModelActionToken = null;
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = 0;
    this.transcriptWindowToken = null;
    this.cleanupPreviewDialogToken = null;
    this.cleanupPreviewDialog = null;
    this.clipboardOverwriteDialog = null;
    this._cleanupCommandToken = null;
    this._recordingCommandToken = null;
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
    this.ollamaModelInstallToken = null;
    this.ollamaModelCleanupFailed = false;
    this.voiceModelCleanupFailed = false;
    this.benchmarkCleanupFailed = false;
    this.processCleanupRetryTimer = 0;
    this.textInsertCancellationFailed = false;
    this.externalApiEnvMonitor = null;
    this.externalApiEnvApplyTarget = "voice";
    this.statusIconReady = STATUS_ICON_DEFAULTS.ready;
    this.statusIconRecording = STATUS_ICON_DEFAULTS.recording;
    this.statusIconProcessing = STATUS_ICON_DEFAULTS.processing;
    this.statusIconRecorded = STATUS_ICON_DEFAULTS.recorded;
    this.statusIconError = STATUS_ICON_DEFAULTS.error;
    this.statusIconSetup = STATUS_ICON_DEFAULTS.setup;
    this._resetStatusIconCache();
    this.set_applet_icon_path(this.metadata.path + "/icon.svg");
    this.set_applet_label("");
    this.set_applet_tooltip(_("Speed of Cinnamon"));

    this.settings = new Settings.AppletSettings(this, UUID, instanceId);
    if (!this.settings || this.settings.isReady !== true) {
      throw new Error("Applet settings are unavailable");
    }
    this._bindSettings();
    this._syncExternalApiConfigOnStartup();
    this._syncActiveLanguage();
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this.lifecycleState = LIFECYCLE_RUNNING;
    this._buildMenu();
    this._registerHotkeys();
    this._refreshStatus();
    this._scheduleSetupCheck();
    this._scheduleAlarmCheck(5);
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
    this._bindSetting(Settings.BindingDirection.IN, "show-transcript-text", "showTranscriptText", this._onTranscriptVisibilitySettingsChanged, null);
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
    this._bindSetting(Settings.BindingDirection.IN, "status-icon-ready", "statusIconReady", this._onStatusIconSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "status-icon-recording", "statusIconRecording", this._onStatusIconSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "status-icon-processing", "statusIconProcessing", this._onStatusIconSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "status-icon-recorded", "statusIconRecorded", this._onStatusIconSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "status-icon-error", "statusIconError", this._onStatusIconSettingsChanged, null);
    this._bindSetting(Settings.BindingDirection.IN, "status-icon-setup", "statusIconSetup", this._onStatusIconSettingsChanged, null);
  },

  _buildMenu: function() {
    this.menuManager = new PopupMenu.PopupMenuManager(this);
    this.menu = new PopupMenu.PopupMenu(this.actor, this.orientation);
    Main.uiGroup.add_actor(this.menu.actor);
    this.menu.actor.hide();
    this.menuManager.addMenu(this.menu);
    this._connectSafe(this.menu, "open-state-changed", (menu, open) => {
      if (!this._applet_context_menu || !this._applet_context_menu.isOpen) {
        let actor = this.actor;
        if (actor &&
            (typeof actor.is_finalized !== "function" || !actor.is_finalized()) &&
            typeof actor.change_style_pseudo_class === "function") {
          actor.change_style_pseudo_class("checked", open);
        }
      }
      if (open) {
        if (this._panelRenderFingerprint) {
          this._panelRenderFingerprint.rootMenuOpen = false;
        }
        this._recordingDisplayFingerprint = null;
        let panelUpdated = this._updatePanel(true) === true;
        if (!panelUpdated) {
          let retryTimer = this._scheduleTrackedTimer("panel-open-retry", 1, () => {
            if (!this.menu || this.menu.isOpen !== true) {
              return false;
            }
            this._updatePanel(true);
            return false;
          }, false);
          if (!retryTimer && this._lifecycleAllowsWork() && this.menu && this.menu.isOpen === true) {
            this._updatePanel(true);
          }
        }
      }
    }, "menu-open-state");
    this._connectSafe(this, "orientation-changed", (applet, orientation) => {
      if (this.menu && this.menu.setOrientation) {
        this.menu.setOrientation(orientation);
      }
    }, "menu-orientation");

    this.toggleItem = new PopupMenu.PopupIconMenuItem(_("Start dictation"), "audio-input-microphone-symbolic", St.IconType.SYMBOLIC);
    this._connectSafe(this.toggleItem, "activate", () => {
      if (!this._hasActiveRecordingState() && !this.isCommandRunning && !this._rememberFocusedWindow(true)) {
        return;
      }
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
    this.recordingMenuItem.menu.addMenuItem(this.recorderItem);
    this._populateRecorderMenu();

    this.recordingLimitItem = new PopupMenu.PopupSubMenuMenuItem(_("Duration: 30s"));
    this.recordingMenuItem.menu.addMenuItem(this.recordingLimitItem);
    this._populateRecordingLimitMenu();

    this.recordingOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording options"));
    this.recordingMenuItem.menu.addMenuItem(this.recordingOptionsItem);
    this._populateRecordingOptionsMenu();

    this.notificationOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Notifications"));
    this.recordingMenuItem.menu.addMenuItem(this.notificationOptionsItem);
    this._populateNotificationOptionsMenu();

    this.alarmItem = new PopupMenu.PopupSubMenuMenuItem(_("Alarms"));
    this._connectSafe(this.alarmItem.menu, "open-state-changed", (menu, open) => {
      if (!open) {
        let refreshActive = Boolean(this.alarmMenuRefreshToken);
        this.alarmMenuRefreshToken = null;
        this.alarmMenuRefreshQueued = false;
        if (refreshActive) {
          this._terminateProcessesByGroup("alarm-menu-refresh");
        }
        return;
      }
      this._refreshAlarmMenu();
    });
    this.toolsMenuItem.menu.addMenuItem(this.alarmItem);
    this._populateAlarmMenu([], _("Open menu to load alarms"));

    this.shortcutItem = new PopupMenu.PopupSubMenuMenuItem(_("Keyboard shortcuts"));
    this.toolsMenuItem.menu.addMenuItem(this.shortcutItem);
    this._populateShortcutMenu();

    this.outputMethodItem = new PopupMenu.PopupSubMenuMenuItem(_("Output: Clipboard and paste"));
    this.textOutputMenuItem.menu.addMenuItem(this.outputMethodItem);
    this._populateOutputMethodMenu();

    this.artifactEncryptionItem = new PopupMenu.PopupSubMenuMenuItem(_("Encryption: Secret Service keyring"));
    this.textOutputMenuItem.menu.addMenuItem(this.artifactEncryptionItem);
    this._populateArtifactEncryptionMenu();

    this.textOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Text options"));
    this.textOutputMenuItem.menu.addMenuItem(this.textOptionsItem);
    this._populateTextOptionsMenu();

    this.autoPasteItem = new PopupMenu.PopupSubMenuMenuItem(_("Auto-Submit: codex"));
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
      if (!open) {
        this.historyRefreshToken = null;
        this.historyRefreshQueued = false;
        this._terminateProcessesByGroup("history-refresh");
        return;
      }
      this._refreshHistory();
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
      if (!open) {
        let refreshActive = Boolean(this.inputSourceMenuRefreshToken);
        this.inputSourceMenuRefreshToken = null;
        if (refreshActive) {
          this._terminateProcessesByGroup("input-source-refresh");
        }
        return;
      }
      this._refreshInputSourceMenu();
    });
    this.recordingMenuItem.menu.addMenuItem(this.inputSourceItem);
    this._populateInputSourceMenu([], _("Open menu to load input sources"));

    this.modelItem = new PopupMenu.PopupSubMenuMenuItem(_("Voice model"));
    this._connectSafe(this.modelItem.menu, "open-state-changed", (menu, open) => {
      if (!open) {
        let refreshActive = Boolean(this.modelMenuRefreshToken);
        this.modelMenuRefreshToken = null;
        if (refreshActive) {
          this._terminateProcessesByGroup("model-menu-refresh");
        }
        return;
      }
      this._refreshModelMenu();
    });
    this.recordingMenuItem.menu.addMenuItem(this.modelItem);
    this._populateModelMenu([], _("Open menu to load voice models"));

    this.textModelItem = new PopupMenu.PopupSubMenuMenuItem(_("Text model"));
    this._connectSafe(this.textModelItem.menu, "open-state-changed", (menu, open) => {
      if (open) {
        this._refreshTextModelMenu();
      } else {
        this.textModelMenuRefreshToken = null;
        this._terminateProcessesByGroup("text-model-refresh");
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

  _markHotkeyRebindPending: function(name, binding) {
    try {
      let key = String(name || "").trim();
      if (key === "") {
        throw new Error("Pending hotkey name is invalid");
      }
      if (!this._pendingHotkeyRebinds || typeof this._pendingHotkeyRebinds !== "object") {
        this._pendingHotkeyRebinds = {};
      }
      let normalizedBinding = typeof binding === "string" ? binding.trim() : "";
      let current = this._pendingHotkeyRebinds[key];
      if (!current || current.binding !== normalizedBinding) {
        this._pendingHotkeyRebinds[key] = { attempts: 0, binding: normalizedBinding };
      }
      if (this._lifecycleAllowsWork() && !this.processCleanupRetryTimer) {
        let scheduled = this._scheduleProcessCleanupRetry();
        if (!scheduled) {
          current = this._pendingHotkeyRebinds[key];
          let warningShown = Boolean(current && current.retryWarningShown === true);
          if (current) {
            current.retryWarningShown = true;
          }
          this._recordLifecycleError("hotkey-rebind-schedule", new Error("Hotkey rebind retry could not be scheduled"));
          if (!warningShown) {
            this._setStatusPreservingRecording(
              "error",
              _("Hotkey change could not be retried automatically"),
              this.lastTranscript
            );
          }
          return false;
        }
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("hotkey-rebind-pending", error);
      return false;
    }
  },

  _clearPendingHotkeyRebind: function(name) {
    if (!this._pendingHotkeyRebinds || typeof this._pendingHotkeyRebinds !== "object") {
      this._pendingHotkeyRebinds = {};
      return true;
    }
    let key = String(name || "").trim();
    if (key === "" || !Object.prototype.hasOwnProperty.call(this._pendingHotkeyRebinds, key)) {
      return true;
    }
    let deleted = delete this._pendingHotkeyRebinds[key];
    if (deleted === false || Object.prototype.hasOwnProperty.call(this._pendingHotkeyRebinds, key)) {
      this._recordLifecycleError("hotkey-rebind-pending", new Error("Pending hotkey rebind could not be cleared"));
      return false;
    }
    return true;
  },

  _hotkeySpecs: function() {
    return [
      { id: HOTKEY_ID, key: "toggle-keybinding", propertyName: "toggleKeybinding", binding: this.toggleKeybinding, callback: this._hotkeyCallbacks[HOTKEY_ID] },
      { id: PRIMARY_HOTKEY_ID, key: "primary-language-keybinding", propertyName: "primaryLanguageKeybinding", binding: this.primaryLanguageKeybinding, callback: this._hotkeyCallbacks[PRIMARY_HOTKEY_ID] },
      { id: SECONDARY_HOTKEY_ID, key: "secondary-language-keybinding", propertyName: "secondaryLanguageKeybinding", binding: this.secondaryLanguageKeybinding, callback: this._hotkeyCallbacks[SECONDARY_HOTKEY_ID] },
      { id: CANCEL_HOTKEY_ID, key: "cancel-keybinding", propertyName: "cancelKeybinding", binding: this.cancelKeybinding, callback: this._hotkeyCallbacks[CANCEL_HOTKEY_ID] },
    ];
  },

  _setHotkeyRuntimeBlocked: function(id, blocked) {
    if (!this._blockedHotkeyIds || typeof this._blockedHotkeyIds !== "object") {
      this._blockedHotkeyIds = {};
    }
    let key = String(id || "").trim();
    if (key === "") {
      this._recordLifecycleError("hotkey-runtime-block", new Error("Hotkey id is invalid"));
      return false;
    }
    if (blocked === true) {
      this._blockedHotkeyIds[key] = true;
      return this._blockedHotkeyIds[key] === true;
    }
    let deleted = delete this._blockedHotkeyIds[key];
    if (deleted === false || Object.prototype.hasOwnProperty.call(this._blockedHotkeyIds, key)) {
      this._recordLifecycleError("hotkey-runtime-block", new Error("Hotkey runtime block could not be cleared"));
      return false;
    }
    return true;
  },

  _disableHotkeyAfterRebindFailure: function(name) {
    let removedExternally = false;
    let disabled = this._runStateGuarded("hotkeys", () => {
      let removeResult = Main.keybindingManager.removeHotKey(name);
      if (removeResult === false) {
        throw new Error("Hotkey could not be disabled after rebind failure");
      }
      removedExternally = true;
      if (this._resourceRegistry && this._resourceRegistry.hotkeys) {
        let deleted = delete this._resourceRegistry.hotkeys[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
          throw new Error("Hotkey registry could not be cleared after rebind failure");
        }
      }
      if (this._hotkeyDefinitions) {
        let deleted = delete this._hotkeyDefinitions[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
          throw new Error("Hotkey definition could not be cleared after rebind failure");
        }
      }
      if (!this._untrackOrphanedHotkey(name)) {
        throw new Error("Hotkey orphan state could not be cleared after rebind failure");
      }
      return true;
    }, false) === true;
    if (!disabled && !removedExternally) {
      return false;
    }
    if (!disabled) {
      this._trackOrphanedHotkey(name, true);
    }
    return true;
  },

  _retryPendingHotkeyRebinds: function() {
    if (!this._lifecycleAllowsWork()) {
      return true;
    }
    if (!this._pendingHotkeyRebinds || typeof this._pendingHotkeyRebinds !== "object") {
      this._pendingHotkeyRebinds = {};
      return true;
    }
    let pendingNames = Object.keys(this._pendingHotkeyRebinds);
    if (pendingNames.length === 0) {
      return true;
    }
    let specs = this._hotkeySpecs();
    let specsByName = {};
    for (let index = 0; index < specs.length; index++) {
      specsByName[this._hotkeyName(specs[index].id)] = specs[index];
    }
    for (let index = 0; index < pendingNames.length; index++) {
      let name = pendingNames[index];
      let state = this._pendingHotkeyRebinds[name];
      let spec = specsByName[name];
      if (!spec) {
        this._clearPendingHotkeyRebind(name);
        continue;
      }
      let desiredBinding = typeof spec.binding === "string" ? spec.binding.trim() : "";
      let attempts = state && state.binding === desiredBinding && Number.isFinite(state.attempts)
        ? state.attempts
        : 0;
      if (attempts >= HOTKEY_REBIND_MAX_RETRIES) {
        let active = this._hotkeyDefinitions && this._hotkeyDefinitions[name];
        let activeBinding = active && typeof active.binding === "string" ? active.binding.trim() : "";
        let activeTracked = Boolean(
          this._resourceRegistry && this._resourceRegistry.hotkeys &&
          this._resourceRegistry.hotkeys[name] === true
        );
        let activeOwned = Boolean(active && active.callback === spec.callback && activeBinding !== "");
        let orphaned = Boolean(
          (Array.isArray(this._orphanedHotkeys) && this._orphanedHotkeys.indexOf(name) >= 0) ||
          (this._orphanedHotkeyStates &&
            Object.prototype.hasOwnProperty.call(this._orphanedHotkeyStates, name))
        );
        let rollbackAttempted = Boolean(state && state.rollbackAttempted === true);
        let terminalNotified = Boolean(state && state.terminalNotified === true);
        if (activeTracked && activeOwned && !orphaned && !rollbackAttempted) {
          let pendingCleared = this._clearPendingHotkeyRebind(name);
          if (pendingCleared && this._commitSettingValue(
            spec.propertyName,
            spec.key,
            activeBinding,
            "hotkey-rebind-rollback",
            _("Hotkey setting could not be restored; disabling shortcut")
          )) {
            this._setStatusPreservingRecording(
              "error",
              _("Hotkey binding could not be applied; previous shortcut restored"),
              this.lastTranscript
            );
            continue;
          }
          this._pendingHotkeyRebinds[name] = {
            attempts: attempts,
            binding: desiredBinding,
            rollbackAttempted: true,
            terminalNotified: true,
          };
          terminalNotified = true;
        }
        let runtimeDisabled = Boolean(state && state.runtimeDisabled === true);
        let disableAttempts = state && Number.isFinite(state.disableAttempts) ? state.disableAttempts : 0;
        if (!runtimeDisabled) {
          runtimeDisabled = this._disableHotkeyAfterRebindFailure(name);
          if (!runtimeDisabled) {
            disableAttempts += 1;
            if (disableAttempts < HOTKEY_REBIND_MAX_RETRIES) {
              this._pendingHotkeyRebinds[name] = {
                attempts: attempts,
                binding: desiredBinding,
                rollbackAttempted: true,
                terminalNotified: terminalNotified,
                runtimeDisabled: false,
                disableAttempts: disableAttempts,
                settingAttempts: 0,
              };
              continue;
            }
            this._setHotkeyRuntimeBlocked(spec.id, true);
            this._clearPendingHotkeyRebind(name);
            if (!terminalNotified) {
              this._setStatusPreservingRecording(
                "error",
                _("Hotkey binding could not be removed; shortcut blocked for this session"),
                this.lastTranscript
              );
            }
            continue;
          }
          this._setHotkeyRuntimeBlocked(spec.id, true);
          this._pendingHotkeyRebinds[name] = {
            attempts: attempts,
            binding: desiredBinding,
            rollbackAttempted: true,
            terminalNotified: terminalNotified,
            runtimeDisabled: true,
            disableAttempts: disableAttempts,
            settingAttempts: 0,
          };
        }
        state = this._pendingHotkeyRebinds[name];
        let settingAttempts = state && Number.isFinite(state.settingAttempts) ? state.settingAttempts : 0;
        let settingCleared = this._commitSettingValue(
          spec.propertyName,
          spec.key,
          "",
          "hotkey-rebind-disable",
          settingAttempts === 0
            ? _("Shortcut was disabled for this session, but its setting could not be saved")
            : ""
        );
        if (settingCleared) {
          this._clearPendingHotkeyRebind(name);
          this._setStatusPreservingRecording(
            "error",
            _("Hotkey binding could not be applied; shortcut disabled"),
            this.lastTranscript
          );
          continue;
        }
        settingAttempts += 1;
        if (settingAttempts >= HOTKEY_REBIND_MAX_RETRIES) {
          this._clearPendingHotkeyRebind(name);
          continue;
        }
        this._pendingHotkeyRebinds[name] = {
          attempts: attempts,
          binding: desiredBinding,
          rollbackAttempted: true,
          terminalNotified: true,
          runtimeDisabled: true,
          disableAttempts: disableAttempts,
          settingAttempts: settingAttempts,
        };
        continue;
      }
      this._pendingHotkeyRebinds[name] = {
        attempts: attempts + 1,
        binding: desiredBinding,
        rollbackAttempted: false,
        terminalNotified: false,
      };
      this._registerHotkey(spec.id, spec.binding, spec.callback);
    }
    return Object.keys(this._pendingHotkeyRebinds).length === 0;
  },

  _registerHotkey: function(id, binding, callback) {
    if (!this._lifecycleAllowsWork()) {
      return false;
    }
    let name = this._hotkeyName(id);
    let accelerator = typeof binding === "string" ? binding.trim() : "";
    let previous = this._hotkeyDefinitions && this._hotkeyDefinitions[name]
      ? this._hotkeyDefinitions[name]
      : null;
    let registryTracksHotkey = Boolean(
      this._resourceRegistry && this._resourceRegistry.hotkeys &&
      this._resourceRegistry.hotkeys[name] === true
    );
    let orphaned = Boolean(
      (Array.isArray(this._orphanedHotkeys) && this._orphanedHotkeys.indexOf(name) >= 0) ||
      (this._orphanedHotkeyStates &&
        Object.prototype.hasOwnProperty.call(this._orphanedHotkeyStates, name))
    );
    let pending = Boolean(
      this._pendingHotkeyRebinds &&
      Object.prototype.hasOwnProperty.call(this._pendingHotkeyRebinds, name)
    );
    if (!orphaned && !pending &&
        ((accelerator === "" && !previous && !registryTracksHotkey) ||
         (accelerator !== "" && previous && previous.binding === accelerator &&
          previous.callback === callback && registryTracksHotkey))) {
      return this._setHotkeyRuntimeBlocked(id, false);
    }
    let removedExternally = false;
    let removeAttemptFailed = false;
    let removed = this._runStateGuarded("hotkeys", () => {
      try {
        let removeResult = Main.keybindingManager.removeHotKey(name);
        if (removeResult === false) {
          throw new Error("Hotkey could not be removed");
        }
      } catch (error) {
        removeAttemptFailed = true;
        throw error;
      }
      removedExternally = true;
      if (!this._untrackOrphanedHotkey(name)) {
        throw new Error("Existing hotkey orphan cleanup failed");
      }
      if (this._resourceRegistry) {
        let deleted = delete this._resourceRegistry.hotkeys[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
          throw new Error("Hotkey registry entry could not be removed");
        }
      }
      return true;
    }, false) === true;
    if (!removed) {
      if (removeAttemptFailed || removedExternally) {
        this._trackOrphanedHotkey(name, removedExternally);
      }
      if (removedExternally && previous) {
        let restored = this._runStateGuarded("hotkeys", () => {
          return Main.keybindingManager.addHotKey(
            name,
            previous.binding,
            this._guardStateCallback("hotkeys", previous.callback, undefined)
          ) === true;
        }, false) === true;
        if (restored && this._orphanedHotkeyStates) {
          this._orphanedHotkeyStates[name] = false;
        }
      }
      this._markHotkeyRebindPending(name, accelerator);
      return false;
    }
    if (accelerator === "") {
      if (this._hotkeyDefinitions) {
        let definitionCleanupSucceeded = this._runStateGuarded("hotkeys", () => {
          let deleted = delete this._hotkeyDefinitions[name];
          if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
            throw new Error("Hotkey definition could not be removed");
          }
          return true;
        }, false) === true;
        if (!definitionCleanupSucceeded) {
          this._trackOrphanedHotkey(name, true);
          this._markHotkeyRebindPending(name, accelerator);
          return false;
        }
      }
      if (!this._setHotkeyRuntimeBlocked(id, false)) {
        this._markHotkeyRebindPending(name, accelerator);
        return false;
      }
      return this._clearPendingHotkeyRebind(name);
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
        let definition = { binding: accelerator, callback: callback };
        this._hotkeyDefinitions[name] = definition;
        if (!Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name) ||
            this._resourceRegistry.hotkeys[name] !== true ||
            !Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name) ||
            this._hotkeyDefinitions[name] !== definition) {
          throw new Error("Hotkey could not be registered");
        }
        return true;
      }, false) === true;
      if (tracked) {
        if (!this._setHotkeyRuntimeBlocked(id, false)) {
          this._markHotkeyRebindPending(name, accelerator);
          return false;
        }
        return this._clearPendingHotkeyRebind(name);
      }
      let rollbackRemoved = this._runStateGuarded("hotkeys", () => {
        let removeResult = Main.keybindingManager.removeHotKey(name);
        if (removeResult === false) {
          throw new Error("Hotkey rollback removal failed");
        }
        if (!this._untrackOrphanedHotkey(name)) {
          throw new Error("Hotkey rollback orphan cleanup failed");
        }
        return true;
      }, false) === true;
      if (!rollbackRemoved) {
        this._trackOrphanedHotkey(name);
      }
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
          if (!this._resourceRegistry ||
              !Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name) ||
              this._resourceRegistry.hotkeys[name] !== true ||
              !Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name) ||
              this._hotkeyDefinitions[name] !== previous) {
            throw new Error("Previous hotkey could not be restored");
          }
          return true;
        }, false) === true;
        if (tracked) {
          this._markHotkeyRebindPending(name, accelerator);
          return false;
        }
        let rollbackRemoved = this._runStateGuarded("hotkeys", () => {
          let removeResult = Main.keybindingManager.removeHotKey(name);
          if (removeResult === false) {
            throw new Error("Previous hotkey rollback removal failed");
          }
          if (!this._untrackOrphanedHotkey(name)) {
            throw new Error("Previous hotkey rollback orphan cleanup failed");
          }
          return true;
        }, false) === true;
        if (!rollbackRemoved) {
          this._trackOrphanedHotkey(name);
        }
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
    this._markHotkeyRebindPending(name, accelerator);
    return false;
  },

  _trackOrphanedHotkey: function(name, externallyRemoved) {
    try {
      let key = String(name || "").trim();
      if (key === "") {
        throw new Error("Hotkey orphan name is invalid");
      }
      if (!Array.isArray(this._orphanedHotkeys)) {
        this._orphanedHotkeys = [];
      }
      if (!this._orphanedHotkeyStates || typeof this._orphanedHotkeyStates !== "object") {
        this._orphanedHotkeyStates = {};
      }
      if (this._orphanedHotkeys.indexOf(key) < 0) {
        this._orphanedHotkeys.push(key);
        if (this._orphanedHotkeys.indexOf(key) < 0) {
          throw new Error("Hotkey orphan entry could not be tracked");
        }
      }
      if (externallyRemoved === true) {
        this._orphanedHotkeyStates[key] = true;
      } else if (!Object.prototype.hasOwnProperty.call(this._orphanedHotkeyStates, key)) {
        this._orphanedHotkeyStates[key] = false;
      }
      if (this._lifecycleAllowsWork() && !this.processCleanupRetryTimer) {
        this._scheduleProcessCleanupRetry();
      }
      return true;
    } catch (error) {
      this._recordLifecycleError("hotkey-orphan", error);
      return false;
    }
  },

  _untrackOrphanedHotkey: function(name) {
    if (!Array.isArray(this._orphanedHotkeys)) {
      return true;
    }
    let success = true;
    let key = String(name || "").trim();
    for (let index = this._orphanedHotkeys.length - 1; index >= 0; index--) {
      if (this._orphanedHotkeys[index] !== key) {
        continue;
      }
      let entry = this._orphanedHotkeys[index];
      let removedFromArray = false;
      try {
        let removed = this._orphanedHotkeys.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this._orphanedHotkeys.indexOf(entry) >= 0) {
          throw new Error("Hotkey orphan entry could not be removed");
        }
        removedFromArray = true;
        if (this._orphanedHotkeyStates && Object.prototype.hasOwnProperty.call(this._orphanedHotkeyStates, key)) {
          let deleted = delete this._orphanedHotkeyStates[key];
          if (deleted === false || Object.prototype.hasOwnProperty.call(this._orphanedHotkeyStates, key)) {
            throw new Error("Hotkey orphan state could not be removed");
          }
        }
      } catch (error) {
        if (removedFromArray) {
          try {
            if (this._orphanedHotkeys.indexOf(entry) < 0) {
              let previousLength = this._orphanedHotkeys.length;
              this._orphanedHotkeys.splice(index, 0, entry);
              if (this._orphanedHotkeys.length !== previousLength + 1 || this._orphanedHotkeys[index] !== entry) {
                throw new Error("Hotkey orphan rollback could not restore the entry");
              }
            }
          } catch (rollbackError) {
            this._recordLifecycleError("hotkey-orphan-rollback", rollbackError);
          }
        }
        this._recordLifecycleError("hotkey-orphan", error);
        success = false;
      }
    }
    return success;
  },

  _retryOrphanedHotkeys: function(includeTracked) {
    let inTeardown = this.appletRemoved ||
      this.lifecycleState === LIFECYCLE_REMOVING ||
      this.lifecycleState === LIFECYCLE_REMOVED;
    let includeTrackedHotkeys = includeTracked === true || inTeardown;
    let pendingNames = [];
    let addPendingName = (value) => {
      let name = String(value || "").trim();
      if (name !== "" && pendingNames.indexOf(name) < 0) {
        pendingNames.push(name);
      }
    };
    let collectPendingNames = (values) => {
      if (!values || (typeof values !== "object" && typeof values !== "function")) {
        return;
      }
      for (let name in values) {
        if (Object.prototype.hasOwnProperty.call(values, name)) {
          addPendingName(name);
        }
      }
    };
    if (Array.isArray(this._orphanedHotkeys)) {
      for (let name of this._orphanedHotkeys) {
        addPendingName(name);
      }
    }
    if (!Array.isArray(this._orphanedHotkeys)) {
      this._recordLifecycleError("hotkey-state", new Error("Hotkey orphan registry is unavailable"));
    }
    collectPendingNames(this._orphanedHotkeyStates);
    if (includeTrackedHotkeys) {
      collectPendingNames(this._resourceRegistry && this._resourceRegistry.hotkeys);
      collectPendingNames(this._hotkeyDefinitions);
    }
    if (pendingNames.length === 0) {
      return true;
    }
    let success = true;
    for (let index = pendingNames.length - 1; index >= 0; index--) {
      let name = pendingNames[index];
      let externallyRemoved = this._orphanedHotkeyStates && this._orphanedHotkeyStates[name] === true;
      let trackedLiveHotkey = !inTeardown && !externallyRemoved && Boolean(
        (this._resourceRegistry && this._resourceRegistry.hotkeys &&
          Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) ||
        (this._hotkeyDefinitions && Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name))
      );
      if (trackedLiveHotkey) {
        if (!this._untrackOrphanedHotkey(name)) {
          success = false;
        }
        continue;
      }
      if (!externallyRemoved) {
        if (!this._runTeardownOperation(
          "teardown-orphaned-hotkeys",
          Main && Main.keybindingManager,
          "removeHotKey",
          [name]
        )) {
          success = false;
          continue;
        }
        this._trackOrphanedHotkey(name, true);
      }
      try {
        if (this._resourceRegistry) {
          if (!this._resourceRegistry.hotkeys) {
            throw new Error("Hotkey registry is unavailable");
          }
          if (Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
            let deleted = delete this._resourceRegistry.hotkeys[name];
            if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
              throw new Error("Hotkey registry entry could not be removed during orphan cleanup");
            }
          }
        }
        if (this._hotkeyDefinitions && Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
          let deleted = delete this._hotkeyDefinitions[name];
          if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
            throw new Error("Hotkey definition could not be removed during orphan cleanup");
          }
        }
        if (!this._untrackOrphanedHotkey(name)) {
          throw new Error("Hotkey orphan state could not be completed");
        }
      } catch (error) {
        this._recordLifecycleError("teardown-orphaned-hotkeys", error);
        success = false;
      }
    }
    return success;
  },

  _removeHotkey: function(id) {
    let name = this._hotkeyName(id);
    this._clearPendingHotkeyRebind(name);
    let removed = false;
    this._runTeardownGuarded("teardown-hotkeys", () => {
      let removeResult = Main.keybindingManager.removeHotKey(name);
      if (removeResult === false) {
        throw new Error("Hotkey removal failed during teardown");
      }
      removed = true;
    });
    if (!removed) {
      this._trackOrphanedHotkey(name, false);
      return;
    }
    let registryCleanupSucceeded = true;
    if (this._resourceRegistry) {
      try {
        if (!this._resourceRegistry.hotkeys) {
          throw new Error("Hotkey registry is unavailable");
        }
        let deleted = delete this._resourceRegistry.hotkeys[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._resourceRegistry.hotkeys, name)) {
          throw new Error("Hotkey registry entry could not be removed during teardown");
        }
      } catch (error) {
        this._recordLifecycleError("teardown-hotkeys", error);
        registryCleanupSucceeded = false;
      }
    }
    let definitionCleanupSucceeded = true;
    if (this._hotkeyDefinitions) {
      try {
        let deleted = delete this._hotkeyDefinitions[name];
        if (deleted === false || Object.prototype.hasOwnProperty.call(this._hotkeyDefinitions, name)) {
          throw new Error("Hotkey definition could not be removed during teardown");
        }
      } catch (error) {
        this._recordLifecycleError("teardown-hotkeys", error);
        definitionCleanupSucceeded = false;
      }
    }
    if (!registryCleanupSucceeded || !definitionCleanupSucceeded) {
      this._trackOrphanedHotkey(name, true);
    } else if (!this._untrackOrphanedHotkey(name)) {
      this._trackOrphanedHotkey(name, true);
    }
  },

  _registerHotkeys: function() {
    this._runStateGuarded("hotkeys", () => {
      let specs = this._hotkeySpecs();
      for (let index = 0; index < specs.length; index++) {
        this._registerHotkey(specs[index].id, specs[index].binding, specs[index].callback);
      }
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

  _onTranscriptVisibilitySettingsChanged: function() {
    this._onTextOutputSettingsChanged();
    if (this.historyItem && this.historyItem.menu && this.historyItem.menu.isOpen === true) {
      this.historyRefreshToken = null;
      this.historyRefreshQueued = false;
      this._refreshHistory();
    }
  },

  _onTextOutputSettingsChanged: function() {
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    this._historyMenuFingerprint = null;
    let promptCleanupSucceeded = this._terminateProcessesByGroup("settings-prompt") !== false;
    this._cancelTextInsertForSettingsChange();
    this.typingDelayMs = this._normalizeTypingDelayMs(this.typingDelayMs);
    this.artifactEncryption = this._normalizeArtifactEncryption(this.artifactEncryption);
    this._populateArtifactEncryptionMenu();
    this._populateTextOptionsMenu();
    this._updateAutoPasteItem();
    this._populateAutoPasteMenu();
    this._updatePanel();
    if (!promptCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Settings prompt could not be stopped"), this.lastTranscript);
    }
  },

  _onTranscriptRetentionSettingsChanged: function() {
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    let promptCleanupSucceeded = this._terminateProcessesByGroup("settings-prompt") !== false;
    this.maxTranscriptFiles = this._normalizeTranscriptLimit(this.maxTranscriptFiles);
    this._updatePanel();
    if (!promptCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Settings prompt could not be stopped"), this.lastTranscript);
    }
  },

  _onRecorderSettingsChanged: function() {
    this.recorder = this._normalizeRecorder(this.recorder);
    this._populateRecorderMenu();
    this._updatePanel();
  },

  _onRecordingLimitSettingsChanged: function() {
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    let promptCleanupSucceeded = this._terminateProcessesByGroup("settings-prompt") !== false;
    this.maxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this._populateRecordingLimitMenu();
    this._updatePanel();
    if (!promptCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Settings prompt could not be stopped"), this.lastTranscript);
    }
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
    let inputSourceCleanupSucceeded = this._terminateProcessesByGroup("input-source-refresh") !== false;
    this._populateInputSourceMenu([], _("Open menu to load input sources"));
    this._updatePanel();
    if (!inputSourceCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Input source refresh could not be stopped"), this.lastTranscript);
    }
  },

  _onVoiceBackendSettingsChanged: function() {
    this.modelMenuRefreshToken = null;
    let modelMenuCleanupSucceeded = this._terminateProcessesByGroup("model-menu-refresh") !== false;
    let hadVoiceModelAction = Boolean(this.voiceModelActionToken);
    let hadVoiceModelCleanupFailure = this.voiceModelCleanupFailed === true;
    this.voiceModelActionToken = null;
    let voiceModelCleanupSucceeded = this._terminateProcessesByGroup("voice-model") !== false;
    if (voiceModelCleanupSucceeded) {
      let busyStateReleased = this._releaseBusyStateAfterProcessCleanup(
        "voice-model",
        "voiceModelCleanupFailed",
        hadVoiceModelAction || hadVoiceModelCleanupFailure
      );
      if ((hadVoiceModelAction || hadVoiceModelCleanupFailure) && busyStateReleased &&
          !this._recordingCommandToken && !this.isCommandRunning && this.status === "processing") {
        this._setStatus("ready", _("Voice model operation cancelled by settings change"), this.lastTranscript);
      }
    } else {
      this.voiceModelCleanupFailed = true;
    }
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this._populateModelMenu([], _("Open menu to load voice models"));
    this._updatePanel();
    if (!voiceModelCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Voice model operation could not be stopped"), this.lastTranscript);
    }
    if (!modelMenuCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Voice model list refresh could not be stopped"), this.lastTranscript);
    }
  },

  _onTextModelSettingsChanged: function() {
    this.textModelMenuRefreshToken = null;
    let textModelRefreshCleanupSucceeded = this._terminateProcessesByGroup("text-model-refresh") !== false;
    let hadOllamaOperation = Boolean(
      this.ollamaModelFlowToken ||
      this.ollamaInstallWatchToken ||
      this.ollamaModelInstallRunning ||
      this.ollamaModelInstallToken ||
      this.ollamaModelCleanupFailed
    );
    let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
    let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
    if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return;
    }
    if (hadOllamaOperation && !this.notificationSessionActive && !this._recordingCommandToken &&
        !this.isCommandRunning && !this._hasLocalProcessingWorkflow() && this.status === "processing") {
      this._setStatus("ready", _("Ollama operation cancelled by settings change"), this.lastTranscript);
    }
    this._populateTextModelMenu([], _("Open menu to load local text models"));
    this._updatePanel();
    if (!textModelRefreshCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Text model list refresh could not be stopped"), this.lastTranscript);
    }
  },

  _onOpenAiFlexProcessingSettingsChanged: function() {
    this.openaiCompatibleFlexProcessing = Boolean(this.openaiCompatibleFlexProcessing);
    this._updateOpenAiFlexProcessingItem();
    this._updatePanel();
  },

  _cancelTextInsertForSettingsChange: function() {
    this.targetWindowGeneration = Number(this.targetWindowGeneration || 0) + 1;
    this._clearClipboardOverwriteApproval();
    let dialogCleanupSucceeded = true;
    if (this.clipboardOverwriteDialog) {
      dialogCleanupSucceeded = this._dialogClose(this.clipboardOverwriteDialog, "clipboard-overwrite");
      if (dialogCleanupSucceeded) {
        this.clipboardOverwriteDialog = null;
      } else {
        this._setStatusPreservingRecording("error", _("Clipboard overwrite prompt could not be stopped"), this.lastTranscript);
      }
    }
    let hadInsertToken = Boolean(this.textInsertToken);
    let pendingInsertFingerprint = String(this.autoInsertPendingFingerprint || "");
    let fingerprintCleanupSucceeded = true;
    let pasteTimerCleanupSucceeded = this._clearPasteTimer() !== false;
    if (hadInsertToken) {
      this.textInsertToken = null;
    }
    if (pendingInsertFingerprint !== "") {
      fingerprintCleanupSucceeded = this._forgetAutoInsertFingerprint(pendingInsertFingerprint) !== false;
      if (fingerprintCleanupSucceeded && this.autoInsertPendingFingerprint === pendingInsertFingerprint) {
        this.autoInsertPendingFingerprint = "";
      }
    }
    let cancellationSucceeded = true;
    if (this._terminateProcessesByGroup("keyboard") === false) {
      cancellationSucceeded = false;
    }
    if (this._terminateProcessesByGroup("clipboard") === false) {
      cancellationSucceeded = false;
    }
    if (this._terminateProcessesByGroup("x11") === false) {
      cancellationSucceeded = false;
    }
    if (!fingerprintCleanupSucceeded) {
      cancellationSucceeded = false;
    }
    if (!pasteTimerCleanupSucceeded) {
      cancellationSucceeded = false;
    }
    if (!dialogCleanupSucceeded) {
      cancellationSucceeded = false;
    }
    this.textInsertCancellationFailed = !cancellationSucceeded;
    if (hadInsertToken && this.autoRelistenPending) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenPendingLanguage = "";
      this.autoRelistenManualStopRequested = true;
    }
    return cancellationSucceeded;
  },

  on_applet_clicked: function() {
    return this._runStateGuarded("menu-toggle", () => {
      let menu = this.menu;
      if (!menu || typeof menu.open !== "function" || typeof menu.close !== "function") {
        return;
      }
      if (menu.isOpen) {
        this._closeMenuSafely(menu, true, true);
        return;
      }
      this._closeMenuSafely(menu, false, true);
      this._rememberFocusedWindow();
      menu.open(true);
    }, undefined);
  },

  on_applet_removed_from_panel: function() {
    if (!this._beginTeardown()) {
      return;
    }
    this._statusRefreshToken++;
    this._statusCommandToken = null;
    this._statusCommandRunning = false;
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenPendingLanguage = "";
    this.autoRelistenManualStopRequested = false;
    this.terminalWorkflowRunning = false;
    this.terminalWorkflowToken = null;
    this.modelMenuRefreshToken = null;
    this.textModelMenuRefreshToken = null;
    this.historyRefreshToken = null;
    this.historyRefreshQueued = false;
    this.alarmMenuRefreshToken = null;
    this.alarmMenuRefreshQueued = false;
    this.inputSourceMenuRefreshToken = null;
    this.voiceModelActionToken = null;
    this.ollamaModelFlowToken = null;
    this.ollamaInstallWatchToken = null;
    this.ollamaModelInstallRunning = false;
    this.ollamaModelInstallToken = null;
    this.ollamaModelCleanupFailed = false;
    this.voiceModelCleanupFailed = false;
    this.benchmarkCleanupFailed = false;
    this.textInsertCancellationFailed = false;
    this.benchmarkFlowToken = null;
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    this.transcriptListPromptToken = null;
    this.transcriptListPromptDialog = null;
    this.transcriptWindowToken = null;
    this.cleanupPreviewDialogToken = null;
    this.cleanupPreviewDialog = null;
    this.clipboardOverwriteDialog = null;
    this.textInsertToken = null;
    this.autoInsertPendingFingerprint = "";
    this.settingsWindowToken = null;
    this.alarmActionToken = null;
    this.alarmCheckToken = null;
    this.settingsTransferToken = null;
    this.setupDiagnosticsToken = null;
    this.doctorCommandToken = null;
    this._doctorCommandRunning = false;
    this._cleanupCommandToken = null;
    this._recordingCommandToken = null;
    this.isCommandRunning = false;
    this._runTeardownGuarded("teardown-processes", () => this._terminateAllProcesses());
    this._runTeardownGuarded("teardown-orphaned-processes", () => this._retryOrphanedProcesses());
    this._runTeardownGuarded("teardown-cancellables", () => this._cancelAllCancellables());
    this._runTeardownGuarded("teardown-orphaned-cancellables", () => this._retryOrphanedCancellables());
    this._runTeardownGuarded("teardown-dialogs", () => this._destroyTrackedDialogs());
    this._runTeardownGuarded("teardown-orphaned-dialogs", () => this._retryOrphanedDialogs());
    this._runTeardownGuarded("teardown-timer", () => this._clearStatusTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearDisplayTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearSetupCheckTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearPasteTimer());
    this._runTeardownGuarded("teardown-clipboard", () => this._clearClipboardOverwriteApproval());
    this._runTeardownGuarded("teardown-timer", () => this._clearAlarmTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearOllamaInstallWatchTimer());
    this._runTeardownGuarded("teardown-timer", () => this._clearProcessCleanupRetryTimer());
    this._runTeardownGuarded("teardown-orphaned-timers", () => this._retryOrphanedTimers());
    this._runTeardownGuarded("teardown-monitor", () => this._clearExternalApiEnvMonitor());
    this._runTeardownGuarded("teardown-signals", () => this._disconnectAllSignals());
    this._runTeardownGuarded("teardown-orphaned-monitors", () => this._retryOrphanedMonitors());
    this._runTeardownGuarded("teardown-applet-signals", () => {
      if (this.disconnectAllSignals) {
        this.disconnectAllSignals();
      }
    });
    this._pendingHotkeyRebinds = {};
    this._removeHotkey(HOTKEY_ID);
    this._removeHotkey(PRIMARY_HOTKEY_ID);
    this._removeHotkey(SECONDARY_HOTKEY_ID);
    this._removeHotkey(CANCEL_HOTKEY_ID);
    this._runTeardownGuarded("teardown-orphaned-hotkeys", () => this._retryOrphanedHotkeys());
    this._runTeardownGuarded("teardown-menus", () => this._destroyMenus());
    if (this.settings) {
      this._runTeardownGuarded("teardown-settings", () => this.settings.finalize());
    }
    this._runTeardownGuarded("teardown-orphaned-menus", () => this._retryOrphanedMenus());
    this._finishTeardown();
    this._destroyAppletTooltip();
    this._runTeardownGuarded("teardown-orphaned-tooltip", () => this._retryOrphanedTooltip());
  },

  _baseArgs: function(command, languageOverride) {
    let safeInputDevice = this._coerceCliTextArgOrFallback(this.inputDevice, "input device", "");
    let safeTranscriberCommand = this._coerceCliTextArgOrFallback(this.transcriberCommand, "transcriber command", "");
    let safePostProcessCommand = this._coerceCliTextArgOrFallback(this.postProcessCommand, "post-process command", "");
    let safeOllamaUrl = this._validatedExternalApiUrlOrFallback(this.ollamaUrl, "ollama URL", DEFAULT_OLLAMA_URL);
    let safeOllamaModel = this._coerceCliTextArgOrFallback(this.ollamaModel, "ollama model", "");
    let safeOpenAiCompatibleUrl = this._validatedExternalApiUrlOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL);
    let safeOpenAiCompatibleModel = this._coerceCliTextArgOrFallback(this.openaiCompatibleModel, "openai-compatible model", DEFAULT_OPENAI_COMPATIBLE_MODEL);
    let safeOpenAiCompatibleTextModel = this._coerceCliTextArgOrFallback(this.openaiCompatibleTextModel, "openai-compatible text model", DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL);
    let safePostProcessPrompt = this._coerceCliTextArgOrFallback(this._effectivePostProcessPrompt(), "post-process prompt", "");
    let safeWhisperModel = this._coerceCliTextArgOrFallback(this.whisperModel, "whisper model", "");
    let safePersonalContext = this._coerceCliTextArgOrFallback(this._singleLineCliTextValue(this.personalContext), "personal context", "");
    let safeVocabulary = this._coerceCliTextArgOrFallback(this._singleLineCliTextValue(this.vocabulary), "vocabulary", "");
    let safeRecorder = this._normalizeRecorder(this.recorder);
    let safeLanguage = this._normalizeLanguage(languageOverride, this._currentLanguage());
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
      "--language", safeLanguage,
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
    if (!Boolean(this.openaiCompatibleFlexProcessing) &&
      !this._appendCliFlagWithinBudget(args, "--no-openai-compatible-flex-processing")) {
      throw new Error("OpenAI-compatible Flex setting exceeds command limit");
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

  _appendCliFlagWithinBudget: function(args, flag) {
    if (!Array.isArray(args) || typeof flag !== "string") {
      return false;
    }
    let flagBytes = utf8ByteLength(flag);
    if (flagBytes > MAX_CLI_ARG_BYTES) {
      this._logLifecycleError("settings-value", new Error("optional CLI flag exceeds argument limit"));
      return false;
    }
    let totalBytes = 0;
    for (let arg of args) {
      totalBytes += utf8ByteLength(arg);
    }
    if (totalBytes + flagBytes > MAX_CLI_COMMAND_BYTES) {
      this._logLifecycleError("settings-value", new Error("optional CLI flag exceeds command limit"));
      return false;
    }
    args.push(flag);
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
    let safeOllamaUrl = this._validatedExternalApiUrlOrFallback(this.ollamaUrl, "ollama URL", DEFAULT_OLLAMA_URL);
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
    let args = [this._cliCommand(), "history", "--limit", "5"];
    if (this.showTranscriptText === true) {
      args.push("--confirm-plaintext");
    }
    args.push("--json");
    return args;
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
    let safeOllamaUrl = this._validatedExternalApiUrlOrFallback(this.ollamaUrl, "ollama URL", DEFAULT_OLLAMA_URL);
    let safeOpenAiCompatibleUrl = this._validatedExternalApiUrlOrFallback(this.openaiCompatibleUrl, "openai-compatible URL", DEFAULT_OPENAI_COMPATIBLE_URL);

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
      if (!this.isCommandRunning && !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow()) {
        this._setStatusPreservingRecording("error", _("Could not prepare text model request: ") + safeError, this.lastTranscript);
      }
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
      if (
        this._cliCommandCache &&
        this._cliCommandCache.configured === configured &&
        this._cliCommandCache.command
      ) {
        return this._cliCommandCache.command;
      }
      if (configured !== "") {
        if (configured.indexOf("~/") === 0) {
          configured = GLib.build_filenamev([GLib.get_home_dir(), configured.substring(2)]);
        }
        if (configured.charAt(0) === "/" && GLib.file_test(configured, GLib.FileTest.IS_EXECUTABLE)) {
          this._cliCommandCache = { configured: String(this.cliPath || "").trim(), command: configured };
          return configured;
        }
      }
      if (GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE)) {
        this._cliCommandCache = { configured: String(this.cliPath || "").trim(), command: DEFAULT_CLI };
        return DEFAULT_CLI;
      }
      if (GLib.file_test(SYSTEM_CLI, GLib.FileTest.IS_EXECUTABLE)) {
        this._cliCommandCache = { configured: String(this.cliPath || "").trim(), command: SYSTEM_CLI };
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
    if (!this._canMutateMenu(this.recorderItem)) {
      return;
    }
    if (!this._clearMenuItems(this.recorderItem.menu)) {
      return;
    }
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
    if (!this._canMutateMenu(this.recordingLimitItem)) {
      return;
    }
    if (!this._clearMenuItems(this.recordingLimitItem.menu)) {
      return;
    }
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
    this._spawnText(recordingPromptArgs, (output, result) => {
      if (this.customLimitPromptToken !== promptToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.customLimitPromptToken = null;
      if (result && result.startupFailed === true) {
        this._setStatusPreservingRecording("error", _("Could not open custom duration prompt"), this.lastTranscript);
        return;
      }
      if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
        return;
      }
      let seconds = this._parseCustomRecordingLimit(output);
      if (seconds === null) {
        return;
      }
      this._selectRecordingLimit(seconds);
    }, { resourceGroup: "settings-prompt" });
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

  _populateRecordingOptionsMenu: function() {
    if (!this._canMutateMenu(this.recordingOptionsItem)) {
      return;
    }
    if (!this._clearMenuItems(this.recordingOptionsItem.menu)) {
      return;
    }

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
    if (!this._canMutateMenu(this.notificationOptionsItem)) {
      return;
    }
    if (!this._clearMenuItems(this.notificationOptionsItem.menu)) {
      return;
    }

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
    if (!this._canMutateMenu(this.outputMethodItem)) {
      return;
    }
    if (!this._clearMenuItems(this.outputMethodItem.menu)) {
      return;
    }
    let current = this._normalizeOutputMethod(this.insertMethod);
    for (let method of OUTPUT_METHODS) {
      let label = (current === method ? "[x] " : "[ ] ") + this._outputMethodLabel(method);
      let item = new PopupMenu.PopupMenuItem(label);
      this._connectSafe(item, "activate", () => this._selectOutputMethod(method));
      this.outputMethodItem.menu.addMenuItem(item);
    }
  },

  _populateArtifactEncryptionMenu: function() {
    if (!this._canMutateMenu(this.artifactEncryptionItem)) {
      return;
    }
    this.artifactEncryption = this._normalizeArtifactEncryption(this.artifactEncryption);
    this._setMenuItemLabelSafely(
      this.artifactEncryptionItem,
      _("Encryption: ") + this._artifactEncryptionLabel(this.artifactEncryption)
    );
    if (!this._clearMenuItems(this.artifactEncryptionItem.menu)) {
      return;
    }
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
    if (!this._canMutateMenu(this.textOptionsItem)) {
      return;
    }
    if (!this._clearMenuItems(this.textOptionsItem.menu)) {
      return;
    }
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
    this._spawnText(promptArgs, (output, result) => {
      if (this.autoPastePromptToken !== promptToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this.autoPastePromptToken = null;
      if (result && result.startupFailed === true) {
        this._setStatusPreservingRecording("error", _("Could not open Auto-Submit prompt"), this.lastTranscript);
        return;
      }
      if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
        return;
      }
      this._setAutoPasteTitles(this._autoPasteTitleValues(output));
    }, { timeoutMs: 0, resourceGroup: "settings-prompt" });
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
    if (!this._canMutateMenu(this.autoPasteItem)) {
      return;
    }
    if (!this._clearMenuItems(this.autoPasteItem.menu)) {
      return;
    }
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

  _updateAutoPasteItem: function(labelText) {
    let nextText = typeof labelText === "string" ? labelText : this._autoPasteLabel();
    return this._setMenuItemLabelSafely(this.autoPasteItem, nextText);
  },

  _windowTitleMatchesAutoPaste: function() {
    let markers = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    if (markers.length === 0) {
      return false;
    }
    let targetWindowUsable = this._isUsableTargetWindow(this.targetWindow);
    if (!targetWindowUsable && this._isTargetWindowXLookupPending()) {
      return false;
    }
    if (!targetWindowUsable && !this.targetWindowXTitle && !this.targetWindowXClass) {
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
    this._setMenuItemLabelSafely(
      this.openAiFlexProcessingItem,
      this._optionLabel(
        Boolean(this.openaiCompatibleFlexProcessing),
        _("OpenAI Flex processing")
      )
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
    let modelMenuCleanupSucceeded = this._terminateProcessesByGroup("model-menu-refresh") !== false;
    let hadVoiceModelAction = Boolean(this.voiceModelActionToken);
    let hadVoiceModelCleanupFailure = this.voiceModelCleanupFailed === true;
    this.voiceModelActionToken = null;
    let voiceModelCleanupSucceeded = this._terminateProcessesByGroup("voice-model") !== false;
    if (voiceModelCleanupSucceeded) {
      let busyStateReleased = this._releaseBusyStateAfterProcessCleanup(
        "voice-model",
        "voiceModelCleanupFailed",
        hadVoiceModelAction || hadVoiceModelCleanupFailure
      );
      if ((hadVoiceModelAction || hadVoiceModelCleanupFailure) && busyStateReleased &&
          !this._recordingCommandToken && !this.isCommandRunning && this.status === "processing") {
        this._setStatus("ready", _("Voice model operation cancelled by settings change"), this.lastTranscript);
      }
    } else {
      this.voiceModelCleanupFailed = true;
    }
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this._populateLanguageMenu();
    this._populateModelMenu([], _("Open menu to load voice models"));
    this._updatePanel();
    if (!voiceModelCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Voice model operation could not be stopped"), this.lastTranscript);
    }
    if (!modelMenuCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Voice model list refresh could not be stopped"), this.lastTranscript);
    }
  },

  _hasActiveRecordingState: function() {
    return this.status === "recording" || this.status === "recorded" || this.status === "processing" ||
      (this.status === "error" && this.recordingArtifactsPresent);
  },

  _hasPendingProcessCleanup: function() {
    for (let registryName of [
      "_orphanedProcesses",
      "_orphanedCancellables",
      "_orphanedTimers",
      "_orphanedSignals",
      "_orphanedMonitors",
      "_orphanedHotkeys",
    ]) {
      if (!Array.isArray(this[registryName]) || this[registryName].length > 0) {
        return true;
      }
    }
    return false;
  },

  _hasPendingDialogCleanup: function() {
    return !Array.isArray(this._orphanedDialogs) || this._orphanedDialogs.length > 0;
  },

  _hasPendingTextInsertCleanup: function() {
    if (!this.textInsertCancellationFailed) {
      return false;
    }
    return String(this.autoInsertPendingFingerprint || "") !== "" || this._hasPendingTextInsertResources();
  },

  _hasPendingTextInsertResources: function() {
    if (this.clipboardOverwriteDialog || this.pasteTimer) {
      return true;
    }
    let timers = this._resourceRegistry && this._resourceRegistry.timers;
    if (timers && timers.paste) {
      return true;
    }
    if (!Array.isArray(this._orphanedTimers)) {
      return true;
    }
    if (this._orphanedTimers.some((entry) => entry &&
        (entry.name === "paste" || entry.propertyName === "pasteTimer"))) {
      return true;
    }
    return ["keyboard", "clipboard", "x11"].some((group) => this._hasTrackedProcessGroup(group));
  },

  _hasLocalProcessingWorkflow: function(includePendingCleanup) {
    let includePendingCleanupState = includePendingCleanup !== false;
    return Boolean(
      this.terminalWorkflowRunning ||
      this.terminalWorkflowToken ||
      this.textInsertToken ||
      this.maintenanceCleanupFailed ||
      this.voiceModelCleanupFailed ||
      this.benchmarkCleanupFailed ||
      this.ollamaModelCleanupFailed ||
      (includePendingCleanupState && this._hasPendingProcessCleanup()) ||
      (includePendingCleanupState && this._hasPendingDialogCleanup()) ||
      (includePendingCleanupState && this._hasPendingTextInsertCleanup()) ||
      this.clipboardOverwriteDialog ||
      this.voiceModelActionToken ||
      this.alarmCheckToken ||
      this.alarmActionToken ||
      this.alarmMenuRefreshToken ||
      this._cleanupCommandToken ||
      this.settingsTransferToken ||
      this.setupDiagnosticsToken ||
      this.cleanupPreviewDialogToken ||
      this.cleanupPreviewDialog ||
      this.customLimitPromptToken ||
      this.autoPastePromptToken ||
      this.transcriptListPromptToken ||
      this.transcriptListPromptDialog ||
      this._doctorCommandRunning ||
      this.doctorCommandToken ||
      this.benchmarkFlowToken ||
      this.transcriptWindowToken ||
      this.ollamaModelFlowToken ||
      this.ollamaModelInstallToken ||
      this.ollamaModelInstallRunning ||
      this.ollamaInstallWatchToken
    );
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
    if (this._hasActiveRecordingState() || this.isCommandRunning || this._recordingCommandToken) {
      this._setStatusPreservingRecording(
        this.status,
        _("Finish the current recording or operation before starting another language"),
        this.lastTranscript
      );
      return false;
    }
    if (!this._rememberFocusedWindow(Boolean(preserveTargetOnFailure))) {
      return false;
    }
    this.activeLanguage = this._normalizeLanguage(language, this._primaryLanguage());
    this.activeLanguageExplicit = true;
    let recordingStarted = this._toggleRecording("start") === true;
    return recordingStarted;
  },

  _populateLanguageMenu: function() {
    if (!this._canMutateMenu(this.languageItem)) {
      return;
    }
    if (!this._clearMenuItems(this.languageItem.menu)) {
      return;
    }
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
    if (!this._canMutateMenu(this.shortcutItem)) {
      return;
    }
    if (!this._clearMenuItems(this.shortcutItem.menu)) {
      return;
    }
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
    let forcedAction = arguments.length > 0 ? String(arguments[0] || "") : "";
    if (this.ollamaModelFlowToken || this.ollamaInstallWatchToken || this.ollamaModelInstallRunning || this.ollamaModelCleanupFailed) {
      if (!this._cancelOllamaFlowForRecording()) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        return false;
      }
    }
    if (this.terminalWorkflowRunning || this.terminalWorkflowToken) {
      this._setStatusPreservingRecording(
        this.status,
        _("Finish the terminal workflow before starting dictation"),
        this.lastTranscript
      );
      return false;
    }
    let hasExistingRecordingWork = this._hasActiveRecordingState();
    if (this.isCommandRunning && this._recordingCommandToken) {
      let activeRecordingCommandAction = String(this._recordingCommandToken.action || "");
      if (forcedAction === "start") {
        return false;
      }
      if (activeRecordingCommandAction === "start") {
        this.stopPendingWhileCommandRunning = true;
        this.autoRelistenManualStopRequested = true;
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenPendingLanguage = "";
        this._setStatus(
          "processing",
          this.autoRelisten ? _("Stopping Auto Relisten...") : _("Stopping recording..."),
          this.lastTranscript
        );
      }
      if (
        activeRecordingCommandAction === "stop" &&
        forcedAction !== "start" &&
        this.status === "processing" &&
        this.autoRelistenPending &&
        Boolean(this.autoTranscribeRecordingKey)
      ) {
        this.autoRelistenManualStopRequested = true;
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenPendingLanguage = "";
      }
      return true;
    }
    let backgroundCleanupSucceeded = this._invalidateBackgroundCallbacksForRecording();
    if (!backgroundCleanupSucceeded && !hasExistingRecordingWork) {
      return false;
    }
    let textInsertCleanupSucceeded = this._cancelTextInsertForSettingsChange();
    if (!textInsertCleanupSucceeded && !hasExistingRecordingWork) {
      return false;
    }
    if (this.isCommandRunning) {
      return false;
    }
    if (!hasExistingRecordingWork && !this._ensureVoiceModelCompatibleWithCurrentLanguage(true)) {
      return false;
    }
    let commandAction = forcedAction === "start" || forcedAction === "stop" ? forcedAction : (hasExistingRecordingWork ? "stop" : "start");
    let toggleArgs;
    try {
      toggleArgs = this._baseArgs(commandAction);
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Could not prepare recording command: ") + safeError, this.lastTranscript);
      return false;
    }
    let manualRelistenStopRequested = Boolean(
      this.autoRelisten &&
      this.notificationSessionActive &&
      (this.status === "recording" || this.status === "recorded" || this.autoRelistenPending)
    );
    this.notificationSessionActive = true;
    if (commandAction === "start") {
      this.lastNotificationKey = "";
    }
    this.autoTranscribeRecordingKey = "";
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenPendingLanguage = "";
    this.autoRelistenManualStopRequested = manualRelistenStopRequested;
    this.autoInsertFingerprint = "";
    this.autoInsertFingerprints = [];
    this.recordingStartedAtMs = commandAction === "start" ? Date.now() : 0;
    this.recordingMaxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this.cancelPendingWhileCommandRunning = false;
    this.stopPendingWhileCommandRunning = false;
    let recordingCommandToken = { action: commandAction };
    this._recordingCommandToken = recordingCommandToken;
    this.isCommandRunning = true;
    if (commandAction === "stop") {
      this._setStatus("processing", _("Stopping recording..."), this.lastTranscript);
    }
    let toggleHandle = this._spawnJson(toggleArgs, (payload) => {
      if (this._recordingCommandToken !== recordingCommandToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this._recordingCommandToken = null;
      this.isCommandRunning = false;
      let stopPending = this.stopPendingWhileCommandRunning && !this.cancelPendingWhileCommandRunning;
      let payloadStatus = String(payload && payload.status || "").trim().toLowerCase();
      let requestStopAfterStart = (
        stopPending &&
        commandAction === "start" &&
        (payloadStatus === "recording" || payloadStatus === "recorded")
      );
      this.stopPendingWhileCommandRunning = false;
      if (requestStopAfterStart) {
        this._toggleRecording("stop");
        return;
      }
      this._applyPayloadSafely(
        payload,
        undefined,
        true
      );
    });
    if (!toggleHandle) {
      if (this._recordingCommandToken === recordingCommandToken) {
        this._recordingCommandToken = null;
        this.isCommandRunning = false;
      }
      if (commandAction === "start") {
        this.recordingStartedAtMs = 0;
        if (!hasExistingRecordingWork) {
          this.recordingArtifactsPresent = false;
          this._setStatus("error", this.lastMessage, this.lastTranscript);
          return false;
        }
      }
      this._updatePanel();
      return false;
    }
    if (commandAction === "start") {
      this._setStatus("recording", _("Recording..."), "");
    }
    return true;
  },

  _restartApplet: function() {
    if (this._terminateProcessesByGroup("keyboard") === false) {
      this._setStatusPreservingRecording("error", _("Could not stop keyboard insertion before restarting applet"), this.lastTranscript);
      return;
    }
    this._setStatusPreservingRecording("processing", _("Restarting applet..."), this.lastTranscript);
    try {
      Extension.reloadExtension(UUID, Extension.Type.APPLET);
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("Could not restart applet"), this.lastTranscript);
    }
  },

  _refreshStatus: function(fromStatusTimer) {
    if (this._statusCommandRunning) {
      return this.status === "recording" || this.status === "processing";
    }
    let localStatusOwner = this.status === "processing" && this._hasLocalProcessingWorkflow();
    if (this.isCommandRunning || localStatusOwner) {
      return this.status === "recording" || this.status === "processing";
    }
    let statusCommandToken = {};
    this._statusCommandToken = statusCommandToken;
    this._statusCommandRunning = true;
    let statusRefreshToken = ++this._statusRefreshToken;
    try {
      this._spawnJson(this._statusArgs(), (payload) => {
        try {
          this._applyPayload(payload, statusRefreshToken);
        } catch (err) {
          if (this._statusCommandToken === statusCommandToken) {
            let safeError = this._sanitizeErrorMessage(err);
            this.microphoneLevel = null;
            this._setStatusPreservingRecording("error", _("Status refresh failed: ") + safeError, this.lastTranscript);
          }
        } finally {
          if (this._statusCommandToken !== statusCommandToken) {
            return;
          }
          this._statusCommandToken = null;
          this._statusCommandRunning = false;
          // A local command may have invalidated this response while its poll timer expired.
          // Always restore active polling after request completion.
          if (this.status === "recording" || this.status === "processing") {
            this._scheduleStatusPoll();
          }
        }
      }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS, resourceGroup: "status" });
    } catch (err) {
      if (this._statusCommandToken !== statusCommandToken) {
        return;
      }
      this._statusCommandToken = null;
      this._statusCommandRunning = false;
      let safeError = this._sanitizeErrorMessage(err);
      this.microphoneLevel = null;
      this._setStatusPreservingRecording("error", _("Status refresh failed: ") + safeError, this.lastTranscript);
      if (this.status === "recording" || this.status === "processing") {
        if (fromStatusTimer === true) {
          return true;
        }
        this._scheduleStatusPoll();
      }
    }
  },

  _hasCancelableRecordingWork: function(statusOverride) {
    let effectiveStatus = typeof statusOverride === "string" ? statusOverride : this.status;
    let localTextInsertAllowsCancel = Boolean(this.autoRelistenPending && this.textInsertToken);
    let localTextInsertOwnsRecording = Boolean(this.textInsertToken && !localTextInsertAllowsCancel);
    let localWorkflowOwnsProcessing = effectiveStatus === "processing" &&
      this._hasLocalProcessingWorkflow() &&
      !localTextInsertAllowsCancel &&
      !this._recordingCommandToken;
    return ((effectiveStatus === "recording" || effectiveStatus === "recorded") && !localTextInsertOwnsRecording) ||
      (effectiveStatus === "error" && this.recordingArtifactsPresent && !localTextInsertOwnsRecording) ||
      (effectiveStatus === "processing" && this.recordingArtifactsPresent && !localWorkflowOwnsProcessing) ||
      (this.autoRelistenPending && !localWorkflowOwnsProcessing) ||
      (this.isCommandRunning && this.notificationSessionActive && Boolean(this._recordingCommandToken));
  },

  _updateRecordingArtifactState: function(payload, status) {
    if (!payload || typeof payload !== "object") {
      return;
    }
    let hasPresenceFields = [
      "audio_path_present",
      "log_path_present",
      "transcript_path_present",
      "pid_present",
      "process_identity_present"
    ]
      .some((field) => Object.prototype.hasOwnProperty.call(payload, field));
    if (hasPresenceFields) {
      this.recordingArtifactsPresent = Boolean(
        payload.audio_path_present === true ||
        payload.log_path_present === true ||
        payload.transcript_path_present === true ||
        payload.pid_present === true ||
        payload.process_identity_present === true
      );
    }
    let cleanupFailurePresent = payload.audio_deleted === false ||
      payload.log_deleted === false ||
      payload.transcript_deleted === false;
    if (status === "idle" || status === "done") {
      this.recordingArtifactsPresent = cleanupFailurePresent;
      return;
    }
    if (
      payload.discarded_audio_path_present === true ||
      cleanupFailurePresent
    ) {
      this.recordingArtifactsPresent = true;
    }
    if (status === "recording" || status === "recorded" || status === "processing") {
      this.recordingArtifactsPresent = true;
    }
  },

  _cancelRecording: function(statusOverride) {
    if (!this._hasCancelableRecordingWork(statusOverride)) {
      return;
    }
    if (!this.isCommandRunning && this.autoRelistenPending && this.textInsertToken) {
      if (!this._cancelTextInsertForSettingsChange()) {
        return;
      }
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenPendingLanguage = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatus("ready", _("Auto Relisten cancelled"), this.lastTranscript);
      return;
    }
    if (this.isCommandRunning) {
      this.autoTranscribeRecordingKey = "";
      this.stopPendingWhileCommandRunning = false;
      this.cancelPendingWhileCommandRunning = true;
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenPendingLanguage = "";
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
    let recordingCommandToken = { action: "cancel" };
    this._recordingCommandToken = recordingCommandToken;
    this.isCommandRunning = true;
    this.autoTranscribeRecordingKey = "";
    this.cancelPendingWhileCommandRunning = false;
    this.stopPendingWhileCommandRunning = false;
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenPendingLanguage = "";
    this.autoRelistenManualStopRequested = true;
    this._setStatus("processing", _("Cancelling..."), this.lastTranscript);
    this._spawnJson(cancelArgs, (payload) => {
      if (this._recordingCommandToken !== recordingCommandToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this._recordingCommandToken = null;
      this.isCommandRunning = false;
      this._applyPayloadSafely(payload, undefined, true);
    });
  },

  _invalidateBackgroundCallbacksForRecording: function() {
    this._statusRefreshToken++;
    this._statusCommandToken = null;
    this._statusCommandRunning = false;
    let statusCleanupSucceeded = this._terminateProcessesByGroup("status") !== false;
    if (!statusCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Status refresh could not be stopped"), this.lastTranscript);
    }
    this.historyRefreshToken = null;
    this.historyRefreshQueued = false;
    let historyRefreshCleanupSucceeded = this._terminateProcessesByGroup("history-refresh") !== false;
    if (!historyRefreshCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("History refresh could not be stopped"), this.lastTranscript);
    }
    this.inputSourceMenuRefreshToken = null;
    let inputSourceRefreshCleanupSucceeded = this._terminateProcessesByGroup("input-source-refresh") !== false;
    if (!inputSourceRefreshCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Input source refresh could not be stopped"), this.lastTranscript);
    }
    this.modelMenuRefreshToken = null;
    let modelMenuRefreshCleanupSucceeded = this._terminateProcessesByGroup("model-menu-refresh") !== false;
    if (!modelMenuRefreshCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Voice model list refresh could not be stopped"), this.lastTranscript);
    }
    let hadVoiceModelAction = Boolean(this.voiceModelActionToken);
    let hadVoiceModelCleanupFailure = this.voiceModelCleanupFailed === true;
    this.voiceModelActionToken = null;
    let voiceModelCleanupSucceeded = this._terminateProcessesByGroup("voice-model") !== false;
    if (!voiceModelCleanupSucceeded) {
      this.voiceModelCleanupFailed = true;
      this._setStatusPreservingRecording("error", _("Voice model operation could not be stopped"), this.lastTranscript);
    } else if ((hadVoiceModelAction || hadVoiceModelCleanupFailure) && !this._recordingCommandToken) {
      this._releaseBusyStateAfterProcessCleanup(
        "voice-model",
        "voiceModelCleanupFailed",
        true
      );
    }
    this.textModelMenuRefreshToken = null;
    let textModelRefreshCleanupSucceeded = this._terminateProcessesByGroup("text-model-refresh") !== false;
    if (!textModelRefreshCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Text model list refresh could not be stopped"), this.lastTranscript);
    }
    this.alarmMenuRefreshToken = null;
    this.alarmMenuRefreshQueued = false;
    let alarmMenuRefreshCleanupSucceeded = this._terminateProcessesByGroup("alarm-menu-refresh") !== false;
    if (!alarmMenuRefreshCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Alarm menu refresh could not be stopped"), this.lastTranscript);
    }
    this.alarmActionToken = null;
    let alarmActionCleanupSucceeded = this._terminateProcessesByGroup("alarm-action") !== false;
    if (!alarmActionCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Alarm action could not be stopped"), this.lastTranscript);
    }
    this.alarmCheckToken = null;
    let alarmCheckCleanupSucceeded = this._terminateProcessesByGroup("alarm-check") !== false;
    if (!alarmCheckCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Alarm check could not be stopped"), this.lastTranscript);
    }
    let hadBenchmarkFlow = Boolean(this.benchmarkFlowToken);
    let hadBenchmarkCleanupFailure = this.benchmarkCleanupFailed === true;
    this.benchmarkFlowToken = null;
    let benchmarkCleanupSucceeded = this._terminateProcessesByGroup("benchmark") !== false;
    if (!benchmarkCleanupSucceeded) {
      this.benchmarkCleanupFailed = true;
      this._setStatusPreservingRecording("error", _("Benchmark could not be stopped"), this.lastTranscript);
    } else if ((hadBenchmarkFlow || hadBenchmarkCleanupFailure) && !this._recordingCommandToken) {
      this._releaseBusyStateAfterProcessCleanup(
        "benchmark",
        "benchmarkCleanupFailed",
        true
      );
    }
    this.settingsTransferToken = null;
    let settingsTransferCleanupSucceeded = this._terminateProcessesByGroup("settings-transfer") !== false;
    if (!settingsTransferCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Settings transfer could not be stopped"), this.lastTranscript);
    }
    this.setupDiagnosticsToken = null;
    let setupDiagnosticsCleanupSucceeded = this._terminateProcessesByGroup("setup-diagnostics") !== false;
    if (!setupDiagnosticsCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Setup diagnostics action could not be stopped"), this.lastTranscript);
    }
    this.doctorCommandToken = null;
    this._doctorCommandRunning = false;
    let doctorCleanupSucceeded = this._terminateProcessesByGroup("doctor") !== false;
    if (!doctorCleanupSucceeded) {
      this._doctorCommandRunning = true;
      this._setStatusPreservingRecording("error", _("Doctor could not be stopped"), this.lastTranscript);
    }
    this.customLimitPromptToken = null;
    this.autoPastePromptToken = null;
    let cleanupPreviewCleanupSucceeded = true;
    if (this.cleanupPreviewDialogToken || this.cleanupPreviewDialog) {
      if (!this.cleanupPreviewDialog) {
        cleanupPreviewCleanupSucceeded = false;
        this._recordLifecycleError("dialog-state", new Error("Cleanup preview dialog is unavailable"));
        this._setStatusPreservingRecording("error", _("Cleanup preview could not be stopped"), this.lastTranscript);
        this.cleanupPreviewDialogToken = null;
      } else {
        cleanupPreviewCleanupSucceeded = this._dialogClose(this.cleanupPreviewDialog, "cleanup-preview");
        if (cleanupPreviewCleanupSucceeded) {
          this.cleanupPreviewDialog = null;
          this.cleanupPreviewDialogToken = null;
        } else {
          this._setStatusPreservingRecording("error", _("Cleanup preview could not be stopped"), this.lastTranscript);
        }
      }
    } else {
      this.cleanupPreviewDialogToken = null;
    }
    let transcriptPromptCleanupSucceeded = true;
    if (this.transcriptListPromptToken || this.transcriptListPromptDialog) {
      if (!this.transcriptListPromptDialog) {
        transcriptPromptCleanupSucceeded = false;
        this._recordLifecycleError("dialog-state", new Error("Transcript list prompt dialog is unavailable"));
        this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be stopped"), this.lastTranscript);
      } else {
        transcriptPromptCleanupSucceeded = this._dialogClose(this.transcriptListPromptDialog, "transcript-list");
        if (transcriptPromptCleanupSucceeded) {
          this.transcriptListPromptDialog = null;
          this.transcriptListPromptToken = null;
        } else {
          this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be stopped"), this.lastTranscript);
        }
      }
    } else {
      this.transcriptListPromptToken = null;
    }
    let orphanedDialogCleanupSucceeded = this._retryOrphanedDialogs();
    if (!orphanedDialogCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Previous dialog could not be stopped"), this.lastTranscript);
    }
    let hadCleanupCommand = Boolean(this._cleanupCommandToken);
    this._cleanupCommandToken = null;
    this.transcriptWindowToken = null;
    let maintenanceCleanupSucceeded = this._terminateProcessesByGroup("maintenance") !== false;
    if (!maintenanceCleanupSucceeded) {
      this.maintenanceCleanupFailed = true;
      this._setStatusPreservingRecording("error", _("Maintenance operation could not be stopped"), this.lastTranscript);
    } else if (hadCleanupCommand || this.maintenanceCleanupFailed) {
      this._releaseBusyStateAfterProcessCleanup(
        "maintenance",
        "maintenanceCleanupFailed",
        true
      );
    }
    let textInsertProcessCleanupSucceeded = true;
    for (let group of ["keyboard", "clipboard", "x11"]) {
      if (this._terminateProcessesByGroup(group) === false) {
        textInsertProcessCleanupSucceeded = false;
      }
    }
    if (!textInsertProcessCleanupSucceeded) {
      this.textInsertCancellationFailed = true;
      this._setStatusPreservingRecording("error", _("Previous text insertion could not be stopped"), this.lastTranscript);
    }
    let settingsPromptCleanupSucceeded = this._terminateProcessesByGroup("settings-prompt") !== false;
    if (!settingsPromptCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Settings prompt could not be stopped"), this.lastTranscript);
    }
    let ollamaWatchTimerCleanupSucceeded = this._clearOllamaInstallWatchTimer() !== false;
    let ollamaCleanupSucceeded = this._terminateProcessesByGroup("ollama") !== false;
    if (!ollamaCleanupSucceeded) {
      this.ollamaModelCleanupFailed = true;
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
    } else {
      this.ollamaModelFlowToken = null;
      this.ollamaInstallWatchToken = null;
      this.ollamaModelInstallToken = null;
      this.ollamaModelInstallRunning = false;
      this.ollamaModelCleanupFailed = false;
    }
    if (!ollamaWatchTimerCleanupSucceeded) {
      this.ollamaModelCleanupFailed = true;
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
    }
    return statusCleanupSucceeded && historyRefreshCleanupSucceeded && inputSourceRefreshCleanupSucceeded && modelMenuRefreshCleanupSucceeded && voiceModelCleanupSucceeded && textModelRefreshCleanupSucceeded && alarmMenuRefreshCleanupSucceeded && alarmActionCleanupSucceeded && alarmCheckCleanupSucceeded && benchmarkCleanupSucceeded && settingsTransferCleanupSucceeded && setupDiagnosticsCleanupSucceeded && doctorCleanupSucceeded && textInsertProcessCleanupSucceeded && settingsPromptCleanupSucceeded && ollamaWatchTimerCleanupSucceeded && ollamaCleanupSucceeded && maintenanceCleanupSucceeded && cleanupPreviewCleanupSucceeded && transcriptPromptCleanupSucceeded && orphanedDialogCleanupSucceeded;
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
    if (this.isCommandRunning || this._statusCommandRunning || this._hasLocalProcessingWorkflow() ||
        this.alarmCheckToken || this.alarmActionToken || this.alarmMenuRefreshToken) {
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
      timeoutMs: DOCTOR_COMMAND_TIMEOUT_MS,
      resourceGroup: "doctor"
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
      this._setMenuItemLabelSafely(this.doctorSummaryItem, this.doctorSummaryText || _("Doctor: not checked"));
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
    if (this.settingsWindowToken) {
      return;
    }
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

  _openUri: function(uri, successMessage, preserveRecording) {
    let setStatus = preserveRecording === false
      ? this._setStatus.bind(this)
      : this._setStatusPreservingRecording.bind(this);
    try {
      let opened = Gio.AppInfo.launch_default_for_uri(uri, null);
      if (opened === false) {
        throw new Error("URI could not be opened");
      }
      setStatus("ready", successMessage, this.lastTranscript);
    } catch (err) {
      this._safeLogError(err);
      setStatus("error", _("Could not open link"), this.lastTranscript);
    }
  },

  _openFolder: function(path, successMessage) {
    try {
      let mkdirResult = GLib.mkdir_with_parents(path, 0o755);
      if (mkdirResult !== 0) {
        throw new Error("folder could not be created");
      }
      let folder = Gio.File.new_for_path(path);
      let info = folder.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
      if (!info || info.get_file_type() !== Gio.FileType.DIRECTORY) {
        throw new Error("path is not a regular directory");
      }
      this._openUri(GLib.filename_to_uri(path, null), successMessage);
    } catch (err) {
      this._safeLogError(err);
      this._setStatusPreservingRecording("error", _("Could not open folder"), this.lastTranscript);
    }
  },

  _openFile: function(path, successMessage, preserveRecording) {
    let setStatus = preserveRecording === false
      ? this._setStatus.bind(this)
      : this._setStatusPreservingRecording.bind(this);
    try {
      let file = Gio.File.new_for_path(path);
      let info = file.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
      if (!info || info.get_file_type() !== Gio.FileType.REGULAR) {
        throw new Error("path is not a regular file");
      }
      this._openUri(GLib.filename_to_uri(path, null), successMessage, preserveRecording);
    } catch (err) {
      this._safeLogError(err);
      setStatus("error", _("Could not open file"), this.lastTranscript);
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
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    inputOption.resourceGroup = "setup-diagnostics";
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
      try {
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
        this._openFile(path, _("Opened profanity replacement list: ") + String(this._safePayloadCount(payload.entries)), false);
      } catch (error) {
        this._failSetupDiagnosticsAction(actionToken, error, _("Could not open profanity replacement list"));
      }
    }, inputOption);
  },

  _copySetupPlan: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    inputOption.resourceGroup = "setup-diagnostics";
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
      try {
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
      } catch (error) {
        this._failSetupDiagnosticsAction(actionToken, error, _("Could not copy setup plan"));
      }
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
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false);
    if (!inputOption) {
      return;
    }
    inputOption.resourceGroup = "setup-diagnostics";
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
      try {
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
      } catch (error) {
        this._failSetupDiagnosticsAction(actionToken, error, _("Could not copy setup commands"));
      }
    }, inputOption);
  },

  _copyDiagnostics: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(true);
    if (!inputOption) {
      return;
    }
    inputOption.resourceGroup = "setup-diagnostics";
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
      try {
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
      } catch (error) {
        this._failSetupDiagnosticsAction(actionToken, error, _("Could not copy diagnostics"));
      }
    }, inputOption);
  },

  _saveDiagnostics: function() {
    if (this.setupDiagnosticsToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(true);
    if (!inputOption) {
      return;
    }
    inputOption.resourceGroup = "setup-diagnostics";
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
      try {
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
      } catch (error) {
        this._failSetupDiagnosticsAction(actionToken, error, _("Could not save diagnostics"));
      }
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
      return ["gnome-terminal", "--wait", "--title=" + terminalTitle, "--", "bash", "-lc", command];
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
    let ollamaFlowContinues = cancelOllamaFlow === true &&
      Boolean(ollamaFlowToken) &&
      this.ollamaModelFlowToken === ollamaFlowToken;
    if (this._hasActiveRecordingState() && !ollamaFlowContinues) {
      this._setStatus(this.status, _("Finish the current recording before starting a terminal workflow"), this.lastTranscript);
      return false;
    }
    if (!ollamaFlowContinues && this._hasLocalProcessingWorkflow()) {
      this._setStatus(this.status, _("Another command is already running"), this.lastTranscript);
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
        resourceGroup: cancelOllamaFlow === true ? "ollama" : "terminal",
      }, (stdout, stderr, result) => {
        if (this.terminalWorkflowToken !== terminalWorkflowToken) {
          if (!this.terminalWorkflowToken) {
            this.terminalWorkflowRunning = false;
          }
          return;
        }
        this.terminalWorkflowRunning = false;
        this.terminalWorkflowToken = null;
        if (cancelOllamaFlow === true && (!ollamaFlowToken || this.ollamaModelFlowToken !== ollamaFlowToken)) {
          return;
        }
        if (result && (result.error || result.timedOut || result.outputTooLarge)) {
          let ollamaCleanupFailed = false;
          if (cancelOllamaFlow === true && ollamaFlowToken && this.ollamaModelFlowToken === ollamaFlowToken) {
            let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
            let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
            ollamaCleanupFailed = !ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded;
          }
          this._setStatus("error", _("Terminal process exited unexpectedly"), this.lastTranscript);
          if (ollamaCleanupFailed) {
            this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
          }
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
      "trap 'rc=$?; printf \"\\nFinished with exit code %s.\\n\" \"$rc\"; read -r -p \"Press Enter to close...\" || true; exit \"$rc\"' EXIT"
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
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        return false;
      }
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
        let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
        let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
        if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
          this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        }
      }
      return false;
    }
    if (opened && continueOllamaFlow) {
      if (!this._watchOllamaInstallThenChoose()) {
        return false;
      }
    } else if (continueOllamaFlow) {
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      }
    }
    return opened;
  },

  _uninstallOllamaRuntime: function() {
    let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
    let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
    if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return;
    }
    try {
      this._runTerminalWorkflow(_("Uninstall Ollama"), this._uninstallOllamaRuntimeCommand(), _("Ollama uninstall terminal opened"));
    } catch (err) {
      this._safeLogError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatusPreservingRecording("error", _("Could not start uninstall terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start uninstall terminal"), safeError, true);
    }
  },

  _runBasicSetup: function() {
    let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
    let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
    if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return;
    }
    try {
      this._runTerminalWorkflow(_("Speed of Cinnamon basic setup"), this._basicSetupCommand(), _("Basic setup terminal opened"));
    } catch (err) {
      this._safeLogError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatusPreservingRecording("error", _("Could not start setup terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start setup terminal"), safeError, true);
    }
  },

  _selectBenchmarkAudioFile: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this.benchmarkFlowToken || this._hasLocalProcessingWorkflow()) {
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
    this._spawnText(audioDialogArgs, (output, result) => {
      if (this.benchmarkFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (result && result.startupFailed === true) {
        this.benchmarkFlowToken = null;
        this._setStatus("error", _("Could not open benchmark audio selection"), this.lastTranscript);
        return;
      }
      if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
        this.benchmarkFlowToken = null;
        this._setStatus(
          result.cancelled ? "ready" : "error",
          result.cancelled ? _("Benchmark cancelled") : _("Could not complete benchmark audio selection"),
          this.lastTranscript
        );
        return;
      }
      let audioPath = String(output || "").trim();
      if (audioPath === "") {
        this.benchmarkFlowToken = null;
        this._setStatus("ready", _("Benchmark cancelled"), this.lastTranscript);
        return;
      }
      this._benchmarkDownloadedModels(audioPath, flowToken);
    }, { timeoutMs: 0, resourceGroup: "benchmark" });
  },

  _benchmarkDownloadedModels: function(audioPath, flowToken) {
    flowToken = flowToken || this.benchmarkFlowToken;
    if (!flowToken || this.benchmarkFlowToken !== flowToken) {
      return;
    }
    if (this.isCommandRunning || (this._hasActiveRecordingState() && this.status !== "processing")) {
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
      try {
        if (this.benchmarkFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
          this._releaseBusyStateAfterProcessCleanup("benchmark", "benchmarkCleanupFailed");
          return;
        }
        this.isCommandRunning = false;
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
      } catch (error) {
        if (this.benchmarkFlowToken === flowToken) {
          this.benchmarkFlowToken = null;
        }
        this._recordLifecycleError("benchmark-flow", error);
        this._setStatus("error", _("Could not complete benchmark"), this.lastTranscript);
      }
    }, { timeoutMs: BENCHMARK_COMMAND_TIMEOUT_MS, resourceGroup: "benchmark" });
  },

  _setAlarmOptionStatus: function(message) {
    this._setStatusPreservingRecording("ready", message, this.lastTranscript);
  },

  _setAlarmErrorStatus: function(message) {
    this._setStatusPreservingRecording("error", message, this.lastTranscript);
  },

  _refreshAlarmMenu: function() {
    if (!this._canMutateMenu(this.alarmItem) || this.alarmItem.menu.isOpen !== true) {
      return;
    }
    let canReportAlarmStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    if (this.alarmMenuRefreshToken) {
      this.alarmMenuRefreshQueued = true;
      return;
    }
    this.alarmMenuRefreshQueued = false;
    if (this.alarmActionToken || this.alarmCheckToken) {
      return;
    }
    if (this._terminateProcessesByGroup("alarm-menu-refresh") === false) {
      this._populateAlarmMenu([], "", _("Alarm menu refresh could not be stopped"));
      if (canReportAlarmStatus()) {
        this._setAlarmErrorStatus(_("Alarm menu refresh could not be stopped"));
      }
      return;
    }
    let refreshToken = {};
    this.alarmMenuRefreshToken = refreshToken;
    let alarmListArgs;
    try {
      if (this._alarmMenuFingerprint === null) {
        this._populateAlarmMenu([], "", _("Loading alarms..."));
      }
      alarmListArgs = this._alarmListArgs();
    } catch (error) {
      if (this.alarmMenuRefreshToken === refreshToken) {
        this.alarmMenuRefreshToken = null;
      }
      this.alarmMenuRefreshQueued = false;
      this._recordLifecycleError("alarm-refresh", error);
      this._populateAlarmMenu([], "", _("Could not prepare alarm list"));
      if (canReportAlarmStatus()) {
        this._setAlarmErrorStatus(_("Could not prepare alarm list"));
      }
      return;
    }
    this._spawnJson(alarmListArgs, (payload) => {
      if (this.alarmMenuRefreshToken !== refreshToken) {
        return;
      }
      let refreshQueued = this.alarmMenuRefreshQueued === true;
      this.alarmMenuRefreshQueued = false;
      try {
        this.alarmMenuRefreshToken = null;
        if (refreshQueued) {
          return;
        }
        if (!this._canMutateMenu(this.alarmItem) || this.alarmItem.menu.isOpen !== true) {
          return;
        }
        if (payload.error) {
          let safeError = this._sanitizeErrorMessage(payload.error);
          this._populateAlarmMenu([], "", safeError);
          if (canReportAlarmStatus()) {
            this._setAlarmErrorStatus(safeError);
          }
          return;
        }
        this._populateAlarmMenu(payload.alarms || [], payload.summary || "");
      } catch (error) {
        if (this.alarmMenuRefreshToken === refreshToken) {
          this.alarmMenuRefreshToken = null;
        }
        this._recordLifecycleError("menu-refresh", error);
        if (canReportAlarmStatus()) {
          this._setAlarmErrorStatus(_("Could not refresh alarm list"));
        }
      } finally {
        if (refreshQueued && !this.alarmMenuRefreshToken &&
            !this.alarmActionToken && !this.alarmCheckToken &&
            this._canMutateMenu(this.alarmItem) && this.alarmItem.menu.isOpen === true) {
          this._refreshAlarmMenu();
        }
      }
    }, { resourceGroup: "alarm-menu-refresh", invalidatesStatus: false });
  },

  _populateAlarmMenu: function(alarms, summary, message) {
    if (!this._canMutateMenu(this.alarmItem)) {
      return;
    }
    alarms = Array.isArray(alarms) ? alarms : [];
    alarms = alarms.filter((alarm) => alarm && typeof alarm === "object" && typeof alarm.id === "string" && alarm.id.trim() !== "");
    let alarmsWereTruncated = alarms.length > MAX_ALARM_MENU_ENTRIES;
    if (alarmsWereTruncated) {
      alarms = alarms.slice(0, MAX_ALARM_MENU_ENTRIES);
    }
    let messageText = typeof message === "string" ? message.trim() : "";
    let summaryText = typeof summary === "string" ? summary.trim() : "";
    let nextFingerprint = JSON.stringify({
      message: messageText,
      summary: summaryText,
      truncated: alarmsWereTruncated,
      alarms: alarms,
    });
    if (this._alarmMenuFingerprint === nextFingerprint) {
      return;
    }
    if (!this._clearMenuItems(this.alarmItem.menu)) {
      return;
    }

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
      this._alarmMenuFingerprint = nextFingerprint;
      return;
    }
    if (!alarms || alarms.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No alarms configured"));
      empty.setSensitive(false);
      this.alarmItem.menu.addMenuItem(empty);
      this._alarmMenuFingerprint = nextFingerprint;
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
    this._alarmMenuFingerprint = nextFingerprint;
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
    if (this.isCommandRunning || this._hasLocalProcessingWorkflow()) {
      return;
    }
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
    if (this.alarmActionToken || this.alarmCheckToken || this.alarmMenuRefreshToken || this.isCommandRunning ||
        this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
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
    let canUpdateAlarmStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    this._spawnJson(alarmEnableArgs, (payload) => {
      try {
        if (this.alarmActionToken !== actionToken || !this._lifecycleAllowsWork()) {
          return;
        }
        if (payload.error) {
          this.alarmActionToken = null;
          if (canUpdateAlarmStatus()) {
            this._setAlarmErrorStatus(this._sanitizeErrorMessage(payload.error));
          }
          return;
        }
        this.alarmActionToken = null;
        if (!canUpdateAlarmStatus()) {
          return;
        }
        let alarmFound = Boolean(
          payload.alarm &&
          typeof payload.alarm === "object" &&
          !Array.isArray(payload.alarm)
        );
        this._setAlarmOptionStatus(
          alarmFound
            ? (enabled ? _("Alarm enabled") : _("Alarm disabled"))
            : _("Alarm not found")
        );
        this._refreshAlarmMenu();
      } catch (error) {
        if (this.alarmActionToken === actionToken) {
          this.alarmActionToken = null;
        }
        this._recordLifecycleError("alarm-action", error);
        if (canUpdateAlarmStatus()) {
          this._setAlarmErrorStatus(_("Could not complete alarm update"));
        }
      }
    }, { resourceGroup: "alarm-action" });
  },

  _removeAlarm: function(id) {
    if (this.alarmActionToken || this.alarmCheckToken || this.alarmMenuRefreshToken || this.isCommandRunning ||
        this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
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
    let canUpdateAlarmStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    this._spawnJson(alarmRemoveArgs, (payload) => {
      try {
        if (this.alarmActionToken !== actionToken || !this._lifecycleAllowsWork()) {
          return;
        }
        if (payload.error) {
          this.alarmActionToken = null;
          if (canUpdateAlarmStatus()) {
            this._setAlarmErrorStatus(this._sanitizeErrorMessage(payload.error));
          }
          return;
        }
        this.alarmActionToken = null;
        if (!canUpdateAlarmStatus()) {
          return;
        }
        this._setAlarmOptionStatus(payload.removed === true ? _("Alarm removed") : _("Alarm not found"));
        this._refreshAlarmMenu();
      } catch (error) {
        if (this.alarmActionToken === actionToken) {
          this.alarmActionToken = null;
        }
        this._recordLifecycleError("alarm-action", error);
        if (canUpdateAlarmStatus()) {
          this._setAlarmErrorStatus(_("Could not complete alarm removal"));
        }
      }
    }, { resourceGroup: "alarm-action" });
  },

  _checkAlarms: function(manual) {
    if (this.alarmCheckToken || this.alarmActionToken || this.alarmMenuRefreshToken || this._statusCommandRunning ||
        this._hasLocalProcessingWorkflow() ||
        (manual && (this._hasActiveRecordingState() || this.isCommandRunning || this._hasLocalProcessingWorkflow()))) {
      return;
    }
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
    let canUpdateManualStatus = () => manual && !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    this._spawnJson(alarmCheckArgs, (payload) => {
      try {
        if (this.alarmCheckToken !== checkToken || !this._lifecycleAllowsWork()) {
          return;
        }
        if (payload.error) {
          this.alarmCheckToken = null;
          if (canUpdateManualStatus()) {
            this._setAlarmErrorStatus(this._sanitizeErrorMessage(payload.error));
          }
          return;
        }
        this.alarmCheckToken = null;
        let localWorkflowBusy = this.isCommandRunning || this._hasLocalProcessingWorkflow();
        let manualStatusAllowed = canUpdateManualStatus();
        let due = Array.isArray(payload.due)
          ? payload.due.filter((alarm) => alarm && typeof alarm === "object")
          : [];
        let dueCount = due.length;
        let notifications = due.filter((alarm) => alarm.notify === true);
        let notificationsWereTruncated = notifications.length > MAX_ALARM_NOTIFICATIONS;
        if (notificationsWereTruncated) {
          notifications = notifications.slice(0, MAX_ALARM_NOTIFICATIONS);
        }
        for (let alarm of notifications) {
          let body = typeof alarm.body === "string" ? alarm.body.trim() : "";
          let label = typeof alarm.label === "string" ? alarm.label.trim() : "";
          this._notify(_("Speed of Cinnamon alarm"), body || label || _("Alarm due"), alarm.critical === true);
        }
        if (due.length > 0) {
          let first = due[0] || {};
          if (manualStatusAllowed || (!localWorkflowBusy &&
              (this.status === "idle" || this.status === "ready" || this.status === "done"))) {
            let firstLabel = typeof first.label === "string" ? first.label.trim() : "";
            let firstTime = typeof first.time === "string" ? first.time.trim() : "";
            let alarmStatusLabel = firstLabel || firstTime || String(dueCount);
            if (notificationsWereTruncated) {
              alarmStatusLabel += " (" + _("some notifications suppressed for safety") + ")";
            }
            this._setAlarmOptionStatus(_("Alarm: ") + alarmStatusLabel);
          }
        } else if (manualStatusAllowed) {
          this._setAlarmOptionStatus(_("No alarms due"));
        }
        if (manualStatusAllowed) {
          this._refreshAlarmMenu();
        }
      } catch (error) {
        if (this.alarmCheckToken === checkToken) {
          this.alarmCheckToken = null;
        }
        this._recordLifecycleError("alarm-check", error);
        if (canUpdateManualStatus()) {
          this._setAlarmErrorStatus(_("Could not complete alarm check"));
        }
      }
    }, { resourceGroup: "alarm-check" });
  },

  _refreshInputSourceMenu: function() {
    if (!this._canMutateMenu(this.inputSourceItem) || this.inputSourceItem.menu.isOpen !== true) {
      return true;
    }
    let canReportInputSourceStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    if (this.inputSourceMenuRefreshToken) {
      return true;
    }
    if (this._terminateProcessesByGroup("input-source-refresh") === false) {
      if (canReportInputSourceStatus()) {
        this._setStatusPreservingRecording("error", _("Input source refresh could not be stopped"), this.lastTranscript);
      }
      return false;
    }
    let refreshToken = {};
    this.inputSourceMenuRefreshToken = refreshToken;
    let inputSourceArgs;
    try {
      if (this._inputSourceMenuFingerprint === null) {
        this._populateInputSourceMenu([], _("Loading input sources..."));
      }
      inputSourceArgs = this._listInputsArgs();
    } catch (error) {
      if (this.inputSourceMenuRefreshToken === refreshToken) {
        this.inputSourceMenuRefreshToken = null;
      }
      this._recordLifecycleError("input-source-refresh", error);
      this._populateInputSourceMenu([], _("Could not prepare input source list"));
      if (canReportInputSourceStatus()) {
        this._setStatusPreservingRecording("error", _("Could not prepare input source list"), this.lastTranscript);
      }
      return false;
    }
    let refreshProcess = this._spawnJson(inputSourceArgs, (payload) => {
      if (this.inputSourceMenuRefreshToken !== refreshToken) {
        return;
      }
      try {
        this.inputSourceMenuRefreshToken = null;
        if (!this._canMutateMenu(this.inputSourceItem) || this.inputSourceItem.menu.isOpen !== true) {
          return;
        }
        if (payload.error) {
          this._populateInputSourceMenu([], this._sanitizeErrorMessage(payload.error));
          if (canReportInputSourceStatus()) {
            this._setStatusPreservingRecording("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
          }
          return;
        }
        this._populateInputSourceMenu(payload.sources || []);
      } catch (error) {
        if (this.inputSourceMenuRefreshToken === refreshToken) {
          this.inputSourceMenuRefreshToken = null;
        }
        this._recordLifecycleError("menu-refresh", error);
        if (canReportInputSourceStatus()) {
          this._setStatusPreservingRecording("error", _("Could not refresh input source list"), this.lastTranscript);
        }
      }
    }, { resourceGroup: "input-source-refresh", invalidatesStatus: false });
    if (!refreshProcess) {
      if (this.inputSourceMenuRefreshToken === refreshToken) {
        this.inputSourceMenuRefreshToken = null;
      }
      if (canReportInputSourceStatus()) {
        this._setStatusPreservingRecording("error", _("Could not start input source refresh"), this.lastTranscript);
      }
      return false;
    }
    return true;
  },

  _populateInputSourceMenu: function(sources, message) {
    if (!this._canMutateMenu(this.inputSourceItem)) {
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
    let current = String(this.inputDevice || "");
    let nextFingerprint = JSON.stringify({
      current: current,
      message: messageText,
      truncated: sourcesWereTruncated,
      sources: sources.map((source) => ({
        name: typeof source.name === "string" ? source.name : "",
        description: typeof source.description === "string" ? source.description : "",
        default: source.default === true,
      })),
    });
    if (this._inputSourceMenuFingerprint === nextFingerprint) {
      return;
    }
    if (!this._clearMenuItems(this.inputSourceItem.menu)) {
      return;
    }
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
      this._inputSourceMenuFingerprint = nextFingerprint;
      return;
    }
    if (!sources || sources.length === 0) {
      addCurrentCustomInput();
      this.inputSourceItem.menu.addMenuItem(this._selectionInfoItem(_("No input sources found")));
      this._inputSourceMenuFingerprint = nextFingerprint;
      return;
    }
    for (let source of sources) {
      if (!source || typeof source !== "object") {
        continue;
      }
      let sourceName;
      try {
        sourceName = this._coerceCliTextArg(source.name, "input device");
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
    this._inputSourceMenuFingerprint = nextFingerprint;
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
    if (this._refreshInputSourceMenu() === false) {
      return;
    }
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
    if (!this._canMutateMenu(this.modelItem) || this.modelItem.menu.isOpen !== true) {
      return;
    }
    let canReportModelStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    if (this.modelMenuRefreshToken || this.voiceModelActionToken || this.voiceModelCleanupFailed === true) {
      return;
    }
    if (this._terminateProcessesByGroup("model-menu-refresh") === false) {
      if (canReportModelStatus()) {
        this._setStatusPreservingRecording("error", _("Voice model list refresh could not be stopped"), this.lastTranscript);
      }
      return;
    }
    let refreshToken = {};
    this.modelMenuRefreshToken = refreshToken;
    let modelArgs;
    try {
      if (this._modelMenuFingerprint === null) {
        this._populateModelMenu([], _("Loading voice models..."));
      }
      modelArgs = this._modelsArgs();
    } catch (error) {
      if (this.modelMenuRefreshToken === refreshToken) {
        this.modelMenuRefreshToken = null;
      }
      this._recordLifecycleError("model-refresh", error);
      try {
        this._populateModelMenu([], _("Could not prepare voice model list"));
      } catch (populateError) {
        this._recordLifecycleError("menu-refresh", populateError);
      }
      if (canReportModelStatus()) {
        this._setStatusPreservingRecording("error", _("Could not prepare voice model list"), this.lastTranscript);
      }
      return;
    }
    this._spawnJson(modelArgs, (payload) => {
      if (this.modelMenuRefreshToken !== refreshToken) {
        return;
      }
      try {
        this.modelMenuRefreshToken = null;
        if (!this._canMutateMenu(this.modelItem) || this.modelItem.menu.isOpen !== true) {
          return;
        }
        if (payload.error) {
          let safeError = this._sanitizeErrorMessage(payload.error);
          this._populateModelMenu([], safeError);
          if (canReportModelStatus()) {
            this._setStatusPreservingRecording("error", safeError, this.lastTranscript);
          }
          return;
        }
        this._populateModelMenu(payload.models || []);
      } catch (error) {
        if (this.modelMenuRefreshToken === refreshToken) {
          this.modelMenuRefreshToken = null;
        }
        this._recordLifecycleError("menu-refresh", error);
        if (canReportModelStatus()) {
          this._setStatusPreservingRecording("error", _("Could not refresh voice model list"), this.lastTranscript);
        }
      }
    }, { resourceGroup: "model-menu-refresh", invalidatesStatus: false });
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
    let nextFingerprint = JSON.stringify({
      transcriber: String(this.transcriber || ""),
      whisperModel: String(this.whisperModel || ""),
      transcriberCommand: String(this.transcriberCommand || ""),
      language: this._voiceModelLanguage(),
      openaiCompatibleModel: String(this.openaiCompatibleModel || ""),
      openaiCompatibleUrl: String(this.openaiCompatibleUrl || ""),
      message: messageText,
      truncated: voiceModelsWereTruncated,
      models: models,
    });
    if (this._modelMenuFingerprint === nextFingerprint) {
      return;
    }
    if (!this._clearMenuItems(this.modelItem.menu)) {
      return;
    }

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
      this._modelMenuFingerprint = nextFingerprint;
      return;
    }
    if (!models || models.length === 0) {
      this.modelItem.menu.addMenuItem(this._selectionInfoItem(_("No models in catalog")));
      this._modelMenuFingerprint = nextFingerprint;
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
    this._modelMenuFingerprint = nextFingerprint;
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
    if (filename === "" || filename === "." || filename === ".." || filename.length > 255 || !/^[A-Za-z0-9._-]+$/.test(filename)) {
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

  _commitVoiceBackendSettings: function(transcriber, whisperModel, group, errorMessage, preserveRecording, result) {
    if (this.voiceModelCleanupFailed === true) {
      return false;
    }
    let previousTranscriber = this.transcriber;
    let previousWhisperModel = this.whisperModel;
    let settingsWrites = [
      ["transcriber", transcriber, previousTranscriber],
      ["whisper-model", whisperModel, previousWhisperModel],
    ];
    if (!this._commitSettingsBatch(settingsWrites, group, errorMessage, preserveRecording, result)) {
      this.transcriber = previousTranscriber;
      this.whisperModel = previousWhisperModel;
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
    if (this.isCommandRunning || this.voiceModelActionToken || this.modelMenuRefreshToken ||
        this.voiceModelCleanupFailed === true ||
        this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    if (name === "") {
      name = this._starterVoiceModelName();
    }
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
      if (this.voiceModelActionToken !== actionToken || !this._lifecycleAllowsWork()) {
        this._releaseBusyStateAfterProcessCleanup("voice-model", "voiceModelCleanupFailed");
        return;
      }
      try {
        this.isCommandRunning = false;
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
        this._selectVoiceModel(payload, false);
        this._refreshModelMenu();
      } catch (error) {
        this.isCommandRunning = false;
        if (this.voiceModelActionToken === actionToken) {
          this.voiceModelActionToken = null;
        }
        this._recordLifecycleError("model-action", error);
        this._setStatus("error", _("Could not complete model download"), this.lastTranscript);
      }
    }, { resourceGroup: "voice-model" });
  },

  _removeVoiceModel: function(model) {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this.voiceModelActionToken ||
        this.modelMenuRefreshToken || this.voiceModelCleanupFailed === true ||
        this._hasLocalProcessingWorkflow()) {
      return;
    }
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    let path = this._modelPathFromPayload(model);
    if (name === "") {
      return;
    }
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
      if (this.voiceModelActionToken !== actionToken || !this._lifecycleAllowsWork()) {
        this._releaseBusyStateAfterProcessCleanup("voice-model", "voiceModelCleanupFailed");
        return;
      }
      try {
        this.isCommandRunning = false;
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
            _("Removed model, but voice settings could not be updated"),
            false
          )) {
            this._refreshModelMenu();
            return;
          }
        }
        this._setStatus("done", _("Removed model: ") + name, this.lastTranscript);
        this._refreshModelMenu();
      } catch (error) {
        this.isCommandRunning = false;
        if (this.voiceModelActionToken === actionToken) {
          this.voiceModelActionToken = null;
        }
        this._recordLifecycleError("model-action", error);
        this._setStatus("error", _("Could not complete model removal"), this.lastTranscript);
      }
    }, { resourceGroup: "voice-model" });
  },

  _selectVoiceModel: function(model, preserveRecording) {
    let setStatus = preserveRecording === false
      ? this._setStatus.bind(this)
      : this._setStatusPreservingRecording.bind(this);
    if (this.voiceModelActionToken || this.voiceModelCleanupFailed === true) {
      return false;
    }
    let path = this._modelPathFromPayload(model);
    let name = model && typeof model.name === "string" ? model.name.trim() : "";
    let backend = model && typeof model.backend === "string" ? model.backend.trim() : "";
    if (!this._isUsableVoiceModelPayload(model)) {
      return false;
    }
    if (!this._voiceModelSupportsCurrentLanguage(model)) {
      setStatus("error", _("English-only model cannot transcribe primary language: ") + this._voiceModelLanguage(), this.lastTranscript);
      return false;
    }
    if (!this._commitVoiceBackendSettings(
      backend,
      path,
      "voice-model-select",
      _("Voice model settings could not be saved"),
      preserveRecording
    )) {
      return false;
    }
    setStatus("ready", _("Voice model: ") + name, this.lastTranscript);
    return true;
  },

  _selectAutomaticVoiceBackend: function() {
    if (this.voiceModelActionToken || this.voiceModelCleanupFailed === true) {
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
    if (this.voiceModelActionToken || this.voiceModelCleanupFailed === true) {
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
      let bracketHost = closing >= 0 ? authority.slice(1, closing) : "";
      if (closing <= 1 || authority.indexOf("[", 1) >= 0 || authority.indexOf("]", closing + 1) >= 0 ||
          !/^[0-9A-Fa-f:.]+$/.test(bracketHost) || bracketHost.indexOf(":") < 0) {
        throw new Error(field + " has invalid host");
      }
      host = authority.slice(0, closing + 1);
      port = authority.slice(closing + 1);
    } else {
      if (authority.indexOf("[") >= 0 || authority.indexOf("]") >= 0) {
        throw new Error(field + " has invalid host");
      }
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
    if (port !== "") {
      if (!/^:[0-9]{1,5}$/.test(port)) {
        throw new Error(field + " has invalid port");
      }
      let numericPort = Number(port.slice(1));
      if (!isFinite(numericPort) || numericPort < 0 || numericPort > 65535) {
        throw new Error(field + " has invalid port");
      }
    }
    let normalizedHost = host.toLowerCase();
    let ipv4Loopback = /^127\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$/.exec(normalizedHost);
    let validIpv4Loopback = Boolean(ipv4Loopback);
    if (validIpv4Loopback) {
      for (let index = 1; index <= 3; index++) {
        if (Number(ipv4Loopback[index]) > 255) {
          validIpv4Loopback = false;
          break;
        }
      }
    }
    let localHost = normalizedHost === "localhost" || normalizedHost === "[::1]" || validIpv4Loopback;
    if (match[1].toLowerCase() === "http" && !localHost) {
      throw new Error(field + " must use https:// unless host is local loopback");
    }
    return normalized;
  },

  _validatedExternalApiUrlOrFallback: function(value, fieldName, fallback) {
    let safeFallback = typeof fallback === "string" ? fallback : DEFAULT_OPENAI_COMPATIBLE_URL;
    try {
      return this._validateExternalApiUrl(value, fieldName);
    } catch (error) {
      this._recordLifecycleError("settings-url", error);
      return safeFallback;
    }
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

  _assertExternalApiEnvDirectoryChainSafe: function(path) {
    let current = Gio.File.new_for_path(GLib.path_get_dirname(path));
    while (current) {
      let info = current.query_info("standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
      if (!info || info.get_file_type() === Gio.FileType.SYMBOLIC_LINK) {
        throw new Error("External API config directory must not use symlinks");
      }
      if (info.get_file_type() !== Gio.FileType.DIRECTORY) {
        throw new Error("External API config path ancestor is not a directory");
      }
      current = current.get_parent();
    }
  },

  _externalApiEnvEncodeValue: function(value) {
    let text = typeof value === "string" ? value : "";
    return '"' + text.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  },

  _externalApiEnvContent: function() {
    let config = this._validatedExternalApiConfig({
      url: this.openaiCompatibleUrl,
      model: this.openaiCompatibleModel,
      textModel: this.openaiCompatibleTextModel,
      apiKey: this.externalApiEnvApiKey || this.openaiCompatibleApiKey || "",
    });
    return [
      "OPENAI_COMPATIBLE_URL=" + this._externalApiEnvEncodeValue(config.url),
      "OPENAI_COMPATIBLE_STT_MODEL=" + this._externalApiEnvEncodeValue(config.model),
      "OPENAI_COMPATIBLE_TEXT_MODEL=" + this._externalApiEnvEncodeValue(config.textModel),
      "OPENAI_COMPATIBLE_API_KEY=" + this._externalApiEnvEncodeValue(config.apiKey),
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
    this._assertExternalApiEnvDirectoryChainSafe(path);
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
    this._assertExternalApiEnvDirectoryChainSafe(path);
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
    let settingsWrites = [];
    let nextUrl = this.openaiCompatibleUrl;
    let nextModel = this.openaiCompatibleModel;
    if (String(this.openaiCompatibleUrl || "").trim() === LEGACY_OPENAI_COMPATIBLE_URL) {
      nextUrl = DEFAULT_OPENAI_COMPATIBLE_URL;
      settingsWrites.push(["openai-compatible-url", nextUrl, this.openaiCompatibleUrl]);
    }
    if (String(this.openaiCompatibleModel || "").trim() === "") {
      nextModel = DEFAULT_OPENAI_COMPATIBLE_MODEL;
      settingsWrites.push(["openai-compatible-model", nextModel, this.openaiCompatibleModel]);
    }
    let defaultsCommitted = true;
    if (settingsWrites.length > 0) {
      defaultsCommitted = this._commitSettingsBatch(
        settingsWrites,
        "settings-external-api",
        _("External API defaults could not be saved")
      );
      if (defaultsCommitted) {
        this.openaiCompatibleUrl = nextUrl;
        this.openaiCompatibleModel = nextModel;
      }
    }
    let hasLegacyApiKey = String(this.openaiCompatibleApiKey || "").trim() !== "";
    if (GLib.file_test(this._externalApiEnvPath(), GLib.FileTest.EXISTS)) {
      let envPath = this._ensureExternalApiEnvFile();
      if (envPath && this._applyExternalApiEnvFile(false)) {
        this._clearPersistedOpenAiCompatibleApiKey();
      }
      return;
    }
    if (defaultsCommitted && (settingsWrites.length > 0 || hasLegacyApiKey)) {
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
    if (!this._commitSettingsBatch(
      [["openai-compatible-api-key", "", previousApiKey]],
      "settings-external-api-key",
      _("Persisted External API key could not be cleared")
    )) {
      this.openaiCompatibleApiKey = previousApiKey;
      return false;
    }
    this.openaiCompatibleApiKey = "";
    return true;
  },

  _ensureExternalApiEnvFile: function() {
    let path;
    try {
      path = this._externalApiEnvPath();
      let mkdirResult = GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
      if (mkdirResult !== 0) {
        throw new Error("External API config directory could not be created");
      }
      this._assertExternalApiEnvDirectoryChainSafe(path);
      let info = this._externalApiEnvFileInfo(path, true);
      if (!info) {
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
    let newline = text.indexOf("\r\n") >= 0 ? "\r\n" : "\n";
    let lines = text.split(/\r?\n/);
    let assignmentPattern = /^(\s*)(OPENAI_COMPATIBLE_URL|OPENAI_COMPATIBLE_STT_MODEL|OPENAI_COMPATIBLE_MODEL|OPENAI_COMPATIBLE_TEXT_MODEL)\s*=/;
    let assignments = [];
    for (let index = 0; index < lines.length; index++) {
      let match = assignmentPattern.exec(lines[index]);
      if (!match) {
        continue;
      }
      let parsedLine = this._parseExternalApiEnvText(lines[index]);
      assignments.push({
        index: index,
        key: match[2],
        indent: match[1],
        value: typeof parsedLine[match[2]] === "string" ? parsedLine[match[2]] : "",
      });
    }
    for (let assignment of assignments) {
      if (assignment.key === "OPENAI_COMPATIBLE_URL" && assignment.value === LEGACY_OPENAI_COMPATIBLE_URL) {
        lines[assignment.index] = assignment.indent + assignment.key + "=" + this._externalApiEnvEncodeValue(DEFAULT_OPENAI_COMPATIBLE_URL);
      }
    }
    let hasSttModel = assignments.some((assignment) => assignment.key === "OPENAI_COMPATIBLE_STT_MODEL");
    let legacyModelAssignments = assignments.filter((assignment) => assignment.key === "OPENAI_COMPATIBLE_MODEL");
    if (!hasSttModel && legacyModelAssignments.length > 0) {
      for (let assignment of legacyModelAssignments) {
        lines[assignment.index] = lines[assignment.index].replace(
          /^(\s*)OPENAI_COMPATIBLE_MODEL(\s*=)/,
          "$1OPENAI_COMPATIBLE_STT_MODEL$2"
        );
      }
      hasSttModel = true;
    }
    let appendAssignment = (key, value) => {
      let assignment = key + "=" + this._externalApiEnvEncodeValue(value);
      if (lines.length > 0 && lines[lines.length - 1] === "") {
        lines[lines.length - 1] = assignment;
      } else {
        lines.push(assignment);
      }
      lines.push("");
    };
    if (!hasSttModel) {
      let model = this._coerceCliTextArg(
        this._externalApiEnvValue(this.openaiCompatibleModel, DEFAULT_OPENAI_COMPATIBLE_MODEL),
        "openai-compatible model"
      ).trim();
      appendAssignment("OPENAI_COMPATIBLE_STT_MODEL", model);
    }
    if (!assignments.some((assignment) => assignment.key === "OPENAI_COMPATIBLE_TEXT_MODEL")) {
      let textModel = this._coerceCliTextArg(
        this._externalApiEnvValue(this.openaiCompatibleTextModel, DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL),
        "openai-compatible text model"
      ).trim();
      appendAssignment("OPENAI_COMPATIBLE_TEXT_MODEL", textModel);
    }
    let migrated = lines.join(newline);
    let legacyApiKey = this._coerceCliTextArg(
      this.openaiCompatibleApiKey || "",
      "openai-compatible API key"
    ).trim();
    let migratedValues = this._parseExternalApiEnvText(migrated);
    let migratedApiKey = typeof migratedValues.OPENAI_COMPATIBLE_API_KEY === "string"
      ? migratedValues.OPENAI_COMPATIBLE_API_KEY.trim()
      : "";
    if (legacyApiKey !== "" && migratedApiKey === "") {
      let migratedLines = migrated.split(newline);
      let assignment = "OPENAI_COMPATIBLE_API_KEY=" + this._externalApiEnvEncodeValue(legacyApiKey);
      if (migratedLines.length > 0 && migratedLines[migratedLines.length - 1] === "") {
        migratedLines[migratedLines.length - 1] = assignment;
      } else {
        migratedLines.push(assignment);
      }
      migratedLines.push("");
      migrated = migratedLines.join(newline);
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
      if (value.indexOf('"') === 0 && value.lastIndexOf('"') === value.length - 1) {
        value = value.slice(1, -1).replace(/\\(["\\])/g, "$1");
      } else if (value.indexOf("'") === 0 && value.lastIndexOf("'") === value.length - 1) {
        value = value.slice(1, -1);
      }
      values[key] = value;
    }
    return values;
  },

  _applyExternalApiEnvFile: function(showStatus, transaction, target) {
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
    if (target !== undefined && target !== null && target !== "") {
      if ((target !== "text" && target !== "voice") || !this._prepareExternalApiEnvTarget(target)) {
        return false;
      }
    }
    let previousConfig = {
      url: this.openaiCompatibleUrl,
      model: this.openaiCompatibleModel,
      textModel: this.openaiCompatibleTextModel,
      apiKey: this.externalApiEnvApiKey,
    };
    let previousPersistedApiKey = this.openaiCompatibleApiKey;
    let settingsWrites = [
      ["openai-compatible-url", config.url, previousConfig.url],
      ["openai-compatible-model", config.model, previousConfig.model],
      ["openai-compatible-text-model", config.textModel, previousConfig.textModel],
    ];
    if (String(previousPersistedApiKey || "") !== "") {
      settingsWrites.push(["openai-compatible-api-key", "", previousPersistedApiKey]);
    }
    let commitResult = {};
    if (!this._commitSettingsBatch(
      settingsWrites,
      "settings-external-api",
      _("External API settings could not be saved"),
      undefined,
      commitResult
    )) {
      if (transaction && typeof transaction === "object") {
        transaction.rollbackFailed = commitResult.rollbackSucceeded === false;
      }
      return false;
    }
    this.openaiCompatibleUrl = config.url;
    this.openaiCompatibleModel = config.model;
    this.openaiCompatibleTextModel = config.textModel;
    this.externalApiEnvApiKey = config.apiKey;
    this.openaiCompatibleApiKey = "";
    if (transaction && typeof transaction === "object") {
      transaction.rollback = () => {
        let rollbackSucceeded = this._rollbackSettingsBatch(settingsWrites);
        this.openaiCompatibleUrl = previousConfig.url;
        this.openaiCompatibleModel = previousConfig.model;
        this.openaiCompatibleTextModel = previousConfig.textModel;
        this.externalApiEnvApiKey = previousConfig.apiKey;
        this.openaiCompatibleApiKey = previousPersistedApiKey;
        return rollbackSucceeded;
      };
    }
    if (showStatus) {
      this._setStatusPreservingRecording("ready", _("External API config loaded: ") + (this.openaiCompatibleModel || _("not configured")), this.lastTranscript);
    }
    return true;
  },

  _clearExternalApiEnvMonitor: function() {
    if (!this.externalApiEnvMonitor) {
      return this._retryOrphanedMonitors(true);
    }
    let monitor = this.externalApiEnvMonitor;
    let clearReference = () => {
      this._clearExternalApiEnvMonitorReference(monitor);
    };
    let cancelSucceeded = this._externalApiEnvMonitorCancelSucceeded === true;
    if (!cancelSucceeded) {
      if (!this._disconnectTrackedSignalsForTarget(monitor)) {
        this._trackOrphanedMonitor(monitor, false);
        clearReference();
        return false;
      }
      try {
        let result = monitor.cancel();
        if (result === false) {
          throw new Error("External API monitor could not be cancelled");
        }
        this._externalApiEnvMonitorCancelSucceeded = true;
        cancelSucceeded = true;
      } catch (err) {
        this._recordLifecycleError("monitor-cancel", err);
        this._trackOrphanedMonitor(monitor, false);
        clearReference();
        return false;
      }
    }
    if (!cancelSucceeded || !this._untrackMonitor(monitor)) {
      this._trackOrphanedMonitor(monitor, true);
      clearReference();
      return false;
    }
    let orphanUntracked = this._untrackOrphanedMonitor(monitor);
    if (!orphanUntracked) {
      this._trackOrphanedMonitor(monitor, true);
      clearReference();
      return false;
    }
    if (!this._clearExternalApiEnvMonitorReference(monitor)) {
      this._trackOrphanedMonitor(monitor, true);
      return false;
    }
    return true;
  },

  _watchExternalApiEnvFile: function(path) {
    if (!this._clearExternalApiEnvMonitor()) {
      return;
    }
    if (!Array.isArray(this._orphanedMonitors)) {
      this._recordLifecycleError("monitor-state", new Error("Monitor orphan registry is unavailable"));
      return;
    }
    if (this._orphanedMonitors.length > 0) {
      this._recordLifecycleError("monitor-state", new Error("An orphaned monitor is still pending"));
      return;
    }
    let applyTarget = this.externalApiEnvApplyTarget || "voice";
    try {
      let file = Gio.File.new_for_path(path);
      let monitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
      this.externalApiEnvMonitor = monitor;
      this._externalApiEnvMonitorCancelSucceeded = false;
      this._trackMonitor(monitor);
      let connectionId = this._connectSafe(monitor, "changed", (changedMonitor, fileObj, otherFile, eventType) => {
        if (this.appletRemoved || !this._lifecycleAllowsWork() || this.externalApiEnvMonitor !== monitor || changedMonitor !== monitor) {
          return;
        }
        if (eventType === Gio.FileMonitorEvent.CHANGES_DONE_HINT || eventType === Gio.FileMonitorEvent.CREATED) {
          let transaction = {};
          let envApplied = this._applyExternalApiEnvFile(false, transaction, applyTarget);
          if (!envApplied) {
            if (transaction.rollbackFailed === true && this.externalApiEnvMonitor === monitor) {
              this._clearExternalApiEnvMonitor();
            }
            return;
          }
          if (envApplied) {
            if (!this._lifecycleAllowsWork() || this.externalApiEnvMonitor !== monitor) {
              if (typeof transaction.rollback === "function" && transaction.rollback() === false) {
                this._recordLifecycleError(
                  "settings-external-api-rollback",
                  new Error("External API settings rollback failed")
                );
              }
              return;
            }
            if (!this._applyExternalApiEnvTarget(applyTarget, true, transaction)) {
              let envRollbackSucceeded = typeof transaction.rollback === "function" &&
                transaction.rollback() !== false;
              if (!envRollbackSucceeded || transaction.targetRollbackFailed === true) {
                this._setStatusPreservingRecording("error", _("External API settings rollback failed"), this.lastTranscript);
                if (this.externalApiEnvMonitor === monitor) {
                  this._clearExternalApiEnvMonitor();
                }
              }
            }
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
    let requestedTarget = target === "text" ? "text" : "voice";
    let previousMonitor = this.externalApiEnvMonitor;
    this.externalApiEnvApplyTarget = requestedTarget;
    let path = this._ensureExternalApiEnvFile();
    if (!path) {
      return;
    }
    let transaction = {};
    let watcherAllowed = true;
    let envApplied = this._applyExternalApiEnvFile(false, transaction, requestedTarget);
    if (transaction.rollbackFailed === true) {
      watcherAllowed = false;
    }
    if (envApplied && !this._applyExternalApiEnvTarget(requestedTarget, true, transaction)) {
      let envRollbackSucceeded = typeof transaction.rollback === "function" &&
        transaction.rollback() !== false;
      if (!envRollbackSucceeded || transaction.targetRollbackFailed === true) {
        watcherAllowed = false;
        this._setStatusPreservingRecording("error", _("External API settings rollback failed"), this.lastTranscript);
      }
    }
    if (watcherAllowed) {
      this._watchExternalApiEnvFile(path);
    } else if (this.externalApiEnvMonitor === previousMonitor) {
      this._clearExternalApiEnvMonitor();
    }
    this._openFile(path, _("Opened External API .env"));
  },

  _prepareExternalApiEnvTarget: function(target) {
    if (target === "text") {
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        return false;
      }
      return true;
    }
    if (target !== "voice") {
      return false;
    }
    if (this.voiceModelActionToken) {
      this._setStatusPreservingRecording("error", _("Voice model operation is still running"), this.lastTranscript);
      return false;
    }
    return this.voiceModelCleanupFailed !== true;
  },

  _applyExternalApiEnvTarget: function(target, cleanupPrepared, transaction) {
    if (cleanupPrepared !== true && !this._prepareExternalApiEnvTarget(target)) {
      return false;
    }
    if (target === "text") {
      let previousBackend = this.postProcessBackend;
      let commitResult = {};
      if (!this._commitSettingsBatch(
        [["post-process-backend", "openai-compatible", previousBackend]],
        "settings-external-api-text",
        _("External API text backend could not be selected"),
        undefined,
        commitResult
      )) {
        if (transaction && typeof transaction === "object") {
          transaction.targetRollbackFailed = commitResult.rollbackSucceeded === false;
        }
        this.postProcessBackend = previousBackend;
        return false;
      }
      this.postProcessBackend = "openai-compatible";
      if (this._refreshTextModelMenuForBackend("openai-compatible") !== false) {
        this._setStatusPreservingRecording("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);
      }
      return true;
    }
    if (target !== "voice") {
      return false;
    }
    let commitResult = {};
    let voiceSelected = this._selectExternalApiVoiceBackend(commitResult);
    if (!voiceSelected && transaction && typeof transaction === "object") {
      transaction.targetRollbackFailed = commitResult.rollbackSucceeded === false;
    }
    return voiceSelected;
  },

  _selectExternalApiVoiceBackend: function(result) {
    if (this.voiceModelActionToken) {
      this._setStatusPreservingRecording("error", _("Voice model operation is still running"), this.lastTranscript);
      return false;
    }
    if (this.voiceModelCleanupFailed === true) {
      return false;
    }
    if (!this._commitVoiceBackendSettings(
      "openai-compatible",
      "",
      "external-api-voice",
      _("External API voice backend could not be selected"),
      undefined,
      result
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
    return this._refreshTextModelMenuForBackend("");
  },

  _refreshTextModelMenuForBackend: function(backendOverride) {
    let requestedBackend = String(backendOverride || "");
    if (requestedBackend !== "" && requestedBackend !== "ollama" &&
        requestedBackend !== "openai-compatible") {
      return false;
    }
    if (!this._canMutateMenu(this.textModelItem) || this.textModelItem.menu.isOpen !== true) {
      return true;
    }
    let canReportTextModelStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    if (this.ollamaModelFlowToken || this.ollamaInstallWatchToken || this.ollamaModelInstallToken || this.ollamaModelInstallRunning || this.ollamaModelCleanupFailed) {
      return true;
    }
    if (this.textModelMenuRefreshToken && !backendOverride) {
      return true;
    }
    this.textModelMenuRefreshToken = null;
    if (this._terminateProcessesByGroup("text-model-refresh") === false) {
      if (canReportTextModelStatus()) {
        this._setStatusPreservingRecording("error", _("Text model list refresh could not be stopped"), this.lastTranscript);
      }
      return false;
    }
    let backend = String(backendOverride || this.postProcessBackend || "");
    let provider = backend === "openai-compatible" ? "openai-compatible" : "ollama";
    let textModelArgs = this._tryTextModelsArgs(backendOverride);
    if (!textModelArgs) {
      try {
        this._populateTextModelMenu([], _("Could not prepare text model list"), provider);
      } catch (error) {
        this._recordLifecycleError("text-model-refresh", error);
      }
      return false;
    }
    let refreshToken = {};
    this.textModelMenuRefreshToken = refreshToken;
    let loadingMessage = provider === "openai-compatible"
      ? _("Loading OpenAI-compatible text models...")
      : _("Loading local text models...");
    try {
      if (this._textModelMenuFingerprint === null || this._textModelMenuProvider !== provider) {
        this._populateTextModelMenu([], loadingMessage, provider);
      }
    } catch (error) {
      if (this.textModelMenuRefreshToken === refreshToken) {
        this.textModelMenuRefreshToken = null;
      }
      this._recordLifecycleError("text-model-refresh", error);
      if (canReportTextModelStatus()) {
        this._setStatusPreservingRecording("error", _("Could not prepare text model list"), this.lastTranscript);
      }
      return false;
    }
    let refreshProcess = this._spawnJson(textModelArgs, (payload) => {
      if (this.textModelMenuRefreshToken !== refreshToken) {
        return;
      }
      try {
        this.textModelMenuRefreshToken = null;
        if (!this._canMutateMenu(this.textModelItem) || this.textModelItem.menu.isOpen !== true) {
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
      } catch (error) {
        if (this.textModelMenuRefreshToken === refreshToken) {
          this.textModelMenuRefreshToken = null;
        }
        this._recordLifecycleError("menu-refresh", error);
        if (canReportTextModelStatus()) {
          this._setStatusPreservingRecording("error", _("Could not refresh text model list"), this.lastTranscript);
        }
      }
    }, { resourceGroup: "text-model-refresh", invalidatesStatus: false });
    if (!refreshProcess) {
      if (this.textModelMenuRefreshToken === refreshToken) {
        this.textModelMenuRefreshToken = null;
      }
      return false;
    }
    return true;
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
    let nextFingerprint = JSON.stringify({
      backend: backend,
      provider: activeProvider,
      postProcessCommand: String(this.postProcessCommand || ""),
      ollamaModel: selectedOllamaModel,
      openaiCompatibleTextModel: String(this.openaiCompatibleTextModel || ""),
      openaiCompatibleModel: String(this.openaiCompatibleModel || ""),
      preset: String(this.postProcessPreset || ""),
      preserveCode: this.postProcessPreserveCode === true,
      neverAddContent: this.postProcessNeverAddContent === true,
      maskSensitiveData: this.postProcessMaskSensitiveData === true,
      message: messageText,
      truncated: modelListWasTruncated,
      models: models,
    });
    if (this._textModelMenuFingerprint === nextFingerprint) {
      return;
    }
    if (!this._clearMenuItems(this.textModelItem.menu)) {
      return;
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
      this._textModelMenuFingerprint = nextFingerprint;
      this._textModelMenuProvider = activeProvider;
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
        this._textModelMenuFingerprint = nextFingerprint;
        this._textModelMenuProvider = activeProvider;
        return;
      }
      let emptyLabel = activeProvider === "openai-compatible"
        ? _("No OpenAI-compatible text models found")
        : _("No local Ollama models found");
      this.textModelItem.menu.addMenuItem(this._selectionInfoItem(emptyLabel));
      this._textModelMenuFingerprint = nextFingerprint;
      this._textModelMenuProvider = activeProvider;
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
    this._textModelMenuFingerprint = nextFingerprint;
    this._textModelMenuProvider = activeProvider;
  },

  _canMutateMenu: function(item) {
    try {
      let menu = item && item.menu;
      let itemActor = item && item.actor;
      let actor = menu && menu.actor;
      return Boolean(
        this._lifecycleAllowsWork() &&
        item &&
        itemActor &&
        (typeof itemActor.is_finalized !== "function" || !itemActor.is_finalized()) &&
        menu &&
        actor &&
        (typeof actor.is_finalized !== "function" || !actor.is_finalized()) &&
        typeof menu.removeAll === "function" &&
        typeof menu.addMenuItem === "function"
      );
    } catch (error) {
      this._recordLifecycleError("menu-state", error);
      return false;
    }
  },

  _setMenuItemLabelSafely: function(item, text) {
    return this._runGuarded("menu-label", () => {
      if (!item || !item.label || !item.actor) {
        return false;
      }
      let itemActor = item.actor;
      let label = item.label;
      if ((typeof itemActor.is_finalized === "function" && itemActor.is_finalized()) ||
          (typeof label.is_finalized === "function" && label.is_finalized()) ||
          typeof label.set_text !== "function") {
        return false;
      }
      let nextText = String(text === undefined || text === null ? "" : text);
      if (typeof label.get_text === "function" && String(label.get_text()) === nextText) {
        return true;
      }
      label.set_text(nextText);
      return true;
    }, false);
  },

  _setMenuItemSensitiveSafely: function(item, sensitive) {
    return this._runGuarded("menu-sensitive", () => {
      if (!item || !item.actor ||
          (typeof item.actor.is_finalized === "function" && item.actor.is_finalized()) ||
          typeof item.setSensitive !== "function") {
        return false;
      }
      item.setSensitive(Boolean(sensitive));
      return true;
    }, false);
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
    if (this._refreshTextModelMenu() !== false) {
      this._setStatusPreservingRecording("ready", _("Polishing preset: ") + this._textPolishingPresetLabel(this.postProcessPreset), this.lastTranscript);
    }
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
    if (this._refreshTextModelMenu() !== false) {
      this._setStatusPreservingRecording("ready", label + ": " + (this[propertyName] ? _("enabled") : _("disabled")), this.lastTranscript);
    }
  },

  _selectTextModelBackend: function(backend, model, message, preserveRecording) {
    let setStatus = preserveRecording === false
      ? this._setStatus.bind(this)
      : this._setStatusPreservingRecording.bind(this);
    let safeModel;
    try {
      safeModel = this._coerceCliTextArg(model === undefined || model === null ? "" : model, "text model");
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      setStatus("error", _("Text model is invalid: ") + safeError, this.lastTranscript);
      return false;
    }
    let nextBackend = String(backend || "none");
    let previousBackend = this.postProcessBackend;
    let previousOllamaModel = this.ollamaModel;
    let previousExternalTextModel = this.openaiCompatibleTextModel;
    let settingsWrites = [["post-process-backend", nextBackend, previousBackend]];
    if (nextBackend === "ollama") {
      settingsWrites.push(["ollama-model", safeModel, previousOllamaModel]);
    }
    if (nextBackend === "openai-compatible") {
      settingsWrites.push(["openai-compatible-text-model", safeModel, previousExternalTextModel]);
    }
    let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
    let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
    if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
      setStatus("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return false;
    }
    if (!this._commitSettingsBatch(settingsWrites, "settings-text-model", _("Text model settings could not be saved"), preserveRecording)) {
      return false;
    }
    this.postProcessBackend = nextBackend;
    if (nextBackend === "ollama") {
      this.ollamaModel = safeModel;
    }
    if (nextBackend === "openai-compatible") {
      this.openaiCompatibleTextModel = safeModel;
      if (!this._writeExternalApiEnvFile()) {
        this.postProcessBackend = previousBackend;
        this.ollamaModel = previousOllamaModel;
        this.openaiCompatibleTextModel = previousExternalTextModel;
        let rollbackSucceeded = this._rollbackSettingsBatch(settingsWrites);
        this._refreshTextModelMenu();
        if (!rollbackSucceeded) {
          setStatus("error", _("Text model settings rollback failed"), this.lastTranscript);
        }
        return false;
      }
    }
    if (this._refreshTextModelMenu() !== false) {
      setStatus("ready", message, this.lastTranscript);
    }
    return true;
  },

  _clearOllamaModelFlow: function(flowToken) {
    if (flowToken && this.ollamaModelFlowToken !== flowToken) {
      return false;
    }
    let hadOllamaModelCleanupFailure = this.ollamaModelCleanupFailed === true;
    let hadOllamaModelInstall = Boolean(this.ollamaModelInstallRunning);
    let installToken = this.ollamaModelInstallToken;
    let hadOllamaTerminalWorkflow = Boolean(
      this.ollamaModelFlowToken &&
      (this.terminalWorkflowToken || this.terminalWorkflowRunning)
    );
    let terminationSucceeded = true;
    this.ollamaModelFlowToken = null;
    if (hadOllamaTerminalWorkflow) {
      this.terminalWorkflowToken = null;
    }
    if (hadOllamaModelInstall) {
      // Flow cleanup owns tokens; suppress cancelled callback to avoid fake backend error.
      terminationSucceeded = this._terminateProcessesByGroup("ollama");
      if (this.ollamaModelInstallToken === installToken) {
        if (terminationSucceeded) {
          this.ollamaModelInstallToken = null;
          this.ollamaModelInstallRunning = false;
          this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed", true);
        } else {
          this.ollamaModelInstallRunning = true;
          this.isCommandRunning = true;
        }
      }
    } else {
      terminationSucceeded = this._terminateProcessesByGroup("ollama");
    }
    if (terminationSucceeded && this._hasTrackedProcessGroup("ollama")) {
      terminationSucceeded = false;
    }
    if (hadOllamaTerminalWorkflow && terminationSucceeded) {
      this.terminalWorkflowRunning = false;
    }
    this.ollamaModelCleanupFailed = !terminationSucceeded;
    if (terminationSucceeded && hadOllamaModelCleanupFailure) {
      this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed", true);
    }
    return terminationSucceeded;
  },

  _clearOllamaModelFlowOrReport: function(flowToken) {
    if (this._clearOllamaModelFlow(flowToken)) {
      return true;
    }
    this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
    return false;
  },

  _isValidOllamaCatalogPayload: function(payload) {
    if (!payload || typeof payload !== "object" || Array.isArray(payload) ||
        typeof payload.available !== "boolean") {
      return false;
    }
    if (payload.models !== undefined && !Array.isArray(payload.models)) {
      return false;
    }
    if (payload.available === true && !Array.isArray(payload.models)) {
      return false;
    }
    for (let model of (Array.isArray(payload.models) ? payload.models : [])) {
      if (!model || typeof model !== "object" || Array.isArray(model) ||
          typeof model.name !== "string" || model.name.trim() === "") {
        return false;
      }
    }
    return true;
  },

  _ollamaCleanupStillPending: function() {
    if (!this.ollamaModelCleanupFailed) {
      return false;
    }
    if (!this._hasTrackedProcessGroup("ollama")) {
      let cleanupReleased = this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed");
      if (cleanupReleased && !this.ollamaModelCleanupFailed &&
          !this.ollamaModelInstallToken && !this.ollamaModelInstallRunning) {
        return false;
      }
    }
    this._setStatusPreservingRecording("error", _("Previous Ollama operation is still stopping; try again shortly"), this.lastTranscript);
    return true;
  },

  _cancelOllamaFlowForRecording: function() {
    if (!this.ollamaModelFlowToken && !this.ollamaInstallWatchToken && !this.ollamaModelInstallRunning && !this.ollamaModelCleanupFailed) {
      return false;
    }
    let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
    let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
    return ollamaWatchCleanupSucceeded && ollamaFlowCleanupSucceeded;
  },

  _activateOllamaTextModelFlow: function() {
    if (this._hasActiveRecordingState()) {
      return;
    }
    if (this._hasLocalProcessingWorkflow()) {
      return;
    }
    if (this._ollamaCleanupStillPending()) {
      return;
    }
    if (this.ollamaModelFlowToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        return;
      }
      this._setStatus("error", _("Install zenity to choose an Ollama model"), this.lastTranscript);
      return;
    }
    let textModelArgs = this._tryTextModelsArgs("ollama");
    if (!textModelArgs) {
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      }
      return;
    }
    this.textModelMenuRefreshToken = null;
    if (this._terminateProcessesByGroup("text-model-refresh") === false) {
      this._setStatusPreservingRecording("error", _("Text model list refresh could not be stopped"), this.lastTranscript);
      return;
    }
    if (this._cancelOllamaInstallWatch() === false) {
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return;
    }
    let flowToken = {};
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Checking Ollama..."), this.lastTranscript);
    this._spawnJson(textModelArgs, (payload) => {
      try {
        if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
          return;
        }
        if (payload.error) {
          let safeError = typeof payload.error === "string" && payload.error.trim() !== ""
            ? this._sanitizeErrorMessage(payload.error)
            : _("Could not check Ollama");
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", safeError, this.lastTranscript);
          this._notify(_("Could not check Ollama"), safeError, true);
          return;
        }
        if (!this._isValidOllamaCatalogPayload(payload)) {
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", _("Ollama status response was invalid"), this.lastTranscript);
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
      } catch (error) {
        this._recordLifecycleError("ollama-flow", error);
        if (this.ollamaModelFlowToken !== flowToken ||
            !this._clearOllamaModelFlowOrReport(flowToken)) {
          return;
        }
        this._setStatus("error", _("Could not check Ollama"), this.lastTranscript);
      }
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
    if (this._hasLocalProcessingWorkflow()) {
      return;
    }
    if (this._ollamaCleanupStillPending()) {
      return;
    }
    if (this.ollamaModelFlowToken) {
      return;
    }
    if (!this._findTrustedProgramInPath("zenity")) {
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        return;
      }
      this._setStatus("error", _("Install zenity to choose an Ollama model"), this.lastTranscript);
      return;
    }
    let textModelArgs = this._tryTextModelsArgs("ollama");
    if (!textModelArgs) {
      let ollamaWatchCleanupSucceeded = this._cancelOllamaInstallWatch() !== false;
      let ollamaFlowCleanupSucceeded = this._clearOllamaModelFlow();
      if (!ollamaWatchCleanupSucceeded || !ollamaFlowCleanupSucceeded) {
        this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      }
      return;
    }
    this.textModelMenuRefreshToken = null;
    if (this._terminateProcessesByGroup("text-model-refresh") === false) {
      this._setStatusPreservingRecording("error", _("Text model list refresh could not be stopped"), this.lastTranscript);
      return;
    }
    if (this._cancelOllamaInstallWatch() === false) {
      this._setStatusPreservingRecording("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return;
    }
    let flowToken = {};
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Loading Ollama text models..."), this.lastTranscript);
    this._spawnJson(textModelArgs, (payload) => {
      try {
        if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
          return;
        }
        if (payload.error) {
          let safeError = typeof payload.error === "string" && payload.error.trim() !== ""
            ? this._sanitizeErrorMessage(payload.error)
            : _("Could not load Ollama models");
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", safeError, this.lastTranscript);
          this._notify(_("Could not load Ollama models"), safeError, true);
          return;
        }
        if (!this._isValidOllamaCatalogPayload(payload)) {
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", _("Ollama status response was invalid"), this.lastTranscript);
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
      } catch (error) {
        this._recordLifecycleError("ollama-flow", error);
        if (this.ollamaModelFlowToken !== flowToken ||
            !this._clearOllamaModelFlowOrReport(flowToken)) {
          return;
        }
        this._setStatus("error", _("Could not load Ollama models"), this.lastTranscript);
      }
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
      this._recordLifecycleError("ollama-flow", error);
      if (!this._clearOllamaModelFlowOrReport(flowToken)) {
        return;
      }
      this._setStatus("error", _("Could not prepare Ollama model selection"), this.lastTranscript);
      return;
    }
    this._spawnText(choiceArgs, (output, result) => {
      if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      let clearFlow = () => {
        if (this._clearOllamaModelFlow(flowToken)) {
          return true;
        }
        this._setStatus("error", _("Ollama operation could not be stopped"), this.lastTranscript);
        return false;
      };
      let finish = (message) => {
        if (!clearFlow()) {
          return false;
        }
        this._setStatus("ready", message, this.lastTranscript);
        return true;
      };
      if (result && result.startupFailed === true) {
        if (!clearFlow()) {
          return;
        }
        this._setStatus("error", _("Could not open Ollama model selection"), this.lastTranscript);
        return;
      }
      if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
        if (!clearFlow()) {
          return;
        }
        this._setStatus(
          result.cancelled ? "ready" : "error",
          result.cancelled ? _("Ollama model selection cancelled") : _("Could not complete Ollama model selection"),
          this.lastTranscript
        );
        return;
      }
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
          if (!clearFlow()) {
            return;
          }
          this._selectTextModelBackend("ollama", model, _("Text model: ") + model, false);
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
      this._recordLifecycleError("ollama-flow", error);
      if (!this._clearOllamaModelFlowOrReport(flowToken)) {
        return;
      }
      this._setStatus("error", _("Could not prepare Ollama model prompt"), this.lastTranscript);
      return;
    }
    if (!zenity) {
      if (!this._clearOllamaModelFlowOrReport(flowToken)) {
        return;
      }
      this._setStatus("error", _("Install zenity to enter an Ollama model name"), this.lastTranscript);
      return;
    }
    this.ollamaModelFlowToken = flowToken;
    this._setStatus("processing", _("Choose Ollama text model..."), this.lastTranscript);
    let promptArgs;
    try {
      promptArgs = this._ollamaModelPromptArgs();
    } catch (error) {
      this._recordLifecycleError("ollama-flow", error);
      if (!this._clearOllamaModelFlowOrReport(flowToken)) {
        return;
      }
      this._setStatus("error", _("Could not prepare Ollama model prompt"), this.lastTranscript);
      return;
    }
    this._spawnText(promptArgs, (output, result) => {
      if (this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
        return;
      }
      if (result && result.startupFailed === true) {
        if (!this._clearOllamaModelFlow(flowToken)) {
          this._setStatus("error", _("Ollama operation could not be stopped"), this.lastTranscript);
          return;
        }
        this._setStatus("error", _("Could not open Ollama model prompt"), this.lastTranscript);
        return;
      }
      if (result && (result.error || result.cancelled || result.timedOut || result.outputTooLarge)) {
        if (!this._clearOllamaModelFlow(flowToken)) {
          this._setStatus("error", _("Ollama operation could not be stopped"), this.lastTranscript);
          return;
        }
        this._setStatus(
          result.cancelled ? "ready" : "error",
          result.cancelled ? _("Ollama model installation cancelled") : _("Could not complete Ollama model prompt"),
          this.lastTranscript
        );
        return;
      }
      let model = String(output || "").trim();
      if (model === "") {
        if (!this._clearOllamaModelFlow(flowToken)) {
          this._setStatus("error", _("Ollama operation could not be stopped"), this.lastTranscript);
          return;
        }
        this._setStatus("ready", _("Ollama model installation cancelled"), this.lastTranscript);
        return;
      }
      this._installOllamaTextModel(model);
    }, { timeoutMs: 0, resourceGroup: "ollama" });
  },

  _installOllamaTextModel: function(model) {
    let flowToken = this.ollamaModelFlowToken;
    if (this._hasActiveRecordingState() && this.status !== "processing") {
      this._clearOllamaModelFlowOrReport(flowToken);
      return;
    }
    if (this.isCommandRunning) {
      if (!this._clearOllamaModelFlowOrReport(flowToken)) {
        return;
      }
      this._setStatus("error", _("Another command is already running"), this.lastTranscript);
      return;
    }
    let installArgs;
    try {
      installArgs = this._installTextModelArgs(model);
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      if (!this._clearOllamaModelFlowOrReport(flowToken)) {
        return;
      }
      this._setStatus("error", _("Could not prepare Ollama model installation: ") + safeError, this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this.ollamaModelInstallRunning = true;
    let installToken = {};
    this.ollamaModelInstallToken = installToken;
    this._setStatus("processing", _("Installing Ollama model: ") + model, this.lastTranscript);
    this._spawnJson(installArgs, (payload) => {
      try {
        if (this.ollamaModelInstallToken !== installToken) {
          return;
        }
        this.ollamaModelInstallToken = null;
        this.ollamaModelInstallRunning = false;
        if (!flowToken || this.ollamaModelFlowToken !== flowToken || !this._lifecycleAllowsWork()) {
          this._releaseBusyStateAfterProcessCleanup("ollama", "ollamaModelCleanupFailed", true);
          return;
        }
        this.isCommandRunning = false;
        if (payload.error) {
          let safeError = typeof payload.error === "string" && payload.error.trim() !== ""
            ? this._sanitizeErrorMessage(payload.error)
            : _("Ollama model installation failed");
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", safeError, this.lastTranscript);
          this._notify(_("Ollama model installation failed"), safeError, true);
          this._refreshTextModelMenu();
          return;
        }
        let hasInstalledModel = Object.prototype.hasOwnProperty.call(payload, "model");
        let hasCompatibleInstalledModel = hasInstalledModel &&
          typeof payload.model === "string" && payload.model.trim() !== "";
        let installedModel = hasCompatibleInstalledModel
          ? payload.model.trim()
          : String(model || "").trim();
        let installConfirmed = (!hasInstalledModel || hasCompatibleInstalledModel) &&
          (payload.status === "done" ||
            (payload.status === undefined && hasCompatibleInstalledModel));
        if (!installConfirmed) {
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", _("Ollama installation response was invalid"), this.lastTranscript);
          this._refreshTextModelMenu();
          return;
        }
        if (installedModel === "") {
          if (!this._clearOllamaModelFlowOrReport(flowToken)) {
            return;
          }
          this._setStatus("error", _("Ollama installation returned no model name"), this.lastTranscript);
          this._refreshTextModelMenu();
          return;
        }
        let message = _("Ollama model installed: ") + installedModel;
        if (!this._clearOllamaModelFlowOrReport(flowToken)) {
          return;
        }
        if (!this._selectTextModelBackend("ollama", installedModel, message, false)) {
          this._refreshTextModelMenu();
          return;
        }
        this._notify(_("Ollama model installed"), installedModel, false);
      } catch (error) {
        this._recordLifecycleError("ollama-flow", error);
        if (this.ollamaModelFlowToken !== flowToken ||
            !this._clearOllamaModelFlowOrReport(flowToken)) {
          return;
        }
        this._setStatus("error", _("Could not complete Ollama model installation"), this.lastTranscript);
      }
    }, { timeoutMs: BENCHMARK_COMMAND_TIMEOUT_MS, resourceGroup: "ollama" });
  },

  _refreshHistory: function() {
    if (!this._canMutateMenu(this.historyItem) || this.historyItem.menu.isOpen !== true) {
      return;
    }
    let canReportHistoryStatus = () => !this.isCommandRunning &&
      !this._hasActiveRecordingState() && !this._hasLocalProcessingWorkflow();
    if (this.historyRefreshToken) {
      this.historyRefreshQueued = true;
      return;
    }
    this.historyRefreshQueued = false;
    if (this._terminateProcessesByGroup("history-refresh") === false) {
      this._populateHistoryMenu([]);
      if (canReportHistoryStatus()) {
        this._setStatusPreservingRecording("error", _("History refresh could not be stopped"), this.lastTranscript);
      }
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
      if (canReportHistoryStatus()) {
        this._setStatusPreservingRecording("error", _("Could not prepare transcript history"), this.lastTranscript);
      }
      return;
    }
    this._spawnJson(historyArgs, (payload) => {
      if (this.historyRefreshToken !== refreshToken) {
        return;
      }
      let refreshQueued = this.historyRefreshQueued === true;
      this.historyRefreshQueued = false;
      try {
        this.historyRefreshToken = null;
        if (!this._canMutateMenu(this.historyItem) || this.historyItem.menu.isOpen !== true) {
          return;
        }
        if (payload.error) {
          this._populateHistoryMenu([]);
          if (canReportHistoryStatus()) {
            this._setStatusPreservingRecording("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
          }
          return;
        }
        this._populateHistoryMenu(payload.transcripts || []);
      } catch (error) {
        if (this.historyRefreshToken === refreshToken) {
          this.historyRefreshToken = null;
        }
        this._recordLifecycleError("menu-refresh", error);
        if (canReportHistoryStatus()) {
          this._setStatusPreservingRecording("error", _("Could not refresh transcript history"), this.lastTranscript);
        }
      } finally {
        if (
          refreshQueued &&
          !this.historyRefreshToken &&
          this._canMutateMenu(this.historyItem) &&
          this.historyItem.menu.isOpen === true
        ) {
          this._refreshHistory();
        }
      }
    }, { resourceGroup: "history-refresh", invalidatesStatus: false });
  },

  _listAllTranscripts: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow() ||
        this.transcriptListPromptToken || this.transcriptWindowToken) {
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
    this.transcriptListPromptDialog = dialog;
    let completed = false;
    let complete = (result, releasePrompt) => {
      if (completed) {
        return;
      }
      completed = true;
      let ownsPrompt = this.transcriptListPromptToken === promptToken;
      if (ownsPrompt && releasePrompt !== false) {
        this.transcriptListPromptToken = null;
        if (this.transcriptListPromptDialog === dialog) {
          this.transcriptListPromptDialog = null;
        }
      }
      if (!ownsPrompt || !this._lifecycleAllowsWork()) {
        return;
      }
      if (typeof completionCallback === "function") {
        try {
          completionCallback(result === true);
        } catch (error) {
          this._recordLifecycleError("transcript-list-completion", error);
        }
      }
    };
    let failToOpen = () => {
      let closed = this._dialogClose(dialog, "transcript-list");
      if (!closed) {
        this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be closed"), this.lastTranscript);
      } else {
        this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);
      }
      complete(false, closed);
    };
    if (!dialog || !this._dialogAddChild(dialog, this._newSafeLabel(_("List all transcripts?"), { x_expand: true }, "transcript-list"), "transcript-list") ||
      !this._dialogAddChild(dialog, this._newSafeLabel(
      _("This shows complete transcript contents in a plaintext window. Continue only if your screen and session are trusted."),
      { x_expand: true },
      "transcript-list"
    ), "transcript-list")) {
      failToOpen();
      return;
    }
    if (!this._dialogSetButtons(dialog, [
      {
        label: _("Cancel"),
        key: Clutter.KEY_Escape,
        action: function() {
          let closed = false;
          try {
            closed = this._dialogClose(dialog, "transcript-list");
            if (!closed) {
              this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be closed"), this.lastTranscript);
              return;
            }
            if (this.transcriptListPromptToken === promptToken) {
              this._setStatusPreservingRecording("ready", _("Transcript list cancelled"), this.lastTranscript);
            }
          } finally {
            complete(false, closed);
          }
        }.bind(this),
      },
      {
        label: _("Show transcripts"),
        action: function() {
          let closed = this._dialogClose(dialog, "transcript-list");
          if (!closed) {
            this._setStatusPreservingRecording("error", _("Transcript list confirmation could not be closed"), this.lastTranscript);
            complete(false, false);
            return;
          }
          complete(true);
        }.bind(this),
      }
    ], "transcript-list")) {
      failToOpen();
      return;
    }
    if (!this._dialogOpen(dialog, "transcript-list")) {
      failToOpen();
      this._notify(_("Speed of Cinnamon"), _("Transcript list confirmation could not be opened"), true);
    }
  },

  _loadAllTranscriptsDocument: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
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
    let cleanupToken = {};
    this._cleanupCommandToken = cleanupToken;
    this._setStatus("processing", _("Preparing transcript list..."), this.lastTranscript);
    this._spawnJson(historyDocumentArgs, (payload) => {
      if (this._cleanupCommandToken !== cleanupToken || !this._lifecycleAllowsWork()) {
        return;
      }
      try {
        this._cleanupCommandToken = null;
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
      } catch (error) {
        if (this._cleanupCommandToken === cleanupToken) {
          this._cleanupCommandToken = null;
        }
        this.isCommandRunning = false;
        this._recordLifecycleError("maintenance-command", error);
        this._setStatus("error", _("Could not complete transcript list"), this.lastTranscript);
      }
    }, { resourceGroup: "maintenance" });
  },

  _showTranscriptsWindow: function(content, count, truncated) {
    if (this.transcriptWindowToken) {
      return;
    }
    let windowToken = {};
    this.transcriptWindowToken = windowToken;
    let isCurrentWindow = () => this.transcriptWindowToken === windowToken && this._lifecycleAllowsWork();
    let setTranscriptWindowError = (message) => {
      if (this.status === "recording" || this.status === "recorded" || (this.status === "processing" && this.isCommandRunning)) {
        this._setStatusPreservingRecording("error", message, this.lastTranscript);
        return;
      }
      this._setStatus("error", message, this.lastTranscript);
    };
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
      this._setStatus("error", message, this.lastTranscript);
      this._notify(_("Could not open transcript list"), message, true);
      return;
    }
    if (!zenity) {
      releaseWindow();
      let message = _("Install zenity to show the transcript list without writing a plaintext file.");
      this._setStatus("error", message, this.lastTranscript);
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
        resourceGroup: "maintenance",
      }, (stdout, stderr, result) => {
        if (!isCurrentWindow()) {
          return;
        }
        releaseWindow();
        if (result && result.error && !result.cancelled) {
          setTranscriptWindowError(_("Transcript list window closed unexpectedly"));
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
      this._setStatus("done", message, this.lastTranscript);
    } catch (err) {
      if (!isCurrentWindow()) {
        return;
      }
      releaseWindow();
      let safeError = this._sanitizeErrorMessage(String(err && err.message ? err.message : err));
      setTranscriptWindowError(_("Could not open transcript list: ") + safeError);
      this._notify(_("Could not open transcript list"), safeError, true);
    }
  },

  _exportAllTranscripts: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
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
    let cleanupToken = {};
    this._cleanupCommandToken = cleanupToken;
    this._setStatus("processing", _("Exporting transcripts..."), this.lastTranscript);
    this._spawnJson(exportArgs, (payload) => {
      if (this._cleanupCommandToken !== cleanupToken || !this._lifecycleAllowsWork()) {
        return;
      }
      try {
        this._cleanupCommandToken = null;
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
      } catch (error) {
        if (this._cleanupCommandToken === cleanupToken) {
          this._cleanupCommandToken = null;
        }
        this.isCommandRunning = false;
        this._recordLifecycleError("maintenance-command", error);
        this._setStatus("error", _("Could not complete transcript export"), this.lastTranscript);
      }
    }, { resourceGroup: "maintenance" });
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
    if (this.cleanupPreviewDialogToken) {
      return;
    }
    let dialogToken = {};
    this.cleanupPreviewDialogToken = dialogToken;
    let releaseDialog = () => {
      if (this.cleanupPreviewDialogToken === dialogToken) {
        this.cleanupPreviewDialogToken = null;
        if (this.cleanupPreviewDialog === dialog) {
          this.cleanupPreviewDialog = null;
        }
      }
    };
    let closeDialog = (dialog) => {
      let closed = this._dialogClose(dialog, "cleanup-preview");
      if (closed) {
        releaseDialog();
      }
      return closed;
    };
    let dialog = this._newSafeDialog("cleanup-preview");
    this.cleanupPreviewDialog = dialog;
    let failToOpen = () => {
      if (!dialog) {
        releaseDialog();
        this._setStatusPreservingRecording("error", _("Cleanup preview could not be opened"), this.lastTranscript);
        return;
      }
      if (closeDialog(dialog)) {
        this._notify(_("Speed of Cinnamon"), _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), false);
      } else {
        this._setStatusPreservingRecording("error", _("Cleanup preview could not be closed"), this.lastTranscript);
      }
    };
    if (!dialog || !this._dialogAddChild(dialog, this._newSafeLabel(this._cleanupPreviewText(payload), { x_expand: true }, "cleanup-preview"), "cleanup-preview") ||
      !this._dialogSetButtons(dialog, [
      {
        label: _("Close"),
        key: Clutter.KEY_Escape,
        action: function() {
          closeDialog(dialog);
        }.bind(this),
      }
    ], "cleanup-preview")) {
      failToOpen();
      return;
    }
    if (!this._dialogOpen(dialog, "cleanup-preview")) {
      failToOpen();
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
    let previousValues = {
      preset: this.postProcessPreset,
      prompt: this.postProcessPrompt,
      preserveCode: this.postProcessPreserveCode,
      neverAddContent: this.postProcessNeverAddContent,
      maskSensitiveData: this.postProcessMaskSensitiveData,
    };
    let settingsWrites = [
      ["post-process-preset", TEXT_POLISHING_SAFE_PRESET, previousValues.preset],
      ["post-process-prompt", "", previousValues.prompt],
      ["post-process-preserve-code", true, previousValues.preserveCode],
      ["post-process-never-add-content", true, previousValues.neverAddContent],
      ["post-process-mask-sensitive-data", false, previousValues.maskSensitiveData],
    ];
    if (!this._commitSettingsBatch(settingsWrites, "settings-text-polishing", _("Text polishing defaults could not be saved"))) {
      return;
    }
    this.postProcessPreset = TEXT_POLISHING_SAFE_PRESET;
    this.postProcessPrompt = "";
    this.postProcessPreserveCode = true;
    this.postProcessNeverAddContent = true;
    this.postProcessMaskSensitiveData = false;
    if (this._refreshTextModelMenu() !== false) {
      this._setStatusPreservingRecording("ready", _("Text polishing defaults restored"), this.lastTranscript);
    }
  },

  _previewCleanup: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this.cleanupPreviewDialogToken || this._hasLocalProcessingWorkflow()) {
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
    let cleanupToken = {};
    this._cleanupCommandToken = cleanupToken;
    this._setStatus("processing", _("Previewing cleanup..."), this.lastTranscript);
    this._spawnJson(cleanupPreviewArgs, (payload) => {
      if (this._cleanupCommandToken !== cleanupToken || !this._lifecycleAllowsWork()) {
        return;
      }
      try {
        this._cleanupCommandToken = null;
        this.isCommandRunning = false;
        if (payload.error) {
          this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
          return;
        }
        this._setStatus("ready", _("Cleanup preview: ") + String(this._cleanupCount(payload, true)), this.lastTranscript);
        this._showCleanupPreviewDialog(payload);
      } catch (error) {
        if (this._cleanupCommandToken === cleanupToken) {
          this._cleanupCommandToken = null;
        }
        this.isCommandRunning = false;
        this._recordLifecycleError("maintenance-command", error);
        this._setStatus("error", _("Could not complete cleanup preview"), this.lastTranscript);
      }
    }, { resourceGroup: "maintenance" });
  },

  _cleanupOldFiles: function() {
    if (this.isCommandRunning || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
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
    let cleanupToken = {};
    this._cleanupCommandToken = cleanupToken;
    this._setStatus("processing", _("Cleaning old files..."), this.lastTranscript);
    this._spawnJson(cleanupArgs, (payload) => {
      if (this._cleanupCommandToken !== cleanupToken || !this._lifecycleAllowsWork()) {
        return;
      }
      try {
        this._cleanupCommandToken = null;
        this.isCommandRunning = false;
        if (payload.error) {
          this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
          return;
        }
        let deleted = this._cleanupCount(payload, false);
        this._setStatus("done", _("Cleaned old files: ") + String(deleted), this.lastTranscript);
        this._refreshHistory();
      } catch (error) {
        if (this._cleanupCommandToken === cleanupToken) {
          this._cleanupCommandToken = null;
        }
        this.isCommandRunning = false;
        this._recordLifecycleError("maintenance-command", error);
        this._setStatus("error", _("Could not complete cleanup"), this.lastTranscript);
      }
    }, { resourceGroup: "maintenance" });
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
    let orphanedResourceValues = {};
    for (let name of ["signals", "hotkeys", "processes", "timers", "dialogs", "monitors", "cancellables", "menus"]) {
      try {
        orphanedResourceValues[name] = this["_orphaned" + name.charAt(0).toUpperCase() + name.slice(1)];
      } catch (error) {
        recordDiagnosticError(error);
        orphanedResourceValues[name] = [];
      }
    }
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
    let orphanedResourceCounts = {
      signals: countArrayEntries(orphanedResourceValues.signals),
      hotkeys: countArrayEntries(orphanedResourceValues.hotkeys),
      processes: countArrayEntries(orphanedResourceValues.processes),
      timers: countArrayEntries(orphanedResourceValues.timers),
      dialogs: countArrayEntries(orphanedResourceValues.dialogs),
      monitors: countArrayEntries(orphanedResourceValues.monitors),
      cancellables: countArrayEntries(orphanedResourceValues.cancellables),
      menus: countArrayEntries(orphanedResourceValues.menus),
    };
    let orphanedTooltip = false;
    try {
      orphanedTooltip = this._orphanedTooltip === true;
    } catch (error) {
      recordDiagnosticError(error);
    }
    let orphanedTotal = orphanedResourceCounts.signals +
      orphanedResourceCounts.hotkeys +
      orphanedResourceCounts.processes +
      orphanedResourceCounts.timers +
      orphanedResourceCounts.dialogs +
      orphanedResourceCounts.monitors +
      orphanedResourceCounts.cancellables +
      orphanedResourceCounts.menus +
      (orphanedTooltip ? 1 : 0);
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
        orphaned_signals: orphanedResourceCounts.signals,
        orphaned_hotkeys: orphanedResourceCounts.hotkeys,
        orphaned_processes: orphanedResourceCounts.processes,
        orphaned_timers: orphanedResourceCounts.timers,
        orphaned_dialogs: orphanedResourceCounts.dialogs,
        orphaned_monitors: orphanedResourceCounts.monitors,
        orphaned_cancellables: orphanedResourceCounts.cancellables,
        orphaned_menus: orphanedResourceCounts.menus,
        orphaned_tooltip: orphanedTooltip ? 1 : 0,
        orphaned_total: orphanedTotal,
      },
      process_groups: processGroups,
    };
  },

  _settingsSnapshotForCli: function(includeLifecycle, preserveMultilineText) {
    let snapshot = this._settingsSnapshot();
    for (let key in CLI_TEXT_SETTINGS) {
      if (Object.prototype.hasOwnProperty.call(CLI_TEXT_SETTINGS, key) && Object.prototype.hasOwnProperty.call(snapshot, key)) {
        let multilineField = key === "personal-context" || key === "vocabulary";
        let value = snapshot[key];
        if (multilineField && !preserveMultilineText) {
          value = this._singleLineCliTextValue(value);
        }
        snapshot[key] = this._coerceCliTextArg(
          value,
          CLI_TEXT_SETTINGS[key],
          Boolean(preserveMultilineText && multilineField)
        );
      }
    }
    if (includeLifecycle) {
      snapshot["applet-lifecycle"] = this._appletLifecycleDiagnostics();
    }
    return snapshot;
  },

  _settingsSnapshotInputOption: function(includeLifecycle, preserveMultilineText) {
    return { inputText: JSON.stringify(this._settingsSnapshotForCli(Boolean(includeLifecycle), Boolean(preserveMultilineText))) };
  },

  _settingsSnapshotInputOptionOrNull: function(includeLifecycle, errorStatus, preserveMultilineText) {
    try {
      return this._settingsSnapshotInputOption(Boolean(includeLifecycle), Boolean(preserveMultilineText));
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatus(errorStatus || "error", _("Could not prepare settings for backend: ") + safeError, this.lastTranscript);
      return null;
    }
  },

  _exportSettings: function() {
    if (this.settingsTransferToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
      return;
    }
    let inputOption = this._settingsSnapshotInputOptionOrNull(false, undefined, true);
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
      try {
        if (payload.error) {
          this.settingsTransferToken = null;
          this._setStatus("error", this._sanitizeErrorMessage(payload.error), this.lastTranscript);
          return;
        }
        this.settingsTransferToken = null;
        this._setStatus("done", _("Exported settings"), this.lastTranscript);
      } catch (error) {
        if (this.settingsTransferToken === transferToken) {
          this.settingsTransferToken = null;
        }
        this._recordLifecycleError("settings-transfer", error);
        this._setStatus("error", _("Could not complete settings export"), this.lastTranscript);
      }
    }, Object.assign({}, inputOption, { resourceGroup: "settings-transfer" }));
  },

  _importSettings: function() {
    if (this.settingsTransferToken || this._hasActiveRecordingState() || this._hasLocalProcessingWorkflow()) {
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
      try {
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
      } catch (error) {
        if (this.settingsTransferToken === transferToken) {
          this.settingsTransferToken = null;
        }
        this._recordLifecycleError("settings-transfer", error);
        this._setStatus("error", _("Could not complete settings import"), this.lastTranscript);
      }
    }, { resourceGroup: "settings-transfer" });
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
        this._setSettingValueOrThrow(item.key, item.value, "Imported setting could not be saved");
      }
    } catch (err) {
      for (let index = attemptedWrites.length - 1; index >= 0; index--) {
        let item = attemptedWrites[index];
        try {
          this._setSettingValueOrThrow(item.key, item.previous, "Imported setting rollback failed");
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
      if (this._cliCommandCache && this._cliCommandCache.command === normalized[0]) {
        this._cliCommandCache = null;
      }
      throw new Error("Backend command is not executable");
    }
    normalized[0] = resolvedCommand;
    return normalized;
  },

  _wrapSubprocessArgs: function(args) {
    if (!Array.isArray(args) || args.length === 0) {
      return args;
    }
    let setsid = this._trustedSetsidPath;
    if (!setsid) {
      setsid = this._findTrustedProgramInPath("setsid");
      if (setsid) {
        this._trustedSetsidPath = setsid;
      }
    }
    if (!setsid) {
      throw new Error("setsid is unavailable; refusing ungrouped subprocess");
    }
    return [setsid, "--"].concat(args);
  },

  _coerceCliTextArg: function(value, fieldName, allowNewlines) {
    if (value !== undefined && value !== null && typeof value !== "string") {
      throw new Error(String(fieldName || "value") + " must be text");
    }
    let normalized = typeof value === "string" ? value : "";
    if (normalized.indexOf("\u0000") >= 0) {
      throw new Error(String(fieldName || "value") + " contains invalid bytes");
    }
    if (this._containsCliControlChars(normalized, Boolean(allowNewlines))) {
      throw new Error(String(fieldName || "value") + " contains invalid control character");
    }
    let maxChars = Object.prototype.hasOwnProperty.call(CLI_RUNTIME_TEXT_LIMITS, String(fieldName || ""))
      ? CLI_RUNTIME_TEXT_LIMITS[String(fieldName || "")]
      : MAX_SETTING_TEXT_CHARS;
    if (normalized.length > maxChars) {
      throw new Error(String(fieldName || "value") + " is too long");
    }
    let valueBytes = ByteArray.fromString(normalized).length;
    if (valueBytes > maxChars) {
      throw new Error(String(fieldName || "value") + " is too large");
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

  _containsCliControlChars: function(value, allowNewlines) {
    let normalized = String(value || "").toLowerCase();
    if (
      normalized.indexOf("\u000d") >= 0
      || (!allowNewlines && normalized.indexOf("\u000a") >= 0)
      || normalized.indexOf("\\r") >= 0
      || normalized.indexOf("\\n") >= 0
      || normalized.indexOf("\\u000d") >= 0
      || normalized.indexOf("\\u000a") >= 0
    ) {
      return true;
    }
    for (let i = 0; i < normalized.length; i++) {
      const code = normalized.charCodeAt(i);
      if ((code < 0x20 && !(allowNewlines && code === 0x0a)) || code === 0x7f) {
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
      return this._coerceCliTextArg(
        value,
        IMPORT_TEXT_SETTINGS[key],
        key === "personal-context" || key === "vocabulary"
      );
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
    try {
      let parsed = JSON.parse(output || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        return { status: "error", error: "Invalid backend response: expected JSON object", transport_error: true };
      }
      return parsed;
    } catch (err) {
      return { status: "error", error: "Invalid backend response: " + err, transport_error: true };
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
    if (!Array.isArray(this._orphanedProcesses)) {
      this._recordLifecycleError("process-state", new Error("Process orphan registry is unavailable"));
      return null;
    }
    if (this._orphanedProcesses.length > 0) {
      let orphanCleanupSucceeded = this._retryOrphanedProcesses();
      if (!orphanCleanupSucceeded || this._orphanedProcesses.length > 0) {
        this._recordLifecycleError("process-state", new Error("An orphaned process is still pending"));
        return null;
      }
    }
    if (!Array.isArray(this._orphanedCancellables)) {
      this._recordLifecycleError("cancellable-state", new Error("Cancellable orphan registry is unavailable"));
      return null;
    }
    if (this._orphanedCancellables.length > 0) {
      let orphanCancellableCleanupSucceeded = this._retryOrphanedCancellables();
      if (!orphanCancellableCleanupSucceeded || this._orphanedCancellables.length > 0) {
        this._recordLifecycleError("cancellable-state", new Error("An orphaned cancellable is still pending"));
        return null;
      }
    }
    if (!Array.isArray(this._orphanedTimers)) {
      this._recordLifecycleError("timer-state", new Error("Timer orphan registry is unavailable"));
      return null;
    }
    if (this._orphanedTimers.length > 0) {
      let orphanTimerCleanupSucceeded = this._retryOrphanedTimers();
      if (!orphanTimerCleanupSucceeded || this._orphanedTimers.length > 0) {
        this._recordLifecycleError("timer-state", new Error("An orphaned timer is still pending"));
        return null;
      }
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
    let process = null;
    try {
      let launcher = new Gio.SubprocessLauncher({ flags: flags });
      env = env || {};
      for (let key in env) {
        if (Object.prototype.hasOwnProperty.call(env, key)) {
          launcher.setenv(key, String(env[key] || ""), true);
        }
      }
      let spawnArgs = this._wrapSubprocessArgs(args);
      process = launcher.spawnv(spawnArgs);
    } catch (error) {
      this._trustedSetsidPath = null;
      this._recordLifecycleError("process-spawn", error);
      return null;
    }
    let generation = this.spawnGeneration;
    let processToken;
    try {
      processToken = this._registerProcess(process, generation, options.resourceGroup);
    } catch (error) {
      if (!processToken && error && (typeof error === "object" || typeof error === "function") && error.processToken) {
        processToken = error.processToken;
      }
      let processTerminated = this._terminateProcess(process);
      if (processTerminated) {
        let processCleanupSucceeded = this._unregisterProcess(processToken);
        if (!processCleanupSucceeded) {
          let orphanTracked = this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, true);
          let orphanCleanupSucceeded = orphanTracked && this._retryOrphanedProcesses();
          if (!orphanCleanupSucceeded) {
            this._scheduleProcessCleanupRetry();
          }
        } else if (!this._untrackOrphanedProcess(process)) {
          this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, true);
          this._scheduleProcessCleanupRetry();
        }
      } else {
        this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken);
        this._scheduleProcessCleanupRetry();
      }
      throw error;
    }
    let cancellable = null;
    let cancellableToken = null;
    try {
      cancellable = new Gio.Cancellable();
      cancellableToken = this._registerCancellable(cancellable);
    } catch (error) {
      if (!cancellableToken && error && (typeof error === "object" || typeof error === "function") && error.cancellableToken) {
        cancellableToken = error.cancellableToken;
      }
      let cancellableCleanupSucceeded = false;
      try {
        cancellableCleanupSucceeded = this._unregisterCancellable(cancellableToken);
      } catch (cleanupError) {
        this._recordLifecycleError("cancellable-unregister", cleanupError);
      }
      if (!cancellableCleanupSucceeded) {
        this._trackOrphanedCancellable(cancellableToken, false);
      } else if (!this._untrackOrphanedCancellable(cancellableToken)) {
        this._recordLifecycleError("cancellable-state", new Error("Cancellable orphan cleanup could not be completed"));
      }
      let orphanCancellableCleanupSucceeded = this._retryOrphanedCancellables();
      if (!orphanCancellableCleanupSucceeded ||
          !Array.isArray(this._orphanedCancellables) || this._orphanedCancellables.length > 0) {
        this._scheduleProcessCleanupRetry();
      }
      let processTerminated = this._terminateProcess(process);
      if (processTerminated) {
        if (!this._unregisterProcess(processToken)) {
          this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, true);
          this._scheduleProcessCleanupRetry();
        }
      } else {
        this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, false);
        this._scheduleProcessCleanupRetry();
      }
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
    let processWaitStarted = false;
    let terminationFailed = false;
    let cleanupComplete = false;
    let callbackDelivered = false;
    let setupFailed = false;
    let inputPending = hasInput;
    let timeoutSourceAlreadyRemoved = false;

    let cleanupResources = (timeoutCleanupSucceeded) => {
      if (cleanupComplete) {
        return true;
      }
      if (timeoutCleanupSucceeded === undefined) {
        timeoutCleanupSucceeded = this._clearTrackedTimer(timeoutKey, undefined, timeoutSourceAlreadyRemoved) !== false;
      }
      if (!timeoutCleanupSucceeded) {
        let timerRetrySucceeded = this._retryOrphanedTimers();
        timeoutCleanupSucceeded = timerRetrySucceeded &&
          Array.isArray(this._orphanedTimers) && this._orphanedTimers.length === 0;
      }
      let cancellableCleanupSucceeded = this._unregisterCancellable(cancellableToken);
      let cancellableOrphanCleanupSucceeded = true;
      if (!cancellableCleanupSucceeded) {
        this._trackOrphanedCancellable(cancellableToken, true);
      } else {
        cancellableOrphanCleanupSucceeded = this._untrackOrphanedCancellable(cancellableToken);
      }
      let processCleanupSucceeded = this._unregisterProcess(processToken);
      let processOrphanCleanupSucceeded = true;
      if (!processCleanupSucceeded) {
        this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, true);
      } else {
        processOrphanCleanupSucceeded = this._untrackOrphanedProcess(process);
      }
      cleanupComplete = timeoutCleanupSucceeded && cancellableCleanupSucceeded && cancellableOrphanCleanupSucceeded &&
        processCleanupSucceeded && processOrphanCleanupSucceeded;
      return cleanupComplete;
    };

    let finish = (result, terminate, suppressCallback, timeoutAlreadyRemoved) => {
      if (timeoutAlreadyRemoved === true) {
        timeoutSourceAlreadyRemoved = true;
      }
      if (cleanupComplete) {
        return true;
      }
      if (done) {
        return cleanupResources();
      }
      let timeoutCleanupSucceeded = this._clearTrackedTimer(timeoutKey, undefined, timeoutSourceAlreadyRemoved) !== false;
      let terminationSucceeded = true;
      if (terminate) {
        terminationSucceeded = this._terminateProcess(process);
        if (!terminationSucceeded) {
          terminationFailed = true;
        }
      }
      let cancellationSucceeded = true;
      try {
        let cancelResult = cancellable.cancel();
        if (cancelResult === false) {
          throw new Error("Subprocess cancellation failed");
        }
      } catch (error) {
        cancellationSucceeded = false;
        this._recordLifecycleError("process-cancel", error);
      }
      if (!terminationSucceeded || !cancellationSucceeded) {
        this._trackOrphanedProcess(process, generation, options.resourceGroup, processToken, terminationSucceeded);
        this._trackOrphanedCancellable(cancellableToken, cancellationSucceeded);
        this._scheduleProcessCleanupRetry();
        if (!suppressCallback && !this.appletRemoved && this.spawnGeneration === generation &&
            typeof callback === "function" && !callbackDelivered) {
          callbackDelivered = true;
          try {
            callback("", "", { error: "Subprocess cleanup failed", cleanupFailed: true });
          } catch (error) {
            this._recordLifecycleError("process-callback", error);
          }
        }
        return false;
      }
      done = true;
      let cleanupSucceeded = cleanupResources(timeoutCleanupSucceeded);
      if (!cleanupSucceeded) {
        this._scheduleProcessCleanupRetry();
      }
      let callbackResult = cleanupSucceeded
        ? (result || {})
        : { error: "Subprocess cleanup failed", cleanupFailed: true };
      if (suppressCallback || this.appletRemoved || this.spawnGeneration !== generation || typeof callback !== "function") {
        return cleanupSucceeded;
      }
      if (!callbackDelivered) {
        callbackDelivered = true;
        try {
          let stdoutText = "";
          let stderrText = "";
          try {
            stdoutText = _decodeSubprocessOutputChunks(stdoutParts);
            stderrText = _decodeSubprocessOutputChunks(stderrParts);
          } catch (error) {
            callbackResult = {
              error: "Subprocess output is not valid UTF-8",
              cleanupFailed: !cleanupSucceeded,
            };
          }
          callback(stdoutText, stderrText, callbackResult);
        } catch (error) {
          this._recordLifecycleError("process-callback", error);
        }
      }
      return cleanupSucceeded;
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

    let startProcessWait = () => {
      if (processWaitStarted || done || !ended.stdout || !ended.stderr || inputPending) {
        return;
      }
      processWaitStarted = true;
      try {
        process.wait_check_async(cancellable, (source, result) => {
          if (done) {
            return;
          }
          try {
            let waitResult = source.wait_check_finish(result);
            if (waitResult !== true) {
              throw new Error("Subprocess exit status check failed");
            }
            processExited = true;
            processSuccessful = true;
          } catch (error) {
            processWaitError = error;
            if (terminationFailed) {
              finish({ error: error }, true);
              return;
            }
            processExited = true;
          }
          finishWhenReady();
        });
      } catch (error) {
        processWaitError = error;
        finish({ error: error }, true);
      }
    };

    let finishWhenReady = () => {
      startProcessWait();
      if (!processExited || !ended.stdout || !ended.stderr || inputPending) {
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
        setupFailed = true;
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
            let chunk = new Uint8Array(data || []);
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
            setupFailed = true;
            finish({ error: error }, true);
          }
        });
      } catch (error) {
        setupFailed = true;
        finish({ error: error }, true);
      }
    };

    if (!done && !setupFailed && timeoutMs > 0 && !this._scheduleTrackedTimer(timeoutKey, Math.max(minimumTimeoutMs, timeoutMs), () => {
      finish({ timedOut: true }, true, false, true);
      return false;
    }, false)) {
      finish({ error: "Subprocess timeout could not be scheduled" }, true);
      return null;
    }

    if (!process.wait_check_async || !process.wait_check_finish) {
      setupFailed = true;
      finish({ error: "Subprocess exit status API unavailable" }, true);
    }

    try {
      readStream(process.get_stdout_pipe(), "stdout", maxStdoutBytes, stdoutParts);
      readStream(process.get_stderr_pipe(), "stderr", maxStderrBytes, stderrParts);
    } catch (error) {
      setupFailed = true;
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
            if (done) {
              return;
            }
            try {
              assertInputWriteSucceeded(stream.write_all_finish(result));
              closeInput(stream);
              inputPending = false;
              finishWhenReady();
            } catch (error) {
              inputPending = false;
              setupFailed = true;
              finish({ error: error }, true);
            }
          });
        } else if (stdin && stdin.write_all) {
          assertInputWriteSucceeded(stdin.write_all(inputBytes, null));
          closeInput(stdin);
          inputPending = false;
          finishWhenReady();
        } else {
          inputPending = false;
          setupFailed = true;
          finish({ error: "Subprocess input stream unavailable" }, true);
        }
      } catch (error) {
        inputPending = false;
        setupFailed = true;
        finish({ error: error }, true);
      }
    }
    if (!setupFailed && !done) {
      finishWhenReady();
    }
    if (setupFailed || done) {
      return null;
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
      completeOnce("", { error: "Subprocess could not be started", startupFailed: true }, "");
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
      if (options.invalidatesStatus !== false &&
          !this._isStatusCommandArgs(normalizedArgs)) {
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
          if (result && result.cleanupFailed) {
            callbackFn({ status: "error", error: "Backend command cleanup failed", transport_error: true });
            return;
          }
          let output = String(stdout || "");
          let parsedPayload = null;
          if (output.trim() !== "") {
            try {
              parsedPayload = this._parseSpawnOutput(output);
            } catch (error) {
              parsedPayload = null;
            }
          }
          if (result && result.timedOut) {
            callbackFn({ status: "error", error: "Backend command timed out", transport_error: true });
            return;
          }
          if (result && result.outputTooLarge) {
            callbackFn({ status: "error", error: "Backend response is too large", transport_error: true });
            return;
          }
          if (result && result.error) {
            if (parsedPayload && parsedPayload.transport_error !== true) {
              callbackFn(parsedPayload);
              return;
            }
            callbackFn({ status: "error", error: "Backend command failed", transport_error: true });
            return;
          }
          if (!parsedPayload) {
            callbackFn({ status: "error", error: "Backend returned no response", transport_error: true });
            return;
          }
          // callbackFn(this._parseSpawnOutput(stdout));
          callbackFn(parsedPayload);
        }, inputText, {
          timeoutMs: timeoutMs,
          maxStdoutBytes: MAX_SPAWN_JSON_BYTES,
          maxStderrBytes: MAX_SPAWN_STDERR_BYTES,
          resourceGroup: options.resourceGroup,
        });
      });
    } catch (error) {
      this._recordLifecycleError("backend-json-spawn", error);
      callbackFn({ status: "error", error: this._lifecycleErrorText(error), transport_error: true });
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
          callbackFn("", result || {});
          return;
        }
        let output = String(stdout || "");
        callbackFn(utf8ByteLength(output) > MAX_SPAWN_TEXT_BYTES ? "" : output, result || {});
      }, null, {
        timeoutMs: timeoutMs,
        maxStdoutBytes: MAX_SPAWN_TEXT_BYTES,
        maxStderrBytes: MAX_SPAWN_STDERR_BYTES,
        resourceGroup: options.resourceGroup,
      });
    } catch (error) {
      this._recordLifecycleError("backend-text-spawn", error);
      callbackFn("", { error: "Subprocess could not be started", startupFailed: true });
      return null;
    }
  },

  _applyPayloadSafely: function(payload, statusRefreshToken) {
    try {
      if (arguments.length > 2) {
        this._applyPayload(payload, statusRefreshToken, arguments[2]);
      } else {
        this._applyPayload(payload, statusRefreshToken);
      }
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatusPreservingRecording("error", _("Backend response handling failed: ") + safeError, this.lastTranscript);
      if (!this.isCommandRunning && (this.status === "recording" || this.status === "processing")) {
        this._scheduleStatusPoll();
      }
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
    this._updateRecordingArtifactState(payload, status);
    if (payload.error || status === "error") {
      let errorMessage = this._payloadErrorMessage(payload, _("Backend reported an error"));
      let preserveRecordingOnError = arguments.length > 2 && arguments[2] === true;
      let activeBackendStatus = status === "recording" || status === "recorded" || status === "processing";
      let preserveActiveRecordingState = (
        payload.transport_error === true &&
        (preserveRecordingOnError || (typeof statusRefreshToken === "number" && this._hasActiveRecordingState()))
      ) || (preserveRecordingOnError && activeBackendStatus);
      if (preserveActiveRecordingState) {
        if (preserveRecordingOnError) {
          this.recordingArtifactsPresent = true;
        }
        this.microphoneLevel = null;
        this._setStatusPreservingRecording("error", errorMessage, this.lastTranscript);
        this._scheduleStatusPoll();
      } else {
        this._applyPayloadLanguage(payload, status);
        this._updateRecordingTiming(payload, status);
        this._applyMicrophoneLevel(payload.microphone_level, status);
        this.cancelPendingWhileCommandRunning = false;
        this.autoTranscribeRecordingKey = "";
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this._setStatus("error", errorMessage, this.lastTranscript);
      }
      this._maybeWarnRejectedArtifactPassphrase(errorMessage);
      return;
    }
    this._applyPayloadLanguage(payload, status);
    this._updateRecordingTiming(payload, status);
    this._applyMicrophoneLevel(payload.microphone_level, status);
    let hasTranscript = typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript);
    if (status === "done" && hasTranscript) {
      this.lastTranscript = payload.transcript;
    }
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
      this._toggleRecording("stop");
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
    if ((!this.autoTranscribeTimeout && !this.autoRelisten) || !this.notificationSessionActive ||
        this.isCommandRunning || this._hasLocalProcessingWorkflow(false)) {
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
    let recordingCommandToken = { action: "stop" };
    this._recordingCommandToken = recordingCommandToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Transcribing timed-out recording..."), this.lastTranscript);
    this._spawnJson(stopArgs, (nextPayload) => {
      if (this._recordingCommandToken !== recordingCommandToken || !this._lifecycleAllowsWork()) {
        return;
      }
      this._recordingCommandToken = null;
      if (relistenToken && this.autoRelistenPendingToken !== relistenToken) {
        this.isCommandRunning = false;
        if (this.cancelPendingWhileCommandRunning) {
          this._applyPayloadSafely(nextPayload, undefined, true);
        }
        return;
      }
      if (nextPayload && nextPayload.error) {
        this.autoTranscribeRecordingKey = "";
      }
      this.isCommandRunning = false;
      this._applyPayloadSafely(nextPayload, undefined, true);
    });
  },

  _clearStatusTimer: function() {
    return this._clearTrackedTimer("status", "statusTimer");
  },

  _clearDisplayTimer: function() {
    return this._clearTrackedTimer("display", "displayTimer");
  },

  _clearSetupCheckTimer: function() {
    return this._clearTrackedTimer("setup", "setupCheckTimer");
  },

  _clearPasteTimer: function() {
    return this._clearTrackedTimer("paste", "pasteTimer");
  },

  _clearAlarmTimer: function() {
    return this._clearTrackedTimer("alarm", "alarmTimer");
  },

  _clearOllamaInstallWatchTimer: function() {
    return this._clearTrackedTimer("ollama-install", "ollamaInstallWatchTimer");
  },

  _cancelOllamaInstallWatch: function() {
    this.ollamaInstallWatchToken = null;
    return this._clearOllamaInstallWatchTimer();
  },

  _watchOllamaInstallThenChoose: function() {
    if (this._cancelOllamaInstallWatch() === false) {
      this._clearOllamaModelFlow();
      this._setStatus("error", _("Ollama operation could not be stopped"), this.lastTranscript);
      return false;
    }
    let watchToken = {};
    this.ollamaInstallWatchToken = watchToken;
    this.ollamaInstallWatchPolls = 0;
    this._setStatus("processing", _("Waiting for Ollama installation..."), this.lastTranscript);
    return this._scheduleOllamaInstallWatchPoll(watchToken) === true;
  },

  _scheduleOllamaInstallWatchPoll: function(watchToken) {
    if (!watchToken || this.ollamaInstallWatchToken !== watchToken || !this._lifecycleAllowsWork()) {
      return false;
    }
    let timerId = this._scheduleTrackedTimer("ollama-install", OLLAMA_INSTALL_POLL_SECONDS, () => {
      if (this.ollamaInstallWatchToken !== watchToken || !this._lifecycleAllowsWork()) {
        return false;
      }
      this.ollamaInstallWatchPolls++;
      let textModelArgs = this._tryTextModelsArgs("ollama");
      if (!textModelArgs) {
        this.ollamaInstallWatchToken = null;
        if (!this._clearOllamaModelFlowOrReport()) {
          return false;
        }
        this._setStatus("error", _("Could not continue Ollama installation watch"), this.lastTranscript);
        return false;
      }
      this._spawnJson(textModelArgs, (payload) => {
        if (this.ollamaInstallWatchToken !== watchToken || !this._lifecycleAllowsWork()) {
          return;
        }
        try {
          if (payload.error) {
            this.ollamaInstallWatchToken = null;
            let safeError = typeof payload.error === "string" && payload.error.trim() !== ""
              ? this._sanitizeErrorMessage(payload.error)
              : _("Ollama status check failed");
            if (!this._clearOllamaModelFlowOrReport()) {
              return;
            }
            this._setStatus("error", safeError, this.lastTranscript);
            return;
          }
          if (!this._isValidOllamaCatalogPayload(payload)) {
            this.ollamaInstallWatchToken = null;
            if (!this._clearOllamaModelFlowOrReport()) {
              return;
            }
            this._setStatus("error", _("Ollama status response was invalid"), this.lastTranscript);
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
            if (!this._clearOllamaModelFlowOrReport()) {
              return;
            }
            this._setStatus("error", _("Ollama installation did not become reachable"), this.lastTranscript);
            this._notify(_("Ollama is not reachable"), _("Install finished or was cancelled, but 127.0.0.1:11434 is still unavailable."), true);
            return;
          }
          this._scheduleOllamaInstallWatchPoll(watchToken);
        } catch (err) {
          this.ollamaInstallWatchToken = null;
          let safeError = this._sanitizeErrorMessage(err);
          if (!this._clearOllamaModelFlowOrReport()) {
            return;
          }
          this._setStatus("error", _("Ollama status check failed: ") + safeError, this.lastTranscript);
        }
      }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS, resourceGroup: "ollama" });
      return false;
    }, true, "ollamaInstallWatchTimer");
    if (!timerId && this.ollamaInstallWatchToken === watchToken) {
      this.ollamaInstallWatchToken = null;
      if (!this._clearOllamaModelFlowOrReport()) {
        return false;
      }
      this._setStatus("error", _("Ollama installation watch could not be scheduled"), this.lastTranscript);
      return false;
    }
    return Boolean(timerId);
  },

  _scheduleSetupCheck: function() {
    this._clearSetupCheckTimer();
    if (this.appletRemoved) {
      return;
    }
    let timerId = this._scheduleTrackedTimer("setup", 2, () => {
      let setupBusy = this._statusCommandRunning || this.isCommandRunning ||
        this.alarmCheckToken || this.alarmActionToken || this.alarmMenuRefreshToken ||
        this._hasLocalProcessingWorkflow();
      if (setupBusy || this._hasActiveRecordingState()) {
        return true;
      }
      this._runDoctor(true);
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
    if (this.appletRemoved) {
      return;
    }
    if (this.status !== "recording" && this.status !== "processing") {
      this._clearStatusTimer();
      return;
    }
    if (this.statusTimer) {
      return;
    }
    let timerId = this._scheduleTrackedTimer("status", 2, () => {
      let statusRefreshContinues = this._refreshStatus(true) === true;
      return statusRefreshContinues || (
        !this._statusCommandRunning &&
        (this.status === "recording" || this.status === "processing")
      );
    }, true, "statusTimer");
    if (!timerId && (this.status === "recording" || this.status === "processing")) {
      this._setStatusPreservingRecording("error", _("Status polling timer could not be scheduled"), this.lastTranscript);
    }
  },

  _scheduleDisplayTick: function() {
    if (this.appletRemoved) {
      return;
    }
    if (this.status !== "recording") {
      this._clearDisplayTimer();
      return;
    }
    if (this.displayTimer) {
      return;
    }
    let timerId = this._scheduleTrackedTimer("display", 1, () => {
      if (this.status === "recording") {
        this._updateRecordingDisplay();
        return !this.appletRemoved;
      }
      return false;
    }, true, "displayTimer");
    if (!timerId && this._lifecycleAllowsWork() && this.status === "recording") {
      this._setStatusPreservingRecording("error", _("Recording display timer could not be scheduled"), this.lastTranscript);
    }
  },

  _updateRecordingDisplay: function() {
    return this._runGuarded("recording-display-update", () => {
      if (this.appletRemoved || this.status !== "recording") {
        this._recordingDisplayFingerprint = null;
        return false;
      }
      let progressText = this._recordingProgressText();
      let microphoneText = this._microphoneLevelText();
      let recordingMessageText = this.lastMessage
        ? this._shortMenuText(this.lastMessage, 160)
        : "";
      let panelLabel = this.showPanelLabel ? "REC " + this._formatSeconds(this._recordingElapsedSeconds()) : "";
      let tooltipText = _("Recording...") + " " + progressText + "\n" + microphoneText;
      let statusText = "recording " + progressText + "; " + microphoneText;
      if (recordingMessageText !== "") {
        tooltipText += "\n" + recordingMessageText;
        statusText += " - " + recordingMessageText;
      }
      tooltipText += "\n" + this._shortTranscript();
      let toggleText = _("Stop dictation");
      let panelActor = this.actor;
      let panelActorReady = Boolean(
        panelActor &&
        (typeof panelActor.is_finalized !== "function" || !panelActor.is_finalized()) &&
        typeof this.set_applet_label === "function" &&
        typeof this.set_applet_tooltip === "function"
      );
      let menuOpen = Boolean(this.menu && this.menu.isOpen === true);
      let previousFingerprint = this._recordingDisplayFingerprint;
      if (
        previousFingerprint &&
        previousFingerprint.panelLabel === panelLabel &&
        previousFingerprint.tooltipText === tooltipText &&
        previousFingerprint.statusText === statusText &&
        previousFingerprint.microphoneText === microphoneText &&
        previousFingerprint.toggleText === toggleText &&
        previousFingerprint.menuOpen === menuOpen &&
        previousFingerprint.panelActorReady === panelActorReady
      ) {
        return true;
      }
      if (panelActorReady) {
        this.set_applet_label(panelLabel);
        this.set_applet_tooltip(tooltipText);
      }
      let menuRenderSucceeded = true;
      if (menuOpen) {
        let labelWriteSucceeded = this._setMenuItemLabelSafely(this.statusItem, _("Status: ") + statusText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.microphoneLevelItem, microphoneText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.toggleItem, toggleText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
      }
      this._recordingDisplayFingerprint = panelActorReady && menuRenderSucceeded ? {
        panelLabel: panelLabel,
        tooltipText: tooltipText,
        statusText: statusText,
        microphoneText: microphoneText,
        toggleText: toggleText,
        menuOpen: menuOpen,
        panelActorReady: panelActorReady,
      } : null;
      return true;
    }, false);
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

  _isTargetWindowXLookupPending: function() {
    let pendingGeneration = Number(this.targetWindowXPendingGeneration || 0);
    return pendingGeneration > 0 &&
      pendingGeneration === Number(this.targetWindowGeneration || 0);
  },

  _rememberFocusedWindow: function(preserveOnFailure) {
    this.targetWindowGeneration = Number(this.targetWindowGeneration || 0) + 1;
    let targetGeneration = this.targetWindowGeneration;
    this.targetWindowXPendingGeneration = 0;
    let processCleanupSucceeded = true;
    for (let group of ["keyboard", "x11", "clipboard"]) {
      if (this._terminateProcessesByGroup(group, true) === false) {
        processCleanupSucceeded = false;
      }
    }
    if (!processCleanupSucceeded) {
      this.textInsertCancellationFailed = true;
      this.targetWindow = null;
      this._clearTargetWindowXid();
      this._setStatusPreservingRecording("error", _("Previous text insertion could not be stopped"), this.lastTranscript);
      return false;
    }
    let window = global.display ? global.display.focus_window : null;
    if (this._isUsableTargetWindow(window)) {
      this.targetWindow = window;
      this._clearTargetWindowXid();
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
    this.targetWindowXPendingGeneration = targetGeneration;
    this._rememberActiveXWindow((remembered) => {
      if (
        targetGeneration !== this.targetWindowGeneration ||
        targetGeneration !== Number(this.targetWindowXPendingGeneration || 0)
      ) {
        return;
      }
      this.targetWindowXPendingGeneration = 0;
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
    if (!this._lifecycleAllowsWork()) {
      complete(false);
      return false;
    }
    if (this._isUsableTargetWindow(this.targetWindow)) {
      let activated = false;
      try {
        let result = Main.activateWindow(this.targetWindow, global.get_current_time());
        if (result === false) {
          throw new Error("Target window could not be activated");
        }
        activated = true;
      } catch (err) {
        this._recordLifecycleError("x11-focus", err);
      }
      if (activated) {
        let callbackDelivered = false;
        this._runStateGuarded("x11-focus-callback", () => {
          callbackDelivered = true;
          complete(true);
        }, undefined);
        if (!callbackDelivered) {
          complete(false);
        }
        return callbackDelivered;
      }
    }
    return this._activateTargetXWindow(complete);
  },

  _closeMenuForKeyboardInsert: function() {
    try {
      if (this.menu) {
        this._closeMenuSafely(this.menu, false, true);
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

  _xdotoolOutput: function(args, maxBytes, completionCallback, timeoutMs, trustedProgramResolver) {
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
      let resolveTrustedProgram = typeof trustedProgramResolver === "function"
        ? trustedProgramResolver
        : (name) => this._findTrustedProgramInPath(name);
      timeout = resolveTrustedProgram("timeout");
      xdotool = resolveTrustedProgram("xdotool");
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
    let callback = typeof completionCallback === "function" ? completionCallback : function() {};
    let complete = (result) => {
      try {
        callback(result);
      } catch (error) {
        this._recordLifecycleError("x11-focus-completion", error);
      }
    };
    let targetGeneration = expectedGeneration === undefined
      ? Number(this.targetWindowGeneration || 0)
      : Number(expectedGeneration);
    let isCurrent = () =>
      targetGeneration === Number(this.targetWindowGeneration || 0) &&
      targetGeneration === Number(this.targetWindowXPendingGeneration || 0) &&
      this._lifecycleAllowsWork();
    let deadlineMs = Date.now() + X11_COMMAND_TIMEOUT_MS;
    let trustedPrograms = Object.create(null);
    let resolveTrustedProgram = (name) => {
      if (name !== "timeout" && name !== "xdotool") {
        return null;
      }
      if (Object.prototype.hasOwnProperty.call(trustedPrograms, name)) {
        return trustedPrograms[name];
      }
      let trustedProgram = this._findTrustedProgramInPath(name);
      if (trustedProgram) {
        trustedPrograms[name] = trustedProgram;
      }
      return trustedProgram;
    };
    this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (activeOutput) => {
      if (!isCurrent()) {
        complete(false);
        return;
      }
      let xid = String(activeOutput || "").trim();
      if (!/^[0-9]+$/.test(xid)) {
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
            complete(false);
            return;
          }
          this.targetWindowXid = xid;
          this.targetWindowXTitle = this._shortMenuText(title, 160);
          this.targetWindowXClass = this._shortMenuText(windowClass, 160);
          complete(true);
        }, Math.max(1, deadlineMs - Date.now()), resolveTrustedProgram);
      }, Math.max(1, deadlineMs - Date.now()), resolveTrustedProgram);
    }, Math.max(1, deadlineMs - Date.now()), resolveTrustedProgram);
    return true;
  },

  _activateTargetXWindow: function(completionCallback) {
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    if (this._isTargetWindowXLookupPending()) {
      complete(false);
      return false;
    }
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
    let xid = this._isTargetWindowXLookupPending()
      ? ""
      : String(this.targetWindowXid || "").trim();
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
    let expectedClass = String(snapshot.windowClass || "").trim().toLowerCase();
    let expectedTitle = String(snapshot.windowTitle || "").trim().toLowerCase();
    if (expectedClass === "" && expectedTitle === "") {
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
    this._xdotoolOutput(["getwindowname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES, (titleOutput) => {
      if (!generationMatches()) {
        complete(false);
        return;
      }
      let activeTitle = this._shortMenuText(String(titleOutput || "").trim(), 160).toLowerCase();
      if (this._xWindowLooksLikeSpeedOfCinnamon(activeTitle, snapshot.windowClass)) {
        this._notifySelfProtectionBlocked(activeTitle, snapshot.windowClass);
        complete(false);
        return;
      }
      if (expectedTitle === "") {
        complete(true);
        return;
      }
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
    let activeInsertToken = this.textInsertToken;
    let recordingKey = String(this.autoInsertPendingFingerprint || this.autoInsertFingerprint || "");
    let now = Date.now();
    if (activeInsertToken) {
      if (activeInsertToken.selfProtectionNoticeShown === true) {
        return;
      }
      activeInsertToken.selfProtectionNoticeShown = true;
    }
    let key = recordingKey !== ""
      ? "self-protection\nrecording\n" + recordingKey
      : [
        "self-protection",
        "window",
        String(this.targetWindowGeneration || 0),
        String(windowClass || ""),
        String(title || "")
      ].join("\n");
    if (now - this.selfProtectionNoticeAtMs < SELF_PROTECTION_NOTICE_COOLDOWN_MS) {
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
    let xTargetAvailable = !this._isTargetWindowXLookupPending();
    let values = [
      this._windowProbeValue(this.targetWindow, "get_wm_class"),
      this._windowProbeValue(this.targetWindow, "get_wm_class_instance"),
      this._windowProbeValue(this.targetWindow, "get_gtk_application_id"),
      xTargetAvailable ? String(this.targetWindowXClass || "").toLowerCase() : "",
      xTargetAvailable ? String(this.targetWindowXTitle || "").toLowerCase() : ""
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
    let targetWindowUsable = this._isUsableTargetWindow(this.targetWindow);
    let xTargetAvailable = !this._isTargetWindowXLookupPending();
    if (!targetWindowUsable && (!xTargetAvailable || (!this.targetWindowXClass && !this.targetWindowXTitle))) {
      return false;
    }
    let values = [
      this._windowProbeValue(this.targetWindow, "get_wm_class"),
      this._windowProbeValue(this.targetWindow, "get_wm_class_instance"),
      this._windowProbeValue(this.targetWindow, "get_gtk_application_id"),
      xTargetAvailable ? String(this.targetWindowXClass || "").toLowerCase() : "",
      xTargetAvailable ? String(this.targetWindowXTitle || "").toLowerCase() : ""
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

  _clipboardProgramSpecs: function() {
    let specs = [];
    if (this._findTrustedProgramInPath("xclip")) {
      specs.push({
        program: "xclip",
        targetArgs: ["-selection", "clipboard", "-t", "TARGETS", "-out"],
      });
    }
    if (this._findTrustedProgramInPath("xsel")) {
      specs.push({
        program: "xsel",
        targetArgs: ["--clipboard", "--output", "--target", "TARGETS"],
      });
    }
    if (this._findTrustedProgramInPath("wl-paste")) {
      specs.push({
        program: "wl-paste",
        targetArgs: ["--list-types"],
      });
    }
    return specs;
  },

  _clipboardProgramSpec: function() {
    let specs = this._clipboardProgramSpecs();
    return specs.length > 0 ? specs[0] : null;
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

  _clipboardFallbackSpec: function(program, args, attemptedPrograms) {
    let attempted = Array.isArray(attemptedPrograms) ? attemptedPrograms.slice() : [];
    if (attempted.indexOf(program) < 0) {
      attempted.push(program);
    }
    let targetName = null;
    let targetList = false;
    args = Array.isArray(args) ? args : [];
    if (program === "xclip") {
      let targetIndex = args.indexOf("-t");
      if (targetIndex < 0 || typeof args[targetIndex + 1] !== "string") {
        return null;
      }
      targetName = args[targetIndex + 1];
      targetList = targetName === "TARGETS";
    } else if (program === "xsel") {
      let targetIndex = args.indexOf("--target");
      if (targetIndex < 0 || typeof args[targetIndex + 1] !== "string") {
        return null;
      }
      targetName = args[targetIndex + 1];
      targetList = targetName === "TARGETS";
    } else if (program === "wl-paste") {
      targetList = args.indexOf("--list-types") >= 0;
      if (!targetList) {
        let targetIndex = args.indexOf("--type");
        if (targetIndex < 0 || typeof args[targetIndex + 1] !== "string") {
          return null;
        }
        targetName = args[targetIndex + 1];
      }
    } else {
      return null;
    }
    let specs = this._clipboardProgramSpecs();
    for (let i = 0; i < specs.length; i++) {
      let spec = specs[i];
      if (!spec || attempted.indexOf(spec.program) >= 0) {
        continue;
      }
      let fallbackArgs = targetList
        ? spec.targetArgs
        : this._clipboardPayloadArgs(spec, targetName);
      if (!Array.isArray(fallbackArgs)) {
        continue;
      }
      return {
        program: spec.program,
        args: fallbackArgs,
        attemptedPrograms: attempted,
      };
    }
    return null;
  },

  _clipboardTargetList: function(program, args, completionCallback, timeoutMs, attemptedPrograms, deadlineMs) {
    args = args || [];
    let timeout = this._findTrustedProgramInPath("timeout");
    let helper = this._findTrustedProgramInPath(program);
    let complete = typeof completionCallback === "function" ? completionCallback : function() {};
    let completed = false;
    let completeOnce = (value, resolvedProgram) => {
      if (completed) {
        return;
      }
      completed = true;
      complete(value, resolvedProgram);
    };
    let subprocessCallbackDelivered = false;
    let fallbackStarted = false;
    let commandTimeoutMs = Math.max(1, Number(timeoutMs || CLIPBOARD_COMMAND_TIMEOUT_MS));
    if (!isFinite(commandTimeoutMs)) {
      commandTimeoutMs = CLIPBOARD_COMMAND_TIMEOUT_MS;
    }
    let commandDeadlineMs = Number(deadlineMs);
    if (!isFinite(commandDeadlineMs) || commandDeadlineMs <= 0) {
      commandDeadlineMs = Date.now() + commandTimeoutMs;
    }
    let attempted = Array.isArray(attemptedPrograms) ? attemptedPrograms.slice() : [];
    if (attempted.indexOf(program) < 0) {
      attempted.push(program);
    }
    let tryFallback = () => {
      if (completed) {
        return fallbackStarted;
      }
      if (fallbackStarted) {
        return true;
      }
      let remainingMs = commandDeadlineMs - Date.now();
      if (remainingMs <= 0 || !this._lifecycleAllowsWork()) {
        return false;
      }
      try {
        let fallback = this._clipboardFallbackSpec(program, args, attempted);
        if (!fallback) {
          return false;
        }
        let fallbackHandle = this._clipboardTargetList(
          fallback.program,
          fallback.args,
          completeOnce,
          Math.max(1, remainingMs),
          fallback.attemptedPrograms,
          commandDeadlineMs
        );
        fallbackStarted = Boolean(fallbackHandle);
        return fallbackStarted;
      } catch (error) {
        this._recordLifecycleError("clipboard-command-fallback", error);
        return false;
      }
    };
    if (!timeout || !this._lifecycleAllowsWork()) {
      completeOnce(null);
      return false;
    }
    if (!helper) {
      if (tryFallback()) {
        return true;
      }
      completeOnce(null);
      return false;
    }
    let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), helper];
    for (let i = 0; i < args.length; i++) {
      command.push(args[i]);
    }
    try {
      let handle = this._runBoundedSubprocess(this._coerceSpawnArgs(command), {}, {
        timeoutMs: Math.max(1, Math.min(commandTimeoutMs, Math.max(1, commandDeadlineMs - Date.now()))),
        minimumTimeoutMs: 1,
        maxStdoutBytes: MAX_CLIPBOARD_TARGET_OUTPUT_BYTES,
        maxStderrBytes: MAX_XDOTOOL_TARGET_OUTPUT_BYTES,
        resourceGroup: "clipboard",
      }, (stdout, stderr, result) => {
        subprocessCallbackDelivered = true;
        if (result && result.cancelled) {
          completeOnce(null);
          return;
        }
        if (result && (result.error || result.timedOut || result.outputTooLarge)) {
          if (tryFallback()) {
            return;
          }
          completeOnce(null);
          return;
        }
        completeOnce(String(stdout || ""), program);
      });
      if (!handle && !subprocessCallbackDelivered) {
        if (tryFallback()) {
          return true;
        }
        completeOnce(null);
      }
      return Boolean(handle) || fallbackStarted;
    } catch (error) {
      this._recordLifecycleError("clipboard-command", error);
      if (tryFallback()) {
        return true;
      }
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
      return [];
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
    let targetDeadlineMs = Date.now() + CLIPBOARD_COMMAND_TIMEOUT_MS;
    try {
      this._clipboardTargetList(spec.program, spec.targetArgs, (targets, resolvedProgram) => {
        try {
          if (targets === null || targets === undefined) {
            unknown();
            return;
          }
          let resolvedSpec = spec;
          if (typeof resolvedProgram === "string" && resolvedProgram !== spec.program) {
            let availableSpecs = this._clipboardProgramSpecs();
            for (let i = 0; i < availableSpecs.length; i++) {
              if (availableSpecs[i] && availableSpecs[i].program === resolvedProgram) {
                resolvedSpec = availableSpecs[i];
                break;
              }
            }
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
          let fingerprintBudgetMs = Math.min(
            CLIPBOARD_PAYLOAD_FINGERPRINT_MAX_BUDGET_MS,
            CLIPBOARD_COMMAND_TIMEOUT_MS * Math.max(1, nonTextTargets.length)
          );
          let fingerprintDeadlineMs = Date.now() + fingerprintBudgetMs;
          this._clipboardPayloadFingerprintFromTargetsAsync(resolvedSpec, nonTextTargets, (payloadFingerprint) => {
            try {
              if (payloadFingerprint === "unknown") {
                unknown();
                return;
              }
              complete({
                signature: targetText,
                hasNonTextPayload: nonTextTargets.length > 0,
                payloadFingerprint: payloadFingerprint,
                description: this._clipboardPayloadDescriptionFromTargets(nonTextTargets),
              });
            } catch (error) {
              this._recordLifecycleError("clipboard-query", error);
              unknown();
            }
          }, fingerprintDeadlineMs);
        } catch (error) {
          this._recordLifecycleError("clipboard-query", error);
          unknown();
        }
      }, Math.max(1, targetDeadlineMs - Date.now()));
    } catch (error) {
      this._recordLifecycleError("clipboard-query", error);
      unknown();
      return false;
    }
    return true;
  },

  _clipboardPayloadFingerprintFromTargetsAsync: function(spec, nonTextTargets, completionCallback, deadlineMs) {
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
    if (!Array.isArray(nonTextTargets)) {
      fail(new Error("Clipboard targets are invalid"));
      return;
    }
    if (nonTextTargets.length === 0) {
      complete("no-nontext");
      return;
    }
    let fingerprints = [];
    let fingerprintDeadlineMs = Number(deadlineMs);
    if (!isFinite(fingerprintDeadlineMs)) {
      fingerprintDeadlineMs = Date.now() + CLIPBOARD_COMMAND_TIMEOUT_MS;
    }
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
        if (Date.now() >= fingerprintDeadlineMs) {
          complete("unknown");
          return;
        }
        let remainingBudgetMs = Math.max(1, Math.floor(fingerprintDeadlineMs - Date.now()));
        let targetName = String(sortedTargets[index] || "");
        this._clipboardTargetList(spec.program, this._clipboardPayloadArgs(spec, targetName), (payload) => {
          try {
            if (payload === null || payload === undefined) {
              complete("unknown");
              return;
            }
            let fingerprint = this._clipboardPayloadFingerprintFromText(String(payload), targetName);
            if (fingerprint === "unknown") {
              complete("unknown");
              return;
            }
            fingerprints.push(fingerprint);
            readNext(index + 1);
          } catch (error) {
            fail(error);
          }
        }, Math.max(1, Math.min(remainingBudgetMs, CLIPBOARD_COMMAND_TIMEOUT_MS)));
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
      let digest = GLib.compute_checksum_for_string(GLib.ChecksumType.SHA256, data, -1);
      if (typeof digest !== "string" || digest.trim() === "") {
        return "unknown";
      }
      return String(targetLabel || "") + ":sha256:" + digest;
    } catch (err) {
      return "unknown";
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
    let signature = snapshot && typeof snapshot.signature === "string" ? snapshot.signature.trim() : "";
    let payloadFingerprint = snapshot && typeof snapshot.payloadFingerprint === "string" ? snapshot.payloadFingerprint.trim() : "";
    if (signature === "" || payloadFingerprint === "" || signature === "unknown" || payloadFingerprint === "unknown") {
      return;
    }
    this._clipboardOverwriteApproval = {
      signature: signature,
      payloadFingerprint: payloadFingerprint,
      expiresAtMs: Date.now() + CLIPBOARD_OVERWRITE_APPROVAL_TTL_MS,
    };
  },

  _hasValidClipboardOverwriteApproval: function(snapshot) {
    let approval = this._clipboardOverwriteApproval;
    if (!approval) {
      return false;
    }
    let expiresAtMs = Number(approval.expiresAtMs);
    if (!isFinite(expiresAtMs) || Date.now() > expiresAtMs) {
      this._clearClipboardOverwriteApproval();
      return false;
    }
    let approvalSignature = typeof approval.signature === "string" ? approval.signature.trim() : "";
    let approvalPayloadFingerprint = typeof approval.payloadFingerprint === "string" ? approval.payloadFingerprint.trim() : "";
    let snapshotSignature = snapshot && typeof snapshot.signature === "string" ? snapshot.signature.trim() : "";
    let snapshotPayloadFingerprint = snapshot && typeof snapshot.payloadFingerprint === "string" ? snapshot.payloadFingerprint.trim() : "";
    if (approvalSignature === "" || approvalPayloadFingerprint === "" ||
      snapshotSignature === "" || snapshotPayloadFingerprint === "" ||
      approvalSignature === "unknown" || approvalPayloadFingerprint === "unknown" ||
      snapshotSignature === "unknown" || snapshotPayloadFingerprint === "unknown") {
      return false;
    }
    return (
      approvalSignature === snapshotSignature &&
      approvalPayloadFingerprint === snapshotPayloadFingerprint
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
      let result = this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
      if (result === false) {
        throw new Error("Clipboard text could not be set");
      }
      this._clearClipboardOverwriteApproval();
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

  _clipboardPayloadDescriptionFromTargets: function(nonTextTargets) {
    if (!Array.isArray(nonTextTargets) || nonTextTargets.length === 0) {
      return _("text");
    }
    let description = nonTextTargets.slice(0, 6).join(", ");
    if (nonTextTargets.length > 6) {
      description += ", +" + String(nonTextTargets.length - 6);
    }
    return this._shortMenuText(description, 160);
  },

  _copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback, operationGuard, expectedClipboardSnapshot) {
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
    let writeClipboardAndPaste = (restored) => {
      try {
        if (!isCurrentOperation()) {
          completeOnce(false);
          return;
        }
        if (!restored) {
          if (this._setClipboardText(text)) {
            this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);
          } else {
            this._setStatus("error", _("Could not copy to clipboard"), transcript);
          }
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
          let pasteCompleted = false;
          try {
            pasteCompleted = completed === true && isCurrentOperation();
            if (pasteCompleted && completed) {
              this._setStatus("done", _("Copied and pasted into target window"), transcript);
            }
          } catch (error) {
            pasteCompleted = false;
            this._recordLifecycleError("keyboard-insert-status", error);
          }
          completeOnce(pasteCompleted);
        }, isCurrentOperation)) {
          this._setStatus("error", _("Copied to clipboard; automatic paste command could not be started"), transcript);
          completeOnce(false);
        }
      } catch (error) {
        this._completeKeyboardInsertFailure(completeOnce, _("Keyboard insert failed"), error);
      }
    };
    this._restoreTargetWindowForPaste((restored) => {
      try {
        if (!isCurrentOperation()) {
          completeOnce(false);
          return;
        }
        if (!expectedClipboardSnapshot) {
          writeClipboardAndPaste(restored);
          return;
        }
        this._clipboardPayloadSnapshotAsync((currentClipboardSnapshot) => {
          try {
            if (!isCurrentOperation()) {
              completeOnce(false);
              return;
            }
            if (!this._clipboardPayloadSignaturesMatch(expectedClipboardSnapshot, currentClipboardSnapshot)) {
              this._clearClipboardOverwriteApproval();
              this._setStatus("ready", _("Clipboard changed; overwrite cancelled"), transcript);
              completeOnce(false);
              return;
            }
            writeClipboardAndPaste(restored);
          } catch (error) {
            this._completeKeyboardInsertFailure(completeOnce, _("Keyboard insert failed"), error);
          }
        });
      } catch (error) {
        this._completeKeyboardInsertFailure(completeOnce, _("Keyboard insert failed"), error);
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
    this.clipboardOverwriteDialog = dialog;
    let completed = false;
    let complete = (result) => {
      if (completed) {
        return;
      }
      completed = true;
      if (typeof completionCallback === "function") {
        try {
          completionCallback(result === true);
        } catch (error) {
          this._recordLifecycleError("keyboard-insert-completion", error);
        }
      }
    };
    let continueWithApprovedSnapshot = (approvedSnapshot) => {
      this._setClipboardOverwriteApproval(approvedSnapshot);
      let result = this._copyAndMaybePasteTranscriptText(
        transcript,
        text,
        method,
        canPasteWithKeyboard,
        submitWithReturn,
        complete,
        operationGuard,
        approvedSnapshot
      );
      if (result !== null) {
        complete(result);
      }
    };
    let failToOpen = () => {
      let closed = this._dialogClose(dialog, "clipboard-overwrite");
      if (closed) {
        this.clipboardOverwriteDialog = null;
      } else {
        this.textInsertCancellationFailed = true;
      }
      this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);
      complete(false);
    };
    if (!dialog || !this._dialogAddChild(dialog, this._newSafeLabel(message, { x_expand: true }, "clipboard-overwrite"), "clipboard-overwrite") ||
      !this._dialogAddChild(dialog, this._newSafeLabel(_("Replace clipboard content and continue paste?"), { x_expand: true }, "clipboard-overwrite"), "clipboard-overwrite")) {
      failToOpen();
      return;
    }
    if (!this._dialogSetButtons(dialog, [
      {
        label: _("Cancel"),
        key: Clutter.KEY_Escape,
        action: function() {
          if (this.clipboardOverwriteDialog !== dialog) {
            complete(false);
            return;
          }
          if (!this._dialogClose(dialog, "clipboard-overwrite")) {
            this.textInsertCancellationFailed = true;
            this._setStatus("error", _("Clipboard overwrite prompt could not be closed"), transcript);
            return;
          }
          if (this.clipboardOverwriteDialog === dialog) {
            this.clipboardOverwriteDialog = null;
          }
          if (!isCurrentOperation()) {
            complete(false);
            return;
          }
          this._setStatus("ready", _("Clipboard overwrite cancelled"), transcript);
          complete(false);
        }.bind(this),
      },
      {
        label: _("Overwrite clipboard"),
        action: function() {
          try {
            if (this.clipboardOverwriteDialog !== dialog) {
              complete(false);
              return;
            }
            if (!this._dialogClose(dialog, "clipboard-overwrite")) {
              this.textInsertCancellationFailed = true;
              this._setStatus("error", _("Clipboard overwrite prompt could not be closed"), transcript);
              return;
            }
            if (this.clipboardOverwriteDialog === dialog) {
              this.clipboardOverwriteDialog = null;
            }
            if (!isCurrentOperation()) {
              complete(false);
              return;
            }
            if (canPasteWithKeyboard) {
              continueWithApprovedSnapshot(clipboardSnapshot);
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
                continueWithApprovedSnapshot(currentClipboardSnapshot);
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
      failToOpen();
      return;
    }
    if (!this._dialogOpen(dialog, "clipboard-overwrite")) {
      failToOpen();
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
        try {
          completionCallback(result === true);
        } catch (error) {
          this._recordLifecycleError("keyboard-insert-completion", error);
        }
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
    let expected = expectedClipboardText === undefined || expectedClipboardText === null
      ? null
      : String(expectedClipboardText);
    try {
      this._spawnKeyboardArgs(
        args,
        followUpArgs,
        expectedTargetWindow,
        expected,
        expected === null ? null : deadlineMs,
        completionCallback,
        isCurrentOperation
      );
    } catch (error) {
      failAsync(error);
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
          this._completeKeyboardInsertFailure(completionCallback, _("Clipboard could not be verified before automatic paste"));
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
                  this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, expected, expectedClipboardDeadlineMs, completionCallback, isCurrentOperation);
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
        failAsync(error, _("Clipboard could not be verified before automatic paste"));
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
      try {
        if (!isCurrentOperation()) {
          fail();
          return;
        }
        if (!matches) {
          fail(_("Target window changed before automatic paste"));
          return;
        }
        if (!this._spawnKeyboardProcess(args, (firstCompleted) => {
          try {
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
              try {
                this._targetXWindowMatchesSnapshot(expectedTargetWindow, (submitTargetMatches) => {
                  try {
                    if (!isCurrentOperation()) {
                      fail();
                      return;
                    }
                    if (!submitTargetMatches) {
                      fail(_("Target window changed before automatic submit"));
                      return;
                    }
                    if (!this._windowTitleMatchesAutoPaste()) {
                      if (typeof completionCallback === "function") completionCallback(true);
                      return;
                    }
                    if (!this._spawnKeyboardProcess(followUpArgs, (submitCompleted) => {
                      try {
                        if (!isCurrentOperation()) {
                          fail();
                          return;
                        }
                        if (!submitCompleted) {
                          fail(_("Keyboard insert failed"));
                          return;
                        }
                        if (typeof completionCallback === "function") completionCallback(true);
                      } catch (error) {
                        this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
                      }
                    })) {
                      fail(_("Keyboard insert failed"));
                    }
                  } catch (error) {
                    this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
                  }
                });
              } catch (error) {
                this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
              }
              return false;
            }, false, "pasteTimer")) {
              fail(_("Keyboard insert failed: submit timer could not be scheduled"));
            }
          } catch (error) {
            this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
          }
        })) {
          fail(_("Keyboard insert failed"));
        }
      } catch (error) {
        this._completeKeyboardInsertFailure(completionCallback, _("Keyboard insert failed"), error);
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
    let clearPendingFingerprint = () => {
      if (this.autoInsertPendingFingerprint === insertFingerprint) {
        this.autoInsertPendingFingerprint = "";
      }
    };
    let releaseFingerprint = () => {
      let released = this._forgetAutoInsertFingerprint(insertFingerprint) !== false;
      if (!released) {
        this.textInsertCancellationFailed = true;
        return false;
      }
      clearPendingFingerprint();
      return true;
    };
    let inserted = false;
    if (payload.inserted === true) {
      inserted = true;
      this._setStatus("done", this._payloadMessage(payload, _("Transcript already inserted by backend")), transcript);
    } else {
      let result;
      this.autoInsertPendingFingerprint = insertFingerprint;
      try {
        result = this._insertTranscriptText(transcript, (completed) => {
          if (!completed) {
            releaseFingerprint();
            this.autoRelistenPending = false;
            this.autoRelistenPendingToken = "";
            this.autoRelistenManualStopRequested = true;
            return;
          }
          clearPendingFingerprint();
          let relistenStarted = this._finishPendingRelisten();
          if (!relistenStarted &&
              (this.status === "recording" || this.status === "recorded" || this.status === "processing") &&
              !this.isCommandRunning && !this._hasLocalProcessingWorkflow()) {
              this._setStatus("done", this._payloadMessage(payload, _("Transcript inserted")), transcript);
          }
        }, insertFingerprint);
      } catch (error) {
        this._recordLifecycleError("payload-insert", error);
        releaseFingerprint();
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
        clearPendingFingerprint();
        inserted = true;
      }
    }
    if (!inserted) {
      releaseFingerprint();
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenPendingLanguage = "";
      this.autoRelistenManualStopRequested = true;
      return;
    }
    clearPendingFingerprint();
    this._finishPendingRelisten();
  },

  _ensureAutoRelistenPendingForDonePayload: function(payload) {
    if (this.autoRelistenManualStopRequested) {
      return;
    }
    let payloadLanguage = payload && typeof payload.language === "string"
      ? payload.language.trim().toLowerCase()
      : "";
    if (LANGUAGE_CODES.indexOf(payloadLanguage) < 0) {
      payloadLanguage = "";
    }
    if (this.autoRelistenPending) {
      if (payloadLanguage !== "") {
        this.autoRelistenPendingLanguage = payloadLanguage;
      }
      return;
    }
    if (!this.autoRelisten || !this.notificationSessionActive) {
      return;
    }
    let marker = this._payloadStringMarker(payload, ["audio_path", "audio", "transcript_path", "stopped_at", "started_at"], "done");
    this.autoRelistenSequence += 1;
    this.autoRelistenPending = true;
    this.autoRelistenPendingToken = String(this.autoRelistenSequence) + ":done:" + marker;
    this.autoRelistenPendingLanguage = payloadLanguage;
  },

  _finishPendingRelisten: function() {
    let shouldRelisten = this.autoRelistenPending;
    let previousNotificationSessionActive = this.notificationSessionActive;
    let relistenStarted = false;
    let relistenFailedWithError = false;
    if (shouldRelisten) {
      this.notificationSessionActive = true;
      relistenStarted = this._restartRelistenRecording();
      relistenFailedWithError = !relistenStarted && this.status === "error";
    }
    if (relistenStarted) {
      this.notificationSessionActive = true;
    } else if (shouldRelisten) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenPendingLanguage = "";
      this.autoRelistenManualStopRequested = false;
      this.notificationSessionActive = previousNotificationSessionActive;
      if (relistenFailedWithError) {
        this.notificationSessionActive = false;
      }
    } else {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenPendingLanguage = "";
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
        if (this.autoInsertFingerprints.indexOf(fingerprint) < 0) {
          throw new Error("Auto-insert fingerprint could not be remembered");
        }
      }
      while (this.autoInsertFingerprints.length > 20) {
        let previousLength = this.autoInsertFingerprints.length;
        this.autoInsertFingerprints.shift();
        if (this.autoInsertFingerprints.length !== previousLength - 1) {
          throw new Error("Auto-insert fingerprint history could not be bounded");
        }
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
    if (!fingerprint) {
      return true;
    }
    try {
      if (!Array.isArray(this.autoInsertFingerprints)) {
        this.autoInsertFingerprints = [];
        if (this.autoInsertFingerprint === fingerprint) {
          this.autoInsertFingerprint = "";
        }
        return true;
      }
      let index = this.autoInsertFingerprints.indexOf(fingerprint);
      if (index >= 0) {
        let entry = this.autoInsertFingerprints[index];
        let removed = this.autoInsertFingerprints.splice(index, 1);
        if (!Array.isArray(removed) || removed.length !== 1 || removed[0] !== entry || this.autoInsertFingerprints.indexOf(entry) >= 0) {
          throw new Error("Auto-insert fingerprint could not be removed");
        }
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
    let hadPendingRelisten = this.autoRelistenPending;
    if (this._finishPendingRelisten()) {
      return;
    }
    if (hadPendingRelisten && this.status === "error") {
      return;
    }
    this._setStatus("done", this._payloadMessage(payload, _("Silent recording skipped")), this.lastTranscript);
  },

  _finishEmptyRelistenDone: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    let hadPendingRelisten = this.autoRelistenPending;
    if (this._finishPendingRelisten()) {
      return;
    }
    if (hadPendingRelisten && this.status === "error") {
      return;
    }
    this._setStatus("done", this._payloadMessage(payload, _("Recording finished without transcript")), this.lastTranscript);
  },

  _insertTranscriptText: function(transcript, completionCallback, protectedInsertFingerprint) {
    if (!this._lifecycleAllowsWork()) {
      return false;
    }
    if (!this.textInsertCancellationFailed && !this.textInsertToken && !this.clipboardOverwriteDialog &&
        this._hasPendingTextInsertResources()) {
      this.textInsertCancellationFailed = true;
    }
    if (this.textInsertCancellationFailed) {
      let hadInsertToken = Boolean(this.textInsertToken);
      if (hadInsertToken) {
        this.textInsertToken = null;
        if (this.autoRelistenPending) {
          this.autoRelistenPending = false;
          this.autoRelistenPendingToken = "";
          this.autoRelistenPendingLanguage = "";
          this.autoRelistenManualStopRequested = true;
        }
      }
      if (this.clipboardOverwriteDialog) {
        if (!this._dialogClose(this.clipboardOverwriteDialog, "clipboard-overwrite")) {
          this._setStatusPreservingRecording("error", _("Previous text insertion is still stopping; try again shortly"), this.lastTranscript);
          return false;
        }
        this.clipboardOverwriteDialog = null;
      }
      let timerCleanupStillPending = false;
      try {
        if (!Array.isArray(this._orphanedTimers)) {
          throw new Error("Timer orphan registry is unavailable");
        }
        if (this._orphanedTimers.length > 0) {
          let orphanCleanupSucceeded = this._retryOrphanedTimers();
          timerCleanupStillPending = !orphanCleanupSucceeded || this._orphanedTimers.length > 0;
        }
        let timers = this._resourceRegistry && this._resourceRegistry.timers;
        timerCleanupStillPending = timerCleanupStillPending || Boolean(this.pasteTimer) || Boolean(timers && timers.paste);
      } catch (error) {
        this._recordLifecycleError("timer-state", error);
        timerCleanupStillPending = true;
      }
      let protectedFingerprint = String(protectedInsertFingerprint || "");
      let fingerprintCleanupStillPending = false;
      let pendingInsertFingerprint = String(this.autoInsertPendingFingerprint || "");
      if (pendingInsertFingerprint !== "" && pendingInsertFingerprint !== protectedFingerprint) {
        let fingerprintCleanupSucceeded = this._forgetAutoInsertFingerprint(pendingInsertFingerprint) !== false;
        if (fingerprintCleanupSucceeded && this.autoInsertPendingFingerprint === pendingInsertFingerprint) {
          this.autoInsertPendingFingerprint = "";
        }
        fingerprintCleanupStillPending = !fingerprintCleanupSucceeded ||
          this.autoInsertPendingFingerprint === pendingInsertFingerprint;
      }
      let cancellationStillPending = timerCleanupStillPending || fingerprintCleanupStillPending ||
        Boolean(this.clipboardOverwriteDialog) ||
        ["keyboard", "clipboard", "x11"].some((group) => this._hasTrackedProcessGroup(group));
      if (cancellationStillPending) {
        this._setStatusPreservingRecording("error", _("Previous text insertion is still stopping; try again shortly"), this.lastTranscript);
        return false;
      }
      this.textInsertCancellationFailed = false;
    }
    if (this.textInsertToken || this.clipboardOverwriteDialog) {
      return false;
    }
    let method = this._normalizeOutputMethod(this.insertMethod);
    if (method === "none") {
      this._setStatus("done", _("Insertion disabled"), transcript);
      return true;
    }
    let autoPasteTarget = method === "clipboard-paste" && this._windowTitleMatchesAutoPaste();
    let canPasteWithKeyboard = method === "clipboard-paste" &&
      (this._findTrustedProgramInPath("xdotool") || this._findTrustedProgramInPath("wtype"));
    let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;
    let suppressAutoPasteEnter = method !== "clipboard-paste" || submitWithReturn;
    let text = this._preparedTranscriptText(transcript, suppressAutoPasteEnter, autoPasteTarget);
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
        try {
          completionCallback(result === true);
        } catch (error) {
          this._recordLifecycleError("text-insert-completion", error);
        }
      }
    };
    let failPreparation = (error, notifyCompletion) => {
      if (this.textInsertToken !== insertToken) {
        return false;
      }
      release();
      this._recordLifecycleError("text-insert", error);
      this._setStatusPreservingRecording("error", _("Could not prepare text insertion"), this.lastTranscript);
      if (notifyCompletion === true && typeof completionCallback === "function") {
        try {
          completionCallback(false);
        } catch (callbackError) {
          this._recordLifecycleError("text-insert-completion", callbackError);
        }
      }
      return false;
    };
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
            try {
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
                let typeCompleted = completed === true;
                try {
                  if (typeCompleted && isCurrentInsert()) {
                    this._setStatus("done", _("Typed into target window"), transcript);
                  }
                } catch (error) {
                  typeCompleted = false;
                  this._recordLifecycleError("keyboard-insert-status", error);
                }
                complete(typeCompleted);
              }, isCurrentInsert)) {
                complete(false);
              }
            } catch (error) {
              failPreparation(error, true);
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
              let result = this._copyAndMaybePasteTranscriptText(
                transcript,
                text,
                method,
                canPasteWithKeyboard,
                submitWithReturn,
                complete,
                isCurrentInsert,
                clipboardSnapshot
              );
              if (result !== null) {
                complete(result);
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
          let result = this._copyAndMaybePasteTranscriptText(
            transcript,
            text,
            method,
            canPasteWithKeyboard,
            submitWithReturn,
            complete,
            isCurrentInsert,
            clipboardSnapshot
          );
          if (result !== null) {
            complete(result);
          }
        } catch (error) {
          failPreparation(error, true);
        }
      });
    } catch (error) {
      return failPreparation(error);
    }
    return null;
  },

  _restartRelistenRecording: function() {
    if (!this.notificationSessionActive) {
      return false;
    }
    if (!this.autoRelisten) {
      return false;
    }
    if (this._recordingCommandToken) {
      return false;
    }
    if (this.terminalWorkflowRunning || this.terminalWorkflowToken) {
      this.terminalWorkflowToken = null;
    }
    let backgroundCleanupSucceeded = this._invalidateBackgroundCallbacksForRecording();
    if (!backgroundCleanupSucceeded) {
      this._setStatus("error", _("Could not start next recording"), this.lastTranscript);
      return false;
    }
    if (this.isCommandRunning || this._hasLocalProcessingWorkflow() || this.textInsertToken) {
      return false;
    }
    let relistenLanguage = this._normalizeLanguage(this.autoRelistenPendingLanguage, this._currentLanguage());
    let voiceModelCompatible = this.autoRelistenPendingLanguage
      ? this._ensureVoiceModelCompatibleForLanguage(relistenLanguage, true, _("relisten language"))
      : this._ensureVoiceModelCompatibleWithCurrentLanguage(true);
    if (!voiceModelCompatible) {
      return false;
    }
    let startArgs;
    try {
      startArgs = this._baseArgs("start", relistenLanguage);
    } catch (err) {
      let safeError = this._sanitizeErrorMessage(err);
      this._setStatus("error", _("Could not prepare relisten command: ") + safeError, this.lastTranscript);
      return false;
    }
    this.autoRelistenPendingLanguage = "";
    this.autoTranscribeRecordingKey = "";
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    let recordingCommandToken = { action: "start" };
    this._recordingCommandToken = recordingCommandToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Starting next recording..."), this.lastTranscript);
    let startHandle = this._spawnJson(startArgs, (payload) => {
      if (this._recordingCommandToken !== recordingCommandToken || !this._lifecycleAllowsWork()) {
        return;
      }
      try {
        this._recordingCommandToken = null;
        this.isCommandRunning = false;
        if (payload.error) {
          this.autoRelistenPending = false;
          this.autoRelistenPendingToken = "";
          this.autoRelistenPendingLanguage = "";
          this._applyPayloadSafely(payload, undefined, true);
          return;
        }
        let nextStatus = this._normalizePayloadStatus(payload && payload.status, Boolean(payload && payload.error));
        if (nextStatus === "recording" || nextStatus === "recorded") {
          this.autoRelistenPending = false;
          this.autoRelistenPendingToken = "";
          this.autoRelistenPendingLanguage = "";
        }
        this._applyPayloadSafely(payload);
      } catch (error) {
        if (this._recordingCommandToken === recordingCommandToken) {
          this._recordingCommandToken = null;
        }
        this.isCommandRunning = false;
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenPendingLanguage = "";
        this._recordLifecycleError("recording-relisten", error);
        this._setStatus("error", _("Could not start next recording"), this.lastTranscript);
      }
    });
    if (!startHandle) {
      if (this._recordingCommandToken === recordingCommandToken) {
        this._recordingCommandToken = null;
        this.isCommandRunning = false;
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenPendingLanguage = "";
        if (this._lifecycleAllowsWork()) {
          this._setStatus("error", _("Could not start next recording"), this.lastTranscript);
        }
      }
      return false;
    }
    this.lastNotificationKey = "";
    return true;
  },

  _preparedTranscriptText: function(transcript, suppressAutoPasteEnter, autoPasteTargetMatch) {
    let text = String(transcript || "");
    let autoPasteEnter = !suppressAutoPasteEnter && (
      typeof autoPasteTargetMatch === "boolean"
        ? autoPasteTargetMatch
        : this._windowTitleMatchesAutoPaste()
    );
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
    if (this.appendSpace && text.length < MAX_TEXT_INSERT_CHARS && text && !" \t\n\r\f\v".includes(text[text.length - 1])) {
      text += " ";
    }
    if (autoPasteEnter && !suppressAutoPasteEnter && text.length < MAX_TEXT_INSERT_CHARS &&
        text && text[text.length - 1] !== "\n") {
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
    if (!this._canMutateMenu(this.historyItem)) {
      return;
    }
    transcripts = Array.isArray(transcripts) ? transcripts : [];
    transcripts = transcripts.filter((transcript) => {
      if (!transcript || typeof transcript !== "object") {
        return false;
      }
      let preview = typeof transcript.preview === "string" ? transcript.preview.trim() : "";
      let name = typeof transcript.name === "string" ? transcript.name.trim() : "";
      let text = typeof transcript.text === "string" ? transcript.text : "";
      return preview !== "" || name !== "" || text.trim() !== "";
    });
    transcripts = transcripts.map((transcript) => ({
      preview: typeof transcript.preview === "string" ? transcript.preview.trim() : "",
      name: typeof transcript.name === "string" ? transcript.name.trim() : "",
      text: typeof transcript.text === "string" ? transcript.text : "",
    }));
    let historyWasTruncated = transcripts.length > MAX_HISTORY_MENU_ENTRIES;
    if (historyWasTruncated) {
      transcripts = transcripts.slice(0, MAX_HISTORY_MENU_ENTRIES);
    }
    let nextFingerprint = JSON.stringify({
      truncated: historyWasTruncated,
      transcripts: transcripts,
    });
    if (this._historyMenuFingerprint === nextFingerprint) {
      return;
    }
    if (!this._clearMenuItems(this.historyItem.menu)) {
      return;
    }
    if (!transcripts || transcripts.length === 0) {
      let empty = new PopupMenu.PopupMenuItem(_("No transcripts yet"));
      empty.setSensitive(false);
      this.historyItem.menu.addMenuItem(empty);
      this._historyMenuFingerprint = nextFingerprint;
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
    this._historyMenuFingerprint = nextFingerprint;
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
      this._statusRefreshToken++;
      let safeMessage = typeof message === "string" ? message : "";
      this.lastMessage = status === "error"
        ? this._uiMessageText(this._sanitizeErrorMessage(safeMessage))
        : this._uiMessageText(safeMessage);
      if (typeof transcript === "string" && transcript !== "") {
        this.lastTranscript = transcript;
      }
      this._setMenuItemSensitiveSafely(this.cancelItem, this._hasCancelableRecordingWork());
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
      this._setMenuItemSensitiveSafely(this.copyLastItem, Boolean(this.lastTranscript));
      this._setMenuItemSensitiveSafely(this.insertLastItem, Boolean(this.lastTranscript));
      this._setMenuItemSensitiveSafely(this.cancelItem, this._hasCancelableRecordingWork());
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
    let key = status === "recording" ? status : status + "\n" + String(message || "");
    if (key === this.lastNotificationKey) {
      if (status === "done" || status === "error" || (status === "idle" && previousStatus !== "idle")) {
        this.notificationSessionActive = false;
      }
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
    let transcriptCacheKey = this.lastTranscript;
    let transcriptVisible = this.showTranscriptText === true;
    if (
      this._shortTranscriptCache &&
      this._shortTranscriptCache.transcript === transcriptCacheKey &&
      this._shortTranscriptCache.visible === transcriptVisible
    ) {
      return this._shortTranscriptCache.value;
    }
    let value;
    if (!this.lastTranscript) {
      value = _("No transcript yet");
    } else if (transcriptVisible) {
      let sanitizedTranscript = String(this.lastTranscript).replace(/[\u0000-\u001F\u007F-\u009F]/g, " ").replace(/\s+/g, " ").trim();
      value = this._shortMenuText(sanitizedTranscript, MAX_UI_MESSAGE_CHARS);
    } else {
      let transcriptLength = String(this.lastTranscript).length;
      value = _("Transcript preview hidden (length: ") + String(transcriptLength) + " chars)";
    }
    this._shortTranscriptCache = {
      transcript: transcriptCacheKey,
      visible: transcriptVisible,
      value: value,
    };
    return value;
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

  _statusIconNameForStatus: function(status) {
    if (status === "recording") return "media-record-symbolic";
    if (status === "processing") return "view-refresh-symbolic";
    return "audio-input-microphone-symbolic";
  },

  _resetStatusIconCache: function() {
    this._statusIconCache = { status: null, icon: null };
  },

  _statusIconSettingForStatus: function(status) {
    if (status === "recording") return this.statusIconRecording;
    if (status === "processing") return this.statusIconProcessing;
    if (status === "recorded" || status === "done") return this.statusIconRecorded;
    if (status === "error") return this.statusIconError;
    if (status === "setup") return this.statusIconSetup;
    return this.statusIconReady;
  },

  _statusIconDefaultForStatus: function(status) {
    if (status === "recording") return STATUS_ICON_DEFAULTS.recording;
    if (status === "processing") return STATUS_ICON_DEFAULTS.processing;
    if (status === "recorded" || status === "done") return STATUS_ICON_DEFAULTS.recorded;
    if (status === "error") return STATUS_ICON_DEFAULTS.error;
    if (status === "setup") return STATUS_ICON_DEFAULTS.setup;
    return STATUS_ICON_DEFAULTS.ready;
  },

  _validatedStatusIconId: function(value, fallbackId) {
    let candidate = String(value || "").trim();
    if (candidate !== "" && STATUS_ICON_ALLOWLIST[candidate]) {
      return candidate;
    }
    let fallback = String(fallbackId || "").trim();
    return STATUS_ICON_ALLOWLIST[fallback] ? fallback : STATUS_ICON_DEFAULTS.ready;
  },

  _statusIconPathForId: function(iconId, fallbackId) {
    let validatedId = this._validatedStatusIconId(iconId, fallbackId);
    if (validatedId === "soc-original") {
      return "";
    }
    if (!this.metadata || !this.metadata.path) {
      return "";
    }
    return this.metadata.path + "/assets/status-icons/" + validatedId + ".png";
  },

  _onStatusIconSettingsChanged: function() {
    this._resetStatusIconCache();
    this._updatePanel();
  },

  _applyPanelIcon: function(status) {
    return this._runGuarded("panel-icon", () => {
      let nextIconName = this._statusIconNameForStatus(status);
      let nextIconId = this._statusIconSettingForStatus(status);
      let nextIconPath = this._statusIconPathForId(nextIconId, this._statusIconDefaultForStatus(status));
      let nextIcon = nextIconPath || nextIconName;
      if (this._statusIconCache && this._statusIconCache.icon === nextIcon) {
        return;
      }
      let applied = false;
      if (nextIconPath && typeof this.set_applet_icon_path === "function") {
        this.set_applet_icon_path(nextIconPath);
        applied = true;
      } else if (nextIconName && typeof this.set_applet_icon_name === "function") {
        this.set_applet_icon_name(nextIconName);
        applied = true;
      }
      if (applied) {
        this._statusIconCache = { status: status, icon: nextIcon };
      }
    }, undefined);
  },

  _applyPanelStyle: function(status) {
    return this._runGuarded("panel-style", () => {
      let actor = this.actor;
      if (!actor ||
          (typeof actor.is_finalized === "function" && actor.is_finalized()) ||
          typeof actor.add_style_class_name !== "function" ||
          typeof actor.remove_style_class_name !== "function") {
        return;
      }
      for (let styleClass of PANEL_STATUS_CLASSES) {
        actor.remove_style_class_name(styleClass);
      }
      actor.add_style_class_name(this._panelStyleClassForStatus(status));
    }, undefined);
  },

  _updatePanel: function(menuOpenOverride) {
    return this._runGuarded("panel-update", () => {
      if (this.status !== "recording") {
        this._recordingDisplayFingerprint = null;
      }
      let rootMenuOpen = typeof menuOpenOverride === "boolean"
        ? menuOpenOverride
        : Boolean(this.menu && this.menu.isOpen === true);
      let label = "";
      let tooltip = "Speed of Cinnamon";
      let statusText = this.status || "idle";
      let toggleText = _("Start dictation");
      let progressText = "";
      let transcriptText = this._shortTranscript();
      let microphoneText = this._microphoneLevelText();
      if (this.status === "recording") {
        let recordingMessageText = this.lastMessage
          ? this._shortMenuText(this.lastMessage, 160)
          : "";
        progressText = this._recordingProgressText();
        label = "REC " + this._formatSeconds(this._recordingElapsedSeconds());
        tooltip = _("Recording...") + " " + progressText + "\n" + microphoneText;
        statusText = "recording " + progressText + "; " + microphoneText;
        if (recordingMessageText !== "") {
          tooltip += "\n" + recordingMessageText;
          statusText += " - " + recordingMessageText;
        }
        toggleText = _("Stop dictation");
      } else if (this.status === "processing") {
        label = "...";
        tooltip = this.lastMessage || _("Processing...");
        toggleText = _("Working...");
      } else if (this.status === "error") {
        label = "ERR";
        tooltip = this.lastMessage || _("Error");
        statusText = "error";
        if (this.lastMessage) {
          statusText += " - " + this._shortMenuText(this.lastMessage, 140);
        }
      } else if (this.status === "recorded") {
        label = "RDY";
        tooltip = this.lastMessage || _("Ready to transcribe");
        toggleText = _("Transcribe recording");
      } else if (this.status === "setup") {
        label = "SET";
        tooltip = this.lastMessage || _("Setup needed");
      } else {
        label = "SOC";
        tooltip = this.lastMessage || _("Ready");
      }
      let recordingLimitText = _("Duration: ") + this._formatSeconds(this._normalizeRecordingLimit(this.maxSeconds));
      let recordingOptionsText = this._recordingOptionsLabel();
      let notificationOptionsText = this._notificationOptionsLabel();
      let outputMethodText = _("Output: ") + this._outputMethodLabel(this._normalizeOutputMethod(this.insertMethod));
      let textOptionsText = this._textOptionsLabel();
      let inputSourceText = this._inputSourceLabel();
      let modelText = this._voiceBackendLabel();
      let textModelText = this._textModelLabel();
      let autoPasteText = this._autoPasteLabel();
      let doctorSummaryText = this.doctorSummaryText || _("Doctor: not checked");
      let languageText = _("Language: ") + this._currentLanguage();
      let recorderText = _("Recorder: ") + this._recorderLabel(this._normalizeRecorder(this.recorder));
      let statusIconName = this._statusIconNameForStatus(this.status);
      let statusIconSetting = this._statusIconSettingForStatus(this.status);
      let statusIconPath = this._statusIconPathForId(statusIconSetting, this._statusIconDefaultForStatus(this.status));
      let statusIcon = statusIconPath || statusIconName;
      let panelLabel = this.showPanelLabel ? label : "";
      let tooltipText = tooltip + "\n" + transcriptText;
      let panelActor = this.actor;
      let panelActorReady = Boolean(
        panelActor &&
        (typeof panelActor.is_finalized !== "function" || !panelActor.is_finalized()) &&
        typeof this.set_applet_label === "function" &&
        typeof this.set_applet_tooltip === "function"
      );
      let styleClass = this._panelStyleClassForStatus(this.status);
      let previousFingerprint = this._panelRenderFingerprint;
      let panelRenderUnchanged = Boolean(
        previousFingerprint &&
        previousFingerprint.status === this.status &&
        previousFingerprint.showPanelLabel === Boolean(this.showPanelLabel) &&
        previousFingerprint.panelLabel === panelLabel &&
        previousFingerprint.tooltipText === tooltipText &&
        previousFingerprint.statusIcon === statusIcon &&
        previousFingerprint.styleClass === styleClass &&
        previousFingerprint.panelActorReady === panelActorReady
      );
      if (
        panelRenderUnchanged &&
        previousFingerprint.statusText === statusText &&
        previousFingerprint.toggleText === toggleText &&
        previousFingerprint.microphoneText === microphoneText &&
        previousFingerprint.doctorSummaryText === doctorSummaryText &&
        previousFingerprint.languageText === languageText &&
        previousFingerprint.recorderText === recorderText &&
        previousFingerprint.recordingLimitText === recordingLimitText &&
        previousFingerprint.recordingOptionsText === recordingOptionsText &&
        previousFingerprint.notificationOptionsText === notificationOptionsText &&
        previousFingerprint.outputMethodText === outputMethodText &&
        previousFingerprint.textOptionsText === textOptionsText &&
        previousFingerprint.inputSourceText === inputSourceText &&
        previousFingerprint.modelText === modelText &&
        previousFingerprint.textModelText === textModelText &&
        previousFingerprint.transcriptText === transcriptText &&
        previousFingerprint.autoPasteText === autoPasteText &&
        previousFingerprint.progressText === progressText &&
        previousFingerprint.rootMenuOpen === rootMenuOpen
      ) {
        return true;
      }
      if (!panelRenderUnchanged) {
        this._applyPanelIcon(this.status);
        this._applyPanelStyle(this.status);
        if (panelActorReady) {
          this.set_applet_label(panelLabel);
          this.set_applet_tooltip(tooltipText);
        }
      }
      let menuRenderSucceeded = true;
      if (rootMenuOpen) {
        let labelWriteSucceeded = this._setMenuItemLabelSafely(this.statusItem, _("Status: ") + statusText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.microphoneLevelItem, microphoneText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.doctorSummaryItem, doctorSummaryText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.languageItem, languageText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.recorderItem, recorderText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.recordingLimitItem, recordingLimitText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.recordingOptionsItem, recordingOptionsText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.notificationOptionsItem, notificationOptionsText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.outputMethodItem, outputMethodText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.textOptionsItem, textOptionsText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._updateAutoPasteItem(autoPasteText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.inputSourceItem, inputSourceText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.modelItem, modelText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.textModelItem, textModelText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.transcriptItem, transcriptText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
        labelWriteSucceeded = this._setMenuItemLabelSafely(this.toggleItem, toggleText);
        menuRenderSucceeded = labelWriteSucceeded && menuRenderSucceeded;
      }
      this._panelRenderFingerprint = panelActorReady && menuRenderSucceeded ? {
        status: this.status,
        showPanelLabel: Boolean(this.showPanelLabel),
        panelLabel: panelLabel,
        tooltipText: tooltipText,
        statusText: statusText,
        toggleText: toggleText,
        microphoneText: microphoneText,
        doctorSummaryText: doctorSummaryText,
        languageText: languageText,
        recorderText: recorderText,
        recordingLimitText: recordingLimitText,
        recordingOptionsText: recordingOptionsText,
        notificationOptionsText: notificationOptionsText,
        outputMethodText: outputMethodText,
        textOptionsText: textOptionsText,
        inputSourceText: inputSourceText,
        modelText: modelText,
        textModelText: textModelText,
        transcriptText: transcriptText,
        autoPasteText: autoPasteText,
        progressText: progressText,
        statusIcon: statusIcon,
        styleClass: styleClass,
        panelActorReady: panelActorReady,
        rootMenuOpen: rootMenuOpen,
      } : null;
      return this._panelRenderFingerprint !== null;
    }, false);
  }
};

function main(metadata, orientation, panelHeight, instanceId) {
  return new MyApplet(metadata, orientation, panelHeight, instanceId);
}
