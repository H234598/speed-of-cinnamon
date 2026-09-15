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
const stateSource = fs.readFileSync(
  path.join(root, "src/speed_of_cinnamon/state.py"),
  "utf8"
);
const cliSource = fs.readFileSync(
  path.join(root, "src/speed_of_cinnamon/cli.py"),
  "utf8"
);
const backendStatusesMatch = appletSource.match(
  /const BACKEND_PAYLOAD_STATUSES = (\[[^;]+\]);/
);
const stateStatusesMatch = stateSource.match(
  /VALID_STATE_STATUSES = frozenset\(\{([^}]+)\}\)/s
);
const cliStatusExtrasMatch = cliSource.match(
  /_PUBLIC_PAYLOAD_STATUSES = VALID_STATE_STATUSES \| frozenset\(\s*\{([^}]+)\}/s
);
assert.notEqual(backendStatusesMatch, null);
assert.notEqual(stateStatusesMatch, null);
assert.notEqual(cliStatusExtrasMatch, null);
const backendPayloadStatuses = JSON.parse(backendStatusesMatch[1]);
const pythonPublicPayloadStatuses = Array.from(new Set([
  ...Array.from(stateStatusesMatch[1].matchAll(/"([^"]+)"/g), (match) => match[1]),
  ...Array.from(cliStatusExtrasMatch[1].matchAll(/"([^"]+)"/g), (match) => match[1]),
]));

function makeDoctorPayload(ok = true) {
  return {
    schema_version: 1,
    status: "done",
    ok,
    checks: [
      { name: "python3", ok, detail: "python3 available" },
    ],
    desktop: { cinnamon: true },
    configured: {
      recorder: { ok, detail: "recorder available" },
      transcriber: { ok, detail: "transcriber available" },
      output: { ok, paste_ok: true, detail: "output available" },
      postprocessor: { ok, detail: "text polishing disabled" },
    },
    warnings: [],
    applet: true,
  };
}

function loadAppletMethod(name, nextName, clock, extraContext = {}) {
  const start = appletSource.indexOf(`  ${name}: function(`);
  const end = appletSource.indexOf(`\n  ${nextName}:`, start);
  assert.notEqual(start, -1);
  assert.notEqual(end, -1);

  const property = appletSource
    .slice(start, end)
    .trim()
    .replace(/,\s*$/, "");

  const loadedMethod = vm.runInNewContext(`({${property}}).${name}`, {
    CLIPBOARD_READY_RETRY_MS: 40,
    CLIPBOARD_READY_TIMEOUT_MS: 100,
    MAX_CANCEL_RECOVERY_ATTEMPTS: 3,
    MAX_KEYBOARD_COMMAND_TIMEOUT_MS: 300000,
    MAX_XDOTOOL_TARGET_OUTPUT_BYTES: 4096,
    MAX_SPAWN_JSON_BYTES: 262144,
    AUTO_RELISTEN_RETRY_DELAY_MS: 250,
    LANGUAGE_CODES: ["en", "de"],
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
  return function(...args) {
    if (this && typeof this._processCleanupStatus !== "function") {
      this._processCleanupStatus = (result) => {
        if (result === true || result === "stopped") {
          return "stopped";
        }
        if (result === "pending") {
          return "pending";
        }
        return "failed";
      };
    }
    return loadedMethod.apply(this, args);
  };
}

const requeueErrorJournalEntry = loadAppletMethod(
  "_requeueErrorJournalEntry",
  "_clearErrorJournalRetryTimer",
  { value: 0 },
  { MAX_ERROR_JOURNAL_QUEUE: 128 }
);

function makeErrorJournalSubprocessHarness(options = {}) {
  const lifecycleErrors = [];
  const safeErrors = [];
  const spawnArgs = [];
  const timerCallbacks = [];
  const cancellable = {
    cancel() {
      if (options.cancelThrows === true) {
        throw new Error("journal cancellation failed");
      }
      return true;
    },
  };
  const pendingStream = {
    read_bytes_async() {},
  };
  const process = {
    get_stdout_pipe: () => pendingStream,
    get_stderr_pipe: () => pendingStream,
    wait_check_async() {},
    wait_check_finish: () => true,
  };
  const cancellableRegistry = options.unregisterCancellableFails === true
    ? new Proxy({}, { deleteProperty: () => false })
    : {};
  const Gio = {
    Cancellable: function() {
      return cancellable;
    },
    SubprocessFlags: {
      STDOUT_PIPE: 1,
      STDERR_PIPE: 2,
      STDIN_PIPE: 4,
    },
    SubprocessLauncher: function() {
      return {
        setenv() {},
        spawnv(args) {
          spawnArgs.push(args.slice());
          if (options.spawnThrows === true) {
            throw new Error("journal spawn failed");
          }
          return process;
        },
      };
    },
  };
  const Mainloop = {
    source_remove: () => true,
    timeout_add(_delay, callback) {
      timerCallbacks.push(callback);
      return 41;
    },
    timeout_add_seconds(_delay, callback) {
      timerCallbacks.push(callback);
      return 41;
    },
  };
  const runBoundedSubprocess = loadAppletMethod(
    "_runBoundedSubprocess",
    "_spawnJsonWithBackendEnvironment",
    { value: 0 },
    {
      ByteArray: { fromString: (value) => Buffer.from(value, "utf8") },
      CLI_COMMAND_TIMEOUT_MS: 1000,
      Gio,
      GLib: { PRIORITY_DEFAULT: 0 },
      MAX_SPAWN_JSON_BYTES: 4096,
      MAX_SPAWN_STDERR_BYTES: 1024,
      SUBPROCESS_READ_CHUNK_BYTES: 256,
      _decodeSubprocessOutputChunks: () => "",
      utf8ByteLength: (value) => Buffer.byteLength(value, "utf8"),
    }
  );
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    { value: 0 }
  );
  const unregisterCancellable = loadAppletMethod(
    "_unregisterCancellable",
    "_trackOrphanedCancellable",
    { value: 0 }
  );
  const clearTrackedTimer = loadAppletMethod(
    "_clearTrackedTimer",
    "_scheduleTrackedTimer",
    { value: 0 },
    { Mainloop }
  );
  const trackedTimerOwnedBy = loadAppletMethod(
    "_trackedTimerOwnedBy",
    "_untrackTimer",
    { value: 0 }
  );
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    { value: 0 },
    { Mainloop }
  );
  const applet = {
    appletRemoved: false,
    spawnGeneration: 1,
    _orphanedProcesses: [],
    _orphanedCancellables: [],
    _orphanedTimers: [],
    _resourceRegistry: {
      cancellables: cancellableRegistry,
      processes: {},
      timers: {},
      timerGroups: {},
      timerOwners: {},
    },
    _lifecycleAllowsWork: () => true,
    _wrapSubprocessArgs: (args) => args,
    _registerProcess(target, generation, group) {
      this._resourceRegistry.processes["process-1"] = {
        process: target,
        generation,
        group,
      };
      return "process-1";
    },
    _registerCancellable(target) {
      this._resourceRegistry.cancellables["cancellable-1"] = target;
      return "cancellable-1";
    },
    _unregisterCancellable: options.unregisterCancellableFails === true
      ? unregisterCancellable
      : () => true,
    _untrackOrphanedCancellable: () => true,
    _unregisterProcess: () => true,
    _untrackOrphanedProcess: () => true,
    _clearTrackedTimer: options.realTimeoutTimerFailure === true
      ? clearTrackedTimer
      : () => true,
    _scheduleTrackedTimer: options.realTimeoutTimerFailure === true
      ? scheduleTrackedTimer
      : undefined,
    _trackTimer(name, sourceId, propertyName, _resourceGroup, owner) {
      this._resourceRegistry.timers[name] = sourceId;
      this._resourceRegistry.timerOwners[name] = owner || {};
      if (propertyName) {
        this[propertyName] = sourceId;
        this[propertyName + "Owner"] = this._resourceRegistry.timerOwners[name];
      }
      return sourceId;
    },
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _untrackTimer: () => true,
    _untrackOrphanedTimer: options.realTimeoutTimerFailure === true
      ? () => false
      : () => true,
    _trackOrphanedTimer: () => true,
    _runStateGuarded(_group, callback) {
      return callback();
    },
    _terminateProcess: () => true,
    _trackOrphanedProcess: () => true,
    _trackOrphanedCancellable: () => true,
    _scheduleProcessCleanupRetry() {},
    _retryOrphanedProcesses: () => true,
    _retryOrphanedCancellables: () => true,
    _retryOrphanedTimers: () => true,
    _runTeardownGuarded(_group, callback) {
      callback();
    },
    _reportSubprocessError: reportSubprocessError,
    _recordLifecycleError(...args) {
      lifecycleErrors.push(args);
    },
    _safeLogError(error) {
      safeErrors.push(error);
    },
  };

  return {
    applet,
    lifecycleErrors,
    runBoundedSubprocess,
    safeErrors,
    spawnArgs,
    timerCallbacks,
  };
}

test("error journal spawn failure cannot recursively spawn record-error", () => {
  const harness = makeErrorJournalSubprocessHarness({ spawnThrows: true });
  let recursiveAttempted = false;
  harness.applet._recordLifecycleError = () => {
    if (recursiveAttempted) {
      return;
    }
    recursiveAttempted = true;
    harness.runBoundedSubprocess.call(
      harness.applet,
      ["/trusted/soc", "record-error", "--json"],
      {},
      { resourceGroup: "error-journal", timeoutMs: 0 },
      () => {}
    );
  };

  const handle = harness.runBoundedSubprocess.call(
    harness.applet,
    ["/trusted/soc", "record-error", "--json"],
    {},
    { resourceGroup: "error-journal", timeoutMs: 0 },
    () => {}
  );

  assert.equal(handle, null);
  assert.equal(harness.spawnArgs.length, 1);
  assert.equal(recursiveAttempted, false);
  assert.equal(harness.safeErrors.length, 1);
});

test("non-journal spawn failure keeps normal lifecycle reporting", () => {
  const harness = makeErrorJournalSubprocessHarness({ spawnThrows: true });

  const handle = harness.runBoundedSubprocess.call(
    harness.applet,
    ["/trusted/soc", "status", "--json"],
    {},
    { resourceGroup: "status", timeoutMs: 0 },
    () => {}
  );

  assert.equal(handle, null);
  assert.equal(harness.spawnArgs.length, 1);
  assert.equal(harness.lifecycleErrors.length, 1);
  assert.equal(harness.safeErrors.length, 0);
});

test("error journal null handle is retained without recursive drain", () => {
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    { value: 0 }
  );
  const spawnCalls = [];
  const entry = { group: "status", message: "status start failed" };
  const applet = {
    appletRemoved: false,
    errorJournalInFlight: false,
    errorJournalQueue: [entry],
    _cliCommand: () => "/trusted/soc",
    _spawnJson(args, _callback, options) {
      spawnCalls.push({ args, options });
      return null;
    },
    _drainErrorJournalQueue: drainErrorJournalQueue,
    _requeueErrorJournalEntry: requeueErrorJournalEntry,
    _scheduleErrorJournalRetry: () => true,
  };

  drainErrorJournalQueue.call(applet);

  assert.equal(spawnCalls.length, 1);
  assert.equal(Array.from(spawnCalls[0].args).join("\0"), "/trusted/soc\0record-error\0--json");
  assert.equal(spawnCalls[0].options.resourceGroup, "error-journal");
  assert.equal(applet.errorJournalInFlight, false);
  assert.deepEqual(applet.errorJournalQueue, [entry]);
});

test("error journal keeps failed payload and removes only explicit success", () => {
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    { value: 0 }
  );
  for (const [payload, expectedQueued, expectedSafeLogs] of [
    [{ status: "error", error: "journal unavailable" }, 1, 1],
    [{ status: "done", logged: false }, 1, 1],
    [{ status: "done", logged: true }, 0, 0],
  ]) {
    const entry = { group: "status", message: "status start failed" };
    const safeErrors = [];
    let spawnCalls = 0;
    let retrySchedules = 0;
    const applet = {
      appletRemoved: false,
      errorJournalInFlight: false,
      errorJournalQueue: [entry],
      _cliCommand: () => "/trusted/soc",
      _spawnJson(_args, callback) {
        spawnCalls += 1;
        callback(payload);
        return { token: "journal" };
      },
      _drainErrorJournalQueue: drainErrorJournalQueue,
      _requeueErrorJournalEntry: requeueErrorJournalEntry,
      _clearErrorJournalRetryTimer: () => true,
      _scheduleErrorJournalRetry() {
        retrySchedules += 1;
        return true;
      },
      _safeLogError(error) {
        safeErrors.push(error);
      },
    };

    drainErrorJournalQueue.call(applet);

    assert.equal(spawnCalls, 1);
    assert.equal(applet.errorJournalInFlight, false);
    assert.equal(applet.errorJournalQueue.length, expectedQueued);
    assert.equal(safeErrors.length, expectedSafeLogs);
    assert.equal(retrySchedules, expectedQueued);
  }
});

test("asynchronous error journal failure requeues without immediate respawn", () => {
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    { value: 0 }
  );
  const entry = { group: "status", message: "status start failed" };
  const safeErrors = [];
  let journalCallback = null;
  let spawnCalls = 0;
  const applet = {
    appletRemoved: false,
    errorJournalInFlight: false,
    errorJournalQueue: [entry],
    _cliCommand: () => "/trusted/soc",
    _spawnJson(_args, callback) {
      spawnCalls += 1;
      journalCallback = callback;
      return { token: "journal" };
    },
    _drainErrorJournalQueue: drainErrorJournalQueue,
    _requeueErrorJournalEntry: requeueErrorJournalEntry,
    _scheduleErrorJournalRetry: () => true,
    _safeLogError(error) {
      safeErrors.push(error);
    },
  };

  drainErrorJournalQueue.call(applet);
  assert.equal(applet.errorJournalInFlight, true);
  assert.equal(applet.errorJournalQueue.length, 0);

  journalCallback({ status: "error", transport_error: true });

  assert.equal(spawnCalls, 1);
  assert.equal(applet.errorJournalInFlight, false);
  assert.deepEqual(applet.errorJournalQueue, [entry]);
  assert.equal(safeErrors.length, 1);
});

test("error journal drain composes with real spawnJson success validation", () => {
  const clock = { value: 0 };
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    clock
  );
  const spawnJson = loadAppletMethod("_spawnJson", "_spawnText", clock, {
    CLI_COMMAND_TIMEOUT_MS: 1000,
    MAX_SPAWN_JSON_BYTES: 4096,
    MAX_SPAWN_STDERR_BYTES: 1024,
  });
  const guardStateCallback = loadAppletMethod(
    "_guardStateCallback",
    "_handleInitializationFailure",
    clock
  );
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    clock
  );
  for (const [payload, expectedQueued] of [
    [{ status: "error", error: "journal unavailable" }, 1],
    [{ status: "done", logged: true }, 0],
  ]) {
    const safeErrors = [];
    const applet = {
      appletRemoved: false,
      errorJournalInFlight: false,
      errorJournalQueue: [{ group: "status", message: "status start failed" }],
      _statusRefreshToken: 0,
      _cliCommand: () => "/trusted/soc",
      _drainErrorJournalQueue: drainErrorJournalQueue,
      _requeueErrorJournalEntry: requeueErrorJournalEntry,
      _scheduleErrorJournalRetry: () => true,
      _clearErrorJournalRetryTimer: () => true,
      _spawnJson: spawnJson,
      _guardStateCallback: guardStateCallback,
      _reportSubprocessError: reportSubprocessError,
      _lifecycleAllowsWork: () => true,
      _coerceSpawnArgs: (args) => args,
      _isStatusCommandArgs: () => false,
      _shouldExposeOpenAiCompatibleApiKeyToBackend: () => false,
      _runWithBackendEnvironment(_includeKey, callback) {
        return callback(null);
      },
      _spawnJsonWithBackendEnvironment(_args, _env, callback) {
        callback(JSON.stringify(payload), {}, "");
        return { token: "journal" };
      },
      _parseSpawnOutput: (output) => JSON.parse(output),
      _recordLifecycleError() {
        throw new Error("journal failure recursed into lifecycle reporting");
      },
      _safeLogError(error) {
        safeErrors.push(error);
      },
      _lifecycleErrorText: (error) => String(error && error.message || error),
    };

    drainErrorJournalQueue.call(applet);

    assert.equal(applet.errorJournalInFlight, false);
    assert.equal(applet.errorJournalQueue.length, expectedQueued);
    assert.equal(safeErrors.length, expectedQueued);
  }
});

function makeErrorJournalRetryHarness(payloads) {
  const clock = { value: 0 };
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    clock
  );
  const scheduleErrorJournalRetry = loadAppletMethod(
    "_scheduleErrorJournalRetry",
    "_safeLogError",
    clock,
    {
      ERROR_JOURNAL_RETRY_BASE_MS: 250,
      ERROR_JOURNAL_RETRY_MAX_MS: 4000,
    }
  );
  const clearErrorJournalRetryTimer = loadAppletMethod(
    "_clearErrorJournalRetryTimer",
    "_scheduleErrorJournalRetry",
    clock
  );
  const timers = [];
  let spawnCalls = 0;
  const applet = {
    appletRemoved: false,
    errorJournalInFlight: false,
    errorJournalQueue: [{ group: "status", message: "status start failed" }],
    errorJournalRetryCount: 0,
    errorJournalRetryTimer: 0,
    _lifecycleAllowsWork: () => true,
    _cliCommand: () => "/trusted/soc",
    _drainErrorJournalQueue: drainErrorJournalQueue,
    _requeueErrorJournalEntry: requeueErrorJournalEntry,
    _scheduleErrorJournalRetry: scheduleErrorJournalRetry,
    _clearErrorJournalRetryTimer: clearErrorJournalRetryTimer,
    _spawnJson(_args, callback) {
      const payload = payloads[Math.min(spawnCalls, payloads.length - 1)];
      spawnCalls += 1;
      callback(payload);
      return { token: `journal-${spawnCalls}` };
    },
    _scheduleTrackedTimer(name, delay, callback, useSeconds, propertyName, resourceGroup) {
      const timer = {
        id: timers.length + 1,
        name,
        delay,
        callback,
        useSeconds,
        propertyName,
        resourceGroup,
      };
      timers.push(timer);
      this[propertyName] = timer.id;
      return timer.id;
    },
    _clearTrackedTimer(_name, propertyName) {
      this[propertyName] = 0;
      return true;
    },
    _safeLogError() {},
  };
  return { applet, timers, get spawnCalls() { return spawnCalls; } };
}

test("failed error journal payload retries once by timer then resets on success", () => {
  const harness = makeErrorJournalRetryHarness([
    { status: "error", transport_error: true },
    { status: "done", logged: true },
  ]);

  harness.applet._drainErrorJournalQueue();

  assert.equal(harness.spawnCalls, 1);
  assert.equal(harness.applet.errorJournalQueue.length, 1);
  assert.equal(harness.timers.length, 1);
  assert.equal(harness.timers[0].delay, 250);
  assert.equal(harness.timers[0].resourceGroup, "error-journal");

  harness.applet.errorJournalQueue.push({ group: "status", message: "another failure" });
  harness.applet._drainErrorJournalQueue();
  assert.equal(harness.spawnCalls, 1);
  assert.equal(harness.timers.length, 1);

  assert.equal(harness.timers[0].callback(), false);

  assert.equal(harness.spawnCalls, 3);
  assert.equal(harness.applet.errorJournalQueue.length, 0);
  assert.equal(harness.applet.errorJournalRetryCount, 0);
  assert.equal(harness.applet.errorJournalRetryTimer, 0);
  assert.equal(harness.timers.length, 1);
});

test("error journal retry uses one capped timer and remains teardown safe", () => {
  const harness = makeErrorJournalRetryHarness([
    { status: "error", transport_error: true },
  ]);

  harness.applet._drainErrorJournalQueue();
  harness.applet._scheduleErrorJournalRetry();
  assert.equal(harness.timers.length, 1);

  for (let index = 0; index < 6; index++) {
    assert.equal(harness.timers[index].callback(), false);
  }

  assert.deepEqual(harness.timers.map((timer) => timer.delay), [250, 500, 1000, 2000, 4000, 4000, 4000]);
  harness.applet.errorJournalQueue = Array.from(
    { length: 128 },
    (_value, index) => ({ group: "status", message: `error-${index}` })
  );
  harness.applet._requeueErrorJournalEntry({ group: "status", message: "retry" });
  assert.equal(harness.applet.errorJournalQueue.length, 128);
  assert.equal(harness.applet.errorJournalQueue[0].message, "retry");

  const pendingCallback = harness.timers.at(-1).callback;
  assert.equal(harness.applet._clearErrorJournalRetryTimer(), true);
  harness.applet.appletRemoved = true;
  assert.equal(pendingCallback(), false);
  assert.equal(harness.applet.errorJournalRetryTimer, 0);
});

test("error journal retry scheduling blocks reentrant drain from foreign timer cleanup", () => {
  const clock = { value: 0 };
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    clock
  );
  const recordErrorFile = loadAppletMethod(
    "_recordErrorFile",
    "_drainErrorJournalQueue",
    clock,
    { MAX_ERROR_JOURNAL_QUEUE: 128 }
  );
  const scheduleErrorJournalRetry = loadAppletMethod(
    "_scheduleErrorJournalRetry",
    "_safeLogError",
    clock,
    {
      ERROR_JOURNAL_RETRY_BASE_MS: 250,
      ERROR_JOURNAL_RETRY_MAX_MS: 4000,
    }
  );
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    clock,
    {
      Mainloop: {
        source_remove: () => false,
        timeout_add(_delay, callback) {
          timerCallbacks.push(callback);
          return 77;
        },
        timeout_add_seconds() {
          throw new Error("seconds timer is unexpected");
        },
      },
    }
  );
  const retryOrphanedTimers = loadAppletMethod(
    "_retryOrphanedTimers",
    "_clearTrackedTimer",
    clock,
    {
      LIFECYCLE_REMOVED: "REMOVED",
      LIFECYCLE_REMOVING: "REMOVING",
      Mainloop: { source_remove: () => false },
    }
  );
  const clearTrackedTimer = loadAppletMethod(
    "_clearTrackedTimer",
    "_scheduleTrackedTimer",
    clock,
    { Mainloop: { source_remove: () => false } }
  );
  const trackedTimerOwnedBy = loadAppletMethod(
    "_trackedTimerOwnedBy",
    "_untrackTimer",
    clock
  );
  const trackTimer = loadAppletMethod("_trackTimer", "_trackedTimerOwnedBy", clock);
  const untrackTimer = loadAppletMethod("_untrackTimer", "_trackOrphanedTimer", clock);
  const untrackOrphanedTimer = loadAppletMethod(
    "_untrackOrphanedTimer",
    "_retryOrphanedTimers",
    clock
  );
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    clock
  );
  const timerCallbacks = [];
  const payloads = [
    { status: "error", transport_error: true },
    { status: "done", logged: true },
    { status: "done", logged: true },
  ];
  let spawnCalls = 0;
  let lifecycleErrors = 0;
  const applet = {
    appletRemoved: false,
    lifecycleState: "RUNNING",
    spawnGeneration: 1,
    errorJournalInFlight: false,
    errorJournalQueue: [{ group: "status", message: "status failed" }],
    errorJournalRetryCount: 0,
    errorJournalRetryTimer: 0,
    errorJournalRetryScheduling: false,
    _activeTrackedTimer: null,
    _orphanedTimers: [{
      name: "foreign-timer",
      sourceId: 31,
      propertyName: "",
      sourceRemoved: false,
      resourceGroup: "status",
    }],
    _resourceRegistry: { timers: {}, timerGroups: {}, timerOwners: {} },
    _lifecycleAllowsWork: () => true,
    _cliCommand: () => "/trusted/soc",
    _drainErrorJournalQueue: drainErrorJournalQueue,
    _recordErrorFile: recordErrorFile,
    _requeueErrorJournalEntry: requeueErrorJournalEntry,
    _scheduleErrorJournalRetry: scheduleErrorJournalRetry,
    _clearErrorJournalRetryTimer() {
      return clearTrackedTimer.call(this, "error-journal-retry", "errorJournalRetryTimer", undefined, "error-journal");
    },
    _scheduleTrackedTimer: scheduleTrackedTimer,
    _retryOrphanedTimers: retryOrphanedTimers,
    _clearTrackedTimer: clearTrackedTimer,
    _trackTimer: trackTimer,
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _untrackTimer: untrackTimer,
    _trackOrphanedTimer: () => true,
    _untrackOrphanedTimer: untrackOrphanedTimer,
    _reportSubprocessError: reportSubprocessError,
    _recordLifecycleError(group, error) {
      lifecycleErrors += 1;
      this._recordErrorFile(group, error);
    },
    _lifecycleErrorText: (error) => String(error && error.message || error),
    _safeLogError() {},
    _spawnJson(_args, callback) {
      const payload = payloads[spawnCalls];
      spawnCalls += 1;
      callback(payload);
      return { token: `journal-${spawnCalls}` };
    },
  };

  applet._drainErrorJournalQueue();

  assert.equal(lifecycleErrors, 1);
  assert.equal(spawnCalls, 1);
  assert.equal(timerCallbacks.length, 1);
  assert.equal(applet.errorJournalRetryScheduling, false);
  assert.equal(applet.errorJournalRetryTimer, 77);
  assert.equal(applet.errorJournalQueue.length, 2);

  assert.equal(timerCallbacks[0](), false);
  assert.equal(spawnCalls, 3);
  assert.equal(applet.errorJournalQueue.length, 0);
});

test("error journal retry scheduling clears gate after scheduler throw or zero", () => {
  const clock = { value: 0 };
  const drainErrorJournalQueue = loadAppletMethod(
    "_drainErrorJournalQueue",
    "_safeLogError",
    clock
  );
  const scheduleErrorJournalRetry = loadAppletMethod(
    "_scheduleErrorJournalRetry",
    "_safeLogError",
    clock,
    {
      ERROR_JOURNAL_RETRY_BASE_MS: 250,
      ERROR_JOURNAL_RETRY_MAX_MS: 4000,
    }
  );

  for (const schedulerResult of ["throw", "zero"]) {
    let spawnCalls = 0;
    let scheduleAttempts = 0;
    const safeErrors = [];
    const entry = { group: "status", message: "status failed" };
    const applet = {
      appletRemoved: false,
      errorJournalInFlight: false,
      errorJournalQueue: [entry],
      errorJournalRetryCount: 0,
      errorJournalRetryTimer: 0,
      errorJournalRetryScheduling: false,
      _lifecycleAllowsWork: () => true,
      _cliCommand: () => "/trusted/soc",
      _drainErrorJournalQueue: drainErrorJournalQueue,
      _spawnJson() {
        spawnCalls += 1;
        return { token: "unexpected" };
      },
      _scheduleTrackedTimer() {
        scheduleAttempts += 1;
        if (scheduleAttempts > 1) {
          this.errorJournalRetryTimer = 91;
          return 91;
        }
        this._drainErrorJournalQueue();
        if (schedulerResult === "throw") {
          throw new Error("timer scheduler failed");
        }
        return 0;
      },
      _safeLogError(error) {
        safeErrors.push(error);
      },
    };

    assert.equal(scheduleErrorJournalRetry.call(applet), false);
    assert.equal(spawnCalls, 0);
    assert.equal(applet.errorJournalRetryScheduling, false);
    assert.equal(applet.errorJournalRetryTimer, 0);
    assert.deepEqual(applet.errorJournalQueue, [entry]);
    assert.equal(safeErrors.length, 1);

    assert.equal(scheduleErrorJournalRetry.call(applet), true);
    assert.equal(scheduleAttempts, 2);
    assert.equal(applet.errorJournalRetryTimer, 91);
    assert.equal(applet.errorJournalRetryScheduling, false);
  }
});

test("error journal cleanup failure only uses bounded safe logging", () => {
  const harness = makeErrorJournalSubprocessHarness({ cancelThrows: true });
  const callbackResults = [];
  const handle = harness.runBoundedSubprocess.call(
    harness.applet,
    ["/trusted/soc", "record-error", "--json"],
    {},
    { resourceGroup: "error-journal", timeoutMs: 0 },
    (_stdout, _stderr, result) => callbackResults.push(result)
  );

  assert.notEqual(handle, null);
  assert.equal(handle.cancel(), false);
  assert.equal(harness.lifecycleErrors.length, 0);
  assert.equal(harness.safeErrors.length, 1);
  assert.equal(callbackResults.length, 1);
  assert.equal(callbackResults[0].cleanupFailed, true);
});

test("error journal internal unregister failure cannot start another journal spawn", () => {
  const harness = makeErrorJournalSubprocessHarness({ unregisterCancellableFails: true });
  const callbackResults = [];
  let recursiveAttempts = 0;
  harness.applet._recordLifecycleError = () => {
    recursiveAttempts += 1;
    harness.runBoundedSubprocess.call(
      harness.applet,
      ["/trusted/soc", "record-error", "--json"],
      {},
      { resourceGroup: "error-journal", timeoutMs: 0 },
      () => {}
    );
  };
  const handle = harness.runBoundedSubprocess.call(
    harness.applet,
    ["/trusted/soc", "record-error", "--json"],
    {},
    { resourceGroup: "error-journal", timeoutMs: 0 },
    (_stdout, _stderr, result) => callbackResults.push(result)
  );

  assert.notEqual(handle, null);
  assert.equal(handle.cancel(), false);
  assert.equal(harness.spawnArgs.length, 1);
  assert.equal(recursiveAttempts, 0);
  assert.equal(harness.safeErrors.length, 1);
  assert.equal(callbackResults.length, 1);
  assert.equal(callbackResults[0].cleanupFailed, true);
});

test("global delayed cleanup orchestrator failure remains neutral", () => {
  const scheduleProcessCleanupRetry = loadAppletMethod(
    "_scheduleProcessCleanupRetry",
    "_cancelPendingRecordingStart",
    { value: 0 }
  );
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    { value: 0 }
  );
  const lifecycleErrors = [];
  const safeErrors = [];
  let retryCallback = null;
  let retryResourceGroup = null;
  const applet = {
    processCleanupRetryTimer: 0,
    _lifecycleAllowsWork: () => true,
    _processCleanupStillPending: () => true,
    _scheduleTrackedTimer(_name, _delay, callback, _useSeconds, _propertyName, resourceGroup) {
      retryCallback = callback;
      retryResourceGroup = resourceGroup;
      return 71;
    },
    _retryOrphanedProcesses() {
      throw new Error("delayed journal cleanup failed");
    },
    _reportSubprocessError: reportSubprocessError,
    _recordLifecycleError(...args) {
      lifecycleErrors.push(args);
    },
    _safeLogError(error) {
      safeErrors.push(error);
    },
  };

  assert.equal(scheduleProcessCleanupRetry.call(applet, "error-journal"), true);
  assert.equal(typeof retryCallback, "function");
  assert.equal(retryResourceGroup, undefined);
  assert.equal(retryCallback(), true);
  assert.equal(lifecycleErrors.length, 1);
  assert.equal(safeErrors.length, 0);
});

test("tracking the same monitor orphan twice upgrades its existing entry", () => {
  const trackOrphanedMonitor = loadAppletMethod(
    "_trackOrphanedMonitor",
    "_untrackOrphanedMonitor",
    { value: 0 }
  );
  const monitor = {};
  const lifecycleErrors = [];
  let scheduleCalls = 0;
  const applet = {
    _orphanedMonitors: [],
    processCleanupRetryTimer: 0,
    _lifecycleAllowsWork: () => true,
    _scheduleProcessCleanupRetry() {
      scheduleCalls += 1;
      this.processCleanupRetryTimer = 73;
      return true;
    },
    _recordLifecycleError(...args) {
      lifecycleErrors.push(args);
    },
  };

  assert.equal(trackOrphanedMonitor.call(applet, monitor, false), true);
  assert.equal(trackOrphanedMonitor.call(applet, monitor, true), true);
  assert.equal(applet._orphanedMonitors.length, 1);
  assert.equal(applet._orphanedMonitors[0].monitor, monitor);
  assert.equal(applet._orphanedMonitors[0].cancelSucceeded, true);
  assert.equal(scheduleCalls, 1);
  assert.equal(lifecycleErrors.length, 0);
});

test("missing cancellable registry routes known orphans by resource group", () => {
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    { value: 0 }
  );
  const retryCancellables = loadAppletMethod(
    "_retryOrphanedCancellables",
    "_cancelAllCancellables",
    { value: 0 },
    { LIFECYCLE_REMOVED: "REMOVED", LIFECYCLE_REMOVING: "REMOVING" }
  );

  for (const [resourceGroup, expectedLifecycleErrors, expectedSafeErrors] of [
    ["status", 1, 0],
    ["error-journal", 0, 1],
  ]) {
    const lifecycleErrors = [];
    const safeErrors = [];
    const applet = {
      appletRemoved: false,
      lifecycleState: "RUNNING",
      _orphanedCancellables: [{
        token: "c-1",
        cancelSucceeded: false,
        resourceGroup,
      }],
      _resourceRegistry: {},
      _reportSubprocessError: reportSubprocessError,
      _recordLifecycleError(group, error) {
        if (resourceGroup === "error-journal") {
          throw new Error("error-journal failure recursed into lifecycle journaling");
        }
        lifecycleErrors.push([group, error]);
      },
      _safeLogError(error) {
        safeErrors.push(error);
      },
    };

    assert.equal(retryCancellables.call(applet), false);
    assert.equal(lifecycleErrors.length, expectedLifecycleErrors);
    assert.equal(safeErrors.length, expectedSafeErrors);
    const routedError = lifecycleErrors[0] || ["", safeErrors[0]];
    if (expectedLifecycleErrors) {
      assert.equal(routedError[0], "cancellable-state");
    }
    assert.match(routedError[1].message, /Cancellable registry is unavailable/);
  }
});

test("mixed orphan cleanup routes each entry by its own resource group", () => {
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    { value: 0 }
  );
  const retryProcesses = loadAppletMethod(
    "_retryOrphanedProcesses",
    "_processCleanupStillPending",
    { value: 0 },
    { LIFECYCLE_REMOVED: "REMOVED", LIFECYCLE_REMOVING: "REMOVING" }
  );
  const retryCancellables = loadAppletMethod(
    "_retryOrphanedCancellables",
    "_cancelAllCancellables",
    { value: 0 },
    { LIFECYCLE_REMOVED: "REMOVED", LIFECYCLE_REMOVING: "REMOVING" }
  );
  const retryTimers = loadAppletMethod(
    "_retryOrphanedTimers",
    "_clearTrackedTimer",
    { value: 0 },
    {
      LIFECYCLE_REMOVED: "REMOVED",
      LIFECYCLE_REMOVING: "REMOVING",
      Mainloop: { source_remove: () => false },
    }
  );

  for (const groups of [
    ["error-journal", "status"],
    ["status", "error-journal"],
  ]) {
    const safeErrors = [];
    const lifecycleErrors = [];
    const base = {
      appletRemoved: false,
      lifecycleState: "RUNNING",
      _reportSubprocessError: reportSubprocessError,
      _recordLifecycleError(group, error) {
        lifecycleErrors.push([group, error]);
      },
      _safeLogError(error) {
        safeErrors.push(error);
      },
    };
    const processApplet = {
      ...base,
      _orphanedProcesses: groups.map((group) => ({ group, process: null })),
      _resourceRegistry: { processes: {} },
    };
    assert.equal(retryProcesses.call(processApplet), false);

    const cancellableApplet = {
      ...base,
      _orphanedCancellables: groups.map((resourceGroup, index) => ({
        token: `c-${index}`,
        cancelSucceeded: false,
        resourceGroup,
      })),
      _resourceRegistry: {
        cancellables: {
          "c-0": { cancel() { throw new Error("cancel failed"); } },
          "c-1": { cancel() { throw new Error("cancel failed"); } },
        },
      },
    };
    assert.equal(retryCancellables.call(cancellableApplet), false);

    const timerApplet = {
      ...base,
      _orphanedTimers: groups.map((resourceGroup, index) => ({
        name: `timer-${index}`,
        sourceId: index + 1,
        propertyName: "",
        sourceRemoved: false,
        resourceGroup,
      })),
      _resourceRegistry: { timers: {} },
    };
    assert.equal(retryTimers.call(timerApplet), false);

    assert.equal(safeErrors.length, 3);
    assert.equal(lifecycleErrors.length, 3);
  }
});

test("mixed teardown registries route each entry by its stored resource group", () => {
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    { value: 0 }
  );
  const terminateAllProcesses = loadAppletMethod(
    "_terminateAllProcesses",
    "_terminateProcessesByGroup",
    { value: 0 }
  );
  const cancelAllCancellables = loadAppletMethod(
    "_cancelAllCancellables",
    "_trackTimer",
    { value: 0 }
  );

  for (const groups of [
    ["error-journal", "status"],
    ["status", "error-journal"],
  ]) {
    const safeErrors = [];
    const lifecycleErrors = [];
    const base = {
      _reportSubprocessError: reportSubprocessError,
      _recordLifecycleError(group, error) {
        lifecycleErrors.push([group, error]);
      },
      _safeLogError(error) {
        safeErrors.push(error);
      },
    };
    const processApplet = {
      ...base,
      _resourceRegistry: {
        processes: Object.fromEntries(groups.map((group, index) => [
          `p-${index}`,
          {
            group,
            process: {},
            cancel() {
              throw new Error("process cancel failed");
            },
          },
        ])),
      },
      _trackOrphanedProcess: () => true,
    };
    assert.equal(terminateAllProcesses.call(processApplet), false);

    const cancellableApplet = {
      ...base,
      _resourceRegistry: {
        cancellables: Object.fromEntries(groups.map((_group, index) => [
          `c-${index}`,
          {
            cancel() {
              throw new Error("cancellable cancel failed");
            },
          },
        ])),
        cancellableGroups: Object.fromEntries(groups.map((group, index) => [`c-${index}`, group])),
      },
      _trackOrphanedCancellable: () => true,
    };
    assert.equal(cancelAllCancellables.call(cancellableApplet), false);

    assert.equal(safeErrors.length, 2);
    assert.equal(lifecycleErrors.length, 2);
  }
});

test("singleton cleanup retry never applies first caller group to mixed orphans", () => {
  const scheduleProcessCleanupRetry = loadAppletMethod(
    "_scheduleProcessCleanupRetry",
    "_cancelPendingRecordingStart",
    { value: 0 }
  );
  let retryCallback = null;
  const retryArgumentCounts = [];
  const applet = {
    processCleanupRetryTimer: 0,
    _lifecycleAllowsWork: () => true,
    _processCleanupStillPending: () => true,
    _scheduleTrackedTimer(_name, _delay, callback) {
      retryCallback = callback;
      this.processCleanupRetryTimer = 91;
      return 91;
    },
    _retryOrphanedProcesses(...args) {
      retryArgumentCounts.push(args.length);
      return false;
    },
    _retryOrphanedCancellables(...args) {
      retryArgumentCounts.push(args.length);
      return true;
    },
    _retryOrphanedTimers(...args) {
      retryArgumentCounts.push(args.length);
      return true;
    },
    _disconnectOrphanedSignals: () => true,
    _retryOrphanedMonitors: () => true,
    _retryOrphanedHotkeys: () => true,
    _retryPendingHotkeyRebinds: () => true,
    _retryOrphanedDialogs: () => true,
    _recordLifecycleError() {},
  };

  assert.equal(scheduleProcessCleanupRetry.call(applet, "status"), true);
  assert.equal(scheduleProcessCleanupRetry.call(applet, "error-journal"), true);
  assert.equal(retryCallback(), true);
  assert.deepEqual(retryArgumentCounts, [0, 0, 0]);
});

test("error journal timeout timer cleanup failure stays non-recursive", () => {
  const harness = makeErrorJournalSubprocessHarness({ realTimeoutTimerFailure: true });
  const callbackResults = [];
  const handle = harness.runBoundedSubprocess.call(
    harness.applet,
    ["/trusted/soc", "record-error", "--json"],
    {},
    { resourceGroup: "error-journal", timeoutMs: 25 },
    (_stdout, _stderr, result) => callbackResults.push(result)
  );

  assert.notEqual(handle, null);
  assert.equal(harness.timerCallbacks.length, 1);
  assert.equal(harness.timerCallbacks[0](), false);
  assert.equal(harness.lifecycleErrors.length, 0);
  assert.equal(harness.safeErrors.length, 1);
  assert.equal(callbackResults.length, 1);
  assert.equal(callbackResults[0].timedOut, true);
});

test("error journal callback failure only uses bounded safe logging", () => {
  const harness = makeErrorJournalSubprocessHarness();
  const handle = harness.runBoundedSubprocess.call(
    harness.applet,
    ["/trusted/soc", "record-error", "--json"],
    {},
    { resourceGroup: "error-journal", timeoutMs: 0 },
    () => {
      throw new Error("journal callback failed");
    }
  );

  assert.notEqual(handle, null);
  assert.equal(handle.cancel(), true);
  assert.equal(harness.lifecycleErrors.length, 0);
  assert.equal(harness.safeErrors.length, 1);
});

test("spawnJson keeps error journal callback failure out of lifecycle journal", () => {
  const clock = { value: 0 };
  const spawnJson = loadAppletMethod("_spawnJson", "_spawnText", clock, {
    CLI_COMMAND_TIMEOUT_MS: 1000,
    MAX_SPAWN_JSON_BYTES: 4096,
    MAX_SPAWN_STDERR_BYTES: 1024,
  });
  const guardStateCallback = loadAppletMethod(
    "_guardStateCallback",
    "_handleInitializationFailure",
    clock
  );
  const runStateGuarded = loadAppletMethod(
    "_runStateGuarded",
    "_runTeardownGuarded",
    clock
  );
  const reportSubprocessError = loadAppletMethod(
    "_reportSubprocessError",
    "_recordLifecycleError",
    clock
  );
  const lifecycleErrors = [];
  const safeErrors = [];
  const applet = {
    _statusRefreshToken: 0,
    _guardStateCallback: guardStateCallback,
    _runStateGuarded: runStateGuarded,
    _reportSubprocessError: reportSubprocessError,
    _lifecycleAllowsWork: () => true,
    _coerceSpawnArgs: (args) => args,
    _isStatusCommandArgs: () => false,
    _shouldExposeOpenAiCompatibleApiKeyToBackend: () => false,
    _runWithBackendEnvironment(_includeKey, callback) {
      return callback(null);
    },
    _spawnJsonWithBackendEnvironment(_args, _env, callback) {
      callback('{"status":"done"}', {}, "");
      return { token: "journal" };
    },
    _parseSpawnOutput: (output) => JSON.parse(output),
    _recordLifecycleError(...args) {
      lifecycleErrors.push(args);
    },
    _safeLogError(error) {
      safeErrors.push(error);
    },
    _lifecycleErrorText: (error) => String(error && error.message || error),
  };

  const handle = spawnJson.call(
    applet,
    ["/trusted/soc", "record-error", "--json"],
    () => {
      throw new Error("journal callback failed");
    },
    { resourceGroup: "error-journal", invalidatesStatus: false }
  );

  assert.equal(handle.token, "journal");
  assert.equal(lifecycleErrors.length, 0);
  assert.equal(safeErrors.length, 1);
});

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
  const copyLastErrorItem = {};
  const applet = {
    status: "recording",
    _statusRefreshToken: 0,
    lastTranscript: "",
    copyLastItem: copyItem,
    insertLastItem: insertItem,
    cancelItem,
    copyLastErrorItem,
    _lifecycleAllowsWork: () => true,
    _hasActiveRecordingState: () => true,
    _hasCancelableRecordingWork: () => true,
    _uiMessageText: (value) => value,
    _sanitizeErrorMessage: (value) => value,
    _rememberLastErrorMessage() {},
    _setMenuItemSensitiveSafely(item, sensitive) {
      item.sensitive = sensitive;
    },
    _updatePanel() {},
    _recordLifecycleError() {},
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

test("language start clears token when focus capture throws", () => {
  const clock = { value: 0 };
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  let lifecycleErrors = 0;
  const applet = {
    status: "idle",
    lastTranscript: "",
    isCommandRunning: false,
    _recordingCommandToken: null,
    _recordingStartToken: null,
    _hasActiveRecordingState: () => false,
    _setStatusPreservingRecording() {},
    _rememberFocusedWindow() {
      throw new Error("focus capture failed");
    },
    _recordLifecycleError() {
      lifecycleErrors += 1;
    },
  };

  assert.equal(startWithLanguage.call(applet, "en"), false);
  assert.equal(applet._recordingStartToken, null);
  assert.equal(lifecycleErrors, 1);
});

test("language start rejects synchronous toggle exception", () => {
  const clock = { value: 0 };
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  let lifecycleErrors = 0;
  const applet = {
    status: "idle",
    lastTranscript: "",
    isCommandRunning: false,
    _recordingCommandToken: null,
    _recordingStartToken: null,
    _hasActiveRecordingState: () => false,
    _setStatusPreservingRecording() {},
    _rememberFocusedWindow(_preserveTargetOnFailure, completionCallback) {
      completionCallback(true, false);
      return true;
    },
    _normalizeLanguage: (value) => value,
    _primaryLanguage: () => "en",
    _toggleRecording() {
      throw new Error("recording start failed");
    },
    _recordLifecycleError() {
      lifecycleErrors += 1;
    },
  };

  assert.equal(startWithLanguage.call(applet, "en"), false);
  assert.equal(applet._recordingStartToken, null);
  assert.equal(lifecycleErrors, 1);
});

test("main hotkey waits for focus capture and cancels stale pending start", () => {
  const clock = { value: 0 };
  const startLifecycle = loadAppletMethod(
    "_startLifecycle",
    "_lifecycleAllowsWork",
    clock,
    {
      LIFECYCLE_INITIALIZING: "INITIALIZING",
      HOTKEY_ID: "speed-of-cinnamon-toggle",
      PRIMARY_HOTKEY_ID: "speed-of-cinnamon-primary-language",
      SECONDARY_HOTKEY_ID: "speed-of-cinnamon-secondary-language",
      CANCEL_HOTKEY_ID: "speed-of-cinnamon-cancel",
    }
  );
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  const cancelPendingFocusStart = loadAppletMethod(
    "_cancelPendingRecordingFocusStart",
    "_populateLanguageMenu",
    clock
  );
  const cancelRecording = loadAppletMethod(
    "_cancelRecording",
    "_invalidateBackgroundCallbacksForRecording",
    clock
  );
  const cancelPendingRecordingStart = loadAppletMethod(
    "_cancelPendingRecordingStart",
    "_queueRecordingStartAfterCleanup",
    clock
  );
  const clearAutoRelistenRetryTimer = loadAppletMethod(
    "_clearAutoRelistenRetryTimer",
    "_transcriptDigest",
    clock
  );
  let completeFocus;
  let startCalls = 0;
  let stopCalls = 0;
  const applet = {
    status: "idle",
    lastTranscript: "",
    isCommandRunning: false,
    _hasActiveRecordingState: () => false,
    _currentLanguage: () => "de",
    _primaryLanguage: () => "en",
    _normalizeLanguage: (value) => value,
    _setStatusPreservingRecording() {},
    _setStatus() {},
    _rememberFocusedWindow(_preserveTargetOnFailure, completionCallback) {
      completeFocus = completionCallback;
      return true;
    },
    _startWithLanguage: startWithLanguage,
    _cancelPendingRecordingFocusStart() {
      stopCalls += 1;
      return cancelPendingFocusStart.call(this);
    },
    _cancelRecording: cancelRecording,
    _cancelPendingRecordingStart: cancelPendingRecordingStart,
    _clearAutoRelistenRetryTimer: clearAutoRelistenRetryTimer,
    _clearRecordingStartRetryTimer() {
      this.recordingStartRetryTimer = 0;
      return true;
    },
    _terminateProcessesByGroup() {
      return true;
    },
    _toggleRecording(action) {
      if (action === "stop") {
        stopCalls += 1;
        return this._cancelPendingRecordingFocusStart();
      }
      startCalls += 1;
      return true;
    },
  };

  startLifecycle.call(applet);
  assert.equal(applet.errorJournalRetryScheduling, false);
  applet._hotkeyCallbacks["speed-of-cinnamon-toggle"]();

  assert.equal(startCalls, 0);
  assert.equal(typeof completeFocus, "function");
  assert.ok(applet._recordingStartToken);

  applet._hotkeyCallbacks["speed-of-cinnamon-toggle"]();
  assert.equal(stopCalls, 1);
  assert.equal(applet._recordingStartToken, null);

  completeFocus(true, false);
  assert.equal(startCalls, 0);

  applet._hotkeyCallbacks["speed-of-cinnamon-toggle"]();
  const currentFocusCompletion = completeFocus;
  currentFocusCompletion(true, false);
  assert.equal(startCalls, 1);

  applet._recordingStartToken = null;
  applet.recordingStartPendingAfterCleanup = true;
  applet.recordingStartRetryTimer = 7;
  applet._hotkeyCallbacks["speed-of-cinnamon-toggle"]();
  assert.equal(applet.recordingStartPendingAfterCleanup, false);
  assert.equal(applet.recordingStartRetryTimer, 0);
});

for (const transientBlock of ["busy", "cleanup"]) {
  test(`transient ${transientBlock} keeps one auto relisten retry`, () => {
    const clock = { value: 0 };
    const finishPendingRelisten = loadAppletMethod(
      "_finishPendingRelisten",
      "_transcriptDigest",
      clock
    );
    const schedulePendingAutoRelistenRetry = loadAppletMethod(
      "_schedulePendingAutoRelistenRetry",
      "_clearAutoRelistenRetryTimer",
      clock
    );
    const timers = [];
    let relistenCalls = 0;
    let blocked = true;
    const applet = {
      autoRelistenPending: true,
      autoRelistenPendingToken: "1:recording",
      autoRelistenPendingLanguage: "de",
      autoRelistenManualStopRequested: false,
      cancelIntentActive: false,
      notificationSessionActive: true,
      autoRelistenRetryTimer: 0,
      _autoRelistenStartBlock: "",
      isCommandRunning: transientBlock === "busy",
      _hasLocalProcessingWorkflow: () => false,
      _processCleanupStillPending: () => transientBlock === "cleanup" && blocked,
      _lifecycleAllowsWork: () => true,
      _restartRelistenRecording() {
        relistenCalls += 1;
        if (relistenCalls === 1) {
          this._autoRelistenStartBlock = transientBlock;
          return false;
        }
        return true;
      },
      _scheduleTrackedTimer(name, delay, callback, useSeconds, propertyName) {
        timers.push({ name, delay, callback, useSeconds, propertyName });
        this[propertyName] = timers.length;
        return timers.length;
      },
      _finishPendingRelisten: finishPendingRelisten,
      _schedulePendingAutoRelistenRetry: schedulePendingAutoRelistenRetry,
    };

    assert.equal(applet._finishPendingRelisten(), false);
    assert.equal(relistenCalls, 1);
    assert.equal(applet.autoRelistenPending, true);
    assert.equal(applet.autoRelistenPendingToken, "1:recording");
    assert.equal(timers.length, 1);

    timers[0].callback();
    assert.equal(relistenCalls, 1);
    assert.equal(timers.length, 1);

    blocked = false;
    applet.isCommandRunning = false;
    timers[0].callback();
    assert.equal(relistenCalls, 2);
    assert.equal(applet.autoRelistenPending, true);
    assert.equal(applet.autoRelistenPendingToken, "1:recording");
});
}

test("auto relisten retry schedule failure terminates pending work", () => {
  const clock = { value: 0 };
  const finishPendingRelisten = loadAppletMethod(
    "_finishPendingRelisten",
    "_transcriptDigest",
    clock
  );
  const schedulePendingAutoRelistenRetry = loadAppletMethod(
    "_schedulePendingAutoRelistenRetry",
    "_clearAutoRelistenRetryTimer",
    clock
  );
  const statusEvents = [];
  const applet = {
    autoRelistenPending: true,
    autoRelistenPendingToken: "1:recording",
    autoRelistenPendingLanguage: "de",
    autoRelistenManualStopRequested: false,
    cancelIntentActive: false,
    notificationSessionActive: true,
    autoRelistenRetryTimer: 0,
    _autoRelistenStartBlock: "",
    isCommandRunning: false,
    _hasLocalProcessingWorkflow: () => false,
    _processCleanupStillPending: () => false,
    _restartRelistenRecording() {
      this._autoRelistenStartBlock = "busy";
      return false;
    },
    _scheduleTrackedTimer() {
      return 0;
    },
    _setStatus(status, message) {
      statusEvents.push({ status, message });
      this.status = status;
    },
    _finishPendingRelisten: finishPendingRelisten,
    _schedulePendingAutoRelistenRetry: schedulePendingAutoRelistenRetry,
  };

  assert.equal(applet._finishPendingRelisten(), false);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenManualStopRequested, true);
  assert.equal(statusEvents.length, 1);
  assert.equal(statusEvents[0].status, "error");
});

test("new auto relisten reservation clears stale retry before scheduling", () => {
  const clock = { value: 0 };
  const ensurePendingRelisten = loadAppletMethod(
    "_ensureAutoRelistenPendingForDonePayload",
    "_finishPendingRelisten",
    clock
  );
  const finishPendingRelisten = loadAppletMethod(
    "_finishPendingRelisten",
    "_transcriptDigest",
    clock
  );
  const prepareAutoRelistenReservation = loadAppletMethod(
    "_prepareAutoRelistenReservation",
    "_finishPendingRelisten",
    clock
  );
  const clearAutoRelistenRetryTimer = loadAppletMethod(
    "_clearAutoRelistenRetryTimer",
    "_transcriptDigest",
    clock
  );
  const schedulePendingAutoRelistenRetry = loadAppletMethod(
    "_schedulePendingAutoRelistenRetry",
    "_clearAutoRelistenRetryTimer",
    clock
  );
  const clearedTimers = [];
  const scheduledTimers = [];
  const applet = {
    autoRelisten: true,
    notificationSessionActive: true,
    autoRelistenPending: false,
    autoRelistenPendingToken: "",
    autoRelistenPendingLanguage: "",
    autoRelistenManualStopRequested: false,
    autoRelistenSequence: 0,
    autoRelistenRetryTimer: 17,
    cancelIntentActive: false,
    isCommandRunning: false,
    _clearTrackedTimer(name, propertyName) {
      clearedTimers.push([name, propertyName]);
      this[propertyName] = 0;
      return true;
    },
    _payloadStringMarker: () => "new-recording",
    _setStatus() {},
    _clearAutoRelistenRetryTimer: clearAutoRelistenRetryTimer,
    _prepareAutoRelistenReservation: prepareAutoRelistenReservation,
    _finishPendingRelisten: finishPendingRelisten,
    _schedulePendingAutoRelistenRetry: schedulePendingAutoRelistenRetry,
    _hasLocalProcessingWorkflow: () => false,
    _processCleanupStillPending: () => false,
    _lifecycleAllowsWork: () => true,
    _restartRelistenRecording() {
      this._autoRelistenStartBlock = "busy";
      return false;
    },
    _scheduleTrackedTimer(name, delay, callback, useSeconds, propertyName) {
      scheduledTimers.push({ name, delay, callback, useSeconds, propertyName });
      this[propertyName] = scheduledTimers.length + 20;
      return this[propertyName];
    },
  };

  assert.equal(ensurePendingRelisten.call(applet, { language: "de" }), true);
  assert.equal(applet.autoRelistenPendingToken, "1:done:new-recording");
  assert.deepEqual(clearedTimers, [["auto-relisten-retry", "autoRelistenRetryTimer"]]);

  assert.equal(applet._finishPendingRelisten(), false);
  assert.equal(scheduledTimers.length, 1);
  assert.notEqual(applet.autoRelistenRetryTimer, 17);
});

test("failed stale retry clear prevents timed auto relisten reservation", () => {
  const harness = makeRecordingApplet({ realTimedAutoTranscribe: true });
  const applet = harness.applet;
  applet.autoRelisten = true;
  applet.autoTranscribeTimeout = true;
  applet.notificationSessionActive = true;
  applet.autoRelistenRetryTimer = 9;
  applet._clearTrackedTimer = (_name, propertyName) => {
    applet[propertyName] = 0;
    return false;
  };

  applet._maybeAutoTranscribeRecorded(
    { status: "recorded", audio_path: "/tmp/new-recording.flac" },
    "recorded"
  );

  assert.equal(harness.requests.length, 0);
  assert.equal(applet.autoTranscribeRecordingKey, "");
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(harness.statusEvents.at(-1).status, "error");
});

test("failed stale retry clear prevents done auto relisten reservation", () => {
  const clock = { value: 0 };
  const ensurePendingRelisten = loadAppletMethod(
    "_ensureAutoRelistenPendingForDonePayload",
    "_finishPendingRelisten",
    clock
  );
  const prepareAutoRelistenReservation = loadAppletMethod(
    "_prepareAutoRelistenReservation",
    "_finishPendingRelisten",
    clock
  );
  const clearAutoRelistenRetryTimer = loadAppletMethod(
    "_clearAutoRelistenRetryTimer",
    "_transcriptDigest",
    clock
  );
  const applet = {
    autoRelisten: true,
    notificationSessionActive: true,
    autoRelistenPending: false,
    autoRelistenPendingToken: "",
    autoRelistenPendingLanguage: "",
    autoRelistenManualStopRequested: false,
    autoRelistenRetryTimer: 9,
    autoRelistenSequence: 0,
    _payloadStringMarker: () => "done-recording",
    _clearAutoRelistenRetryTimer: clearAutoRelistenRetryTimer,
    _prepareAutoRelistenReservation: prepareAutoRelistenReservation,
    _clearTrackedTimer(_name, propertyName) {
      this[propertyName] = 0;
      return false;
    },
    _setStatus(status) {
      this.status = status;
    },
  };

  assert.equal(ensurePendingRelisten.call(applet, { language: "de" }), false);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenManualStopRequested, true);
  assert.equal(applet.status, "error");
});

test("manual stop invalidates pending auto relisten retry", () => {
  const clock = { value: 0 };
  const schedulePendingAutoRelistenRetry = loadAppletMethod(
    "_schedulePendingAutoRelistenRetry",
    "_clearAutoRelistenRetryTimer",
    clock
  );
  const harness = makeRecordingApplet();
  const { applet } = harness;
  applet.autoRelistenPending = true;
  applet.autoRelistenPendingToken = "1:recording";
  applet.autoRelistenManualStopRequested = false;
  applet.textInsertToken = {};
  applet._schedulePendingAutoRelistenRetry = schedulePendingAutoRelistenRetry;
  assert.equal(applet._schedulePendingAutoRelistenRetry("1:recording"), true);
  const staleRetryCallback = harness.scheduledTimers.at(-1).callback;

  applet._cancelRecording();
  staleRetryCallback();
  applet._cancelRecording();

  assert.deepEqual(harness.trackedTimerClears, [["auto-relisten-retry", "autoRelistenRetryTimer"]]);
  assert.equal(applet.autoRelistenRetryTimer, 0);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(harness.requests.length, 0);
});

test("teardown invalidates and removes the tracked auto relisten timer", () => {
  const clock = { value: 0 };
  const onAppletRemoved = loadAppletMethod(
    "on_applet_removed_from_panel",
    "_baseArgs",
    clock,
    {
      HOTKEY_ID: "speed-of-cinnamon-toggle",
      PRIMARY_HOTKEY_ID: "speed-of-cinnamon-primary-language",
      SECONDARY_HOTKEY_ID: "speed-of-cinnamon-secondary-language",
      CANCEL_HOTKEY_ID: "speed-of-cinnamon-cancel",
    }
  );
  const beginTeardown = loadAppletMethod(
    "_beginTeardown",
    "_finishTeardown",
    clock,
    {
      LIFECYCLE_REMOVING: "REMOVING",
      LIFECYCLE_REMOVED: "REMOVED",
    }
  );
  const clearedTimers = [];
  const applet = {
    lifecycleState: "active",
    _teardownComplete: false,
    appletRemoved: false,
    spawnGeneration: 0,
    targetWindowGeneration: 7,
    targetWindowXPendingGeneration: 7,
    targetWindowWaylandEvidence: { generation: 7, codex: true },
    autoRelistenRetryTimer: 17,
    errorJournalRetryScheduling: true,
    _beginTeardown: beginTeardown,
    _clearAutoRelistenRetryTimer: loadAppletMethod(
      "_clearAutoRelistenRetryTimer",
      "_transcriptDigest",
      clock
    ),
    _clearTrackedTimer(name, propertyName) {
      clearedTimers.push([name, propertyName]);
      this[propertyName] = 0;
      return true;
    },
    _runTeardownGuarded(_name, callback) {
      try {
        callback();
      } catch (_error) {
        // Teardown deliberately continues through unrelated missing harness APIs.
      }
    },
    _removeHotkey() {},
    _finishTeardown() {},
    _destroyAppletTooltip() {},
  };

  onAppletRemoved.call(applet);
  onAppletRemoved.call(applet);

  assert.equal(applet.errorJournalRetryScheduling, false);
  assert.deepEqual(clearedTimers[0], ["auto-relisten-retry", "autoRelistenRetryTimer"]);
  assert.equal(applet.autoRelistenRetryTimer, 0);
  assert.equal(applet.targetWindowGeneration, 8);
  assert.equal(applet.targetWindowXPendingGeneration, 0);
  assert.equal(applet.targetWindowWaylandEvidence, null);
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
  const trackedTimerOwnedBy = loadAppletMethod(
    "_trackedTimerOwnedBy",
    "_untrackTimer",
    clock
  );
  const applet = {
    appletRemoved: false,
    lifecycleState: "active",
    spawnGeneration: 1,
    _orphanedTimers: [{ name: "clipboard", sourceId: 99, propertyName: "clipboardTimer" }],
    _resourceRegistry: { timers: {}, timerGroups: {}, timerOwners: {} },
    _lifecycleAllowsWork: () => true,
    _retryOrphanedTimers() {
      retryCalls += 1;
      return false;
    },
    _clearTrackedTimer: () => true,
    _trackTimer(name, sourceId, propertyName, _resourceGroup, owner) {
      this._resourceRegistry.timers[name] = sourceId;
      this._resourceRegistry.timerOwners[name] = owner || {};
      if (propertyName) {
        this[propertyName] = sourceId;
        this[propertyName + "Owner"] = this._resourceRegistry.timerOwners[name];
      }
      return sourceId;
    },
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
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

test("busy command retires status poll and status application schedules one replacement", () => {
  const clock = { value: 0 };
  const timerCallbacks = new Map();
  let nextTimerId = 1;
  const lifecycleErrors = [];
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    clock,
    {
      Mainloop: {
        timeout_add() {
          throw new Error("unexpected millisecond timer");
        },
        timeout_add_seconds(_delay, callback) {
          const sourceId = nextTimerId++;
          timerCallbacks.set(sourceId, callback);
          return sourceId;
        },
        source_remove(sourceId) {
          return timerCallbacks.delete(sourceId);
        },
      },
    }
  );
  const trackedTimerOwnedBy = loadAppletMethod(
    "_trackedTimerOwnedBy",
    "_untrackTimer",
    clock
  );
  const applet = {
    appletRemoved: false,
    spawnGeneration: 1,
    lifecycleState: "active",
    status: "recording",
    isCommandRunning: true,
    cancelIntentActive: false,
    cancelRecoveryAttempts: 0,
    _statusCommandRunning: false,
    _statusRefreshToken: 0,
    statusTimer: 0,
    lastTranscript: "",
    lastErrorMessage: "",
    notificationSessionActive: false,
    recordingLongWarningShown: false,
    _activeTrackedTimer: null,
    _orphanedTimers: [],
    _resourceRegistry: { timers: {}, timerGroups: {}, timerOwners: {} },
    _scheduleTrackedTimer: scheduleTrackedTimer,
    _scheduleStatusPoll: loadAppletMethod(
      "_scheduleStatusPoll",
      "_scheduleDisplayTick",
      clock
    ),
    _refreshStatus: loadAppletMethod(
      "_refreshStatus",
      "_hasCancelableRecordingWork",
      clock
    ),
    _setStatus: loadAppletMethod(
      "_setStatus",
      "_rememberLastErrorMessage",
      clock
    ),
    _lifecycleAllowsWork: () => true,
    _hasLocalProcessingWorkflow: () => false,
    _hasActiveRecordingState: () => true,
    _hasCancelableRecordingWork: () => true,
    _retryOrphanedTimers: () => true,
    _untrackOrphanedTimer: () => true,
    _trackOrphanedTimer: () => true,
    _runStateGuarded(_name, callback) {
      return callback();
    },
    _trackTimer(name, sourceId, propertyName, _resourceGroup, owner) {
      this._resourceRegistry.timers[name] = sourceId;
      this._resourceRegistry.timerOwners[name] = owner || {};
      this[propertyName] = sourceId;
      this[propertyName + "Owner"] = this._resourceRegistry.timerOwners[name];
      return sourceId;
    },
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _untrackTimer(name, sourceId, propertyName) {
      if (this._resourceRegistry.timers[name] === sourceId) {
        delete this._resourceRegistry.timers[name];
        delete this._resourceRegistry.timerOwners[name];
      }
      if (this[propertyName] === sourceId) {
        this[propertyName] = 0;
        this[propertyName + "Owner"] = null;
      }
      return true;
    },
    _clearTrackedTimer(name, propertyName) {
      delete this._resourceRegistry.timers[name];
      delete this._resourceRegistry.timerOwners[name];
      this[propertyName] = 0;
      this[propertyName + "Owner"] = null;
      return true;
    },
    _recordLifecycleError(...args) {
      lifecycleErrors.push(args);
    },
    _reportSubprocessError(...args) {
      lifecycleErrors.push(args);
    },
    _uiMessageText: (value) => value,
    _sanitizeErrorMessage: (value) => value,
    _rememberLastErrorMessage() {},
    _setMenuItemSensitiveSafely() {},
    _updatePanel() {},
    _maybeNotify() {},
    _scheduleDisplayTick() {},
  };

  applet._scheduleStatusPoll();
  assert.equal(applet.statusTimer, 1);
  const firstStatusTickContinues = timerCallbacks.get(1)();
  if (!firstStatusTickContinues) {
    timerCallbacks.delete(1);
  }
  assert.equal(firstStatusTickContinues, false);
  assert.equal(applet.statusTimer, 0);
  assert.equal(applet._resourceRegistry.timers.status, undefined);

  applet.isCommandRunning = false;
  applet._setStatus("recording", "Recording...", "");

  assert.equal(applet.statusTimer, 2);
  assert.equal(applet._resourceRegistry.timers.status, 2);
  assert.equal(timerCallbacks.size, 1);
  assert.deepEqual(lifecycleErrors, []);
});

function makeOwnedStatusPollHarness(status) {
  const clock = { value: 0 };
  const timerCallbacks = new Map();
  let nextTimerId = 1;
  let statusCallback = null;
  let statusSpawnCalls = 0;
  let localProcessing = false;
  const applet = {
    appletRemoved: false,
    status,
    statusTimer: 0,
    isCommandRunning: false,
    cancelIntentActive: false,
    cancelRecoveryAttempts: 0,
    _statusCommandRunning: false,
    _statusCommandToken: null,
    _statusRefreshToken: 0,
    _orphanedTimers: [],
    _resourceRegistry: { timers: {} },
    lastTranscript: "",
    lastErrorMessage: "",
    notificationSessionActive: false,
    recordingLongWarningShown: false,
    _scheduleStatusPoll: loadAppletMethod(
      "_scheduleStatusPoll",
      "_scheduleDisplayTick",
      clock
    ),
    _refreshStatus: loadAppletMethod(
      "_refreshStatus",
      "_hasCancelableRecordingWork",
      clock,
      {
        MAX_CANCEL_RECOVERY_ATTEMPTS: 3,
        STATUS_COMMAND_TIMEOUT_MS: 1000,
      }
    ),
    _setStatus: loadAppletMethod(
      "_setStatus",
      "_rememberLastErrorMessage",
      clock
    ),
    _cancelTextInsertForSettingsChange: loadAppletMethod(
      "_cancelTextInsertForSettingsChange",
      "on_applet_clicked",
      clock
    ),
    _scheduleTrackedTimer(_name, _delay, callback, _useSeconds, propertyName) {
      const sourceId = nextTimerId++;
      this[propertyName] = sourceId;
      this._resourceRegistry.timers.status = sourceId;
      timerCallbacks.set(sourceId, () => {
        const keepTimer = callback() === true;
        if (!keepTimer && this[propertyName] === sourceId) {
          this[propertyName] = 0;
          delete this._resourceRegistry.timers.status;
          timerCallbacks.delete(sourceId);
        }
        return keepTimer;
      });
      return sourceId;
    },
    _clearStatusTimer() {
      const sourceId = this.statusTimer;
      this.statusTimer = 0;
      delete this._resourceRegistry.timers.status;
      timerCallbacks.delete(sourceId);
      return true;
    },
    _retryOrphanedTimers: () => true,
    _hasLocalProcessingWorkflow() {
      return localProcessing || Boolean(this.textInsertToken);
    },
    _lifecycleAllowsWork: () => true,
    _statusArgs: () => ["status"],
    _spawnJson(_args, callback) {
      statusSpawnCalls += 1;
      statusCallback = callback;
      return {};
    },
    _applyPayload(payload) {
      this.status = payload.status;
    },
    _sanitizeErrorMessage: (value) => String(value),
    _setStatusPreservingRecording() {},
    _hasCancelableRecordingWork: () => true,
    _rememberLastErrorMessage() {},
    _setMenuItemSensitiveSafely() {},
    _updatePanel() {},
    _maybeNotify() {},
    _scheduleDisplayTick() {},
    _recordLifecycleError() {},
    _uiMessageText: (value) => value,
    _clearClipboardOverwriteApproval() {},
    _clearPasteTimer: () => true,
    _terminateProcessesByGroup: () => true,
    _forgetAutoInsertFingerprint: () => true,
  };
  return {
    applet,
    timerCallbacks,
    setLocalProcessing(value) {
      localProcessing = value === true;
    },
    statusCallback: () => statusCallback,
    statusSpawnCalls: () => statusSpawnCalls,
    scheduledTimerCount: () => nextTimerId - 1,
  };
}

test("running status command retires poll until its completion schedules one replacement", () => {
  const harness = makeOwnedStatusPollHarness("recording");
  const { applet } = harness;

  assert.equal(applet._refreshStatus(), false);
  assert.equal(applet._statusCommandRunning, true);
  assert.equal(harness.statusSpawnCalls(), 1);
  applet._scheduleStatusPoll();
  assert.equal(harness.timerCallbacks.get(1)(), false);
  assert.equal(applet.statusTimer, 0);
  assert.equal(harness.statusSpawnCalls(), 1);

  harness.statusCallback()({ status: "recording" });
  assert.equal(applet._statusCommandRunning, false);
  assert.equal(applet.statusTimer, 2);
  assert.equal(harness.scheduledTimerCount(), 2);
  assert.equal(harness.timerCallbacks.size, 1);
});

test("text insert cancellation restarts one retired processing poll after real owner release", () => {
  const harness = makeOwnedStatusPollHarness("processing");
  const { applet } = harness;
  applet.textInsertToken = {};
  applet.textInsertCancellationFailed = false;
  applet.autoInsertPendingFingerprint = "";
  applet.clipboardOverwriteDialog = null;
  applet.autoRelistenPending = true;
  applet.autoRelistenPendingToken = "pending-insert";
  applet.autoRelistenPendingLanguage = "de";
  applet.autoRelistenManualStopRequested = false;
  applet.targetWindowGeneration = 4;
  applet.targetWindowWaylandEvidence = {};

  applet._scheduleStatusPoll();
  assert.equal(harness.timerCallbacks.get(1)(), false);
  assert.equal(applet.statusTimer, 0);
  assert.equal(harness.statusSpawnCalls(), 0);

  assert.equal(applet._cancelTextInsertForSettingsChange(), true);
  assert.equal(applet.textInsertToken, null);
  assert.equal(applet.autoRelistenPending, false);
  assert.equal(applet.autoRelistenPendingToken, "");
  assert.equal(applet.autoRelistenPendingLanguage, "");
  assert.equal(applet.autoRelistenManualStopRequested, true);
  assert.equal(applet.statusTimer, 2);
  assert.equal(harness.scheduledTimerCount(), 2);
  assert.equal(harness.timerCallbacks.size, 1);

  assert.equal(applet._cancelTextInsertForSettingsChange(), true);
  assert.equal(harness.scheduledTimerCount(), 2);
  assert.equal(harness.timerCallbacks.size, 1);
});

test("text insert owner release does not poll while another owner remains", () => {
  for (const owner of ["command", "status", "local"]) {
    const harness = makeOwnedStatusPollHarness("processing");
    const { applet } = harness;
    applet.textInsertToken = {};
    applet.textInsertCancellationFailed = false;
    applet.autoInsertPendingFingerprint = "";
    applet.clipboardOverwriteDialog = null;
    applet.autoRelistenPending = false;
    applet.targetWindowGeneration = 0;
    if (owner === "command") {
      applet.isCommandRunning = true;
    } else if (owner === "status") {
      applet._statusCommandRunning = true;
    } else {
      harness.setLocalProcessing(true);
    }

    applet._scheduleStatusPoll();
    assert.equal(harness.timerCallbacks.get(1)(), false);
    assert.equal(applet.statusTimer, 0);
    assert.equal(applet._cancelTextInsertForSettingsChange(), true);
    assert.equal(applet.textInsertToken, null);
    assert.equal(applet.statusTimer, 0);
    assert.equal(harness.scheduledTimerCount(), 1);
    assert.equal(harness.statusSpawnCalls(), 0);
  }
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

test("clipboard-paste-submit fallback does not append auto-submit newline", () => {
  const clock = { value: 0 };
  const insertTranscriptText = loadAppletMethod(
    "_insertTranscriptText",
    "_restartRelistenRecording",
    clock
  );
  const copyAndMaybePaste = loadAppletMethod(
    "_copyAndMaybePasteTranscriptText",
    "_confirmClipboardOverwriteForPaste",
    clock
  );
  const resolveOutputActions = loadAppletMethod(
    "_resolveOutputActions",
    "_migrateInsertMethodSemantics",
    clock
  );
  const preparedTranscriptText = loadAppletMethod(
    "_preparedTranscriptText",
    "_sanitizeSpecialChars",
    clock,
    { MAX_TEXT_INSERT_CHARS: 120000 }
  );
  const clipboardWrites = [];
  const applet = {
    insertMethod: "clipboard-paste-submit",
    sanitizeSpecialChars: false,
    appendSpace: false,
    textInsertCancellationFailed: false,
    textInsertToken: null,
    clipboardOverwriteDialog: null,
    autoInsertConflictToken: null,
    autoRelistenPending: false,
    targetWindowGeneration: 1,
    lastTranscript: "",
    _lifecycleAllowsWork: () => true,
    _hasPendingTextInsertResources: () => false,
    _normalizeOutputMethod: (value) => value,
    _preferredKeyboardProgram: () => null,
    _windowTitleMatchesAutoPaste: () => true,
    _isEmptyTranscriptText: (value) => String(value || "").trim() === "",
    _resolveOutputActions: resolveOutputActions,
    _preparedTranscriptText: preparedTranscriptText,
    _copyAndMaybePasteTranscriptText: copyAndMaybePaste,
    _setClipboardText(text) {
      clipboardWrites.push(text);
      return true;
    },
    _setStatus() {},
    _setStatusPreservingRecording() {},
    _recordLifecycleError() {},
  };

  assert.equal(insertTranscriptText.call(applet, "hello"), true);
  assert.deepEqual(clipboardWrites, ["hello"]);
  assert.equal(clipboardWrites[0].endsWith("\n"), false);
});

test("Wayland focus evidence survives a dynamic Codex title for one paste and submit", () => {
  const clock = { value: 0 };
  const targetWindow = {
    title: "Codex — project",
    windowClass: "xterm",
    get_title() {
      return this.title;
    },
    get_wm_class() {
      return this.windowClass;
    },
    get_wm_class_instance() {
      return this.windowClass;
    },
    get_gtk_application_id: () => "",
    get_xwindow: () => "",
    is_skip_taskbar: () => false,
  };
  const terminalMarkers = ["xterm", "ghostty", "kitty"];
  const identityMarkers = {
    codex: terminalMarkers,
    terminal: terminalMarkers,
    ghostty: ["ghostty"],
    kitty: ["kitty"],
    pdf: ["evince"],
    excel: ["libreoffice"],
    teams: ["microsoft teams"],
    telegram: ["telegram"],
    obsidian: ["obsidian"],
  };
  const rememberFocusedWindow = loadAppletMethod(
    "_rememberFocusedWindow",
    "_restoreTargetWindowForPaste",
    clock,
    { global: { display: { focus_window: targetWindow } } }
  );
  const captureWaylandTargetEvidence = loadAppletMethod(
    "_captureWaylandTargetEvidence",
    "_isTargetWindowXLookupPending",
    clock,
    { AUTO_PASTE_IDENTITY_MARKERS: identityMarkers }
  );
  const waylandTargetEvidence = loadAppletMethod(
    "_waylandTargetEvidenceForCurrentGeneration",
    "_captureWaylandTargetEvidence",
    clock
  );
  const isTerminalTargetWindow = loadAppletMethod(
    "_isTerminalTargetWindow",
    "_clipboardProgramSpecs",
    clock,
    { TERMINAL_WINDOW_MARKERS: terminalMarkers }
  );
  const isCodexTerminalTargetWindow = loadAppletMethod(
    "_isCodexTerminalTargetWindow",
    "_normalizeCodexTerminalSubmitKey",
    clock
  );
  const pasteClipboardAfterFocus = loadAppletMethod(
    "_pasteClipboardAfterFocus",
    "_typeTextAfterFocus",
    clock
  );
  const copyAndMaybePaste = loadAppletMethod(
    "_copyAndMaybePasteTranscriptText",
    "_confirmClipboardOverwriteForPaste",
    clock
  );
  const targetXWindowSnapshot = loadAppletMethod(
    "_targetXWindowSnapshot",
    "_targetXWindowMatchesSnapshot",
    clock
  );
  const codexTerminalSubmitKey = loadAppletMethod(
    "_codexTerminalSubmitKey",
    "_isTerminalTargetWindow",
    clock
  );
  const normalizeCodexTerminalSubmitKey = loadAppletMethod(
    "_normalizeCodexTerminalSubmitKey",
    "_normalizeCodexTerminalCustomKey",
    clock,
    { CODEX_TERMINAL_SUBMIT_KEY_MODES: ["enter", "tab", "custom-key"] }
  );
  const normalizeCodexTerminalCustomKey = loadAppletMethod(
    "_normalizeCodexTerminalCustomKey",
    "_codexTerminalSubmitKey",
    clock
  );
  let clipboardWrites = 0;
  let pasteCalls = 0;
  let submitCalls = 0;
  let submitArgs = null;
  const applet = {
    targetWindowGeneration: 0,
    targetWindowXPendingGeneration: 0,
    targetWindowXid: "",
    targetWindowXTitle: "",
    targetWindowXClass: "",
    targetWindowWaylandEvidence: null,
    targetWindow,
    codexTerminalSubmitKey: "custom-key",
    codexTerminalCustomKey: "F6",
    _isWaylandSession: () => true,
    _isUsableTargetWindow: () => true,
    _windowLooksLikeSpeedOfCinnamon: () => false,
    _windowProbeValue: loadAppletMethod("_windowProbeValue", "_windowLooksLikeSpeedOfCinnamon", clock),
    _windowIdentityValueMatchesMarker: loadAppletMethod(
      "_windowIdentityValueMatchesMarker",
      "_markerAllowsAutoPasteIdentity",
      clock
    ),
    _terminateProcessesByGroup: () => true,
    _clearTargetWindowXid() {
      this.targetWindowXid = "";
      this.targetWindowXTitle = "";
      this.targetWindowXClass = "";
    },
    _setStatusPreservingRecording() {},
    _recordLifecycleError() {},
    _notifySelfProtectionBlocked() {},
    _lifecycleAllowsWork: () => true,
    _rememberFocusedWindow: rememberFocusedWindow,
    _captureWaylandTargetEvidence: captureWaylandTargetEvidence,
    _waylandTargetEvidenceForCurrentGeneration: waylandTargetEvidence,
    _normalizeCodexTerminalSubmitKey: normalizeCodexTerminalSubmitKey,
    _normalizeCodexTerminalCustomKey: normalizeCodexTerminalCustomKey,
    _codexTerminalSubmitKey: codexTerminalSubmitKey,
    _isTerminalTargetWindow: isTerminalTargetWindow,
    _isCodexTerminalTargetWindow: isCodexTerminalTargetWindow,
    _targetXWindowSnapshot: targetXWindowSnapshot,
    _pasteClipboardAfterFocus: pasteClipboardAfterFocus,
    _preferredKeyboardProgram: () => ({ kind: "wtype", path: "/usr/bin/wtype" }),
    _restoreTargetWindowForPaste(callback) {
      callback(true);
      return true;
    },
    _spawnKeyboardAfterFocus(args, followUpArgs, _expectedText, _expectedTarget, callback) {
      pasteCalls += 1;
      if (followUpArgs) {
        submitCalls += 1;
        submitArgs = followUpArgs;
      }
      callback(true);
      return true;
    },
    _setClipboardText() {
      clipboardWrites += 1;
      return true;
    },
    _closeMenuForKeyboardInsert: () => true,
    _setStatus() {},
  };

  assert.equal(applet._rememberFocusedWindow(false, () => {}), true);
  assert.equal(applet.targetWindowWaylandEvidence.generation, 1);
  assert.equal(applet.targetWindowWaylandEvidence.title, "codex — project");
  assert.equal(applet._isCodexTerminalTargetWindow(), true);

  applet.targetWindowGeneration = 2;
  assert.equal(applet._captureWaylandTargetEvidence(targetWindow, 1), false);
  assert.equal(applet._waylandTargetEvidenceForCurrentGeneration(), null);

  targetWindow.title = "Evince";
  targetWindow.windowClass = "unknown-window";
  applet.targetWindowGeneration += 1;
  assert.equal(
    applet._captureWaylandTargetEvidence(targetWindow, applet.targetWindowGeneration),
    true
  );
  assert.equal(applet.targetWindowWaylandEvidence.markerMatches.pdf, false);

  for (const [marker, title, windowClass] of [
    ["pdf", "Document", "evince"],
    ["excel", "Budget", "libreoffice"],
    ["teams", "Chat", "microsoft teams"],
    ["telegram", "Chat", "telegram"],
    ["obsidian", "Notes", "obsidian"],
    ["ghostty", "shell prompt", "ghostty"],
    ["kitty", "shell prompt", "kitty"],
  ]) {
    targetWindow.title = title;
    targetWindow.windowClass = windowClass;
    applet.targetWindowGeneration += 1;
    assert.equal(
      applet._captureWaylandTargetEvidence(targetWindow, applet.targetWindowGeneration),
      true
    );
    assert.equal(applet.targetWindowWaylandEvidence.markerMatches[marker], true);
  }

  targetWindow.title = "shell prompt";
  targetWindow.windowClass = "xterm-codex";
  applet.targetWindowGeneration += 1;
  assert.equal(
    applet._captureWaylandTargetEvidence(targetWindow, applet.targetWindowGeneration),
    true
  );
  assert.equal(applet.targetWindowWaylandEvidence.terminal, true);
  assert.equal(applet.targetWindowWaylandEvidence.codex, false);

  targetWindow.title = "Codex — project";
  targetWindow.windowClass = "xterm";
  applet.targetWindowGeneration += 1;
  assert.equal(
    applet._captureWaylandTargetEvidence(targetWindow, applet.targetWindowGeneration),
    true
  );

  targetWindow.title = "shell prompt";
  applet.codexTerminalCustomKey = "Tab";
  assert.equal(applet._isTerminalTargetWindow(), true);
  assert.equal(applet._isCodexTerminalTargetWindow(), true);
  assert.equal(applet.targetWindowWaylandEvidence.title, "codex — project");
  assert.equal(
    copyAndMaybePaste.call(
      applet,
      "hello",
      "hello",
      "clipboard-paste-submit",
      true,
      true,
      () => {},
      () => true
    ),
    null
  );

  assert.equal(clipboardWrites, 1);
  assert.equal(pasteCalls, 1);
  assert.equal(submitCalls, 1);
  assert.equal(submitArgs[0], "/usr/bin/wtype");
  assert.equal(submitArgs[1], "-k");
  assert.equal(submitArgs[2], "F6");
});

test("X11 Codex detection requires the live or captured title", () => {
  const clock = { value: 0 };
  const targetWindow = {
    title: "shell prompt",
    windowClass: "xterm-codex",
    get_title() {
      return this.title;
    },
    get_wm_class() {
      return this.windowClass;
    },
    get_wm_class_instance() {
      return this.windowClass;
    },
    get_gtk_application_id: () => "codex",
  };
  const isTerminalTargetWindow = loadAppletMethod(
    "_isTerminalTargetWindow",
    "_clipboardProgramSpecs",
    clock,
    { TERMINAL_WINDOW_MARKERS: ["xterm"] }
  );
  const isCodexTerminalTargetWindow = loadAppletMethod(
    "_isCodexTerminalTargetWindow",
    "_normalizeCodexTerminalSubmitKey",
    clock
  );
  const applet = {
    targetWindow,
    targetWindowXTitle: "shell prompt",
    targetWindowXClass: "xterm-codex",
    _isWaylandSession: () => false,
    _isUsableTargetWindow: () => true,
    _isTargetWindowXLookupPending: () => false,
    _windowProbeValue: loadAppletMethod(
      "_windowProbeValue",
      "_windowLooksLikeSpeedOfCinnamon",
      clock
    ),
    _isTerminalTargetWindow: isTerminalTargetWindow,
    _isCodexTerminalTargetWindow: isCodexTerminalTargetWindow,
  };

  targetWindow.windowClass = "text-editor";
  targetWindow.title = "Codex Terminal";
  applet.targetWindowXClass = "text-editor";
  applet.targetWindowXTitle = targetWindow.title;
  assert.equal(applet._isTerminalTargetWindow(), false);
  assert.equal(applet._isCodexTerminalTargetWindow(), false);

  targetWindow.windowClass = "xterm-codex";
  targetWindow.title = "shell prompt";
  applet.targetWindowXClass = targetWindow.windowClass;
  applet.targetWindowXTitle = targetWindow.title;
  assert.equal(applet._isTerminalTargetWindow(), true);
  assert.equal(applet._isCodexTerminalTargetWindow(), false);

  targetWindow.title = "Codex — project";
  applet.targetWindowXTitle = targetWindow.title;
  assert.equal(applet._isCodexTerminalTargetWindow(), true);
});

test("real keyboard paste and submit callbacks remain exactly once", () => {
  const clock = { value: 0 };
  const spawnKeyboardAfterFocus = loadAppletMethod(
    "_spawnKeyboardAfterFocus",
    "_spawnKeyboardWhenClipboardReady",
    clock
  );
  const spawnKeyboardWhenClipboardReady = loadAppletMethod(
    "_spawnKeyboardWhenClipboardReady",
    "_spawnKeyboardProcess",
    clock
  );
  const spawnKeyboardProcess = loadAppletMethod(
    "_spawnKeyboardProcess",
    "_spawnKeyboardArgs",
    clock
  );
  const spawnKeyboardArgs = loadAppletMethod(
    "_spawnKeyboardArgs",
    "_finishAppletTextInsert",
    clock
  );
  const timers = [];
  const keyboardCalls = [];
  let completions = 0;
  const applet = {
    appletRemoved: false,
    pasteTimer: 0,
    _lifecycleAllowsWork: () => true,
    _clearPasteTimer() {
      this.pasteTimer = 0;
      return true;
    },
    _scheduleTrackedTimer(name, delay, callback, useSeconds, propertyName) {
      timers.push({ name, delay, callback, useSeconds, propertyName });
      this[propertyName] = timers.length;
      return timers.length;
    },
    _spawnKeyboardWhenClipboardReady: spawnKeyboardWhenClipboardReady,
    _spawnKeyboardProcess: spawnKeyboardProcess,
    _spawnKeyboardArgs: spawnKeyboardArgs,
    _screenSaverAllowsKeyboardInput(callback) {
      callback(true);
      return true;
    },
    _targetXWindowMatchesSnapshot(_snapshot, callback) {
      callback(true);
      return true;
    },
    _windowSnapshotMatchesAutoPaste: () => true,
    _runBoundedSubprocess(args, _options, _spawnOptions, callback) {
      keyboardCalls.push(args);
      callback("", "", {});
      callback("", "", {});
      return { pid: keyboardCalls.length };
    },
    _coerceSpawnArgs: (args) => args,
    _setStatus() {},
    _recordLifecycleError() {},
    _completeKeyboardInsertFailure() {},
  };

  assert.equal(
    spawnKeyboardAfterFocus.call(
      applet,
      ["paste"],
      ["submit"],
      null,
      { window: {} },
      () => {
        completions += 1;
      },
      () => true,
      100
    ),
    true
  );
  assert.equal(timers.length, 1);

  timers[0].callback();
  assert.equal(timers.length, 2);
  timers[1].callback();

  assert.equal(keyboardCalls.length, 2);
  assert.equal(keyboardCalls[0][0], "paste");
  assert.equal(keyboardCalls[1][0], "submit");
  assert.equal(completions, 1);
});

test("real recording start preserves current Wayland evidence through cleanup", () => {
  const clock = { value: 1000 };
  const harness = makeRecordingApplet();
  const applet = harness.applet;
  const targetWindow = {
    title: "Codex — project",
    get_title() {
      return this.title;
    },
    get_wm_class: () => "xterm",
    get_wm_class_instance: () => "xterm",
    get_gtk_application_id: () => "",
    get_xwindow: () => "",
    is_skip_taskbar: () => false,
  };
  const identityMarkers = {
    codex: ["xterm"],
    terminal: ["xterm"],
    ghostty: ["xterm"],
    kitty: ["xterm"],
  };
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  const rememberFocusedWindow = loadAppletMethod(
    "_rememberFocusedWindow",
    "_restoreTargetWindowForPaste",
    clock,
    { global: { display: { focus_window: targetWindow } } }
  );
  const captureWaylandTargetEvidence = loadAppletMethod(
    "_captureWaylandTargetEvidence",
    "_isTargetWindowXLookupPending",
    clock,
    { AUTO_PASTE_IDENTITY_MARKERS: identityMarkers }
  );
  const waylandTargetEvidence = loadAppletMethod(
    "_waylandTargetEvidenceForCurrentGeneration",
    "_captureWaylandTargetEvidence",
    clock
  );
  const cancelTextInsertForSettingsChange = loadAppletMethod(
    "_cancelTextInsertForSettingsChange",
    "on_applet_clicked",
    clock
  );
  const isTerminalTargetWindow = loadAppletMethod(
    "_isTerminalTargetWindow",
    "_clipboardProgramSpecs",
    clock,
    { TERMINAL_WINDOW_MARKERS: ["xterm"] }
  );
  const isCodexTerminalTargetWindow = loadAppletMethod(
    "_isCodexTerminalTargetWindow",
    "_normalizeCodexTerminalSubmitKey",
    clock
  );
  const targetXWindowSnapshot = loadAppletMethod(
    "_targetXWindowSnapshot",
    "_targetXWindowMatchesSnapshot",
    clock
  );
  const copyAndMaybePaste = loadAppletMethod(
    "_copyAndMaybePasteTranscriptText",
    "_confirmClipboardOverwriteForPaste",
    clock
  );
  const pasteClipboardAfterFocus = loadAppletMethod(
    "_pasteClipboardAfterFocus",
    "_typeTextAfterFocus",
    clock
  );
  const normalizeOutputMethod = (value) => value;
  let pasteCalls = 0;
  let submitArgs = null;
  let clipboardWrites = 0;
  applet.targetWindow = targetWindow;
  applet.targetWindowGeneration = 0;
  applet.targetWindowXPendingGeneration = 0;
  applet.targetWindowWaylandEvidence = null;
  applet._startWithLanguage = startWithLanguage;
  applet._rememberFocusedWindow = rememberFocusedWindow;
  applet._primaryLanguage = () => "en";
  applet._normalizeLanguage = (value) => value;
  applet._captureWaylandTargetEvidence = captureWaylandTargetEvidence;
  applet._waylandTargetEvidenceForCurrentGeneration = waylandTargetEvidence;
  applet._cancelTextInsertForSettingsChange = cancelTextInsertForSettingsChange;
  applet._isWaylandSession = () => true;
  applet._isUsableTargetWindow = () => true;
  applet._windowLooksLikeSpeedOfCinnamon = () => false;
  applet._windowProbeValue = loadAppletMethod(
    "_windowProbeValue",
    "_windowLooksLikeSpeedOfCinnamon",
    clock
  );
  applet._windowIdentityValueMatchesMarker = loadAppletMethod(
    "_windowIdentityValueMatchesMarker",
    "_markerAllowsAutoPasteIdentity",
    clock
  );
  applet._clearTargetWindowXid = () => {
    applet.targetWindowXid = "";
    applet.targetWindowXTitle = "";
    applet.targetWindowXClass = "";
  };
  applet._codexTerminalSubmitKey = () => "F6";
  applet._isTerminalTargetWindow = isTerminalTargetWindow;
  applet._isCodexTerminalTargetWindow = isCodexTerminalTargetWindow;
  applet._targetXWindowSnapshot = targetXWindowSnapshot;
  applet._pasteClipboardAfterFocus = pasteClipboardAfterFocus;
  applet._normalizeOutputMethod = normalizeOutputMethod;
  applet._preferredKeyboardProgram = () => ({ kind: "wtype", path: "/usr/bin/wtype" });
  applet._restoreTargetWindowForPaste = (callback) => {
    callback(true);
    return true;
  };
  applet._spawnKeyboardAfterFocus = (_args, followUpArgs, _text, _target, callback) => {
    pasteCalls += 1;
    submitArgs = followUpArgs;
    callback(true);
    return true;
  };
  applet._setClipboardText = () => {
    clipboardWrites += 1;
    return true;
  };
  applet._copyAndMaybePasteTranscriptText = copyAndMaybePaste;
  applet._closeMenuForKeyboardInsert = () => true;
  applet._completeKeyboardInsertFailure = () => {};
  applet._clearPasteTimer = () => true;
  applet._forgetAutoInsertFingerprint = () => true;

  assert.equal(applet._startWithLanguage("en"), true, JSON.stringify({
    status: applet.status,
    requests: harness.requests,
    lifecycleErrors: harness.lifecycleErrors,
    cleanupGroups: harness.cleanupGroups,
    generation: applet.targetWindowGeneration,
    evidence: applet.targetWindowWaylandEvidence,
  }));
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "start");
  assert.equal(applet.targetWindowWaylandEvidence.targetWindow, targetWindow);
  assert.equal(applet.targetWindowWaylandEvidence.generation, applet.targetWindowGeneration);

  targetWindow.title = "shell prompt";
  assert.equal(applet._isTerminalTargetWindow(), true);
  assert.equal(applet._isCodexTerminalTargetWindow(), true);
  assert.equal(
    copyAndMaybePaste.call(
      applet,
      "hello",
      "hello",
      "clipboard-paste-submit",
      true,
      true,
      () => {},
      () => true
    ),
    null
  );
  assert.equal(clipboardWrites, 1);
  assert.equal(pasteCalls, 1);
  assert.equal(submitArgs[2], "F6");
});

test("pending recording retry preserves Wayland evidence after transient cleanup", () => {
  const clock = { value: 1000 };
  const harness = makeRecordingApplet();
  const applet = harness.applet;
  const targetWindow = {
    title: "Codex — project",
    get_title() {
      return this.title;
    },
    get_wm_class: () => "xterm",
    get_wm_class_instance: () => "xterm",
    get_gtk_application_id: () => "",
    get_xwindow: () => "",
    is_skip_taskbar: () => false,
  };
  const identityMarkers = {
    codex: ["xterm"],
    terminal: ["xterm"],
    ghostty: ["ghostty"],
    kitty: ["kitty"],
  };
  const startWithLanguage = loadAppletMethod(
    "_startWithLanguage",
    "_populateLanguageMenu",
    clock
  );
  const rememberFocusedWindow = loadAppletMethod(
    "_rememberFocusedWindow",
    "_restoreTargetWindowForPaste",
    clock,
    { global: { display: { focus_window: targetWindow } } }
  );
  const captureWaylandTargetEvidence = loadAppletMethod(
    "_captureWaylandTargetEvidence",
    "_isTargetWindowXLookupPending",
    clock,
    { AUTO_PASTE_IDENTITY_MARKERS: identityMarkers }
  );
  const waylandTargetEvidence = loadAppletMethod(
    "_waylandTargetEvidenceForCurrentGeneration",
    "_captureWaylandTargetEvidence",
    clock
  );
  const cancelTextInsertForSettingsChange = loadAppletMethod(
    "_cancelTextInsertForSettingsChange",
    "on_applet_clicked",
    clock
  );
  const isTerminalTargetWindow = loadAppletMethod(
    "_isTerminalTargetWindow",
    "_clipboardProgramSpecs",
    clock,
    { TERMINAL_WINDOW_MARKERS: ["xterm"] }
  );
  const isCodexTerminalTargetWindow = loadAppletMethod(
    "_isCodexTerminalTargetWindow",
    "_normalizeCodexTerminalSubmitKey",
    clock
  );
  const targetXWindowSnapshot = loadAppletMethod(
    "_targetXWindowSnapshot",
    "_targetXWindowMatchesSnapshot",
    clock
  );
  const copyAndMaybePaste = loadAppletMethod(
    "_copyAndMaybePasteTranscriptText",
    "_confirmClipboardOverwriteForPaste",
    clock
  );
  const pasteClipboardAfterFocus = loadAppletMethod(
    "_pasteClipboardAfterFocus",
    "_typeTextAfterFocus",
    clock
  );
  let cleanupPending = true;
  let clipboardWrites = 0;
  let pasteCalls = 0;
  let submitArgs = null;
  const originalTerminate = applet._terminateProcessesByGroup;
  applet._terminateProcessesByGroup = (group) => {
    if (cleanupPending && group === "status") {
      return "pending";
    }
    return originalTerminate.call(applet, group);
  };
  applet._processCleanupStillPending = () => cleanupPending;
  applet.targetWindow = targetWindow;
  applet.targetWindowGeneration = 0;
  applet.targetWindowXPendingGeneration = 0;
  applet.targetWindowWaylandEvidence = null;
  applet._startWithLanguage = startWithLanguage;
  applet._rememberFocusedWindow = rememberFocusedWindow;
  applet._primaryLanguage = () => "en";
  applet._normalizeLanguage = (value) => value;
  applet._captureWaylandTargetEvidence = captureWaylandTargetEvidence;
  applet._waylandTargetEvidenceForCurrentGeneration = waylandTargetEvidence;
  applet._cancelTextInsertForSettingsChange = cancelTextInsertForSettingsChange;
  applet._isWaylandSession = () => true;
  applet._isUsableTargetWindow = () => true;
  applet._windowLooksLikeSpeedOfCinnamon = () => false;
  applet._windowProbeValue = loadAppletMethod(
    "_windowProbeValue",
    "_windowLooksLikeSpeedOfCinnamon",
    clock
  );
  applet._windowIdentityValueMatchesMarker = loadAppletMethod(
    "_windowIdentityValueMatchesMarker",
    "_markerAllowsAutoPasteIdentity",
    clock
  );
  applet._clearTargetWindowXid = () => {};
  applet._codexTerminalSubmitKey = () => "F6";
  applet._isTerminalTargetWindow = isTerminalTargetWindow;
  applet._isCodexTerminalTargetWindow = isCodexTerminalTargetWindow;
  applet._targetXWindowSnapshot = targetXWindowSnapshot;
  applet._pasteClipboardAfterFocus = pasteClipboardAfterFocus;
  applet._preferredKeyboardProgram = () => ({ kind: "wtype", path: "/usr/bin/wtype" });
  applet._restoreTargetWindowForPaste = (callback) => {
    callback(true);
    return true;
  };
  applet._spawnKeyboardAfterFocus = (_args, followUpArgs, _text, _target, callback) => {
    pasteCalls += 1;
    submitArgs = followUpArgs;
    callback(true);
    return true;
  };
  applet._setClipboardText = () => {
    clipboardWrites += 1;
    return true;
  };
  applet._copyAndMaybePasteTranscriptText = copyAndMaybePaste;
  applet._closeMenuForKeyboardInsert = () => true;
  applet._completeKeyboardInsertFailure = () => {};
  applet._clearPasteTimer = () => true;
  applet._forgetAutoInsertFingerprint = () => true;

  assert.equal(applet._startWithLanguage("en"), true);
  assert.equal(harness.requests.length, 0);
  assert.equal(applet.recordingStartPendingAfterCleanup, true);
  const retryTimer = harness.scheduledTimers.find(
    (timer) => timer.propertyName === "recordingStartRetryTimer"
  );
  assert.ok(retryTimer);
  assert.equal(applet.targetWindowWaylandEvidence.title, "codex — project");

  targetWindow.title = "shell prompt";
  cleanupPending = false;
  retryTimer.callback();

  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "start");
  assert.equal(applet.targetWindowWaylandEvidence.targetWindow, targetWindow);
  assert.equal(applet.targetWindowWaylandEvidence.title, "codex — project");
  assert.equal(applet._isCodexTerminalTargetWindow(), true);
  assert.equal(
    copyAndMaybePaste.call(
      applet,
      "hello",
      "hello",
      "clipboard-paste-submit",
      true,
      true,
      () => {},
      () => true
    ),
    null
  );
  assert.equal(clipboardWrites, 1);
  assert.equal(pasteCalls, 1);
  assert.equal(submitArgs[2], "F6");
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
    _ensureAutoRelistenPendingForDonePayload: () => true,
    autoRelistenPendingToken: "relisten",
    autoRelistenPendingLanguage: "en",
    autoRelistenManualStopRequested: false,
    autoInsertConflictToken: null,
    lastTranscript: "",
    _isEmptyTranscriptText: () => false,
    _autoInsertFingerprint: () => "fingerprint",
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
  const trackedTimerClears = [];
  const insertCalls = [];
  const pollCalls = [];
  const relistenCalls = [];
  const transcribeCalls = [];
  const failedCleanupGroups = new Set(options.failedCleanupGroups || []);
  const pendingCleanupGroups = new Set(options.pendingCleanupGroups || []);

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
    autoRelistenRetryTimer: 0,

    ollamaModelFlowToken: null,
    ollamaInstallWatchToken: null,
    ollamaModelInstallToken: null,
    ollamaModelInstallRunning: false,
    ollamaModelCleanupFailed: false,
    _ollamaModelCleanupStatus: "stopped",

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
    _cancelPendingRecordingFocusStart: loadAppletMethod(
      "_cancelPendingRecordingFocusStart",
      "_populateLanguageMenu",
      clock
    ),
    _prepareAutoRelistenReservation: loadAppletMethod(
      "_prepareAutoRelistenReservation",
      "_finishPendingRelisten",
      clock
    ),
    _clearAutoRelistenRetryTimer: loadAppletMethod(
      "_clearAutoRelistenRetryTimer",
      "_transcriptDigest",
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
    _clearTrackedTimer(name, propertyName) {
      trackedTimerClears.push([name, propertyName]);
      this[propertyName] = 0;
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
        if (this._processCleanupStatus(this._terminateProcessesByGroup(group)) !== "stopped") {
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
    _recordingReachedConfiguredLimit() {
      return false;
    },
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

    _processCleanupStatus(result) {
      if (result === true || result === "stopped") {
        return "stopped";
      }
      if (result === "pending") {
        return "pending";
      }
      return "failed";
    },
    _processCleanupSucceeded(result) {
      return this._processCleanupStatus(result) === "stopped";
    },

    _terminateProcessesByGroup(group) {
      cleanupGroups.push(group);
      if (failedCleanupGroups.has(group)) {
        return "failed";
      }
      if (pendingCleanupGroups.has(group)) {
        return "pending";
      }
      return "stopped";
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
    trackedTimerClears,
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
  const targetWindow = {};
  applet.targetWindow = targetWindow;
  applet.targetWindowGeneration = 7;
  applet.targetWindowXPendingGeneration = 7;
  applet.targetWindowWaylandEvidence = {
    generation: 7,
    targetWindow,
    codex: true,
    submitKey: "F6",
  };

  assert.equal(applet._toggleRecording("start"), true);
  const pendingGeneration = applet.targetWindowGeneration;
  applet.targetWindowWaylandEvidence = {
    generation: pendingGeneration,
    targetWindow,
    codex: true,
    submitKey: "F6",
  };
  applet._cancelRecording();

  assert.equal(applet.status, "ready");
  assert.equal(applet.recordingStartPendingAfterCleanup, false);
  assert.equal(applet.recordingStartRetryTimer, 0);
  assert.equal(applet.targetWindowGeneration, pendingGeneration + 1);
  assert.equal(applet.targetWindowWaylandEvidence, null);
  assert.equal(applet.targetWindowXPendingGeneration, 0);
  assert.equal(harness.requests.length, 0);
  harness.scheduledTimers[0].callback();
  assert.equal(harness.requests.length, 0);
  assert.equal(harness.insertCalls.length, 0);
});

test("failed pending-start timer clear fails closed without a second invalidation", () => {
  const harness = makeRecordingApplet();
  const { applet } = harness;
  const targetWindow = {};
  let clearAttempts = 0;
  const clearRecordingStartRetryTimer = applet._clearRecordingStartRetryTimer;
  applet._clearRecordingStartRetryTimer = () => {
    clearAttempts += 1;
    if (clearAttempts === 1) {
      return false;
    }
    return clearRecordingStartRetryTimer.call(applet);
  };
  applet.targetWindow = targetWindow;
  applet.targetWindowGeneration = 9;
  applet.targetWindowXPendingGeneration = 9;

  assert.equal(applet._queueRecordingStartAfterCleanup(), true);
  applet.targetWindowWaylandEvidence = {
    generation: 9,
    targetWindow,
    codex: true,
    submitKey: "F6",
  };
  const retryTimer = harness.scheduledTimers[0];
  const generationBeforeCancel = applet.targetWindowGeneration;

  assert.equal(applet._toggleRecording("stop"), false);
  assert.equal(applet.recordingStartPendingAfterCleanup, false);
  assert.notEqual(applet.recordingStartRetryTimer, 0);
  assert.equal(applet.targetWindowGeneration, generationBeforeCancel + 1);
  assert.equal(applet.targetWindowWaylandEvidence, null);
  assert.equal(applet.targetWindowXPendingGeneration, 0);
  assert.equal(applet.status, "error");

  retryTimer.callback();
  assert.equal(harness.requests.length, 0);
  assert.equal(harness.insertCalls.length, 0);

  assert.equal(applet._cancelRecording(), undefined);
  assert.equal(clearAttempts, 2);
  assert.equal(applet.recordingStartRetryTimer, 0);
  assert.equal(applet.targetWindowGeneration, generationBeforeCancel + 1);
});

test("pending retry never rebinds evidence from a replaced target window", () => {
  const clock = { value: 0 };
  const harness = makeRecordingApplet();
  const applet = harness.applet;
  const oldWindow = {};
  const newWindow = {};
  const waylandTargetEvidence = loadAppletMethod(
    "_waylandTargetEvidenceForCurrentGeneration",
    "_captureWaylandTargetEvidence",
    clock
  );
  const cancelTextInsertForSettingsChange = loadAppletMethod(
    "_cancelTextInsertForSettingsChange",
    "on_applet_clicked",
    clock
  );
  const isCodexTerminalTargetWindow = loadAppletMethod(
    "_isCodexTerminalTargetWindow",
    "_normalizeCodexTerminalSubmitKey",
    clock
  );

  applet._isWaylandSession = () => true;
  applet._isUsableTargetWindow = () => true;
  applet._waylandTargetEvidenceForCurrentGeneration = waylandTargetEvidence;
  applet._cancelTextInsertForSettingsChange = cancelTextInsertForSettingsChange;
  applet._isCodexTerminalTargetWindow = isCodexTerminalTargetWindow;
  applet._clearPasteTimer = () => true;
  applet._forgetAutoInsertFingerprint = () => true;
  applet.targetWindow = oldWindow;
  applet.targetWindowGeneration = 4;
  applet.targetWindowWaylandEvidence = {
    generation: 4,
    targetWindow: oldWindow,
    codex: true,
    submitKey: "F6",
  };

  assert.equal(applet._queueRecordingStartAfterCleanup(), true);
  const retryTimer = harness.scheduledTimers[0];
  applet.targetWindow = newWindow;
  retryTimer.callback();

  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "start");
  assert.equal(applet.targetWindowWaylandEvidence, null);
  assert.equal(applet._isCodexTerminalTargetWindow(), false);
  assert.equal(applet.targetWindow, newWindow);
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

test("parseSpawnOutput enforces exact backend status and error contracts", () => {
  const clock = { value: 0 };
  const parseSpawnOutput = loadAppletMethod(
    "_parseSpawnOutput",
    "_argValue",
    clock,
    { BACKEND_PAYLOAD_STATUSES: backendPayloadStatuses }
  );
  const normalizePayloadStatus = loadAppletMethod(
    "_normalizePayloadStatus",
    "_hotkeyName",
    clock,
    {
      PAYLOAD_STATUSES: [
        "idle",
        "recording",
        "recorded",
        "processing",
        "done",
        "error",
        "setup",
      ],
    }
  );
  const receiver = { _normalizePayloadStatus: normalizePayloadStatus };
  const normalized = (value) => JSON.parse(JSON.stringify(value));
  const invalidResponse = {
    status: "error",
    error: "Invalid backend response",
    transport_error: true,
  };

  assert.deepEqual(
    [...backendPayloadStatuses].sort(),
    [...pythonPublicPayloadStatuses].sort()
  );

  for (const status of backendPayloadStatuses) {
    const raw = {
      status,
      marker: "kept",
      ...(status === "error" ? { error: "bounded failure" } : {}),
    };
    const parsed = parseSpawnOutput.call(receiver, JSON.stringify(raw));
    assert.deepEqual(normalized(parsed), raw);
  }

  for (const raw of [
    {},
    { status: null, configured: { recorder: { ok: true } } },
    { status: 1, configured: { recorder: { ok: true } } },
    { status: "future-status", configured: { recorder: { ok: true } } },
  ]) {
    assert.deepEqual(
      normalized(parseSpawnOutput.call(receiver, JSON.stringify(raw))),
      invalidResponse
    );
  }
  for (const error of [undefined, "", "   ", 1, null]) {
    const raw = { status: "error" };
    if (error !== undefined) {
      raw.error = error;
    }
    assert.deepEqual(
      normalized(parseSpawnOutput.call(receiver, JSON.stringify(raw))),
      invalidResponse
    );
  }
  assert.deepEqual(
    normalized(parseSpawnOutput.call(
      receiver,
      JSON.stringify({
        status: "error",
        message: "bounded failure",
        exit_code: 0,
      })
    )),
    {
      status: "error",
      message: "bounded failure",
      error: "bounded failure",
    }
  );
  assert.deepEqual(
    normalized(parseSpawnOutput.call(
      receiver,
      JSON.stringify({
        status: "error",
        error: " ",
        message: "must not rescue invalid error",
      })
    )),
    invalidResponse
  );
  for (const exitCode of [0, 1, "1", null]) {
    const parsed = parseSpawnOutput.call(
      receiver,
      JSON.stringify({
        status: "done",
        error: "bounded failure",
        exit_code: exitCode,
      })
    );
    assert.equal(parsed.status, "done");
    assert.equal(parsed.error, "bounded failure");
    assert.equal(Object.hasOwn(parsed, "exit_code"), false);
  }
});

test("parseSpawnOutput rejects duplicate raw stdout keys before JSON.parse", () => {
  const clock = { value: 0 };
  let parseCalls = 0;
  const parsingJSON = {
    parse(raw) {
      parseCalls += 1;
      return JSON.parse(raw);
    },
  };
  const parseSpawnOutput = loadAppletMethod(
    "_parseSpawnOutput",
    "_argValue",
    clock,
    { BACKEND_PAYLOAD_STATUSES: backendPayloadStatuses, JSON: parsingJSON }
  );
  const invalidResponse = {
    status: "error",
    error: "Invalid backend response",
    transport_error: true,
  };
  for (const raw of [
    '{"status":"done","status":"error"}',
    '{"status":"done","nested":{"name":1,"name":2}}',
    '{"status":"done","nested":{"\\u006eame":1,"name":2}}',
  ]) {
    const callsBefore = parseCalls;
    assert.deepEqual(
      JSON.parse(JSON.stringify(parseSpawnOutput.call({}, raw))),
      invalidResponse
    );
    assert.equal(parseCalls, callsBefore);
  }
  const valid = parseSpawnOutput.call(
    {},
    '{"status":"done","nested":{"\\u006eame":1},"text":"{\\"name\\":1,\\"name\\":2}"}'
  );
  assert.equal(parseCalls, 1);
  assert.equal(valid.status, "done");
  assert.equal(valid.nested.name, 1);
});

test("parseSpawnOutput ignores inherited controls and rejects prototype keys", () => {
  const clock = { value: 0 };
  const parseSpawnOutput = loadAppletMethod(
    "_parseSpawnOutput",
    "_argValue",
    clock,
    { BACKEND_PAYLOAD_STATUSES: backendPayloadStatuses, JSON, Object }
  );
  const invalidResponse = {
    status: "error",
    error: "Invalid backend response",
    transport_error: true,
  };
  const propertyValues = {
    status: "done",
    error: "inherited failure",
    exit_code: 0,
    configured: { recorder: { ok: true } },
  };
  const originalDescriptors = new Map(
    Object.keys(propertyValues).map((name) => [
      name,
      Object.getOwnPropertyDescriptor(Object.prototype, name),
    ])
  );

  try {
    for (const [name, value] of Object.entries(propertyValues)) {
      Object.defineProperty(Object.prototype, name, {
        configurable: true,
        enumerable: false,
        value,
        writable: true,
      });
    }
    assert.deepEqual(
      JSON.parse(JSON.stringify(parseSpawnOutput.call({}, "{}"))),
      invalidResponse
    );
    const parsed = parseSpawnOutput.call(
      {},
      '{"status":"done","exit_code":0}'
    );
    assert.equal(Object.getPrototypeOf(parsed), null);
    assert.equal(parsed.status, "done");
    assert.equal(parsed.exit_code, 0);
    assert.equal(Object.hasOwn(parsed, "error"), false);
    assert.equal(Object.hasOwn(parsed, "configured"), false);
    for (const hostileKey of ["__proto__", "constructor", "prototype"]) {
      const raw = `{"status":"done","${hostileKey}":{"status":"error"}}`;
      assert.deepEqual(
        JSON.parse(JSON.stringify(parseSpawnOutput.call({}, raw))),
        invalidResponse
      );
    }
  } finally {
    for (const [name, descriptor] of originalDescriptors) {
      if (descriptor) {
        Object.defineProperty(Object.prototype, name, descriptor);
      } else {
        delete Object.prototype[name];
      }
    }
  }
});

test("spawnJson forwards only domain errors after nonzero exit", () => {
  const clock = { value: 0 };
  const spawnJson = loadAppletMethod("_spawnJson", "_spawnText", clock, {
    CLI_COMMAND_TIMEOUT_MS: 1000,
    MAX_SPAWN_JSON_BYTES: 4096,
    MAX_SPAWN_STDERR_BYTES: 1024,
  });
  const guardStateCallback = loadAppletMethod(
    "_guardStateCallback",
    "_handleInitializationFailure",
    clock
  );
  const runStateGuarded = loadAppletMethod(
    "_runStateGuarded",
    "_runTeardownGuarded",
    clock
  );
  const parseSpawnOutput = loadAppletMethod(
    "_parseSpawnOutput",
    "_argValue",
    clock,
    { BACKEND_PAYLOAD_STATUSES: backendPayloadStatuses }
  );
  const normalizePayloadStatus = loadAppletMethod(
    "_normalizePayloadStatus",
    "_hotkeyName",
    clock,
    { PAYLOAD_STATUSES: backendPayloadStatuses }
  );

  const cases = [
    {
      raw: {
        status: "future-status",
        configured: { recorder: { ok: true } },
      },
      success: {
        status: "error",
        error: "Invalid backend response",
        transport_error: true,
      },
      nonzero: {
        status: "error",
        error: "Backend command failed",
        transport_error: true,
      },
    },
    {
      raw: { status: "done", configured: { recorder: { ok: true } } },
      success: { status: "done", configured: { recorder: { ok: true } } },
      nonzero: {
        status: "error",
        error: "Backend command failed",
        transport_error: true,
      },
    },
    {
      raw: { status: "error" },
      success: {
        status: "error",
        error: "Invalid backend response",
        transport_error: true,
      },
      nonzero: {
        status: "error",
        error: "Backend command failed",
        transport_error: true,
      },
    },
    {
      rawOutput: '{"status":"done","nested":{"\\u006eame":1,"name":2}}',
      success: {
        status: "error",
        error: "Invalid backend response",
        transport_error: true,
      },
      nonzero: {
        status: "error",
        error: "Backend command failed",
        transport_error: true,
      },
    },
    {
      raw: {
        status: "error",
        error: "bounded domain failure",
        exit_code: 0,
        cleanup_pending: true,
      },
      success: {
        status: "error",
        error: "bounded domain failure",
        cleanup_pending: true,
      },
      nonzero: {
        status: "error",
        error: "bounded domain failure",
        cleanup_pending: true,
      },
    },
    {
      raw: {
        status: "recording",
        error: "recording cleanup remains pending",
        recording_artifacts_present: true,
      },
      success: {
        status: "recording",
        error: "recording cleanup remains pending",
        recording_artifacts_present: true,
      },
      nonzero: {
        status: "recording",
        error: "recording cleanup remains pending",
        recording_artifacts_present: true,
      },
    },
    {
      raw: {
        status: "finalizing",
        error: "finalization cleanup remains pending",
        cleanup_pending: true,
      },
      success: {
        status: "finalizing",
        error: "finalization cleanup remains pending",
        cleanup_pending: true,
      },
      nonzero: {
        status: "finalizing",
        error: "finalization cleanup remains pending",
        cleanup_pending: true,
      },
    },
  ];
  for (const entry of cases) {
    for (const backendResult of [
      {},
      { error: "backend exited nonzero", exitCode: 1 },
    ]) {
      let received = null;
      const applet = {
      _statusRefreshToken: 0,
      _guardStateCallback: guardStateCallback,
      _runStateGuarded: runStateGuarded,
      _lifecycleAllowsWork: () => true,
      _coerceSpawnArgs: (args) => args,
      _isStatusCommandArgs: () => false,
      _shouldExposeOpenAiCompatibleApiKeyToBackend: () => false,
      _runWithBackendEnvironment(_includeKey, callback) {
        return callback({});
      },
      _spawnJsonWithBackendEnvironment(_args, _env, callback) {
        callback(entry.rawOutput || JSON.stringify(entry.raw), backendResult, "");
        return { pid: 1 };
      },
      _parseSpawnOutput: parseSpawnOutput,
      _normalizePayloadStatus: normalizePayloadStatus,
      _reportSubprocessError() {
        throw new Error("invalid payload escaped parser boundary");
      },
      _recordLifecycleError() {},
      };

      spawnJson.call(applet, ["/trusted/soc", "doctor", "--json"], (payload) => {
        received = JSON.parse(JSON.stringify(payload));
      });

      assert.deepEqual(
        received,
        backendResult.error ? entry.nonzero : entry.success
      );
    }
  }
});

test("doctor caller cannot accept configured data with invalid backend status", () => {
  const clock = { value: 0 };
  const parseSpawnOutput = loadAppletMethod(
    "_parseSpawnOutput",
    "_argValue",
    clock,
    { BACKEND_PAYLOAD_STATUSES: backendPayloadStatuses }
  );
  const normalizePayloadStatus = loadAppletMethod(
    "_normalizePayloadStatus",
    "_hotkeyName",
    clock,
    { PAYLOAD_STATUSES: backendPayloadStatuses }
  );
  const runDoctor = loadAppletMethod(
    "_runDoctor",
    "_applyDoctorPayload",
    clock,
    { DOCTOR_COMMAND_TIMEOUT_MS: 1000 }
  );
  let appliedDoctor = 0;
  const statusEvents = [];
  const invalidPayload = makeDoctorPayload();
  invalidPayload.status = "future-status";
  const applet = {
    status: "idle",
    lastTranscript: "",
    isCommandRunning: false,
    _statusCommandRunning: false,
    alarmCheckToken: null,
    alarmActionToken: null,
    alarmMenuRefreshToken: null,
    doctorCommandToken: null,
    _doctorCommandRunning: false,
    _hasActiveRecordingState: () => false,
    _hasLocalProcessingWorkflow: () => false,
    _settingsSnapshotInputOptionOrNull: () => ({ inputText: null }),
    _doctorArgs: () => ["/trusted/soc", "doctor", "--json"],
    _lifecycleAllowsWork: () => true,
    _sanitizeErrorMessage: (value) => String(value),
    _setDoctorSummary() {},
    _setStatus(status, message) {
      statusEvents.push([status, message]);
    },
    _presentDoctorResult() {},
    _recordLifecycleError() {},
    _applyDoctorPayload() {
      appliedDoctor += 1;
    },
    _spawnJson(_args, callback) {
      callback(
        parseSpawnOutput.call(
          { _normalizePayloadStatus: normalizePayloadStatus },
          JSON.stringify(invalidPayload)
        )
      );
      return { pid: 1 };
    },
  };

  runDoctor.call(applet, true);

  assert.equal(appliedDoctor, 0);
  assert.deepEqual(statusEvents, [
    ["setup", "Doctor failed: Invalid backend response"],
  ]);
});

test("doctor command arguments bind applet report mode", () => {
  const doctorArgs = loadAppletMethod(
    "_doctorArgs",
    "_setupArgs",
    { value: 0 }
  );

  assert.deepEqual(
    Array.from(doctorArgs.call({ _cliCommand: () => "/trusted/soc" })),
    [
      "/trusted/soc",
      "doctor",
      "--applet",
      "--settings-json-stdin",
      "--json",
    ]
  );
});

test("doctor caller requires exact done schema before readiness", () => {
  const clock = { value: 0 };
  const validatedDoctorPayload = loadAppletMethod(
    "_validatedDoctorPayload",
    "_runDoctor",
    clock
  );
  const runDoctor = loadAppletMethod(
    "_runDoctor",
    "_applyDoctorPayload",
    clock,
    { DOCTOR_COMMAND_TIMEOUT_MS: 1000 }
  );
  const applyDoctorPayload = loadAppletMethod(
    "_applyDoctorPayload",
    "_presentDoctorResult",
    clock
  );
  const doctorSummary = loadAppletMethod(
    "_doctorSummary",
    "_doctorSectionText",
    clock
  );
  const doctorSectionText = loadAppletMethod(
    "_doctorSectionText",
    "_openAppletSettings",
    clock
  );
  const cases = [
    {
      payload: makeDoctorPayload(),
      expectedStatus: "ready",
      expectedText: "Doctor: ready",
    },
    {
      insertMethod: "clipboard-paste",
      mutate(payload) {
        payload.configured.output.paste_ok = false;
      },
      expectedStatus: "warning",
      expectedText: "automatic paste is unavailable",
    },
    {
      insertMethod: "clipboard-paste-submit",
      mutate(payload) {
        payload.configured.output.paste_ok = false;
      },
      expectedStatus: "warning",
      expectedText: "automatic paste is unavailable",
    },
    {
      mutate(payload) {
        payload.ok = false;
        payload.configured.recorder.ok = false;
        payload.configured.recorder.detail = "recorder not ready";
      },
      expectedStatus: "setup",
      expectedText: "recorder: recorder not ready",
    },
    {
      mutate(payload) {
        payload.ok = false;
        payload.checks[0].ok = false;
        payload.checks[0].detail = "python3 missing";
      },
      expectedStatus: "setup",
      expectedText: "python3: python3 missing",
    },
    {
      mutate(payload) {
        payload.ok = false;
        payload.desktop.cinnamon = false;
      },
      expectedStatus: "setup",
      expectedText: "desktop: Cinnamon unavailable",
    },
    {
      mutate(payload) {
        payload.ok = false;
      },
      expectedStatus: "setup",
      expectedText: "requirements not ready",
    },
    {
      mutate(payload) {
        delete payload.schema_version;
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      mutate(payload) {
        payload.applet = false;
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      mutate(payload) {
        payload.checks[0].secret_token = "/private/doctor-secret";
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      mutate(payload) {
        payload.configured.recorder.secret_token = "/private/doctor-secret";
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      mutate(payload) {
        delete payload.configured.output.paste_ok;
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      mutate(payload) {
        payload.configured.output.paste_ok = "false";
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      mutate(payload) {
        payload.status = "recording";
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    },
    {
      payload: {
        status: "error",
        error: "Backend command failed",
        transport_error: true,
      },
      expectedStatus: "setup",
      expectedText: "Backend command failed",
    },
  ];
  for (const [field, value] of [
    ["ok", 1],
    ["checks", {}],
    ["desktop", []],
    ["configured", []],
    ["warnings", {}],
    ["applet", 1],
  ]) {
    cases.push({
      mutate(payload) {
        payload[field] = value;
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    });
  }
  for (const [field, value] of [
    ["warnings", ["  "]],
  ]) {
    cases.push({
      mutate(payload) {
        payload[field] = value;
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    });
  }
  for (const [field, value] of [
    ["exit_code", 0],
    ["unknown", "/private/doctor-secret"],
  ]) {
    cases.push({
      mutate(payload) {
        payload[field] = value;
      },
      expectedStatus: "setup",
      expectedText: "Invalid backend response",
    });
  }

  for (const testCase of cases) {
    const payload = testCase.payload || makeDoctorPayload();
    if (testCase.mutate) {
      testCase.mutate(payload);
    }
    const statusEvents = [];
    const applet = {
      status: "idle",
      lastTranscript: "",
      isCommandRunning: false,
      _statusCommandRunning: false,
      alarmCheckToken: null,
      alarmActionToken: null,
      alarmMenuRefreshToken: null,
      doctorCommandToken: null,
      _doctorCommandRunning: false,
      insertMethod: testCase.insertMethod || "clipboard-paste-submit",
      _hasActiveRecordingState: () => false,
      _hasLocalProcessingWorkflow: () => false,
      _settingsSnapshotInputOptionOrNull: () => ({ inputText: null }),
      _doctorArgs: () => ["/trusted/soc", "doctor", "--json"],
      _lifecycleAllowsWork: () => true,
      _sanitizeErrorMessage: (value) => String(value),
      _setDoctorSummary() {},
      _setStatus(status, message) {
        statusEvents.push([status, message]);
      },
      _presentDoctorResult() {},
      _recordLifecycleError() {},
      _validatedDoctorPayload: validatedDoctorPayload,
      _applyDoctorPayload: applyDoctorPayload,
      _doctorSummary: doctorSummary,
      _doctorSectionText: doctorSectionText,
      _spawnJson(_args, callback) {
        callback(payload);
        return { pid: 1 };
      },
    };

    runDoctor.call(applet, true);

    assert.equal(statusEvents.at(-1)[0], testCase.expectedStatus);
    assert.match(statusEvents.at(-1)[1], new RegExp(testCase.expectedText));
    assert.equal(statusEvents.at(-1)[1].includes("/private/doctor-secret"), false);
    assert.notEqual(statusEvents.at(-1)[1], "Doctor: Setup needed: ");
  }
});

test("nonzero cancel transport preserves domain error and continues recovery poll", () => {
  const clock = { value: 0 };
  const spawnJson = loadAppletMethod("_spawnJson", "_spawnText", clock, {
    CLI_COMMAND_TIMEOUT_MS: 1000,
    MAX_SPAWN_JSON_BYTES: 4096,
    MAX_SPAWN_STDERR_BYTES: 1024,
  });
  const guardStateCallback = loadAppletMethod(
    "_guardStateCallback",
    "_handleInitializationFailure",
    clock
  );
  const runStateGuarded = loadAppletMethod(
    "_runStateGuarded",
    "_runTeardownGuarded",
    clock
  );
  const parseSpawnOutput = loadAppletMethod(
    "_parseSpawnOutput",
    "_argValue",
    clock,
    { BACKEND_PAYLOAD_STATUSES: backendPayloadStatuses }
  );
  const normalizePayloadStatus = loadAppletMethod(
    "_normalizePayloadStatus",
    "_hotkeyName",
    clock,
    {
      PAYLOAD_STATUSES: [
        "idle",
        "recording",
        "recorded",
        "processing",
        "done",
        "error",
        "setup",
      ],
    }
  );
  const domainPayload = {
    status: "recording",
    error: "recording cleanup remains pending",
    exit_code: 0,
    audio_deleted: false,
    log_deleted: false,
    inflight_artifacts_deleted: false,
    cleanup_backups_deleted: false,
    transcript_deleted: false,
  };
  const expectedPayload = { ...domainPayload };
  delete expectedPayload.exit_code;

  for (const backendResult of [
    {},
    { error: "backend exited nonzero", exitCode: 1 },
  ]) {
    const transportedPayloads = [];
    const harness = makeRecordingApplet({ realStopPayload: true });
    const { applet } = harness;
    const applyPayloadSafely = applet._applyPayloadSafely;

    applet._spawnJson = spawnJson;
    applet._guardStateCallback = guardStateCallback;
    applet._runStateGuarded = runStateGuarded;
    applet._coerceSpawnArgs = (args) => args;
    applet._isStatusCommandArgs = () => false;
    applet._shouldExposeOpenAiCompatibleApiKeyToBackend = () => false;
    applet._runWithBackendEnvironment = (_includeKey, callback) => callback({});
    applet._spawnJsonWithBackendEnvironment = (_args, _env, callback) => {
      callback(JSON.stringify(domainPayload), backendResult, "");
      return { pid: 1 };
    };
    applet._parseSpawnOutput = parseSpawnOutput;
    applet._normalizePayloadStatus = normalizePayloadStatus;
    applet._reportSubprocessError = () => {
      throw new Error("valid domain payload became a transport error");
    };
    applet._lifecycleErrorText = () => "redacted transport error";
    applet._applyPayloadSafely = function(payload, ...args) {
      transportedPayloads.push(payload);
      return applyPayloadSafely.call(this, payload, ...args);
    };
    applet.status = "recording";
    applet.recordingArtifactsPresent = true;

    applet._cancelRecording();

    assert.deepEqual(
      JSON.parse(JSON.stringify(transportedPayloads)),
      [expectedPayload]
    );
    assert.equal(harness.preservedStatusEvents.length, 1);
    assert.equal(harness.preservedStatusEvents[0].status, "error");
    assert.equal(
      harness.preservedStatusEvents[0].message,
      domainPayload.error
    );
    assert.equal(harness.pollCalls.length, 1);
    assert.equal(applet.cancelIntentActive, true);
    assert.equal(applet.cancelRecoveryAttempts, 1);
  }
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

test("timed auto-transcribe replaces stale relisten retry and retries one real done path", () => {
  const harness = makeRecordingApplet({
    realTimedAutoTranscribe: true,
    realStopPayload: true,
  });
  const applet = harness.applet;
  applet._finishPendingRelisten = loadAppletMethod(
    "_finishPendingRelisten",
    "_transcriptDigest",
    { value: 1000 }
  );
  applet._finishEmptyRelistenDone = loadAppletMethod(
    "_finishEmptyRelistenDone",
    "_insertTranscriptText",
    { value: 1000 }
  );
  applet._ensureAutoRelistenPendingForDonePayload = loadAppletMethod(
    "_ensureAutoRelistenPendingForDonePayload",
    "_finishPendingRelisten",
    { value: 1000 }
  );
  applet._schedulePendingAutoRelistenRetry = loadAppletMethod(
    "_schedulePendingAutoRelistenRetry",
    "_clearAutoRelistenRetryTimer",
    { value: 1000 }
  );
  applet._maybeWarnAutomaticBackup = () => {};
  applet._maybeStartAutoBackup = () => {};
  applet.autoRelisten = true;
  applet.autoTranscribeTimeout = true;
  applet.notificationSessionActive = true;
  applet.autoRelistenSequence = 0;
  applet.autoRelistenRetryTimer = 41;

  let relistenAttempts = 0;
  let successfulRelistenStarts = 0;
  applet._restartRelistenRecording = function() {
    relistenAttempts += 1;
    if (relistenAttempts === 1) {
      this._autoRelistenStartBlock = "busy";
      return false;
    }
    successfulRelistenStarts += 1;
    return true;
  };

  applet._maybeAutoTranscribeRecorded(
    { status: "recorded", audio_path: "/tmp/timed-relisten.flac" },
    "recorded"
  );

  assert.deepEqual(harness.trackedTimerClears, [[
    "auto-relisten-retry",
    "autoRelistenRetryTimer",
  ]]);
  assert.equal(applet.autoRelistenRetryTimer, 0);
  assert.equal(applet.autoRelistenPending, true);
  assert.equal(applet.autoRelistenPendingToken, "1:/tmp/timed-relisten.flac");
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.requests[0].args[0], "stop");

  harness.requests[0].callback({ status: "done" });

  assert.equal(relistenAttempts, 1);
  assert.equal(successfulRelistenStarts, 0);
  assert.equal(applet.autoRelistenPending, true);
  assert.equal(harness.scheduledTimers.length, 1);
  assert.equal(harness.scheduledTimers[0].name, "auto-relisten-retry");
  assert.equal(applet.autoRelistenRetryTimer, 1);

  const retryTimer = harness.scheduledTimers[0];
  applet.isCommandRunning = true;
  assert.equal(retryTimer.callback(), true);
  assert.equal(harness.scheduledTimers.length, 1);
  assert.equal(relistenAttempts, 1);

  applet.isCommandRunning = false;
  assert.equal(retryTimer.callback(), false);
  assert.equal(relistenAttempts, 2);
  assert.equal(successfulRelistenStarts, 1);
  assert.equal(harness.requests.length, 1);
  assert.equal(harness.trackedTimerClears.length, 1);
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

test("process cleanup returns pending before eventual stopped", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const identity = { pid: "321", startTime: "654" };
  const process = {};
  const entry = {
    group: "status",
    process,
    processGroupIdentity: identity,
    cancel: () => false,
  };
  const processes = { status: entry };
  let groupState = "live";
  const scheduled = [];
  const applet = {
    _resourceRegistry: { processes },
    _orphanedProcesses: [],
    _processGroupState: () => groupState,
    _processHandleIsStopped: () => groupState === "stopped",
    _findTrackedProcessGroupIdentity: () => identity,
    _trackOrphanedProcess: () => true,
    _unregisterProcess: (token) => {
      delete processes[token];
      return true;
    },
    _untrackOrphanedProcess: () => true,
    _retryOrphanedProcesses: () => true,
    _processCleanupStillPending: () => false,
    _scheduleProcessCleanupRetry: () => {
      scheduled.push(true);
      return true;
    },
    _recordLifecycleError: () => {},
  };

  assert.equal(terminateProcessesByGroup.call(applet, "status"), "pending");
  assert.equal(scheduled.length, 1);
  groupState = "stopped";
  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(Object.keys(processes).length, 0);
});

test("foreign orphan retry scheduling failure does not fail stopped group", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const lifecycleErrors = [];
  const applet = {
    _resourceRegistry: { processes: {} },
    _orphanedProcesses: [{ group: "foreign", process: {} }],
    _retryOrphanedProcesses: () => true,
    _processCleanupStillPending: () => true,
    _scheduleProcessCleanupRetry: () => false,
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(lifecycleErrors.length, 1);
  assert.equal(lifecycleErrors[0][0], "process-cleanup-retry");
  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(lifecycleErrors.length, 1);
});

test("pending process cancellation is orphan-tracked and eventually unregisters", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const retryOrphanedProcesses = loadAppletMethod(
    "_retryOrphanedProcesses",
    "_processCleanupStillPending",
    { value: 0 },
    { LIFECYCLE_REMOVING: "removing", LIFECYCLE_REMOVED: "removed" }
  );
  const trackOrphanedProcess = loadAppletMethod(
    "_trackOrphanedProcess",
    "_untrackOrphanedProcess",
    { value: 0 }
  );
  const untrackOrphanedProcess = loadAppletMethod(
    "_untrackOrphanedProcess",
    "_retryOrphanedProcesses",
    { value: 0 }
  );
  const unregisterProcess = loadAppletMethod(
    "_unregisterProcess",
    "_trackOrphanedProcess",
    { value: 0 }
  );
  const process = {};
  const processes = {
    "process-1": {
      process,
      generation: 1,
      group: "status",
      cancel: () => "pending",
    },
  };
  const orphanedProcesses = [];
  const lifecycleErrors = [];
  let terminationAttempts = 0;
  const applet = {
    appletRemoved: false,
    lifecycleState: "running",
    _resourceRegistry: { processes },
    _orphanedProcesses: orphanedProcesses,
    _trackOrphanedProcess: trackOrphanedProcess,
    _retryOrphanedProcesses: retryOrphanedProcesses,
    _untrackOrphanedProcess: untrackOrphanedProcess,
    _unregisterProcess: unregisterProcess,
    _readProcessGroupIdentity: () => null,
    _terminateProcess: () => {
      terminationAttempts += 1;
      return terminationAttempts >= 2;
    },
    _processCleanupStillPending: () => orphanedProcesses.length > 0,
    _scheduleProcessCleanupRetry: () => true,
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
    _reportSubprocessError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(
    terminateProcessesByGroup.call(applet, "status"),
    "pending",
    JSON.stringify({ orphanedProcesses, processes, lifecycleErrors, terminationAttempts })
  );
  assert.equal(orphanedProcesses.length, 1);
  assert.equal(orphanedProcesses[0].process, process);
  assert.equal(Object.prototype.hasOwnProperty.call(processes, "process-1"), true);

  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(orphanedProcesses.length, 0);
  assert.equal(Object.keys(processes).length, 0);
  assert.equal(lifecycleErrors.length, 0);
});

test("live no-cancel process is orphan-tracked before normal retry", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const retryOrphanedProcesses = loadAppletMethod(
    "_retryOrphanedProcesses",
    "_processCleanupStillPending",
    { value: 0 },
    { LIFECYCLE_REMOVING: "removing", LIFECYCLE_REMOVED: "removed" }
  );
  const trackOrphanedProcess = loadAppletMethod(
    "_trackOrphanedProcess",
    "_untrackOrphanedProcess",
    { value: 0 }
  );
  const untrackOrphanedProcess = loadAppletMethod(
    "_untrackOrphanedProcess",
    "_retryOrphanedProcesses",
    { value: 0 }
  );
  const unregisterProcess = loadAppletMethod(
    "_unregisterProcess",
    "_trackOrphanedProcess",
    { value: 0 }
  );
  const process = {};
  const processes = {
    "process-1": {
      process,
      generation: 1,
      group: "status",
      processGroupIdentity: { pid: "41", startTime: "9" },
    },
  };
  const orphanedProcesses = [];
  const lifecycleErrors = [];
  let terminationAttempts = 0;
  const applet = {
    appletRemoved: false,
    lifecycleState: "running",
    _resourceRegistry: { processes },
    _orphanedProcesses: orphanedProcesses,
    _trackOrphanedProcess: trackOrphanedProcess,
    _retryOrphanedProcesses: retryOrphanedProcesses,
    _untrackOrphanedProcess: untrackOrphanedProcess,
    _unregisterProcess: unregisterProcess,
    _processGroupState: () => "live",
    _terminateProcess: () => {
      terminationAttempts += 1;
      return terminationAttempts >= 3;
    },
    _processCleanupStillPending: () => orphanedProcesses.length > 0,
    _scheduleProcessCleanupRetry: () => true,
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
    _reportSubprocessError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(terminateProcessesByGroup.call(applet, "status"), "pending");
  assert.equal(orphanedProcesses.length, 1);
  assert.equal(Object.prototype.hasOwnProperty.call(processes, "process-1"), true);
  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(orphanedProcesses.length, 0);
  assert.equal(Object.keys(processes).length, 0);
  assert.equal(lifecycleErrors.length, 0);
});

test("process cleanup retry rejects stale timer ownership without numeric removal", () => {
  const scheduleProcessCleanupRetry = loadAppletMethod(
    "_scheduleProcessCleanupRetry",
    "_cancelPendingRecordingStart",
    { value: 0 }
  );
  const clearTrackedTimer = loadAppletMethod(
    "_clearTrackedTimer",
    "_scheduleTrackedTimer",
    { value: 0 },
    { Mainloop: { source_remove: () => { throw new Error("source_remove must not run"); } } }
  );
  const trackedTimerOwnedBy = loadAppletMethod(
    "_trackedTimerOwnedBy",
    "_untrackTimer",
    { value: 0 }
  );
  const scheduled = [];
  const lifecycleErrors = [];
  const propertyOnlyApplet = {
    _lifecycleAllowsWork: () => true,
    _processCleanupStillPending: () => true,
    processCleanupRetryTimer: 17,
    _resourceRegistry: { timers: {} },
    _orphanedTimers: [],
    _scheduleTrackedTimer(name, delay, callback, useSeconds, propertyName) {
      scheduled.push({ name, delay, callback, useSeconds, propertyName });
      this[propertyName] = 23;
      this._resourceRegistry.timers[name] = 23;
      return 23;
    },
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };
  assert.equal(scheduleProcessCleanupRetry.call(propertyOnlyApplet), true);
  assert.equal(scheduled.length, 1);
  assert.equal(propertyOnlyApplet.processCleanupRetryTimer, 23);

  const mismatchedApplet = {
    _lifecycleAllowsWork: () => true,
    _processCleanupStillPending: () => true,
    processCleanupRetryTimer: 17,
    _resourceRegistry: { timers: { "process-cleanup-retry": 23 } },
    _orphanedTimers: [],
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
    _scheduleTrackedTimer: () => { throw new Error("new timer must not bypass ambiguity"); },
  };
  assert.equal(scheduleProcessCleanupRetry.call(mismatchedApplet), false);
  assert.equal(scheduled.length, 1);

  const currentOwner = {};
  const staleOwner = {};
  const sameIdApplet = {
    _lifecycleAllowsWork: () => true,
    _processCleanupStillPending: () => true,
    processCleanupRetryTimer: 23,
    processCleanupRetryTimerOwner: staleOwner,
    _resourceRegistry: {
      timers: { "process-cleanup-retry": 23, foreign: 23 },
      timerOwners: { "process-cleanup-retry": currentOwner },
    },
    _orphanedTimers: [],
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
    _scheduleTrackedTimer: () => { throw new Error("same-ID stale timer must not schedule through"); },
  };
  assert.equal(scheduleProcessCleanupRetry.call(sameIdApplet), false);
  assert.equal(scheduled.length, 1);

  const sourceReuseApplet = {
    _resourceRegistry: { timers: { stale: 23 } },
    staleTimer: 17,
    _orphanedTimers: [],
    _reportSubprocessError: (...args) => lifecycleErrors.push(args),
  };
  assert.equal(clearTrackedTimer.call(sourceReuseApplet, "stale", "staleTimer"), false);
  assert.equal(lifecycleErrors.length >= 1, true);
});

test("stale timer callback and orphan owner cannot remove same-ID replacement", () => {
  const callbacks = [];
  const removed = [];
  const Mainloop = {
    timeout_add(_delay, callback) {
      callbacks.push(callback);
      return 71;
    },
    timeout_add_seconds(_delay, callback) {
      callbacks.push(callback);
      return 71;
    },
    source_remove(sourceId) {
      removed.push(sourceId);
      return true;
    },
  };
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    { value: 0 },
    { Mainloop }
  );
  const clearTrackedTimer = loadAppletMethod(
    "_clearTrackedTimer",
    "_scheduleTrackedTimer",
    { value: 0 },
    { Mainloop }
  );
  const trackTimer = loadAppletMethod(
    "_trackTimer",
    "_trackedTimerOwnedBy",
    { value: 0 }
  );
  const trackedTimerOwnedBy = loadAppletMethod(
    "_trackedTimerOwnedBy",
    "_untrackTimer",
    { value: 0 }
  );
  const untrackTimer = loadAppletMethod(
    "_untrackTimer",
    "_trackOrphanedTimer",
    { value: 0 }
  );
  const trackOrphanedTimer = loadAppletMethod(
    "_trackOrphanedTimer",
    "_untrackOrphanedTimer",
    { value: 0 }
  );
  const untrackOrphanedTimer = loadAppletMethod(
    "_untrackOrphanedTimer",
    "_retryOrphanedTimers",
    { value: 0 }
  );
  const lifecycleErrors = [];
  const applet = {
    appletRemoved: false,
    spawnGeneration: 1,
    processCleanupRetryTimer: 0,
    processCleanupRetryTimerOwner: null,
    _orphanedTimers: [],
    _resourceRegistry: { timers: {}, timerGroups: {}, timerOwners: {} },
    _lifecycleAllowsWork: () => true,
    _clearTrackedTimer: clearTrackedTimer,
    _trackTimer: trackTimer,
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _untrackTimer: untrackTimer,
    _trackOrphanedTimer: trackOrphanedTimer,
    _untrackOrphanedTimer: untrackOrphanedTimer,
    _retryOrphanedTimers: () => true,
    _runStateGuarded: (_group, callback) => callback(),
    _reportSubprocessError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(
    scheduleTrackedTimer.call(
      applet,
      "process-cleanup-retry",
      1,
      () => false,
      false,
      "processCleanupRetryTimer"
    ),
    71
  );
  const oldOwner = applet.processCleanupRetryTimerOwner;
  assert.equal(typeof oldOwner, "object");
  assert.equal(
    scheduleTrackedTimer.call(
      applet,
      "process-cleanup-retry",
      1,
      () => false,
      false,
      "processCleanupRetryTimer"
    ),
    71
  );
  const newOwner = applet.processCleanupRetryTimerOwner;
  assert.notEqual(newOwner, oldOwner);
  assert.equal(callbacks.length, 2);
  assert.equal(callbacks[0](), false);
  assert.equal(applet._resourceRegistry.timers["process-cleanup-retry"], 71);
  assert.equal(applet.processCleanupRetryTimer, 71);
  assert.equal(applet.processCleanupRetryTimerOwner, newOwner);
  assert.deepEqual(removed, [71]);
  assert.equal(lifecycleErrors.length, 0);

  const staleOrphanOwner = {};
  applet._orphanedTimers.push({
    name: "process-cleanup-retry",
    sourceId: 71,
    propertyName: "processCleanupRetryTimer",
    sourceRemoved: false,
    resourceGroup: "",
    owner: staleOrphanOwner,
  });
  const retryOrphanedTimers = loadAppletMethod(
    "_retryOrphanedTimers",
    "_clearTrackedTimer",
    { value: 0 },
    {
      Mainloop,
      LIFECYCLE_REMOVED: "removed",
      LIFECYCLE_REMOVING: "removing",
    }
  );
  applet._retryOrphanedTimers = retryOrphanedTimers;
  assert.equal(applet._retryOrphanedTimers(), false);
  assert.equal(removed.length, 1);
  assert.equal(applet._resourceRegistry.timers["process-cleanup-retry"], 71);
  assert.equal(applet.processCleanupRetryTimerOwner, newOwner);
  assert.equal(applet._orphanedTimers.length, 1);
});

test("real cleanup retry reaps orphan and resets episode failure latch", () => {
  const Mainloop = {
    callback: null,
    timeout_add(_delay, callback) {
      this.callback = callback;
      return 83;
    },
    timeout_add_seconds(_delay, callback) {
      this.callback = callback;
      return 83;
    },
    source_remove: () => true,
  };
  const scheduleProcessCleanupRetry = loadAppletMethod(
    "_scheduleProcessCleanupRetry",
    "_cancelPendingRecordingStart",
    { value: 0 }
  );
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    { value: 0 },
    { Mainloop }
  );
  const clearTrackedTimer = loadAppletMethod(
    "_clearTrackedTimer",
    "_scheduleTrackedTimer",
    { value: 0 },
    { Mainloop: { source_remove: () => true } }
  );
  const trackTimer = loadAppletMethod("_trackTimer", "_trackedTimerOwnedBy", { value: 0 });
  const trackedTimerOwnedBy = loadAppletMethod("_trackedTimerOwnedBy", "_untrackTimer", { value: 0 });
  const untrackTimer = loadAppletMethod("_untrackTimer", "_trackOrphanedTimer", { value: 0 });
  const trackOrphanedTimer = loadAppletMethod("_trackOrphanedTimer", "_untrackOrphanedTimer", { value: 0 });
  const untrackOrphanedTimer = loadAppletMethod("_untrackOrphanedTimer", "_retryOrphanedTimers", { value: 0 });
  const lifecycleErrors = [];
  const applet = {
    appletRemoved: false,
    lifecycleState: "running",
    spawnGeneration: 4,
    processCleanupRetryTimer: 0,
    processCleanupRetryTimerOwner: null,
    _processCleanupFailureLatch: {
      status: { episode: 4 },
      foreign: { episode: 9 },
      "__process-cleanup-retry__": { episode: 4 },
    },
    _orphanedProcesses: [
      { group: "status", process: {} },
      { group: "foreign", process: {} },
    ],
    _orphanedCancellables: [],
    _orphanedSignals: [],
    _orphanedMonitors: [],
    _orphanedHotkeys: [],
    _pendingHotkeyRebinds: [],
    _orphanedTimers: [],
    _orphanedDialogs: [],
    _resourceRegistry: { timers: {}, timerGroups: {}, timerOwners: {}, processes: {} },
    _lifecycleAllowsWork: () => true,
    _scheduleTrackedTimer: scheduleTrackedTimer,
    _clearTrackedTimer: clearTrackedTimer,
    _trackTimer: trackTimer,
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _untrackTimer: untrackTimer,
    _trackOrphanedTimer: trackOrphanedTimer,
    _untrackOrphanedTimer: untrackOrphanedTimer,
    _retryOrphanedTimers: () => true,
    _retryOrphanedProcesses() {
      this._orphanedProcesses = this._orphanedProcesses.filter(
        (entry) => entry && entry.group !== "status"
      );
      return true;
    },
    _retryOrphanedCancellables: () => true,
    _disconnectOrphanedSignals: () => true,
    _retryOrphanedMonitors: () => true,
    _retryOrphanedHotkeys: () => true,
    _retryPendingHotkeyRebinds: () => true,
    _retryOrphanedDialogs: () => true,
    _processCleanupStillPending() {
      return this._orphanedProcesses.length > 0;
    },
    _releaseBusyStateAfterProcessCleanup: () => true,
    _scheduleStatusPoll: () => true,
    _runStateGuarded: (_group, callback) => callback(),
    _reportSubprocessError: (...args) => lifecycleErrors.push(args),
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };
  assert.equal(scheduleProcessCleanupRetry.call(applet, "status"), true);
  assert.equal(applet.processCleanupRetryTimer, 83);
  assert.equal(applet._resourceRegistry.timers["process-cleanup-retry"], 83);
  assert.equal(typeof Mainloop.callback, "function");
  assert.equal(Mainloop.callback.call(applet), true);
  assert.equal(applet._orphanedProcesses.length, 1);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "status"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "foreign"), true);
  assert.equal(applet.processCleanupRetryTimer, 83);
  applet._orphanedProcesses.length = 0;
  assert.equal(Mainloop.callback.call(applet), false);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._resourceRegistry.timers, "process-cleanup-retry"), false);
  assert.equal(applet.processCleanupRetryTimer, 0);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "foreign"), true);
  assert.equal(lifecycleErrors.length, 0);
});

test("process retry distinguishes ambiguous and disappeared source ownership", () => {
  let sourceRemoveCalls = 0;
  let nextTimerId = 211;
  let removeMode = "throw";
  const Mainloop = {
    timeout_add(_delay, _callback) {
      return nextTimerId;
    },
    timeout_add_seconds(_delay, _callback) {
      return nextTimerId;
    },
    source_remove(_sourceId) {
      sourceRemoveCalls += 1;
      if (removeMode === "throw") {
        throw new Error("source disappeared during removal");
      }
      if (removeMode === "false") {
        return false;
      }
      return true;
    },
  };
  const scheduleProcessCleanupRetry = loadAppletMethod(
    "_scheduleProcessCleanupRetry",
    "_cancelPendingRecordingStart",
    { value: 0 }
  );
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    { value: 0 },
    { Mainloop }
  );
  const clearTrackedTimer = loadAppletMethod(
    "_clearTrackedTimer",
    "_scheduleTrackedTimer",
    { value: 0 },
    { Mainloop }
  );
  const retryOrphanedTimers = loadAppletMethod(
    "_retryOrphanedTimers",
    "_clearTrackedTimer",
    { value: 0 },
    {
      Mainloop,
      LIFECYCLE_REMOVED: "removed",
      LIFECYCLE_REMOVING: "removing",
    }
  );
  const trackTimer = loadAppletMethod("_trackTimer", "_trackedTimerOwnedBy", { value: 0 });
  const trackedTimerOwnedBy = loadAppletMethod("_trackedTimerOwnedBy", "_untrackTimer", { value: 0 });
  const untrackTimer = loadAppletMethod("_untrackTimer", "_trackOrphanedTimer", { value: 0 });
  const trackOrphanedTimer = loadAppletMethod("_trackOrphanedTimer", "_untrackOrphanedTimer", { value: 0 });
  const untrackOrphanedTimer = loadAppletMethod("_untrackOrphanedTimer", "_retryOrphanedTimers", { value: 0 });
  const lifecycleErrors = [];
  const makeApplet = (sourceId, owner) => ({
    appletRemoved: false,
    lifecycleState: "running",
    spawnGeneration: 1,
    processCleanupRetryTimer: sourceId,
    processCleanupRetryTimerOwner: owner,
    _orphanedTimers: [],
    _resourceRegistry: {
      timers: { "process-cleanup-retry": sourceId },
      timerGroups: { "process-cleanup-retry": "" },
      timerOwners: { "process-cleanup-retry": owner },
      processes: {},
    },
    _lifecycleAllowsWork: () => true,
    _processCleanupStillPending: () => true,
    _scheduleTrackedTimer: scheduleTrackedTimer,
    _retryOrphanedTimers: retryOrphanedTimers,
    _clearTrackedTimer: clearTrackedTimer,
    _trackTimer: trackTimer,
    _trackedTimerOwnedBy: trackedTimerOwnedBy,
    _untrackTimer: untrackTimer,
    _trackOrphanedTimer: trackOrphanedTimer,
    _untrackOrphanedTimer: untrackOrphanedTimer,
    _runStateGuarded: (_group, callback) => callback(),
    _reportSubprocessError: (...args) => lifecycleErrors.push(args),
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  });

  const throwOwner = {};
  const throwApplet = makeApplet(211, throwOwner);
  assert.equal(
    clearTrackedTimer.call(throwApplet, "process-cleanup-retry", "processCleanupRetryTimer"),
    false
  );
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(throwApplet._orphanedTimers.length, 1);
  assert.equal(throwApplet._orphanedTimers[0].sourceRemovalAmbiguous, true);
  assert.equal(scheduleProcessCleanupRetry.call(throwApplet, "status"), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(throwApplet.processCleanupRetryTimer, 211);
  assert.equal(throwApplet._resourceRegistry.timers["process-cleanup-retry"], 211);

  const retryThrowOwner = {};
  const retryThrowApplet = makeApplet(212, retryThrowOwner);
  retryThrowApplet._orphanedTimers.push({
    name: "process-cleanup-retry",
    sourceId: 212,
    propertyName: "processCleanupRetryTimer",
    sourceRemoved: false,
    sourceRemovalAmbiguous: false,
    resourceGroup: "",
    owner: retryThrowOwner,
  });
  assert.equal(retryOrphanedTimers.call(retryThrowApplet), false);
  assert.equal(sourceRemoveCalls, 2);
  assert.equal(retryThrowApplet._orphanedTimers[0].sourceRemovalAmbiguous, true);
  assert.equal(retryOrphanedTimers.call(retryThrowApplet), false);
  assert.equal(sourceRemoveCalls, 2);

  const replacementRetryOwner = {};
  retryThrowApplet.processCleanupRetryTimerOwner = replacementRetryOwner;
  retryThrowApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = replacementRetryOwner;
  assert.equal(retryOrphanedTimers.call(retryThrowApplet), false);
  assert.equal(sourceRemoveCalls, 2);
  assert.equal(retryThrowApplet.processCleanupRetryTimer, 212);
  assert.equal(
    retryThrowApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    replacementRetryOwner
  );

  const registryOnlyOwner = {};
  const registryOnlyApplet = makeApplet(214, registryOnlyOwner);
  registryOnlyApplet.lifecycleState = "removed";
  assert.equal(retryOrphanedTimers.call(registryOnlyApplet), false);
  assert.equal(sourceRemoveCalls, 3);
  assert.equal(registryOnlyApplet._orphanedTimers.length, 1);
  assert.equal(registryOnlyApplet._orphanedTimers[0].owner, registryOnlyOwner);
  assert.equal(registryOnlyApplet._orphanedTimers[0].sourceRemovalAmbiguous, true);
  assert.equal(retryOrphanedTimers.call(registryOnlyApplet), false);
  assert.equal(sourceRemoveCalls, 3);
  const registryOnlyReplacementOwner = {};
  registryOnlyApplet.processCleanupRetryTimerOwner = registryOnlyReplacementOwner;
  registryOnlyApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = registryOnlyReplacementOwner;
  assert.equal(retryOrphanedTimers.call(registryOnlyApplet), false);
  assert.equal(sourceRemoveCalls, 3);
  assert.equal(
    registryOnlyApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    registryOnlyReplacementOwner
  );

  removeMode = "false";
  const falseRetryOwner = {};
  const falseRetryApplet = makeApplet(215, falseRetryOwner);
  falseRetryApplet._orphanedTimers.push({
    name: "process-cleanup-retry",
    sourceId: 215,
    propertyName: "processCleanupRetryTimer",
    sourceRemoved: false,
    sourceRemovalAmbiguous: false,
    resourceGroup: "",
    owner: falseRetryOwner,
  });
  assert.equal(retryOrphanedTimers.call(falseRetryApplet), false);
  assert.equal(sourceRemoveCalls, 4);
  assert.equal(falseRetryApplet._orphanedTimers[0].sourceRemoved, true);
  assert.equal(falseRetryApplet._orphanedTimers[0].sourceRemovalAmbiguous, false);
  const falseRetryReplacementOwner = {};
  falseRetryApplet.processCleanupRetryTimerOwner = falseRetryReplacementOwner;
  falseRetryApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = falseRetryReplacementOwner;
  assert.equal(retryOrphanedTimers.call(falseRetryApplet), true);
  assert.equal(sourceRemoveCalls, 4);
  assert.equal(
    falseRetryApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    falseRetryReplacementOwner
  );

  removeMode = "false";
  const disappearedOwner = {};
  const disappearedApplet = makeApplet(211, disappearedOwner);
  assert.equal(
    clearTrackedTimer.call(disappearedApplet, "process-cleanup-retry", "processCleanupRetryTimer"),
    false
  );
  assert.equal(sourceRemoveCalls, 5);
  assert.equal(disappearedApplet._orphanedTimers[0].sourceRemoved, true);
  assert.equal(disappearedApplet._orphanedTimers[0].sourceRemovalAmbiguous, false);
  assert.equal(scheduleProcessCleanupRetry.call(disappearedApplet, "status"), true);
  assert.equal(sourceRemoveCalls, 5);
  assert.equal(disappearedApplet.processCleanupRetryTimer, 211);
  const replacementOwner = disappearedApplet.processCleanupRetryTimerOwner;
  assert.notEqual(replacementOwner, disappearedOwner);
  assert.equal(
    clearTrackedTimer.call(
      disappearedApplet,
      "process-cleanup-retry",
      "processCleanupRetryTimer",
      undefined,
      undefined,
      disappearedOwner
    ),
    false
  );
  assert.equal(sourceRemoveCalls, 5);
  assert.equal(disappearedApplet.processCleanupRetryTimer, 211);
  assert.equal(disappearedApplet.processCleanupRetryTimerOwner, replacementOwner);

  removeMode = "throw";
  nextTimerId = 213;
  const rollbackOwner = {};
  const rollbackApplet = makeApplet(213, rollbackOwner);
  rollbackApplet.processCleanupRetryTimer = 0;
  rollbackApplet.processCleanupRetryTimerOwner = null;
  rollbackApplet._resourceRegistry = { timers: {}, timerGroups: {}, timerOwners: {} };
  rollbackApplet._orphanedTimers = [];
  rollbackApplet._trackTimer = () => { throw new Error("timer owner assignment failed"); };
  rollbackApplet._scheduleProcessCleanupRetry = () => false;
  let rollbackOrphanArgs = null;
  const rollbackTrackOrphanedTimer = rollbackApplet._trackOrphanedTimer;
  rollbackApplet._trackOrphanedTimer = function(...args) {
    rollbackOrphanArgs = args;
    return rollbackTrackOrphanedTimer.apply(this, args);
  };
  assert.equal(
    scheduleTrackedTimer.call(
      rollbackApplet,
      "process-cleanup-retry",
      1,
      () => false,
      false,
      "processCleanupRetryTimer",
      "status"
    ),
    0
  );
  assert.equal(sourceRemoveCalls, 6);
  assert.equal(rollbackOrphanArgs[6], true);
  assert.equal(rollbackApplet._orphanedTimers.length, 1);
  assert.equal(rollbackApplet._orphanedTimers[0].sourceRemovalAmbiguous, true);
  assert.equal(retryOrphanedTimers.call(rollbackApplet), false);
  assert.equal(retryOrphanedTimers.call(rollbackApplet), false);
  assert.equal(sourceRemoveCalls, 6);

  const rollbackReplacementOwner = {};
  rollbackApplet.processCleanupRetryTimer = 213;
  rollbackApplet.processCleanupRetryTimerOwner = rollbackReplacementOwner;
  rollbackApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = rollbackReplacementOwner;
  assert.equal(retryOrphanedTimers.call(rollbackApplet), false);
  assert.equal(sourceRemoveCalls, 6);
  assert.equal(
    rollbackApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    rollbackReplacementOwner
  );

  sourceRemoveCalls = 0;
  removeMode = "throw";
  const trackerThrowOwner = {};
  const trackerThrowApplet = makeApplet(216, trackerThrowOwner);
  trackerThrowApplet.lifecycleState = "removed";
  trackerThrowApplet._trackOrphanedTimer = () => false;
  assert.equal(retryOrphanedTimers.call(trackerThrowApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(trackerThrowApplet._resourceRegistry.timerCleanupBlocks.length, 1);
  assert.equal(trackerThrowApplet._resourceRegistry.timerCleanupBlocks[0].owner, trackerThrowOwner);
  assert.equal(trackerThrowApplet._resourceRegistry.timerCleanupBlocks[0].sourceRemovalAmbiguous, true);
  assert.equal(retryOrphanedTimers.call(trackerThrowApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  const trackerThrowReplacementOwner = {};
  trackerThrowApplet.processCleanupRetryTimerOwner = trackerThrowReplacementOwner;
  trackerThrowApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = trackerThrowReplacementOwner;
  assert.equal(retryOrphanedTimers.call(trackerThrowApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(
    trackerThrowApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    trackerThrowReplacementOwner
  );

  sourceRemoveCalls = 0;
  removeMode = "false";
  const trackerFalseOwner = {};
  const trackerFalseApplet = makeApplet(217, trackerFalseOwner);
  trackerFalseApplet.lifecycleState = "removed";
  trackerFalseApplet._trackOrphanedTimer = () => false;
  assert.equal(retryOrphanedTimers.call(trackerFalseApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(trackerFalseApplet._resourceRegistry.timerCleanupBlocks.length, 1);
  assert.equal(trackerFalseApplet._resourceRegistry.timerCleanupBlocks[0].sourceRemoved, true);
  assert.equal(retryOrphanedTimers.call(trackerFalseApplet), true);
  assert.equal(retryOrphanedTimers.call(trackerFalseApplet), true);
  assert.equal(sourceRemoveCalls, 1);
  trackerFalseApplet._resourceRegistry.timers["process-cleanup-retry"] = 217;
  const trackerFalseReplacementOwner = {};
  trackerFalseApplet.processCleanupRetryTimerOwner = trackerFalseReplacementOwner;
  trackerFalseApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = trackerFalseReplacementOwner;
  assert.equal(retryOrphanedTimers.call(trackerFalseApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(
    trackerFalseApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    trackerFalseReplacementOwner
  );

  sourceRemoveCalls = 0;
  removeMode = "throw";
  const mismatchOwner = {};
  const mismatchForeignOwner = {};
  const mismatchApplet = makeApplet(218, mismatchOwner);
  mismatchApplet.lifecycleState = "removed";
  mismatchApplet._trackOrphanedTimer = () => {
    mismatchApplet._orphanedTimers.push({
      name: "process-cleanup-retry",
      sourceId: 218,
      propertyName: "processCleanupRetryTimer",
      sourceRemoved: false,
      sourceRemovalAmbiguous: false,
      resourceGroup: "",
      owner: mismatchForeignOwner,
    });
    return true;
  };
  assert.equal(retryOrphanedTimers.call(mismatchApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(mismatchApplet._resourceRegistry.timerCleanupBlocks.length, 1);
  assert.equal(mismatchApplet._resourceRegistry.timerCleanupBlocks[0].owner, mismatchOwner);
  assert.equal(mismatchApplet._resourceRegistry.timerCleanupBlocks[0].sourceRemovalAmbiguous, true);
  assert.equal(retryOrphanedTimers.call(mismatchApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  const mismatchReplacementOwner = {};
  mismatchApplet.processCleanupRetryTimerOwner = mismatchReplacementOwner;
  mismatchApplet._resourceRegistry.timerOwners["process-cleanup-retry"] = mismatchReplacementOwner;
  assert.equal(retryOrphanedTimers.call(mismatchApplet), false);
  assert.equal(sourceRemoveCalls, 1);
  assert.equal(
    mismatchApplet._resourceRegistry.timerOwners["process-cleanup-retry"],
    mismatchReplacementOwner
  );
});

test("foreign orphan scheduler throw stays outside stopped group result", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const lifecycleErrors = [];
  let globalPending = true;
  const foreignProcess = {};
  const applet = {
    _resourceRegistry: { processes: {} },
    _orphanedProcesses: [{
      group: "foreign",
      process: foreignProcess,
      generation: 1,
      processGroupIdentity: { pid: "501", startTime: "1" },
    }],
    _processCleanupFailureLatch: {},
    _processCleanupEpisodeByGroup: {},
    _retryOrphanedProcesses: () => true,
    _processCleanupStillPending: () => globalPending,
    _scheduleProcessCleanupRetry: () => { throw new Error("global retry failed"); },
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(lifecycleErrors.length, 1);
  assert.equal(lifecycleErrors[0][0], "process-cleanup-retry");
  assert.equal(terminateProcessesByGroup.call(applet, "doctor"), "stopped");
  assert.equal(lifecycleErrors.length, 1);

  applet._orphanedProcesses = [{
    group: "foreign",
    process: foreignProcess,
    generation: 2,
    processGroupIdentity: { pid: "501", startTime: "2" },
  }];
  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(lifecycleErrors.length, 2);

  globalPending = false;
  assert.equal(terminateProcessesByGroup.call(applet, "doctor"), "stopped");
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "__process-cleanup-retry__"), false);

  globalPending = true;
  applet._orphanedProcesses = [{
    group: "foreign",
    process: {},
    generation: 3,
    processGroupIdentity: { pid: "501", startTime: "3" },
  }];
  assert.equal(terminateProcessesByGroup.call(applet, "status"), "stopped");
  assert.equal(lifecycleErrors.length, 3);
});

test("process cleanup failure latch spans retries and resets on episode or stopped", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const process = {};
  const processes = {
    "process-1": {
      process,
      generation: 1,
      processGroupIdentity: { pid: "601", startTime: "1" },
      group: "focus",
      cancel: () => { throw new Error("focus cleanup failed"); },
    },
  };
  const lifecycleErrors = [];
  const applet = {
    spawnGeneration: 1,
    _processCleanupFailureLatch: {},
    _processCleanupEpisodeByGroup: {},
    _resourceRegistry: { processes },
    _orphanedProcesses: [],
    _trackOrphanedProcess: () => true,
    _retryOrphanedProcesses: () => true,
    _processCleanupStillPending: () => false,
    _scheduleProcessCleanupRetry: () => true,
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(terminateProcessesByGroup.call(applet, "focus"), "failed");
  assert.equal(terminateProcessesByGroup.call(applet, "focus"), "failed");
  assert.equal(lifecycleErrors.length, 1);

  applet.spawnGeneration = 2;
  processes["process-1"].generation = 2;
  assert.equal(terminateProcessesByGroup.call(applet, "focus"), "failed");
  assert.equal(lifecycleErrors.length, 2);

  processes["process-1"].processGroupIdentity = { pid: "601", startTime: "2" };
  assert.equal(terminateProcessesByGroup.call(applet, "focus"), "failed");
  assert.equal(lifecycleErrors.length, 3);

  delete processes["process-1"];
  assert.equal(terminateProcessesByGroup.call(applet, "focus"), "stopped");
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "focus"), false);

  processes["process-2"] = {
    process: {},
    generation: 3,
    processGroupIdentity: { pid: "601", startTime: "3" },
    group: "focus",
    cancel: () => { throw new Error("new focus cleanup failed"); },
  };
  assert.equal(terminateProcessesByGroup.call(applet, "focus"), "failed");
  assert.equal(lifecycleErrors.length, 4);
});

test("cleanup failure episodes stay group-local across successful retries", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const processA = {};
  const processB = {};
  const processes = {
    a: {
      process: processA,
      group: "group-a",
      cancel: () => { throw new Error("group A failed"); },
    },
    b: {
      process: processB,
      group: "group-b",
      cancel: () => { throw new Error("group B failed"); },
    },
  };
  const lifecycleErrors = [];
  const applet = {
    _resourceRegistry: { processes },
    _orphanedProcesses: [],
    _processCleanupFailureLatch: {},
    _trackOrphanedProcess: () => true,
    _retryOrphanedProcesses: () => true,
    _processCleanupStillPending: () => true,
    _scheduleProcessCleanupRetry: (group) => {
      applet.scheduledGroups.push(group);
      return true;
    },
    scheduledGroups: [],
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(terminateProcessesByGroup.call(applet, "group-a"), "failed");
  assert.equal(terminateProcessesByGroup.call(applet, "group-a"), "failed");
  assert.equal(lifecycleErrors.length, 1);
  assert.equal(terminateProcessesByGroup.call(applet, "group-b"), "failed");
  assert.equal(lifecycleErrors.length, 2);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "group-a"), true);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "group-b"), true);

  delete processes.a;
  assert.equal(terminateProcessesByGroup.call(applet, "group-a"), "stopped");
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "group-a"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "group-b"), true);

  processes.a = {
    process: {},
    group: "group-a",
    cancel: () => { throw new Error("new group A failed"); },
  };
  assert.equal(terminateProcessesByGroup.call(applet, "group-a"), "failed");
  assert.equal(lifecycleErrors.length, 3);
  assert.equal(Object.prototype.hasOwnProperty.call(applet._processCleanupFailureLatch, "group-b"), true);
});

test("doctor and status pending cleanup stay silent until stopped", () => {
  const invalidate = loadAppletMethod(
    "_invalidateBackgroundCallbacksForRecording",
    "_validatedDoctorPayload",
    { value: 0 }
  );
  for (const [group, message] of [
    ["status", "Status refresh could not be stopped"],
    ["doctor", "Doctor could not be stopped"],
  ]) {
    const harness = makeRecordingApplet();
    const { applet } = harness;
    applet._invalidateBackgroundCallbacksForRecording = invalidate;
    let attempts = 0;
    applet._processCleanupStillPending = () => false;
    applet._terminateProcessesByGroup = (currentGroup) => {
      if (currentGroup === group) {
        attempts += 1;
        return attempts < 3 ? "pending" : "stopped";
      }
      return "stopped";
    };

    assert.equal(applet._invalidateBackgroundCallbacksForRecording(true), false);
    assert.equal(
      harness.preservedStatusEvents.filter((event) => event.message === message).length,
      0
    );
    assert.equal(applet._invalidateBackgroundCallbacksForRecording(true), false);
    assert.equal(
      harness.preservedStatusEvents.filter((event) => event.message === message).length,
      0
    );
    assert.equal(applet._invalidateBackgroundCallbacksForRecording(true), true);
    assert.equal(
      harness.preservedStatusEvents.filter((event) => event.message === message).length,
      0
    );
  }
});

test("definitive process cleanup failure reports once", () => {
  const invalidate = loadAppletMethod(
    "_invalidateBackgroundCallbacksForRecording",
    "_validatedDoctorPayload",
    { value: 0 }
  );
  const harness = makeRecordingApplet();
  const { applet } = harness;
  applet._invalidateBackgroundCallbacksForRecording = invalidate;
  applet._processCleanupStillPending = () => false;
  applet._terminateProcessesByGroup = (group) =>
    group === "status" ? "failed" : "stopped";

  assert.equal(applet._invalidateBackgroundCallbacksForRecording(true), false);
  assert.equal(
    harness.preservedStatusEvents.filter(
      (event) => event.message === "Status refresh could not be stopped"
    ).length,
    1
  );
});

test("history and model refreshes require stopped cleanup", () => {
  const historyRefresh = loadAppletMethod(
    "_refreshHistory",
    "_listAllTranscripts",
    { value: 0 }
  );
  const modelRefresh = loadAppletMethod(
    "_refreshModelMenu",
    "_refreshTextModelMenu",
    { value: 0 }
  );
  let cleanupStatus = "pending";
  let spawned = 0;
  const statusEvents = [];
  const base = {
    isCommandRunning: false,
    _canMutateMenu: () => true,
    _hasActiveRecordingState: () => false,
    _hasLocalProcessingWorkflow: () => false,
    _processCleanupStatus: (result) => result,
    _terminateProcessesByGroup: () => cleanupStatus,
    _setStatusPreservingRecording: (...args) => statusEvents.push(args),
    _spawnJson: () => {
      spawned += 1;
      return {};
    },
  };
  const historyApplet = {
    ...base,
    historyItem: { menu: { isOpen: true } },
    historyRefreshToken: null,
    historyRefreshQueued: false,
    _populateHistoryMenu: () => {},
    _historyArgs: () => ["history"],
  };
  const modelApplet = {
    ...base,
    modelItem: { menu: { isOpen: true } },
    modelMenuRefreshToken: null,
    modelMenuFingerprint: null,
    modelMenuLastRefreshAt: 0,
    voiceModelActionToken: null,
    voiceModelCleanupFailed: false,
    _populateModelMenu: () => {},
    _modelsArgs: () => ["models"],
  };

  historyRefresh.call(historyApplet);
  modelRefresh.call(modelApplet);
  assert.equal(spawned, 0);
  assert.equal(statusEvents.length, 0);

  cleanupStatus = "failed";
  historyRefresh.call(historyApplet);
  modelRefresh.call(modelApplet);
  assert.equal(spawned, 0);
  assert.equal(statusEvents.length, 2);

  cleanupStatus = "stopped";
  historyRefresh.call(historyApplet);
  modelRefresh.call(modelApplet);
  assert.equal(spawned, 2);
});

test("text-model settings gate UI follow-up on refresh status", () => {
  const onTextModelSettingsChanged = loadAppletMethod(
    "_onTextModelSettingsChanged",
    "_onOpenAiFlexProcessingSettingsChanged",
    { value: 0 }
  );
  let cleanupStatus = "pending";
  let ollamaCleanupCalls = 0;
  let menuPopulationCalls = 0;
  let panelUpdates = 0;
  const preservedStatusEvents = [];
  const applet = {
    textModelMenuRefreshToken: {},
    ollamaModelFlowToken: null,
    ollamaInstallWatchToken: null,
    ollamaModelInstallRunning: false,
    ollamaModelInstallToken: null,
    ollamaModelCleanupFailed: false,
    _ollamaModelCleanupStatus: "stopped",
    notificationSessionActive: false,
    _recordingCommandToken: null,
    isCommandRunning: false,
    status: "idle",
    _hasLocalProcessingWorkflow: () => false,
    _processCleanupStatus: (result) => result,
    _terminateProcessesByGroup: () => cleanupStatus,
    _cancelOllamaInstallWatch: () => true,
    _clearOllamaModelFlow: () => {
      ollamaCleanupCalls += 1;
      return true;
    },
    _setStatusPreservingRecording: (...args) => preservedStatusEvents.push(args),
    _setStatus: () => {},
    _populateTextModelMenu: () => { menuPopulationCalls += 1; },
    _updatePanel: () => { panelUpdates += 1; },
  };

  onTextModelSettingsChanged.call(applet);
  assert.equal(ollamaCleanupCalls, 1);
  assert.equal(menuPopulationCalls, 0);
  assert.equal(panelUpdates, 0);
  assert.equal(preservedStatusEvents.length, 0);

  cleanupStatus = "failed";
  onTextModelSettingsChanged.call(applet);
  assert.equal(ollamaCleanupCalls, 2);
  assert.equal(menuPopulationCalls, 0);
  assert.equal(panelUpdates, 0);
  assert.equal(
    preservedStatusEvents.filter((event) => event[1] === "Text model list refresh could not be stopped").length,
    1
  );

  cleanupStatus = "stopped";
  onTextModelSettingsChanged.call(applet);
  assert.equal(ollamaCleanupCalls, 3);
  assert.equal(menuPopulationCalls, 1);
  assert.equal(panelUpdates, 1);
});

test("Ollama model cleanup tri-state gates release and reports failed", () => {
  const clearFlow = loadAppletMethod(
    "_clearOllamaModelFlow",
    "_clearOllamaModelFlowOrReport",
    { value: 0 }
  );
  const clearFlowOrReport = loadAppletMethod(
    "_clearOllamaModelFlowOrReport",
    "_isValidOllamaCatalogPayload",
    { value: 0 }
  );

  for (const cleanupStatus of ["stopped", "pending", "failed"]) {
    const flowToken = {};
    const installToken = {};
    const terminalToken = {};
    const statusEvents = [];
    let releaseCalls = 0;
    let terminateCalls = 0;
    const applet = {
      ollamaModelFlowToken: flowToken,
      ollamaModelInstallToken: installToken,
      ollamaModelInstallRunning: true,
      ollamaModelCleanupFailed: false,
      _ollamaModelCleanupStatus: "stopped",
      ollamaModelCleanupStatus: "stopped",
      terminalWorkflowToken: terminalToken,
      terminalWorkflowRunning: true,
      isCommandRunning: true,
      lastTranscript: "unchanged",
      _clearOllamaModelFlow: clearFlow,
      _processCleanupStatus: (result) => result,
      _terminateProcessesByGroup: (group) => {
        assert.equal(group, "ollama");
        terminateCalls += 1;
        return cleanupStatus;
      },
      _hasTrackedProcessGroup: (group) => {
        assert.equal(group, "ollama");
        return false;
      },
      _releaseBusyStateAfterProcessCleanup: (group, failureProperty) => {
        assert.equal(group, "ollama");
        assert.equal(failureProperty, "ollamaModelCleanupFailed");
        releaseCalls += 1;
        applet.isCommandRunning = false;
        return true;
      },
      _setStatusPreservingRecording: (status, message, transcript) => {
        statusEvents.push({ status, message, transcript });
      },
    };

    const result = clearFlowOrReport.call(applet, flowToken);
    const stopped = cleanupStatus === "stopped";
    assert.equal(result, stopped);
    assert.equal(terminateCalls, 1);
    assert.equal(applet._ollamaModelCleanupStatus, cleanupStatus);
    assert.equal(applet.ollamaModelFlowToken, null);
    assert.equal(applet.terminalWorkflowToken, null);
    assert.equal(applet.terminalWorkflowRunning, !stopped);
    assert.equal(applet.ollamaModelInstallRunning, !stopped);
    assert.equal(applet.ollamaModelInstallToken, stopped ? null : installToken);
    assert.equal(applet.isCommandRunning, !stopped);
    assert.equal(applet.ollamaModelCleanupFailed, !stopped);
    assert.equal(releaseCalls, stopped ? 1 : 0);
    assert.equal(statusEvents.length, cleanupStatus === "failed" ? 1 : 0);
    if (cleanupStatus === "failed") {
      assert.deepEqual(statusEvents[0], {
        status: "error",
        message: "Ollama operation could not be stopped",
        transcript: "unchanged",
      });
    }
  }
});

test("restart and focused-window cleanup block pending work", () => {
  let cleanupStatus = "pending";
  let reloads = 0;
  const statusEvents = [];
  const restart = loadAppletMethod(
    "_restartApplet",
    "_refreshStatus",
    { value: 0 },
    {
      Extension: {
        reloadExtension: () => { reloads += 1; },
        Type: { APPLET: 1 },
      },
      UUID: "test",
    }
  );
  const restartApplet = {
    lastTranscript: "",
    _processCleanupStatus: (result) => result,
    _terminateProcessesByGroup: () => cleanupStatus,
    _setStatusPreservingRecording: (...args) => statusEvents.push(args),
    _safeLogError: () => {},
  };

  restart.call(restartApplet);
  assert.equal(reloads, 0);
  assert.equal(statusEvents.length, 0);
  cleanupStatus = "failed";
  restart.call(restartApplet);
  assert.equal(reloads, 0);
  assert.equal(statusEvents.length, 1);
  cleanupStatus = "stopped";
  restart.call(restartApplet);
  assert.equal(reloads, 1);

  const rememberFocusedWindow = loadAppletMethod(
    "_rememberFocusedWindow",
    "_closeMenuForKeyboardInsert",
    { value: 0 }
  );
  let completionCalls = 0;
  const focusApplet = {
    targetWindowGeneration: 0,
    targetWindowWaylandEvidence: null,
    targetWindowXPendingGeneration: 0,
    _processCleanupStatus: (result) => result,
    _terminateProcessesByGroup: () => "pending",
    _clearTargetWindowXid: () => {},
    _setStatusPreservingRecording: () => {},
  };
  assert.equal(
    rememberFocusedWindow.call(focusApplet, false, () => { completionCalls += 1; }),
    false
  );
  assert.equal(completionCalls, 1);
  assert.equal(focusApplet.targetWindow, undefined);
});

test("text insertion and recording invalidation distinguish pending from failed", () => {
  const cancelTextInsert = loadAppletMethod(
    "_cancelTextInsertForSettingsChange",
    "on_applet_clicked",
    { value: 0 }
  );
  const harness = makeRecordingApplet({
    pendingCleanupGroups: textInsertCleanupGroups,
  });
  harness.applet._cancelTextInsertForSettingsChange = cancelTextInsert;
  harness.applet._clearPasteTimer = () => true;
  harness.applet.textInsertToken = {};
  harness.applet.autoRelistenPending = true;

  assert.equal(harness.applet._cancelTextInsertForSettingsChange(), false);
  assert.equal(harness.applet.textInsertCancellationFailed, true);
  assert.equal(harness.preservedStatusEvents.length, 0);

  harness.applet._terminateProcessesByGroup = () => "failed";
  assert.equal(harness.applet._cancelTextInsertForSettingsChange(), false);
  assert.equal(harness.preservedStatusEvents.length, 1);

  const invalidationHarness = makeRecordingApplet({
    pendingCleanupGroups: backgroundCleanupGroups,
  });
  assert.equal(
    invalidationHarness.applet._invalidateBackgroundCallbacksForRecording(true),
    false
  );
  assert.equal(invalidationHarness.preservedStatusEvents.length, 0);
});

test("registration token alone starts a new process cleanup episode", () => {
  const terminateProcessesByGroup = loadAppletMethod(
    "_terminateProcessesByGroup",
    "_hasTrackedProcessGroup",
    { value: 0 }
  );
  const lifecycleErrors = [];
  let cancelCalls = 0;
  let trackCalls = 0;
  const processWrapper = {};
  const processEntry = {
    group: "token-episode",
    process: processWrapper,
    generation: 1,
    processGroupIdentity: { pid: "601", startTime: "1" },
    cancel() {
      cancelCalls += 1;
      throw new Error("deterministic cancel failure");
    },
  };
  const applet = {
    _resourceRegistry: {
      processes: { "token-a": processEntry },
    },
    _orphanedProcesses: [],
    _processCleanupFailureLatch: {},
    _processCleanupEpisodeByGroup: {},
    _retryOrphanedProcesses: () => true,
    _processCleanupStillPending: () => false,
    _trackOrphanedProcess: () => {
      trackCalls += 1;
      return true;
    },
    _scheduleProcessCleanupRetry: () => true,
    _recordLifecycleError: (...args) => lifecycleErrors.push(args),
  };

  assert.equal(terminateProcessesByGroup.call(applet, "token-episode"), "failed");
  assert.equal(cancelCalls, 1);
  assert.equal(trackCalls, 1);
  assert.equal(lifecycleErrors.length, 1);
  assert.equal(terminateProcessesByGroup.call(applet, "token-episode"), "failed");
  assert.equal(cancelCalls, 2);
  assert.equal(trackCalls, 2);
  assert.equal(lifecycleErrors.length, 1);

  assert.equal(delete applet._resourceRegistry.processes["token-a"], true);
  applet._resourceRegistry.processes["token-b"] = processEntry;
  assert.equal(terminateProcessesByGroup.call(applet, "token-episode"), "failed");
  assert.equal(cancelCalls, 3);
  assert.equal(trackCalls, 3);
  assert.equal(lifecycleErrors.length, 2);
  assert.equal(terminateProcessesByGroup.call(applet, "token-episode"), "failed");
  assert.equal(cancelCalls, 4);
  assert.equal(trackCalls, 4);
  assert.equal(lifecycleErrors.length, 2);
});

test("state guard gates callback before lifecycle work", () => {
  const runStateGuarded = loadAppletMethod(
    "_runStateGuarded",
    "_runTeardownGuarded",
    { value: 0 }
  );
  let callbackCalls = 0;
  let lifecycleErrors = 0;
  const applet = {
    _lifecycleAllowsWork: () => false,
    _recordLifecycleError: () => { lifecycleErrors += 1; },
  };

  assert.equal(
    runStateGuarded.call(applet, "disabled-state", () => {
      callbackCalls += 1;
      return "unexpected";
    }, "fallback"),
    "fallback"
  );
  assert.equal(callbackCalls, 0);
  assert.equal(lifecycleErrors, 0);

  applet._lifecycleAllowsWork = () => true;
  assert.equal(
    runStateGuarded.call(applet, "state-error", () => {
      throw new Error("state callback failed");
    }, "fallback"),
    "fallback"
  );
  assert.equal(lifecycleErrors, 1);
});

test("spawnText forwards bounded backend options and suppresses stale callbacks", () => {
  const clock = { value: 0 };
  const spawnText = loadAppletMethod("_spawnText", "_applyPayload", clock, {
    CLI_COMMAND_TIMEOUT_MS: 1000,
    MAX_SPAWN_TEXT_BYTES: 32,
    MAX_SPAWN_STDERR_BYTES: 16,
    utf8ByteLength: (value) => Buffer.byteLength(String(value || ""), "utf8"),
  });
  const backendCalls = [];
  const guardGroups = [];
  const lifecycleErrors = [];
  const applet = {
    _statusRefreshToken: 0,
    _guardStateCallback(group, callback) {
      guardGroups.push(group);
      const expectedToken = this._statusRefreshToken + 1;
      return (...args) => {
        if (expectedToken !== this._statusRefreshToken) return;
        callback(...args);
      };
    },
    _coerceSpawnArgs(args) {
      return args.slice();
    },
    _isStatusCommandArgs: () => false,
    _spawnJsonWithBackendEnvironment(args, env, callback, inputText, options) {
      backendCalls.push({ args, env, callback, inputText, options });
      return { cancel: () => true };
    },
    _recordLifecycleError(label, error) {
      lifecycleErrors.push([label, error]);
    },
  };

  const successResults = [];
  const successHandle = spawnText.call(
    applet,
    ["backend", "--success"],
    (output, result) => successResults.push([output, result]),
    { timeoutMs: 77, resourceGroup: "text-success" },
  );
  assert.ok(successHandle);
  assert.equal(applet._statusRefreshToken, 1);
  assert.deepEqual(guardGroups, ["backend-text"]);
  assert.deepEqual(backendCalls[0].args, ["backend", "--success"]);
  assert.equal(Object.keys(backendCalls[0].env).length, 0);
  assert.equal(backendCalls[0].inputText, null);
  assert.equal(backendCalls[0].options.timeoutMs, 77);
  assert.equal(backendCalls[0].options.maxStdoutBytes, 32);
  assert.equal(backendCalls[0].options.maxStderrBytes, 16);
  assert.equal(backendCalls[0].options.resourceGroup, "text-success");
  const successResult = { exitCode: 0 };
  backendCalls[0].callback("plain output", successResult);
  assert.deepEqual(successResults, [["plain output", successResult]]);

  const staleResults = [];
  spawnText.call(
    applet,
    ["backend", "--stale"],
    (output, result) => staleResults.push([output, result]),
    { resourceGroup: "text-stale" },
  );
  const errorResults = [];
  const errorResult = { error: "backend failed" };
  spawnText.call(
    applet,
    ["backend", "--error"],
    (output, result) => errorResults.push([output, result]),
    { resourceGroup: "text-error" },
  );
  assert.equal(applet._statusRefreshToken, 3);
  assert.equal(backendCalls.length, 3);
  assert.equal(backendCalls[1].options.resourceGroup, "text-stale");
  assert.equal(backendCalls[2].options.resourceGroup, "text-error");

  backendCalls[1].callback("stale output", { exitCode: 0 });
  assert.deepEqual(staleResults, []);
  backendCalls[2].callback("ignored output", errorResult);
  assert.deepEqual(errorResults, [["", errorResult]]);
  assert.deepEqual(lifecycleErrors, []);
});

test("stale text and status callbacks cannot clear newer tokens", () => {
  const refreshTextModelMenu = loadAppletMethod(
    "_refreshTextModelMenuForBackend",
    "_ensureTextModelMenuPool",
    { value: 0 }
  );
  const textCallbacks = [];
  let textMenuUpdates = 0;
  const textApplet = {
    textModelItem: { menu: { isOpen: true } },
    textModelMenuRefreshToken: null,
    ollamaModelFlowToken: null,
    ollamaInstallWatchToken: null,
    ollamaModelInstallToken: null,
    ollamaModelInstallRunning: false,
    ollamaModelCleanupFailed: false,
    _textModelMenuFingerprint: null,
    _textModelMenuProvider: null,
    isCommandRunning: false,
    _canMutateMenu: () => true,
    _hasActiveRecordingState: () => false,
    _hasLocalProcessingWorkflow: () => false,
    _processCleanupStatus: (result) => result,
    _terminateProcessesByGroup: () => "stopped",
    _tryTextModelsArgs: () => ["text-models"],
    _populateTextModelMenu: () => { textMenuUpdates += 1; },
    _spawnJson: (_args, callback) => {
      textCallbacks.push(callback);
      return {};
    },
    _sanitizeErrorMessage: (value) => String(value),
    _recordLifecycleError: () => {},
  };

  assert.equal(refreshTextModelMenu.call(textApplet, "ollama"), true);
  assert.equal(textCallbacks.length, 1);
  const staleTextToken = textApplet.textModelMenuRefreshToken;
  const currentTextToken = {};
  textApplet.textModelMenuRefreshToken = currentTextToken;
  const updatesBeforeStaleText = textMenuUpdates;
  textCallbacks[0]({ models: ["stale-model"] });
  assert.notEqual(staleTextToken, currentTextToken);
  assert.equal(textApplet.textModelMenuRefreshToken, currentTextToken);
  assert.equal(textMenuUpdates, updatesBeforeStaleText);

  const refreshStatus = loadAppletMethod(
    "_refreshStatus",
    "_hasCancelableRecordingWork",
    { value: 0 },
    { STATUS_COMMAND_TIMEOUT_MS: 10000 }
  );
  const statusCallbacks = [];
  let appliedStatusPayloads = 0;
  let statusPolls = 0;
  const statusApplet = {
    status: "idle",
    isCommandRunning: false,
    _statusCommandRunning: false,
    _statusRefreshToken: 0,
    cancelIntentActive: false,
    _spawnJson: (_args, callback) => {
      statusCallbacks.push(callback);
      return {};
    },
    _statusArgs: () => ["status"],
    _applyPayload: () => { appliedStatusPayloads += 1; },
    _scheduleStatusPoll: () => { statusPolls += 1; },
    _sanitizeErrorMessage: (value) => String(value),
    _setStatusPreservingRecording: () => {},
    _recordLifecycleError: () => {},
  };

  assert.equal(refreshStatus.call(statusApplet, false), false);
  assert.equal(statusCallbacks.length, 1);
  const staleStatusToken = statusApplet._statusCommandToken;
  const currentStatusToken = {};
  statusApplet._statusCommandToken = currentStatusToken;
  statusApplet._statusCommandRunning = true;
  statusCallbacks[0]({ status: "done" });
  assert.notEqual(staleStatusToken, currentStatusToken);
  assert.equal(statusApplet._statusCommandToken, currentStatusToken);
  assert.equal(statusApplet._statusCommandRunning, true);
  assert.equal(appliedStatusPayloads, 1);
  assert.equal(statusPolls, 0);
});

test("hotkey rebind retry is bounded and isolated per binding", () => {
  const retryPendingHotkeyRebinds = loadAppletMethod(
    "_retryPendingHotkeyRebinds",
    "_registerHotkey",
    { value: 0 },
    { HOTKEY_REBIND_MAX_RETRIES: 2 }
  );
  const toggleCallback = () => {};
  const primaryCallback = () => {};
  const registered = [];
  const blocked = [];
  const statusEvents = [];
  const pending = {
    "toggle-keybinding": { binding: "new-toggle", attempts: 2 },
    "primary-language-keybinding": { binding: "new-primary", attempts: 0 },
  };
  const applet = {
    _pendingHotkeyRebinds: pending,
    _lifecycleAllowsWork: () => true,
    _hotkeyName: (id) => id,
    _hotkeySpecs: () => [
      {
        id: "toggle-keybinding",
        key: "toggle-keybinding",
        propertyName: "toggleKeybinding",
        binding: "new-toggle",
        callback: toggleCallback,
      },
      {
        id: "primary-language-keybinding",
        key: "primary-language-keybinding",
        propertyName: "primaryLanguageKeybinding",
        binding: "new-primary",
        callback: primaryCallback,
      },
    ],
    _clearPendingHotkeyRebind(name) {
      delete this._pendingHotkeyRebinds[name];
      return !Object.prototype.hasOwnProperty.call(this._pendingHotkeyRebinds, name);
    },
    _disableHotkeyAfterRebindFailure: (name) => {
      blocked.push(name);
      return true;
    },
    _setHotkeyRuntimeBlocked: (id, value) => {
      if (value === true) {
        blocked.push(id);
      }
      return true;
    },
    _commitSettingValue: () => true,
    _registerHotkey: (id) => { registered.push(id); },
    _setStatusPreservingRecording: (...args) => statusEvents.push(args),
    _resourceRegistry: { hotkeys: {} },
    _hotkeyDefinitions: {},
    lastTranscript: "",
  };

  assert.equal(retryPendingHotkeyRebinds.call(applet), false);
  assert.deepEqual(registered, ["primary-language-keybinding"]);
  assert.equal(Object.prototype.hasOwnProperty.call(pending, "toggle-keybinding"), false);
  assert.equal(pending["primary-language-keybinding"].attempts, 1);

  assert.equal(retryPendingHotkeyRebinds.call(applet), false);
  assert.deepEqual(registered, ["primary-language-keybinding", "primary-language-keybinding"]);
  assert.equal(pending["primary-language-keybinding"].attempts, 2);

  assert.equal(retryPendingHotkeyRebinds.call(applet), true);
  assert.equal(Object.keys(pending).length, 0);
  assert.equal(statusEvents.length, 2);
  assert.equal(blocked.includes("toggle-keybinding"), true);
  assert.equal(blocked.includes("primary-language-keybinding"), true);
});

test("malformed orphan timer registry fails closed before allocation", () => {
  const scheduleTrackedTimer = loadAppletMethod(
    "_scheduleTrackedTimer",
    "_init",
    { value: 0 },
    {
      Mainloop: {
        timeout_add: () => { throw new Error("timer allocation must not run"); },
        timeout_add_seconds: () => { throw new Error("timer allocation must not run"); },
      },
    }
  );
  const reports = [];
  const applet = {
    _lifecycleAllowsWork: () => true,
    _orphanedTimers: null,
    _resourceRegistry: { timers: {}, timerGroups: {}, timerOwners: {} },
    _reportSubprocessError: (...args) => reports.push(args),
  };
  assert.equal(
    scheduleTrackedTimer.call(applet, "malformed", 1, () => false, false, "malformedTimer", "test"),
    0
  );
  assert.equal(reports.length, 1);
  assert.deepEqual(applet._resourceRegistry.timers, {});

  const retryOrphanedTimers = loadAppletMethod(
    "_retryOrphanedTimers",
    "_clearTrackedTimer",
    { value: 0 },
    { LIFECYCLE_REMOVED: "removed", LIFECYCLE_REMOVING: "removing" }
  );
  let sourceRemoveCalls = 0;
  const retryErrors = [];
  const retryApplet = {
    appletRemoved: false,
    lifecycleState: "running",
    _orphanedTimers: {},
    _resourceRegistry: { timers: {} },
    _recordLifecycleError: (...args) => retryErrors.push(args),
    _reportSubprocessError: (...args) => retryErrors.push(args),
  };
  const retryMainloop = { source_remove: () => { sourceRemoveCalls += 1; return true; } };
  const retryWithMainloop = loadAppletMethod(
    "_retryOrphanedTimers",
    "_clearTrackedTimer",
    { value: 0 },
    {
      Mainloop: retryMainloop,
      LIFECYCLE_REMOVED: "removed",
      LIFECYCLE_REMOVING: "removing",
    }
  );
  assert.equal(retryWithMainloop.call(retryApplet), false);
  assert.equal(retryErrors.length >= 1, true);
  assert.equal(sourceRemoveCalls, 0);
});

test("state callback fallback survives disabled error group", () => {
  const guardStateCallback = loadAppletMethod(
    "_guardStateCallback",
    "_handleInitializationFailure",
    { value: 0 }
  );
  let callbackCalls = 0;
  let safeLogCalls = 0;
  const applet = {
    _lifecycleAllowsWork: () => true,
    _lifecycleGroupEnabled: () => false,
    _safeLogError: () => { safeLogCalls += 1; },
    _runStateGuarded: () => { throw new Error("disabled group must not gate state callback"); },
  };
  const callback = guardStateCallback.call(
    applet,
    "journal-state",
    () => {
      callbackCalls += 1;
      return "state-result";
    },
    "fallback",
    "error-journal"
  );
  assert.equal(callback(), "state-result");
  assert.equal(callbackCalls, 1);
  assert.equal(safeLogCalls, 0);

  const failingCallback = guardStateCallback.call(
    applet,
    "journal-state",
    () => { throw new Error("journal callback failed"); },
    "fallback",
    "error-journal"
  );
  assert.equal(failingCallback(), "fallback");
  assert.equal(safeLogCalls, 1);

  applet._lifecycleAllowsWork = () => false;
  assert.equal(callback(), "fallback");
  assert.equal(callbackCalls, 1);
});
