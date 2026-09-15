import errno
import fcntl
import os
import select
import signal
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from speed_of_cinnamon import process_priority


_READY_GATE_CAPABILITY_ERRNOS = frozenset(
    value
    for value in (
        errno.EACCES,
        errno.EAFNOSUPPORT,
        errno.ENOPROTOOPT,
        errno.ENOSYS,
        errno.EOPNOTSUPP,
        errno.EPERM,
        errno.EPROTONOSUPPORT,
    )
    if isinstance(value, int)
)


def _ready_gate_capability_unavailable_reason() -> str | None:
    required_attributes = (
        (socket, "SCM_CREDENTIALS"),
        (socket, "MSG_CMSG_CLOEXEC"),
        (process_priority, "_SO_PASSPIDFD"),
        (process_priority, "_SCM_PIDFD"),
    )
    for owner, name in required_attributes:
        if not hasattr(owner, name):
            return f"local model ready gate host capability is unavailable: missing {name}"
    pidfd_open = getattr(os, "pidfd_open", None)
    if not callable(pidfd_open):
        return "local model ready gate host capability is unavailable: missing pidfd_open"

    endpoints: list[socket.socket] = []
    descriptors: list[int] = []
    received_pidfds: list[int] = []
    stage = "AF_UNIX"
    try:
        receiver, sender = socket.socketpair(
            socket.AF_UNIX,
            socket.SOCK_DGRAM | getattr(socket, "SOCK_CLOEXEC", 0),
        )
        endpoints.extend((receiver, sender))
        receiver.settimeout(0.25)
        for endpoint_name, endpoint in (("receiver", receiver), ("sender", sender)):
            stage = f"{endpoint_name} SO_PASSCRED"
            endpoint.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            stage = f"{endpoint_name} SO_PASSPIDFD"
            endpoint.setsockopt(
                socket.SOL_SOCKET,
                process_priority._SO_PASSPIDFD,
                1,
            )

        stage = "pidfd_open"
        descriptor = pidfd_open(os.getpid(), 0)
        descriptors.append(descriptor)
        if not fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
            raise AssertionError("pidfd_open did not return a CLOEXEC descriptor")

        stage = "SCM_CREDENTIALS/SCM_PIDFD receive"
        sender.send(b"x")
        data, ancillary, message_flags, _ = receiver.recvmsg(
            1,
            socket.CMSG_SPACE(struct.calcsize("3i"))
            + socket.CMSG_SPACE(struct.calcsize("i")),
            socket.MSG_CMSG_CLOEXEC,
        )
        if data != b"x" or message_flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
            raise AssertionError("ready gate capability probe received invalid datagram")

        credentials: list[tuple[int, int, int]] = []
        for level, kind, payload in ancillary:
            if level != socket.SOL_SOCKET:
                continue
            if kind == socket.SCM_CREDENTIALS and len(payload) >= struct.calcsize("3i"):
                credentials.append(struct.unpack("3i", payload[: struct.calcsize("3i")]))
            elif kind == process_priority._SCM_PIDFD:
                usable = len(payload) - (len(payload) % struct.calcsize("i"))
                for (received,) in struct.iter_unpack("i", payload[:usable]):
                    received_pidfds.append(received)

        expected_credentials = (os.getpid(), os.getuid(), os.getgid())
        if expected_credentials not in credentials:
            return (
                "local model ready gate host capability is unavailable: "
                "SCM_CREDENTIALS was not delivered"
            )
        if len(received_pidfds) != 1:
            return (
                "local model ready gate host capability is unavailable: "
                "SCM_PIDFD was not delivered"
            )
        os.fstat(received_pidfds[0])
        if not fcntl.fcntl(received_pidfds[0], fcntl.F_GETFD) & fcntl.FD_CLOEXEC:
            raise AssertionError("received pidfd is not CLOEXEC")
    except OSError as exc:
        if exc.errno in _READY_GATE_CAPABILITY_ERRNOS:
            error_name = errno.errorcode.get(exc.errno, str(exc.errno))
            return (
                "local model ready gate host capability is unavailable: "
                f"{stage} failed with {error_name}"
            )
        raise
    finally:
        for descriptor in received_pidfds:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for endpoint in reversed(endpoints):
            endpoint.close()
    return None


class LocalModelPriorityTests(unittest.TestCase):
    def test_local_model_scope_lowers_and_restores_cpu_and_io_priority(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=-5),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("2", 4)),
            mock.patch.object(process_priority.os, "geteuid", return_value=0),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True) as set_cpu,
            mock.patch.object(process_priority, "_set_io_priority", return_value=True) as set_io,
        ):
            with process_priority.local_model_priority():
                pass

        self.assertEqual(
            set_cpu.call_args_list,
            [mock.call(process_priority.LOCAL_MODEL_CPU_NICE), mock.call(-5)],
        )
        self.assertEqual(
            set_io.call_args_list,
            [
                mock.call(process_priority.LOCAL_MODEL_IO_PRIORITY_LEVEL),
                mock.call("4", io_class="2"),
            ],
        )

    def test_local_model_scope_restores_non_default_io_class(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("3", 0)),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True),
            mock.patch.object(process_priority, "_set_io_priority", return_value=True) as set_io,
        ):
            with process_priority.local_model_priority():
                pass

        self.assertEqual(
            set_io.call_args_list,
            [
                mock.call(process_priority.LOCAL_MODEL_IO_PRIORITY_LEVEL),
                mock.call("0", io_class="3"),
            ],
        )

    def test_local_model_scope_does_not_stick_nice_for_unprivileged_process(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("2", 4)),
            mock.patch.object(process_priority.os, "geteuid", return_value=1000),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True) as set_cpu,
            mock.patch.object(process_priority, "_set_io_priority", return_value=True),
        ):
            with process_priority.local_model_priority():
                pass

        set_cpu.assert_not_called()

    def test_local_model_scope_does_not_lower_inherited_negative_nice_unprivileged(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=-5),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("2", 4)),
            mock.patch.object(process_priority.os, "geteuid", return_value=1000),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True) as set_cpu,
            mock.patch.object(process_priority, "_set_io_priority", return_value=True) as set_io,
        ):
            with process_priority.local_model_priority():
                pass

        set_cpu.assert_not_called()
        self.assertEqual(
            set_io.call_args_list,
            [
                mock.call(process_priority.LOCAL_MODEL_IO_PRIORITY_LEVEL),
                mock.call("4", io_class="2"),
            ],
        )

    def test_local_model_scope_does_not_change_io_when_previous_value_is_unknown(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
            mock.patch.object(process_priority, "_current_io_priority", return_value=None),
            mock.patch.object(process_priority, "_set_cpu_priority", return_value=True) as set_cpu,
            mock.patch.object(process_priority, "_set_io_priority", return_value=True) as set_io,
        ):
            with process_priority.local_model_priority():
                pass

        set_cpu.assert_not_called()
        set_io.assert_not_called()

    def test_local_model_command_adds_low_priority_wrappers(self) -> None:
        with mock.patch.object(
            process_priority.shutil,
            "which",
            side_effect=lambda name, path=None: f"/usr/bin/{name}",
        ), mock.patch.object(process_priority, "_current_cpu_priority", return_value=0):
            command = process_priority.local_model_command(
                ["/usr/bin/whisper", "audio.flac"]
            )

        scope_delimiter = command.index("--")
        self.assertEqual(
            command[: scope_delimiter + 1],
            [
                "/usr/bin/systemd-run",
                "--user",
                "--scope",
                "--quiet",
                "--expand-environment=no",
                "-p",
                "CPUWeight=10",
                "-p",
                "IOWeight=10",
                "--",
            ],
        )
        wrapper_delimiter = command.index("--", scope_delimiter + 1)
        wrapper = command[scope_delimiter + 1 : wrapper_delimiter + 1]
        self.assertEqual(
            wrapper[:4],
            [
                os.path.realpath(sys.executable),
                "-I",
                os.path.realpath(process_priority.__file__),
                process_priority._SCOPE_EXEC_WRAPPER_TOKEN,
            ],
        )
        self.assertEqual(len(wrapper[4:-1]), 6)
        self.assertTrue(all(value.isdecimal() for value in wrapper[4:-1]))
        self.assertEqual(wrapper[-1], "--")
        self.assertTrue(os.path.isabs(wrapper[0]))
        self.assertTrue(os.path.isabs(wrapper[2]))
        self.assertNotIn("-m", wrapper)
        self.assertNotIn(
            process_priority._LOCAL_MODEL_GATE_REQUIRED_TOKEN,
            wrapper,
        )
        self.assertFalse(any(item.startswith("--unit=") for item in command))
        self.assertEqual(
            command[wrapper_delimiter + 1 :],
            [
                "/usr/bin/ionice",
                "--class",
                "2",
                "--classdata",
                "7",
                "--",
                "/usr/bin/nice",
                "--adjustment",
                "10",
                "/usr/bin/whisper",
                "audio.flac",
            ],
        )

    def test_local_model_command_passes_valid_named_scope_without_reordering(
        self,
    ) -> None:
        unit_name = "soc-ct2-benchmark-runtime-a.scope"
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(
                process_priority,
                "_current_cpu_priority",
                return_value=0,
            ),
        ):
            default = process_priority.local_model_command(
                ["/usr/bin/whisper", "audio.flac"]
            )
            named = process_priority.local_model_command(
                ["/usr/bin/whisper", "audio.flac"],
                unit_name=unit_name,
            )

        unit_option = f"--unit={unit_name}"
        self.assertEqual(named.count(unit_option), 1)
        self.assertEqual(
            named[named.index("--expand-environment=no") + 1],
            unit_option,
        )
        without_unit = named.copy()
        without_unit.remove(unit_option)
        self.assertEqual(without_unit, default)
        self.assertEqual(
            named[named.index("/usr/bin/ionice") :],
            default[default.index("/usr/bin/ionice") :],
        )

    def test_local_model_command_rejects_invalid_named_scope(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(
                process_priority,
                "_current_cpu_priority",
                return_value=0,
            ),
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "priority scope unit name is invalid",
            ),
        ):
            process_priority.local_model_command(
                ["/usr/bin/whisper"],
                unit_name="../ct2.scope",
            )

    def test_local_model_command_uses_nonnegative_adjustment_for_inherited_high_nice(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=-5),
        ):
            command = process_priority.local_model_command(["/usr/bin/whisper"])

        self.assertEqual(command[command.index("--adjustment") + 1], "15")
        self.assertGreaterEqual(int(command[command.index("--adjustment") + 1]), 0)

    def test_local_model_command_keeps_exact_nice_ten_inherited_priority(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
        ):
            command = process_priority.local_model_command(["/usr/bin/whisper"])

        self.assertEqual(command[command.index("--adjustment") + 1], "10")

    def test_local_model_command_keeps_inherited_priority_above_ten(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=12),
        ):
            command = process_priority.local_model_command(["/usr/bin/whisper"])

        self.assertEqual(command[command.index("--adjustment") + 1], "0")

    def test_local_model_command_fails_closed_when_ionice_is_missing(self) -> None:
        with mock.patch.object(
            process_priority.shutil,
            "which",
            side_effect=lambda name, path=None: None if name == "ionice" else f"/usr/bin/{name}",
        ):
            with self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model priority helper is unavailable: ionice",
            ):
                process_priority.local_model_command(["/usr/bin/whisper"])

    def test_local_model_command_fails_closed_when_nice_is_missing(self) -> None:
        with mock.patch.object(
            process_priority.shutil,
            "which",
            side_effect=lambda name, path=None: None if name == "nice" else f"/usr/bin/{name}",
        ):
            with self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model priority helper is unavailable: nice",
            ):
                process_priority.local_model_command(["/usr/bin/whisper"])

    def test_local_model_command_fails_closed_when_cpu_priority_is_unknown(self) -> None:
        with (
            mock.patch.object(process_priority.shutil, "which", return_value="/usr/bin/tool"),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=None),
        ):
            with self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "could not determine CPU priority",
            ):
                process_priority.local_model_command(["/usr/bin/whisper"])

    def test_local_model_command_fails_closed_when_systemd_run_is_missing(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: None if name == "systemd-run" else f"/usr/bin/{name}",
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
        ):
            with self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model priority scope helper is unavailable: systemd-run",
            ):
                process_priority.local_model_command(["/usr/bin/whisper"])

    def test_local_model_scope_probe_uses_same_low_weight_scope(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
        ):
            command = process_priority.local_model_scope_probe_command()

        self.assertEqual(command[command.index("CPUWeight=10") + 2], "IOWeight=10")
        self.assertEqual(command[-1], "/usr/bin/true")
        self.assertIn("/usr/bin/ionice", command)
        self.assertIn("/usr/bin/nice", command)

    def test_local_model_direct_command_orders_wrappers_before_verifier(self) -> None:
        with (
            mock.patch.object(
                process_priority.shutil,
                "which",
                side_effect=lambda name, path=None: f"/usr/bin/{name}",
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=0),
        ):
            command = process_priority.local_model_direct_command(["/usr/bin/true"])

        verifier = command.index(process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN)
        self.assertEqual(
            command[:verifier],
            [
                "/usr/bin/ionice",
                "--class",
                "2",
                "--classdata",
                "7",
                "--",
                "/usr/bin/nice",
                "--adjustment",
                "10",
                os.path.realpath(sys.executable),
                "-I",
                os.path.realpath(process_priority.__file__),
            ],
        )
        self.assertEqual(command[-2:], ["--", "/usr/bin/true"])

    def test_direct_verifier_rejects_wrong_priority_before_target(self) -> None:
        wrapper = process_priority._local_model_direct_exec_wrapper_command(
            ["/usr/bin/true"]
        )
        arguments = wrapper[
            wrapper.index(process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN) :
        ]
        for cpu_priority, io_priority in ((9, ("2", 7)), (10, ("2", 6)), (10, None)):
            with self.subTest(cpu_priority=cpu_priority, io_priority=io_priority):
                with (
                    mock.patch.dict(
                        process_priority.os.environ,
                        {"PATH": "/usr/bin:/bin"},
                        clear=True,
                    ),
                    mock.patch.object(
                        process_priority,
                        "_current_cpu_priority",
                        return_value=cpu_priority,
                    ),
                    mock.patch.object(
                        process_priority,
                        "_current_io_priority",
                        return_value=io_priority,
                    ),
                    mock.patch.object(process_priority.os, "execve") as target,
                ):
                    with self.assertRaisesRegex(
                        process_priority.PriorityScopeError,
                        "direct priority wrapper failed",
                    ):
                        process_priority._run_local_model_direct_exec_wrapper(arguments)

                target.assert_not_called()

    def test_direct_verifier_binds_target_fd_and_requires_cloexec(self) -> None:
        wrapper = process_priority._local_model_direct_exec_wrapper_command(
            ["/usr/bin/true"]
        )
        arguments = wrapper[
            wrapper.index(process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN) :
        ]

        real_open = process_priority.os.open
        real_close = process_priority.os.close

        def close_tracking(expected_launch_fd: int):
            identity_fds: list[int] = []

            def tracked_open(*args, **kwargs):
                descriptor = real_open(*args, **kwargs)
                identity_fds.append(descriptor)
                return descriptor

            def checked_close(descriptor: int) -> None:
                if descriptor in identity_fds:
                    real_close(descriptor)
                    return
                if descriptor != expected_launch_fd:
                    raise AssertionError(f"unexpected descriptor close: {descriptor}")

            return identity_fds, tracked_open, checked_close

        identity_fds, tracked_open, checked_close = close_tracking(91)
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {"PATH": "/usr/bin:/bin"},
                clear=True,
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=10),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("2", 7)),
            mock.patch.object(process_priority, "_open_scope_exec_launch_fd", return_value=91),
            mock.patch.object(process_priority, "_descriptor_is_cloexec", return_value=True),
            mock.patch.object(process_priority.os, "open", side_effect=tracked_open),
            mock.patch.object(process_priority.os, "close", side_effect=checked_close) as close,
            mock.patch.object(
                process_priority.os,
                "execve",
                side_effect=OSError("synthetic exec failure"),
            ) as target,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "direct priority wrapper failed",
            ):
                process_priority._run_local_model_direct_exec_wrapper(arguments)

        target.assert_called_once()
        self.assertEqual(target.call_args.args[0], 91)
        self.assertEqual(len(identity_fds), 1)
        self.assertEqual(
            close.call_args_list,
            [mock.call(identity_fds[0]), mock.call(91)],
        )

        identity_fds, tracked_open, checked_close = close_tracking(92)
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {"PATH": "/usr/bin:/bin"},
                clear=True,
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=10),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("2", 7)),
            mock.patch.object(process_priority, "_open_scope_exec_launch_fd", return_value=92),
            mock.patch.object(process_priority, "_descriptor_is_cloexec", return_value=False),
            mock.patch.object(process_priority.os, "open", side_effect=tracked_open),
            mock.patch.object(process_priority.os, "close", side_effect=checked_close) as close,
            mock.patch.object(process_priority.os, "execve") as target,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "direct priority wrapper failed",
            ):
                process_priority._run_local_model_direct_exec_wrapper(arguments)

        target.assert_not_called()
        self.assertEqual(len(identity_fds), 1)
        self.assertEqual(
            close.call_args_list,
            [mock.call(identity_fds[0]), mock.call(92)],
        )

    def test_direct_verifier_rejects_identity_drift_and_systemd_environment(self) -> None:
        wrapper = process_priority._local_model_direct_exec_wrapper_command(
            ["/usr/bin/true"]
        )
        arguments = wrapper[
            wrapper.index(process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN) :
        ]
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {"PATH": "/usr/bin:/bin"},
                clear=True,
            ),
            mock.patch.object(process_priority, "_current_cpu_priority", return_value=10),
            mock.patch.object(process_priority, "_current_io_priority", return_value=("2", 7)),
            mock.patch.object(process_priority, "_open_scope_exec_launch_fd", return_value=None),
            mock.patch.object(process_priority.os, "execve") as target,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "direct priority wrapper failed",
            ):
                process_priority._run_local_model_direct_exec_wrapper(arguments)
        target.assert_not_called()

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/forbidden"},
                clear=True,
            ),
            mock.patch.object(process_priority.os, "execve") as target,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "direct priority wrapper failed",
            ):
                process_priority._run_local_model_direct_exec_wrapper(arguments)
        target.assert_not_called()


class LocalModelDirectHardeningRegressionTests(unittest.TestCase):
    def test_direct_detection_ignores_plain_target_token_arguments(self) -> None:
        token = process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN
        command = process_priority._local_model_direct_exec_wrapper_command(
            ["/usr/bin/true", token, token]
        )
        self.assertTrue(
            process_priority._is_local_model_direct_supervisor_command(command)
        )
        runtime, entry = process_priority._scope_exec_wrapper_paths()
        ambiguous = [*command, runtime, "-I", entry, token]
        self.assertFalse(
            process_priority._is_local_model_direct_supervisor_command(ambiguous)
        )

    def test_supervisor_dumpability_is_set_and_verified(self) -> None:
        with mock.patch.object(
            process_priority,
            "_local_model_direct_prctl",
            side_effect=(0, 0),
        ) as prctl:
            self.assertTrue(
                process_priority._harden_local_model_direct_supervisor()
            )
        self.assertEqual(
            prctl.call_args_list,
            [
                mock.call(process_priority._PR_SET_DUMPABLE, 0),
                mock.call(process_priority._PR_GET_DUMPABLE),
            ],
        )

        with mock.patch.object(
            process_priority,
            "_local_model_direct_prctl",
            side_effect=(0, 1),
        ):
            self.assertFalse(
                process_priority._harden_local_model_direct_supervisor()
            )

    def test_children_overflow_is_returned_as_incomplete_batch(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_read_affinity_file",
                return_value="11 12 13\n",
            ),
            mock.patch.object(
                process_priority,
                "_LOCAL_MODEL_DIRECT_MAX_CHILDREN",
                2,
            ),
        ):
            self.assertEqual(
                process_priority._local_model_direct_children_batch(),
                ({11, 12}, False),
            )
            self.assertIsNone(process_priority._local_model_direct_children())

    def test_drain_signals_every_incomplete_child_batch(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_local_model_direct_reap",
                side_effect=[({}, False), ({}, False), ({}, True)],
            ),
            mock.patch.object(
                process_priority,
                "_local_model_direct_children_batch",
                side_effect=[({11, 12}, False), ({13}, True), (set(), True)],
            ),
            mock.patch.object(
                process_priority,
                "_canonical_process_identity",
                return_value=mock.sentinel.identity,
            ) as canonical_identity,
            mock.patch.object(
                process_priority,
                "_local_model_direct_signal_identity",
                return_value=True,
            ) as signal_identity,
            mock.patch.object(process_priority.time, "sleep"),
        ):
            self.assertEqual(
                process_priority._drain_local_model_direct_children(99, -1, None),
                (None, True),
            )
        for process_id in (11, 12, 13):
            self.assertIn(
                mock.call(process_id),
                canonical_identity.call_args_list,
            )
        self.assertEqual(signal_identity.call_count, 3)
        self.assertTrue(
            all(
                call.args == (mock.sentinel.identity, signal.SIGTERM)
                for call in signal_identity.call_args_list
            )
        )

    def test_expired_io_probe_deadline_never_spawns(self) -> None:
        with (
            mock.patch.object(process_priority.time, "monotonic", return_value=2.0),
            mock.patch.object(process_priority.subprocess, "Popen") as popen,
        ):
            self.assertIsNone(
                process_priority._read_ionice_output_bounded(
                    ["/usr/bin/ionice", "--pid", "1"],
                    absolute_deadline=1.0,
                )
            )
        popen.assert_not_called()

    def test_verifier_rechecks_deadline_after_io_probe(self) -> None:
        wrapper = process_priority._local_model_direct_exec_wrapper_command(
            ["/usr/bin/true"]
        )
        arguments = wrapper[
            wrapper.index(process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN) :
        ]
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {"PATH": "/usr/bin:/bin"},
                clear=True,
            ),
            mock.patch.object(
                process_priority.time,
                "monotonic",
                side_effect=[10.0, 12.0],
            ),
            mock.patch.object(
                process_priority,
                "_current_cpu_priority",
                return_value=10,
            ),
            mock.patch.object(
                process_priority,
                "_current_io_priority",
                return_value=("2", 7),
            ) as io_priority,
            mock.patch.object(
                process_priority,
                "_open_scope_exec_launch_fd",
            ) as open_target,
            mock.patch.object(process_priority.os, "execve") as target,
            self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "direct priority wrapper failed",
            ),
        ):
            process_priority._run_local_model_direct_exec_wrapper(
                arguments,
                absolute_deadline=11.0,
            )
        io_priority.assert_called_once_with(absolute_deadline=11.0)
        open_target.assert_not_called()
        target.assert_not_called()


class LocalModelDirectSupervisorProtocolTests(unittest.TestCase):
    def _frame(
        self,
        *,
        outcome: int = process_priority._LOCAL_MODEL_DIRECT_OUTCOME_TARGET,
        flags: int = process_priority._LOCAL_MODEL_DIRECT_ALL_FLAGS,
        root_pid: int = 123,
        wait_status: int = 0,
        root_start: int = 456,
    ) -> bytes:
        return process_priority._LOCAL_MODEL_DIRECT_STATUS_STRUCT.pack(
            process_priority._LOCAL_MODEL_DIRECT_STATUS_MAGIC,
            process_priority._LOCAL_MODEL_DIRECT_STATUS_VERSION,
            outcome,
            flags,
            root_pid,
            wait_status,
            root_start,
        )

    def test_internal_python_wrappers_are_isolated(self) -> None:
        wrapper = process_priority._scope_exec_wrapper_command(["/usr/bin/true"])
        direct = process_priority._local_model_direct_exec_wrapper_command(
            ["/usr/bin/true"]
        )
        self.assertEqual(wrapper[1], "-I")
        self.assertEqual(direct[1], "-I")
        self.assertEqual(
            direct[3],
            process_priority._LOCAL_MODEL_DIRECT_EXEC_TOKEN,
        )

    def test_status_frame_accepts_only_bound_clean_target_result(self) -> None:
        payload = self._frame()
        self.assertEqual(
            process_priority._parse_local_model_direct_supervisor_status(
                payload,
                0,
            ),
            ("target", 0),
        )
        for malformed in (payload[:-1], payload + b"x", b""):
            with self.subTest(size=len(malformed)):
                self.assertIsNone(
                    process_priority._parse_local_model_direct_supervisor_status(
                        malformed,
                        0,
                    )
                )

    def test_status_frame_rejects_missing_security_or_returncode_mismatch(self) -> None:
        self.assertIsNone(
            process_priority._parse_local_model_direct_supervisor_status(
                self._frame(
                    flags=(
                        process_priority._LOCAL_MODEL_DIRECT_ALL_FLAGS
                        & ~process_priority._LOCAL_MODEL_DIRECT_FLAG_SECURITY_ARMED
                    )
                ),
                0,
            )
        )
        self.assertIsNone(
            process_priority._parse_local_model_direct_supervisor_status(
                self._frame(),
                1,
            )
        )


class LocalModelReadyAckGateTests(unittest.TestCase):
    _UNIT_NAME = "soc-ct2-benchmark-test.scope"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls._ready_gate_capability_skip_reason = (
            _ready_gate_capability_unavailable_reason()
        )

    def setUp(self) -> None:
        self._runtime_directory = tempfile.TemporaryDirectory(dir="/dev/shm")
        os.chmod(self._runtime_directory.name, 0o700)
        self._sender_paths: list[str] = []

    def tearDown(self) -> None:
        for path in self._sender_paths:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        self._runtime_directory.cleanup()

    def _prepare(
        self,
        *,
        deadline: float | None = None,
        runtime_directory: str | None = None,
        require_host_capability: bool = True,
    ):
        if require_host_capability and self._ready_gate_capability_skip_reason:
            self.skipTest(self._ready_gate_capability_skip_reason)
        runtime = os.path.realpath(sys.executable)
        with (
            mock.patch.dict(
                os.environ,
                {
                    "XDG_RUNTIME_DIR": (
                        self._runtime_directory.name
                        if runtime_directory is None
                        else runtime_directory
                    )
                },
            ),
            mock.patch.object(
                process_priority,
                "_required_scope_tool",
                return_value=runtime,
            ),
            mock.patch.object(
                process_priority,
                "_required_priority_tool",
                return_value=runtime,
            ),
            mock.patch.object(
                process_priority,
                "_current_cpu_priority",
                return_value=0,
            ),
        ):
            return process_priority._prepare_local_model_ready_ack_gate(
                [runtime, "-c", "raise SystemExit(0)"],
                unit_name=self._UNIT_NAME,
                absolute_deadline=(
                    time.monotonic() + 5.0 if deadline is None else deadline
                ),
            )

    def _sender(self, environment: dict[str, str]) -> socket.socket:
        sender = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_DGRAM | socket.SOCK_CLOEXEC,
        )
        sender.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sender.setsockopt(
            socket.SOL_SOCKET,
            process_priority._SO_PASSPIDFD,
            1,
        )
        gate_directory = os.path.dirname(
            environment[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV]
        )
        sender_path = os.path.join(
            gate_directory,
            f"wrapper-{os.urandom(16).hex()}.sock",
        )
        sender.bind(sender_path)
        os.chmod(sender_path, 0o600)
        self._sender_paths.append(sender_path)
        sender.connect(environment[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV])
        return sender

    def _wrapper_environment(self, gate) -> dict[str, str]:
        return {
            **gate.environment_overlay,
            "XDG_RUNTIME_DIR": self._runtime_directory.name,
        }

    @staticmethod
    def _close_ancillary_descriptors(
        ancillary: list[tuple[int, int, bytes]],
    ) -> None:
        for level, kind, data in ancillary:
            if level != socket.SOL_SOCKET or kind not in {
                process_priority._SCM_PIDFD,
                socket.SCM_RIGHTS,
            }:
                continue
            usable = len(data) - (len(data) % struct.calcsize("i"))
            for (descriptor,) in struct.iter_unpack("i", data[:usable]):
                os.close(descriptor)

    def _scope_verification(self, process_id: int):
        identity = process_priority.PriorityScopeIdentity(
            path=f"/trusted/{self._UNIT_NAME}",
            device=7,
            inode=11,
        )
        return (
            mock.patch.object(
                process_priority,
                "priority_scope_identity_for_pid",
                return_value=identity,
            ),
            mock.patch.object(
                process_priority,
                "priority_scope_membership",
                return_value=process_priority.PriorityScopeMembership(
                    process_ids=(process_id,),
                    populated=True,
                ),
            ),
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
                return_value=True,
            ),
        )

    def _retire_for_release(self, gate, sender: socket.socket) -> None:
        source = sender.getsockname()
        gate._listener_owner[0].connect(source)
        gate._ready_source = source
        self.assertTrue(
            process_priority._retire_local_model_gate_domain(
                gate._resources,
                peer_source=source,
            )
        )
        gate._state = "ready_verified"

    def test_ready_ack_gate_happy_path_blocks_until_verified_release(self) -> None:
        gate = self._prepare()
        environment = gate.environment_overlay
        gate_directory = os.path.dirname(
            environment[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV]
        )
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        completed = threading.Event()
        errors: list[BaseException] = []

        def wrapper_gate() -> None:
            try:
                copied = {
                    **environment,
                    process_priority.SOC_PRIORITY_SCOPE_MARKER: "1",
                    process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "0" * 32,
                    process_priority._SCOPE_EXEC_LATCH_NONCE_ENV: "0" * 64,
                }
                configuration = process_priority._local_model_gate_from_environment(
                    copied
                )
                self.assertNotIn(
                    process_priority.SOC_PRIORITY_SCOPE_MARKER,
                    copied,
                )
                self.assertNotIn(
                    process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV,
                    copied,
                )
                self.assertNotIn(
                    process_priority._SCOPE_EXEC_LATCH_NONCE_ENV,
                    copied,
                )
                for key in process_priority._LOCAL_MODEL_GATE_ENV_KEYS:
                    self.assertNotIn(key, copied)
                with mock.patch.dict(
                    process_priority.os.environ,
                    {"XDG_RUNTIME_DIR": self._runtime_directory.name},
                ):
                    process_priority._wait_for_local_model_gate_ack(configuration)
            except BaseException as exc:  # test thread must report exact failure
                errors.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=wrapper_gate, daemon=True)
        worker.start()
        try:
            readable, _, _ = select.select([gate.fileno()], [], [], 2.0)
            self.assertEqual(readable, [gate.fileno()])
            self.assertFalse(completed.is_set())
            identity_patch, membership_patch, verify_patch = (
                self._scope_verification(process_id)
            )
            with identity_patch as identity_for_pid, membership_patch as membership, verify_patch as verify:
                self.assertTrue(gate.try_verify_ready())
                self.assertFalse(completed.is_set())
                self.assertFalse(os.path.lexists(gate_directory))
                self.assertTrue(gate.release())
            worker.join(2.0)
            self.assertFalse(worker.is_alive())
            self.assertTrue(completed.is_set())
            self.assertEqual(errors, [])
            self.assertFalse(os.path.lexists(gate_directory))
            self.assertTrue(gate.release_may_have_occurred)
            self.assertEqual(gate.state, "released")
            identity_for_pid.assert_called_once_with(
                process_id,
                cpu_weight=process_priority.LOCAL_MODEL_CPU_WEIGHT,
                io_weight=process_priority.LOCAL_MODEL_IO_WEIGHT,
            )
            self.assertEqual(membership.call_count, 4)
            self.assertEqual(verify.call_count, 4)
            for checked in (membership, verify):
                for call in checked.call_args_list:
                    self.assertEqual(
                        call.kwargs,
                        {
                            **(
                                {"pid": process_id}
                                if checked is verify
                                else {}
                            ),
                            "cpu_weight": process_priority.LOCAL_MODEL_CPU_WEIGHT,
                            "io_weight": process_priority.LOCAL_MODEL_IO_WEIGHT,
                        },
                    )
        finally:
            gate.close()
            worker.join(2.0)

    def test_real_named_low_scope_stays_blocked_until_ack(self) -> None:
        runtime_directory = os.environ.get("XDG_RUNTIME_DIR")
        session_bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
        if not runtime_directory or not session_bus:
            self.skipTest("user session runtime is unavailable")
        required_capabilities = (
            getattr(os, "pidfd_open", None),
            getattr(signal, "pthread_sigmask", None),
            getattr(socket, "SO_PASSCRED", None),
            getattr(socket, "SCM_CREDENTIALS", None),
            getattr(socket, "MSG_CMSG_CLOEXEC", None),
            getattr(socket, "SOCK_CLOEXEC", None),
        )
        if any(item is None for item in required_capabilities) or not all(
            callable(item) for item in required_capabilities[:2]
        ):
            self.skipTest("named low-scope gate capabilities are unavailable")
        pidfd = -1
        try:
            pidfd = os.pidfd_open(os.getpid(), 0)
        except OSError as exc:
            if exc.errno in {
                errno.EACCES,
                errno.EINVAL,
                errno.ENOSYS,
                errno.ENOTSUP,
                errno.EPERM,
            }:
                self.skipTest("pidfd capability is unavailable")
            raise
        finally:
            if pidfd >= 0:
                os.close(pidfd)
        capability_socket = socket.socket(
            socket.AF_UNIX,
            socket.SOCK_DGRAM | socket.SOCK_CLOEXEC,
        )
        try:
            capability_socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            capability_socket.setsockopt(
                socket.SOL_SOCKET,
                process_priority._SO_PASSPIDFD,
                1,
            )
        except OSError as exc:
            if exc.errno in {
                errno.EACCES,
                errno.EINVAL,
                errno.ENOPROTOOPT,
                errno.ENOTSUP,
                errno.EPERM,
            }:
                self.skipTest("named low-scope socket capability is unavailable")
            raise
        finally:
            capability_socket.close()
        tools = {
            name: shutil.which(name, path=process_priority._TRUSTED_COMMAND_PATH)
            for name in ("systemd-run", "ionice", "nice", "true")
        }
        if any(path is None for path in tools.values()):
            self.skipTest("trusted low-scope tools are unavailable")
        systemd_run = tools["systemd-run"]
        ionice = tools["ionice"]
        nice = tools["nice"]
        true = tools["true"]
        assert systemd_run is not None
        assert ionice is not None
        assert nice is not None
        assert true is not None
        resolved_ionice = os.path.realpath(ionice)
        session_environment = os.environ.copy()
        session_environment["XDG_RUNTIME_DIR"] = runtime_directory
        session_environment["DBUS_SESSION_BUS_ADDRESS"] = session_bus
        try:
            preflight = subprocess.run(  # nosec B603
                [
                    systemd_run,
                    "--user",
                    "--scope",
                    "--quiet",
                    "--expand-environment=no",
                    "--",
                    true,
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=session_environment,
                close_fds=True,
                shell=False,
                start_new_session=True,
                timeout=10.0,
            )
        except subprocess.TimeoutExpired:
            self.fail("user systemd scope preflight timed out")
        unavailable_markers = (
            b"failed to connect to bus: no medium found",
            b"failed to connect to bus: no such file or directory",
            b"failed to create bus connection: no medium found",
            b"failed to create bus connection: no such file or directory",
            b"failed to connect to user scope bus via local transport: operation not permitted",
        )
        bounded_error = preflight.stderr[:4096].lower()
        if (
            preflight.returncode != 0
            and len(preflight.stderr) <= 4096
            and any(marker in bounded_error for marker in unavailable_markers)
        ):
            self.skipTest("user systemd scope manager is unavailable")
        self.assertEqual(preflight.returncode, 0, "user systemd preflight failed")
        self.assertEqual(preflight.stdout, b"")
        self.assertEqual(preflight.stderr, b"")
        runtime = os.path.realpath(sys.executable)
        unit_name = f"soc-ct2-gate-{os.getpid()}-{os.urandom(4).hex()}.scope"
        scrubbed_keys = (
            *process_priority._LOCAL_MODEL_GATE_ENV_KEYS,
            process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV,
            process_priority._SCOPE_EXEC_LATCH_NONCE_ENV,
            process_priority.SOC_PRIORITY_SCOPE_MARKER,
        )
        target = (
            "import os; expected_runtime="
            + repr(runtime_directory)
            + "; keys="
            + repr(scrubbed_keys)
            + "; os.write(1, b'leak\\n' if "
            "os.environ.get('XDG_RUNTIME_DIR') != expected_runtime or "
            "any(k in os.environ for k in keys) "
            "else b'target\\n')"
        )
        absolute_deadline = time.monotonic() + 30.0
        with mock.patch.dict(os.environ, session_environment, clear=True):
            gate = process_priority._prepare_local_model_ready_ack_gate(
                [runtime, "-c", target],
                unit_name=unit_name,
                absolute_deadline=absolute_deadline,
            )
        self.addCleanup(gate.close)
        self.assertEqual(gate.argv[0], os.path.realpath(systemd_run))
        ionice_index = gate.argv.index(resolved_ionice)
        nice_index = gate.argv.index(nice)
        self.assertLess(ionice_index, nice_index)
        self.assertEqual(
            gate.argv[ionice_index : ionice_index + 6],
            (
                resolved_ionice,
                "--class",
                process_priority.IO_PRIORITY_CLASS,
                "--classdata",
                process_priority.LOCAL_MODEL_IO_PRIORITY_LEVEL,
                "--",
            ),
        )
        self.assertEqual(
            gate.argv[nice_index : nice_index + 2],
            (nice, "--adjustment"),
        )
        self.assertEqual(gate.argv[nice_index + 3 :], (runtime, "-c", target))
        self.assertEqual(
            os.path.dirname(
                os.path.dirname(
                    gate.environment_overlay[
                        process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
                    ]
                )
            ),
            runtime_directory,
        )
        environment = session_environment.copy()
        environment.update(gate.environment_overlay)
        self.assertEqual(environment["XDG_RUNTIME_DIR"], runtime_directory)
        process: subprocess.Popen[bytes] | None = None
        try:
            process = subprocess.Popen(  # nosec B603
                list(gate.argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                close_fds=True,
                shell=False,
                start_new_session=True,
            )
            gate.bind_spawn(process.pid)
            remaining = absolute_deadline - time.monotonic()
            if remaining <= 0:
                self.fail("named low-scope gate deadline expired before READY")
            ready, _, _ = select.select([gate.fileno()], [], [], remaining)
            self.assertEqual(ready, [gate.fileno()])
            self.assertTrue(gate.try_verify_ready())
            self.assertIsNone(process.poll())
            assert process.stdout is not None
            readable, _, _ = select.select([process.stdout], [], [], 0.0)
            self.assertEqual(readable, [])
            self.assertTrue(gate.release())
            remaining = absolute_deadline - time.monotonic()
            if remaining <= 0:
                self.fail("named low-scope gate deadline expired before target exit")
            stdout, stderr = process.communicate(timeout=remaining)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(stdout, b"target\n")
            self.assertEqual(stderr, b"")
        finally:
            gate.close()
            if process is not None and process.poll() is None:
                process_priority._terminate_scope_attempt(
                    process,
                    process_group_id=process.pid,
                )
            if process is not None:
                for stream in (process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()

    def test_gate_listener_and_bound_pidfds_are_cloexec_and_close_idempotently(
        self,
    ) -> None:
        gate = self._prepare()
        gate.bind_spawn(os.getpid())
        listener_fd = gate.fileno()
        self.assertTrue(fcntl.fcntl(listener_fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
        self.assertTrue(gate.bound_pidfd_cloexec)

        gate.close()
        gate.close()

        with self.assertRaises(OSError):
            os.fstat(listener_fd)
        self.assertEqual(gate.state, "closed")

    def test_gate_uses_private_pathname_endpoint_and_removes_it_on_close(
        self,
    ) -> None:
        gate = self._prepare()
        endpoint = gate.environment_overlay[
            process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
        ]
        gate_directory = os.path.dirname(endpoint)

        self.assertTrue(os.path.isabs(endpoint))
        self.assertFalse(endpoint.startswith("\0"))
        self.assertEqual(os.path.dirname(gate_directory), self._runtime_directory.name)
        self.assertEqual(os.stat(self._runtime_directory.name).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(gate_directory).st_mode & 0o777, 0o700)
        self.assertEqual(os.lstat(endpoint).st_mode & 0o777, 0o600)

        gate.close()

        self.assertFalse(os.path.lexists(endpoint))

    def test_domain_scans_use_independent_directory_descriptions(self) -> None:
        gate = self._prepare()
        domain = os.path.dirname(
            gate.environment_overlay[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV]
        )
        unexpected = os.path.join(domain, "unexpected")
        with open(unexpected, "xb"):
            pass
        gate_descriptor = gate._resources.directory_descriptors[1]
        expected = set(os.listdir(domain))
        real_open = os.open
        real_scandir = os.scandir
        scan_opens: list[tuple[int, int]] = []
        scan_descriptors: list[int] = []

        def tracking_open(path, flags, mode=0o777, *, dir_fd=None):
            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
            if path == "." and dir_fd == gate_descriptor:
                scan_opens.append((flags, descriptor))
            return descriptor

        def tracking_scandir(path=None):
            if type(path) is int:
                scan_descriptors.append(path)
                self.assertNotEqual(path, gate_descriptor)
            return real_scandir(path)

        try:
            with (
                mock.patch.object(
                    process_priority.os,
                    "open",
                    side_effect=tracking_open,
                ),
                mock.patch.object(
                    process_priority.os,
                    "scandir",
                    side_effect=tracking_scandir,
                ),
            ):
                first = process_priority._local_model_gate_domain_entries(
                    gate_descriptor
                )
                second = process_priority._local_model_gate_domain_entries(
                    gate_descriptor
                )

            self.assertEqual(set(first), expected)
            self.assertEqual(set(second), expected)
            self.assertEqual(len(first), len(expected))
            self.assertEqual(len(second), len(expected))
            self.assertEqual(len(scan_opens), 2)
            self.assertEqual(len(scan_descriptors), 2)
            required = (
                os.O_DIRECTORY
                | os.O_NOFOLLOW
                | os.O_CLOEXEC
                | os.O_NONBLOCK
            )
            for flags, descriptor in scan_opens:
                self.assertEqual(flags & required, required)
                with self.assertRaises(OSError):
                    os.fstat(descriptor)
        finally:
            os.unlink(unexpected)
            gate.close()

    def test_gate_domain_creation_never_reuses_eexist(self) -> None:
        first_name = "g-" + "a" * 32
        second_name = "g-" + "b" * 32
        first_path = os.path.join(self._runtime_directory.name, first_name)
        os.mkdir(first_path, 0o700)
        sentinel = os.path.join(first_path, "sentinel")
        with open(sentinel, "xb"):
            pass

        with mock.patch.object(
            process_priority,
            "_new_local_model_gate_domain_name",
            side_effect=[first_name, second_name],
        ) as new_name:
            gate = self._prepare()
        try:
            endpoint = gate.environment_overlay[
                process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
            ]
            self.assertEqual(os.path.basename(os.path.dirname(endpoint)), second_name)
            self.assertTrue(os.path.isfile(sentinel))
            self.assertEqual(new_name.call_count, 2)
        finally:
            gate.close()
            os.unlink(sentinel)
            os.rmdir(first_path)

    def test_domain_replacement_is_untouched_and_cleanup_retries(self) -> None:
        gate = self._prepare()
        endpoint = gate.environment_overlay[
            process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
        ]
        domain = os.path.dirname(endpoint)
        moved_domain = domain + "-moved"
        os.rename(domain, moved_domain)
        os.mkdir(domain, 0o700)
        sentinel = os.path.join(domain, "sentinel")
        with open(sentinel, "xb"):
            pass

        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate failed",
        ):
            gate.close()

        self.assertNotEqual(gate.state, "closed")
        self.assertTrue(os.path.isfile(sentinel))
        self.assertFalse(os.path.lexists(os.path.join(moved_domain, os.path.basename(endpoint))))
        os.unlink(sentinel)
        os.rmdir(domain)
        os.rename(moved_domain, domain)

        gate.close()

        self.assertEqual(gate.state, "closed")
        self.assertFalse(os.path.lexists(domain))

    def test_disposable_socket_symlink_never_removes_its_target(self) -> None:
        gate = self._prepare()
        endpoint = gate.environment_overlay[
            process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
        ]
        target = os.path.join(self._runtime_directory.name, "foreign-data")
        with open(target, "xb") as output:
            output.write(b"keep")
        gate._listener_owner[0].close()
        gate._listener_owner[0] = None
        os.unlink(endpoint)
        os.symlink(target, endpoint)

        gate.close()

        self.assertEqual(gate.state, "closed")
        self.assertFalse(os.path.lexists(endpoint))
        with open(target, "rb") as source:
            self.assertEqual(source.read(), b"keep")
        os.unlink(target)

    def test_unexpected_domain_entry_blocks_retirement_and_ack(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        domain = os.path.dirname(
            gate.environment_overlay[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV]
        )
        unexpected = os.path.join(domain, "unexpected")
        with open(unexpected, "xb"):
            pass
        valid = process_priority._LocalModelGateDatagram(
            message=(
                process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                + gate.environment_overlay[
                    process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                ].encode("ascii")
            ),
            credentials=((process_id, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        identity_patch, membership_patch, verify_patch = self._scope_verification(
            process_id
        )
        with (
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                side_effect=[valid, BlockingIOError(errno.EAGAIN, "empty")],
            ),
            identity_patch,
            membership_patch,
            verify_patch,
            mock.patch.object(process_priority.socket.socket, "send") as send,
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ),
        ):
            gate.try_verify_ready()

        send.assert_not_called()
        self.assertTrue(os.path.isfile(unexpected))
        self.assertFalse(gate.release_may_have_occurred)
        self.assertNotEqual(gate.state, "closed")
        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate failed",
        ):
            gate.close()
        self.assertTrue(os.path.isfile(unexpected))
        os.unlink(unexpected)
        gate.close()
        sender.close()
        self.assertEqual(gate.state, "closed")

    def test_rmdir_replacement_is_detected_before_ready_or_ack(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        domain = os.path.dirname(
            gate.environment_overlay[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV]
        )
        moved_domain = domain + "-moved"
        binding = gate._resources.binding
        self.assertIsNotNone(binding)
        runtime_descriptor, gate_descriptor = gate._resources.directory_descriptors
        valid = process_priority._LocalModelGateDatagram(
            message=(
                process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                + gate.environment_overlay[
                    process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                ].encode("ascii")
            ),
            credentials=((process_id, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        identity_patch, membership_patch, verify_patch = self._scope_verification(
            process_id
        )
        real_rmdir = os.rmdir
        replaced = False

        def replace_before_rmdir(path, *, dir_fd=None):
            nonlocal replaced
            if not replaced and path == binding.gate_name and dir_fd == runtime_descriptor:
                replaced = True
                os.rename(domain, moved_domain)
                os.mkdir(domain, 0o700)
            return real_rmdir(path, dir_fd=dir_fd)

        try:
            with (
                mock.patch.object(
                    process_priority,
                    "_receive_local_model_gate_datagram",
                    side_effect=[valid, BlockingIOError(errno.EAGAIN, "empty")],
                ),
                identity_patch,
                membership_patch,
                verify_patch,
                mock.patch.object(
                    process_priority.os,
                    "rmdir",
                    side_effect=replace_before_rmdir,
                ),
                mock.patch.object(process_priority.socket.socket, "send") as send,
                self.assertRaisesRegex(
                    process_priority.LocalModelPriorityError,
                    "local model ready gate failed",
                ),
            ):
                gate.try_verify_ready()

            self.assertTrue(replaced)
            send.assert_not_called()
            self.assertFalse(gate.release_may_have_occurred)
            self.assertIs(gate._resources.binding, binding)
            self.assertEqual(
                (os.fstat(gate_descriptor).st_dev, os.fstat(gate_descriptor).st_ino),
                binding.gate_identity[:2],
            )
            self.assertGreater(os.fstat(gate_descriptor).st_nlink, 0)
            os.fstat(runtime_descriptor)
            self.assertFalse(os.path.lexists(domain))
            self.assertTrue(os.path.isdir(moved_domain))
        finally:
            sender.close()
            if os.path.isdir(moved_domain) and not os.path.lexists(domain):
                os.rename(moved_domain, domain)
            gate.close()

    def test_clean_retirement_proves_original_unlinked_before_ack(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        source = sender.getsockname()
        gate._listener_owner[0].connect(source)
        real_rmdir = os.rmdir
        real_path_state = process_priority._local_model_gate_domain_path_state
        rmdir_completed = False
        post_rmdir_states: list[str] = []

        def tracking_rmdir(path, *, dir_fd=None):
            nonlocal rmdir_completed
            result = real_rmdir(path, dir_fd=dir_fd)
            rmdir_completed = True
            return result

        def tracking_path_state(owner):
            result = real_path_state(owner)
            if rmdir_completed:
                post_rmdir_states.append(result)
            return result

        try:
            with (
                mock.patch.object(
                    process_priority.os,
                    "rmdir",
                    side_effect=tracking_rmdir,
                ),
                mock.patch.object(
                    process_priority,
                    "_local_model_gate_domain_path_state",
                    side_effect=tracking_path_state,
                ),
            ):
                self.assertTrue(
                    process_priority._retire_local_model_gate_domain(
                        gate._resources,
                        peer_source=source,
                    )
                )

            self.assertEqual(post_rmdir_states, ["retired"])
            self.assertTrue(
                process_priority._local_model_gate_domain_is_retired(
                    gate._resources
                )
            )
            self.assertEqual(gate._listener_owner[0].send(b"x"), 1)
            sender.settimeout(1.0)
            self.assertEqual(sender.recv(1), b"x")
        finally:
            sender.close()
            gate.close()

    def test_close_failure_is_retryable_and_only_empty_owner_closes_gate(self) -> None:
        gate = self._prepare()
        original_rmdir = process_priority.os.rmdir
        calls = 0

        def fail_once(path, *, dir_fd=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.EIO, "injected rmdir failure")
            return original_rmdir(path, dir_fd=dir_fd)

        with (
            mock.patch.object(
                process_priority.os,
                "rmdir",
                side_effect=fail_once,
            ),
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ),
        ):
            gate.close()

        self.assertNotEqual(gate.state, "closed")
        gate.close()
        self.assertEqual(gate.state, "closed")

    def test_context_manager_preserves_body_exception_over_cleanup(self) -> None:
        gate = self._prepare()
        body_error = ValueError("body")
        cleanup_error = process_priority.LocalModelPriorityError(
            "private cleanup detail"
        )
        with (
            mock.patch.object(
                process_priority._LocalModelReadyAckGate,
                "close",
                side_effect=cleanup_error,
            ),
            self.assertRaises(ValueError) as raised,
        ):
            with gate:
                raise body_error

        self.assertIs(raised.exception, body_error)
        self.assertNotIn("private cleanup detail", " ".join(body_error.__notes__))
        gate.close()

    def test_context_manager_propagates_cleanup_failure_without_body_error(self) -> None:
        gate = self._prepare()
        cleanup_error = KeyboardInterrupt("cleanup")
        with (
            mock.patch.object(
                process_priority._LocalModelReadyAckGate,
                "close",
                side_effect=cleanup_error,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            with gate:
                pass

        self.assertIs(raised.exception, cleanup_error)
        gate.close()

    def test_prepare_failure_removes_bound_pathname_endpoint(self) -> None:
        captured: dict[str, str] = {}
        bind_endpoint = process_priority._bind_local_model_gate_endpoint

        def bind_then_fail(*args, **kwargs):
            endpoint = bind_endpoint(*args, **kwargs)
            self.assertIsNotNone(endpoint)
            self.assertIsNotNone(args[0].endpoint.identity)
            captured["endpoint"] = endpoint
            raise OSError(errno.EIO, "injected failure")

        with (
            mock.patch.object(
                process_priority,
                "_bind_local_model_gate_endpoint",
                side_effect=bind_then_fail,
            ),
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate capability is unavailable",
            ),
        ):
            self._prepare()

        self.assertFalse(os.path.lexists(captured["endpoint"]))

    def test_partial_bind_chmod_failure_removes_owned_socket(self) -> None:
        captured: dict[str, str] = {}

        def fail_endpoint_chmod(path, _mode, *, dir_fd, follow_symlinks):
            self.assertFalse(follow_symlinks)
            domains = os.listdir(self._runtime_directory.name)
            self.assertEqual(len(domains), 1)
            captured["endpoint"] = os.path.join(
                self._runtime_directory.name,
                domains[0],
                path,
            )
            raise OSError(errno.EIO, "injected chmod failure")

        with (
            mock.patch.object(
                process_priority.os,
                "chmod",
                side_effect=fail_endpoint_chmod,
            ),
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate capability is unavailable",
            ),
        ):
            self._prepare()

        self.assertFalse(os.path.lexists(captured["endpoint"]))

    def test_bind_replacement_before_identity_capture_is_domain_confined(self) -> None:
        original_stat = process_priority.os.stat

        for replacement_kind in ("regular", "symlink"):
            with self.subTest(replacement_kind=replacement_kind):
                captured: dict[str, str] = {}
                replaced = False

                def replace_before_capture(path, *args, **kwargs):
                    nonlocal replaced
                    directory_descriptor = kwargs.get("dir_fd")
                    if (
                        not replaced
                        and type(path) is str
                        and path.startswith(
                            process_priority._LOCAL_MODEL_GATE_CONTROLLER_PREFIX
                        )
                        and directory_descriptor is not None
                        and kwargs.get("follow_symlinks") is False
                    ):
                        replaced = True
                        domains = os.listdir(self._runtime_directory.name)
                        self.assertEqual(len(domains), 1)
                        captured["path"] = os.path.join(
                            self._runtime_directory.name,
                            domains[0],
                            path,
                        )
                        os.unlink(path, dir_fd=directory_descriptor)
                        if replacement_kind == "regular":
                            descriptor = os.open(
                                path,
                                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                                0o600,
                                dir_fd=directory_descriptor,
                            )
                            os.close(descriptor)
                        else:
                            target = os.path.join(
                                self._runtime_directory.name,
                                "outside-domain-target",
                            )
                            with open(target, "xb"):
                                pass
                            captured["target"] = target
                            os.symlink(
                                target,
                                path,
                                dir_fd=directory_descriptor,
                            )
                    return original_stat(path, *args, **kwargs)

                with (
                    mock.patch.object(
                        process_priority.os,
                        "stat",
                        side_effect=replace_before_capture,
                    ),
                    self.assertRaisesRegex(
                        process_priority.LocalModelPriorityError,
                        "local model ready gate capability is unavailable",
                    ),
                ):
                    self._prepare()

                self.assertTrue(replaced)
                self.assertFalse(os.path.lexists(captured["path"]))
                if "target" in captured:
                    self.assertTrue(os.path.isfile(captured["target"]))
                    os.unlink(captured["target"])

    def test_gate_rejects_symlinked_runtime_ancestor(self) -> None:
        real_parent = os.path.join(self._runtime_directory.name, "real")
        runtime_directory = os.path.join(real_parent, "runtime")
        alias = os.path.join(self._runtime_directory.name, "alias")
        os.mkdir(real_parent, 0o700)
        os.mkdir(runtime_directory, 0o700)
        os.symlink(real_parent, alias)

        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate capability is unavailable",
        ):
            self._prepare(
                runtime_directory=os.path.join(alias, "runtime"),
                require_host_capability=False,
            )

    def test_gate_rejects_foreign_renameable_runtime_ancestor(self) -> None:
        unsafe_parent = os.path.join(self._runtime_directory.name, "unsafe")
        runtime_directory = os.path.join(unsafe_parent, "runtime")
        os.mkdir(unsafe_parent, 0o777)
        os.chmod(unsafe_parent, 0o777)
        os.mkdir(runtime_directory, 0o700)

        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate capability is unavailable",
        ):
            self._prepare(
                runtime_directory=runtime_directory,
                require_host_capability=False,
            )

    def test_runtime_ancestry_is_opened_componentwise_without_following(self) -> None:
        runtime_name = os.path.basename(self._runtime_directory.name)
        with mock.patch.object(
            process_priority.os,
            "open",
            wraps=os.open,
        ) as opened:
            gate = self._prepare()
        try:
            self.assertFalse(
                any(
                    call.args
                    and call.args[0] == self._runtime_directory.name
                    for call in opened.call_args_list
                )
            )
            component_calls = [
                call
                for call in opened.call_args_list
                if call.args and call.args[0] == runtime_name
            ]
            self.assertGreaterEqual(len(component_calls), 2)
            required_flags = (
                os.O_DIRECTORY
                | os.O_NOFOLLOW
                | os.O_CLOEXEC
                | os.O_NONBLOCK
            )
            for call in component_calls:
                self.assertIsInstance(call.kwargs.get("dir_fd"), int)
                self.assertEqual(call.args[1] & required_flags, required_flags)
        finally:
            gate.close()

    def test_gate_rejects_runtime_directory_without_exact_private_mode(self) -> None:
        os.chmod(self._runtime_directory.name, 0o750)
        try:
            with self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate capability is unavailable",
            ):
                self._prepare(require_host_capability=False)
        finally:
            os.chmod(self._runtime_directory.name, 0o700)

        self.assertEqual(os.listdir(self._runtime_directory.name), [])

    def test_recvmsg_owns_cloexec_pidfd_until_it_is_closed(self) -> None:
        descriptor = os.pidfd_open(os.getpid(), 0)
        self.assertTrue(fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
        receiver = mock.Mock()
        receiver.recvmsg.return_value = (
            b"message",
            [
                (
                    socket.SOL_SOCKET,
                    socket.SCM_CREDENTIALS,
                    struct.pack("3i", os.getpid(), os.getuid(), os.getgid()),
                ),
                (
                    socket.SOL_SOCKET,
                    process_priority._SCM_PIDFD,
                    struct.pack("i", descriptor),
                ),
            ],
            socket.MSG_CMSG_CLOEXEC,
            b"\0" + b"a" * 32,
        )

        datagram = process_priority._receive_local_model_gate_datagram(
            receiver,
            max_bytes=128,
        )

        self.assertEqual(datagram.pidfd_process_ids, (os.getpid(),))
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_gate_rejects_each_wrong_low_scope_weight_without_ack(self) -> None:
        for wrong_weight in ("cpu", "io"):
            with self.subTest(wrong_weight=wrong_weight):
                weight_directory = os.path.join(
                    self._runtime_directory.name,
                    f"weights-{wrong_weight}",
                )
                os.mkdir(weight_directory, 0o700)
                values = {
                    "cpu.weight": "11\n" if wrong_weight == "cpu" else "10\n",
                    "io.weight": (
                        "default 11\n"
                        if wrong_weight == "io"
                        else "default 10\n"
                    ),
                }
                for name, value in values.items():
                    with open(
                        os.path.join(weight_directory, name),
                        "w",
                        encoding="ascii",
                    ) as stream:
                        stream.write(value)
                weight_descriptor = os.open(
                    weight_directory,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
                )
                gate = self._prepare()
                sender = self._sender(gate.environment_overlay)
                process_id = os.getpid()
                gate.bind_spawn(process_id)
                sender.send(
                    process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                    + gate.environment_overlay[
                        process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                    ].encode("ascii")
                )
                identity = process_priority.PriorityScopeIdentity(
                    path=f"/trusted/{self._UNIT_NAME}",
                    device=7,
                    inode=11,
                )

                def identity_from_bound_weights(
                    requested_process_id,
                    *,
                    cpu_weight,
                    io_weight,
                ):
                    self.assertEqual(requested_process_id, process_id)
                    cpu_snapshot = process_priority._read_scope_file_snapshot(
                        weight_descriptor,
                        "cpu.weight",
                        max_bytes=process_priority.MAX_CGROUP_FILE_BYTES,
                    )
                    io_snapshot = process_priority._read_scope_file_snapshot(
                        weight_descriptor,
                        "io.weight",
                        max_bytes=process_priority.MAX_CGROUP_FILE_BYTES,
                    )
                    if cpu_snapshot is None or io_snapshot is None:
                        return None
                    if (
                        process_priority._parse_cgroup_weight_contents(
                            cpu_snapshot.contents
                        )
                        != cpu_weight
                        or process_priority._parse_cgroup_weight_contents(
                            io_snapshot.contents,
                            io_weight=True,
                        )
                        != io_weight
                    ):
                        return None
                    return identity

                try:
                    with (
                        mock.patch.object(
                            process_priority,
                            "priority_scope_identity_for_pid",
                            side_effect=identity_from_bound_weights,
                        ) as identity_for_pid,
                        self.assertRaisesRegex(
                            process_priority.LocalModelPriorityError,
                            "local model ready gate failed",
                        ),
                    ):
                        gate.try_verify_ready()
                    identity_for_pid.assert_called_once_with(
                        process_id,
                        cpu_weight=10,
                        io_weight=10,
                    )
                    self.assertEqual(gate.state, "invalid")
                    self.assertFalse(gate.release_may_have_occurred)
                    sender.settimeout(0.05)
                    with self.assertRaises((TimeoutError, socket.timeout)):
                        sender.recv(256)
                finally:
                    os.close(weight_descriptor)
                    sender.close()
                    gate.close()

    def test_gate_requires_stable_populated_singleton_membership(self) -> None:
        process_id = os.getpid()
        identity = process_priority.PriorityScopeIdentity(
            path=f"/trusted/{self._UNIT_NAME}",
            device=7,
            inode=11,
        )
        for membership in (
            process_priority.PriorityScopeMembership(
                process_ids=(process_id, process_id + 1),
                populated=True,
            ),
            process_priority.PriorityScopeMembership(
                process_ids=(process_id,),
                populated=False,
            ),
        ):
            with self.subTest(membership=repr(membership)):
                gate = self._prepare()
                sender = self._sender(gate.environment_overlay)
                gate.bind_spawn(process_id)
                sender.send(
                    process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                    + gate.environment_overlay[
                        process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                    ].encode("ascii")
                )
                with (
                    mock.patch.object(
                        process_priority,
                        "priority_scope_identity_for_pid",
                        return_value=identity,
                    ),
                    mock.patch.object(
                        process_priority,
                        "priority_scope_membership",
                        return_value=membership,
                    ) as membership_probe,
                    mock.patch.object(
                        process_priority,
                        "verify_priority_scope_identity",
                        return_value=True,
                    ),
                    self.assertRaisesRegex(
                        process_priority.LocalModelPriorityError,
                        "local model ready gate failed",
                    ),
                ):
                    gate.try_verify_ready()
                membership_probe.assert_called_once_with(
                    identity,
                    cpu_weight=process_priority.LOCAL_MODEL_CPU_WEIGHT,
                    io_weight=process_priority.LOCAL_MODEL_IO_WEIGHT,
                )
                self.assertFalse(gate.release_may_have_occurred)
                sender.close()
                gate.close()

    def test_foreign_datagram_flood_is_bounded_and_remains_pending(self) -> None:
        gate = self._prepare()
        gate.bind_spawn(os.getpid())
        foreign = process_priority._LocalModelGateDatagram(
            message=b"foreign",
            credentials=((999_999, os.getuid(), os.getgid()),),
            pidfd_process_ids=(),
            flags=0,
            source=b"\0" + b"f" * 32,
            unknown_ancillary=False,
        )
        with mock.patch.object(
            process_priority,
            "_receive_local_model_gate_datagram",
            return_value=foreign,
        ) as receive:
            self.assertFalse(gate.try_verify_ready())

        self.assertEqual(
            receive.call_count,
            process_priority._LOCAL_MODEL_GATE_RECEIVE_BUDGET,
        )
        self.assertEqual(gate.state, "spawn_bound")
        gate.close()

    def test_foreign_wrong_pid_is_discarded_before_valid_ready(self) -> None:
        gate = self._prepare()
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        foreign = process_priority._LocalModelGateDatagram(
            message=b"foreign",
            credentials=((process_id + 1, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id + 1,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=b"\0" + b"f" * 32,
            unknown_ancillary=False,
        )
        with (
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                side_effect=[foreign, BlockingIOError(errno.EAGAIN, "pending")],
            ) as receive,
        ):
            self.assertFalse(gate.try_verify_ready())

        self.assertEqual(receive.call_count, 2)
        self.assertEqual(gate.state, "spawn_bound")
        gate.close()

    def test_duplicate_attributable_ready_is_terminal(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        ready = (
            process_priority._LOCAL_MODEL_GATE_READY_PREFIX
            + gate.environment_overlay[
                process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
            ].encode("ascii")
        )
        sender.send(ready)
        sender.send(ready)
        identity_patch, membership_patch, verify_patch = self._scope_verification(
            process_id
        )
        with (
            identity_patch,
            membership_patch,
            verify_patch,
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ),
        ):
            gate.try_verify_ready()

        self.assertEqual(gate.state, "invalid")
        self.assertFalse(gate.release_may_have_occurred)
        sender.close()
        gate.close()

    def test_foreign_datagram_after_valid_ready_is_ignored(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        valid = process_priority._LocalModelGateDatagram(
            message=(
                process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                + gate.environment_overlay[
                    process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                ].encode("ascii")
            ),
            credentials=((process_id, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        foreign = process_priority._LocalModelGateDatagram(
            message=b"foreign",
            credentials=((process_id + 1, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id + 1,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=b"\0" + b"f" * 32,
            unknown_ancillary=False,
        )
        identity_patch, membership_patch, verify_patch = self._scope_verification(
            process_id
        )
        with (
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                side_effect=[
                    valid,
                    foreign,
                    BlockingIOError(errno.EAGAIN, "empty"),
                ],
            ) as receive,
            identity_patch,
            membership_patch,
            verify_patch,
        ):
            self.assertTrue(gate.try_verify_ready())

        self.assertEqual(receive.call_count, 3)
        self.assertEqual(gate.state, "ready_verified")
        sender.close()
        gate.close()

    def test_ready_drain_budget_stays_pending_then_rejects_duplicate(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        valid = process_priority._LocalModelGateDatagram(
            message=(
                process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                + gate.environment_overlay[
                    process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                ].encode("ascii")
            ),
            credentials=((process_id, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        foreign = process_priority._LocalModelGateDatagram(
            message=b"foreign",
            credentials=((process_id + 1, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id + 1,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        identity_patch, membership_patch, verify_patch = self._scope_verification(
            process_id
        )
        with (
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                side_effect=[
                    valid,
                    *(
                        foreign
                        for _ in range(
                            process_priority._LOCAL_MODEL_GATE_RECEIVE_BUDGET
                        )
                    ),
                    valid,
                ],
            ) as receive,
            identity_patch as identity_for_pid,
            membership_patch,
            verify_patch,
        ):
            self.assertFalse(gate.try_verify_ready())
            self.assertEqual(gate.state, "ready_draining")
            with self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ):
                gate.try_verify_ready()

        self.assertEqual(
            receive.call_count,
            process_priority._LOCAL_MODEL_GATE_RECEIVE_BUDGET + 2,
        )
        self.assertEqual(identity_for_pid.call_count, 1)
        self.assertEqual(gate.state, "invalid")
        sender.close()
        gate.close()

    def test_ready_drain_budget_eventually_observes_empty_queue(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        process_id = os.getpid()
        gate.bind_spawn(process_id)
        valid = process_priority._LocalModelGateDatagram(
            message=(
                process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                + gate.environment_overlay[
                    process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                ].encode("ascii")
            ),
            credentials=((process_id, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        foreign = process_priority._LocalModelGateDatagram(
            message=b"foreign",
            credentials=((process_id + 1, os.getuid(), os.getgid()),),
            pidfd_process_ids=(process_id + 1,),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=sender.getsockname(),
            unknown_ancillary=False,
        )
        identity_patch, membership_patch, verify_patch = self._scope_verification(
            process_id
        )
        with (
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                side_effect=[
                    valid,
                    *(
                        foreign
                        for _ in range(
                            process_priority._LOCAL_MODEL_GATE_RECEIVE_BUDGET
                        )
                    ),
                    BlockingIOError(errno.EAGAIN, "empty"),
                ],
            ),
            identity_patch as identity_for_pid,
            membership_patch,
            verify_patch,
        ):
            self.assertFalse(gate.try_verify_ready())
            self.assertEqual(gate.state, "ready_draining")
            self.assertTrue(gate.try_verify_ready())

        self.assertEqual(identity_for_pid.call_count, 1)
        self.assertEqual(gate.state, "ready_verified")
        sender.close()
        gate.close()

    def test_attributable_protocol_and_ancillary_failures_are_terminal(self) -> None:
        process_id = os.getpid()
        cases = {
            "missing-pidfd": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (),
            },
            "duplicate-credentials": {
                "credentials": (
                    (process_id, os.getuid(), os.getgid()),
                    (process_id, os.getuid(), os.getgid()),
                ),
                "pidfd_process_ids": (process_id,),
            },
            "duplicate-pidfd": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id, process_id),
            },
            "unknown-ancillary": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id,),
                "unknown_ancillary": True,
            },
            "truncated": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id,),
                "flags": socket.MSG_TRUNC,
            },
            "control-truncated": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id,),
                "flags": socket.MSG_CMSG_CLOEXEC | socket.MSG_CTRUNC,
            },
            "malformed-message": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id,),
                "message": b"wrong",
            },
            "malformed-source": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id,),
            },
            "wrong-uid": {
                "credentials": ((process_id, os.getuid() + 1, os.getgid()),),
                "pidfd_process_ids": (process_id,),
            },
            "wrong-gid": {
                "credentials": ((process_id, os.getuid(), os.getgid() + 1),),
                "pidfd_process_ids": (process_id,),
            },
            "mismatched-pidfd": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (process_id + 1,),
            },
            "dead-pidfd": {
                "credentials": ((process_id, os.getuid(), os.getgid()),),
                "pidfd_process_ids": (None,),
            },
        }
        for failure, changes in cases.items():
            with self.subTest(failure=failure):
                gate = self._prepare()
                sender = self._sender(gate.environment_overlay)
                gate.bind_spawn(process_id)
                sender_source = sender.getsockname()
                source = sender_source
                malformed_sender: socket.socket | None = None
                if failure == "malformed-source":
                    malformed_sender = socket.socket(
                        socket.AF_UNIX,
                        socket.SOCK_DGRAM | socket.SOCK_CLOEXEC,
                    )
                    source = os.path.join(
                        os.path.dirname(source),
                        "invalid.sock",
                    )
                    malformed_sender.bind(source)
                    os.chmod(source, 0o600)
                    self._sender_paths.append(source)
                valid_message = (
                    process_priority._LOCAL_MODEL_GATE_READY_PREFIX
                    + gate.environment_overlay[
                        process_priority._LOCAL_MODEL_GATE_READY_NONCE_ENV
                    ].encode("ascii")
                )
                values = {
                    "message": valid_message,
                    "credentials": (),
                    "pidfd_process_ids": (),
                    "flags": socket.MSG_CMSG_CLOEXEC,
                    "source": source,
                    "unknown_ancillary": False,
                    **changes,
                }
                datagram = process_priority._LocalModelGateDatagram(**values)
                with (
                    mock.patch.object(
                        process_priority,
                        "_receive_local_model_gate_datagram",
                        return_value=datagram,
                    ),
                    mock.patch.object(
                        process_priority,
                        "priority_scope_identity_for_pid",
                    ) as scope_probe,
                    self.assertRaisesRegex(
                        process_priority.LocalModelPriorityError,
                        "local model ready gate failed",
                    ),
                ):
                    gate.try_verify_ready()
                scope_probe.assert_not_called()
                self.assertEqual(gate.state, "invalid")
                sender.close()
                if malformed_sender is not None:
                    malformed_sender.close()
                    for disposable_path in (sender_source, source):
                        try:
                            os.unlink(disposable_path)
                        except FileNotFoundError:
                            pass
                gate.close()

    def test_release_latch_is_permanent_before_eagain_and_error(self) -> None:
        for failure in (
            BlockingIOError(errno.EAGAIN, "busy"),
            OSError(errno.EIO, "failed"),
        ):
            with self.subTest(failure=type(failure).__name__):
                gate = self._prepare()
                sender = self._sender(gate.environment_overlay)
                gate.bind_spawn(os.getpid())
                identity = process_priority.PriorityScopeIdentity(
                    path=f"/trusted/{self._UNIT_NAME}",
                    device=7,
                    inode=11,
                )
                gate._scope_identity = identity
                self._retire_for_release(gate, sender)
                with (
                    mock.patch.object(
                        process_priority,
                        "_pidfd_process_id",
                        side_effect=lambda descriptor: (
                            gate._spawn_identity.process_id
                            if descriptor == gate._spawn_pidfd
                            else gate._controller_identity.process_id
                        ),
                    ),
                    mock.patch.object(
                        process_priority,
                        "_canonical_process_identity",
                        side_effect=lambda process_id: (
                            gate._spawn_identity
                            if process_id == gate._spawn_identity.process_id
                            else gate._controller_identity
                        ),
                    ),
                    mock.patch.object(
                        process_priority,
                        "priority_scope_membership",
                        return_value=process_priority.PriorityScopeMembership(
                            process_ids=(gate._spawn_identity.process_id,),
                            populated=True,
                        ),
                    ),
                    mock.patch.object(
                        process_priority,
                        "verify_priority_scope_identity",
                        return_value=True,
                    ),
                    mock.patch.object(
                        process_priority.socket.socket,
                        "send",
                        side_effect=failure,
                    ),
                ):
                    if failure.errno == errno.EAGAIN:
                        self.assertFalse(gate.release())
                        self.assertEqual(gate.state, "ready_verified")
                    else:
                        with self.assertRaisesRegex(
                            process_priority.LocalModelPriorityError,
                            "local model ready gate failed",
                        ):
                            gate.release()
                        self.assertEqual(gate.state, "invalid")
                self.assertTrue(gate.release_may_have_occurred)
                gate.close()
                sender.close()
                self.assertTrue(gate.release_may_have_occurred)

    def test_release_retries_after_eagain_without_clearing_ambiguity_latch(
        self,
    ) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        gate.bind_spawn(os.getpid())
        gate._scope_identity = process_priority.PriorityScopeIdentity(
            path=f"/trusted/{self._UNIT_NAME}",
            device=7,
            inode=11,
        )
        self._retire_for_release(gate, sender)
        message_size = len(process_priority._LOCAL_MODEL_GATE_ACK_PREFIX) + 64
        with (
            mock.patch.object(
                process_priority,
                "_pidfd_process_id",
                side_effect=lambda descriptor: (
                    gate._spawn_identity.process_id
                    if descriptor == gate._spawn_pidfd
                    else gate._controller_identity.process_id
                ),
            ),
            mock.patch.object(
                process_priority,
                "_canonical_process_identity",
                side_effect=lambda process_id: (
                    gate._spawn_identity
                    if process_id == gate._spawn_identity.process_id
                    else gate._controller_identity
                ),
            ),
            mock.patch.object(
                process_priority,
                "priority_scope_membership",
                return_value=process_priority.PriorityScopeMembership(
                    process_ids=(gate._spawn_identity.process_id,),
                    populated=True,
                ),
            ),
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
                return_value=True,
            ),
            mock.patch.object(
                process_priority.socket.socket,
                "send",
                side_effect=[
                    BlockingIOError(errno.EAGAIN, "busy"),
                    message_size,
                ],
            ) as send,
        ):
            self.assertFalse(gate.release())
            self.assertTrue(gate.release_may_have_occurred)
            self.assertEqual(gate.state, "ready_verified")
            self.assertTrue(gate.release())

        self.assertEqual(send.call_count, 2)
        self.assertTrue(gate.release_may_have_occurred)
        self.assertEqual(gate.state, "released")
        gate.close()
        sender.close()

    def test_release_revalidates_scope_before_first_ack_syscall(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        gate.bind_spawn(os.getpid())
        gate._scope_identity = process_priority.PriorityScopeIdentity(
            path=f"/trusted/{self._UNIT_NAME}",
            device=7,
            inode=11,
        )
        self._retire_for_release(gate, sender)
        with (
            mock.patch.object(
                process_priority,
                "_pidfd_process_id",
                side_effect=lambda descriptor: (
                    gate._spawn_identity.process_id
                    if descriptor == gate._spawn_pidfd
                    else gate._controller_identity.process_id
                ),
            ),
            mock.patch.object(
                process_priority,
                "_canonical_process_identity",
                return_value=gate._spawn_identity,
            ),
            mock.patch.object(
                process_priority,
                "priority_scope_membership",
                return_value=None,
            ),
            mock.patch.object(process_priority.socket.socket, "send") as send,
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ),
        ):
            gate.release()

        send.assert_not_called()
        self.assertFalse(gate.release_may_have_occurred)
        self.assertEqual(gate.state, "invalid")
        gate.close()
        sender.close()

    def test_release_rechecks_deadline_immediately_before_ack(self) -> None:
        gate = self._prepare()
        sender = self._sender(gate.environment_overlay)
        gate.bind_spawn(os.getpid())
        gate._scope_identity = process_priority.PriorityScopeIdentity(
            path=f"/trusted/{self._UNIT_NAME}",
            device=7,
            inode=11,
        )
        self._retire_for_release(gate, sender)
        gate._absolute_deadline = 100.5
        with (
            mock.patch.object(
                process_priority,
                "_pidfd_process_id",
                side_effect=lambda descriptor: (
                    gate._spawn_identity.process_id
                    if descriptor == gate._spawn_pidfd
                    else gate._controller_identity.process_id
                ),
            ),
            mock.patch.object(
                process_priority,
                "_canonical_process_identity",
                return_value=gate._spawn_identity,
            ),
            mock.patch.object(
                process_priority,
                "priority_scope_membership",
                return_value=process_priority.PriorityScopeMembership(
                    process_ids=(gate._spawn_identity.process_id,),
                    populated=True,
                ),
            ),
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
                return_value=True,
            ),
            mock.patch.object(
                process_priority.time,
                "monotonic",
                side_effect=[100.0, 100.5],
            ) as monotonic,
            mock.patch.object(process_priority.socket.socket, "send") as send,
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ),
        ):
            gate.release()

        self.assertEqual(monotonic.call_count, 2)
        send.assert_not_called()
        self.assertFalse(gate.release_may_have_occurred)
        self.assertEqual(gate.state, "invalid")
        gate.close()
        sender.close()

    def test_terminal_gate_failure_closes_owned_resources_immediately(self) -> None:
        gate = self._prepare()
        gate.bind_spawn(os.getpid())
        descriptors = (
            gate.fileno(),
            gate._controller_pidfd,
            gate._spawn_pidfd,
        )
        attributable_bad = process_priority._LocalModelGateDatagram(
            message=b"wrong",
            credentials=((os.getpid(), os.getuid(), os.getgid()),),
            pidfd_process_ids=(os.getpid(),),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=b"\0" + b"a" * 32,
            unknown_ancillary=False,
        )
        with (
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                return_value=attributable_bad,
            ),
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate failed",
            ),
        ):
            gate.try_verify_ready()

        self.assertEqual(gate.state, "invalid")
        self.assertFalse(gate.release_may_have_occurred)
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        gate.close()

    def test_wrapper_ack_deadline_and_controller_identity_failure_are_bounded(
        self,
    ) -> None:
        for failure in ("deadline", "controller-death"):
            with self.subTest(failure=failure):
                gate = self._prepare(deadline=time.monotonic() + 5.0)
                environment = self._wrapper_environment(gate)
                configuration = process_priority._local_model_gate_from_environment(
                    dict(environment)
                )
                liveness = mock.patch.object(
                    process_priority,
                    "_identity_pidfd_is_live",
                    return_value=failure != "controller-death",
                )
                clock = (
                    mock.patch.object(
                        process_priority.time,
                        "monotonic",
                        side_effect=[
                            configuration.absolute_deadline - 1.0,
                            configuration.absolute_deadline,
                        ],
                    )
                    if failure == "deadline"
                    else mock.patch.object(
                        process_priority.time,
                        "monotonic",
                        return_value=configuration.absolute_deadline - 1.0,
                    )
                )
                with (
                    mock.patch.dict(process_priority.os.environ, environment, clear=True),
                    liveness,
                    clock,
                    self.assertRaisesRegex(
                        process_priority.PriorityScopeError,
                        process_priority._SCOPE_EXEC_FAILURE,
                    ),
                ):
                    process_priority._wait_for_local_model_gate_ack(configuration)
                gate.close()

    def test_wrapper_ready_send_eagain_is_nonblocking_and_never_executes(
        self,
    ) -> None:
        gate = self._prepare(deadline=time.monotonic() + 1.0)
        environment = self._wrapper_environment(gate)
        token_index = gate.argv.index(process_priority._SCOPE_EXEC_WRAPPER_TOKEN)
        wrapper_arguments = gate.argv[token_index:]
        try:
            with (
                mock.patch.dict(process_priority.os.environ, environment, clear=True),
                mock.patch.object(
                    process_priority.socket.socket,
                    "send",
                    side_effect=BlockingIOError(errno.EAGAIN, "full"),
                ) as send,
                mock.patch.object(
                    process_priority.select,
                    "select",
                    wraps=process_priority.select.select,
                ),
                mock.patch.object(process_priority.os, "execve") as execute,
                self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    process_priority._SCOPE_EXEC_FAILURE,
                ),
            ):
                process_priority._run_scope_exec_wrapper(wrapper_arguments)

            execute.assert_not_called()
            self.assertEqual(send.call_count, 1)
            self.assertFalse(
                os.path.lexists(
                    os.path.dirname(
                        gate.environment_overlay[
                            process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
                        ]
                    )
                )
            )
        finally:
            gate.close()

    def test_wrapper_rejects_ack_if_controller_pidfd_becomes_readable(
        self,
    ) -> None:
        gate = self._prepare(deadline=time.monotonic() + 1.0)
        environment = self._wrapper_environment(gate)
        configuration = process_priority._local_model_gate_from_environment(
            dict(environment)
        )
        token_index = gate.argv.index(process_priority._SCOPE_EXEC_WRAPPER_TOKEN)
        wrapper_arguments = gate.argv[token_index:]
        ack = process_priority._LocalModelGateDatagram(
            message=(
                process_priority._LOCAL_MODEL_GATE_ACK_PREFIX
                + configuration.ack_nonce.encode("ascii")
            ),
            credentials=((os.getpid(), os.getuid(), os.getgid()),),
            pidfd_process_ids=(os.getpid(),),
            flags=socket.MSG_CMSG_CLOEXEC,
            source=configuration.controller_address,
            unknown_ancillary=False,
        )

        ack_received = False

        def receive_ack(receiver, *, max_bytes):
            nonlocal ack_received
            self.assertEqual(
                max_bytes,
                process_priority._LOCAL_MODEL_GATE_ACK_MAX_BYTES,
            )
            if ack_received:
                raise BlockingIOError(errno.EAGAIN, "empty")
            ack_received = True
            source = receiver.getsockname()
            gate._listener_owner[0].connect(source)
            self.assertTrue(
                process_priority._retire_local_model_gate_domain(
                    gate._resources,
                    peer_source=source,
                )
            )
            return ack

        with (
            mock.patch.dict(process_priority.os.environ, environment, clear=True),
            mock.patch.object(
                process_priority.socket.socket,
                "send",
                side_effect=lambda message: len(message),
            ),
            mock.patch.object(
                process_priority,
                "_receive_local_model_gate_datagram",
                side_effect=receive_ack,
            ),
            mock.patch.object(
                process_priority,
                "_identity_pidfd_is_live",
                create=True,
                side_effect=[True, True, False],
            ) as liveness,
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ),
            mock.patch.object(process_priority.os, "execve") as execute,
            self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                process_priority._SCOPE_EXEC_FAILURE,
            ),
        ):
            process_priority._run_scope_exec_wrapper(wrapper_arguments)

        self.assertEqual(liveness.call_count, 3)
        execute.assert_not_called()
        gate.close()

    def test_wrapper_keeps_controller_pidfd_open_and_cloexec_at_exec(self) -> None:
        gate = self._prepare(deadline=time.monotonic() + 2.0)
        environment = self._wrapper_environment(gate)
        token_index = gate.argv.index(process_priority._SCOPE_EXEC_WRAPPER_TOKEN)
        wrapper_arguments = gate.argv[token_index:]
        captured: dict[str, int] = {}

        def acknowledge(_configuration, *, controller_pidfd_owner) -> None:
            descriptor = os.pidfd_open(os.getpid(), 0)
            controller_pidfd_owner[0] = descriptor
            captured["pidfd"] = descriptor

        def fail_exec(_target, _argv, target_environment) -> None:
            descriptor = captured["pidfd"]
            captured["sink_called"] = 1
            try:
                os.fstat(descriptor)
            except OSError:
                captured["open_at_exec"] = 0
            else:
                captured["open_at_exec"] = 1
                self.assertTrue(
                    fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC
                )
                self.assertTrue(
                    process_priority._identity_pidfd_is_live(
                        gate._controller_identity,
                        descriptor,
                    )
                )
            for key in (
                *process_priority._LOCAL_MODEL_GATE_ENV_KEYS,
                process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV,
                process_priority._SCOPE_EXEC_LATCH_NONCE_ENV,
                process_priority.SOC_PRIORITY_SCOPE_MARKER,
            ):
                self.assertNotIn(key, target_environment)
            raise OSError(errno.EIO, "simulated exec failure")

        with (
            mock.patch.dict(process_priority.os.environ, environment, clear=True),
            mock.patch.object(
                process_priority,
                "_wait_for_local_model_gate_ack",
                side_effect=acknowledge,
            ),
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ),
            mock.patch.object(
                process_priority,
                "_identity_pidfd_is_live",
                wraps=process_priority._identity_pidfd_is_live,
            ),
            mock.patch.object(
                process_priority.os,
                "execve",
                side_effect=fail_exec,
            ),
            self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                process_priority._SCOPE_EXEC_EXEC_FAILURE,
            ),
        ):
            process_priority._run_scope_exec_wrapper(wrapper_arguments)

        self.assertEqual(captured["sink_called"], 1)
        self.assertEqual(captured["open_at_exec"], 1)
        with self.assertRaises(OSError):
            os.fstat(captured["pidfd"])
        gate.close()

    def test_bind_spawn_identity_change_and_missing_pidfd_are_terminal(self) -> None:
        process_id = os.getpid()
        for failure in ("identity", "pidfd"):
            with self.subTest(failure=failure):
                gate = self._prepare()
                patcher = (
                    mock.patch.object(
                        process_priority,
                        "_canonical_process_identity",
                        return_value=None,
                    )
                    if failure == "identity"
                    else mock.patch.object(
                        process_priority,
                        "_open_identity_pidfd",
                        return_value=None,
                    )
                )
                with (
                    patcher,
                    self.assertRaisesRegex(
                        process_priority.LocalModelPriorityError,
                        "local model ready gate failed",
                    ),
                ):
                    gate.bind_spawn(process_id)
                self.assertEqual(gate.state, "invalid")
                gate.close()

    def test_proc_identity_acquisition_interrupt_closes_owned_descriptor(
        self,
    ) -> None:
        descriptor = os.open("/proc", os.O_RDONLY | os.O_DIRECTORY)
        interrupt = KeyboardInterrupt("stop")
        try:
            with (
                mock.patch.object(process_priority.os, "open", return_value=descriptor),
                mock.patch.object(
                    process_priority.signal,
                    "pthread_sigmask",
                    side_effect=[set(), interrupt],
                ),
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                process_priority._canonical_process_identity(os.getpid())
            self.assertIs(raised.exception, interrupt)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def test_pidfd_acquisition_interrupt_closes_owned_descriptor(self) -> None:
        identity = process_priority._canonical_process_identity(os.getpid())
        self.assertIsNotNone(identity)
        descriptor = os.pidfd_open(os.getpid(), 0)
        owner = [-1]
        interrupt = KeyboardInterrupt("stop")
        try:
            with (
                mock.patch.object(
                    process_priority.os,
                    "pidfd_open",
                    return_value=descriptor,
                ),
                mock.patch.object(
                    process_priority.signal,
                    "pthread_sigmask",
                    side_effect=[set(), interrupt],
                ),
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                process_priority._open_identity_pidfd(identity, owner)
            self.assertIs(raised.exception, interrupt)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def test_prepare_post_construction_interrupt_closes_pidfd_socket_and_endpoint(
        self,
    ) -> None:
        captured: dict[str, object] = {}
        interrupt = KeyboardInterrupt("stop")

        def interrupt_handoff(gate):
            captured["listener"] = gate._listener_owner[0]
            captured["controller_descriptor"] = gate._controller_pidfd
            captured["endpoint"] = gate.environment_overlay[
                process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV
            ]
            raise interrupt

        with (
            mock.patch.object(
                process_priority,
                "_handoff_local_model_ready_ack_gate",
                create=True,
                side_effect=interrupt_handoff,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            self._prepare()

        self.assertIs(raised.exception, interrupt)
        listener = captured["listener"]
        controller_descriptor = captured["controller_descriptor"]
        self.assertEqual(listener.fileno(), -1)
        with self.assertRaises(OSError):
            os.fstat(controller_descriptor)
        self.assertFalse(os.path.lexists(captured["endpoint"]))

    def test_bind_spawn_preserves_first_keyboard_interrupt_during_cleanup(
        self,
    ) -> None:
        gate = self._prepare()
        first = KeyboardInterrupt("first")
        cleanup = KeyboardInterrupt("cleanup")
        with (
            mock.patch.object(
                process_priority,
                "_canonical_process_identity",
                side_effect=first,
            ),
            mock.patch.object(
                type(gate),
                "_close_owned_resources",
                return_value=cleanup,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            gate.bind_spawn(os.getpid())

        self.assertIs(raised.exception, first)
        gate.close()

    def test_resource_owner_retains_ownership_after_two_cleanup_interrupts(
        self,
    ) -> None:
        gate = self._prepare()
        owner = gate._resources
        descriptor = owner.controller_pidfd_owner[0]
        first = KeyboardInterrupt("first")
        second = KeyboardInterrupt("second")
        real_close = os.close
        calls = 0

        def interrupt_twice(candidate: int) -> None:
            nonlocal calls
            if candidate == descriptor:
                calls += 1
                if calls == 1:
                    raise first
                if calls == 2:
                    raise second
            real_close(candidate)

        try:
            with mock.patch.object(
                process_priority.os,
                "close",
                side_effect=interrupt_twice,
            ):
                cleanup_error = owner.close()

            self.assertIs(cleanup_error, first)
            self.assertFalse(owner._closed)
            self.assertEqual(owner.controller_pidfd_owner[0], descriptor)
            os.fstat(descriptor)

            owner.__del__()
            self.assertTrue(owner._closed)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        finally:
            try:
                real_close(descriptor)
            except OSError:
                pass

    def test_resource_owner_finalizer_force_closes_fds_without_unknown_unlink(
        self,
    ) -> None:
        gate = self._prepare()
        owner = gate._resources
        domain = os.path.dirname(
            gate.environment_overlay[process_priority._LOCAL_MODEL_GATE_ADDRESS_ENV]
        )
        unexpected = os.path.join(domain, "unexpected")
        with open(unexpected, "xb") as output:
            output.write(b"keep")
        descriptors = {
            gate.fileno(),
            owner.controller_pidfd_owner[0],
            *owner.directory_descriptors,
        }

        with mock.patch.object(
            process_priority,
            "_close_local_model_gate_resource_owner",
            wraps=process_priority._close_local_model_gate_resource_owner,
        ) as retire:
            owner.__del__()

        self.assertEqual(retire.call_count, 1)
        self.assertTrue(owner._closed)
        self.assertTrue(
            process_priority._local_model_gate_resource_owner_is_empty(owner)
        )
        for descriptor in descriptors:
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        with open(unexpected, "rb") as source:
            self.assertEqual(source.read(), b"keep")
        self.assertTrue(os.path.isdir(domain))
        os.unlink(unexpected)
        os.rmdir(domain)
        gate.close()

    def test_prepare_pidfd_boundary_interrupt_closes_acquired_descriptor(
        self,
    ) -> None:
        descriptor = os.pidfd_open(os.getpid(), 0)
        interrupt = KeyboardInterrupt("stop")

        def interrupt_after_acquire(_identity, owner):
            owner[0] = descriptor
            raise interrupt

        try:
            with (
                mock.patch.object(
                    process_priority,
                    "_open_identity_pidfd",
                    side_effect=interrupt_after_acquire,
                ),
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                self._prepare()
            self.assertIs(raised.exception, interrupt)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass

    def test_prepare_rejects_unsupported_pidfd_abi_without_partial_gate(self) -> None:
        with (
            mock.patch.object(process_priority.os, "pidfd_open", None),
            self.assertRaisesRegex(
                process_priority.LocalModelPriorityError,
                "local model ready gate capability is unavailable",
            ),
        ):
            self._prepare(require_host_capability=False)

    def test_expired_ready_gate_deadline_is_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate is invalid",
        ):
            self._prepare(
                deadline=time.monotonic(),
                require_host_capability=False,
            )

    def test_invalid_transitions_are_fail_closed(self) -> None:
        gate = self._prepare()
        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate failed",
        ):
            gate.try_verify_ready()
        self.assertEqual(gate.state, "invalid")
        with self.assertRaisesRegex(
            process_priority.LocalModelPriorityError,
            "local model ready gate failed",
        ):
            gate.release()
        gate.close()

    def test_gated_wrapper_failure_before_ack_cannot_exec_target(self) -> None:
        gate = self._prepare()
        environment = gate.environment_overlay
        token_index = gate.argv.index(process_priority._SCOPE_EXEC_WRAPPER_TOKEN)
        wrapper_arguments = gate.argv[token_index:]
        with (
            mock.patch.dict(process_priority.os.environ, environment, clear=True),
            mock.patch.object(
                process_priority,
                "_wait_for_local_model_gate_ack",
                side_effect=process_priority.PriorityScopeError(
                    process_priority._SCOPE_EXEC_FAILURE
                ),
            ),
            mock.patch.object(process_priority.os, "execve") as execute,
            self.assertRaises(process_priority.PriorityScopeError),
        ):
            process_priority._run_scope_exec_wrapper(wrapper_arguments)
        execute.assert_not_called()
        gate.close()

    def test_legacy_scope_builders_keep_one_way_and_ungated_argv(self) -> None:
        runtime = os.path.realpath(sys.executable)
        with (
            mock.patch.object(
                process_priority,
                "_required_scope_tool",
                return_value=runtime,
            ),
            mock.patch.object(
                process_priority,
                "_required_priority_tool",
                return_value=runtime,
            ),
            mock.patch.object(
                process_priority,
                "_current_cpu_priority",
                return_value=0,
            ),
        ):
            high = process_priority.build_soc_priority_scope_command([runtime])
            local = process_priority.local_model_command([runtime])

        self.assertIn(process_priority._SCOPE_EXEC_LATCH_REQUIRED_TOKEN, high)
        self.assertNotIn(process_priority._LOCAL_MODEL_GATE_REQUIRED_TOKEN, high)
        self.assertNotIn(process_priority._SCOPE_EXEC_LATCH_REQUIRED_TOKEN, local)
        self.assertNotIn(process_priority._LOCAL_MODEL_GATE_REQUIRED_TOKEN, local)


class LocalModelDirectCapabilityTests(unittest.TestCase):
    @staticmethod
    def _status(*, uids=(1000, 1000, 1000, 1000), **capabilities) -> str:
        values = {
            "CapInh": 0,
            "CapPrm": 0,
            "CapEff": 0,
            "CapBnd": 0,
            "CapAmb": 0,
        }
        values.update(capabilities)
        lines = ["Uid:\t" + "\t".join(str(uid) for uid in uids)]
        lines.extend(f"{name}:\t{value:016x}" for name, value in values.items())
        return "\n".join(lines) + "\n"

    def test_direct_capability_gate_accepts_current_unprivileged_process(self) -> None:
        if os.getuid() == 0 or os.geteuid() == 0:
            self.skipTest("requires an unprivileged test process")
        self.assertTrue(
            process_priority._local_model_direct_cap_sys_nice_is_absent()
        )

    def test_direct_capability_gate_rejects_setuid_in_active_sets(self) -> None:
        cap_setuid = 1 << 7
        for field in ("CapEff", "CapPrm", "CapAmb"):
            with (
                self.subTest(field=field),
                mock.patch.object(
                    process_priority,
                    "_read_affinity_file",
                    return_value=self._status(**{field: cap_setuid}),
                ),
                mock.patch.object(process_priority.os, "getuid", return_value=1000),
                mock.patch.object(process_priority.os, "geteuid", return_value=1000),
            ):
                self.assertFalse(
                    process_priority._local_model_direct_cap_sys_nice_is_absent()
                )

    def test_direct_capability_gate_rejects_root_or_split_uids(self) -> None:
        for uids in ((0, 0, 0, 0), (1000, 1000, 0, 1000), (1000, 1000, 1000, 0)):
            with (
                self.subTest(uids=uids),
                mock.patch.object(
                    process_priority,
                    "_read_affinity_file",
                    return_value=self._status(uids=uids),
                ),
                mock.patch.object(process_priority.os, "getuid", return_value=uids[0]),
                mock.patch.object(process_priority.os, "geteuid", return_value=uids[1]),
            ):
                self.assertFalse(
                    process_priority._local_model_direct_cap_sys_nice_is_absent()
                )


if __name__ == "__main__":
    unittest.main()
