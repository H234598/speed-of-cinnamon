from __future__ import annotations

import io
import json
import unittest
from unittest import mock

from speed_of_cinnamon.postprocessor import (
    PostProcessError,
    build_ollama_prompt,
    build_openai_compatible_messages,
    post_process_text,
    MAX_POSTPROCESS_JSON_BYTES,
    MAX_POSTPROCESS_URL_CHARS,
    list_ollama_models,
    list_openai_compatible_models,
    render_postprocess_template,
)
from speed_of_cinnamon.command_chain import CommandChainError


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

    def read(self, size: int = -1) -> bytes:
        return self.buffer.read(size)


class PostProcessorTest(unittest.TestCase):
    def test_empty_command_returns_original_text(self) -> None:
        self.assertEqual(post_process_text("hello", "en", ""), "hello")

    def test_command_receives_text_on_stdin(self) -> None:
        command = "python3 -c 'import sys; print(sys.stdin.read().upper())'"
        self.assertEqual(post_process_text("hello cinnamon", "en", command), "HELLO CINNAMON")

    def test_post_process_chain_passes_output_between_segments(self) -> None:
        command = (
            "python3 -c 'import sys; print(sys.stdin.read().strip().upper())' && "
            "python3 -c 'import sys; print(sys.stdin.read().strip())'"
        )
        self.assertEqual(post_process_text("hello", "en", command), "HELLO")

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

    def test_post_process_command_rejects_unsupported_shell_operators(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "unsupported shell operator"):
            post_process_text("hello", "en", "python3 -c 'print(1)' | python3 -c 'print(2)'")

    def test_post_process_command_rejects_invalid_syntax(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "invalid post-process command"):
            post_process_text("hello", "en", "python3 -c 'unterminated")

    def test_post_process_command_reports_missing_binary(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "command not found"):
            post_process_text("hello", "en", "/definitely/missing/command")

    def test_post_process_reports_empty_chain(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor.run_command_chain",
            side_effect=CommandChainError("post-process command chain is empty"),
        ):
            with self.assertRaisesRegex(PostProcessError, "command chain is empty"):
                post_process_text("hello", "en", "cmd")

    def test_post_process_reports_invalid_chain_limits(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor.run_command_chain",
            side_effect=CommandChainError("max_input_chars must be non-negative"),
        ):
            with self.assertRaisesRegex(PostProcessError, "max_input_chars must be non-negative"):
                post_process_text("hello", "en", "cmd")

    def test_empty_output_is_an_error(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "without output"):
            post_process_text("hello", "en", "true")

    def test_post_process_command_rejects_oversized_output(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.MAX_COMMAND_OUTPUT_CHARS", 4):
            with self.assertRaisesRegex(PostProcessError, "too large"):
                post_process_text("hello", "en", "python3 -c 'print(\"toolong\")'")

    def test_post_process_command_rejects_oversized_input_text(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.MAX_POSTPROCESS_TEXT_CHARS", 4):
            with self.assertRaisesRegex(PostProcessError, "input text is too large"):
                post_process_text("hello", "en", "printf keep")

    def test_post_process_command_rejects_oversized_remote_response(self) -> None:
        giant = "{" + '"x":' + '"' * (MAX_POSTPROCESS_JSON_BYTES + 1) + "}"
        with mock.patch(
            "speed_of_cinnamon.postprocessor.urllib.request.urlopen",
            return_value=FakeResponse(giant),
        ):
            with self.assertRaisesRegex(PostProcessError, "too large"):
                post_process_text(
                    "hello",
                    "en",
                    backend="ollama",
                    ollama_model="llama3.2:3b",
                )

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

    def test_openai_compatible_messages_include_context_vocabulary_and_text(self) -> None:
        messages = build_openai_compatible_messages(
            "hallo cinnamon",
            "de",
            "Use project wording.",
            "PipeWire",
            "Fix spelling only.",
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Fix spelling only.", messages[0]["content"])
        self.assertIn("Language: de", messages[0]["content"])
        self.assertIn("Use project wording.", messages[0]["content"])
        self.assertIn("PipeWire", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("hallo cinnamon", messages[1]["content"])

    def test_openai_url_rejects_null_byte(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible url contains invalid null byte"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="http://127.0.0.1:8000\x00",
            )

    def test_ollama_url_rejects_oversize(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor.MAX_POSTPROCESS_URL_CHARS",
            4,
        ):
            with self.assertRaisesRegex(PostProcessError, "ollama url is too large"):
                post_process_text(
                    "hello",
                    "en",
                    backend="ollama",
                    ollama_model="llama3.2:3b",
                    ollama_url="http://127.0.0.1",
                )

    def test_openai_compatible_backend_calls_chat_completions_endpoint(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with (
            mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", side_effect=fake_urlopen),
            mock.patch.dict("os.environ", {"SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY": "local-key"}),
        ):
            result = post_process_text(
                "hello cinnamon",
                "en",
                backend="openai-compatible",
                openai_compatible_model="llama.cpp-model",
                openai_compatible_url="http://127.0.0.1:8000/v1/",
            )
        self.assertEqual(result, "Hello Cinnamon.")
        request, timeout = requests[0]
        self.assertEqual(timeout, 180)
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/chat/completions")
        self.assertEqual(request.headers["Authorization"], "Bearer local-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "llama.cpp-model")
        self.assertFalse(body["stream"])
        self.assertEqual(body["temperature"], 0)
        self.assertIn("hello cinnamon", body["messages"][1]["content"])

    def test_openai_compatible_backend_requires_model(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "model is required"):
            post_process_text("hello", "en", backend="openai-compatible")

    def test_openai_compatible_empty_response_is_an_error(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", return_value=FakeResponse({"choices": []})):
            with self.assertRaisesRegex(PostProcessError, "without choices"):
                post_process_text("hello", "en", backend="openai-compatible", openai_compatible_model="local-model")

    def test_ollama_empty_response_is_an_error(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", return_value=FakeResponse({"response": ""})):
            with self.assertRaisesRegex(PostProcessError, "without output"):
                post_process_text("hello", "en", backend="ollama", ollama_model="llama3.2:3b")

    def test_list_ollama_models_reads_local_tags(self) -> None:
        payload = {
            "models": [
                {
                    "name": "llama3.2:3b",
                    "model": "llama3.2:3b",
                    "size": 2_016_000_000,
                    "modified_at": "2026-06-01T09:00:00Z",
                    "digest": "abc",
                    "details": {
                        "family": "llama",
                        "parameter_size": "3.2B",
                        "quantization_level": "Q4_K_M",
                    },
                }
            ]
        }
        requests = []

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse(payload)

        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", side_effect=fake_urlopen):
            result = list_ollama_models("http://127.0.0.1:11434/")
        self.assertTrue(result["available"])
        self.assertEqual(result["models"][0]["name"], "llama3.2:3b")
        self.assertEqual(result["models"][0]["description"], "llama 3.2B Q4_K_M")
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(timeout, 5)

    def test_list_ollama_models_reports_unavailable_server(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", side_effect=OSError("offline")):
            result = list_ollama_models("http://127.0.0.1:11434")
        self.assertFalse(result["available"])
        self.assertEqual(result["models"], [])
        self.assertIn("not reachable", result["message"])

    def test_list_openai_compatible_models_reads_models_endpoint(self) -> None:
        payload = {
            "object": "list",
            "data": [
                {"id": "local-llama", "object": "model", "owned_by": "llama.cpp"},
                {"id": "local-mistral", "object": "model", "owned_by": "vllm"},
            ],
        }
        requests = []

        def fake_urlopen(request: object, timeout: int = 0) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse(payload)

        with mock.patch("speed_of_cinnamon.postprocessor.urllib.request.urlopen", side_effect=fake_urlopen):
            result = list_openai_compatible_models("http://127.0.0.1:8000/v1/")
        self.assertTrue(result["available"])
        self.assertEqual([model["name"] for model in result["models"]], ["local-llama", "local-mistral"])
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/models")
        self.assertEqual(timeout, 5)


if __name__ == "__main__":
    unittest.main()
