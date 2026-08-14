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

  const property = appletSource
    .slice(start, end)
    .trim()
    .replace(/,\s*$/, "");

  return vm.runInNewContext(`({${property}}).${name}`, {
    CLIPBOARD_READY_RETRY_MS: 40,
    CLIPBOARD_READY_TIMEOUT_MS: 100,
    MAX_CANCEL_RECOVERY_ATTEMPTS: 3,
    MAX_KEYBOARD_COMMAND_TIMEOUT_MS: 300000,
    MAX_XDOTOOL_TARGET_OUTPUT_BYTES: 4096,
    PASTE_FOCUS_DELAY_MS: 25,
    PASTE_SUBMIT_DELAY_MS: 300,
    X11_COMMAND_TIMEOUT_MS: 100,
    St: { ClipboardType: { CLIPBOARD: 1 } },
    Date: { now: () => clock.value },
    _: (value) => value,
    setTimeout,
    clearTimeout,
    ...extraContext,
  });
}

test("empty done payload follows relisten completion while insert is active", () => {
  const clock = { value: 0 };
  const finishAppletTextInsert = loadAppletMethod(
    "_finishAppletTextInsert",
    "_insertTranscriptText",
    clock
  );
  const completedPayloads = [];
  const applet = {
    textInsertToken: "active-insert",
    _isEmptyTranscriptText: (value) => String(value || "").trim() === "",
    _finishEmptyRelistenDone(payload) {
      completedPayloads.push(payload);
    },
  };

  finishAppletTextInsert.call(applet, { status: "done", transcript: "" });

  assert.equal(completedPayloads.length, 1);
  assert.equal(completedPayloads[0].status, "done");
});

test("preserved status refreshes transcript action sensitivity", () => {
  const clock = { value: 0 };
  const setStatusPreservingRecording = loadAppletMethod(
    "_setStatusPreservingRecording",
    "_setStatus",
    clock
  );
  const copyItem = {};
  const insertItem = {};
  const cancelItem = {};
  const applet = {
    status: "recording",
    _statusRefreshToken: 0,
    lastTranscript: "",
    copyLastItem: copyItem,
    insertLastItem: insertItem,
    cancelItem,
    _lifecycleAllowsWork: () => true,
    _hasActiveRecordingState: () => true,
    _hasCancelableRecordingWork: () => true,
    _uiMessageText: (value) => value,
    _sanitizeErrorMessage: (value) => value,
    _setMenuItemSensitiveSafely(item, sensitive) {
      item.sensitive = sensitive;
    },
    _updatePanel() {},
  };

  setStatusPreservingRecording.call(applet, "done", "Transcript ready", "hello");

  assert.equal(applet.lastTranscript, "hello");
  assert.equal(copyItem.sensitive, true);
  assert.equal(insertItem.sensitive, true);
  assert.equal(cancelItem.sensitive, true);
});

test("settings import preserves only allowlisted status icons", () => {
  const clock = { value: 0 };
  const coerceImportedSetting = loadAppletMethod(
    "_coerceImportedSetting",
    "_coerceImportedEnumSetting",
    clock,
    {
      BOOLEAN_IMPORT_SETTINGS: {},
      STATUS_ICON_ALLOWLIST: { "soc-original": true, "ready-01": true },
    }
  );
  const applet = {
    _coerceImportedEnumSetting(value, allowedValues, fallback) {
      return allowedValues.includes(value) ? value : fallback;
    },
  };

  assert.equal(
    coerceImportedSetting.call(applet, "status-icon-ready", "ready-01", "soc-original"),
    "ready-01"
  );
  assert.equal(
    coerceImportedSetting.call(applet, "status-icon-ready", "ready-999", "soc-original"),
    "soc-original"
  );
});

test("invalid backend payload fails closed without breaking recording state handling", () => {
  for (const payload of [null, undefined, [], "invalid"]) {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;

    assert.doesNotThrow(() => applet._applyPayload(payload));
    assert.equal(applet.status, "error");
    assert.match(applet.lastMessage, /Backend returned an invalid response/);
  }
});

test("recording starts when only focus target capture fails", () => {
  const clock = { value: 0 };
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  let toggleCalls = 0;
  const applet = {
    status: "idle",
    lastTranscript: "",
    isCommandRunning: false,
    _recordingCommandToken: null,
    _hasActiveRecordingState: () => false,
    _setStatusPreservingRecording() {},
    _rememberFocusedWindow(_preserveTargetOnFailure, completionCallback) {
      completionCallback(false, true);
      return true;
    },
    _normalizeLanguage: (value) => value,
    _primaryLanguage: () => "en",
    _toggleRecording(action) {
      assert.equal(action, "start");
      toggleCalls += 1;
      return true;
    },
  };

  assert.equal(startWithLanguage.call(applet, "en"), true);
  assert.equal(toggleCalls, 1);
});

test("language start does not report success before focus callback", () => {
  const clock = { value: 0 };
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  let completeFocus;
  let toggleCalls = 0;
  const applet = {
    status: "idle",
    lastTranscript: "",
    isCommandRunning: false,
    _recordingCommandToken: null,
    _hasActiveRecordingState: () => false,
    _setStatusPreservingRecording() {},
    _rememberFocusedWindow(_preserveTargetOnFailure, completionCallback) {
      completeFocus = completionCallback;
      return true;
    },
    _normalizeLanguage: (value) => value,
    _primaryLanguage: () => "en",
    _toggleRecording(action) {
      assert.equal(action, "start");
      toggleCalls += 1;
      return true;
    },
  };

  assert.equal(startWithLanguage.call(applet, "en"), false);
  assert.equal(toggleCalls, 0);
  completeFocus(true, false);
  assert.equal(toggleCalls, 1);
});

test("unrelated orphaned timer does not block status timer scheduling", () => {
  const clock = { value: 0 };
  let nextTimerId = 1;
  let retryCalls = 0;
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    clock,
    {
      Mainloop: {
        timeout_add() {
          return nextTimerId++;
        },
        timeout_add_seconds() {
          return nextTimerId++;
        },
        source_remove() {
          return true;
        },
      },
    }
  );
  const applet = {
    appletRemoved: false,
    lifecycleState: "active",
    spawnGeneration: 1,
    _orphanedTimers: [{ name: "clipboard", sourceId: 99, propertyName: "clipboardTimer" }],
    _resourceRegistry: { timers: {} },
    _lifecycleAllowsWork: () => true,
    _retryOrphanedTimers() {
      retryCalls += 1;
      return false;
    },
    _clearTrackedTimer: () => true,
    _trackTimer(name, sourceId, propertyName) {
      this._resourceRegistry.timers[name] = sourceId;
      if (propertyName) {
        this[propertyName] = sourceId;
      }
      return sourceId;
    },
    _recordLifecycleError() {},
  };

  const scheduled = scheduleTrackedTimer.call(
    applet,
    "status",
    10,
    () => false,
    false,
    "statusTimer"
  );

  assert.equal(retryCalls, 1);
  assert.equal(scheduled, 1);
  assert.equal(applet.statusTimer, 1);
});

test("direct typing aborts when target changes during focus restore", () => {
  const clock = { value: 0 };
  const insertTranscriptText = loadAppletMethod(
    "_insertTranscriptText",
    "_restartRelistenRecording",
    clock
  );
  let restoreCallback = null;
  let typedCalls = 0;
  let targetChecks = 0;
  const expectedTarget = {
    xid: "10",
    windowClass: "editor",
    windowTitle: "notes",
    targetWindowGeneration: 1,
  };
  const applet = {
    insertMethod: "type",
    textInsertCancellationFailed: false,
    textInsertToken: null,
    clipboardOverwriteDialog: null,
    autoInsertConflictToken: null,
    autoRelistenPending: false,
    targetWindowGeneration: 1,
    lastTranscript: "",
    _lifecycleAllowsWork: () => true,
    _hasPendingTextInsertResources: () => false,
    _normalizeOutputMethod: () => "type",
    _findTrustedProgramInPath: () => "/usr/bin/xdotool",
    _preparedTranscriptText: (value) => value,
    _isEmptyTranscriptText: () => false,
    _closeMenuForKeyboardInsert: () => true,
    _targetXWindowSnapshot: () => expectedTarget,
    _restoreTargetWindowForPaste(callback) {
      restoreCallback = callback;
      return true;
    },
    _targetXWindowMatchesSnapshot(_snapshot, callback) {
      targetChecks += 1;
      callback(false);
      return false;
    },
    _typeTextAfterFocus() {
      typedCalls += 1;
      return true;
    },
    _setStatus() {},
    _setStatusPreservingRecording() {},
    _recordLifecycleError() {},
  };

  assert.equal(insertTranscriptText.call(applet, "secret", () => {}), null);
  assert.equal(typeof restoreCallback, "function");
  restoreCallback(true);

  assert.equal(targetChecks, 1);
  assert.equal(typedCalls, 0);
});

test("clipboard-paste falls back to clipboard when menu close fails", () => {
  const clock = { value: 0 };
  const copyAndMaybePaste = loadAppletMethod(
    "_copyAndMaybePasteTranscriptText",
    "_confirmClipboardOverwriteForPaste",
    clock
  );
  const clipboardWrites = [];
  const statuses = [];
  const completions = [];
  const applet = {
    _lifecycleAllowsWork: () => true,
    _closeMenuForKeyboardInsert: () => false,
    _setClipboardText(text) {
      clipboardWrites.push(text);
      return true;
    },
    _setStatus(status, message, transcript) {
      statuses.push({ status, message, transcript });
    },
  };

  assert.equal(
    copyAndMaybePaste.call(
      applet,
      "hello",
      "hello",
      "clipboard-paste",
      true,
      false,
      (result) => completions.push(result),
      () => true
    ),
    false
  );

  assert.deepEqual(clipboardWrites, ["hello"]);
  assert.deepEqual(completions, [false]);
  assert.deepEqual(statuses, [{
    status: "error",
    message: "Copied to clipboard; paste failed: applet menu could not be closed",
    transcript: "hello",
  }]);
});

test("synchronous text insertion failure releases fingerprint reservation", () => {
  const clock = { value: 0 };
  const finishAppletTextInsert = loadAppletMethod(
    "_finishAppletTextInsert",
    "_ensureAutoRelistenPendingForDonePayload",
    clock
  );
  const forgottenFingerprints = [];
  const applet = {
    textInsertToken: null,
    autoInsertPendingFingerprint: "",
    autoRelistenPending: true,
    autoRelistenPendingToken: "relisten",
    autoRelistenPendingLanguage: "en",
    autoRelistenManualStopRequested: false,
    autoInsertConflictToken: null,
    lastTranscript: "",
    _isEmptyTranscriptText: () => false,
    _autoInsertFingerprint: () => "fingerprint",
    _ensureAutoRelistenPendingForDonePayload() {},
    _reserveAutoInsertFingerprint: () => true,
    _forgetAutoInsertFingerprint(fingerprint) {
      forgottenFingerprints.push(fingerprint);
      return true;
    },
    _insertTranscriptText: () => false,
    _setStatus() {},
    _setStatusPreservingRecording() {},
    _finishPendingRelisten: () => false,
    _payloadMessage: (_payload, fallback) => fallback,
  };

  finishAppletTextInsert.call(applet, { status: "done", transcript: "hello" });

  assert.deepEqual(forgottenFingerprints, ["fingerprint"]);
  assert.equal(applet.autoInsertPendingFingerprint, "");
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenPendingLanguage, "");
  assert.equal(applet.autoRelistenManualStopRequested, true);
});

const backgroundCleanupGroups = [
  "status",
  "history-refresh",
  "input-source-refresh",
  "model-menu-refresh",
  "voice-model",
  "text-model-refresh",
  "alarm-menu-refresh",
  "alarm-action",
  "alarm-check",
  "benchmark",
  "settings-transfer",
  "setup-diagnostics",
  "doctor",
  "maintenance",
  "settings-prompt",
  "ollama",
];

const textInsertCleanupGroups = ["keyboard", "clipboard", "x11"];

function makeRecordingApplet(options = {}) {
  const clock = { value: 1000 };
  const requests = [];
  const scheduledTimers = [];
  const appliedPayloads = [];
  const statusEvents = [];
  const preservedStatusEvents = [];
  const cleanupGroups = [];
  const lifecycleErrors = [];
  const cleanupReleases = [];
  const insertCalls = [];
  const pollCalls = [];
  const relistenCalls = [];
  const transcribeCalls = [];
  const failedCleanupGroups = new Set(options.failedCleanupGroups || []);

  let ollamaTimerClears = 0;
  let orphanedDialogRetries = 0;
  let clipboardApprovalClears = 0;
  let textCleanupCalls = 0;

  const applyPayloadSafely = options.realStopPayload === true
    ? loadAppletMethod("_applyPayloadSafely", "_applyPayload", clock)
    : function(payload) {
        appliedPayloads.push(payload);
        this.status = String(payload.status || this.status).toLowerCase();
      };
  const applyPayload = options.realStopPayload === true
    ? loadAppletMethod("_applyPayload", "_artifactEncryptionWarningKey", clock)
    : function() {};

  const state = {
    status: "idle",
    lastTranscript: "",
    lastMessage: "",
    lastNotificationKey: "",
    maxSeconds: 30,

    recordingArtifactsPresent: false,
    recordingStartedAtMs: 0,
    recordingMaxSeconds: 0,

    autoRelisten: true,
    autoTranscribeTimeout: false,
    notificationSessionActive: false,
    autoRelistenPending: false,
    autoRelistenPendingToken: "",
    autoRelistenPendingLanguage: "",
    autoRelistenManualStopRequested: false,
    autoTranscribeRecordingKey: "",
    autoInsertFingerprint: "",
    autoInsertFingerprints: [],

    isCommandRunning: false,
    _recordingCommandToken: null,
    stopPendingWhileCommandRunning: false,
    cancelPendingWhileCommandRunning: false,
    cancelIntentActive: false,
    cancelRecoveryAttempts: 0,
    recordingStartPendingAfterCleanup: false,
    recordingStartRetryTimer: 0,

    ollamaModelFlowToken: null,
    ollamaInstallWatchToken: null,
    ollamaModelInstallToken: null,
    ollamaModelInstallRunning: false,
    ollamaModelCleanupFailed: false,

    terminalWorkflowRunning: false,
    terminalWorkflowToken: null,

    _statusRefreshToken: 0,
    _statusCommandToken: null,
    _statusCommandRunning: false,
    historyRefreshToken: null,
    historyRefreshQueued: false,
    inputSourceMenuRefreshToken: null,
    modelMenuRefreshToken: null,
    voiceModelActionToken: null,
    voiceModelCleanupFailed: false,
    textModelMenuRefreshToken: null,
    alarmMenuRefreshToken: null,
    alarmMenuRefreshQueued: false,
    alarmActionToken: null,
    alarmCheckToken: null,
    benchmarkFlowToken: null,
    benchmarkCleanupFailed: false,
    settingsTransferToken: null,
    setupDiagnosticsToken: null,
    doctorCommandToken: null,
    _doctorCommandRunning: false,
    customLimitPromptToken: null,
    autoPastePromptToken: null,
    cleanupPreviewDialogToken: null,
    cleanupPreviewDialog: null,
    transcriptListPromptToken: null,
    transcriptListPromptDialog: null,
    _cleanupCommandToken: null,
    transcriptWindowToken: null,
    maintenanceCleanupFailed: false,
    textInsertCancellationFailed: false,

    targetWindowGeneration: 0,
    clipboardOverwriteDialog: null,
    textInsertToken: null,
    autoInsertPendingFingerprint: "",
    autoInsertConflictToken: null,

    _toggleRecording: loadAppletMethod(
      "_toggleRecording",
      "_restartApplet",
      clock
    ),
    _cancelRecording: loadAppletMethod(
      "_cancelRecording",
      "_invalidateBackgroundCallbacksForRecording",
      clock
    ),
    _failCancelRecovery: loadAppletMethod(
      "_failCancelRecovery",
      "_cancelRecording",
      clock
    ),
    _hasCancelableRecordingWork: loadAppletMethod(
      "_hasCancelableRecordingWork",
      "_updateRecordingArtifactState",
      clock
    ),
    _updateRecordingArtifactState: loadAppletMethod(
      "_updateRecordingArtifactState",
      "_failCancelRecovery",
      clock
    ),
    _invalidateBackgroundCallbacksForRecording: loadAppletMethod(
      "_invalidateBackgroundCallbacksForRecording",
      "_runDoctor",
      clock
    ),
    _cancelPendingRecordingStart: loadAppletMethod(
      "_cancelPendingRecordingStart",
      "_queueRecordingStartAfterCleanup",
      clock
    ),
    _queueRecordingStartAfterCleanup: loadAppletMethod(
      "_queueRecordingStartAfterCleanup",
      "_clearProcessCleanupRetryTimer",
      clock
    ),

    _hasActiveRecordingState() {
      return (
        this.status === "recording" ||
        this.status === "processing" ||
        this.recordingArtifactsPresent
      );
    },
    _hasLocalProcessingWorkflow() {
      return false;
    },
    _clearRecordingStartRetryTimer() {
      this.recordingStartRetryTimer = 0;
      return true;
    },
    _processCleanupStillPending() {
      return false;
    },
    _scheduleTrackedTimer(name, delay, callback, useSeconds, propertyName) {
      scheduledTimers.push({ name, delay, callback, useSeconds, propertyName });
      this[propertyName] = scheduledTimers.length;
      return scheduledTimers.length;
    },
    _cancelOllamaFlowForRecording() {
      return true;
    },
    _cancelTextInsertForSettingsChange() {
      textCleanupCalls += 1;
      this.targetWindowGeneration =
        Number(this.targetWindowGeneration || 0) + 1;
      this._clearClipboardOverwriteApproval();

      let cancellationSucceeded = true;
      for (const group of textInsertCleanupGroups) {
        if (this._terminateProcessesByGroup(group) === false) {
          cancellationSucceeded = false;
        }
      }

      this.textInsertCancellationFailed = !cancellationSucceeded;
      if (this.textInsertToken && this.autoRelistenPending) {
        this.autoRelistenPending = false;
        this.autoRelistenPendingToken = "";
        this.autoRelistenPendingLanguage = "";
        this.autoRelistenManualStopRequested = true;
      }
      return cancellationSucceeded;
    },
    _clearClipboardOverwriteApproval() {
      clipboardApprovalClears += 1;
      return true;
    },
    _ensureVoiceModelCompatibleWithCurrentLanguage() {
      return true;
    },
    _baseArgs(action) {
      return [action];
    },
    _cancelArgs() {
      return ["cancel"];
    },
    _sanitizeErrorMessage(value) {
      return String(value);
    },
    _normalizeRecordingLimit(value) {
      return Number(value) || 0;
    },
    _spawnJson(args, callback) {
      requests.push({ args, callback });
      return options.spawnReturnsNull ? null : { pid: requests.length };
    },
    _lifecycleAllowsWork() {
      return true;
    },
    _applyPayloadSafely: applyPayloadSafely,
    _applyPayload: applyPayload,
    _normalizePayloadStatus(value) {
      return String(value || "").trim().toLowerCase();
    },
    _payloadStringMarker(payload, keys, fallback) {
      for (const key of keys || []) {
        const value = payload && payload[key];
        if (typeof value === "string" && value.trim() !== "") {
          return value.trim();
        }
      }
      return fallback || "";
    },
    _applyPayloadLanguage() {},
    _updateRecordingTiming() {},
    _applyMicrophoneLevel() {},
    _isEmptyTranscriptText(value) {
      return String(value || "").trim() === "";
    },
    _payloadMessage() {
      return "";
    },
    _payloadErrorMessage(payload, fallback) {
      return String(payload && payload.error || fallback || "");
    },
    _maybeWarnRejectedArtifactPassphrase() {},
    _maybeWarnUnencryptedArtifactStorage() {},
    _finishAppletTextInsert(...args) {
      insertCalls.push(args);
    },
    _restartRelistenRecording(...args) {
      relistenCalls.push(args);
      return false;
    },
    _maybeAutoTranscribeRecorded: options.realTimedAutoTranscribe === true
      ? loadAppletMethod("_maybeAutoTranscribeRecorded", "_clearStatusTimer", clock)
      : function(...args) {
          transcribeCalls.push(args);
        },
    _setStatus(status, message, transcript) {
      statusEvents.push({ status, message, transcript });
      this.status = status;
      this.lastMessage = message;
      this.lastTranscript = transcript;
    },
    _setStatusPreservingRecording(status, message, transcript) {
      preservedStatusEvents.push({ status, message, transcript });
    },
    _scheduleStatusPoll(...args) {
      pollCalls.push(args);
      return true;
    },
    _updatePanel() {},

    _terminateProcessesByGroup(group) {
      cleanupGroups.push(group);
      return !failedCleanupGroups.has(group);
    },
    _releaseBusyStateAfterProcessCleanup(...args) {
      cleanupReleases.push(args);
      return true;
    },
    _recordLifecycleError(...args) {
      lifecycleErrors.push(args);
    },
    _dialogClose() {
      return true;
    },
    _retryOrphanedDialogs() {
      orphanedDialogRetries += 1;
      return true;
    },
    _clearOllamaInstallWatchTimer() {
      ollamaTimerClears += 1;
      return true;
    },
  };

  return {
    applet: state,
    requests,
    scheduledTimers,
    appliedPayloads,
    statusEvents,
    preservedStatusEvents,
    cleanupGroups,
    lifecycleErrors,
    cleanupReleases,
    insertCalls,
    pollCalls,
    relistenCalls,
    transcribeCalls,
    get ollamaTimerClears() {
      return ollamaTimerClears;
    },
    get orphanedDialogRetries() {
      return orphanedDialogRetries;
    },
    get clipboardApprovalClears() {
      return clipboardApprovalClears;
    },
    get textCleanupCalls() {
      return textCleanupCalls;
    },
  };
}

test("queued manual stop preserves latch and does not relisten", () => {
  const harness = makeRecordingApplet();
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet._toggleRecording(), true);
  assert.equal(applet.stopPendingWhileCommandRunning, true);
  assert.equal(applet.autoRelistenManualStopRequested, true);
  assert.equal(harness.requests.length, 1);

  harness.requests[0].callback({ status: "recording" });

  assert.equal(applet.autoRelistenManualStopRequested, true);
  assert.equal(harness.requests.length, 2);
  assert.equal(harness.requests[1].args[0], "stop");
  assert.equal(applet.isCommandRunning, true);

  harness.requests[1].callback({ status: "recorded" });

  assert.equal(harness.appliedPayloads.length, 1);
  assert.equal(harness.appliedPayloads[0].status, "recorded");
  assert.equal(applet.status, "recorded");
  assert.equal(applet.isCommandRunning, false);
  assert.equal(applet._recordingCommandToken, null);
  assert.equal(applet.stopPendingWhileCommandRunning, false);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenPendingLanguage, "");
  assert.deepEqual(
    harness.requests.map((request) => request.args[0]),
    ["start", "stop"]
  );

  applet.status = "idle";
  applet.recordingArtifactsPresent = false;
  applet.notificationSessionActive = false;
  applet.autoRelistenManualStopRequested = true;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet.autoRelistenManualStopRequested, false);
  assert.equal(harness.requests[2].args[0], "start");
});

test("queued manual stop handles recorded start payload", () => {
  const harness = makeRecordingApplet();
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet._toggleRecording(), true);

  harness.requests[0].callback({ status: "recorded" });

  assert.equal(applet.autoRelistenManualStopRequested, true);
  assert.equal(applet.stopPendingWhileCommandRunning, false);
  assert.equal(harness.requests.length, 2);
  assert.equal(harness.requests[1].args[0], "stop");
});

test("queued manual stop handles processing start payload", () => {
  const harness = makeRecordingApplet();
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet._toggleRecording(), true);

  harness.requests[0].callback({ status: "processing" });

  assert.equal(applet.stopPendingWhileCommandRunning, false);
  assert.equal(harness.requests.length, 2);
  assert.equal(harness.requests[1].args[0], "stop");
});

test("background cleanup failure still cleans text groups but does not spawn", () => {
  const harness = makeRecordingApplet({
    failedCleanupGroups: ["status"],
  });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), false);
  assert.equal(harness.requests.length, 0);
  assert.equal(applet.isCommandRunning, false);
  assert.equal(harness.textCleanupCalls, 1);
  assert.equal(
    harness.preservedStatusEvents.some((event) => event.status === "error"),
    true
  );

  for (const group of textInsertCleanupGroups) {
    assert.equal(
      harness.cleanupGroups.filter((value) => value === group).length,
      1
    );
  }
});

test("recording start waits for transient process cleanup and retries once", () => {
  const harness = makeRecordingApplet({ failedCleanupGroups: ["doctor"] });
  const { applet } = harness;
  let processCleanupPending = true;
  applet._processCleanupStillPending = () => processCleanupPending;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(harness.requests.length, 0);
  assert.equal(applet.status, "ready");
  assert.equal(applet.recordingStartPendingAfterCleanup, true);
  assert.equal(harness.scheduledTimers.length, 1);
  assert.equal(harness.scheduledTimers[0].name, "recording-start-retry");

  processCleanupPending = false;
  applet._terminateProcessesByGroup = () => true;
  harness.scheduledTimers[0].callback();

  assert.equal(applet.recordingStartPendingAfterCleanup, false);
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "start");
});

test("pending recording start is cancelled by stop and cannot resurrect", () => {
  const harness = makeRecordingApplet({ failedCleanupGroups: ["doctor"] });
  const { applet } = harness;
  applet._processCleanupStillPending = () => true;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet._toggleRecording("stop"), true);
  assert.equal(applet.status, "ready");
  assert.equal(applet.recordingStartPendingAfterCleanup, false);
  assert.equal(harness.requests.length, 0);

  harness.scheduledTimers[0].callback();
  assert.equal(harness.requests.length, 0);
});

test("cancel hotkey clears pending recording start without active recording", () => {
  const harness = makeRecordingApplet({ failedCleanupGroups: ["doctor"] });
  const { applet } = harness;
  applet._processCleanupStillPending = () => true;

  assert.equal(applet._toggleRecording("start"), true);
  applet._cancelRecording();

  assert.equal(applet.status, "ready");
  assert.equal(applet.recordingStartPendingAfterCleanup, false);
  assert.equal(harness.requests.length, 0);
  harness.scheduledTimers[0].callback();
  assert.equal(harness.requests.length, 0);
});

test("text cleanup failure does not spawn an idle recording", () => {
  const harness = makeRecordingApplet({
    failedCleanupGroups: ["keyboard"],
  });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), false);
  assert.equal(harness.requests.length, 0);
  assert.equal(applet.isCommandRunning, false);
  assert.equal(applet.textInsertCancellationFailed, true);
  assert.equal(harness.textCleanupCalls, 1);

  for (const group of textInsertCleanupGroups) {
    assert.equal(
      harness.cleanupGroups.filter((value) => value === group).length,
      1
    );
  }
});

const applyPayloadSourceCases = [
  { name: "stop blocks", sourceAction: "stop", shouldStop: false },
  { name: "start allows", sourceAction: "start", shouldStop: true },
  { name: "cancel blocks", sourceAction: "cancel", shouldStop: false },
  { name: "unknown blocks", sourceAction: "unknown", shouldStop: false },
  { name: "poll without context allows", shouldStop: true },
];

for (const testCase of applyPayloadSourceCases) {
  test(`real _applyPayload sourceAction: ${testCase.name}`, () => {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;
    const payload = { status: "recording" };

    applet.autoRelistenManualStopRequested = true;

    if (Object.prototype.hasOwnProperty.call(testCase, "sourceAction")) {
      applet._applyPayloadSafely(
        payload,
        undefined,
        true,
        testCase.sourceAction
      );
    } else {
      applet._applyPayloadSafely(payload, undefined, true);
    }

    assert.equal(
      harness.requests.length,
      testCase.shouldStop ? 1 : 0
    );
    if (testCase.shouldStop) {
      assert.equal(harness.requests[0].args[0], "stop");
    }
  });
}

test("stop error preserves recoverable recording state", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "processing";
  applet.recordingArtifactsPresent = false;
  applet._applyPayloadSafely(
    {
      status: "error",
      error: "recording process could not be stopped safely",
    },
    undefined,
    true,
    "stop"
  );

  assert.equal(applet.status, "processing");
  assert.equal(applet.recordingArtifactsPresent, true);
  assert.equal(applet._hasActiveRecordingState(), true);
  assert.equal(harness.preservedStatusEvents.at(-1).status, "error");
  assert.equal(harness.statusEvents.length, 0);
  assert.equal(harness.pollCalls.length, 1);
});

test("real stop payload does not enqueue another stop request", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet._toggleRecording(), true);
  harness.requests[0].callback({ status: "recording" });

  assert.equal(harness.requests.length, 2);
  assert.equal(harness.requests[1].args[0], "stop");

  harness.requests[1].callback({
    status: "recorded",
    transcript: "queued result",
  });

  assert.equal(harness.requests.length, 2);
  assert.equal(
    harness.requests.filter((request) => request.args[0] === "stop").length,
    1
  );
  assert.equal(applet.isCommandRunning, false);
});

test("recorded payload with backend markers does not stop again or cancel", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  assert.equal(applet._toggleRecording(), true);
  harness.requests[0].callback({ status: "recording" });

  assert.equal(harness.requests.length, 2);
  assert.equal(harness.requests[1].args[0], "stop");
  const baselineStatusEvents = harness.statusEvents.length;

  harness.requests[1].callback({
    status: "recorded",
    message: "Recorded payload from backend",
    pid_present: true,
    process_identity_present: true,
    transcript: "recorded transcript",
  });

  assert.equal(
    harness.requests.filter((request) => request.args[0] === "stop").length,
    1
  );
  assert.equal(
    harness.requests.filter((request) => request.args[0] === "cancel").length,
    0
  );
  assert.equal(applet.isCommandRunning, false);
  assert.equal(harness.statusEvents.length, baselineStatusEvents + 1);
  assert.equal(harness.statusEvents.at(-1).status, "recorded");
  assert.equal(harness.statusEvents.at(-1).transcript, "recorded transcript");
});

test("real cancel queues behind start and spawns once after callback", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  applet._cancelRecording();

  assert.equal(harness.requests.length, 1);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.cancelPendingWhileCommandRunning, true);
  assert.equal(applet.stopPendingWhileCommandRunning, false);

  harness.requests[0].callback({ status: "recording" });

  assert.deepEqual(
    harness.requests.map((request) => request.args[0]),
    ["start", "cancel"]
  );
  assert.equal(applet.cancelRecoveryAttempts, 1);

  const activeCancelToken = applet._recordingCommandToken;
  const statusBeforeStaleCallback = applet.status;
  const messageBeforeStaleCallback = applet.lastMessage;
  const transcriptBeforeStaleCallback = applet.lastTranscript;
  const pollsBeforeStaleCallback = harness.pollCalls.length;
  const insertsBeforeStaleCallback = harness.insertCalls.length;
  const relistensBeforeStaleCallback = harness.relistenCalls.length;
  harness.requests[0].callback({ status: "recording" });

  assert.equal(harness.requests.length, 2);
  assert.equal(applet._recordingCommandToken, activeCancelToken);
  assert.equal(applet.isCommandRunning, true);
  assert.equal(applet.status, statusBeforeStaleCallback);
  assert.equal(applet.lastMessage, messageBeforeStaleCallback);
  assert.equal(applet.lastTranscript, transcriptBeforeStaleCallback);
  assert.equal(harness.pollCalls.length, pollsBeforeStaleCallback);
  assert.equal(harness.insertCalls.length, insertsBeforeStaleCallback);
  assert.equal(harness.relistenCalls.length, relistensBeforeStaleCallback);
});

test("manual cancel does not claim auto relisten when only setting is enabled", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  applet._cancelRecording();

  assert.equal(harness.statusEvents.at(-1).message, "Cancelling...");
});

for (const terminalStatus of ["done", "idle"]) {
  test(`queued cancel retries terminal ${terminalStatus} without cleanup evidence`, () => {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;
    let warningCalls = 0;
    applet._maybeWarnUnencryptedArtifactStorage = () => {
      warningCalls += 1;
    };

    assert.equal(applet._toggleRecording("start"), true);
    applet.lastTranscript = "previous transcript";
    applet._cancelRecording();
    harness.requests[0].callback({
      status: terminalStatus,
      transcript: "must not escape",
    });

    assert.deepEqual(
      harness.requests.map((request) => request.args[0]),
      ["start", "cancel"]
    );
    assert.equal(harness.insertCalls.length, 0);
    assert.equal(harness.transcribeCalls.length, 0);
    assert.equal(harness.relistenCalls.length, 0);
    assert.equal(applet.cancelIntentActive, true);
    assert.equal(applet.cancelPendingWhileCommandRunning, false);
    assert.equal(applet.cancelRecoveryAttempts, 1);
    assert.equal(applet.status, "processing");
    assert.equal(applet.lastTranscript, "previous transcript");
    assert.equal(warningCalls, 0);

    harness.requests[1].callback({
      status: "idle",
      audio_deleted: true,
      log_deleted: true,
      transcript_deleted: true,
      inflight_artifacts_deleted: true,
      cleanup_backups_deleted: true,
    });

    assert.equal(harness.requests.length, 2);
    assert.equal(harness.pollCalls.length, 0);
    assert.equal(harness.insertCalls.length, 0);
    assert.equal(harness.transcribeCalls.length, 0);
    assert.equal(harness.relistenCalls.length, 0);
    assert.equal(applet.cancelIntentActive, false);
    assert.equal(applet.cancelPendingWhileCommandRunning, false);
    assert.equal(applet.cancelRecoveryAttempts, 0);
    assert.equal(applet.status, "ready");
    assert.equal(applet.lastTranscript, "previous transcript");
    assert.equal(warningCalls, 0);
  });
}

for (const stopStatus of ["done", "idle", "recorded"]) {
  test(`queued cancel behind stop routes ${stopStatus} through one cancel`, () => {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;
    applet.status = "recording";
    applet.recordingArtifactsPresent = true;
    let warningCalls = 0;
    applet._maybeWarnUnencryptedArtifactStorage = () => {
      warningCalls += 1;
    };

    assert.equal(applet._toggleRecording("stop"), true);
    applet.lastTranscript = "previous transcript";
    applet._cancelRecording();
    harness.requests[0].callback({
      status: stopStatus,
      transcript: "must not escape",
    });

    assert.deepEqual(
      harness.requests.map((request) => request.args[0]),
      ["stop", "cancel"]
    );
    assert.equal(harness.pollCalls.length, 0);
    assert.equal(harness.insertCalls.length, 0);
    assert.equal(harness.transcribeCalls.length, 0);
    assert.equal(harness.relistenCalls.length, 0);
    assert.equal(applet.cancelIntentActive, true);
    assert.equal(applet.cancelPendingWhileCommandRunning, false);
    assert.equal(applet.cancelRecoveryAttempts, 1);
    assert.equal(applet.status, "processing");
    assert.equal(applet.lastTranscript, "previous transcript");
    assert.equal(warningCalls, 0);

    harness.requests[1].callback({
      status: "idle",
      audio_deleted: true,
      log_deleted: true,
      transcript_deleted: true,
      inflight_artifacts_deleted: true,
      cleanup_backups_deleted: true,
    });

    assert.equal(harness.requests.length, 2);
    assert.equal(harness.pollCalls.length, 0);
    assert.equal(applet.cancelIntentActive, false);
    assert.equal(applet.cancelPendingWhileCommandRunning, false);
    assert.equal(applet.cancelRecoveryAttempts, 0);
    assert.equal(applet.status, "ready");
    assert.equal(applet.lastTranscript, "previous transcript");
    assert.equal(warningCalls, 0);
  });
}

test("real cancel gate rejects work not owned by recording", () => {
  const idleHarness = makeRecordingApplet({ realStopPayload: true });
  idleHarness.applet.status = "idle";
  idleHarness.applet._cancelRecording();
  assert.equal(idleHarness.requests.length, 0);
  assert.equal(idleHarness.applet.cancelIntentActive, false);

  const processingHarness = makeRecordingApplet({ realStopPayload: true });
  processingHarness.applet.status = "processing";
  processingHarness.applet.recordingArtifactsPresent = true;
  processingHarness.applet._hasLocalProcessingWorkflow = () => true;
  processingHarness.applet._cancelRecording();
  assert.equal(processingHarness.requests.length, 0);
  assert.equal(processingHarness.applet.cancelIntentActive, false);

  const recordingProcessingHarness = makeRecordingApplet({
    realStopPayload: true,
  });
  recordingProcessingHarness.applet.status = "processing";
  recordingProcessingHarness.applet.recordingArtifactsPresent = true;
  recordingProcessingHarness.applet._cancelRecording();
  assert.equal(recordingProcessingHarness.requests.length, 1);
  assert.equal(recordingProcessingHarness.requests[0].args[0], "cancel");

  const insertHarness = makeRecordingApplet({ realStopPayload: true });
  insertHarness.applet.status = "recording";
  insertHarness.applet.textInsertToken = {};
  insertHarness.applet._cancelRecording();
  assert.equal(insertHarness.requests.length, 0);
  assert.equal(insertHarness.applet.cancelIntentActive, false);
});

test("real direct cancel done payload never inserts or relistens", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet.notificationSessionActive = true;
  applet.autoRelisten = true;
  applet.lastTranscript = "previous transcript";
  let warningCalls = 0;
  applet._maybeWarnUnencryptedArtifactStorage = () => {
    warningCalls += 1;
  };
  applet._cancelRecording();

  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "cancel");

  applet.autoRelistenPending = true;
  applet.autoRelistenPendingToken = "stale-token";
  applet.autoRelistenPendingLanguage = "de";
  harness.requests[0].callback({
    status: "done",
    transcript: "cancelled transcript",
      audio_deleted: true,
      log_deleted: true,
      transcript_deleted: true,
      inflight_artifacts_deleted: true,
      cleanup_backups_deleted: true,
    });

  assert.equal(harness.insertCalls.length, 0);
  assert.equal(harness.transcribeCalls.length, 0);
  assert.equal(harness.relistenCalls.length, 0);
  assert.equal(applet.lastTranscript, "previous transcript");
  assert.equal(warningCalls, 0);
  assert.equal(harness.requests.length, 1);
  assert.equal(applet.cancelIntentActive, false);
  assert.equal(applet.cancelPendingWhileCommandRunning, false);
  assert.equal(applet.cancelRecoveryAttempts, 0);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenPendingLanguage, "");
  assert.equal(applet.autoRelistenManualStopRequested, false);
  assert.equal(applet.status, "ready");
});

test("real direct cancel idle payload terminates without polling", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  harness.requests[0].callback({
    status: "idle",
    audio_deleted: true,
    log_deleted: true,
    transcript_deleted: true,
    inflight_artifacts_deleted: true,
    cleanup_backups_deleted: true,
  });

  assert.equal(harness.pollCalls.length, 0);
  assert.equal(applet.cancelIntentActive, false);
  assert.equal(applet.cancelRecoveryAttempts, 0);
  assert.equal(applet.autoRelistenManualStopRequested, false);
  assert.equal(applet.status, "ready");
});

for (const transientStatus of ["recording", "recorded"]) {
  test(`transient direct cancel ${transientStatus} response only polls`, () => {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;

    applet.status = "recording";
    applet.recordingArtifactsPresent = true;
    applet.notificationSessionActive = true;
    applet.autoRelisten = true;
    applet._cancelRecording();

    harness.requests[0].callback({ status: transientStatus });

    assert.equal(harness.requests.length, 1);
    assert.equal(harness.insertCalls.length, 0);
    assert.equal(harness.transcribeCalls.length, 0);
    assert.equal(harness.relistenCalls.length, 0);
    assert.equal(harness.pollCalls.length, 1);
    assert.equal(applet.cancelIntentActive, true);
    assert.equal(applet.cancelRecoveryAttempts, 1);
  });
}

test("cancel processing status poll advances bounded recovery", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "processing";
  applet.recordingArtifactsPresent = true;
  applet.cancelIntentActive = true;
  applet.cancelRecoveryAttempts = 1;
  applet._applyPayloadSafely(
    { status: "processing" },
    applet._statusRefreshToken,
    true
  );

  assert.equal(applet.cancelRecoveryAttempts, 2);
  assert.equal(harness.pollCalls.length, 1);
  assert.equal(harness.requests.length, 0);
  assert.equal(harness.insertCalls.length, 0);
  assert.equal(harness.transcribeCalls.length, 0);
  assert.equal(harness.relistenCalls.length, 0);
});

test("cancel recorded status poll starts exactly one bounded retry", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet.cancelIntentActive = true;
  applet.cancelRecoveryAttempts = 1;
  applet._applyPayloadSafely(
    { status: "recorded" },
    applet._statusRefreshToken,
    true
  );

  assert.equal(applet.cancelRecoveryAttempts, 2);
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "cancel");
  assert.equal(harness.pollCalls.length, 0);
  assert.equal(harness.insertCalls.length, 0);
  assert.equal(harness.transcribeCalls.length, 0);
  assert.equal(harness.relistenCalls.length, 0);
});

test("terminal cancel status poll starts one bounded cancel retry", () => {
  for (const initialAttempts of [0, 2]) {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;
    applet.status = "processing";
    applet.cancelIntentActive = true;
    applet.recordingArtifactsPresent = true;
    applet.cancelRecoveryAttempts = initialAttempts;
    applet._statusRefreshToken = 7 + initialAttempts;

    applet._applyPayloadSafely(
      { status: "idle" },
      applet._statusRefreshToken,
      true
    );

    assert.equal(harness.requests.length, 1);
    assert.equal(harness.requests[0].args[0], "cancel");
    assert.equal(harness.pollCalls.length, 0);
    assert.equal(applet.cancelIntentActive, true);
    assert.equal(applet.cancelRecoveryAttempts, initialAttempts + 1);
    assert.equal(applet.status, "processing");
  }
});

test("terminal cancel status poll at retry limit fails without work", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;
  applet.status = "processing";
  applet.cancelIntentActive = true;
  applet.recordingArtifactsPresent = true;
  applet.cancelRecoveryAttempts = 3;
  applet._statusRefreshToken = 9;

  applet._applyPayloadSafely({ status: "idle" }, 9, true);

  assert.equal(harness.requests.length, 0);
  assert.equal(harness.pollCalls.length, 0);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.status, "error");
});

test("cancel setup response fails closed without polling", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  harness.requests[0].callback({ status: "setup" });

  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.recordingArtifactsPresent, true);
  assert.equal(applet.status, "error");
  assert.equal(harness.pollCalls.length, 0);
});

test("terminal cleanup evidence requires every field with strict boolean values", () => {
  const cleanupFields = [
    "audio_deleted",
    "log_deleted",
    "transcript_deleted",
    "inflight_artifacts_deleted",
    "cleanup_backups_deleted",
  ];
  const invalidMutations = [
    ["missing", (payload, field) => {
      delete payload[field];
    }],
    ["null", (payload, field) => {
      payload[field] = null;
    }],
    ["number", (payload, field) => {
      payload[field] = 1;
    }],
    ["string", (payload, field) => {
      payload[field] = "true";
    }],
    ["object", (payload, field) => {
      payload[field] = {};
    }],
  ];

  for (const field of cleanupFields) {
    for (const [caseName, mutate] of invalidMutations) {
      const harness = makeRecordingApplet({ realStopPayload: true });
      const { applet } = harness;
      let warningCalls = 0;
      applet._maybeWarnUnencryptedArtifactStorage = () => {
        warningCalls += 1;
      };

      assert.equal(applet._toggleRecording("start"), true);
      applet.lastTranscript = "previous transcript";
      applet._cancelRecording();
      const payload = {
        status: "done",
        transcript: "must not escape",
        audio_deleted: true,
        log_deleted: true,
        transcript_deleted: true,
        inflight_artifacts_deleted: true,
        cleanup_backups_deleted: true,
      };
      mutate(payload, field);

      harness.requests[0].callback(payload);

      assert.deepEqual(
        harness.requests.map((request) => request.args[0]),
        ["start", "cancel"],
        `${field}/${caseName}: foreign terminal callback must spawn one cancel`
      );
      assert.equal(applet.lastTranscript, "previous transcript");
      assert.equal(warningCalls, 0);

      harness.requests[1].callback(payload);

      assert.equal(harness.requests.length, 2);
      assert.equal(harness.pollCalls.length, 0);
      assert.equal(applet.cancelIntentActive, true);
      assert.equal(applet.recordingArtifactsPresent, true);
      assert.equal(applet.cancelRecoveryAttempts, 3);
      assert.equal(applet.status, "error");
      assert.equal(applet.lastTranscript, "previous transcript");
      assert.equal(warningCalls, 0);
    }
  }
});

test("complete negative cleanup evidence fails closed", () => {
  const cleanupFields = [
    "audio_deleted",
    "log_deleted",
    "transcript_deleted",
    "inflight_artifacts_deleted",
    "cleanup_backups_deleted",
  ];

  for (const field of cleanupFields) {
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;
    applet.status = "recording";
    applet.recordingArtifactsPresent = true;
    applet._cancelRecording();
    const payload = {
      status: "idle",
      audio_deleted: true,
      log_deleted: true,
      transcript_deleted: true,
      inflight_artifacts_deleted: true,
      cleanup_backups_deleted: true,
    };
    payload[field] = false;

    harness.requests[0].callback(payload);

    assert.equal(harness.requests.length, 1, field);
    assert.equal(harness.pollCalls.length, 0, field);
    assert.equal(applet.cancelIntentActive, true, field);
    assert.equal(applet.recordingArtifactsPresent, true, field);
    assert.equal(applet.cancelRecoveryAttempts, 3, field);
    assert.equal(applet.status, "error", field);
  }
});

test("terminal cancel preserves explicit cleanup evidence", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  harness.requests[0].callback({
    status: "idle",
    audio_path_present: true,
    audio_deleted: false,
  });

  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.recordingArtifactsPresent, true);
  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.status, "error");
});

test("terminal cancel without cleanup confirmation stays retryable", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  harness.requests[0].callback({ status: "idle" });

  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.recordingArtifactsPresent, true);
  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.status, "error");
});

test("terminal cancel rejects incomplete positive cleanup confirmation", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  harness.requests[0].callback({
    status: "idle",
    audio_deleted: true,
  });

  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.recordingArtifactsPresent, true);
  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.status, "error");
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.pollCalls.length, 0);
});

test("successful cancel ignores pre-discard presence after confirmed cleanup", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  harness.requests[0].callback({
    status: "idle",
    discarded_audio_path_present: true,
    audio_deleted: true,
    log_deleted: true,
    transcript_deleted: true,
    inflight_artifacts_deleted: true,
    cleanup_backups_deleted: true,
  });

  assert.equal(applet.cancelIntentActive, false);
  assert.equal(applet.recordingArtifactsPresent, false);
  assert.equal(applet.cancelRecoveryAttempts, 0);
  assert.equal(applet.status, "ready");
});

test("duplicate cancel while cancel command runs does not reset or respawn", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet.notificationSessionActive = true;
  applet._cancelRecording();
  const activeToken = applet._recordingCommandToken;

  applet._cancelRecording();

  assert.equal(harness.requests.length, 1);
  assert.equal(applet._recordingCommandToken, activeToken);
  assert.equal(applet.isCommandRunning, true);
  assert.equal(applet.cancelPendingWhileCommandRunning, false);
  assert.equal(applet.cancelRecoveryAttempts, 1);
});

test("stale direct cancel callback cannot mutate newer command state", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelRecording();
  const staleCallback = harness.requests[0].callback;
  const newerToken = { action: "cancel" };
  applet._recordingCommandToken = newerToken;
  applet.isCommandRunning = true;
  applet.status = "processing";
  applet.lastMessage = "newer cancel";
  applet.lastTranscript = "preserved";
  const pollsBefore = harness.pollCalls.length;

  staleCallback({
    status: "idle",
    audio_deleted: true,
    log_deleted: true,
    transcript_deleted: true,
  });

  assert.equal(applet._recordingCommandToken, newerToken);
  assert.equal(applet.isCommandRunning, true);
  assert.equal(applet.status, "processing");
  assert.equal(applet.lastMessage, "newer cancel");
  assert.equal(applet.lastTranscript, "preserved");
  assert.equal(harness.pollCalls.length, pollsBefore);
  assert.equal(harness.insertCalls.length, 0);
  assert.equal(harness.relistenCalls.length, 0);
});

test("stale start callback cannot mutate newer command state", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  assert.equal(applet._toggleRecording("start"), true);
  const staleCallback = harness.requests[0].callback;
  const newerToken = { action: "stop" };
  applet._recordingCommandToken = newerToken;
  applet.isCommandRunning = true;
  applet.status = "processing";
  applet.lastMessage = "newer stop";
  applet.lastTranscript = "preserved";
  const pollsBefore = harness.pollCalls.length;

  staleCallback({ status: "recording", transcript: "stale transcript" });

  assert.equal(applet._recordingCommandToken, newerToken);
  assert.equal(applet.isCommandRunning, true);
  assert.equal(applet.status, "processing");
  assert.equal(applet.lastMessage, "newer stop");
  assert.equal(applet.lastTranscript, "preserved");
  assert.equal(harness.pollCalls.length, pollsBefore);
  assert.equal(harness.insertCalls.length, 0);
  assert.equal(harness.relistenCalls.length, 0);
});

test("exhausted recorded status poll cannot spawn or repoll", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "processing";
  applet.recordingArtifactsPresent = true;
  applet.cancelIntentActive = true;
  applet.cancelRecoveryAttempts = 3;
  applet._applyPayloadSafely(
    { status: "recorded" },
    applet._statusRefreshToken,
    true
  );

  assert.equal(harness.requests.length, 0);
  assert.equal(harness.pollCalls.length, 0);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.recordingArtifactsPresent, true);
  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.status, "error");
});

test("auto relisten text insert cancel stays local and clears pending work", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "done";
  applet.autoRelistenPending = true;
  applet.autoRelistenPendingToken = "pending";
  applet.autoRelistenPendingLanguage = "de";
  applet.textInsertToken = {};
  applet._cancelRecording();

  assert.equal(harness.requests.length, 0);
  assert.equal(harness.textCleanupCalls, 1);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenPendingLanguage, "");
  assert.equal(applet.autoRelistenManualStopRequested, false);
  assert.equal(applet.cancelIntentActive, false);
  assert.equal(applet.status, "ready");
});

test("cancel preparation error preserves intent and schedules recovery", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._cancelArgs = () => {
    throw new Error("prepare failed");
  };

  applet._cancelRecording();

  assert.equal(harness.requests.length, 0);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.cancelRecoveryAttempts, 1);
  assert.equal(harness.pollCalls.length, 1);
  assert.equal(applet.recordingArtifactsPresent, true);
});

test("cancel spawn failure preserves intent and schedules recovery", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;
  let spawnCalls = 0;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._spawnJson = () => {
    spawnCalls += 1;
    return false;
  };

  applet._cancelRecording();

  assert.equal(spawnCalls, 1);
  assert.equal(applet._recordingCommandToken, null);
  assert.equal(applet.isCommandRunning, false);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.cancelRecoveryAttempts, 1);
  assert.equal(harness.pollCalls.length, 1);
});

test("cancel recovery stops spawning after three attempts", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;
  let spawnCalls = 0;

  applet.status = "recording";
  applet.recordingArtifactsPresent = true;
  applet._spawnJson = () => {
    spawnCalls += 1;
    return false;
  };

  applet._cancelRecording();
  applet._cancelRecording("recording");
  applet._cancelRecording("recording");
  applet._cancelRecording("recording");

  assert.equal(spawnCalls, 3);
  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(harness.pollCalls.length, 2);
  assert.equal(applet.status, "error");
  assert.equal(applet.lastMessage, "Could not cancel recording");
});

test("cancel status error exhausts recovery without another poll", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.status = "processing";
  applet.recordingArtifactsPresent = true;
  applet.cancelIntentActive = true;
  applet.cancelRecoveryAttempts = 2;
  applet._applyPayloadSafely(
    { status: "error", error: "status unavailable" },
    applet._statusRefreshToken,
    true
  );

  assert.equal(applet.cancelRecoveryAttempts, 3);
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(harness.pollCalls.length, 0);
  assert.equal(applet.status, "error");
  assert.equal(applet.lastMessage, "status unavailable");
});

test("queued cancel shows generic status without auto relisten", () => {
  const harness = makeRecordingApplet({ realStopPayload: true });
  const { applet } = harness;

  applet.autoRelisten = false;
  applet.notificationSessionActive = true;
  applet.isCommandRunning = true;
  applet._recordingCommandToken = { action: "start" };
  applet._cancelRecording();

  assert.equal(harness.statusEvents.at(-1).status, "processing");
  assert.equal(harness.statusEvents.at(-1).message, "Cancelling...");
  assert.equal(applet.cancelIntentActive, true);
  assert.equal(applet.cancelPendingWhileCommandRunning, true);
});

test("recording start cleans each process group exactly once", () => {
  const harness = makeRecordingApplet();

  assert.equal(harness.applet._toggleRecording("start"), true);
  assert.deepEqual(harness.cleanupGroups, [
    ...backgroundCleanupGroups,
    ...textInsertCleanupGroups,
  ]);
  assert.equal(harness.cleanupGroups.length, 19);

  for (const group of [
    ...backgroundCleanupGroups,
    ...textInsertCleanupGroups,
  ]) {
    assert.equal(
      harness.cleanupGroups.filter((value) => value === group).length,
      1
    );
  }
});

test("timed auto-transcribe starts one stop per recorded payload", () => {
  const harness = makeRecordingApplet({
    realTimedAutoTranscribe: true,
  });
  const applet = harness.applet;
  applet.autoRelisten = false;
  applet.autoTranscribeTimeout = true;
  applet.notificationSessionActive = true;

  const payload = { status: "recorded", audio_path: "/tmp/recording.flac" };
  applet._maybeAutoTranscribeRecorded(payload, "recorded");
  applet._maybeAutoTranscribeRecorded(payload, "recorded");

  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "stop");
  assert.equal(applet.isCommandRunning, true);
  assert.equal(harness.statusEvents.at(-1).status, "processing");

  harness.requests[0].callback({ status: "done", transcript: "timed result" });
  assert.equal(applet.isCommandRunning, false);
  assert.equal(applet._recordingCommandToken, null);
  assert.equal(harness.appliedPayloads.at(-1).status, "done");
});

test("manual toggle consumes timed stop payload without relistening", () => {
  const harness = makeRecordingApplet({
    realTimedAutoTranscribe: true,
  });
  const applet = harness.applet;
  applet.autoRelisten = true;
  applet.autoTranscribeTimeout = true;
  applet.notificationSessionActive = true;

  applet._maybeAutoTranscribeRecorded(
    { status: "recorded", audio_path: "/tmp/recording.flac" },
    "recorded"
  );
  assert.equal(harness.requests.length, 1);
  assert.equal(applet.autoRelistenPending, true);

  assert.equal(applet._toggleRecording(), true);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenManualStopRequested, true);

  harness.requests[0].callback({
    status: "done",
    transcript: "manual timed result",
  });

  assert.equal(harness.appliedPayloads.at(-1).transcript, "manual timed result");
  assert.equal(harness.relistenCalls.length, 0);
  assert.equal(applet.isCommandRunning, false);
});

test("timed auto-transcribe spawn failure releases recording state", () => {
  const harness = makeRecordingApplet({
    realTimedAutoTranscribe: true,
    spawnReturnsNull: true,
  });
  const applet = harness.applet;
  applet.autoRelisten = false;
  applet.autoTranscribeTimeout = true;
  applet.notificationSessionActive = true;

  applet._maybeAutoTranscribeRecorded(
    { status: "recorded", audio_path: "/tmp/recording.flac" },
    "recorded"
  );

  assert.equal(applet.isCommandRunning, false);
  assert.equal(applet._recordingCommandToken, null);
  assert.equal(applet.autoTranscribeRecordingKey, "");
  assert.equal(harness.preservedStatusEvents.at(-1).status, "error");
});

test("recording status uses distinct panel classes and dynamic icon names", () => {
  const styleForStatus = loadAppletMethod(
    "_panelStyleClassForStatus",
    "_statusIconNameForStatus",
    { value: 1000 }
  );
  const iconForStatus = loadAppletMethod(
    "_statusIconNameForStatus",
    "_resetStatusIconCache",
    { value: 1000 }
  );

  assert.equal(styleForStatus("recording"), "speed-of-cinnamon-recording");
  assert.equal(styleForStatus("processing"), "speed-of-cinnamon-processing");
  assert.equal(styleForStatus("recorded"), "speed-of-cinnamon-recorded");
  assert.equal(styleForStatus("done"), "speed-of-cinnamon-recorded");
  assert.equal(styleForStatus("error"), "speed-of-cinnamon-error");
  assert.equal(iconForStatus("recording"), "media-record-symbolic");
  assert.equal(iconForStatus("processing"), "view-refresh-symbolic");
  assert.equal(iconForStatus("recorded"), "audio-input-microphone-symbolic");
  assert.equal(iconForStatus("done"), "audio-input-microphone-symbolic");
});
