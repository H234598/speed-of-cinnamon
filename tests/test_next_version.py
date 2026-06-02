from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


def run_version(*args: str) -> str:
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / "scripts" / "next_version.py")]
    cmd.extend(args)
    result = subprocess.run(cmd, check=True, text=True, capture_output=True)
    return result.stdout.strip()


def run_version_fail(*args: str) -> int:
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / "scripts" / "next_version.py")]
    cmd.extend(args)
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    return result.returncode


def run_version_fail_stdout_stderr(*args: str) -> tuple[int, str]:
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / "scripts" / "next_version.py")]
    cmd.extend(args)
    result = subprocess.run(cmd, check=False, text=True, capture_output=True)
    return result.returncode, (result.stderr or "")


class NextVersionTest(unittest.TestCase):
    def test_single_commit_keeps_patch(self) -> None:
        self.assertEqual(run_version("--base", "0.1.26", "--add-commits", "1"), "0.1.26")

    def test_hundred_commits_increase_patch(self) -> None:
        self.assertEqual(run_version("--base", "0.1.26", "--add-commits", "100"), "0.1.27")

    def test_minor_rolls_over_every_100_patches(self) -> None:
        self.assertEqual(run_version("--base", "0.1.99", "--add-commits", "100"), "0.2.0")

    def test_default_mode_falls_back_to_last_version_tag(self) -> None:
        self.assertEqual(
            run_version("--base", "0.1.26", "--add-commits", "0"),
            "0.1.26",
        )

    def test_major_rolls_over_every_100_minors(self) -> None:
        self.assertEqual(run_version("--base", "0.99.0", "--add-commits", "10000"), "1.0.0")

    def test_feature_increase_moves_minor(self) -> None:
        self.assertEqual(
            run_version("--base", "0.1.25", "--feature"),
            "0.2.25",
        )

    def test_breaking_increase_resets_minor_and_patch(self) -> None:
        self.assertEqual(
            run_version("--base", "0.1.99", "--breaking"),
            "1.0.0",
        )

    def test_missing_from_tag_fails(self) -> None:
        self.assertNotEqual(run_version_fail("--from-tag", "v999.999.999"), 0)

    def test_missing_from_tag_prints_error(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--from-tag", "v999.999.999")
        self.assertNotEqual(code, 0)
        self.assertIn("error:", stderr)

    def test_from_tag_and_add_commits_are_mutually_exclusive(self) -> None:
        code, stderr = run_version_fail_stdout_stderr(
            "--from-tag",
            "0.1.20",
            "--add-commits",
            "10",
        )
        self.assertNotEqual(code, 0)
        self.assertIn("error:", stderr)

    def test_from_tag_without_prefix_is_accepted(self) -> None:
        self.assertEqual(run_version("--from-tag", "0.1.20", "--add-commits", "0"), "0.1.20")


if __name__ == "__main__":
    unittest.main()
