from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .personalization import build_personalization_prompt, command_environment, normalize_context, normalize_vocabulary


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriberConfig:
    backend: str = "auto"
    command_template: str = ""
    whisper_model: str = ""


def _quote(value: Path | str) -> str:
    return shlex.quote(str(value))


def render_command_template(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    output_base = text_path.with_suffix("")
    replacements = {
        "audio": _quote(audio_path),
        "language": _quote(language),
        "text": _quote(text_path),
        "output_base": _quote(output_base),
        "output_dir": _quote(text_path.parent),
        "context": _quote(normalize_context(personal_context)),
        "vocabulary": _quote(normalize_vocabulary(vocabulary)),
        "prompt": _quote(build_personalization_prompt(personal_context, vocabulary)),
    }
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


def transcribe_with_template(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    command = render_command_template(template, audio_path, language, text_path, personal_context, vocabulary)
    proc = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
        timeout=900,
        env=command_environment(personal_context, vocabulary),
    )
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


def resolve_whisper_cpp_command() -> str | None:
    for command in ("whisper-cli", "whisper.cpp"):
        if shutil.which(command):
            return command
    return None


def transcribe_with_whisper_cpp(audio_path: Path, language: str, text_path: Path, model_path: str) -> str:
    if not model_path.strip():
        raise TranscriptionError("whisper.cpp model path is required")
    command = resolve_whisper_cpp_command()
    if not command:
        raise TranscriptionError("whisper.cpp command is not installed")

    output_base = text_path.with_suffix("")
    proc = subprocess.run(
        [
            command,
            "-m",
            model_path,
            "-f",
            str(audio_path),
            "-l",
            language,
            "-otxt",
            "-of",
            str(output_base),
        ],
        text=True,
        capture_output=True,
        timeout=900,
    )
    if proc.returncode != 0:
        raise TranscriptionError(proc.stderr.strip() or proc.stdout.strip() or "whisper.cpp failed")

    if text_path.exists():
        return text_path.read_text(encoding="utf-8").strip()
    raise TranscriptionError("whisper.cpp completed but did not produce a transcript")


def normalize_backend(value: str) -> str:
    normalized = (value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "openai": "whisper",
        "openai-whisper": "whisper",
        "whisper-cpp": "whisper-cpp",
        "whisper.cpp": "whisper-cpp",
        "custom": "command",
        "template": "command",
    }
    return aliases.get(normalized, normalized)


def resolve_transcriber(config: TranscriberConfig) -> str:
    backend = normalize_backend(config.backend)
    if backend == "auto":
        if config.command_template.strip():
            return "command"
        if shutil.which("whisper"):
            return "whisper"
        if config.whisper_model.strip() and resolve_whisper_cpp_command():
            return "whisper-cpp"
        raise TranscriptionError(
            "no transcriber available; install 'whisper', configure whisper.cpp with a model, "
            "or set a custom transcriber command"
        )
    if backend not in {"command", "whisper", "whisper-cpp"}:
        raise TranscriptionError(f"unknown transcriber backend: {config.backend}")
    return backend


def transcribe(
    audio_path: Path,
    language: str,
    text_path: Path,
    command_template: str = "",
    backend: str = "auto",
    whisper_model: str = "",
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise TranscriptionError(f"audio file is missing or empty: {audio_path}")
    text_path.parent.mkdir(parents=True, exist_ok=True)
    config = TranscriberConfig(
        backend=backend,
        command_template=command_template,
        whisper_model=whisper_model,
    )
    resolved_backend = resolve_transcriber(config)
    if resolved_backend == "command":
        if not command_template.strip():
            raise TranscriptionError("custom transcriber command is required")
        text = transcribe_with_template(
            command_template.strip(),
            audio_path,
            language,
            text_path,
            personal_context,
            vocabulary,
        )
    elif resolved_backend == "whisper":
        text = transcribe_with_openai_whisper(audio_path, language, text_path)
    elif resolved_backend == "whisper-cpp":
        text = transcribe_with_whisper_cpp(audio_path, language, text_path, whisper_model)
    else:
        raise TranscriptionError(f"unknown transcriber backend: {resolved_backend}")
    text_path.write_text(text.strip() + "\n", encoding="utf-8")
    return text.strip()
