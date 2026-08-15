import unittest
from unittest import mock

from speed_of_cinnamon import process_priority


class LocalModelPriorityTests(unittest.TestCase):
    def test_local_model_scope_lowers_and_restores_cpu_and_io_priority(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=-5),
            mock.patch.object(process_priority, "_current_io_priority", return_value=4),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True) as set_cpu,
            mock.patch.object(process_priority, "_set_io_priority", return_value=True) as set_io,
        ):
            with process_priority.local_model_priority():
                pass

        self.assertEqual(
            set_cpu.call_args_list,
            [mock.call(process_priority.LOCAL_MODEL_CPU_NICE), mock.call(-5)],
        )
        self.assertEqual(
            set_io.call_args_list,
            [
                mock.call(process_priority.LOCAL_MODEL_IO_PRIORITY_LEVEL),
                mock.call("4"),
            ],
        )

    def test_local_model_scope_does_not_stick_nice_for_unprivileged_process(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
            mock.patch.object(process_priority, "_current_io_priority", return_value=4),
            mock.patch.object(process_priority.os, "geteuid", return_value=1000),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True) as set_cpu,
            mock.patch.object(process_priority, "_set_io_priority", return_value=True),
        ):
            with process_priority.local_model_priority():
                pass

        set_cpu.assert_not_called()

    def test_local_model_command_adds_low_priority_wrappers(self) -> None:
        with mock.patch.object(
            process_priority.shutil,
            "which",
            side_effect=lambda name: f"/usr/bin/{name}",
        ):
            self.assertEqual(
                process_priority.local_model_command(["/usr/bin/whisper", "audio.flac"]),
                [
                    "/usr/bin/ionice",
                    "--class",
                    "2",
                    "--classdata",
                    "7",
                    "--",
                    "/usr/bin/nice",
                    "--adjustment",
                    "10",
                    "/usr/bin/whisper",
                    "audio.flac",
                ],
            )
if __name__ == "__main__":
    unittest.main()
