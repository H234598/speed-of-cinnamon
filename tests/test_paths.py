from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import path_safety
from speed_of_cinnamon import paths


class PathsTest(unittest.TestCase):
    def test_xdg_path_rejects_non_text_environment_values(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.os.environ.__getitem__", return_value=123):
            self.assertEqual(paths.xdg_data_home(), Path.home() / ".local" / "share")
            self.assertEqual(paths.xdg_state_home(), Path.home() / ".local" / "state")
            self.assertEqual(paths.xdg_cache_home(), Path.home() / ".cache")

    def test_xdg_paths_reject_symlinked_environment_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real = base / "real"
            real.mkdir()
            link = base / "link"
            link.symlink_to(real, target_is_directory=True)

            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": str(link),
                    "XDG_STATE_HOME": str(link),
                    "XDG_CACHE_HOME": str(link),
                },
            ):
                self.assertEqual(paths.xdg_data_home(), Path.home() / ".local" / "share")
                self.assertEqual(paths.xdg_state_home(), Path.home() / ".local" / "state")
                self.assertEqual(paths.xdg_cache_home(), Path.home() / ".cache")

    def test_safe_home_path_falls_back_when_home_is_symlinked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_home = base / "real-home"
            real_home.mkdir()
            symlink_home = base / "home-link"
            symlink_home.symlink_to(real_home, target_is_directory=True)
            temp_root = base / "temp"
            temp_root.mkdir()
            private_root = temp_root / f"{paths.APP_ID}-{os.getuid()}"

            with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=symlink_home), mock.patch(
                "speed_of_cinnamon.paths.tempfile.gettempdir", return_value=str(temp_root)
            ):
                self.assertEqual(paths.xdg_data_home(), private_root / ".local" / "share")
                self.assertEqual(paths.xdg_state_home(), private_root / ".local" / "state")
                self.assertEqual(paths.xdg_cache_home(), private_root / ".cache")
                self.assertEqual(private_root.stat().st_mode & 0o077, 0)

    def test_safe_home_path_falls_back_to_tmp_when_tempdir_is_symlinked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_home = base / "real-home"
            real_home.mkdir()
            home_link = base / "home-link"
            home_link.symlink_to(real_home, target_is_directory=True)
            temp_real = base / "temp-real"
            temp_real.mkdir()
            temp_link = base / "temp-link"
            temp_link.symlink_to(temp_real, target_is_directory=True)

            with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=home_link), mock.patch(
                "speed_of_cinnamon.paths.tempfile.gettempdir", return_value=str(temp_link)
            ):
                self.assertEqual(paths.xdg_cache_home(), Path("/tmp") / f"{paths.APP_ID}-{os.getuid()}" / ".cache")

    def test_safe_home_path_rejects_symlinked_private_temp_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_home = base / "real-home"
            real_home.mkdir()
            home_link = base / "home-link"
            home_link.symlink_to(real_home, target_is_directory=True)
            temp_root = base / "temp"
            temp_root.mkdir()
            target = base / "target"
            target.mkdir()
            private_link = temp_root / f"{paths.APP_ID}-{os.getuid()}"
            private_link.symlink_to(target, target_is_directory=True)

            with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=home_link), mock.patch(
                "speed_of_cinnamon.paths.tempfile.gettempdir", return_value=str(temp_root)
            ):
                with self.assertRaises(RuntimeError):
                    paths.xdg_cache_home()

    def test_xdg_paths_accept_absolute_non_symlink_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
                custom = Path(tmp) / "custom-data"
                with mock.patch.dict(
                    paths.os.environ,
                    {
                        "XDG_DATA_HOME": str(custom),
                    },
                ):
                    self.assertEqual(paths.xdg_data_home(), custom)

    def test_xdg_paths_reject_relative_roots(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": "relative-data",
                },
            ):
                self.assertEqual(paths.xdg_data_home(), Path("/home/example") / ".local" / "share")

    def test_xdg_paths_reject_oversized_roots(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            oversized = "/" + ("x" * (paths.MAX_XDG_PATH_CHARS + 1))
            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": oversized,
                },
            ):
                self.assertEqual(paths.xdg_data_home(), Path("/home/example") / ".local" / "share")

    def test_xdg_paths_reject_null_roots(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": "/tmp/root\\x00",
                },
            ):
                self.assertEqual(paths.xdg_data_home(), Path("/home/example") / ".local" / "share")

    def test_xdg_paths_accept_home_subdirectories(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            data_root = Path("/home/example") / ".local"
            allowed = data_root / "share" / "custom"
            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": str(allowed),
                },
            ):
                self.assertEqual(paths.xdg_data_home(), allowed)
                with mock.patch.dict(
                    paths.os.environ,
                    {
                        "XDG_DATA_HOME": str(allowed),
                    },
                ):
                    self.assertEqual(paths.xdg_data_home(), allowed)

    def test_path_safety_rejects_non_path_inputs_before_path_methods(self) -> None:
        with self.assertRaises(RuntimeError):
            path_safety.assert_no_symlink_ancestors("not-a-path", field_name="input")


if __name__ == "__main__":
    unittest.main()
