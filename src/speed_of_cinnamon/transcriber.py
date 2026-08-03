from __future__ import annotations

import shutil
import shlex
import subprocess  # nosec B404
import tempfile
import time
import io
import hashlib
import json
import errno
import fcntl
import os
import re
import stat as stat_module
import sys
import uuid
import urllib.parse
import urllib.error
import urllib.request
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .models import default_ctranslate2_model_path, default_whisper_cpp_model_path, model_backend_for_path, model_supports_language
from .command_chain import CommandChainError, MAX_COMMAND_OUTPUT_CHARS, run_command_chain, run_process_bounded_output, split_command_chain
from .personalization import build_personalization_prompt, normalize_context, normalize_vocabulary
from .http_safety import is_loopback_hostname
from .postprocessor import (
    DEFAULT_OPENAI_COMPATIBLE_MODEL,
    DEFAULT_OPENAI_COMPATIBLE_URL,
    MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
)
from .path_safety import (
    ExpectedTarget,
    ExpectedTargetKind,
    _TargetSnapshot,
    assert_fd_is_private_directory,
    assert_fd_is_regular_private_file,
    assert_safe_path_components,
    assert_no_symlink_ancestors,
    ensure_directory_without_following_symlinks,
    open_directory_without_following_symlinks,
    open_file_without_following_symlinks,
    replace_bytes_atomically_if_identity,
    unlink_file_if_identity,
    write_text_atomically_without_following_symlinks,
)


TRANSCRIBE_COMMAND_TIMEOUT_SECONDS = 900
MAX_TRANSCRIBER_ERROR_CHARS = 4096
MAX_OPENAI_URL_CHARS = 2048
MAX_AUDIO_FILE_BYTES = 200 * 1024 * 1024
MAX_AUDIO_PATH_CHARS = 240
MAX_AUDIO_STEM_CHARS = 120
MAX_LANGUAGE_CODE_CHARS = 64
MAX_TRANSCRIBER_TEXT_CHARS = 65_535
ALLOWED_AUDIO_EXTENSIONS = {".wav", ".m4a", ".flac", ".ogg", ".mp3", ".aac", ".webm"}
MAX_TRANSCRIPT_TEXT_CHARS = 1_000_000
MAX_TRANSCRIBER_JSON_BYTES = 1_000_000
TRANSCRIBER_OUTPUT_LOCK_NAME = ".speed-of-cinnamon-transcriber.lock"
PLACEHOLDER_TRANSCRIPTS = {"[speaking in foreign language]"}
OPENAI_TRANSCRIPTION_MODELS = {
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe-diarize",
    "whisper-1",
}
_SUPPORTED_TRANSCRIBER_BACKENDS = frozenset(
    {"auto", "command", "whisper", "whisper-cpp", "faster-whisper", "openai-compatible"}
)


class _TrustedTranscriptText(str):
    def __new__(
        cls,
        value: str,
        output_path: Path,
        output_stat: os.stat_result | None,
    ) -> "_TrustedTranscriptText":
        result = str.__new__(cls, value)
        result.output_path = output_path
        result.output_stat = output_stat
        return result


def _trusted_transcript_text(
    text: str,
    path: Path,
    output_stat: os.stat_result,
) -> _TrustedTranscriptText:
    if not stat_module.S_ISREG(output_stat.st_mode) or getattr(output_stat, "st_nlink", 1) != 1:
        raise TranscriptionError("transcript output is unsafe")
    return _TrustedTranscriptText(text, path, output_stat)


_AudioSnapshot = (
    tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
)


@dataclass(frozen=True)
class _CommandPreflight:
    segments: tuple[tuple[str, ...], ...]
    audio_positions: tuple[tuple[int, int], ...]
    audio_marker: str | None = None


@dataclass(frozen=True)
class _TranscriberPreflight:
    backend: str
    audio_snapshot: _AudioSnapshot | None
    command: _CommandPreflight | None = None
    resolved_command: str | None = None


def _validate_language_code(language: str) -> str:
    if isinstance(language, bool) or not isinstance(language, str):
        raise TranscriptionError("language must be text")
    if _contains_escaped_null(language):
        raise TranscriptionError("language contains invalid null byte")
    if _contains_http_header_control_chars(language):
        raise TranscriptionError("language contains invalid control character")
    normalized = _assert_text_length(language, field_name="language", max_chars=MAX_LANGUAGE_CODE_CHARS).strip()
    if not normalized:
        raise TranscriptionError("language must not be empty")
    return normalized

_COMMAND_TEMPLATE_PLACEHOLDER_RE = re.compile(
    r"\{(audio|language|text|output_base|output_dir|context|vocabulary|prompt)\}"
)


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


def _filtered_environment(base: dict[str, str] | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    for key in _BASE_ENV_KEYS:
        value = _coerce_environment_value(key)
        if value is not None:
            env[key] = value
    if base is not None:
        if not isinstance(base, dict):
            raise TranscriptionError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise TranscriptionError("environment keys must be text")
            if isinstance(value, bool):
                raise TranscriptionError("environment values must be text")
            if not isinstance(value, str):
                raise TranscriptionError("environment base must be a mapping")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise TranscriptionError("environment key contains invalid control character")
            if _contains_escaped_null(value) or _contains_http_header_control_chars(value):
                raise TranscriptionError("environment value contains invalid control character")
            if _is_unsafe_env_var(key):
                raise TranscriptionError(f"environment key is not allowed: {key}")
            env[key] = value
    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise TranscriptionError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise TranscriptionError("command must be a bare command name without path separators")
    resolution_failed = False
    resolved: str | None = None
    try:
        resolved = _which(command_name)
        if resolved:
            command_path = Path(resolved)
        else:
            resolution_failed = True
    except (OSError, RuntimeError, TypeError, ValueError):
        resolution_failed = True
    if resolution_failed or not resolved:
        raise TranscriptionError("transcriber executable is not available") from None
    return str(command_path)


def _require_whisper_command() -> str:
    try:
        return _command_path("whisper")
    except TranscriptionError:
        raise TranscriptionError("OpenAI whisper command is not installed") from None


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
    lowered = (value or "").lower()
    return "\x00" in lowered or "\\x00" in lowered or "\\u0000" in lowered


def _contains_multipart_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
    lowered = (value or "").lower()
    if _contains_escaped_null(value):
        return True
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


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("value must be text")
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


def _model_path_exists(path: str) -> bool:
    if isinstance(path, bool) or not isinstance(path, str):
        return False
    if _contains_escaped_null(path):
        return False
    if _contains_http_header_control_chars(path):
        return False
    try:
        return _local_model_path_kind(Path(path).expanduser(), field_name="whisper model path") is not None
    except (OSError, RuntimeError, ValueError, TranscriptionError):
        return False


def _local_model_path_kind(path: Path, *, field_name: str) -> str | None:
    try:
        path_stat = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise TranscriptionError(f"{field_name} is invalid") from exc
    if stat_module.S_ISLNK(path_stat.st_mode):
        raise TranscriptionError(f"{field_name} must not pass through a symlink")
    if stat_module.S_ISDIR(path_stat.st_mode):
        return "directory"
    if stat_module.S_ISREG(path_stat.st_mode):
        return "file"
    return "other"


def _validate_ctranslate2_model_tree(path: Path, *, field_name: str) -> None:
    def raise_walk_error(exc: OSError) -> None:
        raise exc

    try:
        for root, dirnames, filenames in os.walk(path, followlinks=False, onerror=raise_walk_error):
            root_path = Path(root)
            for name in dirnames:
                entry = root_path / name
                entry_kind = _local_model_path_kind(entry, field_name=field_name)
                if entry_kind != "directory":
                    raise TranscriptionError(f"{field_name} contains unsafe directory entries")
            for name in filenames:
                entry = root_path / name
                entry_kind = _local_model_path_kind(entry, field_name=field_name)
                if entry_kind != "file":
                    raise TranscriptionError(f"{field_name} contains unsafe file entries")
    except TranscriptionError:
        raise
    except OSError as exc:
        raise TranscriptionError(f"{field_name} is invalid") from exc


def _validate_local_model_path(value: str, *, field_name: str, directory: bool) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError(f"{field_name} must be text")
    if _contains_escaped_null(value):
        raise TranscriptionError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(value):
        raise TranscriptionError(f"{field_name} contains invalid control character")
    raw = value.strip()
    if not raw:
        raise TranscriptionError(f"{field_name} is required")
    try:
        path = Path(raw).expanduser()
    except (OSError, RuntimeError, ValueError) as exc:
        raise TranscriptionError(f"{field_name} is invalid") from exc
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    path_kind = _local_model_path_kind(path, field_name=field_name)
    if path_kind is None:
        raise TranscriptionError(f"{field_name} is missing")
    if directory:
        if path_kind != "directory":
            raise TranscriptionError(f"{field_name} must be a directory")
        _validate_ctranslate2_model_tree(path, field_name=field_name)
    elif path_kind != "file":
        raise TranscriptionError(f"{field_name} must be a file")
    return str(path)


def _write_text_atomic(path: Path, text: str) -> None:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    if isinstance(text, bool) or not isinstance(text, str):
        raise TranscriptionError("text must be text")
    try:
        write_text_atomically_without_following_symlinks(path, text, field_name="transcript path")
    except (OSError, RuntimeError) as exc:
        raise TranscriptionError("failed to write transcript file") from exc


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise TranscriptionError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TranscriptionError("max_chars must be an integer")
    if max_chars <= 0:
        raise TranscriptionError("max_chars must be positive")
    file.seek(0)
    try:
        text = file.read(max_chars).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TranscriptionError(f"command output is not valid UTF-8: {exc}") from exc
    if _contains_escaped_null(text):
        raise TranscriptionError("command output contains invalid null byte")
    return text


def _same_regular_file_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_mode == second.st_mode
        and getattr(first, "st_nlink", 1) == getattr(second, "st_nlink", 1)
    )


def _required_nofollow_flag(error_message: str) -> int:
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if isinstance(nofollow_flag, bool) or not isinstance(nofollow_flag, int) or nofollow_flag <= 0:
        raise TranscriptionError(error_message)
    return nofollow_flag


def _regular_file_stat(path: Path, *, field_name: str) -> os.stat_result | None:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    parent_fd: int | None = None
    fd: int | None = None
    nofollow_flag = _required_nofollow_flag("secure file inspection is not supported")
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
        parent_fd = open_directory_without_following_symlinks(
            path.parent,
            field_name=f"{field_name} directory",
        )
        try:
            fd = os.open(
                path.name,
                os.O_RDONLY | nofollow_flag | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return None
        assert_fd_is_regular_private_file(fd, field_name=field_name)
        return os.fstat(fd)
    except FileNotFoundError:
        return None
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    except OSError as exc:
        raise TranscriptionError(f"failed to inspect {field_name}") from exc
    finally:
        owned_fd = fd
        fd = None
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_owned_resources(
            (owned_fd, owned_parent_fd),
            primary_error=sys.exc_info()[1],
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )


def _read_text_file_with_target(
    path: Path,
    *,
    size_field_name: str | None = None,
) -> tuple[str, os.stat_result, ExpectedTarget]:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    parent_fd: int | None = None
    fd: int | None = None
    handle: io.BufferedReader | None = None
    nofollow_flag = _required_nofollow_flag("secure transcript read is not supported")
    value_error = False
    try:
        assert_no_symlink_ancestors(path, field_name="generated transcript path")
        parent_fd = open_directory_without_following_symlinks(
            path.parent,
            field_name="generated transcript directory",
        )
        fd = os.open(
            path.name,
            os.O_RDONLY | nofollow_flag | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        assert_fd_is_regular_private_file(fd, field_name="generated transcript path")
        output_stat = os.fstat(fd)
        handle = os.fdopen(fd, "rb")
        fd = None
        data = handle.read(MAX_TRANSCRIPT_TEXT_CHARS + 1)
        if len(data) > MAX_TRANSCRIPT_TEXT_CHARS:
            if size_field_name:
                raise TranscriptionError(
                    f"{size_field_name} is too large (max {MAX_TRANSCRIPT_TEXT_CHARS} bytes)"
                )
            raise TranscriptionError("failed to read generated transcript")
        text = data.decode("utf-8")
        if _contains_escaped_null(text):
            raise TranscriptionError("failed to read generated transcript")
        output_target = ExpectedTarget.captured(handle.fileno())
    except UnicodeDecodeError as exc:
        raise TranscriptionError("failed to read generated transcript") from exc
    except OSError as exc:
        raise TranscriptionError("failed to read generated transcript") from exc
    except TranscriptionError:
        raise
    except ValueError:
        value_error = True
    except RuntimeError as exc:
        raise TranscriptionError("failed to read generated transcript") from exc
    finally:
        owned_handle = handle
        handle = None
        owned_fd = fd
        fd = None
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_owned_resources(
            (owned_fd, owned_parent_fd),
            tuple(() if owned_handle is None else (owned_handle.close,)),
            primary_error=sys.exc_info()[1],
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )
    if value_error:
        raise TranscriptionError("failed to read generated transcript")
    return text, output_stat, output_target


def _read_text_file_with_stat(
    path: Path,
    *,
    size_field_name: str | None = None,
) -> tuple[str, os.stat_result]:
    text, output_stat, _output_target = _read_text_file_with_target(
        path,
        size_field_name=size_field_name,
    )
    return text, output_stat


def _read_text_file(path: Path, *, size_field_name: str | None = None) -> str:
    text, _output_stat = _read_text_file_with_stat(path, size_field_name=size_field_name)
    return text


def _file_state_from_stat(file_stat: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_size,
        getattr(file_stat, "st_mtime_ns", 0),
        getattr(file_stat, "st_ctime_ns", 0),
    )


def _snapshot_existing_file_with_state(
    path: Path,
) -> tuple[bytes, tuple[int, int, int, int, int, int], ExpectedTarget] | None:
    try:
        assert_no_symlink_ancestors(path, field_name="existing transcript path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    value_error = False

    def release_fd(primary_error: BaseException | None) -> None:
        nonlocal fd
        owned_fd = fd
        fd = None
        _release_owned_resources(
            (owned_fd,),
            primary_error=primary_error,
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )

    try:
        fd = open_file_without_following_symlinks(
            path,
            os.O_RDONLY | nonblock_flag,
            field_name="existing transcript path",
        )
        assert_fd_is_regular_private_file(fd, field_name="existing transcript path")
        before_stat = os.fstat(fd)
    except FileNotFoundError:
        release_fd(None)
        return None
    except OSError as exc:
        release_fd(exc)
        raise TranscriptionError("failed to snapshot existing transcript file") from exc
    except RuntimeError as exc:
        release_fd(exc)
        raise TranscriptionError("failed to snapshot existing transcript file") from exc
    except ValueError:
        release_fd(None)
        value_error = True
    if value_error:
        raise TranscriptionError("failed to snapshot existing transcript file")
    runtime_error = False
    try:
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_TRANSCRIPT_TEXT_CHARS + 1)
            after_stat = os.fstat(handle.fileno())
            expected_target = ExpectedTarget.captured(handle.fileno())
    except OSError as exc:
        release_fd(exc)
        raise TranscriptionError("failed to snapshot existing transcript file") from exc
    except ValueError:
        release_fd(None)
        value_error = True
    except RuntimeError:
        release_fd(None)
        runtime_error = True
    if value_error or runtime_error:
        raise TranscriptionError("failed to snapshot existing transcript file")
    if len(data) > MAX_TRANSCRIPT_TEXT_CHARS:
        raise TranscriptionError("existing transcript file is too large")
    if (
        not _same_regular_file_identity(before_stat, after_stat)
        or _file_state_from_stat(before_stat) != _file_state_from_stat(after_stat)
        or len(data) != before_stat.st_size
    ):
        raise TranscriptionError("existing transcript file changed while snapshotting")
    return data, _file_state_from_stat(after_stat), expected_target


def _snapshot_existing_file(path: Path) -> bytes | None:
    snapshot_with_state = _snapshot_existing_file_with_state(path)
    if snapshot_with_state is None:
        return None
    return snapshot_with_state[0]


def _file_state(path: Path) -> tuple[int, int, int, int, int, int] | None:
    file_stat = _regular_file_stat(path, field_name="generated transcript")
    if file_stat is None:
        return None
    return _file_state_from_stat(file_stat)


def _capture_expected_target(
    path: Path,
    *,
    field_name: str,
    max_digest_bytes: int | None = None,
) -> ExpectedTarget:
    fd: int | None = None
    try:
        fd = open_file_without_following_symlinks(
            path,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0),
            field_name=field_name,
        )
        assert_fd_is_regular_private_file(fd, field_name=field_name)
        return ExpectedTarget.captured(fd, max_digest_bytes=max_digest_bytes)
    except FileNotFoundError:
        return ExpectedTarget.missing()
    except (OSError, RuntimeError, ValueError):
        return ExpectedTarget.unknown()
    finally:
        owned_fd = fd
        fd = None
        _release_owned_resources(
            (owned_fd,),
            primary_error=sys.exc_info()[1],
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )


def _remove_staged_audio_file_after_mismatch(
    path: Path,
    *,
    expected_target: ExpectedTarget,
) -> None:
    initial_error: BaseException | None = None
    try:
        _remove_generated_transcript_file(
            path,
            field_name="staged audio file",
            expected_target=expected_target,
        )
        return
    except BaseException as exc:
        initial_error = exc
        if _is_non_retryable_cleanup_error(exc):
            raise
    current_target = _capture_expected_target(
        path,
        field_name="staged audio file",
        max_digest_bytes=MAX_AUDIO_FILE_BYTES,
    )
    if current_target.kind is ExpectedTargetKind.MISSING:
        return
    if current_target.kind is not ExpectedTargetKind.CAPTURED:
        if initial_error is None:
            raise TranscriptionError("failed to clean up staged audio file")
        raise initial_error
    _remove_generated_transcript_file(
        path,
        field_name="staged audio file",
        expected_target=current_target,
    )


def _same_expected_target_evidence(left: ExpectedTarget, right: ExpectedTarget) -> bool:
    if not isinstance(left, ExpectedTarget) or not isinstance(right, ExpectedTarget):
        return False
    return (
        left.kind is right.kind
        and left.snapshot == right.snapshot
        and left.require_same_version == right.require_same_version
        and left.content_digest == right.content_digest
    )


def _restore_existing_file_snapshot(
    path: Path,
    snapshot: bytes,
    *,
    expected_target: ExpectedTarget,
) -> None:
    _required_nofollow_flag("secure existing transcript open is not supported")
    underlying_error = False
    try:
        replace_bytes_atomically_if_identity(
            path,
            snapshot,
            expected_target,
            field_name="existing transcript path",
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        underlying_error = True
    if underlying_error:
        raise TranscriptionError("failed to restore existing transcript file")


def _remove_generated_transcript_file(
    path: Path,
    *,
    field_name: str = "generated transcript",
    expected_target: ExpectedTarget,
) -> None:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    if isinstance(field_name, bool) or not isinstance(field_name, str):
        raise TranscriptionError("field_name must be text")

    underlying_error = False
    try:
        unlink_file_if_identity(
            path,
            expected_target,
            field_name=field_name,
        )
    except (OSError, RuntimeError, TypeError, ValueError):
        underlying_error = True
    if underlying_error:
        raise TranscriptionError(f"failed to remove {field_name}")


def _restore_or_remove_generated_transcript(
    path: Path,
    snapshot: bytes | None,
    *,
    expected_target: ExpectedTarget,
    field_name: str = "generated transcript",
) -> None:
    if snapshot is not None:
        _restore_existing_file_snapshot(path, snapshot, expected_target=expected_target)
        return
    underlying_error = False
    try:
        _remove_generated_transcript_file(
            path,
            field_name=field_name,
            expected_target=expected_target,
        )
    except FileNotFoundError:
        return
    except (OSError, TranscriptionError):
        underlying_error = True
    if underlying_error:
        raise TranscriptionError("failed to remove generated transcript")


def _read_response_text(response: object, max_bytes: int = MAX_TRANSCRIBER_JSON_BYTES) -> str:
    if not hasattr(response, "read"):
        raise TranscriptionError("response must be readable")
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool):
        raise TranscriptionError("max response bytes must be an integer")
    if max_bytes < 0:
        raise TranscriptionError("max response bytes must be non-negative")
    chunks: list[bytes] = []
    total = 0
    read_failed = False
    while True:
        try:
            chunk = response.read(65536)
        except Exception:
            read_failed = True
            break
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            raise TranscriptionError("API response chunk must be bytes")
        total += len(chunk)
        if total > max_bytes:
            raise TranscriptionError(f"API response exceeded {max_bytes} bytes")
        chunks.append(chunk)
    if read_failed:
        raise TranscriptionError("API response read failed")
    decoded_response: str | None = None
    try:
        decoded_response = b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError:
        pass
    if decoded_response is None:
        raise TranscriptionError("API response is not valid UTF-8")
    return decoded_response


def _validate_http_request(request: urllib.request.Request, *, field_name: str) -> None:
    if not hasattr(request, "get_full_url"):
        raise TranscriptionError(f"{field_name} is not a valid request object")
    url = request.get_full_url()
    if not isinstance(url, str):
        raise TranscriptionError(f"{field_name} URL must be text")
    _validate_openai_compatible_api_url(url, field_name=field_name)


def _effective_url_port(parsed: urllib.parse.ParseResult) -> int | None:
    with suppress(ValueError):
        if parsed.port is not None:
            return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def _url_origin(url: str, *, field_name: str) -> tuple[str, str, int | None]:
    normalized = _validate_openai_compatible_api_url(url, field_name=field_name, allow_query_fragment=True)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname
    if not hostname:
        raise TranscriptionError(f"{field_name} is missing hostname")
    return parsed.scheme, hostname.lower(), _effective_url_port(parsed)


def _validate_same_origin_redirect(source_url: str, redirect_url: str, *, field_name: str) -> None:
    source_origin = _url_origin(source_url, field_name=field_name)
    redirect_origin = _url_origin(redirect_url, field_name=f"{field_name} redirect")
    if redirect_origin != source_origin:
        raise TranscriptionError(f"{field_name} redirect target changes origin")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        _validate_same_origin_redirect(req.get_full_url(), newurl, field_name="OpenAI-compatible speech request")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_http_request(request: urllib.request.Request, *, timeout: int, field_name: str) -> object:
    _validate_http_request(request, field_name=field_name)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler, urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)  # nosec B310


def _file_size(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise TranscriptionError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _run_transcriber_process(command: list[str], *, timeout: int, env: dict[str, str]) -> subprocess.CompletedProcess[bytes]:
    returncode, stdout_data, stderr_data = run_process_bounded_output(
        command,
        b"",
        timeout_seconds=timeout,
        max_output_bytes=MAX_COMMAND_OUTPUT_CHARS,
        env=env,
        label="transcriber",
    )
    return subprocess.CompletedProcess(command, returncode, stdout=stdout_data, stderr=stderr_data)


def _run_limited_process(command: list[str] | tuple[str, ...], *, timeout: int = TRANSCRIBE_COMMAND_TIMEOUT_SECONDS) -> None:
    if not isinstance(command, (list, tuple)):
        raise TranscriptionError("transcriber command must be a list or tuple")
    if not command:
        raise TranscriptionError("empty transcriber command is not allowed")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise TranscriptionError("timeout must be positive")
    if any(not isinstance(item, str) or isinstance(item, bool) for item in command):
        raise TranscriptionError("transcriber command items must be text")
    executable = command[0].strip()
    if not executable:
        raise TranscriptionError("empty transcriber executable is not allowed")
    if (
        _contains_escaped_null(executable)
        or any(_contains_escaped_null(arg) for arg in command[1:])
    ):
        raise TranscriptionError("command argument contains invalid null byte")
    if (
        _contains_http_header_control_chars(executable)
        or any(_contains_http_header_control_chars(arg) for arg in command[1:])
    ):
        raise TranscriptionError("command argument contains invalid control character")
    if os.path.sep in executable or (os.path.altsep and os.path.altsep in executable):
        raise TranscriptionError("command must be a bare command name without path separators")
    resolution_failed = False
    try:
        runtime_executable = _command_path(executable)
    except (OSError, RuntimeError, TypeError, ValueError):
        resolution_failed = True
    if resolution_failed:
        raise TranscriptionError("transcriber executable is not available") from None
    execution_error: str | None = None
    try:
        try:
            proc = _run_transcriber_process(
                [runtime_executable, *command[1:]],
                timeout=timeout,
                env=_filtered_environment(),
            )
        except FileNotFoundError:
            execution_error = "transcriber executable is not available"
        except CommandChainError as exc:
            execution_error = _sanitize_local_command_error(str(exc))
        except subprocess.TimeoutExpired:
            execution_error = f"transcription backend timed out after {timeout}s"
        except (OSError, RuntimeError, TypeError, ValueError):
            execution_error = "failed to run transcriber backend"
        else:
            if proc.returncode != 0:
                execution_error = f"transcriber command failed: exit code {proc.returncode}"
    except (OSError, RuntimeError, TypeError, ValueError):
        execution_error = "failed to run transcriber backend"
    if execution_error is not None:
        raise TranscriptionError(execution_error) from None


class TranscriptionError(RuntimeError):
    pass


class TranscriptionCleanupError(TranscriptionError):
    pass


class _OutputCleanupState(Enum):
    UNATTEMPTED = "unattempted"
    CAPTURED = "captured"
    MUTATING = "mutating"
    SUCCESS = "success"


class _OutputCleanupTracker:
    def __init__(self, paths: list[Path] | tuple[Path, ...] = ()) -> None:
        self._states = {path: _OutputCleanupState.UNATTEMPTED for path in paths}
        self._targets: dict[Path, ExpectedTarget] = {}
        self._capture_attempts: dict[Path, int] = {}
        self._mutation_attempts: dict[Path, int] = {}

    def capture(self, path: Path, *, field_name: str) -> ExpectedTarget:
        expected_target = self._targets.get(path)
        if expected_target is not None:
            return expected_target
        attempts = self._capture_attempts.get(path, 0)
        if attempts >= 2:
            raise TranscriptionCleanupError("failed to capture generated transcript target")
        self._capture_attempts[path] = attempts + 1
        expected_target = _capture_expected_target(path, field_name=field_name)
        if expected_target.kind is ExpectedTargetKind.UNKNOWN:
            raise TranscriptionError("failed to capture generated transcript target")
        self._targets[path] = expected_target
        self._states[path] = _OutputCleanupState.CAPTURED
        return expected_target

    def remember(self, path: Path, expected_target: ExpectedTarget) -> ExpectedTarget:
        current_target = self._targets.get(path)
        if current_target is not None:
            return current_target
        if not isinstance(expected_target, ExpectedTarget) or expected_target.kind is ExpectedTargetKind.UNKNOWN:
            raise TranscriptionError("failed to capture generated transcript target")
        self._targets[path] = expected_target
        self._states[path] = _OutputCleanupState.CAPTURED
        return expected_target

    def cleanup_once(
        self,
        path: Path,
        snapshot: bytes | None,
        *,
        field_name: str = "generated transcript",
    ) -> None:
        state = self._states.setdefault(path, _OutputCleanupState.UNATTEMPTED)
        if state is _OutputCleanupState.SUCCESS:
            return
        if state is _OutputCleanupState.MUTATING:
            raise TranscriptionCleanupError("failed to clean up generated transcript")
        expected_target = self.capture(path, field_name=field_name)
        attempts = self._mutation_attempts.get(path, 0)
        if attempts >= 2:
            raise TranscriptionCleanupError("failed to clean up generated transcript")
        self._mutation_attempts[path] = attempts + 1
        self._states[path] = _OutputCleanupState.MUTATING
        try:
            _restore_or_remove_generated_transcript(
                path,
                snapshot,
                expected_target=expected_target,
                field_name=field_name,
            )
        except BaseException:
            self._states[path] = _OutputCleanupState.CAPTURED
            raise
        else:
            self._states[path] = _OutputCleanupState.SUCCESS

    def cleanup_with_retry(
        self,
        path: Path,
        snapshot: bytes | None,
        *,
        field_name: str = "generated transcript",
    ) -> None:
        try:
            self.cleanup_once(path, snapshot, field_name=field_name)
        except Exception:
            self.cleanup_once(path, snapshot, field_name=field_name)


def _cleanup_output_candidates(
    tracker: _OutputCleanupTracker,
    candidates: list[Path] | tuple[Path, ...],
    snapshots: dict[Path, bytes | None],
    *,
    exclude: set[Path] | None = None,
    field_names: dict[Path, str] | None = None,
) -> list[BaseException]:
    cleanup_errors: list[BaseException] = []
    for candidate in candidates:
        if exclude is not None and candidate in exclude:
            continue
        try:
            tracker.cleanup_with_retry(
                candidate,
                snapshots.get(candidate),
                field_name=(field_names or {}).get(candidate, "generated transcript"),
            )
        except BaseException as exc:
            cleanup_errors.append(exc)
    return cleanup_errors


def _is_non_retryable_cleanup_error(error: BaseException) -> bool:
    return not isinstance(error, Exception) or isinstance(error, MemoryError)


def _safe_add_note(error: BaseException, note: str) -> None:
    if not isinstance(error, BaseException) or type(note) is not str:
        return
    if not note or len(note) > 256 or any(ord(char) < 0x20 or 0x7F <= ord(char) < 0xA0 for char in note):
        return
    try:
        add_note = getattr(error, "add_note")
        if callable(add_note):
            add_note(note)
    except BaseException:
        return


def _new_sanitized_cleanup_error(
    error: BaseException,
    *,
    message: str,
    note: str,
) -> BaseException:
    if isinstance(error, KeyboardInterrupt):
        sanitized: BaseException = KeyboardInterrupt("transcription cleanup interrupted")
    elif isinstance(error, SystemExit):
        sanitized = SystemExit("transcription cleanup interrupted")
    elif isinstance(error, GeneratorExit):
        sanitized = GeneratorExit("transcription cleanup interrupted")
    elif _is_non_retryable_cleanup_error(error):
        sanitized = TranscriptionError(message)
    else:
        sanitized = TranscriptionError(message)
    _safe_add_note(sanitized, note)
    return sanitized


def _release_owned_fd(fd: int | None, errors: list[BaseException]) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except BaseException as exc:
        errors.append(exc)


def _release_owned_closer(closer: object, errors: list[BaseException]) -> None:
    if closer is None:
        return
    try:
        if callable(closer):
            closer()
    except BaseException as exc:
        errors.append(exc)


def _release_owned_resources(
    fds: tuple[int | None, ...] = (),
    closers: tuple[object, ...] = (),
    *,
    primary_error: BaseException | None,
    message: str,
    note: str,
) -> None:
    errors: list[BaseException] = []
    for fd in fds:
        _release_owned_fd(fd, errors)
    for closer in closers:
        _release_owned_closer(closer, errors)
    _finish_fd_release_errors(
        errors,
        primary_error=primary_error,
        message=message,
        note=note,
    )


def _finish_fd_release_errors(
    errors: list[BaseException],
    *,
    primary_error: BaseException | None,
    message: str,
    note: str,
) -> None:
    if not errors:
        return
    if primary_error is not None:
        _safe_add_note(primary_error, note)
        return
    sanitized = _new_sanitized_cleanup_error(errors[0], message=message, note=note)
    raise sanitized from None


def _release_directory_fd(fd: int | None) -> None:
    _release_owned_resources(
        (fd,),
        primary_error=None,
        message="failed to release transcript directory",
        note="transcript directory release failed",
    )


def _raise_cleanup_errors(cleanup_errors: list[BaseException]) -> None:
    if any(_is_non_retryable_cleanup_error(cleanup_error) for cleanup_error in cleanup_errors):
        cleanup_error = next(
            cleanup_error
            for cleanup_error in cleanup_errors
            if _is_non_retryable_cleanup_error(cleanup_error)
        )
        sanitized = _new_sanitized_cleanup_error(
            cleanup_error,
            message="failed to clean up generated transcript",
            note="transcript cleanup failed",
        )
        raise sanitized from None
    raise TranscriptionCleanupError("failed to clean up generated transcript") from None


@contextmanager
def _transcriber_output_namespace_lock(parent: Path):
    if not isinstance(parent, Path):
        raise TranscriptionError("transcript output directory must be a Path")
    parent_fd: int | None = None
    lock_fd: int | None = None
    locked = False
    primary_error: BaseException | None = None
    release_errors: list[BaseException] = []
    try:
        try:
            nofollow_flag = _required_nofollow_flag(
                "secure transcript output lock is not supported"
            )
            cloexec_flag = getattr(os, "O_CLOEXEC", None)
            if isinstance(cloexec_flag, bool) or not isinstance(cloexec_flag, int) or cloexec_flag <= 0:
                raise TranscriptionError("secure transcript output lock is not supported")
            assert_no_symlink_ancestors(parent, field_name="transcript output directory")
            parent_fd = open_directory_without_following_symlinks(
                parent,
                field_name="transcript output directory",
            )
            assert_fd_is_private_directory(
                parent_fd,
                field_name="transcript output directory",
            )
            try:
                lock_fd = os.open(
                    TRANSCRIBER_OUTPUT_LOCK_NAME,
                    os.O_RDWR
                    | os.O_CREAT
                    | nofollow_flag
                    | cloexec_flag
                    | getattr(os, "O_NONBLOCK", 0),
                    0o600,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise TranscriptionError("transcript output lock is unsafe") from None
                raise TranscriptionError("failed to prepare transcript output lock") from None
            lock_stat = os.fstat(lock_fd)
            if (
                not stat_module.S_ISREG(lock_stat.st_mode)
                or getattr(lock_stat, "st_nlink", 1) != 1
                or stat_module.S_IMODE(lock_stat.st_mode) != 0o600
                or (hasattr(os, "getuid") and lock_stat.st_uid != os.getuid())
            ):
                raise TranscriptionError("transcript output lock is unsafe")
            try:
                os.set_inheritable(lock_fd, False)
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TranscriptionError("transcript output namespace is busy") from None
            except OSError:
                raise TranscriptionError("failed to acquire transcript output lock") from None
            locked = True
        except TranscriptionError as exc:
            primary_error = exc
        except (OSError, RuntimeError, ValueError):
            primary_error = TranscriptionError("failed to prepare transcript output lock")
        except BaseException as exc:
            primary_error = exc
        if primary_error is None:
            try:
                yield
            except BaseException as exc:
                primary_error = exc
    finally:
        if lock_fd is not None:
            if locked:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                except BaseException as exc:
                    release_errors.append(exc)
            owned_lock_fd = lock_fd
            lock_fd = None
            _release_owned_fd(owned_lock_fd, release_errors)
        if parent_fd is not None:
            owned_parent_fd = parent_fd
            parent_fd = None
            _release_owned_fd(owned_parent_fd, release_errors)
    if primary_error is not None:
        if release_errors:
            _safe_add_note(primary_error, "transcript output lock release failed")
        raise primary_error
    if release_errors:
        sanitized = _new_sanitized_cleanup_error(
            release_errors[0],
            message="failed to release transcript output lock",
            note="transcript output lock release failed",
        )
        raise sanitized from None


def _sanitized_transcription_interrupt() -> KeyboardInterrupt:
    return KeyboardInterrupt("transcription interrupted")


@dataclass(frozen=True)
class TranscriberConfig:
    backend: str = "auto"
    command_template: str = ""
    whisper_model: str = ""
    language: str = "en"


def _normalize_transcript_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TranscriptionError("text path must be a Path")
    try:
        normalized = path.expanduser()
        if not normalized.is_absolute():
            normalized = Path.cwd() / normalized
    except (OSError, RuntimeError, ValueError) as exc:
        raise TranscriptionError("text path is invalid") from exc
    return normalized


def _validate_audio_path_shape(path: Path) -> Path:
    if not isinstance(path, Path):
        raise TranscriptionError("audio path must be a Path")
    path_text = str(path)
    try:
        path_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TranscriptionError("audio path contains invalid UTF-8") from exc
    if _contains_escaped_null(path_text):
        raise TranscriptionError("audio path contains invalid null byte")
    if _contains_http_header_control_chars(path_text):
        raise TranscriptionError("audio path contains invalid control character")
    expansion_failed = False
    try:
        normalized = path.expanduser()
        if not normalized.is_absolute():
            normalized = Path.cwd() / normalized
    except (OSError, RuntimeError, ValueError):
        expansion_failed = True
    if expansion_failed:
        raise TranscriptionError("audio path is invalid")
    encoding_failed = False
    try:
        str(normalized).encode("utf-8")
    except UnicodeEncodeError:
        encoding_failed = True
    if encoding_failed:
        raise TranscriptionError("audio path contains invalid UTF-8")
    path_safety_error: str | None = None
    try:
        assert_safe_path_components(normalized, field_name="audio path")
        assert_no_symlink_ancestors(normalized, field_name="audio path")
    except RuntimeError as exc:
        if "symlink" in str(exc).lower():
            path_safety_error = "audio path must not pass through a symlink"
        elif "unsafe path component" in str(exc).lower():
            path_safety_error = "audio path contains unsafe path component"
        else:
            path_safety_error = "audio path is invalid"
    if path_safety_error is not None:
        raise TranscriptionError(path_safety_error)
    if normalized.is_symlink():
        raise TranscriptionError("audio path must not be a symlink")
    if len(str(normalized)) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError("audio file path is too long")
    try:
        normalized_bytes = str(normalized).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TranscriptionError("audio path contains invalid UTF-8") from None
    if len(normalized_bytes) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError("audio file path is too long")
    if len(normalized.name) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError("audio file name is too long")
    try:
        normalized_name_bytes = normalized.name.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TranscriptionError("audio path contains invalid UTF-8") from None
    if len(normalized_name_bytes) > MAX_AUDIO_PATH_CHARS:
        raise TranscriptionError("audio file name is too long")
    if len(normalized.stem) > MAX_AUDIO_STEM_CHARS:
        raise TranscriptionError("audio file stem is too long")
    try:
        normalized_stem_bytes = normalized.stem.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TranscriptionError("audio path contains invalid UTF-8") from None
    if len(normalized_stem_bytes) > MAX_AUDIO_STEM_CHARS:
        raise TranscriptionError("audio file stem is too long")
    return normalized


def _validate_audio_extension(path: Path) -> None:
    if path.suffix.lower() not in ALLOWED_AUDIO_EXTENSIONS:
        raise TranscriptionError(f"unsupported audio extension: {path.suffix}")


def validate_audio_file(path: Path) -> Path:
    normalized = _validate_audio_path_shape(path)
    stat_failed = False
    try:
        stat_result = normalized.stat()
    except OSError:
        stat_failed = True
    if stat_failed:
        raise TranscriptionError("audio file is missing or empty")
    if not stat_module.S_ISREG(stat_result.st_mode):
        raise TranscriptionError("audio path is not a regular file")
    _validate_audio_extension(normalized)
    if stat_result.st_size == 0:
        raise TranscriptionError("audio file is missing or empty")
    if stat_result.st_size > MAX_AUDIO_FILE_BYTES:
        raise TranscriptionError("audio file is too large")
    return normalized


def _read_private_file_bytes(
    path: Path,
    *,
    field_name: str,
    max_bytes: int | None = None,
    expected_snapshot: tuple[int, int, int, int, int, int, str] | tuple[int, int, int, int, int, int] | None = None,
) -> bytes:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0):
        raise TranscriptionError("max_bytes must be a non-negative integer")
    effective_max_bytes = MAX_AUDIO_FILE_BYTES if max_bytes is None else max_bytes
    if isinstance(field_name, bool) or not isinstance(field_name, str):
        raise TranscriptionError("field_name must be text")
    expected_snapshot_metadata: tuple[int, int, int, int, int, int] | None = None
    expected_snapshot_digest: str | None = None
    if expected_snapshot is not None:
        if (
            not isinstance(expected_snapshot, tuple)
            or len(expected_snapshot) not in (6, 7)
            or any(isinstance(part, bool) or not isinstance(part, int) for part in expected_snapshot[:6])
        ):
            raise TranscriptionError(f"failed to read {field_name}")
        expected_snapshot_metadata = expected_snapshot[:6]
        if len(expected_snapshot) == 7:
            if not isinstance(expected_snapshot[6], str) or isinstance(expected_snapshot[6], bool):
                raise TranscriptionError(f"failed to read {field_name}")
            expected_snapshot_digest = expected_snapshot[6]
    nofollow_flag = _required_nofollow_flag(f"secure {field_name} open is not supported on this platform")
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    parent_fd: int | None = None
    handle: io.BufferedReader | None = None

    def _snapshot_fd(fd: int) -> tuple[tuple[int, int, int, int, int, int], int]:
        file_stat = os.fstat(fd)
        return (
            (
                file_stat.st_dev,
                file_stat.st_ino,
                file_stat.st_mode,
                getattr(file_stat, "st_nlink", 1),
                file_stat.st_size,
                getattr(file_stat, "st_mtime_ns", 0),
            ),
            getattr(file_stat, "st_ctime_ns", 0),
        )

    def release_open_fds(primary_error: BaseException | None) -> None:
        nonlocal fd, parent_fd
        owned_fd = fd
        fd = None
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_owned_resources(
            (owned_fd, owned_parent_fd),
            primary_error=primary_error,
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )

    read_setup_failed = False
    try:
        parent_fd = open_directory_without_following_symlinks(path.parent, field_name=f"{field_name} directory")
        fd = os.open(path.name, os.O_RDONLY | nofollow_flag | nonblock_flag, dir_fd=parent_fd)
        assert_fd_is_regular_private_file(fd, field_name=field_name)
        observed_snapshot, observed_ctime_ns = _snapshot_fd(fd)
        if expected_snapshot_metadata is not None and observed_snapshot != expected_snapshot_metadata:
            raise TranscriptionError(f"{field_name} changed between validation and read")
    except OSError:
        release_open_fds(None)
        read_setup_failed = True
    except TranscriptionError as exc:
        release_open_fds(exc)
        raise
    except RuntimeError:
        release_open_fds(None)
        read_setup_failed = True
    if read_setup_failed:
        raise TranscriptionError(f"failed to read {field_name}")
    try:
        handle = os.fdopen(fd, "rb")
        fd = None
    except OSError:
        release_open_fds(None)
        raise TranscriptionError(f"failed to read {field_name}")
    read_failed = False
    try:
        hasher = hashlib.sha256() if expected_snapshot_digest is not None else None
        data = handle.read(effective_max_bytes + 1)
        if hasher is not None:
            hasher.update(data)
        final_snapshot, final_ctime_ns = _snapshot_fd(handle.fileno())
        if (
            final_snapshot != observed_snapshot
            or final_ctime_ns != observed_ctime_ns
            or (
                expected_snapshot_metadata is not None
                and final_snapshot != expected_snapshot_metadata
            )
        ):
            raise TranscriptionError(f"{field_name} changed between validation and read")
        if len(data) > effective_max_bytes:
            raise TranscriptionError(f"{field_name} is too large")
        if hasher is not None and hasher.hexdigest() != expected_snapshot_digest:
            raise TranscriptionError(f"{field_name} changed between validation and read")
    except (OSError, ValueError):
        read_failed = True
    finally:
        owned_handle = handle
        handle = None
        owned_fd = fd
        fd = None
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_owned_resources(
            (owned_fd, owned_parent_fd),
            tuple(() if owned_handle is None else (owned_handle.close,)),
            primary_error=sys.exc_info()[1],
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )
    if read_failed:
        raise TranscriptionError(f"failed to read {field_name}")
    return data


def _snapshot_private_file(
    path: Path,
    *,
    field_name: str,
    include_hash: bool = False,
) -> tuple[int, int, int, int, int, int] | tuple[int, int, int, int, int, int, str]:
    if not isinstance(path, Path):
        raise TranscriptionError("path must be a Path")
    if isinstance(field_name, bool) or not isinstance(field_name, str):
        raise TranscriptionError("field_name must be text")
    path_safety_failed = False
    try:
        assert_no_symlink_ancestors(path, field_name=field_name)
    except RuntimeError:
        path_safety_failed = True
    if path_safety_failed:
        raise TranscriptionError(f"failed to snapshot {field_name}")
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    fd: int | None = None
    value_error = False
    snapshot_error = False
    try:
        fd = open_file_without_following_symlinks(path, os.O_RDONLY | nonblock_flag, field_name=field_name)
        assert_fd_is_regular_private_file(fd, field_name=field_name)
        file_stat = os.fstat(fd)
        if include_hash and file_stat.st_size > MAX_AUDIO_FILE_BYTES:
            raise TranscriptionError(
                f"audio file is too large: {file_stat.st_size} bytes (max {MAX_AUDIO_FILE_BYTES})"
            )
        snapshot = (
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_mode,
            getattr(file_stat, "st_nlink", 1),
            file_stat.st_size,
            getattr(file_stat, "st_mtime_ns", 0),
        )
        if not include_hash:
            return snapshot
        hash_state = hashlib.sha256()
        with os.fdopen(fd, "rb") as handle:
            fd = None
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                hash_state.update(chunk)
            final_stat = os.fstat(handle.fileno())
        if (
            not _same_regular_file_identity(file_stat, final_stat)
            or file_stat.st_size != final_stat.st_size
            or getattr(file_stat, "st_mtime_ns", 0) != getattr(final_stat, "st_mtime_ns", 0)
            or getattr(file_stat, "st_ctime_ns", 0) != getattr(final_stat, "st_ctime_ns", 0)
        ):
            raise TranscriptionError("audio file changed while snapshotting")
        return (*snapshot, hash_state.hexdigest())
    except TranscriptionError:
        raise
    except OSError:
        snapshot_error = True
    except RuntimeError:
        snapshot_error = True
    except ValueError:
        value_error = True
    finally:
        owned_fd = fd
        fd = None
        _release_owned_resources(
            (owned_fd,),
            primary_error=sys.exc_info()[1],
            message="failed to release file descriptor",
            note="file descriptor release failed",
        )
    if value_error or snapshot_error:
        raise TranscriptionError(f"failed to snapshot {field_name}")


@contextmanager
def _staged_audio_file_for_local_backend(
    audio_path: Path,
    *,
    expected_snapshot: tuple[int, int, int, int, int, int] | tuple[int, int, int, int, int, int, str] | None = None,
):
    nonblock_flag = getattr(os, "O_NONBLOCK", 0)
    nofollow_flag = _required_nofollow_flag("failed to stage audio file for backend access")
    if expected_snapshot is None:
        expected_snapshot = _snapshot_private_file(
            audio_path,
            field_name="audio file for backend",
            include_hash=True,
        )
    expected_snapshot = _validate_expected_audio_snapshot(
        expected_snapshot,
        error_message="failed to stage audio file for backend access",
    )
    expected_snapshot_metadata = expected_snapshot[:6]
    expected_snapshot_digest = None
    if len(expected_snapshot) == 7:
        if not isinstance(expected_snapshot[6], str) or isinstance(expected_snapshot[6], bool):
            raise TranscriptionError("failed to stage audio file for backend access")
        expected_snapshot_digest = expected_snapshot[6]
    if expected_snapshot_metadata[4] > MAX_AUDIO_FILE_BYTES:
        raise TranscriptionError(
            f"audio file is too large: {expected_snapshot_metadata[4]} bytes (max {MAX_AUDIO_FILE_BYTES})"
        )
    source_fd: int | None = None
    parent_fd: int | None = None
    staging_dir: Path | None = None
    staging_path: Path | None = None
    staging_stat: os.stat_result | None = None
    staging_target = ExpectedTarget.unknown()
    target_fd: int | None = None
    staging_hasher = hashlib.sha256()
    body_error: BaseException | None = None
    value_error = False
    stage_error = False
    close_errors: list[BaseException] = []
    target_capture_started = False

    def release_fd(fd: int | None) -> None:
        _release_owned_fd(fd, close_errors)

    try:
        staging_dir = Path(tempfile.mkdtemp(prefix=".sc-audio-"))
        staging_path = staging_dir / audio_path.name
        try:
            parent_fd = open_directory_without_following_symlinks(audio_path.parent, field_name="audio file directory")
            source_fd = os.open(audio_path.name, os.O_RDONLY | nofollow_flag | nonblock_flag, dir_fd=parent_fd)
            assert_fd_is_regular_private_file(source_fd, field_name="audio file for backend")
            source_stat = os.fstat(source_fd)
            source_snapshot = (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_mode,
                getattr(source_stat, "st_nlink", 1),
                source_stat.st_size,
                getattr(source_stat, "st_mtime_ns", 0),
            )
            if source_snapshot != expected_snapshot_metadata:
                raise TranscriptionError("audio file changed between validation and copy")
            target_fd = os.open(
                staging_path,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | nofollow_flag | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            staging_stat = os.fstat(target_fd)
            with os.fdopen(target_fd, "wb", closefd=False) as target:
                while True:
                    chunk = os.read(source_fd, 65536)
                    if not chunk:
                        break
                    staging_hasher.update(chunk)
                    target.write(chunk)
                target.flush()
                staging_stat = os.fstat(target.fileno())
                target_capture_started = True
            final_source_stat = os.fstat(source_fd)
            if (
                not _same_regular_file_identity(source_stat, final_source_stat)
                or source_stat.st_size != final_source_stat.st_size
                or getattr(source_stat, "st_mtime_ns", 0) != getattr(final_source_stat, "st_mtime_ns", 0)
                or getattr(source_stat, "st_ctime_ns", 0) != getattr(final_source_stat, "st_ctime_ns", 0)
            ):
                raise TranscriptionError("audio file changed between validation and copy")
            target_snapshot = _TargetSnapshot(
                staging_stat.st_dev,
                staging_stat.st_ino,
                stat_module.S_IFMT(staging_stat.st_mode),
                getattr(staging_stat, "st_nlink", 1),
                staging_stat.st_size,
                getattr(staging_stat, "st_mtime_ns", 0),
                getattr(staging_stat, "st_ctime_ns", 0),
            )
            staging_target = ExpectedTarget(
                ExpectedTargetKind.CAPTURED,
                target_snapshot,
                True,
                staging_hasher.digest(),
                MAX_AUDIO_FILE_BYTES,
            )
            if expected_snapshot_digest is not None and staging_hasher.hexdigest() != expected_snapshot_digest:
                raise TranscriptionError("audio file changed between validation and copy")
            if staging_stat.st_size == 0:
                raise TranscriptionError("audio file is missing or empty")
        except TranscriptionError:
            stage_error = True
        except ValueError as exc:
            if target_capture_started:
                value_error = True
            else:
                body_error = exc
        except Exception:
            stage_error = True
        except BaseException as exc:
            body_error = exc
        finally:
            owned_target_fd = target_fd
            target_fd = None
            release_fd(owned_target_fd)
            owned_source_fd = source_fd
            source_fd = None
            release_fd(owned_source_fd)
            owned_parent_fd = parent_fd
            parent_fd = None
            release_fd(owned_parent_fd)
        if value_error or stage_error:
            body_error = TranscriptionError("failed to stage audio file for backend access")
        if close_errors:
            if body_error is None:
                _finish_fd_release_errors(
                    close_errors,
                    primary_error=None,
                    message="failed to close staged audio file",
                    note="staged audio close failed",
                )
            else:
                _finish_fd_release_errors(
                    close_errors,
                    primary_error=body_error,
                    message="failed to close staged audio file",
                    note="staged audio close failed",
                )
        if body_error is not None:
            raise body_error
        if staging_path is None or staging_dir is None:
            raise TranscriptionError("failed to stage audio file for backend access")
        if staging_stat is None:
            raise TranscriptionError("failed to inspect staged audio file")
        if not stat_module.S_ISREG(staging_stat.st_mode):
            raise TranscriptionError("failed to stage audio file for backend access")
        if getattr(staging_stat, "st_nlink", 1) != 1:
            raise TranscriptionError("failed to stage audio file for backend access")
        if stat_module.S_IMODE(staging_stat.st_mode) != 0o600:
            raise TranscriptionError("failed to stage audio file for backend access")
        try:
            yield staging_path
        except BaseException as exc:
            body_error = exc
            raise
    finally:
        cleanup_errors: list[tuple[str, BaseException]] = []
        if staging_path is not None:
            try:
                try:
                    os.lstat(staging_path)
                except FileNotFoundError:
                    staging_path_exists = False
                except OSError:
                    staging_path_exists = False
                    cleanup_errors.append(
                        (
                            "staged audio file",
                            TranscriptionError("failed to inspect staged audio file"),
                        )
                    )
                else:
                    staging_path_exists = True
                if staging_path_exists:
                    if (
                        staging_stat is None
                        or staging_target.kind is not ExpectedTargetKind.CAPTURED
                    ):
                        cleanup_errors.append(
                            (
                                "staged audio file",
                                TranscriptionError("failed to clean up staged audio file"),
                            )
                        )
                    else:
                        _remove_staged_audio_file_after_mismatch(
                            staging_path,
                            expected_target=staging_target,
                        )
            except FileNotFoundError:
                pass
            except BaseException as exc:
                cleanup_errors.append(("staged audio file", exc))
        if staging_dir is not None:
            try:
                staging_dir.rmdir()
            except FileNotFoundError:
                pass
            except BaseException as exc:
                cleanup_errors.append(("staged audio directory", exc))
        if cleanup_errors:
            cleanup_error_message = "failed to clean up " + ", ".join(
                label for label, _error in cleanup_errors
            )
            if body_error is None:
                interrupt_error = next(
                    (error for _label, error in cleanup_errors if _is_non_retryable_cleanup_error(error)),
                    None,
                )
                if interrupt_error is not None:
                    sanitized = _new_sanitized_cleanup_error(
                        interrupt_error,
                        message=cleanup_error_message,
                        note=cleanup_error_message,
                    )
                    raise sanitized from None
                raise TranscriptionError(cleanup_error_message) from None
            _safe_add_note(body_error, cleanup_error_message)


def _validate_expected_audio_snapshot(
    value: object,
    *,
    error_message: str,
) -> tuple[int, int, int, int, int, int] | tuple[int, int, int, int, int, int, str]:
    if (
        not isinstance(value, tuple)
        or len(value) not in (6, 7)
        or any(isinstance(part, bool) or not isinstance(part, int) for part in value[:6])
    ):
        raise TranscriptionError(error_message)
    if len(value) == 7 and (not isinstance(value[6], str) or isinstance(value[6], bool)):
        raise TranscriptionError(error_message)
    return value


def _prepare_local_backend_audio(
    path: Path,
    *,
    expected_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None,
    field_name: str,
) -> tuple[Path, tuple[int, int, int, int, int, int] | tuple[int, int, int, int, int, int, str]]:
    if expected_snapshot is None:
        normalized = validate_audio_file(path)
        snapshot = _snapshot_private_file(
            normalized,
            field_name=field_name,
            include_hash=True,
        )
        return normalized, snapshot
    normalized = _validate_audio_path_shape(path)
    _validate_audio_extension(normalized)
    snapshot = _validate_expected_audio_snapshot(
        expected_snapshot,
        error_message=f"failed to snapshot {field_name}",
    )
    return normalized, snapshot


def _validate_audio_file_for_upload(
    path: Path,
    *,
    expected_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
    _metadata_only: bool = True,
) -> tuple[Path, _AudioSnapshot]:
    normalized = _validate_audio_path_shape(path)
    if expected_snapshot is None:
        snapshot = _snapshot_private_file(
            normalized,
            field_name="audio file for API upload",
            include_hash=not _metadata_only,
        )
    else:
        snapshot = _validate_expected_audio_snapshot(
            expected_snapshot,
            error_message="failed to validate audio file for API upload",
        )
    if not stat_module.S_ISREG(snapshot[2]):
        raise TranscriptionError("audio path is not a regular file")
    _validate_audio_extension(normalized)
    if snapshot[4] == 0:
        raise TranscriptionError("audio file is missing or empty")
    if snapshot[4] > MAX_AUDIO_FILE_BYTES:
        raise TranscriptionError("audio file is too large")
    return normalized, snapshot


def _assert_text_length(value: str, *, field_name: str, max_chars: int | None = None) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError(f"{field_name} must be text")
    if max_chars is None:
        max_chars = MAX_TRANSCRIPT_TEXT_CHARS
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise TranscriptionError("max_chars must be an integer")
    if max_chars <= 0:
        raise TranscriptionError("max_chars must be positive")
    if _contains_escaped_null(value):
        raise TranscriptionError(f"{field_name} contains invalid null byte")
    if len(value) > max_chars:
        raise TranscriptionError(f"{field_name} is too large (max {max_chars} characters)")
    try:
        encoded_value = value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise TranscriptionError(f"{field_name} contains invalid UTF-8") from exc
    if len(encoded_value) > max_chars:
        raise TranscriptionError(f"{field_name} is too large (max {max_chars} bytes)")
    return value


def _validate_write_transcript(value: bool) -> bool:
    if not isinstance(value, bool):
        raise TranscriptionError("write_transcript must be a boolean")
    return value


def _reject_placeholder_transcript(text: str, language: str) -> None:
    normalized = " ".join(text.strip().lower().split())
    if normalized in PLACEHOLDER_TRANSCRIPTS:
        raise TranscriptionError(
            f"transcriber detected speech outside configured language {language}; "
            "switch the applet language or use a multilingual model"
        )


def _quote(value: Path | str) -> str:
    if isinstance(value, bool) or not isinstance(value, (Path, str)):
        raise TranscriptionError("value must be text")
    return shlex.quote(str(value))


def render_command_template(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
    ) -> str:
    return _render_command_template(
        template,
        audio_path,
        language,
        text_path,
        personal_context,
        vocabulary,
        audio_value=audio_path,
    )


def _render_command_template(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
    *,
    audio_value: Path | str,
) -> str:
    if isinstance(template, bool) or not isinstance(template, str):
        raise TranscriptionError("template must be text")
    template = _assert_text_length(
        template,
        field_name="command template",
        max_chars=MAX_TRANSCRIBER_TEXT_CHARS,
    )
    if not isinstance(text_path, Path):
        raise TranscriptionError("text path must be a Path")
    language = _validate_language_code(language)
    try:
        output_base = text_path.with_suffix("")
        replacements = {
            "audio": _quote(audio_value),
            "language": _quote(language),
            "text": _quote(text_path),
            "output_base": _quote(output_base),
            "output_dir": _quote(text_path.parent),
            "context": _quote(normalize_context(personal_context)),
            "vocabulary": _quote(normalize_vocabulary(vocabulary)),
            "prompt": _quote(build_personalization_prompt(personal_context, vocabulary)),
        }
    except ValueError as exc:
        raise TranscriptionError(str(exc)) from exc
    return _COMMAND_TEMPLATE_PLACEHOLDER_RE.sub(lambda match: replacements[match.group(1)], template)


def _render_command_template_with_audio_provenance(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
) -> tuple[str, str]:
    marker = f"__speed_of_cinnamon_audio_{uuid.uuid4().hex}__"
    return (
        _render_command_template(
            template,
            audio_path,
            language,
            text_path,
            personal_context,
            vocabulary,
            audio_value=marker,
        ),
        marker,
    )


def transcribe_with_template(
    template: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    personal_context: str = "",
    vocabulary: str = "",
    *,
    _expected_audio_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
    _preflight_command: _CommandPreflight | None = None,
) -> str:
    if isinstance(template, bool) or not isinstance(template, str):
        raise TranscriptionError("template must be text")
    template = _assert_text_length(
        template,
        field_name="command template",
        max_chars=MAX_TRANSCRIBER_TEXT_CHARS,
    )
    language = _validate_language_code(language)
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise TranscriptionError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise TranscriptionError("vocabulary must be text")
    try:
        personal_context = normalize_context(personal_context)
        vocabulary = normalize_vocabulary(vocabulary)
    except ValueError as exc:
        raise TranscriptionError(str(exc)) from None
    text_path = _normalize_transcript_path(text_path)
    audio_path, audio_snapshot = _prepare_local_backend_audio(
        audio_path,
        expected_snapshot=_expected_audio_snapshot,
        field_name="audio file for backend",
    )
    if _preflight_command is None:
        command, audio_marker = _render_command_template_with_audio_provenance(
            template,
            audio_path,
            language,
            text_path,
            personal_context,
            vocabulary,
        )
        command_preflight = _preflight_transcriber_command(
            command,
            audio_marker=audio_marker,
            require_audio_binding="{audio}" in template,
        )
    else:
        command_preflight = _preflight_command
    generated_path: Path | None = None
    if "{text}" in template or "{output_base}" in template:
        generated_path = text_path
    elif "{output_dir}" in template:
        # OpenAI Whisper writes <audio stem>.txt when only --output_dir is supplied.
        generated_path = text_path.parent / f"{audio_path.stem}.txt"
    generated_field_name = "transcript path" if generated_path == text_path else "generated transcript path"
    if generated_path is not None:
        try:
            assert_no_symlink_ancestors(generated_path, field_name=generated_field_name)
        except RuntimeError as exc:
            raise TranscriptionError(str(exc)) from exc
        try:
            parent_fd = ensure_directory_without_following_symlinks(
                generated_path.parent,
                field_name=f"{generated_field_name} directory",
            )
        except OSError as exc:
            raise TranscriptionError("failed to prepare transcript directory") from exc
        else:
            owned_parent_fd = parent_fd
            parent_fd = None
            _release_directory_fd(owned_parent_fd)
    existing_snapshot_with_state = (
        _snapshot_existing_file_with_state(generated_path) if generated_path is not None else None
    )
    if existing_snapshot_with_state is None:
        existing_snapshot = None
        existing_state = None
    else:
        existing_snapshot, existing_state, _pre_backend_target = existing_snapshot_with_state
    cleanup_tracker = _OutputCleanupTracker(
        (generated_path,) if generated_path is not None else ()
    )

    def restore_text_path() -> None:
        if generated_path is None:
            return
        cleanup_tracker.cleanup_with_retry(
            generated_path,
            existing_snapshot,
            field_name=generated_field_name,
        )

    success_cleanup_failed = False

    def restore_text_path_for_success() -> None:
        nonlocal success_cleanup_failed
        cleanup_error: BaseException | None = None
        try:
            restore_text_path()
        except BaseException as exc:
            success_cleanup_failed = True
            cleanup_error = exc
        if cleanup_error is not None:
            _raise_cleanup_errors([cleanup_error])

    command_error: TranscriptionError | None = None
    try:
        with _staged_audio_file_for_local_backend(audio_path, expected_snapshot=audio_snapshot) as staged_audio_path:
            staged_segments = _bind_staged_transcriber_command(command_preflight, staged_audio_path)
            output = run_command_chain(
                staged_segments,
                "",
                label="transcriber",
                timeout_seconds=TRANSCRIBE_COMMAND_TIMEOUT_SECONDS,
                max_output_chars=MAX_COMMAND_OUTPUT_CHARS,
                personal_context=personal_context,
                vocabulary=vocabulary,
            )
    except BaseException as exc:
        cleanup_failed = False
        try:
            restore_text_path()
        except BaseException:
            cleanup_failed = True
            _safe_add_note(exc, "transcript cleanup failed")
        if isinstance(exc, CommandChainError):
            command_error = TranscriptionError(_sanitize_local_command_error(str(exc)))
            if cleanup_failed:
                _safe_add_note(command_error, "transcript cleanup failed")
        else:
            raise
    if command_error is not None:
        raise command_error

    try:
        if generated_path is not None:
            current_state = _file_state(generated_path)
            if current_state is not None:
                if existing_state is not None and current_state == existing_state:
                    raise TranscriptionError("transcriber completed but did not update the transcript file")
                output, output_stat, output_target = _read_text_file_with_target(
                    generated_path,
                    size_field_name="transcript file text",
                )
                cleanup_tracker.remember(generated_path, output_target)
                output = _assert_text_length(output.strip(), field_name="transcript file text")
                if generated_path != text_path:
                    restore_text_path_for_success()
                if generated_path == text_path:
                    return _trusted_transcript_text(output, text_path, output_stat)
                return output
            if existing_snapshot is not None:
                restore_text_path_for_success()
        return _assert_text_length(output, field_name="transcript")
    except BaseException as exc:
        if not success_cleanup_failed:
            try:
                restore_text_path()
            except BaseException:
                _safe_add_note(exc, "transcript cleanup failed")
        raise


def transcribe_with_openai_whisper(
    audio_path: Path,
    language: str,
    text_path: Path,
    write_transcript: bool = True,
    *,
    _expected_audio_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
    _skip_backend_preflight: bool = False,
) -> str:
    write_transcript = _validate_write_transcript(write_transcript)
    language = _validate_language_code(language)
    audio_path, audio_snapshot = _prepare_local_backend_audio(
        audio_path,
        expected_snapshot=_expected_audio_snapshot,
        field_name="audio file for backend",
    )
    text_path = _normalize_transcript_path(text_path)
    try:
        assert_no_symlink_ancestors(text_path, field_name="transcript path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    if not _skip_backend_preflight:
        _require_whisper_command()
    try:
        parent_fd = ensure_directory_without_following_symlinks(text_path.parent, field_name="transcript directory")
    except OSError as exc:
        raise TranscriptionError("failed to prepare transcript directory") from exc
    else:
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_directory_fd(owned_parent_fd)
    output_dir = text_path.parent
    generated = output_dir / f"{audio_path.stem}.txt"
    existing_snapshot_with_state = _snapshot_existing_file_with_state(generated)
    if existing_snapshot_with_state is None:
        existing_snapshot = None
        generated_state = None
    else:
        existing_snapshot, generated_state, _pre_backend_target = existing_snapshot_with_state
    cleanup_tracker = _OutputCleanupTracker((generated,))
    cleanup_snapshots = {generated: existing_snapshot}
    with _staged_audio_file_for_local_backend(audio_path, expected_snapshot=audio_snapshot) as staged_audio_path:
        primary_error: BaseException | None = None
        try:
            _run_limited_process(
                [
                    "whisper",
                    str(staged_audio_path),
                    "--language",
                    language,
                    "--output_format",
                    "txt",
                    "--output_dir",
                    str(output_dir),
                ],
            )
        except BaseException as exc:
            primary_error = exc
            cleanup_errors = _cleanup_output_candidates(
                cleanup_tracker,
                (generated,),
                cleanup_snapshots,
            )
            if cleanup_errors:
                _safe_add_note(exc, "transcript cleanup failed")
        if primary_error is not None:
            raise primary_error
        generated_target = cleanup_tracker.capture(
            generated,
            field_name="generated transcript",
        )
        if generated_target.kind is ExpectedTargetKind.CAPTURED:
            if generated_state is not None and _file_state(generated) == generated_state:
                raise TranscriptionError("whisper completed but did not produce a transcript")
            if generated == text_path:
                try:
                    text, output_stat, output_target = _read_text_file_with_target(
                        generated,
                        size_field_name="transcript",
                    )
                    if not _same_expected_target_evidence(output_target, generated_target):
                        raise TranscriptionError("generated transcript changed before read")
                    text = text.strip()
                    _assert_text_length(text, field_name="transcript")
                except BaseException as exc:
                    cleanup_errors = _cleanup_output_candidates(
                        cleanup_tracker,
                        (generated,),
                        cleanup_snapshots,
                    )
                    if cleanup_errors:
                        _safe_add_note(exc, "transcript cleanup failed")
                    raise
                if not write_transcript:
                    cleanup_errors = _cleanup_output_candidates(
                        cleanup_tracker,
                        (generated,),
                        cleanup_snapshots,
                    )
                    if cleanup_errors:
                        _raise_cleanup_errors(cleanup_errors)
                    restored_stat = _regular_file_stat(
                        text_path,
                        field_name="restored transcript",
                    )
                    if restored_stat is None:
                        return text
                    return _trusted_transcript_text(text, text_path, restored_stat)
                final_stat = _regular_file_stat(
                    text_path,
                    field_name="transcript output",
                )
                if final_stat is None or not _same_regular_file_identity(output_stat, final_stat):
                    raise TranscriptionError("transcript output changed before return")
                return _trusted_transcript_text(text, text_path, final_stat)
            primary_error: BaseException | None = None
            output_target = generated_target
            try:
                text, _output_stat, output_target = _read_text_file_with_target(
                    generated,
                    size_field_name="transcript",
                )
                if not _same_expected_target_evidence(output_target, generated_target):
                    raise TranscriptionError("generated transcript changed before read")
                text = text.strip()
                _assert_text_length(text, field_name="transcript")
                if write_transcript:
                    _write_text_atomic(text_path, text + "\n")
            except BaseException as exc:
                primary_error = exc
                raise
            finally:
                cleanup_errors = _cleanup_output_candidates(
                    cleanup_tracker,
                    (generated,),
                    cleanup_snapshots,
                )
                if cleanup_errors:
                    if primary_error is None:
                        _raise_cleanup_errors(cleanup_errors)
                    _safe_add_note(primary_error, "transcript cleanup failed")
            return text
        cleanup_errors = _cleanup_output_candidates(
            cleanup_tracker,
            (generated,),
            cleanup_snapshots,
        )
        if cleanup_errors:
            _raise_cleanup_errors(cleanup_errors)
        raise TranscriptionError("whisper completed but did not produce a transcript")


def resolve_whisper_cpp_command() -> str | None:
    for command in ("whisper-cli", "whisper.cpp", "pwcpp"):
        if _is_command_available(command):
            return command
    return None


def _is_command_available(command: str) -> bool:
    try:
        _command_path(command)
        return True
    except TranscriptionError:
        return False


def _whisper_cpp_invocation(
    command: str,
    audio_path: Path,
    language: str,
    text_path: Path,
    model_path: str,
) -> tuple[list[str], Path]:
    if Path(command).name == "pwcpp":
        return (
            [
                command,
                "-m",
                model_path,
                "--language",
                language,
                "-otxt",
                str(audio_path),
            ],
            audio_path.with_name(audio_path.name + ".txt"),
        )

    output_base = text_path.with_suffix("")
    return (
        [
            command,
            "-m",
            model_path,
            "-f",
            str(audio_path),
            "-l",
            language,
            "-otxt",
            "-of",
            str(output_base),
        ],
        text_path,
    )


def transcribe_with_whisper_cpp(
    audio_path: Path,
    language: str,
    text_path: Path,
    model_path: str,
    write_transcript: bool = True,
    *,
    _expected_audio_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
    _resolved_command: str | None = None,
) -> str:
    write_transcript = _validate_write_transcript(write_transcript)
    language = _validate_language_code(language)
    audio_path, audio_snapshot = _prepare_local_backend_audio(
        audio_path,
        expected_snapshot=_expected_audio_snapshot,
        field_name="audio file for backend",
    )
    text_path = _normalize_transcript_path(text_path)
    try:
        assert_no_symlink_ancestors(text_path, field_name="transcript path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    model_path = _validate_local_model_path(model_path, field_name="whisper.cpp model path", directory=False)
    if not model_supports_language(model_path, language):
        raise TranscriptionError(
            f"English-only whisper.cpp model does not support language {language}; use a multilingual model"
        )
    command = _resolved_command if _resolved_command is not None else resolve_whisper_cpp_command()
    if not command:
        raise TranscriptionError("whisper.cpp command is not installed")
    try:
        parent_fd = ensure_directory_without_following_symlinks(text_path.parent, field_name="transcript directory")
    except OSError as exc:
        raise TranscriptionError("failed to prepare transcript directory") from exc
    else:
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_directory_fd(owned_parent_fd)
    with _staged_audio_file_for_local_backend(audio_path, expected_snapshot=audio_snapshot) as staged_audio_path:
        invocation, generated_path = _whisper_cpp_invocation(
            command,
            staged_audio_path,
            language,
            text_path,
            model_path,
        )
        generated_candidates = [generated_path]
        snapshots: dict[Path, bytes | None] = {}
        generated_states: dict[Path, tuple[int, int, int, int, int, int] | None] = {}
        for candidate in generated_candidates:
            snapshot_with_state = _snapshot_existing_file_with_state(candidate)
            if snapshot_with_state is None:
                snapshots[candidate] = None
                generated_states[candidate] = None
            else:
                (
                    snapshots[candidate],
                    generated_states[candidate],
                    _pre_backend_target,
                ) = snapshot_with_state
        cleanup_tracker = _OutputCleanupTracker(generated_candidates)

        def cleanup_field_names(*, preserve_text_path_errors: bool) -> dict[Path, str]:
            if not preserve_text_path_errors:
                return {}
            return {
                candidate: "generated sidecar"
                for candidate in generated_candidates
                if candidate == text_path and snapshots.get(candidate) is None
            }

        def cleanup_after_error(
            primary_error: BaseException,
            *,
            preserve_text_path_errors: bool = False,
        ) -> NoReturn:
            cleanup_errors = _cleanup_output_candidates(
                cleanup_tracker,
                generated_candidates,
                snapshots,
                field_names=cleanup_field_names(
                    preserve_text_path_errors=preserve_text_path_errors,
                ),
            )
            if cleanup_errors:
                _safe_add_note(primary_error, "transcript cleanup failed")
            raise primary_error

        try:
            _run_limited_process(invocation)
        except BaseException as exc:
            cleanup_after_error(exc)
        try:
            active_generated_path = None
            for candidate in generated_candidates:
                current_state = _file_state(candidate)
                if current_state is None:
                    continue
                if generated_states[candidate] is None or current_state != generated_states[candidate]:
                    active_generated_path = candidate
                    break
        except BaseException as exc:
            cleanup_after_error(exc)
        if active_generated_path is not None:
            generated_path = active_generated_path
            if generated_path == text_path:
                try:
                    text, output_stat, output_target = _read_text_file_with_target(
                        generated_path,
                        size_field_name="transcript",
                    )
                    cleanup_tracker.remember(generated_path, output_target)
                    text = text.strip()
                    _assert_text_length(text, field_name="transcript")
                except BaseException as exc:
                    cleanup_after_error(exc, preserve_text_path_errors=True)
                cleanup_errors = _cleanup_output_candidates(
                    cleanup_tracker,
                    generated_candidates,
                    snapshots,
                    exclude={generated_path} if write_transcript else None,
                    field_names=cleanup_field_names(
                        preserve_text_path_errors=True,
                    ),
                )
                if cleanup_errors:
                    _raise_cleanup_errors(cleanup_errors)
                if not write_transcript:
                    restored_stat = _regular_file_stat(
                        text_path,
                        field_name="restored transcript",
                    )
                    if restored_stat is not None:
                        output_stat = restored_stat
                    else:
                        return text
                return _trusted_transcript_text(text, text_path, output_stat)
            try:
                text, output_stat, output_target = _read_text_file_with_target(
                    generated_path,
                    size_field_name="transcript",
                )
                cleanup_tracker.remember(generated_path, output_target)
                text = text.strip()
                _assert_text_length(text, field_name="transcript")
                if write_transcript:
                    _write_text_atomic(text_path, text + "\n")
            except BaseException as exc:
                cleanup_after_error(exc)
            cleanup_errors = _cleanup_output_candidates(
                cleanup_tracker,
                generated_candidates,
                snapshots,
            )
            if cleanup_errors:
                _raise_cleanup_errors(cleanup_errors)
            return text
        cleanup_errors = _cleanup_output_candidates(
            cleanup_tracker,
            generated_candidates,
            snapshots,
        )
        if cleanup_errors:
            _raise_cleanup_errors(cleanup_errors)
        raise TranscriptionError("whisper.cpp completed but did not produce a transcript")


def faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401  # pylint: disable=import-error
    except Exception:
        return False
    return True


def _require_faster_whisper_available() -> None:
    if not faster_whisper_available():
        raise TranscriptionError("faster-whisper is not available")


def transcribe_with_faster_whisper(
    audio_path: Path,
    language: str,
    text_path: Path,
    model_path: str,
    write_transcript: bool = True,
    *,
    _expected_audio_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
) -> str:
    write_transcript = _validate_write_transcript(write_transcript)
    language = _validate_language_code(language)
    audio_path, audio_snapshot = _prepare_local_backend_audio(
        audio_path,
        expected_snapshot=_expected_audio_snapshot,
        field_name="audio file for backend",
    )
    text_path = _normalize_transcript_path(text_path)
    model_path = _validate_local_model_path(model_path, field_name="CTranslate2 model path", directory=True)
    if not model_supports_language(model_path, language):
        raise TranscriptionError(
            f"CTranslate2 model does not support language {language}; use a multilingual model"
        )
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError("faster-whisper is not installed") from exc
    except Exception as exc:
        raise TranscriptionError("faster-whisper could not be loaded") from exc

    deadline = time.monotonic() + TRANSCRIBE_COMMAND_TIMEOUT_SECONDS

    def ensure_deadline() -> None:
        if time.monotonic() >= deadline:
            raise TranscriptionError("faster-whisper timed out")

    with _staged_audio_file_for_local_backend(audio_path, expected_snapshot=audio_snapshot) as staged_audio_path:
        try:
            ensure_deadline()
            model = WhisperModel(model_path, device="cpu", compute_type="int8")
            ensure_deadline()
            segments, _info = model.transcribe(
                str(staged_audio_path),
                language=language or None,
                task="transcribe",
                beam_size=5,
            )
            ensure_deadline()
            text_parts: list[str] = []
            for segment in segments:
                ensure_deadline()
                segment_text = str(segment.text or "").strip()
                if not segment_text:
                    continue
                text_parts.append(segment_text)
                _assert_text_length(" ".join(text_parts), field_name="transcript")
            text = " ".join(text_parts).strip()
        except Exception as exc:
            if isinstance(exc, TranscriptionError):
                raise
            raise TranscriptionError("faster-whisper failed: error detail redacted") from exc
    if not text:
        raise TranscriptionError("transcriber completed without transcript")
    _assert_text_length(text, field_name="transcript")
    if write_transcript:
        _write_text_atomic(text_path, text + "\n")
    return text


def _openai_compatible_endpoint(url: str, path: str) -> str:
    base = _validate_openai_compatible_api_url(url).rstrip("/")
    if not base:
        raise TranscriptionError("OpenAI-compatible API URL is required")
    normalized_path = "/" + path.strip("/")
    base_parts = [part for part in urllib.parse.urlparse(base).path.split("/") if part]
    target_parts = [part for part in normalized_path.split("/") if part]
    if target_parts and len(base_parts) >= len(target_parts) and base_parts[-len(target_parts):] == target_parts:
        return base
    return base + normalized_path


def _is_openai_api_endpoint(endpoint: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(endpoint)
    except ValueError:
        return False
    return (parsed.hostname or "").lower() == "api.openai.com"


def _is_flex_service_tier_rejected(detail: str) -> bool:
    normalized = detail.lower().replace("-", "_")
    mentions_service_tier = any(marker in normalized for marker in ("service_tier", "service tier", "servicetier"))
    rejects_service_tier = any(
        marker in normalized
        for marker in ("invalid", "unsupported", "not available", "not enabled", "rejected", "unrecognized", "unknown")
    )
    return mentions_service_tier and rejects_service_tier


def _validate_openai_compatible_api_url(
    url: str,
    field_name: str = "OpenAI-compatible API URL",
    allow_query_fragment: bool = False,
) -> str:
    if _contains_escaped_null(url):
        raise TranscriptionError(f"{field_name} contains invalid null byte")
    if _contains_http_header_control_chars(url):
        raise TranscriptionError(f"{field_name} contains invalid control character")
    base = _assert_text_length(url, field_name=field_name, max_chars=MAX_OPENAI_URL_CHARS).strip()
    if not base:
        raise TranscriptionError(f"{field_name} is required")
    if any(char.isspace() for char in base):
        raise TranscriptionError(f"{field_name} contains invalid whitespace")
    try:
        parsed = urllib.parse.urlparse(base)
    except ValueError as exc:
        raise TranscriptionError(f"{field_name} is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise TranscriptionError(f"{field_name} must use http:// or https://")
    if not parsed.hostname:
        raise TranscriptionError(f"{field_name} is missing hostname")
    if parsed.scheme == "http" and not is_loopback_hostname(parsed.hostname):
        raise TranscriptionError(f"{field_name} must use https:// unless host is local loopback")
    try:
        parsed.port
    except ValueError as exc:
        raise TranscriptionError(f"{field_name} has invalid port") from exc
    if "@" in parsed.netloc or parsed.username is not None or parsed.password is not None:
        raise TranscriptionError(f"{field_name} must not contain userinfo")
    if not allow_query_fragment and (parsed.query or parsed.fragment):
        raise TranscriptionError(f"{field_name} must not contain query or fragment")
    return base


def _validate_openai_compatible_transcribe_options(
    model: str,
    url: str,
    api_key: str,
    flex_processing: bool,
    service_tier_fallback: bool,
) -> tuple[str, str, str, bool, bool]:
    if not isinstance(model, str) or isinstance(model, bool):
        raise TranscriptionError("OpenAI-compatible speech model must be text")
    if not isinstance(url, str) or isinstance(url, bool):
        raise TranscriptionError("OpenAI-compatible API URL must be text")
    if not isinstance(api_key, str) or isinstance(api_key, bool):
        raise TranscriptionError("OpenAI-compatible API key must be text")
    if not api_key.strip():
        api_key = _coerce_environment_value("SPEED_OF_CINNAMON_OPENAI_COMPATIBLE_API_KEY") or ""
    if not isinstance(flex_processing, bool):
        raise TranscriptionError("OpenAI-compatible flex processing must be a boolean")
    if not isinstance(service_tier_fallback, bool):
        raise TranscriptionError("OpenAI-compatible service tier fallback must be a boolean")
    if _contains_escaped_null(model):
        raise TranscriptionError("OpenAI-compatible speech model contains invalid null byte")
    if _contains_escaped_null(api_key):
        raise TranscriptionError("OpenAI-compatible API key contains invalid null byte")
    if _contains_http_header_control_chars(api_key):
        raise TranscriptionError("openai-compatible API key contains invalid control character")
    if _contains_http_header_control_chars(model):
        raise TranscriptionError("multipart form field contains invalid control character")
    model = _assert_text_length(
        model,
        field_name="OpenAI-compatible speech model",
        max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
    ).strip()
    if not model:
        raise TranscriptionError("OpenAI-compatible speech model is required")
    url = _validate_openai_compatible_api_url(url)
    api_key = _assert_text_length(
        api_key,
        field_name="OpenAI-compatible API key",
        max_chars=MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
    ).strip()
    endpoint = _openai_compatible_endpoint(url, "/audio/transcriptions")
    if _is_openai_api_endpoint(endpoint) and model not in OPENAI_TRANSCRIPTION_MODELS:
        raise TranscriptionError(
            "OpenAI transcription endpoint requires a speech-to-text model such as "
            "gpt-4o-transcribe, gpt-4o-mini-transcribe, or whisper-1; configured model is "
            f"{model}"
        )
    return model, url, api_key, flex_processing, service_tier_fallback


def _safe_url_display(url: str, *, field_name: str) -> str:
    normalized = _validate_openai_compatible_api_url(url, field_name=field_name)
    parsed = urllib.parse.urlparse(normalized)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    port = _effective_url_port(parsed)
    if parsed.port is not None and port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunparse((parsed.scheme, netloc, "", "", "", ""))


def _openai_compatible_error_detail(raw: str) -> str:
    raw = _assert_text_length(raw or "", field_name="OpenAI-compatible API error", max_chars=MAX_TRANSCRIBER_ERROR_CHARS).strip()
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(payload, dict) and payload.get("error"):
        error = payload["error"]
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            code = str(error.get("code") or "").strip()
            error_type = str(error.get("type") or "").strip()
            parts = [part for part in (message, code, error_type) if part]
            return " - ".join(parts) if parts else str(error)
        return str(error)
    return raw


def _sanitize_local_command_error(message: str) -> str:
    message = message.strip()
    if not message:
        return "transcriber command failed: [redacted command error]"
    lowered = message.lower()
    if message.startswith("invalid transcriber") or message.startswith("unsupported shell operator in transcriber"):
        return message
    if (
        message.startswith("transcriber command ended")
        or message.startswith("empty transcriber")
        or message.startswith("transcriber command chain is empty")
    ):
        return message
    if "path separators" in lowered or "must be text" in lowered or "must not contain" in lowered:
        return message
    if "exit code " in lowered:
        start = lowered.index("exit code ") + len("exit code ")
        exit_code = ""
        while start < len(message) and message[start].isdigit():
            exit_code += message[start]
            start += 1
        if not exit_code:
            exit_code = "unknown"
        return f"transcriber command failed: exit code {exit_code}; command output redacted"
    if "command timed out" in lowered:
        return "transcriber command timed out: [redacted command output]"
    if "command not found" in lowered:
        return "transcriber command not found"
    if "command execution failed" in lowered:
        return "transcriber command execution failed"
    if (
        "command output exceeded" in lowered
        or "command output contains invalid null byte" in lowered
        or "command contains invalid null byte" in lowered
        or "command input exceeded" in lowered
        or "command input contains invalid null byte" in lowered
        or "personal context is too large" in lowered
        or "vocabulary is too large" in lowered
        or "max_input_chars must be positive" in lowered
        or "max_input_chars must be non-negative" in lowered
        or "max_input_chars must not exceed" in lowered
        or "max_output_chars must be non-negative" in lowered
        or "max_output_chars must be positive" in lowered
        or "max_output_chars must not exceed" in lowered
        or "timeout_seconds must be positive" in lowered
    ):
        return message
    return "transcriber command failed: [redacted command error]"


def _split_transcriber_command(command: str) -> list[list[str]]:
    try:
        return split_command_chain(command, label="transcriber")
    except CommandChainError as exc:
        raise TranscriptionError(_sanitize_local_command_error(str(exc))) from None


def _preflight_transcriber_command(
    command: str,
    *,
    audio_path: Path | None = None,
    audio_marker: str | None = None,
    require_audio_binding: bool = False,
) -> _CommandPreflight:
    segments = _split_transcriber_command(command)
    if not segments:
        raise TranscriptionError("transcriber command chain is empty")
    unavailable = False
    invalid_executable = False
    for segment in segments:
        if not segment:
            unavailable = True
            break
        executable = segment[0]
        if os.path.sep in executable or (os.path.altsep and os.path.altsep in executable):
            invalid_executable = True
            break
        try:
            _command_path(executable)
        except TranscriptionError:
            unavailable = True
            break
    if invalid_executable:
        raise TranscriptionError("command must be a bare command name without path separators")
    if unavailable:
        raise TranscriptionError("custom transcriber executable is not available")
    audio_positions: list[tuple[int, int]] = []
    if audio_marker is not None:
        if isinstance(audio_marker, bool) or not isinstance(audio_marker, str) or not audio_marker:
            raise TranscriptionError("transcriber command audio provenance is invalid")
        audio_positions = [
            (segment_index, token_index)
            for segment_index, segment in enumerate(segments)
            for token_index, token in enumerate(segment)
            if audio_marker in token
        ]
    elif audio_path is not None:
        audio_text = str(audio_path)
        audio_positions = [
            (segment_index, token_index)
            for segment_index, segment in enumerate(segments)
            for token_index, token in enumerate(segment)
            if token == audio_text
        ]
    if require_audio_binding and not audio_positions:
        raise TranscriptionError("audio placeholder must be a standalone command argument")
    return _CommandPreflight(
        tuple(tuple(token for token in segment) for segment in segments),
        tuple(audio_positions),
        audio_marker,
    )


def _bind_staged_transcriber_command(
    preflight: _CommandPreflight,
    staged_audio_path: Path,
) -> list[list[str]]:
    if not isinstance(preflight, _CommandPreflight):
        raise TranscriptionError("transcriber command preflight is invalid")
    bound = [list(segment) for segment in preflight.segments]
    staged_audio_text = str(staged_audio_path)
    for segment_index, token_index in preflight.audio_positions:
        try:
            token = bound[segment_index][token_index]
            if preflight.audio_marker is None:
                bound[segment_index][token_index] = staged_audio_text
            else:
                if preflight.audio_marker not in token:
                    raise TranscriptionError("transcriber command preflight is invalid")
                bound[segment_index][token_index] = token.replace(
                    preflight.audio_marker,
                    staged_audio_text,
                )
        except (IndexError, TypeError):
            raise TranscriptionError("transcriber command preflight is invalid") from None
    return bound


def _sanitize_remote_error_detail(value: object) -> str:
    return "[redacted remote error]"


def _multipart_form_data(
    fields: dict[str, str],
    file_field: str,
    file_path: Path,
    expected_file_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
    *,
    file_bytes: bytes | None = None,
) -> tuple[bytearray, str]:
    if isinstance(file_field, bool) or not isinstance(file_field, str):
        raise TranscriptionError("multipart file field must be text")
    if _contains_multipart_control_chars(file_field):
        raise TranscriptionError("multipart file field contains invalid control character")
    file_field_utf8_valid = True
    try:
        file_field.encode("utf-8")
    except UnicodeEncodeError:
        file_field_utf8_valid = False
    if not file_field_utf8_valid:
        raise TranscriptionError("multipart file field contains invalid UTF-8")
    file_field = file_field.replace("\\", "\\\\").replace('"', '\\"')
    boundary = "speed-of-cinnamon-" + uuid.uuid4().hex
    body = bytearray()
    file_name = file_path.name
    if "\r" in file_name or "\n" in file_name:
        raise TranscriptionError("audio file name contains invalid newline")
    file_name = file_name.replace("\\", "\\\\").replace('"', '\\"')
    if file_bytes is None:
        file_bytes = _read_private_file_bytes(
            file_path,
            field_name="audio file for API upload",
            max_bytes=MAX_AUDIO_FILE_BYTES,
            expected_snapshot=expected_file_snapshot,
        )
    elif type(file_bytes) is not bytes:
        raise TranscriptionError("audio bytes must be bytes")
    for key, value in fields.items():
        if _contains_multipart_control_chars(key) or _contains_multipart_control_chars(value):
            raise TranscriptionError("multipart form field contains invalid control character")
        encoded_key: bytes | None = None
        encoded_value: bytes | None = None
        try:
            encoded_key = key.encode("utf-8")
            encoded_value = value.encode("utf-8")
        except UnicodeEncodeError:
            pass
        if encoded_key is None or encoded_value is None:
            raise TranscriptionError("multipart form field contains invalid UTF-8")
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(b'Content-Disposition: form-data; name="')
        body.extend(encoded_key)
        body.extend(b'"\r\n\r\n')
        body.extend(encoded_value)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(file_bytes)
    del file_bytes
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    return body, boundary


def transcribe_with_openai_compatible_api(
    audio_path: Path,
    language: str,
    text_path: Path,
    model: str,
    url: str,
    api_key: str = "",
    flex_processing: bool = True,
    write_transcript: bool = True,
    openai_compatible_service_tier_fallback: bool = False,
    *,
    _expected_audio_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
) -> str:
    write_transcript = _validate_write_transcript(write_transcript)
    option_error: TranscriptionError | None = None
    normalized_options: tuple[str, str, str, bool, bool] | None = None
    try:
        normalized_options = _validate_openai_compatible_transcribe_options(
            model,
            url,
            api_key,
            flex_processing,
            openai_compatible_service_tier_fallback,
        )
    except TranscriptionError as exc:
        option_error = exc
    if option_error is not None:
        raise TranscriptionError(str(option_error))
    if normalized_options is None:
        raise TranscriptionError("OpenAI-compatible options are invalid")
    (
        model,
        url,
        api_key,
        flex_processing,
        openai_compatible_service_tier_fallback,
    ) = normalized_options
    language = _validate_language_code(language)
    text_path = _normalize_transcript_path(text_path)
    audio_path, audio_snapshot = _validate_audio_file_for_upload(
        audio_path,
        expected_snapshot=_expected_audio_snapshot,
    )
    endpoint = _openai_compatible_endpoint(url, "/audio/transcriptions")
    is_openai_api = _is_openai_api_endpoint(endpoint)
    if is_openai_api and model not in OPENAI_TRANSCRIPTION_MODELS:
        raise TranscriptionError(
            "OpenAI transcription endpoint requires a speech-to-text model such as "
            "gpt-4o-transcribe, gpt-4o-mini-transcribe, or whisper-1; configured model is "
            f"{model}"
        )
    fields = {
        "model": model,
        "language": language,
        "response_format": "json",
    }
    use_flex_processing = flex_processing and is_openai_api
    if use_flex_processing:
        fields["service_tier"] = "flex"
    allow_service_tier_fallback = use_flex_processing and openai_compatible_service_tier_fallback
    audio_bytes = _read_private_file_bytes(
        audio_path,
        field_name="audio file for API upload",
        max_bytes=MAX_AUDIO_FILE_BYTES,
        expected_snapshot=audio_snapshot,
    )

    def _request_transcription(request_fields: dict[str, str]) -> str:
        body, boundary = _multipart_form_data(
            request_fields,
            "file",
            audio_path,
            expected_file_snapshot=audio_snapshot,
            file_bytes=audio_bytes,
        )
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Accept": "application/json",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
        with _open_http_request(
            request,
            timeout=TRANSCRIBE_COMMAND_TIMEOUT_SECONDS,
            field_name="OpenAI-compatible speech request",
        ) as response:
            return _read_response_text(response)

    pending_remote_error: TranscriptionError | None = None
    try:
        raw = _request_transcription(fields)
    except urllib.error.HTTPError as exc:
        try:
            raw_error = _read_response_text(exc, MAX_TRANSCRIBER_ERROR_CHARS)
        except Exception:
            raw_error = ""
        finally:
            response_close_errors: list[BaseException] = []
            _release_owned_closer(exc.close, response_close_errors)
            if response_close_errors:
                _safe_add_note(exc, "HTTP response cleanup failed")
        raw_detail = _openai_compatible_error_detail(raw_error) or str(exc.reason or exc)
        if allow_service_tier_fallback and _is_flex_service_tier_rejected(raw_detail):
            fallback_fields = dict(fields)
            fallback_fields.pop("service_tier", None)
            try:
                raw = _request_transcription(fallback_fields)
            except urllib.error.HTTPError as fallback_exc:
                try:
                    raw_error = _read_response_text(fallback_exc, MAX_TRANSCRIBER_ERROR_CHARS)
                except Exception:
                    raw_error = ""
                finally:
                    response_close_errors = []
                    _release_owned_closer(fallback_exc.close, response_close_errors)
                    if response_close_errors:
                        _safe_add_note(fallback_exc, "HTTP response cleanup failed")
                fallback_detail = _sanitize_remote_error_detail(
                    _openai_compatible_error_detail(raw_error) or fallback_exc.reason or str(fallback_exc)
                )
                fallback_code = (
                    fallback_exc.code
                    if isinstance(fallback_exc.code, int) and not isinstance(fallback_exc.code, bool)
                    else "unknown"
                )
                pending_remote_error = TranscriptionError(
                    f"OpenAI-compatible speech API failed ({fallback_code}): {fallback_detail}"
                )
            except OSError as fallback_exc:
                detail = _sanitize_remote_error_detail(fallback_exc)
                pending_remote_error = TranscriptionError(
                    f"OpenAI-compatible speech API is not reachable: {detail}"
                )
        else:
            detail = _sanitize_remote_error_detail(raw_detail)
            status_code = exc.code if isinstance(exc.code, int) and not isinstance(exc.code, bool) else "unknown"
            pending_remote_error = TranscriptionError(
                f"OpenAI-compatible speech API failed ({status_code}): {detail}"
            )
    except OSError as exc:
        detail = _sanitize_remote_error_detail(exc)
        pending_remote_error = TranscriptionError(
            f"OpenAI-compatible speech API is not reachable: {detail}"
        )
    if pending_remote_error is not None:
        raise pending_remote_error
    json_error = False
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        json_error = True
    if json_error:
        raise TranscriptionError("OpenAI-compatible speech API returned invalid JSON")
    if not isinstance(payload, dict):
        raise TranscriptionError("OpenAI-compatible speech API response must be a JSON object")
    if payload.get("error"):
        error = payload["error"]
        detail = _sanitize_remote_error_detail(str(error.get("message") or error) if isinstance(error, dict) else str(error))
        raise TranscriptionError(f"OpenAI-compatible speech API failed: {detail}")
    raw_text = payload.get("text")
    if raw_text is not None and not isinstance(raw_text, str):
        raise TranscriptionError("OpenAI-compatible speech API response text must be text")
    text = (raw_text or "").strip()
    if not text:
        raise TranscriptionError("OpenAI-compatible speech API returned no transcript")
    _assert_text_length(text, field_name="transcript")
    if write_transcript:
        _write_text_atomic(text_path, text + "\n")
    return text


def normalize_backend(value: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise TranscriptionError("backend must be text")
    if _contains_escaped_null(value):
        raise TranscriptionError("backend contains invalid null byte")
    if _contains_http_header_control_chars(value):
        raise TranscriptionError("backend contains invalid control character")
    normalized = (value or "auto").strip().lower().replace("_", "-")
    aliases = {
        "openai": "whisper",
        "openai-whisper": "whisper",
        "whisper-cpp": "whisper-cpp",
        "whisper.cpp": "whisper-cpp",
        "ctranslate2": "faster-whisper",
        "ct2": "faster-whisper",
        "faster-whisper": "faster-whisper",
        "external-api": "openai-compatible",
        "openai-compatible": "openai-compatible",
        "openai-compatible-api": "openai-compatible",
        "custom": "command",
        "template": "command",
    }
    return aliases.get(normalized, normalized)


def resolve_transcriber(config: TranscriberConfig) -> str:
    if not isinstance(config, TranscriberConfig):
        raise TranscriptionError("config must be TranscriberConfig")
    backend = normalize_backend(config.backend)
    if isinstance(config.command_template, bool) or not isinstance(config.command_template, str):
        raise TranscriptionError("command template must be text")
    if isinstance(config.whisper_model, bool) or not isinstance(config.whisper_model, str):
        raise TranscriptionError("whisper model must be text")
    language = _validate_language_code(config.language)
    if backend not in _SUPPORTED_TRANSCRIBER_BACKENDS:
        raise TranscriptionError(f"unknown transcriber backend: {config.backend}")

    model_can_affect_resolution = backend in {"whisper-cpp", "faster-whisper"} or (
        backend == "auto" and not config.command_template.strip()
    )
    raw_whisper_model = config.whisper_model
    configured_model = raw_whisper_model.strip() if model_can_affect_resolution else ""
    if configured_model:
        if _contains_escaped_null(raw_whisper_model):
            raise TranscriptionError("whisper model contains invalid null byte")
        if _contains_http_header_control_chars(raw_whisper_model):
            raise TranscriptionError("whisper model contains invalid control character")
    has_configured_model = bool(configured_model)
    configured_model_backend = model_backend_for_path(configured_model) if configured_model else ""
    configured_model_is_dir = False
    configured_model_exists = False
    configured_model_path = None
    if configured_model:
        try:
            configured_model_path = Path(configured_model).expanduser()
        except (OSError, RuntimeError, ValueError) as exc:
            raise TranscriptionError("configured whisper model path is invalid") from exc
        try:
            assert_no_symlink_ancestors(configured_model_path, field_name="configured whisper model path")
            configured_model_kind = _local_model_path_kind(configured_model_path, field_name="configured whisper model path")
            configured_model_exists = configured_model_kind is not None
            configured_model_is_dir = configured_model_kind == "directory"
        except (OSError, ValueError):
            configured_model_is_dir = False
        except RuntimeError as exc:
            raise TranscriptionError(str(exc)) from exc
    local_model = ""
    if backend == "auto":
        if config.command_template.strip():
            return "command"
        if has_configured_model:
            if not configured_model_exists:
                raise TranscriptionError("configured whisper model path is missing")
            if configured_model_backend == "faster-whisper" or configured_model_is_dir:
                if faster_whisper_available():
                    return "faster-whisper"
                raise TranscriptionError("configured CTranslate2 model requires faster-whisper")
            if configured_model_backend == "whisper-cpp":
                if resolve_whisper_cpp_command():
                    return "whisper-cpp"
                raise TranscriptionError("configured whisper model requires whisper.cpp")
            if resolve_whisper_cpp_command():
                return "whisper-cpp"
            raise TranscriptionError("configured model requires whisper.cpp")
        if configured_model_backend == "faster-whisper" and faster_whisper_available():
            return "faster-whisper"
        if configured_model_backend == "whisper-cpp" and resolve_whisper_cpp_command():
            return "whisper-cpp"
        if _is_command_available("whisper"):
            return "whisper"
        local_model = default_ctranslate2_model_path(language) or default_whisper_cpp_model_path(language)
        local_model_backend = model_backend_for_path(local_model) if local_model else ""
        if local_model and local_model_backend == "faster-whisper" and faster_whisper_available():
            return "faster-whisper"
        if local_model and local_model_backend == "whisper-cpp" and resolve_whisper_cpp_command():
            return "whisper-cpp"
        raise TranscriptionError(
            "no transcriber available; install 'whisper', install faster-whisper, configure whisper.cpp with a model, "
            "or set a custom transcriber command"
        )
    return backend


def _transcribe_locked(
    audio_path: Path,
    language: str,
    text_path: Path,
    command_template: str,
    backend: str,
    whisper_model: str,
    personal_context: str,
    vocabulary: str,
    openai_compatible_model: str,
    openai_compatible_url: str,
    openai_compatible_api_key: str,
    openai_compatible_flex_processing: bool,
    openai_compatible_service_tier_fallback: bool,
    _expected_audio_snapshot: tuple[int, int, int, int, int, int]
    | tuple[int, int, int, int, int, int, str]
    | None = None,
    _preflight: _TranscriberPreflight | None = None,
) -> str:
    config = TranscriberConfig(
        backend=backend,
        command_template=command_template,
        whisper_model=whisper_model,
        language=language,
    )
    if _preflight is None:
        resolved_backend = resolve_transcriber(config)
        locked_audio_snapshot = _expected_audio_snapshot
        preflight_command = None
    else:
        if not isinstance(_preflight, _TranscriberPreflight):
            raise TranscriptionError("transcriber preflight is invalid")
        resolved_backend = _preflight.backend
        locked_audio_snapshot = _preflight.audio_snapshot
        preflight_command = _preflight.command
    if resolved_backend == "openai-compatible":
        (
            openai_compatible_model,
            openai_compatible_url,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback,
        ) = _validate_openai_compatible_transcribe_options(
            openai_compatible_model,
            openai_compatible_url,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback,
        )
    if resolved_backend == "openai-compatible" and (
        "\r" in audio_path.name or "\n" in audio_path.name
    ):
        raise TranscriptionError("audio file name contains invalid newline")
    if resolved_backend == "command":
        if not command_template.strip():
            raise TranscriptionError("custom transcriber command is required")
        text = transcribe_with_template(
            command_template.strip(),
            audio_path,
            language,
            text_path,
            personal_context,
            vocabulary,
            _expected_audio_snapshot=locked_audio_snapshot,
            _preflight_command=preflight_command,
        )
    elif resolved_backend == "whisper":
        text = transcribe_with_openai_whisper(
            audio_path,
            language,
            text_path,
            write_transcript=False,
            _expected_audio_snapshot=locked_audio_snapshot,
            _skip_backend_preflight=_preflight is not None,
        )
    elif resolved_backend == "whisper-cpp":
        text = transcribe_with_whisper_cpp(
            audio_path,
            language,
            text_path,
            whisper_model or default_whisper_cpp_model_path(language),
            write_transcript=False,
            _expected_audio_snapshot=locked_audio_snapshot,
            _resolved_command=_preflight.resolved_command if _preflight is not None else None,
        )
    elif resolved_backend == "faster-whisper":
        text = transcribe_with_faster_whisper(
            audio_path,
            language,
            text_path,
            whisper_model or default_ctranslate2_model_path(language),
            write_transcript=False,
            _expected_audio_snapshot=locked_audio_snapshot,
        )
    elif resolved_backend == "openai-compatible":
        openai_compatible_url = _validate_openai_compatible_api_url(openai_compatible_url)
        text = transcribe_with_openai_compatible_api(
            audio_path,
            language,
            text_path,
            openai_compatible_model,
            openai_compatible_url,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback=openai_compatible_service_tier_fallback,
            write_transcript=False,
            _expected_audio_snapshot=locked_audio_snapshot,
        )
    else:
        raise TranscriptionError(f"unknown transcriber backend: {resolved_backend}")
    trusted_output = text if isinstance(text, _TrustedTranscriptText) else None
    text = text.strip()
    if not text:
        raise TranscriptionError("transcriber completed without transcript")
    _reject_placeholder_transcript(text, language)
    _assert_text_length(text, field_name="transcript")
    if trusted_output is not None:
        if trusted_output.output_path != text_path:
            raise TranscriptionError("transcript output is unsafe")
        output_stat = trusted_output.output_stat
        if output_stat is None or not stat_module.S_ISREG(output_stat.st_mode) or getattr(output_stat, "st_nlink", 1) != 1:
            raise TranscriptionError("transcript output is unsafe")
        current_stat = _regular_file_stat(text_path, field_name="transcript output")
        if current_stat is None:
            return text
        if not _same_regular_file_identity(output_stat, current_stat):
            raise TranscriptionError("transcript output changed before return")
        return _TrustedTranscriptText(
            text,
            text_path,
            current_stat,
        )
    return text


def transcribe(
    audio_path: Path,
    language: str,
    text_path: Path,
    command_template: str = "",
    backend: str = "auto",
    whisper_model: str = "",
    personal_context: str = "",
    vocabulary: str = "",
    openai_compatible_model: str = DEFAULT_OPENAI_COMPATIBLE_MODEL,
    openai_compatible_url: str = DEFAULT_OPENAI_COMPATIBLE_URL,
    openai_compatible_api_key: str = "",
    openai_compatible_flex_processing: bool = True,
    openai_compatible_service_tier_fallback: bool = False,
) -> str:
    if not isinstance(audio_path, Path):
        raise TranscriptionError("audio path must be a Path")
    if not isinstance(text_path, Path):
        raise TranscriptionError("text path must be a Path")
    language = _validate_language_code(language)
    if not isinstance(command_template, str) or isinstance(command_template, bool):
        raise TranscriptionError("command template must be text")
    command_template = _assert_text_length(
        command_template,
        field_name="command template",
        max_chars=MAX_TRANSCRIBER_TEXT_CHARS,
    )
    if not isinstance(backend, str) or isinstance(backend, bool):
        raise TranscriptionError("backend must be text")
    raw_backend = backend
    backend = normalize_backend(backend)
    if backend not in _SUPPORTED_TRANSCRIBER_BACKENDS:
        raise TranscriptionError(f"unknown transcriber backend: {raw_backend}")
    if not isinstance(whisper_model, str) or isinstance(whisper_model, bool):
        raise TranscriptionError("whisper model must be text")
    if backend in {"whisper-cpp", "faster-whisper"} or (
        backend == "auto" and not command_template.strip()
    ):
        if _contains_escaped_null(whisper_model):
            raise TranscriptionError("whisper model contains invalid null byte")
        if _contains_http_header_control_chars(whisper_model):
            raise TranscriptionError("whisper model contains invalid control character")
    whisper_model = whisper_model.strip()
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise TranscriptionError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise TranscriptionError("vocabulary must be text")
    try:
        personal_context = normalize_context(personal_context)
        vocabulary = normalize_vocabulary(vocabulary)
    except ValueError as exc:
        raise TranscriptionError(str(exc)) from None
    if backend == "command" and not command_template.strip():
        raise TranscriptionError("custom transcriber command is required")
    if backend == "openai-compatible":
        (
            openai_compatible_model,
            openai_compatible_url,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback,
        ) = _validate_openai_compatible_transcribe_options(
            openai_compatible_model,
            openai_compatible_url,
            openai_compatible_api_key,
            openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback,
        )
    audio_path = validate_audio_file(audio_path)
    if backend == "openai-compatible" and ("\r" in audio_path.name or "\n" in audio_path.name):
        raise TranscriptionError("audio file name contains invalid newline")
    text_path = _normalize_transcript_path(text_path)
    try:
        assert_no_symlink_ancestors(text_path, field_name="transcript path")
    except RuntimeError as exc:
        raise TranscriptionError(str(exc)) from exc
    preflight_backend = resolve_transcriber(
        TranscriberConfig(
            backend=backend,
            command_template=command_template,
            whisper_model=whisper_model,
            language=language,
        )
    )
    preflight_audio_snapshot: _AudioSnapshot | None = None
    preflight_command: _CommandPreflight | None = None
    preflight_resolved_command: str | None = None
    if preflight_backend == "command":
        command, audio_marker = _render_command_template_with_audio_provenance(
            command_template,
            audio_path,
            language,
            text_path,
            personal_context,
            vocabulary,
        )
        preflight_command = _preflight_transcriber_command(
            command,
            audio_marker=audio_marker,
            require_audio_binding="{audio}" in command_template,
        )
        preflight_audio_snapshot = _snapshot_private_file(
            audio_path,
            field_name="audio file for backend",
            include_hash=False,
        )
    elif preflight_backend == "whisper":
        _require_whisper_command()
        preflight_audio_snapshot = _snapshot_private_file(
            audio_path,
            field_name="audio file for backend",
            include_hash=False,
        )
    elif preflight_backend == "whisper-cpp":
        preflight_model = whisper_model or default_whisper_cpp_model_path(language)
        preflight_model = _validate_local_model_path(
            preflight_model,
            field_name="whisper.cpp model path",
            directory=False,
        )
        if not model_supports_language(preflight_model, language):
            raise TranscriptionError(
                f"English-only whisper.cpp model does not support language {language}; use a multilingual model"
            )
        preflight_resolved_command = resolve_whisper_cpp_command()
        if not preflight_resolved_command:
            raise TranscriptionError("whisper.cpp command is not installed")
        preflight_audio_snapshot = _snapshot_private_file(
            audio_path,
            field_name="audio file for backend",
            include_hash=False,
        )
    elif preflight_backend == "faster-whisper":
        preflight_model = whisper_model or default_ctranslate2_model_path(language)
        preflight_model = _validate_local_model_path(
            preflight_model,
            field_name="CTranslate2 model path",
            directory=True,
        )
        if not model_supports_language(preflight_model, language):
            raise TranscriptionError(
                f"CTranslate2 model does not support language {language}; use a multilingual model"
            )
        _require_faster_whisper_available()
        preflight_audio_snapshot = _snapshot_private_file(
            audio_path,
            field_name="audio file for backend",
            include_hash=False,
        )
    elif preflight_backend == "openai-compatible":
        audio_path, preflight_audio_snapshot = _validate_audio_file_for_upload(audio_path)
    preflight = _TranscriberPreflight(
        preflight_backend,
        preflight_audio_snapshot,
        preflight_command,
        preflight_resolved_command,
    )
    if preflight_backend == "openai-compatible":
        output_namespace = nullcontext()
    else:
        try:
            parent_fd = ensure_directory_without_following_symlinks(text_path.parent, field_name="transcript directory")
        except OSError as exc:
            raise TranscriptionError("failed to prepare transcript directory") from exc
        owned_parent_fd = parent_fd
        parent_fd = None
        _release_directory_fd(owned_parent_fd)
        output_namespace = _transcriber_output_namespace_lock(text_path.parent)
    with output_namespace:
        return _transcribe_locked(
            audio_path=audio_path,
            language=language,
            text_path=text_path,
            command_template=command_template,
            backend=backend,
            whisper_model=whisper_model,
            personal_context=personal_context,
            vocabulary=vocabulary,
            openai_compatible_model=openai_compatible_model,
            openai_compatible_url=openai_compatible_url,
            openai_compatible_api_key=openai_compatible_api_key,
            openai_compatible_flex_processing=openai_compatible_flex_processing,
            openai_compatible_service_tier_fallback=openai_compatible_service_tier_fallback,
            _expected_audio_snapshot=preflight_audio_snapshot,
            _preflight=preflight,
        )
