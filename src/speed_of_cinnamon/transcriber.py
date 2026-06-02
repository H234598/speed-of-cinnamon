from __future__ import annotations

import shutil
import shlex
import subprocess
import tempfile
import io
import os
from dataclasses import dataclass
from pathlib import Path

from .models import default_whisper_cpp_model_path, model_supports_language
from .command_chain import CommandChainError, MAX_COMMAND_OUTPUT_CHARS, run_command_chain, split_command_chain
from .personalization import build_personalization_prompt, normalize_context, normalize_vocabulary


TRANSCRIBE_COMMAND_TIMEOUT_SECONDS = 900
MAX_TRANSCRIBER_ERROR_CHARS = 4096
MAX_AUDIO_FILE_BYTES = 200 * 1024 * 1024
MAX_AUDIO_PATH_CHARS = 240
MAX_AUDIO_STEM_CHARS = 120
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".m4a", ".flac", ".ogg", ".mp3", ".aac", ".webm"}
MAX_TRANSCRIPT_TEXT_CHARS = 1_000_000
PLACEHOLDER_TRANSCRIPTS = {"[speaking in foreign language]"}


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise TranscriptionError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        return command_name
    resolved = shutil.which(command_name)
    if not resolved:
        raise TranscriptionError(f"{command_name} is not available")
    return resolved


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _write_text_atomic(path: Path, text: str) -> None:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    if isinstance(text, bool) or not isinstance(text, str):
        raise TranscriptionError("text must be text")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, dir=path.parent, encoding="utf-8") as handle:
        try:
            os.fchmod(handle.fileno(), 0o600)
        except OSError:
            pass
        handle.write(text)
        tmp_path = Path(handle.name)
    try:
        os.replace(tmp_path, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise TranscriptionError(f"failed to write transcript file: {path}") from exc


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise TranscriptionError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TranscriptionError("max_chars must be an integer")
    if max_chars <= 0:
        raise TranscriptionError("max_chars must be positive")
    file.seek(0)
    try:
        text = file.read(max_chars).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptionError(f"command output is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise TranscriptionError("command output contains invalid null byte")
    return text


def _read_text_file(path: Path) -> str:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptionError(f"failed to read generated transcript: {path}") from exc
    except OSError as exc:
        raise TranscriptionError(f"failed to read generated transcript: {path}") from exc
    if _contains_escaped_null(text):
        raise TranscriptionError(f"failed to read generated transcript: {path}")
    return text


def _file_size(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise TranscriptionError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _run_limited_process(command: list[str], *, timeout: int = TRANSCRIBE_COMMAND_TIMEOUT_SECONDS) -> None:
    if not isinstance(command, list):
        raise TranscriptionError("transcriber command must be a list")
    if not command:
        raise TranscriptionError("empty transcriber command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise TranscriptionError("timeout must be positive")
    if any(not isinstance(item, str) or isinstance(item, bool) for item in command):
        raise TranscriptionError("transcriber command items must be text")
    executable = command[0].strip()
    if not executable:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if _contains_escaped_null(executable) or any(_contains_escaped_null(arg) for arg in command[1:]):
        raise TranscriptionError("command argument contains invalid null byte")
    runtime_executable = _command_path(executable)
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(
                    [runtime_executable, *command[1:]],
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
    language: str = "en"


def validate_audio_file(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TranscriptionError("audio path must be a Path")
    if _contains_escaped_null(str(path)):
        raise TranscriptionError("audio path contains invalid null byte")
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
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError(f"{field_name} must be text")
    if max_chars is None:
        max_chars = MAX_TRANSCRIPT_TEXT_CHARS
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TranscriptionError("max_chars must be an integer")
    if max_chars <= 0:
        raise TranscriptionError("max_chars must be positive")
    if len(value) > max_chars:
        raise TranscriptionError(f"{field_name} is too large (max {max_chars} characters)")
    return value


def _reject_placeholder_transcript(text: str, language: str) -> None:
    normalized = " ".join(text.strip().lower().split())
    if normalized in PLACEHOLDER_TRANSCRIPTS:
        raise TranscriptionError(
            f"transcriber detected speech outside configured language {language}; "
            "switch the applet language or use a multilingual model"
        )


def _quote(value: Path | str) -> str:
    if isinstance(value, bool) or not isinstance(value, (Path, str)):
        raise TranscriptionError("value must be text")
    return shlex.quote(str(value))


def render_command_template(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    try:
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
    except ValueError as exc:
        raise TranscriptionError(str(exc)) from exc
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
            "personal context is too large" in message
            or "vocabulary is too large" in message
            or "command output contains invalid null byte" in message
            or "command contains invalid null byte" in message
            or "command failed" in message
            or "command not found" in message
            or "command execution failed" in message
            or "command input exceeded" in message
            or "max_input_chars must be positive" in message
            or "max_input_chars must be non-negative" in message
            or "max_input_chars must not exceed" in message
            or "max_output_chars must be non-negative" in message
            or "max_output_chars must be positive" in message
            or "max_output_chars must not exceed" in message
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
    if not model_supports_language(model_path, language):
        raise TranscriptionError(
            f"English-only whisper.cpp model does not support language {language}; use a multilingual model"
        )
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
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("backend must be text")
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
    if not isinstance(config, TranscriberConfig):
        raise TranscriptionError("config must be TranscriberConfig")
    backend = normalize_backend(config.backend)
    local_model = config.whisper_model.strip() or default_whisper_cpp_model_path(config.language)
    if _contains_escaped_null(config.whisper_model or ""):
        raise TranscriptionError("whisper model contains invalid null byte")
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
    if not isinstance(audio_path, Path):
        raise TranscriptionError("audio path must be a Path")
    if isinstance(language, bool) or not isinstance(language, str):
        raise TranscriptionError("language must be text")
    audio_path = validate_audio_file(audio_path)
    text_path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(command_template, str) or isinstance(command_template, bool):
        raise TranscriptionError("command template must be text")
    if not isinstance(backend, str) or isinstance(backend, bool):
        raise TranscriptionError("backend must be text")
    if not isinstance(whisper_model, str) or isinstance(whisper_model, bool):
        raise TranscriptionError("whisper model must be text")
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise TranscriptionError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise TranscriptionError("vocabulary must be text")
    config = TranscriberConfig(
        backend=backend,
        command_template=command_template,
        whisper_model=whisper_model,
        language=language,
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
            whisper_model or default_whisper_cpp_model_path(language),
        )
    else:
        raise TranscriptionError(f"unknown transcriber backend: {resolved_backend}")
    text = text.strip()
    _reject_placeholder_transcript(text, language)
    _assert_text_length(text, field_name="transcript")
    _write_text_atomic(text_path, text + "\n")
    return text
