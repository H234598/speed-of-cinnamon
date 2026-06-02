from __future__ import annotations

import unittest
import os
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import paths


class PathsTest(unittest.TestCase):
    def test_xdg_path_rejects_non_text_environment_variable_name(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "environment variable name must be text"):
            paths._xdg_path(123, Path("/tmp"))  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            paths._contains_escaped_null(1)  # type: ignore[arg-type]

    def test_contains_escaped_null_rejects_bool(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "must be text"):
            paths._contains_escaped_null(True)  # type: ignore[arg-type]

    def test_xdg_data_home_rejects_null_byte(self) -> None:
        with mock.patch("os.environ.get", return_value="x\x00data"):
            self.assertEqual(paths.xdg_data_home(), Path.home() / ".local" / "share")

    def test_xdg_state_home_rejects_escaped_null(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_STATE_HOME": "x\\\\x00state"}):
            self.assertEqual(paths.xdg_state_home(), Path.home() / ".local" / "state")

    def test_xdg_home_rejects_non_text_value(self) -> None:
        with mock.patch.object(paths.os.environ, "get", return_value=123):  # type: ignore[arg-type]
            self.assertEqual(paths.xdg_data_home(), Path.home() / ".local" / "share")

    def test_xdg_cache_home_rejects_escaped_null(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": "x\\u0000cache"}):
            self.assertEqual(paths.xdg_cache_home(), Path.home() / ".cache")

    def test_xdg_data_home_uses_value_if_safe(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_DATA_HOME": "/tmp/speed-of-cinnamon-data"}):
            self.assertEqual(paths.xdg_data_home(), Path("/tmp/speed-of-cinnamon-data"))

    def test_xdg_data_home_trims_whitespace(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_DATA_HOME": "  /tmp/speed-of-cinnamon-data  "}):
            self.assertEqual(paths.xdg_data_home(), Path("/tmp/speed-of-cinnamon-data"))

    def test_xdg_cache_home_rejects_empty_value(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_CACHE_HOME": ""}):
            self.assertEqual(paths.xdg_cache_home(), Path.home() / ".cache")

    def test_xdg_data_home_rejects_oversized_value(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_DATA_HOME": "a" * (paths.MAX_XDG_PATH_CHARS + 1)}):
            self.assertEqual(paths.xdg_data_home(), Path.home() / ".local" / "share")

    def test_xdg_data_home_rejects_oversized_value_bytes(self) -> None:
        with (
            mock.patch("speed_of_cinnamon.paths.MAX_XDG_PATH_CHARS", 4),
            mock.patch.dict("os.environ", {"XDG_DATA_HOME": "é" * 3}),
        ):
            self.assertEqual(paths.xdg_data_home(), Path.home() / ".local" / "share")

    def test_xdg_state_home_rejects_relative_path(self) -> None:
        with mock.patch.dict("os.environ", {"XDG_STATE_HOME": "relative/state-home"}):
            self.assertEqual(paths.xdg_state_home(), Path.home() / ".local" / "state")


if __name__ == "__main__":
    unittest.main()
