from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass


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
        command_check("xdotool", "xdotool"),
        command_check("xclip", "xclip"),
        command_check("notify-send", "libnotify"),
        command_check("whisper", "python3-openai-whisper or pipx/pip whisper"),
        command_check("whisper-cli", "whisper.cpp"),
    ]


def report() -> dict[str, object]:
    checks = run_checks()
    by_name = {check.name: check for check in checks}
    required_ok = by_name["python3"].ok and (by_name["pw-record"].ok or by_name["parecord"].ok)
    return {
        "ok": required_ok,
        "checks": [asdict(check) for check in checks],
        "notes": [
            "The Cinnamon applet uses Cinnamon's own clipboard API.",
            "Install xdotool for automatic paste or direct typing on Cinnamon X11.",
            "Install xclip or xsel only if you use the backend CLI clipboard insertion without the applet.",
            "ASR can use Automatic, the 'whisper' command, whisper.cpp plus a model path, or a custom command.",
        ],
    }
