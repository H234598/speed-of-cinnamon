from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import path_safety
from speed_of_cinnamon import paths


class PathsTest(unittest.TestCase):
    def test_xdg_path_falls_back_when_home_expansion_fails(self) -> None:
        fallback = Path("/safe/fallback")
        with (
            mock.patch.dict(paths.os.environ, {"XDG_DATA_HOME": "~/data"}, clear=False),
            mock.patch.object(Path, "expanduser", side_effect=RuntimeError("home unavailable")),
        ):
            self.assertEqual(paths._xdg_path("XDG_DATA_HOME", fallback), fallback)

    def test_safe_home_path_uses_private_fallback_when_home_lookup_fails(self) -> None:
        fallback = Path("/tmp") / f"{paths.APP_ID}-fallback"
        with (
            mock.patch.object(Path, "home", side_effect=RuntimeError("home unavailable")),
            mock.patch("speed_of_cinnamon.paths._private_runtime_temp_root", return_value=fallback),
        ):
            self.assertEqual(paths._safe_home_path(".local", "share"), fallback / ".local" / "share")

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

    def test_ensure_runtime_dirs_rejects_symlinked_final_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data_root = base / "data"
            target = base / "target"
            data_root.mkdir()
            target.mkdir()
            (data_root / paths.APP_ID).symlink_to(target, target_is_directory=True)

            with mock.patch.dict(paths.os.environ, {"XDG_DATA_HOME": str(data_root), "XDG_STATE_HOME": str(base / "state"), "XDG_CACHE_HOME": str(base / "cache")}):
                with self.assertRaises(OSError):
                    paths.ensure_runtime_dirs()

    def test_ensure_runtime_dirs_makes_existing_leaf_directories_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            data_root = base / "data"
            state_root = base / "state"
            cache_root = base / "cache"
            for directory in (
                data_root / paths.APP_ID,
                state_root / paths.APP_ID,
                cache_root / paths.APP_ID / "recordings",
            ):
                directory.mkdir(parents=True)
                directory.chmod(0o777)

            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": str(data_root),
                    "XDG_STATE_HOME": str(state_root),
                    "XDG_CACHE_HOME": str(cache_root),
                },
            ):
                paths.ensure_runtime_dirs()

            for directory in (
                data_root / paths.APP_ID,
                state_root / paths.APP_ID,
                cache_root / paths.APP_ID,
                cache_root / paths.APP_ID / "recordings",
            ):
                self.assertEqual(directory.stat().st_mode & 0o077, 0)

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

    def test_safe_home_path_fails_closed_without_nofollow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            real_home = base / "real-home"
            real_home.mkdir()
            home_link = base / "home-link"
            home_link.symlink_to(real_home, target_is_directory=True)
            temp_root = base / "temp"
            temp_root.mkdir()

            with (
                mock.patch("speed_of_cinnamon.paths.Path.home", return_value=home_link),
                mock.patch("speed_of_cinnamon.paths.tempfile.gettempdir", return_value=str(temp_root)),
                mock.patch("speed_of_cinnamon.paths.os.O_NOFOLLOW", None, create=True),
            ):
                with self.assertRaisesRegex(RuntimeError, "secure temporary directory open is not supported"):
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

    def test_xdg_paths_do_not_resolve_after_symlink_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            custom = Path(tmp) / "custom-data"
            with (
                mock.patch.dict(paths.os.environ, {"XDG_DATA_HOME": str(custom)}),
                mock.patch.object(Path, "resolve", side_effect=AssertionError("must not canonicalize after check")),
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

    def test_xdg_paths_reject_parent_traversal_roots(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": "/tmp/safe/../data",
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

    def test_xdg_paths_reject_unencodable_roots(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            with mock.patch("speed_of_cinnamon.paths.os.environ.__getitem__", return_value="/tmp/root\ud800"):
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

    def test_xdg_paths_reject_c1_control_roots(self) -> None:
        with mock.patch("speed_of_cinnamon.paths.Path.home", return_value=Path("/home/example")):
            with mock.patch.dict(
                paths.os.environ,
                {
                    "XDG_DATA_HOME": "/tmp/root\x85",
                    "XDG_STATE_HOME": "/tmp/root\\x85",
                    "XDG_CACHE_HOME": "/tmp/root\\u0085",
                },
            ):
                self.assertEqual(paths.xdg_data_home(), Path("/home/example") / ".local" / "share")
                self.assertEqual(paths.xdg_state_home(), Path("/home/example") / ".local" / "state")
                self.assertEqual(paths.xdg_cache_home(), Path("/home/example") / ".cache")

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

    def test_path_safety_symlink_error_does_not_echo_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            target = base / "target"
            target.mkdir()
            secret_link = base / "secret-token-link"
            secret_link.symlink_to(target, target_is_directory=True)

            with self.assertRaisesRegex(RuntimeError, "^input path must not pass through a symlink$") as raised:
                path_safety.assert_no_symlink_ancestors(secret_link / "child", field_name="input path")

            self.assertNotIn("secret-token-link", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
