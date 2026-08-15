from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import proc_safety


class ProcSafetyTest(unittest.TestCase):
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
