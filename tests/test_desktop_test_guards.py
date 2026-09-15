from __future__ import annotations

import os
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DesktopTestGuardTests(unittest.TestCase):
    def test_real_e2e_requires_explicit_desktop_opt_in(self) -> None:
        source = (REPO_ROOT / "scripts" / "real-e2e-acceptance.sh").read_text(encoding="utf-8")
        self.assertIn('SOC_RUN_GUI_LIVE_TESTS:-0', source)
        self.assertIn('Refusing desktop acceptance test.', source)

    def test_crash_safety_requires_explicit_desktop_opt_in(self) -> None:
        script_path = REPO_ROOT / "scripts" / "applet-crash-safety.sh"
        source = script_path.read_text(encoding="utf-8")
        self.assertTrue(os.access(script_path, os.X_OK))
        self.assertIn('SOC_RUN_GUI_LIVE_TESTS:-0', source)
        self.assertIn('Refusing desktop crash-safety test.', source)


if __name__ == "__main__":
    unittest.main()
