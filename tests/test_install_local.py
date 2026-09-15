from __future__ import annotations

import hashlib
import importlib.util
import json
import marshal
import os
import shlex
import signal
import shutil
import stat
import struct
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

    def _run_safe_fs(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(REPO_ROOT / "scripts" / "safe-local-fs.py"), *arguments],
            cwd=REPO_ROOT,
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

    def _copy_installable_repo_with_post_activation_hook(self, destination: Path, hook: str) -> tuple[Path, Path]:
        repo_root = self._copy_installable_minimal_repo(destination)
        marker = destination / "post-activation-hook-ran"
        helper = repo_root / "scripts" / "safe-local-fs.py"
        helper_source = helper.read_text(encoding="utf-8")
        self.assertEqual(helper_source.count("        args.func(args)\n"), 1)
        hook = hook.replace("MARKER_PATH", repr(str(marker)))
        helper.write_text(helper_source.replace("        args.func(args)\n", "        args.func(args)\n" + hook), encoding="utf-8")
        return repo_root, marker

    def _inject_before_pyc_validation(self, repo_root: Path, code: str) -> None:
        installer = repo_root / "scripts" / "install-local.sh"
        source = installer.read_text(encoding="utf-8")
        marker = "  # Generated bytecode must be validated before manifests or activation.\n"
        self.assertEqual(source.count(marker), 1)
        command = f'  "${{python3_path}}" -I -B -c {shlex.quote(code)} "${{package_root}}"\n'
        installer.write_text(source.replace(marker, command + marker), encoding="utf-8")

    def test_install_local_generates_checked_hash_bytecode_without_copying_source_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            source_package = repo_root / "src" / "speed_of_cinnamon"
            source_cache = source_package / "__pycache__"
            source_cache.mkdir()
            stale_pyc = source_cache / Path(importlib.util.cache_from_source(str(source_package / "cli.py"))).name
            stale_pyc.write_bytes(b"foreign timestamp bytecode")
            home = tmp_path / "home"
            home.mkdir()

            result = self._run_install_local(repo_root, home)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            installed = home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon"
            sources = sorted(installed.rglob("*.py"))
            expected = {Path(importlib.util.cache_from_source(str(source))): source for source in sources}
            generated = set(installed.rglob("*.pyc"))
            self.assertEqual(generated, set(expected))
            self.assertNotIn(stale_pyc.name, {path.name for path in generated if path.read_bytes() == stale_pyc.read_bytes()})
            for pyc, source in expected.items():
                data = pyc.read_bytes()
                info = pyc.lstat()
                self.assertEqual(data[:4], importlib.util.MAGIC_NUMBER)
                self.assertEqual(struct.unpack("<I", data[4:8])[0], 3)
                self.assertEqual(data[8:16], importlib.util.source_hash(source.read_bytes()))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
                self.assertEqual(info.st_uid, os.getuid())
                self.assertEqual(info.st_nlink, 1)
                self.assertEqual(marshal.loads(data[16:]).co_filename, str(source))
                self.assertEqual(stat.S_IMODE(pyc.parent.lstat().st_mode), 0o700)

    def test_install_local_fails_before_activation_on_compile_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            (repo_root / "src" / "speed_of_cinnamon" / "cli.py").write_text("def broken(:\n", encoding="utf-8")
            home = tmp_path / "home"
            home.mkdir()

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("failed to compile staged Python package", result.stderr)
            self.assertFalse((home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon").exists())

    def test_install_local_rejects_manipulated_generated_bytecode_before_activation(self) -> None:
        mutations = {
            "header": (
                "from pathlib import Path; import sys; p=next(Path(sys.argv[1]).rglob('*.pyc')); "
                "d=bytearray(p.read_bytes()); d[4:8]=(0).to_bytes(4,'little'); p.write_bytes(d)"
            ),
            "missing": "from pathlib import Path; import sys; next(Path(sys.argv[1]).rglob('*.pyc')).unlink()",
            "extra": (
                "from pathlib import Path; import shutil,sys; p=next(Path(sys.argv[1]).rglob('*.pyc')); "
                "shutil.copyfile(p,p.parent/('extra.'+p.name))"
            ),
            "symlink": (
                "from pathlib import Path; import sys; root=Path(sys.argv[1]); p=next(root.rglob('*.pyc')); "
                "p.unlink(); p.symlink_to(root/'cli.py')"
            ),
            "hardlink": (
                "from pathlib import Path; import os,sys; p=next(Path(sys.argv[1]).rglob('*.pyc')); "
                "os.link(p,p.with_name(p.name+'.hardlink'))"
            ),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                repo_root = self._copy_installable_minimal_repo(tmp_path)
                self._inject_before_pyc_validation(repo_root, mutation)
                home = tmp_path / "home"
                home.mkdir()

                result = self._run_install_local(repo_root, home)

                self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
                self.assertIn("staged Python bytecode validation failed", result.stderr)
                self.assertFalse(
                    (home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon").exists()
                )

    def test_install_local_rollback_restores_one_source_bytecode_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            python_target = home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon"
            hook = (
                "        if (\n"
                "            os.environ.get('SPEED_OF_CINNAMON_TEST_CORRUPT_PACKAGE') == '1'\n"
                "            and sys.argv[1:3] == ['replace', 'install']\n"
                f"            and sys.argv[4] == {str(python_target)!r}\n"
                "            and not Path(MARKER_PATH).exists()\n"
                "        ):\n"
                "            Path(sys.argv[4], 'cli.py').write_text('late corruption\\n', encoding='utf-8')\n"
                "            Path(MARKER_PATH).write_text('1', encoding='utf-8')\n"
            )
            repo_root, marker = self._copy_installable_repo_with_post_activation_hook(tmp_path, hook)
            first = self._run_install_local(repo_root, home)
            self.assertEqual(first.returncode, 0, msg=first.stdout + first.stderr)
            before = {
                str(path.relative_to(python_target)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in python_target.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            (repo_root / "src" / "speed_of_cinnamon" / "cli.py").write_text("NEW_GENERATION = 1\n", encoding="utf-8")

            second = self._run_install_local(
                repo_root,
                home,
                {"SPEED_OF_CINNAMON_TEST_CORRUPT_PACKAGE": "1"},
            )

            after = {
                str(path.relative_to(python_target)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in python_target.rglob("*")
                if path.is_file() and not path.is_symlink()
            }
            self.assertNotEqual(second.returncode, 0, msg=second.stdout + second.stderr)
            self.assertIn("installed python package verification failed", second.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(after, before)

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

    def test_install_local_rejects_writable_home_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            writable_parent = tmp_path / "writable-parent"
            home = writable_parent / "home"
            home.mkdir(parents=True)
            writable_parent.chmod(0o777)

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe persistent directory chain", result.stderr)
            self.assertFalse((home / ".local").exists())

    def test_install_local_rejects_writable_existing_target_ancestor_before_mkdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            local = home / ".local"
            local.mkdir()
            local.chmod(0o777)

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsafe persistent directory chain", result.stderr)
            self.assertFalse((local / "share").exists())

    def test_uninstall_local_rejects_writable_home_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            writable_parent = tmp_path / "writable-parent"
            home = writable_parent / "home"
            home.mkdir(parents=True)
            writable_parent.chmod(0o777)

            result = self._run_uninstall_local(REPO_ROOT, home)

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("writable", result.stderr)

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

    def test_install_local_upgrade_replaces_payload_and_preserves_user_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            (repo_root / "files" / "speed-of-cinnamon@H234598" / "metadata.json").write_text(
                '{"generation":"new"}\n', encoding="utf-8"
            )
            (repo_root / "src" / "speed_of_cinnamon" / "cli.py").write_text(
                "NEW_PAYLOAD\n", encoding="utf-8"
            )
            home = tmp_path / "home"
            home.mkdir()
            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            python_target = app_data / "python" / "speed_of_cinnamon"
            wrapper_target = home / ".local" / "bin" / "speed-of-cinnamon"
            man_target = home / ".local" / "share" / "man" / "man1"
            applet_target.mkdir(parents=True)
            python_target.mkdir(parents=True)
            wrapper_target.parent.mkdir(parents=True)
            man_target.mkdir(parents=True)
            (applet_target / "metadata.json").write_text('{"generation":"old"}\n', encoding="utf-8")
            (python_target / "cli.py").write_text("OLD_PAYLOAD\n", encoding="utf-8")
            wrapper_target.write_text("OLD_WRAPPER\n", encoding="utf-8")
            (man_target / "speed-of-cinnamon.1").write_text("OLD_MAN\n", encoding="utf-8")
            (man_target / "speed-of-cinnamon-alarms.1").write_text("OLD_ALARMS\n", encoding="utf-8")
            (app_data / "settings.json").write_text("user settings\n", encoding="utf-8")
            (app_data / "transcripts").mkdir()
            (app_data / "transcripts" / "kept.txt").write_text("private transcript\n", encoding="utf-8")

            result = self._run_install_local(repo_root, home)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(
                (applet_target / "metadata.json").read_text(encoding="utf-8"),
                '{"generation":"new"}\n',
            )
            self.assertEqual((python_target / "cli.py").read_text(encoding="utf-8"), "NEW_PAYLOAD\n")
            self.assertIn("speed_of_cinnamon", wrapper_target.read_text(encoding="utf-8"))
            self.assertEqual((man_target / "speed-of-cinnamon.1").read_text(encoding="utf-8"), "man page\n")
            self.assertEqual((app_data / "settings.json").read_text(encoding="utf-8"), "user settings\n")
            self.assertEqual(
                (app_data / "transcripts" / "kept.txt").read_text(encoding="utf-8"),
                "private transcript\n",
            )
            self.assertFalse(list(app_data.glob("install-stage-*")))

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
                "import shutil\n"
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
            real_copy_tree = module._copy_tree_fd
            original_stat = (source / "payload.txt").stat()

            def copy_tree_with_source_mutation(src: Path, dst: Path, **kwargs: object) -> None:
                (source / "payload.txt").write_text("muted\n", encoding="utf-8")
                os.utime(source / "payload.txt", (original_stat.st_atime, original_stat.st_mtime))
                return real_copy_tree(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_copy_tree_fd", side_effect=copy_tree_with_source_mutation):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_enforces_shared_entry_budget_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "initial.txt").write_text("safe\n", encoding="utf-8")
            real_copy_tree = module._copy_tree_fd

            def copy_tree_with_new_entries(src: Path, dst: Path, **kwargs: object) -> None:
                for index in range(3):
                    (source / f"late-{index}.txt").write_text("x\n", encoding="utf-8")
                return real_copy_tree(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "MAX_TREE_ENTRIES", 2), mock.patch.object(
                module, "_copy_tree_fd", side_effect=copy_tree_with_new_entries
            ):
                with self.assertRaisesRegex(OSError, "tree copy entry limit exceeded"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_enforces_shared_byte_budget_during_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "initial.txt").write_bytes(b"1234")
            real_copy_tree = module._copy_tree_fd

            def copy_tree_with_growing_payload(src: Path, dst: Path, **kwargs: object) -> None:
                (source / "late.txt").write_bytes(b"56789")
                return real_copy_tree(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "MAX_TREE_FILE_BYTES", 8), mock.patch.object(
                module, "_copy_tree_fd", side_effect=copy_tree_with_growing_payload
            ):
                with self.assertRaisesRegex(OSError, "tree copy byte limit exceeded"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_counts_preflight_hashing_in_shared_io_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "initial.txt").write_bytes(b"1234")
            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "MAX_TREE_FILE_BYTES", 4), mock.patch.object(module, "TREE_IO_PASSES", 1):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_install_tree_rejects_existing_targets_unchanged(self) -> None:
        for target_kind in ("directory", "file", "symlink"):
            with self.subTest(target_kind=target_kind), tempfile.TemporaryDirectory() as tmp:
                module = self._load_safe_fs_module()
                root = Path(tmp)
                source = root / "source"
                target = root / "target"
                source.mkdir()
                (source / "new.txt").write_text("new\n", encoding="utf-8")
                if target_kind == "directory":
                    target.mkdir()
                    (target / "old.txt").write_text("old\n", encoding="utf-8")
                elif target_kind == "file":
                    target.write_text("old file\n", encoding="utf-8")
                else:
                    victim = root / "victim"
                    victim.mkdir()
                    (victim / "keep.txt").write_text("victim\n", encoding="utf-8")
                    target.symlink_to(victim, target_is_directory=True)
                args = module.argparse.Namespace(
                    action="install",
                    source=str(source),
                    target=str(target),
                    label="tree",
                )
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

                if target_kind == "directory":
                    self.assertEqual((target / "old.txt").read_text(encoding="utf-8"), "old\n")
                elif target_kind == "file":
                    self.assertEqual(target.read_text(encoding="utf-8"), "old file\n")
                else:
                    self.assertTrue(target.is_symlink())
                    self.assertEqual((target / "keep.txt").read_text(encoding="utf-8"), "victim\n")
                self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_install_tree_commit_failure_has_explicit_create_only_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            real_rename = module._rename_without_replacing

            def fail_final_rename(
                src: str,
                dst: str,
                *,
                directory_fd: int,
                target_directory_fd: int | None = None,
                expected_source_stat: os.stat_result | None = None,
                action: str,
            ) -> None:
                if target_directory_fd is not None:
                    raise OSError("final no-clobber rename failed")
                real_rename(
                    src,
                    dst,
                    directory_fd=directory_fd,
                    target_directory_fd=target_directory_fd,
                    expected_source_stat=expected_source_stat,
                    action=action,
                )

            args = module.argparse.Namespace(
                action="install",
                source=str(source),
                target=str(target),
                label="tree",
            )
            with mock.patch.object(module, "_rename_without_replacing", side_effect=fail_final_rename):
                with self.assertRaisesRegex(OSError, "final no-clobber rename failed"):
                    module.cmd_install_tree(args)
            self.assertFalse(target.exists())
            self.assertEqual(list(root.glob(".target.*.install")), [])

        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("new\n", encoding="utf-8")
            real_fsync = module._fsync_directory_fd
            fsync_calls = 0

            def fail_after_create_only_commit(fd: int, *, action: str) -> None:
                nonlocal fsync_calls
                fsync_calls += 1
                if fsync_calls == 3:
                    raise OSError("commit fsync failed")
                real_fsync(fd, action=action)

            args = module.argparse.Namespace(
                action="install",
                source=str(source),
                target=str(target),
                label="tree",
            )
            with mock.patch.object(module, "_fsync_directory_fd", side_effect=fail_after_create_only_commit):
                with self.assertRaisesRegex(OSError, "commit completed but synchronization failed"):
                    module.cmd_install_tree(args)
            self.assertEqual((target / "payload.txt").read_text(encoding="utf-8"), "new\n")
            self.assertEqual(list(root.glob(".target.*.install")), [])

    def test_safe_fs_install_tree_rejects_staged_payload_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            (source / "payload.txt").write_text("safe\n", encoding="utf-8")
            real_copy_tree = module._copy_tree_fd

            def copy_tree_with_staged_payload_corruption(src: Path, dst: Path, **kwargs: object) -> None:
                real_copy_tree(src, dst, **kwargs)
                (dst / "payload.txt").write_text("evaded\n", encoding="utf-8")

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_copy_tree_fd", side_effect=copy_tree_with_staged_payload_corruption):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

            self.assertFalse(target.exists())

    def test_safe_fs_remove_dir_uses_bounded_fd_delete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            target = root / "target"
            target.mkdir()
            (target / "payload.txt").write_text("safe\n", encoding="utf-8")

            args = module.argparse.Namespace(action="install", path=str(target), kind="dir")
            module.cmd_remove(args)

            self.assertFalse(target.exists())

    def test_safe_fs_remove_dir_rejects_path_substitution_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            target = root / "target"
            original = root / "target-original"
            victim = root / "victim"
            target.mkdir()
            (target / "original.txt").write_text("original\n", encoding="utf-8")
            victim.mkdir()
            (victim / "victim.txt").write_text("victim\n", encoding="utf-8")
            expected_identity = module._identity_text(target.lstat())
            real_lstat_at = module._lstat_at
            target_checks = 0

            def lstat_with_substitution(parent_fd: int, name: str) -> os.stat_result | None:
                nonlocal target_checks
                result = real_lstat_at(parent_fd, name)
                if name == target.name:
                    target_checks += 1
                    if target_checks == 3:
                        target.rename(original)
                        victim.rename(target)
                return result

            args = module.argparse.Namespace(
                action="install",
                path=str(target),
                kind="dir",
                expected_identity=expected_identity,
            )
            with mock.patch.object(module, "_lstat_at", side_effect=lstat_with_substitution):
                with self.assertRaisesRegex(OSError, "identity changed"):
                    module.cmd_remove(args)

            self.assertEqual((target / "victim.txt").read_text(encoding="utf-8"), "victim\n")
            self.assertEqual((original / "original.txt").read_text(encoding="utf-8"), "original\n")

    def test_safe_fs_remove_dir_enforces_entry_depth_and_deadline_limits(self) -> None:
        cases = (
            ("STALE_CLEANUP_MAX_DEPTH", 1, Path("d0") / "d1" / "deep.txt"),
            ("STALE_CLEANUP_MAX_ENTRIES", 0, Path("marker.txt")),
        )
        for limit_name, limit, marker_relative in cases:
            with self.subTest(limit_name=limit_name), tempfile.TemporaryDirectory() as tmp:
                module = self._load_safe_fs_module()
                target = Path(tmp) / "target"
                marker = target / marker_relative
                marker.parent.mkdir(parents=True)
                marker.write_text("keep\n", encoding="utf-8")
                args = module.argparse.Namespace(action="install", path=str(target), kind="dir")
                with mock.patch.object(module, limit_name, limit):
                    with self.assertRaisesRegex(OSError, "stale install cleanup"):
                        module.cmd_remove(args)
                self.assertTrue(marker.exists())
                self.assertTrue(target.exists())

        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            target = Path(tmp) / "target"
            target.mkdir()
            marker = target / "keep.txt"
            marker.write_text("keep\n", encoding="utf-8")
            args = module.argparse.Namespace(action="install", path=str(target), kind="dir")
            with mock.patch.object(module, "STALE_CLEANUP_BUDGET_NS", 10):
                with mock.patch.object(module.time, "monotonic_ns", side_effect=(100, 110)):
                    with self.assertRaises(OSError):
                        module.cmd_remove(args)
            self.assertTrue(marker.exists())
            self.assertTrue(target.exists())

    def test_safe_fs_remove_file_rejects_path_substitution_after_identity_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            target = root / "target.txt"
            original = root / "target-original.txt"
            victim = root / "victim.txt"
            target.write_text("original\n", encoding="utf-8")
            victim.write_text("victim\n", encoding="utf-8")
            expected_identity = module._identity_text(target.lstat())
            real_lstat_at = module._lstat_at
            target_checks = 0

            def lstat_with_substitution(parent_fd: int, name: str) -> os.stat_result | None:
                nonlocal target_checks
                result = real_lstat_at(parent_fd, name)
                if name == target.name:
                    target_checks += 1
                    if target_checks == 3:
                        target.rename(original)
                        victim.rename(target)
                return result

            args = module.argparse.Namespace(
                action="install",
                path=str(target),
                kind="file",
                expected_identity=expected_identity,
            )
            with mock.patch.object(module, "_lstat_at", side_effect=lstat_with_substitution):
                with self.assertRaisesRegex(OSError, "identity changed"):
                    module.cmd_remove(args)

            self.assertEqual(target.read_text(encoding="utf-8"), "victim\n")
            self.assertEqual(original.read_text(encoding="utf-8"), "original\n")

    def test_safe_fs_rmdir_rejects_path_substitution_and_restores_nonempty_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            target = root / "target"
            original = root / "target-original"
            victim = root / "victim"
            target.mkdir()
            victim.mkdir()
            (victim / "victim.txt").write_text("victim\n", encoding="utf-8")
            expected_identity = module._identity_text(target.lstat())
            real_lstat_at = module._lstat_at
            target_checks = 0

            def lstat_with_substitution(parent_fd: int, name: str) -> os.stat_result | None:
                nonlocal target_checks
                result = real_lstat_at(parent_fd, name)
                if name == target.name:
                    target_checks += 1
                    if target_checks == 3:
                        target.rename(original)
                        victim.rename(target)
                return result

            args = module.argparse.Namespace(
                action="install",
                path=str(target),
                expected_identity=expected_identity,
                ignore_non_empty=True,
            )
            with mock.patch.object(module, "_lstat_at", side_effect=lstat_with_substitution):
                with self.assertRaisesRegex(OSError, "identity changed"):
                    module.cmd_rmdir(args)

            self.assertTrue(target.is_dir())
            self.assertEqual((target / "victim.txt").read_text(encoding="utf-8"), "victim\n")
            self.assertTrue(original.is_dir())

        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            target = Path(tmp) / "target"
            target.mkdir()
            args = module.argparse.Namespace(
                action="install",
                path=str(target),
                expected_identity=module._identity_text(target.lstat()),
                ignore_non_empty=False,
            )
            module.cmd_rmdir(args)
            self.assertFalse(target.exists())

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
            real_copy_tree = module._copy_tree_fd

            def copy_tree_with_symlink_race(src: Path, dst: Path, **kwargs: object) -> None:
                payload.unlink()
                payload.symlink_to(outside)
                return real_copy_tree(src, dst, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            with mock.patch.object(module, "_copy_tree_fd", side_effect=copy_tree_with_symlink_race):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_install_tree(args)

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

    def test_safe_fs_install_tree_rejects_source_root_swap_before_signature(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source"
            original_source = root / "original-source"
            outside = root / "outside"
            target = root / "target"
            source.mkdir()
            outside.mkdir()
            (source / "payload.txt").write_text("safe\n", encoding="utf-8")
            (outside / "payload.txt").write_text("outside\n", encoding="utf-8")
            real_tree_signature = module._tree_signature

            def swap_source_before_signature(
                tree: Path,
                **kwargs: object,
            ) -> dict[str, tuple[object, ...]]:
                source.rename(original_source)
                source.symlink_to(outside, target_is_directory=True)
                return real_tree_signature(tree, **kwargs)

            args = module.argparse.Namespace(action="install", source=str(source), target=str(target), label="tree")
            try:
                with mock.patch.object(module, "_tree_signature", side_effect=swap_source_before_signature):
                    with self.assertRaisesRegex(SystemExit, "1"):
                        module.cmd_install_tree(args)
            finally:
                if source.is_symlink():
                    source.unlink()
                if original_source.exists():
                    original_source.rename(source)

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

    def test_install_local_postchecks_exact_staged_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            install_home = tmp_path / "install-home"
            install_home.mkdir()
            result = self._run_install_local(REPO_ROOT, install_home)
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

            installed_package = install_home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon"
            expected_python_root = tmp_path / "expected-python"
            expected_package = expected_python_root / "speed_of_cinnamon"
            shutil.copytree(
                REPO_ROOT / "src" / "speed_of_cinnamon",
                expected_package,
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            wrapper = install_home / ".local" / "bin" / "speed-of-cinnamon"
            exec_line = next(line for line in wrapper.read_text(encoding="utf-8").splitlines() if line.startswith("exec "))
            interpreter = shlex.split(exec_line)[1]
            compile_result = subprocess.run(
                [
                    interpreter,
                    "-I",
                    "-m",
                    "compileall",
                    "-q",
                    "-f",
                    "--invalidation-mode",
                    "checked-hash",
                    "-s",
                    str(expected_python_root),
                    "-p",
                    str(install_home / ".local" / "share" / "speed-of-cinnamon" / "python"),
                    str(expected_package),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, msg=compile_result.stdout + compile_result.stderr)
            for cache_dir in expected_package.rglob("__pycache__"):
                cache_dir.chmod(0o700)
            for pyc in expected_package.rglob("*.pyc"):
                pyc.chmod(0o600)
            manifest = tmp_path / "source-package.manifest"
            snapshot = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "safe-local-fs.py"),
                    "snapshot-tree",
                    "test",
                    str(expected_package),
                    str(manifest),
                    "python package",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(snapshot.returncode, 0, msg=snapshot.stdout + snapshot.stderr)
            digest = snapshot.stdout.strip()
            verify = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "safe-local-fs.py"),
                    "verify-tree",
                    "test",
                    str(manifest),
                    digest,
                    str(installed_package),
                    "python package",
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertEqual(verify.returncode, 0, msg=verify.stdout + verify.stderr)
            self.assertTrue((install_home / ".local" / "bin" / "speed-of-cinnamon").is_file())
            self.assertTrue((install_home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon.1").is_file())
            self.assertTrue(
                (install_home / ".local" / "share" / "man" / "man1" / "speed-of-cinnamon-alarms.1").is_file()
            )

    def test_install_local_kernel_lock_is_crash_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            app_data.mkdir(parents=True)
            flock = shutil.which("flock")
            self.assertIsNotNone(flock)

            def start_lock_holder() -> subprocess.Popen[bytes]:
                holder = subprocess.Popen(
                    [
                        "bash",
                        "-c",
                        'exec {fd}<"$1"; flock -n "$fd" || exit 73; printf "ready\\n"; IFS= read -r _',
                        "lock-holder",
                        str(app_data),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                )
                self.assertEqual(holder.stdout.readline(), b"ready\n")
                return holder

            env = os.environ.copy()
            env.update(
                {
                    "HOME": str(home),
                    "PATH": env.get("PATH", ""),
                    "SPEED_OF_CINNAMON_TEST_HOME": "1",
                }
            )
            killed_holder = start_lock_holder()
            blocked = self._run_install_local(repo_root, home)
            self.assertNotEqual(blocked.returncode, 0, msg=blocked.stdout + blocked.stderr)
            self.assertIn("another local install is active", blocked.stderr)
            os.killpg(killed_holder.pid, signal.SIGKILL)
            killed_holder.communicate(timeout=5)

            after_kill = self._run_install_local(repo_root, home)
            self.assertEqual(after_kill.returncode, 0, msg=after_kill.stdout + after_kill.stderr)

            exited_holder = start_lock_holder()
            exited_holder.stdin.write(b"release\n")
            exited_holder.stdin.close()
            exited_holder.wait(timeout=5)
            self.assertEqual(exited_holder.returncode, 0, msg=exited_holder.stderr.read().decode())
            after_exit = self._run_install_local(repo_root, home)
            self.assertEqual(after_exit.returncode, 0, msg=after_exit.stdout + after_exit.stderr)

    def test_install_local_removes_only_safe_stale_phases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            app_data.mkdir(parents=True)
            for name in ("install-stage-pre", "install-stage-pre-second"):
                workspace = app_data / name
                workspace.mkdir()
                phase_result = self._run_safe_fs("phase-set", "test", str(workspace), "pre-activation")
                self.assertEqual(phase_result.returncode, 0, msg=phase_result.stderr)

            result = self._run_install_local(repo_root, home)

            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertEqual(list(app_data.glob("install-stage-*")), [])

    def test_install_local_blocks_unknown_or_malformed_stale_phase(self) -> None:
        for payload in (b"unknown\n", b"\xff", b"x" * 65):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                repo_root = self._copy_installable_minimal_repo(tmp_path)
                home = tmp_path / "home"
                home.mkdir()
                workspace = home / ".local" / "share" / "speed-of-cinnamon" / "install-stage-recovery"
                workspace.mkdir(parents=True)
                (workspace / ".install-phase").write_bytes(payload)

                result = self._run_install_local(repo_root, home)

                self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
                self.assertIn("unresolved install recovery workspace", result.stderr)
                self.assertTrue(workspace.exists())
                self.assertEqual(list(workspace.parent.glob("install-stage-*")), [workspace])
                self.assertFalse((home / ".local" / "bin" / "speed-of-cinnamon").exists())

    def test_install_local_blocks_more_than_bounded_stale_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            app_data.mkdir(parents=True)
            for index in range(33):
                workspace = app_data / f"install-stage-{index:02d}"
                workspace.mkdir()
                phase = workspace / ".install-phase"
                phase.write_bytes(b"pre-activation\n")
                phase.chmod(0o600)

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("enumeration exceeds max 32", result.stderr)
            self.assertEqual(len(list(app_data.glob("install-stage-*"))), 33)
            self.assertFalse((home / ".local" / "bin" / "speed-of-cinnamon").exists())

    def test_safe_fs_cleanup_revalidates_phase_after_atomic_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            app_data = Path(tmp) / "app-data"
            workspace = app_data / "install-stage-race"
            workspace.mkdir(parents=True)
            phase = workspace / ".install-phase"
            phase.write_bytes(b"pre-activation\n")
            phase.chmod(0o600)
            real_read_phase = module._read_install_phase
            phase_reads = 0

            def read_phase_with_race(directory_fd: int, path: Path, *, action: str) -> str:
                nonlocal phase_reads
                phase_reads += 1
                value = real_read_phase(directory_fd, path, action=action)
                if phase_reads == 2:
                    claimed_phase = Path(f"/proc/self/fd/{directory_fd}") / ".install-phase"
                    claimed_phase.write_bytes(b"recovery-required\n")
                    claimed_phase.chmod(0o600)
                    return real_read_phase(directory_fd, path, action=action)
                return value

            args = module.argparse.Namespace(action="test", app_data=str(app_data))
            with mock.patch.object(module, "_read_install_phase", side_effect=read_phase_with_race):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_cleanup_install_stages(args)

            claimed = list(app_data.glob("install-stage-*"))
            self.assertEqual(len(claimed), 1)
            preserved = self._run_safe_fs("phase-read", "test", str(claimed[0]))
            self.assertEqual(preserved.returncode, 0, msg=preserved.stderr)
            self.assertEqual(preserved.stdout.strip(), "recovery-required")

    def test_safe_fs_cleanup_keeps_replaced_claim_path_and_victim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            app_data = Path(tmp) / "app-data"
            workspace = app_data / "install-stage-race"
            workspace.mkdir(parents=True)
            (workspace / "original.txt").write_text("original\n", encoding="utf-8")
            phase = workspace / ".install-phase"
            phase.write_bytes(b"pre-activation\n")
            phase.chmod(0o600)
            victim = app_data / "victim"
            victim.mkdir()
            (victim / "must-survive.txt").write_text("victim\n", encoding="utf-8")
            real_read_phase = module._read_install_phase
            phase_reads = 0
            original_path: Path | None = None

            def read_phase_with_path_race(directory_fd: int, path: Path, *, action: str) -> str:
                nonlocal phase_reads, original_path
                phase_reads += 1
                value = real_read_phase(directory_fd, path, action=action)
                if phase_reads == 2:
                    original_path = path.with_name(f"{path.name}.original")
                    path.rename(original_path)
                    victim.rename(path)
                return value

            args = module.argparse.Namespace(action="test", app_data=str(app_data))
            with mock.patch.object(module, "_read_install_phase", side_effect=read_phase_with_path_race):
                with self.assertRaisesRegex(SystemExit, "1"):
                    module.cmd_cleanup_install_stages(args)

            self.assertIsNotNone(original_path)
            assert original_path is not None
            self.assertTrue((original_path / "original.txt").exists())
            self.assertTrue((original_path / ".install-phase").exists())
            claimed_victim = [path for path in app_data.glob("install-stage-*") if path != original_path]
            self.assertEqual(len(claimed_victim), 1)
            self.assertEqual((claimed_victim[0] / "must-survive.txt").read_text(encoding="utf-8"), "victim\n")

    def test_safe_fs_cleanup_preserves_overlimit_tree(self) -> None:
        for limit_name, limit, relative_marker in (
            ("STALE_CLEANUP_MAX_DEPTH", 1, Path("d0") / "d1" / "deep.txt"),
            ("STALE_CLEANUP_MAX_ENTRIES", 0, Path("marker.txt")),
        ):
            with self.subTest(limit_name=limit_name), tempfile.TemporaryDirectory() as tmp:
                module = self._load_safe_fs_module()
                app_data = Path(tmp) / "app-data"
                workspace = app_data / "install-stage-overlimit"
                marker = workspace / relative_marker
                marker.parent.mkdir(parents=True)
                marker.write_text("keep\n", encoding="utf-8")
                workspace.mkdir(exist_ok=True)
                phase = workspace / ".install-phase"
                phase.write_bytes(b"pre-activation\n")
                phase.chmod(0o600)
                args = module.argparse.Namespace(action="test", app_data=str(app_data))
                with mock.patch.object(module, limit_name, limit):
                    with self.assertRaisesRegex(SystemExit, "1"):
                        module.cmd_cleanup_install_stages(args)

                preserved = list(app_data.glob("install-stage-*"))
                self.assertTrue(preserved)
                self.assertTrue(any((candidate / relative_marker).exists() for candidate in preserved))

    def test_safe_fs_cleanup_deadline_is_absolute_and_preserves_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            app_data = Path(tmp) / "app-data"
            workspace = app_data / "install-stage-timeout"
            workspace.mkdir(parents=True)
            (workspace / "keep.txt").write_text("keep\n", encoding="utf-8")
            phase = workspace / ".install-phase"
            phase.write_bytes(b"pre-activation\n")
            phase.chmod(0o600)
            args = module.argparse.Namespace(action="test", app_data=str(app_data))
            with mock.patch.object(module, "STALE_CLEANUP_BUDGET_NS", 10):
                with mock.patch.object(module.time, "monotonic_ns", side_effect=(100, 110)):
                    with self.assertRaises(OSError):
                        module.cmd_cleanup_install_stages(args)

            self.assertTrue(workspace.exists())
            self.assertTrue((workspace / "keep.txt").exists())
            self.assertEqual((workspace / ".install-phase").read_bytes(), b"pre-activation\n")

    def test_safe_fs_cleanup_unlinks_symlink_and_hardlink_leaves(self) -> None:
        for node_kind in ("symlink", "hardlink"):
            with self.subTest(node_kind=node_kind), tempfile.TemporaryDirectory() as tmp:
                module = self._load_safe_fs_module()
                root = Path(tmp)
                app_data = root / "app-data"
                workspace = app_data / "install-stage-unsafe"
                workspace.mkdir(parents=True)
                outside = root / "outside.txt"
                outside.write_text("outside\n", encoding="utf-8")
                node = workspace / node_kind
                if node_kind == "symlink":
                    node.symlink_to(outside)
                else:
                    os.link(outside, node)
                phase = workspace / ".install-phase"
                phase.write_bytes(b"pre-activation\n")
                phase.chmod(0o600)
                args = module.argparse.Namespace(action="test", app_data=str(app_data))
                module.cmd_cleanup_install_stages(args)

                preserved = list(app_data.glob("install-stage-*"))
                self.assertEqual(preserved, [])
                if node_kind == "symlink":
                    self.assertTrue(outside.exists())
                else:
                    self.assertEqual(outside.stat().st_nlink, 1)
                    self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")

    def test_safe_fs_phase_api_rejects_removed_safe_complete_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()

            result = self._run_safe_fs("phase-set", "test", str(workspace), "safe-complete")

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((workspace / ".install-phase").exists())

    def test_install_local_sigkill_preserves_recovery_required_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            home = tmp_path / "home"
            home.mkdir()
            ready = tmp_path / "phase-ready"
            gate = tmp_path / "phase-gate"
            os.mkfifo(gate)
            real_helper = repo_root / "scripts" / "safe-local-fs-real.py"
            shutil.copy2(repo_root / "scripts" / "safe-local-fs.py", real_helper)
            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper.write_text(
                "from pathlib import Path\n"
                "import subprocess\n"
                "import sys\n"
                f"ready = Path({str(ready)!r})\n"
                f"gate = Path({str(gate)!r})\n"
                f"real_helper = Path({str(real_helper)!r})\n"
                "result = subprocess.run([sys.executable, str(real_helper), *sys.argv[1:]], check=False)\n"
                "if result.returncode == 0 and len(sys.argv) > 4 and sys.argv[1] == 'phase-set' and sys.argv[4] == 'recovery-required' and not ready.exists():\n"
                "    ready.write_text('1', encoding='utf-8')\n"
                "    print('ready', flush=True)\n"
                "    with gate.open('rb') as stream:\n"
                "        stream.read(1)\n"
                "raise SystemExit(result.returncode)\n",
                encoding="utf-8",
            )
            first = subprocess.Popen(
                ["bash", str(repo_root / "scripts" / "install-local.sh")],
                cwd=repo_root,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "PATH": os.environ.get("PATH", ""),
                    "SPEED_OF_CINNAMON_TEST_HOME": "1",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                self.assertEqual(first.stdout.readline(), "ready\n")
                os.killpg(first.pid, signal.SIGKILL)
                first_stdout, first_stderr = first.communicate(timeout=5)
                self.assertEqual(first.returncode, -signal.SIGKILL, msg=first_stdout + first_stderr)
            finally:
                if first.poll() is None:
                    os.killpg(first.pid, signal.SIGKILL)
                    first.communicate(timeout=5)

            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            stages = list(app_data.glob("install-stage-*"))
            self.assertEqual(len(stages), 1)
            phase = self._run_safe_fs("phase-read", "test", str(stages[0]))
            self.assertEqual(phase.returncode, 0, msg=phase.stderr)
            self.assertEqual(phase.stdout.strip(), "recovery-required")

            retry = self._run_install_local(repo_root, home)

            self.assertNotEqual(retry.returncode, 0, msg=retry.stdout + retry.stderr)
            self.assertIn("manual recovery", retry.stderr)
            self.assertEqual(list(app_data.glob("install-stage-*")), stages)

    def test_safe_fs_copy_file_rejects_growth_after_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = self._load_safe_fs_module()
            root = Path(tmp)
            source = root / "source.txt"
            target = root / "target.txt"
            source.write_text("safe", encoding="utf-8")
            args = module.argparse.Namespace(
                action="install",
                src=str(source),
                dst=str(target),
                mode="0600",
                dst_must_not_exist=False,
                max_bytes=4,
            )
            real_hash = module._hash_open_file

            def hash_and_grow(handle: object, *, max_bytes: int | None = None) -> str:
                digest = real_hash(handle, max_bytes=max_bytes)
                source.write_text("grown", encoding="utf-8")
                return digest

            with mock.patch.object(module, "_hash_open_file", side_effect=hash_and_grow):
                with self.assertRaisesRegex(OSError, "source exceeds copy size limit"):
                    module.cmd_copy_file(args)
            self.assertFalse(target.exists())

    def test_safe_fs_verify_tree_rejects_malformed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "target"
            target.mkdir()
            (target / "payload").write_text("payload\n", encoding="utf-8")
            helper = REPO_ROOT / "scripts" / "safe-local-fs.py"
            valid_manifest = tmp_path / "valid.manifest"
            snapshot = subprocess.run(
                [
                    "python3",
                    str(helper),
                    "snapshot-tree",
                    "test",
                    str(target),
                    str(valid_manifest),
                    "target",
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(snapshot.returncode, 0, msg=snapshot.stdout + snapshot.stderr)
            canonical = valid_manifest.read_bytes()
            malformed = {
                "duplicate": b'{"exclude_names":[],"kind":"dir","schema_version":1,"signature":{},"signature":{}}',
                "noncanonical": b" " + canonical + b"\n",
                "invalid-utf8": b"\xff",
                "nonfinite": b'{"schema_version":1e400}',
                "kind-list": b'{"exclude_names":[],"kind":[],"schema_version":1,"signature":{}}',
            }
            for name, data in malformed.items():
                with self.subTest(name=name):
                    manifest = tmp_path / f"{name}.manifest"
                    manifest.write_bytes(data)
                    result = subprocess.run(
                        [
                            "python3",
                            str(helper),
                            "verify-tree",
                            "test",
                            str(manifest),
                            hashlib.sha256(data).hexdigest(),
                            str(target),
                            "target",
                        ],
                        capture_output=True,
                        check=False,
                        text=True,
                    )
                    self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_safe_fs_rejects_oversized_root_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "sparse-source"
            source.touch()
            with source.open("wb") as handle:
                handle.truncate(536870913)
            result = subprocess.run(
                [
                    "python3",
                    str(REPO_ROOT / "scripts" / "safe-local-fs.py"),
                    "snapshot-tree",
                    "test",
                    str(source),
                    str(tmp_path / "manifest"),
                    "root file",
                ],
                capture_output=True,
                check=False,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("exceeds size limit", result.stderr)

    def test_install_local_rolls_back_when_package_postcheck_detects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            (repo_root / "src" / "speed_of_cinnamon" / "transcriber.py").write_text(
                "# stale runtime marker\n",
                encoding="utf-8",
            )
            home = tmp_path / "home"
            home.mkdir()
            marker = tmp_path / "package-corruption-triggered"

            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper_source = helper.read_text(encoding="utf-8")
            hook = (
                "        if (\n"
                "            os.environ.get(\"SPEED_OF_CINNAMON_TEST_CORRUPT_PACKAGE\") == \"1\"\n"
                "            and len(sys.argv) > 4\n"
                "            and sys.argv[1:3] == [\"replace\", \"install\"]\n"
                "            and \"/python/speed_of_cinnamon\" in sys.argv[4]\n"
                "        ):\n"
                "            marker = Path(os.environ[\"SPEED_OF_CINNAMON_CORRUPTION_MARKER\"])\n"
                "            if not marker.exists():\n"
                "                (Path(sys.argv[4]) / \"transcriber.py\").unlink()\n"
                "                marker.write_text(\"corrupted\", encoding=\"utf-8\")\n"
            )
            self.assertEqual(helper_source.count("        args.func(args)\n"), 1)
            helper.write_text(helper_source.replace("        args.func(args)\n", "        args.func(args)\n" + hook), encoding="utf-8")

            result = self._run_install_local(
                repo_root,
                home,
                {
                    "SPEED_OF_CINNAMON_TEST_CORRUPT_PACKAGE": "1",
                    "SPEED_OF_CINNAMON_CORRUPTION_MARKER": str(marker),
                },
            )

            installed_root = home / ".local" / "share" / "speed-of-cinnamon"
            stages = list(installed_root.glob("install-stage-*")) if installed_root.exists() else []
            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("installed python package verification failed", result.stderr)
            self.assertTrue(marker.exists())
            self.assertFalse((installed_root / "python" / "speed_of_cinnamon").exists())
            self.assertFalse((home / ".local" / "bin" / "speed-of-cinnamon").exists())
            self.assertFalse(
                (home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598").exists()
            )
            self.assertEqual(stages, [])

    def test_install_local_restores_existing_targets_after_late_package_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            python_target = app_data / "python" / "speed_of_cinnamon"
            wrapper_target = home / ".local" / "bin" / "speed-of-cinnamon"
            man_target = home / ".local" / "share" / "man" / "man1"
            hook = (
                "        if (\n"
                "            sys.argv[1:3] == [\"replace\", \"install\"]\n"
                f"            and sys.argv[4] == {str(python_target)!r}\n"
                "            and not Path(MARKER_PATH).exists()\n"
                "        ):\n"
                "            Path(sys.argv[4], \"cli.py\").write_text(\"late corruption\\n\", encoding=\"utf-8\")\n"
                "            Path(MARKER_PATH).write_text(\"1\", encoding=\"utf-8\")\n"
            )
            repo_root, marker = self._copy_installable_repo_with_post_activation_hook(tmp_path, hook)
            (repo_root / "files" / "speed-of-cinnamon@H234598" / "metadata.json").write_text(
                '{"generation":"new"}\n', encoding="utf-8"
            )
            applet_target.mkdir(parents=True)
            python_target.mkdir(parents=True)
            wrapper_target.parent.mkdir(parents=True)
            man_target.mkdir(parents=True)
            (applet_target / "metadata.json").write_text('{"generation":"old"}\n', encoding="utf-8")
            (python_target / "cli.py").write_text("OLD_PAYLOAD\n", encoding="utf-8")
            wrapper_target.write_text("OLD_WRAPPER\n", encoding="utf-8")
            (man_target / "speed-of-cinnamon.1").write_text("OLD_MAN\n", encoding="utf-8")
            (man_target / "speed-of-cinnamon-alarms.1").write_text("OLD_ALARMS\n", encoding="utf-8")
            (app_data / "settings.json").write_text("user settings\n", encoding="utf-8")
            (app_data / "transcripts").mkdir()
            (app_data / "transcripts" / "kept.txt").write_text("private transcript\n", encoding="utf-8")

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("installed python package verification failed", result.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual((applet_target / "metadata.json").read_text(encoding="utf-8"), '{"generation":"old"}\n')
            self.assertEqual((python_target / "cli.py").read_text(encoding="utf-8"), "OLD_PAYLOAD\n")
            self.assertEqual(wrapper_target.read_text(encoding="utf-8"), "OLD_WRAPPER\n")
            self.assertEqual((man_target / "speed-of-cinnamon.1").read_text(encoding="utf-8"), "OLD_MAN\n")
            self.assertEqual(
                (man_target / "speed-of-cinnamon-alarms.1").read_text(encoding="utf-8"), "OLD_ALARMS\n"
            )
            self.assertEqual((app_data / "settings.json").read_text(encoding="utf-8"), "user settings\n")
            self.assertEqual(
                (app_data / "transcripts" / "kept.txt").read_text(encoding="utf-8"), "private transcript\n"
            )
            self.assertFalse(list(app_data.glob("install-stage-*")))

    def test_install_local_preserves_recovery_when_exchange_restore_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            app_data = home / ".local" / "share" / "speed-of-cinnamon"
            applet_target = home / ".local" / "share" / "cinnamon" / "applets" / "speed-of-cinnamon@H234598"
            python_target = app_data / "python" / "speed_of_cinnamon"
            wrapper_target = home / ".local" / "bin" / "speed-of-cinnamon"
            man_target = home / ".local" / "share" / "man" / "man1"
            repo_root = self._copy_installable_minimal_repo(tmp_path)
            marker = tmp_path / "exchange-failure-triggered"
            corruption_marker = tmp_path / "corruption-triggered"
            real_helper = repo_root / "scripts" / "safe-local-fs-real.py"
            shutil.copy2(repo_root / "scripts" / "safe-local-fs.py", real_helper)
            helper = repo_root / "scripts" / "safe-local-fs.py"
            helper.write_text(
                "from pathlib import Path\n"
                "import shutil\n"
                "import subprocess\n"
                "import sys\n"
                f"real_helper = Path({str(real_helper)!r})\n"
                f"marker = Path({str(marker)!r})\n"
                f"corruption_marker = Path({str(corruption_marker)!r})\n"
                "if len(sys.argv) > 1 and sys.argv[1] == 'exchange' and not marker.exists():\n"
                "    marker.write_text('1', encoding='utf-8')\n"
                "    shutil.rmtree(sys.argv[3])\n"
                "    raise SystemExit(77)\n"
                "result = subprocess.run([sys.executable, str(real_helper), *sys.argv[1:]], check=False)\n"
                f"if result.returncode == 0 and len(sys.argv) > 4 and sys.argv[1:3] == ['replace', 'install'] and sys.argv[4] == {str(python_target)!r} and not corruption_marker.exists():\n"
                "    Path(sys.argv[4], 'cli.py').write_text('late corruption\\n', encoding='utf-8')\n"
                "    corruption_marker.write_text('1', encoding='utf-8')\n"
                "raise SystemExit(result.returncode)\n",
                encoding="utf-8",
            )
            applet_target.mkdir(parents=True)
            python_target.mkdir(parents=True)
            wrapper_target.parent.mkdir(parents=True)
            man_target.mkdir(parents=True)
            (applet_target / "metadata.json").write_text('{"generation":"old"}\n', encoding="utf-8")
            (python_target / "cli.py").write_text("OLD_PAYLOAD\n", encoding="utf-8")
            wrapper_target.write_text("OLD_WRAPPER\n", encoding="utf-8")
            (man_target / "speed-of-cinnamon.1").write_text("OLD_MAN\n", encoding="utf-8")
            (man_target / "speed-of-cinnamon-alarms.1").write_text("OLD_ALARMS\n", encoding="utf-8")

            result = self._run_install_local(repo_root, home)

            stages = list(app_data.glob("install-stage-*"))
            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("preserving install recovery workspace", result.stderr)
            self.assertIn("rollback failed", result.stderr)
            self.assertTrue(marker.exists())
            self.assertEqual(len(stages), 1)

    def test_install_local_rolls_back_wrapper_corruption_after_activation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            home = tmp_path / "home"
            home.mkdir()
            wrapper_target = home / ".local" / "bin" / "speed-of-cinnamon"
            hook = (
                "        if (\n"
                "            sys.argv[1:3] == [\"replace\", \"install\"]\n"
                f"            and sys.argv[4] == {str(wrapper_target)!r}\n"
                "            and not Path(MARKER_PATH).exists()\n"
                "        ):\n"
                "            Path(sys.argv[4]).write_text(\"wrapper corruption\\n\", encoding=\"utf-8\")\n"
                "            Path(MARKER_PATH).write_text(\"1\", encoding=\"utf-8\")\n"
            )
            repo_root, marker = self._copy_installable_repo_with_post_activation_hook(tmp_path, hook)

            result = self._run_install_local(repo_root, home)

            self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            self.assertIn("installed wrapper verification failed", result.stderr)
            self.assertTrue(marker.exists())
            self.assertFalse(wrapper_target.exists())
            self.assertFalse(list((home / ".local" / "share" / "speed-of-cinnamon").glob("install-stage-*")))

    def test_install_local_rejects_extra_file_symlink_and_hardlink(self) -> None:
        cases = {
            "extra-file": (
                "            Path(sys.argv[4], \"unexpected.py\").write_text(\"unexpected\\n\", encoding=\"utf-8\")\n"
            ),
            "symlink": (
                "            Path(sys.argv[4], \"unexpected-link.py\").symlink_to(Path(MARKER_PATH))\n"
            ),
            "hardlink": (
                "            source = Path(sys.argv[4], \"cli.py\")\n"
                "            payload = Path(MARKER_PATH).with_name(\"hardlink-payload\")\n"
                "            payload.write_bytes(source.read_bytes())\n"
                "            source.unlink()\n"
                "            os.link(payload, source)\n"
            ),
        }
        for case, mutation in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                hook = (
                    "        if (\n"
                    "            sys.argv[1:3] == [\"replace\", \"install\"]\n"
                    "            and \"/python/speed_of_cinnamon\" in sys.argv[4]\n"
                    "            and not Path(MARKER_PATH).exists()\n"
                    "        ):\n"
                    + mutation
                    + "            Path(MARKER_PATH).write_text(\"1\", encoding=\"utf-8\")\n"
                )
                repo_root, marker = self._copy_installable_repo_with_post_activation_hook(tmp_path, hook)
                home = tmp_path / "home"
                home.mkdir()

                result = self._run_install_local(repo_root, home)

                self.assertNotEqual(result.returncode, 0, msg=result.stdout + result.stderr)
                self.assertIn("installed python package verification failed", result.stderr)
                self.assertTrue(marker.exists())
                self.assertFalse(
                    (home / ".local" / "share" / "speed-of-cinnamon" / "python" / "speed_of_cinnamon").exists()
                )
                self.assertFalse(list((home / ".local" / "share" / "speed-of-cinnamon").glob("install-stage-*")))

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
