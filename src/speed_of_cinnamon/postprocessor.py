from __future__ import annotations

import shlex
import subprocess

from .personalization import build_personalization_prompt, command_environment, normalize_context, normalize_vocabulary


class PostProcessError(RuntimeError):
    pass


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


def post_process_text(
    text: str,
    language: str,
    command_template: str = "",
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
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
