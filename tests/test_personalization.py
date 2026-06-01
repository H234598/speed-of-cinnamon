from __future__ import annotations

import unittest

from speed_of_cinnamon.personalization import (
    build_personalization_prompt,
    command_environment,
    normalize_context,
    normalize_vocabulary,
    vocabulary_terms,
)


class PersonalizationTest(unittest.TestCase):
    def test_context_is_trimmed_without_flattening_lines(self) -> None:
        self.assertEqual(normalize_context("  Work notes  \nUse concise German.  \n"), "Work notes\nUse concise German.")

    def test_vocabulary_accepts_one_term_per_line(self) -> None:
        self.assertEqual(vocabulary_terms("Teladi\n- PipeWire\n\nSpeed of Cinnamon"), ["Teladi", "PipeWire", "Speed of Cinnamon"])
        self.assertEqual(normalize_vocabulary("Teladi\nPipeWire"), "Teladi\nPipeWire")

    def test_prompt_combines_context_and_vocabulary(self) -> None:
        prompt = build_personalization_prompt("Use project terms.", "PipeWire\nCinnamon")
        self.assertIn("Context:\nUse project terms.", prompt)
        self.assertIn("Vocabulary:\n- PipeWire\n- Cinnamon", prompt)

    def test_command_environment_exposes_prompt(self) -> None:
        env = command_environment("Use project terms.", "PipeWire")
        self.assertEqual(env["SPEED_OF_CINNAMON_CONTEXT"], "Use project terms.")
        self.assertEqual(env["SPEED_OF_CINNAMON_VOCABULARY"], "PipeWire")
        self.assertIn("Vocabulary:\n- PipeWire", env["SPEED_OF_CINNAMON_PROMPT"])


if __name__ == "__main__":
    unittest.main()
