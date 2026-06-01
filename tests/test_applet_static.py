from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APPLET_DIR = REPO_ROOT / "files" / "speed-of-cinnamon@H234598"


class AppletStaticTest(unittest.TestCase):
    def test_applet_auto_detects_user_and_system_backend(self) -> None:
        source = (APPLET_DIR / "applet.js").read_text(encoding="utf-8")

        self.assertIn('const SYSTEM_CLI = "/usr/bin/speed-of-cinnamon";', source)
        self.assertIn("GLib.file_test(DEFAULT_CLI, GLib.FileTest.IS_EXECUTABLE)", source)
        self.assertIn("GLib.file_test(SYSTEM_CLI, GLib.FileTest.IS_EXECUTABLE)", source)
        self.assertIn('return "speed-of-cinnamon";', source)
        self.assertNotIn("this.cliPath || DEFAULT_CLI", source)

    def test_backend_setting_documents_auto_detection_order(self) -> None:
        schema = json.loads((APPLET_DIR / "settings-schema.json").read_text(encoding="utf-8"))
        tooltip = schema["cli-path"]["tooltip"]

        self.assertIn("~/.local/bin/speed-of-cinnamon", tooltip)
        self.assertIn("/usr/bin/speed-of-cinnamon", tooltip)
