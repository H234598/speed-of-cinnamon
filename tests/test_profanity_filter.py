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

    def test_parse_profanity_replacement_list_ignores_c1_control_values(self) -> None:
        pairs = parse_profanity_replacement_list("bad\x85 -> harmless\nfuck -> frog\n")

        self.assertEqual(pairs, (("fuck", "frog"),))

    def test_parse_profanity_replacement_list_ignores_empty_normalized_patterns(self) -> None:
        pairs = parse_profanity_replacement_list("\u0308 -> dangerous\nfuck -> frog\n")

        self.assertEqual(pairs, (("fuck", "frog"),))

    def test_compile_profanity_replacements_ignores_unencodable_values(self) -> None:
        compiled = compile_profanity_replacements((("bad\ud800", "harmless"), ("fuck", "frog")))

        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0][1], "frog")

    def test_compile_profanity_replacements_blocks_zero_width_by_default(self) -> None:
        compiled = compile_profanity_replacements((("fuck", "frog"),))

        self.assertEqual(compiled[0][0].sub(compiled[0][1], "Das ist fu\u200Bck im Test."), "Das ist frog im Test.")
        self.assertEqual(compiled[0][0].sub(compiled[0][1], "\u200Bfu\u200Bck\u200D!"), "frog!")
        self.assertEqual(compiled[0][0].sub(compiled[0][1], "f\u2066uck"), "frog")
        self.assertEqual(compiled[0][0].sub(compiled[0][1], "f\u05B0uck"), "frog")
        self.assertEqual(compiled[0][0].sub(compiled[0][1], "x\u200Bfu\u200Bck"), "x\u200Bfu\u200Bck")

    def test_compile_profanity_replacements_blocks_nfd_variant(self) -> None:
        compiled = compile_profanity_replacements((("schön", "blume"),))

        self.assertEqual(compiled[0][0].sub(compiled[0][1], "scho\u0308n"), "blume")

    def test_compile_profanity_replacements_blocks_common_mixed_script_homoglyphs(self) -> None:
        compiled = compile_profanity_replacements((("fuck", "frog"), ("ass", "donkey")))

        self.assertEqual(compiled[0][0].sub(compiled[0][1], "fu\u0441\u043a"), "frog")
        self.assertEqual(compiled[1][0].sub(compiled[1][1], "\u0430\u0455s"), "donkey")

    def test_compile_profanity_replacements_uses_compact_ascii_patterns(self) -> None:
        compiled = compile_profanity_replacements((("fuck", "frog"), ("f.*k", "rainbow")), text="fuck f.*k")

        self.assertEqual(compiled[0][0].sub(compiled[0][1], "fuck"), "frog")
        self.assertEqual(compiled[1][0].sub(compiled[1][1], "f.*k"), "rainbow")

    def test_compile_profanity_replacements_uses_compact_patterns_for_normal_unicode(self) -> None:
        compiled = compile_profanity_replacements((("fuck", "frog"),), text="fuck ä")

        self.assertEqual(compiled[0][0].sub(compiled[0][1], "fuck ä"), "frog ä")

    def test_compile_profanity_replacements_keeps_zero_width_matching_in_text_context(self) -> None:
        text = "fu\u200bck"
        compiled = compile_profanity_replacements((("fuck", "frog"),), text=text)

        self.assertEqual(compiled[0][0].sub(compiled[0][1], text), "frog")

    def test_compile_profanity_replacements_rejects_invalid_text_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "text must be text"):
            compile_profanity_replacements((("fuck", "frog"),), text=123)  # type: ignore[arg-type]

    def test_compile_profanity_replacements_ignores_empty_normalized_patterns(self) -> None:
        compiled = compile_profanity_replacements((("\u0308", "dangerous"), ("fuck", "frog")))

        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0][1], "frog")

    def test_default_profanity_replacements_preserve_trusted_regex_patterns(self) -> None:
        from speed_of_cinnamon.profanity_filter import PROFANITY_REPLACEMENTS

        soften = "scheiße und scheisse"
        for pattern, replacement in PROFANITY_REPLACEMENTS:
            soften = pattern.sub(replacement, soften)

        self.assertNotIn("scheiße", soften)
        self.assertNotIn("scheisse", soften)


if __name__ == "__main__":
    unittest.main()
