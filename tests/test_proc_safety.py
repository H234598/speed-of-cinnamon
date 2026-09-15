from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from speed_of_cinnamon import proc_safety


class ProcSafetyTest(unittest.TestCase):
    def test_bounded_proc_entries_keeps_numeric_snapshot_only(self) -> None:
        entries = ("100", "self", "200")
        scanner = mock.MagicMock()
        scanner.__enter__.return_value = (SimpleNamespace(name=name) for name in entries)
        scanner.__exit__.return_value = False
        with mock.patch.object(proc_safety.os, "scandir", return_value=scanner):
            result = proc_safety._bounded_proc_entries()
        self.assertEqual(result, (Path("/proc/100"), Path("/proc/200")))

    def test_bounded_proc_entries_fails_closed_at_limit(self) -> None:
        entries = tuple(str(pid) for pid in range(3))
        scanner = mock.MagicMock()
        scanner.__enter__.return_value = (SimpleNamespace(name=name) for name in entries)
        scanner.__exit__.return_value = False
        with (
            mock.patch.object(proc_safety.os, "scandir", return_value=scanner),
            mock.patch.object(proc_safety, "MAX_PROC_DIRECTORY_ENTRIES", 2),
        ):
            self.assertIsNone(proc_safety._bounded_proc_entries())

    def test_bounded_proc_entries_fails_closed_on_iteration_error(self) -> None:
        with mock.patch.object(proc_safety.os, "scandir", side_effect=OSError("proc unavailable")):
            self.assertIsNone(proc_safety._bounded_proc_entries())

    def test_process_stat_reader_uses_bounded_ascii_read(self) -> None:
        mocked_open = mock.mock_open(read_data="123 (worker) S 1 2 3\n")
        with mock.patch.object(proc_safety.Path, "open", mocked_open):
            result = proc_safety._read_proc_stat(123)

        self.assertEqual(result, "123 (worker) S 1 2 3")
        mocked_open.assert_called_once_with("r", encoding="ascii")
        mocked_open.return_value.read.assert_called_once_with(proc_safety.MAX_PROC_STAT_BYTES)

    def test_process_stat_path_reader_preserves_io_errors(self) -> None:
        with mock.patch.object(proc_safety.Path, "open", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                proc_safety._read_proc_stat_path(Path("/proc/123/stat"))

    def test_boot_id_reader_uses_bounded_ascii_read(self) -> None:
        mocked_open = mock.mock_open(read_data="boot-id\n")
        with mock.patch.object(proc_safety.Path, "open", mocked_open):
            result = proc_safety._read_proc_boot_id()

        self.assertEqual(result, "boot-id")
        mocked_open.assert_called_once_with("r", encoding="ascii")
        mocked_open.return_value.read.assert_called_once_with(proc_safety.MAX_PROC_BOOT_ID_BYTES)


if __name__ == "__main__":
    unittest.main()
