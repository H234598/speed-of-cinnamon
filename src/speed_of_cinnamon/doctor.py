from __future__ import annotations

import json
import math
import os
import re
import shutil
import stat
import sys
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .command_chain import (
    CommandChainError,
    MAX_COMMAND_LENGTH_CHARS,
    _command_path,
    run_process_bounded_output,
    split_command_chain,
)
from .http_safety import has_unsafe_url_characters, is_loopback_hostname
from .insert_methods import normalize_insert_method
from .models import default_ctranslate2_model_path, default_whisper_cpp_model_path, model_backend_for_path, model_supports_language
from .postprocessor import (
    DEFAULT_OPENAI_COMPATIBLE_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_TEXT_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    MAX_OLLAMA_MODEL_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    POSTPROCESS_TEMPLATE_PLACEHOLDER_RE,
    _openai_compatible_model_supports_text_polishing,
    _safe_prompt_language,
)
from .path_safety import assert_no_symlink_ancestors
from .process_priority import _CPU_ONLINE, _parse_cpu_list, _read_affinity_file
from .recorder import MAX_RECORDING_SECONDS
from .transcriber import (
    FASTER_WHISPER_REQUESTED_COMPUTE_TYPE,
    FASTER_WHISPER_REQUESTED_CPU_THREADS,
    FASTER_WHISPER_REQUESTED_NUM_WORKERS,
    MAX_LANGUAGE_CODE_CHARS,
    MAX_TRANSCRIBER_TEXT_CHARS,
    _COMMAND_TEMPLATE_PLACEHOLDER_RE,
    TranscriptionError,
    _local_model_path_kind,
    _validate_ctranslate2_model_tree,
    faster_whisper_runtime_diagnostics,
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


DOCTOR_CHECK_NAMES = frozenset(
    {
        "python3",
        "pw-record",
        "parecord",
        "arecord",
        "pactl",
        "xdotool",
        "xclip",
        "xsel",
        "wl-copy",
        "wtype",
        "notify-send",
        "timeout",
        "whisper",
        "whisper-cli",
        "whisper.cpp",
        "pwcpp",
        "faster-whisper",
    }
)


_FASTER_WHISPER_READY_DETAIL = "python module, CPU int8 runtime, and child worker available"
_CTRANSLATE2_MISSING_DETAIL = "CTranslate2 runtime is missing"
_FASTER_WHISPER_MODULE_MISSING_DETAIL = "faster-whisper module is missing"
_FASTER_WHISPER_WORKER_MISSING_DETAIL = "faster-whisper child worker is missing or unsafe"
_CTRANSLATE2_INT8_UNSUPPORTED_DETAIL = "CTranslate2 CPU int8 compute type is unsupported"
_FASTER_WHISPER_PROBE_TIMEOUT_DETAIL = "faster-whisper runtime probe timed out"
_FASTER_WHISPER_PROBE_FAILED_DETAIL = "faster-whisper runtime probe failed"
_FASTER_WHISPER_PROBE_INCOMPLETE_DETAIL = "faster-whisper runtime probe is incomplete"
_FASTER_WHISPER_SAFE_FAILURE_DETAILS = frozenset(
    {
        _CTRANSLATE2_MISSING_DETAIL,
        _FASTER_WHISPER_MODULE_MISSING_DETAIL,
        _FASTER_WHISPER_WORKER_MISSING_DETAIL,
        _CTRANSLATE2_INT8_UNSUPPORTED_DETAIL,
        _FASTER_WHISPER_PROBE_TIMEOUT_DETAIL,
        _FASTER_WHISPER_PROBE_FAILED_DETAIL,
        _FASTER_WHISPER_PROBE_INCOMPLETE_DETAIL,
    }
)
_GNA_CPU_MODEL_RE = re.compile(r"(?<![A-Za-z0-9])i5-1245u(?![A-Za-z0-9])", re.ASCII | re.IGNORECASE)
_GNA_DRIVER_CANDIDATES = (
    (Path("/sys/module/intel_gna"), Path("/sys/module")),
    (Path("/sys/bus/pci/drivers/intel_gna"), Path("/sys/bus/pci/drivers")),
    (Path("/sys/module/gna"), Path("/sys/module")),
    (Path("/sys/bus/pci/drivers/gna"), Path("/sys/bus/pci/drivers")),
)
_GNA_DEVICE_CANDIDATES = (
    (Path("/dev/intel_gna0"), Path("/dev")),
    (Path("/dev/gna0"), Path("/dev")),
)


def command_check(name: str, package_hint: str = "") -> Check:
    try:
        path = _which(name)
    except Exception:
        return Check(name, False, "check failed")
    if path:
        return Check(name, True, path)
    hint = f" missing; install {package_hint}" if package_hint else " missing"
    return Check(name, False, hint)


def _failed_ctranslate2_diagnostics() -> dict[str, object]:
    return {
        "probe_status": "failed",
        "available": None,
        "version": None,
        "supported_compute_types": None,
        "requested_compute_type": FASTER_WHISPER_REQUESTED_COMPUTE_TYPE,
        "cpu_threads": FASTER_WHISPER_REQUESTED_CPU_THREADS,
        "num_workers": FASTER_WHISPER_REQUESTED_NUM_WORKERS,
        "faster_whisper": {
            "available": None,
            "version": None,
            "worker_available": None,
            "ready": None,
        },
    }


def _ctranslate2_diagnostics() -> dict[str, object]:
    try:
        runtime = faster_whisper_runtime_diagnostics()
        if not isinstance(runtime, Mapping) or set(runtime) != {
            "probe_status",
            "ctranslate2",
            "faster_whisper",
            "worker_available",
        }:
            return _failed_ctranslate2_diagnostics()
        probe_status = runtime["probe_status"]
        ctranslate2 = runtime["ctranslate2"]
        faster_whisper = runtime["faster_whisper"]
        worker_available = runtime["worker_available"]
        if probe_status not in {"ok", "partial", "unavailable", "timeout", "failed"}:
            return _failed_ctranslate2_diagnostics()
        if not isinstance(ctranslate2, Mapping) or set(ctranslate2) != {
            "available",
            "version",
            "supported_compute_types",
        }:
            return _failed_ctranslate2_diagnostics()
        if not isinstance(faster_whisper, Mapping) or set(faster_whisper) != {
            "available",
            "version",
        }:
            return _failed_ctranslate2_diagnostics()
        available = ctranslate2["available"]
        version = ctranslate2["version"]
        compute_types = ctranslate2["supported_compute_types"]
        faster_available = faster_whisper["available"]
        faster_version = faster_whisper["version"]
        if available is not None and type(available) is not bool:
            return _failed_ctranslate2_diagnostics()
        if faster_available is not None and type(faster_available) is not bool:
            return _failed_ctranslate2_diagnostics()
        if worker_available is not None and type(worker_available) is not bool:
            return _failed_ctranslate2_diagnostics()
        if version is not None and not isinstance(version, str):
            return _failed_ctranslate2_diagnostics()
        if faster_version is not None and not isinstance(faster_version, str):
            return _failed_ctranslate2_diagnostics()
        if compute_types is not None and (
            not isinstance(compute_types, list)
            or not all(isinstance(item, str) for item in compute_types)
        ):
            return _failed_ctranslate2_diagnostics()
    except Exception:
        return _failed_ctranslate2_diagnostics()

    if (
        available is False
        or faster_available is False
        or worker_available is False
        or (
            compute_types is not None
            and FASTER_WHISPER_REQUESTED_COMPUTE_TYPE not in compute_types
        )
    ):
        ready = False
    elif (
        available is True
        and faster_available is True
        and worker_available is True
        and compute_types is not None
    ):
        ready = True
    else:
        ready = None
    return {
        "probe_status": probe_status,
        "available": available,
        "version": version,
        "supported_compute_types": compute_types,
        "requested_compute_type": FASTER_WHISPER_REQUESTED_COMPUTE_TYPE,
        "cpu_threads": FASTER_WHISPER_REQUESTED_CPU_THREADS,
        "num_workers": FASTER_WHISPER_REQUESTED_NUM_WORKERS,
        "faster_whisper": {
            "available": faster_available,
            "version": faster_version,
            "worker_available": worker_available,
            "ready": ready,
        },
    }


def _gna_lstat_kind(path: Path) -> str:
    try:
        path_stat = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except (OSError, TypeError, ValueError):
        return "unknown"
    mode = getattr(path_stat, "st_mode", None)
    if isinstance(mode, bool) or not isinstance(mode, int):
        return "unknown"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISCHR(mode):
        return "character"
    return "unknown"


def _gna_fixed_path_detected(
    candidates: tuple[tuple[Path, Path], ...],
    *,
    expected_kind: str,
) -> bool | None:
    if sys.platform != "linux":
        return None
    base_kinds: dict[Path, str] = {}
    for _candidate, base in candidates:
        if base not in base_kinds:
            base_kinds[base] = _gna_lstat_kind(base)
    uncertain = any(kind != "directory" for kind in base_kinds.values())
    for candidate, base in candidates:
        if base_kinds[base] != "directory":
            continue
        kind = _gna_lstat_kind(candidate)
        if kind == expected_kind:
            return True
        if kind != "missing":
            uncertain = True
    return None if uncertain else False


def _gna_hardware_advertised(cpu: Mapping[str, object]) -> bool | None:
    model = cpu.get("model")
    if (
        isinstance(model, bool)
        or not isinstance(model, str)
        or len(model) > MAX_CPU_MODEL_BYTES
        or not model.isascii()
        or not model.isprintable()
    ):
        return None
    return True if _GNA_CPU_MODEL_RE.search(model) is not None else None


def _gna_diagnostics(cpu: Mapping[str, object]) -> dict[str, object]:
    return {
        "hardware_advertised_by_cpu_model": _gna_hardware_advertised(cpu),
        "driver_detected": _gna_fixed_path_detected(
            _GNA_DRIVER_CANDIDATES,
            expected_kind="directory",
        ),
        "device_node_detected": _gna_fixed_path_detected(
            _GNA_DEVICE_CANDIDATES,
            expected_kind="character",
        ),
        "supported_by_soc": False,
        "reason": "upstream software stack discontinued",
    }


def run_checks(
    runtime_diagnostic: Mapping[str, object] | None = None,
) -> list[Check]:
    if runtime_diagnostic is None:
        runtime_diagnostic = _ctranslate2_diagnostics()
    faster_whisper = runtime_diagnostic.get("faster_whisper")
    if not isinstance(faster_whisper, Mapping):
        faster_whisper = {}
    faster_available = faster_whisper.get("available")
    faster_worker_available = faster_whisper.get("worker_available")
    faster_ready = faster_whisper.get("ready") is True
    probe_status = runtime_diagnostic.get("probe_status")
    ctranslate2_available = runtime_diagnostic.get("available")
    supported_compute_types = runtime_diagnostic.get("supported_compute_types")
    if probe_status == "timeout":
        faster_detail = _FASTER_WHISPER_PROBE_TIMEOUT_DETAIL
    elif probe_status == "failed":
        faster_detail = _FASTER_WHISPER_PROBE_FAILED_DETAIL
    elif ctranslate2_available is False:
        faster_detail = _CTRANSLATE2_MISSING_DETAIL
    elif faster_available is False:
        faster_detail = _FASTER_WHISPER_MODULE_MISSING_DETAIL
    elif faster_worker_available is False:
        faster_detail = _FASTER_WHISPER_WORKER_MISSING_DETAIL
    elif (
        isinstance(supported_compute_types, list)
        and FASTER_WHISPER_REQUESTED_COMPUTE_TYPE not in supported_compute_types
    ):
        faster_detail = _CTRANSLATE2_INT8_UNSUPPORTED_DETAIL
    elif faster_ready:
        faster_detail = _FASTER_WHISPER_READY_DETAIL
    else:
        faster_detail = _FASTER_WHISPER_PROBE_INCOMPLETE_DETAIL
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
            faster_ready,
            faster_detail,
        ),
    ]


MAX_SETTINGS_JSON_CHARS = 250_000
MAX_REMOTE_URL_CHARS = 2_048
MAX_DOCTOR_FIELD_CHARS = 512
MAX_AUDIO_PROBE_OUTPUT_BYTES = 64 * 1024
AUDIO_PROBE_TIMEOUT_SECONDS = 3
MAX_AUDIO_PROBE_LINES = 2_048
MAX_AUDIO_IDENTIFIER_CHARS = 256
MAX_CPU_COUNT = 1_024
MAX_CPUINFO_BYTES = 4 * 1024 * 1024
MAX_KERNEL_CONFIG_BYTES = 2 * 1024 * 1024
MAX_CPU_SCALAR_BYTES = 64
MAX_CPU_MODEL_BYTES = 128
_PROC_CPUINFO = Path("/proc/cpuinfo")
_CPU_SYSFS = Path("/sys/devices/system/cpu")
_BOOT_CONFIG_DIR = Path("/boot")
_KERNEL_RELEASE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$", re.ASCII)
_INTEL_CORE_MODEL_RE = re.compile(
    r"^(?:[1-9][0-9]?(?:st|nd|rd|th) Gen )?Intel\(R\) Core\(TM\) "
    r"(?P<product>i[3579]-[0-9]{3,5}[A-Za-z0-9]{0,4}|Ultra [3579] [0-9]{3}[A-Za-z]{0,2})"
    r"(?: CPU)?(?: @ [0-9]{1,2}\.[0-9]{2}GHz)?$",
    re.ASCII,
)
AUDIO_PACKAGE_NAMES = (
    "alsa-sof-firmware",
    "alsa-ucm",
    "alsa-ucm-utils",
    "pipewire",
    "pipewire-utils",
    "wireplumber",
)
_AUDIO_RPM_QUERY = "%{NAME}=%{VERSION}-%{RELEASE}\\n"
_AUDIO_RPM_ARGS = ("-q", "--qf", _AUDIO_RPM_QUERY, *AUDIO_PACKAGE_NAMES)
_AUDIO_PROBE_ARGV = {
    ("lspci", "-Dnnk"),
    ("lsmod",),
    ("rpm", *_AUDIO_RPM_ARGS),
    ("wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"),
}


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


def _run_audio_probe(command: str, *args: str) -> tuple[str, str]:
    requested_argv = (command, *args)
    if requested_argv not in _AUDIO_PROBE_ARGV:
        return "failed", ""
    try:
        executable = _which(command)
        if (
            not executable
            or not os.path.isabs(executable)
            or os.path.basename(executable) != command
            or len(executable) > MAX_DOCTOR_FIELD_CHARS
            or _contains_escaped_null(executable)
            or _contains_http_header_control_chars(executable)
        ):
            return "unavailable", ""
        if command == "wpctl":
            returncode, stdout, _stderr = run_process_bounded_output(
                [executable, *args],
                timeout_seconds=AUDIO_PROBE_TIMEOUT_SECONDS,
                max_output_bytes=MAX_AUDIO_PROBE_OUTPUT_BYTES,
                env={"LANG": "C", "LC_ALL": "C"},
                label=f"audio {command} probe",
                preserve_user_systemd_environment=True,
            )
        else:
            returncode, stdout, _stderr = run_process_bounded_output(
                [executable, *args],
                timeout_seconds=AUDIO_PROBE_TIMEOUT_SECONDS,
                max_output_bytes=MAX_AUDIO_PROBE_OUTPUT_BYTES,
                env={"LANG": "C", "LC_ALL": "C"},
                label=f"audio {command} probe",
            )
        if b"\x00" in stdout or b"\x00" in _stderr:
            return "failed", ""
        output = stdout.decode("utf-8", errors="strict")
        error_output = _stderr.decode("utf-8", errors="strict")
        if any(
            (ord(char) < 0x20 and char not in "\n\r\t")
            or ord(char) == 0x7F
            or 0x80 <= ord(char) <= 0x9F
            for stream in (output, error_output)
            for char in stream
        ):
            return "failed", ""
        if command == "rpm":
            package_output = _canonical_audio_package_probe_output(
                output,
                error_output,
                returncode,
            )
            return ("ok", package_output) if package_output is not None else ("failed", "")
        if returncode != 0:
            return "failed", ""
        return "ok", output.strip()
    except Exception:
        return "failed", ""


def _audio_probe_lines(output: str) -> list[str] | None:
    lines = output.splitlines()
    if len(lines) > MAX_AUDIO_PROBE_LINES:
        return None
    return lines


def _audio_identifier(value: str, *, version: bool = False) -> str:
    normalized = value.strip()
    allowed_punctuation = "._+~^:-" if version else "._:+-"
    if (
        not normalized
        or len(normalized) > MAX_AUDIO_IDENTIFIER_CHARS
        or not normalized.isascii()
        or not all(char.isalnum() or char in allowed_punctuation for char in normalized)
    ):
        return ""
    return normalized


def _canonical_audio_package_probe_output(
    output: str,
    error_output: str,
    returncode: int,
) -> str | None:
    if returncode not in {0, 1}:
        return None
    output_lines = _audio_probe_lines(output)
    error_lines = _audio_probe_lines(error_output)
    if (
        output_lines is None
        or error_lines is None
        or len(output_lines) + len(error_lines) > MAX_AUDIO_PROBE_LINES
    ):
        return None
    states: dict[str, str | None] = {}

    def record_missing(line: str) -> bool:
        prefix = "package "
        suffix = " is not installed"
        if not line.startswith(prefix) or not line.endswith(suffix):
            return False
        name = line[len(prefix) : -len(suffix)]
        if name not in AUDIO_PACKAGE_NAMES or name in states:
            return False
        states[name] = None
        return True

    for line in output_lines:
        name, separator, raw_version = line.partition("=")
        if separator:
            version = _audio_identifier(raw_version, version=True)
            if name not in AUDIO_PACKAGE_NAMES or not version:
                return None
            if name in states:
                if states[name] != version:
                    return None
                continue
            states[name] = version
        elif not record_missing(line):
            return None
    if any(not record_missing(line) for line in error_lines):
        return None
    if set(states) != set(AUDIO_PACKAGE_NAMES):
        return None
    missing = any(version is None for version in states.values())
    if returncode != (1 if missing else 0):
        return None
    return "\n".join(f"{name}={states[name] or ''}" for name in AUDIO_PACKAGE_NAMES)


def _audio_pci_drivers(output: str) -> list[str] | None:
    lines = _audio_probe_lines(output)
    if lines is None:
        return None
    drivers: set[str] = set()
    audio_device = False
    for line in lines:
        if line and not line[0].isspace():
            header = line.casefold()
            audio_device = "audio device" in header or "multimedia audio controller" in header
            continue
        if not audio_device:
            continue
        marker = "kernel driver in use:"
        stripped = line.strip()
        if stripped.casefold().startswith(marker):
            driver = _audio_identifier(stripped[len(marker) :])
            if driver:
                drivers.add(driver)
    return sorted(drivers)


def _audio_loaded_modules(output: str) -> set[str] | None:
    lines = _audio_probe_lines(output)
    if lines is None:
        return None
    modules: set[str] = set()
    for line in lines:
        fields = line.split(None, 1)
        if not fields or fields[0] == "Module":
            continue
        module = _audio_identifier(fields[0])
        if module:
            modules.add(module)
    return modules


def _audio_package_versions(output: str) -> dict[str, str | None] | None:
    lines = _audio_probe_lines(output)
    if lines is None:
        return None
    versions: dict[str, str | None] = {name: None for name in AUDIO_PACKAGE_NAMES}
    seen: set[str] = set()
    for line in lines:
        name, separator, raw_version = line.partition("=")
        if not separator or name not in versions or name in seen:
            return None
        if raw_version:
            version = _audio_identifier(raw_version, version=True)
            if not version:
                return None
            versions[name] = version
        seen.add(name)
    return versions if seen == set(AUDIO_PACKAGE_NAMES) else None


def _pipewire_source_evidence(output: str) -> tuple[str, bool | None] | None:
    lines = _audio_probe_lines(output)
    if lines is None:
        return None
    source = ""
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("node.name = "):
            continue
        raw_name = stripped.removeprefix("node.name = ")
        if len(raw_name) < 2 or raw_name[0] != '"' or raw_name[-1] != '"':
            return None
        candidate = _audio_identifier(raw_name[1:-1])
        if not candidate or (source and source != candidate):
            return None
        source = candidate
    source_class = "unknown"
    if source.startswith("alsa_input."):
        source_class = "alsa"
    elif source.startswith("bluez_input."):
        source_class = "bluetooth"
    elif source:
        source_class = "other"
    folded_source = source.casefold()
    alsa_source = folded_source.removeprefix("alsa_input.")
    internal_source = source_class == "alsa" and (
        alsa_source.startswith("platform-")
        or (alsa_source.startswith("pci-") and "-platform-" in alsa_source)
    )
    source_tokens = folded_source.replace("-", "_").replace(".", "_").replace(":", "_").split("_")
    structured_dmic = internal_source and (
        any(token.startswith("dmic") for token in source_tokens)
        or ("sof_sdw" in folded_source and any(token.startswith("mic") for token in source_tokens))
    )
    return source_class, True if structured_dmic else None


def _audio_diagnostics() -> dict[str, object]:
    pci_status, pci_output = _run_audio_probe("lspci", "-Dnnk")
    pci_drivers = _audio_pci_drivers(pci_output) if pci_status == "ok" else []
    if pci_drivers is None:
        pci_status = "failed"
        pci_drivers = []

    modules_status, modules_output = _run_audio_probe("lsmod")
    loaded_modules = _audio_loaded_modules(modules_output) if modules_status == "ok" else set()
    if loaded_modules is None:
        modules_status = "failed"
        loaded_modules = set()
    sof_modules = sorted(
        module for module in loaded_modules if module.startswith("snd_sof") or "_sof_" in module
    )
    soundwire_modules = sorted(
        module
        for module in loaded_modules
        if module.startswith("soundwire_") or (module.startswith("snd_") and "_sdw" in module)
    )

    packages_status, packages_output = _run_audio_probe("rpm", *_AUDIO_RPM_ARGS)
    packages = _audio_package_versions(packages_output) if packages_status == "ok" else None
    if packages is None:
        if packages_status == "ok":
            packages_status = "failed"
        packages = {name: None for name in AUDIO_PACKAGE_NAMES}

    pipewire_status, pipewire_output = _run_audio_probe("wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@")
    pipewire_evidence = _pipewire_source_evidence(pipewire_output) if pipewire_status == "ok" else None
    if pipewire_evidence is None:
        if pipewire_status == "ok":
            pipewire_status = "failed"
        pipewire_source_class = "unknown"
        dmic_visible = None
    else:
        pipewire_source_class, dmic_visible = pipewire_evidence

    sof_active = any(
        driver.startswith("snd_sof") or driver.startswith("sof-audio-pci-")
        for driver in pci_drivers
    ) or bool(sof_modules)
    soundwire_active = None
    legacy_hda_active = "snd_hda_intel" in pci_drivers
    legacy_hda_warning = bool(
        legacy_hda_active
        and not sof_active
        and dmic_visible is True
    )
    warnings = []
    if legacy_hda_warning:
        warnings.append(
            "Legacy HDA is active on a DSP/DMIC-capable audio path; forcing it can disable the digital microphone."
        )
    return {
        "schema_version": 2,
        "pci_drivers": pci_drivers,
        "sof_modules": sof_modules,
        "soundwire_modules": soundwire_modules,
        "sof_active": sof_active,
        "soundwire_active": soundwire_active,
        "packages": packages,
        "pipewire_source_class": pipewire_source_class,
        "pipewire_source_detected": pipewire_source_class != "unknown",
        "dmic_visible": dmic_visible,
        "legacy_hda_warning": legacy_hda_warning,
        "warnings": warnings,
        "probes": {
            "pci": pci_status,
            "modules": modules_status,
            "packages": packages_status,
            "pipewire": pipewire_status,
        },
    }


def _empty_cpu_diagnostics(probe_status: str) -> dict[str, object]:
    return {
        "probe_status": probe_status,
        "model": None,
        "physical_cores": None,
        "logical_cpus": None,
        "avx2": None,
        "avx_vnni": None,
        "hybrid": None,
        "hfi_cpu_flag": None,
        "hfi_kernel_built_in": None,
        "hfi_runtime_active": None,
        "hfi_available": None,
    }


def _online_cpus() -> frozenset[int] | None:
    value = _read_affinity_file(_CPU_ONLINE, max_bytes=MAX_CPU_SCALAR_BYTES)
    cpus = _parse_cpu_list(value) if value is not None else None
    if cpus is None or len(cpus) > MAX_CPU_COUNT:
        return None
    return cpus


def _cpuinfo_records(
    value: str | None,
    online_cpus: frozenset[int],
) -> dict[int, dict[str, list[str]]] | None:
    if not value:
        return None
    records: dict[int, dict[str, list[str]]] = {}
    for block in value.strip().split("\n\n"):
        if not block:
            return None
        fields: dict[str, list[str]] = {}
        for line in block.splitlines():
            key, separator, raw_value = line.partition(":")
            if not separator:
                return None
            normalized_key = key.strip()
            if normalized_key in {"processor", "model name", "flags"}:
                fields.setdefault(normalized_key, []).append(raw_value.strip())
        processors = fields.get("processor", [])
        if len(processors) != 1:
            return None
        processor = processors[0]
        if (
            not processor
            or not processor.isascii()
            or not processor.isdecimal()
            or len(processor) > 7
        ):
            return None
        cpu = int(processor)
        if str(cpu) != processor or cpu not in online_cpus or cpu in records:
            return None
        records[cpu] = fields
    return records if set(records) == set(online_cpus) else None


def _normalized_cpu_model(records: Mapping[int, Mapping[str, list[str]]]) -> str | None:
    models: set[str] = set()
    for fields in records.values():
        values = fields.get("model name", [])
        if len(values) != 1:
            return None
        model = values[0]
        try:
            encoded = model.encode("ascii")
        except UnicodeEncodeError:
            return None
        if len(encoded) > MAX_CPU_MODEL_BYTES:
            return None
        models.add(model)
    if len(models) != 1:
        return None
    match = _INTEL_CORE_MODEL_RE.fullmatch(models.pop())
    return f"Intel Core {match.group('product')}" if match is not None else None


def _cpu_flags(
    records: Mapping[int, Mapping[str, list[str]]],
) -> dict[int, frozenset[str]] | None:
    flags: dict[int, frozenset[str]] = {}
    for cpu, fields in records.items():
        values = fields.get("flags", [])
        if len(values) != 1:
            return None
        tokens = values[0].split()
        if (
            not tokens
            or len(tokens) != len(set(tokens))
            or any(
                not token.isascii()
                or not token
                or any(not (char.islower() or char.isdecimal() or char == "_") for char in token)
                for token in tokens
            )
        ):
            return None
        flags[cpu] = frozenset(tokens)
    return flags


def _physical_core_count(online_cpus: frozenset[int]) -> int | None:
    sibling_sets: dict[int, frozenset[int]] = {}
    for cpu in online_cpus:
        path = _CPU_SYSFS / f"cpu{cpu}" / "topology" / "thread_siblings_list"
        value = _read_affinity_file(path, max_bytes=MAX_CPU_SCALAR_BYTES)
        parsed = _parse_cpu_list(value) if value is not None else None
        if parsed is None:
            return None
        online_siblings = frozenset(parsed.intersection(online_cpus))
        if cpu not in online_siblings:
            return None
        sibling_sets[cpu] = online_siblings
    for cpu, siblings in sibling_sets.items():
        if any(sibling_sets.get(sibling) != siblings for sibling in siblings):
            return None
    partitions = set(sibling_sets.values())
    if (
        not partitions
        or set().union(*partitions) != set(online_cpus)
        or sum(len(partition) for partition in partitions) != len(online_cpus)
    ):
        return None
    return len(partitions)


def _cpu_hybrid_state(online_cpus: frozenset[int]) -> bool | None:
    core_types: set[str] = set()
    for cpu in online_cpus:
        path = _CPU_SYSFS / f"cpu{cpu}" / "topology" / "core_type"
        value = _read_affinity_file(path, max_bytes=MAX_CPU_SCALAR_BYTES)
        if value is None:
            return None
        if value.endswith("\n"):
            value = value[:-1]
        if (
            not value
            or "\n" in value
            or "\r" in value
            or not value.isascii()
            or not value.isdecimal()
            or value == "0"
            or (len(value) > 1 and value.startswith("0"))
        ):
            return None
        core_types.add(value)
    return len(core_types) >= 2 if core_types else None


def _kernel_hfi_state() -> bool | None:
    try:
        release = os.uname().release
    except (AttributeError, OSError):
        return None
    if not isinstance(release, str) or _KERNEL_RELEASE_RE.fullmatch(release) is None:
        return None
    config = _read_affinity_file(
        _BOOT_CONFIG_DIR / f"config-{release}",
        max_bytes=MAX_KERNEL_CONFIG_BYTES,
    )
    if config is None:
        return None
    records = [
        line
        for line in config.splitlines()
        if "CONFIG_INTEL_HFI_THERMAL" in line
    ]
    if records == ["CONFIG_INTEL_HFI_THERMAL=y"]:
        return True
    if records == ["# CONFIG_INTEL_HFI_THERMAL is not set"]:
        return False
    return None


def _cpu_diagnostics() -> dict[str, object]:
    if sys.platform != "linux":
        return _empty_cpu_diagnostics("unsupported-platform")
    online_cpus = _online_cpus()
    if online_cpus is None:
        return _empty_cpu_diagnostics("unavailable")

    cpuinfo = _read_affinity_file(_PROC_CPUINFO, max_bytes=MAX_CPUINFO_BYTES)
    records = _cpuinfo_records(cpuinfo, online_cpus)
    model = _normalized_cpu_model(records) if records is not None else None
    flags = _cpu_flags(records) if records is not None else None
    avx2 = all("avx2" in cpu_flags for cpu_flags in flags.values()) if flags is not None else None
    avx_vnni = (
        all("avx_vnni" in cpu_flags for cpu_flags in flags.values())
        if flags is not None
        else None
    )
    physical_cores = _physical_core_count(online_cpus)
    hybrid = _cpu_hybrid_state(online_cpus)
    hfi_kernel_built_in = _kernel_hfi_state()
    hfi_cpu_flag = (
        all("hfi" in cpu_flags for cpu_flags in flags.values())
        if flags is not None
        else None
    )
    hfi_runtime_active = None
    hfi_available = (
        False
        if hfi_cpu_flag is False or hfi_kernel_built_in is False
        else None
    )

    if _online_cpus() != online_cpus:
        return _empty_cpu_diagnostics("partial")
    values: dict[str, object] = {
        "model": model,
        "physical_cores": physical_cores,
        "logical_cpus": len(online_cpus),
        "avx2": avx2,
        "avx_vnni": avx_vnni,
        "hybrid": hybrid,
        "hfi_cpu_flag": hfi_cpu_flag,
        "hfi_kernel_built_in": hfi_kernel_built_in,
        "hfi_runtime_active": hfi_runtime_active,
        "hfi_available": hfi_available,
    }
    observable_values = (
        model,
        physical_cores,
        len(online_cpus),
        avx2,
        avx_vnni,
        hybrid,
        hfi_cpu_flag,
        hfi_kernel_built_in,
    )
    return {
        "probe_status": "ok" if all(value is not None for value in observable_values) else "partial",
        **values,
    }


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


def _doctor_command_template_detail(
    command_template: str,
    *,
    field_name: str,
    placeholder_pattern: Any,
) -> str | None:
    try:
        segments = split_command_chain(command_template, field_name)
    except CommandChainError as exc:
        return str(exc)
    unsupported_placeholder_text = placeholder_pattern.sub("", command_template)
    if "{" in unsupported_placeholder_text or "}" in unsupported_placeholder_text:
        return f"{field_name} contains an unsupported placeholder"
    for segment in segments:
        try:
            _command_path(segment[0])
        except (CommandChainError, IndexError) as exc:
            return str(exc) if isinstance(exc, CommandChainError) else f"{field_name} is empty"
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
    probe = raw[: MAX_REMOTE_URL_CHARS + 1].strip(" ")
    if _contains_escaped_null(probe):
        raise ValueError(f"{field_name} contains invalid null byte")
    if has_unsafe_url_characters(probe) or _contains_http_header_control_chars(probe):
        raise ValueError(f"{field_name} contains invalid control character")
    try:
        probe.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} contains invalid UTF-8") from exc
    if len(raw) > MAX_REMOTE_URL_CHARS:
        raise ValueError(f"{field_name} is too large (max {MAX_REMOTE_URL_CHARS} characters)")
    try:
        raw_encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{field_name} contains invalid UTF-8") from exc
    normalized = raw.strip(" ")
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if len(raw_encoded) > MAX_REMOTE_URL_CHARS:
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
    path_segments = tuple(segment for segment in parsed.path.split("/") if segment)
    safe_path = ""
    if path_segments:
        first_segment = path_segments[0]
        version_suffix = first_segment[1:].split(".") if first_segment[:1].lower() == "v" else []
        is_version_prefix = bool(version_suffix) and all(part.isdigit() for part in version_suffix)
        if first_segment.lower() == "api" or is_version_prefix:
            safe_path = f"/{first_segment}"
            if len(path_segments) > 1:
                safe_path += "/..."
    return urllib.parse.urlunparse((parsed.scheme, netloc, safe_path, "", "", ""))


def _recorder_status(settings: Mapping[str, object], checks: Mapping[str, Check]) -> dict[str, object]:
    recorder = _setting(settings, "recorder", "auto").lower()
    input_device = _setting(settings, "input-device", "").strip()
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
    detail = _check_detail(checks, recorder, f"{recorder} missing")
    if recorder == "arecord" and input_device.lower().startswith(("alsa_input.", "bluez_input.")):
        return {
            "ok": False,
            "value": recorder,
            "detail": (
                "arecord requires an ALSA PCM identifier; the configured input source is a "
                "PipeWire/Pulse source. Select Automatic or pw-record, or provide an ALSA PCM identifier"
            ),
        }
    if recorder == "arecord" and not input_device and _ok(checks, recorder):
        detail = (
            f"{detail}; no ALSA PCM input configured; prefer Automatic or pw-record for "
            "PipeWire/Pulse microphones"
        )
    return {
        "ok": _ok(checks, recorder),
        "value": recorder,
        "detail": detail,
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

    def _faster_whisper_problem_detail() -> str:
        faster_whisper_check = checks.get("faster-whisper")
        if (
            isinstance(faster_whisper_check, Check)
            and faster_whisper_check.detail in _FASTER_WHISPER_SAFE_FAILURE_DETAILS
        ):
            return faster_whisper_check.detail
        return _FASTER_WHISPER_PROBE_INCOMPLETE_DETAIL

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
    command_detail = _doctor_command_template_detail(
        command_template,
        field_name="transcriber command",
        placeholder_pattern=_COMMAND_TEMPLATE_PLACEHOLDER_RE,
    ) if command_template else None
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
            local_model_kind = _local_model_path_kind(local_model_path, field_name="voice model path")
            local_model_exists = local_model_kind is not None
            model_backend = model_backend_for_path(local_model_path)
            if local_model_kind == "directory":
                _validate_ctranslate2_model_tree(local_model_path, field_name="voice model path")
            if local_model_kind == "directory" and not model_backend:
                model_backend = "faster-whisper"
            model_ok = local_model_exists and (
                (model_backend == "whisper-cpp" and local_model_kind == "file")
                or (model_backend == "faster-whisper" and local_model_kind == "directory")
                or (not model_backend and local_model_kind in {"file", "directory"})
            )
        except (OSError, ValueError, RuntimeError, TranscriptionError):
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
                return {
                    "ok": False,
                    "value": value,
                    "detail": _faster_whisper_problem_detail(),
                }
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
    insert_method = normalize_insert_method(_setting(settings, "insert-method", "clipboard-paste"))
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
    if insert_method in {"clipboard-paste", "clipboard-paste-submit"}:
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
        return {"ok": ok, "value": insert_method, "paste_ok": paste_ok, "detail": detail}
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
        command_detail = _doctor_command_template_detail(
            command_template,
            field_name="post-process command",
            placeholder_pattern=POSTPROCESS_TEMPLATE_PLACEHOLDER_RE,
        )
        if command_detail:
            return {"ok": False, "value": "command", "detail": command_detail}
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
    if "paste_ok" not in output:
        output = {**output, "paste_ok": False}
    postprocessor = _status_result(
        lambda: _postprocessor_status(settings),
        fallback_value="postprocessor",
    )
    warnings = []
    if (
        applet
        and type(output.get("value")) is str
        and output.get("value") in {"clipboard-paste", "clipboard-paste-submit"}
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
    ctranslate2 = _ctranslate2_diagnostics()
    checks = run_checks(ctranslate2)
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
    audio = _audio_diagnostics()
    cpu = _cpu_diagnostics()
    gna = _gna_diagnostics(cpu)
    return {
        "ok": required_ok,
        "checks": [asdict(check) for check in checks],
        "desktop": desktop,
        "configured": configured,
        "audio": audio,
        "acceleration": {
            "schema_version": 3,
            "cpu": cpu,
            "audio": audio,
            "ctranslate2": ctranslate2,
            "gna": gna,
        },
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


def _contains_http_header_control_chars(value: str, *, allow_newline: bool = False) -> bool:
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
        if allow_newline and codepoint == 0x0A:
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _reject_non_finite_json_number(_value: str) -> object:
    raise ValueError("settings JSON contains non-finite numbers")


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("settings JSON contains non-finite numbers")
    return parsed


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, item in pairs:
        if key in result:
            raise ValueError("settings JSON contains duplicate object keys")
        result[key] = item
    return result


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
    if len(value) > MAX_SETTINGS_JSON_CHARS:
        raise ValueError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} characters)")
    if len(value.encode("utf-8")) > MAX_SETTINGS_JSON_CHARS:
        raise ValueError(f"settings JSON is too large (max {MAX_SETTINGS_JSON_CHARS} bytes)")
    try:
        parsed = json.loads(
            value,
            parse_float=_parse_finite_json_float,
            parse_constant=_reject_non_finite_json_number,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise ValueError(f"settings JSON could not be parsed: {exc}") from exc
    try:
        _validate_json_string_encoding(parsed, field_name="settings JSON")
        _validate_json_string_safety(parsed, field_name="settings JSON")
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


def _validate_json_string_safety(value: object, *, field_name: str, allow_newline: bool = False) -> None:
    if isinstance(value, str):
        if _contains_escaped_null(value):
            raise ValueError(f"{field_name} contains invalid null byte")
        if _contains_http_header_control_chars(value, allow_newline=allow_newline):
            raise ValueError(f"{field_name} contains invalid control character")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_string_safety(item, field_name=field_name)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{field_name} contains invalid object key")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise ValueError(f"{field_name} contains invalid object key")
            child_allows_newline = key in {"personal-context", "vocabulary"}
            _validate_json_string_safety(
                child,
                field_name=f"{field_name} field {key}",
                allow_newline=child_allows_newline,
            )
