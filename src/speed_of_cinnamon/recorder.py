from __future__ import annotations

import array
import io
import math
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .paths import recordings_dir


class RecorderError(RuntimeError):
    pass


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
    try:
        size = audio_path.stat().st_size
    except OSError as exc:
        raise RecorderError(f"recording audio file is not readable: {audio_path}") from exc

    if size <= DEFAULT_WAV_DATA_OFFSET:
        return RecordingLevel(False, 0, 0.0, 0.0, 0, "waiting for audio")

    try:
        with audio_path.open("rb") as handle:
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
    normalized = path.expanduser().resolve(strict=False)
    if len(str(normalized)) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact path is too long")
    if len(normalized.name) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact file name is too long")
    if len(normalized.stem) > MAX_RECORDING_STEM_CHARS:
        raise RecorderError("recording artifact stem is too long")
    if normalized.suffix.lower() != suffix.lower():
        raise RecorderError(f"recording artifact must use {suffix} extension")
    if require_recordings_dir:
        root = recordings_dir().resolve(strict=False)
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
        if candidate == "pw-record" and shutil.which("pw-record"):
            argv = ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16"]
            if target:
                argv.extend(["--target", target])
            if max_seconds > 0:
                argv.extend(["--sample-count", str(16000 * max_seconds)])
            argv.append(str(audio_path))
            return RecorderCommand(candidate, argv)
        if candidate == "parecord" and shutil.which("parecord"):
            argv = ["parecord", "--file-format=wav", "--rate=16000", "--channels=1", str(audio_path)]
            if target:
                argv.insert(-1, f"--device={target}")
            return RecorderCommand(candidate, argv)
        if candidate == "arecord" and shutil.which("arecord"):
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


def _run_pactl_command(command: list[str], *, required: bool) -> str:
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command) or not isinstance(required, bool):
        raise RecorderError("invalid pactl command")
    if not command:
        raise RecorderError("empty pactl command is not allowed")
    pactl = command[0].strip()
    if not pactl:
        raise RecorderError("empty pactl executable is not allowed")
    if _contains_escaped_null(pactl) or any(_contains_escaped_null(arg) for arg in command[1:]):
        raise RecorderError("pactl command contains invalid null byte")
    runtime_command = [pactl, *command[1:]]
    try:
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            try:
                proc = subprocess.run(
                    args=runtime_command,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=MAX_PACTL_TIMEOUT_SECONDS,
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
    if not shutil.which("pactl"):
        raise RecorderError("pactl is required to list input sources; install pulseaudio-utils")

    default_source = _run_pactl_command(["pactl", "get-default-source"], required=False)
    proc_output = _run_pactl_command(["pactl", "list", "sources"], required=True)
    return parse_pactl_sources(proc_output, default_source, include_monitors)


def _run_kill(command: list[str], *, check_exit: bool) -> None:
    if not isinstance(command, list) or any(not isinstance(item, str) for item in command) or not isinstance(check_exit, bool):
        raise RecorderError("invalid kill command")
    if not command:
        raise RecorderError("empty kill command is not allowed")
    kill_command = command[0].strip()
    if not kill_command:
        raise RecorderError("empty kill executable is not allowed")
    if _contains_escaped_null(kill_command) or any(_contains_escaped_null(arg) for arg in command[1:]):
        raise RecorderError("kill command contains invalid null byte")
    runtime_command = [kill_command, *command[1:]]
    try:
        subprocess.run(
            runtime_command,
            check=check_exit,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecorderError(f"kill command timed out: {runtime_command}") from exc
    except OSError as exc:
        raise RecorderError(f"failed to run kill command {runtime_command}: {exc}") from exc


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
    log_path = validate_recording_path(log_path, suffix=".log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        return subprocess.Popen(command.argv, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True)
    except OSError as exc:
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
