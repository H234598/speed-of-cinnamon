from __future__ import annotations

import json
import shlex
import subprocess
import urllib.request

from .personalization import build_personalization_prompt, command_environment, normalize_context, normalize_vocabulary


class PostProcessError(RuntimeError):
    pass


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
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
    endpoint = (url or DEFAULT_OLLAMA_URL).rstrip("/") + "/api/generate"
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
