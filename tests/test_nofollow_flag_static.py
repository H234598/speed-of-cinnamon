from __future__ import annotations

import unittest
from pathlib import Path


class NoFollowFlagStaticTests(unittest.TestCase):
    def test_source_never_uses_fail_open_no_follow_fallbacks(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src" / "speed_of_cinnamon"
        forbidden_fragments = (
            'getattr(os, "O_NOFOLLOW", 0)',
            'if nofollow_flag is None:',
            'if getattr(os, "O_NOFOLLOW", None) is None:',
        )
        for source_path in sorted(source_root.glob("*.py")):
            source = source_path.read_text(encoding="utf-8")
            for fragment in forbidden_fragments:
                with self.subTest(path=source_path, fragment=fragment):
                    self.assertNotIn(fragment, source)


if __name__ == "__main__":
    unittest.main()
