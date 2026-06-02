from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


def run_version(*args: str) -> str:
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / "scripts" / "next_version.py")]
    if "--add-commits" not in args:
        cmd.extend(["--add-commits", "1"])
    cmd.extend(args)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


class NextVersionTest(unittest.TestCase):
    def test_single_commit_keeps_patch(self) -> None:
        self.assertEqual(run_version("--base", "0.1.26"), "0.1.26")

    def test_hundred_commits_increase_patch(self) -> None:
        self.assertEqual(run_version("--base", "0.1.26", "--add-commits", "100"), "0.1.27")

    def test_minor_rolls_over_every_100_patches(self) -> None:
        self.assertEqual(run_version("--base", "0.1.99"), "0.2.0")

    def test_major_rolls_over_every_100_minors(self) -> None:
        self.assertEqual(run_version("--base", "0.99.0", "--add-commits", "10000"), "1.0.0")

    def test_feature_increase_moves_minor(self) -> None:
        self.assertEqual(
            run_version("--base", "0.1.25", "--feature"),
            "0.2.26",
        )

    def test_breaking_increase_resets_minor_and_patch(self) -> None:
        self.assertEqual(
            run_version("--base", "0.1.99", "--breaking"),
            "1.0.0",
        )


if __name__ == "__main__":
    unittest.main()
