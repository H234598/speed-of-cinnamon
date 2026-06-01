from __future__ import annotations

import shutil
import shlex
import subprocess
import tempfile
import io
import os
from dataclasses import dataclass
from pathlib import Path

from .models import default_whisper_cpp_model_path
from .command_chain import CommandChainError, MAX_COMMAND_OUTPUT_CHARS, run_command_chain, split_command_chain
from .personalization import build_personalization_prompt, normalize_context, normalize_vocabulary


TRANSCRIBE_COMMAND_TIMEOUT_SECONDS = 900
MAX_TRANSCRIBER_ERROR_CHARS = 4096
MAX_AUDIO_FILE_BYTES = 200 * 1024 * 1024
MAX_AUDIO_PATH_CHARS = 240
MAX_AUDIO_STEM_CHARS = 120
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".m4a", ".flac", ".ogg", ".mp3", ".aac", ".webm"}
MAX_TRANSCRIPT_TEXT_CHARS = 1_000_000


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        handle.write(text)
        tmp_path = Path(handle.name)
    try:
        os.replace(tmp_path, path)
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise TranscriptionError(f"failed to write transcript file: {path}") from exc


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    file.seek(0)
    return file.read(max_chars).decode("utf-8", errors="replace")


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TranscriptionError(f"failed to read generated transcript: {path}") from exc


def _file_size(file: io.BufferedRandom) -> int:
    file.seek(0, 2)
    return file.tell()


def _run_limited_process(command: list[str], *, timeout: int = TRANSCRIBE_COMMAND_TIMEOUT_SECONDS) -> None:
    if not command:
        raise TranscriptionError("empty transcriber command is not allowed")
    if timeout <= 0:
        raise TranscriptionError("timeout must be positive")
    executable = command[0].strip()
    if not executable:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if "\x00" in executable or any("\x00" in arg for arg in command[1:]):
        raise TranscriptionError("command argument contains invalid null byte")
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(
                    [executable, *command[1:]],
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout,
                )
            except FileNotFoundError as exc:
                raise TranscriptionError(f"{executable} is not available") from exc

            if _file_size(stdout_file) > MAX_COMMAND_OUTPUT_CHARS:
                raise TranscriptionError(
                    f"command output exceeded {MAX_COMMAND_OUTPUT_CHARS} bytes"
                )
            if _file_size(stderr_file) > MAX_COMMAND_OUTPUT_CHARS:
                raise TranscriptionError(
                    f"command error output exceeded {MAX_COMMAND_OUTPUT_CHARS} bytes"
                )

            if proc.returncode != 0:
                stderr = _read_file_head(stderr_file, MAX_TRANSCRIBER_ERROR_CHARS).strip()
                stdout = _read_file_head(stdout_file, MAX_TRANSCRIBER_ERROR_CHARS).strip()
                raise TranscriptionError(stderr or stdout or f"transcriber backend failed: {command[0]}")
    except subprocess.TimeoutExpired as exc:
        raise TranscriptionError(f"transcription backend timed out after {timeout}s") from exc
    except OSError as exc:
        raise TranscriptionError(f"failed to run transcriber backend: {exc}") from exc


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class TranscriberConfig:
    backend: str = "auto"
    command_template: str = ""
    whisper_model: str = ""


def validate_audio_file(path: Path) -> Path:
    normalized = path.expanduser().resolve(strict=False)
    if len(str(normalized)) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError(f"audio file path is too long: {path}")
    if len(normalized.name) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError(f"audio file name is too long: {path}")
    if len(normalized.stem) > MAX_AUDIO_STEM_CHARS:
        raise TranscriptionError(f"audio file stem is too long: {path}")
    try:
        stat_result = normalized.stat()
    except OSError as exc:
        raise TranscriptionError(f"audio file is missing or empty: {path}") from exc
    if not normalized.is_file():
        raise TranscriptionError(f"audio path is not a regular file: {path}")
    if normalized.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise TranscriptionError(f"unsupported audio extension: {normalized.suffix}")
    if stat_result.st_size == 0:
        raise TranscriptionError(f"audio file is missing or empty: {path}")
    if stat_result.st_size > MAX_AUDIO_FILE_BYTES:
        raise TranscriptionError(
            f"audio file is too large: {stat_result.st_size} bytes (max {MAX_AUDIO_FILE_BYTES})"
        )
    return normalized


def _assert_text_length(value: str, *, field_name: str, max_chars: int | None = None) -> str:
    if max_chars is None:
        max_chars = MAX_TRANSCRIPT_TEXT_CHARS
    if len(value) > max_chars:
        raise TranscriptionError(f"{field_name} is too large (max {max_chars} characters)")
    return value


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
    try:
        segments = split_command_chain(command, label="transcriber")
        output = run_command_chain(
            segments,
            "",
            label="transcriber",
            timeout_seconds=TRANSCRIBE_COMMAND_TIMEOUT_SECONDS,
            max_output_chars=MAX_COMMAND_OUTPUT_CHARS,
            personal_context=personal_context,
            vocabulary=vocabulary,
        )
    except CommandChainError as exc:
        message = str(exc)
        if message.startswith("invalid transcriber") or message.startswith("unsupported shell operator in transcriber"):
            raise TranscriptionError(message) from exc
        if (
            message.startswith("transcriber command ended")
            or message.startswith("empty transcriber")
            or message.startswith("transcriber command chain is empty")
        ):
            raise TranscriptionError(message) from exc
        if (
            "command failed" in message
            or "command not found" in message
            or "command execution failed" in message
            or "command input exceeded" in message
            or "max_input_chars must be non-negative" in message
            or "max_output_chars must be non-negative" in message
            or "timeout_seconds must be positive" in message
            or "command input contains invalid null byte" in message
        ):
            raise TranscriptionError(message) from exc
        if "command output exceeded" in message:
            raise TranscriptionError(message) from exc
        if "command timed out" in message:
            raise TranscriptionError(message) from exc
        raise TranscriptionError(f"transcriber command failed: {message}") from exc

    if "{text}" in template and text_path.exists():
        output = _read_text_file(text_path)
        return _assert_text_length(output.strip(), field_name="transcript file text")
    return _assert_text_length(output, field_name="transcript")


def transcribe_with_openai_whisper(audio_path: Path, language: str, text_path: Path) -> str:
    whisper = shutil.which("whisper")
    if not whisper:
        raise TranscriptionError("OpenAI whisper command is not installed")
    output_dir = text_path.parent
    _run_limited_process(
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
    )
    generated = output_dir / f"{audio_path.stem}.txt"
    if generated.exists():
        text = _read_text_file(generated).strip()
        _assert_text_length(text, field_name="transcript")
        _write_text_atomic(text_path, text + "\n")
        return text
    raise TranscriptionError("whisper completed but did not produce a transcript")


def resolve_whisper_cpp_command() -> str | None:
    for command in ("whisper-cli", "whisper.cpp", "pwcpp"):
        if shutil.which(command):
            return command
    return None


def _whisper_cpp_invocation(
    command: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    model_path: str,
) -> tuple[list[str], Path]:
    if Path(command).name == "pwcpp":
        return (
            [
                command,
                "-m",
                model_path,
                "--language",
                language,
                "-otxt",
                str(audio_path),
            ],
            audio_path.with_name(audio_path.name + ".txt"),
        )

    output_base = text_path.with_suffix("")
    return (
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
        text_path,
    )


def transcribe_with_whisper_cpp(audio_path: Path, language: str, text_path: Path, model_path: str) -> str:
    if not model_path.strip():
        raise TranscriptionError("whisper.cpp model path is required")
    command = resolve_whisper_cpp_command()
    if not command:
        raise TranscriptionError("whisper.cpp command is not installed")

    invocation, generated_path = _whisper_cpp_invocation(command, audio_path, language, text_path, model_path)
    _run_limited_process(invocation)
    if generated_path.exists():
        text = _read_text_file(generated_path).strip()
        _assert_text_length(text, field_name="transcript")
        if generated_path != text_path:
            _write_text_atomic(text_path, text + "\n")
            try:
                generated_path.unlink()
            except OSError:
                pass
        return text
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
    local_model = config.whisper_model.strip() or default_whisper_cpp_model_path()
    if backend == "auto":
        if config.command_template.strip():
            return "command"
        if shutil.which("whisper"):
            return "whisper"
        if local_model and resolve_whisper_cpp_command():
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
    audio_path = validate_audio_file(audio_path)
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
        text = transcribe_with_whisper_cpp(
            audio_path,
            language,
            text_path,
            whisper_model or default_whisper_cpp_model_path(),
        )
    else:
        raise TranscriptionError(f"unknown transcriber backend: {resolved_backend}")
    text = text.strip()
    _assert_text_length(text, field_name="transcript")
    _write_text_atomic(text_path, text + "\n")
    return text
