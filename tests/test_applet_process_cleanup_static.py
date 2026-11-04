from __future__ import annotations

import unittest
from pathlib import Path


APPLET_PATH = (
    Path(__file__).resolve().parents[1]
    / "files"
    / "speed-of-cinnamon@H234598"
    / "applet.js"
)


class AppletProcessCleanupStaticTests(unittest.TestCase):
    def test_false_cancel_accepts_stopped_gio_process_handles(self) -> None:
        source = APPLET_PATH.read_text(encoding="utf-8")
        self.assertIn("_processHandleIsStopped: function (process)", source)
        helper_start = source.index("_processHandleIsStopped: function (process)")
        helper_end = source.index("\n  _terminateProcess:", helper_start)
        helper_block = source[helper_start:helper_end]
        self.assertIn('typeof process.get_identifier === "function"', helper_block)
        self.assertIn('if (identifier !== "") {\n          return false;', helper_block)
        self.assertIn("get_if_exited() === true", helper_block)
        self.assertIn("get_if_signaled() === true", helper_block)
        self.assertLess(
            helper_block.index("get_identifier"), helper_block.index("get_if_exited")
        )
        for start_marker, end_marker, false_end_marker in (
            (
                "_terminateAllProcesses: function ()",
                "\n  _terminateProcessesByGroup:",
                "!this._terminateProcess(entry.process, entryResourceGroup)",
            ),
            (
                "_terminateProcessesByGroup: function (group, notifyCallback)",
                "\n  _cancelAllCancellables:",
                "} else if (!this._terminateProcess(entry.process))",
            ),
        ):
            start = source.index(start_marker)
            block = source[start : source.index(end_marker, start)]
            false_start = block.index("result === false")
            false_block = block[
                false_start : block.index(false_end_marker, false_start)
            ]
            self.assertIn("this._processHandleIsStopped(entry.process)", false_block)
            self.assertLess(
                false_block.index("this._processHandleIsStopped(entry.process)"),
                false_block.index("Process cancellation failed"),
            )

    def test_stopped_group_after_false_cancel_is_not_reported_as_failure(self) -> None:
        source = APPLET_PATH.read_text(encoding="utf-8")
        start = source.index(
            "_terminateProcessesByGroup: function (group, notifyCallback)"
        )
        end = source.index("\n  _hasTrackedProcessGroup:", start)
        block = source[start:end]
        cancel_start = block.index(
            "let result = entry.cancel(Boolean(notifyCallback));"
        )
        cancel_end = block.index(
            "cleanupSucceeded = !processCancellationPending;", cancel_start
        )
        cancel_block = block[cancel_start:cancel_end]

        pending_start = block.index("let markPending = () => {")
        pending_block = block[pending_start : block.index("\n    };", pending_start)]
        failed_start = block.index("let markFailed = (error) => {", pending_start)
        failed_block = block[failed_start : block.index("\n    };", failed_start)]
        self.assertLess(pending_start, failed_start)
        self.assertLess(failed_start, cancel_start)
        self.assertIn(
            'allSucceeded = false;\n      if (cleanupStatus !== "failed") {\n        cleanupStatus = "pending";',
            pending_block,
        )
        self.assertIn(
            'allSucceeded = false;\n      groupHasFailure = true;\n      cleanupStatus = "failed";',
            failed_block,
        )
        live_start = cancel_block.index('if (processGroupState === "live") {')
        stopped_guard = (
            "} else if (\n"
            "                !this._processHandleIsStopped(entry.process) &&\n"
            '                processGroupState !== "stopped"\n'
            "              )"
        )
        stopped_start = cancel_block.index(stopped_guard, live_start)
        live_block = cancel_block[live_start:stopped_start]
        self.assertIn("processCancellationPending = true;", live_block)
        self.assertLess(
            live_block.index("processCancellationPending = true;"),
            live_block.index("this._trackOrphanedProcess("),
        )
        self.assertIn(
            "if (\n"
            "                  this._trackOrphanedProcess(\n"
            "                    entry.process,\n"
            "                    entry.generation,\n"
            "                    entry.group,\n"
            "                    token,\n"
            "                    false,\n"
            "                  )\n"
            "                ) {\n"
            "                  markPending();\n"
            "                } else {\n"
            '                  markFailed(new Error("Pending process could not be tracked"));\n'
            "                }",
            live_block,
        )
        self.assertLess(
            live_start,
            stopped_start,
        )
        self.assertIn(
            "} else if (\n"
            "                !this._processHandleIsStopped(entry.process) &&\n"
            '                processGroupState !== "stopped"\n'
            "              ) {\n"
            '                throw new Error("Process cancellation failed");\n'
            "              }",
            cancel_block,
        )
        self.assertIn(stopped_guard, cancel_block)


if __name__ == "__main__":
    unittest.main()
