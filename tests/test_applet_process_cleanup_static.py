from __future__ import annotations

import unittest
from pathlib import Path


APPLET_PATH = Path(__file__).resolve().parents[1] / "files" / "speed-of-cinnamon@H234598" / "applet.js"


class AppletProcessCleanupStaticTests(unittest.TestCase):
    def test_false_cancel_accepts_stopped_gio_process_handles(self) -> None:
        source = APPLET_PATH.read_text(encoding="utf-8")
        self.assertIn("_processHandleIsStopped: function(process)", source)
        self.assertIn("get_if_exited() === true", source)
        self.assertIn("get_if_signaled() === true", source)
        for start_marker, end_marker in (
            ("_terminateAllProcesses: function()", "\n  _terminateProcessesByGroup:"),
            ("_terminateProcessesByGroup: function(group, notifyCallback)", "\n  _cancelAllCancellables:"),
        ):
            start = source.index(start_marker)
            block = source[start : source.index(end_marker, start)]
            self.assertIn("this._processHandleIsStopped(entry.process)", block)
            self.assertLess(
                block.index("this._processHandleIsStopped(entry.process)"),
                block.index('Process cancellation failed'),
            )

    def test_stopped_group_after_false_cancel_is_not_reported_as_failure(self) -> None:
        source = APPLET_PATH.read_text(encoding="utf-8")
        start = source.index("_terminateProcessesByGroup: function(group, notifyCallback)")
        end = source.index("\n  _hasTrackedProcessGroup:", start)
        block = source[start:end]
        cancel_start = block.index("let result = entry.cancel(Boolean(notifyCallback));")
        cancel_end = block.index("cleanupSucceeded = !processCancellationPending;", cancel_start)
        cancel_block = block[cancel_start:cancel_end]

        self.assertEqual(cancel_block.count("allSucceeded = false;"), 1)
        self.assertLess(
            cancel_block.index("processGroupState === \"live\""),
            cancel_block.index("allSucceeded = false;"),
        )
        self.assertIn("processCancellationPending = true;\n                allSucceeded = false;", cancel_block)
        self.assertIn(
            'else if (!this._processHandleIsStopped(entry.process) && processGroupState !== "stopped")',
            cancel_block,
        )


if __name__ == "__main__":
    unittest.main()
