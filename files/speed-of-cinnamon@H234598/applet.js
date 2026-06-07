const Applet = imports.ui.applet;
const Main = imports.ui.main;
const ModalDialog = imports.ui.modalDialog;
const PopupMenu = imports.ui.popupMenu;
const Settings = imports.ui.settings;
const Clutter = imports.gi.Clutter;
const St = imports.gi.St;
const Util = imports.misc.util;
const GLib = imports.gi.GLib;
const Gio = imports.gi.Gio;
const Pango = imports.gi.Pango;
const ByteArray = imports.byteArray;
const Mainloop = imports.mainloop;
const Extension = imports.ui.extension;

const UUID = "speed-of-cinnamon@H234598";
const HOTKEY_ID = "speed-of-cinnamon-toggle";
const PRIMARY_HOTKEY_ID = "speed-of-cinnamon-primary-language";
const SECONDARY_HOTKEY_ID = "speed-of-cinnamon-secondary-language";
const CANCEL_HOTKEY_ID = "speed-of-cinnamon-cancel";
const DEFAULT_CLI = GLib.build_filenamev([GLib.get_home_dir(), ".local", "bin", "speed-of-cinnamon"]);
const SYSTEM_CLI = "/usr/bin/speed-of-cinnamon";
const RUNBOOK_URL = "https://gist.github.com/H234598/b95129e13ac0b09c9777edd41aeedfa0";
const DEFAULT_OPENAI_COMPATIBLE_URL = "https://api.openai.com/v1";
const DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-4o-transcribe";
const DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL = "gpt-4o-mini";
const LEGACY_OPENAI_COMPATIBLE_URL = "http://127.0.0.1:8000/v1";
const PASTE_FOCUS_DELAY_MS = 120;
const PASTE_SUBMIT_DELAY_MS = 300;
const CLIPBOARD_READY_RETRY_MS = 40;
const CLIPBOARD_READY_TIMEOUT_MS = 1000;
const NON_TEXT_TEXT_CLIPBOARD_TARGETS = {
  "text/uri-list": true,
  "text/x-moz-url": true
};
const SELF_PROTECTION_NOTICE_COOLDOWN_MS = 3000;
const CLIPBOARD_OVERWRITE_APPROVAL_TTL_MS = 5000;
const CLIPBOARD_TARGET_TIMEOUT_SECONDS = 1;
const MAX_CLIPBOARD_TARGET_OUTPUT_BYTES = 65536;
const MAX_XDOTOOL_TARGET_OUTPUT_BYTES = 4096;
const ALARM_CHECK_SECONDS = 60;
const MAX_CLI_ARG_BYTES = 4096;
const MAX_CLI_ARG_COUNT = 128;
const MAX_CLI_COMMAND_BYTES = 32768;
const MAX_TEXT_INSERT_CHARS = 120000;
const MAX_SETTING_TEXT_CHARS = 4096;
const NUL_RE = /\u0000/g;
const NON_ASCII_RE = /[^\u0000-\u007E]/g;
const COMBINING_MARKS_RE = /[\u0300-\u036f]/g;
const ASCII_ONLY_RE = /^[\u0000-\u007E]*$/;
const SENSITIVE_ERROR_RE = /(?:\b(?:bearer|token|api[_ -]?key|apikey|password|passwd|passphrase|secret)\b\s*[:=]\s*[^,\s;]+|\b(?:bearer|token|api[_ -]?key|apikey|password|passwd|passphrase|secret)\b\s+(?!(?:is|are|was|were|contains?|must|too|missing|invalid|required|not|empty)\b)[^,\s;]+|\b(?:sk|sess)-[A-Za-z0-9_\-]{3,}\b|[a-z][a-z0-9+.-]*:\/\/[^/@\s]+@)/i;
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
  ["transcriber-command", "transcriberCommand"],
  ["post-process-backend", "postProcessBackend"],
  ["post-process-command", "postProcessCommand"],
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

  _init: function(metadata, orientation, panelHeight, instanceId) {
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
    this.insertMethod = "none";
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
    this.ollamaUrl = "http://127.0.0.1:11434";
    this.ollamaModel = "";
    this.openaiCompatibleUrl = DEFAULT_OPENAI_COMPATIBLE_URL;
    this.openaiCompatibleModel = DEFAULT_OPENAI_COMPATIBLE_MODEL;
    this.openaiCompatibleTextModel = DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL;
    this.openaiCompatibleFlexProcessing = true;
    this.openaiCompatibleApiKey = "";
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
    this.cancelPendingWhileCommandRunning = false;
    this._statusRefreshToken = 0;
    this._statusCommandRunning = false;
    this._doctorCommandRunning = false;
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
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = 0;
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
    this.externalApiEnvMonitor = null;
    this.externalApiEnvApplyTarget = "voice";
    this.appletRemoved = false;
    this.spawnGeneration = 0;

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
  },

  _bindSettings: function() {
    this.settings.bindProperty(Settings.BindingDirection.IN, "toggle-keybinding", "toggleKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "primary-language-keybinding", "primaryLanguageKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "secondary-language-keybinding", "secondaryLanguageKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "cancel-keybinding", "cancelKeybinding", this._onHotkeyChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "show-panel-label", "showPanelLabel", this._updatePanel, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "language", "language", this._onLanguageSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "secondary-language", "secondaryLanguage", this._onLanguageSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "max-seconds", "maxSeconds", this._onRecordingLimitSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "auto-transcribe-timeout", "autoTranscribeTimeout", this._onRecordingOptionsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "auto-relisten", "autoRelisten", this._onRecordingOptionsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "keep-recording-artifacts", "keepRecordingArtifacts", this._onRecordingOptionsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "recorder", "recorder", this._onRecorderSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "input-device", "inputDevice", this._onInputSourceSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "insert-method", "insertMethod", this._onOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "append-space", "appendSpace", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "typing-delay-ms", "typingDelayMs", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "sanitize-special-chars", "sanitizeSpecialChars", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "soften-profanity", "softenProfanity", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "max-transcript-files", "maxTranscriptFiles", this._onTranscriptRetentionSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "artifact-encryption", "artifactEncryption", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "auto-paste-window-title", "autoPasteWindowTitle", this._onTextOutputSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "cli-path", "cliPath", null, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "transcriber", "transcriber", this._onVoiceBackendSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "whisper-model", "whisperModel", this._onVoiceBackendSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "transcriber-command", "transcriberCommand", this._onVoiceBackendSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-backend", "postProcessBackend", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-command", "postProcessCommand", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "ollama-url", "ollamaUrl", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "ollama-model", "ollamaModel", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-url", "openaiCompatibleUrl", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-model", "openaiCompatibleModel", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-text-model", "openaiCompatibleTextModel", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-flex-processing", "openaiCompatibleFlexProcessing", this._onOpenAiFlexProcessingSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "openai-compatible-api-key", "openaiCompatibleApiKey", this._onVoiceBackendSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-preset", "postProcessPreset", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-preserve-code", "postProcessPreserveCode", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-never-add-content", "postProcessNeverAddContent", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-mask-sensitive-data", "postProcessMaskSensitiveData", this._onTextModelSettingsChanged, null);
    this.settings.bindProperty(Settings.BindingDirection.IN, "post-process-prompt", "postProcessPrompt", this._onTextModelSettingsChanged, null);
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
    this.toggleItem.connect("activate", () => {
      this._rememberFocusedWindow(true);
      this._toggleRecording();
    });
    this.menu.addMenuItem(this.toggleItem);

    this.cancelItem = new PopupMenu.PopupIconMenuItem(_("Cancel recording"), "process-stop-symbolic", St.IconType.SYMBOLIC);
    this.cancelItem.setSensitive(false);
    this.cancelItem.connect("activate", () => this._cancelRecording());
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
    this.languageItem.menu.connect("open-state-changed", (menu, open) => {
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
    this.recorderItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecorderMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.recorderItem);
    this._populateRecorderMenu();

    this.recordingLimitItem = new PopupMenu.PopupSubMenuMenuItem(_("Duration: 30s"));
    this.recordingLimitItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecordingLimitMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.recordingLimitItem);
    this._populateRecordingLimitMenu();

    this.recordingOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Recording options"));
    this.recordingOptionsItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateRecordingOptionsMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.recordingOptionsItem);
    this._populateRecordingOptionsMenu();

    this.notificationOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Notifications"));
    this.notificationOptionsItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateNotificationOptionsMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.notificationOptionsItem);
    this._populateNotificationOptionsMenu();

    this.alarmItem = new PopupMenu.PopupSubMenuMenuItem(_("Alarms"));
    this.alarmItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshAlarmMenu();
      }
    });
    this.toolsMenuItem.menu.addMenuItem(this.alarmItem);
    this._populateAlarmMenu([], _("Open menu to load alarms"));

    this.shortcutItem = new PopupMenu.PopupSubMenuMenuItem(_("Keyboard shortcuts"));
    this.shortcutItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateShortcutMenu();
      }
    });
    this.toolsMenuItem.menu.addMenuItem(this.shortcutItem);
    this._populateShortcutMenu();

    this.outputMethodItem = new PopupMenu.PopupSubMenuMenuItem(_("Output: Clipboard and paste"));
    this.textOutputMenuItem.menu.addMenuItem(this.outputMethodItem);
    this._populateOutputMethodMenu();

    this.textOptionsItem = new PopupMenu.PopupSubMenuMenuItem(_("Text options"));
    this.textOptionsItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._populateTextOptionsMenu();
      }
    });
    this.textOutputMenuItem.menu.addMenuItem(this.textOptionsItem);
    this._populateTextOptionsMenu();

    this.autoPasteItem = new PopupMenu.PopupSubMenuMenuItem(_("Auto-Submitt: codex"));
    this.autoPasteItem.menu.connect("open-state-changed", (menu, open) => {
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
    this.copyLastItem.connect("activate", () => this._copyLastTranscript());
    this.transcriptsMenuItem.menu.addMenuItem(this.copyLastItem);

    this.insertLastItem = new PopupMenu.PopupIconMenuItem(_("Insert last transcript"), "edit-paste-symbolic", St.IconType.SYMBOLIC);
    this.insertLastItem.setSensitive(false);
    this.insertLastItem.connect("activate", () => this._insertLastTranscript());
    this.transcriptsMenuItem.menu.addMenuItem(this.insertLastItem);

    this.historyItem = new PopupMenu.PopupSubMenuMenuItem(_("Recent transcripts"));
    this.historyItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshHistory();
      }
    });
    this.transcriptsMenuItem.menu.addMenuItem(this.historyItem);
    this._populateHistoryMenu([]);

    let statusNow = new PopupMenu.PopupIconMenuItem(_("Refresh status"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    statusNow.connect("activate", () => this._refreshStatus());
    this.toolsMenuItem.menu.addMenuItem(statusNow);

    let restartApplet = new PopupMenu.PopupIconMenuItem(_("Restart applet"), "view-refresh-symbolic", St.IconType.SYMBOLIC);
    restartApplet.connect("activate", () => this._restartApplet());
    this.toolsMenuItem.menu.addMenuItem(restartApplet);

    let doctor = new PopupMenu.PopupIconMenuItem(_("Run doctor"), "dialog-information-symbolic", St.IconType.SYMBOLIC);
    doctor.connect("activate", () => this._runDoctor());
    this.toolsMenuItem.menu.addMenuItem(doctor);

    let openSettings = new PopupMenu.PopupIconMenuItem(_("Open applet settings"), "preferences-system-symbolic", St.IconType.SYMBOLIC);
    openSettings.connect("activate", () => this._openAppletSettings());
    this.toolsMenuItem.menu.addMenuItem(openSettings);

    let openGuide = new PopupMenu.PopupIconMenuItem(_("Open setup guide"), "help-browser-symbolic", St.IconType.SYMBOLIC);
    openGuide.connect("activate", () => this._openSetupGuide());
    this.toolsMenuItem.menu.addMenuItem(openGuide);

    this.installMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Install"));
    this.toolsMenuItem.menu.addMenuItem(this.installMenuItem);

    let installOllamaRuntime = new PopupMenu.PopupIconMenuItem(_("Install Ollama"), "system-software-install-symbolic", St.IconType.SYMBOLIC);
    installOllamaRuntime.connect("activate", () => this._installOllamaRuntime());
    this.installMenuItem.menu.addMenuItem(installOllamaRuntime);

    let uninstallOllamaRuntime = new PopupMenu.PopupIconMenuItem(_("Uninstall Ollama"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
    uninstallOllamaRuntime.connect("activate", () => this._uninstallOllamaRuntime());
    this.installMenuItem.menu.addMenuItem(uninstallOllamaRuntime);

    let basicSetup = new PopupMenu.PopupIconMenuItem(_("Basic setup"), "emblem-system-symbolic", St.IconType.SYMBOLIC);
    basicSetup.connect("activate", () => this._runBasicSetup());
    this.installMenuItem.menu.addMenuItem(basicSetup);

    let installOllamaModel = new PopupMenu.PopupIconMenuItem(_("Choose Ollama text model"), "view-list-symbolic", St.IconType.SYMBOLIC);
    installOllamaModel.connect("activate", () => this._chooseOllamaTextModel());
    this.installMenuItem.menu.addMenuItem(installOllamaModel);

    this.diagnosticsMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Diagnostics"));
    this.toolsMenuItem.menu.addMenuItem(this.diagnosticsMenuItem);
    this.diagnosticsMenuItem.menu.addMenuItem(this.doctorSummaryItem);

    let setupPlan = new PopupMenu.PopupIconMenuItem(_("Copy setup plan"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    setupPlan.connect("activate", () => this._copySetupPlan());
    this.diagnosticsMenuItem.menu.addMenuItem(setupPlan);

    let setupCommands = new PopupMenu.PopupIconMenuItem(_("Copy setup commands"), "utilities-terminal-symbolic", St.IconType.SYMBOLIC);
    setupCommands.connect("activate", () => this._copySetupCommands());
    this.diagnosticsMenuItem.menu.addMenuItem(setupCommands);

    let diagnostics = new PopupMenu.PopupIconMenuItem(_("Copy diagnostics"), "edit-copy-symbolic", St.IconType.SYMBOLIC);
    diagnostics.connect("activate", () => this._copyDiagnostics());
    this.diagnosticsMenuItem.menu.addMenuItem(diagnostics);

    let saveDiagnostics = new PopupMenu.PopupIconMenuItem(_("Save diagnostics"), "document-save-symbolic", St.IconType.SYMBOLIC);
    saveDiagnostics.connect("activate", () => this._saveDiagnostics());
    this.diagnosticsMenuItem.menu.addMenuItem(saveDiagnostics);

    let benchmark = new PopupMenu.PopupIconMenuItem(_("Benchmark downloaded models"), "utilities-system-monitor-symbolic", St.IconType.SYMBOLIC);
    benchmark.connect("activate", () => this._selectBenchmarkAudioFile());
    this.diagnosticsMenuItem.menu.addMenuItem(benchmark);

    this.inputSourceItem = new PopupMenu.PopupSubMenuMenuItem(_("Input source"));
    this.inputSourceItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshInputSourceMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.inputSourceItem);
    this._populateInputSourceMenu([], _("Open menu to load input sources"));

    this.modelItem = new PopupMenu.PopupSubMenuMenuItem(_("Voice model"));
    this.modelItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshModelMenu();
      }
    });
    this.recordingMenuItem.menu.addMenuItem(this.modelItem);
    this._populateModelMenu([], _("Open menu to load voice models"));

    this.textModelItem = new PopupMenu.PopupSubMenuMenuItem(_("Text model"));
    this.textModelItem.menu.connect("open-state-changed", (menu, open) => {
      if (open) {
        this._refreshTextModelMenu();
      }
    });
    this.textOutputMenuItem.menu.addMenuItem(this.textModelItem);
    this._populateTextModelMenu([], _("Open menu to load local text models"));

    this.maintenanceMenuItem = new PopupMenu.PopupSubMenuMenuItem(_("Files and settings"));
    this.toolsMenuItem.menu.addMenuItem(this.maintenanceMenuItem);

    let transcripts = new PopupMenu.PopupIconMenuItem(_("Open transcripts"), "folder-documents-symbolic", St.IconType.SYMBOLIC);
    transcripts.connect("activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_state_dir(), "speed-of-cinnamon", "transcripts"]), _("Opened transcripts"));
    });
    this.maintenanceMenuItem.menu.addMenuItem(transcripts);

    let listTranscripts = new PopupMenu.PopupIconMenuItem(_("List all Transcripts"), "view-list-symbolic", St.IconType.SYMBOLIC);
    listTranscripts.connect("activate", () => this._listAllTranscripts());
    this.maintenanceMenuItem.menu.addMenuItem(listTranscripts);

    let exportTranscripts = new PopupMenu.PopupIconMenuItem(_("Export all Transcripts"), "document-save-symbolic", St.IconType.SYMBOLIC);
    exportTranscripts.connect("activate", () => this._exportAllTranscripts());
    this.maintenanceMenuItem.menu.addMenuItem(exportTranscripts);

    let cleanupPreview = new PopupMenu.PopupIconMenuItem(_("Preview cleanup"), "edit-find-symbolic", St.IconType.SYMBOLIC);
    cleanupPreview.connect("activate", () => this._previewCleanup());
    this.maintenanceMenuItem.menu.addMenuItem(cleanupPreview);

    let cleanup = new PopupMenu.PopupIconMenuItem(_("Clean all old files"), "edit-clear-symbolic", St.IconType.SYMBOLIC);
    cleanup.connect("activate", () => this._cleanupOldFiles());
    this.maintenanceMenuItem.menu.addMenuItem(cleanup);

    let exportSettings = new PopupMenu.PopupIconMenuItem(_("Export settings"), "document-save-symbolic", St.IconType.SYMBOLIC);
    exportSettings.connect("activate", () => this._exportSettings());
    this.maintenanceMenuItem.menu.addMenuItem(exportSettings);

    let importSettings = new PopupMenu.PopupIconMenuItem(_("Import settings"), "document-open-symbolic", St.IconType.SYMBOLIC);
    importSettings.connect("activate", () => this._importSettings());
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
  },

  _styleSelectionSubmenu: function(menuItem) {
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
  },

  _styleMenuItemLabel: function(item, options) {
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
      global.logError(err);
    }
    return item;
  },

  _selectionMenuItem: function(label) {
    return this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(String(label || "")));
  },

  _selectionInfoItem: function(label) {
    let item = this._styleMenuItemLabel(new PopupMenu.PopupMenuItem(String(label || "")), { wrap: true });
    item.setSensitive(false);
    return item;
  },

  _shortMenuText: function(value, maxChars) {
    let text = String(value || "").replace(/\s+/g, " ").trim();
    let limit = Math.max(16, Number(maxChars || 72));
    if (text.length <= limit) {
      return text;
    }
    let head = Math.max(8, Math.floor((limit - 3) * 0.55));
    let tail = Math.max(5, limit - 3 - head);
    return text.slice(0, head) + "..." + text.slice(text.length - tail);
  },

  _sanitizeErrorMessage: function(value) {
    let text = String(value || "").replace(NUL_RE, "");
    if (SENSITIVE_ERROR_RE.test(text)) {
      return "[redacted error details]";
    }
    if (text.length > MAX_SETTING_TEXT_CHARS) {
      return text.slice(0, MAX_SETTING_TEXT_CHARS) + "...";
    }
    return text;
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
    this._registerHotkey(CANCEL_HOTKEY_ID, this.cancelKeybinding, () => {
      this._cancelRecording();
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
    this.typingDelayMs = this._normalizeTypingDelayMs(this.typingDelayMs);
    this._populateTextOptionsMenu();
    this._updateAutoPasteItem();
    this._updatePanel();
  },

  _onTranscriptRetentionSettingsChanged: function() {
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
    this._populateInputSourceMenu([], _("Open menu to load input sources"));
    this._updatePanel();
  },

  _onVoiceBackendSettingsChanged: function() {
    this._ensureVoiceModelCompatibleWithPrimaryLanguage(false);
    this._populateModelMenu([], _("Open menu to load voice models"));
    this._updatePanel();
  },

  _onTextModelSettingsChanged: function() {
    this._populateTextModelMenu([], _("Open menu to load local text models"));
    this._updatePanel();
  },

  _onOpenAiFlexProcessingSettingsChanged: function() {
    this.openaiCompatibleFlexProcessing = Boolean(this.openaiCompatibleFlexProcessing);
    this._updateOpenAiFlexProcessingItem();
    this._updatePanel();
  },

  on_applet_clicked: function() {
    if (!this.menu.isOpen) {
      this._rememberFocusedWindow();
    }
    this.menu.toggle();
  },

  on_applet_removed_from_panel: function() {
    this.appletRemoved = true;
    this.spawnGeneration += 1;
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenManualStopRequested = false;
    this.modelMenuRefreshToken = null;
    this.textModelMenuRefreshToken = null;
    this._clearStatusTimer();
    this._clearDisplayTimer();
    this._clearSetupCheckTimer();
    this._clearPasteTimer();
    this._clearClipboardOverwriteApproval();
    this._clearAlarmTimer();
    this._clearOllamaInstallWatchTimer();
    this._clearExternalApiEnvMonitor();
    Main.keybindingManager.removeHotKey(this._hotkeyName(HOTKEY_ID));
    Main.keybindingManager.removeHotKey(this._hotkeyName(PRIMARY_HOTKEY_ID));
    Main.keybindingManager.removeHotKey(this._hotkeyName(SECONDARY_HOTKEY_ID));
    Main.keybindingManager.removeHotKey(this._hotkeyName(CANCEL_HOTKEY_ID));
    if (this.settings) {
      this.settings.finalize();
    }
  },

  _baseArgs: function(command) {
    let safeInputDevice = this._coerceCliTextArg(this.inputDevice, "input device");
    let safeTranscriberCommand = this._coerceCliTextArg(this.transcriberCommand, "transcriber command");
    let safePostProcessCommand = this._coerceCliTextArg(this.postProcessCommand, "post-process command");
    let safeOllamaUrl = this._coerceCliTextArg(this.ollamaUrl, "ollama URL");
    let safeOllamaModel = this._coerceCliTextArg(this.ollamaModel, "ollama model");
    let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this.openaiCompatibleUrl, "openai-compatible URL");
    let safeOpenAiCompatibleModel = this._coerceCliTextArg(this.openaiCompatibleModel, "openai-compatible model");
    let safeOpenAiCompatibleTextModel = this._coerceCliTextArg(this.openaiCompatibleTextModel, "openai-compatible text model");
    let safePostProcessPrompt = this._coerceCliTextArg(this._effectivePostProcessPrompt(), "post-process prompt");
    let safeWhisperModel = this._coerceCliTextArg(this.whisperModel, "whisper model");
    let safePersonalContext = this._coerceCliTextArg(this._singleLineCliTextValue(this.personalContext), "personal context");
    let safeVocabulary = this._coerceCliTextArg(this._singleLineCliTextValue(this.vocabulary), "vocabulary");

    let args = [
      this._cliCommand(),
      command,
      "--json",
      "--language", String(this._currentLanguage()),
      "--max-seconds", String(this._normalizeRecordingLimit(this.maxSeconds)),
      "--recorder", String(this.recorder || "auto"),
      "--transcriber", String(this.transcriber || "auto"),
      "--post-process-backend", String(this.postProcessBackend || "none"),
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
    if (safeInputDevice.trim() !== "") {
      args.push("--input-device", safeInputDevice);
    }
    if (safeTranscriberCommand.trim() !== "") {
      args.push("--transcriber-command", safeTranscriberCommand);
    }
    if (safePostProcessCommand.trim() !== "") {
      args.push("--post-process-command", safePostProcessCommand);
    }
    if (safeOllamaUrl.trim() !== "") {
      args.push("--ollama-url", safeOllamaUrl);
    }
    if (safeOllamaModel.trim() !== "") {
      args.push("--ollama-model", safeOllamaModel);
    }
    if (safeOpenAiCompatibleUrl.trim() !== "") {
      args.push("--openai-compatible-url", safeOpenAiCompatibleUrl);
    }
    if (safeOpenAiCompatibleModel.trim() !== "") {
      args.push("--openai-compatible-model", safeOpenAiCompatibleModel);
    }
    if (safeOpenAiCompatibleTextModel.trim() !== "") {
      args.push("--openai-compatible-text-model", safeOpenAiCompatibleTextModel);
    }
    if (!Boolean(this.openaiCompatibleFlexProcessing)) {
      args.push("--no-openai-compatible-flex-processing");
    }
    if (safePostProcessPrompt.trim() !== "") {
      args.push("--post-process-prompt", safePostProcessPrompt);
    }
    if (safeWhisperModel.trim() !== "") {
      args.push("--whisper-model", safeWhisperModel);
    }
    if (safePersonalContext.trim() !== "") {
      args.push("--personal-context", safePersonalContext);
    }
    if (safeVocabulary.trim() !== "") {
      args.push("--vocabulary", safeVocabulary);
    }
    return args;
  },

  _statusArgs: function() {
    return [this._cliCommand(), "status", "--json"];
  },

  _doctorArgs: function() {
    return [this._cliCommand(), "doctor", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshotForCli()), "--json"];
  },

  _setupArgs: function() {
    return [this._cliCommand(), "setup", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshotForCli()), "--json"];
  },

  _diagnosticsArgs: function() {
    return [this._cliCommand(), "diagnostics", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshotForCli()), "--json"];
  },

  _diagnosticsSaveArgs: function() {
    return [this._cliCommand(), "diagnostics", "--applet", "--settings-json", JSON.stringify(this._settingsSnapshotForCli()), "--save", "--json"];
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
    let value = Math.floor(Number(seconds));
    if (!isFinite(value)) {
      value = DEFAULT_RECORDING_SECONDS;
    }
    return Math.max(MIN_RECORDING_SECONDS, Math.min(MAX_RECORDING_SECONDS, value));
  },

  _normalizeTypingDelayMs: function(delay) {
    let value = Math.floor(Number(delay));
    if (!isFinite(value)) {
      value = DEFAULT_TYPING_DELAY_MS;
    }
    return Math.max(MIN_TYPING_DELAY_MS, Math.min(MAX_TYPING_DELAY_MS, value));
  },

  _normalizeTranscriptLimit: function(limit) {
    let value = Math.floor(Number(limit));
    if (!isFinite(value)) {
      value = DEFAULT_MAX_TRANSCRIPT_FILES;
    }
    return Math.max(MIN_TRANSCRIPT_FILES, Math.min(MAX_TRANSCRIPT_FILES, value));
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
    this.recordingLimitItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let custom = new PopupMenu.PopupIconMenuItem((hasPreset ? "[ ] " : "[x] ") + _("Custom seconds..."), "document-edit-symbolic", St.IconType.SYMBOLIC);
    custom.connect("activate", () => this._promptCustomRecordingLimit());
    this.recordingLimitItem.menu.addMenuItem(custom);
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
    if (!GLib.find_program_in_path("zenity")) {
      this.lastMessage = _("Install zenity to enter a custom duration.");
      this._setStatus("ready", this.lastMessage, this.lastTranscript);
      return;
    }
    this._spawnText(this._customRecordingLimitPromptArgs(), (output) => {
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
      this._setStatus("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    let seconds = Math.floor(Number(text));
    if (!isFinite(seconds) || seconds < MIN_RECORDING_SECONDS || seconds > MAX_RECORDING_SECONDS) {
      this.lastMessage = _("Duration must be between 0 and 3600 seconds.");
      this._setStatus("ready", this.lastMessage, this.lastTranscript);
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
    this.transcriptStorageItem.menu.removeAll();
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
      item.connect("activate", () => this._selectTranscriptStorageLimit(limit));
      this.transcriptStorageItem.menu.addMenuItem(item);
    }
    this.transcriptStorageItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let custom = new PopupMenu.PopupIconMenuItem((hasPreset ? "[ ] " : "[x] ") + _("Custom transcript limit..."), "document-edit-symbolic", St.IconType.SYMBOLIC);
    custom.connect("activate", () => this._promptCustomTranscriptLimit());
    this.transcriptStorageItem.menu.addMenuItem(custom);
    this._updateTranscriptStorageItem();
  },

  _selectTranscriptStorageLimit: function(limit) {
    this.maxTranscriptFiles = this._normalizeTranscriptLimit(limit);
    this.settings.setValue("max-transcript-files", this.maxTranscriptFiles);
    this._populateTranscriptStorageMenu();
    this._setStatus("ready", _("Keep a maximum of ") + String(this.maxTranscriptFiles) + _(" transcript files"), this.lastTranscript);
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
    if (!GLib.find_program_in_path("zenity")) {
      this.lastMessage = _("Install zenity to enter a custom transcript limit.");
      this._setStatus("ready", this.lastMessage, this.lastTranscript);
      return;
    }
    this._spawnText(this._customTranscriptLimitPromptArgs(), (output) => {
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
      this._setStatus("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    let limit = Math.floor(Number(text));
    if (!isFinite(limit) || limit < MIN_TRANSCRIPT_FILES || limit > MAX_TRANSCRIPT_FILES) {
      this.lastMessage = _("Transcript limit must be between 1 and 1000.");
      this._setStatus("ready", this.lastMessage, this.lastTranscript);
      return null;
    }
    return limit;
  },

  _populateRecordingOptionsMenu: function() {
    if (!this.recordingOptionsItem) {
      return;
    }
    this.recordingOptionsItem.menu.removeAll();

    let autoTranscribe = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.autoTranscribeTimeout), _("Auto-transcribe at time limit")));
    autoTranscribe.connect("activate", () => this._toggleAutoTranscribeTimeout());
    this.recordingOptionsItem.menu.addMenuItem(autoTranscribe);

    let autoRelisten = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.autoRelisten), _("Auto Relisten")));
    autoRelisten.connect("activate", () => this._toggleAutoRelisten());
    this.recordingOptionsItem.menu.addMenuItem(autoRelisten);

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

  _toggleAutoRelisten: function() {
    this.autoRelisten = !Boolean(this.autoRelisten);
    this.settings.setValue("auto-relisten", this.autoRelisten);
    this._populateRecordingOptionsMenu();
    this._setRecordingOptionStatus(
      this.autoRelisten ? _("Auto Relisten enabled") : _("Auto Relisten disabled")
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

    let soften = new PopupMenu.PopupMenuItem(this._optionLabel(Boolean(this.softenProfanity), _("Replace profanity with harmless words")));
    soften.connect("activate", () => this._toggleSoftenProfanity());
    this.textOptionsItem.menu.addMenuItem(soften);
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

  _toggleSoftenProfanity: function() {
    this.softenProfanity = !Boolean(this.softenProfanity);
    this.settings.setValue("soften-profanity", this.softenProfanity);
    this._populateTextOptionsMenu();
    this._setTextOptionStatus(
      this.softenProfanity ? _("Profanity replacement enabled") : _("Profanity replacement disabled")
    );
  },

  _autoPasteTitleValues: function(value) {
    let raw = String(value || "").replace(NUL_RE, "").slice(0, MAX_SETTING_TEXT_CHARS);
    let values = [];
    let seen = {};
    for (let item of raw.split(/[,\n\r]+/)) {
      let title = String(item || "").trim();
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
    return String(value || "").replace(NUL_RE, "").trim().toLowerCase();
  },

  _autoPastePromptArgs: function() {
    let current = this._normalizeAutoPasteTitle(this.autoPasteWindowTitle) || DEFAULT_AUTO_PASTE_TITLE;
    return [
      "zenity",
      "--entry",
      "--title=Auto-Submitt",
      "--text=Built-in marker names match known window classes/app IDs; codex also matches the window title. Custom strings match the full window title case-insensitively. Empty disables Auto-Submitt.",
      "--entry-text=" + current
    ];
  },

  _autoPasteEnabled: function() {
    return this._autoPasteTitleValues(this.autoPasteWindowTitle).length > 0;
  },

  _autoPasteLabel: function() {
    let titles = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    if (titles.length === 0) {
      return _("Auto-Submitt: off");
    }
    return _("Auto-Submitt: ") + this._shortMenuText(titles.join(", "), 48);
  },

  _configureAutoPaste: function() {
    if (!GLib.find_program_in_path("zenity")) {
      this._setTextOptionStatus(_("Install zenity to enter a custom Auto-Submitt string"));
      return;
    }
    this._setTextOptionStatus(_("Enter custom Auto-Submitt window title text..."));
    this._spawnText(this._autoPastePromptArgs(), (output) => {
      this._setAutoPasteTitles(this._autoPasteTitleValues(output));
    }, { timeoutMs: 0 });
  },

  _setAutoPasteTitles: function(values) {
    this.autoPasteWindowTitle = this._normalizeAutoPasteTitle((values || []).join(", "));
    this.settings.setValue("auto-paste-window-title", this.autoPasteWindowTitle);
    this._populateAutoPasteMenu();
    let message = this._autoPasteEnabled()
      ? _("Auto-Submitt targets: ") + this.autoPasteWindowTitle
      : _("Auto-Submitt disabled");
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
    this.autoPasteItem.menu.removeAll();
    let currentValues = this._autoPasteTitleValues(this.autoPasteWindowTitle);
    let current = {};
    for (let value of currentValues) {
      current[value.toLowerCase()] = true;
    }
    for (let preset of AUTO_PASTE_TITLE_PRESETS) {
      let label = (current[String(preset).toLowerCase()] ? "[x] " : "[ ] ") + String(preset);
      let item = new PopupMenu.PopupMenuItem(label);
      item.connect("activate", () => this._toggleAutoPasteTitle(preset));
      this.autoPasteItem.menu.addMenuItem(item);
    }
    this.autoPasteItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let disabled = new PopupMenu.PopupMenuItem((currentValues.length === 0 ? "[x] " : "[ ] ") + _("Disabled"));
    disabled.connect("activate", () => this._setAutoPasteTitles([]));
    this.autoPasteItem.menu.addMenuItem(disabled);
    this.autoPasteItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
    let custom = new PopupMenu.PopupIconMenuItem(_("Custom string..."), "document-edit-symbolic", St.IconType.SYMBOLIC);
    custom.connect("activate", () => this._configureAutoPaste());
    this.autoPasteItem.menu.addMenuItem(custom);
  },

  _updateAutoPasteItem: function() {
    if (this.autoPasteItem) {
      this.autoPasteItem.label.text = this._autoPasteLabel();
    }
    this._populateAutoPasteMenu();
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
    this.openaiCompatibleFlexProcessing = !Boolean(this.openaiCompatibleFlexProcessing);
    this.settings.setValue("openai-compatible-flex-processing", this.openaiCompatibleFlexProcessing);
    this._updateOpenAiFlexProcessingItem();
    let message = this.openaiCompatibleFlexProcessing
      ? _("OpenAI Flex processing enabled")
      : _("OpenAI Flex processing disabled");
    if (this._hasActiveRecordingState()) {
      this.lastMessage = message;
      this._updatePanel();
      return;
    }
    this._setStatus("ready", message, this.lastTranscript);
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
    if (!this.activeLanguageExplicit || (current !== primary && current !== secondary)) {
      this.activeLanguage = primary;
    }
  },

  _onLanguageSettingsChanged: function() {
    this.activeLanguageExplicit = false;
    this._syncActiveLanguage();
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
    startPrimary.connect("activate", () => this._startWithLanguage(primary, true));
    this.languageItem.menu.addMenuItem(startPrimary);

    let startSecondary = new PopupMenu.PopupIconMenuItem(_("Start secondary: ") + secondary, "media-record-symbolic", St.IconType.SYMBOLIC);
    startSecondary.connect("activate", () => this._startWithLanguage(secondary, true));
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
      [_("Cancel recording"), this._formatKeybinding(this.cancelKeybinding)],
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
    let configure = new PopupMenu.PopupIconMenuItem(_("Configure shortcuts"), "preferences-desktop-keyboard-symbolic", St.IconType.SYMBOLIC);
    configure.connect("activate", () => this._openShortcutSettings());
    this.shortcutItem.menu.addMenuItem(configure);
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

  _openShortcutSettings: function() {
    this._openAppletSettings(_("Opened Cinnamon shortcut settings"));
  },

  _toggleRecording: function() {
    if (this.isCommandRunning) {
      if (this.autoRelisten && this.notificationSessionActive) {
        this.autoRelistenManualStopRequested = true;
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this._setStatus("processing", _("Stopping Auto Relisten..."), this.lastTranscript);
      }
      return;
    }
    if (!this._ensureVoiceModelCompatibleWithCurrentLanguage(true)) {
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
    this.isCommandRunning = true;
    this._setStatus("processing", _("Working..."), "");
    this._spawnJson(this._baseArgs("toggle"), (payload) => {
      this.isCommandRunning = false;
      this._applyPayload(payload);
    });
  },

  _restartApplet: function() {
    this._setStatus("processing", _("Restarting applet..."), this.lastTranscript);
    try {
      Extension.reloadExtension(UUID, Extension.Type.APPLET);
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Could not restart applet: ") + String(err), this.lastTranscript);
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
    this._spawnJson(this._statusArgs(), (payload) => {
      try {
        this._applyPayload(payload, statusRefreshToken);
      } finally {
        this._statusCommandRunning = false;
      }
    }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS });
  },

  _cancelRecording: function() {
    if (this.isCommandRunning) {
      this.autoTranscribeRecordingKey = "";
      this.cancelPendingWhileCommandRunning = true;
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatus("processing", _("Stopping Auto Relisten..."), this.lastTranscript);
      return;
    }
    this.isCommandRunning = true;
    this.autoTranscribeRecordingKey = "";
    this.cancelPendingWhileCommandRunning = false;
    this.autoRelistenPending = false;
    this.autoRelistenPendingToken = "";
    this.autoRelistenManualStopRequested = true;
    this._setStatus("processing", _("Cancelling..."), this.lastTranscript);
    this._spawnJson(this._cancelArgs(), (payload) => {
      this.isCommandRunning = false;
      this._applyPayload(payload);
    });
  },

  _runDoctor: function(startupCheck) {
    if (this._doctorCommandRunning) {
      if (!startupCheck) {
        this._setDoctorSummary(_("Doctor: already running"));
        this._setStatus(this._hasActiveRecordingState() ? this.status : "ready", _("Doctor: already running"), this.lastTranscript);
      }
      return;
    }
    this._doctorCommandRunning = true;
    if (!startupCheck) {
      this._setDoctorSummary(_("Doctor: checking..."));
      this._setStatus(this._hasActiveRecordingState() ? this.status : "processing", _("Doctor: checking..."), this.lastTranscript);
    }
    this._spawnJson(this._doctorArgs(), (payload) => {
      try {
        if (payload.error) {
          let message = _("Doctor failed: ") + payload.error;
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
      } finally {
        this._doctorCommandRunning = false;
      }
    }, { timeoutMs: DOCTOR_COMMAND_TIMEOUT_MS });
  },

  _applyDoctorPayload: function(payload, startupCheck) {
    let configured = payload.configured || {};
    let summary = this._doctorSummary(payload);
    this._setDoctorSummary(summary);
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
      this._presentDoctorResult(message, true, Boolean(startupCheck));
      return;
    }
    let warnings = configured.warnings || [];
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
    for (let check of payload.checks || []) {
      if (!check.ok) {
        missing.push(check.name);
      }
    }
    if (payload.ok) {
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
    this.doctorSummaryText = String(message || "");
    if (this.doctorSummaryItem) {
      this.doctorSummaryItem.label.text = this.doctorSummaryText || _("Doctor: not checked");
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
    return (payload.ok ? _("Doctor: ready - ") : _("Doctor: setup needed - ")) + rows.join(", ");
  },

  _doctorSectionText: function(label, section) {
    section = section || {};
    return label + " " + (section.ok ? "OK" : "FAIL");
  },

  _openAppletSettings: function() {
    let openedMessage = arguments.length > 0 ? String(arguments[0] || "") : _("Opened Cinnamon applet settings");
    if (!GLib.find_program_in_path("xlet-settings")) {
      this._setStatus("error", _("xlet-settings command not found"), this.lastTranscript);
      return;
    }
    let args = ["xlet-settings", "applet", UUID];
    let instanceId = String(this.instanceId || "").trim();
    if (instanceId !== "") {
      args.push("--id", instanceId);
    }
    Util.spawn(args);
    this._setStatus("ready", openedMessage, this.lastTranscript);
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

  _openFile: function(path, successMessage) {
    try {
      if (!GLib.file_test(path, GLib.FileTest.EXISTS)) {
        throw new Error("file is not available: " + path);
      }
      this._openUri(GLib.filename_to_uri(path, null), successMessage);
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Could not open file: ") + err.message, this.lastTranscript);
    }
  },

  _openProfanityFilterList: function() {
    this._setStatus("processing", _("Preparing profanity replacement list..."), this.lastTranscript);
    this._spawnJson(this._profanityFilterDocumentArgs(), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let path = String(payload.path || "");
      if (path === "") {
        this._setStatus("error", _("Profanity replacement list was not generated"), this.lastTranscript);
        return;
      }
      this._openFile(path, _("Opened profanity replacement list: ") + String(payload.entries || 0));
    });
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
      this._setStatus("done", _("Saved diagnostics"), this.lastTranscript);
    });
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
    if (GLib.find_program_in_path("gnome-terminal")) {
      return ["gnome-terminal", "--title=" + terminalTitle, "--", "bash", "-lc", command];
    }
    if (GLib.find_program_in_path("x-terminal-emulator")) {
      return ["x-terminal-emulator", "-e", "bash", "-lc", command];
    }
    if (GLib.find_program_in_path("xterm")) {
      return ["xterm", "-T", terminalTitle, "-e", "bash", "-lc", command];
    }
    return [];
  },

  _runTerminalWorkflow: function(title, command, openedMessage) {
    let terminalArgs = this._terminalCommandArgs(title, command);
    if (terminalArgs.length === 0) {
      this._setStatus("error", _("No supported terminal found"), this.lastTranscript);
      this._notify(_("No supported terminal found"), _("Install gnome-terminal, x-terminal-emulator, or xterm."), true);
      return false;
    }
    try {
      Util.spawn(this._coerceSpawnArgs(terminalArgs));
      this._setStatus("processing", openedMessage, this.lastTranscript);
      return true;
    } catch (err) {
      global.logError(err);
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
    return script.join("; ");
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
      "if command -v ollama >/dev/null 2>&1; then ollama serve >/tmp/speed-of-cinnamon-ollama.log 2>&1 & sleep 2 || true; fi",
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
    let opened = false;
    try {
      opened = this._runTerminalWorkflow(_("Install Ollama"), this._installOllamaRuntimeCommand(), _("Ollama install terminal opened"));
    } catch (err) {
      global.logError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not start install terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start install terminal"), safeError, true);
      return;
    }
    if (opened && openChooserAfterInstall) {
      this._watchOllamaInstallThenChoose();
    }
  },

  _uninstallOllamaRuntime: function() {
    try {
      this._runTerminalWorkflow(_("Uninstall Ollama"), this._uninstallOllamaRuntimeCommand(), _("Ollama uninstall terminal opened"));
    } catch (err) {
      global.logError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not start uninstall terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start uninstall terminal"), safeError, true);
    }
  },

  _runBasicSetup: function() {
    try {
      this._runTerminalWorkflow(_("Speed of Cinnamon basic setup"), this._basicSetupCommand(), _("Basic setup terminal opened"));
    } catch (err) {
      global.logError(err);
      let safeError = this._sanitizeErrorMessage(String(err));
      this._setStatus("error", _("Could not start setup terminal: ") + safeError, this.lastTranscript);
      this._notify(_("Could not start setup terminal"), safeError, true);
    }
  },

  _selectBenchmarkAudioFile: function() {
    if (!GLib.find_program_in_path("zenity")) {
      this._setStatus("error", _("Install zenity to choose a benchmark audio file"), this.lastTranscript);
      return;
    }
    this._setStatus("processing", _("Choose benchmark audio file..."), this.lastTranscript);
    this._spawnText(this._benchmarkAudioFileDialogArgs(), (output) => {
      let audioPath = String(output || "").trim();
      if (audioPath === "") {
        this._setStatus("ready", _("Benchmark cancelled"), this.lastTranscript);
        return;
      }
      this._benchmarkDownloadedModels(audioPath);
    }, { timeoutMs: 0 });
  },

  _benchmarkDownloadedModels: function(audioPath) {
    this._setStatus("processing", _("Benchmarking downloaded models..."), this.lastTranscript);
    this._spawnJson(this._benchmarkArgs(audioPath), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let results = Array.isArray(payload.results) ? payload.results : [];
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, JSON.stringify(payload, null, 2));
      let fastest = String(payload.fastest_model || "").trim();
      let message = String(payload.message || _("Benchmark complete"));
      if (fastest !== "") {
        message += "; " + _("fastest: ") + fastest;
      }
      this._setStatus("done", message + _("; copied results") + " (" + String(results.length) + ")", this.lastTranscript);
    }, { timeoutMs: BENCHMARK_COMMAND_TIMEOUT_MS });
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
    let defaultItem = this._selectionMenuItem(defaultLabel);
    defaultItem.connect("activate", () => this._selectInputSource("", _("system default")));
    this.inputSourceItem.menu.addMenuItem(defaultItem);

    if (message) {
      this.inputSourceItem.menu.addMenuItem(this._selectionInfoItem(message));
      return;
    }
    if (!sources || sources.length === 0) {
      this.inputSourceItem.menu.addMenuItem(this._selectionInfoItem(_("No input sources found")));
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
      let itemLabel = (current === sourceName ? "[x] " : "[ ] ") + this._shortMenuText(label + " - " + sourceName, 96);
      let item = this._selectionMenuItem(itemLabel);
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

  _selectDefaultInputSource: function() {
    this._selectInputSource("", _("system default"));
  },

  _refreshModelMenu: function() {
    if (!this._canMutateMenu(this.modelItem)) {
      return;
    }
    let refreshToken = {};
    this.modelMenuRefreshToken = refreshToken;
    this._populateModelMenu([], _("Loading voice models..."));
    this._spawnJson(this._modelsArgs(), (payload) => {
      if (this.modelMenuRefreshToken !== refreshToken || !this._canMutateMenu(this.modelItem)) {
        return;
      }
      if (payload.error) {
        this._populateModelMenu([], payload.error);
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      this._populateModelMenu(payload.models || []);
    });
  },

  _populateModelMenu: function(models, message) {
    if (!this._canMutateMenu(this.modelItem)) {
      return;
    }
    this.modelItem.menu.removeAll();

    let autoActive = String(this.transcriber || "auto") === "auto" && String(this.whisperModel || "") === "";
    let automatic = this._selectionMenuItem((autoActive ? "[x] " : "[ ] ") + _("Automatic voice model"));
    automatic.connect("activate", () => this._selectAutomaticVoiceBackend());
    this.modelItem.menu.addMenuItem(automatic);

    this.modelItem.menu.addMenuItem(this._selectionInfoItem(_("Active: ") + this._activeVoiceModelSummary()));

    let download = this._styleMenuItemLabel(
      new PopupMenu.PopupIconMenuItem(_("Download starter model") + ": " + this._starterVoiceModelName(), "folder-download-symbolic", St.IconType.SYMBOLIC)
    );
    download.connect("activate", () => this._downloadStarterModel());
    this.modelItem.menu.addMenuItem(download);

    let openFolder = new PopupMenu.PopupIconMenuItem(_("Open GGML model folder"), "folder-symbolic", St.IconType.SYMBOLIC);
    openFolder.connect("activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "whisper.cpp"]), _("Opened model folder"));
    });
    this.modelItem.menu.addMenuItem(openFolder);

    let openCt2Folder = new PopupMenu.PopupIconMenuItem(_("Open CTranslate2 model folder"), "folder-symbolic", St.IconType.SYMBOLIC);
    openCt2Folder.connect("activate", () => {
      this._openFolder(GLib.build_filenamev([GLib.get_user_data_dir(), "speed-of-cinnamon", "models", "ctranslate2"]), _("Opened model folder"));
    });
    this.modelItem.menu.addMenuItem(openCt2Folder);

    this.modelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (message) {
      this.modelItem.menu.addMenuItem(this._selectionInfoItem(message));
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
      if (String(model.model_format || "") === "ctranslate2" || String(model.backend || "") === "faster-whisper") {
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
  },

  _populateExternalApiVoiceMenu: function(parentMenu) {
    let active = String(this.transcriber || "") === "openai-compatible";
    let model = String(this.openaiCompatibleModel || "").trim();
    let url = String(this.openaiCompatibleUrl || "").trim();
    let useItem = new PopupMenu.PopupIconMenuItem((active ? "[x] " : "[ ] ") + _("Use OpenAI-compatible API"), "network-server-symbolic", St.IconType.SYMBOLIC);
    this._styleMenuItemLabel(useItem);
    useItem.connect("activate", () => this._openExternalApiEnvEditor("voice"));
    parentMenu.addMenuItem(useItem);

    parentMenu.addMenuItem(this._selectionInfoItem(_("Endpoint: ") + (url || _("not configured"))));
    parentMenu.addMenuItem(this._selectionInfoItem(_("Model: ") + (model || _("not configured"))));
    parentMenu.addMenuItem(this._selectionInfoItem(_("Configure URL, model, and optional API key in applet settings.")));
  },

  _addModelMenuEntry: function(model, parentMenu) {
    let name = String(model.name || "");
    if (name === "") {
      return;
    }
    let downloaded = Boolean(model.downloaded);
    let current = downloaded && this.whisperModel && String(model.path || "") === String(this.whisperModel);
    let compatible = this._voiceModelSupportsCurrentLanguage(model);
    let label = (current ? "[x] " : "[ ] ") + name + " (" + String(model.size || "?") + ")";
    if (!compatible) {
      label += _(" - English only");
    }
    if (!downloaded) {
      label += _(" - not downloaded");
    }
    let entry = new PopupMenu.PopupSubMenuMenuItem(label);
    this._styleMenuItemLabel(entry);
    this._styleSelectionSubmenu(entry);
    parentMenu.addMenuItem(entry);

    entry.menu.addMenuItem(this._selectionInfoItem(String(model.description || "")));
    if (!compatible) {
      entry.menu.addMenuItem(this._selectionInfoItem(_("Not suitable for primary language: ") + this._voiceModelLanguage()));
    }

    if (downloaded) {
      let useItem = new PopupMenu.PopupIconMenuItem(_("Use this model"), "emblem-ok-symbolic", St.IconType.SYMBOLIC);
      this._styleMenuItemLabel(useItem);
      useItem.setSensitive(!current && compatible);
      useItem.connect("activate", () => this._selectVoiceModel(model));
      entry.menu.addMenuItem(useItem);

      let removeItem = new PopupMenu.PopupIconMenuItem(_("Remove model"), "edit-delete-symbolic", St.IconType.SYMBOLIC);
      removeItem.connect("activate", () => this._removeVoiceModel(model));
      entry.menu.addMenuItem(removeItem);
      return;
    }

    let downloadItem = this._styleMenuItemLabel(new PopupMenu.PopupIconMenuItem(_("Download model"), "folder-download-symbolic", St.IconType.SYMBOLIC));
    downloadItem.connect("activate", () => this._downloadVoiceModel(model));
    entry.menu.addMenuItem(downloadItem);
  },

  _isEnglishLanguage: function(language) {
    let value = String(language || "").trim().toLowerCase().replace("_", "-");
    return value === "" || value === "en" || value === "eng" || value === "english" || value.indexOf("en-") === 0;
  },

  _voiceModelSupportsCurrentLanguage: function(model) {
    let languages = model.languages || [];
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
      return GLib.path_get_basename(model);
    }
    if (backend === "command") return _("custom command");
    if (backend === "whisper") return _("Whisper command");
    if (backend === "openai-compatible") {
      return _("External API: ") + (String(this.openaiCompatibleModel || "").trim() || _("not configured"));
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
    this.transcriber = "auto";
    this.whisperModel = "";
    this.settings.setValue("transcriber", this.transcriber);
    this.settings.setValue("whisper-model", this.whisperModel);
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
    if (this.isCommandRunning) {
      return;
    }
    let name = String(model.name || this._starterVoiceModelName());
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
    let name = String(model.name || "voice model");
    let backend = String(model.backend || "whisper-cpp");
    if (path === "") {
      return;
    }
    if (!this._voiceModelSupportsCurrentLanguage(model)) {
      this._setStatus("error", _("English-only model cannot transcribe primary language: ") + this._voiceModelLanguage(), this.lastTranscript);
      return;
    }
    this.transcriber = backend;
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
    this._setStatus("ready", _("Voice model: automatic"), this.lastTranscript);
  },

  _externalApiEnvPath: function() {
    return GLib.build_filenamev([GLib.get_user_config_dir(), "speed-of-cinnamon", "external-api.env"]);
  },

  _externalApiEnvValue: function(value, fallback) {
    let normalized = String(value || "").trim();
    if (normalized === LEGACY_OPENAI_COMPATIBLE_URL) {
      return DEFAULT_OPENAI_COMPATIBLE_URL;
    }
    return normalized || String(fallback || "");
  },

  _externalApiEnvContent: function() {
    let safeOpenAiCompatibleUrl = this._coerceCliTextArg(this._externalApiEnvValue(this.openaiCompatibleUrl, DEFAULT_OPENAI_COMPATIBLE_URL), "openai-compatible URL").trim();
    let safeOpenAiCompatibleModel = this._coerceCliTextArg(this._externalApiEnvValue(this.openaiCompatibleModel, DEFAULT_OPENAI_COMPATIBLE_MODEL), "openai-compatible model").trim();
    let safeOpenAiCompatibleTextModel = this._coerceCliTextArg(this._externalApiEnvValue(this.openaiCompatibleTextModel, DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL), "openai-compatible text model").trim();
    let safeOpenAiCompatibleApiKey = this._coerceCliTextArg(this.openaiCompatibleApiKey || "", "openai-compatible API key").trim();
    return [
      "OPENAI_COMPATIBLE_URL=" + safeOpenAiCompatibleUrl,
      "OPENAI_COMPATIBLE_STT_MODEL=" + safeOpenAiCompatibleModel,
      "OPENAI_COMPATIBLE_TEXT_MODEL=" + safeOpenAiCompatibleTextModel,
      "OPENAI_COMPATIBLE_API_KEY=" + safeOpenAiCompatibleApiKey,
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
    GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
    let info = this._externalApiEnvFileInfo(path, true);
    if (info) {
      Gio.File.new_for_path(path).set_attribute_uint32("unix::mode", 0o600, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
    }
    Gio.File.new_for_path(path).replace_contents(
      ByteArray.fromString(text),
      null,
      false,
      Gio.FileCreateFlags.PRIVATE | Gio.FileCreateFlags.REPLACE_DESTINATION,
      null
    );
    this._externalApiEnvFileInfo(path, false);
    Gio.File.new_for_path(path).set_attribute_uint32("unix::mode", 0o600, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, null);
  },

  _writeExternalApiEnvFile: function() {
    let path = this._externalApiEnvPath();
    try {
      this._writeExternalApiEnvFileContents(path, this._externalApiEnvContent());
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("External API config file could not be written"), this.lastTranscript);
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
    if (GLib.file_test(this._externalApiEnvPath(), GLib.FileTest.EXISTS)) {
      this._ensureExternalApiEnvFile();
      this._applyExternalApiEnvFile(false);
      return;
    }
    if (changed) {
      this._ensureExternalApiEnvFile();
    }
  },

  _ensureExternalApiEnvFile: function() {
    let path = this._externalApiEnvPath();
    try {
      GLib.mkdir_with_parents(GLib.path_get_dirname(path), 0o700);
      if (!GLib.file_test(path, GLib.FileTest.EXISTS)) {
        this._writeExternalApiEnvFileContents(path, this._externalApiEnvContent());
      } else {
        this._migrateExternalApiEnvFile(path);
      }
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("External API config file could not be written"), this.lastTranscript);
    }
    return path;
  },

  _migrateExternalApiEnvFile: function(path) {
    let text;
    try {
      text = this._readExternalApiEnvFile(path);
    } catch (err) {
      global.logError(err);
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
    if (migrated !== text) {
      try {
        this._writeExternalApiEnvFileContents(path, migrated);
      } catch (err) {
        global.logError(err);
        this._setStatus("error", _("External API config file could not be written"), this.lastTranscript);
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
      global.logError(err);
      return false;
    }
    if (text === "") {
      return false;
    }
    let values = this._parseExternalApiEnvText(text);
    let url = this._coerceCliTextArg(this._externalApiEnvValue(values.OPENAI_COMPATIBLE_URL || "", DEFAULT_OPENAI_COMPATIBLE_URL), "openai-compatible URL").trim();
    let model = this._coerceCliTextArg(this._externalApiEnvValue(values.OPENAI_COMPATIBLE_STT_MODEL || values.OPENAI_COMPATIBLE_MODEL || "", DEFAULT_OPENAI_COMPATIBLE_MODEL), "openai-compatible model").trim();
    let textModel = this._coerceCliTextArg(this._externalApiEnvValue(values.OPENAI_COMPATIBLE_TEXT_MODEL || "", DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL), "openai-compatible text model").trim();
    let apiKey = this._coerceCliTextArg(values.OPENAI_COMPATIBLE_API_KEY || "", "openai-compatible API key").trim();
    if (url !== "") {
      this.openaiCompatibleUrl = url;
      this.settings.setValue("openai-compatible-url", this.openaiCompatibleUrl);
    }
    if (model !== "") {
      this.openaiCompatibleModel = model;
      this.settings.setValue("openai-compatible-model", this.openaiCompatibleModel);
    }
    this.openaiCompatibleTextModel = textModel;
    this.settings.setValue("openai-compatible-text-model", this.openaiCompatibleTextModel);
    this.openaiCompatibleApiKey = apiKey;
    this.settings.setValue("openai-compatible-api-key", this.openaiCompatibleApiKey);
    if (showStatus) {
      this._setStatus("ready", _("External API config loaded: ") + (this.openaiCompatibleModel || _("not configured")), this.lastTranscript);
    }
    return true;
  },

  _clearExternalApiEnvMonitor: function() {
    if (this.externalApiEnvMonitor) {
      try {
        this.externalApiEnvMonitor.cancel();
      } catch (err) {
        global.logError(err);
      }
      this.externalApiEnvMonitor = null;
    }
  },

  _watchExternalApiEnvFile: function(path) {
    this._clearExternalApiEnvMonitor();
    try {
      let file = Gio.File.new_for_path(path);
      this.externalApiEnvMonitor = file.monitor_file(Gio.FileMonitorFlags.NONE, null);
      this.externalApiEnvMonitor.connect("changed", (monitor, fileObj, otherFile, eventType) => {
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
    } catch (err) {
      global.logError(err);
    }
  },

  _openExternalApiEnvEditor: function(target) {
    this.externalApiEnvApplyTarget = target || "voice";
    let path = this._ensureExternalApiEnvFile();
    if (this._applyExternalApiEnvFile(false)) {
      this._applyExternalApiEnvTarget(this.externalApiEnvApplyTarget);
    }
    this._watchExternalApiEnvFile(path);
    this._openFile(path, _("Opened External API .env"));
  },

  _applyExternalApiEnvTarget: function(target) {
    if (target === "text") {
      this.postProcessBackend = "openai-compatible";
      this.settings.setValue("post-process-backend", this.postProcessBackend);
      this._refreshTextModelMenuForBackend("openai-compatible");
      this._setStatus("ready", _("Text polishing: OpenAI-compatible API"), this.lastTranscript);
      return;
    }
    this._selectExternalApiVoiceBackend();
  },

  _selectExternalApiVoiceBackend: function() {
    this.transcriber = "openai-compatible";
    this.whisperModel = "";
    this.settings.setValue("transcriber", this.transcriber);
    this.settings.setValue("whisper-model", this.whisperModel);
    this._refreshModelMenu();
    let model = String(this.openaiCompatibleModel || "").trim();
    if (model === "") {
      this._setStatus("error", _("External API speech model is not configured"), this.lastTranscript);
      return;
    }
    this._setStatus("ready", _("Voice model: External API ") + model, this.lastTranscript);
  },

  _refreshTextModelMenu: function() {
    this._refreshTextModelMenuForBackend("");
  },

  _refreshTextModelMenuForBackend: function(backendOverride) {
    if (!this._canMutateMenu(this.textModelItem)) {
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
    this._spawnJson(this._textModelsArgs(backendOverride), (payload) => {
      if (this.textModelMenuRefreshToken !== refreshToken || !this._canMutateMenu(this.textModelItem)) {
        return;
      }
      if (payload.error) {
        this._populateTextModelMenu([], payload.error, provider);
        return;
      }
      this._populateTextModelMenu(payload.models || [], payload.available === false ? payload.message : "", payload.backend || provider);
    });
  },

  _populateTextModelMenu: function(models, message, provider) {
    if (!this._canMutateMenu(this.textModelItem)) {
      return;
    }
    this.textModelItem.menu.removeAll();
    let backend = String(this.postProcessBackend || "none");
    let activeProvider = String(provider || (backend === "openai-compatible" ? "openai-compatible" : "ollama"));

    let disabled = this._selectionMenuItem((backend === "none" ? "[x] " : "[ ] ") + _("Disabled"));
    disabled.connect("activate", () => this._selectTextModelBackend("none", "", _("Text polishing disabled")));
    this.textModelItem.menu.addMenuItem(disabled);

    let custom = this._selectionMenuItem((backend === "command" || backend === "custom" ? "[x] " : "[ ] ") + _("Custom command"));
    custom.connect("activate", () => this._selectTextModelBackend("command", "", _("Text polishing: custom command")));
    this.textModelItem.menu.addMenuItem(custom);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let ollama = this._selectionMenuItem((backend === "ollama" ? "[x] " : "[ ] ") + _("Ollama local model"));
    ollama.connect("activate", () => this._activateOllamaTextModelFlow());
    this.textModelItem.menu.addMenuItem(ollama);

    let openaiCompatible = this._selectionMenuItem((backend === "openai-compatible" ? "[x] " : "[ ] ") + _("OpenAI-compatible API"));
    openaiCompatible.connect("activate", () => this._openExternalApiEnvEditor("text"));
    this.textModelItem.menu.addMenuItem(openaiCompatible);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    let reset = this._selectionMenuItem(_("Reset polishing defaults"));
    reset.connect("activate", () => this._resetTextPolishingDefaults());
    this.textModelItem.menu.addMenuItem(reset);

    this.textModelItem.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

    if (message) {
      this.textModelItem.menu.addMenuItem(this._selectionInfoItem(message));
      return;
    }
    if (!models || models.length === 0) {
      let selectedOllamaModel = String(this.ollamaModel || "").trim();
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
      this._addTextModelMenuEntry(model, activeProvider);
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
    let name = String(model.name || "");
    if (name === "") {
      return;
    }
    let provider = String(backend || "ollama");
    let currentModel = provider === "openai-compatible" ? String(this.openaiCompatibleTextModel || this.openaiCompatibleModel || "") : String(this.ollamaModel || "");
    let current = String(this.postProcessBackend || "") === provider && currentModel === name;
    let details = String(model.description || model.size_label || "");
    let label = (current ? "[x] " : "[ ] ") + name;
    if (details !== "") {
      label += " (" + details + ")";
    }
    let item = this._selectionMenuItem(this._shortMenuText(label, 96));
    item.connect("activate", () => this._selectTextModelBackend(provider, name, _("Text model: ") + name));
    this.textModelItem.menu.addMenuItem(item);
  },

  _selectTextModelBackend: function(backend, model, message) {
    this.postProcessBackend = String(backend || "none");
    this.settings.setValue("post-process-backend", this.postProcessBackend);
    if (this.postProcessBackend === "ollama") {
      this.ollamaModel = String(model || "");
      this.settings.setValue("ollama-model", this.ollamaModel);
    }
    if (this.postProcessBackend === "openai-compatible") {
      this.openaiCompatibleTextModel = String(model || "");
      this.settings.setValue("openai-compatible-text-model", this.openaiCompatibleTextModel);
      if (!this._writeExternalApiEnvFile()) {
        this._refreshTextModelMenu();
        return;
      }
    }
    this._refreshTextModelMenu();
    this._setStatus("ready", message, this.lastTranscript);
  },

  _activateOllamaTextModelFlow: function() {
    if (!GLib.find_program_in_path("zenity")) {
      this._setStatus("error", _("Install zenity to choose an Ollama model"), this.lastTranscript);
      return;
    }
    this._setStatus("processing", _("Checking Ollama..."), this.lastTranscript);
    this._spawnJson(this._textModelsArgs("ollama"), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        this._notify(_("Could not check Ollama"), String(payload.error), true);
        return;
      }
      let models = Array.isArray(payload.models) ? payload.models : [];
      if (payload.available === false) {
        this._setStatus("processing", _("Ollama is not installed or not reachable; opening installer..."), this.lastTranscript);
        this._installOllamaRuntime(true);
        return;
      }
      if (models.length === 0) {
        this._promptInstallOllamaTextModel();
        return;
      }
      this._promptChooseOllamaTextModel(models);
    });
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
    for (let model of (models || [])) {
      let name = this._coerceCliTextArg(model.name || "", "ollama model");
      if (name.trim() === "") {
        continue;
      }
      let details = String(model.description || model.size_label || "").trim();
      let label = details ? name + " (" + details + ")" : name;
      args.push("SELECT:" + name, label);
    }
    return args;
  },

  _chooseOllamaTextModel: function() {
    if (!GLib.find_program_in_path("zenity")) {
      this._setStatus("error", _("Install zenity to choose an Ollama model"), this.lastTranscript);
      return;
    }
    this._setStatus("processing", _("Loading Ollama text models..."), this.lastTranscript);
    this._spawnJson(this._textModelsArgs("ollama"), (payload) => {
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        this._notify(_("Could not load Ollama models"), String(payload.error), true);
        return;
      }
      let models = Array.isArray(payload.models) ? payload.models : [];
      if (models.length === 0) {
        if (payload.available === false && payload.message) {
          this._setStatus("processing", payload.message + "; " + _("opening installer..."), this.lastTranscript);
          this._installOllamaRuntime(true);
          return;
        }
        this._promptInstallOllamaTextModel();
        return;
      }
      this._promptChooseOllamaTextModel(models);
    });
  },

  _promptChooseOllamaTextModel: function(models) {
    this._setStatus("processing", _("Choose Ollama text model..."), this.lastTranscript);
    this._spawnText(this._ollamaModelChoiceArgs(models), (output) => {
      let choice = String(output || "").trim();
      if (choice === "") {
        this._setStatus("ready", _("Ollama model selection cancelled"), this.lastTranscript);
        return;
      }
      if (choice === "ADD") {
        this._promptInstallOllamaTextModel();
        return;
      }
      if (choice.indexOf("SELECT:") === 0) {
        let model = choice.slice("SELECT:".length).trim();
        if (model !== "") {
          this._selectTextModelBackend("ollama", model, _("Text model: ") + model);
        }
      }
    }, { timeoutMs: 0 });
  },

  _promptInstallOllamaTextModel: function() {
    if (!GLib.find_program_in_path("zenity")) {
      this._setStatus("error", _("Install zenity to enter an Ollama model name"), this.lastTranscript);
      return;
    }
    this._setStatus("processing", _("Choose Ollama text model..."), this.lastTranscript);
    this._spawnText(this._ollamaModelPromptArgs(), (output) => {
      let model = String(output || "").trim();
      if (model === "") {
        this._setStatus("ready", _("Ollama model installation cancelled"), this.lastTranscript);
        return;
      }
      this._installOllamaTextModel(model);
    }, { timeoutMs: 0 });
  },

  _installOllamaTextModel: function(model) {
    if (this.isCommandRunning) {
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Installing Ollama model: ") + model, this.lastTranscript);
    this._spawnJson(this._installTextModelArgs(model), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        this._notify(_("Ollama model installation failed"), String(payload.error), true);
        this._refreshTextModelMenu();
        return;
      }
      let installedModel = String(payload.model || model);
      let message = payload.message || _("Ollama model installed");
      this._selectTextModelBackend("ollama", installedModel, message);
      this._notify(_("Ollama model installed"), installedModel, false);
    }, { timeoutMs: BENCHMARK_COMMAND_TIMEOUT_MS });
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

  _listAllTranscripts: function() {
    if (this.isCommandRunning) {
      return;
    }
    if (!GLib.find_program_in_path("zenity")) {
      let message = _("Install zenity to show the transcript list without writing a plaintext file.");
      this._setStatus("error", message, this.lastTranscript);
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
    let dialog = new ModalDialog.ModalDialog();
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
    dialog.contentLayout.add_child(new St.Label({ text: _("List all transcripts?"), x_expand: true }));
    dialog.contentLayout.add_child(new St.Label({
      text: _("This shows complete transcript contents in a plaintext window. Continue only if your screen and session are trusted."),
      x_expand: true
    }));
    dialog.setButtons([
      {
        label: _("Cancel"),
        key: Clutter.KEY_Escape,
        action: function() {
          dialog.close();
          this._setStatus("ready", _("Transcript list cancelled"), this.lastTranscript);
          complete(false);
        }.bind(this),
      },
      {
        label: _("Show transcripts"),
        action: function() {
          dialog.close();
          complete(true);
        }.bind(this),
      }
    ]);
    if (!dialog.open()) {
      this._setStatus("error", _("Transcript list confirmation could not be opened"), this.lastTranscript);
      this._notify(_("Speed of Cinnamon"), _("Transcript list confirmation could not be opened"), true);
      complete(false);
    }
  },

  _loadAllTranscriptsDocument: function() {
    this.isCommandRunning = true;
    this._setStatus("processing", _("Preparing transcript list..."), this.lastTranscript);
    this._spawnJson(this._allHistoryArgs(), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      let content = String(payload.content || "");
      if (content === "") {
        this._setStatus("error", _("Transcript list is empty"), this.lastTranscript);
        return;
      }
      this._showTranscriptsWindow(content, Number(payload.transcripts || 0), Boolean(payload.truncated));
    });
  },

  _showTranscriptsWindow: function(content, count, truncated) {
    let zenity = GLib.find_program_in_path("zenity");
    if (!zenity) {
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
      let process = Gio.Subprocess.new(
        args,
        Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
      );
      process.communicate_utf8_async(String(content || ""), null, (proc, result) => {
        try {
          proc.communicate_utf8_finish(result);
        } catch (err) {
          global.logError(err);
        }
      });
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
      let safeError = this._sanitizeErrorMessage(String(err && err.message ? err.message : err));
      this._setStatus("error", _("Could not open transcript list: ") + safeError, this.lastTranscript);
      this._notify(_("Could not open transcript list"), safeError, true);
    }
  },

  _exportAllTranscripts: function() {
    if (this.isCommandRunning) {
      return;
    }
    this.isCommandRunning = true;
    this._setStatus("processing", _("Exporting transcripts..."), this.lastTranscript);
    this._spawnJson(this._transcriptsExportArgs(), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this._setStatus("error", payload.error, this.lastTranscript);
        this._maybeWarnRejectedArtifactPassphrase(payload.error);
        return;
      }
      let path = String(payload.path || "");
      if (path === "") {
        this._setStatus("error", _("Transcript export path is empty"), this.lastTranscript);
        return;
      }
      if (payload.encrypted !== true || Boolean(payload.plaintext) || String(payload.encryption || "") === "off") {
        let message = _("Transcript export was not encrypted");
        this._setStatus("error", message, this.lastTranscript);
        this._notify(_("Speed of Cinnamon transcript export"), message, true);
        return;
      }
      let message = _("Exported encrypted transcript bundle: ") + path;
      this._setStatus("done", message, this.lastTranscript);
      this._notify(_("Speed of Cinnamon transcript export"), message, false);
      this._openFolder(GLib.path_get_dirname(path), _("Opened transcript export folder"));
    });
  },

  _cleanupCount: function(payload, dryRun) {
    if (dryRun) {
      return Number(payload.would_delete_transcripts || 0) + Number(payload.would_delete_recordings || 0) + Number(payload.would_delete_logs || 0);
    }
    return Number(payload.deleted_transcripts || 0) + Number(payload.deleted_recordings || 0) + Number(payload.deleted_logs || 0);
  },

  _cleanupPreviewText: function(payload) {
    let plannedPaths = Array.isArray(payload.would_delete_paths) ? payload.would_delete_paths : [];
    let failedPaths = Array.isArray(payload.failed_paths) ? payload.failed_paths : [];
    let skippedPaths = Array.isArray(payload.skipped_active_paths) ? payload.skipped_active_paths : [];
    let lines = [
      _("Clean all old files preview"),
      "",
      _("Files that would be deleted: ") + String(this._cleanupCount(payload, true)),
      _("Transcripts: ") + String(Number(payload.would_delete_transcripts || 0)),
      _("Recordings: ") + String(Number(payload.would_delete_recordings || 0)),
      _("Logs: ") + String(Number(payload.would_delete_logs || 0))
    ];
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
    let dialog = new ModalDialog.ModalDialog();
    dialog.contentLayout.add_child(new St.Label({ text: this._cleanupPreviewText(payload), x_expand: true }));
    dialog.setButtons([
      {
        label: _("Close"),
        key: Clutter.KEY_Escape,
        action: function() {
          dialog.close();
        }.bind(this),
      }
    ]);
    if (!dialog.open()) {
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
    return String(value || "")
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
    let customInstruction = String(this.postProcessPrompt || "").trim();
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
    this._setStatus("ready", _("Text polishing defaults restored"), this.lastTranscript);
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
      this._showCleanupPreviewDialog(payload);
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

  _settingsSnapshotForCli: function() {
    let snapshot = this._settingsSnapshot();
    for (let key in CLI_TEXT_SETTINGS) {
      if (Object.prototype.hasOwnProperty.call(CLI_TEXT_SETTINGS, key) && Object.prototype.hasOwnProperty.call(snapshot, key)) {
        snapshot[key] = this._coerceCliTextArg(snapshot[key], CLI_TEXT_SETTINGS[key]);
      }
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
    }, { inputText: JSON.stringify(this._settingsSnapshotForCli()) });
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
      this[prop] = this._coerceImportedSetting(key, settings[key], this[prop]);
      this.settings.setValue(key, this[prop]);
      applied++;
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
    this._updateOpenAiFlexProcessingItem();
    this.insertMethod = this._normalizeOutputMethod(this.insertMethod);
    this._populateOutputMethodMenu();
    this._populateTextOptionsMenu();
    this._updateAutoPasteItem();
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
    if (!this._isAllowedCliCommand(normalized[0])) {
      throw new Error("Backend command is not executable");
    }
    return normalized;
  },

  _coerceCliTextArg: function(value, fieldName) {
    let normalized = String(value || "");
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
    try {
      return this._coerceCliTextArg(value, IMPORT_TEXT_SETTINGS[key]);
    } catch (err) {
      global.logError(err);
      return fallback;
    }
  },

  _coerceImportedEnumSetting: function(value, allowedValues, fallback) {
    let normalized = String(value || "").trim();
    return allowedValues.indexOf(normalized) >= 0 ? normalized : fallback;
  },

  _isAllowedCliCommand: function(command) {
    let value = String(command || "").trim();
    if (value === "") {
      return false;
    }
    if (value.indexOf("/") >= 0) {
      if (value.charAt(0) !== "/") {
        return false;
      }
      return GLib.file_test(value, GLib.FileTest.IS_EXECUTABLE);
    }
    return GLib.find_program_in_path(value) !== null;
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
    return this._coerceCliTextArg(this.openaiCompatibleApiKey || "", "openai-compatible API key").trim();
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

  _spawnJsonWithBackendEnvironment: function(args, env, callback, inputText) {
    env = env || {};
    let hasInput = inputText !== null && inputText !== undefined;
    let flags = Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_PIPE | Gio.SubprocessFlags.SEARCH_PATH;
    if (hasInput) {
      flags |= Gio.SubprocessFlags.STDIN_PIPE;
    }
    let launcher = new Gio.SubprocessLauncher({
      flags: flags,
    });
    for (let key in env) {
      if (Object.prototype.hasOwnProperty.call(env, key)) {
        launcher.setenv(key, String(env[key] || ""), true);
      }
    }
    let process;
    try {
      process = launcher.spawnv(args);
    } catch (err) {
      throw err;
    }
    process.communicate_utf8_async(hasInput ? String(inputText || "") : null, null, (subprocess, result) => {
      try {
        let [, stdout] = subprocess.communicate_utf8_finish(result);
        callback(String(stdout || ""));
      } catch (err) {
        global.logError(err);
        callback("");
      }
    });
  },

  _isStatusCommandArgs: function(args) {
    if (!Array.isArray(args)) {
      return false;
    }
    for (let i = 0; i < args.length; i++) {
      if (String(args[i] || "") === "status") {
        return true;
      }
    }
    return false;
  },

  _spawnJson: function(args, callback, options) {
    options = options || {};
    let timeoutId = 0;
    let done = false;
    let normalizedArgs;
    let callbackFn = typeof callback === "function" ? callback : function() {};
    let applet = this;
    let spawnGeneration = this.spawnGeneration;

    const finalize = function(payload) {
      if (done) {
        return;
      }
      done = true;
      if (timeoutId) {
        Mainloop.source_remove(timeoutId);
        timeoutId = 0;
      }
      if (applet.appletRemoved || applet.spawnGeneration !== spawnGeneration) {
        return;
      }
      try {
        callbackFn(payload || {});
      } catch (err) {
        global.logError(err);
      }
    };

    try {
      normalizedArgs = this._coerceSpawnArgs(args);
      if (!this._isStatusCommandArgs(normalizedArgs)) {
        this._statusRefreshToken++;
      }
      let timeoutMs = Object.prototype.hasOwnProperty.call(options, "timeoutMs")
        ? Number(options.timeoutMs)
        : CLI_COMMAND_TIMEOUT_MS;
      if (timeoutMs > 0) {
        timeoutId = Mainloop.timeout_add(Math.max(250, timeoutMs), () => {
          finalize({ status: "error", error: "Backend command timed out" });
          return false;
        });
      }
      this._runWithBackendEnvironment(this._shouldExposeOpenAiCompatibleApiKeyToBackend(normalizedArgs), (backendEnv) => {
        let handleOutput = (stdout) => {
          if (done) {
            return;
          }
          finalize(this._parseSpawnOutput(stdout));
        };
        let inputText = Object.prototype.hasOwnProperty.call(options, "inputText")
          ? String(options.inputText || "")
          : null;
        if (backendEnv || inputText !== null) {
          this._spawnJsonWithBackendEnvironment(normalizedArgs, backendEnv || {}, handleOutput, inputText);
        } else {
          Util.spawn_async(normalizedArgs, handleOutput);
        }
      });
    } catch (err) {
      finalize({ status: "error", error: String(err) });
    }
  },

  _spawnText: function(args, callback, options) {
    options = options || {};
    let timeoutId = 0;
    let done = false;
    let callbackFn = typeof callback === "function" ? callback : function() {};
    let applet = this;
    let spawnGeneration = this.spawnGeneration;

    const finalize = function(output) {
      if (done) {
        return;
      }
      done = true;
      if (timeoutId) {
        Mainloop.source_remove(timeoutId);
        timeoutId = 0;
      }
      if (applet.appletRemoved || applet.spawnGeneration !== spawnGeneration) {
        return;
      }
      try {
        callbackFn(String(output || ""));
      } catch (err) {
        global.logError(err);
      }
    };

    try {
      let normalizedArgs = this._coerceSpawnArgs(args);
      let timeoutMs = Object.prototype.hasOwnProperty.call(options, "timeoutMs")
        ? Number(options.timeoutMs)
        : CLI_COMMAND_TIMEOUT_MS;
      if (timeoutMs > 0) {
        timeoutId = Mainloop.timeout_add(Math.max(250, timeoutMs), () => {
          finalize("");
          return false;
        });
      }
      Util.spawn_async(normalizedArgs, (stdout) => {
        if (done) {
          return;
        }
        let output = String(stdout || "");
        if (utf8ByteLength(output) > MAX_SPAWN_TEXT_BYTES) {
          finalize("");
          return;
        }
        finalize(output);
      });
    } catch (err) {
      global.logError(err);
      finalize("");
    }
  },

  _applyPayload: function(payload, statusRefreshToken) {
    if (typeof statusRefreshToken === "number" && statusRefreshToken !== this._statusRefreshToken) {
      return;
    }
    if (typeof statusRefreshToken !== "number") {
      this._statusRefreshToken++;
    }
    let status = payload.status || (payload.error ? "error" : "idle");
    this._applyPayloadLanguage(payload);
    this._updateRecordingTiming(payload, status);
    this._applyMicrophoneLevel(payload.microphone_level, status);
    if (payload.error) {
      this.cancelPendingWhileCommandRunning = false;
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this._setStatus("error", payload.error, this.lastTranscript);
      this._maybeWarnRejectedArtifactPassphrase(payload.error);
      return;
    }
    let hasTranscript = typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript);
    if (payload.status === "done") {
      this._maybeWarnUnencryptedArtifactStorage(payload);
    }
    if (this.cancelPendingWhileCommandRunning && payload.status === "done") {
      this.cancelPendingWhileCommandRunning = false;
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
      this.autoRelistenManualStopRequested = true;
      this._setStatus("ready", _("Cancel applied; transcript not inserted"), this.lastTranscript);
      return;
    }
    if (
      this.cancelPendingWhileCommandRunning &&
      (payload.status === "recording" || payload.status === "recorded") &&
      !this.isCommandRunning
    ) {
      this.cancelPendingWhileCommandRunning = false;
      this._cancelRecording();
      return;
    }
    if (this.cancelPendingWhileCommandRunning && !this.isCommandRunning) {
      this.cancelPendingWhileCommandRunning = false;
    }
    if (payload.status === "done" && payload.silence_detected) {
      this._finishSilentRelistenSkip(payload);
      return;
    }
    if (payload.status === "done" && hasTranscript) {
      this._finishAppletTextInsert(payload);
      return;
    }
    if (payload.status === "done" && this.autoRelistenPending) {
      this._finishEmptyRelistenDone(payload);
      return;
    }
    if (!this.isCommandRunning && !this.autoRelistenManualStopRequested) {
      this.autoRelistenPending = false;
      this.autoRelistenPendingToken = "";
    }
    let message = payload.message || status;
    let transcript = typeof payload.transcript === "string" && !this._isEmptyTranscriptText(payload.transcript)
      ? payload.transcript
      : this.lastTranscript || "";
    this._setStatus(status, message, transcript);
    if (
      (payload.status === "recording" || payload.status === "recorded") &&
      this.autoRelistenManualStopRequested &&
      !this.isCommandRunning
    ) {
      this._toggleRecording();
      return;
    }
    this._maybeAutoTranscribeRecorded(payload);
  },

  _artifactEncryptionWarningKey: function(payload) {
    if (!payload) {
      return "";
    }
    let marker = String(payload.transcript_path || payload.audio_path || payload.audio || payload.stopped_at || payload.started_at || "");
    if (marker === "") {
      marker = String(payload.status || "done");
    }
    return marker;
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

  _maybeWarnUnencryptedArtifactStorage: function(payload) {
    if (!payload || String(payload.status || "") !== "done") {
      return;
    }
    let mode = this._normalizeArtifactEncryption(payload.artifact_encryption || this.artifactEncryption);
    if (mode === "off") {
      return;
    }
    let transcriptPath = String(payload.transcript_path || "").trim();
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
    this.microphoneLevel = level;
  },

  _applyPayloadLanguage: function(payload) {
    let language = String(payload.language || "").trim();
    let status = String(payload.status || "");
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
    let maxSeconds = Number(payload.max_seconds);
    if (!isFinite(maxSeconds)) {
      maxSeconds = this.maxSeconds;
    }
    this.recordingMaxSeconds = this._normalizeRecordingLimit(maxSeconds);
  },

  _parseDateMs: function(value) {
    if (!value) {
      return 0;
    }
    let parsed = Date.parse(String(value));
    return isNaN(parsed) ? 0 : parsed;
  },

  _maybeAutoTranscribeRecorded: function(payload) {
    if ((!this.autoTranscribeTimeout && !this.autoRelisten) || !this.notificationSessionActive || this.isCommandRunning) {
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
    let relistenToken = "";
    if (this.autoRelisten) {
      this.autoRelistenSequence += 1;
      relistenToken = String(this.autoRelistenSequence) + ":" + recordingKey;
    }
    this.autoRelistenPending = Boolean(relistenToken);
    this.autoRelistenPendingToken = relistenToken;
    this.isCommandRunning = true;
    this._setStatus("processing", _("Transcribing timed-out recording..."), this.lastTranscript);
    this._spawnJson(this._baseArgs("stop"), (nextPayload) => {
      if (relistenToken && this.autoRelistenPendingToken !== relistenToken) {
        this.isCommandRunning = false;
        return;
      }
      if (nextPayload && nextPayload.error) {
        this.autoTranscribeRecordingKey = "";
      }
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

  _clearOllamaInstallWatchTimer: function() {
    if (this.ollamaInstallWatchTimer) {
      Mainloop.source_remove(this.ollamaInstallWatchTimer);
      this.ollamaInstallWatchTimer = 0;
    }
  },

  _watchOllamaInstallThenChoose: function() {
    this._clearOllamaInstallWatchTimer();
    this.ollamaInstallWatchPolls = 0;
    this._setStatus("processing", _("Waiting for Ollama installation..."), this.lastTranscript);
    this._scheduleOllamaInstallWatchPoll();
  },

  _scheduleOllamaInstallWatchPoll: function() {
    this.ollamaInstallWatchTimer = Mainloop.timeout_add_seconds(OLLAMA_INSTALL_POLL_SECONDS, () => {
      this.ollamaInstallWatchTimer = 0;
      this.ollamaInstallWatchPolls++;
      this._spawnJson(this._textModelsArgs("ollama"), (payload) => {
        if (payload.error) {
          this._setStatus("error", payload.error, this.lastTranscript);
          return;
        }
        if (payload.available !== false) {
          let models = Array.isArray(payload.models) ? payload.models : [];
          this._setStatus("ready", _("Ollama is ready"), this.lastTranscript);
          if (models.length > 0) {
            this._promptChooseOllamaTextModel(models);
          } else {
            this._promptInstallOllamaTextModel();
          }
          return;
        }
        if (this.ollamaInstallWatchPolls >= OLLAMA_INSTALL_MAX_POLLS) {
          this._setStatus("error", _("Ollama installation did not become reachable"), this.lastTranscript);
          this._notify(_("Ollama is not reachable"), _("Install finished or was cancelled, but 127.0.0.1:11434 is still unavailable."), true);
          return;
        }
        this._scheduleOllamaInstallWatchPoll();
      }, { timeoutMs: STATUS_COMMAND_TIMEOUT_MS });
      return false;
    });
  },

  _scheduleSetupCheck: function() {
    this._clearSetupCheckTimer();
    if (this.appletRemoved) {
      return;
    }
    this.setupCheckTimer = Mainloop.timeout_add_seconds(2, () => {
      this.setupCheckTimer = 0;
      if (this.appletRemoved) {
        return false;
      }
      if (this.status === "idle") {
        this._runDoctor(true);
      }
      return false;
    });
  },

  _scheduleAlarmCheck: function(delaySeconds) {
    this._clearAlarmTimer();
    if (this.appletRemoved) {
      return;
    }
    this.alarmTimer = Mainloop.timeout_add_seconds(Math.max(5, Number(delaySeconds || ALARM_CHECK_SECONDS)), () => {
      this.alarmTimer = 0;
      if (this.appletRemoved) {
        return false;
      }
      this._checkAlarms(false);
      if (!this.appletRemoved) {
        this._scheduleAlarmCheck(ALARM_CHECK_SECONDS);
      }
      return false;
    });
  },

  _scheduleStatusPoll: function() {
    this._clearStatusTimer();
    if (this.appletRemoved || (this.status !== "recording" && this.status !== "processing")) {
      return;
    }
    this.statusTimer = Mainloop.timeout_add_seconds(2, () => {
      this.statusTimer = 0;
      if (this.appletRemoved) {
        return false;
      }
      this._refreshStatus();
      return false;
    });
  },

  _scheduleDisplayTick: function() {
    this._clearDisplayTimer();
    if (this.appletRemoved || this.status !== "recording") {
      return;
    }
    this.displayTimer = Mainloop.timeout_add_seconds(1, () => {
      this.displayTimer = 0;
      if (this.appletRemoved) {
        return false;
      }
      if (this.status === "recording") {
        this._updatePanel();
        if (!this.appletRemoved) {
          this._scheduleDisplayTick();
        }
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
    if (this._windowLooksLikeSpeedOfCinnamon(window)) {
      return false;
    }
    return true;
  },

  _hasRememberedTargetWindow: function() {
    return this._isUsableTargetWindow(this.targetWindow) || /^[0-9]+$/.test(String(this.targetWindowXid || "").trim());
  },

  _rememberFocusedWindow: function(preserveOnFailure) {
    let window = global.display ? global.display.focus_window : null;
    if (this._isUsableTargetWindow(window)) {
      this.targetWindow = window;
      this._rememberActiveXWindow();
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
    if (this._rememberActiveXWindow()) {
      return true;
    }
    if (preserveOnFailure && this._hasRememberedTargetWindow()) {
      return true;
    }
    this._clearTargetWindowXid();
    return false;
  },

  _restoreTargetWindowForPaste: function() {
    if (this._isUsableTargetWindow(this.targetWindow)) {
      try {
        Main.activateWindow(this.targetWindow, global.get_current_time());
        return true;
      } catch (err) {
        global.logError(err);
      }
    }
    return this._activateTargetXWindow();
  },

  _closeMenuForKeyboardInsert: function() {
    try {
      if (this.menu && this.menu.isOpen) {
        this.menu.close();
      }
      return true;
    } catch (err) {
      global.logError(err);
      return false;
    }
  },

  _clearTargetWindowXid: function() {
    this.targetWindowXid = "";
    this.targetWindowXTitle = "";
    this.targetWindowXClass = "";
  },

  _xdotoolOutput: function(args, maxBytes) {
    let timeout = GLib.find_program_in_path("timeout");
    let xdotool = GLib.find_program_in_path("xdotool");
    if (!timeout || !xdotool) {
      return null;
    }
    let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), xdotool];
    args = args || [];
    for (let i = 0; i < args.length; i++) {
      command.push(args[i]);
    }
    try {
      let result = GLib.spawn_sync(
        null,
        this._coerceSpawnArgs(command),
        null,
        GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.STDOUT_PIPE | GLib.SpawnFlags.STDERR_PIPE,
        null
      );
      if (!Array.isArray(result) || result.length < 4 || !result[0] || result[3] !== 0) {
        return null;
      }
      if (result[1] && result[1].length > maxBytes) {
        return null;
      }
      return ByteArray.toString(result[1] || []);
    } catch (err) {
      return null;
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

  _rememberActiveXWindow: function() {
    let xid = String(this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES) || "").trim();
    if (!/^[0-9]+$/.test(xid)) {
      this._clearTargetWindowXid();
      return false;
    }
    let title = String(this._xdotoolOutput(["getwindowname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES) || "").trim();
    let windowClass = String(this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES) || "").trim();
    if (this._xWindowLooksLikeSpeedOfCinnamon(title, windowClass)) {
      this._notifySelfProtectionBlocked(title, windowClass);
      this._clearTargetWindowXid();
      return false;
    }
    this.targetWindowXid = xid;
    this.targetWindowXTitle = this._shortMenuText(title, 160);
    this.targetWindowXClass = this._shortMenuText(windowClass, 160);
    return true;
  },

  _activateTargetXWindow: function() {
    let xid = String(this.targetWindowXid || "").trim();
    if (!/^[0-9]+$/.test(xid)) {
      return false;
    }
    let output = this._xdotoolOutput(["windowactivate", "--sync", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES);
    return output !== null;
  },

  _targetXWindowSnapshot: function() {
    let xid = String(this.targetWindowXid || "").trim();
    if (!/^[0-9]+$/.test(xid)) {
      return null;
    }
    return {
      xid: xid,
      windowClass: String(this.targetWindowXClass || "").trim().toLowerCase(),
    };
  },

  _targetXWindowMatchesSnapshot: function(snapshot) {
    if (!snapshot || !snapshot.xid) {
      return false;
    }
    let xid = String(snapshot.xid || "").trim();
    if (!/^[0-9]+$/.test(xid)) {
      return false;
    }
    let active = String(this._xdotoolOutput(["getactivewindow"], MAX_XDOTOOL_TARGET_OUTPUT_BYTES) || "").trim();
    if (active !== xid) {
      return false;
    }
    let expectedClass = String(snapshot.windowClass || "").trim().toLowerCase();
    if (expectedClass !== "") {
      let activeClass = String(this._xdotoolOutput(["getwindowclassname", xid], MAX_XDOTOOL_TARGET_OUTPUT_BYTES) || "").trim().toLowerCase();
      if (activeClass !== expectedClass) {
        return false;
      }
    }
    return true;
  },

  _windowProbeValue: function(window, methodName) {
    if (!window || !window[methodName]) {
      return "";
    }
    try {
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
    let detail = this._shortMenuText(String(title || windowClass || _("unknown target")), 160);
    let key = detail + "\n" + String(windowClass || "");
    let now = Date.now();
    if (key === this.selfProtectionNoticeKey && now - this.selfProtectionNoticeAtMs < SELF_PROTECTION_NOTICE_COOLDOWN_MS) {
      return;
    }
    this.selfProtectionNoticeKey = key;
    this.selfProtectionNoticeAtMs = now;
    let message = _("Auto-Submitt self-protection blocked target: ") + detail;
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

  _clipboardTargetList: function(program, args) {
    args = args || [];
    let timeout = GLib.find_program_in_path("timeout");
    let helper = GLib.find_program_in_path(program);
    if (!timeout || !helper) {
      return null;
    }
    let command = [timeout, "--kill-after=1", String(CLIPBOARD_TARGET_TIMEOUT_SECONDS), helper];
    for (let i = 0; i < args.length; i++) {
      command.push(args[i]);
    }
    let result;
    try {
      let normalizedArgs = this._coerceSpawnArgs(command);
      result = GLib.spawn_sync(
        null,
        normalizedArgs,
        null,
        GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.STDOUT_PIPE | GLib.SpawnFlags.STDERR_PIPE,
        null
      );
      if (!Array.isArray(result) || result.length < 4 || !result[0] || result[3] !== 0) {
        return null;
      }
      if (result[1] && result[1].length > MAX_CLIPBOARD_TARGET_OUTPUT_BYTES) {
        return null;
      }
      return ByteArray.toString(result[1] || []);
    } catch (err) {
      return null;
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

  _clipboardHasNonTextPayload: function() {
    if (GLib.find_program_in_path("xclip")) {
      let targets = this._clipboardTargetList("xclip", ["-selection", "clipboard", "-t", "TARGETS", "-out"]);
      return this._clipboardTargetsContainNonTextPayload(targets);
    }
    if (GLib.find_program_in_path("xsel")) {
      let targets = this._clipboardTargetList("xsel", ["--clipboard", "--output", "--target", "TARGETS"]);
      return this._clipboardTargetsContainNonTextPayload(targets);
    }
    if (GLib.find_program_in_path("wl-paste")) {
      let targets = this._clipboardTargetList("wl-paste", ["--list-types"]);
      return this._clipboardTargetsContainNonTextPayload(targets);
    }
    return false;
  },

  _clipboardPayloadSnapshot: function() {
    let targets = null;
    if (GLib.find_program_in_path("xclip")) {
      targets = this._clipboardTargetList("xclip", ["-selection", "clipboard", "-t", "TARGETS", "-out"]);
    } else if (GLib.find_program_in_path("xsel")) {
      targets = this._clipboardTargetList("xsel", ["--clipboard", "--output", "--target", "TARGETS"]);
    } else if (GLib.find_program_in_path("wl-paste")) {
      targets = this._clipboardTargetList("wl-paste", ["--list-types"]);
    }
    if (targets === null || targets === undefined) {
      return {
        signature: "unknown",
        hasNonTextPayload: true,
        description: _("clipboard contents"),
        payloadFingerprint: "unknown",
      };
    }
    let targetSignature = Array.isArray(targets) ? targets.join("\n") : String(targets || "");
    return {
      signature: targetSignature,
      hasNonTextPayload: this._clipboardTargetsContainNonTextPayload(targets),
      payloadFingerprint: this._clipboardPayloadFingerprintFromTargets(targets),
      description: this._clipboardPayloadDescriptionFromTargets(targets),
    };
  },

  _clipboardPayloadFingerprintFromTargets: function(targets) {
    let nonTextTargets = this._clipboardNonTextPayloadTargets(targets);
    if (!Array.isArray(nonTextTargets) || nonTextTargets.length === 0) {
      return "no-nontext";
    }
    let sampleTarget = String(nonTextTargets[0]);
    let payload = null;
    if (GLib.find_program_in_path("xclip")) {
      payload = this._clipboardTargetList("xclip", ["-selection", "clipboard", "-t", sampleTarget, "-out"]);
    } else if (GLib.find_program_in_path("xsel")) {
      payload = this._clipboardTargetList("xsel", ["--clipboard", "--output", "--target", sampleTarget]);
    } else if (GLib.find_program_in_path("wl-paste")) {
      payload = this._clipboardTargetList("wl-paste", ["--type", sampleTarget]);
    }
    if (payload === null || payload === undefined) {
      return "unknown";
    }
    return this._clipboardPayloadFingerprintFromText(String(payload), sampleTarget);
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

  _describeNonTextClipboardPayload: function() {
    if (GLib.find_program_in_path("xclip")) {
      let targets = this._clipboardTargetList("xclip", ["-selection", "clipboard", "-t", "TARGETS", "-out"]);
      return this._clipboardPayloadDescriptionFromTargets(targets);
    }
    if (GLib.find_program_in_path("xsel")) {
      let targets = this._clipboardTargetList("xsel", ["--clipboard", "--output", "--target", "TARGETS"]);
      return this._clipboardPayloadDescriptionFromTargets(targets);
    }
    if (GLib.find_program_in_path("wl-paste")) {
      let targets = this._clipboardTargetList("wl-paste", ["--list-types"]);
      return this._clipboardPayloadDescriptionFromTargets(targets);
    }
    return _("clipboard contents");
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

  _copyAndMaybePasteTranscriptText: function(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback) {
    if (method === "clipboard") {
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
      this._setStatus("done", _("Copied to clipboard"), transcript);
      return true;
    }
    if (!canPasteWithKeyboard) {
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
      this._setStatus("done", _("Copied to clipboard; install xdotool or wtype for automatic paste"), transcript);
      return true;
    }
    if (!this._closeMenuForKeyboardInsert()) {
      this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);
      return false;
    }
    let restored = this._restoreTargetWindowForPaste();
    if (!restored) {
      this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
      this._setStatus("error", _("Copied to clipboard; paste failed: target window could not be restored"), transcript);
      return false;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, text);
    if (this._pasteClipboardAfterFocus(submitWithReturn, text, (completed) => {
      if (completed) {
        this._setStatus("done", _("Copied and pasted into target window"), transcript);
      }
      if (typeof completionCallback === "function") {
        completionCallback(completed === true);
      }
    })) {
      return null;
    }
    this._setStatus("error", _("Copied to clipboard; automatic paste command could not be started"), transcript);
    return false;
  },

  _confirmClipboardOverwriteForPaste: function(clipboardSnapshot, transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback) {
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
    let dialog = new ModalDialog.ModalDialog();
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
    dialog.contentLayout.add_child(new St.Label({ text: message, x_expand: true }));
    dialog.contentLayout.add_child(new St.Label({ text: _("Replace clipboard content and continue paste?"), x_expand: true }));
    dialog.setButtons([
      {
        label: _("Cancel"),
        key: Clutter.KEY_Escape,
        action: function() {
          dialog.close();
          this._setStatus("ready", _("Clipboard overwrite cancelled"), transcript);
          complete(false);
        }.bind(this),
      },
      {
        label: _("Overwrite clipboard"),
        action: function() {
          dialog.close();
          let currentClipboardSnapshot = this._clipboardPayloadSnapshot();
          if (!this._clipboardPayloadSignaturesMatch(clipboardSnapshot, currentClipboardSnapshot)) {
            this._setStatus("ready", _("Clipboard changed; overwrite cancelled"), transcript);
            complete(false);
            return;
          }
          this._setClipboardOverwriteApproval(currentClipboardSnapshot);
          let result = this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, complete);
          if (result !== null) {
            complete(result);
          }
        }.bind(this),
      }
    ]);
    if (!dialog.open()) {
      this._setStatus("error", _("Clipboard overwrite prompt could not be opened"), transcript);
      complete(false);
    }
  },

  _pasteClipboardAfterFocus: function(sendEnter, expectedClipboardText, completionCallback) {
    let terminalPaste = this._isTerminalTargetWindow();
    let expectedTargetWindow = this._targetXWindowSnapshot();
    let hasXdotool = GLib.find_program_in_path("xdotool");
    let hasWtype = GLib.find_program_in_path("wtype");
    let args = null;
    let followUpArgs = null;
    if (hasXdotool) {
      let pasteKey = terminalPaste ? "ctrl+shift+v" : "ctrl+v";
      args = ["xdotool", "key", "--clearmodifiers", pasteKey];
      if (sendEnter) {
        followUpArgs = ["xdotool", "key", "--clearmodifiers", "Return"];
      }
    } else if (hasWtype) {
      args = terminalPaste
        ? ["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]
        : ["wtype", "-M", "ctrl", "v", "-m", "ctrl"];
      if (sendEnter) {
        followUpArgs = ["wtype", "-k", "Return"];
      }
    }
    if (!args) {
      return false;
    }
    return this._spawnKeyboardAfterFocus(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback);
  },

  _typeTextAfterFocus: function(text, completionCallback) {
    let delay = this._normalizeTypingDelayMs(this.typingDelayMs);
    let typedText = this._coerceTypeText(text);
    if (typedText === null) {
      return false;
    }
    return this._spawnKeyboardAfterFocus(["xdotool", "type", "--clearmodifiers", "--delay", String(delay), "--", typedText], null, null, null, completionCallback);
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

  _spawnKeyboardAfterFocus: function(args, followUpArgs, expectedClipboardText, expectedTargetWindow, completionCallback) {
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
    if (this.appletRemoved) {
      complete(false);
      return false;
    }
    try {
      this.pasteTimer = Mainloop.timeout_add(PASTE_FOCUS_DELAY_MS, () => {
        this.pasteTimer = 0;
        if (this.appletRemoved) {
          complete(false);
          return false;
        }
        this._spawnKeyboardWhenClipboardReady(args, followUpArgs, expectedClipboardText, Date.now() + CLIPBOARD_READY_TIMEOUT_MS, expectedTargetWindow, complete);
        return false;
      });
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Keyboard insert failed") + ": " + String(err), this.lastTranscript);
      complete(false);
      return false;
    }
    return true;
  },

  _spawnKeyboardWhenClipboardReady: function(args, followUpArgs, expectedClipboardText, deadlineMs, expectedTargetWindow, completionCallback) {
    if (expectedClipboardText === undefined || expectedClipboardText === null) {
      this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, null, null, completionCallback);
      return;
    }
    let expected = String(expectedClipboardText);
    try {
      this.clipboard.get_text(St.ClipboardType.CLIPBOARD, (clipboard, clipboardText) => {
        if (this.appletRemoved) {
          if (typeof completionCallback === "function") {
            completionCallback(false);
          }
          return;
        }
        if (String(clipboardText || "") === expected) {
          this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, expected, deadlineMs, completionCallback);
          return;
        }
        if (Date.now() >= deadlineMs) {
          this._setStatus("error", _("Clipboard did not confirm new text before automatic paste"), this.lastTranscript);
          if (typeof completionCallback === "function") {
            completionCallback(false);
          }
          return;
        }
        try {
          this.pasteTimer = Mainloop.timeout_add(CLIPBOARD_READY_RETRY_MS, () => {
            this.pasteTimer = 0;
            if (this.appletRemoved) {
              if (typeof completionCallback === "function") {
                completionCallback(false);
              }
              return false;
            }
            this._spawnKeyboardWhenClipboardReady(args, followUpArgs, expected, deadlineMs, expectedTargetWindow, completionCallback);
            return false;
          });
        } catch (err) {
          global.logError(err);
          this._setStatus("error", _("Keyboard insert failed") + ": " + String(err), this.lastTranscript);
          if (typeof completionCallback === "function") {
            completionCallback(false);
          }
        }
      });
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Clipboard could not be verified before automatic paste"), this.lastTranscript);
      if (typeof completionCallback === "function") {
        completionCallback(false);
      }
    }
  },

  _spawnKeyboardArgs: function(args, followUpArgs, expectedTargetWindow, expectedClipboardText, expectedClipboardDeadlineMs, completionCallback) {
    if (expectedClipboardText !== undefined && expectedClipboardText !== null) {
      let expected = String(expectedClipboardText);
      try {
        this.clipboard.get_text(St.ClipboardType.CLIPBOARD, (clipboard, clipboardText) => {
          if (this.appletRemoved) {
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
            this.pasteTimer = Mainloop.timeout_add(CLIPBOARD_READY_RETRY_MS, () => {
              this.pasteTimer = 0;
              if (this.appletRemoved) {
                if (typeof completionCallback === "function") {
                  completionCallback(false);
                }
                return false;
              }
              this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, expected, expectedClipboardDeadlineMs, completionCallback);
              return false;
            });
            return;
          }
          this._spawnKeyboardArgs(args, followUpArgs, expectedTargetWindow, null, null, completionCallback);
        });
      } catch (err) {
        global.logError(err);
        this._setStatus("error", _("Clipboard changed before automatic paste"), this.lastTranscript);
        if (typeof completionCallback === "function") {
          completionCallback(false);
        }
      }
      return;
    }
    if (expectedTargetWindow && !this._targetXWindowMatchesSnapshot(expectedTargetWindow)) {
      this._setStatus("error", _("Target window changed before automatic paste"), this.lastTranscript);
      if (typeof completionCallback === "function") {
        completionCallback(false);
      }
      return;
    }
    try {
      Util.spawn(this._coerceSpawnArgs(args));
      if (followUpArgs) {
        if (this.appletRemoved) {
          if (typeof completionCallback === "function") {
            completionCallback(false);
          }
          return;
        }
        this.pasteTimer = Mainloop.timeout_add(PASTE_SUBMIT_DELAY_MS, () => {
          this.pasteTimer = 0;
          if (this.appletRemoved) {
            if (typeof completionCallback === "function") {
              completionCallback(false);
            }
            return false;
          }
          if (!expectedTargetWindow || !this._targetXWindowMatchesSnapshot(expectedTargetWindow)) {
            this._setStatus("error", _("Target window changed before automatic submit"), this.lastTranscript);
            if (typeof completionCallback === "function") {
              completionCallback(false);
            }
            return false;
          }
          try {
            Util.spawn(this._coerceSpawnArgs(followUpArgs));
            if (typeof completionCallback === "function") {
              completionCallback(true);
            }
          } catch (err) {
            global.logError(err);
            this._setStatus("error", _("Keyboard insert failed") + ": " + String(err), this.lastTranscript);
            if (typeof completionCallback === "function") {
              completionCallback(false);
            }
          }
          return false;
        });
      } else if (typeof completionCallback === "function") {
        completionCallback(true);
      }
    } catch (err) {
      global.logError(err);
      this._setStatus("error", _("Keyboard insert failed") + ": " + String(err), this.lastTranscript);
      if (typeof completionCallback === "function") {
        completionCallback(false);
      }
    }
  },

  _finishAppletTextInsert: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    let transcript = String(payload.transcript || "");
    if (this._isEmptyTranscriptText(transcript)) {
      this._finishEmptyRelistenDone(payload);
      return;
    }
    let insertFingerprint = this._autoInsertFingerprint(payload, transcript);
    if (!this._reserveAutoInsertFingerprint(insertFingerprint)) {
      this._setStatus("done", payload.message || _("Transcript already inserted"), transcript);
      this._finishPendingRelisten();
      return;
    }
    let inserted = false;
    if (payload.inserted === true) {
      inserted = true;
      this._setStatus("done", payload.message || _("Transcript already inserted by backend"), transcript);
    } else {
      let result = this._insertTranscriptText(transcript, (completed) => {
        if (!completed) {
          this._forgetAutoInsertFingerprint(insertFingerprint);
          this.autoRelistenPending = false;
          this.autoRelistenPendingToken = "";
          this.autoRelistenManualStopRequested = true;
          return;
        }
        this._finishPendingRelisten();
      });
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
    let marker = "done";
    if (payload) {
      marker = String(payload.audio_path || payload.audio || payload.transcript_path || payload.stopped_at || payload.started_at || "done");
    }
    if (marker === "") {
      marker = "done";
    }
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
    let marker = String(payload.audio_path || payload.audio || payload.transcript_path || "");
    let digest = this._transcriptDigest(rawTranscript);
    if (marker === "") {
      marker = String(payload.started_at || payload.stopped_at || "");
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
    if (this._hasAutoInsertFingerprint(fingerprint)) {
      return false;
    }
    this._rememberAutoInsertFingerprint(fingerprint);
    return true;
  },

  _rememberAutoInsertFingerprint: function(fingerprint) {
    if (!fingerprint) {
      return;
    }
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
  },

  _forgetAutoInsertFingerprint: function(fingerprint) {
    if (!fingerprint || !this.autoInsertFingerprints) {
      return;
    }
    let index = this.autoInsertFingerprints.indexOf(fingerprint);
    if (index >= 0) {
      this.autoInsertFingerprints.splice(index, 1);
    }
    if (this.autoInsertFingerprint === fingerprint) {
      this.autoInsertFingerprint = this.autoInsertFingerprints.length > 0
        ? this.autoInsertFingerprints[this.autoInsertFingerprints.length - 1]
        : "";
    }
  },

  _finishSilentRelistenSkip: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    if (this._finishPendingRelisten()) {
      return;
    }
    this._setStatus("done", payload.message || _("Silent recording skipped"), this.lastTranscript);
  },

  _finishEmptyRelistenDone: function(payload) {
    this._ensureAutoRelistenPendingForDonePayload(payload);
    if (this._finishPendingRelisten()) {
      return;
    }
    this._setStatus("done", payload.message || _("Recording finished without transcript"), this.lastTranscript);
  },

  _insertTranscriptText: function(transcript, completionCallback) {
    let method = this._normalizeOutputMethod(this.insertMethod);
    let autoPasteTarget = this._windowTitleMatchesAutoPaste();
    let canPasteWithKeyboard = GLib.find_program_in_path("xdotool") || GLib.find_program_in_path("wtype");
    let submitWithReturn = autoPasteTarget && method === "clipboard-paste" && canPasteWithKeyboard;
    let suppressAutoPasteEnter = method !== "clipboard-paste" || submitWithReturn;
    let text = this._preparedTranscriptText(transcript, suppressAutoPasteEnter);
    if (method === "none") {
      this._setStatus("done", _("Insertion disabled"), transcript);
      return true;
    }
    if (this._isEmptyTranscriptText(transcript) || this._isEmptyTranscriptText(text)) {
      this._setStatus("done", _("No transcript text to insert"), "");
      return true;
    }
    if (method === "type") {
      if (GLib.find_program_in_path("xdotool")) {
        if (!this._closeMenuForKeyboardInsert()) {
          this._setStatus("error", _("Could not close applet menu before keyboard insert"), transcript);
          return false;
        }
        let restored = this._restoreTargetWindowForPaste();
        if (!restored) {
          this._setStatus("error", _("Target window unavailable for direct typing"), transcript);
          return false;
        }
        if (this._typeTextAfterFocus(text, (completed) => {
          if (completed) {
            this._setStatus("done", restored ? _("Typed into target window") : _("Typed text"), transcript);
          }
          if (typeof completionCallback === "function") {
            completionCallback(completed === true);
          }
        })) {
          return null;
        }
      } else {
        this._setStatus("error", _("Install xdotool for direct typing"), transcript);
      }
      return false;
    }
    if (method === "clipboard-paste" && !canPasteWithKeyboard) {
      this._setStatus("error", _("Clipboard-paste requires a keyboard helper (xdotool or wtype)"), transcript);
      return false;
    }
    let clipboardSnapshot = this._clipboardPayloadSnapshot();
    if (method === "clipboard-paste" && clipboardSnapshot.hasNonTextPayload) {
      if (this._hasValidClipboardOverwriteApproval(clipboardSnapshot)) {
        this._clearClipboardOverwriteApproval();
        return this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback);
      }
      this._clearClipboardOverwriteApproval();
      this._confirmClipboardOverwriteForPaste(
        clipboardSnapshot,
        transcript,
        text,
        method,
        canPasteWithKeyboard,
        submitWithReturn,
        completionCallback
      );
      return null;
    }
    return this._copyAndMaybePasteTranscriptText(transcript, text, method, canPasteWithKeyboard, submitWithReturn, completionCallback);
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
    this.isCommandRunning = true;
    this.autoTranscribeRecordingKey = "";
    this.recordingStartedAtMs = 0;
    this.recordingMaxSeconds = this._normalizeRecordingLimit(this.maxSeconds);
    this._setStatus("processing", _("Starting next recording..."), this.lastTranscript);
    this._spawnJson(this._baseArgs("start"), (payload) => {
      this.isCommandRunning = false;
      if (payload.error) {
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this._setStatus("error", payload.error, this.lastTranscript);
        return;
      }
      if (payload.status === "recording" || payload.status === "recorded") {
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
      }
      this._applyPayload(payload);
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
      this._setStatus(this.status, _("No transcript yet"), this.lastTranscript);
      return;
    }
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._preparedTranscriptText(this.lastTranscript, true));
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
      let label = this._shortMenuText(String(transcript.preview || transcript.name || _("Transcript")), 80);
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
    this.clipboard.set_text(St.ClipboardType.CLIPBOARD, this._preparedTranscriptText(text, true));
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
    this.lastMessage = status === "error" ? this._sanitizeErrorMessage(message) : message || "";
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
    if (!level.ok) {
      return _("Microphone: ") + String(level.detail || _("waiting for audio"));
    }
    let percent = Math.max(0, Math.min(100, Math.round(Number(level.percent || 0))));
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
      return _("Voice: ") + GLib.path_get_basename(model);
    }
    if (backend === "command") return _("Voice: custom command");
    if (backend === "whisper") return _("Voice: Whisper command");
    if (backend === "openai-compatible") {
      return _("Voice: External API ") + (String(this.openaiCompatibleModel || "").trim() || _("not configured"));
    }
    if (backend === "whisper-cpp") return _("Voice: local model file");
    if (backend === "faster-whisper") return _("Voice: local model directory");
    return _("Voice: automatic");
  },

  _textModelLabel: function() {
    let backend = String(this.postProcessBackend || "none");
    if (backend === "none") return _("Text model: disabled");
    if (backend === "ollama") return _("Text model: ") + (String(this.ollamaModel || "").trim() || _("Ollama"));
    if (backend === "openai-compatible") {
      return _("Text model: ") + (String(this.openaiCompatibleTextModel || this.openaiCompatibleModel || "").trim() || _("OpenAI-compatible"));
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
  }
};

function main(metadata, orientation, panelHeight, instanceId) {
  return new MyApplet(metadata, orientation, panelHeight, instanceId);
}
