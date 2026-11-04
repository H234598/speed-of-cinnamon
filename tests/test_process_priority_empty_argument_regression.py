import os
import sys
import unittest
import unittest.mock

from speed_of_cinnamon import process_priority


class ScopeExecEmptyArgumentRegressionTests(unittest.TestCase):
    def test_empty_following_argument_is_forwarded_unchanged(self):
        executable = os.path.realpath(sys.executable)
        spec = process_priority._scope_exec_launch_spec([executable, "", "next"])
        self.assertIsNotNone(spec)
        self.assertEqual(spec.argv, (executable, "", "next"))

    def test_missing_or_empty_executable_is_rejected(self):
        self.assertIsNone(process_priority._scope_exec_launch_spec([]))
        self.assertIsNone(process_priority._scope_exec_launch_spec(["", "next"]))

    def test_non_string_argument_is_rejected(self):
        executable = os.path.realpath(sys.executable)
        self.assertIsNone(process_priority._scope_exec_launch_spec([42]))
        self.assertIsNone(process_priority._scope_exec_launch_spec([executable, 42]))

    def test_nul_in_any_argument_is_rejected(self):
        executable = os.path.realpath(sys.executable)
        self.assertIsNone(
            process_priority._scope_exec_launch_spec([executable + "\x00"])
        )
        self.assertIsNone(
            process_priority._scope_exec_launch_spec([executable, "\x00"])
        )

    def test_read_bounded_ascii_descriptor_retries_interrupted_read(self):
        with unittest.mock.patch(
            "speed_of_cinnamon.process_priority.os.read",
            side_effect=[InterruptedError("EINTR"), b"123\n", b""],
        ) as read_mock:
            result = process_priority._read_bounded_ascii_descriptor(7, max_bytes=16)
        self.assertEqual(result, "123\n")
        self.assertEqual(read_mock.call_count, 3)

    def test_read_proc_file_at_retries_interrupted_read(self):
        with (
            unittest.mock.patch(
                "speed_of_cinnamon.process_priority.os.open",
                return_value=11,
            ),
            unittest.mock.patch(
                "speed_of_cinnamon.process_priority.os.close"
            ) as close_mock,
            unittest.mock.patch(
                "speed_of_cinnamon.process_priority.os.read",
                side_effect=[InterruptedError("EINTR"), b"status-data", b""],
            ) as read_mock,
        ):
            result = process_priority._read_proc_file_at(3, "status", 64)
        self.assertEqual(result, b"status-data")
        self.assertEqual(read_mock.call_count, 3)
        close_mock.assert_called_once_with(11)

    def test_uninterrupted_pipe_and_waitpid_helpers_retry_eintr(self):
        with unittest.mock.patch(
            "speed_of_cinnamon.process_priority.os.read",
            side_effect=[InterruptedError("EINTR"), b"G"],
        ) as read_mock:
            self.assertEqual(process_priority._read_uninterrupted_pipe(4, 2), b"G")
        self.assertEqual(read_mock.call_count, 2)

        with unittest.mock.patch(
            "speed_of_cinnamon.process_priority.os.write",
            side_effect=[InterruptedError("EINTR"), 1],
        ) as write_mock:
            self.assertEqual(process_priority._write_uninterrupted_pipe(5, b"P"), 1)
        self.assertEqual(write_mock.call_count, 2)

        with unittest.mock.patch(
            "speed_of_cinnamon.process_priority.os.waitpid",
            side_effect=[InterruptedError("EINTR"), (0, 0)],
        ) as wait_mock:
            self.assertEqual(
                process_priority._waitpid_uninterrupted(99, os.WNOHANG),
                (0, 0),
            )
        self.assertEqual(wait_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
