from __future__ import annotations

import unittest

from speed_of_cinnamon.profanity_filter import (
    compile_profanity_replacements,
    parse_profanity_replacement_list,
)


class ProfanityFilterTest(unittest.TestCase):
    def test_parse_profanity_replacement_list_ignores_unencodable_values(self) -> None:
        pairs = parse_profanity_replacement_list("bad\ud800 -> harmless\nfuck -> frog\n")

        self.assertEqual(pairs, (("fuck", "frog"),))

    def test_compile_profanity_replacements_ignores_unencodable_values(self) -> None:
        compiled = compile_profanity_replacements((("bad\ud800", "harmless"), ("fuck", "frog")))

        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0][1], "frog")


if __name__ == "__main__":
    unittest.main()
