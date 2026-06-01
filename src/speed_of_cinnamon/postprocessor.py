from __future__ import annotations

import shlex
import subprocess


class PostProcessError(RuntimeError):
    pass


def _quote(value: str) -> str:
    return shlex.quote(value)


def render_postprocess_template(template: str, text: str, language: str) -> str:
    values = {
        "text": _quote(text),
        "language": _quote(language),
    }
    return template.format_map(values)


def post_process_text(text: str, language: str, command_template: str = "") -> str:
    template = command_template.strip()
    if not template:
        return text

    command = render_postprocess_template(template, text, language)
    proc = subprocess.run(
        command,
        input=text,
        shell=True,
        text=True,
        capture_output=True,
        timeout=180,
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise PostProcessError(f"post-process command failed: {detail}")

    processed = proc.stdout.strip()
    if not processed:
        raise PostProcessError("post-process command completed without output")
    return processed
