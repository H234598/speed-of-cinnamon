from __future__ import annotations

import os
import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.security_parser import (
    _MAX_BLACKLIST_ENTRIES,
    apply_blacklist_mode,
    apply_security_mode,
    parse_security_directives,
    load_blacklist_file,
    update_blacklist_file,
)


class SecurityParserTest(unittest.TestCase):
    def test_parse_security_directives_extracts_blacklist_entries_and_show_command(self) -> None:
        text = "blacklisteintrag: geheim\nBlacklist anzeigen"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, ["geheim"])
        self.assertTrue(directives.show_blacklist)
        self.assertEqual(directives.text, "")

    def test_parse_security_directives_preserves_mixed_dictation_side_effects(self) -> None:
        text = "blacklisteintrag: geheim\nHallo\nBlacklist anzeigen\nnoch"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, ["geheim"])
        self.assertTrue(directives.show_blacklist)
        self.assertEqual(directives.text, "Hallo\nnoch")

    def test_parse_security_directives_trims_punctuation_from_add_entry(self) -> None:
        text = "blacklisteintrag: geheim!"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, ["geheim"])

    def test_parse_security_directives_detects_show_phrases(self) -> None:
        text = "zeige die Blacklist anzeigen"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, [])
        self.assertTrue(directives.show_blacklist)
        self.assertEqual(directives.text, "")

    def test_parse_security_directives_detects_show_alt_phrase(self) -> None:
        text = "Bitte blacklist zeigen"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, [])
        self.assertTrue(directives.show_blacklist)
        self.assertEqual(directives.text, "")

    def test_parse_security_directives_add_without_colon(self) -> None:
        text = "blacklisteintrag geheim"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, ["geheim"])
        self.assertEqual(directives.text, "")

    def test_parse_security_directives_strips_inline_add_from_output(self) -> None:
        text = "Hallo blacklisteintrag: geheim"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, ["geheim"])
        self.assertFalse(directives.show_blacklist)
        self.assertEqual(directives.text, "Hallo")

    def test_parse_security_directives_detects_show_phrase_with_open(self) -> None:
        text = "Bitte Blacklist öffnen"
        directives = parse_security_directives(text)
        self.assertTrue(directives.show_blacklist)
        self.assertEqual(directives.text, "")

    def test_apply_security_mode_masks_sensitive_tokens_and_bank_terms(self) -> None:
        text = "mein token ist token: abc123 und meine iban DE44500105175407324931 ist aktiv."
        sanitized, count = apply_security_mode(text, [])
        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted iban]", sanitized)
        self.assertGreater(count, 0)

    def test_apply_security_mode_masks_blacklist_items_case_insensitive(self) -> None:
        text = "Das GeHeIm hier steht. Und noch GEHEIM."
        sanitized, count = apply_security_mode(text, ["geheim"])
        self.assertEqual(sanitized, "Das [redacted blacklist item] hier steht. Und noch [redacted blacklist item].")
        self.assertEqual(count, 2)

    def test_apply_blacklist_mode_targets_only_blacklist_entries(self) -> None:
        text = "token xxyyyy und geheim! Und noch GEHEIM."
        sanitized, count = apply_blacklist_mode(text, ["geheim"])
        self.assertEqual(count, 2)
        self.assertEqual(sanitized, "token xxyyyy und [redacted blacklist item]! Und noch [redacted blacklist item].")

    def test_apply_blacklist_mode_masks_entries_with_non_word_boundaries(self) -> None:
        text = "Compiler C++ and @token stay hidden, but XC++Y stays visible."
        sanitized, count = apply_blacklist_mode(text, ["C++", "@token"])
        self.assertEqual(count, 2)
        self.assertEqual(
            sanitized,
            "Compiler [redacted blacklist item] and [redacted blacklist item] stay hidden, but XC++Y stays visible.",
        )

    def test_apply_blacklist_mode_caps_direct_entries_before_regex_build(self) -> None:
        blacklist = [f"secret-{index}" for index in range(_MAX_BLACKLIST_ENTRIES + 5)]
        text = f"secret-0 secret-{_MAX_BLACKLIST_ENTRIES - 1} secret-{_MAX_BLACKLIST_ENTRIES}"
        sanitized, count = apply_blacklist_mode(text, blacklist)

        self.assertEqual(count, 2)
        self.assertIn("[redacted blacklist item]", sanitized)
        self.assertIn(f"secret-{_MAX_BLACKLIST_ENTRIES}", sanitized)

    def test_apply_blacklist_mode_ignores_non_text_direct_entries(self) -> None:
        sanitized, count = apply_blacklist_mode("visible geheim", ["geheim", True])  # type: ignore[list-item]

        self.assertEqual(count, 1)
        self.assertEqual(sanitized, "visible [redacted blacklist item]")

    def test_update_blacklist_file_deduplicates_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            entries = update_blacklist_file(path, ["erste", "zweite", "erste"])
            entries = update_blacklist_file(path, ["zweite", "dritte"])

        self.assertEqual(entries, ["erste", "zweite", "dritte"])

    def test_update_blacklist_file_normalizes_added_entries_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            entries = update_blacklist_file(path, [" geheim! ", "GEHEIM", True, "\x00bad", "zweite"])  # type: ignore[list-item]
            content = path.read_text(encoding="utf-8")

        self.assertEqual(entries, ["geheim", "zweite"])
        self.assertEqual(content, "geheim\nzweite\n")

    def test_update_blacklist_file_writes_through_secure_temp_fd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            with mock.patch("speed_of_cinnamon.security_parser.Path.open", side_effect=AssertionError("reopened temp path")):
                entries = update_blacklist_file(path, ["geheim"])

            content = path.read_text(encoding="utf-8")

        self.assertEqual(entries, ["geheim"])
        self.assertEqual(content, "geheim\n")

    def test_update_blacklist_file_locks_read_modify_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            with mock.patch("speed_of_cinnamon.security_parser.fcntl.flock") as mocked_flock:
                entries = update_blacklist_file(path, ["geheim"])

        self.assertEqual(entries, ["geheim"])
        self.assertEqual(
            [call.args[1] for call in mocked_flock.call_args_list],
            [fcntl.LOCK_EX, fcntl.LOCK_UN],
        )

    def test_load_blacklist_file_rejects_symlink_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.txt"
            target.write_text("geheim\n")
            path = Path(tmp) / "link.txt"
            os.symlink(target, path)
            entries = load_blacklist_file(path)
        self.assertEqual(entries, [])

    def test_update_blacklist_file_does_not_follow_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.txt"
            target.write_text("bestehen")
            path = Path(tmp) / "link.txt"
            os.symlink(target, path)
            with self.assertRaises(ValueError):
                update_blacklist_file(path, ["nein"])
            self.assertEqual(target.read_text(encoding="utf-8"), "bestehen")

    def test_load_blacklist_file_normalizes_and_deduplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_text(
                "\n".join(
                    [
                        "  geheim! ",
                        "GEHEIM",
                        "  ",
                        "geheim ",
                        "test_token! ",
                        "",
                        "\x00bad",
                        "Test   Token",
                    ],
                ),
                encoding="utf-8",
            )
            entries = load_blacklist_file(path)

        self.assertEqual(entries, ["geheim", "test_token", "Test Token"])
