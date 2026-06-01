from __future__ import annotations

import shutil
import subprocess


class OutputError(RuntimeError):
    pass


def _run_with_input(argv: list[str], text: str) -> None:
    proc = subprocess.run(argv, input=text, text=True, capture_output=True, timeout=10)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or f"exit code {proc.returncode}"
        raise OutputError(f"{argv[0]} failed: {detail}")


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
    raise OutputError("no clipboard helper found; install xclip or xsel on X11")


def paste_from_clipboard() -> None:
    if shutil.which("xdotool"):
        subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=True, timeout=10)
        return
    if shutil.which("wtype"):
        subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=True, timeout=10)
        return
    raise OutputError("no keyboard helper found; install xdotool on Cinnamon X11")


def type_text(text: str, delay_ms: int) -> None:
    if not shutil.which("xdotool"):
        raise OutputError("xdotool is required for direct typing on Cinnamon X11")
    subprocess.run(
        ["xdotool", "type", "--clearmodifiers", "--delay", str(max(delay_ms, 0)), text],
        check=True,
        timeout=30,
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

