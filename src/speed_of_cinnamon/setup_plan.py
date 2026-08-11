from __future__ import annotations

import re
from typing import Any, Mapping

from .app_logging import _sub_with_ignored_projection, sanitize_text
from .transcriber import normalize_backend

MAX_SETUP_DETAIL_CHARS = 800

_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b", re.IGNORECASE),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|password|secret)\s*(?::|=|%3a|%3d)\s*[^\r\n,;]+"
    ),
)


def _sanitize_setup_text(value: object, fallback: str = "") -> str:
    text = str(value if value is not None else fallback)
    for pattern in _SECRET_PATTERNS:
        text = _sub_with_ignored_projection(text, pattern, "[redacted]")
    text = sanitize_text(text, max_chars=MAX_SETUP_DETAIL_CHARS)
    text = "".join(" " if ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F else char for char in text)
    text = " ".join(text.split())
    if not text:
        return fallback
    if len(text) > MAX_SETUP_DETAIL_CHARS:
        return text[: MAX_SETUP_DETAIL_CHARS - 3].rstrip() + "..."
    return text


def _configured(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    configured = payload.get("configured")
    if not isinstance(configured, Mapping):
        raise RuntimeError("configured must be an object")
    return configured


def _section(configured: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    section = configured.get(name)
    if not isinstance(section, Mapping):
        raise RuntimeError(f"{name} must be an object")
    return section


def _warnings(payload: Mapping[str, Any]) -> list[str]:
    configured = payload.get("configured")
    if not isinstance(configured, Mapping):
        return []
    warnings = configured.get("warnings")
    if warnings is None:
        return []
    if not isinstance(warnings, list):
        raise RuntimeError("warnings must be a list")
    filtered: list[str] = []
    for item in warnings:
        warning = _sanitize_setup_text(item)
        if warning:
            filtered.append(warning)
    return filtered


def _coerce_plan_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise RuntimeError(f"{key} must be a boolean")
    return value


def _desktop(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    desktop = payload.get("desktop")
    if not isinstance(desktop, Mapping):
        raise RuntimeError("desktop must be an object")
    return desktop


def _add_step(
    steps: list[dict[str, object]],
    step_id: str,
    title: str,
    detail: str,
    commands: list[str] | None = None,
    optional: bool = False,
) -> None:
    if any(step.get("id") == step_id for step in steps):
        return
    steps.append(
        {
            "id": step_id,
            "title": title,
            "detail": detail,
            "commands": commands or [],
            "optional": optional,
        }
    )


def build_setup_plan(doctor_payload: Mapping[str, Any]) -> dict[str, object]:
    if not isinstance(doctor_payload, Mapping):
        raise RuntimeError("doctor payload must be an object")
    ready = _coerce_plan_bool(doctor_payload, "ok")
    applet = doctor_payload.get("applet", False)
    if not isinstance(applet, bool):
        raise RuntimeError("applet must be a boolean")
    steps: list[dict[str, object]] = []

    configured = _configured(doctor_payload)
    desktop = _desktop(doctor_payload)
    cinnamon = _coerce_plan_bool(desktop, "cinnamon")
    checks = doctor_payload.get("checks")
    if not ready and isinstance(checks, list):
        for check in checks:
            if not isinstance(check, Mapping) or check.get("name") != "python3":
                continue
            if check.get("ok") is False:
                _add_step(
                    steps,
                    "python-runtime",
                    "Install Python 3",
                    _sanitize_setup_text(
                        check.get("detail"),
                        "Python 3 is required to run Speed of Cinnamon.",
                    ),
                    ["sudo dnf install -y python3"],
                )
            break
    if applet and not cinnamon:
        _add_step(
            steps,
            "cinnamon-session",
            "Use a Cinnamon session",
            "Speed of Cinnamon is designed for Cinnamon's applet, clipboard, and keybinding APIs.",
        )

    recorder = _section(configured, "recorder")
    if not _coerce_plan_bool(recorder, "ok"):
        _add_step(
            steps,
            "recorder-tools",
            "Install recorder tools",
            _sanitize_setup_text(
                recorder.get("detail"), "Install PipeWire, PulseAudio, or ALSA recording tools."
            ),
            ["sudo dnf install -y pipewire-utils pulseaudio-utils alsa-utils coreutils"],
        )

    transcriber = _section(configured, "transcriber")
    if not _coerce_plan_bool(transcriber, "ok"):
        value = normalize_backend(str(transcriber.get("value") or "auto"))
        detail = _sanitize_setup_text(transcriber.get("detail"), "Configure a local ASR backend.")
        detail_lower = detail.casefold()
        model_language_mismatch = "model does not support language" in detail_lower or "english-only" in detail_lower
        language_error = any(
            marker in detail_lower
            for marker in (
                "language must",
                "language is too",
                "language contains",
                "simple language code",
            )
        )
        if model_language_mismatch or (
            value in {"whisper-cpp", "faster-whisper", "auto"}
            and ("model not found" in detail_lower or "model path" in detail_lower)
        ):
            resolved_backend = normalize_backend(str(transcriber.get("resolved") or value))
            model_download_command = (
                "speed-of-cinnamon download-model base --json"
                if resolved_backend == "whisper-cpp"
                else "speed-of-cinnamon download-model ct2-base-int8 --json"
            )
            _add_step(
                steps,
                "voice-model",
                "Download or select a voice model",
                detail,
                [model_download_command],
            )
        elif language_error:
            _add_step(steps, "language", "Configure a valid language code", detail)
        elif value == "command":
            _add_step(
                steps,
                "custom-transcriber",
                "Configure the custom transcriber command",
                detail,
            )
        elif value == "openai-compatible":
            _add_step(
                steps,
                "external-api-transcriber",
                "Configure the External API speech model",
                detail,
            )
        else:
            _add_step(
                steps,
                "asr-backend",
                "Install or configure a local ASR backend",
                detail.rstrip(".")
                + ". Install a whisper command, install faster-whisper, install a whisper.cpp CLI such as pwcpp, or configure a custom command. "
                "Then use the applet's Voice model menu or download a starter model.",
                [
                    "python3 -m pip install --user faster-whisper",
                    "sudo dnf install -y python3-pywhispercpp",
                    "speed-of-cinnamon models --json",
                    "speed-of-cinnamon download-model ct2-base-int8 --json",
                    "speed-of-cinnamon download-model tiny --json",
                ],
            )

    output = _section(configured, "output")
    if not _coerce_plan_bool(output, "ok"):
        _add_step(
            steps,
            "output-tools",
            "Install text output helpers",
            _sanitize_setup_text(
                output.get("detail"),
                "Install clipboard and keyboard helpers for the selected output mode.",
            ),
            ["sudo dnf install -y xdotool xclip xsel wl-clipboard"],
        )

    for warning in _warnings(doctor_payload):
        warning_key = warning.casefold()
        if "automatic paste" in warning_key or "xdotool" in warning_key:
            _add_step(
                steps,
                "automatic-paste",
                "Optional: install automatic paste support",
                warning,
                ["sudo dnf install -y xdotool"],
                optional=True,
            )

    postprocessor = _section(configured, "postprocessor")
    if not _coerce_plan_bool(postprocessor, "ok"):
        value = str(postprocessor.get("value") or "")
        detail = _sanitize_setup_text(postprocessor.get("detail"), "Configure text polishing.")
        detail_lower = detail.casefold()
        language_error = any(
            marker in detail_lower
            for marker in (
                "language must",
                "language is too",
                "language contains",
                "simple language code",
            )
        )
        if language_error:
            _add_step(steps, "language", "Configure a valid language code", detail)
        elif value == "ollama":
            _add_step(
                steps,
                "ollama-text-model",
                "Select a local Ollama text model",
                detail + " Use the applet's Text model menu, or disable text polishing.",
                ["speed-of-cinnamon text-models --json"],
            )
        elif value == "openai-compatible":
            _add_step(
                steps,
                "openai-compatible-text-model",
                "Select an OpenAI-compatible text model",
                detail + " Use the applet's Text model menu, or disable text polishing.",
                ["speed-of-cinnamon text-models --backend openai-compatible --json"],
            )
        else:
            _add_step(
                steps,
                "text-polishing",
                "Configure text polishing",
                detail,
            )

    commands: list[str] = []
    seen_commands: set[str] = set()
    for step in steps:
        step_commands = step.get("commands")
        if not isinstance(step_commands, list):
            continue
        for command in step_commands:
            command_text = str(command)
            if command_text not in seen_commands:
                commands.append(command_text)
                seen_commands.add(command_text)

    ready = ready and not any(not bool(step.get("optional", False)) for step in steps)
    summary = "configured pipeline ready" if ready else "setup needed"
    return {
        "ready": ready,
        "summary": summary,
        "steps": steps,
        "commands": commands,
        "text": format_setup_plan(summary, steps),
    }


def format_setup_plan(summary: str, steps: list[dict[str, object]]) -> str:
    lines = ["Speed of Cinnamon setup plan", f"Status: {summary}", ""]
    if not steps:
        lines.append("No required setup steps.")
        return "\n".join(lines)

    for index, step in enumerate(steps, start=1):
        optional = " (optional)" if step.get("optional") else ""
        lines.append(f"{index}. {step['title']}{optional}")
        lines.append(f"   {step['detail']}")
        commands = step.get("commands")
        if isinstance(commands, list) and commands:
            lines.append("   Commands:")
            for command in commands:
                lines.append(f"     {command}")
        lines.append("")
    return "\n".join(lines).rstrip()
