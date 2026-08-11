from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
import codecs
import os
import re
import selectors
import shlex
import signal
import subprocess  # nosec B404
import tempfile
import time
import io
import shutil
from pathlib import Path

from .personalization import command_environment
from .output import (
    _clipboard_lock_identity_for_pid,
    _kill_output_process_tree,
    _output_process_identity_is_current,
    _process_pipe_holder_identities,
    _process_tree_descendant_identities,
    _wait_for_output_process_tree_stop,
)


class CommandChainError(RuntimeError):
    pass


_REDACTED_COMMAND_OUTPUT = "exit code {returncode}; command output redacted"
_PIPE_DRAIN_GRACE_SECONDS = 0.25
_PROCESS_POLL_INTERVAL_SECONDS = 0.05
_PROCESS_TREE_SNAPSHOT_INTERVAL_SECONDS = 0.05
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
_ESCAPED_CONTROL_RE = re.compile(
    r"(?i)\\(?:[abfnrtv]|x(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f])|"
    r"u00(?:0[0-9a-f]|1[0-9a-f]|7f|8[0-9a-f]|9[0-9a-f]))"
)


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
            raise CommandChainError("environment base must be a mapping")
        for key, value in base.items():
            if not isinstance(key, str) or isinstance(key, bool):
                raise CommandChainError("environment keys must be text")
            if isinstance(value, bool):
                raise CommandChainError("environment values must be text")
            if not isinstance(value, str):
                raise CommandChainError("environment base must be a mapping")
            if _contains_escaped_null(key) or _contains_http_header_control_chars(key):
                raise CommandChainError("environment key contains invalid control character")
            if _contains_environment_control_chars(value):
                raise CommandChainError("environment value contains invalid control character")
            if _is_unsafe_env_var(key):
                raise CommandChainError(f"environment key is not allowed: {key}")
            env[key] = value

    env["PATH"] = _TRUSTED_COMMAND_PATH
    for key in list(env):
        if _is_unsafe_env_var(key):
            env.pop(key, None)
    return env


def _command_failure_detail(returncode: int, stdout_size: int, stderr_size: int) -> str:
    if stdout_size or stderr_size:
        return _REDACTED_COMMAND_OUTPUT.format(returncode=returncode)
    return f"exit code {returncode}"


def _command_timeout_detail(label: str, timeout_seconds: int) -> str:
    return f"{label} command timed out after {timeout_seconds} seconds"


def _terminate_bounded_process(
    proc: subprocess.Popen[bytes],
    *,
    process_tree: dict[int, str] | None = None,
) -> bool:
    pid = getattr(proc, "pid", None)
    root_identity_current = _output_process_identity_is_current(proc)
    root_identity_changed_after_exit = (
        not root_identity_current
        and process_tree is not None
        and isinstance(getattr(proc, "returncode", None), int)
        and not isinstance(getattr(proc, "returncode", None), bool)
    )
    if not root_identity_current and not root_identity_changed_after_exit:
        return False
    tree_cleanup_confirmed = True
    process_wait_confirmed = False
    if process_tree is None and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        process_tree = _process_tree_descendant_identities(pid)
    if process_tree is not None:
        tree_cleanup_confirmed = _kill_output_process_tree(process_tree)
    if not root_identity_changed_after_exit and not _output_process_identity_is_current(proc):
        return False
    # Root identity changed after reaping: never signal the reused PID/group.
    root_reaped = root_identity_changed_after_exit or _output_process_is_reaped(proc)
    try:
        if root_reaped or _output_process_is_reaped(proc):
            if process_tree is None:
                tree_cleanup_confirmed = False
        elif isinstance(pid, int) and pid > 0:
            os.killpg(pid, signal.SIGKILL)
        else:
            proc.kill()
    except ProcessLookupError:
        pass
    except OSError:
        try:
            if not _output_process_identity_is_current(proc):
                return False
            proc.kill()
        except BaseException:
            return False
    try:
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        process_wait_confirmed = False
    else:
        process_wait_confirmed = True
    if process_tree is not None:
        tree_cleanup_confirmed = (
            tree_cleanup_confirmed and
            _wait_for_output_process_tree_stop(process_tree)
        )
    return process_wait_confirmed and tree_cleanup_confirmed


def _terminate_unidentified_bounded_process(proc: subprocess.Popen[bytes]) -> bool:
    """Stop a freshly spawned process when its /proc identity cannot be read."""
    if type(proc).__module__ != "subprocess":
        return _terminate_bounded_process(proc)
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False

    group_cleanup_confirmed = False
    try:
        process_group_id = os.getpgid(pid)
    except (OSError, ValueError):
        process_group_id = None
    if process_group_id == pid:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            group_cleanup_confirmed = True
        except OSError:
            pass
        else:
            group_cleanup_confirmed = True

    root_cleanup_confirmed = group_cleanup_confirmed
    pidfd: int | None = None
    try:
        if not root_cleanup_confirmed:
            pidfd_open = getattr(os, "pidfd_open", None)
            pidfd_send_signal = getattr(signal, "pidfd_send_signal", None)
            if callable(pidfd_open) and callable(pidfd_send_signal):
                try:
                    candidate_pidfd = pidfd_open(pid, 0)
                except (AttributeError, NotImplementedError, TypeError, OSError):
                    candidate_pidfd = None
                if isinstance(candidate_pidfd, int) and not isinstance(candidate_pidfd, bool) and candidate_pidfd >= 0:
                    pidfd = candidate_pidfd
                    try:
                        pidfd_send_signal(pidfd, signal.SIGKILL, None, 0)
                    except ProcessLookupError:
                        pass
                    except (AttributeError, NotImplementedError, TypeError, OSError):
                        pass
                    else:
                        root_cleanup_confirmed = True
    finally:
        if pidfd is not None:
            try:
                os.close(pidfd)
            except OSError:
                pass

    if not root_cleanup_confirmed:
        return False
    try:
        proc.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        for stream in (getattr(proc, "stdout", None), getattr(proc, "stderr", None)):
            close = getattr(stream, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except BaseException:
                pass
    return True


def _output_process_is_reaped(proc: subprocess.Popen[bytes]) -> bool:
    pid = getattr(proc, "pid", None)
    returncode = getattr(proc, "returncode", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if not isinstance(returncode, int) or isinstance(returncode, bool):
        return False
    try:
        os.stat(f"/proc/{pid}")
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _process_has_exited_without_reaping(process_id: int) -> bool:
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        return False
    try:
        raw = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii").strip()
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return False
    try:
        close = raw.rindex(")")
        process_state = raw[close + 2 :].split()[0]
    except (IndexError, ValueError):
        return False
    return process_state in {"Z", "X", "x"}


def _process_session_descendant_identities(
    process_id: int,
    *,
    expected_process_identity: str | None = None,
) -> dict[int, str] | None:
    if not isinstance(process_id, int) or isinstance(process_id, bool) or process_id <= 0:
        return None
    try:
        proc_entries = tuple(Path("/proc").iterdir())
    except OSError:
        return None
    descendants: dict[int, str] = {}
    scan_incomplete = False
    root_identity_verified = (
        isinstance(expected_process_identity, str)
        and bool(expected_process_identity)
        and _clipboard_lock_identity_for_pid(process_id) == expected_process_identity
    )
    for proc_entry in proc_entries:
        if not proc_entry.name.isdecimal():
            continue
        member_id = int(proc_entry.name)
        if member_id == process_id:
            continue
        try:
            raw = proc_entry.joinpath("stat").read_text(encoding="ascii").strip()
        except FileNotFoundError:
            continue
        except (OSError, UnicodeDecodeError):
            scan_incomplete = True
            continue
        try:
            close = raw.rindex(")")
            fields = raw[close + 2 :].split()
            process_state = fields[0]
            session_id = int(fields[3])
            start_time = fields[19]
        except (IndexError, ValueError):
            scan_incomplete = True
            continue
        if session_id != process_id or process_state in {"Z", "X", "x"}:
            continue
        descendants[member_id] = start_time
    if scan_incomplete:
        return None
    if not root_identity_verified and descendants:
        return None
    return descendants


def run_process_bounded_output(
    argv: Sequence[str],
    input_bytes: bytes = b"",
    *,
    timeout_seconds: int,
    max_output_bytes: int,
    env: dict[str, str],
    label: str,
) -> tuple[int, bytes, bytes]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise CommandChainError("argv must be a non-empty sequence")
    if not all(isinstance(item, str) for item in argv):
        raise CommandChainError("argv must contain text")
    if not isinstance(input_bytes, bytes):
        raise CommandChainError("input bytes must be bytes")
    if len(input_bytes) > MAX_BOUNDED_PROCESS_INPUT_BYTES:
        raise CommandChainError(
            f"input bytes must not exceed {MAX_BOUNDED_PROCESS_INPUT_BYTES}"
        )
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if not isinstance(max_output_bytes, int) or isinstance(max_output_bytes, bool) or max_output_bytes <= 0:
        raise CommandChainError("max_output_bytes must be positive")
    if max_output_bytes > MAX_BOUNDED_PROCESS_OUTPUT_BYTES:
        raise CommandChainError(
            f"max_output_bytes must not exceed {MAX_BOUNDED_PROCESS_OUTPUT_BYTES}"
        )
    if not isinstance(env, dict):
        raise CommandChainError("environment must be a mapping")
    _validate_command_label(label)
    env = _filtered_environment(env)

    runtime_argv = [*argv]
    with tempfile.TemporaryFile() as stdin_file:
        stdin_file.write(input_bytes)
        stdin_file.seek(0)
        try:
            proc = subprocess.Popen(  # nosec B603
                runtime_argv,
                stdin=stdin_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                shell=False,
                start_new_session=True,
            )
            process_identity = _clipboard_lock_identity_for_pid(proc.pid)
            if not process_identity:
                cleanup_confirmed = _terminate_unidentified_bounded_process(proc)
                cleanup_suffix = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                raise CommandChainError(
                    f"{label} command process identity could not be verified{cleanup_suffix}"
                )
            setattr(proc, "_soc_process_identity", process_identity)
        except FileNotFoundError:
            raise
        except OSError:
            raise

        selector = selectors.DefaultSelector()
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        stdout_size = 0
        stderr_size = 0
        process_exit_deadline: float | None = None
        process_tree_snapshot: dict[int, str] | None = None
        process_tree_snapshot_scan_incomplete = False
        next_process_tree_snapshot = 0.0
        process_tree_at_exit: dict[int, str] | None = None
        process_tree_at_exit_scan_incomplete = False
        cleanup_attempted = False

        def capture_process_tree_at_exit() -> None:
            nonlocal process_tree_at_exit, process_tree_at_exit_scan_incomplete
            if process_tree_at_exit is not None:
                return
            process_tree_at_exit = process_tree_snapshot or {}
            if process_tree_snapshot_scan_incomplete:
                process_tree_at_exit_scan_incomplete = True
            pipe_holders = _process_pipe_holder_identities(proc)
            if pipe_holders is None:
                process_tree_at_exit_scan_incomplete = True
            else:
                process_tree_at_exit.update(pipe_holders)
            session_descendants = _process_session_descendant_identities(
                proc.pid,
                expected_process_identity=vars(proc).get("_soc_process_identity"),
            )
            if session_descendants is None:
                process_tree_at_exit_scan_incomplete = True
            else:
                process_tree_at_exit.update(session_descendants)

        try:
            if proc.stdout is not None:
                selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
            if proc.stderr is not None:
                selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
            deadline = time.monotonic() + timeout_seconds
            while selector.get_map():
                now = time.monotonic()
                if now >= next_process_tree_snapshot:
                    current_process_tree = _process_tree_descendant_identities(proc.pid)
                    if current_process_tree is not None:
                        process_tree_snapshot_scan_incomplete = False
                        if process_tree_snapshot is None:
                            process_tree_snapshot = {}
                        process_tree_snapshot.update(current_process_tree)
                    else:
                        process_tree_snapshot_scan_incomplete = True
                    next_process_tree_snapshot = now + _PROCESS_TREE_SNAPSHOT_INTERVAL_SECONDS
                root_exited_before_poll = (
                    proc.returncode is None
                    and _process_has_exited_without_reaping(getattr(proc, "pid", 0))
                )
                if root_exited_before_poll:
                    capture_process_tree_at_exit()
                if proc.poll() is not None:
                    if root_exited_before_poll or _output_process_is_reaped(proc):
                        capture_process_tree_at_exit()
                    if process_exit_deadline is None:
                        process_exit_deadline = min(deadline, now + _PIPE_DRAIN_GRACE_SECONDS)
                    active_deadline = process_exit_deadline
                else:
                    active_deadline = deadline
                remaining = active_deadline - now
                if remaining <= 0:
                    if process_exit_deadline is not None:
                        break
                    cleanup_attempted = True
                    cleanup_confirmed = _terminate_bounded_process(proc)
                    cleanup_detail = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                    raise CommandChainError(
                        _command_timeout_detail(label, timeout_seconds) + cleanup_detail
                    )
                events = selector.select(min(remaining, _PROCESS_POLL_INTERVAL_SECONDS))
                if not events:
                    continue
                for key, _event_mask in events:
                    stream = key.fileobj
                    data = os.read(stream.fileno(), 65_536)
                    if not data:
                        selector.unregister(stream)
                        try:
                            stream.close()
                        except OSError:
                            pass
                        continue
                    if key.data == "stdout":
                        stdout_chunks.append(data)
                        stdout_size += len(data)
                    else:
                        stderr_chunks.append(data)
                        stderr_size += len(data)
                    if stdout_size + stderr_size > max_output_bytes:
                        cleanup_attempted = True
                        cleanup_confirmed = _terminate_bounded_process(proc)
                        cleanup_detail = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                        raise CommandChainError(
                            f"{label} command output exceeded {max_output_bytes} bytes" + cleanup_detail
                        )
            try:
                if (
                    proc.returncode is None
                    and _process_has_exited_without_reaping(getattr(proc, "pid", 0))
                ):
                    capture_process_tree_at_exit()
                returncode = proc.poll()
                if returncode is None:
                    returncode = proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                if returncode is not None:
                    capture_process_tree_at_exit()
            except subprocess.TimeoutExpired:
                cleanup_attempted = True
                cleanup_confirmed = _terminate_bounded_process(proc)
                cleanup_detail = "" if cleanup_confirmed else "; process cleanup was not confirmed"
                raise CommandChainError(
                    _command_timeout_detail(label, timeout_seconds) + cleanup_detail
                ) from None
            if process_tree_at_exit is not None:
                cleanup_attempted = True
                cleanup_confirmed = _terminate_bounded_process(proc, process_tree=process_tree_at_exit)
                if not cleanup_confirmed:
                    raise CommandChainError(f"{label} command descendant cleanup was not confirmed")
            if process_tree_at_exit_scan_incomplete:
                cleanup_attempted = True
                raise CommandChainError(f"{label} command descendant cleanup scan was incomplete")
            return returncode, b"".join(stdout_chunks), b"".join(stderr_chunks)
        except BaseException as exc:
            if not cleanup_attempted:
                try:
                    cleanup_confirmed = _terminate_bounded_process(
                        proc,
                        process_tree=process_tree_at_exit or process_tree_snapshot,
                    )
                except BaseException:
                    exc.add_note(f"{label} command process cleanup failed")
                else:
                    if not cleanup_confirmed:
                        exc.add_note(f"{label} command process cleanup was not confirmed")
            raise
        finally:
            for key in list(selector.get_map().values()):
                stream = key.fileobj
                with suppress(KeyError, ValueError):
                    selector.unregister(stream)
                try:
                    stream.close()
                except OSError:
                    pass
            selector.close()


FORBIDDEN_COMMAND_OPERATORS = {
    "|",
    "||",
    "|&",
    "&",
    ";",
    ";;",
    "<",
    ">",
    ">>",
    "2>",
    "2>>",
    "1>",
    "1>>",
    "&>",
    "2>&1",
    "1>&2",
    "1>&1",
    "2>&2",
    "2<&1",
    "1<&0",
}
DEFAULT_COMMAND_TIMEOUT_SECONDS = 180
MAX_COMMAND_OUTPUT_CHARS = 1_000_000
MAX_COMMAND_LENGTH_CHARS = 8_192
MAX_COMMAND_SEGMENTS = 32
MAX_COMMAND_SEGMENT_TOKENS = 128
MAX_COMMAND_INPUT_CHARS = 1_000_000
MAX_FILE_READ_FOR_ERROR_CHARS = 4096
MAX_BOUNDED_PROCESS_INPUT_BYTES = (MAX_COMMAND_INPUT_CHARS * 4) + 4096
MAX_BOUNDED_PROCESS_OUTPUT_BYTES = (MAX_COMMAND_OUTPUT_CHARS * 4) + 4096


def _command_path(command: str) -> str:
    if not isinstance(command, str) or isinstance(command, bool):
        raise CommandChainError("command must be text")
    command_name = command.strip()
    if not command_name:
        raise CommandChainError("command is empty")
    if os.path.sep in command_name or (os.path.altsep and os.path.altsep in command_name):
        raise CommandChainError("command must be a bare command name without path separators")
    if _contains_command_control_chars(command_name):
        raise CommandChainError("command contains invalid control character")
    resolved = _which(command_name)
    if not resolved:
        raise CommandChainError(f"{command_name} is not available")
    command_path = Path(resolved)
    return str(command_path)


def _contains_escaped_null(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    return "\x00" in value


def _contains_http_header_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    lowered = (value or "").lower()
    if _ESCAPED_CONTROL_RE.search(lowered):
        return True
    for char in lowered:
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _contains_environment_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    return any(
        (codepoint := ord(char)) < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F
        for char in value
    )


def _contains_command_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    if _ESCAPED_CONTROL_RE.search(value.lower()):
        return True
    quote: str | None = None
    escaped = False
    for char in value:
        if escaped:
            escaped = False
            if ord(char) < 0x20 or ord(char) == 0x7F or 0x80 <= ord(char) <= 0x9F:
                return True
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if char in {"'", '"'}:
            if quote is None:
                quote = char
            elif quote == char:
                quote = None
            continue
        codepoint = ord(char)
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            if char == "\n" and quote is not None:
                continue
            return True
    return False


def _contains_command_argument_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    if _ESCAPED_CONTROL_RE.search(value.lower()):
        return True
    for char in value:
        codepoint = ord(char)
        if char == "\n":
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _contains_command_output_control_chars(value: str) -> bool:
    if isinstance(value, bool) or not isinstance(value, str):
        raise CommandChainError("value must be text")
    for char in value:
        codepoint = ord(char)
        if codepoint in (0x09, 0x0A, 0x0D):
            continue
        if codepoint < 0x20 or codepoint == 0x7F or 0x80 <= codepoint <= 0x9F:
            return True
    return False


def _validate_command_label(label: str) -> None:
    if isinstance(label, bool) or not isinstance(label, str):
        raise CommandChainError("label must be text")
    if _contains_http_header_control_chars(label):
        raise CommandChainError("label contains invalid control character")


def split_command_chain(command: str, label: str = "command") -> list[list[str]]:
    if isinstance(command, bool) or not isinstance(command, str):
        raise CommandChainError("command must be text")
    _validate_command_label(label)
    if _contains_escaped_null(command):
        raise CommandChainError(f"invalid {label} command: contains invalid null byte")
    if _contains_command_control_chars(command):
        raise CommandChainError(f"invalid {label} command: contains control characters")
    if len(command) > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")
    try:
        command_bytes_len = len(command.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise CommandChainError(f"invalid {label} command: not valid UTF-8") from exc
    if command_bytes_len > MAX_COMMAND_LENGTH_CHARS:
        raise CommandChainError(f"invalid {label} command: command too long")

    raw_segments = [""]
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            raw_segments[-1] += char
            escaped = False
            index += 1
            continue
        if char == "\\" and quote != "'":
            raw_segments[-1] += char
            escaped = True
            index += 1
            continue
        if quote is not None:
            raw_segments[-1] += char
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            raw_segments[-1] += char
            quote = char
            index += 1
            continue
        if (
            command.startswith(_CHAIN_SEGMENT_SEPARATOR, index)
            and (index == 0 or command[index - 1].isspace())
            and (index + 2 == len(command) or command[index + 2].isspace())
        ):
            raw_segments.append("")
            index += 2
            continue
        raw_segments[-1] += char
        index += 1

    segments: list[list[str]] = []
    for segment_index, raw_segment in enumerate(raw_segments):
        try:
            tokens = shlex.split(raw_segment)
        except ValueError as exc:
            raise CommandChainError(f"invalid {label} command: {exc}") from exc

        if not tokens:
            if not segments:
                raise CommandChainError(f"{label} command is empty")
            if len(segments) >= MAX_COMMAND_SEGMENTS:
                raise CommandChainError(f"{label} command has too many segments")
            raise CommandChainError(
                f"{label} command ended with &&" if segment_index == len(raw_segments) - 1
                else f"empty {label} command segment before &&"
            )
        if len(segments) >= MAX_COMMAND_SEGMENTS:
            raise CommandChainError(f"{label} command has too many segments")
        if len(tokens) > MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        segments.append(tokens)

    if not segments:
        raise CommandChainError(f"{label} command is empty")
    for segment in segments:
        for token in segment:
            if token in FORBIDDEN_COMMAND_OPERATORS:
                raise CommandChainError(f"unsupported shell operator in {label} command: {token}")
    return segments


def run_command_chain(
    segments: Sequence[Sequence[str]],
    input_text: str,
    *,
    label: str,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    max_output_chars: int = MAX_COMMAND_OUTPUT_CHARS,
    max_input_chars: int = MAX_COMMAND_INPUT_CHARS,
    personal_context: str = "",
    vocabulary: str = "",
    include_personalization_env: bool = False,
) -> str:
    if not isinstance(segments, (list, tuple)):
        raise CommandChainError("segments must be a sequence")
    if not all(isinstance(segment, (list, tuple)) for segment in segments):
        raise CommandChainError("segments must contain sequences")
    if not segments:
        raise CommandChainError(f"{label} command chain is empty")
    _validate_command_label(label)
    if len(segments) > MAX_COMMAND_SEGMENTS:
        raise CommandChainError(f"{label} command has too many segments")
    if not isinstance(max_output_chars, int) or isinstance(max_output_chars, bool):
        raise CommandChainError("max_output_chars must be an integer")
    if max_output_chars <= 0:
        raise CommandChainError("max_output_chars must be positive")
    if max_output_chars > MAX_COMMAND_OUTPUT_CHARS:
        raise CommandChainError(f"max_output_chars must not exceed {MAX_COMMAND_OUTPUT_CHARS}")
    if not isinstance(max_input_chars, int) or isinstance(max_input_chars, bool):
        raise CommandChainError("max_input_chars must be an integer")
    if max_input_chars > MAX_COMMAND_INPUT_CHARS:
        raise CommandChainError(f"max_input_chars must not exceed {MAX_COMMAND_INPUT_CHARS}")
    if max_input_chars < 0:
        raise CommandChainError("max_input_chars must be non-negative")
    if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
        raise CommandChainError("timeout_seconds must be an integer")
    if timeout_seconds <= 0:
        raise CommandChainError("timeout_seconds must be positive")
    if not isinstance(input_text, str) or isinstance(input_text, bool):
        raise CommandChainError("input text must be text")
    if not isinstance(personal_context, str) or isinstance(personal_context, bool):
        raise CommandChainError("personal context must be text")
    if not isinstance(vocabulary, str) or isinstance(vocabulary, bool):
        raise CommandChainError("vocabulary must be text")
    if not isinstance(include_personalization_env, bool):
        raise CommandChainError("include_personalization_env must be a boolean")
    if _contains_escaped_null(input_text):
        raise CommandChainError("command input contains invalid null byte")
    try:
        input_bytes = input_text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise CommandChainError("command input is not valid UTF-8") from exc

    try:
        personalization_env = command_environment(personal_context, vocabulary)
        env = (
            {key: value for key, value in personalization_env.items() if isinstance(key, str) and not _is_unsafe_env_var(key)}
            if include_personalization_env
            else {}
        )
        env = _filtered_environment(env)
    except ValueError as exc:
        raise CommandChainError(str(exc)) from exc
    output = input_text

    for segment in segments:
        if len(output) > max_input_chars:
            raise CommandChainError(f"{label} command input exceeded {max_input_chars} characters")

        cmd = list(segment)
        if len(cmd) > MAX_COMMAND_SEGMENT_TOKENS:
            raise CommandChainError(f"invalid {label} command: segment is too long")
        if not all(isinstance(item, str) for item in cmd):
            raise CommandChainError(f"{label} command segment contains non-text item")
        if not cmd:
            raise CommandChainError(f"invalid {label} command segment")
        try:
            command_chars_len = sum(len(item) + 1 for item in cmd)
            command_bytes_len = sum(len(item.encode("utf-8")) + 1 for item in cmd)
        except UnicodeEncodeError as exc:
            raise CommandChainError(f"invalid {label} command: not valid UTF-8") from exc
        if command_chars_len > MAX_COMMAND_LENGTH_CHARS or command_bytes_len > MAX_COMMAND_LENGTH_CHARS:
            raise CommandChainError(f"invalid {label} command: command too long")
        executable = str(cmd[0]).strip()
        if not executable:
            raise CommandChainError(f"invalid {label} command segment")
        if _contains_escaped_null(executable) or any(_contains_escaped_null(str(arg)) for arg in cmd[1:]):
            raise CommandChainError(f"{label} command contains invalid null byte")
        if _contains_command_control_chars(executable) or any(
            _contains_command_argument_control_chars(str(arg)) for arg in cmd[1:]
        ):
            raise CommandChainError(f"{label} command contains invalid control character")
        runtime_command = _command_path(executable)
        try:
            max_output_bytes = (max_output_chars * 4) + 4096
            returncode, stdout_data, stderr_data = run_process_bounded_output(
                [runtime_command, *cmd[1:]],
                input_bytes,
                timeout_seconds=timeout_seconds,
                max_output_bytes=max_output_bytes,
                env=env,
                label=label,
            )
            if returncode != 0:
                detail = _command_failure_detail(returncode, len(stdout_data), len(stderr_data))
                raise CommandChainError(f"{label} command failed: {detail}")
            try:
                decoded_output = stdout_data.decode("utf-8")
            except UnicodeDecodeError:
                raise CommandChainError("command output is not valid UTF-8") from None
            segment_output = decoded_output.rstrip("\r\n")
            if len(segment_output) > max_output_chars:
                raise CommandChainError(f"{label} command output exceeded {max_output_chars} characters")
            if _contains_escaped_null(segment_output):
                raise CommandChainError("command output contains invalid null byte")
            if _contains_command_output_control_chars(segment_output):
                raise CommandChainError("command output contains invalid control character")
            output = segment_output
            try:
                input_bytes = output.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise CommandChainError("command output is not valid UTF-8") from exc
        except FileNotFoundError as exc:
            raise CommandChainError(f"{label} command not found: {executable}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandChainError(f"{label} command timed out after {timeout_seconds}s") from exc
        except OSError:
            raise CommandChainError(f"{label} command execution failed") from None
    return output


def _filesize(file: io.BufferedRandom) -> int:
    if not hasattr(file, "seek") or not hasattr(file, "tell"):
        raise CommandChainError("file must be a binary file handle")
    file.seek(0, 2)
    return file.tell()


def _read_file_head(file: io.BufferedRandom, max_chars: int) -> str:
    if not hasattr(file, "seek") or not hasattr(file, "read"):
        raise CommandChainError("file must be a binary file handle")
    if not isinstance(max_chars, int) or isinstance(max_chars, bool):
        raise CommandChainError("max_chars must be an integer")
    if max_chars <= 0:
        raise CommandChainError("max_chars must be positive")
    file.seek(0)
    decoder = codecs.getincrementaldecoder("utf-8")()
    text_parts: list[str] = []
    text_length = 0
    try:
        while text_length < max_chars:
            raw = file.read(4096)
            if not raw:
                break
            for byte in raw:
                decoded = decoder.decode(bytes((byte,)), final=False)
                if not decoded:
                    continue
                remaining = max_chars - text_length
                text_parts.append(decoded[:remaining])
                text_length += min(len(decoded), remaining)
                if text_length >= max_chars:
                    break
        if text_length < max_chars:
            tail = decoder.decode(b"", final=True)
            if tail:
                text_parts.append(tail[: max_chars - text_length])
        text = "".join(text_parts)
    except UnicodeDecodeError:
        raise CommandChainError("command output is not valid UTF-8") from None
    if _contains_escaped_null(text):
        raise CommandChainError("command output contains invalid null byte")
    return text
_CHAIN_SEGMENT_SEPARATOR = "".join(["&", "&"])
