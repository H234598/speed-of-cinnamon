import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appletSource = fs.readFileSync(
  path.join(root, "files/speed-of-cinnamon@H234598/applet.js"),
  "utf8"
);

let constructors = 0;

class Actor {
  constructor() { this.visible = true; this.focusGrabCount = 0; }
  show() { this.visible = true; }
  hide() { this.visible = false; }
  is_finalized() { return false; }
  contains(actor) { return actor === this; }
  grab_key_focus() {
    this.focusGrabCount += 1;
    methodContext.global.stage.keyFocus = this;
  }
}

class Menu {
  constructor() {
    this.actor = new Actor();
    this.items = [];
    this.handlers = [];
    this.isOpen = false;
    this.removeAllCount = 0;
  }
  addMenuItem(item) { this.items.push(item); }
  removeAll() { this.removeAllCount += 1; this.items = []; }
  connect(signal, callback) { this.handlers.push({ signal, callback }); return this.handlers.length; }
  open() {
    this.isOpen = true;
    this.actor.show();
    for (const handler of this.handlers) {
      if (handler.signal === "open-state-changed") handler.callback(this, true);
    }
  }
  close() {
    this.isOpen = false;
    if (this._activeMenuItem && this._activeMenuItem.setActive) {
      this._activeMenuItem.setActive(false);
    }
    this._activeMenuItem = null;
    this.actor.hide();
    for (const handler of this.handlers) {
      if (handler.signal === "open-state-changed") handler.callback(this, false);
    }
  }
  _getMenuItems() { return this.items; }
}

class PopupMenuItem {
  constructor(label = "") {
    constructors += 1;
    this.actor = new Actor();
    this.label = { text: label };
    this.sensitive = true;
    this.active = false;
    this.handlers = [];
  }
  connect(signal, callback) { this.handlers.push({ signal, callback }); return this.handlers.length; }
  activate() {
    for (const handler of this.handlers) {
      if (handler.signal === "activate") handler.callback(this);
    }
  }
  setSensitive(value) { this.sensitive = Boolean(value); }
  setActive(value) { this.active = Boolean(value); }
}

class PopupSubMenuMenuItem extends PopupMenuItem {
  constructor(label = "") { super(label); this.menu = new Menu(); }
}

class PopupIconMenuItem extends PopupMenuItem {
  constructor(label = "", iconName = "") {
    super(label);
    this._icon = { icon_name: iconName };
  }
}

class PopupSeparatorMenuItem extends PopupMenuItem {}

const methodContext = {
  MAX_ALARM_MENU_ENTRIES: 128,
  MAX_HISTORY_MENU_ENTRIES: 128,
  MAX_INPUT_SOURCE_MENU_ENTRIES: 128,
  MAX_MODEL_MENU_ENTRIES: 128,
  MAX_VOICE_MODEL_MENU_ENTRIES: 128,
  MODEL_MENU_REFRESH_TTL_MS: 5000,
  TEXT_POLISHING_PRESETS: ["default", "clean", "code"],
  PopupMenu: { PopupMenuItem, PopupSubMenuMenuItem, PopupIconMenuItem, PopupSeparatorMenuItem },
  St: { IconType: { SYMBOLIC: 1 } },
  GLib: { build_filenamev: (parts) => parts.join("/"), get_user_data_dir: () => "/data" },
  global: {
    stage: {
      keyFocus: null,
      get_key_focus() { return this.keyFocus; },
    },
  },
  _: (value) => value,
};

function loadMethod(name, nextName) {
  const start = appletSource.indexOf(`  ${name}: function(`);
  const end = appletSource.indexOf(`\n  ${nextName}:`, start);
  assert.notEqual(start, -1, `${name} exists`);
  assert.notEqual(end, -1, `${nextName} follows ${name}`);
  const property = appletSource.slice(start, end).trim().replace(/,\s*$/, "");
  return vm.runInNewContext(`({${property}}).${name}`, methodContext);
}

function commonState() {
  return {
    _canMutateMenu: () => true,
    _connectSafe(target, signal, callback) { target.connect(signal, callback); return 1; },
    _recordLifecycleError() {},
    _safeLogError() {},
    _selectionInfoItem(label) { const item = new PopupMenuItem(label); item.setSensitive(false); return item; },
    _selectionMenuItem(label) { return new PopupMenuItem(label); },
    _setMenuItemLabelSafely(item, text) { item.label.text = text; return true; },
    _setMenuItemSensitiveSafely(item, value) { item.setSensitive(value); return true; },
    _setPooledMenuItemVisible: loadMethod("_setPooledMenuItemVisible", "_ensureAlarmMenuPool"),
    _closeNestedMenusSafely: loadMethod("_closeNestedMenusSafely", "_closeMenuSafely"),
    _closeMenuSafely: loadMethod("_closeMenuSafely", "_clearMenuItems"),
    _styleMenuItemLabel(item) { return item; },
    _styleSelectionSubmenu() {},
    _shortMenuText(value, limit) { return String(value).slice(0, limit); },
    _uiMessageText(value) { return String(value); },
  };
}

test("alarm menu reuses parent rows and current row data", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    alarmItem: { actor: new Actor(), menu: new Menu() },
    _alarmMenuFingerprint: null,
    _alarmMenuPool: null,
    _checkAlarms() {},
    _copyAlarmCommands() {},
    _openFolder() {},
    _setAlarmEnabled(id, enabled) { state.alarmToggle = { id, enabled }; },
    _removeAlarm(id) { state.removedAlarm = id; },
    _ensureAlarmMenuPool: loadMethod("_ensureAlarmMenuPool", "_hydrateAlarmMenuPoolRow"),
    _hydrateAlarmMenuPoolRow: loadMethod("_hydrateAlarmMenuPoolRow", "_populateAlarmMenu"),
    _populateAlarmMenu: loadMethod("_populateAlarmMenu", "_addAlarmMenuEntry"),
    _coerceCliTextArg(value) { return String(value); },
  };
  const alarms = Array.from({ length: 20 }, (_unused, index) => ({ id: `alarm-${index}`, label: `Alarm ${index}`, enabled: false }));
  state._populateAlarmMenu(alarms, "summary");
  const afterWarmup = constructors;
  for (let index = 0; index < 1000; index += 1) {
    state._populateAlarmMenu(alarms.map((alarm) => ({ ...alarm, label: `${alarm.label}-${index}` })), "summary");
  }
  assert.equal(constructors, afterWarmup);
  assert.equal(state.alarmItem.menu.removeAllCount, 0);
  const row = state._alarmMenuPool.rows[0];
  row.menu.open();
  row._socToggle.activate();
  assert.deepEqual(state.alarmToggle, { id: "alarm-0", enabled: true });
});

test("hiding a pooled submenu closes it and hands focus to the applet", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    actor: new Actor(),
    alarmItem: { actor: new Actor(), menu: new Menu() },
    _alarmMenuFingerprint: null,
    _alarmMenuPool: null,
    _checkAlarms() {},
    _copyAlarmCommands() {},
    _openFolder() {},
    _setAlarmEnabled() {},
    _removeAlarm() {},
    _ensureAlarmMenuPool: loadMethod("_ensureAlarmMenuPool", "_hydrateAlarmMenuPoolRow"),
    _hydrateAlarmMenuPoolRow: loadMethod("_hydrateAlarmMenuPoolRow", "_populateAlarmMenu"),
    _populateAlarmMenu: loadMethod("_populateAlarmMenu", "_addAlarmMenuEntry"),
    _coerceCliTextArg(value) { return String(value); },
  };
  state._populateAlarmMenu([{ id: "alarm-1", label: "Alarm", enabled: true }], "summary");
  const row = state._alarmMenuPool.rows[0];
  row.menu.open();
  row.setActive(true);
  methodContext.global.stage.keyFocus = row.menu.actor;

  state._populateAlarmMenu([], "", "refresh failed");

  assert.equal(row.menu.isOpen, false);
  assert.equal(row.menu.actor.visible, false);
  assert.equal(row.actor.visible, false);
  assert.equal(row.active, false);
  assert.equal(methodContext.global.stage.keyFocus, state.actor);
  assert.equal(state.actor.focusGrabCount, 1);
});

test("failed pooled submenu close preserves row data and fingerprint", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    actor: new Actor(),
    alarmItem: { actor: new Actor(), menu: new Menu() },
    _alarmMenuFingerprint: null,
    _alarmMenuPool: null,
    lifecycleErrors: [],
    _recordLifecycleError(channel, error) { this.lifecycleErrors.push({ channel, error }); },
    _checkAlarms() {},
    _copyAlarmCommands() {},
    _openFolder() {},
    _setAlarmEnabled() {},
    _removeAlarm() {},
    _ensureAlarmMenuPool: loadMethod("_ensureAlarmMenuPool", "_hydrateAlarmMenuPoolRow"),
    _hydrateAlarmMenuPoolRow: loadMethod("_hydrateAlarmMenuPoolRow", "_populateAlarmMenu"),
    _populateAlarmMenu: loadMethod("_populateAlarmMenu", "_addAlarmMenuEntry"),
    _coerceCliTextArg(value) { return String(value); },
  };
  state._populateAlarmMenu([{ id: "alarm-1", label: "Alarm", enabled: true }], "summary");
  const row = state._alarmMenuPool.rows[0];
  const previousData = row._socData;
  const previousFingerprint = state._alarmMenuFingerprint;
  row.menu.open();
  row.menu.close = () => { throw new Error("injected close failure"); };

  state._populateAlarmMenu([], "", "refresh failed");

  assert.equal(row.actor.visible, true);
  assert.equal(row.menu.isOpen, true);
  assert.equal(row._socData, previousData);
  assert.equal(state._alarmMenuFingerprint, previousFingerprint);
  assert.equal(state.lifecycleErrors.length, 1);
  assert.equal(state.lifecycleErrors[0].channel, "menu-pool");
});

test("input source menu reuses rows and actions read current data", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    inputDevice: "",
    inputSourceItem: { actor: new Actor(), menu: new Menu() },
    _inputSourceMenuFingerprint: null,
    _inputSourceMenuPool: null,
    _ensureInputSourceMenuPool: loadMethod("_ensureInputSourceMenuPool", "_populateInputSourceMenu"),
    _populateInputSourceMenu: loadMethod("_populateInputSourceMenu", "_selectInputSource"),
    _coerceCliTextArg(value) { return String(value); },
    _selectInputSource(name, label) { state.selected = { name, label }; },
  };
  const sources = Array.from({ length: 20 }, (_unused, index) => ({ name: `source-${index}`, description: `Mic ${index}` }));
  state._populateInputSourceMenu(sources);
  const afterWarmup = constructors;
  for (let index = 0; index < 1000; index += 1) {
    state._populateInputSourceMenu(sources.map((source) => ({ ...source, description: `Mic ${index}` })));
  }
  assert.equal(constructors, afterWarmup);
  assert.equal(state.inputSourceItem.menu.removeAllCount, 0);
  state._inputSourceMenuPool.rows[0].activate();
  assert.equal(state.selected.name, "source-0");
  assert.equal(state.selected.label, "Mic 999");
});

test("history menu reuses lazy rows and current transcript text", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    historyItem: { actor: new Actor(), menu: new Menu() },
    _historyMenuFingerprint: null,
    _historyMenuPool: null,
    _ensureHistoryMenuPool: loadMethod("_ensureHistoryMenuPool", "_hydrateHistoryMenuPoolRow"),
    _hydrateHistoryMenuPoolRow: loadMethod("_hydrateHistoryMenuPoolRow", "_populateHistoryMenu"),
    _populateHistoryMenu: loadMethod("_populateHistoryMenu", "_copyHistoryTranscript"),
    _isEmptyTranscriptText: (text) => String(text).trim() === "",
    _insertHistoryTranscript(text) { state.inserted = text; },
    _copyHistoryTranscript(text) { state.copied = text; },
  };
  const transcripts = Array.from({ length: 20 }, (_unused, index) => ({ preview: `Preview ${index}`, text: `text-${index}` }));
  state._populateHistoryMenu(transcripts);
  assert.equal(state._historyMenuPool.rows[0].menu.items.length, 0, "deep history actions stay lazy");
  const afterWarmup = constructors;
  for (let index = 0; index < 1000; index += 1) {
    state._populateHistoryMenu(transcripts.map((entry) => ({ ...entry, text: `current-${index}` })));
  }
  assert.equal(constructors, afterWarmup);
  assert.equal(state.historyItem.menu.removeAllCount, 0);
  const row = state._historyMenuPool.rows[0];
  row.menu.open();
  row._socCopy.activate();
  assert.equal(state.copied, "current-999");
});

test("voice model menu reuses lazy catalog rows and current model data", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    modelItem: { actor: new Actor(), menu: new Menu() },
    _modelMenuFingerprint: null,
    _modelMenuPool: null,
    transcriber: "auto",
    whisperModel: "",
    transcriberCommand: "",
    openaiCompatibleModel: "voice-api",
    openaiCompatibleUrl: "http://localhost",
    lastTranscript: "",
    _ensureModelMenuPool: loadMethod("_ensureModelMenuPool", "_hydrateModelMenuPoolRow"),
    _hydrateModelMenuPoolRow: loadMethod("_hydrateModelMenuPoolRow", "_populateModelMenu"),
    _populateModelMenu: loadMethod("_populateModelMenu", "_populateExternalApiVoiceMenu"),
    _voiceModelLanguage: () => "de",
    _activeVoiceModelSummary: () => "automatic",
    _starterVoiceModelName: () => "starter",
    _modelPathFromPayload: (model) => `/models/${model.filename}`,
    _voiceModelSupportsCurrentLanguage: () => true,
    _isUsableVoiceModelPayload: (model) => model.downloaded === true,
    _selectAutomaticVoiceBackend() {},
    _selectStaticVoiceBackend() {},
    _openAppletSettings() {},
    _setStatusPreservingRecording() {},
    _downloadStarterModel() {},
    _openFolder() {},
    _openExternalApiEnvEditor() {},
    _selectVoiceModel(model) { state.selectedVoice = model.name; },
    _removeVoiceModel(model) { state.removedVoice = model.name; },
    _downloadVoiceModel(model) { state.downloadedVoice = model.name; },
  };
  const models = Array.from({ length: 20 }, (_unused, index) => ({
    name: `voice-${index}`,
    filename: `voice-${index}.bin`,
    model_format: index % 2 === 0 ? "ctranslate2" : "ggml",
    backend: index % 2 === 0 ? "faster-whisper" : "whisper-cpp",
    downloaded: true,
    description: `Voice ${index}`,
  }));
  state._populateModelMenu(models);
  const afterWarmup = constructors;
  for (let index = 0; index < 1000; index += 1) {
    state._populateModelMenu(models.map((model) => ({ ...model, name: `${model.name}-${index}` })));
  }
  assert.equal(constructors, afterWarmup);
  assert.equal(state.modelItem.menu.removeAllCount, 0);
  const row = state._modelMenuPool.ct2Rows[0];
  row.menu.open();
  row._socUse.activate();
  assert.equal(state.selectedVoice, "voice-0-999");
});

test("voice model refresh skips recent CLI work but refreshes after TTL", () => {
  const previousDate = methodContext.Date;
  let now = 1000;
  methodContext.Date = { now: () => now };
  try {
    const refreshModelMenu = loadMethod("_refreshModelMenu", "_populateModelMenu");
    let spawned = 0;
    const state = {
      ...commonState(),
      modelItem: { menu: { isOpen: true } },
      _modelMenuFingerprint: { loaded: true },
      _modelMenuLastRefreshAt: 1000,
      modelMenuRefreshToken: null,
      voiceModelActionToken: null,
      voiceModelCleanupFailed: false,
      isCommandRunning: false,
      _hasActiveRecordingState: () => false,
      _hasLocalProcessingWorkflow: () => false,
      _terminateProcessesByGroup: () => true,
      _modelsArgs: () => ["speed-of-cinnamon", "models"],
      _spawnJson: () => { spawned += 1; return {}; },
    };

    refreshModelMenu.call(state);
    assert.equal(spawned, 0);

    now = 6001;
    refreshModelMenu.call(state);
    assert.equal(spawned, 1);

    state.modelMenuRefreshToken = null;
    refreshModelMenu.call(state, true);
    assert.equal(spawned, 2);
  } finally {
    methodContext.Date = previousDate;
  }
});

test("text model menu reuses rows and current provider data", () => {
  constructors = 0;
  const state = {
    ...commonState(),
    textModelItem: { actor: new Actor(), menu: new Menu() },
    _textModelMenuFingerprint: null,
    _textModelMenuProvider: "",
    _textModelMenuPool: null,
    postProcessBackend: "ollama",
    postProcessCommand: "",
    ollamaModel: "text-0",
    openaiCompatibleTextModel: "",
    openaiCompatibleModel: "",
    postProcessPreset: "default",
    postProcessPreserveCode: true,
    postProcessNeverAddContent: true,
    postProcessMaskSensitiveData: false,
    lastTranscript: "",
    _ensureTextModelMenuPool: loadMethod("_ensureTextModelMenuPool", "_populateTextModelMenu"),
    _populateTextModelMenu: loadMethod("_populateTextModelMenu", "_canMutateMenu"),
    _selectTextModelBackend(provider, name) { state.selectedText = { provider, name }; },
    _activateOllamaTextModelFlow() {},
    _openExternalApiEnvEditor() {},
    _openAppletSettings() {},
    _setStatusPreservingRecording() {},
    _normalizeTextPolishingPreset: (value) => value,
    _textPolishingPresetLabel: (value) => value,
    _selectTextPolishingPreset() {},
    _optionLabel: (enabled, label) => `${enabled ? "[x]" : "[ ]"} ${label}`,
    _toggleTextPolishingSafetyFlag() {},
    _resetTextPolishingDefaults() {},
  };
  const models = Array.from({ length: 20 }, (_unused, index) => ({ name: `text-${index}`, description: `Text ${index}` }));
  state._populateTextModelMenu(models, "", "ollama");
  const afterWarmup = constructors;
  for (let index = 0; index < 1000; index += 1) {
    state._populateTextModelMenu(models.map((model) => ({ ...model, name: `${model.name}-${index}` })), "", "ollama");
  }
  assert.equal(constructors, afterWarmup);
  assert.equal(state.textModelItem.menu.removeAllCount, 0);
  state._textModelMenuPool.rows[0].activate();
  assert.deepEqual(state.selectedText, { provider: "ollama", name: "text-0-999" });
});
