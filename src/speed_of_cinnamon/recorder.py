from __future__ import annotations

import array
import io
import math
import os
import re
import secrets
import shutil
import subprocess  # nosec B404
import sys
import stat
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path

from .paths import recordings_dir
from .path_safety import (
    assert_fd_is_regular_private_file,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    open_directory_without_following_symlinks,
)


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
    if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
        return None
    return value


def _sanitize_ffmpeg_error_detail(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _strip_terminal_controls(text).strip()
    if not text:
        return ""
    lowered = text.lower()
    if (
        "/" in text
        or "\\" in text
        or "://" in text
        or "device" in lowered
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
    ):
        return "[redacted ffmpeg error]"
    if len(text) > 160:
        return text[:157] + "..."
    return text


def _sanitize_pactl_error_detail(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = _strip_terminal_controls(text).strip()
    if not text:
        return ""
    lowered = text.lower()
    if (
        "/" in text
        or "\\" in text
        or "://" in text
        or "device" in lowered
        or "token" in lowered
        or "secret" in lowered
        or "password" in lowered
    ):
        return "[redacted pactl error]"
    if len(text) > 160:
        return text[:157] + "..."
    return text


def _strip_terminal_controls(text: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            next_index = index + 1
            if next_index < len(text) and text[next_index] == "[":
                index = next_index + 1
                while index < len(text):
                    codepoint = ord(text[index])
                    index += 1
                    if 0x40 <= codepoint <= 0x7E:
                        break
                continue
            index += 1
            continue
        codepoint = ord(char)
        if codepoint in (0x09, 0x0A, 0x0D):
            result.append(" ")
        elif codepoint < 0x20 or 0x7F <= codepoint <= 0x9F:
            pass
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _strip_ffmpeg_terminal_controls(text: str) -> str:
    return _strip_terminal_controls(text)


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
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise RecorderError("environment key contains invalid control character")
            if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
                raise RecorderError("environment value contains invalid control character")
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


@dataclass(frozen=True)
class SilenceDetectionResult:
    analyzed: bool
    silent: bool
    duration_seconds: float
    silence_seconds: float
    speech_seconds: float
    leading_silence_seconds: float = 0.0
    detail: str = ""


MAX_RECORDING_PATH_CHARS = 240
MAX_RECORDING_STEM_CHARS = 120
MAX_RECORDING_SECONDS = 3_600
MAX_RECORDING_INPUT_DEVICE_CHARS = 256
MAX_PACTL_OUTPUT_CHARS = 1_000_000
MAX_PACTL_TIMEOUT_SECONDS = 10
MAX_FFMPEG_OUTPUT_BYTES = 256 * 1024
MAX_FFMPEG_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_RECORDING_LEVEL_BYTES = 128_000
WAV_TRIM_CHUNK_FRAMES = 16_000
WAV_HEADER_SCAN_BYTES = 512
DEFAULT_WAV_DATA_OFFSET = 44
SILENCE_DETECT_NOISE = "-62dB"
SILENCE_DETECT_DURATION_SECONDS = 0.02
SILENCE_TRIM_NOISE = SILENCE_DETECT_NOISE
SILENCE_TRIM_DURATION_SECONDS = SILENCE_DETECT_DURATION_SECONDS
SILENCE_DETECT_TIMEOUT_SECONDS = 60
SILENCE_SKIP_RATIO = 0.999
SILENCE_SKIP_MAX_SPEECH_SECONDS = 0.6

_FFMPEG_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_SILENCE_END_RE = re.compile(
    r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*([0-9]+(?:\.[0-9]+)?)"
)


def _contains_escaped_null(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_http_header_control_chars(value: str) -> bool:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("value must be text")
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


def _completed_output_bytes(value: object, *, field_name: str) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        try:
            payload = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise RecorderError(f"ffmpeg command {field_name} contains invalid UTF-8") from exc
    else:
        return b""
    if len(payload) > MAX_FFMPEG_OUTPUT_BYTES:
        raise RecorderError(f"ffmpeg command {field_name} exceeded safe output limit")
    return payload


def _read_ffmpeg_output(handle: io.BufferedRandom, *, completed_output: object, field_name: str) -> bytes:
    if not hasattr(handle, "seek") or not hasattr(handle, "tell") or not hasattr(handle, "read"):
        raise RecorderError("ffmpeg output must be a binary file handle")
    handle.seek(0, os.SEEK_END)
    size = handle.tell()
    if size > MAX_FFMPEG_OUTPUT_BYTES:
        raise RecorderError(f"ffmpeg command {field_name} exceeded safe output limit")
    handle.seek(0)
    payload = handle.read(MAX_FFMPEG_OUTPUT_BYTES + 1)
    if not isinstance(payload, bytes):
        raise RecorderError("ffmpeg output must be bytes")
    if payload:
        if len(payload) > MAX_FFMPEG_OUTPUT_BYTES:
            raise RecorderError(f"ffmpeg command {field_name} exceeded safe output limit")
        return payload
    return _completed_output_bytes(completed_output, field_name=field_name)


def _decode_ffmpeg_output(payload: object) -> str:
    if isinstance(payload, bytes):
        try:
            return payload.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise RecorderError("ffmpeg output contains invalid UTF-8") from exc
    if isinstance(payload, str):
        return payload.strip()
    return ""


def _run_ffmpeg_bounded(
    command: list[str],
    *,
    timeout: int,
    pass_fds: tuple[int, ...],
) -> subprocess.CompletedProcess[bytes]:
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.run(  # nosec B603
            command,
            check=False,
            stdout=stdout_file,
            stderr=stderr_file,
            timeout=timeout,
            env=_filtered_environment(),
            pass_fds=pass_fds,
        )
        stdout = _read_ffmpeg_output(stdout_file, completed_output=getattr(proc, "stdout", None), field_name="stdout")
        stderr = _read_ffmpeg_output(stderr_file, completed_output=getattr(proc, "stderr", None), field_name="stderr")
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)


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


def _recording_process_identity_for_pid(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        close = raw.rindex(")")
        rest = raw[close + 2 :].split()
    except ValueError:
        return None
    if len(rest) < 20:
        return None
    try:
        boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    start_time = rest[19]
    if not boot_id or not start_time:
        return None
    return f"{boot_id}:{start_time}"


def _recording_process_identity_matches(pid: int, expected_process_identity: str | None) -> bool:
    if expected_process_identity is None:
        return True
    if not isinstance(expected_process_identity, str) or isinstance(expected_process_identity, bool):
        raise RecorderError("expected_process_identity must be text")
    if not expected_process_identity:
        return False
    current_identity = _recording_process_identity_for_pid(pid)
    if current_identity is None:
        return False
    return current_identity == expected_process_identity


def _create_recording_temp_file(audio_path: Path, *, marker: str, suffix: str) -> tuple[int, Path]:
    if not isinstance(audio_path, Path):
        raise RecorderError("recording audio path must be a path")
    if not isinstance(marker, str) or not marker or any(char in marker for char in ("/", "\\", "\x00")):
        raise RecorderError("recording temp marker is invalid")
    if not isinstance(suffix, str) or not suffix.startswith(".") or any(char in suffix for char in ("/", "\\", "\x00")):
        raise RecorderError("recording temp suffix is invalid")
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RecorderError("secure recording temp file creation is not supported on this platform")
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            audio_path.parent,
            field_name="recording artifact directory",
        )
    except OSError as exc:
        raise RecorderError("failed to prepare recording artifact directory") from exc
    temp_name = ""
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow_flag
        for _ in range(100):
            temp_name = f"{audio_path.stem}.{marker}-{secrets.token_hex(8)}{suffix}"
            try:
                fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
                return fd, audio_path.parent / temp_name
            except FileExistsError:
                temp_name = ""
                continue
        raise RecorderError("failed to create recording temporary file")
    except OSError as exc:
        raise RecorderError("failed to create recording temporary file") from exc
    finally:
        os.close(parent_fd)


def _recording_temp_path_matches_fd(path: Path, fd: int) -> bool:
    try:
        path_stat = os.stat(path, follow_symlinks=False)
        fd_stat = os.fstat(fd)
    except OSError:
        return False
    return (
        stat.S_ISREG(path_stat.st_mode)
        and path_stat.st_dev == fd_stat.st_dev
        and path_stat.st_ino == fd_stat.st_ino
    )


def _unlink_recording_path_if_same(path: Path, expected_stat: os.stat_result) -> None:
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            path.parent,
            field_name="recording artifact directory",
        )
    except OSError:
        return
    try:
        try:
            current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == expected_stat.st_dev
            and current.st_ino == expected_stat.st_ino
            and current.st_mode == expected_stat.st_mode
        ):
            os.unlink(path.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except OSError:
        return
    finally:
        os.close(parent_fd)


def _cleanup_recording_temp_file(path: Path, fd: int) -> None:
    try:
        expected_stat = os.fstat(fd)
    except OSError:
        expected_stat = None
    try:
        os.close(fd)
    except OSError:
        pass
    if expected_stat is not None:
        _unlink_recording_path_if_same(path, expected_stat)


def _ffmpeg_output_path_for_fd(fd: int) -> str:
    proc_fd_path = Path("/proc/self/fd") / str(fd)
    if not proc_fd_path.exists():
        raise RecorderError("secure ffmpeg output file descriptor path is not available")
    return str(proc_fd_path)


def _inspect_and_close_recording_temp_file(path: Path, fd: int, *, field_name: str) -> tuple[int, bool, os.stat_result]:
    try:
        output_stat = os.fstat(fd)
        output_size = output_stat.st_size
        output_matches_path = _recording_temp_path_matches_fd(path, fd)
    except OSError as exc:
        raise RecorderError(f"failed to inspect {field_name}") from exc
    finally:
        os.close(fd)
    return output_size, output_matches_path, output_stat


def _parse_ffmpeg_duration(text: str) -> float:
    match = _FFMPEG_DURATION_RE.search(text)
    if not match:
        return 0.0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def _parse_silence_seconds(text: str, duration_seconds: float) -> tuple[float, float]:
    if duration_seconds <= 0:
        return 0.0, 0.0
    intervals: list[tuple[float, float]] = []
    current_start: float | None = None
    leading_silence_seconds = 0.0
    for line in text.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            current_start = max(0.0, float(start_match.group(1)))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if not end_match:
            continue
        end = min(duration_seconds, max(0.0, float(end_match.group(1))))
        silence_duration = max(0.0, float(end_match.group(2)))
        start = current_start if current_start is not None else max(0.0, end - silence_duration)
        intervals.append((min(start, duration_seconds), end))
        if leading_silence_seconds == 0.0 and start <= 0.0:
            leading_silence_seconds = max(0.0, end - start)
        current_start = None
    if current_start is not None and current_start < duration_seconds:
        intervals.append((current_start, duration_seconds))
        if leading_silence_seconds == 0.0 and current_start <= 0.0:
            leading_silence_seconds = max(0.0, duration_seconds - current_start)
    return (
        min(duration_seconds, sum(max(0.0, end - start) for start, end in intervals)),
        min(duration_seconds, leading_silence_seconds),
    )


def detect_silent_recording(audio_path: Path) -> SilenceDetectionResult:
    if not isinstance(audio_path, Path):
        raise RecorderError("recording audio path must be a path")
    audio_fd: int | None = None
    try:
        audio_path, audio_fd = _open_private_recording_audio_file(audio_path, suffix=(".wav", ".flac"))
    except RuntimeError as exc:
        raise RecorderError(str(exc)) from exc
    except OSError as exc:
        raise RecorderError(f"recording audio file is not readable: {exc}") from exc
    try:
        ffmpeg = _command_path("ffmpeg")
    except RecorderError as exc:
        if audio_fd is not None:
            os.close(audio_fd)
        return SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, str(exc))
    try:
        input_path = _ffmpeg_output_path_for_fd(audio_fd)
        # argv-only ffmpeg invocation with trusted binary resolution.
        proc = _run_ffmpeg_bounded(
            [
                ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-nostats",
                "-i",
                input_path,
                "-af",
                f"silencedetect=noise={SILENCE_DETECT_NOISE}:d={SILENCE_DETECT_DURATION_SECONDS}",
                "-f",
                "null",
                "-",
            ],
            timeout=SILENCE_DETECT_TIMEOUT_SECONDS,
            pass_fds=(audio_fd,),
        )
    except (OSError, subprocess.TimeoutExpired, RecorderError) as exc:
        detail = _sanitize_ffmpeg_error_detail(exc)
        if detail and not _contains_escaped_null(detail):
            return SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, f"ffmpeg silence detection failed: {detail}")
        return SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, "ffmpeg silence detection failed")
    finally:
        if audio_fd is not None:
            os.close(audio_fd)
    if proc.returncode != 0:
        detail = _decode_ffmpeg_output(proc.stderr)
        detail = _sanitize_ffmpeg_error_detail(detail)
        if detail and not _contains_escaped_null(detail):
            return SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, f"ffmpeg silence detection failed: {detail}")
        return SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, "ffmpeg silence detection failed")
    stderr_text = _decode_ffmpeg_output(proc.stderr)
    duration_seconds = _parse_ffmpeg_duration(stderr_text)
    if duration_seconds <= 0:
        return SilenceDetectionResult(False, False, 0.0, 0.0, 0.0, 0.0, "ffmpeg duration was unavailable")
    silence_seconds, leading_silence_seconds = _parse_silence_seconds(stderr_text, duration_seconds)
    speech_seconds = max(0.0, duration_seconds - silence_seconds)
    silence_ratio = silence_seconds / duration_seconds if duration_seconds else 0.0
    silent = silence_ratio >= SILENCE_SKIP_RATIO and speech_seconds <= SILENCE_SKIP_MAX_SPEECH_SECONDS
    return SilenceDetectionResult(
        True,
        silent,
        round(duration_seconds, 4),
        round(silence_seconds, 4),
        round(speech_seconds, 4),
        round(leading_silence_seconds, 4),
        "silent recording" if silent else "speech detected",
    )


def trim_recording_leading_silence(audio_path: Path, leading_silence_seconds: float) -> Path:
    if not isinstance(audio_path, Path):
        raise RecorderError("recording audio path must be a path")
    if not isinstance(leading_silence_seconds, (int, float)) or isinstance(leading_silence_seconds, bool):
        raise RecorderError("leading silence seconds must be numeric")
    if leading_silence_seconds <= 0:
        return audio_path
    audio_fd: int | None = None
    try:
        audio_path, audio_fd = _open_private_recording_audio_file(audio_path, suffix=".wav")
    except RuntimeError as exc:
        raise RecorderError(str(exc)) from exc
    fd: int | None = None
    temp_path: Path | None = None
    temp_stat: os.stat_result | None = None
    try:
        with os.fdopen(audio_fd, "rb") as audio_file:
            audio_fd = None
            with wave.open(audio_file, "rb") as source:
                frame_rate = source.getframerate()
                total_frames = source.getnframes()
                raw_start_frame = leading_silence_seconds * frame_rate
                rounded_start_frame = round(raw_start_frame)
                if abs(raw_start_frame - rounded_start_frame) < 1e-6:
                    start_frame = max(0, rounded_start_frame)
                else:
                    start_frame = max(0, int(raw_start_frame))
                if start_frame <= 0:
                    return audio_path
                if start_frame >= total_frames:
                    raise RecorderError("recording contains no speech after leading silence")
                params = source.getparams()
                source.setpos(start_frame)
                frame_width = params.nchannels * params.sampwidth
                if frame_width <= 0:
                    raise RecorderError("recording audio parameters are invalid")
                fd, temp_path = _create_recording_temp_file(audio_path, marker="trimmed", suffix=audio_path.suffix)
                temp_stat = os.fstat(fd)
                with os.fdopen(fd, "wb") as output_file:
                    fd = None
                    with wave.open(output_file, "wb") as dest:
                        dest.setparams(params)
                        remaining = total_frames - start_frame
                        written_bytes = 0
                        while remaining > 0:
                            chunk = source.readframes(min(WAV_TRIM_CHUNK_FRAMES, remaining))
                            frames_read = len(chunk) // frame_width
                            written_bytes += len(chunk)
                            if written_bytes >= MAX_FFMPEG_ARTIFACT_BYTES:
                                raise RecorderError("recording leading silence trim exceeded safe artifact size limit")
                            if frames_read <= 0:
                                raise RecorderError("failed to trim recording audio file")
                            dest.writeframesraw(chunk)
                            remaining -= frames_read
                    output_file.flush()
                    if os.fstat(output_file.fileno()).st_size >= MAX_FFMPEG_ARTIFACT_BYTES:
                        raise RecorderError("recording leading silence trim exceeded safe artifact size limit")
                    if not _recording_temp_path_matches_fd(temp_path, output_file.fileno()):
                        raise RecorderError("trimmed recording temporary file was replaced")
    except (OSError, wave.Error) as exc:
        if temp_path is not None and temp_stat is not None:
            _unlink_recording_path_if_same(temp_path, temp_stat)
        raise RecorderError("failed to trim recording audio file") from exc
    except Exception as exc:
        if temp_path is not None and temp_stat is not None:
            _unlink_recording_path_if_same(temp_path, temp_stat)
        if isinstance(exc, RecorderError):
            raise
        raise RecorderError("failed to write trimmed recording audio file") from exc
    finally:
        if audio_fd is not None:
            os.close(audio_fd)
        if fd is not None:
            os.close(fd)
    if temp_path is None:
        raise RecorderError("failed to write trimmed recording audio file")
    return temp_path


def trim_recording_silence(
    audio_path: Path,
    *,
    noise: str = SILENCE_TRIM_NOISE,
    duration_seconds: float = SILENCE_TRIM_DURATION_SECONDS,
) -> Path:
    if not isinstance(audio_path, Path):
        raise RecorderError("recording audio path must be a path")
    if not isinstance(noise, str) or isinstance(noise, bool):
        raise RecorderError("silence trim noise threshold must be text")
    if not noise.strip():
        raise RecorderError("silence trim noise threshold must not be empty")
    if not isinstance(duration_seconds, (int, float)) or isinstance(duration_seconds, bool):
        raise RecorderError("silence trim duration must be numeric")
    if duration_seconds <= 0:
        raise RecorderError("silence trim duration must be greater than 0")
    if _contains_escaped_null(noise):
        raise RecorderError("silence trim noise threshold contains invalid null byte")
    audio_fd: int | None = None
    try:
        audio_path, audio_fd = _open_private_recording_audio_file(
            audio_path,
            suffix=(".wav", ".flac"),
            require_recordings_dir=False,
        )
    except RuntimeError as exc:
        raise RecorderError(str(exc)) from exc
    try:
        fd, trimmed_path = _create_recording_temp_file(audio_path, marker="trimmed", suffix=".flac")
    except Exception:
        if audio_fd is not None:
            os.close(audio_fd)
        raise
    output_path = ""
    try:
        output_path = _ffmpeg_output_path_for_fd(fd)
        input_path = _ffmpeg_output_path_for_fd(audio_fd)
        ffmpeg = _command_path("ffmpeg")
    except Exception:
        _cleanup_recording_temp_file(trimmed_path, fd)
        if audio_fd is not None:
            os.close(audio_fd)
        raise
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-i",
        input_path,
        "-af",
        (
            f"silenceremove=start_periods=1:start_duration={duration_seconds}:"
            f"start_threshold={noise}:stop_periods=1:stop_duration={duration_seconds}:"
            f"stop_threshold={noise}"
        ),
        "-c:a",
        "flac",
        "-f",
        "flac",
        "-fs",
        str(MAX_FFMPEG_ARTIFACT_BYTES),
        "-y",
        output_path,
    ]
    try:
        proc = _run_ffmpeg_bounded(
            command,
            timeout=SILENCE_DETECT_TIMEOUT_SECONDS,
            pass_fds=(audio_fd, fd),
        )
    except (OSError, subprocess.TimeoutExpired, RecorderError) as exc:
        if audio_fd is not None:
            os.close(audio_fd)
            audio_fd = None
        _cleanup_recording_temp_file(trimmed_path, fd)
        detail = _sanitize_ffmpeg_error_detail(exc)
        raise RecorderError(f"failed to trim silence from recording: {detail or 'ffmpeg failed'}") from exc
    finally:
        if audio_fd is not None:
            os.close(audio_fd)
            audio_fd = None
    if proc.returncode != 0:
        detail = _decode_ffmpeg_output(proc.stderr)
        if detail and _contains_escaped_null(detail):
            detail = ""
        detail = _sanitize_ffmpeg_error_detail(detail)
        _cleanup_recording_temp_file(trimmed_path, fd)
        raise RecorderError(detail or "ffmpeg silence trimming failed")
    try:
        output_size, output_matches_path, output_stat = _inspect_and_close_recording_temp_file(
            trimmed_path,
            fd,
            field_name="ffmpeg silence trimming temporary file",
        )
    except RecorderError:
        raise
    if output_size == 0:
        _unlink_recording_path_if_same(trimmed_path, output_stat)
        raise RecorderError("ffmpeg silence trimming produced empty output")
    if output_size >= MAX_FFMPEG_ARTIFACT_BYTES:
        _unlink_recording_path_if_same(trimmed_path, output_stat)
        raise RecorderError("ffmpeg silence trimming exceeded safe artifact size limit")
    if not output_matches_path:
        _unlink_recording_path_if_same(trimmed_path, output_stat)
        raise RecorderError("ffmpeg silence trimming temporary file was replaced")
    return trimmed_path


def reencode_recording_to_flac(audio_path: Path) -> Path:
    if not isinstance(audio_path, Path):
        raise RecorderError("recording audio path must be a path")
    audio_fd: int | None = None
    try:
        audio_path, audio_fd = _open_private_recording_audio_file(
            audio_path,
            suffix=(".wav", ".flac"),
            require_recordings_dir=False,
        )
    except RuntimeError as exc:
        raise RecorderError(str(exc)) from exc
    if audio_path.suffix.lower() == ".flac":
        if audio_fd is not None:
            os.close(audio_fd)
        return audio_path

    try:
        fd, encoded_path = _create_recording_temp_file(audio_path, marker="encoded", suffix=".flac")
    except Exception:
        if audio_fd is not None:
            os.close(audio_fd)
        raise
    output_path = ""
    try:
        output_path = _ffmpeg_output_path_for_fd(fd)
        input_path = _ffmpeg_output_path_for_fd(audio_fd)
        ffmpeg = _command_path("ffmpeg")
    except Exception:
        _cleanup_recording_temp_file(encoded_path, fd)
        if audio_fd is not None:
            os.close(audio_fd)
        raise
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-nostats",
        "-i",
        input_path,
        "-c:a",
        "flac",
        "-f",
        "flac",
        "-fs",
        str(MAX_FFMPEG_ARTIFACT_BYTES),
        "-y",
        output_path,
    ]
    try:
        proc = _run_ffmpeg_bounded(
            command,
            timeout=SILENCE_DETECT_TIMEOUT_SECONDS,
            pass_fds=(audio_fd, fd),
        )
    except (OSError, subprocess.TimeoutExpired, RecorderError) as exc:
        if audio_fd is not None:
            os.close(audio_fd)
            audio_fd = None
        _cleanup_recording_temp_file(encoded_path, fd)
        detail = _sanitize_ffmpeg_error_detail(exc)
        raise RecorderError(f"failed to convert recording to FLAC: {detail or 'ffmpeg failed'}") from exc
    finally:
        if audio_fd is not None:
            os.close(audio_fd)
            audio_fd = None
    if proc.returncode != 0:
        detail = _decode_ffmpeg_output(proc.stderr)
        if detail and _contains_escaped_null(detail):
            detail = ""
        detail = _sanitize_ffmpeg_error_detail(detail)
        _cleanup_recording_temp_file(encoded_path, fd)
        raise RecorderError(detail or "ffmpeg FLAC conversion failed")
    try:
        output_size, output_matches_path, output_stat = _inspect_and_close_recording_temp_file(
            encoded_path,
            fd,
            field_name="ffmpeg FLAC conversion temporary file",
        )
    except RecorderError:
        raise
    if output_size == 0:
        _unlink_recording_path_if_same(encoded_path, output_stat)
        raise RecorderError("ffmpeg FLAC conversion produced empty output")
    if output_size >= MAX_FFMPEG_ARTIFACT_BYTES:
        _unlink_recording_path_if_same(encoded_path, output_stat)
        raise RecorderError("ffmpeg FLAC conversion exceeded safe artifact size limit")
    if not output_matches_path:
        _unlink_recording_path_if_same(encoded_path, output_stat)
        raise RecorderError("ffmpeg FLAC conversion temporary file was replaced")
    return encoded_path


def _assert_positive_pid(pid: int) -> None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise RecorderError(f"invalid process id: {pid}")


def _assert_valid_input_device(value: str) -> None:
    if not isinstance(value, str) or isinstance(value, bool):
        raise RecorderError("recording input device must be text")
    if _contains_escaped_null(value):
        raise RecorderError("recording input device contains invalid null byte")
    if _contains_http_header_control_chars(value):
        raise RecorderError("recording input device contains invalid control character")
    try:
        encoded_value = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecorderError("recording input device contains invalid UTF-8") from exc
    if len(value) > MAX_RECORDING_INPUT_DEVICE_CHARS:
        raise RecorderError("recording input device name is too long")
    if len(encoded_value) > MAX_RECORDING_INPUT_DEVICE_CHARS:
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
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    try:
        fd = _open_recording_artifact_leaf(
            audio_path,
            os.O_RDONLY | nofollow_flag | nonblock_flag,
            field_name="recording audio file",
        )
        assert_fd_is_regular_private_file(fd, field_name="recording audio file")
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise RecorderError("recording audio file is not readable") from exc
    except RuntimeError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise RecorderError("recording audio file is not readable") from exc
    try:
        with os.fdopen(fd, "rb") as handle:
            fd = None
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
        raise RecorderError("recording audio file is not readable") from exc

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


def _normalize_suffixes(suffix: str | tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(suffix, str):
        suffixes = (suffix,)
    elif isinstance(suffix, tuple):
        if not suffix:
            raise RecorderError("recording artifact suffix must be a non-empty string or tuple")
        suffixes = suffix
    else:
        raise RecorderError("recording artifact suffix must be text")
    normalized: list[str] = []
    for item in suffixes:
        if not isinstance(item, str) or isinstance(item, bool):
            raise RecorderError("recording artifact suffix must be text")
        if not item:
            raise RecorderError("recording artifact suffix must be text")
        normalized.append(item.lower())
    if not normalized:
        raise RecorderError("recording artifact suffix must be text")
    return tuple(dict.fromkeys(normalized))


def validate_recording_path(
    path: Path,
    *,
    suffix: str | tuple[str, ...],
    require_recordings_dir: bool = False,
    recordings_root: Path | None = None,
) -> Path:
    if not isinstance(path, Path):
        raise RecorderError("recording artifact path must be a path")
    normalized_suffixes = _normalize_suffixes(suffix)
    path_text = str(path)
    try:
        path_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecorderError("recording artifact path contains invalid UTF-8") from exc
    if _contains_escaped_null(path_text):
        raise RecorderError("recording artifact path contains invalid null byte")
    if _contains_http_header_control_chars(path_text):
        raise RecorderError("recording artifact path contains invalid control character")
    normalized = path.expanduser()
    try:
        str(normalized).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecorderError("recording artifact path contains invalid UTF-8") from exc
    assert_no_symlink_ancestors(normalized, field_name="recording artifact path")
    normalized = normalized.resolve(strict=False)
    if len(str(normalized)) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact path is too long")
    try:
        normalized_bytes = str(normalized).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecorderError("recording artifact path contains invalid UTF-8") from exc
    if len(normalized_bytes) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError(f"recording artifact path is too long (max {MAX_RECORDING_PATH_CHARS} bytes)")
    if len(normalized.name) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact file name is too long")
    try:
        normalized_name_bytes = normalized.name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecorderError("recording artifact path contains invalid UTF-8") from exc
    if len(normalized_name_bytes) > MAX_RECORDING_PATH_CHARS:
        raise RecorderError("recording artifact file name is too long")
    if len(normalized.stem) > MAX_RECORDING_STEM_CHARS:
        raise RecorderError("recording artifact stem is too long")
    try:
        normalized_stem_bytes = normalized.stem.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RecorderError("recording artifact path contains invalid UTF-8") from exc
    if len(normalized_stem_bytes) > MAX_RECORDING_STEM_CHARS:
        raise RecorderError(f"recording artifact stem is too long (max {MAX_RECORDING_STEM_CHARS} bytes)")
    if normalized.suffix.lower() not in normalized_suffixes:
        if len(normalized_suffixes) == 1:
            raise RecorderError(f"recording artifact must use {normalized_suffixes[0]} extension")
        suffix_summary = ", ".join(normalized_suffixes)
        raise RecorderError(f"recording artifact must use one of the following extensions: {suffix_summary}")
    if require_recordings_dir:
        root = recordings_root if recordings_root is not None else recordings_dir()
        if not isinstance(root, Path):
            raise RecorderError("recordings directory must be a path")
        root = root.expanduser()
        assert_no_symlink_ancestors(root, field_name="recordings directory")
        root = root.resolve(strict=False)
        try:
            normalized.relative_to(root)
        except ValueError as exc:
            raise RecorderError("recording artifact is outside the recordings directory") from exc
    return normalized


def _validate_private_recording_audio_file(
    path: Path,
    *,
    suffix: str | tuple[str, ...],
    require_recordings_dir: bool = False,
    recordings_root: Path | None = None,
) -> Path:
    normalized, fd = _open_private_recording_audio_file(
        path,
        suffix=suffix,
        require_recordings_dir=require_recordings_dir,
        recordings_root=recordings_root,
    )
    os.close(fd)
    return normalized


def _open_recording_artifact_leaf(path: Path, flags: int, *, field_name: str) -> int:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RecorderError("secure recording artifact open is not supported on this platform")
    flags |= nofollow_flag
    parent_fd = open_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
    try:
        return os.open(path.name, flags, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _open_private_recording_audio_file(
    path: Path,
    *,
    suffix: str | tuple[str, ...],
    require_recordings_dir: bool = False,
    recordings_root: Path | None = None,
) -> tuple[Path, int]:
    normalized = validate_recording_path(
        path,
        suffix=suffix,
        require_recordings_dir=require_recordings_dir,
        recordings_root=recordings_root,
    )
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RecorderError("secure recording audio file open is not supported on this platform")
    fd = _open_recording_artifact_leaf(
        normalized,
        os.O_RDONLY | nofollow_flag | getattr(os, "O_NONBLOCK", 0),
        field_name="recording audio file",
    )
    try:
        assert_fd_is_regular_private_file(fd, field_name="recording audio file")
    except Exception:
        os.close(fd)
        raise
    return normalized, fd


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
    if _contains_escaped_null(preference):
        raise RecorderError("recording preference contains invalid null byte")
    if _contains_http_header_control_chars(preference):
        raise RecorderError("recording preference contains invalid control character")
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
            if max_seconds > 0:
                if not _which("timeout"):
                    if preference == "parecord":
                        raise RecorderError("timeout is required to enforce max-seconds with parecord")
                    continue
                argv = ["timeout", "--kill-after=1", str(max_seconds), *argv]
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

    def _current_source_is_safe() -> bool:
        if current is None:
            return False
        return not any(
            _contains_escaped_null(value) or _contains_http_header_control_chars(value)
            for value in current.values()
        )

    def finish() -> None:
        if not current or not current.get("name"):
            return
        if not _current_source_is_safe():
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
                stderr = _sanitize_pactl_error_detail(_ensure_file_head(stderr_file, 2048))
                stdout = _sanitize_pactl_error_detail(_ensure_file_head(stdout_file, 2048))
                raise RecorderError(stderr or stdout or "pactl failed")

            return _ensure_file_head(stdout_file, MAX_PACTL_OUTPUT_CHARS).strip()
    except subprocess.TimeoutExpired as exc:
        raise RecorderError(f"pactl command timed out after {MAX_PACTL_TIMEOUT_SECONDS}s") from exc
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


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino, left.st_mode) == (right.st_dev, right.st_ino, right.st_mode)


def _process_is_gone(process_target: str) -> bool:
    try:
        os.kill(int(process_target), 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def _open_recorder_log_file(log_path: Path) -> tuple[io.BufferedWriter, bool]:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if nofollow_flag is None:
        raise RecorderError("secure log file open is not supported on this platform")
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            log_path.parent,
            field_name="recorder log directory",
        )
    except OSError as exc:
        raise RecorderError("failed to open recorder log file") from exc
    created = False
    fd: int | None = None
    try:
        try:
            fd = os.open(log_path.name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL | nofollow_flag, 0o600, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            fd = os.open(log_path.name, os.O_WRONLY | os.O_APPEND | nofollow_flag, 0o600, dir_fd=parent_fd)
            created = False
        assert_fd_is_regular_private_file(fd, field_name="recorder log file", require_private_mode=True)
        handle = os.fdopen(fd, "ab")
        fd = None
        return handle, created
    except RecorderError:
        raise
    except RuntimeError as exc:
        raise RecorderError(str(exc)) from exc
    except (OSError, ValueError) as exc:
        raise RecorderError("failed to open recorder log file") from exc
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent_fd)


def _unlink_recorder_log_if_same(log_path: Path, expected_stat: os.stat_result) -> None:
    try:
        parent_fd = ensure_directory_without_following_symlinks(
            log_path.parent,
            field_name="recorder log directory",
        )
    except OSError:
        return
    try:
        try:
            current = os.stat(log_path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if _same_file_identity(current, expected_stat):
            os.unlink(log_path.name, dir_fd=parent_fd)
            os.fsync(parent_fd)
    except OSError:
        return
    finally:
        os.close(parent_fd)


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
    log_file, created_log = _open_recorder_log_file(log_path)
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
            opened_stat = os.fstat(log_file.fileno())
            if created_log:
                _unlink_recorder_log_if_same(log_path, opened_stat)
        except OSError:
            pass
        raise RecorderError(f"failed to start {command.name}: {exc}") from exc
    finally:
        log_file.close()


def stop_process(
    pid: int,
    timeout_seconds: float = 5.0,
    *,
    expected_process_identity: str | None = None,
    allow_unverified_process: bool = False,
) -> bool:
    _assert_positive_pid(pid)
    if expected_process_identity is not None and (
        not isinstance(expected_process_identity, str) or isinstance(expected_process_identity, bool)
    ):
        raise RecorderError("expected_process_identity must be text")
    if not isinstance(allow_unverified_process, bool):
        raise RecorderError("allow_unverified_process must be boolean")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise RecorderError("timeout_seconds must be numeric")
    if not math.isfinite(timeout_seconds):
        raise RecorderError("timeout_seconds must be finite")
    if timeout_seconds <= 0:
        raise RecorderError("timeout_seconds must be positive")
    if expected_process_identity is None and not allow_unverified_process:
        raise RecorderError("expected_process_identity is required to stop recorder process")
    try:
        process_group_target = os.getpgid(pid) == pid
        process_target = f"-{pid}" if process_group_target else str(pid)
    except ProcessLookupError:
        return False
    except OSError as exc:
        raise RecorderError(f"failed to inspect recorder process {pid}: {exc}") from exc

    if not _recording_process_identity_matches(pid, expected_process_identity):
        return False

    def target_identity_still_safe() -> bool:
        if _recording_process_identity_matches(pid, expected_process_identity):
            return True
        return False

    _run_kill(["kill", "-INT", "--", process_target], check_exit=False)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _process_is_gone(process_target):
            return True
        if not target_identity_still_safe():
            return False
        time.sleep(0.1)

    if _process_is_gone(process_target):
        return True
    if not target_identity_still_safe():
        return False
    _run_kill(["kill", "-TERM", "--", process_target], check_exit=False)

    time.sleep(0.5)
    if _process_is_gone(process_target):
        return True
    if not target_identity_still_safe():
        return False

    try:
        _run_kill(["kill", "-KILL", "--", process_target], check_exit=False)
    except RecorderError as exc:
        raise RecorderError(f"failed to stop recorder process {pid}: {exc}") from exc
    time.sleep(0.1)
    if _process_is_gone(process_target):
        return True
    if not target_identity_still_safe():
        return False
    return _process_is_gone(process_target)
