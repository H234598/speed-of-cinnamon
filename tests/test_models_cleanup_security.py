import unittest

from speed_of_cinnamon import models


class ModelCleanupSecurityTests(unittest.TestCase):
    def test_cleanup_failure_note_does_not_leak_error_details(self) -> None:
        primary = models.ModelError("primary failure")

        models._note_cleanup_failure(primary, OSError("secret path and descriptor details"))

        self.assertEqual(primary.__notes__, ["model artifact cleanup failed"])


if __name__ == "__main__":
    unittest.main()
