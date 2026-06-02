from __future__ import annotations

import unittest
from unittest import mock

from speed_of_cinnamon.personalization import (
    build_personalization_prompt,
    command_environment,
    MAX_PERSONAL_CONTEXT_CHARS,
    MAX_VOCABULARY_CHARS,
    normalize_context,
    normalize_vocabulary,
    vocabulary_terms,
)


class PersonalizationTest(unittest.TestCase):
    def test_context_is_trimmed_without_flattening_lines(self) -> None:
        self.assertEqual(normalize_context("  Work notes  \nUse concise German.  \n"), "Work notes\nUse concise German.")

    def test_context_rejects_invalid_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "personal context contains invalid null byte"):
            normalize_context("hello\\x00world")

    def test_context_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            normalize_context(123)  # type: ignore[arg-type]

    def test_context_rejects_oversized_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "personal context is too large"):
            normalize_context("x" * (MAX_PERSONAL_CONTEXT_CHARS + 1))

    def test_context_rejects_oversized_input_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.personalization.MAX_PERSONAL_CONTEXT_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "personal context is too large"):
                normalize_context("😀" * 2)

    def test_vocabulary_accepts_one_term_per_line(self) -> None:
        self.assertEqual(vocabulary_terms("Teladi\n- PipeWire\n\nSpeed of Cinnamon"), ["Teladi", "PipeWire", "Speed of Cinnamon"])
        self.assertEqual(normalize_vocabulary("Teladi\nPipeWire"), "Teladi\nPipeWire")

    def test_vocabulary_rejects_invalid_null_byte(self) -> None:
        with self.assertRaisesRegex(ValueError, "vocabulary contains invalid null byte"):
            vocabulary_terms("PipeWire\\x00")

    def test_vocabulary_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be text"):
            vocabulary_terms(True)  # type: ignore[arg-type]

    def test_vocabulary_rejects_oversized_input(self) -> None:
        with self.assertRaisesRegex(ValueError, "vocabulary is too large"):
            vocabulary_terms("x" * (MAX_VOCABULARY_CHARS + 1))

    def test_vocabulary_rejects_oversized_input_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.personalization.MAX_VOCABULARY_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "vocabulary is too large"):
                vocabulary_terms("😀" * 2)

    def test_prompt_combines_context_and_vocabulary(self) -> None:
        prompt = build_personalization_prompt("Use project terms.", "PipeWire\nCinnamon")
        self.assertIn("Context:\nUse project terms.", prompt)
        self.assertIn("Vocabulary:\n- PipeWire\n- Cinnamon", prompt)

    def test_prompt_rejects_oversized_context(self) -> None:
        with self.assertRaisesRegex(ValueError, "personal context is too large"):
            build_personalization_prompt("x" * (MAX_PERSONAL_CONTEXT_CHARS + 1), "PipeWire")

    def test_command_environment_exposes_prompt(self) -> None:
        env = command_environment("Use project terms.", "PipeWire")
        self.assertEqual(env["SPEED_OF_CINNAMON_CONTEXT"], "Use project terms.")
        self.assertEqual(env["SPEED_OF_CINNAMON_VOCABULARY"], "PipeWire")
        self.assertIn("Vocabulary:\n- PipeWire", env["SPEED_OF_CINNAMON_PROMPT"])
        self.assertIn("PATH", env)

    def test_command_environment_rejects_oversized_payload(self) -> None:
        with self.assertRaisesRegex(ValueError, "personal context is too large"):
            command_environment("x" * (MAX_PERSONAL_CONTEXT_CHARS + 1), "PipeWire")
        with self.assertRaisesRegex(ValueError, "vocabulary is too large"):
            command_environment("Use terms", "x" * (MAX_VOCABULARY_CHARS + 1))

    def test_command_environment_filters_dangerous_variables(self) -> None:
        with mock.patch.dict("speed_of_cinnamon.personalization.os.environ", {
            "LD_PRELOAD": "malicious-lib.so",
            "PYTHONPATH": "/tmp/evil",
            "HOME": "/home/test",
            "LANG": "en_US.UTF-8",
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
        }, clear=True):
            env = command_environment("Use project terms.", "PipeWire")

        self.assertNotIn("LD_PRELOAD", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "unix:path=/run/user/1000/bus")
        self.assertEqual(env["PATH"], "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

    def test_command_environment_rejects_oversized_payload_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.personalization.MAX_PERSONAL_CONTEXT_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "personal context is too large"):
                command_environment("😀" * 2, "PipeWire")
        with mock.patch("speed_of_cinnamon.personalization.MAX_VOCABULARY_CHARS", 4):
            with self.assertRaisesRegex(ValueError, "vocabulary is too large"):
                command_environment("Use terms", "😀" * 2)


if __name__ == "__main__":
    unittest.main()
