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

    def test_template_quotes_text_and_language(self) -> None:
        rendered = render_postprocess_template("tool --lang {language} --text {text}", "hello cinnamon", "de")
        self.assertIn("--lang de", rendered)
        self.assertIn("--text 'hello cinnamon'", rendered)

    def test_empty_output_is_an_error(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "without output"):
            post_process_text("hello", "en", "true")


if __name__ == "__main__":
    unittest.main()
