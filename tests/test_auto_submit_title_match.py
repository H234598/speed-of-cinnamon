from pathlib import Path
import unittest


APPLET_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "files/speed-of-cinnamon@H234598/applet.js"
).read_text(encoding="utf-8")


class AutoSubmitTitleMatchTest(unittest.TestCase):
    def test_custom_markers_match_title_substrings_case_insensitively(self):
        self.assertIn(
            "if (this._windowIdentityValueMatchesMarker(title, key))",
            APPLET_SOURCE,
        )
        self.assertNotIn("if (title === key)", APPLET_SOURCE)


if __name__ == "__main__":
    unittest.main()
