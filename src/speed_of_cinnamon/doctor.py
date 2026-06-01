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
    ]


def report() -> dict[str, object]:
    checks = run_checks()
    required_ok = checks[0].ok and (checks[1].ok or checks[2].ok)
    return {
        "ok": required_ok,
        "checks": [asdict(check) for check in checks],
        "notes": [
            "The Cinnamon applet uses Cinnamon's own clipboard API.",
            "Install xdotool for automatic paste or direct typing on Cinnamon X11.",
            "Install xclip or xsel only if you use the backend CLI clipboard insertion without the applet.",
            "ASR is supplied by the configured transcriber command, or by the 'whisper' command when installed.",
        ],
    }
