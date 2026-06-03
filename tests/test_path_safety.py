from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import path_safety


class PathSafetyTest(unittest.TestCase):
    def test_atomic_write_creates_parent_without_pathlib_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "settings.json"
            with mock.patch.object(Path, "mkdir", side_effect=AssertionError("unsafe mkdir")):
                path_safety.write_text_atomically_without_following_symlinks(target, "{}")

            self.assertEqual(target.read_text(encoding="utf-8"), "{}")

    def test_atomic_write_rejects_symlink_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            link = root / "link"
            os.symlink(real, link)

            with self.assertRaises(OSError):
                path_safety.write_text_atomically_without_following_symlinks(link / "settings.json", "{}")


if __name__ == "__main__":
    unittest.main()
