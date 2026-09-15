from __future__ import annotations

import ctypes
import errno
import fcntl
import math
import os
import re
import resource
import secrets
import select
import selectors
import shutil
import signal
import socket
import stat
import struct
import subprocess  # nosec B404
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Iterator, Sequence


IO_PRIORITY_CLASS = "2"
LOCAL_MODEL_CPU_NICE = 10
LOCAL_MODEL_IO_PRIORITY_LEVEL = "7"
SOC_CPU_WEIGHT = 200
SOC_IO_WEIGHT = 200
LOCAL_MODEL_CPU_WEIGHT = 10
LOCAL_MODEL_IO_WEIGHT = 10
SOC_PRIORITY_SCOPE_MARKER = "SPEED_OF_CINNAMON_PRIORITY_SCOPE"
IONICE_TIMEOUT_SECONDS = 1.0
IONICE_REAP_TIMEOUT_SECONDS = 1.0
MAX_IONICE_OUTPUT_BYTES = 256
IONICE_OUTPUT_READ_CHUNK_BYTES = 128
MAX_CGROUP_FILE_BYTES = 256
MAX_CGROUP_PROCS_BYTES = 1024 * 1024
MAX_FDINFO_BYTES = 4096
MAX_CGROUP_RESOURCE_VALUE = (1 << 64) - 1
MAX_CPU_LIST_BYTES = 64 * 1024
MAX_CPU_LIST_COUNT = 65_536
MAX_CPU_INDEX = 1_048_575
MAX_PROC_CGROUP_BYTES = 64 * 1024
MAX_MOUNTINFO_BYTES = 1024 * 1024
MAX_CGROUP2_MOUNT_MAPPINGS = 1024
MAX_SCOPE_EXEC_ARGUMENTS = 4096
MAX_SCOPE_EXEC_ARGUMENT_BYTES = 128 * 1024
MAX_SCOPE_EXEC_TOTAL_BYTES = 2 * 1024 * 1024
MAX_PRIORITY_SCOPE_IDENTITY_INTEGER = (1 << 64) - 1
MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS = 20
MAX_LINUX_PID = (1 << 31) - 1
MAX_LINUX_PID_DECIMAL_DIGITS = 10
# mountinfo exposes major/minor as unsigned decimal device components; a uint32
# ceiling covers Linux dev_t encodings while bounding conversion work.
MAX_CGROUP_DEVICE_COMPONENT = (1 << 32) - 1
MAX_CGROUP_DEVICE_COMPONENT_DIGITS = 10
MAX_CGROUP_PARENT_LEVELS = 256
# Linux PATH_MAX-sized cgroup path plus two separators and two uint64 fields.
MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS = 4096
MAX_PRIORITY_SCOPE_IDENTITY_CHARS = (
    MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS
    + 2
    + (2 * MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS)
)
_PROC_ROOT = Path("/proc")
_PROC_SELF_CGROUP = _PROC_ROOT / "self" / "cgroup"
_PROC_SELF_MOUNTINFO = Path("/proc/self/mountinfo")
_CPU_ONLINE = Path("/sys/devices/system/cpu/online")
_NS_GET_NSTYPE = 0xB703
_NS_GET_ID = 0x8008B70D
_PIDFD_GET_CGROUP_NAMESPACE = 0xFF01
_PIDFD_GET_MNT_NAMESPACE = 0xFF03
_PIDFD_GET_PID_NAMESPACE = 0xFF05
_PIDFD_GET_USER_NAMESPACE = 0xFF09
_CLONE_NEWNS = 0x00020000
_CLONE_NEWCGROUP = 0x02000000
_CLONE_NEWUSER = 0x10000000
_CLONE_NEWPID = 0x20000000
USER_NS_INIT_ID = 3
PID_NS_INIT_ID = 4
CGROUP_NS_INIT_ID = 5
MNT_NS_INIT_ID = 8
USER_NS_INIT_INO = 0xEFFFFFFD
PID_NS_INIT_INO = 0xEFFFFFFC
CGROUP_NS_INIT_INO = 0xEFFFFFFB
MNT_NS_INIT_INO = 0xEFFFFFF8
_SCOPE_EXEC_WRAPPER_TOKEN = "--speed-of-cinnamon-scope-exec"
_LOCAL_MODEL_DIRECT_EXEC_TOKEN = "--speed-of-cinnamon-local-model-direct-exec"
_INTERNAL_EXEC_WRAPPER_TOKENS = frozenset(
    {_SCOPE_EXEC_WRAPPER_TOKEN, _LOCAL_MODEL_DIRECT_EXEC_TOKEN}
)
_SCOPE_EXEC_LATCH_REQUIRED_TOKEN = "--entered-latch-required"
_SCOPE_EXEC_LATCH_ADDRESS_ENV = "SPEED_OF_CINNAMON_SCOPE_ENTERED_ADDRESS"
_SCOPE_EXEC_LATCH_NONCE_ENV = "SPEED_OF_CINNAMON_SCOPE_ENTERED_NONCE"
_SCOPE_EXEC_LATCH_MESSAGE_PREFIX = b"SOC_SCOPE_ENTERED_V1:"
_SCOPE_EXEC_LATCH_ADDRESS_RE = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
_SCOPE_EXEC_LATCH_NONCE_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SCOPE_EXEC_LATCH_MAX_BYTES = len(_SCOPE_EXEC_LATCH_MESSAGE_PREFIX) + 64
_LOCAL_MODEL_GATE_REQUIRED_TOKEN = "--local-model-ready-ack-required"
_LOCAL_MODEL_GATE_ADDRESS_ENV = "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_ADDRESS"
_LOCAL_MODEL_GATE_READY_NONCE_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_READY_NONCE"
)
_LOCAL_MODEL_GATE_ACK_NONCE_ENV = "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_ACK_NONCE"
_LOCAL_MODEL_GATE_CONTROLLER_PID_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_CONTROLLER_PID"
)
_LOCAL_MODEL_GATE_CONTROLLER_UID_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_CONTROLLER_UID"
)
_LOCAL_MODEL_GATE_CONTROLLER_GID_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_CONTROLLER_GID"
)
_LOCAL_MODEL_GATE_CONTROLLER_DEVICE_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_CONTROLLER_DEVICE"
)
_LOCAL_MODEL_GATE_CONTROLLER_INODE_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_CONTROLLER_INODE"
)
_LOCAL_MODEL_GATE_CONTROLLER_START_TIME_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_CONTROLLER_START_TIME"
)
_LOCAL_MODEL_GATE_DEADLINE_ENV = "SPEED_OF_CINNAMON_LOCAL_MODEL_GATE_DEADLINE"
_LOCAL_MODEL_GATE_ENV_KEYS = (
    _LOCAL_MODEL_GATE_ADDRESS_ENV,
    _LOCAL_MODEL_GATE_READY_NONCE_ENV,
    _LOCAL_MODEL_GATE_ACK_NONCE_ENV,
    _LOCAL_MODEL_GATE_CONTROLLER_PID_ENV,
    _LOCAL_MODEL_GATE_CONTROLLER_UID_ENV,
    _LOCAL_MODEL_GATE_CONTROLLER_GID_ENV,
    _LOCAL_MODEL_GATE_CONTROLLER_DEVICE_ENV,
    _LOCAL_MODEL_GATE_CONTROLLER_INODE_ENV,
    _LOCAL_MODEL_GATE_CONTROLLER_START_TIME_ENV,
    _LOCAL_MODEL_GATE_DEADLINE_ENV,
)
_LOCAL_MODEL_GATE_READY_PREFIX = b"SOC_LOCAL_MODEL_READY_V1:"
_LOCAL_MODEL_GATE_ACK_PREFIX = b"SOC_LOCAL_MODEL_ACK_V1:"
_LOCAL_MODEL_GATE_ADDRESS_RE = re.compile(r"^[0-9a-f]{32}$", re.ASCII)
_LOCAL_MODEL_GATE_NONCE_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_LOCAL_MODEL_GATE_READY_MAX_BYTES = len(_LOCAL_MODEL_GATE_READY_PREFIX) + 64
_LOCAL_MODEL_GATE_ACK_MAX_BYTES = len(_LOCAL_MODEL_GATE_ACK_PREFIX) + 64
_LOCAL_MODEL_GATE_ACK_TIMEOUT_SECONDS = 10.0
_LOCAL_MODEL_GATE_RECEIVE_BUDGET = 8
_LOCAL_MODEL_GATE_DIRECTORY_PREFIX = "g-"
_LOCAL_MODEL_GATE_CONTROLLER_PREFIX = "controller-"
_LOCAL_MODEL_GATE_WRAPPER_PREFIX = "wrapper-"
_LOCAL_MODEL_GATE_SOCKET_SUFFIX = ".sock"
_LOCAL_MODEL_GATE_MAX_SOCKET_PATH_BYTES = 107
_LOCAL_MODEL_GATE_MAX_RUNTIME_COMPONENTS = 64
_LOCAL_MODEL_GATE_PROC_STAT_BYTES = 4096
_LOCAL_MODEL_GATE_PIDFD_INFO_BYTES = 4096
_LOCAL_MODEL_GATE_FAILURE = "local model ready gate failed"
_LOCAL_MODEL_GATE_INVALID = "local model ready gate is invalid"
_LOCAL_MODEL_GATE_CAPABILITY = "local model ready gate capability is unavailable"
# Linux UAPI constants may predate Python's socket constants on supported kernels.
_SO_PASSPIDFD = 76
_SCM_PIDFD = 4
_SCOPE_EXEC_ELF_MAGIC = b"\x7fELF"
_SCOPE_EXEC_ENTER_TIMEOUT_SECONDS = 10.0
_SCOPE_EXEC_REAP_TIMEOUT_SECONDS = 2.0
_SCOPE_EXEC_POLL_SECONDS = 0.05
_SCOPE_EXEC_FD_EXEC_SUPPORTED = os.execve in getattr(os, "supports_fd", ())
_SCOPE_EXEC_FORWARDED_SIGNALS = (
    signal.SIGINT,
    signal.SIGTERM,
    signal.SIGHUP,
    signal.SIGQUIT,
)
_SCOPE_EXEC_FAILURE = "SOC priority scope wrapper failed"
_LOCAL_MODEL_DIRECT_EXEC_FAILURE = "local model direct priority wrapper failed"
_LOCAL_MODEL_DIRECT_STATUS_FD_ENV = "SPEED_OF_CINNAMON_LOCAL_MODEL_DIRECT_STATUS_FD"
_LOCAL_MODEL_DIRECT_DEADLINE_ENV = "SPEED_OF_CINNAMON_LOCAL_MODEL_DIRECT_DEADLINE"
_LOCAL_MODEL_DIRECT_CONTROLLER_PID_ENV = (
    "SPEED_OF_CINNAMON_LOCAL_MODEL_DIRECT_CONTROLLER_PID"
)
_LOCAL_MODEL_DIRECT_STATUS_MAGIC = b"SOCDIR1\0"
_LOCAL_MODEL_DIRECT_STATUS_VERSION = 1
_LOCAL_MODEL_DIRECT_STATUS_STRUCT = struct.Struct("!8sBBHIIQ")
_LOCAL_MODEL_DIRECT_OUTCOME_TARGET = 1
_LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE = 2
_LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL = 3
_LOCAL_MODEL_DIRECT_OUTCOME_FAILURE = 4
_LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE = 5
_LOCAL_MODEL_DIRECT_FLAG_SUBREAPER = 1 << 0
_LOCAL_MODEL_DIRECT_FLAG_ROOT_BOUND = 1 << 1
_LOCAL_MODEL_DIRECT_FLAG_SECURITY_ARMED = 1 << 2
_LOCAL_MODEL_DIRECT_FLAG_ROOT_REAPED = 1 << 3
_LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY = 1 << 4
_LOCAL_MODEL_DIRECT_FLAG_GROUP_SEPARATED = 1 << 5
_LOCAL_MODEL_DIRECT_FLAG_SUPERVISOR_HARDENED = 1 << 6
_LOCAL_MODEL_DIRECT_FLAG_CONTROLLER_BOUND = 1 << 7
_LOCAL_MODEL_DIRECT_ALL_FLAGS = (
    _LOCAL_MODEL_DIRECT_FLAG_SUBREAPER
    | _LOCAL_MODEL_DIRECT_FLAG_ROOT_BOUND
    | _LOCAL_MODEL_DIRECT_FLAG_SECURITY_ARMED
    | _LOCAL_MODEL_DIRECT_FLAG_ROOT_REAPED
    | _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
    | _LOCAL_MODEL_DIRECT_FLAG_GROUP_SEPARATED
    | _LOCAL_MODEL_DIRECT_FLAG_SUPERVISOR_HARDENED
    | _LOCAL_MODEL_DIRECT_FLAG_CONTROLLER_BOUND
)
_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT = 65
_LOCAL_MODEL_DIRECT_CHILDREN_MAX_BYTES = 1024 * 1024
_LOCAL_MODEL_DIRECT_MAX_CHILDREN = 4096
_LOCAL_MODEL_DIRECT_NPROC_LIMIT = _LOCAL_MODEL_DIRECT_MAX_CHILDREN
_LOCAL_MODEL_DIRECT_CLEANUP_GRACE_SECONDS = 0.25
_LOCAL_MODEL_DIRECT_CLEANUP_TIMEOUT_SECONDS = 2.0
_PR_SET_PDEATHSIG = 1
_PR_GET_PDEATHSIG = 2
_PR_GET_DUMPABLE = 3
_PR_SET_DUMPABLE = 4
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37
_PR_SET_NO_NEW_PRIVS = 38
_PR_GET_NO_NEW_PRIVS = 39
_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_SECCOMP_RET_KILL_PROCESS = 0x80000000
_SECCOMP_RET_ERRNO = 0x00050000
_SECCOMP_RET_ALLOW = 0x7FFF0000
_AUDIT_ARCH_X86_64 = 0xC000003E
_X86_64_KILL = 62
_X86_64_SETPGID = 109
_X86_64_RT_SIGQUEUEINFO = 129
_X86_64_PRCTL = 157
_X86_64_TKILL = 200
_X86_64_TGKILL = 234
_X86_64_IOPRIO_SET = 251
_X86_64_RT_TGSIGQUEUEINFO = 297
_X86_64_PRLIMIT64 = 302
_X86_64_PIDFD_SEND_SIGNAL = 424
_X86_64_PIDFD_OPEN = 434
_X32_RT_SIGQUEUEINFO = 524
_X32_RT_TGSIGQUEUEINFO = 536
_X32_SYSCALL_BIT = 0x40000000
_CAP_SYS_PTRACE = 19
_CAP_SYS_ADMIN = 21
_CAP_SYS_NICE = 23
_CAP_SYS_RESOURCE = 24
_BPF_LD_W_ABS = 0x20
_BPF_JMP_JEQ_K = 0x15
_BPF_RET_K = 0x06
_SCOPE_EXEC_ARGUMENTS_FAILURE = f"{_SCOPE_EXEC_FAILURE}: arguments"
_SCOPE_EXEC_AFFINITY_FAILURE = f"{_SCOPE_EXEC_FAILURE}: affinity"
_SCOPE_EXEC_AFFINITY_PHASE_FAILURES = {
    "target": f"{_SCOPE_EXEC_FAILURE}: affinity-target",
    "read": f"{_SCOPE_EXEC_FAILURE}: affinity-read",
    "set": f"{_SCOPE_EXEC_FAILURE}: affinity-set",
    "verify": f"{_SCOPE_EXEC_FAILURE}: affinity-verify",
}
_SCOPE_EXEC_EXEC_FAILURE = f"{_SCOPE_EXEC_FAILURE}: exec"
_SCOPE_EXEC_PHASE_FAILURES = frozenset(
    {
        _SCOPE_EXEC_ARGUMENTS_FAILURE,
        _SCOPE_EXEC_AFFINITY_FAILURE,
        *_SCOPE_EXEC_AFFINITY_PHASE_FAILURES.values(),
        _SCOPE_EXEC_EXEC_FAILURE,
    }
)
_TRUSTED_COMMAND_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_IO_PRIORITY_RE = re.compile(
    r"^\s*(none|real[- ]time|best-effort|idle):\s+prio\s+([0-7])\s*$",
    re.IGNORECASE | re.ASCII,
)
_SCOPE_UNIT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]*\.scope$", re.ASCII)
_CGROUP_EVENT_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$", re.ASCII)
_IO_CLASS_VALUES = {
    "none": "0",
    "real-time": "1",
    "best-effort": "2",
    "idle": "3",
}
_IONICE_ENV = {"LANG": "C", "LC_ALL": "C"}
_LOCAL_MODEL_DIRECT_FORBIDDEN_ENV_KEYS = frozenset(
    {
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "ENV",
        "PWD",
        "OLDPWD",
        "CDPATH",
        "PS4",
        "SHELLOPTS",
        "PROMPT_COMMAND",
        "IFS",
        SOC_PRIORITY_SCOPE_MARKER,
        _SCOPE_EXEC_LATCH_ADDRESS_ENV,
        _SCOPE_EXEC_LATCH_NONCE_ENV,
        _LOCAL_MODEL_DIRECT_STATUS_FD_ENV,
        _LOCAL_MODEL_DIRECT_DEADLINE_ENV,
        *_LOCAL_MODEL_GATE_ENV_KEYS,
    }
)
_LOCAL_MODEL_DIRECT_FORBIDDEN_ENV_PREFIXES = ("LD_", "PYTHON", "BASH_", "__")


class LocalModelPriorityError(RuntimeError):
    """The local-model launch boundary cannot enforce its priority contract."""


class PriorityScopeError(RuntimeError):
    """The SOC cgroup-v2 priority boundary cannot be enforced or verified."""


class _ScopeToolUnavailable(PriorityScopeError):
    pass


class _ScopeExecChannelUnavailable(PriorityScopeError):
    pass


class _ScopeExecAffinityError(PriorityScopeError):
    def __init__(self, phase: str) -> None:
        super().__init__("SOC CPU affinity normalization failed")
        self.phase = phase


@dataclass(frozen=True)
class PriorityScopeIdentity:
    path: str
    device: int
    inode: int


@dataclass(frozen=True, slots=True, repr=False)
class PriorityScopeMembership:
    process_ids: tuple[int, ...]
    populated: bool

    def __repr__(self) -> str:
        return "PriorityScopeMembership(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class _CanonicalProcessIdentity:
    process_id: int
    directory_device: int
    directory_inode: int
    start_time: int


@dataclass(frozen=True, slots=True, repr=False)
class _LocalModelGateConfiguration:
    controller_address: str
    ready_nonce: str
    ack_nonce: str
    controller_uid: int
    controller_gid: int
    controller_identity: _CanonicalProcessIdentity
    absolute_deadline: float


@dataclass(frozen=True, slots=True, repr=False)
class _LocalModelGateDatagram:
    message: bytes
    credentials: tuple[tuple[int, int, int], ...]
    pidfd_process_ids: tuple[int | None, ...]
    flags: int
    source: object
    unknown_ancillary: bool


@dataclass(frozen=True, slots=True, repr=False)
class _LocalModelGateDirectoryBinding:
    runtime_path: str
    gate_path: str
    gate_name: str
    runtime_identity: tuple[int, int, int, int]
    gate_identity: tuple[int, int, int, int]


@dataclass(frozen=True, slots=True, repr=False)
class _LocalModelGateEndpoint:
    path: str
    name: str
    identity: tuple[int, int, int, int, int] | None


class _LocalModelGateResourceOwner:
    __slots__ = (
        "_closed",
        "_cleaning",
        "binding",
        "controller_pidfd_owner",
        "created_domain_name",
        "directory_descriptors",
        "endpoint",
        "listener_owner",
        "peer_endpoint_name",
        "spawn_pidfd_owner",
    )

    def __init__(self) -> None:
        self.controller_pidfd_owner = [-1]
        self.spawn_pidfd_owner = [-1]
        self.directory_descriptors = [-1, -1]
        self.listener_owner: list[socket.socket | None] = [None]
        self.binding: _LocalModelGateDirectoryBinding | None = None
        self.created_domain_name: str | None = None
        self.endpoint: _LocalModelGateEndpoint | None = None
        self.peer_endpoint_name: str | None = None
        self._closed = False
        self._cleaning = False

    def close(self) -> BaseException | None:
        if self._closed:
            return None
        if self._cleaning:
            return OSError(errno.EALREADY, "gate cleanup is already active")
        self._cleaning = True
        try:
            return _close_local_model_gate_resource_owner(self)
        finally:
            self._closed = _local_model_gate_resource_owner_is_empty(self)
            self._cleaning = False

    def __del__(self) -> None:
        try:
            self.close()
        except BaseException:
            pass
        try:
            if not _local_model_gate_resource_owner_is_empty(self):
                _force_close_local_model_gate_resource_owner(self)
        except BaseException:
            pass


class _LocalModelReadyAckGate:
    __slots__ = (
        "_absolute_deadline",
        "_ack_nonce",
        "_address",
        "_controller_identity",
        "_controller_pidfd_owner",
        "_controller_uid",
        "_controller_gid",
        "_listener_owner",
        "_ready_nonce",
        "_release_may_have_occurred",
        "_resources",
        "_ready_source",
        "_scope_identity",
        "_spawn_identity",
        "_spawn_pidfd_owner",
        "_state",
        "_unit_name",
        "argv",
    )

    def __init__(
        self,
        *,
        argv: tuple[str, ...],
        unit_name: str,
        absolute_deadline: float,
        resources: _LocalModelGateResourceOwner,
        address: str,
        ready_nonce: str,
        ack_nonce: str,
        controller_uid: int,
        controller_gid: int,
        controller_identity: _CanonicalProcessIdentity,
    ) -> None:
        self.argv = argv
        self._unit_name = unit_name
        self._absolute_deadline = absolute_deadline
        self._resources = resources
        self._listener_owner = resources.listener_owner
        self._address = address
        self._ready_nonce = ready_nonce
        self._ack_nonce = ack_nonce
        self._controller_uid = controller_uid
        self._controller_gid = controller_gid
        self._controller_identity = controller_identity
        self._controller_pidfd_owner = resources.controller_pidfd_owner
        self._spawn_identity: _CanonicalProcessIdentity | None = None
        self._spawn_pidfd_owner = resources.spawn_pidfd_owner
        self._release_may_have_occurred = False
        self._ready_source: str | None = None
        self._scope_identity: PriorityScopeIdentity | None = None
        self._state = "prepared"

    def __repr__(self) -> str:
        return "_LocalModelReadyAckGate(<redacted>)"

    def __enter__(self) -> _LocalModelReadyAckGate:
        return self

    def __exit__(
        self,
        _exc_type: object,
        body_error: BaseException | None,
        _traceback: object,
    ) -> None:
        try:
            self.close()
        except BaseException:
            if body_error is None:
                raise
            try:
                body_error.add_note("local model ready gate cleanup failed")
            except (Exception, SystemExit):
                pass

    @property
    def state(self) -> str:
        return self._state

    @property
    def release_may_have_occurred(self) -> bool:
        return self._release_may_have_occurred

    @property
    def environment_overlay(self) -> dict[str, str]:
        identity = self._controller_identity
        return {
            _LOCAL_MODEL_GATE_ADDRESS_ENV: self._address,
            _LOCAL_MODEL_GATE_READY_NONCE_ENV: self._ready_nonce,
            _LOCAL_MODEL_GATE_ACK_NONCE_ENV: self._ack_nonce,
            _LOCAL_MODEL_GATE_CONTROLLER_PID_ENV: str(identity.process_id),
            _LOCAL_MODEL_GATE_CONTROLLER_UID_ENV: str(self._controller_uid),
            _LOCAL_MODEL_GATE_CONTROLLER_GID_ENV: str(self._controller_gid),
            _LOCAL_MODEL_GATE_CONTROLLER_DEVICE_ENV: str(
                identity.directory_device
            ),
            _LOCAL_MODEL_GATE_CONTROLLER_INODE_ENV: str(
                identity.directory_inode
            ),
            _LOCAL_MODEL_GATE_CONTROLLER_START_TIME_ENV: str(identity.start_time),
            _LOCAL_MODEL_GATE_DEADLINE_ENV: self._absolute_deadline.hex(),
        }

    @property
    def bound_pidfd_cloexec(self) -> bool:
        return self._spawn_pidfd >= 0 and _descriptor_is_cloexec(self._spawn_pidfd)

    @property
    def _controller_pidfd(self) -> int:
        return self._controller_pidfd_owner[0]

    @property
    def _spawn_pidfd(self) -> int:
        return self._spawn_pidfd_owner[0]

    def fileno(self) -> int:
        listener = self._listener_owner[0]
        return -1 if listener is None else listener.fileno()

    def _close_owned_resources(self) -> BaseException | None:
        return self._resources.close()

    def _fail(self) -> None:
        self._state = "invalid"
        cleanup_error = self._close_owned_resources()
        if isinstance(cleanup_error, KeyboardInterrupt):
            raise cleanup_error
        raise LocalModelPriorityError(_LOCAL_MODEL_GATE_FAILURE) from None

    def bind_spawn(self, process_id: int) -> None:
        if self._state != "prepared" or not _valid_process_id(process_id):
            self._fail()
        try:
            identity = _canonical_process_identity(process_id)
            if identity is None or not _open_identity_pidfd(
                identity,
                self._spawn_pidfd_owner,
            ):
                self._fail()
            self._spawn_identity = identity
            self._state = "spawn_bound"
        except LocalModelPriorityError:
            raise
        except KeyboardInterrupt as exc:
            self._state = "invalid"
            cleanup_error = self._close_owned_resources()
            if cleanup_error is not None:
                exc = _preferred_gate_exception(exc, cleanup_error)
            raise exc
        except (Exception, SystemExit):
            self._fail()

    def _ready_scope_is_stable(self) -> bool:
        spawn_identity = self._spawn_identity
        identity = self._scope_identity
        return (
            type(spawn_identity) is _CanonicalProcessIdentity
            and type(identity) is PriorityScopeIdentity
            and type(self._ready_source) is str
            and os.path.basename(identity.path) == self._unit_name
            and _local_model_gate_directory_is_stable(self._resources)
            and _valid_local_model_gate_source(
                self._ready_source,
                self._resources,
                prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
            )
            and priority_scope_membership(
                identity,
                cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
                io_weight=LOCAL_MODEL_IO_WEIGHT,
            )
            == PriorityScopeMembership(
                process_ids=(spawn_identity.process_id,),
                populated=True,
            )
            and verify_priority_scope_identity(
                identity,
                pid=spawn_identity.process_id,
                cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
                io_weight=LOCAL_MODEL_IO_WEIGHT,
            )
            and _pidfd_process_id(self._spawn_pidfd)
            == spawn_identity.process_id
            and _pidfd_process_id(self._controller_pidfd)
            == self._controller_identity.process_id
            and _canonical_process_identity(spawn_identity.process_id)
            == spawn_identity
            and _canonical_process_identity(
                self._controller_identity.process_id
            )
            == self._controller_identity
            and time.monotonic() < self._absolute_deadline
        )

    def _ready_scope_after_retirement_is_stable(self) -> bool:
        spawn_identity = self._spawn_identity
        identity = self._scope_identity
        listener = self._listener_owner[0]
        return (
            type(spawn_identity) is _CanonicalProcessIdentity
            and type(identity) is PriorityScopeIdentity
            and type(self._ready_source) is str
            and listener is not None
            and listener.getpeername() == self._ready_source
            and _local_model_gate_domain_is_retired(self._resources)
            and os.path.basename(identity.path) == self._unit_name
            and priority_scope_membership(
                identity,
                cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
                io_weight=LOCAL_MODEL_IO_WEIGHT,
            )
            == PriorityScopeMembership(
                process_ids=(spawn_identity.process_id,),
                populated=True,
            )
            and verify_priority_scope_identity(
                identity,
                pid=spawn_identity.process_id,
                cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
                io_weight=LOCAL_MODEL_IO_WEIGHT,
            )
            and _pidfd_process_id(self._spawn_pidfd)
            == spawn_identity.process_id
            and _pidfd_process_id(self._controller_pidfd)
            == self._controller_identity.process_id
            and _canonical_process_identity(spawn_identity.process_id)
            == spawn_identity
            and _canonical_process_identity(
                self._controller_identity.process_id
            )
            == self._controller_identity
            and time.monotonic() < self._absolute_deadline
        )

    def _drain_ready_datagrams(self, listener: socket.socket) -> bool:
        spawn_identity = self._spawn_identity
        if type(spawn_identity) is not _CanonicalProcessIdentity:
            self._fail()
        for _attempt in range(_LOCAL_MODEL_GATE_RECEIVE_BUDGET):
            try:
                duplicate = _receive_local_model_gate_datagram(
                    listener,
                    max_bytes=_LOCAL_MODEL_GATE_READY_MAX_BYTES,
                )
            except BlockingIOError as exc:
                if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    self._fail()
                try:
                    stable = self._ready_scope_is_stable()
                except (Exception, SystemExit):
                    self._fail()
                if not stable:
                    self._fail()
                if not _retire_local_model_gate_domain(
                    self._resources,
                    peer_source=self._ready_source,
                ):
                    self._fail()
                try:
                    stable = self._ready_scope_after_retirement_is_stable()
                except (Exception, SystemExit):
                    self._fail()
                if not stable:
                    self._fail()
                self._state = "ready_verified"
                return True
            except (Exception, SystemExit):
                self._fail()
            if _local_model_gate_datagram_is_attributable(
                duplicate,
                spawn_identity.process_id,
            ):
                self._fail()
        return False

    def try_verify_ready(self) -> bool:
        if self._state not in {"spawn_bound", "ready_draining"}:
            self._fail()
        if time.monotonic() >= self._absolute_deadline:
            self._fail()
        spawn_identity = self._spawn_identity
        if type(spawn_identity) is not _CanonicalProcessIdentity or self._spawn_pidfd < 0:
            self._fail()
        listener = self._listener_owner[0]
        if listener is None:
            self._fail()
        if self._state == "ready_draining":
            return self._drain_ready_datagrams(listener)
        expected_credentials = (
            spawn_identity.process_id,
            self._controller_uid,
            self._controller_gid,
        )
        for _attempt in range(_LOCAL_MODEL_GATE_RECEIVE_BUDGET):
            try:
                datagram = _receive_local_model_gate_datagram(
                    listener,
                    max_bytes=_LOCAL_MODEL_GATE_READY_MAX_BYTES,
                )
            except BlockingIOError as exc:
                if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                    return False
                self._fail()
            except (Exception, SystemExit):
                self._fail()
            try:
                if not _local_model_gate_datagram_is_attributable(
                    datagram,
                    spawn_identity.process_id,
                ):
                    continue
                valid = (
                    datagram.message
                    == _LOCAL_MODEL_GATE_READY_PREFIX
                    + self._ready_nonce.encode("ascii")
                    and datagram.flags == socket.MSG_CMSG_CLOEXEC
                    and datagram.credentials == (expected_credentials,)
                    and datagram.pidfd_process_ids == (spawn_identity.process_id,)
                    and not datagram.unknown_ancillary
                    and _valid_local_model_gate_source(
                        datagram.source,
                        self._resources,
                        prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
                    )
                    and _pidfd_process_id(self._spawn_pidfd)
                    == spawn_identity.process_id
                    and _pidfd_process_id(self._controller_pidfd)
                    == self._controller_identity.process_id
                    and _canonical_process_identity(spawn_identity.process_id)
                    == spawn_identity
                    and _canonical_process_identity(
                        self._controller_identity.process_id
                    )
                    == self._controller_identity
                )
                if not valid:
                    self._fail()
                listener.connect(datagram.source)
                if not _local_model_gate_directory_is_stable(self._resources):
                    self._fail()
                identity = priority_scope_identity_for_pid(
                    spawn_identity.process_id,
                    cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
                    io_weight=LOCAL_MODEL_IO_WEIGHT,
                )
                if type(identity) is not PriorityScopeIdentity:
                    self._fail()
                self._scope_identity = identity
                self._ready_source = datagram.source
                if not self._ready_scope_is_stable():
                    self._fail()
                self._state = "ready_draining"
                return self._drain_ready_datagrams(listener)
            except LocalModelPriorityError:
                raise
            except (Exception, SystemExit):
                self._fail()
        return False

    def release(self) -> bool:
        if self._state != "ready_verified":
            self._fail()
        try:
            valid = self._ready_scope_after_retirement_is_stable()
        except (Exception, SystemExit):
            self._fail()
        if not valid:
            self._fail()
        listener = self._listener_owner[0]
        if listener is None:
            self._fail()
        try:
            deadline_expired = time.monotonic() >= self._absolute_deadline
        except (Exception, SystemExit):
            self._fail()
        if deadline_expired:
            self._fail()
        self._release_may_have_occurred = True
        message = _LOCAL_MODEL_GATE_ACK_PREFIX + self._ack_nonce.encode("ascii")
        try:
            written = listener.send(message)
        except BlockingIOError as exc:
            if exc.errno in {errno.EAGAIN, errno.EWOULDBLOCK}:
                return False
            self._fail()
        except (Exception, SystemExit):
            self._fail()
        if written != len(message):
            self._fail()
        self._state = "released"
        return True

    def close(self) -> None:
        if self._state == "closed":
            return
        cleanup_error = self._close_owned_resources()
        owner_empty = _local_model_gate_resource_owner_is_empty(self._resources)
        if owner_empty:
            self._state = "closed"
        if isinstance(cleanup_error, KeyboardInterrupt):
            raise cleanup_error
        if cleanup_error is not None or not owner_empty:
            self._state = "invalid"
            raise LocalModelPriorityError(_LOCAL_MODEL_GATE_FAILURE) from None


@dataclass(frozen=True, slots=True, repr=False)
class _ScopeFileSnapshot:
    contents: str
    device: int
    inode: int
    mode: int
    owner: int
    group: int
    link_count: int


@dataclass(frozen=True)
class CgroupResourceSnapshot:
    """Identifier-free, read-only effective resource limits for this process."""

    effective_cpus: frozenset[int]
    online_cpus: frozenset[int] | None
    cpu_quota_us: int | None
    cpu_period_us: int
    memory_current_bytes: int
    memory_max_bytes: int | None
    memory_high_bytes: int | None
    memory_max_headroom_bytes: int | None
    memory_high_headroom_bytes: int | None


@dataclass(frozen=True)
class InitialNamespaceIdentity:
    user: tuple[int, int]
    pid: tuple[int, int]
    mount: tuple[int, int]
    cgroup: tuple[int, int]


@dataclass(frozen=True)
class _Cgroup2MountMapping:
    mount_root: Path
    mountpoint: Path
    device_major: int
    device_minor: int


@dataclass(frozen=True)
class _ScopeExecCpusetSnapshot:
    selected_directory_index: int
    directory_identities: tuple[tuple[int, int], ...]
    file_identity: tuple[int, int]
    contents: str
    cpus: frozenset[int]


@dataclass(frozen=True)
class _CgroupResourceFileSnapshot:
    identity: tuple[int, int]
    contents: str


@dataclass(frozen=True)
class _CgroupResourceLevelSnapshot:
    cpu_max: _CgroupResourceFileSnapshot
    memory_current: _CgroupResourceFileSnapshot
    memory_max: _CgroupResourceFileSnapshot
    memory_high: _CgroupResourceFileSnapshot


@dataclass(frozen=True)
class _CgroupResourceHierarchySnapshot:
    cpuset: _ScopeExecCpusetSnapshot
    levels: tuple[_CgroupResourceLevelSnapshot, ...]


@dataclass(frozen=True)
class _ScopeExecFileIdentity:
    path: str
    device: int
    inode: int
    size: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    executable: bool


@dataclass(frozen=True)
class _ScopeExecLaunchSpec:
    argv: tuple[str, ...]
    executable: _ScopeExecFileIdentity


@dataclass(frozen=True)
class _ScopeExecSupervisorSpec:
    command: tuple[str, ...]
    target: _ScopeExecLaunchSpec
    fixed_files: tuple[_ScopeExecFileIdentity, ...]


@dataclass(frozen=True)
class _ScopeExecAttemptOutcome:
    returncode: int | None
    latch_state: str
    timed_out: bool


def _scope_exec_fd_has_elf_magic(descriptor: int) -> bool:
    try:
        return os.pread(descriptor, len(_SCOPE_EXEC_ELF_MAGIC), 0) == _SCOPE_EXEC_ELF_MAGIC
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return False


def _current_cpu_priority() -> int | None:
    try:
        return os.getpriority(os.PRIO_PROCESS, 0)
    except (AttributeError, OSError, OverflowError, ValueError):
        return None


def _set_cpu_priority(value: int) -> bool:
    try:
        os.setpriority(os.PRIO_PROCESS, 0, value)
    except (AttributeError, OSError, OverflowError, ValueError):
        return False
    return True


def _ionice_command(arguments: list[str]) -> bool:
    ionice = shutil.which("ionice", path=_TRUSTED_COMMAND_PATH)
    if not ionice:
        return False
    try:
        result = subprocess.run(  # nosec B603
            [ionice, *arguments],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=IONICE_TIMEOUT_SECONDS,
            shell=False,
            env={"LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, OverflowError, ValueError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _set_io_priority(level: str, *, io_class: str = IO_PRIORITY_CLASS) -> bool:
    return _ionice_command(
        [
            "--class",
            io_class,
            "--classdata",
            level,
            "--pid",
            str(os.getpid()),
        ]
    )


def _required_priority_tool(name: str) -> str:
    try:
        command = shutil.which(name, path=_TRUSTED_COMMAND_PATH)
    except (OSError, RuntimeError, TypeError, ValueError):
        command = None
    if not command:
        raise LocalModelPriorityError(
            f"local model priority helper is unavailable: {name}"
        )
    return command


def _scope_exec_file_identity(
    path: str,
    *,
    executable: bool,
) -> _ScopeExecFileIdentity | None:
    if (
        not isinstance(path, str)
        or not path
        or "\x00" in path
        or not os.path.isabs(path)
        or os.path.normpath(path) != path
        or os.path.realpath(path) != path
    ):
        return None
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        path_stat = os.stat(path, follow_symlinks=False)
        after = os.fstat(descriptor)
        elf_magic_ok = not executable or _scope_exec_fd_has_elf_magic(descriptor)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
    identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mode,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if (
        identity
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or identity
        != (
            path_stat.st_dev,
            path_stat.st_ino,
            path_stat.st_size,
            path_stat.st_mode,
            path_stat.st_mtime_ns,
            path_stat.st_ctime_ns,
        )
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink <= 0
        or not elf_magic_ok
        or (
            executable
            and before.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0
        )
    ):
        return None
    return _ScopeExecFileIdentity(
        path=path,
        device=before.st_dev,
        inode=before.st_ino,
        size=before.st_size,
        mode=before.st_mode,
        mtime_ns=before.st_mtime_ns,
        ctime_ns=before.st_ctime_ns,
        executable=executable,
    )


def _required_scope_tool() -> str:
    try:
        command = shutil.which("systemd-run", path=_TRUSTED_COMMAND_PATH)
    except (OSError, RuntimeError, TypeError, ValueError):
        command = None
    if not command:
        raise _ScopeToolUnavailable(
            "SOC priority scope helper is unavailable: systemd-run"
        )
    try:
        resolved = os.fspath(Path(command).resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        raise PriorityScopeError("SOC priority scope helper is unsafe") from None
    if _scope_exec_file_identity(resolved, executable=True) is None:
        raise PriorityScopeError("SOC priority scope helper is unsafe")
    return resolved


def _scope_exec_wrapper_paths() -> tuple[str, str]:
    try:
        runtime = Path(str(sys.executable or "")).resolve(strict=True)
        entry = Path(__file__).resolve(strict=True)
        runtime_stat = runtime.stat()
        entry_stat = entry.stat()
    except (OSError, RuntimeError, ValueError):
        raise PriorityScopeError("SOC priority scope wrapper is unavailable") from None
    if (
        not runtime.is_absolute()
        or not entry.is_absolute()
        or not stat.S_ISREG(runtime_stat.st_mode)
        or not stat.S_ISREG(entry_stat.st_mode)
        or not os.access(runtime, os.X_OK)
    ):
        raise PriorityScopeError("SOC priority scope wrapper is unavailable")
    return os.fspath(runtime), os.fspath(entry)


def _scope_exec_launch_spec(argv: Sequence[str]) -> _ScopeExecLaunchSpec | None:
    command = _validated_scope_exec_target(argv)
    if command is None:
        return None
    try:
        executable = os.fspath(Path(command[0]).resolve(strict=True))
    except (OSError, RuntimeError, ValueError):
        return None
    command[0] = executable
    command = _validated_scope_exec_target(command)
    if command is None:
        return None
    identity = _scope_exec_file_identity(executable, executable=True)
    if identity is None:
        return None
    return _ScopeExecLaunchSpec(tuple(command), identity)


def _scope_exec_launch_spec_is_unchanged(spec: _ScopeExecLaunchSpec) -> bool:
    return _scope_exec_file_identity(
        spec.executable.path,
        executable=True,
    ) == spec.executable


def _open_scope_exec_launch_fd(spec: _ScopeExecLaunchSpec) -> int | None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    if nofollow is None or cloexec is None or spec.argv[0] != spec.executable.path:
        return None
    descriptor = -1
    try:
        descriptor = os.open(spec.executable.path, os.O_RDONLY | nofollow | cloexec)
        before = os.fstat(descriptor)
        path_stat = os.stat(spec.executable.path, follow_symlinks=False)
        after = os.fstat(descriptor)
        elf_magic_ok = _scope_exec_fd_has_elf_magic(descriptor)
    except (OSError, OverflowError, TypeError, ValueError):
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return None
    expected = (
        spec.executable.device,
        spec.executable.inode,
        spec.executable.size,
        spec.executable.mode,
        spec.executable.mtime_ns,
        spec.executable.ctime_ns,
    )
    if (
        expected
        != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        or expected
        != (
            path_stat.st_dev,
            path_stat.st_ino,
            path_stat.st_size,
            path_stat.st_mode,
            path_stat.st_mtime_ns,
            path_stat.st_ctime_ns,
        )
        or expected
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mode,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        or not stat.S_ISREG(after.st_mode)
        or after.st_nlink <= 0
        or not elf_magic_ok
        or after.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0
    ):
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    return descriptor


def _validated_scope_exec_target(argv: Sequence[str]) -> list[str] | None:
    if isinstance(argv, (str, bytes)):
        return None
    try:
        command = list(argv)
    except (TypeError, ValueError):
        return None
    if not command or len(command) > MAX_SCOPE_EXEC_ARGUMENTS:
        return None
    total_bytes = 0
    for item in command:
        if not isinstance(item, str) or not item or "\x00" in item:
            return None
        try:
            encoded = item.encode("utf-8")
        except UnicodeError:
            return None
        if len(encoded) > MAX_SCOPE_EXEC_ARGUMENT_BYTES:
            return None
        total_bytes += len(encoded) + 1
        if total_bytes > MAX_SCOPE_EXEC_TOTAL_BYTES:
            return None
    executable = Path(command[0])
    if not executable.is_absolute() or executable != Path(os.path.normpath(command[0])):
        return None
    try:
        _, entry = _scope_exec_wrapper_paths()
    except PriorityScopeError:
        return None
    if (
        len(command) >= 2
        and os.path.realpath(command[0]) == entry
        and command[1] in _INTERNAL_EXEC_WRAPPER_TOKENS
    ) or (
        len(command) >= 3
        and os.path.realpath(command[1]) == entry
        and command[2] in _INTERNAL_EXEC_WRAPPER_TOKENS
    ) or (
        len(command) >= 4
        and command[1] == "-I"
        and os.path.realpath(command[2]) == entry
        and command[3] in _INTERNAL_EXEC_WRAPPER_TOKENS
    ):
        return None
    return command


def _scope_exec_wrapper_command(
    command: Sequence[str],
    *,
    latch_required: bool = False,
    local_model_gate_required: bool = False,
) -> list[str]:
    if (
        not isinstance(latch_required, bool)
        or type(local_model_gate_required) is not bool
        or (latch_required and local_model_gate_required)
    ):
        raise PriorityScopeError("priority scope latch mode is invalid")
    spec = _scope_exec_launch_spec(command)
    if spec is None:
        raise PriorityScopeError("priority scope command is empty or invalid")
    runtime, entry = _scope_exec_wrapper_paths()
    identity = spec.executable
    wrapper = [
        runtime,
        "-I",
        entry,
        _SCOPE_EXEC_WRAPPER_TOKEN,
    ]
    if latch_required:
        wrapper.append(_SCOPE_EXEC_LATCH_REQUIRED_TOKEN)
    elif local_model_gate_required:
        wrapper.append(_LOCAL_MODEL_GATE_REQUIRED_TOKEN)
    return [
        *wrapper,
        str(identity.device),
        str(identity.inode),
        str(identity.size),
        str(identity.mode),
        str(identity.mtime_ns),
        str(identity.ctime_ns),
        "--",
        *spec.argv,
    ]


def _local_model_direct_exec_wrapper_command(
    command: Sequence[str],
) -> list[str]:
    wrapper = _scope_exec_wrapper_command(command)
    if len(wrapper) < 4 or wrapper[3] != _SCOPE_EXEC_WRAPPER_TOKEN:
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
    wrapper[3] = _LOCAL_MODEL_DIRECT_EXEC_TOKEN
    return wrapper


def _is_local_model_direct_supervisor_command(argv: Sequence[str]) -> bool:
    if isinstance(argv, (str, bytes)):
        return False
    try:
        command = list(argv)
    except (TypeError, ValueError):
        return False
    try:
        runtime, entry = _scope_exec_wrapper_paths()
    except PriorityScopeError:
        return False
    structural_matches = 0
    for token_index in range(3, len(command)):
        if (
            command[token_index] == _LOCAL_MODEL_DIRECT_EXEC_TOKEN
            and command[token_index - 2] == "-I"
            and os.path.realpath(command[token_index - 3]) == runtime
            and os.path.realpath(command[token_index - 1]) == entry
        ):
            structural_matches += 1
    return structural_matches == 1


def _local_model_direct_supervisor_environment(
    status_descriptor: int,
    absolute_deadline: float,
) -> dict[str, str]:
    if (
        type(status_descriptor) is not int
        or status_descriptor < 3
        or type(absolute_deadline) is not float
        or not math.isfinite(absolute_deadline)
    ):
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
    return {
        _LOCAL_MODEL_DIRECT_STATUS_FD_ENV: str(status_descriptor),
        _LOCAL_MODEL_DIRECT_DEADLINE_ENV: format(absolute_deadline, ".17g"),
        _LOCAL_MODEL_DIRECT_CONTROLLER_PID_ENV: str(os.getpid()),
    }


def _local_model_direct_supervisor_status_frame_size() -> int:
    return _LOCAL_MODEL_DIRECT_STATUS_STRUCT.size


def _parse_local_model_direct_supervisor_status(
    payload: bytes,
    wrapper_returncode: int,
) -> tuple[str, int | None] | None:
    if (
        not isinstance(payload, bytes)
        or len(payload) != _LOCAL_MODEL_DIRECT_STATUS_STRUCT.size
        or type(wrapper_returncode) is not int
    ):
        return None
    try:
        magic, version, outcome, flags, root_pid, wait_status, root_start = (
            _LOCAL_MODEL_DIRECT_STATUS_STRUCT.unpack(payload)
        )
    except struct.error:
        return None
    if (
        magic != _LOCAL_MODEL_DIRECT_STATUS_MAGIC
        or version != _LOCAL_MODEL_DIRECT_STATUS_VERSION
        or flags & ~_LOCAL_MODEL_DIRECT_ALL_FLAGS
    ):
        return None
    root_present = root_pid > 0 or root_start > 0
    if root_present != (root_pid > 0 and root_start > 0):
        return None
    cleanup_flags = (
        _LOCAL_MODEL_DIRECT_FLAG_SUBREAPER
        | _LOCAL_MODEL_DIRECT_FLAG_ROOT_BOUND
        | _LOCAL_MODEL_DIRECT_FLAG_ROOT_REAPED
        | _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
        | _LOCAL_MODEL_DIRECT_FLAG_SUPERVISOR_HARDENED
        | _LOCAL_MODEL_DIRECT_FLAG_CONTROLLER_BOUND
    )
    supervisor_flags = (
        _LOCAL_MODEL_DIRECT_FLAG_SUPERVISOR_HARDENED
        | _LOCAL_MODEL_DIRECT_FLAG_CONTROLLER_BOUND
    )
    if flags & supervisor_flags != supervisor_flags:
        return None
    if (
        flags & _LOCAL_MODEL_DIRECT_FLAG_SECURITY_ARMED
        and not flags & _LOCAL_MODEL_DIRECT_FLAG_GROUP_SEPARATED
    ):
        return None
    if outcome == _LOCAL_MODEL_DIRECT_OUTCOME_TARGET:
        if flags != _LOCAL_MODEL_DIRECT_ALL_FLAGS or not root_present:
            return None
        try:
            target_returncode = os.waitstatus_to_exitcode(wait_status)
        except ValueError:
            return None
        if target_returncode != wrapper_returncode:
            return None
        return "target", target_returncode
    if outcome == _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL:
        if (
            not root_present
            or flags & cleanup_flags != cleanup_flags
            or wait_status not in _SCOPE_EXEC_FORWARDED_SIGNALS
            or wrapper_returncode != -wait_status
        ):
            return None
        return "signal", wait_status
    if outcome in {
        _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE,
        _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE,
    }:
        required = (
            _LOCAL_MODEL_DIRECT_FLAG_SUBREAPER
            | _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
            | _LOCAL_MODEL_DIRECT_FLAG_SUPERVISOR_HARDENED
            | _LOCAL_MODEL_DIRECT_FLAG_CONTROLLER_BOUND
        )
        if (
            flags & required != required
            or wrapper_returncode != _LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT
            or (
                root_present
                and flags & cleanup_flags != cleanup_flags
            )
            or (
                not root_present
                and flags
                & (
                    _LOCAL_MODEL_DIRECT_FLAG_ROOT_BOUND
                    | _LOCAL_MODEL_DIRECT_FLAG_ROOT_REAPED
                )
            )
        ):
            return None
        if outcome == _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE:
            return "deadline", None
        return "failure", None
    return None


def _build_priority_scope_command(
    argv: Sequence[str],
    *,
    cpu_weight: int,
    io_weight: int,
    unit_name: str | None = None,
    latch_required: bool = False,
    local_model_gate_required: bool = False,
) -> list[str]:
    if not isinstance(cpu_weight, int) or isinstance(cpu_weight, bool):
        raise PriorityScopeError("priority scope CPU weight must be an integer")
    if not isinstance(io_weight, int) or isinstance(io_weight, bool):
        raise PriorityScopeError("priority scope I/O weight must be an integer")
    if not 1 <= cpu_weight <= 10_000 or not 1 <= io_weight <= 10_000:
        raise PriorityScopeError("priority scope weights must be between 1 and 10000")
    command = _scope_exec_wrapper_command(
        argv,
        latch_required=latch_required,
        local_model_gate_required=local_model_gate_required,
    )
    if unit_name is not None and (
        isinstance(unit_name, bool)
        or not isinstance(unit_name, str)
        or len(unit_name) > 255
        or _SCOPE_UNIT_NAME_RE.fullmatch(unit_name) is None
    ):
        raise PriorityScopeError("priority scope unit name is invalid")
    scope_options = [
        "--quiet",
        "--expand-environment=no",
    ]
    if unit_name is not None:
        scope_options.append(f"--unit={unit_name}")
    return [
        _required_scope_tool(),
        "--user",
        "--scope",
        *scope_options,
        "-p",
        f"CPUWeight={cpu_weight}",
        "-p",
        f"IOWeight={io_weight}",
        "--",
        *command,
    ]


def build_soc_priority_scope_command(argv: Sequence[str]) -> list[str]:
    """Build the trusted systemd scope used by the SOC-owned CLI process."""

    return _build_priority_scope_command(
        argv,
        cpu_weight=SOC_CPU_WEIGHT,
        io_weight=SOC_IO_WEIGHT,
        latch_required=True,
    )


def build_recorder_priority_scope_command(
    argv: Sequence[str],
    *,
    unit_name: str,
) -> list[str]:
    """Build a uniquely named high-weight scope for one recorder only."""

    return _build_priority_scope_command(
        argv,
        cpu_weight=SOC_CPU_WEIGHT,
        io_weight=SOC_IO_WEIGHT,
        unit_name=unit_name,
    )


def build_local_model_priority_scope_command(
    argv: Sequence[str],
    *,
    unit_name: str | None = None,
) -> list[str]:
    """Build the separate low-weight scope used by local model children."""

    return _build_priority_scope_command(
        argv,
        cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
        io_weight=LOCAL_MODEL_IO_WEIGHT,
        unit_name=unit_name,
    )


def _read_cgroup_file(path: Path, *, max_bytes: int = MAX_CGROUP_FILE_BYTES) -> str | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except (OSError, ValueError):
        return None
    if len(data) > max_bytes:
        return None
    try:
        return data.decode("ascii")
    except UnicodeDecodeError:
        return None


def _read_bounded_ascii_descriptor(descriptor: int, *, max_bytes: int) -> str | None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        return None
    try:
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(descriptor, min(4096, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > max_bytes:
            return None
        return bytes(data).decode("ascii")
    except (OSError, OverflowError, UnicodeDecodeError, ValueError):
        return None


def _read_affinity_file(
    path: Path,
    *,
    max_bytes: int = MAX_CPU_LIST_BYTES,
) -> str | None:
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        return None
    nofollow = getattr(os, "O_NOFOLLOW", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or nonblock is None:
        return None
    flags = os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except (OSError, TypeError, ValueError):
        return None
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            return None
        return _read_bounded_ascii_descriptor(descriptor, max_bytes=max_bytes)
    except (OSError, ValueError):
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _namespace_fd_identity(
    descriptor: int,
    *,
    expected_type: int,
    expected_id: int,
    expected_inode: int,
) -> tuple[int, int] | None:
    get_status_flags = getattr(fcntl, "F_GETFL", None)
    get_descriptor_flags = getattr(fcntl, "F_GETFD", None)
    close_on_exec = getattr(fcntl, "FD_CLOEXEC", None)
    access_mode = getattr(os, "O_ACCMODE", None)
    if (
        not isinstance(descriptor, int)
        or isinstance(descriptor, bool)
        or descriptor < 0
        or get_status_flags is None
        or get_descriptor_flags is None
        or close_on_exec is None
        or access_mode is None
        or expected_type
        not in {_CLONE_NEWUSER, _CLONE_NEWPID, _CLONE_NEWNS, _CLONE_NEWCGROUP}
        or not isinstance(expected_id, int)
        or isinstance(expected_id, bool)
        or expected_id <= 0
        or not isinstance(expected_inode, int)
        or isinstance(expected_inode, bool)
        or expected_inode <= 0
    ):
        return None
    try:
        before = os.fstat(descriptor)
        status_flags = fcntl.fcntl(descriptor, get_status_flags)
        descriptor_flags = fcntl.fcntl(descriptor, get_descriptor_flags)
        namespace_type = fcntl.ioctl(descriptor, _NS_GET_NSTYPE)
        namespace_id_buffer = bytearray(8)
        namespace_id_result = fcntl.ioctl(
            descriptor,
            _NS_GET_ID,
            namespace_id_buffer,
            True,
        )
        after = os.fstat(descriptor)
        if (
            not isinstance(status_flags, int)
            or isinstance(status_flags, bool)
            or status_flags & access_mode != os.O_RDONLY
            or not isinstance(descriptor_flags, int)
            or isinstance(descriptor_flags, bool)
            or descriptor_flags & close_on_exec == 0
            or namespace_type != expected_type
            or not isinstance(namespace_id_result, int)
            or isinstance(namespace_id_result, bool)
            or namespace_id_result != 0
            or int.from_bytes(
                namespace_id_buffer,
                byteorder=sys.byteorder,
                signed=False,
            )
            != expected_id
            or not stat.S_ISREG(before.st_mode)
            or (before.st_dev, before.st_ino, before.st_mode)
            != (after.st_dev, after.st_ino, after.st_mode)
            or before.st_ino != expected_inode
        ):
            return None
        return before.st_dev, before.st_ino
    except (OSError, OverflowError, TypeError, ValueError):
        return None


def initial_namespace_identity() -> InitialNamespaceIdentity | None:
    """Return stable initial Linux namespace identities, if provable."""

    pidfd_open = getattr(os, "pidfd_open", None)
    if not callable(pidfd_open):
        return None
    try:
        process_id = os.getpid()
        if (
            not isinstance(process_id, int)
            or isinstance(process_id, bool)
            or process_id <= 0
        ):
            return None
        pidfd = pidfd_open(process_id, 0)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return None
    if not isinstance(pidfd, int) or isinstance(pidfd, bool) or pidfd < 0:
        return None

    descriptors = [pidfd]
    descriptor_set = {pidfd}
    identities: list[tuple[int, int]] = []
    result: InitialNamespaceIdentity | None = None
    cleanup_failed = False
    try:
        for request, namespace_type, initial_id, initial_inode in (
            (
                _PIDFD_GET_USER_NAMESPACE,
                _CLONE_NEWUSER,
                USER_NS_INIT_ID,
                USER_NS_INIT_INO,
            ),
            (
                _PIDFD_GET_PID_NAMESPACE,
                _CLONE_NEWPID,
                PID_NS_INIT_ID,
                PID_NS_INIT_INO,
            ),
            (
                _PIDFD_GET_MNT_NAMESPACE,
                _CLONE_NEWNS,
                MNT_NS_INIT_ID,
                MNT_NS_INIT_INO,
            ),
            (
                _PIDFD_GET_CGROUP_NAMESPACE,
                _CLONE_NEWCGROUP,
                CGROUP_NS_INIT_ID,
                CGROUP_NS_INIT_INO,
            ),
        ):
            namespace_fd = fcntl.ioctl(pidfd, request)
            if (
                not isinstance(namespace_fd, int)
                or isinstance(namespace_fd, bool)
                or namespace_fd < 0
                or namespace_fd in descriptor_set
            ):
                break
            descriptors.append(namespace_fd)
            descriptor_set.add(namespace_fd)
            identity = _namespace_fd_identity(
                namespace_fd,
                expected_type=namespace_type,
                expected_id=initial_id,
                expected_inode=initial_inode,
            )
            if identity is None:
                break
            identities.append(identity)
        if len(identities) == 4:
            result = InitialNamespaceIdentity(
                user=identities[0],
                pid=identities[1],
                mount=identities[2],
                cgroup=identities[3],
            )
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        result = None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except (OSError, OverflowError, TypeError, ValueError):
                cleanup_failed = True
    return None if cleanup_failed else result


def _parse_cpu_list(value: str) -> frozenset[int] | None:
    if not isinstance(value, str) or not value or len(value) > MAX_CPU_LIST_BYTES:
        return None
    if value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value:
        return None
    cpus: set[int] = set()
    for item in value.split(","):
        bounds = item.split("-")
        if len(bounds) not in {1, 2}:
            return None
        parsed: list[int] = []
        for bound in bounds:
            if (
                not bound
                or len(bound) > 7
                or not bound.isascii()
                or not bound.isdecimal()
            ):
                return None
            cpu = int(bound)
            if str(cpu) != bound or cpu > MAX_CPU_INDEX:
                return None
            parsed.append(cpu)
        first = parsed[0]
        last = parsed[-1]
        if first > last or len(cpus) + last - first + 1 > MAX_CPU_LIST_COUNT:
            return None
        item_cpus = set(range(first, last + 1))
        if cpus.intersection(item_cpus):
            return None
        cpus.update(item_cpus)
    return frozenset(cpus) if cpus else None


def _normalized_absolute_path(value: str) -> Path | None:
    if not value or "\x00" in value:
        return None
    path = Path(value)
    if not path.is_absolute() or path != Path(os.path.normpath(value)):
        return None
    return path


def _cgroup2_mount_mappings() -> tuple[_Cgroup2MountMapping, ...] | None:
    mountinfo = _read_affinity_file(
        _PROC_SELF_MOUNTINFO,
        max_bytes=MAX_MOUNTINFO_BYTES,
    )
    if mountinfo is None:
        return None
    mappings: list[_Cgroup2MountMapping] = []
    seen_mappings: set[_Cgroup2MountMapping] = set()
    for line in mountinfo.splitlines():
        before_separator, separator, after_separator = line.partition(" - ")
        after_fields = after_separator.split()
        fields = before_separator.split()
        device_numbers = fields[2].split(":", 1) if len(fields) >= 3 else ()
        if (
            not separator
            or len(after_fields) < 3
            or after_fields[0] != "cgroup2"
            or len(fields) < 6
            or not fields[0].isascii()
            or not fields[0].isdecimal()
            or not fields[1].isascii()
            or not fields[1].isdecimal()
            or len(device_numbers) != 2
            or any(
                not number.isascii() or not number.isdecimal()
                for number in device_numbers
            )
            or any(
                len(number) > MAX_CGROUP_DEVICE_COMPONENT_DIGITS
                for number in device_numbers
            )
            or any(len(number) > 1 and number.startswith("0") for number in device_numbers)
            or not fields[5]
        ):
            continue
        try:
            device_major, device_minor = (int(number) for number in device_numbers)
        except (OverflowError, ValueError):
            continue
        if (
            device_major > MAX_CGROUP_DEVICE_COMPONENT
            or device_minor > MAX_CGROUP_DEVICE_COMPONENT
        ):
            continue
        mount_root = _normalized_absolute_path(_decode_mountinfo_path(fields[3]))
        mountpoint = _normalized_absolute_path(_decode_mountinfo_path(fields[4]))
        if mount_root is None or mountpoint is None:
            continue
        mapping = _Cgroup2MountMapping(
            mount_root,
            mountpoint,
            device_major,
            device_minor,
        )
        if mapping in seen_mappings:
            continue
        if len(seen_mappings) >= MAX_CGROUP2_MOUNT_MAPPINGS:
            return None
        seen_mappings.add(mapping)
        mappings.append(mapping)
    return tuple(mappings) if mappings else None


def _canonical_cgroup2_mapping(
    cgroup_path: Path,
    mappings: tuple[_Cgroup2MountMapping, ...],
) -> tuple[Path, _Cgroup2MountMapping] | None:
    candidates: list[tuple[int, Path, _Cgroup2MountMapping]] = []
    for mapping in mappings:
        try:
            relative = cgroup_path.relative_to(mapping.mount_root)
        except ValueError:
            continue
        candidates.append(
            (
                len(mapping.mount_root.parts),
                mapping.mountpoint.joinpath(*relative.parts),
                mapping,
            )
        )
    if not candidates:
        return None
    longest_root = max(specificity for specificity, _, _ in candidates)
    resolved_candidates = {
        (path, mapping)
        for specificity, path, mapping in candidates
        if specificity == longest_root
    }
    if len(resolved_candidates) != 1:
        return None
    return resolved_candidates.pop()


def _canonical_cgroup2_path(
    cgroup_path: Path,
    mappings: tuple[_Cgroup2MountMapping, ...],
) -> Path | None:
    resolved = _canonical_cgroup2_mapping(cgroup_path, mappings)
    return resolved[0] if resolved is not None else None


def _mapped_cgroup2_location(
    cgroup_file: Path,
    *,
    mappings: tuple[_Cgroup2MountMapping, ...] | None = None,
) -> tuple[Path, _Cgroup2MountMapping] | None:
    cgroup_data = _read_affinity_file(
        cgroup_file,
        max_bytes=MAX_PROC_CGROUP_BYTES,
    )
    if cgroup_data is None:
        return None
    cgroup_paths = [line[3:] for line in cgroup_data.splitlines() if line.startswith("0::")]
    if len(cgroup_paths) != 1:
        return None
    cgroup_path = _normalized_absolute_path(cgroup_paths[0])
    if cgroup_path is None:
        return None
    mount_mappings = mappings if mappings is not None else _cgroup2_mount_mappings()
    if mount_mappings is None:
        return None
    mapped = _canonical_cgroup2_mapping(cgroup_path, mount_mappings)
    if mapped is None:
        return None
    resolved, mount_mapping = mapped
    confirmation = _read_affinity_file(
        cgroup_file,
        max_bytes=MAX_PROC_CGROUP_BYTES,
    )
    if confirmation != cgroup_data:
        return None
    try:
        mapped_stat = os.stat(resolved, follow_symlinks=False)
    except (NotImplementedError, OSError, TypeError, ValueError):
        return None
    if (
        not stat.S_ISDIR(mapped_stat.st_mode)
        or not _scope_stat_matches_mount_mapping(mapped_stat, mount_mapping)
    ):
        return None
    return resolved, mount_mapping


def _mapped_cgroup2_path(
    cgroup_file: Path,
    *,
    mappings: tuple[_Cgroup2MountMapping, ...] | None = None,
) -> Path | None:
    location = _mapped_cgroup2_location(cgroup_file, mappings=mappings)
    return location[0] if location is not None else None


def _canonical_cgroup2_path_origin(
    scope_path: Path,
    mappings: tuple[_Cgroup2MountMapping, ...],
) -> tuple[Path, _Cgroup2MountMapping] | None:
    origins: set[tuple[Path, _Cgroup2MountMapping]] = set()
    for mapping in mappings:
        try:
            relative = scope_path.relative_to(mapping.mountpoint)
        except ValueError:
            continue
        if not relative.parts:
            continue
        cgroup_path = mapping.mount_root.joinpath(*relative.parts)
        if _canonical_cgroup2_mapping(cgroup_path, mappings) == (scope_path, mapping):
            origins.add((cgroup_path, mapping))
    if len(origins) != 1:
        return None
    return origins.pop()


def _stored_scope_path_is_canonical(
    scope_path: Path,
    mappings: tuple[_Cgroup2MountMapping, ...],
) -> bool:
    return _canonical_cgroup2_path_origin(scope_path, mappings) is not None


def _cgroup2_relative_components(
    cgroup_path: Path,
    mount_mapping: _Cgroup2MountMapping,
) -> tuple[str, ...] | None:
    try:
        relative = cgroup_path.relative_to(mount_mapping.mountpoint)
    except (TypeError, ValueError):
        return None
    components = relative.parts
    if len(components) > MAX_CGROUP_PARENT_LEVELS or any(
        not component
        or component in {".", ".."}
        or "/" in component
        or "\x00" in component
        for component in components
    ):
        return None
    return components


@contextmanager
def _bound_cgroup2_hierarchy(
    cgroup_path: Path,
    mount_mapping: _Cgroup2MountMapping,
) -> Iterator[tuple[tuple[int, ...], tuple[tuple[int, int], ...]]]:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if (
        directory_flag is None
        or nofollow is None
        or cloexec is None
        or nonblock is None
    ):
        raise PriorityScopeError("SOC CPU affinity cgroup-v2 boundary is unavailable")
    components = _cgroup2_relative_components(cgroup_path, mount_mapping)
    if components is None:
        raise PriorityScopeError(
            "SOC CPU affinity cgroup-v2 boundary is unavailable"
        )

    directory_flags = os.O_RDONLY | directory_flag | nofollow | cloexec | nonblock
    descriptors: list[int] = []
    identities: list[tuple[int, int]] = []
    try:
        try:
            descriptor = os.open(os.fspath(mount_mapping.mountpoint), directory_flags)
        except (NotImplementedError, OSError, TypeError, ValueError):
            raise PriorityScopeError(
                "SOC CPU affinity cgroup-v2 boundary is unavailable"
            ) from None
        descriptors.append(descriptor)
        for component in (None, *components):
            if component is not None:
                try:
                    descriptor = os.open(
                        component,
                        directory_flags,
                        dir_fd=descriptors[-1],
                    )
                except (NotImplementedError, OSError, TypeError, ValueError):
                    raise PriorityScopeError(
                        "SOC CPU affinity cgroup-v2 boundary is unavailable"
                    ) from None
                descriptors.append(descriptor)
            try:
                directory_stat = os.fstat(descriptors[-1])
            except (OSError, ValueError):
                raise PriorityScopeError(
                    "SOC CPU affinity cgroup-v2 boundary is unavailable"
                ) from None
            if (
                not stat.S_ISDIR(directory_stat.st_mode)
                or not _scope_stat_matches_mount_mapping(
                    directory_stat, mount_mapping
                )
            ):
                raise PriorityScopeError(
                    "SOC CPU affinity cgroup-v2 boundary is unavailable"
                )
            identities.append((directory_stat.st_dev, directory_stat.st_ino))

        yield tuple(descriptors), tuple(identities)
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _scope_exec_cpuset_snapshot_from_bound(
    descriptors: tuple[int, ...],
    identities: tuple[tuple[int, int], ...],
    mount_mapping: _Cgroup2MountMapping,
) -> _ScopeExecCpusetSnapshot:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if (
        nofollow is None
        or cloexec is None
        or nonblock is None
        or len(descriptors) != len(identities)
    ):
        raise PriorityScopeError("SOC CPU affinity cgroup-v2 boundary is unavailable")
    file_flags = os.O_RDONLY | nofollow | cloexec | nonblock
    for selected_index in range(len(descriptors) - 1, -1, -1):
        try:
            cpuset_descriptor = os.open(
                "cpuset.cpus.effective",
                file_flags,
                dir_fd=descriptors[selected_index],
            )
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                continue
            raise PriorityScopeError(
                "SOC CPU affinity cpuset is unavailable or invalid"
            ) from None
        except (NotImplementedError, TypeError, ValueError):
            raise PriorityScopeError(
                "SOC CPU affinity cpuset is unavailable or invalid"
            ) from None
        try:
            try:
                cpuset_stat = os.fstat(cpuset_descriptor)
            except (OSError, ValueError):
                raise PriorityScopeError(
                    "SOC CPU affinity cpuset is unavailable or invalid"
                ) from None
            if (
                not stat.S_ISREG(cpuset_stat.st_mode)
                or not _scope_stat_matches_mount_mapping(
                    cpuset_stat, mount_mapping
                )
            ):
                raise PriorityScopeError(
                    "SOC CPU affinity cpuset is unavailable or invalid"
                )
            contents = _read_bounded_ascii_descriptor(
                cpuset_descriptor,
                max_bytes=MAX_CPU_LIST_BYTES,
            )
            cpus = _parse_cpu_list(contents) if contents is not None else None
            if cpus is None:
                raise PriorityScopeError(
                    "SOC CPU affinity cpuset is unavailable or invalid"
                )
            return _ScopeExecCpusetSnapshot(
                selected_directory_index=selected_index,
                directory_identities=identities,
                file_identity=(cpuset_stat.st_dev, cpuset_stat.st_ino),
                contents=contents,
                cpus=cpus,
            )
        finally:
            try:
                os.close(cpuset_descriptor)
            except OSError:
                pass
    raise PriorityScopeError("SOC CPU affinity cpuset is unavailable or invalid")


def _scope_exec_cpuset_snapshot(
    cgroup_path: Path,
    mount_mapping: _Cgroup2MountMapping,
) -> _ScopeExecCpusetSnapshot:
    with _bound_cgroup2_hierarchy(cgroup_path, mount_mapping) as (
        descriptors,
        identities,
    ):
        return _scope_exec_cpuset_snapshot_from_bound(
            descriptors,
            identities,
            mount_mapping,
        )


_CGROUP_RESOURCE_FILES = frozenset(
    {"cpu.max", "memory.current", "memory.max", "memory.high"}
)


def _read_bound_cgroup_resource_file(
    directory_descriptor: int,
    name: str,
    mount_mapping: _Cgroup2MountMapping,
) -> _CgroupResourceFileSnapshot | None:
    if name not in _CGROUP_RESOURCE_FILES:
        return None
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or cloexec is None or nonblock is None:
        return None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | cloexec | nonblock,
            dir_fd=directory_descriptor,
        )
    except (NotImplementedError, OSError, TypeError, ValueError):
        return None
    try:
        before = os.fstat(descriptor)
        contents = _read_bounded_ascii_descriptor(
            descriptor,
            max_bytes=MAX_CGROUP_FILE_BYTES,
        )
        after = os.fstat(descriptor)
        if (
            contents is None
            or not stat.S_ISREG(before.st_mode)
            or not _scope_stat_matches_mount_mapping(before, mount_mapping)
            or (before.st_dev, before.st_ino, before.st_mode)
            != (after.st_dev, after.st_ino, after.st_mode)
        ):
            return None
        return _CgroupResourceFileSnapshot(
            identity=(before.st_dev, before.st_ino),
            contents=contents,
        )
    except (OSError, ValueError):
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _cgroup_resource_snapshot_from_bound(
    descriptors: tuple[int, ...],
    identities: tuple[tuple[int, int], ...],
    mount_mapping: _Cgroup2MountMapping,
) -> _CgroupResourceHierarchySnapshot | None:
    if len(descriptors) < 2:
        return None
    try:
        cpuset = _scope_exec_cpuset_snapshot_from_bound(
            descriptors,
            identities,
            mount_mapping,
        )
    except PriorityScopeError:
        return None
    levels: list[_CgroupResourceLevelSnapshot] = []
    for descriptor in descriptors[1:]:
        files = [
            _read_bound_cgroup_resource_file(descriptor, name, mount_mapping)
            for name in ("cpu.max", "memory.current", "memory.max", "memory.high")
        ]
        if any(item is None for item in files):
            return None
        cpu_max, memory_current, memory_max, memory_high = files
        assert cpu_max is not None
        assert memory_current is not None
        assert memory_max is not None
        assert memory_high is not None
        levels.append(
            _CgroupResourceLevelSnapshot(
                cpu_max=cpu_max,
                memory_current=memory_current,
                memory_max=memory_max,
                memory_high=memory_high,
            )
        )
    return _CgroupResourceHierarchySnapshot(cpuset=cpuset, levels=tuple(levels))


def _cgroup_resource_snapshot_once(
    cgroup_path: Path,
    mount_mapping: _Cgroup2MountMapping,
) -> _CgroupResourceHierarchySnapshot | None:
    try:
        with _bound_cgroup2_hierarchy(cgroup_path, mount_mapping) as (
            descriptors,
            identities,
        ):
            return _cgroup_resource_snapshot_from_bound(
                descriptors,
                identities,
                mount_mapping,
            )
    except PriorityScopeError:
        return None


def _bound_cgroup2_hierarchy_is_current(
    cgroup_path: Path,
    mount_mapping: _Cgroup2MountMapping,
    descriptors: tuple[int, ...],
    identities: tuple[tuple[int, int], ...],
) -> bool:
    components = _cgroup2_relative_components(cgroup_path, mount_mapping)
    if components is None or len(descriptors) != len(identities):
        return False
    paths = [
        mount_mapping.mountpoint.joinpath(*components[:index])
        for index in range(len(descriptors))
    ]
    try:
        for descriptor, identity, path in zip(
            descriptors, identities, paths, strict=True
        ):
            descriptor_stat = os.fstat(descriptor)
            path_stat = os.stat(path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(descriptor_stat.st_mode)
                or not stat.S_ISDIR(path_stat.st_mode)
                or not _scope_stat_matches_mount_mapping(
                    descriptor_stat, mount_mapping
                )
                or (descriptor_stat.st_dev, descriptor_stat.st_ino) != identity
                or (path_stat.st_dev, path_stat.st_ino) != identity
            ):
                return False
    except (NotImplementedError, OSError, TypeError, ValueError):
        return False
    return True


def _parse_cgroup_unsigned(contents: str, *, allow_max: bool) -> int | None:
    if (
        not isinstance(contents, str)
        or not contents
        or len(contents) > MAX_CGROUP_FILE_BYTES
    ):
        return None
    value = contents[:-1] if contents.endswith("\n") else contents
    if not value or "\n" in value or "\r" in value:
        return None
    if allow_max and value == "max":
        return -1
    if (
        not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
        or len(value) > 20
    ):
        return None
    try:
        parsed = int(value)
    except (OverflowError, ValueError):
        return None
    return parsed if parsed <= MAX_CGROUP_RESOURCE_VALUE else None


def _parse_cgroup_cpu_max(contents: str) -> tuple[int | None, int] | None:
    if (
        not isinstance(contents, str)
        or not contents
        or len(contents) > MAX_CGROUP_FILE_BYTES
    ):
        return None
    value = contents[:-1] if contents.endswith("\n") else contents
    fields = value.split(" ")
    if len(fields) != 2 or any(not field for field in fields):
        return None
    quota = _parse_cgroup_unsigned(fields[0], allow_max=True)
    period = _parse_cgroup_unsigned(fields[1], allow_max=False)
    if quota is None or period is None or period <= 0 or quota == 0:
        return None
    return (None if quota == -1 else quota), period


def _merge_cgroup_resource_snapshots(
    first: _CgroupResourceHierarchySnapshot,
    second: _CgroupResourceHierarchySnapshot,
) -> _CgroupResourceHierarchySnapshot | None:
    if first.cpuset != second.cpuset or len(first.levels) != len(second.levels):
        return None
    merged_levels: list[_CgroupResourceLevelSnapshot] = []
    for earlier, later in zip(first.levels, second.levels, strict=True):
        if (
            earlier.cpu_max != later.cpu_max
            or earlier.memory_max != later.memory_max
            or earlier.memory_high != later.memory_high
            or earlier.memory_current.identity != later.memory_current.identity
        ):
            return None
        earlier_current = _parse_cgroup_unsigned(
            earlier.memory_current.contents,
            allow_max=False,
        )
        later_current = _parse_cgroup_unsigned(
            later.memory_current.contents,
            allow_max=False,
        )
        if earlier_current is None or later_current is None:
            return None
        merged_levels.append(
            _CgroupResourceLevelSnapshot(
                cpu_max=later.cpu_max,
                memory_current=_CgroupResourceFileSnapshot(
                    identity=later.memory_current.identity,
                    contents=f"{max(earlier_current, later_current)}\n",
                ),
                memory_max=later.memory_max,
                memory_high=later.memory_high,
            )
        )
    return _CgroupResourceHierarchySnapshot(
        cpuset=second.cpuset,
        levels=tuple(merged_levels),
    )


def _cgroup_resource_public_snapshot(
    snapshot: _CgroupResourceHierarchySnapshot,
    *,
    online_cpus: frozenset[int] | None,
) -> CgroupResourceSnapshot | None:
    cpu_limits: list[tuple[int, int]] = []
    periods: list[int] = []
    currents: list[int] = []
    maxima: list[int | None] = []
    highs: list[int | None] = []
    for level in snapshot.levels:
        cpu_max = _parse_cgroup_cpu_max(level.cpu_max.contents)
        current = _parse_cgroup_unsigned(
            level.memory_current.contents,
            allow_max=False,
        )
        maximum = _parse_cgroup_unsigned(level.memory_max.contents, allow_max=True)
        high = _parse_cgroup_unsigned(level.memory_high.contents, allow_max=True)
        if cpu_max is None or current is None or maximum is None or high is None:
            return None
        quota, period = cpu_max
        periods.append(period)
        if quota is not None:
            cpu_limits.append((quota, period))
        currents.append(current)
        maxima.append(None if maximum == -1 else maximum)
        highs.append(None if high == -1 else high)
    if not periods or not currents:
        return None
    effective_cpu_limit: tuple[int, int] | None = None
    for candidate in cpu_limits:
        if (
            effective_cpu_limit is None
            or candidate[0] * effective_cpu_limit[1]
            < effective_cpu_limit[0] * candidate[1]
        ):
            effective_cpu_limit = candidate
    finite_maxima = [value for value in maxima if value is not None]
    finite_highs = [value for value in highs if value is not None]
    max_headrooms = [
        max(0, limit - current)
        for limit, current in zip(maxima, currents, strict=True)
        if limit is not None
    ]
    high_headrooms = [
        max(0, limit - current)
        for limit, current in zip(highs, currents, strict=True)
        if limit is not None
    ]
    quota, period = (
        effective_cpu_limit
        if effective_cpu_limit is not None
        else (None, periods[-1])
    )
    return CgroupResourceSnapshot(
        effective_cpus=snapshot.cpuset.cpus,
        online_cpus=online_cpus,
        cpu_quota_us=quota,
        cpu_period_us=period,
        memory_current_bytes=currents[-1],
        memory_max_bytes=min(finite_maxima) if finite_maxima else None,
        memory_high_bytes=min(finite_highs) if finite_highs else None,
        memory_max_headroom_bytes=min(max_headrooms) if max_headrooms else None,
        memory_high_headroom_bytes=min(high_headrooms) if high_headrooms else None,
    )


def current_cgroup_resource_snapshot(
    *,
    include_online_cpus: bool = False,
) -> CgroupResourceSnapshot | None:
    """Return a stable, identifier-free cgroup-v2 resource snapshot."""

    if not isinstance(include_online_cpus, bool):
        return None
    namespace_identity = initial_namespace_identity()
    if namespace_identity is None:
        return None
    location = _mapped_cgroup2_location(_PROC_SELF_CGROUP)
    if location is None:
        return None
    cgroup_path, mount_mapping = location
    # A non-root bind hides enforcing ancestors, so effective limits are unknown.
    if mount_mapping.mount_root != Path("/"):
        return None
    first_snapshot = _cgroup_resource_snapshot_once(cgroup_path, mount_mapping)
    if first_snapshot is None:
        return None
    first_online: str | None = None
    if include_online_cpus:
        first_online = _read_affinity_file(_CPU_ONLINE)
        parsed_online = (
            _parse_cpu_list(first_online) if first_online is not None else None
        )
        if parsed_online is None:
            return None
    confirmation = _mapped_cgroup2_location(_PROC_SELF_CGROUP)
    if confirmation != location:
        return None
    try:
        with _bound_cgroup2_hierarchy(*confirmation) as (descriptors, identities):
            second_snapshot = _cgroup_resource_snapshot_from_bound(
                descriptors,
                identities,
                mount_mapping,
            )
            if second_snapshot is None:
                return None
            stable_snapshot = _merge_cgroup_resource_snapshots(
                first_snapshot,
                second_snapshot,
            )
            if stable_snapshot is None:
                return None
            online_cpus: frozenset[int] | None = None
            if include_online_cpus:
                second_online = _read_affinity_file(_CPU_ONLINE)
                if second_online != first_online:
                    return None
                online_cpus = (
                    _parse_cpu_list(second_online)
                    if second_online is not None
                    else None
                )
                if online_cpus is None:
                    return None
            result = _cgroup_resource_public_snapshot(
                stable_snapshot,
                online_cpus=online_cpus,
            )
            if result is None:
                return None
            if _mapped_cgroup2_location(_PROC_SELF_CGROUP) != location:
                return None
            if not _bound_cgroup2_hierarchy_is_current(
                cgroup_path,
                mount_mapping,
                descriptors,
                identities,
            ):
                return None
            if initial_namespace_identity() != namespace_identity:
                return None
            return result
    except PriorityScopeError:
        return None


def _scope_exec_allowed_cpus() -> frozenset[int]:
    location = _mapped_cgroup2_location(_PROC_SELF_CGROUP)
    if location is None:
        raise PriorityScopeError("SOC CPU affinity cgroup-v2 boundary is unavailable")
    cgroup_path, mount_mapping = location
    first_snapshot = _scope_exec_cpuset_snapshot(cgroup_path, mount_mapping)
    online_data = _read_affinity_file(_CPU_ONLINE)
    online = _parse_cpu_list(online_data) if online_data is not None else None
    if online is None:
        raise PriorityScopeError("SOC online CPU list is unavailable or invalid")
    confirmation = _mapped_cgroup2_location(_PROC_SELF_CGROUP)
    if confirmation != location:
        raise PriorityScopeError("SOC CPU affinity cgroup-v2 boundary is unavailable")
    second_snapshot = _scope_exec_cpuset_snapshot(*confirmation)
    if second_snapshot != first_snapshot:
        raise PriorityScopeError("SOC CPU affinity cpuset is unavailable or invalid")
    if _mapped_cgroup2_location(_PROC_SELF_CGROUP) != location:
        raise PriorityScopeError("SOC CPU affinity cgroup-v2 boundary is unavailable")
    target = first_snapshot.cpus.intersection(online)
    if not target:
        raise PriorityScopeError("SOC CPU affinity boundary contains no online CPUs")
    return frozenset(target)


def _normalize_cpu_affinity_for_scope_exec() -> bool:
    try:
        target = _scope_exec_allowed_cpus()
    except PriorityScopeError:
        return False
    try:
        current = frozenset(os.sched_getaffinity(0))
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return False
    if current == target:
        return True
    try:
        os.sched_setaffinity(0, target)
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return False
    try:
        verified = frozenset(os.sched_getaffinity(0))
    except (AttributeError, OSError, OverflowError, TypeError, ValueError):
        return True
    if not verified.issubset(target):
        raise _ScopeExecAffinityError("verify")
    return True


def _parse_scope_exec_identity_value(value: str) -> int | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value[0] == "0")
    ):
        return None
    try:
        result = int(value)
    except (OverflowError, ValueError):
        return None
    if result < 0 or result > MAX_PRIORITY_SCOPE_IDENTITY_INTEGER:
        return None
    return result


def _scope_exec_launch_spec_from_arguments(
    arguments: Sequence[str],
) -> _ScopeExecLaunchSpec | None:
    if not arguments or arguments[0] != _SCOPE_EXEC_WRAPPER_TOKEN:
        return None
    identity_start = (
        2
        if len(arguments) > 1
        and arguments[1]
        in (
            _SCOPE_EXEC_LATCH_REQUIRED_TOKEN,
            _LOCAL_MODEL_GATE_REQUIRED_TOKEN,
        )
        else 1
    )
    if len(arguments) < identity_start + 8:
        return None
    values = tuple(
        _parse_scope_exec_identity_value(value)
        for value in arguments[identity_start : identity_start + 6]
    )
    delimiter = identity_start + 6
    if any(value is None for value in values) or arguments[delimiter] != "--":
        return None
    spec = _scope_exec_launch_spec(arguments[delimiter + 1 :])
    if spec is None:
        return None
    identity = spec.executable
    expected = (
        identity.device,
        identity.inode,
        identity.size,
        identity.mode,
        identity.mtime_ns,
        identity.ctime_ns,
    )
    if values != expected:
        return None
    return spec


def _local_model_direct_launch_spec_from_arguments(
    arguments: Sequence[str],
) -> _ScopeExecLaunchSpec | None:
    if isinstance(arguments, (str, bytes)):
        return None
    try:
        command = list(arguments)
    except (TypeError, ValueError):
        return None
    if not command or command[0] != _LOCAL_MODEL_DIRECT_EXEC_TOKEN:
        return None
    command[0] = _SCOPE_EXEC_WRAPPER_TOKEN
    return _scope_exec_launch_spec_from_arguments(command)


def _scope_exec_latch_from_environment(
    environment: dict[str, str],
    *,
    required: bool,
) -> tuple[str, str] | None:
    address = environment.pop(_SCOPE_EXEC_LATCH_ADDRESS_ENV, None)
    nonce = environment.pop(_SCOPE_EXEC_LATCH_NONCE_ENV, None)
    if address is None and nonce is None:
        if required:
            raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
        return None
    if (
        not isinstance(address, str)
        or _SCOPE_EXEC_LATCH_ADDRESS_RE.fullmatch(address) is None
        or not isinstance(nonce, str)
        or _SCOPE_EXEC_LATCH_NONCE_RE.fullmatch(nonce) is None
    ):
        raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
    return address, nonce


def _send_scope_exec_entered_latch(address: str, nonce: str) -> None:
    message = _SCOPE_EXEC_LATCH_MESSAGE_PREFIX + nonce.encode("ascii")
    sender: socket.socket | None = None
    try:
        socket_type = socket.SOCK_DGRAM | getattr(socket, "SOCK_CLOEXEC", 0)
        sender = socket.socket(socket.AF_UNIX, socket_type)
        written = sender.sendto(message, "\0" + address)
    except (OSError, UnicodeError, ValueError):
        raise PriorityScopeError(_SCOPE_EXEC_FAILURE) from None
    finally:
        if sender is not None:
            try:
                sender.close()
            except OSError:
                pass
    if written != len(message):
        raise PriorityScopeError(_SCOPE_EXEC_FAILURE)


def _valid_process_id(value: object) -> bool:
    return type(value) is int and 1 <= value <= MAX_LINUX_PID


def _descriptor_is_cloexec(descriptor: int) -> bool:
    try:
        return bool(fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
    except (OSError, OverflowError, TypeError, ValueError):
        return False


def _preferred_gate_exception(
    primary: BaseException | None,
    candidate: BaseException,
) -> BaseException:
    if isinstance(primary, KeyboardInterrupt):
        return primary
    if isinstance(candidate, KeyboardInterrupt):
        return candidate
    return primary if primary is not None else candidate


def _local_model_gate_signals_to_block() -> frozenset[int] | None:
    valid_signals = getattr(signal, "valid_signals", None)
    if not callable(valid_signals):
        return None
    synchronous = {
        item
        for item in (
            getattr(signal, "SIGBUS", None),
            getattr(signal, "SIGFPE", None),
            getattr(signal, "SIGILL", None),
            getattr(signal, "SIGSEGV", None),
            getattr(signal, "SIGTRAP", None),
            getattr(signal, "SIGKILL", None),
            getattr(signal, "SIGSTOP", None),
        )
        if item is not None
    }
    try:
        return frozenset(
            item
            for item in valid_signals()
            if item not in synchronous and callable(signal.getsignal(item))
        )
    except (Exception, SystemExit):
        return None


def _run_with_local_model_gate_signals_blocked(operation):
    blocker = getattr(signal, "pthread_sigmask", None)
    masked = _local_model_gate_signals_to_block()
    if not callable(blocker) or masked is None:
        raise OSError(errno.ENOTSUP, "signal masking is unavailable")
    try:
        previous_mask = blocker(signal.SIG_BLOCK, masked)
    except (Exception, SystemExit):
        raise OSError(errno.ENOTSUP, "signal masking failed") from None
    result: object = None
    primary: BaseException | None = None
    try:
        result = operation()
    except BaseException as exc:
        primary = exc
    try:
        blocker(signal.SIG_SETMASK, previous_mask)
    except BaseException as exc:
        primary = _preferred_gate_exception(primary, exc)
    if primary is not None:
        raise primary
    return result


def _acquire_local_model_gate_descriptor(
    owner: list[int],
    index: int,
    operation,
) -> int:
    def acquire() -> int:
        descriptor = operation()
        if type(descriptor) is not int or descriptor < 0:
            raise OSError(errno.EBADF, "invalid descriptor")
        owner[index] = descriptor
        return descriptor

    result = _run_with_local_model_gate_signals_blocked(acquire)
    if type(result) is not int or result < 0:
        raise OSError(errno.EBADF, "invalid descriptor")
    return result


def _cleanup_local_model_gate_resources(
    descriptor_owners: Sequence[list[int]],
    socket_owners: Sequence[list[socket.socket | None]],
) -> BaseException | None:
    primary: BaseException | None = None

    def cleanup() -> None:
        nonlocal primary
        closed: set[int] = set()
        for owner in descriptor_owners:
            for index, descriptor in enumerate(owner):
                owner[index] = -1
                if (
                    type(descriptor) is not int
                    or descriptor < 0
                    or descriptor in closed
                ):
                    continue
                closed.add(descriptor)
                try:
                    os.close(descriptor)
                except BaseException as exc:
                    primary = _preferred_gate_exception(primary, exc)
        for owner in socket_owners:
            endpoint = owner[0]
            owner[0] = None
            if endpoint is None:
                continue
            try:
                endpoint.close()
            except BaseException as exc:
                primary = _preferred_gate_exception(primary, exc)

    try:
        _run_with_local_model_gate_signals_blocked(cleanup)
    except BaseException as exc:
        primary = _preferred_gate_exception(primary, exc)
        cleanup()
    return primary


def _close_gate_descriptors(descriptors: Sequence[int]) -> bool:
    success = True
    closed: set[int] = set()
    for descriptor in descriptors:
        if type(descriptor) is not int or descriptor < 0 or descriptor in closed:
            success = False
            continue
        closed.add(descriptor)
        try:
            os.close(descriptor)
        except (Exception, SystemExit):
            success = False
    return success


def _read_local_model_gate_descriptor(
    descriptor: int,
    *,
    max_bytes: int,
) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    interruptions = 0
    while total <= max_bytes:
        try:
            chunk = os.read(descriptor, min(4096, max_bytes + 1 - total))
        except InterruptedError:
            interruptions += 1
            if interruptions > 128:
                return None
            continue
        except (OSError, OverflowError, TypeError, ValueError):
            return None
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
    return None


def _parse_process_start_time(contents: bytes, process_id: int) -> int | None:
    if not contents or len(contents) > _LOCAL_MODEL_GATE_PROC_STAT_BYTES:
        return None
    prefix = str(process_id).encode("ascii") + b" ("
    closing = contents.rfind(b") ")
    if not contents.startswith(prefix) or closing < len(prefix):
        return None
    fields = contents[closing + 2 :].strip().split()
    if len(fields) < 20:
        return None
    value = fields[19]
    if (
        not value
        or len(value) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
        or not value.isascii()
        or not value.isdigit()
        or (len(value) > 1 and value.startswith(b"0"))
    ):
        return None
    try:
        start_time = int(value)
    except (OverflowError, ValueError):
        return None
    if not 1 <= start_time <= MAX_PRIORITY_SCOPE_IDENTITY_INTEGER:
        return None
    return start_time


def _canonical_process_identity(
    process_id: int,
) -> _CanonicalProcessIdentity | None:
    if not _valid_process_id(process_id):
        return None
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if None in {nofollow, cloexec, directory_flag, nonblock}:
        return None
    descriptors = [-1, -1, -1]
    result: _CanonicalProcessIdentity | None = None
    primary: BaseException | None = None
    try:
        proc_descriptor = _acquire_local_model_gate_descriptor(
            descriptors,
            0,
            lambda: os.open(
                _PROC_ROOT,
                os.O_RDONLY | directory_flag | nofollow | cloexec,
            ),
        )
        proc_before = os.fstat(proc_descriptor)
        process_descriptor = _acquire_local_model_gate_descriptor(
            descriptors,
            1,
            lambda: os.open(
                str(process_id),
                os.O_RDONLY | directory_flag | nofollow | cloexec,
                dir_fd=proc_descriptor,
            ),
        )
        process_before = os.fstat(process_descriptor)
        stat_descriptor = _acquire_local_model_gate_descriptor(
            descriptors,
            2,
            lambda: os.open(
                "stat",
                os.O_RDONLY | nonblock | nofollow | cloexec,
                dir_fd=process_descriptor,
            ),
        )
        stat_before = os.fstat(stat_descriptor)
        contents = _read_local_model_gate_descriptor(
            stat_descriptor,
            max_bytes=_LOCAL_MODEL_GATE_PROC_STAT_BYTES,
        )
        stat_after = os.fstat(stat_descriptor)
        process_after = os.fstat(process_descriptor)
        proc_after = os.fstat(proc_descriptor)
        start_time = (
            None
            if contents is None
            else _parse_process_start_time(contents, process_id)
        )
        if (
            start_time is not None
            and stat.S_ISDIR(proc_before.st_mode)
            and (proc_before.st_dev, proc_before.st_ino, proc_before.st_mode)
            == (proc_after.st_dev, proc_after.st_ino, proc_after.st_mode)
            and stat.S_ISDIR(process_before.st_mode)
            and (
                process_before.st_dev,
                process_before.st_ino,
                process_before.st_mode,
            )
            == (
                process_after.st_dev,
                process_after.st_ino,
                process_after.st_mode,
            )
            and stat.S_ISREG(stat_before.st_mode)
            and (
                stat_before.st_dev,
                stat_before.st_ino,
                stat_before.st_mode,
            )
            == (stat_after.st_dev, stat_after.st_ino, stat_after.st_mode)
            and all(_descriptor_is_cloexec(item) for item in descriptors)
        ):
            result = _CanonicalProcessIdentity(
                process_id=process_id,
                directory_device=process_before.st_dev,
                directory_inode=process_before.st_ino,
                start_time=start_time,
            )
    except BaseException as exc:
        primary = exc
    cleanup_error = _cleanup_local_model_gate_resources(
        (list(reversed(descriptors)),),
        (),
    )
    if cleanup_error is not None:
        primary = _preferred_gate_exception(primary, cleanup_error)
    if isinstance(primary, KeyboardInterrupt):
        raise primary
    if primary is not None:
        return None
    return result


def _pidfd_process_id(descriptor: int) -> int | None:
    if (
        type(descriptor) is not int
        or descriptor < 0
        or not _descriptor_is_cloexec(descriptor)
    ):
        return None
    contents = _read_affinity_file(
        _PROC_ROOT / "self" / "fdinfo" / str(descriptor),
        max_bytes=_LOCAL_MODEL_GATE_PIDFD_INFO_BYTES,
    )
    if contents is None or not contents.endswith("\n") or "\r" in contents:
        return None
    found: int | None = None
    for line in contents[:-1].split("\n"):
        if not line.startswith("Pid:"):
            continue
        value = line.removeprefix("Pid:\t")
        if (
            found is not None
            or value == line
            or not value
            or len(value) > MAX_LINUX_PID_DECIMAL_DIGITS
            or not value.isascii()
            or not value.isdecimal()
            or (len(value) > 1 and value.startswith("0"))
        ):
            return None
        try:
            parsed = int(value)
        except (OverflowError, ValueError):
            return None
        if not _valid_process_id(parsed):
            return None
        found = parsed
    return found


def _open_identity_pidfd(
    identity: _CanonicalProcessIdentity | None,
    owner: list[int],
) -> bool:
    opener = getattr(os, "pidfd_open", None)
    if (
        type(identity) is not _CanonicalProcessIdentity
        or type(owner) is not list
        or len(owner) != 1
        or owner[0] != -1
        or not callable(opener)
    ):
        return False
    primary: BaseException | None = None
    valid = False
    try:
        descriptor = _acquire_local_model_gate_descriptor(
            owner,
            0,
            lambda: opener(identity.process_id, 0),
        )
        valid = (
            _pidfd_process_id(descriptor) == identity.process_id
            and _canonical_process_identity(identity.process_id) == identity
        )
    except BaseException as exc:
        primary = exc
    if not valid:
        cleanup_error = _cleanup_local_model_gate_resources((owner,), ())
        if cleanup_error is not None:
            primary = _preferred_gate_exception(primary, cleanup_error)
    if isinstance(primary, KeyboardInterrupt):
        raise primary
    return valid and primary is None


def _identity_pidfd_is_live(
    identity: _CanonicalProcessIdentity,
    descriptor: int,
) -> bool:
    try:
        if (
            type(identity) is not _CanonicalProcessIdentity
            or _pidfd_process_id(descriptor) != identity.process_id
        ):
            return False
        readable_before, _, _ = select.select((descriptor,), (), (), 0.0)
        current_identity = _canonical_process_identity(identity.process_id)
        readable_after, _, _ = select.select((descriptor,), (), (), 0.0)
        return (
            not readable_before
            and not readable_after
            and current_identity == identity
        )
    except (Exception, SystemExit):
        return False


def _local_model_gate_datagram_is_attributable(
    datagram: _LocalModelGateDatagram,
    process_id: int,
) -> bool:
    return any(
        credentials[0] == process_id
        for credentials in datagram.credentials
        if len(credentials) == 3
    ) or process_id in datagram.pidfd_process_ids


def _receive_local_model_gate_datagram(
    receiver: socket.socket,
    *,
    max_bytes: int,
) -> _LocalModelGateDatagram:
    blocker = getattr(signal, "pthread_sigmask", None)
    valid_signals = getattr(signal, "valid_signals", None)
    if not callable(blocker) or not callable(valid_signals):
        raise OSError(errno.ENOTSUP, "signal masking is unavailable")
    synchronous = {
        item
        for item in (
            getattr(signal, "SIGBUS", None),
            getattr(signal, "SIGFPE", None),
            getattr(signal, "SIGILL", None),
            getattr(signal, "SIGSEGV", None),
            getattr(signal, "SIGTRAP", None),
            getattr(signal, "SIGKILL", None),
            getattr(signal, "SIGSTOP", None),
        )
        if item is not None
    }
    try:
        masked = {
            item
            for item in valid_signals()
            if item not in synchronous and callable(signal.getsignal(item))
        }
        previous_mask = blocker(signal.SIG_BLOCK, masked)
    except (Exception, SystemExit):
        raise OSError(errno.ENOTSUP, "signal masking failed") from None
    credentials_size = struct.calcsize("3i")
    descriptor_size = struct.calcsize("i")
    ancillary_size = socket.CMSG_SPACE(credentials_size) + socket.CMSG_SPACE(
        descriptor_size
    )
    received_fds: list[int] = []
    result: _LocalModelGateDatagram | None = None
    primary: BaseException | None = None
    cleanup_failed = False
    credentials: list[tuple[int, int, int]] = []
    pidfds: list[int] = []
    unknown = False
    try:
        message, ancillary, flags, source = receiver.recvmsg(
            max_bytes + 1,
            ancillary_size,
            socket.MSG_CMSG_CLOEXEC,
        )
        for level, kind, data in ancillary:
            if level != socket.SOL_SOCKET:
                unknown = True
                continue
            if kind == socket.SCM_CREDENTIALS:
                if len(data) != credentials_size:
                    unknown = True
                else:
                    credentials.append(struct.unpack("3i", data))
                continue
            if kind in {_SCM_PIDFD, socket.SCM_RIGHTS}:
                usable = len(data) - (len(data) % descriptor_size)
                descriptors = [
                    item[0] for item in struct.iter_unpack("i", data[:usable])
                ]
                received_fds.extend(descriptors)
                if kind == _SCM_PIDFD and len(data) == descriptor_size:
                    pidfds.extend(descriptors)
                else:
                    unknown = True
                if usable != len(data):
                    unknown = True
                continue
            unknown = True
        pidfd_process_ids = tuple(
            _pidfd_process_id(descriptor) for descriptor in pidfds
        )
        result = _LocalModelGateDatagram(
            message=message,
            credentials=tuple(credentials),
            pidfd_process_ids=pidfd_process_ids,
            flags=flags,
            source=source,
            unknown_ancillary=unknown,
        )
    except BaseException as exc:
        primary = exc
    finally:
        for descriptor in dict.fromkeys(received_fds):
            try:
                os.close(descriptor)
            except BaseException as exc:
                cleanup_failed = True
                if isinstance(exc, KeyboardInterrupt) and not isinstance(
                    primary, KeyboardInterrupt
                ):
                    primary = exc
                elif primary is None:
                    primary = exc
        try:
            blocker(signal.SIG_SETMASK, previous_mask)
        except BaseException as exc:
            cleanup_failed = True
            if isinstance(exc, KeyboardInterrupt) and not isinstance(
                primary, KeyboardInterrupt
            ):
                primary = exc
            elif primary is None:
                primary = exc
    if primary is not None:
        raise primary
    if cleanup_failed or result is None:
        raise OSError(errno.EIO, "gate descriptor cleanup failed")
    return result


def _valid_local_model_gate_runtime_path(value: object) -> bool:
    return not (
        type(value) is not str
        or not value
        or not value.isascii()
        or value.startswith("//")
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _local_model_gate_runtime_path(uid: int) -> str | None:
    value = os.environ.get("XDG_RUNTIME_DIR")
    if value is None:
        value = f"/run/user/{uid}"
    if not _valid_local_model_gate_runtime_path(value):
        return None
    return value


def _valid_local_model_gate_domain_name(value: object) -> bool:
    return (
        type(value) is str
        and value.startswith(_LOCAL_MODEL_GATE_DIRECTORY_PREFIX)
        and _LOCAL_MODEL_GATE_ADDRESS_RE.fullmatch(
            value.removeprefix(_LOCAL_MODEL_GATE_DIRECTORY_PREFIX)
        )
        is not None
    )


def _new_local_model_gate_domain_name() -> str:
    return _LOCAL_MODEL_GATE_DIRECTORY_PREFIX + secrets.token_hex(16)


def _local_model_gate_directory_identity(
    metadata: os.stat_result,
    uid: int,
) -> tuple[int, int, int, int] | None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        return None
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid)


def _local_model_gate_ancestor_identity(
    metadata: os.stat_result,
    uid: int,
) -> tuple[int, int, int, int] | None:
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid not in {0, uid}
        or mode & (stat.S_ISUID | stat.S_ISGID)
        or (
            mode & 0o022
            and not (
                metadata.st_uid == 0
                and mode & stat.S_ISVTX
            )
        )
    ):
        return None
    return (metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_uid)


def _local_model_gate_endpoint_ownership_identity(
    metadata: os.stat_result,
    uid: int,
) -> tuple[int, int, int, int, int] | None:
    if (
        not stat.S_ISSOCK(metadata.st_mode)
        or metadata.st_uid != uid
        or metadata.st_nlink != 1
    ):
        return None
    return (
        metadata.st_dev,
        metadata.st_ino,
        stat.S_IFMT(metadata.st_mode),
        metadata.st_uid,
        metadata.st_nlink,
    )


def _local_model_gate_endpoint_identity(
    metadata: os.stat_result,
    uid: int,
) -> tuple[int, int, int, int, int] | None:
    if (
        not stat.S_ISSOCK(metadata.st_mode)
        or metadata.st_uid != uid
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        return None
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
    )


def _open_local_model_gate_runtime_path(
    descriptor_owner: list[int],
    index: int,
    runtime_path: str,
    uid: int,
) -> tuple[int, int, int, int] | None:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    directory = getattr(os, "O_DIRECTORY", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    components = tuple(part for part in runtime_path.split("/") if part)
    if (
        type(descriptor_owner) is not list
        or not 0 <= index < len(descriptor_owner)
        or descriptor_owner[index] != -1
        or not _valid_local_model_gate_runtime_path(runtime_path)
        or not 1 <= len(components) <= _LOCAL_MODEL_GATE_MAX_RUNTIME_COMPONENTS
        or None in {nofollow, cloexec, directory, nonblock}
    ):
        return None
    flags = os.O_RDONLY | directory | nofollow | cloexec | nonblock
    current_descriptor = -1
    next_descriptor = -1

    def open_components() -> tuple[int, int, int, int]:
        nonlocal current_descriptor, next_descriptor
        primary: BaseException | None = None
        result: tuple[int, int, int, int] | None = None
        try:
            current_descriptor = os.open("/", flags)
            root_metadata = os.fstat(current_descriptor)
            if (
                _local_model_gate_ancestor_identity(root_metadata, uid) is None
                or not _descriptor_is_cloexec(current_descriptor)
            ):
                raise OSError(errno.EACCES, "unsafe runtime ancestry")
            for position, component in enumerate(components):
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=current_descriptor,
                )
                descriptor_metadata = os.fstat(next_descriptor)
                path_metadata = os.stat(
                    component,
                    dir_fd=current_descriptor,
                    follow_symlinks=False,
                )
                descriptor_identity = (
                    descriptor_metadata.st_dev,
                    descriptor_metadata.st_ino,
                    descriptor_metadata.st_mode,
                    descriptor_metadata.st_uid,
                )
                path_identity = (
                    path_metadata.st_dev,
                    path_metadata.st_ino,
                    path_metadata.st_mode,
                    path_metadata.st_uid,
                )
                final_component = position == len(components) - 1
                validated_identity = (
                    _local_model_gate_directory_identity(descriptor_metadata, uid)
                    if final_component
                    else _local_model_gate_ancestor_identity(
                        descriptor_metadata,
                        uid,
                    )
                )
                if (
                    validated_identity is None
                    or descriptor_identity != path_identity
                    or not _descriptor_is_cloexec(next_descriptor)
                ):
                    raise OSError(errno.EACCES, "unsafe runtime ancestry")
                previous_descriptor = current_descriptor
                current_descriptor = -1
                os.close(previous_descriptor)
                current_descriptor = next_descriptor
                next_descriptor = -1
            result = _local_model_gate_directory_identity(
                os.fstat(current_descriptor),
                uid,
            )
            if result is None:
                raise OSError(errno.EACCES, "unsafe runtime directory")
            descriptor_owner[index] = current_descriptor
            current_descriptor = -1
        except BaseException as exc:
            primary = exc
        for descriptor_name in ("next_descriptor", "current_descriptor"):
            descriptor = (
                next_descriptor
                if descriptor_name == "next_descriptor"
                else current_descriptor
            )
            if descriptor < 0:
                continue
            if descriptor_name == "next_descriptor":
                next_descriptor = -1
            else:
                current_descriptor = -1
            try:
                os.close(descriptor)
            except BaseException as exc:
                primary = _preferred_gate_exception(primary, exc)
        if primary is not None:
            raise primary
        if result is None:
            raise OSError(errno.EIO, "runtime directory unavailable")
        return result

    result = _run_with_local_model_gate_signals_blocked(open_components)
    return result if type(result) is tuple and len(result) == 4 else None


def _local_model_gate_runtime_path_identity(
    runtime_path: str,
    uid: int,
) -> tuple[int, int, int, int] | None:
    descriptor_owner = [-1]
    identity: tuple[int, int, int, int] | None = None
    primary: BaseException | None = None
    try:
        identity = _open_local_model_gate_runtime_path(
            descriptor_owner,
            0,
            runtime_path,
            uid,
        )
    except BaseException as exc:
        primary = exc
    cleanup_error = _cleanup_local_model_gate_resources((descriptor_owner,), ())
    if cleanup_error is not None:
        primary = _preferred_gate_exception(primary, cleanup_error)
    if primary is not None:
        raise primary
    return identity


def _valid_local_model_gate_endpoint_path(
    value: object,
    *,
    prefix: str,
) -> bool:
    if (
        type(value) is not str
        or type(prefix) is not str
        or value.startswith("//")
        or not os.path.isabs(value)
        or os.path.normpath(value) != value
    ):
        return False
    try:
        encoded = os.fsencode(value)
    except (UnicodeError, ValueError):
        return False
    domain_name = os.path.basename(os.path.dirname(value))
    name = os.path.basename(value)
    token = name.removeprefix(prefix).removesuffix(
        _LOCAL_MODEL_GATE_SOCKET_SUFFIX
    )
    return (
        len(encoded) <= _LOCAL_MODEL_GATE_MAX_SOCKET_PATH_BYTES
        and _valid_local_model_gate_domain_name(domain_name)
        and name
        == prefix + token + _LOCAL_MODEL_GATE_SOCKET_SUFFIX
        and _LOCAL_MODEL_GATE_ADDRESS_RE.fullmatch(token) is not None
    )


def _open_local_model_gate_directory(
    owner: _LocalModelGateResourceOwner,
    *,
    uid: int,
    create: bool,
    gate_name: str | None = None,
) -> bool:
    if (
        type(owner) is not _LocalModelGateResourceOwner
        or type(uid) is not int
        or uid < 0
        or type(create) is not bool
        or owner.binding is not None
        or owner.created_domain_name is not None
        or owner.directory_descriptors != [-1, -1]
        or (create and gate_name is not None)
        or (not create and not _valid_local_model_gate_domain_name(gate_name))
    ):
        return False
    runtime_path = _local_model_gate_runtime_path(uid)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    directory = getattr(os, "O_DIRECTORY", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if runtime_path is None or None in {nofollow, cloexec, directory, nonblock}:
        return False
    try:
        runtime_identity = _open_local_model_gate_runtime_path(
            owner.directory_descriptors,
            0,
            runtime_path,
            uid,
        )
        if runtime_identity is None:
            return False
        runtime_descriptor = owner.directory_descriptors[0]
        binding: _LocalModelGateDirectoryBinding | None = None
        for candidate in range(4) if create else (gate_name,):
            candidate_name = (
                _new_local_model_gate_domain_name() if create else candidate
            )
            if not _valid_local_model_gate_domain_name(candidate_name):
                continue
            gate_path = os.path.join(runtime_path, candidate_name)

            def bind_domain() -> _LocalModelGateDirectoryBinding:
                if create:
                    os.mkdir(candidate_name, 0o700, dir_fd=runtime_descriptor)
                    owner.created_domain_name = candidate_name
                gate_descriptor = os.open(
                    candidate_name,
                    os.O_RDONLY | directory | nofollow | cloexec | nonblock,
                    dir_fd=runtime_descriptor,
                )
                if type(gate_descriptor) is not int or gate_descriptor < 0:
                    raise OSError(errno.EBADF, "gate directory unavailable")
                owner.directory_descriptors[1] = gate_descriptor
                descriptor_identity = _local_model_gate_directory_identity(
                    os.fstat(gate_descriptor),
                    uid,
                )
                path_identity = _local_model_gate_directory_identity(
                    os.stat(
                        candidate_name,
                        dir_fd=runtime_descriptor,
                        follow_symlinks=False,
                    ),
                    uid,
                )
                if (
                    descriptor_identity is None
                    or descriptor_identity != path_identity
                    or descriptor_identity[0] != runtime_identity[0]
                    or not _descriptor_is_cloexec(gate_descriptor)
                ):
                    raise OSError(errno.EACCES, "gate directory is unsafe")
                result = _LocalModelGateDirectoryBinding(
                    runtime_path=runtime_path,
                    gate_path=gate_path,
                    gate_name=candidate_name,
                    runtime_identity=runtime_identity,
                    gate_identity=descriptor_identity,
                )
                owner.binding = result
                owner.created_domain_name = None
                return result

            try:
                candidate_binding = _run_with_local_model_gate_signals_blocked(
                    bind_domain
                )
            except FileExistsError:
                if create:
                    continue
                return False
            if type(candidate_binding) is _LocalModelGateDirectoryBinding:
                binding = candidate_binding
                break
            return False
        if binding is None:
            return False
        gate_descriptor = owner.directory_descriptors[1]
        runtime_path_identity = _local_model_gate_runtime_path_identity(
            runtime_path,
            uid,
        )
        gate_path_identity = _local_model_gate_directory_identity(
            os.stat(
                binding.gate_name,
                dir_fd=runtime_descriptor,
                follow_symlinks=False,
            ),
            uid,
        )
        if (
            runtime_path_identity != binding.runtime_identity
            or gate_path_identity != binding.gate_identity
            or not _descriptor_is_cloexec(runtime_descriptor)
            or not _descriptor_is_cloexec(gate_descriptor)
        ):
            return False
        return True
    except (Exception, SystemExit):
        return False


def _local_model_gate_directory_is_stable(
    owner: _LocalModelGateResourceOwner,
) -> bool:
    binding = owner.binding
    descriptors = owner.directory_descriptors
    if (
        type(binding) is not _LocalModelGateDirectoryBinding
        or len(descriptors) != 2
        or any(type(item) is not int or item < 0 for item in descriptors)
    ):
        return False
    try:
        uid = binding.runtime_identity[3]
        return (
            _local_model_gate_directory_identity(
                os.fstat(descriptors[0]),
                uid,
            )
            == binding.runtime_identity
            and _local_model_gate_directory_identity(
                os.fstat(descriptors[1]),
                uid,
            )
            == binding.gate_identity
            and _local_model_gate_directory_identity(
                os.stat(
                    binding.gate_name,
                    dir_fd=descriptors[0],
                    follow_symlinks=False,
                ),
                uid,
            )
            == binding.gate_identity
            and _local_model_gate_runtime_path_identity(
                binding.runtime_path,
                uid,
            )
            == binding.runtime_identity
            and all(_descriptor_is_cloexec(item) for item in descriptors)
        )
    except (Exception, SystemExit):
        return False


def _bind_local_model_gate_endpoint(
    owner: _LocalModelGateResourceOwner,
    endpoint: socket.socket,
    *,
    prefix: str,
    uid: int,
) -> str | None:
    binding = owner.binding
    if (
        type(binding) is not _LocalModelGateDirectoryBinding
        or owner.endpoint is not None
        or endpoint is not owner.listener_owner[0]
        or not _local_model_gate_directory_is_stable(owner)
    ):
        return None
    for _attempt in range(4):
        token = secrets.token_hex(16)
        name = prefix + token + _LOCAL_MODEL_GATE_SOCKET_SUFFIX
        path = os.path.join(binding.gate_path, name)
        if not _valid_local_model_gate_endpoint_path(path, prefix=prefix):
            continue
        owner.endpoint = _LocalModelGateEndpoint(path, name, None)

        def bind_endpoint() -> tuple[int, int, int, int, int]:
            endpoint.bind(path)
            initial_metadata = os.stat(
                name,
                dir_fd=owner.directory_descriptors[1],
                follow_symlinks=False,
            )
            ownership_identity = _local_model_gate_endpoint_ownership_identity(
                initial_metadata,
                uid,
            )
            if ownership_identity is None:
                raise OSError(errno.ESTALE, "gate endpoint changed")
            owner.endpoint = _LocalModelGateEndpoint(
                path,
                name,
                ownership_identity,
            )
            os.chmod(
                name,
                0o600,
                dir_fd=owner.directory_descriptors[1],
                follow_symlinks=False,
            )
            private_metadata = os.stat(
                name,
                dir_fd=owner.directory_descriptors[1],
                follow_symlinks=False,
            )
            private_identity = _local_model_gate_endpoint_identity(
                private_metadata,
                uid,
            )
            if (
                private_identity is None
                or _local_model_gate_endpoint_ownership_identity(
                    private_metadata,
                    uid,
                )
                != ownership_identity
            ):
                raise OSError(errno.ESTALE, "gate endpoint changed")
            return ownership_identity

        try:
            endpoint_identity = _run_with_local_model_gate_signals_blocked(
                bind_endpoint
            )
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                owner.endpoint = None
                continue
            raise
        if (
            type(endpoint_identity) is not tuple
            or len(endpoint_identity) != 5
            or owner.endpoint
            != _LocalModelGateEndpoint(path, name, endpoint_identity)
            or not _local_model_gate_directory_is_stable(owner)
        ):
            return None
        return path
    return None


def _valid_local_model_gate_source(
    source: object,
    owner: _LocalModelGateResourceOwner,
    *,
    prefix: str,
) -> bool:
    binding = owner.binding
    if (
        type(binding) is not _LocalModelGateDirectoryBinding
        or not _valid_local_model_gate_endpoint_path(source, prefix=prefix)
        or os.path.dirname(source) != binding.gate_path
        or not _local_model_gate_directory_is_stable(owner)
    ):
        return False
    try:
        endpoint_identity = _local_model_gate_endpoint_identity(
            os.stat(
                os.path.basename(source),
                dir_fd=owner.directory_descriptors[1],
                follow_symlinks=False,
            ),
            binding.gate_identity[3],
        )
    except (Exception, SystemExit):
        return False
    return endpoint_identity is not None and _local_model_gate_directory_is_stable(
        owner
    )


def _valid_local_model_gate_endpoint_name(value: object, *, prefix: str) -> bool:
    if type(value) is not str:
        return False
    token = value.removeprefix(prefix).removesuffix(
        _LOCAL_MODEL_GATE_SOCKET_SUFFIX
    )
    return (
        value == prefix + token + _LOCAL_MODEL_GATE_SOCKET_SUFFIX
        and _LOCAL_MODEL_GATE_ADDRESS_RE.fullmatch(token) is not None
    )


def _local_model_gate_domain_entries(gate_descriptor: int) -> tuple[str, ...]:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    directory = getattr(os, "O_DIRECTORY", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if (
        type(gate_descriptor) is not int
        or gate_descriptor < 0
        or None in {nofollow, cloexec, directory, nonblock}
    ):
        raise OSError(errno.EBADF, "gate directory unavailable")
    entries: list[str] = []
    scan_descriptor_owner = [-1]
    scanner = None
    primary: BaseException | None = None
    try:
        gate_before = os.fstat(gate_descriptor)
        scan_descriptor = _acquire_local_model_gate_descriptor(
            scan_descriptor_owner,
            0,
            lambda: os.open(
                ".",
                os.O_RDONLY | directory | nofollow | cloexec | nonblock,
                dir_fd=gate_descriptor,
            ),
        )
        scan_before = os.fstat(scan_descriptor)
        before_identity = (
            gate_before.st_dev,
            gate_before.st_ino,
            gate_before.st_mode,
            gate_before.st_uid,
        )
        if (
            not stat.S_ISDIR(gate_before.st_mode)
            or (
                scan_before.st_dev,
                scan_before.st_ino,
                scan_before.st_mode,
                scan_before.st_uid,
            )
            != before_identity
            or not _descriptor_is_cloexec(scan_descriptor)
        ):
            raise OSError(errno.ESTALE, "gate directory changed")
        scanner = os.scandir(scan_descriptor)
        for _index in range(3):
            try:
                entry = next(scanner)
            except StopIteration:
                break
            if type(entry.name) is not str:
                raise OSError(errno.EIO, "invalid gate entry")
            entries.append(entry.name)
        scan_after = os.fstat(scan_descriptor)
        gate_after = os.fstat(gate_descriptor)
        if (
            (
                scan_after.st_dev,
                scan_after.st_ino,
                scan_after.st_mode,
                scan_after.st_uid,
            )
            != before_identity
            or (
                gate_after.st_dev,
                gate_after.st_ino,
                gate_after.st_mode,
                gate_after.st_uid,
            )
            != before_identity
        ):
            raise OSError(errno.ESTALE, "gate directory changed")
    except BaseException as exc:
        primary = exc
    if scanner is not None:
        try:
            scanner.close()
        except BaseException as exc:
            primary = _preferred_gate_exception(primary, exc)
    cleanup_error = _cleanup_local_model_gate_resources(
        (scan_descriptor_owner,),
        (),
    )
    if cleanup_error is not None:
        primary = _preferred_gate_exception(primary, cleanup_error)
    if primary is not None:
        raise primary
    return tuple(entries)


def _local_model_gate_domain_path_state(
    owner: _LocalModelGateResourceOwner,
) -> str:
    binding = owner.binding
    descriptors = owner.directory_descriptors
    if (
        type(binding) is not _LocalModelGateDirectoryBinding
        or len(descriptors) != 2
        or any(type(item) is not int or item < 0 for item in descriptors)
    ):
        return "changed"
    uid = binding.runtime_identity[3]
    try:
        runtime_identity = _local_model_gate_directory_identity(
            os.fstat(descriptors[0]),
            uid,
        )
        gate_metadata = os.fstat(descriptors[1])
        gate_identity = _local_model_gate_directory_identity(gate_metadata, uid)
        runtime_path_identity = _local_model_gate_runtime_path_identity(
            binding.runtime_path,
            uid,
        )
        try:
            path_identity = _local_model_gate_directory_identity(
                os.stat(
                    binding.gate_name,
                    dir_fd=descriptors[0],
                    follow_symlinks=False,
                ),
                uid,
            )
        except FileNotFoundError:
            if (
                runtime_identity == binding.runtime_identity
                and runtime_path_identity == binding.runtime_identity
                and gate_identity == binding.gate_identity
                and gate_metadata.st_nlink == 0
            ):
                return "retired"
            return "changed"
    except (Exception, SystemExit):
        return "changed"
    return (
        "current"
        if runtime_identity == binding.runtime_identity
        and runtime_path_identity == binding.runtime_identity
        and gate_identity == binding.gate_identity
        and path_identity == binding.gate_identity
        and all(_descriptor_is_cloexec(item) for item in descriptors)
        else "changed"
    )


def _local_model_gate_owned_endpoint_is_stable(
    owner: _LocalModelGateResourceOwner,
) -> bool:
    binding = owner.binding
    endpoint = owner.endpoint
    if (
        type(binding) is not _LocalModelGateDirectoryBinding
        or type(endpoint) is not _LocalModelGateEndpoint
        or endpoint.identity is None
        or owner.directory_descriptors[1] < 0
    ):
        return False
    try:
        metadata = os.stat(
            endpoint.name,
            dir_fd=owner.directory_descriptors[1],
            follow_symlinks=False,
        )
    except (Exception, SystemExit):
        return False
    return (
        _local_model_gate_endpoint_identity(
            metadata,
            binding.gate_identity[3],
        )
        is not None
        and _local_model_gate_endpoint_ownership_identity(
            metadata,
            binding.gate_identity[3],
        )
        == endpoint.identity
    )


def _close_local_model_gate_descriptor_slots(
    descriptor_owner: list[int],
    indices: tuple[int, ...],
) -> BaseException | None:
    primary: BaseException | None = None
    closed: set[int] = set()
    for index in indices:
        descriptor = descriptor_owner[index]
        if type(descriptor) is not int or descriptor < 0 or descriptor in closed:
            descriptor_owner[index] = -1
            continue
        closed.add(descriptor)
        for attempt in range(2):
            try:
                os.close(descriptor)
            except BaseException as exc:
                primary = _preferred_gate_exception(primary, exc)
                if isinstance(exc, KeyboardInterrupt) and attempt == 0:
                    continue
                if not isinstance(exc, KeyboardInterrupt):
                    descriptor_owner[index] = -1
                break
            else:
                descriptor_owner[index] = -1
                break
    return primary


def _retire_local_model_gate_domain(
    owner: _LocalModelGateResourceOwner,
    *,
    peer_source: str | None,
) -> bool:
    binding = owner.binding
    endpoint = owner.endpoint
    if (
        type(binding) is not _LocalModelGateDirectoryBinding
        or type(endpoint) is not _LocalModelGateEndpoint
        or not _valid_local_model_gate_endpoint_name(
            endpoint.name,
            prefix=_LOCAL_MODEL_GATE_CONTROLLER_PREFIX,
        )
        or not _valid_local_model_gate_endpoint_path(
            peer_source,
            prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
        )
        or os.path.dirname(peer_source) != binding.gate_path
        or not _local_model_gate_owned_endpoint_is_stable(owner)
        or not _valid_local_model_gate_source(
            peer_source,
            owner,
            prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
        )
    ):
        return False
    peer_name = os.path.basename(peer_source)
    expected_names = {endpoint.name, peer_name}

    def retire() -> bool:
        if (
            _local_model_gate_domain_path_state(owner) != "current"
            or not _local_model_gate_owned_endpoint_is_stable(owner)
            or not _valid_local_model_gate_source(
                peer_source,
                owner,
                prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
            )
            or set(_local_model_gate_domain_entries(
                owner.directory_descriptors[1]
            ))
            != expected_names
        ):
            return False
        owner.peer_endpoint_name = peer_name
        os.unlink(endpoint.name, dir_fd=owner.directory_descriptors[1])
        owner.endpoint = None
        os.unlink(peer_name, dir_fd=owner.directory_descriptors[1])
        owner.peer_endpoint_name = None
        if (
            _local_model_gate_domain_entries(owner.directory_descriptors[1])
            or _local_model_gate_domain_path_state(owner) != "current"
        ):
            return False
        os.rmdir(binding.gate_name, dir_fd=owner.directory_descriptors[0])
        if _local_model_gate_domain_path_state(owner) != "retired":
            return False
        owner.binding = None
        cleanup_error = _close_local_model_gate_descriptor_slots(
            owner.directory_descriptors,
            (1, 0),
        )
        if cleanup_error is not None:
            raise cleanup_error
        return True

    try:
        retired = _run_with_local_model_gate_signals_blocked(retire)
    except (Exception, SystemExit):
        return False
    return retired is True and _local_model_gate_domain_is_retired(owner)


def _close_local_model_gate_resource_owner(
    owner: _LocalModelGateResourceOwner,
) -> BaseException | None:
    primary: BaseException | None = None

    def cleanup() -> None:
        nonlocal primary
        endpoint_socket = owner.listener_owner[0]
        if endpoint_socket is not None:
            for attempt in range(2):
                try:
                    endpoint_socket.close()
                except BaseException as exc:
                    primary = _preferred_gate_exception(primary, exc)
                    if isinstance(exc, KeyboardInterrupt) and attempt == 0:
                        continue
                    if not isinstance(exc, KeyboardInterrupt):
                        owner.listener_owner[0] = None
                    break
                else:
                    owner.listener_owner[0] = None
                    break
        binding = owner.binding
        gate_descriptor = owner.directory_descriptors[1]
        if type(binding) is _LocalModelGateDirectoryBinding and gate_descriptor >= 0:
            try:
                entries = _local_model_gate_domain_entries(gate_descriptor)
            except BaseException as exc:
                entries = ()
                primary = _preferred_gate_exception(primary, exc)
            disposable_names = {
                name
                for name in (
                    (
                        owner.endpoint.name
                        if type(owner.endpoint) is _LocalModelGateEndpoint
                        else None
                    ),
                    owner.peer_endpoint_name,
                )
                if name is not None
            }
            if len(entries) <= 2:
                candidates = [
                    name
                    for name in entries
                    if _valid_local_model_gate_endpoint_name(
                        name,
                        prefix=_LOCAL_MODEL_GATE_CONTROLLER_PREFIX,
                    )
                    or _valid_local_model_gate_endpoint_name(
                        name,
                        prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
                    )
                ]
                if len(candidates) == len(entries):
                    disposable_names.update(candidates)
            for name in disposable_names:
                if name not in entries:
                    if (
                        type(owner.endpoint) is _LocalModelGateEndpoint
                        and owner.endpoint.name == name
                    ):
                        owner.endpoint = None
                    if owner.peer_endpoint_name == name:
                        owner.peer_endpoint_name = None
                    continue
                try:
                    os.unlink(name, dir_fd=gate_descriptor)
                except FileNotFoundError:
                    pass
                except BaseException as exc:
                    primary = _preferred_gate_exception(primary, exc)
                    continue
                if (
                    type(owner.endpoint) is _LocalModelGateEndpoint
                    and owner.endpoint.name == name
                ):
                    owner.endpoint = None
                if owner.peer_endpoint_name == name:
                    owner.peer_endpoint_name = None
            path_state = _local_model_gate_domain_path_state(owner)
            if path_state == "retired":
                owner.binding = None
                owner.endpoint = None
                owner.peer_endpoint_name = None
            elif path_state == "current":
                try:
                    os.rmdir(binding.gate_name, dir_fd=owner.directory_descriptors[0])
                except FileNotFoundError:
                    if _local_model_gate_domain_path_state(owner) == "retired":
                        owner.binding = None
                        owner.endpoint = None
                        owner.peer_endpoint_name = None
                    elif primary is None:
                        primary = OSError(errno.ESTALE, "gate domain changed")
                except BaseException as exc:
                    primary = _preferred_gate_exception(primary, exc)
                else:
                    if _local_model_gate_domain_path_state(owner) == "retired":
                        owner.binding = None
                        owner.endpoint = None
                        owner.peer_endpoint_name = None
                    elif primary is None:
                        primary = OSError(errno.ESTALE, "gate domain changed")
            elif primary is None:
                primary = OSError(errno.ESTALE, "gate domain changed")
        created_name = owner.created_domain_name
        if (
            owner.binding is None
            and created_name is not None
            and owner.directory_descriptors[0] >= 0
        ):
            try:
                os.rmdir(created_name, dir_fd=owner.directory_descriptors[0])
            except BaseException as exc:
                primary = _preferred_gate_exception(primary, exc)
            else:
                owner.created_domain_name = None
        for descriptor_owner, indices in (
            (owner.spawn_pidfd_owner, (0,)),
            (owner.controller_pidfd_owner, (0,)),
        ):
            cleanup_error = _close_local_model_gate_descriptor_slots(
                descriptor_owner,
                indices,
            )
            if cleanup_error is not None:
                primary = _preferred_gate_exception(primary, cleanup_error)
        if owner.binding is None and owner.created_domain_name is None:
            cleanup_error = _close_local_model_gate_descriptor_slots(
                owner.directory_descriptors,
                (1, 0),
            )
            if cleanup_error is not None:
                primary = _preferred_gate_exception(primary, cleanup_error)

    for _attempt in range(2):
        try:
            _run_with_local_model_gate_signals_blocked(cleanup)
        except BaseException as exc:
            primary = _preferred_gate_exception(primary, exc)
            continue
        break
    return primary


def _force_close_local_model_gate_resource_owner(
    owner: _LocalModelGateResourceOwner,
) -> BaseException | None:
    primary = _cleanup_local_model_gate_resources(
        (
            owner.spawn_pidfd_owner,
            owner.controller_pidfd_owner,
            owner.directory_descriptors,
        ),
        (owner.listener_owner,),
    )
    owner.binding = None
    owner.created_domain_name = None
    owner.endpoint = None
    owner.peer_endpoint_name = None
    owner._closed = _local_model_gate_resource_owner_is_empty(owner)
    return primary


def _local_model_gate_resource_owner_is_empty(
    owner: _LocalModelGateResourceOwner,
) -> bool:
    return (
        owner.listener_owner[0] is None
        and owner.binding is None
        and owner.created_domain_name is None
        and owner.endpoint is None
        and owner.peer_endpoint_name is None
        and owner.controller_pidfd_owner == [-1]
        and owner.spawn_pidfd_owner == [-1]
        and owner.directory_descriptors == [-1, -1]
    )


def _local_model_gate_domain_is_retired(
    owner: _LocalModelGateResourceOwner,
) -> bool:
    return (
        owner.binding is None
        and owner.created_domain_name is None
        and owner.endpoint is None
        and owner.peer_endpoint_name is None
        and owner.directory_descriptors == [-1, -1]
    )


def _parse_gate_decimal(value: object, *, allow_zero: bool) -> int | None:
    if (
        type(value) is not str
        or not value
        or len(value) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
        or not value.isascii()
        or not value.isdecimal()
        or (len(value) > 1 and value.startswith("0"))
    ):
        return None
    try:
        parsed = int(value)
    except (OverflowError, ValueError):
        return None
    minimum = 0 if allow_zero else 1
    if not minimum <= parsed <= MAX_PRIORITY_SCOPE_IDENTITY_INTEGER:
        return None
    return parsed


def _local_model_gate_from_environment(
    environment: dict[str, str],
) -> _LocalModelGateConfiguration:
    values = {key: environment.pop(key, None) for key in _LOCAL_MODEL_GATE_ENV_KEYS}
    environment.pop(_SCOPE_EXEC_LATCH_ADDRESS_ENV, None)
    environment.pop(_SCOPE_EXEC_LATCH_NONCE_ENV, None)
    environment.pop(SOC_PRIORITY_SCOPE_MARKER, None)
    address = values[_LOCAL_MODEL_GATE_ADDRESS_ENV]
    ready_nonce = values[_LOCAL_MODEL_GATE_READY_NONCE_ENV]
    ack_nonce = values[_LOCAL_MODEL_GATE_ACK_NONCE_ENV]
    process_id = _parse_gate_decimal(
        values[_LOCAL_MODEL_GATE_CONTROLLER_PID_ENV],
        allow_zero=False,
    )
    uid = _parse_gate_decimal(
        values[_LOCAL_MODEL_GATE_CONTROLLER_UID_ENV],
        allow_zero=True,
    )
    gid = _parse_gate_decimal(
        values[_LOCAL_MODEL_GATE_CONTROLLER_GID_ENV],
        allow_zero=True,
    )
    device = _parse_gate_decimal(
        values[_LOCAL_MODEL_GATE_CONTROLLER_DEVICE_ENV],
        allow_zero=True,
    )
    inode = _parse_gate_decimal(
        values[_LOCAL_MODEL_GATE_CONTROLLER_INODE_ENV],
        allow_zero=False,
    )
    start_time = _parse_gate_decimal(
        values[_LOCAL_MODEL_GATE_CONTROLLER_START_TIME_ENV],
        allow_zero=False,
    )
    deadline_value = values[_LOCAL_MODEL_GATE_DEADLINE_ENV]
    try:
        deadline = (
            float.fromhex(deadline_value)
            if type(deadline_value) is str
            else float("nan")
        )
    except (OverflowError, ValueError):
        deadline = float("nan")
    if (
        not _valid_local_model_gate_endpoint_path(
            address,
            prefix=_LOCAL_MODEL_GATE_CONTROLLER_PREFIX,
        )
        or type(ready_nonce) is not str
        or _LOCAL_MODEL_GATE_NONCE_RE.fullmatch(ready_nonce) is None
        or type(ack_nonce) is not str
        or _LOCAL_MODEL_GATE_NONCE_RE.fullmatch(ack_nonce) is None
        or ready_nonce == ack_nonce
        or process_id is None
        or not _valid_process_id(process_id)
        or uid is None
        or uid > (1 << 32) - 1
        or gid is None
        or gid > (1 << 32) - 1
        or device is None
        or inode is None
        or start_time is None
        or type(deadline_value) is not str
        or not math.isfinite(deadline)
        or deadline.hex() != deadline_value
        or deadline <= time.monotonic()
    ):
        raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
    return _LocalModelGateConfiguration(
        controller_address=address,
        ready_nonce=ready_nonce,
        ack_nonce=ack_nonce,
        controller_uid=uid,
        controller_gid=gid,
        controller_identity=_CanonicalProcessIdentity(
            process_id=process_id,
            directory_device=device,
            directory_inode=inode,
            start_time=start_time,
        ),
        absolute_deadline=deadline,
    )


def _receive_local_model_gate_datagram_before_deadline(
    receiver: socket.socket,
    *,
    max_bytes: int,
    absolute_deadline: float,
) -> _LocalModelGateDatagram:
    interruptions = 0
    while True:
        remaining = absolute_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            return _receive_local_model_gate_datagram(
                receiver,
                max_bytes=max_bytes,
            )
        except BlockingIOError as exc:
            if exc.errno not in {errno.EAGAIN, errno.EWOULDBLOCK}:
                raise
        remaining = absolute_deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        try:
            readable, _, _ = select.select((receiver,), (), (), remaining)
        except InterruptedError:
            interruptions += 1
            if interruptions > 128:
                raise TimeoutError from None
            continue
        if not readable:
            raise TimeoutError


def _wait_for_local_model_gate_ack(
    configuration: _LocalModelGateConfiguration,
    *,
    controller_pidfd_owner: list[int] | None = None,
) -> None:
    if type(configuration) is not _LocalModelGateConfiguration:
        raise PriorityScopeError(_SCOPE_EXEC_FAILURE)
    owns_controller_pidfd = controller_pidfd_owner is None
    pidfd_owner = [-1] if controller_pidfd_owner is None else controller_pidfd_owner
    if type(pidfd_owner) is not list or len(pidfd_owner) != 1 or pidfd_owner[0] != -1:
        raise PriorityScopeError(_SCOPE_EXEC_FAILURE)
    resources = _LocalModelGateResourceOwner()
    success = False
    primary: BaseException | None = None
    try:
        if (
            os.getuid() != configuration.controller_uid
            or os.getgid() != configuration.controller_gid
            or not _open_identity_pidfd(
                configuration.controller_identity,
                pidfd_owner,
            )
            or not _identity_pidfd_is_live(
                configuration.controller_identity,
                pidfd_owner[0],
            )
        ):
            raise OSError(errno.ESRCH, "controller identity changed")
        handshake_deadline = min(
            configuration.absolute_deadline,
            time.monotonic() + _LOCAL_MODEL_GATE_ACK_TIMEOUT_SECONDS,
        )
        if time.monotonic() >= handshake_deadline:
            raise TimeoutError
        controller_domain = os.path.dirname(configuration.controller_address)
        controller_domain_name = os.path.basename(controller_domain)
        if not _open_local_model_gate_directory(
            resources,
            uid=configuration.controller_uid,
            create=False,
            gate_name=controller_domain_name,
        ):
            raise OSError(errno.EACCES, "gate directory unavailable")
        binding = resources.binding
        if (
            type(binding) is not _LocalModelGateDirectoryBinding
            or controller_domain != binding.gate_path
            or not _valid_local_model_gate_source(
                configuration.controller_address,
                resources,
                prefix=_LOCAL_MODEL_GATE_CONTROLLER_PREFIX,
            )
        ):
            raise OSError(errno.EACCES, "gate address is outside runtime")
        resources.peer_endpoint_name = os.path.basename(
            configuration.controller_address
        )

        def acquire_sender() -> socket.socket:
            endpoint = socket.socket(
                socket.AF_UNIX,
                socket.SOCK_DGRAM | socket.SOCK_CLOEXEC,
            )
            resources.listener_owner[0] = endpoint
            return endpoint

        sender = _run_with_local_model_gate_signals_blocked(acquire_sender)
        if sender is None or sender is not resources.listener_owner[0]:
            raise OSError(errno.EBADF, "gate socket unavailable")
        sender.setblocking(False)
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sender.setsockopt(socket.SOL_SOCKET, _SO_PASSPIDFD, 1)
        if not _descriptor_is_cloexec(sender.fileno()):
            raise OSError(errno.ENOTSUP, "descriptor flags unavailable")
        own_address = _bind_local_model_gate_endpoint(
            resources,
            sender,
            prefix=_LOCAL_MODEL_GATE_WRAPPER_PREFIX,
            uid=configuration.controller_uid,
        )
        if own_address is None:
            raise OSError(errno.EADDRINUSE, "gate address unavailable")
        sender.connect(configuration.controller_address)
        if not _local_model_gate_directory_is_stable(resources):
            raise OSError(errno.ESTALE, "gate directory changed")
        if time.monotonic() >= handshake_deadline:
            raise TimeoutError
        ready = _LOCAL_MODEL_GATE_READY_PREFIX + configuration.ready_nonce.encode(
            "ascii"
        )
        if sender.send(ready) != len(ready):
            raise OSError(errno.EIO, "gate datagram was incomplete")
        datagram = _receive_local_model_gate_datagram_before_deadline(
            sender,
            max_bytes=_LOCAL_MODEL_GATE_ACK_MAX_BYTES,
            absolute_deadline=handshake_deadline,
        )
        expected_credentials = (
            configuration.controller_identity.process_id,
            configuration.controller_uid,
            configuration.controller_gid,
        )
        success = (
            datagram.message
            == _LOCAL_MODEL_GATE_ACK_PREFIX + configuration.ack_nonce.encode("ascii")
            and datagram.flags == socket.MSG_CMSG_CLOEXEC
            and datagram.credentials == (expected_credentials,)
            and datagram.pidfd_process_ids
            == (configuration.controller_identity.process_id,)
            and not datagram.unknown_ancillary
            and datagram.source == configuration.controller_address
            and _local_model_gate_domain_path_state(resources) == "retired"
            and time.monotonic() < handshake_deadline
        )
        if success:
            for _drain in range(_LOCAL_MODEL_GATE_RECEIVE_BUDGET):
                try:
                    duplicate = _receive_local_model_gate_datagram(
                        sender,
                        max_bytes=_LOCAL_MODEL_GATE_ACK_MAX_BYTES,
                    )
                except BlockingIOError:
                    break
                if _local_model_gate_datagram_is_attributable(
                    duplicate,
                    configuration.controller_identity.process_id,
                ):
                    success = False
                    break
            if not _identity_pidfd_is_live(
                configuration.controller_identity,
                pidfd_owner[0],
            ) or _local_model_gate_domain_path_state(
                resources
            ) != "retired" or time.monotonic() >= handshake_deadline:
                success = False
    except BaseException as exc:
        primary = exc
        success = False
    cleanup_error = resources.close()
    if cleanup_error is not None:
        primary = _preferred_gate_exception(primary, cleanup_error)
        success = False
    if not success or owns_controller_pidfd:
        cleanup_error = _cleanup_local_model_gate_resources((pidfd_owner,), ())
        if cleanup_error is not None:
            primary = _preferred_gate_exception(primary, cleanup_error)
            success = False
    if isinstance(primary, KeyboardInterrupt):
        raise primary
    if primary is not None or not success:
        raise PriorityScopeError(_SCOPE_EXEC_FAILURE) from None


def _run_scope_exec_wrapper(argv: Sequence[str]) -> None:
    if isinstance(argv, (str, bytes)):
        raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
    try:
        arguments = list(argv)
    except (TypeError, ValueError):
        raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE) from None
    if not arguments or arguments[0] != _SCOPE_EXEC_WRAPPER_TOKEN:
        raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
    latch_required = (
        len(arguments) > 1
        and arguments[1] == _SCOPE_EXEC_LATCH_REQUIRED_TOKEN
    )
    local_model_gate_required = (
        len(arguments) > 1 and arguments[1] == _LOCAL_MODEL_GATE_REQUIRED_TOKEN
    )
    environment = os.environ.copy()
    controller_pidfd_owner = [-1]
    controller_identity: _CanonicalProcessIdentity | None = None
    descriptor = -1
    primary: BaseException | None = None
    try:
        if local_model_gate_required:
            configuration = _local_model_gate_from_environment(environment)
            controller_identity = configuration.controller_identity
            _wait_for_local_model_gate_ack(
                configuration,
                controller_pidfd_owner=controller_pidfd_owner,
            )
        else:
            latch = _scope_exec_latch_from_environment(
                environment,
                required=latch_required,
            )
            if latch is not None:
                _send_scope_exec_entered_latch(*latch)
        if latch_required and environment.get(SOC_PRIORITY_SCOPE_MARKER) != "1":
            raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
        spec = _scope_exec_launch_spec_from_arguments(arguments)
        if spec is None:
            raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
        try:
            _normalize_cpu_affinity_for_scope_exec()
        except _ScopeExecAffinityError as exc:
            failure = _SCOPE_EXEC_AFFINITY_PHASE_FAILURES.get(
                exc.phase, _SCOPE_EXEC_AFFINITY_FAILURE
            )
            raise PriorityScopeError(failure) from None
        except PriorityScopeError:
            pass
        if not _SCOPE_EXEC_FD_EXEC_SUPPORTED:
            raise PriorityScopeError(_SCOPE_EXEC_EXEC_FAILURE)
        opened = _open_scope_exec_launch_fd(spec)
        if opened is None:
            raise PriorityScopeError(_SCOPE_EXEC_ARGUMENTS_FAILURE)
        descriptor = opened
        if local_model_gate_required:
            if (
                type(controller_identity) is not _CanonicalProcessIdentity
                or not _identity_pidfd_is_live(
                    controller_identity,
                    controller_pidfd_owner[0],
                )
            ):
                raise PriorityScopeError(_SCOPE_EXEC_FAILURE)
        try:
            os.execve(descriptor, list(spec.argv), environment)
        except (OSError, OverflowError, TypeError, ValueError):
            raise PriorityScopeError(_SCOPE_EXEC_EXEC_FAILURE) from None
        raise PriorityScopeError(_SCOPE_EXEC_EXEC_FAILURE)
    except BaseException as exc:
        primary = exc
    cleanup_error = _cleanup_local_model_gate_resources(
        (controller_pidfd_owner,),
        (),
    )
    if cleanup_error is not None:
        primary = _preferred_gate_exception(primary, cleanup_error)
    if descriptor >= 0:
        try:
            os.close(descriptor)
        except OSError:
            pass
    if primary is not None:
        raise primary
    raise PriorityScopeError(_SCOPE_EXEC_FAILURE)


def _run_local_model_direct_exec_wrapper(
    argv: Sequence[str],
    *,
    absolute_deadline: float | None = None,
) -> None:
    spec = _local_model_direct_launch_spec_from_arguments(argv)
    environment = os.environ.copy()
    if (
        spec is None
        or any(key in environment for key in _LOCAL_MODEL_DIRECT_FORBIDDEN_ENV_KEYS)
        or any(
            key.startswith(_LOCAL_MODEL_DIRECT_FORBIDDEN_ENV_PREFIXES)
            for key in environment
        )
    ):
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)

    if absolute_deadline is not None and (
        type(absolute_deadline) is not float
        or not math.isfinite(absolute_deadline)
        or time.monotonic() >= absolute_deadline
    ):
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
    cpu_priority = _current_cpu_priority()
    io_priority = _current_io_priority(absolute_deadline=absolute_deadline)
    if (
        type(cpu_priority) is not int
        or cpu_priority < LOCAL_MODEL_CPU_NICE
        or io_priority
        != (IO_PRIORITY_CLASS, int(LOCAL_MODEL_IO_PRIORITY_LEVEL))
        or not _SCOPE_EXEC_FD_EXEC_SUPPORTED
        or (
            absolute_deadline is not None
            and time.monotonic() >= absolute_deadline
        )
    ):
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)

    descriptor = _open_scope_exec_launch_fd(spec)
    if descriptor is None:
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
    try:
        if not _descriptor_is_cloexec(descriptor):
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        if (
            absolute_deadline is not None
            and time.monotonic() >= absolute_deadline
        ):
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        try:
            os.execve(descriptor, list(spec.argv), environment)
        except (OSError, OverflowError, TypeError, ValueError):
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE) from None
        raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


class _LocalModelDirectSockFilter(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("value", ctypes.c_uint32),
    )


class _LocalModelDirectSockFprog(ctypes.Structure):
    _fields_ = (
        ("length", ctypes.c_ushort),
        ("filters", ctypes.POINTER(_LocalModelDirectSockFilter)),
    )


def _local_model_direct_prctl(
    option: int,
    argument2: object = 0,
    argument3: object = 0,
) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.prctl(option, argument2, argument3, 0, 0)
    if result < 0:
        error_number = ctypes.get_errno() or errno.EPERM
        raise OSError(error_number, os.strerror(error_number))
    return result


def _set_local_model_direct_subreaper() -> bool:
    current = ctypes.c_int(0)
    try:
        _local_model_direct_prctl(_PR_SET_CHILD_SUBREAPER, 1)
        _local_model_direct_prctl(
            _PR_GET_CHILD_SUBREAPER,
            ctypes.byref(current),
        )
    except (OSError, TypeError, ValueError):
        return False
    return current.value == 1


def _harden_local_model_direct_supervisor() -> bool:
    try:
        _local_model_direct_prctl(_PR_SET_DUMPABLE, 0)
        return _local_model_direct_prctl(_PR_GET_DUMPABLE) == 0
    except (OSError, TypeError, ValueError):
        return False


def _local_model_direct_cap_sys_nice_is_absent() -> bool:
    contents = _read_affinity_file(
        _PROC_ROOT / "self" / "status",
        max_bytes=64 * 1024,
    )
    if contents is None:
        return False
    values: dict[str, int] = {}
    status_uids: tuple[int, int, int, int] | None = None
    for line in contents.splitlines():
        name, separator, value = line.partition(":")
        if name == "Uid":
            if not separator or status_uids is not None:
                return False
            try:
                parsed_uids = tuple(int(item, 10) for item in value.split())
            except (OverflowError, ValueError):
                return False
            if len(parsed_uids) != 4:
                return False
            status_uids = parsed_uids
            continue
        if name not in {"CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"}:
            continue
        if not separator or name in values:
            return False
        try:
            values[name] = int(value.strip(), 16)
        except (OverflowError, ValueError):
            return False
    if status_uids is None:
        return False
    real_uid, effective_uid, saved_uid, filesystem_uid = status_uids
    return bool(
        real_uid != 0
        and real_uid == effective_uid == saved_uid == filesystem_uid
        and os.getuid() == real_uid
        and os.geteuid() == effective_uid
        and len(values) == 5
        and values["CapEff"] == 0
        and values["CapPrm"] == 0
        and values["CapAmb"] == 0
    )


def _install_local_model_direct_seccomp(
    supervisor_pid: int,
    controller_pid: int,
    protected_process_groups: tuple[int, ...],
) -> bool:
    if (
        sys.platform != "linux"
        or os.uname().machine != "x86_64"
        or sys.byteorder != "little"
        or ctypes.sizeof(ctypes.c_void_p) != 8
        or not _valid_process_id(supervisor_pid)
        or not _valid_process_id(controller_pid)
        or not protected_process_groups
        or any(
            not _valid_process_id(process_group)
            for process_group in protected_process_groups
        )
    ):
        return False
    protected_pids = tuple(dict.fromkeys((supervisor_pid, controller_pid)))
    protected_groups = tuple(dict.fromkeys(protected_process_groups))

    def statement(code: int, value: int) -> _LocalModelDirectSockFilter:
        return _LocalModelDirectSockFilter(code, 0, 0, value)

    def jump(
        code: int,
        value: int,
        yes: int,
        no: int,
    ) -> _LocalModelDirectSockFilter:
        return _LocalModelDirectSockFilter(code, yes, no, value)

    deny = statement(_BPF_RET_K, _SECCOMP_RET_ERRNO | errno.EPERM)
    instruction_list = [
        statement(_BPF_LD_W_ABS, 4),
        jump(_BPF_JMP_JEQ_K, _AUDIT_ARCH_X86_64, 1, 0),
        statement(_BPF_RET_K, _SECCOMP_RET_KILL_PROCESS),
        statement(_BPF_LD_W_ABS, 0),
    ]

    def syscall_numbers(number: int) -> tuple[int, int]:
        return number, _X32_SYSCALL_BIT | number

    def deny_syscall(numbers: tuple[int, ...]) -> None:
        for number in numbers:
            instruction_list.extend(
                (jump(_BPF_JMP_JEQ_K, number, 0, 1), deny)
            )

    def deny_syscall_argument(
        numbers: tuple[int, ...],
        argument: int,
        blocked_values: tuple[int, ...],
    ) -> None:
        for number in numbers:
            block = [statement(_BPF_LD_W_ABS, 16 + (argument * 8))]
            for value in blocked_values:
                block.extend(
                    (
                        jump(_BPF_JMP_JEQ_K, value & 0xFFFFFFFF, 0, 1),
                        deny,
                    )
                )
            block.append(statement(_BPF_LD_W_ABS, 0))
            instruction_list.append(
                jump(_BPF_JMP_JEQ_K, number, 0, len(block))
            )
            instruction_list.extend(block)

    def deny_syscall_nonzero_argument(
        numbers: tuple[int, ...],
        argument: int,
    ) -> None:
        for number in numbers:
            block = [
                statement(_BPF_LD_W_ABS, 16 + (argument * 8)),
                jump(_BPF_JMP_JEQ_K, 0, 1, 0),
                deny,
                statement(_BPF_LD_W_ABS, 0),
            ]
            instruction_list.append(
                jump(_BPF_JMP_JEQ_K, number, 0, len(block))
            )
            instruction_list.extend(block)

    deny_syscall(syscall_numbers(_X86_64_IOPRIO_SET))
    deny_syscall(syscall_numbers(_X86_64_PIDFD_SEND_SIGNAL))
    deny_syscall_argument(
        syscall_numbers(_X86_64_PRCTL),
        0,
        (_PR_SET_PDEATHSIG,),
    )
    deny_syscall_argument(
        syscall_numbers(_X86_64_KILL),
        0,
        (*protected_pids, *(-group for group in protected_groups), -1),
    )
    # tkill carries no thread-group ID. Denying this obsolete primitive is the
    # only argument-safe way to protect every controller thread; tgkill remains
    # available inside the model hierarchy.
    deny_syscall(syscall_numbers(_X86_64_TKILL))
    deny_syscall_argument(
        (
            *syscall_numbers(_X86_64_RT_SIGQUEUEINFO),
            _X32_SYSCALL_BIT | _X32_RT_SIGQUEUEINFO,
        ),
        0,
        protected_pids,
    )
    for numbers in (
        syscall_numbers(_X86_64_TGKILL),
        (
            *syscall_numbers(_X86_64_RT_TGSIGQUEUEINFO),
            _X32_SYSCALL_BIT | _X32_RT_TGSIGQUEUEINFO,
        ),
    ):
        deny_syscall_argument(numbers, 0, protected_pids)
        deny_syscall_argument(numbers, 1, protected_pids)
    deny_syscall_argument(
        syscall_numbers(_X86_64_PIDFD_OPEN),
        0,
        protected_pids,
    )
    # pid=0 remains available for libc/resource self-management. Any explicit
    # PID could name a controller thread sharing its process-wide limits.
    deny_syscall_nonzero_argument(syscall_numbers(_X86_64_PRLIMIT64), 0)
    deny_syscall_argument(
        syscall_numbers(_X86_64_SETPGID),
        1,
        protected_groups,
    )
    instruction_list.append(statement(_BPF_RET_K, _SECCOMP_RET_ALLOW))
    if len(instruction_list) > 4096:
        return False
    filters = (_LocalModelDirectSockFilter * len(instruction_list))(
        *instruction_list
    )
    program = _LocalModelDirectSockFprog(
        len(filters),
        ctypes.cast(filters, ctypes.POINTER(_LocalModelDirectSockFilter)),
    )
    try:
        _local_model_direct_prctl(_PR_SET_NO_NEW_PRIVS, 1)
        if _local_model_direct_prctl(_PR_GET_NO_NEW_PRIVS) != 1:
            return False
        _local_model_direct_prctl(
            _PR_SET_SECCOMP,
            _SECCOMP_MODE_FILTER,
            ctypes.byref(program),
        )
    except (OSError, TypeError, ValueError):
        return False
    contents = _read_affinity_file(
        _PROC_ROOT / "self" / "status",
        max_bytes=64 * 1024,
    )
    return bool(
        contents is not None
        and re.search(r"(?m)^NoNewPrivs:\s+1\s*$", contents)
        and re.search(r"(?m)^Seccomp:\s+2\s*$", contents)
    )


def _arm_local_model_direct_target_security(
    parent_pid: int,
    controller_pid: int,
    protected_process_groups: tuple[int, ...],
) -> bool:
    pdeath_signal = ctypes.c_int(0)
    try:
        _local_model_direct_prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
        _local_model_direct_prctl(
            _PR_GET_PDEATHSIG,
            ctypes.byref(pdeath_signal),
        )
        if os.getppid() != parent_pid or pdeath_signal.value != signal.SIGKILL:
            return False
        if (
            not hasattr(resource, "RLIMIT_NICE")
            or not hasattr(resource, "RLIMIT_RTPRIO")
            or not hasattr(resource, "RLIMIT_NPROC")
        ):
            return False
        resource.setrlimit(resource.RLIMIT_NICE, (0, 0))
        resource.setrlimit(resource.RLIMIT_RTPRIO, (0, 0))
        _, nproc_hard = resource.getrlimit(resource.RLIMIT_NPROC)
        nproc_limit = (
            _LOCAL_MODEL_DIRECT_NPROC_LIMIT
            if nproc_hard == resource.RLIM_INFINITY
            else min(nproc_hard, _LOCAL_MODEL_DIRECT_NPROC_LIMIT)
        )
        if type(nproc_limit) is not int or nproc_limit <= 0:
            return False
        resource.setrlimit(resource.RLIMIT_NPROC, (nproc_limit, nproc_limit))
        if (
            resource.getrlimit(resource.RLIMIT_NICE) != (0, 0)
            or resource.getrlimit(resource.RLIMIT_RTPRIO) != (0, 0)
            or resource.getrlimit(resource.RLIMIT_NPROC)
            != (nproc_limit, nproc_limit)
            or not _local_model_direct_cap_sys_nice_is_absent()
        ):
            return False
        return _install_local_model_direct_seccomp(
            parent_pid,
            controller_pid,
            protected_process_groups,
        )
    except (OSError, OverflowError, TypeError, ValueError):
        return False


def _local_model_direct_children_batch() -> tuple[set[int], bool] | None:
    path = _PROC_ROOT / "self" / "task" / str(os.getpid()) / "children"
    contents = _read_affinity_file(
        path,
        max_bytes=_LOCAL_MODEL_DIRECT_CHILDREN_MAX_BYTES,
    )
    if contents is None:
        return None
    fields = contents.split()
    if _LOCAL_MODEL_DIRECT_MAX_CHILDREN <= 0:
        return None
    children: list[int] = []
    seen: set[int] = set()
    for field in fields:
        if (
            not field.isascii()
            or not field.isdecimal()
            or (len(field) > 1 and field.startswith("0"))
        ):
            return None
        try:
            process_id = int(field)
        except (OverflowError, ValueError):
            return None
        if not _valid_process_id(process_id) or process_id in seen:
            return None
        seen.add(process_id)
        children.append(process_id)
    complete = len(children) <= _LOCAL_MODEL_DIRECT_MAX_CHILDREN
    return set(children[:_LOCAL_MODEL_DIRECT_MAX_CHILDREN]), complete


def _local_model_direct_children() -> set[int] | None:
    result = _local_model_direct_children_batch()
    if result is None or not result[1]:
        return None
    return result[0]


def _local_model_direct_signal_pidfd(
    descriptor: int,
    signal_number: int,
) -> bool:
    sender = getattr(signal, "pidfd_send_signal", None)
    if not callable(sender) or _pidfd_process_id(descriptor) is None:
        return False
    try:
        sender(descriptor, signal_number, None, 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    return True


def _local_model_direct_signal_identity(
    identity: _CanonicalProcessIdentity,
    signal_number: int,
) -> bool:
    owner = [-1]
    if not _open_identity_pidfd(identity, owner):
        return _canonical_process_identity(identity.process_id) is None
    try:
        return _local_model_direct_signal_pidfd(owner[0], signal_number)
    finally:
        try:
            os.close(owner[0])
        except OSError:
            pass


def _local_model_direct_reap() -> tuple[dict[int, int], bool]:
    statuses: dict[int, int] = {}
    while True:
        try:
            process_id, wait_status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return statuses, True
        except InterruptedError:
            continue
        except OSError:
            return statuses, False
        if process_id == 0:
            return statuses, False
        statuses[process_id] = wait_status


def _drain_local_model_direct_children(
    root_pid: int,
    root_pidfd: int,
    root_wait_status: int | None,
) -> tuple[int | None, bool]:
    cleanup_ok = True
    phase_deadlines = (
        (
            signal.SIGTERM,
            time.monotonic() + _LOCAL_MODEL_DIRECT_CLEANUP_GRACE_SECONDS,
        ),
        (
            signal.SIGKILL,
            time.monotonic() + _LOCAL_MODEL_DIRECT_CLEANUP_TIMEOUT_SECONDS,
        ),
    )
    for signal_number, phase_deadline in phase_deadlines:
        while time.monotonic() < phase_deadline:
            statuses, no_children = _local_model_direct_reap()
            if root_pid in statuses:
                root_wait_status = statuses[root_pid]
            child_batch = _local_model_direct_children_batch()
            if child_batch is None:
                cleanup_ok = False
                children = set()
                complete = False
            else:
                children, complete = child_batch
            if no_children and complete and not children:
                return root_wait_status, cleanup_ok
            for process_id in children:
                if process_id == root_pid and root_pidfd >= 0:
                    signalled = _local_model_direct_signal_pidfd(
                        root_pidfd,
                        signal_number,
                    )
                else:
                    identity = _canonical_process_identity(process_id)
                    signalled = bool(
                        identity is not None
                        and _local_model_direct_signal_identity(
                            identity,
                            signal_number,
                        )
                    )
                cleanup_ok = cleanup_ok and signalled
            time.sleep(_SCOPE_EXEC_POLL_SECONDS)
    statuses, no_children = _local_model_direct_reap()
    if root_pid in statuses:
        root_wait_status = statuses[root_pid]
    child_batch = _local_model_direct_children_batch()
    return root_wait_status, bool(
        cleanup_ok
        and no_children
        and child_batch is not None
        and child_batch == (set(), True)
    )


def _take_local_model_direct_supervisor_configuration(
) -> tuple[int, float, int] | None:
    descriptor_value = os.environ.pop(_LOCAL_MODEL_DIRECT_STATUS_FD_ENV, None)
    deadline_value = os.environ.pop(_LOCAL_MODEL_DIRECT_DEADLINE_ENV, None)
    controller_value = os.environ.pop(
        _LOCAL_MODEL_DIRECT_CONTROLLER_PID_ENV,
        None,
    )
    if (
        descriptor_value is None
        or deadline_value is None
        or controller_value is None
        or not descriptor_value.isascii()
        or not descriptor_value.isdecimal()
        or not controller_value.isascii()
        or not controller_value.isdecimal()
        or (len(descriptor_value) > 1 and descriptor_value.startswith("0"))
        or (len(controller_value) > 1 and controller_value.startswith("0"))
    ):
        return None
    try:
        descriptor = int(descriptor_value)
        deadline = float(deadline_value)
        controller_pid = int(controller_value)
        descriptor_stat = os.fstat(descriptor)
        status_flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
        descriptor_flags = fcntl.fcntl(descriptor, fcntl.F_GETFD)
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    if (
        descriptor < 3
        or not math.isfinite(deadline)
        or deadline <= 0.0
        or not _valid_process_id(controller_pid)
        or os.getppid() != controller_pid
        or not stat.S_ISFIFO(descriptor_stat.st_mode)
        or descriptor_stat.st_uid != os.getuid()
        or descriptor_stat.st_nlink != 1
        or status_flags & os.O_ACCMODE != os.O_WRONLY
    ):
        return None
    try:
        fcntl.fcntl(
            descriptor,
            fcntl.F_SETFD,
            descriptor_flags | fcntl.FD_CLOEXEC,
        )
    except OSError:
        return None
    return descriptor, deadline, controller_pid


def _bind_local_model_direct_controller(
    controller_pid: int,
) -> tuple[_CanonicalProcessIdentity, int, int] | None:
    descriptor = -1
    pdeath_signal = ctypes.c_int(0)
    try:
        if os.getppid() != controller_pid:
            return None
        identity = _canonical_process_identity(controller_pid)
        owner = [-1]
        if identity is None or not _open_identity_pidfd(identity, owner):
            return None
        descriptor = owner[0]
        _local_model_direct_prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
        _local_model_direct_prctl(
            _PR_GET_PDEATHSIG,
            ctypes.byref(pdeath_signal),
        )
        process_group = os.getpgid(controller_pid)
        if (
            pdeath_signal.value != signal.SIGKILL
            or os.getppid() != controller_pid
            or _pidfd_process_id(descriptor) != controller_pid
            or _canonical_process_identity(controller_pid) != identity
            or not _valid_process_id(process_group)
        ):
            return None
        result = identity, descriptor, process_group
        descriptor = -1
        return result
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _write_local_model_direct_status(
    descriptor: int,
    outcome: int,
    flags: int,
    root_pid: int,
    wait_status: int,
    root_start: int,
) -> bool:
    try:
        payload = _LOCAL_MODEL_DIRECT_STATUS_STRUCT.pack(
            _LOCAL_MODEL_DIRECT_STATUS_MAGIC,
            _LOCAL_MODEL_DIRECT_STATUS_VERSION,
            outcome,
            flags,
            root_pid,
            wait_status,
            root_start,
        )
        while True:
            try:
                return os.write(descriptor, payload) == len(payload)
            except InterruptedError:
                continue
    except (OSError, OverflowError, struct.error, TypeError, ValueError):
        return False


def _run_local_model_direct_supervisor(argv: Sequence[str]) -> None:
    configuration = _take_local_model_direct_supervisor_configuration()
    if configuration is None:
        os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
    status_descriptor, absolute_deadline, controller_pid = configuration
    flags = 0
    outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
    root_pid = 0
    root_start = 0
    root_pidfd = -1
    controller_pidfd = -1
    controller_process_group = 0
    controller_identity: _CanonicalProcessIdentity | None = None
    root_wait_status: int | None = None
    received_signal = 0
    gate_read = gate_write = ready_read = ready_write = -1

    def latch_signal(signal_number: int, _frame: object) -> None:
        nonlocal received_signal
        if received_signal == 0:
            received_signal = signal_number

    try:
        if (
            sys.platform != "linux"
            or os.uname().machine != "x86_64"
            or not callable(getattr(os, "pidfd_open", None))
            or not callable(getattr(signal, "pidfd_send_signal", None))
            or not callable(getattr(signal, "pthread_sigmask", None))
        ):
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        controller_binding = _bind_local_model_direct_controller(controller_pid)
        if controller_binding is None:
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        (
            controller_identity,
            controller_pidfd,
            controller_process_group,
        ) = controller_binding
        if (
            not _harden_local_model_direct_supervisor()
            or not _set_local_model_direct_subreaper()
        ):
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        flags |= (
            _LOCAL_MODEL_DIRECT_FLAG_SUBREAPER
            | _LOCAL_MODEL_DIRECT_FLAG_SUPERVISOR_HARDENED
            | _LOCAL_MODEL_DIRECT_FLAG_CONTROLLER_BOUND
        )
        if os.getpgrp() != os.getpid():
            os.setpgid(0, 0)
        if os.getpgrp() != os.getpid():
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        _, no_children = _local_model_direct_reap()
        if not no_children or _local_model_direct_children() != set():
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        for signal_number in _SCOPE_EXEC_FORWARDED_SIGNALS:
            signal.signal(
                signal_number,
                latch_signal,
            )
        if time.monotonic() >= absolute_deadline:
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE
            flags |= _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
            raise TimeoutError
        pipe_flags = getattr(os, "O_CLOEXEC", 0)
        gate_read, gate_write = os.pipe2(pipe_flags)
        ready_read, ready_write = os.pipe2(pipe_flags)
        if time.monotonic() >= absolute_deadline:
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE
            flags |= _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
            raise TimeoutError
        root_pid = os.fork()
        if root_pid == 0:
            try:
                os.close(gate_write)
                os.close(ready_read)
                os.close(status_descriptor)
                for signal_number in _SCOPE_EXEC_FORWARDED_SIGNALS:
                    signal.signal(signal_number, signal.SIG_DFL)
                parent_pid = os.getppid()
                _local_model_direct_prctl(_PR_SET_PDEATHSIG, signal.SIGKILL)
                if os.getppid() != parent_pid:
                    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
                os.setpgid(0, 0)
                if (
                    os.getpgrp() != os.getpid()
                    or os.getpgrp() == os.getpgid(parent_pid)
                    or os.write(ready_write, b"P") != 1
                ):
                    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
                if os.read(gate_read, 2) != b"G":
                    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
                os.close(gate_read)
                if not _arm_local_model_direct_target_security(
                    parent_pid,
                    controller_pid,
                    (parent_pid, controller_process_group),
                ):
                    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
                if os.write(ready_write, b"A") != 1:
                    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
                os.close(ready_write)
                if time.monotonic() >= absolute_deadline:
                    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
                _run_local_model_direct_exec_wrapper(
                    argv,
                    absolute_deadline=absolute_deadline,
                )
            except BaseException:
                os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
            os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)

        os.close(gate_read)
        gate_read = -1
        os.close(ready_write)
        ready_write = -1
        identity: _CanonicalProcessIdentity | None = None
        while time.monotonic() < absolute_deadline and identity is None:
            identity = _canonical_process_identity(root_pid)
            if identity is None:
                time.sleep(0.001)
        owner = [-1]
        if identity is None or not _open_identity_pidfd(identity, owner):
            raise PriorityScopeError(_LOCAL_MODEL_DIRECT_EXEC_FAILURE)
        root_pidfd = owner[0]
        root_start = identity.start_time
        flags |= _LOCAL_MODEL_DIRECT_FLAG_ROOT_BOUND
        group_ready = False
        while time.monotonic() < absolute_deadline:
            if received_signal:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL
                break
            process_id, wait_status = os.waitpid(root_pid, os.WNOHANG)
            if process_id == root_pid:
                root_wait_status = wait_status
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
                break
            readable, _, _ = select.select(
                [ready_read],
                [],
                [],
                min(
                    _SCOPE_EXEC_POLL_SECONDS,
                    absolute_deadline - time.monotonic(),
                ),
            )
            if readable:
                if (
                    os.read(ready_read, 1) != b"P"
                    or os.getpgid(root_pid) != root_pid
                    or os.getpgid(root_pid) == os.getpgrp()
                ):
                    outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
                    break
                flags |= _LOCAL_MODEL_DIRECT_FLAG_GROUP_SEPARATED
                group_ready = True
                break
        if not group_ready:
            if received_signal:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL
            elif time.monotonic() >= absolute_deadline:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE
        elif time.monotonic() >= absolute_deadline:
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE
        elif (
            controller_identity is None
            or os.getppid() != controller_pid
            or _pidfd_process_id(controller_pidfd) != controller_pid
            or _canonical_process_identity(controller_pid) != controller_identity
        ):
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
        elif os.write(gate_write, b"G") != 1:
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
        else:
            os.close(gate_write)
            gate_write = -1
            while True:
                if received_signal:
                    outcome = _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL
                    break
                if time.monotonic() >= absolute_deadline:
                    outcome = _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE
                    break
                process_id, wait_status = os.waitpid(root_pid, os.WNOHANG)
                if process_id == root_pid:
                    root_wait_status = wait_status
                    outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
                    break
                readable, _, _ = select.select(
                    [ready_read],
                    [],
                    [],
                    min(
                        _SCOPE_EXEC_POLL_SECONDS,
                        absolute_deadline - time.monotonic(),
                    ),
                )
                if readable:
                    if os.read(ready_read, 2) != b"A":
                        outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
                        break
                    flags |= _LOCAL_MODEL_DIRECT_FLAG_SECURITY_ARMED
                    os.close(ready_read)
                    ready_read = -1
                    break
            if flags & _LOCAL_MODEL_DIRECT_FLAG_SECURITY_ARMED:
                while root_wait_status is None:
                    if received_signal:
                        outcome = _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL
                        break
                    if time.monotonic() >= absolute_deadline:
                        outcome = _LOCAL_MODEL_DIRECT_OUTCOME_DEADLINE
                        break
                    process_id, wait_status = os.waitpid(root_pid, os.WNOHANG)
                    if process_id == root_pid:
                        root_wait_status = wait_status
                        outcome = _LOCAL_MODEL_DIRECT_OUTCOME_TARGET
                        break
                    time.sleep(_SCOPE_EXEC_POLL_SECONDS)
    except TimeoutError:
        pass
    except BaseException:
        outcome = _LOCAL_MODEL_DIRECT_OUTCOME_FAILURE
    finally:
        try:
            signal.pthread_sigmask(
                signal.SIG_BLOCK,
                set(_SCOPE_EXEC_FORWARDED_SIGNALS),
            )
        except (OSError, ValueError):
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE
        for descriptor in (gate_read, gate_write, ready_read, ready_write):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if root_pid > 0:
            root_wait_status, tree_empty = _drain_local_model_direct_children(
                root_pid,
                root_pidfd,
                root_wait_status,
            )
            if root_wait_status is not None:
                flags |= _LOCAL_MODEL_DIRECT_FLAG_ROOT_REAPED
            if tree_empty:
                flags |= _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
            else:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE
        else:
            _, no_children = _local_model_direct_reap()
            child_batch = _local_model_direct_children_batch()
            if no_children and child_batch == (set(), True):
                flags |= _LOCAL_MODEL_DIRECT_FLAG_TREE_EMPTY
            else:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE
        if root_pidfd >= 0:
            try:
                os.close(root_pidfd)
            except OSError:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE
        if controller_pidfd >= 0:
            try:
                os.close(controller_pidfd)
            except OSError:
                outcome = _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE
        try:
            pending_signals = signal.sigpending()
        except (OSError, ValueError):
            pending_signals = set()
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE
        if not received_signal:
            for signal_number in _SCOPE_EXEC_FORWARDED_SIGNALS:
                if signal_number in pending_signals:
                    received_signal = signal_number
                    break
        if received_signal and outcome != _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE:
            outcome = _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL
        wait_status_value = root_wait_status if root_wait_status is not None else 0
        if outcome == _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL:
            wait_status_value = received_signal
        frame_written = _write_local_model_direct_status(
            status_descriptor,
            outcome,
            flags,
            root_pid,
            wait_status_value,
            root_start,
        )
        try:
            os.close(status_descriptor)
        except OSError:
            frame_written = False

    for signal_number in _SCOPE_EXEC_FORWARDED_SIGNALS:
        try:
            signal.signal(signal_number, signal.SIG_DFL)
        except (OSError, ValueError):
            frame_written = False
    try:
        signal.pthread_sigmask(
            signal.SIG_UNBLOCK,
            set(_SCOPE_EXEC_FORWARDED_SIGNALS),
        )
    except (OSError, ValueError):
        frame_written = False

    if not frame_written or outcome == _LOCAL_MODEL_DIRECT_OUTCOME_CLEANUP_FAILURE:
        os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)
    if outcome == _LOCAL_MODEL_DIRECT_OUTCOME_TARGET and root_wait_status is not None:
        returncode = os.waitstatus_to_exitcode(root_wait_status)
        if returncode >= 0:
            os._exit(returncode)
        signal_number = -returncode
        if signal_number not in {signal.SIGKILL, signal.SIGSTOP}:
            signal.signal(signal_number, signal.SIG_DFL)
        os.kill(os.getpid(), signal_number)
    if outcome == _LOCAL_MODEL_DIRECT_OUTCOME_SIGNAL and received_signal:
        os.kill(os.getpid(), received_signal)
    os._exit(_LOCAL_MODEL_DIRECT_SUPERVISOR_EXIT)


def _decode_mountinfo_path(value: str) -> str:
    return (
        value.replace(r"\040", " ")
        .replace(r"\011", "\t")
        .replace(r"\012", "\n")
        .replace(r"\134", "\\")
    )


def _cgroup2_path_for_pid(
    pid: int,
    *,
    mappings: tuple[_Cgroup2MountMapping, ...] | None = None,
) -> Path | None:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    cgroup_path = _mapped_cgroup2_path(
        _PROC_ROOT / str(pid) / "cgroup",
        mappings=mappings,
    )
    if cgroup_path is None:
        return None
    if not cgroup_path.name.endswith(".scope"):
        return None
    return cgroup_path


def _current_cgroup2_path() -> Path | None:
    return _cgroup2_path_for_pid(os.getpid())


def _parse_cgroup_weight_contents(
    contents: str,
    *,
    io_weight: bool = False,
) -> int | None:
    if io_weight:
        for line in contents.splitlines():
            fields = line.split()
            if len(fields) == 2 and fields[0] == "default":
                value = fields[1]
                break
        else:
            return None
    else:
        value = contents.strip()
    if not value.isdecimal():
        return None
    parsed = int(value)
    if not 1 <= parsed <= 10_000:
        return None
    return parsed


def _parse_cgroup_weight(path: Path, *, io_weight: bool = False) -> int | None:
    contents = _read_cgroup_file(path)
    if contents is None:
        return None
    return _parse_cgroup_weight_contents(contents, io_weight=io_weight)


def _scope_stat_matches_mount_mapping(
    scope_stat: os.stat_result,
    mount_mapping: _Cgroup2MountMapping,
) -> bool:
    try:
        return (
            os.major(scope_stat.st_dev) == mount_mapping.device_major
            and os.minor(scope_stat.st_dev) == mount_mapping.device_minor
        )
    except (OverflowError, TypeError, ValueError):
        return False


def _open_scope_directory_candidate(
    scope_path: Path,
    mount_mapping: _Cgroup2MountMapping,
) -> tuple[int, os.stat_result] | None:
    directory = getattr(os, "O_DIRECTORY", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    if directory is None or nofollow is None or cloexec is None:
        return None
    try:
        descriptor = os.open(
            os.fspath(scope_path),
            os.O_RDONLY | directory | nofollow | cloexec,
        )
    except (OSError, TypeError, ValueError):
        return None
    try:
        scope_stat = os.fstat(descriptor)
    except (OSError, ValueError):
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    if (
        not stat.S_ISDIR(scope_stat.st_mode)
        or not _scope_stat_matches_mount_mapping(scope_stat, mount_mapping)
    ):
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    return descriptor, scope_stat


def _open_scope_directory(
    scope_path: Path,
    identity: PriorityScopeIdentity,
    mount_mapping: _Cgroup2MountMapping,
) -> int | None:
    opened = _open_scope_directory_candidate(scope_path, mount_mapping)
    if opened is None:
        return None
    descriptor, scope_stat = opened
    if scope_stat.st_dev == identity.device and scope_stat.st_ino == identity.inode:
        return descriptor
    try:
        os.close(descriptor)
    except OSError:
        pass
    return None


def _scope_fd_matches_identity(
    scope_descriptor: int,
    scope_path: Path,
    identity: PriorityScopeIdentity,
) -> bool:
    try:
        final_scope_stat = os.fstat(scope_descriptor)
        path_stat = os.stat(scope_path, follow_symlinks=False)
    except (NotImplementedError, OSError, TypeError, ValueError):
        return False
    return (
        stat.S_ISDIR(final_scope_stat.st_mode)
        and final_scope_stat.st_dev == identity.device
        and final_scope_stat.st_ino == identity.inode
        and stat.S_ISDIR(path_stat.st_mode)
        and path_stat.st_dev == identity.device
        and path_stat.st_ino == identity.inode
    )


def _descriptor_mount_id(descriptor: int) -> int | None:
    if type(descriptor) is not int or descriptor < 0:
        return None
    contents = _read_affinity_file(
        _PROC_ROOT / "self" / "fdinfo" / str(descriptor),
        max_bytes=MAX_FDINFO_BYTES,
    )
    if contents is None or not contents.endswith("\n") or "\r" in contents:
        return None
    mount_id: int | None = None
    for line in contents[:-1].split("\n"):
        if not line.startswith("mnt_id:"):
            continue
        value = line.removeprefix("mnt_id:\t")
        if (
            mount_id is not None
            or value == line
            or not value
            or len(value) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
            or not value.isascii()
            or not value.isdecimal()
            or (len(value) > 1 and value.startswith("0"))
        ):
            return None
        try:
            parsed = int(value)
        except (OverflowError, ValueError):
            return None
        if parsed <= 0 or parsed > MAX_PRIORITY_SCOPE_IDENTITY_INTEGER:
            return None
        mount_id = parsed
    return mount_id


def _read_scope_file_snapshot(
    scope_descriptor: int,
    name: str,
    *,
    max_bytes: int,
    mount_mapping: _Cgroup2MountMapping | None = None,
    expected_mount_id: int | None = None,
) -> _ScopeFileSnapshot | None:
    if (
        name not in {"cpu.weight", "io.weight", "cgroup.events", "cgroup.procs"}
        or not isinstance(max_bytes, int)
        or isinstance(max_bytes, bool)
        or max_bytes <= 0
        or ((mount_mapping is None) != (expected_mount_id is None))
        or (
            expected_mount_id is not None
            and (type(expected_mount_id) is not int or expected_mount_id <= 0)
        )
    ):
        return None
    nofollow = getattr(os, "O_NOFOLLOW", None)
    cloexec = getattr(os, "O_CLOEXEC", None)
    nonblock = getattr(os, "O_NONBLOCK", None)
    if nofollow is None or cloexec is None or nonblock is None:
        return None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | cloexec | nonblock,
            dir_fd=scope_descriptor,
        )
    except (OSError, TypeError, ValueError):
        return None
    try:
        initial_stat = os.fstat(descriptor)
        initial_mount_id = (
            _descriptor_mount_id(descriptor) if mount_mapping is not None else None
        )
        if (
            not stat.S_ISREG(initial_stat.st_mode)
            or (
                mount_mapping is not None
                and (
                    not _scope_stat_matches_mount_mapping(
                        initial_stat,
                        mount_mapping,
                    )
                    or initial_mount_id != expected_mount_id
                )
            )
        ):
            return None
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(descriptor, min(4096, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > max_bytes:
            return None
        final_stat = os.fstat(descriptor)
        final_mount_id = (
            _descriptor_mount_id(descriptor) if mount_mapping is not None else None
        )
        initial_identity = (
            initial_stat.st_dev,
            initial_stat.st_ino,
            initial_stat.st_mode,
            initial_stat.st_uid,
            initial_stat.st_gid,
            initial_stat.st_nlink,
        )
        final_identity = (
            final_stat.st_dev,
            final_stat.st_ino,
            final_stat.st_mode,
            final_stat.st_uid,
            final_stat.st_gid,
            final_stat.st_nlink,
        )
        if (
            initial_identity != final_identity
            or not stat.S_ISREG(final_stat.st_mode)
            or (
                mount_mapping is not None
                and (
                    not _scope_stat_matches_mount_mapping(final_stat, mount_mapping)
                    or final_mount_id != expected_mount_id
                )
            )
        ):
            return None
        return _ScopeFileSnapshot(
            contents=bytes(data).decode("ascii"),
            device=final_stat.st_dev,
            inode=final_stat.st_ino,
            mode=final_stat.st_mode,
            owner=final_stat.st_uid,
            group=final_stat.st_gid,
            link_count=final_stat.st_nlink,
        )
    except (OSError, OverflowError, UnicodeDecodeError, ValueError):
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


def _read_scope_file(
    scope_descriptor: int,
    name: str,
    *,
    max_bytes: int,
) -> str | None:
    snapshot = _read_scope_file_snapshot(
        scope_descriptor,
        name,
        max_bytes=max_bytes,
    )
    return snapshot.contents if snapshot is not None else None


def _current_priority_scope_weights() -> tuple[int, int] | None:
    cgroup_path = _current_cgroup2_path()
    if cgroup_path is None:
        return None
    cpu_weight = _parse_cgroup_weight(cgroup_path / "cpu.weight")
    io_weight = _parse_cgroup_weight(cgroup_path / "io.weight", io_weight=True)
    if cpu_weight is None or io_weight is None:
        return None
    return cpu_weight, io_weight


def priority_scope_identity_for_pid(
    pid: int,
    *,
    cpu_weight: int = SOC_CPU_WEIGHT,
    io_weight: int = SOC_IO_WEIGHT,
) -> PriorityScopeIdentity | None:
    """Return the verified private systemd-scope identity for a process."""

    if (
        not isinstance(cpu_weight, int)
        or isinstance(cpu_weight, bool)
        or not 1 <= cpu_weight <= 10_000
        or not isinstance(io_weight, int)
        or isinstance(io_weight, bool)
        or not 1 <= io_weight <= 10_000
    ):
        return None
    mappings = _cgroup2_mount_mappings()
    if mappings is None:
        return None
    cgroup_path = _cgroup2_path_for_pid(pid, mappings=mappings)
    if cgroup_path is None:
        return None
    origin = _canonical_cgroup2_path_origin(cgroup_path, mappings)
    if origin is None:
        return None
    _, mount_mapping = origin
    opened = _open_scope_directory_candidate(cgroup_path, mount_mapping)
    if opened is None:
        return None
    scope_descriptor, scope_stat = opened
    candidate = PriorityScopeIdentity(
        path=os.fspath(cgroup_path),
        device=scope_stat.st_dev,
        inode=scope_stat.st_ino,
    )
    try:
        cpu_contents = _read_scope_file(
            scope_descriptor,
            "cpu.weight",
            max_bytes=MAX_CGROUP_FILE_BYTES,
        )
        io_contents = _read_scope_file(
            scope_descriptor,
            "io.weight",
            max_bytes=MAX_CGROUP_FILE_BYTES,
        )
        if cpu_contents is None or io_contents is None:
            return None
        if (
            _parse_cgroup_weight_contents(cpu_contents) != cpu_weight
            or _parse_cgroup_weight_contents(io_contents, io_weight=True) != io_weight
        ):
            return None
        if _cgroup2_path_for_pid(pid, mappings=mappings) != cgroup_path:
            return None
        if not _scope_fd_matches_identity(scope_descriptor, cgroup_path, candidate):
            return None
        return candidate
    finally:
        try:
            os.close(scope_descriptor)
        except OSError:
            pass


def priority_scope_identity_for_control_group(
    control_group: object,
    *,
    cpu_weight: int = SOC_CPU_WEIGHT,
    io_weight: int = SOC_IO_WEIGHT,
) -> PriorityScopeIdentity | None:
    """Return identity for a verified canonical cgroup-v2 scope path."""

    if (
        not isinstance(control_group, str)
        or isinstance(control_group, bool)
        or not control_group
        or len(control_group) > MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS
        or any(not 0x20 <= ord(character) <= 0x7E for character in control_group)
        or not control_group.startswith("/")
        or control_group.startswith("//")
        or control_group != os.path.normpath(control_group)
        or not isinstance(cpu_weight, int)
        or isinstance(cpu_weight, bool)
        or not 1 <= cpu_weight <= 10_000
        or not isinstance(io_weight, int)
        or isinstance(io_weight, bool)
        or not 1 <= io_weight <= 10_000
    ):
        return None
    control_group_path = _normalized_absolute_path(control_group)
    if control_group_path is None:
        return None
    mappings = _cgroup2_mount_mappings()
    if mappings is None:
        return None
    resolved = _canonical_cgroup2_mapping(control_group_path, mappings)
    scope_path = resolved[0] if resolved is not None else None
    mount_mapping = resolved[1] if resolved is not None else None
    origin = (
        _canonical_cgroup2_path_origin(scope_path, mappings)
        if scope_path is not None
        else None
    )
    if (
        scope_path is None
        or mount_mapping is None
        or origin != (control_group_path, mount_mapping)
        or not scope_path.name.endswith(".scope")
        or len(os.fspath(scope_path)) > MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS
    ):
        return None
    opened = _open_scope_directory_candidate(scope_path, mount_mapping)
    if opened is None:
        return None
    scope_descriptor, scope_stat = opened
    candidate = PriorityScopeIdentity(
        path=os.fspath(scope_path),
        device=scope_stat.st_dev,
        inode=scope_stat.st_ino,
    )
    try:
        cpu_contents = _read_scope_file(
            scope_descriptor,
            "cpu.weight",
            max_bytes=MAX_CGROUP_FILE_BYTES,
        )
        io_contents = _read_scope_file(
            scope_descriptor,
            "io.weight",
            max_bytes=MAX_CGROUP_FILE_BYTES,
        )
        if cpu_contents is None or io_contents is None:
            return None
        if (
            _parse_cgroup_weight_contents(cpu_contents) != cpu_weight
            or _parse_cgroup_weight_contents(io_contents, io_weight=True) != io_weight
        ):
            return None
        if not _scope_fd_matches_identity(scope_descriptor, scope_path, candidate):
            return None
        return candidate
    finally:
        try:
            os.close(scope_descriptor)
        except OSError:
            pass


def serialize_priority_scope_identity(identity: PriorityScopeIdentity) -> str | None:
    if not isinstance(identity, PriorityScopeIdentity):
        return None
    if (
        not isinstance(identity.path, str)
        or not identity.path
        or len(identity.path) > MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS
        or "|" in identity.path
        or any(not 0x20 <= ord(character) <= 0x7E for character in identity.path)
        or not isinstance(identity.device, int)
        or isinstance(identity.device, bool)
        or identity.device < 0
        or identity.device > MAX_PRIORITY_SCOPE_IDENTITY_INTEGER
        or not isinstance(identity.inode, int)
        or isinstance(identity.inode, bool)
        or identity.inode <= 0
        or identity.inode > MAX_PRIORITY_SCOPE_IDENTITY_INTEGER
    ):
        return None
    value = f"{identity.path}|{int(identity.device)}|{int(identity.inode)}"
    if len(value) > MAX_PRIORITY_SCOPE_IDENTITY_CHARS:
        return None
    return value


def parse_priority_scope_identity(value: object) -> PriorityScopeIdentity | None:
    if (
        not isinstance(value, str)
        or isinstance(value, bool)
        or not value
        or len(value) > MAX_PRIORITY_SCOPE_IDENTITY_CHARS
    ):
        return None
    parts = value.split("|")
    if len(parts) != 3:
        return None
    path, raw_device, raw_inode = parts
    if (
        not path
        or len(path) > MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS
        or any(not 0x20 <= ord(character) <= 0x7E for character in path)
        or len(raw_device) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
        or len(raw_inode) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
        or not raw_device.isascii()
        or not raw_inode.isascii()
        or not raw_device.isdecimal()
        or not raw_inode.isdecimal()
    ):
        return None
    try:
        device = int(raw_device)
        inode = int(raw_inode)
    except (OverflowError, ValueError):
        return None
    if (
        device > MAX_PRIORITY_SCOPE_IDENTITY_INTEGER
        or inode <= 0
        or inode > MAX_PRIORITY_SCOPE_IDENTITY_INTEGER
        or raw_device != str(device)
        or raw_inode != str(inode)
    ):
        return None
    return PriorityScopeIdentity(path=path, device=device, inode=inode)


def _priority_scope_snapshot(
    identity: PriorityScopeIdentity,
    *,
    pid: int | None,
    cpu_weight: int,
    io_weight: int,
    include_process_ids: bool,
    include_membership: bool = False,
) -> tuple[
    int,
    int,
    str | None,
    _ScopeFileSnapshot | None,
    _ScopeFileSnapshot | None,
] | None:
    if (
        not isinstance(identity, PriorityScopeIdentity)
        or not isinstance(identity.path, str)
        or not isinstance(identity.device, int)
        or isinstance(identity.device, bool)
        or identity.device < 0
        or not isinstance(identity.inode, int)
        or isinstance(identity.inode, bool)
        or identity.inode <= 0
        or not isinstance(cpu_weight, int)
        or isinstance(cpu_weight, bool)
        or not 1 <= cpu_weight <= 10_000
        or not isinstance(io_weight, int)
        or isinstance(io_weight, bool)
        or not 1 <= io_weight <= 10_000
        or (include_membership and not include_process_ids)
    ):
        return None
    scope_path = Path(identity.path)
    if (
        not scope_path.is_absolute()
        or scope_path != Path(os.path.normpath(identity.path))
        or not scope_path.name.endswith(".scope")
    ):
        return None
    mappings = _cgroup2_mount_mappings()
    if mappings is None:
        return None
    mount_mapping: _Cgroup2MountMapping
    if pid is not None:
        if _cgroup2_path_for_pid(pid, mappings=mappings) != scope_path:
            return None
        origin = _canonical_cgroup2_path_origin(scope_path, mappings)
        if origin is None:
            return None
        _, mount_mapping = origin
    else:
        origin = _canonical_cgroup2_path_origin(scope_path, mappings)
        if origin is None:
            return None
        _, mount_mapping = origin
    if pid is None and not _stored_scope_path_is_canonical(scope_path, mappings):
        return None
    scope_descriptor = _open_scope_directory(scope_path, identity, mount_mapping)
    if scope_descriptor is None:
        return None
    try:
        scope_mount_id = None
        if include_membership:
            try:
                scope_stat = os.fstat(scope_descriptor)
            except (OSError, ValueError):
                return None
            scope_mount_id = _descriptor_mount_id(scope_descriptor)
            if (
                not stat.S_ISDIR(scope_stat.st_mode)
                or not _scope_stat_matches_mount_mapping(scope_stat, mount_mapping)
                or scope_mount_id is None
            ):
                return None
            cpu_snapshot = _read_scope_file_snapshot(
                scope_descriptor,
                "cpu.weight",
                max_bytes=MAX_CGROUP_FILE_BYTES,
                mount_mapping=mount_mapping,
                expected_mount_id=scope_mount_id,
            )
            io_snapshot = _read_scope_file_snapshot(
                scope_descriptor,
                "io.weight",
                max_bytes=MAX_CGROUP_FILE_BYTES,
                mount_mapping=mount_mapping,
                expected_mount_id=scope_mount_id,
            )
            cpu_contents = cpu_snapshot.contents if cpu_snapshot is not None else None
            io_contents = io_snapshot.contents if io_snapshot is not None else None
        else:
            cpu_contents = _read_scope_file(
                scope_descriptor,
                "cpu.weight",
                max_bytes=MAX_CGROUP_FILE_BYTES,
            )
            io_contents = _read_scope_file(
                scope_descriptor,
                "io.weight",
                max_bytes=MAX_CGROUP_FILE_BYTES,
            )
        events_before = None
        process_before = None
        process_after = None
        if include_membership:
            events_before = _read_scope_file_snapshot(
                scope_descriptor,
                "cgroup.events",
                max_bytes=MAX_CGROUP_FILE_BYTES,
                mount_mapping=mount_mapping,
                expected_mount_id=scope_mount_id,
            )
        process_contents = None
        if include_membership:
            process_before = _read_scope_file_snapshot(
                scope_descriptor,
                "cgroup.procs",
                max_bytes=MAX_CGROUP_PROCS_BYTES,
                mount_mapping=mount_mapping,
                expected_mount_id=scope_mount_id,
            )
            process_after = _read_scope_file_snapshot(
                scope_descriptor,
                "cgroup.procs",
                max_bytes=MAX_CGROUP_PROCS_BYTES,
                mount_mapping=mount_mapping,
                expected_mount_id=scope_mount_id,
            )
            process_contents = (
                process_before.contents if process_before is not None else None
            )
        elif include_process_ids:
            process_contents = _read_scope_file(
                scope_descriptor,
                "cgroup.procs",
                max_bytes=MAX_CGROUP_PROCS_BYTES,
            )
        events_after = None
        if include_membership:
            events_after = _read_scope_file_snapshot(
                scope_descriptor,
                "cgroup.events",
                max_bytes=MAX_CGROUP_FILE_BYTES,
                mount_mapping=mount_mapping,
                expected_mount_id=scope_mount_id,
            )
        process_ids_before = (
            _parse_scope_membership_process_ids(process_before.contents)
            if process_before is not None
            else None
        )
        process_ids_after = (
            _parse_scope_membership_process_ids(process_after.contents)
            if process_after is not None
            else None
        )
        process_identity_before = (
            (
                process_before.device,
                process_before.inode,
                process_before.mode,
                process_before.owner,
                process_before.group,
                process_before.link_count,
            )
            if process_before is not None
            else None
        )
        process_identity_after = (
            (
                process_after.device,
                process_after.inode,
                process_after.mode,
                process_after.owner,
                process_after.group,
                process_after.link_count,
            )
            if process_after is not None
            else None
        )
        if (
            cpu_contents is None
            or io_contents is None
            or (include_process_ids and process_contents is None)
            or (include_membership and events_before is None)
            or (include_membership and events_after is None)
            or (include_membership and events_before != events_after)
            or (include_membership and process_ids_before is None)
            or (include_membership and process_ids_after is None)
            or (include_membership and process_ids_before != process_ids_after)
            or (include_membership and process_identity_before != process_identity_after)
        ):
            return None
        parsed_cpu_weight = _parse_cgroup_weight_contents(cpu_contents)
        parsed_io_weight = _parse_cgroup_weight_contents(
            io_contents,
            io_weight=True,
        )
        if parsed_cpu_weight != cpu_weight or parsed_io_weight != io_weight:
            return None
        if pid is not None and _cgroup2_path_for_pid(pid, mappings=mappings) != scope_path:
            return None
        if (
            include_membership
            and _descriptor_mount_id(scope_descriptor) != scope_mount_id
        ):
            return None
        if not _scope_fd_matches_identity(scope_descriptor, scope_path, identity):
            return None
        return (
            parsed_cpu_weight,
            parsed_io_weight,
            process_contents,
            events_before,
            events_after,
        )
    finally:
        try:
            os.close(scope_descriptor)
        except OSError:
            pass


def verify_priority_scope_identity(
    identity: PriorityScopeIdentity,
    *,
    pid: int | None = None,
    cpu_weight: int = SOC_CPU_WEIGHT,
    io_weight: int = SOC_IO_WEIGHT,
) -> bool:
    return (
        _priority_scope_snapshot(
            identity,
            pid=pid,
            cpu_weight=cpu_weight,
            io_weight=io_weight,
            include_process_ids=False,
        )
        is not None
    )


def priority_scope_process_ids(
    identity: PriorityScopeIdentity,
    *,
    cpu_weight: int = SOC_CPU_WEIGHT,
    io_weight: int = SOC_IO_WEIGHT,
) -> tuple[int, ...] | None:
    snapshot = _priority_scope_snapshot(
        identity,
        pid=None,
        cpu_weight=cpu_weight,
        io_weight=io_weight,
        include_process_ids=True,
    )
    if snapshot is None:
        return None
    contents = snapshot[2]
    if contents is None:
        return None
    return _parse_scope_process_ids(contents)


def _parse_scope_process_ids(contents: str) -> tuple[int, ...] | None:
    process_ids: set[int] = set()
    for line in contents.splitlines():
        if not line.isdecimal():
            return None
        try:
            process_id = int(line)
        except (OverflowError, ValueError):
            return None
        if process_id <= 0:
            return None
        process_ids.add(process_id)
    return tuple(sorted(process_ids))


def _canonical_cgroup_lines(
    contents: str,
    *,
    max_bytes: int,
    allow_empty: bool,
) -> tuple[str, ...] | None:
    if (
        type(contents) is not str
        or len(contents) > max_bytes
        or not contents.isascii()
    ):
        return None
    if not contents:
        return () if allow_empty else None
    if not contents.endswith("\n") or "\r" in contents:
        return None
    lines = tuple(contents[:-1].split("\n"))
    if not lines or any(not line for line in lines):
        return None
    return lines


def _parse_scope_membership_process_ids(contents: str) -> tuple[int, ...] | None:
    lines = _canonical_cgroup_lines(
        contents,
        max_bytes=MAX_CGROUP_PROCS_BYTES,
        allow_empty=True,
    )
    if lines is None:
        return None
    process_ids: set[int] = set()
    for line in lines:
        if (
            len(line) > MAX_LINUX_PID_DECIMAL_DIGITS
            or not line.isdecimal()
            or (len(line) > 1 and line.startswith("0"))
        ):
            return None
        process_id = int(line)
        if not 1 <= process_id <= MAX_LINUX_PID:
            return None
        process_ids.add(process_id)
    return tuple(sorted(process_ids))


def _parse_scope_populated(contents: str) -> bool | None:
    lines = _canonical_cgroup_lines(
        contents,
        max_bytes=MAX_CGROUP_FILE_BYTES,
        allow_empty=False,
    )
    if lines is None:
        return None
    populated: bool | None = None
    keys: set[str] = set()
    for line in lines:
        fields = line.split(" ")
        if len(fields) != 2:
            return None
        key, value = fields
        if (
            _CGROUP_EVENT_KEY_RE.fullmatch(key) is None
            or key in keys
            or not value.isascii()
            or not value.isdecimal()
            or len(value) > MAX_PRIORITY_SCOPE_IDENTITY_DECIMAL_DIGITS
            or (len(value) > 1 and value.startswith("0"))
        ):
            return None
        keys.add(key)
        if key == "populated":
            if value not in {"0", "1"}:
                return None
            populated = value == "1"
    return populated


def priority_scope_membership(
    identity: PriorityScopeIdentity,
    *,
    cpu_weight: int = SOC_CPU_WEIGHT,
    io_weight: int = SOC_IO_WEIGHT,
) -> PriorityScopeMembership | None:
    """Return stable direct PIDs plus hierarchical populated state for a scope."""

    snapshot = _priority_scope_snapshot(
        identity,
        pid=None,
        cpu_weight=cpu_weight,
        io_weight=io_weight,
        include_process_ids=True,
        include_membership=True,
    )
    if snapshot is None:
        return None
    process_contents = snapshot[2]
    events = snapshot[3]
    if process_contents is None or events is None:
        return None
    process_ids = _parse_scope_membership_process_ids(process_contents)
    populated = _parse_scope_populated(events.contents)
    if process_ids is None or populated is None or (process_ids and not populated):
        return None
    return PriorityScopeMembership(process_ids=process_ids, populated=populated)


def verify_priority_scope(*, cpu_weight: int, io_weight: int) -> bool:
    """Verify the current process is in the requested cgroup-v2 scope."""

    if (
        not isinstance(cpu_weight, int)
        or isinstance(cpu_weight, bool)
        or not isinstance(io_weight, int)
        or isinstance(io_weight, bool)
    ):
        return False
    return _current_priority_scope_weights() == (cpu_weight, io_weight)


def _scope_exec_supervisor_spec(
    command: Sequence[str],
) -> _ScopeExecSupervisorSpec | None:
    if isinstance(command, (str, bytes)):
        return None
    try:
        arguments = tuple(command)
        token_index = arguments.index(_SCOPE_EXEC_WRAPPER_TOKEN)
    except (TypeError, ValueError):
        return None
    if token_index < 2 or any(not isinstance(item, str) for item in arguments):
        return None
    try:
        runtime, entry = _scope_exec_wrapper_paths()
    except PriorityScopeError:
        return None
    if (
        arguments[token_index - 2 : token_index] != (runtime, entry)
        or token_index + 1 >= len(arguments)
        or arguments[token_index + 1] != _SCOPE_EXEC_LATCH_REQUIRED_TOKEN
    ):
        return None
    target = _scope_exec_launch_spec_from_arguments(arguments[token_index:])
    if target is None:
        return None
    fixed_files = (
        _scope_exec_file_identity(arguments[0], executable=True),
        _scope_exec_file_identity(runtime, executable=True),
        _scope_exec_file_identity(entry, executable=False),
    )
    if any(identity is None for identity in fixed_files):
        return None
    return _ScopeExecSupervisorSpec(
        command=arguments,
        target=target,
        fixed_files=tuple(
            identity for identity in fixed_files if identity is not None
        ),
    )


def _scope_exec_supervisor_spec_is_unchanged(
    spec: _ScopeExecSupervisorSpec,
    *,
    include_scope_tool: bool = True,
) -> bool:
    if not _scope_exec_launch_spec_is_unchanged(spec.target):
        return False
    identities = spec.fixed_files if include_scope_tool else spec.fixed_files[1:]
    return all(
        _scope_exec_file_identity(identity.path, executable=identity.executable)
        == identity
        for identity in identities
    )


def _create_scope_exec_latch() -> tuple[socket.socket, str, str]:
    address = secrets.token_hex(16)
    nonce = secrets.token_hex(32)
    if (
        _SCOPE_EXEC_LATCH_ADDRESS_RE.fullmatch(address) is None
        or _SCOPE_EXEC_LATCH_NONCE_RE.fullmatch(nonce) is None
    ):
        raise PriorityScopeError("SOC priority scope status channel is unavailable")
    listener: socket.socket | None = None
    try:
        socket_type = socket.SOCK_DGRAM | getattr(socket, "SOCK_CLOEXEC", 0)
        listener = socket.socket(socket.AF_UNIX, socket_type)
        listener.setsockopt(
            socket.SOL_SOCKET,
            getattr(socket, "SO_PASSCRED"),
            1,
        )
        listener.bind("\0" + address)
        listener.setblocking(False)
    except (AttributeError, OSError, ValueError):
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        raise _ScopeExecChannelUnavailable(
            "SOC priority scope status channel is unavailable"
        ) from None
    return listener, address, nonce


def _receive_scope_exec_latches(
    listener: socket.socket,
    nonce: str,
    state: str,
) -> str:
    expected = _SCOPE_EXEC_LATCH_MESSAGE_PREFIX + nonce.encode("ascii")
    credentials_size = struct.calcsize("3i")
    credentials_space = socket.CMSG_SPACE(credentials_size)
    try:
        message, ancillary, flags, _address = listener.recvmsg(
            _SCOPE_EXEC_LATCH_MAX_BYTES + 1,
            credentials_space,
        )
    except BlockingIOError:
        return state
    except (OSError, ValueError):
        return "invalid"
    credentials = []
    for level, kind, data in ancillary:
        if (
            level == socket.SOL_SOCKET
            and kind == getattr(socket, "SCM_CREDENTIALS", -1)
            and len(data) == credentials_size
        ):
            credentials.append(struct.unpack("3i", data))
    valid = (
        state == "none"
        and message == expected
        and not flags & getattr(socket, "MSG_TRUNC", 0)
        and len(credentials) == 1
        and credentials[0][0] > 0
        and credentials[0][1] == os.getuid()
    )
    return "entered" if valid else "invalid"


def _signal_scope_attempt_group(
    process: subprocess.Popen[bytes],
    stop_signal: int,
    *,
    process_group_id: int | None = None,
) -> bool:
    process_id = getattr(process, "pid", None)
    expected_group = process_id if process_group_id is None else process_group_id
    if (
        not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
        or not isinstance(expected_group, int)
        or isinstance(expected_group, bool)
        or expected_group != process_id
        or stop_signal not in signal.valid_signals()
    ):
        return False
    try:
        if process.poll() is not None or os.getpgid(process_id) != expected_group:
            return False
        if process.poll() is not None:
            return False
        os.killpg(expected_group, stop_signal)
    except (OSError, OverflowError, TypeError, ValueError):
        return False
    return True


def _bind_scope_attempt_process_group(
    process: subprocess.Popen[bytes],
) -> int | None:
    process_id = getattr(process, "pid", None)
    if (
        not isinstance(process_id, int)
        or isinstance(process_id, bool)
        or process_id <= 0
    ):
        raise PriorityScopeError("SOC priority scope child identity is unavailable")
    try:
        if process.poll() is not None:
            return None
        process_group_id = os.getpgid(process_id)
        if process_group_id != process_id:
            raise PriorityScopeError(
                "SOC priority scope child identity is unavailable"
            )
        if process.poll() is not None:
            return None
    except PriorityScopeError:
        raise
    except (OSError, OverflowError, TypeError, ValueError):
        try:
            if process.poll() is not None:
                return None
        except (OSError, OverflowError, TypeError, ValueError):
            pass
        raise PriorityScopeError(
            "SOC priority scope child identity is unavailable"
        ) from None
    return process_group_id


def _raise_scope_attempt_signal(stop_signal: int) -> None:
    if stop_signal == signal.SIGINT:
        raise KeyboardInterrupt
    raise SystemExit(128 + stop_signal)


class _ScopeExecSignalForwarder:
    def __init__(self) -> None:
        self._bound_process_group: (
            tuple[subprocess.Popen[bytes], int | None] | None
        ) = None
        self.pending: list[int] = []
        self.primary_signal: int | None = None
        self.primary_signal_forwarded = False
        self.error_cleanup_active = False

    @property
    def process(self) -> subprocess.Popen[bytes] | None:
        bound = self._bound_process_group
        return None if bound is None else bound[0]

    @property
    def process_group_id(self) -> int | None:
        bound = self._bound_process_group
        return None if bound is None else bound[1]

    def handle(self, stop_signal: int, _frame: object) -> None:
        if stop_signal not in _SCOPE_EXEC_FORWARDED_SIGNALS:
            return
        bound = self._bound_process_group
        if bound is None:
            if self.primary_signal is None and not self.error_cleanup_active:
                self.primary_signal = stop_signal
            if (
                stop_signal not in self.pending
                and len(self.pending) < len(_SCOPE_EXEC_FORWARDED_SIGNALS)
            ):
                self.pending.append(stop_signal)
            return
        process, process_group_id = bound
        is_primary = self.primary_signal is None and not self.error_cleanup_active
        if is_primary:
            self.primary_signal = stop_signal
        forwarded = False
        if process_group_id is not None:
            forwarded = _signal_scope_attempt_group(
                process,
                stop_signal,
                process_group_id=process_group_id,
            )
        if is_primary:
            self.primary_signal_forwarded = forwarded

    def begin_error_cleanup(self) -> int | None:
        self.error_cleanup_active = True
        return self.primary_signal

    def bind(self, process: subprocess.Popen[bytes]) -> None:
        if self._bound_process_group is not None:
            raise PriorityScopeError("SOC priority scope child identity is unavailable")
        process_group_id = _bind_scope_attempt_process_group(process)
        self._bound_process_group = (process, process_group_id)
        pending = tuple(self.pending)
        self.pending.clear()
        if pending and self.primary_signal is None:
            self.primary_signal = pending[0]
        if process_group_id is not None:
            for stop_signal in pending:
                forwarded = _signal_scope_attempt_group(
                    process,
                    stop_signal,
                    process_group_id=process_group_id,
                )
                if stop_signal == self.primary_signal:
                    self.primary_signal_forwarded = forwarded


@contextmanager
def _forward_scope_attempt_signals() -> Iterator[_ScopeExecSignalForwarder]:
    previous_handlers: list[tuple[int, object]] = []
    forwarder = _ScopeExecSignalForwarder()

    try:
        for stop_signal in _SCOPE_EXEC_FORWARDED_SIGNALS:
            previous = signal.getsignal(stop_signal)
            signal.signal(stop_signal, forwarder.handle)
            previous_handlers.append((stop_signal, previous))
        yield forwarder
    finally:
        for stop_signal, previous in reversed(previous_handlers):
            try:
                signal.signal(stop_signal, previous)
            except (OSError, RuntimeError, TypeError, ValueError):
                pass


def _wait_for_scope_attempt(
    process: subprocess.Popen[bytes],
) -> tuple[str, int | None]:
    deadline = time.monotonic() + _SCOPE_EXEC_REAP_TIMEOUT_SECONDS
    timeout = _SCOPE_EXEC_REAP_TIMEOUT_SECONDS
    while timeout > 0:
        try:
            return "reaped", process.wait(timeout=timeout)
        except InterruptedError:
            timeout = deadline - time.monotonic()
        except subprocess.TimeoutExpired:
            return "timeout", None
        except (OSError, OverflowError, TypeError, ValueError):
            return "error", None
    return "timeout", None


def _terminate_scope_attempt(
    process: subprocess.Popen[bytes],
    *,
    process_group_id: int | None = None,
    forwarded_signal: int | None = None,
) -> int | None:
    if forwarded_signal in _SCOPE_EXEC_FORWARDED_SIGNALS:
        wait_state, returncode = _wait_for_scope_attempt(process)
        if wait_state != "timeout":
            return returncode
    for stop_signal in (signal.SIGTERM, signal.SIGKILL):
        _signal_scope_attempt_group(
            process,
            stop_signal,
            process_group_id=process_group_id,
        )
        wait_state, returncode = _wait_for_scope_attempt(process)
        if wait_state != "timeout":
            return returncode
    return None


def _supervise_scope_attempt(
    process: subprocess.Popen[bytes],
    listener: socket.socket,
    nonce: str,
    signal_forwarder: _ScopeExecSignalForwarder | None = None,
) -> _ScopeExecAttemptOutcome:
    selector = selectors.DefaultSelector()
    state = "none"
    deadline = time.monotonic() + _SCOPE_EXEC_ENTER_TIMEOUT_SECONDS
    try:
        selector.register(listener, selectors.EVENT_READ)
        while True:
            if (
                signal_forwarder is not None
                and signal_forwarder.primary_signal is not None
            ):
                return _ScopeExecAttemptOutcome(None, state, False)
            state = _receive_scope_exec_latches(listener, nonce, state)
            try:
                polled = process.poll()
            except (OSError, OverflowError, TypeError, ValueError):
                raise PriorityScopeError(
                    "SOC priority scope child status is unavailable"
                ) from None
            if polled is not None:
                try:
                    waited = process.wait(timeout=_SCOPE_EXEC_REAP_TIMEOUT_SECONDS)
                except (OSError, OverflowError, TypeError, ValueError, subprocess.TimeoutExpired):
                    raise PriorityScopeError(
                        "SOC priority scope child status is unavailable"
                    ) from None
                state = _receive_scope_exec_latches(listener, nonce, state)
                state = _receive_scope_exec_latches(listener, nonce, state)
                if waited != polled:
                    return _ScopeExecAttemptOutcome(None, "invalid", False)
                return _ScopeExecAttemptOutcome(waited, state, False)
            if state == "invalid":
                raise PriorityScopeError(
                    "SOC priority scope status is invalid or ambiguous"
                )
            if state != "entered":
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    state = _receive_scope_exec_latches(listener, nonce, state)
                    if state != "entered":
                        return _ScopeExecAttemptOutcome(None, state, True)
                    continue
                poll_seconds = min(_SCOPE_EXEC_POLL_SECONDS, remaining)
            else:
                poll_seconds = _SCOPE_EXEC_POLL_SECONDS
            selector.select(poll_seconds)
    finally:
        selector.close()


def _propagate_scope_attempt_returncode(returncode: int | None) -> None:
    if isinstance(returncode, bool) or not isinstance(returncode, int):
        raise PriorityScopeError("SOC priority scope child status is unavailable")
    if 0 <= returncode <= 255:
        raise SystemExit(returncode)
    if returncode < 0 and -returncode in signal.valid_signals():
        stop_signal = -returncode
        try:
            signal.signal(stop_signal, signal.SIG_DFL)
        except (OSError, RuntimeError, ValueError):
            pass
        try:
            os.kill(os.getpid(), stop_signal)
        except (OSError, OverflowError, ValueError):
            pass
        raise SystemExit(128 + stop_signal)
    raise PriorityScopeError("SOC priority scope child status is unavailable")


def _ensure_soc_priority_scope_for_module(
    argv: Sequence[str],
    *,
    module_name: str,
    normalize_marker_affinity: bool,
) -> bool:
    if module_name not in {
        "speed_of_cinnamon.cli",
        "speed_of_cinnamon.resource_gate",
    } or not isinstance(normalize_marker_affinity, bool):
        raise PriorityScopeError("SOC CLI launch specification is invalid")

    if os.environ.get(SOC_PRIORITY_SCOPE_MARKER) == "1":
        process_id = os.getpid()
        identity = priority_scope_identity_for_pid(
            process_id,
            cpu_weight=SOC_CPU_WEIGHT,
            io_weight=SOC_IO_WEIGHT,
        )
        if identity is None:
            raise PriorityScopeError("SOC priority scope verification failed")
        if normalize_marker_affinity:
            _normalize_cpu_affinity_for_scope_exec()
        if not verify_priority_scope_identity(
            identity,
            pid=process_id,
            cpu_weight=SOC_CPU_WEIGHT,
            io_weight=SOC_IO_WEIGHT,
        ):
            raise PriorityScopeError("SOC priority scope verification failed")
        return True
    try:
        runtime, _entry = _scope_exec_wrapper_paths()
    except PriorityScopeError:
        raise PriorityScopeError("SOC priority scope runtime is unavailable")
    try:
        user_arguments = list(argv)
    except (TypeError, ValueError):
        raise PriorityScopeError("SOC CLI arguments are invalid") from None
    if any(not isinstance(item, str) for item in user_arguments):
        raise PriorityScopeError("SOC CLI arguments are invalid")
    target = _scope_exec_launch_spec(
        [runtime, "-m", module_name, *user_arguments]
    )
    if target is None:
        raise PriorityScopeError("SOC CLI launch specification is invalid")
    try:
        command = build_soc_priority_scope_command(target.argv)
    except _ScopeToolUnavailable:
        if not _scope_exec_launch_spec_is_unchanged(target):
            raise PriorityScopeError("SOC CLI launch specification changed") from None
        return False
    supervisor = _scope_exec_supervisor_spec(command)
    if supervisor is None or supervisor.target != target:
        raise PriorityScopeError("SOC CLI launch specification is invalid")
    try:
        listener, address, nonce = _create_scope_exec_latch()
    except _ScopeExecChannelUnavailable:
        if not _scope_exec_supervisor_spec_is_unchanged(supervisor):
            raise PriorityScopeError("SOC CLI launch specification changed") from None
        return False
    environment = os.environ.copy()
    environment[SOC_PRIORITY_SCOPE_MARKER] = "1"
    environment[_SCOPE_EXEC_LATCH_ADDRESS_ENV] = address
    environment[_SCOPE_EXEC_LATCH_NONCE_ENV] = nonce
    process: subprocess.Popen[bytes] | None = None
    outcome: _ScopeExecAttemptOutcome | None = None
    fallback_allowed = False
    signal_to_propagate: int | None = None
    try:
        with _forward_scope_attempt_signals() as signal_forwarder:
            try:
                process = subprocess.Popen(  # nosec B603
                    list(supervisor.command),
                    env=environment,
                    shell=False,
                    close_fds=True,
                    start_new_session=True,
                )
            except (OSError, OverflowError, TypeError, ValueError):
                launch_changed = not _scope_exec_supervisor_spec_is_unchanged(
                    supervisor,
                    include_scope_tool=False,
                )
                signal_to_propagate = signal_forwarder.begin_error_cleanup()
                if signal_to_propagate is None:
                    if launch_changed:
                        raise PriorityScopeError(
                            "SOC CLI launch specification changed"
                        ) from None
                    fallback_allowed = True
            except BaseException:
                signal_to_propagate = signal_forwarder.begin_error_cleanup()
                if signal_to_propagate is None:
                    raise
            else:
                try:
                    signal_forwarder.bind(process)
                except BaseException:
                    preexisting_signal = signal_forwarder.begin_error_cleanup()
                    _terminate_scope_attempt(
                        process,
                        process_group_id=signal_forwarder.process_group_id,
                        forwarded_signal=(
                            preexisting_signal
                            if signal_forwarder.primary_signal_forwarded
                            else None
                        ),
                    )
                    if preexisting_signal is None:
                        raise
                    signal_to_propagate = preexisting_signal
                else:
                    try:
                        outcome = _supervise_scope_attempt(
                            process,
                            listener,
                            nonce,
                            signal_forwarder,
                        )
                    except BaseException:
                        preexisting_signal = (
                            signal_forwarder.begin_error_cleanup()
                        )
                        _terminate_scope_attempt(
                            process,
                            process_group_id=signal_forwarder.process_group_id,
                            forwarded_signal=(
                                preexisting_signal
                                if signal_forwarder.primary_signal_forwarded
                                else None
                            ),
                        )
                        if preexisting_signal is None:
                            raise
                        signal_to_propagate = preexisting_signal
                    else:
                        if outcome.timed_out:
                            preexisting_signal = (
                                signal_forwarder.begin_error_cleanup()
                            )
                            _terminate_scope_attempt(
                                process,
                                process_group_id=(
                                    signal_forwarder.process_group_id
                                ),
                                forwarded_signal=(
                                    preexisting_signal
                                    if signal_forwarder.primary_signal_forwarded
                                    else None
                                ),
                            )
                            if preexisting_signal is None:
                                raise PriorityScopeError(
                                    "SOC priority scope attempt timed out"
                                )
                            signal_to_propagate = preexisting_signal
                        else:
                            primary_signal = signal_forwarder.primary_signal
                            if primary_signal is not None:
                                _terminate_scope_attempt(
                                    process,
                                    process_group_id=(
                                        signal_forwarder.process_group_id
                                    ),
                                    forwarded_signal=(
                                        primary_signal
                                        if signal_forwarder.primary_signal_forwarded
                                        else None
                                    ),
                                )
                                signal_to_propagate = primary_signal
    finally:
        try:
            listener.close()
        except OSError:
            pass
    if fallback_allowed and signal_to_propagate is None and signal_forwarder.pending:
        signal_to_propagate = signal_forwarder.pending[0]
    if signal_to_propagate is None:
        signal_to_propagate = signal_forwarder.primary_signal
    if signal_to_propagate is not None:
        _raise_scope_attempt_signal(signal_to_propagate)
    if fallback_allowed:
        return False
    if outcome is None:
        raise PriorityScopeError("SOC priority scope child status is unavailable")
    if (
        outcome.latch_state == "none"
        and isinstance(outcome.returncode, int)
        and not isinstance(outcome.returncode, bool)
        and 1 <= outcome.returncode <= 255
    ):
        if not _scope_exec_supervisor_spec_is_unchanged(supervisor):
            raise PriorityScopeError("SOC CLI launch specification changed")
        return False
    _propagate_scope_attempt_returncode(outcome.returncode)
    raise PriorityScopeError("SOC priority scope child status is unavailable")


def ensure_soc_priority_scope(argv: Sequence[str]) -> bool:
    """Supervise one verified high-weight CLI scope or permit one safe fallback."""

    return _ensure_soc_priority_scope_for_module(
        argv,
        module_name="speed_of_cinnamon.cli",
        normalize_marker_affinity=True,
    )


def ensure_resource_gate_priority_scope(argv: Sequence[str]) -> bool:
    """Bootstrap resource-gate checks only inside a verified high-weight scope."""

    return _ensure_soc_priority_scope_for_module(
        argv,
        module_name="speed_of_cinnamon.resource_gate",
        normalize_marker_affinity=False,
    )


def _local_model_cpu_adjustment() -> int:
    current = _current_cpu_priority()
    if current is None:
        raise LocalModelPriorityError(
            "local model priority could not determine CPU priority"
        )
    # Only request a non-negative adjustment. A negative nice adjustment can
    # print Permission denied yet still return the wrapped command's rc=0.
    return max(0, LOCAL_MODEL_CPU_NICE - current)


def _read_ionice_output_bounded(
    argv: list[str],
    *,
    absolute_deadline: float | None = None,
) -> str | None:
    if (
        absolute_deadline is not None
        and (
            type(absolute_deadline) is not float
            or not math.isfinite(absolute_deadline)
            or time.monotonic() >= absolute_deadline
        )
    ):
        return None
    try:
        process = subprocess.Popen(  # nosec B603
            [*argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            env={"LANG": "C", "LC_ALL": "C"},
        )
    except (OSError, OverflowError, ValueError):
        return None
    if process.stdout is None:
        try:
            process.kill()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=IONICE_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        return None

    output = bytearray()
    deadline = time.monotonic() + IONICE_TIMEOUT_SECONDS
    if absolute_deadline is not None:
        deadline = min(deadline, absolute_deadline)
    selector: selectors.BaseSelector | None = None
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(argv, IONICE_TIMEOUT_SECONDS)
            events = selector.select(remaining)
            if not events:
                raise subprocess.TimeoutExpired(argv, IONICE_TIMEOUT_SECONDS)
            for key, _ in events:
                chunk = os.read(
                    key.fileobj.fileno(),
                    IONICE_OUTPUT_READ_CHUNK_BYTES,
                )
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                output.extend(chunk)
                if len(output) > MAX_IONICE_OUTPUT_BYTES:
                    raise ValueError("ionice output is too large")

        remaining = max(0.0, deadline - time.monotonic())
        if process.wait(timeout=remaining) != 0:
            return None
        return bytes(output).decode("utf-8", errors="replace")
    except (OSError, OverflowError, ValueError, UnicodeError, subprocess.TimeoutExpired):
        return None
    finally:
        if selector is not None:
            selector.close()
        if process.poll() is None:
            try:
                process.kill()
            except (OSError, ValueError):
                pass
        try:
            process.wait(timeout=IONICE_REAP_TIMEOUT_SECONDS)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        try:
            process.stdout.close()
        except (OSError, ValueError):
            pass


def _local_model_low_priority_command(argv: Sequence[str]) -> list[str]:
    command = list(argv)
    ionice = _required_priority_tool("ionice")
    nice = _required_priority_tool("nice")
    cpu_adjustment = _local_model_cpu_adjustment()
    return [
        ionice,
        "--class",
        IO_PRIORITY_CLASS,
        "--classdata",
        LOCAL_MODEL_IO_PRIORITY_LEVEL,
        "--",
        nice,
        "--adjustment",
        str(cpu_adjustment),
        *command,
    ]


def local_model_command(
    argv: Sequence[str],
    *,
    unit_name: str | None = None,
) -> list[str]:
    low_priority_command = _local_model_low_priority_command(argv)
    try:
        return build_local_model_priority_scope_command(
            low_priority_command,
            unit_name=unit_name,
        )
    except PriorityScopeError as exc:
        raise LocalModelPriorityError(str(exc).replace("SOC", "local model", 1)) from exc


def local_model_scope_probe_command() -> list[str]:
    return local_model_command([_required_priority_tool("true")])


def local_model_direct_command(argv: Sequence[str]) -> list[str]:
    try:
        verifier = _local_model_direct_exec_wrapper_command(argv)
    except PriorityScopeError as exc:
        raise LocalModelPriorityError(
            str(exc).replace("SOC", "local model", 1)
        ) from exc
    return _local_model_low_priority_command(verifier)


def _handoff_local_model_ready_ack_gate(
    gate: _LocalModelReadyAckGate,
) -> _LocalModelReadyAckGate:
    return gate


def _prepare_local_model_ready_ack_gate(
    argv: Sequence[str],
    *,
    unit_name: str,
    absolute_deadline: float,
) -> _LocalModelReadyAckGate:
    if (
        type(absolute_deadline) is not float
        or not math.isfinite(absolute_deadline)
        or absolute_deadline <= time.monotonic()
        or any(key in os.environ for key in _LOCAL_MODEL_GATE_ENV_KEYS)
    ):
        raise LocalModelPriorityError(_LOCAL_MODEL_GATE_INVALID)
    required_capabilities = (
        getattr(socket, "SOCK_CLOEXEC", None),
        getattr(socket, "SO_PASSCRED", None),
        getattr(socket, "SCM_CREDENTIALS", None),
        getattr(socket, "MSG_CMSG_CLOEXEC", None),
        getattr(socket, "SCM_RIGHTS", None),
        getattr(os, "pidfd_open", None),
        getattr(signal, "pthread_sigmask", None),
        getattr(signal, "valid_signals", None),
    )
    if any(capability is None for capability in required_capabilities) or not all(
        callable(capability) for capability in required_capabilities[-3:]
    ):
        raise LocalModelPriorityError(_LOCAL_MODEL_GATE_CAPABILITY)
    try:
        low_priority_command = _local_model_low_priority_command(argv)
        command = _build_priority_scope_command(
            low_priority_command,
            cpu_weight=LOCAL_MODEL_CPU_WEIGHT,
            io_weight=LOCAL_MODEL_IO_WEIGHT,
            unit_name=unit_name,
            local_model_gate_required=True,
        )
    except (PriorityScopeError, LocalModelPriorityError) as exc:
        raise LocalModelPriorityError(
            str(exc).replace("SOC", "local model", 1)
        ) from None
    process_id = os.getpid()
    uid = os.getuid()
    gid = os.getgid()
    if (
        not _valid_process_id(process_id)
        or type(uid) is not int
        or not 0 <= uid <= (1 << 32) - 1
        or type(gid) is not int
        or not 0 <= gid <= (1 << 32) - 1
    ):
        raise LocalModelPriorityError(_LOCAL_MODEL_GATE_FAILURE)
    controller_identity = _canonical_process_identity(process_id)
    resources = _LocalModelGateResourceOwner()
    gate: _LocalModelReadyAckGate | None = None
    primary: BaseException | None = None
    try:
        if controller_identity is None or not _open_identity_pidfd(
            controller_identity,
            resources.controller_pidfd_owner,
        ) or not _open_local_model_gate_directory(
            resources,
            uid=uid,
            create=True,
        ):
            raise OSError(errno.ENOTSUP, "controller pidfd unavailable")
        ready_nonce = secrets.token_hex(32)
        ack_nonce = secrets.token_hex(32)
        if (
            _LOCAL_MODEL_GATE_NONCE_RE.fullmatch(ready_nonce) is None
            or _LOCAL_MODEL_GATE_NONCE_RE.fullmatch(ack_nonce) is None
            or ready_nonce == ack_nonce
        ):
            raise OSError(errno.EIO, "gate identifiers unavailable")
        def acquire_listener() -> socket.socket:
            endpoint = socket.socket(
                socket.AF_UNIX,
                socket.SOCK_DGRAM | socket.SOCK_CLOEXEC,
            )
            resources.listener_owner[0] = endpoint
            return endpoint

        listener = _run_with_local_model_gate_signals_blocked(acquire_listener)
        if listener is None or listener is not resources.listener_owner[0]:
            raise OSError(errno.EBADF, "gate socket unavailable")
        listener.setblocking(False)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        listener.setsockopt(socket.SOL_SOCKET, _SO_PASSPIDFD, 1)
        address = _bind_local_model_gate_endpoint(
            resources,
            listener,
            prefix=_LOCAL_MODEL_GATE_CONTROLLER_PREFIX,
            uid=uid,
        )
        if address is None:
            raise OSError(errno.EADDRINUSE, "gate address unavailable")
        if not _descriptor_is_cloexec(listener.fileno()):
            raise OSError(errno.ENOTSUP, "descriptor flags unavailable")
        gate = _LocalModelReadyAckGate(
            argv=tuple(command),
            unit_name=unit_name,
            absolute_deadline=absolute_deadline,
            resources=resources,
            address=address,
            ready_nonce=ready_nonce,
            ack_nonce=ack_nonce,
            controller_uid=uid,
            controller_gid=gid,
            controller_identity=controller_identity,
        )
        handed_off = _handoff_local_model_ready_ack_gate(gate)
        if handed_off is not gate:
            raise OSError(errno.EIO, "gate handoff failed")
    except BaseException as exc:
        primary = exc
    if primary is not None or gate is None:
        cleanup_error = resources.close()
        if cleanup_error is not None:
            primary = _preferred_gate_exception(primary, cleanup_error)
    if isinstance(primary, KeyboardInterrupt):
        raise primary
    if primary is not None or gate is None:
        raise LocalModelPriorityError(_LOCAL_MODEL_GATE_CAPABILITY) from None
    return gate


def _current_io_priority(
    *,
    absolute_deadline: float | None = None,
) -> tuple[str, int] | None:
    ionice = shutil.which("ionice", path=_TRUSTED_COMMAND_PATH)
    if not ionice:
        return None
    output = _read_ionice_output_bounded(
        [ionice, "--pid", str(os.getpid())],
        absolute_deadline=absolute_deadline,
    )
    if output is None:
        return None
    match = _IO_PRIORITY_RE.search(output)
    if not match:
        return None
    class_name = match.group(1).lower().replace("real time", "real-time")
    io_class = _IO_CLASS_VALUES.get(class_name)
    if io_class is None:
        return None
    return io_class, int(match.group(2))


def apply_process_priority() -> tuple[bool, bool]:
    """Report the verified high-priority scope; never fake it with nice/ionice."""

    weights = _current_priority_scope_weights()
    if weights is None:
        return False, False
    return weights[0] == SOC_CPU_WEIGHT, weights[1] == SOC_IO_WEIGHT


@contextmanager
def local_model_priority() -> Iterator[None]:
    previous_cpu = _current_cpu_priority()
    previous_io = _current_io_priority()
    # Unprivileged processes cannot reliably raise nice back after lowering it.
    # Only change CPU priority where restoration is permitted; external local
    # model commands are already launched with a dedicated nice wrapper.
    lower_cpu = previous_cpu is not None and os.geteuid() == 0
    if lower_cpu:
        _set_cpu_priority(LOCAL_MODEL_CPU_NICE)
    # Do not mutate the parent process when its current I/O priority cannot
    # be observed safely. The external local-model wrapper remains the
    # preferred low-priority boundary in that case.
    lower_io = previous_io is not None
    if lower_io:
        _set_io_priority(LOCAL_MODEL_IO_PRIORITY_LEVEL)
    try:
        yield
    finally:
        if lower_cpu and previous_cpu is not None:
            _set_cpu_priority(previous_cpu)
        if lower_io and previous_io is not None:
            previous_io_class, previous_io_level = previous_io
            _set_io_priority(
                str(previous_io_level),
                io_class=previous_io_class,
            )


def with_local_model_priority(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with local_model_priority():
            return function(*args, **kwargs)

    return wrapped


def _scope_exec_main() -> None:
    direct = bool(
        len(sys.argv) > 1 and sys.argv[1] == _LOCAL_MODEL_DIRECT_EXEC_TOKEN
    )
    try:
        if direct:
            _run_local_model_direct_supervisor(sys.argv[1:])
        else:
            _run_scope_exec_wrapper(sys.argv[1:])
    except PriorityScopeError as exc:
        failure = str(exc)
        allowed_failures = (
            frozenset({_LOCAL_MODEL_DIRECT_EXEC_FAILURE})
            if direct
            else _SCOPE_EXEC_PHASE_FAILURES
        )
        if failure not in allowed_failures:
            failure = (
                _LOCAL_MODEL_DIRECT_EXEC_FAILURE
                if direct
                else _SCOPE_EXEC_FAILURE
            )
        raise SystemExit(failure) from None


if __name__ == "__main__":
    _scope_exec_main()
