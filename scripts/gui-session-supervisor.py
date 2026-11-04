#!/usr/bin/python3
from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import math
import os
import re
import select
import selectors
import signal
import stat
import subprocess
import sys
import time


PR_SET_PDEATHSIG = 1
PR_SET_CHILD_SUBREAPER = 36
PR_GET_CHILD_SUBREAPER = 37
EXIT_INTERNAL = 125
POLL_SECONDS = 0.02
MAX_FRAME_BYTES = 256
MAX_TAIL_BYTES = 8192
INSTALL_GUARD_RESULT_MAX_BYTES = 128
INSTALL_GUARD_RESULT_ERROR_CODES = (
    "MISSING",
    "EMPTY",
    "META",
    "FRAME",
    "CODE",
    "INIT",
    "STATUS_FD",
    "STATUS_SPAWN",
    "STATUS_FRAME",
    "WRITE",
)
INSTALL_GUARD_HELPER_CODES = (
    "OUTER_EMPTY",
    "OUTER_CONTROL",
    "OUTER_DUPLICATE",
    "OUTER_MULTIPLE",
    "OUTER_UNKNOWN",
)
INSTALL_GUARD_RESULT_LINES = frozenset(
    {
        b"OK",
        b"ROOT_FAIL ROOT_META",
        b"ROOT_FAIL ROOT_EUID",
        b"ROOT_FAIL ROOT_UID_MAP",
        b"ROOT_FAIL ROOT_FDINFO",
        b"ROOT_FAIL ROOT_MOUNT",
        b"CHAIN_FAIL OWNER",
        b"CHAIN_FAIL MODE",
        b"CHAIN_FAIL META",
        b"CHAIN_FAIL FD",
        b"CHAIN_FAIL MISSING",
        b"INSTALL_GUARD_FAIL CORE_FD",
        b"INSTALL_GUARD_FAIL CORE_FRAME",
        b"INSTALL_GUARD_FAIL CORE_STATUS",
        b"INSTALL_GUARD_FAIL CORE_STATUS_OK_NONZERO",
        b"INSTALL_GUARD_FAIL CORE_STATUS_FAIL_ZERO",
        b"INSTALL_GUARD_FAIL CORE_STATUS_ROOT_EXIT",
        b"INSTALL_GUARD_FAIL CORE_STATUS_CHAIN_EXIT",
        b"INSTALL_GUARD_FAIL CORE_STATUS_MULTIPLE",
        b"INSTALL_GUARD_FAIL CORE_PARSER_EXIT",
        b"INSTALL_GUARD_FAIL CORE_PARSER_EMPTY",
        b"INSTALL_GUARD_FAIL CORE_PARSER_MULTIPLE",
        b"INSTALL_GUARD_FAIL CORE_PARSER_CONTROL",
        b"INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN",
        *(
            f"INSTALL_GUARD_FAIL CORE_RESULT_{code}".encode("ascii")
            for code in INSTALL_GUARD_RESULT_ERROR_CODES
        ),
    }
)
INSTALL_GUARD_STATUS_LINES = frozenset(
    {
        b"INSTALL_GUARD_FAIL ARG",
        b"INSTALL_GUARD_FAIL OPTIN",
        b"INSTALL_GUARD_FAIL ENV",
        b"INSTALL_GUARD_FAIL SUPERVISOR",
        b"INSTALL_GUARD_FAIL NAMESPACE",
        b"ROOT_FAIL ROOT_META",
        b"ROOT_FAIL ROOT_EUID",
        b"ROOT_FAIL ROOT_UID_MAP",
        b"ROOT_FAIL ROOT_FDINFO",
        b"ROOT_FAIL ROOT_MOUNT",
        b"CHAIN_FAIL OWNER",
        b"CHAIN_FAIL MODE",
        b"CHAIN_FAIL META",
        b"CHAIN_FAIL FD",
        b"CHAIN_FAIL MISSING",
        b"INSTALL_GUARD_FAIL CORE_FD",
        b"INSTALL_GUARD_FAIL CORE_FRAME",
        b"INSTALL_GUARD_FAIL CORE_STATUS",
        b"INSTALL_GUARD_FAIL CORE_STATUS_OK_NONZERO",
        b"INSTALL_GUARD_FAIL CORE_STATUS_FAIL_ZERO",
        b"INSTALL_GUARD_FAIL CORE_STATUS_ROOT_EXIT",
        b"INSTALL_GUARD_FAIL CORE_STATUS_CHAIN_EXIT",
        b"INSTALL_GUARD_FAIL CORE_STATUS_MULTIPLE",
        b"INSTALL_GUARD_FAIL CORE_PARSER_EXIT",
        b"INSTALL_GUARD_FAIL CORE_PARSER_EMPTY",
        b"INSTALL_GUARD_FAIL CORE_PARSER_MULTIPLE",
        b"INSTALL_GUARD_FAIL CORE_PARSER_CONTROL",
        b"INSTALL_GUARD_FAIL CORE_PARSER_UNKNOWN",
        *(
            f"INSTALL_GUARD_FAIL CORE_RESULT_{code}".encode("ascii")
            for code in INSTALL_GUARD_RESULT_ERROR_CODES
        ),
        b"INSTALL_GUARD_FAIL OUTER_HELPER",
        b"INSTALL_GUARD_FAIL OUTER_STATUS",
        *(
            f"INSTALL_GUARD_FAIL {code}".encode("ascii")
            for code in INSTALL_GUARD_HELPER_CODES
        ),
    }
)
RUNNER_CHILD_CODES = ("EMPTY", "CONTROL", "FRAME", "CODE")
RUNNER_STATUS_LINES = frozenset(
    f"INSTALL_GUARD_FAIL {code}".encode("ascii")
    for code in (
        "RUNNER_INPUT",
        "RUNNER_SUBREAPER",
        "RUNNER_CONTRACT",
        "RUNNER_SPAWN",
        "RUNNER_IDENTITY",
        "RUNNER_PIDFD",
        "RUNNER_CHILD",
        "RUNNER_CHILD_LAUNCH",
        "RUNNER_CLEANUP",
        *(f"RUNNER_CHILD_{suffix}" for suffix in RUNNER_CHILD_CODES),
    )
)
RUNNER_CHILD_UNTRUSTED_CODES = frozenset(
    f"INSTALL_GUARD_FAIL RUNNER_CHILD_{suffix}".encode("ascii")
    for suffix in ("STATUS_UNKNOWN", "BWRAP", "EXEC", "OTHER")
)
RUNNER_STATUS_LINES = RUNNER_STATUS_LINES | RUNNER_CHILD_UNTRUSTED_CODES
RUNNER_LOCAL_STATUS_LINES = RUNNER_STATUS_LINES - RUNNER_CHILD_UNTRUSTED_CODES
RUNNER_CHILD_STATUS_RE = re.compile(
    rb"(?:INSTALL_GUARD_FAIL|ROOT_FAIL|CHAIN_FAIL) [A-Z0-9_]+"
)
RUNNER_CHILD_BWRAP_RE = re.compile(rb"bwrap: [\x20-\x7e]+")
RUNNER_CHILD_EXEC_RE = re.compile(
    rb"(?:/usr/bin/env|env|/usr/bin/python3.14|/usr/bin/python3|"
    rb"python3.14|python3|ld-linux-x86-64.so.2|"
    rb"/lib64/ld-linux-x86-64.so.2|"
    rb"/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2): [\x20-\x7e]+"
)

_ANSI_RE = re.compile(
    r"(?:\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|P[^\x1b]*(?:\x1b\\))"
    r"|\x9b[0-?]*[ -/]*[@-~]|\x9d[^\x07]*(?:\x07|\x9c)|\x90[^\x9c]*\x9c)"
)
_SECRET_RE = re.compile(
    r"(?ix)(?:"
    r"api[_-]?key|authorization\s+bearer|access_token|refresh_token|token|"
    r"password|passphrase|cookie|nonce|secret|authority|xauth"
    r")\s*(?:[:=]\s*|\s+)(?:bearer\s+)?[^\s,;]+"
    r"|\bBearer\s+[A-Za-z0-9._~+/=-]+"
    r"|\b(?:sk|ghp)[-_][A-Za-z0-9_-]{8,}\b"
    r"|\b[0-9A-Fa-f]{16,}\b"
    r"|\b[A-Za-z0-9+/]{32,}={0,2}\b"
)


def _sanitize_bytes(data: bytes, limit: int = MAX_TAIL_BYTES) -> bytes:
    text = _ANSI_RE.sub("", data.decode("utf-8", "backslashreplace"))
    text = _SECRET_RE.sub("[REDACTED]", text)
    text = "".join(
        character if character in "\n\t" or character.isprintable() else "?"
        for character in text
    )
    output = bytearray()
    for character in text:
        encoded = character.encode("utf-8", "strict")
        if len(output) + len(encoded) > limit:
            break
        output.extend(encoded)
    return bytes(output)


def _write_sanitized_stdin() -> int:
    arguments = sys.argv[2:]
    if len(arguments) not in (0, 2) or (
        arguments and arguments[0] != "--max-output-bytes"
    ):
        return 2
    limit = MAX_TAIL_BYTES
    if arguments:
        value = arguments[1]
        if not value or not value.isascii() or not value.isdigit():
            return 2
        try:
            limit = int(value, 10)
        except ValueError:
            return 2
        if not 0 < limit <= MAX_TAIL_BYTES:
            return 2
    data = sys.stdin.buffer.read(MAX_TAIL_BYTES)
    output = _sanitize_bytes(data, limit)
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()
    return 0


def _read_sanitized_tail(path: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        entry = os.fstat(descriptor)
        if not stat.S_ISREG(entry.st_mode):
            raise OSError(errno.EINVAL, "controller stderr is not regular")
        if entry.st_uid != os.geteuid() or entry.st_nlink != 1:
            raise OSError(errno.EPERM, "controller stderr ownership is unsafe")
        offset = max(0, entry.st_size - MAX_TAIL_BYTES)
        return _sanitize_bytes(os.pread(descriptor, MAX_TAIL_BYTES, offset))
    finally:
        os.close(descriptor)


def _read_install_guard_status(path: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_mode & 0o077
            or before.st_size > MAX_TAIL_BYTES
        ):
            raise OSError(errno.EINVAL, "invalid install status")
        data = bytearray()
        offset = 0
        while offset < before.st_size:
            chunk = os.pread(descriptor, min(1024, before.st_size - offset), offset)
            if not chunk:
                raise OSError(errno.EIO, "short install status")
            data.extend(chunk)
            offset += len(chunk)
        if os.pread(descriptor, 1, offset):
            raise OSError(errno.EOVERFLOW, "install status changed")
        after = os.fstat(descriptor)
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_mode != before.st_mode
            or after.st_uid != before.st_uid
            or after.st_nlink != before.st_nlink
            or after.st_size != len(data)
        ):
            raise OSError(errno.EAGAIN, "install status changed")
        return bytes(data)
    finally:
        os.close(descriptor)


def _install_guard_status_output(path: str) -> bytes:
    try:
        data = _read_install_guard_status(path)
    except OSError:
        return b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"
    if not data:
        return b"INSTALL_GUARD_FAIL OUTER_EMPTY\n"
    if any((byte < 0x20 and byte != 0x0A) or 0x7F <= byte <= 0x9F for byte in data):
        return b"INSTALL_GUARD_FAIL OUTER_CONTROL\n"
    try:
        data.decode("ascii")
    except UnicodeDecodeError:
        return b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"
    if not data.endswith(b"\n"):
        return b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"
    lines = data[:-1].split(b"\n")
    if len(lines) != 1:
        if len(set(lines)) == 1:
            return b"INSTALL_GUARD_FAIL OUTER_DUPLICATE\n"
        return b"INSTALL_GUARD_FAIL OUTER_MULTIPLE\n"
    helper_line = _install_guard_helper_status_line(lines[0])
    if helper_line is not None:
        return helper_line
    if lines[0] in INSTALL_GUARD_STATUS_LINES or lines[0] in RUNNER_STATUS_LINES:
        return lines[0] + b"\n"
    return b"INSTALL_GUARD_FAIL OUTER_UNKNOWN\n"


class _InstallGuardResultReadError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _result_read_error(code: str) -> None:
    raise _InstallGuardResultReadError(code)


def _read_install_guard_result(path: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    parent = os.path.dirname(path)
    leaf = os.path.basename(path)
    try:
        parent_fd = os.open(
            parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as error:
        if error.errno == errno.ENOENT:
            _result_read_error("MISSING")
        _result_read_error("META")
    descriptor = None
    try:
        try:
            descriptor = os.open(leaf, flags, dir_fd=parent_fd)
        except OSError as error:
            if error.errno == errno.ENOENT:
                _result_read_error("MISSING")
            _result_read_error("META")
        try:
            before = os.fstat(descriptor)
        except OSError:
            _result_read_error("META")
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_gid != os.getegid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > INSTALL_GUARD_RESULT_MAX_BYTES
        ):
            _result_read_error("META")
        data = bytearray()
        offset = 0
        while offset < before.st_size:
            try:
                chunk = os.pread(descriptor, min(32, before.st_size - offset), offset)
            except OSError:
                _result_read_error("META")
            if not chunk:
                _result_read_error("FRAME")
            data.extend(chunk)
            offset += len(chunk)
        try:
            if os.pread(descriptor, 1, offset):
                _result_read_error("FRAME")
            after = os.fstat(descriptor)
        except _InstallGuardResultReadError:
            raise
        except OSError:
            _result_read_error("META")
        if (
            after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_mode != before.st_mode
            or after.st_uid != before.st_uid
            or after.st_gid != before.st_gid
            or after.st_nlink != before.st_nlink
            or after.st_size != before.st_size
        ):
            _result_read_error("META")
        try:
            os.unlink(leaf, dir_fd=parent_fd)
        except OSError:
            _result_read_error("META")
        return bytes(data)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent_fd)


def _install_guard_result_output(path: str) -> bytes:
    try:
        data = _read_install_guard_result(path)
    except _InstallGuardResultReadError as error:
        return f"INSTALL_GUARD_FAIL CORE_RESULT_{error.code}\n".encode("ascii")
    except OSError:
        return b"INSTALL_GUARD_FAIL CORE_RESULT_META\n"
    if len(data) == 0:
        return b"INSTALL_GUARD_FAIL CORE_RESULT_EMPTY\n"
    if (
        len(data) > INSTALL_GUARD_RESULT_MAX_BYTES
        or not data.isascii()
        or any(byte < 0x20 or byte == 0x7F for byte in data[:-1])
        or not data.endswith(b"\n")
        or data.count(b"\n") != 1
    ):
        return b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n"
    line = data[:-1]
    if line not in INSTALL_GUARD_RESULT_LINES:
        if re.fullmatch(rb"[A-Z0-9_]+(?: [A-Z0-9_]+)?", line):
            return b"INSTALL_GUARD_FAIL CORE_RESULT_CODE\n"
        return b"INSTALL_GUARD_FAIL CORE_RESULT_FRAME\n"
    return data


def _install_guard_helper_status_line(line: bytes) -> bytes | None:
    helper_lines = frozenset(
        f"INSTALL_GUARD_FAIL {code}".encode("ascii")
        for code in INSTALL_GUARD_HELPER_CODES
    )
    if line in helper_lines:
        return line + b"\n"
    return None


RUNNER_INPUT_BYTES = 256 * 1024
RUNNER_NODE_LIMIT = 64
RUNNER_INFO_MAX_BYTES = 4096
RUNNER_INFO_NAMESPACE_KEYS = (
    "pid-namespace",
    "mnt-namespace",
    "net-namespace",
    "ipc-namespace",
    "uts-namespace",
    "cgroup-namespace",
)
RUNNER_INFO_KEYS = frozenset(
    {
        "child-pid",
        "cgroup-namespace",
        "ipc-namespace",
        "mnt-namespace",
        "net-namespace",
        "pid-namespace",
        "uts-namespace",
    }
)
RUNNER_BWRAP = "/usr/bin/bwrap"
RUNNER_ENV = "/usr/bin/env"
RUNNER_PYTHON = "/usr/bin/python3.14"
RUNNER_XVFB = "/home/teladi/.local/bin/Xvfb"
RUNNER_TRUSTED_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _runner_failure(code: str) -> bytes:
    return f"INSTALL_GUARD_FAIL {code}\n".encode("ascii")


def _runner_trusted_file(path: str, owner: int) -> bool:
    try:
        entry = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISREG(entry.st_mode)
        and entry.st_uid == owner
        and entry.st_nlink == 1
        and not entry.st_mode & 0o022
        and bool(entry.st_mode & 0o111)
    )


def _runner_trusted_dir(path: str, owner: int) -> bool:
    try:
        entry = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISDIR(entry.st_mode)
        and entry.st_uid == owner
        and not entry.st_mode & 0o022
    )


def _runner_bwrap_command(command: list[str], ready_fd: int | None = None) -> list[str]:
    if len(command) < 8 or command[:2] != [RUNNER_ENV, "-i"]:
        raise ValueError("runner command contract")
    if command[2:5] != [
        f"PATH={RUNNER_TRUSTED_PATH}",
        "LC_ALL=C",
        "PYTHONDONTWRITEBYTECODE=1",
    ]:
        raise ValueError("runner environment contract")
    if command[5:9] != [RUNNER_PYTHON, "-I", "-S", "-"]:
        raise ValueError("runner python contract")
    repo = os.path.realpath(os.path.join(os.path.dirname(__file__), os.pardir))
    if not all(
        (
            _runner_trusted_file(path, 0)
            for path in (RUNNER_BWRAP, RUNNER_ENV, RUNNER_PYTHON)
        )
    ):
        raise OSError(errno.EPERM, "runner binary trust")
    if not _runner_trusted_file(RUNNER_XVFB, os.getuid()):
        raise OSError(errno.EPERM, "runner Xvfb trust")
    if not _runner_trusted_dir(repo, os.getuid()) or os.path.realpath(repo) != repo:
        raise OSError(errno.EPERM, "runner repository trust")
    if not _runner_trusted_dir("/", 0) or os.path.realpath("/") != "/":
        raise OSError(errno.EPERM, "runner repository trust")
    command_vector = [
        RUNNER_BWRAP,
        "--unshare-all",
        "--new-session",
        "--die-with-parent",
        "--as-pid-1",
        "--clearenv",
        "--ro-bind",
        "/",
        "/",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--tmpfs",
        "/run",
        "--tmpfs",
        "/home",
        "--dir",
        "/home/teladi",
        "--dir",
        "/home/teladi/.local",
        "--dir",
        "/home/teladi/.local/bin",
        "--ro-bind",
        repo,
        repo,
        "--ro-bind",
        RUNNER_XVFB,
        RUNNER_XVFB,
        "--chdir",
        repo,
        "--",
    ]
    command_vector += [
        RUNNER_ENV,
        "-i",
        f"PATH={RUNNER_TRUSTED_PATH}",
        "LC_ALL=C",
        "PYTHONDONTWRITEBYTECODE=1",
        RUNNER_PYTHON,
        "-I",
        "-S",
        "-",
    ] + command[9:]
    if ready_fd is not None:
        delimiter = command_vector.index("--")
        command_vector[delimiter:delimiter] = ["--info-fd", str(ready_fd)]
    return command_vector


def _runner_unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate info key")
        result[key] = value
    return result


def _runner_info_object(data: bytes):
    try:
        value = json.loads(data.decode("ascii"), object_pairs_hook=_runner_unique_pairs)
    except (RecursionError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or set(value) != RUNNER_INFO_KEYS:
        return None
    for key, item in value.items():
        if (
            not isinstance(item, int)
            or isinstance(item, bool)
            or not 0 < item <= 0x7FFFFFFFFFFFFFFF
        ):
            return None
    return value


def _runner_read_info(descriptor: int, deadline: float):
    data = bytearray()
    try:
        os.set_blocking(descriptor, False)
        while len(data) <= RUNNER_INFO_MAX_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            readable, _, _ = select.select([descriptor], [], [], remaining)
            if not readable:
                return None
            chunk = os.read(descriptor, RUNNER_INFO_MAX_BYTES + 1 - len(data))
            if not chunk:
                return _runner_info_object(bytes(data))
            data.extend(chunk)
    except (OSError, ValueError):
        return None
    return None


def _runner_proc_info(pid: int):
    try:
        with open(f"/proc/{pid}/stat", "rb", buffering=0) as handle:
            fields = handle.read(65536).rsplit(b") ", 1)[1].split()
        return fields[0], int(fields[1]), int(fields[19])
    except (
        FileNotFoundError,
        ProcessLookupError,
        PermissionError,
        IndexError,
        ValueError,
        OSError,
    ):
        return None


def _runner_namespace_inodes(pid: int):
    namespace_paths = {
        "pid-namespace": "pid",
        "mnt-namespace": "mnt",
        "net-namespace": "net",
        "ipc-namespace": "ipc",
        "uts-namespace": "uts",
        "cgroup-namespace": "cgroup",
    }
    try:
        return {
            key: os.stat(f"/proc/{pid}/ns/{name}").st_ino
            for key, name in namespace_paths.items()
        }
    except OSError:
        return None


def _runner_bind_info(info: dict, launcher_pid: int, launcher_start: int):
    launcher = _runner_proc_info(launcher_pid)
    if (
        launcher is None
        or launcher[0] == b"Z"
        or launcher[1] != os.getpid()
        or launcher[2] != launcher_start
    ):
        return None
    try:
        children = _runner_children(launcher_pid)
    except OSError:
        return None
    candidates = []
    for child in children:
        child_info = _runner_proc_info(child)
        if child_info is None or child_info[0] == b"Z" or child_info[1] != launcher_pid:
            continue
        child_namespaces = _runner_namespace_inodes(child)
        if (
            child == info["child-pid"]
            and child_namespaces is not None
            and all(
                child_namespaces[key] == info[key] for key in RUNNER_INFO_NAMESPACE_KEYS
            )
        ):
            candidates.append((child, child_info[2]))
    if len(candidates) != 1:
        return None
    child_pid, child_start = candidates[0]
    try:
        child_pidfd = os.pidfd_open(child_pid, 0)
    except OSError:
        return None
    rebound_launcher = _runner_proc_info(launcher_pid)
    rebound_child = _runner_proc_info(child_pid)
    if (
        rebound_launcher is None
        or rebound_launcher[0] == b"Z"
        or rebound_launcher[1] != os.getpid()
        or rebound_launcher[2] != launcher_start
        or rebound_child is None
        or rebound_child[0] == b"Z"
        or rebound_child[1] != launcher_pid
        or rebound_child[2] != child_start
    ):
        os.close(child_pidfd)
        return None
    rebound_namespaces = _runner_namespace_inodes(child_pid)
    if rebound_namespaces is None or any(
        rebound_namespaces[key] != info[key] for key in RUNNER_INFO_NAMESPACE_KEYS
    ):
        os.close(child_pidfd)
        return None
    return child_pid, child_start, child_pidfd


def _runner_identity(pid: int):
    info = _runner_proc_info(pid)
    if info is None:
        return None
    return info[0], info[2]


def _runner_send(pid: int, start: int, pidfd: int, signum: int) -> bool:
    current = _runner_proc_info(pid)
    if current is None or current[0] == b"Z" or current[2] != start:
        return False
    try:
        signal.pidfd_send_signal(pidfd, signum, None, 0)
    except OSError:
        return False
    return True


def _runner_children(pid: int) -> list[int]:
    with open(f"/proc/{pid}/task/{pid}/children", "rb", buffering=0) as handle:
        data = handle.read(4096)
    if len(data) >= 4096:
        raise OSError(errno.EOVERFLOW, "runner child list too large")
    children = data.split()
    if len(children) > RUNNER_NODE_LIMIT:
        raise OSError(errno.EOVERFLOW, "runner child list too large")
    try:
        return [int(value, 10) for value in children]
    except ValueError:
        raise OSError(errno.EINVAL, "runner child list invalid") from None


def _runner_collect(handles: dict[int, tuple[int, int, int]], root_pid: int) -> bool:
    queue = [root_pid, os.getpid()]
    seen: set[int] = set()
    while queue and len(seen) < RUNNER_NODE_LIMIT:
        parent = queue.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        try:
            children = _runner_children(parent)
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return False
        for child in children:
            if child in seen:
                continue
            info = _runner_proc_info(child)
            if info is None or info[0] == b"Z" or info[1] != parent:
                continue
            existing = handles.get(child)
            if existing is not None:
                if existing[0] != info[2] or existing[2] != parent:
                    return False
            else:
                try:
                    child_pidfd = os.pidfd_open(child, 0)
                except OSError:
                    return False
                rebound = _runner_proc_info(child)
                if (
                    rebound is None
                    or rebound[0] == b"Z"
                    or rebound[1] != parent
                    or rebound[2] != info[2]
                ):
                    os.close(child_pidfd)
                    return False
                handles[child] = (info[2], child_pidfd, parent)
            queue.append(child)
    return not queue


def _runner_reap_children(handles: dict[int, tuple[int, int, int]]) -> None:
    for _ in range(RUNNER_NODE_LIMIT):
        try:
            pid, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        except InterruptedError:
            continue
        if pid == 0:
            return
        handle = handles.pop(pid, None)
        if handle is not None and handle[1] is not None:
            os.close(handle[1])


def _runner_live_handles(handles: dict[int, tuple[int, int, int]]) -> tuple[bool, bool]:
    live = False
    uncertain = False
    for pid, (start, _pidfd, parent) in list(handles.items()):
        info = _runner_proc_info(pid)
        if info is None:
            if os.path.exists(f"/proc/{pid}"):
                uncertain = True
            continue
        if info[2] != start or info[0] == b"Z":
            continue
        if info[1] not in (parent, os.getpid()):
            return True, True
        live = True
    return live, uncertain


def _runner_signal_all(handles: dict[int, tuple[int, int, int]], signum: int) -> None:
    for pid, (start, pidfd, _parent) in list(handles.items()):
        if pidfd is not None:
            _runner_send(pid, start, pidfd, signum)


def _runner_waitpid_empty() -> bool:
    try:
        pid, _status = os.waitpid(-1, os.WNOHANG)
    except ChildProcessError:
        return True
    except InterruptedError:
        return False
    return pid == 0


def _runner_pidfd_exited(pidfd: int | None) -> bool:
    if pidfd is None:
        return False
    try:
        poller = select.poll()
        poller.register(pidfd, select.POLLIN | select.POLLHUP | select.POLLERR)
        return bool(poller.poll(0))
    except (OSError, ValueError):
        return False


def _runner_signal_group(root_pid: int, root_start: int | None, signum: int) -> None:
    if root_start is None:
        return
    info = _runner_proc_info(root_pid)
    if info is None or info[0] == b"Z" or info[2] != root_start:
        return
    try:
        if os.getpgid(root_pid) != root_pid:
            return
        os.killpg(root_pid, signum)
    except OSError:
        return


def _runner_signal_direct(
    process: subprocess.Popen | None, start: int | None, signum: int
) -> None:
    if process is None or process.poll() is not None or start is None:
        return
    info = _runner_proc_info(process.pid)
    if info is None or info[0] == b"Z" or info[1] != os.getpid() or info[2] != start:
        return
    try:
        process.send_signal(signum)
    except OSError:
        return


def _runner_cleanup(
    handles: dict[int, tuple[int, int | None, int]],
    root_pid: int,
    grace: float,
    process: subprocess.Popen | None = None,
    root_start: int | None = None,
    terminal_clean: bool = False,
) -> bool:
    if terminal_clean:
        root_handle = handles.get(root_pid)
        return (
            root_handle is not None
            and _runner_pidfd_exited(root_handle[1])
            and _runner_has_no_children()
            and _runner_waitpid_empty()
        )
    deadline = time.monotonic() + max(0.05, min(grace, 8.0))
    _runner_signal_all(handles, signal.SIGTERM)
    _runner_signal_direct(process, root_start, signal.SIGTERM)
    _runner_signal_group(root_pid, root_start, signal.SIGTERM)
    stable = 0
    while time.monotonic() < deadline:
        _runner_reap_children(handles)
        collected = _runner_collect(handles, root_pid)
        live, live_uncertain = _runner_live_handles(handles)
        if collected and not live and not live_uncertain and _runner_waitpid_empty():
            stable += 1
        else:
            stable = 0
        if stable >= 2:
            return True
        time.sleep(POLL_SECONDS)
    _runner_signal_all(handles, signal.SIGKILL)
    _runner_signal_direct(process, root_start, signal.SIGKILL)
    _runner_signal_group(root_pid, root_start, signal.SIGKILL)
    deadline = time.monotonic() + max(0.05, min(grace, 8.0))
    stable = 0
    while time.monotonic() < deadline:
        _runner_reap_children(handles)
        collected = _runner_collect(handles, root_pid)
        live, live_uncertain = _runner_live_handles(handles)
        if collected and not live and not live_uncertain and _runner_waitpid_empty():
            stable += 1
        else:
            stable = 0
        if stable >= 2:
            return True
        time.sleep(POLL_SECONDS)
    _runner_reap_children(handles)
    live, live_uncertain = _runner_live_handles(handles)
    return (
        not live
        and not live_uncertain
        and _runner_waitpid_empty()
        and _runner_collect(handles, root_pid)
    )


def _runner_abort_direct(
    process: subprocess.Popen, start: int | None, grace: float
) -> bool:
    if process.poll() is not None:
        return True
    info = _runner_proc_info(process.pid)
    if (
        info is None
        or info[0] == b"Z"
        or start is None
        or info[2] != start
        or info[1] != os.getpid()
    ):
        return False
    try:
        process.kill()
        process.wait(timeout=grace)
        return process.poll() is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def _runner_read_stdin() -> bytes:
    fd = sys.stdin.buffer.fileno()
    selector = selectors.PollSelector()
    payload = bytearray()
    deadline = time.monotonic() + 1.0
    try:
        os.set_blocking(fd, False)
        selector.register(fd, selectors.EVENT_READ)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            if not selector.select(remaining):
                raise TimeoutError
            chunk = os.read(fd, 8192)
            if not chunk:
                return bytes(payload)
            payload.extend(chunk)
            if len(payload) > RUNNER_INPUT_BYTES:
                raise ValueError("runner input too large")
    finally:
        selector.close()


def _runner_has_no_children() -> bool:
    for _ in range(2):
        try:
            if _runner_children(os.getpid()):
                return False
            if not _runner_waitpid_empty():
                return False
        except (OSError, ValueError):
            return False
        time.sleep(POLL_SECONDS)
    return True


def _runner_pump(
    process: subprocess.Popen, payload: bytes, deadline: float
) -> tuple[int, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    stdin_fd = process.stdin.fileno()
    stderr_fd = process.stderr.fileno()
    os.set_blocking(stdin_fd, False)
    os.set_blocking(stderr_fd, False)
    selector.register(stdin_fd, selectors.EVENT_WRITE, "stdin")
    selector.register(stderr_fd, selectors.EVENT_READ, "stderr")
    offset = 0
    stderr = bytearray()
    stderr_eof = False
    overflow = False

    def close_stdin() -> None:
        stream = process.stdin
        if stream is None:
            return
        try:
            raw = stream.detach()
            raw.close()
        except (OSError, ValueError):
            try:
                stream.close()
            except (OSError, ValueError):
                pass
        process.stdin = None

    try:
        while True:
            if process.poll() is not None and not stderr_eof:
                selector.modify(stderr_fd, selectors.EVENT_READ, "stderr")
            if process.poll() is not None and stderr_eof:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return (
                    process.poll() if process.poll() is not None else -1,
                    bytes(stderr),
                    True,
                    False,
                )
            if not selector.get_map():
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    return -1, bytes(stderr), True, False
                break
            events = selector.select(remaining)
            if not events:
                return (
                    process.poll() if process.poll() is not None else -1,
                    bytes(stderr),
                    True,
                    False,
                )
            for key, mask in events:
                if key.data == "stdin" and mask & selectors.EVENT_WRITE:
                    if offset >= len(payload):
                        selector.unregister(stdin_fd)
                        close_stdin()
                    else:
                        try:
                            offset += os.write(
                                stdin_fd, payload[offset : offset + 8192]
                            )
                        except (BrokenPipeError, OSError):
                            selector.unregister(stdin_fd)
                            close_stdin()
                elif key.data == "stderr" and mask & selectors.EVENT_READ:
                    chunk = os.read(stderr_fd, 8193)
                    if not chunk:
                        selector.unregister(stderr_fd)
                        stderr_eof = True
                    else:
                        stderr.extend(chunk)
                        if len(stderr) > MAX_TAIL_BYTES:
                            overflow = True
                            return (
                                process.poll() if process.poll() is not None else -1,
                                bytes(stderr[: MAX_TAIL_BYTES + 1]),
                                False,
                                overflow,
                            )
        return process.returncode, bytes(stderr), False, overflow
    finally:
        selector.close()
        if process.stdin is not None:
            close_stdin()


def _classify_install_guard_bytes(data: bytes, ready: bool = False) -> bytes:
    if not data:
        return b"INSTALL_GUARD_FAIL RUNNER_CHILD_EMPTY\n"
    if any((byte < 0x20 and byte != 0x0A) or byte == 0x7F for byte in data):
        return b"INSTALL_GUARD_FAIL RUNNER_CHILD_CONTROL\n"
    if len(data) > MAX_TAIL_BYTES or not data.endswith(b"\n") or data.count(b"\n") != 1:
        return b"INSTALL_GUARD_FAIL RUNNER_CHILD_FRAME\n"
    line = data[:-1]
    if line in INSTALL_GUARD_STATUS_LINES:
        return line + b"\n"
    if line in RUNNER_LOCAL_STATUS_LINES:
        return b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n"
    if ready:
        if (
            re.fullmatch(RUNNER_CHILD_STATUS_RE, line)
            and line not in INSTALL_GUARD_STATUS_LINES
        ):
            return b"INSTALL_GUARD_FAIL RUNNER_CHILD_STATUS_UNKNOWN\n"
        if re.fullmatch(RUNNER_CHILD_BWRAP_RE, line):
            return b"INSTALL_GUARD_FAIL RUNNER_CHILD_BWRAP\n"
        if re.fullmatch(RUNNER_CHILD_EXEC_RE, line):
            return b"INSTALL_GUARD_FAIL RUNNER_CHILD_EXEC\n"
        return b"INSTALL_GUARD_FAIL RUNNER_CHILD_OTHER\n"
    return b"INSTALL_GUARD_FAIL RUNNER_CHILD_CODE\n"


def _run_install_guard_outer(arguments: list[str]) -> int:
    if (
        len(arguments) < 5
        or arguments[0] != "--timeout"
        or arguments[2] != "--term-grace"
    ):
        sys.stderr.buffer.write(_runner_failure("ARG"))
        return 2
    try:
        timeout = float(arguments[1])
        grace = float(arguments[3])
    except (TypeError, ValueError):
        sys.stderr.buffer.write(_runner_failure("ARG"))
        return 2
    if not (0.0 < timeout <= 120.0 and 0.0 < grace <= 8.0) or arguments[4] != "--":
        sys.stderr.buffer.write(_runner_failure("ARG"))
        return 2
    command = arguments[5:]
    if not command:
        sys.stderr.buffer.write(_runner_failure("ARG"))
        return 2
    try:
        payload = _runner_read_stdin()
    except TimeoutError:
        sys.stderr.buffer.write(b"INSTALL_GUARD_FAIL SUPERVISOR\n")
        return 124
    except (OSError, ValueError):
        sys.stderr.buffer.write(_runner_failure("RUNNER_INPUT"))
        return 2
    process = None
    ready_read = None
    ready_write = None
    info_pidfd = None
    handles: dict[int, tuple[int, int | None, int]] = {}
    root_start = None
    result = 2
    diagnostic = b"INSTALL_GUARD_FAIL OUTER_HELPER\n"
    cleanup_ok = True
    trusted_status = False
    terminal_clean = False
    runner_wrapper = False
    phase = "CONTRACT"
    try:
        phase = "SUBREAPER"
        subreaper_ok = True
        try:
            _enable_subreaper()
        except (OSError, ValueError):
            subreaper_ok = False
        if not subreaper_ok or not _runner_has_no_children():
            diagnostic = _runner_failure("RUNNER_SUBREAPER")
        else:
            phase = "CONTRACT"
            ready_read, ready_write = os.pipe2(os.O_CLOEXEC)
            wrapped_command = _runner_bwrap_command(command, ready_write)
            runner_wrapper = bool(
                wrapped_command and wrapped_command[0] == RUNNER_BWRAP
            )
            phase = "SPAWN"
            process = subprocess.Popen(
                [*wrapped_command],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                pass_fds=(ready_write,),
            )
            os.close(ready_write)
            ready_write = None
            phase = "IDENTITY"
            identity = _runner_proc_info(process.pid)
            if identity is None or identity[0] == b"Z" or identity[1] != os.getpid():
                raise OSError(errno.ECHILD, "runner identity unavailable")
            root_start = identity[2]
            try:
                phase = "PIDFD"
                pidfd = os.pidfd_open(process.pid, 0)
            except OSError:
                handles[process.pid] = (root_start, None, os.getpid())
                diagnostic = _runner_failure("RUNNER_PIDFD")
            else:
                handles[process.pid] = (root_start, pidfd, os.getpid())
                rebound = _runner_proc_info(process.pid)
                if (
                    rebound is None
                    or rebound[0] == b"Z"
                    or rebound[1] != os.getpid()
                    or rebound[2] != root_start
                ):
                    raise OSError(errno.EAGAIN, "runner identity changed")
                phase = "CHILD"
                runner_deadline = time.monotonic() + timeout
                info_required = "--info-fd" in wrapped_command
                if info_required:
                    info_value = _runner_read_info(ready_read, runner_deadline)
                    launch_ready = info_value is not None
                    if launch_ready:
                        bound_info = _runner_bind_info(
                            info_value, process.pid, root_start
                        )
                        if bound_info is None:
                            launch_ready = False
                        else:
                            _child_pid, _child_start, info_pidfd = bound_info
                else:
                    launch_ready = True
                if not launch_ready:
                    status, data, timed_out, overflow = 2, b"", False, False
                    diagnostic = b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH\n"
                    result = 2
                else:
                    status, data, timed_out, overflow = _runner_pump(
                        process, payload, runner_deadline
                    )
                if not timed_out and not overflow:
                    try:
                        process.wait(
                            timeout=max(0.0, runner_deadline - time.monotonic())
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                os.close(ready_read)
                ready_read = None
                _runner_reap_children(handles)
                phase = "CLEANUP"
                if not _runner_collect(handles, process.pid):
                    raise OSError(errno.EOVERFLOW, "runner process tree limit")
                had_live, live_uncertain = _runner_live_handles(handles)
                terminal_clean = (
                    runner_wrapper
                    and not timed_out
                    and not overflow
                    and process.poll() is not None
                    and _runner_pidfd_exited(handles[process.pid][1])
                    and not had_live
                    and not live_uncertain
                    and _runner_has_no_children()
                    and _runner_waitpid_empty()
                )
                if not terminal_clean and (
                    timed_out or overflow or had_live or live_uncertain
                ):
                    if timed_out:
                        diagnostic = b"INSTALL_GUARD_FAIL SUPERVISOR\n"
                        result = 124
                    elif overflow:
                        diagnostic = b"INSTALL_GUARD_FAIL RUNNER_CHILD_FRAME\n"
                        result = 2
                    elif had_live or live_uncertain:
                        diagnostic = b"INSTALL_GUARD_FAIL SUPERVISOR\n"
                        result = 124
                    else:
                        diagnostic = b"INSTALL_GUARD_FAIL OUTER_HELPER\n"
                        result = 2
                    trusted_status = timed_out or had_live or live_uncertain
                elif status == 0:
                    diagnostic = b"INSTALL_GUARD_FAIL OUTER_STATUS\n" if data else b""
                    result = 2 if data else 0
                elif launch_ready:
                    diagnostic = _classify_install_guard_bytes(data, ready=True)
                    result = status if 0 < status <= 255 else 125
                if not launch_ready:
                    diagnostic = b"INSTALL_GUARD_FAIL RUNNER_CHILD_LAUNCH\n"
                    result = 2
                    trusted_status = False
    except (OSError, ValueError, subprocess.SubprocessError):
        diagnostic = _runner_failure("RUNNER_" + phase)
        result = 2
    finally:
        if info_pidfd is not None:
            try:
                os.close(info_pidfd)
            except OSError:
                pass
        for descriptor in (ready_read, ready_write):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if process is not None:
            if terminal_clean:
                cleanup_ok = True
            else:
                cleanup_ok = _runner_cleanup(
                    handles, process.pid, grace, process, root_start
                )
        for _pid, (_start, pidfd, _parent) in list(handles.items()):
            try:
                os.close(pidfd)
            except (OSError, TypeError):
                pass
        if process is not None and process.stderr is not None:
            process.stderr.close()
    if process is not None and not cleanup_ok and not trusted_status:
        diagnostic = _runner_failure("RUNNER_CLEANUP")
        result = 2
    if diagnostic:
        sys.stderr.buffer.write(diagnostic)
    return result


def _parse_seconds(value: str, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise SystemExit(2) from None
    if not math.isfinite(parsed) or parsed <= 0.0 or parsed > maximum:
        raise SystemExit(2)
    return parsed


def _parse_fd(value: str) -> int:
    try:
        descriptor = int(value, 10)
    except (TypeError, ValueError):
        raise SystemExit(2) from None
    if descriptor < 3:
        raise SystemExit(2)
    try:
        entry = os.fstat(descriptor)
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    except OSError:
        raise SystemExit(2) from None
    if not (stat.S_ISFIFO(entry.st_mode) or stat.S_ISSOCK(entry.st_mode)):
        raise SystemExit(2)
    if flags & os.O_ACCMODE == os.O_RDONLY:
        raise SystemExit(2)
    os.set_inheritable(descriptor, False)
    return descriptor


def _parse_log_path(value: str) -> str:
    if not value.startswith("/") or "\x00" in value:
        raise SystemExit(2)
    return value


def _parse_arguments() -> tuple[float, float, int, str, str, str, list[str]]:
    args = sys.argv[1:]
    if (
        len(args) < 14
        or args[0] != "--timeout"
        or args[2] != "--term-grace"
        or args[4] != "--completion-fd"
        or args[6] != "--completion-nonce"
        or args[8] != "--stdout-log"
        or args[10] != "--stderr-log"
        or args[12] != "--"
    ):
        raise SystemExit(2)
    timeout = _parse_seconds(args[1], 3600.0)
    grace = _parse_seconds(args[3], 30.0)
    completion_fd = _parse_fd(args[5])
    nonce = args[7]
    stdout_log = _parse_log_path(args[9])
    stderr_log = _parse_log_path(args[11])
    command = args[13:]
    if (
        len(nonce) != 64
        or any(character not in "0123456789abcdef" for character in nonce)
        or not command
        or any("\x00" in item for item in command)
    ):
        raise SystemExit(2)
    return timeout, grace, completion_fd, nonce, stdout_log, stderr_log, command


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def _arm_parent_death_signal() -> None:
    parent = os.getppid()
    libc = _libc()
    if libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG")
    if parent <= 1 or os.getppid() != parent:
        raise OSError(errno.ESRCH, "supervisor parent changed")


def _enable_subreaper() -> None:
    libc = _libc()
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")
    current = ctypes.c_int(0)
    if libc.prctl(PR_GET_CHILD_SUBREAPER, ctypes.byref(current), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_GET_CHILD_SUBREAPER")
    if current.value != 1:
        raise OSError(errno.EPERM, "child subreaper verification")


def _identity(pid: int) -> tuple[bytes, int, int] | None:
    try:
        with open(f"/proc/{pid}/stat", "rb", buffering=0) as handle:
            fields = handle.read(65536).rsplit(b") ", 1)[1].split()
        return fields[0], int(fields[1]), int(fields[19])
    except (
        FileNotFoundError,
        ProcessLookupError,
        PermissionError,
        IndexError,
        ValueError,
        OSError,
    ):
        return None


def _open_private_log(path: str) -> int:
    parent = os.path.dirname(path)
    leaf = os.path.basename(path)
    if not leaf or leaf in {".", ".."}:
        raise OSError(errno.EINVAL, "invalid log path")
    parent_fd = os.open(
        parent,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        parent_entry = os.fstat(parent_fd)
        if parent_entry.st_uid != os.geteuid() or parent_entry.st_mode & 0o077:
            raise OSError(errno.EPERM, "log parent is not private")
        return os.open(
            leaf,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent_fd,
        )
    finally:
        os.close(parent_fd)


class Supervisor:
    def __init__(
        self,
        timeout: float,
        grace: float,
        completion_fd: int,
        nonce: str,
        stdout_log: str,
        stderr_log: str,
        command: list[str],
    ) -> None:
        self.timeout = timeout
        self.grace = grace
        self.completion_fd = completion_fd
        self.nonce = nonce
        self.stdout_log = stdout_log
        self.stderr_log = stderr_log
        self.command = command
        self.handles: dict[int, tuple[int, int]] = {}
        self.root_pid: int | None = None
        self.root_status: int | None = None
        self.pending_signal: int | None = None
        self.completion_written = False

    def _latch_signal(self, signum: int, _frame: object) -> None:
        if self.pending_signal is None:
            self.pending_signal = signum

    def _acquire(self, pid: int, expected_start: int | None = None) -> bool:
        current = self.handles.get(pid)
        info = _identity(pid)
        if info is None or info[0] == b"Z":
            return False
        start = info[2]
        if expected_start is not None and start != expected_start:
            return False
        if current is not None:
            return current[0] == start
        try:
            pidfd = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            return False
        rebound = _identity(pid)
        if rebound is None or rebound[0] == b"Z" or rebound[2] != start:
            os.close(pidfd)
            return False
        self.handles[pid] = (start, pidfd)
        return True

    def _close_handle(self, pid: int) -> None:
        handle = self.handles.pop(pid, None)
        if handle is not None:
            os.close(handle[1])

    def _scan(self) -> None:
        try:
            entries = os.listdir("/proc")
        except OSError:
            return
        table: dict[int, tuple[bytes, int, int]] = {}
        for entry in entries:
            if not entry.isdigit():
                continue
            pid = int(entry)
            info = _identity(pid)
            if info is not None:
                table[pid] = info

        owned = {
            pid
            for pid, (start, _pidfd) in self.handles.items()
            if pid in table and table[pid][2] == start and table[pid][0] != b"Z"
        }
        owned.add(os.getpid())
        changed = True
        while changed:
            changed = False
            for pid, (state, ppid, start) in table.items():
                if state == b"Z" or pid in owned or ppid not in owned:
                    continue
                if self._acquire(pid, start):
                    owned.add(pid)
                    changed = True

        for pid, (start, _pidfd) in list(self.handles.items()):
            info = table.get(pid)
            if info is None or info[2] != start:
                self._close_handle(pid)

    def _reap(self) -> None:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            except InterruptedError:
                continue
            if pid == 0:
                return
            if pid == self.root_pid and self.root_status is None:
                self.root_status = os.waitstatus_to_exitcode(status)
            self._close_handle(pid)

    def _send(self, signum: int) -> None:
        root = self.root_pid
        ordered = [pid for pid in self.handles if pid != root]
        if root is not None and root in self.handles:
            ordered.append(root)
        for pid in ordered:
            start, pidfd = self.handles.get(pid, (0, -1))
            info = _identity(pid)
            if info is None or info[0] == b"Z" or info[2] != start:
                continue
            try:
                signal.pidfd_send_signal(pidfd, signum, None, 0)
            except ProcessLookupError:
                continue

    def _children_empty(self) -> bool:
        self._reap()
        self._scan()
        for pid, (start, _pidfd) in self.handles.items():
            info = _identity(pid)
            if info is not None and info[0] != b"Z" and info[2] == start:
                return False
        try:
            with open(
                f"/proc/{os.getpid()}/task/{os.getpid()}/children", encoding="ascii"
            ) as handle:
                children = handle.read().split()
        except OSError:
            return False
        return not children

    def _drain(self) -> bool:
        term_deadline = time.monotonic() + self.grace
        while True:
            self._reap()
            self._scan()
            self._send(signal.SIGTERM)
            if self._children_empty():
                return True
            if time.monotonic() >= term_deadline:
                break
            time.sleep(POLL_SECONDS)

        kill_deadline = time.monotonic() + self.grace
        while True:
            self._reap()
            self._scan()
            self._send(signal.SIGKILL)
            if self._children_empty():
                return True
            if time.monotonic() >= kill_deadline:
                return False
            time.sleep(POLL_SECONDS)

    def _write_completion(self, status: int) -> None:
        if self.completion_written:
            raise OSError(errno.EALREADY, "completion already written")
        if not self._children_empty():
            raise OSError(errno.EBUSY, "supervised process tree is not empty")
        frame = f"SOC_GUI_COMPLETE {self.nonce} {status}\n".encode("ascii")
        if len(frame) > MAX_FRAME_BYTES:
            raise OSError(errno.EOVERFLOW, "completion frame is too large")
        offset = 0
        while offset < len(frame):
            try:
                written = os.write(self.completion_fd, frame[offset:])
            except InterruptedError:
                continue
            if written <= 0:
                raise OSError(errno.EPIPE, "completion pipe closed")
            offset += written
        self.completion_written = True

    def _finish(self, status: int) -> int:
        if self.pending_signal is not None:
            status = 128 + self.pending_signal
        self._write_completion(status)
        return status

    def _spawn(self) -> bool:
        stdout_fd = _open_private_log(self.stdout_log)
        try:
            stderr_fd = _open_private_log(self.stderr_log)
        except BaseException:
            os.close(stdout_fd)
            raise
        try:
            process = subprocess.Popen(
                [*self.command],
                stdin=subprocess.DEVNULL,
                stdout=stdout_fd,
                stderr=stderr_fd,
                close_fds=True,
                restore_signals=True,
            )
        finally:
            os.close(stdout_fd)
            os.close(stderr_fd)
        self.root_pid = process.pid
        root_identity = _identity(process.pid)
        if root_identity is None or not self._acquire(process.pid, root_identity[2]):
            return False
        return True

    def run(self) -> int:
        _arm_parent_death_signal()
        _enable_subreaper()
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, self._latch_signal)

        deadline = time.monotonic() + self.timeout
        try:
            spawned = self._spawn()
        except BaseException:
            return self._finish(EXIT_INTERNAL)
        if not spawned:
            if not self._drain():
                return EXIT_INTERNAL
            if self.root_status is None:
                return self._finish(EXIT_INTERNAL)
            if self.root_status < 0:
                return self._finish(128 - self.root_status)
            return self._finish(
                self.root_status if self.root_status <= 255 else EXIT_INTERNAL
            )

        timed_out = False
        while self.root_status is None and self.pending_signal is None:
            self._reap()
            self._scan()
            if self.root_status is not None:
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(POLL_SECONDS)

        self._reap()
        self._scan()
        had_leftovers = not self._children_empty()
        if (
            timed_out or self.pending_signal is not None or had_leftovers
        ) and not self._drain():
            return EXIT_INTERNAL
        if self.pending_signal is not None:
            return self._finish(128 + self.pending_signal)
        if timed_out:
            return self._finish(124)
        if self.root_status is None:
            return EXIT_INTERNAL
        if had_leftovers and self.root_status == 0:
            return self._finish(EXIT_INTERNAL)
        if self.root_status < 0:
            return self._finish(128 - self.root_status)
        return self._finish(
            self.root_status if self.root_status <= 255 else EXIT_INTERNAL
        )

    def close(self) -> None:
        for pid in list(self.handles):
            self._close_handle(pid)
        try:
            os.close(self.completion_fd)
        except OSError:
            pass


def main() -> int:
    if sys.argv[1:2] == ["--sanitize-stdin"]:
        return _write_sanitized_stdin()
    if sys.argv[1:2] == ["--run-install-guard-outer"]:
        return _run_install_guard_outer(sys.argv[2:])
    if sys.argv[1:2] == ["--print-sanitized-tail"] and len(sys.argv) == 3:
        try:
            header = b"run-isolated-soc-gui: controller.stderr tail:\n"
            os.write(2, header)
            os.write(
                2,
                _sanitize_bytes(
                    _read_sanitized_tail(sys.argv[2]), MAX_TAIL_BYTES - len(header)
                ),
            )
        except (OSError, UnicodeError):
            return 2
        return 0
    if sys.argv[1:2] == ["--print-install-guard-status"] and len(sys.argv) == 3:
        sys.stderr.buffer.write(_install_guard_status_output(sys.argv[2]))
        sys.stderr.buffer.flush()
        return 0
    if sys.argv[1:2] == ["--print-install-guard-result"] and len(sys.argv) == 3:
        sys.stdout.buffer.write(_install_guard_result_output(sys.argv[2]))
        sys.stdout.buffer.flush()
        return 0
    timeout, grace, completion_fd, nonce, stdout_log, stderr_log, command = (
        _parse_arguments()
    )
    supervisor = Supervisor(
        timeout,
        grace,
        completion_fd,
        nonce,
        stdout_log,
        stderr_log,
        command,
    )
    try:
        return supervisor.run()
    except BaseException:
        try:
            if supervisor._drain():
                return supervisor._finish(EXIT_INTERNAL)
        except BaseException:
            pass
        return EXIT_INTERNAL
    finally:
        supervisor.close()


if __name__ == "__main__":
    raise SystemExit(main())
