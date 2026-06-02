from __future__ import annotations

import shutil
import subprocess  # nosec B404
import tempfile
import io
import os
from pathlib import Path

from .path_safety import assert_no_symlink_ancestors


class OutputError(RuntimeError):
    pass


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
MAX_OUTPUT_CHARS = 1_000_000
MAX_INPUT_CHARS = 1_000_000
MAX_ERROR_CHARS = 1_024
MAX_PASTE_TIMEOUT_SECONDS = 10
MAX_TYPE_TIMEOUT_SECONDS = 30
MAX_EXEC_TIMEOUT_SECONDS = 10
MAX_TYPE_DELAY_MS = 10_000


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
            raise OutputError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise OutputError("environment keys must be text")
            if isinstance(value, bool):
                raise OutputError("environment values must be text")
            if not isinstance(value, str):
                raise OutputError("environment base must be a mapping")
            if _is_unsafe_env_var(key):
                raise OutputError(f"environment key is not allowed: {key}")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env
TERMINAL_WINDOW_MARKERS = (
    "alacritty",
    "blackbox",
    "com.mitchellh.ghostty",
    "com.system76.cosmic-term",
    "console",
    "cool-retro-term",
    "cosmic terminal",
    "cosmic-term",
    "foot",
    "gnome-terminal",
    "guake",
    "hyper",
    "kgx",
    "kitty",
    "konsole",
    "lxterminal",
    "mate-terminal",
    "org.gnome.console",
    "org.gnome.terminal",
    "ptyxis",
    "qterminal",
    "rio",
    "rxvt",
    "sakura",
    "tabby",
    "terminator",
    "termius",
    "tilix",
    "tty",
    "urxvt",
    "wezterm",
    "xfce4-terminal",
    "xterm",
    "yakuake",
)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise OutputError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise OutputError("value must be text")
    lowered = (value or "").lower()
    if "\r" in lowered or "\n" in lowered or "\\r" in lowered or "\\n" in lowered or "\\u000d" in lowered or "\\u000a" in lowered:
        return True
    for char in lowered:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return True
    return False


def _filesize(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise OutputError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise OutputError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise OutputError("max_chars must be an integer")
    if max_chars <= 0:
        raise OutputError("max_chars must be positive")

    file.seek(0)
    try:
        text = file.read(max_chars).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OutputError(f"command output is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise OutputError("command output contains invalid null byte")
    return text


def _validate_text_input(text: str) -> bytes:
    if not isinstance(text, str) or isinstance(text, bool):
        raise OutputError("text must be text")
    if _contains_escaped_null(text):
        raise OutputError("command input contains invalid null byte")
    if len(text) > MAX_INPUT_CHARS:
        raise OutputError(f"command input is too large (max {MAX_INPUT_CHARS} characters)")
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_INPUT_CHARS:
        raise OutputError(f"command input is too large (max {MAX_INPUT_CHARS} bytes)")
    return encoded


def _run_with_input(
    argv: list[str] | tuple[str, ...],
    text: str,
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    max_output_chars: int | None = None,
) -> None:
    if not isinstance(argv, (list, tuple)):
        raise OutputError("argv must be a sequence")
    if not all(isinstance(arg, str) for arg in argv):
        raise OutputError("command arguments must be text")
    if not argv:
        raise OutputError("empty command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise OutputError("timeout must be an integer")
    if timeout <= 0:
        raise OutputError("timeout must be positive")
    if max_output_chars is None:
        max_output_chars = MAX_OUTPUT_CHARS
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool):
        raise OutputError("max_output_chars must be an integer")
    if max_output_chars < 0:
        raise OutputError("max_output_chars must be non-negative")
    if max_output_chars > MAX_OUTPUT_CHARS:
        raise OutputError(f"max_output_chars must not exceed {MAX_OUTPUT_CHARS}")

    command = argv[0].strip()
    if not command:
        raise OutputError("command is empty")
    if _contains_escaped_null(command) or any(_contains_escaped_null(arg) for arg in argv[1:]):
        raise OutputError("command argument contains invalid null byte")
    if _contains_http_header_control_chars(command) or any(
        _contains_http_header_control_chars(arg) for arg in argv[1:]
    ):
        raise OutputError("command argument contains invalid control character")
    runtime_command = _command_path(command)

    input_bytes = _validate_text_input(text)

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            proc = subprocess.run(  # nosec B603
                [runtime_command, *argv[1:]],
                input=input_bytes,
                text=False,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                shell=False,
                env=_filtered_environment(),
            )
        except FileNotFoundError as exc:
            raise OutputError(f"{command} is not available") from exc
        except subprocess.TimeoutExpired as exc:
            raise OutputError(f"{command} timed out after {timeout}s") from exc
        except OSError as exc:
            raise OutputError(f"{command} failed to execute: {exc}") from exc

        if _filesize(stdout_file) > max_output_chars:
            raise OutputError(f"{command} produced too much output")
        if _filesize(stderr_file) > max_output_chars:
            raise OutputError(f"{command} produced too much error output")

        if proc.returncode != 0:
            detail = _read_file_head(stderr_file, MAX_ERROR_CHARS).strip() or _read_file_head(stdout_file, MAX_ERROR_CHARS).strip()
            detail = detail or f"exit code {proc.returncode}"
            raise OutputError(f"{command} failed: {detail}")


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise OutputError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise OutputError("command is empty")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise OutputError("command must be a bare command name without path separators")
    resolved = _which(command_name)
    if not resolved:
        raise OutputError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


def _run_stdout(argv: list[str] | tuple[str, ...], *, timeout: int = MAX_EXEC_TIMEOUT_SECONDS) -> str:
    if not isinstance(argv, (list, tuple)):
        raise OutputError("argv must be a sequence")
    if not all(isinstance(item, str) for item in argv):
        raise OutputError("command arguments must be text")
    if not argv:
        raise OutputError("empty command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise OutputError("timeout must be an integer")
    if timeout <= 0:
        raise OutputError("timeout must be positive")

    command = argv[0].strip()
    if not command:
        raise OutputError("command is empty")
    if _contains_escaped_null(command) or any(_contains_escaped_null(arg) for arg in argv[1:]):
        raise OutputError("command argument contains invalid null byte")
    if _contains_http_header_control_chars(command) or any(
        _contains_http_header_control_chars(arg) for arg in argv[1:]
    ):
        raise OutputError("command argument contains invalid control character")

    runtime_command = _command_path(command)
    try:
        proc = subprocess.run(  # nosec B603
            [runtime_command, *argv[1:]],
            input=b"",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            shell=False,
            env=_filtered_environment(),
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if proc.returncode != 0:
        return ""
    output = proc.stdout or b""
    error_output = proc.stderr or b""
    if len(output) > MAX_OUTPUT_CHARS:
        return ""
    if len(error_output) > MAX_OUTPUT_CHARS:
        return ""
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        return ""
    if _contains_escaped_null(text):
        return ""
    return text.strip()


def _looks_like_terminal(value: str) -> bool:
    normalized = str(value or "").lower()
    return any(marker in normalized for marker in TERMINAL_WINDOW_MARKERS)


def _active_window_paste_key() -> str:
    if not _which("xdotool"):
        return "ctrl+v"
    window_id = _run_stdout(["xdotool", "getactivewindow"], timeout=MAX_PASTE_TIMEOUT_SECONDS)
    if not window_id:
        return "ctrl+v"
    window_class = _run_stdout(["xdotool", "getwindowclassname", window_id], timeout=MAX_PASTE_TIMEOUT_SECONDS)
    return "ctrl+shift+v" if _looks_like_terminal(window_class) else "ctrl+v"


def set_clipboard(text: str) -> str:
    if not isinstance(text, str) or isinstance(text, bool):
        raise OutputError("text must be text")
    if _which("xclip"):
        _run_with_input(["xclip", "-selection", "clipboard"], text)
        return "xclip"
    if _which("xsel"):
        _run_with_input(["xsel", "--clipboard", "--input"], text)
        return "xsel"
    if _which("wl-copy"):
        _run_with_input(["wl-copy"], text)
        return "wl-copy"
    raise OutputError("no clipboard helper found; install xclip, xsel, or wl-clipboard")


def paste_from_clipboard() -> None:
    if _which("xdotool"):
        paste_key = _active_window_paste_key()
        _run_with_input(
            ["xdotool", "key", "--clearmodifiers", paste_key],
            "",
            timeout=MAX_PASTE_TIMEOUT_SECONDS,
        )
        return
    if _which("wtype"):
        _run_with_input(
            ["wtype", "-M", "ctrl", "v", "-m", "ctrl"],
            "",
            timeout=MAX_PASTE_TIMEOUT_SECONDS,
        )
        return
    raise OutputError("no keyboard helper found; install xdotool on Cinnamon X11")


def type_text(text: str, delay_ms: int) -> None:
    if not _which("xdotool"):
        raise OutputError("xdotool is required for direct typing on Cinnamon X11")
    if not isinstance(delay_ms, int) or isinstance(delay_ms, bool):
        raise OutputError("typing delay must be an integer")
    _validate_text_input(text)
    if delay_ms < 0:
        delay_ms = 0
    if delay_ms > MAX_TYPE_DELAY_MS:
        raise OutputError(f"typing delay must be at most {MAX_TYPE_DELAY_MS}")
    _run_with_input(
        ["xdotool", "type", "--clearmodifiers", "--delay", str(max(delay_ms, 0)), text],
        "",
        timeout=MAX_TYPE_TIMEOUT_SECONDS,
    )


def insert_text(text: str, method: str, delay_ms: int = 8) -> bool:
    if not isinstance(method, str) or isinstance(method, bool):
        raise OutputError("method must be text")
    method = (method or "clipboard-paste").strip().lower()
    if method == "none":
        return False
    if method == "clipboard":
        set_clipboard(text)
        return True
    if method == "clipboard-paste":
        set_clipboard(text)
        paste_from_clipboard()
        return True
    if method == "type":
        type_text(text, delay_ms)
        return True
    raise OutputError(f"unknown insert method: {method}")
