from __future__ import annotations

import array
import io
import math
import os
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import recordings_dir
from .path_safety import assert_no_symlink_ancestors


class RecorderError(RuntimeError):
    pass


_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_BASE_ENV_KEYS = {
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TEMP",
    "TMP",
    "TERM",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "PULSE_SERVER",
    "PIPEWIRE_REMOTE",
}
_DANGEROUS_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")
_DANGEROUS_ENV_KEYS = {
    "ENV",
    "PWD",
    "OLDPWD",
    "CDPATH",
    "PS4",
    "BASH_XTRACEFD",
    "SHELLOPTS",
    "PROMPT_COMMAND",
    "IFS",
    "PYTHONPATH",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONSTARTUP",
    "PYTHONHOME",
    "BASH_ENV",
}


def _which(command_name: str) -> str | None:
    return shutil.which(command_name, path=_TRUSTED_COMMAND_PATH)


def _is_unsafe_env_var(name: str) -> bool:
    return name in _DANGEROUS_ENV_KEYS or name.startswith(_DANGEROUS_ENV_PREFIXES)


def _coerce_environment_value(name: str) -> str | None:
    if isinstance(name, bool) or not isinstance(name, str):
        return None
    try:
        value = os.environ.__getitem__(name)
    except KeyError:
        return None
    if value is None or isinstance(value, bool) or not isinstance(value, str):
        return None
    return value


def _filtered_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = _coerce_environment_value(key)
        if value is not None:
            env[key] = value
    if base is not None:
        if not isinstance(base, dict):
            raise RecorderError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise RecorderError("environment keys must be text")
            if isinstance(value, bool):
                raise RecorderError("environment values must be text")
            if not isinstance(value, str):
                raise RecorderError("environment base must be a mapping")
            if _is_unsafe_env_var(key):
                raise RecorderError(f"environment key is not allowed: {key}")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


@dataclass(frozen=True)
class RecorderCommand:
    name: str
    argv: list[str]


@dataclass(frozen=True)
class InputSource:
    id: str
    name: str
    description: str
    driver: str = ""
    state: str = ""
    default: bool = False
    monitor: bool = False


MAX_RECORDING_PATH_CHARS = 240
MAX_RECORDING_STEM_CHARS = 120
MAX_RECORDING_SECONDS = 3_600
MAX_RECORDING_INPUT_DEVICE_CHARS = 256
MAX_PACTL_OUTPUT_CHARS = 1_000_000
MAX_PACTL_TIMEOUT_SECONDS = 10
MAX_RECORDING_LEVEL_BYTES = 128_000
WAV_HEADER_SCAN_BYTES = 512
DEFAULT_WAV_DATA_OFFSET = 44


def _contains_escaped_null(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("value must be text")
    lowered = (value or "").lower()
    if "\r" in lowered or "\n" in lowered or "\\r" in lowered or "\\n" in lowered or "\\u000d" in lowered or "\\u000a" in lowered:
        return True
    for char in lowered:
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return True
    return False


@dataclass(frozen=True)
class RecordingLevel:
    ok: bool
    percent: int
    peak: float
    rms: float
    samples: int
    detail: str
    source: str = "recording-file"


def _ensure_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise RecorderError("pactl output must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or max_chars <= 0:
        raise RecorderError("pactl command max chars must be a positive integer")
    file.seek(0)
    try:
        text = file.read(max_chars).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecorderError(f"pactl command output is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise RecorderError("pactl command output contains invalid null byte")
    return text


def _file_size(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise RecorderError("pactl output must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise RecorderError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise RecorderError("command is empty")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise RecorderError("command must be a bare command name without path separators")
    resolved = _which(command_name)
    if not resolved:
        raise RecorderError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _assert_positive_pid(pid: int) -> None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise RecorderError(f"invalid process id: {pid}")


def _assert_valid_input_device(value: str) -> None:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("recording input device must be text")
    if _contains_escaped_null(value):
        raise RecorderError("recording input device contains invalid null byte")
    if len(value) > MAX_RECORDING_INPUT_DEVICE_CHARS:
        raise RecorderError("recording input device name is too long")
    if len(value.encode("utf-8")) > MAX_RECORDING_INPUT_DEVICE_CHARS:
        raise RecorderError(f"recording input device name is too long (max {MAX_RECORDING_INPUT_DEVICE_CHARS} bytes)")


def _assert_valid_recording_seconds(seconds: int) -> int:
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise RecorderError("max recording seconds must be an integer")
    if seconds < 0:
        raise RecorderError("max recording seconds must be non-negative")
    if seconds > MAX_RECORDING_SECONDS:
        raise RecorderError(f"max recording seconds exceeds limit of {MAX_RECORDING_SECONDS}")
    return seconds


def _wav_data_offset(header: bytes) -> int:
    data_index = header.find(b"data")
    if data_index >= 0 and data_index + 8 <= len(header):
        return data_index + 8
    return DEFAULT_WAV_DATA_OFFSET if len(header) >= DEFAULT_WAV_DATA_OFFSET else len(header)


def read_recording_level(audio_path: Path) -> RecordingLevel:
    audio_path = validate_recording_path(audio_path, suffix=".wav")
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RecorderError("secure recording audio file open is not supported on this platform")
    try:
        fd = os.open(audio_path, os.O_RDONLY | nofollow_flag)
    except OSError as exc:
        raise RecorderError(f"recording audio file is not readable: {audio_path}") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size <= DEFAULT_WAV_DATA_OFFSET:
                return RecordingLevel(False, 0, 0.0, 0.0, 0, "waiting for audio")
            header = handle.read(WAV_HEADER_SCAN_BYTES)
            data_offset = _wav_data_offset(header)
            data_bytes = max(0, size - data_offset)
            read_bytes = min(MAX_RECORDING_LEVEL_BYTES, data_bytes)
            read_bytes -= read_bytes % 2
            if read_bytes <= 0:
                return RecordingLevel(False, 0, 0.0, 0.0, 0, "waiting for audio")
            handle.seek(size - read_bytes)
            raw = handle.read(read_bytes)
    except OSError as exc:
        raise RecorderError(f"recording audio file is not readable: {audio_path}") from exc

    raw = raw[: len(raw) - (len(raw) % 2)]
    if len(raw) < 2:
        return RecordingLevel(False, 0, 0.0, 0.0, 0, "waiting for audio")

    samples = array.array("h")
    samples.frombytes(raw)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return RecordingLevel(False, 0, 0.0, 0.0, 0, "waiting for audio")

    peak_sample = max(abs(sample) for sample in samples)
    rms_sample = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    peak = min(1.0, peak_sample / 32768.0)
    rms = min(1.0, rms_sample / 32768.0)
    return RecordingLevel(
        True,
        int(round(peak * 100)),
        round(peak, 4),
        round(rms, 4),
        len(samples),
        "audio detected" if peak_sample > 0 else "silence",
    )


def validate_recording_path(path: Path, *, suffix: str, require_recordings_dir: bool = False) -> Path:
    if not isinstance(path, Path):
        raise RecorderError("recording artifact path must be a path")
    if not isinstance(suffix, str) or isinstance(suffix, bool):
        raise RecorderError("recording artifact suffix must be text")
    if _contains_escaped_null(str(path)):
        raise RecorderError("recording artifact path contains invalid null byte")
    normalized = path.expanduser()
    assert_no_symlink_ancestors(normalized, field_name="recording artifact path")
    normalized = normalized.resolve(strict=False)
    if len(str(normalized)) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact path is too long")
    if len(str(normalized).encode("utf-8")) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError(f"recording artifact path is too long (max {MAX_RECORDING_PATH_CHARS} bytes)")
    if len(normalized.name) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact file name is too long")
    if len(normalized.name.encode("utf-8")) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact file name is too long")
    if len(normalized.stem) > MAX_RECORDING_STEM_CHARS:
        raise RecorderError("recording artifact stem is too long")
    if len(normalized.stem.encode("utf-8")) > MAX_RECORDING_STEM_CHARS:
        raise RecorderError(f"recording artifact stem is too long (max {MAX_RECORDING_STEM_CHARS} bytes)")
    if normalized.suffix.lower() != suffix.lower():
        raise RecorderError(f"recording artifact must use {suffix} extension")
    if require_recordings_dir:
        root = recordings_dir().resolve(strict=False)
        assert_no_symlink_ancestors(root, field_name="recordings directory")
        try:
            normalized.relative_to(root)
        except ValueError as exc:
            raise RecorderError("recording artifact is outside the recordings directory") from exc
    return normalized


def normalize_input_device(value: str) -> str:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("recording input device must be text")
    device = (value or "").strip()
    if device.lower() in {"", "auto", "default", "@default_source@"}:
        return ""
    _assert_valid_input_device(device)
    return device


def choose_recorder(preference: str, audio_path: Path, max_seconds: int, input_device: str = "") -> RecorderCommand:
    if not isinstance(preference, str) or isinstance(preference, bool):
        raise RecorderError("recording preference must be text")
    if not isinstance(input_device, str) or isinstance(input_device, bool):
        raise RecorderError("recording input device must be text")
    audio_path = validate_recording_path(audio_path, suffix=".wav")
    max_seconds = _assert_valid_recording_seconds(max_seconds)
    preference = (preference or "auto").strip().lower()
    target = normalize_input_device(input_device)
    candidates = [preference] if preference != "auto" else ["pw-record", "parecord", "arecord"]
    for candidate in candidates:
        if candidate == "pw-record" and _which("pw-record"):
            argv = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
            if target:
                argv.extend(["--target", target])
            if max_seconds > 0:
                argv.extend(["--sample-count", str(16000 * max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
        if candidate == "parecord" and _which("parecord"):
            argv = ["parecord", "--file-format=wav", "--rate=16000", "--channels=1", str(audio_path)]
            if target:
                argv.insert(-1, f"--device={target}")
            return RecorderCommand(candidate, argv)
        if candidate == "arecord" and _which("arecord"):
            argv = ["arecord", "-f", "S16_LE", "-r", "16000", "-c", "1"]
            if target:
                argv.extend(["--device", target])
            if max_seconds > 0:
                argv.extend(["-d", str(max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
    raise RecorderError("no supported recorder found; install pipewire-utils or alsa-utils")


def parse_pactl_sources(text: str, default_source: str = "", include_monitors: bool = False) -> list[InputSource]:
    if isinstance(text, bool) or not isinstance(text, str):
        raise RecorderError("invalid pactl source output")
    if not isinstance(default_source, str) or not isinstance(include_monitors, bool):
        raise RecorderError("invalid pactl sources request arguments")
    sources: list[InputSource] = []
    current: dict[str, str] | None = None

    def finish() -> None:
        if not current or not current.get("name"):
            return
        name = current["name"]
        monitor = current.get("monitor_of_sink", "n/a") != "n/a" or name.endswith(".monitor")
        if monitor and not include_monitors:
            return
        sources.append(
            InputSource(
                id=current.get("id", ""),
                name=name,
                description=current.get("description", name),
                driver=current.get("driver", ""),
                state=current.get("state", ""),
                default=name == default_source,
                monitor=monitor,
            )
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("Source #"):
            finish()
            current = {"id": line.removeprefix("Source #").strip()}
            continue
        if current is None or ": " not in line:
            continue
        key, value = line.split(": ", 1)
        normalized_key = key.strip().lower().replace(" ", "_")
        if normalized_key in {"state", "name", "description", "driver", "monitor_of_sink"}:
            current[normalized_key] = value.strip()
    finish()
    return sources


def _run_pactl_command(command: list[str] | tuple[str, ...], *, required: bool) -> str:
    if not isinstance(command, (list, tuple)) or any(not isinstance(item, str) for item in command) or not isinstance(required, bool):
        raise RecorderError("invalid pactl command")
    if not command:
        raise RecorderError("empty pactl command is not allowed")
    pactl = command[0].strip()
    if not pactl:
        raise RecorderError("empty pactl executable is not allowed")
    if _contains_escaped_null(pactl) or any(_contains_escaped_null(arg) for arg in command[1:]):
        raise RecorderError("pactl command contains invalid null byte")
    if _contains_http_header_control_chars(pactl) or any(
        _contains_http_header_control_chars(arg) for arg in command[1:]
    ):
        raise RecorderError("pactl command contains invalid control character")
    runtime_command = [_command_path(pactl), *command[1:]]
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(  # nosec B603
                    args=runtime_command,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=MAX_PACTL_TIMEOUT_SECONDS,
                    shell=False,
                    env=_filtered_environment(),
                )
            except FileNotFoundError as exc:
                raise RecorderError(f"{pactl} command not found") from exc

            if _file_size(stdout_file) > MAX_PACTL_OUTPUT_CHARS:
                raise RecorderError(f"pactl command output exceeded {MAX_PACTL_OUTPUT_CHARS} bytes")

            if proc.returncode != 0:
                if not required:
                    return ""
                stderr = _ensure_file_head(stderr_file, 2048).strip()
                stdout = _ensure_file_head(stdout_file, 2048).strip()
                raise RecorderError(stderr or stdout or f"pactl failed: {command}")

            return _ensure_file_head(stdout_file, MAX_PACTL_OUTPUT_CHARS).strip()
    except subprocess.TimeoutExpired as exc:
        raise RecorderError(f"pactl command timed out after {MAX_PACTL_TIMEOUT_SECONDS}s: {command}") from exc
    except OSError as exc:
        raise RecorderError(f"pactl command failed: {exc}") from exc


def list_input_sources(include_monitors: bool = False) -> list[InputSource]:
    if not _which("pactl"):
        raise RecorderError("pactl is required to list input sources; install pulseaudio-utils")

    default_source = _run_pactl_command(["pactl", "get-default-source"], required=False)
    proc_output = _run_pactl_command(["pactl", "list", "sources"], required=True)
    return parse_pactl_sources(proc_output, default_source, include_monitors)


def _run_kill(command: list[str] | tuple[str, ...], *, check_exit: bool) -> None:
    if not isinstance(command, (list, tuple)) or any(not isinstance(item, str) for item in command) or not isinstance(check_exit, bool):
        raise RecorderError("invalid kill command")
    if not command:
        raise RecorderError("empty kill command is not allowed")
    kill_command = command[0].strip()
    if not kill_command:
        raise RecorderError("empty kill executable is not allowed")
    if _contains_escaped_null(kill_command) or any(_contains_escaped_null(arg) for arg in command[1:]):
        raise RecorderError("kill command contains invalid null byte")
    if _contains_http_header_control_chars(kill_command) or any(
        _contains_http_header_control_chars(arg) for arg in command[1:]
    ):
        raise RecorderError("kill command contains invalid control character")
    runtime_command = [_command_path(kill_command), *command[1:]]
    try:
        subprocess.run(  # nosec B603
            runtime_command,
            check=check_exit,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
            shell=False,
            env=_filtered_environment(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RecorderError(f"kill command timed out: {runtime_command}") from exc
    except OSError as exc:
        raise RecorderError(f"failed to run kill command {runtime_command}: {exc}") from exc


def _open_recorder_log_file(log_path: Path) -> io.BufferedWriter:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RecorderError("secure log file open is not supported on this platform")
    try:
        fd = os.open(log_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | nofollow_flag, 0o600)
    except OSError as exc:
        raise RecorderError(f"failed to open recorder log file {log_path}: {exc}") from exc
    try:
        return os.fdopen(fd, "ab")
    except OSError as exc:
        os.close(fd)
        raise RecorderError(f"failed to open recorder log file {log_path}: {exc}") from exc


def start_recorder(command: RecorderCommand, log_path: Path) -> subprocess.Popen[bytes]:
    if not isinstance(log_path, Path):
        raise RecorderError("invalid recorder log path")
    if not isinstance(command, RecorderCommand):
        raise RecorderError("invalid recorder command")
    if not command.argv:
        raise RecorderError("recorder command is empty")
    if not all(isinstance(item, str) for item in command.argv):
        raise RecorderError("recorder command arguments must be text")
    if not command.name.strip():
        raise RecorderError("recorder name is required")
    if not command.argv[0].strip():
        raise RecorderError("recorder executable is empty")
    if _contains_escaped_null(command.argv[0]) or any(_contains_escaped_null(arg) for arg in command.argv[1:]):
        raise RecorderError("recorder command contains invalid null byte")
    if _contains_http_header_control_chars(command.argv[0]) or any(
        _contains_http_header_control_chars(arg) for arg in command.argv[1:]
    ):
        raise RecorderError("recorder command contains invalid control character")
    log_path = validate_recording_path(log_path, suffix=".log")
    existed_before = log_path.exists()
    preserved_size = log_path.stat().st_size if existed_before else 0
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = _open_recorder_log_file(log_path)
    try:
        try:
            os.fchmod(log_file.fileno(), 0o600)
        except OSError:
            pass
        runtime_command = [_command_path(command.argv[0]), *command.argv[1:]]
        return subprocess.Popen(
            runtime_command,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
            shell=False,
            env=_filtered_environment(),  # nosec B603
        )
    except OSError as exc:
        try:
            if not existed_before or preserved_size == 0:
                os.unlink(log_path)
        except OSError:
            pass
        raise RecorderError(f"failed to start {command.name}: {exc}") from exc
    finally:
        log_file.close()


def stop_process(pid: int, timeout_seconds: float = 5.0) -> None:
    _assert_positive_pid(pid)
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise RecorderError("timeout_seconds must be numeric")
    if not math.isfinite(timeout_seconds):
        raise RecorderError("timeout_seconds must be finite")
    if timeout_seconds <= 0:
        raise RecorderError("timeout_seconds must be positive")
    _run_kill(["kill", "-INT", str(pid)], check_exit=False)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            _run_kill(["kill", "-0", str(pid)], check_exit=True)
        except subprocess.CalledProcessError:
            return
        except RecorderError:
            raise
        time.sleep(0.1)

    _run_kill(["kill", "-TERM", str(pid)], check_exit=False)

    time.sleep(0.5)
    try:
        _run_kill(["kill", "-0", str(pid)], check_exit=True)
    except subprocess.CalledProcessError:
        return

    try:
        _run_kill(["kill", "-KILL", str(pid)], check_exit=False)
    except RecorderError as exc:
        raise RecorderError(f"failed to stop recorder process {pid}: {exc}") from exc
