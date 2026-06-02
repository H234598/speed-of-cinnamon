# pylint: disable=duplicate-code
from __future__ import annotations

import unittest

from speed_of_cinnamon.text_utils import sanitize_special_chars


class TextUtilsTest(unittest.TestCase):
    def test_sanitize_special_chars_replaces_common_accents(self) -> None:
        self.assertEqual(sanitize_special_chars("Grüße, señor! Ça va?"), "Grusse, senor! Ca va?")

    def test_sanitize_special_chars_keeps_non_latin_when_no_safe_mapping_exists(self) -> None:
        self.assertEqual(sanitize_special_chars("東京"), "東京")

    def test_sanitize_special_chars_filters_spanish_marks(self) -> None:
        self.assertEqual(sanitize_special_chars("¡Hola! ¿Listo?"), "Hola! Listo?")

    def test_sanitize_special_chars_rejects_non_text(self) -> None:
        with self.assertRaises(ValueError):
            sanitize_special_chars(123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
