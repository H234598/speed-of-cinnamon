from __future__ import annotations

import json
import os
import shutil
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .command_chain import MAX_COMMAND_LENGTH_CHARS
from .http_safety import is_loopback_hostname
from .models import default_ctranslate2_model_path, default_whisper_cpp_model_path, model_backend_for_path, model_supports_language
from .postprocessor import (
    DEFAULT_OPENAI_COMPATIBLE_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    MAX_OLLAMA_MODEL_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    _openai_compatible_model_supports_text_polishing,
    _safe_prompt_language,
)
from .path_safety import assert_no_symlink_ancestors
from .recorder import MAX_RECORDING_SECONDS
from .transcriber import (
    MAX_LANGUAGE_CODE_CHARS,
    MAX_TRANSCRIBER_TEXT_CHARS,
    _validate_ctranslate2_model_tree,
    faster_whisper_available,
    normalize_backend,
)


_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def command_check(name: str, package_hint: str = "") -> Check:
    try:
        path = _which(name)
    except Exception:
        return Check(name, False, "check failed")
    if path:
        return Check(name, True, path)
    hint = f" missing; install {package_hint}" if package_hint else " missing"
    return Check(name, False, hint)


def run_checks() -> list[Check]:
    try:
        faster_available = faster_whisper_available()
    except Exception:
        faster_available = False
        faster_probe_failed = True
    else:
        faster_probe_failed = False
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
        command_check("timeout", "coreutils"),
        command_check("whisper", "python3-openai-whisper or pipx/pip whisper"),
        command_check("whisper-cli", "whisper.cpp"),
        command_check("whisper.cpp", "whisper.cpp"),
        command_check("pwcpp", "python3-pywhispercpp"),
        Check(
            "faster-whisper",
            faster_available,
            "check failed"
            if faster_probe_failed
            else ("python module available" if faster_available else " missing; install faster-whisper"),
        ),
    ]


MAX_SETTINGS_JSON_CHARS = 250_000
MAX_REMOTE_URL_CHARS = 2_048
MAX_DOCTOR_FIELD_CHARS = 512


def _ok(checks: Mapping[str, Check], name: str) -> bool:
    check = checks.get(name)
    if isinstance(check, Check):
        if not isinstance(check.ok, bool):
            raise RuntimeError(f"{name}.ok must be a boolean")
        return check.ok
    return False


def _check_detail(checks: Mapping[str, Check], name: str, fallback: str) -> str:
    check = checks.get(name)
    return check.detail if isinstance(check, Check) else fallback


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
    current_desktop = _coerce_desktop_env("XDG_CURRENT_DESKTOP")
    session_type = _coerce_desktop_env("XDG_SESSION_TYPE")
    desktop_session = _coerce_desktop_env("DESKTOP_SESSION")
    display = _coerce_desktop_env("DISPLAY")
    desktop_names = ":".join([current_desktop, desktop_session]).lower()
    session_is_x11 = session_type.lower() == "x11" or (not session_type and bool(display))
    return {
        "current_desktop": current_desktop,
        "session_type": session_type,
        "desktop_session": desktop_session,
        "cinnamon": "cinnamon" in desktop_names,
        "x11": session_is_x11,
    }


def _coerce_desktop_env(name: str) -> str:
    if isinstance(name, bool) or not isinstance(name, str):
        return ""
    try:
        value = os.environ.__getitem__(name)
    except KeyError:
        return ""
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return ""
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
        return ""
    return _doctor_field_text(value, field_name=name).lower()


def _setting(
    settings: Mapping[str, object],
    key: str,
    default: str = "",
    *,
    limit: bool = True,
) -> str:
    value = settings.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"setting {key} must be text")
    if _contains_http_header_control_chars(value):
        raise ValueError(f"setting {key} contains invalid control character")
    normalized = value.strip()
    return _doctor_field_text(normalized, field_name=f"setting {key}") if limit else normalized


def _doctor_field_text(value: str, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    normalized = value.strip()
    if len(normalized) > MAX_DOCTOR_FIELD_CHARS:
        return normalized[:MAX_DOCTOR_FIELD_CHARS] + "..."
    return normalized


def _doctor_text_limit(value: str, *, field_name: str, max_chars: int) -> str | None:
    if len(value) > max_chars:
        return f"{field_name} is too large (max {max_chars} characters)"
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return f"{field_name} contains invalid UTF-8"
    if encoded_length > max_chars:
        return f"{field_name} is too large (max {max_chars} bytes)"
    return None


def _valid_http_url(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        return False
    try:
        _validate_remote_http_url(value, field_name="remote endpoint URL")
    except ValueError:
        return False
    return True


def _validate_remote_http_url(value: str, *, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{field_name} must be text")
    raw = value or ""
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} contains invalid UTF-8") from exc
    if _contains_escaped_null(raw):
        raise ValueError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(raw):
        raise ValueError(f"{field_name} contains invalid control character")
    normalized = raw.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if len(normalized) > MAX_REMOTE_URL_CHARS:
        raise ValueError(f"{field_name} is too large (max {MAX_REMOTE_URL_CHARS} characters)")
    if len(normalized.encode("utf-8")) > MAX_REMOTE_URL_CHARS:
        raise ValueError(f"{field_name} is too large (max {MAX_REMOTE_URL_CHARS} bytes)")
    try:
        parsed = urllib.parse.urlparse(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must use http:// or https://")
    if not parsed.hostname:
        raise ValueError(f"{field_name} is missing hostname")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise ValueError(f"{field_name} must use https:// unless host is local loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{field_name} has invalid port") from exc
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field_name} must not contain userinfo")
    if parsed.query or parsed.fragment:
        raise ValueError(f"{field_name} must not contain query or fragment")
    return normalized


def _safe_remote_url_display(value: str, *, field_name: str) -> str:
    normalized = _validate_remote_http_url(value, field_name=field_name)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, "", "", "", ""))


def _recorder_status(settings: Mapping[str, object], checks: Mapping[str, Check]) -> dict[str, object]:
    recorder = _setting(settings, "recorder", "auto").lower()
    max_seconds = settings.get("max-seconds", 30)
    if isinstance(max_seconds, bool) or not isinstance(max_seconds, int):
        raise ValueError("setting max-seconds must be an integer")
    if max_seconds < 0 or max_seconds > MAX_RECORDING_SECONDS:
        raise ValueError(f"setting max-seconds must be between 0 and {MAX_RECORDING_SECONDS}")
    recorder_names = ("pw-record", "parecord", "arecord")

    def _available(name: str) -> bool:
        return _ok(checks, name) and not (
            name == "parecord" and max_seconds > 0 and not _ok(checks, "timeout")
        )

    if recorder in {"", "auto"}:
        available = [name for name in recorder_names if _available(name)]
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
    if recorder == "parecord" and max_seconds > 0 and not _ok(checks, "timeout"):
        return {
            "ok": False,
            "value": recorder,
            "detail": "timeout is required to enforce max-seconds with parecord",
        }
    return {
        "ok": _ok(checks, recorder),
        "value": recorder,
        "detail": _check_detail(checks, recorder, f"{recorder} missing"),
    }


def _transcriber_status(settings: Mapping[str, object], checks: Mapping[str, Check]) -> dict[str, object]:
    language = _setting(settings, "language", "en")
    transcriber = _setting(settings, "transcriber", "auto").lower().replace("_", "-")
    command_template = _setting(settings, "transcriber-command", limit=False)
    whisper_model = _setting(settings, "whisper-model", limit=False)
    openai_compatible_model = _setting(settings, "openai-compatible-model", DEFAULT_OPENAI_COMPATIBLE_MODEL, limit=False)
    openai_compatible_url = _setting(settings, "openai-compatible-url", DEFAULT_OPENAI_COMPATIBLE_URL, limit=False)
    whisper_ok = _ok(checks, "whisper")
    whisper_cpp_ok = _ok(checks, "whisper-cli") or _ok(checks, "whisper.cpp") or _ok(checks, "pwcpp")
    faster_whisper_ok = _ok(checks, "faster-whisper")
    transcriber = normalize_backend(transcriber)
    if not language:
        return {"ok": False, "value": transcriber or "auto", "detail": "language must not be empty"}
    try:
        language_bytes = len(language.encode("utf-8"))
    except UnicodeEncodeError:
        return {"ok": False, "value": transcriber or "auto", "detail": "language contains invalid UTF-8"}
    if len(language) > MAX_LANGUAGE_CODE_CHARS:
        return {
            "ok": False,
            "value": transcriber or "auto",
            "detail": f"language is too large (max {MAX_LANGUAGE_CODE_CHARS} characters)",
        }
    if language_bytes > MAX_LANGUAGE_CODE_CHARS:
        return {
            "ok": False,
            "value": transcriber or "auto",
            "detail": f"language is too large (max {MAX_LANGUAGE_CODE_CHARS} bytes)",
        }
    command_detail = _doctor_text_limit(
        command_template,
        field_name="command template",
        max_chars=MAX_TRANSCRIBER_TEXT_CHARS,
    )
    if command_detail:
        return {"ok": False, "value": transcriber or "auto", "detail": command_detail}
    try:
        if transcriber == "auto" and command_template:
            local_model = ""
        elif whisper_model and transcriber in {"auto", "whisper-cpp", "faster-whisper"}:
            local_model = whisper_model
        elif transcriber == "whisper-cpp":
            local_model = default_whisper_cpp_model_path(language)
        elif transcriber == "faster-whisper":
            local_model = default_ctranslate2_model_path(language)
        elif transcriber == "auto" and not command_template:
            local_model = default_ctranslate2_model_path(language) or default_whisper_cpp_model_path(language)
        else:
            local_model = ""
    except (OSError, ValueError, RuntimeError):
        return {
            "ok": False,
            "value": transcriber or "auto",
            "detail": "voice model path is invalid",
        }

    model_backend = ""
    local_model_exists = False
    local_model_kind = ""
    local_model_is_invalid = bool(
        local_model and (_contains_escaped_null(local_model) or _contains_http_header_control_chars(local_model))
    )
    if local_model and not local_model_is_invalid:
        try:
            local_model_path = Path(local_model).expanduser()
            assert_no_symlink_ancestors(local_model_path, field_name="voice model path")
            model_backend = model_backend_for_path(local_model_path)
            local_model_exists = local_model_path.exists()
            if local_model_path.is_file():
                local_model_kind = "file"
            elif local_model_path.is_dir():
                local_model_kind = "directory"
                _validate_ctranslate2_model_tree(local_model_path, field_name="voice model path")
            if local_model_kind == "directory" and not model_backend:
                model_backend = "faster-whisper"
            model_ok = local_model_exists and (
                (model_backend == "whisper-cpp" and local_model_kind == "file")
                or (model_backend == "faster-whisper" and local_model_kind == "directory")
                or (not model_backend and local_model_kind in {"file", "directory"})
            )
        except (OSError, ValueError, RuntimeError):
            return {
                "ok": False,
                "value": transcriber or "auto",
                "detail": "voice model path is invalid",
            }
    else:
        local_model_path = None
        model_ok = False

    local_model_language_ok = bool(not local_model or model_supports_language(local_model, language))

    def _model_problem(value: str, *, explicit_backend: str = "") -> dict[str, object] | None:
        def _problem(detail: str) -> dict[str, object]:
            result: dict[str, object] = {"ok": False, "value": value, "detail": detail}
            if model_backend in {"whisper-cpp", "faster-whisper"}:
                result["resolved"] = model_backend
            return result

        if local_model_is_invalid:
            return _problem("voice model path is invalid")
        if local_model and not local_model_exists:
            return _problem("voice model not found")
        if local_model and not model_ok:
            if model_backend == "whisper-cpp":
                return _problem("whisper.cpp voice model path must be a file")
            if model_backend == "faster-whisper":
                return _problem("faster-whisper voice model path must be a directory")
            return _problem("voice model path is invalid")
        if local_model and not local_model_language_ok:
            if explicit_backend == "whisper-cpp":
                return _problem(
                    f"English-only whisper.cpp model does not support language {language}; use a multilingual model"
                )
            return _problem(f"voice model does not support language {language}; use a compatible model")
        return None

    def _model_backend_status(value: str, expected_backend: str = "") -> dict[str, object]:
        problem = _model_problem(value, explicit_backend=expected_backend)
        if problem is not None:
            return problem
        if not local_model:
            return {"ok": False, "value": value, "detail": "voice model path is empty"}
        backend = (
            "faster-whisper"
            if model_backend == "faster-whisper" or local_model_kind == "directory"
            else "whisper-cpp"
        )
        if backend == "faster-whisper":
            if not faster_whisper_ok:
                return {"ok": False, "value": value, "detail": "faster-whisper is missing"}
            return {
                "ok": True,
                "value": value,
                "resolved": "faster-whisper",
                "detail": "CTranslate2 model and faster-whisper available",
            }
        if not whisper_cpp_ok:
            return {"ok": False, "value": value, "detail": "whisper.cpp command is missing"}
        return {
            "ok": True,
            "value": value,
            "resolved": "whisper-cpp",
            "detail": "whisper.cpp command and model available",
        }

    if transcriber in {"", "auto"}:
        if command_template:
            return {"ok": True, "value": "auto", "resolved": "command", "detail": "custom command configured"}
        if whisper_model and local_model:
            return _model_backend_status("auto")
        if whisper_ok:
            return {"ok": True, "value": "auto", "resolved": "whisper", "detail": "whisper command available"}
        if local_model:
            return _model_backend_status("auto")
        return {
            "ok": False,
            "value": "auto",
            "detail": "install whisper, install faster-whisper, configure whisper.cpp with a model, or set a custom transcriber command",
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
            "detail": _check_detail(checks, "whisper", "whisper command missing"),
        }
    if transcriber == "openai-compatible":
        if not openai_compatible_model:
            return {
                "ok": False,
                "value": "openai-compatible",
                "detail": "OpenAI-compatible speech model is required",
            }
        model_detail = _doctor_text_limit(
            openai_compatible_model,
            field_name="OpenAI-compatible speech model",
            max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        )
        if model_detail:
            return {"ok": False, "value": "openai-compatible", "detail": model_detail}
        try:
            endpoint_display = _safe_remote_url_display(openai_compatible_url, field_name="OpenAI-compatible speech endpoint URL")
        except ValueError as exc:
            return {
                "ok": False,
                "value": "openai-compatible",
                "detail": str(exc),
            }
        return {
            "ok": True,
            "value": "openai-compatible",
            "detail": f"OpenAI-compatible speech endpoint configured at {endpoint_display}",
        }
    if transcriber in {"whisper-cpp", "whisper.cpp"}:
        return _model_backend_status("whisper-cpp", "whisper-cpp")
    if transcriber in {"faster-whisper", "ctranslate2", "ct2"}:
        return _model_backend_status("faster-whisper", "faster-whisper")
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
    # Applet owns Wayland keyboard insertion. Backend output currently has
    # only the verifiable X11 xdotool path.
    wayland_paste = applet and _ok(checks, "wtype")
    paste_ok = x11_paste or wayland_paste
    cli_clipboard = _ok(checks, "xclip") or _ok(checks, "xsel") or _ok(checks, "wl-copy")
    cli_paste_writer = _ok(checks, "xclip") or _ok(checks, "xsel")

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
        copy_ok = cinnamon_clipboard or cli_paste_writer
        ok = copy_ok and (paste_ok or cinnamon_clipboard)
        if cinnamon_clipboard:
            detail = "Cinnamon clipboard copy works"
        elif cli_paste_writer:
            detail = "CLI clipboard helper available"
        else:
            detail = "install xclip or xsel for CLI automatic paste"
        if x11_paste:
            detail += "; xdotool paste works"
        elif wayland_paste:
            detail += "; wtype paste works"
        elif cinnamon_clipboard:
            detail += "; install xdotool for automatic paste on Cinnamon X11"
        elif applet:
            detail += "; install xdotool or wtype for paste"
        else:
            detail += "; install xdotool for CLI automatic paste"
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
    backend = _setting(settings, "post-process-backend", "none").lower().replace("_", "-")
    command_template = _setting(settings, "post-process-command", limit=False)
    language = _setting(settings, "language", "en", limit=False)
    ollama_model = _setting(settings, "ollama-model", limit=False)
    ollama_url = _setting(settings, "ollama-url", "http://127.0.0.1:11434", limit=False)
    openai_compatible_model = _setting(
        settings,
        "openai-compatible-text-model",
        DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL,
        limit=False,
    )
    openai_compatible_url = _setting(settings, "openai-compatible-url", DEFAULT_OPENAI_COMPATIBLE_URL, limit=False)
    simple_language_required = backend in {"ollama", "openai-compatible", "openai", "local-openai"} or (
        backend in {"command", "custom"} and "{language}" in command_template
    )
    if simple_language_required:
        try:
            _safe_prompt_language(language)
        except RuntimeError as exc:
            return {"ok": False, "value": backend, "detail": str(exc)}
    if backend in {"", "none", "off", "disabled"}:
        return {"ok": True, "value": "none", "detail": "text polishing disabled"}
    if backend in {"command", "custom"}:
        command_detail = _doctor_text_limit(
            command_template,
            field_name="post-process command",
            max_chars=MAX_COMMAND_LENGTH_CHARS,
        )
        if command_detail:
            return {"ok": False, "value": "command", "detail": command_detail}
        if not command_template:
            return {
                "ok": False,
                "value": "command",
                "detail": "custom post-process command is empty",
            }
        return {
            "ok": True,
            "value": "command",
            "detail": "custom command configured",
        }
    if backend == "ollama":
        if not ollama_model:
            return {"ok": False, "value": "ollama", "detail": "Ollama model is required"}
        model_detail = _doctor_text_limit(ollama_model, field_name="Ollama model", max_chars=MAX_OLLAMA_MODEL_CHARS)
        if model_detail:
            return {"ok": False, "value": "ollama", "detail": model_detail}
        try:
            endpoint_display = _safe_remote_url_display(ollama_url, field_name="Ollama URL")
        except ValueError as exc:
            return {"ok": False, "value": "ollama", "detail": str(exc)}
        return {
            "ok": True,
            "value": "ollama",
            "detail": f"Ollama configured at {endpoint_display}; ensure the local server is running",
        }
    if backend in {"openai-compatible", "openai", "local-openai"}:
        if not openai_compatible_model:
            return {
                "ok": False,
                "value": "openai-compatible",
                "detail": "OpenAI-compatible text model is required",
            }
        model_detail = _doctor_text_limit(
            openai_compatible_model,
            field_name="OpenAI-compatible text model",
            max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        )
        if model_detail:
            return {"ok": False, "value": "openai-compatible", "detail": model_detail}
        if not _openai_compatible_model_supports_text_polishing(openai_compatible_model):
            return {
                "ok": False,
                "value": "openai-compatible",
                "detail": "OpenAI-compatible model is not allowed for text polishing",
            }
        try:
            endpoint_display = _safe_remote_url_display(openai_compatible_url, field_name="OpenAI-compatible API URL")
        except ValueError as exc:
            return {"ok": False, "value": "openai-compatible", "detail": str(exc)}
        return {
            "ok": True,
            "value": "openai-compatible",
            "detail": (
                f"OpenAI-compatible API configured at {endpoint_display}; "
                "ensure the configured endpoint is reachable"
            ),
        }
    return {"ok": False, "value": backend, "detail": f"unknown post-process backend: {backend}"}


def configured_status(
    settings: Mapping[str, object],
    checks: Mapping[str, Check],
    desktop: Mapping[str, object],
    applet: bool = False,
) -> dict[str, object]:
    if not isinstance(settings, Mapping):
        raise RuntimeError("settings must be an object")
    if not isinstance(checks, Mapping):
        raise RuntimeError("checks must be an object")
    if not isinstance(desktop, Mapping):
        raise RuntimeError("desktop must be an object")

    def _status_result(fn: object, *, fallback_value: str) -> dict[str, object]:
        try:
            return fn()  # type: ignore[misc]
        except ValueError as exc:
            return {"ok": False, "value": fallback_value, "detail": str(exc)}

    applet = _coerce_required_bool(applet, field_name="applet")
    recorder = _status_result(
        lambda: _recorder_status(settings, checks),
        fallback_value="recorder",
    )
    transcriber = _status_result(
        lambda: _transcriber_status(settings, checks),
        fallback_value="transcriber",
    )
    output = _status_result(
        lambda: _output_status(settings, checks, desktop, applet),
        fallback_value="output",
    )
    postprocessor = _status_result(
        lambda: _postprocessor_status(settings),
        fallback_value="postprocessor",
    )
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
    if settings is not None and not isinstance(settings, Mapping):
        raise RuntimeError("settings must be an object")
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
            "ASR can use Automatic, the 'whisper' command, faster-whisper, whisper.cpp plus a model path, or a custom command.",
            "Text polishing can use a custom command, Ollama, or an OpenAI-compatible API.",
        ],
    }


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("settings JSON must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("value must be text")
    lowered = (value or "").lower()
    control_codepoints = tuple(range(0x20)) + (0x7F,) + tuple(range(0x80, 0xA0))
    if any(sequence in lowered for sequence in ("\\a", "\\b", "\\f", "\\n", "\\r", "\\t", "\\v")):
        return True
    if any(f"\\x{codepoint:02x}" in lowered or f"\\u00{codepoint:02x}" in lowered for codepoint in control_codepoints):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def parse_settings_json(value: str) -> dict[str, object]:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("settings JSON must be text")
    if not value:
        return {}
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("settings JSON contains invalid UTF-8") from exc
    if _contains_escaped_null(value):
        raise ValueError("settings JSON contains invalid null byte")
    if _contains_http_header_control_chars(value):
        raise ValueError("settings JSON contains invalid control character")
    if len(value) > MAX_SETTINGS_JSON_CHARS:
        raise ValueError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} characters)")
    if len(value.encode("utf-8")) > MAX_SETTINGS_JSON_CHARS:
        raise ValueError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} bytes)")
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise ValueError(f"settings JSON could not be parsed: {exc}") from exc
    try:
        _validate_json_string_encoding(parsed, field_name="settings JSON")
    except (RecursionError, MemoryError) as exc:
        raise ValueError(f"settings JSON could not be parsed: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("settings JSON must be an object")
    return parsed


def _validate_json_string_encoding(value: object, *, field_name: str) -> None:
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError(f"{field_name} contains invalid UTF-8") from exc
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_string_encoding(item, field_name=field_name)
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError(f"{field_name} contains invalid UTF-8") from exc
            else:
                raise ValueError(f"{field_name} contains invalid object key")
            _validate_json_string_encoding(child, field_name=field_name)
    return
