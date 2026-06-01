from __future__ import annotations

import unittest

from speed_of_cinnamon.postprocessor import (
    PostProcessError,
    post_process_text,
    render_postprocess_template,
)


class PostProcessorTest(unittest.TestCase):
    def test_empty_command_returns_original_text(self) -> None:
        self.assertEqual(post_process_text("hello", "en", ""), "hello")

    def test_command_receives_text_on_stdin(self) -> None:
        command = "python3 -c 'import sys; print(sys.stdin.read().upper())'"
        self.assertEqual(post_process_text("hello cinnamon", "en", command), "HELLO CINNAMON")

    def test_template_quotes_text_language_and_prompt(self) -> None:
        rendered = render_postprocess_template(
            "tool --lang {language} --text {text} --prompt {prompt}",
            "hello cinnamon",
            "de",
            "Use Cinnamon terms.",
            "PipeWire",
        )
        self.assertIn("--lang de", rendered)
        self.assertIn("--text 'hello cinnamon'", rendered)
        self.assertIn("Use Cinnamon terms.", rendered)
        self.assertIn("PipeWire", rendered)

    def test_command_receives_personalization_environment(self) -> None:
        command = "python3 -c \"import os, sys; print(sys.stdin.read().strip() + '|' + os.environ['SPEED_OF_CINNAMON_VOCABULARY'])\""
        self.assertEqual(
            post_process_text("hello", "en", command, "Use project terms.", "PipeWire"),
            "hello|PipeWire",
        )

    def test_empty_output_is_an_error(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "without output"):
            post_process_text("hello", "en", "true")


if __name__ == "__main__":
    unittest.main()
