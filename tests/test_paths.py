from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import paths


class PathsTest(unittest.TestCase):
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

            with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=symlink_home):
                self.assertEqual(paths.xdg_data_home(), Path("/tmp") / ".local" / "share")
                self.assertEqual(paths.xdg_state_home(), Path("/tmp") / ".local" / "state")
                self.assertEqual(paths.xdg_cache_home(), Path("/tmp") / ".cache")


if __name__ == "__main__":
    unittest.main()
