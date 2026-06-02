from __future__ import annotations

import subprocess
import sys
import importlib.util
import unittest
import os
import tempfile
from pathlib import Path
from unittest import mock


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


def load_next_version_module() -> object:
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("next_version", root / "scripts" / "next_version.py")
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


next_version = load_next_version_module()


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

    def test_whitespace_add_commits_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--add-commits", "   ")
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

    def test_base_with_whitespace_is_accepted(self) -> None:
        self.assertEqual(run_version("--base", "  0.1.20  ", "--add-commits", "0"), "0.1.20")

    def test_base_with_only_whitespace_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "   ", "--add-commits", "0")
        self.assertEqual(code, 2)
        self.assertIn("error", stderr.lower())

    def test_base_empty_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "", "--add-commits", "0")
        self.assertEqual(code, 2)
        self.assertIn("error", stderr.lower())

    def test_add_commits_empty_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--add-commits", "")
        self.assertEqual(code, 2)
        self.assertIn("error", stderr.lower())

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

    def test_parse_version_rejects_non_string(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.parse_version(None)  # type: ignore[arg-type]

    def test_from_tag_without_prefix_is_accepted(self) -> None:
        self.assertEqual(run_version("--from-tag", "0.1.20"), "0.1.20")

    def test_from_tag_with_whitespace_is_accepted(self) -> None:
        self.assertEqual(run_version("--from-tag", "  0.1.20  "), "0.1.20")

    def test_from_tag_with_only_whitespace_is_rejected(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--from-tag", "   ")
        self.assertEqual(code, 2)
        self.assertIn("error", stderr.lower())

    def test_feature_and_breaking_are_mutually_exclusive(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--feature", "--breaking")
        self.assertNotEqual(code, 0)
        self.assertIn("error:", stderr)

    def test_missing_git_command_returns_git_environment_error_code(self) -> None:
        code, stderr = run_version_fail_stdout_stderr("--base", "0.1.20", "--add-commits", "10", path="/nonexistent")
        self.assertEqual(code, 3)
        self.assertIn("error:", stderr)

    def test_large_add_commits_rolls_many_levels(self) -> None:
        self.assertEqual(
            run_version("--base", "0.99.99", "--add-commits", "100000000"),
            "100.99.99",
        )

    def test_missing_pyproject_is_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            code, stderr = run_version_fail_stdout_stderr("--add-commits", "10", cwd=Path(tmpdir))
            self.assertEqual(code, 2)
            self.assertIn("error:", stderr)

    def test_malformed_pyproject_is_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "pyproject.toml").write_text("not a valid toml", encoding="utf-8")
            code, stderr = run_version_fail_stdout_stderr("--add-commits", "10", cwd=Path(tmpdir))
            self.assertEqual(code, 2)
            self.assertIn("error:", stderr)

    def test_read_current_version_rejects_empty_path(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.read_current_version(Path(""))

    def test_read_current_version_rejects_non_path(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.read_current_version("pyproject.toml")  # type: ignore[arg-type]

    def test_read_current_version_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(next_version.UserInputError):
                next_version.read_current_version(Path(tmpdir) / "missing.toml")

    def test_read_current_version_missing_version_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir, "pyproject.toml")
            path.write_text("[tool]\nname=\"x\"\n", encoding="utf-8")
            with self.assertRaises(next_version.UserInputError):
                next_version.read_current_version(path)

    def test_pyproject_missing_version_is_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "pyproject.toml").write_text("[project]\nname=\"x\"\n", encoding="utf-8")
            code, stderr = run_version_fail_stdout_stderr("--add-commits", "10", cwd=Path(tmpdir))
            self.assertEqual(code, 2)
            self.assertIn("error:", stderr)

    def test_pyproject_empty_project_section_is_user_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            Path(tmpdir, "pyproject.toml").write_text("[project]\n", encoding="utf-8")
            code, stderr = run_version_fail_stdout_stderr("--add-commits", "10", cwd=Path(tmpdir))
            self.assertEqual(code, 2)
            self.assertIn("error:", stderr)

    def test_add_patches_core_increment_steps(self) -> None:
        self.assertEqual(next_version.add_patches((0, 1, 99), 100), (0, 2, 99))

    def test_add_patches_rolls_to_major(self) -> None:
        self.assertEqual(next_version.add_patches((0, 99, 99), 100), (1, 0, 99))

    def test_add_patches_rejects_invalid_base_tuples(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.add_patches((0, 1), 10)  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version.add_patches((0, 1, 2, 3), 10)  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version.add_patches(("0", 1, 2), 10)  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version.add_patches((0, 1, 2), True)  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version.add_patches((True, 1, 2), 1)  # type: ignore[arg-type]

    def test_assert_non_negative_int_rejects_bool(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version._assert_non_negative_int("minor", True)  # type: ignore[attr-defined]
        with self.assertRaises(next_version.UserInputError):
            next_version._assert_non_negative_int("minor", "3")  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version._assert_non_negative_int("minor", -1)

    def test_to_version_rejects_bool_input(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.to_version(0, True, 1)  # type: ignore[arg-type]

    def test_assert_version_tuple_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version._assert_version_tuple((0, 1))  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version._assert_version_tuple(("0", 1, 2))  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version._assert_version_tuple((True, 1, 2))  # type: ignore[arg-type]

    def test_apply_feature_increase_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_feature_increase(-1, 0, 0)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_feature_increase(1, -1, 0)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_feature_increase(1, 1, -1)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_feature_increase(True, 1, 1)  # type: ignore[arg-type]
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_feature_increase(1, True, 1)  # type: ignore[arg-type]

    def test_apply_feature_increase_wraps_minor(self) -> None:
        self.assertEqual(next_version.apply_feature_increase(1, 99, 5), (2, 0, 5))

    def test_apply_breaking_change_resets_minor_and_patch(self) -> None:
        self.assertEqual(next_version.apply_breaking_change(9, 99, 99), (10, 0, 0))

    def test_apply_breaking_change_rejects_negative_values(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_breaking_change(-1, 0, 0)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_breaking_change(0, -1, 0)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_breaking_change(0, 0, -1)

    def test_apply_breaking_change_rejects_non_int_values(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_breaking_change("1", 0, 0)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_breaking_change(1, 2.0, 3)
        with self.assertRaises(next_version.UserInputError):
            next_version.apply_breaking_change(True, 0, 0)  # type: ignore[arg-type]

    def test_normalize_tag_rejects_empty_or_non_string(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.normalize_tag("")
        with self.assertRaises(next_version.UserInputError):
            next_version.normalize_tag("   ")
        with self.assertRaises(next_version.UserInputError):
            next_version.normalize_tag(None)  # type: ignore[arg-type]

    def test_to_version_rejects_invalid_inputs(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.to_version(-1, 0, 0)
        with self.assertRaises(next_version.UserInputError):
            next_version.to_version(1, "2", 3)  # type: ignore[arg-type]

    def test_tag_for_version_rejects_invalid_version(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.tag_for_version((0, 1))
        with self.assertRaises(next_version.UserInputError):
            next_version.tag_for_version(("0", 1, 2))  # type: ignore[arg-type]

    def test_parse_version_rejects_negative_segments(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.parse_version("1.-2.3")

    def test_commits_since_ref_parses_git_output(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="7\n", stderr=""),
        ) as run:
            self.assertEqual(next_version.commits_since_ref("v0.1.20"), 7)
            run.assert_called_once()

    def test_commits_since_ref_trims_ref(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="8\n", stderr=""),
        ) as run:
            self.assertEqual(next_version.commits_since_ref("  0.1.20  "), 8)
            run.assert_called_once_with(
                ["git", "rev-list", "--count", "0.1.20..HEAD"],
                check=True,
                text=True,
                capture_output=True,
            )

    def test_commits_since_ref_missing_git_is_git_error(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            side_effect=FileNotFoundError("git"),
        ):
            with self.assertRaises(next_version.GitEnvironmentError):
                next_version.commits_since_ref("v0.1.20")

    def test_commits_since_ref_calledprocesserror_without_stderr_is_handled(self) -> None:
        called = subprocess.CalledProcessError(1, ["git", "rev-list", "--count", "v0.1.20..HEAD"], stderr=None)
        with mock.patch.object(next_version.subprocess, "run", side_effect=called):
            with self.assertRaises(next_version.GitEnvironmentError):
                next_version.commits_since_ref("v0.1.20")

    def test_commits_since_ref_rejects_negative_count(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="-1\n", stderr=""),
        ):
            with self.assertRaises(next_version.GitEnvironmentError):
                next_version.commits_since_ref("v0.1.20")

    def test_commits_since_ref_rejects_empty_output(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="   \n", stderr=""),
        ):
            with self.assertRaises(next_version.GitEnvironmentError):
                next_version.commits_since_ref("v0.1.20")

    def test_commits_since_ref_rejects_empty_ref(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.commits_since_ref("")
        with self.assertRaises(next_version.UserInputError):
            next_version.commits_since_ref("   ")
        with self.assertRaises(next_version.UserInputError):
            next_version.commits_since_ref(None)  # type: ignore[arg-type]

    def test_commits_since_tag_normalizes_input(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="4\n", stderr=""),
        ) as run:
            self.assertEqual(next_version.commits_since_tag("  0.1.20  "), 4)
            run.assert_called_once_with(
                ["git", "rev-list", "--count", "v0.1.20..HEAD"],
                check=True,
                text=True,
                capture_output=True,
            )

    def test_commits_since_tag_rejects_non_string_input(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.commits_since_tag(None)  # type: ignore[arg-type]

    def test_tag_exists_checks_normalized_tag(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="v0.1.20\n", stderr=""),
        ) as run:
            self.assertTrue(next_version.tag_exists("0.1.20"))
            called_cmd = run.call_args.args[0]
            self.assertEqual(called_cmd, ["git", "tag", "-l", "v0.1.20"])

    def test_tag_exists_without_tag_is_false(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(args=["git"], returncode=0, stdout="v0.1.19\n", stderr=""),
        ):
            self.assertFalse(next_version.tag_exists("0.1.20"))
        with self.assertRaises(next_version.UserInputError):
            next_version.tag_exists(None)  # type: ignore[arg-type]

    def test_ensure_tag_exists_nonexistent_tag(self) -> None:
        with mock.patch.object(
            next_version,
            "tag_exists",
            return_value=False,
        ) as tag_exists:
            with self.assertRaises(next_version.UserInputError):
                next_version.ensure_tag_exists("0.1.20")
            tag_exists.assert_called_once_with("0.1.20")

    def test_ensure_tag_exists_existing_tag(self) -> None:
        with mock.patch.object(
            next_version,
            "tag_exists",
            return_value=True,
        ) as tag_exists:
            next_version.ensure_tag_exists("0.1.20")
            tag_exists.assert_called_once_with("0.1.20")

    def test_ensure_tag_exists_rejects_non_string_input(self) -> None:
        with self.assertRaises(next_version.UserInputError):
            next_version.ensure_tag_exists(None)  # type: ignore[arg-type]

    def test_tag_exists_missing_git_is_git_error(self) -> None:
        with mock.patch.object(
            next_version.subprocess,
            "run",
            side_effect=FileNotFoundError("git"),
        ):
            with self.assertRaises(next_version.GitEnvironmentError):
                next_version.tag_exists("0.1.20")

    def test_tag_exists_calledprocesserror_is_git_error(self) -> None:
        called = subprocess.CalledProcessError(1, ["git", "tag", "-l", "v0.1.20"], stderr="boom")
        with mock.patch.object(next_version.subprocess, "run", side_effect=called):
            with self.assertRaises(next_version.GitEnvironmentError):
                next_version.tag_exists("0.1.20")

    def test_main_uses_from_tag_when_provided(self) -> None:
        with (
            mock.patch.object(next_version, "parse_args") as parse_args,
            mock.patch.object(next_version, "ensure_tag_exists") as ensure_tag_exists,
            mock.patch.object(next_version, "commits_since_tag", return_value=2) as commits_since_tag,
            mock.patch.object(next_version, "add_patches", return_value=(2, 3, 4)) as add_patches,
            mock.patch.object(next_version, "print")
        ):
            parse_args.return_value = mock.Mock(from_tag="v9.9.9", add_commits=None, feature=False, breaking=False, base=None)
            next_version.main()
            ensure_tag_exists.assert_called_once_with("v9.9.9")
            commits_since_tag.assert_called_once_with("v9.9.9")
            add_patches.assert_called_once_with((0, 1, 0), 2)

    def test_main_uses_add_commits_when_provided(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "ensure_tag_exists") as ensure_tag_exists, \
            mock.patch.object(next_version, "commits_since_tag") as commits_since_tag, \
            mock.patch.object(next_version, "add_patches", return_value=(2, 3, 4)) as add_patches, \
            mock.patch.object(next_version, "print"):
            parse_args.return_value = mock.Mock(from_tag=None, add_commits=77, feature=False, breaking=False, base="1.2.3")
            next_version.main()
            self.assertFalse(ensure_tag_exists.called)
            self.assertFalse(commits_since_tag.called)
            add_patches.assert_called_once_with((1, 2, 3), 77)

    def test_main_prefers_feature_over_no_change(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "add_patches", return_value=(1, 2, 3)) as add_patches, \
            mock.patch.object(next_version, "apply_feature_increase", return_value=(1, 3, 3)) as apply_feature, \
            mock.patch.object(next_version, "print"):
            parse_args.return_value = mock.Mock(from_tag=None, add_commits=0, feature=True, breaking=False, base="1.2.0")
            next_version.main()
            add_patches.assert_called_once_with((1, 2, 0), 0)
            apply_feature.assert_called_once_with(1, 2, 3)

    def test_main_breaking_overrides_feature(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "add_patches", return_value=(1, 2, 3)) as add_patches, \
            mock.patch.object(next_version, "apply_feature_increase") as apply_feature, \
            mock.patch.object(next_version, "apply_breaking_change", return_value=(2, 0, 0)) as apply_breaking, \
            mock.patch.object(next_version, "print"):
            parse_args.return_value = mock.Mock(from_tag=None, add_commits=0, feature=False, breaking=True, base="1.2.0")
            next_version.main()
            self.assertFalse(apply_feature.called)
            apply_breaking.assert_called_once_with(1, 2, 3)

    def test_main_rejects_whitespace_from_tag(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "ensure_tag_exists") as ensure_tag_exists, \
            mock.patch.object(next_version, "read_current_version") as read_current_version:
            parse_args.return_value = mock.Mock(from_tag="   ", add_commits=None, feature=False, breaking=False, base=None)
            with self.assertRaises(next_version.UserInputError):
                next_version.main()
            self.assertFalse(ensure_tag_exists.called)
            self.assertFalse(read_current_version.called)

    def test_main_rejects_non_int_add_commits(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args:
            parse_args.return_value = mock.Mock(from_tag=None, add_commits=True, feature=False, breaking=False, base="0.1.20")
            with self.assertRaises(next_version.UserInputError):
                next_version.main()

    def test_main_rejects_non_string_base_and_from_tag(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "read_current_version") as read_current_version:
            parse_args.return_value = mock.Mock(from_tag="0.1.20", add_commits=0, feature=False, breaking=False, base=True)
            with self.assertRaises(next_version.UserInputError):
                next_version.main()
            self.assertFalse(read_current_version.called)

        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "read_current_version") as read_current_version:
            parse_args.return_value = mock.Mock(from_tag=123, add_commits=0, feature=False, breaking=False, base=None)
            with self.assertRaises(next_version.UserInputError):
                next_version.main()
            self.assertFalse(read_current_version.called)

    def test_main_falls_back_to_tag_not_existing(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "read_current_version", return_value=(3, 4, 5)) as read_current_version, \
            mock.patch.object(next_version, "tag_for_version", return_value="v3.4.5") as tag_for_version, \
            mock.patch.object(next_version, "tag_exists", return_value=False) as tag_exists, \
            mock.patch.object(next_version, "commits_since_tag") as commits_since_tag, \
            mock.patch.object(next_version, "add_patches", return_value=(3, 4, 6)) as add_patches, \
            mock.patch.object(next_version, "print"):
            parse_args.return_value = mock.Mock(from_tag=None, add_commits=None, feature=False, breaking=False, base=None)
            next_version.main()
            read_current_version.assert_called_once_with()
            tag_for_version.assert_called_once_with((3, 4, 5))
            tag_exists.assert_called_once_with("v3.4.5")
            self.assertFalse(commits_since_tag.called)
            add_patches.assert_called_once_with((3, 4, 5), 0)

    def test_main_uses_auto_tag_when_tag_exists(self) -> None:
        with mock.patch.object(next_version, "parse_args") as parse_args, \
            mock.patch.object(next_version, "read_current_version", return_value=(2, 0, 0)) as read_current_version, \
            mock.patch.object(next_version, "tag_for_version", return_value="v2.0.0") as tag_for_version, \
            mock.patch.object(next_version, "tag_exists", return_value=True) as tag_exists, \
            mock.patch.object(next_version, "commits_since_tag", return_value=42) as commits_since_tag, \
            mock.patch.object(next_version, "add_patches", return_value=(2, 1, 0)) as add_patches, \
            mock.patch.object(next_version, "print"):
            parse_args.return_value = mock.Mock(from_tag=None, add_commits=None, feature=False, breaking=False, base=None)
            next_version.main()
            read_current_version.assert_called_once_with()
            tag_exists.assert_called_once_with("v2.0.0")
            commits_since_tag.assert_called_once_with("v2.0.0")
            add_patches.assert_called_once_with((2, 0, 0), 42)

    def test_run_returns_zero_for_SystemExit_zero(self) -> None:
        with mock.patch.object(next_version, "main", side_effect=SystemExit(0)):
            self.assertEqual(next_version.run(), 0)

    def test_run_reports_systemexit_nonzero(self) -> None:
        with mock.patch.object(next_version, "main", side_effect=SystemExit(5)):
            result = next_version.run()
            self.assertEqual(result, 2)

    def test_run_reports_systemexit_non_int(self) -> None:
        with mock.patch.object(next_version, "main", side_effect=SystemExit("bad exit")):
            result = next_version.run()
            self.assertEqual(result, 2)

    def test_run_reports_unexpected_exception(self) -> None:
        with mock.patch.object(next_version, "main", side_effect=RuntimeError("boom")):
            result = next_version.run()
            self.assertEqual(result, 1)

    def test_run_reports_user_input_error(self) -> None:
        with mock.patch.object(
            next_version,
            "main",
            side_effect=next_version.UserInputError("bad input"),
        ):
            result = next_version.run()
            self.assertEqual(result, 2)

    def test_run_reports_next_version_error(self) -> None:
        with mock.patch.object(
            next_version,
            "main",
            side_effect=next_version.GitEnvironmentError("bad git"),
        ):
            result = next_version.run()
            self.assertEqual(result, 3)

    def test_run_reports_systemexit_error_code(self) -> None:
        with mock.patch.object(next_version, "main", side_effect=SystemExit(7)):
            result = next_version.run()
            self.assertEqual(result, 2)

    if __name__ == "__main__":
        unittest.main()
