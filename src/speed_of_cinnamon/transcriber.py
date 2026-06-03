from __future__ import annotations

import shutil
import shlex
import subprocess  # nosec B404
import tempfile
import io
import json
import os
import stat as stat_module
import uuid
import urllib.parse
import urllib.error
import urllib.request
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from .models import default_ctranslate2_model_path, default_whisper_cpp_model_path, model_backend_for_path, model_supports_language
from .command_chain import CommandChainError, MAX_COMMAND_OUTPUT_CHARS, run_command_chain, split_command_chain
from .personalization import build_personalization_prompt, normalize_context, normalize_vocabulary
from .postprocessor import (
    DEFAULT_OPENAI_COMPATIBLE_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
)
from .path_safety import (
    assert_no_symlink_ancestors,
    open_file_without_following_symlinks,
    read_text_without_following_symlinks,
    write_bytes_atomically_without_following_symlinks,
    write_text_atomically_without_following_symlinks,
)


TRANSCRIBE_COMMAND_TIMEOUT_SECONDS = 900
MAX_TRANSCRIBER_ERROR_CHARS = 4096
MAX_OPENAI_URL_CHARS = 2048
MAX_AUDIO_FILE_BYTES = 200 * 1024 * 1024
MAX_AUDIO_PATH_CHARS = 240
MAX_AUDIO_STEM_CHARS = 120
MAX_TRANSCRIBER_TEXT_CHARS = 65_535
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".m4a", ".flac", ".ogg", ".mp3", ".aac", ".webm"}
MAX_TRANSCRIPT_TEXT_CHARS = 1_000_000
MAX_TRANSCRIBER_JSON_BYTES = 1_000_000
PLACEHOLDER_TRANSCRIPTS = {"[speaking in foreign language]"}
OPENAI_TRANSCRIPTION_MODELS = {
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe-diarize",
    "whisper-1",
}


_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BASE_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
}
_DANGEROUS_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")
_DANGEROUS_ENV_KEYS = {
    "ENV",
    "PWD",
    "OLDPWD",
    "CDPATH",
    "PS4",
    "BASH_XTRACEFD",
    "SHELLOPTS",
    "PROMPT_COMMAND",
    "IFS",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "BASH_ENV",
}


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


def _is_unsafe_env_var(name: str) -> bool:
    return name in _DANGEROUS_ENV_KEYS or name.startswith(_DANGEROUS_ENV_PREFIXES)


def _coerce_environment_value(name: str) -> str | None:
    if isinstance(name, bool) or not isinstance(name, str):
        return None
    try:
        value = os.environ.__getitem__(name)
    except KeyError:
        return None
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return None
    return value


def _filtered_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = _coerce_environment_value(key)
        if value is not None:
            env[key] = value
    if base is not None:
        if not isinstance(base, dict):
            raise TranscriptionError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise TranscriptionError("environment keys must be text")
            if isinstance(value, bool):
                raise TranscriptionError("environment values must be text")
            if not isinstance(value, str):
                raise TranscriptionError("environment base must be a mapping")
            if _is_unsafe_env_var(key):
                raise TranscriptionError(f"environment key is not allowed: {key}")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise TranscriptionError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise TranscriptionError("command must be a bare command name without path separators")
    resolved = _which(command_name)
    if not resolved:
        raise TranscriptionError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_multipart_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
    lowered = (value or "")
    if _contains_escaped_null(value):
        return True
    if any(ord(ch) < 0x20 for ch in lowered):
        return True
    if "\\r" in lowered.lower() or "\\n" in lowered.lower():
        return True
    return False


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
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


def _model_path_exists(path: str) -> bool:
    if isinstance(path, bool) or not isinstance(path, str):
        return False
    if _contains_escaped_null(path):
        return False
    try:
        return Path(path).expanduser().exists()
    except (OSError, ValueError):
        return False


def _write_text_atomic(path: Path, text: str) -> None:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    if isinstance(text, bool) or not isinstance(text, str):
        raise TranscriptionError("text must be text")
    try:
        write_text_atomically_without_following_symlinks(path, text, field_name="transcript path")
    except (OSError, RuntimeError) as exc:
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
        text = read_text_without_following_symlinks(path, field_name="generated transcript path")
    except UnicodeDecodeError as exc:
        raise TranscriptionError(f"failed to read generated transcript: {path}") from exc
    except OSError as exc:
        raise TranscriptionError(f"failed to read generated transcript: {path}") from exc
    if _contains_escaped_null(text):
        raise TranscriptionError(f"failed to read generated transcript: {path}")
    return text


def _snapshot_existing_file(path: Path) -> bytes | None:
    try:
        assert_no_symlink_ancestors(path, field_name="existing transcript path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    if not path.exists():
        return None
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY, field_name="existing transcript path")
    except OSError as exc:
        raise TranscriptionError(f"failed to snapshot existing transcript file: {path}") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            data = handle.read(MAX_TRANSCRIPT_TEXT_CHARS + 1)
    except OSError as exc:
        raise TranscriptionError(f"failed to snapshot existing transcript file: {path}") from exc
    if len(data) > MAX_TRANSCRIPT_TEXT_CHARS:
        raise TranscriptionError(f"existing transcript file is too large: {path}")
    return data


def _restore_existing_file_snapshot(path: Path, snapshot: bytes) -> None:
    try:
        write_bytes_atomically_without_following_symlinks(
            path,
            snapshot,
            field_name="existing transcript path",
        )
    except (OSError, RuntimeError) as exc:
        raise TranscriptionError(f"failed to restore existing transcript file: {path}") from exc


def _read_response_text(response: object, max_bytes: int = MAX_TRANSCRIBER_JSON_BYTES) -> str:
    if not hasattr(response, "read"):
        raise TranscriptionError("response must be readable")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise TranscriptionError(f"API response exceeded {max_bytes} bytes")
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptionError("API response is not valid UTF-8") from exc


def _validate_http_request(request: urllib.request.Request, *, field_name: str) -> None:
    if not hasattr(request, "get_full_url"):
        raise TranscriptionError(f"{field_name} is not a valid request object")
    url = request.get_full_url()
    if not isinstance(url, str):
        raise TranscriptionError(f"{field_name} URL must be text")
    _validate_openai_compatible_api_url(url, field_name=field_name)


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
    normalized = _validate_openai_compatible_api_url(url, field_name=field_name, allow_query_fragment=True)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname
    if not hostname:
        raise TranscriptionError(f"{field_name} is missing hostname")
    return parsed.scheme, hostname.lower(), _effective_url_port(parsed)


def _validate_same_origin_redirect(source_url: str, redirect_url: str, *, field_name: str) -> None:
    source_origin = _url_origin(source_url, field_name=field_name)
    redirect_origin = _url_origin(redirect_url, field_name=f"{field_name} redirect")
    if redirect_origin != source_origin:
        raise TranscriptionError(f"{field_name} redirect target changes origin")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _validate_same_origin_redirect(req.get_full_url(), newurl, field_name="OpenAI-compatible speech request")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_http_request(request: urllib.request.Request, *, timeout: int, field_name: str) -> object:
    _validate_http_request(request, field_name=field_name)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler, urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)  # nosec B310


def _file_size(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise TranscriptionError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _run_limited_process(command: list[str] | tuple[str, ...], *, timeout: int = TRANSCRIBE_COMMAND_TIMEOUT_SECONDS) -> None:
    if not isinstance(command, (list, tuple)):
        raise TranscriptionError("transcriber command must be a list or tuple")
    if not command:
        raise TranscriptionError("empty transcriber command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise TranscriptionError("timeout must be positive")
    if any(not isinstance(item, str) or isinstance(item, bool) for item in command):
        raise TranscriptionError("transcriber command items must be text")
    executable = command[0].strip()
    if not executable:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if (
        _contains_escaped_null(executable)
        or any(_contains_escaped_null(arg) for arg in command[1:])
    ):
        raise TranscriptionError("command argument contains invalid null byte")
    if (
        _contains_http_header_control_chars(executable)
        or any(_contains_http_header_control_chars(arg) for arg in command[1:])
    ):
        raise TranscriptionError("command argument contains invalid control character")
    runtime_executable = _command_path(executable)
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(  # nosec B603
                    [runtime_executable, *command[1:]],
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout,
                    shell=False,
                    env=_filtered_environment(),
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
    if _contains_http_header_control_chars(str(path)):
        raise TranscriptionError("audio path contains invalid control character")
    normalized = path.expanduser()
    try:
        assert_no_symlink_ancestors(normalized, field_name="audio path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    if normalized.is_symlink():
        raise TranscriptionError(f"audio path must not be a symlink: {path}")
    if len(str(normalized)) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError(f"audio file path is too long: {path}")
    if len(str(normalized).encode("utf-8")) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError(f"audio file path is too long: {path}")
    if len(normalized.name) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError(f"audio file name is too long: {path}")
    if len(normalized.name.encode("utf-8")) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError(f"audio file name is too long: {path}")
    if len(normalized.stem) > MAX_AUDIO_STEM_CHARS:
        raise TranscriptionError(f"audio file stem is too long: {path}")
    if len(normalized.stem.encode("utf-8")) > MAX_AUDIO_STEM_CHARS:
        raise TranscriptionError(f"audio file stem is too long: {path}")
    try:
        stat_result = normalized.stat()
    except OSError as exc:
        raise TranscriptionError(f"audio file is missing or empty: {path}") from exc
    if not stat_module.S_ISREG(stat_result.st_mode):
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


def _read_private_file_bytes(path: Path, *, field_name: str) -> bytes:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise TranscriptionError(f"secure {field_name} open is not supported on this platform")
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
    except RuntimeError as exc:
        raise TranscriptionError(f"failed to read {field_name}: {path}") from exc
    try:
        fd = os.open(path, os.O_RDONLY | nofollow_flag)
    except OSError as exc:
        raise TranscriptionError(f"failed to read {field_name}: {path}") from exc
    try:
        handle = os.fdopen(fd, "rb")
    except OSError as exc:
        try:
            os.close(fd)
        except OSError:
            pass
        raise TranscriptionError(f"failed to read {field_name}: {path}") from exc
    try:
        return handle.read()
    except OSError as exc:
        raise TranscriptionError(f"failed to read {field_name}: {path}") from exc
    finally:
        handle.close()


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
    if len(value.encode("utf-8")) > max_chars:
        raise TranscriptionError(f"{field_name} is too large (max {max_chars} bytes)")
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
    should_read_text_file = "{text}" in template
    existing_snapshot = (
        _snapshot_existing_file(text_path)
        if should_read_text_file and text_path.exists()
        else None
    )
    command = render_command_template(template, audio_path, language, text_path, personal_context, vocabulary)
    def restore_text_path() -> None:
        if not should_read_text_file:
            return
        if existing_snapshot is not None:
            _restore_existing_file_snapshot(text_path, existing_snapshot)
            return
        try:
            text_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

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
        restore_text_path()
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

    try:
        if should_read_text_file and text_path.exists():
            output = _read_text_file(text_path)
            return _assert_text_length(output.strip(), field_name="transcript file text")
        return _assert_text_length(output, field_name="transcript")
    except Exception:
        restore_text_path()
        raise


def transcribe_with_openai_whisper(
    audio_path: Path,
    language: str,
    text_path: Path,
    write_transcript: bool = True,
) -> str:
    try:
        _command_path("whisper")
    except TranscriptionError as exc:
        raise TranscriptionError("OpenAI whisper command is not installed") from exc
    output_dir = text_path.parent
    generated = output_dir / f"{audio_path.stem}.txt"
    existing_snapshot = _snapshot_existing_file(generated) if generated == text_path else None
    _run_limited_process(
        [
            "whisper",
            str(audio_path),
            "--language",
            language,
            "--output_format",
            "txt",
            "--output_dir",
            str(output_dir),
        ],
    )
    if generated.exists():
        if generated == text_path:
            try:
                text = _read_text_file(generated).strip()
                _assert_text_length(text, field_name="transcript")
            except Exception:
                if existing_snapshot is not None:
                    _restore_existing_file_snapshot(generated, existing_snapshot)
                else:
                    try:
                        generated.unlink()
                    except OSError:
                        pass
                raise
            if not write_transcript:
                if existing_snapshot is not None:
                    _restore_existing_file_snapshot(generated, existing_snapshot)
                else:
                    try:
                        generated.unlink()
                    except OSError:
                        pass
            return text
        primary_error: BaseException | None = None
        try:
            text = _read_text_file(generated).strip()
            _assert_text_length(text, field_name="transcript")
            if write_transcript:
                _write_text_atomic(text_path, text + "\n")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                generated.unlink()
            except OSError as exc:
                if primary_error is None:
                    raise TranscriptionError("failed to remove generated transcript") from exc
        return text
    raise TranscriptionError("whisper completed but did not produce a transcript")


def resolve_whisper_cpp_command() -> str | None:
    for command in ("whisper-cli", "whisper.cpp", "pwcpp"):
        if _is_command_available(command):
            return command
    return None


def _is_command_available(command: str) -> bool:
    try:
        _command_path(command)
        return True
    except TranscriptionError:
        return False


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


def transcribe_with_whisper_cpp(
    audio_path: Path,
    language: str,
    text_path: Path,
    model_path: str,
    write_transcript: bool = True,
) -> str:
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
    existing_snapshot = _snapshot_existing_file(text_path) if generated_path == text_path else None
    _run_limited_process(invocation)
    if generated_path.exists():
        if generated_path == text_path:
            try:
                text = _read_text_file(generated_path).strip()
                _assert_text_length(text, field_name="transcript")
            except Exception:
                if existing_snapshot is not None:
                    _restore_existing_file_snapshot(generated_path, existing_snapshot)
                else:
                    try:
                        generated_path.unlink()
                    except OSError:
                        pass
                raise
            if not write_transcript:
                if existing_snapshot is not None:
                    _restore_existing_file_snapshot(generated_path, existing_snapshot)
                else:
                    try:
                        generated_path.unlink()
                    except OSError:
                        pass
            return text
        primary_error: BaseException | None = None
        try:
            text = _read_text_file(generated_path).strip()
            _assert_text_length(text, field_name="transcript")
            if write_transcript:
                _write_text_atomic(text_path, text + "\n")
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            try:
                generated_path.unlink()
            except OSError as exc:
                if primary_error is None:
                    raise TranscriptionError("failed to remove generated transcript") from exc
        return text
    raise TranscriptionError("whisper.cpp completed but did not produce a transcript")


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe_with_faster_whisper(
    audio_path: Path,
    language: str,
    text_path: Path,
    model_path: str,
    write_transcript: bool = True,
) -> str:
    if not model_path.strip():
        raise TranscriptionError("CTranslate2 model path is required")
    if not model_supports_language(model_path, language):
        raise TranscriptionError(
            f"CTranslate2 model does not support language {language}; use a multilingual model"
        )
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError("faster-whisper is not installed") from exc

    try:
        model = WhisperModel(model_path, device="cpu", compute_type="int8")
        segments, _info = model.transcribe(
            str(audio_path),
            language=language or None,
            task="transcribe",
            beam_size=5,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
    except Exception as exc:
        raise TranscriptionError(f"faster-whisper failed: {exc}") from exc
    _assert_text_length(text, field_name="transcript")
    if write_transcript:
        _write_text_atomic(text_path, text + "\n")
    return text


def _openai_compatible_endpoint(url: str, path: str) -> str:
    base = _validate_openai_compatible_api_url(url).rstrip("/")
    if not base:
        raise TranscriptionError("OpenAI-compatible API URL is required")
    normalized_path = "/" + path.strip("/")
    if base.endswith(normalized_path):
        return base
    return base + normalized_path


def _is_openai_api_endpoint(endpoint: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(endpoint)
    except ValueError:
        return False
    return (parsed.hostname or "").lower() == "api.openai.com"


def _is_flex_service_tier_rejected(detail: str) -> bool:
    normalized = detail.lower()
    return "service_tier" in normalized and ("invalid" in normalized or "unsupported" in normalized)


def _validate_openai_compatible_api_url(
    url: str,
    field_name: str = "OpenAI-compatible API URL",
    allow_query_fragment: bool = False,
) -> str:
    if _contains_escaped_null(url):
        raise TranscriptionError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(url):
        raise TranscriptionError(f"{field_name} contains invalid control character")
    base = _assert_text_length(url, field_name=field_name, max_chars=MAX_OPENAI_URL_CHARS).strip()
    if not base:
        raise TranscriptionError(f"{field_name} is required")
    parsed = urllib.parse.urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TranscriptionError(f"{field_name} must use http:// or https://")
    try:
        parsed.port
    except ValueError as exc:
        raise TranscriptionError(f"{field_name} has invalid port") from exc
    if parsed.username or parsed.password:
        raise TranscriptionError(f"{field_name} must not contain userinfo")
    if not allow_query_fragment and (parsed.query or parsed.fragment):
        raise TranscriptionError(f"{field_name} must not contain query or fragment")
    return base


def _safe_url_display(url: str, *, field_name: str) -> str:
    normalized = _validate_openai_compatible_api_url(url, field_name=field_name)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    port = _effective_url_port(parsed)
    if parsed.port is not None and port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path.rstrip("/"), "", "", ""))


def _openai_compatible_error_detail(raw: str) -> str:
    raw = _assert_text_length(raw or "", field_name="OpenAI-compatible API error", max_chars=MAX_TRANSCRIBER_ERROR_CHARS).strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = str(error.get("code") or "").strip()
            error_type = str(error.get("type") or "").strip()
            parts = [part for part in (message, code, error_type) if part]
            return " - ".join(parts) if parts else str(error)
        return str(error)
    return raw


def _sanitize_remote_error_detail(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "remote error"
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\x00", "\\x00")
    lowered = text.lower()
    sensitive_hints = (
        "authorization",
        "bearer ",
        "api key",
        "api_key",
        "apikey",
        "password",
        "prompt",
        "secret",
        "sess-",
        "sk-",
        "token",
        "transcript",
    )
    if any(hint in lowered for hint in sensitive_hints):
        return "[redacted remote error]"
    if "://" in text and "@" in text:
        return "[redacted remote error]"
    if len(text) > 160:
        return text[:157] + "..."
    return text


def _multipart_form_data(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
) -> tuple[bytearray, str]:
    boundary = "speed-of-cinnamon-" + uuid.uuid4().hex
    body = bytearray()
    file_name = file_path.name
    if "\r" in file_name or "\n" in file_name:
        raise TranscriptionError("audio file name contains invalid newline")
    file_name = file_name.replace("\\", "\\\\").replace('"', '\\"')
    try:
        file_bytes = _read_private_file_bytes(file_path, field_name="audio file for API upload")
    except TranscriptionError as exc:
        raise TranscriptionError(str(exc)) from exc
    for key, value in fields.items():
        if _contains_multipart_control_chars(key) or _contains_multipart_control_chars(value):
            raise TranscriptionError("multipart form field contains invalid control character")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_bytes)
    del file_bytes
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return body, boundary


def transcribe_with_openai_compatible_api(
    audio_path: Path,
    language: str,
    text_path: Path,
    model: str,
    url: str,
    api_key: str = "",
    flex_processing: bool = True,
    write_transcript: bool = True,
) -> str:
    audio_path = validate_audio_file(audio_path)
    if _contains_escaped_null(model):
        raise TranscriptionError("OpenAI-compatible speech model contains invalid null byte")
    if _contains_http_header_control_chars(model):
        raise TranscriptionError("multipart form field contains invalid control character")
    model = _assert_text_length(
        model,
        field_name="OpenAI-compatible speech model",
        max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    ).strip()
    if not model:
        raise TranscriptionError("OpenAI-compatible speech model is required")
    if _contains_escaped_null(api_key):
        raise TranscriptionError("OpenAI-compatible API key contains invalid null byte")
    if _contains_http_header_control_chars(api_key):
        raise TranscriptionError("OpenAI-compatible API key contains invalid control character")
    api_key = _assert_text_length(
        api_key,
        field_name="OpenAI-compatible API key",
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    ).strip()
    if not isinstance(flex_processing, bool):
        raise TranscriptionError("OpenAI-compatible flex processing must be a boolean")
    endpoint = _openai_compatible_endpoint(url, "/audio/transcriptions")
    endpoint_display = _safe_url_display(endpoint, field_name="OpenAI-compatible API URL")
    is_openai_api = _is_openai_api_endpoint(endpoint)
    if is_openai_api and model not in OPENAI_TRANSCRIPTION_MODELS:
        raise TranscriptionError(
            "OpenAI transcription endpoint requires a speech-to-text model such as "
            "gpt-4o-transcribe, gpt-4o-mini-transcribe, or whisper-1; configured model is "
            f"{model}"
        )
    fields = {
        "model": model,
        "language": language,
        "response_format": "json",
    }
    use_flex_processing = flex_processing and is_openai_api
    if use_flex_processing:
        fields["service_tier"] = "flex"

    def _request_transcription(request_fields: dict[str, str]) -> str:
        body, boundary = _multipart_form_data(request_fields, "file", audio_path)
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        with _open_http_request(
            request,
            timeout=TRANSCRIBE_COMMAND_TIMEOUT_SECONDS,
            field_name="OpenAI-compatible speech request",
        ) as response:
            return _read_response_text(response)

    try:
        raw = _request_transcription(fields)
    except urllib.error.HTTPError as exc:
        try:
            raw_error = _read_response_text(exc, MAX_TRANSCRIBER_ERROR_CHARS)
        except TranscriptionError:
            raw_error = ""
        finally:
            with suppress(Exception):
                exc.close()
        raw_detail = _openai_compatible_error_detail(raw_error) or exc.reason or str(exc)
        if use_flex_processing and _is_flex_service_tier_rejected(raw_detail):
            fallback_fields = dict(fields)
            fallback_fields.pop("service_tier", None)
            try:
                raw = _request_transcription(fallback_fields)
            except urllib.error.HTTPError as fallback_exc:
                try:
                    raw_error = _read_response_text(fallback_exc, MAX_TRANSCRIBER_ERROR_CHARS)
                except TranscriptionError:
                    raw_error = ""
                finally:
                    with suppress(Exception):
                        fallback_exc.close()
                fallback_detail = _sanitize_remote_error_detail(
                    _openai_compatible_error_detail(raw_error) or fallback_exc.reason or str(fallback_exc)
                )
                raise TranscriptionError(
                    f"OpenAI-compatible speech API failed ({fallback_exc.code}) at {endpoint_display}: {fallback_detail}"
                ) from fallback_exc
            except OSError as fallback_exc:
                detail = _sanitize_remote_error_detail(fallback_exc)
                raise TranscriptionError(f"OpenAI-compatible speech API is not reachable at {endpoint_display}: {detail}") from fallback_exc
        else:
            detail = _sanitize_remote_error_detail(raw_detail)
            raise TranscriptionError(f"OpenAI-compatible speech API failed ({exc.code}) at {endpoint_display}: {detail}") from exc
    except OSError as exc:
        detail = _sanitize_remote_error_detail(exc)
        raise TranscriptionError(f"OpenAI-compatible speech API is not reachable at {endpoint_display}: {detail}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TranscriptionError("OpenAI-compatible speech API returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise TranscriptionError("OpenAI-compatible speech API response must be a JSON object")
    if payload.get("error"):
        error = payload["error"]
        detail = _sanitize_remote_error_detail(str(error.get("message") or error) if isinstance(error, dict) else str(error))
        raise TranscriptionError(f"OpenAI-compatible speech API failed at {endpoint_display}: {detail}")
    text = str(payload.get("text") or "").strip()
    if not text:
        raise TranscriptionError("OpenAI-compatible speech API returned no transcript")
    _assert_text_length(text, field_name="transcript")
    if write_transcript:
        _write_text_atomic(text_path, text + "\n")
    return text


def normalize_backend(value: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("backend must be text")
    normalized = (value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "openai": "whisper",
        "openai-whisper": "whisper",
        "whisper-cpp": "whisper-cpp",
        "whisper.cpp": "whisper-cpp",
        "ctranslate2": "faster-whisper",
        "ct2": "faster-whisper",
        "faster-whisper": "faster-whisper",
        "external-api": "openai-compatible",
        "openai-compatible": "openai-compatible",
        "openai-compatible-api": "openai-compatible",
        "custom": "command",
        "template": "command",
    }
    return aliases.get(normalized, normalized)


def resolve_transcriber(config: TranscriberConfig) -> str:
    if not isinstance(config, TranscriberConfig):
        raise TranscriptionError("config must be TranscriberConfig")
    backend = normalize_backend(config.backend)
    configured_model = config.whisper_model.strip()
    has_configured_model = bool(configured_model)
    configured_model_backend = model_backend_for_path(configured_model) if configured_model else ""
    configured_model_is_dir = False
    if configured_model:
        try:
            configured_model_path = Path(configured_model).expanduser()
            configured_model_is_dir = configured_model_path.exists() and configured_model_path.is_dir()
        except (OSError, ValueError):
            configured_model_is_dir = False
    local_model = configured_model or default_ctranslate2_model_path(config.language) or default_whisper_cpp_model_path(config.language)
    if _contains_escaped_null(config.whisper_model or ""):
        raise TranscriptionError("whisper model contains invalid null byte")
    if backend == "auto":
        if config.command_template.strip():
            return "command"
        if has_configured_model:
            if not _model_path_exists(configured_model):
                raise TranscriptionError(f"configured whisper model path is missing: {configured_model}")
            if configured_model_backend == "faster-whisper" or configured_model_is_dir:
                if faster_whisper_available():
                    return "faster-whisper"
                raise TranscriptionError("configured CTranslate2 model requires faster-whisper")
            if configured_model_backend == "whisper-cpp":
                if resolve_whisper_cpp_command():
                    return "whisper-cpp"
                raise TranscriptionError("configured whisper model requires whisper.cpp")
            if resolve_whisper_cpp_command():
                return "whisper-cpp"
            raise TranscriptionError("configured model requires whisper.cpp")
        if configured_model_backend == "faster-whisper" and faster_whisper_available():
            return "faster-whisper"
        if configured_model_backend == "whisper-cpp" and resolve_whisper_cpp_command():
            return "whisper-cpp"
        if _is_command_available("whisper"):
            return "whisper"
        if local_model and model_backend_for_path(local_model) == "faster-whisper" and faster_whisper_available():
            return "faster-whisper"
        if local_model and resolve_whisper_cpp_command():
            return "whisper-cpp"
        raise TranscriptionError(
            "no transcriber available; install 'whisper', install faster-whisper, configure whisper.cpp with a model, "
            "or set a custom transcriber command"
        )
    if backend not in {"command", "whisper", "whisper-cpp", "faster-whisper", "openai-compatible"}:
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
    openai_compatible_model: str = DEFAULT_OPENAI_COMPATIBLE_MODEL,
    openai_compatible_url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    openai_compatible_api_key: str = "",
    openai_compatible_flex_processing: bool = True,
) -> str:
    if not isinstance(audio_path, Path):
        raise TranscriptionError("audio path must be a Path")
    if not isinstance(text_path, Path):
        raise TranscriptionError("text path must be a Path")
    if isinstance(language, bool) or not isinstance(language, str):
        raise TranscriptionError("language must be text")
    text_path = text_path.expanduser()
    try:
        assert_no_symlink_ancestors(text_path, field_name="transcript path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
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
    if not isinstance(openai_compatible_model, str) or isinstance(openai_compatible_model, bool):
        raise TranscriptionError("OpenAI-compatible speech model must be text")
    if not isinstance(openai_compatible_url, str) or isinstance(openai_compatible_url, bool):
        raise TranscriptionError("OpenAI-compatible API URL must be text")
    if not isinstance(openai_compatible_api_key, str) or isinstance(openai_compatible_api_key, bool):
        raise TranscriptionError("OpenAI-compatible API key must be text")
    if not isinstance(openai_compatible_flex_processing, bool):
        raise TranscriptionError("OpenAI-compatible flex processing must be a boolean")
    if _contains_escaped_null(openai_compatible_model):
        raise TranscriptionError("OpenAI-compatible speech model contains invalid null byte")
    if _contains_escaped_null(openai_compatible_api_key):
        raise TranscriptionError("OpenAI-compatible API key contains invalid null byte")
    if _contains_http_header_control_chars(openai_compatible_api_key):
        raise TranscriptionError("openai-compatible API key contains invalid control character")
    if _contains_http_header_control_chars(openai_compatible_model):
        raise TranscriptionError("multipart form field contains invalid control character")

    command_template = _assert_text_length(command_template, field_name="command template", max_chars=MAX_TRANSCRIBER_TEXT_CHARS)
    openai_compatible_model = _assert_text_length(
        openai_compatible_model,
        field_name="OpenAI-compatible speech model",
        max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    ).strip()
    openai_compatible_url = _assert_text_length(openai_compatible_url, field_name="OpenAI-compatible API URL", max_chars=MAX_OPENAI_URL_CHARS)
    openai_compatible_api_key = _assert_text_length(
        openai_compatible_api_key,
        field_name="OpenAI-compatible API key",
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    ).strip()
    audio_path = validate_audio_file(audio_path)

    config = TranscriberConfig(
        backend=backend,
        command_template=command_template,
        whisper_model=whisper_model,
        language=language,
    )
    resolved_backend = resolve_transcriber(config)
    if resolved_backend == "openai-compatible" and (
        "\r" in audio_path.name or "\n" in audio_path.name
    ):
        raise TranscriptionError("audio file name contains invalid newline")
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
        text = transcribe_with_openai_whisper(audio_path, language, text_path, write_transcript=False)
    elif resolved_backend == "whisper-cpp":
        text = transcribe_with_whisper_cpp(
            audio_path,
            language,
            text_path,
            whisper_model or default_whisper_cpp_model_path(language),
            write_transcript=False,
        )
    elif resolved_backend == "faster-whisper":
        text = transcribe_with_faster_whisper(
            audio_path,
            language,
            text_path,
            whisper_model or default_ctranslate2_model_path(language),
            write_transcript=False,
        )
    elif resolved_backend == "openai-compatible":
        openai_compatible_url = _validate_openai_compatible_api_url(openai_compatible_url)
        text = transcribe_with_openai_compatible_api(
            audio_path,
            language,
            text_path,
            openai_compatible_model,
            openai_compatible_url,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            write_transcript=False,
        )
    else:
        raise TranscriptionError(f"unknown transcriber backend: {resolved_backend}")
    text = text.strip()
    _reject_placeholder_transcript(text, language)
    _assert_text_length(text, field_name="transcript")
    return text
