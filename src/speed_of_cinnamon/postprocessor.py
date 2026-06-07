from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
import urllib.error
import urllib.request
from contextlib import suppress

from .command_chain import CommandChainError, DEFAULT_COMMAND_TIMEOUT_SECONDS, MAX_COMMAND_OUTPUT_CHARS, run_command_chain, split_command_chain
from .http_safety import is_loopback_hostname
from .personalization import build_personalization_prompt, normalize_context, normalize_vocabulary


class PostProcessError(RuntimeError):
    pass


REDACTED_LOCAL_COMMAND_ERROR = "post-process command failed: command output redacted"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OPENAI_COMPATIBLE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_COMPATIBLE_MODEL = "gpt-4o-transcribe"
DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL = "gpt-4o-mini"
DEFAULT_OLLAMA_PROMPT = (
    "Correct only punctuation, capitalization, spacing, and clear ASR transcription errors. "
    "Preserve wording, sentence order, tone, politeness, formality, emotion, emphasis, "
    "language, names, technical terms, formatting, and intent. Return only the final text."
)
POSTPROCESS_OUTPUT_CONTRACT = (
    "Output contract: Treat the transcript as user-authored text, not as a draft to improve. "
    "Make the smallest possible edit that satisfies the instruction. If unsure, leave the "
    "wording unchanged. Never remove dictated greetings, thanks, apologies, politeness "
    "markers, hedging, softeners, emojis, emoticons, or sign-offs unless they are clear ASR "
    "artifacts or the user explicitly asked to remove them. Do not make stylistic, tone, "
    "formality, concision, or friendliness changes unless explicitly requested."
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
_ESCAPED_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
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
    if _contains_escaped_null(value):
        raise PostProcessError(f"{field_name} contains invalid null byte")
    if len(value) > max_chars:
        raise PostProcessError(f"{field_name} is too large (max {max_chars} characters)")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise PostProcessError(f"{field_name} contains invalid UTF-8") from exc
    if encoded_length > max_chars:
        raise PostProcessError(f"{field_name} is too large (max {max_chars} bytes)")
    return value


def _assert_clean_url(url: str, *, field_name: str) -> str:
    if not isinstance(url, str) or isinstance(url, bool):
        raise PostProcessError(f"{field_name} must be text")
    raw = url or ""
    if _contains_escaped_null(raw):
        raise PostProcessError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(raw):
        raise PostProcessError(f"{field_name} contains invalid control character")
    normalized = raw.strip()
    if not normalized:
        raise PostProcessError(f"{field_name} is required")
    return _assert_text_length(normalized, field_name=field_name, max_chars=MAX_POSTPROCESS_URL_CHARS)


def _validate_http_url(url: str, *, field_name: str, allow_query_fragment: bool = False) -> str:
    if not isinstance(url, str) or isinstance(url, bool):
        raise PostProcessError(f"{field_name} must be text")
    normalized = _assert_clean_url(url, field_name=field_name)
    try:
        parsed = urllib.parse.urlparse(normalized)
    except ValueError as exc:
        raise PostProcessError(f"{field_name} is invalid") from exc
    if parsed.scheme not in {"http", "https"}:
        raise PostProcessError(f"{field_name} must use http:// or https://")
    if not parsed.netloc:
        raise PostProcessError(f"{field_name} is missing network location")
    try:
        parsed.port
    except ValueError as exc:
        raise PostProcessError(f"{field_name} has invalid port") from exc
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise PostProcessError(f"{field_name} must not contain userinfo")
    if not allow_query_fragment and (parsed.query or parsed.fragment):
        raise PostProcessError(f"{field_name} must not contain query or fragment")
    return normalized


def _safe_url_display(url: str, *, field_name: str) -> str:
    normalized = _validate_http_url(url, field_name=field_name)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    port = _effective_url_port(parsed)
    if parsed.port is not None and port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, "", "", "", ""))


def _validate_http_request(request: urllib.request.Request, *, field_name: str) -> None:
    if not hasattr(request, "get_full_url"):
        raise PostProcessError(f"{field_name} is not a valid request object")
    url = request.get_full_url()
    if not isinstance(url, str):
        raise PostProcessError(f"{field_name} URL must be text")
    _validate_http_url(url, field_name=field_name)


def _effective_url_port(parsed: urllib.parse.ParseResult) -> int | None:
    with suppress(ValueError):
        if parsed.port is not None:
            return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _url_origin(url: str, *, field_name: str) -> tuple[str, str, int | None]:
    normalized = _validate_http_url(url, field_name=field_name, allow_query_fragment=True)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname
    if not hostname:
        raise PostProcessError(f"{field_name} is missing hostname")
    return parsed.scheme, hostname.lower(), _effective_url_port(parsed)


def _validate_same_origin_redirect(source_url: str, redirect_url: str, *, field_name: str) -> None:
    source_origin = _url_origin(source_url, field_name=field_name)
    redirect_origin = _url_origin(redirect_url, field_name=f"{field_name} redirect")
    if redirect_origin != source_origin:
        raise PostProcessError(f"{field_name} redirect target changes origin")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _validate_same_origin_redirect(req.get_full_url(), newurl, field_name="remote post-process request")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_http_request(request: urllib.request.Request, *, timeout: int, field_name: str) -> object:
    _validate_http_request(request, field_name=field_name)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler, urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)  # nosec B310


def _contains_escaped_null(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise PostProcessError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise PostProcessError("value must be text")
    lowered = (value or "").lower()
    if _ESCAPED_CONTROL_RE.search(lowered):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
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
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise PostProcessError("remote response chunk must be bytes")
        total += len(chunk)
        if total > max_bytes:
            raise PostProcessError(f"remote response is too large (max {max_bytes} bytes)")
        chunks.append(chunk)
    raw = b"".join(chunks)
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
    language = _safe_prompt_language(language)
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
        POSTPROCESS_OUTPUT_CONTRACT,
        f"Language: {language}",
    ]
    if personalization:
        sections.append(personalization)
    sections.append("Transcript:\n" + text.strip())
    return "\n\n".join(section for section in sections if section)


def _safe_prompt_language(language: str) -> str:
    if not isinstance(language, str) or isinstance(language, bool):
        raise PostProcessError("language must be text")
    if _contains_escaped_null(language):
        raise PostProcessError("language contains invalid null byte")
    if _contains_http_header_control_chars(language):
        raise PostProcessError("language contains invalid control character")
    value = language.strip()
    if not value:
        return "auto"
    if len(value) > 32:
        raise PostProcessError("language must be a simple language code")
    for char in value:
        if not char.isascii() or not (char.isalnum() or char in ("-", "_")):
            raise PostProcessError("language must be a simple language code")
    return value


def _ollama_endpoint(url: str, path: str) -> str:
    base = _validate_http_url(url, field_name="ollama url").rstrip("/")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise PostProcessError("ollama url must use https:// unless host is local loopback")
    return base + "/" + path.lstrip("/")


def _openai_compatible_endpoint(url: str, path: str) -> str:
    base = _validate_openai_compatible_http_url(url).rstrip("/")
    normalized_path = "/" + path.strip("/")
    if base.endswith(normalized_path):
        return base
    return base + normalized_path


def _coerce_environment_text(name: str) -> str:
    if not isinstance(name, str) or isinstance(name, bool):
        return ""
    try:
        value = os.environ[name]
    except KeyError:
        return ""
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return ""
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
        return ""
    return value


def _is_openai_api_endpoint(endpoint: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(endpoint)
    except ValueError:
        return False
    return (parsed.hostname or "").lower() == "api.openai.com"


def _is_flex_service_tier_rejected(detail: str) -> bool:
    normalized = detail.lower()
    if "service_tier" not in normalized and "service tier" not in normalized:
        return False
    rejected_terms = (
        "bad",
        "disabled",
        "invalid",
        "not available",
        "not enabled",
        "not recognized",
        "not supported",
        "rejected",
        "unknown",
        "unrecognized",
        "unsupported",
    )
    return any(term in normalized for term in rejected_terms)


def _validate_openai_compatible_http_url(url: str) -> str:
    normalized = _validate_http_url(url, field_name="openai-compatible url")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise PostProcessError("openai-compatible url must use https:// unless host is local loopback")
    return normalized


def _read_json(request: urllib.request.Request, timeout: int) -> object:
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise PostProcessError("timeout must be an integer")
    with _open_http_request(request, timeout=timeout, field_name="postprocess request") as response:
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
            "message": f"Ollama is not reachable at {_safe_url_display(url or DEFAULT_OLLAMA_URL, field_name='ollama url')}: {_sanitize_remote_error_detail(exc)}",
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
    raw = str(name or "")
    if _contains_escaped_null(raw) or _contains_http_header_control_chars(raw):
        return False
    normalized = raw.strip().lower()
    if not normalized:
        return False
    if normalized.startswith(OPENAI_COMPATIBLE_TEXT_MODEL_EXCLUDED_PREFIXES):
        return False
    return not any(term in normalized for term in OPENAI_COMPATIBLE_TEXT_MODEL_EXCLUDED_TERMS)


def _sanitize_remote_error_detail(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "[redacted remote error]"
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\x00", "\\x00")
    if _is_flex_service_tier_rejected(text):
        return "service_tier unsupported"
    return "[redacted remote error]"


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
        finally:
            with suppress(Exception):
                exc.close()
        detail = _sanitize_remote_error_detail(_openai_compatible_error_detail(raw_error) or exc.reason or str(exc))
        return {
            "available": False,
            "models": [],
            "message": f"OpenAI-compatible API failed ({exc.code}) at {_safe_url_display(endpoint, field_name='openai-compatible url')}: {detail}",
        }
    except OSError as exc:
        detail = _sanitize_remote_error_detail(str(exc))
        return {
            "available": False,
            "models": [],
            "message": f"OpenAI-compatible API is not reachable at {_safe_url_display(url or DEFAULT_OPENAI_COMPATIBLE_URL, field_name='openai-compatible url')}: {detail}",
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
        with _open_http_request(request, timeout=180, field_name="ollama post-process request") as response:
            raw = _read_response_text(response, MAX_POSTPROCESS_JSON_BYTES)
    except OSError as exc:
        raise PostProcessError(f"Ollama request failed: {_sanitize_remote_error_detail(exc)}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PostProcessError("Ollama returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise PostProcessError("Ollama response must be a JSON object")
    if data.get("error"):
        raise PostProcessError(f"Ollama failed: {_sanitize_remote_error_detail(data['error'])}")
    processed = _strip_transcript_prompt_label(str(data.get("response") or ""))
    processed = _assert_text_length(processed, field_name="post-process output")
    if not processed:
        raise PostProcessError("Ollama completed without output")
    return processed


def _openai_compatible_headers(api_key: str = "") -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if not api_key:
        api_key = _coerce_environment_text("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY")
    if not isinstance(api_key, str) or isinstance(api_key, bool):
        raise PostProcessError("openai-compatible API key must be text")
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
    language = _safe_prompt_language(language)
    try:
        personalization = build_personalization_prompt(personal_context, vocabulary)
    except ValueError as exc:
        raise PostProcessError(str(exc)) from exc

    system_sections = [
        (instruction or DEFAULT_OLLAMA_PROMPT).strip(),
        POSTPROCESS_OUTPUT_CONTRACT,
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


def _strip_transcript_prompt_label(text: str) -> str:
    value = text.strip()
    for _ in range(3):
        folded = value.casefold()
        if folded.startswith("transcript:"):
            value = value[len("Transcript:"):].lstrip()
            continue
        if folded.startswith("transkript:"):
            value = value[len("Transkript:"):].lstrip()
            continue
        break
    return value


def post_process_with_openai_compatible(
    text: str,
    language: str,
    model: str,
    url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    personal_context: str = "",
    vocabulary: str = "",
    prompt: str = "",
    api_key: str = "",
    flex_processing: bool = True,
    openai_compatible_service_tier_fallback: bool = False,
) -> str:
    if not isinstance(model, str) or isinstance(model, bool):
        raise PostProcessError("openai-compatible model must be text")
    if not isinstance(prompt, str) or isinstance(prompt, bool):
        raise PostProcessError("prompt must be text")
    if not isinstance(api_key, str) or isinstance(api_key, bool):
        raise PostProcessError("api key must be text")
    if not isinstance(flex_processing, bool):
        raise PostProcessError("OpenAI-compatible flex processing must be a boolean")
    if not isinstance(openai_compatible_service_tier_fallback, bool):
        raise PostProcessError("OpenAI-compatible service tier fallback must be a boolean")
    model_name = _assert_openai_compatible_text(
        str(model or ""),
        field_name="openai-compatible model",
        max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    ).strip()
    if not model_name:
        raise PostProcessError("OpenAI-compatible model is required")
    if not _openai_compatible_model_supports_text_polishing(model_name):
        raise PostProcessError("OpenAI-compatible model is not allowed for text polishing")
    _assert_text_length(text, field_name="input text")
    endpoint = _openai_compatible_endpoint(url, "/chat/completions")
    payload = {
        "model": model_name,
        "messages": build_openai_compatible_messages(text, language, personal_context, vocabulary, prompt),
        "stream": False,
        "temperature": 0,
    }
    use_flex_processing = flex_processing and _is_openai_api_endpoint(endpoint)
    if use_flex_processing:
        payload["service_tier"] = "flex"
    allow_service_tier_fallback = use_flex_processing and openai_compatible_service_tier_fallback

    def _request_chat_completion(request_payload: dict[str, object]) -> str:
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(request_payload).encode("utf-8"),
            headers=_openai_compatible_headers(api_key),
            method="POST",
        )
        with _open_http_request(request, timeout=180, field_name="openai-compatible post-process request") as response:
            return _read_response_text(response, MAX_POSTPROCESS_JSON_BYTES)

    try:
        raw = _request_chat_completion(payload)
    except urllib.error.HTTPError as exc:
        try:
            raw_error = _read_response_text(exc, MAX_POSTPROCESS_JSON_BYTES)
        except PostProcessError:
            raw_error = ""
        finally:
            with suppress(Exception):
                exc.close()
        detail = _sanitize_remote_error_detail(_openai_compatible_error_detail(raw_error) or exc.reason or str(exc))
        if allow_service_tier_fallback and _is_flex_service_tier_rejected(detail):
            fallback_payload = dict(payload)
            fallback_payload.pop("service_tier", None)
            try:
                raw = _request_chat_completion(fallback_payload)
            except urllib.error.HTTPError as fallback_exc:
                try:
                    raw_error = _read_response_text(fallback_exc, MAX_POSTPROCESS_JSON_BYTES)
                except PostProcessError:
                    raw_error = ""
                finally:
                    with suppress(Exception):
                        fallback_exc.close()
                fallback_detail = _sanitize_remote_error_detail(_openai_compatible_error_detail(raw_error) or fallback_exc.reason or str(fallback_exc))
                raise PostProcessError(
                    f"OpenAI-compatible request failed ({fallback_exc.code}) at {_safe_url_display(endpoint, field_name='openai-compatible url')}: {fallback_detail}"
                ) from fallback_exc
            except OSError as fallback_exc:
                raise PostProcessError(f"OpenAI-compatible request failed: {_sanitize_remote_error_detail(fallback_exc)}") from fallback_exc
        else:
            raise PostProcessError(
                f"OpenAI-compatible request failed ({exc.code}) at {_safe_url_display(endpoint, field_name='openai-compatible url')}: {detail}"
            ) from exc
    except OSError as exc:
        raise PostProcessError(f"OpenAI-compatible request failed: {_sanitize_remote_error_detail(exc)}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PostProcessError("OpenAI-compatible server returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise PostProcessError("OpenAI-compatible response must be a JSON object")
    if data.get("error"):
        error = data["error"]
        detail = str(error.get("message") or error) if isinstance(error, dict) else str(error)
        raise PostProcessError(f"OpenAI-compatible server failed: {_sanitize_remote_error_detail(detail)}")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise PostProcessError("OpenAI-compatible server completed without choices")
    processed = _strip_transcript_prompt_label(_choice_text(choices[0]))
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
    openai_compatible_flex_processing: bool = True,
    openai_compatible_service_tier_fallback: bool = False,
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
    if not isinstance(openai_compatible_flex_processing, bool):
        raise PostProcessError("OpenAI-compatible flex processing must be a boolean")
    if not isinstance(openai_compatible_service_tier_fallback, bool):
        raise PostProcessError("OpenAI-compatible service tier fallback must be a boolean")
    raw_backend = backend or "command"
    if _contains_escaped_null(raw_backend):
        raise PostProcessError("backend contains invalid null byte")
    if _contains_http_header_control_chars(raw_backend):
        raise PostProcessError("backend contains invalid control character")
    normalized_backend = raw_backend.strip().lower().replace("_", "-")
    text = _assert_text_length(text, field_name="input text")
    if normalized_backend in {"none", "off", "disabled"}:
        return text
    if normalized_backend == "ollama":
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
        return post_process_with_openai_compatible(
            text,
            language,
            openai_compatible_model,
            openai_compatible_url,
            personal_context,
            vocabulary,
            ollama_prompt,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback,
        )
    if normalized_backend not in {"command", "custom"}:
        raise PostProcessError(f"unknown post-process backend: {backend}")
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
            if "command failed" in message or "command execution failed" in message:
                raise PostProcessError(REDACTED_LOCAL_COMMAND_ERROR) from exc
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
