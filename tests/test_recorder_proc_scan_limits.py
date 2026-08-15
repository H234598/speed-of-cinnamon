import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import recorder


class RecorderProcScanLimitTests(unittest.TestCase):
    def test_bounded_proc_entries_keeps_numeric_snapshot_only(self) -> None:
        entries = (Path("/proc/100"), Path("/proc/thread-self"), Path("/proc/200"))
        with mock.patch.object(recorder.Path, "iterdir", return_value=entries):
            self.assertEqual(
                recorder._bounded_proc_entries(),
                (Path("/proc/100"), Path("/proc/200")),
            )

    def test_bounded_proc_entries_fails_closed_at_limit(self) -> None:
        entries = (
            Path(f"/proc/{index}")
            for index in range(3)
        )
        with (
            mock.patch.object(recorder, "MAX_PROC_DIRECTORY_ENTRIES", 2),
            mock.patch.object(recorder.Path, "iterdir", return_value=entries),
        ):
            self.assertIsNone(recorder._bounded_proc_entries())

    def test_bounded_proc_entries_fails_closed_on_iteration_error(self) -> None:
        def raise_during_iteration():
            yield Path("/proc/100")
            raise OSError("proc disappeared")

        with mock.patch.object(
            recorder.Path,
            "iterdir",
            return_value=raise_during_iteration(),
        ):
            self.assertIsNone(recorder._bounded_proc_entries())


if __name__ == "__main__":
    unittest.main()
