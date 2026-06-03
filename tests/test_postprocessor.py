from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest import mock

from speed_of_cinnamon.postprocessor import (
    PostProcessError,
    build_ollama_prompt,
    build_openai_compatible_messages,
    _ollama_endpoint,
    _openai_compatible_endpoint,
    _contains_escaped_null,
    _coerce_environment_text,
    _quote,
    _assert_text_length,
    _openai_compatible_headers,
    _validate_same_origin_redirect,
    _format_model_size,
    post_process_text,
    MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    MAX_POSTPROCESS_JSON_BYTES,
    MAX_POSTPROCESS_URL_CHARS,
    list_ollama_models,
    list_openai_compatible_models,
    render_postprocess_template,
)
from speed_of_cinnamon.command_chain import CommandChainError
from speed_of_cinnamon.personalization import MAX_PERSONAL_CONTEXT_CHARS, MAX_VOCABULARY_CHARS


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


class FakeBytesResponse:
    def __init__(self, payload: bytes) -> None:
        self.data = payload
        self.buffer = io.BytesIO(self.data)

    def __enter__(self) -> "FakeBytesResponse":
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

    def test_template_rejects_oversized_personal_context(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "personal context is too large"):
            render_postprocess_template(
                "tool --prompt {prompt}",
                "hello",
                "en",
                "x" * (MAX_PERSONAL_CONTEXT_CHARS + 1),
                "PipeWire",
            )

    def test_template_rejects_oversized_vocabulary(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "vocabulary is too large"):
            render_postprocess_template(
                "tool --prompt {prompt}",
                "hello",
                "en",
                "Use terms",
                "x" * (MAX_VOCABULARY_CHARS + 1),
            )

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
        with self.assertRaisesRegex(PostProcessError, "path separators"):
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

    def test_post_process_reports_updated_chain_limit_error(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor.run_command_chain",
            side_effect=CommandChainError("max_output_chars must not exceed 1"),
        ):
            with self.assertRaisesRegex(PostProcessError, "max_output_chars must not exceed"):
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

    def test_post_process_command_rejects_oversized_text_bytes(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.MAX_POSTPROCESS_TEXT_CHARS", 4):
            with self.assertRaisesRegex(PostProcessError, "input text is too large"):
                post_process_text("😀😀", "en", "cmd")

    def test_post_process_command_rejects_oversized_remote_response(self) -> None:
        giant = "{" + '"x":' + '"' * (MAX_POSTPROCESS_JSON_BYTES + 1) + "}"
        with mock.patch(
            "speed_of_cinnamon.postprocessor._open_http_request",
            return_value=FakeResponse(giant),
        ):
            with self.assertRaisesRegex(PostProcessError, "too large"):
                post_process_text(
                    "hello",
                    "en",
                    backend="ollama",
                    ollama_model="llama3.2:3b",
                )

    def test_post_process_with_ollama_rejects_invalid_utf8_response(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor._open_http_request",
            return_value=FakeBytesResponse(b"\xff"),
        ):
            with self.assertRaisesRegex(PostProcessError, "invalid UTF-8"):
                post_process_text("hello", "en", backend="ollama", ollama_model="llama3.2:3b")

    def test_post_process_with_ollama_rejects_escaped_null_response(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor._open_http_request",
            return_value=FakeResponse('{"response":"hello\\\\u0000"}'),
        ):
            with self.assertRaisesRegex(PostProcessError, "invalid null byte"):
                post_process_text("hello", "en", backend="ollama", ollama_model="llama3.2:3b")

    def test_ollama_url_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "ollama url contains invalid null byte"):
            post_process_text(
                "hello",
                "en",
                backend="ollama",
                ollama_model="llama3.2:3b",
                ollama_url="http://127.0.0.1:11434\\\\x00",
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

    def test_ollama_backend_rejects_oversized_personal_context(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "personal context is too large"):
            post_process_text(
                "hello",
                "en",
                backend="ollama",
                ollama_model="llama3.2:3b",
                personal_context="x" * (MAX_PERSONAL_CONTEXT_CHARS + 1),
            )

    def test_openai_compatible_backend_rejects_oversized_vocabulary(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "vocabulary is too large"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                vocabulary="x" * (MAX_VOCABULARY_CHARS + 1),
            )

    def test_openai_compatible_backend_rejects_oversized_model(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible model is too large"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="x" * (MAX_OPENAI_COMPATIBLE_MODEL_CHARS + 1),
            )

    def test_openai_compatible_backend_rejects_oversized_api_key(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible API key is too large"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_api_key="x" * (MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1),
            )

    def test_openai_compatible_backend_rejects_api_key_with_newline(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="http://127.0.0.1:8000/v1/",
                openai_compatible_api_key="secret\r\nX: injected",
            )

    def test_openai_compatible_backend_rejects_escaped_newline_in_api_key(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="http://127.0.0.1:8000/v1/",
                openai_compatible_api_key="secret\\r\\n",
            )

    def test_openai_compatible_backend_rejects_model_with_newline(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local\\nX",
                openai_compatible_url="http://127.0.0.1:8000/v1/",
            )

    def test_openai_compatible_backend_rejects_model_with_escaped_hex_newline(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local\\\\x0a",
                openai_compatible_url="http://127.0.0.1:8000/v1/",
            )

    def test_ollama_backend_calls_generate_endpoint(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"response": "Hello Cinnamon."})

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
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

    def test_ollama_backend_redacts_sensitive_remote_error(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor._open_http_request",
            return_value=FakeResponse({"error": "Bearer sk-secret private transcript"}),
        ):
            with self.assertRaises(PostProcessError) as caught:
                post_process_text(
                    "private transcript",
                    "en",
                    backend="ollama",
                    ollama_model="llama3.2:3b",
                )
        message = str(caught.exception)
        self.assertIn("[redacted remote error]", message)
        self.assertNotIn("sk-secret", message)
        self.assertNotIn("private transcript", message)

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

    def test_openai_url_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible url contains invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="http://127.0.0.1:8000/v1\\r\\n",
            )

    def test_openai_url_rejects_escaped_hex_newline(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible url contains invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="http://127.0.0.1:8000/v1\\\\x0a",
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

    def test_openai_compatible_url_rejects_escaped_null(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible url contains invalid null byte"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="http://127.0.0.1:8000/v1\\\\u0000",
            )

    def test_ollama_url_rejects_control_character(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "ollama url contains invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="ollama",
                ollama_model="llama3.2:3b",
                ollama_url="http://127.0.0.1:11434/v1\\n",
            )

    def test_ollama_url_rejects_escaped_hex_newline(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "ollama url contains invalid control character"):
            post_process_text(
                "hello",
                "en",
                backend="ollama",
                ollama_model="llama3.2:3b",
                ollama_url="http://127.0.0.1:11434/v1\\\\x0a",
            )

    def test_openai_compatible_backend_rejects_non_http_url(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "must use http:// or https://"):
            post_process_text(
                "hello",
                "en",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url="ftp://127.0.0.1:8000/v1",
            )

    def test_openai_compatible_backend_redacts_sensitive_remote_error(self) -> None:
        with mock.patch(
            "speed_of_cinnamon.postprocessor._open_http_request",
            return_value=FakeResponse({"error": {"message": "token=abc123 private transcript"}}),
        ):
            with self.assertRaises(PostProcessError) as caught:
                post_process_text(
                    "private transcript",
                    "en",
                    backend="openai-compatible",
                    openai_compatible_model="local",
                    openai_compatible_url="http://127.0.0.1:8000/v1",
                )
        message = str(caught.exception)
        self.assertIn("[redacted remote error]", message)
        self.assertNotIn("abc123", message)
        self.assertNotIn("private transcript", message)

    def test_endpoint_builders_normalize_without_duplicate_behavior_change(self) -> None:
        self.assertEqual(_ollama_endpoint("http://127.0.0.1:11434/", "/api/generate"), "http://127.0.0.1:11434/api/generate")
        self.assertEqual(
            _openai_compatible_endpoint("http://127.0.0.1:8000/v1/", "/models"),
            "http://127.0.0.1:8000/v1/models",
        )

    def test_remote_redirects_must_keep_same_origin(self) -> None:
        _validate_same_origin_redirect(
            "https://api.openai.com/v1/models",
            "https://api.openai.com:443/v1/models?cursor=next",
            field_name="postprocess request",
        )
        with self.assertRaisesRegex(PostProcessError, "redirect target changes origin"):
            _validate_same_origin_redirect(
                "http://127.0.0.1:11434/api/generate",
                "http://example.invalid/api/generate",
                field_name="postprocess request",
            )

    def test_openai_compatible_backend_calls_chat_completions_endpoint(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with (
            mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen),
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
        self.assertNotIn("service_tier", body)
        self.assertIn("hello cinnamon", body["messages"][1]["content"])

    def test_openai_compatible_backend_enables_flex_for_openai_api_by_default(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
            result = post_process_text(
                "hello cinnamon",
                "en",
                backend="openai-compatible",
                openai_compatible_model="gpt-4o-mini",
                openai_compatible_url="https://api.openai.com/v1",
                openai_compatible_api_key="secret",
            )

        self.assertEqual(result, "Hello Cinnamon.")
        request, _timeout = requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["service_tier"], "flex")

    def test_openai_compatible_backend_can_disable_flex_for_openai_api(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
            result = post_process_text(
                "hello cinnamon",
                "en",
                backend="openai-compatible",
                openai_compatible_model="gpt-4o-mini",
                openai_compatible_url="https://api.openai.com/v1",
                openai_compatible_api_key="secret",
                openai_compatible_flex_processing=False,
            )

        self.assertEqual(result, "Hello Cinnamon.")
        request, _timeout = requests[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertNotIn("service_tier", body)

    def test_openai_compatible_backend_falls_back_when_flex_is_rejected(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            if len(requests) == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    {},
                    io.BytesIO(b'{"error":{"message":"Invalid service_tier argument","type":"invalid_request_error"}}'),
                )
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
            result = post_process_text(
                "hello cinnamon",
                "en",
                backend="openai-compatible",
                openai_compatible_model="gpt-4o-mini",
                openai_compatible_url="https://api.openai.com/v1",
                openai_compatible_api_key="secret",
            )

        self.assertEqual(result, "Hello Cinnamon.")
        first_body = json.loads(requests[0][0].data.decode("utf-8"))
        second_body = json.loads(requests[1][0].data.decode("utf-8"))
        self.assertEqual(first_body["service_tier"], "flex")
        self.assertNotIn("service_tier", second_body)

    def test_openai_compatible_backend_uses_explicit_api_key(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
            result = post_process_text(
                "hello cinnamon",
                "en",
                backend="openai-compatible",
                openai_compatible_model="gpt-4o-mini",
                openai_compatible_url="https://api.openai.com/v1",
                openai_compatible_api_key="secret",
            )
        self.assertEqual(result, "Hello Cinnamon.")
        request, _timeout = requests[0]
        self.assertEqual(request.headers["Authorization"], "Bearer secret")

    def test_openai_compatible_headers_ignores_invalid_environment_key(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor.os.environ.__getitem__", return_value=123):
            headers = _openai_compatible_headers()
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertNotIn("Authorization", headers)

    def test_openai_compatible_backend_ignores_invalid_environment_key(self) -> None:
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "Hello Cinnamon."}}]})

        with mock.patch("speed_of_cinnamon.postprocessor.os.environ.__getitem__", return_value=123):
            with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
                result = post_process_text(
                    "hello cinnamon",
                    "en",
                    backend="openai-compatible",
                    openai_compatible_model="llama.cpp-model",
                    openai_compatible_url="http://127.0.0.1:8000/v1/",
                )
        self.assertEqual(result, "Hello Cinnamon.")
        request, _timeout = requests[0]
        self.assertNotIn("Authorization", request.headers)

    def test_coerce_environment_text(self) -> None:
        with mock.patch.dict("speed_of_cinnamon.postprocessor.os.environ", {"OPENAI_COMPATIBLE_TEST_ENV": "secret"}):
            self.assertEqual(_coerce_environment_text("OPENAI_COMPATIBLE_TEST_ENV"), "secret")
        with mock.patch("speed_of_cinnamon.postprocessor.os.environ.__getitem__", return_value=123):
            self.assertEqual(_coerce_environment_text("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY"), "")

    def test_openai_compatible_backend_reports_http_error_detail(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":{"message":"missing API key","type":"invalid_request_error"}}'),
        )
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=error):
            with self.assertRaisesRegex(PostProcessError, r"failed \(401\).*missing API key"):
                post_process_text(
                    "hello cinnamon",
                    "en",
                    backend="openai-compatible",
                    openai_compatible_model="gpt-4o-mini",
                    openai_compatible_url="https://api.openai.com/v1",
                )
        self.assertTrue(error.fp.closed)

    def test_openai_compatible_backend_requires_model(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "model is required"):
            post_process_text("hello", "en", backend="openai-compatible")

    def test_openai_compatible_empty_response_is_an_error(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", return_value=FakeResponse({"choices": []})):
            with self.assertRaisesRegex(PostProcessError, "without choices"):
                post_process_text("hello", "en", backend="openai-compatible", openai_compatible_model="local-model")

    def test_quote_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "value must be text"):
            _quote(123)  # type: ignore[arg-type]

    def test_assert_text_length_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "must be text"):
            _assert_text_length(123, field_name="input text")  # type: ignore[arg-type]

    def test_post_process_text_rejects_non_text_language(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "language must be text"):
            post_process_text("hello", 123, "command")  # type: ignore[arg-type]

    def test_post_process_text_rejects_non_text_backend(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "backend must be text"):
            post_process_text("hello", "en", backend=123)  # type: ignore[arg-type]

    def test_post_process_text_rejects_non_text_urls(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "openai-compatible url must be text"):
            post_process_text(
                "hello",
                "en",
                "command",
                backend="openai-compatible",
                openai_compatible_model="local",
                openai_compatible_url=123,  # type: ignore[arg-type]
            )

    def test_contains_escaped_null_rejects_non_text(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "value must be text"):
            _contains_escaped_null(123)  # type: ignore[arg-type]

    def test_ollama_empty_response_is_an_error(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", return_value=FakeResponse({"response": ""})):
            with self.assertRaisesRegex(PostProcessError, "without output"):
                post_process_text("hello", "en", backend="ollama", ollama_model="llama3.2:3b")

    def test_format_model_size_rejects_boolean(self) -> None:
        self.assertEqual(_format_model_size(True), "")
        self.assertEqual(_format_model_size(False), "")

    def test_format_model_size_rejects_float(self) -> None:
        self.assertEqual(_format_model_size(3.5), "")

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

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse(payload)

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
            result = list_ollama_models("http://127.0.0.1:11434/")
        self.assertTrue(result["available"])
        self.assertEqual(result["models"][0]["name"], "llama3.2:3b")
        self.assertEqual(result["models"][0]["description"], "llama 3.2B Q4_K_M")
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/tags")
        self.assertEqual(timeout, 5)

    def test_list_ollama_models_reports_unavailable_server(self) -> None:
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=OSError("offline")):
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
                {"id": "gpt-4o-transcribe", "object": "model", "owned_by": "openai"},
                {"id": "whisper-1", "object": "model", "owned_by": "openai"},
                {"id": "text-embedding-3-large", "object": "model", "owned_by": "openai"},
                {"id": "gpt-image-1", "object": "model", "owned_by": "openai"},
                {"id": "tts-1", "object": "model", "owned_by": "openai"},
            ],
        }
        requests = []

        def fake_urlopen(request: object, timeout: int = 0, **_: object) -> FakeResponse:
            requests.append((request, timeout))
            return FakeResponse(payload)

        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=fake_urlopen):
            result = list_openai_compatible_models("http://127.0.0.1:8000/v1/", api_key="secret")
        self.assertTrue(result["available"])
        self.assertEqual([model["name"] for model in result["models"]], ["local-llama", "local-mistral"])
        request, timeout = requests[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8000/v1/models")
        self.assertEqual(request.headers["Authorization"], "Bearer secret")
        self.assertEqual(timeout, 5)

    def test_list_openai_compatible_models_keeps_text_models_only(self) -> None:
        payload = {
            "object": "list",
            "data": [
                {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
                {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
                {"id": "gpt-5", "object": "model", "owned_by": "openai"},
                {"id": "o4-mini", "object": "model", "owned_by": "openai"},
                {"id": "gpt-3.5-turbo-instruct", "object": "model", "owned_by": "openai"},
                {"id": "gpt-4o-transcribe", "object": "model", "owned_by": "openai"},
                {"id": "whisper-1", "object": "model", "owned_by": "openai"},
                {"id": "gpt-4o-audio-preview", "object": "model", "owned_by": "openai"},
                {"id": "gpt-4o-mini-tts", "object": "model", "owned_by": "openai"},
                {"id": "text-embedding-3-small", "object": "model", "owned_by": "openai"},
                {"id": "omni-moderation-latest", "object": "model", "owned_by": "openai"},
                {"id": "dall-e-3", "object": "model", "owned_by": "openai"},
                {"id": "gpt-image-1", "object": "model", "owned_by": "openai"},
                {"id": "local-mistral-instruct", "object": "model", "owned_by": "local"},
            ],
        }
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", return_value=FakeResponse(payload)):
            result = list_openai_compatible_models("https://api.openai.com/v1")
        self.assertEqual(
            [model["name"] for model in result["models"]],
            ["gpt-4o", "gpt-4o-mini", "gpt-5", "local-mistral-instruct", "o4-mini"],
        )

    def test_list_openai_compatible_models_reports_when_only_non_text_models_exist(self) -> None:
        payload = {
            "object": "list",
            "data": [
                {"id": "gpt-4o-transcribe", "object": "model", "owned_by": "openai"},
                {"id": "whisper-1", "object": "model", "owned_by": "openai"},
                {"id": "text-embedding-3-large", "object": "model", "owned_by": "openai"},
            ],
        }
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", return_value=FakeResponse(payload)):
            result = list_openai_compatible_models("https://api.openai.com/v1")
        self.assertTrue(result["available"])
        self.assertEqual(result["models"], [])
        self.assertEqual(result["message"], "No OpenAI-compatible text models found")

    def test_list_openai_compatible_models_rejects_non_http_url(self) -> None:
        with self.assertRaisesRegex(PostProcessError, "must use http:// or https://"):
            list_openai_compatible_models("mailto:admin@localhost")

    def test_list_openai_compatible_models_rejects_oversized_api_key(self) -> None:
        result = list_openai_compatible_models(
            "http://127.0.0.1:8000/v1",
            api_key="x" * (MAX_OPENAI_COMPATIBLE_API_KEY_CHARS + 1),
        )
        self.assertFalse(result["available"])
        self.assertIn("openai-compatible API key is too large", result["message"])

    def test_list_openai_compatible_models_rejects_api_key_with_newline(self) -> None:
        result = list_openai_compatible_models("http://127.0.0.1:8000/v1", api_key="secret\n")
        self.assertFalse(result["available"])
        self.assertIn("invalid control character", result["message"])

    def test_list_openai_compatible_models_reports_http_error_detail(self) -> None:
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/models",
            401,
            "Unauthorized",
            {},
            io.BytesIO(b'{"error":{"message":"missing API key","type":"invalid_request_error"}}'),
        )
        with mock.patch("speed_of_cinnamon.postprocessor._open_http_request", side_effect=error):
            result = list_openai_compatible_models("https://api.openai.com/v1")
        self.assertFalse(result["available"])
        self.assertIn("failed (401)", result["message"])
        self.assertIn("missing API key", result["message"])
        self.assertNotIn("local server", result["message"])
        self.assertTrue(error.fp.closed)


if __name__ == "__main__":
    unittest.main()
