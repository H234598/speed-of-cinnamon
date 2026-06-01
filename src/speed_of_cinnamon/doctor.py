from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def command_check(name: str, package_hint: str = "") -> Check:
    path = shutil.which(name)
    if path:
        return Check(name, True, path)
    hint = f" missing; install {package_hint}" if package_hint else " missing"
    return Check(name, False, hint)


def run_checks() -> list[Check]:
    return [
        command_check("python3", "python3"),
        command_check("pw-record", "pipewire-utils"),
        command_check("parecord", "pulseaudio-utils"),
        command_check("arecord", "alsa-utils"),
        command_check("pactl", "pulseaudio-utils"),
        command_check("xdotool", "xdotool"),
        command_check("xclip", "xclip"),
        command_check("xsel", "xsel"),
        command_check("wl-copy", "wl-clipboard"),
        command_check("wtype", "wtype"),
        command_check("notify-send", "libnotify"),
        command_check("whisper", "python3-openai-whisper or pipx/pip whisper"),
        command_check("whisper-cli", "whisper.cpp"),
        command_check("whisper.cpp", "whisper.cpp"),
    ]


def _ok(checks: Mapping[str, Check], name: str) -> bool:
    return bool(checks.get(name) and checks[name].ok)


def _env_desktop() -> dict[str, object]:
    current_desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    session_type = os.environ.get("XDG_SESSION_TYPE", "")
    desktop_session = os.environ.get("DESKTOP_SESSION", "")
    desktop_names = ":".join([current_desktop, desktop_session]).lower()
    return {
        "current_desktop": current_desktop,
        "session_type": session_type,
        "desktop_session": desktop_session,
        "cinnamon": "cinnamon" in desktop_names,
        "x11": session_type.lower() == "x11",
    }


def _setting(settings: Mapping[str, object], key: str, default: str = "") -> str:
    return str(settings.get(key, default) or "").strip()


def _recorder_status(settings: Mapping[str, object], checks: Mapping[str, Check]) -> dict[str, object]:
    recorder = _setting(settings, "recorder", "auto").lower()
    recorder_names = ("pw-record", "parecord", "arecord")
    if recorder in {"", "auto"}:
        available = [name for name in recorder_names if _ok(checks, name)]
        return {
            "ok": bool(available),
            "value": "auto",
            "detail": (
                "available recorder: " + available[0]
                if available
                else "install pipewire-utils, pulseaudio-utils, or alsa-utils"
            ),
        }
    if recorder not in recorder_names:
        return {"ok": False, "value": recorder, "detail": f"unknown recorder: {recorder}"}
    return {
        "ok": _ok(checks, recorder),
        "value": recorder,
        "detail": checks[recorder].detail if recorder in checks else f"{recorder} missing",
    }


def _transcriber_status(settings: Mapping[str, object], checks: Mapping[str, Check]) -> dict[str, object]:
    transcriber = _setting(settings, "transcriber", "auto").lower().replace("_", "-")
    command_template = _setting(settings, "transcriber-command")
    whisper_model = _setting(settings, "whisper-model")
    whisper_ok = _ok(checks, "whisper")
    whisper_cpp_ok = _ok(checks, "whisper-cli") or _ok(checks, "whisper.cpp")
    model_ok = bool(whisper_model and Path(whisper_model).expanduser().exists())

    if transcriber in {"", "auto"}:
        if command_template:
            return {"ok": True, "value": "auto", "resolved": "command", "detail": "custom command configured"}
        if whisper_ok:
            return {"ok": True, "value": "auto", "resolved": "whisper", "detail": "whisper command available"}
        if whisper_model and whisper_cpp_ok and model_ok:
            return {"ok": True, "value": "auto", "resolved": "whisper-cpp", "detail": "whisper.cpp command and model available"}
        if whisper_model and whisper_cpp_ok:
            return {"ok": False, "value": "auto", "detail": f"whisper.cpp model not found: {whisper_model}"}
        return {
            "ok": False,
            "value": "auto",
            "detail": "install whisper, configure whisper.cpp with a model, or set a custom transcriber command",
        }
    if transcriber == "command":
        return {
            "ok": bool(command_template),
            "value": "command",
            "detail": "custom command configured" if command_template else "custom transcriber command is empty",
        }
    if transcriber == "whisper":
        return {
            "ok": whisper_ok,
            "value": "whisper",
            "detail": checks["whisper"].detail if "whisper" in checks else "whisper command missing",
        }
    if transcriber in {"whisper-cpp", "whisper.cpp"}:
        if not whisper_cpp_ok:
            return {"ok": False, "value": "whisper-cpp", "detail": "whisper.cpp command is missing"}
        if not whisper_model:
            return {"ok": False, "value": "whisper-cpp", "detail": "whisper.cpp model path is empty"}
        return {
            "ok": model_ok,
            "value": "whisper-cpp",
            "detail": "whisper.cpp command and model available" if model_ok else f"whisper.cpp model not found: {whisper_model}",
        }
    return {"ok": False, "value": transcriber, "detail": f"unknown transcriber: {transcriber}"}


def _output_status(
    settings: Mapping[str, object],
    checks: Mapping[str, Check],
    desktop: Mapping[str, object],
    applet: bool = False,
) -> dict[str, object]:
    insert_method = _setting(settings, "insert-method", "clipboard-paste").lower()
    cinnamon_clipboard = applet and bool(desktop.get("cinnamon"))
    x11_paste = bool(desktop.get("x11")) and _ok(checks, "xdotool")
    wayland_paste = _ok(checks, "wtype")
    paste_ok = x11_paste or wayland_paste
    cli_clipboard = _ok(checks, "xclip") or _ok(checks, "xsel") or _ok(checks, "wl-copy")

    if insert_method == "none":
        return {"ok": True, "value": "none", "paste_ok": False, "detail": "text insertion disabled"}
    if insert_method == "clipboard":
        return {
            "ok": cinnamon_clipboard or cli_clipboard,
            "value": "clipboard",
            "paste_ok": False,
            "detail": (
                "Cinnamon clipboard available"
                if cinnamon_clipboard
                else "install xclip, xsel, or wl-clipboard for CLI clipboard output"
            ),
        }
    if insert_method == "clipboard-paste":
        copy_ok = cinnamon_clipboard or cli_clipboard
        ok = copy_ok and (paste_ok or cinnamon_clipboard)
        if cinnamon_clipboard:
            detail = "Cinnamon clipboard copy works"
        else:
            detail = "CLI clipboard helper available" if cli_clipboard else "install xclip, xsel, or wl-clipboard for clipboard output"
        if x11_paste:
            detail += "; xdotool paste works"
        elif wayland_paste:
            detail += "; wtype paste works"
        elif cinnamon_clipboard:
            detail += "; install xdotool for automatic paste on Cinnamon X11"
        else:
            detail += "; install xdotool or wtype for paste"
        return {"ok": ok, "value": "clipboard-paste", "paste_ok": paste_ok, "detail": detail}
    if insert_method == "type":
        return {
            "ok": x11_paste,
            "value": "type",
            "paste_ok": x11_paste,
            "detail": "xdotool direct typing works" if x11_paste else "xdotool on Cinnamon X11 is required for direct typing",
        }
    return {"ok": False, "value": insert_method, "paste_ok": False, "detail": f"unknown insert method: {insert_method}"}


def configured_status(
    settings: Mapping[str, object],
    checks: Mapping[str, Check],
    desktop: Mapping[str, object],
    applet: bool = False,
) -> dict[str, object]:
    recorder = _recorder_status(settings, checks)
    transcriber = _transcriber_status(settings, checks)
    output = _output_status(settings, checks, desktop, applet)
    warnings = []
    if applet and output.get("value") == "clipboard-paste" and output.get("ok") and not output.get("paste_ok"):
        warnings.append("automatic paste is unavailable; Cinnamon clipboard copy still works")
    return {
        "recorder": recorder,
        "transcriber": transcriber,
        "output": output,
        "warnings": warnings,
    }


def report(settings: Mapping[str, object] | None = None, applet: bool = False) -> dict[str, object]:
    checks = run_checks()
    by_name = {check.name: check for check in checks}
    desktop = _env_desktop()
    configured = configured_status(settings or {}, by_name, desktop, applet)
    required_ok = (
        by_name["python3"].ok
        and bool(configured["recorder"]["ok"])
        and bool(configured["transcriber"]["ok"])
        and bool(configured["output"]["ok"])
    )
    return {
        "ok": required_ok,
        "checks": [asdict(check) for check in checks],
        "desktop": desktop,
        "configured": configured,
        "applet": applet,
        "notes": [
            "The Cinnamon applet uses Cinnamon's own clipboard API.",
            "Clipboard copy can work from the applet even when xdotool paste is unavailable.",
            "Install pactl/pulseaudio-utils for input source discovery.",
            "Install xdotool for automatic paste or direct typing on Cinnamon X11.",
            "Install xclip or xsel only if you use the backend CLI clipboard insertion without the applet.",
            "ASR can use Automatic, the 'whisper' command, whisper.cpp plus a model path, or a custom command.",
        ],
    }


def parse_settings_json(value: str) -> dict[str, object]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("settings JSON must be an object")
    return parsed
