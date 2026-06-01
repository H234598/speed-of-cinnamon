from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from speed_of_cinnamon.postprocessor import (
    PostProcessError,
    build_ollama_prompt,
    post_process_text,
    render_postprocess_template,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object] | str) -> None:
        if isinstance(payload, str):
            self.data = payload.encode("utf-8")
        else:
            self.data = json.dumps(payload).encode("utf-8")
        self.buffer = io.BytesIO(self.data)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def read(self) -> bytes:
        return self.buffer.read()


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

    def test_disabled_backend_returns_original_text(self) -> None:
        self.assertEqual(post_process_text("hello", "en", backend="none"), "hello")

    def test_ollama_prompt_includes_context_vocabulary_and_text(self) -> None:
        prompt = build_ollama_prompt(
            "hallo cinnamon",
            "de",
            "Use project wording.",
            "PipeWire",
            "Fix spelling only.",
        )
        self.assertIn("Fix spelling only.", prompt)
        self.assertIn("Language: de", prompt)
        self.assertIn("Use project wording.", prompt)
        self.assertIn("PipeWire", prompt)
        self.assertIn("hallo cinnamon", prompt)

    def test_ollama_backend_calls_generate_endpoint(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"response": "Hello Cinnamon."})

        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", side_effect=fake_urlopen):
            result = post_process_text(
                "hello cinnamon",
                "en",
                backend="ollama",
                ollama_model="llama3.2:3b",
                ollama_url="http://127.0.0.1:11434/",
            )
        self.assertEqual(result, "Hello Cinnamon.")
        request, timeout = requests[0]
        self.assertEqual(timeout, 180)
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "llama3.2:3b")
        self.assertFalse(body["stream"])
        self.assertIn("hello cinnamon", body["prompt"])

    def test_ollama_backend_requires_model(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "model is required"):
            post_process_text("hello", "en", backend="ollama")

    def test_ollama_empty_response_is_an_error(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", return_value=FakeResponse({"response": ""})):
            with self.assertRaisesRegex(PostProcessError, "without output"):
                post_process_text("hello", "en", backend="ollama", ollama_model="llama3.2:3b")


if __name__ == "__main__":
    unittest.main()
