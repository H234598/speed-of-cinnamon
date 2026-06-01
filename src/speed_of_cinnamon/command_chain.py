from __future__ import annotations

from collections.abc import Sequence
import shlex
import subprocess
import tempfile
import io

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


def split_command_chain(command: str, label: str = "command") -> list[list[str]]:
    if "\x00" in command:
        raise CommandChainError(f"invalid {label} command: contains invalid null byte")
    if len(command) > MAX_COMMAND_LENGTH_CHARS:
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
    if not segments:
        raise CommandChainError(f"{label} command chain is empty")
    if max_output_chars < 0:
        raise CommandChainError("max_output_chars must be non-negative")
    if max_input_chars < 0:
        raise CommandChainError("max_input_chars must be non-negative")
    if timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if "\x00" in input_text:
        raise CommandChainError("command input contains invalid null byte")

    env = command_environment(personal_context, vocabulary)
    output = input_text
    input_bytes = output.encode("utf-8")

    for segment in segments:
        if len(input_bytes) > max_input_chars:
            raise CommandChainError(f"{label} command input exceeded {max_input_chars} bytes")

        cmd = list(segment)
        if not cmd:
            raise CommandChainError(f"invalid {label} command segment")
        executable = str(cmd[0]).strip()
        if not executable:
            raise CommandChainError(f"invalid {label} command segment")
        if "\x00" in executable or any("\x00" in str(arg) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid null byte")
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                proc = subprocess.run(
                    [executable, *cmd[1:]],
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
                input_bytes = output.encode("utf-8")
        except FileNotFoundError as exc:
            raise CommandChainError(f"{label} command not found: {executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandChainError(f"{label} command timed out after {timeout_seconds}s") from exc
        except OSError as exc:
            raise CommandChainError(f"{label} command execution failed: {exc}") from exc
    return output


def _filesize(file: io.BufferedRandom) -> int:
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    file.seek(0)
    return file.read(max_chars).decode("utf-8", errors="replace")
