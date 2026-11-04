import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
