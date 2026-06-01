from __future__ import annotations

from typing import Any, Mapping


def _section(payload: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    configured = payload.get("configured")
    if not isinstance(configured, Mapping):
        return {}
    section = configured.get(name)
    return section if isinstance(section, Mapping) else {}


def _warnings(payload: Mapping[str, Any]) -> list[str]:
    configured = payload.get("configured")
    if not isinstance(configured, Mapping):
        return []
    warnings = configured.get("warnings")
    if not isinstance(warnings, list):
        return []
    return [str(item) for item in warnings if str(item).strip()]


def _desktop(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    desktop = payload.get("desktop")
    return desktop if isinstance(desktop, Mapping) else {}


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
    ready = bool(doctor_payload.get("ok"))
    steps: list[dict[str, object]] = []

    desktop = _desktop(doctor_payload)
    if desktop and not bool(desktop.get("cinnamon")):
        _add_step(
            steps,
            "cinnamon-session",
            "Use a Cinnamon session",
            "Speed of Cinnamon is designed for Cinnamon's applet, clipboard, and keybinding APIs.",
        )

    recorder = _section(doctor_payload, "recorder")
    if recorder and not bool(recorder.get("ok")):
        _add_step(
            steps,
            "recorder-tools",
            "Install recorder tools",
            str(recorder.get("detail") or "Install PipeWire, PulseAudio, or ALSA recording tools."),
            ["sudo dnf install -y pipewire-utils pulseaudio-utils alsa-utils"],
        )

    transcriber = _section(doctor_payload, "transcriber")
    if transcriber and not bool(transcriber.get("ok")):
        value = str(transcriber.get("value") or "auto")
        detail = str(transcriber.get("detail") or "Configure a local ASR backend.")
        if value == "command":
            _add_step(
                steps,
                "custom-transcriber",
                "Configure the custom transcriber command",
                detail,
            )
        elif value in {"whisper-cpp", "auto"} and (
            "model not found" in detail.lower() or "model path" in detail.lower()
        ):
            _add_step(
                steps,
                "voice-model",
                "Download or select a whisper.cpp voice model",
                detail,
                ["speed-of-cinnamon download-model tiny.en --json"],
            )
        else:
            _add_step(
                steps,
                "asr-backend",
                "Install or configure a local ASR backend",
                detail.rstrip(".")
                + ". Install a whisper command, install whisper.cpp plus whisper-cli, or configure a custom command. "
                "Then use the applet's Voice model menu or download a starter model.",
                ["speed-of-cinnamon models --json", "speed-of-cinnamon download-model tiny.en --json"],
            )

    output = _section(doctor_payload, "output")
    if output and not bool(output.get("ok")):
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

    postprocessor = _section(doctor_payload, "postprocessor")
    if postprocessor and not bool(postprocessor.get("ok")):
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
