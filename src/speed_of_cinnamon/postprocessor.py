from __future__ import annotations

import json
import os
import shlex
import urllib.parse
import urllib.error
import urllib.request

from .command_chain import CommandChainError, DEFAULT_COMMAND_TIMEOUT_SECONDS, MAX_COMMAND_OUTPUT_CHARS, run_command_chain, split_command_chain
from .personalization import build_personalization_prompt, normalize_context, normalize_vocabulary


class PostProcessError(RuntimeError):
    pass


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OPENAI_COMPATIBLE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-4o-transcribe"
DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_PROMPT = (
    "Clean up the transcript for direct insertion. Preserve meaning, language, names, "
    "technical terms, and formatting. Return only the final text."
)
MAX_POSTPROCESS_TEXT_CHARS = 1_000_000
MAX_POSTPROCESS_JSON_BYTES = 1_500_000
MAX_POSTPROCESS_URL_CHARS = 2_048
MAX_OPENAI_COMPATIBLE_API_KEY_CHARS = 4_096
MAX_OPENAI_COMPATIBLE_MODEL_CHARS = 240
OPENAI_COMPATIBLE_TEXT_MODEL_EXCLUDED_PREFIXES = (
    "ada-",
    "babbage-",
    "curie-",
    "dall-e-",
    "davinci-",
    "gpt-3.5-turbo-instruct",
    "gpt-image-",
    "omni-moderation-",
    "text-davinci-",
    "text-embedding-",
    "text-moderation-",
    "tts-",
    "whisper-",
)
OPENAI_COMPATIBLE_TEXT_MODEL_EXCLUDED_TERMS = (
    "audio",
    "computer-use",
    "embedding",
    "image",
    "moderation",
    "ranker",
    "realtime",
    "rerank",
    "speech",
    "transcribe",
    "tts",
)


def _quote(value: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise PostProcessError("value must be text")
    return shlex.quote(value)


def _assert_text_length(value: str, *, field_name: str, max_chars: int | None = None) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise PostProcessError(f"{field_name} must be text")
    if max_chars is None:
        max_chars = MAX_POSTPROCESS_TEXT_CHARS
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise PostProcessError(f"{field_name} max chars must be an integer")
    if len(value) > max_chars:
        raise PostProcessError(f"{field_name} is too large (max {max_chars} characters)")
    if len(value.encode("utf-8")) > max_chars:
        raise PostProcessError(f"{field_name} is too large (max {max_chars} bytes)")
    return value


def _assert_clean_url(url: str, *, field_name: str) -> str:
    if not isinstance(url, str) or isinstance(url, bool):
        raise PostProcessError(f"{field_name} must be text")
    normalized = (url or "").strip()
    if not normalized:
        raise PostProcessError(f"{field_name} is required")
    if _contains_escaped_null(normalized):
        raise PostProcessError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(normalized):
        raise PostProcessError(f"{field_name} contains invalid control character")
    return _assert_text_length(normalized, field_name=field_name, max_chars=MAX_POSTPROCESS_URL_CHARS)


def _validate_http_url(url: str, *, field_name: str) -> str:
    if not isinstance(url, str) or isinstance(url, bool):
        raise PostProcessError(f"{field_name} must be text")
    normalized = _assert_clean_url(url, field_name=field_name)
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise PostProcessError(f"{field_name} must use http:// or https://")
    if not parsed.netloc:
        raise PostProcessError(f"{field_name} is missing network location")
    return normalized


def _validate_http_request(request: urllib.request.Request, *, field_name: str) -> None:
    if not hasattr(request, "get_full_url"):
        raise PostProcessError(f"{field_name} is not a valid request object")
    url = request.get_full_url()
    if not isinstance(url, str):
        raise PostProcessError(f"{field_name} URL must be text")
    _validate_http_url(url, field_name=field_name)


def _contains_escaped_null(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise PostProcessError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise PostProcessError("value must be text")
    lowered = (value or "").lower()
    if (
        "\r" in lowered
        or "\n" in lowered
        or "\\r" in lowered
        or "\\n" in lowered
        or "\\u000d" in lowered
        or "\\u000a" in lowered
        or "\\x0a" in lowered
        or "\\x0d" in lowered
    ):
        return True
    for char in lowered:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return True
    return False


def _assert_openai_compatible_text(
    value: str,
    *,
    field_name: str,
    max_chars: int,
) -> str:
    if _contains_escaped_null(value):
        raise PostProcessError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(value):
        raise PostProcessError(f"{field_name} contains invalid control character")
    return _assert_text_length(value, field_name=field_name, max_chars=max_chars)


def _read_response_text(response: object, max_bytes: int) -> str:
    if not hasattr(response, "read"):
        raise PostProcessError("remote response must be readable")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise PostProcessError("max response bytes must be an integer")
    if max_bytes < 0:
        raise PostProcessError("max response bytes must be non-negative")
    raw = response.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise PostProcessError(f"remote response is too large (max {max_bytes} bytes)")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PostProcessError("remote response contains invalid UTF-8") from exc
    if _contains_escaped_null(text):
        raise PostProcessError("remote response contains invalid null byte")
    return text


def render_postprocess_template(
    template: str,
    text: str,
    language: str,
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    try:
        values = {
            "text": _quote(text),
            "language": _quote(language),
            "context": _quote(normalize_context(personal_context)),
            "vocabulary": _quote(normalize_vocabulary(vocabulary)),
            "prompt": _quote(build_personalization_prompt(personal_context, vocabulary)),
        }
    except ValueError as exc:
        raise PostProcessError(str(exc)) from exc
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def build_ollama_prompt(
    text: str,
    language: str,
    personal_context: str = "",
    vocabulary: str = "",
    instruction: str = "",
) -> str:
    if not isinstance(text, str) or isinstance(text, bool):
        raise PostProcessError("text must be text")
    if not isinstance(language, str) or isinstance(language, bool):
        raise PostProcessError("language must be text")
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise PostProcessError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise PostProcessError("vocabulary must be text")
    if not isinstance(instruction, str) or isinstance(instruction, bool):
        raise PostProcessError("instruction must be text")
    try:
        personalization = build_personalization_prompt(personal_context, vocabulary)
    except ValueError as exc:
        raise PostProcessError(str(exc)) from exc

    sections = [
        (instruction or DEFAULT_OLLAMA_PROMPT).strip(),
        f"Language: {language}",
    ]
    if personalization:
        sections.append(personalization)
    sections.append("Transcript:\n" + text.strip())
    return "\n\n".join(section for section in sections if section)


def _ollama_endpoint(url: str, path: str) -> str:
    base = _validate_http_url(_assert_clean_url(url, field_name="ollama url"), field_name="ollama url").rstrip("/")
    return base + "/" + path.lstrip("/")


def _openai_compatible_endpoint(url: str, path: str) -> str:
    base = _assert_clean_url(url, field_name="openai-compatible url")
    base = _validate_openai_compatible_http_url(base).rstrip("/")
    normalized_path = "/" + path.strip("/")
    if base.endswith(normalized_path):
        return base
    return base + normalized_path


def _validate_openai_compatible_http_url(url: str) -> str:
    return _validate_http_url(url, field_name="openai-compatible url")


def _read_json(request: urllib.request.Request, timeout: int) -> object:
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise PostProcessError("timeout must be an integer")
    _validate_http_request(request, field_name="postprocess request")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
        raw = _read_response_text(response, MAX_POSTPROCESS_JSON_BYTES)
    return json.loads(raw)


def _openai_compatible_error_detail(raw: str) -> str:
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if not isinstance(payload, dict):
        return str(payload)
    error = payload.get("error")
    if isinstance(error, dict):
        parts = [str(error.get("message") or "").strip(), str(error.get("type") or "").strip(), str(error.get("code") or "").strip()]
        return "; ".join(part for part in parts if part)
    if error:
        return str(error)
    return str(payload)


def _format_model_size(size: object) -> str:
    if isinstance(size, bool):
        return ""
    if isinstance(size, float):
        return ""
    try:
        value = int(size)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    return f"{amount:.1f} {unit}" if amount < 10 and unit != "B" else f"{amount:.0f} {unit}"


def _normalize_ollama_model(model: object) -> dict[str, object] | None:
    if not isinstance(model, dict):
        return None
    name = str(model.get("name") or model.get("model") or "").strip()
    if not name:
        return None
    details = model.get("details") if isinstance(model.get("details"), dict) else {}
    parameter_size = str(details.get("parameter_size") or "").strip()
    quantization = str(details.get("quantization_level") or "").strip()
    family = str(details.get("family") or "").strip()
    return {
        "name": name,
        "model": str(model.get("model") or name),
        "modified_at": str(model.get("modified_at") or ""),
        "size": model.get("size") or 0,
        "size_label": _format_model_size(model.get("size")),
        "digest": str(model.get("digest") or ""),
        "family": family,
        "parameter_size": parameter_size,
        "quantization": quantization,
        "description": " ".join(part for part in (family, parameter_size, quantization) if part),
    }


def list_ollama_models(url: str = DEFAULT_OLLAMA_URL, timeout: int = 5) -> dict[str, object]:
    endpoint = _ollama_endpoint(url, "/api/tags")
    request = urllib.request.Request(endpoint, method="GET")
    try:
        data = _read_json(request, timeout)
    except OSError as exc:
        return {
            "available": False,
            "models": [],
            "message": f"Ollama is not reachable at {(url or DEFAULT_OLLAMA_URL).rstrip('/')}: {exc}",
        }
    except PostProcessError as exc:
        return {
            "available": False,
            "models": [],
            "message": str(exc),
        }
    except json.JSONDecodeError:
        return {
            "available": False,
            "models": [],
            "message": "Ollama returned invalid JSON for model listing",
        }
    if not isinstance(data, dict):
        return {
            "available": False,
            "models": [],
            "message": "Ollama model listing must be a JSON object",
        }
    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        return {
            "available": True,
            "models": [],
            "message": "Ollama is running but returned no model list",
        }
    models = [model for item in raw_models if (model := _normalize_ollama_model(item))]
    models.sort(key=lambda item: str(item["name"]).lower())
    return {
        "available": True,
        "models": models,
        "message": "Ollama models loaded" if models else "No local Ollama models found",
    }


def _normalize_openai_compatible_model(model: object) -> dict[str, object] | None:
    if not isinstance(model, dict):
        return None
    name = str(model.get("id") or model.get("name") or "").strip()
    if not name:
        return None
    if not _openai_compatible_model_supports_text_polishing(name):
        return None
    owned_by = str(model.get("owned_by") or "").strip()
    return {
        "name": name,
        "model": name,
        "owned_by": owned_by,
        "description": owned_by,
    }


def _openai_compatible_model_supports_text_polishing(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    if not normalized:
        return False
    if normalized.startswith(OPENAI_COMPATIBLE_TEXT_MODEL_EXCLUDED_PREFIXES):
        return False
    return not any(term in normalized for term in OPENAI_COMPATIBLE_TEXT_MODEL_EXCLUDED_TERMS)


def list_openai_compatible_models(
    url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    timeout: int = 5,
    api_key: str = "",
) -> dict[str, object]:
    endpoint = _openai_compatible_endpoint(url, "/models")
    try:
        request = urllib.request.Request(endpoint, headers=_openai_compatible_headers(api_key), method="GET")
        data = _read_json(request, timeout)
    except urllib.error.HTTPError as exc:
        try:
            raw_error = _read_response_text(exc, MAX_POSTPROCESS_JSON_BYTES)
        except PostProcessError:
            raw_error = ""
        detail = _openai_compatible_error_detail(raw_error) or exc.reason or str(exc)
        return {
            "available": False,
            "models": [],
            "message": f"OpenAI-compatible API failed ({exc.code}) at {endpoint}: {detail}",
        }
    except OSError as exc:
        return {
            "available": False,
            "models": [],
            "message": f"OpenAI-compatible API is not reachable at {(url or DEFAULT_OPENAI_COMPATIBLE_URL).rstrip('/')}: {exc}",
        }
    except PostProcessError as exc:
        return {
            "available": False,
            "models": [],
            "message": str(exc),
        }
    except json.JSONDecodeError:
        return {
            "available": False,
            "models": [],
            "message": "OpenAI-compatible API returned invalid JSON for model listing",
        }
    if not isinstance(data, dict):
        return {
            "available": False,
            "models": [],
            "message": "OpenAI-compatible model listing must be a JSON object",
        }
    raw_models = data.get("data")
    if not isinstance(raw_models, list):
        return {
            "available": True,
            "models": [],
            "message": "OpenAI-compatible API returned no model list",
        }
    models = [model for item in raw_models if (model := _normalize_openai_compatible_model(item))]
    models.sort(key=lambda item: str(item["name"]).lower())
    return {
        "available": True,
        "models": models,
        "message": "OpenAI-compatible models loaded" if models else "No OpenAI-compatible text models found",
    }


def post_process_with_ollama(
    text: str,
    language: str,
    model: str,
    url: str = DEFAULT_OLLAMA_URL,
    personal_context: str = "",
    vocabulary: str = "",
    prompt: str = "",
) -> str:
    if not isinstance(model, str) or isinstance(model, bool):
        raise PostProcessError("ollama model must be text")
    if not isinstance(prompt, str) or isinstance(prompt, bool):
        raise PostProcessError("prompt must be text")
    model_name = (model or "").strip()
    if not model_name:
        raise PostProcessError("Ollama model is required")
    _assert_text_length(text, field_name="input text")
    endpoint = _ollama_endpoint(url, "/api/generate")
    payload = {
        "model": model_name,
        "prompt": build_ollama_prompt(text, language, personal_context, vocabulary, prompt),
        "stream": False,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        _validate_http_request(request, field_name="ollama post-process request")
        with urllib.request.urlopen(request, timeout=180) as response:  # nosec B310
            raw = _read_response_text(response, MAX_POSTPROCESS_JSON_BYTES)
    except OSError as exc:
        raise PostProcessError(f"Ollama request failed: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PostProcessError("Ollama returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise PostProcessError("Ollama response must be a JSON object")
    if data.get("error"):
        raise PostProcessError(f"Ollama failed: {data['error']}")
    processed = str(data.get("response") or "").strip()
    processed = _assert_text_length(processed, field_name="post-process output")
    if not processed:
        raise PostProcessError("Ollama completed without output")
    return processed


def _openai_compatible_headers(api_key: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = api_key or os.environ.get("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY", "")
    if _contains_escaped_null(api_key):
        raise PostProcessError("openai-compatible API key contains invalid null byte")
    if _contains_http_header_control_chars(api_key):
        raise PostProcessError("openai-compatible API key contains invalid control character")
    api_key = api_key.strip()
    api_key = _assert_openai_compatible_text(api_key, field_name="openai-compatible API key", max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def build_openai_compatible_messages(
    text: str,
    language: str,
    personal_context: str = "",
    vocabulary: str = "",
    instruction: str = "",
) -> list[dict[str, str]]:
    try:
        personalization = build_personalization_prompt(personal_context, vocabulary)
    except ValueError as exc:
        raise PostProcessError(str(exc)) from exc

    system_sections = [
        (instruction or DEFAULT_OLLAMA_PROMPT).strip(),
        f"Language: {language}",
    ]
    if personalization:
        system_sections.append(personalization)
    return [
        {"role": "system", "content": "\n\n".join(section for section in system_sections if section)},
        {"role": "user", "content": "Transcript:\n" + text.strip()},
    ]


def _choice_text(choice: object) -> str:
    if not isinstance(choice, dict):
        return ""
    message = choice.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or ""))
                else:
                    parts.append(str(item))
            return "".join(parts)
    return str(choice.get("text") or "")


def post_process_with_openai_compatible(
    text: str,
    language: str,
    model: str,
    url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    personal_context: str = "",
    vocabulary: str = "",
    prompt: str = "",
    api_key: str = "",
) -> str:
    if not isinstance(model, str) or isinstance(model, bool):
        raise PostProcessError("openai-compatible model must be text")
    if not isinstance(prompt, str) or isinstance(prompt, bool):
        raise PostProcessError("prompt must be text")
    if not isinstance(api_key, str) or isinstance(api_key, bool):
        raise PostProcessError("api key must be text")
    model_name = _assert_openai_compatible_text(
        str(model or ""),
        field_name="openai-compatible model",
        max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    ).strip()
    if not model_name:
        raise PostProcessError("OpenAI-compatible model is required")
    _assert_text_length(text, field_name="input text")
    endpoint = _openai_compatible_endpoint(url, "/chat/completions")
    payload = {
        "model": model_name,
        "messages": build_openai_compatible_messages(text, language, personal_context, vocabulary, prompt),
        "stream": False,
        "temperature": 0,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers=_openai_compatible_headers(api_key),
        method="POST",
    )
    try:
        _validate_http_request(request, field_name="openai-compatible post-process request")
        with urllib.request.urlopen(request, timeout=180) as response:  # nosec B310
            raw = _read_response_text(response, MAX_POSTPROCESS_JSON_BYTES)
    except urllib.error.HTTPError as exc:
        try:
            raw_error = _read_response_text(exc, MAX_POSTPROCESS_JSON_BYTES)
        except PostProcessError:
            raw_error = ""
        detail = _openai_compatible_error_detail(raw_error) or exc.reason or str(exc)
        raise PostProcessError(f"OpenAI-compatible request failed ({exc.code}) at {endpoint}: {detail}") from exc
    except OSError as exc:
        raise PostProcessError(f"OpenAI-compatible request failed: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PostProcessError("OpenAI-compatible server returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise PostProcessError("OpenAI-compatible response must be a JSON object")
    if data.get("error"):
        error = data["error"]
        detail = str(error.get("message") or error) if isinstance(error, dict) else str(error)
        raise PostProcessError(f"OpenAI-compatible server failed: {detail}")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise PostProcessError("OpenAI-compatible server completed without choices")
    processed = _choice_text(choices[0]).strip()
    processed = _assert_text_length(processed, field_name="post-process output")
    if not processed:
        raise PostProcessError("OpenAI-compatible server completed without output")
    return processed


def post_process_text(
    text: str,
    language: str,
    command_template: str = "",
    personal_context: str = "",
    vocabulary: str = "",
    backend: str = "command",
    ollama_model: str = "",
    ollama_url: str = DEFAULT_OLLAMA_URL,
    ollama_prompt: str = "",
    openai_compatible_model: str = "",
    openai_compatible_url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    openai_compatible_api_key: str = "",
) -> str:
    if not isinstance(text, str) or isinstance(text, bool):
        raise PostProcessError("text must be text")
    if not isinstance(language, str) or isinstance(language, bool):
        raise PostProcessError("language must be text")
    if not isinstance(command_template, str) or isinstance(command_template, bool):
        raise PostProcessError("command template must be text")
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise PostProcessError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise PostProcessError("vocabulary must be text")
    if not isinstance(backend, str) or isinstance(backend, bool):
        raise PostProcessError("backend must be text")
    if not isinstance(ollama_url, str) or isinstance(ollama_url, bool):
        raise PostProcessError("ollama url must be text")
    if not isinstance(openai_compatible_url, str) or isinstance(openai_compatible_url, bool):
        raise PostProcessError("openai-compatible url must be text")
    if not isinstance(openai_compatible_api_key, str) or isinstance(openai_compatible_api_key, bool):
        raise PostProcessError("openai-compatible API key must be text")
    normalized_backend = (backend or "command").strip().lower().replace("_", "-")
    if normalized_backend in {"none", "off", "disabled"}:
        return text
    if normalized_backend == "ollama":
        _assert_text_length(text, field_name="input text")
        return post_process_with_ollama(
            text,
            language,
            ollama_model,
            ollama_url,
            personal_context,
            vocabulary,
            ollama_prompt,
        )
    if normalized_backend in {"openai-compatible", "openai", "local-openai"}:
        _assert_text_length(text, field_name="input text")
        return post_process_with_openai_compatible(
            text,
            language,
            openai_compatible_model,
            openai_compatible_url,
            personal_context,
            vocabulary,
            ollama_prompt,
            openai_compatible_api_key,
        )
    if normalized_backend not in {"command", "custom"}:
        raise PostProcessError(f"unknown post-process backend: {backend}")
    text = _assert_text_length(text, field_name="input text")

    template = command_template.strip()
    if not template:
        return text

    command = render_postprocess_template(template, text, language, personal_context, vocabulary)
    try:
        segments = split_command_chain(command, label="post-process")
        processed = run_command_chain(
            segments,
            text,
            label="post-process",
            timeout_seconds=DEFAULT_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=MAX_COMMAND_OUTPUT_CHARS,
            personal_context=personal_context,
            vocabulary=vocabulary,
        )
    except CommandChainError as exc:
        message = str(exc)
        if message.startswith("invalid post-process") or message.startswith("unsupported shell operator in post-process"):
            raise PostProcessError(message) from exc
        if (
            message.startswith("post-process command ended")
            or message.startswith("empty post-process")
            or message.startswith("post-process command chain is empty")
        ):
            raise PostProcessError(message) from exc
        if (
            "personal context is too large" in message
            or "vocabulary is too large" in message
            or "command contains invalid null byte" in message
            or "command output contains invalid null byte" in message
            or "command failed" in message
            or "command not found" in message
            or "command timed out" in message
            or "command execution failed" in message
            or "command input exceeded" in message
            or "max_input_chars must be positive" in message
            or "max_input_chars must be non-negative" in message
            or "max_input_chars must not exceed" in message
            or "max_output_chars must be positive" in message
            or "max_output_chars must be non-negative" in message
            or "max_output_chars must not exceed" in message
            or "timeout_seconds must be positive" in message
            or "command input contains invalid null byte" in message
        ):
            raise PostProcessError(message) from exc
        if "command output exceeded" in message:
            raise PostProcessError(f"post-process output is too large: {message}") from exc
        if "exceeded" in message:
            raise PostProcessError(message) from exc
        raise PostProcessError(f"post-process command failed: {message}") from exc
    processed = _assert_text_length(processed, field_name="post-process output")
    if not processed:
        raise PostProcessError("post-process command completed without output")
    return processed
