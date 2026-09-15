from __future__ import annotations

import contextlib
import copy
import dataclasses
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import ct2_benchmark, ct2_benchmark_worker


class Ctranslate2BenchmarkWorkerTests(unittest.TestCase):
    _NONCE = "b" * 64
    _SITE_PACKAGES = "/opt/soc/site-packages"
    _ARGV = [
        "--probe-runtime",
        "--site-packages",
        _SITE_PACKAGES,
        "--nonce",
        _NONCE,
    ]

    @staticmethod
    def _isolated_flags(*, isolated: bool = True) -> object:
        value = 1 if isolated else 0
        return types.SimpleNamespace(
            isolated=value,
            ignore_environment=value,
            no_user_site=value,
            no_site=value,
            safe_path=isolated,
        )

    def _run_main(
        self,
        *,
        argv: list[str] | None = None,
        isolated: bool = True,
        ct2: object | None = None,
        faster_whisper: object | None = None,
        import_error: BaseException | None = None,
        write_effect: object | None = None,
    ) -> tuple[int, bytes, list[str], str, str, mock.Mock]:
        ct2_module = ct2 or types.SimpleNamespace(
            __version__="4.7.2",
            get_supported_compute_types=lambda device: {"int8", "float32"},
        )
        faster_module = faster_whisper or types.SimpleNamespace(__version__="1.2.1")
        imports: list[str] = []
        output = bytearray()

        def import_module(name: str) -> object:
            imports.append(name)
            self.assertEqual(sys.path[-1], self._SITE_PACKAGES)
            self.assertEqual(sys.path.count(self._SITE_PACKAGES), 1)
            if import_error is not None:
                raise import_error
            if name == "ctranslate2":
                return ct2_module
            if name == "faster_whisper":
                return faster_module
            raise AssertionError("unexpected import")

        def write(_descriptor: int, value: object) -> int:
            data = bytes(value)
            output.extend(data)
            return len(data)

        writer = mock.Mock(
            side_effect=write if write_effect is None else write_effect
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(
                ct2_benchmark_worker.sys,
                "flags",
                self._isolated_flags(isolated=isolated),
            ),
            mock.patch.object(
                ct2_benchmark_worker.importlib,
                "import_module",
                side_effect=import_module,
            ),
            mock.patch.object(ct2_benchmark_worker.os, "write", writer),
            mock.patch.object(
                ct2_benchmark_worker.platform,
                "python_version",
                return_value="3.13.7",
            ),
            mock.patch.object(
                ct2_benchmark_worker.platform,
                "python_implementation",
                return_value="CPython",
            ),
            mock.patch.object(
                ct2_benchmark_worker.sysconfig,
                "get_config_var",
                return_value="cpython-313-x86_64-linux-gnu",
            ),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = ct2_benchmark_worker.main(self._ARGV if argv is None else argv)
        return result, bytes(output), imports, stdout.getvalue(), stderr.getvalue(), writer

    def test_cli_and_isolation_precede_third_party_imports(self) -> None:
        invalid = (
            [],
            self._ARGV + ["extra"],
            [*self._ARGV[:2], "relative/site", *self._ARGV[3:]],
            [*self._ARGV[:4], "B" * 63],
            [
                "--probe-runtime",
                "--site-packages",
                self._SITE_PACKAGES,
                "--result-token",
                "a" * 64,
                "--nonce",
                self._NONCE,
            ],
        )
        for argv in invalid:
            with self.subTest(argv_length=len(argv)):
                result, output, imports, stdout, stderr, writer = self._run_main(
                    argv=argv
                )
                self.assertEqual(result, 65)
                self.assertEqual(output, b"")
                self.assertEqual(imports, [])
                self.assertEqual((stdout, stderr), ("", ""))
                writer.assert_not_called()

        result, output, imports, stdout, stderr, writer = self._run_main(
            isolated=False
        )
        self.assertEqual(result, 65)
        self.assertEqual(imports, [])
        self.assertEqual((stdout, stderr), ("", ""))
        writer.assert_called_once()
        self.assertEqual(
            json.loads(output),
            {
                "error_code": "python-isolation",
                "nonce": self._NONCE,
                "schema_version": 1,
                "status": "error",
            },
        )

        required = {
            "isolated": 1,
            "ignore_environment": 1,
            "no_user_site": 1,
            "no_site": 1,
            "safe_path": True,
        }
        for field in required:
            flags = dict(required)
            flags[field] = False if field == "safe_path" else 0
            with (
                self.subTest(flag=field),
                mock.patch.object(
                    ct2_benchmark_worker.sys,
                    "flags",
                    types.SimpleNamespace(**flags),
                ),
            ):
                self.assertFalse(
                    ct2_benchmark_worker._isolated_python_start_is_valid()
                )

    def test_success_writes_one_canonical_observation_to_stdout(self) -> None:
        original_path = list(sys.path)

        def compute_types(device: str) -> set[str]:
            self.assertEqual(device, "cpu")
            return {"int8", "float32"}

        ct2 = types.SimpleNamespace(
            __version__="4.7.2",
            get_supported_compute_types=compute_types,
        )
        result, output, imports, stdout, stderr, writer = self._run_main(ct2=ct2)

        self.assertEqual(result, 0)
        self.assertEqual(imports, ["ctranslate2", "faster_whisper"])
        self.assertEqual(sys.path, original_path)
        self.assertEqual((stdout, stderr), ("", ""))
        writer.assert_called_once()
        self.assertEqual(writer.call_args.args[0], 1)
        self.assertFalse(output.endswith(b"\n"))
        self.assertLessEqual(len(output), ct2_benchmark_worker.MAX_RESULT_BYTES)
        payload = json.loads(output)
        self.assertEqual(
            payload,
            {
                "mode": "probe-runtime",
                "nonce": self._NONCE,
                "runtime": {
                    "abi": "cpython-313-x86_64-linux-gnu",
                    "ctranslate2_version": "4.7.2",
                    "faster_whisper_version": "1.2.1",
                    "implementation": "CPython",
                    "python_version": "3.13.7",
                    "supported_cpu_compute_types": ["float32", "int8"],
                },
                "schema_version": 1,
                "status": "ok",
            },
        )
        self.assertEqual(output, ct2_benchmark_worker._canonical_json(payload))

    def test_runtime_failures_write_only_safe_error_payloads(self) -> None:
        unsafe_ct2 = types.SimpleNamespace(
            __version__="private\nvalue",
            get_supported_compute_types=lambda _device: ["int8"],
        )
        cases = (
            ({"ct2": unsafe_ct2}, "runtime-values"),
            ({"import_error": SystemExit("private /secret/path")}, "runtime-import"),
            (
                {
                    "ct2": types.SimpleNamespace(
                        __version__="4.7.2",
                        get_supported_compute_types=lambda _device: ["int8", "int8"],
                    )
                },
                "runtime-values",
            ),
        )
        for arguments, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                result, output, _imports, stdout, stderr, _writer = self._run_main(
                    **arguments
                )
                self.assertEqual(result, 65)
                self.assertEqual((stdout, stderr), ("", ""))
                payload = json.loads(output)
                self.assertEqual(
                    payload,
                    {
                        "error_code": expected_code,
                        "nonce": self._NONCE,
                        "schema_version": 1,
                        "status": "error",
                    },
                )
                self.assertNotIn("private", output.decode("ascii"))
                self.assertNotIn("secret", output.decode("ascii"))

    def test_write_all_retries_eintr_and_short_writes(self) -> None:
        writer = mock.Mock(side_effect=[InterruptedError(), 2, 1, 3])
        with mock.patch.object(ct2_benchmark_worker.os, "write", writer):
            ct2_benchmark_worker._write_all(b"abcdef")
        self.assertEqual(
            writer.call_args_list,
            [
                mock.call(1, b"abcdef"),
                mock.call(1, b"abcdef"),
                mock.call(1, b"cdef"),
                mock.call(1, b"def"),
            ],
        )

    def test_write_zero_invalid_count_and_error_exit_safely(self) -> None:
        for effect in (0, 7, OSError("private write failure")):
            with (
                self.subTest(effect=type(effect).__name__),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "write",
                    side_effect=effect if isinstance(effect, BaseException) else None,
                    return_value=effect if isinstance(effect, int) else None,
                ),
                self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised,
            ):
                ct2_benchmark_worker._write_all(b"abc")
            self.assertEqual(raised.exception.code, "internal")

        result, output, _imports, stdout, stderr, _writer = self._run_main(
            write_effect=OSError("private write failure")
        )
        self.assertEqual(result, 65)
        self.assertEqual(output, b"")
        self.assertEqual((stdout, stderr), ("", ""))

    def test_worker_direct_surface_has_no_socket_metadata_model_or_subprocess(self) -> None:
        namespace = ct2_benchmark_worker.__dict__
        for forbidden in (
            "importlib_metadata",
            "socket",
            "WhisperModel",
            "subprocess",
            "urllib",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, namespace)


class Ctranslate2BenchmarkAttestationWorkerTests(unittest.TestCase):
    _NONCE = "c" * 64
    _ARGV = ["--attest-artifact", "--nonce", _NONCE]

    @staticmethod
    def _encoded(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    @classmethod
    def _request(
        cls,
        root: Path,
        contents: dict[str, tuple[bytes, bool]],
        **changes: object,
    ) -> bytes:
        files = [
            {
                "executable": executable,
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for path, (content, executable) in sorted(contents.items())
        ]
        manifest = {
            "artifact_id": "artifact-main",
            "files": files,
            "kind": "clips",
            "schema_version": 1,
        }
        manifest_bytes = cls._encoded(manifest)
        payload: dict[str, object] = {
            "manifest": manifest,
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "mode": "attest-artifact",
            "nonce": cls._NONCE,
            "root": str(root),
            "schema_version": 1,
        }
        payload.update(changes)
        return cls._encoded(payload)

    @staticmethod
    def _write_root(
        root: Path,
        contents: dict[str, tuple[bytes, bool]],
    ) -> None:
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        for relative, (content, executable) in contents.items():
            target = root / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.parent.chmod(0o700)
            target.write_bytes(content)
            target.chmod(0o700 if executable else 0o600)

    def _run(
        self,
        request: bytes,
        *,
        argv: list[str] | None = None,
    ) -> tuple[int, bytes, mock.Mock]:
        chunks = [request, b""]
        output = bytearray()

        def write(_descriptor: int, value: object) -> int:
            data = bytes(value)
            output.extend(data)
            return len(data)

        writer = mock.Mock(side_effect=write)
        isolated = types.SimpleNamespace(
            isolated=1,
            ignore_environment=1,
            no_user_site=1,
            no_site=1,
            safe_path=True,
        )
        with (
            mock.patch.object(ct2_benchmark_worker.sys, "flags", isolated),
            mock.patch.object(
                ct2_benchmark_worker.os,
                "read",
                side_effect=lambda _fd, _size: chunks.pop(0),
            ),
            mock.patch.object(ct2_benchmark_worker.os, "write", writer),
            mock.patch.object(
                ct2_benchmark_worker.importlib,
                "import_module",
                side_effect=AssertionError("third-party import"),
            ) as importer,
        ):
            result = ct2_benchmark_worker.main(self._ARGV if argv is None else argv)
        importer.assert_not_called()
        return result, bytes(output), writer

    def test_attestation_mode_hashes_both_walks_and_returns_canonical_result(
        self,
    ) -> None:
        contents = {
            "payload/data.bin": (b"payload", False),
            "payload/run.bin": (b"run", True),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            self._write_root(root, contents)
            request = self._request(root, contents)
            with mock.patch.object(
                ct2_benchmark_worker,
                "_artifact_file_digest",
                wraps=ct2_benchmark_worker._artifact_file_digest,
            ) as digest:
                returncode, output, writer = self._run(request)

        self.assertEqual(returncode, 0)
        self.assertEqual(digest.call_count, 4)
        payload = json.loads(output)
        self.assertEqual(
            payload,
            {
                "attestation": {
                    "artifact_id": "artifact-main",
                    "file_count": 2,
                    "manifest_sha256": hashlib.sha256(
                        self._encoded(json.loads(request)["manifest"])
                    ).hexdigest(),
                    "total_bytes": 10,
                },
                "mode": "attest-artifact",
                "nonce": self._NONCE,
                "schema_version": 1,
                "status": "ok",
            },
        )
        self.assertEqual(output, self._encoded(payload))
        writer.assert_called_once()

    def test_attestation_requires_exact_root_directory_and_file_modes(self) -> None:
        contents = {"payload/data.bin": (b"payload", False)}
        mutations = (
            ("root", 0o750, "artifact-root-invalid"),
            ("directory", 0o750, "artifact-tree-mismatch"),
            ("plain", 0o640, "artifact-tree-mismatch"),
            ("executable", 0o710, "artifact-tree-mismatch"),
        )
        for mutation, mode, expected in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "artifact"
                selected = (
                    {"payload/run.bin": (b"payload", True)}
                    if mutation == "executable"
                    else contents
                )
                self._write_root(root, selected)
                target = {
                    "root": root,
                    "directory": root / "payload",
                    "plain": root / "payload/data.bin",
                    "executable": root / "payload/run.bin",
                }[mutation]
                target.chmod(mode)
                returncode, output, _writer = self._run(self._request(root, selected))
                self.assertEqual(returncode, 65)
                self.assertEqual(json.loads(output)["error_code"], expected)

    def test_attestation_second_walk_same_size_mutation_is_changed(self) -> None:
        contents = {"payload.bin": (b"before", False)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            self._write_root(root, contents)
            target = root / "payload.bin"
            real_digest = ct2_benchmark_worker._artifact_file_digest
            digest_calls = 0

            def mutate_between_walks(descriptor: int, size: int) -> str:
                nonlocal digest_calls
                digest_calls += 1
                if digest_calls == 2:
                    target.write_bytes(b"after!")
                    target.chmod(0o600)
                return real_digest(descriptor, size)

            with mock.patch.object(
                ct2_benchmark_worker,
                "_artifact_file_digest",
                side_effect=mutate_between_walks,
            ):
                returncode, output, _writer = self._run(
                    self._request(root, contents)
                )

        self.assertEqual(returncode, 65)
        self.assertEqual(json.loads(output)["error_code"], "artifact-tree-changed")

    def test_attestation_rejects_missing_extra_symlink_and_hardlink(self) -> None:
        contents = {"payload/data.bin": (b"payload", False)}
        mutations = ("missing", "extra", "root-symlink", "leaf-symlink", "hardlink")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root = base / "artifact"
                self._write_root(root, contents)
                request = self._request(root, contents)
                target = root / "payload/data.bin"
                if mutation == "missing":
                    target.unlink()
                elif mutation == "extra":
                    extra = root / "extra"
                    extra.write_bytes(b"extra")
                    extra.chmod(0o600)
                elif mutation == "root-symlink":
                    link = base / "artifact-link"
                    link.symlink_to(root, target_is_directory=True)
                    request = self._request(link, contents)
                elif mutation == "leaf-symlink":
                    outside = base / "outside"
                    outside.write_bytes(b"payload")
                    outside.chmod(0o600)
                    target.unlink()
                    target.symlink_to(outside)
                else:
                    outside = base / "outside"
                    target.rename(outside)
                    os.link(outside, target)

                returncode, output, _writer = self._run(request)

                self.assertEqual(returncode, 65)
                expected = (
                    "artifact-root-invalid"
                    if mutation == "root-symlink"
                    else "artifact-tree-mismatch"
                )
                self.assertEqual(json.loads(output)["error_code"], expected)

    def test_attestation_rebind_detects_root_replacement(self) -> None:
        contents = {"payload.bin": (b"payload", False)}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "artifact"
            old_root = base / "old-artifact"
            self._write_root(root, contents)
            real_open = ct2_benchmark_worker._open_bound_root
            calls = 0

            def replace_before_rebind(path: str, *, error_code: str) -> int:
                nonlocal calls
                calls += 1
                if calls == 2:
                    root.rename(old_root)
                    self._write_root(root, contents)
                return real_open(path, error_code=error_code)

            with mock.patch.object(
                ct2_benchmark_worker,
                "_open_bound_root",
                side_effect=replace_before_rebind,
            ):
                returncode, output, _writer = self._run(
                    self._request(root, contents)
                )

        self.assertEqual(returncode, 65)
        self.assertEqual(json.loads(output)["error_code"], "artifact-tree-changed")
        self.assertEqual(calls, 2)

    def test_attestation_retries_partial_pread_and_closes_fds_on_failure(self) -> None:
        contents = {"payload.bin": (b"abcdef", False)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            self._write_root(root, contents)
            request = self._request(root, contents)
            real_pread = os.pread
            reads = 0

            def partial_pread(fd: int, count: int, offset: int) -> bytes:
                nonlocal reads
                reads += 1
                if reads == 1:
                    raise InterruptedError
                return real_pread(fd, min(count, 2), offset)

            with mock.patch.object(
                ct2_benchmark_worker.os,
                "pread",
                side_effect=partial_pread,
            ):
                returncode, output, _writer = self._run(request)
            self.assertEqual(returncode, 0)
            self.assertEqual(json.loads(output)["status"], "ok")
            self.assertGreaterEqual(reads, 9)

            real_open = os.open
            opened: list[int] = []

            def tracking_open(*args: object, **kwargs: object) -> int:
                descriptor = real_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            with (
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "open",
                    side_effect=tracking_open,
                ),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_artifact_file_digest",
                    side_effect=RuntimeError("private"),
                ),
            ):
                returncode, output, _writer = self._run(request)
            self.assertEqual(returncode, 65)
            self.assertEqual(
                json.loads(output)["error_code"],
                "artifact-attestation-failed",
            )
            self.assertNotIn("private", output.decode("ascii"))
            for descriptor in set(opened):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_attestation_oversized_request_and_system_exit_are_redacted(self) -> None:
        oversized = b"x" * (ct2_benchmark_worker.MAX_ATTESTATION_REQUEST_BYTES + 1)
        returncode, output, _writer = self._run(oversized)
        self.assertEqual(returncode, 65)
        self.assertEqual(
            json.loads(output)["error_code"],
            "artifact-attestation-failed",
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            contents = {"payload.bin": (b"payload", False)}
            self._write_root(root, contents)
            with mock.patch.object(
                ct2_benchmark_worker,
                "_attest_artifact",
                side_effect=SystemExit("private"),
            ):
                returncode, output, _writer = self._run(
                    self._request(root, contents)
                )
        self.assertEqual(returncode, 65)
        self.assertEqual(
            json.loads(output)["error_code"],
            "artifact-attestation-failed",
        )
        self.assertNotIn("private", output.decode("ascii"))

    def test_open_bound_root_preserves_interrupt_when_cleanup_close_fails(
        self,
    ) -> None:
        root_descriptor = os.open(
            "/",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        real_close = os.close

        def open_then_interrupt(
            path: str,
            _flags: int,
            *,
            dir_fd: int | None = None,
        ) -> int:
            if path == "/" and dir_fd is None:
                return root_descriptor
            raise expected_interrupt

        def close_then_fail(descriptor: int) -> None:
            real_close(descriptor)
            raise OSError("private close failure")

        expected_interrupt = KeyboardInterrupt()
        closer = mock.Mock(side_effect=close_then_fail)
        interrupt: KeyboardInterrupt | None = None
        with (
            mock.patch.object(
                ct2_benchmark_worker.os,
                "open",
                side_effect=open_then_interrupt,
            ),
            mock.patch.object(
                ct2_benchmark_worker.os,
                "close",
                closer,
            ),
        ):
            try:
                ct2_benchmark_worker._open_bound_root(
                    "/child",
                    error_code="artifact-root-invalid",
                )
            except KeyboardInterrupt as exc:
                interrupt = exc

        self.assertIs(interrupt, expected_interrupt)
        closer.assert_called_once_with(root_descriptor)
        with self.assertRaises(OSError):
            os.fstat(root_descriptor)

    def test_attestation_rejects_total_over_one_gib_before_tree_open(self) -> None:
        payload = json.loads(
            self._request(Path("/not-opened"), {"a": (b"", False)})
        )
        payload["manifest"]["files"] = [
            {
                "executable": False,
                "path": "a",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "size": 1 << 30,
            },
            {
                "executable": False,
                "path": "b",
                "sha256": hashlib.sha256(b"").hexdigest(),
                "size": 1,
            },
        ]
        manifest_bytes = self._encoded(payload["manifest"])
        payload["manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        opener = mock.Mock(side_effect=AssertionError("filesystem traversal"))
        with mock.patch.object(
            ct2_benchmark_worker,
            "_open_bound_root",
            opener,
        ):
            returncode, output, _writer = self._run(self._encoded(payload))

        self.assertEqual(returncode, 65)
        self.assertEqual(
            json.loads(output)["error_code"],
            "artifact-attestation-failed",
        )
        opener.assert_not_called()

    def test_attestation_rejects_fifo_without_blocking(self) -> None:
        contents = {"payload.fifo": (b"", False)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            fifo = root / "payload.fifo"
            os.mkfifo(fifo, mode=0o600)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    str(Path(ct2_benchmark_worker.__file__).resolve()),
                    *self._ARGV,
                ],
                input=self._request(root, contents),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=2,
            )

        self.assertEqual(result.returncode, 65)
        self.assertEqual(result.stderr, b"")
        self.assertEqual(
            json.loads(result.stdout)["error_code"],
            "artifact-tree-mismatch",
        )

    def test_file_snapshot_rejects_wrong_owner_and_device(self) -> None:
        declaration = ct2_benchmark_worker._ArtifactFile(
            "payload.bin",
            hashlib.sha256(b"payload").hexdigest(),
            7,
            False,
        )
        baseline = {
            "device": 7,
            "inode": 11,
            "mode": stat.S_IFREG | 0o600,
            "link_count": 1,
            "user_id": 1000,
            "group_id": 1000,
            "size": 7,
            "modified_ns": 1,
            "changed_ns": 1,
        }
        for field, value in (("user_id", 1001), ("device", 8)):
            snapshot = ct2_benchmark_worker._StatSnapshot(
                **{**baseline, field: value}
            )
            with (
                self.subTest(field=field),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_snapshot",
                    return_value=snapshot,
                ),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_has_cloexec",
                    return_value=True,
                ),
                self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised,
            ):
                ct2_benchmark_worker._file_snapshot(
                    9,
                    declaration,
                    user_id=1000,
                    device=7,
                    error_code="artifact-tree-mismatch",
                )
            self.assertEqual(raised.exception.code, "artifact-tree-mismatch")

    def test_directory_enumeration_stops_after_expected_plus_one(self) -> None:
        class Entry:
            def __init__(self, name: str) -> None:
                self.name = name

        class PoisonEntry:
            @property
            def name(self) -> str:
                raise AssertionError("N+1 name must not be read")

        class EndlessEntries:
            def __init__(self) -> None:
                self.count = 0

            def __enter__(self) -> EndlessEntries:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self) -> EndlessEntries:
                return self

            def __next__(self) -> Entry | PoisonEntry:
                self.count += 1
                if self.count == 1:
                    return Entry("expected")
                return PoisonEntry()

        entries = EndlessEntries()
        with (
            mock.patch.object(
                ct2_benchmark_worker.os,
                "scandir",
                return_value=entries,
            ),
            self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised,
        ):
            ct2_benchmark_worker._directory_children(
                9,
                {"expected"},
                error_code="artifact-tree-mismatch",
            )
        self.assertEqual(raised.exception.code, "artifact-tree-mismatch")
        self.assertEqual(entries.count, 2)

    def test_attestation_request_contract_is_closed_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            contents = {"payload.bin": (b"payload", False)}
            self._write_root(root, contents)
            request = self._request(root, contents)
            invalid = (
                [*self._ARGV, "extra"],
                ["--attest-artifact", "--nonce", "D" * 64],
            )
            for argv in invalid:
                with self.subTest(argv=argv):
                    returncode, output, writer = self._run(request, argv=argv)
                    self.assertEqual(returncode, 65)
                    self.assertEqual(output, b"")
                    writer.assert_not_called()

            payload = json.loads(request)
            malformed = (
                self._encoded({**payload, "schema_version": True}),
                self._encoded({**payload, "nonce": "d" * 64}),
                request + b"\n",
            )
            for candidate in malformed:
                with self.subTest(candidate_length=len(candidate)):
                    returncode, output, _writer = self._run(candidate)
                    self.assertEqual(returncode, 65)
                    self.assertEqual(json.loads(output)["error_code"], "artifact-attestation-failed")
                    self.assertNotIn(str(root), output.decode("ascii"))


class Ctranslate2BenchmarkArmRequestParserTests(unittest.TestCase):
    _NONCES = ("1" * 64, "2" * 64)
    _DEFAULT_NONCE = object()

    @staticmethod
    def _encoded(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    @classmethod
    def _manifest(
        cls,
        artifact_id: str,
        kind: str,
        files: tuple[tuple[str, bytes, bool], ...],
    ) -> ct2_benchmark.ArtifactManifest:
        declarations = tuple(
            ct2_benchmark.ArtifactFileDeclaration(
                path=path,
                sha256=hashlib.sha256(contents).hexdigest(),
                size=len(contents),
                executable=executable,
            )
            for path, contents, executable in sorted(files)
        )
        payload = {
            "artifact_id": artifact_id,
            "files": [
                {
                    "executable": value.executable,
                    "path": value.path,
                    "sha256": value.sha256,
                    "size": value.size,
                }
                for value in declarations
            ],
            "kind": kind,
            "schema_version": 1,
        }
        reference = ct2_benchmark.ArtifactManifestReference(
            artifact_id=artifact_id,
            manifest_sha256=hashlib.sha256(cls._encoded(payload)).hexdigest(),
        )
        return ct2_benchmark.ArtifactManifest(
            reference=reference,
            kind=kind,
            files=declarations,
            schema_version=1,
        )

    @staticmethod
    def _reference_payload(
        reference: ct2_benchmark.ArtifactManifestReference,
    ) -> dict[str, str]:
        return {
            "id": reference.artifact_id,
            "manifest_sha256": reference.manifest_sha256,
        }

    def setUp(self) -> None:
        self.runtime_a = self._manifest(
            "runtime-a",
            "runtime",
            (("package-a.bin", b"runtime-a", False),),
        )
        self.runtime_b = self._manifest(
            "runtime-b",
            "runtime",
            (("package-b.bin", b"runtime-b", False),),
        )
        self.clips = self._manifest(
            "clips-set",
            "clips",
            (
                ("corpus-v1.json", b"{}", False),
                ("sprache/clip-\u00e4.raw", b"clip", False),
            ),
        )
        self.model = self._manifest(
            "model-one",
            "model",
            (("modell-\u00e4.bin", b"model", False),),
        )
        experiment = self._encoded(
            {
                "clips": self._reference_payload(self.clips.reference),
                "mode": "full",
                "models": [self._reference_payload(self.model.reference)],
                "pair_count": 5,
                "runtimes": {
                    "a": self._reference_payload(self.runtime_a.reference),
                    "b": self._reference_payload(self.runtime_b.reference),
                },
                "schema_version": 1,
            }
        )
        self.plan = ct2_benchmark.build_run_plan(experiment, seed=17)
        self.artifacts = (
            (Path("/srv/soc/runtime-a"), self.runtime_a),
            (Path("/srv/soc/runtime-b"), self.runtime_b),
            (Path("/srv/soc/clips-\u00e4"), self.clips),
            (Path("/srv/soc/model-\u00e4"), self.model),
        )
        self.specs = (
            ct2_benchmark._RuntimeSpec(
                interpreter="/usr/bin/python3",
                site_packages=str(self.artifacts[0][0]),
                expected_ctranslate2_version="4.7.2",
                expected_faster_whisper_version="1.2.1",
            ),
            ct2_benchmark._RuntimeSpec(
                interpreter="/usr/bin/python3",
                site_packages=str(self.artifacts[1][0]),
                expected_ctranslate2_version="4.8.1",
                expected_faster_whisper_version="1.2.1",
            ),
        )
        self.profile = ct2_benchmark.DecodeProfile(
            profile_schema_version=1,
            device="cpu",
            requested_compute_type="int8",
            cpu_threads=4,
            num_workers=1,
            language="de",
            task="transcribe",
            beam_size=5,
            temperature_milli=0,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=False,
            without_timestamps=False,
        )
        self.requests = ct2_benchmark.build_benchmark_pair_requests(
            self.plan,
            self.artifacts,
            self.specs,
            self.profile,
            model_block_index=0,
            phase_index=0,
            nonces=self._NONCES,
        )

    def _payload(self, arm: int = 0) -> dict[str, object]:
        return json.loads(self.requests[arm])

    def _rehash(self, payload: dict[str, object], domain: str) -> None:
        if domain == "run_plan":
            payload["run_plan_sha256"] = hashlib.sha256(
                self._encoded(payload["run_plan"])
            ).hexdigest()
        elif domain == "decode_profile":
            payload["decode_profile_sha256"] = hashlib.sha256(
                self._encoded(payload["decode_profile"])
            ).hexdigest()
        else:
            artifact = payload[domain]
            artifact["manifest_sha256"] = hashlib.sha256(
                self._encoded(artifact["manifest"])
            ).hexdigest()

    def _padded_object(
        self,
        value: dict[str, object],
        target_size: int,
    ) -> dict[str, object]:
        result = copy.deepcopy(value)
        result["padding"] = ""
        padding_size = target_size - len(self._encoded(result))
        self.assertGreaterEqual(padding_size, 0)
        result["padding"] = "x" * padding_size
        self.assertEqual(len(self._encoded(result)), target_size)
        return result

    def _manifest_payload_at_size(
        self,
        value: dict[str, object],
        target_size: int,
    ) -> dict[str, object]:
        result = copy.deepcopy(value)
        required_files = (
            [
                copy.deepcopy(
                    next(
                        item
                        for item in result["files"]
                        if item["path"] == "corpus-v1.json"
                    )
                )
            ]
            if result["kind"] == "clips"
            else []
        )
        generated = [
            {
                "executable": False,
                "path": f"payload/{index:04d}-",
                "sha256": "0" * 64,
                "size": 0,
            }
            for index in range(3000)
        ]
        result["files"] = [*required_files, *generated]
        remaining = target_size - len(self._encoded(result))
        self.assertGreaterEqual(remaining, 0)
        for declaration in generated:
            room = 512 - len(declaration["path"])
            added = min(room, remaining)
            declaration["path"] += "x" * added
            remaining -= added
            if remaining == 0:
                break
        self.assertEqual(remaining, 0)
        self.assertEqual(len(result["files"]), 3000 + len(required_files))
        self.assertEqual(len(self._encoded(result)), target_size)
        return result

    def _retarget_plan_reference(
        self,
        payload: dict[str, object],
        domain: str,
    ) -> None:
        artifact = payload[domain]
        reference = {
            "id": artifact["manifest"]["artifact_id"],
            "manifest_sha256": artifact["manifest_sha256"],
        }
        if domain == "runtime":
            runtime_id = reference["id"]
            key = next(
                key
                for key, value in payload["run_plan"]["runtimes"].items()
                if value["id"] == runtime_id
            )
            payload["run_plan"]["runtimes"][key] = reference
        elif domain == "model":
            payload["run_plan"]["model_blocks"][0]["model"] = reference
        else:
            payload["run_plan"]["clips"] = reference
        self._rehash(payload, "run_plan")

    def assertInvalid(
        self,
        data: object,
        *,
        nonce: object = _DEFAULT_NONCE,
    ) -> None:
        expected_nonce = self._NONCES[0] if nonce is self._DEFAULT_NONCE else nonce
        with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
            ct2_benchmark_worker._parse_benchmark_arm_request(
                data,
                expected_nonce=expected_nonce,
            )
        self.assertEqual(raised.exception.code, "benchmark-request-invalid")
        self.assertEqual(str(raised.exception), "benchmark-request-invalid")
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)

    def test_controller_requests_parse_both_phase_orders_and_compute_types(
        self,
    ) -> None:
        phases = self.plan.model_blocks[0].phases
        first_by_runtime = {
            phase.pair.runtime_order[0]: index
            for index, phase in enumerate(phases)
        }
        self.assertEqual(set(first_by_runtime), {"runtime-a", "runtime-b"})
        for compute_type in ("int8", "float32"):
            profile = dataclasses.replace(
                self.profile,
                requested_compute_type=compute_type,
            )
            for runtime_id, phase_index in first_by_runtime.items():
                requests = ct2_benchmark.build_benchmark_pair_requests(
                    self.plan,
                    self.artifacts,
                    self.specs,
                    profile,
                    model_block_index=0,
                    phase_index=phase_index,
                    nonces=self._NONCES,
                )
                for arm_index, request in enumerate(requests):
                    with self.subTest(
                        compute_type=compute_type,
                        runtime_id=runtime_id,
                        arm_index=arm_index,
                    ):
                        parsed = ct2_benchmark_worker._parse_benchmark_arm_request(
                            request,
                            expected_nonce=self._NONCES[arm_index],
                        )
                        phase = parsed.run_plan.model_blocks[0].phases[phase_index]
                        self.assertEqual(
                            parsed.runtime.manifest.reference.artifact_id,
                            phase.pair.runtime_order[arm_index],
                        )
                        self.assertEqual(
                            parsed.model.manifest.reference,
                            parsed.run_plan.model_blocks[0].model,
                        )
                        self.assertEqual(
                            parsed.clips.manifest.reference,
                            parsed.run_plan.clips,
                        )
                        self.assertEqual(
                            parsed.decode_profile.requested_compute_type,
                            compute_type,
                        )

    def test_controller_quick_request_retains_noneligible_schedule(self) -> None:
        experiment = self._encoded(
            {
                "clips": self._reference_payload(self.clips.reference),
                "mode": "quick",
                "models": [self._reference_payload(self.model.reference)],
                "pair_count": 1,
                "runtimes": {
                    "a": self._reference_payload(self.runtime_a.reference),
                    "b": self._reference_payload(self.runtime_b.reference),
                },
                "schema_version": 1,
            }
        )
        plan = ct2_benchmark.build_run_plan(experiment, seed=17)
        request = ct2_benchmark.build_benchmark_pair_requests(
            plan,
            self.artifacts,
            self.specs,
            self.profile,
            model_block_index=0,
            phase_index=1,
            nonces=self._NONCES,
        )[0]
        parsed = ct2_benchmark_worker._parse_benchmark_arm_request(
            request,
            expected_nonce=self._NONCES[0],
        )
        self.assertEqual(parsed.run_plan.mode, "quick")
        self.assertEqual(parsed.run_plan.pair_count, 1)
        self.assertFalse(parsed.run_plan.decision_eligibility_capable)
        self.assertEqual(len(parsed.run_plan.model_blocks[0].phases), 2)

    def test_wire_types_bounds_and_canonical_encoding_are_strict(self) -> None:
        candidates: tuple[object, ...] = (
            bytearray(self.requests[0]),
            b"",
            b"x" * (3_403_776 + 1),
            b" " + self.requests[0],
            self.requests[0].replace(
                b'{"clips":',
                b'{"clips":null,"clips":',
                1,
            ),
            b'{"value":NaN}',
            b'{"value":"\xc3\xa4"}',
        )
        for candidate in candidates:
            with self.subTest(kind=type(candidate).__name__, size=len(candidate)):
                self.assertInvalid(candidate)
        with mock.patch.object(
            ct2_benchmark_worker,
            "MAX_ARM_REQUEST_BYTES",
            len(self.requests[0]),
        ):
            ct2_benchmark_worker._parse_benchmark_arm_request(
                self.requests[0], expected_nonce=self._NONCES[0]
            )
        with mock.patch.object(
            ct2_benchmark_worker,
            "MAX_ARM_REQUEST_BYTES",
            len(self.requests[0]) - 1,
        ):
            self.assertInvalid(self.requests[0])

    def test_unknown_and_missing_fields_at_each_structure_family_are_rejected(
        self,
    ) -> None:
        families = (
            ("top", lambda value: value, None, "mode"),
            ("plan", lambda value: value["run_plan"], "run_plan", "mode"),
            (
                "reference",
                lambda value: value["run_plan"]["runtimes"]["a"],
                "run_plan",
                "id",
            ),
            (
                "model-block",
                lambda value: value["run_plan"]["model_blocks"][0],
                "run_plan",
                "model",
            ),
            (
                "phase",
                lambda value: value["run_plan"]["model_blocks"][0]["phases"][0],
                "run_plan",
                "kind",
            ),
            (
                "pair",
                lambda value: value["run_plan"]["model_blocks"][0]["phases"][0][
                    "pair"
                ],
                "run_plan",
                "runtime_order",
            ),
            ("artifact", lambda value: value["runtime"], None, "root"),
            (
                "manifest",
                lambda value: value["runtime"]["manifest"],
                "runtime",
                "kind",
            ),
            (
                "file",
                lambda value: value["runtime"]["manifest"]["files"][0],
                "runtime",
                "path",
            ),
            ("selection", lambda value: value["selection"], None, "arm_index"),
            ("layout", lambda value: value["runtime_layout"], None, "interpreter"),
            ("corpus", lambda value: value["corpus"], None, "manifest_member"),
            (
                "profile",
                lambda value: value["decode_profile"],
                "decode_profile",
                "device",
            ),
        )
        for family, locate, domain, required in families:
            for mutation in ("unknown", "missing"):
                with self.subTest(family=family, mutation=mutation):
                    payload = self._payload()
                    target = locate(payload)
                    if mutation == "unknown":
                        target["unexpected"] = "value"
                    else:
                        del target[required]
                    if domain is not None:
                        self._rehash(payload, domain)
                    self.assertInvalid(self._encoded(payload))

    def test_hashes_and_expected_nonce_are_bound(self) -> None:
        for domain, hash_key in (
            ("run_plan", "run_plan_sha256"),
            ("decode_profile", "decode_profile_sha256"),
            ("runtime", "manifest_sha256"),
            ("model", "manifest_sha256"),
            ("clips", "manifest_sha256"),
        ):
            with self.subTest(domain=domain):
                payload = self._payload()
                owner = payload if domain in {"run_plan", "decode_profile"} else payload[domain]
                owner[hash_key] = "f" * 64
                self.assertInvalid(self._encoded(payload))
        self.assertInvalid(self.requests[0], nonce="3" * 64)
        for nonce in (None, True, "A" * 64, "1" * 63):
            with self.subTest(nonce=nonce):
                self.assertInvalid(self.requests[0], nonce=nonce)

    def test_changed_valid_manifests_do_not_escape_stale_plan_references(
        self,
    ) -> None:
        for domain in ("runtime", "model", "clips"):
            with self.subTest(domain=domain):
                payload = self._payload()
                old_reference = copy.deepcopy(
                    payload["run_plan"]["clips"]
                    if domain == "clips"
                    else (
                        payload["run_plan"]["model_blocks"][0]["model"]
                        if domain == "model"
                        else next(
                            value
                            for value in payload["run_plan"]["runtimes"].values()
                            if value["id"]
                            == payload["runtime"]["manifest"]["artifact_id"]
                        )
                    )
                )
                payload[domain]["manifest"]["files"][0]["sha256"] = "e" * 64
                self._rehash(payload, domain)
                self.assertNotEqual(
                    payload[domain]["manifest_sha256"],
                    old_reference["manifest_sha256"],
                )
                parsed_manifest = ct2_benchmark_worker._benchmark_manifest(
                    payload[domain]["manifest"],
                    manifest_sha256=payload[domain]["manifest_sha256"],
                )
                self.assertEqual(
                    parsed_manifest.reference.manifest_sha256,
                    payload[domain]["manifest_sha256"],
                )
                self.assertEqual(
                    (
                        payload["run_plan"]["clips"]
                        if domain == "clips"
                        else (
                            payload["run_plan"]["model_blocks"][0]["model"]
                            if domain == "model"
                            else next(
                                value
                                for value in payload["run_plan"]["runtimes"].values()
                                if value["id"]
                                == payload["runtime"]["manifest"]["artifact_id"]
                            )
                        )
                    ),
                    old_reference,
                )
                self.assertInvalid(self._encoded(payload))

    def test_run_plan_requires_exact_integer_and_boolean_types(self) -> None:
        equivalent_mutations = (
            (
                "schema-version-bool",
                lambda plan: (plan, "schema_version"),
                True,
            ),
            (
                "decision-eligibility-int",
                lambda plan: (plan, "decision_eligibility_capable"),
                1,
            ),
            (
                "fresh-process-int",
                lambda plan: (
                    plan["model_blocks"][0]["phases"][0],
                    "fresh_process_per_arm",
                ),
                1,
            ),
            (
                "warmup-before-int",
                lambda plan: (
                    plan["model_blocks"][0]["phases"][0],
                    "warmup_before_measurement",
                ),
                0,
            ),
            (
                "warmup-discarded-int",
                lambda plan: (
                    plan["model_blocks"][0]["phases"][0],
                    "warmup_discarded",
                ),
                0,
            ),
        )
        for name, locate, replacement in equivalent_mutations:
            with self.subTest(name=name):
                payload = self._payload()
                owner, key = locate(payload["run_plan"])
                original = owner[key]
                self.assertEqual(original, replacement)
                self.assertIsNot(type(original), type(replacement))
                owner[key] = replacement
                self._rehash(payload, "run_plan")
                self.assertInvalid(self._encoded(payload))

        def request_payload(*, mode: str, seed: int) -> dict[str, object]:
            experiment = self._encoded(
                {
                    "clips": self._reference_payload(self.clips.reference),
                    "mode": mode,
                    "models": [self._reference_payload(self.model.reference)],
                    "pair_count": 5 if mode == "full" else 1,
                    "runtimes": {
                        "a": self._reference_payload(self.runtime_a.reference),
                        "b": self._reference_payload(self.runtime_b.reference),
                    },
                    "schema_version": 1,
                }
            )
            plan = ct2_benchmark.build_run_plan(experiment, seed=seed)
            request = ct2_benchmark.build_benchmark_pair_requests(
                plan,
                self.artifacts,
                self.specs,
                self.profile,
                model_block_index=0,
                phase_index=0,
                nonces=self._NONCES,
            )[0]
            return json.loads(request)

        for name, mode, seed, field in (
            ("seed-bool", "full", 1, "seed"),
            ("pair-count-bool", "quick", 17, "pair_count"),
        ):
            with self.subTest(name=name):
                payload = request_payload(mode=mode, seed=seed)
                original = payload["run_plan"][field]
                self.assertIs(type(original), int)
                self.assertEqual(original, True)
                payload["run_plan"][field] = True
                self._rehash(payload, "run_plan")
                self.assertInvalid(self._encoded(payload))

        valid_pair = {
            "clip_order_seed": 1,
            "runtime_order": ["runtime-a", "runtime-b"],
        }
        parsed_pair = ct2_benchmark_worker._benchmark_run_pair_from_payload(
            valid_pair
        )
        self.assertEqual(parsed_pair.clip_order_seed, 1)
        bool_seed = copy.deepcopy(valid_pair)
        self.assertEqual(bool_seed["clip_order_seed"], True)
        self.assertIs(type(bool_seed["clip_order_seed"]), int)
        bool_seed["clip_order_seed"] = True
        with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
            ct2_benchmark_worker._benchmark_run_pair_from_payload(bool_seed)
        self.assertEqual(raised.exception.code, "benchmark-request-invalid")
        self.assertEqual(str(raised.exception), "benchmark-request-invalid")

    def test_schedule_global_references_indexes_and_artifact_binding_are_strict(
        self,
    ) -> None:
        payload = self._payload()
        payload["run_plan"]["model_blocks"][0]["phases"][0]["pair"][
            "runtime_order"
        ].reverse()
        self._rehash(payload, "run_plan")
        self.assertInvalid(self._encoded(payload))

        payload = self._payload()
        payload["run_plan"]["clips"] = copy.deepcopy(
            payload["run_plan"]["runtimes"]["a"]
        )
        self._rehash(payload, "run_plan")
        self.assertInvalid(self._encoded(payload))

        for field in ("id", "manifest_sha256"):
            with self.subTest(duplicate=field):
                payload = self._payload()
                payload["run_plan"]["clips"][field] = payload["run_plan"][
                    "runtimes"
                ]["a"][field]
                self._rehash(payload, "run_plan")
                self.assertInvalid(self._encoded(payload))

        for key, value in (
            ("arm_index", True),
            ("arm_index", 2),
            ("model_block_index", True),
            ("model_block_index", 1),
            ("phase_index", True),
            ("phase_index", 99),
        ):
            with self.subTest(key=key, value=value):
                payload = self._payload()
                payload["selection"][key] = value
                self.assertInvalid(self._encoded(payload))

        payload = self._payload()
        payload["runtime"], payload["model"] = payload["model"], payload["runtime"]
        self.assertInvalid(self._encoded(payload))

        payload = self._payload()
        payload["runtime"]["manifest"]["kind"] = "model"
        self._rehash(payload, "runtime")
        self._retarget_plan_reference(payload, "runtime")
        self.assertInvalid(self._encoded(payload))

    def test_manifest_members_counts_totals_and_roots_are_strict(self) -> None:
        file_template = copy.deepcopy(
            self._payload()["runtime"]["manifest"]["files"][0]
        )
        cases = (
            ("empty", []),
            ("too-many", [file_template] * (4096 + 1)),
            (
                "unordered",
                [
                    {**file_template, "path": "z"},
                    {**file_template, "path": "a"},
                ],
            ),
            (
                "duplicate",
                [
                    {**file_template, "path": "a"},
                    {**file_template, "path": "a"},
                ],
            ),
            (
                "ancestor",
                [
                    {**file_template, "path": "a"},
                    {**file_template, "path": "a/b"},
                ],
            ),
            ("total", [{**file_template, "size": (1 << 30) + 1}]),
            ("bool-size", [{**file_template, "size": True}]),
            ("non-bool-executable", [{**file_template, "executable": 0}]),
            ("relative-parent", [{**file_template, "path": "../payload"}]),
            ("backslash", [{**file_template, "path": "a\\b"}]),
            ("empty-component", [{**file_template, "path": "a//b"}]),
            ("line-separator", [{**file_template, "path": "a\u2028b"}]),
            ("non-nfc", [{**file_template, "path": "a\u0308"}]),
            ("too-long", [{**file_template, "path": "a" * 513}]),
            ("too-deep", [{**file_template, "path": "/".join("a" for _ in range(33))}]),
        )
        for name, files in cases:
            with self.subTest(name=name):
                payload = self._payload()
                payload["runtime"]["manifest"]["files"] = files
                self._rehash(payload, "runtime")
                self._retarget_plan_reference(payload, "runtime")
                self.assertInvalid(self._encoded(payload))

        for duplicate in ("model", "clips"):
            with self.subTest(duplicate_root=duplicate):
                payload = self._payload()
                payload[duplicate]["root"] = payload["runtime"]["root"]
                self.assertInvalid(self._encoded(payload))

    def test_worker_limits_and_path_predicates_match_controller(self) -> None:
        constants = (
            "MAX_ARM_REQUEST_BYTES",
            "MAX_ARTIFACT_MANIFEST_BYTES",
            "MAX_ARTIFACT_FILES",
            "MAX_ARTIFACT_PATH_CHARS",
            "MAX_ARTIFACT_PATH_BYTES",
            "MAX_ARTIFACT_PATH_DEPTH",
            "MAX_DECLARED_ARTIFACT_BYTES",
            "MAX_CORPUS_BYTES",
            "MAX_MODEL_BLOCKS",
            "FULL_PAIR_COUNT",
            "QUICK_PAIR_COUNT",
        )
        for name in constants:
            with self.subTest(constant=name):
                self.assertEqual(
                    getattr(ct2_benchmark_worker, name),
                    getattr(ct2_benchmark, name),
                )
        self.assertEqual(
            ct2_benchmark_worker.MAX_RESULT_BYTES,
            ct2_benchmark.MAX_RESULT_BYTES,
        )
        self.assertEqual(
            ct2_benchmark_worker.MAX_RUN_PLAN_BYTES,
            ct2_benchmark.MAX_RUN_PLAN_BYTES,
        )
        self.assertEqual(
            ct2_benchmark_worker.MAX_DECODE_PROFILE_BYTES,
            ct2_benchmark.MAX_RESULT_BYTES,
        )
        roots: tuple[object, ...] = (
            "/srv/runtime",
            "/srv/modell-\u00e4",
            "//srv/runtime",
            "/srv/../runtime",
            "/srv/a\u2028b",
            "relative",
            None,
        )
        for root in roots:
            with self.subTest(root=root):
                self.assertEqual(
                    ct2_benchmark_worker._safe_root_path(root) is not None,
                    ct2_benchmark._artifact_root_text_is_valid(root),
                )
        runtime_paths: tuple[object, ...] = (
            "/srv/runtime",
            "/srv/r\u00fcntime",
            "//srv/runtime",
            "/srv/../runtime",
            None,
        )
        for path in runtime_paths:
            with self.subTest(runtime_path=path):
                worker_value = ct2_benchmark_worker._absolute_path(path)
                if type(path) is str and path.startswith("//"):
                    worker_value = None
                controller_value = ct2_benchmark._validated_path(path)
                if type(path) is str and path.startswith("//"):
                    controller_value = None
                self.assertEqual(worker_value is not None, controller_value is not None)

    def test_paths_unicode_members_and_host_interpreter_boundary(self) -> None:
        parsed = ct2_benchmark_worker._parse_benchmark_arm_request(
            self.requests[0],
            expected_nonce=self._NONCES[0],
        )
        self.assertEqual(parsed.model.root, "/srv/soc/model-\u00e4")
        self.assertEqual(parsed.model.manifest.files[0].path, "modell-\u00e4.bin")
        self.assertEqual(parsed.clips.manifest.files[1].path, "sprache/clip-\u00e4.raw")

        mutations = (
            ("runtime-unicode", lambda value: value["runtime"].__setitem__("root", "/srv/r\u00fcntime")),
            ("runtime-double-slash", lambda value: value["runtime"].__setitem__("root", "//srv/runtime")),
            ("model-double-slash", lambda value: value["model"].__setitem__("root", "//srv/model")),
            ("clips-double-slash", lambda value: value["clips"].__setitem__("root", "//srv/clips")),
            ("interpreter-double-slash", lambda value: value["runtime_layout"].__setitem__("interpreter", "//usr/bin/python3")),
            ("interpreter-under-root", lambda value: value["runtime_layout"].__setitem__("interpreter", f"{value['model']['root']}/python")),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                payload = self._payload()
                mutate(payload)
                self.assertInvalid(self._encoded(payload))

    def test_corpus_layout_versions_profile_and_nonce_are_closed(self) -> None:
        direct = (
            ("corpus", "manifest_member", "other.json"),
            ("runtime_layout", "interpreter_contract", "attested"),
            ("runtime_layout", "site_packages_member", "site"),
            ("runtime_layout", "expected_ctranslate2_version", "4.9.0"),
            ("runtime_layout", "expected_faster_whisper_version", "1.2.2"),
            (None, "nonce", "3" * 64),
        )
        for domain, key, value in direct:
            with self.subTest(domain=domain, key=key):
                payload = self._payload()
                owner = payload if domain is None else payload[domain]
                owner[key] = value
                self.assertInvalid(self._encoded(payload))

        for key, value in (
            ("profile_schema_version", True),
            ("device", "auto"),
            ("requested_compute_type", "auto"),
            ("cpu_threads", True),
            ("cpu_threads", 0),
            ("num_workers", 2),
            ("language", "en"),
            ("task", "translate"),
            ("beam_size", 33),
            ("temperature_milli", 1),
            ("vad_filter", 0),
            ("vad_filter", True),
            ("condition_on_previous_text", True),
            ("word_timestamps", True),
            ("without_timestamps", True),
        ):
            with self.subTest(profile_key=key, value=value):
                payload = self._payload()
                payload["decode_profile"][key] = value
                self._rehash(payload, "decode_profile")
                self.assertInvalid(self._encoded(payload))

        for executable, size in ((True, 2), (False, 0), (False, (1 << 20) + 1)):
            with self.subTest(executable=executable, size=size):
                payload = self._payload()
                corpus = next(
                    value
                    for value in payload["clips"]["manifest"]["files"]
                    if value["path"] == "corpus-v1.json"
                )
                corpus["executable"] = executable
                corpus["size"] = size
                self._rehash(payload, "clips")
                self._retarget_plan_reference(payload, "clips")
                self.assertInvalid(self._encoded(payload))

    def test_real_run_plan_and_profile_caps_are_exact(self) -> None:
        baseline = ct2_benchmark_worker._parse_benchmark_arm_request(
            self.requests[0],
            expected_nonce=self._NONCES[0],
        )
        domains = (
            (
                "run_plan",
                "run_plan_sha256",
                "_benchmark_run_plan",
                baseline.run_plan,
            ),
            (
                "decode_profile",
                "decode_profile_sha256",
                "_benchmark_decode_profile",
                baseline.decode_profile,
            ),
        )
        for domain, hash_field, parser_name, typed_result in domains:
            for nested_size, accepted in (
                (ct2_benchmark_worker.MAX_RESULT_BYTES, True),
                (ct2_benchmark_worker.MAX_RESULT_BYTES + 1, False),
            ):
                with self.subTest(
                    domain=domain,
                    nested_size=nested_size,
                ):
                    payload = self._payload()
                    payload[domain] = self._padded_object(
                        payload[domain],
                        nested_size,
                    )
                    payload[hash_field] = hashlib.sha256(
                        self._encoded(payload[domain])
                    ).hexdigest()
                    request = self._encoded(payload)
                    self.assertLess(
                        len(request),
                        ct2_benchmark_worker.MAX_ARM_REQUEST_BYTES,
                    )
                    with mock.patch.object(
                        ct2_benchmark_worker,
                        parser_name,
                        return_value=typed_result,
                    ) as semantic_parser:
                        if accepted:
                            ct2_benchmark_worker._parse_benchmark_arm_request(
                                request,
                                expected_nonce=self._NONCES[0],
                            )
                            semantic_parser.assert_called_once_with(payload[domain])
                        else:
                            self.assertInvalid(request)
                            semantic_parser.assert_not_called()

    def test_real_manifest_caps_are_exact_for_each_artifact(self) -> None:
        for domain in ("runtime", "model", "clips"):
            for nested_size, accepted in (
                (ct2_benchmark_worker.MAX_ARTIFACT_MANIFEST_BYTES, True),
                (ct2_benchmark_worker.MAX_ARTIFACT_MANIFEST_BYTES + 1, False),
            ):
                with self.subTest(
                    domain=domain,
                    nested_size=nested_size,
                ):
                    payload = self._payload()
                    payload[domain]["manifest"] = self._manifest_payload_at_size(
                        payload[domain]["manifest"],
                        nested_size,
                    )
                    self._rehash(payload, domain)
                    self._retarget_plan_reference(payload, domain)
                    request = self._encoded(payload)
                    self.assertEqual(
                        len(self._encoded(payload[domain]["manifest"])),
                        nested_size,
                    )
                    self.assertLess(
                        len(request),
                        ct2_benchmark_worker.MAX_ARM_REQUEST_BYTES,
                    )
                    if accepted:
                        parsed = ct2_benchmark_worker._parse_benchmark_arm_request(
                            request,
                            expected_nonce=self._NONCES[0],
                        )
                        self.assertEqual(
                            getattr(parsed, domain).manifest.reference.manifest_sha256,
                            payload[domain]["manifest_sha256"],
                        )
                    else:
                        self.assertInvalid(request)

    def test_result_is_deeply_immutable_and_repr_is_redacted(self) -> None:
        parsed = ct2_benchmark_worker._parse_benchmark_arm_request(
            self.requests[0], expected_nonce=self._NONCES[0]
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parsed.nonce = "3" * 64
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parsed.runtime.root = "/changed"
        self.assertIsInstance(parsed.run_plan.model_blocks, tuple)
        self.assertIsInstance(parsed.runtime.manifest.files, tuple)
        for value in (
            parsed,
            parsed.run_plan,
            parsed.runtime,
            parsed.runtime.manifest,
            parsed.decode_profile,
        ):
            rendered = repr(value)
            self.assertEqual(rendered, f"<{type(value).__name__} redacted>")
            for secret in (
                "/srv/",
                "runtime-a",
                "model-one",
                self.runtime_a.reference.manifest_sha256,
            ):
                self.assertNotIn(secret, rendered)

    def test_parser_is_pure_and_boundary_redacts_unexpected_failures(self) -> None:
        with (
            mock.patch.object(
                ct2_benchmark_worker.os,
                "open",
                side_effect=AssertionError("filesystem"),
            ) as opener,
            mock.patch.object(
                ct2_benchmark_worker.os,
                "scandir",
                side_effect=AssertionError("filesystem"),
            ) as scanner,
            mock.patch.object(
                ct2_benchmark_worker.importlib,
                "import_module",
                side_effect=AssertionError("runtime"),
            ) as importer,
            mock.patch.object(
                ct2_benchmark_worker,
                "_attest_artifact",
                side_effect=AssertionError("attestation"),
            ) as attester,
        ):
            ct2_benchmark_worker._parse_benchmark_arm_request(
                self.requests[0], expected_nonce=self._NONCES[0]
            )
        opener.assert_not_called()
        scanner.assert_not_called()
        importer.assert_not_called()
        attester.assert_not_called()

        for failure in (RuntimeError("private/path"), SystemExit("private/path")):
            with (
                self.subTest(failure=type(failure).__name__),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_parse_benchmark_arm_request_checked",
                    side_effect=failure,
                ),
            ):
                self.assertInvalid(self.requests[0])

        interrupt = KeyboardInterrupt()
        with (
            mock.patch.object(
                ct2_benchmark_worker,
                "_parse_benchmark_arm_request_checked",
                side_effect=interrupt,
            ),
            self.assertRaises(KeyboardInterrupt) as raised,
        ):
            ct2_benchmark_worker._parse_benchmark_arm_request(
                self.requests[0], expected_nonce=self._NONCES[0]
            )
        self.assertIs(raised.exception, interrupt)


class Ctranslate2BenchmarkArmBundleTests(unittest.TestCase):
    _NONCES = ("3" * 64, "4" * 64)

    @staticmethod
    def _encoded(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    @classmethod
    def _manifest(
        cls,
        artifact_id: str,
        kind: str,
        files: tuple[tuple[str, bytes, bool], ...],
    ) -> ct2_benchmark.ArtifactManifest:
        return Ctranslate2BenchmarkArmRequestParserTests._manifest(
            artifact_id,
            kind,
            files,
        )

    @staticmethod
    def _reference_payload(
        reference: ct2_benchmark.ArtifactManifestReference,
    ) -> dict[str, str]:
        return {
            "id": reference.artifact_id,
            "manifest_sha256": reference.manifest_sha256,
        }

    @staticmethod
    def _corpus_payload(count: int = 12) -> dict[str, object]:
        return {
            "clips": [
                {
                    "audio_path": f"audio/clip-{index:02d}.wav",
                    "id": f"clip-{index:02d}",
                    "reference_text": f"Beispiel {index:02d}.",
                    "scenario": "quiet-room",
                }
                for index in range(count)
            ],
            "language": "de",
            "schema_version": 1,
        }

    @staticmethod
    def _manifest_files(
        corpus_data: bytes,
        clips: list[dict[str, object]],
        *,
        duplicate_audio_hash: bool = False,
    ) -> tuple[tuple[str, bytes, bool], ...]:
        files: list[tuple[str, bytes, bool]] = [
            ("corpus-v1.json", corpus_data, False)
        ]
        for index, clip in enumerate(clips):
            contents = (
                b"duplicate-audio"
                if duplicate_audio_hash and index < 2
                else f"audio-{index:02d}".encode("ascii")
            )
            files.append((clip["audio_path"], contents, False))
        return tuple(files)

    def _build_case(
        self,
        *,
        corpus_payload: dict[str, object] | None = None,
        corpus_data: bytes | None = None,
        manifest_files: tuple[tuple[str, bytes, bool], ...] | None = None,
        mode: str = "full",
        seed: int = 17,
        phase_index: int = 0,
    ) -> tuple[tuple[bytes, bytes], bytes]:
        payload = (
            self._corpus_payload()
            if corpus_payload is None
            else corpus_payload
        )
        encoded_corpus = (
            self._encoded(payload) if corpus_data is None else corpus_data
        )
        files = (
            self._manifest_files(encoded_corpus, payload["clips"])
            if manifest_files is None
            else manifest_files
        )
        runtime_a = self._manifest(
            "runtime-a",
            "runtime",
            (("runtime-a.bin", b"runtime-a", False),),
        )
        runtime_b = self._manifest(
            "runtime-b",
            "runtime",
            (("runtime-b.bin", b"runtime-b", False),),
        )
        clips = self._manifest("clips-set", "clips", files)
        model = self._manifest(
            "model-one",
            "model",
            (("model.bin", b"model", False),),
        )
        experiment = self._encoded(
            {
                "clips": self._reference_payload(clips.reference),
                "mode": mode,
                "models": [self._reference_payload(model.reference)],
                "pair_count": 5 if mode == "full" else 1,
                "runtimes": {
                    "a": self._reference_payload(runtime_a.reference),
                    "b": self._reference_payload(runtime_b.reference),
                },
                "schema_version": 1,
            }
        )
        plan = ct2_benchmark.build_run_plan(experiment, seed=seed)
        artifacts = (
            (Path("/srv/soc/runtime-a"), runtime_a),
            (Path("/srv/soc/runtime-b"), runtime_b),
            (Path("/srv/soc/clips"), clips),
            (Path("/srv/soc/model"), model),
        )
        specs = (
            ct2_benchmark._RuntimeSpec(
                interpreter="/usr/bin/python3",
                site_packages=str(artifacts[0][0]),
                expected_ctranslate2_version="4.7.2",
                expected_faster_whisper_version="1.2.1",
            ),
            ct2_benchmark._RuntimeSpec(
                interpreter="/usr/bin/python3",
                site_packages=str(artifacts[1][0]),
                expected_ctranslate2_version="4.8.1",
                expected_faster_whisper_version="1.2.1",
            ),
        )
        profile = ct2_benchmark.DecodeProfile(
            profile_schema_version=1,
            device="cpu",
            requested_compute_type="int8",
            cpu_threads=4,
            num_workers=1,
            language="de",
            task="transcribe",
            beam_size=5,
            temperature_milli=0,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=False,
            without_timestamps=False,
        )
        requests = ct2_benchmark.build_benchmark_pair_requests(
            plan,
            artifacts,
            specs,
            profile,
            model_block_index=0,
            phase_index=phase_index,
            nonces=self._NONCES,
        )
        return requests, encoded_corpus

    def _parse(
        self,
        request: bytes,
        corpus_data: bytes,
        *,
        arm: int = 0,
    ) -> ct2_benchmark_worker._BenchmarkArmBundle:
        return ct2_benchmark_worker._parse_benchmark_arm_bundle(
            request,
            corpus_data,
            expected_nonce=self._NONCES[arm],
        )

    def _assert_corpus_invalid(
        self,
        request: bytes,
        corpus_data: object,
        *,
        arm: int = 0,
    ) -> ct2_benchmark_worker.WorkerFailure:
        with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
            self._parse(request, corpus_data, arm=arm)
        self.assertEqual(raised.exception.code, "benchmark-corpus-invalid")
        self.assertEqual(raised.exception.args, ("benchmark-corpus-invalid",))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        return raised.exception

    def _assert_semantic_invalid(
        self,
        payload: dict[str, object],
        *,
        manifest_files: tuple[tuple[str, bytes, bool], ...] | None = None,
    ) -> None:
        requests, corpus_data = self._build_case(
            corpus_payload=payload,
            manifest_files=manifest_files,
        )
        parsed_request = ct2_benchmark_worker._parse_benchmark_arm_request(
            requests[0],
            expected_nonce=self._NONCES[0],
        )
        self.assertIsInstance(
            parsed_request,
            ct2_benchmark_worker._BenchmarkArmRequest,
        )
        self._assert_corpus_invalid(requests[0], corpus_data)

    def test_valid_full_and_quick_bundles_share_seeded_order(self) -> None:
        for count in (12, 20):
            payload = self._corpus_payload(count)
            if count == 20:
                payload["clips"][0]["audio_path"] = "audio/clip-00.flac"
            requests, corpus_data = self._build_case(
                corpus_payload=payload
            )
            bundles = tuple(
                self._parse(requests[arm], corpus_data, arm=arm)
                for arm in range(2)
            )
            with self.subTest(count=count):
                self.assertEqual(len(bundles[0].corpus.clips), count)
                self.assertEqual(
                    tuple(clip.id for clip in bundles[0].corpus.clips),
                    tuple(clip.id for clip in bundles[1].corpus.clips),
                )
                self.assertEqual(
                    bundles[0].corpus.execution_clips,
                    bundles[0].corpus.clips,
                )
                self.assertEqual(
                    bundles[1].corpus.execution_clips,
                    bundles[1].corpus.clips,
                )
                declarations = {
                    value.path: value
                    for value in bundles[0].request.clips.manifest.files
                }
                for clip in bundles[0].corpus.clips:
                    self.assertEqual(
                        (clip.audio_sha256, clip.audio_size),
                        (
                            declarations[clip.audio_path].sha256,
                            declarations[clip.audio_path].size,
                        ),
                    )
            if count == 20:
                self.assertTrue(
                    any(
                        clip.audio_path.endswith(".flac")
                        for clip in bundles[0].corpus.clips
                    )
                )

        requests, corpus_data = self._build_case(
            mode="quick",
            phase_index=1,
        )
        quick = self._parse(requests[0], corpus_data)
        phases = quick.request.run_plan.model_blocks[0].phases
        selected = phases[quick.request.selection.phase_index]
        self.assertEqual(phases[0].kind, "cold")
        self.assertEqual(selected.kind, "measurement")
        self.assertFalse(quick.request.run_plan.decision_eligibility_capable)
        self.assertEqual(
            quick.request.run_plan.pair_count,
            ct2_benchmark_worker.QUICK_PAIR_COUNT,
        )
        self.assertEqual(
            quick.corpus.execution_clips,
            quick.corpus.clips[:3],
        )

    def test_request_is_parsed_once_before_corpus_is_touched(self) -> None:
        requests, corpus_data = self._build_case()
        request_parser = ct2_benchmark_worker._parse_benchmark_arm_request
        corpus_parser = ct2_benchmark_worker._parse_benchmark_corpus_checked
        with (
            mock.patch.object(
                ct2_benchmark_worker,
                "_parse_benchmark_arm_request",
                wraps=request_parser,
            ) as request_probe,
            mock.patch.object(
                ct2_benchmark_worker,
                "_parse_benchmark_corpus_checked",
                wraps=corpus_parser,
            ) as corpus_probe,
        ):
            self._parse(requests[0], corpus_data)
        request_probe.assert_called_once_with(
            requests[0],
            expected_nonce=self._NONCES[0],
        )
        self.assertEqual(corpus_probe.call_count, 1)

        class PoisonCorpus:
            def __getattribute__(self, _name: str) -> object:
                raise AssertionError("corpus inspected")

        with mock.patch.object(
            ct2_benchmark_worker,
            "_parse_benchmark_corpus_checked",
            side_effect=AssertionError("corpus parser called"),
        ) as corpus_probe:
            with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
                ct2_benchmark_worker._parse_benchmark_arm_bundle(
                    b"{}",
                    PoisonCorpus(),
                    expected_nonce=self._NONCES[0],
                )
        self.assertEqual(raised.exception.code, "benchmark-request-invalid")
        corpus_probe.assert_not_called()

    def test_bundle_requires_exact_request_and_corpus_parser_result_types(
        self,
    ) -> None:
        requests, corpus_data = self._build_case()
        request = ct2_benchmark_worker._parse_benchmark_arm_request(
            requests[0],
            expected_nonce=self._NONCES[0],
        )
        corpus = ct2_benchmark_worker._parse_benchmark_corpus_checked(
            corpus_data,
            request=request,
        )

        class RequestSubclass(ct2_benchmark_worker._BenchmarkArmRequest):
            pass

        class CorpusSubclass(ct2_benchmark_worker._ParsedCorpus):
            pass

        request_subclass = RequestSubclass(
            **{
                field.name: getattr(request, field.name)
                for field in dataclasses.fields(request)
            }
        )
        corpus_subclass = CorpusSubclass(
            **{
                field.name: getattr(corpus, field.name)
                for field in dataclasses.fields(corpus)
            }
        )
        for invalid_request in (object(), request_subclass):
            with (
                self.subTest(request_type=type(invalid_request).__name__),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_parse_benchmark_arm_request",
                    return_value=invalid_request,
                ) as request_parser,
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_parse_benchmark_corpus_checked",
                    return_value=object(),
                ) as corpus_parser,
                self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised,
            ):
                self._parse(requests[0], corpus_data)
            self.assertEqual(raised.exception.code, "benchmark-request-invalid")
            self.assertEqual(raised.exception.args, ("benchmark-request-invalid",))
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)
            request_parser.assert_called_once_with(
                requests[0],
                expected_nonce=self._NONCES[0],
            )
            corpus_parser.assert_not_called()

        for invalid_corpus in (object(), corpus_subclass):
            with (
                self.subTest(corpus_type=type(invalid_corpus).__name__),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_parse_benchmark_arm_request",
                    return_value=request,
                ) as request_parser,
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_parse_benchmark_corpus_checked",
                    return_value=invalid_corpus,
                ) as corpus_parser,
            ):
                error = self._assert_corpus_invalid(requests[0], corpus_data)
            self.assertIsNone(error.__cause__)
            self.assertIsNone(error.__context__)
            request_parser.assert_called_once_with(
                requests[0],
                expected_nonce=self._NONCES[0],
            )
            corpus_parser.assert_called_once_with(corpus_data, request=request)

        forged = request_parser(
            requests[0],
            expected_nonce=self._NONCES[0],
        )
        with mock.patch.object(
            ct2_benchmark_worker,
            "_parse_benchmark_corpus_checked",
        ) as corpus_probe:
            with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
                ct2_benchmark_worker._parse_benchmark_arm_bundle(
                    forged,
                    corpus_data,
                    expected_nonce=self._NONCES[0],
                )
        self.assertEqual(raised.exception.code, "benchmark-request-invalid")
        corpus_probe.assert_not_called()

    def test_corpus_raw_type_size_length_and_hash_are_bound(self) -> None:
        requests, corpus_data = self._build_case()
        changed = corpus_data[:-1] + bytes([corpus_data[-1] ^ 1])

        class BytesSubclass(bytes):
            pass

        cases = (
            BytesSubclass(corpus_data),
            bytearray(corpus_data),
            memoryview(corpus_data),
            b"",
            b"x" * (ct2_benchmark_worker.MAX_CORPUS_BYTES + 1),
            corpus_data + b"x",
            changed,
        )
        for candidate in cases:
            with self.subTest(candidate_type=type(candidate).__name__):
                self._assert_corpus_invalid(requests[0], candidate)

    def test_declared_corpus_size_mismatch_reaches_bundle_guard(self) -> None:
        requests, corpus_data = self._build_case()
        payload = json.loads(requests[0])
        corpus_file = next(
            value
            for value in payload["clips"]["manifest"]["files"]
            if value["path"] == "corpus-v1.json"
        )
        expected_sha256 = hashlib.sha256(corpus_data).hexdigest()
        self.assertEqual(corpus_file["sha256"], expected_sha256)
        corpus_file["size"] += 1
        self.assertGreater(corpus_file["size"], 0)
        self.assertLessEqual(
            corpus_file["size"],
            ct2_benchmark_worker.MAX_CORPUS_BYTES,
        )
        self.assertEqual(corpus_file["sha256"], expected_sha256)
        manifest_sha256 = hashlib.sha256(
            self._encoded(payload["clips"]["manifest"])
        ).hexdigest()
        payload["clips"]["manifest_sha256"] = manifest_sha256
        payload["run_plan"]["clips"]["manifest_sha256"] = manifest_sha256
        payload["run_plan_sha256"] = hashlib.sha256(
            self._encoded(payload["run_plan"])
        ).hexdigest()
        candidate = self._encoded(payload)
        parsed_request = ct2_benchmark_worker._parse_benchmark_arm_request(
            candidate,
            expected_nonce=self._NONCES[0],
        )
        self.assertIs(
            type(parsed_request),
            ct2_benchmark_worker._BenchmarkArmRequest,
        )
        self._assert_corpus_invalid(candidate, corpus_data)

    def test_corpus_wire_is_closed_canonical_ascii_and_finite(self) -> None:
        payload = self._corpus_payload()
        canonical = self._encoded(payload)
        unknown = copy.deepcopy(payload)
        unknown["extra"] = 1
        missing = copy.deepcopy(payload)
        del missing["language"]
        clip_unknown = copy.deepcopy(payload)
        clip_unknown["clips"][0]["extra"] = 1
        clip_missing = copy.deepcopy(payload)
        del clip_missing["clips"][0]["scenario"]
        unicode_payload = copy.deepcopy(payload)
        unicode_payload["clips"][0]["reference_text"] = "Gr\u00fc\u00dfe"
        raw_candidates = (
            canonical.replace(
                b'"language":"de"',
                b'"language":"de","language":"de"',
                1,
            ),
            canonical.replace(b'"schema_version":1', b'"schema_version":NaN', 1),
            canonical.replace(
                b'"id":"clip-00"',
                b'"id":"clip-00","id":"clip-00"',
                1,
            ),
            self._encoded(unknown),
            self._encoded(missing),
            self._encoded(clip_unknown),
            self._encoded(clip_missing),
            json.dumps(payload, ensure_ascii=True).encode("ascii"),
            json.dumps(
                unicode_payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
        )
        for corpus_data in raw_candidates:
            requests, _ = self._build_case(corpus_data=corpus_data)
            with self.subTest(length=len(corpus_data)):
                self._assert_corpus_invalid(requests[0], corpus_data)

    def test_corpus_top_level_types_and_language_are_exact(self) -> None:
        cases = (
            ("schema_version", True),
            ("schema_version", 2),
            ("language", True),
            ("language", "en"),
            ("clips", {}),
            ("clips", True),
        )
        defaults = self._corpus_payload()
        for field, value in cases:
            payload = self._corpus_payload()
            payload[field] = value
            corpus_data = self._encoded(payload)
            files = (
                None
                if type(payload["clips"]) is list
                else self._manifest_files(corpus_data, defaults["clips"])
            )
            with self.subTest(field=field, value_type=type(value).__name__):
                self._assert_semantic_invalid(payload, manifest_files=files)

    def test_clip_count_id_order_and_scalar_contracts_are_strict(self) -> None:
        for count in (11, 21):
            with self.subTest(count=count):
                self._assert_semantic_invalid(self._corpus_payload(count))

        mutations: list[tuple[str, object]] = [
            ("id", "Clip-00"),
            ("id", "x" * 65),
            ("scenario", "Upper"),
            ("scenario", "gr\u00fcn"),
            ("scenario", "x" * 33),
            ("scenario", 1),
            ("reference_text", 1),
            ("audio_path", 1),
        ]
        for field, value in mutations:
            payload = self._corpus_payload()
            payload["clips"][0][field] = value
            data = self._encoded(payload)
            files = self._manifest_files(data, self._corpus_payload()["clips"])
            with self.subTest(field=field, value_type=type(value).__name__):
                self._assert_semantic_invalid(payload, manifest_files=files)

        duplicate = self._corpus_payload()
        duplicate["clips"][1]["id"] = duplicate["clips"][0]["id"]
        self._assert_semantic_invalid(duplicate)
        unsorted = self._corpus_payload()
        unsorted["clips"][0], unsorted["clips"][1] = (
            unsorted["clips"][1],
            unsorted["clips"][0],
        )
        self._assert_semantic_invalid(unsorted)

    def test_audio_paths_hashes_and_manifest_tree_are_exact(self) -> None:
        payload = self._corpus_payload()
        corpus_data = self._encoded(payload)
        default_files = self._manifest_files(corpus_data, payload["clips"])

        duplicate_path = copy.deepcopy(payload)
        duplicate_path["clips"][1]["audio_path"] = duplicate_path["clips"][0][
            "audio_path"
        ]
        duplicate_data = self._encoded(duplicate_path)
        duplicate_audio_paths = tuple(
            value["audio_path"] for value in duplicate_path["clips"]
        )
        unique_audio_paths = tuple(dict.fromkeys(duplicate_audio_paths))
        duplicate_files = (
            ("corpus-v1.json", duplicate_data, False),
            *tuple(
                (path, f"deduplicated-{index}".encode("ascii"), False)
                for index, path in enumerate(unique_audio_paths)
            ),
        )
        requests, _ = self._build_case(
            corpus_payload=duplicate_path,
            manifest_files=duplicate_files,
        )
        duplicate_request = ct2_benchmark_worker._parse_benchmark_arm_request(
            requests[0],
            expected_nonce=self._NONCES[0],
        )
        self.assertEqual(
            set(value.path for value in duplicate_request.clips.manifest.files),
            {"corpus-v1.json", *duplicate_audio_paths},
        )
        shared_path = duplicate_audio_paths[0]
        shared_declarations = tuple(
            value
            for value in duplicate_request.clips.manifest.files
            if value.path == shared_path
        )
        self.assertEqual(duplicate_audio_paths.count(shared_path), 2)
        self.assertEqual(len(shared_declarations), 1)
        self.assertEqual(
            tuple(
                shared_declarations[0].sha256
                for path in duplicate_audio_paths
                if path == shared_path
            ),
            (shared_declarations[0].sha256, shared_declarations[0].sha256),
        )
        self._assert_corpus_invalid(requests[0], duplicate_data)

        duplicate_hash_files = self._manifest_files(
            corpus_data,
            payload["clips"],
            duplicate_audio_hash=True,
        )
        self._assert_semantic_invalid(
            payload,
            manifest_files=duplicate_hash_files,
        )

        variants = (
            default_files[:-1],
            (*default_files, ("audio/extra.wav", b"extra", False)),
            (
                default_files[0],
                (default_files[1][0], default_files[1][1], True),
                *default_files[2:],
            ),
            (
                default_files[0],
                (default_files[1][0], b"", False),
                *default_files[2:],
            ),
        )
        for files in variants:
            with self.subTest(file_count=len(files)):
                self._assert_semantic_invalid(payload, manifest_files=files)

        unsupported = self._corpus_payload()
        unsupported["clips"][0]["audio_path"] = "audio/clip-00.ogg"
        self._assert_semantic_invalid(unsupported)
        self_reference = self._corpus_payload()
        self_reference["clips"][0]["audio_path"] = "corpus-v1.json"
        self._assert_semantic_invalid(
            self_reference,
            manifest_files=self._manifest_files(
                self._encoded(self_reference),
                payload["clips"],
            ),
        )

        requests, _ = self._build_case()
        executable_corpus = json.loads(requests[0])
        corpus_file = next(
            value
            for value in executable_corpus["clips"]["manifest"]["files"]
            if value["path"] == "corpus-v1.json"
        )
        corpus_file["executable"] = True
        manifest_sha256 = hashlib.sha256(
            self._encoded(executable_corpus["clips"]["manifest"])
        ).hexdigest()
        executable_corpus["clips"]["manifest_sha256"] = manifest_sha256
        executable_corpus["run_plan"]["clips"][
            "manifest_sha256"
        ] = manifest_sha256
        executable_corpus["run_plan_sha256"] = hashlib.sha256(
            self._encoded(executable_corpus["run_plan"])
        ).hexdigest()
        with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
            self._parse(self._encoded(executable_corpus), corpus_data)
        self.assertEqual(raised.exception.code, "benchmark-request-invalid")

    def test_bundle_order_uses_selected_nonfirst_phase_seed(self) -> None:
        requests, corpus_data = self._build_case(seed=17, phase_index=1)
        parsed = self._parse(requests[0], corpus_data)
        phases = parsed.request.run_plan.model_blocks[0].phases
        selected_seed = phases[parsed.request.selection.phase_index].pair.clip_order_seed
        adjacent_seed = phases[0].pair.clip_order_seed
        ids = tuple(value["id"] for value in self._corpus_payload()["clips"])
        domain = b"SOC-CT2-CORPUS-ORDER-V1\x00"

        def independent_order(seed: int) -> tuple[str, ...]:
            return tuple(
                sorted(
                    ids,
                    key=lambda clip_id: (
                        hashlib.sha256(
                            domain
                            + seed.to_bytes(8, "big")
                            + b"\x00"
                            + clip_id.encode("ascii")
                        ).digest(),
                        clip_id,
                    ),
                )
            )

        expected = independent_order(selected_seed)
        adjacent = independent_order(adjacent_seed)
        self.assertNotEqual(selected_seed, adjacent_seed)
        self.assertNotEqual(expected, adjacent)
        self.assertEqual(
            tuple(value.id for value in parsed.corpus.clips),
            expected,
        )

    def test_member_paths_and_extensions_are_strict_but_nfc_unicode_is_valid(
        self,
    ) -> None:
        defaults = self._corpus_payload()
        invalid_paths = (
            "../clip.wav",
            "/clip.wav",
            "audio\\clip.wav",
            "audio/a\u0308.wav",
            "audio/clip.mp3",
        )
        for value in invalid_paths:
            payload = self._corpus_payload()
            payload["clips"][0]["audio_path"] = value
            corpus_data = self._encoded(payload)
            files = self._manifest_files(corpus_data, defaults["clips"])
            with self.subTest(value=value.encode("unicode_escape")):
                self._assert_semantic_invalid(payload, manifest_files=files)

        payload = self._corpus_payload()
        payload["clips"][0]["audio_path"] = "audio/\u00e4.wav"
        payload["clips"][0]["reference_text"] = "Gr\u00fc\u00dfe, sch\u00f6ne Welt!"
        requests, corpus_data = self._build_case(corpus_payload=payload)
        parsed = self._parse(requests[0], corpus_data)
        clip = next(value for value in parsed.corpus.clips if value.id == "clip-00")
        self.assertEqual(clip.audio_path, "audio/\u00e4.wav")
        self.assertEqual(clip.reference_text, "Gr\u00fc\u00dfe, sch\u00f6ne Welt!")

    def test_uppercase_audio_extensions_reach_corpus_policy(self) -> None:
        for extension in (".WAV", ".FLAC"):
            payload = self._corpus_payload()
            audio_path = f"audio/clip-00{extension}"
            payload["clips"][0]["audio_path"] = audio_path
            requests, corpus_data = self._build_case(corpus_payload=payload)
            parsed_request = ct2_benchmark_worker._parse_benchmark_arm_request(
                requests[0],
                expected_nonce=self._NONCES[0],
            )
            with self.subTest(extension=extension):
                self.assertIs(
                    type(parsed_request),
                    ct2_benchmark_worker._BenchmarkArmRequest,
                )
                self.assertIn(
                    audio_path,
                    {
                        value.path
                        for value in parsed_request.clips.manifest.files
                    },
                )
                self._assert_corpus_invalid(requests[0], corpus_data)

    def test_reference_text_limits_whitespace_and_categories_are_strict(self) -> None:
        accepted = self._corpus_payload()
        accepted["clips"][0]["reference_text"] = "a" * 4096
        requests, corpus_data = self._build_case(corpus_payload=accepted)
        self._parse(requests[0], corpus_data)

        invalid_values = (
            "",
            "a" * 4097,
            " leading",
            "trailing ",
            "two  spaces",
            "tab\tvalue",
            "line\nvalue",
            "non\u00a0breaking",
            "format\u200bmark",
            "line\u2028separator",
            "paragraph\u2029separator",
            "a\u0308",
            "surrogate\ud800",
        )
        for value in invalid_values:
            payload = self._corpus_payload()
            payload["clips"][0]["reference_text"] = value
            with self.subTest(value=value.encode("unicode_escape")):
                self._assert_semantic_invalid(payload)

        exact_codepoints = self._corpus_payload(16)
        for clip in exact_codepoints["clips"]:
            clip["reference_text"] = "a" * 4096
        requests, corpus_data = self._build_case(corpus_payload=exact_codepoints)
        self._parse(requests[0], corpus_data)

        exact_utf8 = self._corpus_payload(16)
        for clip in exact_utf8["clips"]:
            clip["reference_text"] = "\U0001f642" * 4096
        self.assertEqual(
            sum(
                len(clip["reference_text"].encode("utf-8"))
                for clip in exact_utf8["clips"]
            ),
            262_144,
        )
        requests, corpus_data = self._build_case(corpus_payload=exact_utf8)
        self.assertLessEqual(len(corpus_data), ct2_benchmark_worker.MAX_CORPUS_BYTES)
        self._parse(requests[0], corpus_data)

        overflow = copy.deepcopy(exact_codepoints)
        overflow["clips"].append(
            {
                "audio_path": "audio/clip-16.wav",
                "id": "clip-16",
                "reference_text": "x",
                "scenario": "quiet-room",
            }
        )
        self._assert_semantic_invalid(overflow)

    def test_ordering_known_answers_use_raw_digest_big_endian_and_id_tiebreak(
        self,
    ) -> None:
        clips = tuple(
            ct2_benchmark_worker._CorpusClip(
                id=f"clip-{index:02d}",
                audio_path=f"audio/clip-{index:02d}.wav",
                audio_sha256=f"{index + 1:064x}",
                audio_size=index + 1,
                reference_text="Text",
                scenario="quiet",
            )
            for index in range(12)
        )
        vectors = (
            (
                0,
                (
                    "clip-05",
                    "clip-09",
                    "clip-07",
                    "clip-10",
                    "clip-08",
                    "clip-06",
                    "clip-02",
                    "clip-04",
                    "clip-03",
                    "clip-11",
                    "clip-01",
                    "clip-00",
                ),
                "f93ea8180cdd2ed2f79ba7db81729668e728726be9b1285d3aa2eb7e252e4c91",
            ),
            (
                (1 << 64) - 1,
                (
                    "clip-09",
                    "clip-02",
                    "clip-05",
                    "clip-04",
                    "clip-07",
                    "clip-10",
                    "clip-00",
                    "clip-01",
                    "clip-11",
                    "clip-08",
                    "clip-06",
                    "clip-03",
                ),
                "ab22c8cf8f352b6d21ef3a94769897b23db2da1234f6a7a243d05ecf5e71e8d9",
            ),
        )
        domain = b"SOC-CT2-CORPUS-ORDER-V1\x00"
        for seed, expected, clip_zero_digest in vectors:
            ordered = ct2_benchmark_worker._order_corpus_clips(clips, seed=seed)
            with self.subTest(seed=seed):
                self.assertEqual(tuple(value.id for value in ordered), expected)
                self.assertEqual(
                    hashlib.sha256(
                        domain
                        + seed.to_bytes(8, "big")
                        + b"\x00"
                        + b"clip-00"
                    ).hexdigest(),
                    clip_zero_digest,
                )

        with mock.patch.object(
            ct2_benchmark_worker.hashlib,
            "sha256",
            return_value=types.SimpleNamespace(digest=lambda: b"\x00" * 32),
        ):
            tied = ct2_benchmark_worker._order_corpus_clips(
                tuple(reversed(clips)),
                seed=0,
            )
        self.assertEqual(tuple(value.id for value in tied), tuple(sorted(v.id for v in clips)))

    def test_bundle_values_are_deeply_immutable_and_repr_is_redacted(self) -> None:
        requests, corpus_data = self._build_case()
        parsed = self._parse(requests[0], corpus_data)
        self.assertIs(type(parsed.corpus.clips), tuple)
        self.assertIs(type(parsed.corpus.execution_clips), tuple)
        self.assertTrue(all(type(value) is ct2_benchmark_worker._CorpusClip for value in parsed.corpus.clips))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parsed.corpus.language = "en"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            parsed.corpus.clips[0].id = "changed"
        for value in (parsed, parsed.corpus, parsed.corpus.clips[0]):
            rendered = repr(value)
            self.assertEqual(rendered, f"<{type(value).__name__} redacted>")
            for secret in (
                "clip-00",
                "audio/",
                "Beispiel",
                parsed.corpus.clips[0].audio_sha256,
            ):
                self.assertNotIn(secret, rendered)

    def test_corpus_boundary_is_fresh_redacted_and_preserves_interrupts(self) -> None:
        requests, corpus_data = self._build_case()
        for original in (
            ct2_benchmark_worker.WorkerFailure("benchmark-request-invalid"),
            ValueError("private/path"),
            SystemExit("private/path"),
        ):
            with (
                self.subTest(error=type(original).__name__),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_parse_benchmark_corpus_checked",
                    side_effect=original,
                ),
            ):
                error = self._assert_corpus_invalid(requests[0], corpus_data)
            self.assertIsNot(error, original)
            self.assertNotIn("private", str(error))

        request_error = ct2_benchmark_worker.WorkerFailure(
            "benchmark-request-invalid"
        )
        with (
            mock.patch.object(
                ct2_benchmark_worker,
                "_parse_benchmark_arm_request",
                side_effect=request_error,
            ),
            mock.patch.object(
                ct2_benchmark_worker,
                "_parse_benchmark_corpus_checked",
            ) as corpus_parser,
            self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised,
        ):
            self._parse(requests[0], corpus_data)
        self.assertIs(raised.exception, request_error)
        corpus_parser.assert_not_called()

        for stage in ("request", "corpus"):
            interrupt = KeyboardInterrupt()
            target = (
                "_parse_benchmark_arm_request"
                if stage == "request"
                else "_parse_benchmark_corpus_checked"
            )
            with (
                self.subTest(stage=stage),
                mock.patch.object(
                    ct2_benchmark_worker,
                    target,
                    side_effect=interrupt,
                ),
                self.assertRaises(KeyboardInterrupt) as raised_interrupt,
            ):
                self._parse(requests[0], corpus_data)
            self.assertIs(raised_interrupt.exception, interrupt)

    def test_bundle_parser_is_pure_and_constants_match_controller(self) -> None:
        requests, corpus_data = self._build_case()
        with (
            mock.patch("builtins.open", side_effect=AssertionError("io")) as fopen,
            mock.patch.object(
                ct2_benchmark_worker.os,
                "open",
                side_effect=AssertionError("io"),
            ) as os_open,
            mock.patch.object(
                ct2_benchmark_worker.os,
                "stat",
                side_effect=AssertionError("io"),
            ) as stat_probe,
            mock.patch.object(
                ct2_benchmark_worker.os,
                "scandir",
                side_effect=AssertionError("io"),
            ) as scanner,
            mock.patch.object(
                ct2_benchmark_worker.importlib,
                "import_module",
                side_effect=AssertionError("import"),
            ) as importer,
            mock.patch.object(
                ct2_benchmark_worker,
                "_attest_artifact",
                side_effect=AssertionError("attestation"),
            ) as attester,
            mock.patch.object(
                ct2_benchmark_worker,
                "_runtime_observation",
                side_effect=AssertionError("runtime"),
            ) as runtime,
        ):
            self._parse(requests[0], corpus_data)
        for probe in (
            fopen,
            os_open,
            stat_probe,
            scanner,
            importer,
            attester,
            runtime,
        ):
            probe.assert_not_called()
        self.assertEqual(
            ct2_benchmark_worker.MAX_CORPUS_BYTES,
            ct2_benchmark.MAX_CORPUS_BYTES,
        )
        self.assertIn(
            "benchmark-corpus-invalid",
            ct2_benchmark_worker.WORKER_ERROR_CODES,
        )
        for forbidden in ("subprocess", "WhisperModel"):
            self.assertNotIn(forbidden, ct2_benchmark_worker.__dict__)


class Ctranslate2BenchmarkAudioReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bundle_cases = Ctranslate2BenchmarkArmBundleTests()

    def _bundle_for_root(
        self,
        root: Path,
        *,
        mode: str = "full",
    ) -> tuple[ct2_benchmark_worker._BenchmarkArmBundle, bytes, Path]:
        requests, corpus_data = self.bundle_cases._build_case(mode=mode)
        payload = json.loads(requests[0])
        payload["clips"]["root"] = str(root)
        request_data = self.bundle_cases._encoded(payload)
        bundle = ct2_benchmark_worker._parse_benchmark_arm_bundle(
            request_data,
            corpus_data,
            expected_nonce=self.bundle_cases._NONCES[0],
        )
        clip = bundle.corpus.execution_clips[0]
        suffix = clip.id.removeprefix("clip-")
        contents = f"audio-{suffix}".encode("ascii")
        return bundle, contents, root / clip.audio_path

    @staticmethod
    def _write_audio(root: Path, target: Path, contents: bytes) -> None:
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        target.parent.mkdir(mode=0o700, parents=True)
        target.parent.chmod(0o700)
        target.write_bytes(contents)
        target.chmod(0o600)

    def _ready_fixture(
        self,
        temporary: str,
        *,
        mode: str = "full",
    ) -> tuple[ct2_benchmark_worker._BenchmarkArmBundle, bytes, Path, Path]:
        root = Path(temporary) / "clips"
        bundle, contents, target = self._bundle_for_root(root, mode=mode)
        self._write_audio(root, target, contents)
        return bundle, contents, root, target

    def _assert_audio_invalid(
        self,
        bundle: object,
        *,
        execution_index: object = 0,
    ) -> ct2_benchmark_worker.WorkerFailure:
        with self.assertRaises(ct2_benchmark_worker.WorkerFailure) as raised:
            ct2_benchmark_worker._read_attested_corpus_audio(
                bundle,
                execution_index=execution_index,
            )
        self.assertEqual(raised.exception.code, "benchmark-audio-invalid")
        self.assertEqual(raised.exception.args, ("benchmark-audio-invalid",))
        self.assertIsNone(raised.exception.__cause__)
        self.assertIsNone(raised.exception.__context__)
        return raised.exception

    def test_audio_read_returns_exact_bytes_once_and_closes_all_descriptors(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, _target = self._ready_fixture(temporary)
            real_open = os.open
            real_pread = os.pread
            real_sha256 = hashlib.sha256
            opened: list[int] = []

            def tracking_open(*args: object, **kwargs: object) -> int:
                descriptor = real_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            with (
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "open",
                    side_effect=tracking_open,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "pread",
                    wraps=real_pread,
                ) as reader,
                mock.patch.object(
                    ct2_benchmark_worker.hashlib,
                    "sha256",
                    wraps=real_sha256,
                ) as hasher,
                mock.patch.object(
                    ct2_benchmark_worker.importlib,
                    "import_module",
                    side_effect=AssertionError("runtime import"),
                ) as importer,
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_attest_artifact",
                    side_effect=AssertionError("attestation"),
                ) as attester,
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_runtime_observation",
                    side_effect=AssertionError("runtime"),
                ) as runtime,
            ):
                result = ct2_benchmark_worker._read_attested_corpus_audio(
                    bundle,
                    execution_index=0,
                )

            self.assertIs(type(result), bytes)
            self.assertEqual(result, expected)
            self.assertEqual(
                [(call.args[1], call.args[2]) for call in reader.call_args_list],
                [(len(expected), 0), (1, len(expected))],
            )
            hasher.assert_called_once_with(result)
            importer.assert_not_called()
            attester.assert_not_called()
            runtime.assert_not_called()
            self.assertNotIn("subprocess", ct2_benchmark_worker.__dict__)
            for descriptor in set(opened):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_audio_file_and_selected_total_caps_are_inclusive(self) -> None:
        for mode in ("full", "quick"):
            with tempfile.TemporaryDirectory() as temporary:
                bundle, expected, _root, _target = self._ready_fixture(
                    temporary,
                    mode=mode,
                )
                selected_total = sum(
                    value.audio_size
                    for value in bundle.corpus.execution_clips
                )
                with (
                    self.subTest(mode=mode, boundary="file-exact"),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "MAX_CORPUS_AUDIO_FILE_BYTES",
                        len(expected),
                    ),
                ):
                    self.assertEqual(
                        ct2_benchmark_worker._read_attested_corpus_audio(
                            bundle,
                            execution_index=0,
                        ),
                        expected,
                    )
                with (
                    self.subTest(mode=mode, boundary="file-plus-one"),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "MAX_CORPUS_AUDIO_FILE_BYTES",
                        len(expected) - 1,
                    ),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "_open_bound_root",
                    ) as opener,
                ):
                    self._assert_audio_invalid(bundle)
                opener.assert_not_called()

                with (
                    self.subTest(mode=mode, boundary="selected-exact"),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "MAX_SELECTED_CORPUS_AUDIO_BYTES",
                        selected_total,
                    ),
                ):
                    self.assertEqual(
                        ct2_benchmark_worker._read_attested_corpus_audio(
                            bundle,
                            execution_index=0,
                        ),
                        expected,
                    )
                with (
                    self.subTest(mode=mode, boundary="selected-plus-one"),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "MAX_SELECTED_CORPUS_AUDIO_BYTES",
                        selected_total - 1,
                    ),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "_open_bound_root",
                    ) as opener,
                ):
                    self._assert_audio_invalid(bundle)
                opener.assert_not_called()

    def test_audio_read_rejects_short_trailing_hash_and_snapshot_changes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, target = self._ready_fixture(temporary)
            first = expected[:1]
            remainder = expected[1:]
            with mock.patch.object(
                ct2_benchmark_worker.os,
                "pread",
                side_effect=[first, remainder, b""],
            ) as reader:
                self.assertEqual(
                    ct2_benchmark_worker._read_attested_corpus_audio(
                        bundle,
                        execution_index=0,
                    ),
                    expected,
                )
            self.assertEqual(
                [call.args[1:] for call in reader.call_args_list],
                [
                    (len(expected), 0),
                    (len(expected) - 1, 1),
                    (1, len(expected)),
                ],
            )

        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, _target = self._ready_fixture(temporary)
            real_pread = os.pread
            with (
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_HASH_CHUNK_BYTES",
                    3,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "pread",
                    wraps=real_pread,
                ) as reader,
            ):
                self.assertEqual(
                    ct2_benchmark_worker._read_attested_corpus_audio(
                        bundle,
                        execution_index=0,
                    ),
                    expected,
                )
            expected_reads = [
                (min(3, len(expected) - offset), offset)
                for offset in range(0, len(expected), 3)
            ]
            expected_reads.append((1, len(expected)))
            self.assertEqual(
                [call.args[1:] for call in reader.call_args_list],
                expected_reads,
            )

        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, _target = self._ready_fixture(temporary)
            for chunks in (
                [b""],
                [b"x" * (len(expected) + 1)],
            ):
                with (
                    self.subTest(chunks=tuple(map(len, chunks))),
                    mock.patch.object(
                        ct2_benchmark_worker.os,
                        "pread",
                        side_effect=chunks,
                    ),
                ):
                    self._assert_audio_invalid(bundle)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            with (
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_MAX_SHORT_READS",
                    1,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "pread",
                    side_effect=[b"x", b"y"],
                ) as reader,
            ):
                self._assert_audio_invalid(bundle)
            self.assertEqual(reader.call_count, 2)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            with (
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_MAX_PREAD_INTERRUPTS",
                    1,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "pread",
                    side_effect=[InterruptedError, InterruptedError],
                ) as reader,
            ):
                self._assert_audio_invalid(bundle)
            self.assertEqual(reader.call_count, 2)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, _target = self._ready_fixture(temporary)
            with mock.patch.object(
                ct2_benchmark_worker.os,
                "pread",
                side_effect=[expected, b"x"],
            ):
                self._assert_audio_invalid(bundle)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, target = self._ready_fixture(temporary)
            target.write_bytes(b"X" * len(expected))
            target.chmod(0o600)
            self._assert_audio_invalid(bundle)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            real_snapshot = ct2_benchmark_worker._file_snapshot
            snapshots = 0

            def changed_snapshot(*args: object, **kwargs: object) -> object:
                nonlocal snapshots
                value = real_snapshot(*args, **kwargs)
                snapshots += 1
                if snapshots == 2:
                    return dataclasses.replace(
                        value,
                        changed_ns=value.changed_ns + 1,
                    )
                return value

            with mock.patch.object(
                ct2_benchmark_worker,
                "_file_snapshot",
                side_effect=changed_snapshot,
            ):
                self._assert_audio_invalid(bundle)
            self.assertEqual(snapshots, 2)

    def test_audio_read_rejects_symlink_fifo_hardlink_and_mode_violations(
        self,
    ) -> None:
        variants = ("symlink", "fifo", "hardlink", "root-mode", "dir-mode", "file-mode")
        for variant in variants:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "clips"
                bundle, expected, target = self._bundle_for_root(root)
                if variant == "fifo":
                    root.mkdir(mode=0o700)
                    target.parent.mkdir(mode=0o700, parents=True)
                    os.mkfifo(target, mode=0o600)
                elif variant == "symlink":
                    root.mkdir(mode=0o700)
                    target.parent.mkdir(mode=0o700, parents=True)
                    outside = Path(temporary) / "outside.wav"
                    outside.write_bytes(expected)
                    outside.chmod(0o600)
                    target.symlink_to(outside)
                else:
                    self._write_audio(root, target, expected)
                    if variant == "hardlink":
                        os.link(target, root / "second-link.wav")
                    elif variant == "root-mode":
                        root.chmod(0o750)
                    elif variant == "dir-mode":
                        target.parent.chmod(0o750)
                    elif variant == "file-mode":
                        target.chmod(0o640)
                with self.subTest(variant=variant):
                    self._assert_audio_invalid(bundle)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            real_snapshot = ct2_benchmark_worker._file_snapshot

            def foreign_device(
                descriptor: int,
                declaration: object,
                *,
                user_id: int,
                device: int,
                error_code: str,
            ) -> object:
                return real_snapshot(
                    descriptor,
                    declaration,
                    user_id=user_id,
                    device=device + 1,
                    error_code=error_code,
                )

            with mock.patch.object(
                ct2_benchmark_worker,
                "_file_snapshot",
                side_effect=foreign_device,
            ):
                self._assert_audio_invalid(bundle)

    def test_audio_failures_close_every_descriptor_and_preserve_first_interrupt(
        self,
    ) -> None:
        for failure in (
            OSError("private read"),
            ValueError("private read"),
            SystemExit("private read"),
        ):
            with tempfile.TemporaryDirectory() as temporary:
                bundle, _expected, _root, _target = self._ready_fixture(temporary)
                real_open = os.open
                opened: list[int] = []

                def tracking_open(*args: object, **kwargs: object) -> int:
                    descriptor = real_open(*args, **kwargs)
                    opened.append(descriptor)
                    return descriptor

                with (
                    self.subTest(failure=type(failure).__name__),
                    mock.patch.object(
                        ct2_benchmark_worker.os,
                        "open",
                        side_effect=tracking_open,
                    ),
                    mock.patch.object(
                        ct2_benchmark_worker.os,
                        "pread",
                        side_effect=failure,
                    ),
                ):
                    error = self._assert_audio_invalid(bundle)
                self.assertNotIn("private", str(error))
                for descriptor in set(opened):
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            real_open = os.open
            real_close = os.close
            opened: list[int] = []
            active: set[int] = set()
            closed: list[int] = []
            unsafe_closes: list[object] = []
            close_failed = False

            def tracking_open(*args: object, **kwargs: object) -> int:
                descriptor = real_open(*args, **kwargs)
                self.assertGreater(descriptor, 2)
                self.assertNotIn(descriptor, active)
                opened.append(descriptor)
                active.add(descriptor)
                return descriptor

            def close_then_fail(descriptor: int) -> None:
                nonlocal close_failed
                if (
                    type(descriptor) is not int
                    or descriptor <= 2
                    or descriptor not in active
                ):
                    unsafe_closes.append(descriptor)
                    return
                active.remove(descriptor)
                closed.append(descriptor)
                real_close(descriptor)
                if not close_failed:
                    close_failed = True
                    raise OSError("private close")

            with (
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "open",
                    side_effect=tracking_open,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "close",
                    side_effect=close_then_fail,
                ),
            ):
                self._assert_audio_invalid(bundle)
            self.assertTrue(close_failed)
            self.assertEqual(unsafe_closes, [])
            self.assertEqual(active, set())
            self.assertEqual(sorted(closed), sorted(opened))
            for descriptor in set(opened):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            real_open = os.open
            real_close = os.close
            opened: list[int] = []
            active: set[int] = set()
            closed: list[int] = []
            unsafe_closes: list[object] = []
            interrupt = KeyboardInterrupt()
            interrupt_seen = False

            def tracking_open(*args: object, **kwargs: object) -> int:
                descriptor = real_open(*args, **kwargs)
                self.assertGreater(descriptor, 2)
                self.assertNotIn(descriptor, active)
                opened.append(descriptor)
                active.add(descriptor)
                return descriptor

            def interrupting_pread(*_args: object) -> bytes:
                nonlocal interrupt_seen
                interrupt_seen = True
                raise interrupt

            def close_then_fail(descriptor: int) -> None:
                if (
                    type(descriptor) is not int
                    or descriptor <= 2
                    or descriptor not in active
                ):
                    unsafe_closes.append(descriptor)
                    return
                active.remove(descriptor)
                closed.append(descriptor)
                real_close(descriptor)
                if interrupt_seen:
                    raise OSError("private close")

            with (
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "open",
                    side_effect=tracking_open,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "pread",
                    side_effect=interrupting_pread,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "close",
                    side_effect=close_then_fail,
                ),
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                ct2_benchmark_worker._read_attested_corpus_audio(
                    bundle,
                    execution_index=0,
                )
            self.assertIs(raised.exception, interrupt)
            self.assertEqual(unsafe_closes, [])
            self.assertEqual(active, set())
            self.assertEqual(sorted(closed), sorted(opened))
            for descriptor in set(opened):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            real_open = os.open
            real_close = os.close
            opened = []
            active: set[int] = set()
            closed: list[int] = []
            unsafe_closes: list[object] = []
            first_interrupt = KeyboardInterrupt()
            later_interrupt = KeyboardInterrupt()
            cleanup_interrupts = 0
            read_failed = False

            def tracking_open(*args: object, **kwargs: object) -> int:
                descriptor = real_open(*args, **kwargs)
                self.assertGreater(descriptor, 2)
                self.assertNotIn(descriptor, active)
                opened.append(descriptor)
                active.add(descriptor)
                return descriptor

            def failing_pread(*_args: object) -> bytes:
                nonlocal read_failed
                read_failed = True
                raise OSError("private read")

            def interrupting_close(descriptor: int) -> None:
                nonlocal cleanup_interrupts
                if (
                    type(descriptor) is not int
                    or descriptor <= 2
                    or descriptor not in active
                ):
                    unsafe_closes.append(descriptor)
                    return
                active.remove(descriptor)
                closed.append(descriptor)
                real_close(descriptor)
                if read_failed:
                    cleanup_interrupts += 1
                    if cleanup_interrupts == 1:
                        raise first_interrupt
                    raise later_interrupt

            with (
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "open",
                    side_effect=tracking_open,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "pread",
                    side_effect=failing_pread,
                ),
                mock.patch.object(
                    ct2_benchmark_worker.os,
                    "close",
                    side_effect=interrupting_close,
                ),
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                ct2_benchmark_worker._read_attested_corpus_audio(
                    bundle,
                    execution_index=0,
                )
            self.assertIs(raised.exception, first_interrupt)
            self.assertGreaterEqual(cleanup_interrupts, 2)
            self.assertEqual(unsafe_closes, [])
            self.assertEqual(active, set())
            self.assertEqual(sorted(closed), sorted(opened))
            for descriptor in set(opened):
                with self.assertRaises(OSError):
                    os.fstat(descriptor)

    def test_audio_preflight_rejects_indexes_caps_and_forged_bindings_before_io(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)

            class BundleSubclass(ct2_benchmark_worker._BenchmarkArmBundle):
                pass

            subclass = BundleSubclass(bundle.request, bundle.corpus)
            selected = bundle.corpus.execution_clips[0]
            forged_clip = dataclasses.replace(
                selected,
                audio_sha256="f" * 64,
            )
            forged_clips = tuple(
                forged_clip if value is selected else value
                for value in bundle.corpus.clips
            )
            forged_execution = tuple(
                forged_clip if value is selected else value
                for value in bundle.corpus.execution_clips
            )
            forged_corpus = dataclasses.replace(
                bundle.corpus,
                clips=forged_clips,
                execution_clips=forged_execution,
            )
            forged_bundle = dataclasses.replace(bundle, corpus=forged_corpus)

            declaration = next(
                value
                for value in bundle.request.clips.manifest.files
                if value.path == selected.audio_path
            )
            duplicate_manifest = dataclasses.replace(
                bundle.request.clips.manifest,
                files=(*bundle.request.clips.manifest.files, declaration),
            )
            duplicate_artifact = dataclasses.replace(
                bundle.request.clips,
                manifest=duplicate_manifest,
            )
            duplicate_request = dataclasses.replace(
                bundle.request,
                clips=duplicate_artifact,
            )
            duplicate_bundle = dataclasses.replace(
                bundle,
                request=duplicate_request,
            )

            traversal_clip = dataclasses.replace(
                selected,
                audio_path="../escape.wav",
            )
            traversal_clips = tuple(
                traversal_clip if value is selected else value
                for value in bundle.corpus.clips
            )
            traversal_execution = tuple(
                traversal_clip if value is selected else value
                for value in bundle.corpus.execution_clips
            )
            traversal_declaration = dataclasses.replace(
                declaration,
                path="../escape.wav",
            )
            traversal_manifest = dataclasses.replace(
                bundle.request.clips.manifest,
                files=tuple(
                    traversal_declaration if value is declaration else value
                    for value in bundle.request.clips.manifest.files
                ),
            )
            traversal_bundle = dataclasses.replace(
                bundle,
                request=dataclasses.replace(
                    bundle.request,
                    clips=dataclasses.replace(
                        bundle.request.clips,
                        manifest=traversal_manifest,
                    ),
                ),
                corpus=dataclasses.replace(
                    bundle.corpus,
                    clips=traversal_clips,
                    execution_clips=traversal_execution,
                ),
            )

            cases = (
                (object(), 0),
                (subclass, 0),
                (bundle, True),
                (bundle, -1),
                (bundle, len(bundle.corpus.execution_clips)),
                (forged_bundle, 0),
                (duplicate_bundle, 0),
                (traversal_bundle, 0),
            )
            for candidate, index in cases:
                with (
                    self.subTest(
                        bundle_type=type(candidate).__name__,
                        index=index,
                    ),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "_open_bound_root",
                        return_value=-1,
                    ) as opener,
                ):
                    self._assert_audio_invalid(
                        candidate,
                        execution_index=index,
                    )
                opener.assert_not_called()

            quick_temporary = tempfile.mkdtemp(dir=temporary)
            quick_bundle, _expected, _root, _target = self._ready_fixture(
                quick_temporary,
                mode="quick",
            )
            unselected = quick_bundle.corpus.clips[-1]
            oversized = dataclasses.replace(
                unselected,
                audio_size=9,
            )
            clips = tuple(
                oversized if value is unselected else value
                for value in quick_bundle.corpus.clips
            )
            self.assertIs(clips[-1], oversized)
            self.assertNotIn(oversized, quick_bundle.corpus.execution_clips)
            forged_corpus = dataclasses.replace(quick_bundle.corpus, clips=clips)
            unselected_declaration = next(
                value
                for value in quick_bundle.request.clips.manifest.files
                if value.path == unselected.audio_path
            )
            different_hash = (
                ("0" if unselected_declaration.sha256[0] != "0" else "1")
                + unselected_declaration.sha256[1:]
            )
            mismatched_declaration = dataclasses.replace(
                unselected_declaration,
                sha256=different_hash,
            )
            mismatched_manifest = dataclasses.replace(
                quick_bundle.request.clips.manifest,
                files=tuple(
                    mismatched_declaration
                    if value is unselected_declaration
                    else value
                    for value in quick_bundle.request.clips.manifest.files
                ),
            )
            mismatched_bundle = dataclasses.replace(
                quick_bundle,
                request=dataclasses.replace(
                    quick_bundle.request,
                    clips=dataclasses.replace(
                        quick_bundle.request.clips,
                        manifest=mismatched_manifest,
                    ),
                ),
            )
            with mock.patch.object(
                ct2_benchmark_worker,
                "_open_bound_root",
                return_value=-1,
            ) as opener:
                self._assert_audio_invalid(mismatched_bundle)
            opener.assert_not_called()

            oversized_declaration = dataclasses.replace(
                unselected_declaration,
                size=9,
            )
            oversized_manifest = dataclasses.replace(
                quick_bundle.request.clips.manifest,
                files=tuple(
                    oversized_declaration
                    if value is unselected_declaration
                    else value
                    for value in quick_bundle.request.clips.manifest.files
                ),
            )
            forged_bundle = dataclasses.replace(
                quick_bundle,
                request=dataclasses.replace(
                    quick_bundle.request,
                    clips=dataclasses.replace(
                        quick_bundle.request.clips,
                        manifest=oversized_manifest,
                    ),
                ),
                corpus=forged_corpus,
            )
            with (
                mock.patch.object(
                    ct2_benchmark_worker,
                    "MAX_CORPUS_AUDIO_FILE_BYTES",
                    8,
                ),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_open_bound_root",
                ) as opener,
            ):
                self._assert_audio_invalid(forged_bundle)
            opener.assert_not_called()

    def test_audio_preflight_rejects_noncanonical_execution_order_and_shape(
        self,
    ) -> None:
        for mode in ("full", "quick"):
            with tempfile.TemporaryDirectory() as temporary:
                bundle, _expected, _root, _target = self._ready_fixture(
                    temporary,
                    mode=mode,
                )
                clips = bundle.corpus.clips
                wrong_shape = clips[:3] if mode == "full" else clips
                reordered_clips = (clips[1], clips[0], *clips[2:])
                reordered_execution = (
                    reordered_clips
                    if mode == "full"
                    else reordered_clips[:3]
                )
                cases = (
                    dataclasses.replace(
                        bundle,
                        corpus=dataclasses.replace(
                            bundle.corpus,
                            execution_clips=wrong_shape,
                        ),
                    ),
                    dataclasses.replace(
                        bundle,
                        corpus=dataclasses.replace(
                            bundle.corpus,
                            clips=reordered_clips,
                            execution_clips=reordered_execution,
                        ),
                    ),
                )
                for candidate in cases:
                    with (
                        self.subTest(mode=mode, shape=len(candidate.corpus.execution_clips)),
                        mock.patch.object(
                            ct2_benchmark_worker,
                            "_open_bound_root",
                            return_value=-1,
                        ) as opener,
                    ):
                        self._assert_audio_invalid(candidate)
                    opener.assert_not_called()

    def test_audio_directory_second_snapshot_change_is_causal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            real_snapshot = ct2_benchmark_worker._directory_snapshot
            root_descriptor: int | None = None
            root_calls = 0
            exact_calls = 0

            def changed_root_snapshot(
                descriptor: int,
                **kwargs: object,
            ) -> object:
                nonlocal exact_calls, root_calls, root_descriptor
                value = real_snapshot(descriptor, **kwargs)
                if kwargs.get("exact_mode") is True:
                    exact_calls += 1
                    if root_descriptor is None:
                        root_descriptor = descriptor
                    if descriptor == root_descriptor:
                        root_calls += 1
                        if root_calls == 2:
                            return dataclasses.replace(
                                value,
                                changed_ns=value.changed_ns + 1,
                            )
                return value

            with mock.patch.object(
                ct2_benchmark_worker,
                "_directory_snapshot",
                side_effect=changed_root_snapshot,
            ):
                self._assert_audio_invalid(bundle)
            self.assertEqual(root_calls, 2)
            self.assertEqual(
                exact_calls,
                bundle.corpus.execution_clips[0].audio_path.count("/") + 2,
            )

    def test_audio_uid_and_descriptor_slots_fail_before_child_ownership(
        self,
    ) -> None:
        for invalid_user_id in (True, 1.0, -1):
            with tempfile.TemporaryDirectory() as temporary:
                bundle, _expected, _root, _target = self._ready_fixture(temporary)
                with (
                    self.subTest(user_id=invalid_user_id),
                    mock.patch.object(
                        ct2_benchmark_worker.os,
                        "geteuid",
                        return_value=invalid_user_id,
                    ),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "_directory_snapshot",
                        side_effect=lambda descriptor, **_kwargs: (
                            ct2_benchmark_worker._snapshot(descriptor)
                        ),
                    ),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "_open_child",
                        side_effect=AssertionError("child I/O"),
                    ) as child_opener,
                ):
                    self._assert_audio_invalid(bundle)
                child_opener.assert_not_called()

        with tempfile.TemporaryDirectory() as temporary:
            bundle, expected, _root, _target = self._ready_fixture(temporary)

            class PoisonAppendSlots(list[object]):
                def append(self, _value: object) -> None:
                    raise AssertionError("descriptor append")

            slot_count = (
                len(bundle.corpus.execution_clips[0].audio_path.split("/")) + 1
            )
            with mock.patch.object(
                ct2_benchmark_worker,
                "_new_audio_descriptor_slots",
                side_effect=lambda count: PoisonAppendSlots([None] * count),
            ) as allocator:
                self.assertEqual(
                    ct2_benchmark_worker._read_attested_corpus_audio(
                        bundle,
                        execution_index=0,
                    ),
                    expected,
                )
            allocator.assert_called_once_with(slot_count)

        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            with (
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_new_audio_descriptor_slots",
                    side_effect=MemoryError,
                ),
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_open_bound_root",
                ) as opener,
            ):
                self._assert_audio_invalid(bundle)
            opener.assert_not_called()

    def test_audio_public_boundary_is_fresh_redacted_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, _expected, _root, _target = self._ready_fixture(temporary)
            for failure in (
                ct2_benchmark_worker.WorkerFailure("artifact-tree-mismatch"),
                ValueError("private/path"),
                SystemExit("private/path"),
            ):
                with (
                    self.subTest(failure=type(failure).__name__),
                    mock.patch.object(
                        ct2_benchmark_worker,
                        "_read_attested_corpus_audio_checked",
                        side_effect=failure,
                    ),
                ):
                    error = self._assert_audio_invalid(bundle)
                self.assertIsNot(error, failure)
                self.assertNotIn("private", str(error))

            with mock.patch.object(
                ct2_benchmark_worker,
                "_read_attested_corpus_audio_checked",
                return_value=bytearray(b"forged"),
            ):
                self._assert_audio_invalid(bundle)

            interrupt = KeyboardInterrupt()
            with (
                mock.patch.object(
                    ct2_benchmark_worker,
                    "_read_attested_corpus_audio_checked",
                    side_effect=interrupt,
                ),
                self.assertRaises(KeyboardInterrupt) as raised,
            ):
                ct2_benchmark_worker._read_attested_corpus_audio(
                    bundle,
                    execution_index=0,
                )
            self.assertIs(raised.exception, interrupt)

        self.assertEqual(
            ct2_benchmark_worker.MAX_CORPUS_AUDIO_FILE_BYTES,
            32 * 1024 * 1024,
        )
        self.assertEqual(
            ct2_benchmark_worker.MAX_SELECTED_CORPUS_AUDIO_BYTES,
            256 * 1024 * 1024,
        )
        self.assertIn(
            "benchmark-audio-invalid",
            ct2_benchmark_worker.WORKER_ERROR_CODES,
        )


if __name__ == "__main__":
    unittest.main()
