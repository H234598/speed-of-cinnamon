import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from speed_of_cinnamon import recorder


class RecorderProcScanLimitTests(unittest.TestCase):
    def test_bounded_proc_entries_keeps_numeric_snapshot_only(self) -> None:
        entries = (
            SimpleNamespace(name="100", path="/proc/100"),
            SimpleNamespace(name="thread-self", path="/proc/thread-self"),
            SimpleNamespace(name="200", path="/proc/200"),
        )
        scanner = mock.MagicMock()
        scanner.__enter__.return_value = iter(entries)
        with mock.patch.object(recorder.os, "scandir", return_value=scanner):
            self.assertEqual(
                recorder._bounded_proc_entries(),
                (Path("/proc/100"), Path("/proc/200")),
            )

    def test_bounded_proc_entries_fails_closed_at_limit(self) -> None:
        entries = (
            SimpleNamespace(name=str(index), path=f"/proc/{index}")
            for index in range(3)
        )
        scanner = mock.MagicMock()
        scanner.__enter__.return_value = entries
        with (
            mock.patch.object(recorder, "MAX_PROC_DIRECTORY_ENTRIES", 2),
            mock.patch.object(recorder.os, "scandir", return_value=scanner),
        ):
            self.assertIsNone(recorder._bounded_proc_entries())

    def test_bounded_proc_entries_fails_closed_on_iteration_error(self) -> None:
        def raise_during_iteration():
            yield SimpleNamespace(name="100", path="/proc/100")
            raise OSError("proc disappeared")

        scanner = mock.MagicMock()
        scanner.__enter__.return_value = raise_during_iteration()
        with mock.patch.object(
            recorder.os,
            "scandir",
            return_value=scanner,
        ):
            self.assertIsNone(recorder._bounded_proc_entries())


if __name__ == "__main__":
    unittest.main()
