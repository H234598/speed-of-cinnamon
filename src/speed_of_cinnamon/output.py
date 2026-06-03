from __future__ import annotations

import json
import shutil
import stat
import subprocess  # nosec B404
import tempfile
import io
import os
import time
from pathlib import Path

from .app_logging import log_event
from .path_safety import assert_no_symlink_ancestors, read_text_without_following_symlinks
from .paths import state_dir


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
MAX_DUPLICATE_TEXT_SECONDS = 2.5
MAX_DUPLICATE_LOCK_SECONDS = 30.0
CLIPBOARD_DEDUP_STATE_FILE = "clipboard-last.json"
CLIPBOARD_DEDUP_LOCK_FILE = ".clipboard-last.lock"

_LAST_CLIPBOARD_TEXT: str = ""
_LAST_CLIPBOARD_METHOD: str | None = None
_LAST_CLIPBOARD_INSERTION: float = 0.0


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
    "codex",
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


def _clipboard_dedup_state_path() -> Path:
    path = state_dir() / CLIPBOARD_DEDUP_STATE_FILE
    assert_no_symlink_ancestors(path, field_name="clipboard dedupe state")
    return path


def _read_clipboard_dedup_state() -> tuple[str, float]:
    trusted, snapshot = _read_trusted_clipboard_dedup_state()
    if not trusted:
        return "", 0.0
    return snapshot


def _read_trusted_clipboard_dedup_state() -> tuple[bool, tuple[str, float]]:
    try:
        path = _clipboard_dedup_state_path()
    except RuntimeError:
        return False, ("", 0.0)
    if not path.exists() and not path.is_symlink():
        return True, ("", 0.0)
    try:
        raw = read_text_without_following_symlinks(path, field_name="clipboard dedupe state")
    except FileNotFoundError:
        return True, ("", 0.0)
    except OSError:
        return False, ("", 0.0)
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return False, ("", 0.0)
    if not isinstance(payload, dict):
        return False, ("", 0.0)
    text_value = payload.get("text")
    at_value = payload.get("at")
    if not isinstance(text_value, str) or text_value is None or isinstance(text_value, bool):
        return False, ("", 0.0)
    if not isinstance(at_value, (int, float)) or isinstance(at_value, bool):
        return False, ("", 0.0)
    if not text_value:
        return False, ("", 0.0)
    return True, (text_value, float(at_value))


def _write_clipboard_dedup_state(text: str, at: float) -> bool:
    if not text:
        return False
    try:
        path = _clipboard_dedup_state_path()
    except RuntimeError:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    fd, temp_name = tempfile.mkstemp(prefix="clipboard-dedupe-", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            try:
                os.fchmod(handle.fileno(), 0o600)
            except OSError:
                pass
            json.dump({"text": text, "at": at}, handle)
            handle.write("\n")
        assert_no_symlink_ancestors(path, field_name="clipboard dedupe state")
        os.replace(temp_path, path)
    except (OSError, RuntimeError):
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    return True


def _clipboard_dedup_lock_path() -> Path:
    path = state_dir() / CLIPBOARD_DEDUP_LOCK_FILE
    assert_no_symlink_ancestors(path, field_name="clipboard dedupe lock")
    return path


def _acquire_clipboard_dedup_lock() -> Path | None:
    try:
        path = _clipboard_dedup_lock_path()
    except RuntimeError:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    now = time.time()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            existing = path.lstat()
        except OSError:
            return None
        if not stat.S_ISREG(existing.st_mode):
            return None
        if now - existing.st_mtime > MAX_DUPLICATE_LOCK_SECONDS:
            try:
                path.unlink()
            except OSError:
                return None
            return _acquire_clipboard_dedup_lock()
        return None
    except OSError:
        return None
    try:
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            path.unlink()
        except OSError:
            pass
        return None
    try:
        os.close(fd)
    except OSError:
        pass
    return path


def _release_clipboard_dedup_lock(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _normalize_clipboard_text(text: str) -> str:
    if text == "":
        return ""
    cleaned = " ".join(text.strip().split())
    return cleaned or text


def _record_clipboard_insertion(text: str, method: str) -> bool:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION
    cleaned = _normalize_clipboard_text(text)
    if not cleaned:
        _LAST_CLIPBOARD_TEXT = cleaned
        _LAST_CLIPBOARD_INSERTION = time.monotonic()
        _LAST_CLIPBOARD_METHOD = method
        return True
    now = time.time()
    now_monotonic = time.monotonic()
    if not _write_clipboard_dedup_state(cleaned, now):
        return False
    _LAST_CLIPBOARD_TEXT = cleaned
    _LAST_CLIPBOARD_INSERTION = now_monotonic
    _LAST_CLIPBOARD_METHOD = method
    return True


def _clear_clipboard_dedup_state() -> None:
    try:
        path = _clipboard_dedup_state_path()
    except RuntimeError:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _restore_clipboard_dedup_state(snapshot: tuple[str, float]) -> None:
    text, at = snapshot
    if text:
        if not _write_clipboard_dedup_state(text, at):
            _clear_clipboard_dedup_state()
        return
    _clear_clipboard_dedup_state()


def _clipboard_insertion_snapshot() -> tuple[str, str, float]:
    return _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION


def _restore_clipboard_insertion_snapshot(snapshot: tuple[str, str, float]) -> None:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION
    _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION = snapshot


def _reserve_clipboard_insertion_memory(text: str, method: str) -> tuple[str, str, float] | None:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION
    cleaned = _normalize_clipboard_text(text)
    if not cleaned and text != "":
        return None
    snapshot = _clipboard_insertion_snapshot()
    _LAST_CLIPBOARD_TEXT = cleaned
    _LAST_CLIPBOARD_METHOD = method
    _LAST_CLIPBOARD_INSERTION = time.monotonic()
    return snapshot


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
    resolved_command: str | None = None,
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
    runtime_command = resolved_command or _command_path(command)

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


def _run_stdout(
    argv: list[str] | tuple[str, ...],
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    resolved_command: str | None = None,
) -> str:
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

    runtime_command = resolved_command or _command_path(command)
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


def _run_stdout_raw(
    argv: list[str] | tuple[str, ...],
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    resolved_command: str | None = None,
) -> str | None:
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
    runtime_command = resolved_command or _command_path(command)
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
        return None
    if proc.returncode != 0:
        return None
    output = proc.stdout or b""
    error_output = proc.stderr or b""
    if len(output) > MAX_OUTPUT_CHARS or len(error_output) > MAX_OUTPUT_CHARS:
        return None
    try:
        text = output.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if _contains_escaped_null(text):
        return None
    return text


def _looks_like_terminal(value: str) -> bool:
    normalized = str(value or "").lower()
    return any(marker in normalized for marker in TERMINAL_WINDOW_MARKERS)


def _active_window_paste_key(*, xdotool_available: bool | None = None, xdotool_command: str | None = None) -> str:
    if xdotool_available is None:
        if xdotool_command is None:
            xdotool_command = _which("xdotool")
        xdotool_available = bool(xdotool_command)
    if not xdotool_available:
        return "ctrl+v"
    runtime_command = xdotool_command or _command_path("xdotool")
    window_id = _run_stdout(
        ["xdotool", "getactivewindow"],
        timeout=MAX_PASTE_TIMEOUT_SECONDS,
        resolved_command=runtime_command,
    )
    if not window_id:
        return "ctrl+v"
    window_class = _run_stdout(
        ["xdotool", "getwindowclassname", window_id],
        timeout=MAX_PASTE_TIMEOUT_SECONDS,
        resolved_command=runtime_command,
    )
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


def _read_text_clipboard() -> str | None:
    xclip = _which("xclip")
    if xclip:
        text = _run_stdout(["xclip", "-selection", "clipboard", "-out"], resolved_command=xclip)
        return text or None
    xsel = _which("xsel")
    if xsel:
        text = _run_stdout(["xsel", "--clipboard", "--output"], resolved_command=xsel)
        return text or None
    wl_paste = _which("wl-paste")
    if wl_paste:
        text = _run_stdout(["wl-paste"], resolved_command=wl_paste)
        return text or None
    return None


def _read_text_clipboard_snapshot() -> tuple[bool, str]:
    xclip = _which("xclip")
    if xclip:
        text = _run_stdout_raw(["xclip", "-selection", "clipboard", "-out"], resolved_command=xclip)
        return (text is not None), text or ""
    xsel = _which("xsel")
    if xsel:
        text = _run_stdout_raw(["xsel", "--clipboard", "--output"], resolved_command=xsel)
        return (text is not None), text or ""
    wl_paste = _which("wl-paste")
    if wl_paste:
        text = _run_stdout_raw(["wl-paste"], resolved_command=wl_paste)
        return (text is not None), text or ""
    return False, ""


def _clipboard_targets_contain_non_text_payload(targets: str) -> bool:
    ignored = {"targets", "multiple", "timestamp", "save_targets"}
    text_targets = {
        "compound_text",
        "text/plain; charset=utf-8",
        "text/plain; charset=utf8",
        "text",
        "string",
        "utf8_string",
        "text/plain",
        "text/plain;charset=utf-8",
        "text/plain;charset=utf8",
    }
    saw_text_target = False
    for line in str(targets or "").splitlines():
        target = line.strip().lower()
        if not target or target in ignored:
            continue
        if target in text_targets:
            saw_text_target = True
            continue
        return True
    return not saw_text_target


def _clipboard_has_non_text_payload() -> bool:
    xclip = _which("xclip")
    if xclip:
        targets = _run_stdout(["xclip", "-selection", "clipboard", "-t", "TARGETS", "-out"], resolved_command=xclip)
        return _clipboard_targets_contain_non_text_payload(targets)
    xsel = _which("xsel")
    if xsel:
        targets = _run_stdout(["xsel", "--clipboard", "--output", "--target", "TARGETS"], resolved_command=xsel)
        return _clipboard_targets_contain_non_text_payload(targets)
    wl_paste = _which("wl-paste")
    if wl_paste:
        targets = _run_stdout(["wl-paste", "--list-types"], resolved_command=wl_paste)
        return _clipboard_targets_contain_non_text_payload(targets)
    return True


def paste_from_clipboard() -> None:
    xdotool_error: OutputError | None = None
    xdotool = _which("xdotool")
    if xdotool:
        paste_key = _active_window_paste_key(xdotool_available=True, xdotool_command=xdotool)
        try:
            _run_with_input(
                ["xdotool", "key", "--clearmodifiers", paste_key],
                "",
                timeout=MAX_PASTE_TIMEOUT_SECONDS,
                resolved_command=xdotool,
            )
            return
        except OutputError as exc:
            xdotool_error = exc
    wtype = _which("wtype")
    if wtype:
        if xdotool_error is not None:
            log_event("warning", "clipboard_paste_xdotool_failed_falling_back_to_wtype", error=str(xdotool_error))
        _run_with_input(
            ["wtype", "-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"],
            "",
            timeout=MAX_PASTE_TIMEOUT_SECONDS,
            resolved_command=wtype,
        )
        return
    if xdotool_error is not None:
        raise xdotool_error
    raise OutputError("no keyboard helper found; install xdotool or wtype")


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


def _clipboard_dedup_state_is_untrusted() -> bool:
    trusted, _snapshot = _read_trusted_clipboard_dedup_state()
    return not trusted


def _should_skip_clipboard_duplicate(
    text: str,
    method: str,
    *,
    persistent_snapshot: tuple[str, float] | None = None,
    persistent_state_trusted: bool | None = None,
) -> bool:
    global _LAST_CLIPBOARD_TEXT, _LAST_CLIPBOARD_METHOD, _LAST_CLIPBOARD_INSERTION
    if not isinstance(text, str) or isinstance(text, bool):
        raise OutputError("text must be text")
    if method not in {"clipboard", "clipboard-paste"}:
        return False
    cleaned = _normalize_clipboard_text(text)
    if not cleaned:
        return False
    now_wall = time.time()
    if persistent_snapshot is None or persistent_state_trusted is None:
        persistent_state_trusted, persistent_snapshot = _read_trusted_clipboard_dedup_state()
    if not persistent_state_trusted:
        return True
    cached_text, cached_at = persistent_snapshot
    if cleaned == cached_text and 0 <= (now_wall - cached_at) <= MAX_DUPLICATE_TEXT_SECONDS:
        return True
    now = time.monotonic()
    if (
        cleaned == _LAST_CLIPBOARD_TEXT
        and method == _LAST_CLIPBOARD_METHOD
        and (now - _LAST_CLIPBOARD_INSERTION) <= MAX_DUPLICATE_TEXT_SECONDS
    ):
        return True
    return False


def _should_skip_clipboard_memory_duplicate(text: str, method: str) -> bool:
    return _should_skip_clipboard_duplicate(
        text,
        method,
        persistent_snapshot=("", 0.0),
        persistent_state_trusted=True,
    )


def _begin_clipboard_insertion(text: str, method: str) -> tuple[Path, tuple[str, float]] | None:
    if _should_skip_clipboard_memory_duplicate(text, method):
        return None
    lock_path = _acquire_clipboard_dedup_lock()
    if lock_path is None:
        return None
    try:
        persistent_state_trusted, persistent_snapshot = _read_trusted_clipboard_dedup_state()
        if _should_skip_clipboard_duplicate(
            text,
            method,
            persistent_snapshot=persistent_snapshot,
            persistent_state_trusted=persistent_state_trusted,
        ):
            _release_clipboard_dedup_lock(lock_path)
            return None
        return lock_path, persistent_snapshot
    except Exception:
        _release_clipboard_dedup_lock(lock_path)
        raise


def insert_text(text: str, method: str, delay_ms: int = 8) -> bool:
    if not isinstance(method, str) or isinstance(method, bool):
        raise OutputError("method must be text")
    method = (method or "clipboard-paste").strip().lower()
    if method == "none":
        return False
    if method == "clipboard":
        insertion = _begin_clipboard_insertion(text, method)
        if insertion is None:
            return False
        lock_path, persistent_snapshot = insertion
        snapshot = _reserve_clipboard_insertion_memory(text, method)
        if snapshot is None:
            _release_clipboard_dedup_lock(lock_path)
            return False
        committed = False
        try:
            if not _record_clipboard_insertion(text, method):
                return False
            set_clipboard(text)
            committed = True
            return True
        finally:
            if not committed:
                _restore_clipboard_insertion_snapshot(snapshot)
                _restore_clipboard_dedup_state(persistent_snapshot)
            _release_clipboard_dedup_lock(lock_path)
    if method == "clipboard-paste":
        insertion = _begin_clipboard_insertion(text, method)
        if insertion is None:
            return False
        lock_path, persistent_snapshot = insertion
        snapshot = _reserve_clipboard_insertion_memory(text, method)
        if snapshot is None:
            _release_clipboard_dedup_lock(lock_path)
            return False
        committed = False
        clipboard_snapshot_available = False
        clipboard_snapshot = ""
        try:
            if not _record_clipboard_insertion(text, method):
                return False
            if _clipboard_has_non_text_payload():
                raise OutputError("refusing to overwrite non-text clipboard for automatic paste")
            clipboard_snapshot_available, clipboard_snapshot = _read_text_clipboard_snapshot()
            if not clipboard_snapshot_available:
                raise OutputError("refusing automatic paste without readable text clipboard snapshot")
            set_clipboard(text)
            paste_from_clipboard()
            committed = True
            return True
        finally:
            rollback_error: OutputError | None = None
            if not committed:
                if clipboard_snapshot_available:
                    try:
                        set_clipboard(clipboard_snapshot)
                    except OutputError as exc:
                        rollback_error = exc
                if rollback_error is None:
                    _restore_clipboard_insertion_snapshot(snapshot)
                    _restore_clipboard_dedup_state(persistent_snapshot)
            _release_clipboard_dedup_lock(lock_path)
            if rollback_error is not None:
                raise OutputError("failed to restore previous clipboard after paste failure") from rollback_error
    if method == "type":
        type_text(text, delay_ms)
        return True
    raise OutputError(f"unknown insert method: {method}")
