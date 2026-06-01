from __future__ import annotations

import json
import os
import shlex
import subprocess
import urllib.request

from .personalization import build_personalization_prompt, command_environment, normalize_context, normalize_vocabulary


class PostProcessError(RuntimeError):
    pass


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OPENAI_COMPATIBLE_URL = "http://127.0.0.1:8000/v1"
DEFAULT_OLLAMA_PROMPT = (
    "Clean up the transcript for direct insertion. Preserve meaning, language, names, "
    "technical terms, and formatting. Return only the final text."
)


def _quote(value: str) -> str:
    return shlex.quote(value)


def render_postprocess_template(
    template: str,
    text: str,
    language: str,
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    values = {
        "text": _quote(text),
        "language": _quote(language),
        "context": _quote(normalize_context(personal_context)),
        "vocabulary": _quote(normalize_vocabulary(vocabulary)),
        "prompt": _quote(build_personalization_prompt(personal_context, vocabulary)),
    }
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
    sections = [
        (instruction or DEFAULT_OLLAMA_PROMPT).strip(),
        f"Language: {language}",
    ]
    personalization = build_personalization_prompt(personal_context, vocabulary)
    if personalization:
        sections.append(personalization)
    sections.append("Transcript:\n" + text.strip())
    return "\n\n".join(section for section in sections if section)


def _ollama_endpoint(url: str, path: str) -> str:
    base = (url or DEFAULT_OLLAMA_URL).rstrip("/")
    return base + "/" + path.lstrip("/")


def _openai_compatible_endpoint(url: str, path: str) -> str:
    base = (url or DEFAULT_OPENAI_COMPATIBLE_URL).rstrip("/")
    normalized_path = "/" + path.strip("/")
    if base.endswith(normalized_path):
        return base
    return base + normalized_path


def _read_json(request: urllib.request.Request, timeout: int) -> object:
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _format_model_size(size: object) -> str:
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
    owned_by = str(model.get("owned_by") or "").strip()
    return {
        "name": name,
        "model": name,
        "owned_by": owned_by,
        "description": owned_by,
    }


def list_openai_compatible_models(
    url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    timeout: int = 5,
) -> dict[str, object]:
    endpoint = _openai_compatible_endpoint(url, "/models")
    request = urllib.request.Request(endpoint, method="GET")
    try:
        data = _read_json(request, timeout)
    except OSError as exc:
        return {
            "available": False,
            "models": [],
            "message": (
                "OpenAI-compatible local server is not reachable at "
                f"{(url or DEFAULT_OPENAI_COMPATIBLE_URL).rstrip('/')}: {exc}"
            ),
        }
    except json.JSONDecodeError:
        return {
            "available": False,
            "models": [],
            "message": "OpenAI-compatible local server returned invalid JSON for model listing",
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
            "message": "OpenAI-compatible local server is running but returned no model list",
        }
    models = [model for item in raw_models if (model := _normalize_openai_compatible_model(item))]
    models.sort(key=lambda item: str(item["name"]).lower())
    return {
        "available": True,
        "models": models,
        "message": "OpenAI-compatible models loaded" if models else "No OpenAI-compatible local models found",
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
    model_name = (model or "").strip()
    if not model_name:
        raise PostProcessError("Ollama model is required")
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
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
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
    if not processed:
        raise PostProcessError("Ollama completed without output")
    return processed


def _openai_compatible_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    api_key = os.environ.get("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY", "").strip()
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
    system_sections = [
        (instruction or DEFAULT_OLLAMA_PROMPT).strip(),
        f"Language: {language}",
    ]
    personalization = build_personalization_prompt(personal_context, vocabulary)
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
) -> str:
    model_name = (model or "").strip()
    if not model_name:
        raise PostProcessError("OpenAI-compatible model is required")
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
        headers=_openai_compatible_headers(),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
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
) -> str:
    normalized_backend = (backend or "command").strip().lower().replace("_", "-")
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
        )
    if normalized_backend not in {"command", "custom"}:
        raise PostProcessError(f"unknown post-process backend: {backend}")

    template = command_template.strip()
    if not template:
        return text

    command = render_postprocess_template(template, text, language, personal_context, vocabulary)
    proc = subprocess.run(
        command,
        input=text,
        shell=True,
        text=True,
        capture_output=True,
        timeout=180,
        env=command_environment(personal_context, vocabulary),
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise PostProcessError(f"post-process command failed: {detail}")

    processed = proc.stdout.strip()
    if not processed:
        raise PostProcessError("post-process command completed without output")
    return processed
