"""Isolated protocol-v1 worker for the remote model-list operation."""

from __future__ import annotations

import ctypes
import os
import resource
import signal


_HTTP_REDIRECTS = frozenset({301, 302, 303, 307, 308})
_POSTPROCESS_REDIRECTS = frozenset({307, 308})
_MAX_REDIRECTS = 5
_HTTP_READ_BYTES = 65_536
_NOFILE_LIMIT = 64
_ADDRESS_SPACE_LIMIT = 384 * 1024 * 1024
_WORKER_EXIT_CODE = 65
_DEFAULT_OPERATION = "list-ollama-models"
_BOOTSTRAP_PROTOCOL_OPERATIONS = frozenset(
    {
        "list-ollama-models",
        "list-openai-compatible-models",
        "postprocess-ollama",
        "postprocess-openai-compatible",
    }
)
_BOOTSTRAP_WORKER_ERROR_CODES = frozenset(
    {
        "remote-request-invalid",
        "remote-url-unsafe",
        "remote-dns-failed",
        "remote-connect-failed",
        "remote-http-failed",
        "remote-response-too-large",
        "remote-response-invalid",
        "remote-operation-failed",
    }
)
_RUNTIME_LOADED = False
_RUNTIME_COMPAT_NAMES = frozenset(
    {
        "_CONTROL_CREDENTIALS",
        "_PinnedHTTPConnection",
        "_PinnedHTTPSConnection",
        "_REQUEST_FRAME_DEADLINE_NS",
        "MAX_MODEL_LIST_ENTRIES",
        "MAX_OLLAMA_MODEL_CHARS",
        "MAX_OPENAI_COMPATIBLE_MODEL_CHARS",
        "MAX_POSTPROCESS_JSON_BYTES",
        "MAX_POSTPROCESS_TEXT_CHARS",
        "PostProcessError",
        "_assert_openai_compatible_text",
        "_assert_text_length",
        "_choice_text",
        "_contains_escaped_null",
        "_contains_http_header_control_chars",
        "_is_flex_service_tier_rejected",
        "_is_openai_api_endpoint",
        "_normalize_ollama_model",
        "_ollama_endpoint",
        "_openai_compatible_endpoint",
        "_openai_compatible_model_supports_text_polishing",
        "_read_response_text",
        "_reject_duplicate_json_keys",
        "_reject_non_finite_json_number",
        "_strip_transcript_prompt_label",
        "_validate_http_url",
        "_validate_same_origin_redirect",
        "build_ollama_prompt",
        "build_openai_compatible_messages",
        "http",
        "ipaddress",
        "is_loopback_hostname",
        "json",
        "math",
        "remote_http",
        "selectors",
        "socket",
        "ssl",
        "struct",
        "sys",
        "time",
        "urllib",
    }
)

_PR_SET_PDEATHSIG = 1
_PR_SET_DUMPABLE = 4
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_AUDIT_ARCH_X86_64 = 0xC000003E
_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_ALU_AND_K = 0x54
_BPF_RET_K = 0x06
_SECCOMP_DATA_NR_OFFSET = 0
_SECCOMP_DATA_ARCH_OFFSET = 4
_X32_SYSCALL_BIT = 0x40000000
_SYS_CLONE = 56
_SYS_FORK = 57
_SYS_VFORK = 58
_SYS_CLONE3 = 435

_NO_FORK_FILTER_INSTRUCTIONS = (
    (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_ARCH_OFFSET),
    (_BPF_JMP_JEQ_K, 0, 10, _AUDIT_ARCH_X86_64),
    (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_NR_OFFSET),
    (_BPF_ALU_AND_K, 0, 0, _X32_SYSCALL_BIT),
    (_BPF_JMP_JEQ_K, 1, 0, 0),
    (_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
    (_BPF_LD_W_ABS, 0, 0, _SECCOMP_DATA_NR_OFFSET),
    (_BPF_JMP_JEQ_K, 4, 0, _SYS_CLONE),
    (_BPF_JMP_JEQ_K, 3, 0, _SYS_FORK),
    (_BPF_JMP_JEQ_K, 2, 0, _SYS_VFORK),
    (_BPF_JMP_JEQ_K, 1, 0, _SYS_CLONE3),
    (_BPF_RET_K, 0, 0, _SECCOMP_RET_ALLOW),
    (_BPF_RET_K, 0, 0, _SECCOMP_RET_KILL_PROCESS),
)


class _SockFilter(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    )


class _SockFprog(ctypes.Structure):
    _fields_ = (
        ("length", ctypes.c_ushort),
        ("filter", ctypes.POINTER(_SockFilter)),
    )


class _WorkerFailure(Exception):
    __slots__ = ("code", "reason", "status")

    def __init__(
        self,
        code: str,
        reason: str | None = None,
        status: int | None = None,
    ) -> None:
        if code not in _BOOTSTRAP_WORKER_ERROR_CODES:
            code = "remote-operation-failed"
        if reason is not None or status is not None:
            try:
                if not _RUNTIME_LOADED or "remote_http" not in globals():
                    raise ValueError
                reason, status = remote_http.validate_failure_metadata(
                    reason,
                    status,
                    error_code=code,
                )
            except ValueError:
                code = "remote-operation-failed"
                reason = "worker_protocol" if _RUNTIME_LOADED else None
                status = None
        self.code = code
        self.reason = reason
        self.status = status
        super().__init__(code)


class _WorkerDeadline(Exception):
    pass


def _check_io_deadline(deadline_ns: int | None) -> None:
    _ensure_runtime()
    if deadline_ns is not None and time.monotonic_ns() >= deadline_ns:
        raise _WorkerDeadline


def _set_resource_limits() -> None:
    limits = (
        (resource.RLIMIT_NOFILE, _NOFILE_LIMIT),
        (resource.RLIMIT_AS, _ADDRESS_SPACE_LIMIT),
        (resource.RLIMIT_CORE, 0),
    )
    try:
        for limit, target in limits:
            _soft, hard = resource.getrlimit(limit)
            if hard != resource.RLIM_INFINITY and hard < target:
                raise OSError
            resource.setrlimit(limit, (target, target))
            current, _current_hard = resource.getrlimit(limit)
            if current != target:
                raise OSError
    except (AttributeError, OSError, ValueError):
        raise _WorkerFailure("remote-operation-failed")


def _prctl(option: int, argument: int) -> None:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(option, argument, 0, 0, 0)
    except (AttributeError, OSError, TypeError, ValueError):
        raise _WorkerFailure("remote-operation-failed")
    if result != 0:
        raise _WorkerFailure("remote-operation-failed")


def _install_no_fork_boundary() -> None:
    """Install no-fork boundary before reading request or doing HTTP work."""
    try:
        machine = os.uname().machine
    except (AttributeError, OSError):
        raise _WorkerFailure("remote-operation-failed")
    if machine not in {"x86_64", "amd64"}:
        raise _WorkerFailure("remote-operation-failed")
    filters = (_SockFilter * len(_NO_FORK_FILTER_INSTRUCTIONS))(
        *(_SockFilter(*instruction) for instruction in _NO_FORK_FILTER_INSTRUCTIONS)
    )
    program = _SockFprog(
        len(filters),
        ctypes.cast(filters, ctypes.POINTER(_SockFilter)),
    )
    _prctl(_PR_SET_NO_NEW_PRIVS, 1)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(
            _PR_SET_SECCOMP,
            _SECCOMP_MODE_FILTER,
            ctypes.byref(program),
            0,
            0,
        )
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        raise _WorkerFailure("remote-operation-failed")
    if result != 0:
        raise _WorkerFailure("remote-operation-failed")


def _parent_death(_signum: int, _frame: object) -> None:
    os._exit(_WORKER_EXIT_CODE)


def _bind_parent() -> int:
    opener = getattr(os, "pidfd_open", None)
    if not callable(opener):
        raise _WorkerFailure("remote-operation-failed")
    parent_pid = os.getppid()
    if parent_pid <= 1:
        raise _WorkerFailure("remote-operation-failed")
    try:
        parent_pidfd = opener(parent_pid, 0)
        os.set_inheritable(parent_pidfd, False)
        signal.signal(signal.SIGTERM, _parent_death)
        _prctl(_PR_SET_PDEATHSIG, signal.SIGTERM)
        _prctl(_PR_SET_DUMPABLE, 0)
    except (OSError, TypeError, ValueError):
        try:
            os.close(parent_pidfd)
        except (UnboundLocalError, OSError):
            pass
        raise _WorkerFailure("remote-operation-failed")
    if os.getppid() != parent_pid:
        os.close(parent_pidfd)
        raise _WorkerFailure("remote-operation-failed")
    return parent_pidfd


def _load_runtime() -> None:
    """Load project, HTTP, and codec code only after all security boundaries."""

    global _CONTROL_CREDENTIALS, _REQUEST_FRAME_DEADLINE_NS, _RUNTIME_LOADED
    global http, ipaddress, json, math, remote_http, selectors, socket
    global ssl, struct, sys, time, urllib
    global MAX_OLLAMA_MODEL_CHARS, MAX_MODEL_LIST_ENTRIES, MAX_OPENAI_COMPATIBLE_MODEL_CHARS, MAX_POSTPROCESS_JSON_BYTES, MAX_POSTPROCESS_TEXT_CHARS, PostProcessError, _assert_openai_compatible_text, _assert_text_length, _choice_text, _contains_escaped_null, _contains_http_header_control_chars, _is_flex_service_tier_rejected, _is_openai_api_endpoint, _normalize_ollama_model, _openai_compatible_endpoint, _openai_compatible_model_supports_text_polishing, _ollama_endpoint, _read_response_text, _reject_duplicate_json_keys, _reject_non_finite_json_number, _strip_transcript_prompt_label, _validate_http_url, _validate_same_origin_redirect, build_ollama_prompt, build_openai_compatible_messages, _PinnedHTTPConnection, _PinnedHTTPSConnection, is_loopback_hostname
    if _RUNTIME_LOADED and _RUNTIME_COMPAT_NAMES.issubset(globals()):
        return
    _RUNTIME_LOADED = False

    # pylint: disable=import-outside-toplevel
    import encodings.idna  # noqa: F401
    import http.client
    import ipaddress
    import json
    import math
    import selectors
    import socket
    import ssl
    import struct
    import sys
    import time
    import urllib.parse

    from . import http_safety as http_safety_support
    from . import postprocessor as support
    from . import remote_http as protocol

    if (
        protocol.WORKER_EXIT_CODE != _WORKER_EXIT_CODE
        or protocol.PROTOCOL_SCHEMA_VERSION != 1
        or protocol.LIST_OLLAMA_MODELS_OPERATION != _DEFAULT_OPERATION
        or protocol.PROTOCOL_OPERATIONS != _BOOTSTRAP_PROTOCOL_OPERATIONS
        or protocol.SUPPORTED_OPERATIONS != _BOOTSTRAP_PROTOCOL_OPERATIONS
        or protocol.WORKER_ERROR_CODES != _BOOTSTRAP_WORKER_ERROR_CODES
        or support.MAX_MODEL_LIST_ENTRIES != protocol.MAX_MODEL_LIST_ENTRIES
        or support.MAX_OLLAMA_MODEL_CHARS != protocol.MAX_POSTPROCESS_MODEL_CHARS
    ):
        raise _WorkerFailure("remote-operation-failed")

    remote_http = protocol
    (
        MAX_OLLAMA_MODEL_CHARS,
        MAX_MODEL_LIST_ENTRIES,
        MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        MAX_POSTPROCESS_JSON_BYTES,
        MAX_POSTPROCESS_TEXT_CHARS,
        PostProcessError,
        _assert_openai_compatible_text,
        _assert_text_length,
        _choice_text,
        _contains_escaped_null,
        _contains_http_header_control_chars,
        _is_flex_service_tier_rejected,
        _is_openai_api_endpoint,
        _normalize_ollama_model,
        _openai_compatible_endpoint,
        _openai_compatible_model_supports_text_polishing,
        _ollama_endpoint,
        _read_response_text,
        _reject_duplicate_json_keys,
        _reject_non_finite_json_number,
        _strip_transcript_prompt_label,
        _validate_http_url,
        _validate_same_origin_redirect,
        build_ollama_prompt,
        build_openai_compatible_messages,
        _PinnedHTTPConnection,
        _PinnedHTTPSConnection,
        is_loopback_hostname,
    ) = (
        support.MAX_OLLAMA_MODEL_CHARS,
        support.MAX_MODEL_LIST_ENTRIES,
        support.MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        support.MAX_POSTPROCESS_JSON_BYTES,
        support.MAX_POSTPROCESS_TEXT_CHARS,
        support.PostProcessError,
        support._assert_openai_compatible_text,
        support._assert_text_length,
        support._choice_text,
        support._contains_escaped_null,
        support._contains_http_header_control_chars,
        support._is_flex_service_tier_rejected,
        support._is_openai_api_endpoint,
        support._normalize_ollama_model,
        support._openai_compatible_endpoint,
        support._openai_compatible_model_supports_text_polishing,
        support._ollama_endpoint,
        support._read_response_text,
        support._reject_duplicate_json_keys,
        support._reject_non_finite_json_number,
        support._strip_transcript_prompt_label,
        support._validate_http_url,
        support._validate_same_origin_redirect,
        support.build_ollama_prompt,
        support.build_openai_compatible_messages,
        http_safety_support._PinnedHTTPConnection,
        http_safety_support._PinnedHTTPSConnection,
        http_safety_support.is_loopback_hostname,
    )
    _CONTROL_CREDENTIALS = struct.Struct("=3i")
    _REQUEST_FRAME_DEADLINE_NS = max(
        protocol.LISTING_DEADLINE_NS,
        protocol.POSTPROCESS_DEADLINE_NS,
    )
    _RUNTIME_LOADED = True


def _ensure_runtime() -> None:
    if not _RUNTIME_LOADED or not _RUNTIME_COMPAT_NAMES.issubset(globals()):
        _load_runtime()


def __getattr__(name: str) -> object:
    if name not in _RUNTIME_COMPAT_NAMES:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    _ensure_runtime()
    try:
        return globals()[name]
    except KeyError:
        raise _WorkerFailure("remote-operation-failed") from None


def _install_deadline(deadline_ns: int) -> None:
    _ensure_runtime()
    def alarm_handler(_signum: int, _frame: object) -> None:
        raise _WorkerDeadline

    remaining = deadline_ns - time.monotonic_ns()
    if remaining <= 0:
        raise _WorkerDeadline
    signal.signal(signal.SIGALRM, alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, remaining / 1_000_000_000)


def _clear_deadline() -> None:
    try:
        signal.setitimer(signal.ITIMER_REAL, 0)
    except (OSError, ValueError):
        pass


def _open_control_socket() -> tuple[socket.socket, tuple[int, int, int]]:
    _ensure_runtime()
    required = (
        getattr(socket, "SO_PASSCRED", None),
        getattr(socket, "SCM_CREDENTIALS", None),
        getattr(socket, "MSG_CTRUNC", None),
        getattr(socket, "CMSG_SPACE", None),
    )
    if not isinstance(required[0], int) or not isinstance(required[1], int):
        raise _WorkerFailure("remote-operation-failed")
    if not isinstance(required[2], int) or not callable(required[3]):
        raise _WorkerFailure("remote-operation-failed")
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if not callable(getuid) or not callable(getgid):
        raise _WorkerFailure("remote-operation-failed")
    parent_pid = os.getppid()
    if type(parent_pid) is not int or parent_pid <= 1:
        raise _WorkerFailure("remote-operation-failed")
    control: socket.socket | None = None
    try:
        control = socket.socket(fileno=0)
        control.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        control.setblocking(False)
    except (OSError, TypeError, ValueError):
        if control is not None:
            control.close()
        raise _WorkerFailure("remote-operation-failed")
    return control, (parent_pid, getuid(), getgid())


def _recv_control_chunk(
    control: socket.socket,
    max_bytes: int,
    expected_credentials: tuple[int, int, int],
    error_code: str,
    deadline_ns: int | None = None,
) -> bytes:
    _ensure_runtime()
    _check_io_deadline(deadline_ns)
    try:
        data, ancillary, flags, _address = control.recvmsg(
            max_bytes,
            socket.CMSG_SPACE(_CONTROL_CREDENTIALS.size),
        )
    except BlockingIOError:
        raise
    except (OSError, TypeError, ValueError):
        raise _WorkerFailure(error_code)
    if type(data) is not bytes or flags & socket.MSG_CTRUNC:
        raise _WorkerFailure(error_code)
    credentials: list[tuple[int, int, int]] = []
    for level, kind, payload in ancillary:
        if level != socket.SOL_SOCKET or kind != socket.SCM_CREDENTIALS:
            raise _WorkerFailure(error_code)
        if len(payload) != _CONTROL_CREDENTIALS.size:
            raise _WorkerFailure(error_code)
        try:
            credentials.append(_CONTROL_CREDENTIALS.unpack(payload))
        except struct.error:
            raise _WorkerFailure(error_code)
    if len(credentials) != 1 or credentials[0] != expected_credentials:
        raise _WorkerFailure(error_code)
    if not data:
        raise _WorkerFailure(error_code)
    return data


def _reject_queued_control_data(
    control: socket.socket,
    expected_credentials: tuple[int, int, int],
    error_code: str,
    deadline_ns: int | None = None,
) -> None:
    _ensure_runtime()
    try:
        _recv_control_chunk(
            control,
            remote_http._CONTROL_RELEASE_BYTES + 1,
            expected_credentials,
            error_code,
            deadline_ns,
        )
    except BlockingIOError:
        return
    raise _WorkerFailure(error_code)


def _read_request_frame(
    control: socket.socket,
    parent_pidfd: int,
    deadline_ns: int,
    expected_credentials: tuple[int, int, int],
) -> bytes:
    _ensure_runtime()
    selector = selectors.DefaultSelector()
    response = bytearray()
    frame_length: int | None = None
    try:
        selector.register(control, selectors.EVENT_READ, "control")
        selector.register(parent_pidfd, selectors.EVENT_READ, "parent")
        while True:
            now = time.monotonic_ns()
            if now >= deadline_ns:
                raise _WorkerDeadline
            events = selector.select((deadline_ns - now) / 1_000_000_000)
            if not events:
                raise _WorkerDeadline
            _check_io_deadline(deadline_ns)
            for key, _mask in events:
                if key.data == "parent":
                    os._exit(remote_http.WORKER_EXIT_CODE)
                try:
                    chunk = _recv_control_chunk(
                        control,
                        _HTTP_READ_BYTES,
                        expected_credentials,
                        "remote-request-invalid",
                        deadline_ns,
                    )
                except BlockingIOError:
                    continue
                if len(response) < remote_http.FRAME_PREFIX_BYTES:
                    prefix_needed = remote_http.FRAME_PREFIX_BYTES - len(response)
                    response.extend(chunk[:prefix_needed])
                    chunk = chunk[prefix_needed:]
                    if len(response) == remote_http.FRAME_PREFIX_BYTES:
                        declared = int.from_bytes(response[:4], "big", signed=False)
                        frame_length = remote_http.FRAME_PREFIX_BYTES + declared
                        if frame_length > remote_http.MAX_REQUEST_FRAME_BYTES:
                            raise _WorkerFailure("remote-request-invalid")
                if chunk:
                    if frame_length is None or len(response) + len(chunk) > frame_length:
                        raise _WorkerFailure("remote-request-invalid")
                    response.extend(chunk)
                if frame_length is not None and len(response) == frame_length:
                    _reject_queued_control_data(
                        control,
                        expected_credentials,
                        "remote-request-invalid",
                        deadline_ns,
                    )
                    return bytes(response)
    except (OSError, ValueError):
        raise _WorkerFailure("remote-request-invalid")
    finally:
        selector.close()


def _write_response_frame(parent_pidfd: int, frame: bytes, deadline_ns: int) -> None:
    _ensure_runtime()
    selector = selectors.DefaultSelector()
    output_fd = 1
    offset = 0
    try:
        os.set_blocking(output_fd, False)
        selector.register(output_fd, selectors.EVENT_WRITE, "stdout")
        selector.register(parent_pidfd, selectors.EVENT_READ, "parent")
        while offset < len(frame):
            now = time.monotonic_ns()
            if now >= deadline_ns:
                raise _WorkerDeadline
            events = selector.select((deadline_ns - now) / 1_000_000_000)
            if not events:
                raise _WorkerDeadline
            _check_io_deadline(deadline_ns)
            for key, _mask in events:
                if key.data == "parent":
                    os._exit(remote_http.WORKER_EXIT_CODE)
                try:
                    _check_io_deadline(deadline_ns)
                    written = os.write(output_fd, frame[offset:])
                except BlockingIOError:
                    continue
                except OSError:
                    raise _WorkerFailure("remote-operation-failed")
                if written <= 0:
                    raise _WorkerFailure("remote-operation-failed")
                offset += written
    finally:
        selector.close()
        try:
            os.close(output_fd)
        except OSError:
            pass


def _wait_for_release(
    control: socket.socket,
    parent_pidfd: int,
    expected_credentials: tuple[int, int, int],
    expected_nonce: str,
    response_frame: bytes,
    deadline_ns: int,
) -> None:
    _ensure_runtime()
    selector = selectors.DefaultSelector()
    release = bytearray()
    try:
        selector.register(control, selectors.EVENT_READ, "control")
        selector.register(parent_pidfd, selectors.EVENT_READ, "parent")
        while True:
            now = time.monotonic_ns()
            if now >= deadline_ns:
                raise _WorkerDeadline
            events = selector.select((deadline_ns - now) / 1_000_000_000)
            if not events:
                raise _WorkerDeadline
            _check_io_deadline(deadline_ns)
            for key, _mask in events:
                if key.data == "parent":
                    os._exit(remote_http.WORKER_EXIT_CODE)
                try:
                    chunk = _recv_control_chunk(
                        control,
                        remote_http._CONTROL_RELEASE_BYTES + 1,
                        expected_credentials,
                        "remote-operation-failed",
                        deadline_ns,
                    )
                except BlockingIOError:
                    continue
                if len(release) + len(chunk) > remote_http._CONTROL_RELEASE_BYTES:
                    raise _WorkerFailure("remote-operation-failed")
                release.extend(chunk)
                if len(release) == remote_http._CONTROL_RELEASE_BYTES:
                    if not remote_http._validate_control_release(
                        bytes(release),
                        expected_nonce,
                        response_frame,
                    ):
                        raise _WorkerFailure("remote-operation-failed")
                    _reject_queued_control_data(
                        control,
                        expected_credentials,
                        "remote-operation-failed",
                        deadline_ns,
                    )
                    return
    finally:
        selector.close()


def _finish_response(
    control: socket.socket,
    parent_pidfd: int,
    frame: bytes,
    expected_credentials: tuple[int, int, int],
    expected_nonce: str,
    deadline_ns: int,
) -> bool:
    _ensure_runtime()
    try:
        _write_response_frame(parent_pidfd, frame, deadline_ns)
        if parent_pidfd >= 0:
            try:
                os.close(2)
            except OSError:
                pass
        _wait_for_release(
            control,
            parent_pidfd,
            expected_credentials,
            expected_nonce,
            frame,
            deadline_ns,
        )
    except (_WorkerDeadline, _WorkerFailure, OSError, ValueError):
        return False
    return True


def _remaining_seconds(deadline_ns: int) -> float:
    _ensure_runtime()
    remaining = deadline_ns - time.monotonic_ns()
    if remaining <= 0:
        raise _WorkerDeadline
    return remaining / 1_000_000_000


def _network_failure_reason(error: BaseException) -> str:
    _ensure_runtime()
    import errno as errno_module

    current: BaseException | None = error
    seen: set[int] = set()
    for _depth in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, socket.gaierror):
            return "network_dns"
        if isinstance(current, (ssl.SSLError, ssl.CertificateError)):
            return "network_tls"
        if isinstance(current, TimeoutError):
            return "timeout"
        if isinstance(current, OSError) and current.errno == errno_module.ETIMEDOUT:
            return "timeout"
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return "network_connect"


def _network_failure(error: BaseException) -> _WorkerFailure:
    reason = _network_failure_reason(error)
    code = "remote-dns-failed" if reason == "network_dns" else "remote-connect-failed"
    return _WorkerFailure(code, reason)


def _exception_chain_has_timeout(error: BaseException) -> bool:
    _ensure_runtime()
    import errno as errno_module

    current: BaseException | None = error
    seen: set[int] = set()
    for _depth in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        if isinstance(current, TimeoutError):
            return True
        if isinstance(current, OSError) and current.errno == errno_module.ETIMEDOUT:
            return True
        next_error = current.__cause__
        if next_error is None and not current.__suppress_context__:
            next_error = current.__context__
        current = next_error
    return False


def _response_read_failure(
    error: PostProcessError,
    deadline_ns: int,
    *,
    status: int | None,
) -> _WorkerFailure:
    _ensure_runtime()
    if time.monotonic_ns() >= deadline_ns or _exception_chain_has_timeout(error):
        return _WorkerFailure("remote-connect-failed", "timeout")
    args = error.args
    message = args[0] if type(args) is tuple and len(args) == 1 else None
    too_large_prefix = "remote response is too large (max "
    too_large_suffix = " bytes)"
    too_large = (
        type(message) is str
        and len(message) <= 256
        and message.startswith(too_large_prefix)
        and message.endswith(too_large_suffix)
        and message[len(too_large_prefix) : -len(too_large_suffix)].isdigit()
    )
    code = "remote-response-too-large" if too_large else "remote-response-invalid"
    provider_status = (
        status if type(status) is int and 200 <= status <= 299 else None
    )
    return _WorkerFailure(code, "provider_malformed_payload", provider_status)


def _http_failure(
    status: int,
    *,
    unsupported_parameter: bool = False,
) -> _WorkerFailure:
    if type(status) is not int or not 100 <= status <= 599:
        return _WorkerFailure("remote-http-failed", "worker_protocol")
    try:
        reason = remote_http.failure_reason_for_http_status(
            status,
            unsupported_parameter=unsupported_parameter,
        )
    except ValueError:
        return _WorkerFailure("remote-operation-failed", "worker_protocol")
    return _WorkerFailure("remote-http-failed", reason, status)


def _resolve_addresses(url: str, deadline_ns: int) -> tuple[str, ...]:
    _ensure_runtime()
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise _WorkerFailure("remote-url-unsafe")
    if not hostname:
        raise _WorkerFailure("remote-url-unsafe")
    try:
        resolved = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as error:
        raise _network_failure(error) from None
    loopback_hostname = is_loopback_hostname(hostname)
    addresses: list[str] = []
    for result in resolved:
        sockaddr = result[4] if len(result) > 4 else ()
        address_text = sockaddr[0] if isinstance(sockaddr, tuple) and sockaddr else ""
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            continue
        if loopback_hostname:
            unsafe = not address.is_loopback
        else:
            unsafe = address.is_multicast or not address.is_global
        if unsafe:
            raise _WorkerFailure("remote-url-unsafe")
        normalized = str(address)
        if normalized not in addresses:
            addresses.append(normalized)
    if not addresses:
        raise _WorkerFailure("remote-dns-failed", "network_dns")
    _remaining_seconds(deadline_ns)
    return tuple(addresses)


def _http_target(url: str) -> str:
    _ensure_runtime()
    parsed = urllib.parse.urlparse(url)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return target


def _close_http_resources(
    response: object | None,
    connection: object | None,
) -> None:
    _ensure_runtime()
    primary = sys.exc_info()[1]
    primary_is_semantic = primary is not None and (
        isinstance(primary, (_WorkerDeadline, _WorkerFailure, KeyboardInterrupt, SystemExit))
        or not isinstance(primary, Exception)
    )
    close_failed = False
    close_abort: BaseException | None = None

    def record_close_failure(error: BaseException) -> None:
        nonlocal close_abort, close_failed
        if isinstance(error, (_WorkerDeadline, KeyboardInterrupt, SystemExit)) or not isinstance(
            error, Exception
        ):
            if close_abort is None:
                close_abort = error
        else:
            close_failed = True

    try:
        if response is not None:
            response.close()  # type: ignore[attr-defined]
    except BaseException as error:
        record_close_failure(error)
    finally:
        try:
            if connection is not None:
                connection.close()  # type: ignore[attr-defined]
        except BaseException as error:
            record_close_failure(error)
    if close_abort is not None and not primary_is_semantic:
        raise close_abort
    if primary is not None:
        return
    if close_failed:
        raise _WorkerFailure("remote-operation-failed") from None


def _open_connection(
    url: str,
    deadline_ns: int,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    _ensure_runtime()
    addresses = _resolve_addresses(url, deadline_ns)
    parsed = urllib.parse.urlparse(url)
    timeout = _remaining_seconds(deadline_ns)
    kwargs: dict[str, object] = {
        "port": parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80),
        "timeout": timeout,
        "pinned_addresses": addresses,
    }
    connection: http.client.HTTPConnection | None = None
    try:
        if parsed.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
            connection = _PinnedHTTPSConnection(parsed.hostname or "", **kwargs)
        elif parsed.scheme == "http":
            connection = _PinnedHTTPConnection(parsed.hostname or "", **kwargs)
        else:
            raise _WorkerFailure("remote-url-unsafe")
        connection.request(
            method,
            _http_target(url),
            body=body,
            headers=headers or {"Accept": "application/json", "Connection": "close"},
        )
        response = connection.getresponse()
    except BaseException as error:
        _close_http_resources(None, connection)
        if isinstance(error, (http.client.HTTPException, OSError, ValueError)):
            raise _network_failure(error) from None
        raise
    return connection, response


def _read_listing(url: str, deadline_ns: int) -> dict[str, object]:
    _ensure_runtime()
    try:
        current_url = _ollama_endpoint(url, "/api/tags")
    except (PostProcessError, ValueError):
        raise _WorkerFailure("remote-url-unsafe")
    for redirect_count in range(_MAX_REDIRECTS + 1):
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection, response = _open_connection(current_url, deadline_ns)
            if response.status in _HTTP_REDIRECTS:
                location = response.getheader("Location")
                if not isinstance(location, str) or not location:
                    raise _WorkerFailure("remote-http-failed")
                if redirect_count >= _MAX_REDIRECTS:
                    raise _WorkerFailure("remote-http-failed")
                try:
                    redirect_url = urllib.parse.urljoin(current_url, location)
                    _validate_http_url(
                        redirect_url,
                        field_name="ollama redirect",
                        allow_query_fragment=True,
                    )
                    _validate_same_origin_redirect(
                        current_url,
                        redirect_url,
                        field_name="ollama url",
                    )
                    redirect_parsed = urllib.parse.urlparse(redirect_url)
                    if redirect_parsed.scheme == "http" and not is_loopback_hostname(redirect_parsed.hostname):
                        raise PostProcessError("unsafe redirect")
                except (PostProcessError, ValueError):
                    raise _WorkerFailure("remote-url-unsafe")
                current_url = redirect_url
                continue
            if not 200 <= response.status < 300:
                try:
                    _read_response_text(
                        response,
                        MAX_POSTPROCESS_JSON_BYTES,
                        deadline=deadline_ns / 1_000_000_000,
                    )
                except PostProcessError as error:
                    raise _response_read_failure(
                        error,
                        deadline_ns,
                        status=response.status,
                    ) from None
            raw_text = _read_response_text(
                response,
                MAX_POSTPROCESS_JSON_BYTES,
                deadline=deadline_ns / 1_000_000_000,
            )
            return _decode_listing(raw_text)
        except _WorkerFailure:
            raise
        except _WorkerDeadline:
            raise
        except PostProcessError as error:
            raise _response_read_failure(
                error,
                deadline_ns,
                status=getattr(response, "status", None),
            ) from None
        except (http.client.HTTPException, OSError, ValueError) as error:
            raise _network_failure(error) from None
        finally:
            _close_http_resources(response, connection)
    raise _WorkerFailure("remote-http-failed")


def _decode_listing(raw_text: str) -> dict[str, object]:
    _ensure_runtime()
    try:
        data = json.loads(
            raw_text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=remote_http._parse_float,
        )
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if type(data) is not dict:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if "models" not in data:
        return {"listing_state": "missing-model-list", "models": []}
    raw_models = data["models"]
    if type(raw_models) is not list:
        return {"listing_state": "missing-model-list", "models": []}
    if len(raw_models) > MAX_MODEL_LIST_ENTRIES:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    models_by_name: dict[str, dict[str, object]] = {}
    for raw_model in raw_models:
        model = _normalize_ollama_model(raw_model)
        if model is None:
            continue
        name = model["name"]
        if type(name) is not str:
            continue
        models_by_name.setdefault(name, model)
    models = list(models_by_name.values())
    models.sort(key=lambda item: str(item["name"]).lower())
    return {"listing_state": "listed", "models": models}


def _validate_openai_json_tree(value: object, secret: str, depth: int = 0) -> None:
    _ensure_runtime()
    if depth > 64:
        raise ValueError
    if type(value) is str:
        remote_http._reject_surrogates(value)
        if secret and secret in value:
            raise ValueError
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or key in remote_http._FORBIDDEN_KEYS:
                raise ValueError
            remote_http._reject_surrogates(key)
            if secret and secret in key:
                raise ValueError
            _validate_openai_json_tree(item, secret, depth + 1)
        return
    if type(value) is list:
        for item in value:
            _validate_openai_json_tree(item, secret, depth + 1)
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError
        return
    if value is None or type(value) is bool or type(value) is int:
        return
    raise ValueError


def _decode_openai_http_error(raw_text: str, api_key: str) -> tuple[bool, bool]:
    _ensure_runtime()
    try:
        if type(raw_text) is not str:
            raise ValueError
        data = json.loads(
            raw_text,
            object_pairs_hook=remote_http._object_pairs,
            parse_constant=_reject_non_finite_json_number,
            parse_float=remote_http._parse_float,
        )
        _validate_openai_json_tree(data, api_key)
        remote_http._require_exact_keys(data, frozenset({"error"}))
        error = data["error"]
        if type(error) is not dict:
            raise ValueError
        return remote_http.classify_openai_error_fields(error)
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")


def _normalize_openai_model(model: object) -> dict[str, object] | None:
    _ensure_runtime()
    if type(model) is not dict:
        return None
    raw_name = model.get("id")
    if raw_name is None or raw_name == "":
        raw_name = model.get("name")
    if type(raw_name) is not str:
        return None
    name = raw_name.strip()
    try:
        remote_http._validate_text(
            name,
            max_chars=remote_http.MAX_MODEL_CHARS,
            max_bytes=remote_http.MAX_MODEL_BYTES,
            require_nonempty=True,
            require_trimmed=True,
        )
    except (UnicodeError, ValueError):
        return None
    if not _openai_compatible_model_supports_text_polishing(name):
        return None
    return {"name": name, "model": name}


def _decode_openai_listing(raw_text: str, api_key: str) -> dict[str, object]:
    _ensure_runtime()
    try:
        data = json.loads(
            raw_text,
            object_pairs_hook=remote_http._object_pairs,
            parse_constant=_reject_non_finite_json_number,
            parse_float=remote_http._parse_float,
        )
        _validate_openai_json_tree(data, api_key)
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if type(data) is not dict:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    raw_models = data.get("data")
    if type(raw_models) is not list:
        return {"listing_state": "missing-model-list", "models": []}
    if len(raw_models) > MAX_MODEL_LIST_ENTRIES:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    models_by_name: dict[str, dict[str, object]] = {}
    for raw_model in raw_models:
        model = _normalize_openai_model(raw_model)
        if model is None:
            continue
        name = model["name"]
        if type(name) is str:
            models_by_name.setdefault(name, model)
    models = list(models_by_name.values())
    models.sort(key=lambda item: str(item["name"]).lower())
    return {"listing_state": "listed", "models": models}


def _openai_headers(api_key: str) -> dict[str, str]:
    _ensure_runtime()
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _read_openai_listing(url: str, api_key: str, deadline_ns: int) -> dict[str, object]:
    _ensure_runtime()
    try:
        current_url = _openai_compatible_endpoint(url, "/models")
    except (PostProcessError, ValueError):
        raise _WorkerFailure("remote-url-unsafe")
    headers = _openai_headers(api_key)
    for redirect_count in range(_MAX_REDIRECTS + 1):
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection, response = _open_connection(
                current_url,
                deadline_ns,
                headers=headers,
            )
            if response.status in _HTTP_REDIRECTS:
                location = response.getheader("Location")
                if not isinstance(location, str) or not location:
                    raise _WorkerFailure("remote-http-failed")
                if redirect_count >= _MAX_REDIRECTS:
                    raise _WorkerFailure("remote-http-failed")
                try:
                    redirect_url = urllib.parse.urljoin(current_url, location)
                    _validate_http_url(
                        redirect_url,
                        field_name="openai-compatible redirect",
                        allow_query_fragment=True,
                    )
                    _validate_same_origin_redirect(
                        current_url,
                        redirect_url,
                        field_name="openai-compatible url",
                    )
                    redirect_parsed = urllib.parse.urlparse(redirect_url)
                    if redirect_parsed.scheme == "http" and not is_loopback_hostname(redirect_parsed.hostname):
                        raise PostProcessError("unsafe redirect")
                except (PostProcessError, ValueError):
                    raise _WorkerFailure("remote-url-unsafe")
                current_url = redirect_url
                continue
            if not 200 <= response.status < 300:
                try:
                    _read_response_text(
                        response,
                        MAX_POSTPROCESS_JSON_BYTES,
                        deadline=deadline_ns / 1_000_000_000,
                    )
                except PostProcessError as error:
                    raise _response_read_failure(
                        error,
                        deadline_ns,
                        status=response.status,
                    ) from None
            raw_text = _read_response_text(
                response,
                MAX_POSTPROCESS_JSON_BYTES,
                deadline=deadline_ns / 1_000_000_000,
            )
            return _decode_openai_listing(raw_text, api_key)
        except _WorkerFailure:
            raise
        except _WorkerDeadline:
            raise
        except PostProcessError as error:
            raise _response_read_failure(
                error,
                deadline_ns,
                status=getattr(response, "status", None),
            ) from None
        except (http.client.HTTPException, OSError, ValueError) as error:
            raise _network_failure(error) from None
        finally:
            _close_http_resources(response, connection)
    raise _WorkerFailure("remote-http-failed")


def _postprocess_request_body(
    model: str,
    text: str,
    language: str,
    personal_context: str,
    vocabulary: str,
    instruction: str,
) -> bytes:
    _ensure_runtime()
    try:
        if type(model) is not str:
            raise ValueError
        if _contains_escaped_null(model) or _contains_http_header_control_chars(model):
            raise ValueError
        model_name = _assert_text_length(
            model,
            field_name="ollama model",
            max_chars=MAX_OLLAMA_MODEL_CHARS,
        ).strip()
        if not model_name:
            raise ValueError
        prompt = build_ollama_prompt(
            text,
            language,
            personal_context,
            vocabulary,
            instruction,
        )
        body = json.dumps(
            {"model": model_name, "prompt": prompt, "stream": False},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (MemoryError, RecursionError, UnicodeError, PostProcessError, ValueError):
        raise _WorkerFailure("remote-request-invalid")
    if len(body) > remote_http.MAX_REQUEST_FRAME_BYTES:
        raise _WorkerFailure("remote-request-invalid")
    return body


def _openai_postprocess_payload(
    endpoint: str,
    model: str,
    text: str,
    language: str,
    personal_context: str,
    vocabulary: str,
    instruction: str,
    flex_processing: bool,
    service_tier_fallback: bool,
) -> dict[str, object]:
    _ensure_runtime()
    try:
        if type(model) is not str:
            raise ValueError
        model_name = _assert_openai_compatible_text(
            model,
            field_name="openai-compatible model",
            max_chars=MAX_OPENAI_COMPATIBLE_MODEL_CHARS,
        ).strip()
        if not model_name or not _openai_compatible_model_supports_text_polishing(model_name):
            raise ValueError
        if type(flex_processing) is not bool or type(service_tier_fallback) is not bool:
            raise ValueError
        payload: dict[str, object] = {
            "messages": build_openai_compatible_messages(
                text,
                language,
                personal_context,
                vocabulary,
                instruction,
            ),
            "model": model_name,
            "stream": False,
        }
        if flex_processing and _is_openai_api_endpoint(endpoint):
            payload["service_tier"] = "flex"
        return payload
    except (MemoryError, RecursionError, UnicodeError, PostProcessError, ValueError):
        raise _WorkerFailure("remote-request-invalid")


def _encode_openai_postprocess_body(payload: dict[str, object]) -> bytes:
    _ensure_runtime()
    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-request-invalid")
    if len(body) > remote_http.MAX_REQUEST_FRAME_BYTES:
        raise _WorkerFailure("remote-request-invalid")
    return body


def _openai_postprocess_request_body(
    endpoint: str,
    model: str,
    text: str,
    language: str,
    personal_context: str,
    vocabulary: str,
    instruction: str,
    flex_processing: bool,
    service_tier_fallback: bool,
) -> bytes:
    _ensure_runtime()
    return _encode_openai_postprocess_body(
        _openai_postprocess_payload(
            endpoint,
            model,
            text,
            language,
            personal_context,
            vocabulary,
            instruction,
            flex_processing,
            service_tier_fallback,
        )
    )


def _decode_openai_postprocess_response(
    raw_text: str,
    source_text: str,
    api_key: str,
) -> dict[str, object]:
    _ensure_runtime()
    try:
        data = json.loads(
            raw_text,
            object_pairs_hook=remote_http._object_pairs,
            parse_constant=_reject_non_finite_json_number,
            parse_float=remote_http._parse_float,
        )
        _validate_openai_json_tree(data, api_key)
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if type(data) is not dict:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if data.get("error"):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    choices = data.get("choices")
    if type(choices) is not list or not choices:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    choice = choices[0]
    if type(choice) is not dict:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    processed = _strip_transcript_prompt_label(_choice_text(choice), source_text)
    try:
        processed = _assert_text_length(
            processed,
            field_name="post-process output",
            max_chars=MAX_POSTPROCESS_TEXT_CHARS,
        )
    except (PostProcessError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if not processed:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    finish_reason = choice.get("finish_reason")
    if finish_reason is not None and finish_reason != "stop":
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    return {"text": processed}


def _read_openai_postprocess(
    url: str,
    model: str,
    text: str,
    language: str,
    personal_context: str,
    vocabulary: str,
    instruction: str,
    api_key: str,
    flex_processing: bool,
    service_tier_fallback: bool,
    deadline_ns: int,
) -> dict[str, object]:
    _ensure_runtime()
    try:
        current_url = _openai_compatible_endpoint(url, "/chat/completions")
    except (PostProcessError, ValueError):
        raise _WorkerFailure("remote-url-unsafe")
    try:
        if type(api_key) is not str:
            raise ValueError
        remote_http._validate_text(
            api_key,
            max_chars=remote_http.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
            max_bytes=remote_http.MAX_OPENAI_COMPATIBLE_API_KEY_BYTES,
        )
        api_key = api_key.strip()
        remote_http._validate_text(
            api_key,
            max_chars=remote_http.MAX_OPENAI_COMPATIBLE_API_KEY_CHARS,
            max_bytes=remote_http.MAX_OPENAI_COMPATIBLE_API_KEY_BYTES,
        )
    except (UnicodeError, ValueError):
        raise _WorkerFailure("remote-request-invalid")
    request_payload = _openai_postprocess_payload(
        current_url,
        model,
        text,
        language,
        personal_context,
        vocabulary,
        instruction,
        flex_processing,
        service_tier_fallback,
    )
    request_body = _encode_openai_postprocess_body(request_payload)
    headers = _openai_headers(api_key)
    fallback_attempted = False
    redirect_count = 0
    while redirect_count <= _MAX_REDIRECTS:
        redirected = False
        while True:
            connection: http.client.HTTPConnection | None = None
            response: http.client.HTTPResponse | None = None
            try:
                connection, response = _open_connection(
                    current_url,
                    deadline_ns,
                    headers=headers,
                    method="POST",
                    body=request_body,
                )
                if response.status in _HTTP_REDIRECTS:
                    if response.status not in _POSTPROCESS_REDIRECTS:
                        raise _WorkerFailure("remote-url-unsafe")
                    location = response.getheader("Location")
                    if not isinstance(location, str) or not location:
                        raise _WorkerFailure("remote-http-failed")
                    if redirect_count >= _MAX_REDIRECTS:
                        raise _WorkerFailure("remote-http-failed")
                    try:
                        redirect_url = urllib.parse.urljoin(current_url, location)
                        _validate_http_url(
                            redirect_url,
                            field_name="openai-compatible post-process redirect",
                            allow_query_fragment=True,
                        )
                        _validate_same_origin_redirect(
                            current_url,
                            redirect_url,
                            field_name="openai-compatible post-process url",
                        )
                        redirect_parsed = urllib.parse.urlparse(redirect_url)
                        if redirect_parsed.scheme == "http" and not is_loopback_hostname(
                            redirect_parsed.hostname
                        ):
                            raise PostProcessError("unsafe redirect")
                    except (PostProcessError, ValueError):
                        raise _WorkerFailure("remote-url-unsafe")
                    current_url = redirect_url
                    redirect_count += 1
                    redirected = True
                    break
                if not 200 <= response.status < 300:
                    raw_error = _read_response_text(
                        response,
                        MAX_POSTPROCESS_JSON_BYTES,
                        deadline=deadline_ns / 1_000_000_000,
                    )
                    try:
                        unsupported_parameter, flex_rejected = _decode_openai_http_error(
                            raw_error,
                            api_key,
                        )
                    except _WorkerFailure:
                        raise _http_failure(response.status) from None
                    fallback_payload: dict[str, object] | None = None
                    if not fallback_attempted:
                        if (
                            service_tier_fallback
                            and "service_tier" in request_payload
                            and flex_rejected
                        ):
                            fallback_payload = dict(request_payload)
                            fallback_payload.pop("service_tier", None)
                    if fallback_payload is not None:
                        request_payload = fallback_payload
                        request_body = _encode_openai_postprocess_body(request_payload)
                        fallback_attempted = True
                        continue
                    raise _http_failure(
                        response.status,
                        unsupported_parameter=unsupported_parameter,
                    )
                raw_text = _read_response_text(
                    response,
                    MAX_POSTPROCESS_JSON_BYTES,
                    deadline=deadline_ns / 1_000_000_000,
                )
                return _decode_openai_postprocess_response(raw_text, text, api_key)
            except _WorkerFailure:
                raise
            except _WorkerDeadline:
                raise
            except PostProcessError as error:
                raise _response_read_failure(
                    error,
                    deadline_ns,
                    status=getattr(response, "status", None),
                ) from None
            except (http.client.HTTPException, OSError, ValueError) as error:
                raise _network_failure(error) from None
            finally:
                _close_http_resources(response, connection)
        if redirected:
            continue
    raise _WorkerFailure("remote-http-failed")


def _decode_ollama_postprocess_response(
    raw_text: str,
    source_text: str,
) -> dict[str, object]:
    _ensure_runtime()
    try:
        data = json.loads(
            raw_text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=remote_http._parse_float,
        )
        _validate_openai_json_tree(data, "")
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if type(data) is not dict:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if "error" in data:
        raise _WorkerFailure("remote-http-failed")
    if data.get("done") is not True:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    response_text = data.get("response")
    if type(response_text) is not str:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    processed = _strip_transcript_prompt_label(response_text, source_text)
    try:
        processed = _assert_text_length(
            processed,
            field_name="post-process output",
            max_chars=MAX_POSTPROCESS_TEXT_CHARS,
        )
    except (PostProcessError, UnicodeError, ValueError):
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    if not processed:
        raise _WorkerFailure("remote-response-invalid", "provider_malformed_payload")
    return {"text": processed}


def _read_ollama_postprocess(
    url: str,
    model: str,
    text: str,
    language: str,
    personal_context: str,
    vocabulary: str,
    instruction: str,
    deadline_ns: int,
) -> dict[str, object]:
    _ensure_runtime()
    try:
        current_url = _ollama_endpoint(url, "/api/generate")
    except (PostProcessError, ValueError):
        raise _WorkerFailure("remote-url-unsafe")
    request_body = _postprocess_request_body(
        model,
        text,
        language,
        personal_context,
        vocabulary,
        instruction,
    )
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connection": "close",
    }
    for redirect_count in range(_MAX_REDIRECTS + 1):
        connection: http.client.HTTPConnection | None = None
        response: http.client.HTTPResponse | None = None
        try:
            connection, response = _open_connection(
                current_url,
                deadline_ns,
                headers=headers,
                method="POST",
                body=request_body,
            )
            if response.status in _HTTP_REDIRECTS:
                if response.status not in _POSTPROCESS_REDIRECTS:
                    raise _WorkerFailure("remote-url-unsafe")
                location = response.getheader("Location")
                if not isinstance(location, str) or not location:
                    raise _WorkerFailure("remote-http-failed")
                if redirect_count >= _MAX_REDIRECTS:
                    raise _WorkerFailure("remote-http-failed")
                try:
                    redirect_url = urllib.parse.urljoin(current_url, location)
                    _validate_http_url(
                        redirect_url,
                        field_name="ollama post-process redirect",
                        allow_query_fragment=True,
                    )
                    _validate_same_origin_redirect(
                        current_url,
                        redirect_url,
                        field_name="ollama post-process url",
                    )
                    redirect_parsed = urllib.parse.urlparse(redirect_url)
                    if redirect_parsed.scheme == "http" and not is_loopback_hostname(
                        redirect_parsed.hostname
                    ):
                        raise PostProcessError("unsafe redirect")
                except (PostProcessError, ValueError):
                    raise _WorkerFailure("remote-url-unsafe")
                current_url = redirect_url
                continue
            if not 200 <= response.status < 300:
                try:
                    _read_response_text(
                        response,
                        MAX_POSTPROCESS_JSON_BYTES,
                        deadline=deadline_ns / 1_000_000_000,
                    )
                except PostProcessError as error:
                    raise _response_read_failure(
                        error,
                        deadline_ns,
                        status=response.status,
                    ) from None
            raw_text = _read_response_text(
                response,
                MAX_POSTPROCESS_JSON_BYTES,
                deadline=deadline_ns / 1_000_000_000,
            )
            return _decode_ollama_postprocess_response(raw_text, text)
        except _WorkerFailure:
            raise
        except _WorkerDeadline:
            raise
        except PostProcessError as error:
            raise _response_read_failure(
                error,
                deadline_ns,
                status=getattr(response, "status", None),
            ) from None
        except (http.client.HTTPException, OSError, ValueError) as error:
            raise _network_failure(error) from None
        finally:
            _close_http_resources(response, connection)
    raise _WorkerFailure("remote-http-failed")


def _worker_response(
    nonce: str,
    result: dict[str, object] | None,
    error_code: str | None,
    operation: str = _DEFAULT_OPERATION,
    failure_reason: str | None = None,
    provider_status: int | None = None,
) -> bytes:
    _ensure_runtime()
    if error_code is None:
        response: dict[str, object] = {
            "nonce": nonce,
            "result": result,
            "schema_version": remote_http.PROTOCOL_SCHEMA_VERSION,
            "status": "ok",
        }
    else:
        response = {
            "error_code": error_code,
            "nonce": nonce,
            "schema_version": remote_http.PROTOCOL_SCHEMA_VERSION,
            "status": "error",
        }
        if failure_reason is not None:
            response["failure_reason"] = failure_reason
        if provider_status is not None:
            response["provider_status"] = provider_status
    try:
        return remote_http.encode_response(response, operation=operation)
    except remote_http.RemoteProtocolError:
        raise _WorkerFailure("remote-operation-failed")


def _run_with_control(
    parent_pidfd: int,
    control: socket.socket,
    expected_credentials: tuple[int, int, int],
) -> int:
    _ensure_runtime()
    request_deadline = time.monotonic_ns() + _REQUEST_FRAME_DEADLINE_NS
    raw_frame = _read_request_frame(
        control,
        parent_pidfd,
        request_deadline,
        expected_credentials,
    )
    try:
        decoded = remote_http._decode_frame(
            raw_frame,
            max_frame_bytes=remote_http.MAX_REQUEST_FRAME_BYTES,
        )
        envelope = remote_http._validate_request_envelope(decoded, time.monotonic_ns)
    except (MemoryError, RecursionError, UnicodeError, ValueError):
        return remote_http.WORKER_EXIT_CODE
    nonce = envelope["nonce"]
    operation = envelope["operation"]
    if type(nonce) is not str or type(operation) is not str:
        return remote_http.WORKER_EXIT_CODE
    try:
        request = remote_http._validate_request_payload(envelope)
        payload = request["payload"]
        if type(payload) is not dict or type(payload.get("url")) is not str:
            raise ValueError
        api_key = payload.get("api_key", "")
        if operation in {
            remote_http.LIST_OPENAI_COMPATIBLE_MODELS_OPERATION,
            remote_http.POSTPROCESS_OPENAI_COMPATIBLE_OPERATION,
        }:
            if type(api_key) is not str:
                raise ValueError
        else:
            api_key = ""
        deadline = request["deadline_monotonic_ns"]
        if type(deadline) is not int:
            raise ValueError
    except Exception:
        frame = _worker_response(
            nonce,
            None,
            "remote-request-invalid",
            operation,
        )
        _finish_response(
            control,
            parent_pidfd,
            frame,
            expected_credentials,
            nonce,
            request_deadline,
        )
        return remote_http.WORKER_EXIT_CODE
    _install_deadline(deadline)
    try:
        try:
            if operation == remote_http.LIST_OPENAI_COMPATIBLE_MODELS_OPERATION:
                result = _read_openai_listing(payload["url"], api_key, deadline)
            elif operation == remote_http.POSTPROCESS_OLLAMA_OPERATION:
                result = _read_ollama_postprocess(
                    payload["url"],
                    payload["model"],
                    payload["text"],
                    payload["language"],
                    payload["personal_context"],
                    payload["vocabulary"],
                    payload["prompt"],
                    deadline,
                )
            elif operation == remote_http.POSTPROCESS_OPENAI_COMPATIBLE_OPERATION:
                result = _read_openai_postprocess(
                    payload["url"],
                    payload["model"],
                    payload["text"],
                    payload["language"],
                    payload["personal_context"],
                    payload["vocabulary"],
                    payload["prompt"],
                    payload["api_key"],
                    payload["flex_processing"],
                    payload["service_tier_fallback"],
                    deadline,
                )
            else:
                result = _read_listing(payload["url"], deadline)
            frame = _worker_response(nonce, result, None, operation)
            return (
                0
                if _finish_response(
                    control,
                    parent_pidfd,
                    frame,
                    expected_credentials,
                    nonce,
                    deadline,
                )
                else remote_http.WORKER_EXIT_CODE
            )
        except _WorkerDeadline:
            return remote_http.WORKER_EXIT_CODE
        except _WorkerFailure as failure:
            frame = _worker_response(
                nonce,
                None,
                failure.code,
                operation,
                failure.reason,
                failure.status,
            )
            _finish_response(
                control,
                parent_pidfd,
                frame,
                expected_credentials,
                nonce,
                deadline,
            )
            return remote_http.WORKER_EXIT_CODE
        except Exception:
            frame = _worker_response(
                nonce,
                None,
                "remote-operation-failed",
                operation,
                "worker_protocol",
            )
            _finish_response(
                control,
                parent_pidfd,
                frame,
                expected_credentials,
                nonce,
                deadline,
            )
            return remote_http.WORKER_EXIT_CODE
    finally:
        _clear_deadline()


def _run(parent_pidfd: int) -> int:
    _ensure_runtime()
    control, expected_credentials = _open_control_socket()
    try:
        return _run_with_control(parent_pidfd, control, expected_credentials)
    finally:
        try:
            control.close()
        except OSError:
            pass


def main() -> int:
    parent_pidfd = -1
    try:
        _prctl(_PR_SET_DUMPABLE, 0)
        _set_resource_limits()
        parent_pidfd = _bind_parent()
        _install_no_fork_boundary()
        _load_runtime()
        return _run(parent_pidfd)
    except Exception:
        return _WORKER_EXIT_CODE
    finally:
        if parent_pidfd >= 0:
            try:
                os.close(parent_pidfd)
            except OSError:
                pass


if __name__ == "__main__":
    os._exit(main())
