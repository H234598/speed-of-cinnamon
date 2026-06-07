from __future__ import annotations

import os
import fcntl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon.security_parser import (
    _MAX_BLACKLIST_ENTRIES,
    _MAX_BLACKLIST_FILE_BYTES,
    _MAX_SECURITY_TEXT_CHARS,
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

    def test_parse_security_directives_does_not_treat_mixed_sentence_as_command(self) -> None:
        text = "Hallo blacklisteintrag: geheim"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, [])
        self.assertFalse(directives.show_blacklist)
        self.assertEqual(directives.text, "Hallo blacklisteintrag: geheim")

    def test_parse_security_directives_does_not_treat_show_with_trailing_dictation_as_command(self) -> None:
        text = "Bitte zeige die Blacklist und dann weiter"
        directives = parse_security_directives(text)
        self.assertEqual(directives.added_blacklist, [])
        self.assertFalse(directives.show_blacklist)
        self.assertEqual(directives.text, text)

    def test_parse_security_directives_rejects_oversized_transcript(self) -> None:
        with self.assertRaisesRegex(ValueError, "transcript is too large"):
            parse_security_directives("x" * (_MAX_SECURITY_TEXT_CHARS + 1))

    def test_parse_security_directives_rejects_control_characters(self) -> None:
        bad_inputs = [
            "blacklisteintrag:\rgeheim",
            "blacklisteintrag:\tgeheim",
            "Blacklist anzeigen\x1b",
            "blacklisteintrag:\\rgeheim",
        ]
        for text in bad_inputs:
            with self.subTest(text=repr(text)):
                with self.assertRaisesRegex(ValueError, "invalid control character"):
                    parse_security_directives(text)

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

    def test_apply_security_mode_rejects_oversized_transcript(self) -> None:
        with self.assertRaisesRegex(ValueError, "transcript is too large"):
            apply_security_mode("x" * (_MAX_SECURITY_TEXT_CHARS + 1), [])

    def test_apply_security_mode_rejects_control_characters(self) -> None:
        bad_inputs = [
            "token:\rabc123",
            "token:\tabc123",
            "token:\x1babc123",
            "token:\\x1babc123",
            "token:\\x85abc123",
            "token:\\u001babc123",
            "token:\\u0085abc123",
        ]
        for text in bad_inputs:
            with self.subTest(text=repr(text)):
                with self.assertRaisesRegex(ValueError, "invalid control character"):
                    apply_security_mode(text, [])

    def test_apply_security_mode_masks_spaced_iban_and_hyphenated_single_name(self) -> None:
        text = "mein name ist Jean-Luc und IBAN DE44 5001 0517 5407 3249 31"
        sanitized, count = apply_security_mode(text, [])

        self.assertIn("[redacted name]", sanitized)
        self.assertIn("[redacted iban]", sanitized)
        self.assertNotIn("Jean-Luc", sanitized)
        self.assertNotIn("DE44 5001", sanitized)
        self.assertGreaterEqual(count, 2)

    def test_apply_security_mode_masks_multi_word_password_values(self) -> None:
        sanitized, count = apply_security_mode("password: ab cd ist gesetzt.", [])

        self.assertIn("[redacted password]", sanitized)
        self.assertNotIn("ab", sanitized)
        self.assertNotIn("cd", sanitized)
        self.assertGreaterEqual(count, 1)

    def test_apply_security_mode_masks_labeled_multisegment_and_long_password_values(self) -> None:
        long_password = "a" * 1500
        sanitized, count = apply_security_mode(f"token: abc def und password: {long_password}", [])

        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted password]", sanitized)
        self.assertNotIn("abc def", sanitized)
        self.assertNotIn(long_password, sanitized)
        self.assertGreaterEqual(count, 2)

    def test_apply_security_mode_masks_spoken_secret_values(self) -> None:
        sanitized, count = apply_security_mode("mein Passwort ist ab cd und token is: abc123", [])

        self.assertIn("[redacted password]", sanitized)
        self.assertIn("[redacted token]", sanitized)
        self.assertNotIn("ab cd", sanitized)
        self.assertNotIn("abc123", sanitized)
        self.assertGreaterEqual(count, 2)

    def test_apply_security_mode_masks_newline_split_sensitive_values(self) -> None:
        text = (
            "token:\nabc123\n\n"
            "passwort:\nab\ncd\n\n"
            "Name:\nMax Mustermann\n"
            "Adresse: Hauptstraße 5"
        )
        sanitized, count = apply_security_mode(text, [])

        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted password]", sanitized)
        self.assertIn("[redacted name]", sanitized)
        self.assertIn("[redacted address]", sanitized)
        self.assertNotIn("abc123", sanitized)
        self.assertNotIn("ab\ncd", sanitized)
        self.assertNotIn("Max Mustermann", sanitized)
        self.assertNotIn("Hauptstraße 5", sanitized)
        self.assertGreaterEqual(count, 4)

    def test_apply_security_mode_stops_spoken_name_at_plain_conjunction(self) -> None:
        sanitized, count = apply_security_mode("mein name ist Anna und gehe jetzt weiter", [])

        self.assertEqual(sanitized, "[redacted name] und gehe jetzt weiter")
        self.assertEqual(count, 1)

    def test_apply_security_mode_masks_unaccented_german_spoken_labels(self) -> None:
        sanitized, count = apply_security_mode(
            "ich bin Max Mustermann und token heise abc123 und passwort heise blau",
            [],
        )

        self.assertIn("[redacted name]", sanitized)
        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted password]", sanitized)
        self.assertNotIn("Max Mustermann", sanitized)
        self.assertNotIn("abc123", sanitized)
        self.assertNotIn("blau", sanitized)
        self.assertGreaterEqual(count, 3)

    def test_apply_security_mode_masks_long_spoken_secrets_and_names_without_tail_leaks(self) -> None:
        long_secret = "a" * 1500
        long_name = (
            "Anna Berta Carla Dora Elsa Frieda Greta Helga Ida Julia Karin Laura Maria Nora "
            "Olga Paula Rosa Sabine Tina Ulla Vera"
        )
        sanitized, count = apply_security_mode(
            f"mein name ist {long_name} und token heise {long_secret}.",
            [],
        )

        self.assertIn("[redacted name]", sanitized)
        self.assertIn("[redacted token]", sanitized)
        self.assertNotIn(long_name, sanitized)
        self.assertNotIn(long_secret, sanitized)
        self.assertGreaterEqual(count, 2)

    def test_apply_security_mode_does_not_mask_common_negative_status_phrases(self) -> None:
        sanitized, count = apply_security_mode("password is not set und token ist nicht gesetzt.", [])

        self.assertEqual(sanitized, "password is not set und token ist nicht gesetzt.")
        self.assertEqual(count, 0)

    def test_apply_security_mode_masks_bare_spoken_word_secret_values(self) -> None:
        sanitized, count = apply_security_mode("token ab cd und password blau gruen, aber token invalid bleibt.", [])

        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted password]", sanitized)
        self.assertNotIn("ab cd", sanitized)
        self.assertNotIn("blau", sanitized)
        self.assertNotIn("gruen", sanitized)
        self.assertIn("token invalid bleibt", sanitized)
        self.assertGreaterEqual(count, 2)

    def test_apply_security_mode_masks_bare_secret_words_past_conjunctions(self) -> None:
        sanitized, count = apply_security_mode("token alpha und beta und password rot und blau", [])

        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted password]", sanitized)
        self.assertNotIn("alpha und beta", sanitized)
        self.assertNotIn("rot und blau", sanitized)
        self.assertGreaterEqual(count, 2)

    def test_apply_security_mode_masks_long_bare_spoken_word_secret_values(self) -> None:
        phrase = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"
        sanitized, count = apply_security_mode(f"token {phrase}, danach weiter.", [])

        self.assertIn("[redacted token]", sanitized)
        self.assertNotIn(phrase, sanitized)
        self.assertGreaterEqual(count, 1)

    def test_apply_security_mode_does_not_mask_spoken_token_status_values(self) -> None:
        sanitized, count = apply_security_mode("token ist aktiv und password is gesetzt.", [])

        self.assertEqual(sanitized, "token ist aktiv und password is gesetzt.")
        self.assertEqual(count, 0)

    def test_apply_security_mode_masks_broader_personal_information(self) -> None:
        text = (
            "token abc123 Name: Max Mustermann Adresse: Hauptstraße 5 "
            "Kundennummer K-12345 SSN 123-45-6789"
        )
        sanitized, count = apply_security_mode(text, [])

        self.assertIn("[redacted token]", sanitized)
        self.assertIn("[redacted name]", sanitized)
        self.assertIn("[redacted address]", sanitized)
        self.assertIn("[redacted customer id]", sanitized)
        self.assertIn("[redacted id]", sanitized)
        self.assertNotIn("abc123", sanitized)
        self.assertNotIn("Max Mustermann", sanitized)
        self.assertNotIn("Hauptstraße 5", sanitized)
        self.assertNotIn("K-12345", sanitized)
        self.assertNotIn("123-45-6789", sanitized)
        self.assertGreaterEqual(count, 5)

    def test_apply_security_mode_does_not_mask_plain_token_status_words(self) -> None:
        sanitized, count = apply_security_mode("Der token ist invalid und fehlt.", [])

        self.assertEqual(sanitized, "Der token ist invalid und fehlt.")
        self.assertEqual(count, 0)

    def test_apply_security_mode_does_not_mask_iso_dates_as_phone_numbers(self) -> None:
        sanitized, count = apply_security_mode("Release am 2026-06-03 bleibt sichtbar.", [])

        self.assertEqual(sanitized, "Release am 2026-06-03 bleibt sichtbar.")
        self.assertEqual(count, 0)

    def test_apply_security_mode_masks_lowercase_labeled_names(self) -> None:
        sanitized, count = apply_security_mode("name: max mustermann", [])

        self.assertEqual(sanitized, "[redacted name]")
        self.assertEqual(count, 1)

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

    def test_apply_blacklist_mode_rejects_oversized_transcript(self) -> None:
        with self.assertRaisesRegex(ValueError, "transcript is too large"):
            apply_blacklist_mode("x" * (_MAX_SECURITY_TEXT_CHARS + 1), ["x"])

    def test_apply_blacklist_mode_rejects_control_characters(self) -> None:
        for text in ("geheim\tsichtbar", "geheim\rsichtbar", "geheim\x85sichtbar"):
            with self.subTest(text=repr(text)):
                with self.assertRaisesRegex(ValueError, "invalid control character"):
                    apply_blacklist_mode(text, ["geheim"])

    def test_update_blacklist_file_deduplicates_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            entries = update_blacklist_file(path, ["erste", "zweite", "erste"])
            entries = update_blacklist_file(path, ["zweite", "dritte"])

        self.assertEqual(entries, ["erste", "zweite", "dritte"])

    def test_update_blacklist_file_normalizes_added_entries_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            entries = update_blacklist_file(path, [" geheim! ", "GEHEIM", True, "\x00bad", "\tbad", "zweite"])  # type: ignore[list-item]
            content = path.read_text(encoding="utf-8")

        self.assertEqual(entries, ["geheim", "zweite"])
        self.assertEqual(content, "geheim\nzweite\n")

    def test_update_blacklist_file_fails_closed_on_corrupt_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_bytes(b"bestehend\n\xff")

            with self.assertRaises(ValueError):
                update_blacklist_file(path, ["neu"])

            self.assertEqual(path.read_bytes(), b"bestehend\n\xff")

    def test_update_blacklist_file_fails_closed_on_oversized_existing_entry_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_text("\n".join(f"entry-{index}" for index in range(_MAX_BLACKLIST_ENTRIES + 1)), encoding="utf-8")

            with self.assertRaises(ValueError):
                update_blacklist_file(path, ["neu"])

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

    def test_update_blacklist_file_rejects_hardlinked_existing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            lock_path = path.with_name(f".{path.name}.lock")
            backing = Path(tmp) / "foreign-lock"
            backing.write_text("lock\n", encoding="utf-8")
            backing.chmod(0o644)
            try:
                os.link(backing, lock_path)
            except OSError as exc:
                self.skipTest(f"hardlinks unavailable: {exc}")

            with self.assertRaisesRegex(ValueError, "failed to lock blacklist file"):
                update_blacklist_file(path, ["geheim"])

            self.assertTrue(lock_path.exists())
            self.assertTrue(backing.exists())
            self.assertEqual(backing.stat().st_mode & 0o777, 0o644)

    def test_load_blacklist_file_rejects_symlink_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.txt"
            target.write_text("geheim\n")
            path = Path(tmp) / "link.txt"
            os.symlink(target, path)
            with self.assertRaisesRegex(ValueError, "blacklist file path is not safe"):
                load_blacklist_file(path)

    def test_load_blacklist_file_strict_rejects_non_regular_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.mkdir()

            with self.assertRaisesRegex(ValueError, "blacklist file is not a regular file"):
                load_blacklist_file(path)
            with self.assertRaisesRegex(ValueError, "blacklist file is not a regular file"):
                load_blacklist_file(path, strict=True)

    def test_load_blacklist_file_strict_rejects_unreadable_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_bytes(b"\xff")

            with self.assertRaises(ValueError):
                load_blacklist_file(path)
            with self.assertRaises(ValueError):
                load_blacklist_file(path, strict=True)

    def test_load_blacklist_file_rejects_file_that_grows_after_size_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_text("geheim\n", encoding="utf-8")
            path.chmod(0o600)

            with mock.patch(
                "speed_of_cinnamon.security_parser.read_text_without_following_symlinks",
                side_effect=OSError("blacklist file is too large"),
            ) as mocked_read:
                with self.assertRaisesRegex(ValueError, "blacklist file is too large"):
                    load_blacklist_file(path)
                with self.assertRaisesRegex(ValueError, "blacklist file is too large"):
                    load_blacklist_file(path, strict=True)

        self.assertEqual(mocked_read.call_count, 2)
        mocked_read.assert_called_with(
            path,
            field_name="blacklist file",
            max_bytes=_MAX_BLACKLIST_FILE_BYTES,
            require_private_mode=True,
        )

    def test_load_blacklist_file_rejects_world_readable_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blacklist.txt"
            path.write_text("geheim\n", encoding="utf-8")
            path.chmod(0o644)

            with self.assertRaisesRegex(ValueError, "failed to read blacklist file"):
                load_blacklist_file(path)

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
            path.chmod(0o600)
            entries = load_blacklist_file(path)

        self.assertEqual(entries, ["geheim", "test_token", "Test Token"])
