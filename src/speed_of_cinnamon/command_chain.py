from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
import os
import re
import selectors
import shlex
import signal
import subprocess  # nosec B404
import tempfile
import time
import io
import shutil
from pathlib import Path

from .personalization import command_environment
from .path_safety import assert_no_symlink_ancestors


class CommandChainError(RuntimeError):
    pass


_REDACTED_COMMAND_OUTPUT = "exit code {returncode}; command output redacted"
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
_ESCAPED_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)


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
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
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
            raise CommandChainError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise CommandChainError("environment keys must be text")
            if isinstance(value, bool):
                raise CommandChainError("environment values must be text")
            if not isinstance(value, str):
                raise CommandChainError("environment base must be a mapping")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise CommandChainError("environment key contains invalid control character")
            if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
                raise CommandChainError("environment value contains invalid control character")
            if _is_unsafe_env_var(key):
                raise CommandChainError(f"environment key is not allowed: {key}")
            env[key] = value

    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


def _command_failure_detail(returncode: int, stdout_size: int, stderr_size: int) -> str:
    if stdout_size or stderr_size:
        return _REDACTED_COMMAND_OUTPUT.format(returncode=returncode)
    return f"exit code {returncode}"


def _command_timeout_detail(label: str, timeout_seconds: int) -> str:
    return f"{label} command timed out after {timeout_seconds} seconds"


def _terminate_bounded_process(proc: subprocess.Popen[bytes]) -> None:
    pid = getattr(proc, "pid", None)
    try:
        if isinstance(pid, int) and pid > 0:
            os.killpg(pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except OSError:
        with suppress(OSError):
            proc.kill()
    try:
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_process_bounded_output(
    argv: Sequence[str],
    input_bytes: bytes = b"",
    *,
    timeout_seconds: int,
    max_output_bytes: int,
    env: dict[str, str],
    label: str,
) -> tuple[int, bytes, bytes]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise CommandChainError("argv must be a non-empty sequence")
    if not all(isinstance(item, str) for item in argv):
        raise CommandChainError("argv must contain text")
    if not isinstance(input_bytes, bytes):
        raise CommandChainError("input bytes must be bytes")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes <= 0:
        raise CommandChainError("max_output_bytes must be positive")
    if not isinstance(env, dict):
        raise CommandChainError("environment must be a mapping")

    runtime_argv = [*argv]
    with tempfile.TemporaryFile() as stdin_file:
        stdin_file.write(input_bytes)
        stdin_file.seek(0)
        try:
            proc = subprocess.Popen(  # nosec B603
                runtime_argv,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                shell=False,
                start_new_session=True,
            )
        except FileNotFoundError:
            raise
        except OSError:
            raise

        selector = selectors.DefaultSelector()
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_size = 0
        stderr_size = 0
        try:
            if proc.stdout is not None:
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            if proc.stderr is not None:
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + timeout_seconds
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _terminate_bounded_process(proc)
                    raise CommandChainError(_command_timeout_detail(label, timeout_seconds))
                events = selector.select(remaining)
                if not events:
                    continue
                for key, _event_mask in events:
                    stream = key.fileobj
                    data = os.read(stream.fileno(), 65_536)
                    if not data:
                        selector.unregister(stream)
                        try:
                            stream.close()
                        except OSError:
                            pass
                        continue
                    if key.data == "stdout":
                        stdout_chunks.append(data)
                        stdout_size += len(data)
                    else:
                        stderr_chunks.append(data)
                        stderr_size += len(data)
                    if stdout_size + stderr_size > max_output_bytes:
                        _terminate_bounded_process(proc)
                        raise CommandChainError(f"{label} command output exceeded {max_output_bytes} bytes")
            try:
                returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                _terminate_bounded_process(proc)
                raise CommandChainError(_command_timeout_detail(label, timeout_seconds)) from None
            return returncode, b"".join(stdout_chunks), b"".join(stderr_chunks)
        finally:
            for key in list(selector.get_map().values()):
                stream = key.fileobj
                with suppress(KeyError, ValueError):
                    selector.unregister(stream)
                try:
                    stream.close()
                except OSError:
                    pass
            selector.close()


FORBIDDEN_COMMAND_OPERATORS = {
    "|",
    "||",
    "|&",
    "&",
    ";",
    ";;",
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
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise CommandChainError("command must be a bare command name without path separators")
    if _contains_command_control_chars(command_name):
        raise CommandChainError("command contains invalid control character")
    resolved = _which(command_name)
    if not resolved:
        raise CommandChainError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    lowered = (value or "").lower()
    if _ESCAPED_CONTROL_RE.search(lowered):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _contains_command_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    return _contains_http_header_control_chars(value)


def _contains_command_output_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    for char in value:
        codepoint = ord(char)
        if codepoint in (0x09, 0x0A, 0x0D):
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def split_command_chain(command: str, label: str = "command") -> list[list[str]]:
    if isinstance(command, bool) or not isinstance(command, str):
        raise CommandChainError("command must be text")
    if isinstance(label, bool) or not isinstance(label, str):
        raise CommandChainError("label must be text")
    if _contains_escaped_null(command):
        raise CommandChainError(f"invalid {label} command: contains invalid null byte")
    if _contains_command_control_chars(command):
        raise CommandChainError(f"invalid {label} command: contains control characters")
    if len(command) > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")
    try:
        command_bytes_len = len(command.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise CommandChainError(f"invalid {label} command: not valid UTF-8") from exc
    if command_bytes_len > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")

    try:
        tokens = shlex.split(command)
    except ValueError as exc:
        raise CommandChainError(f"invalid {label} command: {exc}") from exc

    if not tokens:
        raise CommandChainError(f"{label} command is empty")

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token == _CHAIN_SEGMENT_SEPARATOR:
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
    include_personalization_env: bool = False,
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
    if not isinstance(include_personalization_env, bool):
        raise CommandChainError("include_personalization_env must be a boolean")
    if _contains_escaped_null(input_text):
        raise CommandChainError("command input contains invalid null byte")
    try:
        input_bytes = input_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CommandChainError("command input is not valid UTF-8") from exc

    try:
        personalization_env = command_environment(personal_context, vocabulary)
        env = (
            {key: value for key, value in personalization_env.items() if isinstance(key, str) and not _is_unsafe_env_var(key)}
            if include_personalization_env
            else {}
        )
        env = _filtered_environment(env)
    except ValueError as exc:
        raise CommandChainError(str(exc)) from exc
    output = input_text

    for segment in segments:
        if len(output) > max_input_chars:
            raise CommandChainError(f"{label} command input exceeded {max_input_chars} characters")

        cmd = list(segment)
        if len(cmd) > MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        if not all(isinstance(item, str) for item in cmd):
            raise CommandChainError(f"{label} command segment contains non-text item")
        if not cmd:
            raise CommandChainError(f"invalid {label} command segment")
        try:
            command_chars_len = sum(len(item) + 1 for item in cmd)
            command_bytes_len = sum(len(item.encode("utf-8")) + 1 for item in cmd)
        except UnicodeEncodeError as exc:
            raise CommandChainError(f"invalid {label} command: not valid UTF-8") from exc
        if command_chars_len > MAX_COMMAND_LENGTH_CHARS or command_bytes_len > MAX_COMMAND_LENGTH_CHARS:
            raise CommandChainError(f"invalid {label} command: command too long")
        executable = str(cmd[0]).strip()
        if not executable:
            raise CommandChainError(f"invalid {label} command segment")
        if _contains_escaped_null(executable) or any(_contains_escaped_null(str(arg)) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid null byte")
        if _contains_command_control_chars(executable) or any(_contains_command_control_chars(str(arg)) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid control character")
        runtime_command = _command_path(executable)
        try:
            max_output_bytes = (max_output_chars * 4) + 4096
            returncode, stdout_data, stderr_data = run_process_bounded_output(
                [runtime_command, *cmd[1:]],
                input_bytes,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                env=env,
                label=label,
            )
            if returncode != 0:
                detail = _command_failure_detail(returncode, len(stdout_data), len(stderr_data))
                raise CommandChainError(f"{label} command failed: {detail}")
            try:
                decoded_output = stdout_data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CommandChainError(f"command output is not valid UTF-8: {exc}") from exc
            segment_output = decoded_output.strip()
            if len(segment_output) > max_output_chars:
                raise CommandChainError(f"{label} command output exceeded {max_output_chars} characters")
            if _contains_escaped_null(segment_output):
                raise CommandChainError("command output contains invalid null byte")
            if _contains_command_output_control_chars(segment_output):
                raise CommandChainError("command output contains invalid control character")
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
_CHAIN_SEGMENT_SEPARATOR = "".join(["&", "&"])
