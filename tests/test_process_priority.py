import unittest
from types import SimpleNamespace
from unittest import mock

from speed_of_cinnamon import process_priority


class ProcessPriorityTests(unittest.TestCase):
    def test_apply_process_priority_sets_requested_cpu_and_io_values(self) -> None:
        with (
            mock.patch.object(process_priority.os, "setpriority") as setpriority,
            mock.patch.object(process_priority.shutil, "which", return_value="/usr/bin/ionice"),
            mock.patch.object(
                process_priority.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0),
            ) as run,
        ):
            result = process_priority.apply_process_priority()

        self.assertEqual(result, (True, True))
        setpriority.assert_called_once_with(
            process_priority.os.PRIO_PROCESS,
            0,
            process_priority.REQUESTED_CPU_NICE,
        )
        run.assert_called_once_with(
            [
                "/usr/bin/ionice",
                "--class",
                process_priority.IO_PRIORITY_CLASS,
                "--classdata",
                process_priority.IO_PRIORITY_LEVEL,
                "--pid",
                mock.ANY,
            ],
            check=False,
            stdout=process_priority.subprocess.DEVNULL,
            stderr=process_priority.subprocess.DEVNULL,
            timeout=process_priority.IONICE_TIMEOUT_SECONDS,
            shell=False,
        )

    def test_priority_failures_fall_back_without_raising(self) -> None:
        with (
            mock.patch.object(process_priority.os, "setpriority", side_effect=PermissionError),
            mock.patch.object(process_priority.shutil, "which", return_value=None),
        ):
            self.assertEqual(process_priority.apply_process_priority(), (False, False))

    def test_ionice_failure_falls_back_without_raising(self) -> None:
        with (
            mock.patch.object(process_priority.os, "setpriority"),
            mock.patch.object(process_priority.shutil, "which", return_value="/usr/bin/ionice"),
            mock.patch.object(
                process_priority.subprocess,
                "run",
                side_effect=process_priority.subprocess.TimeoutExpired("ionice", 1),
            ),
        ):
            self.assertEqual(process_priority.apply_process_priority(), (True, False))


if __name__ == "__main__":
    unittest.main()
