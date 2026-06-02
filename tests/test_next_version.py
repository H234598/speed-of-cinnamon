from __future__ import annotations

import subprocess
import sys
import unittest
import os
import tempfile
from pathlib import Path


def _run_version(*args: str, expect_ok: bool, cwd: Path | None = None, path: str | None = None) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / "scripts" / "next_version.py")]
    cmd.extend(args)
    env = os.environ.copy() if path is not None else None
    if path is not None:
        env["PATH"] = path
    result = subprocess.run(
        cmd,
        check=expect_ok,
        text=True,
        capture_output=True,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
    )
    return result


def run_version(*args: str) -> str:
    result = _run_version(*args, expect_ok=True)
    return result.stdout.strip()


def run_version_fail(*args: str) -> int:
    return _run_version(*args, expect_ok=False).returncode


def run_version_fail_stdout_stderr(*args: str, path: str | None = None, cwd: Path | None = None) -> tuple[int, str]:
    result = _run_version(*args, expect_ok=False, path=path, cwd=cwd)
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

    def test_invalid_base_version_prints_error(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "bad-version")
        self.assertNotEqual(code, 0)
        self.assertIn("error:", stderr)

    def test_negative_add_commits_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--add-commits", "-10")
        self.assertEqual(code, 2)
        self.assertIn("error", stderr.lower())

    def test_non_numeric_add_commits_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--add-commits", "oops")
        self.assertEqual(code, 2)
        self.assertIn("error", stderr.lower())

    def test_parse_version_variants_are_accepted(self) -> None:
        for version in [
            "0.1.20",
            "v0.1.20",
            "V0.1.20",
            " 0.1.20 ",
        ]:
            with self.subTest(version=version):
                self.assertEqual(run_version("--base", version, "--add-commits", "0"), "0.1.20")

    def test_parse_version_invalid_values_are_rejected(self) -> None:
        for version in [
            "",
            "1",
            "1.2",
            "1.2.3.4",
            "bad",
            "-1.2.3",
            "1.-2.3",
            "1.2.x",
        ]:
            with self.subTest(version=version):
                code, stderr = run_version_fail_stdout_stderr("--base", version, "--add-commits", "0")
                self.assertEqual(code, 2)
                self.assertIn("error", stderr.lower())

    def test_from_tag_without_prefix_is_accepted(self) -> None:
        self.assertEqual(run_version("--from-tag", "0.1.20"), "0.1.20")

    def test_feature_and_breaking_are_mutually_exclusive(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--feature", "--breaking")
        self.assertNotEqual(code, 0)
        self.assertIn("error:", stderr)

    def test_missing_git_command_returns_git_environment_error_code(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--add-commits", "10", path="/nonexistent")
        self.assertEqual(code, 3)
        self.assertIn("error:", stderr)

    def test_missing_pyproject_is_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            code, stderr = run_version_fail_stdout_stderr("--add-commits", "10", cwd=Path(tmpdir))
            self.assertEqual(code, 2)
            self.assertIn("error:", stderr)


if __name__ == "__main__":
    unittest.main()
