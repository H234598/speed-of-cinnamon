from __future__ import annotations

import unittest
from pathlib import Path


APPLET = Path(__file__).resolve().parents[1] / "files" / "speed-of-cinnamon@H234598" / "applet.js"


class AutoBackupHandoffStaticTest(unittest.TestCase):
    def test_recording_handoff_uses_private_settings_only_for_audio_backup(self) -> None:
        source = APPLET.read_text(encoding="utf-8")
        self.assertIn('args.push("--settings-json-stdin");', source)
        self.assertIn("_recordingSettingsInputOptionOrNull: function()", source)
        self.assertIn('toggleInputOption = typeof this._recordingSettingsInputOptionOrNull === "function"', source)
        self.assertIn('stopInputOption = typeof this._recordingSettingsInputOptionOrNull === "function"', source)
        self.assertIn('startInputOption = typeof this._recordingSettingsInputOptionOrNull === "function"', source)
        self.assertIn("}, toggleInputOption);", source)
        self.assertIn("}, stopInputOption);", source)
        self.assertIn("}, startInputOption);", source)
        self.assertIn("payload.automatic_backup.status === \"done\"", source)
        self.assertIn("payload.silence_detected === true", source)


if __name__ == "__main__":
    unittest.main()
