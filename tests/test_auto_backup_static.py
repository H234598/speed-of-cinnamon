from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLET_DIR = REPO_ROOT / "files" / "speed-of-cinnamon@H234598"


class AutoBackupStaticTest(unittest.TestCase):
    def test_schema_exposes_nonblocking_backup_controls(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))
        sections = schema["layout"]["main-page"]["sections"]
        self.assertIn("backup-section", sections)
        backup_keys = schema["layout"]["backup-section"]["keys"]
        for key in (
            "auto-backup-enabled",
            "auto-backup-directory",
            "auto-backup-config",
            "auto-backup-transcripts",
            "auto-backup-audio",
            "auto-backup-encryption",
            "auto-backup-on-success",
            "auto-backup-config-debounce-seconds",
            "auto-backup-retry-count",
            "auto-backup-retry-delay-seconds",
            "auto-backup-notify-success",
            "auto-backup-notify-error",
        ):
            self.assertIn(key, backup_keys)
        self.assertFalse(schema["auto-backup-enabled"]["default"])
        self.assertEqual(schema["auto-backup-encryption"]["default"], "keyring")
        self.assertEqual(schema["auto-backup-config-debounce-seconds"]["default"], 30)
        self.assertEqual(schema["auto-backup-retry-count"]["default"], 3)
        self.assertEqual(schema["auto-backup-retry-delay-seconds"]["default"], 30)
        self.assertIn("asynchronously", schema["auto-backup-enabled"]["tooltip"])
        self.assertIn("never blocks clipboard", schema["auto-backup-enabled"]["tooltip"])

    def test_applet_exports_and_runs_backup_outside_transcript_flow(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")
        for setting in (
            "auto-backup-enabled",
            "auto-backup-directory",
            "auto-backup-config",
            "auto-backup-transcripts",
            "auto-backup-audio",
            "auto-backup-encryption",
            "auto-backup-on-success",
            "auto-backup-config-debounce-seconds",
            "auto-backup-retry-count",
            "auto-backup-retry-delay-seconds",
            "auto-backup-notify-success",
            "auto-backup-notify-error",
        ):
            self.assertIn('"%s"' % setting, source)
        self.assertIn('"auto-backup-directory": "auto backup directory"', source)
        self.assertIn("_autoBackupArgs: function()", source)
        self.assertIn('this._cliCommand(), "backup", "create", "--directory", directory', source)
        self.assertIn('"--settings-json-stdin", "--json"', source)
        self.assertIn("_maybeStartAutoBackup: function(payload)", source)
        self.assertIn("_runAutoBackup: function(payload, attempt)", source)
        self.assertIn('resourceGroup: "backup"', source)
        self.assertIn("this._settingsSnapshotInputOptionOrNull(false, undefined, true)", source)
        self.assertIn("autoBackupConfigDebounceSeconds", source)
        self.assertIn("autoBackupRetryCount", source)
        self.assertIn("autoBackupRetryDelaySeconds", source)
        self.assertIn("this._maybeStartAutoBackup(payload);", source)
        self.assertIn("this._finishAppletTextInsert(payload);", source)
        self.assertLess(source.index("this._maybeStartAutoBackup(payload);"), source.index("this._finishAppletTextInsert(payload);"))


if __name__ == "__main__":
    unittest.main()
