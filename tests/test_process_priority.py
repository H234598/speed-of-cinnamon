import json
import errno
import os
import select
import signal
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import command_chain, process_priority


def _mountinfo_device(path: Path) -> str:
    device = path.stat().st_dev
    return f"{os.major(device)}:{os.minor(device)}"


def _different_mountinfo_device(path: Path) -> str:
    device = path.stat().st_dev
    major = os.major(device)
    minor = os.minor(device)
    if minor < process_priority.MAX_CGROUP_DEVICE_COMPONENT:
        minor += 1
    else:
        major = major + 1 if major < process_priority.MAX_CGROUP_DEVICE_COMPONENT else major - 1
    return f"{major}:{minor}"


def _write_affinity_scope_fixture(
    root: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    mountpoint = root / "cgroup"
    parent = mountpoint / "parent.slice"
    scope = parent / "work.scope"
    scope.mkdir(parents=True)
    (parent / "cpuset.cpus.effective").write_text("0-3\n", encoding="ascii")
    cgroup = root / "self.cgroup"
    cgroup.write_text("0::/parent.slice/work.scope\n", encoding="ascii")
    mountinfo = root / "mountinfo"
    mountinfo.write_text(
        f"36 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
        encoding="ascii",
    )
    online = root / "online"
    online.write_text("1-4\n", encoding="ascii")
    return cgroup, mountinfo, online, mountpoint, parent, scope


def _write_resource_scope_fixture(
    root: Path,
) -> tuple[Path, Path, Path, Path, Path, Path]:
    cgroup, mountinfo, online, mountpoint, parent, scope = (
        _write_affinity_scope_fixture(root)
    )
    gib = 1024**3
    values = {
        parent: ("200000 100000\n", 2 * gib, f"{12 * gib}\n", f"{11 * gib}\n"),
        scope: ("400000 100000\n", gib, f"{20 * gib}\n", "max\n"),
    }
    for directory, (cpu_max, current, maximum, high) in values.items():
        (directory / "cpu.max").write_text(cpu_max, encoding="ascii")
        (directory / "memory.current").write_text(f"{current}\n", encoding="ascii")
        (directory / "memory.max").write_text(maximum, encoding="ascii")
        (directory / "memory.high").write_text(high, encoding="ascii")
    return cgroup, mountinfo, online, mountpoint, parent, scope


def _write_scope_membership_fixture(
    root: Path,
    *,
    events: str = "populated 0\n",
    process_ids: str = "",
) -> tuple[process_priority.PriorityScopeIdentity, Path, Path]:
    mountpoint = root / "cgroup"
    scope = mountpoint / "work.scope"
    scope.mkdir(parents=True)
    (scope / "cpu.weight").write_text("200\n", encoding="ascii")
    (scope / "io.weight").write_text("default 200\n", encoding="ascii")
    (scope / "cgroup.events").write_text(events, encoding="ascii")
    (scope / "cgroup.procs").write_text(process_ids, encoding="ascii")
    scope_stat = scope.stat()
    identity = process_priority.PriorityScopeIdentity(
        os.fspath(scope),
        scope_stat.st_dev,
        scope_stat.st_ino,
    )
    mountinfo = root / "mountinfo"
    mountinfo.write_text(
        f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
        encoding="ascii",
    )
    return identity, mountinfo, scope


def _scope_manager_unavailable(stderr: bytes) -> bool:
    detail = stderr.decode("utf-8", errors="replace").lower()
    return any(
        marker in detail
        for marker in (
            "failed to connect to bus: no medium found",
            "failed to connect to bus: no such file or directory",
            "failed to create bus connection: no medium found",
            "failed to create bus connection: no such file or directory",
            "failed to connect to user scope bus via local transport: operation not permitted",
        )
    )


def _scope_exec_test_arguments(
    *arguments: str,
    latch_required: bool = False,
) -> list[str]:
    target = [os.path.realpath(sys.executable), *arguments]
    return process_priority._scope_exec_wrapper_command(
        target,
        latch_required=latch_required,
    )[2:]


def _fake_scope_process(returncode: int | None) -> mock.Mock:
    process = mock.Mock()
    process.pid = 4242
    process.poll.return_value = returncode
    process.wait.return_value = returncode
    return process


def _send_scope_test_datagrams(
    environment: dict[str, str],
    payloads: tuple[bytes | None, ...],
) -> None:
    address = "\0" + environment[process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV]
    nonce = environment[process_priority._SCOPE_EXEC_LATCH_NONCE_ENV]
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:
        for payload in payloads:
            message = (
                process_priority._SCOPE_EXEC_LATCH_MESSAGE_PREFIX
                + nonce.encode("ascii")
                if payload is None
                else payload
            )
            sender.sendto(message, address)


class ProcessPriorityTests(unittest.TestCase):
    def test_scope_bootstrap_expands_inherited_affinity_to_cgroup_limit(self) -> None:
        if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
            self.skipTest("Linux CPU affinity APIs are unavailable")
        inherited = os.sched_getaffinity(0)
        expected = process_priority._scope_exec_allowed_cpus()
        if len(expected) < 2 or not inherited:
            self.skipTest("multiple cgroup-allowed CPUs are unavailable")
        probe = (
            "import json, os; "
            "from speed_of_cinnamon import process_priority; "
            "os.sched_setaffinity(0, {min(os.sched_getaffinity(0))}); "
            "process_priority._normalize_cpu_affinity_for_scope_exec(); "
            "print(json.dumps(sorted(os.sched_getaffinity(0))))"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", probe],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=5.0,
            shell=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        normalized = set(json.loads(result.stdout.decode("ascii")))
        self.assertEqual(normalized, set(expected))

    def test_cpu_list_parser_is_strict_and_bounded(self) -> None:
        self.assertEqual(
            process_priority._parse_cpu_list("0-2,4,7-8\n"),
            frozenset({0, 1, 2, 4, 7, 8}),
        )
        for invalid in (
            "",
            "0-",
            "2-1",
            "0, 1",
            "00",
            "0,0",
            "1048576",
            "9" * 65_537,
        ):
            with self.subTest(invalid=invalid[:20]):
                self.assertIsNone(process_priority._parse_cpu_list(invalid))

    def test_scope_affinity_resolves_mount_root_and_intersects_online_cpus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generic_mount = root / "generic"
            generic_mount.mkdir()
            mountpoint = root / "specific"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpuset.cpus.effective").write_text("0-3,8\n", encoding="ascii")
            cgroup = root / "self.cgroup"
            cgroup.write_text("0::/root.slice/work.scope\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {generic_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /root.slice {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            online = root / "online"
            online.write_text("1-4\n", encoding="ascii")

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                self.assertEqual(
                    process_priority._scope_exec_allowed_cpus(),
                    frozenset({1, 2, 3}),
                )

    def test_scope_affinity_uses_first_valid_parent_after_leaf_enoent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, mountpoint, _, _ = (
                _write_affinity_scope_fixture(root)
            )
            real_open = os.open
            real_close = os.close
            open_calls: list[tuple[str, int | None, int]] = []
            open_descriptors: set[int] = set()

            def track_open(
                path: str | bytes | os.PathLike[str],
                flags: int,
                *,
                dir_fd: int | None = None,
            ) -> int:
                open_calls.append((os.fsdecode(path), dir_fd, flags))
                descriptor = real_open(path, flags, dir_fd=dir_fd)
                open_descriptors.add(descriptor)
                return descriptor

            def track_close(descriptor: int) -> None:
                real_close(descriptor)
                open_descriptors.discard(descriptor)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(process_priority.os, "open", side_effect=track_open),
                mock.patch.object(
                    process_priority.os, "close", side_effect=track_close
                ),
            ):
                self.assertEqual(
                    process_priority._scope_exec_allowed_cpus(),
                    frozenset({1, 2, 3}),
                )

            directory_flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            self.assertTrue(
                any(
                    path == os.fspath(mountpoint)
                    and dir_fd is None
                    and flags & directory_flags == directory_flags
                    for path, dir_fd, flags in open_calls
                )
            )
            for component in ("parent.slice", "work.scope"):
                self.assertTrue(
                    any(
                        path == component
                        and dir_fd is not None
                        and flags & directory_flags == directory_flags
                        for path, dir_fd, flags in open_calls
                    )
                )
            self.assertTrue(
                any(
                    path == "cpuset.cpus.effective" and dir_fd is not None
                    for path, dir_fd, _ in open_calls
                )
            )
            self.assertFalse(open_descriptors)

    def test_scope_affinity_intermediate_directory_enoent_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _, _, _ = _write_affinity_scope_fixture(root)
            real_open = os.open
            real_close = os.close
            injected = False
            cpuset_opened = False
            open_descriptors: set[int] = set()

            def remove_intermediate(
                path: str | bytes | os.PathLike[str],
                flags: int,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal cpuset_opened, injected
                name = os.fsdecode(path)
                if name == "work.scope" and dir_fd is not None:
                    injected = True
                    raise FileNotFoundError(errno.ENOENT, "gone")
                if name == "cpuset.cpus.effective" and dir_fd is not None:
                    cpuset_opened = True
                descriptor = real_open(path, flags, dir_fd=dir_fd)
                open_descriptors.add(descriptor)
                return descriptor

            def track_close(descriptor: int) -> None:
                real_close(descriptor)
                open_descriptors.discard(descriptor)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority.os, "open", side_effect=remove_intermediate
                ),
                mock.patch.object(
                    process_priority.os, "close", side_effect=track_close
                ),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cgroup-v2 boundary"
                ):
                    process_priority._scope_exec_allowed_cpus()

            self.assertTrue(injected)
            self.assertFalse(cpuset_opened)
            self.assertFalse(open_descriptors)

    def test_scope_affinity_symlinked_intermediate_directory_is_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _, parent, _ = _write_affinity_scope_fixture(
                root
            )
            outside_parent = root / "outside-parent"
            parent.rename(outside_parent)
            parent.symlink_to(outside_parent, target_is_directory=True)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cgroup-v2 boundary"
                ):
                    process_priority._scope_exec_allowed_cpus()

    def test_scope_affinity_does_not_ascend_past_terminal_leaf_error(self) -> None:
        for failure in ("malformed", "nonregular", "symlink"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cgroup, mountinfo, online, _, _, scope = _write_affinity_scope_fixture(
                    root
                )
                cpuset = scope / "cpuset.cpus.effective"
                if failure == "malformed":
                    cpuset.write_text("invalid\n", encoding="ascii")
                elif failure == "nonregular":
                    cpuset.mkdir()
                else:
                    outside = root / "outside-cpuset"
                    outside.write_text("0-3\n", encoding="ascii")
                    cpuset.symlink_to(outside)

                with (
                    mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                    mock.patch.object(
                        process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                    ),
                    mock.patch.object(process_priority, "_CPU_ONLINE", online),
                ):
                    with self.assertRaisesRegex(
                        process_priority.PriorityScopeError, "cpuset"
                    ):
                        process_priority._scope_exec_allowed_cpus()

    def test_scope_affinity_does_not_ascend_after_unreadable_leaf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _, parent, scope = _write_affinity_scope_fixture(
                root
            )
            leaf_cpuset = scope / "cpuset.cpus.effective"
            real_open = os.open
            leaf_descriptor: int | None = None
            cpuset_attempts = 0

            def deny_leaf(
                path: str | bytes | os.PathLike[str],
                flags: int,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal cpuset_attempts, leaf_descriptor
                candidate = Path(os.fsdecode(path))
                if candidate == leaf_cpuset:
                    cpuset_attempts += 1
                    raise PermissionError("denied")
                if candidate == Path("cpuset.cpus.effective") and dir_fd is not None:
                    cpuset_attempts += 1
                    if dir_fd == leaf_descriptor:
                        raise PermissionError("denied")
                descriptor = real_open(path, flags, dir_fd=dir_fd)
                if candidate == Path("work.scope") and dir_fd is not None:
                    leaf_descriptor = descriptor
                return descriptor

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(process_priority.os, "open", side_effect=deny_leaf),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cpuset"
                ):
                    process_priority._scope_exec_allowed_cpus()

            self.assertEqual(cpuset_attempts, 1)

    def test_scope_affinity_stops_at_selected_mount_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, mountpoint, parent, scope = (
                _write_affinity_scope_fixture(root)
            )
            (parent / "cpuset.cpus.effective").unlink()
            outside_cpuset = root / "cpuset.cpus.effective"
            outside_cpuset.write_text("0-3\n", encoding="ascii")
            real_open = os.open
            opened: list[tuple[str, int | None]] = []

            def track_open(
                path: str | bytes | os.PathLike[str],
                flags: int,
                *,
                dir_fd: int | None = None,
            ) -> int:
                opened.append((os.fsdecode(path), dir_fd))
                return real_open(path, flags, dir_fd=dir_fd)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(process_priority.os, "open", side_effect=track_open),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cpuset"
                ):
                    process_priority._scope_exec_allowed_cpus()

            self.assertEqual(
                sum(
                    path == "cpuset.cpus.effective" and dir_fd is not None
                    for path, dir_fd in opened
                ),
                3,
            )
            self.assertNotIn((os.fspath(outside_cpuset), None), opened)

    def test_scope_affinity_rejects_selected_parent_directory_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, mountpoint, parent, _ = (
                _write_affinity_scope_fixture(root)
            )
            real_reader = process_priority._read_affinity_file
            replaced = False

            def replace_parent(path: Path, **kwargs: object) -> object:
                nonlocal replaced
                value = real_reader(path, **kwargs)
                if path == online and not replaced:
                    replaced = True
                    parent.rename(root / "original-parent")
                    replacement = mountpoint / "parent.slice"
                    (replacement / "work.scope").mkdir(parents=True)
                    (replacement / "cpuset.cpus.effective").write_text(
                        "0-3\n", encoding="ascii"
                    )
                return value

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority,
                    "_read_affinity_file",
                    side_effect=replace_parent,
                ),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cpuset"
                ):
                    process_priority._scope_exec_allowed_cpus()

            self.assertTrue(replaced)

    def test_scope_affinity_rejects_cpuset_file_inode_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _, parent, _ = _write_affinity_scope_fixture(
                root
            )
            cpuset = parent / "cpuset.cpus.effective"
            original_inode = cpuset.stat().st_ino
            real_reader = process_priority._read_affinity_file
            replaced = False

            def replace_cpuset(path: Path, **kwargs: object) -> object:
                nonlocal replaced
                value = real_reader(path, **kwargs)
                if path == online and not replaced:
                    replaced = True
                    cpuset.rename(parent / "original-cpuset")
                    cpuset.write_text("0-3\n", encoding="ascii")
                return value

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority,
                    "_read_affinity_file",
                    side_effect=replace_cpuset,
                ),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cpuset"
                ):
                    process_priority._scope_exec_allowed_cpus()

            self.assertTrue(replaced)
            self.assertNotEqual(cpuset.stat().st_ino, original_inode)

    def test_scope_affinity_rejects_same_inode_cpuset_content_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _, parent, _ = _write_affinity_scope_fixture(
                root
            )
            cpuset = parent / "cpuset.cpus.effective"
            original_inode = cpuset.stat().st_ino
            real_reader = process_priority._read_affinity_file
            changed = False

            def change_cpuset(path: Path, **kwargs: object) -> object:
                nonlocal changed
                value = real_reader(path, **kwargs)
                if path == online and not changed:
                    changed = True
                    cpuset.write_text("0-2\n", encoding="ascii")
                return value

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority,
                    "_read_affinity_file",
                    side_effect=change_cpuset,
                ),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError, "cpuset"
                ):
                    process_priority._scope_exec_allowed_cpus()

            self.assertTrue(changed)
            self.assertEqual(cpuset.stat().st_ino, original_inode)

    def test_scope_affinity_revalidates_membership_and_mapping_after_read(self) -> None:
        for changed_state in ("membership", "mapping"):
            with self.subTest(
                changed_state=changed_state
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cgroup, mountinfo, online, _, parent, scope = (
                    _write_affinity_scope_fixture(root)
                )
                leaf_cpuset = scope / "cpuset.cpus.effective"
                leaf_cpuset.write_text("0-3\n", encoding="ascii")
                leaf_stat = leaf_cpuset.stat()
                replacement_mountinfo = None
                if changed_state == "membership":
                    other_scope = parent / "other.scope"
                    other_scope.mkdir()
                    (other_scope / "cpuset.cpus.effective").write_text(
                        "0-3\n", encoding="ascii"
                    )
                else:
                    replacement_mountpoint = root / "replacement-cgroup"
                    replacement_scope = (
                        replacement_mountpoint / "parent.slice" / "work.scope"
                    )
                    replacement_scope.mkdir(parents=True)
                    (replacement_scope / "cpuset.cpus.effective").write_text(
                        "0-3\n", encoding="ascii"
                    )
                    replacement_mountinfo = (
                        f"37 25 {_mountinfo_device(root)} / {replacement_mountpoint} "
                        "rw - cgroup2 cgroup rw\n"
                    )
                real_read = os.read
                changed = False

                def change_after_cpuset_read(descriptor: int, size: int) -> bytes:
                    nonlocal changed
                    data = real_read(descriptor, size)
                    descriptor_stat = os.fstat(descriptor)
                    if (
                        not changed
                        and data
                        and descriptor_stat.st_dev == leaf_stat.st_dev
                        and descriptor_stat.st_ino == leaf_stat.st_ino
                    ):
                        changed = True
                        if changed_state == "membership":
                            cgroup.write_text(
                                "0::/parent.slice/other.scope\n", encoding="ascii"
                            )
                        else:
                            assert replacement_mountinfo is not None
                            mountinfo.write_text(
                                replacement_mountinfo, encoding="ascii"
                            )
                    return data

                with (
                    mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                    mock.patch.object(
                        process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                    ),
                    mock.patch.object(process_priority, "_CPU_ONLINE", online),
                    mock.patch.object(
                        process_priority.os,
                        "read",
                        side_effect=change_after_cpuset_read,
                    ),
                ):
                    with self.assertRaisesRegex(
                        process_priority.PriorityScopeError, "cgroup-v2 boundary"
                    ):
                        process_priority._scope_exec_allowed_cpus()

                self.assertTrue(changed)

    def test_scope_affinity_revalidates_after_second_cpuset_snapshot(self) -> None:
        for changed_state in ("membership", "mapping"):
            with self.subTest(
                changed_state=changed_state
            ), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cgroup, mountinfo, online, _, parent, _ = (
                    _write_affinity_scope_fixture(root)
                )
                cpuset_stat = (parent / "cpuset.cpus.effective").stat()
                replacement_mountinfo = None
                if changed_state == "membership":
                    (parent / "other.scope").mkdir()
                else:
                    replacement_mountpoint = root / "replacement-cgroup"
                    (replacement_mountpoint / "parent.slice" / "work.scope").mkdir(
                        parents=True
                    )
                    replacement_mountinfo = (
                        f"37 25 {_mountinfo_device(root)} / {replacement_mountpoint} "
                        "rw - cgroup2 cgroup rw\n"
                    )

                real_open = os.open
                real_close = os.close
                real_read = os.read
                open_descriptors: set[int] = set()
                cpuset_reads = 0
                changed = False

                def track_open(
                    path: str | bytes | os.PathLike[str],
                    flags: int,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    descriptor = real_open(path, flags, dir_fd=dir_fd)
                    open_descriptors.add(descriptor)
                    return descriptor

                def track_close(descriptor: int) -> None:
                    real_close(descriptor)
                    open_descriptors.discard(descriptor)

                def change_during_second_cpuset_read(
                    descriptor: int, size: int
                ) -> bytes:
                    nonlocal changed, cpuset_reads
                    data = real_read(descriptor, size)
                    descriptor_stat = os.fstat(descriptor)
                    if (
                        data
                        and descriptor_stat.st_dev == cpuset_stat.st_dev
                        and descriptor_stat.st_ino == cpuset_stat.st_ino
                    ):
                        cpuset_reads += 1
                        if cpuset_reads == 2:
                            changed = True
                            if changed_state == "membership":
                                cgroup.write_text(
                                    "0::/parent.slice/other.scope\n",
                                    encoding="ascii",
                                )
                            else:
                                assert replacement_mountinfo is not None
                                mountinfo.write_text(
                                    replacement_mountinfo,
                                    encoding="ascii",
                                )
                    return data

                with (
                    mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                    mock.patch.object(
                        process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                    ),
                    mock.patch.object(process_priority, "_CPU_ONLINE", online),
                    mock.patch.object(
                        process_priority.os,
                        "open",
                        side_effect=track_open,
                    ),
                    mock.patch.object(
                        process_priority.os,
                        "close",
                        side_effect=track_close,
                    ),
                    mock.patch.object(
                        process_priority.os,
                        "read",
                        side_effect=change_during_second_cpuset_read,
                    ),
                ):
                    with self.assertRaisesRegex(
                        process_priority.PriorityScopeError, "cgroup-v2 boundary"
                    ):
                        process_priority._scope_exec_allowed_cpus()

                self.assertEqual(cpuset_reads, 2)
                self.assertTrue(changed)
                self.assertFalse(open_descriptors)

    def test_pid_cgroup_path_maps_relative_to_cgroup2_mount_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/root.slice/work.scope\n", encoding="ascii")
            mountpoint = root / "cgroup"
            expected = mountpoint / "work.scope"
            expected.mkdir(parents=True)
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"36 25 {_mountinfo_device(root)} /root.slice {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )

            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
            ):
                resolved = process_priority._cgroup2_path_for_pid(4242)

        self.assertEqual(resolved, expected)

    def test_pid_cgroup_path_rejects_invalid_unmapped_or_changed_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            mountpoint = root / "cgroup"
            (mountpoint / "work.scope").mkdir(parents=True)
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"36 25 {_mountinfo_device(root)} /root.slice {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
            ):
                for invalid in (
                    "5:cpuset:/root.slice/work.scope\n",
                    "0::relative/work.scope\n",
                    "0::/other.slice/work.scope\n",
                    "0::/root.slice/work.scope\n0::/root.slice/other.scope\n",
                ):
                    with self.subTest(invalid=invalid):
                        pid_cgroup.write_text(invalid, encoding="ascii")
                        self.assertIsNone(process_priority._cgroup2_path_for_pid(4242))

            reads = iter(
                (
                    "0::/root.slice/work.scope\n",
                    mountinfo.read_text(encoding="ascii"),
                    "0::/root.slice/other.scope\n",
                )
            )
            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_read_affinity_file",
                    side_effect=lambda *_args, **_kwargs: next(reads),
                ) as read_file,
            ):
                self.assertIsNone(process_priority._cgroup2_path_for_pid(4242))
            self.assertEqual(read_file.call_count, 3)

    def test_cgroup2_mount_mapping_rejects_invalid_device_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            mountpoint.mkdir()
            mountinfo = root / "mountinfo"
            for device in (
                "4294967296:0",
                "0:4294967296",
                f"{'9' * 100}:0",
                f"0:{'9' * 100}",
                "-1:0",
                "0:+1",
                "00:1",
                "0:01",
                "０:1",
            ):
                with self.subTest(device=device[:20]):
                    mountinfo.write_text(
                        f"36 25 {device} / {mountpoint} rw - cgroup2 cgroup rw\n",
                        encoding="utf-8",
                    )
                    with mock.patch.object(
                        process_priority,
                        "_PROC_SELF_MOUNTINFO",
                        mountinfo,
                    ):
                        self.assertIsNone(process_priority._cgroup2_mount_mappings())

    def test_cgroup2_mount_mapping_deduplication_is_set_bounded(self) -> None:
        fanout = process_priority.MAX_CGROUP2_MOUNT_MAPPINGS
        mountinfo = "".join(
            f"{index + 1} 1 0:1 /root/{index} /mount/{index} "
            "rw - cgroup2 cgroup rw\n"
            for index in range(fanout)
        )
        equality_calls = 0
        original_equality = process_priority._Cgroup2MountMapping.__eq__

        def count_equality(left: object, right: object) -> bool:
            nonlocal equality_calls
            equality_calls += 1
            return original_equality(left, right)

        with (
            mock.patch.object(
                process_priority,
                "_read_affinity_file",
                return_value=mountinfo,
            ),
            mock.patch.object(
                process_priority._Cgroup2MountMapping,
                "__eq__",
                new=count_equality,
            ),
        ):
            mappings = process_priority._cgroup2_mount_mappings()

        self.assertIsNotNone(mappings)
        self.assertEqual(len(mappings), fanout)
        self.assertLessEqual(equality_calls, fanout * 2)

        with mock.patch.object(
            process_priority,
            "_read_affinity_file",
            return_value=(
                mountinfo
                + f"{fanout + 1} 1 0:1 /overflow /overflow "
                "rw - cgroup2 cgroup rw\n"
            ),
        ):
            self.assertIsNone(process_priority._cgroup2_mount_mappings())

    def test_scope_identity_capture_and_pid_verify_use_specific_cgroup2_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/root.slice/work.scope\n", encoding="ascii")
            generic_mount = root / "generic"
            generic_mount.mkdir()
            specific_mount = root / "specific"
            scope = specific_mount / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {generic_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /root.slice {specific_mount} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
            ):
                identity = process_priority.priority_scope_identity_for_pid(4242)
                self.assertIsNotNone(identity)
                assert identity is not None
                self.assertEqual(identity.path, os.fspath(scope))
                self.assertTrue(
                    process_priority.verify_priority_scope_identity(identity, pid=4242)
                )

    def test_scope_identity_capture_rejects_pid_migration_during_weight_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open = os.open
            migrated = False

            def migrating_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal migrated
                if path == "io.weight" and dir_fd is not None and not migrated:
                    migrated = True
                    pid_cgroup.write_text("0::/other.scope\n", encoding="ascii")
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority.os, "open", side_effect=migrating_open),
                mock.patch.object(
                    process_priority,
                    "_cgroup2_path_for_pid",
                    wraps=process_priority._cgroup2_path_for_pid,
                ) as map_pid,
            ):
                self.assertIsNone(process_priority.priority_scope_identity_for_pid(4242))

            self.assertTrue(migrated)
            self.assertEqual(map_pid.call_count, 2)

    def test_scope_identity_capture_rechecks_inode_after_final_pid_remap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            remaps = 0

            def replace_on_final_remap(
                _pid: int,
                *,
                mappings: tuple[tuple[Path, Path], ...] | None = None,
            ) -> Path:
                nonlocal remaps
                self.assertIsNotNone(mappings)
                remaps += 1
                if remaps == 2:
                    scope.rename(root / "orphan.scope")
                    scope.mkdir()
                    (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                    (scope / "io.weight").write_text("default 200\n", encoding="ascii")
                return scope

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_cgroup2_path_for_pid",
                    side_effect=replace_on_final_remap,
                ),
            ):
                self.assertIsNone(process_priority.priority_scope_identity_for_pid(4242))

            self.assertEqual(remaps, 2)

    def test_scope_identity_capture_reads_weights_relative_to_bound_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open = os.open
            relative_children: list[tuple[str, int | None]] = []

            def track_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if path in {"cpu.weight", "io.weight"}:
                    relative_children.append((str(path), dir_fd))
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority.os, "open", side_effect=track_open),
                mock.patch.object(
                    process_priority,
                    "_parse_cgroup_weight",
                    side_effect=AssertionError("path weight parser used"),
                ),
            ):
                identity = process_priority.priority_scope_identity_for_pid(4242)

            self.assertIsNotNone(identity)
            assert identity is not None
            scope_stat = scope.stat()
            self.assertEqual(
                identity,
                process_priority.PriorityScopeIdentity(
                    os.fspath(scope),
                    scope_stat.st_dev,
                    scope_stat.st_ino,
                ),
            )
            self.assertEqual([name for name, _ in relative_children], ["cpu.weight", "io.weight"])
            self.assertTrue(all(dir_fd is not None for _, dir_fd in relative_children))

    def test_scope_identity_capture_closes_fd_on_read_failure_and_requires_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open = os.open
            real_close = os.close
            scope_descriptors: list[int] = []

            def track_scope_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if dir_fd is None:
                    descriptor = real_open(path, flags, mode)
                else:
                    descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if Path(path) == scope:
                    scope_descriptors.append(descriptor)
                return descriptor

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_cgroup2_path_for_pid",
                    return_value=scope,
                ),
                mock.patch.object(process_priority.os, "open", side_effect=track_scope_open),
                mock.patch.object(process_priority, "_read_scope_file", return_value=None),
                mock.patch.object(process_priority.os, "close", wraps=real_close) as close_fd,
            ):
                self.assertIsNone(process_priority.priority_scope_identity_for_pid(4242))

            self.assertEqual(len(scope_descriptors), 1)
            close_fd.assert_any_call(scope_descriptors[0])

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_cgroup2_path_for_pid",
                    return_value=scope,
                ),
                mock.patch.object(process_priority.os, "O_DIRECTORY", None),
            ):
                self.assertIsNone(process_priority.priority_scope_identity_for_pid(4242))

    def test_control_group_identity_uses_canonical_specific_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generic_mount = root / "generic"
            (generic_mount / "root.slice" / "work.scope").mkdir(parents=True)
            specific_mount = root / "specific"
            specific_scope = specific_mount / "work.scope"
            specific_scope.mkdir(parents=True)
            (specific_scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (specific_scope / "io.weight").write_text(
                "default 200\n",
                encoding="ascii",
            )
            mountinfo = root / "mountinfo"
            mount_records = (
                f"35 25 {_mountinfo_device(root)} / {generic_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /root.slice {specific_mount} rw - cgroup2 cgroup rw\n"
            )
            for records in (
                mount_records,
                f"36 25 {_mountinfo_device(root)} /root.slice {specific_mount} rw - cgroup2 cgroup rw\n",
            ):
                with self.subTest(records=records.count("\n")):
                    mountinfo.write_text(records, encoding="ascii")
                    with mock.patch.object(
                        process_priority,
                        "_PROC_SELF_MOUNTINFO",
                        mountinfo,
                    ):
                        identity = (
                            process_priority.priority_scope_identity_for_control_group(
                                "/root.slice/work.scope"
                            )
                        )

                    self.assertIsNotNone(identity)
                    assert identity is not None
                    scope_stat = specific_scope.stat()
                    self.assertEqual(
                        identity,
                        process_priority.PriorityScopeIdentity(
                            os.fspath(specific_scope),
                            scope_stat.st_dev,
                            scope_stat.st_ino,
                        ),
                    )

    def test_control_group_identity_rejects_ambiguous_reverse_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared_mount = root / "shared"
            foreign_scope = shared_mount / "work.scope"
            foreign_scope.mkdir(parents=True)
            (foreign_scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (foreign_scope / "io.weight").write_text(
                "default 200\n",
                encoding="ascii",
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} /owned.slice {shared_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /foreign.slice {shared_mount} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )

            with mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo):
                self.assertIsNone(
                    process_priority.priority_scope_identity_for_control_group(
                        "/owned.slice/work.scope"
                    )
                )

    def test_control_group_identity_deduplicates_identical_mount_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            record = f"35 25 {_mountinfo_device(root)} /root.slice {mountpoint} rw - cgroup2 cgroup rw\n"
            mountinfo.write_text(record + record, encoding="ascii")

            with mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo):
                identity = process_priority.priority_scope_identity_for_control_group(
                    "/root.slice/work.scope"
                )

            self.assertIsNotNone(identity)
            assert identity is not None
            scope_stat = scope.stat()
            self.assertEqual(
                identity,
                process_priority.PriorityScopeIdentity(
                    os.fspath(scope),
                    scope_stat.st_dev,
                    scope_stat.st_ino,
                ),
            )

    def test_scope_identity_rejects_mount_device_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            scope_stat = scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_different_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open = os.open
            scope_descriptors: list[int] = []

            def track_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if dir_fd is None:
                    descriptor = real_open(path, flags, mode)
                else:
                    descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                if dir_fd is None and Path(path) == scope:
                    scope_descriptors.append(descriptor)
                return descriptor

            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_read_scope_file",
                    side_effect=AssertionError("device mismatch reached scope contents"),
                ) as read_scope,
                mock.patch.object(process_priority.os, "open", side_effect=track_open),
                mock.patch.object(
                    process_priority.os,
                    "close",
                    wraps=os.close,
                ) as close_fd,
            ):
                self.assertIsNone(process_priority._cgroup2_path_for_pid(4242))
                self.assertIsNone(
                    process_priority.priority_scope_identity_for_control_group(
                        "/work.scope"
                    )
                )
                self.assertIsNone(process_priority.priority_scope_identity_for_pid(4242))
                self.assertFalse(process_priority.verify_priority_scope_identity(identity))

            read_scope.assert_not_called()
            self.assertEqual(len(scope_descriptors), 2)
            for descriptor in scope_descriptors:
                close_fd.assert_any_call(descriptor)

    def test_cgroup_mapping_rejects_same_path_with_different_devices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n"
                f"36 25 {_different_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )

            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
            ):
                self.assertIsNone(process_priority._cgroup2_path_for_pid(4242))
                self.assertIsNone(
                    process_priority.priority_scope_identity_for_control_group(
                        "/work.scope"
                    )
                )

    def test_control_group_identity_rejects_untrusted_inputs_and_scope(self) -> None:
        oversized = "/" + ("a" * process_priority.MAX_PRIORITY_SCOPE_IDENTITY_PATH_CHARS)
        for control_group in (
            None,
            True,
            "",
            "relative/work.scope",
            "/root.slice/../root.slice/work.scope",
            "/root.slice//work.scope",
            "//root.slice/work.scope",
            "/root.slice/work\x00.scope",
            "/root.slice/wörk.scope",
            "/root.slice/work\n.scope",
            oversized,
        ):
            with (
                self.subTest(control_group=repr(control_group)[:40]),
                mock.patch.object(
                    process_priority,
                    "_cgroup2_mount_mappings",
                    side_effect=AssertionError("invalid input reached mount mapping"),
                ),
            ):
                self.assertIsNone(
                    process_priority.priority_scope_identity_for_control_group(
                        control_group
                    )
                )

        for kwargs in (
            {"cpu_weight": 0},
            {"cpu_weight": True},
            {"io_weight": 10_001},
            {"io_weight": False},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(
                    process_priority.priority_scope_identity_for_control_group(
                        "/root.slice/work.scope",
                        **kwargs,
                    )
                )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_mount = root / "first"
            first_scope = first_mount / "work.scope"
            first_scope.mkdir(parents=True)
            (first_scope / "cpu.weight").write_text("100\n", encoding="ascii")
            (first_scope / "io.weight").write_text("default 200\n", encoding="ascii")
            second_mount = root / "second"
            second_mount.mkdir()
            outside_scope = root / "outside.scope"
            outside_scope.mkdir()
            symlink_scope = first_mount / "linked.scope"
            symlink_scope.symlink_to(outside_scope, target_is_directory=True)
            mountinfo = root / "mountinfo"
            cases = (
                (
                    f"35 25 {_mountinfo_device(root)} /root.slice {first_mount} rw - cgroup2 cgroup rw\n",
                    "/root.slice/work.scope",
                ),
                (
                    f"35 25 {_mountinfo_device(root)} /root.slice {first_mount} rw - cgroup2 cgroup rw\n",
                    "/root.slice/linked.scope",
                ),
                (
                    f"35 25 {_mountinfo_device(root)} /root.slice {first_mount} rw - cgroup2 cgroup rw\n"
                    f"36 25 {_mountinfo_device(root)} /root.slice {second_mount} rw - cgroup2 cgroup rw\n",
                    "/root.slice/work.scope",
                ),
                (
                    f"35 25 {_mountinfo_device(root)} /other.slice {first_mount} rw - cgroup2 cgroup rw\n",
                    "/root.slice/work.scope",
                ),
                ("malformed mountinfo\n", "/root.slice/work.scope"),
            )
            for records, control_group in cases:
                with self.subTest(records=records[:20], control_group=control_group):
                    mountinfo.write_text(records, encoding="ascii")
                    with mock.patch.object(
                        process_priority,
                        "_PROC_SELF_MOUNTINFO",
                        mountinfo,
                    ):
                        self.assertIsNone(
                            process_priority.priority_scope_identity_for_control_group(
                                control_group
                            )
                        )

    def test_control_group_identity_rejects_replacement_after_fd_bind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open_scope = process_priority._open_scope_directory_candidate
            real_read_scope = process_priority._read_scope_file
            real_close = os.close
            scope_descriptors: list[int] = []
            raced = False

            def track_open(
                path: Path,
                mount_mapping: process_priority._Cgroup2MountMapping,
            ) -> tuple[int, os.stat_result] | None:
                opened = real_open_scope(path, mount_mapping)
                if opened is not None:
                    scope_descriptors.append(opened[0])
                return opened

            def racing_read(
                descriptor: int,
                name: str,
                *,
                max_bytes: int,
            ) -> str | None:
                nonlocal raced
                contents = real_read_scope(descriptor, name, max_bytes=max_bytes)
                if name == "io.weight" and not raced:
                    raced = True
                    scope.rename(root / "orphan.scope")
                    scope.mkdir()
                    (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                    (scope / "io.weight").write_text(
                        "default 200\n",
                        encoding="ascii",
                    )
                return contents

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_open_scope_directory_candidate",
                    side_effect=track_open,
                ),
                mock.patch.object(
                    process_priority,
                    "_read_scope_file",
                    side_effect=racing_read,
                ),
                mock.patch.object(process_priority.os, "close", wraps=real_close) as close_fd,
            ):
                self.assertIsNone(
                    process_priority.priority_scope_identity_for_control_group(
                        "/work.scope"
                    )
                )

            self.assertTrue(raced)
            self.assertEqual(len(scope_descriptors), 1)
            close_fd.assert_any_call(scope_descriptors[0])

    def test_pidless_scope_identity_accepts_only_canonical_specific_mount_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generic_mount = root / "generic"
            generic_scope = generic_mount / "root.slice" / "work.scope"
            generic_scope.mkdir(parents=True)
            specific_mount = root / "specific"
            specific_scope = specific_mount / "work.scope"
            specific_scope.mkdir(parents=True)
            for scope in (generic_scope, specific_scope):
                (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {generic_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /root.slice {specific_mount} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            specific_stat = specific_scope.stat()
            specific_identity = process_priority.PriorityScopeIdentity(
                os.fspath(specific_scope),
                specific_stat.st_dev,
                specific_stat.st_ino,
            )
            generic_stat = generic_scope.stat()
            alias_identity = process_priority.PriorityScopeIdentity(
                os.fspath(generic_scope),
                generic_stat.st_dev,
                generic_stat.st_ino,
            )
            with mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo):
                self.assertTrue(
                    process_priority.verify_priority_scope_identity(specific_identity)
                )
                self.assertFalse(
                    process_priority.verify_priority_scope_identity(alias_identity)
                )
                self.assertFalse(
                    process_priority.verify_priority_scope_identity(
                        process_priority.PriorityScopeIdentity(
                            specific_identity.path,
                            specific_identity.device,
                            specific_identity.inode + 1,
                        )
                    )
                )
                (specific_scope / "cpu.weight").write_text("100\n", encoding="ascii")
                self.assertFalse(
                    process_priority.verify_priority_scope_identity(specific_identity)
                )

    def test_scope_identity_rejects_ambiguous_or_malformed_mount_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_mount = root / "first"
            first_scope = first_mount / "work.scope"
            first_scope.mkdir(parents=True)
            (first_scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (first_scope / "io.weight").write_text("default 200\n", encoding="ascii")
            second_mount = root / "second"
            second_mount.mkdir()
            scope_stat = first_scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(first_scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} /root.slice {first_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /root.slice {second_mount} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            with mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo):
                self.assertFalse(process_priority.verify_priority_scope_identity(identity))

            mountinfo.write_text(
                f"bad record /root.slice {first_mount} - cgroup2\n",
                encoding="ascii",
            )
            with mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo):
                self.assertFalse(process_priority.verify_priority_scope_identity(identity))

            outside = root / "outside.scope"
            outside.mkdir()
            outside_stat = outside.stat()
            outside_identity = process_priority.PriorityScopeIdentity(
                os.fspath(outside),
                outside_stat.st_dev,
                outside_stat.st_ino,
            )
            self.assertFalse(
                process_priority.verify_priority_scope_identity(outside_identity)
            )

    def test_scope_identity_rejects_directory_or_symlink_replacement_after_fd_bind(self) -> None:
        for replacement_kind in ("directory", "symlink"):
            with self.subTest(replacement_kind=replacement_kind):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    mountpoint = root / "cgroup"
                    scope = mountpoint / "work.scope"
                    scope.mkdir(parents=True)
                    (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                    (scope / "io.weight").write_text("default 200\n", encoding="ascii")
                    scope_stat = scope.stat()
                    identity = process_priority.PriorityScopeIdentity(
                        os.fspath(scope),
                        scope_stat.st_dev,
                        scope_stat.st_ino,
                    )
                    mountinfo = root / "mountinfo"
                    mountinfo.write_text(
                        f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                        encoding="ascii",
                    )
                    real_open = os.open
                    raced = False

                    def racing_open(
                        path: str | os.PathLike[str],
                        flags: int,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> int:
                        nonlocal raced
                        if path == "cpu.weight" and dir_fd is not None and not raced:
                            raced = True
                            orphan = root / "orphan.scope"
                            scope.rename(orphan)
                            if replacement_kind == "directory":
                                scope.mkdir()
                                (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                                (scope / "io.weight").write_text(
                                    "default 200\n",
                                    encoding="ascii",
                                )
                            else:
                                scope.symlink_to(orphan, target_is_directory=True)
                        if dir_fd is None:
                            return real_open(path, flags, mode)
                        return real_open(path, flags, mode, dir_fd=dir_fd)

                    with (
                        mock.patch.object(
                            process_priority,
                            "_PROC_SELF_MOUNTINFO",
                            mountinfo,
                        ),
                        mock.patch.object(process_priority.os, "open", side_effect=racing_open),
                    ):
                        self.assertFalse(
                            process_priority.verify_priority_scope_identity(identity)
                        )
                    self.assertTrue(raced)

    def test_scope_process_ids_rejects_replacement_without_path_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            (scope / "cgroup.procs").write_text("123\n", encoding="ascii")
            scope_stat = scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open = os.open
            raced = False

            def racing_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal raced
                if path == "cgroup.procs" and dir_fd is not None and not raced:
                    raced = True
                    scope.rename(root / "orphan.scope")
                    scope.mkdir()
                    (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                    (scope / "io.weight").write_text("default 200\n", encoding="ascii")
                    (scope / "cgroup.procs").write_text("999\n", encoding="ascii")
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority.os, "open", side_effect=racing_open),
                mock.patch.object(
                    process_priority,
                    "verify_priority_scope_identity",
                    wraps=process_priority.verify_priority_scope_identity,
                ) as public_verify,
            ):
                self.assertIsNone(process_priority.priority_scope_process_ids(identity))

            self.assertTrue(raced)
            public_verify.assert_not_called()

    def test_scope_identity_rechecks_pid_mapping_after_bound_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            scope_stat = scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_open = os.open
            migrated = False

            def migrating_open(
                path: str | os.PathLike[str],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal migrated
                if path == "io.weight" and dir_fd is not None and not migrated:
                    migrated = True
                    pid_cgroup.write_text("0::/other.scope\n", encoding="ascii")
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with (
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority.os, "open", side_effect=migrating_open),
            ):
                self.assertFalse(
                    process_priority.verify_priority_scope_identity(identity, pid=4242)
                )
            self.assertTrue(migrated)

    def test_scope_identity_rechecks_path_after_final_pid_remap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            scope_stat = scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_stat = os.stat
            events: list[str] = []
            remaps = 0

            def remap_after_reads(
                _pid: int,
                *,
                mappings: tuple[tuple[Path, Path], ...] | None = None,
            ) -> Path:
                nonlocal remaps
                self.assertIsNotNone(mappings)
                remaps += 1
                if remaps == 2:
                    events.append("remap")
                    scope.rename(root / "orphan.scope")
                    scope.mkdir()
                    (scope / "cpu.weight").write_text("200\n", encoding="ascii")
                    (scope / "io.weight").write_text("default 200\n", encoding="ascii")
                return scope

            def track_path_stat(
                path: str | os.PathLike[str],
                *args: object,
                **kwargs: object,
            ) -> os.stat_result:
                if Path(path) == scope and kwargs.get("follow_symlinks") is False:
                    events.append("path-stat")
                return real_stat(path, *args, **kwargs)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_cgroup2_path_for_pid",
                    side_effect=remap_after_reads,
                ) as map_pid,
                mock.patch.object(process_priority.os, "stat", side_effect=track_path_stat),
            ):
                self.assertFalse(
                    process_priority.verify_priority_scope_identity(identity, pid=4242)
                )

        self.assertEqual(map_pid.call_count, 2)
        self.assertEqual(events, ["remap", "path-stat"])

    def test_scope_snapshot_closes_bound_directory_fd_after_child_read_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            scope_stat = scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            scope_fd = os.open(scope, os.O_RDONLY | os.O_DIRECTORY)
            real_close = os.close
            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_open_scope_directory",
                    return_value=scope_fd,
                ),
                mock.patch.object(
                    process_priority,
                    "_read_scope_file",
                    return_value=None,
                ),
                mock.patch.object(
                    process_priority.os,
                    "close",
                    wraps=real_close,
                ) as close_fd,
            ):
                self.assertFalse(
                    process_priority.verify_priority_scope_identity(identity)
                )

            close_fd.assert_any_call(scope_fd)

    def test_scope_process_ids_accepts_zero_sized_pseudo_file_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            (scope / "cgroup.procs").write_text("123\n456\n", encoding="ascii")
            scope_stat = scope.stat()
            identity = process_priority.PriorityScopeIdentity(
                os.fspath(scope),
                scope_stat.st_dev,
                scope_stat.st_ino,
            )
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            real_fstat = os.fstat

            def zero_sized_files(descriptor: int) -> os.stat_result:
                result = real_fstat(descriptor)
                if not stat.S_ISREG(result.st_mode):
                    return result
                values = list(result)
                values[6] = 0
                return os.stat_result(values)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority.os, "fstat", side_effect=zero_sized_files),
            ):
                self.assertEqual(
                    process_priority.priority_scope_process_ids(identity),
                    (123, 456),
                )

    def test_priority_scope_membership_reports_direct_and_hierarchical_members(self) -> None:
        cases = (
            ("populated 0\nfrozen 0\n", "", (), False),
            ("populated 1\nfrozen 0\n", "456\n123\n123\n", (123, 456), True),
            ("populated 1\n", "", (), True),
        )
        for events, process_ids, expected_ids, expected_populated in cases:
            with self.subTest(events=events, process_ids=process_ids):
                with tempfile.TemporaryDirectory() as tmp:
                    identity, mountinfo, _scope = _write_scope_membership_fixture(
                        Path(tmp),
                        events=events,
                        process_ids=process_ids,
                    )
                    with mock.patch.object(
                        process_priority,
                        "_PROC_SELF_MOUNTINFO",
                        mountinfo,
                    ):
                        membership = process_priority.priority_scope_membership(identity)

                self.assertEqual(
                    membership,
                    process_priority.PriorityScopeMembership(
                        process_ids=expected_ids,
                        populated=expected_populated,
                    ),
                )
                self.assertNotIn("123", repr(membership))

    def test_priority_scope_membership_rejects_unpopulated_scope_with_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity, mountinfo, _scope = _write_scope_membership_fixture(
                Path(tmp),
                events="populated 0\n",
                process_ids="123\n",
            )
            with mock.patch.object(
                process_priority,
                "_PROC_SELF_MOUNTINFO",
                mountinfo,
            ):
                self.assertIsNone(process_priority.priority_scope_membership(identity))

    def test_priority_scope_membership_rejects_malformed_duplicate_or_oversized_events(
        self,
    ) -> None:
        malformed_values = (
            "populated\n",
            "populated 1\npopulated 1\n",
            "populated 2\n",
            "populated 1\nunknown nope\n",
            "populated 1\r\n",
            "populated 1",
            "populated 1\n\n",
            "populated 01\n",
            "populated 1\nfuture 01\n",
            "populated 1\nfuture " + ("9" * 21) + "\n",
            "populated 1\n" + "".join(f"unknown{index} 0\n" for index in range(32)),
        )
        for events in malformed_values:
            with self.subTest(events=events[:32]):
                with tempfile.TemporaryDirectory() as tmp:
                    identity, mountinfo, _scope = _write_scope_membership_fixture(
                        Path(tmp),
                        events=events,
                    )
                    with mock.patch.object(
                        process_priority,
                        "_PROC_SELF_MOUNTINFO",
                        mountinfo,
                    ):
                        self.assertIsNone(
                            process_priority.priority_scope_membership(identity)
                        )

    def test_priority_scope_membership_rejects_events_change_between_reads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity, mountinfo, scope = _write_scope_membership_fixture(
                Path(tmp),
                events="populated 1\nfrozen 0\n",
            )
            real_read = process_priority._read_scope_file_snapshot
            changed = False

            def change_after_process_read(
                scope_descriptor: int,
                name: str,
                *,
                max_bytes: int,
                **kwargs: object,
            ) -> object:
                nonlocal changed
                result = real_read(
                    scope_descriptor,
                    name,
                    max_bytes=max_bytes,
                    **kwargs,
                )
                if name == "cgroup.procs" and not changed:
                    changed = True
                    (scope / "cgroup.events").write_text(
                        "populated 1\nfrozen 1\n",
                        encoding="ascii",
                    )
                return result

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_read_scope_file_snapshot",
                    side_effect=change_after_process_read,
                ),
            ):
                self.assertIsNone(process_priority.priority_scope_membership(identity))
            self.assertTrue(changed)

    def test_priority_scope_membership_rechecks_processes_semantically(self) -> None:
        cases = (
            ("123\n", "456\n", None),
            (
                "123\n456\n",
                "456\n123\n123\n",
                process_priority.PriorityScopeMembership(
                    process_ids=(123, 456),
                    populated=True,
                ),
            ),
        )
        for initial, replacement, expected in cases:
            with self.subTest(initial=initial, replacement=replacement):
                with tempfile.TemporaryDirectory() as tmp:
                    identity, mountinfo, scope = _write_scope_membership_fixture(
                        Path(tmp),
                        events="populated 1\n",
                        process_ids=initial,
                    )
                    real_snapshot = process_priority._read_scope_file_snapshot
                    process_reads = 0

                    def replace_after_first_process_snapshot(
                        scope_descriptor: int,
                        name: str,
                        *,
                        max_bytes: int,
                        **kwargs: object,
                    ) -> object:
                        nonlocal process_reads
                        result = real_snapshot(
                            scope_descriptor,
                            name,
                            max_bytes=max_bytes,
                            **kwargs,
                        )
                        if name == "cgroup.procs":
                            process_reads += 1
                            if process_reads == 1:
                                (scope / "cgroup.procs").write_text(
                                    replacement,
                                    encoding="ascii",
                                )
                        return result

                    with (
                        mock.patch.object(
                            process_priority,
                            "_PROC_SELF_MOUNTINFO",
                            mountinfo,
                        ),
                        mock.patch.object(
                            process_priority,
                            "_read_scope_file_snapshot",
                            side_effect=replace_after_first_process_snapshot,
                        ),
                    ):
                        actual = process_priority.priority_scope_membership(identity)

                self.assertEqual(process_reads, 2)
                self.assertEqual(actual, expected)

    def test_priority_scope_membership_rejects_process_file_inode_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity, mountinfo, scope = _write_scope_membership_fixture(
                root,
                events="populated 1\n",
                process_ids="123\n",
            )
            real_snapshot = process_priority._read_scope_file_snapshot
            process_reads = 0

            def replace_after_first_process_snapshot(
                scope_descriptor: int,
                name: str,
                *,
                max_bytes: int,
                **kwargs: object,
            ) -> object:
                nonlocal process_reads
                result = real_snapshot(
                    scope_descriptor,
                    name,
                    max_bytes=max_bytes,
                    **kwargs,
                )
                if name == "cgroup.procs":
                    process_reads += 1
                    if process_reads == 1:
                        replacement = root / "replacement.procs"
                        replacement.write_text("123\n", encoding="ascii")
                        os.replace(replacement, scope / "cgroup.procs")
                return result

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_read_scope_file_snapshot",
                    side_effect=replace_after_first_process_snapshot,
                ),
            ):
                self.assertIsNone(process_priority.priority_scope_membership(identity))
            self.assertEqual(process_reads, 2)

    def test_priority_scope_membership_rejects_noncanonical_process_grammar(self) -> None:
        invalid_process_contents = (
            "0123\n",
            "2147483648\n",
            ("9" * 21) + "\n",
            "123",
            "123\r\n",
            "123\n\n",
        )
        for process_contents in invalid_process_contents:
            with self.subTest(process_contents=process_contents[:16]):
                with tempfile.TemporaryDirectory() as tmp:
                    identity, mountinfo, _scope = _write_scope_membership_fixture(
                        Path(tmp),
                        events="populated 1\n",
                        process_ids=process_contents,
                    )
                    with mock.patch.object(
                        process_priority,
                        "_PROC_SELF_MOUNTINFO",
                        mountinfo,
                    ):
                        self.assertIsNone(
                            process_priority.priority_scope_membership(identity)
                        )

    def test_priority_scope_membership_mount_binds_every_child_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity, mountinfo, _scope = _write_scope_membership_fixture(
                Path(tmp),
                events="populated 1\n",
                process_ids="123\n",
            )
            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_read_scope_file_snapshot",
                    wraps=process_priority._read_scope_file_snapshot,
                ) as read_snapshot,
            ):
                self.assertIsNotNone(
                    process_priority.priority_scope_membership(identity)
                )

            self.assertEqual(
                [call.args[1] for call in read_snapshot.call_args_list],
                [
                    "cpu.weight",
                    "io.weight",
                    "cgroup.events",
                    "cgroup.procs",
                    "cgroup.procs",
                    "cgroup.events",
                ],
            )
            for call in read_snapshot.call_args_list:
                self.assertIsInstance(
                    call.kwargs.get("mount_mapping"),
                    process_priority._Cgroup2MountMapping,
                )
                self.assertIs(type(call.kwargs.get("expected_mount_id")), int)

    def test_scope_file_snapshot_rejects_foreign_device_or_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _identity, _mountinfo, scope = _write_scope_membership_fixture(root)
            scope_fd = os.open(scope, os.O_RDONLY | os.O_DIRECTORY)
            scope_device = scope.stat().st_dev
            correct_mapping = process_priority._Cgroup2MountMapping(
                mount_root=Path("/"),
                mountpoint=scope.parent,
                device_major=os.major(scope_device),
                device_minor=os.minor(scope_device),
            )
            foreign_mapping = process_priority._Cgroup2MountMapping(
                mount_root=Path("/"),
                mountpoint=scope.parent,
                device_major=os.major(scope_device) + 1,
                device_minor=os.minor(scope_device),
            )
            try:
                with self.subTest(boundary="device"):
                    with mock.patch.object(
                        process_priority,
                        "_descriptor_mount_id",
                        return_value=41,
                        create=True,
                    ):
                        self.assertIsNone(
                            process_priority._read_scope_file_snapshot(
                                scope_fd,
                                "cgroup.events",
                                max_bytes=process_priority.MAX_CGROUP_FILE_BYTES,
                                mount_mapping=foreign_mapping,
                                expected_mount_id=41,
                            )
                        )
                with self.subTest(boundary="mount"):
                    real_open = os.open
                    real_read = os.read
                    real_close = os.close
                    child_descriptors: list[int] = []

                    def track_child_open(
                        path: str | os.PathLike[str],
                        flags: int,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> int:
                        if dir_fd is None:
                            descriptor = real_open(path, flags, mode)
                        else:
                            descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
                        if path == "cgroup.events" and dir_fd == scope_fd:
                            child_descriptors.append(descriptor)
                        return descriptor

                    with (
                        mock.patch.object(
                            process_priority,
                            "_descriptor_mount_id",
                            side_effect=(41, 42),
                        ) as mount_id,
                        mock.patch.object(
                            process_priority.os,
                            "open",
                            side_effect=track_child_open,
                        ),
                        mock.patch.object(
                            process_priority.os,
                            "read",
                            wraps=real_read,
                        ) as read_descriptor,
                        mock.patch.object(
                            process_priority.os,
                            "close",
                            wraps=real_close,
                        ) as close_descriptor,
                    ):
                        self.assertIsNone(
                            process_priority._read_scope_file_snapshot(
                                scope_fd,
                                "cgroup.events",
                                max_bytes=process_priority.MAX_CGROUP_FILE_BYTES,
                                mount_mapping=correct_mapping,
                                expected_mount_id=41,
                            )
                        )
                    self.assertEqual(mount_id.call_count, 2)
                    self.assertEqual(len(child_descriptors), 1)
                    child_descriptor = child_descriptors[0]
                    self.assertEqual(
                        mount_id.call_args_list,
                        [mock.call(child_descriptor), mock.call(child_descriptor)],
                    )
                    read_descriptor.assert_called()
                    self.assertEqual(
                        sum(
                            call.args == (child_descriptor,)
                            for call in close_descriptor.call_args_list
                        ),
                        1,
                    )
                    with self.assertRaises(OSError):
                        os.fstat(child_descriptor)
            finally:
                os.close(scope_fd)

    def test_priority_scope_membership_rejects_final_scope_mount_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity, mountinfo, _scope = _write_scope_membership_fixture(
                Path(tmp),
                events="populated 1\n",
                process_ids="123\n",
            )
            mount_descriptors: list[int] = []
            child_names: list[str] = []
            child_snapshot_count = 6
            expected_mount_calls = (2 * child_snapshot_count) + 2
            real_snapshot = process_priority._read_scope_file_snapshot

            def change_on_final_scope_check(descriptor: int) -> int:
                mount_descriptors.append(descriptor)
                return 42 if len(mount_descriptors) == expected_mount_calls else 41

            def assert_valid_child_snapshot(
                scope_descriptor: int,
                name: str,
                *,
                max_bytes: int,
                **kwargs: object,
            ) -> object:
                snapshot = real_snapshot(
                    scope_descriptor,
                    name,
                    max_bytes=max_bytes,
                    **kwargs,
                )
                self.assertIsNotNone(snapshot)
                child_names.append(name)
                return snapshot

            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_descriptor_mount_id",
                    side_effect=change_on_final_scope_check,
                ) as mount_id,
                mock.patch.object(
                    process_priority,
                    "_read_scope_file_snapshot",
                    side_effect=assert_valid_child_snapshot,
                ),
            ):
                self.assertIsNone(process_priority.priority_scope_membership(identity))

            self.assertEqual(mount_id.call_count, expected_mount_calls)
            self.assertEqual(
                child_names,
                [
                    "cpu.weight",
                    "io.weight",
                    "cgroup.events",
                    "cgroup.procs",
                    "cgroup.procs",
                    "cgroup.events",
                ],
            )
            self.assertEqual(mount_descriptors[0], mount_descriptors[-1])
            for index in range(1, expected_mount_calls - 1, 2):
                self.assertEqual(mount_descriptors[index], mount_descriptors[index + 1])
                self.assertNotEqual(mount_descriptors[index], mount_descriptors[0])

    def test_priority_scope_membership_rejects_events_symlink_or_inode_replacement(
        self,
    ) -> None:
        for replacement in ("symlink", "inode"):
            with self.subTest(replacement=replacement):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    identity, mountinfo, scope = _write_scope_membership_fixture(
                        root,
                        events="populated 1\n",
                    )
                    real_read = process_priority._read_scope_file_snapshot
                    replaced = False

                    def replace_after_process_read(
                        scope_descriptor: int,
                        name: str,
                        *,
                        max_bytes: int,
                        **kwargs: object,
                    ) -> object:
                        nonlocal replaced
                        result = real_read(
                            scope_descriptor,
                            name,
                            max_bytes=max_bytes,
                            **kwargs,
                        )
                        if name == "cgroup.procs" and not replaced:
                            replaced = True
                            events_path = scope / "cgroup.events"
                            if replacement == "symlink":
                                target = root / "replacement.events"
                                target.write_text("populated 1\n", encoding="ascii")
                                events_path.unlink()
                                events_path.symlink_to(target)
                            else:
                                replacement_path = root / "replacement.events"
                                replacement_path.write_text(
                                    "populated 1\n",
                                    encoding="ascii",
                                )
                                os.replace(replacement_path, events_path)
                        return result

                    with (
                        mock.patch.object(
                            process_priority,
                            "_PROC_SELF_MOUNTINFO",
                            mountinfo,
                        ),
                        mock.patch.object(
                            process_priority,
                            "_read_scope_file_snapshot",
                            side_effect=replace_after_process_read,
                        ),
                    ):
                        self.assertIsNone(
                            process_priority.priority_scope_membership(identity)
                        )
                    self.assertTrue(replaced)

    def test_priority_scope_membership_closes_scope_fd_on_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            identity, mountinfo, scope = _write_scope_membership_fixture(root)
            (scope / "cgroup.events").unlink()
            scope_fd = os.open(scope, os.O_RDONLY | os.O_DIRECTORY)
            real_close = os.close
            with (
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(
                    process_priority,
                    "_open_scope_directory",
                    return_value=scope_fd,
                ),
                mock.patch.object(
                    process_priority.os,
                    "close",
                    wraps=real_close,
                ) as close_fd,
            ):
                self.assertIsNone(process_priority.priority_scope_membership(identity))

            close_fd.assert_any_call(scope_fd)

    def test_scope_affinity_rejects_cgroup_v1_and_symlinked_cpuset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup = root / "self.cgroup"
            mountinfo = root / "mountinfo"
            online = root / "online"
            cgroup.write_text("5:cpuset:/legacy\n", encoding="ascii")
            mountinfo.write_text("", encoding="ascii")
            online.write_text("0-3\n", encoding="ascii")
            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                with self.assertRaisesRegex(process_priority.PriorityScopeError, "cgroup-v2"):
                    process_priority._scope_exec_allowed_cpus()

            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            outside = root / "outside"
            outside.write_text("0-3\n", encoding="ascii")
            (scope / "cpuset.cpus.effective").symlink_to(outside)
            cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountinfo.write_text(
                f"36 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                with self.assertRaisesRegex(process_priority.PriorityScopeError, "cpuset"):
                    process_priority._scope_exec_allowed_cpus()

    def test_scope_affinity_rejects_oversized_or_unreadable_cpu_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            cpuset = scope / "cpuset.cpus.effective"
            cpuset.write_text("9" * 65_537, encoding="ascii")
            cgroup = root / "self.cgroup"
            cgroup.write_text("0::/work.scope\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"36 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            online = root / "online"
            online.write_text("0-3\n", encoding="ascii")
            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                with self.assertRaisesRegex(process_priority.PriorityScopeError, "cpuset"):
                    process_priority._scope_exec_allowed_cpus()

            cpuset.write_text("0-3\n", encoding="ascii")
            real_open = os.open

            def deny_cpuset(
                path: str | bytes | os.PathLike[str],
                flags: int,
                *,
                dir_fd: int | None = None,
            ) -> int:
                if os.fsdecode(path) == "cpuset.cpus.effective" and dir_fd is not None:
                    raise PermissionError("denied")
                return real_open(path, flags, dir_fd=dir_fd)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority.os,
                    "open",
                    side_effect=deny_cpuset,
                ),
            ):
                with self.assertRaisesRegex(process_priority.PriorityScopeError, "cpuset"):
                    process_priority._scope_exec_allowed_cpus()

    def test_scope_affinity_set_degrades_but_outside_verification_fails_closed(self) -> None:
        target = frozenset({0, 1})
        with (
            mock.patch.object(process_priority, "_scope_exec_allowed_cpus", return_value=target),
            mock.patch.object(process_priority.os, "sched_getaffinity", return_value={0}),
            mock.patch.object(
                process_priority.os,
                "sched_setaffinity",
                side_effect=PermissionError("denied"),
            ),
        ):
            self.assertFalse(process_priority._normalize_cpu_affinity_for_scope_exec())

        for verified in (OSError("readback unavailable"), {0}):
            with (
                self.subTest(verified=verified),
                mock.patch.object(
                    process_priority,
                    "_scope_exec_allowed_cpus",
                    return_value=target,
                ),
                mock.patch.object(
                    process_priority.os,
                    "sched_getaffinity",
                    side_effect=[{0}, verified],
                ),
                mock.patch.object(
                    process_priority.os,
                    "sched_setaffinity",
                ) as set_affinity,
            ):
                self.assertTrue(
                    process_priority._normalize_cpu_affinity_for_scope_exec()
                )

            set_affinity.assert_called_once_with(0, target)

        with (
            mock.patch.object(process_priority, "_scope_exec_allowed_cpus", return_value=target),
            mock.patch.object(
                process_priority.os,
                "sched_getaffinity",
                side_effect=[{0}, {0, 2}],
            ),
            mock.patch.object(process_priority.os, "sched_setaffinity") as set_affinity,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._normalize_cpu_affinity_for_scope_exec()
        self.assertEqual(str(raised.exception), "SOC CPU affinity normalization failed")
        self.assertEqual(getattr(raised.exception, "phase", None), "verify")
        set_affinity.assert_called_once_with(0, target)

    def test_scope_exec_wrapper_continues_once_after_affinity_target_failure(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_scope_exec_allowed_cpus",
                side_effect=process_priority.PriorityScopeError(
                    "private target /secret/cgroup pid 4242"
                ),
            ),
            mock.patch.object(process_priority.os, "sched_getaffinity") as get_affinity,
            mock.patch.object(process_priority.os, "sched_setaffinity") as set_affinity,
            mock.patch.object(
                process_priority.os,
                "execve",
                side_effect=OSError("expected test stop"),
            ) as execve,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        self.assertEqual(
            str(raised.exception),
            "SOC priority scope wrapper failed: exec",
        )
        get_affinity.assert_not_called()
        set_affinity.assert_not_called()
        execve.assert_called_once()

    def test_scope_exec_wrapper_continues_once_after_affinity_read_failure(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_scope_exec_allowed_cpus",
                return_value=frozenset({0, 1}),
            ),
            mock.patch.object(
                process_priority.os,
                "sched_getaffinity",
                side_effect=OSError("private affinity read /secret/path"),
            ),
            mock.patch.object(process_priority.os, "sched_setaffinity") as set_affinity,
            mock.patch.object(
                process_priority.os,
                "execve",
                side_effect=OSError("expected test stop"),
            ) as execve,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        self.assertEqual(
            str(raised.exception),
            "SOC priority scope wrapper failed: exec",
        )
        set_affinity.assert_not_called()
        execve.assert_called_once()

    def test_scope_exec_wrapper_continues_once_after_affinity_set_failure(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_scope_exec_allowed_cpus",
                return_value=frozenset({0, 1}),
            ),
            mock.patch.object(
                process_priority.os,
                "sched_getaffinity",
                return_value={0},
            ),
            mock.patch.object(
                process_priority.os,
                "sched_setaffinity",
                side_effect=PermissionError("private set failure cpu list"),
            ) as set_affinity,
            mock.patch.object(
                process_priority.os,
                "execve",
                side_effect=OSError("expected test stop"),
            ) as execve,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        self.assertEqual(
            str(raised.exception),
            "SOC priority scope wrapper failed: exec",
        )
        self.assertEqual(set_affinity.call_count, 1)
        execve.assert_called_once()

    def test_scope_exec_wrapper_executes_once_after_applied_affinity_readback(self) -> None:
        target = frozenset({0, 1})
        for readback in (OSError("readback unavailable"), {0}):
            with (
                self.subTest(readback=readback),
                mock.patch.object(
                    process_priority,
                    "_scope_exec_allowed_cpus",
                    return_value=target,
                ),
                mock.patch.object(
                    process_priority.os,
                    "sched_getaffinity",
                    side_effect=[{0}, readback],
                ),
                mock.patch.object(
                    process_priority.os,
                    "sched_setaffinity",
                ) as set_affinity,
                mock.patch.object(
                    process_priority.os,
                    "execve",
                    side_effect=OSError("expected test stop"),
                ) as execve,
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    ": exec",
                ):
                    process_priority._run_scope_exec_wrapper(
                        _scope_exec_test_arguments()
                    )

            set_affinity.assert_called_once_with(0, target)
            execve.assert_called_once()

    def test_scope_exec_wrapper_reports_affinity_verification_failure(self) -> None:
        target = frozenset({0, 1})
        with (
            mock.patch.object(
                process_priority,
                "_scope_exec_allowed_cpus",
                return_value=target,
            ),
            mock.patch.object(
                process_priority.os,
                "sched_getaffinity",
                side_effect=({0}, {0, 2}),
            ),
            mock.patch.object(process_priority.os, "sched_setaffinity") as set_affinity,
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        self.assertEqual(
            str(raised.exception),
            "SOC priority scope wrapper failed: affinity-verify",
        )
        set_affinity.assert_called_once_with(0, target)
        execve.assert_not_called()

    def test_scope_exec_wrapper_normalizes_then_execs_exact_target_and_environment(self) -> None:
        events: list[str] = []
        captured: dict[str, object] = {}

        def fail_exec(
            descriptor: int,
            argv: list[str],
            env: dict[str, str],
        ) -> None:
            events.append("exec")
            captured.update(
                descriptor=descriptor,
                identity=(os.fstat(descriptor).st_dev, os.fstat(descriptor).st_ino),
                argv=argv,
                env=env,
            )
            raise OSError("private exec detail: /sensitive/path pid=1234")

        with (
            mock.patch.dict(process_priority.os.environ, {"SOC_TEST_ENV": "kept"}, clear=True),
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
                side_effect=lambda: events.append("normalize"),
            ) as normalize,
            mock.patch.object(process_priority.os, "execve", side_effect=fail_exec),
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(
                    _scope_exec_test_arguments("secret-argument")
                )

        normalize.assert_called_once_with()
        self.assertEqual(events, ["normalize", "exec"])
        self.assertIsInstance(captured["descriptor"], int)
        runtime_stat = os.stat(os.path.realpath(sys.executable))
        self.assertEqual(
            captured["identity"],
            (runtime_stat.st_dev, runtime_stat.st_ino),
        )
        self.assertEqual(
            captured["argv"],
            [os.path.realpath(sys.executable), "secret-argument"],
        )
        self.assertEqual(captured["env"], {"SOC_TEST_ENV": "kept"})
        self.assertEqual(str(raised.exception), "SOC priority scope wrapper failed: exec")
        self.assertNotIn("secret-argument", str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("1234", str(raised.exception))

    def test_scope_exec_wrapper_executes_bound_fd_across_path_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            original = Path(tmp) / "original"
            shutil.copyfile(os.path.realpath(sys.executable), target)
            target.chmod(0o700)
            original_stat = target.stat()
            arguments = process_priority._scope_exec_wrapper_command([os.fspath(target)])[2:]
            captured: dict[str, object] = {}

            def replace_then_fail_exec(
                descriptor: int,
                argv: list[str],
                _environment: dict[str, str],
            ) -> None:
                target.rename(original)
                target.write_bytes(b"\x7fELFreplacement")
                target.chmod(0o700)
                bound_stat = os.fstat(descriptor)
                captured.update(
                    descriptor=descriptor,
                    argv=argv,
                    identity=(bound_stat.st_dev, bound_stat.st_ino),
                )
                raise OSError("expected test stop")

            with (
                mock.patch.object(
                    process_priority,
                    "_normalize_cpu_affinity_for_scope_exec",
                    return_value=True,
                ),
                mock.patch.object(
                    process_priority.os,
                    "execve",
                    side_effect=replace_then_fail_exec,
                ) as execve,
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    ": exec",
                ):
                    process_priority._run_scope_exec_wrapper(arguments)

            execve.assert_called_once()
            self.assertIsInstance(captured["descriptor"], int)
            self.assertEqual(captured["argv"], [os.fspath(target)])
            self.assertEqual(
                captured["identity"],
                (original_stat.st_dev, original_stat.st_ino),
            )
            self.assertNotEqual(target.stat().st_ino, original_stat.st_ino)
            with self.assertRaises(OSError):
                os.fstat(captured["descriptor"])

    def test_scope_exec_target_requires_exact_bounded_elf_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid_magic = root / "valid-magic"
            short_magic = root / "short-magic"
            shebang = root / "script"
            valid_magic.write_bytes(b"\x7fELF" + (b"x" * 123))
            short_magic.write_bytes(b"\x7fEL")
            shebang.write_text("#!/bin/sh\nexit 7\n", encoding="ascii")
            for candidate in (valid_magic, short_magic, shebang):
                candidate.chmod(0o700)

            self.assertIsNotNone(
                process_priority._scope_exec_launch_spec([os.fspath(valid_magic)])
            )
            self.assertIsNone(
                process_priority._scope_exec_launch_spec([os.fspath(short_magic)])
            )
            self.assertIsNone(
                process_priority._scope_exec_launch_spec([os.fspath(shebang)])
            )

    def test_scope_exec_direct_shebang_target_fails_before_scope_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "script"
            script.write_text("#!/bin/sh\nexit 7\n", encoding="ascii")
            script.chmod(0o700)
            with mock.patch.object(
                process_priority,
                "_required_scope_tool",
            ) as scope_tool:
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    "empty or invalid",
                ):
                    process_priority.build_soc_priority_scope_command(
                        [os.fspath(script)]
                    )

            self.assertIsNotNone(
                process_priority._scope_exec_launch_spec(
                    [os.path.realpath(sys.executable), os.fspath(script)]
                )
            )

        scope_tool.assert_not_called()

    def test_scope_exec_bound_elf_fd_executes_without_systemd(self) -> None:
        arguments = _scope_exec_test_arguments(
            "-c",
            "print('scope-fd-exec-ok')",
        )
        probe = (
            "import json, sys; "
            "from speed_of_cinnamon import process_priority as p; "
            "p._normalize_cpu_affinity_for_scope_exec=lambda: False; "
            "p._run_scope_exec_wrapper(json.loads(sys.argv[1]))"
        )
        environment = os.environ.copy()
        environment.update(
            PYTHONDONTWRITEBYTECODE="1",
            PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
        )
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", probe, json.dumps(arguments)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=5.0,
            shell=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(result.stdout, b"scope-fd-exec-ok\n")

    def test_scope_exec_wrapper_rejects_invalid_or_reentrant_argv_before_normalize(self) -> None:
        runtime = os.path.realpath(sys.executable)
        entry = os.path.realpath(process_priority.__file__)
        invalid_arguments = (
            [],
            ["wrong-token", "--", "/usr/bin/probe"],
            ["--speed-of-cinnamon-scope-exec", "/usr/bin/probe"],
            ["--speed-of-cinnamon-scope-exec", "--"],
            ["--speed-of-cinnamon-scope-exec", "--", "relative-probe"],
            ["--speed-of-cinnamon-scope-exec", "--", "/usr/bin/probe", ""],
            ["--speed-of-cinnamon-scope-exec", "--", "/usr/bin/probe", "bad\x00arg"],
            ["--speed-of-cinnamon-scope-exec", "--", "/usr/bin/probe", "x" * 131_073],
            [
                "--speed-of-cinnamon-scope-exec",
                "--",
                runtime,
                entry,
                "--speed-of-cinnamon-scope-exec",
                "--",
                "/usr/bin/probe",
            ],
        )
        with (
            mock.patch.object(process_priority, "_normalize_cpu_affinity_for_scope_exec") as normalize,
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            for arguments in invalid_arguments:
                with self.subTest(arguments=arguments[:3]):
                    with self.assertRaises(process_priority.PriorityScopeError) as raised:
                        process_priority._run_scope_exec_wrapper(arguments)
                    self.assertEqual(
                        str(raised.exception),
                        "SOC priority scope wrapper failed: arguments",
                    )
                    self.assertNotIn("relative-probe", str(raised.exception))

        normalize.assert_not_called()
        execve.assert_not_called()

    def test_scope_exec_wrapper_degrades_unknown_qos_failure_before_exec(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
                side_effect=process_priority.PriorityScopeError(
                    "private affinity detail: /sensitive/path pid=1234"
                ),
            ),
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        self.assertEqual(str(raised.exception), "SOC priority scope wrapper failed: exec")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("1234", str(raised.exception))
        execve.assert_called_once()

    def test_scope_exec_wrapper_redacts_unknown_structured_affinity_phase(self) -> None:
        private_phase = "private-phase:/sensitive/path:pid=1234"
        with (
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
                side_effect=process_priority._ScopeExecAffinityError(private_phase),
            ),
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            with self.assertRaises(process_priority.PriorityScopeError) as raised:
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        self.assertEqual(str(raised.exception), "SOC priority scope wrapper failed: affinity")
        self.assertNotIn(private_phase, str(raised.exception))
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("1234", str(raised.exception))
        execve.assert_not_called()

    def test_scope_exec_main_emits_only_allowlisted_phase_failures(self) -> None:
        phase_failures = (
            "SOC priority scope wrapper failed: arguments",
            "SOC priority scope wrapper failed: affinity",
            "SOC priority scope wrapper failed: affinity-target",
            "SOC priority scope wrapper failed: affinity-read",
            "SOC priority scope wrapper failed: affinity-set",
            "SOC priority scope wrapper failed: affinity-verify",
            "SOC priority scope wrapper failed: exec",
        )
        for failure in phase_failures:
            with self.subTest(failure=failure), mock.patch.object(
                process_priority,
                "_run_scope_exec_wrapper",
                side_effect=process_priority.PriorityScopeError(failure),
            ):
                with self.assertRaises(SystemExit) as raised:
                    process_priority._scope_exec_main()

            self.assertEqual(raised.exception.code, failure)

        private_detail = "private /sensitive/path pid=1234 unit=secret.scope"
        with mock.patch.object(
            process_priority,
            "_run_scope_exec_wrapper",
            side_effect=process_priority.PriorityScopeError(private_detail),
        ):
            with self.assertRaises(SystemExit) as raised:
                process_priority._scope_exec_main()

        self.assertEqual(raised.exception.code, "SOC priority scope wrapper failed")
        self.assertNotIn("sensitive", str(raised.exception))
        self.assertNotIn("1234", str(raised.exception))
        self.assertNotIn("secret.scope", str(raised.exception))

    def test_scope_exec_wrapper_expands_child_affinity_before_target_exec(self) -> None:
        if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
            self.skipTest("Linux CPU affinity APIs are unavailable")
        available = sorted(process_priority._scope_exec_allowed_cpus())
        if len(available) < 2:
            self.skipTest("multiple cgroup-allowed CPUs are unavailable")
        target = available[:2]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mountpoint = root / "cgroup"
            scope = mountpoint / "target.scope"
            scope.mkdir(parents=True)
            (scope / "cpuset.cpus.effective").write_text(
                f"{target[0]},{target[1]}\n",
                encoding="ascii",
            )
            cgroup = root / "self.cgroup"
            cgroup.write_text("0::/target.scope\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"36 25 {_mountinfo_device(root)} / {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            online = root / "online"
            online.write_text(f"{target[0]},{target[1]}\n", encoding="ascii")
            probe = (
                "import json, os; "
                "print(json.dumps(sorted(os.sched_getaffinity(0))))"
            )
            wrapper = (
                "import os, sys; from pathlib import Path; "
                "from speed_of_cinnamon import process_priority as p; "
                "p._PROC_SELF_CGROUP=Path(os.environ['SOC_TEST_CGROUP']); "
                "p._PROC_SELF_MOUNTINFO=Path(os.environ['SOC_TEST_MOUNTINFO']); "
                "p._CPU_ONLINE=Path(os.environ['SOC_TEST_ONLINE']); "
                f"os.sched_setaffinity(0, {{{target[0]}}}); "
                "command=p._scope_exec_wrapper_command([sys.executable, '-c', "
                "os.environ['SOC_TEST_PROBE']]); "
                "p._run_scope_exec_wrapper(command[2:])"
            )
            environment = os.environ.copy()
            environment.update(
                PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
                SOC_TEST_CGROUP=os.fspath(cgroup),
                SOC_TEST_MOUNTINFO=os.fspath(mountinfo),
                SOC_TEST_ONLINE=os.fspath(online),
                SOC_TEST_PROBE=probe,
            )
            result = subprocess.run(  # nosec B603
                [sys.executable, "-c", wrapper],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=5.0,
                shell=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout.decode("ascii")), target)

    def test_ionice_output_reader_accepts_small_output(self) -> None:
        output = process_priority._read_ionice_output_bounded(
            [sys.executable, "-c", "print('prio 2')"]
        )
        self.assertEqual(output, "prio 2\n")

    def test_current_io_priority_preserves_idle_class(self) -> None:
        with (
            mock.patch.object(process_priority.shutil, "which", return_value="/usr/bin/ionice"),
            mock.patch.object(
                process_priority,
                "_read_ionice_output_bounded",
                return_value="idle: prio 0\n",
            ),
        ):
            self.assertEqual(process_priority._current_io_priority(), ("3", 0))

    def test_ionice_output_reader_rejects_oversized_output(self) -> None:
        output = process_priority._read_ionice_output_bounded(
            [sys.executable, "-c", "import sys; sys.stdout.write('x' * 257)"]
        )
        self.assertIsNone(output)

    def test_ionice_output_reader_bounds_reap_after_timeout(self) -> None:
        process = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = None
        process.wait.side_effect = process_priority.subprocess.TimeoutExpired("ionice", 1)
        selector = mock.Mock()
        selector.get_map.side_effect = [{"stdout": object()}]
        selector.select.return_value = []

        with (
            mock.patch.object(process_priority.subprocess, "Popen", return_value=process),
            mock.patch.object(process_priority.selectors, "DefaultSelector", return_value=selector),
        ):
            output = process_priority._read_ionice_output_bounded(["ionice", "--pid", "1"])

        self.assertIsNone(output)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once()
        self.assertEqual(
            process.wait.call_args.kwargs["timeout"],
            process_priority.IONICE_REAP_TIMEOUT_SECONDS,
        )
        process.stdout.close.assert_called_once_with()

    def test_ionice_output_reader_reaps_when_selector_setup_fails(self) -> None:
        process = mock.Mock()
        process.stdout = mock.Mock()
        process.poll.return_value = None

        with (
            mock.patch.object(process_priority.subprocess, "Popen", return_value=process),
            mock.patch.object(
                process_priority.selectors,
                "DefaultSelector",
                side_effect=OSError("selector unavailable"),
            ),
        ):
            output = process_priority._read_ionice_output_bounded(["ionice", "--pid", "1"])

        self.assertIsNone(output)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once()
        self.assertEqual(
            process.wait.call_args.kwargs["timeout"],
            process_priority.IONICE_REAP_TIMEOUT_SECONDS,
        )
        process.stdout.close.assert_called_once_with()

    def test_ionice_output_reader_bounds_reap_when_stdout_is_unavailable(self) -> None:
        process = mock.Mock()
        process.stdout = None
        process.wait.side_effect = process_priority.subprocess.TimeoutExpired("ionice", 1)

        with mock.patch.object(process_priority.subprocess, "Popen", return_value=process):
            output = process_priority._read_ionice_output_bounded(["ionice", "--pid", "1"])

        self.assertIsNone(output)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=process_priority.IONICE_TIMEOUT_SECONDS)

    def test_apply_process_priority_reports_scope_without_mutating_nice_or_io(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_current_priority_scope_weights",
                return_value=(process_priority.SOC_CPU_WEIGHT, process_priority.SOC_IO_WEIGHT),
            ),
            mock.patch.object(process_priority.os, "setpriority") as setpriority,
            mock.patch.object(process_priority, "_set_io_priority") as set_io,
        ):
            result = process_priority.apply_process_priority()

        self.assertEqual(result, (True, True))
        setpriority.assert_not_called()
        set_io.assert_not_called()

    def test_priority_scope_verification_fails_closed_when_scope_is_missing(self) -> None:
        with (
            mock.patch.object(process_priority, "_current_priority_scope_weights", return_value=None),
        ):
            self.assertEqual(process_priority.apply_process_priority(), (False, False))

    def test_priority_scope_verification_reports_each_weight_independently(self) -> None:
        with (
            mock.patch.object(
                process_priority,
                "_current_priority_scope_weights",
                return_value=(100, process_priority.SOC_IO_WEIGHT),
            ),
        ):
            self.assertEqual(process_priority.apply_process_priority(), (False, True))

    def test_scope_builders_use_internal_affinity_wrapper_after_delimiter(self) -> None:
        runtime = os.path.realpath(sys.executable)
        with mock.patch.object(
            process_priority.shutil,
            "which",
            return_value="/usr/bin/systemd-run",
        ):
            commands = (
                process_priority.build_soc_priority_scope_command(
                    [runtime, "soc"]
                ),
                process_priority.build_recorder_priority_scope_command(
                    [runtime, "recorder"],
                    unit_name="speed-of-cinnamon-recorder-test.scope",
                ),
                process_priority.build_local_model_priority_scope_command(
                    [runtime, "model"]
                ),
            )

        targets = (
            [runtime, "soc"],
            [runtime, "recorder"],
            [runtime, "model"],
        )
        for command_index, (command, target) in enumerate(
            zip(commands, targets, strict=True)
        ):
            with self.subTest(target=target[0]):
                self.assertNotIn("--wait", command)
                delimiter = command.index("--")
                wrapper = command[delimiter + 1 :]
                self.assertEqual(
                    wrapper[:3],
                    [
                        runtime,
                        os.path.realpath(process_priority.__file__),
                        "--speed-of-cinnamon-scope-exec",
                    ],
                )
                identity_start = 4 if command_index == 0 else 3
                if command_index == 0:
                    self.assertEqual(
                        wrapper[3],
                        process_priority._SCOPE_EXEC_LATCH_REQUIRED_TOKEN,
                    )
                else:
                    self.assertNotIn(
                        process_priority._SCOPE_EXEC_LATCH_REQUIRED_TOKEN,
                        wrapper[:4],
                    )
                self.assertEqual(
                    len(wrapper[identity_start : identity_start + 6]),
                    6,
                )
                self.assertTrue(
                    all(
                        value.isascii() and value.isdecimal()
                        for value in wrapper[identity_start : identity_start + 6]
                    )
                )
                self.assertEqual(wrapper[identity_start + 6], "--")
                self.assertEqual(wrapper[identity_start + 7 :], target)
                self.assertNotIn("-m", wrapper[: identity_start + 7])
                self.assertNotIn("CPUAffinity", command)
                self.assertNotIn("AllowedCPUs", command)

    def test_recorder_scope_command_is_named_high_weight_and_delimited(self) -> None:
        with mock.patch.object(
            process_priority.shutil,
            "which",
            return_value="/usr/bin/systemd-run",
        ):
            command = process_priority.build_recorder_priority_scope_command(
                [sys.executable, "-c", "print('ok')"],
                unit_name="speed-of-cinnamon-recorder-test.scope",
            )

        self.assertIn("--unit=speed-of-cinnamon-recorder-test.scope", command)
        self.assertEqual(
            command[-3:],
            [os.path.realpath(sys.executable), "-c", "print('ok')"],
        )
        self.assertEqual(command[command.index("--")], "--")
        wrapper = command[command.index("--") + 1 : -3]
        self.assertEqual(
            wrapper[:3],
            [
                os.path.realpath(sys.executable),
                os.path.realpath(process_priority.__file__),
                "--speed-of-cinnamon-scope-exec",
            ],
        )
        self.assertEqual(len(wrapper[3:9]), 6)
        self.assertTrue(
            all(value.isascii() and value.isdecimal() for value in wrapper[3:9])
        )
        self.assertEqual(wrapper[9], "--")
        self.assertIn("CPUWeight=200", command)
        self.assertIn("IOWeight=200", command)
        self.assertNotIn("CPUAffinity", command)
        self.assertNotIn("AllowedCPUs", command)

    def test_recorder_scope_command_rejects_untrusted_unit_name(self) -> None:
        with mock.patch.object(
            process_priority.shutil,
            "which",
            return_value="/usr/bin/systemd-run",
        ):
            with self.assertRaisesRegex(process_priority.PriorityScopeError, "unit name"):
                process_priority.build_recorder_priority_scope_command(
                    [sys.executable],
                    unit_name="../shared.scope",
                )

    def test_scope_marker_rejects_missing_concrete_scope_identity(self) -> None:
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                clear=False,
            ),
            mock.patch.object(process_priority.os, "getpid", return_value=4242),
            mock.patch.object(
                process_priority,
                "priority_scope_identity_for_pid",
                return_value=None,
            ) as capture_identity,
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ) as normalize_affinity,
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
            ) as reverify_identity,
            mock.patch.object(process_priority, "build_soc_priority_scope_command") as build,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaisesRegex(process_priority.PriorityScopeError, "verification failed"):
                process_priority.ensure_soc_priority_scope(["status", "--json"])

        capture_identity.assert_called_once_with(
            4242,
            cpu_weight=process_priority.SOC_CPU_WEIGHT,
            io_weight=process_priority.SOC_IO_WEIGHT,
        )
        normalize_affinity.assert_not_called()
        reverify_identity.assert_not_called()
        build.assert_not_called()
        execvpe.assert_not_called()

    def test_scope_marker_normalizes_between_identity_capture_and_reverification(self) -> None:
        identity = process_priority.PriorityScopeIdentity(
            path="/sys/fs/cgroup/user.slice/soc.scope",
            device=42,
            inode=1234,
        )
        events: list[str] = []

        def capture(*_args: object, **_kwargs: object) -> process_priority.PriorityScopeIdentity:
            events.append("capture")
            return identity

        def reverify(*_args: object, **_kwargs: object) -> bool:
            events.append("reverify")
            return True

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                clear=False,
            ),
            mock.patch.object(process_priority.os, "getpid", return_value=4242),
            mock.patch.object(
                process_priority,
                "priority_scope_identity_for_pid",
                side_effect=capture,
            ) as capture_identity,
            mock.patch.object(
                process_priority,
                "_scope_exec_allowed_cpus",
                return_value=frozenset({0, 1}),
            ),
            mock.patch.object(
                process_priority.os,
                "sched_getaffinity",
                side_effect=[{0}, {0, 1}],
            ) as get_affinity,
            mock.patch.object(
                process_priority.os,
                "sched_setaffinity",
                side_effect=lambda *_args: events.append("set"),
            ) as set_affinity,
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
                side_effect=reverify,
            ) as reverify_identity,
            mock.patch.object(process_priority, "build_soc_priority_scope_command") as build,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            process_priority.ensure_soc_priority_scope(["status", "--json"])

        self.assertEqual(events, ["capture", "set", "reverify"])
        capture_identity.assert_called_once_with(
            4242,
            cpu_weight=process_priority.SOC_CPU_WEIGHT,
            io_weight=process_priority.SOC_IO_WEIGHT,
        )
        self.assertEqual(get_affinity.call_args_list, [mock.call(0), mock.call(0)])
        set_affinity.assert_called_once_with(0, frozenset({0, 1}))
        reverify_identity.assert_called_once_with(
            identity,
            pid=4242,
            cpu_weight=process_priority.SOC_CPU_WEIGHT,
            io_weight=process_priority.SOC_IO_WEIGHT,
        )
        build.assert_not_called()
        execvpe.assert_not_called()

    def test_scope_marker_rejects_scope_change_after_affinity_normalization(self) -> None:
        identity = process_priority.PriorityScopeIdentity(
            path="/sys/fs/cgroup/user.slice/soc.scope",
            device=42,
            inode=1234,
        )
        events: list[str] = []
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                clear=False,
            ),
            mock.patch.object(process_priority.os, "getpid", return_value=4242),
            mock.patch.object(
                process_priority,
                "priority_scope_identity_for_pid",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("capture") or identity
                ),
            ),
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
                side_effect=lambda: events.append("normalize"),
            ),
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
                side_effect=lambda *_args, **_kwargs: (
                    events.append("reverify") or False
                ),
            ) as reverify_identity,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaisesRegex(process_priority.PriorityScopeError, "verification failed"):
                process_priority.ensure_soc_priority_scope(["status", "--json"])

        self.assertEqual(events, ["capture", "normalize", "reverify"])
        reverify_identity.assert_called_once_with(
            identity,
            pid=4242,
            cpu_weight=process_priority.SOC_CPU_WEIGHT,
            io_weight=process_priority.SOC_IO_WEIGHT,
        )
        execvpe.assert_not_called()

    def test_scope_marker_affinity_failure_stops_before_identity_reverification(self) -> None:
        identity = process_priority.PriorityScopeIdentity(
            path="/sys/fs/cgroup/user.slice/soc.scope",
            device=42,
            inode=1234,
        )
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                clear=False,
            ),
            mock.patch.object(process_priority.os, "getpid", return_value=4242),
            mock.patch.object(
                process_priority,
                "priority_scope_identity_for_pid",
                return_value=identity,
            ),
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
                side_effect=process_priority.PriorityScopeError("affinity unavailable"),
            ),
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
            ) as reverify_identity,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaisesRegex(process_priority.PriorityScopeError, "affinity unavailable"):
                process_priority.ensure_soc_priority_scope(["status", "--json"])

        reverify_identity.assert_not_called()
        execvpe.assert_not_called()

    def test_scope_marker_maps_non_root_mount_for_capture_and_reverification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proc_root = root / "proc"
            pid_cgroup = proc_root / "4242" / "cgroup"
            pid_cgroup.parent.mkdir(parents=True)
            pid_cgroup.write_text("0::/root.slice/work.scope\n", encoding="ascii")
            generic_mount = root / "generic"
            generic_mount.mkdir()
            mountpoint = root / "specific"
            scope = mountpoint / "work.scope"
            scope.mkdir(parents=True)
            (scope / "cpu.weight").write_text("200\n", encoding="ascii")
            (scope / "io.weight").write_text("default 200\n", encoding="ascii")
            (scope / "cpuset.cpus.effective").write_text("0-1\n", encoding="ascii")
            mountinfo = root / "mountinfo"
            mountinfo.write_text(
                f"35 25 {_mountinfo_device(root)} / {generic_mount} rw - cgroup2 cgroup rw\n"
                f"36 25 {_mountinfo_device(root)} /root.slice {mountpoint} rw - cgroup2 cgroup rw\n",
                encoding="ascii",
            )
            online = root / "online"
            online.write_text("0-1\n", encoding="ascii")
            with (
                mock.patch.dict(
                    process_priority.os.environ,
                    {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                    clear=False,
                ),
                mock.patch.object(process_priority.os, "getpid", return_value=4242),
                mock.patch.object(process_priority, "_PROC_ROOT", proc_root),
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", pid_cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority.os,
                    "sched_getaffinity",
                    side_effect=[{0}, {0, 1}],
                ) as get_affinity,
                mock.patch.object(process_priority.os, "sched_setaffinity") as set_affinity,
                mock.patch.object(process_priority.os, "execvpe") as execvpe,
            ):
                process_priority.ensure_soc_priority_scope(["status", "--json"])

        self.assertEqual(get_affinity.call_args_list, [mock.call(0), mock.call(0)])
        set_affinity.assert_called_once_with(0, frozenset({0, 1}))
        execvpe.assert_not_called()

    def test_soc_scope_bootstrap_sets_marker_and_execs_trusted_command(self) -> None:
        process = _fake_scope_process(1)
        captured: dict[str, object] = {}

        def reject_scope(argv: list[str], **kwargs: object) -> mock.Mock:
            captured.update(argv=argv, **kwargs)
            return process

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "spoof"},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=reject_scope,
            ) as popen,
            mock.patch.object(
                process_priority.os,
                "execvpe",
                side_effect=AssertionError("legacy process replacement is forbidden"),
            ) as execvpe,
        ):
            self.assertFalse(
                process_priority.ensure_soc_priority_scope(["status", "--json"])
            )

        popen.assert_called_once()
        execvpe.assert_not_called()
        command = captured["argv"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertEqual(command[0:9], [
            "/usr/bin/systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--expand-environment=no",
            "-p",
            "CPUWeight=200",
            "-p",
            "IOWeight=200",
        ])
        self.assertEqual(command[9], "--")
        self.assertIn("speed_of_cinnamon.cli", command)
        environment = captured["env"]
        self.assertIsInstance(environment, dict)
        assert isinstance(environment, dict)
        self.assertEqual(
            environment[process_priority.SOC_PRIORITY_SCOPE_MARKER],
            "1",
        )
        self.assertIn(process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV, environment)
        self.assertIn(process_priority._SCOPE_EXEC_LATCH_NONCE_ENV, environment)
        self.assertTrue(captured["start_new_session"])
        self.assertTrue(captured["close_fds"])
        self.assertFalse(captured["shell"])
        self.assertNotIn("stdin", captured)
        self.assertNotIn("stdout", captured)
        self.assertNotIn("stderr", captured)
        process.wait.assert_called_once()

    def test_required_latch_missing_never_executes_before_parent_fallback(self) -> None:
        process = _fake_scope_process(1)
        child_target = mock.Mock(side_effect=SystemExit(7))
        parent_target = mock.Mock()

        def strip_latch_environment(
            command: list[str],
            **kwargs: object,
        ) -> mock.Mock:
            environment = dict(kwargs["env"])
            environment.pop(process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV)
            environment.pop(process_priority._SCOPE_EXEC_LATCH_NONCE_ENV)
            wrapper = command[command.index(process_priority._SCOPE_EXEC_WRAPPER_TOKEN) :]
            self.assertEqual(
                wrapper[1],
                process_priority._SCOPE_EXEC_LATCH_REQUIRED_TOKEN,
            )
            with (
                mock.patch.dict(process_priority.os.environ, environment, clear=True),
                mock.patch.object(
                    process_priority,
                    "_normalize_cpu_affinity_for_scope_exec",
                    return_value=True,
                ),
                mock.patch.object(
                    process_priority.os,
                    "execve",
                    side_effect=child_target,
                ),
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    ": arguments",
                ):
                    process_priority._run_scope_exec_wrapper(wrapper)
            return process

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=strip_latch_environment,
            ),
        ):
            if not process_priority.ensure_soc_priority_scope(["status"]):
                parent_target()

        child_target.assert_not_called()
        parent_target.assert_called_once_with()
        process.wait.assert_called_once()

    def test_required_latch_is_sent_before_launch_spec_validation(self) -> None:
        arguments = _scope_exec_test_arguments(latch_required=True)
        arguments[2] = str(int(arguments[2]) + 1)
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {
                    process_priority.SOC_PRIORITY_SCOPE_MARKER: "1",
                    process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "a" * 32,
                    process_priority._SCOPE_EXEC_LATCH_NONCE_ENV: "b" * 64,
                },
                clear=True,
            ),
            mock.patch.object(
                process_priority,
                "_send_scope_exec_entered_latch",
            ) as send_latch,
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ) as normalize,
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                ": arguments",
            ):
                process_priority._run_scope_exec_wrapper(arguments)

        send_latch.assert_called_once_with("a" * 32, "b" * 64)
        normalize.assert_not_called()
        execve.assert_not_called()

    def test_required_latch_missing_or_malformed_never_executes_target(self) -> None:
        arguments = _scope_exec_test_arguments(latch_required=True)
        environments = (
            {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
            {
                process_priority.SOC_PRIORITY_SCOPE_MARKER: "1",
                process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "not-valid",
                process_priority._SCOPE_EXEC_LATCH_NONCE_ENV: "b" * 64,
            },
            {
                process_priority.SOC_PRIORITY_SCOPE_MARKER: "1",
                process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "a" * 32,
            },
        )
        for environment in environments:
            with (
                self.subTest(keys=tuple(sorted(environment))),
                mock.patch.dict(
                    process_priority.os.environ,
                    environment,
                    clear=True,
                ),
                mock.patch.object(
                    process_priority,
                    "_normalize_cpu_affinity_for_scope_exec",
                ) as normalize,
                mock.patch.object(process_priority.os, "execve") as execve,
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    ": arguments",
                ):
                    process_priority._run_scope_exec_wrapper(arguments)

            normalize.assert_not_called()
            execve.assert_not_called()

    def test_required_latch_precedes_marker_check_and_unsupported_fd_exec(self) -> None:
        arguments = _scope_exec_test_arguments(latch_required=True)
        for marker, expected_failure in (("", ": arguments"), ("1", ": exec")):
            with (
                self.subTest(marker=marker),
                mock.patch.dict(
                    process_priority.os.environ,
                    {
                        process_priority.SOC_PRIORITY_SCOPE_MARKER: marker,
                        process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "a" * 32,
                        process_priority._SCOPE_EXEC_LATCH_NONCE_ENV: "b" * 64,
                    },
                    clear=True,
                ),
                mock.patch.object(
                    process_priority,
                    "_send_scope_exec_entered_latch",
                ) as send_latch,
                mock.patch.object(
                    process_priority,
                    "_normalize_cpu_affinity_for_scope_exec",
                    return_value=True,
                ) as normalize,
                mock.patch.object(
                    process_priority,
                    "_SCOPE_EXEC_FD_EXEC_SUPPORTED",
                    False,
                ),
                mock.patch.object(
                    process_priority,
                    "_open_scope_exec_launch_fd",
                ) as open_target,
                mock.patch.object(process_priority.os, "execve") as execve,
            ):
                with self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    expected_failure,
                ):
                    process_priority._run_scope_exec_wrapper(arguments)

            send_latch.assert_called_once_with("a" * 32, "b" * 64)
            if marker:
                normalize.assert_called_once_with()
            else:
                normalize.assert_not_called()
            open_target.assert_not_called()
            execve.assert_not_called()

    def test_scope_channel_unavailable_degrades_before_child_spawn(self) -> None:
        for failure_stage in ("create", "credentials", "bind"):
            listener = mock.Mock()
            if failure_stage == "credentials":
                listener.setsockopt.side_effect = OSError("private socket failure")
            elif failure_stage == "bind":
                listener.bind.side_effect = OSError("private bind failure")
            socket_result: object = listener
            if failure_stage == "create":
                socket_result = OSError("private create failure")

            with (
                self.subTest(failure_stage=failure_stage),
                mock.patch.dict(
                    process_priority.os.environ,
                    {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                    clear=False,
                ),
                mock.patch.object(
                    process_priority.shutil,
                    "which",
                    return_value="/usr/bin/systemd-run",
                ),
                mock.patch.object(
                    process_priority.socket,
                    "socket",
                    side_effect=(
                        socket_result
                        if isinstance(socket_result, BaseException)
                        else None
                    ),
                    return_value=(
                        None
                        if isinstance(socket_result, BaseException)
                        else socket_result
                    ),
                ),
                mock.patch.object(process_priority.subprocess, "Popen") as popen,
            ):
                parent_target = mock.Mock()
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

            parent_target.assert_called_once_with()
            popen.assert_not_called()
            if failure_stage != "create":
                listener.close.assert_called_once_with()

    def test_missing_scope_channel_capability_degrades_before_spawn(self) -> None:
        for attribute in ("AF_UNIX", "SO_PASSCRED"):
            listener = mock.Mock()
            with (
                self.subTest(attribute=attribute),
                mock.patch.dict(
                    process_priority.os.environ,
                    {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                    clear=False,
                ),
                mock.patch.object(
                    process_priority.shutil,
                    "which",
                    return_value="/usr/bin/systemd-run",
                ),
                mock.patch.object(
                    process_priority.socket,
                    "socket",
                    return_value=listener,
                ) as create_socket,
                mock.patch.object(process_priority.subprocess, "Popen") as popen,
                mock.patch.object(process_priority.socket, attribute),
            ):
                delattr(process_priority.socket, attribute)
                parent_target = mock.Mock()
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

            parent_target.assert_called_once_with()
            popen.assert_not_called()
            if attribute == "AF_UNIX":
                create_socket.assert_not_called()
            else:
                create_socket.assert_called_once()
                listener.close.assert_called_once_with()

    def test_scope_signal_forwarder_reserves_primary_before_reentrant_forward(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        process.wait.return_value = 73
        forwarder = process_priority._ScopeExecSignalForwarder()
        with mock.patch.object(
            process_priority,
            "_bind_scope_attempt_process_group",
            return_value=4242,
        ):
            forwarder.bind(process)

        calls: list[tuple[int, int | None]] = []

        def forward_with_nested_signal(
            bound_process: mock.Mock,
            stop_signal: int,
            *,
            process_group_id: int | None = None,
        ) -> bool:
            self.assertIs(bound_process, process)
            calls.append((stop_signal, process_group_id))
            if stop_signal == signal.SIGINT:
                forwarder.handle(signal.SIGHUP, None)
            return True

        handler_error: BaseException | None = None
        try:
            with mock.patch.object(
                process_priority,
                "_signal_scope_attempt_group",
                side_effect=forward_with_nested_signal,
            ):
                forwarder.handle(signal.SIGINT, None)
        except BaseException as error:
            handler_error = error

        self.assertIsNone(handler_error)
        self.assertEqual(forwarder.primary_signal, signal.SIGINT)
        self.assertTrue(forwarder.primary_signal_forwarded)
        self.assertEqual(
            calls,
            [(signal.SIGINT, 4242), (signal.SIGHUP, 4242)],
        )
        with (
            mock.patch.object(process_priority.os, "getpgid") as getpgid,
            mock.patch.object(process_priority.os, "killpg") as kill_group,
        ):
            result = process_priority._terminate_scope_attempt(
                process,
                process_group_id=forwarder.process_group_id,
                forwarded_signal=(
                    forwarder.primary_signal
                    if forwarder.primary_signal_forwarded
                    else None
                ),
            )

        self.assertEqual(result, 73)
        getpgid.assert_not_called()
        kill_group.assert_not_called()

    def test_scope_signal_during_bind_stays_pending_until_group_is_published(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        process.wait.return_value = 73

        class SignalDuringBind(process_priority._ScopeExecSignalForwarder):
            def __init__(self) -> None:
                super().__init__()
                self.inject_signal = True

            def __setattr__(self, name: str, value: object) -> None:
                if (
                    name == "_bound_process_group"
                    and isinstance(value, tuple)
                    and value
                    and value[0] is process
                    and getattr(self, "inject_signal", False)
                ):
                    self.inject_signal = False
                    self.handle(signal.SIGINT, None)
                super().__setattr__(name, value)
                if (
                    name == "process"
                    and value is process
                    and getattr(self, "inject_signal", False)
                ):
                    self.inject_signal = False
                    self.handle(signal.SIGINT, None)

        forwarder = SignalDuringBind()
        handler_error = None
        try:
            with (
                mock.patch.object(
                    process_priority,
                    "_bind_scope_attempt_process_group",
                    return_value=4242,
                ),
                mock.patch.object(
                    process_priority,
                    "_signal_scope_attempt_group",
                    return_value=True,
                ) as forward_signal,
            ):
                forwarder.bind(process)
        except BaseException as error:
            handler_error = error

        self.assertIsNone(handler_error)
        self.assertIs(forwarder.process, process)
        self.assertEqual(forwarder.process_group_id, 4242)
        self.assertEqual(forwarder.primary_signal, signal.SIGINT)
        self.assertTrue(forwarder.primary_signal_forwarded)
        forward_signal.assert_called_once_with(
            process,
            signal.SIGINT,
            process_group_id=4242,
        )
        with (
            mock.patch.object(process_priority.os, "getpgid") as getpgid,
            mock.patch.object(process_priority.os, "killpg") as kill_group,
        ):
            result = process_priority._terminate_scope_attempt(
                process,
                process_group_id=forwarder.process_group_id,
                forwarded_signal=(
                    forwarder.primary_signal
                    if forwarder.primary_signal_forwarded
                    else None
                ),
            )

        self.assertEqual(result, 73)
        getpgid.assert_not_called()
        kill_group.assert_not_called()

    def test_scope_supervisor_observes_recorded_signal_without_handler_unwind(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        listener = mock.Mock()
        listener.recvmsg.side_effect = BlockingIOError
        selector = mock.Mock()
        forwarder = process_priority._ScopeExecSignalForwarder()
        with mock.patch.object(
            process_priority,
            "_bind_scope_attempt_process_group",
            return_value=4242,
        ):
            forwarder.bind(process)

        def record_signal(_timeout: float) -> list[object]:
            forwarder.handle(signal.SIGINT, None)
            return []

        selector.select.side_effect = record_signal
        handler_error = None
        outcome = None
        try:
            with (
                mock.patch.object(
                    process_priority.selectors,
                    "DefaultSelector",
                    return_value=selector,
                ),
                mock.patch.object(
                    process_priority,
                    "_signal_scope_attempt_group",
                    return_value=True,
                ) as forward_signal,
            ):
                outcome = process_priority._supervise_scope_attempt(
                    process,
                    listener,
                    "b" * 64,
                    forwarder,
                )
        except BaseException as error:
            handler_error = error

        self.assertIsNone(handler_error)
        self.assertEqual(
            outcome,
            process_priority._ScopeExecAttemptOutcome(None, "none", False),
        )
        self.assertEqual(forwarder.primary_signal, signal.SIGINT)
        self.assertTrue(forwarder.primary_signal_forwarded)
        forward_signal.assert_called_once_with(
            process,
            signal.SIGINT,
            process_group_id=4242,
        )
        process.wait.assert_not_called()
        selector.close.assert_called_once_with()

    def test_scope_poll_and_wait_failures_use_one_outer_cleanup(
        self,
    ) -> None:
        for failure_point in ("poll", "wait"):
            process = _fake_scope_process(None)
            if failure_point == "poll":
                process.poll.side_effect = (
                    None,
                    None,
                    OSError("private poll failure"),
                )
            else:
                process.poll.side_effect = (None, None, 7)
                process.wait.side_effect = OSError("private wait failure")
            listener = mock.Mock()
            listener.recvmsg.side_effect = BlockingIOError
            selector = mock.Mock()
            parent_target = mock.Mock()

            class SignalAfterErrorTransition(
                process_priority._ScopeExecSignalForwarder
            ):
                def begin_error_cleanup(self) -> int | None:
                    primary_signal = super().begin_error_cleanup()
                    self.handle(signal.SIGHUP, None)
                    return primary_signal

            forwarder = SignalAfterErrorTransition()

            with (
                self.subTest(failure_point=failure_point),
                mock.patch.dict(
                    process_priority.os.environ,
                    {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                    clear=False,
                ),
                mock.patch.object(
                    process_priority.shutil,
                    "which",
                    return_value="/usr/bin/systemd-run",
                ),
                mock.patch.object(
                    process_priority,
                    "_create_scope_exec_latch",
                    return_value=(listener, "a" * 32, "b" * 64),
                ),
                mock.patch.object(
                    process_priority.subprocess,
                    "Popen",
                    return_value=process,
                ),
                mock.patch.object(
                    process_priority,
                    "_ScopeExecSignalForwarder",
                    return_value=forwarder,
                ),
                mock.patch.object(
                    process_priority.selectors,
                    "DefaultSelector",
                    return_value=selector,
                ),
                mock.patch.object(process_priority.os, "getpgid", return_value=4242),
                mock.patch.object(
                    process_priority,
                    "_signal_scope_attempt_group",
                    return_value=True,
                ) as forward_signal,
                mock.patch.object(
                    process_priority,
                    "_terminate_scope_attempt",
                ) as terminate,
                self.assertRaisesRegex(
                    process_priority.PriorityScopeError,
                    "child status is unavailable",
                ),
            ):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

            parent_target.assert_not_called()
            self.assertIsNone(forwarder.primary_signal)
            forward_signal.assert_called_once_with(
                process,
                signal.SIGHUP,
                process_group_id=4242,
            )
            terminate.assert_called_once_with(
                process,
                process_group_id=4242,
                forwarded_signal=None,
            )
            selector.close.assert_called_once_with()
            listener.close.assert_called_once_with()

    def test_scope_invalid_latch_hanging_child_uses_immediate_outer_cleanup(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        process.wait.side_effect = (
            subprocess.TimeoutExpired("systemd-run", 2.0),
            -signal.SIGKILL,
        )
        listener = mock.Mock()
        received_datagrams = 0

        def continuous_invalid_datagrams(
            _message_bytes: int,
            _credentials_bytes: int,
        ) -> tuple[bytes, list[object], int, None]:
            nonlocal received_datagrams
            received_datagrams += 1
            if received_datagrams > 4:
                raise AssertionError("invalid latch input was drained")
            return b"malformed", [], 0, None

        listener.recvmsg.side_effect = continuous_invalid_datagrams
        selector = mock.Mock()
        selector.select.side_effect = AssertionError("invalid latch state was polled")
        parent_target = mock.Mock()

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                process_priority.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(process_priority.os, "killpg") as kill_group,
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
                wraps=process_priority._terminate_scope_attempt,
            ) as terminate,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "invalid or ambiguous",
            ):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        self.assertEqual(received_datagrams, 1)
        selector.select.assert_not_called()
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=None,
        )
        self.assertEqual(
            kill_group.call_args_list,
            [
                mock.call(4242, signal.SIGTERM),
                mock.call(4242, signal.SIGKILL),
            ],
        )
        self.assertEqual(process.wait.call_count, 2)
        listener.close.assert_called_once_with()

    def test_scope_continuous_latch_input_cannot_starve_recorded_signal(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        process.wait.return_value = 73
        listener = mock.Mock()
        received_datagrams = 0

        def continuous_invalid_datagrams(
            _message_bytes: int,
            _credentials_bytes: int,
        ) -> tuple[bytes, list[object], int, None]:
            nonlocal received_datagrams
            received_datagrams += 1
            if received_datagrams > 4:
                raise AssertionError("continuous latch input starved signal check")
            if received_datagrams == 1:
                active_handler = active_handlers[signal.SIGINT]
                if not callable(active_handler):
                    raise AssertionError("signal handler is unavailable")
                active_handler(signal.SIGINT, None)
            return b"malformed", [], 0, None

        listener.recvmsg.side_effect = continuous_invalid_datagrams
        selector = mock.Mock()
        prior_handlers = {
            stop_signal: object()
            for stop_signal in process_priority._SCOPE_EXEC_FORWARDED_SIGNALS
        }
        active_handlers = dict(prior_handlers)

        def install_handler(stop_signal: int, handler: object) -> object:
            previous = active_handlers[stop_signal]
            active_handlers[stop_signal] = handler
            return previous

        selector.select.side_effect = AssertionError("invalid latch state was polled")
        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ),
            mock.patch.object(
                process_priority.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(process_priority.os, "killpg") as kill_group,
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
                wraps=process_priority._terminate_scope_attempt,
            ) as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        self.assertEqual(received_datagrams, 1)
        selector.select.assert_not_called()
        kill_group.assert_called_once_with(4242, signal.SIGINT)
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=signal.SIGINT,
        )
        process.wait.assert_called_once_with(
            timeout=process_priority._SCOPE_EXEC_REAP_TIMEOUT_SECONDS
        )
        selector.close.assert_called_once_with()
        self.assertEqual(active_handlers, prior_handlers)
        listener.close.assert_called_once_with()

    def test_scope_signal_during_popen_is_forwarded_after_child_binding(self) -> None:
        process = _fake_scope_process(None)
        listener = mock.Mock()
        forwarded_signals = (
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGHUP,
            signal.SIGQUIT,
        )
        prior_handlers = {
            signal_number: object()
            for signal_number in forwarded_signals
        }
        installed_handlers: dict[int, object] = {}
        active_handlers = dict(prior_handlers)

        def install_handler(signal_number: int, handler: object) -> object:
            previous = active_handlers[signal_number]
            if signal_number not in installed_handlers:
                installed_handlers[signal_number] = handler
            active_handlers[signal_number] = handler
            return previous

        def signal_before_return(
            _command: list[str],
            **_kwargs: object,
        ) -> mock.Mock:
            handler = installed_handlers[signal.SIGINT]
            assert callable(handler)
            handler(signal.SIGINT, None)
            return process

        def terminate_during_forwarding(
            bound_process: mock.Mock,
            *,
            process_group_id: int | None,
            forwarded_signal: int | None,
        ) -> int:
            self.assertIs(bound_process, process)
            self.assertEqual(process_group_id, 4242)
            self.assertEqual(forwarded_signal, signal.SIGINT)
            followup_handler = active_handlers[signal.SIGHUP]
            self.assertTrue(callable(followup_handler))
            followup_handler(signal.SIGHUP, None)
            return 73

        def observe_pending_signal(
            bound_process: mock.Mock,
            bound_listener: mock.Mock,
            nonce: str,
            forwarder: process_priority._ScopeExecSignalForwarder,
        ) -> process_priority._ScopeExecAttemptOutcome:
            self.assertIs(bound_process, process)
            self.assertIs(bound_listener, listener)
            self.assertEqual(nonce, "b" * 64)
            self.assertEqual(forwarder.primary_signal, signal.SIGINT)
            return process_priority._ScopeExecAttemptOutcome(None, "none", False)

        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=signal_before_return,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ) as set_handler,
            mock.patch.object(
                process_priority.os,
                "getpgid",
                return_value=4242,
            ),
            mock.patch.object(process_priority.os, "killpg") as killpg,
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
                side_effect=terminate_during_forwarding,
            ) as terminate,
            mock.patch.object(
                process_priority,
                "_supervise_scope_attempt",
                side_effect=observe_pending_signal,
            ) as supervise,
        ):
            with self.assertRaises(KeyboardInterrupt):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(4242, signal.SIGINT),
                mock.call(4242, signal.SIGHUP),
            ],
        )
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=signal.SIGINT,
        )
        supervise.assert_called_once()
        self.assertEqual(
            set_handler.call_args_list[-4:],
            [
                mock.call(signal.SIGQUIT, prior_handlers[signal.SIGQUIT]),
                mock.call(signal.SIGHUP, prior_handlers[signal.SIGHUP]),
                mock.call(signal.SIGTERM, prior_handlers[signal.SIGTERM]),
                mock.call(signal.SIGINT, prior_handlers[signal.SIGINT]),
            ],
        )
        self.assertEqual(active_handlers, prior_handlers)
        listener.close.assert_called_once_with()

    def test_scope_signal_during_error_cleanup_never_replaces_original_error(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        process.wait.side_effect = (
            subprocess.TimeoutExpired("systemd-run", 2.0),
            -signal.SIGKILL,
        )
        listener = mock.Mock()
        listener.recvmsg.side_effect = BlockingIOError
        selector = mock.Mock()
        original_error = RuntimeError("original supervisor failure")
        selector.select.side_effect = original_error
        prior_handlers = {
            signal_number: object()
            for signal_number in process_priority._SCOPE_EXEC_FORWARDED_SIGNALS
        }
        active_handlers = dict(prior_handlers)
        forwarded: list[tuple[int, int | None]] = []

        class SignalAfterErrorTransition(
            process_priority._ScopeExecSignalForwarder
        ):
            def __init__(self) -> None:
                super().__init__()
                self.inject_cleanup_signal = True

            def begin_error_cleanup(self) -> int | None:
                primary_signal = super().begin_error_cleanup()
                if self.inject_cleanup_signal:
                    self.inject_cleanup_signal = False
                    active_handler = active_handlers[signal.SIGHUP]
                    if not callable(active_handler):
                        raise AssertionError("cleanup signal handler is unavailable")
                    active_handler(signal.SIGHUP, None)
                return primary_signal

        forwarder = SignalAfterErrorTransition()

        def install_handler(signal_number: int, handler: object) -> object:
            previous = active_handlers[signal_number]
            active_handlers[signal_number] = handler
            return previous

        def signal_during_cleanup(
            bound_process: mock.Mock,
            stop_signal: int,
            *,
            process_group_id: int | None = None,
        ) -> bool:
            self.assertIs(bound_process, process)
            self.assertEqual(process_group_id, 4242)
            forwarded.append((stop_signal, process_group_id))
            return True

        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ),
            mock.patch.object(
                process_priority,
                "_ScopeExecSignalForwarder",
                return_value=forwarder,
            ),
            mock.patch.object(
                process_priority.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(
                process_priority,
                "_signal_scope_attempt_group",
                side_effect=signal_during_cleanup,
            ),
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
                wraps=process_priority._terminate_scope_attempt,
            ) as terminate,
        ):
            with self.assertRaises(RuntimeError) as raised:
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        self.assertIs(raised.exception, original_error)
        parent_target.assert_not_called()
        self.assertEqual(
            forwarded,
            [
                (signal.SIGHUP, 4242),
                (signal.SIGTERM, 4242),
                (signal.SIGKILL, 4242),
            ],
        )
        self.assertIsNone(forwarder.primary_signal)
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=None,
        )
        self.assertEqual(process.wait.call_count, 2)
        selector.close.assert_called_once_with()
        self.assertEqual(active_handlers, prior_handlers)
        listener.close.assert_called_once_with()

    def test_scope_recorded_signal_wins_over_later_selector_failure(self) -> None:
        process = _fake_scope_process(None)
        process.wait.return_value = 73
        listener = mock.Mock()
        listener.recvmsg.side_effect = BlockingIOError
        selector = mock.Mock()
        original_error = RuntimeError("original selector failure")
        prior_handlers = {
            signal_number: object()
            for signal_number in process_priority._SCOPE_EXEC_FORWARDED_SIGNALS
        }
        active_handlers = dict(prior_handlers)

        def install_handler(signal_number: int, handler: object) -> object:
            previous = active_handlers[signal_number]
            active_handlers[signal_number] = handler
            return previous

        def signal_then_fail(_timeout: float) -> list[object]:
            active_handler = active_handlers[signal.SIGINT]
            if not callable(active_handler):
                raise AssertionError("signal handler is unavailable")
            active_handler(signal.SIGINT, None)
            raise original_error

        selector.select.side_effect = signal_then_fail
        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ),
            mock.patch.object(
                process_priority.selectors,
                "DefaultSelector",
                return_value=selector,
            ),
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(process_priority.os, "killpg") as kill_group,
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
                wraps=process_priority._terminate_scope_attempt,
            ) as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        kill_group.assert_called_once_with(4242, signal.SIGINT)
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=signal.SIGINT,
        )
        process.wait.assert_called_once_with(
            timeout=process_priority._SCOPE_EXEC_REAP_TIMEOUT_SECONDS
        )
        selector.close.assert_called_once_with()
        self.assertEqual(active_handlers, prior_handlers)
        listener.close.assert_called_once_with()

    def test_scope_popen_failure_with_pending_signal_never_falls_back(self) -> None:
        listener = mock.Mock()
        forwarded_signals = (
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGHUP,
            signal.SIGQUIT,
        )
        prior_handlers = {
            signal_number: object()
            for signal_number in forwarded_signals
        }
        installed_handlers: dict[int, object] = {}

        def install_handler(signal_number: int, handler: object) -> object:
            if signal_number not in installed_handlers:
                installed_handlers[signal_number] = handler
            return prior_handlers[signal_number]

        def signal_then_fail(
            _command: list[str],
            **_kwargs: object,
        ) -> mock.Mock:
            handler = installed_handlers[signal.SIGINT]
            assert callable(handler)
            handler(signal.SIGINT, None)
            raise OSError("private spawn failure")

        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=signal_then_fail,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ) as set_handler,
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
            ) as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        terminate.assert_not_called()
        self.assertEqual(
            set_handler.call_args_list[-4:],
            [
                mock.call(signal.SIGQUIT, prior_handlers[signal.SIGQUIT]),
                mock.call(signal.SIGHUP, prior_handlers[signal.SIGHUP]),
                mock.call(signal.SIGTERM, prior_handlers[signal.SIGTERM]),
                mock.call(signal.SIGINT, prior_handlers[signal.SIGINT]),
            ],
        )
        listener.close.assert_called_once_with()

    def test_scope_popen_fallback_signal_during_handler_restore_never_runs_target(
        self,
    ) -> None:
        listener = mock.Mock()
        prior_handlers = {
            stop_signal: object()
            for stop_signal in process_priority._SCOPE_EXEC_FORWARDED_SIGNALS
        }
        active_handlers = dict(prior_handlers)
        handler_calls = 0

        def install_or_restore_handler(
            stop_signal: int,
            handler: object,
        ) -> object:
            nonlocal handler_calls
            handler_calls += 1
            previous = active_handlers[stop_signal]
            if handler_calls == len(prior_handlers) + 1:
                late_handler = active_handlers[signal.SIGINT]
                if not callable(late_handler):
                    raise AssertionError("late signal handler is unavailable")
                late_handler(signal.SIGINT, None)
            active_handlers[stop_signal] = handler
            return previous

        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=OSError("private spawn failure"),
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_or_restore_handler,
            ),
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
            ) as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        terminate.assert_not_called()
        self.assertEqual(active_handlers, prior_handlers)
        listener.close.assert_called_once_with()

    def test_scope_pending_signal_with_bind_failure_reaps_before_propagation(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        process.wait.return_value = 73
        listener = mock.Mock()
        prior_handlers = {
            signal_number: object()
            for signal_number in process_priority._SCOPE_EXEC_FORWARDED_SIGNALS
        }
        installed_handlers: dict[int, object] = {}

        def install_handler(signal_number: int, handler: object) -> object:
            if signal_number not in installed_handlers:
                installed_handlers[signal_number] = handler
            return prior_handlers[signal_number]

        def record_pending_before_return(
            _command: list[str],
            **_kwargs: object,
        ) -> mock.Mock:
            active_handler = installed_handlers[signal.SIGINT]
            if not callable(active_handler):
                raise AssertionError("pending signal handler is unavailable")
            active_handler(signal.SIGINT, None)
            return process

        parent_target = mock.Mock()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=record_pending_before_return,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ),
            mock.patch.object(
                process_priority,
                "_bind_scope_attempt_process_group",
                side_effect=process_priority.PriorityScopeError(
                    "SOC priority scope child identity is unavailable"
                ),
            ),
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(process_priority.os, "killpg") as kill_group,
            mock.patch.object(
                process_priority,
                "_supervise_scope_attempt",
            ) as supervise,
        ):
            with self.assertRaises(KeyboardInterrupt):
                if not process_priority.ensure_soc_priority_scope(["status"]):
                    parent_target()

        parent_target.assert_not_called()
        supervise.assert_not_called()
        kill_group.assert_called_once_with(4242, signal.SIGTERM)
        process.wait.assert_called_once_with(
            timeout=process_priority._SCOPE_EXEC_REAP_TIMEOUT_SECONDS
        )
        listener.close.assert_called_once_with()

    def test_scope_recorded_signal_is_reaped_then_synchronously_propagated(
        self,
    ) -> None:
        process = _fake_scope_process(None)
        listener = mock.Mock()
        forwarded_signals = (
            signal.SIGINT,
            signal.SIGTERM,
            signal.SIGHUP,
            signal.SIGQUIT,
        )
        prior_handlers = {
            signal_number: object()
            for signal_number in forwarded_signals
        }
        installed_handlers: dict[int, object] = {}
        handler_returned = False

        def install_handler(signal_number: int, handler: object) -> object:
            if signal_number not in installed_handlers:
                installed_handlers[signal_number] = handler
            return prior_handlers[signal_number]

        def record_signal(
            _process: mock.Mock,
            _listener: mock.Mock,
            _nonce: str,
            forwarder: process_priority._ScopeExecSignalForwarder,
        ) -> process_priority._ScopeExecAttemptOutcome:
            nonlocal handler_returned
            handler = installed_handlers[signal.SIGINT]
            assert callable(handler)
            handler(signal.SIGINT, None)
            handler_returned = True
            self.assertEqual(forwarder.primary_signal, signal.SIGINT)
            return process_priority._ScopeExecAttemptOutcome(None, "none", False)

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "_create_scope_exec_latch",
                return_value=(listener, "a" * 32, "b" * 64),
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                return_value=process,
            ),
            mock.patch.object(
                process_priority.signal,
                "getsignal",
                side_effect=lambda number: prior_handlers[number],
            ),
            mock.patch.object(
                process_priority.signal,
                "signal",
                side_effect=install_handler,
            ) as set_handler,
            mock.patch.object(
                process_priority,
                "_supervise_scope_attempt",
                side_effect=record_signal,
            ),
            mock.patch.object(
                process_priority.os,
                "getpgid",
                return_value=4242,
            ) as getpgid,
            mock.patch.object(process_priority.os, "killpg") as killpg,
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
            ) as terminate,
        ):
            with self.assertRaises(KeyboardInterrupt):
                process_priority.ensure_soc_priority_scope(["status"])

        self.assertTrue(handler_returned)
        self.assertEqual(getpgid.call_args_list, [mock.call(4242), mock.call(4242)])
        killpg.assert_called_once_with(4242, signal.SIGINT)
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=signal.SIGINT,
        )
        self.assertEqual(
            set_handler.call_args_list[-4:],
            [
                mock.call(signal.SIGQUIT, prior_handlers[signal.SIGQUIT]),
                mock.call(signal.SIGHUP, prior_handlers[signal.SIGHUP]),
                mock.call(signal.SIGTERM, prior_handlers[signal.SIGTERM]),
                mock.call(signal.SIGINT, prior_handlers[signal.SIGINT]),
            ],
        )
        listener.close.assert_called_once_with()

    def test_soc_scope_supervisor_falls_back_only_before_entered(self) -> None:
        process = _fake_scope_process(1)
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=(OSError("spawn failed"), process),
            ) as popen,
            mock.patch.object(
                process_priority.os,
                "execvpe",
                side_effect=AssertionError("legacy process replacement is forbidden"),
            ) as execvpe,
        ):
            self.assertFalse(process_priority.ensure_soc_priority_scope(["status"]))
            self.assertFalse(process_priority.ensure_soc_priority_scope(["status"]))

        self.assertEqual(popen.call_count, 2)
        process.wait.assert_called_once()
        execvpe.assert_not_called()

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(process_priority.shutil, "which", return_value=None),
            mock.patch.object(process_priority.subprocess, "Popen") as popen,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            self.assertFalse(process_priority.ensure_soc_priority_scope(["status"]))

        popen.assert_not_called()
        execvpe.assert_not_called()

    def test_soc_scope_supervisor_never_returns_after_entered(self) -> None:
        for returncode in (0, 1, 7):
            process = _fake_scope_process(returncode)

            def entered_scope(
                _argv: list[str],
                **kwargs: object,
            ) -> mock.Mock:
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                _send_scope_test_datagrams(environment, (None,))
                return process

            with (
                self.subTest(returncode=returncode),
                mock.patch.dict(
                    process_priority.os.environ,
                    {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                    clear=False,
                ),
                mock.patch.object(
                    process_priority.shutil,
                    "which",
                    return_value="/usr/bin/systemd-run",
                ),
                mock.patch.object(
                    process_priority.subprocess,
                    "Popen",
                    side_effect=entered_scope,
                ),
                mock.patch.object(process_priority.os, "execvpe") as execvpe,
            ):
                with self.assertRaises(SystemExit) as raised:
                    process_priority.ensure_soc_priority_scope(["status"])

            self.assertEqual(raised.exception.code, returncode)
            process.wait.assert_called_once()
            execvpe.assert_not_called()

    def test_soc_scope_supervisor_never_retries_ambiguous_status(self) -> None:
        cases = (
            (0, ()),
            (1, (b"malformed private payload",)),
            (1, (None, None)),
        )
        for returncode, payloads in cases:
            process = _fake_scope_process(returncode)

            def ambiguous_scope(
                _argv: list[str],
                **kwargs: object,
            ) -> mock.Mock:
                environment = kwargs["env"]
                assert isinstance(environment, dict)
                _send_scope_test_datagrams(environment, payloads)
                return process

            with (
                self.subTest(returncode=returncode, messages=len(payloads)),
                mock.patch.dict(
                    process_priority.os.environ,
                    {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                    clear=False,
                ),
                mock.patch.object(
                    process_priority.shutil,
                    "which",
                    return_value="/usr/bin/systemd-run",
                ),
                mock.patch.object(
                    process_priority.subprocess,
                    "Popen",
                    side_effect=ambiguous_scope,
                ),
                mock.patch.object(process_priority.os, "execvpe") as execvpe,
            ):
                with self.assertRaises(SystemExit) as raised:
                    process_priority.ensure_soc_priority_scope(["status"])

            self.assertEqual(raised.exception.code, returncode)
            process.wait.assert_called_once()
            execvpe.assert_not_called()

    def test_soc_scope_supervisor_timeout_is_reaped_without_retry(self) -> None:
        process = _fake_scope_process(None)

        class SignalAfterTimeoutTransition(
            process_priority._ScopeExecSignalForwarder
        ):
            def begin_error_cleanup(self) -> int | None:
                primary_signal = super().begin_error_cleanup()
                self.handle(signal.SIGHUP, None)
                return primary_signal

        forwarder = SignalAfterTimeoutTransition()
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(process_priority.subprocess, "Popen", return_value=process),
            mock.patch.object(
                process_priority,
                "_ScopeExecSignalForwarder",
                return_value=forwarder,
            ),
            mock.patch.object(
                process_priority,
                "_SCOPE_EXEC_ENTER_TIMEOUT_SECONDS",
                0.0,
            ),
            mock.patch.object(
                process_priority,
                "_terminate_scope_attempt",
                return_value=1,
            ) as terminate,
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(
                process_priority,
                "_signal_scope_attempt_group",
                return_value=True,
            ) as forward_signal,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaisesRegex(process_priority.PriorityScopeError, "timed out"):
                process_priority.ensure_soc_priority_scope(["status"])

        self.assertIsNone(forwarder.primary_signal)
        forward_signal.assert_called_once_with(
            process,
            signal.SIGHUP,
            process_group_id=4242,
        )
        terminate.assert_called_once_with(
            process,
            process_group_id=4242,
            forwarded_signal=None,
        )
        execvpe.assert_not_called()

    def test_soc_scope_supervisor_propagates_signal_after_reaping(self) -> None:
        process = _fake_scope_process(-signal.SIGTERM)
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(process_priority.subprocess, "Popen", return_value=process),
            mock.patch.object(process_priority.os, "getpid", return_value=4343),
            mock.patch.object(process_priority.signal, "signal") as set_signal,
            mock.patch.object(process_priority.os, "kill") as kill,
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaises(SystemExit) as raised:
                process_priority.ensure_soc_priority_scope(["status"])

        self.assertEqual(raised.exception.code, 128 + signal.SIGTERM)
        process.wait.assert_called_once()
        self.assertEqual(
            set_signal.call_args_list[-1],
            mock.call(signal.SIGTERM, signal.SIG_DFL),
        )
        kill.assert_called_once_with(4343, signal.SIGTERM)
        execvpe.assert_not_called()

    def test_soc_scope_supervisor_rejects_launch_spec_drift_before_fallback(self) -> None:
        process = _fake_scope_process(1)
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(process_priority.subprocess, "Popen", return_value=process),
            mock.patch.object(
                process_priority,
                "_scope_exec_supervisor_spec_is_unchanged",
                return_value=False,
            ),
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "launch specification changed",
            ):
                process_priority.ensure_soc_priority_scope(["status"])

        process.wait.assert_called_once()
        execvpe.assert_not_called()

    def test_scope_exec_wrapper_sends_entered_once_and_scrubs_status_environment(self) -> None:
        captured_environment: dict[str, str] = {}

        def fail_exec(
            _path: str,
            _argv: list[str],
            environment: dict[str, str],
        ) -> None:
            captured_environment.update(environment)
            raise OSError("expected test stop")

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {
                    process_priority.SOC_PRIORITY_SCOPE_MARKER: "1",
                    process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "a" * 32,
                    process_priority._SCOPE_EXEC_LATCH_NONCE_ENV: "b" * 64,
                    "SOC_TEST_ENV": "kept",
                },
                clear=True,
            ),
            mock.patch.object(
                process_priority,
                "_send_scope_exec_entered_latch",
            ) as send_latch,
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ),
            mock.patch.object(process_priority.os, "execve", side_effect=fail_exec),
        ):
            with self.assertRaisesRegex(process_priority.PriorityScopeError, ": exec"):
                process_priority._run_scope_exec_wrapper(_scope_exec_test_arguments())

        send_latch.assert_called_once_with("a" * 32, "b" * 64)
        self.assertNotIn(
            process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV,
            captured_environment,
        )
        self.assertNotIn(
            process_priority._SCOPE_EXEC_LATCH_NONCE_ENV,
            captured_environment,
        )
        self.assertEqual(captured_environment["SOC_TEST_ENV"], "kept")

    def test_scope_exec_wrapper_latch_send_failure_never_reaches_target(self) -> None:
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {
                    process_priority.SOC_PRIORITY_SCOPE_MARKER: "1",
                    process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV: "a" * 32,
                    process_priority._SCOPE_EXEC_LATCH_NONCE_ENV: "b" * 64,
                },
                clear=True,
            ),
            mock.patch.object(
                process_priority,
                "_send_scope_exec_entered_latch",
                side_effect=process_priority.PriorityScopeError(
                    "SOC priority scope wrapper failed"
                ),
            ) as send_latch,
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ) as normalize,
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "SOC priority scope wrapper failed",
            ):
                process_priority._run_scope_exec_wrapper(
                    _scope_exec_test_arguments()
                )

        send_latch.assert_called_once_with("a" * 32, "b" * 64)
        normalize.assert_not_called()
        execve.assert_not_called()

    def test_scope_exec_wrapper_rejects_target_identity_drift(self) -> None:
        arguments = _scope_exec_test_arguments()
        arguments[1] = str(int(arguments[1]) + 1)
        with (
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ) as normalize,
            mock.patch.object(process_priority.os, "execve") as execve,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                ": arguments",
            ):
                process_priority._run_scope_exec_wrapper(arguments)

        normalize.assert_not_called()
        execve.assert_not_called()

    def test_scope_exec_latch_rejects_foreign_credentials(self) -> None:
        nonce = "b" * 64
        listener = mock.Mock()
        listener.recvmsg.side_effect = (
            (
                process_priority._SCOPE_EXEC_LATCH_MESSAGE_PREFIX
                + nonce.encode("ascii"),
                [
                    (
                        socket.SOL_SOCKET,
                        socket.SCM_CREDENTIALS,
                        process_priority.struct.pack(
                            "3i",
                            4242,
                            os.getuid() + 1,
                            os.getgid(),
                        ),
                    )
                ],
                0,
                None,
            ),
            BlockingIOError(),
        )

        self.assertEqual(
            process_priority._receive_scope_exec_latches(listener, nonce, "none"),
            "invalid",
        )

    def test_soc_scope_supervisor_rejects_unclear_wait_status(self) -> None:
        process = _fake_scope_process(1)
        process.wait.return_value = None
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(process_priority.subprocess, "Popen", return_value=process),
            mock.patch.object(process_priority.os, "execvpe") as execvpe,
        ):
            with self.assertRaisesRegex(
                process_priority.PriorityScopeError,
                "child status is unavailable",
            ):
                process_priority.ensure_soc_priority_scope(["status"])

        process.wait.assert_called_once()
        execvpe.assert_not_called()

    def test_scope_attempt_termination_escalates_and_reaps(self) -> None:
        process = _fake_scope_process(None)
        wait_results: list[int | BaseException] = [
            process_priority.subprocess.TimeoutExpired("systemd-run", 2.0),
            process_priority.subprocess.TimeoutExpired("systemd-run", 2.0),
            -signal.SIGKILL,
        ]
        events: list[str] = []

        def wait_for_child(*, timeout: float) -> int:
            events.append("wait")
            item = wait_results.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item

        process.wait.side_effect = wait_for_child

        def signal_group(_process_group_id: int, stop_signal: int) -> None:
            events.append(f"signal:{stop_signal}")

        with (
            mock.patch.object(process_priority.os, "getpgid", return_value=4242),
            mock.patch.object(
                process_priority.os,
                "killpg",
                side_effect=signal_group,
            ) as kill_group,
        ):
            result = process_priority._terminate_scope_attempt(
                process,
                process_group_id=4242,
                forwarded_signal=signal.SIGINT,
            )

        self.assertEqual(result, -signal.SIGKILL)
        self.assertEqual(
            events,
            [
                "wait",
                f"signal:{signal.SIGTERM}",
                "wait",
                f"signal:{signal.SIGKILL}",
                "wait",
            ],
        )
        self.assertEqual(
            kill_group.call_args_list,
            [mock.call(4242, signal.SIGTERM), mock.call(4242, signal.SIGKILL)],
        )
        self.assertEqual(process.wait.call_count, 3)

    def test_scope_attempt_forwarded_signal_grace_reaps_without_escalation(self) -> None:
        process = _fake_scope_process(None)
        process.wait.return_value = 73
        with (
            mock.patch.object(process_priority.os, "getpgid") as getpgid,
            mock.patch.object(process_priority.os, "killpg") as kill_group,
        ):
            result = process_priority._terminate_scope_attempt(
                process,
                process_group_id=4242,
                forwarded_signal=signal.SIGQUIT,
            )

        self.assertEqual(result, 73)
        process.wait.assert_called_once_with(
            timeout=process_priority._SCOPE_EXEC_REAP_TIMEOUT_SECONDS
        )
        getpgid.assert_not_called()
        kill_group.assert_not_called()

    def test_scope_signal_forwarder_real_child_grace_and_followup(self) -> None:
        child_code = (
            "import signal, sys, time\n"
            "exit_code = int(sys.argv[1])\n"
            "def stop(signum, _frame):\n"
            "    print(f'signal:{signum}', flush=True)\n"
            "    time.sleep(0.3)\n"
            "    print(f'clean:{signum}', flush=True)\n"
            "    raise SystemExit(exit_code)\n"
            "for item in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGQUIT):\n"
            "    signal.signal(item, stop)\n"
            "print('ready', flush=True)\n"
            "time.sleep(10)\n"
        )
        probe = textwrap.dedent(
            f"""
            import json
            import os
            import signal
            import subprocess
            import sys
            import threading
            import time
            from speed_of_cinnamon import process_priority

            CHILD_CODE = {child_code!r}

            def run_case(first_signal, followup_signal, exit_code):
                child = subprocess.Popen(
                    [sys.executable, "-c", CHILD_CODE, str(exit_code)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=True,
                )
                sender = None
                try:
                    if child.stdout is None or child.stdout.readline().strip() != "ready":
                        raise RuntimeError("child readiness failed")

                    def send_signals():
                        time.sleep(0.05)
                        os.kill(os.getpid(), first_signal)
                        if followup_signal is not None:
                            time.sleep(0.05)
                            os.kill(os.getpid(), followup_signal)

                    with process_priority._forward_scope_attempt_signals() as forwarder:
                        forwarder.bind(child)
                        sender = threading.Thread(target=send_signals)
                        sender.start()
                        signal_deadline = time.monotonic() + 2.0
                        while (
                            forwarder.primary_signal is None
                            and time.monotonic() < signal_deadline
                        ):
                            time.sleep(0.01)
                        primary_signal = forwarder.primary_signal
                        if primary_signal is None:
                            raise RuntimeError("parent signal was not recorded")
                        forwarded_signal = (
                            primary_signal
                            if forwarder.primary_signal_forwarded
                            else None
                        )
                        returncode = process_priority._terminate_scope_attempt(
                            child,
                            process_group_id=forwarder.process_group_id,
                            forwarded_signal=forwarded_signal,
                        )
                    if sender is not None:
                        sender.join(timeout=1.0)
                    try:
                        process_priority._raise_scope_attempt_signal(primary_signal)
                    except BaseException as error:
                        caught = type(error).__name__
                    else:
                        raise RuntimeError("recorded signal was not propagated")
                    events = child.stdout.read().splitlines()
                    try:
                        os.kill(child.pid, 0)
                    except ProcessLookupError:
                        gone = True
                    else:
                        gone = False
                    return {{
                        "caught": caught,
                        "returncode": returncode,
                        "events": events,
                        "gone": gone,
                    }}
                finally:
                    if sender is not None:
                        sender.join(timeout=1.0)
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait(timeout=2.0)

            print(json.dumps({{
                "interrupt_followup": run_case(signal.SIGINT, signal.SIGHUP, 73),
                "quit": run_case(signal.SIGQUIT, None, 74),
            }}, sort_keys=True))
            """
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", probe],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=10.0,
            shell=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        payload = json.loads(result.stdout.decode("ascii"))
        interrupt = payload["interrupt_followup"]
        self.assertEqual(interrupt["caught"], "KeyboardInterrupt")
        self.assertEqual(interrupt["returncode"], 73)
        self.assertEqual(
            interrupt["events"],
            [
                f"signal:{signal.SIGINT}",
                f"signal:{signal.SIGHUP}",
                f"clean:{signal.SIGHUP}",
            ],
        )
        self.assertTrue(interrupt["gone"])
        quit_case = payload["quit"]
        self.assertEqual(quit_case["caught"], "SystemExit")
        self.assertEqual(quit_case["returncode"], 74)
        self.assertEqual(
            quit_case["events"],
            [f"signal:{signal.SIGQUIT}", f"clean:{signal.SIGQUIT}"],
        )
        self.assertTrue(quit_case["gone"])

    def test_scope_signal_never_targets_exited_or_unbound_process_group(self) -> None:
        exited = _fake_scope_process(7)
        with (
            mock.patch.object(process_priority.os, "getpgid") as getpgid,
            mock.patch.object(process_priority.os, "killpg") as kill_group,
        ):
            self.assertFalse(
                process_priority._signal_scope_attempt_group(
                    exited,
                    signal.SIGINT,
                )
            )
        getpgid.assert_not_called()
        kill_group.assert_not_called()

        unbound = _fake_scope_process(None)
        with (
            mock.patch.object(process_priority.os, "getpgid", return_value=4343),
            mock.patch.object(process_priority.os, "killpg") as kill_group,
        ):
            self.assertFalse(
                process_priority._signal_scope_attempt_group(
                    unbound,
                    signal.SIGTERM,
                )
            )
        kill_group.assert_not_called()

    def test_local_model_scope_rejection_from_high_parent_never_reaches_launcher(self) -> None:
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                clear=False,
            ),
            mock.patch.object(
                process_priority,
                "_required_priority_tool",
                side_effect=("/usr/bin/ionice", "/usr/bin/nice"),
            ),
            mock.patch.object(
                process_priority,
                "_local_model_cpu_adjustment",
                return_value=10,
            ),
            mock.patch.object(
                process_priority,
                "build_local_model_priority_scope_command",
                side_effect=process_priority.PriorityScopeError("low scope rejected"),
            ),
            mock.patch.object(
                command_chain,
                "run_process_bounded_output",
            ) as model_launcher,
            mock.patch.object(
                command_chain,
                "_command_path",
                return_value=os.path.realpath(sys.executable),
            ) as resolve_command,
        ):
            with self.assertRaisesRegex(
                command_chain.CommandChainError,
                "low scope rejected",
            ):
                command_chain.run_command_chain(
                    [["python3", "-c", "print('model')"]],
                    "",
                    label="local model",
                    local_model_priority=True,
                )

        resolve_command.assert_called_once_with("python3")
        model_launcher.assert_not_called()

    def test_priority_weight_parser_accepts_cgroup_v2_io_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cpu_path = Path(tmp) / "cpu.weight"
            io_path = Path(tmp) / "io.weight"
            cpu_path.write_text("200\n", encoding="ascii")
            io_path.write_text("default 200\n8:0 100\n", encoding="ascii")

            self.assertEqual(process_priority._parse_cgroup_weight(cpu_path), 200)
            self.assertEqual(
                process_priority._parse_cgroup_weight(io_path, io_weight=True),
                200,
            )

    def test_priority_scope_identity_round_trips_and_rejects_ambiguous_paths(self) -> None:
        identity = process_priority.PriorityScopeIdentity(
            "/sys/fs/cgroup/user.slice/recording.scope",
            42,
            1234,
        )
        rendered = process_priority.serialize_priority_scope_identity(identity)

        self.assertIsNotNone(rendered)
        self.assertEqual(process_priority.parse_priority_scope_identity(rendered), identity)
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                "/sys/fs/cgroup/user.slice/recording|scope|42|1234"
            )
        )
        self.assertIsNone(
            process_priority.serialize_priority_scope_identity(
                process_priority.PriorityScopeIdentity(
                    "/sys/fs/cgroup/user.slice/recording\n.scope",
                    42,
                    1234,
                )
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                "/sys/fs/cgroup/user.slice/recording\t.scope|42|1234"
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                "/sys/fs/cgroup/user.slice/recording.scope|٤٢|1234"
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                "/sys/fs/cgroup/user.slice/recording.scope|042|1234"
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                "/sys/fs/cgroup/user.slice/recording.scope|-42|1234"
            )
        )

    def test_priority_scope_identity_parser_rejects_huge_numbers_without_exception(self) -> None:
        path = "/sys/fs/cgroup/user.slice/recording.scope"
        huge = "9" * 5000

        self.assertIsNone(
            process_priority.parse_priority_scope_identity(f"{path}|{huge}|1")
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(f"{path}|1|{huge}")
        )

    def test_priority_scope_identity_enforces_unsigned_64_bit_boundaries(self) -> None:
        path = "/sys/fs/cgroup/user.slice/recording.scope"
        maximum = (1 << 64) - 1
        identity = process_priority.PriorityScopeIdentity(path, maximum, maximum)

        rendered = process_priority.serialize_priority_scope_identity(identity)

        self.assertEqual(rendered, f"{path}|18446744073709551615|18446744073709551615")
        self.assertEqual(process_priority.parse_priority_scope_identity(rendered), identity)
        too_large = maximum + 1
        self.assertIsNone(
            process_priority.serialize_priority_scope_identity(
                process_priority.PriorityScopeIdentity(path, too_large, 1)
            )
        )
        self.assertIsNone(
            process_priority.serialize_priority_scope_identity(
                process_priority.PriorityScopeIdentity(path, 1, too_large)
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                f"{path}|18446744073709551616|1"
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                f"{path}|1|18446744073709551616"
            )
        )

    def test_priority_scope_identity_rejects_oversized_path_and_total_value(self) -> None:
        oversized_path = "/" + "a" * 4096
        self.assertIsNone(
            process_priority.serialize_priority_scope_identity(
                process_priority.PriorityScopeIdentity(oversized_path, 1, 1)
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                f"{oversized_path}|1|1"
            )
        )
        self.assertIsNone(
            process_priority.parse_priority_scope_identity(
                "/scope|" + "1" * 5000 + "|1"
            )
        )

    def test_scope_preflight_does_not_skip_unrelated_nonzero_host_error(self) -> None:
        self.assertFalse(_scope_manager_unavailable(b"systemd-run: operation not supported\n"))
        self.assertFalse(_scope_manager_unavailable(b"systemd-run: not supported\n"))

    def test_real_soc_scope_expands_taskset_affinity_inside_target_cgroup(self) -> None:
        systemd_run = shutil.which("systemd-run", path=process_priority._TRUSTED_COMMAND_PATH)
        current = os.sched_getaffinity(0)
        if (
            not systemd_run
            or not os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            or not os.environ.get("XDG_RUNTIME_DIR")
            or not current
        ):
            self.skipTest("user systemd scope environment is unavailable")
        probe = (
            "import json, os; from speed_of_cinnamon import process_priority as p; "
            "print(json.dumps({'affinity': sorted(os.sched_getaffinity(0)), "
            "'allowed': sorted(p._scope_exec_allowed_cpus())}))"
        )
        command = process_priority.build_soc_priority_scope_command(
            [sys.executable, "-c", probe]
        )
        launcher = (
            "import os; "
            f"command={command!r}; "
            f"os.sched_setaffinity(0, {{{min(current)}}}); "
            "os.execv(command[0], command)"
        )
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        result = subprocess.run(  # nosec B603
            [sys.executable, "-c", launcher],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            timeout=5.0,
            shell=False,
        )
        if result.returncode != 0 and _scope_manager_unavailable(result.stderr):
            self.skipTest(
                "user systemd scope unavailable: "
                f"{result.stderr.decode(errors='replace')}"
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        payload = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(payload["affinity"], payload["allowed"])
        self.assertGreater(len(payload["allowed"]), 1)

    def test_real_soc_scope_preserves_identity_weights_and_term_cleanup(self) -> None:
        systemd_run = shutil.which("systemd-run", path=process_priority._TRUSTED_COMMAND_PATH)
        if not systemd_run:
            self.skipTest("trusted systemd-run is unavailable")
        probe = (
            "import json, os, time; from pathlib import Path; "
            "relative=next(line[3:] for line in Path('/proc/self/cgroup').read_text().splitlines() "
            "if line.startswith('0::')); "
            "cgroup=Path('/sys/fs/cgroup') / relative.lstrip('/'); "
            "print(json.dumps({'pid': os.getpid(), 'pgid': os.getpgid(0), 'sid': os.getsid(0), "
            "'cgroup': str(cgroup), 'cpu': (cgroup / 'cpu.weight').read_text().strip(), "
            "'io': (cgroup / 'io.weight').read_text().strip()}), flush=True); "
            "time.sleep(30)"
        )
        process = subprocess.Popen(  # nosec B603
            process_priority.build_soc_priority_scope_command([sys.executable, "-c", probe]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
        )
        payload: dict[str, object] | None = None
        try:
            if process.stdout is None:
                self.fail("scope probe stdout is unavailable")
            ready, _, _ = select.select([process.stdout], [], [], 3.0)
            if not ready:
                stdout, stderr = process.communicate(timeout=3.0)
                if process.returncode != 0 and _scope_manager_unavailable(stderr):
                    self.skipTest(f"user systemd scope unavailable: {stderr.decode(errors='replace')}")
                self.fail(f"scope probe produced no output: {stdout!r}")
            line = process.stdout.readline()
            if not line:
                stdout, stderr = process.communicate(timeout=3.0)
                if process.returncode != 0 and _scope_manager_unavailable(stderr):
                    self.skipTest(f"user systemd scope unavailable: {stderr.decode(errors='replace')}")
                self.fail(f"scope probe produced no output: {stdout!r}")
            payload = json.loads(line.decode("utf-8"))
            self.assertEqual(payload["pid"], process.pid)
            self.assertEqual(payload["pgid"], process.pid)
            self.assertEqual(payload["sid"], process.pid)
            self.assertEqual(payload["cpu"], "200")
            self.assertEqual(payload["io"], "default 200")
            scope_path = Path(str(payload["cgroup"]))
            self.assertTrue(scope_path.is_absolute())
            self.assertTrue(str(scope_path).startswith("/sys/fs/cgroup/"))
            os.killpg(process.pid, signal.SIGTERM)
            stdout_tail, stderr = process.communicate(timeout=3.0)
            self.assertEqual(process.returncode, -signal.SIGTERM)
            self.assertEqual(stderr, b"")
            self.assertEqual(stdout_tail, b"")
            cleanup_deadline = time.monotonic() + 2.0
            while scope_path.exists() and time.monotonic() < cleanup_deadline:
                time.sleep(0.05)
            self.assertFalse(scope_path.exists())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3.0)

    def test_real_soc_scope_keeps_detached_child_for_recorder_persistence(self) -> None:
        systemd_run = shutil.which("systemd-run", path=process_priority._TRUSTED_COMMAND_PATH)
        if not systemd_run or not os.environ.get("DBUS_SESSION_BUS_ADDRESS") or not os.environ.get("XDG_RUNTIME_DIR"):
            self.skipTest("user systemd scope environment is unavailable")
        probe = (
            "import json, os, sys, time\n"
            "child=os.fork()\n"
            "if child:\n"
            "    print(json.dumps({'child': child}), flush=True)\n"
            "    sys.exit(0)\n"
            "os.setsid()\n"
            "time.sleep(30)\n"
        )
        process = subprocess.Popen(  # nosec B603
            process_priority.build_soc_priority_scope_command([sys.executable, "-c", probe]),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            shell=False,
        )
        child_pid: int | None = None
        try:
            if process.stdout is None:
                self.fail("scope persistence probe stdout is unavailable")
            ready, _, _ = select.select([process.stdout], [], [], 3.0)
            if not ready:
                stdout, stderr = process.communicate(timeout=3.0)
                if process.returncode != 0 and _scope_manager_unavailable(stderr):
                    self.skipTest(f"user systemd scope unavailable: {stderr.decode(errors='replace')}")
                self.fail(f"scope persistence probe produced no output: {stdout!r}")
            payload = json.loads(process.stdout.readline().decode("utf-8"))
            child_pid = int(payload["child"])
            process.wait(timeout=3.0)
            self.assertEqual(process.returncode, 0)
            os.kill(child_pid, 0)
            child_cgroup = Path(f"/proc/{child_pid}/cgroup").read_text(encoding="ascii")
            child_relative = next(line[3:] for line in child_cgroup.splitlines() if line.startswith("0::"))
            child_scope = Path("/sys/fs/cgroup") / child_relative.lstrip("/")
            self.assertTrue(str(child_scope).endswith(".scope"))
            self.assertEqual((child_scope / "cpu.weight").read_text(encoding="ascii").strip(), "200")
            self.assertTrue(
                (child_scope / "io.weight").read_text(encoding="ascii").startswith("default 200")
            )
            os.killpg(child_pid, signal.SIGTERM)
            for _ in range(40):
                try:
                    os.kill(child_pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
            with self.assertRaises(OSError):
                os.kill(child_pid, 0)
        finally:
            if child_pid is not None:
                try:
                    os.killpg(child_pid, signal.SIGTERM)
                except OSError:
                    pass
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3.0)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_real_soc_scope_preserves_exit_and_streams(self) -> None:
        systemd_run = shutil.which("systemd-run", path=process_priority._TRUSTED_COMMAND_PATH)
        if not systemd_run:
            self.skipTest("trusted systemd-run is unavailable")
        command = process_priority.build_soc_priority_scope_command(
            [
                sys.executable,
                "-c",
                "import sys; print('stdout'); print('stderr', file=sys.stderr); sys.exit(7)",
            ]
        )
        listener, address, nonce = process_priority._create_scope_exec_latch()
        environment = os.environ.copy()
        environment[process_priority.SOC_PRIORITY_SCOPE_MARKER] = "1"
        environment[process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV] = address
        environment[process_priority._SCOPE_EXEC_LATCH_NONCE_ENV] = nonce
        process: subprocess.Popen[bytes] | None = None
        watchdog: threading.Timer | None = None
        watchdog_started = False
        watchdog_fired = threading.Event()
        cleanup_lock = threading.Lock()
        cleanup_started = False
        cleanup_failed = False
        stream_timed_out = False
        skip_reason: str | None = None

        def terminate_scope_once() -> None:
            nonlocal cleanup_started, cleanup_failed
            with cleanup_lock:
                if (
                    cleanup_started
                    or process is None
                    or process.poll() is not None
                ):
                    return
                cleanup_started = True
            try:
                process_priority._terminate_scope_attempt(
                    process,
                    process_group_id=process.pid,
                )
            except BaseException:
                cleanup_failed = True
            if process.poll() is None:
                try:
                    process.kill()
                    process.wait(timeout=3.0)
                except (OSError, subprocess.TimeoutExpired):
                    cleanup_failed = True

        def expire_harness() -> None:
            watchdog_fired.set()
            terminate_scope_once()

        try:
            process = subprocess.Popen(  # nosec B603
                command,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                shell=False,
            )
            watchdog = threading.Timer(5.0, expire_harness)
            watchdog.start()
            watchdog_started = True
            outcome = process_priority._supervise_scope_attempt(
                process,
                listener,
                nonce,
            )
            if process.stdout is None or process.stderr is None:
                self.fail("scope streams are unavailable")
            try:
                stdout, stderr = process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                stream_timed_out = True
                terminate_scope_once()
                stdout, stderr = b"", b""
            if not watchdog_fired.is_set() and not stream_timed_out:
                if outcome.returncode != 7 and _scope_manager_unavailable(stderr):
                    skip_reason = (
                        "user systemd scope unavailable: "
                        f"rc={outcome.returncode}, "
                        f"stderr={stderr.decode(errors='replace')}"
                    )
                else:
                    self.assertEqual(
                        outcome,
                        process_priority._ScopeExecAttemptOutcome(
                            returncode=7,
                            latch_state="entered",
                            timed_out=False,
                        ),
                    )
                    self.assertEqual(stdout, b"stdout\n")
                    self.assertEqual(stderr, b"stderr\n")
        finally:
            active_exception = sys.exc_info()[0] is not None
            if watchdog_started and watchdog is not None:
                try:
                    watchdog.cancel()
                    watchdog.join()
                except Exception:
                    cleanup_failed = True
            try:
                terminate_scope_once()
            except Exception:
                cleanup_failed = True
            if process is not None and process.stdout is not None:
                try:
                    process.stdout.close()
                except Exception:
                    cleanup_failed = True
            if process is not None and process.stderr is not None:
                try:
                    process.stderr.close()
                except Exception:
                    cleanup_failed = True
            try:
                listener.close()
            except Exception:
                cleanup_failed = True
            if not active_exception:
                if watchdog_fired.is_set() or stream_timed_out:
                    self.fail("scope harness timed out")
                if cleanup_failed:
                    self.fail("scope harness cleanup failed")
                if skip_reason is not None:
                    self.skipTest(skip_reason)

    def test_real_cli_entrypoint_keeps_json_after_high_scope_bootstrap(self) -> None:
        systemd_run = shutil.which("systemd-run", path=process_priority._TRUSTED_COMMAND_PATH)
        if not systemd_run or not os.environ.get("DBUS_SESSION_BUS_ADDRESS") or not os.environ.get("XDG_RUNTIME_DIR"):
            self.skipTest("user systemd scope environment is unavailable")
        environment = os.environ.copy()
        environment.pop(process_priority.SOC_PRIORITY_SCOPE_MARKER, None)
        environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        with tempfile.TemporaryDirectory() as tmp:
            environment["XDG_STATE_HOME"] = str(Path(tmp) / "state")
            environment["XDG_CACHE_HOME"] = str(Path(tmp) / "cache")
            result = subprocess.run(  # nosec B603
                [sys.executable, "-m", "speed_of_cinnamon.cli", "models", "--json"],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                timeout=10.0,
                shell=False,
            )
        if result.returncode != 0 and _scope_manager_unavailable(result.stderr):
            self.skipTest(
                "direct CLI scope unavailable: "
                f"rc={result.returncode}, stderr={result.stderr.decode(errors='replace')}"
            )
        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertIsInstance(payload, dict)
        self.assertIn("status", payload)
        self.assertEqual(result.stderr, b"")

    def test_negative_nice_permission_denied_with_zero_exit_keeps_low_child_priority(self) -> None:
        nice = shutil.which("nice", path=process_priority._TRUSTED_COMMAND_PATH)
        if not nice or os.geteuid() == 0:
            self.skipTest("unprivileged nice semantics are not available")
        result = subprocess.run(
            [
                nice,
                "--adjustment",
                "10",
                nice,
                "--adjustment",
                "-5",
                sys.executable,
                "-c",
                "import os; print(os.getpriority(os.PRIO_PROCESS, 0))",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"LANG": "C", "LC_ALL": "C"},
        )
        if "permission denied" not in result.stderr.lower():
            self.skipTest("host permits the negative nice adjustment")
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(int(result.stdout.strip()), process_priority.LOCAL_MODEL_CPU_NICE)

    def test_ionice_failure_falls_back_without_raising(self) -> None:
        with mock.patch.object(process_priority, "_current_priority_scope_weights", return_value=(200, 100)):
            self.assertEqual(process_priority.apply_process_priority(), (True, False))


class CgroupResourceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.namespace_identity = process_priority.InitialNamespaceIdentity(
            user=(1, 0xEFFFFFFD),
            pid=(1, 0xEFFFFFFC),
            mount=(1, 0xEFFFFFF8),
            cgroup=(1, 0xEFFFFFFB),
        )
        self.namespace_patcher = mock.patch.object(
            process_priority,
            "initial_namespace_identity",
            return_value=self.namespace_identity,
        )
        self.namespace_patcher.start()
        self.addCleanup(self.namespace_patcher.stop)

    def test_snapshot_uses_descriptor_bound_cpuset_and_ancestry_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _mountpoint, _parent, _scope = (
                _write_resource_scope_fixture(root)
            )
            online.write_text("0-3\n", encoding="ascii")
            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(process_priority, "_PROC_SELF_MOUNTINFO", mountinfo),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                snapshot = process_priority.current_cgroup_resource_snapshot(
                    include_online_cpus=True
                )

        gib = 1024**3
        self.assertEqual(
            snapshot,
            process_priority.CgroupResourceSnapshot(
                effective_cpus=frozenset({0, 1, 2, 3}),
                online_cpus=frozenset({0, 1, 2, 3}),
                cpu_quota_us=200_000,
                cpu_period_us=100_000,
                memory_current_bytes=gib,
                memory_max_bytes=12 * gib,
                memory_high_bytes=11 * gib,
                memory_max_headroom_bytes=10 * gib,
                memory_high_headroom_bytes=9 * gib,
            ),
        )

    def test_snapshot_rejects_direct_mount_root_membership(self) -> None:
        mapping = process_priority._Cgroup2MountMapping(
            mount_root=Path("/"),
            mountpoint=Path("/cgroup"),
            device_major=0,
            device_minor=1,
        )
        with (
            mock.patch.object(
                process_priority,
                "_scope_exec_cpuset_snapshot_from_bound",
            ) as cpuset_snapshot,
            mock.patch.object(
                process_priority,
                "_read_bound_cgroup_resource_file",
            ) as resource_read,
        ):
            snapshot = process_priority._cgroup_resource_snapshot_from_bound(
                (7,),
                ((1, 2),),
                mapping,
            )

        self.assertIsNone(snapshot)
        cpuset_snapshot.assert_not_called()
        resource_read.assert_not_called()

    def test_snapshot_fails_closed_for_invalid_resources(self) -> None:
        for failure in (
            "symlink",
            "oversized",
            "malformed",
            "parent-missing",
            "leaf-missing",
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cgroup, mountinfo, _online, _mountpoint, parent, scope = (
                    _write_resource_scope_fixture(root)
                )
                target = scope / "memory.current"
                if failure == "symlink":
                    replacement = root / "replacement"
                    replacement.write_text("1\n", encoding="ascii")
                    target.unlink()
                    target.symlink_to(replacement)
                elif failure == "oversized":
                    target.write_text(
                        "9" * (process_priority.MAX_CGROUP_FILE_BYTES + 1),
                        encoding="ascii",
                    )
                elif failure == "malformed":
                    (scope / "cpu.max").write_text(
                        "max invalid\n",
                        encoding="ascii",
                    )
                elif failure == "parent-missing":
                    (parent / "memory.high").unlink()
                elif failure == "leaf-missing":
                    (scope / "memory.high").unlink()
                with (
                    mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                    mock.patch.object(
                        process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                    ),
                ):
                    self.assertIsNone(
                        process_priority.current_cgroup_resource_snapshot()
                    )

    def test_snapshot_reads_online_cpus_only_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _mountpoint, _parent, _scope = (
                _write_resource_scope_fixture(root)
            )
            target = root / "online-target"
            target.write_text("0-3\n", encoding="ascii")
            online.unlink()
            online.symlink_to(target)
            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(
                    process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                ),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
            ):
                without_online = process_priority.current_cgroup_resource_snapshot()
                with_online = process_priority.current_cgroup_resource_snapshot(
                    include_online_cpus=True
                )

        self.assertIsNotNone(without_online)
        self.assertIsNone(without_online.online_cpus)
        self.assertIsNone(with_online)

    def test_snapshot_opens_proc_sys_and_cgroup_files_nonblocking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, online, _mountpoint, _parent, _scope = (
                _write_resource_scope_fixture(root)
            )
            online.write_text("0-3\n", encoding="ascii")
            real_open = process_priority.os.open
            opened_flags: list[int] = []

            def capture_flags(
                path: object,
                flags: int,
                *args: object,
                **kwargs: object,
            ) -> int:
                opened_flags.append(flags)
                return real_open(path, flags, *args, **kwargs)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(
                    process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                ),
                mock.patch.object(process_priority, "_CPU_ONLINE", online),
                mock.patch.object(
                    process_priority.os,
                    "open",
                    side_effect=capture_flags,
                ),
            ):
                snapshot = process_priority.current_cgroup_resource_snapshot(
                    include_online_cpus=True
                )

        self.assertIsNotNone(snapshot)
        self.assertTrue(opened_flags)
        self.assertTrue(
            all(flags & process_priority.os.O_NONBLOCK for flags in opened_flags)
        )

    def test_snapshot_rejects_same_inode_content_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, _online, _mountpoint, _parent, scope = (
                _write_resource_scope_fixture(root)
            )
            original = process_priority._cgroup_resource_snapshot_once
            calls = 0

            def mutate_after_first(*args: object) -> object:
                nonlocal calls
                result = original(*args)
                calls += 1
                if calls == 1:
                    (scope / "memory.max").write_text("2\n", encoding="ascii")
                return result

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(
                    process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                ),
                mock.patch.object(
                    process_priority,
                    "_cgroup_resource_snapshot_once",
                    side_effect=mutate_after_first,
                ),
            ):
                self.assertIsNone(process_priority.current_cgroup_resource_snapshot())

        self.assertEqual(calls, 1)

    def test_snapshot_conservatively_accepts_dynamic_memory_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, _online, _mountpoint, _parent, scope = (
                _write_resource_scope_fixture(root)
            )
            original = process_priority._cgroup_resource_snapshot_once

            def increase_after_first(*args: object) -> object:
                result = original(*args)
                (scope / "memory.current").write_text(
                    f"{2 * 1024**3}\n",
                    encoding="ascii",
                )
                return result

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(
                    process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                ),
                mock.patch.object(
                    process_priority,
                    "_cgroup_resource_snapshot_once",
                    side_effect=increase_after_first,
                ),
            ):
                snapshot = process_priority.current_cgroup_resource_snapshot()

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.memory_current_bytes, 2 * 1024**3)

    def test_snapshot_rejects_final_membership_mapping_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, _online, _mountpoint, _parent, _scope = (
                _write_resource_scope_fixture(root)
            )
            original = process_priority._mapped_cgroup2_location
            calls = 0

            def change_on_final(*args: object, **kwargs: object) -> object:
                nonlocal calls
                calls += 1
                if calls == 3:
                    return None
                return original(*args, **kwargs)

            with (
                mock.patch.object(process_priority, "_PROC_SELF_CGROUP", cgroup),
                mock.patch.object(
                    process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                ),
                mock.patch.object(
                    process_priority,
                    "_mapped_cgroup2_location",
                    side_effect=change_on_final,
                ),
            ):
                self.assertIsNone(process_priority.current_cgroup_resource_snapshot())

        self.assertEqual(calls, 3)

    def test_snapshot_requires_stable_initial_namespaces(self) -> None:
        changes = {
            "user_changed": process_priority.InitialNamespaceIdentity(
                user=(1, 0xEFFFFFF0),
                pid=self.namespace_identity.pid,
                mount=self.namespace_identity.mount,
                cgroup=self.namespace_identity.cgroup,
            ),
            "pid_changed": process_priority.InitialNamespaceIdentity(
                user=self.namespace_identity.user,
                pid=(1, 0xEFFFFFF0),
                mount=self.namespace_identity.mount,
                cgroup=self.namespace_identity.cgroup,
            ),
            "mount_changed": process_priority.InitialNamespaceIdentity(
                user=self.namespace_identity.user,
                pid=self.namespace_identity.pid,
                mount=(1, 0xEFFFFFF0),
                cgroup=self.namespace_identity.cgroup,
            ),
            "cgroup_changed": process_priority.InitialNamespaceIdentity(
                user=self.namespace_identity.user,
                pid=self.namespace_identity.pid,
                mount=self.namespace_identity.mount,
                cgroup=(1, 0xEFFFFFF0),
            ),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cgroup, mountinfo, _online, _mountpoint, _parent, _scope = (
                _write_resource_scope_fixture(root)
            )
            cases = [("unavailable", [None])]
            cases.extend(
                (name, [self.namespace_identity, changed])
                for name, changed in changes.items()
            )
            for name, identities in cases:
                with (
                    self.subTest(name=name),
                    mock.patch.object(
                        process_priority, "_PROC_SELF_CGROUP", cgroup
                    ),
                    mock.patch.object(
                        process_priority, "_PROC_SELF_MOUNTINFO", mountinfo
                    ),
                    mock.patch.object(
                        process_priority,
                        "initial_namespace_identity",
                        side_effect=identities,
                    ) as namespace_probe,
                ):
                    self.assertIsNone(
                        process_priority.current_cgroup_resource_snapshot()
                    )
                self.assertEqual(namespace_probe.call_count, len(identities))

    def test_snapshot_defers_when_mount_hides_resource_ancestors(self) -> None:
        mapping = process_priority._Cgroup2MountMapping(
            mount_root=Path("/hidden.slice"),
            mountpoint=Path("/visible"),
            device_major=0,
            device_minor=1,
        )
        with (
            mock.patch.object(
                process_priority,
                "_mapped_cgroup2_location",
                return_value=(Path("/visible/work.scope"), mapping),
            ),
            mock.patch.object(
                process_priority,
                "_cgroup_resource_snapshot_once",
            ) as snapshot,
        ):
            self.assertIsNone(process_priority.current_cgroup_resource_snapshot())

        snapshot.assert_not_called()

    def test_resource_gate_bootstrap_binds_resource_gate_module(self) -> None:
        process = _fake_scope_process(1)
        captured: dict[str, object] = {}

        def reject_scope(argv: list[str], **kwargs: object) -> mock.Mock:
            captured.update(argv=argv, **kwargs)
            return process

        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: ""},
                clear=False,
            ),
            mock.patch.object(
                process_priority.shutil,
                "which",
                return_value="/usr/bin/systemd-run",
            ),
            mock.patch.object(
                process_priority,
                "build_soc_priority_scope_command",
                wraps=process_priority.build_soc_priority_scope_command,
            ) as build,
            mock.patch.object(
                process_priority.subprocess,
                "Popen",
                side_effect=reject_scope,
            ) as popen,
        ):
            self.assertFalse(
                process_priority.ensure_resource_gate_priority_scope(
                    ["--require-full-online"]
                )
            )

        runtime, entry = process_priority._scope_exec_wrapper_paths()
        target = (
            runtime,
            "-m",
            "speed_of_cinnamon.resource_gate",
            "--require-full-online",
        )
        build.assert_called_once_with(target)
        popen.assert_called_once()
        command = captured["argv"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertNotIn("--wait", command)
        token_index = command.index(process_priority._SCOPE_EXEC_WRAPPER_TOKEN)
        self.assertEqual(command[token_index - 2 : token_index], [runtime, entry])
        self.assertEqual(
            command[token_index + 1],
            process_priority._SCOPE_EXEC_LATCH_REQUIRED_TOKEN,
        )
        supervisor = process_priority._scope_exec_supervisor_spec(command)
        self.assertIsNotNone(supervisor)
        assert supervisor is not None
        self.assertEqual(supervisor.target.argv, tuple(target))
        environment = captured["env"]
        self.assertIsInstance(environment, dict)
        assert isinstance(environment, dict)
        self.assertEqual(
            environment[process_priority.SOC_PRIORITY_SCOPE_MARKER],
            "1",
        )
        self.assertRegex(
            environment[process_priority._SCOPE_EXEC_LATCH_ADDRESS_ENV],
            process_priority._SCOPE_EXEC_LATCH_ADDRESS_RE,
        )
        self.assertRegex(
            environment[process_priority._SCOPE_EXEC_LATCH_NONCE_ENV],
            process_priority._SCOPE_EXEC_LATCH_NONCE_RE,
        )
        process.wait.assert_called_once()

    def test_verified_resource_gate_scope_does_not_mutate_affinity(self) -> None:
        identity = process_priority.PriorityScopeIdentity(
            path="/trusted.scope",
            device=1,
            inode=2,
        )
        with (
            mock.patch.dict(
                process_priority.os.environ,
                {process_priority.SOC_PRIORITY_SCOPE_MARKER: "1"},
                clear=False,
            ),
            mock.patch.object(process_priority.os, "getpid", return_value=4242),
            mock.patch.object(
                process_priority,
                "priority_scope_identity_for_pid",
                return_value=identity,
            ),
            mock.patch.object(
                process_priority,
                "verify_priority_scope_identity",
                return_value=True,
            ) as verify,
            mock.patch.object(
                process_priority,
                "_normalize_cpu_affinity_for_scope_exec",
            ) as normalize,
        ):
            self.assertTrue(
                process_priority.ensure_resource_gate_priority_scope([])
            )

        normalize.assert_not_called()
        verify.assert_called_once_with(
            identity,
            pid=4242,
            cpu_weight=process_priority.SOC_CPU_WEIGHT,
            io_weight=process_priority.SOC_IO_WEIGHT,
        )


class InitialNamespaceIdentityTests(unittest.TestCase):
    _PIDFD = 40
    _SPECS = (
        ("user", 0xFF09, 0x10000000, 3, 0xEFFFFFFD, 41),
        ("pid", 0xFF05, 0x20000000, 4, 0xEFFFFFFC, 42),
        ("mnt", 0xFF03, 0x00020000, 8, 0xEFFFFFF8, 43),
        ("cgroup", 0xFF01, 0x02000000, 5, 0xEFFFFFFB, 44),
    )

    def _run_probe(
        self,
        faults: dict[object, object] | None = None,
    ) -> tuple[
        process_priority.InitialNamespaceIdentity | None,
        mock.Mock,
        mock.Mock,
        mock.Mock,
        mock.Mock,
        list[tuple[str, int, int]],
    ]:
        configured = {} if faults is None else faults
        specs_by_request = {spec[1]: spec for spec in self._SPECS}
        specs_by_descriptor = {spec[5]: spec for spec in self._SPECS}
        stat_calls: dict[str, int] = {}
        events: list[tuple[str, int, int]] = []

        def configured_value(key: object, default: object) -> object:
            value = configured.get(key, default)
            if isinstance(value, BaseException):
                raise value
            return value

        def pidfd_open(_process_id: int, _flags: int) -> object:
            return configured_value("pidfd_open", self._PIDFD)

        def ioctl(
            descriptor: int,
            request: int,
            argument: object = 0,
            mutate: bool = True,
        ) -> object:
            events.append(("ioctl", descriptor, request))
            if descriptor == self._PIDFD:
                spec = specs_by_request.get(request)
                if spec is None:
                    raise OSError(errno.ENOTTY, "unsupported")
                name, _request, _kind, _namespace_id, _inode, namespace_fd = spec
                return configured_value(("get", name), namespace_fd)
            spec = specs_by_descriptor.get(descriptor)
            if spec is None:
                raise OSError(errno.EBADF, "invalid")
            name, _request, kind, namespace_id, _inode, _namespace_fd = spec
            if request == 0xB703:
                return configured_value(("type", name), kind)
            if request == 0x8008B70D:
                result = configured_value(("id_result", name), 0)
                if result == 0:
                    value = int(configured_value(("id", name), namespace_id))
                    if not isinstance(argument, bytearray) or not mutate:
                        raise OSError(errno.EINVAL, "invalid buffer")
                    argument[:] = value.to_bytes(8, byteorder=sys.byteorder)
                return result
            raise OSError(errno.ENOTTY, "unsupported")

        def fstat(descriptor: int) -> object:
            spec = specs_by_descriptor.get(descriptor)
            if spec is None:
                raise OSError(errno.EBADF, "invalid")
            name, _request, _kind, _namespace_id, inode, _namespace_fd = spec
            count = stat_calls.get(name, 0)
            stat_calls[name] = count + 1
            key = ("before_stat" if count == 0 else "after_stat", name)
            override = configured_value(key, None)
            if override is not None:
                return override
            first_inode = int(configured_value(("inode", name), inode))
            effective_inode = int(
                configured_value(("after_inode", name), first_inode)
            )
            mode = int(configured_value(("mode", name), stat.S_IFREG | 0o444))
            return mock.Mock(st_mode=mode, st_dev=7, st_ino=effective_inode)

        def descriptor_flags(descriptor: int, operation: int) -> int:
            spec = specs_by_descriptor.get(descriptor)
            if spec is None:
                raise OSError(errno.EBADF, "invalid")
            name = spec[0]
            if operation == process_priority.fcntl.F_GETFL:
                return int(
                    configured_value(("status_flags", name), os.O_RDONLY)
                )
            if operation == process_priority.fcntl.F_GETFD:
                return int(
                    configured_value(
                        ("descriptor_flags", name),
                        process_priority.fcntl.FD_CLOEXEC,
                    )
                )
            raise OSError(errno.EINVAL, "invalid operation")

        def close_descriptor(descriptor: int) -> None:
            configured_value(("close", descriptor), None)

        with (
            mock.patch.object(process_priority.os, "getpid", return_value=1234),
            mock.patch.object(
                process_priority.os,
                "pidfd_open",
                side_effect=pidfd_open,
            ) as open_pidfd,
            mock.patch.object(
                process_priority.os,
                "open",
                side_effect=AssertionError("namespace path open is forbidden"),
            ) as path_open,
            mock.patch.object(
                process_priority.fcntl,
                "ioctl",
                side_effect=ioctl,
            ) as ioctl_mock,
            mock.patch.object(process_priority.os, "fstat", side_effect=fstat),
            mock.patch.object(
                process_priority.fcntl,
                "fcntl",
                side_effect=descriptor_flags,
            ),
            mock.patch.object(
                process_priority.os,
                "close",
                side_effect=close_descriptor,
            ) as close,
        ):
            identity = process_priority.initial_namespace_identity()
        return identity, open_pidfd, path_open, ioctl_mock, close, events

    def test_initial_namespace_identity_uses_kernel_pidfd_namespaces(self) -> None:
        identity, open_pidfd, path_open, ioctl, close, events = self._run_probe()

        self.assertEqual(
            identity,
            process_priority.InitialNamespaceIdentity(
                user=(7, 0xEFFFFFFD),
                pid=(7, 0xEFFFFFFC),
                mount=(7, 0xEFFFFFF8),
                cgroup=(7, 0xEFFFFFFB),
            ),
        )
        open_pidfd.assert_called_once_with(1234, 0)
        path_open.assert_not_called()
        self.assertEqual(
            [event[2] for event in events if event[1] == self._PIDFD],
            [spec[1] for spec in self._SPECS],
        )
        for _name, _request, _kind, _namespace_id, _inode, descriptor in self._SPECS:
            self.assertIn(("ioctl", descriptor, 0xB703), events)
            self.assertIn(("ioctl", descriptor, 0x8008B70D), events)
        closed = [call.args[0] for call in close.call_args_list]
        self.assertCountEqual(closed, [self._PIDFD, 41, 42, 43, 44])
        self.assertEqual(len(closed), len(set(closed)))
        self.assertGreaterEqual(ioctl.call_count, 12)

    def test_initial_namespace_uapi_constants_are_exact(self) -> None:
        expected = {
            "_PIDFD_GET_CGROUP_NAMESPACE": 0xFF01,
            "_PIDFD_GET_MNT_NAMESPACE": 0xFF03,
            "_PIDFD_GET_PID_NAMESPACE": 0xFF05,
            "_PIDFD_GET_USER_NAMESPACE": 0xFF09,
            "_NS_GET_NSTYPE": 0xB703,
            "_NS_GET_ID": 0x8008B70D,
            "USER_NS_INIT_ID": 3,
            "PID_NS_INIT_ID": 4,
            "CGROUP_NS_INIT_ID": 5,
            "MNT_NS_INIT_ID": 8,
            "USER_NS_INIT_INO": 0xEFFFFFFD,
            "PID_NS_INIT_INO": 0xEFFFFFFC,
            "CGROUP_NS_INIT_INO": 0xEFFFFFFB,
            "MNT_NS_INIT_INO": 0xEFFFFFF8,
        }
        for name, value in expected.items():
            with self.subTest(name=name):
                self.assertEqual(getattr(process_priority, name), value)

    def test_each_namespace_rejects_wrong_type_id_or_inode(self) -> None:
        for index, spec in enumerate(self._SPECS):
            name, _request, kind, namespace_id, inode, _descriptor = spec
            for fault, value in (
                ("type", kind ^ 1),
                ("id", namespace_id + 16),
                ("inode", inode + 16),
            ):
                with self.subTest(namespace=name, fault=fault):
                    identity, _pidfd, _open, _ioctl, close, _events = (
                        self._run_probe({(fault, name): value})
                    )
                self.assertIsNone(identity)
                closed = [call.args[0] for call in close.call_args_list]
                expected_closed = [self._PIDFD]
                expected_closed.extend(item[5] for item in self._SPECS[: index + 1])
                self.assertCountEqual(closed, expected_closed)
                self.assertEqual(len(closed), len(set(closed)))

    def test_namespace_descriptor_flags_and_snapshot_are_fail_closed(self) -> None:
        user = self._SPECS[0]
        name = user[0]
        unstable = mock.Mock(
            st_mode=stat.S_IFREG | 0o444,
            st_dev=7,
            st_ino=user[4] + 1,
        )
        cases = {
            "unstable": {("after_stat", name): unstable},
            "unreadable": {
                ("before_stat", name): PermissionError(errno.EACCES, "denied")
            },
            "nonregular": {("mode", name): stat.S_IFIFO | 0o400},
            "writeable": {("status_flags", name): os.O_WRONLY},
            "no_cloexec": {("descriptor_flags", name): 0},
            "getfl_error": {
                ("status_flags", name): PermissionError(errno.EPERM, "denied")
            },
            "getfd_error": {
                ("descriptor_flags", name): OSError(errno.ENOTTY, "unsupported")
            },
        }
        for failure, faults in cases.items():
            with self.subTest(failure=failure):
                identity, _pidfd, path_open, _ioctl, close, _events = (
                    self._run_probe(faults)
                )
                self.assertIsNone(identity)
                path_open.assert_not_called()
                closed = [call.args[0] for call in close.call_args_list]
                self.assertCountEqual(closed, [self._PIDFD, user[5]])
                self.assertEqual(len(closed), len(set(closed)))

    def test_pidfd_and_ioctl_failures_close_each_acquired_fd_once(self) -> None:
        for error_number in (
            errno.EACCES,
            errno.EPERM,
            errno.ENOTTY,
            errno.ESRCH,
            errno.EMFILE,
        ):
            with self.subTest(stage="pidfd_open", errno=error_number):
                identity, _pidfd, path_open, _ioctl, close, _events = (
                    self._run_probe(
                        {"pidfd_open": OSError(error_number, "unavailable")}
                    )
                )
                self.assertIsNone(identity)
                path_open.assert_not_called()
                close.assert_not_called()

        for index, spec in enumerate(self._SPECS):
            name = spec[0]
            with self.subTest(stage="pidfd_get", namespace=name):
                identity, _pidfd, _open, _ioctl, close, _events = (
                    self._run_probe(
                        {("get", name): OSError(errno.ENOTTY, "unsupported")}
                    )
                )
                self.assertIsNone(identity)
                expected_closed = [self._PIDFD]
                expected_closed.extend(item[5] for item in self._SPECS[:index])
                closed = [call.args[0] for call in close.call_args_list]
                self.assertCountEqual(closed, expected_closed)
                self.assertEqual(len(closed), len(set(closed)))

        for stage in ("type", "id_result"):
            with self.subTest(stage=stage):
                identity, _pidfd, _open, _ioctl, close, _events = (
                    self._run_probe(
                        {(stage, "user"): OSError(errno.EACCES, "denied")}
                    )
                )
                self.assertIsNone(identity)
                closed = [call.args[0] for call in close.call_args_list]
                self.assertCountEqual(closed, [self._PIDFD, 41])
                self.assertEqual(len(closed), len(set(closed)))

    def test_invalid_duplicate_and_cleanup_failures_are_fail_closed(self) -> None:
        cases = {
            "negative": ({("get", "user"): -1}, [self._PIDFD]),
            "boolean": ({("get", "user"): False}, [self._PIDFD]),
            "duplicates_pidfd": (
                {("get", "user"): self._PIDFD},
                [self._PIDFD],
            ),
            "duplicates_namespace": (
                {("get", "pid"): 41},
                [self._PIDFD, 41],
            ),
            "bad_id_return": (
                {("id_result", "user"): 1},
                [self._PIDFD, 41],
            ),
            "negative_id_return": (
                {("id_result", "user"): -1},
                [self._PIDFD, 41],
            ),
            "close_error": (
                {("close", 41): OSError(errno.EIO, "failed")},
                [self._PIDFD, 41, 42, 43, 44],
            ),
        }
        for failure, (faults, expected_closed) in cases.items():
            with self.subTest(failure=failure):
                identity, _pidfd, path_open, _ioctl, close, _events = (
                    self._run_probe(faults)
                )
                self.assertIsNone(identity)
                path_open.assert_not_called()
                closed = [call.args[0] for call in close.call_args_list]
                self.assertCountEqual(closed, expected_closed)
                self.assertEqual(len(closed), len(set(closed)))

    def test_missing_pidfd_open_is_fail_closed_without_path_fallback(self) -> None:
        with (
            mock.patch.object(process_priority.os, "pidfd_open", None),
            mock.patch.object(process_priority.os, "open") as path_open,
        ):
            self.assertIsNone(process_priority.initial_namespace_identity())
        path_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
