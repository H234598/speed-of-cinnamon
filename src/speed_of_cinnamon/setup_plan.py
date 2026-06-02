from __future__ import annotations

from typing import Any, Mapping

from .transcriber import normalize_backend


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
    return [str(item) for item in warnings if str(item).strip()]


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
    ready = _coerce_plan_bool(doctor_payload, "ok")
    steps: list[dict[str, object]] = []

    configured = _configured(doctor_payload)
    desktop = _desktop(doctor_payload)
    if not _coerce_plan_bool(desktop, "cinnamon"):
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
            str(recorder.get("detail") or "Install PipeWire, PulseAudio, or ALSA recording tools."),
            ["sudo dnf install -y pipewire-utils pulseaudio-utils alsa-utils"],
        )

    transcriber = _section(configured, "transcriber")
    if not _coerce_plan_bool(transcriber, "ok"):
        value = normalize_backend(str(transcriber.get("value") or "auto"))
        detail = str(transcriber.get("detail") or "Configure a local ASR backend.")
        if value == "command":
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
        elif value in {"whisper-cpp", "faster-whisper", "auto"} and (
            "model not found" in detail.lower() or "model path" in detail.lower()
        ):
            _add_step(
                steps,
                "voice-model",
                "Download or select a voice model",
                detail,
                ["speed-of-cinnamon download-model ct2-base-int8 --json"],
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
            str(output.get("detail") or "Install clipboard and keyboard helpers for the selected output mode."),
            ["sudo dnf install -y xdotool xclip xsel wl-clipboard"],
        )

    for warning in _warnings(doctor_payload):
        if "automatic paste" in warning or "xdotool" in warning:
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
        detail = str(postprocessor.get("detail") or "Configure text polishing.")
        if value == "ollama":
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
        for command in step["commands"]:
            command_text = str(command)
            if command_text not in seen_commands:
                commands.append(command_text)
                seen_commands.add(command_text)

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
