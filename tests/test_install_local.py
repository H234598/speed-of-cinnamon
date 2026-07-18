from __future__ import annotations

import json
import importlib.util
import os
import shutil
import stat
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallLocalTest(unittest.TestCase):
    def _load_safe_fs_module(self):
        spec = importlib.util.spec_from_file_location("safe_local_fs_test", REPO_ROOT / "scripts" / "safe-local-fs.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run_install_local(
        self,
        repo_root: Path,
        home: Path,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = env.get("PATH", "")
        env["SPEED_OF_CINNAMON_TEST_HOME"] = "1"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(repo_root / "scripts" / "install-local.sh")],
            cwd=repo_root,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def _run_uninstall_local(
        self,
        repo_root: Path,
        home: Path,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["PATH"] = env.get("PATH", "")
        env["SPEED_OF_CINNAMON_TEST_HOME"] = "1"
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            ["bash", str(repo_root / "scripts" / "uninstall-local.sh")],
            cwd=repo_root,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def _copy_minimal_repo(self, destination: Path) -> Path:
        repo_root = destination / "repo"
        (repo_root / "scripts").mkdir(parents=True)
        (repo_root / "docs" / "man").mkdir(parents=True)
        (repo_root / "files" / "speed-of-cinnamon@H234598").mkdir(parents=True)
        (repo_root / "src" / "speed_of_cinnamon").mkdir(parents=True)
        (repo_root / "src" / "speed_of_cinnamon" / "__init__.py").write_text("", encoding="utf-8")
        (repo_root / "docs" / "man" / "speed-of-cinnamon.1").write_text("man page\n", encoding="utf-8")
        (repo_root / "docs" / "man" / "speed-of-cinnamon-alarms.1").write_text("man page\n", encoding="utf-8")
        shutil.copy2(REPO_ROOT / "scripts" / "install-local.sh", repo_root / "scripts" / "install-local.sh")
        shutil.copy2(REPO_ROOT / "scripts" / "safe-local-fs.py", repo_root / "scripts" / "safe-local-fs.py")
        payload = destination / "payload.py"
        payload.write_text(
            "from pathlib import Path\n"
            "import os\n"
            "Path(os.environ['SPEED_OF_CINNAMON_PWNED_MARKER']).write_text('PWNED', encoding='utf-8')\n",
            encoding="utf-8",
        )
        (repo_root / "src" / "speed_of_cinnamon" / "cli.py").symlink_to(payload)
        return repo_root

    def _copy_installable_minimal_repo(self, destination: Path) -> Path:
        repo_root = destination / "repo"
        (repo_root / "scripts").mkdir(parents=True)
        (repo_root / "docs" / "man").mkdir(parents=True)
        (repo_root / "files" / "speed-of-cinnamon@H234598").mkdir(parents=True)
        (repo_root / "src" / "speed_of_cinnamon").mkdir(parents=True)
        (repo_root / "files" / "speed-of-cinnamon@H234598" / "metadata.json").write_text("{}", encoding="utf-8")
        (repo_root / "src" / "speed_of_cinnamon" / "__init__.py").write_text("", encoding="utf-8")
        (repo_root / "src" / "speed_of_cinnamon" / "cli.py").write_text("", encoding="utf-8")
        (repo_root / "docs" / "man" / "speed-of-cinnamon.1").write_text("man page\n", encoding="utf-8")
        (repo_root / "docs" / "man" / "speed-of-cinnamon-alarms.1").write_text("man page\n", encoding="utf-8")
        shutil.copy2(REPO_ROOT / "scripts" / "install-local.sh", repo_root / "scripts" / "install-local.sh")
        shutil.copy2(REPO_ROOT / "scripts" / "safe-local-fs.py", repo_root / "scripts" / "safe-local-fs.py")
        return repo_root

    def test_install_local_fails_cleanly_when_home_is_unset(self) -> None:
        env = os.environ.copy()
        env.pop("HOME", None)
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "install-local.sh")],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOME must be set.", result.stderr)
        self.assertNotIn("unbound variable", result.stderr)

    def test_uninstall_local_fails_cleanly_when_home_is_unset(self) -> None:
        env = os.environ.copy()
        env.pop("HOME", None)
        result = subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "uninstall-local.sh")],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HOME must be set.", result.stderr)
        self.assertNotIn("unbound variable", result.stderr)

    def test_install_local_refuses_symlinked_python_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            marker = tmp_path / "install-local-pwned-marker"
            result = self._run_install_local(
                repo_root,
                home,
                {"SPEED_OF_CINNAMON_PWNED_MARKER": str(marker)},
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("refusing to install unsafe python package source tree", result.stderr)
            self.assertFalse(marker.exists(), "symlinked cli.py should not have been imported")

    def test_install_local_still_installs_regular_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            result = self._run_install_local(REPO_ROOT, home)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue((home / ".local" / "bin" / "speed-of-cinnamon").exists())
            self.assertTrue(
                (home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon" / "cli.py").exists()
            )

    def test_install_local_reinstalls_existing_targets_without_cross_device_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            first = self._run_install_local(REPO_ROOT, home)
            self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)

            second = self._run_install_local(REPO_ROOT, home)

            self.assertEqual(second.returncode, 0, msg=second.stdout + second.stderr)
            self.assertNotIn("Invalid cross-device link", second.stderr)
            self.assertFalse(list((home / ".local" / "share" / "speed-of-cinnamon").glob("install-stage-*")))

    def test_install_local_preserves_existing_applet_when_staging_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            applet_target.mkdir(parents=True)
            marker = applet_target / "existing.txt"
            marker.write_text("old install\n", encoding="utf-8")
            (repo_root / "files" / "speed-of-cinnamon@H234598" / "bad-link").symlink_to(tmp_path / "payload")

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "old install\n")

    def test_install_local_restores_existing_target_when_backup_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            applet_target.mkdir(parents=True)
            marker = applet_target / "existing.txt"
            marker.write_text("old install\n", encoding="utf-8")

            real_helper = repo_root / "scripts" / "safe-local-fs-real.py"
            shutil.copy2(repo_root / "scripts" / "safe-local-fs.py", real_helper)
            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"real_helper = Path({str(real_helper)!r})\n"
                "result = subprocess.run([sys.executable, str(real_helper), *sys.argv[1:]], check=False)\n"
                "if result.returncode:\n"
                "    raise SystemExit(result.returncode)\n"
                "if len(sys.argv) > 4 and sys.argv[1] == 'replace' and 'cinnamon/applets/speed-of-cinnamon@H234598' in sys.argv[3]:\n"
                "    raise SystemExit(77)\n",
                encoding="utf-8",
            )

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("failed to back up existing applet", result.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(marker.read_text(encoding="utf-8"), "old install\n")
            self.assertEqual(list((home / ".local" / "share" / "speed-of-cinnamon").glob("install-stage-*")), [])

    def test_install_local_removes_new_targets_when_late_activation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            bad_target = home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon.1"
            bad_target.parent.mkdir(parents=True)
            bad_target.symlink_to(tmp_path / "payload")

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("refusing to follow symlink during install", result.stderr)
            self.assertFalse(
                (home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598").exists()
            )
            self.assertFalse(
                (home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon").exists()
            )
            self.assertFalse((home / ".local" / "bin" / "speed-of-cinnamon").exists())
            self.assertTrue(bad_target.is_symlink())

    def test_install_local_preserves_changed_target_and_recovery_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            applet_target.mkdir(parents=True)
            (applet_target / "existing.txt").write_text("old install\n", encoding="utf-8")
            bad_target = home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon.1"
            bad_target.parent.mkdir(parents=True)
            bad_target.symlink_to(tmp_path / "payload")
            trigger = tmp_path / "race-triggered"
            raced_target = tmp_path / "raced-applet"

            real_helper = repo_root / "scripts" / "safe-local-fs-real.py"
            shutil.copy2(repo_root / "scripts" / "safe-local-fs.py", real_helper)
            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"real_helper = Path({str(real_helper)!r})\n"
                "result = subprocess.run([sys.executable, str(real_helper), *sys.argv[1:]], check=False)\n"
                "if result.returncode:\n"
                "    raise SystemExit(result.returncode)\n"
                f"target = Path({str(applet_target)!r})\n"
                f"trigger = Path({str(trigger)!r})\n"
                f"raced_target = Path({str(raced_target)!r})\n"
                "if len(sys.argv) > 4 and sys.argv[1] == 'replace' and sys.argv[2] == 'install' and '/share/speed-of-cinnamon@H234598' in sys.argv[3] and sys.argv[4] == str(target) and not trigger.exists():\n"
                "    target.rename(raced_target)\n"
                "    target.mkdir()\n"
                "    trigger.write_text(str(target.stat().st_ino), encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("preserving install recovery workspace", result.stderr)
            self.assertEqual(applet_target.stat().st_ino, int(trigger.read_text(encoding="utf-8")))
            stages = list((home / ".local" / "share" / "speed-of-cinnamon").glob("install-stage-*"))
            self.assertEqual(len(stages), 1)
            recovery = stages[0] / "rollback" / ".applet"
            self.assertEqual((recovery / "existing.txt").read_text(encoding="utf-8"), "old install\n")
            self.assertTrue(bad_target.is_symlink())

    def test_install_local_rejects_target_changed_before_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            applet_target.mkdir(parents=True)
            (applet_target / "old.txt").write_text("old\n", encoding="utf-8")
            trigger = tmp_path / "race-triggered"
            raced_target = tmp_path / "raced-applet"

            real_helper = repo_root / "scripts" / "safe-local-fs-real.py"
            shutil.copy2(repo_root / "scripts" / "safe-local-fs.py", real_helper)
            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"real_helper = Path({str(real_helper)!r})\n"
                "result = subprocess.run([sys.executable, str(real_helper), *sys.argv[1:]], check=False)\n"
                "if result.returncode:\n"
                "    raise SystemExit(result.returncode)\n"
                f"target = Path({str(applet_target)!r})\n"
                f"trigger = Path({str(trigger)!r})\n"
                f"raced_target = Path({str(raced_target)!r})\n"
                "if len(sys.argv) > 3 and sys.argv[1] == 'identity' and sys.argv[3] == str(target) and not trigger.exists():\n"
                "    target.rename(raced_target)\n"
                "    target.mkdir()\n"
                "    (target / 'foreign.txt').write_text('foreign\\n', encoding='utf-8')\n"
                "    trigger.write_text('1', encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(trigger.exists())
            self.assertEqual((applet_target / "foreign.txt").read_text(encoding="utf-8"), "foreign\n")
            self.assertEqual((raced_target / "old.txt").read_text(encoding="utf-8"), "old\n")

    def test_install_local_refuses_hardlinked_man_page_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            hardlink_source = tmp_path / "hardlinked-man-source"
            man_page = repo_root / "docs" / "man" / "speed-of-cinnamon.1"
            home.mkdir()
            hardlink_source.write_text("man page\n", encoding="utf-8")
            man_page.unlink()
            os.link(hardlink_source, man_page)

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("refusing to use hardlinked man page source during install", result.stderr)

    def test_safe_fs_atomic_write_keeps_replaced_target_after_postcheck_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            target = Path(tmp) / "target.txt"
            target.write_text("old\n", encoding="utf-8")

            with mock.patch.object(module, "_check_leaf", side_effect=RuntimeError("postcheck failed")):
                with self.assertRaisesRegex(RuntimeError, "postcheck failed"):
                    module._write_bytes_atomic(target, b"restored\n", 0o600, action="install")

            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "restored\n")

    def test_safe_fs_install_tree_rejects_source_mutation_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("safe\n", encoding="utf-8")
            real_copytree = module.shutil.copytree
            original_stat = (source / "payload.txt").stat()

            def copytree_with_source_mutation(src: Path, dst: Path, **kwargs: object) -> Path:
                (source / "payload.txt").write_text("muted\n", encoding="utf-8")
                os.utime(source / "payload.txt", (original_stat.st_atime, original_stat.st_mtime))
                return real_copytree(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module.shutil, "copytree", side_effect=copytree_with_source_mutation):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_rejects_staged_payload_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("safe\n", encoding="utf-8")
            real_copytree = module.shutil.copytree

            def copytree_with_staged_payload_corruption(src: Path, dst: Path, **kwargs: object) -> Path:
                result = real_copytree(src, dst, **kwargs)
                (dst / "payload.txt").write_text("evaded\n", encoding="utf-8")
                return result

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module.shutil, "copytree", side_effect=copytree_with_staged_payload_corruption):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_rollback_preserves_replaced_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            raced_target = root / "raced-target"
            source.mkdir()
            target.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            (target / "payload.txt").write_text("old\n", encoding="utf-8")
            real_check_leaf = module._check_leaf

            def replace_target_then_fail(
                parent_fd: int,
                name: str,
                path: Path,
                *,
                action: str,
                kind: str,
                must_exist: bool,
            ) -> None:
                if path == target:
                    target.rename(raced_target)
                    target.mkdir()
                    (target / "foreign.txt").write_text("foreign\n", encoding="utf-8")
                    raise OSError("post-activation target check failed")
                real_check_leaf(parent_fd, name, path, action=action, kind=kind, must_exist=must_exist)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_check_leaf", side_effect=replace_target_then_fail):
                with self.assertRaisesRegex(OSError, "post-activation target check failed"):
                    module.cmd_install_tree(args)

            self.assertEqual((target / "foreign.txt").read_text(encoding="utf-8"), "foreign\n")
            self.assertEqual((raced_target / "payload.txt").read_text(encoding="utf-8"), "new\n")
            backups = list(root.glob(".target.*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_install_tree_does_not_move_destination_changed_after_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            raced_target = root / "raced-target"
            source.mkdir()
            target.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            (target / "payload.txt").write_text("old\n", encoding="utf-8")
            real_lstat_at = module._lstat_at
            destination_checks = 0

            def lstat_and_replace_destination(parent_fd: int, name: str) -> os.stat_result | None:
                nonlocal destination_checks
                result = real_lstat_at(parent_fd, name)
                if (
                    name == target.name
                    and result is not None
                    and Path(os.readlink(f"/proc/self/fd/{parent_fd}")) == root
                ):
                    destination_checks += 1
                    if destination_checks == 2:
                        target.rename(raced_target)
                        target.mkdir()
                        (target / "foreign.txt").write_text("foreign\n", encoding="utf-8")
                        return real_lstat_at(parent_fd, name)
                return result

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_lstat_at", side_effect=lstat_and_replace_destination):
                with self.assertRaisesRegex(OSError, "destination changed"):
                    module.cmd_install_tree(args)

            self.assertEqual((source / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertEqual((target / "foreign.txt").read_text(encoding="utf-8"), "foreign\n")
            self.assertEqual((raced_target / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_install_tree_restores_backup_when_backup_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            (target / "payload.txt").write_text("old\n", encoding="utf-8")
            real_fsync_directory_fd = module._fsync_directory_fd
            fsync_calls = 0

            def fail_on_backup_fsync(fd: int, *, action: str) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 2:
                    raise OSError("backup directory fsync failed")
                real_fsync_directory_fd(fd, action=action)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_fsync_directory_fd", side_effect=fail_on_backup_fsync):
                with self.assertRaisesRegex(OSError, "backup directory fsync failed"):
                    module.cmd_install_tree(args)

            self.assertEqual((target / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertEqual(list(root.glob(".target.*.backup")), [])
            self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_install_tree_removes_new_target_when_activation_fsync_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            real_fsync_directory_fd = module._fsync_directory_fd
            fsync_calls = 0

            def fail_on_activation_fsync(fd: int, *, action: str) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 2:
                    raise OSError("activation directory fsync failed")
                real_fsync_directory_fd(fd, action=action)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_fsync_directory_fd", side_effect=fail_on_activation_fsync):
                with self.assertRaisesRegex(OSError, "activation directory fsync failed"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".target.*.backup")), [])
            self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_install_tree_preserves_backup_when_rollback_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            (target / "payload.txt").write_text("old\n", encoding="utf-8")
            real_fsync_directory_fd = module._fsync_directory_fd
            real_replace = module.os.replace
            fsync_calls = 0

            def fail_after_activation(fd: int, *, action: str) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 3:
                    raise OSError("post-activation fsync failed")
                real_fsync_directory_fd(fd, action=action)

            def fail_backup_restore(src: str, dst: str, **kwargs: object) -> None:
                if ".backup" in src:
                    raise OSError("backup restore failed")
                real_replace(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with (
                mock.patch.object(module, "_fsync_directory_fd", side_effect=fail_after_activation),
                mock.patch.object(module.os, "replace", side_effect=fail_backup_restore),
            ):
                with self.assertRaisesRegex(OSError, "post-activation fsync failed"):
                    module.cmd_install_tree(args)

            backups = list(root.glob(".target.*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "payload.txt").read_text(encoding="utf-8"), "old\n")
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_remove_dir_requires_symlink_safe_rmtree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            (target / "payload.txt").write_text("safe\n", encoding="utf-8")

            args = module.argparse.Namespace(action="install", path=str(target), kind="dir")
            with mock.patch.object(module.shutil.rmtree, "avoids_symlink_attacks", False):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_remove(args)

            self.assertTrue(target.exists())

    def test_safe_fs_write_wrapper_fsyncs_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            target = Path(tmp) / "soc-wrapper"
            fsynced_modes: list[int] = []
            real_fsync = module.os.fsync

            def record_fsync(fd: int) -> None:
                fsynced_modes.append(module.os.fstat(fd).st_mode)
                real_fsync(fd)

            args = module.argparse.Namespace(
                action="install",
                dst=str(target),
                python_path="/tmp/soc-package",
                python_executable="/usr/bin/python3",
            )
            with mock.patch.object(module.os, "fsync", side_effect=record_fsync):
                module.cmd_write_wrapper(args)

            self.assertTrue(target.exists())
            self.assertTrue(any(stat.S_ISREG(mode) for mode in fsynced_modes))
            self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsynced_modes))

    def test_safe_fs_copy_file_fsyncs_file_and_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            fsynced_modes: list[int] = []
            real_fsync = module.os.fsync

            def record_fsync(fd: int) -> None:
                fsynced_modes.append(module.os.fstat(fd).st_mode)
                real_fsync(fd)

            args = module.argparse.Namespace(
                action="install",
                src=str(source),
                dst=str(target),
                mode="0644",
                dst_must_not_exist=False,
            )
            with mock.patch.object(module.os, "fsync", side_effect=record_fsync):
                module.cmd_copy_file(args)

            self.assertEqual(target.read_text(encoding="utf-8"), "safe\n")
            self.assertTrue(any(stat.S_ISREG(mode) for mode in fsynced_modes))
            self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsynced_modes))

    def test_safe_fs_remove_file_fsyncs_parent_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            target = Path(tmp) / "target.txt"
            target.write_text("safe\n", encoding="utf-8")
            fsynced_modes: list[int] = []
            real_fsync = module.os.fsync

            def record_fsync(fd: int) -> None:
                fsynced_modes.append(module.os.fstat(fd).st_mode)
                real_fsync(fd)

            args = module.argparse.Namespace(action="install", path=str(target), kind="file")
            with mock.patch.object(module.os, "fsync", side_effect=record_fsync):
                module.cmd_remove(args)

            self.assertFalse(target.exists())
            self.assertTrue(any(stat.S_ISDIR(mode) for mode in fsynced_modes))

    def test_safe_fs_copy_file_rejects_in_place_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            real_open = module.os.open
            mutated = False

            def open_and_mutate(*args: object, **kwargs: object) -> int:
                nonlocal mutated
                fd = real_open(*args, **kwargs)
                if (
                    not mutated
                    and args
                    and args[0] == source.name
                    and kwargs.get("dir_fd") is not None
                    and (args[1] & module.os.O_NOFOLLOW)
                ):
                    source.write_text("muted\n", encoding="utf-8")
                    mutated = True
                return fd

            args = module.argparse.Namespace(
                action="install",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=False,
            )
            with mock.patch.object(module.os, "open", side_effect=open_and_mutate):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_copy_file(args)

            self.assertFalse(target.exists())

    def test_safe_fs_copy_file_rejects_hardlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            sibling = root / "sibling.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            os.link(source, sibling)

            args = module.argparse.Namespace(
                action="install",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=False,
            )
            with self.assertRaisesRegex(SystemExit, "1"):
                module.cmd_copy_file(args)

            self.assertFalse(target.exists())

    def test_safe_fs_replace_file_rejects_hardlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            sibling = root / "sibling.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            os.link(source, sibling)

            args = module.argparse.Namespace(
                action="build-dist",
                src=str(source),
                dst=str(target),
                src_kind="file",
                dst_must_not_exist=False,
            )
            with self.assertRaisesRegex(SystemExit, "1"):
                module.cmd_replace(args)

            self.assertTrue(source.exists())
            self.assertFalse(target.exists())

    def test_safe_fs_replace_file_rejects_source_mutation_before_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            target.write_text("old\n", encoding="utf-8")
            real_check_leaf = module._check_leaf
            source_checks = 0

            def check_leaf(parent_fd: int, name: str, path: Path, *, action: str, kind: str, must_exist: bool) -> None:
                nonlocal source_checks
                real_check_leaf(parent_fd, name, path, action=action, kind=kind, must_exist=must_exist)
                if path == source and kind == "file":
                    source_checks += 1
                    if source_checks == 2:
                        source.write_text("mutated payload\n", encoding="utf-8")

            args = module.argparse.Namespace(
                action="build-dist",
                src=str(source),
                dst=str(target),
                src_kind="file",
                dst_must_not_exist=False,
            )
            with mock.patch.object(module, "_check_leaf", side_effect=check_leaf):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_replace(args)

            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

    def test_safe_fs_copy_file_rejects_source_exchange_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            replacement = root / "replacement.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            replacement.write_text("evil\n", encoding="utf-8")
            real_open = module.os.open
            mutated = False

            def open_and_replace(*args: object, **kwargs: object) -> int:
                nonlocal mutated
                fd = real_open(*args, **kwargs)
                if (
                    not mutated
                    and args
                    and args[0] == source.name
                    and kwargs.get("dir_fd") is not None
                    and (args[1] & module.os.O_NOFOLLOW)
                ):
                    replacement.replace(source)
                    mutated = True
                return fd

            args = module.argparse.Namespace(
                action="install",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=False,
            )
            with mock.patch.object(module.os, "open", side_effect=open_and_replace):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_copy_file(args)

            self.assertFalse(target.exists())

    def test_safe_fs_copy_file_can_reject_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("safe\n", encoding="utf-8")
            target.write_text("old\n", encoding="utf-8")

            args = module.argparse.Namespace(
                action="build-snap",
                src=str(source),
                dst=str(target),
                mode="0644",
                dst_must_not_exist=True,
            )
            with self.assertRaisesRegex(SystemExit, "1"):
                module.cmd_copy_file(args)

            self.assertEqual(target.read_text(encoding="utf-8"), "old\n")

    def test_safe_fs_install_tree_copies_with_symlink_preservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            outside = root / "outside.txt"
            source.mkdir()
            payload = source / "payload.txt"
            payload.write_text("safe\n", encoding="utf-8")
            outside.write_text("outside\n", encoding="utf-8")
            real_copytree = module.shutil.copytree
            seen: dict[str, bool] = {}

            def copytree_with_symlink_race(src: Path, dst: Path, **kwargs: object) -> Path:
                seen["symlinks"] = bool(kwargs.get("symlinks"))
                payload.unlink()
                payload.symlink_to(outside)
                return real_copytree(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module.shutil, "copytree", side_effect=copytree_with_symlink_race):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

            self.assertTrue(seen.get("symlinks"))
            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_rejects_symlinked_source_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            real_parent = root / "real"
            source = real_parent / "source"
            target = root / "target"
            real_parent.mkdir()
            source.mkdir()
            (source / "payload.txt").write_text("safe\n", encoding="utf-8")
            link_parent = root / "link"
            link_parent.symlink_to(real_parent, target_is_directory=True)

            args = module.argparse.Namespace(action="install", source=str(link_parent / "source"), target=str(target), label="tree")
            with self.assertRaisesRegex(SystemExit, "1"):
                module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_install_local_refuses_symlinked_home_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            outside = tmp_path / "outside"
            home.mkdir()
            outside.mkdir()
            (home / ".local").symlink_to(outside, target_is_directory=True)

            result = self._run_install_local(REPO_ROOT, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("refusing to follow symlink during install", result.stderr)
            self.assertFalse((outside / "bin" / "speed-of-cinnamon").exists())

    def test_install_local_rejects_relative_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()

            result = self._run_install_local(
                repo_root,
                home,
                {"TMPDIR": "bad-relative"},
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("temporary root must be an absolute path", result.stderr)

    def test_install_local_rejects_symlinked_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            bad_tmp_root = tmp_path / "unsafe-tmp"
            bad_tmp_target = tmp_path / "tmp-target"
            bad_tmp_target.mkdir()
            bad_tmp_root.symlink_to(bad_tmp_target, target_is_directory=True)

            result = self._run_install_local(
                repo_root,
                home,
                {"TMPDIR": str(bad_tmp_root)},
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("temporary root must not be a symlink", result.stderr)

    def test_installed_wrapper_uses_install_path_not_runtime_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            project_version = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
                "version"
            ]
            install_home = tmp_path / "install-home"
            runtime_home = tmp_path / "runtime-home"
            install_home.mkdir()
            runtime_home.mkdir()
            result = self._run_install_local(REPO_ROOT, install_home)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            env = os.environ.copy()
            env["HOME"] = str(runtime_home)
            wrapper = install_home / ".local" / "bin" / "speed-of-cinnamon"
            wrapper_source = wrapper.read_text(encoding="utf-8")
            version_result = subprocess.run(
                [str(wrapper), "--version"],
                env=env,
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(version_result.returncode, 0, msg=version_result.stdout + version_result.stderr)
        self.assertIn(f"speed-of-cinnamon {project_version}", version_result.stdout)
        self.assertNotIn("command -v -- python3", wrapper_source)
        exec_lines = [line for line in wrapper_source.splitlines() if line.startswith("exec ")]
        self.assertEqual(len(exec_lines), 1)
        self.assertTrue(exec_lines[0].startswith("exec /"))
        self.assertIn(' -m speed_of_cinnamon.cli "$@"', exec_lines[0])

    def test_install_local_does_not_use_path_mv_for_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fake_bin = tmp_path / "fake-bin"
            fake_bin.mkdir()
            marker = tmp_path / "mv-marker"
            python_marker = tmp_path / "python-marker"
            fake_python = fake_bin / "python3"
            fake_python.write_text(
                "#!/usr/bin/env bash\n"
                f"printf used > {str(python_marker)!r}\n"
                "exit 77\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)
            fake_mv = fake_bin / "mv"
            fake_mv.write_text(
                "#!/usr/bin/env bash\n"
                f"printf used > {str(marker)!r}\n"
                "exit 77\n",
                encoding="utf-8",
            )
            fake_mv.chmod(0o755)
            home = tmp_path / "home"
            home.mkdir()

            result = self._run_install_local(REPO_ROOT, home, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"})

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(python_marker.exists())
            self.assertTrue((home / ".local" / "bin" / "speed-of-cinnamon").exists())

    def test_uninstall_local_removes_installed_code_but_preserves_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            install_result = self._run_install_local(REPO_ROOT, home)
            self.assertEqual(install_result.returncode, 0, msg=install_result.stdout + install_result.stderr)

            data_dir = home / ".local" / "share" / "speed-of-cinnamon"
            model_file = data_dir / "models" / "whisper.cpp" / "ggml-base.bin"
            alarm_file = data_dir / "alarms.json"
            model_file.parent.mkdir(parents=True)
            alarm_file.write_text("[]\n", encoding="utf-8")
            model_file.write_text("model\n", encoding="utf-8")

            uninstall_result = self._run_uninstall_local(REPO_ROOT, home)

            self.assertEqual(uninstall_result.returncode, 0, msg=uninstall_result.stdout + uninstall_result.stderr)
            self.assertFalse((home / ".local" / "bin" / "speed-of-cinnamon").exists())
            self.assertFalse((home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598").exists())
            self.assertFalse((home / ".local" / "share" / "speed-of-cinnamon" / "python").exists())
            self.assertFalse((home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon.1").exists())
            self.assertFalse((home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon-alarms.1").exists())
            self.assertTrue(model_file.exists())
            self.assertTrue(alarm_file.exists())

    def test_uninstall_local_preserves_non_empty_data_dir_with_ignore_non_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            install_result = self._run_install_local(REPO_ROOT, home)
            self.assertEqual(install_result.returncode, 0, msg=install_result.stdout + install_result.stderr)

            data_dir = home / ".local" / "share" / "speed-of-cinnamon"
            models_dir = data_dir / "models" / "whisper.cpp"
            alarm_file = data_dir / "alarms.json"
            models_dir.mkdir(parents=True)
            alarm_file.write_text("[]\n", encoding="utf-8")
            (models_dir / "ggml-base.bin").write_text("model\n", encoding="utf-8")

            uninstall_result = self._run_uninstall_local(REPO_ROOT, home)

            self.assertEqual(uninstall_result.returncode, 0, msg=uninstall_result.stdout + uninstall_result.stderr)
            self.assertTrue(models_dir.exists())
            self.assertTrue(alarm_file.exists())

    def test_uninstall_local_preserves_target_replaced_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            applet_target.mkdir(parents=True)
            (applet_target / "old.txt").write_text("old\n", encoding="utf-8")
            trigger = tmp_path / "race-triggered"
            raced_target = tmp_path / "raced-applet"

            real_helper = repo_root / "scripts" / "safe-local-fs-real.py"
            shutil.copy2(repo_root / "scripts" / "safe-local-fs.py", real_helper)
            shutil.copy2(REPO_ROOT / "scripts" / "uninstall-local.sh", repo_root / "scripts" / "uninstall-local.sh")
            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"real_helper = Path({str(real_helper)!r})\n"
                "result = subprocess.run([sys.executable, str(real_helper), *sys.argv[1:]], check=False)\n"
                "if result.returncode:\n"
                "    raise SystemExit(result.returncode)\n"
                f"target = Path({str(applet_target)!r})\n"
                f"trigger = Path({str(trigger)!r})\n"
                f"raced_target = Path({str(raced_target)!r})\n"
                "if len(sys.argv) > 3 and sys.argv[1] == 'identity' and sys.argv[3] == str(target) and not trigger.exists():\n"
                "    target.rename(raced_target)\n"
                "    target.mkdir()\n"
                "    (target / 'foreign.txt').write_text('foreign\\n', encoding='utf-8')\n"
                "    trigger.write_text('1', encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = self._run_uninstall_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertTrue(trigger.exists(), msg=result.stdout + result.stderr)
            self.assertEqual((applet_target / "foreign.txt").read_text(encoding="utf-8"), "foreign\n")
            self.assertEqual((raced_target / "old.txt").read_text(encoding="utf-8"), "old\n")

    def test_uninstall_local_refuses_symlinked_home_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            outside = tmp_path / "outside"
            home.mkdir()
            outside.mkdir()
            (home / ".local").symlink_to(outside, target_is_directory=True)
            protected = outside / "share" / "speed-of-cinnamon" / "python" / "protected.txt"
            protected.parent.mkdir(parents=True)
            protected.write_text("keep\n", encoding="utf-8")

            result = self._run_uninstall_local(REPO_ROOT, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("refusing to follow symlink during uninstall", result.stderr)
            self.assertTrue(protected.exists())

    def test_uninstall_local_does_not_use_path_rm_for_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            fake_bin = tmp_path / "fake-bin"
            marker = tmp_path / "rm-marker"
            home.mkdir()
            fake_bin.mkdir()
            (fake_bin / "rm").write_text(
                "#!/usr/bin/env bash\n"
                f"printf used > {str(marker)!r}\n"
                "exit 77\n",
                encoding="utf-8",
            )
            (fake_bin / "rm").chmod(0o755)
            (home / ".local" / "bin").mkdir(parents=True)
            (home / ".local" / "bin" / "speed-of-cinnamon").write_text("wrapper\n", encoding="utf-8")
            (home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598").mkdir(parents=True)
            (home / ".local" / "share" / "speed-of-cinnamon" / "python").mkdir(parents=True)
            (home / ".local" / "share" / "man" / "man1").mkdir(parents=True)
            (home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon.1").write_text(
                "man\n", encoding="utf-8"
            )

            result = self._run_uninstall_local(REPO_ROOT, home, {"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"})

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse((home / ".local" / "bin" / "speed-of-cinnamon").exists())


class SmokeBackendTest(unittest.TestCase):
    def _run_smoke_backend(self, home: Path, backend: Path) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["SPEED_OF_CINNAMON_TEST_HOME"] = "1"
        return subprocess.run(
            ["bash", str(REPO_ROOT / "scripts" / "smoke-backend.sh"), str(backend)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            check=False,
            text=True,
        )

    def _write_fake_backend(self, path: Path, start_error: str) -> None:
        path.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "case \"${1:-}\" in\n"
            "  doctor|models|status|cleanup)\n"
            "    printf '{\"status\":\"done\"}\\n'\n"
            "    ;;\n"
            "  alarms)\n"
            "    printf '{\"status\":\"done\"}\\n'\n"
            "    ;;\n"
            "  start)\n"
            f"    printf '{{\"status\":\"error\",\"error\":\"{start_error}\"}}\\n'\n"
            "    exit 1\n"
            "    ;;\n"
            "  *)\n"
            "    printf 'unexpected command: %s\\n' \"${1:-}\" >&2\n"
            "    exit 2\n"
            "    ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        path.chmod(0o700)

    def test_smoke_backend_skips_live_audio_when_no_recorder_can_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            backend = tmp_path / "backend"
            self._write_fake_backend(backend, "no recorder backend started successfully: pw-record failed")

            result = self._run_smoke_backend(home, backend)

        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Skipping live recorder smoke", result.stderr)

    def test_smoke_backend_keeps_unexpected_start_errors_hard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            backend = tmp_path / "backend"
            self._write_fake_backend(backend, "unexpected start failure")

            result = self._run_smoke_backend(home, backend)

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Skipping live recorder smoke", result.stderr)

    def test_smoke_backend_uses_isolated_xdg_dirs_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            marker = tmp_path / "xdg-marker.json"
            backend = tmp_path / "backend"
            backend.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "case \"${1:-}\" in\n"
                "  doctor)\n"
                "    python3 - <<'PY'\n"
                "import json, os\n"
                f"open({str(marker)!r}, 'w', encoding='utf-8').write(json.dumps({{k: (os.environ[k] if k in os.environ else '') for k in ('XDG_STATE_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME')}}))\n"
                "PY\n"
                "    printf '{\"status\":\"done\"}\\n'\n"
                "    ;;\n"
                "  models|status|cleanup)\n"
                "    printf '{\"status\":\"done\"}\\n'\n"
                "    ;;\n"
                "  alarms)\n"
                "    printf '{\"status\":\"done\"}\\n'\n"
                "    ;;\n"
                "  start)\n"
                "    printf '{\"status\":\"error\",\"error\":\"no recorder backend started successfully: fake\"}\\n'\n"
                "    exit 1\n"
                "    ;;\n"
                "  *)\n"
                "    printf 'unexpected command: %s\\n' \"${1:-}\" >&2\n"
                "    exit 2\n"
                "    ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            backend.chmod(0o700)

            result = self._run_smoke_backend(home, backend)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            payload = json.loads(marker.read_text(encoding="utf-8"))
            self.assertNotEqual(payload["XDG_STATE_HOME"], str(home / ".local" / "state"))
            self.assertIn("speed-of-cinnamon-smoke-", payload["XDG_STATE_HOME"])
            self.assertIn("speed-of-cinnamon-smoke-", payload["XDG_DATA_HOME"])
            self.assertIn("speed-of-cinnamon-smoke-", payload["XDG_CACHE_HOME"])
