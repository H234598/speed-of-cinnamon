from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


class InstallLocalTest(unittest.TestCase):
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
        return repo_root

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
            version_result = subprocess.run(
                [str(wrapper), "--version"],
                env=env,
                capture_output=True,
                check=False,
                text=True,
            )

        self.assertEqual(version_result.returncode, 0, msg=version_result.stdout + version_result.stderr)
        self.assertIn(f"speed-of-cinnamon {project_version}", version_result.stdout)

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
