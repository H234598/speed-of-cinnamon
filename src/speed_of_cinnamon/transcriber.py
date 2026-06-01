from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path


class TranscriptionError(RuntimeError):
    pass


def _quote(value: Path | str) -> str:
    return shlex.quote(str(value))


def render_command_template(template: str, audio_path: Path, language: str, text_path: Path) -> str:
    output_base = text_path.with_suffix("")
    replacements = {
        "audio": _quote(audio_path),
        "language": _quote(language),
        "text": _quote(text_path),
        "output_base": _quote(output_base),
        "output_dir": _quote(text_path.parent),
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def transcribe_with_template(template: str, audio_path: Path, language: str, text_path: Path) -> str:
    command = render_command_template(template, audio_path, language, text_path)
    proc = subprocess.run(command, shell=True, text=True, capture_output=True, timeout=900)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise TranscriptionError(f"transcriber command failed: {detail}")

    if "{text}" in template and text_path.exists():
        return text_path.read_text(encoding="utf-8").strip()
    return proc.stdout.strip()


def transcribe_with_openai_whisper(audio_path: Path, language: str, text_path: Path) -> str:
    whisper = shutil.which("whisper")
    if not whisper:
        raise TranscriptionError("OpenAI whisper command is not installed")
    output_dir = text_path.parent
    proc = subprocess.run(
        [
            whisper,
            str(audio_path),
            "--language",
            language,
            "--output_format",
            "txt",
            "--output_dir",
            str(output_dir),
        ],
        text=True,
        capture_output=True,
        timeout=900,
    )
    if proc.returncode != 0:
        raise TranscriptionError(proc.stderr.strip() or "whisper failed")

    generated = output_dir / f"{audio_path.stem}.txt"
    if generated.exists():
        text = generated.read_text(encoding="utf-8").strip()
        text_path.write_text(text + "\n", encoding="utf-8")
        return text
    raise TranscriptionError("whisper completed but did not produce a transcript")


def transcribe(audio_path: Path, language: str, text_path: Path, command_template: str = "") -> str:
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise TranscriptionError(f"audio file is missing or empty: {audio_path}")
    text_path.parent.mkdir(parents=True, exist_ok=True)
    if command_template.strip():
        text = transcribe_with_template(command_template.strip(), audio_path, language, text_path)
    else:
        text = transcribe_with_openai_whisper(audio_path, language, text_path)
    text_path.write_text(text.strip() + "\n", encoding="utf-8")
    return text.strip()

