from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import command_chain, ct2_benchmark


class Ctranslate2BenchmarkProtocolTests(unittest.TestCase):
    _NONCE = "b" * 64
    _SPEC = ct2_benchmark._RuntimeSpec(
        interpreter="/opt/soc/python",
        site_packages="/opt/soc/site-packages",
        expected_ctranslate2_version="4.7.2",
        expected_faster_whisper_version="1.2.1",
    )

    def _success_payload(self, **changes: object) -> dict[str, object]:
        runtime: dict[str, object] = {
            "abi": "cpython-313-x86_64-linux-gnu",
            "ctranslate2_version": "4.7.2",
            "faster_whisper_version": "1.2.1",
            "implementation": "CPython",
            "python_version": "3.13.7",
            "supported_cpu_compute_types": ["float32", "int8"],
        }
        payload: dict[str, object] = {
            "mode": "probe-runtime",
            "nonce": self._NONCE,
            "runtime": runtime,
            "schema_version": 1,
            "status": "ok",
        }
        for key, value in changes.items():
            if key.startswith("runtime_"):
                runtime[key.removeprefix("runtime_")] = value
            else:
                payload[key] = value
        return payload

    @staticmethod
    def _encoded(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    def _run_mocked(
        self,
        *,
        stdout: object | None = None,
        stderr: object = b"",
        returncode: object = 0,
        runner_effect: BaseException | None = None,
        low_qos_effect: BaseException | None = None,
    ) -> tuple[object, mock.Mock, mock.Mock]:
        if stdout is None:
            stdout = self._encoded(self._success_payload())
        low_qos = mock.Mock(
            side_effect=low_qos_effect
            if low_qos_effect is not None
            else lambda argv: ["/low/scope", *argv]
        )
        runner = mock.Mock()
        if runner_effect is not None:
            runner.side_effect = runner_effect
        else:
            runner.return_value = (returncode, stdout, stderr)
        with (
            mock.patch.object(
                ct2_benchmark.secrets,
                "token_hex",
                return_value=self._NONCE,
            ),
            mock.patch.object(
                ct2_benchmark,
                "_worker_path",
                return_value="/repo/ct2_benchmark_worker.py",
            ),
            mock.patch.object(ct2_benchmark, "local_model_command", low_qos),
            mock.patch.object(
                ct2_benchmark,
                "run_process_bounded_output",
                runner,
            ),
            mock.patch.object(ct2_benchmark.time, "monotonic", return_value=100.0),
        ):
            try:
                result: object = ct2_benchmark.observe_runtime(self._SPEC)
            except BaseException as exc:
                result = exc
        return result, low_qos, runner

    def test_exact_stdout_argv_low_qos_and_runner_contract(self) -> None:
        result, low_qos, runner = self._run_mocked()

        self.assertEqual(
            result,
            ct2_benchmark.RuntimeObservation(
                python_version="3.13.7",
                implementation="CPython",
                abi="cpython-313-x86_64-linux-gnu",
                ctranslate2_version="4.7.2",
                faster_whisper_version="1.2.1",
                supported_cpu_compute_types=("float32", "int8"),
            ),
        )
        runtime_argv = [
            "/opt/soc/python",
            "-I",
            "-S",
            "-B",
            "/repo/ct2_benchmark_worker.py",
            "--probe-runtime",
            "--site-packages",
            "/opt/soc/site-packages",
            "--nonce",
            self._NONCE,
        ]
        low_qos.assert_called_once_with(runtime_argv)
        runner.assert_called_once_with(
            ["/low/scope", *runtime_argv],
            timeout_seconds=15,
            max_output_bytes=ct2_benchmark.MAX_RESULT_BYTES,
            env={
                "HF_HUB_OFFLINE": "1",
                "LANG": "C",
                "LC_ALL": "C",
                "TRANSFORMERS_OFFLINE": "1",
            },
            label="CTranslate2 benchmark runtime observation",
            deadline=115.0,
            preserve_user_systemd_environment=True,
        )
        for removed in ("socket", "signal", "struct", "_receive_one"):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, ct2_benchmark.__dict__)

    def test_stdout_framing_and_runner_failures_are_closed_and_redacted(self) -> None:
        payload = self._encoded(self._success_payload())
        malformed = (
            b"{}{}",
            b"prefix" + payload,
            payload + b"suffix",
            payload + payload,
            payload + b"\n",
            json.dumps(self._success_payload()).encode("ascii"),
            b'{"nonce":"a","nonce":"b"}',
            b'{"value":NaN}',
            b"\xff",
        )
        cases: tuple[tuple[str, dict[str, object]], ...] = (
            ("result-missing", {"stdout": b""}),
            ("auxiliary-output", {"stderr": b"private stderr"}),
            ("result-oversized", {"stdout": b"x" * (ct2_benchmark.MAX_RESULT_BYTES + 1)}),
            *(("result-invalid", {"stdout": value}) for value in malformed),
            ("runner-failed", {"stdout": "not-bytes"}),
            ("runner-failed", {"stderr": "not-bytes"}),
            (
                "runner-failed",
                {"runner_effect": TimeoutError("private timeout")},
            ),
            (
                "low-qos-failed",
                {"low_qos_effect": RuntimeError("private low qos")},
            ),
        )
        for expected_code, arguments in cases:
            with self.subTest(expected_code=expected_code, arguments=tuple(arguments)):
                result, _low_qos, runner = self._run_mocked(**arguments)
                self.assertIsInstance(result, ct2_benchmark.RuntimeProbeError)
                self.assertEqual(result.code, expected_code)
                self.assertEqual(str(result), expected_code)
                self.assertNotIn("private", str(result))
                if expected_code == "low-qos-failed":
                    runner.assert_not_called()

    def test_schema_nonce_status_returncode_and_versions_are_strict(self) -> None:
        worker_error = {
            "error_code": "runtime-import",
            "nonce": self._NONCE,
            "schema_version": 1,
            "status": "error",
        }
        cases = (
            (
                "result-mismatch",
                self._success_payload(schema_version=True),
                0,
            ),
            (
                "result-mismatch",
                self._success_payload(schema_version=1.0),
                0,
            ),
            ("result-mismatch", self._success_payload(schema_version=2), 0),
            ("result-mismatch", self._success_payload(nonce="c" * 64), 0),
            ("worker-exit", self._success_payload(), 7),
            ("worker-exit", self._success_payload(), False),
            (
                "version-mismatch",
                self._success_payload(runtime_ctranslate2_version="4.8.0"),
                0,
            ),
            ("worker-error", worker_error, 65),
            ("worker-exit", worker_error, 0),
        )
        for expected_code, candidate, returncode in cases:
            with self.subTest(expected_code=expected_code, returncode=returncode):
                result, _low_qos, _runner = self._run_mocked(
                    stdout=self._encoded(candidate),
                    returncode=returncode,
                )
                self.assertIsInstance(result, ct2_benchmark.RuntimeProbeError)
                self.assertEqual(result.code, expected_code)

    def test_untrusted_member_types_are_rejected_without_type_errors(self) -> None:
        cases = [
            (
                {
                    "error_code": error_code,
                    "nonce": self._NONCE,
                    "schema_version": 1,
                    "status": "error",
                },
                65,
            )
            for error_code in ([], {})
        ]
        cases.extend(
            (
                self._success_payload(
                    runtime_supported_cpu_compute_types=compute_types
                ),
                0,
            )
            for compute_types in (["int8", 1], ["int8", []], ["int8", {}])
        )

        for payload, returncode in cases:
            with self.subTest(payload=payload):
                result, _low_qos, _runner = self._run_mocked(
                    stdout=self._encoded(payload),
                    returncode=returncode,
                )

                self.assertIsInstance(result, ct2_benchmark.RuntimeProbeError)
                self.assertEqual(result.code, "result-invalid")

    def test_parser_backstop_redacts_unexpected_payload_exception(self) -> None:
        with mock.patch.object(
            ct2_benchmark,
            "_runtime_text",
            side_effect=TypeError("private parser detail"),
        ):
            with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                ct2_benchmark._parse_observation(
                    self._encoded(self._success_payload()),
                    nonce=self._NONCE,
                    spec=self._SPEC,
                    returncode=0,
                )

        self.assertEqual(raised.exception.code, "result-invalid")
        self.assertNotIn("private", str(raised.exception))

    def test_dependency_system_exit_is_redacted_but_keyboard_interrupt_propagates(
        self,
    ) -> None:
        for dependency, effect, expected_code in (
            ("low-qos", SystemExit("private low-qos detail"), "low-qos-failed"),
            ("runner", SystemExit("private runner detail"), "runner-failed"),
        ):
            with self.subTest(dependency=dependency, exception="SystemExit"):
                kwargs = (
                    {"low_qos_effect": effect}
                    if dependency == "low-qos"
                    else {"runner_effect": effect}
                )
                result, _low_qos, runner = self._run_mocked(**kwargs)

                self.assertIsInstance(result, ct2_benchmark.RuntimeProbeError)
                self.assertEqual(result.code, expected_code)
                self.assertNotIn("private", str(result))
                if dependency == "low-qos":
                    runner.assert_not_called()

        for dependency in ("low-qos", "runner"):
            with self.subTest(dependency=dependency, exception="KeyboardInterrupt"):
                interrupt = KeyboardInterrupt()
                kwargs = (
                    {"low_qos_effect": interrupt}
                    if dependency == "low-qos"
                    else {"runner_effect": interrupt}
                )
                result, _low_qos, _runner = self._run_mocked(**kwargs)

                self.assertIs(result, interrupt)

    def test_invalid_specs_fail_before_launch(self) -> None:
        invalid = (
            ct2_benchmark._RuntimeSpec(
                interpreter="python",
                site_packages=self._SPEC.site_packages,
                expected_ctranslate2_version="4.7.2",
                expected_faster_whisper_version="1.2.1",
            ),
            ct2_benchmark._RuntimeSpec(
                interpreter=self._SPEC.interpreter,
                site_packages="/opt/soc/../private",
                expected_ctranslate2_version="4.7.2",
                expected_faster_whisper_version="1.2.1",
            ),
            ct2_benchmark._RuntimeSpec(
                interpreter=self._SPEC.interpreter,
                site_packages=self._SPEC.site_packages,
                expected_ctranslate2_version="private\nversion",
                expected_faster_whisper_version="1.2.1",
            ),
        )
        for spec in invalid:
            low_qos = mock.Mock()
            runner = mock.Mock()
            with (
                self.subTest(spec=spec),
                mock.patch.object(ct2_benchmark, "local_model_command", low_qos),
                mock.patch.object(
                    ct2_benchmark,
                    "run_process_bounded_output",
                    runner,
                ),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.observe_runtime(spec)
            self.assertEqual(raised.exception.code, "invalid-spec")
            low_qos.assert_not_called()
            runner.assert_not_called()


class Ctranslate2BenchmarkPlanTests(unittest.TestCase):
    _HASH_A = "a" * 64
    _HASH_B = "b" * 64
    _HASH_CLIPS = "c" * 64
    _HASH_MODEL_ONE = "d" * 64
    _HASH_MODEL_TWO = "e" * 64

    @classmethod
    def _manifest_payload(
        cls,
        *,
        mode: object = "full",
        pair_count: object = 5,
    ) -> dict[str, object]:
        return {
            "clips": {
                "id": "clips-main",
                "manifest_sha256": cls._HASH_CLIPS,
            },
            "mode": mode,
            "models": [
                {
                    "id": "model-small",
                    "manifest_sha256": cls._HASH_MODEL_ONE,
                },
                {
                    "id": "model-medium",
                    "manifest_sha256": cls._HASH_MODEL_TWO,
                },
            ],
            "pair_count": pair_count,
            "runtimes": {
                "a": {
                    "id": "runtime-a",
                    "manifest_sha256": cls._HASH_A,
                },
                "b": {
                    "id": "runtime-b",
                    "manifest_sha256": cls._HASH_B,
                },
            },
            "schema_version": 1,
        }

    @staticmethod
    def _encoded(payload: object) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")

    def test_full_plan_is_immutable_canonical_deterministic_and_balanced(self) -> None:
        manifest = self._encoded(self._manifest_payload())

        first = ct2_benchmark.build_run_plan(manifest, seed=6)
        second = ct2_benchmark.build_run_plan(manifest, seed=6)
        encoded = ct2_benchmark.canonical_run_plan_bytes(first)

        self.assertEqual(first, second)
        self.assertEqual(encoded, ct2_benchmark.canonical_run_plan_bytes(second))
        self.assertEqual(
            encoded,
            json.dumps(
                json.loads(encoded),
                ensure_ascii=True,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii"),
        )
        self.assertLessEqual(len(encoded), ct2_benchmark.MAX_RUN_PLAN_BYTES)
        self.assertEqual(first.mode, "full")
        self.assertEqual(first.pair_count, 5)
        self.assertTrue(first.decision_eligibility_capable)
        with self.assertRaises(AttributeError):
            setattr(first, "seed", 7)

        runtime_ids = tuple(reference.artifact_id for reference in first.runtimes)
        self.assertEqual(runtime_ids, ("runtime-a", "runtime-b"))
        for block in first.model_blocks:
            with self.subTest(model=block.model.artifact_id):
                self.assertEqual(len(block.phases), 6)
                cold, *measurements = block.phases
                self.assertEqual(cold.kind, "cold")
                self.assertTrue(cold.fresh_process_per_arm)
                self.assertFalse(cold.warmup_before_measurement)
                self.assertFalse(cold.warmup_discarded)
                self.assertEqual(len(measurements), 5)
                self.assertTrue(
                    all(
                        phase.kind == "measurement"
                        and phase.fresh_process_per_arm
                        and phase.warmup_before_measurement
                        and phase.warmup_discarded
                        for phase in measurements
                    )
                )
                first_runtimes = [
                    phase.pair.runtime_order[0] for phase in measurements
                ]
                self.assertEqual(first_runtimes.count("runtime-a"), 3)
                self.assertEqual(first_runtimes.count("runtime-b"), 2)
                for phase in block.phases:
                    self.assertEqual(set(phase.pair.runtime_order), set(runtime_ids))
                    self.assertIs(type(phase.pair.clip_order_seed), int)

        different_order = ct2_benchmark.build_run_plan(manifest, seed=8)
        self.assertNotEqual(
            tuple(
                phase.pair.runtime_order
                for phase in first.model_blocks[0].phases[1:]
            ),
            tuple(
                phase.pair.runtime_order
                for phase in different_order.model_blocks[0].phases[1:]
            ),
        )

    def test_seed_selects_runtime_majority_and_quick_is_never_eligible(self) -> None:
        full = self._encoded(self._manifest_payload())
        for seed, majority in ((6, "runtime-a"), (7, "runtime-b")):
            with self.subTest(seed=seed):
                plan = ct2_benchmark.build_run_plan(full, seed=seed)
                starts = [
                    phase.pair.runtime_order[0]
                    for phase in plan.model_blocks[0].phases[1:]
                ]
                self.assertEqual(starts.count(majority), 3)

        quick = self._encoded(self._manifest_payload(mode="quick", pair_count=1))
        quick_plan = ct2_benchmark.build_run_plan(quick, seed=0)
        self.assertEqual(quick_plan.pair_count, 1)
        self.assertFalse(quick_plan.decision_eligibility_capable)
        self.assertTrue(
            all(len(block.phases) == 2 for block in quick_plan.model_blocks)
        )

    def test_manifest_shape_canonical_form_ids_hashes_and_counts_are_strict(
        self,
    ) -> None:
        valid_payload = self._manifest_payload()
        invalid_payloads: list[dict[str, object]] = []

        unknown = self._manifest_payload()
        unknown["unknown"] = True
        invalid_payloads.append(unknown)
        bool_schema = self._manifest_payload()
        bool_schema["schema_version"] = True
        invalid_payloads.append(bool_schema)
        bool_count = self._manifest_payload(pair_count=True)
        invalid_payloads.append(bool_count)
        invalid_payloads.append(self._manifest_payload(pair_count=4))
        invalid_payloads.append(self._manifest_payload(mode="quick", pair_count=2))
        invalid_hash = self._manifest_payload()
        invalid_hash["clips"]["manifest_sha256"] = "A" * 64
        invalid_payloads.append(invalid_hash)
        invalid_id = self._manifest_payload()
        invalid_id["models"][0]["id"] = "private/model"
        invalid_payloads.append(invalid_id)
        too_many_models = self._manifest_payload()
        too_many_models["models"] = [
            {"id": f"model-{index}", "manifest_sha256": "f" * 64}
            for index in range(ct2_benchmark.MAX_MODEL_BLOCKS + 1)
        ]
        invalid_payloads.append(too_many_models)

        canonical = self._encoded(valid_payload)
        malformed = [
            *(self._encoded(payload) for payload in invalid_payloads),
            canonical + b"\n",
            canonical.replace(b'{"clips":', b'{"schema_version":1,"clips":', 1),
            canonical.replace(b'"pair_count":5', b'"pair_count":NaN', 1),
            b"x" * (ct2_benchmark.MAX_EXPERIMENT_MANIFEST_BYTES + 1),
        ]
        for candidate in malformed:
            with self.subTest(candidate_length=len(candidate)):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.build_run_plan(candidate, seed=1)
                self.assertEqual(raised.exception.code, "manifest-invalid")
                self.assertEqual(str(raised.exception), "manifest-invalid")

    def test_duplicate_ids_are_rejected_separately_from_duplicate_hashes(self) -> None:
        duplicate_runtime_id = self._manifest_payload()
        duplicate_runtime_id["runtimes"]["b"]["id"] = "runtime-a"
        duplicate_model_id = self._manifest_payload()
        duplicate_model_id["models"][1]["id"] = "model-small"

        for payload in (duplicate_runtime_id, duplicate_model_id):
            with self.subTest(payload=payload):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.build_run_plan(self._encoded(payload), seed=1)
                self.assertEqual(raised.exception.code, "manifest-invalid")

    def test_distinct_ids_with_duplicate_manifest_hashes_are_rejected(self) -> None:
        duplicate_runtime_hash = self._manifest_payload()
        duplicate_runtime_hash["runtimes"]["b"]["manifest_sha256"] = self._HASH_A
        duplicate_model_hash = self._manifest_payload()
        duplicate_model_hash["models"][1]["manifest_sha256"] = (
            self._HASH_MODEL_ONE
        )

        for payload in (duplicate_runtime_hash, duplicate_model_hash):
            with self.subTest(payload=payload):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.build_run_plan(self._encoded(payload), seed=1)
                self.assertEqual(raised.exception.code, "manifest-invalid")

    def test_wire_schema_uses_ordered_phases_and_serializer_checks_values(self) -> None:
        payload = self._manifest_payload()
        payload["models"] = payload["models"][:1]
        plan = ct2_benchmark.build_run_plan(self._encoded(payload), seed=6)
        wire = json.loads(ct2_benchmark.canonical_run_plan_bytes(plan))

        self.assertEqual(
            set(wire),
            {
                "clips",
                "decision_eligibility_capable",
                "mode",
                "model_blocks",
                "pair_count",
                "runtimes",
                "schema_version",
                "seed",
            },
        )
        block = wire["model_blocks"][0]
        self.assertEqual(set(block), {"model", "phases"})
        phases = block["phases"]
        self.assertEqual([phase["kind"] for phase in phases], ["cold"] + [
            "measurement"
        ] * 5)
        for index, phase in enumerate(phases):
            with self.subTest(index=index):
                self.assertEqual(
                    set(phase),
                    {
                        "fresh_process_per_arm",
                        "kind",
                        "pair",
                        "warmup_before_measurement",
                        "warmup_discarded",
                    },
                )
                self.assertTrue(phase["fresh_process_per_arm"])
                self.assertEqual(phase["warmup_before_measurement"], index > 0)
                self.assertEqual(phase["warmup_discarded"], index > 0)
                self.assertEqual(
                    set(phase["pair"]),
                    {"clip_order_seed", "runtime_order"},
                )

        invalid_phases = (
            replace(plan.model_blocks[0].phases[0], warmup_discarded=True),
            replace(plan.model_blocks[0].phases[0], fresh_process_per_arm=1),
        )
        for invalid_phase in invalid_phases:
            invalid_block = replace(
                plan.model_blocks[0],
                phases=(invalid_phase, *plan.model_blocks[0].phases[1:]),
            )
            invalid_plan = replace(plan, model_blocks=(invalid_block,))
            with self.subTest(invalid_phase=invalid_phase):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.canonical_run_plan_bytes(invalid_plan)
                self.assertEqual(raised.exception.code, "plan-invalid")

    def test_pair_seed_known_answers_and_domains(self) -> None:
        seed = 0x0123456789ABCDEF
        vectors = (
            ("model-small", "cold", 0, 17318488996598721922),
            ("model-medium", "cold", 0, 6063226666317306793),
            ("model-small", "measurement", 0, 788756171926989713),
            ("model-small", "measurement", 1, 9969091874729765784),
        )
        actual = []
        for model_id, phase, index, expected in vectors:
            with self.subTest(model_id=model_id, phase=phase, index=index):
                value = ct2_benchmark._pair_seed(seed, model_id, phase, index)
                self.assertEqual(value, expected)
                actual.append(value)
        self.assertEqual(len(set(actual)), len(actual))

    def test_seed_is_explicit_uint64(self) -> None:
        manifest = self._encoded(self._manifest_payload())

        with self.assertRaises(TypeError):
            ct2_benchmark.build_run_plan(manifest)
        for seed in (True, False, -1, 2**64, 1.0, "1", None):
            with self.subTest(seed=seed):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.build_run_plan(manifest, seed=seed)
                self.assertEqual(raised.exception.code, "seed-invalid")

    def test_plan_preflight_does_not_open_or_execute(self) -> None:
        manifest = self._encoded(self._manifest_payload())
        with (
            mock.patch("builtins.open", side_effect=AssertionError("file open")) as fopen,
            mock.patch.object(
                ct2_benchmark.os,
                "open",
                side_effect=AssertionError("os.open"),
            ) as os_open,
            mock.patch.object(ct2_benchmark, "local_model_command") as low_qos,
            mock.patch.object(ct2_benchmark, "run_process_bounded_output") as runner,
        ):
            plan = ct2_benchmark.build_run_plan(manifest, seed=1)

        self.assertEqual(plan.seed, 1)
        fopen.assert_not_called()
        os_open.assert_not_called()
        low_qos.assert_not_called()
        runner.assert_not_called()


class Ctranslate2ArtifactManifestTests(unittest.TestCase):
    _ARTIFACT_ID = "artifact-main"
    _FILE_HASH = "a" * 64

    @classmethod
    def _payload(
        cls,
        *,
        kind: object = "model",
        files: object | None = None,
    ) -> dict[str, object]:
        if files is None:
            files = [
                {
                    "executable": False,
                    "path": "clips/Grüße_日本.wav",
                    "sha256": cls._FILE_HASH,
                    "size": 17,
                }
            ]
        return {
            "artifact_id": cls._ARTIFACT_ID,
            "files": files,
            "kind": kind,
            "schema_version": 1,
        }

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
    def _reference(
        cls,
        data: bytes,
        *,
        artifact_id: str | None = None,
    ) -> ct2_benchmark.ArtifactManifestReference:
        return ct2_benchmark.ArtifactManifestReference(
            artifact_id=artifact_id or cls._ARTIFACT_ID,
            manifest_sha256=hashlib.sha256(data).hexdigest(),
        )

    @classmethod
    def _manifest_for_contents(
        cls,
        contents: dict[str, tuple[bytes, bool]],
    ) -> ct2_benchmark.ArtifactManifest:
        files = [
            {
                "executable": executable,
                "path": path,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for path, (content, executable) in sorted(contents.items())
        ]
        data = cls._encoded(cls._payload(kind="clips", files=files))
        return ct2_benchmark.parse_artifact_manifest(
            data,
            reference=cls._reference(data),
        )

    @staticmethod
    def _write_artifact_root(
        root: Path,
        contents: dict[str, tuple[bytes, bool]],
    ) -> None:
        root.mkdir(mode=0o700)
        for relative_path, (content, executable) in contents.items():
            target = root / relative_path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o700 if executable else 0o600)

    def test_valid_unicode_manifest_is_frozen_bound_and_roundtrips_without_io(
        self,
    ) -> None:
        data = self._encoded(self._payload())
        reference = self._reference(data)
        with (
            mock.patch("builtins.open", side_effect=AssertionError("file open")) as fopen,
            mock.patch.object(
                ct2_benchmark.os,
                "open",
                side_effect=AssertionError("os.open"),
            ) as os_open,
            mock.patch.object(ct2_benchmark, "local_model_command") as low_qos,
            mock.patch.object(ct2_benchmark, "run_process_bounded_output") as runner,
            mock.patch.object(
                ct2_benchmark.secrets,
                "compare_digest",
                wraps=ct2_benchmark.secrets.compare_digest,
            ) as compare_digest,
        ):
            manifest = ct2_benchmark.parse_artifact_manifest(
                data,
                reference=reference,
            )
            roundtrip = ct2_benchmark.canonical_artifact_manifest_bytes(manifest)

        self.assertEqual(roundtrip, data)
        self.assertIs(manifest.reference, reference)
        self.assertEqual(manifest.kind, "model")
        self.assertEqual(manifest.files[0].path, "clips/Grüße_日本.wav")
        with self.assertRaises(AttributeError):
            setattr(manifest.files[0], "size", 18)
        self.assertGreaterEqual(compare_digest.call_count, 2)
        fopen.assert_not_called()
        os_open.assert_not_called()
        low_qos.assert_not_called()
        runner.assert_not_called()

    def test_reference_hash_id_and_shape_are_strict(self) -> None:
        data = self._encoded(self._payload())
        cases = (
            (
                ct2_benchmark.ArtifactManifestReference(
                    artifact_id=self._ARTIFACT_ID,
                    manifest_sha256="f" * 64,
                ),
                "artifact-reference-mismatch",
            ),
            (
                self._reference(data, artifact_id="artifact-other"),
                "artifact-reference-mismatch",
            ),
            (
                ct2_benchmark.ArtifactManifestReference(
                    artifact_id="private/artifact",
                    manifest_sha256=hashlib.sha256(data).hexdigest(),
                ),
                "artifact-reference-invalid",
            ),
            (True, "artifact-reference-invalid"),
        )
        for reference, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.parse_artifact_manifest(data, reference=reference)
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(str(raised.exception), expected_code)

    def test_all_declared_artifact_kinds_roundtrip(self) -> None:
        for kind in ("runtime", "model", "clips"):
            with self.subTest(kind=kind):
                data = self._encoded(self._payload(kind=kind))
                manifest = ct2_benchmark.parse_artifact_manifest(
                    data,
                    reference=self._reference(data),
                )
                self.assertEqual(manifest.kind, kind)
                self.assertEqual(
                    ct2_benchmark.canonical_artifact_manifest_bytes(manifest),
                    data,
                )

    def test_paths_must_be_safe_nfc_unique_and_sorted(self) -> None:
        unsafe_paths = (
            ["/absolute"],
            ["a\\b"],
            ["a//b"],
            ["a/./b"],
            ["a/../b"],
            ["a/"],
            ["a\x00b"],
            ["a\u0085b"],
            ["clips/Cafe\u0301.wav"],
            ["a" * 513],
            ["/".join(["a"] * 33)],
            ["b", "a"],
            ["a", "a"],
        )
        for paths in unsafe_paths:
            files = [
                {
                    "executable": False,
                    "path": path,
                    "sha256": self._FILE_HASH,
                    "size": 1,
                }
                for path in paths
            ]
            data = self._encoded(self._payload(files=files))
            with self.subTest(paths=paths):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.parse_artifact_manifest(
                        data,
                        reference=self._reference(data),
                    )
                self.assertEqual(raised.exception.code, "artifact-manifest-invalid")

    def test_paths_reject_ancestor_collisions_even_when_not_adjacent(self) -> None:
        files = [
            {
                "executable": False,
                "path": path,
                "sha256": self._FILE_HASH,
                "size": 1,
            }
            for path in ("a", "a-b", "a/b")
        ]
        data = self._encoded(self._payload(files=files))

        with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
            ct2_benchmark.parse_artifact_manifest(
                data,
                reference=self._reference(data),
            )

        self.assertEqual(raised.exception.code, "artifact-manifest-invalid")

    def test_paths_reject_line_and_paragraph_separators(self) -> None:
        for separator in ("\u2028", "\u2029"):
            files = [
                {
                    "executable": False,
                    "path": f"clips/a{separator}b.wav",
                    "sha256": self._FILE_HASH,
                    "size": 1,
                }
            ]
            data = self._encoded(self._payload(files=files))
            with self.subTest(separator=ord(separator)):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.parse_artifact_manifest(
                        data,
                        reference=self._reference(data),
                    )
                self.assertEqual(
                    raised.exception.code,
                    "artifact-manifest-invalid",
                )

    def test_serializer_prebudgets_oversized_manifest_per_entry(self) -> None:
        files = tuple(
            ct2_benchmark.ArtifactFileDeclaration(
                path=f"{index:04x}-" + ("\U0001f600" * 500),
                sha256=self._FILE_HASH,
                size=0,
                executable=False,
            )
            for index in range(ct2_benchmark.MAX_ARTIFACT_FILES)
        )
        manifest = ct2_benchmark.ArtifactManifest(
            reference=ct2_benchmark.ArtifactManifestReference(
                artifact_id=self._ARTIFACT_ID,
                manifest_sha256="0" * 64,
            ),
            kind="clips",
            files=files,
            schema_version=ct2_benchmark.ARTIFACT_MANIFEST_SCHEMA_VERSION,
        )
        with (
            mock.patch.object(
                ct2_benchmark,
                "_artifact_manifest_payload",
                side_effect=AssertionError("full payload must not be built"),
            ) as full_payload,
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.canonical_artifact_manifest_bytes(manifest)

        self.assertEqual(raised.exception.code, "artifact-manifest-invalid")
        self.assertEqual(full_payload.call_count, 0)

    def test_manifest_fields_numbers_bounds_and_canonical_form_are_strict(
        self,
    ) -> None:
        invalid_payloads = []
        unknown = self._payload()
        unknown["unknown"] = True
        invalid_payloads.append(unknown)
        unknown_file = self._payload()
        unknown_file["files"][0]["unknown"] = True
        invalid_payloads.append(unknown_file)
        invalid_artifact_id = self._payload()
        invalid_artifact_id["artifact_id"] = "private/artifact"
        invalid_payloads.append(invalid_artifact_id)
        invalid_schema = self._payload()
        invalid_schema["schema_version"] = True
        invalid_payloads.append(invalid_schema)
        for size in (True, -1, 2**64):
            payload = self._payload()
            payload["files"][0]["size"] = size
            invalid_payloads.append(payload)
        invalid_executable = self._payload()
        invalid_executable["files"][0]["executable"] = 1
        invalid_payloads.append(invalid_executable)
        invalid_hash = self._payload()
        invalid_hash["files"][0]["sha256"] = "A" * 64
        invalid_payloads.append(invalid_hash)
        invalid_payloads.extend(
            (
                self._payload(kind="other"),
                self._payload(files=[]),
                self._payload(
                    files=[
                        {
                            "executable": False,
                            "path": "a",
                            "sha256": self._FILE_HASH,
                            "size": 2**40,
                        },
                        {
                            "executable": False,
                            "path": "b",
                            "sha256": "b" * 64,
                            "size": 1,
                        },
                    ]
                ),
            )
        )
        canonical = self._encoded(self._payload())
        malformed = [
            *(self._encoded(payload) for payload in invalid_payloads),
            canonical + b"\n",
            canonical.replace(
                b'{"artifact_id":',
                b'{"artifact_id":"artifact-main","artifact_id":',
                1,
            ),
            canonical.replace(b'"size":17', b'"size":NaN', 1),
            b"x" * ((1024 * 1024) + 1),
        ]
        for data in malformed:
            with self.subTest(data_length=len(data)):
                with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
                    ct2_benchmark.parse_artifact_manifest(
                        data,
                        reference=self._reference(data),
                    )
                self.assertEqual(raised.exception.code, "artifact-manifest-invalid")
                self.assertNotIn("artifact-main", str(raised.exception))

    def test_declared_root_total_is_limited_to_one_gibibyte(self) -> None:
        at_limit = self._payload(
            files=[
                {
                    "executable": False,
                    "path": "payload.bin",
                    "sha256": self._FILE_HASH,
                    "size": 1 << 30,
                }
            ]
        )
        encoded = self._encoded(at_limit)
        manifest = ct2_benchmark.parse_artifact_manifest(
            encoded,
            reference=self._reference(encoded),
        )
        self.assertEqual(manifest.files[0].size, 1 << 30)

        over_limit = self._payload(
            files=[
                {
                    "executable": False,
                    "path": "a.bin",
                    "sha256": self._FILE_HASH,
                    "size": 1 << 30,
                },
                {
                    "executable": False,
                    "path": "b.bin",
                    "sha256": "b" * 64,
                    "size": 1,
                },
            ]
        )
        encoded = self._encoded(over_limit)
        with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
            ct2_benchmark.parse_artifact_manifest(
                encoded,
                reference=self._reference(encoded),
            )
        self.assertEqual(raised.exception.code, "artifact-manifest-invalid")

    def test_unexpected_parser_errors_are_redacted_but_interrupt_propagates(
        self,
    ) -> None:
        data = self._encoded(self._payload())
        reference = self._reference(data)
        for error in (TypeError("private payload"), SystemExit("private exit")):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(
                    ct2_benchmark,
                    "_artifact_file_declaration",
                    side_effect=error,
                ),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.parse_artifact_manifest(data, reference=reference)
            self.assertEqual(raised.exception.code, "artifact-manifest-invalid")
            self.assertNotIn("private", str(raised.exception))

        interrupt = KeyboardInterrupt()
        with mock.patch.object(
            ct2_benchmark,
            "_artifact_file_declaration",
            side_effect=interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised_interrupt:
                ct2_benchmark.parse_artifact_manifest(data, reference=reference)
        self.assertIs(raised_interrupt.exception, interrupt)

    @classmethod
    def _attestation_payload(
        cls,
        manifest: ct2_benchmark.ArtifactManifest,
        **changes: object,
    ) -> bytes:
        payload: dict[str, object] = {
            "attestation": {
                "artifact_id": manifest.reference.artifact_id,
                "file_count": len(manifest.files),
                "manifest_sha256": manifest.reference.manifest_sha256,
                "total_bytes": sum(item.size for item in manifest.files),
            },
            "mode": "attest-artifact",
            "nonce": "c" * 64,
            "schema_version": 1,
            "status": "ok",
        }
        payload.update(changes)
        return cls._encoded(payload)

    def _run_mocked_attestation(
        self,
        manifest: ct2_benchmark.ArtifactManifest,
        *,
        response: object | None = None,
        returncode: object = 0,
        stderr: object = b"",
        runner_effect: BaseException | None = None,
        low_qos_effect: BaseException | None = None,
        worker_path_effect: BaseException | None = None,
    ) -> tuple[object, mock.Mock, mock.Mock]:
        if response is None:
            response = self._attestation_payload(manifest)
        low_qos = mock.Mock(
            side_effect=(
                low_qos_effect
                if low_qos_effect is not None
                else lambda argv: ["/low/scope", *argv]
            )
        )
        runner = mock.Mock(
            side_effect=runner_effect,
            return_value=(returncode, response, stderr),
        )
        with (
            mock.patch.object(
                ct2_benchmark.secrets,
                "token_hex",
                return_value="c" * 64,
            ),
            mock.patch.object(
                ct2_benchmark,
                "_worker_path",
                side_effect=worker_path_effect,
                return_value="/repo/ct2_benchmark_worker.py",
            ),
            mock.patch.object(ct2_benchmark, "local_model_command", low_qos),
            mock.patch.object(
                ct2_benchmark,
                "run_process_bounded_output",
                runner,
            ),
            mock.patch.object(ct2_benchmark.time, "monotonic", return_value=40.0),
        ):
            try:
                result: object = ct2_benchmark.attest_artifact_root(
                    Path("/safe/artifact"),
                    manifest,
                )
            except BaseException as exc:
                result = exc
        return result, low_qos, runner

    def test_attestation_delegates_exact_request_to_low_qos_worker(self) -> None:
        manifest = self._manifest_for_contents(
            {"payload.bin": (b"payload", False)}
        )

        result, low_qos, runner = self._run_mocked_attestation(manifest)

        self.assertEqual(
            result,
            ct2_benchmark.ArtifactAttestation(
                artifact_id=manifest.reference.artifact_id,
                manifest_sha256=manifest.reference.manifest_sha256,
                file_count=1,
                total_bytes=7,
            ),
        )
        runtime_argv = [
            sys.executable,
            "-I",
            "-S",
            "-B",
            "/repo/ct2_benchmark_worker.py",
            "--attest-artifact",
            "--nonce",
            "c" * 64,
        ]
        low_qos.assert_called_once_with(runtime_argv)
        runner.assert_called_once()
        args, kwargs = runner.call_args
        self.assertEqual(args, (["/low/scope", *runtime_argv],))
        self.assertEqual(kwargs["timeout_seconds"], 60)
        self.assertEqual(kwargs["max_output_bytes"], 16 * 1024)
        self.assertEqual(kwargs["deadline"], 100.0)
        self.assertTrue(kwargs["preserve_user_systemd_environment"])
        self.assertEqual(kwargs["env"], ct2_benchmark._ENVIRONMENT)
        request = json.loads(kwargs["input_bytes"])
        self.assertEqual(
            set(request),
            {"manifest", "manifest_sha256", "mode", "nonce", "root", "schema_version"},
        )
        self.assertEqual(request["mode"], "attest-artifact")
        self.assertEqual(request["root"], "/safe/artifact")
        self.assertEqual(request["manifest_sha256"], manifest.reference.manifest_sha256)
        self.assertEqual(kwargs["input_bytes"], self._encoded(request))

    def test_attestation_invalid_root_fails_before_worker_launch(self) -> None:
        manifest = self._manifest_for_contents(
            {"payload.bin": (b"payload", False)}
        )
        low_qos = mock.Mock()
        runner = mock.Mock()
        with (
            mock.patch.object(ct2_benchmark, "local_model_command", low_qos),
            mock.patch.object(ct2_benchmark, "run_process_bounded_output", runner),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_artifact_root(Path("relative"), manifest)
        self.assertEqual(raised.exception.code, "artifact-root-invalid")
        low_qos.assert_not_called()
        runner.assert_not_called()

    def test_attestation_protocol_failures_use_only_public_codes(self) -> None:
        manifest = self._manifest_for_contents(
            {"payload.bin": (b"payload", False)}
        )
        valid = json.loads(self._attestation_payload(manifest))
        cases: tuple[tuple[str, dict[str, object]], ...] = (
            ("artifact-attestation-failed", {"runner_effect": TimeoutError("private")}),
            (
                "artifact-attestation-failed",
                {
                    "worker_path_effect": ct2_benchmark.RuntimeProbeError(
                        "invalid-spec"
                    )
                },
            ),
            ("artifact-attestation-failed", {"stderr": b"private"}),
            ("artifact-attestation-failed", {"response": b"not-json"}),
            (
                "artifact-attestation-failed",
                {"response": b"x" * ((16 * 1024) + 1)},
            ),
            (
                "artifact-attestation-failed",
                {"response": self._encoded({**valid, "nonce": "d" * 64})},
            ),
            (
                "artifact-attestation-failed",
                {"response": self._encoded({**valid, "schema_version": True})},
            ),
            (
                "artifact-attestation-failed",
                {
                    "response": self._encoded(
                        {
                            **valid,
                            "attestation": {
                                **valid["attestation"],
                                "file_count": True,
                            },
                        }
                    )
                },
            ),
            (
                "artifact-tree-changed",
                {
                    "returncode": 65,
                    "response": self._encoded(
                        {
                            "error_code": "artifact-tree-changed",
                            "nonce": "c" * 64,
                            "schema_version": 1,
                            "status": "error",
                        }
                    ),
                },
            ),
            ("artifact-attestation-failed", {"returncode": 7}),
        )
        for expected, arguments in cases:
            with self.subTest(expected=expected, arguments=tuple(arguments)):
                result, _low_qos, _runner = self._run_mocked_attestation(
                    manifest,
                    **arguments,
                )
                self.assertIsInstance(result, ct2_benchmark.RuntimeProbeError)
                self.assertEqual(result.code, expected)
                self.assertEqual(str(result), expected)
                self.assertNotIn("private", str(result))

    def test_attestation_dependency_system_exit_is_redacted_and_interrupt_passes(
        self,
    ) -> None:
        manifest = self._manifest_for_contents(
            {"payload.bin": (b"payload", False)}
        )
        for dependency in ("low", "runner"):
            with self.subTest(dependency=dependency):
                arguments = (
                    {"low_qos_effect": SystemExit("private")}
                    if dependency == "low"
                    else {"runner_effect": SystemExit("private")}
                )
                result, _low_qos, _runner = self._run_mocked_attestation(
                    manifest,
                    **arguments,
                )
                self.assertIsInstance(result, ct2_benchmark.RuntimeProbeError)
                self.assertEqual(result.code, "artifact-attestation-failed")

                interrupt = KeyboardInterrupt()
                arguments = (
                    {"low_qos_effect": interrupt}
                    if dependency == "low"
                    else {"runner_effect": interrupt}
                )
                result, _low_qos, _runner = self._run_mocked_attestation(
                    manifest,
                    **arguments,
                )
                self.assertIs(result, interrupt)


class Ctranslate2ArtifactSetTests(unittest.TestCase):
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
        *,
        size: int = 1,
        variant: str = "main",
    ) -> ct2_benchmark.ArtifactManifest:
        payload = {
            "artifact_id": artifact_id,
            "files": [
                {
                    "executable": False,
                    "path": f"payload-{variant}.bin",
                    "sha256": hashlib.sha256(variant.encode("ascii")).hexdigest(),
                    "size": size,
                }
            ],
            "kind": kind,
            "schema_version": 1,
        }
        data = cls._encoded(payload)
        reference = ct2_benchmark.ArtifactManifestReference(
            artifact_id=artifact_id,
            manifest_sha256=hashlib.sha256(data).hexdigest(),
        )
        return ct2_benchmark.parse_artifact_manifest(data, reference=reference)

    @classmethod
    def _fixture(
        cls,
        *,
        sizes: tuple[int, ...] | None = None,
        model_count: int = 2,
    ) -> tuple[
        ct2_benchmark.RunPlan,
        tuple[tuple[Path, ct2_benchmark.ArtifactManifest], ...],
    ]:
        effective_sizes = sizes or ((1,) * (3 + model_count))
        manifests = (
            cls._manifest("runtime-a", "runtime", size=effective_sizes[0]),
            cls._manifest("runtime-b", "runtime", size=effective_sizes[1]),
            cls._manifest("clips-main", "clips", size=effective_sizes[2]),
            *(
                cls._manifest(
                    f"model-{index}",
                    "model",
                    size=effective_sizes[3 + index],
                )
                for index in range(model_count)
            ),
        )
        payload = {
            "clips": {
                "id": manifests[2].reference.artifact_id,
                "manifest_sha256": manifests[2].reference.manifest_sha256,
            },
            "mode": "full",
            "models": [
                {
                    "id": manifest.reference.artifact_id,
                    "manifest_sha256": manifest.reference.manifest_sha256,
                }
                for manifest in manifests[3:]
            ],
            "pair_count": 5,
            "runtimes": {
                label: {
                    "id": manifest.reference.artifact_id,
                    "manifest_sha256": manifest.reference.manifest_sha256,
                }
                for label, manifest in zip(("a", "b"), manifests[:2], strict=True)
            },
            "schema_version": 1,
        }
        plan = ct2_benchmark.build_run_plan(cls._encoded(payload), seed=7)
        artifacts = tuple(
            (Path(f"/artifacts/{index}-{manifest.reference.artifact_id}"), manifest)
            for index, manifest in enumerate(manifests)
        )
        return plan, artifacts

    @staticmethod
    def _attestation(
        root: Path,
        manifest: ct2_benchmark.ArtifactManifest,
    ) -> ct2_benchmark.ArtifactAttestation:
        del root
        return ct2_benchmark.ArtifactAttestation(
            artifact_id=manifest.reference.artifact_id,
            manifest_sha256=manifest.reference.manifest_sha256,
            file_count=len(manifest.files),
            total_bytes=sum(item.size for item in manifest.files),
        )

    def test_attests_complete_set_in_plan_order_after_full_preflight(self) -> None:
        plan, artifacts = self._fixture()
        attester = mock.Mock(side_effect=self._attestation)

        with mock.patch.object(ct2_benchmark, "attest_artifact_root", attester):
            result = ct2_benchmark.attest_run_plan_artifacts(plan, artifacts)

        self.assertIs(type(result), tuple)
        self.assertEqual(
            tuple(value.artifact_id for value in result),
            ("runtime-a", "runtime-b", "clips-main", "model-0", "model-1"),
        )
        self.assertEqual(
            tuple(call.args for call in attester.call_args_list),
            artifacts,
        )

    def test_set_shape_order_kind_and_reference_fail_before_io(self) -> None:
        plan, artifacts = self._fixture()
        other_runtime = self._manifest("runtime-other", "runtime")
        cases = (
            artifacts[:-1],
            (*artifacts, artifacts[-1]),
            (artifacts[1], artifacts[0], *artifacts[2:]),
            ((artifacts[0][0], other_runtime), *artifacts[1:]),
        )
        for candidate in cases:
            attester = mock.Mock()
            with (
                self.subTest(length=len(candidate)),
                mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.attest_run_plan_artifacts(plan, candidate)
            self.assertEqual(raised.exception.code, "artifact-set-invalid")
            attester.assert_not_called()

    def test_wrong_kind_fails_with_matching_manifest_reference(self) -> None:
        plan, artifacts = self._fixture()
        wrong_kind = self._manifest("clips-other", "model")
        candidate_plan = replace(plan, clips=wrong_kind.reference)
        candidate = (
            *artifacts[:2],
            (artifacts[2][0], wrong_kind),
            *artifacts[3:],
        )
        attester = mock.Mock(side_effect=self._attestation)
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(candidate_plan, candidate)
        self.assertEqual(raised.exception.code, "artifact-set-invalid")
        attester.assert_not_called()

    def test_cross_category_duplicate_id_has_valid_matching_bindings(self) -> None:
        plan, artifacts = self._fixture()
        duplicate = self._manifest(
            plan.runtimes[0].artifact_id,
            "clips",
            variant="duplicate-clips",
        )
        candidate_plan = replace(plan, clips=duplicate.reference)
        candidate = (
            *artifacts[:2],
            (artifacts[2][0], duplicate),
            *artifacts[3:],
        )
        self.assertEqual(candidate[2][1].reference, candidate_plan.clips)
        self.assertNotEqual(
            candidate_plan.clips.manifest_sha256,
            candidate_plan.runtimes[0].manifest_sha256,
        )
        attester = mock.Mock(side_effect=self._attestation)
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(candidate_plan, candidate)
        self.assertEqual(raised.exception.code, "artifact-set-invalid")
        attester.assert_not_called()

    def test_cross_category_duplicate_reference_hash_isolated_from_binding(self) -> None:
        plan, artifacts = self._fixture()
        duplicate_reference = replace(
            plan.clips,
            manifest_sha256=plan.runtimes[0].manifest_sha256,
        )
        candidate_plan = replace(plan, clips=duplicate_reference)
        candidate = (
            *artifacts[:2],
            (
                artifacts[2][0],
                replace(artifacts[2][1], reference=duplicate_reference),
            ),
            *artifacts[3:],
        )
        validator = mock.Mock(return_value=b"canonical")
        attester = mock.Mock(side_effect=self._attestation)
        with (
            mock.patch.object(
                ct2_benchmark,
                "canonical_artifact_manifest_bytes",
                validator,
            ),
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(candidate_plan, candidate)
        self.assertEqual(raised.exception.code, "artifact-set-invalid")
        validator.assert_not_called()
        attester.assert_not_called()

    def test_manifest_reference_mismatch_is_preserved_before_io(self) -> None:
        plan, artifacts = self._fixture()
        mismatched_reference = replace(
            plan.runtimes[0],
            manifest_sha256="9" * 64,
        )
        candidate_plan = replace(
            plan,
            runtimes=(mismatched_reference, plan.runtimes[1]),
        )
        candidate = (
            (
                artifacts[0][0],
                replace(artifacts[0][1], reference=mismatched_reference),
            ),
            *artifacts[1:],
        )
        attester = mock.Mock()
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(candidate_plan, candidate)
        self.assertEqual(raised.exception.code, "artifact-reference-mismatch")
        attester.assert_not_called()

    def test_outer_and_pair_containers_must_be_exact_tuples(self) -> None:
        plan, artifacts = self._fixture()

        class TupleSubclass(tuple):
            pass

        cases = (
            list(artifacts),
            TupleSubclass(artifacts),
            (list(artifacts[0]), *artifacts[1:]),
            (TupleSubclass(artifacts[0]), *artifacts[1:]),
            ((artifacts[0][0],), *artifacts[1:]),
        )
        for candidate in cases:
            attester = mock.Mock()
            with (
                self.subTest(container=type(candidate).__name__),
                mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.attest_run_plan_artifacts(plan, candidate)
            self.assertEqual(raised.exception.code, "artifact-set-invalid")
            attester.assert_not_called()

    def test_invalid_plan_and_non_string_path_fail_before_io(self) -> None:
        plan, artifacts = self._fixture()
        attester = mock.Mock()
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(
                replace(plan, schema_version=True),
                artifacts,
            )
        self.assertEqual(raised.exception.code, "plan-invalid")
        attester.assert_not_called()

        class BytesPath:
            def __fspath__(self) -> bytes:
                return b"/artifacts/not-text"

        candidate = ((BytesPath(), artifacts[0][1]), *artifacts[1:])
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(plan, candidate)
        self.assertEqual(raised.exception.code, "artifact-set-invalid")
        attester.assert_not_called()

    def test_paths_are_captured_once_frozen_and_globally_unique(self) -> None:
        plan, artifacts = self._fixture()

        class AlternatingPath:
            def __init__(self, first: str, second: str) -> None:
                self.values = (first, second)
                self.calls = 0

            def __fspath__(self) -> str:
                value = self.values[min(self.calls, 1)]
                self.calls += 1
                return value

        changing = AlternatingPath("/artifacts/frozen", "/artifacts/changed")
        candidate = ((changing, artifacts[0][1]), *artifacts[1:])
        attester = mock.Mock(side_effect=self._attestation)
        with mock.patch.object(ct2_benchmark, "attest_artifact_root", attester):
            ct2_benchmark.attest_run_plan_artifacts(plan, candidate)
        self.assertEqual(changing.calls, 1)
        self.assertEqual(attester.call_args_list[0].args[0], Path("/artifacts/frozen"))

        invalid_roots = (
            ((Path("relative"), artifacts[0][1]), *artifacts[1:]),
            ((Path("/artifacts/../other"), artifacts[0][1]), *artifacts[1:]),
            (("//artifacts/root", artifacts[0][1]), *artifacts[1:]),
            ((artifacts[1][0], artifacts[0][1]), *artifacts[1:]),
        )
        for invalid in invalid_roots:
            attester = mock.Mock()
            with (
                self.subTest(root=invalid[0][0]),
                mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.attest_run_plan_artifacts(plan, invalid)
            self.assertEqual(raised.exception.code, "artifact-set-invalid")
            attester.assert_not_called()

    def test_four_gibibyte_aggregate_is_inclusive_and_checked_before_io(self) -> None:
        limit = ct2_benchmark.MAX_DECLARED_ARTIFACT_BYTES
        plan, artifacts = self._fixture(
            sizes=(limit, limit, limit, limit),
            model_count=1,
        )
        attester = mock.Mock(side_effect=self._attestation)
        with mock.patch.object(ct2_benchmark, "attest_artifact_root", attester):
            result = ct2_benchmark.attest_run_plan_artifacts(plan, artifacts)
        self.assertEqual(sum(value.total_bytes for value in result), 4 << 30)
        self.assertEqual(attester.call_count, 4)

        over_plan, over_artifacts = self._fixture(
            sizes=(limit, limit, limit, limit, 1),
            model_count=2,
        )
        attester.reset_mock()
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(over_plan, over_artifacts)
        self.assertEqual(raised.exception.code, "artifact-set-invalid")
        attester.assert_not_called()

    def test_invalid_final_manifest_prevents_every_attestation(self) -> None:
        plan, artifacts = self._fixture()
        invalid = replace(artifacts[-1][1], schema_version=True)
        candidate = (*artifacts[:-1], (artifacts[-1][0], invalid))
        attester = mock.Mock()
        with (
            mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark.attest_run_plan_artifacts(plan, candidate)
        self.assertEqual(raised.exception.code, "artifact-manifest-invalid")
        attester.assert_not_called()

    def test_phase_two_preserves_public_codes_and_interrupt_identity(self) -> None:
        plan, artifacts = self._fixture()
        for code in ct2_benchmark._ARTIFACT_ATTESTATION_ERROR_CODES:
            attester = mock.Mock(
                side_effect=[
                    self._attestation(*artifacts[0]),
                    ct2_benchmark.RuntimeProbeError(code),
                ]
            )
            with (
                self.subTest(code=code),
                mock.patch.object(ct2_benchmark, "attest_artifact_root", attester),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.attest_run_plan_artifacts(plan, artifacts)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(attester.call_count, 2)

        for error in (ValueError("private"), SystemExit("private")):
            with (
                self.subTest(error=type(error).__name__),
                mock.patch.object(
                    ct2_benchmark,
                    "attest_artifact_root",
                    side_effect=error,
                ),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.attest_run_plan_artifacts(plan, artifacts)
            self.assertEqual(raised.exception.code, "artifact-attestation-failed")
            self.assertNotIn("private", str(raised.exception))

        interrupt = KeyboardInterrupt()
        with mock.patch.object(
            ct2_benchmark,
            "attest_artifact_root",
            side_effect=interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised_interrupt:
                ct2_benchmark.attest_run_plan_artifacts(plan, artifacts)
        self.assertIs(raised_interrupt.exception, interrupt)

    def test_phase_two_result_fields_are_exact_types_before_comparison(self) -> None:
        plan, artifacts = self._fixture()

        class ExplosiveEquality:
            def __init__(self) -> None:
                self.comparisons = 0

            def __eq__(self, other: object) -> bool:
                del other
                self.comparisons += 1
                raise ValueError("private equality")

            def __ne__(self, other: object) -> bool:
                return not self == other

        expected = self._attestation(*artifacts[0])
        explosive_id = ExplosiveEquality()
        explosive_hash = ExplosiveEquality()
        invalid_results = (
            replace(expected, artifact_id=explosive_id),
            replace(expected, manifest_sha256=explosive_hash),
            replace(expected, file_count=True),
            replace(expected, total_bytes=True),
        )
        for invalid in invalid_results:
            with (
                self.subTest(field_types=tuple(type(value).__name__ for value in (
                    invalid.artifact_id,
                    invalid.manifest_sha256,
                    invalid.file_count,
                    invalid.total_bytes,
                ))),
                mock.patch.object(
                    ct2_benchmark,
                    "attest_artifact_root",
                    return_value=invalid,
                ),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.attest_run_plan_artifacts(plan, artifacts)
            self.assertEqual(raised.exception.code, "artifact-attestation-failed")
            self.assertNotIn("private", str(raised.exception))
        self.assertEqual(explosive_id.comparisons, 0)
        self.assertEqual(explosive_hash.comparisons, 0)

    def test_set_error_is_controller_only_allowlisted(self) -> None:
        self.assertEqual(
            ct2_benchmark.RuntimeProbeError("artifact-set-invalid").code,
            "artifact-set-invalid",
        )
        self.assertNotIn(
            "artifact-set-invalid",
            ct2_benchmark._ARTIFACT_ATTESTATION_ERROR_CODES,
        )


class Ctranslate2BenchmarkPairRequestTests(unittest.TestCase):
    _NONCES = ("1" * 64, "2" * 64)

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
        files: tuple[tuple[str, int, bool], ...],
    ) -> ct2_benchmark.ArtifactManifest:
        payload = {
            "artifact_id": artifact_id,
            "files": [
                {
                    "executable": executable,
                    "path": path,
                    "sha256": hashlib.sha256(path.encode("utf-8")).hexdigest(),
                    "size": size,
                }
                for path, size, executable in sorted(files)
            ],
            "kind": kind,
            "schema_version": 1,
        }
        data = cls._encoded(payload)
        reference = ct2_benchmark.ArtifactManifestReference(
            artifact_id=artifact_id,
            manifest_sha256=hashlib.sha256(data).hexdigest(),
        )
        return ct2_benchmark.parse_artifact_manifest(data, reference=reference)

    @classmethod
    def _inputs(
        cls,
    ) -> tuple[
        ct2_benchmark.RunPlan,
        tuple[tuple[Path, ct2_benchmark.ArtifactManifest], ...],
        tuple[ct2_benchmark._RuntimeSpec, ct2_benchmark._RuntimeSpec],
        ct2_benchmark.DecodeProfile,
    ]:
        manifests = (
            cls._manifest("runtime-a", "runtime", (("package-a.bin", 11, False),)),
            cls._manifest("runtime-b", "runtime", (("package-b.bin", 12, False),)),
            cls._manifest(
                "clips-main",
                "clips",
                (
                    ("clip.wav", 128, False),
                    ("corpus-v1.json", 64, False),
                ),
            ),
            cls._manifest("model-small", "model", (("model.bin", 21, False),)),
            cls._manifest("model-large", "model", (("model.bin", 22, False),)),
        )
        experiment = {
            "clips": {
                "id": manifests[2].reference.artifact_id,
                "manifest_sha256": manifests[2].reference.manifest_sha256,
            },
            "mode": "full",
            "models": [
                {
                    "id": manifest.reference.artifact_id,
                    "manifest_sha256": manifest.reference.manifest_sha256,
                }
                for manifest in manifests[3:]
            ],
            "pair_count": 5,
            "runtimes": {
                label: {
                    "id": manifest.reference.artifact_id,
                    "manifest_sha256": manifest.reference.manifest_sha256,
                }
                for label, manifest in zip(("a", "b"), manifests[:2], strict=True)
            },
            "schema_version": 1,
        }
        plan = ct2_benchmark.build_run_plan(cls._encoded(experiment), seed=9)
        artifacts = tuple(
            (Path(f"/artifacts/{manifest.reference.artifact_id}"), manifest)
            for manifest in manifests
        )
        specs = (
            ct2_benchmark._RuntimeSpec(
                interpreter="/opt/soc/python",
                site_packages=str(artifacts[0][0]),
                expected_ctranslate2_version="4.7.2",
                expected_faster_whisper_version="1.2.1",
            ),
            ct2_benchmark._RuntimeSpec(
                interpreter="/opt/soc/python",
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
        return plan, artifacts, specs, profile

    @classmethod
    def _build(
        cls,
        *,
        plan: ct2_benchmark.RunPlan | None = None,
        artifacts: object | None = None,
        runtime_specs: object | None = None,
        profile: object | None = None,
        model_block_index: object = 0,
        phase_index: object = 0,
        nonces: object = _NONCES,
    ) -> tuple[bytes, bytes]:
        base_plan, base_artifacts, base_specs, base_profile = cls._inputs()
        return ct2_benchmark.build_benchmark_pair_requests(
            base_plan if plan is None else plan,
            base_artifacts if artifacts is None else artifacts,
            base_specs if runtime_specs is None else runtime_specs,
            base_profile if profile is None else profile,
            model_block_index=model_block_index,
            phase_index=phase_index,
            nonces=nonces,
        )

    @classmethod
    def _bind(
        cls,
        requests: object,
        *,
        plan: ct2_benchmark.RunPlan | None = None,
        artifacts: object | None = None,
        runtime_specs: object | None = None,
        profile: object | None = None,
        model_block_index: object = 0,
        phase_index: object = 0,
        nonces: object = _NONCES,
    ) -> tuple[bytes, bytes]:
        if (
            plan is None
            or artifacts is None
            or runtime_specs is None
            or profile is None
        ):
            base_plan, base_artifacts, base_specs, base_profile = cls._inputs()
        else:
            base_plan, base_artifacts, base_specs, base_profile = (
                plan,
                artifacts,
                runtime_specs,
                profile,
            )
        return ct2_benchmark.bind_benchmark_pair_requests(
            requests,
            base_plan if plan is None else plan,
            base_artifacts if artifacts is None else artifacts,
            base_specs if runtime_specs is None else runtime_specs,
            base_profile if profile is None else profile,
            model_block_index=model_block_index,
            phase_index=phase_index,
            nonces=nonces,
        )

    def _assert_binding_invalid(
        self,
        requests: object,
        **arguments: object,
    ) -> ct2_benchmark.RuntimeProbeError:
        with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
            self._bind(requests, **arguments)
        error = raised.exception
        self.assertEqual(error.code, "benchmark-request-invalid")
        self.assertEqual(error.args, ("benchmark-request-invalid",))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)
        return error

    def test_golden_schema_binds_plan_profile_artifacts_and_runtime_order(self) -> None:
        plan, artifacts, specs, profile = self._inputs()
        requests = ct2_benchmark.build_benchmark_pair_requests(
            plan,
            artifacts,
            specs,
            profile,
            model_block_index=1,
            phase_index=2,
            nonces=self._NONCES,
        )
        decoded = tuple(json.loads(value) for value in requests)
        plan_bytes = ct2_benchmark.canonical_run_plan_bytes(plan)
        profile_payload = {
            "beam_size": 5,
            "condition_on_previous_text": False,
            "cpu_threads": 4,
            "device": "cpu",
            "language": "de",
            "num_workers": 1,
            "profile_schema_version": 1,
            "requested_compute_type": "int8",
            "task": "transcribe",
            "temperature_milli": 0,
            "vad_filter": False,
            "without_timestamps": False,
            "word_timestamps": False,
        }
        expected_top_level = {
            "clips",
            "corpus",
            "decode_profile",
            "decode_profile_sha256",
            "mode",
            "model",
            "nonce",
            "run_plan",
            "run_plan_sha256",
            "runtime",
            "runtime_layout",
            "schema_version",
            "selection",
        }
        phase = plan.model_blocks[1].phases[2]
        runtime_by_id = {
            manifest.reference.artifact_id: (root, manifest, spec)
            for (root, manifest), spec in zip(artifacts[:2], specs, strict=True)
        }
        for arm_index, payload in enumerate(decoded):
            with self.subTest(arm_index=arm_index):
                self.assertEqual(set(payload), expected_top_level)
                self.assertEqual(payload["schema_version"], 1)
                self.assertEqual(payload["mode"], "benchmark-arm")
                self.assertEqual(payload["nonce"], self._NONCES[arm_index])
                self.assertEqual(payload["run_plan"], json.loads(plan_bytes))
                self.assertEqual(
                    payload["run_plan_sha256"],
                    hashlib.sha256(plan_bytes).hexdigest(),
                )
                self.assertEqual(payload["decode_profile"], profile_payload)
                self.assertEqual(
                    payload["decode_profile_sha256"],
                    hashlib.sha256(self._encoded(profile_payload)).hexdigest(),
                )
                self.assertEqual(
                    payload["selection"],
                    {
                        "arm_index": arm_index,
                        "model_block_index": 1,
                        "phase_index": 2,
                    },
                )
                runtime_id = phase.pair.runtime_order[arm_index]
                runtime_root, runtime_manifest, runtime_spec = runtime_by_id[runtime_id]
                self.assertEqual(
                    payload["runtime"],
                    self._artifact_payload(runtime_root, runtime_manifest),
                )
                self.assertEqual(
                    payload["model"],
                    self._artifact_payload(*artifacts[4]),
                )
                self.assertEqual(
                    payload["clips"],
                    self._artifact_payload(*artifacts[2]),
                )
                self.assertEqual(
                    payload["runtime_layout"],
                    {
                        "expected_ctranslate2_version": (
                            runtime_spec.expected_ctranslate2_version
                        ),
                        "expected_faster_whisper_version": "1.2.1",
                        "interpreter": "/opt/soc/python",
                        "interpreter_contract": "host-tcb-unattested",
                        "site_packages_member": ".",
                    },
                )
                self.assertEqual(
                    payload["corpus"],
                    {"manifest_member": "corpus-v1.json"},
                )
                self.assertEqual(requests[arm_index], self._encoded(payload))

        shared_keys = {
            "clips",
            "corpus",
            "decode_profile",
            "decode_profile_sha256",
            "model",
            "run_plan",
            "run_plan_sha256",
        }
        for key in shared_keys:
            self.assertEqual(decoded[0][key], decoded[1][key])
        self.assertEqual(
            set(decoded[0]["selection"]),
            {"arm_index", "model_block_index", "phase_index"},
        )
        for duplicate in (
            "clip_order_seed",
            "decision_eligibility_capable",
            "fresh_process_per_arm",
            "pair_count",
            "seed",
            "warmup_before_measurement",
            "warmup_discarded",
        ):
            self.assertNotIn(duplicate, decoded[0])
            self.assertNotIn(duplicate, decoded[0]["selection"])

    @classmethod
    def _artifact_payload(
        cls,
        root: Path,
        manifest: ct2_benchmark.ArtifactManifest,
    ) -> dict[str, object]:
        manifest_bytes = ct2_benchmark.canonical_artifact_manifest_bytes(manifest)
        return {
            "manifest": json.loads(manifest_bytes),
            "manifest_sha256": manifest.reference.manifest_sha256,
            "root": str(root),
        }

    def test_profile_is_frozen_closed_and_strictly_typed(self) -> None:
        _plan, _artifacts, _specs, profile = self._inputs()
        with self.assertRaises(AttributeError):
            profile.cpu_threads = 8

        class IntSubclass(int):
            pass

        invalid = (
            replace(profile, profile_schema_version=True),
            replace(profile, profile_schema_version=2),
            replace(profile, device="cuda"),
            replace(profile, requested_compute_type="float16"),
            replace(profile, requested_compute_type="auto"),
            replace(profile, requested_compute_type="default"),
            replace(profile, cpu_threads=True),
            replace(profile, cpu_threads=IntSubclass(4)),
            replace(profile, cpu_threads=0),
            replace(profile, cpu_threads=257),
            replace(profile, num_workers=True),
            replace(profile, num_workers=2),
            replace(profile, language="en"),
            replace(profile, task="translate"),
            replace(profile, beam_size=True),
            replace(profile, beam_size=0),
            replace(profile, beam_size=33),
            replace(profile, temperature_milli=False),
            replace(profile, temperature_milli=1),
            replace(profile, vad_filter=True),
            replace(profile, condition_on_previous_text=True),
            replace(profile, word_timestamps=True),
            replace(profile, without_timestamps=True),
        )
        for candidate in invalid:
            with (
                self.subTest(candidate=candidate),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                self._build(profile=candidate)
            self.assertEqual(raised.exception.code, "benchmark-request-invalid")

        for compute_type in ("int8", "float32"):
            with self.subTest(compute_type=compute_type):
                requests = self._build(
                    profile=replace(
                        profile,
                        requested_compute_type=compute_type,
                        cpu_threads=256,
                        beam_size=32,
                    )
                )
                self.assertEqual(len(requests), 2)

    def test_runtime_specs_are_aligned_once_and_versions_are_bijective(self) -> None:
        _plan, _artifacts, specs, _profile = self._inputs()
        with mock.patch.object(
            ct2_benchmark,
            "_validated_spec",
            wraps=ct2_benchmark._validated_spec,
        ) as validator:
            self._build(runtime_specs=specs)
        self.assertEqual(validator.call_args_list, [mock.call(specs[0]), mock.call(specs[1])])

        class TupleSubclass(tuple):
            pass

        cases = (
            list(specs),
            TupleSubclass(specs),
            (specs[0],),
            tuple(reversed(specs)),
            (replace(specs[0], site_packages="/artifacts/other"), specs[1]),
            (specs[0], replace(specs[1], interpreter="/opt/other/python")),
            (
                replace(specs[0], interpreter="//opt/soc/python"),
                replace(specs[1], interpreter="//opt/soc/python"),
            ),
            (
                specs[0],
                replace(specs[1], expected_ctranslate2_version="4.7.2"),
            ),
            (
                specs[0],
                replace(specs[1], expected_ctranslate2_version="4.9.0"),
            ),
            (
                specs[0],
                replace(specs[1], expected_faster_whisper_version="1.3.0"),
            ),
        )
        for candidate in cases:
            with (
                self.subTest(candidate_type=type(candidate).__name__),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                self._build(runtime_specs=candidate)
            self.assertEqual(raised.exception.code, "benchmark-request-invalid")

    def test_runtime_versions_may_swap_but_remain_bound_to_their_roots(self) -> None:
        plan, artifacts, specs, profile = self._inputs()
        swapped = (
            replace(specs[0], expected_ctranslate2_version="4.8.1"),
            replace(specs[1], expected_ctranslate2_version="4.7.2"),
        )
        requests = ct2_benchmark.build_benchmark_pair_requests(
            plan,
            artifacts,
            swapped,
            profile,
            model_block_index=0,
            phase_index=0,
            nonces=self._NONCES,
        )
        expected_versions = {
            plan.runtimes[index].artifact_id: spec.expected_ctranslate2_version
            for index, spec in enumerate(swapped)
        }
        for arm_index, encoded in enumerate(requests):
            payload = json.loads(encoded)
            runtime_id = payload["runtime"]["manifest"]["artifact_id"]
            with self.subTest(arm_index=arm_index, runtime_id=runtime_id):
                self.assertEqual(
                    payload["runtime_layout"]["expected_ctranslate2_version"],
                    expected_versions[runtime_id],
                )
                self.assertEqual(
                    payload["runtime"]["root"],
                    swapped[
                        0 if runtime_id == plan.runtimes[0].artifact_id else 1
                    ].site_packages,
                )

    def test_host_tcb_interpreter_is_outside_every_artifact_root(self) -> None:
        _plan, artifacts, specs, _profile = self._inputs()
        overlapping = (
            str(artifacts[0][0]),
            str(artifacts[3][0] / "bin" / "python"),
        )
        for interpreter in overlapping:
            candidate = tuple(
                replace(spec, interpreter=interpreter) for spec in specs
            )
            with (
                self.subTest(interpreter=interpreter),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                self._build(runtime_specs=candidate)
            self.assertEqual(raised.exception.code, "benchmark-request-invalid")

        prefix_sibling = tuple(
            replace(spec, interpreter="/artifacts/runtime-a-tools/python")
            for spec in specs
        )
        requests = self._build(runtime_specs=prefix_sibling)
        self.assertEqual(len(requests), 2)

    def test_host_tcb_interpreter_rejects_unselected_artifact_root(self) -> None:
        _plan, artifacts, specs, _profile = self._inputs()
        interpreter = str(artifacts[4][0] / "bin" / "python")
        candidate = tuple(replace(spec, interpreter=interpreter) for spec in specs)

        with self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised:
            self._build(runtime_specs=candidate)

        self.assertEqual(raised.exception.code, "benchmark-request-invalid")

    def test_corpus_member_is_exact_bounded_and_non_executable(self) -> None:
        plan, artifacts, _specs, _profile = self._inputs()
        self.assertEqual(ct2_benchmark.MAX_CORPUS_BYTES, 1 << 20)
        at_limit = self._manifest(
            "clips-main",
            "clips",
            (("corpus-v1.json", ct2_benchmark.MAX_CORPUS_BYTES, False),),
        )
        at_limit_plan = replace(plan, clips=at_limit.reference)
        at_limit_artifacts = (
            *artifacts[:2],
            (artifacts[2][0], at_limit),
            *artifacts[3:],
        )
        self.assertEqual(
            len(self._build(plan=at_limit_plan, artifacts=at_limit_artifacts)),
            2,
        )

        cases = (
            (("other.json", 64, False),),
            (("corpus-v1.json", 64, True),),
            (("corpus-v1.json", 0, False),),
            (
                (
                    "corpus-v1.json",
                    ct2_benchmark.MAX_CORPUS_BYTES + 1,
                    False,
                ),
            ),
        )
        for files in cases:
            clips = self._manifest("clips-main", "clips", files)
            candidate_plan = replace(plan, clips=clips.reference)
            candidate_artifacts = (
                *artifacts[:2],
                (artifacts[2][0], clips),
                *artifacts[3:],
            )
            with (
                self.subTest(files=files),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                self._build(plan=candidate_plan, artifacts=candidate_artifacts)
            self.assertEqual(raised.exception.code, "benchmark-request-invalid")

    def test_indices_and_nonces_require_exact_types_shapes_and_ranges(self) -> None:
        plan, _artifacts, _specs, _profile = self._inputs()

        class TupleSubclass(tuple):
            pass

        cases = (
            {"model_block_index": True},
            {"model_block_index": -1},
            {"model_block_index": len(plan.model_blocks)},
            {"phase_index": True},
            {"phase_index": -1},
            {"phase_index": len(plan.model_blocks[0].phases)},
            {"nonces": list(self._NONCES)},
            {"nonces": TupleSubclass(self._NONCES)},
            {"nonces": (self._NONCES[0],)},
            {"nonces": (self._NONCES[0], self._NONCES[0])},
            {"nonces": ("A" * 64, self._NONCES[1])},
            {"nonces": (self._NONCES[0], 2)},
        )
        for arguments in cases:
            with (
                self.subTest(arguments=arguments),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                self._build(**arguments)
            self.assertEqual(raised.exception.code, "benchmark-request-invalid")

    def test_requests_are_deterministic_and_profile_change_changes_both(self) -> None:
        first = self._build()
        second = self._build()
        self.assertEqual(first, second)
        for encoded in first:
            self.assertEqual(encoded, self._encoded(json.loads(encoded)))

        _plan, _artifacts, _specs, profile = self._inputs()
        changed = self._build(profile=replace(profile, cpu_threads=5))
        self.assertNotEqual(first[0], changed[0])
        self.assertNotEqual(first[1], changed[1])
        self.assertNotEqual(
            json.loads(first[0])["decode_profile_sha256"],
            json.loads(changed[0])["decode_profile_sha256"],
        )

    def test_arm_request_final_size_gate_is_inclusive(self) -> None:
        limit = 3_403_776
        with mock.patch.object(
            ct2_benchmark,
            "_canonical_json",
            return_value=b"x" * limit,
        ):
            self.assertEqual(len(ct2_benchmark._canonical_arm_request({})), limit)
        with (
            mock.patch.object(
                ct2_benchmark,
                "_canonical_json",
                return_value=b"x" * (limit + 1),
            ),
            self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
        ):
            ct2_benchmark._canonical_arm_request({})
        self.assertEqual(raised.exception.code, "benchmark-request-invalid")

    def test_builder_freezes_each_path_once_and_has_no_io_or_execution(self) -> None:
        plan, artifacts, specs, profile = self._inputs()

        class AlternatingPath:
            def __init__(self) -> None:
                self.calls = 0

            def __fspath__(self) -> str:
                self.calls += 1
                return (
                    "/artifacts/runtime-a"
                    if self.calls == 1
                    else "/artifacts/changed"
                )

        root = AlternatingPath()
        candidate = ((root, artifacts[0][1]), *artifacts[1:])
        with (
            mock.patch("builtins.open", side_effect=AssertionError("open")) as fopen,
            mock.patch.object(
                ct2_benchmark.os,
                "open",
                side_effect=AssertionError("os.open"),
            ) as os_open,
            mock.patch.object(ct2_benchmark, "attest_artifact_root") as attester,
            mock.patch.object(ct2_benchmark, "local_model_command") as low_qos,
            mock.patch.object(ct2_benchmark, "run_process_bounded_output") as runner,
        ):
            requests = ct2_benchmark.build_benchmark_pair_requests(
                plan,
                candidate,
                specs,
                profile,
                model_block_index=0,
                phase_index=0,
                nonces=self._NONCES,
            )
        self.assertEqual(root.calls, 1)
        self.assertEqual(len(requests), 2)
        fopen.assert_not_called()
        os_open.assert_not_called()
        attester.assert_not_called()
        low_qos.assert_not_called()
        runner.assert_not_called()

    def test_preflight_codes_and_interrupt_are_preserved(self) -> None:
        plan, artifacts, specs, profile = self._inputs()
        for code in (
            "plan-invalid",
            "artifact-manifest-invalid",
            "artifact-reference-mismatch",
            "artifact-set-invalid",
        ):
            with (
                self.subTest(code=code),
                mock.patch.object(
                    ct2_benchmark,
                    "_validated_run_plan_artifacts",
                    side_effect=ct2_benchmark.RuntimeProbeError(code),
                ),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                ct2_benchmark.build_benchmark_pair_requests(
                    plan,
                    artifacts,
                    specs,
                    profile,
                    model_block_index=0,
                    phase_index=0,
                    nonces=self._NONCES,
                )
            self.assertEqual(raised.exception.code, code)

        interrupt = KeyboardInterrupt()
        with mock.patch.object(
            ct2_benchmark,
            "_validated_run_plan_artifacts",
            side_effect=interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised_interrupt:
                ct2_benchmark.build_benchmark_pair_requests(
                    plan,
                    artifacts,
                    specs,
                    profile,
                    model_block_index=0,
                    phase_index=0,
                    nonces=self._NONCES,
                )
        self.assertIs(raised_interrupt.exception, interrupt)
        self.assertEqual(
            ct2_benchmark.RuntimeProbeError("benchmark-request-invalid").code,
            "benchmark-request-invalid",
        )

    def test_binding_returns_exact_regenerated_tuple_and_rejects_stale_bytes(
        self,
    ) -> None:
        requests = self._build()
        regenerated = self._build()
        plan, artifacts, specs, profile = self._inputs()
        with mock.patch.object(
            ct2_benchmark,
            "build_benchmark_pair_requests",
            return_value=regenerated,
        ) as builder:
            result = ct2_benchmark.bind_benchmark_pair_requests(
                requests,
                plan,
                artifacts,
                specs,
                profile,
                model_block_index=0,
                phase_index=0,
                nonces=self._NONCES,
            )
        self.assertIs(result, regenerated)
        builder.assert_called_once_with(
            plan,
            artifacts,
            specs,
            profile,
            model_block_index=0,
            phase_index=0,
            nonces=self._NONCES,
        )

        changed_profile = self._build(profile=replace(profile, cpu_threads=5))
        cross_pair = self._build(phase_index=1)
        modified = bytes([requests[0][0] ^ 1]) + requests[0][1:]
        rehashed_payload = json.loads(requests[0])
        rehashed_payload["decode_profile"]["cpu_threads"] = 5
        rehashed_payload["decode_profile_sha256"] = hashlib.sha256(
            self._encoded(rehashed_payload["decode_profile"])
        ).hexdigest()
        variants = (
            tuple(reversed(requests)),
            (modified, requests[1]),
            (requests[0][:-1], requests[1]),
            (requests[0] + b"x", requests[1]),
            (changed_profile[0], requests[1]),
            (cross_pair[0], requests[1]),
            (self._encoded(rehashed_payload), requests[1]),
        )
        for candidate in variants:
            with (
                self.subTest(candidate_lengths=tuple(map(len, candidate))),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    wraps=ct2_benchmark.build_benchmark_pair_requests,
                ) as builder,
            ):
                self._assert_binding_invalid(candidate)
            self.assertEqual(builder.call_count, 1)

    def test_binding_candidate_preflight_is_exact_and_precedes_builder(self) -> None:
        requests = self._build()
        plan, artifacts, specs, profile = self._inputs()
        arguments = {
            "plan": plan,
            "artifacts": artifacts,
            "runtime_specs": specs,
            "profile": profile,
        }

        class TupleSubclass(tuple):
            pass

        class BytesSubclass(bytes):
            pass

        equal_first = b"distinct-equal"
        equal_second = bytes(bytearray(equal_first))
        self.assertIsNot(equal_first, equal_second)
        cases = (
            list(requests),
            TupleSubclass(requests),
            (requests[0],),
            (*requests, b"extra"),
            (bytearray(requests[0]), requests[1]),
            (memoryview(requests[0]), requests[1]),
            (BytesSubclass(requests[0]), requests[1]),
            (b"", requests[1]),
            (b"x" * (ct2_benchmark.MAX_ARM_REQUEST_BYTES + 1), requests[1]),
            (requests[0], requests[0]),
            (equal_first, equal_second),
        )
        for candidate in cases:
            builder = mock.Mock()
            comparator = mock.Mock()
            with (
                self.subTest(candidate_type=type(candidate).__name__),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    builder,
                ),
                mock.patch.object(
                    ct2_benchmark.secrets,
                    "compare_digest",
                    comparator,
                ),
            ):
                self._assert_binding_invalid(candidate, **arguments)
            builder.assert_not_called()
            comparator.assert_not_called()

    def test_binding_validates_builder_pair_before_comparison(self) -> None:
        requests = self._build()
        plan, artifacts, specs, profile = self._inputs()
        arguments = {
            "plan": plan,
            "artifacts": artifacts,
            "runtime_specs": specs,
            "profile": profile,
        }

        class TupleSubclass(tuple):
            pass

        class BytesSubclass(bytes):
            pass

        equal_first = b"distinct-equal"
        equal_second = bytes(bytearray(equal_first))
        self.assertIsNot(equal_first, equal_second)
        malformed = (
            list(requests),
            TupleSubclass(requests),
            (requests[0],),
            (*requests, b"extra"),
            (bytearray(requests[0]), requests[1]),
            (BytesSubclass(requests[0]), requests[1]),
            (b"", requests[1]),
            (b"x" * (ct2_benchmark.MAX_ARM_REQUEST_BYTES + 1), requests[1]),
            (requests[0], requests[0]),
            (equal_first, equal_second),
        )
        for generated in malformed:
            comparator = mock.Mock()
            with (
                self.subTest(generated_type=type(generated).__name__),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    return_value=generated,
                ) as builder,
                mock.patch.object(
                    ct2_benchmark.secrets,
                    "compare_digest",
                    comparator,
                ),
            ):
                self._assert_binding_invalid(requests, **arguments)
            self.assertEqual(builder.call_count, 1)
            comparator.assert_not_called()

        for generated in (
            (b"a", b"b"),
            (
                b"a" * ct2_benchmark.MAX_ARM_REQUEST_BYTES,
                b"b" * ct2_benchmark.MAX_ARM_REQUEST_BYTES,
            ),
        ):
            candidate = (generated[0], generated[1])
            built = (generated[0], generated[1])
            self.assertIsNot(candidate, built)
            real_compare = ct2_benchmark.secrets.compare_digest
            with (
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    return_value=built,
                ) as builder,
                mock.patch.object(
                    ct2_benchmark.secrets,
                    "compare_digest",
                    wraps=real_compare,
                ) as comparator,
            ):
                result = self._bind(candidate, **arguments)
            self.assertIs(result, built)
            builder.assert_called_once_with(
                plan,
                artifacts,
                specs,
                profile,
                model_block_index=0,
                phase_index=0,
                nonces=self._NONCES,
            )
            self.assertEqual(
                comparator.call_args_list,
                [
                    mock.call(candidate[0], built[0]),
                    mock.call(candidate[1], built[1]),
                ],
            )

    def test_binding_compares_both_arms_despite_first_arm_mismatch(self) -> None:
        generated = self._build()
        plan, artifacts, specs, profile = self._inputs()
        arguments = {
            "plan": plan,
            "artifacts": artifacts,
            "runtime_specs": specs,
            "profile": profile,
        }
        candidates = (
            (generated[0][:-1], generated[1]),
            (bytes([generated[0][0] ^ 1]) + generated[0][1:], generated[1]),
            (generated[0], generated[1][:-1]),
            (generated[0], bytes([generated[1][0] ^ 1]) + generated[1][1:]),
        )
        real_compare = ct2_benchmark.secrets.compare_digest
        for candidate in candidates:
            with (
                self.subTest(candidate_lengths=tuple(map(len, candidate))),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    return_value=generated,
                ) as builder,
                mock.patch.object(
                    ct2_benchmark.secrets,
                    "compare_digest",
                    side_effect=real_compare,
                ) as comparator,
            ):
                self._assert_binding_invalid(candidate, **arguments)
            self.assertEqual(builder.call_count, 1)
            self.assertEqual(
                comparator.call_args_list,
                [
                    mock.call(candidate[0], generated[0]),
                    mock.call(candidate[1], generated[1]),
                ],
            )

    def test_binding_builder_errors_are_fresh_allowlisted_or_normalized(self) -> None:
        requests = self._build()
        preserved = {
            "plan-invalid",
            "artifact-manifest-invalid",
            "artifact-reference-mismatch",
            "artifact-set-invalid",
            "benchmark-request-invalid",
        }
        for code in preserved:
            original = ct2_benchmark.RuntimeProbeError(code)
            with (
                self.subTest(code=code),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    side_effect=original,
                ),
                self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
            ):
                self._bind(requests)
            self.assertIsNot(raised.exception, original)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.args, (code,))
            self.assertIsNone(raised.exception.__cause__)
            self.assertIsNone(raised.exception.__context__)

        missing_code = ct2_benchmark.RuntimeProbeError("plan-invalid")
        del missing_code.code
        with (
            mock.patch.object(
                ct2_benchmark,
                "build_benchmark_pair_requests",
                side_effect=missing_code,
            ),
        ):
            error = self._assert_binding_invalid(requests)
        self.assertIsNot(error, missing_code)
        self.assertEqual(error.code, "benchmark-request-invalid")
        self.assertEqual(error.args, ("benchmark-request-invalid",))
        self.assertIsNone(error.__cause__)
        self.assertIsNone(error.__context__)

        class ErrorSubclass(ct2_benchmark.RuntimeProbeError):
            pass

        malformed_args = ct2_benchmark.RuntimeProbeError("plan-invalid")
        malformed_args.args = ()
        malformed_code = ct2_benchmark.RuntimeProbeError("plan-invalid")
        malformed_code.code = "artifact-set-invalid"
        unknown = ct2_benchmark.RuntimeProbeError("plan-invalid")
        unknown.args = ("private-code",)
        unknown.code = "private-code"
        for original in (
            ErrorSubclass("plan-invalid"),
            malformed_args,
            malformed_code,
            unknown,
            ValueError("private/path"),
            SystemExit("private/path"),
        ):
            with (
                self.subTest(error_type=type(original).__name__),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    side_effect=original,
                ),
            ):
                error = self._assert_binding_invalid(requests)
            self.assertNotIn("private", str(error))

        interrupt = KeyboardInterrupt()
        with (
            mock.patch.object(
                ct2_benchmark,
                "build_benchmark_pair_requests",
                side_effect=interrupt,
            ),
            self.assertRaises(KeyboardInterrupt) as raised_interrupt,
        ):
            self._bind(requests)
        self.assertIs(raised_interrupt.exception, interrupt)

    def test_binding_comparison_errors_are_redacted_and_interrupts_propagate(
        self,
    ) -> None:
        requests = self._build()
        generated = self._build()
        plan, artifacts, specs, profile = self._inputs()
        arguments = {
            "plan": plan,
            "artifacts": artifacts,
            "runtime_specs": specs,
            "profile": profile,
        }
        for position in (0, 1):
            for original in (
                ct2_benchmark.RuntimeProbeError("plan-invalid"),
                ValueError("private/path"),
                SystemExit("private/path"),
            ):
                effects = [original] if position == 0 else [True, original]
                with (
                    self.subTest(position=position, error=type(original).__name__),
                    mock.patch.object(
                        ct2_benchmark,
                        "build_benchmark_pair_requests",
                        return_value=generated,
                    ),
                    mock.patch.object(
                        ct2_benchmark.secrets,
                        "compare_digest",
                        side_effect=effects,
                    ),
                ):
                    error = self._assert_binding_invalid(requests, **arguments)
                self.assertNotIn("private", str(error))

            interrupt = KeyboardInterrupt()
            effects = [interrupt] if position == 0 else [True, interrupt]
            with (
                self.subTest(position=position, error="KeyboardInterrupt"),
                mock.patch.object(
                    ct2_benchmark,
                    "build_benchmark_pair_requests",
                    return_value=generated,
                ),
                mock.patch.object(
                    ct2_benchmark.secrets,
                    "compare_digest",
                    side_effect=effects,
                ),
                self.assertRaises(KeyboardInterrupt) as raised_interrupt,
            ):
                self._bind(requests, **arguments)
            self.assertIs(raised_interrupt.exception, interrupt)

        with (
            mock.patch.object(
                ct2_benchmark,
                "build_benchmark_pair_requests",
                return_value=generated,
            ),
            mock.patch.object(
                ct2_benchmark.secrets,
                "compare_digest",
                side_effect=[1, True],
            ),
        ):
            self._assert_binding_invalid(requests, **arguments)

    def test_binding_is_pure_and_pathlike_is_captured_only_by_one_builder_call(
        self,
    ) -> None:
        requests = self._build()
        plan, artifacts, specs, profile = self._inputs()

        class AlternatingPath:
            def __init__(self) -> None:
                self.calls = 0

            def __fspath__(self) -> str:
                self.calls += 1
                return (
                    str(artifacts[0][0])
                    if self.calls == 1
                    else "/artifacts/changed"
                )

        root = AlternatingPath()
        candidate_artifacts = ((root, artifacts[0][1]), *artifacts[1:])
        with (
            mock.patch("builtins.open", side_effect=AssertionError("open")) as fopen,
            mock.patch.object(
                ct2_benchmark.os,
                "open",
                side_effect=AssertionError("os.open"),
            ) as os_open,
            mock.patch.object(ct2_benchmark, "attest_artifact_root") as attester,
            mock.patch.object(ct2_benchmark, "local_model_command") as low_qos,
            mock.patch.object(
                ct2_benchmark,
                "run_process_bounded_output",
            ) as runner,
            mock.patch.object(
                ct2_benchmark.worker_protocol,
                "_parse_benchmark_arm_request",
            ) as worker_parser,
            mock.patch.object(
                ct2_benchmark,
                "build_benchmark_pair_requests",
                wraps=ct2_benchmark.build_benchmark_pair_requests,
            ) as builder,
        ):
            result = ct2_benchmark.bind_benchmark_pair_requests(
                requests,
                plan,
                candidate_artifacts,
                specs,
                profile,
                model_block_index=0,
                phase_index=0,
                nonces=self._NONCES,
            )
        self.assertEqual(result, requests)
        self.assertEqual(root.calls, 1)
        self.assertEqual(builder.call_count, 1)
        fopen.assert_not_called()
        os_open.assert_not_called()
        attester.assert_not_called()
        low_qos.assert_not_called()
        runner.assert_not_called()
        worker_parser.assert_not_called()


class Ctranslate2BenchmarkSyntheticProcessTests(unittest.TestCase):
    @staticmethod
    def _write_fake_packages(root: Path, *, output_mode: str | None) -> None:
        ct2 = root / "ctranslate2"
        faster = root / "faster_whisper"
        ct2.mkdir()
        faster.mkdir()
        prefix = {
            None: "",
            "print": "print('private import output')\n",
            "fd1": "import os\nos.write(1, b'private fd1 output')\n",
            "fd2": "import os\nos.write(2, b'private fd2 output')\n",
        }[output_mode]
        (ct2 / "__init__.py").write_text(
            prefix
            + "__version__ = '4.7.2'\n"
            + "def get_supported_compute_types(device):\n"
            + "    return {'int8', 'float32'} if device == 'cpu' else set()\n",
            encoding="ascii",
        )
        (faster / "__init__.py").write_text(
            "__version__ = '1.2.1'\n",
            encoding="ascii",
        )

    @staticmethod
    def _spec(site_packages: Path) -> ct2_benchmark._RuntimeSpec:
        return ct2_benchmark._RuntimeSpec(
            interpreter=sys.executable,
            site_packages=str(site_packages),
            expected_ctranslate2_version="4.7.2",
            expected_faster_whisper_version="1.2.1",
        )

    def test_artifact_attestation_crosses_real_isolated_worker_process(self) -> None:
        content = b"bounded synthetic payload"
        manifest_payload = {
            "artifact_id": "artifact-synthetic",
            "files": [
                {
                    "executable": False,
                    "path": "payload.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            ],
            "kind": "clips",
            "schema_version": 1,
        }
        manifest_bytes = json.dumps(
            manifest_payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        manifest = ct2_benchmark.parse_artifact_manifest(
            manifest_bytes,
            reference=ct2_benchmark.ArtifactManifestReference(
                artifact_id="artifact-synthetic",
                manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "artifact"
            root.mkdir(mode=0o700)
            root.chmod(0o700)
            target = root / "payload.bin"
            target.write_bytes(content)
            target.chmod(0o600)
            with mock.patch.object(
                ct2_benchmark,
                "local_model_command",
                side_effect=lambda argv: argv,
            ):
                result = ct2_benchmark.attest_artifact_root(root, manifest)

        self.assertEqual(
            result,
            ct2_benchmark.ArtifactAttestation(
                artifact_id="artifact-synthetic",
                manifest_sha256=manifest.reference.manifest_sha256,
                file_count=1,
                total_bytes=len(content),
            ),
        )

    def test_stalled_attestation_worker_is_bounded_terminated_and_reaped(
        self,
    ) -> None:
        content = b"payload"
        manifest_payload = {
            "artifact_id": "artifact-stalled",
            "files": [
                {
                    "executable": False,
                    "path": "payload.bin",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": len(content),
                }
            ],
            "kind": "clips",
            "schema_version": 1,
        }
        manifest_bytes = json.dumps(
            manifest_payload,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        manifest = ct2_benchmark.parse_artifact_manifest(
            manifest_bytes,
            reference=ct2_benchmark.ArtifactManifestReference(
                artifact_id="artifact-stalled",
                manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            ),
        )
        processes: list[object] = []
        real_popen = command_chain.subprocess.Popen

        def capture_process(*args: object, **kwargs: object) -> object:
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process

        started = time.monotonic()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                root = base / "artifact"
                root.mkdir(mode=0o700)
                root.chmod(0o700)
                target = root / "payload.bin"
                target.write_bytes(content)
                target.chmod(0o600)
                worker = base / "stalled_worker.py"
                worker.write_text("import time\ntime.sleep(30)\n", encoding="ascii")
                with (
                    mock.patch.object(
                        ct2_benchmark,
                        "_worker_path",
                        return_value=str(worker),
                    ),
                    mock.patch.object(
                        ct2_benchmark,
                        "local_model_command",
                        side_effect=lambda argv: list(argv),
                    ),
                    mock.patch.object(
                        ct2_benchmark,
                        "ARTIFACT_ATTESTATION_TIMEOUT_SECONDS",
                        1,
                    ),
                    mock.patch.object(
                        command_chain.subprocess,
                        "Popen",
                        side_effect=capture_process,
                    ),
                    self.assertRaises(ct2_benchmark.RuntimeProbeError) as raised,
                ):
                    ct2_benchmark.attest_artifact_root(root, manifest)

            self.assertEqual(raised.exception.code, "artifact-attestation-failed")
            self.assertLess(time.monotonic() - started, 3.0)
            self.assertEqual(len(processes), 1)
            process = processes[0]
            self.assertIsNotNone(process.returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=0.5)
                    except command_chain.subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=0.5)

    def test_isolated_synthetic_worker_observes_runtime_without_sitecustomize(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            site_packages = root / "site"
            malicious = root / "malicious"
            site_packages.mkdir()
            malicious.mkdir()
            self._write_fake_packages(site_packages, output_mode=None)
            marker = root / "sitecustomize-ran"
            (site_packages / "sitecustomize.py").write_text(
                f"open({str(marker)!r}, 'w').write('bad')\n",
                encoding="ascii",
            )
            self._write_fake_packages(malicious, output_mode="fd2")
            with (
                mock.patch.object(
                    ct2_benchmark,
                    "local_model_command",
                    side_effect=lambda argv: list(argv),
                ),
                mock.patch.dict(os.environ, {"PYTHONPATH": str(malicious)}),
            ):
                result = ct2_benchmark.observe_runtime(self._spec(site_packages))

            self.assertEqual(result.ctranslate2_version, "4.7.2")
            self.assertEqual(result.faster_whisper_version, "1.2.1")
            self.assertEqual(
                result.supported_cpu_compute_types,
                ("float32", "int8"),
            )
            self.assertFalse(marker.exists())

    def test_import_output_contaminates_exact_channel_and_is_redacted(self) -> None:
        cases = (
            ("print", "result-invalid"),
            ("fd1", "result-invalid"),
            ("fd2", "auxiliary-output"),
        )
        for output_mode, expected_code in cases:
            with self.subTest(output_mode=output_mode):
                with tempfile.TemporaryDirectory() as tmp:
                    site_packages = Path(tmp) / "site"
                    site_packages.mkdir()
                    self._write_fake_packages(
                        site_packages,
                        output_mode=output_mode,
                    )
                    with (
                        mock.patch.object(
                            ct2_benchmark,
                            "local_model_command",
                            side_effect=lambda argv: list(argv),
                        ),
                        self.assertRaises(
                            ct2_benchmark.RuntimeProbeError
                        ) as raised,
                    ):
                        ct2_benchmark.observe_runtime(self._spec(site_packages))
                self.assertEqual(raised.exception.code, expected_code)
                self.assertEqual(str(raised.exception), expected_code)
                self.assertNotIn("private", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
