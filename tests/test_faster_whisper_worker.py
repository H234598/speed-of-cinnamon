from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from speed_of_cinnamon import faster_whisper_worker


class FasterWhisperWorkerTests(unittest.TestCase):
    def test_transcribe_classifies_model_load_failure(self) -> None:
        class WhisperModel:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                raise RuntimeError("secret model details")

        fake_module = type("FakeFasterWhisper", (), {"WhisperModel": WhisperModel})
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            with self.assertRaises(faster_whisper_worker.WorkerFailure) as context:
                faster_whisper_worker._transcribe(Path("audio.flac"), "de", Path("model"))

        self.assertEqual(context.exception.code, "load")

    def test_transcribe_classifies_lazy_segment_failure_as_inference(self) -> None:
        class WhisperModel:
            def __init__(self, *_args: object, **_kwargs: object) -> None:
                pass

            def transcribe(self, *_args: object, **_kwargs: object) -> tuple[object, None]:
                def segments() -> object:
                    raise RuntimeError("secret inference details")
                    yield None

                return segments(), None

        fake_module = type("FakeFasterWhisper", (), {"WhisperModel": WhisperModel})
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            with self.assertRaises(faster_whisper_worker.WorkerFailure) as context:
                faster_whisper_worker._transcribe(Path("audio.flac"), "de", Path("model"))

        self.assertEqual(context.exception.code, "inference")

    def test_transcribe_passes_local_files_only_to_model_loader(self) -> None:
        captured: dict[str, object] = {}

        class Segment:
            text = "local transcript"

        class WhisperModel:
            def __init__(self, *_args: object, **kwargs: object) -> None:
                captured.update(kwargs)

            def transcribe(self, *_args: object, **_kwargs: object) -> tuple[list[Segment], object]:
                return [Segment()], object()

        fake_module = type("FakeFasterWhisper", (), {"WhisperModel": WhisperModel})
        with mock.patch.dict(sys.modules, {"faster_whisper": fake_module}):
            result = faster_whisper_worker._transcribe(Path("audio.flac"), "de", Path("model"))

        self.assertEqual(result, "local transcript")
        self.assertIs(captured.get("local_files_only"), True)
        self.assertEqual(
            captured.get("compute_type"),
            faster_whisper_worker.FASTER_WHISPER_REQUESTED_COMPUTE_TYPE,
        )
        self.assertIsNone(faster_whisper_worker.FASTER_WHISPER_REQUESTED_CPU_THREADS)
        self.assertIsNone(faster_whisper_worker.FASTER_WHISPER_REQUESTED_NUM_WORKERS)
        self.assertNotIn("cpu_threads", captured)
        self.assertNotIn("num_workers", captured)

    def test_runtime_diagnostic_reports_capabilities_without_model_or_inference(self) -> None:
        calls: list[str] = []

        class FasterWhisperModule:
            __version__ = "1.1.0"

            def __getattribute__(self, name: str) -> object:
                if name == "WhisperModel":
                    raise AssertionError("diagnostic must not access WhisperModel")
                return super().__getattribute__(name)

        ctranslate2_module = type(
            "CTranslate2Module",
            (),
            {
                "__version__": "4.6.0",
                "get_supported_compute_types": staticmethod(
                    lambda device: calls.append(device) or {"int8", "float32"}
                ),
            },
        )
        output = io.StringIO()
        with (
            mock.patch.dict(
                sys.modules,
                {
                    "ctranslate2": ctranslate2_module,
                    "faster_whisper": FasterWhisperModule(),
                },
            ),
            mock.patch.object(
                faster_whisper_worker,
                "_transcribe",
                side_effect=AssertionError("diagnostic must not transcribe"),
            ) as transcribe,
            mock.patch("sys.stdout", output),
        ):
            result = faster_whisper_worker.main(["--diagnose-runtime"])

        self.assertEqual(result, 0)
        self.assertEqual(calls, ["cpu"])
        transcribe.assert_not_called()
        self.assertEqual(
            json.loads(output.getvalue()),
            {
                "schema_version": 1,
                "ctranslate2": {
                    "available": True,
                    "version": "4.6.0",
                    "supported_compute_types": ["float32", "int8"],
                },
                "faster_whisper": {"available": True, "version": "1.1.0"},
            },
        )

    def test_runtime_diagnostic_reports_missing_imports_and_partial_capability(self) -> None:
        cases = (
            (
                {"ctranslate2": None, "faster_whisper": None},
                {
                    "schema_version": 1,
                    "ctranslate2": {
                        "available": False,
                        "version": None,
                        "supported_compute_types": None,
                    },
                    "faster_whisper": {"available": False, "version": None},
                },
            ),
            (
                {
                    "ctranslate2": type(
                        "CTranslate2Module",
                        (),
                        {
                            "get_supported_compute_types": staticmethod(
                                lambda _device: (_ for _ in ()).throw(RuntimeError("private"))
                            )
                        },
                    ),
                    "faster_whisper": type("FasterWhisperModule", (), {})(),
                },
                {
                    "schema_version": 1,
                    "ctranslate2": {
                        "available": True,
                        "version": None,
                        "supported_compute_types": None,
                    },
                    "faster_whisper": {"available": True, "version": None},
                },
            ),
        )
        for modules, expected in cases:
            output = io.StringIO()
            with (
                self.subTest(available=expected["ctranslate2"]["available"]),
                mock.patch.dict(sys.modules, modules),
                mock.patch("sys.stdout", output),
            ):
                result = faster_whisper_worker.main(["--diagnose-runtime"])

            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue()), expected)

    def test_runtime_diagnostic_rejects_additional_arguments(self) -> None:
        for argv in (
            ["--diagnose-runtime", "extra"],
            ["--diagnose-runtime", "--audio", "audio.flac"],
        ):
            with (
                self.subTest(argv=argv),
                mock.patch.object(faster_whisper_worker, "_diagnose_runtime_payload") as diagnose,
                mock.patch("sys.stderr", io.StringIO()),
            ):
                self.assertEqual(faster_whisper_worker.main(argv), 2)
            diagnose.assert_not_called()

    def test_argument_validation_accepts_regular_audio_and_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "speech.flac"
            audio.write_bytes(b"audio")
            model = root / "model"
            model.mkdir()
            args = argparse.Namespace(audio=str(audio), language="de", model=str(model))

            result = faster_whisper_worker._validate_arguments(args)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result[0], audio)
        self.assertEqual(result[1], "de")
        self.assertEqual(result[2], model)

    def test_argument_validation_rejects_non_text_arguments_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "speech.flac"
            audio.write_bytes(b"audio")
            model = root / "model"
            model.mkdir()
            for field, value in (("audio", None), ("language", 42), ("model", True)):
                args = argparse.Namespace(audio=str(audio), language="de", model=str(model))
                setattr(args, field, value)
                self.assertIsNone(faster_whisper_worker._validate_arguments(args))

    def test_argument_validation_rejects_symlinked_audio_and_model_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_audio = root / "real.flac"
            real_audio.write_bytes(b"audio")
            real_model = root / "real-model"
            real_model.mkdir()
            linked_audio = root / "speech.flac"
            linked_audio.symlink_to(real_audio)
            linked_model = root / "model"
            linked_model.symlink_to(real_model, target_is_directory=True)

            self.assertIsNone(
                faster_whisper_worker._validate_arguments(
                    argparse.Namespace(audio=str(linked_audio), language="de", model=str(real_model))
                )
            )
            self.assertIsNone(
                faster_whisper_worker._validate_arguments(
                    argparse.Namespace(audio=str(real_audio), language="de", model=str(linked_model))
                )
            )

    def test_argument_validation_rejects_symlinked_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_root = root / "real"
            real_root.mkdir()
            audio = real_root / "speech.flac"
            audio.write_bytes(b"audio")
            model = real_root / "model"
            model.mkdir()
            linked_root = root / "linked"
            linked_root.symlink_to(real_root, target_is_directory=True)

            self.assertIsNone(
                faster_whisper_worker._validate_arguments(
                    argparse.Namespace(
                        audio=str(linked_root / "speech.flac"),
                        language="de",
                        model=str(model),
                    )
                )
            )
            self.assertIsNone(
                faster_whisper_worker._validate_arguments(
                    argparse.Namespace(
                        audio=str(audio),
                        language="de",
                        model=str(linked_root / "model"),
                    )
                )
            )

    def test_argument_validation_rejects_malformed_path_text_without_raising(self) -> None:
        for value in ("bad\x00path", "\ud800"):
            result = faster_whisper_worker._validate_arguments(
                argparse.Namespace(audio=value, language="de", model=value)
            )
            self.assertIsNone(result)

    def test_main_rejects_malformed_path_without_traceback(self) -> None:
        for value in ("bad\x00path", "\ud800"):
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                result = faster_whisper_worker.main(
                    ["--audio", value, "--language", "de", "--model", value]
                )
            self.assertEqual(result, 1)
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_worker_failure_payload_contains_only_safe_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "speech.flac"
            audio.write_bytes(b"audio")
            model = root / "model"
            model.mkdir()
            with mock.patch.object(
                faster_whisper_worker,
                "_transcribe",
                side_effect=faster_whisper_worker.WorkerFailure("load"),
            ):
                output = root / "output.json"
                with output.open("w", encoding="utf-8") as handle:
                    with mock.patch("sys.stdout", handle):
                        self.assertEqual(
                            faster_whisper_worker.main(
                                ["--audio", str(audio), "--language", "de", "--model", str(model)]
                            ),
                            1,
                        )

            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(payload, {"status": "error", "error_code": "load"})

    def test_worker_success_payload_contains_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "speech.flac"
            audio.write_bytes(b"audio")
            model = root / "model"
            model.mkdir()
            output = io.StringIO()
            with (
                mock.patch.object(faster_whisper_worker, "_transcribe", return_value="hello ä"),
                mock.patch("sys.stdout", output),
            ):
                result = faster_whisper_worker.main(
                    ["--audio", str(audio), "--language", "de", "--model", str(model)]
                )

        self.assertEqual(result, 0)
        self.assertEqual(
            json.loads(output.getvalue()),
            {"status": "done", "transcript": "hello ä"},
        )


if __name__ == "__main__":
    unittest.main()
