from __future__ import annotations

import unittest

from speed_of_cinnamon.http_safety import is_loopback_hostname


class HttpSafetyTest(unittest.TestCase):
    def test_is_loopback_hostname_accepts_valid_loopback_forms(self) -> None:
        self.assertTrue(is_loopback_hostname("localhost"))
        self.assertTrue(is_loopback_hostname("127.0.0.1"))
        self.assertTrue(is_loopback_hostname("[::1]"))

    def test_is_loopback_hostname_rejects_malformed_bracket_forms(self) -> None:
        self.assertFalse(is_loopback_hostname("localhost]"))
        self.assertFalse(is_loopback_hostname("[::1"))
        self.assertFalse(is_loopback_hostname("::1]"))

    def test_is_loopback_hostname_rejects_whitespace_padded_hosts(self) -> None:
        for hostname in (" localhost", "localhost ", " 127.0.0.1", "127.0.0.1 ", " [::1]", "[::1] "):
            with self.subTest(hostname=hostname):
                self.assertFalse(is_loopback_hostname(hostname))

    def test_is_loopback_hostname_rejects_non_text_values(self) -> None:
        self.assertFalse(is_loopback_hostname(123))  # type: ignore[arg-type]
        self.assertFalse(is_loopback_hostname(True))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
