from __future__ import annotations

import unittest
import unicodedata
from unittest import mock

from speed_of_cinnamon import profanity_filter
from speed_of_cinnamon.profanity_filter import (
    MAX_PROFANITY_IGNORABLE_CODEPOINTS,
    PROFANITY_REPLACEMENT_PAIRS,
    compile_profanity_replacements,
    parse_profanity_replacement_list,
    render_profanity_replacement_list,
)


class ProfanityFilterTest(unittest.TestCase):
    def test_default_profanity_catalog_contains_exactly_200_entries(self) -> None:
        self.assertEqual(len(PROFANITY_REPLACEMENT_PAIRS), 200)
        self.assertEqual(len({pattern for pattern, _replacement in PROFANITY_REPLACEMENT_PAIRS}), 200)

    def test_parse_profanity_replacement_list_fast_paths_untouched_bundled_list(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.profanity_filter._safe_profanity_pattern_source",
            side_effect=AssertionError("bundled list must not revalidate regexes"),
        ):
            pairs = parse_profanity_replacement_list(render_profanity_replacement_list())

        self.assertEqual(pairs, PROFANITY_REPLACEMENT_PAIRS)

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

    def test_compile_profanity_replacements_keeps_casefold_expanding_literals(self) -> None:
        compiled = compile_profanity_replacements((("ß", "ersetzt"), ("ﬀ", "doppelt")))

        softened = "ß und ﬀ"
        for pattern, replacement in compiled:
            softened = pattern.sub(replacement, softened)

        self.assertEqual(softened, "ersetzt und doppelt")

    def test_compile_profanity_replacements_blocks_common_mixed_script_homoglyphs(self) -> None:
        compiled = compile_profanity_replacements((("fuck", "frog"), ("ass", "donkey")))

        self.assertEqual(compiled[0][0].sub(compiled[0][1], "fu\u0441\u043a"), "frog")
        self.assertEqual(compiled[1][0].sub(compiled[1][1], "\u0430\u0455s"), "donkey")

    def test_compile_profanity_replacements_matches_cjk_adjacent_text(self) -> None:
        text = "我fuck你"
        compiled = compile_profanity_replacements((("fuck", "frog"),), text=text)

        softened = text
        for pattern, replacement in compiled:
            softened = pattern.sub(replacement, softened)

        self.assertEqual(softened, "我frog你")

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

    def test_compile_profanity_replacements_bounds_dynamic_ignorable_character_class(self) -> None:
        ignorable = []
        for codepoint in range(0x110000):
            if unicodedata.category(chr(codepoint)) in profanity_filter._MATCH_IGNORE_CATEGORIES:
                ignorable.append(chr(codepoint))
                if len(ignorable) > MAX_PROFANITY_IGNORABLE_CODEPOINTS * 4:
                    break

        text = "fu" + ignorable[0] + "ck " + " ".join(pattern for pattern, _ in PROFANITY_REPLACEMENT_PAIRS)
        text += "".join(ignorable)
        compiled = compile_profanity_replacements(PROFANITY_REPLACEMENT_PAIRS, text=text)

        fuck_pattern = next(pattern for pattern, replacement in compiled if replacement == "Frickelfrosch")
        self.assertEqual(fuck_pattern.sub("Frickelfrosch", "fu\u200bck"), "Frickelfrosch")
        bounded_source_length = max(
            len(profanity_filter._build_tolerant_profanity_pattern(pattern))
            for pattern, _ in PROFANITY_REPLACEMENT_PAIRS
        )
        self.assertLessEqual(max(len(pattern.pattern) for pattern, _ in compiled), bounded_source_length)

    def test_compile_profanity_replacements_skips_impossible_rules_in_ignorable_text(self) -> None:
        text = "\u200b" * 100_000
        pairs = tuple((f"bad{index}", "safe") for index in range(500))

        self.assertEqual(compile_profanity_replacements(pairs, text=text), ())

    def test_compile_profanity_replacements_skips_impossible_rules_in_normal_text(self) -> None:
        text = "hello world"
        pairs = tuple((f"bad{index}", "safe") for index in range(500))

        self.assertEqual(compile_profanity_replacements(pairs, text=text), ())

    def test_compile_profanity_replacements_keeps_replacement_chain_candidates(self) -> None:
        text = "f\u200boo"
        compiled = compile_profanity_replacements((("foo", "bar"), ("bar", "baz")), text=text)

        softened = text
        for pattern, replacement in compiled:
            softened = pattern.sub(replacement, softened)
        self.assertEqual(softened, "baz")

    def test_compile_profanity_replacements_rejects_invalid_text_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "text must be text"):
            compile_profanity_replacements((("fuck", "frog"),), text=123)  # type: ignore[arg-type]

    def test_compile_profanity_replacements_ignores_empty_normalized_patterns(self) -> None:
        compiled = compile_profanity_replacements((("\u0308", "dangerous"), ("fuck", "frog")))

        self.assertEqual(len(compiled), 1)
        self.assertEqual(compiled[0][1], "frog")

    def test_compile_profanity_replacements_uses_tolerant_defaults_for_invalid_pairs(self) -> None:
        text = "fu\u200bck"
        compiled = compile_profanity_replacements((("\u0308", "invalid"),), text=text)

        softened = text
        for pattern, replacement in compiled:
            softened = pattern.sub(replacement, softened)

        self.assertEqual(softened, "Frickelfrosch")

    def test_compile_profanity_replacements_skips_unrelated_replacement_candidates(self) -> None:
        compiled = compile_profanity_replacements(PROFANITY_REPLACEMENT_PAIRS, text="Scheiße")

        self.assertLess(len(compiled), len(PROFANITY_REPLACEMENT_PAIRS))
        softened = "Scheiße"
        for pattern, replacement in compiled:
            softened = pattern.sub(replacement, softened)
        self.assertNotIn("Scheiße", softened)

    def test_default_profanity_replacements_preserve_trusted_regex_patterns(self) -> None:
        from speed_of_cinnamon.profanity_filter import PROFANITY_REPLACEMENTS

        soften = "scheiße und scheisse"
        for pattern, replacement in PROFANITY_REPLACEMENTS:
            soften = pattern.sub(replacement, soften)

        self.assertNotIn("scheiße", soften)
        self.assertNotIn("scheisse", soften)

    def test_default_profanity_replacements_block_zero_width_variants(self) -> None:
        from speed_of_cinnamon.profanity_filter import PROFANITY_REPLACEMENTS

        soften = "schei\u200bße und schei\u200bßhaus"
        for pattern, replacement in PROFANITY_REPLACEMENTS:
            soften = pattern.sub(replacement, soften)

        self.assertEqual(soften, "Glitzerkram und Glitzerhaus")

    def test_compile_profanity_replacements_blocks_zero_width_in_trusted_regex_patterns(self) -> None:
        text = "schei\u200bße und schei\u200bßhaus"
        compiled = compile_profanity_replacements(PROFANITY_REPLACEMENT_PAIRS, text=text)

        softened = text
        for pattern, replacement in compiled:
            softened = pattern.sub(replacement, softened)

        self.assertEqual(softened, "Glitzerkram und Glitzerhaus")


if __name__ == "__main__":
    unittest.main()
