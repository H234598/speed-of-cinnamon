import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from speed_of_cinnamon import app_logging


class LogScanLimitTests(unittest.TestCase):
    def test_bounded_log_paths_returns_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            path = directory / "speed-of-cinnamon-2026-08-01.log"
            path.touch()
            self.assertEqual(app_logging._bounded_log_paths(directory, "*.log"), (path,))

    def test_bounded_log_paths_fails_closed_at_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for index in range(3):
                (directory / f"log-{index}.log").touch()
            with mock.patch.object(app_logging, "MAX_LOG_MAINTENANCE_SCAN_ENTRIES", 2):
                with self.assertRaisesRegex(RuntimeError, "log directory exceeds scan budget"):
                    app_logging._bounded_log_paths(directory, "*.log")

    def test_bounded_log_paths_fails_closed_on_iteration_error(self) -> None:
        def raise_during_iteration():
            yield SimpleNamespace(name="log-0.log")
            raise OSError("directory disappeared")

        with mock.patch.object(
            app_logging,
            "open_directory_without_following_symlinks",
            return_value=123,
        ), mock.patch.object(
            app_logging.os,
            "scandir",
            return_value=mock.MagicMock(
                __enter__=lambda self: raise_during_iteration(),
                __exit__=lambda self, exc_type, exc, traceback: False,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "log directory scan failed"):
                app_logging._bounded_log_paths(Path("/tmp"), "*.log")


if __name__ == "__main__":
    unittest.main()
