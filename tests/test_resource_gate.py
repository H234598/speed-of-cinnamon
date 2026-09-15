from __future__ import annotations

import io
import json
import os
import signal
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import process_priority, resource_gate


GIB = 1024**3
_DEFAULT_SNAPSHOT = object()


def initial_namespace(
    *,
    user_inode: int = 0xEFFFFFFD,
    pid_inode: int = 0xEFFFFFFC,
    mount_inode: int = 0xEFFFFFF8,
    cgroup_inode: int = 0xEFFFFFFB,
) -> process_priority.InitialNamespaceIdentity:
    return process_priority.InitialNamespaceIdentity(
        user=(1, user_inode),
        pid=(1, pid_inode),
        mount=(1, mount_inode),
        cgroup=(1, cgroup_inode),
    )


def resource_snapshot(
    *,
    effective_cpus: frozenset[int] = frozenset({0, 1, 2, 3}),
    online_cpus: frozenset[int] | None = None,
    cpu_quota_us: int | None = 400_000,
    cpu_period_us: int = 100_000,
    max_headroom: int | None = 8 * GIB,
    high_headroom: int | None = 8 * GIB,
) -> process_priority.CgroupResourceSnapshot:
    return process_priority.CgroupResourceSnapshot(
        effective_cpus=effective_cpus,
        online_cpus=online_cpus,
        cpu_quota_us=cpu_quota_us,
        cpu_period_us=cpu_period_us,
        memory_current_bytes=GIB,
        memory_max_bytes=16 * GIB if max_headroom is not None else None,
        memory_high_bytes=16 * GIB if high_headroom is not None else None,
        memory_max_headroom_bytes=max_headroom,
        memory_high_headroom_bytes=high_headroom,
    )


def system_files(
    *,
    loadavg: str = "1.00 1.20 1.60 1/100 1\n",
    available_kib: int = 9 * 1024 * 1024,
    total_kib: int = 32 * 1024 * 1024,
    cpu_psi: str = "some avg10=5.00 avg60=4.00 avg300=3.00 total=1\n",
    memory_psi: str = "full avg10=1.00 avg60=0.50 avg300=0.25 total=1\n",
    io_psi: str = "full avg10=1.00 avg60=0.50 avg300=0.25 total=1\n",
) -> dict[Path, str]:
    return {
        resource_gate._LOADAVG: loadavg,
        resource_gate._MEMINFO: (
            f"MemTotal: {total_kib} kB\n"
            f"MemAvailable: {available_kib} kB\n"
            "SwapTotal: 1048576 kB\n"
            "SwapFree: 524288 kB\n"
        ),
        resource_gate._PSI_CPU: cpu_psi,
        resource_gate._PSI_MEMORY: memory_psi,
        resource_gate._PSI_IO: io_psi,
    }


class ResourceGateTests(unittest.TestCase):
    def run_gate(
        self,
        *,
        snapshot: process_priority.CgroupResourceSnapshot | None | object = (
            _DEFAULT_SNAPSHOT
        ),
        files: dict[Path, str | None] | None = None,
        affinity: frozenset[int] = frozenset({0, 1, 2, 3}),
        argv: list[str] | None = None,
        bootstrap: bool = True,
        namespace_values: list[
            process_priority.InitialNamespaceIdentity | None
        ]
        | None = None,
    ) -> tuple[int, dict[str, object], str]:
        output = io.StringIO()
        file_values = system_files() if files is None else files
        with (
            mock.patch.object(
                resource_gate.process_priority,
                "ensure_resource_gate_priority_scope",
                return_value=bootstrap,
            ) as ensure_scope,
            mock.patch.object(
                resource_gate.process_priority,
                "current_cgroup_resource_snapshot",
                return_value=(
                    resource_snapshot()
                    if snapshot is _DEFAULT_SNAPSHOT
                    else snapshot
                ),
            ),
            mock.patch.object(
                resource_gate,
                "_read_system_file",
                side_effect=lambda path, **_kwargs: file_values.get(path),
            ),
            mock.patch.object(
                resource_gate.os,
                "sched_getaffinity",
                side_effect=[affinity, affinity],
            ),
            mock.patch.object(
                resource_gate.process_priority,
                "initial_namespace_identity",
                side_effect=(
                    [initial_namespace(), initial_namespace()]
                    if namespace_values is None
                    else namespace_values
                ),
            ),
        ):
            code = resource_gate.main([] if argv is None else argv, stdout=output)
        raw = output.getvalue()
        payload = json.loads(
            raw,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            object_pairs_hook=lambda pairs: (
                dict(pairs)
                if len(dict(pairs)) == len(pairs)
                else (_ for _ in ()).throw(ValueError("duplicate"))
            ),
        )
        ensure_scope.assert_called_once_with([] if argv is None else argv)
        return code, payload, raw

    def test_exact_thresholds_are_green_and_swap_is_warning_only(self) -> None:
        code, payload, raw = self.run_gate()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "green")
        self.assertEqual(payload["reasons"], [])
        self.assertEqual(payload["warnings"], ["swap_in_use"])
        self.assertTrue(all(payload["checks"].values()))
        self.assertLessEqual(len(raw.encode("utf-8")), resource_gate.MAX_JSON_BYTES)

    def test_each_gate_failure_defers(self) -> None:
        cases = {
            "load": ({"files": system_files(loadavg="1.01 1.20 1.60\n")}, "load"),
            "memory_bytes": (
                {"files": system_files(available_kib=(8 * 1024 * 1024) - 1)},
                "memory_available",
            ),
            "memory_ratio": (
                {
                    "files": system_files(
                        available_kib=9 * 1024 * 1024,
                        total_kib=40 * 1024 * 1024,
                    )
                },
                "memory_available",
            ),
            "psi": (
                {
                    "files": system_files(
                        cpu_psi=(
                            "some avg10=5.01 avg60=4.00 "
                            "avg300=3.00 total=1\n"
                        )
                    )
                },
                "psi",
            ),
            "memory_psi": (
                {
                    "files": system_files(
                        memory_psi=(
                            "full avg10=1.01 avg60=0.50 "
                            "avg300=0.25 total=1\n"
                        )
                    )
                },
                "psi",
            ),
            "io_psi": (
                {
                    "files": system_files(
                        io_psi=(
                            "full avg10=1.01 avg60=0.50 "
                            "avg300=0.25 total=1\n"
                        )
                    )
                },
                "psi",
            ),
            "affinity": (
                {"affinity": frozenset({0, 1, 2, 4})},
                "affinity_matches_cpuset",
            ),
            "quota": (
                {"snapshot": resource_snapshot(cpu_quota_us=399_999)},
                "cpu_quota",
            ),
            "memory_max": (
                {"snapshot": resource_snapshot(max_headroom=(8 * GIB) - 1)},
                "memory_headroom",
            ),
            "memory_high": (
                {"snapshot": resource_snapshot(high_headroom=(8 * GIB) - 1)},
                "memory_headroom",
            ),
        }
        for name, (arguments, failed_check) in cases.items():
            with self.subTest(name=name):
                code, payload, _raw = self.run_gate(**arguments)
                self.assertEqual(code, 75)
                self.assertEqual(payload["status"], "defer")
                self.assertFalse(payload["checks"][failed_check])

    def test_full_online_is_optional_and_strict(self) -> None:
        snapshot = resource_snapshot(online_cpus=frozenset({0, 1, 2, 3, 4}))
        code, payload, _raw = self.run_gate(
            snapshot=snapshot,
            argv=["--require-full-online"],
        )

        self.assertEqual(code, 75)
        self.assertFalse(payload["checks"]["full_online"])
        self.assertEqual(payload["metrics"]["online_cpus"], 5)

        code, payload, _raw = self.run_gate(
            snapshot=resource_snapshot(
                online_cpus=frozenset({0, 1, 2, 3})
            ),
            argv=["--require-full-online"],
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["checks"]["full_online"])

    def test_unlimited_cgroup_limits_do_not_invent_headroom_failure(self) -> None:
        code, payload, _raw = self.run_gate(
            snapshot=resource_snapshot(
                cpu_quota_us=None,
                max_headroom=None,
                high_headroom=None,
            )
        )

        self.assertEqual(code, 0)
        self.assertTrue(payload["checks"]["cpu_quota"])
        self.assertTrue(payload["checks"]["memory_headroom"])

    def test_missing_or_malformed_mandatory_evidence_defers(self) -> None:
        malformed = {
            "missing": {**system_files(), resource_gate._MEMINFO: None},
            "load_nan": {**system_files(), resource_gate._LOADAVG: "nan 0 0\n"},
            "duplicate_mem": {
                **system_files(),
                resource_gate._MEMINFO: system_files()[resource_gate._MEMINFO]
                + "MemAvailable: 1 kB\n",
            },
            "psi_missing_full": {
                **system_files(),
                resource_gate._PSI_MEMORY: "some avg10=0.00 total=1\n",
            },
        }
        for name, files in malformed.items():
            with self.subTest(name=name):
                code, payload, raw = self.run_gate(files=files)
                self.assertEqual(code, 75)
                self.assertEqual(payload["status"], "defer")
                self.assertNotIn("NaN", raw)

        code, payload, _raw = self.run_gate(snapshot=None)
        self.assertEqual(code, 75)
        self.assertEqual(payload["reasons"], ["resource_boundary_unavailable"])

    def test_secure_reader_rejects_symlink_non_ascii_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target"
            link = root / "link"
            target.write_text("safe\n", encoding="ascii")
            link.symlink_to(target)
            self.assertIsNone(resource_gate._read_system_file(link, max_bytes=64))
            target.write_bytes(b"\xff")
            self.assertIsNone(resource_gate._read_system_file(target, max_bytes=64))
            target.write_bytes(b"x" * 65)
            self.assertIsNone(resource_gate._read_system_file(target, max_bytes=64))
            self.assertIsNone(resource_gate._read_system_file(root, max_bytes=64))

    def test_secure_reader_sets_nonblock_before_fstat(self) -> None:
        def reject_open(_path: object, flags: int) -> int:
            self.assertTrue(flags & os.O_NONBLOCK)
            raise OSError("unavailable")

        with (
            mock.patch.object(
                process_priority.os,
                "open",
                side_effect=reject_open,
            ),
            mock.patch.object(process_priority.os, "fstat") as fstat,
        ):
            self.assertIsNone(
                resource_gate._read_system_file(Path("/proc/fifo"), max_bytes=64)
            )
        fstat.assert_not_called()

    def test_fifo_without_writer_returns_without_blocking(self) -> None:
        if not hasattr(signal, "setitimer") or not hasattr(os, "O_NONBLOCK"):
            self.skipTest("Linux nonblocking FIFO probe is unavailable")

        class BlockedFifoRead(RuntimeError):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "probe"
            os.mkfifo(fifo, 0o600)
            previous = signal.getsignal(signal.SIGALRM)
            signal.signal(
                signal.SIGALRM,
                lambda _signal, _frame: (_ for _ in ()).throw(BlockedFifoRead()),
            )
            signal.setitimer(signal.ITIMER_REAL, 1.0)
            try:
                result = resource_gate._read_system_file(fifo, max_bytes=64)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0.0)
                signal.signal(signal.SIGALRM, previous)

        self.assertIsNone(result)

    def test_initial_namespace_must_be_available_and_stable(self) -> None:
        stable = initial_namespace()
        changes = {
            "user_changed": initial_namespace(user_inode=0xEFFFFFF0),
            "pid_changed": initial_namespace(pid_inode=0xEFFFFFF0),
            "mount_changed": initial_namespace(mount_inode=0xEFFFFFF0),
            "cgroup_changed": initial_namespace(cgroup_inode=0xEFFFFFF0),
        }
        cases = [("unavailable", [None], "initial_namespace_unavailable")]
        cases.extend(
            (name, [stable, changed], "initial_namespace_changed")
            for name, changed in changes.items()
        )
        for name, values, reason in cases:
            with self.subTest(name=name):
                code, payload, _raw = self.run_gate(namespace_values=values)
                self.assertEqual(code, 75)
                self.assertEqual(payload["status"], "defer")
                self.assertEqual(payload["reasons"], [reason])

    def test_probe_never_spawns_writes_or_changes_affinity(self) -> None:
        with (
            mock.patch.object(process_priority.subprocess, "Popen") as popen,
            mock.patch.object(process_priority.os, "sched_setaffinity") as set_affinity,
        ):
            code, payload, _raw = self.run_gate()

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "green")
        popen.assert_not_called()
        set_affinity.assert_not_called()

    def test_payload_is_identifier_free_and_finite(self) -> None:
        code, payload, raw = self.run_gate()

        self.assertEqual(code, 0)
        forbidden = ("hostname", "username", "model", "path", "cgroup", "unit", "pid")
        lowered = raw.casefold()
        self.assertFalse(any(item in lowered for item in forbidden))
        self.assertNotIn("NaN", raw)
        self.assertNotIn("Infinity", raw)
        self.assertEqual(payload["schema_version"], 1)
        with self.assertRaises(ValueError):
            resource_gate._render_payload({"metric": float("nan")})
        with self.assertRaises(ValueError):
            resource_gate._render_payload(
                {"padding": "x" * resource_gate.MAX_JSON_BYTES}
            )

    def test_unverified_bootstrap_defers_without_probing(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(
                resource_gate.process_priority,
                "ensure_resource_gate_priority_scope",
                return_value=False,
            ),
            mock.patch.object(
                resource_gate.process_priority,
                "current_cgroup_resource_snapshot",
            ) as snapshot,
            mock.patch.object(resource_gate, "_read_system_file") as reader,
            mock.patch.object(resource_gate.os, "sched_getaffinity") as affinity,
        ):
            code = resource_gate.main([], stdout=output)

        self.assertEqual(code, 75)
        self.assertEqual(json.loads(output.getvalue())["reasons"], ["high_qos_unavailable"])
        snapshot.assert_not_called()
        reader.assert_not_called()
        affinity.assert_not_called()

        output = io.StringIO()
        with (
            mock.patch.object(
                resource_gate.process_priority,
                "ensure_resource_gate_priority_scope",
                side_effect=process_priority.PriorityScopeError("private"),
            ),
            mock.patch.object(resource_gate, "_build_payload") as build,
        ):
            code = resource_gate.main([], stdout=output)
        self.assertEqual(code, 75)
        self.assertNotIn("private", output.getvalue())
        build.assert_not_called()

    def test_cli_render_and_stdout_exit_codes_are_fixed(self) -> None:
        output = io.StringIO()
        self.assertEqual(resource_gate.main(["--unknown"], stdout=output), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

        output = io.StringIO()
        with (
            mock.patch.object(
                resource_gate.process_priority,
                "ensure_resource_gate_priority_scope",
                return_value=True,
            ),
            mock.patch.object(
                resource_gate,
                "_build_payload",
                side_effect=RuntimeError("private"),
            ),
        ):
            self.assertEqual(resource_gate.main([], stdout=output), 70)
        self.assertNotIn("private", output.getvalue())

        output = io.StringIO()
        with (
            mock.patch.object(
                resource_gate.process_priority,
                "ensure_resource_gate_priority_scope",
                return_value=True,
            ),
            mock.patch.object(resource_gate, "_build_payload", return_value={}),
            mock.patch.object(
                resource_gate,
                "_render_payload",
                side_effect=ValueError("private"),
            ),
        ):
            self.assertEqual(resource_gate.main([], stdout=output), 70)
        self.assertEqual(
            json.loads(output.getvalue())["error"],
            "internal_invariant",
        )

        failing = mock.Mock()
        failing.write.side_effect = OSError("private")
        self.assertEqual(resource_gate.main(["--unknown"], stdout=failing), 74)


if __name__ == "__main__":
    unittest.main()
