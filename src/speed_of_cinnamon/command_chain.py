from __future__ import annotations

from collections.abc import Sequence
import os
import shlex
import subprocess
import tempfile
import io
import shutil

from .personalization import command_environment


class CommandChainError(RuntimeError):
    pass


FORBIDDEN_COMMAND_OPERATORS = {
    "|",
    "||",
    "|&",
    "&",
    "<",
    ">",
    ">>",
    "2>",
    "2>>",
    "1>",
    "1>>",
    "&>",
    "2>&1",
    "1>&2",
    "1>&1",
    "2>&2",
    "2<&1",
    "1<&0",
}
DEFAULT_COMMAND_TIMEOUT_SECONDS = 180
MAX_COMMAND_OUTPUT_CHARS = 1_000_000
MAX_COMMAND_LENGTH_CHARS = 8_192
MAX_COMMAND_SEGMENTS = 32
MAX_COMMAND_SEGMENT_TOKENS = 128
MAX_COMMAND_INPUT_CHARS = 1_000_000
MAX_FILE_READ_FOR_ERROR_CHARS = 4096


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise CommandChainError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise CommandChainError("command is empty")
    if _contains_command_control_chars(command_name):
        raise CommandChainError("command contains invalid control character")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        return command_name
    resolved = shutil.which(command_name)
    if not resolved:
        raise CommandChainError(f"{command_name} is not available")
    return resolved


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_command_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    return "\r" in value or "\n" in value


def split_command_chain(command: str, label: str = "command") -> list[list[str]]:
    if isinstance(command, bool) or not isinstance(command, str):
        raise CommandChainError("command must be text")
    if isinstance(label, bool) or not isinstance(label, str):
        raise CommandChainError("label must be text")
    if _contains_command_control_chars(command):
        raise CommandChainError(f"invalid {label} command: contains control characters")
    if _contains_escaped_null(command):
        raise CommandChainError(f"invalid {label} command: contains invalid null byte")
    if len(command) > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")
    if len(command.encode("utf-8")) > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise CommandChainError(f"invalid {label} command: {exc}") from exc

    if not tokens:
        raise CommandChainError(f"{label} command is empty")

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == "&&":
            if not segments[-1]:
                raise CommandChainError(f"empty {label} command segment before &&")
            if len(segments) >= MAX_COMMAND_SEGMENTS:
                raise CommandChainError(f"{label} command has too many segments")
            segments.append([])
            continue
        if token in FORBIDDEN_COMMAND_OPERATORS:
            raise CommandChainError(f"unsupported shell operator in {label} command: {token}")
        if len(segments[-1]) >= MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        segments[-1].append(token)

    if not segments[-1]:
        raise CommandChainError(f"{label} command ended with &&")
    return segments


def run_command_chain(
    segments: Sequence[Sequence[str]],
    input_text: str,
    *,
    label: str,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_output_chars: int = MAX_COMMAND_OUTPUT_CHARS,
    max_input_chars: int = MAX_COMMAND_INPUT_CHARS,
    personal_context: str = "",
    vocabulary: str = "",
) -> str:
    if not isinstance(segments, (list, tuple)):
        raise CommandChainError("segments must be a sequence")
    if not all(isinstance(segment, (list, tuple)) for segment in segments):
        raise CommandChainError("segments must contain sequences")
    if not segments:
        raise CommandChainError(f"{label} command chain is empty")
    if isinstance(label, bool) or not isinstance(label, str):
        raise CommandChainError("label must be text")
    if len(segments) > MAX_COMMAND_SEGMENTS:
        raise CommandChainError(f"{label} command has too many segments")
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool):
        raise CommandChainError("max_output_chars must be an integer")
    if max_output_chars <= 0:
        raise CommandChainError("max_output_chars must be positive")
    if max_output_chars > MAX_COMMAND_OUTPUT_CHARS:
        raise CommandChainError(f"max_output_chars must not exceed {MAX_COMMAND_OUTPUT_CHARS}")
    if not isinstance(max_input_chars, int) or isinstance(max_input_chars, bool):
        raise CommandChainError("max_input_chars must be an integer")
    if max_input_chars > MAX_COMMAND_INPUT_CHARS:
        raise CommandChainError(f"max_input_chars must not exceed {MAX_COMMAND_INPUT_CHARS}")
    if max_input_chars < 0:
        raise CommandChainError("max_input_chars must be non-negative")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise CommandChainError("timeout_seconds must be an integer")
    if timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if not isinstance(input_text, str) or isinstance(input_text, bool):
        raise CommandChainError("input text must be text")
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise CommandChainError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise CommandChainError("vocabulary must be text")
    if _contains_escaped_null(input_text):
        raise CommandChainError("command input contains invalid null byte")
    try:
        input_bytes = input_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CommandChainError("command input is not valid UTF-8") from exc

    try:
        env = command_environment(personal_context, vocabulary)
    except ValueError as exc:
        raise CommandChainError(str(exc)) from exc
    output = input_text

    for segment in segments:
        if len(input_bytes) > max_input_chars:
            raise CommandChainError(f"{label} command input exceeded {max_input_chars} bytes")

        cmd = list(segment)
        if len(cmd) >= MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        if not all(isinstance(item, str) for item in cmd):
            raise CommandChainError(f"{label} command segment contains non-text item")
        if not cmd:
            raise CommandChainError(f"invalid {label} command segment")
        executable = str(cmd[0]).strip()
        if not executable:
            raise CommandChainError(f"invalid {label} command segment")
        if _contains_escaped_null(executable) or any(_contains_escaped_null(str(arg)) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid null byte")
        if _contains_command_control_chars(executable) or any(_contains_command_control_chars(str(arg)) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid control character")
        runtime_command = _command_path(executable)
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                proc = subprocess.run(
                    [runtime_command, *cmd[1:]],
                    input=input_bytes,
                    text=False,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=timeout_seconds,
                    env=env,
                )
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout_size = _filesize(stdout_file)
                stderr_size = _filesize(stderr_file)

                if stdout_size > max_output_chars:
                    raise CommandChainError(f"{label} command output exceeded {max_output_chars} bytes")

                if proc.returncode != 0:
                    detail = ""
                    if stderr_size:
                        detail = _read_file_head(stderr_file, MAX_FILE_READ_FOR_ERROR_CHARS).strip()
                    if not detail:
                        detail = _read_file_head(stdout_file, MAX_FILE_READ_FOR_ERROR_CHARS).strip()
                    if not detail:
                        detail = f"exit code {proc.returncode}"
                    raise CommandChainError(f"{label} command failed: {detail}")

                segment_output = _read_file_head(stdout_file, max_output_chars).strip()
                output = segment_output
                try:
                    input_bytes = output.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise CommandChainError("command output is not valid UTF-8") from exc
        except FileNotFoundError as exc:
            raise CommandChainError(f"{label} command not found: {executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandChainError(f"{label} command timed out after {timeout_seconds}s") from exc
        except OSError as exc:
            raise CommandChainError(f"{label} command execution failed: {exc}") from exc
    return output


def _filesize(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise CommandChainError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise CommandChainError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise CommandChainError("max_chars must be an integer")
    if max_chars <= 0:
        raise CommandChainError("max_chars must be positive")
    file.seek(0)
    try:
        text = file.read(max_chars).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CommandChainError(f"command output is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise CommandChainError("command output contains invalid null byte")
    return text
