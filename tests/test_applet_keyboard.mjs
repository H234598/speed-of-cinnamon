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

function loadAppletMethod(name, nextName, clock, extraContext = {}) {
  const start = appletSource.indexOf(`  ${name}: function(`);
  const end = appletSource.indexOf(`\n  ${nextName}:`, start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);
  const property = appletSource.slice(start, end).trim().replace(/,\s*$/, "");
  return vm.runInNewContext(`({${property}}).${name}`, {
    CLIPBOARD_READY_RETRY_MS: 40,
    CLIPBOARD_READY_TIMEOUT_MS: 100,
    MAX_KEYBOARD_COMMAND_TIMEOUT_MS: 300000,
    MAX_XDOTOOL_TARGET_OUTPUT_BYTES: 4096,
    PASTE_FOCUS_DELAY_MS: 25,
    PASTE_SUBMIT_DELAY_MS: 300,
    FOCUS_RESTORE_POLL_MS: 20,
    FOCUS_RESTORE_TIMEOUT_MS: 1000,
    X11_COMMAND_TIMEOUT_MS: 100,
    St: { ClipboardType: { CLIPBOARD: 1 } },
    Date: { now: () => clock.value },
    _: (value) => value,
    ...extraContext,
  });
}

function loadSpawnKeyboardArgs(clock) {
  return loadAppletMethod("_spawnKeyboardArgs", "_finishAppletTextInsert", clock);
}

test("built-in Auto-Submit marker matches title when class is unknown", () => {
  const clock = { value: 0 };
  const matchesMarker = loadAppletMethod(
    "_windowIdentityValueMatchesMarker",
    "_markerAllowsAutoPasteIdentity",
    clock
  );
  const matchesTitle = loadAppletMethod(
    "_windowTitleMatchesAutoPaste",
    "_updateOpenAiFlexProcessingItem",
    clock,
    { AUTO_PASTE_IDENTITY_MARKERS: { pdf: ["evince"] } }
  );
  const applet = {
    autoPasteWindowTitle: "PDF",
    targetWindow: null,
    targetWindowXTitle: "Quarterly report.pdf",
    targetWindowXClass: "unknown-reader",
    _autoPasteTitleValues: () => ["pdf"],
    _isUsableTargetWindow: () => false,
    _isTargetWindowXLookupPending: () => false,
    _windowProbeValue: () => "",
    _normalizedAutoPasteWindowTitle: (value) => String(value).trim().toLowerCase(),
    _windowIdentityMatchesAutoPaste: () => false,
    _windowIdentityValueMatchesMarker: matchesMarker,
  };

  assert.equal(matchesTitle.call(applet), true);
});

test("output policy separates paste from auto-submit and fails closed", () => {
  const clock = { value: 0 };
  const resolveOutputActions = loadAppletMethod(
    "_resolveOutputActions",
    "_migrateInsertMethodSemantics",
    clock
  );
  const methods = new Set([
    "clipboard-paste",
    "clipboard-paste-submit",
    "clipboard",
    "type",
    "none",
  ]);
  const applet = {
    _normalizeOutputMethod: (method) => methods.has(method) ? method : "none",
  };

  assert.deepEqual(
    { ...resolveOutputActions.call(applet, "clipboard-paste-submit", true, true) },
    { copy: true, restoreFocus: true, paste: true, submit: true }
  );
  assert.deepEqual(
    { ...resolveOutputActions.call(applet, "clipboard-paste", true, true) },
    { copy: true, restoreFocus: true, paste: true, submit: false }
  );
  assert.deepEqual(
    { ...resolveOutputActions.call(applet, "clipboard-paste-submit", false, true) },
    { copy: true, restoreFocus: false, paste: false, submit: false }
  );
  assert.deepEqual(
    { ...resolveOutputActions.call(applet, "clipboard-paste-submit", true, false) },
    { copy: true, restoreFocus: false, paste: false, submit: false }
  );
});

test("old clipboard-paste setting migrates once and preserves auto-submit", () => {
  const clock = { value: 0 };
  const migrateInsertMethodSemantics = loadAppletMethod(
    "_migrateInsertMethodSemantics",
    "_normalizeArtifactEncryption",
    clock,
    { OUTPUT_METHOD_SEMANTICS_VERSION: 2 }
  );
  const writes = [];
  const applet = {
    insertMethod: "clipboard-paste",
    insertMethodSemanticsVersion: 0,
    lastTranscript: "",
    _setSettingValueOrThrow(key, value) {
      writes.push([key, value]);
    },
    _recordLifecycleError() {},
    _setStatusPreservingRecording() {},
    _populateOutputMethodMenu() {},
    _updatePanel() {},
  };

  assert.equal(migrateInsertMethodSemantics.call(applet), true);
  assert.equal(applet.insertMethod, "clipboard-paste-submit");
  assert.equal(applet.insertMethodSemanticsVersion, 2);
  assert.deepEqual(writes, [
    ["insert-method", "clipboard-paste-submit"],
    ["insert-method-semantics-version", 2],
  ]);
  writes.length = 0;
  assert.equal(migrateInsertMethodSemantics.call(applet), false);
  assert.deepEqual(writes, []);
});

function loadSpawnKeyboardAfterFocus(clock) {
  return loadAppletMethod(
    "_spawnKeyboardAfterFocus",
    "_spawnKeyboardWhenClipboardReady",
    clock
  );
}

function loadSpawnKeyboardWhenClipboardReady(clock) {
  return loadAppletMethod(
    "_spawnKeyboardWhenClipboardReady",
    "_spawnKeyboardProcess",
    clock
  );
}

function loadTrackedTimerOwnedBy(clock) {
  return loadAppletMethod("_trackedTimerOwnedBy", "_untrackTimer", clock);
}

function makeTypeApplet(clock, delay) {
  const calls = [];
  const statuses = [];
  const applet = {
    lastTranscript: "",
    typingDelayMs: delay,
    _typeTextAfterFocus: loadAppletMethod(
      "_typeTextAfterFocus",
      "_coerceTypeText",
      clock,
      { X11_COMMAND_TIMEOUT_MS: 2000 }
    ),
    _coerceTypeText: (text) => String(text),
    _completeKeyboardInsertFailure() {},
    _findTrustedProgramInPath: () => "/usr/bin/xdotool",
    _normalizeTypingDelayMs: (value) => value,
    _setStatus(status, message) {
      statuses.push({ message, status });
    },
    _spawnKeyboardAfterFocus(...args) {
      calls.push(args);
      return true;
    },
    _targetXWindowSnapshot: () => ({ xid: "1" }),
  };
  return {
    applet,
    calls,
    statuses,
    invoke(text) {
      return applet._typeTextAfterFocus(
        text,
        () => {},
        () => true,
        "/usr/bin/xdotool"
      );
    },
  };
}

function makeCaptureApplet(clock) {
  const calls = [];
  const completions = [];
  const applet = {
    targetWindowGeneration: 1,
    targetWindowXPendingGeneration: 1,
    targetWindowXid: "",
    targetWindowXTitle: "",
    targetWindowXClass: "",
    _rememberActiveXWindow: loadAppletMethod(
      "_rememberActiveXWindow",
      "_activateTargetXWindow",
      clock
    ),
    _findTrustedProgramInPath: (name) => `/usr/bin/${name}`,
    _lifecycleAllowsWork: () => true,
    _notifySelfProtectionBlocked() {},
    _recordLifecycleError() {},
    _shortMenuText: (value, limit) => String(value).slice(0, limit),
    _xWindowLooksLikeSpeedOfCinnamon: () => false,
    _xdotoolOutput(args, _maxBytes, callback, timeoutMs) {
      calls.push({ args, callback, timeoutMs });
      return true;
    },
  };
  return {
    applet,
    calls,
    completions,
    invoke() {
      return applet._rememberActiveXWindow(
        (result) => completions.push(result),
        1
      );
    },
  };
}

function makeRestoreApplet(clock) {
  const completions = [];
  const targetWindow = {};
  let activations = 0;
  let fallbacks = 0;
  const display = { focus_window: targetWindow };
  const applet = {
    targetWindow,
    _restoreTargetWindowForPaste: loadAppletMethod(
      "_restoreTargetWindowForPaste",
      "_closeMenuForKeyboardInsert",
      clock,
      {
        global: {
          display,
          get_current_time: () => 1,
        },
        Main: {
          activateWindow() {
            activations += 1;
            return true;
          },
        },
      }
    ),
    _activateTargetXWindow(callback) {
      fallbacks += 1;
      callback(false);
      return false;
    },
    _isUsableTargetWindow: (window) => window === targetWindow,
    _lifecycleAllowsWork: () => true,
    _recordLifecycleError() {},
    _runStateGuarded(_group, callback) {
      callback();
    },
  };
  return {
    applet,
    activations: () => activations,
    completions,
    display,
    fallbacks: () => fallbacks,
    invoke() {
      return applet._restoreTargetWindowForPaste(
        (result) => completions.push(result)
      );
    },
  };
}

function makeDelayedRestoreApplet(clock) {
  const completions = [];
  const targetWindow = {};
  let activations = 0;
  let focusTimer = null;
  const display = { focus_window: null };
  const applet = {
    targetWindow,
    _restoreTargetWindowForPaste: loadAppletMethod(
      "_restoreTargetWindowForPaste",
      "_closeMenuForKeyboardInsert",
      clock,
      {
        global: {
          display,
          get_current_time: () => 1,
        },
        Main: {
          activateWindow() {
            activations += 1;
            return true;
          },
        },
      }
    ),
    _activateTargetXWindow(callback) {
      callback(false);
      return false;
    },
    _isUsableTargetWindow: (window) => window === targetWindow,
    _lifecycleAllowsWork: () => true,
    _recordLifecycleError() {},
    _runStateGuarded(_group, callback) {
      callback();
    },
    _scheduleTrackedTimer(_name, _delay, callback) {
      focusTimer = callback;
      return 1;
    },
  };
  return {
    applet,
    activations: () => activations,
    completions,
    display,
    fireFocusTimer() {
      assert.equal(typeof focusTimer, "function");
      return focusTimer();
    },
    invoke() {
      return applet._restoreTargetWindowForPaste(
        (result) => completions.push(result)
      );
    },
  };
}

function makeTargetApplet(clock) {
  const calls = [];
  const completions = [];
  const applet = {
    targetWindowGeneration: 1,
    _targetXWindowMatchesSnapshot: loadAppletMethod(
      "_targetXWindowMatchesSnapshot",
      "_targetXWindowMatchesSnapshotTitle",
      clock
    ),
    _targetXWindowMatchesSnapshotTitle: loadAppletMethod(
      "_targetXWindowMatchesSnapshotTitle",
      "_windowProbeValue",
      clock
    ),
    _isUsableTargetWindow: () => false,
    _shortMenuText: (value, limit) => String(value).slice(0, limit),
    _xWindowLooksLikeSpeedOfCinnamon: () => false,
    _notifySelfProtectionBlocked() {},
    _xdotoolOutput(args, _maxBytes, callback, timeoutMs) {
      calls.push({ args, callback, timeoutMs });
      return true;
    },
  };
  const snapshot = {
    targetWindowGeneration: 1,
    windowClass: "terminal",
    windowTitle: "shell",
    xid: "1",
  };
  return {
    applet,
    calls,
    completions,
    invoke() {
      return applet._targetXWindowMatchesSnapshot(
        snapshot,
        (result) => completions.push(result)
      );
    },
    snapshot,
  };
}

function makeApplet(clock) {
  const timers = new Map();
  const reads = [];
  const processes = [];
  const processCallbacks = [];
  const processTimeouts = [];
  const completions = [];
  const statuses = [];
  let clearPasteTimerCalls = 0;
  let nextTimerId = 1;
  let operationCurrent = true;

  const applet = {
    appletRemoved: false,
    pasteTimer: 0,
    failNextProcess: false,
    failNextTimer: false,
    queueProcessCallbacks: false,
    targetWindowMatches: true,
    _resourceRegistry: { timers: {} },
    clipboard: {
      get_text(_type, callback) {
        reads.push(callback);
      },
    },
    _spawnKeyboardAfterFocus: loadSpawnKeyboardAfterFocus(clock),
    _spawnKeyboardWhenClipboardReady: loadSpawnKeyboardWhenClipboardReady(clock),
    _spawnKeyboardArgs: loadSpawnKeyboardArgs(clock),
    _lifecycleAllowsWork: () => true,
    _guardStateCallback: (_group, callback) => callback,
    _recordLifecycleError() {},
    _setStatus(status, message) {
      statuses.push({ status, message });
    },
    _completeKeyboardInsertFailure(callback) {
      if (typeof callback === "function") {
        callback(false);
      }
    },
    _trackedTimerOwnedBy: loadTrackedTimerOwnedBy(clock),
    _scheduleTrackedTimer(name, delay, callback, _seconds, propertyName) {
      if (this.failNextTimer) {
        this.failNextTimer = false;
        return 0;
      }
      const previousId = this._resourceRegistry.timers[name] || (
        propertyName ? this[propertyName] : 0
      );
      if (previousId) {
        timers.delete(previousId);
        if (this._resourceRegistry.timers[name] === previousId) {
          delete this._resourceRegistry.timers[name];
        }
        if (propertyName && this[propertyName] === previousId) {
          this[propertyName] = 0;
        }
      }
      const id = nextTimerId++;
      timers.set(id, { callback, delay, name, propertyName });
      this._resourceRegistry.timers[name] = id;
      if (propertyName) {
        this[propertyName] = id;
      }
      assert.equal(
        [...timers.values()].filter((entry) => entry.name === name).length,
        1
      );
      return id;
    },
    _clearPasteTimer() {
      clearPasteTimerCalls += 1;
      const id = this.pasteTimer;
      if (!id) {
        return true;
      }
      timers.delete(id);
      if (this._resourceRegistry.timers.paste === id) {
        delete this._resourceRegistry.timers.paste;
      }
      if (this.pasteTimer === id) {
        this.pasteTimer = 0;
      }
      return true;
    },
    _targetXWindowMatchesSnapshot(_snapshot, callback) {
      callback(this.targetWindowMatches);
      return this.targetWindowMatches;
    },
    _windowTitleMatchesAutoPaste() {
      return true;
    },
    _spawnKeyboardProcess(args, callback, timeoutMs) {
      processes.push(args);
      processTimeouts.push(timeoutMs);
      if (this.failNextProcess) {
        this.failNextProcess = false;
        callback(false);
        return false;
      }
      if (this.queueProcessCallbacks) {
        processCallbacks.push(callback);
      } else {
        callback(true);
      }
      return true;
    },
    fireTimer(id) {
      const entry = timers.get(id);
      assert.ok(entry, `missing timer ${id}`);
      const keep = entry.callback();
      if (keep === false) {
        timers.delete(id);
        if (this._resourceRegistry.timers[entry.name] === id) {
          delete this._resourceRegistry.timers[entry.name];
        }
        if (entry.propertyName && this[entry.propertyName] === id) {
          this[entry.propertyName] = 0;
        }
      }
      return keep;
    },
    fireCallback(callback) {
      return callback();
    },
    invoke(expected = "text", deadline = 1100, followUpArgs = null, processTimeoutMs = null) {
      // Models _spawnKeyboardAfterFocus.complete(), which guards every production entry.
      let outerCompletionDelivered = false;
      this._spawnKeyboardArgs(
        ["xdotool", "key", "ctrl+v"],
        followUpArgs,
        { xid: "1" },
        expected,
        deadline,
        (result) => {
          if (outerCompletionDelivered) {
            return;
          }
          outerCompletionDelivered = true;
          completions.push(result);
        },
        () => operationCurrent,
        processTimeoutMs
      );
    },
    invokeAfterFocus(expected = "text", followUpArgs = null, processTimeoutMs = null) {
      return this._spawnKeyboardAfterFocus(
        ["xdotool", "key", "ctrl+v"],
        followUpArgs,
        expected,
        { xid: "1" },
        (result) => completions.push(result),
        () => operationCurrent,
        processTimeoutMs
      );
    },
    setOperationCurrent(value) {
      operationCurrent = value;
    },
  };

  return {
    applet,
    completions,
    processes,
    processCallbacks,
    processTimeouts,
    reads,
    statuses,
    timers,
    clearPasteTimerCalls: () => clearPasteTimerCalls,
  };
}

test("clipboard read timeout completes and blocks late callback", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.invoke();

  const watchdogId = state.applet.pasteTimer;
  assert.notEqual(watchdogId, 0);
  clock.value = 1100;
  assert.equal(state.applet.fireTimer(watchdogId), false);
  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);

  state.reads[0](null, "text");
  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);
});

test("matching clipboard callback at deadline fails closed", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.invoke();

  clock.value = 1100;
  state.reads[0](null, "text");

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);
});

test("late callback preserves foreign paste timer owner", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.invoke();

  const foreignTimerId = state.applet.pasteTimer + 1;
  state.applet.pasteTimer = foreignTimerId;
  state.applet._resourceRegistry.timers.paste = foreignTimerId;
  state.reads[0](null, "text");

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);
  assert.equal(state.clearPasteTimerCalls(), 0);
  assert.equal(state.applet.pasteTimer, foreignTimerId);
  assert.equal(state.applet._resourceRegistry.timers.paste, foreignTimerId);
});

test("stale focus callback cannot clear new insertion timer", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const newCompletions = [];
  const staleCompletions = [];

  state.applet._spawnKeyboardAfterFocus(
    ["xdotool", "key", "ctrl+v"],
    null,
    "new text",
    { xid: "new" },
    (result) => newCompletions.push(result),
    () => true,
  );
  const newTimerId = state.applet.pasteTimer;
  assert.notEqual(newTimerId, 0);

  state.applet._spawnKeyboardAfterFocus(
    ["xdotool", "key", "ctrl+v"],
    null,
    "stale text",
    { xid: "stale" },
    (result) => staleCompletions.push(result),
    () => false,
  );

  assert.deepEqual(staleCompletions, [false]);
  assert.equal(state.applet.pasteTimer, newTimerId);
  assert.equal(state.applet._resourceRegistry.timers.paste, newTimerId);
  assert.equal(state.clearPasteTimerCalls(), 1);

  state.applet.fireTimer(newTimerId);
  state.reads[0](null, "new text");
  assert.deepEqual(newCompletions, [true]);
});

test("matching clipboard callback before deadline is exactly once", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.invoke();

  const watchdogId = state.applet.pasteTimer;
  const watchdogCallback = state.timers.get(watchdogId).callback;
  clock.value = 1050;
  state.reads[0](null, "text");
  state.reads[0](null, "text");
  state.applet.fireCallback(watchdogCallback);

  assert.deepEqual(state.completions, [true]);
  assert.equal(state.processes.length, 1);
  assert.equal(state.clearPasteTimerCalls(), 1);
  assert.equal(state.applet.pasteTimer, 0);
  assert.equal(state.timers.size, 0);
});

test("duplicate clipboard callback while paste is pending is ignored", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.queueProcessCallbacks = true;
  state.applet.invoke();

  clock.value = 1050;
  state.reads[0](null, "text");
  state.reads[0](null, "text");

  assert.deepEqual(state.completions, []);
  assert.equal(state.processes.length, 1);
  assert.equal(state.processCallbacks.length, 1);

  state.processCallbacks[0](true);

  assert.deepEqual(state.completions, [true]);
});

test("paste without follow-up revalidates target before completion", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  let targetChecks = 0;
  state.applet.queueProcessCallbacks = true;
  state.applet._targetXWindowMatchesSnapshot = (_snapshot, callback) => {
    targetChecks += 1;
    callback(targetChecks === 1);
    return targetChecks === 1;
  };
  state.applet.invoke("text");

  state.reads[0](null, "text");
  assert.equal(state.processCallbacks.length, 1);
  state.processCallbacks[0](true);

  assert.equal(targetChecks, 2);
  assert.deepEqual(state.completions, [false]);
});

test("clipboard mismatch retries with one owner before success", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.invoke();

  const firstWatchdogId = state.applet.pasteTimer;
  clock.value = 1020;
  state.reads[0](null, "old");

  const retryTimerId = state.applet.pasteTimer;
  assert.notEqual(retryTimerId, firstWatchdogId);
  assert.equal(state.timers.size, 1);

  state.applet.fireTimer(retryTimerId);
  assert.equal(state.reads.length, 2);
  assert.equal(state.timers.size, 1);

  clock.value = 1040;
  state.reads[1](null, "text");

  assert.deepEqual(state.completions, [true]);
  assert.equal(state.processes.length, 1);
  assert.equal(state.timers.size, 0);
});

test("successful paste starts follow-up Enter as second process", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];

  state.applet._spawnKeyboardArgs(
    firstArgs,
    followUpArgs,
    { xid: "1" },
    null,
    null,
    (result) => state.completions.push(result),
    () => true
  );

  assert.deepEqual(state.processes, [firstArgs]);
  const submitTimerId = state.applet.pasteTimer;
  assert.notEqual(submitTimerId, 0);

  state.applet.fireTimer(submitTimerId);

  assert.deepEqual(state.completions, [true]);
  assert.deepEqual(state.processes, [firstArgs, followUpArgs]);
});

test("submit title mismatch fails instead of claiming success", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];

  state.applet._windowTitleMatchesAutoPaste = () => false;
  state.applet._spawnKeyboardArgs(
    firstArgs,
    followUpArgs,
    { xid: "1" },
    null,
    null,
    (result) => state.completions.push(result),
    () => true
  );

  state.applet.fireTimer(state.applet.pasteTimer);

  assert.deepEqual(state.completions, [false]);
  assert.deepEqual(state.processes, [firstArgs]);
});

test("stale operation completes without process start", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.invoke();

  const watchdogId = state.applet.pasteTimer;
  state.applet.setOperationCurrent(false);
  state.reads[0](null, "text");

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);

  state.applet.fireTimer(watchdogId);

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);
  assert.equal(state.applet.pasteTimer, 0);
});

test("window mismatch completes without process start", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.targetWindowMatches = false;

  state.applet._spawnKeyboardArgs(
    ["xdotool", "key", "ctrl+v"],
    null,
    { xid: "1" },
    null,
    null,
    (result) => state.completions.push(result),
    () => true
  );

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.processes.length, 0);
});

test("first keyboard process failure completes without follow-up", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  state.applet.failNextProcess = true;

  state.applet._spawnKeyboardArgs(
    firstArgs,
    ["xdotool", "key", "Return"],
    { xid: "1" },
    null,
    null,
    (result) => state.completions.push(result),
    () => true
  );

  assert.deepEqual(state.completions, [false]);
  assert.deepEqual(state.processes, [firstArgs]);
  assert.equal(state.applet.pasteTimer, 0);
});

test("operation stale before first process callback blocks submit", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];
  state.applet.queueProcessCallbacks = true;

  state.applet.invoke(null, null, followUpArgs);

  assert.deepEqual(state.processes, [firstArgs]);
  assert.equal(state.processCallbacks.length, 1);

  state.applet.setOperationCurrent(false);
  state.processCallbacks[0](true);

  assert.deepEqual(state.completions, [false]);
  assert.deepEqual(state.processes, [firstArgs]);
  assert.equal(state.applet.pasteTimer, 0);
  assert.equal(state.timers.size, 0);
});

test("submit window mismatch after timer blocks follow-up process", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];
  state.applet.queueProcessCallbacks = true;

  state.applet.invoke(null, null, followUpArgs);
  state.processCallbacks[0](true);

  const submitTimerId = state.applet.pasteTimer;
  assert.notEqual(submitTimerId, 0);

  state.applet.targetWindowMatches = false;
  state.applet.fireTimer(submitTimerId);

  assert.deepEqual(state.completions, [false]);
  assert.deepEqual(state.processes, [firstArgs]);
  assert.equal(state.processCallbacks.length, 1);
  assert.equal(state.applet.pasteTimer, 0);
});

test("duplicate first process callback submits and completes once", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];
  state.applet.queueProcessCallbacks = true;

  state.applet.invoke(null, null, followUpArgs);

  const firstCallback = state.processCallbacks[0];
  firstCallback(true);
  firstCallback(true);

  assert.deepEqual(state.completions, []);
  assert.deepEqual(state.processes, [firstArgs]);
  assert.equal(state.timers.size, 1);

  const submitTimerId = state.applet.pasteTimer;
  assert.notEqual(submitTimerId, 0);
  state.applet.fireTimer(submitTimerId);

  assert.deepEqual(state.processes, [firstArgs, followUpArgs]);
  assert.equal(state.processCallbacks.length, 2);

  state.processCallbacks[1](true);

  assert.deepEqual(state.completions, [true]);
  assert.equal(state.applet.pasteTimer, 0);
  assert.equal(state.timers.size, 0);
});

test("follow-up process failure and duplicate callback complete once", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];
  state.applet.queueProcessCallbacks = true;

  state.applet.invoke(null, null, followUpArgs);
  state.processCallbacks[0](true);

  const submitTimerId = state.applet.pasteTimer;
  assert.notEqual(submitTimerId, 0);
  state.applet.fireTimer(submitTimerId);

  assert.deepEqual(state.processes, [firstArgs, followUpArgs]);
  assert.equal(state.processCallbacks.length, 2);

  const followUpCallback = state.processCallbacks[1];
  followUpCallback(false);
  followUpCallback(false);

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.applet.pasteTimer, 0);
  assert.equal(state.timers.size, 0);
});

test("synchronous first process failure reports once", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.failNextProcess = true;

  state.applet.invoke(null, null, ["xdotool", "key", "Return"]);

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.statuses.length, 1);
  assert.equal(state.applet.pasteTimer, 0);
});

test("synchronous follow-up process failure reports once", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  const firstArgs = ["xdotool", "key", "ctrl+v"];
  const followUpArgs = ["xdotool", "key", "Return"];
  state.applet.queueProcessCallbacks = true;

  state.applet.invoke(null, null, followUpArgs);
  state.processCallbacks[0](true);
  const submitTimerId = state.applet.pasteTimer;
  assert.notEqual(submitTimerId, 0);

  state.applet.failNextProcess = true;
  state.applet.fireTimer(submitTimerId);

  assert.deepEqual(state.processes, [firstArgs, followUpArgs]);
  assert.deepEqual(state.completions, [false]);
  assert.equal(state.statuses.length, 1);
  assert.equal(state.applet.pasteTimer, 0);
});

test("real focus path delays then verifies clipboard before paste", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);

  assert.equal(state.applet.invokeAfterFocus(), true);
  const focusTimerId = state.applet.pasteTimer;
  assert.equal(state.timers.get(focusTimerId).delay, 25);
  assert.equal(state.reads.length, 0);

  state.applet.fireTimer(focusTimerId);
  const watchdogId = state.applet.pasteTimer;
  assert.notEqual(watchdogId, focusTimerId);
  assert.equal(state.timers.get(watchdogId).delay, 100);
  assert.equal(state.reads.length, 1);

  state.reads[0](null, "text");

  assert.deepEqual(state.processes, [["xdotool", "key", "ctrl+v"]]);
  assert.deepEqual(state.completions, [true]);
  assert.equal(state.applet.pasteTimer, 0);
});

test("real focus path deduplicates inner completion", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.queueProcessCallbacks = true;

  assert.equal(state.applet.invokeAfterFocus(null), true);
  state.applet.fireTimer(state.applet.pasteTimer);
  assert.equal(state.processCallbacks.length, 1);

  state.processCallbacks[0](true);
  state.processCallbacks[0](false);

  assert.deepEqual(state.completions, [true]);
});

test("real focus path reports timer scheduling failure once", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.failNextTimer = true;

  assert.equal(state.applet.invokeAfterFocus(), false);

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.statuses.length, 1);
  assert.equal(state.timers.size, 0);
});

test("real focus path aborts stale operation without timer", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);
  state.applet.setOperationCurrent(false);

  assert.equal(state.applet.invokeAfterFocus(), false);

  assert.deepEqual(state.completions, [false]);
  assert.equal(state.timers.size, 0);
});

test("target validation stops before class probe at expired deadline", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);

  assert.equal(state.invoke(), true);
  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0].timeoutMs, 100);

  clock.value = 1100;
  state.calls[0].callback("1");

  assert.equal(state.calls.length, 1);
  assert.deepEqual(state.completions, [false]);
});

test("target validation stops before title probe at expired deadline", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);
  state.snapshot.windowClass = "";

  state.invoke();
  state.calls[0].callback("1");
  assert.equal(state.calls.length, 2);

  clock.value = 1100;
  state.calls[1].callback("terminal");

  assert.equal(state.calls.length, 2);
  assert.deepEqual(state.completions, [false]);
});

test("target title fallback passes exact remaining budget to every probe", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);
  state.snapshot.windowClass = "";

  state.invoke();
  assert.equal(state.calls[0].timeoutMs, 100);

  clock.value = 1025;
  state.calls[0].callback("1");
  assert.equal(state.calls[1].timeoutMs, 75);

  clock.value = 1050;
  state.calls[1].callback("shell");

  assert.deepEqual(state.completions, [true]);
});

test("target validation accepts stable XID and class after title changes", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);

  state.snapshot.windowTitle = "codex: waiting";
  state.invoke();
  state.calls[0].callback("1");
  state.calls[1].callback("terminal");

  assert.equal(state.calls.length, 2);
  assert.deepEqual(state.completions, [true]);
});

test("target title validation rejects expired direct deadline without spawn", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);

  state.applet._targetXWindowMatchesSnapshotTitle(
    state.snapshot,
    "1",
    (result) => state.completions.push(result),
    1000
  );

  assert.equal(state.calls.length, 0);
  assert.deepEqual(state.completions, [false]);
});

test("target title validation rejects matching callback after deadline", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);
  state.snapshot.windowClass = "";

  state.invoke();
  state.calls[0].callback("1");
  clock.value = 1100;
  state.calls[1].callback("shell");

  assert.deepEqual(state.completions, [false]);
});

test("target validation rejects long titles differing only in middle", () => {
  const clock = { value: 1000 };
  const state = makeTargetApplet(clock);
  state.snapshot.windowClass = "";
  const prefix = "a".repeat(100);
  const suffix = "z".repeat(100);
  state.snapshot.windowTitle = `${prefix}expected-middle${suffix}`;

  state.invoke();
  state.calls[0].callback("1");
  state.calls[1].callback(`${prefix}changed-middle${suffix}`);

  assert.deepEqual(state.completions, [false]);
});

test("target capture stops before title probe at expired deadline", () => {
  const clock = { value: 1000 };
  const state = makeCaptureApplet(clock);

  assert.equal(state.invoke(), true);
  assert.equal(state.calls[0].timeoutMs, 100);

  clock.value = 1100;
  state.calls[0].callback("1");

  assert.equal(state.calls.length, 1);
  assert.deepEqual(state.completions, [false]);
});

test("target capture stops before class probe at expired deadline", () => {
  const clock = { value: 1000 };
  const state = makeCaptureApplet(clock);

  state.invoke();
  state.calls[0].callback("1");
  assert.equal(state.calls.length, 2);

  clock.value = 1100;
  state.calls[1].callback("shell");

  assert.equal(state.calls.length, 2);
  assert.deepEqual(state.completions, [false]);
});

test("target capture rejects class callback after deadline", () => {
  const clock = { value: 1000 };
  const state = makeCaptureApplet(clock);

  state.invoke();
  state.calls[0].callback("1");
  state.calls[1].callback("shell");
  assert.equal(state.calls.length, 3);

  clock.value = 1100;
  state.calls[2].callback("terminal");

  assert.equal(state.applet.targetWindowXid, "");
  assert.deepEqual(state.completions, [false]);
});

test("target capture passes exact remaining budget and commits in time", () => {
  const clock = { value: 1000 };
  const state = makeCaptureApplet(clock);

  state.invoke();
  assert.equal(state.calls[0].timeoutMs, 100);
  clock.value = 1025;
  state.calls[0].callback("1");
  assert.equal(state.calls[1].timeoutMs, 75);
  clock.value = 1050;
  state.calls[1].callback("shell");
  assert.equal(state.calls[2].timeoutMs, 50);
  state.calls[2].callback("terminal");

  assert.equal(state.applet.targetWindowXid, "1");
  assert.deepEqual(state.completions, [true]);
});

test("focused target restore skips redundant compositor activation", () => {
  const clock = { value: 1000 };
  const state = makeRestoreApplet(clock);

  assert.equal(state.invoke(), true);

  assert.deepEqual(state.completions, [true]);
  assert.equal(state.activations(), 0);
  assert.equal(state.fallbacks(), 0);
});

test("target restore waits for observed focus after compositor activation", () => {
  const clock = { value: 1000 };
  const state = makeDelayedRestoreApplet(clock);

  assert.equal(state.invoke(), true);
  assert.equal(state.activations(), 1);
  assert.deepEqual(state.completions, []);

  state.fireFocusTimer();
  assert.deepEqual(state.completions, []);

  state.display.focus_window = state.applet.targetWindow;
  state.fireFocusTimer();
  assert.deepEqual(state.completions, [true]);
});

test("direct typing derives bounded timeout from codepoints and delay", () => {
  const clock = { value: 1000 };
  const state = makeTypeApplet(clock, 8);
  const text = "x".repeat(4000);

  assert.equal(state.invoke(text), true);

  assert.equal(state.calls.length, 1);
  assert.equal(state.calls[0][6], 33992);
  assert.equal(state.statuses.length, 0);
});

test("direct typing timeout counts Unicode codepoints", () => {
  const clock = { value: 1000 };
  const state = makeTypeApplet(clock, 100);

  assert.equal(state.invoke("😀a"), true);

  assert.equal(state.calls[0][6], 2100);
});

test("direct typing rejects unsafe duration before spawn", () => {
  const clock = { value: 1000 };
  const state = makeTypeApplet(clock, 10000);

  assert.equal(state.invoke("x".repeat(40)), false);

  assert.equal(state.calls.length, 0);
  assert.equal(state.statuses.length, 1);
  assert.equal(state.statuses[0].status, "error");
});

test("direct typing timeout reaches keyboard process through focus path", () => {
  const clock = { value: 1000 };
  const state = makeApplet(clock);

  state.applet.invokeAfterFocus(null, null, 33992);
  state.applet.fireTimer(state.applet.pasteTimer);

  assert.deepEqual(state.processTimeouts, [33992]);
  assert.deepEqual(state.completions, [true]);
});

test("direct typing accepts exact maximum timeout and rejects next codepoint", () => {
  const clock = { value: 1000 };

  const atLimit = makeTypeApplet(clock, 1000);
  assert.equal(atLimit.invoke("x".repeat(299)), true);
  assert.equal(atLimit.calls[0][6], 300000);

  const aboveLimit = makeTypeApplet(clock, 1000);
  assert.equal(aboveLimit.invoke("x".repeat(300)), false);
  assert.equal(aboveLimit.calls.length, 0);
  assert.equal(aboveLimit.statuses.length, 1);
  assert.equal(aboveLimit.statuses[0].status, "error");
});

function makeAsyncTargetRememberApplet(clock) {
  let pendingCallback = null;
  const completions = [];
  const applet = {
    targetWindow: null,
    targetWindowGeneration: 0,
    targetWindowXPendingGeneration: 0,
    targetWindowXid: "",
    targetWindowXTitle: "",
    targetWindowXClass: "",
    lastTranscript: "",
    _rememberFocusedWindow: loadAppletMethod(
      "_rememberFocusedWindow",
      "_restoreTargetWindowForPaste",
      clock,
      {
        global: { display: { focus_window: null } },
      }
    ),
    _terminateProcessesByGroup: () => true,
    _isUsableTargetWindow: () => false,
    _windowLooksLikeSpeedOfCinnamon: () => false,
    _rememberActiveXWindow(callback) {
      pendingCallback = callback;
      return true;
    },
    _clearTargetWindowXid() {
      this.targetWindowXid = "";
      this.targetWindowXTitle = "";
      this.targetWindowXClass = "";
    },
    _hasRememberedTargetWindow() {
      return /^[0-9]+$/.test(String(this.targetWindowXid || "").trim());
    },
    _setStatusPreservingRecording() {},
    _recordLifecycleError() {},
    _lifecycleAllowsWork: () => true,
  };
  return {
    applet,
    completions,
    resolve(value) {
      pendingCallback(value);
    },
  };
}

test("async X11 target resolution gates completion until fallback resolves", () => {
  const state = makeAsyncTargetRememberApplet({ value: 1000 });

  assert.equal(
    state.applet._rememberFocusedWindow(false, (result) => state.completions.push(result)),
    true
  );
  assert.deepEqual(state.completions, []);
  assert.equal(state.applet.targetWindowXPendingGeneration, 1);

  state.resolve(true);

  assert.deepEqual(state.completions, [true]);
  assert.equal(state.applet.targetWindowXPendingGeneration, 0);
});
