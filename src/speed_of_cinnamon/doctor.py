from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .models import default_whisper_cpp_model_path, model_supports_language


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
        command_check("pwcpp", "python3-pywhispercpp"),
    ]


MAX_SETTINGS_JSON_CHARS = 250_000


def _ok(checks: Mapping[str, Check], name: str) -> bool:
    check = checks.get(name)
    if isinstance(check, Check):
        if not isinstance(check.ok, bool):
            raise RuntimeError(f"{name}.ok must be a boolean")
        return check.ok
    return False


def _coerce_payload_bool(payload: Mapping[str, Any], key: str) -> bool:
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    raise RuntimeError(f"{key} must be a boolean")


def _coerce_required_bool(value: object, *, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{field_name} must be a boolean")
    return value


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
    value = settings.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"setting {key} must be text")
    return value.strip()


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
    language = _setting(settings, "language", "en")
    transcriber = _setting(settings, "transcriber", "auto").lower().replace("_", "-")
    command_template = _setting(settings, "transcriber-command")
    whisper_model = _setting(settings, "whisper-model")
    if whisper_model:
        local_model = whisper_model
        fallback_model = ""
    else:
        local_model = default_whisper_cpp_model_path(language)
        fallback_model = "" if local_model else default_whisper_cpp_model_path(language)
    incompatible_default_model = bool(
        not whisper_model and not local_model and fallback_model and not model_supports_language(fallback_model, language)
    )
    whisper_ok = _ok(checks, "whisper")
    whisper_cpp_ok = _ok(checks, "whisper-cli") or _ok(checks, "whisper.cpp") or _ok(checks, "pwcpp")
    local_model_is_invalid = bool(local_model and _contains_escaped_null(local_model))
    local_model_language_ok = bool(not local_model or model_supports_language(local_model, language))
    model_ok = False
    if local_model and not local_model_is_invalid:
        try:
            model_ok = bool(Path(local_model).expanduser().exists())
        except ValueError:
            return {
                "ok": False,
                "value": "auto",
                "detail": f"whisper.cpp model path is invalid: {local_model}",
            }

    if transcriber in {"", "auto"}:
        if command_template:
            return {"ok": True, "value": "auto", "resolved": "command", "detail": "custom command configured"}
        if local_model_is_invalid:
            return {"ok": False, "value": "auto", "detail": f"whisper.cpp model path is invalid: {local_model}"}
        if local_model and not local_model_language_ok:
            return {
                "ok": False,
                "value": "auto",
                "detail": f"English-only whisper.cpp model does not support language {language}; use a multilingual model",
            }
        if whisper_ok:
            return {"ok": True, "value": "auto", "resolved": "whisper", "detail": "whisper command available"}
        if incompatible_default_model:
            return {
                "ok": False,
                "value": "auto",
                "detail": f"English-only whisper.cpp model does not support language {language}; download a multilingual model",
            }
        if local_model and whisper_cpp_ok and model_ok:
            return {
                "ok": True,
                "value": "auto",
                "resolved": "whisper-cpp",
                "detail": "whisper.cpp command and model available",
            }
        if local_model and whisper_cpp_ok:
            return {"ok": False, "value": "auto", "detail": f"whisper.cpp model not found: {local_model}"}
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
        if local_model_is_invalid:
            return {"ok": False, "value": "whisper-cpp", "detail": f"whisper.cpp model path is invalid: {local_model}"}
        if local_model and not local_model_language_ok:
            return {
                "ok": False,
                "value": "whisper-cpp",
                "detail": f"English-only whisper.cpp model does not support language {language}; use a multilingual model",
            }
        if not local_model:
            return {"ok": False, "value": "whisper-cpp", "detail": "whisper.cpp model path is empty"}
        return {
            "ok": model_ok,
            "value": "whisper-cpp",
            "detail": (
                "whisper.cpp command and model available"
                if model_ok
                else f"whisper.cpp model not found: {local_model}"
            ),
        }
    return {"ok": False, "value": transcriber, "detail": f"unknown transcriber: {transcriber}"}


def _output_status(
    settings: Mapping[str, object],
    checks: Mapping[str, Check],
    desktop: Mapping[str, object],
    applet: bool = False,
) -> dict[str, object]:
    applet = _coerce_required_bool(applet, field_name="applet")
    insert_method = _setting(settings, "insert-method", "clipboard-paste").lower()
    cinnamon_flag = _coerce_payload_bool(desktop, "cinnamon")
    x11_flag = _coerce_payload_bool(desktop, "x11")
    cinnamon_clipboard = applet and cinnamon_flag
    x11_paste = x11_flag and _ok(checks, "xdotool")
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


def _postprocessor_status(settings: Mapping[str, object]) -> dict[str, object]:
    backend = _setting(settings, "post-process-backend", "command").lower().replace("_", "-")
    command_template = _setting(settings, "post-process-command")
    ollama_model = _setting(settings, "ollama-model")
    ollama_url = _setting(settings, "ollama-url", "http://127.0.0.1:11434")
    openai_compatible_model = _setting(settings, "openai-compatible-model")
    openai_compatible_url = _setting(settings, "openai-compatible-url", "http://127.0.0.1:8000/v1")
    if backend in {"", "none", "off", "disabled"}:
        return {"ok": True, "value": "none", "detail": "text polishing disabled"}
    if backend in {"command", "custom"}:
        return {
            "ok": True,
            "value": "command",
            "detail": "custom command configured" if command_template else "text polishing disabled",
        }
    if backend == "ollama":
        if not ollama_model:
            return {"ok": False, "value": "ollama", "detail": "Ollama model is required"}
        return {
            "ok": True,
            "value": "ollama",
            "detail": f"Ollama configured at {ollama_url}; ensure the local server is running",
        }
    if backend in {"openai-compatible", "openai", "local-openai"}:
        if not openai_compatible_model:
            return {
                "ok": False,
                "value": "openai-compatible",
                "detail": "OpenAI-compatible local model is required",
            }
        return {
            "ok": True,
            "value": "openai-compatible",
            "detail": (
                f"OpenAI-compatible local endpoint configured at {openai_compatible_url}; "
                "ensure vLLM, llama.cpp, LM Studio, or another local server is running"
            ),
        }
    return {"ok": False, "value": backend, "detail": f"unknown post-process backend: {backend}"}


def configured_status(
    settings: Mapping[str, object],
    checks: Mapping[str, Check],
    desktop: Mapping[str, object],
    applet: bool = False,
) -> dict[str, object]:
    applet = _coerce_required_bool(applet, field_name="applet")
    recorder = _recorder_status(settings, checks)
    transcriber = _transcriber_status(settings, checks)
    output = _output_status(settings, checks, desktop, applet)
    postprocessor = _postprocessor_status(settings)
    warnings = []
    if (
        applet
        and output.get("value") == "clipboard-paste"
        and _coerce_payload_bool(output, "ok")
        and not _coerce_payload_bool(output, "paste_ok")
    ):
        warnings.append("automatic paste is unavailable; Cinnamon clipboard copy still works")
    return {
        "recorder": recorder,
        "transcriber": transcriber,
        "output": output,
        "postprocessor": postprocessor,
        "warnings": warnings,
    }


def report(settings: Mapping[str, object] | None = None, applet: bool = False) -> dict[str, object]:
    applet = _coerce_required_bool(applet, field_name="applet")
    checks = run_checks()
    by_name = {check.name: check for check in checks}
    desktop = _env_desktop()
    configured = configured_status(settings or {}, by_name, desktop, applet)
    python_check = by_name.get("python3")
    python_ok = _ok({"python3": python_check}, "python3")
    required_ok = (
        python_ok
        and (not applet or _coerce_payload_bool(desktop, "cinnamon"))
        and _coerce_payload_bool(configured["recorder"], "ok")
        and _coerce_payload_bool(configured["transcriber"], "ok")
        and _coerce_payload_bool(configured["output"], "ok")
        and _coerce_payload_bool(configured["postprocessor"], "ok")
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
            "Text polishing can use a custom command, Ollama, or an OpenAI-compatible local server.",
        ],
    }


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("settings JSON must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def parse_settings_json(value: str) -> dict[str, object]:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("settings JSON must be text")
    if not value:
        return {}
    if _contains_escaped_null(value):
        raise ValueError("settings JSON contains invalid null byte")
    if len(value) > MAX_SETTINGS_JSON_CHARS:
        raise ValueError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} characters)")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("settings JSON must be an object")
    return parsed
