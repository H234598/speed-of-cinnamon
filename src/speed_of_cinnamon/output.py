from __future__ import annotations

import shutil
import subprocess
import tempfile
import io


class OutputError(RuntimeError):
    pass


MAX_OUTPUT_CHARS = 1_000_000
MAX_INPUT_CHARS = 1_000_000
MAX_ERROR_CHARS = 1_024
MAX_PASTE_TIMEOUT_SECONDS = 10
MAX_TYPE_TIMEOUT_SECONDS = 30
MAX_EXEC_TIMEOUT_SECONDS = 10


def _filesize(file: io.BufferedRandom) -> int:
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    file.seek(0)
    return file.read(max_chars).decode("utf-8", errors="replace")


def _validate_text_input(text: str) -> bytes:
    if "\x00" in text:
        raise OutputError("command input contains invalid null byte")
    if len(text) > MAX_INPUT_CHARS:
        raise OutputError(f"command input is too large (max {MAX_INPUT_CHARS} characters)")
    return text.encode("utf-8")


def _run_with_input(
    argv: list[str],
    text: str,
    *,
    timeout: int = MAX_EXEC_TIMEOUT_SECONDS,
    max_output_chars: int | None = None,
) -> None:
    if not argv:
        raise OutputError("empty command is not allowed")
    if timeout <= 0:
        raise OutputError("timeout must be positive")
    if max_output_chars is None:
        max_output_chars = MAX_OUTPUT_CHARS
    if max_output_chars < 0:
        raise OutputError("max_output_chars must be non-negative")

    command = argv[0].strip()
    if not command:
        raise OutputError("command is empty")

    input_bytes = _validate_text_input(text)

    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        try:
            proc = subprocess.run(
                [command, *argv[1:]],
                input=input_bytes,
                text=False,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
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


def set_clipboard(text: str) -> str:
    if shutil.which("xclip"):
        _run_with_input(["xclip", "-selection", "clipboard"], text)
        return "xclip"
    if shutil.which("xsel"):
        _run_with_input(["xsel", "--clipboard", "--input"], text)
        return "xsel"
    if shutil.which("wl-copy"):
        _run_with_input(["wl-copy"], text)
        return "wl-copy"
    raise OutputError("no clipboard helper found; install xclip, xsel, or wl-clipboard")


def paste_from_clipboard() -> None:
    if shutil.which("xdotool"):
        _run_with_input(["xdotool", "key", "--clearmodifiers", "ctrl+v"], "", timeout=MAX_PASTE_TIMEOUT_SECONDS)
        return
    if shutil.which("wtype"):
        _run_with_input(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], "", timeout=MAX_PASTE_TIMEOUT_SECONDS)
        return
    raise OutputError("no keyboard helper found; install xdotool on Cinnamon X11")


def type_text(text: str, delay_ms: int) -> None:
    if not shutil.which("xdotool"):
        raise OutputError("xdotool is required for direct typing on Cinnamon X11")
    _validate_text_input(text)
    _run_with_input(
        ["xdotool", "type", "--clearmodifiers", "--delay", str(max(delay_ms, 0)), text],
        "",
        timeout=MAX_TYPE_TIMEOUT_SECONDS,
    )


def insert_text(text: str, method: str, delay_ms: int = 8) -> bool:
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
